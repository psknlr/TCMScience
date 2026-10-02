"""Spatial statistics on tissue coordinates, as small native tools.

The full processed-data workflow is ``bioagent.spatial.pipeline.run_spatial`` (run in its
own interpreter by ``bioagent.spatial.runner.run_isolated``). These tools expose its
building blocks on in-memory inputs, for agents and for checks: a neighbour graph from
coordinates, Moran's I of a value over that graph, and neighbourhood enrichment of labels.
Coordinates are tissue positions; an embedding (UMAP, t-SNE) is not a tissue position.
"""

from __future__ import annotations

from typing import Any, Sequence

import numpy as np

from ..spatial.graph import SpatialGraph
from ..spatial.statistics import morans_i, neighbourhood_enrichment

__all__ = ["spatial_neighbors", "spatial_morans_i", "spatial_neighborhood_enrichment"]


def _graph(coords: Sequence[Sequence[float]], k: int, radius: float | None,
           sections: Sequence[str] | None) -> SpatialGraph:
    from ..spatial.graph import _knn_edges, _radius_edges
    xy = np.asarray(coords, float)
    if xy.ndim != 2 or xy.shape[1] != 2 or len(xy) < 3:
        raise ValueError("coords is a list of at least three [x, y] tissue positions")
    sec = np.asarray(sections if sections is not None else ["s"] * len(xy)).astype(str)
    if len(sec) != len(xy):
        raise ValueError("one section label per coordinate")
    srcs, dsts, idx = [], [], np.zeros(len(xy), int)
    for i, s in enumerate(np.unique(sec)):
        rows = np.flatnonzero(sec == s)
        idx[rows] = i
        if len(rows) < 2:
            continue
        a, b = (_radius_edges(xy[rows], radius) if radius else
                _knn_edges(xy[rows], min(k, len(rows) - 1)))
        srcs.append(rows[a])
        dsts.append(rows[b])
    src = np.concatenate(srcs) if srcs else np.zeros(0, int)
    dst = np.concatenate(dsts) if dsts else np.zeros(0, int)
    return SpatialGraph(src, dst, len(xy), idx, "radius" if radius else "knn",
                        {"k": k, "radius": radius})


def spatial_neighbors(coords: Sequence[Sequence[float]], k: int = 6,
                      radius: float | None = None,
                      sections: Sequence[str] | None = None) -> dict[str, Any]:
    """Spatial neighbour graph from tissue coordinates (kNN or radius), never across sections."""
    g = _graph(coords, k, radius, sections)
    pairs = sorted({(int(a), int(b)) for a, b in zip(g.src, g.dst) if a < b})
    return {"n": g.n, "edges": [list(p) for p in pairs], "summary": g.summary()}


def spatial_morans_i(coords: Sequence[Sequence[float]], values: Sequence[float], k: int = 6,
                     radius: float | None = None, sections: Sequence[str] | None = None,
                     transformation: str = "r") -> dict[str, Any]:
    """Moran's I of one value over a tissue neighbour graph (upper-tail normal p)."""
    g = _graph(coords, k, radius, sections)
    r = morans_i(np.asarray(values, float), g, transformation=transformation)
    return {"I": float(r["I"][0]), "expected": float(r["expected"][0]),
            "z": float(r["z"][0]), "p_norm": float(r["p_norm"][0]),
            "n": g.n, "weights": "row-standardised" if transformation == "r" else "binary"}


def spatial_neighborhood_enrichment(coords: Sequence[Sequence[float]],
                                    labels: Sequence[str], k: int = 6,
                                    radius: float | None = None,
                                    sections: Sequence[str] | None = None,
                                    n_perms: int = 200, seed: int = 0) -> dict[str, Any]:
    """Label × label neighbour counts against labels permuted within each section (z)."""
    g = _graph(coords, k, radius, sections)
    r = neighbourhood_enrichment(np.asarray(labels).astype(str), g, n_perms=n_perms,
                                 seed=seed)
    labs = r["labels"]
    return {"labels": labs, "n_perms": n_perms,
            "z": [[None if not np.isfinite(v) else round(float(v), 6) for v in row]
                  for row in r["z"]],
            "observed": r["observed"].astype(int).tolist()}
