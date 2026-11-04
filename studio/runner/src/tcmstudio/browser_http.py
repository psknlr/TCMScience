"""A urllib-compatible transport for the browser's registered source gateway.

The Python policy kernel, source templates, parser, cache and rate limits stay in
HTTPBackend. Only socket I/O crosses the same-origin gateway. No native networking
is changed, and the gateway independently checks every requested route.
"""
from __future__ import annotations

import base64
import io
import json
import sys
import urllib.error
import urllib.parse
from email.message import Message


class GatewayRefusal(urllib.error.URLError):
    """The source gateway itself refused or failed the request (not the upstream).

    ``gateway`` keeps what the gateway said apart from any upstream status: its HTTP status,
    its error type (``rate_limited``, ``timeout``, ``source_not_allowed`` …) and the
    ``Retry-After`` it asked for. ``HTTPBackend`` reads ``layer`` and ``gateway`` to record
    the failure layer, back off on ``rate_limited`` and stop on a refusal a retry would meet
    again. A URLError subclass, so any caller that caught URLError still does.
    """

    layer = "gateway"

    def __init__(self, message: str, gateway: dict):
        super().__init__(message)
        self.gateway = gateway


class GatewayTimeout(TimeoutError):
    """The source gateway gave up waiting for the upstream (its own ``timeout``, HTTP 504)."""

    layer = "gateway"

    def __init__(self, message: str, gateway: dict):
        super().__init__(message)
        self.gateway = gateway


def _gateway_info(result: dict) -> dict:
    err = result.get("error") or {}
    gw = result.get("gateway") if isinstance(result.get("gateway"), dict) else {}
    info = {"error_type": str(err.get("type") or "refused"),
            "message": str(err.get("message") or "")[:300]}
    if gw.get("status") is not None:
        info["status"] = int(gw["status"])
    if gw.get("retry_after_s") is not None:
        info["retry_after_s"] = float(gw["retry_after_s"])
    return info


class GatewayOpener:
    def __init__(self, allowed_hosts, send):
        self.allowed_hosts = sorted(allowed_hosts)
        self.send = send

    def open(self, request, timeout=30):
        host = urllib.parse.urlsplit(request.full_url).hostname
        if host not in self.allowed_hosts:
            raise urllib.error.URLError("network policy denied the source host")
        packet = {"url": request.full_url, "method": request.get_method(),
                  "headers": dict(request.header_items()),
                  "body_base64": base64.b64encode(request.data).decode() if request.data else None,
                  "allowed_hosts": self.allowed_hosts, "timeout_s": timeout}
        try:
            result = json.loads(str(self.send(json.dumps(packet), int(timeout * 1000))))
        except Exception as exc:
            raise urllib.error.URLError(f"source gateway network error: {exc}") from exc
        if result.get("error"):
            info = _gateway_info(result)
            if info["error_type"] == "timeout":
                raise GatewayTimeout("source gateway timed out", info)
            raise GatewayRefusal(f"source gateway {info['error_type']}"
                                 + (f" (HTTP {info['status']})" if "status" in info else "")
                                 + f": {info['message'] or 'source gateway refused request'}", info)
        status = int(result["status"])
        headers = Message()
        for key, value in result.get("headers", {}).items():
            headers[key] = str(value)
        raw = base64.b64decode(result["body_base64"], validate=True)
        final_url = result.get("url") or request.full_url
        if urllib.parse.urlsplit(final_url).hostname not in self.allowed_hosts:
            raise urllib.error.URLError("network policy denied the final source host")
        if not 200 <= status < 300:
            raise urllib.error.HTTPError(final_url, status, f"source HTTP {status}", headers, io.BytesIO(raw))
        return GatewayResponse(raw, status, headers, final_url)


class GatewayResponse(io.BytesIO):
    def __init__(self, raw, status, headers, url):
        super().__init__(raw)
        self.status, self.headers, self.url = status, headers, url

    def geturl(self):
        return self.url


def install(send):
    """Install only inside Pyodide; ``send`` is a synchronous Worker XHR callback."""
    if sys.platform != "emscripten":
        raise RuntimeError("the browser source transport is only available in Pyodide")
    from bioagent.backends.http import HTTPBackend
    HTTPBackend.browser_opener_factory = staticmethod(lambda allowed: GatewayOpener(allowed, send))
