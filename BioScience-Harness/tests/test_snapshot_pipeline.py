"""bioagent.sources.pipeline: the research snapshots built in one recorded run.

The raw files are the parser tests' (the real headers, the marker compounds of 葛根芩连汤),
served over real HTTP from the loopback interface; the specs point at that server, with the
fixtures' own checksums in place of the reviewed ones.
"""
from __future__ import annotations

import hashlib
import http.server
import threading
from dataclasses import replace
from pathlib import Path

import pytest

from bioagent.acquisition.sources import ACQUIRABLE
from bioagent.sources import pipeline
from bioagent.sources.herbs import KEY as HERB_KEY
from bioagent.sources.ledger import SnapshotLedger
from bioagent.validation import BLOCKED, FAIL, PASS, SKIPPED, UNVERIFIED
from test_source_parsers import cmaup_files, lotus_file, npass_files, string_files, tsv


def _raw(d: Path) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    npass_files(d)
    cmaup_files(d)
    lotus_file(d)
    string_files(d)
    tsv(d / "UniProt2Reactome.txt", None, [
        ["P35354", "R-HSA-2162123", "u", "Synthesis of Prostaglandins", "TAS", "Homo sapiens"],
        ["Q92753", "R-HSA-383280", "u", "Nuclear Receptor transcription", "TAS",
         "Homo sapiens"]])
    (d / "uniprot_human_reviewed_accessions.txt").write_text("P35354\nQ92753\nP23219\n")
    return d


class _Serve(http.server.BaseHTTPRequestHandler):
    root: Path = Path(".")
    policy: set[str] = set()
    seen: list[str] = []

    def log_message(self, *args):
        pass

    def _answer(self, body: bool):
        name = self.path.lstrip("/")
        self.seen.append(f"{self.command} {name}")
        if name in self.policy:
            self.send_response(403)
            self.end_headers()
            if body:
                self.wfile.write(b"Forbidden by the sandbox egress policy")
            return
        path = self.root / name
        if not path.is_file():
            self.send_response(404)
            self.end_headers()
            return
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if body:
            self.wfile.write(data)

    def do_HEAD(self):
        self._answer(False)

    def do_GET(self):
        self._answer(True)


@pytest.fixture
def served(tmp_path, monkeypatch):
    """Fixture raw files on an HTTP server, and specs pointing at them: the first file of
    each project with its checksum, the rest by size, the unreviewed two with neither."""
    upstream = _raw(tmp_path / "upstream")
    _Serve.root, _Serve.policy, _Serve.seen = upstream, set(), []
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Serve)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    specs, first = {}, set()
    for name, a in pipeline._SPECS.items():
        path = upstream / name
        if not path.is_file():
            continue
        data = path.read_bytes()
        reviewed = a in ACQUIRABLE
        checksum = ("sha256:" + hashlib.sha256(data).hexdigest()
                    if reviewed and a.source_project not in first else None)
        first.add(a.source_project)
        specs[name] = replace(a, url=f"{base}/{name}", host="127.0.0.1", checksum=checksum,
                              expected_bytes=len(data) if reviewed else None)
    monkeypatch.setattr(pipeline, "_SPECS", specs)
    yield upstream
    srv.shutdown()


def test_the_files_needed_are_the_ones_the_parsers_read():
    from bioagent.sources.parsers import cmaup, npass, reactome, string_db

    names = {a.filename for a in pipeline.required_files()}
    assert set(npass.FILES.values()) | set(cmaup.FILES.values()) <= names
    assert {reactome.FILE, string_db.FILES["aliases"], "260413_frozen.csv.gz"} <= names
    assert {a.filename for a in pipeline.required_files(["lotus"], network=False)} == {
        "260413_frozen.csv.gz"}
    unreviewed = {a.filename for a in pipeline.UNREVIEWED}
    assert unreviewed == {"9606.protein.aliases.v12.0.txt.gz", "UniProt2Reactome.txt"}
    assert all(a.checksum is None and a.expected_bytes is None for a in pipeline.UNREVIEWED)
    with pytest.raises(ValueError, match="no research source"):
        pipeline.required_files(["chembl"])


