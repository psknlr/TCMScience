"""Jobs on the runner: a real RNA-seq run end to end, the governed skill worker, the queue,
cancellation, re-attachment after a restart, and the refusals."""

from __future__ import annotations

import http.client
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tcmstudio import devices as dev

from test_server_support import running_server, test_kinds

HARNESS_TESTS = Path(__file__).resolve().parents[3] / "BioScience-Harness" / "tests"


def _wait_state(c, job_id, states, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = c.get(f"/api/jobs/{job_id}")[1]
        if job["state"] in states:
            return job
        time.sleep(0.1)
    raise AssertionError(f"{job_id} never reached {states}: {job['state']}")


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    if not (HARNESS_TESTS / "omics_world.py").is_file():
        pytest.skip("BioScience-Harness tests (omics_world) are not beside this checkout")
    sys.path.insert(0, str(HARNESS_TESTS))
    try:
        import omics_world
    finally:
        sys.path.remove(str(HARNESS_TESTS))
    return omics_world.make_world(tmp_path_factory.mktemp("world"))


# ------------------------------------------------------------------- the real pipeline

def test_rnaseq_job_lifecycle_with_events_files_and_verification(tmp_path, world):
    with running_server(tmp_path / "home") as (srv, c):
        ids = {}
        for path in sorted(world.root.iterdir()):
            if path.name.endswith((".fastq.gz", ".fa", ".gtf", ".csv")):
                ids[path.name] = c.upload(path)["id"]
        # the sheet names its FASTQs by file name: they resolve to the uploads
        status, job = c.post("/api/jobs", {
            "kind": "pipeline.rnaseq", "project_id": "p-omics", "submission_id": "sub-rna-1",
            "params": {"samples": ids["samples.csv"], "transcripts": ids["transcripts.fa"],
                       "annotation": ids["genes.gtf"], "design": "~ condition",
                       "contrast": "condition,treated,control"}})
        assert status == 201, job
        assert job["state"] in ("queued", "running") and job["kind"] == "pipeline.rnaseq"
        assert job["files_url"] == f"/api/jobs/{job['id']}/files"
        assert job["inputs"]["samples"]["upload"] == ids["samples.csv"]
        assert job["result"] is None and job["outcome"] is None
        # the same submission id names the same job
        status, again = c.post("/api/jobs", {"kind": "pipeline.rnaseq", "submission_id": "sub-rna-1",
                                             "params": {"samples": ids["samples.csv"]}})
        assert status == 200 and again["id"] == job["id"]

        events = c.events(job["id"])
        kinds = [e for e, _ in events]
        assert kinds[0] == "state" and kinds[-1] == "done"
        assert "log" in kinds and "artefact" in kinds
        done = events[-1][1]
        assert done["state"] == "succeeded", done["outcome"]
        artefact_paths = {d["path"] for e, d in events if e == "artefact"}
        assert {"run.json", "report.html", "deseq2_results.tsv"} <= artefact_paths

        status, final = c.get(f"/api/jobs/{job['id']}")
        assert final["state"] == "succeeded"
        assert final["device"] == "cpu" and final["started_at"] and final["finished_at"]
        assert final["outcome"]["status"] == "succeeded" and final["outcome"]["exit_code"] == 0
        names = {a["name"]: a for a in final["artefacts"]}
        assert set(names) == {"run", "report", "results", "limitations"}
        out = srv.home / "jobs" / job["id"] / "out"
        import hashlib
        for a in final["artefacts"]:
            assert hashlib.sha256((out / a["path"]).read_bytes()).hexdigest() == a["sha256"]
        de = final["result"]["differential_expression"]
        assert de["significant"] > 0 and de["tested"] == 30
        # what the collection checked, and what the kind requires, are on the record
        assert final["outcome"]["checks"] == ["controller_digests", "validator:run"]
        assert final["outcome"]["expected"] == ["run", "report", "results", "limitations"]
        # the pipeline's own stages: progress named them while it ran, then "verifying"
        stage_names = [s["name"] for s in final["stages"]]
        assert stage_names[:2] == ["validate_reference", "ingest"]
        assert stage_names[-2:] == ["report", "verify"]
        assert all(s["status"] == "succeeded" and s["device"] == "cpu"
                   for s in final["stages"])
        seen = [d.get("stage") for e, d in events if e == "progress" and d]
        assert "verifying" in seen and set(seen) & set(stage_names), seen
        assert all(d["fraction"] < 1 for e, d in events if e == "progress" and d.get("fraction"))
        lines = (out / "limitations.md").read_text().splitlines()
        assert final["limitations"] == lines and any("PyDESeq2" in x for x in lines)
        # the job receipt, written at collection, its digest in the outcome
        receipt = final["outcome"]["receipt"]
        assert receipt["path"] == "job_receipt.json" == final["receipt"]["path"]
        assert hashlib.sha256((out / "job_receipt.json").read_bytes()).hexdigest() == \
            receipt["sha256"]
        doc = srv.jobs.job_receipt(job["id"])
        assert doc["schema"] == "tcmstudio.job-receipt/1" and doc["job_id"] == job["id"]
        assert doc["kind"] == "pipeline.rnaseq" and doc["state"] == "succeeded"
        assert len(doc["params_sha256"]) == 64 and doc["complete"] is True
        assert doc["device"]["assigned"] == "cpu" and doc["device"]["evidence"] == "selected"
        assert set(doc["device"]["stages"].values()) == {"cpu"}
        assert doc["artefacts"] == final["artefacts"] and doc["missing"] == []
        assert doc["checks"] == ["controller_digests", "validator:run"]
        assert [s["name"] for s in doc["stages"]] == stage_names
        assert doc["limitations"] == lines and doc["duration_s"] > 0
        assert doc["run_json_sha256"] == names["run"]["sha256"]
        assert doc["resources"]["peak_rss_mb"] > 10
        assert "RUSAGE_CHILDREN" in doc["resources"]["source"]
        assert {e["id"] for e in doc["engine"]} >= {"builtin-quant", "builtin-de"}
        assert doc["inputs"]["samples"]["sha256"] == final["inputs"]["samples"]["sha256"]
        assert "name" not in doc["inputs"]["samples"]               # digests only
        st = c.call("job_status", {"job_id": job["id"]})
        res = st["receipt"]["result"]
        assert (res["kind"], res["outcome"], res["assurance"]) == (
            "computation", "validated", "outputs_verified")
        assert [ch["id"] for ch in res["checks"]] == ["controller_digests", "validator:run",
                                                      "run_manifest"]
        assert res["artifacts"] == {"expected": ["run", "report", "results", "limitations"],
                                    "missing": [], "hash_mismatch": []}
        # a succeeded pipeline job shows its own limitations and its stages
        assert any("does not establish a mechanism" in x
                   for x in st["governance"]["limitations"])
        assert [s["name"] for s in st["result"]["stages"]] == stage_names
        impl = st["receipt"]["execution"]["implementation"]
        assert impl["job_kind"] == "pipeline.rnaseq" and impl["pipeline"] == "bioagent.omics.rnaseq"
        assert len(impl["code_digest"]) == 64
        engine = st["receipt"]["execution"]["engine"]          # the run's own choice, not "auto"
        assert engine["id"] == "builtin-quant" and engine["version"]
        assert [e["id"] for e in engine["with"]] == ["builtin-trim", "builtin-de"]
        inputs = {d["param"]: d for d in st["receipt"]["provenance"]["datasets"]
                  if d.get("role") == "input"}
        assert inputs["samples"]["sha256"] == final["inputs"]["samples"]["sha256"]
        # run.json is read only at the digest recorded when the job was collected
        manifest = srv.jobs.run_manifest(job["id"])
        assert manifest["pipeline"] == "bioagent.omics.rnaseq"
        run_json = srv.home / "jobs" / job["id"] / "out" / "run.json"
        original = run_json.read_bytes()
        run_json.write_text(json.dumps({**manifest, "code_digest": "0" * 64}))
        with pytest.raises(LookupError, match="no longer has the digest"):
            srv.jobs.run_manifest(job["id"])
        st = c.call("job_status", {"job_id": job["id"]})
        res = st["receipt"]["result"]
        assert res["artifacts"]["hash_mismatch"] == ["run"] and res["assurance"] == "none"
        assert {"id": "run_manifest", "ok": False} == {k: v for k, v in res["checks"][-1].items()
                                                       if k != "detail"}
        assert "code_digest" not in st["receipt"]["execution"]["implementation"]
        run_json.write_bytes(original)
        assert "--threads=4" in " ".join(final["command"]) or "--threads=" in " ".join(final["command"])
        assert final["command"][1:4] == ["-I", "-m", "bioagent.cli"]

        status, files = c.get(f"/api/jobs/{job['id']}/files")
        listed = {f["path"]: f for f in files["files"]}
        assert {"run.json", "report.html", "deseq2_results.tsv", "job_receipt.json",
                "stages.json", "limitations.md"} <= set(listed)
        assert listed["job_receipt.json"]["sha256"] == receipt["sha256"]
        assert listed["report.html"]["media_type"] == "text/html"
        status, h, body = c.raw("GET", f"/api/jobs/{job['id']}/files/report.html",
                                token=False, headers={})
        assert status == 401
        status, h, body = c.raw("GET", f"/api/jobs/{job['id']}/files/report.html?token={c.token}",
                                token=False)
        assert status == 200 and b"<html" in body.lower()
        assert h["content-security-policy"] == "sandbox"
        assert h["cross-origin-resource-policy"] == "cross-origin"
        status, h, body = c.raw("GET", f"/api/jobs/{job['id']}/files/run.json?download=1")
        assert status == 200 and h["content-disposition"].startswith("attachment")
        for bad in ("../request.json", "..%2Frequest.json", "%2E%2E/job.json", "/etc/passwd"):
            assert c.raw("GET", f"/api/jobs/{job['id']}/files/{bad}")[0] == 404, bad

        # the outcome is persisted beside the job
        cached = json.loads((srv.home / "jobs" / job["id"] / "outcome.json").read_text())
        assert cached["state"] == "succeeded"
        # a finished job's stream is its state and done, at once
        late = c.events(job["id"], use_query_token=False)
        assert [e for e, _ in late if e in ("state", "done")] == ["state", "done"]
        assert c.json("DELETE", f"/api/jobs/{job['id']}")[0] == 409
        listed = c.get("/api/jobs?project_id=p-omics")[1]["jobs"]
        assert [j["id"] for j in listed] == [job["id"]]
        assert c.get("/api/jobs?project_id=other")[1]["jobs"] == []
        assert c.get("/api/jobs?state=succeeded")[1]["jobs"][0]["id"] == job["id"]


def test_rnaseq_refuses_a_sheet_whose_files_were_not_uploaded(tmp_path, world):
    with running_server(tmp_path / "home") as (srv, c):
        sheet = c.upload(world.sheet)["id"]
        tx = c.upload(world.transcripts)["id"]
        status, body = c.post("/api/jobs", {"kind": "pipeline.rnaseq",
                                            "params": {"samples": sheet, "transcripts": tx}})
        assert status == 400 and body["error"]["type"] == "bad_arguments"
        assert "fastq_1" in body["error"]["message"]
        assert not any(p.name.startswith("j_") for p in (srv.home / "jobs").iterdir())


# --------------------------------------------------------- the dispatcher and skill.run

def test_run_pipeline_and_job_status_through_the_dispatcher(tmp_path):
    with running_server(tmp_path / "home") as (srv, c):
        env = c.call("call_tool", {"tool": "job.skill.run", "arguments": {
            "skill_id": "assess-tcm-safety",
            "arguments": {"subject": "甘草", "co_administered": ["甘遂"]}}},
            project_id="p-skill")
        assert env["status"] == "job_submitted", env
        job_id = env["job"]["id"]
        assert env["job"]["kind"] == "skill.run"
        assert env["receipt"]["result"]["outcome"] in ("queued", "running")
        assert env["receipt"]["result"]["kind"] is None, "no result yet"
        assert env["receipt"]["execution"]["implementation"]["job_kind"] == "skill.run"
        # a safety-record lookup run as a job is pending work, not a prediction
        assert env["governance"]["limitations"] == [
            "A job that has not succeeded is pending work, not a result."]
        assert "Job" in env["summary_en"] and "已提交任务" in env["summary"]
        deadline = time.monotonic() + 120
        while True:
            st = c.call("job_status", {"job_id": job_id, "wait_s": 10})
            assert st["status"] == "succeeded"
            if st["result"]["state"] in ("succeeded", "failed", "cancelled"):
                break
            assert "尚无结果" in st["summary"]
            assert time.monotonic() < deadline
        job = st["result"]
        assert job["state"] == "succeeded", job["outcome"]
        assert job["result"]["released"] is True and job["result"]["status"] == "succeeded"
        assert job["result"]["content_hash"]
        out = srv.home / "jobs" / job_id / "out"
        envelope = json.loads((out / "envelope.json").read_text())
        assert envelope["receipt"]["project_id"] == "p-skill"
        assert envelope["receipt"]["durable"] is True
        assert (out / "outputs" / "safety.json").is_file()
        # the run extended the project's own audit chain
        assert (srv.home / "projects" / "p-skill" / "psh" / "events.db").is_file()
        # the job's status carries the governed run's own governance: its release verdict,
        # claims, evidence and limitations, as a run in the thread shows them
        g = st["governance"]
        assert g["kind"] == "skill" and g["released"] is True
        assert g["verdict"]["states"] and all(g["verdict"]["states"].values())
        assert g["claims"] and g["evidence"] and g["artifact"]["skill_id"] == "assess-tcm-safety"
        assert g["limitations"] == envelope["governance"]["limitations"]
        assert any("not evidence of safety" in x for x in g["limitations"])
        assert st["receipt"]["content_hash"] == envelope["receipt"]["content_hash"]
        assert st["receipt"]["skill_pinned"] is True
        assert "输出哈希已核验" in st["summary"] and "已准予发布" in st["summary"]
        assert "已完成并核验" not in st["summary"]
        assert "release authorized" in st["summary_en"]
        assert "Release: released" in st["text"] and "Claims" in st["text"]
        # receipt/1: the governed run's six states passed (a record, release authorized);
        # the job's own checks follow them
        res = st["receipt"]["result"]
        assert (res["kind"], res["outcome"], res["assurance"]) == (
            "record", "validated", "release_authorized")
        ids = [ch["id"] for ch in res["checks"]]
        assert ids[:8] == ["schema_valid", "evidence_verified", "outputs_verified",
                           "execution_declared", "execution_attested", "release_authorized",
                           "skill_pinned", "outputs_present"]
        assert ids[8:] == ["controller_digests", "validator:envelope", "envelope_digest"]
        impl = st["receipt"]["execution"]["implementation"]
        assert impl["content_hash"] == envelope["receipt"]["content_hash"] and impl["pinned"] is True
        assert impl["job_kind"] == "skill.run" and impl["job_id"] == job_id
        assert any(d["id"] == "tcmscience.tcm.seed" for d in st["receipt"]["provenance"]["datasets"])
        assert st["receipt"]["execution"]["device"]["assigned"] == "cpu"
        # read back only at the digest recorded when the job was collected
        (out / "envelope.json").write_text(json.dumps({**envelope, "summary": "edited"}))
        st = c.call("job_status", {"job_id": job_id})
        assert st["governance"]["released"] is False and not st["governance"]["claims"]
        assert "digest" in st["governance"]["limitations"][0]
        assert "未准予发布" in st["summary"]
        res = st["receipt"]["result"]
        assert res["artifacts"]["hash_mismatch"] == ["envelope"] and res["assurance"] == "none"
        # bad arguments are caught before anything runs
        env = c.call("run_pipeline", {"pipeline": "rnaseq", "arguments": {}})
        assert env["status"] == "failed" and env["error"]["type"] == "bad_arguments"
        env = c.call("call_tool", {"tool": "job.skill.run", "arguments": {
            "skill_id": "dock-ligands", "arguments": {"receptor": "x", "ligands": "CCO"},
            "allow_unpinned": False}})
        assert env["status"] == "failed"


def test_an_unpinned_skill_job_shows_that_it_was_not_released(tmp_path):
    from conftest import INTAKE
    with running_server(tmp_path / "home") as (srv, c):
        env = c.call("call_tool", {"tool": "job.skill.run", "arguments": {
            "skill_id": "draft-tcm-prescription", "allow_unpinned": True,
            "arguments": {"intake": INTAKE}}}, project_id="p-unpinned")
        assert env["status"] == "job_submitted", env
        job_id = env["job"]["id"]
        st = c.call("job_status", {"job_id": job_id, "wait_s": 30})
        deadline = time.monotonic() + 120
        while st["result"]["state"] not in ("succeeded", "failed", "cancelled"):
            assert time.monotonic() < deadline
            st = c.call("job_status", {"job_id": job_id, "wait_s": 10})
        assert st["result"]["state"] == "succeeded", st["result"]["outcome"]
        g = st["governance"]
        # a development run: recorded, never released, and the job says so
        assert g["kind"] == "skill" and g["released"] is False
        assert "UNPINNED" in [r["code"] for r in g["refusals"]]
        assert not all(g["verdict"]["states"].values())
        assert "未准予发布" in st["summary"] and "not released" in st["summary_en"]
        assert "Release: NOT released" in st["text"]
        assert st["receipt"]["skill_pinned"] is False
        assert any("draft for a licensed TCM practitioner" in x for x in g["limitations"])


def test_skill_run_refuses_unpinned_without_consent(tmp_path):
    with running_server(tmp_path / "home") as (_, c):
        status, body = c.post("/api/jobs", {"kind": "skill.run", "params": {
            "skill_id": "draft-tcm-prescription", "arguments": {}}})
        assert status == 400 and "allow_unpinned" in body["error"]["message"]
        status, body = c.post("/api/jobs", {"kind": "skill.run", "params": {
            "skill_id": "assess-tcm-safety", "arguments": {"subjekt": "甘草"}}})
        assert status == 400 and "subject" in json.dumps(body)


# ---------------------------------------------------------------- queue and cancel

def test_cancel_a_running_job_and_a_queued_one(tmp_path):
    with running_server(tmp_path / "home", kinds=test_kinds(), max_jobs=1) as (_, c):
        status, first = c.post("/api/jobs", {"kind": "test.sleep",
                                             "params": {"seconds": 60, "lines": 60}})
        assert status == 201
        status, second = c.post("/api/jobs", {"kind": "test.sleep", "params": {"seconds": 1}})
        assert second["state"] == "queued"
        _wait_state(c, first["id"], ("running",))
        assert c.get(f"/api/jobs/{second['id']}")[1]["state"] == "queued"
        status, cancelled = c.json("DELETE", f"/api/jobs/{second['id']}")
        assert status == 200 and cancelled["state"] == "cancelled"
        status, cancelled = c.json("DELETE", f"/api/jobs/{first['id']}")
        assert status == 200 and cancelled["state"] == "cancelled", cancelled
        assert cancelled["outcome"]["status"] == "cancelled"
        status, body = c.json("DELETE", f"/api/jobs/{first['id']}")
        assert status == 409 and body["error"]["type"] == "conflict"
        assert c.json("DELETE", "/api/jobs/j_0000000000000000")[0] == 404


def test_fifo_queue_and_max_jobs(tmp_path):
    with running_server(tmp_path / "home", kinds=test_kinds(), max_jobs=2) as (_, c):
        ids = [c.post("/api/jobs", {"kind": "test.sleep",
                                    "params": {"seconds": 1, "lines": 2}})[1]["id"]
               for _ in range(4)]
        time.sleep(0.6)
        states = [c.get(f"/api/jobs/{i}")[1]["state"] for i in ids]
        assert states.count("running") <= 2
        jobs = [c.wait_job(i) for i in ids]
        assert all(j["state"] == "succeeded" for j in jobs)
        starts = [j["started_at"] for j in jobs]
        assert starts == sorted(starts)


def test_exit_codes_that_are_results_and_failures(tmp_path):
    with running_server(tmp_path / "home", kinds=test_kinds(), threads=2) as (srv, c):
        ok = c.post("/api/jobs", {"kind": "test.sleep", "params": {"seconds": 0, "code": 1}})[1]
        bad = c.post("/api/jobs", {"kind": "test.sleep",
                                   "params": {"seconds": 0, "code": 0, "lines": 0}})[1]
        ok = c.wait_job(ok["id"])
        assert ok["state"] == "succeeded" and ok["outcome"]["exit_code"] == 1
        assert "a result" in ok["outcome"]["note"]
        text = (srv.home / "jobs" / ok["id"] / "out" / "result.txt").read_text()
        assert "threads=2" in text and "cuda=''" in text          # the CPU was enforced
        bad = c.wait_job(bad["id"])
        assert bad["state"] == "succeeded"
        failing = c.post("/api/jobs", {"kind": "test.sleep",
                                       "params": {"seconds": 0, "code": 3}})[1]
        failing = c.wait_job(failing["id"])
        assert failing["state"] == "failed" and failing["outcome"]["exit_code"] == 3
        assert failing["result"] is None and failing["outcome"]["error"]


def test_restart_reattaches_running_and_requeues_queued_jobs(tmp_path):
    home = tmp_path / "home"
    with running_server(home, kinds=test_kinds(), max_jobs=1) as (_, c):
        running = c.post("/api/jobs", {"kind": "test.sleep",
                                       "params": {"seconds": 4, "lines": 8}})[1]
        queued = c.post("/api/jobs", {"kind": "test.sleep", "params": {"seconds": 0}})[1]
        _wait_state(c, running["id"], ("running",))
    # the first runner is gone; its job's supervisor lives on in its own session
    with running_server(home, kinds=test_kinds(), max_jobs=1) as (srv, c):
        assert srv.jobs.reattached == 1
        assert c.get(f"/api/jobs/{running['id']}")[1]["state"] == "running"
        assert c.get(f"/api/jobs/{queued['id']}")[1]["state"] == "queued"
        first = c.wait_job(running["id"])
        assert first["state"] == "succeeded" and first["artefacts"][0]["name"] == "result"
        second = c.wait_job(queued["id"])
        assert second["state"] == "succeeded"


def test_a_job_whose_record_was_lost_is_found_in_the_trace(tmp_path):
    home = tmp_path / "home"
    with running_server(home, kinds=test_kinds()) as (_, c):
        job = c.post("/api/jobs", {"kind": "test.sleep", "params": {"seconds": 2, "lines": 2}})[1]
        _wait_state(c, job["id"], ("running",))
    (home / "jobs" / job["id"] / "request.json").unlink()
    with running_server(home, kinds=test_kinds()) as (srv, c):
        assert srv.jobs.reattached == 1
        final = c.wait_job(job["id"])
        assert final["state"] == "succeeded" and final["kind"] == "test.sleep"
        assert "trace" in final["device_note"]


def test_job_receipts_follow_the_job(tmp_path):
    with running_server(tmp_path / "home", kinds=test_kinds(), max_jobs=1) as (_, c):
        slow = c.post("/api/jobs", {"kind": "test.sleep", "params": {"seconds": 3, "lines": 3}})[1]
        _wait_state(c, slow["id"], ("running",))
        running = c.call("job_status", {"job_id": slow["id"]})["receipt"]
        assert running["result"]["outcome"] == "running" and running["result"]["kind"] is None
        assert running["execution"]["device"]["assigned"] == "cpu"
        done = c.wait_job(slow["id"])
        assert done["outcome"]["checks"] == ["controller_digests"]
        assert done["outcome"]["expected"] == ["result"]
        r = c.call("job_status", {"job_id": slow["id"]})["receipt"]
        assert (r["result"]["outcome"], r["result"]["assurance"]) == ("validated", "outputs_verified")
        assert r["result"]["checks"] == [{"id": "controller_digests", "ok": True}]
        assert r["result"]["artifacts"] == {"expected": ["result"], "missing": [], "hash_mismatch": []}
        assert r["execution"]["device"] == {"requested": "auto", "assigned": "cpu", "used": "cpu",
                                            "evidence": "selected"}
        # an exit code that is a result: the runner digested the outputs itself
        coded = c.wait_job(c.post("/api/jobs", {"kind": "test.sleep",
                                                "params": {"seconds": 0, "code": 1}})[1]["id"])
        assert coded["outcome"]["checks"] == ["runner_digests", "required_artefacts"]
        failed = c.wait_job(c.post("/api/jobs", {"kind": "test.sleep",
                                                 "params": {"seconds": 0, "code": 3}})[1]["id"])
        assert "checks" not in failed["outcome"] and failed["outcome"]["expected"] == ["result"]
        f = c.call("job_status", {"job_id": failed["id"]})["receipt"]["result"]
        assert (f["outcome"], f["kind"], f["assurance"]) == ("failed", None, "none")


def test_a_validator_is_listed_only_for_an_artefact_it_checked(tmp_path):
    """The controller validates the artefacts it collected; an optional one that is absent
    was never validated, and outcome.checks does not say it was."""
    from test_server_support import Kind, _sleep_command, _sleep_prepare
    base = test_kinds()["test.sleep"]
    kinds = {"test.sleep": Kind("test.sleep", base.title, "seconds", _sleep_prepare, _sleep_command,
                                artefacts=(("result", "result.txt", True), ("extra", "extra.txt", False)),
                                validators={"result": lambda p: None, "extra": lambda p: None},
                                ok_exit_codes=frozenset({0, 1}), parameters=base.parameters,
                                heavy=(), timeout_s=120)}
    with running_server(tmp_path / "home", kinds=kinds) as (_, c):
        done = c.wait_job(c.post("/api/jobs", {"kind": "test.sleep", "params": {"seconds": 0}})[1]["id"])
        assert done["state"] == "succeeded"
        assert done["outcome"]["checks"] == ["controller_digests", "validator:result"]
        coded = c.wait_job(c.post("/api/jobs", {"kind": "test.sleep",
                                                "params": {"seconds": 0, "code": 1}})[1]["id"])
        assert coded["outcome"]["checks"] == ["runner_digests", "required_artefacts", "validator:result"]


# A stand-in pipeline: records its stages with bioagent.runrecord, as the real ones do, and
# writes run.json and limitations.md. Its third stage's check passes or fails on request,
# and it exits 1 when the run is incomplete (as bioagent dock and admet do) unless told to
# exit 0 regardless, which leaves the incompleteness to the run.json validator. Asked for a
# malformed record, it rewrites run.json with a stage that is not a mapping and a version
# that is a list, and leaves a hidden temporary file behind, as a crashed writer would.
_STUB_PIPELINE = r"""
import hashlib, json, os, sys, time
from bioagent.runrecord import StageRecorder, write_limitations
out, complete, honest = sys.argv[1], sys.argv[2] == "1", sys.argv[3] == "1"
malformed = sys.argv[4] == "1"
rec = StageRecorder(out, "test.pipeline", plan=("prepare", "compute", "check", "report"))
for name in ("prepare", "compute"):
    with rec.stage(name, engine="stub", version="1"):
        time.sleep(0.8)
with rec.stage("check", engine="stub", version="1") as st:
    st.check = "PASS" if complete else "FAIL"
    st.reason = "the planted check" + ("" if complete else ": 3.1 A from the crystal pose")
with rec.stage("report", engine="stub", version="1") as st:
    lines = write_limitations(out, ["A stub result is not a measurement."])
    st.outputs = ["limitations.md"]
data = open(os.path.join(out, "limitations.md"), "rb").read()
doc = {"pipeline": "test.pipeline", "outputs": {"limitations.md": hashlib.sha256(data).hexdigest()},
       "stages": rec.as_list(), "complete": rec.complete, "limitations": lines}
json.dump(doc, open(os.path.join(out, "run.json"), "w"))
rec.finish()
if malformed:
    doc["stages"] = ["not a stage", *doc["stages"]]
    doc["stages"][1]["version"] = ["1", "2"]
    json.dump(doc, open(os.path.join(out, "run.json"), "w"))
    open(os.path.join(out, ".stages.json.999.tmp"), "w").write("{}")
sys.exit(1 if honest and not rec.complete else 0)
"""


def _stub_pipeline_kinds():
    from bioagent.runrecord import record_problems
    from test_server_support import Kind
    from tcmstudio import kinds as K

    def prepare(p, ctx):
        return {"complete": bool(p.get("complete", True)), "honest": bool(p.get("honest", True)),
                "malformed": bool(p.get("malformed", False))}

    def command(r, ctx):
        return [ctx.python, "-I", "-c", _STUB_PIPELINE, "{output}",
                "1" if r["complete"] else "0", "1" if r["honest"] else "0",
                "1" if r["malformed"] else "0"]

    def check_run(path):
        # as kinds._verifier does: a record that cannot be checked is a problem, not a crash
        try:
            return "; ".join(record_problems(json.loads(path.read_text()), path.parent)) or None
        except Exception as exc:                                # noqa: BLE001
            return f"the run could not be verified: {type(exc).__name__}: {exc}"

    params = {"type": "object", "properties": {"complete": {"type": "boolean"},
                                               "honest": {"type": "boolean"},
                                               "malformed": {"type": "boolean"}},
              "additionalProperties": False}
    return {"test.pipeline": Kind("test.pipeline", ("测试流程", "Test pipeline"), "seconds",
                                  prepare, command,
                                  artefacts=(("run", "run.json", True),
                                             ("limitations", "limitations.md", True)),
                                  validators={"run": check_run}, parameters=params, heavy=(),
                                  timeout_s=120, progress=K._stage_progress)}


def test_a_complete_pipeline_shows_its_stages_and_limitations(tmp_path):
    with running_server(tmp_path / "home", kinds=_stub_pipeline_kinds()) as (srv, c):
        job = c.post("/api/jobs", {"kind": "test.pipeline", "params": {}})[1]
        events = c.events(job["id"])
        progress = [d for e, d in events if e == "progress" and d]
        stages_seen = [p.get("stage") for p in progress]
        assert "compute" in stages_seen and stages_seen[-1] == "verifying", stages_seen
        assert any(p.get("message") == "stage compute" for p in progress)
        fractions = [p["fraction"] for p in progress if "fraction" in p]
        assert fractions == sorted(fractions) and max(fractions) < 1
        final = c.wait_job(job["id"])
        assert final["state"] == "succeeded", final["outcome"]
        assert [s["name"] for s in final["stages"]] == ["prepare", "compute", "check", "report"]
        assert final["limitations"] == ["A stub result is not a measurement."]
        st = c.call("job_status", {"job_id": job["id"]})
        assert "A stub result is not a measurement." in st["governance"]["limitations"]
        assert [s["status"] for s in st["result"]["stages"]] == ["succeeded"] * 4
        receipt = srv.jobs.job_receipt(job["id"])
        assert receipt["state"] == "succeeded" and receipt["complete"] is True


def test_an_incomplete_pipeline_is_a_failed_job_with_its_stages_as_problems(tmp_path):
    with running_server(tmp_path / "home", kinds=_stub_pipeline_kinds()) as (srv, c):
        honest = c.wait_job(c.post("/api/jobs", {"kind": "test.pipeline",
                                                 "params": {"complete": False}})[1]["id"])
        # the pipeline said so itself: exit 1
        assert honest["state"] == "failed" and honest["outcome"]["exit_code"] == 1
        why = "stage check: its check failed: the planted check: 3.1 A from the crystal pose"
        assert why in honest["outcome"]["problems"]
        assert honest["result"] is None and honest["stages"][2]["check"] == "FAIL"
        receipt = srv.jobs.job_receipt(honest["id"])
        assert receipt["state"] == "failed" and receipt["complete"] is False
        assert why in receipt["problems"] and receipt["checks"] == []
        # a pipeline that exited 0 all the same: its run.json validator catches it
        quiet = c.wait_job(c.post("/api/jobs", {"kind": "test.pipeline",
                                                "params": {"complete": False,
                                                           "honest": False}})[1]["id"])
        assert quiet["state"] == "failed" and quiet["outcome"]["exit_code"] == 0
        assert why in quiet["outcome"]["problems"]
        assert any("records the run as incomplete" in p for p in quiet["outcome"]["problems"])
        f = c.call("job_status", {"job_id": quiet["id"]})["receipt"]["result"]
        assert (f["outcome"], f["assurance"]) == ("failed", "none")
        assert any(why in ch.get("detail", "") for ch in f["checks"])


def test_a_malformed_stage_record_fails_the_job_instead_of_stalling_it(tmp_path):
    """A run.json whose stages hold a non-mapping entry and a list for a version used to
    raise inside collection, which was retried, and failed, on every poll: the job stayed
    'running'. Now it is collected once, as a failed job with a receipt."""
    with running_server(tmp_path / "home", kinds=_stub_pipeline_kinds()) as (srv, c):
        job = c.post("/api/jobs", {"kind": "test.pipeline", "params": {"malformed": True}})[1]
        events = c.events(job["id"])
        final = c.wait_job(job["id"])
        assert final["state"] == "failed", final
        assert any("could not be verified" in p for p in final["outcome"]["problems"])
        assert [s["name"] for s in final["stages"]] == ["prepare", "compute", "check", "report"]
        receipt = srv.jobs.job_receipt(job["id"])
        assert receipt["state"] == "failed"
        assert {"id": "stub", "version": "['1', '2']", "stages": ["prepare"]} in receipt["engine"]
        # the hidden temporary file is not announced as an output
        seen = [d.get("path") for e, d in events if e == "artefact" and d]
        assert "limitations.md" in seen and not any(p.endswith(".tmp") for p in seen), seen


def test_the_runner_reads_a_jobs_own_record_only_as_a_bounded_regular_file(tmp_path):
    from tcmstudio import kinds as K
    out = tmp_path / "job" / "out"
    out.mkdir(parents=True)
    doc = {"schema": "bioagent.stages/1", "stages": [{"name": "a", "required": True}]}
    elsewhere = tmp_path / "elsewhere.json"
    elsewhere.write_text(json.dumps(doc))
    # a symlink out of the output directory is not followed
    (out / "stages.json").symlink_to(elsewhere)
    assert K.read_stages(tmp_path / "job") is None
    (out / "stages.json").unlink()
    (out / "stages.json").write_text(json.dumps(doc))
    assert K.read_stages(tmp_path / "job")["stages"][0]["name"] == "a"
    # a file over its budget is not loaded
    (out / "stages.json").write_text(json.dumps(doc) + " " * K.RECORD_MAX_BYTES)
    assert K.read_stages(tmp_path / "job") is None
    assert K.read_record(out / "missing.md") is None
    (out / "bad.md").write_bytes(b"\xff\xfe")
    assert K.read_record(out / "bad.md") is None


def test_limitations_reach_the_job_bounded(tmp_path):
    from types import SimpleNamespace

    from tcmstudio.jobs import JobService
    out = tmp_path / "out"
    out.mkdir()
    lines = ["x" * 5000] + [f"limitation {i}" for i in range(150)]
    (out / "limitations.md").write_text("\n".join(lines) + "\n")
    (out / "run.json").write_text(json.dumps({"stages": [{"name": "a"}, 3, "b"]}))
    run_doc, stages, got = JobService._pipeline_record(None, SimpleNamespace(dir=tmp_path))
    assert stages == [{"name": "a"}] and run_doc is not None
    assert got[0] == "x" * 999 + "…" and got[1:100] == lines[1:100] and len(got) == 101
    assert got[-1] == "… and 51 more in limitations.md"
    (out / "limitations.md").unlink()
    (out / "limitations.md").symlink_to(tmp_path / "out" / "run.json")
    assert JobService._pipeline_record(None, SimpleNamespace(dir=tmp_path))[2] is None


def test_events_stream_progress_and_logs(tmp_path):
    with running_server(tmp_path / "home", kinds=test_kinds()) as (_, c):
        job = c.post("/api/jobs", {"kind": "test.sleep",
                                   "params": {"seconds": 1.5, "lines": 5}})[1]
        events = c.events(job["id"])
        logs = [d["line"] for e, d in events if e == "log"]
        assert any(line.startswith("step") for line in logs)
        progress = [d for e, d in events if e == "progress" and d]
        assert any("fraction" in p for p in progress)
        assert any(e == "comment" for e, _ in events) or len(events) > 3   # keep-alives
        assert events[-1][1]["state"] == "succeeded"
        status, _, body = c.raw("GET", "/api/jobs/j_0000000000000000/events")
        assert status == 404


def test_events_are_written_live_not_at_the_end(tmp_path):
    """The SSE stream writes each event as it happens: the job's state arrives while it still runs."""
    with running_server(tmp_path / "home", kinds=test_kinds()) as (_, c):
        job = c.post("/api/jobs", {"kind": "test.sleep",
                                   "params": {"seconds": 6, "lines": 3}})[1]
        _wait_state(c, job["id"], ("running",))
        conn = http.client.HTTPConnection("127.0.0.1", c.port, timeout=30)
        conn.request("GET", f"/api/jobs/{job['id']}/events?token={c.token}",
                     headers={"Accept": "text/event-stream"})
        res = conn.getresponse()
        assert res.status == 200
        started = time.monotonic()
        first_event = None
        while first_event is None:
            line = res.fp.readline().decode("utf-8")
            assert line, "the stream ended before any event"
            if line.startswith("event:"):
                first_event = line[6:].strip()
        waited = time.monotonic() - started
        assert first_event == "state"
        # before the fix the whole stream was buffered until the job finished (about 6 s)
        assert waited < 3, f"the first event took {waited:.1f} s"
        assert c.get(f"/api/jobs/{job['id']}")[1]["state"] == "running"
        conn.close()
        c.wait_job(job["id"])


def test_gpu_jobs_run_one_at_a_time(tmp_path):
    nvidia = "0, NVIDIA RTX A4000, 16376, 550.54.14\n"

    def fake_run(cmd, timeout=5.0):
        return subprocess.CompletedProcess(cmd, 0, stdout=nvidia, stderr="")
    probe = dev.DeviceProbe(run=fake_run, which=lambda n: "/usr/bin/nvidia-smi"
                            if n == "nvidia-smi" else None, torch=False)
    with running_server(tmp_path / "home", kinds=test_kinds(gpu=True), max_jobs=3) as (srv, c):
        srv.jobs.probe = probe
        a = c.post("/api/jobs", {"kind": "test.gpu", "params": {"seconds": 1.5, "lines": 3}})[1]
        b = c.post("/api/jobs", {"kind": "test.gpu", "params": {"seconds": 0.2}})[1]
        cpu = c.post("/api/jobs", {"kind": "test.sleep", "params": {"seconds": 0.2}})[1]
        _wait_state(c, a["id"], ("running",))
        time.sleep(0.5)
        assert c.get(f"/api/jobs/{b['id']}")[1]["state"] == "queued"
        assert c.wait_job(cpu["id"])["state"] == "succeeded"     # a CPU job is not held back
        first, second = c.wait_job(a["id"]), c.wait_job(b["id"])
        assert first["device"] == second["device"] == "cuda:0"
        assert second["started_at"] >= first["finished_at"]
        text = (srv.home / "jobs" / a["id"] / "out" / "result.txt").read_text()
        assert "cuda='0'" in text
        # the poll's receipt names the GPU the runner assigned, never the CPU the poll ran on,
        # and says no more than that it was selected
        r = c.call("job_status", {"job_id": a["id"]})["receipt"]
        assert r["device"] == "cuda:0"
        device = r["execution"]["device"]
        assert device["assigned"] == "cuda:0" and device["evidence"] == "selected"
        assert device["used"] is None and device["note"]


# --------------------------------------------------------------------- refusals

def test_bad_requests_are_refused_before_anything_runs(tmp_path):
    with running_server(tmp_path / "home", kinds=test_kinds()) as (srv, c):
        status, body = c.post("/api/jobs", {"kind": "no.such", "params": {}})
        assert status == 400 and "test.sleep" in body["error"]["hint"]
        status, body = c.post("/api/jobs", {"kind": "test.sleep", "params": {"seconds": "x"}})
        assert status == 400 and body["error"]["type"] == "bad_arguments"
        status, body = c.post("/api/jobs", {"kind": "test.sleep", "params": {"secs": 1}})
        assert status == 400
        status, body = c.post("/api/jobs", {"kind": "test.sleep", "params": [1]})
        assert status == 400
        status, body = c.post("/api/jobs", {"kind": "test.sleep", "params": {},
                                            "submission_id": "bad id!"})
        assert status == 400
        assert not any(p.name.startswith("j_") for p in (srv.home / "jobs").iterdir())


def test_output_token_is_reserved(tmp_path):
    with running_server(tmp_path / "home") as (_, c):
        status, body = c.post("/api/jobs", {"kind": "pipeline.fold", "params": {
            "source": "{output}/x.fa", "method": "esmfold"}})
        assert status == 400 and "{output}" in body["error"]["message"]


def test_network_and_remote_gates(tmp_path):
    with running_server(tmp_path / "home", network=False) as (srv, c):
        status, body = c.post("/api/jobs", {"kind": "tcmdb.fetch", "params": {"dataset": "itcm"}})
        assert status == 422 and body["error"]["type"] == "network_off"
        status, body = c.post("/api/jobs", {"kind": "pipeline.fold", "params": {
            "source": "MKTAYIAKQRQISFVKSHFSRQ", "method": "esmatlas"}})
        assert status == 400 and "allow_remote" in body["error"]["message"]
        srv.state.update({"network": {"enabled": True}})
        status, body = c.post("/api/jobs", {"kind": "pipeline.fold", "params": {
            "source": "MKTAYIAKQRQISFVKSHFSRQ", "method": "esmatlas", "allow_remote": True}})
        assert status == 422 and body["error"]["type"] == "unavailable"
        assert "third-party" in body["error"]["message"]
        status, body = c.post("/api/jobs", {"kind": "pipeline.fold", "params": {
            "source": "-rf /", "method": "esmfold"}})
        assert status == 400


def test_missing_engines_and_data_are_refusals(tmp_path):
    with running_server(tmp_path / "home") as (_, c):
        status, body = c.post("/api/jobs", {"kind": "research.run",
                                            "params": {"question": "葛根芩连汤的实测靶点集中在哪些通路？"}})
        assert status == 422 and body["error"]["type"] == "unavailable"
        assert any("snapshots" in m for m in body["error"]["missing"])
        kinds = {k["kind"]: k for k in c.get("/api/kinds")[1]}
        if not kinds["pipeline.dock"]["available"]:
            status, body = c.post("/api/jobs", {"kind": "pipeline.dock", "params": {
                "receptor": "x", "ligands": "CCO"}})
            assert status == 422 and "Nothing is approximated" in body["error"]["message"]


def test_a_refused_job_reaches_the_model_as_a_refusal(tmp_path):
    with running_server(tmp_path / "home") as (_, c):
        env = c.call("network_pharmacology_run", {"formula": "葛根芩连汤"}, project_id="p")
        assert env["status"] == "failed"
        assert env["error"]["type"] == "unavailable"
        assert env["error"]["message"].startswith("Research loop cannot run on this runner")
        assert "job.research.snapshot" in env["error"]["hint"]
        assert any("snapshots" in m for m in env["error"]["missing"])
        assert "此处不可运行" in env["summary"] and "运行出错" not in env["summary"]
        assert "cannot run here" in env["summary_en"] and "runtime error" not in env["summary_en"]
        assert "Error (unavailable): Research loop" in env["text"]
        assert "Remedy: Build the data snapshots" in env["text"]
        assert "runtime_error" not in env["text"]
        # nothing was queued: not executed, and no implementation named as run
        assert env["receipt"]["result"]["outcome"] == "not_executed"
        assert env["receipt"]["execution"]["implementation"] is None


def test_paths_outside_the_home_are_not_inputs(tmp_path):
    outside = tmp_path / "outside.fa"
    outside.write_text(">x\nMKTAYIAKQRQISFVKSHFSRQ\n")
    with running_server(tmp_path / "home") as (srv, c):
        status, body = c.post("/api/jobs", {"kind": "pipeline.fold", "params": {
            "source": str(outside), "method": "esmfold"}})
        # not a file the runner may read: taken as a sequence, and refused as one
        assert status == 400
        inside = srv.home / "data" / "seq.fa"
        inside.write_text(">x\nMKTAYIAKQRQISFVKSHFSRQ\n")
        status, job = c.post("/api/jobs", {"kind": "pipeline.fold", "params": {
            "source": str(inside), "method": "esmfold"}})
        assert status == 201 and job["inputs"]["source"] == {"path": str(inside.resolve())}
        c.json("DELETE", f"/api/jobs/{job['id']}")
