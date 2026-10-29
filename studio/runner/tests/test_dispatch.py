"""The dispatcher: every core tool offline, governed runs, refusals, both runtimes."""

from __future__ import annotations

import json
from pathlib import Path

from tcmstudio.catalog import get_entry
from tcmstudio.dispatch import Context, call, call_json, validate

LOCK_HASHES = {
    "normalize-tcm-entities": "4bdb9f7bf49b55f4e8ae4e4ccfffe313a705d7cd7b3dc880ea3b0b386eef66c6",
    "retrieve-tcm-evidence": "94a6671b7225e4b7df320e89082a076db23aa75c19da5ad3ce3763ceaca238df",
    "assess-tcm-safety": "5a12df2abe348b70547efb3529853cc6cb40c0495bce4eb5db9c732cf306a87e",
    "analyze-tcm-network-pharmacology": "3acaee4fdb5f1bd6de4f6ee65644e634a6ff1e8e4484ffe276674568935e1760",
}


def lock_hash(skill_id: str) -> str:
    """The pin in the repository's lockfile (read, not assumed)."""
    import yaml
    from tcmstudio.catalog import skill_specs
    spec = next(s for s in skill_specs() if s["id"] == skill_id)
    doc = yaml.safe_load(Path(spec["lockfile"]).read_text(encoding="utf-8"))
    return next(s["content_hash"] for s in doc["skills"] if s["skill_id"] == skill_id)


def core_cases(intake, visits):
    return [
        ("tcm_lookup", {"name": "参"}, "succeeded"),
        ("tcm_herb", {"name": "黄芪"}, "succeeded"),
        ("tcm_formula", {"name": "桂枝汤"}, "succeeded"),
        ("tcm_syndrome", {"name": "脾胃气虚证"}, "succeeded"),
        ("tcm_classical_search", {"query": "桂枝汤主之", "limit": 3}, "succeeded"),
        ("tcm_compatibility", {"herbs": ["甘草", "甘遂"]}, "succeeded"),
        ("tcm_applicability", {"subject": "桂枝汤", "object": "太阳中风证",
                               "claim_kind": "attribution"}, "succeeded"),
        ("tcm_normalize", {"names": ["黄芪", "桂枝汤", "姜"]}, "succeeded"),
        ("tcm_evidence", {"subject": "黄芪", "claim_kind": "traditional_use"}, "succeeded"),
        ("tcm_safety_report", {"subject": "甘草", "co_administered": ["甘遂"]}, "succeeded"),
        ("tcm_network_hypothesis", {"formula_name": "桂枝汤"}, "succeeded"),
        ("clinic_assess", {"intake": intake}, "succeeded"),
        ("clinic_check_prescription", {"intake": intake, "herbs": [{"herb": "甘草", "grams": 6},
                                                                   {"herb": "甘遂", "grams": 3}]},
         "succeeded"),
        ("clinic_followup", visits, "succeeded"),
        ("tcmdb_catalog", {"query": "HERB"}, "succeeded"),
        ("tcmdb_relations", {"kind": "herb_drug_interaction", "subject": "Ginkgo",
                             "contains": True}, "succeeded"),
        ("tcmdb_consensus", {"kind": "herb_ingredient", "subject": "黄芪"}, "succeeded"),
        ("connector_call", {"connector": "uniprot", "operation": "entry",
                            "arguments": {"accession": "P04637"}}, "failed"),
        ("literature_search", {"query": "Astragalus membranaceus"}, "failed"),
        ("network_pharmacology_run", {"formula": "葛根芩连汤", "disease": "MONDO_0005148"},
         "job_submitted"),
        ("run_pipeline", {"pipeline": "rnaseq", "arguments": {"samples": "upload_123"}},
         "job_submitted"),
        ("capabilities_status", {}, "succeeded"),
        ("catalog_search", {"query": "十八反"}, "succeeded"),
        ("research_routes", {"route": "safety"}, "succeeded"),
        ("research_inquiry", {"action": "scaffolds"}, "succeeded"),
        ("call_tool", {"tool": "native.egfr_ckd_epi_2021",
                       "arguments": {"creatinine_mg_dl": 1.0, "age_years": 50, "sex": "female"}},
         "succeeded"),
    ]


def check_envelope(e):
    assert set(e) == {"ok", "tool", "via", "status", "duration_ms", "summary", "summary_en",
                      "text", "result", "citations", "governance", "receipt", "job", "approval",
                      "error"}
    assert e["status"] in ("succeeded", "failed", "refused", "needs_approval", "job_submitted",
                           "cancelled")
    assert e["ok"] is (e["status"] in ("succeeded", "job_submitted"))
    assert isinstance(e["summary"], str) and e["summary"]
    # the same line in English for an English page: never the Chinese one copied
    assert isinstance(e["summary_en"], str) and e["summary_en"] and e["summary_en"] != e["summary"]
    for word in ("完成", "已拒绝", "未找到", "参数有误", "运行出错", "已准予发布", "未准予发布",
                 "任务", "记载", "不等于"):
        assert word not in e["summary_en"], (word, e["summary_en"])
    assert isinstance(e["text"], str) and 0 < len(e["text"]) <= 16_000
    assert set(e["governance"]) >= {"kind", "released", "artifact", "verdict", "claims",
                                    "evidence", "refusals", "labels", "licences", "limitations",
                                    "outputs"}
    assert set(e["receipt"]) >= {"where", "runtime", "device", "versions", "composite_version",
                                 "content_hash", "audit_head", "input_sha256", "output_sha256",
                                 "started_at"}
    assert len(e["receipt"]["input_sha256"]) == 64 and len(e["receipt"]["output_sha256"]) == 64
    r = e["receipt"]
    assert r["schema"] == "tcmstudio.receipt/1" and r["finished_at"] >= r["started_at"]
    assert set(r["execution"]) >= {"runtime", "kernel", "engine", "implementation", "validation",
                                   "device"}
    assert set(r["provenance"]) == {"datasets", "seeds", "models"}
    assert set(r["result"]) == {"kind", "outcome", "assurance", "checks", "artifacts",
                                "fallbacks", "reference"}
    assert r["result"]["outcome"] in ("not_executed", "queued", "running", "succeeded",
                                      "validated", "failed", "cancelled")
    assert r["result"]["assurance"] in ("release_authorized", "outputs_verified", "none")
    assert r["result"]["kind"] in (None, "record", "computation", "prediction", "draft",
                                   "fixture", "system")
    if e["error"]:
        assert e["error"]["type"] in ("bad_arguments", "unavailable", "not_found", "refused",
                                      "runtime_error", "timeout", "network_off")
    for i, c in enumerate(e["citations"], 1):
        assert c["id"] == f"E{i}" and c["kind"] in ("pmid", "doi", "nct", "classical", "source")
    json.dumps(e, ensure_ascii=False, allow_nan=False)


def test_every_core_tool_runs_offline_on_the_runner(runner_ctx, intake, visits, jobs):
    seen = set()
    for tool, args, expected in core_cases(intake, visits):
        e = call(tool, args, runner_ctx)
        check_envelope(e)
        assert e["status"] == expected, (tool, e["status"], e["error"])
        assert e["tool"] == tool and e["receipt"]["where"] == "runner"
        seen.add(tool)
    e = call("job_status", {"job_id": jobs.submitted and "j_0001"}, runner_ctx)
    check_envelope(e)
    assert e["status"] == "succeeded" and e["job"]["state"] == "queued"
    assert "pending" in e["text"]
    seen.add("job_status")
    from tcmstudio.core_tools import CORE_BY_NAME
    assert seen == set(CORE_BY_NAME)