def test_a_full_run_downloads_verifies_builds_and_records_every_snapshot(served, tmp_path):
    raw, out = tmp_path / "raw", tmp_path / "snap"
    doc = pipeline.build_research_snapshots(raw, out, out / "ledger.jsonl",
                                            report_path=tmp_path / "report.json",
                                            log=lambda m: None)
    stages = {s["stage"]: s for s in doc["stages"]}
    assert [s["stage"] for s in doc["stages"]] == [
        "discovery", "acquisition", "integrity", "build", "ledger"]
    assert stages["acquisition"]["status"] == PASS
    assert {f["verified"] for f in stages["acquisition"]["files"]} == {"checksum", "size"}
    # the two files without a reviewed checksum or size are the honest exception
    assert stages["integrity"]["status"] == UNVERIFIED
    unverified = {f["file"] for f in stages["integrity"]["files"] if f["status"] == UNVERIFIED}
    assert unverified == {"9606.protein.aliases.v12.0.txt.gz", "UniProt2Reactome.txt"}
    assert all(f["sha256"].startswith("sha256:") for f in stages["integrity"]["files"])
    assert stages["build"]["status"] == PASS and stages["build"]["gold"]["passed"]
    assert set(doc["snapshots"]) == {HERB_KEY, "npass", "cmaup", "lotus", "string", "reactome"}
    assert stages["ledger"]["status"] == PASS
    ledger = SnapshotLedger(out / "ledger.jsonl")
    assert ledger.expected("npass", doc["snapshots"]["npass"]["version"]) == \
        doc["snapshots"]["npass"]["snapshot_id"]
    assert doc["status"] == UNVERIFIED, "PASS only once the two files are pinned too"
    assert (tmp_path / "report.json").is_file()
    assert doc["reproduce"].startswith("bioagent snapshots build --raw ")
    # a rerun reuses every verified file and asks the server for nothing
    _Serve.seen.clear()
    again = pipeline.build_research_snapshots(raw, out, out / "ledger.jsonl", log=lambda m: None)
    assert {f["route"] for f in again["stages"][1]["files"]} == {"cache"}
    assert not any(s.startswith("GET") for s in _Serve.seen)
    assert again["snapshots"]["npass"]["snapshot_id"] == doc["snapshots"]["npass"]["snapshot_id"]


def test_a_host_the_policy_refuses_stops_before_the_build_and_names_the_import_route(
        served, tmp_path):
    _Serve.policy = set(pipeline._SPECS)          # every file is on the one (refusing) host
    doc = pipeline.build_research_snapshots(tmp_path / "raw", tmp_path / "snap",
                                            tmp_path / "snap" / "ledger.jsonl",
                                            log=lambda m: None)
    assert doc["status"] == BLOCKED
    assert [s["stage"] for s in doc["stages"]] == ["discovery", "acquisition"]
    files = doc["stages"][1]["files"]
    assert all(f["status"] == BLOCKED for f in files) and "policy" in files[0]["reason"]
    assert all("not asked again" in f["reason"] for f in files[1:])
    asked = {s.split(" ", 1)[1] for s in _Serve.seen}
    assert asked == {files[0]["file"]}, "the first file is refused, then the host is not asked again"
    assert "bioagent snapshots import" in doc["next_step"]


def test_a_file_that_differs_from_the_reviewed_one_stops_the_run(served, tmp_path):
    raw = _raw(tmp_path / "raw")                                # present before the run
    name = "NPASSv2.0_download_naturalProducts_speciesInfo.txt"
    if pipeline._SPECS[name].checksum is None:                  # the one held to a checksum
        name = next(n for n, a in pipeline._SPECS.items()
                    if a.source_project == "NPASS" and a.checksum)
    (raw / name).write_text((raw / name).read_text() + "tampered\n")
    doc = pipeline.build_research_snapshots(raw, tmp_path / "snap",
                                            tmp_path / "snap" / "ledger.jsonl", offline=True,
                                            log=lambda m: None)
    integrity = next(s for s in doc["stages"] if s["stage"] == "integrity")
    assert integrity["status"] == FAIL and doc["status"] == FAIL
    assert not any(s["stage"] == "build" for s in doc["stages"])
    assert name in doc["next_step"]


