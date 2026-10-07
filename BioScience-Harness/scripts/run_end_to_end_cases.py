#!/usr/bin/env python
"""Run the three end-to-end cases and write each one's report (Markdown and JSON).

    python scripts/run_end_to_end_cases.py --out RUN/cases
    python scripts/run_end_to_end_cases.py --out RUN/cases --de-backend pydeseq2
    python scripts/run_end_to_end_cases.py --out RUN/cases --only literature

Each case needs the optional implementations it runs: the `analysis` extra (PyDESeq2,
GSEApy), `literature` (paper-qa), and `docking` and `admet` (Vina, Meeko, RDKit, gemmi).
A step whose implementation is missing is reported UNAVAILABLE and the case stops there;
nothing else runs in its place. docs/end-to-end-cases.md describes the cases.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import _bootstrap

_bootstrap.bootstrap()

CASES = ("rnaseq", "literature", "compound")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", required=True)
    ap.add_argument("--only", choices=CASES, action="append",
                    help="run only this case (repeatable)")
    ap.add_argument("--de-backend", default="builtin", choices=("builtin", "pydeseq2"))
    ap.add_argument("--admet-models", metavar="DIR",
                    help="ADMET models built by `bioagent admet --build-models` (the "
                         "directory holding models/); without it the compound case "
                         "reports rules and alerts only")
    args = ap.parse_args(argv)
    out = Path(args.out)
    unexpected = 0
    for name in args.only or CASES:
        work = out / name
        if name == "rnaseq":
            from bioagent.cases.rnaseq_gsea import run_case
            report = run_case(work, de_backend=args.de_backend)
        elif name == "literature":
            from bioagent.cases.literature_claims import run_case
            report = run_case(work)
        else:
            from bioagent.cases.compound_hypothesis import run_case
            report = run_case(work, admet_models=args.admet_models)
        work.mkdir(parents=True, exist_ok=True)
        (work / "report.md").write_text(report.markdown(), encoding="utf-8")
        (work / "report.json").write_text(report.to_json() + "\n", encoding="utf-8")
        steps = ", ".join(f"{s.name}: {s.status.value}" for s in report.steps)
        verdicts = sum(c.as_expected for c in report.claims)
        print(f"{name}: {steps}; {verdicts}/{len(report.claims)} claims as expected "
              f"-> {work / 'report.md'}")
        unexpected += len(report.claims) - verdicts
    return 1 if unexpected else 0


if __name__ == "__main__":
    raise SystemExit(main())
