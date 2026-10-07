"""The model proxy (``POST /api/llm``) and the local model probe (``GET /api/llm/local``).

Some providers refuse calls from a web page (CORS), and local model servers often answer
only their own origin. The page then sends the request to the runner with the full target
URL in ``X-TCM-Target``; the runner forwards the body and an allowlist of headers, and
streams the answer back as it arrives. The key travels in the request's own
``Authorization`` / ``x-api-key`` header and is neither stored nor logged.

Targets: loopback addresses (any port), the providers Studio offers (OpenAI, Anthropic,
DeepSeek, DashScope, Moonshot, Zhipu, SiliconFlow, OpenRouter) over HTTPS, and hosts named
with ``--allow-host``. Anything else is refused before a connection is made. When the page
stops reading (the user pressed stop, or closed the tab), the upstream connection is closed
at once, so the provider stops generating.
"""

from __future__ import annotations

import base64
import http.client
import json
import select
import socket
import ssl
import threading
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Iterable, Mapping
from urllib.parse import unquote, urlsplit

from .security import is_loopback_host

__all__ = ["PROVIDER_HOSTS", "FORWARD_HEADERS", "RESPONSE_HEADERS", "LOCAL_SERVERS",
           "ProxyRefused", "Target", "LLMProxy", "parse_allow_host", "probe_local"]

#: Hosts of the providers Studio's presets name (``web/js/core/providers.js``), HTTPS only.
PROVIDER_HOSTS = frozenset({
    "api.openai.com", "api.anthropic.com", "api.deepseek.com", "dashscope.aliyuncs.com",
    "dashscope-intl.aliyuncs.com", "api.moonshot.cn", "api.moonshot.ai", "open.bigmodel.cn",
    "api.siliconflow.cn", "api.siliconflow.com", "openrouter.ai"})
#: Request headers forwarded to the target; everything else (cookies, the runner's token,
#: the page's origin) stays here.
FORWARD_HEADERS = ("content-type", "accept", "authorization", "x-api-key", "api-key",
                   "anthropic-version", "anthropic-beta",
                   "anthropic-dangerous-direct-browser-access", "http-referer", "x-title",
                   "openai-organization", "openai-project")
#: Response headers passed back to the page.
RESPONSE_HEADERS = ("content-type", "retry-after", "x-request-id", "request-id",
                    "x-ratelimit-remaining-requests", "x-ratelimit-remaining-tokens")
MAX_BODY = 8 * 2**20

#: Local model servers and where they listen by default.
LOCAL_SERVERS: tuple[dict[str, Any], ...] = (
    {"id": "ollama", "label": "Ollama", "base_url": "http://127.0.0.1:11434/v1",
     "probe": ("http://127.0.0.1:11434/api/tags", "http://127.0.0.1:11434/v1/models")},
    {"id": "lmstudio", "label": "LM Studio", "base_url": "http://127.0.0.1:1234/v1",
     "probe": ("http://127.0.0.1:1234/v1/models",)},
    {"id": "vllm", "label": "vLLM", "base_url": "http://127.0.0.1:8000/v1",
     "probe": ("http://127.0.0.1:8000/v1/models",)},
    {"id": "llamacpp", "label": "llama.cpp", "base_url": "http://127.0.0.1:8080/v1",
     "probe": ("http://127.0.0.1:8080/v1/models",)},
)


