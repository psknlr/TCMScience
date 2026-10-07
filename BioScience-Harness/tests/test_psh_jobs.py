"""Long jobs as governed tool calls: submit, collect, cancel and restart through PSH.

A job tool (``bioagent.backends.jobtool``) admitted through the bridge, in process and in
an isolated child, on the local executor and on the HTTP job-service fixture of
``test_compute_jobs.py``. What each test holds:

* a submission is a tool call whose answer is pending work, recorded in PSH's audit chain
  with the job reference — never a result, and never a ContractViolation;
* collecting a running job is pending again; a finished one returns its validated
  artefacts with their SHA-256 digests; a failed, corrupt or missing one is not a success;
* a continuation (``_psh_pending``) collects and never submits, and a key names one job;
* a cancellation needs a grant recorded in the chain and the trace beforehand;
* a restart reads ``open_jobs`` back, ``reconcile`` turns a call that died in doubt into
  pending work, and the resumed loop collects the job it started instead of a second one.

The local jobs wait on a gate file, so a test decides when a job finishes.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import re
import sys
import threading
import time
from pathlib import Path

import pytest

psh = pytest.importorskip("psh", reason="PSH-Harness is not importable here")

from psh.capabilities import CapabilityRegistry  # noqa: E402
from psh.config import PSHConfig  # noqa: E402
from psh.contracts import (  # noqa: E402
    Autonomy, ContractViolation, PolicyDenied, ResultPending, RiskTier, content_hash,
)
from psh.kernel import TrustedKernel  # noqa: E402
from psh.labels import Destination, Sensitivity  # noqa: E402
from psh.policy import PolicySnapshot  # noqa: E402
from psh.runtime import (  # noqa: E402
    CheckpointStore, Criterion, OperationLedger, OperationState, Plan, PlanTask,
    ResearchRunService, StaticPlanner, TaskKind,
)

from bioagent.backends.jobs import (  # noqa: E402
    ArtefactSpec, JobRef, JobState, LocalSubprocessJobs, open_jobs,
)
from bioagent.backends.jobtool import JobDefinition, JobTool, reference_digest  # noqa: E402
from bioagent.policy import PROFILES, PermissionProfile, PolicyKernel  # noqa: E402
from bioagent.psh import (  # noqa: E402
    BioScienceBridge, BridgeRefused, default_runtime, grant_cancellation, reconcile,
)
from bioagent.runtime.agentspec import AgentSpec  # noqa: E402
from bioagent.runtime.component import (  # noqa: E402
    ComponentManifest, LicenseSpec, Permissions, RuntimeSpec,
)
from bioagent.runtime.events import EventLog, EventType  # noqa: E402
from bioagent.status import ExecutionStatus  # noqa: E402

from test_compute_jobs import MODEL, executor as service_executor  # noqa: E402
from test_compute_jobs import service  # noqa: E402,F401 - the fixture, requested below

OPEN = (Destination.LOCAL_COMPUTE, Destination.LOCAL_MODEL, Destination.USER_OUTPUT,
        Destination.PERSISTENT, Destination.PUBLIC_REMOTE)

#: A job that waits for its gate file, then writes its model and notes that it ran.
GATED = ("import os, sys, time\n"
         "out, gate = sys.argv[1], sys.argv[2]\n"
         "deadline = time.time() + 60\n"
         "while not os.path.exists(gate) and time.time() < deadline:\n"
         "    time.sleep(0.02)\n"
         "if not os.path.exists(gate):\n"
         "    sys.exit('the gate never opened')\n"
         "open(os.path.join(out, 'model.cif'), 'w').write('data_model\\n')\n"
         "open(os.path.join(out, 'runs.txt'), 'a').write('ran\\n')\n")


def policy(**kw):
    params = dict(profile_id="jobs_bench", max_data_label=Sensitivity.PHI,
                  allowed_destinations=OPEN, autonomy=Autonomy.ACT,
                  risk_ceiling=RiskTier.R3_CLINICAL, require_claim_support=False)
    params.update(kw)
    return PolicySnapshot(**params)


@pytest.fixture
def kernel(tmp_path):
    k = TrustedKernel(PSHConfig(state_dir=tmp_path / "k").ensure_dirs(), policy=policy())
    yield k
    k.close()


def local_tool(base: Path, *, executor=None, validators=None) -> JobTool:
    return JobTool(executor or LocalSubprocessJobs(base / "jobs"),
                   JobDefinition(arguments=("gate",),
                                 argv=(sys.executable, "-c", GATED, "{output}", "{gate}"),
                                 artefacts=(ArtefactSpec("model", "model.cif"),
                                            ArtefactSpec("runs", "runs.txt")),
                                 validators=dict(validators or {})),
                   trace=base / "trace" / "fold.json")


def job_manifest(tool: JobTool, *, cid="tests.job.fold", backend="subprocess",
                 subprocess=True, network=(), writes=None) -> ComponentManifest:
    if writes is None:
        writes = [str(tool.trace.parent)]
        if isinstance(tool.executor, LocalSubprocessJobs):
            writes.append(str(tool.executor.root))
        if tool.collect_dir is not None:
            writes.append(str(tool.collect_dir))
    return ComponentManifest(
        id=cid, kind="tool", name="fold, as a long job",
        description="a structure prediction that runs for hours on a job executor",
        runtime=RuntimeSpec(backend=backend),
        permissions=Permissions(subprocess=subprocess, network=tuple(network),
                                filesystem_write=tuple(writes)),
        license=LicenseSpec(spdx="MIT", integration_mode="native"))


def admitted(kernel, tool, tmp_path, *, isolate=False, runtime_kernel=None, spec=None,
             events=None, **manifest_kw):
    runtime = default_runtime(catalogue=False, public_apis=False, native_tools=False,
                              skills=False, data_lake=tmp_path / "no-lake",
                              kernel=runtime_kernel)
    kw = {"roots": {"workspace": tmp_path / "ws"}} if isolate else {}
    bridge = BioScienceBridge(kernel, runtime, isolate=isolate, spec=spec, events=events,
                              **kw)
    manifest = bridge.admit(job_manifest(tool, **manifest_kw), jobs=tool)
    return bridge, bridge.component(manifest.id)


def pending_of(kernel, component, payload, envelope):
    with pytest.raises(ResultPending) as raised:
        kernel.broker.call_tool(component, payload, envelope)
    return raised.value.pending


def finished(executor, ref, timeout_s=30.0) -> JobState:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        state = executor.status(ref).state
        if state.finished:
            return state
        time.sleep(0.05)
    raise AssertionError(f"job {ref.job_id} did not finish within {timeout_s}s")


def trace_events(tool, kind):
    return [e for e in EventLog.read(tool.trace).events if e.event_type == kind]


def chain(kernel, kind):
    return [e for e in kernel.events.records() if e.event_type == kind]


# ======================================================== the reference and the tool

def test_the_child_computes_the_digest_psh_names_pending_work_by():
    reference = {"job_id": "j1", "executor": "local-subprocess", "submitted_at": 1.5,
                 "artefacts": [{"name": "model", "path": "model.cif", "required": True}]}
    assert reference_digest(reference) == content_hash(reference)


@pytest.mark.parametrize("definition,words", [
    (dict(argv=("x", "{missing}")), "not an argument"),
    (dict(arguments=("a",), argv=("x", "{a}"), validators={"model": "m:f"}),
     "not an artefact"),
    (dict(artefacts=(ArtefactSpec("model", "m.cif"),), validators={"model": "nocolon"}),
     "module:function"),
])
def test_a_job_definition_refuses_what_it_could_not_honour(definition, words):
    with pytest.raises(ValueError, match=words):
        JobDefinition(**definition)


@pytest.mark.parametrize("arguments,words", [
    ({}, "missing argument 'gate'"),
    ({"gate": "g", "extra": 1}, "unknown argument 'extra'"),
    ({"gate": ["a", "b"]}, "must be a string or a number"),
    ({"gate": "{output}"}, "only the executor may fill in"),
])
def test_a_call_gives_exactly_the_arguments_the_job_takes(tmp_path, arguments, words):
    with pytest.raises(ValueError, match=words):
        local_tool(tmp_path).definition.spec("tests.job.fold", arguments)


def test_a_tool_survives_the_trip_to_another_process(tmp_path):
    tool = local_tool(tmp_path, validators={"model": "json:loads"})
    copied = JobTool.load(tool.write(tmp_path / "tool.json"))
    assert copied.digest == tool.digest and copied.config() == tool.config()
    assert oct((tmp_path / "tool.json").stat().st_mode)[-3:] == "600"


# ============================================================== in process, local

def test_a_submission_is_pending_work_recorded_in_the_chain(kernel, tmp_path):
    tool = local_tool(tmp_path)
    bioscience = EventLog()
    bridge, component = admitted(kernel, tool, tmp_path, events=bioscience)
    manifest = component.manifest
    assert manifest.mutates and not manifest.idempotent
    assert manifest.risk_tier is RiskTier.R2_CONSEQUENTIAL
    assert manifest.min_autonomy is Autonomy.ACT_WITH_APPROVAL
    assert manifest.provenance["job_tool"] is True
    envelope = kernel.policy.envelope()

    pending = pending_of(kernel, component, {"gate": str(tmp_path / "gate"),
                                             "_psh_idempotency_key": "run:fold"}, envelope)
    reference = pending.reference
    assert reference["executor"] == "local-subprocess" and reference["job_id"]
    assert pending.reference_digest == reference_digest(reference)
    (recorded,) = chain(kernel, "tool_call_pending")
    assert recorded.status == "pending"
    assert recorded.detail["reference"]["job_id"] == reference["job_id"]
    assert recorded.detail["reference_sha256"] == pending.reference_digest
    assert not chain(kernel, "tool_call"), "a submission is not a completed call"
    assert component.last_status is ExecutionStatus.RUNNING, "never SUCCEEDED"
    called = bioscience.of_type(EventType.TOOL_CALLED)
    assert [e.status for e in called] == ["RUNNING"], "BioScience's own log says so too"
    assert [e.status for e in bioscience.of_type(EventType.POLICY_CHECKED)] == ["ALLOW"]
    kinds = [e.event_type for e in EventLog.read(tool.trace).events]
    assert kinds[:2] == [EventType.JOB_REQUESTED, EventType.JOB_SUBMITTED]
    (tmp_path / "gate").write_text("open")
    assert kernel.events.verify()


def test_collecting_returns_pending_until_the_artefacts_validate(kernel, tmp_path):
    tool = local_tool(tmp_path)
    _, component = admitted(kernel, tool, tmp_path)
    envelope = kernel.policy.envelope()
    gate = tmp_path / "gate"
    submitted = pending_of(kernel, component, {"gate": str(gate)}, envelope)
    job_id = submitted.reference["job_id"]

    again = pending_of(kernel, component, {"operation": "collect", "job": job_id}, envelope)
    assert again.reference_digest == submitted.reference_digest
    assert "is running" in again.reason
    gate.write_text("open")
    assert finished(tool.executor, JobRef.from_dict(submitted.reference)) is JobState.COMPLETED
    result = kernel.broker.call_tool(component, {"operation": "collect", "job": job_id},
                                     envelope)
    artefacts = result.value["artefacts"]
    model = Path(artefacts["model"]["path"])
    assert artefacts["model"]["sha256"] == hashlib.sha256(model.read_bytes()).hexdigest()
    assert artefacts["runs"]["size"] == len("ran\n")
    assert result.value["state"] == "completed"
    collected = trace_events(tool, EventType.JOB_COLLECTED)
    assert collected[-1].status == "SUCCEEDED"
    assert {e.detail["name"] for e in trace_events(tool, EventType.ARTIFACT_CREATED)} == \
        {"model", "runs"}
    assert component.last_status is ExecutionStatus.SUCCEEDED


def reject_model(path):
    """A validator the tests name as ``test_psh_jobs:reject_model``."""
    return "not a model this test accepts"


def test_an_artefact_that_fails_its_validator_is_not_a_success(kernel, tmp_path):
    tool = local_tool(tmp_path, validators={"model": "test_psh_jobs:reject_model"})
    _, component = admitted(kernel, tool, tmp_path)
    envelope = kernel.policy.envelope()
    (tmp_path / "gate").write_text("open")
    submitted = pending_of(kernel, component, {"gate": str(tmp_path / "gate")}, envelope)
    finished(tool.executor, JobRef.from_dict(submitted.reference))
    with pytest.raises(ContractViolation, match="not a model this test accepts"):
        kernel.broker.call_tool(component, {"operation": "collect",
                                            "job": submitted.reference["job_id"]}, envelope)
    unloadable = local_tool(tmp_path / "u", validators={"model": "no_such_module_x:f"})
    _, other = admitted(kernel, unloadable, tmp_path / "u", cid="tests.job.other")
    done = pending_of(kernel, other, {"gate": str(tmp_path / "gate")}, envelope)
    finished(unloadable.executor, JobRef.from_dict(done.reference))
    with pytest.raises(ContractViolation, match="cannot be loaded"):
        kernel.broker.call_tool(other, {"operation": "collect",
                                        "job": done.reference["job_id"]}, envelope)


def test_a_job_stopped_at_its_wall_clock_limit_is_a_timeout(kernel, tmp_path):
    from psh.contracts import ToolTimeout

    tool = JobTool(LocalSubprocessJobs(tmp_path / "jobs"),
                   JobDefinition(argv=(sys.executable, "-c", "import time; time.sleep(60)"),
                                 artefacts=(ArtefactSpec("model", "model.cif"),),
                                 timeout_s=0.3),
                   trace=tmp_path / "trace" / "fold.json")
    _, component = admitted(kernel, tool, tmp_path)
    envelope = kernel.policy.envelope()
    submitted = pending_of(kernel, component, {}, envelope)
    assert finished(tool.executor, JobRef.from_dict(submitted.reference)) is JobState.FAILED
    with pytest.raises(ToolTimeout, match="timed out"):
        kernel.broker.call_tool(component, {"operation": "collect",
                                            "job": submitted.reference["job_id"]}, envelope)


def test_a_continuation_collects_and_a_key_names_one_job(kernel, tmp_path):
    tool = local_tool(tmp_path)
    _, component = admitted(kernel, tool, tmp_path)
    envelope = kernel.policy.envelope()
    gate = tmp_path / "gate"
    payload = {"gate": str(gate), "_psh_idempotency_key": "run:fold"}
    first = pending_of(kernel, component, payload, envelope)
    second = pending_of(kernel, component, payload, envelope)
    assert second.reference_digest == first.reference_digest, "a key names one job"
    continued = pending_of(kernel, component,
                           {**payload, "_psh_pending": first.reference_digest}, envelope)
    assert continued.reference_digest == first.reference_digest
    with pytest.raises(ContractViolation, match="does not hold"):
        kernel.broker.call_tool(component, {**payload, "_psh_pending": "ab" * 32}, envelope)
    with pytest.raises(ContractViolation, match="another call"):
        kernel.broker.call_tool(component, {"gate": str(gate), "_psh_idempotency_key": "x:y",
                                            "_psh_pending": first.reference_digest}, envelope)
    assert len(list(tool.executor.root.iterdir())) == 1, "one job, however it was asked"
    gate.write_text("open")


def test_a_payload_cannot_fill_the_job_keywords_psh_owns(kernel, tmp_path):
    _, component = admitted(kernel, local_tool(tmp_path), tmp_path)
    for reserved in ("job_key", "job_pending"):
        with pytest.raises(ContractViolation, match="filled from PSH's bookkeeping"):
            kernel.broker.call_tool(component, {"gate": "g", reserved: "run:other"},
                                    kernel.policy.envelope())


def test_the_manifest_must_declare_what_the_job_tool_does(kernel, tmp_path):
    tool = local_tool(tmp_path)
    runtime = default_runtime(catalogue=False, public_apis=False, native_tools=False,
                              skills=False, data_lake=tmp_path / "no-lake")
    bridge = BioScienceBridge(kernel, runtime, isolate=False)
    with pytest.raises(BridgeRefused, match="subprocess: true"):
        bridge.admit(job_manifest(tool, subprocess=False), jobs=tool)
    with pytest.raises(BridgeRefused, match="does not cover"):
        bridge.admit(job_manifest(tool, cid="tests.job.b", writes=[str(tool.trace.parent)]),
                     jobs=tool)
    with pytest.raises(BridgeRefused, match="backend 'subprocess'"):
        bridge.admit(job_manifest(tool, cid="tests.job.c", backend="python"), jobs=tool)


def test_the_bioscience_policy_rules_on_every_job_call(kernel, tmp_path):
    tool = local_tool(tmp_path)
    offline = AgentSpec(name="offline", permission_profile="offline-analysis")
    _, component = admitted(kernel, tool, tmp_path, spec=offline)
    with pytest.raises(PolicyDenied, match="forbids subprocess"):
        kernel.broker.call_tool(component, {"gate": "g"}, kernel.policy.envelope())
    assert not tool.executor.root.exists() or not any(tool.executor.root.iterdir()), \
        "the BioScience kernel refused before a job was started"


def test_cancelling_needs_the_grant_recorded_in_the_chain_and_the_trace(kernel, tmp_path):
    tool = local_tool(tmp_path)
    _, component = admitted(kernel, tool, tmp_path)
    envelope = kernel.policy.envelope()
    first = pending_of(kernel, component, {"gate": str(tmp_path / "g1")}, envelope)
    other = pending_of(kernel, component, {"gate": str(tmp_path / "g2")}, envelope)
    job, other_job = first.reference["job_id"], other.reference["job_id"]
    ref = JobRef.from_dict(first.reference)

    with pytest.raises(PolicyDenied, match="no grant"):
        kernel.broker.call_tool(component, {"operation": "cancel", "job": job}, envelope)
    with pytest.raises(PolicyDenied, match="no grant"):
        kernel.broker.call_tool(component, {"operation": "cancel", "job": job,
                                            "grant": "grant-written-by-the-planner"}, envelope)
    assert tool.executor.status(ref).state is JobState.RUNNING

    with pytest.raises(ValueError, match="no job"):
        grant_cancellation(kernel, component, "job-nobody-submitted",
                           granted_by="the operator", reason="typo")
    grant = grant_cancellation(kernel, component, job, granted_by="the operator",
                               reason="superseded by run 12", run_id=envelope.run_id)
    (granted,) = chain(kernel, "job_cancel_granted")
    assert granted.detail["grant_id"] == grant and granted.detail["job_id"] == job
    assert granted.detail["reference_sha256"] == first.reference_digest

    with pytest.raises(PolicyDenied, match=f"names job {job}"):
        kernel.broker.call_tool(component, {"operation": "cancel", "job": other_job,
                                            "grant": grant}, envelope)
    result = kernel.broker.call_tool(component, {"operation": "cancel", "job": job,
                                                 "grant": grant}, envelope)
    assert result.value["cancelled"] is True and result.value["state"] == "cancelled"
    assert tool.executor.status(ref).state is JobState.CANCELLED
    statuses = [e.status for e in trace_events(tool, EventType.JOB_CANCELLED)]
    assert statuses == ["DENIED", "DENIED", "DENIED", "CANCELLED"]
    with pytest.raises(ContractViolation, match="cancelled"):
        kernel.broker.call_tool(component, {"operation": "collect", "job": job}, envelope)
    other_grant = grant_cancellation(kernel, component, other_job, granted_by="the operator",
                                     reason="cleanup", run_id=envelope.run_id)
    kernel.broker.call_tool(component, {"operation": "cancel", "job": other_job,
                                        "grant": other_grant}, envelope)


def test_a_grant_the_chain_cannot_hold_is_not_given(kernel, tmp_path, monkeypatch):
    tool = local_tool(tmp_path)
    _, component = admitted(kernel, tool, tmp_path)
    envelope = kernel.policy.envelope()
    pending = pending_of(kernel, component, {"gate": str(tmp_path / "gate")}, envelope)
    monkeypatch.setattr(kernel, "audit", lambda *a, **k: None)      # the chain records nothing
    with pytest.raises(RuntimeError, match="does not hold grant"):
        grant_cancellation(kernel, component, pending.reference["job_id"],
                           granted_by="the operator", reason="superseded")
    assert trace_events(tool, EventType.JOB_CANCEL_GRANTED) == [], \
        "nothing was written to the trace, so nothing can be cancelled with it"
    (tmp_path / "gate").write_text("open")


def test_concurrent_calls_on_one_tool_lose_no_record(kernel, tmp_path):
    tool = local_tool(tmp_path)
    _, component = admitted(kernel, tool, tmp_path)
    envelope = kernel.policy.envelope()
    (tmp_path / "gate").write_text("open")
    errors: list = []

    def submit(i):
        try:
            kernel.broker.call_tool(component, {"gate": str(tmp_path / "gate"),
                                                "_psh_idempotency_key": f"run:t{i}"}, envelope)
        except ResultPending:
            pass
        except Exception as exc:  # noqa: BLE001 - reported below
            errors.append(exc)

    threads = [threading.Thread(target=submit, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert len(trace_events(tool, EventType.JOB_SUBMITTED)) == 4, "every submission recorded"
    assert len(open_jobs(tool.trace).jobs) == 4


def test_an_unreadable_trace_is_refused_not_started_afresh(kernel, tmp_path):
    tool = local_tool(tmp_path)
    _, component = admitted(kernel, tool, tmp_path)
    tool.trace.parent.mkdir(parents=True, exist_ok=True)
    tool.trace.write_text("{ the record of a job still running somewhere", encoding="utf-8")
    with pytest.raises(ContractViolation, match="could not be used"):
        kernel.broker.call_tool(component, {"gate": "g"}, kernel.policy.envelope())
    assert tool.trace.read_text(encoding="utf-8").startswith("{ the record"), "not overwritten"
    assert not tool.executor.root.exists() or not any(tool.executor.root.iterdir())


# ============================================================ the HTTP job service

@pytest.fixture
def job_service(request):
    """The HTTP job-service fixture of ``test_compute_jobs``: (base URL, service state)."""
    return request.getfixturevalue("service")


def service_tool(url, tmp_path) -> JobTool:
    return JobTool(service_executor(url),
                   JobDefinition(arguments=("behaviour",),
                                 artefacts=(ArtefactSpec("model", "model.cif"),
                                            ArtefactSpec("log", "run.log", required=False))),
                   trace=tmp_path / "trace" / "service.json",
                   collect_dir=tmp_path / "collected")


def loopback_policy_kernel() -> PolicyKernel:
    """The trusted profiles, plus one that reaches the loopback fixture service."""
    test = PermissionProfile(name="job-service-tests", allow_network=True,
                             allowed_hosts=frozenset({"127.0.0.1"}),
                             allow_filesystem_write=frozenset({"${tmp}"}))
    return PolicyKernel(profiles={**PROFILES, test.name: test})


def service_component(kernel, url, tmp_path):
    tool = service_tool(url, tmp_path)
    _, component = admitted(kernel, tool, tmp_path, backend="http", subprocess=False,
                            network=("127.0.0.1",), runtime_kernel=loopback_policy_kernel(),
                            spec=AgentSpec(name="jobs",
                                           permission_profile="job-service-tests"))
    return tool, component


def test_a_service_job_is_pending_then_collected_and_digested(kernel, job_service, tmp_path):
    url, state = job_service
    tool, component = service_component(kernel, url, tmp_path)
    assert component.manifest.destinations == (Destination.PUBLIC_REMOTE,)
    envelope = kernel.policy.envelope()
    submitted = pending_of(kernel, component, {"behaviour": "complete",
                                               "_psh_idempotency_key": "run:fold"}, envelope)
    assert submitted.reference["location"] == url
    job = submitted.reference["job_id"]
    running = pending_of(kernel, component, {"operation": "collect", "job": job}, envelope)
    assert running.reference_digest == submitted.reference_digest
    result = kernel.broker.call_tool(component, {"operation": "collect", "job": job}, envelope)
    model = result.value["artefacts"]["model"]
    assert model["sha256"] == hashlib.sha256(MODEL).hexdigest() and model["size"] == len(MODEL)
    assert Path(model["path"]).read_bytes() == MODEL
    assert Path(model["path"]).parent == tmp_path / "collected" / job
    assert len(state.jobs) == 1, "one POST"


@pytest.mark.parametrize("behaviour,words", [
    ("fail", "segmentation fault in the sampler"),
    ("corrupt", "does not match the digest the executor declared"),
    ("missing", "required artefact 'model' (model.cif) is missing"),
])
def test_a_service_job_that_did_not_deliver_is_not_a_success(kernel, job_service, tmp_path,
                                                             behaviour, words):
    url, _ = job_service
    _, component = service_component(kernel, url, tmp_path)
    envelope = kernel.policy.envelope()
    job = pending_of(kernel, component, {"behaviour": behaviour}, envelope).reference["job_id"]
    pending_of(kernel, component, {"operation": "collect", "job": job}, envelope)
    with pytest.raises(ContractViolation, match=re.escape(words)):
        kernel.broker.call_tool(component, {"operation": "collect", "job": job}, envelope)


def test_a_state_the_client_does_not_know_stays_pending_and_unknown(kernel, job_service,
                                                                      tmp_path):
    url, _ = job_service
    _, component = service_component(kernel, url, tmp_path)
    envelope = kernel.policy.envelope()
    job = pending_of(kernel, component, {"behaviour": "weird"}, envelope).reference["job_id"]
    pending_of(kernel, component, {"operation": "collect", "job": job}, envelope)
    unknown = pending_of(kernel, component, {"operation": "collect", "job": job}, envelope)
    assert "'paused'" in unknown.reason, "never read as completed, never as failed"


def test_a_service_job_is_cancelled_only_with_its_grant(kernel, job_service, tmp_path):
    url, state = job_service
    _, component = service_component(kernel, url, tmp_path)
    envelope = kernel.policy.envelope()
    job = pending_of(kernel, component, {"behaviour": "slow"}, envelope).reference["job_id"]
    with pytest.raises(PolicyDenied, match="no grant"):
        kernel.broker.call_tool(component, {"operation": "cancel", "job": job}, envelope)
    assert state.deletes == 0, "a refused cancellation never reaches the service"
    grant = grant_cancellation(kernel, component, job, granted_by="a reviewer",
                               reason="superseded")
    done = kernel.broker.call_tool(component, {"operation": "cancel", "job": job,
                                               "grant": grant}, envelope)
    assert done.value["cancelled"] is True and state.deletes == 1
    stubborn = pending_of(kernel, component, {"behaviour": "stubborn"},
                          envelope).reference["job_id"]
    asked = kernel.broker.call_tool(component, {
        "operation": "cancel", "job": stubborn,
        "grant": grant_cancellation(kernel, component, stubborn, granted_by="a reviewer",
                                    reason="stop")}, envelope)
    assert asked.value["cancelled"] is False and "not cancelled" in asked.value["detail"]


# ===================================================================== the loop

def loop_service(kernel, component, payload, *, store, ledger):
    """A governed run with one task: the job tool, under the authority it needs."""
    registry = CapabilityRegistry()
    registry.register(component)
    plan = Plan(objective="predict the structure", produced_by="test",
                tasks=(PlanTask(task_id="fold", objective="fold the sequence",
                                kind=TaskKind.TOOL, component_id=component.manifest.id,
                                payload=dict(payload), autonomy=Autonomy.ACT,
                                max_risk=RiskTier.R2_CONSEQUENTIAL,
                                # PSH's fallback classifier reads a long temporary path as
                                # uninspectable (SENSITIVE); the local tool accepts it
                                max_label=Sensitivity.PHI),),
                completion_criteria=(Criterion(description="folded", kind="task"),))
    return ResearchRunService(kernel, planner=StaticPlanner(plan), registry=registry,
                              checkpoints=store, operations=ledger)


@pytest.mark.parametrize("isolate", [False, True], ids=["in_process", "isolated"])
def test_a_governed_loop_waits_for_its_job_and_resumes_to_collect_it(kernel, tmp_path,
                                                                     isolate):
    base = tmp_path / "ws" if isolate else tmp_path
    tool = local_tool(base)
    _, component = admitted(kernel, tool, tmp_path, isolate=isolate)
    gate = tmp_path / "gate"
    store, ledger = CheckpointStore(tmp_path / "ckpt"), OperationLedger(tmp_path / "ops.db")
    research = loop_service(kernel, component, {"gate": str(gate)}, store=store, ledger=ledger)

    first = research.run("predict the structure")
    assert first.status == "not_completed" and first.refusal_kind == "awaiting"
    assert first.released_output is None, "a submission is never released"
    loop = research.last_loop_result
    (record,) = ledger.pending(loop.run_id)
    assert record.result_digest == loop.pending["fold"].reference_digest
    assert chain(kernel, "loop_task_waiting")

    gate.write_text("open")
    finished(tool.executor, JobRef.from_dict(trace_events(
        tool, EventType.JOB_SUBMITTED)[0].detail["job"]))
    released = research.resume(store.latest_for(loop.loop_id))
    assert released.status == "released", released.summary()
    digest = hashlib.sha256(b"data_model\n").hexdigest()
    assert digest in released.released_output, "the artefact digest is the deliverable"
    assert len(list(tool.executor.root.iterdir())) == 1, "the job was submitted once"
    assert ledger.get(record.key).state is OperationState.SUCCEEDED
    if isolate:
        assert kernel.broker.stats()["isolated_tool_calls"] >= 2 and component.calls == 0
    assert kernel.events.verify()
    ledger.close()


# ===================================================================== restarts

def test_a_restart_finds_the_open_job_and_resumes_collecting_it(tmp_path, monkeypatch):
    state = tmp_path / "k"
    tool = local_tool(tmp_path)
    gate = tmp_path / "gate"
    payload = {"gate": str(gate)}

    # the first process: the executor accepts the job, and the process dies before the
    # loop can record the call as pending
    first = TrustedKernel(PSHConfig(state_dir=state).ensure_dirs(), policy=policy())
    _, component = admitted(first, tool, tmp_path)

    def die(result):
        raise SystemExit("the harness died here")

    monkeypatch.setattr(component, "value_of", die)
    ledger = OperationLedger(tmp_path / "ops.db")
    with pytest.raises(SystemExit):
        loop_service(first, component, payload, store=CheckpointStore(tmp_path / "c"),
                     ledger=ledger).run("predict the structure")
    (in_doubt,) = ledger.in_doubt()
    run_id = in_doubt.run_id
    assert len(open_jobs(tool.trace).jobs) == 1, "the trace holds the job"
    ledger.close()
    first.close()

    # the restart: a new kernel on the same state, the same tool and trace
    kernel = TrustedKernel(PSHConfig(state_dir=state).ensure_dirs(), policy=policy())
    _, component = admitted(kernel, tool, tmp_path)
    ledger = OperationLedger(tmp_path / "ops.db")
    store = CheckpointStore(tmp_path / "c")
    envelope = dataclasses.replace(kernel.policy.envelope(), run_id=run_id)
    research = loop_service(kernel, component, payload, store=store, ledger=ledger)

    refused = research.run("predict the structure", envelope=envelope)
    assert refused.status == "not_completed", "in doubt, the loop does not guess"
    assert len(list(tool.executor.root.iterdir())) == 1, "and starts no second job"

    found = reconcile(ledger, component, run_id=run_id, audit=kernel.audit)
    assert found.pending == (in_doubt.key,) and found.unconfirmed == ()
    assert ledger.get(in_doubt.key).state is OperationState.PENDING
    assert chain(kernel, "job_reconciled")

    waiting = research.run("predict the structure", envelope=envelope)
    assert waiting.refusal_kind == "awaiting", waiting.summary()
    gate.write_text("open")
    finished(tool.executor, open_jobs(tool.trace).jobs[0])
    released = research.resume(store.latest_for(research.last_loop_result.loop_id))
    assert released.status == "released", released.summary()
    (job_dir,) = list(tool.executor.root.iterdir())
    assert (job_dir / "out" / "runs.txt").read_text() == "ran\n", "it ran once"
    assert open_jobs(tool.trace).jobs == ()
    assert kernel.events.verify()
    ledger.close()
    kernel.close()


def test_a_restart_after_the_job_was_collected_collects_the_same_job_again(tmp_path,
                                                                           monkeypatch):
    """The process dies after the tool collected the job and before the loop recorded the
    value. The trace calls the job finished, so ``open_jobs`` no longer lists it; the
    ledger still holds the call as pending, and that is what the resumed loop goes by."""
    state = tmp_path / "k"
    tool = local_tool(tmp_path)
    gate = tmp_path / "gate"
    payload = {"gate": str(gate)}
    first = TrustedKernel(PSHConfig(state_dir=state).ensure_dirs(), policy=policy())
    _, component = admitted(first, tool, tmp_path)
    ledger = OperationLedger(tmp_path / "ops.db")
    store = CheckpointStore(tmp_path / "c")
    research = loop_service(first, component, payload, store=store, ledger=ledger)
    waiting = research.run("predict the structure")
    assert waiting.refusal_kind == "awaiting", waiting.summary()
    loop = research.last_loop_result
    (record,) = ledger.pending(loop.run_id)
    gate.write_text("open")
    finished(tool.executor, open_jobs(tool.trace).jobs[0])

    def die(result):
        raise SystemExit("the harness died here")

    monkeypatch.setattr(component, "value_of", die)
    with pytest.raises(SystemExit):
        research.resume(store.latest_for(loop.loop_id))
    assert open_jobs(tool.trace).jobs == (), "the trace records the job as collected"
    assert ledger.get(record.key).state is OperationState.PENDING, "the ledger never heard"
    ledger.close()
    first.close()

    kernel = TrustedKernel(PSHConfig(state_dir=state).ensure_dirs(), policy=policy())
    _, component = admitted(kernel, tool, tmp_path)
    ledger = OperationLedger(tmp_path / "ops.db")
    found = reconcile(ledger, component, run_id=loop.run_id)
    assert found.pending == () and found.conflicts == (), "nothing is in doubt"
    research = loop_service(kernel, component, payload, store=store, ledger=ledger)
    released = research.resume(store.latest_for(loop.loop_id))
    assert released.status == "released", released.summary()
    (job_dir,) = list(tool.executor.root.iterdir())
    assert (job_dir / "out" / "runs.txt").read_text() == "ran\n", "it ran once"
    assert ledger.get(record.key).state is OperationState.SUCCEEDED
    assert kernel.events.verify()
    ledger.close()
    kernel.close()


def test_a_request_never_confirmed_is_reported_and_resubmitted_only_by_its_call(
        kernel, tmp_path):
    class Crashing(LocalSubprocessJobs):
        def submit(self, spec, *, submission_id):
            super().submit(spec, submission_id=submission_id)
            raise SystemExit("the harness died here")

    gate = tmp_path / "gate"
    crashing = local_tool(tmp_path, executor=Crashing(tmp_path / "jobs"))
    _, component = admitted(kernel, crashing, tmp_path)
    envelope = kernel.policy.envelope()
    payload = {"gate": str(gate), "_psh_idempotency_key": f"{envelope.run_id}:fold"}
    with pytest.raises(SystemExit):
        kernel.broker.call_tool(component, payload, envelope)
    ledger = OperationLedger(tmp_path / "ops.db")
    found = reconcile(ledger, component, run_id=envelope.run_id)
    (request,) = found.unconfirmed
    assert request["idempotency_key"] == payload["_psh_idempotency_key"]
    assert found.pending == (), "a job nobody confirmed is not assumed to exist"

    tool = local_tool(tmp_path)                       # the same root and trace, restarted
    _, again = admitted(kernel, tool, tmp_path)
    pending = pending_of(kernel, again, payload, envelope)
    assert pending.reference["submission_id"] == request["submission_id"]
    assert len(list(tool.executor.root.iterdir())) == 1, "the request's own job, found"
    gate.write_text("open")
    ledger.close()


# ===================================================================== isolated

def test_an_isolated_child_submits_collects_and_needs_the_grant_to_cancel(kernel, tmp_path):
    tool = local_tool(tmp_path / "ws")
    bridge, component = admitted(kernel, tool, tmp_path, isolate=True)
    manifest = component.manifest
    assert manifest.backend == "subprocess" and "--jobs" in manifest.entrypoint
    config = kernel.config.state_dir / "bioscience" / f"{manifest.id}.jobs.json"
    assert JobTool.load(config).digest == tool.digest
    envelope = kernel.policy.envelope()
    gate = tmp_path / "gate"

    submitted = pending_of(kernel, component, {"gate": str(gate)}, envelope)
    assert chain(kernel, "tool_call_pending")[0].detail["execution"] == "isolated"
    assert component.calls == 0, "the parent never ran it"
    gate.write_text("open")
    finished(tool.executor, JobRef.from_dict(submitted.reference))
    result = kernel.broker.call_tool(component, {"gate": str(gate),
                                                 "_psh_pending": submitted.reference_digest},
                                     envelope)
    assert result.value["artefacts"]["model"]["sha256"] == \
        hashlib.sha256(b"data_model\n").hexdigest()

    held = pending_of(kernel, component, {"gate": str(tmp_path / "closed")}, envelope)
    job = held.reference["job_id"]
    with pytest.raises(ContractViolation, match="DENIED.*no grant"):
        kernel.broker.call_tool(component, {"operation": "cancel", "job": job}, envelope)
    assert tool.executor.status(JobRef.from_dict(held.reference)).state is JobState.RUNNING
    grant = grant_cancellation(kernel, component, job, granted_by="the operator",
                               reason="superseded")
    done = kernel.broker.call_tool(component, {"operation": "cancel", "job": job,
                                               "grant": grant}, envelope)
    assert done.value["cancelled"] is True
    assert kernel.broker.stats()["isolated_tool_calls"] == 5


def test_an_isolated_child_rules_under_the_bridges_permission_profile(kernel, tmp_path):
    """The child was started without the bridge's profile and ruled under its own default
    (``biomedical-research``), so a bridge configured for offline analysis started jobs
    from its children. The profile now travels with the entrypoint."""
    tool = local_tool(tmp_path / "ws")
    offline = AgentSpec(name="offline", permission_profile="offline-analysis")
    _, component = admitted(kernel, tool, tmp_path, isolate=True, spec=offline)
    assert "--profile offline-analysis" in component.manifest.entrypoint
    with pytest.raises(ContractViolation, match="DENIED.*forbids subprocess"):
        kernel.broker.call_tool(component, {"gate": "g"}, kernel.policy.envelope())
    assert not tool.executor.root.exists() or not any(tool.executor.root.iterdir())


def test_an_isolated_child_refuses_a_job_configuration_changed_after_admission(
        kernel, tmp_path):
    tool = local_tool(tmp_path / "ws")
    _, component = admitted(kernel, tool, tmp_path, isolate=True)
    config = kernel.config.state_dir / "bioscience" / f"{component.manifest.id}.jobs.json"
    doc = json.loads(config.read_text())
    doc["definition"]["argv"][-1] = "{gate} --and-something-else"
    config.write_text(json.dumps(doc))
    with pytest.raises(ContractViolation, match="admitted under"):
        kernel.broker.call_tool(component, {"gate": "g"}, kernel.policy.envelope())
    assert not tool.executor.root.exists() or not any(tool.executor.root.iterdir())


def test_an_isolated_child_cannot_reach_the_loopback_fixture_service(kernel, job_service,
                                                                     tmp_path):
    """Not a verification of the isolated HTTP path: the child runs under the shipped
    profile and behind the kernel's egress proxy, and both refuse a loopback service. The
    refusal is a refusal — no job is started and none is reported."""
    url, state = job_service
    tool = JobTool(service_executor(url),
                   JobDefinition(arguments=("behaviour",),
                                 artefacts=(ArtefactSpec("model", "model.cif"),)),
                   trace=tmp_path / "ws" / "trace" / "service.json")
    _, component = admitted(kernel, tool, tmp_path, isolate=True, backend="http",
                            subprocess=False, network=("127.0.0.1",))
    with pytest.raises(ContractViolation, match="DENIED"):
        kernel.broker.call_tool(component, {"behaviour": "complete"}, kernel.policy.envelope())
    assert state.jobs == {}


# ====================================================== governed skill operations

def test_a_pending_operation_blocks_the_release_of_a_governed_skill_run(kernel, monkeypatch):
    """A governed skill's operations are one-shot calls, and none of them is a job tool. If
    one ever answered with pending work it would be recorded RUNNING, which blocks release
    (ART118), so a skill cannot release a submission as its result. The component's answer
    is the only thing replaced here: PSH's broker records and raises as it would."""
    from types import SimpleNamespace

    from psh.contracts import PendingResult

    from bioagent.operations import RCSB_ENTRY, OperationBroker, OperationError, fetch_bytes
    from bioagent.psh.component import BridgedComponent

    reference = {"job_id": "job1", "executor": "fake"}

    def answers_pending(self, payload, envelope=None):
        self.last_status = ExecutionStatus.RUNNING
        return PendingResult(reference, "submitted; the job is queued")

    monkeypatch.setattr(BridgedComponent, "invoke", answers_pending)
    spec = SimpleNamespace(id="probe", permissions=SimpleNamespace(
        network=("files.rcsb.org",), subprocess=False))
    broker = OperationBroker(spec, kernel, run_id="run_probe", anchor="0" * 64)
    with broker.governing():
        with pytest.raises(OperationError) as raised:
            broker.invoke(RCSB_ENTRY, fetch_bytes,
                          {"url": "https://files.rcsb.org/download/1UBQ.pdb"})
    entry = raised.value.evidence
    assert entry.status == "RUNNING" and entry.recorded
    assert "ResultPending" in entry.reason
    assert content_hash(reference)[:16] in entry.reason, "the reason names the work"
    assert entry.output_sha256 == "", "there is no output to digest"
    (pending,) = chain(kernel, "tool_call_pending")
    assert pending.detail["reference_sha256"] == content_hash(reference)
    (blocker,) = broker.release_blockers()
    assert "ended RUNNING" in blocker