def test_governed_skills_are_released_with_their_lockfile_hash(runner_ctx):
    cases = {"normalize-tcm-entities": ("tcm_normalize", {"names": ["黄芪", "姜"]}),
             "retrieve-tcm-evidence": ("tcm_evidence", {"subject": "黄芪"}),
             "assess-tcm-safety": ("tcm_safety_report", {"subject": "甘草",
                                                         "co_administered": ["甘遂"]}),
             "analyze-tcm-network-pharmacology": ("tcm_network_hypothesis",
                                                  {"formula_name": "桂枝汤"})}
    heads = []
    for sid, (tool, args) in cases.items():
        e = call(tool, args, runner_ctx)
        assert e["status"] == "succeeded", e["error"]
        g = e["governance"]
        assert g["kind"] == "skill" and g["released"] is True
        assert all(g["verdict"]["states"].values()), g["verdict"]["states"]
        assert e["receipt"]["content_hash"] == lock_hash(sid) == LOCK_HASHES[sid]
        assert e["receipt"]["audit_head"] and e["receipt"]["composite_version"].startswith("runtime=")
        assert e["receipt"]["durable"] is True
        assert g["artifact"]["skill_id"] == sid and g["limitations"]
        out = g["outputs"][0]
        assert len(out["sha256"]) == 64 and out["bytes"] > 0 and isinstance(out["content"], dict)
        heads.append(e["receipt"]["audit_head"])
    # One project, one durable chain that every run extends.
    chain = Path(runner_ctx["state_root"]) / "p-test" / "psh" / "events.db"
    assert chain.is_file()
    v = call("call_tool", {"tool": "system.audit_verify"}, runner_ctx)
    assert v["status"] == "succeeded" and v["result"]["intact"] is True
    assert v["result"]["records"] >= 3 * len(cases)
    assert len(set(heads)) == len(heads)          # each run is attested at its own chain head
    assert v["result"]["events"]["bioscience_artifact_attested"] == len(cases)


def test_safety_report_text_restates_limits_and_cites(runner_ctx):
    e = call("tcm_safety_report", {"subject": "甘草", "co_administered": ["甘遂"]}, runner_ctx)
    assert "not evidence of safety" in e["text"]
    assert "Release: released" in e["text"]
    assert "十八反" in e["summary"] and "非临床安全性结论" in e["summary"]
    refs = {c["evidence_ref"] for c in e["citations"]}
    evidence_ids = {item["id"] for item in e["governance"]["evidence"]}
    assert evidence_ids <= refs
    assert any(c["kind"] == "classical" and c["label"] == "《儒门事亲》十八反歌"
               for c in e["citations"])


def test_unknown_subject_is_unknown_not_safe(runner_ctx):
    e = call("tcm_safety_report", {"subject": "不存在的药"}, runner_ctx)
    assert e["status"] == "succeeded"
    assert "unknown" in e["summary"] and "不等于安全" in e["summary"]


def test_network_off_is_a_refusal_with_a_remedy(runner_ctx):
    e = call("connector_call", {"connector": "uniprot", "operation": "entry",
                                "arguments": {"accession": "P04637"}}, runner_ctx)
    assert e["status"] == "failed" and e["error"]["type"] == "network_off"
    assert "web access" in e["error"]["hint"].lower()
    codes = [r["code"] for r in e["governance"]["refusals"]]
    assert "perm.network.denied" in codes
    assert e["via"] == "connector.uniprot.entry"
    lit = call("literature_search", {"query": "astragalus", "source": "pubmed"}, runner_ctx)
    assert lit["error"]["type"] == "network_off"


def test_every_connector_is_refused_offline_before_any_request(runner_ctx):
    from tcmstudio.catalog import entries
    count = 0
    for entry in entries("runner", probe=False):
        if entry["kind"] != "connector" or entry["example"] is None:
            continue
        e = call(entry["id"], entry["example"], runner_ctx)
        assert e["status"] == "failed" and e["error"]["type"] == "network_off", (
            entry["id"], e["error"])
        assert "request" not in e["receipt"]
        count += 1
    assert count >= 400


def test_every_native_example_runs(runner_ctx):
    from tcmstudio.catalog import entries
    for entry in entries("runner", probe=False):
        if entry["kind"] != "native":
            continue
        e = call(entry["id"], entry["example"], runner_ctx)
        assert e["status"] == "succeeded", (entry["id"], e["error"])
        assert e["receipt"]["kernel"] == "bioagent.policy"


def test_clinic_sign_is_never_a_tool(runner_ctx):
    for tool, args in (("clinic.sign", {}), ("clinic_sign", {}),
                       ("call_tool", {"tool": "clinic.sign",
                                      "arguments": {"session_id": "cs_000000000000",
                                                    "practitioner": "x", "licence": "y",
                                                    "decision": "accept"}})):
        e = call(tool, args, runner_ctx)
        assert e["status"] == "refused" and e["error"]["type"] == "refused"
        assert e["governance"]["refusals"][0]["code"] == "HUMAN_ONLY"
        assert "practitioner" in e["error"]["message"]
    assert get_entry("clinic.sign") is None


def test_unknown_tool_suggests_close_names(runner_ctx):
    e = call("tcm_herbs", {"name": "黄芪"}, runner_ctx)
    assert e["status"] == "failed" and e["error"]["type"] == "not_found"
    assert "tcm_herb" in e["error"]["hint"]
    e = call("call_tool", {"tool": "native.egfr_ckd_epi", "arguments": {}}, runner_ctx)
    assert e["error"]["type"] == "not_found" and "native.egfr_ckd_epi_2021" in e["error"]["hint"]


def test_bad_arguments_carry_did_you_mean_hints(runner_ctx):
    e = call("tcm_safety_report", {"query": "甘草"}, runner_ctx)
    assert e["status"] == "failed" and e["error"]["type"] == "bad_arguments"
    assert "Did you mean 'subject'" in e["error"]["hint"]
    e = call("tcm_normalize", {"name": ["黄芪"]}, runner_ctx)
    assert "Did you mean 'names'" in e["error"]["hint"]
    e = call("tcm_evidence", {"subject": "黄芪", "claim_kind": "efficacyy"}, runner_ctx)
    assert e["error"]["type"] == "bad_arguments" and "'efficacy'" in e["error"]["hint"]
    e = call("tcm_compatibility", {"herbs": []}, runner_ctx)   # one name is a formula's check
    assert e["error"]["type"] == "bad_arguments" and "at least 1" in e["error"]["message"]
    e = call("native.egfr_ckd_epi_2021", {"creatinine_mg_dl": 1.0, "age_years": 50,
                                          "sex": "x"}, runner_ctx)
    # The tool's own refusal of a value is reported as a bad argument, with the signature.
    assert e["error"]["type"] == "bad_arguments" and "sex" in e["error"]["message"]


def test_harmless_slips_are_repaired_and_noted(runner_ctx):
    e = call("tcm_normalize", {"names": "黄芪"}, runner_ctx)
    assert e["status"] == "succeeded"
    assert "one-element list" in e["text"]
    assert "1 个名称" in e["summary"]               # not 黄 and 芪 as two names
    e = call("tcm_classical_search", {"query": "桂枝汤", "limit": "2"}, runner_ctx)
    assert e["status"] == "succeeded" and "read as the integer 2" in e["text"]
    e = call("tcm_lookup", {"name": "黄芪", "kind": None}, runner_ctx)
    assert e["status"] == "succeeded"


def test_validate_handles_the_schema_subset():
    schema = {"type": "object", "properties": {
        "a": {"type": "array", "items": {"type": "integer"}, "minItems": 1},
        "b": {"anyOf": [{"type": "number"}, {"type": "null"}]},
        "c": {"type": "string", "enum": ["x", "y"]},
        "d": {"type": "object", "properties": {}, "additionalProperties": {"type": "number"}}},
        "required": ["a"], "additionalProperties": False}
    assert validate(schema, {"a": [1, 2.0], "b": None, "c": "x", "d": {"k": 1}}).ok
    v = validate(schema, {"a": [], "c": "z", "d": {"k": "nope"}, "e": 1})
    assert not v.ok and len(v.problems) == 4
    assert validate(schema, {"a": [True]}).problems


def test_purpose_commercial_is_ruled_on(runner_ctx):
    ctx = {**runner_ctx, "purpose": "commercial"}
    ok = call("tcm_herb", {"name": "黄芪"}, ctx)
    assert ok["status"] == "succeeded"
    assert any(lic["commercial"] for lic in ok["governance"]["licences"])
    denied = call("native.hkbu_formula_lookup", {"formula": "x"}, ctx)
    assert denied["status"] == "refused" and denied["error"]["type"] == "refused"
    assert denied["governance"]["refusals"]
    assert "academic" in denied["error"]["hint"]


def test_browser_mode_refuses_runner_only_entries(browser_ctx):
    for tool, args in (("run_pipeline", {"pipeline": "rnaseq", "arguments": {"samples": "u"}}),
                       ("job_status", {"job_id": "j_1"})):
        e = call(tool, args, browser_ctx)
        assert e["status"] == "failed" and e["error"]["type"] == "unavailable", tool
        assert "local runner" in e["error"]["message"]
        assert e["receipt"]["where"] == "browser"
    e = call("tcm_safety_report", {"subject": "甘草", "co_administered": ["甘遂"]}, browser_ctx)
    assert e["status"] == "succeeded" and e["governance"]["released"] is True
    assert e["receipt"]["durable"] is False


