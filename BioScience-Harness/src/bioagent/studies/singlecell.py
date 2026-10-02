"""Cell composition against cell state, judged per patient.

A bulk signal in disease tissue (say, higher expression of an epithelial gene) can mean two
different things:

* **composition**: the tissue holds a different mix of cells (fewer epithelial cells,
  more infiltrating immune cells);
* **state**: the same kind of cell behaves differently.

The two have different consequences for a target or a mechanism. Here every test is at
the level of the independent unit, the subject: cells are aggregated per subject × cell
type (pseudobulk) before anything is compared, so ten thousand cells from six patients are
six observations, not ten thousand.

* ``pseudobulk``: summed counts per sample × cell type, with the number of cells.
* ``composition_test``: per-subject cell-type counts, centred log-ratio, compared between
  arms with a Welch interval and a permutation p value over subjects (sign flips when
  paired), BH-adjusted across cell types. Proportions are relative: a CLR difference says a
  type rose *relative to the others*, not in absolute number.
* ``state_test``: per-subject log-CPM of each gene within one cell type, compared between
  arms, BH-adjusted across genes.
* ``decompose_bulk``: the difference in the per-subject mean expression of a gene split
  exactly into a composition term ``Σ Δp·ē`` and a state term ``Σ p̄·Δe``, with subject
  bootstrap intervals and a verdict: composition, state, both, or unresolved.
"""

from __future__ import annotations

import math
import warnings
from collections import defaultdict
from dataclasses import dataclass
from typing import Sequence

import numpy as np

from .contract import CohortManifest, ContrastSpec
from .stats import bh, t_ppf, t_sf, welch_difference

__all__ = ["Pseudobulk", "pseudobulk", "subject_levels", "composition_test", "state_test",
           "decompose_bulk", "clr"]


@dataclass(frozen=True)
class Pseudobulk:
    keys: tuple[tuple[str, str], ...]     # (sample, cell type)
    counts: np.ndarray                    # groups × genes
    n_cells: np.ndarray
    genes: tuple[str, ...]

    def of_type(self, cell_type: str) -> tuple[list[str], np.ndarray, np.ndarray]:
        rows = [i for i, (_, t) in enumerate(self.keys) if t == cell_type]
        return [self.keys[i][0] for i in rows], self.counts[rows], self.n_cells[rows]


def pseudobulk(counts: np.ndarray, cell_sample: Sequence[str], cell_type: Sequence[str],
               genes: Sequence[str], *, min_cells: int = 10) -> Pseudobulk:
    """Sum raw counts per sample × cell type; groups with fewer than ``min_cells`` drop."""
    x = np.asarray(counts)
    if x.shape[0] != len(cell_sample) or len(cell_sample) != len(cell_type):
        raise ValueError("one sample and one cell type per cell row")
    keys = list(zip(map(str, cell_sample), map(str, cell_type)))
    uniq = sorted(set(keys))
    index = {k: i for i, k in enumerate(uniq)}
    g = np.fromiter((index[k] for k in keys), int, len(keys))
    order = np.argsort(g, kind="stable")
    starts = np.r_[0, np.flatnonzero(np.diff(g[order])) + 1]
    sums = np.add.reduceat(x[order], starts, axis=0)
    n = np.bincount(g, minlength=len(uniq))
    keep = n >= min_cells
    return Pseudobulk(tuple(k for k, ok in zip(uniq, keep) if ok), sums[keep], n[keep],
                      tuple(genes))


def clr(counts: np.ndarray, pseudocount: float = 0.5) -> np.ndarray:
    lx = np.log(np.asarray(counts, float) + pseudocount)
    return lx - lx.mean(axis=-1, keepdims=True)


def subject_levels(manifest: CohortManifest, contrast: ContrastSpec) -> dict[str, tuple[str, str]]:
    """sample id -> (subject, level) for samples in the contrast."""
    out = {}
    for lvl, ss in contrast.select(manifest).items():
        for s in ss:
            out[s.sample_id] = (s.get(contrast.unit), lvl)
    return out


def _perm_p(a: np.ndarray, b: np.ndarray, n_perm: int, rng) -> float:
    """Two-sided permutation p for a difference in means, over units."""
    pooled = np.r_[a, b]
    obs = abs(a.mean() - b.mean())
    na = len(a)
    hits = 0
    for _ in range(n_perm):
        p = rng.permutation(pooled)
        hits += abs(p[:na].mean() - p[na:].mean()) >= obs - 1e-12
    return (hits + 1) / (n_perm + 1)


