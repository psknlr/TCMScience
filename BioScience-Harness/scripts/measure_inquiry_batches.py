#!/usr/bin/env python
"""Measure the inquiry's batches in the scaffolds' noisy worlds: the table in docs/inquiry.md.

    python scripts/measure_inquiry_batches.py        # prints the table, in Markdown

The worlds are the calibration worlds of ``tests/test_rivals.py``: for every rival of every
scaffold, 100 worlds whose outcomes are drawn from that rival's own sealed predictions. Each
batch size meets the same worlds (the seed is ``scaffold:rival``, as in the calibration),
so the row for one analysis per round is the calibration itself. A round runs the engine's
batch (``Inquiry.batch``) and records every outcome in it; the inquiry ends when the batch
is empty, and its conclusion is counted.
"""

from __future__ import annotations

import argparse
import random
import sys

import _bootstrap

_bootstrap.bootstrap()
sys.path.insert(0, str(_bootstrap.ROOT / "tests"))

from bioagent.research import rivals  # noqa: E402
from test_rivals import RIVALS, VALUES  # noqa: E402

WORLDS = 100


def measure(size: int) -> dict[str, float]:
    rounds = cost = true = wrong = worlds = 0
    for scaffold_id, truth in RIVALS:
        rows = {t.id: t.predictions[truth] for t in rivals.scaffold(scaffold_id).tests}
        rng = random.Random(f"{scaffold_id}:{truth}")
        for _ in range(WORLDS):
            inquiry = rivals.open_scaffold(scaffold_id, VALUES[scaffold_id])
            while batch := inquiry.batch(size):
                rounds += 1
                for option in batch:
                    row = rows[option.analysis_id]
                    inquiry.observe(option.analysis_id,
                                    rng.choices(list(row), weights=list(row.values()))[0])
            c = inquiry.conclude()
            worlds += 1
            cost += inquiry.spent
            true += c.accepted and c.leader == truth
            wrong += c.accepted and c.leader != truth
    return {"size": size, "worlds": worlds, "rounds": rounds / worlds,
            "cost": cost / worlds, "true": true, "wrong": wrong}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--sizes", type=int, nargs="+", default=[1, 2, 3, 4])
    args = ap.parse_args(argv)
    print("| Analyses per round | Rounds (mean) | Cost (mean) | True rival accepted | "
          "Wrong rival accepted |")
    print("| --- | ---: | ---: | ---: | ---: |")
    for size in args.sizes:
        m = measure(size)
        label = "1, the engine's next step" if size == 1 else str(size)
        print(f"| {label} | {m['rounds']:.2f} | {m['cost']:.2f} | {m['true']:,} | "
              f"{m['wrong']:,} |", flush=True)
    print(f"\n{len(RIVALS) * WORLDS:,} worlds per row ({WORLDS} per rival, "
          f"{len(RIVALS)} rivals)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
