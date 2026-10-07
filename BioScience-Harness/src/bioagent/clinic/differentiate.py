"""Syndrome differentiation (辨证): scoring the pack's criteria against the intake.

Each syndrome's criteria are its main findings (主症), secondary findings (次症), tongue
(舌) and pulse (脉). A finding counts 1 when it matches, 0.5 when it matches partially
(see ``findings``); the score is the weighted sum over the most it could be,

    score = (2·主症 + 次症 + 舌 + 脉 − 舌相悖 − 脉相悖) / (2·|主症| + |次症| + [舌] + [脉])

so a tongue or pulse that contradicts the syndrome (舌淡 against 舌红少苔) counts against
it. Scores rank; the **criteria** decide. A syndrome *meets* them, in the form the
diagnostic textbooks and the 中药新药临床研究指导原则 state them, when

    主症 ≥ 2, and 次症 ≥ 1 (or 主症 ≥ 3), and 舌 or 脉 agrees, and 舌 and 脉 do not both
    contradict it;

it *leans* when 主症 ≥ 1 and the score is at least 0.25. Only a syndrome that meets the
criteria goes on to a draft prescription; otherwise the result is the questions that
would decide it: the leading candidates' findings not yet asked, those that separate
the first two first.

A more specific syndrome that meets the criteria (脾胃气虚证) is put before the general
one it refines (气虚证), which is kept and marked as covered.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from .findings import TermMatcher, match_pulse, match_tongue
from .intake import Intake
from .pack import ClinicPack, Syndrome

__all__ = ["CriterionHit", "Candidate", "Question", "Differentiation", "differentiate",
           "WEIGHTS", "RULE"]

WEIGHTS = {"main": 2.0, "secondary": 1.0, "tongue": 1.0, "pulse": 1.0}
RULE = "主症≥2项，次症≥1项（或主症≥3项），舌、脉至少一项相符，且舌、脉不同时相悖"
_RANK = {"meets": 2, "leans": 1, "weak": 0}


@dataclass(frozen=True)
class CriterionHit:
    category: str
    term: str
    strength: float
    via: str
    entry: str


@dataclass(frozen=True)
class Candidate:
    syndrome: str
    score: float
    status: str
    hits: tuple[CriterionHit, ...]
    main: float
    secondary: float
    tongue: float
    pulse: float
    contradictions: tuple[str, ...]
    unasked: tuple[str, ...]
    principle: str
    formula: str | None
    parent: str | None
    covered_by: str = ""

    def explain(self) -> str:
        parts = [f"主症 {self.main:g}", f"次症 {self.secondary:g}",
                 f"舌 {self.tongue:g}", f"脉 {self.pulse:g}"]
        return "，".join(parts) + (f"；相悖：{'；'.join(self.contradictions)}"
                                   if self.contradictions else "")


@dataclass(frozen=True)
class Question:
    finding: str
    group: str
    why: str


@dataclass
class Differentiation:
    candidates: list[Candidate]
    status: str                       # meets | leans | insufficient
    questions: list[Question]
    notes: list[str] = field(default_factory=list)

    @property
    def top(self) -> Candidate | None:
        if self.candidates and self.candidates[0].status == "meets":
            return self.candidates[0]
        return None

    @property
    def leading(self) -> Candidate | None:
        return self.candidates[0] if self.candidates else None

    def ranked(self, k: int = 3) -> list[str]:
        return [c.syndrome for c in self.candidates[:k]]


def _depth(pack: ClinicPack, name: str) -> int:
    d, seen = 0, set()
    while (parent := pack.syndromes[name].parent) and parent not in seen:
        seen.add(parent)
        d, name = d + 1, parent
    return d


def _ancestors(pack: ClinicPack, name: str) -> list[str]:
    out = []
    while (parent := pack.syndromes[name].parent) and parent not in out:
        out.append(parent)
        name = parent
    return out


def _score(s: Syndrome, intake: Intake, matcher: TermMatcher) -> Candidate:
    hits: list[CriterionHit] = []
    unasked: list[str] = []
    totals = {"main": 0.0, "secondary": 0.0}
    for category, terms in (("main", s.main), ("secondary", s.secondary)):
        for term in terms:
            m = matcher.match(term, intake.present, intake.absent)
            if m.strength > 0:
                hits.append(CriterionHit(category, term, m.strength, m.via, m.entry))
                totals[category] += m.strength
            if not matcher.asked(term, intake.present, intake.absent):
                unasked.append(term)
    contradictions: list[str] = []
    best = {}
    for category, terms, fn, observed in (("tongue", s.tongue, match_tongue, intake.tongue),
                                          ("pulse", s.pulse, match_pulse, intake.pulse)):
        best[category] = 0.0
        if not terms or not observed:
            continue
        results = [fn(t, observed) for t in terms]
        top = max(results, key=lambda r: r.strength)
        if top.strength > 0:
            best[category] = top.strength
            hits.append(CriterionHit(category, top.criterion, top.strength,
                                     "features", top.observed))
        elif all(r.contradicted for r in results):
            contradictions.append(f"{'舌' if category == 'tongue' else '脉'}：记录为"
                                  f"{results[0].observed}，与{'/'.join(terms)}相悖")
    den = (WEIGHTS["main"] * len(s.main) + WEIGHTS["secondary"] * len(s.secondary)
           + (WEIGHTS["tongue"] if s.tongue else 0) + (WEIGHTS["pulse"] if s.pulse else 0))
    num = (WEIGHTS["main"] * totals["main"] + WEIGHTS["secondary"] * totals["secondary"]
           + WEIGHTS["tongue"] * best["tongue"] + WEIGHTS["pulse"] * best["pulse"]
           - len(contradictions))
    score = max(0.0, num / den) if den else 0.0
    tongue_contra = any(c.startswith("舌") for c in contradictions)
    pulse_contra = any(c.startswith("脉") for c in contradictions)
    both_contra = tongue_contra and pulse_contra
    agrees = best["tongue"] >= 0.5 or best["pulse"] >= 0.5
    main, sec = totals["main"], totals["secondary"]
    if main >= 2 and (sec >= 1 or main >= 3) and agrees and not both_contra:
        status = "meets"
    elif main >= 1 and score >= 0.25 and not both_contra:
        status = "leans"
    else:
        status = "weak"
    return Candidate(syndrome=s.name, score=round(score, 3), status=status, hits=tuple(hits),
                     main=main, secondary=sec, tongue=best["tongue"], pulse=best["pulse"],
                     contradictions=tuple(contradictions), unasked=tuple(unasked),
                     principle=s.principle, formula=s.formula, parent=s.parent)


def differentiate(intake: Intake, pack: ClinicPack, *, limit: int = 8) -> Differentiation:
    matcher = TermMatcher(pack.finding_terms(), pack.synonyms)
    scored = [_score(s, intake, matcher) for s in pack.syndromes.values()]
    scored = [c for c in scored if c.score > 0 or c.main > 0]
    scored.sort(key=lambda c: (-_RANK[c.status], -c.score, -_depth(pack, c.syndrome),
                               c.syndrome))
    # A general syndrome that a more specific one refines goes after it, and says so.
    meeting = {c.syndrome for c in scored if c.status == "meets"}
    covered: dict[str, str] = {}
    for c in scored:
        if c.syndrome in meeting:
            for anc in _ancestors(pack, c.syndrome):
                if anc in meeting and anc not in covered:
                    covered[anc] = c.syndrome
    if covered:
        ordered: list[Candidate] = []
        for c in scored:
            if c.syndrome in covered:
                continue
            ordered.append(c)
        for name, child in covered.items():
            c = next(x for x in scored if x.syndrome == name)
            at = next(i for i, x in enumerate(ordered) if x.syndrome == child) + 1
            ordered.insert(at, replace(c, covered_by=child))
        scored = ordered
    candidates = scored[:limit]
    lead = candidates[0] if candidates else None
    status = ("meets" if lead and lead.status == "meets"
              else "leans" if lead and lead.status == "leans" else "insufficient")
    notes: list[str] = []
    if lead and lead.status == "meets":
        rivals = [c for c in candidates[1:] if c.status == "meets" and not c.covered_by
                  and c.syndrome not in _ancestors(pack, lead.syndrome)
                  and lead.syndrome not in _ancestors(pack, c.syndrome)]
        for r in rivals:
            if lead.score - r.score <= 0.1:
                notes.append(f"{r.syndrome}亦符合诊断标准，评分接近（{r.score:.2f} 对 "
                             f"{lead.score:.2f}）：或为兼证，或需进一步鉴别，由医师判断")
    return Differentiation(candidates=candidates, status=status,
                           questions=_questions(intake, pack, candidates), notes=notes)


def _questions(intake: Intake, pack: ClinicPack, candidates: list[Candidate],
               *, limit: int = 10) -> list[Question]:
    group_of = {t: g for g, terms in pack.inquiry_groups.items() for t in terms}
    out: list[Question] = []
    if not intake.tongue:
        out.append(Question("舌象（舌质、舌苔）", "望诊", "四诊合参：辨证需要舌象"))
    if not intake.pulse:
        out.append(Question("脉象", "切诊", "四诊合参：辨证需要脉象"))
    lead = [c for c in candidates if c.status in ("meets", "leans")]
    if lead and lead[0].status == "meets":
        # The general forms of a syndrome that already meets the criteria decide nothing.
        above = set(_ancestors(pack, lead[0].syndrome))
        lead = [c for c in lead if c.syndrome not in above and not c.covered_by]
    lead = lead[:3] or candidates[:2]
    seen: set[str] = set()
    if len(lead) >= 2:
        a, b = lead[0], lead[1]
        sa = set(pack.syndromes[a.syndrome].main + pack.syndromes[a.syndrome].secondary)
        sb = set(pack.syndromes[b.syndrome].main + pack.syndromes[b.syndrome].secondary)
        for term in a.unasked:
            if term not in sb and term not in seen:
                seen.add(term)
                out.append(Question(term, group_of.get(term, "问诊"),
                                    f"鉴别{a.syndrome}与{b.syndrome}"))
        for term in b.unasked:
            if term not in sa and term not in seen:
                seen.add(term)
                out.append(Question(term, group_of.get(term, "问诊"),
                                    f"鉴别{b.syndrome}与{a.syndrome}"))
    for c in lead:
        main = set(pack.syndromes[c.syndrome].main)
        for term in sorted(c.unasked, key=lambda t: (t not in main, t)):
            if term not in seen:
                seen.add(term)
                out.append(Question(term, group_of.get(term, "问诊"),
                                    f"{c.syndrome}的{'主症' if term in main else '次症'}"))
    return out[:limit]
