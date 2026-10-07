"""Work that outlives one call: the pending result kind, from the broker to the release.

A long job is accepted long before it is done. Before this kind existed a component had
two answers and both were wrong: return the job id as the result (the submission reported
as the work, the v1 false-success defect) or raise (a ContractViolation recorded for a
call that did exactly what it should). The tests below hold the third answer to its
promises:

* the broker records a pending call in the audit chain with the reference it is collected
  by, labels it like its inputs, and gives the caller no value to read;
* the isolated child can say the same thing, and saying it without a reference is refused;
* the loop keeps the task WAITING, pauses (AWAITING) instead of spinning, collects the work
  on resume by naming its digest, and never starts it a second time — after a checkpoint,
  or with only the operation ledger to go on;
* nothing pending is ever a result or released, whatever the termination says.

The last section states the same promises as properties over random job schedules.
"""

from __future__ import annotations

import json
import sys

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

from psh.capabilities import CapabilityRegistry
from psh.config import PSHConfig
from psh.contracts import (
    Budget, CapabilityUnavailable, ComponentKind, ComponentManifest, ContractViolation,
    PendingResult, ResultPending, RiskTier, content_hash, reference_digest,
)
from psh.durable import Decision, NodeOutcome, RecordKind, WorkflowJournal, diff
from psh.kernel import PendingOutcome, TrustedKernel
from psh.kernel.isolation import _unwrap_child_result
from psh.labels import DataLabel, Labeled, Sensitivity
from psh.runtime import (
    AgentLoopController, CheckpointStore, Criterion, Finalizer, LoopLimits, LoopResult,
    OperationLedger, OperationState, Plan, PlanTask, ResearchRunService, RetryPolicy,
    StaticPlanner, TaskKind, TaskState, Termination, resume,
)
from psh.sir import Acceptance, Effect, ExecKind, SIRNode, SIRProgram, SideEffectClass

from conftest import bench_policy

PHI_TEXT = "Patient Alice Smith MRN 04851923 admitted with chest pain"
REFERENCE = {"job_id": "job1", "executor": "fake-executor", "location": "queue-a"}


def kernel_at(tmp_path, name="k", **policy_kw):
    policy_kw.setdefault("require_claim_support", False)
    return TrustedKernel(PSHConfig(state_dir=tmp_path / name).ensure_dirs(),
                         policy=bench_policy(**policy_kw))


class Job:
    """A long-job component: submits on a fresh call, collects on a continuation.

    ``schedule`` is what each collection answers, in order: ``"running"`` (still pending),
    ``"done"`` (a value), ``"failed"`` (the job ended badly), ``"unreachable"`` (the
    executor cannot be asked), ``"other"`` (a different job: a broken component). A fresh
    call — one without ``_psh_pending`` — is a submission, and every one is counted.
    """

    def __init__(self, cid="job", schedule=("done",), *, idempotent=False, value=None,
                 reference=None, **manifest_kw):
        params = dict(id=cid, name=cid, kind=ComponentKind.TOOL, max_label=Sensitivity.PHI,
                      risk_tier=RiskTier.R1_ROUTINE, idempotent=idempotent)
        params.update(manifest_kw)
        self.manifest = ComponentManifest(**params)
        self.schedule = list(schedule)
        self.value = value if value is not None else {"artefacts": {"model": "ab" * 32}}
        self.reference = dict(reference or {**REFERENCE, "job_id": f"{cid}-1"})
        self.submissions: list[dict] = []
        self.collections: list[str] = []

    @property
    def digest(self) -> str:
        return reference_digest(self.reference)

    def invoke(self, payload, envelope):
        if "_psh_pending" not in payload:
            self.submissions.append(dict(payload))
            return PendingResult(self.reference, "submitted; the job is queued")
        self.collections.append(payload["_psh_pending"])
        answer = self.schedule.pop(0) if self.schedule else "done"
        if answer == "running":
            return PendingResult(self.reference, "the job is running")
        if answer == "failed":
            raise ContractViolation("the job failed: segmentation fault in the sampler")
        if answer == "unreachable":
            raise CapabilityUnavailable("the executor did not answer")
        if answer == "other":
            return PendingResult({**self.reference, "job_id": "someone-else"}, "running")
        return dict(self.value)


def registry_of(*components):
    registry = CapabilityRegistry()
    for component in components:
        registry.register(component)
    return registry


