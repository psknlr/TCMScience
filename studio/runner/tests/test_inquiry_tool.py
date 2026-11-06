"""research_inquiry: rival explanations competed through the dispatcher, kept per project.

The engine and the scaffolds are tested in BioScience-Harness (test_rivals.py,
test_inquiries.py). Here: the core tool reaches study.inquiry; a runtime-classified test
runs its tool through the dispatcher's own validation and executor and is classified from
that result; the engine's refusals come back as refusals with their remedy; an inquiry
lives in its project's folder and nowhere else; and the lists the tool schema restates
match the code they come from. The three ways an agent could certify its own inquiry
(an explanation with no claim kind read as an artefact, invented sources, analyses added
beside a scaffold's classified tests) are reproduced here as the agent would call them.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tcmstudio.catalog import get_entry
from tcmstudio.core_tools import (CORE_BY_NAME, INQUIRY_SCAFFOLDS, INQUIRY_SCHEMA,
                                  STUDY_DESIGNS, route)
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
    from bioagent.research.inquiries import DESIGN_ALIASES as ALIASES
    from bioagent.research.rivals import SCAFFOLDS
    from psh.sir.values import StudyDesign
    from tcmstudio.core_tools import DESIGN_ALIASES
    assert INQUIRY_SCAFFOLDS == tuple(SCAFFOLDS)
    assert STUDY_DESIGNS == tuple(d.value for d in StudyDesign)
    assert DESIGN_ALIASES == tuple(ALIASES)
    analysis = INQUIRY_SCHEMA["properties"]["analyses"]["items"]
    assert analysis["properties"]["design"]["enum"] == [*STUDY_DESIGNS, *DESIGN_ALIASES]
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


# ======================================== an agent cannot certify its own inquiry

def inquire(ctx, **args):
    return call("research_inquiry", args, ctx)


DOCKING = {
    "question": "does X cure T2D?",
    "explanations": [{"id": "cures", "proposition": "herb X cures type 2 diabetes in patients",
                      "prior": 0.475},
                     {"id": "inert", "proposition": "herb X has no effect in patients",
                      "prior": 0.475}],
    "analyses": [{"id": "dock", "title": "docking of X constituents to INSR",
                  "design": "in_silico", "outcomes": ["binds", "no"]},
                 {"id": "dock2", "title": "docking, second program", "design": "in_silico",
                  "outcomes": ["binds", "no"], "replicates": "dock"}],
    "predictions": {a: {"cures": {"binds": 0.99, "no": 0.01},
                        "inert": {"binds": 0.01, "no": 0.99}} for a in ("dock", "dock2")}}


def test_an_explanation_with_neither_claim_kind_nor_role_is_refused(runner_ctx):
    """role.py: with claim_kind left out, an explanation was read as an artefact, so licensing
    never applied and two docking runs accepted a human cure at strong certainty. Open now
    refuses it with the engine's code, saying what to give."""
    e = inquire(runner_ctx, action="open", **DOCKING)
    assert e["status"] == "refused" and e["error"]["type"] == "refused"
    refusal = e["governance"]["refusals"][0]
    assert refusal["code"] == "INQ103" and "INQ103" in e["summary_en"]
    assert refusal["message"].startswith("cures gives neither claim_kind nor role")
    assert 'give claim_kind, or role "artefact"' in refusal["message"]
    assert inquire(runner_ctx, action="list")["result"]["inquiries"] == []     # nothing saved
    # the schema says so before the call: there is no artefact by default any more
    item = INQUIRY_SCHEMA["properties"]["explanations"]["items"]
    assert "omit for an artefact" not in json.dumps(item)
    assert "claim_kind, or role" in item["description"]


def test_an_artefact_verdict_is_a_finding_about_the_analysis(runner_ctx):
    """role.py with role 'artefact' given: the verdict is about the analysis, not the world,
    and no surer than the docking designs it rests on."""
    explicit = {**DOCKING, "explanations": [{**x, "role": "artefact"}
                                            for x in DOCKING["explanations"]]}
    iid = inquire(runner_ctx, action="open", **explicit)["result"]["inquiry_id"]
    for a in ("dock", "dock2"):
        e = inquire(runner_ctx, action="record", inquiry_id=iid, analysis_id=a,
                    outcome="binds", evidence_ref=f"vina run {a}")
        assert e["status"] == "succeeded", e["error"]
    e = inquire(runner_ctx, action="conclude", inquiry_id=iid)
    c = e["result"]["conclusion"]
    assert c["leader"] == "cures" and c["role"] == "artefact" and c["claim_kind"] is None
    assert c["certainty"] in ("tentative", "uncertain")
    assert "a finding about the analysis, not about the world" in e["summary_en"]
    assert "关于分析本身的发现" in e["summary"]
    assert "a finding about the analysis, not about the world" in e["result"]["statement"]


