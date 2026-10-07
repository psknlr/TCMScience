"""Jobs on the runner: a real RNA-seq run end to end, the governed skill worker, the queue,
cancellation, re-attachment after a restart, and the refusals."""

from __future__ import annotations

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
        assert set(names) == {"run", "report", "results"}
        out = srv.home / "jobs" / job["id"] / "out"
        import hashlib
        for a in final["artefacts"]:
            assert hashlib.sha256((out / a["path"]).read_bytes()).hexdigest() == a["sha256"]
        de = final["result"]["differential_expression"]
        assert de["significant"] > 0 and de["tested"] == 30
        assert "--threads=4" in " ".join(final["command"]) or "--threads=" in " ".join(final["command"])
        assert final["command"][1:4] == ["-I", "-m", "bioagent.cli"]

        status, files = c.get(f"/api/jobs/{job['id']}/files")
        listed = {f["path"]: f for f in files["files"]}
        assert {"run.json", "report.html", "deseq2_results.tsv"} <= set(listed)
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
        # bad arguments are caught before anything runs
        env = c.call("run_pipeline", {"pipeline": "rnaseq", "arguments": {}})
        assert env["status"] == "failed" and env["error"]["type"] == "bad_arguments"
        env = c.call("call_tool", {"tool": "job.skill.run", "arguments": {
            "skill_id": "dock-ligands", "arguments": {"receptor": "x", "ligands": "CCO"},
            "allow_unpinned": False}})
        assert env["status"] == "failed"


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
    with running_server(tmp_path / "home") as (srv, c):
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
        assert "build_source_snapshots" in env["error"]["hint"]
        assert any("snapshots" in m for m in env["error"]["missing"])
        assert "此处不可运行" in env["summary"] and "运行出错" not in env["summary"]
        assert "Error (unavailable): Research loop" in env["text"]
        assert "Remedy: Build the data snapshots" in env["text"]
        assert "runtime_error" not in env["text"]


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
