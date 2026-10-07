"""Differential expression between conditions, per cell type, by pseudobulk.

Cells are not independent replicates: cells from one donor share that donor's state, so
testing cells against cells counts one sample many times and finds hundreds of false
differences (Squair et al. 2021, *Nature Communications* 12:5692). The counts of each
cell type are therefore summed per sample, and the samples, which are the replicates,
are compared with the DESeq2 method. A cell type is tested only where every compared
condition has at least ``min_samples`` samples with at least ``min_cells`` cells of it.

The test runs through :func:`..de_backends.run_de`, by the built-in implementation or by
PyDESeq2 (``de_backend``). Each pseudobulk column is named by its sample, and that
sample's variables are matched to it by name, so the two implementations see the same
design and the same contrast. The backend is checked before the first cell type, so an
unavailable one is refused rather than replaced.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

import numpy as np
from scipy import sparse

from ..de_backends import DEResult, check_backend, run_de
from ..deseq import DESeqError

__all__ = ["PseudobulkResult", "pseudobulk_de"]


@dataclass
class PseudobulkResult:
    results: dict[str, DEResult]
    skipped: dict[str, str]
    samples_per_group: dict[str, dict[str, int]] = field(default_factory=dict)
    backend: str = "builtin"
    version: str = ""


def pseudobulk_de(counts: sparse.csr_matrix, genes: Sequence[str], groups: Sequence[Any],
                  samples: Sequence[Any], sample_info: Mapping[str, Mapping[str, Any]], *,
                  design: str, contrast: tuple[str, str, str], min_cells: int = 10,
                  min_samples: int = 2, alpha: float = 0.05,
                  de_backend: str = "builtin") -> PseudobulkResult:
    found = check_backend(de_backend)
    groups = np.asarray([str(g) for g in groups], dtype=object)
    samples = np.asarray([str(s) for s in samples], dtype=object)
    factor, num, den = contrast
    results: dict[str, DEResult] = {}
    skipped: dict[str, str] = {}
    per_group: dict[str, dict[str, int]] = {}
    for g in sorted(set(groups)):
        in_g = groups == g
        names, cols = [], []
        for s in sorted(set(samples[in_g])):
            mask = in_g & (samples == s)
            if mask.sum() < min_cells:
                continue
            names.append(s)
            cols.append(np.asarray(counts[mask].sum(axis=0)).ravel())
        levels = [str(sample_info[s].get(factor, "")) for s in names]
        per_group[g] = {lv: levels.count(lv) for lv in (num, den)}
        if min(per_group[g].values()) < min_samples:
            skipped[g] = (f"needs {min_samples} samples per condition with {min_cells}+ "
                          f"cells; has {per_group[g]}")
            continue
        keep = [i for i, lv in enumerate(levels) if lv in (num, den)]
        kept = [names[i] for i in keep]
        try:
            results[g] = run_de(np.column_stack([cols[i] for i in keep]), list(genes), kept,
                                {s: sample_info[s] for s in kept}, design=design,
                                contrast=contrast, backend=de_backend, alpha=alpha,
                                covariates=())
        except DESeqError as exc:
            skipped[g] = str(exc)
    return PseudobulkResult(results=results, skipped=skipped, samples_per_group=per_group,
                            backend=de_backend, version=found)
