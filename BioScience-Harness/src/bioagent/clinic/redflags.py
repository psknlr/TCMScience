"""Red flags: findings and vital signs that stop a TCM consultation and send the person on.

Screening reads every recorded entry, the chief complaint included, against the pack's
red-flag rules (terms, and patterns such as 胸…痛 that catch 胸部刺痛 as well as 胸痛), and
the vital signs against NEWS2's single-parameter red scores. It is built for recall: a
false alarm costs a check, a missed one can cost a life.

A flag stops drafting until it is **cleared**: the intake's ``red_flags_cleared`` names
the flag, who cleared it and how (心电图、肌钙蛋白正常). An uncleared flag means no
differentiation is offered and no prescription is drafted.
"""

from __future__ import annotations

from dataclasses import dataclass

from .findings import normalise
from .intake import Intake
from .pack import ClinicPack

__all__ = ["RedFlag", "screen"]


@dataclass(frozen=True)
class RedFlag:
    id: str
    why: str
    action: str
    evidence: str                  # the entry or vital sign that raised it
    cleared_by: str = ""
    cleared_how: str = ""

    @property
    def cleared(self) -> bool:
        return bool(self.cleared_by)


def screen(intake: Intake, pack: ClinicPack) -> list[RedFlag]:
    entries = [e for e in intake.all_text if e]
    hits: dict[str, RedFlag] = {}
    for rule in pack.red_flags:
        terms = [normalise(t) for t in rule.terms]
        for e in entries:
            if any(t and t in e for t in terms) or any(p.search(e) for p in rule.patterns):
                hits.setdefault(rule.id, RedFlag(rule.id, rule.why, rule.action, e))
                break
    for v in pack.vital_rules:
        value = intake.vitals.get(v.field)
        if value is None:
            continue
        low = v.low is not None and value <= v.low
        high = v.high is not None and value >= v.high
        if low or high:
            hits.setdefault(v.id, RedFlag(
                v.id, v.why, "urgent medical assessment before any TCM treatment",
                f"{v.field} {value:g} {v.unit} ({'≤' if low else '≥'} "
                f"{v.low if low else v.high:g})"))
    cleared = {c["flag"]: c for c in intake.red_flags_cleared}
    out = []
    for flag in hits.values():
        c = cleared.get(flag.id)
        out.append(RedFlag(flag.id, flag.why, flag.action, flag.evidence,
                           cleared_by=c["by"] if c else "", cleared_how=c["how"] if c else ""))
    return out
