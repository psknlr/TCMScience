"""The runner's jobs and uploads.

Jobs run as supervised local processes through bioagent's job layer
(``bioagent.backends.jobs``: ``LocalSubprocessJobs`` under one ``JobController`` whose
trace is ``<home>/jobs/trace.json``). The controller is not thread-safe, so one lock is held
around every call to it. On top of it this module adds what a UI needs:

* a FIFO queue with at most ``max_jobs`` running at once, and at most one job on a GPU;
* one poll thread that watches running jobs, tails their logs, notices new output files,
  estimates progress, and collects a finished job exactly once;
* ``request.json`` and ``outcome.json`` beside each job, so a restarted runner re-attaches
  to jobs that kept running (their supervisors live in their own sessions), collects those
  that finished meanwhile, and queues again those that never started;
* fan-out of ``state | log | progress | artefact | done`` events to every subscriber (SSE);
* cancellation through a ``CancelGrant`` (the user's click, authenticated by the token);
* ``JobsHook``, the ``context.jobs`` object the dispatcher calls (``submit`` / ``get``).

A job is ``succeeded`` only after its required outputs were collected and verified (the
controller's digest check plus the pipeline's ``verify_run``); until then it has no result.

Uploads are streamed to ``<home>/uploads/<id>/<name>`` with their SHA-256, and job
parameters refer to them by id.
"""

from __future__ import annotations

import collections
import copy
import hashlib
import json
import mimetypes
import os
import queue
import re
import secrets
import shutil
import sys
import threading
import time
from pathlib import Path
from typing import Any, BinaryIO, Iterator, Mapping

from . import devices as dev
from .kinds import KINDS, BadParams, Kind, PrepareContext, StartContext, _no_output_token
from .settings import RunnerState, data_environment, home_paths

__all__ = ["JobError", "BadJobParams", "UnknownJob", "JobRefused", "JobConflict", "Uploads",
           "JobService", "JobsHook", "STATES"]

STATES = ("queued", "running", "succeeded", "failed", "cancelled")
_FINISHED = frozenset({"succeeded", "failed", "cancelled"})
_JOB_ID = re.compile(r"^j_[0-9a-f]{16}$")
_UPLOAD_ID = re.compile(r"^u_[0-9a-f]{16}$")
_SUBMISSION = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
_LOG_KEEP = 300
_LOG_LINE = 2000
# variables a network job needs to reach the internet the way the runner does
_NETWORK_ENV = ("HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY", "ALL_PROXY", "https_proxy",
                "http_proxy", "no_proxy", "all_proxy", "SSL_CERT_FILE", "SSL_CERT_DIR",
                "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE")
# caches and configuration a job keeps from the runner's environment when set
_PASS_ENV = ("HF_HOME", "TORCH_HOME", "XDG_CACHE_HOME", "TMPDIR")


def _now() -> float:
    return time.time()


def _iso(ts: float | None) -> str | None:
    if not ts:
        return None
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def _write_json(path: Path, doc: Any) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    os.replace(tmp, path)


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def media_type(name: str) -> str:
    lower = name.lower()
    for ext, kind in ((".tsv", "text/tab-separated-values"), (".csv", "text/csv"),
                      (".md", "text/markdown"), (".jsonl", "application/x-ndjson"),
                      (".fa", "text/plain"), (".fasta", "text/plain"), (".fastq", "text/plain"),
                      (".gtf", "text/plain"), (".pdb", "chemical/x-pdb"),
                      (".cif", "chemical/x-cif"), (".sdf", "chemical/x-mdl-sdfile"),
                      (".smi", "text/plain"), (".h5ad", "application/x-hdf5"),
                      (".h5", "application/x-hdf5"), (".npz", "application/octet-stream"),
                      (".gz", "application/gzip")):
        if lower.endswith(ext):
            return kind
    return mimetypes.guess_type(name)[0] or "application/octet-stream"


# -------------------------------------------------------------------------------- errors

class JobError(Exception):
    """A request the job service refuses; ``status`` is the HTTP status, ``type`` the
    error type of CONTRACTS §3."""

    status = 400
    type = "bad_arguments"

    def __init__(self, message: str, *, hint: str = "", **extra: Any) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.extra = extra

    def as_dict(self) -> dict[str, Any]:
        out = {"type": self.type, "message": self.message}
        if self.hint:
            out["hint"] = self.hint
        out.update(self.extra)
        return out


class BadJobParams(JobError, ValueError):       # the dispatcher maps ValueError to bad_arguments
    status, type = 400, "bad_arguments"


class UnknownJob(JobError, KeyError):           # ... and KeyError to not_found
    status, type = 404, "not_found"

    def __str__(self) -> str:
        # the dispatcher prints "no job '<id>' (<this>)"
        return "not on this runner"


class JobRefused(JobError, RuntimeError):
    """The job cannot run here now: an engine is missing, or the runner's network or
    allow_remote setting is off. A refusal, reported as such; nothing is approximated."""

    status = 422

    def __init__(self, message: str, *, type: str = "unavailable", hint: str = "",
                 **extra: Any) -> None:
        super().__init__(message, hint=hint, **extra)
        self.type = type


class JobConflict(JobError):
    status, type = 409, "conflict"


# ------------------------------------------------------------------------------- uploads

