"""TCM evidence in the compiler's scientific type system.

``bioagent.tcm.model.CLAIM_SUPPORT`` and ``psh.sir.values.LICENSING`` are two tables saying
which sources license which kind of claim — one over ``EvidenceTier``, one over
``StudyDesign``. They were written independently and they agree on the hard cases: a trial
cannot license an attribution, a docking score licenses a mechanism *hypothesis* and not a
mechanism. That agreement is asserted here, because it is the part a future edit to either
table could break.

What the matrix adds is resolution. A set membership test answers yes or no; the matrix
answers ``DIRECT``, ``EXTRAPOLATED`` or ``UNLICENSED`` with a reason naming the dimension
that decided it, and checks population, intervention, outcome and subject on top of design.
The last test in the divergence section is the invariant that keeps the addition honest:
wherever the two disagree, it is the matrix saying ``EXTRAPOLATED`` where the tier table
refuses — never ``DIRECT``, and never the other way round.
"""

from __future__ import annotations

import pytest

pytest.importorskip("psh", reason="the epistemics bridge needs PSH-Harness importable")

from psh.evidence.support import Certainty                       # noqa: E402
from psh.sir import ClaimKind, Licensing, Provenance, StudyDesign, Subject  # noqa: E402

from bioagent.psh import (                                        # noqa: E402
    CLAIM_KIND_FOR, claim_type_for, design_for, evidence_type_for, licensing_for,
)
from bioagent.tcm.knowledge import seed                           # noqa: E402
from bioagent.tcm.model import ActionRelation, EvidenceTier, StudyEvidence  # noqa: E402

pytestmark = pytest.mark.unit


@pytest.fixture
def kb():
    """A fresh seed per test.

    Not ``default_knowledge()``: that memoises one base for the whole process, so a test
    calling ``with_trial`` on it would leave a synthetic trial in the knowledge every later
    test — and every later *tool* — reads. ``seed()`` builds a new one.
    """
    return seed()


def with_trial(kb, *, tier=EvidenceTier.RANDOMIZED_TRIAL, design="randomised, double-blind",
               condition="chronic heart failure", population="adults"):
    """Add a synthetic trial to a copy of the seed.

    Synthetic on purpose: the checked-in seed cites public-domain texts, the pharmacopoeia
    and a textbook, and holds **no invented trials**. A test that needed one therefore
    builds it here, where it is plainly a fixture and cannot be mistaken for knowledge.
    """
    study = StudyEvidence(id="study.synthetic_rct", tier=tier, subject_id="herb.huangqi",
                          design=design, condition=condition, population=population,
                          outcome="6-minute walk distance", pmid="00000000")
    relation = ActionRelation(id="relation.synthetic", subject_id="herb.huangqi",
                              predicate="treats", object_id="syndrome.qixu", tier=tier,
                              evidence_ids=("study.synthetic_rct",),
                              population=population, condition=condition)
    kb.extend(studies=[study], relations=[relation])
    return relation.id


# ============================================================ the divergence

def test_both_tables_refuse_a_trial_an_attribution(kb):
    """A trial is the strongest thing on the tier scale and cannot say what the 伤寒论 says.

    This is the case a *threshold* gets wrong — tier 6 clears any tier-1 minimum — and
    the reason ``CLAIM_SUPPORT`` is a set and ``LICENSING`` is a table. Both refuse, and
    the matrix says which dimension decided.
    """
    fresh = seed()
    relation_id = with_trial(fresh)

    assert fresh.applicability(relation_id, claim_kind="attribution").verdict \
        == "unsupported"

    verdict = licensing_for(fresh, relation_id, claim_kind="attribution")
    assert verdict.grade is Licensing.UNLICENSED
    assert "attribution" in verdict.reasons[0]
    assert "classical_text" in verdict.reasons[0]


def test_a_classical_passage_licenses_an_attribution_and_not_an_efficacy_claim(kb):
    classical = next(r for r in kb.relations.values()
                     if r.tier is EvidenceTier.CLASSICAL_TEXT)
    assert licensing_for(kb, classical.id, claim_kind="attribution",
                         subject=Subject.TEXT).grade is Licensing.DIRECT
    assert licensing_for(kb, classical.id, claim_kind="efficacy").grade \
        is Licensing.UNLICENSED
    # And the tier table agrees on both, which is the point: the matrix is an addition,
    # not a replacement.
    assert kb.applicability(classical.id, claim_kind="efficacy").verdict == "unsupported"
    assert kb.applicability(classical.id, claim_kind="attribution").verdict \
        == "within_scope"