def _signflip_p(d: np.ndarray, n_perm: int, rng) -> float:
    obs = abs(d.mean())
    signs = rng.choice([-1.0, 1.0], size=(n_perm, len(d)))
    return float((np.sum(np.abs((signs * d).mean(1)) >= obs - 1e-12) + 1) / (n_perm + 1))


def _paired_t(d: np.ndarray, level: float) -> dict:
    n = len(d)
    est = float(d.mean())
    se = float(d.std(ddof=1) / math.sqrt(n))
    if se == 0:
        return {"estimate": est, "ci": [est, est], "p_value": 0.0 if est else 1.0, "n": n}
    q = t_ppf(1 - (1 - level) / 2, n - 1)
    return {"estimate": est, "ci": [est - q * se, est + q * se],
            "p_value": 2 * t_sf(abs(est) / se, n - 1), "n": n}


def _subject_table(values: dict[tuple[str, str], np.ndarray]) -> dict[str, dict[str, np.ndarray]]:
    """(subject, level) -> vector  into  level -> subject -> vector."""
    out: dict[str, dict[str, np.ndarray]] = defaultdict(dict)
    for (subj, lvl), v in values.items():
        out[lvl][subj] = v
    return out


def composition_test(cell_sample: Sequence[str], cell_type: Sequence[str],
                     manifest: CohortManifest, contrast: ContrastSpec, *,
                     pseudocount: float = 0.5, n_perm: int = 5000, seed: int = 0,
                     level: float = 0.95) -> dict:
    lv = subject_levels(manifest, contrast)
    types = sorted(set(map(str, cell_type)))
    tidx = {t: i for i, t in enumerate(types)}
    acc: dict[tuple[str, str], np.ndarray] = defaultdict(lambda: np.zeros(len(types)))
    for smp, t in zip(map(str, cell_sample), map(str, cell_type)):
        if smp in lv:
            acc[lv[smp]][tidx[t]] += 1      # a subject's samples at one level are pooled
    table = _subject_table(dict(acc))
    case, ctrl = table.get(contrast.case, {}), table.get(contrast.control, {})
    rng = np.random.default_rng(seed)
    rows = []
    if contrast.paired:
        subj = sorted(set(case) & set(ctrl))
        if len(subj) < 3:
            raise ValueError(f"{len(subj)} complete pairs; a paired composition test needs 3")
        ca = clr(np.array([case[s] for s in subj]), pseudocount)
        cb = clr(np.array([ctrl[s] for s in subj]), pseudocount)
        props_a = np.array([case[s] / case[s].sum() for s in subj])
        props_b = np.array([ctrl[s] / ctrl[s].sum() for s in subj])
        for i, t in enumerate(types):
            d = ca[:, i] - cb[:, i]
            r = _paired_t(d, level)
            rows.append({"cell_type": t, "clr_difference": r["estimate"], "ci": r["ci"],
                         "p_t": r["p_value"], "p_perm": _signflip_p(d, n_perm, rng),
                         "mean_prop_case": float(props_a[:, i].mean()),
                         "mean_prop_control": float(props_b[:, i].mean())})
        n_units = {"pairs": len(subj)}
    else:
        sa, sb = sorted(case), sorted(ctrl)
        if min(len(sa), len(sb)) < 2:
            raise ValueError(f"subjects per arm {len(sa)}/{len(sb)}; at least two each")
        ca = clr(np.array([case[s] for s in sa]), pseudocount)
        cb = clr(np.array([ctrl[s] for s in sb]), pseudocount)
        props_a = np.array([case[s] / case[s].sum() for s in sa])
        props_b = np.array([ctrl[s] / ctrl[s].sum() for s in sb])
        for i, t in enumerate(types):
            try:
                w = welch_difference(ca[:, i], cb[:, i], level=level)
                est, ci, p = w.estimate, [w.ci_low, w.ci_high], w.p_value
            except ValueError:
                est, ci, p = float(ca[:, i].mean() - cb[:, i].mean()), [math.nan, math.nan], 1.0
            rows.append({"cell_type": t, "clr_difference": est, "ci": ci, "p_t": p,
                         "p_perm": _perm_p(ca[:, i], cb[:, i], n_perm, rng),
                         "mean_prop_case": float(props_a[:, i].mean()),
                         "mean_prop_control": float(props_b[:, i].mean())})
        n_units = {contrast.case: len(sa), contrast.control: len(sb)}
    q = bh([r["p_perm"] for r in rows])
    for r, qq in zip(rows, q):
        r["q_perm"] = qq
    return {"contrast": contrast.id, "unit": contrast.unit, "paired": contrast.paired,
            "units": n_units, "cell_types": rows,
            "note": "CLR differences are relative to the other cell types; a rise in one "
                    "type lowers the share of all others"}


