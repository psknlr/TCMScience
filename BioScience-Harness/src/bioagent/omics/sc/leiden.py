"""Leiden community detection (Traag, Waltman & van Eck 2019, *Scientific Reports* 9:5233).

The quality is modularity with a resolution parameter (leidenalg's
``RBConfigurationVertexPartition``, Scanpy's default):

    Q = sum_ij (A_ij - gamma k_i k_j / 2m) [c_i = c_j]

Each level runs the three phases of the paper:

1. **fast local moving**: nodes in a queue move to the neighbouring community that most
   increases Q; when a node moves, its neighbours outside the new community re-enter
   the queue;
2. **refinement**: within each community, singletons that are well connected to it merge
   into well-connected sub-communities, chosen at random with probability proportional
   to exp(gain / theta) among the moves that do not decrease Q, so every community of
   the result is guaranteed connected;
3. **aggregation** of the refined partition, the unrefined one giving the aggregate's
   starting partition.

It stops when local moving leaves every node of the aggregate in its own community, and
the whole is repeated from the partition found until it no longer changes (leidenalg's
``n_iterations=-1``). Seeded, so a run is reproducible.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass

import numpy as np
from scipy import sparse

__all__ = ["LeidenResult", "leiden", "modularity"]


@dataclass
class LeidenResult:
    membership: np.ndarray
    quality: float              # modularity at the given resolution
    iterations: int
    levels: int


def modularity(adj: sparse.csr_matrix, membership: np.ndarray, gamma: float = 1.0) -> float:
    a = sparse.csr_matrix(adj)
    deg = np.asarray(a.sum(axis=1)).ravel()
    two_m = deg.sum()
    if two_m == 0:
        return 0.0
    coo = a.tocoo()
    inside = coo.data[membership[coo.row] == membership[coo.col]].sum()
    k_c = np.bincount(membership, weights=deg)
    return float((inside - gamma * (k_c ** 2).sum() / two_m) / two_m)


class _Level:
    def __init__(self, adj: sparse.csr_matrix):
        a = sparse.csr_matrix(adj)
        a.sum_duplicates()
        self.indptr, self.indices, self.weights = a.indptr, a.indices, a.data.astype(float)
        self.n = a.shape[0]
        self.deg = np.asarray(a.sum(axis=1)).ravel()
        self.two_m = float(self.deg.sum())
        self.adj = a

    def neighbours(self, v: int) -> tuple[np.ndarray, np.ndarray]:
        lo, hi = self.indptr[v], self.indptr[v + 1]
        nb, w = self.indices[lo:hi], self.weights[lo:hi]
        keep = nb != v
        return nb[keep], w[keep]


def _weights_to(comm: np.ndarray, nb: np.ndarray, w: np.ndarray) -> dict[int, float]:
    out: dict[int, float] = {}
    for c, x in zip(comm[nb].tolist(), w.tolist()):
        out[c] = out.get(c, 0.0) + x
    return out


def _move_nodes(g: _Level, comm: np.ndarray, gamma: float, rng: np.random.Generator
                ) -> bool:
    """Fast local moving; True when some node moved."""
    k = np.bincount(comm, weights=g.deg, minlength=g.n).astype(float)
    sizes = np.bincount(comm, minlength=g.n)
    empty = [c for c in range(g.n) if sizes[c] == 0]
    queue = deque(rng.permutation(g.n).tolist())
    queued = np.ones(g.n, dtype=bool)
    moved = False
    scale = gamma / g.two_m if g.two_m else 0.0
    while queue:
        v = queue.popleft()
        queued[v] = False
        nb, w = g.neighbours(v)
        wt = _weights_to(comm, nb, w)
        old = int(comm[v])
        dv = g.deg[v]
        k[old] -= dv
        sizes[old] -= 1
        best_c, best_gain = old, wt.get(old, 0.0) - scale * dv * k[old]
        for c, wc in wt.items():
            gain = wc - scale * dv * k[c]
            if gain > best_gain + 1e-12:
                best_c, best_gain = c, gain
        if best_gain < -1e-12 and sizes[old] > 0:     # alone is better than anywhere
            if not empty:
                empty.append(int(np.flatnonzero(sizes == 0)[0]))
            best_c = empty.pop()
        if sizes[old] == 0 and best_c != old:
            empty.append(old)
        comm[v] = best_c
        k[best_c] += dv
        sizes[best_c] += 1
        if best_c != old:
            moved = True
            for u in nb[comm[nb] != best_c].tolist():
                if not queued[u]:
                    queued[u] = True
                    queue.append(u)
    return moved


def _refine(g: _Level, comm: np.ndarray, gamma: float, theta: float,
            rng: np.random.Generator) -> np.ndarray:
    refined = np.arange(g.n)
    size = np.ones(g.n, dtype=int)
    k_r = g.deg.astype(float).copy()
    scale = gamma / g.two_m if g.two_m else 0.0
    members: dict[int, list[int]] = {}
    for v, c in enumerate(comm.tolist()):
        members.setdefault(c, []).append(v)
    for c, nodes in members.items():
        if len(nodes) == 1:
            continue
        k_c = float(g.deg[nodes].sum())
        inside = set(nodes)
        # weight from each node, and each refined community, to the rest of C
        ext = {}
        for v in nodes:
            nb, w = g.neighbours(v)
            ext[v] = float(sum(x for u, x in zip(nb.tolist(), w.tolist()) if u in inside))
        ext_r = {v: ext[v] for v in nodes}
        for v in rng.permutation(nodes).tolist():
            if size[refined[v]] != 1:
                continue
            dv = g.deg[v]
            if ext[v] < scale * dv * (k_c - dv):
                continue                                      # not well connected to C
            nb, w = g.neighbours(v)
            to = {}
            for u, x in zip(nb.tolist(), w.tolist()):
                if u in inside:
                    r = int(refined[u])
                    to[r] = to.get(r, 0.0) + x
            options, gains = [], []
            for r, wr in to.items():
                if r == refined[v]:
                    continue
                if ext_r[r] < scale * k_r[r] * (k_c - k_r[r]):
                    continue                                  # r not well connected to C
                gain = wr - scale * dv * k_r[r]
                if gain >= 0:
                    options.append(r)
                    gains.append(gain)
            if not options:
                continue
            gains_a = np.array(gains)
            p = np.exp((gains_a - gains_a.max()) / theta)
            target = options[int(rng.choice(len(options), p=p / p.sum()))]
            own = int(refined[v])
            w_vt = to[target]
            ext_r[target] = ext_r[target] + ext_r[own] - 2 * w_vt
            k_r[target] += k_r[own]
            size[target] += 1
            size[own] = 0
            k_r[own] = 0.0
            refined[v] = target
    return refined


def _relabel(x: np.ndarray) -> np.ndarray:
    _, inv = np.unique(x, return_inverse=True)
    return inv


def _one_pass(adj: sparse.csr_matrix, start: np.ndarray, gamma: float, theta: float,
              rng: np.random.Generator) -> tuple[np.ndarray, int]:
    g = _Level(adj)
    node_of = np.arange(g.n)                  # original node -> node of the current level
    comm = _relabel(start.copy())
    levels = 0
    while True:
        levels += 1
        _move_nodes(g, comm, gamma, rng)
        comm = _relabel(comm)
        if comm.max() + 1 == g.n:
            break
        refined = _relabel(_refine(g, comm, gamma, theta, rng))
        n_r = refined.max() + 1
        s = sparse.csr_matrix((np.ones(g.n), (np.arange(g.n), refined)), shape=(g.n, n_r))
        agg = (s.T @ g.adj @ s).tocsr()
        start_agg = np.zeros(n_r, dtype=int)
        start_agg[refined] = comm
        node_of = refined[node_of]
        g = _Level(agg)
        comm = _relabel(start_agg)
    return _relabel(comm[node_of]), levels


def leiden(adj: sparse.spmatrix, *, resolution: float = 1.0, theta: float = 0.01,
           seed: int = 0, max_iterations: int = 20) -> LeidenResult:
    a = sparse.csr_matrix(adj, dtype=float)
    if (abs(a - a.T) > 1e-10).nnz:
        raise ValueError("the graph must be undirected (a symmetric adjacency matrix)")
    rng = np.random.default_rng(seed)
    membership = np.arange(a.shape[0])
    best_q = -math.inf
    levels = 0
    iteration = 0
    for iteration in range(1, max_iterations + 1):
        new, levels = _one_pass(a, membership, resolution, theta, rng)
        q = modularity(a, new, resolution)
        if q <= best_q + 1e-12:
            break
        membership, best_q = new, q
    # order communities by size, largest first, so labels are stable and readable
    sizes = np.bincount(membership)
    order = np.argsort(-sizes, kind="mergesort")
    rank = np.empty_like(order)
    rank[order] = np.arange(len(order))
    membership = rank[membership]
    return LeidenResult(membership=membership, quality=modularity(a, membership, resolution),
                        iterations=iteration, levels=levels)
