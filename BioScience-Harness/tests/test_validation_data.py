"""bioagent.validation.data and the ``bioagent validate`` reports, on files served for real.

The raw files are the parser tests' (real headers, the marker compounds of 葛根芩连汤, a
measured activity of berberine), served over HTTP from the loopback interface with byte
ranges, so a resumed download is a real one. The specs point at that server and carry the
fixtures' own checksums in place of the reviewed ones.
"""
from __future__ import annotations

import hashlib
import http.server
import json
import threading
from dataclasses import replace

import pytest

from bioagent.acquisition.sources import ACQUIRABLE
from bioagent.sources import pipeline
from bioagent.tcmdb import TCMDataHub
from bioagent.tcmdb import datasets as datasets_module
from bioagent.tcmdb import hub as hub_module
from bioagent.tcmdb.extra import EXTRA_CHECKS
from bioagent.validation import (BLOCKED, FAIL, NOT_APPLICABLE, PASS, SKIPPED, UNVERIFIED,
                                 load_report)
from bioagent.validation import data, databases
from bioagent.validation.cli import main as validate
from test_snapshot_pipeline import _raw


def _sha(path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


class _Ranges(http.server.BaseHTTPRequestHandler):
    root = None
    policy: set[str] = set()
    seen: list[tuple[str, str, str | None]] = []

    def log_message(self, *args):
        pass

    def _answer(self, body: bool):
        name = self.path.lstrip("/")
        self.seen.append((self.command, name, self.headers.get("Range")))
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
        data_ = path.read_bytes()
        rng = self.headers.get("Range")
        if rng:
            start, _, end = rng[6:].partition("-")
            start, end = int(start), int(end) if end else len(data_) - 1
            chunk = data_[start:end + 1]
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{len(data_)}")
        else:
            chunk = data_
            self.send_response(200)
            self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(len(chunk)))
        self.end_headers()
        if body:
            self.wfile.write(chunk)

    def do_HEAD(self):
        self._answer(False)

    def do_GET(self):
        self._answer(True)


@pytest.fixture
def upstream(tmp_path, monkeypatch):
    up = _raw(tmp_path / "upstream")
    acts = up / "NPASSv2.0_download_naturalProducts_activities.txt"   # berberine on COX-2
    acts.write_text(acts.read_text() + "\t".join(
        ["NPC2", "NPT1", "IC50", "=", "IC50", "5000", "nM", "Homo sapiens", "9606", "n.a.",
         "n.a.", "n.a.", "777777", "PMID"]) + "\n")
    # CMAUP's berberine: in Coptis chinensis, measured on COX-2 as NPASS has it too
    def add(name, line):
        path = up / name
        path.write_text(path.read_text() + line + "\n")
    add("CMAUPv2.0_download_Plants.txt", "\t".join(
        ["NPO5649", "Coptis Chinensis", "261450", "Coptis chinensis", "n.a.", "Coptis", "n.a.",
         "Ranunculaceae"]))
    add("CMAUPv2.0_download_Plant_Ingredient_Associations_allIngredients.txt", "NPO5649\tNPC12")
    add("CMAUPv2.0_download_Ingredients_All.txt", "\t".join(
        ["NPC12", "Berberine", "n.a.", "n.a.", "2353"] + ["0"] * 9
        + ["InChI=1S/d", "YBHILYKTIRIUTE-UHFFFAOYSA-N", "C"]))
    add("CMAUPv2.0_download_Targets.txt", "\t".join(
        ["NPT600", "PTGS2", "Cyclooxygenase-2", "P35354", "CHEMBL230", "NA", "0", "0", "1", "a",
         "b", "c", "c", "Therapeutic Target"]))
    add("CMAUPv2.0_download_Ingredient_Target_Associations_ActivityValues_References.txt",
        "\t".join(["NPC12", "NPT600", "IC50", "=", "3000", "nM", "888888", "PMID"]))
    _Ranges.root, _Ranges.policy, _Ranges.seen = up, set(), []
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Ranges)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    specs = {}
    for name, a in pipeline._SPECS.items():
        if (up / name).is_file():
            reviewed = a in ACQUIRABLE
            specs[name] = replace(a, url=f"{base}/{name}", host="127.0.0.1",
                                  checksum=_sha(up / name) if reviewed else None,
                                  expected_bytes=(up / name).stat().st_size if reviewed else None)
    monkeypatch.setattr(pipeline, "_SPECS", specs)
    real = datasets_module.dataset

    def fixture_dataset(key):
        spec = real(key)
        if key not in data.HUB_DATASETS:
            return spec
        return replace(spec, files=tuple(
            replace(f, checksum=_sha(up / f.name) if (up / f.name).is_file() else None,
                    expected_bytes=None) for f in spec.files))
    monkeypatch.setattr(datasets_module, "dataset", fixture_dataset)
    monkeypatch.setattr(hub_module, "dataset", fixture_dataset)
    for key in data.HUB_DATASETS:                  # the reviewed-checksum check: the real files'
        monkeypatch.setitem(EXTRA_CHECKS, key, lambda raw, conn: ([], []))
    yield up
    srv.shutdown()


