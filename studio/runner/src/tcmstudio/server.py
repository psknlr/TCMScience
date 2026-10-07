"""``tcmstudio serve``: the local runner — the user's own CPU and GPU behind TCMScience Studio.

    tcmstudio serve [--host 127.0.0.1] [--port 8765] [--home ~/.tcmscience/studio] [--web DIR]
                    [--allow-origin URL]... [--allow-host HOST]... [--no-token] [--no-browser]
                    [--device auto|cpu|cuda:0|mps] [--threads N] [--max-jobs N] [--network]
                    [--quiet]

A standard-library HTTP server (``ThreadingHTTPServer``) with the JSON API of CONTRACTS §4:
tool calls through ``tcmstudio.dispatch`` (in a worker pool: governed runs are CPU-bound),
the runner's catalog, devices and settings, jobs with live events (SSE), uploads, the model
proxy, and the web app itself, so ``http://127.0.0.1:8765/`` works offline.

Who may call it is decided in ``tcmstudio.security`` (Host check, Origin allowlist, pairing
token). Request bodies are never logged: a proxied model call carries the user's key.
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import gzip
import json
import os
import platform
import shutil
import signal
import sys
import threading
import time
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import parse_qs, quote, unquote, urlsplit

from . import __version__
from . import devices as dev
from .jobs import JobError, JobService, JobsHook, Uploads, iter_events, media_type
from .llmproxy import LOCAL_SERVERS, LLMProxy, ProxyRefused, probe_local
from .security import PUBLIC_ORIGIN, Guard, SecurityError, is_loopback_host, normalize_origin
from .settings import (DEFAULT_HOME, HomeInUse, HomeLock, RunnerState, SettingsError,
                       apply_environment, data_environment, prepare_home)

__all__ = ["ServerConfig", "RunnerServer", "Handler", "add_arguments", "run", "make_server",
           "pairing_link", "default_web_root"]

JSON_TYPE = "application/json; charset=utf-8"
MAX_JSON = 8 * 2**20
GZIP_MIN = 32 * 1024
STATIC_TYPES = {
    ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8", ".webmanifest": "application/manifest+json",
    ".svg": "image/svg+xml", ".png": "image/png", ".ico": "image/x-icon",
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp", ".gif": "image/gif",
    ".woff2": "font/woff2", ".woff": "font/woff", ".ttf": "font/ttf", ".otf": "font/otf",
    ".wasm": "application/wasm", ".map": "application/json; charset=utf-8",
    ".txt": "text/plain; charset=utf-8", ".md": "text/markdown; charset=utf-8",
    ".gz": "application/gzip", ".tgz": "application/gzip", ".zip": "application/zip",
    ".whl": "application/zip", ".xml": "application/xml; charset=utf-8",
}
ISOLATION_HEADERS = (("Cross-Origin-Opener-Policy", "same-origin"),
                     ("Cross-Origin-Embedder-Policy", "require-corp"),
                     ("Cross-Origin-Resource-Policy", "cross-origin"))
# what a page served by this runner reads at /v1/health (Chinese, like the relay's own messages)
NO_RELAY_MESSAGE = ("本机 Runner 不提供 Tao-S1。Tao-S1 只接受 https://science.impf.ai 与 "
                    "http://127.0.0.1:8765 上的页面：请在默认端口 8765 启动 Runner，"
                    "或在「设置 → 模型」中使用自己的模型 API 或本地模型。")


def default_web_root() -> Path | None:
    """The web app this runner serves when ``--web`` is not given: a copy shipped inside the
    package, else ``studio/web`` of the checkout this module runs from."""
    here = Path(__file__).resolve()
    for candidate in (here.parent / "web", here.parents[3] / "web"):
        if (candidate / "index.html").is_file():
            return candidate
    return None


def pairing_link(url: str, token: str | None, origin: str = PUBLIC_ORIGIN) -> str:
    payload = json.dumps({"url": url, "token": token or ""}, separators=(",", ":"))
    code = base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii").rstrip("=")
    return f"{origin}/#pair={code}"


@dataclass
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 8765
    home: Path = field(default_factory=lambda: DEFAULT_HOME.expanduser())
    web: Path | None | str = "auto"
    allow_origins: list[str] = field(default_factory=list)
    allow_hosts: list[str] = field(default_factory=list)
    no_token: bool = False
    device: str | None = None
    threads: int | None = None
    max_jobs: int | None = None
    network: bool = False
    quiet: bool = False
    open_browser: bool = False
    kinds: Mapping[str, Any] | None = None                 # tests add their own kinds
    poll_s: float = 1.0
    keepalive_s: float = 15.0
    local_servers: tuple[Mapping[str, Any], ...] = LOCAL_SERVERS
    local_timeout: float = 1.5
    call_workers: int | None = None
    environ: Mapping[str, str] | None = None
    torch_probe: bool = True


class RunnerServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    block_on_close = False

    def __init__(self, config: ServerConfig) -> None:
        self.config = config
        self.home = prepare_home(config.home)["home"]
        self.home_lock = HomeLock(self.home)
        self.home_lock.acquire()
        try:
            self._setup(config)
        except BaseException:
            self.home_lock.release()
            raise

    def _setup(self, config: ServerConfig) -> None:
        self.state = RunnerState(self.home)
        overrides: dict[str, Any] = {}
        if config.device:
            overrides["device"] = config.device
        if config.threads:
            overrides["threads"] = config.threads
        if config.max_jobs:
            overrides["max_jobs"] = config.max_jobs
        if config.network:
            overrides["network"] = {"enabled": True}
        if overrides:
            self.state.update(overrides)            # raises SettingsError on a bad flag value
        token = Guard.resolve_token(config.host, self.state.token, config.no_token)
        self.guard = Guard(bind_host=config.host, token=token, allow_origins=config.allow_origins)
        self.proxy = LLMProxy(config.allow_hosts)
        self.environ = dict(os.environ if config.environ is None else config.environ)
        self.data_env = data_environment(self.home, self.environ)
        self.probe = dev.DeviceProbe(torch=config.torch_probe)
        self.uploads = Uploads(self.home / "uploads")
        self.jobs = JobService(self.home, self.state, self.probe, uploads=self.uploads,
                               kinds=config.kinds if config.kinds is not None else None,
                               poll_s=config.poll_s, environ=self.environ)
        self.hook = JobsHook(self.jobs)
        workers = config.call_workers or max(2, min(8, os.cpu_count() or 2))
        self.pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="tcmstudio-call")
        self.web_root = self._web_root(config.web)
        self.stopping = threading.Event()
        self._catalog: dict[str, Any] | None = None
        self._catalog_lock = threading.Lock()
        self._runtime_lock = threading.Lock()
        self._facts_at = 0.0
        self._facts: dict[str, Any] | None = None
        if ":" in config.host:
            import socket
            self.address_family = socket.AF_INET6
        super().__init__((config.host, config.port), Handler)

    # -------------------------------------------------------------------- setup
    @staticmethod
    def _web_root(web: Path | None | str) -> Path | None:
        if web == "auto":
            return default_web_root()
        if web in (None, ""):
            return None
        root = Path(str(web)).expanduser().resolve()
        if not (root / "index.html").is_file():
            raise SecurityError(f"--web {root}: no index.html there")
        return root

    @property
    def port(self) -> int:
        return int(self.server_address[1])

    @property
    def url(self) -> str:
        host = self.config.host
        if host in ("", "0.0.0.0", "::") or host in ("localhost",):
            host = "127.0.0.1"
        shown = f"[{host}]" if ":" in host else host
        return f"http://{shown}:{self.port}"

    def pairing_link(self) -> str:
        return pairing_link(self.url, self.guard.token)

    def start_background(self) -> None:
        self.jobs.start()
        threading.Thread(target=self._warm, name="tcmstudio-warm", daemon=True).start()

    def _warm(self) -> None:
        try:
            self.probe.facts()
            self.catalog()
        except Exception as exc:                                # noqa: BLE001
            print(f"tcmstudio: warming up failed: {type(exc).__name__}: {exc}", file=sys.stderr)

    def close(self) -> None:
        self.stopping.set()
        self.jobs.stop()
        self.pool.shutdown(wait=False, cancel_futures=True)
        self.server_close()
        self.home_lock.release()

    # ------------------------------------------------------------------ catalog
    def catalog(self, refresh: bool = False) -> dict[str, Any]:
        with self._catalog_lock:
            if self._catalog is None or refresh:
                from .catalog import build_catalog
                doc = build_catalog("runner", probe=True)
                # what the job service knows beyond the import system (e.g. the research
                # loop's snapshots, a system without process groups)
                kinds = {k["kind"]: k for k in self.jobs.kind_list()}
                for entry in doc.get("entries", ()):
                    if entry.get("kind") != "job":
                        continue
                    info = kinds.get(entry["id"].split(".", 1)[1])
                    if info and info["missing"]:
                        entry["available"] = False
                        entry["missing"] = sorted(set(entry.get("missing") or []) |
                                                  set(info["missing"]))
                self._catalog = doc
            return self._catalog

    # -------------------------------------------------------------- host facts
    def host_facts(self) -> dict[str, Any]:
        """What the dispatcher's system.capabilities reports as ``host`` (cached briefly)."""
        now = time.monotonic()
        if self._facts is not None and now - self._facts_at < 20:
            return self._facts
        facts = self.probe.facts()
        settings = self.state.get()
        resolved, note = dev.resolve(settings["device"], facts["devices"])
        self._facts = {
            "runner": {"url": self.url, "version": __version__},
            "cpu": facts["cpu"], "memory_gb": facts["memory_gb"], "devices": facts["devices"],
            "device_setting": settings["device"], "device_for_jobs": resolved,
            **({"device_note": note} if note else {}),
            "threads_per_job": settings["threads"], "max_jobs": settings["max_jobs"],
            "allow_remote": settings["allow_remote"],
            "engines": dev.engines(),
            "kinds": [{"kind": k["kind"], "available": k["available"], "missing": k["missing"]}
                      for k in self.jobs.kind_list()],
            "jobs": self.jobs.counts()}
        self._facts_at = now
        return self._facts

    def info(self) -> dict[str, Any]:
        from .envelope import versions
        facts = self.probe.facts()
        settings = self.state.get()
        doc = self.catalog()
        counts = doc.get("counts") or {}
        return {
            "name": "tcmstudio", "version": __version__, "versions": versions(),
            "home": str(self.home), "platform": platform.platform(terse=True),
            "cpu": {"cores": facts["cpu"]["cores"], "model": facts["cpu"]["model"],
                    "arch": facts["cpu"].get("arch")},
            "memory_gb": facts["memory_gb"], "devices": facts["devices"],
            "engines": dev.engines(), "settings": settings, "network": settings["network"],
            "purpose": settings["purpose"],
            "counts": {"core": counts.get("core", len(doc.get("core", ()))),
                       "entries": counts.get("entries", len(doc.get("entries", ())))},
            "url": self.url, "token_required": self.guard.token_required,
            "web": self.web_root is not None,
            "jobs": {**self.jobs.counts(), "available": self.jobs.unavailable is None,
                     **({"note": self.jobs.unavailable} if self.jobs.unavailable else {})},
            "paths": {"data_lake": self.data_env["BIOAGENT_DATA_LAKE"],
                      "tcmdb": self.data_env["BIOAGENT_TCMDB"],
                      "workspace": self.data_env["BIOAGENT_WORKSPACE"],
                      "uploads": str(self.home / "uploads"),
                      "projects": str(self.home / "projects")},
            "notes": facts.get("notes") or []}

    def devices_doc(self, refresh: bool = False) -> dict[str, Any]:
        facts = self.probe.facts(refresh)
        settings = self.state.get()
        resolved, note = dev.resolve(settings["device"], facts["devices"])
        out = {"devices": facts["devices"], "selected": settings["device"], "resolved": resolved,
               "engine_options": dev.engine_options(resolved), "usage": self.probe.usage(),
               "torch": facts.get("torch"), "torch_probe": facts.get("torch_probe"),
               "notes": facts.get("notes") or []}
        if note:
            out["note"] = note
        return out

    # ------------------------------------------------------------------ calls
    def call(self, tool: str, arguments: Any, context: Mapping[str, Any]) -> dict[str, Any]:
        from .dispatch import call
        settings = self.state.get()
        ctx: dict[str, Any] = {
            "where": "runner", "network": settings["network"], "purpose": settings["purpose"],
            "state_root": str(self.home / "projects"), "device": "cpu", "jobs": self.hook,
            "tcmdb_root": self.data_env["BIOAGENT_TCMDB"], "capabilities": self.host_facts()}
        for key in ("project_id", "conversation_id"):
            value = context.get(key)
            if isinstance(value, str) and value.strip():
                ctx[key] = value.strip()[:200]
        if isinstance(context.get("approvals"), list):
            ctx["approvals"] = [str(a)[:100] for a in context["approvals"][:50]]
        def run() -> tuple[dict[str, Any], Any]:
            self.hook.take_refusal()
            return call(tool, arguments, ctx), self.hook.take_refusal()
        envelope, refusal = self.pool.submit(run).result()
        if refusal is not None:
            _restate_refusal(envelope, refusal)
        err = envelope.get("error") if isinstance(envelope, dict) else None
        net = settings["network"]
        if isinstance(err, dict) and err.get("type") == "network_off" and not (
                net.get("enabled") and net.get("profile") != "offline-analysis"):
            # Two switches gate the network: the project's web access (the page) and this
            # runner's own setting. Say which one is off.
            err["hint"] = ("This runner's network access is off: turn on 'Runner network' in "
                           "Settings → Compute, or start the runner with --network. "
                           + (err.get("hint") or "")).strip()
        return envelope

    # --------------------------------------------------------------- runtime
    def runtime_file(self, name: str) -> tuple[Path | bytes | None, str | None, str | None]:
        """A file of the in-browser runtime (``/runtime/<name>``) as (a path or the bytes, the
        media type, a problem). A built site's own ``runtime/`` comes first (``--web
        studio/_site``); otherwise ``tcmstudio.webbuild`` makes the files in memory from this
        installation's code, so the app served here gets a browser runtime too."""
        if self.web_root is not None:
            found = _confined(self.web_root / "runtime", name)
            if found is not None and found.is_file():
                return found, None, None
        try:
            import importlib
            webbuild = importlib.import_module("tcmstudio.webbuild")
            make = webbuild.runtime_file
        except (ImportError, AttributeError) as exc:
            if name == "catalog.json":          # the tools can still be offered
                return self._browser_catalog(), "application/json; charset=utf-8", None
            return None, None, (f"the in-browser runtime is not built here and cannot be made "
                                f"({exc}); build the site with `python3 studio/scripts/"
                                "build_web.py`, or use https://science.impf.ai")
        with self._runtime_lock:                # the first build takes a few seconds
            try:
                made = make(name)
            except Exception as exc:                            # noqa: BLE001
                return None, None, (f"making the in-browser runtime failed: "
                                    f"{type(exc).__name__}: {exc}")
        if made is None:
            return None, None, None
        data, media = made
        return data, media, None

    def _browser_catalog(self) -> Path:
        out = self.home / "cache" / "catalog.browser.json"
        if not out.is_file():
            from .catalog import build_catalog
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(build_catalog("browser", probe=False), ensure_ascii=False,
                                      separators=(",", ":")), encoding="utf-8")
        return out