def test_jobs_need_the_job_service(runner_ctx):
    no_jobs = {k: v for k, v in runner_ctx.items() if k != "jobs"}
    e = call("run_pipeline", {"pipeline": "rnaseq", "arguments": {"samples": "u"}}, no_jobs)
    assert e["error"]["type"] == "unavailable" and "job" in e["error"]["message"]
    e = call("job_status", {"job_id": "j_1"}, no_jobs)
    assert e["error"]["type"] == "unavailable"


def test_jobs_are_submitted_with_typed_params(runner_ctx, jobs):
    e = call("run_pipeline", {"pipeline": "scrna", "arguments": {"source": "u_9",
                                                                  "resolution": "0.8"}}, runner_ctx)
    assert e["status"] == "job_submitted" and e["job"]["kind"] == "pipeline.scrna"
    kind, params, project = jobs.submitted[-1]
    assert kind == "pipeline.scrna" and params == {"source": "u_9", "resolution": 0.8}
    assert project == "p-test"
    assert "not a result" in e["text"] or "pending" in e["text"]
    e = call("network_pharmacology_run", {"formula": "葛根芩连汤",
                                          "parameters": {"permutations": 500}}, runner_ctx)
    kind, params, _ = jobs.submitted[-1]
    assert kind == "research.run" and params["formula"] == "葛根芩连汤"
    assert params["permutations"] == 500 and "葛根芩连汤" in params["question"]
    bad = call("run_pipeline", {"pipeline": "fold", "arguments": {"method": "esmatlas"}},
               runner_ctx)
    assert bad["error"]["type"] == "bad_arguments" and "source" in bad["error"]["message"]
    remote = call("run_pipeline", {"pipeline": "fold", "arguments": {
        "source": "MQIFVK", "allow_remote": True}}, runner_ctx)
    assert remote["error"]["type"] == "network_off"      # sending a sequence out needs web access
    online = call("run_pipeline", {"pipeline": "fold", "arguments": {
        "source": "MQIFVK", "allow_remote": True}}, {**runner_ctx, "network": True})
    assert online["status"] == "job_submitted"


def test_candidate_skill_as_a_job(runner_ctx, jobs):
    e = call("call_tool", {"tool": "skill.predict-protein-structure",
                           "arguments": {"sequences": "MQIFVKTLTGK"}}, runner_ctx)
    assert e["status"] == "job_submitted"
    kind, params, _ = jobs.submitted[-1]
    assert kind == "skill.run" and params["skill_id"] == "predict-protein-structure"
    assert params["allow_unpinned"] is True


def test_clinic_assess_writes_a_verifiable_session(runner_ctx, intake):
    e = call("clinic_assess", {"intake": intake}, runner_ctx)
    assert e["status"] == "succeeded"
    r = e["result"]
    assert r["status"] == "draft" and r["prescription"]["formula"] == "四君子汤"
    assert "draft for a licensed" in e["text"]
    assert any("未经执业中医师审核" in x for x in e["governance"]["limitations"])
    paths = {o["path"]: o for o in e["governance"]["outputs"]}
    assert set(paths) == {"session.json", "report.md", "report.html"}
    assert isinstance(paths["session.json"]["content"], dict)
    assert isinstance(paths["report.md"]["content"], str) and paths["report.html"]["content"] is None
    sid = r["session_id"]
    folder = Path(runner_ctx["state_root"]) / "p-test" / "clinic" / sid
    assert (folder / "session.json").is_file()
    v = call("call_tool", {"tool": "clinic.verify", "arguments": {"session_id": sid}}, runner_ctx)
    assert v["status"] == "succeeded" and v["result"]["verified"] is True
    traversal = call("call_tool", {"tool": "clinic.verify",
                                   "arguments": {"session_id": "../../etc"}}, runner_ctx)
    assert traversal["error"]["type"] == "bad_arguments"


def test_red_flags_mean_refer(runner_ctx, red_flag_intake):
    e = call("clinic_assess", {"intake": red_flag_intake}, runner_ctx)
    assert e["status"] == "succeeded"
    assert e["result"]["status"] == "refer" and e["result"]["prescription"] is None
    assert e["summary"].startswith("转诊")


def test_clinic_bad_intake_is_a_bad_argument(runner_ctx):
    e = call("clinic_assess", {"intake": {"patient": {"age": 200}}}, runner_ctx)
    assert e["status"] == "failed" and e["error"]["type"] == "bad_arguments"


def test_an_allergy_that_is_not_text_is_refused_alike_here_and_by_the_library(runner_ctx,
                                                                                 intake):
    # the command line reads an intake with bioagent.clinic.intake: an allergy given as an
    # object was turned into text there (or raised a TypeError for a number) while the studio
    # refused it; both now refuse it with one message
    import pytest
    from bioagent.clinic.intake import IntakeError, parse_intake
    from bioagent.clinic.pack import load_pack
    herbs = [{"herb": "黄芪", "grams": 9}]
    for value in ([{"substance": "北芪", "reaction": "皮疹"}], {"北芪": "皮疹"}, [None], 5, [5],
                  5.5, ["北芪", 3]):
        bad = {**intake, "allergies": value}
        e = call("clinic_check_prescription", {"intake": bad, "herbs": herbs}, runner_ctx)
        assert e["status"] == "failed" and e["error"]["type"] == "bad_arguments"
        with pytest.raises(IntakeError) as exc:
            parse_intake(bad, load_pack())
        assert e["error"]["message"] == f"arguments.intake.{exc.value}", value
    # a blank allergy field records nothing: not asked, by either path
    for blank in ("", [""], ["。。。"]):
        e = call("clinic_check_prescription", {"intake": {**intake, "allergies": blank},
                                               "herbs": herbs}, runner_ctx)
        assert e["status"] == "succeeded" and "过敏史未询问" in e["result"]["intake_warnings"]
    # and an alias in the history's own words stops the herb, through the studio too
    e = call("clinic_check_prescription", {"intake": {**intake, "allergies": ["患者对北芪过敏"]},
                                           "herbs": herbs}, runner_ctx)
    assert e["result"]["stop"] and any(i["id"] == "allergy:黄芪" for i in e["result"]["issues"])
    # a formula of the pack in the history asks about each of its herbs, through the studio too
    e = call("clinic_check_prescription", {"intake": {**intake, "allergies": ["补中益气丸过敏"]},
                                           "herbs": herbs}, runner_ctx)
    assert any(i["id"] == "allergy_formula:黄芪" and i["severity"] == "block"
               for i in e["result"]["issues"]), e["result"]["issues"]


def test_draft_prescription_skill_is_a_development_run(runner_ctx, intake):
    e = call("call_tool", {"tool": "skill.draft-tcm-prescription",
                           "arguments": {"intake": intake}}, runner_ctx)
    assert e["status"] == "succeeded", e["error"]
    g = e["governance"]
    assert g["released"] is False
    assert any(r["code"] == "UNPINNED" for r in g["refusals"])
    assert "draft for a licensed" in e["text"]


def test_data_hub_without_built_data(runner_ctx):
    e = call("tcmdb_relations", {"kind": "herb_ingredient", "subject": "黄芪"}, runner_ctx)
    assert e["status"] == "succeeded" and e["result"]["count"] == 0
    assert any("No dataset is built" in x for x in e["governance"]["limitations"])
    t = call("call_tool", {"tool": "tcmdb.tables", "arguments": {"dataset": "ddid"}}, runner_ctx)
    assert t["status"] == "failed" and t["error"]["type"] == "unavailable"
    assert "fetch" in t["error"]["hint"]
    k = call("call_tool", {"tool": "tcmdb.relations", "arguments": {"kind": "no_such_kind"}},
             runner_ctx)
    assert k["error"]["type"] == "bad_arguments"
    cards = call("tcmdb_catalog", {"access": "live_api"}, runner_ctx)
    assert cards["status"] == "succeeded" and cards["result"]["count"] > 0
    assert all("local" in c for c in cards["result"]["cards"] if c.get("dataset"))


