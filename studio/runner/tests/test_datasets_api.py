"""The runner's dataset routes (CONTRACTS §4 /api/datasets) and acquisitions through them.

The acquisitions run the real ``tcmdb.fetch`` job — ``bioagent tcmdb fetch`` →
``acquisition.service.acquire_dataset`` in a supervised subprocess — against a throttled
HTTP server on the loopback interface with real Range, ETag and If-Range semantics, the
dataset spec pointed at that server. These are tests of the transfer, cancellation and
resume mechanics on loopback; nothing here reaches a real scientific host.
"""
from __future__ import annotations

import dataclasses
import hashlib
import http.server
import json
import os
import threading
import time
from pathlib import Path

import pytest

from bioagent.acquisition.transfer import exclusive, read_sidecar
from bioagent.tcmdb import TCMDataHub
from bioagent.tcmdb import hub as hub_module
from bioagent.tcmdb.acquire import read_audit
from bioagent.tcmdb.datasets import dataset as real_dataset
from bioagent.tcmdb.spec import DatasetSpec, FileSpec

from tcmstudio.datasets import REDISTRIBUTION_UNKNOWN, licence_of, release_covered
from tcmstudio.kinds import KINDS, OUTPUT_TOKEN, StartContext

from test_server_support import running_server, test_kinds

NAME = "npass_fixture.tsv"


# ------------------------------------------------------------------- the routes

def test_every_route_needs_the_pairing_token(tmp_path):
    with running_server(tmp_path / "home") as (_, c):
        for method, path in (("GET", "/api/datasets"), ("GET", "/api/datasets/npass/manifest"),
                             ("GET", "/api/datasets/npass/readiness"),
                             ("POST", "/api/datasets/acquisitions"),
                             ("GET", "/api/datasets/acquisitions")):
            status, _, raw = c.raw(method, path, b"{}" if method == "POST" else None,
                                   token=False)
            assert status == 401 and json.loads(raw)["error"]["type"] == "token", path


def test_the_list_says_what_each_dataset_is_and_how_ready_it_is(tmp_path):
    with running_server(tmp_path / "home") as (_, c):
        status, doc = c.get("/api/datasets")
    assert status == 200 and doc["count"] == len(doc["datasets"]) >= 70
    by = {d["key"]: d for d in doc["datasets"]}
    npass = by["npass"]
    assert npass["readiness"]["state"] == "supported" and npass["acquirable"] is True
    assert npass["redistribution"] == REDISTRIBUTION_UNKNOWN and npass["licence_class"] == "unknown"
    assert npass["hosts"] == ["bidd.group"] and npass["bytes_default"] > 100_000_000
    assert by["tcmsp_export"]["acquirable"] is False                # a manual import
    assert by["lotus"]["redistribution"] == "with attribution (CC BY 4.0)", \
        "a reviewed licence record for the same release says so"


def test_a_licence_record_speaks_only_for_the_release_it_names():
    lotus = "2026-04-13 frozen export (doi:10.5281/zenodo.19360665)"
    assert release_covered("2026-04-13", lotus)
    string = "12.5 (current); 12.0 (the bulk files bioagent fetches)"
    assert release_covered("12.0", string) and release_covered("12.5", string)
    for version, covered in (("12", string), ("2", "12.0"), ("2.0", "2.0.1"),
                             ("2026-04", lotus), ("", lotus), (None, lotus),
                             ("current", "current"), ("latest", "latest (2026)")):
        assert not release_covered(version, covered), (version, covered)
    spec = real_dataset("lotus")
    assert licence_of(spec)["redistribution"] == "with attribution (CC BY 4.0)"
    unversioned = licence_of(dataclasses.replace(spec, version=""), record=True)
    assert unversioned["redistribution"] == REDISTRIBUTION_UNKNOWN
    assert unversioned["licence_record"]["applies"] is False


