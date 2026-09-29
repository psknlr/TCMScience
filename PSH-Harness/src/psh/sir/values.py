"""Scientific types: what a value *is*, scientifically, and what it licenses.

An ordinary agent type system knows ``str``, ``float``, ``DataFrame``, ``File``. That
vocabulary cannot express the distinction this harness exists to keep:

    Evidence[design=animal, subject=animal, outcome=viral_load]
        ──X──>
    Claim[kind=efficacy, subject=human]

Both are "a dict with some numbers in it" to a program type system, and the second is a
different scientific object from the first. ``psh.evidence.scope`` already answers this
question *about a sentence, after a run has produced it*. By then the analysis has been
run, the budget spent and the extrapolation written down; the check can only refuse the
output. This module answers the same question *about a plan, before it executes* — so a
workflow whose fourth step asserts human efficacy from a mouse experiment is refused at
the plan, the way ``PlanValidator`` already refuses a step that wants a destination the
run does not hold.

Two design decisions are load-bearing.

**The matrix is two-dimensional, not a ladder.** A single ``EvidenceTier`` ordering says a
classical text is the weakest evidence there is. That is true of a clinical efficacy claim
and false of an attribution: for "the Shanghan Lun prescribes 桂枝汤 for this presentation"
the classical text is the *only* thing that can license it, and a randomised trial cannot.
So ``LICENSING`` is a table over (design, claim kind) rather than a comparison, and
``StudyDesign`` is deliberately **not** ordered.

**A mismatch is a finding, not weak signal.** Every rule below returns
``Licensing.UNLICENSED`` or ``Licensing.EXTRAPOLATED`` with a reason naming the dimension.
The same argument ``scope.py`` makes: an extrapolated claim reuses its source's exact
vocabulary, so any similarity measure scores it *higher* than a cautiously worded true
claim. Structure is what sees it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Iterable, Mapping

from ..evidence.support import Certainty
from ..labels import Sensitivity

__all__ = [
    "StudyDesign", "Subject", "Provenance", "ClaimKind", "Licensing", "LicenseVerdict",
    "EvidenceType", "ClaimType", "DataType", "ScientificType", "FlowLabel",
    "LICENSING", "MAX_CERTAINTY", "CLINICAL_KINDS", "UNEVIDENTIAL",
    "licenses", "provenance_rank", "normalise_term", "join_labels",
]


class StudyDesign(str, Enum):
    """How a piece of evidence was produced. Deliberately unordered.

    There is no ``<`` on this enum and that is the point: ordering it would require
    deciding whether a classical passage is above or below an in-vitro assay, and the
    honest answer is "for which claim?". ``LICENSING`` answers that per claim kind.
    """

    CLASSICAL_TEXT = "classical_text"        # 经典文献原文
    COMMENTARY = "commentary"                # 注家/历代医家注释
    EXPERT_CONSENSUS = "expert_consensus"    # 教材、药典、专家共识
    IN_SILICO = "in_silico"                  # docking, network pharmacology, simulation
    IN_VITRO = "in_vitro"
    ANIMAL = "animal"
    CASE_REPORT = "case_report"
    CASE_SERIES = "case_series"
    CROSS_SECTIONAL = "cross_sectional"
    CASE_CONTROL = "case_control"
    COHORT = "cohort"
    NON_RANDOMISED_TRIAL = "non_randomised_trial"
    RANDOMISED_TRIAL = "randomised_trial"
    SYSTEMATIC_REVIEW = "systematic_review"
    GUIDELINE = "guideline"
    UNKNOWN = "unknown"


class Subject(str, Enum):
    """What the evidence was collected *on*, and what a claim is *about*."""

    HUMAN = "human"
    ANIMAL = "animal"
    CELL = "cell"
    COMPUTATIONAL = "computational"
    TEXT = "text"                            # a passage, not an experiment
    UNSPECIFIED = "unspecified"


class Provenance(str, Enum):
    """Where the content came from. Ordered by ``provenance_rank``, weakest first."""

    UNKNOWN = "unknown"
    GENERATED = "generated"                  # a model wrote it
    SUPPLIED = "supplied"                    # a human pasted it
    RETRIEVED = "retrieved"                  # a capability fetched it
    RETRIEVED_VERIFIED = "retrieved_verified"  # fetched by a registered, signing retriever


_PROVENANCE_ORDER: tuple[Provenance, ...] = (
    Provenance.UNKNOWN, Provenance.GENERATED, Provenance.SUPPLIED,
    Provenance.RETRIEVED, Provenance.RETRIEVED_VERIFIED)


def provenance_rank(value: Provenance) -> int:
    """Higher is stronger. Used for the join, which takes the weakest."""
    return _PROVENANCE_ORDER.index(value)


#: Provenances that cannot license any claim at all. A model's own output is not evidence
#: for the model's own conclusion — that is the circularity ``EvidenceRecord`` was built to
#: prevent, arriving one layer earlier. ``UNKNOWN`` joins it because a value nobody can
#: attribute is not better than one attributed to the model.
UNEVIDENTIAL: frozenset[Provenance] = frozenset({Provenance.GENERATED, Provenance.UNKNOWN})


class ClaimKind(str, Enum):
    """What sort of assertion a claim is. Each needs a different kind of source."""

    ATTRIBUTION = "attribution"              # 记载：this text says this
    TRADITIONAL_USE = "traditional_use"      # 传统应用/功效
    MECHANISM_HYPOTHESIS = "mechanism_hypothesis"  # 机制假说: worth testing, untested
    MECHANISM = "mechanism"                  # acts on this target/pathway
    SAFETY_SIGNAL = "safety_signal"
    ASSOCIATION = "association"
    EFFICACY = "efficacy"                    # improves this outcome in these patients
    RECOMMENDATION = "recommendation"        # should be given to these patients


class Licensing(str, Enum):
    """Whether a source licenses a claim, and how directly."""

    DIRECT = "direct"
    EXTRAPOLATED = "extrapolated"            # related, requires inference the source omits
    UNLICENSED = "unlicensed"                # the source cannot reach this claim at all


D = StudyDesign
K = ClaimKind

#: The matrix. ``LICENSING[claim kind][design]`` is the *best* grade that design can reach
#: for that kind of claim, before the refinement checks below narrow it further. A design
#: absent from a row is ``UNLICENSED`` for it.
#:
#: Read the ATTRIBUTION row first, because it is the one a single ordered tier gets wrong:
#: a classical text licenses an attribution DIRECTLY, and a randomised trial cannot license
#: one at all — a trial of 桂枝汤 says nothing about what the Shanghan Lun records.
#: RECOMMENDATION is the mirror image: no single study licenses "should be given to", which
#: is the rule ``evidence/scope.py`` already enforces on prose.
LICENSING: Mapping[ClaimKind, Mapping[StudyDesign, Licensing]] = {
    K.ATTRIBUTION: {
        D.CLASSICAL_TEXT: Licensing.DIRECT,
        D.COMMENTARY: Licensing.DIRECT,
        D.EXPERT_CONSENSUS: Licensing.EXTRAPOLATED,
    },
    K.TRADITIONAL_USE: {
        D.CLASSICAL_TEXT: Licensing.DIRECT,
        D.COMMENTARY: Licensing.DIRECT,
        D.EXPERT_CONSENSUS: Licensing.DIRECT,
        D.CASE_SERIES: Licensing.EXTRAPOLATED,
    },
    #: A docking score is a reason to run an assay, not a finding about a pathway. It
    #: licenses the hypothesis row and nothing on the MECHANISM row below — the rule
    #: ``bioagent.tcm.model.CLAIM_SUPPORT`` states for tiers, stated here for designs.
    K.MECHANISM_HYPOTHESIS: {
        D.IN_SILICO: Licensing.DIRECT,
        D.IN_VITRO: Licensing.DIRECT,
        D.ANIMAL: Licensing.DIRECT,
        D.CLASSICAL_TEXT: Licensing.EXTRAPOLATED,
        D.EXPERT_CONSENSUS: Licensing.EXTRAPOLATED,
        D.COHORT: Licensing.EXTRAPOLATED,
        D.CASE_CONTROL: Licensing.EXTRAPOLATED,
        D.RANDOMISED_TRIAL: Licensing.EXTRAPOLATED,
        D.SYSTEMATIC_REVIEW: Licensing.EXTRAPOLATED,
    },
    K.MECHANISM: {
        D.IN_VITRO: Licensing.DIRECT,
        D.ANIMAL: Licensing.DIRECT,
        D.COHORT: Licensing.EXTRAPOLATED,
        D.CASE_CONTROL: Licensing.EXTRAPOLATED,
        D.RANDOMISED_TRIAL: Licensing.EXTRAPOLATED,
        D.SYSTEMATIC_REVIEW: Licensing.EXTRAPOLATED,
    },
    K.SAFETY_SIGNAL: {
        D.CASE_REPORT: Licensing.DIRECT,
        D.CASE_SERIES: Licensing.DIRECT,
        D.CROSS_SECTIONAL: Licensing.DIRECT,
        D.CASE_CONTROL: Licensing.DIRECT,
        D.COHORT: Licensing.DIRECT,
        D.NON_RANDOMISED_TRIAL: Licensing.DIRECT,
        D.RANDOMISED_TRIAL: Licensing.DIRECT,
        D.SYSTEMATIC_REVIEW: Licensing.DIRECT,
        D.EXPERT_CONSENSUS: Licensing.EXTRAPOLATED,
        D.CLASSICAL_TEXT: Licensing.EXTRAPOLATED,
        D.ANIMAL: Licensing.EXTRAPOLATED,
        D.IN_VITRO: Licensing.EXTRAPOLATED,
    },
    K.ASSOCIATION: {
        D.CROSS_SECTIONAL: Licensing.DIRECT,
        D.CASE_CONTROL: Licensing.DIRECT,
        D.COHORT: Licensing.DIRECT,
        D.NON_RANDOMISED_TRIAL: Licensing.DIRECT,
        D.RANDOMISED_TRIAL: Licensing.DIRECT,
        D.SYSTEMATIC_REVIEW: Licensing.DIRECT,
        D.CASE_SERIES: Licensing.EXTRAPOLATED,
    },
    K.EFFICACY: {
        D.RANDOMISED_TRIAL: Licensing.DIRECT,
        D.SYSTEMATIC_REVIEW: Licensing.DIRECT,
        D.NON_RANDOMISED_TRIAL: Licensing.EXTRAPOLATED,
        D.COHORT: Licensing.EXTRAPOLATED,
    },
    K.RECOMMENDATION: {
        D.GUIDELINE: Licensing.DIRECT,
        D.SYSTEMATIC_REVIEW: Licensing.EXTRAPOLATED,
    },
}

#: The strongest certainty a single study of each design can license. ``DEFINITIVE`` is
#: absent from every row on purpose: "cures", "eliminates", "in all patients" is not a
#: thing one source establishes, which is the rule ``evidence/support.py`` applies to
#: sentences and this applies to plans.
MAX_CERTAINTY: Mapping[StudyDesign, Certainty] = {
    D.CLASSICAL_TEXT: Certainty.MODERATE,
    D.COMMENTARY: Certainty.MODERATE,
    D.EXPERT_CONSENSUS: Certainty.MODERATE,
    D.IN_SILICO: Certainty.TENTATIVE,
    D.IN_VITRO: Certainty.TENTATIVE,
    D.ANIMAL: Certainty.TENTATIVE,
    D.CASE_REPORT: Certainty.TENTATIVE,
    D.CASE_SERIES: Certainty.TENTATIVE,
    D.CROSS_SECTIONAL: Certainty.MODERATE,
    D.CASE_CONTROL: Certainty.MODERATE,
    D.COHORT: Certainty.MODERATE,
    D.NON_RANDOMISED_TRIAL: Certainty.MODERATE,
    D.RANDOMISED_TRIAL: Certainty.STRONG,
    D.SYSTEMATIC_REVIEW: Certainty.STRONG,
    D.GUIDELINE: Certainty.STRONG,
    D.UNKNOWN: Certainty.UNCERTAIN,
}

#: The subject a claim kind is *about* when it is about people. An efficacy or
#: recommendation claim is a claim about patients however carefully it is worded, so
#: evidence collected on something else cannot license it — only motivate a study that
#: could. This is the ``EvidenceScopeError`` case.
CLINICAL_KINDS: frozenset[ClaimKind] = frozenset({K.EFFICACY, K.RECOMMENDATION})

_PUNCT = re.compile(r"[\s\-_/]+")


def _a(word: str) -> str:
    """``a``/``an`` for a word the message is about. Diagnostics are read by people and
    fed back to models; "a animal source" reads as carelessness in both audiences."""
    return "an" if str(word)[:1].lower() in "aeiou" else "a"


def normalise_term(value: str) -> str:
    """Casefold and strip separators, so ``Piezo-1`` and ``piezo 1`` compare equal.

    Deliberately shallow. It cannot resolve synonyms (``T2DM`` vs ``type 2 diabetes``)
    without an ontology, and pretending otherwise would make a mismatch verdict depend on
    vocabulary luck. An unresolved synonym surfaces as a *mismatch* — visible and
    correctable by naming the term the same way — rather than as a silent pass.
    """
    return _PUNCT.sub("", str(value or "").strip().lower())


@dataclass(frozen=True, slots=True)
class LicenseVerdict:
    """Whether a source licenses a claim, with the dimension that decided it."""

    grade: Licensing
    reasons: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        """True when the claim may be asserted from this source without extrapolation."""
        return self.grade is Licensing.DIRECT

    @property
    def usable(self) -> bool:
        """True when the claim may be asserted *if the extrapolation is declared*."""
        return self.grade is not Licensing.UNLICENSED

    def describe(self) -> str:
        return f"{self.grade.value}" + (f": {'; '.join(self.reasons)}" if self.reasons else "")


@dataclass(frozen=True, slots=True)
class EvidenceType:
    """The type of a value that is evidence: how it was produced and about what.

    The refinement fields (``population``, ``intervention``, ``comparator``, ``outcome``)
    are free text compared by ``normalise_term``. They are refinements in the type-theory
    sense — two ``EvidenceType``s with the same design and different interventions are
    different types, and the compiler will not let one stand in for the other.
    """

    design: StudyDesign = StudyDesign.UNKNOWN
    subject: Subject = Subject.UNSPECIFIED
    population: str = ""
    intervention: str = ""
    comparator: str = ""
    outcome: str = ""
    outcome_is_surrogate: bool = False
    provenance: Provenance = Provenance.UNKNOWN
    identifier: str = ""                     # PMID / DOI / registry id / passage id
    retracted: bool | None = None

    def describe(self) -> str:
        parts = [self.design.value]
        if self.subject is not Subject.UNSPECIFIED:
            parts.append(f"in {self.subject.value}")
        if self.population:
            parts.append(f"({self.population})")
        if self.intervention:
            parts.append(f"of {self.intervention}")
        if self.outcome:
            parts.append(f"on {self.outcome}"
                         + (" [surrogate]" if self.outcome_is_surrogate else ""))
        return " ".join(parts)

    def as_dict(self) -> dict[str, Any]:
        return {"design": self.design.value, "subject": self.subject.value,
                "population": self.population, "intervention": self.intervention,
                "comparator": self.comparator, "outcome": self.outcome,
                "outcome_is_surrogate": self.outcome_is_surrogate,
                "provenance": self.provenance.value, "identifier": self.identifier,
                "retracted": self.retracted}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "EvidenceType":
        return cls(
            design=StudyDesign(data.get("design") or StudyDesign.UNKNOWN.value),
            subject=Subject(data.get("subject") or Subject.UNSPECIFIED.value),
            population=str(data.get("population") or ""),
            intervention=str(data.get("intervention") or ""),
            comparator=str(data.get("comparator") or ""),
            outcome=str(data.get("outcome") or ""),
            outcome_is_surrogate=bool(data.get("outcome_is_surrogate")),
            provenance=Provenance(data.get("provenance") or Provenance.UNKNOWN.value),
            identifier=str(data.get("identifier") or ""),
            retracted=data.get("retracted"))


@dataclass(frozen=True, slots=True)
class ClaimType:
    """The type of a value that is a claim: what is being asserted, about whom, how firmly."""

    kind: ClaimKind = ClaimKind.ASSOCIATION
    subject: Subject = Subject.UNSPECIFIED
    population: str = ""
    intervention: str = ""
    outcome: str = ""
    certainty: Certainty = Certainty.MODERATE

    def describe(self) -> str:
        parts = [f"{self.kind.value} claim"]
        if self.intervention:
            parts.append(f"about {self.intervention}")
        if self.outcome:
            parts.append(f"on {self.outcome}")
        if self.subject is not Subject.UNSPECIFIED:
            parts.append(f"in {self.subject.value}")
        if self.population:
            parts.append(f"({self.population})")
        return " ".join(parts) + f", asserted {self.certainty.value}"

    def as_dict(self) -> dict[str, Any]:
        return {"kind": self.kind.value, "subject": self.subject.value,
                "population": self.population, "intervention": self.intervention,
                "outcome": self.outcome, "certainty": self.certainty.value}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ClaimType":
        return cls(
            kind=ClaimKind(data.get("kind") or ClaimKind.ASSOCIATION.value),
            subject=Subject(data.get("subject") or Subject.UNSPECIFIED.value),
            population=str(data.get("population") or ""),
            intervention=str(data.get("intervention") or ""),
            outcome=str(data.get("outcome") or ""),
            certainty=Certainty(data.get("certainty") or Certainty.MODERATE.value))


def _mismatch(left: str, right: str) -> bool:
    """True when both terms are stated and they are not the same term."""
    a, b = normalise_term(left), normalise_term(right)
    return bool(a and b and a != b)


def licenses(evidence: EvidenceType, claim: ClaimType) -> LicenseVerdict:
    """Does ``evidence`` license ``claim``? The compiler's central question.

    The checks run in the order their failures matter, and every one of them can only
    *lower* the grade the matrix allowed — there is no path that raises it, so a rule
    added later cannot accidentally license something an earlier rule refused.
    """
    reasons: list[str] = []

    # 0. Provenance. Checked first because it is the one failure no amount of matching
    #    fixes: a model's own paragraph is not a source, however well its words align.
    if evidence.provenance in UNEVIDENTIAL:
        return LicenseVerdict(Licensing.UNLICENSED, (
            f"the source's provenance is {evidence.provenance.value}; a value the system "
            "generated or cannot attribute is not evidence for a claim",))
    if evidence.retracted is True:
        return LicenseVerdict(Licensing.UNLICENSED, (
            f"source {evidence.identifier or 'this source'} is retracted",))

    # 1. The matrix: can this kind of study reach this kind of claim at all?
    grade = LICENSING.get(claim.kind, {}).get(evidence.design, Licensing.UNLICENSED)
    if grade is Licensing.UNLICENSED:
        return LicenseVerdict(Licensing.UNLICENSED, (
            f"{_a(evidence.design.value)} {evidence.design.value} source cannot license "
            f"{_a(claim.kind.value)} {claim.kind.value} claim; {claim.kind.value} needs "
            f"one of {sorted(d.value for d in LICENSING.get(claim.kind, {}))}",))
    if grade is Licensing.EXTRAPOLATED:
        reasons.append(f"{_a(evidence.design.value)} {evidence.design.value} source only "
                       f"indirectly supports {_a(claim.kind.value)} {claim.kind.value} claim")

    # 2. Subject. The EvidenceScopeError case: mouse work does not license human efficacy.
    #    For non-clinical kinds a subject gap is an extrapolation the author may declare;
    #    for a clinical kind it is a refusal, because "it worked in mice" and "it works in
    #    patients" are the two statements this system exists to keep apart.
    if (evidence.subject is not Subject.UNSPECIFIED
            and claim.subject is not Subject.UNSPECIFIED
            and evidence.subject is not claim.subject):
        if claim.kind in CLINICAL_KINDS and claim.subject is Subject.HUMAN:
            return LicenseVerdict(Licensing.UNLICENSED, tuple(reasons) + (
                f"evidence collected in {evidence.subject.value} cannot license "
                f"{_a(claim.kind.value)} {claim.kind.value} claim about humans; that "
                "inference needs a clinical study, not a stronger reading of this one",))
        grade = Licensing.EXTRAPOLATED
        reasons.append(f"the source studied {evidence.subject.value} and the claim is "
                       f"about {claim.subject.value}")

    # 3. Intervention. A claim about a different agent is not weakly supported; it is
    #    about something else. (``claims.py`` calls this the subject mismatch, and it is
    #    the defect that made a CTX paper "support" a P1NP claim.)
    if _mismatch(evidence.intervention, claim.intervention):
        return LicenseVerdict(Licensing.UNLICENSED, tuple(reasons) + (
            f"the source studied {evidence.intervention!r} and the claim is about "
            f"{claim.intervention!r}",))

    # 4. Outcome, including the surrogate substitution named rather than merely failed.
    if _mismatch(evidence.outcome, claim.outcome):
        return LicenseVerdict(Licensing.UNLICENSED, tuple(reasons) + (
            f"the source measured {evidence.outcome!r} and the claim is about "
            f"{claim.outcome!r}",))
    if evidence.outcome_is_surrogate and claim.kind in (K.EFFICACY, K.ASSOCIATION):
        grade = Licensing.EXTRAPOLATED
        reasons.append(f"{evidence.outcome or 'the measured outcome'} is a surrogate; "
                       f"{_a(claim.kind.value)} {claim.kind.value} claim on it is an "
                       "inference the source does not make")

    # 5. Population refinement, when both are stated.
    if _mismatch(evidence.population, claim.population):
        grade = Licensing.EXTRAPOLATED
        reasons.append(f"the source enrolled {evidence.population!r} and the claim is "
                       f"about {claim.population!r}")

    # 6. Certainty ceiling. An ATTRIBUTION may be asserted firmly — the text really does
    #    say it — so it is exempt from the design table and not from the DEFINITIVE rule.
    if claim.certainty is Certainty.DEFINITIVE:
        return LicenseVerdict(Licensing.UNLICENSED, tuple(reasons) + (
            "no single source licenses a definitive assertion (\"cures\", \"eliminates\", "
            "\"in all patients\"); state it at most as strong",))
    ceiling = (Certainty.STRONG if claim.kind is K.ATTRIBUTION
               else MAX_CERTAINTY.get(evidence.design, Certainty.UNCERTAIN))
    if claim.certainty.rank < ceiling.rank:
        grade = Licensing.EXTRAPOLATED
        reasons.append(f"the claim is asserted {claim.certainty.value} and "
                       f"{_a(evidence.design.value)} {evidence.design.value} source "
                       f"supports at most {ceiling.value}")

    # 7. Weak provenance caps a clinical claim. Supplied text may back a mechanism note;
    #    it may not be the sole basis of an efficacy claim, which is the rule
    #    ``EvidenceRecord.provenance_caveat`` applies after the fact.
    if evidence.provenance is Provenance.SUPPLIED and claim.kind in CLINICAL_KINDS:
        grade = Licensing.EXTRAPOLATED
        reasons.append("the source text was supplied rather than retrieved through a "
                       "trusted capability, so its provenance is unverified")

    return LicenseVerdict(grade, tuple(reasons))


# --------------------------------------------------------------------- plain data

@dataclass(frozen=True, slots=True)
class DataType:
    """A value that is neither evidence nor a claim: a table, a figure, a fitted model.

    ``shape`` and ``unit`` are documentation the compiler passes through; ``rows`` feeds
    the statistical pass, which needs to know whether a step that declares 20,000 tests is
    plausible against the data it was handed.
    """

    kind: str = "object"                     # table | figure | model | sequence | object
    unit: str = ""
    rows: int | None = None
    columns: tuple[str, ...] = ()
    schema: Mapping[str, Any] = field(default_factory=dict)

    def describe(self) -> str:
        size = f", {self.rows} rows" if self.rows is not None else ""
        return f"{self.kind}{size}"

    def as_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "unit": self.unit, "rows": self.rows,
                "columns": list(self.columns), "schema": dict(self.schema)}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "DataType":
        return cls(kind=str(data.get("kind") or "object"), unit=str(data.get("unit") or ""),
                   rows=data.get("rows"), columns=tuple(data.get("columns") or ()),
                   schema=dict(data.get("schema") or {}))


@dataclass(frozen=True, slots=True)
class ScientificType:
    """The type of one value in the IR: at most one of evidence, claim or data.

    A value is not all three at once, and a node that says it produces both evidence and a
    claim is describing two values — which is a structure error, caught at construction
    rather than by whichever pass happens to read the wrong field first.
    """

    evidence: EvidenceType | None = None
    claim: ClaimType | None = None
    data: DataType | None = None

    def __post_init__(self) -> None:
        stated = [f for f in (self.evidence, self.claim, self.data) if f is not None]
        if len(stated) > 1:
            raise ValueError(
                "a scientific type is evidence, a claim or data — not several at once; "
                "a step producing two of them produces two values, so give it two ports")

    @property
    def is_evidence(self) -> bool:
        return self.evidence is not None

    @property
    def is_claim(self) -> bool:
        return self.claim is not None

    @property
    def stated(self) -> bool:
        return any(f is not None for f in (self.evidence, self.claim, self.data))

    def describe(self) -> str:
        for member in (self.evidence, self.claim, self.data):
            if member is not None:
                return member.describe()
        return "unstated"

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if self.evidence is not None:
            out["evidence"] = self.evidence.as_dict()
        if self.claim is not None:
            out["claim"] = self.claim.as_dict()
        if self.data is not None:
            out["data"] = self.data.as_dict()
        return out

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ScientificType":
        return cls(
            evidence=EvidenceType.from_dict(data["evidence"]) if data.get("evidence") else None,
            claim=ClaimType.from_dict(data["claim"]) if data.get("claim") else None,
            data=DataType.from_dict(data["data"]) if data.get("data") else None)


# ------------------------------------------------------------------- flow labels

@dataclass(frozen=True, slots=True)
class FlowLabel:
    """What travels with a value through the graph: four dimensions, one join.

    ``psh.labels.DataLabel`` is the sensitivity dimension and it is already a lattice. The
    other three are the ones a research workflow loses:

    * **provenance** — a table computed from supplied text is no better attributed than the
      text. The join takes the *weakest* contributor.
    * **designs** — the set of study designs that fed this value. A union rather than a
      maximum, because "which claims may rest on this?" is answered by asking whether *any*
      contributor licenses the claim, and a single ordered tier cannot express that.
    * **licence terms** — a dataset that may be used for research only taints everything
      derived from it, which is how a licence condition survives three transformations.

    Sensitivity is carried as ``psh.labels.Sensitivity`` rather than re-implemented, so the
    compile-time label and the runtime label are the same lattice on that dimension.
    """

    sensitivity: Sensitivity = Sensitivity.PUBLIC
    provenance: Provenance = Provenance.RETRIEVED_VERIFIED
    designs: frozenset[StudyDesign] = frozenset()
    license_terms: frozenset[str] = frozenset()
    #: True once this value was computed from another rather than declared. A derived value
    #: may not assert a lower sensitivity than its inputs without an explicit gate.
    derived: bool = False

    def join(self, other: "FlowLabel") -> "FlowLabel":
        """The most restrictive of two labels, on every dimension at once."""
        return FlowLabel(
            sensitivity=max(self.sensitivity, other.sensitivity),
            provenance=min(self.provenance, other.provenance, key=provenance_rank),
            designs=self.designs | other.designs,
            license_terms=self.license_terms | other.license_terms,
            derived=True)

    def with_sensitivity(self, sensitivity: Sensitivity) -> "FlowLabel":
        return replace(self, sensitivity=sensitivity)

    def describe(self) -> str:
        designs = ", ".join(sorted(d.value for d in self.designs)) or "none"
        terms = ", ".join(sorted(self.license_terms)) or "none"
        return (f"{self.sensitivity.name}/{self.provenance.value} "
                f"[designs: {designs}] [licence: {terms}]")

    def as_dict(self) -> dict[str, Any]:
        return {"sensitivity": self.sensitivity.name, "provenance": self.provenance.value,
                "designs": sorted(d.value for d in self.designs),
                "license_terms": sorted(self.license_terms), "derived": self.derived}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "FlowLabel":
        return cls(
            sensitivity=Sensitivity[data.get("sensitivity", "PUBLIC")],
            provenance=Provenance(data.get("provenance")
                                  or Provenance.RETRIEVED_VERIFIED.value),
            designs=frozenset(StudyDesign(d) for d in data.get("designs") or ()),
            license_terms=frozenset(data.get("license_terms") or ()),
            derived=bool(data.get("derived")))


def join_labels(labels: Iterable[FlowLabel]) -> FlowLabel:
    """Join any number of flow labels; the empty join is the bottom label."""
    out: FlowLabel | None = None
    for label in labels:
        out = label if out is None else out.join(label)
    return out if out is not None else FlowLabel()
