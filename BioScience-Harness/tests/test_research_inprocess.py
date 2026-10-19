"""The governed analysis run in this process (``run_research(isolate=False)``), the way the
browser's Python runs it, against the default isolated run.

The browser cannot start a process, so there the research loop's tool calls run in the
kernel's own process. They must still be the compiled program's calls, made through the
same PSH broker and recorded in the same audit chain, and they must write the same bytes;
the record must say where they ran and never claim a process isolation they did not have.
On the planted worlds (bioagent.research.planted): fixtures, not measurements.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from bioagent.research import default_protocol, parse_question, run_research
from bioagent.research.loop import research_summary
from bioagent.research.planted import build_world
from bioagent.sources.ledger import SnapshotLedger

from test_np_workflow import PLANTED, QUESTION

#: what a browser run and a runner run must agree on byte for byte
SAME_BYTES = ("compounds.tsv", "targets.tsv", "disease_targets.tsv", "enrichment.tsv",
              "network.tsv", "limitations.md", "report.html")
TOOL = "research.tool.network_pharmacology"


def _run(tmp: Path, root: Path, ledger: Path, tag: str, *, out: str = "", **kw):
    q = parse_question(QUESTION)
    return run_research(q, protocol=default_protocol(q, parameters=PLANTED),
                        snapshot_root=root, ledger_path=ledger, state_dir=tmp / f"state-{tag}",
                        output_dir=tmp / f"out-{out or tag}", **kw)


def _world(tmp: Path, scenario: str) -> tuple[Path, Path]:
    ledger = SnapshotLedger(tmp / "w" / "ledger.jsonl")
    build_world(scenario, tmp / "w" / "snap", ledger=ledger)
    return tmp / "w" / "snap", ledger.path


@pytest.fixture(scope="module", params=["selective", "coverage"])
def pair(request, tmp_path_factory):
    """One planted world, run by default (isolated) and with ``isolate=False``."""
    tmp = tmp_path_factory.mktemp(request.param)
    root, ledger = _world(tmp, request.param)
    return (tmp, root, ledger, _run(tmp, root, ledger, "isolated"),
            _run(tmp, root, ledger, "in-process", isolate=False))


def _records(run) -> list:
    from psh import PSHConfig, TrustedKernel
    kernel = TrustedKernel(PSHConfig(state_dir=Path(run.state_dir) / "psh").ensure_dirs())
    try:
        assert kernel.events.verify(), "the audit chain verifies"
        return list(kernel.events.records())
    finally:
        kernel.close()


def _manifest(run) -> dict:
    return json.loads((Path(run.output_dir) / "run_manifest.json").read_text(encoding="utf-8"))


def test_both_ways_write_the_same_tables_report_and_result(pair):
    _, _, _, isolated, in_process = pair
    a, b = Path(isolated.output_dir), Path(in_process.output_dir)
    for name in SAME_BYTES:
        assert (a / name).read_bytes() == (b / name).read_bytes(), name
    assert isolated.artifact.provenance["result_digest"] == \
        in_process.artifact.provenance["result_digest"]
    assert _manifest(isolated)["result_digest"] == _manifest(in_process)["result_digest"]
    assert isolated.hypotheses == in_process.hypotheses
    assert isolated.released and in_process.released


def test_the_artifact_says_where_the_calls_ran_and_both_are_governed(pair):
    _, _, _, isolated, in_process = pair
    for run, where in ((isolated, "process"), (in_process, "in_process")):
        prov = run.artifact.provenance
        assert prov["governed_execution"] is True and prov["profile"] == "trusted_local"
        assert {stage: r["isolation"] for stage, r in prov["execution"].items()} == \
            {"analyse": where, "rebut": where}
        assert all(r["mode"] == "governed" for r in prov["execution"].values())
        execution = _manifest(run)["execution"]
        assert execution["governed_execution"] is True
    assert isolated.artifact.provenance["tool_calls"] == \
        in_process.artifact.provenance["tool_calls"] >= 1


def test_in_process_calls_go_through_the_broker_and_the_audit_chain(pair):
    _, _, _, isolated, in_process = pair
    records = _records(in_process)
    types = [r.event_type for r in records]
    calls = [r for r in records if r.event_type == "tool_call"]
    # every call of the compiled program, gated and recorded, none of them in a child
    assert len(calls) == in_process.artifact.provenance["tool_calls"]
    assert {r.component_id for r in calls} == {TOOL}
    assert {(r.detail or {}).get("execution") for r in calls} == {"in_process"}
    assert "isolated_run" not in types
    assert types.count("egress_decision") == len(calls)
    assert types.index("research_program_compiled") < types.index("tool_call")
    admitted = next(r for r in records if r.event_type == "bioscience_component_admitted")
    assert admitted.detail["isolated"] is False
    done = next(r for r in records if r.event_type == "research_analysis_completed")
    assert done.detail["execution"]["isolation"] == "in_process"
    # the default run: each call in a child process, and recorded so
    records = _records(isolated)
    calls = [r for r in records if r.event_type == "tool_call"]
    assert {(r.detail or {}).get("execution") for r in calls} == {"isolated"}
    assert [r.event_type for r in records].count("isolated_run") == len(calls)


def test_a_stage_computed_one_way_is_not_reused_by_a_run_the_other_way(tmp_path):
    root, ledger = _world(tmp_path, "selective")
    first = _run(tmp_path, root, ledger, "s", isolate=False)
    again = _run(tmp_path, root, ledger, "s", out="again", isolate=False)
    assert {"analyse", "rebut"} <= set(again.resumed_stages)
    # the same state, isolated: the stages are computed again, not reported as isolated
    other = _run(tmp_path, root, ledger, "s", out="other")
    assert not {"analyse", "rebut"} & set(other.resumed_stages)
    assert {r["isolation"] for r in other.artifact.provenance["execution"].values()} == \
        {"process"}
    assert other.artifact.provenance["result_digest"] == \
        first.artifact.provenance["result_digest"]


def test_the_manifest_records_the_interpreter_it_ran_in(pair):
    import platform
    _, _, _, isolated, in_process = pair
    for run in (isolated, in_process):
        assert _manifest(run)["execution"]["runtime"] == {
            "python": platform.python_version(), "implementation": sys.implementation.name,
            "platform": sys.platform, "machine": platform.machine()}
    a, b = _manifest(isolated), _manifest(in_process)
    a.pop("execution")
    b.pop("execution")
    assert a == b, "only the execution block may differ"


def test_a_profile_that_requires_isolation_refuses_to_run_in_process(tmp_path):
    root, ledger = _world(tmp_path, "selective")
    with pytest.raises(ValueError, match="requires isolation"):
        _run(tmp_path, root, ledger, "r", profile="restricted_research", isolate=False)
    assert not (tmp_path / "out-r" / "run_manifest.json").exists()


def test_isolate_says_nothing_about_an_injected_analysis(tmp_path):
    from bioagent.analysis.network_pharmacology import run_network_pharmacology
    root, ledger = _world(tmp_path, "selective")
    with pytest.raises(ValueError, match="not governed"):
        _run(tmp_path, root, ledger, "x", analyse=run_network_pharmacology, isolate=False)


def test_research_summary_is_what_the_command_prints(tmp_path, capsys, monkeypatch):
    import bioagent.research as research
    from bioagent.cli import main

    root, ledger = _world(tmp_path, "selective")
    research_run, ran = research.run_research, []

    def keep(*args, **kw):                   # the run the command made, to summarise again
        ran.append(research_run(*args, **kw))
        return ran[-1]

    monkeypatch.setattr(research, "run_research", keep)
    code = main(["research", QUESTION, "--snapshots", str(root), "--ledger", str(ledger),
                 "--state-dir", str(tmp_path / "st"), "--out", str(tmp_path / "o"),
                 "--background", "assayed", "--permutations", "200"])
    printed = capsys.readouterr()
    summary, failed = research_summary(ran[0], parse_question(QUESTION))
    assert printed.out == json.dumps(summary, indent=2, ensure_ascii=False) + "\n"
    assert failed == [] and printed.err == ""
    assert code == (0 if ran[0].released else 1) == 0
    assert summary["governed_execution"] is True and len(summary["stages"]) == 16


def test_research_summary_reports_a_manifest_it_cannot_read(pair):
    _, _, _, _, in_process = pair
    manifest = Path(in_process.output_dir) / "run_manifest.json"
    text = manifest.read_text(encoding="utf-8")
    try:
        manifest.write_text("{", encoding="utf-8")
        summary, failed = research_summary(in_process, parse_question(QUESTION))
    finally:
        manifest.write_text(text, encoding="utf-8")
    assert summary["stages"] == [] and summary["complete"] is None
    assert len(failed) == 1 and failed[0].startswith("run_manifest.json could not be read")


def test_writing_the_exports_loads_neither_numpy_nor_scipy(pair, tmp_path):
    """The renderer is bioagent.report_html now, not omics.rnaseq (which imports both)."""
    _, _, _, _, in_process = pair
    import bioagent
    package_root = str(Path(bioagent.__file__).resolve().parents[1])
    script = f"""