_REFUSED_ZH = {"unavailable": "此处不可运行", "network_off": "未联网"}


def _restate_refusal(envelope: dict[str, Any], refusal: Any) -> None:
    """The dispatcher reports any exception from the job service as ``runtime_error``; a job
    the runner refused (a missing engine or dataset, the network off) is a refusal, and the
    envelope says so: its error type, message, remedy and summary."""
    err = envelope.get("error")
    if not isinstance(err, dict) or err.get("type") != "runtime_error":
        return
    kind = refusal.type if refusal.type in _REFUSED_ZH else "unavailable"
    old_message, old_summary = str(err.get("message") or ""), str(envelope.get("summary") or "")
    err.update(type=kind, message=refusal.message, hint=refusal.hint or err.get("hint") or "")
    if refusal.extra.get("missing"):
        err["missing"] = list(refusal.extra["missing"])
    title = old_summary.split("：", 1)[0] if "：" in old_summary else envelope.get("via", "")
    short = " ".join(refusal.message.split())
    short = short if len(short) <= 90 else short[:89] + "…"
    envelope["summary"] = f"{title}：{_REFUSED_ZH[kind]}（{short}）"
    text = str(envelope.get("text") or "")
    text = text.replace(old_summary, envelope["summary"])
    text = text.replace(f"Error (runtime_error): {old_message}",
                        f"Error ({kind}): {refusal.message}"
                        + (f"\nRemedy: {refusal.hint}" if refusal.hint else ""))
    envelope["text"] = text[:16000]


