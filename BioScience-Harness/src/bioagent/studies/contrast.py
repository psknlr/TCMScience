"""Whole formula against its main constituent(s): a direct contrast, never an indirect one.

The question is whether the formula does something its key monomer does not, on a
stated endpoint, in the same study. Three rules follow:

* **Direct comparison only.** "Formula vs control significant, monomer vs control not"
  says nothing about formula vs monomer. Groups must come from the same study (same
  model, batch, time point); groups from different studies are refused.
* **Equivalence needs a margin set in advance.** "No significant difference" is not
  equivalence. With a margin, two one-sided tests decide; without one, a non-significant
  difference is ``inconclusive``.
* **Residual is not synergy.** When single-constituent responses are available, the part
  of the formula's response they do not explain (on held-out units) is reported as
  ``unexplained``. It may come from unknown constituents, dose mismatch, preparation or a
  wrong combination model; calling it synergy needs a combination design
  (``studies.combination``).

Each verdict comes with the conclusions it permits and the ones it does not.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping, Sequence

import numpy as np

from .stats import t_ppf, tost, welch_difference

__all__ = ["Group", "contrast_formula_monomer", "unexplained_response", "VERDICTS"]

VERDICTS = ("formula_exceeds", "monomer_exceeds", "equivalent", "inconclusive")


@dataclass(frozen=True)
class Group:
    label: str                           # "formula", "monomer:<id>", "control", …
    values: tuple[float, ...]            # one per independent unit (animal, patient, well plate)
    study: str                           # same study for every group compared
    intervention: str = ""               # InterventionSpec fingerprint
    dose: str = ""


def _allowed(verdict: str, margin: float | None) -> tuple[list[str], list[str]]:
    never = ["that the formula is clinically superior or equivalent in people",
             "a mechanism for any difference",
             "that the difference is synergy among constituents"]
    if verdict == "formula_exceeds":
        return (["on this endpoint, in this model and at these doses, the formula's effect "
                 "exceeds the monomer's"], never)
    if verdict == "monomer_exceeds":
        return (["on this endpoint, in this model and at these doses, the monomer's effect "
                 "exceeds the formula's"], never)
    if verdict == "equivalent":
        return ([f"the difference lies within the prespecified margin ±{margin}: the monomer "
                 "accounts for the formula's effect on this endpoint, in this model"],
                never + ["that the formula adds nothing on other endpoints"])
    return (["the data cannot distinguish the formula from the monomer on this endpoint"],
            never + ["that the two are equivalent (no margin was met)",
                     "that the formula is better or worse"])


def contrast_formula_monomer(formula: Group, monomer: Group, *, higher_is_better: bool = True,
                             margin: float | None = None, alpha: float = 0.05,
                             dose_matched: bool | None = None) -> dict:
    """The direct formula-minus-monomer contrast, its interval, and a verdict.

    ``margin`` is the equivalence margin fixed before the analysis, in the endpoint's units.
    ``dose_matched`` records whether the monomer dose equals its amount in the formula dose
    (None: not known); a mismatch is reported because it can create or hide a difference.
    """
    if formula.study != monomer.study:
        raise ValueError(f"groups from different studies ({formula.study} vs {monomer.study})"
                         " are not a direct comparison")
    diff = welch_difference(formula.values, monomer.values, level=1 - alpha)
    sign = 1 if higher_is_better else -1
    lo, hi = sorted((sign * diff.ci_low, sign * diff.ci_high))
    equivalence = tost(diff, margin, alpha=alpha) if margin is not None else None
    if lo > 0:
        verdict = "formula_exceeds"
    elif hi < 0:
        verdict = "monomer_exceeds"
    elif equivalence and equivalence["equivalent"]:
        verdict = "equivalent"
    else:
        verdict = "inconclusive"
    allowed, not_allowed = _allowed(verdict, margin)
    notes = []
    if dose_matched is None:
        notes.append("whether the monomer dose matches its amount in the formula is not "
                     "recorded; a dose mismatch can create or hide a difference")
    elif not dose_matched:
        notes.append("the monomer dose differs from its amount in the formula")
    if diff.n_a < 5 or diff.n_b < 5:
        notes.append(f"few independent units ({diff.n_a} vs {diff.n_b}): the interval is wide "
                     "and an 'inconclusive' verdict is expected rather than informative")
    return {"endpoint_direction": "higher is better" if higher_is_better else "lower is better",
            "study": formula.study, "formula": formula.label, "monomer": monomer.label,
            "difference": diff.as_dict(), "equivalence": equivalence, "verdict": verdict,
            "may_conclude": allowed, "may_not_conclude": not_allowed, "notes": notes}


def unexplained_response(formula: Sequence[float],
                         constituents: Mapping[str, Sequence[float]],
                         control: Sequence[float], *, holdout: float = 0.5,
                         seed: int = 0, level: float = 0.95) -> dict:
    """How much of the formula's effect over control an additive constituent model leaves.

    Effects are differences from the control mean. The additive expectation (the sum of
    each constituent's mean effect) is estimated on one part of the units; the formula's
    effect is judged on the held-out part, so the residual is not fitted to the units it is
    judged on. The interval is a one-sample t interval over the held-out formula units and
    ignores the uncertainty of the expectation, so it is narrower than the truth.
    """
    rng = np.random.default_rng(seed)
    groups = {"control": np.asarray(control, float), "formula": np.asarray(formula, float),
              **{k: np.asarray(v, float) for k, v in constituents.items()}}
    if any(len(v) < 4 for v in groups.values()):
        raise ValueError("each group needs at least four units to hold part of them out")
    fit, test = {}, {}
    for name, x in groups.items():
        idx = rng.permutation(len(x))
        k = max(2, min(len(x) - 2, int(round(len(x) * (1 - holdout)))))
        fit[name], test[name] = x[idx[:k]], x[idx[k:]]
    expected = float(sum(fit[k].mean() - fit["control"].mean() for k in constituents))
    observed = test["formula"] - test["control"].mean()
    resid = observed - expected
    n = len(resid)
    se = float(resid.std(ddof=1) / math.sqrt(n))
    q = t_ppf(1 - (1 - level) / 2, n - 1)
    est = float(resid.mean())
    return {"additive_expectation": expected, "observed_effect_heldout": float(observed.mean()),
            "unexplained": {"estimate": est, "ci": [est - q * se, est + q * se],
                            "level": level, "n_heldout": n},
            "label": "unexplained response, not synergy: unknown constituents, dose "
                     "mismatch, preparation or a wrong additive model can all produce it"}
