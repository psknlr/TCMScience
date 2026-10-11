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


# ------------------------------------------------------- which files, which entities
def test_a_dataset_is_validated_on_the_files_its_declared_relations_come_from():
    from bioagent.tcmdb.datasets import dataset

    def names(key):
        return [f.name for f in hub_datasets._files(dataset(key))]
    # genetic interactions are only in BioGRID's complete file; nSIDES's adverse events
    # only in OFFSIDES (TWOSIDES, 738 MB, yields none); the eQTL credible sets, not the
    # 779 MB analysis-layer parquet
    assert "BIOGRID-ALL-5.0.262.tab3.zip" in names("biogrid")
    assert names("nsides") == ["README.txt", "OFFSIDES.csv.gz"]
    assert "QTD000266.credible_sets.tsv.gz" in names("eqtl_catalogue")
    assert not any(n.endswith(".parquet") for n in names("eqtl_catalogue"))
    assert names("herb1") == [f.name for f in dataset("herb1").files], "all optional"
    assert names("npass") == [f.name for f in dataset("npass").files if not f.optional]


class _Answer:
    def __init__(self, value):
        from bioagent.status import ExecutionStatus
        self.status, self.value, self.error = ExecutionStatus("SUCCEEDED"), value, None


_SYMMAP_HERB = {
    "Mol": [{"MOL_id": "SMIT00013", "Molecule_name": "Quercetin", "PubChem_CID": "5280343",
             "TCMSP_id": "MOL000098", "evidence": ""}],
    "Gene": [{"Gene_symbol": "TNF", "Gene_name": "tumor necrosis factor", "Value": "0.2",
              "P_value": "0.01", "FDR(BH)": "0.04", "Relationship": "By_ingredient"}],
    "TCM_symptom": [{"TCM_symptom_id": "SMTS00001", "TCM_symptom_name": "心悸",
                     "Type": "Summarized terms"}],
    "MM_symptom": [{"UMLS_id": "C0030252", "MM_symptom_id": "SMMS00001",
                    "MM_symptom_name": "Palpitations", "Value": "0.1", "P_value": "0.02",
                    "FDR(BH)": "0.05", "Relationship": "By_TCM_symptom"}],
    "Disease": [{"Disease_id": "SMDE00001", "Disease_name": "Arrhythmia", "Value": "0.1",
                 "P_value": "0.03", "FDR(BH)": "0.06", "Relationship": "By_target"}],
    "Syndrome": [{"Syndrome_id": "SMSY00003", "Syndrome_name": "中气下陷",
                  "Syndrome_English": "sinking of the middle qi", "Type": "Summarized terms"}],
}


def test_a_per_entity_service_is_asked_for_a_herb_and_an_ingredient(tmp_path):
    """SymMap's ingredient_target comes only from an ingredient's answer (the first live
    run asked for a herb alone and the kind came back empty)."""
    hub, asked = TCMDataHub(tmp_path / "hub"), []

    def live(connector, operation, **kw):
        asked.append((kw["entity_id"], kw["related"]))
        if kw["entity_id"].startswith("SMIT"):
            return _Answer({"data": [{"Gene_symbol": "PTGS2", "Gene_name": "COX-2",
                                      "score": "1", "evidence": "PMID: 123"}]})
        return _Answer({"data": _SYMMAP_HERB[kw["related"]]})
    hub.live = live
    rec = hub_datasets.validate_dataset(hub, "symmap_api", live=True, log=lambda m: None)
    assert rec.status == PASS, rec.as_dict()
    assert ("SMIT00013", "Gene") in asked and ("SMIT00013", "Mol") not in asked
    assert set(rec.check("queries").evidence["relation_kinds"]) == {
        "herb_ingredient", "herb_target", "herb_symptom", "herb_disease", "herb_syndrome",
        "ingredient_target"}


def test_an_empty_kind_names_what_the_answers_held(tmp_path):
    hub = TCMDataHub(tmp_path / "hub")
    hub.live = lambda connector, operation, **kw: _Answer(
        {"data": [] if kw["entity_id"].startswith("SMIT") else _SYMMAP_HERB[kw["related"]]})
    rec = hub_datasets.validate_dataset(hub, "symmap_api", live=True, log=lambda m: None)
    queries = rec.check("queries")
    assert queries.status == FAIL and queries.evidence["empty_kinds"] == ["ingredient_target"]
    assert queries.evidence["answer_shapes"]["SMIT00013__Gene"] == {"data": 0}
    assert "the answers hold" in queries.detail


class _Hub:
    """A built store, as far as the query check sees it."""

    def __init__(self, kinds, tables):
        self.kinds, self.tables = set(kinds), set(tables)

    def relations(self, kind, sources, limit):
        return [{}] if kind in self.kinds else []

    def query(self, key, table, limit):
        return [{}] if table in self.tables else []


