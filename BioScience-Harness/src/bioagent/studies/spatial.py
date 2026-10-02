"""Spatial neighbourhoods, tested per patient.

Whether two cell types sit together more than chance is first judged *within each sample*:
cell-type labels are permuted over the sample's own positions, which keeps its tissue
architecture and cell numbers. Each sample then gives one log ratio (observed / expected
neighbour pairs). Only these per-subject values are compared between conditions, so a
single large section cannot dominate and cells are never treated as patients.

``detectable_genes`` gives the background for any gene-level analysis of a targeted
panel: the genes the panel measured and detected in enough cells, never the genome.
"""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Sequence

import numpy as np

from .contract import CohortManifest, ContrastSpec
from .stats import permutation_p, signflip_p, t_ppf, t_sf, welch_difference

__all__ = ["neighbour_pairs", "neighbourhood_enrichment", "compare_neighbourhoods",
           "detectable_genes"]


def neighbour_pairs(coords: np.ndarray, radius: float) -> np.ndarray:
    """Ordered pairs (i, j), i != j, closer than ``radius``; a grid keeps it near-linear."""
    xy = np.asarray(coords, float)
    if len(xy) == 0:
        return np.empty((0, 2), int)
    cell = np.floor(xy / radius).astype(np.int64)
    bins: dict[tuple[int, int], list[int]] = defaultdict(list)
    for i, (a, b) in enumerate(cell):
        bins[(int(a), int(b))].append(i)
    bins_np = {k: np.array(v) for k, v in bins.items()}
    out = []
    r2 = radius * radius
    for (a, b), idx in bins_np.items():
        for da in (-1, 0, 1):
            for db in (-1, 0, 1):
                other = bins_np.get((a + da, b + db))
                if other is None:
                    continue
                d = xy[idx, None, :] - xy[None, other, :]
                close = (d ** 2).sum(-1) < r2
                ii, jj = np.nonzero(close)
                pairs = np.column_stack([idx[ii], other[jj]])
                out.append(pairs[pairs[:, 0] != pairs[:, 1]])
    return np.concatenate(out) if out else np.empty((0, 2), int)


def neighbourhood_enrichment(coords: np.ndarray, labels: Sequence[str], type_a: str,
                             type_b: str, *, radius: float, n_perm: int = 1000,
                             seed: int = 0) -> dict:
    """Within one sample: how often a cell of ``type_a`` has a ``type_b`` neighbour,
    against labels permuted over the same positions."""
    lab = np.asarray(labels)
    pairs = neighbour_pairs(coords, radius)
    if (lab == type_a).sum() == 0 or (lab == type_b).sum() == 0:
        return {"observed": 0, "expected": math.nan, "log2_ratio": math.nan,
                "z": math.nan, "p_perm": math.nan, "n_pairs": len(pairs),
                "reason": "a cell type is absent from the sample"}
    i, j = pairs[:, 0], pairs[:, 1]

    def count(lab_):
        return int(np.sum((lab_[i] == type_a) & (lab_[j] == type_b)))

    obs = count(lab)
    rng = np.random.default_rng(seed)
    null = np.array([count(rng.permutation(lab)) for _ in range(n_perm)], float)
    exp = float(null.mean())
    sd = float(null.std(ddof=1)) if n_perm > 1 else math.nan
    p = float((1 + np.sum(np.abs(null - exp) >= abs(obs - exp) - 1e-12)) / (n_perm + 1))
    return {"observed": obs, "expected": exp,
            "log2_ratio": math.log2((obs + 0.5) / (exp + 0.5)),
            "z": (obs - exp) / sd if sd and sd > 0 else math.nan,
            "p_perm": p, "n_pairs": len(pairs),
            "n_a": int((lab == type_a).sum()), "n_b": int((lab == type_b).sum())}


def compare_neighbourhoods(per_sample: dict[str, dict], manifest: CohortManifest,
                           contrast: ContrastSpec, *, n_perm: int = 5000, seed: int = 0,
                           level: float = 0.95) -> dict:
    """Compare per-section log ratios between levels, one value per subject and level
    (sections averaged). Paired contrasts compare each subject with itself."""
    by_subject: dict[tuple[str, str], list[float]] = defaultdict(list)
    for lvl, ss in contrast.select(manifest).items():
        for s in ss:
            r = per_sample.get(s.sample_id)
            if r is not None and not math.isnan(r.get("log2_ratio", math.nan)):
                by_subject[(s.get(contrast.unit), lvl)].append(r["log2_ratio"])
    per = {key: float(np.mean(v)) for key, v in by_subject.items()}
    if contrast.paired:
        subj = sorted({u for u, lv in per if lv == contrast.case} &
                      {u for u, lv in per if lv == contrast.control})
        if len(subj) < 3:
            raise ValueError(f"{len(subj)} subjects have usable sections at both levels; "
                             "a paired comparison needs 3")
        d = np.array([per[(u, contrast.case)] - per[(u, contrast.control)] for u in subj])
        est = float(d.mean())
        se = float(d.std(ddof=1) / math.sqrt(len(d)))
        q = t_ppf(1 - (1 - level) / 2, len(d) - 1)
        perm = signflip_p(d, n_perm=n_perm, seed=seed)
        return {"contrast": contrast.id, "paired": True, "pairs": len(d),
                "difference_log2_ratio": est, "ci": [est - q * se, est + q * se],
                "p_t": 2 * t_sf(abs(est) / se, len(d) - 1) if se > 0 else
                (0.0 if est else 1.0),
                "p_perm": perm["p"], "min_attainable_p": perm["min_p"],
                "unit": contrast.unit}
    a = np.array([v for (_, lv), v in sorted(per.items()) if lv == contrast.case])
    b = np.array([v for (_, lv), v in sorted(per.items()) if lv == contrast.control])
    if min(len(a), len(b)) < 2:
        raise ValueError(f"subjects with a usable section per arm: {len(a)}/{len(b)}")
    w = welch_difference(a, b, level=level)
    perm = permutation_p(a, b, n_perm=n_perm, seed=seed)
    return {"contrast": contrast.id, "paired": False,
            "subjects": {contrast.case: len(a), contrast.control: len(b)},
            "difference_log2_ratio": w.estimate, "ci": [w.ci_low, w.ci_high],
            "p_welch": w.p_value, "p_perm": perm["p"], "min_attainable_p": perm["min_p"],
            "unit": contrast.unit}


def detectable_genes(counts: np.ndarray, genes: Sequence[str], *, min_fraction: float = 0.01,
                     min_count: int = 1) -> list[str]:
    """Genes detected (``>= min_count``) in at least ``min_fraction`` of cells."""
    x = np.asarray(counts)
    frac = (x >= min_count).mean(axis=0)
    return [g for g, f in zip(genes, frac) if f >= min_fraction]
