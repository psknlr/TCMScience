"""The clinical knowledge pack: syndrome criteria, formulas, herb limits and safety rules.

The pack is data (``bioagent/data/clinic_pack.json``), loaded and checked here. Its
entries are transcriptions of named sources — syndrome criteria from 《中医诊断学》 and
GB/T 16751.2-2021, formulas and doses from 《方剂学》, herb dose ranges, toxicity,
processing and pregnancy notes from 《中华人民共和国药典》2020年版一部 — and its
``review_status`` says whether a licensed practitioner has reviewed them. Until one has,
every output built on the pack says so.

``check`` holds the pack to what the code relies on: every formula's herbs have limits,
every syndrome's formula exists, every finding a criterion scores is on the intake form,
no base formula contains a pair the pack itself calls incompatible, every medicine mapped to
drug-interaction classes is mapped to exactly the classes whose ATC groups hold its ATC code
(a thiazide to both 利尿剂 and 降压药), every herb alias (``herb_aliases``, read by the allergy
check) names a pack herb, cites its source and is given to one herb only, and every herb
source record (``herb_sources``: the species of a herb the materia table lacks) names a pack
herb and cites its source, every herb's pinyin (``herb_pinyin``) is of a pack herb and in Latin
letters, and every English common name (``herb_common_names``) gives its species and cites its
source. A pack that fails is not loaded.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

__all__ = ["ClinicPack", "Syndrome", "FormulaSpec", "Modification", "HerbSpec", "RedFlagRule",
           "VitalRule", "load_pack", "check", "PackError", "DEFAULT_PACK"]

DEFAULT_PACK = Path(__file__).resolve().parent.parent / "data" / "clinic_pack.json"


class PackError(ValueError):
    """The knowledge pack is malformed or inconsistent."""


@dataclass(frozen=True)
class Syndrome:
    name: str
    main: tuple[str, ...]
    secondary: tuple[str, ...]
    tongue: tuple[str, ...]
    pulse: tuple[str, ...]
    principle: str
    formula: str | None
    parent: str | None = None


@dataclass(frozen=True)
class Modification:
    """A textbook 加减: when any of ``when_any`` is present, add, remove or replace herbs."""

    when_any: tuple[str, ...]
    add: tuple[str, ...] = ()
    remove: tuple[str, ...] = ()
    replace: tuple[tuple[str, str], ...] = ()
    why: str = ""


@dataclass(frozen=True)
class FormulaSpec:
    name: str
    source: str
    herbs: tuple[tuple[str, float], ...]
    note: str = ""
    modifications: tuple[Modification, ...] = ()


@dataclass(frozen=True)
class HerbSpec:
    name: str
    low: float
    high: float
    toxicity: str = ""
    note: str = ""
    pregnancy: str = ""          # "" | 慎用 | 禁用


@dataclass(frozen=True)
class RedFlagRule:
    id: str
    terms: tuple[str, ...]
    patterns: tuple[re.Pattern, ...]
    why: str
    action: str


@dataclass(frozen=True)
class VitalRule:
    id: str
    field: str
    low: float | None
    high: float | None
    unit: str
    why: str


@dataclass(frozen=True)
class ClinicPack:
    version: str
    review_status: str
    about: str
    reference: Mapping[str, str]
    syndromes: Mapping[str, Syndrome]
    formulas: Mapping[str, FormulaSpec]
    herbs: Mapping[str, HerbSpec]
    incompatible: tuple[tuple[str, str, str], ...]      # (herb, herb, 十八反 | 十九畏)
    drug_interactions: tuple[Mapping[str, Any], ...]
    condition_cautions: tuple[Mapping[str, Any], ...]
    red_flags: tuple[RedFlagRule, ...]
    vital_rules: tuple[VitalRule, ...]
    vital_source: str
    paediatric: tuple[tuple[float, float, float, str], ...]
    emergency_syndromes: frozenset[str]
    inquiry_groups: Mapping[str, tuple[str, ...]]
    synonyms: Mapping[str, str]
    digest: str
    incompatible_sources: Mapping[str, str] = field(default_factory=dict)   # rule -> source
    #: a medicine's name (呋塞米, Lasix) -> (the rule class words it is read as, its ATC code);
    #: one in two classes has both (氢氯噻嗪: 利尿剂 and 降压药)
    drug_classes: Mapping[str, tuple[tuple[str, ...], str]] = field(default_factory=dict)
    atc_groups: Mapping[str, tuple[str, ...]] = field(default_factory=dict)  # class -> groups
    drug_classes_source: str = ""
    path: str = ""
    extra: Mapping[str, Any] = field(default_factory=dict)
    #: another name of a pack herb -> (the herb, the source that gives the name): 夜交藤 ->
    #: 首乌藤, 川黄柏 -> 黄柏. Read by the allergy check; a name no source is cited for is not
    #: listed.
    herb_aliases: Mapping[str, tuple[str, str]] = field(default_factory=dict)
    #: a pack herb the materia table does not hold -> (its source species, the source):
    #: 首乌藤 is the stem of the plant whose root is 何首乌, Polygonum multiflorum
    herb_sources: Mapping[str, tuple[tuple[str, ...], str]] = field(default_factory=dict)
    #: the Latin name of a herb the materia table does not hold: 首乌藤, Polygoni Multiflori
    #: Caulis (read by the allergy check, in this and the older word order)
    herb_latin: Mapping[str, str] = field(default_factory=dict)
    #: each pack herb's name in pinyin (人参: ren shen), read by the allergy check; and where
    #: the pinyin comes from
    herb_pinyin: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    herb_pinyin_source: str = ""
    #: English common names, each with the species it names and its source: ginger, Zingiber
    #: officinale. The allergy check reads them as the species, never as one herb
    herb_common_names: tuple[tuple[tuple[str, ...], tuple[str, ...], str], ...] = ()

    @property
    def reviewed(self) -> bool:
        return self.review_status.lower().startswith("reviewed")

    def finding_terms(self) -> set[str]:
        out: set[str] = set()
        for s in self.syndromes.values():
            out.update(s.main)
            out.update(s.secondary)
        return out

    def tongue_terms(self) -> list[str]:
        return sorted({t for s in self.syndromes.values() for t in s.tongue})

    def pulse_terms(self) -> list[str]:
        return sorted({t for s in self.syndromes.values() for t in s.pulse})

    def incompatible_with(self, herb: str) -> list[str]:
        return sorted({b if a == herb else a for a, b, _ in self.incompatible if herb in (a, b)})

    def incompatibility(self, first: str, second: str) -> tuple[str, str]:
        """(the rule that forbids this pair, its source), or ("", "")."""
        for a, b, rule in self.incompatible:
            if {a, b} == {first, second}:
                return rule, self.incompatible_sources.get(rule, "")
        return "", ""


def _tuple(v: Any) -> tuple[str, ...]:
    return tuple(str(x) for x in (v or ()))


def load_pack(path: str | Path | None = None) -> ClinicPack:
    p = Path(path) if path else DEFAULT_PACK
    raw = p.read_bytes()
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PackError(f"{p}: not a JSON knowledge pack ({exc})") from exc
    try:
        pack = _build(data, hashlib.sha256(raw).hexdigest(), str(p))
    except (KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, PackError):
            raise
        raise PackError(f"{p}: malformed knowledge pack ({exc!r})") from exc
    problems = check(pack)
    if problems:
        raise PackError(f"{p}: the knowledge pack is inconsistent: " + "; ".join(problems[:10]))
    return pack


def _build(data: Mapping[str, Any], digest: str, path: str) -> ClinicPack:
    syndromes = {
        name: Syndrome(name=name, main=_tuple(s["main"]), secondary=_tuple(s.get("secondary")),
                       tongue=_tuple(s.get("tongue")), pulse=_tuple(s.get("pulse")),
                       principle=str(s.get("principle") or ""), formula=s.get("formula"),
                       parent=s.get("parent"))
        for name, s in data["syndromes"].items()}
    formulas = {
        name: FormulaSpec(name=name, source=str(f.get("source") or ""),
                          herbs=tuple((str(h), float(d)) for h, d in f["herbs"]),
                          note=str(f.get("note") or ""),
                          modifications=tuple(
                              Modification(when_any=_tuple(m["when_any"]), add=_tuple(m.get("add")),
                                           remove=_tuple(m.get("remove")),
                                           replace=tuple((str(a), str(b))
                                                         for a, b in m.get("replace") or ()),
                                           why=str(m.get("why") or ""))
                              for m in f.get("modifications") or ()))
        for name, f in data["formulas"].items()}
    herbs = {
        name: HerbSpec(name=name, low=float(h["range"][0]), high=float(h["range"][1]),
                       toxicity=str(h.get("toxicity") or ""), note=str(h.get("note") or ""),
                       pregnancy=str(h.get("pregnancy") or ""))
        for name, h in data["herbs"].items()}
    flags = tuple(
        RedFlagRule(id=str(r["id"]), terms=_tuple(r.get("any")),
                    patterns=tuple(re.compile(x, re.IGNORECASE) for x in r.get("patterns") or ()),
                    why=str(r["why"]), action=str(r.get("action") or "refer"))
        for r in data["red_flags"])
    classes = data.get("drug_classes") or {}
    named: dict[str, tuple[tuple[str, ...], str]] = {}
    for d in classes.get("drugs") or ():
        words = (d["class"],) if isinstance(d["class"], str) else d["class"]
        entry = (tuple(dict.fromkeys(str(w) for w in words)), str(d["atc"]))
        if not entry[0]:
            raise PackError(f"drug_classes: {list(_tuple(d['names']))} are read as no class")
        for n in _tuple(d["names"]):
            if named.setdefault(n, entry) != entry:
                raise PackError(f"drug_classes: {n} is listed as {named[n]} and as {entry}")
    aliases: dict[str, tuple[str, str]] = {}
    for a in data.get("herb_aliases") or ():
        entry = (str(a["herb"]), str(a.get("source") or ""))
        for name in _tuple(a["aliases"]):
            if aliases.setdefault(name, entry)[0] != entry[0]:
                raise PackError(f"herb_aliases: {name} is given to {aliases[name][0]} and to "
                                f"{entry[0]}")
    sources = {str(r["herb"]): (_tuple(r.get("species")), str(r.get("source") or ""))
               for r in data.get("herb_sources") or ()}
    latin = {str(r["herb"]): str(r["latin"]) for r in data.get("herb_sources") or ()
             if r.get("latin")}
    pinyin_doc = data.get("herb_pinyin") or {}
    pinyin = {str(h): _tuple(p) for h, p in (pinyin_doc.get("names") or {}).items()}
    common = tuple((_tuple(c.get("names")), _tuple(c.get("species")), str(c.get("source") or ""))
                   for c in data.get("herb_common_names") or ())
    vitals = data.get("vital_limits") or {}
    vital_rules = tuple(
        VitalRule(id=str(v["id"]), field=str(v["field"]),
                  low=None if v.get("low") is None else float(v["low"]),
                  high=None if v.get("high") is None else float(v["high"]),
                  unit=str(v.get("unit") or ""), why=str(v["why"]))
        for v in vitals.get("rules") or ())
    return ClinicPack(
        version=str(data.get("version") or ""),
        review_status=str(data.get("review_status") or "unreviewed"),
        about=str(data.get("about") or ""), reference=dict(data.get("reference") or {}),
        syndromes=syndromes, formulas=formulas, herbs=herbs,
        incompatible=_pairs(data.get("incompatible") or ()),
        incompatible_sources={str(r["rule"]): str(r.get("source") or "")
                              for r in data.get("incompatible") or ()},
        drug_classes=named,
        atc_groups={str(k): _tuple(v.get("atc"))
                    for k, v in (classes.get("classes") or {}).items()},
        drug_classes_source=str(classes.get("source") or ""),
        drug_interactions=tuple(data.get("drug_interactions") or ()),
        condition_cautions=tuple(data.get("condition_cautions") or ()),
        red_flags=flags, vital_rules=vital_rules, vital_source=str(vitals.get("source") or ""),
        paediatric=tuple((float(a), float(b), float(f), str(n))
                         for a, b, f, n in data.get("paediatric_fraction") or ()),
        emergency_syndromes=frozenset(_tuple(data.get("emergency_syndromes"))),
        inquiry_groups={g: _tuple(t) for g, t in (data.get("inquiry_groups") or {}).items()},
        synonyms={str(k): str(v) for k, v in (data.get("synonyms") or {}).items()},
        digest=digest, path=path, herb_aliases=aliases, herb_sources=sources,
        herb_latin=latin, herb_pinyin=pinyin,
        herb_pinyin_source=str(pinyin_doc.get("source") or ""), herb_common_names=common)


def _pairs(rules: Any) -> tuple[tuple[str, str, str], ...]:
    """Each rule {rule, a: [...], b: [...]} as every (a, b) pair it forbids.

    A herb in both lists would make a herb incompatible with itself and block every
    prescription holding it, so such a rule is refused rather than silently dropped.
    """
    out: list[tuple[str, str, str]] = []
    seen: set[frozenset[str]] = set()
    for r in rules:
        left, right = _tuple(r["a"]), _tuple(r["b"])
        both = sorted(set(left) & set(right))
        if both:
            raise PackError(f"incompatibility rule {r['rule']}: {both} are on both sides")
        for a in left:
            for b in right:
                if frozenset((a, b)) not in seen:
                    seen.add(frozenset((a, b)))
                    out.append((a, b, str(r["rule"])))
    return tuple(out)


def check(pack: ClinicPack) -> list[str]:
    """What is wrong with the pack, as messages; empty when it is consistent."""
    problems: list[str] = []
    for f in pack.formulas.values():
        for herb, dose in f.herbs:
            if herb not in pack.herbs:
                problems.append(f"formula {f.name}: herb {herb} has no dose limits")
            if dose <= 0:
                problems.append(f"formula {f.name}: {herb} has a non-positive dose")
        names = [h for h, _ in f.herbs]
        if len(names) != len(set(names)):
            problems.append(f"formula {f.name}: a herb is listed twice")
        for a, b, rule in pack.incompatible:
            if a in names and b in names:
                problems.append(f"formula {f.name}: contains the {rule} pair {a}+{b}")
        for m in f.modifications:
            for herb in (*m.add, *(new for _, new in m.replace)):
                if herb not in pack.herbs:
                    problems.append(f"formula {f.name}: modification herb {herb} has no limits")
            for herb in (*m.remove, *(old for old, _ in m.replace)):
                if herb not in names:
                    problems.append(f"formula {f.name}: modification removes {herb}, "
                                    "which the formula does not contain")
            if not m.when_any:
                problems.append(f"formula {f.name}: a modification has no trigger")
    for s in pack.syndromes.values():
        if s.formula is not None and s.formula not in pack.formulas:
            problems.append(f"syndrome {s.name}: formula {s.formula} is not in the pack")
        if s.parent is not None and s.parent not in pack.syndromes:
            problems.append(f"syndrome {s.name}: parent {s.parent} is not in the pack")
        if not s.main:
            problems.append(f"syndrome {s.name}: no main findings")
    for h in pack.herbs.values():
        if not 0 < h.low <= h.high:
            problems.append(f"herb {h.name}: dose range {h.low}-{h.high} is not a range")
        if h.pregnancy not in ("", "慎用", "禁用"):
            problems.append(f"herb {h.name}: pregnancy note {h.pregnancy!r} is not 慎用 or 禁用")
    on_form = [t for terms in pack.inquiry_groups.values() for t in terms]
    if len(on_form) != len(set(on_form)):
        dup = sorted({t for t in on_form if on_form.count(t) > 1})
        problems.append(f"intake form lists findings twice: {dup[:5]}")
    missing = pack.finding_terms() - set(on_form)
    if missing:
        problems.append(f"findings scored but not on the intake form: {sorted(missing)[:8]}")
    for k, v in pack.synonyms.items():
        if v not in pack.finding_terms():
            problems.append(f"synonym {k} -> {v}: {v} is not a scored finding")
    ages = sorted(pack.paediatric)
    for (a0, a1, frac, _), (b0, _, _, _) in zip(ages, ages[1:]):
        if a1 != b0:
            problems.append(f"paediatric bands leave a gap or overlap at age {a1}")
    if ages and (ages[0][0] != 0 or any(not 0 < f <= 1 for _, _, f, _ in ages)):
        problems.append("paediatric bands must start at age 0 with fractions in (0, 1]")
    for name in pack.emergency_syndromes:
        if name in pack.syndromes and pack.syndromes[name].formula:
            problems.append(f"emergency syndrome {name} must not carry a formula to draft")
    words = {str(d) for r in pack.drug_interactions for d in r.get("drugs") or ()}
    for name, (read_as, atc) in pack.drug_classes.items():
        groups = [k for k, gs in pack.atc_groups.items() if any(atc.startswith(g) for g in gs)]
        for word in read_as:
            if word not in words:
                problems.append(f"drug_classes: {name} is read as {word}, which no interaction "
                                "rule names")
        # read as every class whose ATC groups hold its code, and only those: a thiazide
        # (C03AA) as 利尿剂 and as 降压药, a loop diuretic (C03CA) as 利尿剂 alone
        if sorted(groups) != sorted(read_as):
            problems.append(f"drug_classes: {name} ({atc}) is read as {list(read_as)}, but the "
                            f"classes whose ATC groups hold its code are {groups}")
    for name, (herb, source) in pack.herb_aliases.items():
        if herb not in pack.herbs:
            problems.append(f"herb alias {name}: {herb} is not a herb of the pack")
        if name in pack.herbs:
            problems.append(f"herb alias {name} is itself a herb of the pack")
        if not name.strip() or not source.strip():
            problems.append(f"herb alias {name!r} of {herb} must cite its source")
    for herb, names in pack.herb_pinyin.items():
        if herb not in pack.herbs:
            problems.append(f"herb pinyin {herb} is not a herb of the pack")
        if not names or not all(n.strip() and n.isascii() for n in names):
            problems.append(f"herb pinyin of {herb} must be written in Latin letters")
    if pack.herb_pinyin and not pack.herb_pinyin_source.strip():
        problems.append("herb pinyin must say where it comes from")
    for herb in pack.herb_latin:
        if herb not in pack.herb_sources:
            problems.append(f"herb Latin name of {herb} must come with its source record")
    for names, species, source in pack.herb_common_names:
        if not names or not species or not source.strip():
            problems.append(f"herb common names {list(names)} must give their species and "
                            "cite their source")
    for herb, (species, source) in pack.herb_sources.items():
        if herb not in pack.herbs:
            problems.append(f"herb source {herb} is not a herb of the pack")
        if not species or not source.strip():
            problems.append(f"herb source {herb} must give its species and cite its source")
    ids = [r.id for r in pack.red_flags] + [v.id for v in pack.vital_rules]
    if len(ids) != len(set(ids)):
        problems.append("red-flag and vital-sign rule ids must be unique")
    return problems
