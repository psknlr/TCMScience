"""The catalog document (CONTRACTS §2): shape, schemas, counts, browser mode, search."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from tcmstudio.catalog import (BROWSER_JOB_KINDS, CATEGORIES, JOB_KINDS, build_catalog, catalog_search, entries,
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
    "tcmdb_relations": ("tcmdb.relations", ["browser", "runner"], False, False, False),
    "tcmdb_consensus": ("tcmdb.consensus", ["browser", "runner"], False, False, False),
    "connector_call": ("connector.*", ["browser", "runner"], False, False, True),
    "literature_search": ("connector.europepmc.search", ["browser", "runner"], False, False, True),
    "network_pharmacology_run": ("job.research.run", ["browser", "runner"], True, True, False),
    "run_pipeline": ("job.pipeline.*", ["browser", "runner"], True, True, False),
    "job_status": ("system.job_status", ["browser", "runner"], False, False, False),
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
    # system.capability_audit made the system entries ten (and the catalog 650), job.admet.models the
    # jobs twelve (651): counted on purpose
    assert kinds["job"] == 12 and kinds["system"] == 10
    assert runner_catalog["counts"]["entries"] == len(runner_catalog["entries"]) == 651


def test_the_capability_audit_entry(runner_catalog):
    e = get_entry("system.capability_audit")
    assert e["kind"] == "system" and e["category"] == "system" and e["exec"] == ["browser", "runner"]
    assert e["title"] == {"zh": "能力审计", "en": "Capability audit"}
    assert "THIS runtime" in e["summary"] and "never claims network reachability" in e["summary"]
    props = e["parameters"]["properties"]
    from tcmstudio.capability import READINESS
    assert props["state"]["enum"] == list(READINESS) and props["limit"]["maximum"] == 1000
    assert not e["network"] and not e["confirm"] and not e["job"]
    assert {"能力", "就绪"} <= set(e["tags"])
    assert catalog_search("能力审计", limit=3)["matched"][0]["id"] == "system.capability_audit"


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


def test_the_seed_skill_is_titled_as_the_limited_analysis_it_is(runner_catalog):
    """The pinned seed-corpus skill builds no measured-target network (its corpus records no
    target relation): its entry and its core tool say so in both languages, and the core
    tool points to the research job that does build one."""
    entry = get_entry("skill.analyze-tcm-network-pharmacology")
    assert entry["title"] == {"zh": "有限种子语料网络假说分析",
                              "en": "Limited seed-corpus network hypothesis analysis"}
    listed = next(e for e in runner_catalog["entries"]
                  if e["id"] == "skill.analyze-tcm-network-pharmacology")
    assert listed["title"] == entry["title"]
    core = next(c for c in CORE_TOOLS if c["name"] == "tcm_network_hypothesis")
    assert core["title"] == {"zh": "有限种子语料网络假说（受治理）",
                             "en": "Limited seed-corpus network hypothesis (governed)"}
    assert "limited seed-corpus network hypothesis" in core["description"]
    assert "network_pharmacology_run" in core["description"]


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
    lock_hash = {"assess-tcm-safety": "5a12df2abe348b70547efb3529853cc6cb40c0495bce4eb5db9c732cf306a87e"}
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
    assert e["network"] and e["exec"] == ["browser", "runner"] and e["hosts"] == ["rest.uniprot.org"]
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
    from tcmstudio.kinds import KINDS
    assert set(JOB_KINDS) == set(KINDS) == {
        "pipeline.rnaseq", "pipeline.scrna", "pipeline.fold", "pipeline.dock", "pipeline.admet",
        "admet.models", "research.run", "research.snapshot", "env.provision", "skill.run",
        "tcmdb.fetch", "tcmdb.build"}
    # the browser's job service runs exactly these (studio/web/js/scientific/pipelines)
    assert BROWSER_JOB_KINDS == ("research.snapshot", "research.run", "pipeline.rnaseq", "pipeline.scrna",
                                 "admet.models", "pipeline.admet", "pipeline.dock")
    admet = ["numpy", "scipy", "scikit-learn"]
    packages = {"research.snapshot": ["pyarrow"], "research.run": ["pyarrow"],
                "pipeline.rnaseq": ["numpy", "scipy"], "pipeline.scrna": ["numpy", "scipy"],
                "admet.models": admet, "pipeline.admet": admet, "pipeline.dock": ["numpy"]}
    for kind in JOB_KINDS:
        e = get_entry(f"job.{kind}")
        assert e["job"] and e["confirm"]
        if kind in BROWSER_JOB_KINDS:
            assert e["exec"] == ["browser", "runner"], kind
            browser_job = e["execution"]["browser_job"]
            # the Python stages run in Pyodide; an engine that is not Python is named in engines (docking's Vina)
            assert browser_job["runtime"] == "pyodide" and browser_job["pyodide_packages"] == packages[kind]
            assert browser_job["service"] == "studio-jobs/1"
            assert browser_job["storage"] == "origin private file system"
        else:
            assert e["exec"] == ["runner"], kind
            assert "browser_job" not in (e.get("execution") or {}), kind
    # each its own dict: the pipelines say what they read and with which engines, and what needs the runner
    rnaseq = get_entry("job.pipeline.rnaseq")["execution"]
    assert rnaseq["browser_job"]["engines"] == ["builtin"] and "FASTQ needs the runner" in rnaseq["browser_job"]["inputs"]
    assert [r["arg"] for r in rnaseq["browser_job"]["runner_only"]] == ["counts", "de_backend"]
    assert {e["id"] for e in rnaseq["engines"]} >= {"builtin-de", "pydeseq2"}   # the runner's engines stay
    scrna = get_entry("job.pipeline.scrna")["execution"]["browser_job"]
    assert scrna["engines"] == ["builtin", "builtin-harmony"]
    assert "10x HDF5, .h5ad and directories need the runner" in scrna["inputs"]
    assert [r["arg"] for r in scrna["runner_only"]] == ["analysis_backend", "integration", "source"]
    assert "runner_only" not in get_entry("job.research.run")["execution"]["browser_job"]


def test_the_admet_jobs_name_the_browsers_chemistry_and_what_needs_the_runner():
    """The ADMET jobs run in the browser with RDKit.js for the chemistry (Pyodide has no RDKit)
    and scikit-learn for the models; the pin is the one rdkit.js holds, and the one the runner's
    environment pins for rdkit."""
    import json
    from pathlib import Path

    from tcmstudio import capability, webbuild
    from tcmstudio.catalog import RDKIT_JS
    pin = webbuild.rdkit_pin()
    assert (RDKIT_JS["version"], RDKIT_JS["release"]) == (pin["version"], pin["release"])
    envs = json.loads((Path(__file__).resolve().parents[3] / "BioScience-Harness" / "registry" /
                       "scientific_environments.json").read_text())
    assert envs["groups"]["admet"]["packages"]["rdkit"] == RDKIT_JS["version"]
    for kind in ("job.admet.models", "job.pipeline.admet"):
        job = get_entry(kind)["execution"]["browser_job"]
        assert job["chemistry"] == {"engine": "rdkit.js", "package": "@rdkit/rdkit",
                                    "version": "2026.9.1", "release": "2026.09.1"}
        assert job["engines"] == ["scikit-learn HistGradientBoosting"]
    models = get_entry("job.admet.models")
    assert "runner_only" not in models["execution"]["browser_job"]
    assert models["network"] is True and models["hosts"] == ["dataverse.harvard.edu"]
    # it downloads the archive unless it is given a copy of it: then web access is not needed
    assert models["network_unless"] == "archive"
    offline = {"where": "runner", "network": False}
    assert [c for c, _ in capability.requirements(models, args={}, **offline)] == ["network_off"]
    assert [c for c, _ in capability.requirements(models, **offline)] == ["network_off"]
    assert capability.requirements(models, args={"archive": "u_0000000000000001"}, **offline) == []
    assert set(models["parameters"]["properties"]) == {"endpoints", "archive", "max_iter", "seed", "rebuild"}
    assert "nothing trained is shipped" in models["summary"].lower()
    admet = get_entry("job.pipeline.admet")
    assert [r["arg"] for r in admet["execution"]["browser_job"]["runner_only"]] == ["molecules"]
    from tcmstudio.browser_jobs import ADMET_MOLECULES_MAX
    assert admet["execution"]["browser_job"]["limits"] == {"molecules": ADMET_MOLECULES_MAX}
    for name in ("ligands.sdf", "ligands.SDF.GZ", "one.mol"):
        why = capability.runner_only(admet, {"molecules": name})
        assert len(why) == 1 and "SDMolSupplier" in why[0], name
    for value in ("molecules.csv", "set.smi", "m.tsv", "CC(=O)Oc1ccccc1C(=O)O"):
        assert capability.runner_only(admet, {"molecules": value}) == [], value
    assert "job.admet.models" in admet["summary"]


def test_the_docking_job_names_its_webassembly_engine_and_runs_prepared_inputs_only():
    """pipeline.dock runs in the browser on prepared inputs (PDBQT), each Vina run in AutoDock Vina 1.2.7
    compiled to WebAssembly; the pin is the build manifest's, boot.json's and the runner's environment's.
    Everything that needs preparing (a PDB receptor, SMILES/CSV/SDF ligands, a site_ligand, a fetched
    structure) takes the call to the runner."""
    import json
    from pathlib import Path

    from tcmstudio import browser_jobs, capability, webbuild
    from tcmstudio.catalog import VINA_WASM

    root = Path(__file__).resolve().parents[3]
    build = json.loads((root / "studio" / "web" / "js" / "scientific" / "dock" / "vina_build.json").read_text())
    wasm = build["outputs"]["vendor/vina-1.2.7.wasm"]
    assert VINA_WASM == {"version": build["engine"]["version"], "wasm": "js/scientific/dock/vendor/vina-1.2.7.wasm",
                         "sha256": wasm["sha256"], "bytes": wasm["bytes"]}
    assert webbuild.vina_pin() == {"version": VINA_WASM["version"],
                                   "loader": "js/scientific/dock/vendor/vina-1.2.7.mjs",
                                   "url": VINA_WASM["wasm"], "sha256": VINA_WASM["sha256"],
                                   "bytes": VINA_WASM["bytes"]}
    envs = json.loads((root / "BioScience-Harness" / "registry" / "scientific_environments.json").read_text())
    assert envs["groups"]["docking"]["packages"]["vina"] == VINA_WASM["version"] == "1.2.7"
    dock = get_entry("job.pipeline.dock")
    job = dock["execution"]["browser_job"]
    assert dock["exec"] == ["browser", "runner"] and job["runtime"] == "pyodide"
    assert job["engines"] == {"vina": {"engine": "AutoDock Vina", "version": "1.2.7",
                                       "build": "WebAssembly (studio/scripts/build_vina_wasm.py)",
                                       "wasm_sha256": VINA_WASM["sha256"]}}
    assert job["limits"] == {"ligands": browser_jobs.DOCK_LIGANDS_MAX,
                             "receptor_atoms": browser_jobs.DOCK_RECEPTOR_ATOMS_MAX,
                             "ligand_atoms": browser_jobs.DOCK_LIGAND_ATOMS_MAX,
                             "box_volume": browser_jobs.DOCK_BOX_VOLUME_MAX}
    assert [(r["arg"], next(k for k in r if k not in ("arg", "why"))) for r in job["runner_only"]] == [
        ("receptor", "not_suffix"), ("ligands", "not_suffix"), ("site_ligand", "present"), ("allow_remote", "is")]
    props = dock["parameters"]["properties"]
    assert "reference_ligand" in props and "crystal pose" in props["reference_ligand"]["description"]
    assert ".pdbqt" in props["receptor"]["description"] and "MODEL blocks" in props["ligands"]["description"]
    assert "prepared inputs" in dock["summary"] and "PDBQT" in dock["summary"]
    # prepared inputs run here, by name in any case, or by a file id whose name the page judges once it finds it
    for args in ({"receptor": "3ptb.pdbqt", "ligands": "ligands.PDBQT", "reference_ligand": "ben.pdbqt"},
                 {"receptor": "u_0123456789abcdef", "ligands": "3F2A1B4C-1234-4ABC-8DEF-0123456789AB",
                  "center": [0, 0, 0], "size": [20, 20, 20]},
                 {"receptor": " r.pdbqt ", "ligands": "l.pdbqt", "site_ligand": "", "allow_remote": False}):
        assert capability.runner_only(dock, args) == [], args
    # what needs preparing: the runner, with the reason
    for args, why in (({"receptor": "3ptb.pdb", "ligands": "l.pdbqt"}, ["meeko"]),
                      ({"receptor": "PDB:3ptb", "ligands": "CCO"}, ["meeko", "RDKit and meeko"]),
                      ({"receptor": "r.pdbqt", "ligands": "set.sdf"}, ["RDKit and meeko"]),
                      ({"receptor": "r.pdbqt", "ligands": "l.pdbqt", "site_ligand": "BEN"}, ["Chemical Component"]),
                      ({"receptor": "r.pdbqt", "ligands": "l.pdbqt", "allow_remote": True}, ["AlphaFold DB"])):
        found = capability.runner_only(dock, args)
        assert len(found) == len(why) and all(w in f for w, f in zip(why, found)), (args, found)


def test_the_runner_only_rule_types():
    """capability.runner_only's rules, as the page's core/catalog.js runnerOnly reads them: absent, is, present,
    suffix, not_suffix (a file id is no name, so not_suffix leaves it to whoever finds the file)."""
    from tcmstudio import capability

    def entry(*rules):
        return {"execution": {"browser_job": {"runner_only": [{**r, "why": f"rule {k}"} for k, r in enumerate(rules)]}}}
    absent, present = entry({"arg": "a", "absent": True}), entry({"arg": "a", "present": True})
    for value, gone in ((None, True), ("", True), (" ", False), ("x", False), (0, False), (False, False), ([], False)):
        args = {} if value is None else {"a": value}
        assert capability.runner_only(absent, args) == (["rule 0"] if gone else []), value
        assert capability.runner_only(present, args) == ([] if gone else ["rule 0"]), value
    assert capability.runner_only(present, None) == []
    not_suffix = entry({"arg": "f", "not_suffix": [".pdbqt"]})
    for value, met in (("r.pdbqt", False), ("R.PDBQT ", False), ("r.pdb", True), ("PDB:3ptb", True), ("CCO", True),
                       ("u_0123456789abcdef", False), ("3f2a1b4c-1234-4abc-8def-0123456789ab", False),
                       ("u_0123", True), ("", False), (None, False), (7, False)):
        assert capability.runner_only(not_suffix, {"f": value}) == (["rule 0"] if met else []), value
    suffix = entry({"arg": "f", "suffix": [".sdf", ".mol"]})
    assert capability.runner_only(suffix, {"f": "x.SDF"}) == ["rule 0"]
    assert capability.runner_only(suffix, {"f": "x.csv"}) == []
    assert capability.runner_only(entry({"arg": "b", "is": True}), {"b": True}) == ["rule 0"]
    assert capability.runner_only(entry({"arg": "b", "is": True}), {"b": False}) == []


def test_the_pipeline_parameters_and_their_bounds_are_the_runners():
    from tcmstudio import kinds
    rnaseq = get_entry("job.pipeline.rnaseq")["parameters"]["properties"]
    assert rnaseq["counts"]["description"].startswith("a gene × sample count table (TSV/CSV; integers)")
    assert (rnaseq["alpha"]["minimum"], rnaseq["alpha"]["maximum"]) == kinds.RNASEQ_ALPHA
    scrna = get_entry("job.pipeline.scrna")["parameters"]["properties"]
    assert scrna["n_top_genes"]["default"] == 2000
    assert (scrna["n_top_genes"]["minimum"], scrna["n_top_genes"]["maximum"]) == kinds.SCRNA_TOP_GENES
    assert (scrna["resolution"]["minimum"], scrna["resolution"]["maximum"]) == kinds.SCRNA_RESOLUTION
    models = get_entry("job.admet.models")["parameters"]["properties"]
    assert (models["max_iter"]["minimum"], models["max_iter"]["maximum"]) == kinds.ADMET_MAX_ITER
    assert models["max_iter"]["default"] == 600 and models["seed"]["default"] == 0


@pytest.mark.parametrize("kind, args, why", [
    ("job.pipeline.rnaseq", {"samples": "sheet.csv", "transcripts": "tx.fa"}, "without counts this is the FASTQ run"),
    ("job.pipeline.rnaseq", {"samples": "s.tsv", "counts": "c.tsv", "de_backend": "pydeseq2"}, "PyDESeq2 is not"),
    ("job.pipeline.scrna", {"source": "s.csv", "analysis_backend": "scanpy"}, "Scanpy, anndata"),
    ("job.pipeline.scrna", {"source": "s.csv", "integration": "scvi"}, "scVI (scvi-tools and torch)"),
    ("job.pipeline.scrna", {"source": "pbmc.h5"}, "a 10x HDF5 or .h5ad source"),
    ("job.pipeline.scrna", {"source": "atlas.H5AD"}, "a 10x HDF5 or .h5ad source"),
])
def test_a_browser_job_call_the_browser_runs_only_on_the_runner_needs_the_runner(kind, args, why):
    from tcmstudio import capability
    e = get_entry(kind)
    found = capability.requirements(e, where="browser", args=args, browser_jobs=True)
    assert [code for code, _ in found] == ["needs_runner"], found
    assert found[0][1]["detail"].startswith("the local runner (tcmstudio serve): ") and why in found[0][1]["detail"]
    assert capability.readiness(e, where="browser", args=args, browser_jobs=True)["state"] == "needs_runner"
    # on the runner the same call is the runner's to judge
    assert not [c for c, _ in capability.requirements(e, where="runner", args=args) if c == "needs_runner"]


def test_a_browser_job_call_the_browser_runs_needs_only_its_job_service():
    from tcmstudio import capability
    for kind, args in (("job.pipeline.rnaseq", {"samples": "s.tsv", "counts": "c.tsv"}),
                       ("job.pipeline.scrna", {"source": "sheet.csv", "integration": "harmony"}),
                       ("job.pipeline.scrna", {"source": "ctrl.tsv.gz", "integration": "none"})):
        e = get_entry(kind)
        assert capability.requirements(e, where="browser", args=args, browser_jobs=True) == []
        assert [c for c, _ in capability.requirements(e, where="browser", args=args, browser_jobs=False)] == \
            ["needs_job_service"]
        # without a call in view: ready only while the page's job service runs, as research.run
        assert capability.readiness(e, where="browser", browser_jobs=True)["state"] == "ready"
        assert capability.readiness(e, where="browser", browser_jobs=False)["state"] == "needs_runner"
    assert capability.runner_only(get_entry("job.pipeline.fold"), {"source": "x.h5"}) == []


def test_browser_research_run_readiness_follows_the_browsers_job_service_and_snapshots():
    from tcmstudio import capability

    run, snap = get_entry("job.research.run"), get_entry("job.research.snapshot")

    def needs(e, **kw):
        return [(code, r["need"]) for code, r in capability.requirements(e, where="browser", **kw)]

    assert needs(run, browser_jobs=False) == [("needs_job_service", "job_service")]
    # the service runs, and the page has not said whether snapshots are built: not judged
    assert needs(run, browser_jobs=True) == []
    assert needs(run, browser_jobs=True, browser_snapshots=True) == []
    # none built in this browser: research.run would be refused, so it is not ready (as the page's catalog says)
    missing = capability.requirements(run, where="browser", browser_jobs=True, browser_snapshots=False)
    assert [(c, r["need"], r.get("names")) for c, r in missing] == [("needs_dataset", "dataset", ["research snapshots"])]
    assert capability.readiness(run, where="browser", browser_jobs=True, browser_snapshots=False)["state"] == "needs_dataset"
    # building them needs no snapshots; the runner is not judged by the browser's
    assert needs(snap, browser_jobs=True, browser_snapshots=False) == []
    assert all(r["need"] != "dataset" or "browser" not in r["detail"]
               for _, r in capability.requirements(run, where="runner", jobs=True, browser_snapshots=False))


def test_browser_admet_readiness_follows_the_models_built_in_this_browser():
    """As the page's catalog says it (web/js/core/catalog.js entryState): pipeline.admet in the
    browser reads only the models built there, for the endpoints a call names, or any."""
    from tcmstudio import capability, dispatch

    run, build = get_entry("job.pipeline.admet"), get_entry("job.admet.models")

    def needs(e, **kw):
        return [(c, r["need"], r.get("names"), r["detail"])
                for c, r in capability.requirements(e, where="browser", browser_jobs=True, **kw)]

    # the page has not said: not judged
    assert needs(run) == []
    built = ["caco2_wang", "herg"]
    assert needs(run, browser_admet_models=built) == []
    assert needs(run, args={"endpoints": ["herg"]}, browser_admet_models=built) == []
    assert needs(run, args={"endpoints": ["herg", "ames"]}, browser_admet_models=built) == [
        ("needs_dataset", "dataset", ["ADMET models"], "the ADMET models built in this browser for ames")]
    # none built: refused whatever it names
    assert needs(run, browser_admet_models=[]) == [
        ("needs_dataset", "dataset", ["ADMET models"], "the ADMET models built in this browser")]
    assert capability.readiness(run, where="browser", browser_jobs=True,
                                browser_admet_models=[])["state"] == "needs_dataset"
    # building them needs no models; the runner is not judged by the browser's
    assert needs(build, browser_admet_models=[]) == []
    assert all(r["need"] != "dataset" for _, r in capability.requirements(
        run, where="runner", jobs=True, browser_admet_models=[]))
    # the page's context reaches the dispatcher's
    ctx, _ = dispatch.Context.coerce({"where": "browser", "browser_admet_models": built})
    assert ctx.browser_admet_models == ("caco2_wang", "herg")
    assert dispatch.Context.coerce({"browser_admet_models": [1, 2]})[0].browser_admet_models is None


def test_browser_catalog(browser_catalog, runner_catalog):
    assert browser_catalog["where"] == "browser"
    by_id = {e["id"]: e for e in browser_catalog["entries"]}
    assert set(by_id) == {e["id"] for e in runner_catalog["entries"]}
    for e in by_id.values():
        assert "available" not in e, "the build machine is not the user's browser"
        if e["kind"] == "job":
            # only the kinds the browser's job service implements
            assert ("browser" in e["exec"]) == (e["id"][4:] in BROWSER_JOB_KINDS), e["id"]
    for runner_only in ("job.pipeline.fold", "skill.dock-ligands", "skill.rnaseq-differential-expression"):
        assert "browser" not in by_id[runner_only]["exec"]
    # browser job ids (bj-…) are answered by the page; the runner's by the runner
    assert by_id["system.job_status"]["exec"] == ["browser", "runner"]
    for both in ("native.reverse_complement", "skill.assess-tcm-safety", "clinic.assess",
                 "tcmdb.catalog", "study.exposure_screen", "tcmdb.relations", "tcmdb.consensus",
                 "native.hkbu_formula_lookup", "connector.uniprot.entry"):
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
    # the acts reserved for a person ship with the catalog, so the page refuses them by name too
    assert "clinic.sign" in doc["never_offered"] and "system.shell" in doc["never_offered"]
    ids = {e["id"] for e in doc["entries"]} | {t["name"] for t in doc["core"]}
    assert not ids & set(doc["never_offered"])
