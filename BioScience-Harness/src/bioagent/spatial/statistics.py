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


def morans_i(values: np.ndarray, g: SpatialGraph, *, transformation: str = "r",
             n_perms: int = 0, seed: int = 0) -> dict[str, np.ndarray]:
    """Moran's I for each column of ``values`` (nodes × features)."""
    x = np.asarray(values, float)
    if x.ndim == 1:
        x = x[:, None]
    if x.shape[0] != g.n:
        raise ValueError("one row of values per graph node")
    z = x.copy()
    for s in np.unique(g.section):
        m = g.section == s
        z[m] -= z[m].mean(0)
    w = edge_weights(g, transformation)
    n = g.n
    s0 = w.sum()

    def stat(zz):
        num = np.zeros(zz.shape[1])
        for start in range(0, len(g.src), 200000):
            sl = slice(start, start + 200000)
            num += (w[sl, None] * zz[g.src[sl]] * zz[g.dst[sl]]).sum(0)
        den = (zz * zz).sum(0)
        with np.errstate(invalid="ignore", divide="ignore"):
            return (n / s0) * num / den

    obs = stat(z)
    # normality-assumption moments (Cliff & Ord)
    e = -1.0 / (n - 1)
    # S1 = 1/2 Σ (w_ij + w_ji)^2 ; S2 = Σ_i (w_i. + w_.i)^2
    key = g.src * n + g.dst
    rev = g.dst * n + g.src
    order = np.argsort(key)
    pos = np.searchsorted(key[order], rev)
    pos = np.clip(pos, 0, len(key) - 1)
    has_rev = key[order][pos] == rev
    w_rev = np.where(has_rev, w[order][pos], 0.0)
    s1 = 0.5 * ((w + w_rev) ** 2).sum() + 0.5 * (w[~has_rev] ** 2).sum()
    out_w = np.bincount(g.src, weights=w, minlength=n)
    in_w = np.bincount(g.dst, weights=w, minlength=n)
    s2 = ((out_w + in_w) ** 2).sum()
    var = (n * n * s1 - n * s2 + 3 * s0 * s0) / ((n * n - 1) * s0 * s0) - e * e
    zscore = (obs - e) / math.sqrt(var)
    p = _norm_sf(zscore)
    res = {"I": obs, "expected": np.full(x.shape[1], e), "var_norm": np.full(x.shape[1], var),
           "z": zscore, "p_norm": p}
    ok = np.isfinite(p)
    q = np.full(len(p), np.nan)
    if ok.any():
        q[ok] = bh(p[ok])
    res["q_norm"] = q
    if n_perms:
        rng = np.random.default_rng(seed)
        hits = np.zeros(x.shape[1])
        groups = [np.flatnonzero(g.section == s) for s in np.unique(g.section)]
        for _ in range(n_perms):
            zp = z.copy()
            for idx in groups:
                zp[idx] = z[rng.permutation(idx)]
            hits += stat(zp) >= obs - 1e-12
        res["p_perm"] = (hits + 1) / (n_perms + 1)
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
