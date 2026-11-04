import base64
import json
import urllib.error
import urllib.request

import pytest

from tcmstudio.browser_http import GatewayOpener, install


def transport(status=200, raw=b'{"gene":"TP53"}', url="https://example.org/gene"):
    packets = []
    def send(packet, timeout):
        packets.append((json.loads(packet), timeout))
        return json.dumps({"status": status, "headers": {"Content-Type": "application/json"},
                           "body_base64": base64.b64encode(raw).decode(), "url": url})
    return GatewayOpener(["example.org"], send), packets


def test_transport_keeps_original_binary_body_headers_and_request():
    opener, packets = transport(raw="黄芩".encode("gbk"))
    request = urllib.request.Request("https://example.org/gene", data=b"a=1", method="POST")
    with opener.open(request, timeout=12) as response:
        assert response.read() == "黄芩".encode("gbk")
        assert response.status == 200
        assert response.headers["Content-Type"] == "application/json"
    assert packets[0][0]["body_base64"] == "YT0x"
    assert packets[0][0]["allowed_hosts"] == ["example.org"]
    assert packets[0][0]["timeout_s"] == 12
    assert packets[0][1] == 12000


def test_upstream_error_remains_http_error_not_success():
    opener, _ = transport(status=429, raw=b"rate limited")
    with pytest.raises(urllib.error.HTTPError) as exc:
        opener.open(urllib.request.Request("https://example.org/gene"))
    assert exc.value.code == 429
    assert exc.value.read() == b"rate limited"


def test_transport_rechecks_initial_and_final_hosts():
    opener, packets = transport(url="https://evil.test/data")
    with pytest.raises(urllib.error.URLError):
        opener.open(urllib.request.Request("https://example.org/gene"))
    with pytest.raises(urllib.error.URLError):
        opener.open(urllib.request.Request("https://localhost/private"))
    assert len(packets) == 1


def test_native_python_networking_cannot_be_replaced():
    with pytest.raises(RuntimeError, match="Pyodide"):
        install(lambda *_: "{}")


def test_gateway_timeout_retains_timeout_status():
    opener = GatewayOpener(["example.org"], lambda *_: '{"error":{"type":"timeout","message":"查询超时"}}')
    with pytest.raises(TimeoutError, match="timed out"):
        opener.open(urllib.request.Request("https://example.org/gene"))


# ------------------------------------------------- the gateway's own refusals (audit 2026-11-04)

def _refusing(error_type, status, retry=None, calls=None):
    def send(packet, timeout):
        if calls is not None:
            calls.append(packet)
        gw = {"status": status, **({"retry_after_s": retry} if retry is not None else {})}
        return json.dumps({"error": {"type": error_type, "message": "数据库服务查询队列已满，请稍后重试"},
                           "gateway": gw})
    return send


def test_a_gateway_refusal_keeps_its_layer_status_type_and_retry_after():
    from tcmstudio.browser_http import GatewayRefusal
    opener = GatewayOpener(["example.org"], _refusing("rate_limited", 429, 10))
    with pytest.raises(GatewayRefusal) as exc:
        opener.open(urllib.request.Request("https://example.org/gene"))
    assert isinstance(exc.value, urllib.error.URLError), "callers that caught URLError still do"
    assert exc.value.layer == "gateway"
    assert exc.value.gateway == {"error_type": "rate_limited", "status": 429, "retry_after_s": 10.0,
                                 "message": "数据库服务查询队列已满，请稍后重试"}


def _backend_through(send, **kw):
    from bioagent.backends import http as H
    from bioagent.backends.http import HTTPBackend
    from bioagent.runtime.component import ComponentManifest, Permissions, RuntimeSpec
    manifest = ComponentManifest(id="t.gw", kind="connector", name="t",
                                 runtime=RuntimeSpec(backend="http", server="https://example.org"),
                                 permissions=Permissions(network=("example.org",)))
    backend = HTTPBackend(rates={"example.org": 1000.0}, **kw)
    saved = H.HTTPBackend.browser_opener_factory
    H.HTTPBackend.browser_opener_factory = staticmethod(lambda allowed: GatewayOpener(allowed, send))
    try:
        return backend.invoke(manifest, path="gene")
    finally:
        H.HTTPBackend.browser_opener_factory = saved


def test_a_rate_limited_gateway_is_waited_for_then_reported_with_its_retry_time(monkeypatch):
    from bioagent.backends import http as H
    slept = []
    monkeypatch.setattr(H.time, "sleep", lambda s: slept.append(s))
    calls = []
    res = _backend_through(_refusing("rate_limited", 429, 3, calls), max_retries=2)
    assert res.status.value == "FAILED" and len(calls) == 2
    assert slept and slept[0] >= 3, "the Retry-After the gateway asked for is honoured"
    assert res.metadata["failure_layer"] == "gateway"
    assert res.metadata["gateway"]["status"] == 429 and res.metadata["retry_after_s"] == 3.0
    # a pause longer than a call may wait ends the call at once, saying when to retry
    calls.clear()
    res = _backend_through(_refusing("rate_limited", 429, 600, calls), max_retries=3)
    assert len(calls) == 1 and res.metadata["retry_after_s"] == 600.0 and "600s" in res.error


def test_an_authorization_or_input_refusal_is_not_retried():
    calls = []
    res = _backend_through(_refusing("source_not_allowed", 403, None, calls), max_retries=3)
    assert res.status.value == "UNAVAILABLE" and len(calls) == 1
    assert res.metadata["failure_layer"] == "gateway" and "source_not_allowed" in res.error


def test_a_gateway_timeout_and_an_upstream_status_are_different_layers(monkeypatch):
    from bioagent.backends import http as H
    monkeypatch.setattr(H.time, "sleep", lambda s: None)
    res = _backend_through(_refusing("timeout", 504), max_retries=2)
    assert res.status.value == "TIMEOUT" and res.metadata["failure_layer"] == "gateway"
    assert res.metadata["gateway"]["status"] == 504

    def upstream_403(packet, timeout):
        return json.dumps({"status": 403, "headers": {"Content-Type": "text/plain"},
                           "body_base64": base64.b64encode(b"error code: 1003").decode(),
                           "url": json.loads(packet)["url"]})
    res = _backend_through(upstream_403, max_retries=1)
    assert res.status.value == "FAILED" and res.metadata["failure_layer"] == "upstream_http"
    assert res.metadata["http_status"] == 403 and "gateway" not in res.metadata
