"""Which records are the same original source, so that a count counts each source once.

One paper reaches this system by several routes. PubMed hands back a PMID; Europe PMC a
PMCID or a ``MED:`` id; BioMCP's article search a PMID with its DOI; a TCM database a DOI
typed as ``https://doi.org/10.1016/J.JEP.2021.114180``; NPASS ``doi:10.1016/j.jep...``.
Counted as written, that one paper is four sources, and four sources read as four
independent reports. The same holds for a trial registered once and cited as ``NCT…`` in
three spellings, and for a variant record reached through MyVariant and through dbSNP.

Two steps, both pure:

1. :func:`canonical` gives one identifier one spelling. PMID, PMCID, DOI, NCT, rsID and
   HGVS each have a rule (below); anything else returns ``None``. A bare number is not
   read as a PMID unless the caller says it is one: ``2244`` is as likely a PubChem CID.
2. :func:`group_sources` treats the identifiers one record carries as names of the same
   source, and merges records that share any name (union-find). A record listing a PMID
   and a DOI joins a record that has only the DOI, and through it one that has only the
   PMID. A record with no identifier this module recognises cannot be merged with
   anything; it is counted on its own and reported as unidentified, so the count says how
   much of it rests on records it could not check.

Merging follows names transitively, so it errs towards counting fewer sources rather than
more — the same direction as ``tcmdb.consensus.independent_count``. An rsID names a dbSNP
locus record, so two alleles recorded under one rsID are one source by default; a caller
that counts alleles passes ``link_on`` without ``rsid``. Only identifiers that name the
record itself belong in it: a trial's reference list or a paper's bibliography are
citations, and listing them as names would merge every paper with everything it cites.

What it does not do: map a PMID to a DOI that no record states. That needs a lookup
service (NCBI's ID converter), a network call this module never makes; two records that
name the same paper only by different schemes stay two sources until some record states
both.

Rules, and the failure each prevents:

* **PMID** — digits, leading zeros dropped (``PMID: 012345`` and ``12345`` are one paper).
* **PMCID** — ``PMC`` and digits, upper case, version suffix dropped (``pmc8696197.2``).
* **DOI** — the DOI name only: resolver prefixes (``https://doi.org/``, ``doi:``,
  ``info:doi/``) removed, percent-escapes decoded, lower case (DOI names are
  case-insensitive), trailing ``.``, ``,`` and ``;`` removed (a DOI copied from the end of
  a sentence).
* **NCT** — ``NCT`` and eight digits, upper case.
* **rsID** — ``rs`` and digits, lower case, leading zeros dropped.
* **HGVS** — an accession-based description keeps its accession (upper case, version kept:
  ``NM_004333.4`` and ``NM_004333.5`` are different transcripts) and drops a gene
  annotation (``NM_004333.4(BRAF):c.1799T>A``); a one-letter protein substitution becomes
  three-letter (``p.V600E`` is ``p.Val600Glu``). A genomic description by chromosome
  (``chr10:g.114758349C>T``, MyVariant's form) means different positions on GRCh37 and
  GRCh38, so it carries the assembly when one is given and never matches one that names a
  different assembly or none.
"""

from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass
from typing import Any, Collection, Iterable, Mapping

__all__ = ["SCHEMES", "SourceId", "SourceGroup", "SourceCount", "canonical",
           "canonical_many", "group_sources", "normalise_assembly"]

#: The schemes this module canonicalises, in the order a group's representative is picked.
SCHEMES: tuple[str, ...] = ("pmid", "pmcid", "doi", "nct", "rsid", "hgvs")

#: Spellings of a scheme a CURIE may use, onto the scheme. ``med`` is Europe PMC's source
#: code for a PubMed record, ``pmc`` its code for a PMC one.
_SCHEME_ALIASES: Mapping[str, str] = {
    "pmid": "pmid", "pubmed": "pmid", "med": "pmid",
    "pmcid": "pmcid", "pmc": "pmcid",
    "doi": "doi", "nct": "nct", "clinicaltrials": "nct",
    "rsid": "rsid", "rs": "rsid", "dbsnp": "rsid", "hgvs": "hgvs",
}

_ASSEMBLIES: Mapping[str, str] = {
    "grch38": "GRCh38", "hg38": "GRCh38", "38": "GRCh38",
    "grch37": "GRCh37", "hg19": "GRCh37", "37": "GRCh37", "b37": "GRCh37",
    "ncbi36": "NCBI36", "hg18": "NCBI36", "36": "NCBI36",
}

