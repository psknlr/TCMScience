"""A data source's readiness is said, never inferred from its being in the catalogue.

A case-study session read the hub's cards for NPASS and CMAUP as available data: the cards
named them, no dataset stood behind them, and every attempt to fetch, build or query them
failed. Now each card and dataset says how ready it is here (catalogued, live, supported,
partial, downloaded, built) and the one step that would move it on; NPASS, CMAUP and LOTUS
are datasets the runner can fetch and build.
"""
from __future__ import annotations

from bioagent.tcmdb import TCMDataHub

from tcmstudio.dispatch import READINESS, call

IK = "LPLVUJXQOOQHMX-QWBHMCJMSA-N"


def _cards(ctx, **args):
    e = call("call_tool", {"tool": "tcmdb.catalog", "arguments": args}, ctx)
    assert e["status"] == "succeeded", e["error"]
    return e, {c["no"]: c for c in e["result"]["cards"]}


def _npass_files(raw):
    raw.mkdir(parents=True, exist_ok=True)
    files = {
        "generalInfo": "np_id\tpref_name\nNPC1\tglycyrrhizin\n",
        "structureInfo": f"np_id\tInChIKey\nNPC1\t{IK}\n",
        "speciesInfo": "org_id\torg_name\torg_tax_id\nNPO1\tGlycyrrhiza uralensis\t74649\n",
        "species_pair": "org_id\tnp_id\tref_id\tref_id_type\nNPO1\tNPC1\t123\tPMID\n",
        "targetInfo": "target_id\ttarget_name\tuniprot_id\nNPT1\tCyclooxygenase-2\tP35354\n",
        "activities": ("np_id\ttarget_id\tactivity_type\tactivity_value\tactivity_units\tref_id\t"
                       "ref_id_type\nNPC1\tNPT1\tIC50\t12.5\tnM\t456\tPMID\n"),
    }
    for part, text in files.items():
        (raw / f"NPASSv2.0_download_naturalProducts_{part}.txt").write_text(text, encoding="utf-8")


def test_a_card_says_how_ready_its_source_is_and_what_would_move_it_on(runner_ctx):
    e, cards = _cards(runner_ctx)
    assert e["result"]["readiness_states"] == list(READINESS)
    states = {c["readiness"]["state"] for c in cards.values()}
    assert states <= set(READINESS) and {"catalogued", "live", "supported"} <= states
    npass = cards[5]["readiness"]
    assert npass == {"state": "supported",
                     "next_step": "job.tcmdb.fetch {dataset: npass}, then job.tcmdb.build"}
    assert cards[137]["dataset"] == "lotus" and cards[137]["readiness"]["state"] == "supported"
    assert "wikidata_sparql" in cards[137]["readiness"]["live"], "the source answers live meanwhile"
    described = next(c for c in cards.values() if not c["dataset"] and not c["connector"])
    assert described["readiness"]["state"] == "catalogued"
    assert "built" not in e["summary_en"] and "catalogued" in e["summary_en"]
    assert any("only a dataset whose readiness is 'built'" in x for x in e["governance"]["limitations"])


def test_in_the_browser_the_step_names_the_runner_or_an_imported_store(browser_ctx):
    _, cards = _cards(browser_ctx, query="CMAUP")
    step = next(c for c in cards.values() if c["dataset"] == "cmaup")["readiness"]["next_step"]
    assert step.startswith("on the local runner, job.tcmdb.fetch {dataset: cmaup}")
    assert "tcmdb.import_store" in step


def test_npass_can_be_fetched_built_and_queried_where_it_only_could_be_found(runner_ctx):
    # the fetch job takes it now (it was "dataset must be one of …; did you mean 'gnps'?")
    fetch = call("call_tool", {"tool": "job.tcmdb.fetch", "arguments": {"dataset": "npass"}},
                 {**runner_ctx, "network": True})
    assert fetch["error"] is None or fetch["error"]["type"] != "bad_arguments", fetch["error"]
    hub = TCMDataHub(runner_ctx["tcmdb_root"])
    _npass_files(hub.raw_dir("npass"))
    status = call("call_tool", {"tool": "tcmdb.status", "arguments": {"dataset": "npass"}}, runner_ctx)
    assert status["result"]["datasets"][0]["readiness"] == {
        "state": "downloaded", "next_step": "job.tcmdb.build {dataset: npass}"}
    hub.build("npass", log=lambda m: None)
    _, cards = _cards(runner_ctx, query="NPASS")
    assert cards[5]["readiness"]["state"] == "built"
    rows = call("call_tool", {"tool": "tcmdb.relations", "arguments": {
        "kind": "compound_target", "subject": f"inchikey:{IK}"}}, runner_ctx)
    assert rows["status"] == "succeeded", rows["error"]
    found = rows["result"]["rows"]
    assert [(r["source"], r["object_id"], r["evidence"], r["reference"]) for r in found] == [
        ("npass", "uniprot:P35354", "known", "pmid:456")]


def test_an_optional_only_dataset_with_nothing_present_is_not_downloaded(runner_ctx):
    s = call("call_tool", {"tool": "tcmdb.status", "arguments": {"dataset": "herb1"}}, runner_ctx)
    r = s["result"]["datasets"][0]["readiness"]
    assert r["state"] == "supported" and "include_optional: true" in r["next_step"]