class Uploads:
    """Files the page sent, at ``<root>/<id>/<name>`` with ``meta.json``. Ids are random;
    the same bytes under the same name are stored once."""

    MAX_BYTES = 4 * 2**30

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    @staticmethod
    def safe_name(name: str) -> str:
        name = (name or "").replace("\\", "/").split("/")[-1].strip()
        name = re.sub(r"[\x00-\x1f\x7f]", "", name)
        name = name.lstrip(".") or "upload.bin"
        return name[:200]

    def save(self, stream: BinaryIO, length: int, name: str, media: str = "",
             project_id: str | None = None) -> tuple[dict[str, Any], bool]:
        """Stream ``length`` bytes to disk; returns (meta, created). Raises ValueError for a
        size beyond the cap or a body that ended early."""
        if length < 0 or length > self.MAX_BYTES:
            raise ValueError(f"an upload is at most {self.MAX_BYTES // 2**30} GB")
        name = self.safe_name(name)
        tmp_dir = self.root / ".incoming"
        tmp_dir.mkdir(exist_ok=True)
        tmp = tmp_dir / secrets.token_hex(8)
        digest = hashlib.sha256()
        received = 0
        try:
            with tmp.open("wb") as fh:
                while received < length:
                    chunk = stream.read(min(1 << 20, length - received))
                    if not chunk:
                        break
                    fh.write(chunk)
                    digest.update(chunk)
                    received += len(chunk)
            if received != length:
                raise ValueError(f"the upload ended after {received} of {length} bytes")
            sha = digest.hexdigest()
            with self._lock:
                for meta in self._all():
                    if meta.get("sha256") == sha and meta.get("name") == name and \
                            (self.root / meta["id"] / name).is_file():
                        tmp.unlink(missing_ok=True)
                        return meta, False
                upload_id = f"u_{secrets.token_hex(8)}"
                folder = self.root / upload_id
                folder.mkdir()
                os.replace(tmp, folder / name)
                meta = {"id": upload_id, "name": name, "bytes": received, "sha256": sha,
                        "media_type": (media or "").split(";")[0].strip()
                        if media and media != "application/octet-stream" else media_type(name),
                        "created_at": _iso(_now())}
                if project_id:
                    meta["project_id"] = str(project_id)[:200]
                _write_json(folder / "meta.json", meta)
                return meta, True
        finally:
            tmp.unlink(missing_ok=True)

    def _all(self) -> list[dict[str, Any]]:
        out = []
        for folder in self.root.iterdir():
            if folder.is_dir() and _UPLOAD_ID.match(folder.name):
                meta = _read_json(folder / "meta.json")
                if isinstance(meta, dict) and meta.get("id") == folder.name:
                    out.append(meta)
        return out

    def list(self) -> list[dict[str, Any]]:
        return sorted(self._all(), key=lambda m: m.get("created_at") or "", reverse=True)

    def get(self, upload_id: str) -> dict[str, Any] | None:
        if not _UPLOAD_ID.match(upload_id or ""):
            return None
        meta = _read_json(self.root / upload_id / "meta.json")
        return meta if isinstance(meta, dict) and meta.get("id") == upload_id else None

    def path(self, upload_id: str) -> Path | None:
        meta = self.get(upload_id)
        if meta is None:
            return None
        p = self.root / upload_id / str(meta["name"])
        return p if p.is_file() else None

    def by_name(self, name: str, project_id: str | None = None) -> dict[str, Any] | None:
        """The newest upload with this file name (this project's first)."""
        found = [m for m in self.list() if m.get("name") == name]
        if project_id:
            mine = [m for m in found if m.get("project_id") == project_id]
            found = mine or found
        return found[0] if found else None

    def delete(self, upload_id: str) -> bool:
        if self.get(upload_id) is None:
            return False
        shutil.rmtree(self.root / upload_id, ignore_errors=True)
        return True


# ---------------------------------------------------------------------------- the jobs

class _Subscription:
    def __init__(self, job_id: str) -> None:
        self.job_id = job_id
        self.queue: "queue.Queue[tuple[str, Any]]" = queue.Queue(maxsize=2000)
        self.closed = False

    def put(self, event: str, data: Any) -> None:
        if self.closed:
            return
        try:
            self.queue.put_nowait((event, data))
        except queue.Full:
            if event in ("state", "done"):            # never lose the end of a job
                try:
                    self.queue.get_nowait()
                    self.queue.put_nowait((event, data))
                except (queue.Empty, queue.Full):
                    pass


class _Job:
    """One job's record. ``request.json`` holds everything to rebuild it after a restart."""

    def __init__(self, doc: Mapping[str, Any], job_dir: Path) -> None:
        self.dir = job_dir
        self.id: str = doc["id"]
        self.kind: str = doc["kind"]
        self.params: dict[str, Any] = dict(doc.get("params") or {})
        self.prepared: dict[str, Any] = dict(doc.get("prepared") or {})
        self.inputs: dict[str, Any] = dict(doc.get("inputs") or {})
        self.project_id: str | None = doc.get("project_id")
        self.submission_id: str | None = doc.get("submission_id")
        self.created_at: float = float(doc.get("created_at") or _now())
        self.started_at: float | None = doc.get("started_at")
        self.finished_at: float | None = doc.get("finished_at")
        self.state: str = doc.get("state") or "queued"
        self.device: str | None = doc.get("device")
        self.device_note: str | None = doc.get("device_note")
        self.gpu: bool = bool(doc.get("gpu"))
        self.ref: dict[str, Any] | None = doc.get("ref")
        self.command: list[str] | None = doc.get("command")
        self.outcome: dict[str, Any] | None = None
        self.artefacts: list[dict[str, Any]] = []
        self.result: Any = None
        self.progress: dict[str, Any] | None = None
        self.log: "collections.deque[dict[str, str]]" = collections.deque(maxlen=_LOG_KEEP)
        self.offsets = {"stdout": 0, "stderr": 0}
        self.partial = {"stdout": "", "stderr": ""}
        self.files_seen: set[str] = set()
        # the poll thread and a cancelling request may both read the logs at the end
        self.io_lock = threading.Lock()
        self.collecting = False
        self.version = 0

    def request_doc(self) -> dict[str, Any]:
        return {"id": self.id, "kind": self.kind, "params": self.params,
                "prepared": self.prepared, "inputs": self.inputs,
                "project_id": self.project_id, "submission_id": self.submission_id,
                "created_at": self.created_at, "started_at": self.started_at,
                "finished_at": self.finished_at, "state": self.state, "device": self.device,
                "device_note": self.device_note, "gpu": self.gpu, "ref": self.ref,
                "command": self.command}

    def save(self) -> None:
        _write_json(self.dir / "request.json", self.request_doc())

    def view(self, kind: Kind | None) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.id, "kind": self.kind, "state": self.state, "params": self.params,
            "project_id": self.project_id, "created_at": _iso(self.created_at),
            "started_at": _iso(self.started_at), "finished_at": _iso(self.finished_at),
            "progress": self.progress if self.state == "running" else None,
            "device": self.device, "outcome": self.outcome,
            "artefacts": self.artefacts, "files_url": f"/api/jobs/{self.id}/files",
            "submission_id": self.submission_id,
            "title": {"zh": kind.title[0], "en": kind.title[1]} if kind else None,
            "inputs": self.inputs, "result": self.result if self.state == "succeeded" else None,
            "command": self.command}
        if self.device_note:
            out["device_note"] = self.device_note
        return out


