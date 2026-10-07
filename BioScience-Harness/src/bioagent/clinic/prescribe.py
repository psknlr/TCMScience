"""Draft prescriptions (处方草案) and the checks every prescription goes through.

A draft starts from the syndrome's base formula in 《方剂学》 with its textbook doses and is
then made to fit the person:

* **age** — under 14 the doses and the Pharmacopoeia ranges are scaled by the pack's
  paediatric fractions (成人量的 1/6 … 2/3); under 1 every dose must be set by the
  practitioner; from 65 the report says doses are usually reduced;
* **dose limits** — a textbook dose above the Pharmacopoeia upper limit (六味地黄丸's
  熟地黄 24 g against 9–15 g) is drafted at the limit, and the report shows both;
* **加减** — the textbook modifications whose findings are present are listed, and
  applied only when asked (``apply_textbook``); an added herb starts at the
  Pharmacopoeia lower limit;
* the practitioner's own changes (``modifications``) — add, remove, replace, re-dose.

``check`` then holds the prescription, drafted or the practitioner's own, to:
十八反 / 十九畏 (the pack's rules and the seed knowledge base's records); pregnancy
(禁用 stops, 慎用 needs the practitioner's reason, an unknown status in a woman of
child-bearing age needs it asked first); allergies; drug interactions with the recorded
medications; cautions for recorded conditions; toxic herbs and their processing and
decoction notes; doses outside the (age-scaled) range. Where the pack and the knowledge
base disagree on a safety point (附子 in pregnancy: 药典 2020 慎用, the seed's 《中药学》
reading 禁用), the stricter applies and the report says the sources differ.

Severities: ``stop`` — cannot be signed as it stands; ``block`` — can be signed only with
the practitioner's recorded reason; ``warn`` and ``info`` — shown. Nothing here decides;
a draft is a starting point for a licensed practitioner, who signs or rejects it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from .findings import TermMatcher, normalise
from .intake import Intake
from .pack import ClinicPack

__all__ = ["Issue", "Line", "Prescription", "draft", "check", "age_fraction",
           "instructions_for", "SEVERITIES"]

SEVERITIES = ("stop", "block", "warn", "info")
_TOXIC = ("有大毒", "有毒", "有小毒")


@dataclass(frozen=True)
class Issue:
    id: str
    severity: str
    code: str
    message: str
    herbs: tuple[str, ...] = ()
    source: str = ""


@dataclass(frozen=True)
class Line:
    herb: str
    dose_g: float
    basis: str
    textbook_g: float | None = None
    range_g: tuple[float, float] | None = None
    notes: tuple[str, ...] = ()
    toxicity: str = ""


@dataclass
class Prescription:
    status: str                      # draft | blocked | refer | no_formula | withheld
    syndrome: str
    principle: str
    formula: str | None
    source: str
    lines: list[Line]
    issues: list[Issue]
    instructions: list[str]
    fraction: float
    fraction_note: str
    applied: list[str] = field(default_factory=list)
    suggested: list[dict[str, Any]] = field(default_factory=list)
    days: int = 7
    formula_note: str = ""

    @property
    def total_g(self) -> float:
        return round(sum(line.dose_g for line in self.lines), 1)

    def blocking(self) -> list[Issue]:
        return [i for i in self.issues if i.severity in ("stop", "block")]

    def as_dict(self) -> dict[str, Any]:
        return {"status": self.status, "syndrome": self.syndrome, "principle": self.principle,
                "formula": self.formula, "source": self.source, "days": self.days,
                "fraction": self.fraction, "fraction_note": self.fraction_note,
                "lines": [{"herb": ln.herb, "dose_g": ln.dose_g, "basis": ln.basis,
                           "textbook_g": ln.textbook_g,
                           "range_g": list(ln.range_g) if ln.range_g else None,
                           "notes": list(ln.notes), "toxicity": ln.toxicity}
                          for ln in self.lines],
                "total_g": self.total_g,
                "issues": [i.__dict__ for i in self.issues],
                "instructions": self.instructions, "applied": self.applied,
                "suggested": self.suggested, "formula_note": self.formula_note}


def _round(g: float) -> float:
    return max(0.5, round(g * 2) / 2)


def age_fraction(pack: ClinicPack, age: float) -> tuple[float, str]:
    for lo, hi, frac, note in pack.paediatric:
        if lo <= age < hi:
            return frac, note
    return 1.0, "成人量"


def _kb():
    from ..tcm.knowledge import default_knowledge
    return default_knowledge()


def _resolve(kb: Any, herb: str) -> Any:
    try:
        r = kb.resolve(herb)
    except Exception:                                            # noqa: BLE001
        return None
    return r.entity if getattr(r, "found", False) else None


def _line_for(pack: ClinicPack, herb: str, dose: float | None, fraction: float, *,
              basis: str, textbook: float | None = None) -> tuple[Line, list[Issue]]:
    spec = pack.herbs.get(herb)
    issues: list[Issue] = []
    if spec is None:
        g = _round(dose) if dose else 0.0
        return Line(herb, g, basis, textbook), issues
    lo, hi = spec.low * fraction, spec.high * fraction
    if dose is None:
        g, basis = _round(lo), "added at the Pharmacopoeia lower limit"
    else:
        g = _round(dose)
        if basis == "textbook" and g > hi:
            capped = max(0.5, int(hi * 2) / 2)
            issues.append(Issue(f"dose_capped:{herb}", "warn", "dose_capped",
                                f"{herb}：《方剂学》剂量 {g:g} g"
                                + ("（按年龄折算）" if fraction < 1 else "")
                                + f"超过《药典》上限 {hi:g} g，草案按 {capped:g} g", (herb,),
                                "中华人民共和国药典 2020年版"))
            g, basis = capped, "textbook, capped at the Pharmacopoeia upper limit"
    notes = tuple(n for n in (spec.note,) if n)
    return Line(herb, g, basis, textbook, (round(lo, 2), round(hi, 2)), notes,
                spec.toxicity), issues


def _apply(pack: ClinicPack, lines: list[Line], mods: Sequence[Mapping[str, Any]],
           fraction: float) -> tuple[list[Line], list[Issue], list[str]]:
    issues: list[Issue] = []
    applied: list[str] = []
    by_herb = {ln.herb: ln for ln in lines}
    order = [ln.herb for ln in lines]
    for m in mods:
        if "add" in m:
            herb = str(m["add"])
            if herb in by_herb:
                raise ValueError(f"{herb} is already in the prescription; use dose to change it")
            dose = None if m.get("dose") in (None, "") else float(m["dose"])
            line, more = _line_for(pack, herb, dose, fraction,
                                   basis="practitioner" if dose is not None else "added")
            by_herb[herb] = line
            order.append(herb)
            issues += more
            applied.append(f"加 {herb} {line.dose_g:g} g")
        elif "remove" in m:
            herb = str(m["remove"])
            if herb not in by_herb:
                raise ValueError(f"{herb} is not in the prescription")
            del by_herb[herb]
            order.remove(herb)
            applied.append(f"去 {herb}")
        elif "replace" in m:
            old, new = str(m["replace"]), str(m.get("with") or "")
            if old not in by_herb or not new:
                raise ValueError(f"replace needs a herb in the prescription and 'with': {m}")
            dose = None if m.get("dose") in (None, "") else float(m["dose"])
            if dose is None and new in pack.herbs and old in pack.herbs:
                dose = by_herb[old].dose_g          # 白芍易赤芍: the same dose
            line, more = _line_for(pack, new, dose, fraction, basis="practitioner")
            del by_herb[old]
            by_herb[new] = line
            order[order.index(old)] = new
            issues += more
            applied.append(f"{old}易为{new} {line.dose_g:g} g")
        elif "dose" in m:
            herb = str(m["dose"])
            if herb not in by_herb:
                raise ValueError(f"{herb} is not in the prescription")
            g = float(m.get("g"))
            old = by_herb[herb]
            by_herb[herb] = Line(herb, _round(g), "practitioner", old.textbook_g, old.range_g,
                                 old.notes, old.toxicity)
            applied.append(f"{herb} 改为 {_round(g):g} g")
        else:
            raise ValueError(f"a modification is add, remove, replace or dose, not {dict(m)}")
    return [by_herb[h] for h in order], issues, applied


def _suggestions(pack: ClinicPack, formula: str, intake: Intake) -> list[dict[str, Any]]:
    spec = pack.formulas[formula]
    triggers = {t for m in spec.modifications for t in m.when_any}
    matcher = TermMatcher(triggers | pack.finding_terms(), pack.synonyms)
    out = []
    names = [h for h, _ in spec.herbs]
    for m in spec.modifications:
        hit = next((m2 for t in m.when_any
                    if (m2 := matcher.match(t, intake.present, intake.absent)).strength >= 1),
                   None)
        if hit is None:
            hit = next((t for t in m.when_any if any(normalise(t) == normalise(x)
                                                     for x in intake.tongue)), None)
            if hit is None:
                continue
            matched = hit
        else:
            matched = hit.entry
        mods: list[dict[str, Any]] = [{"remove": h} for h in m.remove if h in names]
        mods += [{"replace": a, "with": b} for a, b in m.replace if a in names]
        mods += [{"add": h} for h in m.add if h not in names]
        out.append({"when": "、".join(m.when_any), "matched": matched, "why": m.why,
                    "modifications": mods})
    return out


def draft(intake: Intake, syndrome: str, pack: ClinicPack, *, kb: Any = None,
          modifications: Sequence[Mapping[str, Any]] = (), apply_textbook: bool = False,
          days: int = 7) -> Prescription:
    s = pack.syndromes[syndrome]
    fraction, fnote = age_fraction(pack, intake.patient.age)
    base = dict(syndrome=syndrome, principle=s.principle, fraction=fraction,
                fraction_note=fnote, days=days)
    if syndrome in pack.emergency_syndromes:
        return Prescription(status="refer", formula=None, source="", lines=[], instructions=[],
                            issues=[Issue("emergency", "stop", "emergency",
                                          f"{syndrome}为急危重证（{s.principle}），须立即转诊"
                                          "急救，不出门诊处方")], **base)
    if not s.formula:
        return Prescription(status="no_formula", formula=None, source="", lines=[],
                            instructions=[],
                            issues=[Issue("no_formula", "info", "no_formula",
                                          f"知识包未收录{syndrome}的基础方；治法为{s.principle}，"
                                          "由医师选方")], **base)
    spec = pack.formulas[s.formula]
    lines, issues = [], []
    for herb, dose in spec.herbs:
        line, more = _line_for(pack, herb, dose * fraction, fraction, basis="textbook",
                               textbook=dose)
        lines.append(line)
        issues += more
    suggested = _suggestions(pack, s.formula, intake)
    applied: list[str] = []
    if apply_textbook:
        # Two textbook 加减 of one formula can combine into an incompatible pair even
        # though neither does on its own (四君子汤: 附子 for 畏寒肢冷, 法半夏 for 呕吐 —
        # 十八反). Each is checked against what the prescription already holds and
        # skipped if it would clash; the practitioner is told which and why.
        for sug in suggested:
            adding = [str(m["add"]) for m in sug["modifications"] if "add" in m]
            adding += [str(m["with"]) for m in sug["modifications"] if "replace" in m]
            present = [ln.herb for ln in lines]
            clash = next(((a, b, r) for a in adding for b in present
                          if (r := pack.incompatibility(a, b)[0])), None)
            if clash is not None:
                a, b, rule = clash
                issues.append(Issue(f"modification_skipped:{a}", "warn",
                                    "modification_skipped",
                                    f"未应用整条加减「加{'、'.join(adding)}」（{sug['why']}）："
                                    f"{rule}，{a}与方中{b}不宜同用；如确需使用由医师决定",
                                    (a, b)))
                sug["skipped"] = f"{rule}：与方中{b}不宜同用"
                continue
            lines, more, done = _apply(pack, lines, sug["modifications"], fraction)
            issues += more
            applied += [f"{d}（{sug['why']}）" for d in done]
    if modifications:
        lines, more, done = _apply(pack, lines, modifications, fraction)
        issues += more
        applied += done
    issues += check(lines, intake, pack, kb=kb)
    instructions = instructions_for(lines, days)
    status = "blocked" if any(i.severity == "stop" for i in issues) else "draft"
    return Prescription(status=status, formula=s.formula, source=spec.source, lines=lines,
                        issues=_dedupe(issues), instructions=instructions, applied=applied,
                        suggested=suggested, formula_note=spec.note, **base)


def instructions_for(lines: Sequence[Line], days: int) -> list[str]:
    """How to take it: the standard decoction, plus each herb's own 煎服 note.

    Recomputed from the lines in hand, never carried over, so a herb the practitioner
    removed does not leave its 另煎、先煎 or 后下 note behind on what is signed.
    """
    out = ["每日1剂，水煎2次，分早晚温服（常规煎服法，医师可改）",
           f"先开{days}剂，复诊后再定"]
    for ln in lines:
        for n in ln.notes:
            out.append(f"{ln.herb}：{n}")
    return out


def _dedupe(issues: list[Issue]) -> list[Issue]:
    seen, out = set(), []
    for i in issues:
        if i.id not in seen:
            seen.add(i.id)
            out.append(i)
    order = {s: k for k, s in enumerate(SEVERITIES)}
    return sorted(out, key=lambda i: (order[i.severity], i.id))


def check(lines: Sequence[Line], intake: Intake, pack: ClinicPack, *, kb: Any = None
          ) -> list[Issue]:
    """Every safety issue with these lines for this person."""
    kb = kb if kb is not None else _kb()
    patient = intake.patient
    issues: list[Issue] = []
    herbs = [ln.herb for ln in lines]
    fraction, _ = age_fraction(pack, patient.age)
    if patient.age < 1:
        issues.append(Issue("infant", "block", "infant",
                            "婴儿用药：每味药剂量须由医师逐一确定（草案仅按成人量1/6折算）"))
    elif patient.age >= 65:
        issues.append(Issue("elderly", "info", "elderly",
                            "老年患者：用量一般酌减，由医师据体质确定", source="《中药学》"))
    entities = {h: _resolve(kb, h) for h in herbs}
    for ln in lines:
        spec = pack.herbs.get(ln.herb)
        if spec is None:
            issues.append(Issue(f"unknown_herb:{ln.herb}", "block", "unknown_herb",
                                f"{ln.herb}不在知识包中：无剂量范围、毒性与妊娠资料，须医师自行核对",
                                (ln.herb,)))
            continue
        lo, hi = spec.low * fraction, spec.high * fraction
        toxic = spec.toxicity in _TOXIC
        if ln.dose_g > hi + 1e-9 and ln.basis == "practitioner":
            issues.append(Issue(f"dose_above_range:{ln.herb}", "block" if toxic else "warn",
                                "dose_above_range",
                                f"{ln.herb} {ln.dose_g:g} g 超过《药典》上限 {hi:g} g"
                                + ("（有毒药材）" if toxic else ""), (ln.herb,),
                                "中华人民共和国药典 2020年版"))
        if ln.dose_g < lo - 1e-9:
            issues.append(Issue(f"dose_below_range:{ln.herb}", "info", "dose_below_range",
                                f"{ln.herb} {ln.dose_g:g} g 低于《药典》常用量下限 {lo:g} g",
                                (ln.herb,), "中华人民共和国药典 2020年版"))
        entity = entities.get(ln.herb)
        kb_notes = []
        if entity is not None:
            for rec in kb.safety_for(entity.id):
                if rec.kind == "toxicity" and rec.management:
                    kb_notes.append(rec.management)
        if toxic:
            note = "；".join(x for x in (spec.note, *kb_notes) if x)
            issues.append(Issue(f"toxic:{ln.herb}", "warn", "toxic",
                                f"{ln.herb}{spec.toxicity}" + (f"：{note}" if note else ""),
                                (ln.herb,), "中华人民共和国药典 2020年版"))
        if patient.breastfeeding and toxic:
            issues.append(Issue(f"breastfeeding:{ln.herb}", "warn", "breastfeeding",
                                f"哺乳期：{ln.herb}{spec.toxicity}，须医师权衡", (ln.herb,)))
        _pregnancy(issues, ln.herb, spec.pregnancy, entity, kb, patient)
    _incompatibility(issues, herbs, entities, kb, pack)
    if intake.allergies:
        for h in herbs:
            for a in intake.allergies:
                if normalise(h) in a or (len(a) >= 2 and a in normalise(h)):
                    issues.append(Issue(f"allergy:{h}", "stop", "allergy",
                                        f"过敏史记录“{a}”：处方含{h}", (h,)))
    if intake.medications is not None:
        meds = [normalise(m) for m in intake.medications]
        for rule in pack.drug_interactions:
            present = [h for h in herbs if h in rule["herbs"]]
            drugs = [d for d in rule["drugs"] if any(normalise(d) in m for m in meds)]
            if present and drugs:
                issues.append(Issue(f"interaction:{'+'.join(present)}:{drugs[0]}", "warn",
                                    "interaction",
                                    f"{'、'.join(present)}与正在使用的{'、'.join(drugs)}："
                                    f"{rule['risk']}", tuple(present),
                                    str(rule.get("source") or "")))
    if intake.conditions:
        conds = [normalise(c) for c in intake.conditions]
        for rule in pack.condition_cautions:
            present = [h for h in herbs if h in rule["herbs"]]
            hit = [c for c in rule["conditions"] if any(normalise(c) in x for x in conds)]
            if present and hit:
                issues.append(Issue(f"condition:{'+'.join(present)}:{hit[0]}", "warn",
                                    "condition",
                                    f"既往{'、'.join(hit)}：{'、'.join(present)}，{rule['why']}",
                                    tuple(present), str(rule.get("source") or "")))
    return _dedupe(issues)


def _pregnancy(issues: list[Issue], herb: str, pack_note: str, entity: Any, kb: Any,
               patient) -> None:
    kb_note = ""
    if entity is not None:
        for rec in kb.safety_for(entity.id):
            if rec.kind == "contraindication" and "孕" in (rec.population + rec.description):
                kb_note = "禁用" if "禁" in rec.description else "慎用"
    notes = [n for n in (pack_note, kb_note) if n]
    if not notes:
        return
    level = "禁用" if "禁用" in notes else "慎用"
    differ = (f"（来源不一：《药典》2020年版“孕妇{pack_note or '未注'}”，种子知识库"
              f"“孕妇{kb_note}”；取较严者）" if pack_note and kb_note and pack_note != kb_note
              else "")
    if patient.pregnant:
        issues.append(Issue(f"pregnancy:{herb}", "stop" if level == "禁用" else "block",
                            "pregnancy", f"妊娠：{herb}孕妇{level}{differ}", (herb,),
                            "中华人民共和国药典 2020年版"))
    elif patient.pregnant is None and patient.childbearing_age:
        issues.append(Issue(f"pregnancy_unknown:{herb}", "block", "pregnancy_unknown",
                            f"妊娠状态未知：{herb}孕妇{level}{differ}，须先确认是否妊娠",
                            (herb,)))


def _incompatibility(issues: list[Issue], herbs: list[str], entities: Mapping[str, Any],
                     kb: Any, pack: ClinicPack) -> None:
    seen: set[frozenset[str]] = set()
    for i, a in enumerate(herbs):
        for b in herbs[i + 1:]:
            rule, source = pack.incompatibility(a, b)
            if rule:
                seen.add(frozenset((a, b)))
                issues.append(Issue(f"incompatible:{'+'.join(sorted((a, b)))}", "block",
                                    "incompatible", f"{rule}：{a}与{b}不宜同用", (a, b),
                                    source))
    ids = {h: e.id for h, e in entities.items() if e is not None}
    if len(ids) < 2:
        return
    name_of = {}
    for h, e in entities.items():
        if e is not None:
            name_of[e.id] = h
            name_of[getattr(e, "herb_id", e.id)] = h
    for conflict in kb.check_compatibility(list(ids.values())):
        a = name_of.get(conflict.first_id, conflict.first_id)
        b = name_of.get(conflict.second_id, conflict.second_id)
        pair = frozenset((a, b))
        if pair in seen or a == b:
            continue
        seen.add(pair)
        issues.append(Issue(f"incompatible:{'+'.join(sorted((a, b)))}", "block", "incompatible",
                            f"{conflict.record.description}（{a}、{b}）", (a, b),
                            "种子知识库"))