def job_plan(*tasks):
    return Plan(objective="predict a structure", produced_by="test", tasks=tuple(tasks),
                completion_criteria=(Criterion(description="every task ran", kind="task"),))


def tool_task(task_id, component_id="job", **kw):
    kw.setdefault("max_label", Sensitivity.PHI)
    return PlanTask(task_id=task_id, objective=f"run {task_id}", kind=TaskKind.TOOL,
                    component_id=component_id, **kw)


def controller(kernel, plan, registry, **kw):
    kw.setdefault("sleep", lambda s: None)
    kw.setdefault("limits", LoopLimits())
    return AgentLoopController(kernel, planner=StaticPlanner(plan), registry=registry, **kw)


def events_of(kernel, kind):
    return [e for e in kernel.events.records() if e.event_type == kind]


# ================================================================ the contract

@pytest.mark.parametrize("reference,words", [
    ({}, "names nothing"),
    (None, "names nothing"),
    ("job1", "names nothing"),
    ({"job_id": "j", "payload": {"sequence": "MQIF"}}, "content keys"),
    ({"job_id": "j", "nested": {"text": "x"}}, "content keys"),
    ({"job_id": object()}, "plain JSON"),
])
def test_a_pending_result_must_name_its_work_and_carry_no_content(reference, words):
    with pytest.raises(ValueError, match=words):
        PendingResult(reference)


def test_the_reference_digest_is_the_kernels_content_hash():
    assert reference_digest(REFERENCE) == content_hash(REFERENCE)
    assert reference_digest(dict(reversed(list(REFERENCE.items())))) == \
        reference_digest(REFERENCE), "key order does not change what the work is called"


def test_pending_is_neither_a_violation_nor_retryable():
    exc = ResultPending("x", pending=None)
    assert not isinstance(exc, ContractViolation)
    assert not RetryPolicy().permits(exc), "a retried submission is a second job"
    assert not RetryPolicy(retryable=("ContractViolation", "ToolTimeout")).permits(exc)


# ================================================================== the broker

def test_the_broker_records_a_pending_call_and_hands_back_no_value(tmp_path):
    kernel = kernel_at(tmp_path)
    job = Job()
    before = kernel.broker.stats()["tool_calls"]
    with pytest.raises(ResultPending) as raised:
        kernel.broker.call_tool(job, {"sequence": "MQIFVKTLTG"}, kernel.policy.envelope())
    pending = raised.value.pending
    assert isinstance(pending, PendingOutcome)
    assert pending.reference == job.reference and pending.reference_digest == job.digest
    assert not hasattr(pending, "value"), "a pending outcome has nothing to read as a result"
    assert kernel.broker.stats()["tool_calls"] == before + 1, "the call was counted"

    recorded = events_of(kernel, "tool_call_pending")
    assert len(recorded) == 1 and recorded[0].status == "pending"
    assert recorded[0].detail["reference_sha256"] == job.digest
    assert recorded[0].detail["reference"] == job.reference, "the chain holds the reference"
    assert recorded[0].detail["execution"] == "in_process"
    assert not events_of(kernel, "tool_call"), "no completed call was recorded"
    assert kernel.events.verify(), "the chain is intact"


def test_a_pending_outcome_is_labelled_like_the_inputs_that_started_it(tmp_path):
    kernel = kernel_at(tmp_path)
    phi = Labeled({"note": "fold this"}, DataLabel(Sensitivity.PHI,
                                                   categories=("medical_record_number",)))
    with pytest.raises(ResultPending) as raised:
        kernel.broker.call_tool(Job(), phi, kernel.policy.envelope())
    assert raised.value.pending.label.sensitivity is Sensitivity.PHI, \
        "a job started from PHI is PHI however plain its id looks"
    with pytest.raises(ResultPending) as clean:
        kernel.broker.call_tool(Job(cid="job2"), {"sequence": "MQIF"},
                                kernel.policy.envelope())
    assert clean.value.pending.label.sensitivity < Sensitivity.PHI


