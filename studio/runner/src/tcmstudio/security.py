"""Who may talk to the runner: the Host check, the Origin allowlist and the pairing token.

The runner listens on 127.0.0.1, but any web page the user opens can try to reach it.
Three checks keep it the user's own:

* **Host.** The ``Host`` header must be a loopback name (``localhost``, ``127.0.0.1``,
  ``[::1]``) or the address the runner is bound to. A DNS-rebinding page reaches the
  runner under its own domain name and is refused here.
* **Origin.** A browser request is answered only for ``https://science.impf.ai``, loopback
  pages (``http(s)://localhost|127.0.0.1|[::1]`` on any port), the runner's own pages, and
  origins given with ``--allow-origin``. Any other origin gets 403, and its preflight is
  refused too. Preflights carry ``Access-Control-Allow-Private-Network: true`` (Chrome's
  Private Network Access).
* **Token.** Every ``/api/*`` request except ``GET /api/health`` carries the pairing token
  in ``X-TCM-Token`` (or ``?token=`` on a GET, for EventSource and file links). It is
  compared in constant time. ``--no-token`` turns it off, and is refused unless the runner
  is bound to a loopback address.

Pairing never puts the token in a URL (docs/V2.md §16). :class:`PairBook` holds the two ways a
page gets it: a **one-time code** in the link the runner prints or opens
(``#pair=<base64url({url, code})>``, 128 bits, single use, 10 minutes), which the page trades
at ``POST /api/pair/claim``; and a **request approved on this computer**: an allowed page asks
(``POST /api/pair/request``), the user compares a 6-digit code on the runner's own page
(``/pair``) and allows it, and the page that asked, holding the request's secret, receives the
token once. Both are rate limited, and at most five requests wait at a time.
"""

from __future__ import annotations

import collections
import hashlib
import hmac
import ipaddress
import secrets
import threading
import time
from typing import Any, Callable, Iterable
from urllib.parse import urlsplit

__all__ = ["PUBLIC_ORIGIN", "SecurityError", "Guard", "is_loopback_host", "normalize_origin",
           "host_of", "ALLOW_HEADERS", "EXPOSE_HEADERS", "ALLOW_METHODS", "PAIR_TTL_S",
           "MAX_PENDING_REQUESTS", "RateLimit", "PairBook"]

PUBLIC_ORIGIN = "https://science.impf.ai"
LOOPBACK_NAMES = frozenset({"localhost", "localhost.", "127.0.0.1", "::1"})
WILDCARD_BINDS = frozenset({"", "0.0.0.0", "::"})
ALLOW_METHODS = "GET, HEAD, POST, PUT, DELETE, OPTIONS"
# What a page may send: the runner's own headers, and those the model proxy forwards.
ALLOW_HEADERS = ", ".join((
    "content-type", "accept", "x-tcm-token", "x-tcm-pair-secret", "x-tcm-target", "x-filename",
    "x-project-id",
    "authorization", "x-api-key", "api-key", "anthropic-version", "anthropic-beta",
    "anthropic-dangerous-direct-browser-access", "http-referer", "x-title",
    "openai-organization", "openai-project", "last-event-id", "cache-control"))
EXPOSE_HEADERS = ", ".join(("content-type", "content-length", "content-disposition",
                            "retry-after", "x-request-id", "request-id",
                            "x-ratelimit-remaining-requests", "x-ratelimit-remaining-tokens"))


class SecurityError(ValueError):
    """A configuration the runner refuses to start with."""


def host_of(value: str | None) -> str:
    """The host name in a ``Host`` header or a URL netloc, lowercased, brackets removed."""
    value = (value or "").strip()
    if not value:
        return ""
    try:
        return (urlsplit("//" + value).hostname or "").lower()
    except ValueError:
        return ""


def is_loopback_host(host: str | None) -> bool:
    host = (host or "").strip().lower().strip("[]")
    if host in LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def normalize_origin(value: str) -> str:
    """``scheme://host[:port]`` for an ``--allow-origin`` value; refuses anything else
    (a path, a wildcard, a scheme other than http or https)."""
    raw = (value or "").strip().rstrip("/")
    if raw == "*" or "*" in raw:
        raise SecurityError("--allow-origin takes one exact origin (https://example.org); "
                            "a wildcard would let every web page use this runner")
    parts = urlsplit(raw)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise SecurityError(f"--allow-origin: {value!r} is not an origin such as "
                            "https://example.org")
    if parts.path or parts.query or parts.fragment or parts.username or parts.password:
        raise SecurityError(f"--allow-origin: {value!r} must be scheme://host[:port] only")
    host = parts.hostname.lower()
    shown = f"[{host}]" if ":" in host else host
    default = 443 if parts.scheme == "https" else 80
    port = parts.port
    return f"{parts.scheme}://{shown}" + (f":{port}" if port and port != default else "")


def _origin_key(value: str) -> str | None:
    try:
        return normalize_origin(value)
    except SecurityError:
        return None


