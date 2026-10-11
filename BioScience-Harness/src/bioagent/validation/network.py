"""Is a host reachable from here? Answered by sending the request, never by a setting.

A runner or sandbox whose network switch is on may still reach nothing: an egress proxy can
refuse a host, a name can fail to resolve, a server can be down or rate-limiting. A
setting says what is *allowed*; only a request says what *works*. :func:`probe` sends one
(``HEAD``, or a one-byte ranged ``GET`` for a server that refuses ``HEAD``) and
:func:`classify` names what went wrong precisely, so a report says "the proxy refused
bidd.group" instead of "network error".

The statuses follow :mod:`bioagent.validation`: an answer from the server is PASS; a
condition outside the code (a policy, DNS, a timeout, a server error, a rate limit, a
credential) is BLOCKED; an answer that shows the reviewed URL itself is wrong (404, a
redirect the downloader would refuse) is FAIL. Probes are paced per host with the
downloader's pacer, so a validation run is as polite as a download.
"""

from __future__ import annotations

import http.client
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from typing import Any

from . import BLOCKED, FAIL, PASS

__all__ = ["Probe", "probe", "classify"]

_KEEP_HEADERS = ("content-length", "content-range", "accept-ranges", "content-type",
                 "last-modified", "etag", "content-encoding")
_POLICY_WORDS = ("sandbox", "proxy", "policy", "egress", "not allowed", "blocked")


@dataclass
class Probe:
    url: str
    host: str
    status: str
    reason: str
    method: str = "HEAD"
    http_status: int | None = None
    final_url: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    elapsed_ms: int = 0

    @property
    def size(self) -> int | None:
        """The resource's size, from Content-Range (a ranged answer) or Content-Length."""
        cr = self.headers.get("content-range", "")
        if "/" in cr and cr.rsplit("/", 1)[1].isdigit():
            return int(cr.rsplit("/", 1)[1])
        cl = self.headers.get("content-length", "")
        if self.method == "HEAD" and cl.isdigit() and int(cl) > 0:
            return int(cl)
        return None

    @property
    def ranges(self) -> bool | None:
        """Whether the server says it serves byte ranges (None: it does not say)."""
        if self.http_status == 206:
            return True
        value = self.headers.get("accept-ranges", "").lower()
        if value == "bytes":
            return True
        return False if value == "none" else None

    def as_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["size"] = self.size
        out["ranges"] = self.ranges
        return out


def _excerpt(exc: urllib.error.HTTPError) -> str:
    try:
        return exc.read(400).decode("utf-8", "replace").lower()
    except Exception:                                           # noqa: BLE001
        return ""


def classify(exc: BaseException, host: str = "", *, timeout_s: float | None = None
             ) -> tuple[str, str]:
    """``(status, reason)`` for an exception a request raised."""
    where = host or "the host"
    if isinstance(exc, urllib.error.HTTPError):
        code = exc.code
        if code in (401, 403, 407):
            if code == 407 or any(w in _excerpt(exc) for w in _POLICY_WORDS):
                return BLOCKED, (f"the network policy here denies {where} (HTTP {code} from "
                                 "the proxy); nothing was retried around it")
            return BLOCKED, (f"{where} requires authorisation (HTTP {code}); credentials "
                             "and access controls are not bypassed")
        if code in (404, 410):
            return FAIL, (f"{where} has no such file (HTTP {code}): the reviewed URL is out "
                          "of date and needs review")
        if code == 429:
            return BLOCKED, f"{where} is rate-limiting this client (HTTP 429); retry later"
        if code >= 500:
            return BLOCKED, f"{where} answered with a server error (HTTP {code})"
        return FAIL, f"{where} refused the request (HTTP {code})"
    reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
    text = str(reason)
    if "tunnel connection failed" in text.lower():
        return BLOCKED, (f"the proxy refused to connect to {where} ({text}): the network "
                         "policy here does not allow this host")
    if isinstance(reason, socket.gaierror):
        return BLOCKED, f"the name {where} does not resolve here ({reason})"
    if isinstance(reason, ssl.SSLCertVerificationError) or "certificate verify failed" in text:
        return BLOCKED, (f"TLS certificate verification failed for {where} ({text}); "
                         "verification is never turned off")
    if isinstance(reason, (TimeoutError, socket.timeout)) or "timed out" in text.lower():
        limit = f" within {timeout_s:g} s" if timeout_s else ""
        return BLOCKED, f"{where} did not answer{limit} (timeout)"
    if isinstance(reason, ConnectionRefusedError):
        return BLOCKED, f"{where} refused the connection"
    if isinstance(reason, (ConnectionResetError, http.client.RemoteDisconnected,
                           ConnectionAbortedError)):
        return BLOCKED, f"{where} closed the connection ({type(reason).__name__})"
    if isinstance(reason, OSError) and "network is unreachable" in text.lower():
        return BLOCKED, f"no route to {where} (network unreachable)"
    if isinstance(exc, urllib.error.URLError):
        return BLOCKED, f"{where} could not be reached ({text})"
    if isinstance(exc, OSError):
        return BLOCKED, f"{where}: {type(exc).__name__}: {text}"
    return FAIL, f"{type(exc).__name__}: {text}"


