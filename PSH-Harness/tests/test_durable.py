"""The workflow journal and the amendment it makes possible.

The case: a workflow has run its retrieval, its preprocessing and its differential
expression, and the scientist changes the fold-change threshold. Re-running everything pays
for the retrieval again; re-running nothing is worse. What survives is decided by content,
so the tests here are mostly about identity — and the one that matters most is
``test_identity_is_content_and_not_position``, because the obvious implementation is an
ordinal and an ordinal silently re-points every later result when a step is inserted.
"""

from __future__ import annotations

import pytest

from psh.durable import (
    Decision, NodeOutcome, RecordKind, WorkflowJournal, diff, result_digest, seed_graph,
)
from psh.labels import DataLabel, Sensitivity
from psh.runtime import ExecutionGraph, TaskState
from psh.sir import (
    Acceptance, AnalysisSpec, Effect, ExecKind, Role, SIRNode, SIRProgram, SideEffectClass,
)


def node(node_id, *, depends_on=(), payload=None, side_effect=SideEffectClass.IDEMPOTENT,
         cache=True, effects=(Effect.READ_LOCAL,), **kw):
    return SIRNode(node_id=node_id, kind=ExecKind.TOOL, objective=f"run {node_id}",
                   component_id="tool", effects=frozenset(effects),
                   depends_on=tuple(depends_on), payload=dict(payload or {}),
                   side_effect=side_effect, cache=cache, **kw)


def pipeline(threshold=1.5, program_id="prog"):
    return SIRProgram(
        question="which pathways are enriched?", program_id=program_id,
        acceptance=(Acceptance(description="done", kind="task"),),
        nodes=(node("fetch"),
               node("deg", depends_on=("fetch",), payload={"fold_change": threshold}),
               node("pathway", depends_on=("deg",))))


def run_through(journal, program, run_id="run_1", results=None, nodes=None):
    """Journal a complete successful run of the given nodes."""
    results = results or {}
    for node_id in (nodes if nodes is not None else [n.node_id for n in program.nodes]):
        journal.append(RecordKind.NODE_STARTED, run_id, node_id=node_id,
                       signature=program.signature(node_id))
        journal.record_success(run_id, node_id, signature=program.signature(node_id),
                               result=results.get(node_id, {"node": node_id}),
                               label=DataLabel(Sensitivity.PUBLIC))
    return journal.replay(run_id)


# ==================================================================== journal

def test_a_replay_folds_the_log_and_is_the_only_source_of_state():
    journal = WorkflowJournal()
    program = pipeline()
    replay = run_through(journal, program)
    assert replay.succeeded() == ("deg", "fetch", "pathway")
    assert replay.has_result("fetch") and replay.result("fetch") == {"node": "fetch"}
    assert replay.signature("deg") == program.signature("deg")


def test_a_node_that_started_and_never_reported_is_in_doubt_not_failed():
    """"It failed" and "nobody knows" call for opposite responses."""
    journal = WorkflowJournal()
    program = pipeline()
    journal.append(RecordKind.NODE_STARTED, "r", node_id="fetch",
                   signature=program.signature("fetch"))
    journal.append(RecordKind.NODE_FAILED, "r", node_id="deg",
                   signature=program.signature("deg"))
    replay = journal.replay("r")
    assert replay.in_doubt() == ("fetch",)
    assert replay.outcome("fetch") is NodeOutcome.UNKNOWN
    assert replay.outcome("deg") is NodeOutcome.FAILED


def test_a_result_the_persistence_rules_refuse_is_recorded_as_withheld():
    """The journal applies the checkpoint's rule rather than carrying a second copy of it."""
    journal = WorkflowJournal()
    record = journal.record_success(
        "r", "chart", signature="sig", result={"mrn": "4417789"},
        label=DataLabel(Sensitivity.PHI), ceiling=Sensitivity.RESEARCH_DEIDENTIFIED)
    assert "exceeds the persistence ceiling" in record.withheld
    replay = journal.replay("r")
    assert replay.outcome("chart") is NodeOutcome.SUCCEEDED
    assert not replay.has_result("chart"), "the value was not stored, so it cannot be reused"
    assert replay.withheld_reason("chart")


def test_a_run_that_may_not_persist_stores_no_results_at_all():
    journal = WorkflowJournal()
    journal.record_success("r", "n", signature="s", result={"a": 1},
                           label=DataLabel(Sensitivity.PUBLIC), allow_results=False)
    assert not journal.replay("r").has_result("n")