class Guard:
    """The three checks, for one server. ``token`` None means no token is required."""

    def __init__(self, *, bind_host: str, token: str | None,
                 allow_origins: Iterable[str] = (), public_origin: str = PUBLIC_ORIGIN) -> None:
        self.bind_host = (bind_host or "").strip().lower().strip("[]")
        self.token = token or None
        self.origins = {normalize_origin(o) for o in allow_origins}
        self.origins.add(normalize_origin(public_origin))

    # ------------------------------------------------------------------- config
    @property
    def token_required(self) -> bool:
        return self.token is not None

    @staticmethod
    def resolve_token(bind_host: str, stored_token: str, no_token: bool) -> str | None:
        """The token this server requires: the stored one, or none with ``--no-token`` on a
        loopback bind. Any other bind can be reached from other machines and always needs it."""
        if no_token:
            if not is_loopback_host(bind_host):
                raise SecurityError(f"--no-token is refused when binding to {bind_host!r}: other "
                                    "machines could reach this runner. Bind to 127.0.0.1, or "
                                    "keep the token.")
            return None
        return stored_token

    # ------------------------------------------------------------------- checks
    def host_ok(self, host_header: str | None) -> bool:
        if not host_header or not host_header.strip():
            return False
        host = host_of(host_header)
        if not host:
            return False
        if is_loopback_host(host):
            return True
        if self.bind_host in WILDCARD_BINDS:
            # Reachable from the network on purpose: the token is what guards it then.
            return True
        return host == self.bind_host

    def origin_allowed(self, origin: str | None, host_header: str | None = None) -> bool:
        if origin is None:
            return True               # not a cross-origin browser request (curl, same-origin GET)
        origin = origin.strip()
        if not origin or origin.lower() == "null":
            return False              # sandboxed frames, file:// pages
        key = _origin_key(origin)
        if key is None:
            return False
        if key in self.origins:
            return True
        parts = urlsplit(key)
        if is_loopback_host(parts.hostname):
            return True
        # the runner's own pages, wherever they are reached from (a LAN address)
        return bool(host_header) and parts.netloc.lower() == host_header.strip().lower()

    def token_ok(self, given: str | None) -> bool:
        if self.token is None:
            return True
        if not given:
            return False
        return hmac.compare_digest(given.encode("utf-8"), self.token.encode("utf-8"))

    # ------------------------------------------------------------------ headers
    def cors_headers(self, origin: str | None, host_header: str | None) -> list[tuple[str, str]]:
        """Headers for an allowed cross-origin request (none for a refused one)."""
        if not origin or not self.origin_allowed(origin, host_header):
            return []
        return [("Access-Control-Allow-Origin", origin.strip()),
                ("Vary", "Origin"),
                ("Access-Control-Expose-Headers", EXPOSE_HEADERS)]

    def preflight_headers(self, origin: str | None, host_header: str | None,
                          private_network: bool,
                          requested_headers: str | None = None) -> tuple[int, list[tuple[str, str]]]:
        if not origin or not self.origin_allowed(origin, host_header):
            return 403, [("Vary", "Origin")]
        allowed = [h.strip() for h in ALLOW_HEADERS.split(",")]
        # A custom provider may need its own header; an allowed page may name it (the model
        # proxy still forwards only its own list).
        for name in (requested_headers or "").split(","):
            name = name.strip().lower()
            if name and name not in allowed and all(c.isalnum() or c in "-_" for c in name):
                allowed.append(name)
        headers = self.cors_headers(origin, host_header)
        headers += [("Access-Control-Allow-Methods", ALLOW_METHODS),
                    ("Access-Control-Allow-Headers", ", ".join(allowed)),
                    ("Access-Control-Max-Age", "600")]
        if private_network:
            headers.append(("Access-Control-Allow-Private-Network", "true"))
        return 204, headers


# ================================================================================ pairing

#: A one-time code and a pairing request live ten minutes.
PAIR_TTL_S = 600
#: Requests waiting for the user's answer at once: a page cannot flood the user with approvals.
MAX_PENDING_REQUESTS = 5
#: Codes outstanding at once (each printed or opened link has its own).
MAX_CODES = 32