def test_both_tables_license_a_classical_text_for_traditional_use(kb):
    """A classical text is the primary record of a traditional use, and both say so."""
    classical = next(r for r in kb.relations.values()
                     if r.tier is EvidenceTier.CLASSICAL_TEXT)
    assert kb.applicability(classical.id, claim_kind="traditional_use").verdict \
        == "within_scope"
    assert licensing_for(kb, classical.id, claim_kind="traditional_use",
                         subject=Subject.TEXT).grade is Licensing.DIRECT


def test_a_docking_score_licenses_a_hypothesis_and_not_a_mechanism():
    """网络药理学 output is a reason to run an assay, not a finding about a pathway.

    The distinction ``CLAIM_SUPPORT`` draws between ``mechanism_hypothesis`` and
    ``mechanism``, drawn the same way in the design matrix. Without it a docking score
    licenses "黄芪 acts on the PI3K/Akt pathway", which is the single most common
    overstatement in the network-pharmacology literature.
    """
    fresh = seed()
    relation_id = with_trial(fresh, tier=EvidenceTier.PRECLINICAL, design="分子对接",
                             condition="", population="")
    docking = evidence_type_for(fresh.relations[relation_id], knowledge=fresh)
    assert docking.design is StudyDesign.IN_SILICO, "the design text chose within the tier"

    hypothesis = claim_type_for("mechanism_hypothesis", subject=Subject.COMPUTATIONAL,
                                intervention="herb.huangqi",
                                certainty=Certainty.TENTATIVE)
    mechanism = claim_type_for("mechanism", subject=Subject.CELL,
                               intervention="herb.huangqi",
                               certainty=Certainty.TENTATIVE)

    from psh.sir import licenses
    assert licenses(docking, hypothesis).grade is Licensing.DIRECT
    refused = licenses(docking, mechanism)
    assert refused.grade is Licensing.UNLICENSED
    assert any("in_silico" in reason for reason in refused.reasons)


def test_a_classical_text_indirectly_supports_a_safety_signal(kb):
    """十八反 is a safety record, and the tier table wants a case report at least.

    The matrix grades it ``EXTRAPOLATED`` rather than ``DIRECT`` — a classical
    incompatibility is a signal worth acting on and is not a reported adverse event —
    which is the distinction a set membership test has to collapse in one direction or the
    other.
    """
    classical = next(r for r in kb.relations.values()
                     if r.tier is EvidenceTier.CLASSICAL_TEXT)
    assert kb.applicability(classical.id, claim_kind="safety_signal").verdict \
        == "unsupported"
    assert licensing_for(kb, classical.id, claim_kind="safety_signal",
                         subject=Subject.TEXT).grade is Licensing.EXTRAPOLATED


def test_the_matrix_is_never_the_more_permissive_of_the_two(kb):
    """The invariant. Over every relation and every claim kind in the shipped seed.

    Two halves, and the second is the one that matters:

    * the matrix never refuses what the tier table licenses — it is an addition, so it
      must not quietly remove answers the rest of the system already depends on;
    * where the tier table refuses, the matrix answers ``EXTRAPOLATED`` at most. It never
      answers ``DIRECT``, so no claim can be published as directly supported on evidence
      ``CLAIM_SUPPORT`` would have refused.

    A future edit to either table that breaks the second half fails here rather than in a
    release.
    """
    disagreements = []
    for relation in kb.relations.values():
        for kind in CLAIM_KIND_FOR:
            ladder = kb.applicability(relation.id, claim_kind=kind).verdict
            matrix = licensing_for(kb, relation.id, claim_kind=kind).grade
            if ladder != "unsupported":
                assert matrix is not Licensing.UNLICENSED, (
                    f"{relation.id} [{kind}]: the tier table licenses it and the matrix "
                    "refuses; the matrix is an addition, not a veto")
                continue
            if matrix is not Licensing.UNLICENSED:
                assert matrix is Licensing.EXTRAPOLATED, (
                    f"{relation.id} [{kind}]: the tier table refuses and the matrix says "
                    f"{matrix.value}")
                disagreements.append((relation.id, kind))
    # The disagreements are real and this test is not vacuous.
    assert {kind for _, kind in disagreements} == {
        "attribution", "mechanism_hypothesis", "safety_signal"}, sorted(
        {kind for _, kind in disagreements})


# =============================================================== the mapping

@pytest.mark.parametrize("tier,expected", [
    (EvidenceTier.CLASSICAL_TEXT, StudyDesign.CLASSICAL_TEXT),
    (EvidenceTier.EXPERT_EXPERIENCE, StudyDesign.EXPERT_CONSENSUS),
    (EvidenceTier.PRECLINICAL, StudyDesign.IN_VITRO),
    (EvidenceTier.CASE_REPORT, StudyDesign.CASE_REPORT),
    (EvidenceTier.OBSERVATIONAL, StudyDesign.CROSS_SECTIONAL),
    (EvidenceTier.RANDOMIZED_TRIAL, StudyDesign.RANDOMISED_TRIAL),
    (EvidenceTier.SYSTEMATIC_REVIEW, StudyDesign.SYSTEMATIC_REVIEW),
])
def test_every_tier_maps_to_a_design(tier, expected):
    mapping = design_for(tier)
    assert mapping.design is expected
    assert not mapping.refined


