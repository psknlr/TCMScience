"""Capability state (CONTRACTS §2): implementation, verification and per-runtime readiness, from one
function (tcmstudio.capability), and the preflight refusals that follow from it.

Everything here is offline. 'ready' for an entry that reaches a host is tested only as "web access is on";
no host is contacted, and nothing from a fixture is ever read as live evidence."""

from __future__ import annotations

import csv
import importlib
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from tcmstudio import capability
from tcmstudio.catalog import (DEPRECATED, build_catalog, catalog_search, entries, get_entry,
                               skill_specs)
from tcmstudio.dispatch import call

REPO = Path(__file__).resolve().parents[3]
CSV = REPO / "BioScience-Harness" / "data" / "connector_live_verification.csv"
LEVELS = {"offline_invocation", "reference_output", "kernel_parity", "pyodide_parity", "lock_pin",
          "gateway_mock", "live_upstream", "live_gateway"}
STATUSES = {"PASS", "FAIL", "BLOCKED", "UNVERIFIED", "SKIPPED", "NOT_APPLICABLE"}
#: the size the browser catalog may reach (it was 775,364 bytes before the capability fields, 953,181 after)
CATALOG_BUDGET = 1_000_000


@pytest.fixture(scope="module")
def browser_doc():
    return build_catalog("browser", probe=False)


@pytest.fixture(scope="module")
def by_id(browser_doc):
    return {e["id"]: e for e in browser_doc["entries"]}


def has(name: str) -> bool:
    from tcmstudio.catalog import dependency_present
    return dependency_present(name)


# ---------------------------------------------------------------------- implementation and evidence

def test_every_entry_has_an_implementation_state_and_verification(browser_doc):
    for e in browser_doc["entries"]:
        impl, state, ver = e["implementation"], e["state"], e["verification"]
        assert impl["ref"] and impl["version"], e["id"]
        assert state["implementation"] in capability.IMPLEMENTATION, e["id"]
        assert "readiness" not in state, f"{e['id']}: the browser catalog decides no readiness"
        assert ver["status"] in STATUSES and set(ver["levels"]) <= LEVELS, e["id"]
        passed = any(lv["status"] == "PASS" for lv in ver["levels"].values())
        assert (state["implementation"] == "tested") is passed, e["id"]
        if not ver["levels"]:
            assert ver["status"] == "UNVERIFIED", e["id"]
        for lv in ver["levels"].values():
            assert lv["status"] in STATUSES
            assert "record" not in lv or lv["record"] not in browser_doc["verification_records"].values()
    assert set(browser_doc["verification_records"]) == {"live_upstream", "reference_output",
                                                        "kernel_parity", "lock_pin"}
    for path in browser_doc["verification_records"].values():
        assert (REPO / path).is_file(), path
    assert not DEPRECATED and not any(e["state"]["implementation"] == "deprecated"
                                      for e in browser_doc["entries"])


def test_implementation_references_resolve(browser_doc):
    from bioagent.providers.public_apis import SOURCES
    ops = {(s.key, op.name) for s in SOURCES for op in s.operations}
    for e in browser_doc["entries"]:
        ref = e["implementation"]["ref"]
        module, _, rest = ref.partition(":")
        if e["kind"] == "connector":
            assert module == "bioagent.providers.public_apis"
            assert tuple(rest.split(".", 1)) in ops, ref
            continue
        attr, _, key = rest.partition("#")
        mod = importlib.import_module(module)
        target = getattr(mod, attr)
        if e["kind"] == "job":
            assert key in target, ref                                  # tcmstudio.kinds.KINDS
        elif e["kind"] in ("native", "skill"):
            assert callable(target) and not key, ref
        else:
            assert callable(target) and key == e["id"].split(".", 1)[1], ref
            assert key in mod.HANDLED_OPS[attr], f"{ref}: the dispatcher handles no such op"
        assert e["state"]["implementation"] in ("implemented", "tested"), ref
    skill = get_entry("skill.assess-tcm-safety")["implementation"]
    spec = next(s for s in skill_specs() if s["id"] == "assess-tcm-safety")
    assert skill == {"ref": spec["entrypoint"], "version": spec["version"],
                     "content_hash": spec["content_hash"]}


