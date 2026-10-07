"""Per-cell quality metrics and filtering.

The metrics are Scanpy's ``calculate_qc_metrics``: genes detected, total counts and the
percentage of counts from mitochondrial (``MT-``/``mt-``) and ribosomal (``RPS``/``RPL``)
genes. Filtering follows the single-cell best-practice recommendation (Heumos et al.
2023, *Nature Reviews Genetics* 24:550): a cell is an outlier when log1p total counts,
log1p genes detected or the top-20-gene share lies more than 5 median absolute
deviations from its sample's median, or when its mitochondrial share is more than 3 MADs
above the median *and* above a fixed ceiling. Thresholds are per sample, so one
library's depth does not set another's cut. Fixed floors (200 genes per cell, 3 cells
per gene) apply as well, as in Seurat's and Scanpy's tutorials.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
from scipy import sparse

from .io import CellMatrix

__all__ = ["QCSettings", "QCResult", "qc_metrics", "filter_cells"]


@dataclass(frozen=True)
class QCSettings:
    min_genes: int = 200
    min_cells: int = 3
    nmads: float = 5.0
    mito_nmads: float = 3.0
    max_mito_percent: float = 8.0       # a cell must also exceed this to be dropped
    hard_max_mito_percent: float = 50.0  # dropped above this whatever the MADs say

    def as_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def _mito(names: np.ndarray) -> np.ndarray:
    return np.array([str(n).upper().startswith("MT-") for n in names])


def _ribo(names: np.ndarray) -> np.ndarray:
    return np.array([str(n).upper().startswith(("RPS", "RPL")) for n in names])


def qc_metrics(m: CellMatrix) -> dict[str, np.ndarray]:
    x = m.counts
    total = np.asarray(x.sum(axis=1)).ravel()
    genes = np.diff(x.indptr)
    mito = np.asarray(x[:, _mito(m.gene_names)].sum(axis=1)).ravel()
    ribo = np.asarray(x[:, _ribo(m.gene_names)].sum(axis=1)).ravel()
    top = np.zeros(len(total))
    for i in range(x.shape[0]):
        row = x.data[x.indptr[i]:x.indptr[i + 1]]
        if len(row):
            top[i] = np.sort(row)[::-1][:20].sum()
    with np.errstate(divide="ignore", invalid="ignore"):
        pct = lambda a: np.where(total > 0, 100.0 * a / total, 0.0)   # noqa: E731
        return {"total_counts": total, "n_genes": genes.astype(float),
                "pct_mito": pct(mito), "pct_ribo": pct(ribo), "pct_top20": pct(top)}


def _mad_outlier(values: np.ndarray, nmads: float, *, upper_only: bool = False
                 ) -> np.ndarray:
    med = np.median(values)
    mad = np.median(np.abs(values - med))
    if mad == 0:
        return np.zeros(len(values), dtype=bool)
    high = values > med + nmads * mad
    return high if upper_only else high | (values < med - nmads * mad)


@dataclass
class QCResult:
    matrix: CellMatrix
    metrics: dict[str, np.ndarray]                     # for the kept cells
    removed: dict[str, int] = field(default_factory=dict)
    per_sample: dict[str, dict[str, Any]] = field(default_factory=dict)
    genes_removed: int = 0


def filter_cells(m: CellMatrix, settings: QCSettings = QCSettings()) -> QCResult:
    met = qc_metrics(m)
    n = m.shape[0]
    samples = m.obs.get("sample", np.array(["all"] * n, dtype=object))
    reasons = {"too_few_genes": met["n_genes"] < settings.min_genes,
               "count_outlier": np.zeros(n, dtype=bool),
               "high_mito": met["pct_mito"] > settings.hard_max_mito_percent}
    per_sample: dict[str, dict[str, Any]] = {}
    for s in dict.fromkeys(samples):
        idx = samples == s
        out = (_mad_outlier(np.log1p(met["total_counts"][idx]), settings.nmads)
               | _mad_outlier(np.log1p(met["n_genes"][idx]), settings.nmads)
               | _mad_outlier(met["pct_top20"][idx], settings.nmads))
        reasons["count_outlier"][idx] = out
        mito = (_mad_outlier(met["pct_mito"][idx], settings.mito_nmads, upper_only=True)
                & (met["pct_mito"][idx] > settings.max_mito_percent))
        reasons["high_mito"][idx] |= mito
        per_sample[str(s)] = {"cells_in": int(idx.sum()),
                              "median_genes": float(np.median(met["n_genes"][idx])),
                              "median_counts": float(np.median(met["total_counts"][idx])),
                              "median_pct_mito": float(np.median(met["pct_mito"][idx]))}
    drop = np.zeros(n, dtype=bool)
    removed = {}
    for name, flag in reasons.items():
        removed[name] = int((flag & ~drop).sum())
        drop |= flag
    kept = m.subset(cells=~drop)
    cells_per_gene = np.diff(sparse.csc_matrix(kept.counts).indptr)
    keep_genes = cells_per_gene >= settings.min_cells
    kept = kept.subset(genes=keep_genes)
    for s in per_sample:
        per_sample[s]["cells_out"] = int((kept.obs.get("sample", np.array(
            ["all"] * kept.shape[0], dtype=object)) == s).sum())
    return QCResult(matrix=kept, metrics={k: v[~drop] for k, v in met.items()},
                    removed=removed, per_sample=per_sample,
                    genes_removed=int((~keep_genes).sum()))
