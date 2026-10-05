"""Templates for the reviewed manual imports, and a check that an import works end to end.

``templates(key)`` writes a dataset's files as the reader expects them: the header row and
nothing else. A template that ships with filled-in rows gets imported with those rows, so
a placeholder marked ``verified`` became a relation. ``templates(key, demo=True)`` writes a
separate demonstration set instead: invented names that cannot be mistaken for real ones,
every row ``pending``, so building it yields no relation until a person changes a status.

``verify(hub)`` is what a person runs after placing reviewed files: for each manual
dataset with files present it builds the store, runs the acceptance check and calls the
dataset's lookup tool once, the way an agent will. It reports how many datasets it
checked, so "nothing was there to check" is never read as "everything passed".
"""

from __future__ import annotations

import csv
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator

from .extra.traditional import KEY_DNA, KEY_FORMULAS, KEY_STANDARDS, SCHEMAS
from .spec import DatasetSpec

__all__ = ["MANUAL_TEMPLATES", "templates", "verify", "EXIT_NONE_CHECKED"]

#: dataset key -> (file name, table) of each reviewed template file
MANUAL_TEMPLATES: dict[str, tuple[tuple[str, str], ...]] = {
    KEY_FORMULAS: (("formula_herb.tsv", "formula_herb"),),
    KEY_STANDARDS: (("quality_standards.tsv", "quality_standards"),),
    KEY_DNA: (("specimen_metadata.tsv", "specimen_metadata"),),
}
#: ``verify`` exit status when no dataset had files to check (not a pass, not a failure)
EXIT_NONE_CHECKED = 3

_DEMO: dict[str, list[dict[str, str]]] = {
    "formula_herb": [
        {"formula_id": "DEMO-F1", "formula_name": "演示方剂甲（虚构）", "formula_version": "演示本",
         "herb_id": "DEMO-H1", "herb_name": "演示药材子（虚构）", "dose": "1", "dose_unit": "演示单位",
         "processing": "", "preparation": "", "reference": "虚构文献，仅用于演示",
         "source_url": "https://example.invalid/demo/f1", "locator": "演示页 1",
         "source_row_id": "DEMO-ROW-1", "review_status": "pending"},
        {"formula_id": "DEMO-F1", "formula_name": "演示方剂甲（虚构）", "formula_version": "演示本",
         "herb_id": "DEMO-H2", "herb_name": "演示药材丑（虚构）", "dose": "2", "dose_unit": "演示单位",
         "processing": "演示炮制", "preparation": "", "reference": "虚构文献，仅用于演示",
         "source_url": "https://example.invalid/demo/f1", "locator": "演示页 1",
         "source_row_id": "DEMO-ROW-2", "review_status": "pending"},
    ],
    "quality_standards": [
        {"standard_id": "DEMO-S1", "herb_id": "DEMO-H1", "source_name": "演示标准（虚构）",
         "edition": "演示版", "monograph": "演示药材子（虚构）", "test_item": "演示检查项",
         "method": "演示方法", "limit_value": "0", "limit_unit": "演示单位", "chemical_marker": "",
         "source_url": "https://example.invalid/demo/s1", "page": "0",
         "source_row_id": "DEMO-STD-1", "review_status": "pending"},
    ],
    "specimen_metadata": [
        {"sequence_id": "DEMO-SEQ-1", "herb_id": "DEMO-H1", "taxon_id": "DEMO-TAXON",
         "scientific_name": "Demo species (fictitious)", "marker": "DEMO-MARKER",
         "accession": "DEMO-ACC-1", "voucher": "DEMO-VOUCHER-1",
         "source_url": "https://example.invalid/demo/seq1", "review_status": "pending"},
    ],
}
_DEMO_FASTA = ">DEMO-SEQ-1 Demo species (fictitious), not a real sequence\nACGTACGTAC\n"


def templates(key: str, out_dir: str | Path, *, demo: bool = False) -> list[Path]:
    """Write ``key``'s template files into ``out_dir``; never over an existing file."""
    if key not in MANUAL_TEMPLATES:
        raise KeyError(f"no template for {key!r}; have {sorted(MANUAL_TEMPLATES)}")
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    targets = [(out / name, table) for name, table in MANUAL_TEMPLATES[key]]
    if demo and key == KEY_DNA:
        targets.append((out / "reference_sequences.fasta", ""))
    clash = [str(p) for p, _ in targets if p.exists()]
    if clash:
        raise FileExistsError(f"refusing to overwrite {clash}")
    written = []
    for path, table in targets:
        if not table:
            path.write_text(_DEMO_FASTA, encoding="utf-8")
        else:
            with path.open("w", encoding="utf-8", newline="") as fh:
                writer = csv.DictWriter(fh, fieldnames=list(SCHEMAS[table]), delimiter="\t",
                                        lineterminator="\n")
                writer.writeheader()
                if demo:
                    writer.writerows(_DEMO[table])
        written.append(path)
    return written