def _send(url: str, method: str, headers: dict[str, str], timeout_s: float):
    from ..backends.http import user_agent

    req = urllib.request.Request(url, method=method,
                                 headers={"User-Agent": user_agent(), **headers})
    return urllib.request.urlopen(req, timeout=timeout_s)  # noqa: S310 - https URLs only


def probe(url: str, *, timeout_s: float = 20.0) -> Probe:
    """Send a ``HEAD`` (or a one-byte ranged ``GET``) to ``url`` and say what happened."""
    from ..acquisition.downloader import PACER

    parts = urllib.parse.urlsplit(url)
    host = (parts.hostname or "").lower()
    if parts.scheme not in ("https", "http"):
        return Probe(url, host, FAIL, f"not an http(s) URL: {parts.scheme or '(none)'}")
    t0 = time.perf_counter()
    last: Probe | None = None
    for method, headers in (("HEAD", {}), ("GET", {"Range": "bytes=0-0"})):
        PACER.wait(host)
        try:
            with _send(url, method, headers, timeout_s) as resp:
                if method == "GET":
                    resp.read(1)
                final = getattr(resp, "url", None) or url
                kept = {k: v for k, v in ((h.lower(), resp.headers.get(h)) for h in _KEEP_HEADERS)
                        if v}
                status_code = getattr(resp, "status", None)
        except urllib.error.HTTPError as exc:
            status, reason = classify(exc, host, timeout_s=timeout_s)
            last = Probe(url, host, status, reason, method, exc.code, url,
                         elapsed_ms=int((time.perf_counter() - t0) * 1000))
            # Some servers refuse HEAD (403, 405, 501) and serve GET; ask the second way
            # before concluding anything from a HEAD refusal.
            if method == "HEAD" and exc.code in (403, 405, 501):
                continue
            return last
        except (urllib.error.URLError, OSError, http.client.HTTPException) as exc:
            status, reason = classify(exc, host, timeout_s=timeout_s)
            return Probe(url, host, status, reason, method,
                         elapsed_ms=int((time.perf_counter() - t0) * 1000))
        finally:
            PACER.done(host)
        elapsed = int((time.perf_counter() - t0) * 1000)
        moved = urllib.parse.urlsplit(final)
        if (moved.hostname or "").lower() != host:
            return Probe(url, host, FAIL,
                         f"redirected to {moved.hostname!r}, another host, which the "
                         "downloader refuses: the reviewed URL needs review",
                         method, status_code, final, kept, elapsed)
        if parts.scheme == "https" and moved.scheme != "https":
            return Probe(url, host, FAIL, "redirected from https down to http", method,
                         status_code, final, kept, elapsed)
        return Probe(url, host, PASS, f"HTTP {status_code} from {host}", method, status_code,
                     final, kept, elapsed)
    return last if last is not None else Probe(url, host, FAIL, "no answer")
