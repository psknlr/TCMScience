"""Cell-type annotation of clusters from marker genes.

Each cell type in a marker panel is scored per cell by Scanpy's ``score_genes`` (the
method of Satija et al. 2015): the mean log-normalised expression of the type's markers
minus that of control genes drawn from the same expression bins (25 bins, 50 controls
per marker), so a type is not favoured for having highly expressed markers. A cluster
takes the type with the highest median score when that median is above zero and leads
the runner-up by at least ``margin``; otherwise it is ``unassigned``. Its confidence is
the share of its cells whose own best-scoring type is the cluster's.

The default panel (``bioagent/data/cell_markers.json``) holds canonical markers for 30
common human cell types and states its sources. A panel can be supplied instead, as
JSON ``{"cell type": [genes]}`` or a CSV/TSV of ``cell_type,gene`` rows. Annotation by
markers is an inference from expression; the report lists each cluster's scores and
top markers so it can be checked.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
from scipy import sparse

__all__ = ["Annotation", "load_panel", "default_panel", "score_genes", "annotate_clusters"]

UNASSIGNED = "unassigned"


@dataclass
class Annotation:
    labels: dict[str, str]                       # cluster -> cell type
    confidence: dict[str, float]
    scores: dict[str, dict[str, float]]          # cluster -> type -> median score
    markers_used: dict[str, list[str]]           # type -> markers present in the data
    panel: dict[str, Any] = field(default_factory=dict)


def default_panel() -> dict[str, Any]:
    from importlib.resources import files
    text = (files("bioagent.data") / "cell_markers.json").read_text(encoding="utf-8")
    return json.loads(text)


def load_panel(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    if p.suffix.lower() == ".json":
        doc = json.loads(p.read_text(encoding="utf-8"))
        types = doc.get("cell_types", doc)
        return {"about": doc.get("about", f"supplied panel {p.name}"),
                "cell_types": {str(k): [str(g) for g in v] for k, v in types.items()},
                "source_file": str(p)}
    text = p.read_text(encoding="utf-8")
    dialect = "excel-tab" if text.split("\n", 1)[0].count("\t") else "excel"
    types: dict[str, list[str]] = {}
    for row in csv.reader(text.splitlines(), dialect=dialect):
        if len(row) < 2 or row[0].strip().lower() in ("cell_type", "celltype", "type"):
            continue
        types.setdefault(row[0].strip(), []).append(row[1].strip())
    if not types:
        raise ValueError(f"{p} lists no cell_type,gene rows")
    return {"about": f"supplied panel {p.name}", "cell_types": types, "source_file": str(p)}


def score_genes(logx: sparse.csr_matrix, gene_names: np.ndarray, markers: Sequence[str], *,
                ctrl_size: int = 50, n_bins: int = 25, seed: int = 0) -> np.ndarray | None:
    """Per-cell score of a gene set, or None when fewer than two markers are present."""
    rng = np.random.default_rng(seed)
    upper = {str(g).upper(): i for i, g in enumerate(gene_names)}
    idx = sorted({upper[m.upper()] for m in markers if m.upper() in upper})
    if len(idx) < 2:
        return None
    means = np.asarray(logx.mean(axis=0)).ravel()
    n_items = max(int(round(len(means) / (n_bins - 1))), 1)
    order = np.argsort(means, kind="mergesort")
    ranks = np.empty(len(means), dtype=int)
    ranks[order] = np.arange(len(means))
    cuts = ranks // n_items
    marker_set = set(idx)
    controls: set[int] = set()
    for b in np.unique(cuts[idx]):
        pool = [j for j in np.flatnonzero(cuts == b) if j not in marker_set]
        if pool:
            controls.update(rng.choice(pool, size=min(ctrl_size, len(pool)),
                                       replace=False).tolist())
    ctrl = sorted(controls)
    score = np.asarray(logx[:, idx].mean(axis=1)).ravel()
    if ctrl:
        score = score - np.asarray(logx[:, ctrl].mean(axis=1)).ravel()
    return score


def annotate_clusters(logx: sparse.csr_matrix, gene_names: np.ndarray,
                      clusters: Sequence[Any], panel: Mapping[str, Any] | None = None, *,
                      margin: float = 0.05, seed: int = 0) -> Annotation:
    panel = dict(panel or default_panel())
    types = panel["cell_types"]
    labels_arr = np.asarray([str(c) for c in clusters], dtype=object)
    cluster_ids = sorted(set(labels_arr), key=lambda v: (len(v), v))
    per_type: dict[str, np.ndarray] = {}
    used: dict[str, list[str]] = {}
    upper = {str(g).upper(): str(g) for g in gene_names}
    for k, (name, markers) in enumerate(types.items()):
        s = score_genes(logx, gene_names, markers, seed=seed + k)
        if s is not None:
            per_type[name] = s
            used[name] = [upper[m.upper()] for m in markers if m.upper() in upper]
    names = list(per_type)
    labels: dict[str, str] = {}
    confidence: dict[str, float] = {}
    scores: dict[str, dict[str, float]] = {}
    if not names:
        for c in cluster_ids:
            labels[c], confidence[c], scores[c] = UNASSIGNED, 0.0, {}
        return Annotation(labels, confidence, scores, used, panel)
    matrix = np.vstack([per_type[n] for n in names])        # types x cells
    best_cell = np.argmax(matrix, axis=0)
    for c in cluster_ids:
        mask = labels_arr == c
        med = np.median(matrix[:, mask], axis=1)
        scores[c] = {n: round(float(v), 4) for n, v in zip(names, med)}
        order = np.argsort(-med, kind="mergesort")
        top = order[0]
        second = med[order[1]] if len(order) > 1 else -np.inf
        if med[top] > 0 and med[top] - second >= margin:
            labels[c] = names[top]
            confidence[c] = round(float(np.mean(best_cell[mask] == top)), 4)
        else:
            labels[c] = UNASSIGNED
            confidence[c] = 0.0
    return Annotation(labels, confidence, scores, used, panel)