_PMID = re.compile(r"\d{1,9}")
_PMCID = re.compile(r"(?i)pmc(\d{1,9})(?:\.\d+)?")
_DOI = re.compile(r"10\.\d{4,9}/\S+")
_NCT = re.compile(r"(?i)nct\s?(\d{8})")
_RSID = re.compile(r"(?i)rs\s?(\d{1,12})")
#: Identifiers carried inside a URL, by the site that serves them.
_URLS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?i)(?:pubmed\.ncbi\.nlm\.nih\.gov|ncbi\.nlm\.nih\.gov/pubmed)/(\d+)"),
     "pmid"),
    (re.compile(r"(?i)europepmc\.org/(?:abstract|article)/MED/(\d+)"), "pmid"),
    (re.compile(r"(?i)(?:ncbi\.nlm\.nih\.gov/pmc/articles|pmc\.ncbi\.nlm\.nih\.gov/articles"
                r"|europepmc\.org/(?:abstract|article)/PMC)/(PMC\d+)"), "pmcid"),
    (re.compile(r"(?i)clinicaltrials\.gov/(?:ct2/show|study)/(NCT\d{8})"), "nct"),
    (re.compile(r"(?i)ncbi\.nlm\.nih\.gov/snp/(rs\d+)"), "rsid"),
)
_DOI_PREFIXES = re.compile(r"(?i)^(?:https?://)?(?:dx\.)?doi\.org/|^doi:\s*|^info:doi/")
_HGVS_ACCESSION = re.compile(
    r"(?i)(?P<acc>(?:N[CGMRPTW]|X[MRP]|LRG|ENS[TGP])[_A-Za-z0-9]*(?:\.\d+)?)"
    r"(?:\([A-Za-z0-9_.-]+\))?:(?P<kind>[cgnmrp])\.(?P<change>\S+)")
_ON_ASSEMBLY = re.compile(r"(?i)(grch3[78]|ncbi36|hg1[89]|hg38|b37):(.+)")
_HGVS_CHROMOSOME = re.compile(
    r"(?i)(?:chr)?(?P<chrom>[1-9]|1\d|2[0-2]|X|Y|MT?):g\.(?P<change>\S+)")
_SUBSTITUTION = re.compile(r"(?i)^(?P<pos>.*\d)(?P<ref>[acgtu])>(?P<alt>[acgtu])$")
_PROTEIN_ONE_LETTER = re.compile(r"^\(?(?P<ref>[A-Z])(?P<pos>\d+)(?P<alt>[A-Z*=])\)?$")
_THREE_LETTER = {
    "A": "Ala", "R": "Arg", "N": "Asn", "D": "Asp", "C": "Cys", "Q": "Gln", "E": "Glu",
    "G": "Gly", "H": "His", "I": "Ile", "L": "Leu", "K": "Lys", "M": "Met", "F": "Phe",
    "P": "Pro", "S": "Ser", "T": "Thr", "W": "Trp", "Y": "Tyr", "V": "Val", "*": "Ter",
    "U": "Sec", "O": "Pyl",
}


@dataclass(frozen=True, order=True)
class SourceId:
    """One identifier in canonical form. ``str()`` is the CURIE, ``pmid:12345``."""

    scheme: str
    value: str

    @property
    def curie(self) -> str:
        return f"{self.scheme}:{self.value}"

    def __str__(self) -> str:
        return self.curie


def normalise_assembly(assembly: str | None) -> str | None:
    """``GRCh37``/``GRCh38``/``NCBI36`` for the names in use (``hg19``, ``b37``, ...).

    An unknown name raises: a typo would otherwise drop the assembly and let a GRCh37
    position match a GRCh38 one.
    """
    if assembly is None or not str(assembly).strip():
        return None
    name = _ASSEMBLIES.get(str(assembly).strip().lower())
    if name is None:
        raise ValueError(f"unknown genome assembly {assembly!r}; "
                         f"known: {sorted(set(_ASSEMBLIES.values()))}")
    return name


