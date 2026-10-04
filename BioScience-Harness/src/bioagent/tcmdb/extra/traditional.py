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
  barcode identifies a base species, it is not a drug ingredient, and the project's
  relation kinds have no ``has_base_species``.

The licences below say what is actually known: nothing. A manual dataset is not licensed
for reuse because its adapter runs; the ``unknown`` licence class is a warning, not
permission, and the guide forbids rewriting it as CC BY or MIT to make a check pass.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Iterator

from ..rowkit import Row, ctx, rel, rows, unresolved, v
from ..spec import DatasetSpec, FileSpec

__all__ = ["DATASETS", "EXTRACTORS"]

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

DATASETS = (
    DatasetSpec(
        key=KEY_FORMULAS,
        name="HKBU formulas (reviewed manual export)",
        catalog=(134,),
        homepage="https://library.hkbu.edu.hk/electronic/libdbs/cmfid/index.html",
        license=_UNKNOWN_LICENSE,
        files=(FileSpec("", "formula_herb.tsv", "formula_herb", fmt="tsv"),),
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
        files=(FileSpec("", "quality_standards.tsv", "quality_standards", fmt="tsv"),),
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
               FileSpec("", "specimen_metadata.tsv", "specimen_metadata", fmt="tsv")),
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
            k: r[k] for k in ("formula_version", "processing", "preparation", "source_url",
                              "locator") if v(r[k]) is not None
        }
        yield rel(
            "formula_herb", KEY_FORMULAS,
            subject_id, r["formula_name"],
            f"{KEY_FORMULAS}:herb.{hid}", r["herb_name"],
            "listed", reference=reference,
            note=json.dumps(qualifiers, ensure_ascii=False, sort_keys=True),
            context=ctx(
                dose=v(r["dose"]), dose_unit=v(r["dose_unit"]),
                source_db="HKBU", source_id=rid,
            ),
        )


EXTRACTORS = {KEY_FORMULAS: extract}
