"""Clauses: where one sentence makes more than one statement.

A plan, a method or a call for further work makes no claim about the world, and the gates
let such sentences pass unchecked. They decided it for the whole sentence, so a claim and a
plan written as one sentence went out together: "黄芪能治愈肺癌，未来研究将优化剂量" and
"Astragalus cures lung cancer; future studies will optimise the dose" were released while
the same claim on its own was refused. A plan in one clause says nothing about a claim in
another, so the decision is made clause by clause.

A sentence is cut at clause punctuation and at conjunctions that join two statements, and
also just before a construction that opens a statement of its own ("future studies will",
"未来研究"), so "X cures Y and future studies will …" is two clauses without a comma.
Constructions that close a statement ("… has not been studied", "…尚未证实") are left in the
clause they close, which they turn into an open question rather than a claim.

The constructions are defined here once, for the output gate and for the claim parser.
Each kept its own list, and the parser's also exempted reporting constructions ("this
study reports that", "the results were", "Table 2 shows"). Those introduce a finding
rather than withdraw one, and exempting them skipped the scope check for exactly the
sentences that restate a source's result: "This study reports that empagliflozin
reduced … in children" was supported at 0.95 by a trial in adults.
"""

from __future__ import annotations

import re
from typing import Callable

__all__ = ["NON_CLAIM_CLOSES", "NON_CLAIM_OPENS", "asserted", "clauses", "is_non_claim",
           "without"]

#: Clause punctuation, and conjunctions that join two statements rather than two terms. A
#: comma between digits is a thousands separator ("1,000 mg"), not a clause break.
_BREAK = re.compile(r"[，；;]|(?<!\d),|,(?!\d)|\b(?:but|however|whereas|although|though)\b"
                    r"|但是|然而|不过|而且|并且|同时", re.I)


#: Constructions that open a statement of intent, method or further work: a plan, not a
#: claim about the world. Distinct from hedging, which weakens a claim without removing it.
NON_CLAIM_OPENS = re.compile(
    r"\b(?:we (?:plan|propose|will|intend|aim)|this (?:study|analysis|paper) (?:will|aims)"
    r"|further (?:study|research|work) is (?:needed|required|warranted)"
    r"|future (?:studies|work|research)|methods? section|protocol specifies)\b"
    r"|(?:本研究拟|未来研究|方法部分)", re.I)
#: Constructions that close the statement they are part of ("whether X reduces Y has not
#: been studied"), which turn that clause into an open question.
NON_CLAIM_CLOSES = re.compile(
    r"\b(?:has not been (?:studied|investigated))\b"
    r"|(?:有待(?:进一步|今后)|尚需进一步|尚未(?:研究|证实|明确))", re.I)


def is_non_claim(clause: str) -> bool:
    """Whether ``clause`` is a plan or an open question rather than a claim."""
    return bool(NON_CLAIM_OPENS.search(clause) or NON_CLAIM_CLOSES.search(clause))


def _spans(sentence: str, opens: re.Pattern[str] | None) -> list[tuple[int, int]]:
    cuts = {0, len(sentence)}
    for m in _BREAK.finditer(sentence):
        cuts.update((m.start(), m.end()))
    for m in (opens.finditer(sentence) if opens is not None else ()):
        cuts.add(m.start())
    points = sorted(cuts)
    return [(a, b) for a, b in zip(points, points[1:])
            if sentence[a:b].strip() and not _BREAK.fullmatch(sentence[a:b].strip())]


def clauses(sentence: str, opens: re.Pattern[str] | None = None) -> list[str]:
    """``sentence`` cut into clauses, and before every match of ``opens``."""
    sentence = sentence or ""
    return [sentence[a:b].strip() for a, b in _spans(sentence, opens)]


def without(sentence: str, drop: Callable[[str], bool],
            opens: re.Pattern[str] | None = None) -> str:
    """``sentence`` with every clause that ``drop`` selects blanked out.

    The rest keeps its positions and punctuation, so a pattern that matched the sentence
    still matches it when no clause is dropped.
    """
    sentence = sentence or ""
    out = list(sentence)
    for a, b in _spans(sentence, opens):
        if drop(sentence[a:b]):
            out[a:b] = " " * (b - a)
    return "".join(out)


def asserted(sentence: str) -> str:
    """``sentence`` with its plans and open questions blanked out: what it asserts."""
    return without(sentence, is_non_claim, NON_CLAIM_OPENS)
