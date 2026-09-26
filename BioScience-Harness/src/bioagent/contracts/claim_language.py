"""Does a claim's wording say more than its declared ``claim_kind``?

`check_claim` licenses a claim by its *kind*: an ``attribution`` needs a
classical text, an ``efficacy`` claim needs a trial. That is only as good as the
kind is honest. A claim declared as an attribution whose text reads "this herb
has been proven effective for all cancer patients and should replace standard
treatment" passed every structural check, because nothing compared the words
with the kind (audit F02).

This module is that comparison. It is lexical and deliberately narrow: it looks
for a handful of phrase families that each imply a stronger claim kind than
the one declared, in English and Chinese, and reports each hit with the kind the
wording would need. It does not try to understand the sentence. A miss is
possible (paraphrase defeats any word list); a hit is a claim whose wording and
declared kind disagree, which is refused until one of them is changed. The
families are:

``efficacy``
    "is effective", "cures", "clinically proven", 有效, 疗效, 治愈 — outcome language that
    only a trial-backed ``efficacy`` (or ``recommendation``) claim may use.
``normative``
    "should", "recommend", "replace standard", 应当, 建议, 替代标准治疗 — only a
    ``recommendation`` may tell anyone what to do (see also CLM007).
``certainty``
    "proven", "demonstrated", "confirmed", 已被证明, 证实 — a hypothesis, a
    traditional use, an attribution or an association cannot be stated as
    established.
``universal``
    "all patients", "every patient", 所有患者, 所有癌症 — no evidence in this
    system covers every patient; a universal population is always an
    extrapolation.

A phrase preceded closely by a negation ("not proven", 未证实, 尚无证据表明 …
有效) is not counted: stating that something is *not* established is the
calibrated statement, and refusing it would push authors to drop it.
"""

from __future__ import annotations

import re
from typing import NamedTuple

__all__ = ["LanguageFinding", "overreaching_language"]


class LanguageFinding(NamedTuple):
    family: str
    phrase: str
    #: The claim kinds whose wording this phrase fits.
    permitted_kinds: frozenset[str]


_EFFICACY_KINDS = frozenset({"efficacy", "recommendation"})
_NORMATIVE_KINDS = frozenset({"recommendation"})
_CERTAIN_KINDS = frozenset({"efficacy", "recommendation", "mechanism", "safety_signal"})

_FAMILIES: tuple[tuple[str, frozenset[str], tuple[str, ...]], ...] = (
    ("efficacy", _EFFICACY_KINDS, (
        r"\b(?:is|are|was|were|be|been|being)\s+(?:clinically\s+|highly\s+|very\s+)?"
        r"effective\b",
        r"\beffective\s+(?:for|in|against|at)\b",
        r"\b(?:cures?|cured|curing)\b",
        r"\bclinically\s+(?:proven|effective|beneficial)\b",
        r"有效", r"疗效", r"治愈", r"根治", r"显著改善", r"可以治疗", r"能够治疗",
    )),
    ("normative", _NORMATIVE_KINDS, (
        r"\bshould\s+(?:be\s+)?(?:used|given|taken|replace|prescribed|adopted|"
        r"preferred|use|take|prescribe|receive)\b",
        r"\b(?:we\s+)?recommend(?:s|ed)?\b",
        r"\bmust\s+(?:be\s+)?(?:used|given|taken|prescribed)\b",
        r"\b(?:replace|instead\s+of)\s+(?:the\s+)?standard\s+(?:of\s+)?(?:care|"
        r"treatment|therapy)\b",
        r"应当", r"应该", r"应(?:替代|取代|使用|服用|首选|采用)", r"建议", r"推荐",
        r"替代标准治疗", r"取代标准治疗",
    )),
    ("certainty", _CERTAIN_KINDS, (
        r"\b(?:has|have|had)\s+been\s+(?:proven|proved|demonstrated|established|"
        r"confirmed)\b",
        r"\b(?:is|are)\s+(?:proven|established|confirmed)\b",
        r"\bproves?\s+that\b", r"\bdemonstrates?\s+that\b",
        r"已被证明", r"已证明", r"被证实", r"已证实", r"证实了", r"确证",
    )),
    ("universal", frozenset(), (
        r"\b(?:all|every|any)\s+(?:\w+\s+){0,2}(?:patients?|people|persons?|"
        r"individuals|humans|adults|children)\b",
        r"所有(?:\S{0,6})?(?:患者|病人|人群|病例|人)", r"一切(?:\S{0,6})?(?:患者|病人)",
        r"任何(?:\S{0,6})?(?:患者|病人)",
    )),
)

_COMPILED = tuple((family, kinds, tuple(re.compile(p, re.IGNORECASE) for p in patterns))
                  for family, kinds, patterns in _FAMILIES)

#: A negation this close before a phrase turns an assertion into its denial.
_NEGATION = re.compile(
    r"(?:\bnot\b|\bno\b|\bnever\b|\bnor\b|\bneither\b|\bwithout\b|\bunproven\b|"
    r"n't\b|未|不(?![同良适少仅但])|没有|无(?!论)|尚无|并非|缺乏|尚未)", re.IGNORECASE)
_NEGATION_WINDOW = 24


def _negated(text: str, start: int) -> bool:
    window = text[max(0, start - _NEGATION_WINDOW):start]
    # Only the current clause: a negation in an earlier clause does not reach.
    window = re.split(r"[。；;.!?！？,，]", window)[-1]
    return bool(_NEGATION.search(window))


def overreaching_language(text: str, claim_kind: str) -> tuple[LanguageFinding, ...]:
    """Phrases in ``text`` that need a stronger kind than ``claim_kind``.

    Returns one finding per family that fires, with the first matching phrase.
    Empty when the wording fits the kind, or no family matched.
    """
    findings: list[LanguageFinding] = []
    for family, kinds, patterns in _COMPILED:
        if claim_kind in kinds:
            continue
        for pattern in patterns:
            hit = next((m for m in pattern.finditer(text)
                        if not _negated(text, m.start())), None)
            if hit is not None:
                findings.append(LanguageFinding(family, hit.group(0), kinds))
                break
    return tuple(findings)
