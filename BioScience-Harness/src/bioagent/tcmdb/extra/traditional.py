"""Formulas, quality standards and reference sequences from the Hong Kong sources.

First phase of the integration guide (``TCMScience-Data-Integration-Guide.md``): the HKBU
Chinese medicine formula image database, the Hong Kong Chinese Materia Medica Standards
(HKCMMS) and the Hong Kong Chinese Materia Medica reference DNA sequences.

All three are **manual imports**. A person obtains the data under whatever terms apply to
it and places the files in ``raw/<key>/``; nothing in this module downloads, logs in to,
solves a challenge for or crawls a website. None of the three sites was confirmed to
offer an open bulk API, and the guide requires that a manual file be reviewed before it
becomes a relation.

* ``hkbu_formulas_manual`` (catalogue 134) — the composition of a formula as the source
  lists it, one row per herb in one version of one formula, yielding ``formula_herb``
  rows with evidence ``listed``. ``listed`` says the source lists the herb in the formula;
  it is not evidence that the formula works.
* ``hkcmms_manual`` (catalogue 135) — one row per monograph test item (identity, checks,
  assay). Kept as a queryable table only: a heavy-metal limit or an identification method
  is not a herb-ingredient relation, and forcing it into ``ingredient_target`` would
  invent a mechanism. A quality snapshot is a later, separately validated step.
* ``hk_cmm_dna_manual`` (catalogue 136) — the published reference sequences (kept as a
  raw FASTA file) and one row per sequence's specimen metadata. Also query-only: a DNA
  barcode identifies a base species, it is not a drug ingredient, and the hub's relation
  kinds (``tcmdb.rowkit``) have no herb-to-species kind. (The research snapshot schema,
  ``bioagent.sources.schema``, does: ``organism`` nodes and ``has_base_species`` edges,
  which ``sources.herbs`` already writes. A herb's base species can go there; a sequence
  object cannot yet.)

The three files are the project's own reviewed templates, so they are read strictly
(``FMT``): exactly the template's columns, the header's number of fields on every line,
a tab or line break inside a value only when quoted, valid UTF-8. The shared ``tsv``
reader is lenient for upstream exports that break records without quoting, and on a
filled-in template that leniency hid mistakes — one stray tab moved every later value a
column to the right and dropped the last, and the store still built. ``CHECKS`` adds what
the generic acceptance check cannot know: rows not yet reviewed, and FASTA records that
the specimen metadata does not match.

The licences below say what is actually known: nothing. A manual dataset is not licensed
for reuse because its adapter runs; the ``unknown`` licence class is a warning, not
permission, and the guide forbids rewriting it as CC BY or MIT to make a check pass.
"""

from __future__ import annotations

import csv
import io
import json
import re
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Iterator

from ..rowkit import Row, ctx, has, rel, rows, unresolved, v
from ..spec import DatasetSpec, FileSpec
from ..store import StoreError

__all__ = ["DATASETS", "EXTRACTORS", "READERS", "CHECKS", "SCHEMAS", "FMT", "VERIFIED",
           "FORMULA_HERB_COLUMNS", "QUALITY_STANDARD_COLUMNS", "SPECIMEN_COLUMNS",
           "material_name", "fasta_ids"]

KEY_FORMULAS = "hkbu_formulas_manual"
KEY_STANDARDS = "hkcmms_manual"
KEY_DNA = "hk_cmm_dna_manual"

_UNKNOWN_LICENSE = ("not stated; verify applicable data permissions before production use")
_HK_GOV_LICENSE = ("Hong Kong Government publication; the terms are not stated here — "
                   "verify reproduction and redistribution terms before production use")

#: Columns of the reviewed HKBU export. The file is UTF-8, tab-separated, its columns
#: fixed. One row is one herb in one version of one formula.
FORMULA_HERB_COLUMNS = ("formula_id", "formula_name", "formula_version", "herb_id",
                        "herb_name", "dose", "dose_unit", "processing", "preparation",
                        "reference", "source_url", "locator", "source_row_id",
                        "review_status")
#: One row per monograph test item.
QUALITY_STANDARD_COLUMNS = ("standard_id", "herb_id", "source_name", "edition", "monograph",
                            "test_item", "method", "limit_value", "limit_unit",
                            "chemical_marker", "source_url", "page", "source_row_id",
                            "review_status")
#: One row per reference sequence; ``sequence_id`` is the FASTA record's id.
SPECIMEN_COLUMNS = ("sequence_id", "herb_id", "taxon_id", "scientific_name", "marker",
                    "accession", "voucher", "source_url", "review_status")
