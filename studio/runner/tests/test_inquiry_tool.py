"""research_inquiry: rival explanations competed through the dispatcher, kept per project.

The engine and the scaffolds are tested in BioScience-Harness (test_rivals.py,
test_inquiries.py). Here: the core tool reaches study.inquiry; a runtime-classified test
runs its tool through the dispatcher's own validation and executor and is classified from
that result; the engine's refusals come back as refusals with their remedy; an inquiry
lives in its project's folder and nowhere else; and the lists the tool schema restates
match the code they come from.
"""

from __future__ import annotations

from pathlib import Path

from tcmstudio.catalog import get_entry
from tcmstudio.core_tools import (CORE_BY_NAME, INQUIRY_SCAFFOLDS, STUDY_DESIGNS, route)
from tcmstudio.dispatch import call

FVC = {"formula": "葛根芩连汤", "constituent": "berberine", "endpoint": "fasting glucose",
       "margin": 0.5, "better": "lower"}


def contrast(formula, monomer, study="s1"):
    return {"formula": {"label": "GQD", "values": formula, "study": study},
            "monomer": {"label": "berberine", "values": monomer, "study": study},
            "higher_is_better": False, "margin": 0.5, "dose_matched": True}


LOWER = [5.1, 5.0, 4.9, 5.2, 5.0, 4.8]
HIGHER = [6.1, 6.0, 6.3, 5.9, 6.2, 6.0]


def test_the_lists_in_the_schema_are_the_codes_own():
    from bioagent.research.rivals import SCAFFOLDS
    from psh.sir.values import StudyDesign
    assert INQUIRY_SCAFFOLDS == tuple(SCAFFOLDS)
    assert STUDY_DESIGNS == tuple(d.value for d in StudyDesign)
    assert route("research_inquiry", {"action": "list"}) == ("study.inquiry",
                                                             {"action": "list"})
    entry = get_entry("study.inquiry")
    assert entry["kind"] == "study" and entry["parameters"]["additionalProperties"] is False
    assert CORE_BY_NAME["research_inquiry"]["maps_to"] == "study.inquiry"


def test_the_classifier_tools_run_locally_and_need_no_approval():
    # recording data runs the classifier's tool inside the inquiry's own call: the page must
    # have loaded that tool's packages first, the tool must run in the browser, and nothing
    # it does may need an approval the page would have asked for on the tool's own call
    from bioagent.research.rivals import CLASSIFIER_TOOLS
    tools = [get_entry(t) for t in CLASSIFIER_TOOLS.values() if t]
    need = {p for e in tools for p in e.get("pyodide_packages") or ()}
    assert need and need <= set(get_entry("study.inquiry")["pyodide_packages"])
    for e in tools:
        assert e["exec"] == ["browser", "runner"], e["id"]
        assert not any(e.get(k) for k in ("confirm", "job", "network", "network_if")), e["id"]


def test_a_tool_that_needs_an_approval_is_never_run_inside_an_inquiry(runner_ctx,
                                                                       monkeypatch):
    from bioagent.research import rivals
    monkeypatch.setitem(rivals.CLASSIFIER_TOOLS, "contrast", "connector.europepmc.search")
    e = call("research_inquiry", {"action": "open", "scaffold": "formula_vs_constituent",
                                  "values": FVC}, runner_ctx)
    iid = e["result"]["inquiry_id"]
    e = call("research_inquiry", {"action": "record", "inquiry_id": iid,
                                  "analysis_id": "contrast", "data": {"query": "berberine"}},
             runner_ctx)
    assert e["status"] == "refused" and "does not run inside an inquiry" in e["error"]["message"]
    e = call("research_inquiry", {"action": "status", "inquiry_id": iid}, runner_ctx)
    assert e["result"]["observed"] == []


