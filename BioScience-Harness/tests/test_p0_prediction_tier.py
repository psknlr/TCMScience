"""A computational prediction keeps its tier through the P0 skills.

The design labels in ``retrieve`` and ``assess_safety`` covered every tier but the one
ranked lowest, and fell back to a default for it: a network-pharmacology prediction in the
corpus would have become ``expert_consensus`` evidence in one skill and ``classical_text``
in the other, designs that license attribution and traditional-use claims no prediction
can. ``_quality_for`` compared tiers by order and rated the prediction DIRECT. The corpus
holds no prediction today, which is why nothing failed; these tests hold the line for the
day it does.
"""

from __future__ import annotations

from bioagent.contracts import CandidateClaim, check_claim
from bioagent.contracts.quality import Directness
from bioagent.skills.p0.assess_safety import _item_for_record
from bioagent.skills.p0.common import _quality_for
from bioagent.skills.p0.retrieve import _DESIGN_FOR_TIER, _item_from_study
from bioagent.tcm.model import EvidenceTier, SafetyRecord, StudyEvidence

PREDICTION = StudyEvidence(
    id="np.gqd.ptp1b", tier=EvidenceTier.COMPUTATIONAL_PREDICTION, subject_id="葛根芩连汤",
    outcome="PTP1B", citation="network pharmacology: SwissTargetPrediction, 2026-10")


def test_every_tier_has_a_design_and_none_is_a_default():
    assert set(_DESIGN_FOR_TIER) == set(EvidenceTier)
    assert _DESIGN_FOR_TIER[EvidenceTier.COMPUTATIONAL_PREDICTION] == "in_silico"


def test_a_retrieved_prediction_is_in_silico_and_licenses_only_a_hypothesis():
    item = _item_from_study(PREDICTION, run_id="t", index=0)
    assert item.design == "in_silico"
    assert item.quality.directness is Directness.NOT_ASSESSED

    def claim(kind):
        return CandidateClaim(id="c", text="葛根芩连汤 may act on PTP1B.", claim_kind=kind,
                              subject="葛根芩连汤", supports=(item.id,), confidence=0.3,
                              confidence_basis="one prediction",
                              falsified_by="an enzyme assay with no inhibition")
    assert "CLM005" not in check_claim(claim("mechanism_hypothesis"), {item.id: item}).codes
    for kind in ("traditional_use", "attribution", "mechanism"):
        assert "CLM005" in check_claim(claim(kind), {item.id: item}).codes, kind


def test_a_predicted_safety_record_is_in_silico_not_classical_text():
    record = SafetyRecord(id="pred.tox", subject_id="何首乌", kind="toxicity",
                          description="predicted hepatotoxicity alert",
                          tier=EvidenceTier.COMPUTATIONAL_PREDICTION)
    assert _item_for_record(record, run_id="t", index=0).design == "in_silico"


def test_the_texts_stay_direct_and_studies_partial():
    assert _quality_for(EvidenceTier.CLASSICAL_TEXT, assessed_by="t").directness \
        is Directness.DIRECT
    assert _quality_for(EvidenceTier.EXPERT_EXPERIENCE, assessed_by="t").directness \
        is Directness.DIRECT
    assert _quality_for(EvidenceTier.RANDOMIZED_TRIAL, assessed_by="t").directness \
        is Directness.PARTIAL