class ProxyRefused(Exception):
    def __init__(self, status: int, type_: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.type = type_
        self.message = message


@dataclass(frozen=True)
class Target:
    url: str
    scheme: str
    host: str
    port: int
    path: str            # path and query, as sent

    @property
    def loopback(self) -> bool:
        return is_loopback_host(self.host)


def parse_allow_host(value: str) -> tuple[str, str, int]:
    """``--allow-host``: ``api.example.com`` (HTTPS, 443), ``https://api.example.com:8443`` or
    ``http://10.0.0.5:8000`` → (scheme, host, port)."""
    raw = (value or "").strip()
    parts = urlsplit(raw if "://" in raw else f"https://{raw}")
    if parts.scheme not in ("http", "https") or not parts.hostname or "*" in raw:
        raise ValueError(f"--allow-host: {value!r} is not a host name or a URL")
    return parts.scheme, parts.hostname.lower(), parts.port or (443 if parts.scheme == "https" else 80)


def _proxy_for(target: Target) -> tuple[str, int, str | None] | None:
    """The HTTP proxy this process would use for the target (environment variables), with
    its Proxy-Authorization; None for a direct connection. Loopback is always direct."""
    if target.loopback:
        return None
    proxies = urllib.request.getproxies()
    proxy = proxies.get(target.scheme) or proxies.get("all")
    if not proxy:
        return None
    try:
        if urllib.request.proxy_bypass(target.host):
            return None
    except OSError:                                              # pragma: no cover
        pass
    parts = urlsplit(proxy if "://" in proxy else f"http://{proxy}")
    if not parts.hostname:
        return None
    auth = None
    if parts.username:
        cred = f"{unquote(parts.username)}:{unquote(parts.password or '')}"
        auth = "Basic " + base64.b64encode(cred.encode("utf-8")).decode("ascii")
    return parts.hostname, parts.port or 8080, auth


class LLMProxy:
    def __init__(self, allow_hosts: Iterable[str] = (), *, connect_timeout: float = 15.0,
                 read_timeout: float = 600.0, enabled: bool = True) -> None:
        self.allowed = {parse_allow_host(h) for h in allow_hosts}
        self.connect_timeout = connect_timeout
        self.read_timeout = read_timeout
        self.enabled = enabled

    # ----------------------------------------------------------------- checks
    def check(self, url: str | None) -> Target:
        url = (url or "").strip()
        if not url:
            raise ProxyRefused(400, "bad_request", "X-TCM-Target is missing: name the full URL "
                                                   "of the model endpoint")
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ProxyRefused(400, "bad_request", f"X-TCM-Target {url!r} is not an http(s) URL")
        if parts.username or parts.password:
            raise ProxyRefused(400, "bad_request", "X-TCM-Target must not carry credentials; "
                                                   "send the key in its header")
        host = parts.hostname.lower()
        try:
            port = parts.port or (443 if parts.scheme == "https" else 80)
        except ValueError:
            raise ProxyRefused(400, "bad_request", f"X-TCM-Target {url!r} has a bad port") from None
        path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        target = Target(url, parts.scheme, host, port, path)
        ok = (target.loopback
              or (parts.scheme == "https" and port == 443 and host in PROVIDER_HOSTS)
              or (parts.scheme, host, port) in self.allowed)
        if not ok:
            raise ProxyRefused(403, "forbidden_target",
                               f"the runner forwards model calls only to this machine, to the "
                               f"providers Studio lists, and to hosts named with --allow-host; "
                               f"not to {parts.scheme}://{parts.netloc}. Restart the runner with "
                               f"--allow-host {parts.scheme}://{parts.netloc} to allow it.")
        return target

    # ------------------------------------------------------------- connecting
    def _connect(self, target: Target) -> http.client.HTTPConnection:
        via = _proxy_for(target)
        context = ssl.create_default_context() if target.scheme == "https" else None
        if via is None:
            if context is not None:
                return http.client.HTTPSConnection(target.host, target.port,
                                                   timeout=self.connect_timeout, context=context)
            return http.client.HTTPConnection(target.host, target.port,
                                              timeout=self.connect_timeout)
        host, port, auth = via
        headers = {"Proxy-Authorization": auth} if auth else None
        if context is not None:
            conn = http.client.HTTPSConnection(host, port, timeout=self.connect_timeout,
                                               context=context)
            conn.set_tunnel(target.host, target.port, headers=headers)
            return conn
        conn = http.client.HTTPConnection(host, port, timeout=self.connect_timeout)
        conn._tcm_absolute = True                 # type: ignore[attr-defined]
        if auth:
            conn._tcm_proxy_auth = auth           # type: ignore[attr-defined]
        return conn

    def open(self, target: Target, body: bytes, headers: Mapping[str, str]
             ) -> tuple[http.client.HTTPConnection, http.client.HTTPResponse]:
        """Send the request; returns the connection and the response (headers read)."""
        conn = self._connect(target)
        send = {k: v for k in FORWARD_HEADERS if (v := headers.get(k)) is not None}
        send.setdefault("content-type", "application/json")
        send["content-length"] = str(len(body))
        path = target.path
        if getattr(conn, "_tcm_absolute", False):
            path = target.url
            if getattr(conn, "_tcm_proxy_auth", None):
                send["proxy-authorization"] = conn._tcm_proxy_auth  # type: ignore[attr-defined]
        try:
            conn.request("POST", path, body=body, headers=send)
            if conn.sock is not None:
                conn.sock.settimeout(self.read_timeout)
            response = conn.getresponse()
        except BaseException:
            conn.close()
            raise
        return conn, response

    # -------------------------------------------------------------- forwarding
    def forward(self, handler: Any, body: bytes, cors: list[tuple[str, str]]) -> None:
        """Forward the handler's request and stream the answer back (chunked)."""
        target = self.check(handler.headers.get("X-TCM-Target"))
        headers = {k.lower(): v for k, v in handler.headers.items()}
        try:
            conn, upstream = self.open(target, body, headers)
        except ProxyRefused:
            raise
        except (OSError, http.client.HTTPException, ssl.SSLError) as exc:
            raise ProxyRefused(502, "upstream_unreachable",
                               f"cannot reach {target.host}:{target.port}: "
                               f"{type(exc).__name__}: {exc}") from None
        client = handler.connection
        stop = threading.Event()
        gone = threading.Event()

        def watch() -> None:
            # A page that stops reading closes its connection; close the upstream then, so the
            # provider stops generating instead of finishing an answer nobody reads.
            while not stop.is_set():
                try:
                    ready, _, _ = select.select([client], [], [], 0.25)
                except (OSError, ValueError):
                    break
                if not ready:
                    continue
                try:
                    peek = client.recv(1, socket.MSG_PEEK)
                except (BlockingIOError, InterruptedError):
                    continue
                except OSError:
                    peek = b""
                if peek == b"":
                    gone.set()
                    try:
                        if conn.sock is not None:
                            conn.sock.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                return
        watcher = threading.Thread(target=watch, name="tcmstudio-llm-watch", daemon=True)
        try:
            handler.send_response(upstream.status)
            for k, v in cors:
                handler.send_header(k, v)
            for name in RESPONSE_HEADERS:
                value = upstream.getheader(name)
                if value:
                    handler.send_header(name.title() if name != "content-type" else "Content-Type",
                                        value)
            if not upstream.getheader("content-type"):
                handler.send_header("Content-Type", "application/octet-stream")
            handler.send_header("Cache-Control", "no-store")
            handler.send_header("X-Accel-Buffering", "no")
            handler.send_header("Transfer-Encoding", "chunked")
            handler.end_headers()
            watcher.start()
            while True:
                try:
                    chunk = upstream.read1(65536)
                except (OSError, http.client.HTTPException, ValueError):
                    break
                if not chunk:
                    break
                handler.wfile.write(f"{len(chunk):X}\r\n".encode("ascii") + chunk + b"\r\n")
                handler.wfile.flush()
            if not gone.is_set():
                handler.wfile.write(b"0\r\n\r\n")
                handler.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            gone.set()
        finally:
            stop.set()
            try:
                upstream.close()
            finally:
                conn.close()
            if gone.is_set():
                handler.close_connection = True


# ------------------------------------------------------------------------- local probe

def _get_json(url: str, timeout: float) -> Any:
    parts = urlsplit(url)
    conn = http.client.HTTPConnection(parts.hostname, parts.port or 80, timeout=timeout)
    try:
        conn.request("GET", parts.path or "/", headers={"Accept": "application/json"})
        res = conn.getresponse()
        data = res.read(2 * 2**20)
        if res.status != 200:
            raise ValueError(f"HTTP {res.status}")
        try:
            return json.loads(data.decode("utf-8", errors="replace"))
        except ValueError:
            return None                   # answered, but not with JSON
    finally:
        conn.close()


def _models_of(doc: Any) -> list[str] | None:
    """Model names from an OpenAI ``/v1/models`` list or Ollama's ``/api/tags``."""
    if not isinstance(doc, Mapping):
        return None
    if isinstance(doc.get("models"), list):                     # ollama /api/tags
        names = [str(m.get("name") or m.get("model")) for m in doc["models"]
                 if isinstance(m, Mapping) and (m.get("name") or m.get("model"))]
        return names
    if isinstance(doc.get("data"), list):                       # OpenAI-compatible
        return [str(m.get("id")) for m in doc["data"] if isinstance(m, Mapping) and m.get("id")]
    return None


def _probe_one(server: Mapping[str, Any], timeout: float) -> dict[str, Any]:
    out: dict[str, Any] = {"id": server["id"], "label": server.get("label", server["id"]),
                           "base_url": server["base_url"], "ok": False, "models": []}
    errors = []
    for url in server["probe"]:
        try:
            models = _models_of(_get_json(url, timeout))
        except ConnectionRefusedError:
            errors.append("not running")
            break                         # nothing listens on that port: no second probe
        except (OSError, ValueError, http.client.HTTPException) as exc:
            errors.append(f"{type(exc).__name__}: {exc}"[:200])
            continue
        if models is None:
            errors.append("answered, but not with a model list (another program on this port?)")
            continue
        out.update(ok=True, models=models, probed=url)
        return out
    out["error"] = errors[0] if errors else "no answer"
    return out


def probe_local(servers: Iterable[Mapping[str, Any]] = LOCAL_SERVERS,
                timeout: float = 1.5) -> dict[str, Any]:
    """Which local model servers answer, and their models; probed in parallel."""
    servers = list(servers)
    if not servers:
        return {"servers": []}
    with ThreadPoolExecutor(max_workers=len(servers)) as pool:
        results = list(pool.map(lambda s: _probe_one(s, timeout), servers))
    return {"servers": results}
