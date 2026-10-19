"""The runner's dataset routes: what can be acquired, how ready it is here, and acquisitions.

    GET    /api/datasets                            every registered dataset and its readiness
    GET    /api/datasets/{key}/manifest             its reviewed files, licence, local record,
                                                    built store and last audit entries
    GET    /api/datasets/{key}/readiness            how ready it is here, and the next step
    POST   /api/datasets/acquisitions               {dataset, include_optional?, project_id?,
                                                    submission_id?} → 201 + Location; 409
                                                    while it is already being acquired, or
                                                    when submission_id names another job
    GET    /api/datasets/acquisitions               ?project_id=&state=
    GET    /api/datasets/acquisitions/{id}          the job and each file's state
    DELETE /api/datasets/acquisitions/{id}          cancel (409 once finished)
    POST   /api/datasets/acquisitions/{id}/cancel   the same as DELETE

An acquisition is a ``tcmdb.fetch`` job (``tcmstudio.kinds``), submitted through the job
service like any other: the runner's network switch gates it (``network_off``), it is
cancelled the way a job is, and it runs ``bioagent tcmdb fetch`` →
``bioagent.acquisition.service.acquire_dataset`` (verified transfers into the runner's own
data directory). The body names a registered dataset key and nothing else: no URL is ever
accepted, and there is no field that confirms a file above the 512 MB size gate (Studio
never confirms it for the user; ``bioagent tcmdb fetch <key> --confirm`` on the runner
machine is a person's own act). Bulk files never pass through the browser's bounded
``/api/sources`` gateway.

Licences are said as they were found. ``redistribution`` comes from a reviewed licence
record (``registry/licence_records.yaml``) for the same dataset whose version names this
release (``release_covered``: a whole-token match, never a dataset with no release or a
rolling one); without one it is "unknown — downloading is not permission to
redistribute". Nothing here copies a file
anywhere but the runner's data directory; there is no object storage and no shared bucket.
"""

from __future__ import annotations

import functools
import json
import sqlite3
import types
from contextlib import closing
from typing import Any, Mapping

from .jobs import BadJobParams, JobConflict, JobError, JobService, UnknownJob

__all__ = ["DatasetAPI", "UnknownDataset", "REDISTRIBUTION_UNKNOWN", "licence_of",
           "release_covered"]

REDISTRIBUTION_UNKNOWN = "unknown — downloading is not permission to redistribute"
_BODY = ("dataset", "include_optional", "project_id", "submission_id")
_KINDS = ("tcmdb.fetch",)
_RUNNER = types.SimpleNamespace(where="runner")


class UnknownDataset(JobError):
    status, type = 404, "not_found"


@functools.lru_cache(maxsize=1)
def _data_records() -> dict[str, Any]:
    try:
        from bioagent.licences import LicenceRecords
        return {r.id: r for r in LicenceRecords.load().records if getattr(r, "kind", "") == "data"}
    except Exception:                                           # noqa: BLE001
        return {}


@functools.lru_cache(maxsize=1)
def _live_downloads() -> dict[str, Any]:
    """CI's live-download record (bioagent/data/live_download_verification.json), by file
    name: what a real download in the live-data workflow found. Read once; {} without it."""
    try:
        from importlib import resources
        doc = json.loads(resources.files("bioagent.data").joinpath(
            "live_download_verification.json").read_text(encoding="utf-8"))
    except Exception:                                           # noqa: BLE001
        return {}
    files = doc.get("files") if isinstance(doc, dict) else None
    return files if isinstance(files, dict) else {}


def live_record(spec: Any) -> dict[str, Any] | None:
    """The committed CI record of real downloads of this dataset's files: how many of them a
    live run downloaded and verified (PASS), of how many it reached, the latest run and its
    date. None when no live run reached any of them. It says what CI found on those days,
    not what is on this runner (that is ``local``)."""
    record = _live_downloads()
    rows = [record[f.name] for f in spec.files if isinstance(record.get(f.name), dict)]
    if not rows:
        return None
    latest = max(rows, key=lambda r: str(r.get("verified_at") or ""))
    return {"files_pass": sum(1 for r in rows if r.get("status") == "PASS"),
            "files_recorded": len(rows), "files": len(spec.files),
            "run": latest.get("run"), "at": str(latest.get("verified_at") or "")[:10] or None,
            "record": "bioagent/data/live_download_verification.json"}


