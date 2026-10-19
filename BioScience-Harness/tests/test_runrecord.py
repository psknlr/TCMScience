"""Run records: stages timed and written as they happen, and the rule that makes a run
complete (every required stage succeeded, no check failed)."""

from __future__ import annotations

import json

import pytest

from bioagent import runrecord as RR

pytestmark = pytest.mark.unit


def _stages(out):
    return json.loads((out / RR.STAGES_FILE).read_text(encoding="utf-8"))


def test_a_stage_is_timed_and_written_while_it_runs(tmp_path):
    rec = RR.StageRecorder(tmp_path, "demo", plan=("one", "two"))
    doc = _stages(tmp_path)
    assert doc["schema"] == RR.SCHEMA and doc["planned"] == ["one", "two"]
    assert doc["stages"] == [] and doc["complete"] is None
    with rec.stage("one", engine="numpy", version="2") as st:
        assert _stages(tmp_path)["running"] == "one"          # a reader sees it running
        st.outputs = ["b.tsv", "a.tsv", "a.tsv"]
        st.check = "PASS"
    (one,) = _stages(tmp_path)["stages"]
    assert set(one) == {"name", "required", "status", "check", "reason", "engine", "version",
                        "device", "seconds", "outputs"}
    assert one["status"] == "succeeded" and one["device"] == "cpu"
    assert one["outputs"] == ["a.tsv", "b.tsv"] and one["seconds"] >= 0
    assert _stages(tmp_path)["running"] is None
    assert rec.finish() is True
    final = _stages(tmp_path)
    assert final["complete"] is True and final["problems"] == []
    assert not list(tmp_path.glob(".*.tmp"))                 # the writes were atomic


def test_an_exception_marks_the_stage_failed_and_is_raised_again(tmp_path):
    rec = RR.StageRecorder(tmp_path, "demo")
    with rec.stage("first"):
        pass
    with pytest.raises(ValueError, match="bad input"):
        with rec.stage("second"):
            raise ValueError("bad input")
    stages = _stages(tmp_path)["stages"]
    assert [s["status"] for s in stages] == ["succeeded", "failed"]
    assert stages[1]["reason"] == "ValueError: bad input"
    assert not rec.complete
    assert rec.problems() == ["stage second failed: ValueError: bad input"]


def test_the_completeness_rule():
    ok = {"name": "a", "required": True, "status": "succeeded", "check": "PASS"}
    assert RR.complete([ok])
    assert not RR.complete([])                                 # nothing recorded
    assert not RR.complete([ok, {"name": "b", "required": True, "status": "unavailable"}])
    # a stage left out by choice does not stop a run; a failed check does, required or not
    assert RR.complete([ok, {"name": "c", "required": False, "status": "skipped"}])
    assert not RR.complete([ok, {"name": "d", "required": False, "status": "succeeded",
                                 "check": "FAIL", "reason": "3.1 Å"}])
    assert RR.problems([ok, {"name": "d", "required": False, "status": "succeeded",
                             "check": "FAIL", "reason": "3.1 Å"}]) == [
        "stage d: its check failed: 3.1 Å"]


def test_statuses_and_checks_are_checked(tmp_path):
    rec = RR.StageRecorder(tmp_path, "demo")
    with pytest.raises(ValueError, match="status 'done'"):
        rec.record("x", "done")
    with pytest.raises(ValueError, match="check 'OK'"):
        with rec.stage("y") as st:
            st.check = "OK"


def test_stages_not_run_are_recorded_with_their_reason(tmp_path):
    rec = RR.StageRecorder(tmp_path, "demo")
    rec.not_applicable("integrate", "one batch only")
    rec.skip("trim", "trimmer 'none'")
    rec.unavailable("models", "no model is built")
    kinds = {s.name: (s.status, s.required, s.device) for s in rec.stages}
    assert kinds == {"integrate": ("not_applicable", False, None),
                     "trim": ("skipped", False, None), "models": ("unavailable", True, None)}
    assert not rec.complete
    lines = RR.stage_limitations(rec.stages)
    assert lines == ["Stage integrate was not applicable: one batch only.",
                     "Stage trim was not run: trimmer 'none'.",
                     "Stage models was unavailable here: no model is built."]


