"""A long job as a component: one tool call submits it, collects it or cancels it.

``bioagent.backends.jobs`` gives a long job four calls with one meaning each, recorded in a
run's event log. A governed run reaches a component only through one tool call at a time,
through the BioScience runtime (resolver, policy kernel, event log), and possibly from an
isolated child process that exits when the call returns. This module is the seam:

``JobDefinition``  how one call's arguments become a ``JobSpec``: the command (a local
                   executor) or the task a service receives, the artefacts the job must
                   leave, and their validators named as ``module:function`` — declarative,
                   so an isolated child rebuilds exactly what was admitted.
``JobTool``        a definition bound to an executor, the trace that records its jobs and
                   where collected artefacts land. Every call takes an exclusive lock on the
                   trace and reads it back first, so the in-process component, any number of
                   isolated children and a restarted process all append to one record and
                   none replaces another's.
``JobBackend``     the backend the runtime routes the tool to. A call is ``submit`` (the
                   default), ``collect`` or ``cancel``; a call that continues work an earlier
                   call left pending (``job_pending``: the digest PSH names that work by) is
                   always a collection. Its answer is an ``ExecutionStatus`` like any
                   backend's, with one rule that matters: a job that has not finished is
                   RUNNING, one that cannot be collected now (its executor out of reach) is
                   UNAVAILABLE, and both name the job in ``metadata["job"]``. A submission is
                   never SUCCEEDED; only a collection whose artefacts all validated is.

A key (the governing loop's ``"<run id>:<task id>"``) names one job: a submission under a
key that already has a job reports on that job and starts nothing. A cancellation needs a
grant recorded in the trace beforehand (``JobTool.grant_cancellation``) and names it by id;
a payload cannot carry a grant of its own.

The tool runs where its manifest says (``JobTool.check``): a local executor is a
``subprocess`` component that declares the subprocess and the paths it writes, a service is
an ``http`` component that declares the service's host. The runtime it runs in
(``JobTool.runtime``) maps that mechanism to this backend and to nothing else, so the
policy kernel rules on the mechanism the job really uses.
"""

from __future__ import annotations

import contextlib
import hashlib
import importlib
import json
import os
import re
import time
import urllib.parse
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping

from ..adapters.base import CallResult
from ..runtime.events import EventLog
from ..status import ExecutionStatus
from .base import Backend
from .jobs import (OUTPUT_TOKEN, ArtefactSpec, CancelGrant, HTTPJobService, JobController,
                   JobExecutor, JobRef, JobSpec, JobSubmitError, LocalSubprocessJobs)

__all__ = ["JobDefinition", "JobTool", "JobBackend", "reference_digest", "OPERATIONS"]

#: What a call to a job tool may ask for.
OPERATIONS = ("submit", "collect", "cancel")

_PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
_OUTPUT = OUTPUT_TOKEN.strip("{}")


