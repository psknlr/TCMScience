"""Long jobs: submit, observe, collect, cancel — and why a submission is not a result.

A GPU structure prediction, a molecular-dynamics run or an nf-core pipeline takes minutes
to days. Treated like any other tool call it goes wrong in one of two ways. Either the
caller blocks until a timeout and reports TIMEOUT for a job that is still running, or it
reports the *submission* as the result: a job id came back, so the step "succeeded", and
nothing downstream checks that the job finished or that what it wrote exists. The second
is the v1 false-success defect in a new form. An accepted request is not executed work.

So a long job is four calls, each with one meaning:

``submit``   the executor accepted the work. The step is RUNNING. The request is recorded
             in the run's event log before the executor is asked (``JobRequested``) and
             the executor's reference after it answers (``JobSubmitted``), so a crash at
             either point leaves something a restart can reconcile (``open_jobs``).
``status``   what the executor says now. ``completed`` is the executor's claim about its
             own work, not the step's success.
``collect``  fetch every declared artefact, digest it and validate it. This is the only
             place a step becomes SUCCEEDED, and only when every required artefact is
             present, non-empty, matches the digest the executor declared, and passes its
             validator.
``cancel``   only with a ``CancelGrant`` naming the job. A cancellation the executor does
             not confirm is reported as requested, not as done.

Two executors: ``LocalSubprocessJobs`` (a supervised local process, for tests and a single
workstation) and ``HTTPJobService`` (a generic service: POST submit, GET status, GET
artefacts, DELETE cancel). ``JobController`` binds either to a run's ``EventLog``.
"""

from __future__ import annotations

import abc
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Mapping

from ..runtime.events import EventLog, EventType, content_hash
from ..status import ExecutionStatus

__all__ = ["JobState", "ArtefactSpec", "JobSpec", "JobRef", "JobStatus", "Artefact",
           "JobOutcome", "CancelGrant", "CancelOutcome", "JobSubmitError", "JobFetchError",
           "JobExecutor", "LocalSubprocessJobs", "HTTPJobService", "JobController",
           "OpenJobs", "open_jobs", "OUTPUT_TOKEN"]

#: Placeholder in a local job's argv for the directory the job writes its artefacts to.
OUTPUT_TOKEN = "{output}"

_ID = re.compile(r"[A-Za-z0-9_.-]{1,96}")


class JobState(str, Enum):
    """The executor's view of a job. Not an ExecutionStatus: none of these is a result."""

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"       # the executor reports success; nothing validated yet
    FAILED = "failed"
    CANCELLED = "cancelled"       # confirmed by the executor
    LOST = "lost"                 # the executor has no outcome for it
    UNREACHABLE = "unreachable"   # the executor could not be asked; the state is unknown

    @property
    def finished(self) -> bool:
        return self in (JobState.COMPLETED, JobState.FAILED, JobState.CANCELLED,
                        JobState.LOST)


class JobSubmitError(RuntimeError):
    """The executor did not accept the job. ``status`` says whether anything may have run."""

    def __init__(self, message: str, *, status: ExecutionStatus = ExecutionStatus.FAILED):
        super().__init__(message)
        self.status = status


class JobFetchError(RuntimeError):
    """An artefact could not be retrieved. ``transient`` when retrying later may work."""

    def __init__(self, message: str, *, transient: bool = False) -> None:
        super().__init__(message)
        self.transient = transient


@dataclass(frozen=True)
class ArtefactSpec:
    """One file a job must leave behind, by a name the step uses."""

    name: str
    path: str                    # under the job's output directory, or the service's name
    required: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "path": self.path, "required": self.required}


@dataclass(frozen=True)
class JobSpec:
    """What to run. ``argv`` for a local executor, ``payload`` for a service."""

    component_id: str
    argv: tuple[str, ...] = ()
    payload: Mapping[str, Any] = field(default_factory=dict)
    artefacts: tuple[ArtefactSpec, ...] = ()
    env: Mapping[str, str] = field(default_factory=dict)
    cwd: str = ""
    timeout_s: float = 0.0       # the executor's wall-clock limit for the job; 0 = none

    def digest(self) -> str:
        return content_hash({"component_id": self.component_id, "argv": list(self.argv),
                             "payload": dict(self.payload),
                             "artefacts": [a.to_dict() for a in self.artefacts],
                             "env": dict(self.env), "cwd": self.cwd,
                             "timeout_s": self.timeout_s})


@dataclass(frozen=True)
class JobRef:
    """Everything needed to find, collect or cancel a job later, from another process."""

    job_id: str
    executor: str
    location: str                # a job directory, or a service's base URL
    component_id: str
    spec_digest: str
    submission_id: str
    submitted_at: float
    artefacts: tuple[ArtefactSpec, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {"job_id": self.job_id, "executor": self.executor, "location": self.location,
                "component_id": self.component_id, "spec_digest": self.spec_digest,
                "submission_id": self.submission_id, "submitted_at": self.submitted_at,
                "artefacts": [a.to_dict() for a in self.artefacts]}

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "JobRef":
        return cls(job_id=str(d["job_id"]), executor=str(d["executor"]),
                   location=str(d["location"]), component_id=str(d.get("component_id", "")),
                   spec_digest=str(d.get("spec_digest", "")),
                   submission_id=str(d.get("submission_id", "")),
                   submitted_at=float(d.get("submitted_at") or 0.0),
                   artefacts=tuple(ArtefactSpec(str(a["name"]), str(a["path"]),
                                                bool(a.get("required", True)))
                                   for a in d.get("artefacts") or ()))


@dataclass(frozen=True)
class JobStatus:
    state: JobState
    detail: str = ""
    exit_code: int | None = None
    timed_out: bool = False
    #: Digests the executor declares, by artefact path: {"sha256": ..., "size": ...}
    declared: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)


