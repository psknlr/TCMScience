#!/usr/bin/env python
"""Validate a network-pharmacology result: variants, sensitivity, controls, reproducibility.

    python scripts/run_np_validation.py --snapshots SNAP --ledger SNAP/audit/snapshots.jsonl \\
        --lock RUN/snapshot_lock.json --out RUN/validation \\
        [--controls 50] [--rewirings 20] [--permutations 1000]

Uses exactly the snapshots of the lock (as written by run_network_pharmacology.py) and
runs every check in ``bioagent.studies.validation``:
- each herb removed in turn (拆方), with one dose and one processing variant;
- both backgrounds and a threshold scan;
- the top hubs removed;
- random herb combinations of the formula's size;
- degree-preserving rewiring of the compound-target network.

It writes validation.json, validation.md and reproduce.json; the last can be checked with
``--verify``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bioagent.analysis.network_pharmacology import Parameters, run_network_pharmacology  # noqa: E402
from bioagent.analysis.skill_runner import read_lock  # noqa: E402
from bioagent.sources.herbs import GEGEN_QINLIAN  # noqa: E402
from bioagent.sources.ledger import SnapshotLedger  # noqa: E402
from bioagent.sources.snapshot import load_snapshot  # noqa: E402
from bioagent.studies import validation as V  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--snapshots", required=True)
    ap.add_argument("--ledger", required=True)
    ap.add_argument("--lock", default="", help="snapshot_lock.json of the run to validate")
    ap.add_argument("--out", required=True)
    ap.add_argument("--controls", type=int, default=50)
    ap.add_argument("--rewirings", type=int, default=20)
    ap.add_argument("--permutations", type=int, default=Parameters.permutations)
    ap.add_argument("--background", default=Parameters.background)
    ap.add_argument("--hits", default=Parameters.hits)
    ap.add_argument("--seed", type=int, default=Parameters.seed)
    ap.add_argument("--verify", default="", help="a reproduce.json to re-run and check")
    args = ap.parse_args(argv)
    ledger = SnapshotLedger(args.ledger)
    ledger.verify()
    if args.verify:
        ok, detail = V.verify_reproduction(args.verify, args.snapshots, ledger=ledger)
        print(("reproduced: " if ok else "NOT REPRODUCED: ") + detail)
        return 0 if ok else 1
    if not args.lock:
        ap.error("--lock is required unless --verify is given")
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
    formula = GEGEN_QINLIAN
    first = formula.components[0][0]
    variants = [("without", c[0], V.without_herb(formula, c[0])) for c in formula.components]
    variants += [("dose", first, V.with_dose(formula, first, "一斤")),
                 ("processing", first, V.with_processing(formula, first, "煨"))]
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    report = V.validate(snaps, formula, params, root=out / "derived", variants=variants,
                        controls=args.controls, rewirings=args.rewirings, seed=args.seed)
    report["lock"] = lock
    (out / "validation.json").write_text(json.dumps(report, ensure_ascii=False, indent=2,
                                                    default=str), encoding="utf-8")
    text = V.render_markdown(report)
    (out / "validation.md").write_text(text, encoding="utf-8")
    result = run_network_pharmacology(snaps, formula=formula, params=params)
    bundle = V.reproducibility_bundle(result, snaps, formula, out_dir=out, ledger=ledger)
    print(text)
    print(f"wrote {out / 'validation.json'}, {out / 'validation.md'} and {bundle}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
