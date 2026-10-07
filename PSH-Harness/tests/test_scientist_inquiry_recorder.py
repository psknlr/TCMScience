"""An inquiry recorded in the world model as it runs, through the kernel's own doors.

The inquiry's trail recomputes its belief. These tests pin what the recorder adds on top:
records a later reader can query (``belief``, ``briefing``, ``why_believe``), written
through the persistence gateway under the run's authority, before the inquiry's state
moves.
"""

from __future__ import annotations

import pytest

from psh.config import PSHConfig
from psh.contracts import Autonomy, PolicyDenied, RiskTier
from psh.kernel import TrustedKernel
from psh.labels import Destination, Sensitivity
from psh.policy import PolicySnapshot
from psh.scientist import (
    Analysis, Executed, Explanation, HypothesisStatus, Inquiry, Role, ScientificLedger,
    ScientificWorldModel, Stage, Verdict, WorldModelRecorder, run_inquiry,
)
from psh.sir.values import ClaimKind, StudyDesign
from psh.workgraph import EdgeKind, NodeKind

LOCAL = (Destination.LOCAL_COMPUTE, Destination.LOCAL_MODEL, Destination.USER_OUTPUT,
         Destination.PERSISTENT)
BINARY = ("enriched", "not_enriched")
SPECIFICITY = ("specific", "not_specific")


@pytest.fixture
def bench(tmp_path):
    policy = PolicySnapshot(profile_id="inquiry_bench", max_data_label=Sensitivity.PHI,
                            allowed_destinations=LOCAL, autonomy=Autonomy.ACT,
                            risk_ceiling=RiskTier.R3_CLINICAL, require_claim_support=False)
    kernel = TrustedKernel(PSHConfig(state_dir=tmp_path / "k").ensure_dirs(), policy=policy)
    project = kernel.graph.project("葛根芩连汤 pathway A")
    envelope = kernel.envelope(project_id=project.id)
    world = ScientificWorldModel(ScientificLedger(kernel, project.id))
    yield world, envelope, kernel
    kernel.close()


def row(p, outcomes=BINARY):
    return {outcomes[0]: p, outcomes[1]: 1.0 - p}


def inquiry_for(world, envelope):
    recorder = WorldModelRecorder(world, envelope,
                                  population="in silico: measured activities of the "
                                             "formula's constituents")
    inquiry = Inquiry(
        "Is the concentration of measured targets in pathway A real?",
        [Explanation("target", "the constituents act selectively on pathway A",
                     ClaimKind.MECHANISM_HYPOTHESIS, prior=0.4),
         Explanation("coverage", "the enrichment comes from which proteins were assayed",
                     role=Role.ARTEFACT, prior=0.3),
         Explanation("nonspecific", "any herb combination of this size reaches it",
                     role=Role.ARTEFACT, prior=0.2)],
        [Analysis("assayed", "enrichment against the assayed proteins",
                  StudyDesign.IN_SILICO, BINARY),
         Analysis("assayed-seed", "the same, another permutation seed",
                  StudyDesign.IN_SILICO, BINARY, replicates="assayed"),
         Analysis("random", "random herb combinations of the same size",
                  StudyDesign.IN_SILICO, SPECIFICITY),
         Analysis("random-2", "random herb combinations, a second draw",
                  StudyDesign.IN_SILICO, SPECIFICITY, replicates="random")],
        recorder=recorder)
    for a in ("assayed", "assayed-seed"):
        # coverage declares a significant result impossible: it is refuted by one.
        inquiry.declare(a, {"target": row(0.85), "coverage": row(0.0),
                            "nonspecific": row(0.85)})
    for a in ("random", "random-2"):
        inquiry.declare(a, {"target": row(0.85, SPECIFICITY),
                            "coverage": row(0.5, SPECIFICITY),
                            "nonspecific": row(0.05, SPECIFICITY)})
    return inquiry, recorder


def truth(analysis, step):
    return Executed({"assayed": "enriched", "assayed-seed": "enriched",
                     "random": "specific", "random-2": "specific"}[analysis.id],
                    evidence_ref=f"artifact:{analysis.id}")


def test_each_explanation_is_a_hypothesis_record_with_its_competitors_as_edges(bench):
    world, envelope, kernel = bench
    inquiry, recorder = inquiry_for(world, envelope)
    assert set(recorder.hypotheses) == {"target", "coverage", "nonspecific"}
    target = recorder.hypotheses["target"]
    competitors = {n.id for n in world.competing(target)}
    assert competitors == {recorder.hypotheses["coverage"], recorder.hypotheses["nonspecific"]}
    record = world.ledger.read(target, envelope)
    assert record["record"]["population"].startswith("in silico")
    assert "none of the named explanations" in record["record"]["alternatives"]


def test_a_protocol_is_preregistered_against_every_explanation_it_predicts_for(bench):
    world, envelope, kernel = bench
    inquiry, recorder = inquiry_for(world, envelope)
    protocol_id, protocol = recorder.protocols["assayed"]
    sources = {n.id for _, n in world.graph.neighbours(protocol_id,
                                                       kind=EdgeKind.DERIVED_FROM,
                                                       direction="out")}
    assert sources == set(recorder.hypotheses.values())
    assert "sha256:" in protocol.statistical_test
    # The predictions differ, so the analysis is also an experiment that tests them all.
    experiment = recorder.experiments["assayed"]
    tested = {n.id for _, n in world.graph.neighbours(experiment, kind=EdgeKind.TESTS,
                                                      direction="out")}
    assert tested == set(recorder.hypotheses.values())


