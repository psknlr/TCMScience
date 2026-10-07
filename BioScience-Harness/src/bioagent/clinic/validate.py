"""Agreement with practitioners: how often the differentiation names the syndrome a
licensed practitioner named, on cases the practitioner labelled.

A case is an intake plus the practitioner's label: the syndrome, who labelled it and
their credential. The harness refuses a case without a label or a labeller, and it
refuses synthetic cases unless told they are only a test (the report then says so on
its first line). For each case the differentiation is run blind to the label, and:

* **top-1 agreement** — the leading candidate meets or leans to the criteria and is the
  label; an *abstention* (no candidate does) counts as disagreement, and is reported;
* **top-3 agreement** — the label is among the first three candidates;
* **Cohen's kappa** between the leading answer (with abstention as its own category) and
  the label, which discounts the agreement two raters reach by chance;
* Wilson 95 % intervals for both proportions, and the confusions (label → answer);
* where a second practitioner labelled the same case, their kappa with the first: the
  ceiling for the system, since practitioners disagree too.

Labels outside the pack are counted apart: the differentiation cannot name a syndrome
the pack does not hold.

What this measures is agreement with these labellers on these cases. It is not
evidence that a drafted prescription helps anyone; that needs a prospective study with
outcomes.
"""

from __future__ import annotations

import json
import math
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from .differentiate import differentiate
from .intake import IntakeError, parse_intake
from .pack import ClinicPack

__all__ = ["AgreementReport", "agreement", "read_cases", "cohen_kappa", "wilson"]

ABSTAIN = "（未作答）"


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return (round(max(0.0, centre - half), 4), round(min(1.0, centre + half), 4))


def cohen_kappa(a: Sequence[str], b: Sequence[str]) -> float | None:
    if len(a) != len(b) or not a:
        raise ValueError("kappa needs two equal, non-empty label lists")
    n = len(a)
    po = sum(x == y for x, y in zip(a, b)) / n
    ca, cb = Counter(a), Counter(b)
    pe = sum(ca[k] * cb[k] for k in set(ca) | set(cb)) / (n * n)
    if pe >= 1.0:
        return None
    return round((po - pe) / (1 - pe), 4)


@dataclass
class AgreementReport:
    n: int
    synthetic: bool
    top1: int
    top3: int
    abstained: int
    kappa: float | None
    top1_ci: tuple[float, float]
    top3_ci: tuple[float, float]
    out_of_pack: int
    confusions: list[tuple[str, str, int]]
    per_label: dict[str, dict[str, int]]
    inter_rater_kappa: float | None
    inter_rater_n: int
    labellers: list[str]
    rows: list[dict[str, Any]] = field(default_factory=list)
    pack_version: str = ""
    pack_digest: str = ""
    pack_reviewed: bool = False

    def as_dict(self) -> dict[str, Any]:
        d = dict(self.__dict__)
        d["confusions"] = [list(c) for c in self.confusions]
        return d

    def markdown(self) -> str:
        lines = []
        if self.synthetic:
            lines += ["> **SYNTHETIC CASES — a test of the harness, not evidence of agreement "
                      "with practitioners.**", ""]
        lines += ["# 辨证一致性评估", "",
                  f"病例 {self.n} 例；标注者：{', '.join(self.labellers) or '—'}；知识包 "
                  f"{self.pack_version}（{'已审核' if self.pack_reviewed else '未经执业医师审核'}）。", "",
                  "| 指标 | 值 | 95% CI |", "|---|---|---|",
                  f"| top-1 一致 | {self.top1}/{self.n} = {self.top1 / max(self.n, 1):.1%} | "
                  f"{self.top1_ci[0]:.1%}–{self.top1_ci[1]:.1%} |",
                  f"| top-3 一致 | {self.top3}/{self.n} = {self.top3 / max(self.n, 1):.1%} | "
                  f"{self.top3_ci[0]:.1%}–{self.top3_ci[1]:.1%} |",
                  f"| Cohen's κ（top-1 与标注） | {self.kappa if self.kappa is not None else '—'} | |",
                  f"| 未作答（无候选证符合或倾向） | {self.abstained} | |",
                  f"| 标注证候不在知识包内 | {self.out_of_pack} | |"]
        if self.inter_rater_n:
            lines.append(f"| 两位标注者之间的 κ（{self.inter_rater_n} 例） | "
                         f"{self.inter_rater_kappa if self.inter_rater_kappa is not None else '—'}"
                         " | |")
        lines += ["", "## 混淆（标注 → 系统）", ""]
        if self.confusions:
            lines += ["| 标注 | 系统 | 例数 |", "|---|---|---|"]
            lines += [f"| {a} | {b} | {k} |" for a, b, k in self.confusions]
        else:
            lines.append("无。")
        lines += ["", "## 这是什么", "",
                  "这是系统与上述标注者在这些病例上的一致程度，不是疗效证据：处方是否有益，"
                  "需要有结局指标的前瞻性研究。标注者之间同样存在分歧，其 κ 是系统可达到的上限参考。",
                  ""]
        return "\n".join(lines)


