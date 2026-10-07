"""The long-job protocol: submit, status, collect, cancel, and what each may claim.

Two executors: supervised local processes, and a generic HTTP job service, here a local
``http.server`` fixture implementing the protocol (POST submit, GET status, GET
artefacts, DELETE cancel) with jobs that complete, fail, hang, lie about their digests,
lose an artefact, or report a state the client does not know.
"""

from __future__ import annotations

import hashlib
import http.server
import json
import os
import signal
import sys
import threading
import time
import urllib.parse
from pathlib import Path

import pytest

from bioagent.backends.http import HTTPBackend
from bioagent.backends.jobs import (ArtefactSpec, CancelGrant, JobController, JobRef,
                                    JobSpec, JobState, JobSubmitError, HTTPJobService,
                                    LocalSubprocessJobs, open_jobs)
from bioagent.runtime.events import EventType
from bioagent.status import ExecutionStatus

MODEL = b"data_model\n# a model the service computed\n"


# ------------------------------------------------------------- the job service
class _Service:
    def __init__(self) -> None:
        self.jobs: dict[str, dict] = {}
        self.by_submission: dict[str, str] = {}
        self.deletes = 0
        self.lock = threading.Lock()


class _Handler(http.server.BaseHTTPRequestHandler):
    def _send(self, code: int, doc=None, raw: bytes | None = None) -> None:
        body = raw if raw is not None else json.dumps(doc).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/octet-stream" if raw is not None
                         else "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _job(self):
        parts = [urllib.parse.unquote(p) for p in self.path.strip("/").split("/")]
        job = self.server.state.jobs.get(parts[1]) if len(parts) > 1 else None
        return parts, job

    def do_POST(self):  # noqa: N802 - http.server API
        doc = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        state = self.server.state
        with state.lock:
            if doc["submission_id"] in state.by_submission:
                jid = state.by_submission[doc["submission_id"]]
                return self._send(200, {"job_id": jid, "state": state.jobs[jid]["state"]})
            jid = f"job{len(state.jobs) + 1}"
            state.jobs[jid] = {"behaviour": doc["task"].get("behaviour", "complete"),
                               "state": "queued", "polls": 0, "paths": doc["artefacts"]}
            state.by_submission[doc["submission_id"]] = jid
        self._send(201, {"job_id": jid, "state": "queued"})

    def do_GET(self):  # noqa: N802
        parts, job = self._job()
        if job is None:
            return self._send(404, {"detail": "no such job"})
        if len(parts) == 4 and parts[2] == "artefacts":
            if job["behaviour"] == "missing" or parts[3] not in job["paths"]:
                return self._send(404, {"detail": "no such artefact"})
            return self._send(200, raw=MODEL)
        behaviour = job["behaviour"]
        job["polls"] += 1
        if job["state"] in ("queued", "running") and behaviour != "slow":
            job["state"] = "running" if job["polls"] < 2 else (
                "failed" if behaviour == "fail" else
                "paused" if behaviour == "weird" else "completed")
        elif job["state"] == "queued":
            job["state"] = "running"
        doc = {"job_id": parts[1], "state": job["state"]}
        if job["state"] == "failed":
            doc["detail"] = "segmentation fault in the sampler"
        if job["state"] == "completed":
            digest = hashlib.sha256(b"something else" if behaviour == "corrupt"
                                    else MODEL).hexdigest()
            doc["artefacts"] = [{"name": p, "sha256": digest, "size": len(MODEL)}
                                for p in job["paths"]]
        self._send(200, doc)

    def do_DELETE(self):  # noqa: N802
        parts, job = self._job()
        self.server.state.deletes += 1
        if job is None:
            return self._send(404, {"detail": "no such job"})
        if job["state"] in ("completed", "failed", "cancelled"):
            return self._send(409, {"detail": "already finished"})
        if job["behaviour"] == "stubborn":
            return self._send(202, {"state": "cancelling"})
        job["state"] = "cancelled"
        self._send(200, {"state": "cancelled"})

    def log_message(self, *args):
        pass


@pytest.fixture
def service(monkeypatch):
    for var in ("http_proxy", "HTTP_PROXY", "https_proxy", "HTTPS_PROXY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    httpd.state = _Service()
    thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.05},
                              daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", httpd.state
    httpd.shutdown()


