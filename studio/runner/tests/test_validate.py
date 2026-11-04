"""tcmstudio validate: every capability enumerated, run through the dispatcher, judged.

These run the validator itself on real entries (no entry is mocked); two tests break one
thing on purpose — a native tool that opens a connection, a reference that disagrees — to
show the validator reports it instead of passing it.
"""
from __future__ import annotations

import json
import socket

import pytest

from bioagent.validation import FAIL, PASS, SKIPPED
from bioagent.validation.references import Reference
from tcmstudio import dispatch, validate
from tcmstudio.catalog import entries
from tcmstudio.core_tools import CORE_TOOLS


def _items(*ids):
    by = {e["id"]: e for e in entries("runner", probe=False)}
    core = {c["name"]: c for c in CORE_TOOLS}
    return [(i, "core", core[i]) if i in core else (i, by[i]["kind"], by[i]) for i in ids]


def _ctx(tmp_path):
    return {"where": "runner", "state_root": str(tmp_path / "state"),
            "tcmdb_root": str(tmp_path / "tcmdb"), "project_id": "v"}


def _run(tmp_path, *ids):
    return {r.component: r for r in validate.validate_entries(
        _items(*ids), ctx_base=_ctx(tmp_path), tier="offline", built=set(), log=lambda m: None)}


def test_a_native_tool_with_a_reference_passes_all_twelve_checks(tmp_path):
    rec = _run(tmp_path, "native.kaplan_meier")["native.kaplan_meier"]
    assert {c.name for c in rec.checks} == set(validate.CHECKS)
    assert rec.status == PASS, rec.as_dict()
    assert "Freireich" in rec.check("functional_correctness").detail
    assert rec.check("execution_isolation").status == PASS
    assert rec.check("error_handling").status == PASS


def test_offline_a_connector_is_refused_and_counted_as_skipped_never_passed(tmp_path):
    rec = _run(tmp_path, "connector.europepmc.search")["connector.europepmc.search"]
    assert rec.check("invocation").status == SKIPPED
    assert rec.check("execution_isolation").status == PASS, "refused with network_off"
    assert rec.status != PASS


def test_the_tcm_tools_agree_with_the_classical_texts(tmp_path):
    recs = _run(tmp_path, "tcm_formula", "tcm_compatibility", "tcm_herb")
    for name in ("tcm_formula", "tcm_compatibility", "tcm_herb"):
        fc = recs[name].check("functional_correctness")
        assert fc.status == PASS, (name, fc.detail)


def test_a_native_tool_that_opens_a_connection_fails_isolation(tmp_path, monkeypatch):
    real = dispatch._EXECUTORS["native"]

    def leaky(entry, args, ctx):
        try:
            socket.create_connection(("example.org", 80), timeout=1)
        except OSError:
            pass
        return real(entry, args, ctx)
    monkeypatch.setitem(dispatch._EXECUTORS, "native", leaky)
    rec = _run(tmp_path, "native.gc_content")["native.gc_content"]
    iso = rec.check("execution_isolation")
    assert iso.status == FAIL and "example.org" in iso.detail and rec.status == FAIL


def test_a_result_that_disagrees_with_its_reference_fails(tmp_path, monkeypatch):
    from bioagent.validation import references
    wrong = Reference("bmi", {"weight_kg": 70, "height_cm": 175}, (("bmi", 30.0, 1e-6),), "a wrong value")
    monkeypatch.setattr(references, "REFERENCES", (wrong,))
    rec = _run(tmp_path, "native.bmi")["native.bmi"]
    assert rec.check("functional_correctness").status == FAIL
    assert "expected 30.0" in rec.check("functional_correctness").detail


def test_the_watch_refuses_and_records_lookups_and_connections_off_loopback():
    with validate.watched_sockets() as attempts:
        with pytest.raises(OSError):
            socket.create_connection(("example.org", 443), timeout=1)
        with pytest.raises(socket.gaierror):
            socket.getaddrinfo("bidd.group", 443)
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        cli = socket.socket()
        cli.connect(("127.0.0.1", srv.getsockname()[1]))         # loopback is allowed
        cli.close()
        srv.close()
    assert attempts == ["example.org", "bidd.group"]
    socket.getaddrinfo("localhost", 80)                              # restored