def _ops_in_code(module, name: str, seen: set[str] | None = None) -> set[str]:
    """The ops a dispatcher function compares ``op`` with, and those of the helpers it hands
    ``op`` to: what it really dispatches on."""
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(module))
    funcs = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    seen = set() if seen is None else seen
    if name in seen or name not in funcs:
        return set()
    seen.add(name)
    out: set[str] = set()
    for node in ast.walk(funcs[name]):
        if isinstance(node, ast.Compare) and isinstance(node.left, ast.Name) and node.left.id == "op":
            for cmp, right in zip(node.ops, node.comparators):
                if isinstance(cmp, ast.Eq) and isinstance(right, ast.Constant):
                    out.add(right.value)
                elif isinstance(cmp, ast.In) and isinstance(right, (ast.Tuple, ast.List, ast.Set)):
                    out |= {c.value for c in right.elts if isinstance(c, ast.Constant)}
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.args
                and isinstance(node.args[0], ast.Name) and node.args[0].id == "op"):
            out |= _ops_in_code(module, node.func.id, seen)
    return out


def test_the_handled_ops_are_the_dispatchers_own(browser_doc):
    """'implemented' for a hand-written op means its executor dispatches on it."""
    from tcmstudio import catalog, dispatch
    assert set(dispatch.HANDLED_OPS) == {f"_exec_{k}" for k in ("clinic", "tcmdb", "study", "system")}
    for name, ops in dispatch.HANDLED_OPS.items():
        assert ops == _ops_in_code(dispatch, name), name
        kind = name.removeprefix("_exec_")
        listed = {e["id"].split(".", 1)[1] for e in browser_doc["entries"] if e["kind"] == kind}
        assert listed <= ops, (name, listed - ops)
    assert catalog._resolves("tcmstudio.dispatch:_exec_clinic#assess")
    assert not catalog._resolves("tcmstudio.dispatch:_exec_clinic#no_such_op")
    assert not catalog._resolves("tcmstudio.dispatch:_exec_native#gc_content"), "no op table"


def test_reference_output_pass_is_each_natives_own_validation(by_id, tmp_path):
    """A native reads reference_output PASS exactly when `tcmstudio validate` finds its output
    matching its independent references here (functional_correctness PASS), one by one."""
    from tcmstudio.cli import main
    # the exit code also counts the performance budgets, which a loaded machine can miss; this test is about
    # correctness only, and a native whose output is wrong drops out of `passed` below
    main(["validate", "--out", str(tmp_path), "--kinds", "native"])
    report = json.loads((tmp_path / "tool_validation_report.json").read_text(encoding="utf-8"))
    passed = {r["component"] for r in report["records"] if r["kind"] == "native"
              and any(c["name"] == "functional_correctness" and c["status"] == "PASS" for c in r["checks"])}
    claimed = {i for i, e in by_id.items() if e["kind"] == "native"
               and (e["verification"]["levels"].get("reference_output") or {}).get("status") == "PASS"}
    assert claimed and claimed == passed


def test_lock_pin_pass_is_a_content_hash_match(by_id):
    """lock_pin PASS: the skill's code in the tree hashes to the lockfile's pin, now."""
    from bioagent.skills.loader import load_skills
    pinned = [s for s in skill_specs() if s["pinned"]]
    assert pinned
    for spec in pinned:
        e = by_id[f"skill.{spec['id']}"]
        assert e["verification"]["levels"]["lock_pin"]["status"] == "PASS"
        loaded, refused = load_skills(Path(spec["group"]))
        assert not refused
        tree = next(x for x in loaded if x.spec.id == spec["id"])
        assert tree.content_hash == spec["content_hash"] == e["implementation"]["content_hash"], spec["id"]
    for i, e in by_id.items():
        if e["kind"] == "skill" and not e["skill"]["pinned"]:
            assert "lock_pin" not in e["verification"]["levels"], i


def test_connector_verification_is_the_committed_live_record(by_id):
    rows = {(r["source"], r["operation"]): r for r in csv.DictReader(CSV.open(encoding="utf-8", newline=""))}
    connectors = [e for e in by_id.values() if e["kind"] == "connector"]
    assert connectors
    for e in connectors:
        row = rows.get((e["connector"]["key"], e["connector"]["operation"]))
        levels = e["verification"]["levels"]
        if row is None:
            assert "live_upstream" not in levels, f"{e['id']}: live without a record"
            continue
        want = {"SUCCEEDED": "PASS", "FAILED": "FAIL", "BLOCKED": "BLOCKED"}.get(row["status"], "UNVERIFIED")
        assert levels["live_upstream"] == {"status": want, "at": row["verified_at"]}, e["id"]
    # live evidence exists only for connectors; nothing else claims a live level
    for e in by_id.values():
        if e["kind"] != "connector":
            assert not {"live_upstream", "live_gateway"} & set(e["verification"]["levels"]), e["id"]


