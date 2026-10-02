"""Does a baseline characteristic modify the treatment effect? Not: who improved.

"Patients who improved on the formula had feature Z" can be a prognostic factor that
predicts improvement in either arm. Effect modification is the treatment × Z interaction,
estimated with both arms:

    Y_follow-up = β0 + β1·T + β2·Z + β3·T·Z + β4·Y_baseline + …

Rules enforced:

* only features measured before treatment (``baseline=True``) may be modifiers; a
  post-treatment feature can be an outcome or a mediator, never a modifier;
* the number of modifiers tested is fixed in advance and the interaction p values are
  adjusted (Benjamini-Hochberg) across them;
* a single arm cannot answer the question, and the analysis refuses;
* without syndrome (证候) data collected as such, a cluster of baseline features is not
  named after a syndrome.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from .stats import bh, ols

__all__ = ["Feature", "effect_modification"]


@dataclass(frozen=True)
class Feature:
    name: str
    values: tuple[float, ...]
    baseline: bool                       # measured before the intervention


def effect_modification(outcome: Sequence[float], treated: Sequence[int],
                        modifiers: Sequence[Feature], *,
                        baseline_outcome: Sequence[float] | None = None,
                        covariates: Mapping[str, Sequence[float]] | None = None,
                        prespecified: int | None = None, level: float = 0.95) -> dict:
    n = len(outcome)
    if any(t not in (0, 1) for t in treated):
        raise ValueError("treatment is coded 0 (control) or 1 (treated), nothing else")
    arms = set(int(t) for t in treated)
    if arms != {0, 1}:
        raise ValueError("effect modification needs a treated and a control arm; one arm "
                         "can only show who improved, not who benefited more")
    late = [f.name for f in modifiers if not f.baseline]
    if late:
        raise ValueError(f"{late} were not measured at baseline: a post-treatment feature "
                         "cannot modify the effect that produced it")
    if prespecified is not None and len(modifiers) > prespecified:
        raise ValueError(f"{len(modifiers)} modifiers tested, {prespecified} prespecified")
    results = []
    for f in modifiers:
        if len(f.values) != n:
            raise ValueError(f"{f.name}: {len(f.values)} values for {n} participants")
        mean = sum(f.values) / n
        z = [v - mean for v in f.values]          # centred, so β1 is the effect at the mean
        cols = {"treated": list(map(float, treated)), f.name: z,
                f"treated:{f.name}": [t * v for t, v in zip(treated, z)]}
        if baseline_outcome is not None:
            cols["baseline_outcome"] = list(baseline_outcome)
        for k, v in (covariates or {}).items():
            cols[k] = list(v)
        fit = ols(outcome, cols)
        results.append({"modifier": f.name,
                        "interaction": fit.term(f"treated:{f.name}", level=level),
                        "treatment_effect_at_mean": fit.term("treated", level=level),
                        "n": fit.n})
    adjusted = bh([r["interaction"]["p_value"] for r in results])
    for r, q in zip(results, adjusted):
        r["interaction"]["q_value"] = q
    return {"modifiers": results, "tested": len(results),
            "interpretation": ("a non-zero interaction says the treatment effect differs "
                               "with the baseline feature in this trial; it does not make the "
                               "feature a mechanism, and a feature cluster is not a 证候 "
                               "unless syndromes were assessed as such")}