@dataclass(frozen=True)
class Artefact:
    name: str
    path: Path
    sha256: str
    size: int


@dataclass
class JobOutcome:
    """A step's result. SUCCEEDED only from ``collect``, after every artefact validated."""

    ref: JobRef | None
    status: ExecutionStatus
    state: JobState | None
    artefacts: dict[str, Artefact] = field(default_factory=dict)
    error: str | None = None
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status is ExecutionStatus.SUCCEEDED


@dataclass(frozen=True)
class CancelGrant:
    """Permission to cancel one job, from someone, for a reason. A grant names its job, so
    a blanket "yes" given for one job cannot be spent on another."""

    job_id: str
    granted_by: str
    reason: str


@dataclass(frozen=True)
class CancelOutcome:
    """What a cancellation attempt did. ``status`` describes the attempt, not the job:
    CANCELLED (confirmed), DENIED (no grant), RUNNING (requested, not yet confirmed),
    FAILED (the job had already finished or the executor refused), UNAVAILABLE."""

    job_id: str
    status: ExecutionStatus
    state: JobState | None
    detail: str


def _sha256(path: Path) -> tuple[str, int]:
    h, n = hashlib.sha256(), 0
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
            n += len(block)
    return h.hexdigest(), n


def _write_json(path: Path, doc: Any) -> None:
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(doc, indent=2, default=str), encoding="utf-8")
    os.replace(tmp, path)


class JobExecutor(abc.ABC):
    """Where long jobs run. Every method answers for one job reference."""

    name: str = "executor"

    @abc.abstractmethod
    def submit(self, spec: JobSpec, *, submission_id: str) -> JobRef:
        """Start the job, or return the job an earlier submission with this id started."""

    @abc.abstractmethod
    def status(self, ref: JobRef) -> JobStatus:
        """The executor's current view. Never raises for an unreachable executor."""

    @abc.abstractmethod
    def fetch(self, ref: JobRef, artefact: ArtefactSpec, dest_dir: Path) -> Path | None:
        """A local path holding the artefact, or None when the job did not write it."""

    @abc.abstractmethod
    def cancel(self, ref: JobRef, *, reason: str) -> JobStatus:
        """Ask the executor to stop the job; the returned state says whether it did."""


# ------------------------------------------------------------------ local executor

#: Runs in its own session, holds an exclusive lock on <job>/lock for as long as it lives,
#: starts the command, enforces the wall-clock limit and records the exit status. The lock
#: is how ``status`` tells "running" from "gone" without trusting a PID that the operating
#: system may since have given to another process.
_SUPERVISOR = r'''
import fcntl, json, os, subprocess, sys, time

def write(path, doc):
    with open(path + ".tmp", "w", encoding="utf-8") as fh:
        json.dump(doc, fh)
    os.replace(path + ".tmp", path)

job = sys.argv[1]
with open(os.path.join(job, "job.json"), encoding="utf-8") as fh:
    spec = json.load(fh)
lock = open(os.path.join(job, "lock"), "w")
fcntl.flock(lock, fcntl.LOCK_EX)
write(os.path.join(job, "started.json"),
      {"pid": os.getpid(), "pgid": os.getpgid(0), "at": time.time()})
out = open(os.path.join(job, "stdout.log"), "wb")
err = open(os.path.join(job, "stderr.log"), "wb")
try:
    proc = subprocess.Popen(spec["argv"], cwd=spec.get("cwd") or None, env=spec["env"],
                            stdin=subprocess.DEVNULL, stdout=out, stderr=err)
except OSError as exc:
    write(os.path.join(job, "exit.json"), {"returncode": None, "timed_out": False,
          "error": "could not start: %s" % exc, "finished_at": time.time()})
    sys.exit(0)
timed_out = False
try:
    rc = proc.wait(timeout=spec.get("timeout_s") or None)
except subprocess.TimeoutExpired:
    timed_out = True
    proc.kill()
    rc = proc.wait()
write(os.path.join(job, "exit.json"),
      {"returncode": rc, "timed_out": timed_out, "finished_at": time.time()})
'''

#: Variables a local job inherits from the harness. Everything else (API keys, tokens,
#: PYTHONPATH) stays behind; a job that needs more declares it in ``JobSpec.env``.
INHERITED_ENV = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "CUDA_VISIBLE_DEVICES",
                 "LD_LIBRARY_PATH")