def _specs():
    return pipeline.required_files()


def test_every_file_is_requested_resumed_verified_and_read_through(upstream, tmp_path):
    records = data.validate_files(_specs(), tmp_path / "raw", live=True, log=lambda m: None)
    by = {r.component: r for r in records}
    assert all(r.check("connectivity").status == PASS for r in records)
    # one resume per host: here every file is on one host, so the smallest one
    resumed = [r for r in records if r.check("resume").status == PASS]
    assert len(resumed) == 1
    evidence = resumed[0].check("resume").evidence
    assert evidence["range_requests"] and evidence["resumed_at"] > 0
    assert sum(1 for _, _, rng in _Ranges.seen if rng and rng != "bytes=0-0") >= 2
    gz = by["9606.protein.links.v12.0.txt.gz"]
    assert gz.check("decompression").status == PASS
    assert gz.check("decompression").evidence["uncompressed_bytes"] > 0
    assert by["NPASSv2.0_download_naturalProducts_activities.txt"].check(
        "version").status == PASS                                        # v2.0 in the name
    assert by["260413_frozen.csv.gz"].check("version").detail.endswith("2026-04-13 freeze")
    assert by["UniProt2Reactome.txt"].check("version").status == NOT_APPLICABLE
    unreviewed = by["9606.protein.aliases.v12.0.txt.gz"]
    assert unreviewed.check("integrity").status == UNVERIFIED and unreviewed.status == UNVERIFIED
    reviewed = by["NPASSv2.0_download_naturalProducts_targetInfo.txt"]
    assert reviewed.status == PASS and reviewed.provenance["sha256"].startswith("sha256:")


def test_a_refused_host_is_blocked_and_learnt_once(upstream, tmp_path):
    _Ranges.policy = set(pipeline._SPECS)              # one host here, refusing everything
    records = data.validate_files(_specs(), tmp_path / "raw", live=True, resume_check=False,
                                  log=lambda m: None)
    first = records[0]
    assert first.check("connectivity").status == BLOCKED and first.status == BLOCKED
    assert "policy" in first.check("connectivity").detail
    assert first.check("integrity").status == SKIPPED
    assert all("learnt from an earlier file" in r.check("connectivity").detail
               for r in records[1:])
    assert {path for _, path, _ in _Ranges.seen} == {first.component}


def test_a_file_that_changed_upstream_fails_on_its_size_and_its_download(upstream, tmp_path):
    tampered = upstream / "CMAUPv2.0_download_Targets.txt"
    tampered.write_text(tampered.read_text() + "NPT999\tX\tX\tP00000\n")
    records = {r.component: r for r in data.validate_files(
        _specs(), tmp_path / "raw", live=True, resume_check=False, log=lambda m: None)}
    cmaup = records["CMAUPv2.0_download_Targets.txt"]
    assert cmaup.check("size").status == FAIL and "upstream file changed" in cmaup.check("size").detail
    assert cmaup.check("download").status == FAIL and cmaup.status == FAIL
    assert not (tmp_path / "raw" / "CMAUPv2.0_download_Targets.txt").exists()
    assert records["CMAUPv2.0_download_Plants.txt"].status == PASS


def test_offline_nothing_is_requested_and_nothing_counts_as_passed(upstream, tmp_path):
    records = data.validate_files(_specs(), tmp_path / "raw", live=False, log=lambda m: None)
    assert not _Ranges.seen
    assert all(r.check("connectivity").status == SKIPPED for r in records)
    assert all(r.status in (SKIPPED, UNVERIFIED, NOT_APPLICABLE) for r in records)