def test_study_designs(runner_ctx):
    contrast = get_entry("study.contrast_formula_monomer")["example"]
    e = call("study.contrast_formula_monomer", contrast, runner_ctx)
    assert e["status"] == "succeeded" and e["result"]["verdict"] in (
        "formula_exceeds", "monomer_exceeds", "equivalent", "inconclusive")
    assert e["result"]["may_not_conclude"][0] in e["governance"]["limitations"]
    cells = [{"dose_a": a, "dose_b": b, "effect": [eff, eff + 0.01, eff - 0.01]}
             for (a, b, eff) in ((1, 0, 0.1), (3, 0, 0.25), (0, 1, 0.12), (0, 3, 0.3),
                                 (1, 1, 0.2), (1, 3, 0.35), (3, 1, 0.33), (3, 3, 0.5))]
    e = call("study.combination", {"cells": cells, "primary": "bliss", "boot": 50}, runner_ctx)
    assert e["status"] == "succeeded", e["error"]
    e = call("study.effect_modification", {
        "outcome": [1, 2, 3, 4, 2, 3, 5, 6], "treated": [0, 0, 0, 0, 1, 1, 1, 1],
        "modifiers": [{"name": "age", "values": [30, 40, 50, 60, 30, 40, 50, 60],
                       "baseline": True}]}, runner_ctx)
    assert e["status"] == "succeeded", e["error"]
    e = call("study.exposure_screen", {
        "exposures": [{"molecule": "berberine", "species": "human", "site": "plasma",
                       "time_h": 2, "concentration": {"qualifier": "measured", "value": 0.4,
                                                      "unit": "ng/mL"}}],
        "assays": [{"molecule": "berberine", "target": "AMPK", "endpoint": "EC50",
                    "result": {"qualifier": "measured", "value": 5, "unit": "uM"}}],
        "site": "plasma", "mw": {"berberine": 336.4}}, runner_ctx)
    assert e["status"] == "succeeded", e["error"]
    assert e["result"]["pairs"][0]["verdict"]
    bad = call("study.combination", {"cells": cells[:4], "primary": "bliss"}, runner_ctx)
    assert bad["error"]["type"] == "bad_arguments"


def test_system_operations(runner_ctx):
    caps = call("capabilities_status", {}, runner_ctx)
    r = caps["result"]
    assert r["where"] == "runner" and r["network"] == {"enabled": False,
                                                        "profile": "offline-analysis"}
    assert r["counts"]["runnable_here"] < r["counts"]["entries"]
    assert r["jobs"] is True and r["audit_chain"]["durable"] is True
    phi = call("call_tool", {"tool": "system.classify",
                             "arguments": {"text": "患者张三，身份证 110101199003071234，手机 13800138000"}},
               runner_ctx)
    assert phi["status"] == "succeeded" and phi["result"]["sensitivity"] in ("PHI", "SECRET")
    assert phi["result"]["permits"]["PUBLIC_REMOTE"] is False
    skills = call("call_tool", {"tool": "system.skills"}, runner_ctx)
    assert len(skills["result"]["skills"]) >= 11
    empty = call("call_tool", {"tool": "system.audit_verify"}, {**runner_ctx, "project_id": "new"})
    assert empty["status"] == "succeeded" and empty["result"]["records"] == 0


def test_call_never_raises_on_garbage():
    class Weird:
        def __repr__(self):
            raise RuntimeError("no repr")
    cases = [(None, None), (123, {}), ("tcm_herb", [1, 2]), ("tcm_herb", "{not json"),
             ("tcm_herb", '{"name": "黄芪"}'), ("tcm_herb", {"name": Weird()}),
             ("call_tool", {"tool": "call_tool"}), ("call_tool", {"tool": 5}),
             ("tcm_herb", {"name": float("nan")}), (b"tcm_herb", {"name": "黄芪"}),
             ("tcm_herb", {"name": "黄芪"})]
    for tool, args in cases:
        for ctx in (None, "garbage", {"where": "mars", "purpose": "evil", "network": "yes"},
                    Context(where="browser")):
            e = call(tool, args, ctx)
            check_envelope(e)
    ok = call("tcm_herb", '{"name": "黄芪"}')
    assert ok["status"] == "succeeded"


def test_input_hash_is_stable_across_key_order(runner_ctx):
    a = call("tcm_applicability", {"subject": "桂枝汤", "object": "太阳中风证",
                                   "claim_kind": "attribution"}, runner_ctx)
    b = call("tcm_applicability", {"claim_kind": "attribution", "object": "太阳中风证",
                                   "subject": "桂枝汤"}, runner_ctx)
    assert a["receipt"]["input_sha256"] == b["receipt"]["input_sha256"]
    assert a["receipt"]["output_sha256"] == b["receipt"]["output_sha256"]


def test_call_json_for_the_browser_worker(browser_ctx):
    text = call_json("tcm_compatibility", '{"herbs": ["甘草", "甘遂"]}', json.dumps(browser_ctx))
    e = json.loads(text)
    check_envelope(e)
    assert e["status"] == "succeeded" and e["receipt"]["where"] == "browser"
    bad = json.loads(call_json("tcm_herb", "{oops", "{also oops"))
    assert bad["error"]["type"] == "bad_arguments" and "context ignored" in bad["text"]


# ------------------------------------------------------- what the summaries may say (§7)

def test_no_record_is_never_shown_as_a_clean_bill(runner_ctx):
    e = call("tcm_safety_report", {"subject": "黄芪"}, runner_ctx)
    assert e["result"]["outputs"]["safety.json"]["status"] == "no_record"
    assert "无记录（不等于安全）" in e["summary"] and "未见配伍禁忌记录" not in e["summary"]
    assert "which is not safety" in e["summary_en"]
    # the case's own limitation leads, so the compact card's first two show it
    assert "`no_record`" in e["governance"]["limitations"][0]
    e = call("tcm_herb", {"name": "黄芪"}, runner_ctx)
    assert "安全性：无记录（不等于安全）" in e["summary"] and "0 条" not in e["summary"]
    e = call("tcm_evidence", {"subject": "葛根芩连汤"}, runner_ctx)
    assert "无记录（不等于无证据）" in e["summary"] and "0 条" not in e["summary"]
    assert "not absence of evidence" in e["summary_en"]


def test_an_unresolved_formula_is_an_empty_result_not_a_null_network(runner_ctx):
    e = call("tcm_network_hypothesis", {"formula_name": "葛根芩连汤"}, runner_ctx)
    assert e["result"]["outputs"]["network.json"]["formula"] is None
    assert "组成 0 味" not in e["summary"] and "空结果，不是阴性发现" in e["summary"]
    assert "6 首方剂" in e["summary"] and "not a null finding" in e["summary_en"]
    assert "did not resolve" in e["governance"]["limitations"][0]
    e = call("tcm_network_hypothesis", {"formula_name": "桂枝汤"}, runner_ctx)
    assert "组成 5 味" in e["summary"] and "未构建靶点网络，无主张" in e["summary"]
    assert "实测边" not in e["summary"]
    assert "no target network was built" in e["governance"]["limitations"][0]


def test_applicability_names_the_licensing_set_and_says_when_nothing_is_recorded(runner_ctx):
    e = call("tcm_applicability", {"subject": "桂枝汤", "object": "太阳中风证",
                                   "claim_kind": "mechanism_hypothesis"}, runner_ctx)
    assert "需要计算预测或临床前研究证据" in e["summary"]
    assert "computational prediction or preclinical" in e["summary_en"]
    e = call("tcm_applicability", {"subject": "黄芪", "object": "太阳中风证",
                                   "claim_kind": "efficacy"}, runner_ctx)
    assert e["result"]["relations"] == []
    assert "无 黄芪→太阳中风证 的记载（不等于无证据）" in e["summary"]
    assert "现有记载" not in e["summary"]
    assert "随机对照试验或系统评价" in e["summary"]


