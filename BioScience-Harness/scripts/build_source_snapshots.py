#!/usr/bin/env python
"""Build source snapshots from downloaded raw files.

    # the 葛根芩连汤 gold build (herb layer + NPASS + CMAUP + LOTUS, restricted to the
    # four herbs' source species) and its composition table:
    python scripts/build_source_snapshots.py gold --raw DIR --out DIR

    # one whole source:
    python scripts/build_source_snapshots.py source npass --raw DIR --out DIR

    # BindingDB rows for the gold build's compounds, from a file a person downloaded:
    python scripts/build_source_snapshots.py bindingdb --file BindingDB_All_202609_tsv.zip \\
        --release 202609 --out DIR

Pass ``--ledger PATH`` to record every snapshot id in an append-only, hash-chained ledger
(keep it outside the agent-writable tree, e.g. the workspace's ``audit/``); loads can then
be checked against it with ``load_snapshot(..., ledger=SnapshotLedger(PATH))``.

The raw files are the ones ``bioagent fetch`` downloads (see ``acquisition/sources.py``);
nothing here touches the network. Exit status is non-zero when the quality gate rejects a
snapshot or the gold standard is not reproduced.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bioagent.sources.build import build_gold, build_source  # noqa: E402
from bioagent.sources.herbs import GOLD  # noqa: E402
from bioagent.sources.ledger import SnapshotLedger  # noqa: E402
from bioagent.sources.snapshot import SnapshotError  # noqa: E402


def _summary(snap) -> dict:
    content = snap.manifest["content"]
    return {"snapshot_id": snap.snapshot_id, "qc": content["qc"]["status"],
            "nodes": len(snap.nodes), "edges": len(snap.edges),
            "warnings": content["qc"]["warnings"],
            "dropped": (content.get("extra") or {}).get("parse_report", {}).get("dropped", {})}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("gold")
    g.add_argument("--raw", required=True)
    g.add_argument("--out", required=True)
    s = sub.add_parser("source")
    s.add_argument("key", choices=("npass", "cmaup", "lotus"))
    s.add_argument("--raw", required=True)
    s.add_argument("--out", required=True)
    b = sub.add_parser("bindingdb")
    b.add_argument("--file", required=True)
    b.add_argument("--release", required=True)
    b.add_argument("--out", required=True)
    for p in (g, s, b):
        p.add_argument("--ledger", help="append snapshot ids to this hash-chained ledger")
    args = ap.parse_args(argv)
    ledger = SnapshotLedger(args.ledger) if args.ledger else None

    try:
        if args.cmd == "gold":
            build = build_gold(args.raw, args.out, ledger=ledger)
            report = {"snapshots": {k: _summary(v) for k, v in build.snapshots.items()},
                      "gold_missing": build.missing,
                      "composition": build.composition}
            (Path(args.out) / "composition.json").write_text(
                json.dumps(build.composition, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps({k: v for k, v in report.items() if k != "composition"},
                             ensure_ascii=False, indent=2))
            print(f"composition rows: {len(build.composition)} "
                  f"(written to {Path(args.out) / 'composition.json'})")
            return 0 if build.passed else 2
        if args.cmd == "source":
            snap = build_source(args.key, args.raw, args.out, ledger=ledger)
        else:
            snap = build_source("bindingdb", ".", args.out, path=args.file, ledger=ledger,
                                release=args.release,
                                inchikeys=[ik for _, ik in GOLD.values()])
        print(json.dumps(_summary(snap), ensure_ascii=False, indent=2))
        return 0
    except SnapshotError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
