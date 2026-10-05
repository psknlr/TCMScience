"""Read-only lookup of locally built manual TCM datasets. Offline, no network.

These tools answer from stores a person built with ``bioagent.cli tcmdb build`` and never
download, call or crawl anything. Each answer says which of these it is:

* ``ok``: records matched and are returned;
* ``no_matching_record``: the store is complete, and nothing matched;
* ``withheld``: records matched, but none may be returned for this call — none a person
  has verified (``include_pending=True`` shows them, marked as such), or none whose data
  licence allows the call's data use;
* ``incomplete_dataset``: the store exists but lacks a table, a column or a file the
  dataset needs (a reference FASTA without its specimen table, say); add the files and
  rebuild;
* ``not_loaded``: no store was built where the tool looks (``store`` names the path).

None of them licenses a statement that a herb, formula or sample is ineffective or
unsafe: a missing record is only a missing record.

A record here is what one source lists, with its provenance: a formula's composition that
the source lists (not a proof it works), a monograph's test item (not a mechanism), a
reference sequence's specimen (not a species identification by itself). Every record
carries its dataset, the licence of the file it came from, that licence's reuse class and
the dataset's stated commercial use; that is the data's licence, not the licence of this
wrapper code (MIT).

Data use. ``commercial=True`` keeps only records whose licence allows commercial reuse.
An operator sets the floor for every call with ``BIOAGENT_DATA_USE=commercial``; a call
can narrow it, never widen it, and an unrecognised value counts as commercial.
"""

from __future__ import annotations

import os
from contextlib import closing
from typing import Any, Iterable

from ..tcmdb.datasets import dataset
from ..tcmdb.extra.traditional import SCHEMAS, VERIFIED
from ..tcmdb.hub import TCMDataHub
from ..tcmdb.spec import allows_commercial, licence_class
from ..tcmdb.store import connect

__all__ = ["hkbu_formula_lookup", "hkcmms_standard_lookup", "hk_cmm_dna_lookup",
           "ENV_DATA_USE", "STATUSES", "DATASETS_READ"]

_FORMULAS = "hkbu_formulas_manual"
_STANDARDS = "hkcmms_manual"
_DNA = "hk_cmm_dna_manual"
#: The datasets these tools read (``NativeTool.data``).
DATASETS_READ = (_FORMULAS, _STANDARDS, _DNA)

ENV_DATA_USE = "BIOAGENT_DATA_USE"
STATUSES = ("ok", "no_matching_record", "withheld", "incomplete_dataset", "not_loaded")
_PENDING = "not verified by a person (pass include_pending=True to see it)"
_COMMERCIAL = "licence does not allow commercial use"


def _check(name: str, value: Any, limit: Any, **flags: Any) -> tuple[str, int]:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty name or identifier")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError("limit must be an integer from 1 to 100")
    for flag, given in flags.items():
        if not isinstance(given, bool):
            raise ValueError(f"{flag} must be true or false")
    return value.strip(), limit


def _data_use(commercial: bool) -> str:
    """The call's data use: the operator's floor, narrowed by the caller."""
    floor = (os.environ.get(ENV_DATA_USE) or "academic").strip().lower()
    if floor not in ("academic", "commercial"):
        floor = "commercial"                             # unrecognised: the stricter one
    return "commercial" if commercial or floor == "commercial" else "academic"


def _dataset_licence(key: str) -> dict[str, Any]:
    spec = dataset(key)
    return {"license": spec.license, "class": licence_class(spec.license),
            "commercial_use": spec.commercial_use}


