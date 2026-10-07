"""Matching recorded findings to the criteria's terms.

Symptoms and signs are matched as text, in decreasing strength:

* **exact** — the recorded entry is the criterion's term (after normalisation);
* **synonym** — the pack's synonym table maps the entry to the term (怕冷 → 畏寒);
* **contains** — the entry contains the term (胁肋胀痛 contains 胀痛);
* **partial** — the term contains the entry (耳鸣 in 头晕耳鸣); half strength, and the
  explanation says it was partial, because 耳鸣 alone is not 头晕耳鸣.

An entry that starts with a negation (无、没有、否认、不) and is not itself a term (无汗 is)
is a **pertinent negative**: the finding was asked about and is absent.

Tongue and pulse descriptions are compared as **features**, not text. 舌红苔黄腻 is
{body 红, coat 黄, coat 腻}; 脉沉迟无力 is {沉, 迟, 无力}, and 弱 implies 沉, 细 and 无力.
A criterion matches when all its features were observed, partially when some were and
none contradicts, and it is **contradicted** when an observed feature is its opposite
(淡 against 红, 浮 against 沉, 迟 against 数, 紧 against 缓, 少苔 against 腻苔).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

__all__ = ["normalise", "split_entries", "Match", "TermMatcher", "tongue_features",
           "pulse_features", "FeatureMatch", "match_tongue", "match_pulse"]

_PUNCT = re.compile(r"[\s，,。．.、；;：:！!？?（）()\[\]【】“”\"'‘’]+")
_SPLIT = re.compile(r"[，,、；;\n。]+")
_NEGATION = ("没有", "否认", "无", "不")


def normalise(text: str) -> str:
    t = unicodedata.normalize("NFKC", str(text or "")).strip().lower()
    return _PUNCT.sub("", t)


def split_entries(values: Iterable[str] | str | None) -> list[str]:
    """Entries from a list or a comma-separated string, normalised and de-duplicated."""
    if values is None:
        return []
    if isinstance(values, str):
        values = [values]
    out: list[str] = []
    for v in values:
        for piece in _SPLIT.split(unicodedata.normalize("NFKC", str(v))):
            n = normalise(piece)
            if n and n not in out:
                out.append(n)
    return out


@dataclass(frozen=True)
class Match:
    term: str
    strength: float          # 1.0 full, 0.5 partial, 0 none
    via: str                 # exact | synonym | contains | partial | absent | ""
    entry: str = ""


class TermMatcher:
    """Matches recorded entries against criterion terms, with the pack's synonyms."""

    def __init__(self, known_terms: Iterable[str], synonyms: Mapping[str, str]):
        self.known = {normalise(t) for t in known_terms}
        self.synonyms = {normalise(k): normalise(v) for k, v in synonyms.items()}

    def classify(self, entries: Sequence[str]) -> tuple[list[str], list[str]]:
        """Split entries into (present, absent), reading negation prefixes."""
        present, absent = [], []
        for e in entries:
            if e in self.known or e in self.synonyms:
                present.append(e)
                continue
            for prefix in _NEGATION:
                rest = e[len(prefix):]
                if e.startswith(prefix) and len(rest) >= 1:
                    absent.append(rest)
                    break
            else:
                present.append(e)
        return present, absent

    def canonical(self, entry: str) -> str:
        return self.synonyms.get(entry, entry)

    def match(self, term: str, present: Sequence[str], absent: Sequence[str] = ()) -> Match:
        t = normalise(term)
        best = Match(term=term, strength=0.0, via="")
        for e in present:
            canon = self.canonical(e)
            if e == t:
                return Match(term, 1.0, "exact", e)
            if canon == t:
                best = max(best, Match(term, 1.0, "synonym", e), key=lambda m: m.strength)
            elif t in e:
                best = max(best, Match(term, 1.0, "contains", e), key=lambda m: m.strength)
            elif t in canon and canon != e:
                best = max(best, Match(term, 1.0, "synonym", e), key=lambda m: m.strength)
            elif e in t and len(e) >= 2 and best.strength < 0.5:
                best = Match(term, 0.5, "partial", e)
        if best.strength >= 1.0:
            return best
        for e in absent:
            canon = self.canonical(e)
            if e == t or canon == t or t in e:
                return Match(term, 0.0, "absent", e)
        return best

    def asked(self, term: str, present: Sequence[str], absent: Sequence[str]) -> bool:
        m = self.match(term, present, absent)
        return m.via in ("exact", "synonym", "contains", "absent")


# --------------------------------------------------------------------------- tongue
_COAT_CHARS = "白黄灰黑腻滑薄厚燥润少无剥"
_BODY_TOKENS = (("边尖红", ("body:边尖红",)), ("淡红", ("body:淡红",)), ("淡白", ("body:淡",)),
                ("齿痕", ("body:齿痕",)), ("裂纹", ("body:裂纹",)), ("少津", ("moist:少津",)),
                ("津少", ("moist:少津",)), ("淡", ("body:淡",)), ("红", ("body:红",)),
                ("绛", ("body:绛",)), ("紫", ("body:紫",)), ("暗", ("body:暗",)),
                ("青", ("body:青",)), ("胖", ("body:胖",)), ("嫩", ("body:嫩",)),
                ("瘦", ("body:瘦",)), ("干", ("moist:少津",)))