def test_an_inquiry_runs_to_acceptance_through_the_dispatcher(runner_ctx):
    e = call("research_inquiry", {"action": "open", "scaffold": "formula_vs_constituent",
                                  "values": FVC}, runner_ctx)
    assert e["status"] == "succeeded", e["error"]
    iid = e["result"]["inquiry_id"]
    assert "no leader yet" not in e["summary_en"] or "next: run contrast" in e["summary_en"]
    assert any("not a release" in x for x in e["governance"]["limitations"])
    e = call("research_inquiry", {"action": "record", "inquiry_id": iid,
                                  "analysis_id": "contrast",
                                  "data": contrast(LOWER, HIGHER)}, runner_ctx)
    assert e["status"] == "succeeded", e["error"]
    update = e["result"]["update"]
    assert update["outcome"] == "formula_exceeds" and update["by"] == "runtime"
    assert update["evidence_ref"].startswith("runtime:study.contrast_formula_monomer:")
    assert "classified by the runtime" in e["summary_en"] and "运行时判定" in e["summary"]
    e = call("call_tool", {"tool": "study.inquiry", "arguments": {
        "action": "record", "inquiry_id": iid, "analysis_id": "contrast-rep",
        "data": contrast([x - 0.1 for x in LOWER], HIGHER, study="s2")}}, runner_ctx)
    assert e["status"] == "succeeded", e["error"]
    e = call("research_inquiry", {"action": "conclude", "inquiry_id": iid}, runner_ctx)
    c = e["result"]["conclusion"]
    assert c["verdict"] == "accepted" and c["leader"] == "formula_adds"
    assert c["certainty"] == "tentative"
    assert "stated at most tentative" in e["summary_en"]
    assert e["governance"]["limitations"][0].startswith("A conclusion is not a release")
    folder = Path(runner_ctx["state_root"]) / "p-test" / "inquiries"
    assert (folder / f"{iid}.json").is_file()


def test_the_receipt_binds_the_inquiry_and_its_trail_head(runner_ctx):
    from tcmstudio.envelope import sha256_json
    e = call("research_inquiry", {"action": "open", "scaffold": "formula_vs_constituent",
                                  "values": FVC}, runner_ctx)
    r = e["result"]
    assert r["inquiry_id"].startswith("inq_") and len(r["head"]) == 64
    assert e["receipt"]["output_sha256"] == sha256_json(r)


def test_the_tool_a_test_names_is_validated_before_anything_is_recorded(runner_ctx):
    iid = call("research_inquiry", {"action": "open", "scaffold": "formula_vs_constituent",
                                    "values": FVC}, runner_ctx)["result"]["inquiry_id"]
    bad = call("research_inquiry", {"action": "record", "inquiry_id": iid,
                                    "analysis_id": "contrast",
                                    "data": {"formula": {"label": "GQD"}}}, runner_ctx)
    assert bad["status"] == "failed" and bad["error"]["type"] == "bad_arguments"
    assert "nothing was recorded" in bad["error"]["message"]
    unsealed = call("research_inquiry", {"action": "record", "inquiry_id": iid,
                                         "analysis_id": "contrast",
                                         "data": {**contrast(LOWER, HIGHER), "margin": 3}},
                    runner_ctx)
    assert unsealed["error"]["type"] == "bad_arguments"
    assert "not classified, nothing recorded" in unsealed["error"]["message"]
    reported = call("research_inquiry", {"action": "record", "inquiry_id": iid,
                                         "analysis_id": "contrast",
                                         "outcome": "formula_exceeds",
                                         "evidence_ref": "PMID:1"}, runner_ctx)
    assert reported["error"]["type"] == "bad_arguments"
    assert "classified by the runtime" in reported["error"]["message"]
    status = call("research_inquiry", {"action": "status", "inquiry_id": iid}, runner_ctx)
    assert status["result"]["observed"] == []


def test_engine_refusals_come_back_as_refusals_with_their_remedy(runner_ctx):
    e = call("research_inquiry", {
        "action": "open", "question": "Does X improve Y?",
        "explanations": [{"id": "works", "proposition": "X improves Y",
                          "claim_kind": "efficacy", "prior": 0.4},
                         {"id": "not", "proposition": "X does not improve Y",
                          "claim_kind": "efficacy", "prior": 0.4}],
        "analyses": [{"id": "dock", "title": "docking", "design": "in_silico",
                      "outcomes": ["good", "bad"]}],
        "predictions": {"dock": {"works": {"good": 0.9, "bad": 0.1},
                                 "not": {"good": 0.2, "bad": 0.8}}}}, runner_ctx)
    assert e["status"] == "refused" and e["error"]["type"] == "refused"
    assert e["governance"]["refusals"][0]["code"] == "INQ113"
    assert "holds this explanation fixed" in e["governance"]["refusals"][0]["remedy"]
    assert "INQ113" in e["summary_en"]
    lowered = call("research_inquiry", {"action": "open", "scaffold": "efficacy", "values": {
        "intervention": "x", "outcome": "y", "population": "z", "comparator": "w",
        "scale": "raw", "better": "lower"}, "rule": {"require_replication": False}},
        runner_ctx)
    assert lowered["error"]["type"] == "bad_arguments"
    assert "cannot be turned off" in lowered["error"]["message"]