class LocalSubprocessJobs(JobExecutor):
    """Jobs as supervised local processes, one directory each under ``root``.

    ``<root>/<job id>/`` holds ``job.json`` (the reference and the command), ``out/`` (the
    job's output directory, substituted for ``{output}`` in its argv and exported as
    ``BIOAGENT_JOB_OUTPUT``), ``stdout.log``, ``stderr.log`` and ``exit.json``. The job id
    is the submission id, so submitting the same submission twice finds the first job
    instead of starting a second. Needs POSIX process groups and ``flock``.
    """

    name = "local-subprocess"

    def __init__(self, root: str | Path, *, start_timeout_s: float = 15.0,
                 cancel_grace_s: float = 5.0) -> None:
        if os.name != "posix":
            raise RuntimeError("LocalSubprocessJobs needs POSIX process groups and flock")
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.start_timeout_s = start_timeout_s
        self.cancel_grace_s = cancel_grace_s

    def _dir(self, ref: JobRef) -> Path:
        return Path(ref.location)

    def submit(self, spec: JobSpec, *, submission_id: str) -> JobRef:
        if not _ID.fullmatch(submission_id):
            raise JobSubmitError(f"submission id {submission_id!r} is not a plain identifier")
        if not spec.argv:
            raise JobSubmitError(f"{spec.component_id}: a local job needs an argv")
        job_dir = self.root / submission_id
        if (job_dir / "job.json").is_file():
            recorded = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
            ref = JobRef.from_dict(recorded["ref"])
            if ref.spec_digest != spec.digest():
                raise JobSubmitError(f"submission {submission_id} already started a different "
                                     "job; a submission id names one job")
            return ref
        out_dir = job_dir / "out"
        out_dir.mkdir(parents=True, exist_ok=True)    # a crash may have left it, empty
        ref = JobRef(job_id=submission_id, executor=self.name, location=str(job_dir),
                     component_id=spec.component_id, spec_digest=spec.digest(),
                     submission_id=submission_id, submitted_at=time.time(),
                     artefacts=tuple(spec.artefacts))
        env = {k: os.environ[k] for k in INHERITED_ENV if k in os.environ}
        env.update({str(k): str(v) for k, v in spec.env.items()})
        env.update(BIOAGENT_JOB_OUTPUT=str(out_dir), BIOAGENT_JOB_ID=submission_id)
        argv = [a.replace(OUTPUT_TOKEN, str(out_dir)) for a in spec.argv]
        _write_json(job_dir / "job.json", {"ref": ref.to_dict(), "argv": argv,
                                           "cwd": spec.cwd, "env": env,
                                           "timeout_s": spec.timeout_s})
        try:
            subprocess.Popen(  # noqa: S603 - the harness's own interpreter, no shell
                [sys.executable, "-I", "-c", _SUPERVISOR, str(job_dir)],
                start_new_session=True, stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)
        except OSError as exc:
            raise JobSubmitError(f"the supervisor could not be started: {exc}",
                                 status=ExecutionStatus.UNAVAILABLE) from exc
        deadline = time.monotonic() + self.start_timeout_s
        while not (job_dir / "started.json").is_file():
            if time.monotonic() > deadline:
                raise JobSubmitError(f"the supervisor of job {submission_id} did not start "
                                     f"within {self.start_timeout_s:g}s",
                                     status=ExecutionStatus.UNAVAILABLE)
            time.sleep(0.02)
        return ref

    @staticmethod
    def _running(job_dir: Path) -> bool:
        import fcntl

        try:
            fh = open(job_dir / "lock", "a")  # noqa: SIM115 - closed below
        except OSError:
            return False
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        finally:
            fh.close()
        return False

    def status(self, ref: JobRef) -> JobStatus:
        job_dir = self._dir(ref)
        if not (job_dir / "job.json").is_file():
            return JobStatus(JobState.LOST, f"no job directory at {job_dir}")
        exit_file = job_dir / "exit.json"
        if exit_file.is_file():
            doc = json.loads(exit_file.read_text(encoding="utf-8"))
            rc = doc.get("returncode")
            if rc == 0 and not doc.get("timed_out"):
                return JobStatus(JobState.COMPLETED, exit_code=0)
            tail = self._stderr_tail(job_dir)
            detail = doc.get("error") or (f"timed out and was stopped; {tail}"
                                          if doc.get("timed_out") else f"exit {rc}; {tail}")
            return JobStatus(JobState.FAILED, detail.strip("; "), exit_code=rc,
                             timed_out=bool(doc.get("timed_out")))
        if self._running(job_dir):
            return JobStatus(JobState.RUNNING)
        if (job_dir / "cancel.json").is_file():
            return JobStatus(JobState.CANCELLED, "stopped on request")
        return JobStatus(JobState.LOST, "the job's supervisor ended without recording an exit "
                                        "status (killed, or the machine restarted)")

    @staticmethod
    def _stderr_tail(job_dir: Path) -> str:
        try:
            lines = (job_dir / "stderr.log").read_text(encoding="utf-8",
                                                       errors="replace").strip().splitlines()
        except OSError:
            return ""
        return lines[-1][:300] if lines else ""

    def fetch(self, ref: JobRef, artefact: ArtefactSpec, dest_dir: Path) -> Path | None:
        out_dir = (self._dir(ref) / "out").resolve()
        path = (out_dir / artefact.path).resolve()
        if out_dir not in path.parents:
            raise JobFetchError(f"artefact path {artefact.path!r} leaves the job's output "
                                "directory")
        return path if path.is_file() else None

    def cancel(self, ref: JobRef, *, reason: str) -> JobStatus:
        current = self.status(ref)
        if current.state is not JobState.RUNNING:
            return current
        job_dir = self._dir(ref)
        _write_json(job_dir / "cancel.json", {"reason": reason, "at": time.time()})
        started = json.loads((job_dir / "started.json").read_text(encoding="utf-8"))
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(int(started["pgid"]), sig)
            except ProcessLookupError:
                break
            deadline = time.monotonic() + self.cancel_grace_s
            while self._running(job_dir) and time.monotonic() < deadline:
                time.sleep(0.05)
            if not self._running(job_dir):
                break
        return self.status(ref)