def test_the_journal_stores_the_label_so_a_reused_result_is_not_laundered():
    journal = WorkflowJournal()
    journal.append(RecordKind.NODE_STARTED, "r", node_id="n", signature="s")
    journal.record_success("r", "n", signature="s", result={"count": 3},
                           label=DataLabel(Sensitivity.SENSITIVE))
    assert journal.replay("r").label("n").sensitivity is Sensitivity.SENSITIVE


def test_a_closed_journal_refuses_to_append():
    journal = WorkflowJournal()
    journal.close()
    with pytest.raises(RuntimeError, match="closed"):
        journal.append(RecordKind.RUN_STARTED, "r")


def test_a_durable_journal_survives_a_reopen(tmp_path):
    path = tmp_path / "journal.db"
    program = pipeline()
    with WorkflowJournal(path) as journal:
        assert journal.durable
        run_through(journal, program)
    with WorkflowJournal(path) as reopened:
        replay = reopened.replay("run_1")
        assert replay.has_result("fetch")
        assert reopened.runs() == ["run_1"]


def test_a_value_that_will_not_serialise_is_recorded_as_withheld_not_as_its_repr():
    journal = WorkflowJournal()
    journal.append(RecordKind.NODE_STARTED, "r", node_id="n", signature="s")
    record = journal.append(RecordKind.NODE_SUCCEEDED, "r", node_id="n", signature="s",
                            result=object(), store_result=True,
                            label=DataLabel(Sensitivity.PUBLIC))
    assert "not JSON-serialisable" in record.withheld
    assert not journal.replay("r").has_result("n")


# ====================================================================== delta

def test_changing_one_step_reuses_what_is_upstream_and_redoes_what_is_after():
    journal = WorkflowJournal()
    first = pipeline(1.5)
    replay = run_through(journal, first)
    amended = pipeline(2.0)

    delta = diff(amended, replay, previous=first)
    assert delta.reused == ("fetch",)
    assert set(delta.recomputed) == {"deg", "pathway"}
    assert delta.safe
    assert "something it depends on changed" in delta.delta_for("pathway").reason
    assert "its definition changed" in delta.delta_for("deg").reason


def test_identity_is_content_and_not_position():
    """Inserting a step at the top must not re-point every later result.

    The defect an ordinal-keyed cache has. ``fetch`` is unchanged and unaffected by a new
    node beside it; ``deg`` is unchanged in itself and is not reused, because what it
    depends on is different — which is the correct answer for the correct reason.
    """
    journal = WorkflowJournal()
    original = pipeline()
    replay = run_through(journal, original)

    inserted = SIRProgram(
        question=original.question, program_id=original.program_id,
        acceptance=original.acceptance,
        nodes=(node("preflight"),
               node("fetch"),
               node("deg", depends_on=("fetch", "preflight"),
                    payload={"fold_change": 1.5}),
               node("pathway", depends_on=("deg",))))

    delta = diff(inserted, replay, previous=original)
    assert delta.delta_for("preflight").decision is Decision.NEW
    assert delta.delta_for("fetch").decision is Decision.REUSE
    assert delta.delta_for("deg").decision is Decision.RECOMPUTE


def test_a_removed_node_is_reported_when_the_previous_program_is_supplied():
    journal = WorkflowJournal()
    first = pipeline()
    replay = run_through(journal, first)
    shortened = SIRProgram(question=first.question, program_id=first.program_id,
                           acceptance=first.acceptance, nodes=first.nodes[:2])
    delta = diff(shortened, replay, previous=first)
    assert delta.removed == ("pathway",)


def test_declarations_do_not_appear_in_the_execution_delta():
    journal = WorkflowJournal()
    from psh.sir import HypothesisSpec

    program = SIRProgram(
        question="q", program_id="p", acceptance=(Acceptance(description="d", kind="task"),),
        nodes=(SIRNode(node_id="h", role=Role.HYPOTHESIS,
                       hypothesis=HypothesisSpec(proposition="P")),
               node("fetch", depends_on=("h",))))
    replay = run_through(journal, program, nodes=["fetch"])
    delta = diff(program, replay, previous=program)
    assert [d.node_id for d in delta.nodes] == ["fetch"]