#: table -> the columns its reviewed file must hold, each exactly once (any order)
SCHEMAS = {"formula_herb": FORMULA_HERB_COLUMNS,
           "quality_standards": QUALITY_STANDARD_COLUMNS,
           "specimen_metadata": SPECIMEN_COLUMNS}
#: The one review status that admits a record; anything else waits for a person.
VERIFIED = "verified"
_KNOWN_STATUSES = frozenset({VERIFIED, "pending", "rejected"})
#: The strict reader's format name (``FileSpec.fmt``).
FMT = "manual_template_tsv"


def _read_utf8(path: Path) -> str:
    try:
        return path.read_bytes().decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise StoreError(f"{path.name}: not valid UTF-8 (byte {exc.start}); save the file "
                         "as UTF-8 before importing it") from None


def _manual_tsv(path: Path, spec: FileSpec) -> Iterator[list[str | None]]:
    """A reviewed template file, read strictly; the first row yielded is its header.

    The header must hold exactly the template's columns (``SCHEMAS``), in any order, each
    once. Every record must have the header's number of fields: a record that does not is
    an error naming its line, not a row moved or cut to fit. A value may hold a tab, a
    line break or a leading quote only quoted, the way ``csv.writer(delimiter="\\t")``
    writes it. Any violation raises ``StoreError``, and ``TCMDataHub.build`` then leaves
    the previous store as it was.
    """
    expected = SCHEMAS[spec.table]
    reader = csv.reader(io.StringIO(_read_utf8(path), newline=""), delimiter="\t",
                        quotechar='"', doublequote=True, strict=True)
    header: list[str] | None = None
    try:
        for record in reader:
            if not any(field.strip() for field in record):
                continue                                  # a blank line
            if header is None:
                header = [field.strip() for field in record]
                missing = [c for c in expected if c not in header]
                unknown = [c for c in header if c not in expected]
                repeated = sorted({c for c in header if header.count(c) > 1})
                if missing or unknown or repeated:
                    raise StoreError(
                        f"{path.name}: the header must hold exactly the template's columns"
                        + (f"; missing {missing}" if missing else "")
                        + (f"; not in the template {unknown}" if unknown else "")
                        + (f"; repeated {repeated}" if repeated else ""))
                yield list(header)
                continue
            if len(record) != len(header):
                raise StoreError(
                    f"{path.name} line {reader.line_num}: {len(record)} fields where the "
                    f"header has {len(header)}; a tab or a line break inside a value must "
                    "be quoted")
            yield [(field.strip() or None) for field in record]
    except csv.Error as exc:
        raise StoreError(f"{path.name} line {reader.line_num}: {exc}") from None
    if header is None:
        raise StoreError(f"{path.name}: the file is empty; the template's header row is "
                         "missing")


READERS = {FMT: _manual_tsv}