def test_reference_kernel_and_pin_levels(by_id):
    from bioagent.validation.references import references_for
    from tcmstudio.browser_compute import _KERNEL_TOOLS
    for e in by_id.values():
        levels = e["verification"]["levels"]
        if e["kind"] == "native":
            assert ("reference_output" in levels) is bool(references_for(e["id"].split(".", 1)[1])), e["id"]
            assert ("kernel_parity" in levels) is (e["id"] in _KERNEL_TOOLS), e["id"]
        else:
            assert not {"reference_output", "kernel_parity"} & set(levels), e["id"]
        if e["kind"] == "skill":
            assert ("lock_pin" in levels) is e["skill"]["pinned"], e["id"]
        elif e["kind"] not in ("native", "connector"):
            assert e["verification"] == {"status": "UNVERIFIED", "levels": {}}, e["id"]
    pinned = [e["id"] for e in by_id.values() if "lock_pin" in e["verification"]["levels"]]
    assert sorted(pinned) == ["skill.analyze-tcm-network-pharmacology", "skill.assess-tcm-safety",
                              "skill.normalize-tcm-entities", "skill.retrieve-tcm-evidence"]


def test_browser_compute_is_derived_for_all_seven_kernel_tools(by_id):
    from tcmstudio.browser_compute import _KERNEL_TOOLS, _TOOLS
    found = {i: e["execution"]["browser_compute"] for i, e in by_id.items()
             if (e.get("execution") or {}).get("browser_compute")}
    assert set(found) == set(_KERNEL_TOOLS) and len(found) == 7
    for tool, bc in found.items():
        assert set(bc["backends"]) <= {"webgpu", "wasm-simd", "js"} and bc["precision"] and bc["workload"]
        assert ("webgpu" in bc["backends"]) is (tool in _TOOLS), tool
        assert bc["kernel"] == _KERNEL_TOOLS[tool]
        assert by_id[tool]["exec"] == ["browser", "runner"]
    # (as a word: a browser job's "inputs" is not an NPU)
    assert not any(re.search(r"\bnpus?\b", json.dumps(e.get("execution") or {}).lower())
                   for e in by_id.values())


def test_engines_hosts_data_approvals_and_limits(browser_doc, by_id):
    fold = {g["id"]: g for g in by_id["job.pipeline.fold"]["execution"]["engines"]}
    assert fold["esmfold"]["requires"][0]["names"] == ["torch", "transformers"]
    assert fold["esmfold"]["device"] == "gpu" and fold["esmatlas"]["device"] == "remote"
    scrna = {g["id"]: g for g in by_id["job.pipeline.scrna"]["execution"]["engines"]}
    assert scrna["scvi"]["selected_by"] == {"arg": "integration", "value": "scvi"}
    assert scrna["scvi"]["device"] == "cpu"
    skill = {g["id"]: g for g in by_id["skill.scrna-cell-atlas"]["execution"]["engines"]}
    assert skill["scvi"]["selected_by"]["arg"] == "integration_method"
    assert by_id["skill.scrna-cell-atlas"]["gpu"] is False          # scVI trains on the CPU by design
    rnaseq = {g["id"]: g for g in by_id["job.pipeline.rnaseq"]["execution"]["engines"]}
    assert rnaseq["salmon"]["requires"][0]["names"] == ["salmon"] and not rnaseq["builtin-quant"]["requires"]
    for e in by_id.values():
        for g in (e.get("execution") or {}).get("engines") or ():
            assert g["device"] in ("cpu", "gpu", "remote")
            assert g["selected_by"]["arg"] in e["parameters"]["properties"], (e["id"], g["id"])
            assert g["selected_by"]["value"] in e["parameters"]["properties"][g["selected_by"]["arg"]].get(
                "enum", [g["selected_by"]["value"]]), (e["id"], g["id"])
    fetch = by_id["job.tcmdb.fetch"]
    table = fetch["hosts_by"]["dataset"]
    assert set(table) == set(fetch["parameters"]["properties"]["dataset"]["enum"])
    assert all(1 <= len(h) <= 3 for h in table.values()) and "hosts" not in fetch
    tools = by_id["system.provider_call"]["hosts_by"]["tool"]
    from bioagent.providers.tooluniverse import load_allowlist
    assert {t.name for t in load_allowlist().tools} <= set(tools) and all(tools.values())
    for key in ("tcmdb.tables", "tcmdb.query", "tcmdb.unresolved"):
        assert by_id[key]["data_from"] == {"param": "dataset"}
    for key in ("tcmdb.relations", "tcmdb.consensus", "tcmdb.compare", "tcmdb.evidence_for"):
        assert by_id[key]["data"] == ["*"]
    assert by_id["native.hkbu_formula_lookup"]["data"] == ["hkbu_formulas_manual"]
    for e in by_id.values():
        reasons = [a["reason"] for a in e["approvals"]]
        assert reasons == [r for r in ("confirm", "job", "network", "remote_upload") if r in reasons]
        assert ("network" in reasons) is bool(e["network"]), e["id"]
        assert ("remote_upload" in reasons) is bool(e.get("network_if")), e["id"]
        assert bool({"confirm", "job"} & set(reasons)) is bool(e["confirm"]), e["id"]
    assert browser_doc["limits"] == {"max_request_bytes": capability.MAX_REQUEST_BYTES}
    from tcmstudio.server import MAX_JSON
    assert capability.MAX_REQUEST_BYTES["runner"] == MAX_JSON
    assert [e["id"] for e in by_id.values() if "limits" in e] == ["tcmdb.import_store"]


