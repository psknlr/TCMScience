"""Spatial neighbour graphs built from tissue positions, one section at a time.

Edges are built only *within* a section. Two sections are separate pieces of tissue whose
pixel coordinates live in unrelated coordinate systems, so spots that happen to have
nearby numbers are not neighbours; joining sections needs a registration step this
module does not perform. ``assert_section_isolated`` checks the property directly.

Modes:

* ``grid``: the platform's array grid. Visium spots are hexagonal (array_col steps by
  two), giving 6 neighbours per ring; Visium HD bins are square, giving 4 (or 8).
* ``knn``: the ``k`` nearest spots by Euclidean distance in full-resolution pixels.
* ``auto`` (default): ``grid`` for sections with an array grid, ``knn`` otherwise.
* ``radius``: all spots closer than ``radius`` pixels.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .io import SpatialSection

__all__ = ["SpatialGraph", "build_graph", "assert_section_isolated"]

_HEX = ((0, 2), (0, -2), (1, 1), (1, -1), (-1, 1), (-1, -1))
_SQ4 = ((0, 1), (0, -1), (1, 0), (-1, 0))
_SQ8 = _SQ4 + ((1, 1), (1, -1), (-1, 1), (-1, -1))


@dataclass
class SpatialGraph:
    src: np.ndarray                  # directed edges, both directions present
    dst: np.ndarray
    n: int
    section: np.ndarray              # section index per node
    mode: str
    params: dict = field(default_factory=dict)

    @property
    def degree(self) -> np.ndarray:
        return np.bincount(self.src, minlength=self.n)

    def summary(self) -> dict:
        deg = self.degree
        return {"mode": self.mode, "params": self.params, "nodes": self.n,
                "undirected_edges": int(len(self.src) // 2),
                "mean_degree": float(deg.mean()) if self.n else 0.0,
                "isolated_nodes": int((deg == 0).sum()),
                "degree_counts": {int(k): int(v) for k, v in
                                  zip(*np.unique(deg, return_counts=True))}}


def _grid_edges(rc: np.ndarray, offsets, rings: int) -> tuple[np.ndarray, np.ndarray]:
    lookup = {(int(r), int(c)): i for i, (r, c) in enumerate(rc)}
    src, dst = [], []
    ring_offsets = set()
    frontier = {(0, 0)}
    seen = {(0, 0)}
    for _ in range(rings):
        nxt = set()
        for (a, b) in frontier:
            for dr, dc in offsets:
                o = (a + dr, b + dc)
                if o not in seen:
                    seen.add(o)
                    nxt.add(o)
        ring_offsets |= nxt
        frontier = nxt
    for i, (r, c) in enumerate(rc):
        for dr, dc in ring_offsets:
            j = lookup.get((int(r) + dr, int(c) + dc))
            if j is not None:
                src.append(i)
                dst.append(j)
    return np.array(src, np.int64), np.array(dst, np.int64)


def _knn_edges(xy: np.ndarray, k: int) -> tuple[np.ndarray, np.ndarray]:
    n = len(xy)
    k = min(k, n - 1)                    # never the spot itself
    if k < 1:
        return np.zeros(0, np.int64), np.zeros(0, np.int64)
    src, dst = [], []
    step = max(1, min(1024, 20_000_000 // max(n, 1)))   # bound memory to ~160 MB
    sq = (xy * xy).sum(1)
    for start in range(0, n, step):
        q = xy[start:start + step]
        d = np.maximum(sq[start:start + step, None] + sq[None] - 2 * q @ xy.T, 0.0)
        d[np.arange(len(q)), np.arange(start, start + len(q))] = np.inf
        nn = np.argpartition(d, k - 1, axis=1)[:, :k]
        src.append(np.repeat(np.arange(start, start + len(q)), nn.shape[1]))
        dst.append(nn.ravel())
    s, t = np.concatenate(src), np.concatenate(dst)
    # symmetrise: i~j if either is among the other's k nearest
    pairs = np.unique(np.concatenate([np.stack([s, t], 1), np.stack([t, s], 1)]), axis=0)
    return pairs[:, 0], pairs[:, 1]


def _radius_edges(xy: np.ndarray, radius: float) -> tuple[np.ndarray, np.ndarray]:
    cell = np.floor(xy / radius).astype(np.int64)
    bins: dict[tuple[int, int], list[int]] = {}
    for i, (a, b) in enumerate(cell):
        bins.setdefault((int(a), int(b)), []).append(i)
    arr = {k: np.array(v) for k, v in bins.items()}
    src, dst = [], []
    for (a, b), idx in arr.items():
        for da in (-1, 0, 1):
            for db in (-1, 0, 1):
                other = arr.get((a + da, b + db))
                if other is None:
                    continue
                d = ((xy[idx, None] - xy[None, other]) ** 2).sum(-1)
                ii, jj = np.nonzero(d < radius * radius)
                keep = idx[ii] != other[jj]
                src.append(idx[ii][keep])
                dst.append(other[jj][keep])
    if not src:
        return np.zeros(0, np.int64), np.zeros(0, np.int64)
    return np.concatenate(src), np.concatenate(dst)


def build_graph(sections: list[SpatialSection], *, mode: str = "auto", rings: int = 1,
                k: int = 6, radius: float | None = None, square_neighbours: int = 4
                ) -> SpatialGraph:
    srcs, dsts, sec_idx = [], [], []
    offset = 0
    for si, sec in enumerate(sections):
        sec_mode = mode
        if mode == "auto":                # the platform grid where there is one, else kNN
            sec_mode = "grid" if sec.array_rc is not None and sec.grid != "none" else "knn"
        if sec_mode == "grid":
            if sec.array_rc is None or sec.grid == "none":
                raise ValueError(f"section {sec.section_id} has no array grid; use mode='knn' "
                                 "or 'radius'")
            offs = _HEX if sec.grid == "visium_hex" else (_SQ8 if square_neighbours == 8
                                                          else _SQ4)
            s, t = _grid_edges(sec.array_rc, offs, rings)
        elif sec_mode == "knn":
            s, t = _knn_edges(sec.xy, k)
        elif sec_mode == "radius":
            if not radius or radius <= 0:
                raise ValueError("mode='radius' needs a positive radius in pixels")
            s, t = _radius_edges(sec.xy, radius)
        else:
            raise ValueError(f"unknown graph mode {mode!r}")
        srcs.append(s + offset)
        dsts.append(t + offset)
        sec_idx.append(np.full(sec.n_spots, si))
        offset += sec.n_spots
    g = SpatialGraph(np.concatenate(srcs), np.concatenate(dsts), offset,
                     np.concatenate(sec_idx), mode,
                     {"rings": rings, "k": k, "radius": radius,
                      "square_neighbours": square_neighbours})
    assert_section_isolated(g)
    return g


def assert_section_isolated(g: SpatialGraph) -> None:
    cross = int((g.section[g.src] != g.section[g.dst]).sum())
    if cross:
        raise AssertionError(f"{cross} edges join different sections; sections are separate "
                             "tissue and are only joined after registration")