def test_the_hub_datasets_build_from_the_files_with_no_record_lost(upstream, tmp_path):
    raw = tmp_path / "raw"
    data.validate_files(_specs(), raw, live=True, resume_check=False, log=lambda m: None)
    hub = TCMDataHub(tmp_path / "hub")
    recs = {k: data.validate_hub_dataset(hub, k, raw, log=lambda m: None)
            for k in data.HUB_DATASETS}
    npass = recs["npass"]
    assert npass.check("acquisition").status == PASS
    assert npass.check("build").status == PASS and npass.check("record_loss").status == PASS
    assert npass.check("check").status == PASS and npass.check("check").evidence["rebuilt"]
    queries = npass.check("queries")
    assert queries.status == PASS and queries.evidence["berberine_targets"] == 1
    assert queries.evidence["gold"]["berberine"]["found_in"] == ["ncbitaxon:261450"]
    assert recs["lotus"].check("queries").status == PASS     # glycyrrhizin in G. uralensis
    integration = data.integration_check(hub, data.HUB_DATASETS)
    check = integration.check("integration")
    assert check.status == PASS, check.detail
    assert check.evidence["species"]["berberine"], "NPASS and CMAUP place it in Coptis chinensis"
    assert check.evidence["berberine_common_targets"] == ["uniprot:P35354"]


def test_a_dataset_whose_files_are_not_all_here_is_blocked(upstream, tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    rec = data.validate_hub_dataset(TCMDataHub(tmp_path / "hub"), "cmaup", raw, log=lambda m: None)
    assert rec.check("acquisition").status == BLOCKED and rec.status == BLOCKED
    assert rec.check("build").status == SKIPPED


def test_the_reports_are_written_and_a_live_run_folds_into_the_record(upstream, tmp_path):
    out, work = tmp_path / "reports", tmp_path / "work"
    code = validate(["data", "--out", str(out), "--work", str(work), "--live"])
    assert code == 0
    files = load_report(out / "live_download_validation.json")
    assert files["tier"] == "live" and files["summary"]["records"] == len(_specs())
    research = load_report(out / "research_pipeline_report.json")
    loop = next(r for r in research["records"] if r["kind"] == "research_run")
    assert loop["status"] == PASS, loop
    repro = load_report(out / "scientific_reproducibility_report.json")
    assert {r["component"]: r["status"] for r in repro["records"]}["research.snapshots"] == PASS
    dbs = load_report(out / "database_readiness_report.json")
    assert {r["kind"] for r in dbs["records"]} >= {"database", "hub_dataset", "integration"}
    record = tmp_path / "record.json"
    assert validate(["record", "--report", str(out / "live_download_validation.json"),
                     "--record", str(record)]) == 0
    folded = json.loads(record.read_text())["files"]
    assert folded["NPASSv2.0_download_naturalProducts_targetInfo.txt"]["status"] == PASS
    # an offline report verifies nothing about the servers, and is not folded in
    assert validate(["data", "--out", str(tmp_path / "off"), "--work", str(tmp_path / "w2"),
                     "--no-research"]) == 0
    assert validate(["record", "--report", str(tmp_path / "off" / "live_download_validation.json"),
                     "--record", str(record)]) == 2


def test_the_database_report_reads_the_record_and_never_calls_unknown_ready(tmp_path):
    hub = TCMDataHub(tmp_path / "hub")
    record = {"files": {f.name: {"status": PASS, "verified_at": "2026-10-11T00:00:00Z",
                                 "run": "https://ci/run/1"}
                        for f in datasets_module.dataset("npass").files}}
    recs = {r.component: r for r in databases.readiness(hub, verification=record,
                                                        connectors={})}
    npass = recs["source.5"]
    assert npass.check("real_download").status == PASS
    assert npass.check("integrity").status == PASS, "every default file has a reviewed checksum"
    assert npass.check("local_build").status == SKIPPED and npass.status != PASS
    cmaup = recs["source.6"]
    assert cmaup.check("real_download").status == UNVERIFIED
    assert all(r.check("catalogue").status == PASS for r in recs.values())
    connector = next(r for r in recs.values() if r.check("live_api"))
    assert connector.check("live_api").status == UNVERIFIED, "no recorded live call given"
