"""Differential expression by a chosen implementation of the DESeq2 method.

    from bioagent.omics.de_backends import run_de
    res = run_de(counts, genes, sample_ids, metadata, design="~ batch + condition",
                 contrast=("condition", "treated", "control"), backend="pydeseq2")

There are two implementations of Love, Huber & Anders (2014): ``builtin``
(:mod:`.deseq`) and ``pydeseq2`` (PyDESeq2; Muzellec et al. 2023, *Bioinformatics*
39:btad547; MIT licence). Two implementations can disagree silently about what a fold
change means in several places. Each of those is settled here, once, before either
backend runs:

* **Orientation.** Counts are genes x samples throughout this package; PyDESeq2 takes
  samples x genes. The matrix is transposed once, with gene and sample IDs attached,
  and every result is read back by ID, never by position.
* **Sample alignment.** Metadata are matched to the count columns by sample ID and put
  in the columns' order. A repeated ID is refused, and so is a count column without
  metadata or metadata without a count column. Matching by position would pair one
  sample's counts with another sample's condition and raise no error.
* **Design.** One model matrix (:func:`.deseq.design_matrix`) is built and given to
  both backends. The contrast factor's reference level is the denominator. Any other
  factor's reference is its first level in sorted order. Numeric covariates are only
  those named, and they are centred. PyDESeq2 receives the matrix itself, not the
  formula, because its formula parser would choose reference levels itself and would
  decide which columns are numbers from their data types.
* **Contrast.** One numeric vector over the named columns: +1 on the numerator, -1 on
  the denominator. A log2 fold change above 0 means higher in the numerator, in both.
* **Result contract.** Both backends fill the same six columns, with DESeq2's meanings
  (:data:`CONTRACT`). What each estimated on the way (dispersions, Cook's flags,
  convergence, the trend) is kept apart in ``gene_diagnostics`` and ``diagnostics``.
  Those are each backend's own estimates and are not presented as interchangeable.
* **Procedure.** Size factors are median-of-ratios, or poscounts when every gene has
  a zero. PyDESeq2 left to itself would switch to its iterative estimator there. In
  both backends a Cook's outlier loses its p-value and is not replaced and refitted.
  The built-in implementation has no replacement step (DESeq2 replaces at 7 or more
  replicates per group). Letting PyDESeq2 replace would make it test different counts
  from the same input.
* **VST.** It is computed by the backend that ran, from that backend's normalised
  counts and its own dispersion trend fitted with the design (DESeq2's
  ``blind=FALSE``). One implementation's trend is never applied to the other's counts.
* **Availability.** A backend that cannot be imported is refused with the reason
  (:class:`.optional.BackendUnavailable`) and never replaced by the built-in one. The
  name and version that ran are in every result and in the pipelines' run records.
"""

from __future__ import annotations

import hashlib
import warnings
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from ..status import ExecutionStatus
from . import deseq
from .deseq import DESeqError, DesignMatrix
from .optional import BackendUnavailable, require, version

__all__ = ["BACKENDS", "CONTRACT", "GENE_DIAGNOSTICS", "DEResult", "align_samples",
           "backend_status", "check_backend", "run_de"]

BACKENDS = ("builtin", "pydeseq2")

#: The shared columns: DESeq2's (and PyDESeq2's) name, the attribute here, the meaning.
CONTRACT = (
    ("baseMean", "base_mean", "mean over all samples of the counts divided by the size "
                              "factors"),
    ("log2FoldChange", "log2_fold_change", "maximum-likelihood log2(numerator / "
                                           "denominator), not shrunken"),
    ("lfcSE", "lfc_se", "standard error of log2FoldChange"),
    ("stat", "stat", "Wald statistic, log2FoldChange / lfcSE"),
    ("pvalue", "p_value", "two-sided Wald p-value; missing for genes without counts and "
                          "for Cook's outliers"),
    ("padj", "p_adjusted", "Benjamini-Hochberg after independent filtering on baseMean; "
                           "missing for genes filtered out"),
)

#: Per-gene estimates each backend reports from its own fit (not part of the contract).
GENE_DIAGNOSTICS = ("dispersion_gene_wise", "dispersion_trend", "dispersion",
                    "cooks_outlier", "converged")


