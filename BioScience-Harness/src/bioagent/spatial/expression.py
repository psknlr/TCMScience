"""Expression processing that ignores space: normalisation, variable genes, PCA, clusters.

Clusters here are *expression* clusters drawn on the tissue afterwards. They are not
spatial domains (no spatial information enters them), and markers are spot-level contrasts
between clusters chosen from the same data, so no p values are reported for them: spots
are neither independent nor were the groups defined independently of the genes.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .matrix import CSR

__all__ = ["ExpressionParams", "normalise", "highly_variable", "pca", "pca_csr", "kmeans",
           "choose_k", "markers"]


@dataclass(frozen=True)
class ExpressionParams:
    target_sum: float = 1e4
    n_hvg: int = 2000
    n_pcs: int = 30
    k: int | None = None              # None: chosen by silhouette in k_range
    k_range: tuple[int, int] = (4, 12)
    n_markers: int = 10
    seed: int = 0


def normalise(counts: CSR, target_sum: float) -> tuple[CSR, np.ndarray]:
    """Library-size normalisation to ``target_sum`` then log1p; returns factors too."""
    lib = counts.row_sums()
    factors = np.where(lib > 0, target_sum / np.maximum(lib, 1e-12), 0.0)
    return counts.scale_rows(factors).log1p(), factors


def highly_variable(counts: CSR, factors: np.ndarray, n_top: int, n_bins: int = 20
                    ) -> np.ndarray:
    """Seurat-flavour dispersion ranking: per gene mean and variance of normalised (not
    log) counts, log dispersion z-scored within mean bins; returns the top gene indices."""
    mean, var = counts.col_mean_var(row_scale=factors)
    with np.errstate(divide="ignore", invalid="ignore"):
        disp = np.where(mean > 0, var / mean, np.nan)
        disp = np.log(disp)
        lmean = np.log1p(mean)
    edges = np.linspace(np.nanmin(lmean), np.nanmax(lmean), n_bins + 1)
    b = np.clip(np.digitize(lmean, edges[1:-1]), 0, n_bins - 1)
    norm = np.full(len(mean), np.nan)
    for k in range(n_bins):
        sel = (b == k) & np.isfinite(disp)
        if sel.sum() == 0:
            continue
        mu, sd = disp[sel].mean(), disp[sel].std(ddof=1) if sel.sum() > 1 else 0.0
        norm[sel] = (disp[sel] - mu) / sd if sd > 0 else 0.0
    order = np.argsort(-np.nan_to_num(norm, nan=-np.inf), kind="stable")
    return np.sort(order[:min(n_top, int(np.isfinite(norm).sum()))])


def pca(x: np.ndarray, n_pcs: int, seed: int = 0) -> tuple[np.ndarray, np.ndarray]:
    """PCA of a dense (spots × genes) matrix, genes scaled to unit variance and clipped at
    10; signs fixed so each component's largest loading is positive (reproducible)."""
    z = x - x.mean(0)
    sd = z.std(0, ddof=1)
    z = np.clip(z / np.where(sd > 0, sd, 1.0), -10, 10)
    u, s, vt = np.linalg.svd(z, full_matrices=False)
    n = min(n_pcs, len(s))
    signs = np.sign(vt[:n][np.arange(n), np.abs(vt[:n]).argmax(1)])
    scores = u[:, :n] * s[:n] * signs
    var_ratio = (s ** 2 / (s ** 2).sum())[:n]
    return scores, var_ratio


def pca_csr(m: CSR, n_pcs: int, *, chunk: int = 4096) -> tuple[np.ndarray, np.ndarray]:
    """The same PCA as ``pca`` (scaled, clipped at 10), streamed from a sparse matrix.

    Rows are densified ``chunk`` at a time to accumulate the gene × gene scatter matrix;
    its eigenvectors give the loadings and the scores are projected chunk by chunk, so
    memory is O(chunk · genes + spots · n_pcs), not O(spots · genes).
    """
    n, p = m.shape
    mean, var = m.col_mean_var()
    sd = np.sqrt(var)
    sd = np.where(sd > 0, sd, 1.0)
    scatter = np.zeros((p, p))
    for start in range(0, n, chunk):
        z = np.clip((m.take_rows(np.arange(start, min(n, start + chunk))).to_dense(np.float64)
                     - mean) / sd, -10, 10)
        scatter += z.T @ z
    evals, evecs = np.linalg.eigh(scatter)
    order = np.argsort(evals)[::-1]
    evals, evecs = np.maximum(evals[order], 0), evecs[:, order]
    k = min(n_pcs, p)
    v = evecs[:, :k]
    v = v * np.sign(v[np.abs(v).argmax(0), np.arange(k)])
    scores = np.zeros((n, k))
    for start in range(0, n, chunk):
        rows = np.arange(start, min(n, start + chunk))
        z = np.clip((m.take_rows(rows).to_dense(np.float64) - mean) / sd, -10, 10)
        scores[rows] = z @ v
    return scores, (evals / evals.sum())[:k]