def test_a_node_in_doubt_that_cannot_be_replayed_blocks_the_amendment():
    journal = WorkflowJournal()
    program = SIRProgram(
        question="q", program_id="p", acceptance=(Acceptance(description="d", kind="task"),),
        nodes=(node("order", side_effect=SideEffectClass.NON_REPEATABLE,
                    effects=(Effect.WETLAB_ACTION,)),))
    journal.append(RecordKind.NODE_STARTED, "r", node_id="order",
                   signature=program.signature("order"))
    delta = diff(program, journal.replay("r"))
    assert delta.unsafe and delta.unsafe[0].decision is Decision.UNSAFE
    assert not delta.safe
    assert "non_repeatable" in delta.unsafe[0].reason


def test_a_node_that_plainly_failed_is_recomputed_even_when_non_repeatable():
    """A failed call did not do its work; only an unreported one might have."""
    journal = WorkflowJournal()
    program = SIRProgram(
        question="q", program_id="p", acceptance=(Acceptance(description="d", kind="task"),),
        nodes=(node("order", side_effect=SideEffectClass.NON_REPEATABLE,
                    effects=(Effect.WETLAB_ACTION,)),))
    journal.append(RecordKind.NODE_STARTED, "r", node_id="order",
                   signature=program.signature("order"))
    journal.append(RecordKind.NODE_FAILED, "r", node_id="order",
                   signature=program.signature("order"))
    delta = diff(program, journal.replay("r"))
    assert delta.delta_for("order").decision is Decision.RECOMPUTE
    assert delta.safe


def test_cache_false_is_recomputed_even_when_nothing_changed():
    journal = WorkflowJournal()
    program = SIRProgram(
        question="q", program_id="p", acceptance=(Acceptance(description="d", kind="task"),),
        nodes=(node("today", cache=False),))
    replay = run_through(journal, program)
    delta = diff(program, replay, previous=program)
    assert delta.delta_for("today").decision is Decision.RECOMPUTE
    assert "cache=false" in delta.delta_for("today").reason


def test_a_succeeded_node_whose_result_was_withheld_is_recomputed():
    journal = WorkflowJournal()
    program = pipeline()
    journal.append(RecordKind.NODE_STARTED, "r", node_id="fetch",
                   signature=program.signature("fetch"))
    journal.record_success("r", "fetch", signature=program.signature("fetch"),
                           result={"phi": True}, label=DataLabel(Sensitivity.PHI),
                           ceiling=Sensitivity.INTERNAL)
    delta = diff(program, journal.replay("r"))
    entry = delta.delta_for("fetch")
    assert entry.decision is Decision.RECOMPUTE
    assert "was not stored" in entry.reason


# =================================================================== seeding

def test_seeding_marks_reused_nodes_succeeded_with_their_stored_labels():
    """Where incremental recompute meets the executor."""
    from psh.compiler import compile_program
    from psh.contracts import Autonomy, RiskTier
    from psh.labels import Destination
    from psh.policy import PolicySnapshot

    policy = PolicySnapshot(profile_id="seed", max_data_label=Sensitivity.PHI,
                            allowed_destinations=(Destination.LOCAL_COMPUTE,
                                                  Destination.LOCAL_MODEL,
                                                  Destination.USER_OUTPUT,
                                                  Destination.PERSISTENT),
                            autonomy=Autonomy.ACT, risk_ceiling=RiskTier.R3_CLINICAL,
                            require_claim_support=False)
    journal = WorkflowJournal()
    first = pipeline()
    journal.append(RecordKind.NODE_STARTED, "r", node_id="fetch",
                   signature=first.signature("fetch"))
    journal.record_success("r", "fetch", signature=first.signature("fetch"),
                           result={"rows": 7}, label=DataLabel(Sensitivity.SENSITIVE))
    replay = journal.replay("r")

    amended = pipeline(2.0)
    compiled = compile_program(amended, policy.envelope(), policy=policy)
    assert compiled.ok, compiled.diagnostics.render()
    graph = ExecutionGraph(compiled.plan)
    delta = diff(amended, replay, previous=first)

    seeded = seed_graph(graph, delta, replay)
    assert seeded == ("fetch",)
    assert graph.nodes["fetch"].state is TaskState.SUCCEEDED
    assert graph.nodes["fetch"].result == {"rows": 7}
    assert graph.nodes["fetch"].label.sensitivity is Sensitivity.SENSITIVE
    assert [n.id for n in graph.ready()] == ["deg"], (
        "only what follows the reused node is schedulable")


def test_a_result_digest_distinguishes_two_results_without_holding_either():
    assert result_digest({"a": 1}) != result_digest({"a": 2})
    assert result_digest({"a": 1}) == result_digest({"a": 1})
    assert len(result_digest({"a": 1})) == 24