def release_covered(version: Any, covered: Any) -> bool:
    """Whether a licence record's version text names this release: one of its
    ``;``-separated entries is the release, or begins with it as a whole token
    ("2026-04-13 frozen export (...)" covers "2026-04-13"; "12.5 (current)" does not cover
    "12" or "12.0"). A dataset with no release, or a rolling one ("current", "latest"),
    is covered by no record: its terms may have changed since the record was checked."""
    v = str(version or "").strip()
    if not v or v.lower() in ("current", "latest"):
        return False
    for entry in str(covered or "").split(";"):
        entry = entry.strip()
        if entry == v or (entry.startswith(v) and not entry[len(v)].isalnum()
                          and entry[len(v)] not in "._-/"):
            return True
    return False


def licence_of(spec: Any, *, record: bool = False) -> dict[str, Any]:
    """The dataset's licence as its spec states it, its reuse class, and what is known about
    redistribution: a reviewed record's statement when the record names this dataset's
    release (``release_covered``), else unknown (which is not permission)."""
    from bioagent.tcmdb.spec import licence_class

    out: dict[str, Any] = {"license": spec.license, "licence_class": licence_class(spec.license),
                           "commercial_use": spec.commercial_use,
                           "redistribution": REDISTRIBUTION_UNKNOWN}
    rec = _data_records().get(f"data.{spec.key}")
    applies = rec is not None and release_covered(spec.version, rec.version)
    if applies:
        out["redistribution"] = rec.redistribution
    if record and rec is not None:
        out["licence_record"] = {
            "id": rec.id, "version": rec.version, "licence": rec.licence,
            "terms_url": rec.terms_url, "commercial_use": rec.commercial_use,
            "retention": rec.retention, "redistribution": rec.redistribution,
            "checked": rec.checked.as_dict(), "applies": applies}
    return out


def _hosts(spec: Any) -> list[str]:
    return sorted({f.url.split("/")[2] for f in spec.files if "://" in f.url})


