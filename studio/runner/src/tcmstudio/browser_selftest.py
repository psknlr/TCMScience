"""A known answer for browser research jobs: one planted world studied the way a job studies it.

``bioagent.research.planted`` builds small synthetic snapshots whose right answer is known (the
'selective' world: the formula's constituents act on pathway A, and the specificity check has
control herbs to draw). Studying it runs the snapshot store (pyarrow), the ledger, the herb layer,
NPASS activities, the Reactome enrichment with its seeded degree-matched permutation null
(CPython's MT19937, so a browser draws exactly what the runner draws), the governed analysis, the
rebuttals, the nine exports and their recomputation — in seconds, with no download. It does not
run everything a real research job runs: the world has no STRING, disease, CMAUP or LOTUS layer,
so network.tsv and disease_targets.tsv are header only and the network topology (degree,
betweenness) is not exercised; the 葛根芩连汤 comparison on the real files covers those.

The answer it must give is committed (``data/browser_selftest.json``): the five tables as native
CPython wrote them. :func:`run` studies the world here and compares; the browser's job service runs
it in a job worker ("Check this browser" in Studio; studio/e2e/science.spec.mjs in CI), and the
committed answer is kept current by ``python -m tcmstudio.browser_selftest --write`` (checked in
studio/runner/tests/test_browser_selftest.py). Tables are compared byte for byte; when bytes differ,
every cell is compared, numbers within a relative 1e-12 (the tolerance np_exports.verify allows a
recomputed p-value), and the report says which held — a difference is shown, never smoothed over.
"""

from __future__ import annotations

import hashlib
import json
import math
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable, Mapping

__all__ = ["EXPECTED", "SCENARIO", "QUESTION", "PERMUTATIONS", "TABLES", "run", "study", "compare"]

EXPECTED = Path(__file__).with_name("data") / "browser_selftest.json"
SCHEMA = "tcmstudio.browser-selftest/1"
SCENARIO = "selective"
QUESTION = "葛根芩连汤的实测靶点集中在哪些通路？"
#: the planted worlds' own analysis: the assayed background, 200 permutations (enough to draw the
#: null for the planted pathway in seconds; the research job's default is 1000)
PERMUTATIONS = 200
TABLES = ("compounds.tsv", "targets.tsv", "disease_targets.tsv", "enrichment.tsv", "network.tsv")
RELATIVE = 1e-12

Emit = Callable[[str, Mapping[str, Any]], None]


def _sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def study(work_dir: str | Path, *, emit: Emit | None = None) -> dict[str, Any]:
    """Build the planted world under ``work_dir`` and study it through tcmstudio.browser_jobs (the
    functions a browser job calls). → the tables, their digests, the summary and the verification."""
    from bioagent.research.planted import build_world
    from bioagent.sources.ledger import SnapshotLedger

    from . import browser_jobs

    root = Path(work_dir)
    snapshots = root / "snapshots"
    ledger = SnapshotLedger(snapshots / "ledger.jsonl")
    if emit:
        emit("log", {"text": f"building the planted world '{SCENARIO}'"})
    build_world(SCENARIO, snapshots, ledger=ledger)
    args = {"question": QUESTION, "background": "assayed", "permutations": PERMUTATIONS,
            "snapshot_root": str(snapshots), "ledger": str(ledger.path),
            "state_dir": str(root / "state"), "out_dir": str(root / "out")}
    if emit:
        emit("log", {"text": "studying it (the research job's sixteen stages)"})
    ran = browser_jobs.run_research(args)
    out = root / "out"
    verified = browser_jobs.verify_research({"out_dir": str(out), "snapshot_root": str(snapshots),
                                             "ledger": str(ledger.path)})
    tables = {name: (out / name).read_text(encoding="utf-8") for name in TABLES
              if (out / name).is_file()}
    return {"exit": ran.get("exit"), "summary": ran.get("summary"), "refused": ran.get("refused"),
            "tables": tables, "digests": {n: _sha256(out / n) for n in tables},
            "verify": {"status": verified.get("status"), "problems": verified.get("problems") or []}}


def _cells(text: str) -> list[list[str]]:
    return [line.split("\t") for line in text.splitlines()]


