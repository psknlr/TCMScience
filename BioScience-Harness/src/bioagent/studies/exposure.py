"""Could an activity measured in vitro happen at the exposure reached at the site of action?

For one molecule, one site and one assay endpoint, the screening quantity is

    R = free concentration at the site / concentration at which the assay showed the effect

reported as a range, with the verdict ``plausible`` (the whole range ≥ 1), ``implausible``
(the whole range < ``implausible_below``), ``depends_on_unknowns`` (the range straddles),
or ``undeterminable``. It is a screen for mechanisms worth testing, not target occupancy
and not a probability of efficacy. The rules:

* molecule (parent vs metabolite) and species must match between exposure and assay;
* concentrations are converted to molar only from a stated unit (mass units need the
  molecular weight); an AUC is never divided by a potency;
* without a measured free fraction, the free concentration is a range over the
  ``unknown_free_fraction`` bounds — never an assumed 1;
* plasma is not tissue: an assay judged against a tissue or gut-lumen site needs an
  exposure measured there;
* IC50, EC50, Ki and Kd are kept apart; a censored exposure (below the limit of
  detection) gives an upper bound only.
"""

from __future__ import annotations

import re
from typing import Iterable

from .design import AssayResult, ExposureRecord

__all__ = ["to_molar", "exposure_ratio", "screen_exposure"]

_MOLAR = {"M": 1.0, "mM": 1e-3, "uM": 1e-6, "µM": 1e-6, "μM": 1e-6, "nM": 1e-9, "pM": 1e-12}
_MASS = {"g/L": 1.0, "mg/L": 1e-3, "ug/mL": 1e-3, "µg/mL": 1e-3, "μg/mL": 1e-3,
         "ng/mL": 1e-6, "ug/L": 1e-6, "µg/L": 1e-6, "pg/mL": 1e-9}
_TIME_INTEGRATED = re.compile(r"[*·.\s]\s*(h|hr|min)\b|\b(h|hr|min)\s*/|h\*|·h")


def to_molar(value: float, unit: str, *, mw: float | None = None) -> float:
    u = unit.strip()
    if _TIME_INTEGRATED.search(u):
        raise ValueError(f"{unit!r} is time-integrated (an AUC), not a concentration")
    if u in _MOLAR:
        return value * _MOLAR[u]
    if u in _MASS:
        if not mw:
            raise ValueError(f"{unit!r} is a mass concentration: the molecular weight is needed")
        return value * _MASS[u] / mw
    raise ValueError(f"unit {unit!r} is not a concentration unit this converts")


def exposure_ratio(exposure: ExposureRecord, assay: AssayResult, *,
                   mw: float | None = None,
                   unknown_free_fraction: tuple[float, float] = (0.01, 1.0),
                   implausible_below: float = 0.1) -> dict:
    out = {"molecule": exposure.molecule, "site": exposure.site, "endpoint": assay.endpoint,
           "target": assay.target, "exposure_study": exposure.study,
           "assay_study": assay.study}

    def undeterminable(why: str) -> dict:
        return {**out, "verdict": "undeterminable", "reason": why, "ratio": None}

    if exposure.molecule != assay.molecule:
        return undeterminable(f"exposure is of {exposure.molecule}, the assay of "
                              f"{assay.molecule} (parent and metabolite are different molecules)")
    if exposure.species and assay.species and exposure.species != assay.species:
        out["species_note"] = (f"exposure in {exposure.species}, assay on {assay.species} "
                               "protein: potency may differ across species")
    if assay.endpoint not in ("IC50", "EC50", "AC50", "Ki", "Kd"):
        return undeterminable(f"{assay.endpoint} is not a potency the ratio is defined for")
    if assay.result.qualifier == "inactive":
        return {**out, "verdict": "inactive_in_assay", "ratio": None,
                "reason": "the assay found no activity; exposure is moot for this endpoint"}
    if assay.result.qualifier == "above_max_tested":
        potency_lo = assay.result.bound
        potency_censored = True
    elif assay.result.known:
        potency_lo = assay.result.value
        potency_censored = False
    else:
        return undeterminable(f"assay result is {assay.result.qualifier}")
    try:
        potency = to_molar(potency_lo, assay.result.unit, mw=mw)
    except ValueError as exc:
        return undeterminable(str(exc))
    c = exposure.concentration
    if c.qualifier == "below_lod":
        upper_only = True
        conc_value = c.bound
    elif c.known:
        upper_only = False
        conc_value = c.value
    else:
        return undeterminable(f"exposure is {c.qualifier}")
    try:
        conc = to_molar(conc_value, c.unit, mw=mw)
    except ValueError as exc:
        return undeterminable(str(exc))
    if exposure.free:
        fu = (1.0, 1.0)
        fu_note = "free concentration measured"
    elif exposure.free_fraction:
        fu = exposure.free_fraction
        fu_note = "measured free-fraction range applied"
    else:
        fu = unknown_free_fraction
        fu_note = (f"free fraction unknown: range {fu[0]}–{fu[1]} applied (not assumed to "
                   "be 1)")
    low, high = conc * fu[0] / potency, conc * fu[1] / potency
    if upper_only:
        low = 0.0
    if potency_censored:
        # the true potency is weaker (larger) than the bound: the ratio is at most this
        low = 0.0
    if low >= 1:
        verdict = "plausible"
    elif high < implausible_below:
        verdict = "implausible"
    else:
        verdict = "depends_on_unknowns"
    return {**out, "verdict": verdict, "ratio": [low, high], "free_fraction": list(fu),
            "free_fraction_note": fu_note, "concentration_molar": conc,
            "potency_molar": potency, "censored": {"exposure_below_lod": upper_only,
                                                   "potency_above_max_tested": potency_censored},
            "not": "target occupancy or a probability of efficacy"}


def screen_exposure(exposures: Iterable[ExposureRecord], assays: Iterable[AssayResult], *,
                    site: str, mw: dict[str, float] | None = None, **kw) -> list[dict]:
    """Every (exposure at ``site``, assay) pair for the same molecule.

    An assay with no exposure measured at ``site`` is reported as undeterminable rather
    than judged against plasma.
    """
    by_mol: dict[str, list[ExposureRecord]] = {}
    for e in exposures:
        by_mol.setdefault(e.molecule, []).append(e)
    out = []
    for a in assays:
        at_site = [e for e in by_mol.get(a.molecule, []) if e.site == site]
        if not at_site:
            others = sorted({e.site for e in by_mol.get(a.molecule, [])})
            out.append({"molecule": a.molecule, "target": a.target, "endpoint": a.endpoint,
                        "site": site, "verdict": "undeterminable", "ratio": None,
                        "reason": f"no exposure measured at {site}"
                                  + (f" (measured at {others}; another site is not this one)"
                                     if others else "")})
            continue
        best = max(at_site, key=lambda e: (e.concentration.value or e.concentration.bound or 0))
        out.append(exposure_ratio(best, a, mw=(mw or {}).get(a.molecule), **kw))
    return out