def test_every_capability_that_is_not_a_job_has_an_input_to_run_it_with():
    from tcmstudio.catalog import get_entry
    every = [c["name"] for c in CORE_TOOLS] + [e["id"] for e in entries("runner", probe=False)]
    unrun = []
    for rid, kind, item in _items(*every):
        entry = (get_entry(item.get("maps_to") or "") or {}) if kind == "core" else item
        job = item.get("job") if kind == "core" else entry.get("job")
        if not job and not isinstance(item.get("example"), dict) and rid not in validate.PROBES:
            unrun.append(rid)
    assert not unrun, f"give these a probe in tcmstudio/validate.py PROBES: {unrun}"


PROBED = ("clinic_assess", "clinic_check_prescription", "clinic_followup", "clinic.assess",
          "clinic.check", "clinic.verify", "skill.draft-tcm-prescription", "study.combination",
          "study.effect_modification", "study.exposure_screen", "tcmdb.consensus",
          "tcmdb.compare", "tcmdb.import_store", "tcmdb.tables", "tcmdb.query",
          "tcmdb.unresolved")


def test_the_probes_run_what_ships_no_example_and_write_into_no_hub_they_were_given(tmp_path):
    recs = _run(tmp_path, *PROBED, "connector_call", "literature_search", "system.provider_call",
                "system.job_status", "tcm_syndrome", "tcm_applicability")
    for rid in (*PROBED, "tcm_syndrome", "tcm_applicability"):
        inv = recs[rid].check("invocation")
        assert inv.status == PASS and inv.detail.startswith("succeeded"), (rid, inv.detail)
        assert recs[rid].check("execution_isolation").status == PASS, rid
    for rid in ("connector_call", "literature_search", "system.provider_call"):
        assert recs[rid].check("invocation").status == SKIPPED
        assert recs[rid].check("execution_isolation").status == PASS, "refused with network_off"
    # without a job service it is refused as unavailable, never counted as a pass
    assert recs["system.job_status"].check("invocation").status == "BLOCKED"
    # the store the import probe wrote is in the validator's own hub, not the one it was given
    assert not (tmp_path / "tcmdb").exists() or not list((tmp_path / "tcmdb").rglob("*.sqlite"))
    assert list((tmp_path / "state" / "probe-tcmdb").rglob("*.sqlite"))


def _sentinel_hub(root):
    """A data hub holding a DDID store of a person's own (not the probe's), and its bytes."""
    import sqlite3

    from bioagent.tcmdb.rowkit import COLUMNS
    store = root / "db" / "ddid.sqlite"
    store.parent.mkdir(parents=True)
    with sqlite3.connect(store) as conn:
        conn.execute("CREATE TABLE _tcmdb_files (tbl TEXT, rows INTEGER, license TEXT, sha256 TEXT)")
        conn.execute("INSERT INTO _tcmdb_files VALUES ('built_by_a_person', 1, 'CC BY 4.0', 'mine')")
        conn.execute("CREATE TABLE built_by_a_person (note TEXT)")
        conn.execute("INSERT INTO built_by_a_person VALUES ('the real DDID store')")
        conn.execute("CREATE TABLE relations (" + ",".join(f'"{c}" TEXT' for c in COLUMNS) + ")")
    conn.close()
    return store, store.read_bytes()


def _tree(root):
    return sorted(str(p.relative_to(root)) for p in root.rglob("*"))


@pytest.fixture
def default_hub(tmp_path, monkeypatch):
    """The default data hub ($BIOAGENT_TCMDB, as a developer's real one would be) holding a
    sentinel DDID store; the data lake moved into tmp_path too, so nothing reaches the repo."""
    root = tmp_path / "default-hub"
    store, before = _sentinel_hub(root)
    monkeypatch.setenv("BIOAGENT_TCMDB", str(root))
    monkeypatch.setenv("BIOAGENT_DATA_LAKE", str(tmp_path / "lake"))
    return root, store, before, _tree(root)


def test_a_probe_made_without_a_context_writes_into_no_default_hub(default_hub):
    """sweep_calls.py asked validate._example for its inputs with no context: the probe of
    tcmdb.tables/query/unresolved then imported the probe store into the default hub,
    replacing a person's real DDID store with alpha and beta. A probe that writes runs in
    throwaway roots of its own when its context names none."""
    root, store, before, tree = default_hub
    for rid in ("tcmdb.tables", "tcmdb.query", "tcmdb.unresolved", "tcmdb.import_store",
                "clinic.verify"):
        args = validate._example({}, rid)
        assert isinstance(args, dict) and args, rid
        assert store.read_bytes() == before, f"{rid}'s probe rewrote the default hub's store"
    assert validate._example({}, "clinic.verify")["session_id"]
    # a context that names no hub of its own gets a throwaway one too (a runner context would
    # otherwise resolve to the default hub)
    validate._example({}, "tcmdb.tables", {"where": "runner", "network": False})
    assert store.read_bytes() == before and _tree(root) == tree


