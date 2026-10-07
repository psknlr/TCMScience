"""Trajectories: diffusion maps, diffusion pseudotime and PAGA.

* **Diffusion map** (Coifman & Lafon 2006; Haghverdi et al. 2015), as Scanpy's
  ``diffmap``: the connectivities are density-normalised (K = Q^-1 W Q^-1, Q the degrees),
  made a symmetric transition matrix D^-1/2 K D^-1/2, and its leading eigenvectors taken.
* **Diffusion pseudotime** (Haghverdi et al. 2016, *Nature Methods* 13:845): the distance
  from a root cell in the space of the diffusion components weighted by
  lambda / (1 - lambda), so every scale of the random walk counts, scaled to [0, 1].
  The root is the cell of the named starting cluster at the extreme of the first
  diffusion component, as Scanpy's tutorials choose it. Only the clusters PAGA joins to
  the root's (connectivity >= 0.05) are ordered; the rest get no pseudotime. Without a named root nothing
  is ordered: which cells come first is biology the data cannot decide.
* **PAGA** (Wolf et al. 2019, *Genome Biology* 20:59), as Scanpy's v1.2 connectivity:
  the edges between two clusters over the number expected if edges fell at random given
  the clusters' sizes and degrees, capped at 1, with the minimum spanning tree of the
  inverse connectivities as the coarse trajectory.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

import numpy as np
from scipy import sparse
from scipy.sparse.csgraph import minimum_spanning_tree
from scipy.sparse.linalg import eigsh

__all__ = ["DiffusionMap", "diffusion_map", "dpt", "paga", "Paga", "pseudotime_from",
           "connected_clusters"]


@dataclass
class DiffusionMap:
    eigenvalues: np.ndarray
    components: np.ndarray            # cells x components (right eigenvectors)


def diffusion_map(connectivities: sparse.spmatrix, n_comps: int = 15) -> DiffusionMap:
    w = sparse.csr_matrix(connectivities, dtype=np.float64)
    q = np.asarray(w.sum(axis=0)).ravel()
    q[q == 0] = 1.0
    k = sparse.diags(1.0 / q) @ w @ sparse.diags(1.0 / q)
    z = np.sqrt(np.asarray(k.sum(axis=0)).ravel())
    z[z == 0] = 1.0
    t_sym = sparse.diags(1.0 / z) @ k @ sparse.diags(1.0 / z)
    n = w.shape[0]
    comps = min(n_comps, n - 2)
    vals, vecs = eigsh(t_sym, k=comps, which="LM", v0=np.ones(n), tol=1e-8,
                       maxiter=n * 20)
    order = np.argsort(-vals)
    vals, vecs = vals[order], vecs[:, order]
    right = vecs / z[:, None]                                # right eigenvectors of T
    right /= np.linalg.norm(right, axis=0)
    for j in range(right.shape[1]):                          # stable signs
        if right[np.argmax(np.abs(right[:, j])), j] < 0:
            right[:, j] = -right[:, j]
    return DiffusionMap(eigenvalues=vals, components=right)


def dpt(dm: DiffusionMap, root: int, n_dcs: int = 10) -> np.ndarray:
    """Diffusion pseudotime from ``root``, in [0, 1]."""
    lam = dm.eigenvalues[1:n_dcs]
    psi = dm.components[:, 1:n_dcs]
    with np.errstate(divide="ignore"):
        weight = np.where(lam < 1 - 1e-12, lam / (1 - lam), 0.0)
    basis = psi * weight
    d = np.sqrt(((basis - basis[root]) ** 2).sum(axis=1))
    span = d.max()
    return d / span if span > 0 else d


def root_cell(dm: DiffusionMap, clusters: Sequence[Any], root_cluster: str) -> int:
    labels = np.asarray([str(c) for c in clusters])
    members = np.flatnonzero(labels == str(root_cluster))
    if not len(members):
        raise ValueError(f"no cluster {root_cluster!r} to start the trajectory from")
    dc1 = dm.components[:, 1]
    # the end of DC1 the root cluster lies towards
    centre = np.median(dc1[members])
    side = np.sign(centre - np.median(dc1)) or 1.0
    return int(members[np.argmax(side * dc1[members])])


def connected_clusters(p: "Paga", root_cluster: str, threshold: float = 0.05) -> list[str]:
    """Clusters joined to the root cluster through PAGA edges of at least ``threshold``."""
    seen, todo = {str(root_cluster)}, [str(root_cluster)]
    while todo:
        a = todo.pop()
        i = p.groups.index(a)
        for j, b in enumerate(p.groups):
            if b not in seen and p.connectivities[i, j] >= threshold:
                seen.add(b)
                todo.append(b)
    return sorted(seen, key=lambda v: (len(v), v))


def pseudotime_from(connectivities: sparse.spmatrix, clusters: Sequence[Any],
                    root_cluster: str, include: Sequence[str] | None = None,
                    n_comps: int = 15) -> tuple[np.ndarray, int, int]:
    """Diffusion pseudotime over the cells of ``include`` (default: every cluster) that
    are connected to the root cluster in the neighbour graph.

    A diffusion map over clusters that are barely joined has one near-stationary
    component per piece, and those dominate the weighted distance, so cells of another
    piece would get a pseudotime that only says "elsewhere". Cells outside the
    trajectory get NaN instead. Returns (pseudotime, root cell, cells ordered).
    """
    from scipy.sparse.csgraph import connected_components
    g = sparse.csr_matrix(connectivities)
    labels = np.asarray([str(c) for c in clusters])
    members = np.flatnonzero(labels == str(root_cluster))
    if not len(members):
        raise ValueError(f"no cluster {root_cluster!r} to start the trajectory from")
    allowed = np.isin(labels, list(include)) if include is not None else np.ones(len(labels), bool)
    pool = np.flatnonzero(allowed)
    _, comp = connected_components(g[pool][:, pool], directed=False)
    in_root = np.isin(pool, members)
    main = np.bincount(comp[in_root]).argmax()
    cells = pool[comp == main]
    sub = g[cells][:, cells]
    dm = diffusion_map(sub, n_comps=min(n_comps, len(cells) - 2))
    root_local = root_cell(dm, labels[cells], root_cluster)
    out = np.full(g.shape[0], np.nan)
    out[cells] = dpt(dm, root_local)
    return out, int(cells[root_local]), int(len(cells))


@dataclass
class Paga:
    groups: list[str]
    connectivities: np.ndarray        # groups x groups, in [0, 1]
    tree: list[tuple[str, str, float]]
    detail: dict[str, Any] = field(default_factory=dict)


def paga(adjacency: sparse.spmatrix, clusters: Sequence[Any]) -> Paga:
    labels = np.asarray([str(c) for c in clusters], dtype=object)
    groups = sorted(set(labels), key=lambda v: (len(v), v))
    gi = np.array([groups.index(v) for v in labels])
    a = sparse.triu(sparse.csr_matrix(adjacency), k=1).tocoo()
    keep = a.data > 0
    r, c = gi[a.row[keep]], gi[a.col[keep]]
    g = len(groups)
    inter = np.zeros((g, g))
    inner = np.zeros(g)
    same = r == c
    np.add.at(inner, r[same], 1.0)
    np.add.at(inter, (r[~same], c[~same]), 1.0)
    inter = inter + inter.T
    sizes = np.bincount(gi, minlength=g).astype(float)
    n = float(len(labels))
    edges = inner + inter.sum(axis=1)
    conn = np.zeros((g, g))
    for i in range(g):
        for j in range(g):
            if i == j or inter[i, j] == 0:
                continue
            expected = (edges[i] * sizes[j] + edges[j] * sizes[i]) / (n - 1)
            conn[i, j] = min(inter[i, j] / expected, 1.0) if expected else 1.0
    with np.errstate(divide="ignore"):
        inverse = np.where(conn > 0, 1.0 / np.maximum(conn, 1e-12), 0.0)
    mst = minimum_spanning_tree(sparse.csr_matrix(inverse)).tocoo()
    tree = sorted((groups[i], groups[j], round(float(conn[i, j]), 4))
                  for i, j in zip(mst.row, mst.col))
    return Paga(groups=groups, connectivities=conn, tree=tree,
                detail={"method": "PAGA v1.2 connectivity, capped at 1"})
