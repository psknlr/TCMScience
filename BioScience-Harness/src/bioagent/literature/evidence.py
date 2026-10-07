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
  rules prefer no answer to a wrong one;
* a design mention that is not the study's own is set aside: after a negation
  ("non-randomized", "未随机"), in the plural ("randomized controlled trials" are the ones
  a review pools or an introduction cites), or a random split of data ("随机分为训练集");
* a review that names itself one ("this meta-analysis", "本meta分析") is read as a review
  whatever else it names, unless it names another design as its own too;
* a protocol ("study protocol for a randomized trial", "participants will be randomized")
  has no design read at all: it reports no results, so it licenses nothing.

They are measured on 478 real abstracts, and were changed looking at the dev third of them
only (``docs/evidence-typing-accuracy.md``, ``bioagent.benchmarks.evidence_typing``).

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
_NEGATED = re.compile(r"(?:\bnot|\bnon[-–]?|\bwithout|未|非|无)\s*$", re.I)

#: The study noun a design mention qualifies, when one follows it through modifiers only
#: ("randomized, double-blind, placebo-controlled trial", "randomized and non-randomized
#: trials"), not across a preposition or a verb. In the plural it names other studies: the
#: "randomized controlled trials" a review pools or an introduction cites, the "cohort
#: studies" an editorial weighs. A study states its own design in the singular ("a
#: randomized trial", "this cohort study") or as what was done ("were randomized").
_NOUN_AFTER = re.compile(
    r"(?:[ ,–-]+(?!(?:of|in|from|with|for|on|to|at|by|the|a|an|was|were|is|are)\b)"
    r"[\w–-]+){0,4}?[ ,–-]+(trials?|stud(?:y|ies)|RCTs?|reviews?|analys[ie]s|surveys?)\b",
    re.I)
_PLURAL_NOUNS = frozenset({"trials", "studies", "rcts", "reviews", "analyses", "surveys"})
#: The designs whose mentions name a study's kind, and so can name other studies'. A
#: species or a cell line has no plural of that sort: "mice" are the study's own.
_STUDY_KINDS = frozenset({"systematic_review", "randomized_trial", "observational"})

#: Data split at random, not people: "randomly divided into training and validation sets",
#: "随机分为训练集和验证集". A prognostic model's split is not a trial's allocation.
_DATA_SPLIT = re.compile(r"[\w ,()=]{0,40}?\b(?:training|derivation|development)\b"
                         r"[^.;]{0,40}?\b(?:validation|test(?:ing)?) (?:sets?|cohorts?"
                         r"|data ?sets?|samples?|groups?)\b"
                         r"|[^。；]{0,12}?(?:训练集|验证集|测试集)", re.I)

#: A study still to be done. A protocol reports no results, so it licenses nothing whatever
#: design it plans: "study protocol for a randomized controlled trial" is not a trial, and
#: neither is a text whose participants "will be randomized".
_PROTOCOL = re.compile(
    r"\b(?:study|trial|research) protocol\b|\bprotocol (?:for|of) (?:a|an|the)"
    r"(?: [\w-]+){0,5}? (?:trial|study|review|cohort|project)\b"
    r"|\brationale and design\b|\bdesign and rationale\b"
    r"|\bwill be (?:randomi[sz]ed|randomly|allocated|assigned|recruited|enrolled)\b"
    r"|\bwe (?:will|plan to) (?:recruit|enrol|enroll|randomi[sz]e)\b", re.I)

_REVIEW = r"(?:systematic (?:literature )?review|(?:network )?meta[-–]?analysis)"
_REVIEW_ZH = r"(?:系统评价|系统综述|荟萃分析|meta分析)"
#: (rule id, pattern): a review that names itself one, as "this meta-analysis", "a
#: systematic review was conducted", "we performed a systematic review", "本meta分析",
#: "进行Meta分析", or a title line ending "…的系统评价". The other designs it names are then
#: the ones it pools ("of randomized controlled trials", "收集…病例对照研究") and leave it
#: unambiguous, unless it also names another design as its own (:data:`_OWN_STUDY`).
_OWN_REVIEW: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("design.review.own.en", re.compile(
        rf"\b(?:this|the present|our|the current) {_REVIEW}\b"
        rf"|\b{_REVIEW}(?: and (?:a )?{_REVIEW})? (?:was|were) (?:conducted|performed"
        rf"|carried out|undertaken)\b"
        rf"|\bwe (?:conducted|performed|undertook|carried out)(?: an?)?(?: [\w-]+){{0,2}}? "
        rf"{_REVIEW}\b", re.I)),
    ("design.review.own.zh", re.compile(
        rf"本(?:研究|文)?{_REVIEW_ZH}|(?:进行|采用)了?{_REVIEW_ZH}|的{_REVIEW_ZH}$",
        re.I | re.M)),
)
#: Another design named as the study's own: "the present cohort study", "this randomized
#: trial". Beside a review naming itself one, the text reports two studies.
_OWN_STUDY = re.compile(
    r"\b(?:this|the present|our|the current)(?: [\w-]+){0,2}? (?:randomi[sz]ed|cohort"
    r"|case[-–]control|cross[-–]sectional|observational)(?: [\w-]+){0,2}? (?:trial|study)\b",
    re.I)