_TONGUE_OPPOSITES = ({"body:淡", "body:红"}, {"body:淡", "body:绛"}, {"body:胖", "body:瘦"},
                     {"coat:少", "coat:腻"}, {"coat:少", "coat:厚"}, {"coat:无", "coat:腻"},
                     {"coat:无", "coat:厚"}, {"coat:少", "coat:滑"}, {"coat:无", "coat:滑"},
                     {"coat:无", "coat:白"}, {"coat:无", "coat:黄"},
                     {"coat:滑", "moist:少津"}, {"coat:滑", "coat:燥"},
                     {"coat:润", "moist:少津"}, {"coat:薄", "coat:厚"})


def tongue_features(text: str, *, expand: bool = True) -> frozenset[str]:
    t = normalise(text)
    feats: set[str] = set()
    for spot in ("瘀斑", "瘀点"):
        if spot in t:
            feats.add(f"spot:{spot}")
            t = t.replace(spot, "|")

    def coat(chars: str) -> None:
        feats.update(f"coat:{c}" for c in chars)

    for m in re.finditer(rf"([{_COAT_CHARS}]+)苔", t):
        coat(m.group(1))
    t = re.sub(rf"([{_COAT_CHARS}]+)苔", "|", t)
    for m in re.finditer(rf"苔([{_COAT_CHARS}]+)", t):
        coat(m.group(1))
    t = re.sub(rf"苔([{_COAT_CHARS}]*)", "|", t)
    body = t.replace("舌", "").replace("质", "")
    for token, fs in _BODY_TOKENS:
        if token in body:
            feats.update(fs)
            body = body.replace(token, "|")
    return frozenset(feats)


# --------------------------------------------------------------------------- pulse
_PULSE_MULTI = (("有力", "有力"), ("无力", "无力"), ("欲绝", "欲绝"))
_PULSE_CHARS = "浮沉迟数虚实滑涩弦紧缓细弱濡洪微结代促大长短芤伏动革牢散疾"
_PULSE_IMPLIES = {"弱": {"沉", "细", "无力"}, "虚": {"无力"}, "微": {"细", "无力"},
                  "濡": {"浮", "细", "无力"}, "洪": {"有力", "大"}, "实": {"有力"}}
# The other way: a pulse described by its parts is the named pulse (沉细无力 is 弱).
_PULSE_DERIVED = ((frozenset({"沉", "细", "无力"}), "弱"), (frozenset({"无力"}), "虚"))
_PULSE_OPPOSITES = ({"浮", "沉"}, {"迟", "数"}, {"迟", "疾"}, {"有力", "无力"}, {"实", "虚"},
                    {"滑", "涩"}, {"紧", "缓"}, {"缓", "数"}, {"洪", "细"}, {"洪", "微"},
                    {"实", "弱"}, {"有力", "弱"}, {"有力", "微"}, {"大", "细"})


def pulse_features(text: str, *, expand: bool = True) -> frozenset[str]:
    t = normalise(text).replace("脉", "")
    feats: set[str] = set()
    for token, feat in _PULSE_MULTI:
        if token in t:
            feats.add(feat)
            t = t.replace(token, "|")
    feats.update(c for c in t if c in _PULSE_CHARS)
    if expand:
        for f in list(feats):
            feats |= _PULSE_IMPLIES.get(f, set())
        for parts, name in _PULSE_DERIVED:
            if parts <= feats:
                feats.add(name)
    return frozenset(feats)


@dataclass(frozen=True)
class FeatureMatch:
    criterion: str
    strength: float           # 1 all features observed, 0.5 some and none contradicted
    contradicted: bool
    observed: str = ""
    conflict: tuple[str, str] | None = None


def _opposed(a: frozenset[str], b: frozenset[str], opposites) -> tuple[str, str] | None:
    for x in a:
        for y in b:
            if {x, y} in opposites:
                return x, y
    return None


def _match_features(criterion: str, observed: Sequence[str], features, opposites
                    ) -> FeatureMatch:
    """One tongue (or one pulse) however many entries describe it: 舌红 and 苔黄腻 recorded
    apart are the 舌红苔黄腻 of a criterion, so the entries' features are pooled."""
    want = features(criterion, expand=False)
    have: frozenset[str] = frozenset().union(*(features(o) for o in observed))
    text = "、".join(observed)
    clash = _opposed(want, have, opposites)
    if clash:
        return FeatureMatch(criterion, 0.0, True, text, clash)
    if want and want <= have:
        return FeatureMatch(criterion, 1.0, False, text)
    if want & have:
        return FeatureMatch(criterion, 0.5, False, text)
    return FeatureMatch(criterion, 0.0, False)


def match_tongue(criterion: str, observed: Sequence[str]) -> FeatureMatch:
    return _match_features(criterion, observed, tongue_features, _TONGUE_OPPOSITES)


def match_pulse(criterion: str, observed: Sequence[str]) -> FeatureMatch:
    return _match_features(criterion, observed, pulse_features, _PULSE_OPPOSITES)
