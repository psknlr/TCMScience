#!/usr/bin/env python
"""Fault-injection benchmark: a naive pipeline against the contract-gated one.

    python scripts/eval_design_faults.py --reps 50 --out docs/studies

Every task is a simulated single-cell study with known truth (``studies.faults``). The
report gives, per pipeline: false-positive rate on clean null studies, power on clean
positive studies, false alarms on clean studies (null, positive and paired), and, per
injected defect, how often it was detected by the check meant to catch it and how often
the pipeline still made a (spurious) claim.

Other pipelines, such as an LLM agent given the same files, plug in as a callable
``study -> {"claim": bool, "flags": [...]}``; none is included here because its result
would depend on a model run that this script cannot reproduce.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bioagent.studies.faults import (EXPECTED_CHECK, FAULTS, gated_pipeline,  # noqa: E402
                                     naive_pipeline, run_benchmark)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reps", type=int, default=50)
    ap.add_argument("--seed", type=int, default=20261002)
    ap.add_argument("--out", type=Path, default=ROOT.parent / "docs" / "studies")
    a = ap.parse_args()
    r = run_benchmark({"naive": naive_pipeline, "gated": gated_pipeline}, n_rep=a.reps,
                      seed=a.seed)
    a.out.mkdir(parents=True, exist_ok=True)
    (a.out / "design_faults.json").write_text(json.dumps(r, indent=2) + "\n")
    p = r["pipelines"]
    lines = [f"Fault-injection benchmark, {a.reps} replicates per cell, seed {a.seed}.", "",
             "| | naive | gated |", "| --- | ---: | ---: |"]
    for key, label in (("false_positive_rate", "false positives, clean null"),
                       ("power", "power, clean positive"),
                       ("false_alarm_rate", "false alarms, clean studies"),
                       ("detection_rate", "defects detected (all faults)"),
                       ("spurious_claim_rate_under_faults", "claims made on faulted null data")):
        lines.append(f"| {label} | {p['naive'][key]:.2f} | {p['gated'][key]:.2f} |")
    lines += ["", "| fault | check | naive: detected / claimed | gated: detected / claimed |",
              "| --- | --- | ---: | ---: |"]
    for f in FAULTS:
        n, g = p["naive"]["faults"][f], p["gated"]["faults"][f]
        lines.append(f"| {f} | {EXPECTED_CHECK[f]} | {n['detected']}/{n['spurious_claims']} "
                     f"of {n['runs']} | {g['detected']}/{g['spurious_claims']} of {g['runs']} |")
    (a.out / "design_faults.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