class JobService:
    """Queue, scheduler, poll thread and event fan-out over one ``JobController``."""

    def __init__(self, home: str | Path, state: RunnerState, probe: dev.DeviceProbe, *,
                 uploads: Uploads | None = None, kinds: Mapping[str, Kind] | None = None,
                 poll_s: float = 1.0, environ: Mapping[str, str] | None = None,
                 python: str | None = None) -> None:
        self.home = Path(home).expanduser().resolve()
        self.paths = home_paths(self.home)
        for p in self.paths.values():
            p.mkdir(parents=True, exist_ok=True)
        self.state = state
        self.probe = probe
        self.uploads = uploads or Uploads(self.paths["uploads"])
        self.kinds: dict[str, Kind] = dict(kinds if kinds is not None else KINDS)
        self.poll_s = poll_s
        self.environ = dict(os.environ if environ is None else environ)
        self.data_env = data_environment(self.home, self.environ)
        self.python = python or sys.executable
        self._jobs: dict[str, _Job] = {}
        self._queue: list[str] = []
        self._subs: dict[str, list[_Subscription]] = {}
        self._lock = threading.RLock()
        self._changed = threading.Condition(self._lock)
        self._ctl_lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.unavailable: str | None = None
        self.ctl: Any = None
        self.reattached = 0
        self._open_controller()

    # ------------------------------------------------------------------ setup
    def _open_controller(self) -> None:
        try:
            from bioagent.backends.jobs import JobController, LocalSubprocessJobs
            from bioagent.runtime.events import EventLog
        except ImportError as exc:                              # pragma: no cover
            self.unavailable = f"bioagent's job layer could not be imported: {exc}"
            return
        root = self.paths["jobs"]
        trace = root / "trace.json"
        try:
            executor = LocalSubprocessJobs(root)
        except RuntimeError as exc:
            self.unavailable = (f"jobs need a POSIX system (Linux or macOS; on Windows, run the "
                                f"runner in WSL): {exc}")
            return
        events = None
        if trace.is_file():
            # The trace is rewritten in full on every event; start a new one when it is large.
            # Open jobs are followed through their own job.json references either way.
            if trace.stat().st_size > 8 * 2**20:
                trace.replace(root / f"trace-{time.strftime('%Y%m%d-%H%M%S')}.json")
            else:
                try:
                    events = EventLog.read(trace)
                except Exception:                               # noqa: BLE001
                    trace.replace(root / f"trace-unreadable-{int(_now())}.json")
        self.ctl = JobController(executor, events=events, trace_path=trace)

    def start(self) -> None:
        self._reattach()
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, name="tcmstudio-jobs", daemon=True)
            self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        self._wake.set()
        with self._lock:
            for subs in self._subs.values():
                for s in subs:
                    s.put("close", None)
            self._changed.notify_all()
        if self._thread is not None:
            self._thread.join(timeout)
            self._thread = None

    def _reattach(self) -> None:
        root = self.paths["jobs"]
        found = []
        for d in root.iterdir():
            if not d.is_dir() or not _JOB_ID.match(d.name):
                continue
            doc = _read_json(d / "request.json")
            if not isinstance(doc, dict) or doc.get("id") != d.name or doc.get("kind") is None:
                continue
            job = _Job(doc, d)
            outcome = _read_json(d / "outcome.json")
            if isinstance(outcome, dict):
                job.state = outcome.get("state") or job.state
                job.outcome = outcome.get("outcome")
                job.artefacts = outcome.get("artefacts") or []
                job.result = outcome.get("result")
                job.finished_at = outcome.get("finished_at") or job.finished_at
            elif job.state in _FINISHED:
                pass                       # finished before it was ever started (cancelled queued)
            elif (d / "job.json").is_file():
                job.state = "running"      # its supervisor may still live; the poll decides
                if job.ref is None:
                    ref_doc = _read_json(d / "job.json")
                    job.ref = (ref_doc or {}).get("ref")
                self._skip_logs(job)
                self.reattached += 1
            else:
                job.state = "queued"
                job.started_at = None
            found.append(job)
        found += self._orphans({j.id for j in found})
        found.sort(key=lambda j: j.created_at)
        with self._lock:
            for job in found:
                self._jobs[job.id] = job
                if job.state == "queued":
                    self._queue.append(job.id)

    def _orphans(self, known: set[str]) -> list[_Job]:
        """Jobs the controller's trace still holds open but this runner has no record of (a
        lost request.json): followed and collected like the others, with unknown params."""
        if self.ctl is None:
            return []
        try:
            from bioagent.backends.jobs import open_jobs
            pending = open_jobs(self.ctl.events)
        except Exception:                                       # noqa: BLE001
            return []
        out = []
        for ref in pending.jobs:
            job_dir = Path(ref.location)
            if ref.job_id in known or not _JOB_ID.match(ref.job_id) or \
                    job_dir.parent.resolve() != self.paths["jobs"].resolve() or not job_dir.is_dir():
                continue
            job = _Job({"id": ref.job_id, "kind": ref.component_id.removeprefix("tcmstudio."),
                        "params": {}, "state": "running", "ref": ref.to_dict(),
                        "created_at": ref.submitted_at, "started_at": ref.submitted_at,
                        "device_note": "re-attached from the job trace; its request was lost"},
                       job_dir)
            job.save()
            self._skip_logs(job)
            self.reattached += 1
            out.append(job)
        return out

    def _skip_logs(self, job: _Job) -> None:
        # after a restart, follow the logs from near their end rather than replaying them
        for stream in ("stdout", "stderr"):
            try:
                size = (job.dir / f"{stream}.log").stat().st_size
            except OSError:
                size = 0
            job.offsets[stream] = max(0, size - 16384)

    # ------------------------------------------------------------------ queries
    def kind_list(self) -> list[dict[str, Any]]:
        out = []
        for kind in self.kinds.values():
            missing = kind.missing(self.data_env)
            if self.unavailable:
                missing = [self.unavailable] + missing
            needs_net = kind.network is True
            out.append({"kind": kind.kind, "title": {"zh": kind.title[0], "en": kind.title[1]},
                        "parameters": kind.schema(), "available": not missing, "missing": missing,
                        "gpu": bool(kind.gpu), "duration": kind.duration,
                        "network": needs_net or callable(kind.network)})
        return out

    def counts(self) -> dict[str, int]:
        with self._lock:
            states = [j.state for j in self._jobs.values()]
        return {s: states.count(s) for s in STATES}

    def _view(self, job: _Job) -> dict[str, Any]:
        return copy.deepcopy(job.view(self.kinds.get(job.kind)))

    def get(self, job_id: str, wait_s: float = 0) -> dict[str, Any]:
        """The job; with ``wait_s``, wait up to that long for its state to change."""
        with self._lock:
            job = self._jobs.get(str(job_id))
            if job is None:
                raise UnknownJob(f"no job {job_id!r} on this runner")
            if wait_s and job.state not in _FINISHED:
                state = job.state
                deadline = time.monotonic() + min(float(wait_s), 60.0)
                while job.state == state and not self._stop.is_set():
                    left = deadline - time.monotonic()
                    if left <= 0:
                        break
                    self._changed.wait(left)
            return self._view(job)

    def list(self, project_id: str | None = None, state: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            jobs = [j for j in self._jobs.values()
                    if (project_id is None or j.project_id == project_id)
                    and (state is None or j.state == state)]
            jobs.sort(key=lambda j: j.created_at, reverse=True)
            return [self._view(j) for j in jobs]

    def _job(self, job_id: str) -> _Job:
        with self._lock:
            job = self._jobs.get(str(job_id))
        if job is None:
            raise UnknownJob(f"no job {job_id!r} on this runner")
        return job

    def out_dir(self, job_id: str) -> Path:
        return self._job(job_id).dir / "out"

    def envelope(self, job_id: str) -> dict[str, Any]:
        """The §3 envelope a succeeded ``skill.run`` job's governed run wrote
        (``out/envelope.json``): its release verdict, claims, evidence, refusals and
        limitations. It is read back only while it has the digest recorded when the job was
        collected. Raises UnknownJob for no such job, and LookupError (with the reason) when
        there is no such envelope to show."""
        job = self._job(job_id)
        with self._lock:
            state, kind, artefacts = job.state, job.kind, [dict(a) for a in job.artefacts]
        if kind != "skill.run" or state != "succeeded":
            raise LookupError(f"job {job_id} is not a succeeded governed-skill job")
        record = next((a for a in artefacts if a.get("name") == "envelope"), None)
        if not record or not record.get("sha256"):
            raise LookupError("no digest was recorded for envelope.json when the job was "
                              "collected")
        try:
            data = (job.dir / "out" / "envelope.json").read_bytes()
        except OSError as exc:
            raise LookupError(f"envelope.json cannot be read: {exc}") from None
        if hashlib.sha256(data).hexdigest() != str(record["sha256"]).removeprefix("sha256:"):
            raise LookupError("envelope.json no longer has the digest recorded when the job "
                              "was collected")
        try:
            doc = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise LookupError(f"envelope.json is not JSON: {exc}") from None
        if not isinstance(doc, dict):
            raise LookupError("envelope.json is not an envelope")
        return doc

    def files(self, job_id: str) -> list[dict[str, Any]]:
        job = self._job(job_id)
        out = job.dir / "out"
        files = []
        if out.is_dir():
            for path in sorted(out.rglob("*")):
                if path.is_file() and not path.is_symlink():
                    rel = path.relative_to(out).as_posix()
                    files.append({"path": rel, "bytes": path.stat().st_size,
                                  "sha256": self._sha256(path), "media_type": media_type(rel)})
                if len(files) >= 5000:
                    break
        return files

    _SHA_CACHE: dict[tuple[str, int, int], str] = {}

    def _sha256(self, path: Path) -> str:
        st = path.stat()
        key = (str(path), st.st_size, st.st_mtime_ns)
        cached = self._SHA_CACHE.get(key)
        if cached:
            return cached
        h = hashlib.sha256()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        digest = h.hexdigest()
        if len(self._SHA_CACHE) > 20000:
            self._SHA_CACHE.clear()
        self._SHA_CACHE[key] = digest
        return digest

    def file_path(self, job_id: str, rel: str) -> Path | None:
        """A file under the job's output directory; None for anything outside it."""
        out = (self._job(job_id).dir / "out").resolve()
        rel = (rel or "").replace("\\", "/").lstrip("/")
        if not rel or any(part in ("..", "") for part in rel.split("/")):
            return None
        target = (out / rel).resolve()
        if out not in target.parents or not target.is_file():
            return None
        return target

    # ------------------------------------------------------------------ submit
    def _find_file(self, project_id: str | None) -> Any:
        allowed = [self.paths["home"].resolve()]
        for key in ("BIOAGENT_DATA_LAKE", "BIOAGENT_TCMDB", "BIOAGENT_WORKSPACE"):
            allowed.append(Path(self.data_env[key]).expanduser().resolve())

        def find(value: str) -> tuple[Path, dict[str, Any]] | None:
            value = (value or "").strip()
            if not value:
                return None
            if _UPLOAD_ID.match(value):
                p = self.uploads.path(value)
                meta = self.uploads.get(value)
                if p is None or meta is None:
                    return None
                return p, {"upload": value, "name": meta["name"], "sha256": meta["sha256"],
                           "bytes": meta["bytes"]}
            if os.path.isabs(value) or value.startswith("~"):
                p = Path(value).expanduser().resolve()
                if p.is_file() and any(root == p or root in p.parents for root in allowed):
                    return p, {"path": str(p)}
                return None
            if "/" not in value and "\\" not in value:
                meta = self.uploads.by_name(value, project_id)
                if meta is not None:
                    p = self.uploads.path(meta["id"])
                    if p is not None:
                        return p, {"upload": meta["id"], "name": meta["name"],
                                   "sha256": meta["sha256"], "bytes": meta["bytes"]}
            return None
        return find

    def submit(self, kind_name: str, params: Mapping[str, Any] | None = None,
               project_id: str | None = None, submission_id: str | None = None
               ) -> tuple[dict[str, Any], bool]:
        """Queue a job; returns (job, created). The same ``submission_id`` returns the job it
        already started. Raises BadJobParams, JobRefused."""
        from .dispatch import validate

        kind = self.kinds.get(str(kind_name or ""))
        if kind is None:
            raise BadJobParams(f"no job kind {kind_name!r}",
                               hint="kinds: " + ", ".join(sorted(self.kinds)))
        if project_id is not None and (not isinstance(project_id, str) or len(project_id) > 200):
            raise BadJobParams("project_id must be a string of at most 200 characters")
        if submission_id is not None:
            if not isinstance(submission_id, str) or not _SUBMISSION.match(submission_id):
                raise BadJobParams("submission_id: letters, digits and _.:- (at most 128)")
            with self._lock:
                for job in self._jobs.values():
                    if job.submission_id == submission_id:
                        return self._view(job), False
        if self.unavailable:
            raise JobRefused(self.unavailable)
        params = dict(params or {})
        try:
            _no_output_token(params)
        except BadParams as exc:
            raise BadJobParams(str(exc)) from None
        v = validate(kind.schema(), params, "params")
        if not v.ok:
            raise BadJobParams("; ".join(v.problems[:8]),
                               hint=" ".join(dict.fromkeys(v.hints)) or "")
        missing = kind.missing(self.data_env)
        if missing:
            raise JobRefused(f"{kind.title[1]} cannot run on this runner: missing "
                             f"{', '.join(missing)}. Nothing is approximated.",
                             hint=self._missing_hint(kind, missing), missing=missing)
        job_id = f"j_{secrets.token_hex(8)}"
        job_dir = self.paths["jobs"] / job_id
        job_dir.mkdir(parents=True)
        ctx = PrepareContext(job_id=job_id, job_dir=job_dir, project_id=project_id,
                             paths=self.paths, data_env=self.data_env,
                             find=self._find_file(project_id), settings=self.state.get())
        try:
            prepared = kind.prepare(dict(v.value), ctx)
            self._gate(kind, prepared, ctx.settings)
        except BaseException as exc:
            shutil.rmtree(job_dir, ignore_errors=True)    # nothing of a refused job remains
            if isinstance(exc, BadParams):
                raise BadJobParams(str(exc)) from None
            raise
        job = _Job({"id": job_id, "kind": kind.kind, "params": params, "prepared": prepared,
                    "inputs": ctx.inputs, "project_id": project_id,
                    "submission_id": submission_id, "created_at": _now(), "state": "queued",
                    "gpu": kind.uses_gpu(prepared)}, job_dir)
        with self._lock:
            if submission_id is not None:         # a concurrent request with the same id won
                for other in self._jobs.values():
                    if other.submission_id == submission_id:
                        shutil.rmtree(job_dir, ignore_errors=True)
                        return self._view(other), False
            job.save()
            self._jobs[job_id] = job
            self._queue.append(job_id)
            view = self._view(job)
        self._wake.set()
        return view, True

    @staticmethod
    def _missing_hint(kind: Kind, missing: list[str]) -> str:
        if kind.kind == "research.run" and any(m.startswith("snapshots") for m in missing):
            return ("Build the data snapshots and their ledger first (BioScience-Harness "
                    "scripts/build_source_snapshots.py … --ledger <data>/snapshots/ledger.jsonl).")
        return "Install it on the runner machine (Settings → Compute lists the commands)."

    def _gate(self, kind: Kind, prepared: dict[str, Any], settings: Mapping[str, Any]) -> None:
        net = settings.get("network") or {}
        if kind.needs_network(prepared) and not (net.get("enabled") and
                                                 net.get("profile") != "offline-analysis"):
            raise JobRefused(f"{kind.title[1]} needs the network, and this runner's network "
                             "access is off.", type="network_off",
                             hint="Turn on Runner network access (Settings → Compute), or start "
                                  "the runner with --network.")
        if kind.sends_remote(prepared) and not settings.get("allow_remote"):
            raise JobRefused(f"{kind.title[1]} would send data to a third-party service, and "
                             "this runner does not allow that.",
                             hint="Turn on 'Allow sending sequences to third-party services' "
                                  "(Settings → Compute), or run it locally (e.g. method esmfold).")

    # ------------------------------------------------------------------ cancel
    def cancel(self, job_id: str) -> dict[str, Any]:
        job = self._job(job_id)
        with self._lock:
            if job.state in _FINISHED:
                raise JobConflict(f"job {job_id} already finished ({job.state}); there is "
                                  "nothing to cancel", job=self._view(job))
            if job.state == "queued":
                if job.id in self._queue:
                    self._queue.remove(job.id)
                job.state = "cancelled"
                job.finished_at = _now()
                job.outcome = {"status": "cancelled", "error": "cancelled before it started"}
                self._finish_record(job)
                return self._view(job)
        from bioagent.backends.jobs import CancelGrant, JobRef
        with self._lock:
            # claimed and being started: its supervisor appears within seconds
            deadline = time.monotonic() + 20
            while job.ref is None and job.state == "running" and time.monotonic() < deadline:
                self._changed.wait(0.5)
            if job.state in _FINISHED:
                return self._view(job)
        ref = JobRef.from_dict(job.ref) if job.ref else None
        if ref is None:
            raise JobConflict(f"job {job_id} is starting; try again in a moment")
        grant = CancelGrant(job.id, "studio-user", "cancelled from TCMScience Studio")
        with self._ctl_lock:
            result = self.ctl.cancel(ref, grant)
        if result.state is not None and result.state.value in ("running", "queued"):
            raise JobConflict(f"job {job_id}: {result.detail}")
        self._collect(job)
        return self._view(job)

    # --------------------------------------------------------------- events
    def subscribe(self, job_id: str) -> tuple[_Subscription, dict[str, Any], list[dict[str, str]]]:
        """A subscription to one job's events, with the job now and its recent log lines."""
        job = self._job(job_id)
        sub = _Subscription(job.id)
        with self._lock:
            backlog = list(job.log)[-80:]
            if not backlog and job.state in _FINISHED:
                backlog = self._read_tail(job)
            self._subs.setdefault(job.id, []).append(sub)
            return sub, self._view(job), backlog

    def unsubscribe(self, sub: _Subscription) -> None:
        sub.closed = True
        with self._lock:
            subs = self._subs.get(sub.job_id) or []
            if sub in subs:
                subs.remove(sub)
            if not subs:
                self._subs.pop(sub.job_id, None)

    def _emit(self, job: _Job, event: str, data: Any) -> None:
        for sub in list(self._subs.get(job.id) or ()):
            sub.put(event, data)

    def _read_tail(self, job: _Job) -> list[dict[str, str]]:
        lines = []
        for stream in ("stdout", "stderr"):
            try:
                with (job.dir / f"{stream}.log").open("rb") as fh:
                    fh.seek(0, os.SEEK_END)
                    fh.seek(max(0, fh.tell() - 8192))
                    text = fh.read().decode("utf-8", errors="replace")
            except OSError:
                continue
            for line in re.split(r"[\r\n]+", text)[-40:]:
                if line.strip():
                    lines.append({"stream": stream, "line": line[:_LOG_LINE]})
        return lines

    # ------------------------------------------------------------- the loop
    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception as exc:                            # noqa: BLE001
                # the loop must outlive one bad job; the error is the job's, not the runner's
                print(f"tcmstudio jobs: {type(exc).__name__}: {exc}", file=sys.stderr)
            self._wake.wait(self.poll_s)
            self._wake.clear()

    def wake(self) -> None:
        self._wake.set()

    def tick(self) -> None:
        """One pass: follow running jobs, collect finished ones, start queued ones."""
        with self._lock:
            running = [j for j in self._jobs.values() if j.state == "running"]
        for job in running:
            self._follow(job)
        self._start_queued()

    def _follow(self, job: _Job) -> None:
        if not job.ref:
            return
        from bioagent.backends.jobs import JobRef
        ref = JobRef.from_dict(job.ref)
        with self._ctl_lock:
            st = self.ctl.status(ref)
        self._tail(job)
        self._scan_files(job)
        kind = self.kinds.get(job.kind)
        if kind is not None and kind.progress is not None and not st.state.finished:
            try:
                progress = kind.progress(job.dir, job.prepared)
            except Exception:                                   # noqa: BLE001
                progress = None
            if progress != job.progress:
                with self._lock:
                    job.progress = progress
                    job.version += 1
                    self._emit(job, "progress", progress or {})
        if st.state.finished:
            self._collect(job)

    def _tail(self, job: _Job) -> None:
        with job.io_lock:
            self._tail_locked(job)

    def _tail_locked(self, job: _Job) -> None:
        for stream in ("stdout", "stderr"):
            path = job.dir / f"{stream}.log"
            try:
                size = path.stat().st_size
            except OSError:
                continue
            start = job.offsets[stream]
            if size <= start:
                if size < start:
                    job.offsets[stream] = 0
                continue
            with path.open("rb") as fh:
                fh.seek(start)
                data = fh.read(min(size - start, 256 * 1024))
            job.offsets[stream] = start + len(data)
            text = job.partial[stream] + data.decode("utf-8", errors="replace")
            parts = re.split(r"\r\n|\r|\n", text)
            job.partial[stream] = parts.pop()[-_LOG_LINE:]
            lines = [p[:_LOG_LINE] for p in parts if p.strip()]
            with self._lock:
                for line in lines[-200:]:
                    item = {"stream": stream, "line": line}
                    job.log.append(item)
                    self._emit(job, "log", item)

    def _flush_partial(self, job: _Job) -> None:
        with job.io_lock, self._lock:
            for stream in ("stdout", "stderr"):
                rest = job.partial[stream]
                job.partial[stream] = ""
                if rest.strip():
                    item = {"stream": stream, "line": rest[:_LOG_LINE]}
                    job.log.append(item)
                    self._emit(job, "log", item)

    def _scan_files(self, job: _Job) -> None:
        out = job.dir / "out"
        if not out.is_dir():
            return
        new = []
        with job.io_lock:
            for i, path in enumerate(out.rglob("*")):
                if i > 5000:
                    break
                if path.is_file():
                    rel = path.relative_to(out).as_posix()
                    if rel not in job.files_seen:
                        job.files_seen.add(rel)
                        new.append({"path": rel, "bytes": path.stat().st_size,
                                    "media_type": media_type(rel)})
        if new:
            with self._lock:
                for item in new[:200]:
                    self._emit(job, "artefact", item)

    def _start_queued(self) -> None:
        if not self._queue:
            return
        settings = self.state.get()
        max_jobs = int(settings.get("max_jobs") or 1)
        devices = self.probe.devices()          # cached after the first probe
        while True:
            with self._lock:
                running = [j for j in self._jobs.values() if j.state == "running"]
                if len(running) >= max_jobs or not self._queue:
                    return
                gpu_busy = any(j.gpu and j.device and j.device != "cpu" for j in running)
                chosen = None
                for job_id in list(self._queue):
                    job = self._jobs.get(job_id)
                    if job is None or job.state != "queued":
                        self._queue.remove(job_id)
                        continue
                    device, note = dev.resolve(settings.get("device") or "auto", devices,
                                               gpu_capable=job.gpu)
                    if device != "cpu" and gpu_busy:
                        continue                      # one job on the GPU at a time
                    chosen = (job, device, note)
                    break
                if chosen is None:
                    return
                job, device, note = chosen
                self._queue.remove(job.id)
                job.state = "running"                  # claimed; nobody else starts it
                job.device, job.device_note = device, note
                job.started_at = _now()
            self._launch(job, settings)

    def _job_env(self, kind: Kind, job: _Job, settings: Mapping[str, Any]) -> dict[str, str]:
        env = dict(self.data_env)
        for key, value in self.environ.items():
            if key.startswith("BIOAGENT_") and not key.startswith("BIOAGENT_JOB_"):
                env.setdefault(key, value)
        for key in _PASS_ENV:
            if self.environ.get(key):
                env[key] = self.environ[key]
        if kind.needs_network(job.prepared):
            for key in _NETWORK_ENV:
                if self.environ.get(key):
                    env[key] = self.environ[key]
        env.update(dev.job_env(job.device or "cpu", int(settings.get("threads") or 1)))
        return env

    def _launch(self, job: _Job, settings: Mapping[str, Any]) -> None:
        from bioagent.backends.jobs import ArtefactSpec, JobSpec, JobSubmitError

        kind = self.kinds.get(job.kind)
        try:
            if kind is None:
                raise JobSubmitError(f"the job kind {job.kind!r} is not known to this runner")
            ctx = StartContext(job_id=job.id, job_dir=job.dir, project_id=job.project_id,
                               paths=self.paths, data_env=self.data_env, settings=settings,
                               device=job.device or "cpu",
                               threads=int(settings.get("threads") or 1), python=self.python)
            argv = kind.command(job.prepared, ctx)
            spec = JobSpec(component_id=f"tcmstudio.{job.kind}", argv=tuple(argv),
                           artefacts=tuple(ArtefactSpec(n, p, r) for n, p, r in kind.artefacts),
                           env=self._job_env(kind, job, settings), timeout_s=kind.timeout_s)
            job.command = list(argv)
            with self._ctl_lock:
                ref = self.ctl.submit(spec, submission_id=job.id, idempotency_key=job.id)
            with self._lock:
                job.ref = ref.to_dict()
                job.version += 1
                job.save()
                self._emit(job, "state", self._view(job))
                self._changed.notify_all()
        except Exception as exc:                                # noqa: BLE001
            with self._lock:
                job.state = "failed"
                job.finished_at = _now()
                job.outcome = {"status": "failed",
                               "error": f"the job could not be started: {exc}"}
                self._finish_record(job)

    # -------------------------------------------------------------- collect
    def _collect(self, job: _Job) -> None:
        """Collect a finished job once: digests and validators, the kind's own checks, and the
        exit codes that are results rather than failures."""
        with self._lock:
            if job.state in _FINISHED or job.collecting:
                return
            job.collecting = True
        try:
            self._collect_once(job)
        finally:
            job.collecting = False

    def _collect_once(self, job: _Job) -> None:
        from bioagent.backends.jobs import JobRef
        from bioagent.status import ExecutionStatus

        kind = self.kinds.get(job.kind)
        ref = JobRef.from_dict(job.ref)
        self._tail(job)
        self._flush_partial(job)
        self._scan_files(job)
        with self._ctl_lock:
            st = self.ctl.status(ref)
            if not st.state.finished:
                return
            outcome = self.ctl.collect(ref, validators=dict(kind.validators) if kind else {})
        status = outcome.status
        problems = list(outcome.problems or [])
        error = outcome.error or ""
        exit_code = st.exit_code
        note = None
        if (kind is not None and status is ExecutionStatus.FAILED and not st.timed_out
                and exit_code is not None and exit_code != 0 and exit_code in kind.ok_exit_codes):
            # the process ended with a code that is a result (research: release not authorised)
            problems = self._check_artefacts(job, kind)
            status = ExecutionStatus.SUCCEEDED if not problems else ExecutionStatus.FAILED
            error = "; ".join(problems)
            note = f"exit {exit_code}: a result, not a failure (see the outputs)"
        if status is ExecutionStatus.SUCCEEDED and kind is not None and kind.verify is not None:
            try:
                extra = kind.verify(job.prepared, self.data_env)
            except Exception as exc:                            # noqa: BLE001
                extra = [f"the check after the job failed: {type(exc).__name__}: {exc}"]
            if extra:
                status, problems, error = ExecutionStatus.FAILED, extra, "; ".join(extra)
        artefacts = []
        for name, art in sorted((outcome.artefacts or {}).items()):
            try:
                rel = Path(art.path).resolve().relative_to((job.dir / "out").resolve()).as_posix()
            except ValueError:
                rel = Path(art.path).name
            artefacts.append({"name": name, "path": rel, "sha256": art.sha256, "bytes": art.size})
        if not artefacts and status is ExecutionStatus.SUCCEEDED and kind is not None:
            artefacts = self._artefact_list(job, kind)
        state = ("succeeded" if status is ExecutionStatus.SUCCEEDED
                 else "cancelled" if status is ExecutionStatus.CANCELLED else "failed")
        result_doc = None
        if state == "succeeded" and kind is not None and kind.result is not None:
            try:
                result_doc = _compact(kind.result(job.dir, job.prepared))
            except Exception:                                   # noqa: BLE001
                result_doc = None
        out: dict[str, Any] = {"status": status.value.lower()}
        if error and state != "succeeded":
            out["error"] = error[:1500]
        if problems and state != "succeeded":
            out["problems"] = problems[:20]
        if exit_code is not None:
            out["exit_code"] = exit_code
        if st.timed_out:
            out["timed_out"] = True
        if note:
            out["note"] = note
        with self._lock:
            if job.state in _FINISHED:
                return
            job.state = state
            job.finished_at = _now()
            job.outcome = out
            job.artefacts = artefacts
            job.result = result_doc
            self._finish_record(job)

    def _check_artefacts(self, job: _Job, kind: Kind) -> list[str]:
        problems = []
        out = job.dir / "out"
        for name, rel, required in kind.artefacts:
            path = out / rel
            if not path.is_file() or path.stat().st_size == 0:
                if required:
                    problems.append(f"required artefact {name!r} ({rel}) is missing or empty")
                continue
            check = kind.validators.get(name)
            verdict = check(path) if check else None
            if verdict:
                problems.append(f"artefact {name!r}: {verdict}")
        return problems

    def _artefact_list(self, job: _Job, kind: Kind) -> list[dict[str, Any]]:
        out = job.dir / "out"
        items = []
        for name, rel, _ in kind.artefacts:
            path = out / rel
            if path.is_file():
                items.append({"name": name, "path": rel, "sha256": self._sha256(path),
                              "bytes": path.stat().st_size})
        return items

    def _finish_record(self, job: _Job) -> None:
        """Persist a finished job and tell everyone (caller holds the lock)."""
        job.progress = None
        job.version += 1
        job.save()
        _write_json(job.dir / "outcome.json",
                    {"state": job.state, "outcome": job.outcome, "artefacts": job.artefacts,
                     "result": job.result, "finished_at": job.finished_at})
        view = self._view(job)
        self._emit(job, "state", view)
        self._emit(job, "done", view)
        self._changed.notify_all()


def _compact(value: Any, budget: int = 12000) -> Any:
    """A result small enough to hand to the model through job_status."""
    if value is None:
        return None
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return None
    if len(text) <= budget:
        return json.loads(text)
    from .envelope import fit_json
    try:
        return json.loads(fit_json(value, budget))
    except ValueError:
        return {"note": "the result is too large to show here; see the job's files"}


class JobsHook:
    """``context.jobs`` for the dispatcher: ``submit(kind, params, project_id) -> Job`` and
    ``get(job_id, wait_s) -> Job``. Refusals raise: ValueError (bad parameters), KeyError
    (no such job), RuntimeError (cannot run here now)."""

    def __init__(self, service: JobService) -> None:
        self.service = service
        self._local = threading.local()

    def submit(self, kind: str, params: Mapping[str, Any], project_id: Any = None) -> dict[str, Any]:
        try:
            job, _ = self.service.submit(kind, dict(params or {}),
                                         project_id=str(project_id) if project_id else None)
        except JobRefused as exc:
            self._local.refusal = exc          # the runner restates it in the envelope
            raise
        return job

    def take_refusal(self) -> JobRefused | None:
        """The refusal the last submit on this thread raised, once."""
        refusal = getattr(self._local, "refusal", None)
        self._local.refusal = None
        return refusal

    def get(self, job_id: str, wait_s: int = 0) -> dict[str, Any]:
        return self.service.get(job_id, wait_s)

    def envelope(self, job_id: str) -> dict[str, Any]:
        """A succeeded skill.run job's governed-run envelope (JobService.envelope)."""
        return self.service.envelope(job_id)


def iter_events(service: JobService, job_id: str, *, keepalive_s: float = 15.0,
                stop: threading.Event | None = None) -> Iterator[tuple[str, Any]]:
    """The events of one job for an SSE stream: the job now, its recent log, then live
    events until ``done``. Yields ("keepalive", None) when nothing happened for a while."""
    sub, job, backlog = service.subscribe(job_id)
    try:
        yield "state", job
        for item in backlog:
            yield "log", item
        if job.get("progress"):
            yield "progress", job["progress"]
        if job["state"] in _FINISHED:
            yield "done", job
            return
        while not (stop is not None and stop.is_set()):
            try:
                event, data = sub.queue.get(timeout=keepalive_s)
            except queue.Empty:
                yield "keepalive", None
                continue
            if event == "close":
                return
            yield event, data
            if event == "done":
                return
    finally:
        service.unsubscribe(sub)
