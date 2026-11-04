"""Acceptance tests for the repairs of the tool and database availability audit of
2026-11-04 (``TCMScience_Tool_and_Database_Availability_Audit_20261104.xlsx``).

Each section names the audit's priority and finding, and tests its acceptance criterion
through Studio's dispatcher, the way the browser and the runner call a tool.
"""

from __future__ import annotations

import base64
import importlib.util
import sys
from pathlib import Path

import pytest

from tcmstudio import dispatch

ROOT = Path(__file__).resolve().parents[3]


def _coverage():
    spec = importlib.util.spec_from_file_location(
        "connector_coverage", ROOT / "studio" / "scripts" / "connector_coverage.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("connector_coverage", mod)
    spec.loader.exec_module(mod)
    return mod


def _answer(body: bytes, ctype: str = "text/plain;charset=UTF-8", status: int = 200):
    return lambda p: {"status": status, "headers": {"Content-Type": ctype},
                      "body_base64": base64.b64encode(body).decode(), "url": p["url"]}


@pytest.fixture
def browser(tmp_path):
    return {"where": "browser", "network": True, "state_root": str(tmp_path),
            "project_id": "audit"}


# ============================================================ P0 · SIGNOR error as HTTP 200

SIGNOR = "connector.signor.relations_among"
CONNECT = {"proteins": "P29317,Q06124,P04049,P15056"}
PHP = (b"<br />\n<b>Fatal error</b>:  Uncaught TypeError: array_map(): Argument #2 ($array) must "
       b"be of type array, string given in /var/www/html/getData.php:58\nStack trace:\n#0 {main}\n")


def _signor_rows(*rows):
    from bioagent.providers.supplement.mechanism import SIGNOR_COLUMNS
    out = []
    for a, ida, b, idb, sid in rows:
        f = dict.fromkeys(SIGNOR_COLUMNS, "")
        f.update(ENTITYA=a, TYPEA="protein", IDA=ida, DATABASEA="UNIPROT", ENTITYB=b,
                 TYPEB="protein", IDB=idb, DATABASEB="UNIPROT", EFFECT="up-regulates activity",
                 MECHANISM="binding", SIGNOR_ID=sid)
        out.append("\t".join(f.values()))
    return ("\n".join(out) + "\n").encode()


def test_the_audit_replay_of_signors_php_error_is_not_succeeded(browser):
    """The reproduced P0: the dispatcher used to answer succeeded, result.format=xml."""
    cc = _coverage()
    with cc.mock_transport(_answer(PHP)):
        env = dispatch.call(SIGNOR, CONNECT, browser)
    assert env["status"] == "failed"
    assert env["error"]["type"] == "invalid_response"
    assert env["result"] is None and "PHP" in env["error"]["message"]
    request = env["receipt"]["request"]
    assert request["failure_layer"] == "upstream_content"
    assert request["content_check"]["check"] == "server_error_report"
    assert not env.get("citations"), "an error report cites nothing"


@pytest.mark.parametrize("body, ctype", [
    (b"", "text/plain"),                                                     # empty
    (b"<!DOCTYPE html><html><body>Just a moment...</body></html>", "text/html"),  # challenge
    (b"ENTITYA\tP04049\nnot a relation\n", "text/plain"),                    # malformed
])
def test_challenge_pages_and_malformed_or_empty_payloads_cannot_contribute(browser, body, ctype):
    cc = _coverage()
    with cc.mock_transport(_answer(body, ctype)):
        env = dispatch.call(SIGNOR, CONNECT, browser)
    assert env["status"] == "failed" and env["error"]["type"] == "invalid_response"
    assert env["result"] is None


def test_the_four_accession_positive_control_returns_valid_relation_rows(browser):
    cc = _coverage()
    body = _signor_rows(("BRAF", "P15056", "RAF1", "P04049", "SIGNOR-900001"),
                        ("PTPN11", "Q06124", "RAF1", "P04049", "SIGNOR-900002"),
                        ("EPHA2", "P29317", "PTPN11", "Q06124", "SIGNOR-900003"))
    with cc.mock_transport(_answer(body)):
        env = dispatch.call(SIGNOR, CONNECT, browser)
    assert env["status"] == "succeeded", env.get("error")
    rows = env["result"]["rows"]
    assert len(rows) == 3 and rows[0]["ENTITYA"] == "BRAF" and rows[0]["IDB"] == "P04049"
    assert env["receipt"]["request"]["content_check"] == {"ok": True, "check": "response_contract",
                                                          "rows": 3}


# ============================================ P0 · a missing store reported as zero matches

GLYCYRRHIZIN = "LPLVUJXQOOQHMX-QWBHMCJMSA-N"
GAN_CAO = "ncbitaxon:74649"          # Glycyrrhiza uralensis


def _tsv(path, header, rows):
    path.write_text("\n".join(["\t".join(header), *("\t".join(r) for r in rows)]) + "\n",
                    encoding="utf-8")


@pytest.fixture
def cmaup_only(runner_ctx):
    """A hub holding CMAUP only (as the audit's hub held NPASS and CMAUP, not LOTUS)."""
    from bioagent.tcmdb import TCMDataHub
    hub = TCMDataHub(runner_ctx["tcmdb_root"])
    raw = hub.raw_dir("cmaup")
    raw.mkdir(parents=True)
    _tsv(raw / "CMAUPv2.0_download_Plants.txt",
         ["Plant_ID", "Plant_Name", "Species_Tax_ID", "Species_Name", "Genus_Name", "Family_Name"],
         [["NPO1001", "Gan Cao", "74649", "Glycyrrhiza uralensis", "Glycyrrhiza", "Fabaceae"]])
    _tsv(raw / "CMAUPv2.0_download_Ingredients_All.txt",
         ["np_id", "pref_name", "InChIKey", "pubchem_cid", "chembl_id"],
         [["NPC1", "glycyrrhizin", GLYCYRRHIZIN, "14982", ""]])
    (raw / "CMAUPv2.0_download_Plant_Ingredient_Associations_allIngredients.txt").write_text(
        "NPO1001\tNPC1\n", encoding="utf-8")
    _tsv(raw / "CMAUPv2.0_download_Targets.txt",
         ["Target_ID", "Gene_Symbol", "Protein_Name", "Uniprot_ID"],
         [["NPT1", "PTGS2", "Cyclooxygenase-2", "P35354"]])
    _tsv(raw / "CMAUPv2.0_download_Ingredient_Target_Associations_ActivityValues_References.txt",
         ["Ingredient_ID", "Target_ID", "Activity_Type", "Activity_Relationship",
          "Activity_Value", "Activity_Unit", "Reference_ID", "Reference_ID_Type"],
         [["NPC1", "NPT1", "IC50", "=", "15", "nM", "10.3/ghi", "DOI"]])
    hub.build("cmaup", log=lambda m: None)
    return runner_ctx


def _relations(ctx, **args):
    return dispatch.call("tcmdb.relations", {"kind": "organism_compound", "limit": 50, **args}, ctx)


def test_case_1_every_store_missing_is_dataset_not_built_not_zero_hits(runner_ctx):
    """An entirely empty hub used to answer succeeded with zero rows."""
    env = _relations(runner_ctx, subject=GAN_CAO)
    assert env["status"] == "failed" and env["error"]["type"] == "dataset_not_built"
    assert env["result"] is None, "no rows and no zero: nothing was searched"
    assert env["error"]["coverage"]["searched"] == []
    assert env["error"]["reason"] == "needs_dataset"
    named = dispatch.call("tcmdb.relations", {"kind": "organism_compound", "subject": GAN_CAO,
                                              "sources": ["lotus"]}, runner_ctx)
    assert named["error"]["type"] == "dataset_not_built"
    assert [m["dataset"] for m in named["error"]["coverage"]["missing"]] == ["lotus"]
    assert "job.tcmdb.fetch {dataset: lotus}" in named["error"]["message"]


def test_case_2_one_requested_store_missing_is_partial_and_names_it(cmaup_only):
    """LOTUS asked for alongside a built CMAUP: CMAUP's rows, LOTUS listed as not searched."""
    env = _relations(cmaup_only, subject=GAN_CAO, sources=["cmaup", "lotus"])
    assert env["status"] == "succeeded"
    cov = env["result"]["coverage"]
    assert cov["requested"] == ["cmaup", "lotus"] and cov["searched"] == ["cmaup"]
    assert [m["dataset"] for m in cov["missing"]] == ["lotus"]
    assert cov["partial"] is True and cov["complete"] is False
    assert env["result"]["by_source"] == {"cmaup": 1}, "a missing store is never a zero"
    assert "lotus" in env["summary_en"] and "not searched" in env["summary_en"]
    assert any("Partial coverage" in x for x in env["governance"]["limitations"])
    assert env["receipt"]["result"]["fallbacks"] == [
        {"what": "degraded", "reason": "partial coverage: not built here, not searched: lotus"}]
    # only LOTUS asked for, in a hub without it: refused, not an empty success
    alone = _relations(cmaup_only, subject=GAN_CAO, sources=["lotus"])
    assert alone["status"] == "failed" and alone["error"]["type"] == "dataset_not_built"


def test_case_3_a_searched_store_with_zero_matches_reports_its_zero(cmaup_only):
    env = _relations(cmaup_only, subject="ncbitaxon:261450", sources=["cmaup"])  # Coptis chinensis
    assert env["status"] == "succeeded" and env["result"]["count"] == 0
    assert env["result"]["by_source"] == {"cmaup": 0}
    cov = env["result"]["coverage"]
    assert cov["complete"] is True and cov["missing"] == []
    assert "searched with no match: cmaup" in env["summary_en"]


def test_case_4_a_positive_match(cmaup_only):
    env = _relations(cmaup_only, subject=GAN_CAO)
    assert env["status"] == "succeeded" and env["result"]["count"] == 1
    row = env["result"]["rows"][0]
    assert (row["subject_id"], row["object_id"]) == (GAN_CAO, f"inchikey:{GLYCYRRHIZIN}")
    assert env["result"]["coverage"]["requested"] == "all_built"
    assert env["result"]["coverage"]["searched"] == ["cmaup"]


def test_an_unknown_store_name_is_an_input_error_not_an_empty_search(cmaup_only):
    env = _relations(cmaup_only, subject=GAN_CAO, sources=["lotsu"])
    assert env["status"] == "failed" and env["error"]["type"] == "bad_arguments"


def test_a_missing_store_supplies_no_negative_evidence_to_consensus(cmaup_only):
    env = dispatch.call("tcmdb.consensus", {"kind": "organism_compound", "subject": GAN_CAO,
                                            "sources": ["cmaup", "lotus"]}, cmaup_only)
    assert env["status"] == "succeeded"
    assert env["result"]["coverage"]["missing"][0]["dataset"] == "lotus"
    assert "lotus" not in env["result"]["sources"], "a store not built is not a silent source"
    for item in env["result"]["items"]:
        assert "lotus" not in item.get("silent", []) and "lotus" not in item.get("not_tested", [])
    alone = dispatch.call("tcmdb.consensus", {"kind": "organism_compound", "subject": GAN_CAO,
                                              "sources": ["lotus"]}, cmaup_only)
    assert alone["error"]["type"] == "dataset_not_built"


def test_scenarios_and_the_agent_read_dataset_not_built_as_missing_data():
    from tcmstudio import scenarios
    env = {"status": "failed", "error": {"type": "dataset_not_built", "message": "x"}}
    assert scenarios.outcome_of(env) == "data_unavailable"


# =============================== P1 · historical verification did not reflect availability

from tcmstudio import opstatus  # noqa: E402


def test_the_committed_record_shows_the_production_failures_by_layer():
    herb = opstatus.view("connector.herb_api.search")
    assert herb["state"] == "failing" and herb["consecutive_failures"] == 2
    assert herb["latest_production"]["upstream_http"] == 403
    assert herb["latest_production"]["layer"] == "upstream_http"
    # the direct route worked the day before: the last success says where it was seen
    assert herb["last_success"] == {"at": "2026-11-03", "route": "direct_upstream",
                                    "record": "actions-41631774775"}
    dcabm = opstatus.view("connector.dcabm_tcm.herb_blood")
    assert dcabm["state"] == "failing" and dcabm["consecutive_failures"] == 3
    assert dcabm["last_failure"]["layer"] == "transport"            # the retry: no answer at all
    first = [o for o in opstatus.committed()["operations"]["connector.dcabm_tcm.herb_blood"]
             if o.get("layer") == "gateway"][0]
    assert first["gateway_http"] == 504 and first["error_type"] == "timeout"
    signor = opstatus.view("connector.signor.relations_among")
    assert signor["state"] == "failing" and signor["last_failure"]["layer"] == "upstream_content"


def test_a_successful_retry_marks_recovery_and_keeps_the_failure():
    chip = opstatus.view("connector.chip_atlas.genomes")
    assert chip["state"] == "recovered"
    assert chip["last_failure"]["error_type"] == "rate_limited"
    assert chip["last_failure"]["gateway_http"] == 429
    assert chip["last_success"]["at"] > chip["last_failure"]["at"]
    assert opstatus.view("connector.mygene.gene")["state"] == "operational"


def test_the_catalog_shows_current_status_apart_from_verification():
    from tcmstudio.catalog import get_entry
    e = get_entry("connector.herb_api.search")
    assert e["verification"]["levels"]["live_upstream"]["status"] == "PASS", "history, unchanged"
    assert e["operational"]["state"] == "failing"
    assert e["operational"]["latest_production"]["upstream_http"] == 403
    zh, en = opstatus.describe(e["operational"])
    assert "currently failing" in en and "upstream" in en and "last success 2026-11-03" in en
    assert "当前故障" in zh


def test_model_discovery_carries_the_status_and_says_what_is_failing(browser):
    env = dispatch.call("catalog_search", {"query": "HERB herb_api search", "limit": 25}, browser)
    assert env["status"] == "succeeded"
    hits = {m["id"]: m for m in env["result"]["matched"]}
    assert "connector.herb_api.search" in hits
    op = hits["connector.herb_api.search"]["operational"]
    assert op["state"] == "failing" and op["failure"]["upstream_http"] == 403
    assert op["last_success"] == "2026-11-03"
    assert "connector.herb_api.search" in env["text"] and "currently failing" in env["summary_en"]


def test_invocation_records_the_call_and_a_success_after_failure_is_recovery(browser):
    cc = _coverage()
    sid = "connector.signor.relations_among"
    with cc.mock_transport(_answer(PHP)):
        failed = dispatch.call(SIGNOR, CONNECT, browser)
    assert failed["receipt"]["operational"]["state"] == "failing"
    assert "already failing before this call" in failed["error"]["hint"]
    mine = opstatus.local(browser["state_root"], sid)[sid]
    assert mine[-1]["route"] == "browser_gateway" and mine[-1]["layer"] == "upstream_content"
    body = _signor_rows(("BRAF", "P15056", "RAF1", "P04049", "SIGNOR-900001"))
    with cc.mock_transport(_answer(body)):
        ok = dispatch.call(SIGNOR, CONNECT, browser)
    assert ok["status"] == "succeeded"
    status = ok["receipt"]["operational"]
    assert status["state"] == "recovered" and status["failure"]["layer"] == "upstream_content"
    v = opstatus.view(sid, browser["state_root"])
    assert v["last_failure"]["route"] == "browser_gateway", "the failure stays in the record"
    assert v["latest"]["route"] == "browser_gateway" and v["latest"]["outcome"] == "succeeded"


def test_a_call_refused_before_any_request_is_not_an_observation(tmp_path):
    off = {"where": "browser", "network": False, "state_root": str(tmp_path), "project_id": "x"}
    env = dispatch.call(SIGNOR, CONNECT, off)
    assert env["error"]["type"] == "network_off"
    assert opstatus.local(str(tmp_path)) == {}


def test_the_committed_record_is_well_formed():
    import subprocess
    res = subprocess.run([sys.executable, str(ROOT / "studio" / "scripts" / "operational_status.py"),
                          "--check"], capture_output=True, text=True, timeout=300)
    assert res.returncode == 0, res.stdout + res.stderr
    assert "444 operations" in res.stdout


def test_the_browser_catalog_states_the_plain_case_once():
    """444 full views would not fit catalog.json's budget: an operation that succeeded at its
    latest production check carries none, and the document says so once."""
    from tcmstudio.catalog import build_catalog
    doc = build_catalog("browser", probe=False)
    assert doc["operational"]["absent"] == "operational" and doc["operational"]["checked"] == "2026-11-04"
    entries = {e["id"]: e for e in doc["entries"]}
    assert "operational" not in entries["connector.mygene.gene"]
    carried = [e["id"] for e in doc["entries"] if "operational" in e]
    assert len(carried) == 40 and "connector.signor.relations_among" in carried
    assert {entries[i]["operational"]["state"] for i in carried} == {"failing", "recovered"}
