"""The model proxy and the local model probe, against fake OpenAI-style servers on loopback."""

from __future__ import annotations

import http.client
import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from tcmstudio.llmproxy import LLMProxy, ProxyRefused, parse_allow_host, probe_local

from test_server_support import running_server


class FakeProvider(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _FakeHandler)
        self.seen: list[dict] = []
        self.aborted = threading.Event()
        self.finished = threading.Event()
        threading.Thread(target=self.serve_forever, kwargs={"poll_interval": 0.05},
                         daemon=True).start()

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}"

    def stop(self) -> None:
        self.shutdown()
        self.server_close()


class _FakeHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server: FakeProvider

    def log_message(self, *a):
        pass

    def _chunk(self, data: bytes) -> None:
        self.wfile.write(f"{len(data):X}\r\n".encode() + data + b"\r\n")
        self.wfile.flush()

    def do_GET(self):
        if self.path == "/v1/models":
            body = json.dumps({"object": "list", "data": [{"id": "qwen2.5:7b"}]}).encode()
        elif self.path == "/api/tags":
            body = json.dumps({"models": [{"name": "llama3.2:3b"}, {"model": "qwen3:8b"}]}).encode()
        elif self.path == "/html/v1/models":
            body = b"<html>not a model server</html>"
        else:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self.server.seen.append({"path": self.path, "headers": {k.lower(): v for k, v in
                                                                self.headers.items()},
                                 "body": body})
        if self.path.startswith("/v1/limited"):
            data = json.dumps({"error": {"message": "slow down", "type": "rate_limit"}}).encode()
            self.send_response(429)
            self.send_header("Content-Type", "application/json")
            self.send_header("Retry-After", "7")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("x-request-id", "req-123")
        self.send_header("Set-Cookie", "upstream=1")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        slow = self.path.startswith("/v1/slow")
        n = 200 if slow else 3
        try:
            for i in range(n):
                event = {"choices": [{"delta": {"content": f"片段{i} "}}]}
                self._chunk(f"data: {json.dumps(event, ensure_ascii=False)}\n\n".encode())
                time.sleep(0.05 if slow else 0.4)
            self._chunk(b"data: [DONE]\n\n")
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
            self.server.finished.set()
        except (BrokenPipeError, ConnectionResetError, OSError):
            self.server.aborted.set()


@pytest.fixture
def provider():
    p = FakeProvider()
    yield p
    p.stop()


def _proxy_request(c, target, body=b'{"model":"m","stream":true}', extra=None):
    conn = http.client.HTTPConnection("127.0.0.1", c.port, timeout=30)
    headers = {"X-TCM-Token": c.token, "X-TCM-Target": target, "Content-Type": "application/json",
               "Authorization": "Bearer sk-test-123", "Accept": "text/event-stream",
               "Cookie": "session=secret", "anthropic-version": "2023-06-01",
               "X-Private": "do-not-forward", "Origin": "https://science.impf.ai"}
    headers.update(extra or {})
    conn.request("POST", "/api/llm", body=body, headers=headers)
    return conn, conn.getresponse()


def test_stream_passes_through_as_it_arrives(tmp_path, provider):
    with running_server(tmp_path / "home") as (_, c):
        t0 = time.monotonic()
        conn, res = _proxy_request(c, f"{provider.base}/v1/chat/completions")
        assert res.status == 200
        assert res.getheader("Content-Type") == "text/event-stream"
        assert res.getheader("Transfer-Encoding") == "chunked"
        assert res.getheader("Access-Control-Allow-Origin") == "https://science.impf.ai"
        assert res.getheader("X-Request-Id") == "req-123"
        assert res.getheader("Set-Cookie") is None
        first = res.read1(65536)
        first_at = time.monotonic() - t0
        rest = res.read()
        total = time.monotonic() - t0
        conn.close()
        text = (first + rest).decode("utf-8")
        assert "片段0" in first.decode("utf-8")
        assert "片段2" in text and "[DONE]" in text
        # the first piece arrived well before the upstream finished (three pieces 0.4 s apart)
        assert first_at < total - 0.5
        sent = provider.seen[-1]
        assert sent["path"] == "/v1/chat/completions"
        assert sent["body"] == b'{"model":"m","stream":true}'
        h = sent["headers"]
        assert h["authorization"] == "Bearer sk-test-123"
        assert h["anthropic-version"] == "2023-06-01"
        for private in ("cookie", "x-tcm-token", "x-tcm-target", "x-private", "origin"):
            assert private not in h, private


def test_upstream_errors_keep_their_status_and_body(tmp_path, provider):
    with running_server(tmp_path / "home") as (_, c):
        conn, res = _proxy_request(c, f"{provider.base}/v1/limited")
        assert res.status == 429
        assert res.getheader("Retry-After") == "7"
        assert json.loads(res.read())["error"]["message"] == "slow down"
        conn.close()