def test_a_post_tool_hook_withholds_the_pending_reference_too(tmp_path):
    from psh.kernel.hooks import HookBlocked, HookEvent, callable_hook

    kernel = kernel_at(tmp_path)

    def deny(payload):
        raise HookBlocked("results of this tool are withheld", hook="withhold")

    kernel.hooks.register(callable_hook("withhold", HookEvent.POST_TOOL_USE, deny))
    from psh.contracts import EgressDenied

    with pytest.raises(EgressDenied, match="withheld"):
        kernel.broker.call_tool(Job(), {"sequence": "MQIF"}, kernel.policy.envelope())
    assert not events_of(kernel, "tool_call_pending")


# ============================================================== isolated child

def test_a_child_reports_pending_work_with_its_reference():
    unwrapped = _unwrap_child_result(
        {"$psh": {"status": "pending", "reason": "queued", "reference": REFERENCE}})
    assert isinstance(unwrapped, PendingResult) and unwrapped.reference == REFERENCE
    assert unwrapped.reason == "queued"


@pytest.mark.parametrize("envelope", [
    {"status": "pending"},
    {"status": "pending", "reference": {}},
    {"status": "pending", "reference": "job1"},
    {"status": "pending", "reference": {"job_id": "j", "value": {"model": "..."}}},
])
def test_a_child_that_says_pending_without_a_usable_reference_is_refused(envelope):
    with pytest.raises(ContractViolation, match="without a usable reference"):
        _unwrap_child_result({"$psh": envelope})


CHILD = """
import json, sys
request = json.loads(sys.stdin.read())
payload = request["payload"]
reference = {"job_id": "child-1", "executor": "child-executor"}
if "_psh_pending" in payload:
    print(json.dumps({"artefacts": {"model": "cd" * 32},
                      "collected": payload["_psh_pending"]}))
else:
    print(json.dumps({"$psh": {"status": "pending", "reason": "submitted from the child",
                               "reference": reference}}))
"""


def isolated_job(tmp_path):
    script = tmp_path / "child_job.py"
    script.write_text(CHILD)

    class IsolatedJob:
        manifest = ComponentManifest(
            id="iso-job", name="iso-job", kind=ComponentKind.TOOL, backend="subprocess",
            entrypoint=f"{sys.executable} -I {script}", max_label=Sensitivity.PHI,
            idempotent=False, risk_tier=RiskTier.R1_ROUTINE)

        def invoke(self, payload, envelope):  # pragma: no cover - the kernel runs the child
            raise AssertionError("an isolated component is never invoked in process")

    return IsolatedJob()


def test_an_isolated_child_leaves_pending_work_and_collects_it(tmp_path):
    kernel = kernel_at(tmp_path)
    component = isolated_job(tmp_path)
    envelope = kernel.policy.envelope()
    with pytest.raises(ResultPending) as raised:
        kernel.broker.call_tool(component, {"sequence": "MQIF"}, envelope)
    digest = raised.value.pending.reference_digest
    assert raised.value.pending.reference == {"job_id": "child-1",
                                              "executor": "child-executor"}
    assert kernel.broker.stats()["isolated_tool_calls"] == 1
    assert events_of(kernel, "tool_call_pending")[0].detail["execution"] == "isolated"
    result = kernel.broker.call_tool(component, {"sequence": "MQIF", "_psh_pending": digest},
                                     envelope)
    assert result.value["collected"] == digest


# ===================================================================== the loop

def test_a_loop_waits_on_a_job_and_releases_nothing(tmp_path):
    kernel = kernel_at(tmp_path)
    job = Job(schedule=("running", "done"))
    ledger = OperationLedger(tmp_path / "ops.db")
    envelope = kernel.policy.envelope()
    result = controller(kernel, job_plan(tool_task("fold")), registry_of(job),
                        operations=ledger).run("fold", envelope)

    assert result.termination is Termination.AWAITING and not result.ok
    assert result.results == {}, "a submission is not a result"
    assert set(result.pending) == {"fold"}
    assert result.pending["fold"].reference_digest == job.digest
    assert result.state_counts == {"waiting": 1}
    record = ledger.get(f"{envelope.run_id}:fold")
    assert record.state is OperationState.PENDING and record.result_digest == job.digest
    assert ledger.in_doubt() == [], "pending work is named, so it is not in doubt"
    assert [r.key for r in ledger.pending(envelope.run_id)] == [record.key]

    released = Finalizer(kernel).finalize(result, envelope)
    assert released.status == "not_completed" and released.released_output is None
    assert released.refusal_kind == "awaiting"
    assert "task fold waits on work that has not finished" in released.limitations
    waiting = events_of(kernel, "loop_task_waiting")
    assert waiting and waiting[0].detail["reference_sha256"] == job.digest
    assert kernel.events.verify()
    ledger.close()