def test_an_offline_run_downloads_nothing_and_says_what_is_missing(served, tmp_path):
    doc = pipeline.build_research_snapshots(tmp_path / "raw", tmp_path / "snap",
                                            tmp_path / "snap" / "ledger.jsonl", offline=True,
                                            log=lambda m: None)
    assert doc["stages"][1]["status"] == SKIPPED and doc["status"] == SKIPPED
    assert not _Serve.seen


def test_an_import_is_held_to_the_reviewed_checksum(served, tmp_path):
    reviewed = next(n for n, a in pipeline._SPECS.items() if a.checksum)
    good = served / reviewed
    rows = pipeline.import_files(tmp_path / "raw", [good], log=lambda m: None)
    assert rows[0]["status"] == PASS and rows[0]["verified"] == "checksum"
    bad = tmp_path / "elsewhere" / reviewed
    bad.parent.mkdir()
    bad.write_bytes(good.read_bytes() + b"x")
    with pytest.raises(ValueError, match="not the reviewed"):
        pipeline.import_files(tmp_path / "raw", [bad], log=lambda m: None)
    with pytest.raises(ValueError, match="not a research raw file"):
        pipeline.import_files(tmp_path / "raw", [tmp_path / "raw" / ".downloads.json"])


def test_the_snapshots_it_builds_are_the_ones_the_research_loop_reads(served, tmp_path):
    from bioagent.research import default_protocol, parse_question, run_research

    raw, out = tmp_path / "raw", tmp_path / "snap"
    doc = pipeline.build_research_snapshots(raw, out, out / "ledger.jsonl", log=lambda m: None)
    assert doc["stages"][-1]["status"] == PASS
    question = parse_question("葛根芩连汤的实测靶点是否集中在某条通路？")
    run = run_research(question, protocol=default_protocol(question, activity=("npass",),
                                                           optional=("string",)),
                       snapshot_root=out, ledger_path=out / "ledger.jsonl",
                       state_dir=tmp_path / "state", output_dir=tmp_path / "run")
    assert run.snapshots["npass"] == doc["snapshots"]["npass"]["snapshot_id"]
    assert run.verdict is not None, "the loop ran to its verdict on these snapshots"


def test_nothing_is_downloaded_when_the_disk_cannot_hold_a_file(served, tmp_path, monkeypatch):
    """The disk-shortfall branch of the acquisition stage: with 10 MB free (less than the
    64 MB headroom alone), no file is fetched, each is BLOCKED with the reason, and the run
    stops before the build."""
    from bioagent.validation import environment

    monkeypatch.setattr(environment, "disk_free", lambda path: 10_000_000)
    raw = tmp_path / "raw"
    doc = pipeline.build_research_snapshots(raw, tmp_path / "snap",
                                            tmp_path / "snap" / "ledger.jsonl",
                                            log=lambda m: None)
    assert doc["status"] == BLOCKED
    assert [s["stage"] for s in doc["stages"]] == ["discovery", "acquisition"]
    files = doc["stages"][1]["files"]
    sized = {n for n, a in pipeline._SPECS.items() if a.expected_bytes}
    assert sized and {f["file"] for f in files if f["status"] == BLOCKED} >= sized
    for f in files:
        if f["file"] in sized:
            assert "not enough disk space" in f["reason"] and "nothing was started" in f["reason"]
    assert all(f["status"] != PASS for f in files)
    assert not any(s.startswith("GET") for s in _Serve.seen), "no body was requested"
    kept = [p.name for p in raw.rglob("*") if p.is_file() and not p.name.startswith(".")]
    assert not [n for n in kept if n in pipeline._SPECS], kept
