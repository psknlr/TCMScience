"""IEDB, the Immune Epitope Database: its public exports as a snapshot dataset.

IEDB curates immune epitopes and the assays that tested them from the literature and
from direct submissions. The exports were checked from this environment on 2026-10-01:
``https://www.iedb.org/downloader.php?file_name=doc/<file>`` answers without a login,
with no Content-Length and ignoring Range (a made-up file name answers 200 with an empty
body). www.iedb.org's robots.txt sets ``Crawl-delay: 10``; the spec's ``min_interval_s``
makes ``hub.fetch`` send every request (size probe, download, retry) 10.5 s apart.

Each ``*_full_v3`` export is a zipped TSV with a two-row header: a category row
(``Reference``, ``Epitope``, ``Host``, ``Assay``, ...) above a field row (``IEDB IRI``,
``Name``, ...). ``READERS["iedb_tsv"]`` joins the two into one column name,
``"<category> | <field>"`` (``Assay | Qualitative Measurement``, which the store makes
``Assay_Qualitative_Measurement``), and keeps the columns ``_KEEP`` lists for the table:
the identifiers, the assay, its outcome and the conditions it was run under. The T-cell
export has 161 columns and 1.35 GB uncompressed; the zip keeps everything else.

What the relations mean:

* ``compound_assay`` / ``epitope_assay``: one row per T-cell (or B-cell) assay of an
  epitope. ``outcome`` is the assay's own qualitative call: Positive, Positive-High,
  -Intermediate and -Low are ``positive``, Negative is ``negative`` (about 60% of the
  T-cell assays are negative). Evidence is ``known`` (curated from the experiment).
  The object is the assay type (an OBI term: method and response measured); the
  context holds the host, effector cell and tissue, the measurement and the IEDB assay
  id. A non-peptidic epitope that IEDB maps to PubChem (``epitope_id_chebi_id_pubchem_id
  _maps.txt``) or to ChEBI is a compound, so its assays are ``compound_assay`` rows with
  a ``pubchem:`` (else ``chebi:``) subject, which meet other sources' compound rows;
  every other epitope is ``iedb:epitope.<id>`` in ``epitope_assay``.
* ``epitope_antigen``: the protein an epitope comes from (UniProt, else NCBI Protein),
  with the protein's species; ``listed``, one row per pair.
* ``receptor_epitope``: a T-cell or B-cell receptor group (identical CDR3s) and an
  epitope it recognises, per reference; ``known``.

Ids: ``ncbiprotein:<accession>`` (an NCBI Protein accession, when IEDB names no UniProt
entry for the antigen) and ``obi:<7 digits>`` (an OBI assay-type term, ``OBI_0001234``)
are global identifiers, registered with the shared prefixes in docs/tcm-data-sources.md;
IEDB's own records are ``iedb:epitope.<n>``, ``iedb:receptor.<n>``, ``iedb:assay.<n>``,
``iedb:reference.<n>``. ``context["species"]`` is always prefixed (``ncbitaxon:9606``, or
IEDB's ``iedb:ONTIE_<n>`` strain term with the strain's name in the note).

MHC binding and elution assays (``mhc_ligand_full``, 10 GB uncompressed), the epitope
table and the B-cell assays are optional; the MHC export and the complete XML export
are larger than this harness fetches by default.
"""

from __future__ import annotations

import csv
import re
import sqlite3
from pathlib import Path
from typing import Iterator

from ..rowkit import Row, ctx, has, names, rel, rows, unresolved, v
from ..spec import DatasetSpec, FileSpec
from ..store import StoreError, open_text, safe_name

__all__ = ["DATASETS", "EXTRACTORS", "KINDS", "READERS"]

csv.field_size_limit(1 << 30)