def test_the_npass_manifest_lists_its_reviewed_files_and_says_redistribution_is_unknown(tmp_path):
    with running_server(tmp_path / "home") as (_, c):
        status, m = c.get("/api/datasets/npass/manifest")
        assert c.get("/api/datasets/nope/manifest")[0] == 404
    assert status == 200 and m["dataset"] == "npass" and m["version"] == "2.0"
    assert len(m["files"]) == 6
    assert all(f["reviewed_checksum"].startswith("sha256:") and f["expected_bytes"]
               and f["host"] == "bidd.group" and f["local"] == {"present": False}
               for f in m["files"])
    assert m["redistribution"] == REDISTRIBUTION_UNKNOWN and "licence_record" not in m
    assert m["store"] == {"built": False} and m["audit"] == []
    assert m["size_gate_bytes"] == 512 * 1024 * 1024


def test_readiness_on_an_empty_home_names_the_acquisition(tmp_path):
    with running_server(tmp_path / "home") as (srv, c):
        status, r = c.get("/api/datasets/npass/readiness")
        assert status == 200
        assert r["state"] == "supported"
        assert r["next_step"] == "job.tcmdb.fetch {dataset: npass}, then job.tcmdb.build"
        assert r["acquire"] == {"method": "POST", "path": "/api/datasets/acquisitions",
                                "body": {"dataset": "npass"}}
        assert r["requires"] == [] and r["local"]["built"] is False
        srv.state.update({"network": {"enabled": False}})
        r = c.get("/api/datasets/npass/readiness")[1]
        assert [q["need"] for q in r["requires"]] == ["network"]
        assert r["requires"][0]["names"] == ["bidd.group"]
        assert c.get("/api/datasets/nope/readiness")[0] == 404


def test_an_acquisition_names_a_registered_key_and_nothing_else(tmp_path):
    with running_server(tmp_path / "home") as (_, c):
        for body, words in (({"dataset": "no_such_db"}, "no registered dataset"),
                            ({"dataset": "https://bidd.group/NPASS/x.txt"}, "never a URL"),
                            ({"dataset": "npass", "url": "https://example.org/x"}, "unknown field"),
                            ({"dataset": "npass", "confirm": True}, "size-gate"),
                            ({"dataset": "tcmsp_export"}, "cannot be downloaded"),
                            ({"dataset": "npass", "include_optional": "yes"}, "true or false"),
                            ({}, "dataset")):
            status, doc = c.post("/api/datasets/acquisitions", body)
            assert status == 400 and doc["error"]["type"] == "bad_arguments", body
            assert words in doc["error"]["message"], (body, doc)
        assert c.get("/api/jobs")[1]["jobs"] == [], "nothing was queued"


def test_with_the_network_off_an_acquisition_is_refused(tmp_path):
    with running_server(tmp_path / "home", network=False) as (_, c):
        status, doc = c.post("/api/datasets/acquisitions", {"dataset": "npass"})
    assert status == 422 and doc["error"]["type"] == "network_off"


def test_a_job_that_is_not_an_acquisition_is_not_found_there(tmp_path):
    with running_server(tmp_path / "home", kinds={**KINDS, **test_kinds()}) as (_, c):
        job = c.post("/api/jobs", {"kind": "test.sleep", "params": {"seconds": 0, "lines": 1}})[1]
        c.wait_job(job["id"])
        status, doc = c.get(f"/api/datasets/acquisitions/{job['id']}")
        assert status == 404 and doc["error"]["type"] == "not_found"
        assert c.raw("DELETE", f"/api/datasets/acquisitions/{job['id']}")[0] == 404
        assert c.get("/api/datasets/acquisitions/j_0000000000000000")[0] == 404
        assert c.get("/api/datasets/acquisitions")[1] == {"acquisitions": []}


# ------------------------------------------------------- progress, from the disk

