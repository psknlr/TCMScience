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


def test_the_command_writes_every_report(tmp_path):
    from tcmstudio.cli import main
    out = tmp_path / "reports"
    assert main(["validate", "--out", str(out), "--kinds", "core", "native"]) == 0
    for name in ("agent_inventory", "tool_validation_report", "sandbox_execution_report",
                 "dependency_readiness_report", "database_readiness_report", "validation_summary"):
        assert (out / f"{name}.json").is_file(), name
    inv = json.loads((out / "agent_inventory.json").read_text())
    assert inv["counts"]["core_tools"] == len(CORE_TOOLS)
    assert inv["counts"]["entries"] == len(entries("runner", probe=False))
    report = json.loads((out / "tool_validation_report.json").read_text())
    assert report["summary"]["status"][FAIL] == 0
    assert report["summary"]["records"] == len(CORE_TOOLS) + 150 + len(validate.CHAINS)
    summary = json.loads((out / "validation_summary.json").read_text())
    assert summary["reports"]["tool_validation_report"]["PASS"] >= 74
    sandbox = json.loads((out / "sandbox_execution_report.json").read_text())
    net = next(r for r in sandbox["records"] if r["component"] == "network")
    assert net["status"] == SKIPPED, "never 'online' without a request"
