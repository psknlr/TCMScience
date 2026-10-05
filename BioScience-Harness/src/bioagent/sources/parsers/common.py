"""What every source parser shares: reading files, blanks, identifiers, a parse report.

Parsers are pure functions from raw files to node and edge rows (``sources.schema``).
They never touch the network and never filter on scientific grounds (no OB/DL cut-offs);
they only drop rows that cannot become a sound row, and they count every drop by reason
in a ``ParseReport`` so a snapshot says what it left out.

Node identity: a node with a standard global identifier uses it — ``inchikey:`` for a
compound, ``uniprot:`` for a protein, ``ncbitaxon:`` for an organism — so the same
compound from LOTUS, NPASS, CMAUP and BindingDB is one node when snapshots are read
together. The source's own identifier is kept in ``xrefs`` under the source key. A node
without a global identifier falls back to ``<source>:<source id>``.
"""

from __future__ import annotations

import csv
import gzip
import hashlib
import io
import json
import re
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence

__all__ = ["BLANKS", "ParseReport", "ParseResult", "open_text", "read_rows", "clean",
           "publication", "compound_id", "target_id", "organism_id", "NodeBook",
           "parse_measure", "TaxonFilter", "row_digest"]

#: Spellings the BIDD files, LOTUS and BindingDB use for "no value".
BLANKS = frozenset({"", "n.a.", "na", "n/a", "nan", "none", "null", "-"})

csv.field_size_limit(1 << 27)

_INCHIKEY = re.compile(r"^[A-Z]{14}-[A-Z]{10}-[A-Z]$")
_UNIPROT = re.compile(
    r"^([OPQ][0-9][A-Z0-9]{3}[0-9]|[A-NR-Z][0-9]([A-Z][A-Z0-9]{2}[0-9]){1,2})(-\d+)?$")


def clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return None if text.lower() in BLANKS else text


def open_text(path: str | Path, encoding: str = "utf-8") -> io.TextIOBase:
    """Plain, ``.gz`` or ``.zip`` (first member) as text in ``encoding``.

    Undecodable bytes become U+FFFD rather than an exception, so one bad byte does not
    lose a file — and ``read_rows`` counts the rows it happened to. Chinese exports
    (ETCM, HERB) are often GB18030; pass it rather than let UTF-8 mangle every name.
    """
    path = Path(path)
    if path.suffix == ".gz":
        return io.TextIOWrapper(gzip.open(path, "rb"), encoding=encoding, errors="replace",
                                newline="")
    if path.suffix == ".zip":
        archive = zipfile.ZipFile(path)
        member = next(n for n in archive.namelist() if not n.endswith("/"))
        return io.TextIOWrapper(archive.open(member), encoding=encoding, errors="replace",
                                newline="")
    return open(path, encoding=encoding, errors="replace", newline="")


def read_rows(path: str | Path, *, delimiter: str = "\t",
              fieldnames: Sequence[str] | None = None, encoding: str = "utf-8",
              report: "ParseReport | None" = None) -> Iterator[dict[str, str | None]]:
    """Rows as dicts with blanks normalised to None. ``fieldnames`` for headerless files.

    With a ``report``, a row holding bytes that are not valid ``encoding`` is counted as a
    warning: the row is kept, but its text is not what the file said, and a snapshot
    built from it should say so.
    """
    with open_text(path, encoding) as fh:
        reader = csv.DictReader(fh, delimiter=delimiter, fieldnames=fieldnames,
                                quoting=csv.QUOTE_NONE if delimiter == "\t" else csv.QUOTE_MINIMAL)
        for row in reader:
            out = {(k or "").strip(): clean(v) for k, v in row.items() if k is not None}
            if report is not None and any(v and "\ufffd" in v for v in out.values()):
                report.warn(f"row with bytes that are not valid {encoding}")
            yield out


def publication(ref_id: str | None, ref_type: str | None) -> str | None:
    """A PMID or DOI as a CURIE; anything else (a database name, a URL) is not one."""
    ref_id, kind = clean(ref_id), (clean(ref_type) or "").lower()
    if not ref_id:
        return None
    if kind == "pmid" or (not kind and ref_id.isdigit()):
        return f"pmid:{ref_id}" if ref_id.isdigit() else None
    if kind == "doi" or ref_id.lower().startswith("10."):
        doi = ref_id.lower().removeprefix("https://doi.org/").removeprefix("doi:")
        return f"doi:{doi}" if doi.startswith("10.") else None
    return None


def compound_id(source: str, source_id: str, inchikey: str | None) -> str:
    ik = (clean(inchikey) or "").upper()
    return f"inchikey:{ik}" if _INCHIKEY.match(ik) else f"{source}:{source_id}"


def target_id(source: str, source_id: str, uniprot: str | None) -> str:
    acc = clean(uniprot)
    return f"uniprot:{acc}" if acc and _UNIPROT.match(acc) else f"{source}:{source_id}"


def organism_id(source: str, source_id: str, taxid: str | None) -> str:
    tax = clean(taxid)
    return f"ncbitaxon:{tax}" if tax and tax.isdigit() else f"{source}:{source_id}"


