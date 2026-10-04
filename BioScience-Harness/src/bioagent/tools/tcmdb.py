"""Read-only lookup of locally built manual TCM datasets. Offline, no network.

These tools answer from stores a person built with ``bioagent.cli tcmdb build`` and never
download, call or crawl anything. Each returns a status rather than raising when the
dataset is absent: ``not_loaded`` (the store was never built) and ``no_matching_record``
(nothing matched) both mean *no record was found*. Neither licenses a statement that a
herb, formula or sample is ineffective or unsafe.

A record here is what one source lists, with its provenance: a formula's composition that
the source lists (not a proof it works), a monograph's test item (not a mechanism), a
reference sequence's specimen (not a species identification by itself). The data's licence
travels with each record; it is not the licence of this wrapper code.
"""

from __future__ import annotations

from typing import Any

from ..tcmdb.hub import TCMDataHub

__all__ = ["hkbu_formula_lookup", "hkcmms_standard_lookup", "hk_cmm_dna_lookup"]

_FORMULAS = "hkbu_formulas_manual"
_STANDARDS = "hkcmms_manual"
_DNA = "hk_cmm_dna_manual"


def _check(name: str, value: Any, limit: Any) -> tuple[str, int]:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty name or identifier")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError("limit must be an integer from 1 to 100")
    return value.strip(), limit


def _missing(key: str, status: str) -> dict[str, Any]:
    return {"status": status, "source_dataset": key, "records": []}


def hkbu_formula_lookup(formula: str, limit: int = 20) -> dict[str, Any]:
    """A reviewed HKBU formula's listed constituents, each with its source provenance.

    Evidence is ``listed``: the source lists the herb in that version of the formula. It
    is composition, not efficacy, and a missing record is not evidence the formula lacks
    the herb or does not work.
    """
    formula, limit = _check("formula", formula, limit)
    hub = TCMDataHub()
    if not hub.db_path(_FORMULAS).is_file():
        return {"status": "not_loaded", "source_dataset": _FORMULAS, "records": []}
    records = hub.relations("formula_herb", sources=[_FORMULAS], subject=formula, limit=limit)
    return {"status": "ok" if records else "no_matching_record", "source_dataset": _FORMULAS,
            "claim_scope": "listed formula composition", "records": records}


def hkcmms_standard_lookup(herb: str, limit: int = 20) -> dict[str, Any]:
    """One herb's HKCMMS monograph test items (identity, checks, assay limits).

    Query-only: a limit or an identification method is evidence about a sample meeting a
    standard, not about an effect. The record keeps its edition, method, value, unit and
    page; different editions coexist and are not merged.
    """
    herb, limit = _check("herb", herb, limit)
    hub = TCMDataHub()
    if not hub.db_path(_STANDARDS).is_file():
        return _missing(_STANDARDS, "not_loaded")
    records = hub.query(_STANDARDS, "quality_standards", contains={"monograph": herb},
                        limit=limit)
    return {"status": "ok" if records else "no_matching_record", "source_dataset": _STANDARDS,
            "claim_scope": "quality-standard test item", "records": records}


def hk_cmm_dna_lookup(name: str, limit: int = 20) -> dict[str, Any]:
    """Reference sequences whose identified scientific name matches, with specimen metadata.

    Query-only: a barcode's base species and voucher, not a drug ingredient and not a
    species identification on its own (that needs a quality check and a discrimination
    method). The sequences themselves stay in the raw FASTA file.
    """
    name, limit = _check("name", name, limit)
    hub = TCMDataHub()
    if not hub.db_path(_DNA).is_file():
        return _missing(_DNA, "not_loaded")
    records = hub.query(_DNA, "specimen_metadata", contains={"scientific_name": name},
                        limit=limit)
    return {"status": "ok" if records else "no_matching_record", "source_dataset": _DNA,
            "claim_scope": "reference sequence and voucher specimen", "records": records}
