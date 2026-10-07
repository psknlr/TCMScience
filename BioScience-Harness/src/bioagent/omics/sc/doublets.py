"""Doublet detection by Scrublet's method (Wolock, Lopez & Klein 2019, *Cell Systems* 8:281).

A doublet looks like the sum of two cells, so Scrublet simulates doublets by adding the
raw counts of random pairs of observed cells (twice as many as there are cells), embeds
observed and simulated cells together, and scores each observed cell by how many of its
neighbours are simulated doublets. With k neighbours, an expected doublet rate rho and
r simulated doublets per observed cell, the score is Scrublet's

    q = (simulated neighbours + 1) / (k + 2)
    score = q rho / r / (1 - rho - q (1 - rho - rho / r))

The steps are Scrublet's defaults: total-count normalisation, genes kept above the
85th percentile of a v-score (the Fano factor over the one a fitted CV^2 floor predicts;
a simpler fit than Klein et al.'s gamma model, ranking genes the same way in practice),
z-scoring, 30 principal components fitted on the observed cells, and
k = round(0.5 sqrt(n)) neighbours widened by (1 + simulated / observed), as Scrublet's
classifier widens them. The threshold is the minimum of the
smoothed histogram of the simulated doublets' scores between its two modes, as
skimage's ``threshold_minimum`` finds it; when the histogram is not bimodal no doublet is
called, as Scrublet does, unless a threshold is given. Scored per sample, since doublets
form within a library. Cells in a continuous transition between two types resemble
doublets of those types and can be flagged; the report lists the flagged cells' clusters.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np
from scipy import sparse

from .graph import knn

__all__ = ["DoubletResult", "scrublet"]


@dataclass
class DoubletResult:
    scores: np.ndarray
    predicted: np.ndarray
    threshold: float
    simulated_scores: np.ndarray
    detail: dict[str, Any]


def _vscore(x: np.ndarray, min_mean: float = 0.0) -> np.ndarray:
    """Klein et al.'s v-score: the gene's Fano factor over the one expected from noise."""
    mu = x.mean(axis=0)
    var = x.var(axis=0, ddof=1)
    ok = mu > min_mean
    fano = np.zeros_like(mu)
    fano[ok] = var[ok] / mu[ok]
    # the excess over Poisson, relative to a fitted CV^2 floor: a robust stand-in for
    # Klein's gamma fit, ranking genes the same way for the percentile cut
    cv2 = np.zeros_like(mu)
    cv2[ok] = var[ok] / mu[ok] ** 2
    if ok.sum() > 10:
        floor = np.percentile(cv2[ok] - 1 / mu[ok], 5)
    else:
        floor = 0.0
    expected = np.ones_like(mu)
    expected[ok] = 1 + max(floor, 0.0) * mu[ok]
    return np.where(ok, fano / expected, 0.0)


def _local_maxima(hist: np.ndarray) -> list[int]:
    """skimage's find_local_maxima_idx: plateaus count once."""
    out, direction = [], 1
    for i in range(len(hist) - 1):
        if direction > 0:
            if hist[i + 1] < hist[i]:
                direction = -1
                out.append(i)
        elif hist[i + 1] > hist[i]:
            direction = 1
    return out


def _threshold(scores: np.ndarray, max_iter: int = 10_000) -> float | None:
    """skimage's threshold_minimum, as Scrublet calls it: smooth the 256-bin histogram
    until at most two maxima remain and take the minimum between them; None when the
    histogram is not bimodal (Scrublet then calls no doublets)."""
    from scipy.ndimage import uniform_filter1d
    hist, edges = np.histogram(scores, bins=256)
    centres = (edges[:-1] + edges[1:]) / 2
    smooth = hist.astype(np.float64)
    maxima: list[int] = []
    for _ in range(max_iter):
        smooth = uniform_filter1d(smooth, 3)
        maxima = _local_maxima(smooth)
        if len(maxima) < 3:
            break
    if len(maxima) != 2:
        return None
    lo, hi = maxima
    return float(centres[lo + int(np.argmin(smooth[lo:hi + 1]))])


