"""acquisition.transfer.VerifiedTransfer and acquisition.service: transfer mechanics.

Every file here is served over real HTTP from the loopback interface by a server with the
semantics a resume depends on (RFC 9110): byte ranges, a strong or a weak ETag,
Last-Modified, If-Range (a validator that does not match gets the whole file with 200) and
416 for a range past the end. Tests script the misbehaviour they need (a wrong 206, a 503
with Retry-After, a dropped connection, a redirect). These are tests of the transfer's
mechanics on loopback; nothing here reaches a real scientific host, and nothing here is a
live download.
"""
from __future__ import annotations

import email.utils
import errno
import gzip
import hashlib
import http.server
import json
import threading
import time
from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from bioagent.acquisition.service import acquire_dataset, acquisition_state, plan
from bioagent.acquisition.transfer import (TransferCancelled, TransferError, VerifiedTransfer,
                                           check_archive, read_sidecar)
from bioagent.acquisition.downloader import DownloadError
from bioagent.tcmdb import TCMDataHub
from bioagent.tcmdb import hub as hub_module
from bioagent.tcmdb.acquire import outcome_of_error, read_audit
from bioagent.tcmdb.datasets import dataset as real_dataset
from bioagent.validation import BLOCKED, FAIL, PASS, SKIPPED

BLOCK = 1 << 16


@dataclass
class _File:
    body: bytes
    etag: str | None = None
    last_modified: str | None = None