def test_fetch_progress_is_the_share_of_bytes_on_disk(tmp_path):
    kind = KINDS["tcmdb.fetch"]
    root = tmp_path / "tcmdb"
    spec = real_dataset("npass")
    raw = TCMDataHub(root).raw_dir("npass")
    raw.mkdir(parents=True)
    first, second = spec.files[0], spec.files[1]
    with open(raw / f"{first.name}.part", "wb") as fh:       # sparse: the size is what counts
        fh.truncate(first.expected_bytes // 2)
    with open(raw / second.name, "wb") as fh:
        fh.truncate(second.expected_bytes)
    total = sum(f.expected_bytes for f in spec.files)
    p = kind.progress(tmp_path / "job", {"root": str(root), "dataset": "npass",
                                         "include_optional": False})
    done = first.expected_bytes // 2 + second.expected_bytes
    assert p["bytes"] == {"done": done, "total": total}
    assert p["fraction"] == round(done / total, 3) and p["files"] == {"done": 1, "total": 6}
    assert p["verified"] == {"checksum": 0, "size": 0, "none": 1}, "kept with no record"
    assert p["stage"] == "transferring"
    with open(raw / f"{first.name}.part", "r+b") as fh:
        fh.truncate(first.expected_bytes)
    assert kind.progress(tmp_path / "job", {"root": str(root), "dataset": "npass"})["stage"] \
        == "verifying"


def test_a_fetch_whose_file_is_not_the_reviewed_one_fails_its_check(tmp_path):
    kind = KINDS["tcmdb.fetch"]
    root = tmp_path / "tcmdb"
    spec = real_dataset("npass")
    raw = TCMDataHub(root).raw_dir("npass")
    raw.mkdir(parents=True)
    records = {}
    for f in spec.files:
        (raw / f.name).write_bytes(b"x")
        records[f.name] = {"url": f.url, "bytes": 1, "checksum": f.checksum, "verified": True}
    first = spec.files[0].name
    records[first]["checksum"] = "sha256:" + "0" * 64                # another file's digest
    (raw / ".downloads.json").write_text(json.dumps(records))
    prepared = {"root": str(root), "dataset": "npass", "include_optional": False}
    problems = kind.verify(prepared, {"BIOAGENT_TCMDB": str(root)})
    assert problems == [f"not the reviewed file (its recorded checksum differs from the "
                        f"reviewed one): {first}"]
    records[first]["checksum"] = spec.files[0].checksum
    (raw / ".downloads.json").write_text(json.dumps(records))
    assert kind.verify(prepared, {"BIOAGENT_TCMDB": str(root)}) == []


def test_the_fetch_command_writes_its_report_and_offers_the_snapshot_raw_as_a_mirror(tmp_path):
    from tcmstudio.kinds import PrepareContext
    data_env = {"BIOAGENT_DATA_LAKE": str(tmp_path / "lake"),
                "BIOAGENT_TCMDB": str(tmp_path / "lake" / "tcmdb")}
    ctx = PrepareContext(job_id="j1", job_dir=tmp_path / "job", project_id=None, paths={},
                         data_env=data_env, find=lambda v: None, settings={})
    prepared = KINDS["tcmdb.fetch"].prepare({"dataset": "npass"}, ctx)
    assert prepared["mirrors"] == [str(tmp_path / "lake" / "raw")]
    start = StartContext(job_id="j1", job_dir=tmp_path / "job", project_id=None, paths={},
                         data_env=data_env, settings={}, device="cpu", threads=1)
    argv = KINDS["tcmdb.fetch"].command(prepared, start)
    assert f"--report={OUTPUT_TOKEN}/acquisition.json" in argv
    assert f"--mirror={tmp_path / 'lake' / 'raw'}" in argv
    snap = KINDS["research.snapshot"].prepare({}, ctx)
    assert snap["mirrors"] == [str(tmp_path / "lake" / "tcmdb" / "raw" / k)
                               for k in ("npass", "cmaup", "lotus")]
    assert f"--mirror={tmp_path / 'lake' / 'tcmdb' / 'raw' / 'npass'}" in \
        KINDS["research.snapshot"].command(snap, start)


# ---------------------------------------- acquisitions, cancelled and resumed

class _Slow(http.server.BaseHTTPRequestHandler):
    """Range, ETag and If-Range as RFC 9110 has them, 64 KiB every 5 ms. A whole-file answer
    stops after ``hold_after`` bytes until ``release`` is set, so a test can cancel the
    transfer at a known point however fast or slow the machine is."""

    body = b""
    etag = '"r1"'
    seen: list[dict] = []
    hold_after = 3 << 19                                         # 1.5 MiB
    release = threading.Event()

    def log_message(self, *args):
        pass

    def _answer(self, send: bool) -> None:
        self.seen.append({"method": self.command, "range": self.headers.get("Range"),
                          "if_range": self.headers.get("If-Range")})
        data, start = self.body, 0
        rng, validator = self.headers.get("Range"), self.headers.get("If-Range")
        if rng and (validator is None or validator == self.etag):
            start = int(rng[6:].split("-", 1)[0])
        chunk = data[start:]
        self.send_response(206 if start else 200)
        if start:
            self.send_header("Content-Range", f"bytes {start}-{len(data) - 1}/{len(data)}")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("ETag", self.etag)
        self.send_header("Content-Length", str(len(chunk)))
        self.end_headers()
        if not send:
            return
        try:
            for i in range(0, len(chunk), 1 << 16):
                if not start and i == self.hold_after:
                    self.wfile.flush()
                    self.release.wait(30)
                self.wfile.write(chunk[i:i + (1 << 16)])
                time.sleep(0.005)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_HEAD(self):
        self._answer(False)

    def do_GET(self):
        self._answer(True)


_FETCH = r"""
import json, sys
from bioagent.tcmdb import hub as hub_module
from bioagent.tcmdb.spec import DatasetSpec, FileSpec
doc = json.load(open(sys.argv[1], encoding="utf-8"))
spec = DatasetSpec("npass", "NPASS (loopback fixture)", (5,), "https://example.org",
                   "not stated", tuple(FileSpec(**f) for f in doc["files"]), version="2.0")
hub_module.dataset = lambda key: spec
from bioagent.cli import main
sys.exit(main(sys.argv[2:]))
"""


@pytest.fixture
def slow():
    _Slow.seen, _Slow.release = [], threading.Event()
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Slow)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    _Slow.release.set()
    srv.shutdown()
    srv.server_close()


