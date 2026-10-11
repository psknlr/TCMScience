"""bioagent.validation.hub_datasets: every hub dataset, fetched for real and followed through.

The NPASS files are the parser tests' (real headers), served over HTTP from the loopback
interface; the dataset's spec points at that server with the fixtures' own checksums in
place of the reviewed ones. ``TCMDataHub.fetch`` — the code Studio's ``tcmdb.fetch`` job
runs — downloads them, and the validator builds, checks and queries what it fetched.
"""
from __future__ import annotations

import http.server
import json
import threading
from dataclasses import replace

import pytest

from bioagent.tcmdb import TCMDataHub
from bioagent.tcmdb import datasets as datasets_module
from bioagent.tcmdb import hub as hub_module
from bioagent.tcmdb.extra import EXTRA_CHECKS
from bioagent.validation import BLOCKED, FAIL, PASS, SKIPPED, UNVERIFIED, load_report
from bioagent.validation import databases, hub_datasets
from bioagent.validation.cli import main as validate
from test_snapshot_pipeline import _raw
from test_validation_data import _Ranges, _sha


@pytest.fixture
def served(tmp_path, monkeypatch):
    """NPASS's files on a local server, its spec pointing there."""
    up = _raw(tmp_path / "upstream")
    _Ranges.root, _Ranges.policy, _Ranges.seen = up, set(), []
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Ranges)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    real = datasets_module.dataset

    def local(key):
        spec = real(key)
        if key != "npass":
            return spec
        return replace(spec, files=tuple(
            replace(f, url=f"{base}/{f.name}",
                    checksum=_sha(up / f.name) if (up / f.name).is_file() else None,
                    expected_bytes=None) for f in spec.files))
    monkeypatch.setattr(datasets_module, "dataset", local)
    monkeypatch.setattr(hub_module, "dataset", local)
    monkeypatch.setitem(EXTRA_CHECKS, "npass", lambda raw, conn: ([], []))  # the real files' sums
    yield up
    srv.shutdown()


def test_every_dataset_is_listed_once_by_how_it_is_obtained():
    keys = hub_datasets.dataset_keys()
    assert len(keys) == len(set(keys)) and {"npass", "cmaup", "lotus", "chebi"} <= set(keys)
    assert set(hub_datasets.dataset_keys(["manual"])) >= {"tcmsp_export", "drugbank_vocabulary"}
    assert set(hub_datasets.dataset_keys(["live"])) == set(hub_datasets.LIVE_PROBES)


def test_a_dataset_is_fetched_as_the_job_fetches_it_then_built_checked_and_queried(served, tmp_path):
    hub = TCMDataHub(tmp_path / "hub")
    rec = hub_datasets.validate_dataset(hub, "npass", live=True, log=lambda m: None)
    checks = {c.name: c for c in rec.checks}
    assert [c.name for c in rec.checks] == ["acquisition", "integrity", "build", "check", "queries"]
    assert all(c.status == PASS for c in rec.checks), rec.as_dict()
    files = checks["acquisition"].evidence["files"]
    assert all(f["verified"] == "checksum" and f["url"].startswith("http://127.0.0.1")
               for f in files.values())
    assert "compound_target" in checks["queries"].evidence["relation_kinds"]
    assert (tmp_path / "hub" / "audit" / "downloads.jsonl").is_file(), "every attempt recorded"
    # the second run reads the verified files it already has
    again = hub_datasets.validate_dataset(hub, "npass", live=True, log=lambda m: None)
    assert again.status == PASS


def test_a_refused_host_and_a_page_in_place_of_a_file_are_blocked_never_passed(served, tmp_path):
    name = "NPASSv2.0_download_naturalProducts_activities.txt"
    _Ranges.policy = {name}                       # the egress policy refuses this one
    rec = hub_datasets.validate_dataset(TCMDataHub(tmp_path / "h1"), "npass", live=True,
                                        log=lambda m: None)
    assert rec.check("acquisition").status in (BLOCKED, FAIL) and rec.status != PASS
    assert rec.check("build").status == SKIPPED, "nothing is built from a partial download"
    _Ranges.policy = set()
    page = served / name
    page.write_text("<!DOCTYPE html><html><body>Please log in</body></html>")
    rec = hub_datasets.validate_dataset(TCMDataHub(tmp_path / "h2"), "npass", live=True,
                                        log=lambda m: None)
    assert rec.check("acquisition").status != PASS and rec.status != PASS


