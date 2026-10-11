"""The runner an agent talks to, for validating what runs as a job.

``tcmstudio validate --approve-jobs`` starts tcmstudio's own server in a temporary home, on a
loopback port with a pairing token, and calls it over HTTP the way the Studio page does:
``POST /api/call`` with the token and the approvals the page's router would send, files
through ``POST /api/uploads``, a job followed with ``job_status``. The job service is the
runner's own: the same queue, gate, command lines, artefact checks and run verification a
user's jobs go through, with nothing reached around them.

A job runs only on an approval, and here the approval is the operator's: they gave
``--approve-jobs`` on the command line, and every report says so. Nothing is approved on a
user's behalf, and without the flag no job is submitted.
"""

from __future__ import annotations

import http.client
import json
import os
import threading
import time
import urllib.parse
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

__all__ = ["AgentRunner", "FINISHED", "approvals_for"]

FINISHED = ("succeeded", "failed", "cancelled")


def approvals_for(tool: str, args: Mapping[str, Any], *, job: bool, network: bool) -> list[str]:
    """The approvals the page's router asks the user for before this call (router.js
    ``#reasons``): a job, the network, sending data to a third party, the runner."""
    out = ["job"] if job else []
    if network:
        out.append("network")
    inner = args.get("arguments") if isinstance(args.get("arguments"), Mapping) else {}
    if args.get("allow_remote") is True or inner.get("allow_remote") is True:
        out.append("remote_upload")
    return out + ["first_runner_call"]


class AgentRunner:
    """tcmstudio's runner in a temporary home, served on 127.0.0.1, called over HTTP."""

    def __init__(self, home: str | Path, *, tcmdb_root: str | Path, network: bool,
                 data_lake: str | Path | None = None,
                 log: Callable[[str], None] = lambda m: None, poll_s: float = 0.5) -> None:
        from .server import RunnerServer, ServerConfig
        from .settings import home_paths

        environ = dict(os.environ)
        environ["BIOAGENT_TCMDB"] = str(tcmdb_root)
        self.home = Path(home)
        # The runner's data lake (research raw files and snapshots) and workspace are its
        # own, never the operator's: what its jobs write stays where this run can see it.
        paths = home_paths(self.home)
        self.data_lake = Path(data_lake) if data_lake else paths["data"]
        environ["BIOAGENT_DATA_LAKE"] = str(self.data_lake)
        environ["BIOAGENT_WORKSPACE"] = str(paths["workspace"])
        self.network = network
        self.log = log
        self.server = RunnerServer(ServerConfig(
            home=self.home, port=0, web=None, torch_probe=False, poll_s=poll_s,
            keepalive_s=5.0, local_servers=(), environ=environ, network=network, max_jobs=1,
            quiet=True))
        # the runner's own settings, as a user sets them in Settings → Compute: the network
        # on or off, and no job may send data to a third-party service (no scenario does;
        # one that tried would be refused, as on a runner whose user has not allowed it)
        self.server.state.update({"network": {"enabled": network,
                                              "profile": "biomedical-research" if network
                                              else "offline-analysis"},
                                  "allow_remote": False})
        self.server.start_background()
        self._thread = threading.Thread(target=self.server.serve_forever,
                                        kwargs={"poll_interval": 0.1}, daemon=True)
        self._thread.start()
        self.port, self.token = self.server.port, self.server.guard.token
        self.calls: list[dict[str, Any]] = []

    # ------------------------------------------------------------------- http
    def _request(self, method: str, path: str, body: bytes | None = None,
                 headers: Mapping[str, str] | None = None, timeout: float = 600.0,
                 *, decode: bool = True) -> tuple[int, Any]:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=timeout)
        h = dict(headers or {})
        if self.token:
            h["X-TCM-Token"] = self.token
        try:
            conn.request(method, path, body=body, headers=h)
            res = conn.getresponse()
            raw = res.read()
        finally:
            conn.close()
        if not decode:
            return res.status, raw
        try:
            return res.status, json.loads(raw.decode("utf-8")) if raw else None
        except ValueError:
            return res.status, raw

    def call(self, tool: str, arguments: Mapping[str, Any], *, approvals: Sequence[str] = (),
             project_id: str = "validation") -> dict[str, Any]:
        """``POST /api/call``, as the page sends it."""
        body = json.dumps({"tool": tool, "arguments": dict(arguments),
                           "context": {"project_id": project_id,
                                       "approvals": list(approvals)}},
                          ensure_ascii=False).encode("utf-8")
        status, env = self._request("POST", "/api/call", body,
                                    {"Content-Type": "application/json"})
        if status != 200 or not isinstance(env, dict):
            return {"status": "failed", "error": {"type": "runtime_error",
                                                  "message": f"HTTP {status}: {str(env)[:300]}"}}
        self.calls.append({"tool": tool, "status": env.get("status"),
                           "approvals": list(approvals)})
        return env

    def upload(self, path: str | Path, *, name: str | None = None,
               project_id: str = "validation") -> dict[str, Any]:
        """``POST /api/uploads``: the file, as a user adds one in the Studio."""
        path = Path(path)
        data = path.read_bytes()
        status, meta = self._request(
            "POST", "/api/uploads", data,
            {"Content-Type": "application/octet-stream", "Content-Length": str(len(data)),
             "X-Filename": urllib.parse.quote(name or path.name), "X-Project-Id": project_id})
        if status not in (200, 201) or not isinstance(meta, dict) or not meta.get("id"):
            raise RuntimeError(f"upload of {path.name} refused: HTTP {status}: {str(meta)[:300]}")
        return meta

    def file(self, job_id: str, rel: str) -> bytes:
        """One of a finished job's output files, as the page downloads it."""
        status, body = self._request(
            "GET", f"/api/jobs/{urllib.parse.quote(job_id)}/files/{urllib.parse.quote(rel)}",
            decode=False)
        if status != 200:
            raise RuntimeError(f"{rel} of job {job_id}: HTTP {status}")
        return body

    # -------------------------------------------------------------------- jobs
    def follow(self, job_id: str, *, timeout_s: float,
               on_poll: Callable[[dict[str, Any]], None] | None = None) -> dict[str, Any]:
        """``job_status`` (wait_s 60) until the job has finished or `timeout_s` passed; the
        last envelope."""
        deadline = time.monotonic() + timeout_s
        env: dict[str, Any] = {}
        while True:
            left = deadline - time.monotonic()
            env = self.call("job_status", {"job_id": job_id,
                                           "wait_s": int(max(1, min(60, left)))},
                            approvals=["first_runner_call"])
            job = env.get("result") if isinstance(env.get("result"), dict) else {}
            if on_poll is not None:
                on_poll(env)
            if job.get("state") in FINISHED or env.get("status") != "succeeded":
                return env
            if time.monotonic() >= deadline:
                return env

    def cancel_running(self) -> list[str]:
        cancelled = []
        for job in self.server.jobs.list():
            if job.get("state") not in FINISHED:
                try:
                    self.server.jobs.cancel(job["id"])
                    cancelled.append(job["id"])
                except Exception:                                   # noqa: BLE001
                    pass
        return cancelled

    def close(self) -> None:
        """Cancel what still runs, then stop the server."""
        try:
            for job_id in self.cancel_running():
                self.log(f"cancelled job {job_id} (the validation ended before it)")
        finally:
            self.server.shutdown()
            self.server.close()
            self._thread.join(5)

    def __enter__(self) -> "AgentRunner":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()