def test_a_syndrome_named_as_the_classics_write_it_is_the_seeds_syndrome(runner_ctx):
    """伤寒论 第12条 writes 太阳中风 for the syndrome the seed records as 太阳中风证: the name
    with its closing 证 is read, and the answer says so. A name that is only part of one stays
    a suggestion."""
    e = call("tcm_syndrome", {"name": "太阳中风"}, runner_ctx)
    assert e["status"] == "succeeded", e["error"]
    assert e["result"]["syndrome"]["chinese"] == "太阳中风证"
    assert [f["chinese"] for f in e["result"]["formulas"]] == ["桂枝汤"]
    assert e["result"]["read_as"] == {"name": {"given": "太阳中风", "read": "太阳中风证",
                                               "as": "太阳中风证"}}
    assert any(x.startswith("「太阳中风」 was read as 太阳中风证")
               for x in e["governance"]["limitations"])
    assert call("tcm_syndrome", {"name": "太阳伤寒"}, runner_ctx)["result"]["syndrome"][
        "chinese"] == "太阳伤寒证"
    # its recorded alias with 证 names it too, and the answer names the alias read
    alias = call("tcm_syndrome", {"name": "表虚"}, runner_ctx)
    assert alias["result"]["syndrome"]["chinese"] == "太阳中风证"
    assert any("read as 表虚证" in x and "records as 太阳中风证" in x
               for x in alias["governance"]["limitations"])
    a = call("tcm_applicability", {"subject": "桂枝汤", "object": "太阳中风",
                                   "claim_kind": "attribution"}, runner_ctx)
    b = call("tcm_applicability", {"subject": "桂枝汤", "object": "太阳中风证",
                                   "claim_kind": "attribution"}, runner_ctx)
    assert a["status"] == "succeeded" and a["result"]["object"]["chinese"] == "太阳中风证"
    assert a["result"]["verdict"] == b["result"]["verdict"] == "within_scope"
    assert set(a["result"]["read_as"]) == {"object"} and "read_as" not in b["result"]
    # a partial match is no name: 气虚, part of 气虚证 and of 脾胃气虚证, is read as 气虚证
    qi = call("tcm_syndrome", {"name": "气虚"}, runner_ctx)
    assert qi["result"]["read_as"] == {"name": {"given": "气虚", "read": "气虚证", "as": "气虚证"}}
    # a name that names something, or only part of one and nothing with 证, is left as given
    assert call("tcm_syndrome", {"name": "中风"}, runner_ctx)["error"]["type"] == "not_found"
    assert call("tcm_syndrome", {"name": "太阳"}, runner_ctx)["status"] == "failed"
    assert "read_as" not in call("tcm_syndrome", {"name": "脾胃气虚证"}, runner_ctx)["result"]


def test_significance_wording_follows_the_p_value(runner_ctx):
    e = call("call_tool", {"tool": "native.fisher_exact",
                           "arguments": {"a": 20, "b": 1, "c": 1, "d": 20}}, runner_ctx)
    assert "显著 ≠ 有效或因果" in e["summary"] and "不显著" not in e["summary"]
    assert "significant ≠ effective or causal" in e["summary_en"]
    assert e["governance"]["limitations"][0].startswith("Significant ≠ effective or causal")
    e = call("call_tool", {"tool": "native.fisher_exact",
                           "arguments": {"a": 3, "b": 2, "c": 2, "d": 3}}, runner_ctx)
    assert "不显著 ≠ 无关" in e["summary"]
    assert e["governance"]["limitations"][0].startswith("Not significant ≠ irrelevant")


def test_a_name_missing_from_the_seed_corpus_is_not_found_not_a_bad_argument(runner_ctx):
    for tool, args in (("tcm_formula", {"name": "不存在的测试方剂_12345"}),
                       ("tcm_applicability", {"subject": "黄芪", "object": "不存在证",
                                              "claim_kind": "traditional_use"})):
        e = call(tool, args, runner_ctx)
        assert e["status"] == "failed" and e["error"]["type"] == "not_found", e["error"]
        assert "参数有误" not in e["summary"] and "不等于不存在" in e["summary"]
        assert "种子语料" in e["summary"] and "Seed corpus" in e["summary_en"]
        assert "Absence here is not absence" in e["error"]["hint"]


def test_summaries_name_the_knowledge_base_a_composition_comes_from(runner_ctx, intake):
    seed = call("tcm_formula", {"name": "四君子汤"}, runner_ctx)
    assert seed["summary"].startswith("完整方剂库：四君子汤")
    assert seed["result"]["annotation_scope"]
    assert seed["result"]["repository_records"]["total"] > 1
    assert seed["summary_en"].startswith("Complete formula workbook: ")
    clinic = call("clinic_assess", {"intake": intake}, runner_ctx)
    assert "临床知识包 " in clinic["summary"] and "13 首方剂" in clinic["summary"]
    assert "未经审核" in clinic["summary"] and "clinic pack" in clinic["summary_en"]
    check = call("clinic_check_prescription", {"intake": intake, "herbs": [
        {"herb": "甘草", "grams": 6}]}, runner_ctx)
    assert "临床知识包 " in check["summary"]


def test_no_tool_text_promises_what_studio_does_not_do():
    from tcmstudio import governance as gvn
    from tcmstudio.catalog import NEVER_OFFERED, build_catalog
    from tcmstudio.core_tools import CORE_BY_NAME
    texts = [*NEVER_OFFERED.values(), gvn.remedy_for("HUMAN_ONLY"),
             *(t["description"] for t in CORE_BY_NAME.values()),
             *(e["summary"] for e in build_catalog("runner", probe=False)["entries"])]
    for text in texts:
        # there is no clinic or data view in Studio, and what the model writes has left the
        # machine for the model service the user selected
        assert "clinic view" not in text and "data view" not in text, text
        assert "stays on this machine" not in text and "nothing leaves" not in text, text
    assert "bioagent clinic sign" in NEVER_OFFERED["clinic.sign"]
    assert "--confirm" in NEVER_OFFERED["tcmdb.fetch_confirm"]
    assert "model service the user selected" in CORE_BY_NAME["clinic_assess"]["description"]


def test_english_summaries_cover_failures_and_refusals(runner_ctx, browser_ctx):
    e = call("clinic.sign", {}, runner_ctx)
    assert e["summary_en"].startswith("Refused: only a person")
    e = call("run_pipeline", {"pipeline": "rnaseq", "arguments": {"samples": "x"}}, browser_ctx)
    assert e["status"] == "failed" and "needs the local runner" in e["summary_en"]
    e = call("tcm_herb", {}, runner_ctx)
    assert e["error"]["type"] == "bad_arguments" and "bad arguments" in e["summary_en"]
    env = json.loads(call_json("tcm_compatibility", '{"herbs": ["甘草", "甘遂"]}',
                               json.dumps({"where": "browser"})))
    assert env["summary_en"].startswith("Seed corpus · 甘草 + 甘遂: 1 recorded incompatibility")


def test_job_submissions_say_predicted_only_for_predictions(runner_ctx, jobs):
    e = call("tcm_safety_report", {"subject": "甘草"}, runner_ctx)     # not a job
    assert e["status"] == "succeeded"
    pending = "A job that has not succeeded is pending work, not a result."
    for args, predicted in (({"skill_id": "assess-tcm-safety", "arguments": {"subject": "甘草"}},
                             False),
                            ({"skill_id": "analyze-tcm-network-pharmacology",
                              "arguments": {"formula_name": "桂枝汤"}}, True)):
        e = call("call_tool", {"tool": "job.skill.run", "arguments": args}, runner_ctx)
        assert e["status"] == "job_submitted"
        lim = e["governance"]["limitations"]
        assert lim[0] == pending and (any("Predicted ≠ measured" in x for x in lim) is predicted)
    from tcmstudio.dispatch import _predictive_job
    assert _predictive_job("pipeline.dock", {}) and _predictive_job("pipeline.fold", {})
    assert not _predictive_job("pipeline.rnaseq", {}) and not _predictive_job("tcmdb.fetch", {})


def test_a_skill_job_whose_envelope_cannot_be_read_is_not_shown_as_released(runner_ctx, jobs):
    e = call("call_tool", {"tool": "job.skill.run", "arguments": {
        "skill_id": "assess-tcm-safety", "arguments": {"subject": "甘草"}}}, runner_ctx)
    job_id = e["job"]["id"]
    jobs.jobs[job_id].update(state="succeeded", artefacts=[{"name": "envelope"}],
                             result={"released": True})
    st = call("job_status", {"job_id": job_id}, runner_ctx)     # FakeJobs has no envelope()
    g = st["governance"]
    assert g["kind"] == "skill" and g["released"] is False
    assert "could not be read back" in g["limitations"][0]
    assert any("not evidence of safety" in x for x in g["limitations"])
    assert "按未准予发布处理" in st["summary"] and "已完成并核验" not in st["summary"]
    assert "not released" in st["summary_en"]
    # a job that is not a governed skill: no release verdict, and its state is not 'release'
    jobs.jobs["j_dock"] = {"id": "j_dock", "kind": "pipeline.dock", "state": "succeeded",
                           "params": {}, "artefacts": [{"name": "scores"}]}
    st = call("job_status", {"job_id": "j_dock"}, runner_ctx)
    assert st["governance"]["released"] is None
    assert "输出哈希已核验" in st["summary"] and "output hashes verified" in st["summary_en"]
    assert any("Predicted ≠ measured" in x for x in st["governance"]["limitations"])