SELF_CERTIFIED = {
    "question": "Does herb X cure disease Y in humans?",
    "explanations": [
        {"id": "works", "proposition": "X is clinically efficacious for Y",
         "claim_kind": "efficacy", "prior": 0.475},
        {"id": "null", "proposition": "X has no clinical effect on Y", "claim_kind": "efficacy",
         "prior": 0.475}],
    "analyses": [
        {"id": "t1", "title": "RCT 1", "design": "randomised_trial",
         "outcomes": ["benefit", "none"]},
        {"id": "t1r", "title": "RCT 2", "design": "randomised_trial",
         "outcomes": ["benefit", "none"], "replicates": "t1"}],
    "predictions": {a: {"works": {"benefit": 0.99, "none": 0.01},
                        "null": {"benefit": 0.01, "none": 0.99}} for a in ("t1", "t1r")}}


def test_outcomes_the_agent_classified_do_not_decide_acceptance(runner_ctx):
    """selfcert.py: rivals, designs, predictions, outcomes and sources ('PMID 1', 'PMID 2')
    all from the agent gave 'accepted, efficacy, licensed direct, strong'. The outcomes still
    move belief; they cannot be the severe test or the replication acceptance rests on."""
    iid = inquire(runner_ctx, action="open", **SELF_CERTIFIED)["result"]["inquiry_id"]
    for a, ref in (("t1", "PMID 1"), ("t1r", "PMID 2")):
        e = inquire(runner_ctx, action="record", inquiry_id=iid, analysis_id=a,
                    outcome="benefit", evidence_ref=ref)
        assert e["status"] == "succeeded", e["error"]
    e = inquire(runner_ctx, action="conclude", inquiry_id=iid)
    r, c = e["result"], e["result"]["conclusion"]
    assert c["leader"] == "works" and c["leader_posterior"] > 0.95
    assert c["verdict"] == "provisional" and c["certainty"] in ("tentative", "uncertain")
    assert r["by_agent"] == 2 and r["by_runtime"] == 0
    assert c["severe_tests"] == {} and c["replications"] == {}
    assert "the stopping rule is met" not in c["reason"]
    named = [x for x in c["limitations"] if x.startswith("2 of the 2 outcomes were classified "
                                                         "by the agent")]
    assert named and "t1, t1r" in named[0]
    assert "no test of this inquiry is classified by the runtime" in c["reason"]
    assert "Inquiry conclusion: provisional" in e["summary_en"] and "暂定" in e["summary"]


EFFICACY = {"intervention": "herb X", "outcome": "HbA1c", "population": "adults with T2D",
            "comparator": "placebo", "scale": "raw", "better": "lower", "mcid": 0.3}


def test_analyses_added_beside_a_scaffolds_classified_tests_cannot_decide_it(runner_ctx):
    """bypass.py: the efficacy scaffold's four tests are classified by the runtime, and a
    reported outcome on one is refused. Analyses the agent added beside them (not
    replicates), with its own 0.99/0.01 predictions and outcomes, reached 'accepted strong'
    with no runtime-classified outcome at all. Now they move belief and decide nothing."""
    iid = inquire(runner_ctx, action="open", scaffold="efficacy",
                  values=EFFICACY)["result"]["inquiry_id"]
    e = inquire(runner_ctx, action="record", inquiry_id=iid, analysis_id="low_risk",
                outcome="benefit", evidence_ref="PMID 1")
    assert e["error"]["type"] == "bad_arguments"
    sibling = {"id": "my_trials", "title": "low-risk trials, my reading",
               "design": "systematic_review", "outcomes": ["benefit", "none"]}
    mine = {"effective": {"benefit": 0.99, "none": 0.01},
            "ineffective": {"benefit": 0.01, "none": 0.99},
            "bias": {"benefit": 0.01, "none": 0.99}}
    for n, (aid, extra) in enumerate((("my_trials", {}),
                                      ("my_trials2", {"replicates": "my_trials"}),
                                      ("my_trials3", {"replicates": "my_trials"})), start=1):
        e = inquire(runner_ctx, action="declare", inquiry_id=iid, analysis_id=aid,
                    analysis={**sibling, "id": aid, **extra}, predictions=mine)
        assert e["status"] == "succeeded", e["error"]
        e = inquire(runner_ctx, action="record", inquiry_id=iid, analysis_id=aid,
                    outcome="benefit", evidence_ref=f"PMID {n}{n}{n}{n}{n}")
        assert e["status"] == "succeeded", e["error"]
    e = inquire(runner_ctx, action="conclude", inquiry_id=iid)
    r, c = e["result"], e["result"]["conclusion"]
    assert c["leader"] == "effective" and c["leader_posterior"] > 0.95
    assert c["verdict"] != "accepted" and c["certainty"] in ("tentative", "uncertain")
    assert r["by_runtime"] == 0 and r["by_agent"] == 3
    # the reason names the tests that can decide it: those the runtime classifies
    assert "only outcomes the runtime classified can decide acceptance" in c["reason"]
    for test in ("all_trials", "low_risk"):
        assert test in c["reason"]
    decide = {g["analysis_id"]: g for g in r["to_decide"]}
    assert decide["low_risk"]["classifier"] == "meta"