def _fixture(tmp_path, monkeypatch, url: str, checksum: str | None):
    """The npass dataset pointed at the loopback server: in the runner (its progress, views
    and checks) and in the job's subprocess (the fetch itself)."""
    files = [{"url": f"{url}/{NAME}", "name": NAME, "table": "general", "fmt": "raw",
              "checksum": checksum}]
    spec_file = tmp_path / "spec.json"
    spec_file.write_text(json.dumps({"files": files}))
    spec = DatasetSpec("npass", "NPASS (loopback fixture)", (5,), "https://example.org",
                       "not stated", tuple(FileSpec(**f) for f in files), version="2.0")
    real = hub_module.dataset
    monkeypatch.setattr(hub_module, "dataset", lambda key: spec if key == "npass" else real(key))

    def command(r, ctx):
        argv = [ctx.python, "-I", "-c", _FETCH, str(spec_file), "tcmdb", f"--root={r['root']}",
                "fetch", r["dataset"], f"--report={OUTPUT_TOKEN}/acquisition.json"]
        return argv
    return {**KINDS, "tcmdb.fetch": dataclasses.replace(KINDS["tcmdb.fetch"], command=command)}


def _wait(predicate, timeout=60.0, what="the condition"):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError(f"timed out waiting for {what}")


def _cancel_midway(c, raw: Path, size: int) -> tuple[dict, int]:
    status, headers, data = c.raw("POST", "/api/datasets/acquisitions",
                                  json.dumps({"dataset": "npass", "submission_id": "acq-1"}).encode(),
                                  {"Content-Type": "application/json"})
    doc = json.loads(data)
    assert status == 201, doc
    job_id = doc["acquisition"]["id"]
    assert headers["location"] == f"/api/datasets/acquisitions/{job_id}"
    assert doc["acquisition"]["job"]["kind"] == "tcmdb.fetch"
    assert doc["plan"]["files"][0]["file"] == NAME and doc["plan"]["hosts"] == ["127.0.0.1"]
    again = c.post("/api/datasets/acquisitions", {"dataset": "npass", "submission_id": "acq-1"})
    assert again[0] == 200 and again[1]["acquisition"]["id"] == job_id, "the same submission"
    part = raw / f"{NAME}.part"
    seen = _wait(lambda: (lambda v: v if v["state"] == "running" and v.get("files")
                          and v["files"][0]["state"] == "transferring"
                          and (v["job"].get("progress") or {}).get("bytes", {}).get("done")
                          else None)(c.get(f"/api/datasets/acquisitions/{job_id}")[1]),
                 what="a transferring file in the acquisition's view")
    assert 0 < seen["job"]["progress"]["bytes"]["done"] < size
    assert seen["job"]["progress"]["files"] == {"done": 0, "total": 1}
    _wait(lambda: part.exists() and part.stat().st_size >= 1 << 20, what="1 MiB on disk")
    status, view = c.json("DELETE", f"/api/datasets/acquisitions/{job_id}")
    assert status == 200 and view["state"] == "cancelled", view
    _Slow.release.set()               # the next block arrives; the transfer stops after it
    # the transfer stops between two blocks and lets go of the file's lock
    lock = raw / ".locks" / f"{NAME}.lock"

    def released():
        with exclusive(lock, wait=False) as held:
            return held
    _wait(released, what="the cancelled transfer to stop")
    have = part.stat().st_size
    assert 0 < have < size, "the partial file is kept"
    assert read_sidecar(raw / f"{NAME}.part.json")["etag"] == '"r1"'
    assert c.json("DELETE", f"/api/datasets/acquisitions/{job_id}")[0] == 409
    return view, have


