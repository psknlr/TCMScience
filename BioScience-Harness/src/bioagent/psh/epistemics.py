"""TCM evidence, expressed in the compiler's scientific type system.

``bioagent.tcm.model.CLAIM_SUPPORT`` and ``psh.sir.values.LICENSING`` are the same idea
reached from two directions: which sources license which *kind* of claim, as a table rather
than as a comparison against one ordered scale. Neither is a ladder. ``CLAIM_SUPPORT`` maps
a claim kind to the set of ``EvidenceTier`` values admitted for it — which is why a trial
cannot license an attribution, and why a docking score licenses ``mechanism_hypothesis``
and not ``mechanism``. ``LICENSING`` maps a claim kind to the ``StudyDesign`` values
admitted for it, with a grade attached.

Two things the tier table has no room for, and this bridge exists to add:

* **A grade rather than a yes/no.** ``Licensing.EXTRAPOLATED`` is the answer for a source
  that bears on the claim at one remove — a 药典 reporting what the 伤寒论 records, a
  classical incompatibility record read as a safety signal. A set membership test has to
  round that to "no", and rounding it to "no" and rounding it to "yes" are both wrong.
* **The refinement dimensions.** Population, intervention, outcome, surrogate status,
  subject and provenance are each checked against the claim, so "an RCT licenses efficacy"
  stops being the end of the sentence: an RCT in 成人 does not license a claim about 儿童,
  and an RCT on a surrogate endpoint does not license a claim about the outcome that
  surrogate stands in for.

Where the two disagree on the shipped seed they disagree in **one direction and one
grade**: the tier table refuses, and the matrix answers ``EXTRAPOLATED``. It never answers
``DIRECT`` where the tier table refuses, and it never licenses a clinical claim the tier
table refuses. ``tests/test_tcm_epistemics.py`` asserts exactly that, over every relation
and every claim kind in the seed, so a future edit to either table cannot quietly make the
matrix the more permissive of the two.

This module maps one onto the other. A TCM relation becomes an ``EvidenceType`` carrying
its design, its subject and its provenance; a TCM claim kind becomes a ``ClaimType``; and
``psh.sir.licenses`` rules on the pair, returning the grade and a reason naming the
dimension that decided it.

Nothing here imports ``psh.kernel``, and ``bioagent.tcm`` does not import ``psh`` at all:
the dependency sits in the bridge package, where it already sat.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping

from psh.evidence.support import Certainty
from psh.sir import (
    ClaimKind, ClaimType, EvidenceType, LicenseVerdict, Licensing, Provenance, StudyDesign,
    Subject, licenses,
)

from ..tcm.model import CLAIM_KINDS, ActionRelation, ClassicalPassage, EvidenceTier, StudyEvidence

__all__ = [
    "DESIGN_FOR_TIER", "CLAIM_KIND_FOR", "SUBJECT_FOR_DESIGN", "TierMapping",
    "design_for", "evidence_type_for", "claim_type_for", "licensing_for",
]

#: The design a tier maps to when nothing finer is stated. ``PRECLINICAL`` and
#: ``OBSERVATIONAL`` cover several designs each, so ``design_for`` refines them from the
#: study's own ``design`` text and falls back to the weakest member of the group — the
#: conservative direction, since a weaker design licenses no more than a stronger one.
DESIGN_FOR_TIER: Mapping[EvidenceTier, StudyDesign] = {
    EvidenceTier.CLASSICAL_TEXT: StudyDesign.CLASSICAL_TEXT,
    EvidenceTier.EXPERT_EXPERIENCE: StudyDesign.EXPERT_CONSENSUS,
    EvidenceTier.PRECLINICAL: StudyDesign.IN_VITRO,
    EvidenceTier.CASE_REPORT: StudyDesign.CASE_REPORT,
    EvidenceTier.OBSERVATIONAL: StudyDesign.CROSS_SECTIONAL,
    EvidenceTier.RANDOMIZED_TRIAL: StudyDesign.RANDOMISED_TRIAL,
    EvidenceTier.SYSTEMATIC_REVIEW: StudyDesign.SYSTEMATIC_REVIEW,
}

#: ``bioagent.tcm``'s claim kinds are the same eight the compiler knows, by design: the
#: vocabulary was chosen in ``CLAIM_SUPPORT`` first and the compiler adopted it, including
#: the ``mechanism_hypothesis`` / ``mechanism`` split.
CLAIM_KIND_FOR: Mapping[str, ClaimKind] = {
    "attribution": ClaimKind.ATTRIBUTION,
    "traditional_use": ClaimKind.TRADITIONAL_USE,
    "mechanism_hypothesis": ClaimKind.MECHANISM_HYPOTHESIS,
    "mechanism": ClaimKind.MECHANISM,
    "safety_signal": ClaimKind.SAFETY_SIGNAL,
    "association": ClaimKind.ASSOCIATION,
    "efficacy": ClaimKind.EFFICACY,
    "recommendation": ClaimKind.RECOMMENDATION,
}

#: What a design was collected on. A classical passage is about a text; a docking study is
#: about a computation; neither is about a patient, which is the distinction that stops an
#: in-silico result licensing a clinical claim.
SUBJECT_FOR_DESIGN: Mapping[StudyDesign, Subject] = {
    StudyDesign.CLASSICAL_TEXT: Subject.TEXT,
    StudyDesign.COMMENTARY: Subject.TEXT,
    StudyDesign.EXPERT_CONSENSUS: Subject.HUMAN,
    StudyDesign.IN_SILICO: Subject.COMPUTATIONAL,
    StudyDesign.IN_VITRO: Subject.CELL,
    StudyDesign.ANIMAL: Subject.ANIMAL,
}

#: Design vocabulary in the ``design`` free-text field, Chinese and English. Ordered:
#: the first pattern that matches wins, so the more specific ones come first.
_DESIGN_CUES: tuple[tuple[str, StudyDesign], ...] = (
    (r"分子对接|网络药理|计算|模拟|in[\s-]?silico|dock|molecular dynamics|simulation",
     StudyDesign.IN_SILICO),
    (r"动物|小鼠|大鼠|家兔|斑马鱼|在体|体内|animal|mouse|mice|rat|rabbit|zebrafish|in[\s-]?vivo|murine",
     StudyDesign.ANIMAL),
    (r"细胞|离体|体外|in[\s-]?vitro|cell line|culture|assay", StudyDesign.IN_VITRO),
    (r"系统评价|荟萃|meta[\s-]?analys|systematic review", StudyDesign.SYSTEMATIC_REVIEW),
    (r"随机|双盲|安慰剂|randomi[sz]ed|double[\s-]?blind|placebo|\bRCT\b",
     StudyDesign.RANDOMISED_TRIAL),
    (r"非随机|单臂|前后对照|non[\s-]?randomi[sz]ed|single[\s-]?arm|before[\s-]?after",
     StudyDesign.NON_RANDOMISED_TRIAL),
    (r"队列|前瞻|随访|cohort|prospective|follow[\s-]?up", StudyDesign.COHORT),
    (r"病例对照|case[\s-]?control", StudyDesign.CASE_CONTROL),
    (r"横断面|现况|cross[\s-]?sectional|survey", StudyDesign.CROSS_SECTIONAL),
    (r"病例系列|case series", StudyDesign.CASE_SERIES),
    (r"病例报告|个案|case report", StudyDesign.CASE_REPORT),
    (r"指南|guideline", StudyDesign.GUIDELINE),
    (r"共识|教材|药典|consensus|pharmacopoeia|textbook", StudyDesign.EXPERT_CONSENSUS),
    (r"注|笺|释|commentary|annotation", StudyDesign.COMMENTARY),
)

#: Which refinements a tier will accept. A relation declaring tier ``PRECLINICAL`` and a
#: design text reading "randomised" is a contradiction, and honouring the text would let a
#: mislabelled record license a claim its tier does not. The text may only choose **within**
#: the tier's own group.
_TIER_GROUP: Mapping[EvidenceTier, frozenset[StudyDesign]] = {
    EvidenceTier.CLASSICAL_TEXT: frozenset({StudyDesign.CLASSICAL_TEXT,
                                            StudyDesign.COMMENTARY}),
    EvidenceTier.EXPERT_EXPERIENCE: frozenset({StudyDesign.EXPERT_CONSENSUS,
                                               StudyDesign.GUIDELINE}),
    EvidenceTier.PRECLINICAL: frozenset({StudyDesign.IN_SILICO, StudyDesign.IN_VITRO,
                                         StudyDesign.ANIMAL}),
    EvidenceTier.CASE_REPORT: frozenset({StudyDesign.CASE_REPORT,
                                         StudyDesign.CASE_SERIES}),
    EvidenceTier.OBSERVATIONAL: frozenset({StudyDesign.CROSS_SECTIONAL,
                                           StudyDesign.CASE_CONTROL, StudyDesign.COHORT}),
    EvidenceTier.RANDOMIZED_TRIAL: frozenset({StudyDesign.RANDOMISED_TRIAL,
                                              StudyDesign.NON_RANDOMISED_TRIAL}),
    EvidenceTier.SYSTEMATIC_REVIEW: frozenset({StudyDesign.SYSTEMATIC_REVIEW}),
}


@dataclass(frozen=True, slots=True)
class TierMapping:
    """What a tier and a design text resolved to, and whether the text was honoured."""

    tier: EvidenceTier
    design: StudyDesign
    subject: Subject
    refined: bool = False
    note: str = ""


def design_for(tier: EvidenceTier, design_text: str = "") -> TierMapping:
    """Resolve a tier plus its free-text design into a ``StudyDesign`` and a subject."""
    tier = EvidenceTier(tier)
    default = DESIGN_FOR_TIER[tier]
    resolved, refined, note = default, False, ""
    text = (design_text or "").strip()
    if text:
        for pattern, candidate in _DESIGN_CUES:
            if re.search(pattern, text, re.I):
                if candidate in _TIER_GROUP.get(tier, frozenset()):
                    resolved, refined = candidate, True
                else:
                    note = (f"the design text reads {text!r}, which is "
                            f"{candidate.value}, and the record declares tier "
                            f"{tier.name}; the tier decides")
                break
    subject = SUBJECT_FOR_DESIGN.get(resolved, Subject.HUMAN)
    return TierMapping(tier=tier, design=resolved, subject=subject, refined=refined,
                       note=note)


def _provenance(evidence: Any) -> Provenance:
    """Retrieved when the record cites something anyone can look up, else supplied.

    Never ``RETRIEVED_VERIFIED``: this package can say a record carries a PMID and cannot
    say a registered retriever fetched it. That distinction belongs to
    ``psh.evidence.record``, which signs what it retrieves.
    """
    if isinstance(evidence, StudyEvidence):
        if evidence.pmid or evidence.doi or evidence.registry_id:
            return Provenance.RETRIEVED
        return Provenance.SUPPLIED if evidence.citation else Provenance.UNKNOWN
    if isinstance(evidence, ClassicalPassage):
        return Provenance.RETRIEVED if evidence.source else Provenance.UNKNOWN
    return Provenance.UNKNOWN


def evidence_type_for(relation: ActionRelation, *, knowledge: Any = None,
                      outcome: str = "", surrogate: bool = False) -> EvidenceType:
    """The ``EvidenceType`` a TCM relation carries, for the compiler to rule on.

    ``knowledge`` is an optional ``TCMKnowledgeBase``. With it the design text and the
    citation come from the cited evidence records; without it the relation's own tier is
    all there is, which is a weaker answer and never a wrong one — an unrefined tier maps
    to the weakest design in its group.
    """
    design_text, records = "", []
    if knowledge is not None:
        records = list(knowledge.evidence(relation.evidence_ids))
        for record in records:
            if isinstance(record, StudyEvidence) and record.design:
                design_text = record.design
                break
    mapping = design_for(relation.tier, design_text)
    retracted = any(isinstance(r, StudyEvidence) and r.retracted is True for r in records)
    provenance = (min((_provenance(r) for r in records),
                      key=lambda p: list(Provenance).index(p))
                  if records else Provenance.UNKNOWN)
    identifier = ""
    for record in records:
        identifier = getattr(record, "pmid", "") or getattr(record, "doi", "") \
            or getattr(record, "registry_id", "") or getattr(record, "id", "")
        if identifier:
            break
    return EvidenceType(
        design=mapping.design, subject=mapping.subject,
        population=relation.population, intervention=relation.subject_id,
        outcome=outcome or relation.condition, outcome_is_surrogate=surrogate,
        provenance=provenance, identifier=identifier,
        retracted=True if retracted else None)


def claim_type_for(claim_kind: str, *, subject: Subject = Subject.HUMAN,
                   population: str = "", intervention: str = "", outcome: str = "",
                   certainty: Certainty = Certainty.MODERATE) -> ClaimType:
    """The ``ClaimType`` for one of ``bioagent.tcm``'s claim kinds."""
    if claim_kind not in CLAIM_KIND_FOR:
        raise ValueError(
            f"claim kind {claim_kind!r} is not one of {sorted(CLAIM_KIND_FOR)}")
    kind = CLAIM_KIND_FOR[claim_kind]
    if kind is ClaimKind.ATTRIBUTION and subject is Subject.HUMAN:
        # An attribution is a claim about what a text records, not about patients. The
        # default would otherwise make every attribution a subject mismatch against the
        # passage that licenses it.
        subject = Subject.TEXT
    return ClaimType(kind=kind, subject=subject, population=population,
                     intervention=intervention, outcome=outcome, certainty=certainty)