def test_a_page_that_stops_reading_stops_the_upstream(tmp_path, provider):
    with running_server(tmp_path / "home") as (_, c):
        conn, res = _proxy_request(c, f"{provider.base}/v1/slow")
        assert res.status == 200
        assert b"data:" in res.read1(4096)
        # the user pressed stop: the page drops the connection
        res.close()
        conn.sock.shutdown(socket.SHUT_RDWR)
        conn.close()
        assert provider.aborted.wait(5), "the upstream kept streaming to nobody"
        assert not provider.finished.is_set()


def test_targets_outside_the_allowlist_are_refused(tmp_path):
    with running_server(tmp_path / "home") as (_, c):
        for target in ("https://evil.example/v1/chat/completions",
                       "http://api.openai.com/v1/chat/completions",
                       "https://api.openai.com:8443/v1/chat/completions",
                       "https://user:pw@api.openai.com/v1/chat/completions",
                       "ftp://127.0.0.1/x", ""):
            conn, res = _proxy_request(c, target)
            body = json.loads(res.read())
            conn.close()
            assert res.status in (400, 403), target
            assert body["error"]["type"] in ("forbidden_target", "bad_request")
        conn, res = _proxy_request(c, "https://evil.example/v1/chat/completions")
        assert "--allow-host" in json.loads(res.read())["error"]["message"]
        conn.close()
        # without the token nothing is forwarded at all
        conn = http.client.HTTPConnection("127.0.0.1", c.port, timeout=10)
        conn.request("POST", "/api/llm", body=b"{}", headers={
            "X-TCM-Target": "http://127.0.0.1:9/v1/chat/completions"})
        assert conn.getresponse().status == 401
        conn.close()


def test_unreachable_target_is_a_clear_502(tmp_path):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    with running_server(tmp_path / "home") as (_, c):
        conn, res = _proxy_request(c, f"http://127.0.0.1:{port}/v1/chat/completions")
        body = json.loads(res.read())
        conn.close()
        assert res.status == 502 and body["error"]["type"] == "upstream_unreachable"


def test_allowlist_rules():
    proxy = LLMProxy(["http://10.1.2.3:8000", "llm.example.org"])
    assert proxy.check("https://api.anthropic.com/v1/messages").host == "api.anthropic.com"
    assert proxy.check("https://open.bigmodel.cn/api/paas/v4/chat/completions")
    assert proxy.check("http://localhost:11434/v1/chat/completions").loopback
    assert proxy.check("http://[::1]:1234/v1/chat/completions").loopback
    assert proxy.check("http://10.1.2.3:8000/v1/chat/completions").port == 8000
    assert proxy.check("https://llm.example.org/v1/chat/completions?api-version=1").path == \
        "/v1/chat/completions?api-version=1"
    for bad in ("http://10.1.2.3:8001/v1", "https://10.1.2.3:8000/v1", "http://llm.example.org/v1",
                "https://api.openai.com.evil.example/v1"):
        with pytest.raises(ProxyRefused):
            proxy.check(bad)
    assert parse_allow_host("api.example.com") == ("https", "api.example.com", 443)
    assert parse_allow_host("http://10.0.0.5:8000") == ("http", "10.0.0.5", 8000)
    with pytest.raises(ValueError):
        parse_allow_host("*.example.com")


def test_local_model_probe(provider):
    closed = socket.socket()
    closed.bind(("127.0.0.1", 0))
    dead = closed.getsockname()[1]
    closed.close()
    base = provider.base
    servers = [
        {"id": "ollama", "base_url": f"{base}/v1", "probe": (f"{base}/api/tags", f"{base}/v1/models")},
        {"id": "lmstudio", "base_url": f"{base}/v1", "probe": (f"{base}/v1/models",)},
        {"id": "vllm", "base_url": f"http://127.0.0.1:{dead}/v1",
         "probe": (f"http://127.0.0.1:{dead}/v1/models",)},
        {"id": "llamacpp", "base_url": f"{base}/html/v1", "probe": (f"{base}/html/v1/models",)},
    ]
    t0 = time.monotonic()
    found = {s["id"]: s for s in probe_local(servers, timeout=1.0)["servers"]}
    assert time.monotonic() - t0 < 3
    assert found["ollama"]["ok"] and found["ollama"]["models"] == ["llama3.2:3b", "qwen3:8b"]
    assert found["lmstudio"]["ok"] and found["lmstudio"]["models"] == ["qwen2.5:7b"]
    assert not found["vllm"]["ok"] and found["vllm"]["error"] == "not running"
    assert not found["llamacpp"]["ok"] and "model list" in found["llamacpp"]["error"]


def test_local_model_probe_endpoint(tmp_path, provider):
    servers = ({"id": "lmstudio", "label": "LM Studio", "base_url": f"{provider.base}/v1",
                "probe": (f"{provider.base}/v1/models",)},)
    with running_server(tmp_path / "home", local_servers=servers) as (_, c):
        status, body = c.get("/api/llm/local")
        assert status == 200
        assert body["servers"][0]["ok"] is True and body["servers"][0]["models"] == ["qwen2.5:7b"]
        assert body["servers"][0]["label"] == "LM Studio"