def reference_digest(reference: Mapping[str, Any]) -> str:
    """The digest PSH names a pending reference by, computed without PSH.

    ``psh.contracts.reference_digest`` is a SHA-256 over sorted, compact JSON. The isolated
    child has BioScience and nothing else, and it must match a continuation to the job it
    names, so the same canonical form is written out here; a test holds the two equal.
    """
    body = json.dumps(dict(reference), sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


# ------------------------------------------------------------------- definition

@dataclass(frozen=True)
class JobDefinition:
    """How a call's arguments become one job, in a form an isolated child can rebuild.

    ``argv`` is a local executor's command: ``{output}`` is the job's output directory and
    ``{name}`` the call's argument of that name. A service executor has no ``argv``; the
    call's arguments are the task it receives. ``arguments`` are the names a call must give,
    each a string or a number — a list or a mapping would reach a command line as its
    ``repr``. ``validators`` name an artefact's checker as ``module:function``, a callable
    taking the artefact's path and answering a problem or None.
    """

    arguments: tuple[str, ...] = ()
    argv: tuple[str, ...] = ()
    artefacts: tuple[ArtefactSpec, ...] = ()
    env: Mapping[str, str] = field(default_factory=dict)
    timeout_s: float = 0.0
    validators: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        problems = []
        for token in _PLACEHOLDER.findall(" ".join(self.argv)):
            if token != _OUTPUT and token not in self.arguments:
                problems.append(f"the command names {{{token}}}, which is not an argument")
        names = {a.name for a in self.artefacts}
        problems += [f"a validator is named for {n!r}, which is not an artefact"
                     for n in sorted(set(self.validators) - names)]
        problems += [f"validator {target!r} for {n!r} is not module:function"
                     for n, target in sorted(self.validators.items())
                     if ":" not in str(target)]
        if problems:
            raise ValueError("; ".join(problems))

    def spec(self, component_id: str, arguments: Mapping[str, Any]) -> JobSpec:
        """The job one call asks for; refuses arguments the definition does not take."""
        problems = [f"unknown argument {k!r}" for k in sorted(set(arguments) -
                                                              set(self.arguments))]
        problems += [f"missing argument {k!r}" for k in self.arguments if k not in arguments]
        values: dict[str, str] = {}
        for name in self.arguments:
            if name not in arguments:
                continue
            value = arguments[name]
            if isinstance(value, bool) or not isinstance(value, (str, int, float)):
                problems.append(f"argument {name!r} must be a string or a number, not "
                                f"{type(value).__name__}")
            elif OUTPUT_TOKEN in str(value):
                problems.append(f"argument {name!r} contains {OUTPUT_TOKEN}, which only the "
                                "executor may fill in")
            else:
                values[name] = str(value)
        if problems:
            raise ValueError(f"{component_id}: " + "; ".join(problems))
        argv = tuple(_PLACEHOLDER.sub(lambda m: values.get(m.group(1), m.group(0)), a)
                     for a in self.argv)
        return JobSpec(component_id, argv=argv, payload={} if self.argv else values,
                       artefacts=self.artefacts, env=dict(self.env), timeout_s=self.timeout_s)

    def validator_functions(self) -> dict[str, Callable[[Path], "str | None"]]:
        """The validators, imported. One that cannot be imported raises: an artefact whose
        check could not run is not an artefact that passed it."""
        out: dict[str, Callable[[Path], "str | None"]] = {}
        for name, target in self.validators.items():
            module, _, function = str(target).partition(":")
            try:
                out[name] = getattr(importlib.import_module(module), function)
            except (ImportError, AttributeError) as exc:
                raise ValueError(f"validator {target!r} for artefact {name!r} cannot be "
                                 f"loaded: {exc}") from exc
        return out

    def to_dict(self) -> dict[str, Any]:
        return {"arguments": list(self.arguments), "argv": list(self.argv),
                "artefacts": [a.to_dict() for a in self.artefacts], "env": dict(self.env),
                "timeout_s": self.timeout_s, "validators": dict(self.validators)}

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "JobDefinition":
        return cls(arguments=tuple(str(a) for a in d.get("arguments") or ()),
                   argv=tuple(str(a) for a in d.get("argv") or ()),
                   artefacts=tuple(ArtefactSpec(str(a["name"]), str(a["path"]),
                                                bool(a.get("required", True)))
                                   for a in d.get("artefacts") or ()),
                   env={str(k): str(v) for k, v in (d.get("env") or {}).items()},
                   timeout_s=float(d.get("timeout_s") or 0.0),
                   validators={str(k): str(v) for k, v in (d.get("validators") or {}).items()})


# ------------------------------------------------------------------------- tool

def _executor_config(executor: JobExecutor) -> dict[str, Any]:
    if isinstance(executor, LocalSubprocessJobs):
        return {"kind": "local", "root": str(executor.root),
                "start_timeout_s": executor.start_timeout_s,
                "cancel_grace_s": executor.cancel_grace_s}
    if isinstance(executor, HTTPJobService):
        return {"kind": "http", "url": executor.base_url, "timeout_s": executor.timeout_s,
                "max_artefact_bytes": executor.max_artefact_bytes}
    raise ValueError(f"a {type(executor).__name__} cannot be described for another process; "
                     "only a local executor and an HTTP job service can")


def _executor_from(config: Mapping[str, Any]) -> JobExecutor:
    kind = config.get("kind")
    if kind == "local":
        return LocalSubprocessJobs(
            config["root"], start_timeout_s=float(config.get("start_timeout_s") or 15.0),
            cancel_grace_s=float(config.get("cancel_grace_s") or 5.0))
    if kind == "http":
        return HTTPJobService(
            str(config["url"]), timeout_s=float(config.get("timeout_s") or 30.0),
            max_artefact_bytes=int(config.get("max_artefact_bytes") or 8 << 30))
    raise ValueError(f"unknown job executor kind {kind!r}")


def _inside(path: Path, declared: Any) -> bool:
    """Whether ``path`` lies inside one of the manifest's declared write paths, each read
    exactly as the policy kernel reads it (root tokens expanded, relative to the
    workspace), so this check and the kernel's ruling are about the same directories."""
    from ..policy import _resolve_requested

    target = path.resolve()
    for root in declared:
        base = _resolve_requested(str(root))
        if target == base or base in target.parents:
            return True
    return False


@dataclass(frozen=True)
class JobTool:
    """One job definition, the executor it runs on, and the trace that records its jobs."""

    executor: JobExecutor
    definition: JobDefinition
    trace: Path
    collect_dir: Path | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "trace", Path(self.trace).resolve())
        if self.collect_dir is not None:
            object.__setattr__(self, "collect_dir", Path(self.collect_dir).resolve())
        if isinstance(self.executor, LocalSubprocessJobs) and not self.definition.argv:
            raise ValueError("a local executor runs a command; the definition gives no argv")
        if isinstance(self.executor, HTTPJobService) and self.definition.argv:
            raise ValueError("a job service receives a task, not a command; the definition "
                             "gives an argv")

    # ----------------------------------------------------------------- config
    def config(self) -> dict[str, Any]:
        """What an isolated child needs to rebuild this tool, and nothing else."""
        return {"executor": _executor_config(self.executor),
                "definition": self.definition.to_dict(), "trace": str(self.trace),
                "collect_dir": str(self.collect_dir) if self.collect_dir else ""}

    @property
    def digest(self) -> str:
        return reference_digest(self.config())

    def write(self, path: str | Path) -> Path:
        """The configuration, owner-only, for the child the bridge starts."""
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(f".{target.name}.tmp")
        tmp.write_text(json.dumps(self.config(), indent=2, sort_keys=True), encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, target)
        return target

    @classmethod
    def load(cls, path: str | Path) -> "JobTool":
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(executor=_executor_from(doc["executor"]),
                   definition=JobDefinition.from_dict(doc["definition"]),
                   trace=Path(doc["trace"]),
                   collect_dir=Path(doc["collect_dir"]) if doc.get("collect_dir") else None)

    # ----------------------------------------------------------- declaration
    def check(self, manifest: Any) -> list[str]:
        """Why a manifest does not declare what this tool does; empty when it does.

        The PSH gates and the BioScience policy kernel rule on the manifest, so it must
        say what the tool really reaches: a local executor starts a subprocess and writes
        its job directories, a service receives the task at its host, and either writes
        the trace and the collected artefacts. A manifest that said less would be gated
        on a mechanism that is not the one running.
        """
        problems: list[str] = []
        backend = manifest.runtime.backend
        perms = manifest.permissions
        if isinstance(self.executor, LocalSubprocessJobs):
            if backend != "subprocess" or not perms.subprocess:
                problems.append("a local job runs as a supervised subprocess, so the "
                                "manifest declares backend 'subprocess' and subprocess: true")
            writes = [self.executor.root, self.trace.parent]
        elif isinstance(self.executor, HTTPJobService):
            host = (urllib.parse.urlsplit(self.executor.base_url).hostname or "").lower()
            if backend != "http" or host not in {h.lower() for h in perms.network}:
                problems.append(f"a service job sends its task to {host}, so the manifest "
                                f"declares backend 'http' and network host {host}")
            writes = [self.trace.parent]
        else:
            problems.append(f"no manifest rule is known for a {type(self.executor).__name__}")
            writes = []
        if self.collect_dir is not None:
            writes.append(self.collect_dir)
        missing = [str(w) for w in writes if not _inside(Path(w), perms.filesystem_write)]
        if missing:
            problems.append("the manifest's filesystem_write does not cover "
                            + ", ".join(missing))
        return problems

    # -------------------------------------------------------------- the trace
    @contextlib.contextmanager
    def open(self) -> Iterator[JobController]:
        """A controller over the trace as it is on disk, holding the trace's lock.

        Exclusive for the call: the in-process component, isolated children and a
        restarted process each read the trace, append and write it back, and a write made
        from a stale copy would delete the other's records — the record of a job that is
        still running somewhere. A trace that cannot be read raises rather than being
        started afresh over the top. POSIX ``flock``.
        """
        import fcntl

        self.trace.parent.mkdir(parents=True, exist_ok=True)
        lock = self.trace.with_name(f".{self.trace.name}.lock")
        with open(lock, "a", encoding="utf-8") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                events = EventLog.read(self.trace) if self.trace.exists() else EventLog()
                yield JobController(self.executor, events=events, trace_path=self.trace,
                                    collect_dir=self.collect_dir)
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)

    def grant_cancellation(self, job_id: str, *, granted_by: str, reason: str,
                           witness: Callable[[str, JobRef], None] | None = None) -> str:
        """Record permission to cancel one of this tool's jobs; the id a cancellation cites.

        ``witness(grant_id, ref)`` records the grant somewhere else first — PSH's audit
        chain, through ``bioagent.psh.jobs.grant_cancellation`` — and raises if it cannot.
        Only then is the grant written to the trace, so a grant the chain does not hold is
        never usable.
        """
        grant = CancelGrant(job_id, granted_by, reason)
        with self.open() as controller:
            ref = controller.job(job_id)
            if ref is None:
                raise ValueError(f"no job {job_id!r} is recorded in {self.trace}; a grant "
                                 "names a job this tool submitted")
            if not granted_by.strip() or not reason.strip():
                raise ValueError("a grant names who gave it and why")
            grant_id = f"grant-{uuid.uuid4().hex[:16]}"
            if witness is not None:
                witness(grant_id, ref)
            return controller.record_grant(ref, grant, grant_id=grant_id)

    # --------------------------------------------------------------- runtime
    def runtime(self, manifest: Any, *, kernel: Any = None) -> Any:
        """A runtime holding this tool and routing its mechanism to the job backend.

        Separate from the runtime that runs everything else, where ``subprocess`` and
        ``http`` mean one-shot calls: here the mechanism the manifest declares runs as a
        job, through resolution, the policy kernel and the event log like any call.
        """
        import copy

        from ..runtime.agentspec import Runtime
        from ..runtime.registry import ComponentRegistry
        from .base import BackendRegistry

        own = copy.deepcopy(manifest)        # its lifecycle state is this runtime's alone
        return Runtime(ComponentRegistry([own]),
                       BackendRegistry([JobBackend(self, serves=own.runtime.backend)]),
                       kernel=kernel)