def licensing_for(knowledge: Any, relation_id: str, *, claim_kind: str = "efficacy",
                  subject: Subject = Subject.HUMAN, population: str = "",
                  outcome: str = "", certainty: Certainty = Certainty.MODERATE
                  ) -> LicenseVerdict:
    """Rule on a TCM relation with the compiler's matrix, alongside the tier table.

    The case the two answer differently, and the reason this function exists::

        kb.applicability(classical, claim_kind="safety_signal").verdict   # unsupported
        licensing_for(kb, classical, claim_kind="safety_signal").grade    # extrapolated

    Both are consistent with their own model. ``CLAIM_SUPPORT`` admits a set of tiers and
    has to answer yes or no, so a 十八反 record — a safety signal that predates every
    clinical design on the scale — is refused outright. The matrix grades it
    ``EXTRAPOLATED`` and says why, which is the answer a reader can act on: cite it, and
    do not call it a pharmacovigilance finding.
    """
    relation = knowledge.relations[relation_id]
    evidence = evidence_type_for(relation, knowledge=knowledge, outcome=outcome)
    claim = claim_type_for(claim_kind, subject=subject, population=population,
                           intervention=relation.subject_id,
                           outcome=outcome or relation.condition, certainty=certainty)
    return licenses(evidence, claim)
