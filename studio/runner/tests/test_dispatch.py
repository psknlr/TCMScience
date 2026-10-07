"""The dispatcher: every core tool offline, governed runs, refusals, both runtimes."""

from __future__ import annotations

import json
from pathlib import Path

from tcmstudio.catalog import get_entry
from tcmstudio.dispatch import Context, call, call_json, validate

LOCK_HASHES = {
    "normalize-tcm-entities": "a5de563ed8d6a76dfcc023b7c8f7d4f0fd78b6126ab33e008328d6615b8297b6",
    "retrieve-tcm-evidence": "f844cf695a60a4dd1ac876d24677031e2e5efdbcf60933314e798b0d25b222ab",
    "assess-tcm-safety": "e525e3abd5011488dd01b5a2720b8256e7c75766d49383287fe214271b173312",
    "analyze-tcm-network-pharmacology": "6a8a71ad7c52b64809572ee66abec5e0e780d5d70ab16a18de92c5fddbddb0e6",
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
        ("call_tool", {"tool": "native.egfr_ckd_epi_2021",
                       "arguments": {"creatinine_mg_dl": 1.0, "age_years": 50, "sex": "female"}},
         "succeeded"),
    ]


def check_envelope(e):
    assert set(e) == {"ok", "tool", "via", "status", "duration_ms", "summary", "text", "result",
                      "citations", "governance", "receipt", "job", "approval", "error"}
    assert e["status"] in ("succeeded", "failed", "refused", "needs_approval", "job_submitted",
                           "cancelled")
    assert e["ok"] is (e["status"] in ("succeeded", "job_submitted"))
    assert isinstance(e["summary"], str) and e["summary"]
    assert isinstance(e["text"], str) and 0 < len(e["text"]) <= 16_000
    assert set(e["governance"]) >= {"kind", "released", "artifact", "verdict", "claims",
                                    "evidence", "refusals", "labels", "licences", "limitations",
                                    "outputs"}
    assert set(e["receipt"]) >= {"where", "runtime", "device", "versions", "composite_version",
                                 "content_hash", "audit_head", "input_sha256", "output_sha256",
                                 "started_at"}
    assert len(e["receipt"]["input_sha256"]) == 64 and len(e["receipt"]["output_sha256"]) == 64
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
    e = call("tcm_compatibility", {"herbs": ["甘草"]}, runner_ctx)
    assert e["error"]["type"] == "bad_arguments" and "at least 2" in e["error"]["message"]
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
    for tool, args in (("tcmdb_relations", {"kind": "herb_ingredient"}),
                       ("connector_call", {"connector": "uniprot", "operation": "entry",
                                           "arguments": {"accession": "P04637"}}),
                       ("run_pipeline", {"pipeline": "rnaseq", "arguments": {"samples": "u"}}),
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