def canonical(identifier: Any, scheme: str | None = None, *,
              assembly: str | None = None) -> SourceId | None:
    """``identifier`` in canonical form, or ``None`` when it is none of :data:`SCHEMES`.

    ``scheme`` says what the identifier is when its spelling does not (a bare PMID); a
    ``SourceId`` or a CURIE (``doi:10.…``, ``PMID: 123``) says it itself. ``assembly``
    qualifies a genomic HGVS description by chromosome.
    """
    if isinstance(identifier, SourceId):
        return canonical(identifier.value, identifier.scheme)
    if identifier is None or isinstance(identifier, bool):
        return None
    text = urllib.parse.unquote(str(identifier)).strip()
    if not text:
        return None
    hinted = _SCHEME_ALIASES.get((scheme or "").strip().lower())
    if scheme and hinted is None:
        raise ValueError(f"unknown identifier scheme {scheme!r}; known: {SCHEMES}")
    for pattern, kind in _URLS:
        found = pattern.search(text)
        if found:
            # A PubMed address handed over as a DOI is not a DOI.
            return _by_scheme(found.group(1), kind, assembly) if hinted in (None, kind) \
                else None
    if hinted is not None:
        return _by_scheme(text, hinted, assembly)
    prefix, sep, rest = text.partition(":")
    named = _SCHEME_ALIASES.get(prefix.strip().lower()) if sep else None
    if named is not None:
        return _by_scheme(rest.strip(), named, assembly)
    return _by_shape(text, assembly)


def _by_scheme(text: str, scheme: str, assembly: str | None) -> SourceId | None:
    if scheme == "pmid":
        digits = re.sub(r"(?i)^(?:pmid|pubmed|med)[:\s]*", "", text)
        return SourceId("pmid", str(int(digits))) if _PMID.fullmatch(digits) and \
            int(digits) > 0 else None
    if scheme == "pmcid":
        found = _PMCID.fullmatch(text) or (_PMCID.fullmatch("PMC" + text)
                                           if text.isdigit() else None)
        return SourceId("pmcid", f"PMC{int(found.group(1))}") if found else None
    if scheme == "doi":
        return _doi(text)
    if scheme == "nct":
        found = _NCT.fullmatch(text) or (_NCT.fullmatch("NCT" + text)
                                         if text.isdigit() else None)
        return SourceId("nct", f"NCT{found.group(1)}") if found else None
    if scheme == "rsid":
        found = _RSID.fullmatch(text) or (_RSID.fullmatch("rs" + text)
                                          if text.isdigit() else None)
        return SourceId("rsid", f"rs{int(found.group(1))}") if found else None
    return _hgvs(text, assembly)


def _by_shape(text: str, assembly: str | None) -> SourceId | None:
    """An identifier recognised by its spelling alone. Bare digits are not one."""
    if _DOI_PREFIXES.search(text) or _DOI.fullmatch(text.rstrip(".,;")):
        return _doi(text)
    for scheme, pattern in (("pmcid", _PMCID), ("nct", _NCT), ("rsid", _RSID)):
        if pattern.fullmatch(text):
            return _by_scheme(text, scheme, assembly)
    labelled = re.fullmatch(r"(?i)(?:pmid|pubmed)\s*[:#]?\s*(\d{1,9})", text)
    if labelled:
        return _by_scheme(labelled.group(1), "pmid", assembly)
    return _hgvs(text, assembly)


def _doi(text: str) -> SourceId | None:
    name = _DOI_PREFIXES.sub("", text.strip()).strip().rstrip(".,;").lower()
    return SourceId("doi", name) if _DOI.fullmatch(name) else None


def _hgvs(text: str, assembly: str | None) -> SourceId | None:
    built = _ON_ASSEMBLY.fullmatch(text)
    if built:
        # This module's own form, ``GRCh37:chr10:g.…``: the assembly is part of the name.
        named, given = normalise_assembly(built.group(1)), normalise_assembly(assembly)
        if given not in (None, named):
            raise ValueError(f"{text!r} names {named}, not the {given} it was given with")
        return _hgvs(built.group(2), named)
    found = _HGVS_ACCESSION.fullmatch(text)
    if found:
        kind = found.group("kind").lower()
        change = _change(kind, found.group("change"))
        return SourceId("hgvs", f"{found.group('acc').upper()}:{kind}.{change}")
    found = _HGVS_CHROMOSOME.fullmatch(text)
    if found:
        chrom = found.group("chrom").upper()
        chrom = "M" if chrom in ("M", "MT") else chrom
        genomic = f"chr{chrom}:g.{_change('g', found.group('change'))}"
        build = normalise_assembly(assembly)
        return SourceId("hgvs", f"{build}:{genomic}" if build else genomic)
    return None