# ---------------------------------------------------------------------- backend

class JobBackend(Backend):
    """Submits, collects or cancels a ``JobTool``'s jobs; never runs one to completion."""

    def __init__(self, tool: JobTool, *, serves: str) -> None:
        self.tool = tool
        self.backend = serves

    def available(self) -> bool:
        return os.name == "posix"

    def unavailable_reason(self) -> str:
        return "" if self.available() else "job tools need POSIX file locks and process groups"

    def invoke(self, manifest: Any, *, operation: str = "submit", job: str = "",
               grant: str = "", job_key: str = "", job_pending: str = "",
               **arguments: Any) -> CallResult:
        t0 = time.perf_counter()
        op = str(operation or "submit")
        if op not in OPERATIONS:
            return self._result(manifest, ExecutionStatus.FAILED, t0,
                                error=f"no operation {op!r}; one of {', '.join(OPERATIONS)}")
        if job_pending and op == "cancel":
            return self._result(manifest, ExecutionStatus.FAILED, t0,
                                error="a cancellation leaves no work pending to continue")
        try:
            with self.tool.open() as controller:
                if job_pending:
                    return self._continue(manifest, controller, job_pending, job_key, t0)
                if op == "collect":
                    ref = controller.job(str(job))
                    if ref is None:
                        return self._unknown(manifest, job, t0)
                    return self._collect(manifest, controller, ref, t0)
                if op == "cancel":
                    return self._cancel(manifest, controller, str(job), str(grant), t0)
                return self._submit(manifest, controller, job_key, arguments, t0)
        except (OSError, ValueError) as exc:
            # The trace or the lock could not be read or written. Nothing is assumed about
            # the job: a record that cannot be kept is not a call that was made.
            return self._result(manifest, ExecutionStatus.FAILED, t0,
                                error=f"the job record {self.tool.trace} could not be used: "
                                      f"{type(exc).__name__}: {exc}"[:500])

    # -------------------------------------------------------------- operations
    def _submit(self, manifest: Any, controller: JobController, key: str,
                arguments: Mapping[str, Any], t0: float) -> CallResult:
        existing = controller.job_for(key)
        if existing is not None:
            # A key names one job: report on it, start nothing.
            return self._collect(manifest, controller, existing, t0)
        try:
            spec = self.tool.definition.spec(manifest.id, arguments)
        except ValueError as exc:
            return self._result(manifest, ExecutionStatus.FAILED, t0, error=str(exc))
        try:
            ref = controller.submit(spec, idempotency_key=key,
                                    submission_id=controller.unconfirmed_for(key))
        except JobSubmitError as exc:
            return self._result(manifest, exc.status, t0, error=str(exc)[:500])
        return self._result(manifest, ExecutionStatus.RUNNING, t0,
                            error=(f"job {ref.job_id} was submitted to {ref.executor}; it is "
                                   "running and nothing has been collected"),
                            metadata={"job": ref.to_dict()})

    def _continue(self, manifest: Any, controller: JobController, digest: str, key: str,
                  t0: float) -> CallResult:
        """Collect the job a continuation names; a continuation never submits."""
        for ref in controller.jobs():
            if reference_digest(ref.to_dict()) == digest:
                recorded = controller.key_of(ref.job_id)
                if key and recorded and recorded != key:
                    return self._result(manifest, ExecutionStatus.FAILED, t0, error=(
                        f"the continuation names job {ref.job_id}, which was submitted for "
                        f"another call ({recorded}), not {key}"))
                return self._collect(manifest, controller, ref, t0)
        return self._result(manifest, ExecutionStatus.FAILED, t0, error=(
            f"the continuation names work {digest[:16]} that {self.tool.trace} does not hold; "
            "nothing was submitted in its place"))

    def _collect(self, manifest: Any, controller: JobController, ref: JobRef,
                 t0: float) -> CallResult:
        try:
            validators = self.tool.definition.validator_functions()
        except ValueError as exc:
            return self._result(manifest, ExecutionStatus.FAILED, t0, error=str(exc),
                                metadata={"job": ref.to_dict()})
        outcome = controller.collect(ref, validators=validators)
        if outcome.status is ExecutionStatus.RUNNING:
            return self._result(manifest, ExecutionStatus.RUNNING, t0,
                                error=outcome.error, metadata={"job": ref.to_dict()})
        if outcome.ok:
            value = {"job": ref.to_dict(), "state": "completed",
                     "artefacts": {name: {"path": str(a.path), "sha256": a.sha256,
                                          "size": a.size}
                                   for name, a in sorted(outcome.artefacts.items())}}
            return self._result(manifest, ExecutionStatus.SUCCEEDED, t0, value=value)
        return self._result(manifest, outcome.status, t0, error=outcome.error,
                            metadata={"job": ref.to_dict(),
                                      "problems": list(outcome.problems)})

    def _cancel(self, manifest: Any, controller: JobController, job_id: str, grant_id: str,
                t0: float) -> CallResult:
        ref = controller.job(job_id)
        if ref is None:
            return self._unknown(manifest, job_id, t0)
        # Only a grant recorded beforehand counts; the payload names it and cannot supply
        # one. Without it the controller refuses and records the refusal (DENIED).
        recorded = controller.recorded_grant(grant_id) if grant_id else None
        outcome = controller.cancel(ref, recorded)
        if outcome.status in (ExecutionStatus.DENIED, ExecutionStatus.UNAVAILABLE):
            return self._result(manifest, outcome.status, t0, error=outcome.detail)
        # The attempt was permitted and made; whether the job stopped is in the value, as
        # the executor confirmed it (CANCELLED) or not (requested, or already finished).
        value = {"job_id": ref.job_id, "grant": grant_id,
                 "cancelled": outcome.status is ExecutionStatus.CANCELLED,
                 "state": outcome.state.value if outcome.state else "",
                 "detail": outcome.detail}
        return self._result(manifest, ExecutionStatus.SUCCEEDED, t0, value=value)

    def _unknown(self, manifest: Any, job_id: Any, t0: float) -> CallResult:
        return self._result(manifest, ExecutionStatus.FAILED, t0, error=(
            f"no job {job_id!r} is recorded in {self.tool.trace}; a call may name only a job "
            "this tool submitted"))