class _Upstream(http.server.BaseHTTPRequestHandler):
    files: dict[str, _File] = {}
    seen: list[dict] = []
    script: dict[str, list[dict]] = {}
    hold: dict[str, threading.Event] = {}
    after_head: dict[str, _File] = {}
    head_status: dict[str, int] = {}          # a HEAD refused (a host that serves only GET)
    bare_head: set[str] = set()               # a HEAD answered without ETag or Last-Modified

    def log_message(self, *args):
        pass

    def version_string(self):
        return "fixture/1"                     # the Server header

    def _empty(self, status: int, headers: dict[str, str] | None = None) -> None:
        self.send_response(status)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _answer(self, body: bool) -> None:
        self.seen.append({"method": self.command, "path": self.path,
                          "range": self.headers.get("Range"),
                          "if_range": self.headers.get("If-Range"), "at": time.monotonic()})
        act: dict = {}
        if self.command == "HEAD" and self.path in self.head_status:
            return self._empty(self.head_status[self.path])
        if self.command == "GET" and self.script.get(self.path):
            act = self.script[self.path].pop(0)
            if "status" in act:
                return self._empty(act["status"], act.get("headers"))
            if "redirect" in act:
                return self._empty(302, {"Location": act["redirect"]})
        f = self.files.get(self.path)
        if f is None:
            return self._empty(404)
        data = f.body
        status, start, end = 200, 0, len(data) - 1
        rng = self.headers.get("Range")
        if rng and rng.startswith("bytes="):
            validator = self.headers.get("If-Range")
            honour = True
            if validator and not act.get("ignore_if_range"):   # a strong comparison (RFC 9110)
                if validator.startswith(('"', "W/")):
                    honour = bool(f.etag) and not f.etag.startswith("W/") and validator == f.etag
                else:
                    honour = bool(f.last_modified) and validator == f.last_modified
            if honour:
                first, _, last = rng[6:].partition("-")
                first = int(first)
                if first >= len(data):
                    return self._empty(416, {"Content-Range": f"bytes */{len(data)}"})
                status, start = 206, first
                if last:
                    end = min(int(last), len(data) - 1)
        if act.get("wrong_range") and status == 206:
            start = 0                           # a 206 for another range than the one asked
        chunk = data[start:end + 1]
        self.send_response(status)
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{len(data)}")
        self.send_header("Accept-Ranges", "bytes")
        bare = self.command == "HEAD" and self.path in self.bare_head
        if f.etag and not bare:
            self.send_header("ETag", f.etag)
        if f.last_modified and not bare:
            self.send_header("Last-Modified", f.last_modified)
        self.send_header("Content-Length", str(len(chunk)))
        self.end_headers()
        if not body:
            if self.path in self.after_head:   # the file changes between the probe and the GET
                self.files[self.path] = self.after_head.pop(self.path)
            return
        try:
            if act.get("truncate"):
                self.wfile.write(chunk[:len(chunk) // 2])
                self.wfile.flush()
                return                          # HTTP/1.0: the connection closes short
            hold = self.hold.get(self.path)
            if hold is not None:
                self.wfile.write(chunk[:BLOCK])
                self.wfile.flush()
                hold.wait(20)
                chunk = chunk[BLOCK:]
            self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_HEAD(self):
        self._answer(False)

    def do_GET(self):
        self._answer(True)


@pytest.fixture
def upstream():
    _Upstream.files, _Upstream.seen, _Upstream.script = {}, [], {}
    _Upstream.hold, _Upstream.after_head = {}, {}
    _Upstream.head_status, _Upstream.bare_head = {}, set()
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Upstream)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    for event in _Upstream.hold.values():
        event.set()
    srv.shutdown()
    srv.server_close()


def _body(n: int, seed: str) -> bytes:
    out = bytearray()
    i = 0
    while len(out) < n:
        out += hashlib.sha256(f"{seed}:{i}".encode()).digest()
        i += 1
    return bytes(out[:n])


def _sha(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _dl(root: Path, **kw) -> VerifiedTransfer:
    kw.setdefault("log", lambda m: None)
    kw.setdefault("retry_backoff_s", 0.01)
    return VerifiedTransfer(root, chunk=BLOCK, **kw)


def _cancel_after(n: int):
    stop = threading.Event()

    def progress(name, done, total):
        if done >= n:
            stop.set()
    return stop, progress


def _gets():
    return [s for s in _Upstream.seen if s["method"] == "GET"]


def _interrupt(root: Path, url: str, name: str, *, at: int = 200_000, **kw) -> int:
    """Start a transfer and cancel it once ``at`` bytes are on disk: the partial file and
    its identity record stay, as a cancelled job leaves them."""
    stop, progress = _cancel_after(at)
    with pytest.raises(TransferCancelled):
        _dl(root, progress=progress, cancel=stop).fetch(url, name, **kw)
    have = (root / f"{name}.part").stat().st_size
    assert 0 < have
    return have


# ---------------------------------------------------------------------- resume rules

def test_the_same_file_resumes_with_range_and_if_range(upstream, tmp_path):
    body = _body(600_000, "same")
    _Upstream.files["/f.bin"] = _File(body, etag='"v1"')
    have = _interrupt(tmp_path, f"{upstream}/f.bin", "f.bin")
    assert (tmp_path / "f.bin.part").read_bytes() == body[:have]
    side = read_sidecar(tmp_path / "f.bin.part.json")
    assert side["etag"] == '"v1"' and side["total"] == len(body) and side["url"].endswith("/f.bin")
    _Upstream.seen.clear()
    r = _dl(tmp_path).fetch(f"{upstream}/f.bin", "f.bin")
    assert (tmp_path / "f.bin").read_bytes() == body
    [get] = _gets()
    assert get["range"] == f"bytes={have}-" and get["if_range"] == '"v1"'
    assert r.resumed and r.resumed_from == have and r.verified and not r.restarts
    rec = json.loads((tmp_path / ".downloads.json").read_text())["f.bin"]
    assert rec["resumed"] is True and rec["resumed_from"] == have and rec["etag"] == '"v1"'
    assert rec["server"] == "fixture/1" and rec["retrieved_at"].endswith("Z")
    assert rec["sha256"] == _sha(body) and rec["verified"] is True
    assert not (tmp_path / "f.bin.part").exists() and not (tmp_path / "f.bin.part.json").exists()


def test_an_upstream_change_without_a_checksum_restarts_and_never_splices(upstream, tmp_path):
    v1, v2 = _body(600_000, "release-1"), _body(600_000, "release-2")
    _Upstream.files["/f.bin"] = _File(v1, etag='"v1"')
    _interrupt(tmp_path, f"{upstream}/f.bin", "f.bin")
    _Upstream.files["/f.bin"] = _File(v2, etag='"v2"')            # same size, another file
    _Upstream.seen.clear()
    r = _dl(tmp_path).fetch(f"{upstream}/f.bin", "f.bin")
    assert (tmp_path / "f.bin").read_bytes() == v2, "the new release, whole: no splice"
    [get] = _gets()
    assert get["range"] is None, "the probe showed another ETag: nothing was resumed"
    assert not r.resumed and r.resumed_from is None
    assert any("ETag changed" in why for why in r.restarts)


def test_a_change_between_the_probe_and_the_get_is_caught_by_if_range(upstream, tmp_path):
    v1, v2 = _body(600_000, "probe-1"), _body(600_000, "probe-2")
    _Upstream.files["/f.bin"] = _File(v1, etag='"v1"')
    have = _interrupt(tmp_path, f"{upstream}/f.bin", "f.bin")
    _Upstream.after_head["/f.bin"] = _File(v2, etag='"v2"')       # swapped after the HEAD
    _Upstream.seen.clear()
    r = _dl(tmp_path).fetch(f"{upstream}/f.bin", "f.bin")
    [get] = _gets()
    assert get["range"] == f"bytes={have}-" and get["if_range"] == '"v1"'
    assert (tmp_path / "f.bin").read_bytes() == v2, "a 200 restarts from byte 0 on that answer"
    assert not r.resumed and any("whole file" in why for why in r.restarts)
    assert r.etag == '"v2"'


def test_a_206_for_another_range_is_dropped_and_the_transfer_restarted(upstream, tmp_path):
    body = _body(600_000, "wrong-range")
    _Upstream.files["/f.bin"] = _File(body, etag='"v1"')
    have = _interrupt(tmp_path, f"{upstream}/f.bin", "f.bin")
    _Upstream.script["/f.bin"] = [{"wrong_range": True}]
    _Upstream.seen.clear()
    r = _dl(tmp_path).fetch(f"{upstream}/f.bin", "f.bin")
    first, second = _gets()
    assert first["range"] == f"bytes={have}-" and second["range"] is None
    assert (tmp_path / "f.bin").read_bytes() == body and not r.resumed
    assert any("Content-Range" in why for why in r.restarts)


def test_a_weak_etag_alone_never_resumes_a_file_without_a_checksum(upstream, tmp_path):
    body = _body(600_000, "weak")
    _Upstream.files["/f.bin"] = _File(body, etag='W/"v1"')
    _interrupt(tmp_path, f"{upstream}/f.bin", "f.bin")
    assert read_sidecar(tmp_path / "f.bin.part.json")["etag"] is None, "a weak tag is not kept"
    _Upstream.seen.clear()
    r = _dl(tmp_path).fetch(f"{upstream}/f.bin", "f.bin")
    [get] = _gets()
    assert get["range"] is None and get["if_range"] is None
    assert (tmp_path / "f.bin").read_bytes() == body and not r.resumed
    assert any("identity unverifiable" in why for why in r.restarts)


def test_last_modified_is_a_validator_too(upstream, tmp_path):
    body = _body(600_000, "dated")
    date = "Wed, 14 Oct 2026 08:00:00 GMT"
    _Upstream.files["/f.bin"] = _File(body, last_modified=date)
    have = _interrupt(tmp_path, f"{upstream}/f.bin", "f.bin")
    _Upstream.seen.clear()
    r = _dl(tmp_path).fetch(f"{upstream}/f.bin", "f.bin")
    [get] = _gets()
    assert get["range"] == f"bytes={have}-" and get["if_range"] == date
    assert r.resumed and r.resumed_from == have and r.last_modified == date


def test_a_reviewed_checksum_allows_a_resume_without_validators_and_catches_a_splice(
        upstream, tmp_path):
    v1, v2 = _body(600_000, "pinned-1"), _body(600_000, "pinned-2")
    _Upstream.files["/f.bin"] = _File(v1)                         # no ETag, no Last-Modified
    have = _interrupt(tmp_path, f"{upstream}/f.bin", "f.bin", checksum=_sha(v1))
    _Upstream.files["/f.bin"] = _File(v2)                         # changed, same size
    _Upstream.seen.clear()
    with pytest.raises(DownloadError) as exc:
        _dl(tmp_path).fetch(f"{upstream}/f.bin", "f.bin", checksum=_sha(v1))
    [get] = _gets()
    assert get["range"] == f"bytes={have}-" and get["if_range"] is None
    assert "checksum mismatch" in str(exc.value) and f"joined from two transfers at byte {have}" \
        in str(exc.value)
    status, reason = outcome_of_error(exc.value, "127.0.0.1")
    assert status == FAIL and "not the reviewed one" in reason
    assert not (tmp_path / "f.bin").exists() and not (tmp_path / "f.bin.part").exists()
    assert not (tmp_path / "f.bin.part.json").exists()


def test_a_cancelled_transfer_resumes_into_the_verified_file(upstream, tmp_path):
    body = _body(900_000, "cancel")
    _Upstream.files["/f.bin"] = _File(body)
    have = _interrupt(tmp_path, f"{upstream}/f.bin", "f.bin", checksum=_sha(body))
    r = _dl(tmp_path).fetch(f"{upstream}/f.bin", "f.bin", checksum=_sha(body))
    assert r.resumed and r.resumed_from == have and r.checksum == _sha(body)
    assert (tmp_path / "f.bin").read_bytes() == body


def test_a_dropped_connection_resumes_within_the_same_fetch(upstream, tmp_path):
    body = _body(600_000, "dropped")
    _Upstream.files["/f.bin"] = _File(body, etag='"v1"')
    _Upstream.script["/f.bin"] = [{"truncate": True}]
    r = _dl(tmp_path).fetch(f"{upstream}/f.bin", "f.bin")
    first, second = _gets()
    assert first["range"] is None
    assert second["range"] == f"bytes={len(body) // 2}-" and second["if_range"] == '"v1"'
    assert r.resumed and r.resumed_from == len(body) // 2 and r.attempts == 2
    assert (tmp_path / "f.bin").read_bytes() == body


def test_a_whole_partial_file_is_checked_as_it_is_and_not_downloaded_again(upstream, tmp_path):
    body = _body(300_000, "whole")
    _Upstream.files["/f.bin"] = _File(body)
    (tmp_path / "f.bin.part").write_bytes(body)        # whole: a stop while it was hashed
    r = _dl(tmp_path).fetch(f"{upstream}/f.bin", "f.bin", checksum=_sha(body))
    assert not _gets(), "the reviewed checksum checks it: nothing is requested"
    assert r.checksum == _sha(body) and r.resumed and r.resumed_from == len(body)
    assert (tmp_path / "f.bin").read_bytes() == body and not r.restarts
    # a cancel after the last block, with no checksum: the server's ETag, the recorded one,
    # shows it is the same file
    _Upstream.files["/g.bin"] = _File(body, etag='"g1"')
    have = _interrupt(tmp_path, f"{upstream}/g.bin", "g.bin", at=len(body))
    assert have == len(body)
    _Upstream.seen.clear()
    r = _dl(tmp_path).fetch(f"{upstream}/g.bin", "g.bin")
    assert [s["method"] for s in _Upstream.seen] == ["HEAD"], "the probe alone"
    assert r.verified and r.resumed_from == len(body) and r.etag == '"g1"'
    assert (tmp_path / "g.bin").read_bytes() == body
    assert not (tmp_path / "g.bin.part").exists() and not (tmp_path / "g.bin.part.json").exists()


def test_a_whole_partial_file_the_probe_cannot_place_is_confirmed_by_a_416(upstream, tmp_path):
    body = _body(300_000, "placed")
    _Upstream.files["/f.bin"] = _File(body, etag='"p1"')
    _interrupt(tmp_path, f"{upstream}/f.bin", "f.bin", at=len(body))
    _Upstream.bare_head.add("/f.bin")                 # the HEAD names no ETag
    _Upstream.seen.clear()
    r = _dl(tmp_path).fetch(f"{upstream}/f.bin", "f.bin")
    [get] = _gets()
    assert get["range"] == f"bytes={len(body)}-" and get["if_range"] == '"p1"'
    assert r.resumed_from == len(body) and not r.restarts, "If-Range matched: it is whole"
    assert (tmp_path / "f.bin").read_bytes() == body
    # the same, after the file changed upstream: a 200, and the new file whole
    other = _body(300_000, "placed-2")
    _interrupt(tmp_path, f"{upstream}/f.bin", "f2.bin", at=len(body))
    _Upstream.files["/f.bin"] = _File(other, etag='"p2"')
    _Upstream.seen.clear()
    r = _dl(tmp_path).fetch(f"{upstream}/f.bin", "f2.bin")
    assert (tmp_path / "f2.bin").read_bytes() == other and not r.resumed
    assert any("whole file" in why for why in r.restarts)


def test_a_416_for_a_partial_file_restarts_once(upstream, tmp_path):
    body = _body(300_000, "416")
    _Upstream.files["/f.bin"] = _File(body)
    (tmp_path / "f.bin.part").write_bytes(body[:1000])
    _Upstream.script["/f.bin"] = [{"status": 416,
                                   "headers": {"Content-Range": f"bytes */{len(body) + 7}"}}]
    r = _dl(tmp_path).fetch(f"{upstream}/f.bin", "f.bin", checksum=_sha(body))
    first, second = _gets()
    assert first["range"] == "bytes=1000-" and second["range"] is None
    assert any("HTTP 416" in why for why in r.restarts) and r.checksum == _sha(body)
    assert not r.resumed


def test_a_206_from_another_file_is_caught_by_its_date_when_if_range_is_ignored(upstream,
                                                                              tmp_path):
    v1, v2 = _body(600_000, "ignored-1"), _body(600_000, "ignored-2")
    _Upstream.files["/f.bin"] = _File(v1, last_modified="Wed, 14 Oct 2026 08:00:00 GMT")
    have = _interrupt(tmp_path, f"{upstream}/f.bin", "f.bin")
    # swapped after the probe, by a server that answers the range of whatever it holds
    _Upstream.after_head["/f.bin"] = _File(v2, last_modified="Fri, 16 Oct 2026 08:00:00 GMT")
    _Upstream.script["/f.bin"] = [{"ignore_if_range": True}]
    _Upstream.seen.clear()
    r = _dl(tmp_path).fetch(f"{upstream}/f.bin", "f.bin")
    first, second = _gets()
    assert first["range"] == f"bytes={have}-" and second["range"] is None
    assert (tmp_path / "f.bin").read_bytes() == v2, "the new file, whole: no splice"
    assert not r.resumed and "Last-Modified" in r.restarts[0] and "another file" in r.restarts[0]
    assert "Content-Range" not in r.restarts[0]


def test_a_206_with_another_etag_is_said_to_come_from_another_file(upstream, tmp_path):
    v1, v2 = _body(600_000, "etag-1"), _body(600_000, "etag-2")
    _Upstream.files["/f.bin"] = _File(v1, etag='"v1"')
    _interrupt(tmp_path, f"{upstream}/f.bin", "f.bin")
    _Upstream.after_head["/f.bin"] = _File(v2, etag='"v2"')
    _Upstream.script["/f.bin"] = [{"ignore_if_range": True}]
    r = _dl(tmp_path).fetch(f"{upstream}/f.bin", "f.bin")
    assert (tmp_path / "f.bin").read_bytes() == v2
    assert 'ETag "v2", not the recorded "v1"' in r.restarts[0]
    assert "Content-Range" not in r.restarts[0]


def test_a_date_the_answer_does_not_show_strong_is_never_sent_in_if_range(upstream, tmp_path):
    body = _body(600_000, "weak-date")
    # a Last-Modified after the answer's Date (a clock ahead, a file still being written):
    # not a strong validator (RFC 9110 §8.8.2.2), so not one for If-Range (§13.1.5)
    future = email.utils.formatdate(time.time() + 3600, usegmt=True)
    _Upstream.files["/f.bin"] = _File(body, last_modified=future)
    _interrupt(tmp_path, f"{upstream}/f.bin", "f.bin")
    side = read_sidecar(tmp_path / "f.bin.part.json")
    assert side["last_modified"] == future and side["date"]
    _Upstream.seen.clear()
    r = _dl(tmp_path).fetch(f"{upstream}/f.bin", "f.bin")
    [get] = _gets()
    assert get["range"] is None and get["if_range"] is None
    assert any("identity unverifiable" in why for why in r.restarts)
    # a reviewed checksum still allows the resume, without the weak date in If-Range
    have = _interrupt(tmp_path, f"{upstream}/f.bin", "g.bin", checksum=_sha(body))
    _Upstream.seen.clear()
    r = _dl(tmp_path).fetch(f"{upstream}/f.bin", "g.bin", checksum=_sha(body))
    [get] = _gets()
    assert get["range"] == f"bytes={have}-" and get["if_range"] is None
    assert r.resumed_from == have and r.checksum == _sha(body)


# ------------------------------------------------------------------------- retries

def test_a_missing_file_fails_at_once_with_one_get(upstream, tmp_path):
    with pytest.raises(DownloadError) as exc:
        _dl(tmp_path).fetch(f"{upstream}/gone.bin", "gone.bin")
    assert len(_gets()) == 1, "a 404 is not retried"
    status, reason = outcome_of_error(exc.value, "127.0.0.1")
    assert status == FAIL and "no such file (HTTP 404)" in reason


def test_a_503_with_retry_after_is_honoured_then_the_file_arrives(upstream, tmp_path):
    body = _body(200_000, "busy")
    _Upstream.files["/f.bin"] = _File(body)
    _Upstream.script["/f.bin"] = [{"status": 503, "headers": {"Retry-After": "1"}}]
    r = _dl(tmp_path).fetch(f"{upstream}/f.bin", "f.bin", checksum=_sha(body))
    first, second = _gets()
    assert second["at"] - first["at"] >= 1.0, "the server's Retry-After was waited out"
    assert r.checksum == _sha(body) and r.attempts == 2


def test_a_pause_longer_than_the_cap_ends_the_transfer(upstream, tmp_path):
    _Upstream.files["/f.bin"] = _File(_body(1000, "later"))
    _Upstream.script["/f.bin"] = [{"status": 503, "headers": {"Retry-After": "3600"}}]
    t0 = time.monotonic()
    with pytest.raises(DownloadError) as exc:
        _dl(tmp_path).fetch(f"{upstream}/f.bin", "f.bin")
    assert time.monotonic() - t0 < 30 and len(_gets()) == 1
    assert outcome_of_error(exc.value, "127.0.0.1")[0] == BLOCKED


def test_a_redirect_to_another_host_is_refused(upstream, tmp_path):
    port = upstream.rsplit(":", 1)[1]
    _Upstream.files["/f.bin"] = _File(b"x" * 100)
    _Upstream.script["/away"] = [{"redirect": f"http://localhost:{port}/f.bin"}]
    with pytest.raises(DownloadError) as exc:
        _dl(tmp_path).fetch(f"{upstream}/away", "away.bin")
    status, reason = outcome_of_error(exc.value, "127.0.0.1")
    assert status == FAIL and "redirected" in reason
    assert not (tmp_path / "away.bin").exists()


def test_a_redirect_through_another_host_and_back_never_reaches_that_host(upstream, tmp_path):
    # 127.0.0.1 → localhost → 127.0.0.1: the URL it ends on is the request's host again, so
    # only a check of each hop before it is followed keeps the other host uncontacted (one
    # loopback server answers both names; the path it was asked for says which hop it was)
    port = upstream.rsplit(":", 1)[1]
    _Upstream.files["/real.bin"] = _File(b"y" * 100)
    _Upstream.script["/bounce"] = [{"redirect": f"http://localhost:{port}/hop"}]
    _Upstream.script["/hop"] = [{"redirect": f"http://127.0.0.1:{port}/real.bin"}]
    with pytest.raises(DownloadError) as exc:
        _dl(tmp_path).fetch(f"{upstream}/bounce", "bounce.bin",
                            checksum="sha256:" + hashlib.sha256(b"y" * 100).hexdigest())
    status, reason = outcome_of_error(exc.value, "127.0.0.1")
    assert status == FAIL and "redirected" in reason and "localhost" in reason
    assert "/hop" not in {s["path"] for s in _Upstream.seen}, "the other host was contacted"
    assert not (tmp_path / "bounce.bin").exists()


# ----------------------------------------------------------------- disk and locks

def test_no_get_is_sent_when_the_disk_cannot_hold_the_servers_size(upstream, tmp_path,
                                                                    monkeypatch):
    _Upstream.files["/big.bin"] = _File(_body(2_000_000, "big"))
    monkeypatch.setattr("bioagent.validation.environment.disk_free", lambda path: 1 << 20)
    with pytest.raises(TransferError) as exc:                     # no size declared
        _dl(tmp_path).fetch(f"{upstream}/big.bin", "big.bin")
    assert exc.value.outcome == BLOCKED and "not enough disk space" in str(exc.value)
    assert [s["method"] for s in _Upstream.seen] == ["HEAD"], "the size came from the probe"


def test_a_full_disk_stops_at_once_and_keeps_the_partial_file(upstream, tmp_path, monkeypatch):
    body = _body(600_000, "enospc")
    _Upstream.files["/f.bin"] = _File(body, etag='"v1"')
    writes = []

    def full(fh, block):
        if writes:
            raise OSError(errno.ENOSPC, "No space left on device")
        writes.append(len(block))
        fh.write(block)
    monkeypatch.setattr(VerifiedTransfer, "_write_block", staticmethod(full))
    with pytest.raises(TransferError) as exc:
        _dl(tmp_path).fetch(f"{upstream}/f.bin", "f.bin")
    assert exc.value.outcome == BLOCKED and "not enough disk space" in str(exc.value)
    assert len(_gets()) == 1, "a full disk is not retried"
    assert (tmp_path / "f.bin.part").read_bytes() == body[:writes[0]]
    assert read_sidecar(tmp_path / "f.bin.part.json")["etag"] == '"v1"'
    assert outcome_of_error(exc.value, "127.0.0.1")[0] == BLOCKED


def test_a_host_that_refuses_head_still_gives_its_size_to_the_disk_check(upstream, tmp_path,
                                                                         monkeypatch):
    _Upstream.files["/big.bin"] = _File(_body(2_000_000, "head-403"))
    _Upstream.head_status["/big.bin"] = 403              # a signed URL that allows GET only
    monkeypatch.setattr("bioagent.validation.environment.disk_free", lambda path: 1 << 20)
    with pytest.raises(TransferError) as exc:
        _dl(tmp_path).fetch(f"{upstream}/big.bin", "big.bin")
    assert exc.value.outcome == BLOCKED and "not enough disk space" in str(exc.value)
    assert [(s["method"], s["range"]) for s in _Upstream.seen] == [
        ("HEAD", None), ("GET", "bytes=0-0")], "the ranged GET gave the size; no download"


def _full_disk_on(suffix: str, monkeypatch) -> None:
    from bioagent.acquisition import transfer

    real = transfer._write_json

    def write(path, doc):
        if str(path).endswith(suffix):
            raise OSError(errno.ENOSPC, "No space left on device")
        real(path, doc)
    monkeypatch.setattr(transfer, "_write_json", write)


def test_a_full_disk_while_the_identity_record_is_written_is_the_disks(upstream, tmp_path,
                                                                       monkeypatch):
    _Upstream.files["/f.bin"] = _File(_body(200_000, "side"), etag='"v1"')
    _full_disk_on(".part.json", monkeypatch)
    with pytest.raises(TransferError) as exc:
        _dl(tmp_path).fetch(f"{upstream}/f.bin", "f.bin")
    assert exc.value.outcome == BLOCKED and "not enough disk space" in str(exc.value)
    assert "identity record" in str(exc.value) and "nothing was kept" in str(exc.value)
    status, reason = outcome_of_error(exc.value, "127.0.0.1")
    assert status == BLOCKED and "127.0.0.1" not in reason, "never blamed on the host"
    assert not list(tmp_path.glob("f.bin*")) and not list(tmp_path.glob(".f.bin*"))


def test_a_full_disk_while_the_record_is_written_keeps_nothing_unrecorded(upstream, tmp_path,
                                                                          monkeypatch):
    body = _body(200_000, "record")
    _Upstream.files["/f.bin"] = _File(body, etag='"v1"')
    _full_disk_on(".downloads.json", monkeypatch)
    with pytest.raises(TransferError) as exc:
        _dl(tmp_path).fetch(f"{upstream}/f.bin", "f.bin")
    assert exc.value.outcome == BLOCKED and "record of the kept file" in str(exc.value)
    assert not (tmp_path / "f.bin").exists(), "no file kept without its record"
    assert (tmp_path / "f.bin.part").read_bytes() == body
    assert read_sidecar(tmp_path / "f.bin.part.json")["etag"] == '"v1"'
    monkeypatch.undo()
    _Upstream.seen.clear()
    r = _dl(tmp_path).fetch(f"{upstream}/f.bin", "f.bin")
    assert not _gets(), "once space is freed it is checked as it is, not downloaded again"
    assert r.resumed_from == len(body) and (tmp_path / "f.bin").read_bytes() == body
    assert json.loads((tmp_path / ".downloads.json").read_text())["f.bin"]["etag"] == '"v1"'


def test_a_cancel_is_seen_as_soon_as_a_block_arrives_not_after_a_whole_chunk(upstream,
                                                                              tmp_path):
    body = _body(600_000, "slow")
    _Upstream.files["/f.bin"] = _File(body, etag='"v1"')
    _Upstream.hold["/f.bin"] = threading.Event()     # 64 KiB, then nothing for 20 s
    stop, progress = _cancel_after(1)
    t0 = time.monotonic()
    with pytest.raises(TransferCancelled) as exc:     # the default 1 MiB chunk
        VerifiedTransfer(tmp_path, log=lambda m: None, progress=progress,
                         cancel=stop).fetch(f"{upstream}/f.bin", "f.bin")
    assert time.monotonic() - t0 < 5, "a 1 MiB read would have waited for the server"
    assert 0 < exc.value.have <= BLOCK
    assert (tmp_path / "f.bin.part").read_bytes() == body[:exc.value.have]


def test_a_cancelled_process_is_ended_after_its_grace_even_inside_a_read(tmp_path):
    import os
    import signal
    import subprocess
    import sys

    src = Path(__file__).resolve().parents[1] / "src"
    code = ("import sys, time\n"
            "from bioagent.acquisition.service import cancel_on_signals\n"
            "cancel_on_signals(grace_s=0.5)\n"
            "print('ready', flush=True)\n"
            "while True:\n"
            "    time.sleep(0.05)   # a read the server never answers\n")
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(
        [str(src), os.environ.get("PYTHONPATH", "")])}
    proc = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, env=env,
                            text=True)
    try:
        assert proc.stdout.readline().strip() == "ready"
        t0 = time.monotonic()
        proc.send_signal(signal.SIGTERM)
        assert proc.wait(timeout=15) == 130
        assert time.monotonic() - t0 < 10
    finally:
        if proc.poll() is None:
            proc.kill()