def _logcpm(counts: np.ndarray, prior: float = 1.0) -> np.ndarray:
    lib = counts.sum(1, keepdims=True)
    return np.log2((counts + prior) / (lib + 2 * prior) * 1e6)


def state_test(pb: Pseudobulk, manifest: CohortManifest, contrast: ContrastSpec,
               cell_type: str, *, genes: Sequence[str] | None = None, min_cells: int = 20,
               level: float = 0.95) -> dict:
    """Per-gene differences in log-CPM within one cell type, one value per subject."""
    lv = subject_levels(manifest, contrast)
    samples, counts, ncell = pb.of_type(cell_type)
    gi = [pb.genes.index(g) for g in genes] if genes else list(range(len(pb.genes)))
    agg: dict[tuple[str, str], np.ndarray] = defaultdict(lambda: 0)
    nc: dict[tuple[str, str], int] = defaultdict(int)
    for s, c, n in zip(samples, counts, ncell):
        if s in lv:
            agg[lv[s]] = agg[lv[s]] + c
            nc[lv[s]] += int(n)
    agg = {k: v for k, v in agg.items() if nc[k] >= min_cells}
    table = _subject_table(agg)
    case, ctrl = table.get(contrast.case, {}), table.get(contrast.control, {})
    rows = []
    if contrast.paired:
        subj = sorted(set(case) & set(ctrl))
        if len(subj) < 3:
            raise ValueError(f"{len(subj)} complete pairs in {cell_type}; need 3")
        la = _logcpm(np.array([case[s] for s in subj], float))[:, gi]
        lb = _logcpm(np.array([ctrl[s] for s in subj], float))[:, gi]
        for j, g in enumerate(gi):
            r = _paired_t(la[:, j] - lb[:, j], level)
            rows.append({"gene": pb.genes[g], "log2_difference": r["estimate"],
                         "ci": r["ci"], "p_value": r["p_value"]})
        units = {"pairs": len(subj)}
    else:
        sa, sb = sorted(case), sorted(ctrl)
        if min(len(sa), len(sb)) < 2:
            raise ValueError(f"{cell_type}: subjects with ≥{min_cells} cells per arm "
                             f"{len(sa)}/{len(sb)}; at least two each")
        la = _logcpm(np.array([case[s] for s in sa], float))[:, gi]
        lb = _logcpm(np.array([ctrl[s] for s in sb], float))[:, gi]
        for j, g in enumerate(gi):
            try:
                w = welch_difference(la[:, j], lb[:, j], level=level)
                rows.append({"gene": pb.genes[g], "log2_difference": w.estimate,
                             "ci": [w.ci_low, w.ci_high], "p_value": w.p_value})
            except ValueError:
                rows.append({"gene": pb.genes[g], "log2_difference": 0.0,
                             "ci": [math.nan, math.nan], "p_value": 1.0})
        units = {contrast.case: len(sa), contrast.control: len(sb)}
    for r, q in zip(rows, bh([r["p_value"] for r in rows])):
        r["q_value"] = q
    return {"contrast": contrast.id, "cell_type": cell_type, "units": units,
            "min_cells": min_cells, "genes": rows}


