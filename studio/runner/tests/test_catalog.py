"""The catalog document (CONTRACTS §2): shape, schemas, counts, browser mode, search."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from tcmstudio.catalog import (CATEGORIES, JOB_KINDS, build_catalog, catalog_search, entries,
                               get_entry, skill_specs)
from tcmstudio.core_tools import CORE_TOOLS
from tcmstudio.dispatch import validate

CONTRACT_CORE = {
    # name: (maps_to, exec, confirm, job, network)
    "tcm_lookup": ("native.tcm_lookup", ["browser", "runner"], False, False, False),
    "tcm_herb": ("native.tcm_herb", ["browser", "runner"], False, False, False),
    "tcm_formula": ("native.tcm_formula", ["browser", "runner"], False, False, False),
    "tcm_syndrome": ("native.tcm_syndrome", ["browser", "runner"], False, False, False),
    "tcm_classical_search": ("native.tcm_classical_search", ["browser", "runner"], False, False, False),
    "tcm_compatibility": ("native.tcm_compatibility", ["browser", "runner"], False, False, False),
    "tcm_applicability": ("native.tcm_applicability", ["browser", "runner"], False, False, False),
    "tcm_normalize": ("skill.normalize-tcm-entities", ["browser", "runner"], False, False, False),
    "tcm_evidence": ("skill.retrieve-tcm-evidence", ["browser", "runner"], False, False, False),
    "tcm_safety_report": ("skill.assess-tcm-safety", ["browser", "runner"], False, False, False),
    "tcm_network_hypothesis": ("skill.analyze-tcm-network-pharmacology", ["browser", "runner"],
                               False, False, False),
    "clinic_assess": ("clinic.assess", ["browser", "runner"], False, False, False),
    "clinic_check_prescription": ("clinic.check", ["browser", "runner"], False, False, False),
    "clinic_followup": ("clinic.followup", ["browser", "runner"], False, False, False),
    "tcmdb_catalog": ("tcmdb.catalog", ["browser", "runner"], False, False, False),
    "tcmdb_relations": ("tcmdb.relations", ["runner"], False, False, False),
    "tcmdb_consensus": ("tcmdb.consensus", ["runner"], False, False, False),
    "connector_call": ("connector.*", ["runner"], False, False, True),
    "literature_search": ("connector.europepmc.search", ["runner"], False, False, True),
    "network_pharmacology_run": ("job.research.run", ["runner"], True, True, False),
    "run_pipeline": ("job.pipeline.*", ["runner"], True, True, False),
    "job_status": ("system.job_status", ["runner"], False, False, False),
    "capabilities_status": ("system.capabilities", ["browser", "runner"], False, False, False),
    "catalog_search": ("system.catalog_search", ["browser", "runner"], False, False, False),
    "call_tool": (None, ["browser", "runner"], False, False, False),
}
TYPES = {"string", "integer", "number", "boolean", "array", "object", "null"}


@pytest.fixture(scope="module")
def runner_catalog():
    return build_catalog("runner", probe=True)


@pytest.fixture(scope="module")
def browser_catalog():
    return build_catalog("browser", probe=True)


def walk_schema(schema, path, problems):
    """Every array has items, every object has properties, every type is a JSON type."""
    if not isinstance(schema, dict):
        problems.append(f"{path}: not an object")
        return
    t = schema.get("type")
    for one in (t if isinstance(t, list) else [t] if t else []):
        if one not in TYPES:
            problems.append(f"{path}: unknown type {one!r}")
    if t == "array" or (isinstance(t, list) and "array" in t):
        if "items" not in schema:
            problems.append(f"{path}: array without items")
        else:
            walk_schema(schema["items"], f"{path}[]", problems)
    if t == "object" or (isinstance(t, list) and "object" in t):
        if not isinstance(schema.get("properties"), dict):
            problems.append(f"{path}: object without properties")
        for k, v in (schema.get("properties") or {}).items():
            walk_schema(v, f"{path}.{k}", problems)
        extra = schema.get("additionalProperties")
        if isinstance(extra, dict):
            walk_schema(extra, f"{path}.*", problems)
        for r in schema.get("required") or ():
            if r not in (schema.get("properties") or {}):
                problems.append(f"{path}: required {r!r} is not a property")
    for key in ("anyOf", "oneOf"):
        for i, sub in enumerate(schema.get(key) or ()):
            walk_schema(sub, f"{path}<{key}{i}>", problems)


def test_document_shape(runner_catalog):
    doc = runner_catalog
    assert doc["schema"] == "tcmstudio.catalog/1"
    assert set(doc["versions"]) >= {"tcmstudio", "bioagent", "psh"}
    assert doc["versions"]["tcmstudio"] == "0.1.0"
    assert "problems" not in doc, doc.get("problems")
    json.dumps(doc, ensure_ascii=False, allow_nan=False)


def test_thirteen_categories_in_order_with_counts(runner_catalog):
    cats = runner_catalog["categories"]
    assert [c["id"] for c in cats] == [c[0] for c in CATEGORIES]
    assert len(cats) == 13
    assert cats[0] == {"id": "tcm_knowledge", "zh": "中医知识", "en": "TCM Knowledge",
                       "count": cats[0]["count"]}
    by_cat = {}
    for e in runner_catalog["entries"]:
        by_cat[e["category"]] = by_cat.get(e["category"], 0) + 1
    assert {c["id"]: c["count"] for c in cats} == {c[0]: by_cat.get(c[0], 0) for c in CATEGORIES}
    assert all(c["count"] > 0 for c in cats)


def test_counts(runner_catalog):
    kinds = runner_catalog["counts"]["by_kind"]
    assert kinds["native"] == 150
    assert kinds["connector"] >= 400
    assert kinds["skill"] >= 11
    assert kinds["clinic"] >= 4 and kinds["tcmdb"] >= 8 and kinds["study"] >= 4
    assert kinds["job"] == 9 and kinds["system"] >= 5
    assert runner_catalog["counts"]["entries"] == len(runner_catalog["entries"])


def test_core_tools_match_the_contract(runner_catalog):
    core = {c["name"]: c for c in runner_catalog["core"]}
    assert list(core) == list(CONTRACT_CORE)
    for name, (maps_to, exec_, confirm, job, network) in CONTRACT_CORE.items():
        c = core[name]
        assert re.fullmatch(r"[a-z0-9_]{1,64}", name)
        assert c["maps_to"] == maps_to, name
        assert c["exec"] == exec_, name
        assert (c["confirm"], c["job"], c["network"]) == (confirm, job, network), name
        assert set(c["title"]) == {"zh", "en"} and all(c["title"].values())
        assert 40 <= len(c["description"]) <= 1000, name
        assert c["category"] in [x[0] for x in CATEGORIES]
        problems = []
        walk_schema(c["parameters"], name, problems)
        assert not problems, problems
        if maps_to and "*" not in maps_to:
            assert get_entry(maps_to) is not None, maps_to


def test_core_descriptions_state_limits():
    text = {c["name"]: c["description"] for c in CORE_TOOLS}
    assert "not evidence of safety" in text["tcm_safety_report"]
    assert "never a statement that the combination is safe" in text["tcm_compatibility"]
    assert "predicted ≠ measured" in text["tcm_network_hypothesis"]
    assert "draft" in text["clinic_assess"] and "licensed" in text["clinic_assess"]
    assert "attribution" in text["tcm_classical_search"]


def test_entries_are_well_formed(runner_catalog):
    ids = [e["id"] for e in runner_catalog["entries"]]
    assert len(ids) == len(set(ids)), "entry ids must be unique"
    problems = []
    for e in runner_catalog["entries"]:
        assert e["kind"] in ("native", "skill", "connector", "clinic", "tcmdb", "study", "job",
                             "system")
        assert e["id"].startswith(e["kind"] + ".")
        assert set(e["title"]) == {"zh", "en"} and e["title"]["zh"] and e["title"]["en"]
        assert e["summary"], e["id"]
        assert e["exec"] and set(e["exec"]) <= {"browser", "runner"}, e["id"]
        for flag in ("network", "confirm", "job", "gpu"):
            assert isinstance(e[flag], bool), (e["id"], flag)
        assert isinstance(e["heavy"], list) and isinstance(e["tags"], list) and e["tags"]
        walk_schema(e["parameters"], e["id"], problems)
    assert not problems, problems[:20]


def test_examples_satisfy_their_schemas(runner_catalog):
    bad = []
    for e in runner_catalog["entries"]:
        if e.get("example") is not None:
            v = validate(e["parameters"], e["example"])
            if not v.ok:
                bad.append((e["id"], v.problems))
    assert not bad, bad[:10]


def test_native_summary_is_the_full_first_paragraph():
    e = get_entry("native.tcm_compatibility")
    # NativeTool.description stops at the first line ("… for recorded"); the summary does not.
    assert e["summary"].endswith("and unresolved names.")
    assert "十八反" in e["summary"]
    assert e["parameters"]["properties"]["herbs"] == {
        "type": "array", "items": {"type": "string"}, "examples": [["甘草", "甘遂"]]}
    egfr = get_entry("native.egfr_ckd_epi_2021")
    assert egfr["parameters"]["required"] == ["creatinine_mg_dl", "age_years", "sex"]
    assert egfr["parameters"]["properties"]["age_years"]["type"] == "number"
    assert egfr["title"]["zh"].startswith("估算肾小球滤过率")


def test_tags_carry_chinese_search_terms():
    tags = get_entry("native.tcm_compatibility")["tags"]
    assert {"十八反", "配伍禁忌", "十九畏"} <= set(tags)
    assert "性味归经" in get_entry("native.tcm_herb")["tags"]
    assert "生存分析" in get_entry("native.kaplan_meier")["tags"]


def test_skills_from_manifests_and_signatures():
    specs = {s["id"]: s for s in skill_specs()}
    assert {"normalize-tcm-entities", "retrieve-tcm-evidence", "assess-tcm-safety",
            "analyze-tcm-network-pharmacology"} <= set(specs)
    assert len(specs) >= 11
    lock_hash = {"assess-tcm-safety": "e525e3abd5011488dd01b5a2720b8256e7c75766d49383287fe214271b173312"}
    safety = get_entry("skill.assess-tcm-safety")
    # The function takes subject / co_administered / population, not the manifest's 'query'.
    assert safety["parameters"]["required"] == ["subject"]
    assert set(safety["parameters"]["properties"]) == {"subject", "co_administered", "population"}
    assert safety["skill"]["pinned"] is True
    assert safety["skill"]["content_hash"] == lock_hash["assess-tcm-safety"]
    assert safety["skill"]["claim_kinds"] and safety["skill"]["max_evidence_tier"]
    assert get_entry("skill.normalize-tcm-entities")["parameters"]["required"] == ["names"]
    assert get_entry("skill.analyze-tcm-network-pharmacology")["parameters"]["required"] == ["formula_name"]
    for sid in ("dock-ligands", "predict-admet", "scrna-cell-atlas"):
        e = get_entry(f"skill.{sid}")
        assert e["skill"]["pinned"] is False and e["job"] and e["confirm"]
        assert e["exec"] == ["runner"]
    draft = get_entry("skill.draft-tcm-prescription")
    assert draft["parameters"]["properties"]["intake"]["type"] == "object"
    assert draft["confirm"] is True and not draft["job"]


def test_connector_schemas_from_templates():
    e = get_entry("connector.uniprot.entry")
    assert e["parameters"]["required"] == ["accession"]
    assert e["network"] and e["exec"] == ["runner"] and e["hosts"] == ["rest.uniprot.org"]
    search = get_entry("connector.europepmc.search")
    props = search["parameters"]["properties"]
    assert props["page_size"]["type"] == "integer" and props["page_size"]["default"] == 5
    assert search["category"] == "literature"
    assert get_entry("connector.dcabm_tcm.herb_blood")["category"] == "tcm_data"


def test_human_acts_are_not_entries(runner_catalog):
    ids = {e["id"] for e in runner_catalog["entries"]}
    assert "clinic.sign" not in ids and get_entry("clinic.sign") is None
    assert not any(i.rsplit(".", 1)[-1] == "sign" for i in ids if i.startswith("clinic."))
    assert not any(i.startswith("registry.") for i in ids)
    assert {"clinic.assess", "clinic.check", "clinic.followup", "clinic.template"} <= ids
    fetch = get_entry("job.tcmdb.fetch")
    assert "confirm" not in fetch["parameters"]["properties"]


def test_job_kinds_are_the_runner_kinds():
    assert set(JOB_KINDS) == {"pipeline.rnaseq", "pipeline.scrna", "pipeline.fold",
                              "pipeline.dock", "pipeline.admet", "research.run", "skill.run",
                              "tcmdb.fetch", "tcmdb.build"}
    for kind in JOB_KINDS:
        e = get_entry(f"job.{kind}")
        assert e["job"] and e["confirm"] and e["exec"] == ["runner"]


def test_browser_catalog(browser_catalog, runner_catalog):
    assert browser_catalog["where"] == "browser"
    by_id = {e["id"]: e for e in browser_catalog["entries"]}
    assert set(by_id) == {e["id"] for e in runner_catalog["entries"]}
    for e in by_id.values():
        assert "available" not in e, "the build machine is not the user's browser"
        if e["kind"] in ("connector", "job"):
            assert "browser" not in e["exec"], e["id"]
    for runner_only in ("tcmdb.relations", "tcmdb.consensus", "system.job_status",
                        "native.hkbu_formula_lookup", "skill.dock-ligands"):
        assert "browser" not in by_id[runner_only]["exec"]
    for both in ("native.reverse_complement", "skill.assess-tcm-safety", "clinic.assess",
                 "tcmdb.catalog", "study.exposure_screen"):
        assert by_id[both]["exec"] == ["browser", "runner"]


def test_runner_probe_marks_missing_dependencies(runner_catalog):
    import importlib.util
    dock = next(e for e in runner_catalog["entries"] if e["id"] == "job.pipeline.dock")
    have_rdkit = importlib.util.find_spec("rdkit") is not None
    assert dock["available"] is (have_rdkit and all(
        importlib.util.find_spec(m) is not None for m in ("meeko", "vina", "gemmi")))
    if not dock["available"]:
        assert dock["missing"]
    native = next(e for e in runner_catalog["entries"] if e["id"] == "native.bmi")
    assert native["available"] is True
    unprobed = entries("runner", probe=False)
    assert all("available" not in e for e in unprobed)


@pytest.mark.parametrize("query,expected", [
    ("十八反", {"native.tcm_compatibility", "skill.assess-tcm-safety"}),
    ("配伍禁忌", {"native.tcm_compatibility"}),
    ("eGFR", {"native.egfr_ckd_epi_2021"}),
    ("生存分析", {"native.kaplan_meier"}),
    ("uniprot", {"connector.uniprot.entry"}),
    ("样本量", {"native.sample_size_two_means", "native.sample_size_two_proportions"}),
    ("甘草和甘遂能一起用吗", {"native.tcm_compatibility"}),
    ("性味归经", {"native.tcm_herb"}),
])
def test_search_ranks_the_obvious_entry_first(query, expected):
    r = catalog_search(query, limit=5)
    assert r["matched"], query
    assert r["matched"][0]["id"] in expected, (query, [m["id"] for m in r["matched"]])
    assert set(r["categories"]) == {c[0] for c in CATEGORIES}


def test_search_filters():
    r = catalog_search("search", kind="connector", limit=50)
    assert len(r["matched"]) == 25                      # the limit is capped at 25
    assert all(m["kind"] == "connector" for m in r["matched"])
    r = catalog_search("", category="clinical_calc", limit=25)
    assert r["matched"] and all(m["category"] == "clinical_calc" for m in r["matched"])
    browser = catalog_search("uniprot", runnable_now=True, where="browser")
    assert all("browser" in m["exec"] for m in browser["matched"])
    offline = catalog_search("uniprot", where="runner")
    assert offline["matched"][0]["runnable_now"] is False
    assert "web access for this project" in offline["matched"][0]["needs"]
    online = catalog_search("uniprot", where="runner", network=True)
    assert online["matched"][0]["runnable_now"] is True
    assert catalog_search("zzzz-no-such-thing")["matched"] == []


def test_catalog_cli_writes_the_document(tmp_path):
    out = tmp_path / "catalog.json"
    proc = subprocess.run([sys.executable, "-m", "tcmstudio", "catalog", "--where", "browser",
                           "--no-probe", "--out", str(out)], capture_output=True, text=True,
                          timeout=120)
    assert proc.returncode == 0, proc.stderr
    doc = json.loads(Path(out).read_text(encoding="utf-8"))
    assert doc["where"] == "browser" and len(doc["core"]) == 25
    assert len(doc["entries"]) == doc["counts"]["entries"]
