"""Marker genes: each cluster against the rest, by the Wilcoxon rank-sum test.

As Scanpy's ``rank_genes_groups(method="wilcoxon")``: every gene's log-normalised values
are ranked over all cells (ties averaged), the rank sum of the cluster's cells gives the
normal-approximation z-score, p-values are two-sided and Benjamini–Hochberg adjusted
within the cluster, and the log2 fold change compares the de-logged means,
``log2((expm1(mean in) + 1e-9) / (expm1(mean out) + 1e-9))``. The tie correction is
applied (Scanpy's ``tie_correct=True``): with most values zero, ties are the rule in
single-cell data and the uncorrected variance overstates them.

Zeros are ranked as one tied block without being materialised, so the test runs on the
sparse matrix.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

import numpy as np
from scipy import sparse, special, stats

from ..deseq import benjamini_hochberg

__all__ = ["MarkerResult", "rank_genes_groups"]


@dataclass
class MarkerResult:
    groups: list[str]
    genes: np.ndarray
    scores: np.ndarray              # groups x genes, z
    pvals: np.ndarray
    pvals_adj: np.ndarray
    logfoldchanges: np.ndarray
    pct_in: np.ndarray
    pct_out: np.ndarray
    detail: dict[str, Any] = field(default_factory=dict)

    def top(self, group: str, n: int = 10, *, min_lfc: float = 0.25) -> list[dict[str, Any]]:
        g = self.groups.index(group)
        order = np.argsort(-self.scores[g], kind="mergesort")
        out = []
        for j in order:
            if len(out) >= n:
                break
            if self.logfoldchanges[g, j] < min_lfc:
                continue
            out.append({"gene": str(self.genes[j]), "score": float(self.scores[g, j]),
                        "log2_fold_change": float(self.logfoldchanges[g, j]),
                        "p_adjusted": float(self.pvals_adj[g, j]),
                        "pct_in": float(self.pct_in[g, j]),
                        "pct_out": float(self.pct_out[g, j])})
        return out


def rank_genes_groups(logx: sparse.csr_matrix, labels: Sequence[Any], genes: np.ndarray
                      ) -> MarkerResult:
    labels = np.asarray([str(v) for v in labels], dtype=object)
    groups = sorted(set(labels), key=lambda v: (len(v), v))
    gidx = np.array([groups.index(v) for v in labels])
    n_cells, n_genes = logx.shape
    n_groups = len(groups)
    sizes = np.bincount(gidx, minlength=n_groups).astype(float)
    csc = sparse.csc_matrix(logx)
    rank_sum = np.zeros((n_groups, n_genes))
    tie = np.ones(n_genes)
    sums = np.zeros((n_groups, n_genes))
    nonzero = np.zeros((n_groups, n_genes))
    total = float(n_cells)
    for j in range(n_genes):
        lo, hi = csc.indptr[j], csc.indptr[j + 1]
        rows, vals = csc.indices[lo:hi], csc.data[lo:hi]
        zeros = n_cells - len(vals)
        zero_rank = (zeros + 1) / 2.0
        in_group_nz = np.bincount(gidx[rows], minlength=n_groups)
        nonzero[:, j] = in_group_nz
        sums[:, j] = np.bincount(gidx[rows], weights=vals, minlength=n_groups)
        if len(vals):
            r = stats.rankdata(vals) + zeros
            rank_sum[:, j] = (np.bincount(gidx[rows], weights=r, minlength=n_groups)
                              + (sizes - in_group_nz) * zero_rank)
            _, t = np.unique(vals, return_counts=True)
            t = np.append(t, zeros).astype(float)
        else:
            rank_sum[:, j] = sizes * zero_rank
            t = np.array([float(zeros)])
        tie[j] = 1.0 - (t ** 3 - t).sum() / (total ** 3 - total) if total > 1 else 1.0
    rest = total - sizes
    expected = sizes[:, None] * (total + 1) / 2.0
    var = tie[None, :] * sizes[:, None] * rest[:, None] * (total + 1) / 12.0
    with np.errstate(divide="ignore", invalid="ignore"):
        z = np.where(var > 0, (rank_sum - expected) / np.sqrt(var), 0.0)
    z = np.nan_to_num(z)
    p = 2.0 * special.ndtr(-np.abs(z))
    padj = np.vstack([benjamini_hochberg(p[g]) for g in range(n_groups)])
    mean_in = sums / sizes[:, None]
    all_sums = sums.sum(axis=0)
    mean_out = (all_sums[None, :] - sums) / np.where(rest > 0, rest, 1.0)[:, None]
    lfc = np.log2((np.expm1(mean_in) + 1e-9) / (np.expm1(mean_out) + 1e-9))
    all_nz = nonzero.sum(axis=0)
    pct_in = nonzero / sizes[:, None]
    pct_out = (all_nz[None, :] - nonzero) / np.where(rest > 0, rest, 1.0)[:, None]
    return MarkerResult(groups=groups, genes=np.asarray(genes), scores=z, pvals=p,
                        pvals_adj=padj, logfoldchanges=lfc, pct_in=pct_in, pct_out=pct_out,
                        detail={"method": "wilcoxon", "tie_correct": True,
                                "cells": n_cells, "genes": n_genes})