def test_resuming_collects_the_job_by_its_digest_and_never_submits_twice(tmp_path):
    kernel = kernel_at(tmp_path)
    job = Job(schedule=("running", "done"), value={"artefacts": {"model": "ab" * 32}})
    store = CheckpointStore(tmp_path / "ckpt")
    ledger = OperationLedger(tmp_path / "ops.db")
    plan = job_plan(tool_task("fold"), tool_task("score", component_id="score",
                                                 dependencies=("fold",)))
    score = Job(cid="score", idempotent=True)
    score.invoke = lambda payload, envelope: {"score": 0.9}          # an ordinary tool
    registry = registry_of(job, score)
    service = ResearchRunService(kernel, planner=StaticPlanner(plan), registry=registry,
                                 checkpoints=store, operations=ledger)

    first = service.run("fold then score")
    assert first.status == "not_completed" and first.refusal_kind == "awaiting"
    loop_id = service.last_loop_result.loop_id
    iterations = service.last_loop_result.iterations

    second = service.resume(store.latest_for(loop_id))               # still running
    assert second.status == "not_completed" and second.refusal_kind == "awaiting"
    assert service.last_loop_result.iterations == iterations, \
        "a collection that changed nothing spends no iteration"

    third = service.resume(store.latest_for(loop_id))                # done
    assert third.status == "released", third.summary()
    final = service.last_loop_result
    assert final.termination is Termination.GOAL_SATISFIED and final.pending == {}
    assert final.results["fold"] == {"artefacts": {"model": "ab" * 32}}
    assert final.results["score"] == {"score": 0.9}
    assert len(job.submissions) == 1, "the job was submitted once"
    assert job.collections == [job.digest, job.digest], "each collection named the work"
    assert ledger.get(f"{final.run_id}:fold").state is OperationState.SUCCEEDED
    assert kernel.events.verify()
    ledger.close()


def test_a_dependent_task_waits_for_the_job_it_reads(tmp_path):
    kernel = kernel_at(tmp_path)
    job = Job(schedule=("done",))
    seen: list = []

    class Reader:
        manifest = ComponentManifest(id="reader", name="reader", kind=ComponentKind.TOOL,
                                     max_label=Sensitivity.PHI)

        def invoke(self, payload, envelope):
            seen.append(payload.get("upstream"))
            return {"read": True}

    plan = job_plan(tool_task("fold"), tool_task("read", component_id="reader",
                                                 dependencies=("fold",)))
    ctl = controller(kernel, plan, registry_of(job, Reader()),
                     checkpoints=CheckpointStore(tmp_path / "ckpt"))
    envelope = kernel.policy.envelope()
    paused = ctl.run("fold then read", envelope)
    assert paused.termination is Termination.AWAITING and seen == []
    state = resume(ctl.checkpoints.latest_for(paused.loop_id), kernel)
    assert state.graph.nodes["fold"].state is TaskState.WAITING
    assert state.graph.nodes["read"].state is TaskState.PENDING, "paused, not cancelled"
    done = ctl.run("fold then read", state.envelope, resume_from=state)
    assert done.termination is Termination.GOAL_SATISFIED
    assert len(seen) == 1 and seen[0]["fold"] == job.value, "it read the collected value"


def test_the_ledger_alone_is_enough_to_collect_instead_of_resubmitting(tmp_path):
    """A restart that lost the graph (no checkpoint) still knows the work is pending."""
    kernel = kernel_at(tmp_path)
    job = Job(schedule=("done",))
    ledger = OperationLedger(tmp_path / "ops.db")
    envelope = kernel.policy.envelope()
    plan = job_plan(tool_task("fold"))
    first = controller(kernel, plan, registry_of(job), operations=ledger).run("f", envelope)
    assert first.termination is Termination.AWAITING
    ledger.close()

    reopened = OperationLedger(tmp_path / "ops.db")           # the process restarted
    again = controller(kernel, plan, registry_of(job), operations=reopened).run("f", envelope)
    assert again.termination is Termination.GOAL_SATISFIED, again.reason
    assert len(job.submissions) == 1 and job.collections == [job.digest]
    reopened.close()


