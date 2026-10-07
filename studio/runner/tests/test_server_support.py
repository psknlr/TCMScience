"""Helpers for the runner-service tests: a real server on an ephemeral port in a thread, a
small HTTP client, an SSE reader, and a test-only job kind.

No tests live here; the test modules import these names.
"""

from __future__ import annotations

import http.client
import json
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from tcmstudio.kinds import Kind, PrepareContext, StartContext, _int, _num
from tcmstudio.server import RunnerServer, ServerConfig

# A fixed script: prints numbered lines, sleeps, writes result.txt into the output directory,
# exits with the given code. Only whole numbers reach it.
_SLEEPER = r"""
import os, sys, time
seconds, code, lines = float(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3])
out = sys.argv[4]
for i in range(lines):
    print(f"step {i + 1} of {lines} ({(i + 1) * 100 // max(lines, 1)}%)", flush=True)
    time.sleep(seconds / max(lines, 1))
os.makedirs(out, exist_ok=True)
with open(os.path.join(out, "result.txt"), "w") as fh:
    fh.write("done %s threads=%s cuda=%r\n" % (lines, os.environ.get("OMP_NUM_THREADS"),
                                               os.environ.get("CUDA_VISIBLE_DEVICES")))
print("finished", flush=True)
sys.exit(code)
"""


def _sleep_prepare(p: dict[str, Any], ctx: PrepareContext) -> dict[str, Any]:
    return {"seconds": min(120.0, max(0.0, _num("seconds", p.get("seconds", 1)))),
            "code": _int("code", p.get("code", 0), 0, 9),
            "lines": _int("lines", p.get("lines", 3), 0, 1000)}


def _sleep_command(r: dict[str, Any], ctx: StartContext) -> list[str]:
    return [ctx.python, "-I", "-c", _SLEEPER, str(r["seconds"]), str(r["code"]),
            str(r["lines"]), "{output}"]


def test_kinds(*, gpu: bool = False) -> dict[str, Kind]:
    """``test.sleep`` (and ``test.gpu`` when asked): only the tests register them."""
    params = {"type": "object", "properties": {
        "seconds": {"type": "number", "minimum": 0, "maximum": 120, "default": 1},
        "code": {"type": "integer", "minimum": 0, "maximum": 9, "default": 0},
        "lines": {"type": "integer", "minimum": 0, "maximum": 1000, "default": 3}},
        "additionalProperties": False}
    kinds = {"test.sleep": Kind("test.sleep", ("测试任务", "Test job"), "seconds",
                                _sleep_prepare, _sleep_command,
                                artefacts=(("result", "result.txt", True),),
                                ok_exit_codes=frozenset({0, 1}), parameters=params, heavy=(),
                                timeout_s=120)}
    if gpu:
        kinds["test.gpu"] = Kind("test.gpu", ("测试 GPU 任务", "Test GPU job"), "seconds",
                                 _sleep_prepare, _sleep_command,
                                 artefacts=(("result", "result.txt", True),), gpu=True,
                                 parameters=params, heavy=(), timeout_s=120)
    return kinds


test_kinds.__test__ = False                        # a helper, not a test


class Client:
    def __init__(self, port: int, token: str | None) -> None:
        self.port = port
        self.token = token

    def raw(self, method: str, path: str, body: bytes | None = None,
            headers: dict[str, str] | None = None, token: bool = True,
            timeout: float = 120) -> tuple[int, dict[str, str], bytes]:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=timeout)
        h = dict(headers or {})
        if token and self.token and "X-TCM-Token" not in h:
            h["X-TCM-Token"] = self.token
        try:
            conn.request(method, path, body=body, headers=h)
            res = conn.getresponse()
            data = res.read()
            return res.status, {k.lower(): v for k, v in res.getheaders()}, data
        finally:
            conn.close()

    def json(self, method: str, path: str, body: Any = None, **kw: Any) -> tuple[int, Any]:
        headers = dict(kw.pop("headers", None) or {})
        data = None
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers.setdefault("Content-Type", "application/json")
        status, h, raw = self.raw(method, path, data, headers, **kw)
        try:
            return status, json.loads(raw.decode("utf-8")) if raw else None
        except ValueError:
            return status, raw

    def get(self, path: str, **kw: Any) -> tuple[int, Any]:
        return self.json("GET", path, **kw)

    def post(self, path: str, body: Any, **kw: Any) -> tuple[int, Any]:
        return self.json("POST", path, body, **kw)

    def call(self, tool: str, arguments: Any, **context: Any) -> dict[str, Any]:
        status, env = self.post("/api/call", {"tool": tool, "arguments": arguments,
                                              "context": context})
        assert status == 200, env
        return env

    def upload(self, path: Path, name: str | None = None) -> dict[str, Any]:
        from urllib.parse import quote
        data = path.read_bytes()
        status, _, raw = self.raw("POST", "/api/uploads", data,
                                  {"X-Filename": quote(name or path.name),
                                   "Content-Type": "application/octet-stream"})
        assert status in (200, 201), raw
        return json.loads(raw)

    def wait_job(self, job_id: str, timeout: float = 120) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while True:
            status, job = self.get(f"/api/jobs/{job_id}?wait_s=5")
            assert status == 200, job
            if job["state"] in ("succeeded", "failed", "cancelled"):
                return job
            if time.monotonic() > deadline:
                raise AssertionError(f"job {job_id} still {job['state']} after {timeout}s")

    def events(self, job_id: str, timeout: float = 120, use_query_token: bool = True
               ) -> list[tuple[str, Any]]:
        """Read a job's SSE stream to its end."""
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=timeout)
        path = f"/api/jobs/{job_id}/events"
        headers = {"Accept": "text/event-stream"}
        if self.token:
            if use_query_token:
                path += f"?token={self.token}"
            else:
                headers["X-TCM-Token"] = self.token
        conn.request("GET", path, headers=headers)
        res = conn.getresponse()
        assert res.status == 200, res.read()
        assert res.getheader("Content-Type", "").startswith("text/event-stream")
        events = []
        event, data = None, []
        try:
            while True:
                line = res.fp.readline()
                if not line:
                    break
                line = line.decode("utf-8").rstrip("\n")
                if line.startswith(":"):
                    events.append(("comment", line[1:].strip()))
                    continue
                if line.startswith("event:"):
                    event = line[6:].strip()
                elif line.startswith("data:"):
                    data.append(line[5:].strip())
                elif line == "":
                    if event is not None:
                        events.append((event, json.loads("\n".join(data)) if data else None))
                        if event == "done":
                            break
                    event, data = None, []
        finally:
            conn.close()
        return events


@contextmanager
def running_server(home: Path, **options: Any) -> Iterator[tuple[RunnerServer, Client]]:
    """A runner on 127.0.0.1 with an ephemeral port, served from a thread."""
    options.setdefault("port", 0)
    options.setdefault("web", None)
    options.setdefault("torch_probe", False)
    options.setdefault("poll_s", 0.2)
    options.setdefault("keepalive_s", 0.5)
    options.setdefault("local_servers", ())
    options.setdefault("environ", {"PATH": "/usr/bin:/bin", "HOME": str(home)})
    server = RunnerServer(ServerConfig(home=home, **options))
    server.start_background()
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1},
                              daemon=True)
    thread.start()
    try:
        yield server, Client(server.port, server.guard.token)
    finally:
        server.shutdown()
        server.close()
        thread.join(5)


def python() -> str:
    return sys.executable