def test_a_process_leaves_no_temporary_directory_behind(tmp_path):
    """The skill view and the non-durable project state live in temporary directories that
    go when the process ends (each runner start and CLI call used to leave both behind)."""
    import os
    import subprocess
    import sys
    tmp = tmp_path / "tmp"
    tmp.mkdir()
    env = {**os.environ, "TMPDIR": str(tmp), "TEMP": str(tmp), "TMP": str(tmp)}
    proc = subprocess.run([sys.executable, "-m", "tcmstudio", "call", "tcm_safety_report",
                           "--where", "runner", "--args",
                           json.dumps({"subject": "甘草", "co_administered": ["甘遂"]})],
                          capture_output=True, text=True, env=env, timeout=300)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["status"] == "succeeded"
    assert [p.name for p in tmp.iterdir() if p.name.startswith("tcmstudio-")] == []


def test_a_name_differing_only_in_separators_runs_as_the_id_it_means(runner_ctx):
    # function-calling APIs allow no dots in a tool's name: models write tcmdb_status
    e = call("call_tool", {"tool": "tcmdb_status", "arguments": {}}, runner_ctx)
    assert e["status"] == "succeeded", e["error"]
    assert e["via"] == "tcmdb.status"
    assert "'tcmdb_status' is written 'tcmdb.status'" in e["text"]
    herb = call("TCM.Herb", {"name": "黄芪"}, runner_ctx)
    assert herb["status"] == "succeeded" and "is written 'tcm_herb'" in herb["text"]
    # a misspelling is not a separator: still unknown, with the did-you-mean hint
    typo = call("tcm_herbs", {"name": "黄芪"}, runner_ctx)
    assert typo["error"]["type"] == "not_found" and "tcm_herb" in typo["error"]["hint"]
    from tcmstudio.catalog import canonical_name
    assert canonical_name("connector_europepmc_search") == "connector.europepmc.search"
    assert canonical_name("no.such.tool") is None


def test_literature_search_reaches_registered_trials(runner_ctx, monkeypatch):
    from tcmstudio import dispatch
    from tcmstudio.dispatch import Outcome
    seen = {}

    def fake(entry_id, args, ctx):
        seen.update(entry=entry_id, args=args)
        return Outcome(result={"studies": [{"protocolSection": {
            "identificationModule": {"nctId": "NCT01234567", "briefTitle": "Guizhi decoction for palpitations"},
            "statusModule": {"overallStatus": "COMPLETED"}, "designModule": {"phases": ["PHASE2"]},
            "conditionsModule": {"conditions": ["Palpitations", "Arrhythmia"]}}}]})
    monkeypatch.setattr(dispatch, "_run_connector_entry", fake)
    e = call("literature_search", {"query": "guizhi decoction", "source": "clinicaltrials", "limit": 5},
             {**runner_ctx, "network": True})
    assert e["status"] == "succeeded", e["error"]
    assert seen == {"entry": "connector.clinicaltrials.search", "args": {"term": "guizhi decoction", "page_size": 5}}
    assert e["result"]["hits"] == [{"title": "Guizhi decoction for palpitations", "nct": "NCT01234567",
                                    "status": "COMPLETED", "phase": "PHASE2",
                                    "conditions": "Palpitations, Arrhythmia"}]
    assert "ClinicalTrials.gov" in e["summary_en"]
    assert e["governance"]["limitations"][0].startswith("A registered trial is a record")


def test_a_formula_the_research_loop_would_refuse_is_refused_before_a_job(runner_ctx):
    # 桂枝汤 names 30 compositions in the workbook: the job used to be queued and fail
    e = call("network_pharmacology_run", {"formula": "桂枝汤"}, runner_ctx)
    assert e["status"] == "failed" and e["error"]["type"] == "bad_arguments", e["error"]
    assert "30 different compositions" in e["error"]["message"]
    assert "桂枝汤《" in e["error"]["message"] and "source book" in e["error"]["message"]
    assert runner_ctx["jobs"].submitted == [], "no job was queued"
    from tcmstudio.kinds import formula_ambiguity
    assert formula_ambiguity("葛根芩连汤") is None, "a hand-checked formula"
    assert formula_ambiguity("桂枝汤《伤寒论》") is None, "a name with its book"
    assert formula_ambiguity("桂枝甘草汤") is None, "one composition"


def test_capabilities_count_by_the_one_readiness(runner_ctx):
    from tcmstudio import capability
    r = call("capabilities_status", {}, runner_ctx)["result"]
    by = r["counts"]["by_readiness"]
    assert set(by) <= set(capability.READINESS)
    assert sum(by.values()) == r["counts"]["entries"]
    assert by.get("ready", 0) == r["counts"]["runnable_here"] == sum(r["counts"]["by_kind"].values())
    assert by.get("network_unavailable", 0) >= 444          # web access is off in this context
    assert by.get("needs_dataset", 0) >= 3                  # nothing is built in this tcmdb root


def test_the_capability_audit_op_and_catalog_search_agree(runner_ctx):
    audit = call("call_tool", {"tool": "system.capability_audit", "arguments": {"limit": 1000}},
                 runner_ctx)["result"]
    state = {row["id"]: row for row in audit["entries"]}
    found = call("catalog_search", {"query": "十八反", "limit": 25}, runner_ctx)["result"]["matched"]
    assert found
    for m in found:
        assert m["runnable_now"] is (state[m["id"]]["readiness"] == "ready")
        assert m["needs"] == [q["detail"] for q in state[m["id"]]["requires"]]


# ------------------------------------------------------------------------- receipt/1

def test_validation_record_hashes_the_executed_arguments(runner_ctx):
    from tcmstudio.envelope import sha256_json
    a = call("call_tool", {"tool": "native.gc_content",
                           "arguments": {"sequence": "ACGTACGT", "window": "3"}}, runner_ctx)
    b = call("call_tool", {"arguments": {"window": 3, "sequence": "ACGTACGT"},
                           "tool": "native.gc_content"}, runner_ctx)
    va, vb = a["receipt"]["execution"]["validation"], b["receipt"]["execution"]["validation"]
    assert va["ok"] is True and va["schema"] == "entry"
    assert va["repairs"] == ['arguments.window: "3" was read as the integer 3'] and vb["repairs"] == []
    # the arguments the tool ran on, canonical: equal across key orders and the repair
    assert va["normalized_sha256"] == vb["normalized_sha256"] == sha256_json(
        {"sequence": "ACGTACGT", "window": 3})
    assert a["receipt"]["input_sha256"] != b["receipt"]["input_sha256"]     # the raw call differs
    core = call("tcm_classical_search", {"query": "桂枝汤", "limit": "2"}, runner_ctx)
    assert any("read as the integer 2" in x for x in core["receipt"]["execution"]["validation"]["repairs"])
    bad = call("call_tool", {"tool": "native.gc_content", "arguments": {"sequense": "ACGT"}},
               runner_ctx)["receipt"]["execution"]["validation"]
    assert bad["ok"] is False and bad["schema"] == "entry" and "normalized_sha256" not in bad
    assert any("sequense" in p for p in bad["problems"])
    core_bad = call("tcm_safety_report", {"query": "甘草"}, runner_ctx)["receipt"]["execution"]
    assert core_bad["validation"]["schema"] == "core" and core_bad["validation"]["ok"] is False
    assert core_bad["implementation"] is None, "nothing ran"
    garbage = call("tcm_herb", "{not json", runner_ctx)["receipt"]["execution"]["validation"]
    assert garbage["ok"] is False and "not valid JSON" in garbage["problems"][0]


def test_patient_data_never_reaches_the_receipt(runner_ctx, intake):
    marker = "乏力纳差两月"
    e = call("clinic_assess", {"intake": intake}, runner_ctx)
    assert e["status"] == "succeeded"
    r = e["receipt"]
    assert marker not in json.dumps(r, ensure_ascii=False)
    assert r["provenance"]["seeds"] == {} and len(r["execution"]["validation"]["normalized_sha256"]) == 64
    slip = {**intake, "patient": {**intake["patient"], "age": "40"}}
    v = call("clinic_assess", {"intake": slip}, runner_ctx)["receipt"]["execution"]["validation"]
    assert v["ok"] is True and v["repairs"] == [
        "arguments.intake.patient.age: (detail not recorded: patient data)"]
    wrong = {**intake, "patient": {**intake["patient"], "age": 900}}
    w = call("clinic_assess", {"intake": wrong}, runner_ctx)["receipt"]
    assert w["execution"]["validation"]["ok"] is False
    assert "900" not in json.dumps(w["execution"], ensure_ascii=False)


