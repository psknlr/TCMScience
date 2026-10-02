"""Power by simulation, for designs where cells are nested in people.

The simulation draws subjects (between-subject SD) and cells within them (within-subject
SD). The correct test compares subject means; the naive one treats every cell as a
replicate. Two lessons come out of it and are tested:

* more cells per subject barely raise the power of the correct test once the subject
  mean is estimated well, because the between-subject variance does not shrink;
* the naive cell-level test rejects far above its nominal rate when there is no effect,
  and its "power" grows with the number of cells, for the wrong reason.
"""

from __future__ import annotations

import math
from typing import Sequence

import numpy as np

from .stats import t_sf

__all__ = ["simulate_power", "power_grid", "subjects_needed"]

_erfc = np.vectorize(math.erfc)


def simulate_power(n_per_arm: int, cells_per_subject: int, *, effect: float,
                   sd_subject: float, sd_cell: float, n_sim: int = 1000,
                   alpha: float = 0.05, seed: int = 0) -> dict:
    if n_per_arm < 2 or cells_per_subject < 2:
        raise ValueError("at least two subjects per arm and two cells per subject")
    rng = np.random.default_rng(seed)
    n, m = n_per_arm, cells_per_subject
    # subject true means and sampled cell means / within sums of squares
    true = rng.normal(0, sd_subject, (n_sim, 2, n))
    true[:, 0, :] += effect
    cell_means = true + rng.normal(0, sd_cell / math.sqrt(m), (n_sim, 2, n))
    ss_within = sd_cell ** 2 * rng.chisquare(m - 1, (n_sim, 2, n))

    # correct: Welch on subject means
    mean = cell_means.mean(2)
    var = cell_means.var(2, ddof=1) / n
    se = np.sqrt(var.sum(1))
    t = (mean[:, 0] - mean[:, 1]) / se
    df = var.sum(1) ** 2 / (var ** 2 / (n - 1)).sum(1)
    p_subject = np.array([2 * t_sf(abs(a), b) for a, b in zip(t, df)])

    # naive: every cell a replicate (normal approximation; N is large)
    N = n * m
    arm_var = (ss_within.sum(2) + m * ((cell_means - mean[..., None]) ** 2).sum(2)) / (N - 1)
    z = (mean[:, 0] - mean[:, 1]) / np.sqrt(arm_var.sum(1) / N)
    p_cell = _erfc(np.abs(z) / math.sqrt(2))
    return {"n_per_arm": n, "cells_per_subject": m, "effect": effect,
            "sd_subject": sd_subject, "sd_cell": sd_cell, "alpha": alpha, "n_sim": n_sim,
            "subject_level_rejection": float((p_subject < alpha).mean()),
            "cell_level_rejection": float((p_cell < alpha).mean())}


def power_grid(subjects: Sequence[int], cells: Sequence[int], **kw) -> list[dict]:
    return [simulate_power(n, m, **kw) for n in subjects for m in cells]


def subjects_needed(target: float = 0.8, *, cells_per_subject: int = 500,
                    max_subjects: int = 200, **kw) -> int | None:
    """Smallest subjects per arm reaching ``target`` power for the subject-level test."""
    for n in range(2, max_subjects + 1):
        if simulate_power(n, cells_per_subject, **kw)["subject_level_rejection"] >= target:
            return n
    return None