def row_digest(row: Mapping[str, Any]) -> str:
    """A short content hash of one source row: its identity when the source gives none.

    NPASS and CMAUP activity files carry no record id, and the parsers used the row's
    *position* instead. A position is not an identity: one row inserted near the top of
    the next release renumbers every row after it, so comparing two snapshots' ids says
    everything changed. And the files repeat rows exactly — 87,882 of NPASS 2.0's 958,866
    activity rows and 1,230 of CMAUP 2.0's 28,871 — which positions turned into distinct
    edges: on the full files, 3,659 of NPASS's 157,579 cited protein-target edges and
    1,191 of CMAUP's 26,934 were repeats. A digest of the row's content is the same
    wherever the row sits, and two identical rows share it, so the duplicate can be
    recognised and counted.
    """
    blob = json.dumps({k: row[k] for k in sorted(row)}, ensure_ascii=False,
                      separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def is_inchikey(value: str | None) -> bool:
    return bool(value) and bool(_INCHIKEY.match(value))


def is_uniprot(value: str | None) -> bool:
    return bool(value) and bool(_UNIPROT.match(value))


_RELATION = re.compile(r"^\s*(<=|>=|<|>|=|~)?\s*([-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)\s*$")


def parse_measure(kind: str | None, value: str | None, unit: str | None,
                  relation: str | None = None) -> dict[str, Any] | None:
    """``{type, relation, value, unit}`` or None when there is no number to record.

    BindingDB writes a qualifier into the value (``>10000``); BIDD files give it in its
    own column. Either way the number is a float and the qualifier is kept.
    """
    kind, text = clean(kind), clean(value)
    if not kind or not text:
        return None
    m = _RELATION.match(text)
    if not m:
        return None
    rel = clean(relation) or m.group(1) or "="
    return {"type": kind, "relation": rel, "value": float(m.group(2)),
            "unit": clean(unit)}


@dataclass
class ParseReport:
    """Counts of what a parser read, produced and left out, by reason."""

    source: str
    read: Counter = field(default_factory=Counter)
    dropped: Counter = field(default_factory=Counter)
    #: Rows kept with a caveat (undecodable bytes). Absent from ``as_dict`` when empty, so
    #: a clean file's report — and the snapshot id it is part of — is unchanged.
    warnings: Counter = field(default_factory=Counter)

    def drop(self, reason: str, n: int = 1) -> None:
        self.dropped[reason] += n

    def warn(self, reason: str, n: int = 1) -> None:
        self.warnings[reason] += n

    def as_dict(self) -> dict[str, Any]:
        out = {"source": self.source, "read": dict(self.read), "dropped": dict(self.dropped)}
        if self.warnings:
            out["warnings"] = dict(self.warnings)
        return out


@dataclass
class ParseResult:
    nodes: list[dict[str, Any]]
    edges: list[dict[str, Any]]
    report: ParseReport
    raw_files: dict[str, Path]


class NodeBook:
    """Collects nodes by id, merging names and xrefs when a node is seen twice."""

    def __init__(self, source: str) -> None:
        self.source = source
        self._nodes: dict[str, dict[str, Any]] = {}

    def add(self, id: str, category: str, name: str | None, *,
            xrefs: Mapping[str, Iterable[str]] | None = None,
            names: Mapping[str, Iterable[str]] | None = None,
            raw: Mapping[str, Any] | None = None) -> str:
        node = self._nodes.get(id)
        if node is None:
            node = self._nodes[id] = {"id": id, "category": category,
                                      "name": name or id.split(":", 1)[1],
                                      "source": self.source, "xrefs": {}, "names": {}}
            if raw:
                node["raw"] = dict(raw)
        for bucket, values in (("xrefs", xrefs), ("names", names)):
            for key, items in (values or {}).items():
                have = node[bucket].setdefault(key, [])
                for item in items:
                    item = clean(item)
                    if item and item not in have:
                        have.append(item)
        return id

    def __contains__(self, id: str) -> bool:
        return id in self._nodes

    def rows(self) -> list[dict[str, Any]]:
        out = []
        for node in self._nodes.values():
            row = dict(node)
            for bucket in ("xrefs", "names"):
                row[bucket] = {k: sorted(v) for k, v in sorted(row[bucket].items()) if v}
                if not row[bucket]:
                    del row[bucket]
            out.append(row)
        return out


@dataclass(frozen=True)
class TaxonFilter:
    """Keep only these organisms: NCBI taxon ids, plus accepted names and synonyms for
    sources (LOTUS's light export) that give names but no taxon id."""

    taxa: Mapping[str, Sequence[str]]          # taxid -> names

    def by_id(self, *taxids: str | None) -> str | None:
        for tax in taxids:
            tax = clean(tax)
            if tax and tax in self.taxa:
                return tax
        return None

    def by_name(self, name: str | None) -> str | None:
        name = (clean(name) or "").casefold()
        for tax, names in self.taxa.items():
            if any(name == n.casefold() for n in names):
                return tax
        return None
