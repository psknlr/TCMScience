"""Spot quality control: per-spot metrics, filters with recorded thresholds, gene filter."""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np

from .io import SpatialSection

__all__ = ["QCParams", "spot_metrics", "apply_qc"]


@dataclass(frozen=True)
class QCParams:
    min_counts: int = 200
    min_genes: int = 100
    max_pct_mt: float = 30.0
    min_spots_per_gene: int = 10
    mt_prefixes: tuple[str, ...] = ("MT-", "mt-")


def spot_metrics(sec: SpatialSection, params: QCParams) -> dict[str, np.ndarray]:
    mt = np.array([any(str(g).startswith(p) for p in params.mt_prefixes)
                   for g in sec.gene_names])
    total = sec.counts.row_sums()
    genes = sec.counts.row_nnz()
    mt_counts = sec.counts.take_cols(np.flatnonzero(mt)).row_sums() if mt.any() else \
        np.zeros(sec.n_spots)
    with np.errstate(invalid="ignore", divide="ignore"):
        pct = np.where(total > 0, 100 * mt_counts / total, 0.0)
    return {"total_counts": total, "n_genes": genes, "pct_mt": pct,
            "n_mt_genes": np.array([int(mt.sum())])}


def _summary(x: np.ndarray) -> dict:
    if len(x) == 0:
        return {"n": 0}
    q = np.percentile(x, [0, 5, 50, 95, 100])
    return {"n": int(len(x)), "min": float(q[0]), "p5": float(q[1]), "median": float(q[2]),
            "p95": float(q[3]), "max": float(q[4])}


def apply_qc(sections: list[SpatialSection], params: QCParams
             ) -> tuple[list[SpatialSection], dict, dict[str, dict[str, np.ndarray]]]:
    """Filter spots per section, then genes on the pooled kept spots (same genes everywhere)."""
    report: dict = {"params": asdict(params), "sections": {}}
    kept, metrics = [], {}
    for sec in sections:
        m = spot_metrics(sec, params)
        keep = (m["total_counts"] >= params.min_counts) & (m["n_genes"] >= params.min_genes) \
            & (m["pct_mt"] <= params.max_pct_mt)
        reasons = {"low_counts": int((m["total_counts"] < params.min_counts).sum()),
                   "low_genes": int((m["n_genes"] < params.min_genes).sum()),
                   "high_mt": int((m["pct_mt"] > params.max_pct_mt).sum())}
        report["sections"][sec.section_id] = {
            "spots_in": sec.n_spots, "spots_kept": int(keep.sum()),
            "removed_by": reasons, "mt_genes_found": int(m["n_mt_genes"][0]),
            "total_counts": _summary(m["total_counts"]), "n_genes": _summary(m["n_genes"]),
            "pct_mt": _summary(m["pct_mt"])}
        if keep.sum() < 10:
            raise ValueError(f"section {sec.section_id}: {int(keep.sum())} spots pass QC; "
                             "check the thresholds or the input")
        metrics[sec.section_id] = {k: v[keep] for k, v in m.items() if k != "n_mt_genes"}
        kept.append(sec.subset(spots=np.flatnonzero(keep)))
    genes0 = kept[0].gene_ids
    for s in kept[1:]:
        if not np.array_equal(s.gene_ids, genes0):
            raise ValueError("sections do not share the same gene list; align features first")
    spots_per_gene = sum(s.counts.col_nnz() for s in kept)
    gkeep = np.flatnonzero(spots_per_gene >= params.min_spots_per_gene)
    report["genes_in"] = int(len(genes0))
    report["genes_kept"] = int(len(gkeep))
    return [s.subset(genes=gkeep) for s in kept], report, metrics