def test_two_fetches_of_one_file_one_is_refused_and_the_file_is_whole(upstream, tmp_path):
    body = _body(800_000, "twice")
    _Upstream.files["/f.bin"] = _File(body, etag='"v1"')
    _Upstream.hold["/f.bin"] = release = threading.Event()
    results: dict[str, object] = {}

    def first():
        try:
            results["first"] = _dl(tmp_path).fetch(f"{upstream}/f.bin", "f.bin",
                                                   checksum=_sha(body))
        except Exception as exc:                                # noqa: BLE001
            results["first"] = exc
    t = threading.Thread(target=first)
    t.start()
    part = tmp_path / "f.bin.part"
    deadline = time.monotonic() + 10
    while not (part.exists() and part.stat().st_size >= BLOCK) and time.monotonic() < deadline:
        time.sleep(0.01)
    with pytest.raises(TransferError) as exc:
        _dl(tmp_path).fetch(f"{upstream}/f.bin", "f.bin", checksum=_sha(body))
    assert exc.value.outcome == BLOCKED and "another acquisition" in str(exc.value)
    release.set()
    t.join(20)
    assert getattr(results["first"], "checksum", None) == _sha(body)
    assert (tmp_path / "f.bin").read_bytes() == body
    assert len(_gets()) == 1, "the refused fetch asked the server for nothing"