def test_the_browser_catalog_is_deterministic_and_within_its_budget(tmp_path):
    from tcmstudio.webbuild import catalog_bytes
    data, _ = catalog_bytes()
    assert len(data) <= CATALOG_BUDGET, f"catalog.json is {len(data):,} bytes; keep added fields compact"
    out = tmp_path / "catalog.json"
    proc = subprocess.run([sys.executable, "-m", "tcmstudio", "catalog", "--where", "browser", "--no-probe",
                           "--out", str(out)], capture_output=True, text=True, timeout=180)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(out.read_text(encoding="utf-8")) == json.loads(data)


# ------------------------------------------------------------------------------------- readiness

def test_one_vocabulary():
    assert capability.READINESS[0] == "ready" and "needs_approval" not in capability.READINESS
    assert set(capability.STATE_OF_NEED) == set(capability.NEEDS)
    assert set(capability.STATE_OF_NEED.values()) <= set(capability.READINESS)
    assert set(capability.REASON_NEED.values()) <= set(capability.NEEDS)
    for code in ("needs_runner", "missing_deps", "compute_browser", "no_runtime", "runner_offline",
                 "unknown_compute", "remote_not_configured", "too_large_for_browser", "missing_engine",
                 "needs_dataset", "network_off", "needs_job_service", "remote_upload_off"):
        assert code in capability.REASON_NEED
    with pytest.raises(ValueError):
        capability.requirement("magic", "x", "y")
    assert capability.state_of([{"need": "network"}, {"need": "dependency"}]) == "missing_dependency"
    assert capability.state_of([]) == "ready"


def test_the_page_knows_every_reason_code():
    """Every error.reason Python can send is one the page's router lists (core/router.js)."""
    import re
    js = (REPO / "studio" / "web" / "js" / "core" / "router.js").read_text(encoding="utf-8")
    known: set[str] = set()
    for name in ("PLACEMENT_REASONS", "RUNTIME_REASONS"):
        body = re.search(rf"export const {name} = Object\.freeze\(\[(.*?)\]\)", js, re.S)
        assert body, name
        known |= set(re.findall(r'"([a-z_]+)"', body.group(1)))
    assert set(capability.REASON_NEED) <= known, sorted(set(capability.REASON_NEED) - known)


def test_readiness_is_judged_only_against_what_the_caller_says():
    """No readiness from a guess: without this runner's network switch and job service, the
    probed catalog carries none (the running runner fills it in, server.py)."""
    guessed = entries("runner", probe=True)
    assert all("readiness" not in e["state"] for e in guessed)
    assert any("readiness" in g for e in guessed for g in (e.get("execution") or {}).get("engines") or ())
    told = build_catalog("runner", probe=True, network=False, jobs=False, built=[])
    assert all(e["state"]["readiness"]["runner"]["state"] in capability.READINESS for e in told["entries"])


