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


# ------------------------------------------- the verified acquisition (acquisition.service)
# The same hub, the same server: `bioagent tcmdb fetch` and Studio's tcmdb.fetch job run
# acquisition.service.acquire_dataset, which sends each file through the hub's _fetch_file
# with an acquisition.transfer.VerifiedTransfer.

from dataclasses import replace  # noqa: E402

from bioagent.acquisition.service import acquire_dataset, acquisition_state  # noqa: E402


def test_the_service_keeps_the_hubs_rules_and_records_how_each_file_was_obtained(hub, server):
    hub._spec["value"] = replace(_spec(server), version="2026.1")
    _Files.files["/general.txt"] = GOOD
    doc = acquire_dataset(hub, "fixture", log=lambda m: None)
    [r] = doc["files"]
    assert doc["status"] == PASS and r["verified"] == "checksum" and doc["verified"]["checksum"] == 1
    [entry] = read_audit(hub.root, "fixture")
    assert entry["route"] == "download" and entry["status"] == PASS and entry["release"] == "2026.1"
    assert entry["sha256"] == SHA and entry["retrieved_at"].endswith("Z")
    assert entry["final_url"].endswith("/general.txt") and entry["resumed"] is False
    record = json.loads((hub.raw_dir("fixture") / ".downloads.json").read_text())["general.txt"]
    assert record["release"] == "2026.1" and record["checksum"] == SHA, "fields added, none renamed"
    assert {"url", "bytes", "checksum", "verified", "fetched_at"} <= set(record)
    again = acquire_dataset(hub, "fixture", log=lambda m: None)
    assert again["files"][0]["cached"] and read_audit(hub.root)[-1]["route"] == "cache"


def test_the_service_learns_a_refused_host_once_as_the_hub_does(hub, server):
    hub._spec["value"] = DatasetSpec(
        "fixture", "Fixture DB", (0,), "https://example.org", "CC0-1.0",
        (FileSpec(f"{server}/denied.txt", "denied.txt", "a", checksum=SHA),
         FileSpec(f"{server}/general.txt", "general.txt", "b", checksum=SHA)))
    _Files.files["/general.txt"] = GOOD
    _Files.policy = {"/denied.txt"}
    first, second = acquire_dataset(hub, "fixture", log=lambda m: None)["files"]
    assert first["status"] == BLOCKED and "network policy here denies" in first["reason"]
    assert second["status"] == BLOCKED and "not requested again" in second["reason"]
    assert not any(path == "/general.txt" for _, path, _ in _Files.seen)
    states = acquisition_state(hub, "fixture")["files"]
    assert [s["state"] for s in states] == ["refused", "refused"]


def test_a_file_that_arrived_and_was_refused_is_rejected_not_refused(hub, server):
    hub._spec["value"] = DatasetSpec(
        "fixture", "Fixture DB", (0,), "https://example.org", "CC0-1.0",
        (FileSpec(f"{server}/general.txt", "general.txt", "a", checksum=SHA),
         FileSpec(f"{server}/page.txt", "page.txt", "b")))
    _Files.files["/general.txt"] = GOOD.replace(b"glycyrrhizin", b"glycyrrhizln")
    _Files.files["/page.txt"] = b"<!DOCTYPE html><html><body>Please log in</body></html>"
    files = acquire_dataset(hub, "fixture", log=lambda m: None)["files"]
    assert [(f["status"], f.get("check")) for f in files] == [(FAIL, "checksum"), (BLOCKED, "html")]
    state = acquisition_state(hub, "fixture")
    assert [(s["state"], s.get("check")) for s in state["files"]] == [
        ("rejected", "checksum"), ("rejected", "html")]
    assert state["states"]["rejected"] == 2 and not any(hub.raw_dir("fixture").glob("*.txt"))


def test_a_partial_file_without_a_validator_resumes_only_under_a_reviewed_checksum(hub, server):
    _Files.files["/general.txt"] = GOOD
    raw = hub.raw_dir("fixture")
    raw.mkdir(parents=True)
    half = len(GOOD) // 2
    (raw / "general.txt.part").write_bytes(GOOD[:half])
    [r] = acquire_dataset(hub, "fixture", log=lambda m: None)["files"]
    assert r["status"] == PASS and r["resumed_from"] == half
    assert ("GET", "/general.txt", f"bytes={half}-") in _Files.seen
    # with only a size to check it, the same partial file is not trusted: fetched whole
    hub._spec["value"] = _spec(server, checksum=None, size=len(GOOD))
    (raw / "general.txt").unlink()
    (raw / "general.txt.part").write_bytes(GOOD[:half])
    _Files.seen.clear()
    [r] = acquire_dataset(hub, "fixture", log=lambda m: None)["files"]
    assert r["status"] == PASS and "resumed_from" not in r
    assert [rng for m, _, rng in _Files.seen if m == "GET"] == [None]
    assert "identity unverifiable" in read_audit(hub.root)[-1]["restarts"][0]


def test_a_corrupt_archive_is_rejected_when_it_is_acquired(hub, server):
    import gzip
    good = gzip.compress(GOOD, mtime=0)
    cut = good[: len(good) // 2]
    hub._spec["value"] = DatasetSpec(
        "fixture", "Fixture DB", (0,), "https://example.org", "CC0-1.0",
        (FileSpec(f"{server}/general.txt.gz", "general.txt.gz", "general",
                  expected_bytes=len(cut)),))
    _Files.files["/general.txt.gz"] = cut
    [r] = acquire_dataset(hub, "fixture", log=lambda m: None)["files"]
    assert r["status"] == FAIL and r["check"] == "decompression" and "not kept" in r["reason"]
    assert not (hub.raw_dir("fixture") / "general.txt.gz").exists()
    assert acquisition_state(hub, "fixture")["files"][0]["state"] == "rejected"


def test_a_verified_copy_elsewhere_is_adopted_as_a_hard_link_not_downloaded(hub, server, tmp_path):
    import os
    mirror = tmp_path / "lake" / "raw"
    mirror.mkdir(parents=True)
    (mirror / "general.txt").write_bytes(GOOD)
    _Files.files["/general.txt"] = GOOD
    [r] = acquire_dataset(hub, "fixture", mirrors=[mirror], log=lambda m: None)["files"]
    assert r["status"] == PASS and r["route"] == "local-mirror"
    assert not _Files.seen, "nothing was requested"
    kept = hub.raw_dir("fixture") / "general.txt"
    assert os.path.samefile(kept, mirror / "general.txt"), "one copy on disk"
    assert read_audit(hub.root)[-1]["route"] == "local-mirror"
    assert hub.status("fixture")[0]["verification"] == {"general.txt": "checksum"}
    # a copy that is not the reviewed file is left alone and the file downloaded
    other = tmp_path / "other"
    other.mkdir()
    (other / "general.txt").write_bytes(GOOD + b"x")
    hub2 = TCMDataHub(tmp_path / "hub2")
    [r2] = acquire_dataset(hub2, "fixture", mirrors=[other], log=lambda m: None)["files"]
    assert r2["status"] == PASS and r2.get("route") is None and r2["cached"] is False
    assert any(m == "GET" for m, _, _ in _Files.seen)
    # and a file with no reviewed checksum is never adopted
    hub._spec["value"] = _spec(server, checksum=None, size=len(GOOD))
    hub3 = TCMDataHub(tmp_path / "hub3")
    _Files.seen.clear()
    acquire_dataset(hub3, "fixture", mirrors=[mirror], log=lambda m: None)
    assert any(m == "GET" for m, _, _ in _Files.seen)