def test_cancel_then_resume_through_the_runner(tmp_path, monkeypatch, slow):
    body = os.urandom(6 << 20)
    checksum = "sha256:" + hashlib.sha256(body).hexdigest()
    _Slow.body, _Slow.etag = body, '"r1"'
    kinds = _fixture(tmp_path, monkeypatch, slow, checksum)
    with running_server(tmp_path / "home", kinds=kinds) as (srv, c):
        hub = TCMDataHub(srv.data_env["BIOAGENT_TCMDB"])
        raw = hub.raw_dir("npass")
        _, have = _cancel_midway(c, raw, len(body))
        _wait(lambda: any(e["status"] == "SKIPPED" for e in read_audit(hub.root, "npass")),
              what="the cancelled attempt in the audit")
        [cancelled] = read_audit(hub.root, "npass")
        assert "cancelled" in cancelled["reason"]
        _Slow.seen = []
        status, doc = c.post("/api/datasets/acquisitions", {"dataset": "npass"})
        assert status == 201
        job = c.wait_job(doc["acquisition"]["id"])
        assert job["state"] == "succeeded", job["outcome"]
        gets = [s for s in _Slow.seen if s["method"] == "GET"]
        assert gets == [{"method": "GET", "range": f"bytes={have}-", "if_range": '"r1"'}]
        assert (raw / NAME).read_bytes() == body
        last = read_audit(hub.root, "npass")[-1]
        assert last["status"] == "PASS" and last["resumed"] is True
        assert last["resumed_from"] == have and last["checksum"] == checksum
        view = c.get(f"/api/datasets/acquisitions/{job['id']}")[1]
        assert view["files"][0]["state"] == "kept" and view["files"][0]["verification"] == "checksum"
        assert view["summary"]["verified"] == {"checksum": 1, "size": 0, "none": 0}
        assert job["result"]["acquisition"]["files"][0]["resumed_from"] == have
        listed = c.get("/api/datasets/acquisitions")[1]["acquisitions"]
        assert {a["id"] for a in listed} >= {job["id"]} and all("files" not in a for a in listed)


