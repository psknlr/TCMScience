#!/usr/bin/env python
"""Decide what a network-pharmacology signal means with a governed inquiry.

    # the three planted worlds, whose true explanation is known in advance
    python scripts/run_inquiry.py --planted all --out RUN/inquiry

    # a pathway in a real run: exactly the snapshots of its lock, verified on the ledger
    python scripts/run_inquiry.py --snapshots SNAP --ledger SNAP/audit/snapshots.jsonl \\
        --lock RUN/snapshot_lock.json --pathway reactome:R-HSA-1234567 --out RUN/inquiry \\
        [--controls 50] [--rewirings 20] [--state RUN/state]

The rival explanations and what each predicts every analysis will show are sealed before
any analysis runs (``bioagent.research.inquiry``). PSH's inquiry engine then chooses each
next analysis by expected information per network-pharmacology run, updates belief from
the sealed predictions only, and stops when the leader has passed a severe test against
every rival and each test is replicated. It writes inquiry.json (the conclusion, every
step and the replayable trail) and inquiry.md per world or pathway; with --state the
inquiry is also recorded in PSH's world model and audit chain.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import _bootstrap

_bootstrap.bootstrap()

from bioagent.analysis.network_pharmacology import Parameters  # noqa: E402
from bioagent.research.inquiry import render_markdown, run_pathway_inquiry  # noqa: E402
from bioagent.research.planted import PATHWAY, SCENARIOS, TRUTH, build_world  # noqa: E402


def _write(result, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "inquiry.json").write_text(json.dumps(result.as_dict(), ensure_ascii=False,
                                                 indent=2, default=str), encoding="utf-8")
    (out / "inquiry.md").write_text(render_markdown(result), encoding="utf-8")


def _real(args, params) -> int:
    from bioagent.analysis.skill_runner import read_lock
    from bioagent.sources.ledger import SnapshotLedger
    from bioagent.sources.snapshot import load_snapshot
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
    out = Path(args.out)
    result = run_pathway_inquiry(snaps, pathway=args.pathway, root=out / "derived",
                                 params=params, controls=args.controls,
                                 rewirings=args.rewirings, state_dir=args.state or None)
    _write(result, out)
    print(render_markdown(result))
    return 0


def _planted(args, params) -> int:
    scenarios = SCENARIOS if args.planted == "all" else (args.planted,)
    out = Path(args.out)
    rows = []
    for scenario in scenarios:
        root = out / scenario
        snaps = build_world(scenario, root / "snapshots")
        state = (Path(args.state) / scenario) if args.state else None
        result = run_pathway_inquiry(snaps, pathway=PATHWAY, root=root / "derived",
                                     params=params, controls=args.controls,
                                     rewirings=args.rewirings, state_dir=state)
        _write(result, root)
        c = result.conclusion
        rows.append((scenario, TRUTH[scenario], c.verdict.value, c.leader,
                     f"{c.leader_posterior:.3f}", len(result.released),
                     f"{result.np_runs}/{result.np_runs_if_everything}"))
    print("| World | True explanation | Verdict | Leader | Posterior | Mechanism claims "
          "released | NP runs used / all |")
    print("| --- | --- | --- | --- | ---: | ---: | ---: |")
    for row in rows:
        print("| " + " | ".join(str(x) for x in row) + " |")
    wrong = [r for r in rows if r[3] != r[1] or r[2] != "accepted"]
    print(f"\n{len(rows) - len(wrong)} of {len(rows)} planted explanations recovered; "
          f"reports under {out}")
    return 1 if wrong else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--planted", choices=("all", *SCENARIOS), default="",
                    help="run on planted worlds instead of real snapshots")
    ap.add_argument("--snapshots", default="")
    ap.add_argument("--ledger", default="")
    ap.add_argument("--lock", default="", help="snapshot_lock.json of the run to question")
    ap.add_argument("--pathway", default="", help="the pathway whose signal is questioned")
    ap.add_argument("--out", required=True)
    ap.add_argument("--state", default="",
                    help="record the inquiry in a PSH world model and audit chain here")
    ap.add_argument("--controls", type=int, default=20)
    ap.add_argument("--rewirings", type=int, default=10)
    ap.add_argument("--permutations", type=int, default=Parameters.permutations)
    ap.add_argument("--seed", type=int, default=Parameters.seed)
    args = ap.parse_args(argv)
    params = Parameters(permutations=args.permutations, seed=args.seed)
    if args.planted:
        return _planted(args, params)
    missing = [k for k in ("snapshots", "ledger", "lock", "pathway") if not getattr(args, k)]
    if missing:
        ap.error("without --planted, give " + ", ".join(f"--{k}" for k in missing))
    return _real(args, params)


if __name__ == "__main__":
    raise SystemExit(main())