KINDS = {
    # an epitope that is not an identified compound, tested in an immune assay
    "epitope_assay": ("epitope", "assay"),
    # the protein (antigen) an epitope is a part of
    "epitope_antigen": ("epitope", "protein"),
    # a T-cell or B-cell receptor and an epitope it recognises
    "receptor_epitope": ("receptor", "epitope"),
}

_DL = "https://www.iedb.org/downloader.php?file_name=doc/"
_LICENCE = "CC BY 4.0"

#: Columns kept per table, as "<category row> | <field row>" of the export.
_ASSAY_COMMON = (
    "Assay ID | IEDB IRI", "Reference | PMID", "Reference | Submission ID",
    "Epitope | IEDB IRI", "Epitope | Object Type", "Epitope | Name", "Epitope | Reference Name",
    "Epitope | IRI", "Epitope | Molecule Parent", "Epitope | Molecule Parent IRI",
    "Epitope | Source Molecule IRI", "Epitope | Species", "Epitope | Species IRI",
    "Host | Name", "Host | IRI",
    "1st in vivo Process | Process Type", "1st in vivo Process | Disease",
    "Assay | Method", "Assay | Response measured", "Assay | Units", "Assay | IRI",
    "Assay | Measurement Inequality", "Assay | Quantitative measurement",
    "Assay | Number of Subjects Tested", "Assay | Number of Subjects Positive")
_RECEPTOR = (
    "Receptor | Group IRI", "Receptor | IEDB Receptor ID", "Receptor | Reference Name",
    "Receptor | Type", "Reference | IEDB IRI", "Epitope | IEDB IRI", "Epitope | Name",
    "Epitope | Source Molecule", "Epitope | Source Organism", "Assay | Type",
    "Assay | IEDB IDs", "Assay | MHC Allele Names",
    "Chain 1 | Type", "Chain 1 | Organism IRI", "Chain 1 | Curated V Gene",
    "Chain 1 | Curated J Gene", "Chain 1 | CDR3 Curated", "Chain 1 | CDR3 Calculated",
    "Chain 2 | Type", "Chain 2 | Organism IRI", "Chain 2 | Curated V Gene",
    "Chain 2 | Curated J Gene", "Chain 2 | CDR3 Curated", "Chain 2 | CDR3 Calculated")
_KEEP: dict[str, tuple[str, ...]] = {
    "tcell": _ASSAY_COMMON + (
        "Assay | Qualitative Measurement", "Effector Cell | Name",
        "Effector Cell | Source Tissue", "MHC Restriction | Name", "MHC Restriction | Class"),
    "bcell": _ASSAY_COMMON + (
        "Assay | Qualitative Measure", "Assay Antibody | Heavy chain isotype",
        "Assay Antibody | Light chain isotype", "Assay Antibody | Antibody Name"),
    "tcr": _RECEPTOR,
    "bcr": _RECEPTOR,
    "reference": ("Reference ID | IEDB IRI", "Reference | Type", "Reference | PMID",
                  "Reference | Submission ID", "Reference | Journal", "Reference | Date",
                  "Reference | Title"),
}


# ----------------------------------------------------------------------------- readers
def _clean(value: str | None) -> str | None:
    text = (value or "").strip()
    return text or None


def _iedb_tsv(path: Path, spec: FileSpec) -> Iterator[list[str | None]]:
    """An IEDB export: two header rows joined, then the ``_KEEP`` columns of each record.

    Fields are quoted where they hold tabs or quotes, so the csv module reads them.
    """
    keep = _KEEP.get(spec.table)
    with open_text(path) as fh:
        reader = csv.reader(fh, delimiter="\t")
        try:
            top, field = next(reader), next(reader)
        except StopIteration:
            raise StoreError(f"{path.name}: fewer than two header rows") from None
        header = [f"{a.strip()} | {b.strip()}" for a, b in zip(top, field)]
        if keep is None:
            idx = list(range(len(header)))
        else:
            missing = [c for c in keep if c not in header]
            if missing:
                raise StoreError(f"{path.name}: the export has no column(s) {missing}; "
                                 "IEDB changed its layout")
            idx = [header.index(c) for c in keep]
        yield [header[i] for i in idx]
        width = len(header)
        for record in reader:
            if not any(f.strip() for f in record):
                continue
            record = (record + [""] * width)[:width]
            yield [_clean(record[i]) for i in idx]