def _confined(base: Path, rel: str) -> Path | None:
    rel = (rel or "").replace("\\", "/").lstrip("/")
    if not rel or any(part in ("..", "") for part in rel.split("/")):
        return None
    try:
        root = base.resolve()
        target = (root / rel).resolve()
    except OSError:
        return None
    return target if root in target.parents else None


# ============================================================================== handler

class _Refused(Exception):
    def __init__(self, status: int, type_: str, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.status, self.type, self.message, self.extra = status, type_, message, extra


class Handler(BaseHTTPRequestHandler):
    server: RunnerServer
    protocol_version = "HTTP/1.1"
    server_version = "tcmstudio"
    sys_version = ""
    timeout = 300

    # ---------------------------------------------------------------- logging
    def log_message(self, format: str, *args: Any) -> None:     # noqa: A002
        # The request line can carry ?token=; bodies carry keys. Log neither.
        return

    def log_request(self, code: Any = "-", size: Any = "-") -> None:
        if self.server.config.quiet:
            return
        try:
            status = int(code)
        except (TypeError, ValueError):
            status = 0
        path = urlsplit(self.path).path
        if self.command in ("GET", "HEAD", "OPTIONS") and status < 400:
            return                     # reads and polls would drown what changed
        if path.startswith("/api/") or status >= 400:
            sys.stderr.write(f"{time.strftime('%H:%M:%S')} {self.command} {path} {status}\n")

    def log_error(self, format: str, *args: Any) -> None:       # noqa: A002
        return

    # --------------------------------------------------------------- plumbing
    def setup(self) -> None:
        super().setup()
        self._body_consumed = True

    def _cors(self) -> list[tuple[str, str]]:
        return self.server.guard.cors_headers(self.headers.get("Origin"), self.headers.get("Host"))

    def _start(self, status: int, headers: list[tuple[str, str]]) -> None:
        self.send_response(status)
        for k, v in self._cors():
            self.send_header(k, v)
        for k, v in headers:
            self.send_header(k, v)
        if not self._body_consumed:
            # an unread body would be read as the next request
            self.send_header("Connection", "close")
            self.close_connection = True
        self.end_headers()

    def _send(self, status: int, body: bytes, content_type: str, *,
              headers: list[tuple[str, str]] | None = None, cache: str = "no-store") -> None:
        extra = list(headers or [])
        accept = self.headers.get("Accept-Encoding") or ""
        if len(body) >= GZIP_MIN and "gzip" in accept and not content_type.startswith(
                ("image/", "application/gzip", "application/zip", "font/", "application/wasm")):
            body = gzip.compress(body, compresslevel=5)
            extra += [("Content-Encoding", "gzip"), ("Vary", "Accept-Encoding")]
        self._start(status, [("Content-Type", content_type), ("Content-Length", str(len(body))),
                             ("Cache-Control", cache), ("X-Content-Type-Options", "nosniff"),
                             *extra])
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, value: Any, status: int = 200, headers: list[tuple[str, str]] | None = None) -> None:
        body = json.dumps(value, ensure_ascii=False, default=str).encode("utf-8")
        self._send(status, body, JSON_TYPE, headers=headers)

    def _error(self, status: int, type_: str, message: str, **extra: Any) -> None:
        self._json({"error": {"type": type_, "message": message, **extra}}, status)

    def _length(self) -> int | None:
        raw = self.headers.get("Content-Length")
        if raw is None:
            return None
        try:
            n = int(raw)
        except ValueError:
            raise _Refused(400, "bad_request", "Content-Length is not a number") from None
        if n < 0:
            raise _Refused(400, "bad_request", "Content-Length is negative")
        return n

    def _body(self, limit: int = MAX_JSON) -> bytes:
        if "chunked" in (self.headers.get("Transfer-Encoding") or "").lower():
            return self._chunked(limit)
        n = self._length() or 0
        if n > limit:
            if n <= 8 * limit:
                # read it away so the client sees the answer instead of a broken connection
                left = n
                while left > 0:
                    chunk = self.rfile.read(min(1 << 20, left))
                    if not chunk:
                        break
                    left -= len(chunk)
                self._body_consumed = left == 0
            raise _Refused(413, "too_large", f"the request body is larger than {limit // 2**20} MB")
        data = self.rfile.read(n) if n else b""
        self._body_consumed = True
        return data

    def _chunked(self, limit: int) -> bytes:
        out = bytearray()
        while True:
            line = self.rfile.readline(1024)
            try:
                size = int(line.split(b";")[0].strip() or b"0", 16)
            except ValueError:
                raise _Refused(400, "bad_request", "bad chunked encoding") from None
            if size == 0:
                while self.rfile.readline(1024) not in (b"\r\n", b"\n", b""):
                    pass
                break
            if len(out) + size > limit:
                raise _Refused(413, "too_large", f"the request body is larger than {limit // 2**20} MB")
            out += self.rfile.read(size)
            self.rfile.readline(8)
        self._body_consumed = True
        return bytes(out)

    def _json_body(self) -> Any:
        data = self._body()
        if not data.strip():
            return {}
        try:
            return json.loads(data.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise _Refused(400, "bad_request", f"the body is not JSON: {exc}") from None

    def _query(self) -> dict[str, str]:
        return {k: v[-1] for k, v in parse_qs(urlsplit(self.path).query).items()}

    def _token(self) -> str | None:
        given = self.headers.get("X-TCM-Token")
        if given:
            return given.strip()
        if self.command in ("GET", "HEAD"):
            return self._query().get("token")
        return None

    # ------------------------------------------------------------------ verbs
    def do_OPTIONS(self) -> None:
        self._handle("OPTIONS")

    def do_GET(self) -> None:
        self._handle("GET")

    def do_HEAD(self) -> None:
        self._handle("HEAD")

    def do_POST(self) -> None:
        self._handle("POST")

    def do_PUT(self) -> None:
        self._handle("PUT")

    def do_DELETE(self) -> None:
        self._handle("DELETE")

    def _handle(self, method: str) -> None:
        length = self.headers.get("Content-Length")
        chunked = "chunked" in (self.headers.get("Transfer-Encoding") or "").lower()
        self._body_consumed = not (chunked or (length not in (None, "", "0")))
        try:
            self._route(method)
        except _Refused as exc:
            self._error(exc.status, exc.type, exc.message, **exc.extra)
        except JobError as exc:
            self._json({"error": exc.as_dict()}, exc.status)
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        except Exception as exc:                                # noqa: BLE001
            try:
                self._error(500, "runner_error", f"{type(exc).__name__}: {exc}"[:500])
            except OSError:
                self.close_connection = True

    def _route(self, method: str) -> None:
        guard = self.server.guard
        host = self.headers.get("Host")
        if not guard.host_ok(host):
            raise _Refused(421, "forbidden_host",
                           "this runner answers only to this machine's own names "
                           "(localhost, 127.0.0.1); the Host header named another")
        origin = self.headers.get("Origin")
        if method == "OPTIONS":
            status, headers = guard.preflight_headers(
                origin, host,
                (self.headers.get("Access-Control-Request-Private-Network") or "").lower() == "true",
                self.headers.get("Access-Control-Request-Headers"))
            self.send_response(status)
            for k, v in headers:
                self.send_header(k, v)
            self.send_header("Content-Length", "0")
            if not self._body_consumed:
                self.send_header("Connection", "close")
                self.close_connection = True
            self.end_headers()
            return
        if not guard.origin_allowed(origin, host):
            raise _Refused(403, "forbidden_origin",
                           f"the origin {origin} may not use this runner; start it with "
                           f"--allow-origin {origin} to allow it")
        path = unquote(urlsplit(self.path).path)
        if path == "/v1" or path.startswith("/v1/"):
            return self._no_relay(method, path)
        if not path.startswith("/api/"):
            if method not in ("GET", "HEAD"):
                raise _Refused(405, "method_not_allowed", f"{method} is not allowed here",
                               allow="GET, HEAD")
            return self._static(path)
        if path == "/api/health":
            if method not in ("GET", "HEAD"):
                raise _Refused(405, "method_not_allowed", "GET only")
            return self._json({"name": "tcmstudio", "version": __version__,
                               "token_required": guard.token_required,
                               "paired": guard.token_ok(self._token())})
        if not guard.token_ok(self._token()):
            self._json({"error": {"type": "token",
                                  "message": "this runner needs its pairing token (X-TCM-Token); "
                                             "open the pairing link the runner printed, or enter "
                                             "the token in Settings → Compute"},
                        "token_required": True}, 401)
            return
        self._api(method, path)

    # -------------------------------------------------------------------- api
    def _api(self, method: str, path: str) -> None:
        srv = self.server
        parts = path.strip("/").split("/")[1:]          # after "api"
        head = parts[0] if parts else ""

        def allow(*methods: str) -> None:
            if method not in methods and not (method == "HEAD" and "GET" in methods):
                raise _Refused(405, "method_not_allowed", f"{method} is not allowed on {path}",
                               allow=", ".join(methods))

        if head == "info" and len(parts) == 1:
            allow("GET")
            return self._json(srv.info())
        if head == "catalog" and len(parts) == 1:
            allow("GET")
            return self._json(srv.catalog(refresh=self._query().get("refresh") == "1"))
        if head == "devices" and len(parts) == 1:
            allow("GET")
            return self._json(srv.devices_doc(refresh=self._query().get("refresh") == "1"))
        if head == "settings" and len(parts) == 1:
            allow("GET", "PUT")
            if method == "PUT":
                body = self._json_body()
                if not isinstance(body, dict):
                    raise _Refused(400, "bad_arguments", "settings must be a JSON object")
                try:
                    settings = srv.state.update(body)
                except SettingsError as exc:
                    raise _Refused(400, "bad_arguments", str(exc)) from None
                srv._facts = None
                srv.jobs.wake()
                return self._json(settings)
            return self._json(srv.state.get())
        if head == "kinds" and len(parts) == 1:
            allow("GET")
            return self._json(srv.jobs.kind_list())
        if head == "call" and len(parts) == 1:
            allow("POST")
            return self._call()
        if head == "jobs":
            return self._jobs(method, parts[1:])
        if head == "uploads":
            return self._uploads(method, parts[1:])
        if head == "llm":
            if len(parts) == 1:
                allow("POST")
                return self._llm()
            if len(parts) == 2 and parts[1] == "local":
                allow("GET")
                return self._json(probe_local(srv.config.local_servers, srv.config.local_timeout))
        raise _Refused(404, "not_found", f"no endpoint {path}")

    def _call(self) -> None:
        body = self._json_body()
        if not isinstance(body, dict):
            raise _Refused(400, "bad_request", "the body must be {tool, arguments, context}")
        tool = body.get("tool")
        if not isinstance(tool, str) or not tool.strip():
            raise _Refused(400, "bad_arguments", "name the tool: {\"tool\": \"tcm_herb\", "
                                                 "\"arguments\": {…}}")
        context = body.get("context") if isinstance(body.get("context"), dict) else {}
        envelope = self.server.call(tool.strip(), body.get("arguments"), context)
        self._json(envelope)

    # -------------------------------------------------------------------- jobs
    def _jobs(self, method: str, rest: list[str]) -> None:
        jobs = self.server.jobs
        if not rest:
            if method == "POST":
                body = self._json_body()
                if not isinstance(body, dict):
                    raise _Refused(400, "bad_request", "the body must be {kind, params}")
                params = body.get("params") if body.get("params") is not None else {}
                if not isinstance(params, dict):
                    raise _Refused(400, "bad_arguments", "params must be an object")
                job, created = jobs.submit(body.get("kind"), params,
                                           project_id=body.get("project_id") or None,
                                           submission_id=body.get("submission_id") or None)
                return self._json(job, 201 if created else 200,
                                  headers=[("Location", f"/api/jobs/{job['id']}")])
            if method in ("GET", "HEAD"):
                q = self._query()
                state = q.get("state") or None
                return self._json({"jobs": jobs.list(q.get("project_id") or None, state)})
            raise _Refused(405, "method_not_allowed", "GET or POST", allow="GET, POST")
        job_id = rest[0]
        if len(rest) == 1:
            if method in ("GET", "HEAD"):
                wait = self._query().get("wait_s")
                try:
                    wait_s = max(0.0, min(float(wait), 60.0)) if wait else 0.0
                except ValueError:
                    wait_s = 0.0
                return self._json(jobs.get(job_id, wait_s))
            if method == "DELETE":
                return self._json(jobs.cancel(job_id))
            raise _Refused(405, "method_not_allowed", "GET or DELETE", allow="GET, DELETE")
        if method not in ("GET", "HEAD"):
            raise _Refused(405, "method_not_allowed", "GET only", allow="GET")
        if rest[1] == "events" and len(rest) == 2:
            return self._events(job_id)
        if rest[1] == "files":
            if len(rest) == 2:
                job = jobs.get(job_id)
                return self._json({"files": jobs.files(job_id), "state": job["state"]})
            return self._file(job_id, "/".join(rest[2:]))
        raise _Refused(404, "not_found", "no such job endpoint")

    def _events(self, job_id: str) -> None:
        srv = self.server
        stream = iter_events(srv.jobs, job_id, keepalive_s=srv.config.keepalive_s,
                             stop=srv.stopping)
        try:
            first = next(stream)                   # raises UnknownJob before any header is sent
        except StopIteration:                                    # pragma: no cover
            raise _Refused(404, "not_found", f"no job {job_id}") from None
        self.close_connection = True
        self._start(200, [("Content-Type", "text/event-stream; charset=utf-8"),
                          ("Cache-Control", "no-store"), ("X-Accel-Buffering", "no"),
                          ("Connection", "close")])
        if self.command == "HEAD":
            stream.close()
            return
        try:
            self.wfile.write(b"retry: 3000\n\n")
            for event, data in [first, *_rest(stream)]:
                if event == "keepalive":
                    self.wfile.write(b": keep-alive\n\n")
                else:
                    payload = json.dumps(data, ensure_ascii=False, default=str)
                    self.wfile.write(f"event: {event}\ndata: {payload}\n\n".encode("utf-8"))
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            stream.close()

    def _file(self, job_id: str, rel: str) -> None:
        jobs = self.server.jobs
        path = jobs.file_path(job_id, rel)
        if path is None:
            raise _Refused(404, "not_found", f"no file {rel!r} in job {job_id}'s outputs")
        kind = media_type(path.name)
        if kind.startswith("text/") or kind in ("application/json", "application/x-ndjson"):
            kind += "; charset=utf-8"
        name = quote(path.name)
        disposition = "attachment" if self._query().get("download") == "1" else "inline"
        headers = [("Content-Type", kind), ("Content-Length", str(path.stat().st_size)),
                   ("Cache-Control", "no-cache"), ("X-Content-Type-Options", "nosniff"),
                   ("Content-Disposition", f"{disposition}; filename*=UTF-8''{name}"),
                   ("Cross-Origin-Resource-Policy", "cross-origin")]
        if path.suffix.lower() in (".html", ".htm", ".svg", ".xhtml"):
            # a report is shown, never run: no scripts, no same-origin access
            headers += [("Content-Security-Policy", "sandbox"),
                        ("Cross-Origin-Embedder-Policy", "require-corp")]
        self._start(200, headers)
        if self.command != "HEAD":
            with path.open("rb") as fh:
                shutil.copyfileobj(fh, self.wfile, 1 << 20)

    # ----------------------------------------------------------------- uploads
    def _uploads(self, method: str, rest: list[str]) -> None:
        uploads = self.server.uploads
        if not rest:
            if method == "POST":
                if "chunked" in (self.headers.get("Transfer-Encoding") or "").lower():
                    raise _Refused(411, "length_required", "send the file with a Content-Length")
                length = self._length()
                if length is None:
                    raise _Refused(411, "length_required", "send the file with a Content-Length")
                if length > Uploads.MAX_BYTES:
                    raise _Refused(413, "too_large", "an upload is at most 4 GB")
                name = unquote(self.headers.get("X-Filename") or "") or "upload.bin"
                project = self.headers.get("X-Project-Id") or self._query().get("project_id")
                try:
                    meta, created = uploads.save(self.rfile, length, name,
                                                 self.headers.get("Content-Type") or "",
                                                 project_id=project)
                except ValueError as exc:
                    self.close_connection = True
                    raise _Refused(400, "bad_request", str(exc)) from None
                finally:
                    self._body_consumed = True
                return self._json(meta, 201 if created else 200)
            if method in ("GET", "HEAD"):
                return self._json({"uploads": uploads.list()})
            raise _Refused(405, "method_not_allowed", "GET or POST", allow="GET, POST")
        if len(rest) == 1:
            if method in ("GET", "HEAD"):
                meta = uploads.get(rest[0])
                if meta is None:
                    raise _Refused(404, "not_found", f"no upload {rest[0]}")
                return self._json(meta)
            if method == "DELETE":
                if not uploads.delete(rest[0]):
                    raise _Refused(404, "not_found", f"no upload {rest[0]}")
                return self._json({"deleted": rest[0]})
        raise _Refused(404, "not_found", "no such upload endpoint")

    # --------------------------------------------------------------------- llm
    def _llm(self) -> None:
        proxy = self.server.proxy
        try:
            proxy.check(self.headers.get("X-TCM-Target"))
        except ProxyRefused as exc:
            raise _Refused(exc.status, exc.type, exc.message) from None
        body = self._body(MAX_JSON)
        try:
            proxy.forward(self, body, self._cors())
        except ProxyRefused as exc:
            raise _Refused(exc.status, exc.type, exc.message) from None

    # ------------------------------------------------------------------ /v1
    def _no_relay(self, method: str, path: str) -> None:
        # The app served from here looks for Tao-S1 on its own origin (web-core's relayBaseUrl) unless the port is
        # the default 8765, whose pages go to science.impf.ai. The runner never relays Tao-S1 (it holds no key, and
        # the relay admits pages by origin), so it says so as a health answer the page can show, not as a 404.
        message = NO_RELAY_MESSAGE
        if path == "/v1/health" and method in ("GET", "HEAD"):
            return self._json({"ok": False, "service": "tcmstudio", "relay": False, "version": __version__,
                               "model": "Tao-S1", "models": [], "max_output_tokens": None, "limits": None,
                               "error": {"type": "not_relay", "message": message}})
        raise _Refused(404, "not_relay", message)

    # ------------------------------------------------------------------ static
    def _static(self, path: str) -> None:
        srv = self.server
        if path.startswith("/runtime/"):
            found, media, problem = srv.runtime_file(path[len("/runtime/"):])
            if found is None:
                if problem:
                    raise _Refused(503, "unavailable", problem)
                raise _Refused(404, "not_found", f"no runtime file {path}")
            # the bundle's name carries its digest: it never changes under that name
            name = path.rsplit("/", 1)[-1]
            immutable = name.endswith((".tar.gz", ".whl", ".zip")) and name.count(".") >= 2
            cache = "public, max-age=31536000, immutable" if immutable else "no-cache"
            if isinstance(found, bytes):
                return self._serve_bytes(found, media or media_type(name), cache=cache)
            return self._serve_file(found, cache=cache)
        root = srv.web_root
        if root is None:
            raise _Refused(404, "not_found", "this runner serves no web app (run it from a "
                                             "TCMScience checkout, or pass --web DIR); the app "
                                             "is at https://science.impf.ai")
        rel = path.lstrip("/") or "index.html"
        target = _confined(root, rel) if rel != "index.html" else root / "index.html"
        if target is not None and target.is_dir():
            target = target / "index.html"
        if target is None or not target.is_file():
            if self._is_navigation():
                target = root / "index.html"      # History-API routes: the app decides
            else:
                raise _Refused(404, "not_found", f"no file {path}")
        self._serve_file(target, cache="no-cache")

    def _is_navigation(self) -> bool:
        mode = self.headers.get("Sec-Fetch-Mode")
        if mode:
            return mode == "navigate"
        return "text/html" in (self.headers.get("Accept") or "")

    def _serve_bytes(self, data: bytes, kind: str, *, cache: str) -> None:
        self._start(200, [("Content-Type", kind), ("Content-Length", str(len(data))),
                          ("Cache-Control", cache), ("X-Content-Type-Options", "nosniff"),
                          *ISOLATION_HEADERS])
        if self.command != "HEAD":
            self.wfile.write(data)

    def _serve_file(self, path: Path, *, cache: str) -> None:
        kind = STATIC_TYPES.get(path.suffix.lower()) or media_type(path.name)
        size = path.stat().st_size
        self._start(200, [("Content-Type", kind), ("Content-Length", str(size)),
                          ("Cache-Control", cache), ("X-Content-Type-Options", "nosniff"),
                          *ISOLATION_HEADERS])
        if self.command != "HEAD":
            with path.open("rb") as fh:
                shutil.copyfileobj(fh, self.wfile, 1 << 20)


def _rest(gen: Any) -> Any:
    yield from gen


# ============================================================================= the command

def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.description = ("The TCMScience Studio local runner: tools, jobs and models on this "
                          "machine's CPU and GPU, for https://science.impf.ai.")
    parser.add_argument("--host", default="127.0.0.1",
                        help="address to listen on (default 127.0.0.1: this machine only)")
    parser.add_argument("--port", type=int, default=8765,
                        help="port (default 8765; 0 lets the system pick a free one)")
    parser.add_argument("--home", default=str(DEFAULT_HOME),
                        help="runner home: settings, token, jobs, uploads, project audit chains, "
                             "data (default ~/.tcmscience/studio)")
    parser.add_argument("--web", default="auto",
                        help="serve the web app from DIR (default: the checkout's studio/web; "
                             "'none' for the API only)")
    parser.add_argument("--allow-origin", action="append", default=[], metavar="URL",
                        help="also accept requests from this page origin (repeatable)")
    parser.add_argument("--allow-host", action="append", default=[], metavar="HOST",
                        help="let the model proxy forward to this host or URL (repeatable)")
    parser.add_argument("--no-token", action="store_true",
                        help="do not require the pairing token (only when bound to loopback)")
    parser.add_argument("--no-browser", action="store_true",
                        help="do not open the pairing link in a browser")
    parser.add_argument("--device", default=None,
                        help="device for jobs: auto, cpu, cuda:N, rocm:N or mps (saved)")
    parser.add_argument("--threads", type=int, default=None,
                        help="CPU threads per job (saved)")
    parser.add_argument("--max-jobs", type=int, default=None,
                        help="jobs running at once (saved)")
    parser.add_argument("--network", action="store_true",
                        help="turn on the runner's network access (saved; connectors, "
                             "downloads); each project still needs its own web access")
    parser.add_argument("--quiet", action="store_true", help="log nothing per request")


def _config_from_args(args: argparse.Namespace) -> ServerConfig:
    web: Path | None | str = args.web
    if isinstance(web, str) and web.lower() == "none":
        web = None
    elif web != "auto":
        web = Path(web)
    for origin in args.allow_origin:
        normalize_origin(origin)                 # refuse a bad one before binding
    return ServerConfig(host=args.host, port=args.port, home=Path(args.home).expanduser(),
                        web=web, allow_origins=list(args.allow_origin),
                        allow_hosts=list(args.allow_host), no_token=args.no_token,
                        device=args.device, threads=args.threads, max_jobs=args.max_jobs,
                        network=args.network, quiet=args.quiet,
                        open_browser=not args.no_browser)


def make_server(config: ServerConfig) -> RunnerServer:
    return RunnerServer(config)


def _lan_address() -> str | None:
    import socket
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("192.0.2.1", 9))                  # TEST-NET-1: routed, nothing sent
            address = sock.getsockname()[0]
    except OSError:
        return None
    return None if address.startswith("127.") else address