@dataclass
class DEResult:
    """One test of one contrast, with the same fields whichever backend produced it."""

    backend: str
    version: str
    genes: tuple[str, ...]
    samples: tuple[str, ...]
    contrast: tuple[str, str, str]
    design: str
    design_columns: tuple[str, ...]
    contrast_vector: tuple[float, ...]
    reference_levels: dict[str, str]
    base_mean: np.ndarray
    log2_fold_change: np.ndarray
    lfc_se: np.ndarray
    stat: np.ndarray
    p_value: np.ndarray
    p_adjusted: np.ndarray
    size_factors: np.ndarray
    alpha: float
    gene_diagnostics: dict[str, np.ndarray] = field(default_factory=dict)
    diagnostics: dict[str, Any] = field(default_factory=dict)
    vst: np.ndarray | None = None              # genes x samples, by the same backend
    vst_detail: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def table(self) -> list[dict[str, Any]]:
        """One row per gene, in input order, holding the contract columns; NaN is None."""
        def num(v: float) -> float | None:
            return float(v) if np.isfinite(v) else None
        return [{"gene": g, **{attr: num(getattr(self, attr)[i]) for _, attr, _ in CONTRACT}}
                for i, g in enumerate(self.genes)]

    def significant(self, alpha: float | None = None) -> list[int]:
        level = self.alpha if alpha is None else alpha
        return [i for i, q in enumerate(self.p_adjusted) if np.isfinite(q) and q < level]

    def summary(self) -> dict[str, Any]:
        sig = self.significant()
        up = sum(1 for i in sig if self.log2_fold_change[i] > 0)
        cooks = self.gene_diagnostics.get("cooks_outlier")
        return {"backend": self.backend, "version": self.version, "genes": len(self.genes),
                "tested": int(np.sum(np.isfinite(self.p_value))), "alpha": self.alpha,
                "significant": len(sig), "up": up, "down": len(sig) - up,
                "filtered_low_count": int(np.sum(np.isfinite(self.p_value)
                                                 & np.isnan(self.p_adjusted))),
                "cooks_outliers": int(cooks.sum()) if cooks is not None else None,
                "all_zero": int(np.sum(self.base_mean == 0)),
                "filter_threshold": self.diagnostics.get("filter_threshold"),
                "size_factors": dict(zip(self.samples, map(float, self.size_factors))),
                "design": self.design, "contrast": list(self.contrast),
                "notes": list(self.notes)}

    def record(self) -> dict[str, Any]:
        """What ran and how, for a run record."""
        return {"backend": self.backend, "version": self.version,
                "status": ExecutionStatus.SUCCEEDED.value, "design": self.design,
                "design_columns": list(self.design_columns),
                "reference_levels": dict(self.reference_levels),
                "contrast": list(self.contrast),
                "contrast_vector": list(self.contrast_vector),
                "samples": list(self.samples), "alpha": self.alpha,
                "contract": {name: attr for name, attr, _ in CONTRACT},
                "diagnostics": self.diagnostics, "vst": self.vst_detail,
                "notes": list(self.notes)}


# ============================================================== availability

def _builtin_version() -> str:
    """The built-in implementation is named by the digest of its source file."""
    digest = hashlib.sha256(Path(deseq.__file__).read_bytes()).hexdigest()
    return f"bioagent.omics.deseq sha256:{digest[:12]}"


def check_backend(name: str) -> str:
    """The version of backend ``name`` that would run; an unknown name or a backend
    that cannot be imported is refused."""
    if name not in BACKENDS:
        raise ValueError(f"de_backend is one of {', '.join(BACKENDS)}, not {name!r}")
    if name == "builtin":
        return _builtin_version()
    for module in ("pydeseq2.dds", "pydeseq2.ds", "pydeseq2.default_inference"):
        require(module, backend="pydeseq2", distribution="pydeseq2")
    return version("pydeseq2")


def backend_status(name: str) -> dict[str, Any]:
    """Whether backend ``name`` can run here, without running it."""
    try:
        found = check_backend(name)
    except BackendUnavailable as exc:
        return {"backend": name, "status": exc.status.value, "version": None,
                "reason": exc.reason}
    return {"backend": name, "status": ExecutionStatus.READY.value, "version": found,
            "reason": ""}