def executor(url: str) -> HTTPJobService:
    return HTTPJobService(url, http=HTTPBackend(max_retries=1, timeout_s=5,
                                                rates={"127.0.0.1": 1000.0}))


def service_spec(behaviour: str = "complete") -> JobSpec:
    return JobSpec("svc.tool.fold", payload={"behaviour": behaviour},
                   artefacts=(ArtefactSpec("model", "model.cif"),
                              ArtefactSpec("log", "run.log", required=False)))


# ----------------------------------------------------------------- HTTP service
def test_a_service_job_succeeds_only_once_its_artefacts_are_collected(service, tmp_path):
    url, _ = service
    ctl = JobController(executor(url), collect_dir=tmp_path)
    ref = ctl.submit(service_spec())
    early = ctl.collect(ref)
    assert early.status is ExecutionStatus.RUNNING and early.artefacts == {}
    assert ctl.wait(ref, timeout_s=10, poll_s=0.01).state is JobState.COMPLETED
    done = ctl.collect(ref)
    assert done.status is ExecutionStatus.SUCCEEDED, done.error
    assert done.artefacts["model"].sha256 == hashlib.sha256(MODEL).hexdigest()
    assert done.artefacts["model"].path.read_bytes() == MODEL
    assert done.artefacts["model"].path.parent == tmp_path / ref.job_id
    kinds = [(e.event_type, e.status) for e in ctl.events]
    assert kinds[:2] == [(EventType.JOB_REQUESTED, "REQUESTED"),
                         (EventType.JOB_SUBMITTED, "RUNNING")]
    assert (EventType.JOB_COLLECTED, "SUCCEEDED") in kinds
    assert sum(k == EventType.ARTIFACT_CREATED for k, _ in kinds) == 2
    submitted = ctl.events.of_type(EventType.JOB_SUBMITTED)[0]
    assert submitted.detail["job"]["location"] == url, "the reference is in the run's log"


@pytest.mark.parametrize("behaviour,status,words", [
    ("fail", ExecutionStatus.FAILED, "segmentation fault in the sampler"),
    ("corrupt", ExecutionStatus.FAILED, "does not match the digest the executor declared"),
    ("missing", ExecutionStatus.FAILED, "required artefact 'model' (model.cif) is missing"),
    ("weird", ExecutionStatus.UNAVAILABLE, "is unreachable"),
])
def test_a_service_job_that_did_not_deliver_is_not_a_success(service, tmp_path, behaviour,
                                                             status, words):
    url, _ = service
    outcome = JobController(executor(url), collect_dir=tmp_path).run(
        service_spec(behaviour), timeout_s=1.0, poll_s=0.01)
    assert outcome.status is status
    assert words in outcome.error
    assert not outcome.ok


def test_a_state_the_client_does_not_know_is_never_read_as_completed(service, tmp_path):
    url, _ = service
    ctl = JobController(executor(url), collect_dir=tmp_path)
    ref = ctl.submit(service_spec("weird"))
    ctl.wait(ref, timeout_s=1.0, poll_s=0.01)
    status = ctl.status(ref)
    assert status.state is JobState.UNREACHABLE and "'paused'" in status.detail
    assert ctl.collect(ref).status is ExecutionStatus.UNAVAILABLE


def test_cancelling_needs_a_grant_naming_the_job_and_the_services_confirmation(service):
    url, state = service
    ctl = JobController(executor(url))
    ref = ctl.submit(service_spec("slow"))
    assert ctl.cancel(ref, None).status is ExecutionStatus.DENIED
    other = ctl.cancel(ref, CancelGrant("job99", "a reviewer", "wrong job"))
    assert other.status is ExecutionStatus.DENIED and "names job job99" in other.detail
    assert state.deletes == 0, "a refused cancellation never reaches the service"
    done = ctl.cancel(ref, CancelGrant(ref.job_id, "a reviewer", "superseded"))
    assert done.status is ExecutionStatus.CANCELLED and done.state is JobState.CANCELLED
    assert ctl.collect(ref).status is ExecutionStatus.CANCELLED
    cancelled = ctl.events.of_type(EventType.JOB_CANCELLED)
    assert [e.status for e in cancelled] == ["DENIED", "DENIED", "CANCELLED"]
    assert cancelled[-1].policy_ruling == "granted by a reviewer: superseded"

    stubborn = ctl.submit(service_spec("stubborn"))
    asked = ctl.cancel(stubborn, CancelGrant(stubborn.job_id, "a reviewer", "stop"))
    assert asked.status is ExecutionStatus.RUNNING and "not cancelled" in asked.detail


