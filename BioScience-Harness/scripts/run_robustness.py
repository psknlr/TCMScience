#!/usr/bin/env python
"""Hold out each assay campaign (or assay family) in turn and see which pathways survive.

    python scripts/run_robustness.py --snapshots SNAP --ledger SNAP/audit/snapshots.jsonl \\
        --lock RUN/snapshot_lock.json --out RUN/robustness [--group aid|family] [--top 12]

Uses exactly the snapshots of the lock (as written by run_network_pharmacology.py), the
same analysis parameters as that run (screening hits against the assayed background by
default), and writes robustness.json and robustness.md.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bioagent.analysis.network_pharmacology import Parameters  # noqa: E402
from bioagent.analysis.skill_runner import read_lock  # noqa: E402
from bioagent.sources.herbs import GEGEN_QINLIAN  # noqa: E402
from bioagent.sources.ledger import SnapshotLedger  # noqa: E402
from bioagent.sources.snapshot import load_snapshot  # noqa: E402
from bioagent.studies.robustness import aid_of, assay_family, leave_one_group_out  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--snapshots", required=True)
    ap.add_argument("--ledger", required=True)
    ap.add_argument("--lock", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--group", choices=("aid", "family"), default="aid")
    ap.add_argument("--top", type=int, default=12)
    ap.add_argument("--permutations", type=int, default=10000)
    ap.add_argument("--seed", type=int, default=20261001)
    ap.add_argument("--background", default="assayed")
    ap.add_argument("--hits", default="screening")
    args = ap.parse_args(argv)
    ledger = SnapshotLedger(args.ledger)
    ledger.verify()
    lock = read_lock(args.lock)
    snaps = []
    for key, sid in sorted(lock.items()):
        version = sid.split("@", 1)[1].rsplit("#", 1)[0]
        snap = load_snapshot(args.snapshots, key, version, ledger=ledger)
        if snap.snapshot_id != sid:
            print(f"refused: {key} loads as {snap.snapshot_id}, the lock pins {sid}",
                  file=sys.stderr)
            return 1
        snaps.append(snap)
    params = Parameters(permutations=args.permutations, seed=args.seed,
                        background=args.background, hits=args.hits)
    group_of = (lambda e: aid_of(e)) if args.group == "aid" else assay_family
    result = leave_one_group_out(snaps, formula=GEGEN_QINLIAN, params=params,
                                 source="pubchem_bioassay", group_of=group_of, top=args.top)
    result["lock"] = lock
    result["grouping"] = args.group
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"robustness_{args.group}.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [f"# Leave-one-{args.group}-out ({result['source']}, FDR {result['fdr']})", "",
             "| Pathway | Baseline q | Verdict |", "| --- | ---: | --- |"]
    for p, s in result["pathways"].items():
        lines.append(f"| {s['name']} | {s['baseline_q']:.3g} | {s['verdict']} |")
    lines += ["", "| Held out | Edges removed | " + " | ".join(
        result["pathways"][p]["name"][:30] for p in result["pathways"]) + " |",
        "| --- | ---: |" + " ---: |" * len(result["pathways"])]
    for r in result["runs"]:
        lines.append(f"| {r['held_out']} | {r['edges_removed']} | " + " | ".join(
            (f"{v['q_value']:.3g}" if v["q_value"] is not None else "–")
            + ("" if v["significant"] else " ✗") for v in r["pathways"].values()) + " |")
    (out / f"robustness_{args.group}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
