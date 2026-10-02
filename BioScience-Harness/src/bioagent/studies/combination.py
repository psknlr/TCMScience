"""Combination response against a reference model chosen in advance.

"Synergy" is three questions, answered separately:

1. **Is the combination effective?** Its observed effect, as measured.
2. **Does it exceed what the single agents predict?** The excess over a reference model:
   Bliss independence (``E_A + E_B − E_A·E_B``), highest single agent (HSA), or Loewe
   additivity (from Hill fits of each single agent). The primary model is fixed before
   the analysis; the others are reported as sensitivity, never picked afterwards. The
   interval on the excess is a t interval: the variance of the observed mean plus the
   propagated variance of the reference (delta method for Bliss and HSA; refitting both
   Hill curves to replicate-resampled single-agent data for Loewe), with conservative
   degrees of freedom (the smallest group's replicates − 1). A percentile bootstrap over
   three replicates flagged "exceeds" several times too often.
3. **Is the excess selective?** When a cytotoxicity readout of the same wells is given,
   cells where the combination is also broadly cytotoxic are flagged, and the excess on
   cytotoxicity is reported beside the excess on the target endpoint.

Effects are fractions in [0, 1] (inhibition, kill). A combination study needs the control,
each agent alone at the doses combined, and the combinations, in the same experiment; with
single agents only, nothing here is computed, because exceeding a reference requires the
combination to have been measured. Pharmacokinetic enhancement (one agent raising the
other's exposure) is a different claim (``studies.exposure``) and does not show up here.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

import numpy as np

from .stats import t_ppf

__all__ = ["Cell", "analyse_combination", "fit_hill", "REFERENCE_MODELS"]

REFERENCE_MODELS = ("bliss", "hsa", "loewe")


@dataclass(frozen=True)
class Cell:
    dose_a: float
    dose_b: float
    effect: tuple[float, ...]            # replicates, fraction in [0, 1]
    cytotoxicity: tuple[float, ...] = ()


def fit_hill(doses: Sequence[float], effects: Sequence[float]) -> dict:
    """Least-squares Hill fit E = Emax·d^h / (EC50^h + d^h), no scipy.

    A coarse grid over (Emax, h, log EC50), then two finer grids around the best point,
    all vectorised.
    """
    d = np.asarray(doses, float)
    e = np.asarray(effects, float)
    mask = d > 0
    d, e = d[mask], e[mask]
    if len(set(d.tolist())) < 3:
        raise ValueError("a Hill fit needs a single agent at three or more doses")
    lo_ec, hi_ec = math.log(d.min()) - 2, math.log(d.max()) + 2
    ranges = [(max(0.05, float(e.max()) * 0.8), 1.0), (0.3, 4.0), (lo_ec, hi_ec)]
    best = None
    for _ in range(3):
        em = np.linspace(*ranges[0], 24)
        hh = np.linspace(*ranges[1], 24)
        lec = np.linspace(*ranges[2], 40)
        EM, HH, LEC = np.meshgrid(em, hh, lec, indexing="ij")
        pred = EM[..., None] * d ** HH[..., None] / (
            np.exp(LEC)[..., None] ** HH[..., None] + d ** HH[..., None])
        sse = ((pred - e) ** 2).sum(-1)
        k = np.unravel_index(np.argmin(sse), sse.shape)
        best = (float(sse[k]), float(EM[k]), float(HH[k]), float(LEC[k]))
        steps = [(r[1] - r[0]) / n for r, n in zip(ranges, (24, 24, 40))]
        ranges = [(max(0.01, best[1] - 2 * steps[0]), min(1.0, best[1] + 2 * steps[0])),
                  (max(0.05, best[2] - 2 * steps[1]), best[2] + 2 * steps[1]),
                  (best[3] - 2 * steps[2], best[3] + 2 * steps[2])]
    sse, emax, h, lec = best
    return {"emax": emax, "hill": h, "ec50": math.exp(lec), "sse": sse}


def _dose_for(effect: float, fit: dict) -> float:
    """The single-agent dose giving ``effect`` (inf when the agent cannot reach it)."""
    if effect <= 0:
        return 0.0
    if effect >= fit["emax"]:
        return math.inf
    return fit["ec50"] * (effect / (fit["emax"] - effect)) ** (1 / fit["hill"])


def _loewe(da: float, db: float, fa: dict, fb: dict) -> float:
    lo, hi = 0.0, min(0.999999, max(fa["emax"], fb["emax"]))
    for _ in range(80):
        mid = (lo + hi) / 2
        Da, Db = _dose_for(mid, fa), _dose_for(mid, fb)
        index = (da / Da if Da > 0 else math.inf if da > 0 else 0.0) + \
                (db / Db if Db > 0 else math.inf if db > 0 else 0.0)
        if index > 1:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def analyse_combination(cells: Sequence[Cell], *, primary: str, seed: int = 0,
                        boot: int = 200, cytotoxic_above: float = 0.5,
                        level: float = 0.95) -> dict:
    if primary not in REFERENCE_MODELS:
        raise ValueError(f"primary reference model {primary!r} is not one of {REFERENCE_MODELS}")
    single_a = {c.dose_a: np.asarray(c.effect, float) for c in cells
                if c.dose_b == 0 and c.dose_a > 0}
    single_b = {c.dose_b: np.asarray(c.effect, float) for c in cells
                if c.dose_a == 0 and c.dose_b > 0}
    combos = [c for c in cells if c.dose_a > 0 and c.dose_b > 0]
    if not combos:
        raise ValueError("no combination was measured: an excess over a reference model "
                         "cannot be computed from single agents alone")
    missing = sorted({(c.dose_a, c.dose_b) for c in combos
                      if c.dose_a not in single_a or c.dose_b not in single_b})
    if missing:
        raise ValueError(f"combinations {missing} lack a single-agent measurement at the "
                         "same dose in the same experiment")
    fits = {}
    for name, single in (("a", single_a), ("b", single_b)):
        try:
            fits[name] = fit_hill(list(single), [v.mean() for v in single.values()])
        except ValueError as exc:
            fits[name] = {"error": str(exc)}
    rng = np.random.default_rng(seed)
    loewe_ok = "error" not in fits["a"] and "error" not in fits["b"]
    # the Loewe reference depends on the fitted curves: their uncertainty comes from
    # refitting both curves to replicate-resampled single-agent data
    loewe_refits = []
    if loewe_ok and primary == "loewe":
        for _ in range(boot):
            ra = {d: rng.choice(v, len(v)).mean() for d, v in single_a.items()}
            rb = {d: rng.choice(v, len(v)).mean() for d, v in single_b.items()}
            try:
                loewe_refits.append((fit_hill(list(ra), list(ra.values())),
                                     fit_hill(list(rb), list(rb.values()))))
            except ValueError:
                continue
    rows = []
    for c in combos:
        obs = np.asarray(c.effect, float)
        a, b = single_a[c.dose_a], single_b[c.dose_b]
        ma, mb = a.mean(), b.mean()
        expected = {"bliss": ma + mb - ma * mb, "hsa": max(ma, mb)}
        if loewe_ok:
            expected["loewe"] = _loewe(c.dose_a, c.dose_b, fits["a"], fits["b"])
        if primary not in expected:
            raise ValueError(f"the primary model {primary!r} cannot be computed: "
                             f"{fits['a'].get('error') or fits['b'].get('error')}")
        if min(len(obs), len(a), len(b)) < 2:
            raise ValueError("an interval needs at least two replicates of the combination "
                             "and of each single agent at that dose")
        var_obs = obs.var(ddof=1) / len(obs)
        va, vb = a.var(ddof=1) / len(a), b.var(ddof=1) / len(b)
        if primary == "bliss":                        # delta method
            var_ref = (1 - mb) ** 2 * va + (1 - ma) ** 2 * vb
        elif primary == "hsa":
            var_ref = va if ma >= mb else vb
        else:
            refs = [_loewe(c.dose_a, c.dose_b, fa, fb) for fa, fb in loewe_refits]
            var_ref = float(np.var(refs, ddof=1)) if len(refs) > 1 else math.inf
        se = math.sqrt(var_obs + var_ref)
        # conservative degrees of freedom: the smallest group drives them
        df = max(1, min(len(obs), len(a), len(b)) - 1)
        q = t_ppf(1 - (1 - level) / 2, df)
        est = float(obs.mean() - expected[primary])
        lo, hi = est - q * se, est + q * se
        cyto = float(np.mean(c.cytotoxicity)) if c.cytotoxicity else None
        rows.append({
            "dose_a": c.dose_a, "dose_b": c.dose_b, "observed": float(obs.mean()),
            "expected": {k: float(v) for k, v in expected.items()},
            "excess": {k: float(obs.mean() - v) for k, v in expected.items()},
            "primary_excess_ci": [float(lo), float(hi)], "df": df,
            "exceeds_primary": bool(lo > 0), "below_primary": bool(hi < 0),
            "cytotoxicity": cyto,
            "near_general_cytotoxicity": cyto is not None and cyto >= cytotoxic_above})
    exceed = [r for r in rows if r["exceeds_primary"]]
    selective = [r for r in exceed if not r["near_general_cytotoxicity"]]
    sensitivity = {m: sum(1 for r in rows if m in r["excess"] and r["excess"][m] > 0)
                   for m in REFERENCE_MODELS}
    return {
        "primary_model": primary, "cells": rows, "single_agent_fits": fits,
        "effective": {"max_observed": max(r["observed"] for r in rows)},
        "exceeds_reference": {"cells": len(exceed), "of": len(rows),
                              "selective": len(selective)},
        "sensitivity_cells_above_reference": sensitivity,
        "interpretation": (
            "excess over the prespecified reference in this experiment; it is not "
            "pharmacokinetic enhancement, not evidence about the formula's preparation, and "
            "not clinical synergy. Cells flagged near general cytotoxicity do not count as "
            "selective."),
    }