@pytest.mark.parametrize("text,expected,subject", [
    ("小鼠模型，在体给药", StudyDesign.ANIMAL, Subject.ANIMAL),
    ("mouse model, in vivo", StudyDesign.ANIMAL, Subject.ANIMAL),
    ("HepG2 细胞体外实验", StudyDesign.IN_VITRO, Subject.CELL),
    ("分子对接与网络药理学", StudyDesign.IN_SILICO, Subject.COMPUTATIONAL),
])
def test_a_preclinical_design_text_refines_within_its_tier(text, expected, subject):
    mapping = design_for(EvidenceTier.PRECLINICAL, text)
    assert mapping.design is expected and mapping.subject is subject
    assert mapping.refined


def test_a_design_text_may_not_promote_a_record_out_of_its_tier():
    """A mislabelled record must not license what its tier does not.

    A relation declaring ``PRECLINICAL`` whose design text reads "randomised" is a
    contradiction. Honouring the text would let a data-entry error license an efficacy
    claim, so the tier decides and the disagreement is recorded.
    """
    mapping = design_for(EvidenceTier.PRECLINICAL, "randomised, double-blind, placebo")
    assert mapping.design is StudyDesign.IN_VITRO
    assert not mapping.refined
    assert "the tier decides" in mapping.note


def test_an_in_silico_relation_reaches_a_hypothesis_and_stops_there():
    """The same refusal as above, through the public ``licensing_for`` entry point."""
    fresh = seed()
    relation_id = with_trial(fresh, tier=EvidenceTier.PRECLINICAL,
                             design="分子对接", condition="", population="")
    refused = licensing_for(fresh, relation_id, claim_kind="mechanism",
                            subject=Subject.COMPUTATIONAL, certainty=Certainty.TENTATIVE)
    assert refused.grade is Licensing.UNLICENSED
    assert any("in_silico" in reason for reason in refused.reasons)

    allowed = licensing_for(fresh, relation_id, claim_kind="mechanism_hypothesis",
                            subject=Subject.COMPUTATIONAL, certainty=Certainty.TENTATIVE)
    assert allowed.grade is Licensing.DIRECT


def test_provenance_comes_from_what_the_record_cites(kb):
    fresh = seed()
    relation_id = with_trial(fresh)
    evidence = evidence_type_for(fresh.relations[relation_id], knowledge=fresh)
    assert evidence.provenance is Provenance.RETRIEVED
    assert evidence.identifier == "00000000"
    assert evidence.provenance is not Provenance.RETRIEVED_VERIFIED, (
        "this package can say a record cites a PMID; it cannot say a signer fetched it")


def test_a_retracted_study_makes_the_evidence_unusable():
    fresh = seed()
    study = StudyEvidence(id="study.retracted", tier=EvidenceTier.RANDOMIZED_TRIAL,
                          subject_id="herb.huangqi", design="randomised",
                          pmid="99999999", retracted=True)
    relation = ActionRelation(id="relation.retracted", subject_id="herb.huangqi",
                              predicate="treats", object_id="syndrome.qixu",
                              tier=EvidenceTier.RANDOMIZED_TRIAL,
                              evidence_ids=("study.retracted",))
    fresh.extend(studies=[study], relations=[relation])
    evidence = evidence_type_for(relation, knowledge=fresh)
    assert evidence.retracted is True
    assert licensing_for(fresh, relation.id, claim_kind="efficacy").grade \
        is Licensing.UNLICENSED


def test_an_attribution_claim_is_about_a_text_by_default():
    claim = claim_type_for("attribution")
    assert claim.kind is ClaimKind.ATTRIBUTION and claim.subject is Subject.TEXT
    clinical = claim_type_for("efficacy")
    assert clinical.subject is Subject.HUMAN


def test_an_unknown_claim_kind_is_refused():
    with pytest.raises(ValueError, match="not one of"):
        claim_type_for("vibes")


def test_a_relation_maps_without_the_knowledge_base_too(kb):
    """Without the cited records the tier is all there is, which is weaker and never wrong."""
    relation = next(iter(kb.relations.values()))
    evidence = evidence_type_for(relation)
    assert evidence.design is StudyDesign.CLASSICAL_TEXT
    assert evidence.provenance is Provenance.UNKNOWN