def test_offline_nothing_is_requested_and_absent_files_are_skipped(served, tmp_path):
    rec = hub_datasets.validate_dataset(TCMDataHub(tmp_path / "hub"), "npass", live=False,
                                        log=lambda m: None)
    assert rec.check("acquisition").status == SKIPPED and rec.status == SKIPPED
    assert not _Ranges.seen, "offline, the server was never asked"


def test_a_manual_dataset_says_what_a_person_has_to_do(tmp_path):
    rec = hub_datasets.validate_dataset(TCMDataHub(tmp_path / "hub"), "drugbank_vocabulary",
                                        live=True, log=lambda m: None)
    acq = rec.check("acquisition")
    assert acq.status == BLOCKED and "import-raw drugbank_vocabulary" in acq.detail
    assert rec.check("build").status == SKIPPED


def test_the_command_writes_the_report_and_the_record_and_readiness_read_it(served, tmp_path):
    out = tmp_path / "out"
    assert validate(["datasets", "--datasets", "npass", "--live", "--out", str(out),
                     "--root", str(tmp_path / "hub")]) == 0
    doc = load_report(out / "hub_dataset_report.json")
    assert doc["report"] == "hub_dataset_validation" and doc["tier"] == "live"
    assert doc["datasets"] == ["npass"] and doc["records"][0]["status"] == PASS
    record = tmp_path / "record.json"
    assert validate(["record", "--report", str(out / "hub_dataset_report.json"),
                     "--record", str(record)]) == 0
    entry = json.loads(record.read_text())["datasets"]["npass"]
    assert entry["status"] == PASS and entry["checks"]["queries"] == PASS
    assert all(f["status"] == PASS and f["checksum"].startswith("sha256:")
               for f in entry["files"].values())
    recs = {r.component: r for r in databases.readiness(
        TCMDataHub(tmp_path / "empty"), verification=json.loads(record.read_text()),
        connectors={})}
    npass = recs["source.5"]
    assert npass.check("real_download").status == PASS
    assert npass.check("recorded_build").status == PASS
    # an offline report is never folded in as a live one
    assert validate(["datasets", "--datasets", "npass", "--out", str(tmp_path / "off"),
                     "--root", str(tmp_path / "hub")]) == 0
    assert validate(["record", "--report", str(tmp_path / "off" / "hub_dataset_report.json"),
                     "--record", str(record)]) == 2


def test_the_list_is_what_the_workflow_runs_one_runner_each(capsys):
    assert validate(["datasets", "--out", "unused", "--access", "download", "live", "--list"]) == 0
    keys = json.loads(capsys.readouterr().out)
    assert "npass" in keys and "symmap_api" in keys and "drugbank_vocabulary" not in keys


def test_a_validator_defect_on_one_dataset_does_not_end_the_run(served, tmp_path, monkeypatch):
    hub = TCMDataHub(tmp_path / "hub")
    monkeypatch.setattr(hub, "build", lambda key, log=None: (_ for _ in ()).throw(KeyError("x")))
    rec = hub_datasets.validate_dataset(hub, "npass", live=True, log=lambda m: None)
    assert rec.check("build").status == FAIL and "KeyError" in rec.check("build").detail
    assert rec.check("check").status == SKIPPED
    recs = hub_datasets.validate_datasets(hub, ["npass", "drugbank_vocabulary"], live=True,
                                          log=lambda m: None)
    assert [r.component for r in recs] == ["tcmdb.npass", "tcmdb.drugbank_vocabulary"]
    assert UNVERIFIED not in {recs[1].status}
