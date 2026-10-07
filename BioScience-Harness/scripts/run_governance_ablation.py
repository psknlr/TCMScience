#!/usr/bin/env python
"""Governance ablation: which part of the stack catches which scientific error.

    # the committed benchmark: base cases, their mutants, every configuration
    python scripts/run_governance_ablation.py --out benchmarks/ablation

    # CI: recompute and refuse a regression against the committed results
    python scripts/run_governance_ablation.py --check benchmarks/ablation/results.json

    # your own labelled outputs (one JSON case per line, gold = release | refuse)
    python scripts/run_governance_ablation.py --drafts drafts.jsonl --out RUN/ablation

The base cases and mutation operators are ``bioagent.benchmarks.ablation_corpus`` and
``bioagent.benchmarks.ablation.MUTATIONS``; every gate is the runtime's own check. A
regression is the full stack releasing a mutant the committed results show it refusing,
or refusing a base case they show it releasing. Improvements pass and ask for the results
to be regenerated, so the committed numbers stay the ones the code produces.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import _bootstrap

_bootstrap.bootstrap()

from bioagent.benchmarks.ablation import (  # noqa: E402
    load_drafts, render_markdown, run_ablation,
)


def _compact(data: dict) -> dict:
    """The committed form: per case, which gates refused and with what codes."""
    out = dict(data)
    out["cases"] = {cid: {"error_class": c["error_class"], "gold": c["gold"],
                          "language": c["language"],
                          "refused_by": {g: r["codes"] for g, r in c["gates"].items()
                                         if r["refused"]}}
                    for cid, c in data["cases"].items()}
    return out


def _full(data: dict) -> dict:
    return next(r for r in data["configurations"] if r["configuration"] == "full")


def _check(path: Path) -> int:
    committed = json.loads(path.read_text(encoding="utf-8"))
    now = _compact(run_ablation().as_dict())
    problems, notes = [], []
    if set(now["cases"]) != set(committed["cases"]):
        added = sorted(set(now["cases"]) - set(committed["cases"]))
        gone = sorted(set(committed["cases"]) - set(now["cases"]))
        problems.append(f"the case set changed (added {added}, removed {gone}); "
                        "regenerate the results with --out")
    was = {s["id"] for s in committed["survivors"]}
    is_ = {s["id"] for s in now["survivors"]}
    if is_ - was:
        problems.append(f"the full stack now releases {sorted(is_ - was)}")
    if was - is_:
        notes.append(f"the full stack now refuses {sorted(was - is_)}: regenerate the "
                     "results with --out")
    refused_was = {f["id"] for f in committed["false_refusals"]}
    refused_is = {f["id"] for f in now["false_refusals"]}
    if refused_is - refused_was:
        problems.append(f"correct outputs now refused: {sorted(refused_is - refused_was)}")
    if refused_was - refused_is:
        notes.append(f"no longer refused: {sorted(refused_was - refused_is)}: regenerate")
    full = _full(now)
    print(f"full stack: {full['errors_released']}/{full['mutants']} errors released, "
          f"{full['false_refusals']}/{full['base_cases']} correct outputs refused "
          f"(committed: {_full(committed)['errors_released']}, "
          f"{_full(committed)['false_refusals']})")
    for note in notes:
        print(f"note: {note}")
    for problem in problems:
        print(f"REGRESSION: {problem}", file=sys.stderr)
    return 1 if problems else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", default="", help="write results.json and results.md here")
    ap.add_argument("--check", default="", help="committed results.json to compare against")
    ap.add_argument("--drafts", default="", help="labelled outputs to run instead (JSONL)")
    args = ap.parse_args(argv)
    if args.check:
        return _check(Path(args.check))
    if not args.out:
        ap.error("give --out, or --check")
    report = run_ablation(load_drafts(args.drafts) if args.drafts else None)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    data = _compact(report.as_dict())
    (out / "results.json").write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n",
                                      encoding="utf-8")
    text = render_markdown(report)
    (out / "results.md").write_text(text, encoding="utf-8")
    print(text)
    print(f"wrote {out / 'results.json'} and {out / 'results.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