def kmeans(x: np.ndarray, k: int, *, seed: int = 0, n_init: int = 10, iters: int = 100
           ) -> tuple[np.ndarray, float]:
    """k-means with k-means++ starts; the run with the lowest inertia wins."""
    rng = np.random.default_rng(seed)
    best, best_inertia = None, np.inf
    for _ in range(n_init):
        c = [x[rng.integers(len(x))]]
        for _ in range(k - 1):
            d2 = _sqdist(x, np.array(c)).min(1)
            c.append(x[rng.choice(len(x), p=d2 / d2.sum())] if d2.sum() > 0
                     else x[rng.integers(len(x))])
        c = np.array(c)
        for _ in range(iters):
            lab = _sqdist(x, c).argmin(1)
            new = np.array([x[lab == j].mean(0) if (lab == j).any() else c[j]
                            for j in range(k)])
            if np.allclose(new, c):
                break
            c = new
        lab = _sqdist(x, c).argmin(1)     # labels of the final centroids
        inertia = float(((x - c[lab]) ** 2).sum())
        if inertia < best_inertia:
            best, best_inertia = lab, inertia
    # relabel clusters by size (largest = 0) so labels do not depend on start order
    sizes = np.bincount(best, minlength=k)
    order = np.argsort(-sizes, kind="stable")
    remap = np.empty(k, int)
    remap[order] = np.arange(k)
    return remap[best], best_inertia


def _sqdist(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    return np.maximum((a * a).sum(1)[:, None] + (b * b).sum(1)[None] - 2 * a @ b.T, 0.0)


def _silhouette(x: np.ndarray, lab: np.ndarray) -> float:
    d = np.sqrt(_sqdist(x, x))
    ks = np.unique(lab)
    if len(ks) < 2:
        return -1.0
    size = np.array([(lab == lab[i]).sum() for i in range(len(x))])
    a = np.array([d[i, lab == lab[i]].sum() / max(size[i] - 1, 1) for i in range(len(x))])
    b = np.array([min(d[i, lab == k].mean() for k in ks if k != lab[i]) for i in range(len(x))])
    with np.errstate(invalid="ignore", divide="ignore"):
        s = np.where(np.maximum(a, b) > 0, (b - a) / np.maximum(a, b), 0.0)
    s[size == 1] = 0.0                    # a singleton scores 0 (scikit-learn convention)
    return float(np.mean(s))


def choose_k(x: np.ndarray, k_range: tuple[int, int], *, seed: int = 0, sample: int = 1500,
             min_cluster_size: int = 10) -> tuple[int, dict[int, float]]:
    """k with the best silhouette among clusterings whose smallest cluster has at least
    ``min_cluster_size`` spots (an outlier spot is not a cluster)."""
    rng = np.random.default_rng(seed)
    idx = rng.choice(len(x), min(sample, len(x)), replace=False)
    scores, admissible = {}, {}
    for k in range(k_range[0], k_range[1] + 1):
        lab, _ = kmeans(x, k, seed=seed, n_init=4)
        scores[k] = _silhouette(x[idx], lab[idx])
        if np.bincount(lab, minlength=k).min() >= min_cluster_size:
            admissible[k] = scores[k]
    pool = admissible or scores
    return max(pool, key=pool.get), scores


def markers(lognorm: CSR, labels: np.ndarray, gene_names: np.ndarray, n_top: int,
            gene_ids: np.ndarray | None = None) -> list[dict]:
    """Top genes per cluster by Welch t of log-normalised expression against all other
    spots, with log2 fold change of mean normalised expression and detection rates."""
    out = []
    for c in np.unique(labels):
        a = lognorm.take_rows(np.flatnonzero(labels == c))
        b = lognorm.take_rows(np.flatnonzero(labels != c))
        ma, va = a.col_mean_var()
        mb, vb = b.col_mean_var()
        se = np.sqrt(va / a.shape[0] + vb / b.shape[0])
        t = np.where(se > 0, (ma - mb) / np.where(se > 0, se, 1), 0.0)
        pa = a.col_nnz() / a.shape[0]
        pb = b.col_nnz() / b.shape[0]
        # from the means of log values: a geometric-type fold change, hence "approx"
        lfc = np.log2((np.expm1(ma) + 1e-9) / (np.expm1(mb) + 1e-9))
        for j in np.argsort(-t)[:n_top]:
            out.append({"cluster": int(c), "gene": str(gene_names[j]),
                        "gene_id": str(gene_ids[j]) if gene_ids is not None else "",
                        "t": float(t[j]),
                        "log2fc_approx": float(lfc[j]), "pct_in": float(pa[j]),
                        "pct_out": float(pb[j]), "mean_log_in": float(ma[j]),
                        "mean_log_out": float(mb[j])})
    return out