def test_observations_draw_the_edges_the_belief_states_are_computed_from(bench):
    world, envelope, kernel = bench
    inquiry, recorder = inquiry_for(world, envelope)
    conclusion = run_inquiry(inquiry, truth)
    assert conclusion.verdict is Verdict.ACCEPTED
    recorder.close(inquiry, conclusion)

    assert world.belief(recorder.hypotheses["target"], envelope).status \
        is HypothesisStatus.CORROBORATED
    coverage = world.belief(recorder.hypotheses["coverage"], envelope)
    assert coverage.status is HypothesisStatus.REFUTED
    nonspecific = world.belief(recorder.hypotheses["nonspecific"], envelope)
    # Corroborated by the significant results it also predicted, then out-predicted on
    # specificity: lowered, not refuted, so it draws no refutation edge.
    assert nonspecific.refutations == 0
    # The refuted explanation leads a later reader's briefing.
    assert world.briefing(envelope).splitlines()[1].startswith("  - [refuted]")
    # Every observation is a record under its protocol, linked back by provenance.
    for analysis_id, node_id in recorder.observations.items():
        node = world.graph.get(node_id)
        assert node.kind is NodeKind.OBSERVATION
        protocol_id, _ = recorder.protocols[analysis_id]
        parents = {n.id for _, n in world.graph.neighbours(
            node_id, kind=EdgeKind.DERIVED_FROM, direction="out")}
        assert parents == {protocol_id}
        walk = "\n".join(world.why_believe(node_id))
        assert "protocol:" in walk and "hypothesis:" in walk


def test_every_turn_of_the_cycle_passed_through_preregistration(bench):
    world, envelope, kernel = bench
    inquiry, recorder = inquiry_for(world, envelope)
    run_inquiry(inquiry, truth)
    assert recorder.cycles
    for cycle in recorder.cycles.values():
        assert cycle.preregistered and not cycle.exploratory
        assert cycle.stage is Stage.CLOSED


def test_the_audit_chain_carries_the_inquiry_and_still_verifies(bench):
    world, envelope, kernel = bench
    inquiry, recorder = inquiry_for(world, envelope)
    conclusion = run_inquiry(inquiry, truth)
    recorder.close(inquiry, conclusion)
    events = [r for r in kernel.events.records() if r.event_type.startswith("inquiry_")]
    kinds = [r.event_type for r in events]
    assert kinds[0] == "inquiry_opened" and kinds[-1] == "inquiry_concluded"
    assert kinds.count("inquiry_declared") == 4
    assert kinds.count("inquiry_observed") == len(inquiry.updates)
    digests = {r.detail.get("digest") for r in events}
    assert {entry["digest"] for entry in inquiry.trail} <= digests
    assert events[-1].detail["verdict"] == "accepted"
    assert kernel.events.verify().intact


def test_a_refusing_gateway_leaves_belief_where_it_was(bench):
    world, envelope, kernel = bench
    inquiry, recorder = inquiry_for(world, envelope)
    before = (inquiry.belief, inquiry.head)
    recorder.envelope = envelope.restrict(
        allowed_destinations=frozenset({Destination.LOCAL_COMPUTE}))
    with pytest.raises(PolicyDenied, match="PERSISTENT"):
        inquiry.observe("assayed", "enriched")
    assert (inquiry.belief, inquiry.head) == before
    assert not inquiry.observed("assayed")


def test_an_inquiry_cannot_open_without_authority_to_record_it(bench):
    world, envelope, kernel = bench
    local = envelope.restrict(allowed_destinations=frozenset({Destination.LOCAL_COMPUTE}))
    recorder = WorldModelRecorder(world, local, population="in silico")
    with pytest.raises(PolicyDenied):
        Inquiry("q", [Explanation("a", "one", ClaimKind.MECHANISM_HYPOTHESIS, prior=0.4),
                      Explanation("b", "two", role=Role.ARTEFACT, prior=0.4)],
                recorder=recorder)
    assert world.hypotheses() == []


def test_a_late_explanation_is_recorded_as_a_competitor(bench):
    world, envelope, kernel = bench
    inquiry, recorder = inquiry_for(world, envelope)
    inquiry.admit([Explanation("interference", "constituents interfere with the assay",
                               role=Role.ARTEFACT)], {"interference": 0.5})
    late = recorder.hypotheses["interference"]
    assert {n.id for n in world.competing(late)} == {
        recorder.hypotheses[h] for h in ("target", "coverage", "nonspecific")}


def test_a_preregistration_against_several_hypotheses_keeps_the_single_form(bench):
    """The ledger's new ``also`` is additive: one hypothesis still means one source."""
    world, envelope, kernel = bench
    inquiry, recorder = inquiry_for(world, envelope)
    _, protocol = recorder.protocols["assayed"]
    node = world.ledger.preregister(recorder.hypotheses["target"], protocol, envelope)
    sources = world.graph.neighbours(node.id, kind=EdgeKind.DERIVED_FROM, direction="out")
    assert [n.id for _, n in sources] == [recorder.hypotheses["target"]]
    with pytest.raises(ValueError):
        world.ledger.preregister(recorder.hypotheses["target"], protocol, envelope,
                                 also=("not-a-node",))
