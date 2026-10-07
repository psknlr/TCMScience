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
"""

from __future__ import annotations

import hmac
import ipaddress
from typing import Iterable
from urllib.parse import urlsplit

__all__ = ["PUBLIC_ORIGIN", "SecurityError", "Guard", "is_loopback_host", "normalize_origin",
           "host_of", "ALLOW_HEADERS", "EXPOSE_HEADERS", "ALLOW_METHODS"]

PUBLIC_ORIGIN = "https://science.impf.ai"
LOOPBACK_NAMES = frozenset({"localhost", "localhost.", "127.0.0.1", "::1"})
WILDCARD_BINDS = frozenset({"", "0.0.0.0", "::"})
ALLOW_METHODS = "GET, HEAD, POST, PUT, DELETE, OPTIONS"
# What a page may send: the runner's own headers, and those the model proxy forwards.
ALLOW_HEADERS = ", ".join((
    "content-type", "accept", "x-tcm-token", "x-tcm-target", "x-filename", "x-project-id",
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