# ================================================= open question 2: saved inquiries

def _as_saved_before_the_rule(ctx, iid):
    path = Path(ctx["state_root"]) / "p-test" / "inquiries" / f"{iid}.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    del doc["record"]["acceptance_rule"]
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")


def test_a_saved_inquiry_is_re_evaluated_and_says_so_when_its_verdict_changed(runner_ctx):
    """An inquiry saved before the 2026-11 acceptance rule is concluded under it, and the
    conclusion says, in English and Chinese, that the verdict differs from the earlier one."""
    iid = inquire(runner_ctx, action="open", **SELF_CERTIFIED)["result"]["inquiry_id"]
    for a, ref in (("t1", "PMID 1"), ("t1r", "PMID 2")):
        inquire(runner_ctx, action="record", inquiry_id=iid, analysis_id=a,
                outcome="benefit", evidence_ref=ref)
    fresh = inquire(runner_ctx, action="conclude", inquiry_id=iid)
    assert "re_evaluated" not in fresh["result"]["conclusion"]     # begun under the rule
    _as_saved_before_the_rule(runner_ctx, iid)
    e = inquire(runner_ctx, action="conclude", inquiry_id=iid)
    c = e["result"]["conclusion"]
    assert c["verdict"] == "provisional"
    note = c["re_evaluated"]
    assert note["earlier_verdict"] == "accepted"
    assert note["en"].startswith("re-evaluated under the 2026-11 acceptance rule")
    assert note["zh"].startswith("已按 2026-11 接受规则重新评估")
    assert note["en"] in e["summary_en"] and note["zh"] in e["summary"]
    assert c["limitations"][0] == note["en"]


def test_a_saved_inquiry_whose_verdict_holds_carries_no_note(runner_ctx):
    iid = inquire(runner_ctx, action="open", scaffold="formula_vs_constituent",
                  values=FVC)["result"]["inquiry_id"]
    inquire(runner_ctx, action="record", inquiry_id=iid, analysis_id="contrast",
            data=contrast(LOWER, HIGHER))
    inquire(runner_ctx, action="record", inquiry_id=iid, analysis_id="contrast-rep",
            data=contrast([x - 0.1 for x in LOWER], HIGHER, study="s2"))
    _as_saved_before_the_rule(runner_ctx, iid)
    e = inquire(runner_ctx, action="conclude", inquiry_id=iid)
    assert e["result"]["conclusion"]["verdict"] == "accepted"
    assert "re_evaluated" not in e["result"]["conclusion"]
    assert "re-evaluated" not in e["summary_en"]


# ============================================================ hints the agent can act on

def test_predictions_that_sum_to_one_within_a_hundredth_are_sealed_normalised(runner_ctx):
    third = {"up": 0.33, "flat": 0.33, "down": 0.33}
    e = inquire(runner_ctx, action="open", question="Which way does the marker move?",
                explanations=[{"id": "a", "proposition": "A",
                               "claim_kind": "mechanism_hypothesis", "prior": 0.4},
                              {"id": "b", "proposition": "B", "role": "artefact",
                               "prior": 0.4}],
                analyses=[{"id": "t1", "title": "assay", "design": "in_vitro",
                           "outcomes": ["up", "flat", "down"]}],
                predictions={"t1": {"a": {"up": 0.8, "flat": 0.1, "down": 0.1}, "b": third}})
    assert e["status"] == "succeeded", e["error"]
    path = (Path(runner_ctx["state_root"]) / "p-test" / "inquiries"
            / f"{e['result']['inquiry_id']}.json")
    sealed = json.loads(path.read_text(encoding="utf-8"))["inquiry"]["trail"][1]["body"]
    assert sealed["predictions"]["b"] == pytest.approx({o: 1 / 3 for o in third})
    off = inquire(runner_ctx, action="open", question="q",
                  explanations=[{"id": "a", "proposition": "A", "role": "artefact",
                                 "prior": 0.4},
                                {"id": "b", "proposition": "B", "role": "artefact",
                                 "prior": 0.4}],
                  analyses=[{"id": "t1", "title": "assay", "design": "in_vitro",
                             "outcomes": ["up", "down"]}],
                  predictions={"t1": {"a": {"up": 0.7, "down": 0.28}}})
    assert off["governance"]["refusals"][0]["code"] == "INQ112"       # 0.98 is not 1