def test_the_sweep_call_listing_leaves_the_default_hub_byte_identical(tmp_path, default_hub):
    """sweep.mjs runs sweep_calls.py before anything else, in CI and on a developer's machine:
    listing the calls must not touch the default hub (it created <repo>/data/ and replaced the
    DDID store a developer had built there)."""
    import os
    import subprocess
    import sys
    from pathlib import Path

    from tcmstudio.webbuild import catalog_bytes
    root, store, before, tree = default_hub
    site = tmp_path / "site"
    (site / "runtime").mkdir(parents=True)
    (site / "runtime" / "catalog.json").write_bytes(catalog_bytes(strict=False)[0])
    script = Path(__file__).resolve().parents[2] / "web" / "test" / "runtime" / "sweep_calls.py"
    out = tmp_path / "calls.json"
    done = subprocess.run([sys.executable, str(script), "--site", str(site), "--groups",
                           "entry,core", "--out", str(out)], env=dict(os.environ),
                          capture_output=True, text=True, timeout=300)
    assert done.returncode == 0, done.stderr[-2000:]
    calls = {c["key"]: c for s in json.loads(out.read_text())["sequences"] for c in s["calls"]}
    assert calls["entry:tcmdb.tables"]["args"] == {"tool": "tcmdb.tables",
                                                   "arguments": {"dataset": "ddid"}}
    assert store.read_bytes() == before, "listing the calls rewrote the default hub's DDID store"
    assert _tree(root) == tree


@pytest.mark.parametrize("given", [False, True])
def test_the_command_leaves_the_default_and_the_given_hub_alone(tmp_path, default_hub, given):
    """tcmstudio validate's probes import into a hub of the validator's own: neither the
    default hub nor one given with --tcmdb-root has its store replaced."""
    from tcmstudio.cli import main
    root, store, before, tree = default_hub
    argv = ["validate", "--out", str(tmp_path / "reports"), "--kinds", "tcmdb"]
    assert main(argv + (["--tcmdb-root", str(root)] if given else [])) == 0
    assert store.read_bytes() == before
    assert _tree(root) == tree
    report = json.loads((tmp_path / "reports" / "tool_validation_report.json").read_text())
    ran = {r["component"]: r for r in report["records"]}
    for rid in ("tcmdb.import_store", "tcmdb.tables", "tcmdb.query", "tcmdb.unresolved"):
        inv = next(c for c in ran[rid]["checks"] if c["name"] == "invocation")
        assert inv["status"] == PASS, (rid, inv)


def test_live_a_network_tool_is_called_for_real_not_through_the_offline_watch(tmp_path,
                                                                              monkeypatch):
    """live-data 33: in the live tier the network core tools' probes ran inside the watch that
    refuses every lookup off the loopback interface, so connector_call and literature_search
    FAILed with "an offline call looked up rest.uniprot.org" (www.ebi.ac.uk) without a request
    ever leaving. Live, a network entry reaches the network; the watch is for offline calls.
    The name lookups here land in a stand-in resolver: no request leaves this machine."""
    asked: list[str] = []

    def resolver(host, *args, **kwargs):
        asked.append(str(host))
        raise socket.gaierror(-2, f"stand-in resolver: {host} is not looked up in tests")
    for var in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "https_proxy", "http_proxy",
                "all_proxy"):
        monkeypatch.delenv(var, raising=False)      # as in CI: the call looks up its host
    monkeypatch.setattr(socket, "getaddrinfo", resolver)
    recs = {r.component: r for r in validate.validate_entries(
        _items("connector_call", "literature_search", "native.gc_content"),
        ctx_base=_ctx(tmp_path), tier="live", built=set(), log=lambda m: None)}
    for rid, host in (("connector_call", "rest.uniprot.org"), ("literature_search", "www.ebi.ac.uk")):
        inv = recs[rid].check("invocation")
        assert "an offline call" not in inv.detail, (rid, inv.detail)
        assert host in asked, (rid, asked)
        assert recs[rid].check("execution_isolation").status == "NOT_APPLICABLE"
    # an in-process tool is still watched in the live tier, and makes no lookup
    assert recs["native.gc_content"].check("execution_isolation").status == PASS
    assert recs["native.gc_content"].check("invocation").status == PASS


