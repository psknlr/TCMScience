"""The scientist plane: the records, and the relations that make them a world model.

``ScientificLedger`` already writes immutable, content-hashed hypotheses, protocols and
observations through the persistence gateway. What it cannot answer is relational: which
explanations compete, which observations bear on which, and therefore where a hypothesis
stands. ``ScientificWorldModel`` adds exactly that and writes no records of its own — the
structural test below is the one that keeps the two halves from becoming two writers.
"""

from __future__ import annotations

import ast
import dataclasses
from pathlib import Path

import pytest

from psh.config import PSHConfig
from psh.contracts import Autonomy, PolicyDenied, RiskTier
from psh.kernel import TrustedKernel
from psh.labels import Destination, Sensitivity
from psh.policy import PolicySnapshot
from psh.scientist import (
    BeliefState, CycleViolation, DeviationSeverity, Hypothesis, HypothesisStatus,
    Observation, Protocol, ScientificCycle, ScientificLedger, ScientificWorldModel, Stage,
    WorldModelRefused, bayes_update, grade, grade_changes, worst,
)
from psh.workgraph import EdgeKind, NodeKind

LOCAL = (Destination.LOCAL_COMPUTE, Destination.LOCAL_MODEL, Destination.USER_OUTPUT,
         Destination.PERSISTENT)


def hypothesis(proposition="Piezo1 activation promotes HSV-1 entry", **kw):
    params = dict(proposition=proposition, population="mice",
                  predictions=("agonism increases early entry",),
                  falsifiers=("entry unchanged under agonism",),
                  alternatives=("the effect is interferon-mediated",))
    params.update(kw)
    return Hypothesis(**params)


def protocol(**kw):
    params = dict(primary_endpoint="viral entry at 2h", secondary_endpoints=("viral load",),
                  exclusion_criteria="no prior infection", statistical_test="negative binomial",
                  sample_size_assumptions="n=60 for a 30% difference at 80% power",
                  covariates=("batch",), subgroup_plan="none preplanned",
                  stopping_criteria="fixed n, no interim analysis")
    params.update(kw)
    return Protocol(**params)


def observation(outcome="observed", **kw):
    params = dict(summary="entry increased 2.4-fold under agonism",
                  artifact_refs=("artifact:abc123",), outcome=outcome)
    params.update(kw)
    return Observation(**params)


@pytest.fixture
def bench(tmp_path):
    policy = PolicySnapshot(profile_id="scientist_bench", max_data_label=Sensitivity.PHI,
                            allowed_destinations=LOCAL, autonomy=Autonomy.ACT,
                            risk_ceiling=RiskTier.R3_CLINICAL, require_claim_support=False)
    kernel = TrustedKernel(PSHConfig(state_dir=tmp_path / "k").ensure_dirs(), policy=policy)
    project = kernel.graph.project("Piezo1 and HSV-1")
    envelope = kernel.envelope(project_id=project.id)
    ledger = ScientificLedger(kernel, project.id)
    yield ScientificWorldModel(ledger), envelope, ledger, kernel
    kernel.close()


# ================================================== the two halves stay separate

def test_the_world_model_writes_no_records_of_its_own():
    """Structural: every immutable record is written by the ledger, not here.

    ``graph.link`` is permitted — an edge carries no content, so there is nothing for the
    gateway to classify. ``graph.add`` creates a node with a body, and a record written
    around the ledger has no content hash and no provenance edges. One writer, or the two
    halves become two sources of truth about the same project.
    """
    source = (Path(__file__).resolve().parents[1]
              / "src" / "psh" / "scientist" / "worldmodel.py").read_text()
    tree = ast.parse(source)
    offenders = [
        node.lineno for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and node.func.attr in ("add", "update", "commit_raw")
        and isinstance(node.func.value, ast.Attribute)
        and node.func.value.attr == "graph"]
    assert not offenders, (
        f"worldmodel.py reaches WorkGraph.add/update directly at line(s) {offenders}")