def test_resume_after_upstream_change_restarts(tmp_path, monkeypatch, slow):
    v1, v2 = os.urandom(6 << 20), os.urandom(6 << 20)
    _Slow.body, _Slow.etag = v1, '"r1"'
    kinds = _fixture(tmp_path, monkeypatch, slow, None)              # no reviewed checksum
    with running_server(tmp_path / "home", kinds=kinds) as (srv, c):
        hub = TCMDataHub(srv.data_env["BIOAGENT_TCMDB"])
        raw = hub.raw_dir("npass")
        _cancel_midway(c, raw, len(v1))
        _Slow.body, _Slow.etag, _Slow.seen = v2, '"r2"', []          # a new upstream release
        status, doc = c.post("/api/datasets/acquisitions", {"dataset": "npass"})
        assert status == 201
        job = c.wait_job(doc["acquisition"]["id"])
        assert job["state"] == "succeeded", job["outcome"]
        assert [s["range"] for s in _Slow.seen if s["method"] == "GET"] == [None]
        assert (raw / NAME).read_bytes() == v2, "the new file, whole; no splice was kept"
        last = read_audit(hub.root, "npass")[-1]
        assert last["resumed"] is False and last["etag"] == '"r2"'
        assert any("ETag changed" in why for why in last["restarts"])


def test_a_dataset_already_being_acquired_or_a_reused_submission_id_is_a_conflict(
        tmp_path, monkeypatch, slow):
    _Slow.body, _Slow.etag = os.urandom(4 << 20), '"r1"'
    kinds = {**_fixture(tmp_path, monkeypatch, slow, None), **test_kinds()}
    with running_server(tmp_path / "home", kinds=kinds) as (srv, c):
        status, doc = c.post("/api/datasets/acquisitions",
                             {"dataset": "npass", "submission_id": "acq-a"})
        assert status == 201
        job_id = doc["acquisition"]["id"]
        try:
            # a second acquisition of the same dataset would find its files locked
            status, doc = c.post("/api/datasets/acquisitions", {"dataset": "npass"})
            assert status == 409 and doc["error"]["type"] == "conflict", doc
            assert doc["error"]["job"] == job_id and "already being acquired" \
                in doc["error"]["message"]
            # the same submission_id is the same request: the running acquisition
            again = c.post("/api/datasets/acquisitions",
                           {"dataset": "npass", "submission_id": "acq-a"})
            assert again[0] == 200 and again[1]["acquisition"]["id"] == job_id
            # ... but not for another dataset, or another kind of job
            status, doc = c.post("/api/datasets/acquisitions",
                                 {"dataset": "cmaup", "submission_id": "acq-a"})
            assert status == 409 and doc["error"]["job"] == job_id, doc
            assert "already names another job" in doc["error"]["message"]
            sleep = c.post("/api/jobs", {"kind": "test.sleep", "submission_id": "s-1",
                                         "params": {"seconds": 0, "lines": 1}})[1]
            status, doc = c.post("/api/datasets/acquisitions",
                                 {"dataset": "cmaup", "submission_id": "s-1"})
            assert status == 409 and doc["error"]["job"] == sleep["id"], doc
            assert "test.sleep" in doc["error"]["message"]
            assert {j["kind"] for j in c.get("/api/jobs")[1]["jobs"]} == {"tcmdb.fetch",
                                                                         "test.sleep"}
            assert len([j for j in c.get("/api/jobs")[1]["jobs"]
                        if j["kind"] == "tcmdb.fetch"]) == 1, "nothing else was queued"
        finally:
            c.json("DELETE", f"/api/datasets/acquisitions/{job_id}")
            _Slow.release.set()
