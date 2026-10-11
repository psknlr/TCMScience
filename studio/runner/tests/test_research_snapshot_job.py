"""The research.snapshot job: the snapshots research.run reads, built on the runner.

The build itself is tested in BioScience-Harness (tests/test_snapshot_pipeline.py, against
files served over HTTP); here, what the runner adds: the parameters it accepts and refuses,
the command it runs, where the snapshots land (where research.run looks), and how the
report becomes the job's result.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tcmstudio.kinds import (KINDS, OUTPUT_TOKEN, BadParams, PrepareContext, StartContext,
                             snapshot_paths)
from test_server_support import running_server


def _ctx(tmp_path: Path) -> PrepareContext:
    lake = tmp_path / "lake"
    return PrepareContext(job_id="j1", job_dir=tmp_path / "job", project_id="p",
                          paths={"projects": tmp_path / "projects"},
                          data_env={"BIOAGENT_DATA_LAKE": str(lake),
                                    "BIOAGENT_TCMDB": str(lake / "tcmdb")},
                          find=lambda value: None, settings={})


def test_it_builds_into_the_ledger_research_run_reads(tmp_path):
    kind = KINDS["research.snapshot"]
    ctx = _ctx(tmp_path)
    prepared = kind.prepare({}, ctx)
    root, ledger = snapshot_paths(ctx.data_env)
    assert prepared["snapshots"] == str(root) and prepared["ledger"] == str(ledger)
    assert prepared["sources"] == ["npass", "cmaup", "lotus"] and prepared["network"] is True
    assert kind.needs_network(prepared), "it downloads: the runner's network gate applies"
    start = StartContext(job_id="j1", job_dir=tmp_path / "job", project_id="p", paths={},
                         data_env=ctx.data_env, settings={}, device="cpu", threads=1)
    argv = kind.command({**prepared, "formulas": ["桂枝汤《伤寒论》"]}, start)
    assert argv[3:6] == ["bioagent.cli", "snapshots", "build"]
    assert f"--report={OUTPUT_TOKEN}/snapshot_report.json" in argv
    assert f"--ledger={ledger}" in argv and "--formula=桂枝汤《伤寒论》" in argv
    narrow = kind.prepare({"sources": ["lotus", "lotus"], "network": False}, ctx)
    assert narrow["sources"] == ["lotus"]
    assert "--no-network" in kind.command(narrow, start)


def test_a_formula_the_research_loop_would_refuse_is_refused_at_submission(tmp_path):
    kind = KINDS["research.snapshot"]
    with pytest.raises(BadParams, match="different compositions"):
        kind.prepare({"formulas": ["桂枝汤"]}, _ctx(tmp_path))
    assert kind.prepare({"formulas": ["桂枝汤《伤寒论》"]}, _ctx(tmp_path))["formulas"]
    with pytest.raises(BadParams):
        kind.prepare({"sources": ["chembl"]}, _ctx(tmp_path))
    with pytest.raises(BadParams, match="at most 20"):
        kind.prepare({"formulas": [f"方{i}《书》" for i in range(21)]}, _ctx(tmp_path))


def test_the_report_becomes_the_job_result_and_a_missing_ledger_fails_it(tmp_path):
    kind = KINDS["research.snapshot"]
    out = tmp_path / "job" / "out"
    out.mkdir(parents=True)
    (out / "snapshot_report.json").write_text(json.dumps({
        "schema": "tcmscience.snapshots/1", "status": "UNVERIFIED",
        "stages": [{"stage": "acquisition", "status": "PASS"},
                   {"stage": "integrity", "status": "UNVERIFIED"}],
        "snapshots": {"npass": {"snapshot_id": "s1", "version": "2.0+subset-ab", "qc": "pass",
                                "edges": 12}}}))
    result = kind.result(tmp_path / "job", {})
    assert result["status"] == "UNVERIFIED" and result["stages"]["integrity"] == "UNVERIFIED"
    assert result["snapshots"]["npass"]["id"] == "s1"
    prepared = kind.prepare({}, _ctx(tmp_path))
    assert kind.verify(prepared, {}) == [f"no ledger at {prepared['ledger']} after the build"]
    Path(prepared["ledger"]).parent.mkdir(parents=True)
    Path(prepared["ledger"]).write_text("")
    assert kind.verify(prepared, {}) == []


def test_with_the_runner_offline_it_is_refused_with_the_reason(tmp_path):
    with running_server(tmp_path / "home", network=False) as (_, c):
        status, body = c.post("/api/jobs", {"kind": "research.snapshot", "params": {}})
        assert status == 422 and body["error"]["type"] == "network_off"