def test_a_hypothesis_is_recorded_by_the_ledger_with_its_content_hash(bench):
    world, envelope, ledger, kernel = bench
    node = world.declare(hypothesis(), envelope)
    assert node.kind is NodeKind.HYPOTHESIS
    assert node.ref.startswith("sha256:")
    # The ledger's read verifies the hash, so this is not merely "a node exists".
    body = ledger.read(node.id, envelope)
    assert body["record"]["proposition"].startswith("Piezo1")


# ======================================================== relations and standing

def test_a_hypothesis_starts_proposed_and_moves_as_observations_arrive(bench):
    world, envelope, ledger, kernel = bench
    h = world.declare(hypothesis(), envelope)
    assert world.belief(h.id, envelope).status is HypothesisStatus.PROPOSED

    p = ledger.preregister(h.id, protocol(), envelope)
    o = ledger.observe(p.id, observation(), protocol(), envelope)
    world.corroborate(o.id, h.id, envelope, note="matched the prediction")

    state = world.belief(h.id, envelope)
    assert state.status is HypothesisStatus.CORROBORATED
    assert state.corroborations == 1 and state.refutations == 0
    assert state.predictions == ("agonism increases early entry",)
    assert state.falsifiers == ("entry unchanged under agonism",)


def test_an_experiment_puts_a_hypothesis_under_test(bench):
    world, envelope, ledger, kernel = bench
    h1 = world.declare(hypothesis(), envelope)
    h2 = world.declare(hypothesis("the effect is interferon-mediated"), envelope,
                       alternative_to=[h1.id])
    experiment = world.design("Piezo1 agonist entry assay", envelope,
                              tests=[h1.id, h2.id], discriminates=[h1.id, h2.id])
    assert experiment.kind is NodeKind.EXPERIMENT
    assert world.belief(h1.id, envelope).status is HypothesisStatus.UNDER_TEST
    assert world.belief(h1.id, envelope).experiments == (experiment.id,)


def test_an_experiment_that_discriminates_nothing_is_refused(bench):
    world, envelope, ledger, kernel = bench
    h = world.declare(hypothesis(), envelope)
    with pytest.raises(WorldModelRefused, match="cannot decide between them"):
        world.design("a confirmatory assay", envelope, tests=[h.id], discriminates=[h.id])


def test_a_refuted_hypothesis_stays_in_the_graph(bench):
    """The project has to remember what it ruled out, or it proposes it again."""
    world, envelope, ledger, kernel = bench
    h = world.declare(hypothesis("the effect is interferon-mediated"), envelope)
    p = ledger.preregister(h.id, protocol(), envelope)
    negative = ledger.observe(p.id, observation("negative", summary="IFN response unchanged"),
                              protocol(), envelope)
    world.refute(negative.id, h.id, envelope, note="matched the falsifier")

    state = world.belief(h.id, envelope)
    assert state.status is HypothesisStatus.REFUTED
    assert state.refutations == 1
    assert state.negative_observations == 1
    assert kernel.graph.get(h.id) is not None, "a refuted hypothesis is not deleted"


def test_corroboration_and_refutation_together_are_contested_not_settled(bench):
    world, envelope, ledger, kernel = bench
    h = world.declare(hypothesis(), envelope)
    p = ledger.preregister(h.id, protocol(), envelope)
    yes = ledger.observe(p.id, observation(), protocol(), envelope)
    no = ledger.observe(p.id, observation("negative", summary="no effect on entry"),
                        protocol(), envelope)
    world.corroborate(yes.id, h.id, envelope)
    world.refute(no.id, h.id, envelope)
    assert world.belief(h.id, envelope).status is HypothesisStatus.CONTESTED


