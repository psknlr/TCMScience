"""Command-line entry point for ADMET prediction (``bioagent admet``)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

__all__ = ["register", "dispatch", "COMMANDS"]

COMMANDS = ("admet",)


def register(sub: argparse._SubParsersAction) -> None:
    a = sub.add_parser("admet", help="ADMET from structure: properties, drug-likeness rules, "
                                     "structural alerts and 22 TDC-trained endpoints")
    a.add_argument("molecules", nargs="?", default="",
                   help="a CSV (name,smiles), an SDF, a .smi file or one SMILES")
    a.add_argument("--out", default="")
    a.add_argument("--build-models", action="store_true",
                   help="download the TDC ADMET benchmark group (once) and train the models")
    a.add_argument("--rebuild", action="store_true",
                   help="with --build-models: train every endpoint again, including those "
                        "whose finished model still matches its card")
    a.add_argument("--archive", default="", help="a local copy of the TDC ADMET group zip")
    a.add_argument("--cache", default="", help="where models live (default: data lake/admet)")
    a.add_argument("--endpoint", action="append", default=[], help="only these; repeatable")
    a.add_argument("--verify", default="", help="re-check a finished run directory and exit")


def dispatch(a: argparse.Namespace) -> int | None:
    if a.cmd != "admet":
        return None
    from .chem import ChemError
    from .pipeline import AdmetConfig, default_cache, run_admet, verify_run

    cache = Path(a.cache) if a.cache else default_cache()
    if a.verify:
        ok, problems = verify_run(a.verify)
        print("verified: every output matches run.json" if ok
              else "NOT VERIFIED:\n" + "\n".join(f"  - {p}" for p in problems))
        return 0 if ok else 1
    if a.build_models:
        from .models import build_models
        try:
            ms = build_models(cache, archive=a.archive or None, endpoints=a.endpoint or None,
                              rebuild=a.rebuild)
        except (ChemError, OSError) as exc:
            print(f"refused: {exc}", file=sys.stderr)
            return 1
        print(f"{len(ms.cards)} models in {ms.directory}")
        if not a.molecules:
            return 0
    if not (a.molecules and a.out):
        print("molecules and --out are required (or --build-models, --verify)", file=sys.stderr)
        return 2
    try:
        run = run_admet(a.molecules, AdmetConfig(cache_dir=str(cache),
                                                 endpoints=tuple(a.endpoint)), a.out)
    except ChemError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 1
    print(f"{len(run.molecules)} molecules; {len(run.cards)} endpoints predicted")
    for w in run.warnings:
        print(f"warning: {w}")
    print(f"report: {Path(a.out) / 'report.html'}")
    return 0