def test_a_component_that_answers_a_collection_with_other_work_fails(tmp_path):
    kernel = kernel_at(tmp_path)
    job = Job(schedule=("other",))
    ledger = OperationLedger(tmp_path / "ops.db")
    envelope = kernel.policy.envelope()
    ctl = controller(kernel, job_plan(tool_task("fold")), registry_of(job),
                     operations=ledger, checkpoints=CheckpointStore(tmp_path / "c"),
                     limits=LoopLimits(max_replans=0))
    paused = ctl.run("f", envelope)
    state = resume(ctl.checkpoints.latest_for(paused.loop_id), kernel)
    ended = ctl.run("f", state.envelope, resume_from=state)
    assert ended.termination is Termination.ESCALATED
    assert any("never starts the work again" in f for f in ended.failures)
    assert ledger.get(f"{envelope.run_id}:fold").state is OperationState.UNKNOWN
    assert len(job.submissions) == 1
    ledger.close()


def test_a_job_that_failed_fails_its_task_and_is_not_resubmitted(tmp_path):
    kernel = kernel_at(tmp_path)
    job = Job(schedule=("failed",))
    ledger = OperationLedger(tmp_path / "ops.db")
    envelope = kernel.policy.envelope()
    plan = job_plan(tool_task("fold", retry=RetryPolicy(max_attempts=3)))
    ctl = controller(kernel, plan, registry_of(job), operations=ledger,
                     limits=LoopLimits(max_replans=0))
    assert ctl.run("f", envelope).termination is Termination.AWAITING
    ended = ctl.run("f", envelope)          # the same run id: the ledger names the work
    assert ended.termination is Termination.ESCALATED
    assert any("segmentation fault" in f for f in ended.failures)
    assert len(job.submissions) == 1, "a failed job of a non-idempotent tool is not retried"
    assert ledger.get(f"{envelope.run_id}:fold").state is OperationState.UNKNOWN
    ledger.close()


def test_an_executor_out_of_reach_leaves_the_work_pending(tmp_path):
    kernel = kernel_at(tmp_path)
    job = Job(schedule=("unreachable", "done"))
    ledger = OperationLedger(tmp_path / "ops.db")
    envelope = kernel.policy.envelope()
    ctl = controller(kernel, job_plan(tool_task("fold")), registry_of(job), operations=ledger)
    assert ctl.run("f", envelope).termination is Termination.AWAITING
    unreachable = ctl.run("f", envelope)
    assert unreachable.termination is Termination.AWAITING, "an outage is not an outcome"
    assert set(unreachable.pending) == {"fold"}
    assert ledger.get(f"{envelope.run_id}:fold").state is OperationState.PENDING
    assert events_of(kernel, "loop_task_collection_unavailable")
    assert ctl.run("f", envelope).termination is Termination.GOAL_SATISFIED
    assert len(job.submissions) == 1
    ledger.close()


def test_a_bound_that_stops_a_collection_leaves_the_task_waiting(tmp_path):
    kernel = kernel_at(tmp_path)
    job = Job(schedule=("done",))
    ctl = controller(kernel, job_plan(tool_task("fold")), registry_of(job),
                     checkpoints=CheckpointStore(tmp_path / "c"))
    envelope = kernel.policy.envelope(budget=Budget(max_tool_calls=1))
    paused = ctl.run("f", envelope)
    assert paused.termination is Termination.AWAITING
    state = resume(ctl.checkpoints.latest_for(paused.loop_id), kernel)
    ended = ctl.run("f", state.envelope, resume_from=state)
    assert ended.termination is Termination.BUDGET_EXHAUSTED
    assert set(ended.pending) == {"fold"}, "the job still runs; the loop says so"
    assert ended.state_counts.get("waiting") == 1 and "fold" not in ended.results
    assert Finalizer(kernel).finalize(ended, envelope).status == "not_completed"