# ------------------------------------------------------------------- HTTP executor

_STATES = {s.value: s for s in (JobState.QUEUED, JobState.RUNNING, JobState.COMPLETED,
                                JobState.FAILED, JobState.CANCELLED)}
#: Every state a JobObserved event may record, for a controller read back from a trace.
_STATE_VALUES = frozenset(s.value for s in JobState)


class HTTPJobService(JobExecutor):
    """A job service over HTTP: the protocol in ``docs/compute-tasks.md``.

    ``POST /jobs`` with ``{submission_id, component_id, spec_digest, task, artefacts}``
    answers ``{job_id, state}``; a service should return the existing job for a repeated
    ``submission_id``. ``GET /jobs/<id>`` answers ``{state, detail, artefacts: [{name,
    sha256, size}]}``; ``GET /jobs/<id>/artefacts/<name>`` the bytes; ``DELETE /jobs/<id>``
    ``{state}``, where only ``cancelled`` confirms.

    JSON calls go through ``HTTPBackend.request`` (redirects held to this host, an HTML page
    refused as data, size capped), with one attempt each: a retried POST is a second
    submission unless the service deduplicates, and status polls repeat anyway.
    """

    name = "http-job-service"

    def __init__(self, base_url: str, *, http: Any = None, timeout_s: float = 30.0,
                 max_artefact_bytes: int = 8 << 30) -> None:
        from .http import HTTPBackend

        if not base_url.startswith(("http://", "https://")):
            raise ValueError(f"a job service is an http(s) URL, not {base_url!r}")
        self.base_url = base_url.rstrip("/")
        self.host = urllib.parse.urlsplit(self.base_url).hostname or ""
        self.http = http if http is not None else HTTPBackend(max_retries=1,
                                                              timeout_s=timeout_s)
        self.timeout_s = timeout_s
        self.max_artefact_bytes = max_artefact_bytes

    def _url(self, *parts: str) -> str:
        return "/".join([self.base_url, *(urllib.parse.quote(p, safe="") for p in parts)])

    def _json(self, method: str, url: str, body: Any = None) -> tuple[Any, Any, str, dict]:
        from .http import HTTPRequest

        req = HTTPRequest(url=url, method=method, json_body=body, accept="application/json")
        return self.http.request(req, use_cache=False, allowed_hosts={self.host})

    def submit(self, spec: JobSpec, *, submission_id: str) -> JobRef:
        body = {"submission_id": submission_id, "component_id": spec.component_id,
                "spec_digest": spec.digest(), "task": dict(spec.payload),
                "artefacts": [a.path for a in spec.artefacts]}
        status, value, err, meta = self._json("POST", self._url("jobs"), body)
        if status is ExecutionStatus.SUCCEEDED and isinstance(value, dict) \
                and value.get("job_id"):
            return JobRef(job_id=str(value["job_id"]), executor=self.name,
                          location=self.base_url, component_id=spec.component_id,
                          spec_digest=spec.digest(), submission_id=submission_id,
                          submitted_at=time.time(), artefacts=tuple(spec.artefacts))
        if status is ExecutionStatus.TIMEOUT:
            raise JobSubmitError(
                f"{self.host} did not answer the submission; it may have accepted the job. "
                f"Submit again with submission id {submission_id} to find out",
                status=ExecutionStatus.TIMEOUT)
        if status is ExecutionStatus.SUCCEEDED:
            raise JobSubmitError(f"{self.host} answered the submission without a job_id: "
                                 f"{json.dumps(value, default=str)[:200]}")
        refused = status if status in (ExecutionStatus.UNAVAILABLE,
                                       ExecutionStatus.DENIED) else ExecutionStatus.FAILED
        raise JobSubmitError(f"{self.host} did not accept the job: {err or status.value}",
                             status=refused)

    def status(self, ref: JobRef) -> JobStatus:
        status, value, err, meta = self._json("GET", self._url("jobs", ref.job_id))
        if meta.get("http_status") == 404:
            return JobStatus(JobState.LOST, f"{self.host} does not know job {ref.job_id} "
                                            "(expired, purged or never accepted)")
        if status is not ExecutionStatus.SUCCEEDED or not isinstance(value, dict):
            return JobStatus(JobState.UNREACHABLE, err or f"no status for {ref.job_id}")
        state = _STATES.get(str(value.get("state", "")).lower())
        if state is None:
            return JobStatus(JobState.UNREACHABLE,
                             f"{self.host} reported state {value.get('state')!r}, which this "
                             "client does not know; it is not taken as completed")
        declared: dict[str, Mapping[str, Any]] = {}
        for item in value.get("artefacts") or ():
            if isinstance(item, Mapping) and item.get("name"):
                declared[str(item["name"])] = {"sha256": str(item.get("sha256") or ""),
                                               "size": item.get("size")}
        code = value.get("exit_code")
        return JobStatus(state, str(value.get("detail") or "")[:500],
                         exit_code=code if isinstance(code, int) else None,
                         declared=declared)

    def fetch(self, ref: JobRef, artefact: ArtefactSpec, dest_dir: Path) -> Path | None:
        from .http import _GuardedRedirects, _RedirectRefused, user_agent

        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / re.sub(r"[^A-Za-z0-9_.-]", "_", artefact.name)
        tmp = dest.with_name(f".{dest.name}.part")
        opener = urllib.request.build_opener(_GuardedRedirects(
            frozenset({self.host}), urllib.parse.urlsplit(self.base_url).scheme))
        req = urllib.request.Request(self._url("jobs", ref.job_id, "artefacts", artefact.path),
                                     headers={"User-Agent": user_agent(), "Accept": "*/*"})
        try:
            with opener.open(req, timeout=self.timeout_s) as resp, tmp.open("wb") as fh:
                total = 0
                for chunk in iter(lambda: resp.read(1 << 20), b""):
                    total += len(chunk)
                    if total > self.max_artefact_bytes:
                        raise JobFetchError(f"artefact {artefact.name} exceeds "
                                            f"{self.max_artefact_bytes} bytes")
                    fh.write(chunk)
        except urllib.error.HTTPError as exc:
            tmp.unlink(missing_ok=True)
            if exc.code == 404:
                return None
            raise JobFetchError(f"artefact {artefact.name}: HTTP {exc.code}",
                                transient=exc.code >= 500) from exc
        except _RedirectRefused as exc:
            tmp.unlink(missing_ok=True)
            raise JobFetchError(f"artefact {artefact.name}: {exc}") from exc
        except (urllib.error.URLError, OSError) as exc:
            tmp.unlink(missing_ok=True)
            raise JobFetchError(f"artefact {artefact.name}: {exc}", transient=True) from exc
        except JobFetchError:
            tmp.unlink(missing_ok=True)
            raise
        os.replace(tmp, dest)
        return dest

    def cancel(self, ref: JobRef, *, reason: str) -> JobStatus:
        status, value, err, meta = self._json("DELETE", self._url("jobs", ref.job_id),
                                              {"reason": reason})
        code = meta.get("http_status")
        if code == 404:
            return JobStatus(JobState.LOST, f"{self.host} does not know job {ref.job_id}")
        if code == 409:                       # already finished: report what it finished as
            return self.status(ref)
        if status is not ExecutionStatus.SUCCEEDED or not isinstance(value, dict):
            return JobStatus(JobState.UNREACHABLE, err or "no answer to the cancellation")
        if str(value.get("state", "")).lower() == JobState.CANCELLED.value:
            return JobStatus(JobState.CANCELLED, str(value.get("detail") or ""))
        return JobStatus(JobState.RUNNING, f"cancellation requested; {self.host} reports "
                                           f"{value.get('state')!r}, not cancelled")