def read_cases(path: str | Path) -> list[dict[str, Any]]:
    text = Path(path).read_text(encoding="utf-8")
    if Path(path).suffix == ".jsonl":
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    doc = json.loads(text)
    return list(doc["cases"] if isinstance(doc, Mapping) else doc)


def _label(case: Mapping[str, Any], key: str) -> Mapping[str, Any] | None:
    lab = case.get(key)
    if not lab:
        return None
    if not isinstance(lab, Mapping) or not lab.get("syndrome") or not lab.get("labelled_by"):
        raise ValueError(f"case {case.get('id')!r}: {key} needs syndrome and labelled_by")
    return lab


def agreement(cases: Sequence[Mapping[str, Any]], pack: ClinicPack, *,
              allow_synthetic: bool = False) -> AgreementReport:
    if not cases:
        raise ValueError("no cases")
    problems: list[str] = []
    synthetic = False
    for c in cases:
        try:
            if _label(c, "expert") is None:
                problems.append(f"case {c.get('id')!r} has no expert label")
        except ValueError as exc:
            problems.append(str(exc))
        if c.get("synthetic"):
            synthetic = True
    if problems:
        raise ValueError("cases without a practitioner's label cannot be scored: "
                         + "; ".join(problems[:5]))
    if synthetic and not allow_synthetic:
        raise ValueError("synthetic cases are not evidence of agreement; pass "
                         "allow_synthetic to run them as a test of the harness")
    answers, labels, rows = [], [], []
    top1 = top3 = abstained = out_of_pack = 0
    per_label: dict[str, dict[str, int]] = {}
    second_a, second_b = [], []
    labellers: set[str] = set()
    for c in cases:
        lab = _label(c, "expert")
        truth = str(lab["syndrome"])
        labellers.add(str(lab["labelled_by"]))
        try:
            intake = parse_intake(c["intake"], pack)
        except (IntakeError, KeyError) as exc:
            raise ValueError(f"case {c.get('id')!r}: {exc}") from exc
        d = differentiate(intake, pack)
        lead = d.leading
        answer = lead.syndrome if lead and lead.status in ("meets", "leans") else ABSTAIN
        if answer == ABSTAIN:
            abstained += 1
        ranked = d.ranked(3)
        hit1, hit3 = answer == truth, truth in ranked
        top1 += hit1
        top3 += hit3
        if truth not in pack.syndromes:
            out_of_pack += 1
        stats = per_label.setdefault(truth, {"n": 0, "top1": 0, "top3": 0})
        stats["n"] += 1
        stats["top1"] += hit1
        stats["top3"] += hit3
        answers.append(answer)
        labels.append(truth)
        second = _label(c, "second_expert")
        if second is not None:
            second_a.append(truth)
            second_b.append(str(second["syndrome"]))
            labellers.add(str(second["labelled_by"]))
        rows.append({"id": c.get("id"), "label": truth, "answer": answer,
                     "status": d.status, "top3": ranked, "top1": hit1, "in_top3": hit3})
    n = len(cases)
    confusions = Counter((t, a) for t, a in zip(labels, answers) if t != a)
    return AgreementReport(
        n=n, synthetic=synthetic, top1=top1, top3=top3, abstained=abstained,
        kappa=cohen_kappa(answers, labels), top1_ci=wilson(top1, n), top3_ci=wilson(top3, n),
        out_of_pack=out_of_pack,
        confusions=sorted(((a, b, k) for (a, b), k in confusions.items()),
                          key=lambda x: (-x[2], x[0], x[1])),
        per_label=per_label,
        inter_rater_kappa=cohen_kappa(second_a, second_b) if second_a else None,
        inter_rater_n=len(second_a), labellers=sorted(labellers), rows=rows,
        pack_version=pack.version, pack_digest=pack.digest, pack_reviewed=pack.reviewed)