def test_a_loop_that_ends_never_calls_a_waiting_task_cancelled(tmp_path):
    kernel = kernel_at(tmp_path)

    class Token:
        cancelled = False
        reason = "the operator stopped the run"

    token = Token()
    job = Job(schedule=("running",) * 5)
    stopper = Job(cid="stopper", idempotent=True)
    stopper.invoke = lambda payload, envelope: (setattr(token, "cancelled", True)
                                                or {"ok": True})
    after = Job(cid="after", idempotent=True)
    plan = job_plan(tool_task("fold"), tool_task("stop", component_id="stopper"),
                    tool_task("next", component_id="after", dependencies=("stop",)))
    envelope = kernel.policy.envelope()
    result = controller(kernel, plan, registry_of(job, stopper, after),
                        cancellation=token).run("f", envelope)
    assert result.termination is Termination.CANCELLED
    assert set(result.pending) == {"fold"}, "the job still runs; the loop says so"
    assert result.state_counts == {"waiting": 1, "succeeded": 1, "cancelled": 1}
    assert Finalizer(kernel).finalize(result, envelope).status == "not_completed"


def test_a_finalizer_never_releases_pending_work_whatever_the_termination(tmp_path):
    kernel = kernel_at(tmp_path)
    envelope = kernel.policy.envelope()
    forged = LoopResult(loop_id="l", run_id=envelope.run_id,
                        termination=Termination.GOAL_SATISFIED, results={},
                        goal_status="verified",
                        pending={"fold": PendingOutcome(REFERENCE, reference_digest(REFERENCE),
                                                        DataLabel())})
    released = Finalizer(kernel).finalize(forged, envelope, output="the structure is ready")
    assert released.status == "not_completed" and released.released_output is None
    assert "task fold waits on work that has not finished" in released.limitations


def test_only_a_tool_call_may_leave_work_pending(tmp_path):
    kernel = kernel_at(tmp_path)

    def backend(contract):
        raise ResultPending("a delegate cannot be collected",
                            pending=PendingOutcome(REFERENCE, reference_digest(REFERENCE),
                                                   DataLabel()))

    plan = job_plan(PlanTask(task_id="d", objective="delegate", kind=TaskKind.DELEGATE))
    result = AgentLoopController(kernel, planner=StaticPlanner(plan), delegate_backend=backend,
                                 sleep=lambda s: None, limits=LoopLimits(max_replans=0)
                                 ).run("d", kernel.policy.envelope())
    assert result.termination is Termination.ESCALATED and result.pending == {}
    assert any("only a tool call can leave work" in f for f in result.failures)


def test_a_checkpoint_keeps_the_waiting_work_under_the_rules_for_a_result(tmp_path):
    kernel = kernel_at(tmp_path)
    job = Job()
    store = CheckpointStore(tmp_path / "c")
    plan = job_plan(tool_task("fold", input_sensitivity=Sensitivity.PHI))
    ctl = controller(kernel, plan, registry_of(job), checkpoints=store)
    paused = ctl.run("f", kernel.policy.envelope())
    record = store.latest_for(paused.loop_id).task_states["fold"]
    assert record["state"] == "waiting"
    assert record["pending"]["reference_sha256"] == job.digest
    assert record["pending"]["label"]["sensitivity"] == "PHI"
    assert record["pending"]["reference"] == job.reference, \
        "this policy stores PHI on local disk, so the reference is kept like a result"
    restored = resume(store.latest_for(paused.loop_id), kernel).graph.nodes["fold"]
    assert restored.state is TaskState.WAITING
    assert restored.pending.reference_digest == job.digest
    assert restored.pending.label.sensitivity is Sensitivity.PHI


def test_a_run_that_may_not_persist_keeps_only_the_digest_of_its_waiting_work(tmp_path):
    from psh.labels import Destination

    kernel = kernel_at(tmp_path)
    job = Job(schedule=("done",))
    store = CheckpointStore(tmp_path / "c")
    plan = job_plan(tool_task("fold"))
    envelope = kernel.policy.envelope(allowed_destinations=(
        Destination.LOCAL_COMPUTE, Destination.LOCAL_MODEL, Destination.USER_OUTPUT))
    ctl = controller(kernel, plan, registry_of(job), checkpoints=store)
    paused = ctl.run("f", envelope)
    checkpoint = store.latest_for(paused.loop_id)
    record = checkpoint.task_states["fold"]
    assert record["pending"]["reference_sha256"] == job.digest
    assert record["pending"]["reference"] is None
    assert "fake-executor" not in json.dumps(checkpoint.to_dict()), \
        "a run that may not persist writes no reference to disk, only its digest"
    state = resume(checkpoint, kernel, objective="f", plan=plan)
    done = ctl.run("f", state.envelope, resume_from=state)
    assert done.termination is Termination.GOAL_SATISFIED, done.reason
    assert job.collections == [job.digest] and len(job.submissions) == 1


