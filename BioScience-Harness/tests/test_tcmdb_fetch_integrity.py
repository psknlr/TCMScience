"""hub.fetch keeps only verified files and records every attempt; import_raw holds a copy
to the same standard.

The files are served over real HTTP from the loopback interface (with byte ranges, so an
interrupted download resumes), and the dataset spec points at that server.
"""
from __future__ import annotations

import hashlib
import http.server
import json
import threading

import pytest

from bioagent.tcmdb import hub as hub_module
from bioagent.tcmdb import HubError, TCMDataHub
from bioagent.tcmdb.acquire import read_audit
from bioagent.tcmdb.spec import DatasetSpec, FileSpec
from bioagent.validation import BLOCKED, FAIL, PASS

GOOD = b"np_id\tpref_name\nNPC1\tglycyrrhizin\n" * 200
SHA = "sha256:" + hashlib.sha256(GOOD).hexdigest()


class _Files(http.server.BaseHTTPRequestHandler):
    files: dict[str, bytes] = {}
    policy: set[str] = set()
    seen: list[tuple[str, str, str | None]] = []

    def log_message(self, *args):
        pass

    def _send(self, body: bool):
        self.seen.append((self.command, self.path, self.headers.get("Range")))
        if self.path in self.policy:                            # as an egress proxy answers
            self.send_response(403)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            if body:
                self.wfile.write(b"Forbidden: blocked by the sandbox egress policy")
            return
        data = self.files.get(self.path)
        if data is None:
            self.send_response(404)
            self.end_headers()
            return
        rng = self.headers.get("Range")
        if rng and rng.startswith("bytes="):
            start, _, end = rng[6:].partition("-")
            start = int(start)
            end = int(end) if end else len(data) - 1
            chunk = data[start:end + 1]
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{len(data)}")
        else:
            chunk = data
            self.send_response(200)
            self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(len(chunk)))
        self.end_headers()
        if body:
            self.wfile.write(chunk)

    def do_HEAD(self):
        self._send(False)

    def do_GET(self):
        self._send(True)


@pytest.fixture
def server():
    _Files.files, _Files.seen, _Files.policy = {}, [], set()
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Files)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def _spec(base: str, *, checksum: str | None = SHA, size: int | None = None) -> DatasetSpec:
    return DatasetSpec("fixture", "Fixture DB", (0,), "https://example.org", "CC0-1.0",
                       (FileSpec(f"{base}/general.txt", "general.txt", "general",
                                 checksum=checksum, expected_bytes=size),))


@pytest.fixture
def hub(tmp_path, monkeypatch, server):
    spec = {"value": _spec(server)}
    monkeypatch.setattr(hub_module, "dataset", lambda key: spec["value"])
    h = TCMDataHub(tmp_path / "hub")
    h._spec = spec                                              # the test swaps it
    return h


def test_a_file_is_kept_only_when_it_hashes_to_the_reviewed_checksum(hub):
    _Files.files["/general.txt"] = GOOD
    [r] = hub.fetch("fixture", log=lambda m: None)
    assert r["status"] == PASS and r["verified"] == "checksum" and r["ok"]
    assert hub.status("fixture")[0]["verification"] == {"general.txt": "checksum"}
    [again] = hub.fetch("fixture", log=lambda m: None)
    assert again["status"] == PASS and again["cached"], "verified again from the local copy"
    audit = read_audit(hub.root, "fixture")
    assert [(e["route"], e["status"]) for e in audit] == [("download", PASS), ("cache", PASS)]
    assert audit[0]["checksum"] == SHA and audit[0]["reviewed_checksum"] == SHA


def test_a_file_that_differs_from_the_reviewed_one_is_refused_and_recorded(hub):
    _Files.files["/general.txt"] = GOOD.replace(b"glycyrrhizin", b"glycyrrhizln")
    [r] = hub.fetch("fixture", log=lambda m: None)
    assert r["status"] == FAIL and not r["ok"] and "not the reviewed one" in r["reason"]
    assert not (hub.raw_dir("fixture") / "general.txt").exists()
    assert not (hub.raw_dir("fixture") / "general.txt.part").exists(), "no partial file kept"
    assert read_audit(hub.root)[0]["status"] == FAIL


