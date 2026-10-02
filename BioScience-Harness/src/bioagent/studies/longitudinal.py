"""Repeated visits: pairing layers and predicting the next visit without leaking the future.

* ``match_layers``: which measured layers (metagenome, metatranscriptome, metabolome, …)
  come from the same collection, or from the same subject within a tolerance in days.
  "The same subjects" is not "the same sample": DNA and RNA from different stools cannot
  show that a gene present was expressed.
* ``visit_pairs``: consecutive visits of each subject whose gap falls in a window fixed in
  the validation plan. Visits outside the window are not stretched to fit.
* ``next_visit_prediction``: predict the next visit's outcome from this visit's features,
  split by subject, against the two baselines that usually win in longitudinal microbiome
  and activity data: *persistence* (the next visit looks like this one) and the training
  mean. A model is useful only if it beats persistence on people it has not seen.
"""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Mapping, Sequence

import numpy as np

from .contract import CohortManifest, Sample
from .validation import _cv_scores, _metric, grouped_folds

__all__ = ["match_layers", "visit_pairs", "next_visit_prediction"]


def _day(s: Sample, field: str) -> float | None:
    try:
        v = float(s.get(field))
    except ValueError:
        return None
    return None if math.isnan(v) else v


def match_layers(manifest: CohortManifest, layers: Sequence[str], *,
                 layer_field: str = "data_type", collection_field: str = "collection",
                 day_field: str = "day", tolerance_days: float | None = None) -> dict:
    """Counts of subjects and collections that have every layer in ``layers``.

    Same-collection matching uses ``collection_field``; with ``tolerance_days`` it also
    counts subject-level matches whose layers were collected within that many days.
    """
    coll = defaultdict(set)
    subj_layers = defaultdict(lambda: defaultdict(list))
    for s in manifest:
        layer = s.get(layer_field)
        if layer in layers:
            if s.get(collection_field):
                coll[(s.subject_id, s.get(collection_field))].add(layer)
            subj_layers[s.subject_id][layer].append(_day(s, day_field))
    want = set(layers)
    complete_coll = [k for k, v in coll.items() if want <= v]
    out = {"layers": list(layers),
           "subjects_with_any": len(subj_layers),
           "subjects_with_all_layers": sum(1 for v in subj_layers.values() if want <= set(v)),
           "collections_with_all_layers": len(complete_coll),
           "subjects_with_a_complete_collection": len({k[0] for k in complete_coll}),
           "per_layer_samples": {ly: sum(len(v.get(ly, [])) for v in subj_layers.values())
                                 for ly in layers}}
    if tolerance_days is not None:
        n = 0
        for v in subj_layers.values():
            if not want <= set(v):
                continue
            first, *rest = layers
            for d in v[first]:
                if d is None:
                    continue
                if all(any(e is not None and abs(e - d) <= tolerance_days for e in v[o])
                       for o in rest):
                    n += 1
        out[f"anchor_samples_matched_within_{tolerance_days:g}_days"] = n
    return out


def visit_pairs(manifest: CohortManifest, *, window: tuple[float, float],
                day_field: str = "day", where: Mapping[str, str] | None = None
                ) -> dict:
    """Consecutive same-subject visits whose gap lies in ``window`` (days, inclusive)."""
    lo, hi = window
    chosen = manifest.where(**where) if where else manifest
    by_subject = defaultdict(list)
    undated = 0
    for s in chosen:
        d = _day(s, day_field)
        if d is None:
            undated += 1
        else:
            by_subject[s.subject_id].append((d, s.sample_id))
    pairs, outside = [], 0
    for subj, visits in sorted(by_subject.items()):
        visits.sort()
        for (d0, a), (d1, b) in zip(visits, visits[1:]):
            if lo <= d1 - d0 <= hi:
                pairs.append({"subject": subj, "current": a, "next": b, "gap_days": d1 - d0})
            else:
                outside += 1
    return {"window": [lo, hi], "pairs": pairs, "undated_samples": undated,
            "consecutive_gaps_outside_window": outside,
            "subjects": len({p["subject"] for p in pairs})}


def next_visit_prediction(features: Mapping[str, Sequence[float]], outcome: Mapping[str, float],
                          pairs: Sequence[Mapping], *, task: str = "binary", k: int = 5,
                          include_current_outcome: bool = True, l2: float = 1.0,
                          n_boot: int = 1000, seed: int = 0) -> dict:
    """Features at visit t → outcome at t+1, split by subject, against persistence."""
    rows = [p for p in pairs if p["current"] in features and p["current"] in outcome
            and p["next"] in outcome]
    if len(rows) < 10:
        raise ValueError(f"{len(rows)} usable visit pairs; too few to cross-validate")
    X = np.array([features[p["current"]] for p in rows], float)
    y_now = np.array([outcome[p["current"]] for p in rows], float)
    y = np.array([outcome[p["next"]] for p in rows], float)
    if include_current_outcome:
        X = np.column_stack([X, y_now])
    groups = np.array([p["subject"] for p in rows])
    folds = grouped_folds(groups, k, seed=seed)
    score = _cv_scores(X, y, folds, task, None, l2, groups=groups)
    ok = ~np.isnan(score)
    # Persistence: the score is the current outcome (for a continuous outcome, its
    # prediction); for a binary outcome its AUC is that of the current state.
    persist = y_now
    model_m = _metric(task, y[ok], score[ok])
    pers_m = _metric(task, y[ok], persist[ok])
    rng = np.random.default_rng(seed)
    uniq = np.unique(groups[ok])
    idx_by = {u: np.flatnonzero((groups == u) & ok) for u in uniq}
    diffs = []
    for _ in range(n_boot):
        take = np.concatenate([idx_by[u] for u in rng.choice(uniq, len(uniq))])
        yy = y[take]
        if task == "binary" and len(set(yy)) < 2:
            continue
        diffs.append(_metric(task, yy, score[take]) - _metric(task, yy, persist[take]))
    diffs = np.array(diffs)
    ci = [float(np.nanpercentile(diffs, 2.5)), float(np.nanpercentile(diffs, 97.5))]
    verdict = ("beats persistence" if ci[0] > 0 else
               "worse than persistence" if ci[1] < 0 else "not distinguishable from "
                                                          "persistence")
    return {"metric": "auc" if task == "binary" else "r2", "pairs": int(ok.sum()),
            "subjects": int(len(uniq)), "model": model_m, "persistence": pers_m,
            "difference_ci95": ci, "verdict": verdict, "split": "by subject",
            "baseline_note": "persistence predicts the next visit from the current one; "
                             "a model that does not beat it adds no forecasting value"}

