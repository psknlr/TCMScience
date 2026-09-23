#!/usr/bin/env python
"""Run the tcm.network-pharmacology skill on ledger-recorded snapshots.

    python scripts/build_source_snapshots.py gold --network --raw RAW --out SNAP \\
        --ledger SNAP/audit/snapshots.jsonl
    python scripts/run_network_pharmacology.py --snapshots SNAP \\
        --ledger SNAP/audit/snapshots.jsonl --out RUN

Writes compounds/targets/enrichment/network tables, claims.json, release.json,
provenance.json and limitations.md to RUN. Exit status is non-zero when a snapshot does not
match the ledger, the skill's contract refuses the run, or a claim is refused at release.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bioagent.analysis.network_pharmacology import Parameters  # noqa: E402
from bioagent.analysis.skill_runner import SkillRunRefused, run_skill  # noqa: E402
from bioagent.sources.ledger import LedgerError  # noqa: E402
from bioagent.sources.snapshot import SnapshotError  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--snapshots", required=True)
    ap.add_argument("--ledger", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--skill", default=str(ROOT / "skills" / "tcm" / "network-pharmacology"))
    ap.add_argument("--activity-max-nm", type=float, default=Parameters.activity_max_nm)
    ap.add_argument("--string-min-score", type=float, default=Parameters.string_min_score)
    ap.add_argument("--permutations", type=int, default=Parameters.permutations)
    ap.add_argument("--seed", type=int, default=Parameters.seed)
    args = ap.parse_args(argv)
    params = Parameters(activity_max_nm=args.activity_max_nm,
                        string_min_score=args.string_min_score,
                        permutations=args.permutations, seed=args.seed)
    try:
        provenance = run_skill(skill_dir=args.skill, snapshot_root=args.snapshots,
                               ledger_path=args.ledger, out_dir=args.out, params=params)
    except (SkillRunRefused, SnapshotError, LedgerError) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({k: provenance[k] for k in ("dataset_hashes", "excluded", "network",
                                                "claims", "psh_program_fingerprint",
                                                "result_digest")},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