def _change(kind: str, change: str) -> str:
    """The variant part of an HGVS description, with the spellings that vary made one."""
    if kind == "p":
        one = _PROTEIN_ONE_LETTER.fullmatch(change)
        if one:
            return f"{_THREE_LETTER[one['ref']]}{one['pos']}" + (
                "=" if one["alt"] == "=" else _THREE_LETTER[one["alt"]])
        return change.removeprefix("(").removesuffix(")")
    if kind == "r":
        return change.lower()                    # RNA is written in lower case
    sub = _SUBSTITUTION.fullmatch(change)
    if sub:
        return f"{sub['pos']}{sub['ref'].upper()}>{sub['alt'].upper()}"
    return change


def canonical_many(identifiers: Iterable[Any], *,
                   assembly: str | None = None) -> tuple[SourceId, ...]:
    """The canonical identifiers among ``identifiers``, once each, sorted."""
    out = {c for c in (canonical(i, assembly=assembly) for i in identifiers or ())
           if c is not None}
    return tuple(sorted(out, key=_rank))


def _rank(sid: SourceId) -> tuple[int, str]:
    return (SCHEMES.index(sid.scheme), sid.value)


@dataclass(frozen=True)
class SourceGroup:
    """Records that name one source. ``key`` is its first identifier by :data:`SCHEMES`."""

    key: SourceId
    identifiers: tuple[SourceId, ...]
    members: tuple[Any, ...]


@dataclass(frozen=True)
class SourceCount:
    """Records grouped into sources, and the records no identifier could place."""

    groups: tuple[SourceGroup, ...]
    unidentified: tuple[Any, ...]

    @property
    def count(self) -> int:
        """Distinct sources: one per group, and one per record that could not be placed."""
        return len(self.groups) + len(self.unidentified)

    def group_of(self, member: Any) -> SourceGroup | None:
        return next((g for g in self.groups if member in g.members), None)

    def as_dict(self) -> dict[str, Any]:
        return {"sources": self.count, "identified": len(self.groups),
                "unidentified": len(self.unidentified),
                "groups": [{"key": str(g.key), "identifiers": [str(i) for i in g.identifiers],
                            "members": list(g.members)} for g in self.groups]}


def group_sources(records: Mapping[Any, Iterable[Any]] | Iterable[Iterable[Any]], *,
                  link_on: Collection[str] = SCHEMES,
                  assembly: str | None = None) -> SourceCount:
    """Group records that name the same source; each record is a collection of its names.

    ``records`` maps a record key to the identifiers that name it, or is a sequence of
    such collections (keyed by position). Only identifiers whose scheme is in ``link_on``
    merge records; the others are kept in the group's identifiers. Groups and members come
    back in a fixed order, so the same records give the same answer.
    """
    unknown = set(link_on) - set(SCHEMES)
    if unknown:
        raise ValueError(f"cannot link on {sorted(unknown)}; schemes are {SCHEMES}")
    items = list(records.items()) if isinstance(records, Mapping) else list(
        enumerate(records))
    parent = list(range(len(items)))

    def root(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    names: list[tuple[SourceId, ...]] = []
    first: dict[SourceId, int] = {}
    for index, (_, identifiers) in enumerate(items):
        if isinstance(identifiers, (str, SourceId)):
            identifiers = (identifiers,)          # one name, not its characters
        ids = canonical_many(identifiers, assembly=assembly)
        names.append(ids)
        for sid in ids:
            if sid.scheme not in link_on:
                continue
            seen = first.setdefault(sid, index)
            a, b = root(seen), root(index)
            if a != b:
                parent[max(a, b)] = min(a, b)
    members: dict[int, list[int]] = {}
    for index in range(len(items)):
        members.setdefault(root(index), []).append(index)
    groups: list[SourceGroup] = []
    unidentified: list[Any] = []
    for indices in members.values():
        ids = canonical_many(sid for i in indices for sid in names[i])
        if not ids:
            unidentified.extend(items[i][0] for i in indices)
            continue
        groups.append(SourceGroup(key=ids[0], identifiers=ids,
                                  members=tuple(items[i][0] for i in indices)))
    groups.sort(key=lambda g: _rank(g.key))
    return SourceCount(tuple(groups), tuple(unidentified))
