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
            if result["error"].get("type") == "timeout":
                raise TimeoutError("source gateway timed out")
            raise urllib.error.URLError(result["error"].get("message", "source gateway refused request"))
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