@contextmanager
def _hub_root(root: Path) -> Iterator[None]:
    """The lookup tools read ``$BIOAGENT_TCMDB``; point it at the hub being verified."""
    from ..config import ENV_TCMDB
    before = os.environ.get(ENV_TCMDB)
    os.environ[ENV_TCMDB] = str(root)
    try:
        yield
    finally:
        if before is None:
            os.environ.pop(ENV_TCMDB, None)
        else:
            os.environ[ENV_TCMDB] = before


def _probe(hub: Any, key: str) -> tuple[str, Callable[..., dict[str, Any]], str] | None:
    """The lookup an agent would make, with a value the built store holds."""
    from ..tools import tcmdb as tool
    if key == KEY_FORMULAS:
        rows = hub.query(key, "formula_herb", columns=["formula_name"], limit=1)
        return ("hkbu_formula_lookup", tool.hkbu_formula_lookup,
                rows[0]["formula_name"]) if rows and rows[0]["formula_name"] else None
    if key == KEY_STANDARDS:
        rows = hub.query(key, "quality_standards", columns=["monograph"], limit=1)
        return ("hkcmms_standard_lookup", tool.hkcmms_standard_lookup,
                rows[0]["monograph"]) if rows and rows[0]["monograph"] else None
    if key == KEY_DNA:
        rows = hub.query(key, "specimen_metadata", columns=["scientific_name"], limit=1)
        return ("hk_cmm_dna_lookup", tool.hk_cmm_dna_lookup,
                rows[0]["scientific_name"]) if rows and rows[0]["scientific_name"] else None
    return None


def verify(hub: Any, keys: list[str] | None = None) -> dict[str, Any]:
    """Build, check and look up every manual dataset that has files in its raw directory."""
    from .datasets import DATASETS
    manual: list[DatasetSpec] = [d for d in DATASETS if d.access == "manual"]
    wanted = [d for d in manual if keys is None or d.key in keys]
    unknown = sorted(set(keys or ()) - {d.key for d in manual})
    results: list[dict[str, Any]] = []
    for spec in wanted:
        raw = hub.raw_dir(spec.key)
        if not raw.is_dir() or not any(raw.iterdir()):
            continue
        entry: dict[str, Any] = {"dataset": spec.key, "ok": False}
        results.append(entry)
        try:
            report = hub.build(spec.key, log=lambda m: None)
        except Exception as exc:  # noqa: BLE001 - reported per dataset
            entry["error"] = f"build failed: {type(exc).__name__}: {exc}"
            continue
        entry["tables"], entry["relations"] = report["tables"], report["relations"]
        entry["unresolved"] = report["unresolved"]
        check = hub.check(spec.key)
        entry.update(check_ok=check["ok"], problems=check["problems"],
                     warnings=check["warnings"])
        entry["ok"] = check["ok"]
        probe = _probe(hub, spec.key)
        if probe is not None:
            name, fn, value = probe
            with _hub_root(Path(hub.root)):
                answer = fn(value, limit=5, include_pending=True) if name != \
                    "hkbu_formula_lookup" else fn(value, limit=5)
            entry["lookup"] = {"tool": name, "query": value, "status": answer["status"],
                               "records": len(answer["records"]),
                               "unreviewed_rows": answer.get("unreviewed_rows")}
            # a store the tool cannot read, or one it finds incomplete, has not worked
            if answer["status"] in ("not_loaded", "incomplete_dataset"):
                entry["ok"] = False
                entry["problems"] = [*entry["problems"],
                                     f"{name}: {answer['status']} ({answer.get('reason', '')})"]
    checked = len(results)
    return {"hub": str(hub.root), "checked": checked, "unknown": unknown,
            "passed": sum(1 for r in results if r["ok"]), "results": results,
            "outcome": ("NO DATASETS CHECKED" if not checked
                        else "ALL CHECKED DATASETS PASSED" if all(r["ok"] for r in results)
                        else "SOME CHECKS FAILED")}