def test_implementation_is_named(runner_ctx):
    native = call("call_tool", {"tool": "native.gc_content", "arguments": {"sequence": "ACGT"}},
                  runner_ctx)["receipt"]["execution"]["implementation"]
    assert native["ref"] == native["entrypoint"] == "bioagent.tools.sequence:gc_content"
    assert native["component"] == "native.tool.gc_content" and native["deterministic"] is True
    assert native["version"] and native["manifest_version"]
    for sid, (tool, args) in {"assess-tcm-safety": ("tcm_safety_report", {"subject": "甘草"}),
                              "analyze-tcm-network-pharmacology": ("tcm_network_hypothesis",
                                                                   {"formula_name": "桂枝汤"})}.items():
        e = call(tool, args, runner_ctx)
        impl = e["receipt"]["execution"]["implementation"]
        spec = get_entry(f"skill.{sid}")["skill"]
        assert impl["content_hash"] == LOCK_HASHES[sid] == e["receipt"]["content_hash"]
        assert impl["version"] == spec["version"] and impl["pinned"] is True
        assert impl["ref"] == get_entry(f"skill.{sid}")["implementation"]["ref"]
    conn = call("connector_call", {"connector": "uniprot", "operation": "entry",
                                   "arguments": {"accession": "P04637"}}, runner_ctx)
    assert conn["receipt"]["execution"]["implementation"] is None, "refused offline: nothing ran"
    assert conn["receipt"]["result"]["outcome"] == "not_executed"


def test_result_kinds(runner_ctx, intake):
    cases = [
        ("tcm_herb", {"name": "黄芪"}, "record", "succeeded", "none"),
        ("call_tool", {"tool": "native.reverse_complement", "arguments": {"sequence": "ACGT"}},
         "computation", "succeeded", "none"),
        ("call_tool", {"tool": "native.egfr_ckd_epi_2021", "arguments": {
            "creatinine_mg_dl": 1.0, "age_years": 50, "sex": "female"}},
         "computation", "succeeded", "none"),
        ("tcm_safety_report", {"subject": "甘草", "co_administered": ["甘遂"]},
         "record", "validated", "release_authorized"),
        ("tcm_network_hypothesis", {"formula_name": "桂枝汤"},
         "prediction", "validated", "release_authorized"),
        ("clinic_assess", {"intake": intake}, "draft", "succeeded", "none"),
        ("tcm_safety_report", {"query": "甘草"}, None, "not_executed", "none"),   # wrong arguments
        ("clinic_sign", {}, None, "not_executed", "none"),
        ("catalog_search", {"query": "十八反"}, "system", "succeeded", "none"),
        ("tcmdb_relations", {"kind": "herb_ingredient", "subject": "黄芪"}, "record",
         "succeeded", "none"),
        ("run_pipeline", {"pipeline": "rnaseq", "arguments": {"samples": "u"}}, None, "queued",
         "none"),
    ]
    for tool, args, kind, outcome, assurance in cases:
        e = call(tool, args, runner_ctx)
        got = e["receipt"]["result"]
        assert (got["kind"], got["outcome"], got["assurance"]) == (kind, outcome, assurance), (
            tool, got, e["error"])
    # release_authorized is the kernel's six states, never a claim that the result is true
    safety = call("tcm_safety_report", {"subject": "甘草", "co_administered": ["甘遂"]},
                  runner_ctx)["receipt"]["result"]
    assert [c["id"] for c in safety["checks"]] == [
        "schema_valid", "evidence_verified", "outputs_verified", "execution_declared",
        "execution_attested", "release_authorized", "skill_pinned", "outputs_present"]
    assert all(c["ok"] for c in safety["checks"])
    assert safety["artifacts"] == {"expected": ["safety.json"], "missing": [], "hash_mismatch": []}
    draft = call("call_tool", {"tool": "skill.draft-tcm-prescription",
                               "arguments": {"intake": intake}}, runner_ctx)["receipt"]["result"]
    assert (draft["kind"], draft["assurance"]) == ("draft", "none"), "an unpinned run is not released"
    assert {"id": "skill_pinned", "ok": False} in draft["checks"]


def test_only_a_call_an_executor_ran_can_fail():
    """Arguments that fail validation ran nothing (not_executed, as the page's own envelopes
    say); bad_arguments from an executor that ran is a failure of that run."""
    from tcmstudio import dispatch
    entry = get_entry("native.gc_content")
    o = dispatch._failure("native", "bad_arguments", "sequence: a string is needed")
    o.validation = {"ok": False, "schema": "entry", "repairs": [], "problems": ["sequence"]}
    assert dispatch._outcome_of(o, entry, None) == "not_executed"
    o.validation = {"ok": True, "schema": "entry", "repairs": [], "normalized_sha256": "0" * 64}
    assert dispatch._outcome_of(o, entry, None) == "failed"
    assert dispatch._outcome_of(dispatch._failure("native", "runtime_error", "boom"), entry, None) == "failed"


def test_a_fixture_is_only_what_a_source_declares(runner_ctx):
    from tcmstudio import dispatch
    o = dispatch.Outcome(result={"x": 1}, governance=dispatch.gv.empty("job"))
    planted = [{"id": "npass", "version": "demo+planted"}]
    assert dispatch._result_kind(None, "system.job_status", "system", o.governance,
                                 {"kind": "research.run", "params": {}}, planted) == "fixture"
    seed = [{"id": "tcmscience.tcm.seed", "version": "0.2.6"}]
    assert dispatch._result_kind(get_entry("native.tcm_herb"), "native.tcm_herb", "native",
                                 o.governance, None, seed) == "record"


def test_receipt_datasets(runner_ctx, tmp_path):
    safety = call("tcm_safety_report", {"subject": "甘草", "co_administered": ["甘遂"]}, runner_ctx)
    sources = safety["governance"]["artifact"]["sources"]
    listed = {d["id"]: d for d in safety["receipt"]["provenance"]["datasets"]}
    for s in sources:
        assert listed[s["id"]]["sha256"] == s["snapshot_hash"] and listed[s["id"]]["version"] == s["version"]
    formula = call("tcm_formula", {"name": "桂枝汤"}, runner_ctx)
    books = [d for d in formula["receipt"]["provenance"]["datasets"] if d["id"] == "repository_formulas"]
    assert books and books[0]["sha256"] == formula["result"]["repository_records"]["provenance"]["source_sha256"]
    assert books[0]["release"] is None, "the workbook states no release"
    seed = next(d for d in formula["receipt"]["provenance"]["datasets"] if d["id"] == "tcmscience.tcm.seed")
    assert seed["version"] and len(seed["sha256"]) == 64
    # a built store's rows name the datasets they came from, with the dataset's version
    import base64
    import sqlite3
    from bioagent.tcmdb.rowkit import COLUMNS
    path = tmp_path / "batman2.sqlite"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE _tcmdb_files (tbl TEXT, rows INTEGER, license TEXT, sha256 TEXT)")
        conn.execute("INSERT INTO _tcmdb_files VALUES ('relations', 1, 'CC BY-NC 4.0', 'test')")
        conn.execute("CREATE TABLE relations (" + ",".join(f'"{c}" TEXT' for c in COLUMNS) + ")")
        row = {c: "" for c in COLUMNS}
        row.update(kind="herb_ingredient", source="batman2", subject_type="herb",
                   subject_name="黄芪", object_type="ingredient", object_name="astragaloside IV",
                   evidence="curated", license="CC BY-NC 4.0")
        conn.execute(f"INSERT INTO relations VALUES ({','.join('?' * len(COLUMNS))})",
                     [row[c] for c in COLUMNS])
    browser = {"where": "browser", "state_root": str(tmp_path / "persist"), "project_id": "p-hub",
               "durable": False, "network": False}
    imported = call("tcmdb.import_store", {"dataset": "batman2",
                                           "content_base64": base64.b64encode(path.read_bytes()).decode()},
                    browser)
    assert imported["status"] == "succeeded", imported["error"]
    rel = call("tcmdb_relations", {"kind": "herb_ingredient", "subject": "黄芪"}, browser)
    assert rel["status"] == "succeeded" and rel["result"]["count"] == 1, rel["error"]
    hub = {d["id"]: d for d in rel["receipt"]["provenance"]["datasets"]}
    assert hub["batman2"]["version"] == hub["batman2"]["release"] == "2.0"
    # a refused live call fetched nothing, so it names no source
    off = call("connector_call", {"connector": "uniprot", "operation": "entry",
                                  "arguments": {"accession": "P04637"}}, runner_ctx)
    assert off["receipt"]["provenance"]["datasets"] == []