def _pipe(path: Path, spec: FileSpec) -> Iterator[list[str | None]]:
    """A headerless ``|``-separated file; the header is ``spec.columns``."""
    yield list(spec.columns)
    with open_text(path) as fh:
        for line in fh:
            line = line.rstrip("\r\n")
            if line.strip():
                parts = line.split("|")
                yield [_clean(p) for p in (parts + [""] * len(spec.columns))
                       [:len(spec.columns)]]


READERS = {"iedb_tsv": _iedb_tsv, "pipe": _pipe}

# --------------------------------------------------------------------------- the dataset
DATASETS = (
    DatasetSpec(
        "iedb", "IEDB (Immune Epitope Database) exports", (49,), "https://www.iedb.org/",
        _LICENCE,
        (FileSpec(_DL + "tcell_full_v3_tsv.zip", "tcell_full_v3_tsv.zip", "tcell",
                  fmt="iedb_tsv",
                  note="45 MB zip, 1.35 GB TSV, about 578,000 T-cell assays; 30 of its 161 "
                       "columns are loaded"),
         FileSpec(_DL + "tcr_full_v3_tsv.zip", "tcr_full_v3_tsv.zip", "tcr", fmt="iedb_tsv",
                  note="9 MB zip; T-cell receptors and the epitopes they recognise"),
         FileSpec(_DL + "bcr_full_v3_tsv.zip", "bcr_full_v3_tsv.zip", "bcr", fmt="iedb_tsv",
                  note="1.4 MB zip; B-cell receptors (antibodies) and their epitopes"),
         FileSpec(_DL + "reference_full_v3_tsv.zip", "reference_full_v3_tsv.zip", "reference",
                  fmt="iedb_tsv", note="3.3 MB zip; IEDB reference id -> PubMed id"),
         FileSpec(_DL + "epitope_id_chebi_id_pubchem_id_maps.txt",
                  "epitope_id_chebi_id_pubchem_id_maps.txt", "compound_map", fmt="pipe",
                  columns=("epitope_iri", "chebi_iri", "pubchem_url"),
                  note="non-peptidic epitopes -> ChEBI and PubChem, as three URLs per line"),
         FileSpec(_DL + "bcell_full_v3_tsv.zip", "bcell_full_v3_tsv.zip", "bcell",
                  fmt="iedb_tsv", optional=True,
                  note="91 MB zip, 3.27 GB TSV, about 1.4 million B-cell and antibody "
                       "assays; optional because of the size of the built store"),
         FileSpec(_DL + "epitope_full_v3_tsv.zip", "epitope_full_v3_tsv.zip", "", fmt="raw",
                  optional=True, note="112 MB zip, 1.07 GB TSV; every epitope"),
         FileSpec(_DL + "antigen_full_v3_tsv.zip", "antigen_full_v3_tsv.zip", "", fmt="raw",
                  optional=True, note="3 MB zip; antigens with epitope and assay counts"),
         FileSpec(_DL + "mhc_ligand_full_tsv.zip", "mhc_ligand_full_tsv.zip", "", fmt="raw",
                  optional=True,
                  note="323 MB ZIP64, 10 GB TSV of MHC binding and elution assays; not "
                       "loaded (use the iedb connector's mhc_by_sequence per peptide)")),
        version="exports of 2026-10-01 (IEDB publishes no release number; the exports are "
                "regenerated as curation proceeds)",
        notes="Curated immune epitopes and the T-cell, B-cell and receptor assays that "
              "tested them, from the literature (PubMed) and direct submissions. Assay rows "
              "carry the source's own outcome, negatives included, and the host, effector "
              "cell, tissue and measurement; non-peptidic epitopes (drugs, haptens, natural "
              "products) are keyed by PubChem/ChEBI so they meet compound rows of other "
              "sources. Licence: CC BY 4.0 (iedb.org JSON-LD and the site's licence text); "
              "the terms of use add that submitters may claim intellectual-property rights "
              "in data they submitted. robots.txt sets Crawl-delay: 10, so fetch sends every "
              "request (each file is a size probe and a download) 10.5 s apart.",
        relations=("compound_assay", "epitope_assay", "epitope_antigen", "receptor_epitope"),
        commercial_use="allowed",
        upstream=(),
        min_interval_s=10.5),
)