def test_a_size_is_what_verifies_a_file_without_a_checksum(hub, server):
    hub._spec["value"] = _spec(server, checksum=None, size=len(GOOD))
    _Files.files["/general.txt"] = GOOD
    [r] = hub.fetch("fixture", log=lambda m: None)
    assert r["status"] == PASS and r["verified"] == "size"
    assert hub.status("fixture")[0]["verification"] == {"general.txt": "size"}


def test_an_interrupted_download_resumes_from_its_partial_file(hub):
    _Files.files["/general.txt"] = GOOD
    raw = hub.raw_dir("fixture")
    raw.mkdir(parents=True)
    (raw / "general.txt.part").write_bytes(GOOD[: len(GOOD) // 2])
    [r] = hub.fetch("fixture", log=lambda m: None)
    assert r["status"] == PASS and (raw / "general.txt").read_bytes() == GOOD
    assert ("GET", "/general.txt", f"bytes={len(GOOD) // 2}-") in _Files.seen


def test_a_host_the_policy_refuses_is_learnt_once_and_not_asked_again(hub, server):
    hub._spec["value"] = DatasetSpec(
        "fixture", "Fixture DB", (0,), "https://example.org", "CC0-1.0",
        (FileSpec(f"{server}/denied.txt", "denied.txt", "a", checksum=SHA),
         FileSpec(f"{server}/general.txt", "general.txt", "b", checksum=SHA)))
    _Files.files["/general.txt"] = GOOD
    _Files.policy = {"/denied.txt"}
    first, second = hub.fetch("fixture", log=lambda m: None)
    assert first["status"] == BLOCKED and "network policy here denies" in first["reason"]
    assert second["status"] == BLOCKED and "not requested again" in second["reason"]
    assert not any(path == "/general.txt" for _, path, _ in _Files.seen), \
        "the second file on a refused host is never requested"
    assert [e["status"] for e in read_audit(hub.root)] == [BLOCKED, BLOCKED]


def test_nothing_starts_when_the_disk_cannot_hold_the_file(hub, monkeypatch, server):
    hub._spec["value"] = _spec(server, size=50 << 20)
    _Files.files["/general.txt"] = GOOD
    monkeypatch.setattr("bioagent.validation.environment.disk_free", lambda path: 10 << 20)
    [r] = hub.fetch("fixture", log=lambda m: None)
    assert r["status"] == BLOCKED and "not enough disk space" in r["reason"]
    assert not any(m == "GET" for m, _, _ in _Files.seen)


def test_an_import_is_held_to_the_same_checksum_as_a_download(hub, tmp_path):
    good = tmp_path / "general.txt"
    good.write_bytes(GOOD)
    r = hub.import_raw("fixture", good, log=lambda m: None)
    assert r["status"] == PASS and r["verified"] == "checksum"
    assert (hub.raw_dir("fixture") / "general.txt").read_bytes() == GOOD
    assert hub.status("fixture")[0]["verification"] == {"general.txt": "checksum"}
    other = tmp_path / "copy.txt"
    other.write_bytes(GOOD + b"extra\n")
    with pytest.raises(HubError, match="not the reviewed general.txt"):
        hub.import_raw("fixture", other, name="general.txt", log=lambda m: None)
    with pytest.raises(HubError, match="not one of"):
        hub.import_raw("fixture", other, log=lambda m: None)
    routes = [(e["route"], e["status"]) for e in read_audit(hub.root)]
    assert routes == [("import", PASS), ("import", FAIL)]
    assert json.loads((hub.raw_dir("fixture") / ".downloads.json").read_text())[
        "general.txt"]["route"] == "import"