def test_a_probe_that_answers_not_found_fails(tmp_path, monkeypatch):
    """A probe names what the corpus holds: not_found from it is a failure, never a pass (a
    job id named on purpose is the exception)."""
    monkeypatch.setitem(validate.PROBES, "tcm_syndrome", {"name": "肝郁脾虚证"})
    monkeypatch.setitem(validate.PROBES, "tcm_applicability",
                        {"subject": "桂枝汤", "object": "肝郁脾虚证"})
    recs = _run(tmp_path, "tcm_syndrome", "tcm_applicability")
    for rid in ("tcm_syndrome", "tcm_applicability"):
        inv = recs[rid].check("invocation")
        assert inv.status == FAIL and "answered not_found" in inv.detail, (rid, inv.detail)
        assert recs[rid].status == FAIL


def test_a_probe_that_succeeds_with_an_empty_result_fails(tmp_path, monkeypatch):
    """A name outside a governed run's corpus 'succeeds' with nothing analysed (葛根芩连汤 is
    not among the seed corpus's six formulas: no network is built, "an empty result, not a
    null finding"): a not_found by other means, which fails its probe as not_found does. The
    shipped probe names a seed formula, so the call exercises the tool."""
    from bioagent.tcm.knowledge import default_knowledge
    seed = {f.chinese for f in default_knowledge().formulas.values()}
    assert validate.PROBES["tcm_network_hypothesis"]["formula_name"] in seed
    inv = _run(tmp_path, "tcm_network_hypothesis")["tcm_network_hypothesis"].check("invocation")
    assert inv.status == PASS and inv.detail.startswith("succeeded"), inv.detail
    monkeypatch.setitem(validate.PROBES, "tcm_network_hypothesis", {"formula_name": "葛根芩连汤"})
    monkeypatch.setitem(validate.PROBES, "tcm_safety_report", {"subject": "不存在的药"})
    recs = _run(tmp_path, "tcm_network_hypothesis", "tcm_safety_report")
    for rid in ("tcm_network_hypothesis", "tcm_safety_report"):
        inv = recs[rid].check("invocation")
        assert inv.status == FAIL and "empty result" in inv.detail, (rid, inv.detail)
        assert "did not resolve" in inv.detail and recs[rid].status == FAIL, rid
    # a call that ran on what it was given and found no record is not an unresolved name
    assert validate._unresolved({"governance": {"limitations": [
        "No dataset is built on this runner, so nothing could be found: an empty result here "
        "says nothing about the relation."]}}) is None


def test_the_command_writes_every_report(tmp_path):
    from tcmstudio.cli import main
    out = tmp_path / "reports"
    assert main(["validate", "--out", str(out), "--kinds", "core", "native"]) == 0
    for name in ("agent_inventory", "tool_validation_report", "sandbox_execution_report",
                 "dependency_readiness_report", "database_readiness_report", "validation_summary",
                 "agent_regression_report"):
        assert (out / f"{name}.json").is_file(), name
    inv = json.loads((out / "agent_inventory.json").read_text())
    assert inv["counts"]["core_tools"] == len(CORE_TOOLS)
    assert inv["counts"]["entries"] == len(entries("runner", probe=False))
    report = json.loads((out / "tool_validation_report.json").read_text())
    assert report["summary"]["status"][FAIL] == 0
    assert report["summary"]["records"] == len(CORE_TOOLS) + 150 + len(validate.CHAINS)
    summary = json.loads((out / "validation_summary.json").read_text())
    assert summary["reports"]["tool_validation_report"]["PASS"] >= 74
    regression = json.loads((out / "agent_regression_report.json").read_text())
    assert regression["summary"]["records"] == 12 and len(regression["scenarios"]) == 12
    assert regression["summary"]["status"][FAIL] == 0
    sandbox = json.loads((out / "sandbox_execution_report.json").read_text())
    net = next(r for r in sandbox["records"] if r["component"] == "network")
    assert net["status"] == SKIPPED, "never 'online' without a request"
