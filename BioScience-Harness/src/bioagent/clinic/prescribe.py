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
child-bearing age needs it asked first); allergies (a stop when the entry names the herb or
another processed form of it, the practitioner's reason when it only may: the materia table
gives the two one source, a near name, or 花粉); drug interactions with the
recorded medications, and which of them the rules do not recognise (so an unchecked
medicine is not mistaken for a safe one); cautions for recorded conditions; toxic herbs and
their processing and decoction notes; doses outside the (age-scaled) range. Where the pack
and the knowledge base disagree on a safety point (附子 in pregnancy: 药典 2020 慎用, the
seed's 《中药学》 reading 禁用), the stricter applies and the report says the sources differ.

Severities: ``stop`` — cannot be signed as it stands; ``block`` — can be signed only with
the practitioner's recorded reason; ``warn`` and ``info`` — shown. Nothing here decides;
a draft is a starting point for a licensed practitioner, who signs or rejects it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
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


def _identity(kb: Any, name: str) -> set[str]:
    """The herb a whole written name denotes by the seed knowledge base's names (黄耆, 北芪 and
    绵芪 are 黄芪, 芍药 is 白芍, 国老 is 甘草); a processed form is its herb, whether the
    knowledge base lists it (炙甘草) or a processing word stands before one of its names
    (炙黄耆, 酒当归). This is drug identity. The materia table's names are not: it groups the
    names of a source, so its 鸡内金 also lists 鸡蛋 and 乌鸡, and its 海藻 海带 and 昆布
    (``_materia``)."""
    from ..sources.materia import PROCESSING_WORDS
    entity = _resolve(kb, name)
    for word in sorted(PROCESSING_WORDS, key=len, reverse=True):
        if entity is not None:
            break
        if name.startswith(word) and len(name) > len(word):
            entity = _resolve(kb, name[len(word):])
    if entity is None:
        return set()
    herb = getattr(entity, "herb_id", "") or entity.id
    return {herb} if herb.startswith("herb.") else set()


def _materia(name: str) -> Any:
    """The materia table's entry for a whole written name, or None. The table files names
    by source (species or product), for network pharmacology: an entry's names are of one
    source, not always of one drug (鸡内金 with 鸡蛋, 海藻 with 海带)."""
    from ..sources.materia import MATERIA, resolve_name
    try:
        drug = resolve_name(name)
    except Exception:                                            # noqa: BLE001
        return None
    return MATERIA.get(drug) if drug else None


def _processed(longer: str, shorter: str) -> bool:
    """``longer`` is ``shorter`` with a processing word in front: 炙黄芪 of 黄芪, 熟地黄 of
    地黄, 制附子 of 附子. Not a word of origin or colour (白附子 is not 附子), and not when the
    materia table files ``longer`` under another drug, unless that drug is named ``longer``:
    熟地黄 is the table's own entry for processed 地黄, but its 炮姜 is under 干姜, not 姜
    (which it files with 生姜)."""
    from ..sources.materia import PROCESSING_WORDS
    if not (len(longer) > len(shorter) and longer.endswith(shorter)
            and longer[:-len(shorter)] in PROCESSING_WORDS):
        return False
    lm, sm = _materia(longer), _materia(shorter)
    return lm is None or sm is None or lm.id == sm.id or lm.chinese == longer


#: Words of quality or state that the materia table also strips before a name (鲜地黄,
#: 干地黄). They are dropped only where the table files both names under one entry: 鲜生地 is
#: 生地, but 干姜, which the table files apart, is not 姜.
_STATE_WORDS = ("鲜", "干", "真", "好", "上", "嫩", "新", "陈", "细", "净")


def _base(kb: Any, name: str) -> str:
    """``name`` without the processing word, or the word of state, in front of a known name
    (熟地黄 → 地黄, 炙黄耆 → 黄耆, 鲜生地 → 生地), or ``name`` itself."""
    from ..sources.materia import PROCESSING_WORDS
    for word in sorted(PROCESSING_WORDS, key=len, reverse=True):
        rest = name[len(word):]
        if (name.startswith(word) and rest and _processed(name, rest)
                and (_materia(rest) is not None or _resolve(kb, rest) is not None)):
            return rest
    whole = _materia(name)
    for word in _STATE_WORDS:
        rest = name[len(word):]
        if (whole is not None and name.startswith(word) and rest
                and (part := _materia(rest)) is not None and part.id == whole.id):
            return rest
    return name


@lru_cache(maxsize=1)
def _materia_names() -> frozenset[str]:
    from ..sources.materia import MATERIA
    return frozenset(n for e in MATERIA.values() for n in (e.chinese, *e.aliases))


def _forms(kb: Any, name: str) -> tuple[list[str], list[str]]:
    """(``name`` and what it is a processed form of, those plus the drug's other processed
    forms that the tables name). 熟地黄 gives ([熟地黄, 地黄], [熟地黄, 地黄, 生地黄, …]):
    processing leaves the herb what it is, so 生地黄 and 熟地黄 are both 地黄, and 生地, the
    short name of 生地黄, reaches 熟地黄 through it."""
    from ..sources.materia import PROCESSING_WORDS
    forms = [name]
    while (b := _base(kb, forms[-1])) != forms[-1] and b not in forms:
        forms.append(b)
    wider = list(forms)
    for b in forms:
        for word in PROCESSING_WORDS:
            s = word + b
            if (s not in wider and _processed(s, b)
                    and (s in _materia_names() or _resolve(kb, s) is not None)):
                wider.append(s)
    return forms, wider


#: The words before the name, with or without 过敏 after it: a word of time (曾, 曾经, 既往,
#: 以往) and 对 or 有. 对 is read on its own: the intake splits 对黄耆、人参过敏 at 、 into 对黄耆
#: and 人参过敏, so the first name of a list has 对 and no 过敏. No name the tables know
#: starts with one of these words.
_ALLERGY_LEAD = re.compile(r"(?:既往|以往|曾经|曾)?(?:对|有)?")
#: The label before the name: 过敏, 过敏史, 过敏原 or 过敏源, 过敏药物, 过敏物, 过敏反应, and
#: 药物过敏(史) or 食物过敏(史) (过敏：黄耆; the intake drops the colon, so 过敏黄耆). 过敏性
#: is the adjective of a condition (过敏性鼻炎), not a label.
_ALLERGY_LABEL = re.compile(r"(?:药物|食物)?过敏(?:史|原|源|药物|食物|药|物|反应)?(?!性)")
#: What is left of a record that there is none: 无, 否认 (否认过敏史), 过敏史：无, 无药物过敏史,
#: 否认食物及药物过敏史, NKDA. The whole remainder must be such words, so a drug whose name
#: starts with one (没药, 无食子, 无灰酒) is still that drug.
_NO_ALLERGY = re.compile(
    r"(?:无|否认|没有|暂无|未|不|none|no|nil|nka|nkda)+"
    r"(?:明显|已知|特殊|任何|发现|药物|食物|药|及|和|与)*")
#: What is left of a record that names no allergen without denying one: 过敏体质 (an atopic
#: constitution), 过敏史不详 or 不明 (not known). 过敏反应 is a label with nothing after it.
_NO_ALLERGEN = re.compile(r"体质|不详|不明|未知|未详|待查")


def _allergen(entry: str) -> str:
    """The name an allergy record names, or "" when it names none.

    The words a history writes around the name are dropped, with either colon and any
    whitespace (the intake has already split a list at 、，,；;。 and line breaks):

    * before it, 对 or 有, after a word of time or not: 对黄耆 (the first name of
      对黄耆、人参过敏), 有黄耆过敏史, 曾对黄耆过敏, 既往黄耆过敏;
    * before it, a label: 过敏：黄耆, 过敏史：黄耆, 过敏原：黄耆, 过敏源：黄耆, 过敏药物：黄耆,
      过敏物：黄耆, 药物过敏史：黄耆;
    * after it, 过敏 and what follows: 黄耆过敏, 黄耆过敏史, 黄耆过敏反应, 黄耆过敏（皮疹）,
      对黄耆有过敏史.

    A record of no allergy (无, 否认过敏史, 过敏史：无) or of no allergen (过敏体质, 过敏反应,
    过敏史不详) names none. Traditional characters are not folded (黃耆 stays 黃耆): the
    repository has no conversion table."""
    text = normalise(entry)                     # NFKC, no whitespace or punctuation
    text = text[_ALLERGY_LEAD.match(text).end():]
    if label := _ALLERGY_LABEL.match(text):     # 过敏史：对黄耆过敏 reads on as 对黄耆过敏
        text = text[label.end():]
        text = text[_ALLERGY_LEAD.match(text).end():]
    if (at := text.find("过敏")) > 0:           # no name the tables know holds 过敏, or ends in 有
        text = text[:at].removesuffix("有")
    return ("" if not text or _NO_ALLERGY.fullmatch(text) or _NO_ALLERGEN.fullmatch(text)
            else text)


#: Names that an allergy record uses for something other than the drug the materia table
#: files them under. In a prescription 花粉 is 天花粉's short name; in an allergy history 花粉
#: (花粉过敏, 对花粉过敏) is pollen, the most common entry of all. Such an entry is checked
#: against the drug it may name only, with a block that asks which was meant, and not
#: carried to that drug's species-mates (瓜蒌, from the same Trichosanthes).
_ALLERGY_HOMONYMS: Mapping[str, str] = {"花粉": "植物花粉"}


_RANK = {"name": 3, "kb": 2, "near": 1, "": 0}


def _one_drug(kb: Any, x: str, y: str, *, entry: str | None = None) -> str:
    """How the names ``x`` (the allergy's) and ``y`` (the herb's) are one drug: ``"name"``
    (the same name, or one inside the other where the name tables do not give them to
    different drugs, or the longer is the shorter processed), ``"kb"`` (the seed knowledge
    base gives both to one herb), ``"near"`` (one inside the other, but the tables give them
    to different drugs and neither is the other processed) or ``""``.

    With ``entry``, the allergy record as written is searched for ``y`` as a whole (人参过敏史
    names 人参). Without it, two names are compared, and a name of one character is not
    looked for inside another (the 姜 of 生姜 is not in 沙姜)."""
    x_kb, y_kb = _identity(kb, x), _identity(kb, y)
    same_kb = bool(x_kb & y_kb)
    if entry is not None:
        inside = bool(y) and (y in entry or (len(entry) >= 2 and entry in y))
    else:
        inside = x == y or (len(x) >= 2 and len(y) >= 2 and (x in y or y in x))
    if inside and not same_kb:
        x_m, y_m = _materia(x), _materia(y)
        if (((x_m is not None and y_m is not None and x_m.id != y_m.id) or (x_kb and y_kb))
                and not (_processed(x, y) or _processed(y, x))):
            return "near"
    if inside:
        return "name"
    return "kb" if same_kb else ""


def _allergies(herbs: Sequence[str], allergies: Sequence[str], kb: Any) -> list[Issue]:
    """Each herb against each recorded allergy.

    ``stop`` (cannot be signed) when the entry names the herb: the same name, one inside the
    other (人参过敏史 and 人参, 黄芪 and 炙黄芪), or a name the seed knowledge base gives the
    same herb (黄耆, 北芪 and 黄芪; 芍药 and 白芍; 国老 and 甘草). Processing leaves the herb
    what it is, so the processed forms of one drug stop each other: 生地黄 and 熟地黄 are both
    地黄, and 生地 (生地黄) and 鲜生地 reach 熟地黄 the same way.

    ``block`` (can be signed only with the practitioner's recorded reason) when the entry
    may be the herb but the names cannot tell:

    * ``allergy_source``: the materia table puts the two under one source. The entry is
      another of the names it files with the herb (薯蓣 and 山药, 双花 and 金银花, but also
      鸡蛋 and 鸡内金, 海带 and 海藻), or the two drugs come from one species (芍药 and 赤芍,
      Paeonia lactiflora). The table groups names by source, not by drug, so the message
      says they share a source and asks which was meant; it never says they are one herb.
    * ``allergy_near_name``: one name inside the other, where the name tables give the two
      to different drugs and neither is the other processed: a near name is often another
      drug (白附子 and 附子, 核桃仁 and 桃仁, 川牛膝 and 牛膝), but 茱萸 may mean either.
    * ``allergy_homonym``: an entry an allergy record uses for something else (花粉 is
      pollen), against the drug the table files it under (天花粉) only.
    """
    # a record that names nothing (无, 否认过敏史, 过敏史：无) is no allergy, and is not
    # searched for herb names either
    entries = [(a, name, _forms(kb, name), _materia(name))
               for a in allergies for name in (_allergen(a),) if name]
    out: list[Issue] = []
    for h in herbs:
        h_m, h_n = _materia(h), normalise(h)
        h_forms, h_wider = _forms(kb, h_n)
        for a, name, (a_forms, a_wider), a_m in entries:
            recorded = f"过敏史记录“{a}”：处方含{h}"
            both = a_m is not None and h_m is not None
            if name in _ALLERGY_HOMONYMS:
                if (both and a_m.id == h_m.id) or name in h_n:
                    out.append(Issue(f"allergy_homonym:{h}", "block", "allergy_homonym",
                                     f"{recorded}。过敏史中的“{name}”通常指"
                                     f"{_ALLERGY_HOMONYMS[name]}，但处方中{h}也简称“{name}”，"
                                     f"请核实所记过敏是否为{h}", (h,), "药材名表（materia）"))
                continue
            # the record as written (人参过敏史), and the name in it (地黄 of 地黄过敏)
            direct = max(_one_drug(kb, name, h_n, entry=a), _one_drug(kb, name, h_n),
                         key=_RANK.__getitem__)
            processed = direct not in ("name", "kb") and any(
                _one_drug(kb, x, y) in ("name", "kb")
                for xs, ys in ((a_forms, h_wider), (a_wider, h_forms))
                for x in xs for y in ys)
            if direct == "name":
                out.append(Issue(f"allergy:{h}", "stop", "allergy", recorded, (h,)))
            elif direct == "kb":
                out.append(Issue(f"allergy:{h}", "stop", "allergy",
                                 f"{recorded}（{name}与{h}为同一药材）", (h,), "种子知识库"))
            elif processed:
                base = h_forms[-1] if len(h_forms) > 1 or len(a_forms) == 1 else a_forms[-1]
                out.append(Issue(f"allergy:{h}", "stop", "allergy",
                                 f"{recorded}（{name}与{h}同为{base}或其炮制品）", (h,)))
            elif both and a_m.id == h_m.id:
                out.append(Issue(f"allergy_source:{h}", "block", "allergy_source",
                                 f"{recorded}。药材名表将{name}与{h}同列于“{h_m.chinese}”条下，"
                                 "按该表二者同源（同一物种/来源），未必是同一种药，请核实", (h,),
                                 "药材名表（materia）"))
            elif both and (shared := [s for s in a_m.species_names if s in h_m.species_names]):
                out.append(Issue(f"allergy_source:{h}", "block", "allergy_source",
                                 f"{recorded}。{name}与{h}同源（同一物种：{'、'.join(shared)}），"
                                 "未必是同一种药，请核实", (h,), "药材名表（materia）"))
            elif direct == "near":
                which = (f"药材名表将{name}"
                         + ("" if a_m.chinese == name else f"（{a_m.chinese}）")
                         + f"与{h}列为不同药材" if both
                         else f"种子知识库将{name}与{h}列为不同药材")
                out.append(Issue(f"allergy_near_name:{h}", "block", "allergy_near_name",
                                 f"{recorded}。二者名称相近，但{which}，请核实所记过敏为何药",
                                 (h,)))
    return out


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
        issues += _allergies(herbs, intake.allergies, kb)
    if intake.medications is not None:
        meds = [normalise(m) for m in intake.medications]
        named = _named_medicines(pack, meds)
        for rule in pack.drug_interactions:
            present = [h for h in herbs if h in rule["herbs"]]
            drugs = [d for d in rule["drugs"] if any(normalise(d) in m for m in meds)]
            # a medicine recorded by name (呋塞米, Lasix) is read as its ATC class (利尿剂);
            # one in two classes (氢氯噻嗪: 利尿剂 and 降压药) by each rule that names either
            by_name = [(n, next(w for w in words if w in rule["drugs"]), atc)
                       for n, words, atc in named if any(w in rule["drugs"] for w in words)]
            if present and (drugs or by_name):
                labels = drugs + [f"{n}（{word}，ATC {atc}）" for n, word, atc in by_name]
                source = str(rule.get("source") or "")
                if by_name:
                    source += f"；药物分类：{pack.drug_classes_source}"
                issues.append(Issue(f"interaction:{'+'.join(present)}:"
                                    f"{(drugs or [n for n, _, _ in by_name])[0]}", "warn",
                                    "interaction",
                                    f"{'、'.join(present)}与正在使用的{'、'.join(labels)}："
                                    f"{rule['risk']}", tuple(present), source))
        # "no interaction" is told from "not recognised": a medicine neither a rule's class
        # word nor the ATC mapping names was not checked at all (美托洛尔, 螺内酯)
        unknown = _unrecognised(pack, intake.medications)
        if unknown:
            issues.append(Issue("medications_unrecognised", "info", "medications_unrecognised",
                                f"未识别的用药：{'、'.join(unknown)}。知识包的相互作用规则与药物"
                                "分类均未收录，未核查其与本方的相互作用（未提示不等于无相互作用）",
                                (), pack.drug_classes_source))
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


def _named_medicines(pack: ClinicPack, meds: Sequence[str]
                     ) -> list[tuple[str, tuple[str, ...], str]]:
    """(name, class words, ATC code) of each medicine the pack's ATC mapping finds in the
    recorded medications, once per ATC code. A name inside a longer one found in the same
    record is that one's (泼尼松 in 泼尼松龙 is prednisolone, not prednisone)."""
    out: list[tuple[str, tuple[str, ...], str]] = []
    codes: set[str] = set()
    for m in meds:
        found = [n for n in pack.drug_classes if normalise(n) and normalise(n) in m]
        for n in found:
            words, atc = pack.drug_classes[n]
            if atc in codes or any(n != o and normalise(n) in normalise(o) for o in found):
                continue
            codes.add(atc)
            out.append((n, words, atc))
    return out


#: A medications entry that records there are none: 无, 否认, 无特殊用药, 未服药, none. The
#: template asks for [] then, but 无 is how it is usually written, and it is no medicine.
_NO_MEDICATION = re.compile(
    r"(无|否认|没有|暂无|未|不|none|no|nil|na|n/a|nkda)+"
    r"(特殊|其他|其它|长期|规律|规则|常规|任何|服用|使用|口服|服|用|吃|西药|中药|药物|药|史"
    r"|medications?|medicines?|meds?|drugs?)*")


def _unrecognised(pack: ClinicPack, medications: Sequence[str]) -> list[str]:
    """The recorded medications that no interaction rule's class word and no name in the
    ATC mapping is found in: nothing was checked for them. An entry that says there are
    none (无, 否认用药史) is not a medication."""
    known = {normalise(str(d)) for r in pack.drug_interactions for d in r.get("drugs") or ()}
    known |= {normalise(n) for n in pack.drug_classes}
    known.discard("")
    return [m for m in medications
            if not _NO_MEDICATION.fullmatch(normalise(m))
            and not any(k in normalise(m) for k in known)]


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