class RateLimit:
    """At most ``limit`` events in any ``window_s`` seconds (a sliding window, thread-safe)."""

    def __init__(self, limit: int, window_s: float, clock: Callable[[], float] = time.monotonic) -> None:
        self.limit = limit
        self.window_s = window_s
        self._clock = clock
        self._events: collections.deque[float] = collections.deque()
        self._lock = threading.Lock()

    def _trim(self, now: float) -> None:
        while self._events and self._events[0] <= now - self.window_s:
            self._events.popleft()

    def allow(self) -> bool:
        """Record one event if it is within the limit; False (nothing recorded) when it is not."""
        with self._lock:
            now = self._clock()
            self._trim(now)
            if len(self._events) >= self.limit:
                return False
            self._events.append(now)
            return True

    def exhausted(self) -> bool:
        with self._lock:
            self._trim(self._clock())
            return len(self._events) >= self.limit


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class PairBook:
    """One-time pairing codes and page-initiated pairing requests, in memory (a restart forgets
    them: an old link or request is then simply invalid).

    A request goes ``pending`` → ``approved`` (on the runner's page) → ``delivered`` (the token
    was handed to the page that asked, once), or ``pending`` → ``denied`` | ``expired``. Every
    lookup compares secrets in constant time; codes are kept only as their SHA-256."""

    def __init__(self, *, ttl_s: float = PAIR_TTL_S, max_pending: int = MAX_PENDING_REQUESTS,
                 max_codes: int = MAX_CODES, clock: Callable[[], float] = time.monotonic) -> None:
        self.ttl_s = ttl_s
        self.max_pending = max_pending
        self.max_codes = max_codes
        self._clock = clock
        self._lock = threading.Lock()
        self._codes: dict[str, float] = {}                    # sha256(code) -> expiry
        self._requests: dict[str, dict[str, Any]] = {}
        # Guessing is hopeless (128-bit codes, 96-bit ids with 192-bit secrets), and these keep it so.
        self.failed_claims = RateLimit(10, 60, clock)
        self.opened = RateLimit(20, ttl_s, clock)
        self.failed_answers = RateLimit(10, 60, clock)
        self.failed_polls = RateLimit(30, 60, clock)

    # ------------------------------------------------------------------- codes
    def new_code(self) -> str:
        """A fresh single-use code (22 URL-safe characters, 128 bits)."""
        code = secrets.token_urlsafe(16)
        with self._lock:
            now = self._clock()
            for key, expires in list(self._codes.items()):
                if expires <= now:
                    del self._codes[key]
            while len(self._codes) >= self.max_codes:        # the oldest link stops working first
                del self._codes[min(self._codes, key=self._codes.__getitem__)]
            self._codes[_digest(code)] = now + self.ttl_s
        return code

    def claim(self, code: Any) -> bool:
        """True once for a code that was issued and has not expired; it is spent either way."""
        if not isinstance(code, str) or not 16 <= len(code) <= 64:
            return False
        with self._lock:
            expires = self._codes.pop(_digest(code), None)
            return expires is not None and expires > self._clock()

    # ---------------------------------------------------------------- requests
    def _sweep(self, now: float) -> None:
        for rid, r in list(self._requests.items()):
            if r["state"] == "pending" and r["expires"] <= now:
                r["state"] = "expired"
            if r["expires"] + 60 <= now:                       # the page has had its answer
                del self._requests[rid]

    def pending(self) -> int:
        with self._lock:
            self._sweep(self._clock())
            return sum(r["state"] == "pending" for r in self._requests.values())

    def open_request(self, origin: str) -> dict[str, Any] | None:
        """A new request from ``origin`` (a copy, secrets included), or None when
        ``max_pending`` requests already wait for an answer."""
        with self._lock:
            now = self._clock()
            self._sweep(now)
            if sum(r["state"] == "pending" for r in self._requests.values()) >= self.max_pending:
                return None
            r = {"id": secrets.token_urlsafe(12), "secret": secrets.token_urlsafe(24),
                 "nonce": secrets.token_urlsafe(24), "code": f"{secrets.randbelow(10 ** 6):06d}",
                 "origin": origin, "state": "pending", "created": now, "expires": now + self.ttl_s}
            self._requests[r["id"]] = r
            return dict(r)

    def request(self, rid: Any) -> dict[str, Any] | None:
        """A copy of the request ``rid`` (its state brought up to date), or None."""
        if not isinstance(rid, str) or not rid:
            return None
        with self._lock:
            now = self._clock()
            self._sweep(now)
            r = self._requests.get(rid)
            return {**r, "expires_in": max(0, int(r["expires"] - now))} if r else None

    def answer(self, rid: Any, nonce: Any, allow: bool) -> str | None:
        """The user's answer on the runner's page. Returns the new state, or None when there is
        no pending request with that id and nonce (expired, answered, or not that page's)."""
        if not isinstance(rid, str) or not isinstance(nonce, str):
            return None
        with self._lock:
            self._sweep(self._clock())
            r = self._requests.get(rid)
            if r is None or r["state"] != "pending" or not hmac.compare_digest(
                    nonce.encode("utf-8"), r["nonce"].encode("utf-8")):
                return None
            r["state"] = "approved" if allow else "denied"
            return r["state"]

    def poll(self, rid: Any, origin: str | None, secret: Any) -> dict[str, Any] | None:
        """What the page that asked sees: ``{state, expires_in}``; ``deliver: True`` exactly once,
        when the request was approved (the caller then hands out the token). None when the id,
        the origin or the secret does not match: the poller learns nothing about other requests."""
        if not isinstance(rid, str) or not isinstance(secret, str) or not origin:
            return None
        with self._lock:
            now = self._clock()
            self._sweep(now)
            r = self._requests.get(rid)
            if r is None or r["origin"] != origin.strip() or not hmac.compare_digest(
                    secret.encode("utf-8"), r["secret"].encode("utf-8")):
                return None
            out: dict[str, Any] = {"state": r["state"],
                                   "expires_in": max(0, int(r["expires"] - now))}
            if r["state"] == "approved":
                r["state"] = "delivered"
                out["deliver"] = True
            return out