def test_the_american_spelling_of_a_randomised_design_is_read(runner_ctx):
    e = inquire(runner_ctx, action="open", **{
        **SELF_CERTIFIED, "analyses": [{**a, "design": "randomized_trial"}
                                       for a in SELF_CERTIFIED["analyses"]]})
    assert e["status"] == "succeeded", e["error"]
    inquiry = json.loads((Path(runner_ctx["state_root"]) / "p-test" / "inquiries"
                          / f"{e['result']['inquiry_id']}.json").read_text(encoding="utf-8"))
    designs = {a["design"] for a in inquiry["inquiry"]["trail"][0]["body"]["analyses"]}
    assert designs == {"randomised_trial"}


E2 = [{"id": "a", "proposition": "A is true", "claim_kind": "mechanism_hypothesis",
       "prior": 0.4},
      {"id": "b", "proposition": "B is true", "claim_kind": "mechanism_hypothesis",
       "prior": 0.4}]
A1 = [{"id": "t1", "title": "assay", "design": "in_vitro", "outcomes": ["up", "down"]}]


@pytest.mark.parametrize("action,args,says", [
    ("open", {"question": "q", "explanations": E2, "analyses": A1,
              "rule": {"accept_at": 0.9}}, "{question, explanations"),
    ("status", {"inquiry_id": "inq_123"}, "the inquiry_id open returned"),
    ("record", {"analysis_id": "t1", "outcome": "up"}, "evidence_ref (PMID"),
    ("declare", {"analysis_id": "t1"}, "predictions {explanation id: {outcome:"),
    ("withdraw", {"analysis_id": "t1"}, "the reason it cannot be run"),
    ("admit", {"explanations": [{"id": "c", "proposition": "C",
                                 "claim_kind": "mechanism_hypothesis"}]}, "shares"),
    ("plan", {"inquiry_id": "inq_123"}, "size 2 to 4"),
    ("conclude", {"inquiry_id": "x"}, "the inquiry_id open returned"),
])
def test_each_action_says_how_to_call_it(runner_ctx, action, args, says):
    """errors.py: every refusal that is not the engine's got one hint, 'action scaffolds
    shows each scaffold's parameters and tests', whatever the action."""
    iid = inquire(runner_ctx, action="open", question="q", explanations=E2,
                  analyses=A1)["result"]["inquiry_id"]
    if action != "open":
        args = {"inquiry_id": iid, **args}
    e = inquire(runner_ctx, action=action, **args)
    assert e["error"]["type"] == "bad_arguments", e
    assert says in e["error"]["hint"], e["error"]["hint"]


def test_a_missing_or_misshapen_prediction_comes_back_with_the_one_to_fill_in(runner_ctx):
    iid = inquire(runner_ctx, action="open", question="q", explanations=E2,
                  analyses=A1)["result"]["inquiry_id"]
    # the open shape given to declare (INQ111), and a record before any declaration (INQ110)
    for args in ({"action": "declare", "analysis_id": "t1",
                  "predictions": {"t1": {"a": {"up": 0.7, "down": 0.3}}}},
                 {"action": "record", "analysis_id": "t1", "outcome": "up",
                  "evidence_ref": "PMID 123"}):
        e = inquire(runner_ctx, inquiry_id=iid, **args)
        assert e["governance"]["refusals"][0]["code"] in ("INQ110", "INQ111")
        hint = e["error"]["hint"]
        skeleton = json.loads(hint[hint.index("{"):])
        assert skeleton == {"action": "declare", "inquiry_id": iid, "analysis_id": "t1",
                            "predictions": {"a": {"up": None, "down": None},
                                            "b": {"up": None, "down": None}}}
    # at open, an unknown analysis named in predictions: the shape for the ones it has
    e = inquire(runner_ctx, action="open", question="q", explanations=E2, analyses=A1,
                predictions={"t9": {"a": {"up": 0.7, "down": 0.3}}})
    assert e["governance"]["refusals"][0]["code"] == "INQ111"
    hint = e["error"]["hint"]
    assert json.loads(hint[hint.index("{"):]) == {"t1": {"a": {"up": None, "down": None},
                                                        "b": {"up": None, "down": None}}}
