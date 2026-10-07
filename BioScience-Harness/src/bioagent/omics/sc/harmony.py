"""Harmony batch integration (Korsunsky et al. 2019, *Nature Methods* 16:1289).

Cells are softly assigned to clusters in the cosine-normalised PCA space, with a penalty
that pushes each cluster towards the batch composition of the whole data set (theta);
each cluster then removes, by a ridge-regularised linear mixture of experts, the part of
its cells' embedding that the batch explains. The two steps alternate until the
objective settles. Defaults are the original R package's: theta 2, lambda 1, sigma 0.1,
min(round(n / 30), 100) clusters, blocks of 5% of cells, at most 10 rounds of 20
clustering iterations, tolerances 1e-5 and 1e-4.

The input and output are cells x PCs; only the embedding changes, never the counts, so
batch-corrected values are used for the graph, clusters and layout and the original
expression for markers and differential expression.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

import numpy as np

__all__ = ["HarmonyResult", "harmony"]


@dataclass
class HarmonyResult:
    embedding: np.ndarray               # cells x PCs, corrected
    clusters: int
    rounds: int
    converged: bool
    objective: list[float] = field(default_factory=list)
    detail: dict[str, Any] = field(default_factory=dict)


def _kmeans(x: np.ndarray, k: int, rng: np.random.Generator, *, n_init: int = 3,
            iters: int = 25) -> np.ndarray:
    """k-means++ and Lloyd's iterations; the centroids of the best of ``n_init`` runs."""
    best, best_inertia = None, np.inf
    n = x.shape[0]
    sq = (x ** 2).sum(axis=1)
    for _ in range(n_init):
        centres = [x[rng.integers(n)]]
        d2 = np.maximum(sq - 2 * x @ centres[0] + centres[0] @ centres[0], 0)
        for _ in range(1, k):
            p = d2 / d2.sum() if d2.sum() > 0 else np.full(n, 1 / n)
            c = x[rng.choice(n, p=p)]
            centres.append(c)
            d2 = np.minimum(d2, np.maximum(sq - 2 * x @ c + c @ c, 0))
        c = np.array(centres)
        for _ in range(iters):
            dist = sq[:, None] - 2 * x @ c.T + (c ** 2).sum(axis=1)[None, :]
            label = np.argmin(dist, axis=1)
            new = np.array([x[label == j].mean(axis=0) if np.any(label == j) else c[j]
                            for j in range(k)])
            if np.allclose(new, c):
                break
            c = new
        inertia = float(np.min(sq[:, None] - 2 * x @ c.T + (c ** 2).sum(axis=1)[None, :],
                               axis=1).sum())
        if inertia < best_inertia:
            best, best_inertia = c, inertia
    return best


def _unit(m: np.ndarray, axis: int = 0) -> np.ndarray:
    norm = np.linalg.norm(m, axis=axis, keepdims=True)
    return m / np.where(norm > 0, norm, 1.0)


def harmony(pcs: np.ndarray, batches: Sequence[Any], *, theta: float = 2.0,
            lamb: float = 1.0, sigma: float = 0.1, n_clusters: int | None = None,
            block_size: float = 0.05, max_rounds: int = 10, max_kmeans: int = 20,
            eps_cluster: float = 1e-5, eps_harmony: float = 1e-4,
            seed: int = 0) -> HarmonyResult:
    rng = np.random.default_rng(seed)
    z_orig = np.asarray(pcs, dtype=np.float64).T              # d x N
    d, n = z_orig.shape
    labels = list(dict.fromkeys(batches))
    b = len(labels)
    if b < 2:
        return HarmonyResult(np.asarray(pcs, dtype=np.float64).copy(), 0, 0, True,
                             detail={"skipped": "one batch"})
    index = {v: i for i, v in enumerate(labels)}
    phi = np.zeros((b, n))
    phi[[index[v] for v in batches], np.arange(n)] = 1.0
    pr_b = phi.sum(axis=1) / n
    k = n_clusters or int(min(round(n / 30.0), 100))
    k = max(min(k, n - 1), 2)
    theta_v = np.full(b, theta)
    lamb_mat = np.diag(np.concatenate([[0.0], np.full(b, lamb)]))
    phi_moe = np.vstack([np.ones(n), phi])

    z_cos = _unit(z_orig)
    y = _unit(_kmeans(z_cos.T, k, rng).T)                     # d x K
    dist = 2 * (1 - y.T @ z_cos)                              # K x N
    r = -dist / sigma
    r = np.exp(r - r.max(axis=0))
    r /= r.sum(axis=0)
    e = np.outer(r.sum(axis=1), pr_b)                         # K x B
    o = r @ phi.T                                             # K x B

    def objective() -> float:
        kmeans_error = float((r * dist).sum())
        with np.errstate(divide="ignore", invalid="ignore"):
            ent = np.where(r > 0, r * np.log(r), 0.0)
        entropy = float(ent.sum() * sigma)
        cross = float(((r * sigma) * ((theta_v[None, :] * np.log((o + 1) / (e + 1))) @ phi))
                      .sum())
        return kmeans_error + entropy + cross

    history = [objective()]
    z_corr = z_orig.copy()
    converged = False
    rounds = 0
    for rounds in range(1, max_rounds + 1):
        # -- clustering, with the diversity penalty -------------------------------
        window: list[float] = []
        for _ in range(max_kmeans):
            y = _unit(z_cos @ r.T)
            dist = 2 * (1 - y.T @ z_cos)
            scale = np.exp(-dist / sigma - (-dist / sigma).max(axis=0))
            order = rng.permutation(n)
            step = max(int(np.ceil(n * block_size)), 1)
            for start in range(0, n, step):
                cells = order[start:start + step]
                e -= np.outer(r[:, cells].sum(axis=1), pr_b)
                o -= r[:, cells] @ phi[:, cells].T
                new = scale[:, cells] * (((e + 1) / (o + 1)) ** theta_v[None, :]
                                         @ phi[:, cells])
                r[:, cells] = new / new.sum(axis=0)
                e += np.outer(r[:, cells].sum(axis=1), pr_b)
                o += r[:, cells] @ phi[:, cells].T
            window.append(objective())
            if len(window) > 3:                    # harmonypy: windows of three rounds
                old, new_ = sum(window[-4:-1]), sum(window[-3:])
                if abs(old - new_) < eps_cluster * abs(old):
                    break
        # -- correction: a ridge mixture of experts per cluster ---------------------
        z_corr = z_orig.copy()
        for c in range(k):
            phi_rk = phi_moe * r[c]
            w = np.linalg.solve(phi_rk @ phi_moe.T + lamb_mat, phi_rk @ z_orig.T)
            w[0, :] = 0.0                                      # keep the intercept
            z_corr -= w.T @ phi_rk
        z_cos = _unit(z_corr)
        history.append(objective())
        if (history[-2] - history[-1]) < eps_harmony * abs(history[-2]):
            converged = True
            break
    return HarmonyResult(embedding=z_corr.T, clusters=k, rounds=rounds, converged=converged,
                         objective=history,
                         detail={"theta": theta, "lambda": lamb, "sigma": sigma,
                                 "batches": [str(x) for x in labels]})