# ---------------------------------------------------------------- content checks

def test_a_corrupt_gzip_is_refused_and_not_kept(upstream, tmp_path):
    good = gzip.compress(b"org_id\tnp_id\n" + b"NPO1\tNPC1\n" * 50_000, mtime=0)
    truncated = good[: len(good) // 2]
    flipped = bytearray(good)
    flipped[len(good) // 2] ^= 0xFF
    for name, data in (("cut.tsv.gz", truncated), ("flip.tsv.gz", bytes(flipped))):
        _Upstream.files[f"/{name}"] = _File(data)
        with pytest.raises(TransferError) as exc:
            _dl(tmp_path).fetch(f"{upstream}/{name}", name, expected_bytes=len(data))
        assert exc.value.outcome == FAIL and exc.value.check == "decompression"
        assert "not kept" in str(exc.value)
        assert not (tmp_path / name).exists() and not (tmp_path / f"{name}.part").exists()
    _Upstream.files["/good.tsv.gz"] = _File(good)
    r = _dl(tmp_path).fetch(f"{upstream}/good.tsv.gz", "good.tsv.gz")
    assert r.checks["decompression"]["status"] == PASS
    assert r.checks["decompression"]["uncompressed_bytes"] == len(gzip.decompress(good))


def test_a_headerless_table_must_have_the_columns_its_spec_declares(upstream, tmp_path):
    _Upstream.files["/pairs.txt"] = _File(b"NPO1\nNPO2\n")
    with pytest.raises(TransferError) as exc:
        _dl(tmp_path).fetch(f"{upstream}/pairs.txt", "pairs.txt", fmt="tsv",
                            columns=("plant_id", "np_id"))
    assert exc.value.check == "format" and "declares 2" in str(exc.value)
    _Upstream.files["/pairs2.txt"] = _File(b"NPO1\tNPC1\n")
    r = _dl(tmp_path).fetch(f"{upstream}/pairs2.txt", "pairs2.txt", fmt="tsv",
                            columns=("plant_id", "np_id"))
    assert r.checks["format"]["status"] == PASS


def test_more_fields_than_declared_pass_and_a_reviewed_file_skips_the_heuristic(upstream,
                                                                                tmp_path):
    _Upstream.files["/wide.txt"] = _File(b"NPO1\tNPC1\textra\t\n")
    r = _dl(tmp_path).fetch(f"{upstream}/wide.txt", "wide.txt", fmt="tsv",
                            columns=("plant_id", "np_id"))
    assert r.checks["format"]["status"] == PASS, "as tcmdb.store reads it"
    reviewed = b"# a header line the heuristic cannot read\nNPO1\tNPC1\n"
    _Upstream.files["/reviewed.txt"] = _File(reviewed)
    r = _dl(tmp_path).fetch(f"{upstream}/reviewed.txt", "reviewed.txt", fmt="tsv",
                            columns=("plant_id", "np_id"), checksum=_sha(reviewed))
    assert "format" not in r.checks and (tmp_path / "reviewed.txt").read_bytes() == reviewed


def test_archives_are_read_through_in_bounded_blocks(tmp_path):
    import zipfile
    z = tmp_path / "a.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("x.txt", "hello\n" * 1000)
    assert check_archive(z)[0] == PASS
    data = bytearray(z.read_bytes())
    data[40] ^= 0xFF                                             # inside the member's data
    bad = tmp_path / "bad.zip"
    bad.write_bytes(bytes(data))
    assert check_archive(bad)[0] == FAIL
    plain = tmp_path / "x.tsv"
    plain.write_text("a\tb\n")
    assert check_archive(plain)[0] == "NOT_APPLICABLE"


# --------------------------------------------------------- a dataset, end to end

def _ik(i: int) -> str:
    """A well-formed InChIKey for compound ``i`` (letters only, as the format has them)."""
    letters = "".join(chr(65 + b % 26) for b in hashlib.sha256(f"ik{i}".encode()).digest())
    return f"{letters[:14]}-{letters[14:24]}-N"


def _npass_files(n: int = 40_000) -> dict[str, bytes]:
    """NPASS-shaped tables, several MB in all, generated the same way every time."""
    general = ["np_id\tpref_name"] + [f"NPC{i}\tcompound {i}" for i in range(n)]
    structure = ["np_id\tInChIKey"] + [f"NPC{i}\t{_ik(i)}" for i in range(n)]
    species = ["org_id\torg_name\torg_tax_id"] + [f"NPO{i}\tSpecies {i}\t{100000 + i}"
                                                  for i in range(n // 10)]
    pairs = ["org_id\tnp_id\tref_id\tref_id_type"] + [
        f"NPO{i % (n // 10)}\tNPC{i}\t{1000000 + i}\tPMID" for i in range(n)]
    targets = ["target_id\ttarget_name\tuniprot_id"] + [
        f"NPT{i}\ttarget {i}\tP{10000 + i}" for i in range(500)]
    activities = ["np_id\ttarget_id\tactivity_type\tactivity_value\tactivity_units\tref_id\t"
                  "ref_id_type"] + [f"NPC{i}\tNPT{i % 500}\tIC50\t{(i % 97) + 1}\tnM\t"
                                    f"{2000000 + i}\tPMID" for i in range(n)]
    text = {"generalInfo": general, "structureInfo": structure, "speciesInfo": species,
            "species_pair": pairs, "targetInfo": targets, "activities": activities}
    return {part: ("\n".join(lines) + "\n").encode() for part, lines in text.items()}


def test_a_dataset_is_acquired_decompressed_built_and_queried(upstream, tmp_path, monkeypatch):
    tables = _npass_files()
    real = real_dataset("npass")
    files = []
    for f in real.files:
        part = f.name.split("naturalProducts_", 1)[1].rsplit(".", 1)[0]
        data = tables[part]
        name = f.name
        if part == "activities":                 # one file of the set is served gzipped
            data, name = gzip.compress(data, mtime=0), f.name + ".gz"
        _Upstream.files[f"/{name}"] = _File(data, etag=f'"{part}"')
        files.append(replace(f, url=f"{upstream}/{name}", name=name, checksum=_sha(data),
                             expected_bytes=len(data)))
    spec = replace(real, files=tuple(files), min_interval_s=0.0)
    monkeypatch.setattr(hub_module, "dataset", lambda key: spec)
    assert sum(len(v) for v in tables.values()) > 5_000_000, "several MB, not a toy"
    hub = TCMDataHub(tmp_path / "hub")
    planned = plan(hub, "npass")
    assert planned["download_bytes"] == sum(f.expected_bytes for f in files)
    assert planned["hosts"] == ["127.0.0.1"] and planned["unknown_sizes"] == 0
    doc = acquire_dataset(hub, "npass", log=lambda m: None)
    assert doc["status"] == PASS and doc["kept"] == 6 and doc["verified"]["checksum"] == 6
    audit = read_audit(hub.root, "npass")
    assert {e["status"] for e in audit} == {PASS}
    gz = next(e for e in audit if e["file"].endswith(".gz"))
    assert gz["decompression"]["status"] == PASS and gz["release"] == "2.0"
    assert gz["etag"] == '"activities"' and gz["server"] == "fixture/1"
    state = acquisition_state(hub, "npass")
    assert state["states"]["kept"] == 6 and state["progress"]["fraction"] == 1.0
    assert state["progress"]["verified"] == {"checksum": 6, "size": 0, "none": 0}
    report = hub.build("npass", log=lambda m: None)
    assert report["tables"]["activities"] == 40_000
    assert report["relations"]["compound_target"] > 0
    # a downstream query reads the built store: compound 7's measured target
    hits = hub.relations("compound_target", subject=f"inchikey:{_ik(7)}", sources=["npass"])
    assert [(h["object_id"], h["reference"]) for h in hits] == [("uniprot:P10007", "pmid:2000007")]
    # a second acquisition finds every file verified on disk and downloads nothing
    _Upstream.seen.clear()
    again = acquire_dataset(hub, "npass", log=lambda m: None)
    assert all(r["cached"] for r in again["files"]) and not _gets()



def test_a_cancelled_acquisition_is_recorded_and_resumes(upstream, tmp_path, monkeypatch):
    body = _body(3_000_000, "dataset-cancel")          # the service reads 1 MiB blocks
    spec = replace(real_dataset("lotus"), files=(
        replace(real_dataset("lotus").files[0], url=f"{upstream}/pairs.csv",
                name="pairs.csv", checksum=_sha(body), expected_bytes=len(body), optional=False),),
        min_interval_s=0.0)
    monkeypatch.setattr(hub_module, "dataset", lambda key: spec)
    _Upstream.files["/pairs.csv"] = _File(body, etag='"p1"')
    hub = TCMDataHub(tmp_path / "hub")
    stop, progress = _cancel_after(150_000)
    doc = acquire_dataset(hub, "lotus", log=lambda m: None, progress=progress, cancel=stop)
    assert doc["cancelled"] and doc["status"] == SKIPPED
    [entry] = read_audit(hub.root, "lotus")
    assert entry["status"] == SKIPPED and "cancelled" in entry["reason"]
    state = acquisition_state(hub, "lotus", running=False)
    [row] = state["files"]
    assert row["state"] == "pending" and row["resumable"] and 0 < row["have_bytes"] < len(body)
    running = acquisition_state(hub, "lotus", running=True)["files"][0]
    assert running["state"] == "transferring"
    done = acquire_dataset(hub, "lotus", log=lambda m: None)
    assert done["status"] == PASS and done["files"][0]["resumed_from"] == row["have_bytes"]
    last = read_audit(hub.root, "lotus")[-1]
    assert last["resumed"] is True and last["resumed_from"] == row["have_bytes"]


def test_the_snapshot_pipeline_adopts_the_hubs_verified_copy_and_stores_it_once(upstream,
                                                                               tmp_path):
    import os

    from bioagent.acquisition.sources import AcquisitionSpec
    from bioagent.sources import pipeline

    body = _body(50_000, "mirror")
    _Upstream.files["/a.txt"] = _File(body)
    spec = AcquisitionSpec(f"{upstream}/a.txt", "a.txt", "127.0.0.1", "CC0-1.0", "a file",
                           "test", expected_bytes=len(body), checksum=_sha(body), version="2.0")
    hub_raw = tmp_path / "tcmdb" / "raw" / "npass"
    hub_raw.mkdir(parents=True)
    (hub_raw / "a.txt").write_bytes(body)
    raw = tmp_path / "lake" / "raw"
    [row] = pipeline._acquire(raw, [spec], confirm=False, offline=False, log=lambda m: None,
                              mirrors=[hub_raw])
    assert row["status"] == PASS and row["route"] == "local-mirror" and not _Upstream.seen
    assert os.path.samefile(raw / "a.txt", hub_raw / "a.txt"), "one copy on disk"
    # a copy that differs is not adopted: the reviewed file is downloaded, verified
    other = tmp_path / "other"
    other.mkdir()
    (other / "a.txt").write_bytes(body[::-1])
    [row] = pipeline._acquire(tmp_path / "raw2", [spec], confirm=False, offline=False,
                              log=lambda m: None, mirrors=[other])
    assert row["status"] == PASS and row["route"] == "download" and row["release"] == "2.0"
    assert (tmp_path / "raw2" / "a.txt").read_bytes() == body


def test_a_copy_the_disk_cannot_adopt_is_downloaded_instead(upstream, tmp_path, monkeypatch):
    from bioagent.acquisition import service

    body = _body(50_000, "adopt")
    mirror = tmp_path / "mirror"
    mirror.mkdir()
    (mirror / "a.txt").write_bytes(body)
    raw = tmp_path / "raw"

    def full(path, algorithms):
        raise OSError(errno.ENOSPC, "No space left on device")
    monkeypatch.setattr(service, "_digests", full)
    assert service.adopt_verified([mirror], raw, "a.txt", _sha(body), log=lambda m: None) is None
    assert not list(raw.glob(".a.txt.adopt.*")) and not (raw / "a.txt").exists()
    monkeypatch.undo()
    # an adopted file leaves no partial download of itself behind
    (raw / "a.txt.part").write_bytes(body[:100])
    (raw / "a.txt.part.json").write_text("{}")
    got = service.adopt_verified([mirror], raw, "a.txt", _sha(body), log=lambda m: None)
    assert got["route"] == "local-mirror" and (raw / "a.txt").read_bytes() == body
    assert not (raw / "a.txt.part").exists() and not (raw / "a.txt.part.json").exists()


# ------------------------------------------- the live-data resume check, on loopback

def _live_spec(url: str, name: str, checksum: str | None = None):
    from bioagent.acquisition.sources import AcquisitionSpec
    return AcquisitionSpec(url, name, "127.0.0.1", "CC0-1.0", "a file", "test",
                           checksum=checksum, fmt="text", version="current")


def test_the_live_resume_check_keeps_the_halfs_identity_for_an_unpinned_file(upstream,
                                                                             tmp_path):
    from bioagent.validation import NOT_APPLICABLE, data

    body = b"P12345\n" * 60_000                       # no reviewed checksum or size
    _Upstream.files["/UniProt2Reactome.txt"] = _File(body, etag='"r97"')
    [rec] = data.validate_files([_live_spec(f"{upstream}/UniProt2Reactome.txt",
                                            "UniProt2Reactome.txt")], tmp_path / "raw",
                                live=True, log=lambda m: None)
    check = rec.check("resume")
    assert check.status == PASS, check.detail
    resumed = [s for s in _gets() if s["range"] and s["range"] != "bytes=0-0"
               and not s["range"].startswith("bytes=0-")]
    assert resumed and resumed[0]["if_range"] == '"r97"', "the resume was sent with If-Range"
    assert rec.check("download").status in (PASS, "UNVERIFIED") and rec.status != FAIL
    # a server that gives nothing to validate a range with: not tested, never a FAIL
    _Upstream.files["/plain.txt"] = _File(body)
    _Upstream.seen.clear()
    [rec] = data.validate_files([_live_spec(f"{upstream}/plain.txt", "plain.txt")],
                                tmp_path / "raw2", live=True, log=lambda m: None)
    check = rec.check("resume")
    assert check.status == NOT_APPLICABLE and "validator or a reviewed checksum" in check.detail
    assert not any(s["range"] and s["range"] != "bytes=0-0" for s in _gets()), \
        "nothing was fetched for it"
    assert rec.status != FAIL