def _banner(server: RunnerServer, env_set: Mapping[str, str]) -> str:
    from .envelope import versions
    v = versions()
    settings = server.state.get()
    facts = server.probe.facts()
    gpus = [d for d in facts["devices"] if d["id"] != "cpu"]
    resolved, _ = dev.resolve(settings["device"], facts["devices"])
    cpu = facts["cpu"]
    lines = [
        f"TCMScience Studio 本机 Runner / local runner  tcmstudio {v['tcmstudio']} · "
        f"bioagent {v['bioagent']} · psh {v['psh']} · Python {v['python']}",
        "",
        f"  配对链接 Pair      {server.pairing_link()}",
        f"  本机地址 Local     {server.url}/" + ("" if server.web_root else
                                               "   (API only: no web app found)"),
    ]
    if server.web_root is not None:
        # the app served here connects itself when opened through this link (any port, token or not)
        lines.append(f"  本机页面 Local app {pairing_link(server.url, server.guard.token, server.url)}")
    if server.guard.token:
        lines.append(f"  配对令牌 Token     {server.guard.token}")
        lines.append("                     只在你自己的浏览器中使用 · keep it to your own browser")
    else:
        lines.append("  配对令牌 Token     不需要 / not required (--no-token, this machine only)")
    if server.config.host in ("0.0.0.0", "::") and (lan := _lan_address()):
        lines.append(f"  局域网 LAN        http://{lan}:{server.port}/  (pair by address and token)")
    lines += [
        f"  主目录 Home       {server.home}",
        f"  设备 Device       {settings['device']} → {resolved} · CPU {cpu.get('cores')} 核 cores"
        + (f" · {cpu['model']}" if cpu.get("model") else "")
        + (f" · {facts['memory_gb']} GB" if facts.get("memory_gb") else ""),
    ]
    for g in gpus:
        mem = f" · {g['memory_gb']} GB" if g.get("memory_gb") else ""
        lines.append(f"                    {g['id']}: {g.get('name')}{mem}"
                     + ("" if g.get("available") else " (unavailable)"))
    net = settings["network"]
    lines.append("  联网 Network      " + ("开启 on (" + net["profile"] + ")" if net["enabled"] else
                                         "关闭 off · turn on in Settings → Compute, or --network"))
    jobs = server.jobs
    lines.append(f"  任务 Jobs         {settings['max_jobs']} at a time · {settings['threads']} "
                 "threads each" + (f" · {jobs.reattached} re-attached" if jobs.reattached else "")
                 + (f" · unavailable: {jobs.unavailable}" if jobs.unavailable else ""))
    if env_set:
        lines.append(f"  数据 Data         {env_set.get('BIOAGENT_DATA_LAKE')}")
    for note in server.state.notes:
        lines.append(f"  注意 Note         {note}")
    lines += ["",
              "  在浏览器打开配对链接即可连接。Open the pairing link to connect Studio to this runner.",
              "  Ctrl+C 停止 stop · 运行中的任务会继续，下次启动时重新接管 · running jobs keep "
              "running and are re-attached at the next start."]
    return "\n".join(lines)


