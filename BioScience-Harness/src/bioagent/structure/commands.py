"""Command-line entry point for structure prediction (``bioagent fold``)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

__all__ = ["register", "dispatch", "COMMANDS"]

COMMANDS = ("fold",)


def register(sub: argparse._SubParsersAction) -> None:
    f = sub.add_parser("fold", help="predict protein structures and check them: pLDDT, "
                                    "DSSP, geometry, TM-score against a reference")
    f.add_argument("source", nargs="?", default="", help="a FASTA file or one sequence")
    f.add_argument("--out", default="", help="output directory")
    f.add_argument("--method", default="esmatlas", choices=("esmatlas", "esmfold", "colabfold"),
                   help="esmatlas: the ESM Atlas service (remote); esmfold: local, needs torch "
                        "and transformers; colabfold: colabfold_batch")
    f.add_argument("--allow-remote", action="store_true",
                   help="permit sending sequences to a remote service and fetching references")
    f.add_argument("--reference", action="append", default=[],
                   help="NAME=REF, REF being PDB:<id>[:chain], UniProt:<acc> or a file; repeatable")
    f.add_argument("--timeout", type=float, default=300.0)
    f.add_argument("--verify", default="", help="re-check a finished run directory and exit")
    f.add_argument("--json", action="store_true")


def dispatch(a: argparse.Namespace) -> int | None:
    if a.cmd != "fold":
        return None
    from .fold import FoldConfig, run_fold, verify_run
    from .predict import PredictionError

    if a.verify:
        ok, problems = verify_run(a.verify)
        print("verified: every output matches run.json" if ok
              else "NOT VERIFIED:\n" + "\n".join(f"  - {p}" for p in problems))
        return 0 if ok else 1
    if not (a.source and a.out):
        print("a source and --out are required (or --verify DIR)", file=sys.stderr)
        return 2
    refs = {}
    for item in a.reference:
        name, sep, ref = item.partition("=")
        if not sep:
            print(f"--reference {item!r} is NAME=REF", file=sys.stderr)
            return 2
        refs[name] = ref
    try:
        results = run_fold(a.source, FoldConfig(method=a.method, allow_remote=a.allow_remote,
                                                references=refs, timeout=a.timeout), a.out)
    except (PredictionError, ValueError, OSError) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 1
    if a.json:
        print((Path(a.out) / "summary.json").read_text(encoding="utf-8"))
        return 0
    for r in results:
        cmp_ = r.comparison or {}
        tail = (f"; TM-score {cmp_['tm_score']} vs {cmp_['reference']}" if cmp_ else "")
        print(f"{r.name}: {len(r.sequence)} residues, mean pLDDT {r.confidence['mean_plddt']}"
              + tail)
        for w in r.warnings:
            print(f"  warning: {w}")
    print(f"report: {Path(a.out) / 'report.html'}")
    return 0
