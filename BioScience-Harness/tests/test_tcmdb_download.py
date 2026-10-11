"""The hub's downloader: a reviewed hand-off followed, any other redirect refused, and a
body cut off mid-transfer resumed.

Two names of the loopback interface stand for two hosts: ``127.0.0.1`` is the URL's host,
``localhost`` the one it hands the file off to (as figshare hands its files to S3).
"""
from __future__ import annotations

import http.server
import threading

import pytest

from bioagent.acquisition.downloader import DownloadError
from bioagent.tcmdb.download import HubDownloader, handed_off

BODY = bytes(range(256)) * 8                  # 2048 bytes


class _Handler(http.server.BaseHTTPRequestHandler):
    seen: list[tuple[str, str, str, str | None]] = []
    cut_first = False
    other_port = 0                      # the server on 127.0.0.2, a host no spec names

    def log_message(self, *args):
        pass

    def _send(self, head: bool):
        host = self.headers.get("Host", "").split(":")[0]
        self.seen.append((self.command, host, self.path, self.headers.get("Range")))
        if self.path == "/files/1":                       # the URL: hands off elsewhere
            self.send_response(302)
            self.send_header("Location", f"http://localhost:{self.server.server_port}"
                                         "/bucket/1?signature=abc")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if self.path == "/files/2":                       # hands off to an unnamed host
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.2:{type(self).other_port}/direct")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if not self.path.startswith(("/bucket/1", "/direct")):
            self.send_response(404)
            self.end_headers()
            return
        rng = self.headers.get("Range")
        if rng:
            start = int(rng[6:].split("-")[0])
            end = (int(rng.split("-")[1]) if rng.split("-")[1] else len(BODY) - 1)
            chunk = BODY[start:end + 1]
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{len(BODY)}")
            self.send_header("Content-Length", str(len(chunk)))
            self.end_headers()
            if not head:
                self.wfile.write(chunk)
            return
        gets = sum(1 for c, *_ in self.seen if c == "GET")
        if not head and self.path.startswith("/direct") and type(self).cut_first and gets == 1:
            # chunked, and the connection ends inside the second chunk
            self.send_response(200)
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            self.wfile.write(b"%x\r\n" % 1024 + BODY[:1024] + b"\r\n")
            self.wfile.write(b"%x\r\n" % 1024 + BODY[1024:1300])
            self.wfile.flush()
            self.close_connection = True
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(BODY)))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()
        if not head:
            self.wfile.write(BODY)

    def do_HEAD(self):
        self._send(True)

    def do_GET(self):
        self._send(False)


@pytest.fixture
def server(monkeypatch):
    for var in ("http_proxy", "HTTP_PROXY", "https_proxy", "HTTPS_PROXY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,127.0.0.2,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,127.0.0.2,localhost")
    _Handler.seen, _Handler.cut_first = [], False
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    other = http.server.ThreadingHTTPServer(("127.0.0.2", 0), _Handler)
    _Handler.other_port = other.server_address[1]
    for srv in (httpd, other):
        threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    other.shutdown()


def test_a_hand_off_the_spec_names_is_followed_and_recorded(server, tmp_path):
    dl = HubDownloader(tmp_path, log=lambda m: None)
    res = dl.fetch(server + "/files/1", "one.bin", redirects=("localhost",))
    assert (tmp_path / "one.bin").read_bytes() == BODY and res.verified
    assert res.url == server + "/files/1", "the record keeps the reviewed URL"
    assert dl.served_from == "localhost"
    # every request asked the reviewed URL first, then the host it was handed to
    hosts = [h for _, h, *_ in _Handler.seen]
    assert hosts.count("127.0.0.1") == hosts.count("localhost") >= 2


def test_a_redirect_without_a_named_hand_off_is_refused_as_before(server, tmp_path):
    with pytest.raises(DownloadError, match="not a host this call may contact"):
        HubDownloader(tmp_path, log=lambda m: None).fetch(server + "/files/1", "a.bin")
    assert not (tmp_path / "a.bin").exists()
    # a named hand-off does not open the way to any other host: the answer is refused unread
    with pytest.raises(DownloadError, match="not a host this call may contact"):
        HubDownloader(tmp_path, log=lambda m: None).fetch(
            server + "/files/2", "b.bin", redirects=("localhost",))
    assert not (tmp_path / "b.bin").exists() and not (tmp_path / "b.bin.part").exists()


def test_hand_off_hosts_match_exactly_or_under_a_named_domain():
    assert handed_off("us.aws.cdn.hf.co", ["*.hf.co"])
    assert handed_off("cas-bridge.xethub.hf.co", ["*.hf.co"])
    assert not handed_off("hf.co.evil.example", ["*.hf.co"])
    assert not handed_off("evilhf.co", ["*.hf.co"])
    assert handed_off("s3-eu-west-1.amazonaws.com", ["s3-eu-west-1.amazonaws.com"])
    assert not handed_off("x.s3-eu-west-1.amazonaws.com", ["s3-eu-west-1.amazonaws.com"])


def test_a_body_cut_off_mid_transfer_is_resumed_not_failed(server, tmp_path):
    _Handler.cut_first = True
    logs: list[str] = []
    dl = HubDownloader(tmp_path, log=logs.append, chunk=512)
    res = dl.fetch(server + "/direct", "cut.bin")
    assert (tmp_path / "cut.bin").read_bytes() == BODY and res.verified
    assert any("IncompleteRead" in m for m in logs), logs
    ranges = [r for c, _, _, r in _Handler.seen if c == "GET" and r]
    assert ranges and ranges[-1].startswith("bytes=1024-"), "resumed from what was kept"


def test_a_streamed_file_without_a_size_is_checked_for_being_whole(tmp_path):
    import gzip
    import json
    import zipfile

    from bioagent.tcmdb.acquire import structure

    z = tmp_path / "a.zip"
    with zipfile.ZipFile(z, "w") as fh:
        fh.writestr("x.tsv", "a\tb\n" * 1000)
    assert structure(z) == (True, "every member of the zip matches its CRC-32")
    cut = tmp_path / "cut.zip"
    cut.write_bytes(z.read_bytes()[:-40])                 # the central directory cut off
    assert structure(cut)[0] is False
    g = tmp_path / "a.gz"
    g.write_bytes(gzip.compress(b"x" * 100_000))
    assert structure(g)[0] is True
    (tmp_path / "cut.gz").write_bytes(g.read_bytes()[:-6])
    assert structure(tmp_path / "cut.gz")[0] is False
    (tmp_path / "a.json").write_text(json.dumps({"rows": list(range(100))}))
    assert structure(tmp_path / "a.json")[0] is True
    (tmp_path / "cut.json").write_text(json.dumps({"rows": list(range(100))})[:-10])
    assert structure(tmp_path / "cut.json")[0] is False
    (tmp_path / "a.xml").write_text("<?xml version='1.0'?><a><b/></a>")
    assert structure(tmp_path / "a.xml")[0] is True
    (tmp_path / "cut.xml").write_text("<?xml version='1.0'?><a><b/>")
    assert structure(tmp_path / "cut.xml")[0] is False
    (tmp_path / "t.tsv").write_text("a\tb\n")
    assert structure(tmp_path / "t.tsv") == (None, ""), "a table carries no such check"