def test_seeds_are_recorded_with_the_default_the_code_uses(runner_ctx, jobs):
    given = call("run_pipeline", {"pipeline": "scrna", "arguments": {"source": "u_9", "seed": 7}},
                 runner_ctx)
    default = call("run_pipeline", {"pipeline": "scrna", "arguments": {"source": "u_9"}},
                   runner_ctx)
    assert given["status"] == default["status"] == "job_submitted"
    assert given["receipt"]["provenance"]["seeds"] == {"seed": 7}
    assert default["receipt"]["provenance"]["seeds"] == {"seed": 0}      # kinds.py's default too
    cells = [{"dose_a": a, "dose_b": b, "effect": [eff, eff + 0.01, eff - 0.01]}
             for (a, b, eff) in ((1, 0, 0.1), (3, 0, 0.25), (0, 1, 0.12), (0, 3, 0.3),
                                 (1, 1, 0.2), (1, 3, 0.35), (3, 1, 0.33), (3, 3, 0.5))]
    s = call("study.combination", {"cells": cells, "primary": "bliss", "boot": 20}, runner_ctx)
    assert s["receipt"]["provenance"]["seeds"] == {"seed": 0}


def test_a_queued_job_has_no_device_yet_and_a_gpu_job_is_never_called_cpu(runner_ctx, jobs):
    e = call("run_pipeline", {"pipeline": "scrna", "arguments": {"source": "u_9"}}, runner_ctx)
    dev = e["receipt"]["execution"]["device"]
    assert e["receipt"]["result"]["outcome"] == "queued"
    assert dev["assigned"] is None and dev["used"] is None and dev["evidence"] is None
    job_id = e["job"]["id"]
    jobs.jobs[job_id].update(state="running", device="cuda:0")
    st = call("job_status", {"job_id": job_id}, runner_ctx)
    assert st["receipt"]["device"] == "cuda:0", "the poll names the job's device, not the CPU it ran on"
    dev = st["receipt"]["execution"]["device"]
    assert dev["assigned"] == "cuda:0" and dev["evidence"] == "selected" and dev["used"] is None
    assert st["receipt"]["result"]["outcome"] == "running" and st["receipt"]["result"]["kind"] is None
    jobs.jobs[job_id].update(state="succeeded", device="cpu",
                             device_note="the selected device cuda:1 is not available here; "
                                         "the job runs on the CPU")
    st = call("job_status", {"job_id": job_id}, runner_ctx)
    r = st["receipt"]
    assert r["execution"]["device"]["used"] == "cpu"
    assert r["result"]["fallbacks"] == [{"what": "device",
                                         "reason": jobs.jobs[job_id]["device_note"]}]
    # no artefact was digested: a succeeded job without outputs is not outputs_verified
    assert r["result"]["outcome"] == "succeeded" and r["result"]["assurance"] == "none"


def test_a_queued_gpu_job_names_no_device_in_its_legacy_field_either(runner_ctx, jobs):
    # the dispatcher's own context is the CPU; a job not yet started has no device at all
    runner_ctx["capabilities"] = {"device_setting": "cuda:0"}
    # (any job: the device comes when it starts; scrna is one this runner can queue without optional engines)
    e = call("run_pipeline", {"pipeline": "scrna", "arguments": {"source": "u_9"}}, runner_ctx)
    assert e["status"] == "job_submitted", e["error"]
    assert e["receipt"]["device"] is None, "never the CPU the dispatcher runs on"
    st = call("job_status", {"job_id": e["job"]["id"]}, runner_ctx)
    assert st["receipt"]["device"] is None and st["receipt"]["execution"]["device"]["assigned"] is None
    jobs.jobs[e["job"]["id"]].update(state="running", device="cuda:0")
    assert call("job_status", {"job_id": e["job"]["id"]}, runner_ctx)["receipt"]["device"] == "cuda:0"


def test_a_sample_sheet_s_files_each_reach_the_receipt_s_provenance(runner_ctx, jobs):
    e = call("run_pipeline", {"pipeline": "rnaseq", "arguments": {"samples": "u_sheet"}}, runner_ctx)
    job_id = e["job"]["id"]
    fq = {f"fastq_{n}:{line}": {"upload": f"u_fq{n}{line}", "name": f"s{line}_R{n}.fq.gz",
                                "sha256": f"{n}{line}" * 32, "bytes": 1000 + line}
          for n in (1, 2) for line in (2, 3)}
    jobs.jobs[job_id].update(inputs={"samples": {"upload": "u_sheet", "name": "sheet.csv",
                                                 "sha256": "ab" * 32, "bytes": 90},
                                     "samples.files": fq})
    st = call("job_status", {"job_id": job_id}, runner_ctx)
    got = {d["id"]: d for d in st["receipt"]["provenance"]["datasets"] if d.get("role") == "input"}
    assert got["u_sheet"]["sha256"] == "ab" * 32
    for key, rec in fq.items():
        d = got[rec["upload"]]
        assert (d["sha256"], d["bytes"], d["param"]) == (rec["sha256"], rec["bytes"], f"samples.files.{key}")
    assert "samples.files" not in got, "the mapping itself is not an input"


def test_a_degraded_call_records_its_fallback(runner_ctx, monkeypatch):
    from types import SimpleNamespace
    from tcmstudio import dispatch
    res = SimpleNamespace(status=SimpleNamespace(value="DEGRADED"), value={"x": 1}, metadata={},
                          adapter="python-backend", error="cache only")
    o = dispatch._call_result(res, get_entry("native.gc_content"), Context(network=False),
                              kind="native", licences=[], limits=[])
    assert o.result_info["fallbacks"] == [{"what": "degraded", "reason": "cache only"}]


def test_capabilities_say_remote_compute_is_not_configured(runner_ctx, browser_ctx):
    for ctx in (runner_ctx, browser_ctx):
        r = call("capabilities_status", {}, ctx)["result"]
        assert r["remote_compute"] == {
            "configured": False,
            "reason": "no remote compute backend is registered; calls run in this browser or on "
                      "the paired runner"}


def test_remote_compute_reads_as_the_pages_answer():
    """The page's capabilities answer (core/catalog.js REMOTE_COMPUTE) says the same; it adds
    only a pointer to runner.loopback, a field of the page's own answer that Python's lacks
    (a runner answering cannot tell whether the page reached it over loopback)."""
    import re
    from tcmstudio.dispatch import _REMOTE_COMPUTE
    js = (Path(__file__).resolve().parents[2] / "web" / "js" / "core" / "catalog.js").read_text(encoding="utf-8")
    found = re.search(r"REMOTE_COMPUTE = Object\.freeze\(\{\s*configured: false,\s*reason: \"([^\"]+)\"", js)
    assert found, "core/catalog.js REMOTE_COMPUTE"
    page, ours = found.group(1), _REMOTE_COMPUTE["reason"]
    assert page == ours or (page.startswith(ours + " (") and "runner.loopback" in page[len(ours):]), page


def test_receipt_code_never_touches_the_result(runner_ctx, monkeypatch):
    """result, governance (its outputs too), text and output_sha256 are what they are without
    receipt/1; and a failure in the receipt code leaves the defaults, never a lost envelope."""
    from tcmstudio import dispatch
    args = ("tcm_safety_report", {"subject": "甘草", "co_administered": ["甘遂"]})
    native = ("call_tool", {"tool": "native.distance_matrix",
                            "arguments": {"sequences": ["ACGT", "AGGT", "GGTT"]}})
    with_receipt = [call(*a, runner_ctx) for a in (args, native)]

    def broken(*_a, **_k):
        raise RuntimeError("receipt code broke")
    monkeypatch.setattr(dispatch, "_receipt_parts", broken)
    without = [call(*a, runner_ctx) for a in (args, native)]
    for a, b in zip(with_receipt, without):
        assert a["status"] == b["status"] == "succeeded"
        assert b["receipt"]["schema"] == "tcmstudio.receipt/1"
        assert b["receipt"]["result"]["kind"] is None and b["receipt"]["result"]["outcome"] == "succeeded"
    # the governed run's outputs are reproducible, and the same with or without receipt/1
    assert [o["sha256"] for o in with_receipt[0]["governance"]["outputs"]] == [
        o["sha256"] for o in without[0]["governance"]["outputs"]]
    # a deterministic native: the same bytes either way
    assert with_receipt[1]["result"] == without[1]["result"]
    assert with_receipt[1]["receipt"]["output_sha256"] == without[1]["receipt"]["output_sha256"]
    assert with_receipt[1]["text"] == without[1]["text"]
    assert with_receipt[1]["governance"] == without[1]["governance"]