def scrublet(counts: sparse.csr_matrix, *, expected_rate: float = 0.06,
             sim_ratio: float = 2.0, n_pcs: int = 30, seed: int = 0,
             threshold: float | None = None) -> DoubletResult:
    n = counts.shape[0]
    rng = np.random.default_rng(seed)
    if n < 50:
        return DoubletResult(np.zeros(n), np.zeros(n, dtype=bool), float("nan"),
                             np.zeros(0), {"skipped": f"{n} cells is too few to score"})
    x = counts.astype(np.float64).tocsr()
    totals = np.asarray(x.sum(axis=1)).ravel()
    gene_cells = np.diff(x.tocsc().indptr)
    gene_counts = np.asarray(x.sum(axis=0)).ravel()
    keep = (gene_cells >= 3) & (gene_counts >= 3)
    norm = (sparse.diags(totals.mean() / np.maximum(totals, 1)) @ x)[:, keep].toarray()
    v = _vscore(norm, min_mean=0.1)
    genes = np.flatnonzero(keep)[v >= np.percentile(v, 85)]
    n_sim = int(round(sim_ratio * n))
    pairs = rng.integers(0, n, size=(n_sim, 2))
    sim = x[pairs[:, 0]] + x[pairs[:, 1]]
    sim_totals = np.asarray(sim.sum(axis=1)).ravel()
    obs_n = (sparse.diags(totals.mean() / np.maximum(totals, 1)) @ x)[:, genes].toarray()
    sim_n = (sparse.diags(totals.mean() / np.maximum(sim_totals, 1)) @ sim)[:, genes].toarray()
    mu, sd = obs_n.mean(axis=0), obs_n.std(axis=0, ddof=1)
    sd[sd == 0] = 1.0
    zo, zs = (obs_n - mu) / sd, (sim_n - mu) / sd
    k_pcs = min(n_pcs, min(zo.shape) - 1)
    _, _, vt = np.linalg.svd(zo - zo.mean(axis=0), full_matrices=False)
    basis = vt[:k_pcs].T
    centre = zo.mean(axis=0)
    emb = np.vstack([(zo - centre) @ basis, (zs - centre) @ basis])
    # Scrublet's k, then widened by the factor the simulated cells grew the data by
    k = max(int(round(round(0.5 * math.sqrt(n)) * (1 + n_sim / n))), 1)
    idx, _ = knn(emb, k + 1)
    neigh = idx[:, 1:]                                   # self excluded
    is_sim = np.zeros(len(emb), dtype=bool)
    is_sim[n:] = True
    q = (is_sim[neigh].sum(axis=1) + 1) / (k + 2)
    rho, r = expected_rate, n_sim / n
    score = q * rho / r / (1 - rho - q * (1 - rho - rho / r))
    obs, simulated = score[:n], score[n:]
    how = "given"
    if threshold is None:
        threshold = _threshold(simulated)
        how = "histogram minimum" if threshold is not None else "none"
    if threshold is None:
        return DoubletResult(scores=obs, predicted=np.zeros(n, dtype=bool),
                             threshold=float("nan"), simulated_scores=simulated,
                             detail={"expected_rate": expected_rate, "simulated": n_sim,
                                     "k": k, "genes": int(len(genes)), "pcs": k_pcs,
                                     "threshold_method": "none: the simulated doublets' "
                                     "scores are not bimodal, so no doublet is called; "
                                     "give a threshold to call them"})
    return DoubletResult(scores=obs, predicted=obs > threshold, threshold=threshold,
                         simulated_scores=simulated,
                         detail={"expected_rate": expected_rate, "simulated": n_sim,
                                 "k": k, "genes": int(len(genes)), "pcs": k_pcs,
                                 "threshold_method": how,
                                 "detectable_fraction": float((simulated > threshold).mean())})