def decompose_bulk(expression: np.ndarray, cell_sample: Sequence[str],
                   cell_type: Sequence[str], manifest: CohortManifest, contrast: ContrastSpec,
                   *, min_cells: int = 5, n_boot: int = 2000, seed: int = 0,
                   level: float = 0.95) -> dict:
    """Split the case − control difference of a gene's per-subject mean into composition
    and state.

    ``expression``: one normalised value per cell (e.g. log1p counts per 10k) for one
    gene. Per subject, ``p_k`` is the fraction of cells of type ``k`` and ``e_k`` their mean
    expression; the subject's tissue-level mean is ``Σ_k p_k e_k``. With arm means
    ``p̄``, ``ē``, the difference ``Σ p̄ᵃē^a − Σ p̄ᵇēᵇ`` is exactly
    ``Σ_k Δp_k·ē_k`` (composition) ``+ Σ_k p̄_k·Δe_k`` (state), with ``ē``, ``p̄`` averaged
    over the two arms. Intervals resample subjects
    within each arm. Unpaired designs only; for paired designs use the per-subject
    differences with ``state_test`` and ``composition_test``.
    """
    if contrast.paired:
        raise ValueError("decompose_bulk treats arms as independent; the contrast is paired")
    lv = subject_levels(manifest, contrast)
    types = sorted(set(map(str, cell_type)))
    tidx = {t: i for i, t in enumerate(types)}
    k = len(types)
    sums: dict[tuple[str, str], np.ndarray] = defaultdict(lambda: np.zeros(k))
    cnt: dict[tuple[str, str], np.ndarray] = defaultdict(lambda: np.zeros(k))
    x = np.asarray(expression, float)
    for v, smp, t in zip(x, map(str, cell_sample), map(str, cell_type)):
        if smp in lv:
            sums[lv[smp]][tidx[t]] += v
            cnt[lv[smp]][tidx[t]] += 1
    arms = {}
    for lvl in (contrast.case, contrast.control):
        keys = sorted(key for key in cnt if key[1] == lvl)
        P = np.array([cnt[key] / cnt[key].sum() for key in keys])
        with np.errstate(invalid="ignore", divide="ignore"):
            E = np.array([np.where(cnt[key] >= min_cells, sums[key] / cnt[key], np.nan)
                          for key in keys])
        arms[lvl] = (P, E, [key[0] for key in keys])
    if min(len(arms[contrast.case][2]), len(arms[contrast.control][2])) < 3:
        raise ValueError("at least three subjects per arm are needed to decompose")

    def terms(Pa, Ea, Pb, Eb):
        pa, pb_ = Pa.mean(0), Pb.mean(0)
        with warnings.catch_warnings():
            # a cell type too sparse in every subject of an arm has no mean: excluded below
            warnings.simplefilter("ignore", RuntimeWarning)
            ea, eb = np.nanmean(Ea, 0), np.nanmean(Eb, 0)
        ok = ~(np.isnan(ea) | np.isnan(eb))
        ebar = np.where(ok, (ea + eb) / 2, 0.0)
        comp = (pa - pb_) * ebar
        state = np.where(ok, (pa + pb_) / 2 * (ea - eb), 0.0)
        return comp, state, ok

    Pa, Ea, sa = arms[contrast.case]
    Pb, Eb, sb = arms[contrast.control]
    with np.errstate(invalid="ignore"):
        comp, state, ok = terms(Pa, Ea, Pb, Eb)
        rng = np.random.default_rng(seed)
        bc, bs = [], []
        for _ in range(n_boot):
            ia = rng.integers(0, len(sa), len(sa))
            ib = rng.integers(0, len(sb), len(sb))
            c, s, _ = terms(Pa[ia], Ea[ia], Pb[ib], Eb[ib])
            bc.append(c.sum())
            bs.append(s.sum())
    lo, hi = (1 - level) / 2 * 100, (1 + level) / 2 * 100
    ci_c = [float(np.nanpercentile(bc, lo)), float(np.nanpercentile(bc, hi))]
    ci_s = [float(np.nanpercentile(bs, lo)), float(np.nanpercentile(bs, hi))]
    sig_c = ci_c[0] > 0 or ci_c[1] < 0
    sig_s = ci_s[0] > 0 or ci_s[1] < 0
    verdict = ("both" if sig_c and sig_s else "composition" if sig_c else
               "state" if sig_s else "unresolved")
    observed = float(np.nansum(Pa * np.nan_to_num(Ea), 1).mean()
                     - np.nansum(Pb * np.nan_to_num(Eb), 1).mean())
    return {"contrast": contrast.id, "subjects": {contrast.case: len(sa),
                                                  contrast.control: len(sb)},
            "difference": float(comp.sum() + state.sum()),
            "difference_observed": observed,
            "composition": {"estimate": float(comp.sum()), "ci": ci_c,
                            "by_cell_type": dict(zip(types, map(float, comp)))},
            "state": {"estimate": float(state.sum()), "ci": ci_s,
                      "by_cell_type": dict(zip(types, map(float, state)))},
            "within_arm_covariance": observed - float(comp.sum() + state.sum()),
            "excluded_cell_types": [t for t, good in zip(types, ok) if not good],
            "verdict": verdict, "level": level,
            "note": "bootstrap over subjects. The terms decompose the difference of "
                    "arm-mean composition times arm-mean expression exactly; the observed "
                    "difference of subject means also carries the within-arm covariance of "
                    "proportion and expression, reported separately"}
