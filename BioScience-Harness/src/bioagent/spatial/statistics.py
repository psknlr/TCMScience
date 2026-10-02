"""Spatial statistics on a neighbour graph: Moran's I and neighbourhood enrichment.

* ``morans_i``: spatial autocorrelation of each gene. Values are centred within each
  section (sections share no edges, and their means need not agree). Weights are the
  graph's binary adjacency, row-standardised by default (as Squidpy does). The analytic
  z-score uses the normality-assumption variance; p is one-sided for positive
  autocorrelation, and BH-adjusted across the genes tested. An optional permutation p
  shuffles values within each section.
* ``neighbourhood_enrichment``: for each pair of labels, the number of graph edges joining
  them against labels permuted within each section (z-score). Spot labels from expression
  clusters make this a statement about where expression programmes sit, not about cells.

Both describe one tissue section (or several pooled). Neither compares conditions or
patients; that needs one value per patient (``bioagent.studies.spatial``).
"""

from __future__ import annotations

import math

import numpy as np

from ..studies.stats import bh
from .graph import SpatialGraph

__all__ = ["edge_weights", "morans_i", "neighbourhood_enrichment"]


def edge_weights(g: SpatialGraph, transformation: str = "r") -> np.ndarray:
    w = np.ones(len(g.src))
    if transformation == "r":
        deg = g.degree.astype(float)
        w = w / np.where(deg[g.src] > 0, deg[g.src], 1.0)
    elif transformation not in ("b", None, ""):
        raise ValueError("transformation is 'r' (row-standardised) or 'b' (binary)")
    return w


def _norm_sf(z: np.ndarray) -> np.ndarray:
    return 0.5 * np.vectorize(math.erfc)(z / math.sqrt(2))


def _column_blocks(values, block: int):
    """Yield (column slice, dense float64 block) from a dense array or a CSR matrix."""
    from .matrix import CSR
    if isinstance(values, CSR):
        p = values.shape[1]
        for start in range(0, p, block):
            cols = np.arange(start, min(p, start + block))
            yield cols, values.take_cols(cols).to_dense(np.float64)
    else:
        x = np.asarray(values, float)
        if x.ndim == 1:
            x = x[:, None]
        for start in range(0, x.shape[1], block):
            cols = np.arange(start, min(x.shape[1], start + block))
            yield cols, x[:, cols]


def morans_i(values, g: SpatialGraph, *, transformation: str = "r",
             n_perms: int = 0, seed: int = 0, block: int = 128,
             edge_chunk: int = 200_000) -> dict[str, np.ndarray]:
    """Moran's I for each column of ``values`` (nodes × features; dense or ``CSR``).

    Features are processed ``block`` columns at a time and edges ``edge_chunk`` at a
    time, so memory does not grow with spots × features.
    """
    from .matrix import CSR
    if not isinstance(values, CSR):
        values = np.asarray(values, float)
        if values.ndim == 1:
            values = values[:, None]
    n = g.n
    p = values.shape[1]
    if values.shape[0] != n:
        raise ValueError("one row of values per graph node")
    w = edge_weights(g, transformation)
    s0 = w.sum()
    groups = [np.flatnonzero(g.section == s) for s in np.unique(g.section)]

    def centre(z):
        z = z.copy()
        for idx in groups:
            z[idx] -= z[idx].mean(0)
        return z

    def stat(zz):
        num = np.zeros(zz.shape[1])
        for start in range(0, len(g.src), edge_chunk):
            sl = slice(start, start + edge_chunk)
            num += (w[sl, None] * zz[g.src[sl]] * zz[g.dst[sl]]).sum(0)
        den = (zz * zz).sum(0)
        with np.errstate(invalid="ignore", divide="ignore"):
            return (n / s0) * num / den

    obs = np.full(p, np.nan)
    hits = np.zeros(p)
    rng = np.random.default_rng(seed)
    perms = [[rng.permutation(idx) for idx in groups] for _ in range(n_perms)]
    for cols, block_vals in _column_blocks(values, block):
        z = centre(block_vals)
        o = stat(z)
        obs[cols] = o
        for perm in perms:
            zp = z.copy()
            for idx, pi in zip(groups, perm):
                zp[idx] = z[pi]
            hits[cols] += stat(zp) >= o - 1e-12
    # normality-assumption moments (Cliff & Ord)
    e = -1.0 / (n - 1)
    key = g.src * n + g.dst
    rev = g.dst * n + g.src
    order = np.argsort(key)
    pos = np.clip(np.searchsorted(key[order], rev), 0, max(len(key) - 1, 0))
    has_rev = key[order][pos] == rev if len(key) else np.zeros(0, bool)
    w_rev = np.where(has_rev, w[order][pos], 0.0) if len(key) else np.zeros(0)
    s1 = 0.5 * ((w + w_rev) ** 2).sum() + 0.5 * (w[~has_rev] ** 2).sum()
    out_w = np.bincount(g.src, weights=w, minlength=n)
    in_w = np.bincount(g.dst, weights=w, minlength=n)
    s2 = ((out_w + in_w) ** 2).sum()
    var = (n * n * s1 - n * s2 + 3 * s0 * s0) / ((n * n - 1) * s0 * s0) - e * e
    zscore = (obs - e) / math.sqrt(var)
    pv = _norm_sf(zscore)
    res = {"I": obs, "expected": np.full(p, e), "var_norm": np.full(p, var),
           "z": zscore, "p_norm": pv}
    ok = np.isfinite(pv)
    q = np.full(p, np.nan)
    if ok.any():
        q[ok] = bh(pv[ok])
    res["q_norm"] = q
    if n_perms:
        pp = (hits + 1) / (n_perms + 1)
        pp[~np.isfinite(obs)] = np.nan          # a constant feature has no I and no p
        res["p_perm"] = pp
    return res


def neighbourhood_enrichment(labels: np.ndarray, g: SpatialGraph, *, n_perms: int = 1000,
                             seed: int = 0) -> dict:
    lab_names, lab = np.unique(np.asarray(labels).astype(str), return_inverse=True)
    k = len(lab_names)

    def counts(lv):
        return np.bincount(lv[g.src] * k + lv[g.dst], minlength=k * k).reshape(k, k)

    obs = counts(lab)
    rng = np.random.default_rng(seed)
    groups = [np.flatnonzero(g.section == s) for s in np.unique(g.section)]
    perms = np.zeros((n_perms, k, k))
    for i in range(n_perms):
        lp = lab.copy()
        for idx in groups:
            lp[idx] = lab[rng.permutation(idx)]
        perms[i] = counts(lp)
    mu, sd = perms.mean(0), perms.std(0)
    with np.errstate(invalid="ignore", divide="ignore"):
        zz = np.where(sd > 0, (obs - mu) / sd, np.nan)
    p_hi = (1 + (perms >= obs).sum(0)) / (n_perms + 1)
    p_lo = (1 + (perms <= obs).sum(0)) / (n_perms + 1)
    return {"labels": lab_names.tolist(), "observed": obs, "expected": mu, "z": zz,
            "p_enriched": p_hi, "p_depleted": p_lo, "n_perms": n_perms}