def test_the_runner_catalog_carries_readiness_and_a_missing_heavy_dep_is_never_ready():
    items = entries("runner", probe=True, network=False, jobs=True, built=[])
    for e in items:
        r = e["state"]["readiness"]["runner"]
        assert r["state"] in capability.READINESS and isinstance(r["requires"], list)
        if e.get("available") is False:
            assert r["state"] == "missing_dependency", e["id"]
            assert r["requires"][0]["names"] == e["missing"]
        if e["kind"] == "connector":
            assert r["state"] == "network_unavailable", e["id"]
    by = {e["id"]: e for e in items}
    # an optional engine does not make its entry unready; the engine says so itself
    fold = by["job.pipeline.fold"]
    assert fold["state"]["readiness"]["runner"]["state"] == "ready"
    esmfold = next(g for g in fold["execution"]["engines"] if g["id"] == "esmfold")
    torch_here = has("torch") and has("transformers")
    assert (esmfold["readiness"]["runner"]["state"] == "ready") is torch_here


@pytest.mark.parametrize("entry_id", ["native.hkbu_formula_lookup", "native.hkcmms_standard_lookup",
                                      "native.hk_cmm_dna_lookup", "tcmdb.tables", "tcmdb.query",
                                      "tcmdb.relations", "tcmdb.consensus", "tcmdb.compare",
                                      "tcmdb.evidence_for", "tcmdb.unresolved"])
def test_needs_dataset_until_its_store_is_built(entry_id, tmp_path):
    from bioagent.tcmdb import TCMDataHub
    e = get_entry(entry_id)
    empty = capability.readiness(e, where="runner", network=True, jobs=True,
                                 built=TCMDataHub(tmp_path / "tcmdb").built())
    assert empty["state"] == "needs_dataset"
    need = empty["requires"][0]
    assert need["need"] == "dataset" and "job.tcmdb" in need["remedy"] and "tcmdb.import_store" in need["remedy"]
    key = (e.get("data") or ["hkbu_formulas_manual"])[0]
    key = "hkbu_formulas_manual" if key == "*" else key
    hub = TCMDataHub(tmp_path / "tcmdb")
    hub.db_path(key).parent.mkdir(parents=True, exist_ok=True)
    hub.db_path(key).write_bytes(b"")           # built() asks only whether the store exists
    ready = capability.readiness(e, where="runner", network=True, jobs=True, built=hub.built(),
                                 args={"dataset": key} if e.get("data_from") else None)
    assert ready == {"state": "ready", "requires": []}
    if e.get("data_from"):
        other = capability.readiness(e, where="runner", network=True, jobs=True, built=hub.built(),
                                     args={"dataset": "herb2"})
        assert other["state"] == "needs_dataset" and other["requires"][0]["names"] == ["herb2"]


def test_a_job_service_that_cannot_start_is_named_once_with_a_runner_remedy():
    e = get_entry("job.pipeline.rnaseq")
    why = "jobs need a POSIX system (Linux or macOS; on Windows, run the runner in WSL): no fork"
    r = capability.readiness(e, where="runner", network=True, jobs=False, built=[], kind_missing=[why])
    jobs = [q for q in r["requires"] if q["need"] == "job_service"]
    assert [q["detail"] for q in jobs] == [why]
    assert "tcmstudio serve" not in jobs[0]["remedy"], "on the runner, never 'start the runner'"
    plain = capability.readiness(e, where="runner", network=True, jobs=False, built=[])
    jobs = [q for q in plain["requires"] if q["need"] == "job_service"]
    assert [q["detail"] for q in jobs] == ["the runner's job service"]
    assert "tcmstudio serve" not in jobs[0]["remedy"]
    # a kind the browser does not run (pipeline.rnaseq is a browser job now)
    page = capability.readiness(get_entry("job.pipeline.fold"), where="browser", network=True,
                                jobs=False)
    assert "tcmstudio serve" in page["requires"][0]["remedy"]          # the browser: start one