DATASETS = (
    DatasetSpec(
        key=KEY_FORMULAS,
        name="HKBU formulas (reviewed manual export)",
        catalog=(134,),
        homepage="https://library.hkbu.edu.hk/electronic/libdbs/cmfid/index.html",
        license=_UNKNOWN_LICENSE,
        files=(FileSpec("", "formula_herb.tsv", "formula_herb", fmt=FMT),),
        access="manual",
        version="manual-export-v1",
        instructions="Export the formula composition data you are permitted to use, convert "
                     "it to the reviewed TSV in instructions/formula_herb.tsv (columns: "
                     "formula_id formula_name formula_version herb_id herb_name dose "
                     "dose_unit processing preparation reference source_url locator "
                     "source_row_id review_status), keep the upstream original beside it, "
                     "and place both in raw/hkbu_formulas_manual/.",
        relations=("formula_herb",),
        notes="Composition as listed by the source; not evidence of efficacy. A formula "
              "known under several sources or versions is several formula_id values; a "
              "crude drug and its processed form (生品/炮制品) are separate herb_id "
              "values; historical doses keep their original unit and are converted only "
              "with a separately referenced note.",
        commercial_use="unknown",
    ),
    DatasetSpec(
        key=KEY_STANDARDS,
        name="Hong Kong Chinese Materia Medica Standards (HKCMMS)",
        catalog=(135,),
        homepage="https://www.cmro.gov.hk/html/gb/useful_information/hkcmms/volumes.html",
        license=_HK_GOV_LICENSE,
        files=(FileSpec("", "quality_standards.tsv", "quality_standards", fmt=FMT),),
        access="manual",
        version="import edition",
        instructions="Read the monograph volumes you are permitted to use and record one "
                     "row per test item in the reviewed TSV in raw/hkcmms_manual/ "
                     "(columns: standard_id herb_id source_name edition monograph "
                     "test_item method limit_value limit_unit chemical_marker source_url "
                     "page source_row_id review_status). Keep the standard file's version, "
                     "method, sample basis and unit; do not record a limit or an "
                     "identification method as a herb-ingredient relation.",
        relations=(),
        notes="One row per monograph test item (identity, checks, assay). Query-only: the "
              "quality standard is evidence about a sample meeting a standard, not about "
              "an effect, and no relation kind expresses it. Different editions coexist; "
              "the effective date and scope decide which one a query should use.",
        commercial_use="unknown",
    ),
    DatasetSpec(
        key=KEY_DNA,
        name="Hong Kong Chinese Materia Medica reference DNA sequences",
        catalog=(136,),
        homepage="https://www.cmro.gov.hk/html/gb/useful_information/gcmti/research/"
                 "dna_sequences/CMMR_SL.html",
        license=_UNKNOWN_LICENSE,
        files=(FileSpec("", "reference_sequences.fasta", "", fmt="raw",
                        note="the reference sequences as published, kept as the original "
                             "FASTA file"),
               FileSpec("", "specimen_metadata.tsv", "specimen_metadata", fmt=FMT)),
        access="manual",
        version="import edition",
        instructions="Place the published reference sequences (FASTA) as "
                     "reference_sequences.fasta and one row per sequence in "
                     "specimen_metadata.tsv (columns: sequence_id herb_id taxon_id "
                     "scientific_name marker accession voucher source_url review_status) "
                     "in raw/hk_cmm_dna_manual/.",
        relations=(),
        notes="Query-only. A sequence identifies a base species and links it to a voucher "
              "specimen; it is not a drug ingredient or a compound. Authentication also "
              "needs a real sequence quality check, a reference set and a discrimination "
              "method, so nothing here is a species identification on its own.",
        commercial_use="unknown",
    ),
)


#: ``processing`` values that name the crude drug itself rather than a processed form.
_CRUDE = frozenset({"生", "生品", "生用", "原药材", "无", "none", "raw"})


def material_name(herb_name: object, processing: object) -> str | None:
    """The name of the material a row lists: 炙 with 黄芪 is 炙黄芪, not 黄芪.

    Identity downstream (``tcmdb.consensus.herb_key``) is read from the name, so a
    processing that lives only in another column made 黄芪 (生) and 黄芪 (炙) one herb in a
    consensus although they were two herb ids here. A name that already carries the
    processing (炙甘草 with 炙, or 炙黄芪 with 蜜炙) is kept as written, and so is a crude
    drug's. The row's note keeps both original fields.
    """
    name, proc = v(herb_name), v(processing)
    if not name or not proc or proc.lower() in _CRUDE or proc in name \
            or name.startswith(proc[-1]):
        return name
    return f"{proc}{name}"


def extract(conn: sqlite3.Connection) -> Iterator[Row]:
    """``formula_herb`` rows from a reviewed HKBU export.

    A row becomes a relation only when it has a stable formula id, a stable herb id, a
    stable source row id, a reference and a reviewer's ``verified`` mark. Everything else
    goes to the ``unresolved`` queue with its original fields, not into the relations
    table: the source gives the record, but not enough to join it to a formula and a herb
    without guessing. ``listed`` is the evidence — the source lists the herb in the
    formula — and it licenses no efficacy claim.
    """
    for r in rows(conn, "SELECT * FROM formula_herb"):
        fid = v(r["formula_id"])
        hid = v(r["herb_id"])
        rid = v(r["source_row_id"])
        reference = v(r["reference"])
        verified = v(r["review_status"]) == "verified"
        subject_id = f"{KEY_FORMULAS}:formula.{fid}" if fid else None
        if not fid or not hid or not rid or not reference or not verified:
            yield unresolved(
                "formula_herb", KEY_FORMULAS, subject_id, r["formula_name"], r["herb_name"],
                "missing stable id/reference or not manually verified",
                reference=reference,
                note=json.dumps(dict(r), ensure_ascii=False, sort_keys=True),
            )
            continue
        qualifiers = {
            k: r[k] for k in ("formula_version", "herb_name", "processing", "preparation",
                              "source_url", "locator") if v(r[k]) is not None
        }
        yield rel(
            "formula_herb", KEY_FORMULAS,
            subject_id, r["formula_name"],
            f"{KEY_FORMULAS}:herb.{hid}", material_name(r["herb_name"], r["processing"]),
            "listed", reference=reference,
            note=json.dumps(qualifiers, ensure_ascii=False, sort_keys=True),
            context=ctx(
                dose=v(r["dose"]), dose_unit=v(r["dose_unit"]),
                source_db="HKBU", source_id=rid,
            ),
        )