# ============================================================== inputs

def _no_repeats(ids: Sequence[str], where: str) -> None:
    repeated = sorted(k for k, n in Counter(ids).items() if n > 1)
    if repeated:
        raise DESeqError(f"{where} name a sample more than once: {', '.join(repeated)}")


def align_samples(sample_ids: Sequence[Any],
                  metadata: Mapping[Any, Mapping[str, Any]]
                  | Sequence[tuple[Any, Mapping[str, Any]]]) -> list[dict[str, Any]]:
    """The metadata rows in the order of ``sample_ids``, matched by ID.

    ``metadata`` maps each sample ID to its design variables, or lists (ID, row) pairs,
    whose IDs are then checked for repeats too. IDs are compared as strings. Refused:
    an ID repeated on either side, a count column without a row, a row without a count
    column.
    """
    ids = [str(s) for s in sample_ids]
    _no_repeats(ids, "the count columns")
    pairs = list(metadata.items()) if isinstance(metadata, Mapping) else list(metadata)
    names = [str(k) for k, _ in pairs]
    _no_repeats(names, "the metadata")
    rows = {str(k): v for k, v in pairs}
    missing = [s for s in ids if s not in rows]
    if missing:
        raise DESeqError(f"no metadata for sample(s) {', '.join(missing)}")
    known = set(ids)
    extra = [s for s in names if s not in known]
    if extra:
        raise DESeqError(f"metadata for sample(s) with no count column: {', '.join(extra)}")
    return [dict(rows[s]) for s in ids]


def _counts(counts: Any, genes: Sequence[str], ids: Sequence[str]) -> np.ndarray:
    y = np.asarray(counts, dtype=float)
    if y.ndim != 2:
        raise DESeqError("counts must be a genes x samples matrix")
    if y.shape != (len(genes), len(ids)):
        hint = ("; it is samples x genes, so transpose it"
                if y.shape == (len(ids), len(genes)) else "")
        raise DESeqError(f"counts are {y.shape[0]} x {y.shape[1]}, but there are "
                         f"{len(genes)} genes and {len(ids)} samples: counts are genes x "
                         f"samples, one column per sample ID{hint}")
    if not np.all(np.isfinite(y)) or np.any(y < 0):
        raise DESeqError("counts must be finite and non-negative")
    if np.any(np.abs(y - np.round(y)) > 1e-8):
        raise DESeqError("counts must be integers; round estimated counts first")
    if len(set(genes)) != len(genes):
        raise DESeqError("gene identifiers must be unique")
    if np.any(y.sum(axis=0) == 0):
        raise DESeqError("a sample has no counts at all")
    return np.round(y)


# ============================================================== the backends

def _run_builtin(y: np.ndarray, genes: tuple[str, ...], ids: tuple[str, ...],
                 rows: list[dict[str, Any]], dm: DesignMatrix,
                 contrast: tuple[str, str, str], *, alpha: float, cooks_cutoff: bool,
                 independent_filtering: bool, vst: bool, threads: int) -> dict[str, Any]:
    res = deseq.run_deseq(y, list(genes), rows, design=dm, contrast=contrast, alpha=alpha,
                          sample_names=list(ids), cooks_cutoff=cooks_cutoff,
                          independent_filtering=independent_filtering)
    trend = dict(res.trend)
    return {
        "base_mean": res.base_mean, "log2_fold_change": res.log2_fold_change,
        "lfc_se": res.lfc_se, "stat": res.stat, "p_value": res.p_value,
        "p_adjusted": res.p_adjusted, "size_factors": res.size_factors,
        "gene_diagnostics": {"dispersion_gene_wise": res.dispersion_gene,
                             "dispersion_trend": res.dispersion_trend,
                             "dispersion": res.dispersion,
                             "cooks_outlier": res.cooks_outlier,
                             "converged": res.converged},
        "diagnostics": {"size_factor_method": res.size_factor_method,
                        "dispersion_trend": trend,
                        "filter_threshold": (float(res.filter_threshold)
                                             if independent_filtering else None),
                        "not_converged": int(np.sum(~res.converged)),
                        "prior_dispersion_variance": float(res.prior_variance),
                        "settings": {"cooks_cutoff": cooks_cutoff,
                                     "independent_filtering": independent_filtering,
                                     "outlier_replacement": False}},
        "vst": deseq.vst(res.normalized, res.trend) if vst else None,
        "vst_detail": ({"computed_by": "builtin", "blind": False, "trend": trend}
                       if vst else {}),
        "notes": list(res.notes)}