# ---------------------------------------------------------------------------- extractor
def _c(name: str) -> str:
    """The store column of an export column (``Assay | IRI`` -> ``Assay_IRI``)."""
    return safe_name(name)


_NUM = re.compile(r"/(?:epitope|reference|receptor|assay)/(\d+)\s*$")
_TAXON = re.compile(r"NCBITaxon_(\d+)$")
_ONTIE = re.compile(r"/(ONTIE_\d+)$")
_IEDB_TAXON = re.compile(r"ontology\.iedb\.org/taxon/(\d+)$")
_OBI = re.compile(r"/(OBI)_(\d+)$")
_CHEBI = re.compile(r"CHEBI[_:](\d+)$")
_UNIPROT = re.compile(r"uniprot\.org/uniprot/([A-Z0-9]+(?:-\d+)?)$", re.I)
_NCBI_PROTEIN = re.compile(r"ncbi\.nlm\.nih\.gov/protein/([A-Za-z0-9_.]+)$")
_CID = re.compile(r"cid=(\d+)")

_POSITIVE = {"positive", "positive-high", "positive-intermediate", "positive-low"}


def _num(iri: str | None) -> str | None:
    m = _NUM.search(iri or "")
    return m.group(1) if m else None


def _species(iri: str | None, name: str | None = None) -> tuple[str | None, str | None]:
    """(species id, the name to note) for a host, source or receptor organism IRI.

    The id is always prefixed: ``ncbitaxon:<n>`` for an NCBI taxon, else IEDB's own term
    (``iedb:ONTIE_<n>`` for a strain or transgenic line such as "Mus musculus HLA-A2 Tg",
    ``iedb:taxon.<n>`` for an IEDB taxon). The name is returned only when the id is not
    an NCBI taxon, for the row's note, so a strain stays readable.
    """
    text = iri or ""
    m = _TAXON.search(text)
    if m:
        return f"ncbitaxon:{m.group(1)}", None
    m = _ONTIE.search(text)
    if m:
        return f"iedb:{m.group(1)}", v(name)
    m = _IEDB_TAXON.search(text)
    if m:
        return f"iedb:taxon.{m.group(1)}", v(name)
    return None, v(name)


def _outcome(call: str | None) -> str | None:
    text = (v(call) or "").lower()
    if text in _POSITIVE:
        return "positive"
    if text == "negative":
        return "negative"
    return None


def _compounds(conn: sqlite3.Connection) -> dict[str, tuple[str | None, str | None]]:
    """IEDB epitope number -> (PubChem CID, ChEBI number) from the compound map."""
    out: dict[str, tuple[str | None, str | None]] = {}
    if has(conn, "compound_map"):
        for r in rows(conn, "SELECT * FROM compound_map"):
            num = _num(r["epitope_iri"])
            cid, chebi = _CID.search(r["pubchem_url"] or ""), _CHEBI.search(r["chebi_iri"] or "")
            if num:
                out[num] = (cid.group(1) if cid else None, chebi.group(1) if chebi else None)
    return out