def test_record_problems_reads_a_run_json(tmp_path):
    (tmp_path / "a.tsv").write_text("x\n")
    stage = {"name": "s", "required": True, "status": "succeeded", "check": None,
             "outputs": ["a.tsv"]}
    manifest = {"stages": [stage], "complete": True, "outputs": {"a.tsv": "0" * 64}}
    assert RR.record_problems(manifest, tmp_path) == []
    assert RR.record_problems({"outputs": {}}, tmp_path) == []          # legacy: none
    named = {**manifest, "stages": [{**stage, "outputs": ["a.tsv", "gone.tsv"]}]}
    assert RR.record_problems(named, tmp_path) == [
        "stage s: output gone.tsv is not among the run's recorded outputs"]
    (tmp_path / "a.tsv").unlink()
    assert RR.record_problems(manifest, tmp_path) == ["stage s: output a.tsv is missing"]
    assert "run.json records the run as incomplete" in RR.record_problems(
        {**manifest, "complete": False}, tmp_path)
    assert "run.json says complete, but its stages do not make it so" in RR.record_problems(
        {**manifest, "stages": [{**stage, "status": "failed"}]}, tmp_path)


def test_limitations_are_one_per_line_and_deterministic(tmp_path):
    lines = RR.write_limitations(tmp_path, ["A first limit.", "A  second\nlimit.",
                                            "A first limit.", ""])
    assert lines == ["A first limit.", "A second limit."]
    text = (tmp_path / RR.LIMITATIONS_FILE).read_text(encoding="utf-8")
    assert text == "A first limit.\nA second limit.\n"
    assert RR.read_limitations(tmp_path / RR.LIMITATIONS_FILE) == lines
    assert RR.read_limitations(tmp_path / "none.md") == []


def test_a_reference_dataset_is_matched_by_its_content(monkeypatch, tmp_path):
    refs = tmp_path / "refs.json"
    refs.write_text(json.dumps({"references": [
        {"id": "world", "version": "1", "pipeline": "p", "kind": "simulated",
         "label": "simulated, known answer", "statement": "Not public data.",
         "inputs": {"a": "1" * 64, "b": "2" * 64}}]}))
    monkeypatch.setattr(RR, "REFERENCES_FILE", refs)
    found = RR.match_reference("p", {"x": "2" * 64, "y": "1" * 64})       # names aside
    assert found == {"id": "world", "version": "1", "kind": "simulated",
                     "label": "simulated, known answer", "statement": "Not public data."}
    assert RR.match_reference("p", {"x": "1" * 64}) is None              # a subset is not it
    assert RR.match_reference("q", {"x": "2" * 64, "y": "1" * 64}) is None
    assert RR.content_digest({"b": "2", "a": "1"}) == RR.content_digest({"a": "1", "b": "2"})


def test_the_shipped_reference_manifest_is_well_formed():
    doc = RR.pipeline_references()
    assert doc["schema"] == "bioagent.pipeline-references/1" and doc["version"]
    pipelines = {r["pipeline"] for r in doc["references"]}
    assert pipelines == {"bioagent.omics.rnaseq", "bioagent.omics.rnaseq_counts",
                         "bioagent.omics.scrna", "bioagent.docking", "bioagent.structure.fold",
                         "bioagent.admet"}
    for ref in doc["references"]:
        for key in ("id", "version", "kind", "label", "statement", "source", "licence",
                    "sha256", "inputs", "parameters", "expected", "status", "check"):
            assert key in ref, (ref["id"], key)
        assert ref["kind"] in ("simulated", "public")
        if ref["kind"] == "simulated":
            # never presented as public data
            assert ref["label"] == "simulated, known answer"
            assert "not public data" in ref["statement"]
            assert ref["sha256"] == RR.content_digest(ref["inputs"])
        assert all(len(d) == 64 for d in ref["inputs"].values())


def test_the_null_recorder_keeps_nothing():
    rec = RR.NullRecorder()
    with rec.stage("x") as st:
        st.reason = "anything"
    rec.not_applicable("y", "why")
    assert rec.stages == []