# ---------------------------------------------------------------------- controller

Validator = Callable[[Path], "str | None"]


class JobController:
    """Runs the four calls for one executor and records each in the run's event log.

    ``events`` is the run's ``EventLog``; without one the controller keeps its own, so a
    job is never submitted unrecorded. With ``trace_path`` the log is written to disk after
    every job event, which is what lets a restarted process find a job (``open_jobs``).
    A log read back from a trace (``EventLog.read``) carries on where it stopped: the jobs
    it holds are found again, their events stay linked to their submissions, and a state
    already observed is not recorded a second time on every poll.
    """

    def __init__(self, executor: JobExecutor, *, events: EventLog | None = None,
                 trace_path: str | Path | None = None, parent_event: str | None = None,
                 collect_dir: str | Path | None = None) -> None:
        self.executor = executor
        self.events = events if events is not None else EventLog()
        self.trace_path = Path(trace_path) if trace_path else None
        self.parent_event = parent_event
        self.collect_dir = Path(collect_dir) if collect_dir else None
        self._seen: dict[str, JobState] = {}
        self._parents: dict[str, str] = {}
        for ev in self.events.events:
            if ev.event_type == EventType.JOB_SUBMITTED and ev.detail.get("job"):
                self._parents[str(ev.detail["job"].get("job_id"))] = ev.event_id
            elif ev.event_type == EventType.JOB_OBSERVED and ev.status in _STATE_VALUES:
                self._seen[str(ev.detail.get("job_id"))] = JobState(ev.status)

    # ----------------------------------------------------------------- records
    def _emit(self, kind: str, **kw: Any) -> str:
        event = self.events.emit(kind, **kw)
        if self.trace_path is not None:
            self.events.save(self.trace_path)
        return event.event_id

    def _parent(self, ref: JobRef) -> str | None:
        return self._parents.get(ref.job_id, self.parent_event)

    def _existing(self, idempotency_key: str) -> JobRef | None:
        for ev in reversed(self.events.events):
            if (ev.event_type == EventType.JOB_SUBMITTED and ev.detail.get("job")
                    and ev.detail.get("idempotency_key") == idempotency_key):
                return JobRef.from_dict(ev.detail["job"])
        return None

    # ---------------------------------------------------------------- lookups
    def job(self, job_id: str) -> JobRef | None:
        """The reference this log recorded for a job id; None for a job it never submitted.

        What a later call is allowed to name: a job id or a reference handed in from outside
        is only ever looked up here, so nothing can be collected or cancelled that this
        log's executor did not submit.
        """
        for ev in reversed(self.events.events):
            job = ev.detail.get("job") if ev.event_type == EventType.JOB_SUBMITTED else None
            if job and str(job.get("job_id")) == str(job_id):
                return JobRef.from_dict(job)
        return None

    def jobs(self) -> tuple[JobRef, ...]:
        """Every job this log recorded as submitted, oldest first."""
        return tuple(JobRef.from_dict(ev.detail["job"]) for ev in self.events.events
                     if ev.event_type == EventType.JOB_SUBMITTED and ev.detail.get("job"))

    def job_for(self, idempotency_key: str) -> JobRef | None:
        """The job submitted under a key, if any: a key names one job."""
        return self._existing(idempotency_key) if idempotency_key else None

    def key_of(self, job_id: str) -> str:
        """The idempotency key a job was submitted under ("" when none was given)."""
        for ev in reversed(self.events.events):
            job = ev.detail.get("job") if ev.event_type == EventType.JOB_SUBMITTED else None
            if job and str(job.get("job_id")) == str(job_id):
                return str(ev.detail.get("idempotency_key") or "")
        return ""

    def unconfirmed_for(self, idempotency_key: str) -> str:
        """The submission id of a request under this key that no answer confirmed, or "".

        Submitting again with that id lets the executor answer with the job the request
        started, if it started one, instead of starting a second (``submit``).
        """
        if not idempotency_key:
            return ""
        found = open_jobs(self.events)
        for request in found.unconfirmed:
            if request.get("idempotency_key") == idempotency_key:
                return str(request["submission_id"])
        return ""

    # ----------------------------------------------------------------- grants
    def record_grant(self, ref: JobRef, grant: CancelGrant, *, grant_id: str = "") -> str:
        """Record permission to cancel one job; the id a cancellation must cite.

        Recorded before it can be used, so a governed cancellation can be held to a grant
        someone gave rather than one its caller wrote: a planner can name a grant id in a
        payload, and only a recorded one names anything. ``grant_id`` lets a caller that
        records the grant elsewhere first (PSH's audit chain) use the same id here.
        """
        if grant.job_id != ref.job_id:
            raise ValueError(f"the grant names job {grant.job_id}, not {ref.job_id}")
        if not grant.granted_by.strip() or not grant.reason.strip():
            raise ValueError("a grant names who gave it and why")
        if self.recorded_grant(grant_id) is not None:
            raise ValueError(f"grant {grant_id} is already recorded")
        grant_id = grant_id or f"grant-{uuid.uuid4().hex[:16]}"
        self._emit(EventType.JOB_CANCEL_GRANTED, parent=self._parent(ref),
                   component_id=ref.component_id, status="GRANTED",
                   policy_ruling=f"granted by {grant.granted_by}: {grant.reason}"[:300],
                   detail={"grant_id": grant_id, "job_id": ref.job_id,
                           "granted_by": grant.granted_by[:200], "reason": grant.reason[:300]})
        return grant_id

    def recorded_grant(self, grant_id: str) -> CancelGrant | None:
        """The grant this log recorded under an id, or None."""
        for ev in reversed(self.events.events):
            if (ev.event_type == EventType.JOB_CANCEL_GRANTED
                    and ev.detail.get("grant_id") == grant_id):
                return CancelGrant(str(ev.detail.get("job_id")),
                                   str(ev.detail.get("granted_by") or ""),
                                   str(ev.detail.get("reason") or ""))
        return None

    # -------------------------------------------------------------------- calls
    def submit(self, spec: JobSpec, *, idempotency_key: str = "",
               submission_id: str = "") -> JobRef:
        """Submit, or return the job already submitted under ``idempotency_key``.

        ``submission_id`` re-uses the id of a request that was recorded and never
        confirmed (``open_jobs().unconfirmed``); the executor then answers with the job
        that request started, if it started one, rather than starting a second.
        """
        if idempotency_key:
            existing = self._existing(idempotency_key)
            if existing is not None:
                return existing
        submission_id = submission_id or uuid.uuid4().hex
        requested = self._emit(
            EventType.JOB_REQUESTED, parent=self.parent_event,
            component_id=spec.component_id, status="REQUESTED",
            inputs={"submission_id": submission_id, "spec_digest": spec.digest(),
                    "executor": self.executor.name, "idempotency_key": idempotency_key,
                    "argv": list(spec.argv), "env_keys": sorted(spec.env)})
        try:
            ref = self.executor.submit(spec, submission_id=submission_id)
        except JobSubmitError as exc:
            self._emit(EventType.JOB_SUBMITTED, parent=requested,
                       component_id=spec.component_id, status=exc.status.value,
                       detail={"submission_id": submission_id, "error": str(exc)[:500],
                               "idempotency_key": idempotency_key})
            raise
        self._parents[ref.job_id] = self._emit(
            EventType.JOB_SUBMITTED, parent=requested, component_id=spec.component_id,
            status=ExecutionStatus.RUNNING.value,
            detail={"job": ref.to_dict(), "idempotency_key": idempotency_key})
        return ref

    def status(self, ref: JobRef) -> JobStatus:
        st = self.executor.status(ref)
        if self._seen.get(ref.job_id) is not st.state:
            self._seen[ref.job_id] = st.state
            self._emit(EventType.JOB_OBSERVED, parent=self._parent(ref),
                       component_id=ref.component_id, status=st.state.value,
                       detail={"job_id": ref.job_id, "detail": st.detail[:300],
                               "exit_code": st.exit_code})
        return st

    def wait(self, ref: JobRef, *, timeout_s: float, poll_s: float = 2.0) -> JobStatus:
        """Poll until the job finishes or ``timeout_s`` passes. Never cancels."""
        deadline = time.monotonic() + timeout_s
        while True:
            st = self.status(ref)
            remaining = deadline - time.monotonic()
            if st.state.finished or remaining <= 0:
                return st
            time.sleep(min(poll_s, remaining))

    def collect(self, ref: JobRef, *,
                validators: Mapping[str, Validator] | None = None) -> JobOutcome:
        """Fetch, digest and validate every declared artefact; the only path to SUCCEEDED."""
        st = self.status(ref)
        if not st.state.finished:
            waiting = st.state in (JobState.QUEUED, JobState.RUNNING)
            return JobOutcome(ref, ExecutionStatus.RUNNING if waiting
                              else ExecutionStatus.UNAVAILABLE, st.state,
                              error=(f"job {ref.job_id} is {st.state.value}; nothing was "
                                     "collected" + (f": {st.detail}" if st.detail else "")))
        if st.state is JobState.COMPLETED:
            outcome = self._validate(ref, st, validators or {})
        elif st.state is JobState.CANCELLED:
            outcome = JobOutcome(ref, ExecutionStatus.CANCELLED, st.state,
                                 error=st.detail or "cancelled")
        elif st.state is JobState.FAILED and st.timed_out:
            outcome = JobOutcome(ref, ExecutionStatus.TIMEOUT, st.state, error=st.detail)
        elif st.state is JobState.FAILED:
            outcome = JobOutcome(ref, ExecutionStatus.FAILED, st.state,
                                 error=st.detail or "the job failed")
        else:
            outcome = JobOutcome(ref, ExecutionStatus.FAILED, st.state,
                                 error=f"lost: {st.detail}; whether it did its work is "
                                       "unknown")
        self._record(ref, outcome)
        return outcome

    def _validate(self, ref: JobRef, st: JobStatus,
                  validators: Mapping[str, Validator]) -> JobOutcome:
        # A local executor hands back paths in place; a service's artefacts are downloaded
        # here. Give ``collect_dir`` for service jobs: the temporary default is recorded in
        # the event log but does not outlive the machine's temp cleaning.
        import tempfile

        base = self.collect_dir or Path(tempfile.gettempdir()) / "bioagent-collected"
        dest = base / ref.job_id
        artefacts: dict[str, Artefact] = {}
        problems: list[str] = []
        transient = False
        for spec in ref.artefacts:
            try:
                path = self.executor.fetch(ref, spec, dest)
            except JobFetchError as exc:
                problems.append(str(exc))
                transient = transient or exc.transient
                continue
            if path is None:
                if spec.required:
                    problems.append(f"required artefact {spec.name!r} ({spec.path}) is "
                                    "missing")
                continue
            digest, size = _sha256(path)
            if size == 0:
                problems.append(f"artefact {spec.name!r} is empty")
                continue
            # the executor knows an artefact by the path it was asked for, not by the
            # step's name for it
            declared = str((st.declared.get(spec.path) or {}).get("sha256") or "").lower()
            if declared and declared != digest:
                problems.append(f"artefact {spec.name!r} does not match the digest the "
                                f"executor declared ({declared[:12]}… vs {digest[:12]}…)")
                continue
            check = validators.get(spec.name)
            verdict = check(path) if check is not None else None
            if verdict:
                problems.append(f"artefact {spec.name!r}: {verdict}")
                continue
            artefacts[spec.name] = Artefact(spec.name, path, digest, size)
        if not problems:
            return JobOutcome(ref, ExecutionStatus.SUCCEEDED, st.state, artefacts)
        status = ExecutionStatus.UNAVAILABLE if transient else ExecutionStatus.FAILED
        return JobOutcome(ref, status, st.state, artefacts, error="; ".join(problems)[:1500],
                          problems=problems)

    def _record(self, ref: JobRef, outcome: JobOutcome) -> None:
        collected = self._emit(
            EventType.JOB_COLLECTED, parent=self._parent(ref), component_id=ref.component_id,
            status=outcome.status.value,
            output={n: a.sha256 for n, a in sorted(outcome.artefacts.items())} or None,
            detail={"job_id": ref.job_id, "job_state": outcome.state.value
                    if outcome.state else "", "error": (outcome.error or "")[:500],
                    "artefacts": {n: {"path": str(a.path), "sha256": a.sha256,
                                      "size": a.size} for n, a in outcome.artefacts.items()}})
        if outcome.status is ExecutionStatus.SUCCEEDED:
            for name, art in sorted(outcome.artefacts.items()):
                self._emit(EventType.ARTIFACT_CREATED, parent=collected,
                           component_id=ref.component_id, status=outcome.status.value,
                           output={"sha256": art.sha256},
                           detail={"job_id": ref.job_id, "name": name, "path": str(art.path),
                                   "size": art.size})

    def run(self, spec: JobSpec, *, timeout_s: float, poll_s: float = 2.0,
            validators: Mapping[str, Validator] | None = None,
            idempotency_key: str = "") -> JobOutcome:
        """Submit, wait up to ``timeout_s``, collect. A job still running at the deadline is
        TIMEOUT, left running, and can be collected later from its reference."""
        try:
            ref = self.submit(spec, idempotency_key=idempotency_key)
        except JobSubmitError as exc:
            return JobOutcome(None, exc.status, None, error=str(exc))
        st = self.wait(ref, timeout_s=timeout_s, poll_s=poll_s)
        if st.state.finished:
            return self.collect(ref, validators=validators)
        outcome = JobOutcome(
            ref, ExecutionStatus.TIMEOUT if st.state is not JobState.UNREACHABLE
            else ExecutionStatus.UNAVAILABLE, st.state,
            error=(f"job {ref.job_id} did not finish within {timeout_s:g}s; it was not "
                   f"cancelled and is {st.state.value}: collect it later from its reference"))
        self._record(ref, outcome)
        return outcome

    def cancel(self, ref: JobRef, grant: CancelGrant | None) -> CancelOutcome:
        """Cancel only with a grant naming this job; report what the executor confirmed."""
        if grant is None or grant.job_id != ref.job_id or not grant.granted_by.strip() \
                or not grant.reason.strip():
            why = ("no grant" if grant is None else
                   f"the grant names job {grant.job_id}, not {ref.job_id}"
                   if grant.job_id != ref.job_id else "a grant names who gave it and why")
            outcome = CancelOutcome(ref.job_id, ExecutionStatus.DENIED, None,
                                    f"cancelling {ref.job_id} needs permission: {why}")
            self._emit(EventType.JOB_CANCELLED, parent=self._parent(ref),
                       component_id=ref.component_id, status=outcome.status.value,
                       policy_ruling=outcome.detail, detail={"job_id": ref.job_id})
            return outcome
        st = self.executor.cancel(ref, reason=grant.reason)
        if st.state is JobState.CANCELLED:
            status, detail = ExecutionStatus.CANCELLED, st.detail or "cancelled"
        elif st.state is JobState.RUNNING or st.state is JobState.QUEUED:
            status = ExecutionStatus.RUNNING
            detail = st.detail or "cancellation requested; the executor has not confirmed it"
        elif st.state is JobState.UNREACHABLE:
            status, detail = ExecutionStatus.UNAVAILABLE, st.detail
        else:
            status = ExecutionStatus.FAILED
            detail = f"not cancelled: the job had already finished ({st.state.value})"
        self._seen[ref.job_id] = st.state
        self._emit(EventType.JOB_CANCELLED, parent=self._parent(ref),
                   component_id=ref.component_id, status=status.value,
                   policy_ruling=f"granted by {grant.granted_by}: {grant.reason}"[:300],
                   detail={"job_id": ref.job_id, "job_state": st.state.value,
                           "detail": detail[:300]})
        return CancelOutcome(ref.job_id, status, st.state, detail)