def test_a_kind_the_release_is_known_to_leave_empty_is_reported_not_failed():
    from bioagent.tcmdb.datasets import dataset
    spec = dataset("aopwiki")
    assert "stressor_event" in spec.known_empty
    report = {"tables": {"aop": 1, "key_event": 1, "key_event_stressor": 0}}
    rec = hub_datasets.Record("tcmdb.aopwiki", "hub_dataset", "")
    hub_datasets._queries(rec, _Hub(set(spec.relations) - {"stressor_event"},
                                    {"aop", "key_event"}), spec, report)
    check = rec.check("queries")
    assert check.status == PASS and check.evidence["known_empty"] == [
        "stressor_event", "key_event_stressor"]
    assert "AOPWikiRDF" in check.detail, "with how it is known"
    # without the spec's statement, the same store fails
    bare = replace(spec, known_empty={})
    rec = hub_datasets.Record("tcmdb.aopwiki", "hub_dataset", "")
    hub_datasets._queries(rec, _Hub(set(spec.relations) - {"stressor_event"},
                                    {"aop", "key_event"}), bare, report)
    assert rec.check("queries").status == FAIL


def _entity_fetch(hub, entities, *, build, log, **kw):
    assert build is False
    return [{"antigen": e, "file": f"{e}.tsv", "ok": True} for e in entities]


def _entity_fetch_refused(hub, entities, *, build, log, **kw):
    return [{"antigen": e, "ok": False, "error": f"no Target Genes table for {e!r}"}
            for e in entities]


def test_relations_fetched_per_entity_are_fetched_for_one_before_the_build(
        served, tmp_path, monkeypatch):
    monkeypatch.setitem(hub_datasets.ENTITY_TABLES, "npass",
                        (__name__, "_entity_fetch", ("STAT3",), {}))
    rec = hub_datasets.validate_dataset(TCMDataHub(tmp_path / "h1"), "npass", live=True,
                                        log=lambda m: None)
    assert rec.check("entity_tables").status == PASS and rec.status == PASS
    monkeypatch.setitem(hub_datasets.ENTITY_TABLES, "npass",
                        (__name__, "_entity_fetch_refused", ("STAT3",), {}))
    rec = hub_datasets.validate_dataset(TCMDataHub(tmp_path / "h2"), "npass", live=True,
                                        log=lambda m: None)
    assert rec.check("entity_tables").status == FAIL
    assert rec.check("build").status == SKIPPED
    assert "STAT3" in rec.check("entity_tables").detail


def test_the_hub_fetches_exactly_the_files_named(served, tmp_path):
    from bioagent.tcmdb import HubError
    hub = TCMDataHub(tmp_path / "hub")
    name = "NPASSv2.0_download_naturalProducts_generalInfo.txt"
    out = hub.fetch("npass", files=[name], log=lambda m: None)
    assert [r["file"] for r in out] == [name] and out[0]["ok"]
    with pytest.raises(HubError, match="no file"):
        hub.fetch("npass", files=["nothing.txt"], log=lambda m: None)


# ------------------------------------------------------- unreachable sources, asked again
def test_the_unreachable_sources_are_asked_once_each_and_an_answer_asks_for_review(
        tmp_path, monkeypatch):
    from bioagent.validation import network
    from bioagent.validation.network import Probe

    asked = []

    def fake(url, timeout_s=20.0):
        asked.append(url)
        if "hit2.badd-cao" in url:
            return Probe(url, "hit2.badd-cao.net", PASS, "HTTP 200 from hit2.badd-cao.net",
                         http_status=200, final_url=url)
        return Probe(url, "x", BLOCKED, "the name does not resolve here")
    monkeypatch.setattr(network, "probe", fake)
    assert validate(["reachability", "--out", str(tmp_path)]) == 0
    doc = load_report(tmp_path / "source_reachability_report.json")
    recs = {r["component"]: r for r in doc["records"]}
    assert len(asked) == len(set(asked)) == len(recs) - 1, "one request per source but one"
    assert all(u.startswith("http") and " " not in u for u in asked), "only the address"
    hit = recs["source.9"]
    assert hit["status"] == PASS and "re-assess" in hit["checks"][0]["detail"]
    assert recs["source.2"]["status"] == BLOCKED
    # HIT 1.0's host was defaced: it is recorded as compromised and gets no request at all
    assert not any("www.badd-cao.net" in u for u in asked)
    assert recs["source.10"]["status"] == SKIPPED
    assert "compromised" in recs["source.10"]["checks"][0]["detail"]