_SPECIES = (r"(?:dogs|pigs|piglets|rabbits|sheep|goats|calves|cows|cattle|chickens|broilers"
            r"|hamsters|monkeys|macaques|horses|ferrets|zebrafish|gerbils|guinea pigs)")
#: Widely used cell lines, by name: a study that names one worked on it. "PC-3" keeps its
#: hyphen, because "PC3" is also a third principal component.
_CELL_LINES = (r"(?:HeLa|HepG2|Hep ?3B|Huh-?7|MCF-?7|MDA-MB-\d+|A549|H1299|HEK-?293T?"
               r"|HCT-?116|HT-?29|SW480|SW620|Caco-?2|PC-3|DU-?145|LNCaP|U-?87|U-?251"
               r"|SH-SY5Y|PC-?12|RAW ?264\.7|THP-1|U937|Jurkat|K562|HL-60|HaCaT|HUVECs?"
               r"|NIH-?3T3|3T3-L1|C2C12|H9c2|BV-?2|Vero)")

#: (rule id, design, pattern). Each pattern is a statement of a study's design, in English
#: or Chinese. Bare words that also occur in other designs' abstracts are left out on
#: purpose: "placebo" (an observational study can compare with one), "cohort" alone (a
#: trial reports its "overall cohort"), "随机" alone (随机抽样 is random sampling), a
#: species named without a count ("in zebrafish, transgenesis is efficient" opens a
#: review), "retrospective" (a trial is "retrospectively registered").
_DESIGN_RULES: tuple[tuple[str, str, re.Pattern[str]], ...] = tuple(
    (rule, design, re.compile(pattern, re.I)) for rule, design, pattern in (
        ("design.review.en", "systematic_review",
         r"\b(?:systematic (?:literature )?reviews?|meta[-–]?analys[ie]s"
         r"|umbrella reviews?)\b"),
        ("design.review.zh", "systematic_review", r"系统评价|系统综述|荟萃分析|meta分析"),
        ("design.trial.en", "randomized_trial",
         r"\b(?:randomi[sz]ed|randomly (?:assigned|allocated))\b"),
        ("design.trial.zh", "randomized_trial",
         r"随机(?:[、，,]\s*)?(?:对照|双盲|单盲)|随机(?:分组|分为|分配)"),
        ("design.observational.en", "observational",
         r"\b(?:(?:prospective|retrospective|population[-–]based) cohort|cohort study"
         r"|case[-–]control|cross[-–]sectional"
         r"|observational(?:,? (?:and )?[\w-]+){0,3} (?:study|design|analysis|cohort))\b"),
        ("design.observational.zh", "observational",
         r"队列研究|前瞻性队列|回顾性队列|病例对照研究|横断面(?:研究|调查)|观察性研究"),
        ("design.case.en", "case_report",
         r"\b(?:case (?:report|series)|we (?:report|describe|present) (?:a |an |the )?"
         r"(?:\w+ )?cases?|(?:a|an) \d{1,3}[- ](?:years?|months?)[- ]old)\b"),
        # "1例…文献复习" only: "1例报告" alone is as often "one patient reported".
        ("design.case.zh", "case_report",
         r"病例报告|病例系列|个案报道|(?<!\d)1例(?:报道|报告)?(?:并|及)文献(?:复习|回顾)"),
        ("design.animal.en", "animal",
         rf"\b(?:mice|mouse|rats?|murine|\d+ (?:(?:adult|young|aged|healthy|male|female"
         rf"|pregnant|client-owned|laboratory|neonatal|weaned) )?{_SPECIES})\b"),
        ("design.animal.zh", "animal", r"大鼠|小鼠|斑马鱼|家兔|比格犬|豚鼠|仓鼠"),
        # "in vitro fertilization" is a clinical procedure, not a bench study.
        ("design.cells.en", "in_vitro",
         rf"\b(?:in vitro(?! fertili[sz]ation| maturation)|cell lines?|cultured cells"
         rf"|(?:CCK-8|MTT|colony[- ]formation|transwell|wound[- ]healing|EdU"
         rf"|cell (?:viability|proliferation|counting)) assays?"
         rf"|cells were (?:treated|transfected|incubated|cultured|exposed|co-?cultured)"
         rf"|{_CELL_LINES})\b"),
        ("design.cells.zh", "in_vitro", rf"体外实验|体外培养|细胞系|细胞株|{_CELL_LINES}细胞"),
    ))