def test_alternatives_are_edges_in_both_directions(bench):
    """The record names competitors as text; only an edge answers the question from both ends."""
    world, envelope, ledger, kernel = bench
    h1 = world.declare(hypothesis(), envelope)
    h2 = world.declare(hypothesis("the effect is interferon-mediated"), envelope,
                       alternative_to=[h1.id])
    assert [n.id for n in world.competing(h1.id)] == [h2.id]
    assert [n.id for n in world.competing(h2.id)] == [h1.id]


def test_an_alternative_that_is_not_a_recorded_hypothesis_is_refused(bench):
    world, envelope, ledger, kernel = bench
    with pytest.raises(WorldModelRefused, match="not a recorded hypothesis"):
        world.declare(hypothesis(), envelope, alternative_to=["hyp_nonexistent"])


def test_a_cross_project_or_wrong_kind_reference_is_refused(bench):
    world, envelope, ledger, kernel = bench
    h = world.declare(hypothesis(), envelope)
    with pytest.raises(WorldModelRefused, match="wrong-kind or cross-project"):
        world.corroborate(h.id, h.id, envelope)      # a hypothesis is not an observation


def test_why_believe_walks_back_to_the_observation_and_its_protocol(bench):
    world, envelope, ledger, kernel = bench
    h = world.declare(hypothesis(), envelope)
    p = ledger.preregister(h.id, protocol(), envelope)
    o = ledger.observe(p.id, observation(), protocol(), envelope)
    world.corroborate(o.id, h.id, envelope)

    trace = "\n".join(world.why_believe(h.id))
    assert "corroborates" in trace
    assert "observation" in trace


def test_the_briefing_leads_with_what_a_fresh_reader_would_re_propose(bench):
    world, envelope, ledger, kernel = bench
    settled = world.declare(hypothesis("well supported"), envelope)
    ruled_out = world.declare(hypothesis("already ruled out"), envelope)
    p1 = ledger.preregister(settled.id, protocol(), envelope)
    p2 = ledger.preregister(ruled_out.id, protocol(), envelope)
    world.corroborate(ledger.observe(p1.id, observation(), protocol(), envelope).id,
                      settled.id, envelope)
    world.refute(ledger.observe(p2.id, observation("negative"), protocol(), envelope).id,
                 ruled_out.id, envelope)

    briefing = world.briefing(envelope)
    assert briefing.index("already ruled out") < briefing.index("well supported")


def test_belief_is_refused_for_something_that_is_not_a_hypothesis(bench):
    world, envelope, ledger, kernel = bench
    h = world.declare(hypothesis(), envelope)
    p = ledger.preregister(h.id, protocol(), envelope)
    with pytest.raises(WorldModelRefused, match="not a hypothesis"):
        world.belief(p.id, envelope)


def test_bayes_update_is_offered_and_never_applied_automatically(bench):
    world, envelope, ledger, kernel = bench
    h = world.declare(hypothesis(), envelope)
    state = world.belief(h.id, envelope)
    assert isinstance(state, BeliefState)
    assert not hasattr(state, "posterior")
    assert bayes_update(0.2, 4.0) == pytest.approx(0.5)
    for bad in ((0.0, 2.0), (1.0, 2.0), (0.5, 0.0)):
        with pytest.raises(ValueError):
            bayes_update(*bad)


# ================================================================ the cycle

def test_the_cycle_refuses_preregistration_after_the_results_are_in():
    cycle = ScientificCycle()
    for stage in (Stage.HYPOTHESIS, Stage.PREDICTION, Stage.DESIGN, Stage.EXECUTED,
                  Stage.OBSERVED):
        cycle.advance(stage)
    with pytest.raises(CycleViolation, match="not preregistration"):
        cycle.advance(Stage.PREREGISTERED)


def test_a_cycle_records_whether_it_was_preregistered_or_exploratory():
    frozen = ScientificCycle()
    for stage in (Stage.HYPOTHESIS, Stage.PREDICTION, Stage.DESIGN, Stage.PREREGISTERED,
                  Stage.EXECUTED):
        frozen.advance(stage)
    assert frozen.preregistered and not frozen.exploratory

    loose = ScientificCycle()
    for stage in (Stage.HYPOTHESIS, Stage.PREDICTION, Stage.DESIGN, Stage.EXECUTED):
        loose.advance(stage)
    assert loose.exploratory and not loose.preregistered