def _trend(coefficients: Any) -> dict[str, Any]:
    return {"kind": "parametric", "asymptotic": float(coefficients["a0"]),
            "extra_poisson": float(coefficients["a1"])}


def _run_pydeseq2(y: np.ndarray, genes: tuple[str, ...], ids: tuple[str, ...],
                  rows: list[dict[str, Any]], dm: DesignMatrix,
                  contrast: tuple[str, str, str], *, alpha: float, cooks_cutoff: bool,
                  independent_filtering: bool, vst: bool, threads: int) -> dict[str, Any]:
    import pandas as pd
    dds_mod = require("pydeseq2.dds", backend="pydeseq2", distribution="pydeseq2")
    ds_mod = require("pydeseq2.ds", backend="pydeseq2", distribution="pydeseq2")
    inference_mod = require("pydeseq2.default_inference", backend="pydeseq2",
                            distribution="pydeseq2")
    sample_index, gene_index = pd.Index(ids), pd.Index(genes)
    # samples x genes, as PyDESeq2 (AnnData) holds them; IDs travel with the values
    counts = pd.DataFrame(y.T.astype(np.int64), index=sample_index, columns=gene_index)
    metadata = pd.DataFrame(rows, index=sample_index)
    design = pd.DataFrame(dm.matrix, index=sample_index, columns=list(dm.columns))
    vector = np.asarray(dm.contrast(*contrast), dtype=float)
    size_factor_method = "ratio" if np.any(np.all(y > 0, axis=1)) else "poscounts"
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        inference = inference_mod.DefaultInference(n_cpus=threads)
        dds = dds_mod.DeseqDataSet(counts=counts, metadata=metadata, design=design,
                                   refit_cooks=False, inference=inference,
                                   size_factors_fit_type=size_factor_method, quiet=True)
        if tuple(dds.obs_names) != ids or tuple(dds.var_names) != genes:
            raise DESeqError("PyDESeq2 holds the samples or genes in another order than "
                             "it was given them")
        dds.deseq2()
        stats = ds_mod.DeseqStats(dds, contrast=vector, alpha=alpha,
                                  cooks_filter=cooks_cutoff,
                                  independent_filter=independent_filtering,
                                  inference=inference, quiet=True)
        stats.summary()
        table = stats.results_df.loc[list(genes)]
        var = dds.var.loc[list(genes)]
        trend = (_trend(dds.uns["trend_coeffs"])
                 if dds.uns.get("disp_function_type") == "parametric"
                 else {"kind": "mean", "mean": float(dds.uns["mean_disp"])})
        out: dict[str, Any] = {
            "base_mean": table["baseMean"].to_numpy(float),
            "log2_fold_change": table["log2FoldChange"].to_numpy(float),
            "lfc_se": table["lfcSE"].to_numpy(float),
            "stat": table["stat"].to_numpy(float),
            "p_value": table["pvalue"].to_numpy(float),
            "p_adjusted": table["padj"].to_numpy(float),
            "size_factors": dds.obs["size_factors"].loc[list(ids)].to_numpy(float),
            "gene_diagnostics": {
                "dispersion_gene_wise": var["genewise_dispersions"].to_numpy(float),
                "dispersion_trend": var["fitted_dispersions"].to_numpy(float),
                "dispersion": var["dispersions"].to_numpy(float),
                "cooks_outlier": (var["_pvalue_cooks_outlier"].to_numpy(bool)
                                  if cooks_cutoff else np.zeros(len(genes), dtype=bool)),
                "converged": var["_LFC_converged"].fillna(True).to_numpy(bool)},
            "vst": None, "vst_detail": {}}
        out["diagnostics"] = {
            "size_factor_method": size_factor_method, "dispersion_trend": trend,
            "filter_threshold": None,
            "not_converged": int(np.sum(~out["gene_diagnostics"]["converged"])),
            "prior_dispersion_variance": float(dds.uns["prior_disp_var"]),
            "settings": {"refit_cooks": False, "cooks_filter": cooks_cutoff,
                         "independent_filter": independent_filtering,
                         "fit_type": "parametric", "n_cpus": threads,
                         "design": "the model matrix, not a formula"}}
        if vst:
            # the design-aware trend, refitted by PyDESeq2 for the transformation
            dds.vst(use_design=True)
            frame = pd.DataFrame(np.asarray(dds.layers["vst_counts"]),
                                 index=dds.obs_names, columns=dds.var_names)
            out["vst"] = frame.loc[list(ids), list(genes)].to_numpy(float).T
            out["vst_detail"] = {
                "computed_by": "pydeseq2", "blind": False,
                "trend": (_trend(dds.uns["vst_trend_coeffs"])
                          if dds.vst_fit_type == "parametric"
                          else {"kind": "mean", "mean": float(dds.uns["mean_disp"])})}
    out["diagnostics"]["warnings"] = sorted({f"{w.category.__name__}: {w.message}"[:300]
                                             for w in caught})
    out["notes"] = ["fold changes are maximum-likelihood estimates (no apeglm shrinkage)",
                    "Cook's outliers lose their p-value and are not replaced and refitted"]
    return out


