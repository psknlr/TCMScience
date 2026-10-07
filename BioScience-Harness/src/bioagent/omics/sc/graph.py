"""Nearest-neighbour graphs over an embedding.

``knn`` finds each cell's k nearest neighbours exactly (Euclidean, in chunks, self
first). ``umap_connectivities`` turns them into UMAP's fuzzy simplicial set (McInnes et
al. 2018), the graph Scanpy clusters and lays out by default: per cell, the distance to
its nearest neighbour (rho) and a bandwidth (sigma) found by binary search so that the
memberships of its neighbours sum to log2(k); then the fuzzy union A + A' - A o A'.
"""

from __future__ import annotations

import math

import numpy as np
from scipy import sparse

__all__ = ["knn", "umap_connectivities", "knn_graph"]

SMOOTH_K_TOLERANCE = 1e-5
MIN_K_DIST_SCALE = 1e-3


def knn(x: np.ndarray, k: int, *, chunk: int = 2048,
        query: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray]:
    """(indices, distances), each n x k, nearest first; a point is its own first
    neighbour when ``query`` is None."""
    x = np.asarray(x, dtype=np.float64)
    q = x if query is None else np.asarray(query, dtype=np.float64)
    k = min(k, x.shape[0])
    sq = (x ** 2).sum(axis=1)
    idx = np.empty((q.shape[0], k), dtype=np.int64)
    dist = np.empty((q.shape[0], k))
    for start in range(0, q.shape[0], chunk):
        block = q[start:start + chunk]
        d2 = np.maximum((block ** 2).sum(axis=1)[:, None] + sq[None, :] - 2 * block @ x.T, 0)
        if query is None:
            rows = np.arange(block.shape[0])
            d2[rows, start + rows] = -1.0                  # self first, at distance 0
        part = np.argpartition(d2, k - 1, axis=1)[:, :k]
        pd2 = np.take_along_axis(d2, part, axis=1)
        order = np.lexsort((part, pd2), axis=1)
        idx[start:start + chunk] = np.take_along_axis(part, order, axis=1)
        dist[start:start + chunk] = np.sqrt(np.maximum(np.take_along_axis(pd2, order, axis=1),
                                                       0.0))
    return idx, dist


def _smooth_knn_dist(dist: np.ndarray, k: int, n_iter: int = 64,
                     local_connectivity: float = 1.0) -> tuple[np.ndarray, np.ndarray]:
    target = math.log2(k)
    n = dist.shape[0]
    rho = np.zeros(n)
    sigma = np.zeros(n)
    mean_all = float(dist.mean())
    for i in range(n):
        lo, hi, mid = 0.0, np.inf, 1.0
        row = dist[i]
        nonzero = row[row > 0]
        if len(nonzero) >= local_connectivity:
            index = int(math.floor(local_connectivity))
            interp = local_connectivity - index
            if index > 0:
                rho[i] = nonzero[index - 1]
                if interp > SMOOTH_K_TOLERANCE:
                    rho[i] += interp * (nonzero[index] - nonzero[index - 1])
            else:
                rho[i] = interp * nonzero[0]
        elif len(nonzero):
            rho[i] = nonzero.max()
        d = row[1:] - rho[i]
        for _ in range(n_iter):
            psum = float(np.where(d > 0, np.exp(-d / mid), 1.0).sum())
            if abs(psum - target) < SMOOTH_K_TOLERANCE:
                break
            if psum > target:
                hi = mid
                mid = (lo + hi) / 2.0
            else:
                lo = mid
                mid = mid * 2 if hi == np.inf else (lo + hi) / 2.0
        sigma[i] = mid
        mean_i = float(row.mean())
        if rho[i] > 0.0:
            sigma[i] = max(sigma[i], MIN_K_DIST_SCALE * mean_i)
        else:
            sigma[i] = max(sigma[i], MIN_K_DIST_SCALE * mean_all)
    return sigma, rho


def umap_connectivities(idx: np.ndarray, dist: np.ndarray) -> sparse.csr_matrix:
    """UMAP's symmetric fuzzy graph from a kNN result whose first column is self."""
    n, k = idx.shape
    sigma, rho = _smooth_knn_dist(dist, k)
    vals = np.where(dist - rho[:, None] <= 0, 1.0,
                    np.exp(-(dist - rho[:, None]) / sigma[:, None]))
    vals[idx == np.arange(n)[:, None]] = 0.0
    a = sparse.csr_matrix((vals.ravel(), (np.repeat(np.arange(n), k), idx.ravel())),
                          shape=(n, n))
    a.eliminate_zeros()
    t = a.T.tocsr()
    p = a + t - a.multiply(t)
    p.eliminate_zeros()
    return p.tocsr()


def knn_graph(idx: np.ndarray) -> sparse.csr_matrix:
    """The binary, symmetrised kNN adjacency (no self loops)."""
    n, k = idx.shape
    a = sparse.csr_matrix((np.ones(n * k), (np.repeat(np.arange(n), k), idx.ravel())),
                          shape=(n, n))
    a.setdiag(0)
    a.eliminate_zeros()
    a = ((a + a.T) > 0).astype(np.float64)
    return a.tocsr()