class DatasetAPI:
    """The routes above, over the runner's job service and its data directories."""

    def __init__(self, jobs: JobService, data_env: Mapping[str, str]) -> None:
        self.jobs = jobs
        self.data_env = data_env

    # ------------------------------------------------------------------ helpers
    def hub(self) -> Any:
        from bioagent.tcmdb.hub import TCMDataHub
        return TCMDataHub(self.data_env["BIOAGENT_TCMDB"])

    @staticmethod
    def spec(key: str) -> Any:
        from bioagent.tcmdb import hub as hub_module
        try:
            return hub_module.dataset(str(key))
        except KeyError:
            raise UnknownDataset(f"no registered dataset {key!r}",
                                 hint="GET /api/datasets lists them") from None

    def network_on(self) -> bool:
        net = (self.jobs.state.get() or {}).get("network") or {}
        return bool(net.get("enabled")) and net.get("profile") != "offline-analysis"

    def _readiness(self, spec: Any, status: Mapping[str, Any] | None) -> dict[str, Any]:
        from .dispatch import _readiness
        return _readiness(_RUNNER, spec.key, status, access=spec.access)

    # -------------------------------------------------------------- datasets
    def list(self) -> dict[str, Any]:
        from bioagent.tcmdb import DATASETS

        statuses = {s["dataset"]: s for s in self.hub().status()}
        rows = []
        for d in DATASETS:
            default = [f for f in d.files if not f.optional]
            sizes = [f.expected_bytes for f in default]
            r = self._readiness(d, statuses.get(d.key))
            lic = licence_of(d)
            status = statuses.get(d.key) or {}
            rows.append({"key": d.key, "name": d.name, "access": d.access,
                         "version": d.version or None, **lic, "hosts": _hosts(d),
                         "files_default": len(default),
                         "bytes_default": sum(sizes) if sizes and None not in sizes else None,
                         "acquirable": d.access == "download",
                         "readiness": {"state": r["state"], "next_step": r["next_step"]},
                         # what this runner holds, so a list needs no manifest per row
                         "local": {k: status.get(k) for k in ("built", "files_present",
                                                              "files_default", "missing_default",
                                                              "verification")
                                   if status.get(k) is not None},
                         "live_record": live_record(d)})
        return {"count": len(rows), "datasets": rows, "network": {"enabled": self.network_on()},
                "acquire": "POST /api/datasets/acquisitions {dataset}"}

    def readiness(self, key: str) -> dict[str, Any]:
        spec = self.spec(key)
        hub = self.hub()
        status = hub.status(spec.key)[0]
        r = self._readiness(spec, status)
        out: dict[str, Any] = {
            "dataset": spec.key, "access": spec.access, **r,
            "local": {k: status.get(k) for k in ("built", "files_present", "files_default",
                                                  "missing_default", "verification")
                      if status.get(k) is not None},
            "network": {"enabled": self.network_on()}}
        requires: list[dict[str, Any]] = []
        moving = r["state"] in ("supported", "partial")
        if spec.access == "download" and moving:
            optional_only = not int(status.get("files_default") or 0)
            out["acquire"] = {"method": "POST", "path": "/api/datasets/acquisitions",
                              "body": {"dataset": spec.key,
                                       **({"include_optional": True} if optional_only else {})}}
            if not self.network_on():
                requires.append({
                    "need": "network", "names": _hosts(spec),
                    "detail": "Acquiring this dataset downloads its files, and this runner's "
                              "network access is off.",
                    "remedy": "Turn on Runner network access (Settings → Compute), or start "
                              "the runner with --network."})
        mismatched = sorted(n for n, how in (status.get("verification") or {}).items()
                            if how == "mismatch")
        if mismatched:
            requires.append({
                "need": "dataset", "names": mismatched,
                "detail": "These files were recorded with another checksum than the reviewed "
                          "one: they are not the reviewed files.",
                "remedy": "Delete them and acquire the dataset again, or review the upstream "
                          "change before using them."})
        if status.get("built"):
            checked = hub.check(spec.key, rebuild=False)
            out["check"] = {"ok": checked.get("ok"), "problems": checked.get("problems", [])[:50],
                            "warnings": checked.get("warnings", [])[:20]}
        out["requires"] = requires
        return out

    def manifest(self, key: str) -> dict[str, Any]:
        from bioagent.acquisition.service import SIZE_GATE_BYTES, wanted_files
        from bioagent.acquisition.transfer import read_sidecar
        from bioagent.tcmdb import acquire

        spec = self.spec(key)
        hub = self.hub()
        raw = hub.raw_dir(spec.key)
        record = acquire.read_manifest(raw)
        files = []
        for f in wanted_files(spec, include_optional=True):
            path, part = raw / f.name, raw / (f.name + ".part")
            local: dict[str, Any] = {"present": path.is_file()}
            if local["present"]:
                local.update(bytes=path.stat().st_size,
                             verification=acquire.verification_of(record.get(f.name) or {},
                                                                  f.checksum))
            if f.name in record:
                local["record"] = record[f.name]
            if part.is_file():
                side = read_sidecar(raw / (f.name + ".part.json")) or {}
                local.update(part_bytes=part.stat().st_size,
                             resumable=bool(side.get("etag") or side.get("last_modified")
                                            or f.checksum))
            files.append({"name": f.name, "url": f.url,
                          "host": f.url.split("/")[2] if "://" in f.url else None,
                          "table": f.table, "fmt": f.fmt, "optional": f.optional,
                          "expected_bytes": f.expected_bytes, "reviewed_checksum": f.checksum,
                          **({"license": f.license} if f.license else {}),
                          **({"note": f.note} if f.note else {}), "local": local})
        out: dict[str, Any] = {
            "dataset": spec.key, "name": spec.name, "access": spec.access,
            "version": spec.version or None, "homepage": spec.homepage,
            **licence_of(spec, record=True), "hosts": _hosts(spec), "files": files,
            "live_record": live_record(spec),
            "size_gate_bytes": SIZE_GATE_BYTES,
            "store": self._store(hub, spec.key),
            "audit": acquire.read_audit(hub.root, spec.key)[-20:]}
        if spec.instructions:
            out["instructions"] = spec.instructions
        if spec.notes:
            out["notes"] = spec.notes
        return out

    @staticmethod
    def _store(hub: Any, key: str) -> dict[str, Any]:
        path = hub.db_path(key)
        if not path.is_file():
            return {"built": False}
        from bioagent.tcmdb.store import connect
        out: dict[str, Any] = {"built": True}
        try:
            with closing(connect(path)) as conn:
                out["files"] = [dict(r) for r in conn.execute(
                    "SELECT tbl, file, url, bytes, sha256, rows, license, loaded_at "
                    "FROM _tcmdb_files")]
                try:
                    out["build"] = {r[0]: r[1] for r in conn.execute(
                        "SELECT key, value FROM _tcmdb_build")}
                except sqlite3.OperationalError:
                    out["build"] = {}
        except sqlite3.Error as exc:
            out["error"] = f"the store could not be read: {exc}"
        return out

    # ---------------------------------------------------------- acquisitions
    def acquire(self, body: Any) -> tuple[dict[str, Any], bool]:
        from bioagent.acquisition.service import plan

        if not isinstance(body, dict):
            raise BadJobParams("the body must be {dataset, include_optional?, project_id?, "
                               "submission_id?}")
        extra = sorted(set(body) - set(_BODY))
        if extra:
            raise BadJobParams(
                f"unknown field(s) {', '.join(extra)}: an acquisition names a registered "
                "dataset and nothing else (no URL; no size-gate override, which Studio never "
                "gives)", hint="fields: " + ", ".join(_BODY))
        key = body.get("dataset")
        if not isinstance(key, str) or not key.strip():
            raise BadJobParams("dataset: name a registered dataset key",
                               hint="GET /api/datasets lists them")
        key = key.strip()
        if "://" in key or "/" in key or "\\" in key:
            raise BadJobParams("dataset: a registered dataset key, never a URL or a path: only "
                               "the reviewed files of a registered dataset are acquired",
                               hint="GET /api/datasets lists them")
        try:
            spec = self.spec(key)
        except UnknownDataset as exc:
            raise BadJobParams(f"dataset: {exc.message}", hint=exc.hint) from None
        if spec.access != "download":
            raise BadJobParams(
                f"dataset: {spec.name} cannot be downloaded by a program here ({spec.access})"
                + (f": {spec.instructions}" if spec.instructions else ""))
        optional = body.get("include_optional", False)
        if not isinstance(optional, bool):
            raise BadJobParams("include_optional must be true or false")
        submission_id = body.get("submission_id") or None
        # a second acquisition of a dataset already being acquired would find each shared
        # file locked by the first and fail: it is refused here, naming the one that runs
        # (the same submission_id is a retry of that request and returns it below)
        for other in self.jobs.list(None, None):
            if (other.get("kind") in _KINDS and other.get("state") in ("queued", "running")
                    and (other.get("params") or {}).get("dataset") == spec.key
                    and (submission_id is None or other.get("submission_id") != submission_id)):
                raise JobConflict(
                    f"{spec.key} is already being acquired ({other['id']}, {other['state']}); a "
                    "second acquisition would find its files locked by the first",
                    hint=f"GET /api/datasets/acquisitions/{other['id']}", job=other["id"])
        job, created = self.jobs.submit(
            "tcmdb.fetch", {"dataset": spec.key, "include_optional": optional},
            project_id=body.get("project_id") or None, submission_id=submission_id)
        params = job.get("params") or {}
        if not created and (job.get("kind") not in _KINDS or params.get("dataset") != spec.key
                            or bool(params.get("include_optional")) != optional):
            # the job service returns whichever job holds the submission_id, of any kind
            of = f" of {params['dataset']}" if params.get("dataset") else ""
            raise JobConflict(
                f"submission_id {submission_id!r} already names another job ({job['id']}, "
                f"{job.get('kind')}{of}), not this acquisition of {spec.key}",
                hint="use a new submission_id for a new request", job=job["id"])
        return {"acquisition": self._view(job),
                "plan": plan(self.hub(), spec.key, include_optional=optional)}, created

    def _job(self, job_id: str) -> dict[str, Any]:
        job = self.jobs.get(job_id)
        if job.get("kind") not in _KINDS:
            raise UnknownJob(f"job {job_id} is not a dataset acquisition (a tcmdb.fetch job)",
                             hint=f"GET /api/jobs/{job_id}")
        return job

    def _view(self, job: Mapping[str, Any], *, files: bool = True) -> dict[str, Any]:
        from bioagent.acquisition.service import acquisition_state

        params = job.get("params") or {}
        key = str(params.get("dataset") or "")
        out: dict[str, Any] = {"id": job["id"], "dataset": key, "state": job["state"],
                               "url": f"/api/datasets/acquisitions/{job['id']}", "job": job}
        if not files:
            return out
        try:
            self.spec(key)
        except UnknownDataset:
            return out
        since = job.get("started_at")
        state = acquisition_state(self.hub(), key,
                                  include_optional=bool(params.get("include_optional")),
                                  since=since, running=job["state"] == "running",
                                  audit=since is not None)
        summary = dict(state["progress"])
        if job["state"] != "running":
            summary.pop("fraction", None)
            summary.pop("stage", None)
        out.update(files=state["files"], states=state["states"], summary=summary)
        return out

    def acquisitions(self, project_id: str | None = None, state: str | None = None
                     ) -> dict[str, Any]:
        jobs = [j for j in self.jobs.list(project_id, state) if j.get("kind") in _KINDS]
        return {"acquisitions": [self._view(j, files=False) for j in jobs]}

    def acquisition(self, job_id: str) -> dict[str, Any]:
        return self._view(self._job(job_id))

    def cancel(self, job_id: str) -> dict[str, Any]:
        self._job(job_id)
        return self._view(self.jobs.cancel(job_id))
