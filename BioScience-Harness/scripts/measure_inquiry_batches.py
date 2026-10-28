#!/usr/bin/env python
"""Measure the inquiry's batches in the scaffolds' noisy worlds: the table in docs/inquiry.md.

    python scripts/measure_inquiry_batches.py                 # prints the table, in Markdown
    python scripts/measure_inquiry_batches.py --sensitivity   # and the sensitivity figures below it

The worlds are the calibration worlds of ``tests/test_rivals.py``: for every rival of every
scaffold, 100 worlds whose outcomes are drawn from that rival's own sealed predictions. Each
batch size starts from the same seed (``scaffold:rival``, as in the calibration), so the row
for one analysis per round is the calibration itself. The outcomes are drawn as analyses
run, so a larger batch, which runs them in another order, draws other ones: the batch
sizes share seeds, not worlds. A round runs the engine's
batch (``Inquiry.batch``) and records every outcome in it; the inquiry ends when the batch
is empty, and its conclusion is counted.

With ``--sensitivity``, every conclusion accepted in the calibration (one analysis per
round) is replayed by ``Inquiry.sensitivity`` at each temper: how often its leader is still
the leader, and by how much the leader's posterior falls on average.
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


def sensitivity(tempers: tuple[float, ...] = (0.1, 0.2, 0.3)) -> dict[str, object]:
    """The calibration's accepted conclusions, replayed with tempered predictions."""
    accepted = 0
    held = dict.fromkeys(tempers, 0)
    fall = dict.fromkeys(tempers, 0.0)
    for scaffold_id, truth in RIVALS:
        rows = {t.id: t.predictions[truth] for t in rivals.scaffold(scaffold_id).tests}
        rng = random.Random(f"{scaffold_id}:{truth}")
        for _ in range(WORLDS):
            inquiry = rivals.open_scaffold(scaffold_id, VALUES[scaffold_id])
            while batch := inquiry.batch(1):
                for option in batch:
                    row = rows[option.analysis_id]
                    inquiry.observe(option.analysis_id,
                                    rng.choices(list(row), weights=list(row.values()))[0])
            c = inquiry.conclude()
            if not c.accepted:
                continue
            accepted += 1
            for result in inquiry.sensitivity(tempers):
                t = result["temper"]
                held[t] += result["leader"] == c.leader
                fall[t] += c.leader_posterior - result["belief"].get(c.leader, 0.0)
    return {"accepted": accepted, "held": held,
            "fall": {t: (fall[t] / accepted if accepted else 0.0) for t in tempers}}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--sizes", type=int, nargs="+", default=[1, 2, 3, 4])
    ap.add_argument("--sensitivity", action="store_true",
                    help="also replay the calibration's accepted conclusions with tempered predictions")
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
    if args.sensitivity:
        s = sensitivity()
        print(f"\nSensitivity of the {s['accepted']:,} conclusions accepted in the calibration:")
        for t, n in s["held"].items():
            print(f"  t = {t}: the leader held in {n:,}; its posterior fell by "
                  f"{s['fall'][t]:.3f} on average")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