EXTRACTORS = {KEY_FORMULAS: extract}


# --------------------------------------------------------------------------- checks
def _review_warnings(conn: sqlite3.Connection, table: str) -> list[str]:
    if not has(conn, table):
        return []
    statuses = Counter(v(r[0]) for r in conn.execute(f'SELECT review_status FROM "{table}"'))
    out = []
    waiting = sum(n for s, n in statuses.items() if s != VERIFIED)
    if waiting:
        out.append(f"{table}: {waiting} of {sum(statuses.values())} rows are not verified; "
                   "lookups leave them out unless asked to include pending rows")
    odd = sorted(str(s) for s in statuses if s is not None and s not in _KNOWN_STATUSES)
    if odd:
        out.append(f"{table}: review_status values {odd} are not verified, pending or "
                   "rejected (a misspelt 'verified' keeps a reviewed row out)")
    return out


def fasta_ids(path: Path) -> tuple[list[str], list[str]]:
    """``(ids, empty)``: each record's id (the header's first word) in file order, and the
    ids of records with no sequence."""
    ids: list[str] = []
    empty: list[str] = []
    current: str | None = None
    length = 0
    for line in _read_utf8(path).splitlines():
        if line.startswith(">"):
            if current is not None and not length:
                empty.append(current)
            current = (line[1:].split() or [""])[0]
            ids.append(current)
            length = 0
        elif line.strip():
            length += len(line.strip())
    if current is not None and not length:
        empty.append(current)
    return ids, empty


_IUPAC = re.compile(r"[^ACGTURYSWKMBDHVN\-.*]", re.I)


def _check_dna(raw: Path, conn: sqlite3.Connection) -> tuple[list[str], list[str]]:
    """The FASTA and the specimen metadata must describe the same sequences."""
    problems: list[str] = []
    warnings = _review_warnings(conn, "specimen_metadata")
    fasta = raw / "reference_sequences.fasta"
    if not fasta.exists():
        return problems, warnings                       # reported as a missing file
    ids, empty = fasta_ids(fasta)
    if not ids:
        problems.append("reference_sequences.fasta holds no FASTA record")
    repeated = sorted({i for i in ids if ids.count(i) > 1})
    if repeated:
        problems.append(f"reference_sequences.fasta repeats the record ids {repeated[:5]}")
    if "" in ids:
        problems.append("reference_sequences.fasta has a record with an empty header")
    if empty:
        problems.append(f"reference_sequences.fasta records without a sequence: {empty[:5]}")
    odd = sum(1 for line in _read_utf8(fasta).splitlines()
              if line.strip() and not line.startswith(">") and _IUPAC.search(line.strip()))
    if odd:
        warnings.append(f"reference_sequences.fasta: {odd} sequence lines hold characters "
                        "that are not IUPAC nucleotide codes")
    if has(conn, "specimen_metadata"):
        meta = [v(r[0]) for r in conn.execute("SELECT sequence_id FROM specimen_metadata")]
        if None in meta:
            problems.append("specimen_metadata has rows without a sequence_id")
        unmatched = sorted({m for m in meta if m} - set(ids))
        if unmatched:
            problems.append(f"{len(unmatched)} specimen_metadata rows name a sequence_id that "
                            f"no FASTA record has (e.g. {unmatched[:3]})")
        unlisted = sorted(set(ids) - {m for m in meta if m})
        if unlisted:
            warnings.append(f"{len(unlisted)} FASTA records have no specimen_metadata row "
                            f"(e.g. {unlisted[:3]}); they cannot be looked up")
    return problems, warnings


#: dataset key -> fn(raw_dir, built store) -> (problems, warnings), run by
#: ``TCMDataHub.check`` after its generic checks.
CHECKS = {
    KEY_FORMULAS: lambda raw, conn: ([], []),          # unreviewed rows are the queue
    KEY_STANDARDS: lambda raw, conn: ([], _review_warnings(conn, "quality_standards")),
    KEY_DNA: _check_dna,
}
