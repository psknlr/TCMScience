"""Follow-up (随访): change in the syndrome's findings, adverse events, and what to do next.

Each visit scores the findings being followed, 0 无 / 1 轻 / 2 中 / 3 重. With the
syndrome given, its main findings count double (0/2/4/6), as the 中药新药临床研究指导原则
(2002) scores 证候; the change is that guideline's 证候疗效 (尼莫地平法):

    reduction = (baseline score − current score) / baseline score

≥ 95 % 临床痊愈, ≥ 70 % 显效, ≥ 30 % 有效, below 30 % 无效, and a rise is 加重. It is the
change in recorded findings in one person, not evidence that the prescription caused it.

An adverse event that is severe, or moderate and possibly related, stops the
prescription until the practitioner sees the person; a red flag among the new findings
sends them on. No improvement after two weeks, a worsening, or new findings outside
the syndrome's criteria call for differentiating again.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Mapping, Sequence

from .findings import normalise, split_entries
from .pack import ClinicPack

__all__ = ["Visit", "AdverseEvent", "FollowUp", "parse_visit", "assess_followup", "SCORES"]

SCORES = {"无": 0, "轻": 1, "中": 2, "重": 3}
_AE_SEVERITY = ("mild", "moderate", "severe")
_RELATION = ("unrelated", "unlikely", "possible", "probable", "certain")


@dataclass(frozen=True)
class AdverseEvent:
    event: str
    severity: str
    relation: str


@dataclass(frozen=True)
class Visit:
    date: str
    scores: Mapping[str, int]
    adverse_events: tuple[AdverseEvent, ...] = ()
    new_findings: tuple[str, ...] = ()
    adherence: str = "unknown"
    tongue: tuple[str, ...] = ()
    pulse: tuple[str, ...] = ()
    notes: str = ""


def _score(v: Any, finding: str) -> int:
    if isinstance(v, str) and v.strip() in SCORES:
        return SCORES[v.strip()]
    try:
        n = int(v)
    except (TypeError, ValueError):
        raise ValueError(f"score for {finding} is 0-3 or 无/轻/中/重, not {v!r}") from None
    if not 0 <= n <= 3:
        raise ValueError(f"score for {finding} is 0-3, not {n}")
    return n


def parse_visit(doc: Mapping[str, Any]) -> Visit:
    if not doc.get("date"):
        raise ValueError("a visit needs a date (YYYY-MM-DD)")
    date.fromisoformat(str(doc["date"]))
    scores = {normalise(k): _score(v, k) for k, v in (doc.get("scores") or {}).items()}
    events = []
    for e in doc.get("adverse_events") or ():
        sev = str(e.get("severity") or "").lower()
        rel = str(e.get("relation") or "possible").lower()
        if sev not in _AE_SEVERITY:
            raise ValueError(f"adverse event severity is one of {_AE_SEVERITY}, not {sev!r}")
        if rel not in _RELATION:
            raise ValueError(f"adverse event relation is one of {_RELATION}, not {rel!r}")
        events.append(AdverseEvent(str(e.get("event") or ""), sev, rel))
    adherence = str(doc.get("adherence") or "unknown").lower()
    if adherence not in ("full", "partial", "none", "unknown"):
        raise ValueError("adherence is full, partial, none or unknown")
    return Visit(date=str(doc["date"]), scores=scores, adverse_events=tuple(events),
                 new_findings=tuple(split_entries(doc.get("new_findings"))),
                 adherence=adherence, tongue=tuple(split_entries(doc.get("tongue"))),
                 pulse=tuple(split_entries(doc.get("pulse"))), notes=str(doc.get("notes") or ""))


@dataclass
class FollowUp:
    days: int
    baseline_total: float
    current_total: float
    reduction: float | None
    category: str
    changes: list[dict[str, Any]]
    actions: list[str]
    stop: bool
    refer: list[str] = field(default_factory=list)
    new_outside_criteria: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def _category(reduction: float | None, worse: bool) -> str:
    if worse:
        return "加重"
    if reduction is None:
        return "无基线积分"
    if reduction >= 0.95:
        return "临床痊愈"
    if reduction >= 0.70:
        return "显效"
    if reduction >= 0.30:
        return "有效"
    return "无效"


def assess_followup(baseline: Visit, current: Visit, pack: ClinicPack, *,
                    syndrome: str | None = None) -> FollowUp:
    days = (date.fromisoformat(current.date) - date.fromisoformat(baseline.date)).days
    if days < 0:
        raise ValueError("the current visit is dated before the baseline")
    main = set()
    criteria: set[str] = set()
    if syndrome:
        s = pack.syndromes[syndrome]
        main = {normalise(t) for t in s.main}
        criteria = main | {normalise(t) for t in s.secondary}
    weight = {f: (2 if f in main else 1) for f in set(baseline.scores) | set(current.scores)}
    changes = []
    for f in sorted(set(baseline.scores) | set(current.scores)):
        b, c = baseline.scores.get(f), current.scores.get(f)
        changes.append({"finding": f, "baseline": b, "current": c, "weight": weight[f],
                        "change": None if b is None or c is None else c - b})
    followed = [f for f in baseline.scores if f in current.scores]
    b_total = float(sum(weight[f] * baseline.scores[f] for f in followed))
    c_total = float(sum(weight[f] * current.scores[f] for f in followed))
    reduction = (b_total - c_total) / b_total if b_total > 0 else None
    worse = c_total > b_total
    category = _category(reduction, worse)
    actions: list[str] = []
    refer: list[str] = []
    stop = False
    texts = list(current.new_findings) + [normalise(e.event) for e in current.adverse_events]
    for rule in pack.red_flags:
        terms = [normalise(t) for t in rule.terms]
        for e in texts:
            if any(t and t in e for t in terms) or any(p.search(e) for p in rule.patterns):
                refer.append(f"{rule.id}：{e}（{rule.why}）")
                break
    if refer:
        stop = True
        actions.append("出现红旗征：停用当前处方，立即转诊（" + "；".join(refer) + "）")
    for e in current.adverse_events:
        related = e.relation in ("possible", "probable", "certain")
        if e.severity == "severe" or (e.severity == "moderate" and related):
            stop = True
            actions.append(f"不良事件“{e.event}”（{e.severity}，{e.relation}）：停药，"
                           "由医师面诊评估后再定")
        elif related:
            actions.append(f"不良事件“{e.event}”（{e.severity}）：记录并观察，复诊时评估")
    unscored = [f for f in baseline.scores if f not in current.scores]
    if unscored:
        actions.append("本次未评分：" + "、".join(unscored) + "（不计入积分变化）")
    outside = [f for f in current.new_findings if criteria and f not in criteria]
    if outside or worse:
        actions.append("出现新症状或病情加重：请重新四诊合参、重新辨证")
    if category == "无效" and days >= 14:
        actions.append("两周以上无明显改善：复核辨证与用药，必要时转诊")
    if current.adherence in ("partial", "none"):
        actions.append("服药依从性不足：疗效评价须结合实际服药情况")
    if category in ("显效", "临床痊愈") and not stop:
        actions.append("改善明显：是否守方、减量或停药由医师决定")
    if not actions:
        actions.append("继续观察，按期复诊")
    return FollowUp(days=days, baseline_total=b_total, current_total=c_total,
                    reduction=None if reduction is None else round(reduction, 3),
                    category=category, changes=changes, actions=actions, stop=stop,
                    refer=refer, new_outside_criteria=outside)


def read_visits(docs: Sequence[Mapping[str, Any]]) -> list[Visit]:
    return [parse_visit(d) for d in docs]