def run(args: argparse.Namespace) -> int:
    try:
        config = _config_from_args(args)
        if not 0 <= config.port < 65536:          # 0: the system picks a free port
            raise SecurityError(f"--port {config.port} is not a port number")
    except SecurityError as exc:
        print(f"tcmstudio serve: {exc}", file=sys.stderr)
        return 2
    prepare_home(config.home)
    env_set = apply_environment(config.home)
    try:
        server = make_server(config)
    except ValueError as exc:            # SecurityError, SettingsError, a bad --allow-host
        print(f"tcmstudio serve: {exc}", file=sys.stderr)
        return 2
    except HomeInUse as exc:
        print(f"tcmstudio serve: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"tcmstudio serve: cannot listen on {config.host}:{config.port} ({exc.strerror or exc}). "
              "Is another runner already running? Use --port to pick another port.", file=sys.stderr)
        return 1
    server.start_background()
    print(_banner(server, env_set), file=sys.stderr, flush=True)
    if not is_loopback_host(config.host) and config.host not in ("0.0.0.0", "::"):
        print("  注意 Note: the pairing link works only for a loopback runner; pair this one "
              "by its address and token.", file=sys.stderr)
    if config.open_browser:
        threading.Timer(0.5, lambda: _open(server.pairing_link())).start()

    def stop(signum: int, frame: Any) -> None:
        del signum, frame
        threading.Thread(target=server.shutdown, daemon=True).start()
    previous = {}
    for sig in (signal.SIGTERM, getattr(signal, "SIGHUP", None)):
        if sig is not None:
            with contextlib.suppress(ValueError, OSError):      # not the main thread
                previous[sig] = signal.signal(sig, stop)
    try:
        server.serve_forever(poll_interval=0.3)
    except KeyboardInterrupt:
        pass
    finally:
        running = server.jobs.counts().get("running", 0)
        server.close()
        for sig, handler in previous.items():
            with contextlib.suppress(ValueError, OSError):
                signal.signal(sig, handler)
        print("\ntcmstudio: stopped" + (f"; {running} job(s) keep running and will be "
                                         "re-attached at the next start" if running else ""),
              file=sys.stderr)
    return 0


def _open(url: str) -> None:
    with contextlib.suppress(Exception):
        webbrowser.open(url)