# ============================================================ the durable journal

def sir_node(node_id, side_effect):
    return SIRNode(node_id=node_id, kind=ExecKind.TOOL, objective=f"run {node_id}",
                   component_id="tool", effects=frozenset((Effect.READ_LOCAL,)),
                   side_effect=side_effect)


@pytest.mark.parametrize("side_effect,decision", [
    (SideEffectClass.NON_REPEATABLE, Decision.UNSAFE),
    (SideEffectClass.IDEMPOTENT, Decision.RECOMPUTE),
])
def test_the_journal_records_waiting_work_and_a_rerun_does_not_start_it_twice(
        side_effect, decision):
    journal = WorkflowJournal()
    program = SIRProgram(question="q", program_id="p",
                         acceptance=(Acceptance(description="d", kind="task"),),
                         nodes=(sir_node("fold", side_effect),))
    signature = program.signature("fold")
    journal.append(RecordKind.NODE_STARTED, "r", node_id="fold", signature=signature)
    journal.append(RecordKind.NODE_WAITING, "r", node_id="fold", signature=signature,
                   reference_sha256="ab" * 32)
    replay = journal.replay("r")
    assert replay.outcome("fold") is NodeOutcome.WAITING
    assert replay.waiting() == ("fold",) and replay.in_doubt() == ()
    assert not replay.has_result("fold"), "waiting work is never a stored result"
    assert diff(program, replay).delta_for("fold").decision is decision


# ================================================================== properties

SCHEDULES = st.lists(st.sampled_from(["running", "done", "failed", "unreachable"]),
                     min_size=0, max_size=4)


@settings(max_examples=30, deadline=None,
          suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(schedules=st.lists(SCHEDULES, min_size=1, max_size=3),
       chained=st.booleans(), phi=st.booleans())
def test_pending_work_is_never_a_result_never_released_and_never_resubmitted(
        tmp_path_factory, schedules, chained, phi):
    tmp_path = tmp_path_factory.mktemp("prop")
    kernel = kernel_at(tmp_path)
    jobs = [Job(cid=f"job{i}", schedule=s) for i, s in enumerate(schedules)]
    tasks = []
    for i, _ in enumerate(jobs):
        deps = (f"t{i - 1}",) if chained and i else ()
        tasks.append(tool_task(f"t{i}", component_id=f"job{i}", dependencies=deps,
                               input_sensitivity=Sensitivity.PHI if phi
                               else Sensitivity.PUBLIC))
    store = CheckpointStore(tmp_path / "c")
    ledger = OperationLedger(tmp_path / "ops.db")
    ctl = controller(kernel, job_plan(*tasks), registry_of(*jobs), checkpoints=store,
                     operations=ledger, limits=LoopLimits(max_replans=0))
    envelope = kernel.policy.envelope()
    result = ctl.run("jobs", envelope)
    # enough resumes for every poll of every job, run one after another
    for _ in range(sum(len(s) + 2 for s in schedules)):
        # nothing pending is a result, and nothing pending is released
        assert not set(result.pending) & set(result.results)
        released = Finalizer(kernel).finalize(result, envelope)
        if result.pending:
            assert released.status != "released" and released.released_output is None
        if result.termination is Termination.GOAL_SATISFIED:
            assert result.pending == {} and result.state_counts.get("waiting", 0) == 0
        for task_id, outcome in result.pending.items():
            assert ledger.get(f"{envelope.run_id}:{task_id}").state is OperationState.PENDING
            if phi:
                assert outcome.label.sensitivity is Sensitivity.PHI
        if result.termination is not Termination.AWAITING:
            break
        state = resume(store.latest_for(result.loop_id), kernel)
        result = ctl.run("jobs", state.envelope, resume_from=state)
    assert result.termination is not Termination.AWAITING, "every schedule ends"
    for job in jobs:
        assert len(job.submissions) <= 1, "no job is ever submitted twice"
        assert all(d == job.digest for d in job.collections)
    pending_events = events_of(kernel, "tool_call_pending")
    digests = {j.digest for j in jobs}
    assert all(e.detail["reference_sha256"] in digests for e in pending_events)
    assert kernel.events.verify(), "the audit chain is intact after every schedule"
    ledger.close()
    kernel.close()