# -------------------------------------------------------------------- reconciling

_FINISHED = frozenset(s.value for s in JobState if s.finished)


@dataclass(frozen=True)
class OpenJobs:
    """What a restart must reconcile, read from a saved trace."""

    #: submitted and not yet recorded as finished: poll, collect or cancel them
    jobs: tuple[JobRef, ...]
    #: requested with no reference recorded: submit again with the same submission id
    unconfirmed: tuple[Mapping[str, str], ...]
    #: job id -> the idempotency key it was submitted under (a governing loop's
    #: "<run id>:<task id>"), which is how a restart matches a job to the call that started it
    keys: Mapping[str, str] = field(default_factory=dict)


def open_jobs(trace: EventLog | Mapping[str, Any] | str | Path) -> OpenJobs:
    """Jobs a run started and has not finished with, from its event log or a saved trace."""
    if isinstance(trace, EventLog):
        events = [e.to_dict() for e in trace.events]
    elif isinstance(trace, Mapping):
        events = list(trace.get("events") or ())
    else:
        events = list(EventLog.load(trace).get("events") or ())
    requested: dict[str, dict[str, str]] = {}
    submitted: dict[str, JobRef] = {}
    keys: dict[str, str] = {}
    confirmed: set[str] = set()
    finished: set[str] = set()
    for ev in events:
        kind, detail = ev.get("event_type"), ev.get("detail") or {}
        if kind == EventType.JOB_REQUESTED:
            inputs = ev.get("inputs") or {}
            requested[str(inputs.get("submission_id"))] = {
                "submission_id": str(inputs.get("submission_id")),
                "component_id": str(ev.get("component_id") or ""),
                "spec_digest": str(inputs.get("spec_digest") or ""),
                "executor": str(inputs.get("executor") or ""),
                "idempotency_key": str(inputs.get("idempotency_key") or "")}
        elif kind == EventType.JOB_SUBMITTED:
            if detail.get("job"):
                ref = JobRef.from_dict(detail["job"])
                submitted[ref.job_id] = ref
                keys[ref.job_id] = str(detail.get("idempotency_key") or "")
                confirmed.add(ref.submission_id)
            elif detail.get("submission_id"):
                # the executor refused: that request will not start anything
                confirmed.add(str(detail["submission_id"]))
        elif kind == EventType.JOB_COLLECTED and detail.get("job_state") in _FINISHED:
            # collected (or found failed, cancelled or lost): nothing left to reconcile
            finished.add(str(detail.get("job_id")))
        elif kind == EventType.JOB_CANCELLED and \
                detail.get("job_state") == JobState.CANCELLED.value:
            finished.add(str(detail.get("job_id")))
        # A JobObserved "completed" closes nothing: the job still has to be collected.
    still_open = {jid: ref for jid, ref in submitted.items() if jid not in finished}
    return OpenJobs(
        jobs=tuple(still_open.values()),
        unconfirmed=tuple(r for sid, r in requested.items() if sid not in confirmed),
        keys={jid: keys[jid] for jid in still_open})