def test_the_cycle_closes_into_the_next_one():
    cycle = ScientificCycle()
    for stage in (Stage.HYPOTHESIS, Stage.PREDICTION, Stage.DESIGN, Stage.EXECUTED,
                  Stage.OBSERVED, Stage.ANALYSED, Stage.INTERPRETED,
                  Stage.BELIEF_UPDATED):
        cycle.advance(stage)
    assert cycle.may(Stage.HYPOTHESIS), "a belief update starts the next turn"
    cycle.advance(Stage.HYPOTHESIS)
    assert cycle.path()[0] == "question" and not cycle.terminal


def test_a_cycle_round_trips_through_a_dict():
    cycle = ScientificCycle()
    cycle.advance(Stage.HYPOTHESIS, note="from the literature")
    restored = ScientificCycle.from_dict(cycle.as_dict())
    assert restored.stage is cycle.stage and restored.path() == cycle.path()
    assert restored.history[0].note == "from the literature"


# ========================================================== deviation grading

def test_changing_the_primary_endpoint_is_critical_and_adding_a_covariate_is_not():
    planned = protocol()
    actual = dataclasses.replace(planned, primary_endpoint="viral load at 24h",
                                 statistical_test="Poisson",
                                 covariates=("batch", "operator"))
    graded = {d.field: d for d in grade(planned, actual)}
    assert graded["primary_endpoint"].severity is DeviationSeverity.CRITICAL
    assert graded["statistical_test"].severity is DeviationSeverity.MAJOR
    assert graded["covariates"].severity is DeviationSeverity.MINOR
    assert graded["covariates"].added == ("operator",)
    assert worst(graded.values()) is DeviationSeverity.CRITICAL


def test_an_added_secondary_endpoint_outranks_a_dropped_one():
    planned = protocol(secondary_endpoints=("viral load", "weight"))
    added = dataclasses.replace(planned, secondary_endpoints=("viral load", "weight", "IFN"))
    dropped = dataclasses.replace(planned, secondary_endpoints=("viral load",))
    assert grade(planned, added)[0].severity is DeviationSeverity.MAJOR
    assert grade(planned, dropped)[0].severity is DeviationSeverity.MINOR


def test_an_identical_protocol_produces_no_deviations():
    assert grade(protocol(), protocol()) == ()
    assert worst(()) is None


def test_a_graded_deviation_quotes_no_protocol_content():
    """A deviation travels into summaries; exclusion criteria can describe a cohort."""
    planned = protocol()
    actual = dataclasses.replace(planned, exclusion_criteria="excluded MRN 4417789")
    deviation = grade(planned, actual)[0]
    assert "4417789" not in str(deviation)
    assert "4417789" not in repr(deviation.as_dict())
    assert deviation.field == "exclusion_criteria"


def test_the_ledgers_own_changed_fields_can_be_graded_without_recomputing_them(bench):
    """The ledger already diffed the protocols; grading reads its result."""
    world, envelope, ledger, kernel = bench
    h = world.declare(hypothesis(), envelope)
    planned = protocol()
    p = ledger.preregister(h.id, planned, envelope)
    actual = dataclasses.replace(planned, primary_endpoint="viral load at 24h")
    node = ledger.observe(p.id, observation(), actual, envelope,
                          deviation_reason="the 2h assay failed")
    body = ledger.read(node.id, envelope)
    graded = grade_changes(body["deviation"]["changed_fields"])
    assert [d.field for d in graded] == ["primary_endpoint"]
    assert worst(graded) is DeviationSeverity.CRITICAL


def test_grading_refuses_anything_that_is_not_a_protocol():
    with pytest.raises(ValueError, match="two Protocols"):
        grade(protocol(), {"primary_endpoint": "x"})