def _number(cell: str) -> float | None:
    try:
        value = float(cell)
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def compare(got: Mapping[str, str], want: Mapping[str, str]) -> dict[str, Any]:
    """Table by table: "identical" (the same bytes), "within_tolerance" (every non-numeric cell the
    same, every number within RELATIVE), or "different" with the first differing cells."""
    out: dict[str, Any] = {}
    for name in TABLES:
        a, b = got.get(name), want.get(name)
        if a is None or b is None:
            out[name] = {"status": "missing", "detail": "not written" if a is None else "no committed answer"}
            continue
        if a == b:
            out[name] = {"status": "identical"}
            continue
        ra, rb = _cells(a), _cells(b)
        diffs: list[dict[str, Any]] = []
        worst = 0.0
        if len(ra) != len(rb):
            diffs.append({"rows": [len(ra), len(rb)]})
        for i, (x, y) in enumerate(zip(ra, rb)):
            if len(x) != len(y):
                diffs.append({"row": i, "columns": [len(x), len(y)]})
                continue
            for j, (u, v) in enumerate(zip(x, y)):
                if u == v:
                    continue
                nu, nv = _number(u), _number(v)
                if nu is not None and nv is not None:
                    rel = abs(nu - nv) / max(abs(nu), abs(nv), 1e-300)
                    worst = max(worst, rel)
                    if rel <= RELATIVE:
                        continue
                diffs.append({"row": i, "column": j, "got": u, "want": v})
        out[name] = ({"status": "within_tolerance", "max_relative": worst} if not diffs
                     else {"status": "different", "first": diffs[:5], "count": len(diffs)})
    return out


def _expected() -> dict[str, Any]:
    return json.loads(EXPECTED.read_text(encoding="utf-8"))


def run(args: Mapping[str, Any] | None = None, emit: Emit | None = None) -> dict[str, Any]:
    """Study the planted world here and compare with the committed answer. → {"status": "PASS" |
    "FAIL", "tables": {name: comparison}, ...}. PASS needs the run to finish (exit 0 or 1, both
    results), its exports to recompute (verify PASS) and every table identical or within tolerance."""
    args = dict(args or {})
    work = Path(args["work_dir"]) if args.get("work_dir") else Path(tempfile.mkdtemp(prefix="tcms-selftest-"))
    got = study(work, emit=emit)
    want = _expected()
    tables = compare(got["tables"], want["tables"])
    ok = (got["exit"] in (0, 1) and got["verify"]["status"] == "PASS"
          and all(t["status"] in ("identical", "within_tolerance") for t in tables.values()))
    import platform
    return {"schema": SCHEMA, "status": "PASS" if ok else "FAIL", "scenario": SCENARIO,
            "permutations": PERMUTATIONS, "exit": got["exit"], "refused": got["refused"],
            "verify": got["verify"], "tables": tables, "digests": got["digests"],
            "expected_digests": want["digests"], "expected_from": want.get("recorded_with"),
            "here": {"python": platform.python_version(), "platform": sys.platform,
                     "machine": platform.machine()},
            "released": (got["summary"] or {}).get("released"),
            "work_dir": str(work)}


def _write() -> int:
    import platform
    with tempfile.TemporaryDirectory(prefix="tcms-selftest-") as tmp:
        got = study(tmp)
    if got["exit"] not in (0, 1) or got["verify"]["status"] != "PASS":
        print(f"the planted world did not study cleanly: exit {got['exit']}, verify {got['verify']}",
              file=sys.stderr)
        return 1
    doc = {"schema": SCHEMA, "scenario": SCENARIO, "question": QUESTION,
           "permutations": PERMUTATIONS, "digests": got["digests"], "tables": got["tables"],
           "recorded_with": {"python": platform.python_version(), "platform": sys.platform,
                             "machine": platform.machine()}}
    EXPECTED.parent.mkdir(parents=True, exist_ok=True)
    EXPECTED.write_text(json.dumps(doc, indent=1, ensure_ascii=False, sort_keys=True) + "\n",
                        encoding="utf-8")
    print(f"wrote {EXPECTED}")
    return 0


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="The browser research jobs' known answer (planted world).")
    ap.add_argument("--write", action="store_true", help="record the answer native CPython gives")
    a = ap.parse_args(argv)
    if a.write:
        return _write()
    report = run()
    print(json.dumps({k: v for k, v in report.items() if k != "work_dir"}, indent=2, ensure_ascii=False))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