def _answer(key: str, hub: TCMDataHub, scope: str, use: str, status: str,
            records: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
    return {"status": status, "source_dataset": key, "store": str(hub.db_path(key)),
            "claim_scope": scope, "data_use": use,
            "dataset_licence": _dataset_licence(key), "records": records, **extra}


def _state(hub: TCMDataHub, key: str, tables: Iterable[str],
           files: Iterable[str] = ()) -> tuple[str, str] | None:
    """``(status, reason)`` when the store cannot answer, else ``None``."""
    path = hub.db_path(key)
    if not path.is_file():
        return "not_loaded", (f"no store at {path}; place the files in {hub.raw_dir(key)} "
                              f"and run: python -m bioagent.cli tcmdb build {key}")
    try:
        have = hub.tables(key)
    except Exception as exc:  # noqa: BLE001 - an unreadable store is an incomplete one
        return "incomplete_dataset", f"the store cannot be read ({exc})"
    gaps = []
    for table in tables:
        if table not in have:
            gaps.append(f"table {table!r} (its file was not imported)")
            continue
        missing = [c for c in SCHEMAS[table] if c not in have[table]]
        if missing:
            gaps.append(f"columns {missing} of {table!r}")
    gaps += [f"file {name!r}" for name in files if not (hub.raw_dir(key) / name).exists()]
    if gaps:
        return "incomplete_dataset", ("the store lacks " + "; ".join(gaps)
                                      + f"; add them and rebuild {key}")
    return None


def _file_licences(hub: TCMDataHub, key: str) -> dict[str, str]:
    """The licence each table was built under (``_tcmdb_files``), by table."""
    with closing(connect(hub.db_path(key))) as conn:
        return {r["tbl"]: r["license"] for r in conn.execute(
            "SELECT tbl, license FROM _tcmdb_files")}


def _provenance(row: dict[str, Any], key: str, licence: str | None) -> dict[str, Any]:
    spec = dataset(key)
    text = licence or spec.license
    return {**row, "source_dataset": key, "license": text, "licence_class": licence_class(text),
            "commercial_use": spec.commercial_use}


def _filter(rows: list[dict[str, Any]], use: str, include_pending: bool, *,
            review: bool) -> tuple[list[dict[str, Any]], dict[str, int]]:
    kept: list[dict[str, Any]] = []
    withheld: dict[str, int] = {}
    for row in rows:
        why = None
        if review and not include_pending and (row.get("review_status") or "") != VERIFIED:
            why = _PENDING
        elif use == "commercial" and not allows_commercial(row.get("license")):
            why = _COMMERCIAL
        if why:
            withheld[why] = withheld.get(why, 0) + 1
        else:
            kept.append(row)
    return kept, withheld


def _table_lookup(key: str, table: str, column: str, value: str, limit: int, *,
                  scope: str, commercial: bool, include_pending: bool,
                  files: Iterable[str] = ()) -> dict[str, Any]:
    hub = TCMDataHub()
    use = _data_use(commercial)
    state = _state(hub, key, (table,), files)
    if state:
        return _answer(key, hub, scope, use, state[0], [], reason=state[1])
    licence = _file_licences(hub, key).get(table)
    matched = [_provenance(r, key, licence)
               for r in hub.query(key, table, contains={column: value}, limit=10_000)]
    if not matched:
        return _answer(key, hub, scope, use, "no_matching_record", [])
    kept, withheld = _filter(matched, use, include_pending, review=True)
    status = "ok" if kept else "withheld"
    return _answer(key, hub, scope, use, status, kept[:limit], withheld=withheld,
                   matched=len(matched))


def hkbu_formula_lookup(formula: str, limit: int = 20, commercial: bool = False
                        ) -> dict[str, Any]:
    """A reviewed HKBU formula's listed constituents, each with its source provenance.

    Evidence is ``listed``: the source lists the herb in that version of the formula. It
    is composition, not efficacy, and a missing record is not evidence the formula lacks
    the herb or does not work. A processed herb is named as the processed material
    (炙黄芪, not 黄芪). Rows a person has not verified are not relations at all: they wait
    in the store's review queue, counted as ``unreviewed_rows``.
    """
    formula, limit = _check("formula", formula, limit, commercial=commercial)
    scope = "listed formula composition"
    hub = TCMDataHub()
    use = _data_use(commercial)
    state = _state(hub, _FORMULAS, ("formula_herb",))
    if state:
        return _answer(_FORMULAS, hub, scope, use, state[0], [], reason=state[1])
    matched = [_provenance(r, _FORMULAS, r.get("license"))
               for r in hub.relations("formula_herb", sources=[_FORMULAS], subject=formula,
                                      limit=10_000)]
    queue = sum(1 for r in hub.unresolved(_FORMULAS, limit=10 ** 6)
                if formula.casefold() in str(r.get("subject_name") or "").casefold()
                or formula == r.get("subject_id"))
    if not matched:
        return _answer(_FORMULAS, hub, scope, use, "no_matching_record", [],
                       unreviewed_rows=queue)
    kept, withheld = _filter(matched, use, False, review=False)
    return _answer(_FORMULAS, hub, scope, use, "ok" if kept else "withheld", kept[:limit],
                   withheld=withheld, matched=len(matched), unreviewed_rows=queue)


def hkcmms_standard_lookup(herb: str, limit: int = 20, include_pending: bool = False,
                           commercial: bool = False) -> dict[str, Any]:
    """One herb's HKCMMS monograph test items (identity, checks, assay limits).

    Query-only: a limit or an identification method is evidence about a sample meeting a
    standard, not about an effect. The record keeps its edition, method, value, unit and
    page; different editions coexist and are not merged. Only verified rows are returned
    unless ``include_pending=True``.
    """
    herb, limit = _check("herb", herb, limit, include_pending=include_pending,
                         commercial=commercial)
    return _table_lookup(_STANDARDS, "quality_standards", "monograph", herb, limit,
                         scope="quality-standard test item", commercial=commercial,
                         include_pending=include_pending)


def hk_cmm_dna_lookup(name: str, limit: int = 20, include_pending: bool = False,
                      commercial: bool = False) -> dict[str, Any]:
    """Reference sequences whose identified scientific name matches, with specimen metadata.

    Query-only: a barcode's base species and voucher, not a drug ingredient and not a
    species identification on its own (that needs a quality check and a discrimination
    method). The sequences themselves stay in the raw FASTA file, which the dataset needs:
    without it the answer is ``incomplete_dataset``. Only verified rows are returned unless
    ``include_pending=True``.
    """
    name, limit = _check("name", name, limit, include_pending=include_pending,
                         commercial=commercial)
    return _table_lookup(_DNA, "specimen_metadata", "scientific_name", name, limit,
                         scope="reference sequence and voucher specimen",
                         commercial=commercial, include_pending=include_pending,
                         files=("reference_sequences.fasta",))