# ============================================================== the entry point

def run_de(counts: Any, genes: Sequence[Any], sample_ids: Sequence[Any],
           metadata: Mapping[Any, Mapping[str, Any]] | Sequence[tuple[Any, Mapping[str, Any]]],
           *, design: str, contrast: tuple[str, str, str], backend: str = "builtin",
           alpha: float = 0.05, covariates: Sequence[str] | None = None,
           cooks_cutoff: bool = True, independent_filtering: bool = True,
           vst: bool = False, threads: int = 1) -> DEResult:
    """Test ``contrast = (factor, numerator, denominator)`` with ``backend``.

    ``counts`` is genes x samples, its columns named by ``sample_ids``; ``metadata``
    gives each sample's design variables by ID (:func:`align_samples`). ``covariates``
    names the numeric design terms, as :func:`.deseq.design_matrix` reads it. With
    ``vst`` the variance-stabilised matrix is returned too, from the same backend.
    ``threads`` bounds PyDESeq2's worker processes.
    """
    found = check_backend(backend)
    gene_ids = tuple(str(g) for g in genes)
    ids = tuple(str(s) for s in sample_ids)
    y = _counts(counts, gene_ids, ids)
    rows = align_samples(ids, metadata)
    factor, numerator, denominator = (str(c) for c in contrast)
    dm = deseq.design_matrix(rows, design, references={factor: denominator},
                             covariates=covariates)
    vector = dm.contrast(factor, numerator, denominator)
    runner = _run_builtin if backend == "builtin" else _run_pydeseq2
    fields = runner(y, gene_ids, ids, rows, dm, (factor, numerator, denominator),
                    alpha=alpha, cooks_cutoff=cooks_cutoff,
                    independent_filtering=independent_filtering, vst=vst,
                    threads=max(int(threads), 1))
    # Genes whose own dispersion estimate stayed at the floor are left out of the trend
    # and of the prior's spread. With few residual degrees of freedom the two
    # optimisers leave different numbers there, and their shrinkage parts from that.
    gene_wise = fields["gene_diagnostics"]["dispersion_gene_wise"]
    fields["diagnostics"]["gene_wise_at_floor"] = int(np.sum(gene_wise < 100 * deseq.MIN_DISP))
    return DEResult(backend=backend, version=found, genes=gene_ids, samples=ids,
                    contrast=(factor, numerator, denominator), design=dm.formula,
                    design_columns=dm.columns,
                    contrast_vector=tuple(float(v) for v in vector),
                    reference_levels={f: levels[0] for f, levels in dm.factors.items()},
                    alpha=alpha, **fields)
