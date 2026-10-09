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