import json, sys
sys.path.insert(0, {package_root!r})
from pathlib import Path
from bioagent.analysis.np_exports import write_exports, write_manifest
from bioagent.sources.herbs import GEGEN_QINLIAN
result = json.loads(Path({str(Path(in_process.output_dir) / 'result.json')!r}).read_text("utf-8"))
exports = write_exports(result, {str(tmp_path / 'x')!r}, formula=GEGEN_QINLIAN, question="q")
write_manifest(exports)
print(json.dumps({{"loaded": sorted(m for m in ("numpy", "scipy") if m in sys.modules),
                  "files": sorted(exports.files)}}))
"""
    res = subprocess.run([sys.executable, "-I", "-c", script], capture_output=True, text=True,
                         timeout=300, cwd=tmp_path)
    assert res.returncode == 0, res.stderr[-2000:]
    out = json.loads(res.stdout.strip().splitlines()[-1])
    assert out["loaded"] == []
    assert "report.html" in out["files"]
    assert (tmp_path / "x" / "report.html").read_text(encoding="utf-8").startswith(
        "<!doctype html>")


def test_the_old_renderer_names_are_the_new_functions():
    from bioagent import report_html
    from bioagent.omics import rnaseq
    assert rnaseq._html is report_html.render and rnaseq._inline is report_html.inline