def test_a_repeated_submission_finds_the_job_it_started(service):
    url, state = service
    svc = executor(url)
    first = svc.submit(service_spec(), submission_id="s-1")
    again = svc.submit(service_spec(), submission_id="s-1")
    assert first.job_id == again.job_id and len(state.jobs) == 1


def test_an_unreachable_or_forgetful_service_is_not_a_failed_job(service, tmp_path):
    url, _ = service
    gone = HTTPJobService("http://127.0.0.1:9", http=HTTPBackend(max_retries=1, timeout_s=2))
    with pytest.raises(JobSubmitError) as refused:
        gone.submit(service_spec(), submission_id="s-2")
    assert refused.value.status is ExecutionStatus.UNAVAILABLE
    ctl = JobController(gone)
    outcome = ctl.run(service_spec(), timeout_s=1)
    assert outcome.status is ExecutionStatus.UNAVAILABLE and outcome.ref is None
    assert open_jobs(ctl.events).unconfirmed == (), "the refusal settles the request"
    ref = JobRef("job1", "http-job-service", "http://127.0.0.1:9", "svc.tool.fold", "", "s",
                 0.0)
    assert gone.status(ref).state is JobState.UNREACHABLE
    forgotten = JobController(executor(url)).collect(
        JobRef("nope", "http-job-service", url, "svc.tool.fold", "", "s", 0.0))
    assert forgotten.status is ExecutionStatus.FAILED
    assert "does not know job" in forgotten.error


# ------------------------------------------------------------------ local jobs
def local_spec(code: str, **kw) -> JobSpec:
    return JobSpec("t.tool.local", argv=(sys.executable, "-c", code, "{output}"), **kw)


WRITE = ("import json, os, sys\n"
         "open(os.path.join(sys.argv[1], 'runs.txt'), 'a').write('ran\\n')\n"
         "json.dump(sorted(os.environ), open(os.path.join(sys.argv[1], 'env.json'), 'w'))\n")