@pytest.mark.parametrize("kind,missing,need,expected", [
    ("unavailable", ["rdkit", "vina"], None, ("missing_deps", ["dependency"])),
    ("unavailable", ["pyarrow", "snapshots (/x/ledger.jsonl)"], None,
     ("missing_deps", ["dependency", "dataset"])),
    ("unavailable", ["snapshots (/x/ledger.jsonl)"], None, ("needs_dataset", ["dataset"])),
    ("unavailable", [], "compute_setting", ("remote_upload_off", ["compute_setting"])),
    ("unavailable", [], "job_service", ("needs_job_service", ["job_service"])),
    ("network_off", [], "network", ("network_off", ["network"])),
])
def test_a_runner_refusal_in_the_one_vocabulary(kind, missing, need, expected):
    reason, requires = capability.refusal_requirements(kind, missing, message="It cannot run.",
                                                       hint="Do this.", need=need)
    assert (reason, [r["need"] for r in requires]) == expected
    assert capability.REASON_NEED[reason] == requires[0]["need"]
    if expected[1][0] == "dependency":
        assert requires[0]["names"] == [m for m in missing if not m.startswith("snapshots")]
        assert requires[0]["detail"] == "install: " + ", ".join(requires[0]["names"])
    # the same words the runner's catalog gives for the same missing list
    if missing:
        e = get_entry("job.research.run")
        same = capability.readiness({**e, "available": False, "missing": []}, where="runner",
                                    network=True, jobs=True, kind_missing=missing)
        assert same["requires"] == requires


def test_needs_dataset_leaves_the_tools_results_as_they_were(runner_ctx):
    # the hk lookups keep answering not_loaded; readiness says needs_dataset beside them
    e = get_entry("native.hkbu_formula_lookup")
    env = call("call_tool", {"tool": e["id"], "arguments": e["example"]}, runner_ctx)
    assert env["status"] == "succeeded" and env["result"]["status"] == "not_loaded"
    found = call("catalog_search", {"query": "hkbu_formula_lookup"}, runner_ctx)["result"]["matched"]
    hk = next(m for m in found if m["id"] == e["id"])
    assert hk["runnable_now"] is False
    assert hk["needs"] == ["the hkbu_formulas_manual dataset built or imported in this runtime"]


def test_catalog_search_needs_keep_their_words():
    assert catalog_search("uniprot", where="runner")["matched"][0]["needs"] == ["web access for this project"]
    dock = next(m for m in catalog_search("dock ligands", where="browser", limit=25)["matched"]
                if m["id"] == "skill.dock-ligands")
    assert dock["needs"] == ["the local runner (tcmstudio serve)", "the runner's job service"]
    pool = entries("runner", probe=True)
    admet = next(m for m in catalog_search("predict admet", where="runner", jobs=True, items=pool,
                                           limit=25)["matched"] if m["id"] == "job.pipeline.admet")
    if not (has("rdkit") and has("scikit-learn")):
        assert admet["needs"] == ["install: " + ", ".join(d for d in ("rdkit", "scikit-learn") if not has(d))]
    else:
        assert admet["needs"] == []


def test_ready_entries_are_not_refused_by_the_preflight(runner_ctx):
    """'ready' must mean the dispatcher will not say unavailable / network_off for the entry's own example."""
    items = entries("runner", probe=True, network=False, jobs=True, built=[])
    checked = 0
    for e in items:
        if e["kind"] in ("job", "skill", "connector") or e.get("example") is None:
            continue
        if e["state"]["readiness"]["runner"]["state"] != "ready":
            continue
        env = call("call_tool", {"tool": e["id"], "arguments": e["example"]}, runner_ctx)
        assert (env.get("error") or {}).get("type") not in ("unavailable", "network_off"), (e["id"], env["error"])
        checked += 1
    assert checked >= 150


# ------------------------------------------------------------------------------------- preflight

