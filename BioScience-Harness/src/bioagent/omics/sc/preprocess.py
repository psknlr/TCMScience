"""Normalisation, highly variable genes, scaling and PCA, with Scanpy's defaults.

* ``normalize_total`` to 10,000 counts per cell, then ``log1p`` (natural log);
* highly variable genes by the Seurat method (Satija et al. 2015; Scanpy's default
  ``flavor="seurat"``): mean and dispersion on the de-logged data, genes binned into 20
  bins by mean, dispersion z-scored within its bin (a bin holding one gene gets 1), the
  top 2,000 by normalised dispersion. With several batches each batch is scored
  separately and genes ranked by how many batches call them variable, then by mean
  normalised dispersion, as Scanpy's ``batch_key`` does;
* ``scale``: each gene centred and divided by its SD, clipped to [-10, 10];
* PCA of the scaled matrix by SVD, signs fixed so each component's largest loading is
  positive.
"""

from __future__ import annotations

import numpy as np
from scipy import sparse

__all__ = ["normalize_log1p", "highly_variable_genes", "scale", "pca"]


def normalize_log1p(counts: sparse.csr_matrix, target_sum: float = 1e4) -> sparse.csr_matrix:
    totals = np.asarray(counts.sum(axis=1)).ravel()
    factors = np.where(totals > 0, target_sum / np.where(totals > 0, totals, 1.0), 0.0)
    x = sparse.diags(factors.astype(np.float64)) @ counts.astype(np.float64)
    x = sparse.csr_matrix(x)
    x.data = np.log1p(x.data)
    return x


def _seurat_dispersion(logx: sparse.csr_matrix, n_bins: int = 20) -> tuple[np.ndarray, ...]:
    """(mean, log dispersion, normalised dispersion) per gene, as Scanpy's seurat flavor."""
    x = logx.copy()
    x.data = np.expm1(x.data)
    n = x.shape[0]
    mean = np.asarray(x.mean(axis=0)).ravel()
    sq = np.asarray(x.multiply(x).mean(axis=0)).ravel()
    var = (sq - mean ** 2) * n / max(n - 1, 1)
    mean[mean == 0] = 1e-12
    dispersion = var / mean
    dispersion[dispersion == 0] = np.nan
    log_disp = np.log(dispersion)
    log_mean = np.log1p(mean)
    edges = np.linspace(log_mean.min(), log_mean.max(), n_bins + 1)
    bins = np.clip(np.digitize(log_mean, edges[1:-1], right=True), 0, n_bins - 1)
    norm = np.full(len(mean), np.nan)
    for b in np.unique(bins):
        idx = bins == b
        vals = log_disp[idx]
        ok = np.isfinite(vals)
        if ok.sum() == 1:
            norm[idx] = np.where(ok, 1.0, np.nan)
            continue
        mu = np.nanmean(vals)
        sd = np.nanstd(vals, ddof=1) if ok.sum() > 1 else np.nan
        if not np.isfinite(sd) or sd == 0:
            norm[idx] = np.where(ok, 0.0, np.nan)
        else:
            norm[idx] = (vals - mu) / sd
    return log_mean, log_disp, norm


def highly_variable_genes(logx: sparse.csr_matrix, *, n_top: int = 2000,
                          batches: np.ndarray | None = None) -> dict[str, np.ndarray]:
    """``highly_variable`` (bool), ``norm_dispersion`` and ``n_batches`` per gene."""
    g = logx.shape[1]
    n_top = min(n_top, g)
    if batches is None or len(set(batches)) < 2:
        _, _, norm = _seurat_dispersion(logx)
        score = np.where(np.isfinite(norm), norm, -np.inf)
        order = np.argsort(-score, kind="mergesort")[:n_top]
        hv = np.zeros(g, dtype=bool)
        hv[order[np.isfinite(score[order])]] = True
        return {"highly_variable": hv, "norm_dispersion": norm,
                "n_batches": hv.astype(int)}
    labels = list(dict.fromkeys(batches))
    calls = np.zeros(g, dtype=int)
    disp = np.zeros((len(labels), g))
    for k, b in enumerate(labels):
        sub = logx[np.asarray(batches) == b]
        _, _, norm = _seurat_dispersion(sub)
        disp[k] = np.where(np.isfinite(norm), norm, np.nan)
        score = np.where(np.isfinite(norm), norm, -np.inf)
        top = np.argsort(-score, kind="mergesort")[:n_top]
        calls[top[np.isfinite(score[top])]] += 1
    with np.errstate(all="ignore"):
        mean_disp = np.nanmean(disp, axis=0)
    mean_disp = np.where(np.isfinite(mean_disp), mean_disp, -np.inf)
    order = np.lexsort((-mean_disp, -calls))[:n_top]
    hv = np.zeros(g, dtype=bool)
    hv[order[calls[order] > 0]] = True
    return {"highly_variable": hv, "norm_dispersion": mean_disp, "n_batches": calls}


def scale(x: sparse.spmatrix | np.ndarray, max_value: float = 10.0) -> np.ndarray:
    dense = x.toarray() if sparse.issparse(x) else np.asarray(x, dtype=np.float64)
    mean = dense.mean(axis=0)
    sd = dense.std(axis=0, ddof=1)
    sd[sd == 0] = 1.0
    z = (dense - mean) / sd
    return np.clip(z, -max_value, max_value)


def pca(z: np.ndarray, n_comps: int = 50) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(cell scores, gene loadings, variance ratio) of the centred matrix."""
    zc = z - z.mean(axis=0)
    k = min(n_comps, min(zc.shape) - 1)
    u, s, vt = np.linalg.svd(zc, full_matrices=False)
    u, s, vt = u[:, :k], s[:k], vt[:k]
    signs = np.sign(vt[np.arange(k), np.argmax(np.abs(vt), axis=1)])
    signs[signs == 0] = 1.0
    scores = u * s * signs
    total = float((zc ** 2).sum()) or 1.0
    return scores, (vt.T * signs), (s ** 2) / total
