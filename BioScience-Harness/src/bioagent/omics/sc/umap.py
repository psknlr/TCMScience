"""A UMAP layout of the fuzzy kNN graph (McInnes, Healy & Melville 2018).

The steps are UMAP's ``simplicial_set_embedding``: edges weaker than max / n_epochs are
dropped, the layout starts from the graph's spectral embedding (the normalised
Laplacian's smallest non-trivial eigenvectors, scaled to [0, 10]), and stochastic
gradient descent pulls each edge's ends together (as often as its weight says) and
pushes five random points away per pull, with the curve 1 / (1 + a d^2b) fitted to
``min_dist`` = 0.5 and ``spread`` = 1 (Scanpy's defaults) and a learning rate falling
linearly to zero over 500 epochs (200 above 10,000 cells).

One difference from umap-learn: the edges due in an epoch are updated together, as
arrays, rather than one after another. The fixed points are the same; the path to them
differs, so coordinates are not umap-learn's. A layout is a picture of the graph for
reading the clusters, not a measurement, and distances in it are not quantitative.
"""

from __future__ import annotations

import numpy as np
from scipy import sparse
from scipy.optimize import curve_fit
from scipy.sparse.linalg import eigsh

__all__ = ["umap_layout", "find_ab"]


def find_ab(spread: float = 1.0, min_dist: float = 0.5) -> tuple[float, float]:
    x = np.linspace(0, spread * 3, 300)
    y = np.where(x < min_dist, 1.0, np.exp(-(x - min_dist) / spread))
    (a, b), _ = curve_fit(lambda d, a, b: 1.0 / (1.0 + a * d ** (2 * b)), x, y)
    return float(a), float(b)


def _spectral(graph: sparse.csr_matrix, dim: int, rng: np.random.Generator) -> np.ndarray:
    n = graph.shape[0]
    deg = np.asarray(graph.sum(axis=1)).ravel()
    d = sparse.diags(1.0 / np.sqrt(np.maximum(deg, 1e-12)))
    lap = sparse.identity(n) - d @ graph @ d
    k = dim + 1
    try:
        vals, vecs = eigsh(lap, k=k, which="SM", tol=1e-4, v0=np.ones(n),
                           maxiter=n * 5)
        order = np.argsort(vals)
        emb = vecs[:, order[1:k]]
    except Exception:                                   # noqa: BLE001 - fall back
        emb = rng.uniform(-10, 10, size=(n, dim))
    span = emb.max(axis=0) - emb.min(axis=0)
    emb = 10.0 * (emb - emb.min(axis=0)) / np.where(span > 0, span, 1.0)
    return emb + rng.normal(scale=1e-4, size=emb.shape)


def umap_layout(graph: sparse.spmatrix, *, dim: int = 2, n_epochs: int | None = None,
                min_dist: float = 0.5, spread: float = 1.0, negative_rate: int = 5,
                gamma: float = 1.0, alpha: float = 1.0, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    g = sparse.coo_matrix(graph)
    n = g.shape[0]
    epochs = n_epochs or (500 if n <= 10_000 else 200)
    keep = g.data >= g.data.max() / epochs
    head, tail, w = g.row[keep], g.col[keep], g.data[keep]
    a, b = find_ab(spread, min_dist)
    emb = _spectral(sparse.csr_matrix(graph), dim, rng)
    n_samples = epochs * (w / w.max())
    eps = np.where(n_samples > 0, epochs / np.maximum(n_samples, 1e-12), -1.0)
    eps_neg = eps / negative_rate
    next_pos = eps.copy()
    next_neg = eps_neg.copy()
    for epoch in range(epochs):
        lr = alpha * (1.0 - epoch / epochs)
        due = np.flatnonzero((eps > 0) & (next_pos <= epoch))
        if not len(due):
            continue
        j, k = head[due], tail[due]
        diff = emb[j] - emb[k]
        d2 = (diff ** 2).sum(axis=1)
        coeff = np.where(d2 > 0, -2.0 * a * b * np.power(d2, b - 1.0)
                         / (a * np.power(d2, b) + 1.0), 0.0)
        grad = np.clip(coeff[:, None] * diff, -4.0, 4.0)
        np.add.at(emb, j, grad * lr)
        np.add.at(emb, k, -grad * lr)
        next_pos[due] += eps[due]
        n_neg = ((epoch - next_neg[due]) / eps_neg[due]).astype(int)
        n_neg = np.maximum(n_neg, 0)
        if n_neg.sum():
            src = np.repeat(j, n_neg)
            dst = rng.integers(0, n, size=len(src))
            diff = emb[src] - emb[dst]
            d2 = (diff ** 2).sum(axis=1)
            coeff = np.where(d2 > 0, 2.0 * gamma * b / ((0.001 + d2)
                                                        * (a * np.power(d2, b) + 1.0)), 0.0)
            grad = np.where(coeff[:, None] > 0, np.clip(coeff[:, None] * diff, -4.0, 4.0),
                            0.0)
            grad[src == dst] = 0.0
            np.add.at(emb, src, grad * lr)
        next_neg[due] += n_neg * eps_neg[due]
    return emb