def test_preflight_names_what_is_missing(runner_ctx, browser_ctx):
    e = call("run_pipeline", {"pipeline": "dock", "arguments": {"receptor": "PDB:3ptb",
                                                                "ligands": "CCO"}}, browser_ctx)
    err = e["error"]
    assert err["type"] == "unavailable" and err["reason"] == "needs_runner"
    assert err["requires"][0] == {"need": "runner", "detail": "the local runner (tcmstudio serve)",
                                  "remedy": err["hint"]}
    # a browser job kind, with arguments only the runner runs: the runner, and why
    e = call("run_pipeline", {"pipeline": "rnaseq", "arguments": {"samples": "x"}}, browser_ctx)
    err = e["error"]
    assert err["type"] == "unavailable" and err["reason"] == "needs_runner"
    assert err["requires"][0]["detail"].startswith("the local runner (tcmstudio serve): without "
                                                   "counts this is the FASTQ run")
    assert "these arguments need the local runner: without counts" in err["message"]
    off = call("call_tool", {"tool": "job.tcmdb.fetch", "arguments": {"dataset": "herb2"}}, runner_ctx)
    assert off["error"]["type"] == "network_off" and off["error"]["reason"] == "network_off"
    hosts = get_entry("job.tcmdb.fetch")["hosts_by"]["dataset"]["herb2"]
    assert off["error"]["requires"] == [{"need": "network", "detail": "web access for this project",
                                         "remedy": off["error"]["hint"], "names": hosts}]
    assert all(h in off["error"]["message"] for h in hosts)
    if not has("rdkit"):
        dock = call("call_tool", {"tool": "job.pipeline.dock", "arguments": {"receptor": "PDB:3ptb",
                                                                            "ligands": "CCO"}}, runner_ctx)
        assert dock["error"]["reason"] == "missing_deps"
        assert dock["error"]["requires"][0]["need"] == "dependency"
        assert "rdkit" in dock["error"]["requires"][0]["names"]


def test_run_pipeline_in_the_browser_says_which_calls_need_the_runner(browser_ctx):
    """The browser's job service runs pipeline.rnaseq with counts and pipeline.scrna on count
    tables with the built-in engines; any other call needs the runner, and says why."""
    for args, why in (
            ({"pipeline": "rnaseq", "arguments": {"samples": "sheet.csv", "transcripts": "tx.fa"}},
             "without counts this is the FASTQ run"),
            ({"pipeline": "rnaseq", "arguments": {"samples": "s.tsv", "counts": "c.tsv",
                                                  "de_backend": "pydeseq2"}},
             "PyDESeq2 is not in this browser's Python"),
            ({"pipeline": "scrna", "arguments": {"source": "pbmc.h5"}}, "a 10x HDF5 or .h5ad source"),
            ({"pipeline": "scrna", "arguments": {"source": "t.csv", "analysis_backend": "scanpy"}},
             "Scanpy, anndata, leidenalg and umap-learn")):
        err = call("run_pipeline", args, browser_ctx)["error"]
        assert err["type"] == "unavailable" and err["reason"] == "needs_runner", err
        assert "runs in the browser only in part" in err["message"] and why in err["message"]
        assert err["requires"][0]["need"] == "runner" and why in err["requires"][0]["detail"]
    # a pipeline the browser does not run at all
    err = call("run_pipeline", {"pipeline": "fold", "arguments": {
        "source": "MKTAYIAKQRQISFVKSHFSRQIEER"}}, browser_ctx)["error"]
    assert err["reason"] == "needs_runner" and "it is not available in the browser" in err["message"]
    # one the browser runs is not refused for the runner (the page's job service takes it)
    ok = call("run_pipeline", {"pipeline": "scrna", "arguments": {"source": "sheet.csv"}}, browser_ctx)
    assert (ok.get("error") or {}).get("reason") != "needs_runner"


def test_a_request_too_large_for_the_browser_is_refused_before_it_runs(browser_ctx):
    big = "A" * (capability.MAX_REQUEST_BYTES["browser"] + 16)
    e = call("call_tool", {"tool": "native.reverse_complement", "arguments": {"sequence": big}}, browser_ctx)
    assert e["status"] == "failed" and e["error"]["type"] == "unavailable"
    assert e["error"]["reason"] == "too_large_for_browser"
    assert e["error"]["requires"][0]["need"] == "memory" and e["result"] is None