def test_inquiries_belong_to_their_project(runner_ctx, browser_ctx):
    iid = call("research_inquiry", {"action": "open", "scaffold": "target_engagement",
                                    "values": {"compound": "quercetin", "target": "PTGS2"}},
               runner_ctx)["result"]["inquiry_id"]
    other = {**runner_ctx, "project_id": "another"}
    e = call("research_inquiry", {"action": "status", "inquiry_id": iid}, other)
    assert e["error"]["type"] == "not_found"
    listed = call("research_inquiry", {"action": "list"}, runner_ctx)
    assert [i["inquiry_id"] for i in listed["result"]["inquiries"]] == [iid]
    assert call("research_inquiry", {"action": "list"}, other)["result"]["inquiries"] == []
    web = call("research_inquiry", {"action": "open", "scaffold": "target_engagement",
                                    "values": {"compound": "quercetin", "target": "PTGS2"}},
               browser_ctx)
    assert web["status"] == "succeeded"
    assert (Path(browser_ctx["state_root"]) / "p-web" / "inquiries").is_dir()


def test_reported_outcomes_and_withdrawals_through_the_dispatcher(runner_ctx):
    iid = call("research_inquiry", {"action": "open", "scaffold": "safety_signal",
                                    "values": {"herb": "何首乌", "event": "liver injury"}},
               runner_ctx)["result"]["inquiry_id"]
    missing = call("research_inquiry", {"action": "record", "inquiry_id": iid,
                                        "analysis_id": "dechallenge", "outcome": "positive"},
                   runner_ctx)
    assert missing["error"]["type"] == "bad_arguments"
    assert "evidence_ref" in missing["error"]["message"]
    e = call("research_inquiry", {"action": "record", "inquiry_id": iid,
                                  "analysis_id": "dechallenge", "outcome": "positive",
                                  "evidence_ref": "PMID:12345678"}, runner_ctx)
    assert e["status"] == "succeeded" and "reported by the agent" in e["summary_en"]
    e = call("research_inquiry", {"action": "withdraw", "inquiry_id": iid,
                                  "analysis_id": "controlled",
                                  "reason": "not yet done: no cohort study found"}, runner_ctx)
    assert e["status"] == "succeeded" and "Withdrawn controlled" in e["summary_en"]
    e = call("research_inquiry", {"action": "conclude", "inquiry_id": iid}, runner_ctx)
    gaps = {g["analysis_id"] for g in e["result"]["to_decide"]}
    assert "controlled" in gaps
    assert any("not reassurance" in x for x in e["governance"]["limitations"])


def test_a_plan_seals_analyses_to_run_at_once_and_a_conclusion_says_how_it_holds(runner_ctx):
    iid = call("research_inquiry", {"action": "open", "scaffold": "formula_vs_constituent",
                                    "values": FVC}, runner_ctx)["result"]["inquiry_id"]
    for size in (1, 5, 2.5):
        e = call("research_inquiry", {"action": "plan", "inquiry_id": iid, "size": size},
                 runner_ctx)
        assert e["error"]["type"] == "bad_arguments", size
    e = call("research_inquiry", {"action": "plan", "inquiry_id": iid}, runner_ctx)
    assert e["status"] == "succeeded", e["error"]
    batch = e["result"]["batch"]
    assert [b["analysis_id"] for b in batch] == ["contrast", "contrast-rep"]
    assert all(b["run_with"].startswith("study.contrast_formula_monomer") for b in batch)
    assert "sealed 2 analyses to run at once" in e["summary_en"] and "已封存" in e["summary"]
    # run together: recorded in either order, each as part of the engine's batch
    e = call("research_inquiry", {"action": "record", "inquiry_id": iid,
                                  "analysis_id": "contrast-rep",
                                  "data": contrast([x - 0.1 for x in LOWER], HIGHER, "s2")},
             runner_ctx)
    assert e["result"]["update"]["purpose"] == "batch"
    call("research_inquiry", {"action": "record", "inquiry_id": iid, "analysis_id": "contrast",
                              "data": contrast(LOWER, HIGHER)}, runner_ctx)
    e = call("research_inquiry", {"action": "plan", "inquiry_id": iid, "size": 3}, runner_ctx)
    assert e["result"]["batch"] == [] and "nothing to run at once" in e["summary_en"]
    e = call("research_inquiry", {"action": "conclude", "inquiry_id": iid}, runner_ctx)
    assert e["result"]["sensitivity"]["leader_holds_to"] == 0.3
    assert "still leads with every prediction 30% less sure" in e["summary_en"]
    assert "仍领先" in e["summary"]
    assert "still leads" in e["governance"]["limitations"][-1]