_HEAD = (r"(?:patients|adults|children|participants|individuals|people|persons|women|men"
         r"|subjects|infants|adolescents|volunteers)")
_QUALIFIER = r"(?:with|aged|who|undergoing|receiving)"
_DIRECTION = (r"(?:higher|lower|increased|reduced|decreased|greater|worse|better|poorer"
              r"|longer|shorter|elevated)")
#: Where a population phrase ends: punctuation, or a word that starts the next clause ("…
#: with chronic renal insufficiency can be adopted", "… with chronic conditions by
#: socioeconomic status").
_POPULATION_END = (r"(?=[.;:()]|,\s|\s(?:followed|were|was|randomi[sz]ed|received|treated"
                   r"|enrolled|assigned|from|during|for|by|can|could|may|might|should"
                   r"|would|will)\b|$)")

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
        # Not "compared with baseline": a change within one group has no comparator.
        ("comparator.compared.en", re.compile(
            r"\bcompared (?:with|to) (?P<v>(?!the\b|baseline\b)[a-z][\w-]*(?: [\w-]+){0,3}?)"
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
        # An outcome is what an association runs to in a direction ("associated with worse
        # FEV1") or with a ratio estimated for it ("associated with all-cause mortality
        # (hazard ratio 1.33 …)"). A bare "associated with" names any companion: a factor,
        # a protein, a history ("…with several risk factors", "…with nerve damage").
        ("outcome.associated.en", re.compile(
            rf"\b(?:was|were) associated with (?:an? |the )?{_DIRECTION} (?:(?:risk"
            r"|odds|incidence|rate|likelihood|prevalence) of )?(?P<v>[^.;:()]{1,120}?)"
            r"(?= in | among |\s*[.;:(,]|$)", re.I)),
        ("outcome.associated_ratio.en", re.compile(
            rf"\b(?:was|were) associated with (?!(?:an? |the )?{_DIRECTION}\b)"
            r"(?P<v>[^.;:()]{1,80}?)\s*\((?:adjusted )?(?:hazard ratio|odds ratio"
            r"|relative risk|risk ratio|incidence rate ratio|a?HR|a?OR|RR|IRR)\b", re.I)),
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


def _set_aside(text: str, match: re.Match[str], design: str) -> str:
    """Why a design mention says nothing of this study's own design, or ``""``."""
    if _NEGATED.search(text[max(0, match.start() - 12):match.start()]):
        return "negated"
    if design in _STUDY_KINDS:
        noun = _NOUN_AFTER.match(text, match.end())
        span = match.group(0).lower()
        if span.endswith(("reviews", "analyses")) or (
                noun and noun.group(1).lower() in _PLURAL_NOUNS):
            return "plural: other studies"
    if design == "randomized_trial" and _DATA_SPLIT.match(text, match.end()):
        return "a random split of data"
    return ""


def _read_design(text: str) -> FieldReading:
    """The one design the text states as its own, or why none is read.

    In order: a protocol has none; mentions that are negated, plural or a data split are
    set aside; one design left is read; several, with a review that names itself one, read
    as that review; several otherwise are ambiguous.
    """
    planned = _PROTOCOL.search(text)
    if planned:
        return FieldReading("design", note=(
            f"the text describes a study still to be done ({planned.group(0)!r} at "
            f"{planned.start()}); a protocol reports no results, so no design is read"))
    first: dict[str, tuple[str, str, int]] = {}
    set_aside: list[str] = []
    for rule, design, pattern in _DESIGN_RULES:
        for match in pattern.finditer(text):
            why = _set_aside(text, match, design)
            if why:
                set_aside.append(f"{match.group(0)!r} at {match.start()} ({why})")
                continue
            if design not in first or match.start() < first[design][2]:
                first[design] = (rule, match.group(0), match.start())
    if len(first) == 1:
        design, (rule, span, offset) = next(iter(first.items()))
        return FieldReading("design", design, rule, span, offset)
    named = sorted(first.items(), key=lambda i: i[1][2])
    own = None
    if "systematic_review" in first:
        own = min(((m.start(), rule, m) for rule, pattern in _OWN_REVIEW
                   for m in [pattern.search(text)] if m), key=lambda o: o[0], default=None)
    if own and not _OWN_STUDY.search(text):
        offset, rule, match = own
        pooled = ", ".join(f"{d} ({span!r} at {at})" for d, (_, span, at) in named
                           if d != "systematic_review")
        return FieldReading("design", "systematic_review", rule, match.group(0), offset,
                            note=f"a review that names itself one; what it pools: {pooled}")
    if first:
        stated = ", ".join(f"{d} ({span!r} at {at})" for d, (_, span, at) in named)
        return FieldReading("design", note=f"ambiguous: the text states {stated}")
    return FieldReading("design", note="no rule matched a statement of the study design"
                        + (f"; mentions set aside: {', '.join(set_aside)}"
                           if set_aside else ""))


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
