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

import json
from collections import Counter
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
    replicate_key: str = "sample"
    design: str = ""
    unit_metadata: dict = field(default_factory=dict)


def _batch_balanced(rows: Sequence[Mapping[str, Any]], contrast: tuple[str, str, str]) -> bool:
    """Whether both groups have the same proportions of independent units in each batch."""
    factor, num, den = contrast
    a = Counter(r["batch"] for r in rows if r[factor] == num)
    b = Counter(r["batch"] for r in rows if r[factor] == den)
    return bool(a and b) and all(a[k] * b.total() == b[k] * a.total() for k in a.keys() | b)


def experimental_units(obs: Mapping[str, np.ndarray], contrast: tuple[str, str, str],
                       replicate_key: str, design: str | None
                       ) -> tuple[np.ndarray, dict, str, str]:
    """Collapse technical libraries to one column per independent unit and contrast level.

    Cell-level metadata supplies the independent unit even in a multiplexed input.
    A repeated unit across conditions requires a paired design. Technical libraries in
    the same unit/condition must agree on the covariates the model will use.
    """
    factor, num, den = contrast
    unit = ("donor" if "donor" in obs else "sample") if replicate_key == "auto" else replicate_key
    if unit not in obs or factor not in obs:
        raise DESeqError(f"pseudobulk needs columns {unit!r} and {factor!r} in the metadata")
    ids, levels = obs[unit], obs[factor]
    if any(not str(v).strip() for v in ids) or any(not str(v).strip() for v in levels):
        raise DESeqError("a cell is missing its biological unit or condition")
    subjects = {str(v) for v in ids[levels == num]} & {str(v) for v in ids[levels == den]}
    paired = bool(subjects)
    mixed = False
    terms = []
    if paired:
        terms.append(unit)
    conditional_batch = "batch" in obs and factor != "batch" and unit != "batch"
    if conditional_batch:
        batches = {str(v) for v in obs["batch"]}
        if len(batches) > 1:
            # A subject fixed effect already absorbs a batch constant within each subject.
            mixed = len(set(zip(ids, obs["batch"]))) > len(set(ids))
            eligible = set((str(subject), str(level), str(batch))
                           for subject, level, batch in zip(ids, levels, obs["batch"])
                           if level in (num, den))
            rows = [{factor: level, "batch": batch} for _, level, batch in eligible]
            if (not paired or mixed) and not _batch_balanced(rows, contrast):
                terms.append("batch")
    formula = design or "~ " + " + ".join([*terms, factor])
    model_terms = {s.strip() for s in formula.removeprefix("~").split("+")}
    if factor not in model_terms or (paired and unit not in model_terms):
        raise DESeqError(f"pseudobulk design must include {factor!r}"
                         + (f" and the paired unit {unit!r}" if paired else ""))
    missing = model_terms.difference(obs)
    if missing:
        raise DESeqError(f"missing pseudobulk design column(s): {', '.join(sorted(missing))}")
    columns = []
    metadata = {}
    for i, (subject, level) in enumerate(zip(ids, levels)):
        name = (str(subject) if not paired else
                json.dumps([str(subject), str(level)], ensure_ascii=False))
        columns.append(name)
        if level not in (num, den):
            continue
        attrs = {k: str(obs[k][i]) for k in model_terms}
        if conditional_batch and (not paired or mixed):
            attrs["batch"] = str(obs["batch"][i])
        if name in metadata and metadata[name] != attrs:
            raise DESeqError(f"technical libraries of unit {subject!r} in {level!r} disagree "
                             "on design variables")
        metadata[name] = attrs
    if num not in set(levels) or den not in set(levels) or num == den:
        raise DESeqError(f"contrast levels must be distinct and present in {factor!r}")
    return np.asarray(columns, dtype=object), metadata, formula, unit


def pseudobulk_de(counts: sparse.csr_matrix, genes: Sequence[str], groups: Sequence[Any],
                  samples: Sequence[Any], sample_info: Mapping[str, Mapping[str, Any]], *,
                  design: str, contrast: tuple[str, str, str], min_cells: int = 10,
                  min_samples: int = 2, alpha: float = 0.05,
                  de_backend: str = "builtin",
                  covariates: Sequence[str] = (), adjust_batch: bool = False) -> PseudobulkResult:
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
        model = design
        terms = {t.strip() for t in design.removeprefix("~").split("+")}
        records = [sample_info[s] for s in kept]
        if all("batch" in r for r in records) and factor != "batch":
            batch_sets = [{r["batch"] for r in records if r[factor] == level}
                          for level in (num, den)]
            if not batch_sets[0].intersection(batch_sets[1]):
                skipped[g] = "condition and batch are completely confounded in retained units"
                continue
            if adjust_batch and "batch" not in terms and not _batch_balanced(records, contrast):
                model = design + " + batch"
        try:
            results[g] = run_de(np.column_stack([cols[i] for i in keep]), list(genes), kept,
                                {s: sample_info[s] for s in kept}, design=model,
                                contrast=contrast, backend=de_backend, alpha=alpha,
                                covariates=covariates)
        except DESeqError as exc:
            skipped[g] = str(exc)
    return PseudobulkResult(results=results, skipped=skipped, samples_per_group=per_group,
                            backend=de_backend, version=found)