class _Epitopes:
    """The id a row gives an epitope: a compound id for an identified molecule."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.map = _compounds(conn)

    def __call__(self, iri: str | None, object_type: str | None = None,
                 molecule_iri: str | None = None) -> tuple[str | None, bool]:
        """(id, is a compound) for an epitope IRI."""
        num = _num(iri)
        if not num:
            return None, False
        cid, chebi = self.map.get(num, (None, None))
        if not chebi and (object_type or "").lower() == "non-peptidic":
            m = _CHEBI.search(molecule_iri or "")
            chebi = m.group(1) if m else None
        if cid:
            return f"pubchem:{cid}", True
        if chebi:
            return f"chebi:{chebi}", True
        return f"iedb:epitope.{num}", False


def _assays(conn: sqlite3.Connection, table: str, epitope: _Epitopes,
            call_col: str, cell: str) -> Iterator[Row | None]:
    for r in rows(conn, f'SELECT * FROM "{table}"'):
        epi_iri = r[_c("Epitope | IEDB IRI")]
        sid, compound = epitope(epi_iri, r[_c("Epitope | Object Type")], r[_c("Epitope | IRI")])
        num = _num(epi_iri)
        sname = names(r[_c("Epitope | Name")], r[_c("Epitope | Reference Name")])
        # the source protein, once per epitope (the dedup keeps one row per pair)
        parent = r[_c("Epitope | Molecule Parent IRI")] or r[_c("Epitope | Source Molecule IRI")]
        up, ncbi = _UNIPROT.search(parent or ""), _NCBI_PROTEIN.search(parent or "")
        if up or ncbi:
            species, species_name = _species(r[_c("Epitope | Species IRI")],
                                             r[_c("Epitope | Species")])
            yield rel("epitope_antigen", "iedb", sid, sname,
                      f"uniprot:{up.group(1)}" if up else f"ncbiprotein:{ncbi.group(1)}",
                      r[_c("Epitope | Molecule Parent")], "listed",
                      note="; ".join(x for x in (
                          f"iedb:epitope.{num}" if compound else None,
                          f"species {species_name}" if species_name else None) if x) or None,
                      context=ctx(species=species))
        outcome = _outcome(r[call_col])
        obi = _OBI.search(r[_c("Assay | IRI")] or "")
        assay_id = _num(r[_c("Assay ID | IEDB IRI")])
        method, measure = r[_c("Assay | Method")], r[_c("Assay | Response measured")]
        pmid = v(r[_c("Reference | PMID")])
        ref = (f"pmid:{pmid}" if pmid else
               f"iedb:submission.{v(r[_c('Reference | Submission ID')])}"
               if v(r[_c("Reference | Submission ID")]) else None)
        if outcome is None:
            yield unresolved("compound_assay" if compound else "epitope_assay", "iedb", sid,
                             sname, names(method, measure),
                             f"assay {assay_id}: qualitative measure "
                             f"{r[call_col]!r} is neither positive nor negative",
                             reference=ref)
            continue
        if not obi:
            yield unresolved("compound_assay" if compound else "epitope_assay", "iedb", sid,
                             sname, names(method, measure),
                             f"assay {assay_id} has no OBI assay type", reference=ref)
            continue
        ineq = v(r[_c("Assay | Measurement Inequality")])
        value = v(r[_c("Assay | Quantitative measurement")])
        tested = v(r[_c("Assay | Number of Subjects Tested")])
        positive = v(r[_c("Assay | Number of Subjects Positive")])
        extra = ([f"MHC {r['MHC_Restriction_Name']}"
                  + (f" (class {r['MHC_Restriction_Class']})"
                     if v(r["MHC_Restriction_Class"]) else "")]
                 if cell == "T cell" and v(r["MHC_Restriction_Name"]) else [])
        if cell == "B cell":
            iso = "/".join(x for x in (v(r[_c("Assay Antibody | Heavy chain isotype")]),
                                       v(r[_c("Assay Antibody | Light chain isotype")])) if x)
            extra += [f"antibody {iso}"] if iso else []
        if tested and positive:
            extra.append(f"{positive}/{tested} subjects positive")
        host, host_name = _species(r[_c("Host | IRI")], r[_c("Host | Name")])
        if host_name:
            extra.append(f"host {host_name}")
        if compound:
            extra.append(f"iedb:epitope.{num}")
        yield rel(
            "compound_assay" if compound else "epitope_assay", "iedb", sid, sname,
            f"obi:{obi.group(2)}", names(method, measure), "known",
            reference=ref, outcome=outcome, note="; ".join(extra) or None,
            context=ctx(
                assay=cell,
                value=(f"{ineq}{value}" if ineq and ineq != "=" and value else value),
                unit=v(r[_c("Assay | Units")]) if value else None,
                species=host,
                cell=v(r["Effector_Cell_Name"]) if cell == "T cell" else None,
                tissue=v(r["Effector_Cell_Source_Tissue"]) if cell == "T cell" else None,
                condition=names(r[_c("1st in vivo Process | Process Type")],
                                r[_c("1st in vivo Process | Disease")]),
                n=tested, source_id=f"iedb:assay.{assay_id}" if assay_id else None))


def _receptors(conn: sqlite3.Connection, table: str, epitope: _Epitopes,
               pmids: dict[str, str]) -> Iterator[Row | None]:
    for r in rows(conn, f'SELECT * FROM "{table}"'):
        group = _num(r[_c("Receptor | Group IRI")])
        sid, _ = epitope(r[_c("Epitope | IEDB IRI")])
        cdr3 = [v(r[_c(f"Chain {i} | CDR3 Curated")]) or v(r[_c(f"Chain {i} | CDR3 Calculated")])
                for i in (1, 2)]
        types = [v(r[_c(f"Chain {i} | Type")]) for i in (1, 2)]
        chains = " / ".join(f"{t or 'chain'} {c}" for t, c in zip(types, cdr3) if c)
        ref_num = _num(r[_c("Reference | IEDB IRI")])
        ref = (f"pmid:{pmids[ref_num]}" if ref_num in pmids
               else f"iedb:reference.{ref_num}" if ref_num else None)
        mhc = v(r[_c("Assay | MHC Allele Names")])
        kind = v(r[_c("Receptor | Type")])
        yield rel("receptor_epitope", "iedb", f"iedb:receptor.{group}" if group else None,
                  names(r[_c("Receptor | Reference Name")],
                        f"CDR3 {chains}" if chains else None),
                  sid, names(r[_c("Epitope | Name")]), "known", reference=ref,
                  note="; ".join(x for x in (f"{kind} receptor" if kind else None,
                                             f"MHC {mhc.replace('|', ', ')}" if mhc else None)
                                 if x) or None,
                  context=ctx(assay=v(r[_c("Assay | Type")]),
                              species=_species(r[_c("Chain 1 | Organism IRI")])[0]
                              or _species(r[_c("Chain 2 | Organism IRI")])[0]))


def _iedb(conn: sqlite3.Connection) -> Iterator[Row | None]:
    epitope = _Epitopes(conn)
    if has(conn, "tcell"):
        yield from _assays(conn, "tcell", epitope, "Assay_Qualitative_Measurement", "T cell")
    if has(conn, "bcell"):
        yield from _assays(conn, "bcell", epitope, "Assay_Qualitative_Measure", "B cell")
    pmids: dict[str, str] = {}
    if has(conn, "reference"):
        for r in rows(conn, "SELECT * FROM reference"):
            num, pmid = _num(r[_c("Reference ID | IEDB IRI")]), v(r[_c("Reference | PMID")])
            if num and pmid:
                pmids[num] = pmid
    for table in ("tcr", "bcr"):
        if has(conn, table):
            yield from _receptors(conn, table, epitope, pmids)


EXTRACTORS = {"iedb": _iedb}