def test_a_local_job_is_supervised_collected_and_kept_from_the_harness_secrets(
        tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET_TOKEN", "do-not-pass")
    ctl = JobController(LocalSubprocessJobs(tmp_path / "jobs"),
                        trace_path=tmp_path / "trace.json")
    outcome = ctl.run(local_spec(WRITE, artefacts=(ArtefactSpec("env", "env.json"),)),
                      timeout_s=30, poll_s=0.02)
    assert outcome.status is ExecutionStatus.SUCCEEDED, outcome.error
    seen = json.loads(outcome.artefacts["env"].path.read_text())
    assert "BIOAGENT_JOB_OUTPUT" in seen and "SECRET_TOKEN" not in seen
    trace = json.loads((tmp_path / "trace.json").read_text())
    assert [e["event_type"] for e in trace["events"]][-2:] == ["JobCollected",
                                                               "ArtifactCreated"]


def test_a_local_job_that_writes_nothing_or_fails_is_not_a_success(tmp_path):
    ctl = JobController(LocalSubprocessJobs(tmp_path / "jobs"))
    silent = ctl.run(local_spec("pass", artefacts=(ArtefactSpec("model", "model.cif"),)),
                     timeout_s=30, poll_s=0.02)
    assert silent.state is JobState.COMPLETED, "the process exited 0 ..."
    assert silent.status is ExecutionStatus.FAILED, "... and the step still failed"
    assert "required artefact 'model' (model.cif) is missing" in silent.error
    crashed = ctl.run(local_spec("import sys; sys.exit('weights not found')"),
                      timeout_s=30, poll_s=0.02)
    assert crashed.status is ExecutionStatus.FAILED and "weights not found" in crashed.error
    rejected = ctl.run(local_spec(WRITE, artefacts=(ArtefactSpec("runs", "runs.txt"),)),
                       timeout_s=30, poll_s=0.02,
                       validators={"runs": lambda p: "not a model"})
    assert rejected.status is ExecutionStatus.FAILED
    assert "artefact 'runs': not a model" in rejected.error


def test_the_executor_enforces_a_jobs_wall_clock_limit(tmp_path):
    ctl = JobController(LocalSubprocessJobs(tmp_path / "jobs"))
    outcome = ctl.run(local_spec("import time; time.sleep(60)", timeout_s=0.5),
                      timeout_s=30, poll_s=0.02)
    assert outcome.status is ExecutionStatus.TIMEOUT and "timed out" in outcome.error


def test_a_job_running_at_the_deadline_is_left_running_and_cancelled_only_with_a_grant(
        tmp_path):
    ctl = JobController(LocalSubprocessJobs(tmp_path / "jobs"),
                        trace_path=tmp_path / "trace.json")
    outcome = ctl.run(local_spec("import time; time.sleep(60)"), timeout_s=0.3, poll_s=0.02)
    assert outcome.status is ExecutionStatus.TIMEOUT and "was not cancelled" in outcome.error
    ref = outcome.ref
    assert ctl.status(ref).state is JobState.RUNNING
    assert open_jobs(tmp_path / "trace.json").jobs == (ref,), "a restart can find it"
    assert ctl.cancel(ref, None).status is ExecutionStatus.DENIED
    assert ctl.status(ref).state is JobState.RUNNING
    done = ctl.cancel(ref, CancelGrant(ref.job_id, "the operator", "superseded"))
    assert done.status is ExecutionStatus.CANCELLED
    assert ctl.collect(ref).status is ExecutionStatus.CANCELLED
    assert open_jobs(tmp_path / "trace.json").jobs == ()


def test_a_crash_between_submission_and_its_record_is_reconciled_without_a_second_run(
        tmp_path):
    class Crashing(LocalSubprocessJobs):
        def submit(self, spec, *, submission_id):
            super().submit(spec, submission_id=submission_id)
            raise SystemExit("the harness died here")

    spec = local_spec(WRITE, artefacts=(ArtefactSpec("runs", "runs.txt"),))
    crashed = JobController(Crashing(tmp_path / "jobs"), trace_path=tmp_path / "trace.json")
    with pytest.raises(SystemExit):
        crashed.submit(spec)
    pending = open_jobs(tmp_path / "trace.json")
    assert pending.jobs == () and len(pending.unconfirmed) == 1
    submission = pending.unconfirmed[0]["submission_id"]

    restarted = JobController(LocalSubprocessJobs(tmp_path / "jobs"))
    ref = restarted.submit(spec, submission_id=submission)
    assert ref.job_id == submission
    restarted.wait(ref, timeout_s=30, poll_s=0.02)
    outcome = restarted.collect(ref)
    assert outcome.status is ExecutionStatus.SUCCEEDED
    assert outcome.artefacts["runs"].path.read_text() == "ran\n", "it ran once"
    with pytest.raises(JobSubmitError, match="already started a different job"):
        restarted.submit(local_spec("pass"), submission_id=submission)


def test_an_idempotency_key_returns_the_job_already_submitted(tmp_path):
    ctl = JobController(LocalSubprocessJobs(tmp_path / "jobs"))
    spec = local_spec("import time; time.sleep(5)")
    first = ctl.submit(spec, idempotency_key="run1:task3")
    assert ctl.submit(spec, idempotency_key="run1:task3") == first
    assert len(ctl.events.of_type(EventType.JOB_REQUESTED)) == 1
    ctl.cancel(first, CancelGrant(first.job_id, "tests", "cleanup"))


def test_a_supervisor_killed_without_a_cancellation_leaves_a_lost_job(tmp_path):
    ctl = JobController(LocalSubprocessJobs(tmp_path / "jobs"))
    ref = ctl.submit(local_spec("import time; time.sleep(60)"))
    started = json.loads((Path(ref.location) / "started.json").read_text())
    os.killpg(started["pgid"], signal.SIGKILL)
    deadline = time.monotonic() + 10
    while ctl.status(ref).state is JobState.RUNNING and time.monotonic() < deadline:
        time.sleep(0.05)
    assert ctl.status(ref).state is JobState.LOST
    outcome = ctl.collect(ref)
    assert outcome.status is ExecutionStatus.FAILED and "is unknown" in outcome.error
