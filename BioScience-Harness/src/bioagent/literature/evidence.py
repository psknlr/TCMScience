"""Typing a retrieved passage: the EvidenceItem fields a fixed rule can read, and no others.

A retriever returns text. Text becomes evidence here when it is **located** — the digest of
the content and the offset of the quote, issued by :meth:`EvidenceItem.located_in` — and
**typed**: a study design, and the population, comparator and outcome it covers. Location
is mechanical. Typing is where a retriever or a summarising model guesses: an abstract
that mentions a placebo group reads as a trial, one that reports a hazard ratio reads as a
cohort. The design is the worst field to guess, because it decides which claims the item
may license (``tcm.model.CLAIM_SUPPORT``): a cohort read as a trial licenses efficacy.

So each field is filled only by a fixed rule, and only when the text gives exactly one
reading:

* every rule is a regular expression with a stable id, and the reading records the rule,
  the matched span and its offset in the same content the quote is located in, so the
  reading can be re-checked as a receipt can;
* a field the rules read two different ways is **ambiguous** and left unassessed — the
  rules prefer no answer to a wrong one, which leaves most systematic reviews unassessed
  (they name the designs they pool);
* a design mention after a negation ("non-randomized", "未随机") is not a reading.

``EvidenceItem`` requires a design and refuses an unknown one, deliberately
(``tier_for_design``: "a default here would be the precise failure mode this module exists
to avoid"). A passage whose design no rule reads is therefore **withheld**: it is reported,
located, as a :class:`TypedPassage` with its readings and the reason, and it becomes an
EvidenceItem when someone states its design — never by default. Unread population,
comparator and outcome stay empty in the item, its notes say they were not assessed, its
quality has every dimension ``NOT_ASSESSED``, and its retraction state stays
``unverified``, because nothing here checks one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from ..contracts import EvidenceItem, EvidenceQuality, SourceCard
from .records import IndexedDocument, Passage

__all__ = ["FIELDS", "FieldReading", "TypedPassage", "card_id", "document_card",
           "evidence_id", "read_fields", "to_evidence"]

#: The EvidenceItem fields a rule may fill, in the order they are reported.
FIELDS = ("design", "population", "comparator", "outcome")

#: A negation just before a design mention: "non-randomized", "not randomly assigned",
#: "未随机分组", "非随机对照".
_NEGATED = re.compile(r"(?:\bnot|\bnon-?|\bwithout|未|非|无)\s*$", re.I)

#: (rule id, design, pattern). Each pattern is a statement of a study's design, in English
#: or Chinese. Bare words that also occur in other designs' abstracts are left out on
#: purpose: "placebo" (an observational study can compare with one), "cohort" alone (a
#: trial reports its "overall cohort"), "随机" alone (随机抽样 is random sampling).
_DESIGN_RULES: tuple[tuple[str, str, re.Pattern[str]], ...] = tuple(
    (rule, design, re.compile(pattern, re.I)) for rule, design, pattern in (
        ("design.review.en", "systematic_review",
         r"\b(?:systematic review|meta-analys[ie]s)\b"),
        ("design.review.zh", "systematic_review", r"系统评价|荟萃分析|meta分析"),
        ("design.trial.en", "randomized_trial",
         r"\b(?:randomi[sz]ed|randomly (?:assigned|allocated))\b"),
        ("design.trial.zh", "randomized_trial", r"随机(?:对照|双盲|单盲|分组|分为|分配)"),
        ("design.observational.en", "observational",
         r"\b(?:(?:prospective|retrospective|population-based) cohort|cohort study"
         r"|case-control|cross-sectional)\b"),
        ("design.observational.zh", "observational",
         r"队列研究|前瞻性队列|回顾性队列|病例对照研究|横断面(?:研究|调查)"),
        ("design.case.en", "case_report",
         r"\b(?:case (?:report|series)|we (?:report|describe|present) (?:a |an |the )?"
         r"(?:\w+ )?cases?)\b"),
        ("design.case.zh", "case_report", r"病例报告|病例系列|个案报道"),
        ("design.animal.en", "animal", r"\b(?:mice|mouse|rats?|murine)\b"),
        ("design.animal.zh", "animal", r"大鼠|小鼠"),
        ("design.cells.en", "in_vitro", r"\b(?:in vitro|cell lines?|cultured cells)\b"),
        ("design.cells.zh", "in_vitro", r"体外实验|细胞系|细胞株"),
    ))

_HEAD = (r"(?:patients|adults|children|participants|individuals|people|persons|women|men"
         r"|subjects|infants|adolescents|volunteers)")
_QUALIFIER = r"(?:with|aged|who|undergoing|receiving)"
#: Where a population phrase ends: punctuation, or a verb that starts the next clause.
_POPULATION_END = (r"(?=[.;:()]|,\s|\s(?:followed|were|was|randomi[sz]ed|received|treated"
                   r"|enrolled|assigned|from|during|for)\b|$)")

#: (rule id, pattern with a ``v`` group). The value is the matched phrase, verbatim.
_VALUE_RULES: Mapping[str, tuple[tuple[str, re.Pattern[str]], ...]] = {
    "population": (
        ("population.in.en", re.compile(
            rf"\b(?:in|among) (?P<v>{_HEAD} {_QUALIFIER}\b.{{1,120}}?){_POPULATION_END}",
            re.I | re.S)),
        ("population.of.en", re.compile(
            rf"\b(?:cohort|sample|trial|study) of (?:[\d,]+ )?(?P<v>{_HEAD} {_QUALIFIER}\b"
            rf".{{1,120}}?){_POPULATION_END}", re.I | re.S)),
        ("population.enrolled.zh", re.compile(
            r"纳入\s*[\d,，]+\s*(?:名|例)(?P<v>[^，。；、（(]{1,40}?(?:患者|受试者|志愿者|儿童|成人))")),
    ),
    "comparator": (
        ("comparator.placebo.en", re.compile(r"\b(?P<v>placebo)\b", re.I)),
        ("comparator.placebo.zh", re.compile(r"(?P<v>安慰剂)")),
        ("comparator.compared.en", re.compile(
            r"\bcompared (?:with|to) (?P<v>(?!the\b)[a-z][\w-]*(?: [\w-]+){0,3}?)"
            r"(?=[,.;:()]| in | among |$)", re.I)),
        ("comparator.compared.zh", re.compile(r"与(?P<v>[^，。；、与]{1,12}?)相比")),
    ),
    "outcome": (
        ("outcome.primary.en", re.compile(
            r"\bprimary (?:outcome|end ?point) (?:was|is) (?:the |a |an )?"
            r"(?P<v>[^.;:()]{1,120}?)(?=\s*[.;:(,]| in | among |$)", re.I)),
        ("outcome.risk.en", re.compile(
            r"\b(?:reduced|increased|lowered|raised|decreased) the (?:combined |composite "
            r"|relative )?risk of (?P<v>[^.;:()]{1,120}?)(?= in | among | compared| versus"
            r"| vs\b|\s*[.;:(,]|$)", re.I)),
        ("outcome.associated.en", re.compile(
            r"\b(?:was|were) associated with (?:an? )?(?:(?:higher|lower|increased|reduced"
            r"|greater) )?(?:risk of )?(?P<v>[^.;:()]{1,120}?)(?= in | among |\s*[.;:(,]|$)",
            re.I)),
        ("outcome.result.zh", re.compile(
            r"(?:降低|升高|增加|减少|改善|延长|缩短)了(?P<v>[^，。；、（(]{1,20})")),
    ),
}


@dataclass(frozen=True, slots=True)
class FieldReading:
    """What a rule read for one field, or why the field was left unassessed.

    ``offset`` is into the same content the passage is located in, so ``span`` can be
    checked there the way a quote receipt is. ``value`` is ``span`` with its whitespace
    collapsed: a PDF breaks lines inside a phrase, and the line break is not part of the
    population.
    """

    field: str
    value: str = ""
    rule: str = ""
    span: str = ""
    offset: int = -1
    note: str = ""

    @property
    def assessed(self) -> bool:
        return bool(self.rule)

    def describe(self) -> str:
        if self.assessed:
            return (f"{self.field} {self.value!r} read by rule {self.rule} from "
                    f"{self.span!r} at offset {self.offset}")
        return f"{self.field} not assessed ({self.note})"

    def as_dict(self) -> dict[str, Any]:
        return {"status": "read" if self.assessed else "not_assessed", "value": self.value,
                "rule": self.rule, "span": self.span, "offset": self.offset,
                "note": self.note}


def read_fields(text: str) -> dict[str, FieldReading]:
    """Every field in :data:`FIELDS`, read from a document's whole text or left unassessed.

    The whole text rather than the passage: a study states its design in its methods and
    its result in another sentence, and a passage is wherever the retriever cut.
    """
    readings = {"design": _read_design(text)}
    for name, rules in _VALUE_RULES.items():
        readings[name] = _read_value(name, text, rules)
    return readings


def _read_design(text: str) -> FieldReading:
    first: dict[str, tuple[str, str, int]] = {}
    negated: list[str] = []
    for rule, design, pattern in _DESIGN_RULES:
        for match in pattern.finditer(text):
            if _NEGATED.search(text[max(0, match.start() - 12):match.start()]):
                negated.append(f"{match.group(0)!r} at {match.start()}")
                continue
            if design not in first or match.start() < first[design][2]:
                first[design] = (rule, match.group(0), match.start())
    if len(first) == 1:
        design, (rule, span, offset) = next(iter(first.items()))
        return FieldReading("design", design, rule, span, offset)
    if first:
        found = ", ".join(f"{d} ({span!r} at {at})"
                          for d, (_, span, at) in sorted(first.items(), key=lambda i: i[1][2]))
        return FieldReading("design", note=f"ambiguous: the text states {found}")
    return FieldReading("design", note="no rule matched a statement of the study design"
                        + (f"; negated mentions ignored: {', '.join(negated)}"
                           if negated else ""))


def _read_value(name: str, text: str,
                rules: tuple[tuple[str, re.Pattern[str]], ...]) -> FieldReading:
    first: dict[str, tuple[str, str, int, str]] = {}
    for rule, pattern in rules:
        for match in pattern.finditer(text):
            span = match.group("v")
            value = " ".join(span.split())
            if not value:
                continue
            key = value.lower()
            if key not in first or match.start("v") < first[key][2]:
                first[key] = (rule, span, match.start("v"), value)
    if len(first) == 1:
        rule, span, offset, value = next(iter(first.values()))
        return FieldReading(name, value, rule, span, offset)
    if first:
        values = ", ".join(repr(v[3]) for v in sorted(first.values(), key=lambda v: v[2]))
        return FieldReading(name, note=f"ambiguous: the text reads as {values}")
    return FieldReading(name, note="no rule matched")


@dataclass(frozen=True)
class TypedPassage:
    """One passage after typing: its readings, and its EvidenceItem when it earned one.

    ``item`` is ``None`` exactly when ``withheld`` says why. A withheld passage is still a
    located excerpt — the readings and the passage's offset are reported — but it is
    material, not evidence, until its design is stated.
    """

    passage: Passage
    readings: Mapping[str, FieldReading]
    item: EvidenceItem | None = None
    withheld: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {**self.passage.as_dict(),
                "fields": {name: self.readings[name].as_dict() for name in FIELDS},
                "evidence_id": self.item.id if self.item is not None else None,
                "withheld": self.withheld}


def evidence_id(passage: Passage) -> str:
    return f"lit.{passage.doc_id}.{passage.offset}"


def card_id(document: IndexedDocument) -> str:
    return f"literature.{document.doc_id}"


def to_evidence(passage: Passage, content: str, *, source_card_id: str,
                retrieved_by: str = "", retrieval_run: str = "",
                retrieved_at: float = 0.0, store: Any = None) -> TypedPassage:
    """Type ``passage`` from ``content``, the full text it was retrieved from.

    Raises ``ValueError`` when ``content`` is not that text or the passage is not at its
    offset in it: a receipt issued against other content would verify nothing. The
    item's receipt is issued by :meth:`EvidenceItem.located_in`, which searches the content
    itself and keeps it in ``store`` (default: the process content store) so the receipt
    can be re-checked later.
    """
    if not passage.located_in(content):
        raise ValueError(f"passage {passage.passage_id}: the content given is not the text "
                         f"it was retrieved from, or the passage is not at offset "
                         f"{passage.offset} in it")
    readings = read_fields(content)
    design = readings["design"]
    if not design.assessed:
        return TypedPassage(passage, readings, withheld=(
            f"no study design was read from {passage.doc_id} ({design.note}); an "
            "EvidenceItem must state one, and none is guessed"))
    identifier, scheme = passage.document.identifier
    item = EvidenceItem(
        id=evidence_id(passage), design=design.value, quote=passage.text,
        citation=passage.document.citation, title=passage.document.ref.title,
        identifier=identifier, identifier_type=scheme, source_card_id=source_card_id,
        quality=EvidenceQuality(),
        population=readings["population"].value, comparator=readings["comparator"].value,
        outcome=readings["outcome"].value, year=passage.document.ref.year,
        retrieved_by=retrieved_by, retrieval_run=retrieval_run, retrieved_at=retrieved_at,
        notes=_notes(passage, readings, content.find(passage.text)))
    return TypedPassage(passage, readings, item.located_in(content, store=store))


def _notes(passage: Passage, readings: Mapping[str, FieldReading], first: int) -> str:
    parts = [f"retrieved by vector search (cosine {passage.score:.4f}, rank {passage.rank})"]
    parts += [readings[name].describe() for name in FIELDS]
    parts.append("retraction status not checked; quality not assessed")
    if first != passage.offset:
        # located_in names the first occurrence of the quote; the retriever cut this one
        # later. Both are the same characters, so either is a true receipt.
        parts.append(f"the passage at offset {passage.offset} also occurs at offset {first},"
                     " which the receipt names")
    return "; ".join(parts)


def document_card(document: IndexedDocument, *, snapshot_at: str) -> SourceCard:
    """The source card for one indexed document, pinned by the digest of its bytes.

    One card per document rather than one for the corpus: each document has its own
    identifier, licence and digest, and a claim resting on one of them must be traceable
    to that one file.
    """
    identifier, scheme = document.identifier
    limits = ["supplied by the caller as a local file; not fetched from a publisher or a "
              "registry"]
    if scheme != "local_artifact":
        limits.append(f"{scheme.upper()} {identifier} is as the caller declared it and was "
                      "not checked against a registry")
    if document.text_sha256 != document.ref.sha256:
        limits.append(f"quotes are located in the text {document.parser} extracted, not in "
                      "the file's bytes")
    return SourceCard(
        id=card_id(document), name=document.ref.title or Path(document.ref.path).name,
        kind="literature_index", access_method="local_file",
        license_spdx=document.ref.license_spdx,
        license_note=("" if document.ref.license_spdx else
                      "no licence was declared for this document; its reuse terms are its "
                      "publisher's"),
        integration_mode="native", snapshot_hash=document.ref.sha256,
        snapshot_at=snapshot_at, operation_hashes={"text": document.text_sha256},
        known_limits=tuple(limits), offline_capable=True,
        notes=(f"{document.ref.media_type}, {document.bytes} bytes, read as "
               f"{document.text_chars} characters by {document.parser}; indexed with "
               "paper-qa"))