@pytest.mark.parametrize("entry_id,args,needs", [
    ("job.pipeline.fold", {"source": "MKTAYIAKQRQISFVKSHFSRQIEER", "method": "esmfold"}, ("torch", "transformers")),
    ("job.pipeline.scrna", {"source": "u_0000000000000000", "integration": "scvi"}, ("scvi-tools", "torch")),
    ("job.pipeline.rnaseq", {"samples": "u_0000000000000000", "engine": "salmon"}, ("salmon",)),
    ("job.pipeline.rnaseq", {"samples": "u_0000000000000000", "de_backend": "pydeseq2"}, ("pydeseq2",)),
    ("skill.scrna-cell-atlas", {"source": "x", "integration_method": "scvi"}, ("scvi-tools", "torch")),
])
def test_a_chosen_engine_that_is_not_installed_is_refused_before_submit(entry_id, args, needs, runner_ctx, jobs):
    missing = [n for n in needs if not has(n)]
    if not missing:
        pytest.skip(f"{', '.join(needs)} installed here")
    e = call("call_tool", {"tool": entry_id, "arguments": args}, runner_ctx)
    assert e["status"] == "failed" and e["error"]["type"] == "unavailable", e["error"]
    assert e["error"]["reason"] == "missing_engine"
    assert set(missing) <= set(e["error"]["requires"][0]["names"])
    assert not jobs.submitted, "nothing is submitted for an engine that is not here"


def test_the_builtin_defaults_still_submit(runner_ctx, jobs):
    for entry_id, args in (("job.pipeline.scrna", {"source": "u_0000000000000000"}),
                           ("job.pipeline.rnaseq", {"samples": "u_0000000000000000", "engine": "builtin"}),
                           ("job.pipeline.scrna", {"source": "u_0000000000000000", "integration": "harmony"})):
        e = call("call_tool", {"tool": entry_id, "arguments": args}, runner_ctx)
        assert e["status"] == "job_submitted", (entry_id, e["error"])
    assert len(jobs.submitted) == 3


# ------------------------------------------------------------------------------------------ audit

def test_the_audit_counts_every_entry_and_filters_only_the_list(runner_ctx):
    full = call("call_tool", {"tool": "system.capability_audit", "arguments": {"limit": 1000}}, runner_ctx)
    assert full["status"] == "succeeded"
    r = full["result"]
    n = len(entries("runner", probe=False))
    assert r["where"] == "runner" and r["counts"]["entries"] == n == r["matched"] == len(r["entries"])
    for key in ("by_readiness", "by_implementation", "by_verification"):
        assert sum(r["counts"][key].values()) == n
    for row in r["entries"]:
        assert row["readiness"] in capability.READINESS
        assert (row["readiness"] == "ready") is (not row["requires"])
        e = get_entry(row["id"])
        if e.get("heavy") and not all(has(d) for d in e["heavy"]):
            assert row["readiness"] != "ready", row["id"]
    assert "no request was sent" in r["note"]
    jobs_only = call("call_tool", {"tool": "system.capability_audit",
                                   "arguments": {"kind": "job", "limit": 3}}, runner_ctx)["result"]
    assert jobs_only["counts"] == r["counts"] and jobs_only["matched"] == 11 and len(jobs_only["entries"]) == 3
    unready = call("call_tool", {"tool": "system.capability_audit",
                                 "arguments": {"state": "needs_dataset"}}, runner_ctx)["result"]
    assert unready["entries"] and all(x["readiness"] == "needs_dataset" for x in unready["entries"])
    assert full["summary"].startswith("能力审计（本机 Runner）") and full["summary_en"].startswith("Capability audit")


def test_the_browser_audit_judges_the_browser(browser_ctx):
    e = call("call_tool", {"tool": "system.capability_audit", "arguments": {"kind": "job"}}, browser_ctx)
    assert e["status"] == "succeeded" and e["result"]["where"] == "browser"
    assert all(x["readiness"] == "needs_runner" for x in e["result"]["entries"])


def test_the_envelope_keeps_well_formed_requires_and_reason_only():
    from tcmstudio.envelope import shape
    good = shape("x", status="failed", error={
        "type": "unavailable", "message": "m", "hint": "h", "reason": "missing_deps",
        "requires": [{"need": "dependency", "detail": "install: rdkit", "remedy": "h", "names": ["rdkit"]}]})
    assert good["error"]["reason"] == "missing_deps"
    assert good["error"]["requires"][0]["names"] == ["rdkit"]
    bad = shape("x", status="failed", error={"type": "unavailable", "message": "m", "reason": "Not A Code!",
                                             "requires": [{"detail": "no need"}]})
    assert set(bad["error"]) == {"type", "message", "hint"}
    plain = shape("x", status="failed", error={"type": "unavailable", "message": "m"})
    assert set(plain["error"]) == {"type", "message", "hint"}
