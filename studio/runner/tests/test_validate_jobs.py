"""Engines under the agent's own scheduling (tcmstudio.validate_jobs, scenarios 7-9).

With the operator's approval, the regression scenarios call tcmstudio's runner over HTTP the
way the Studio page does, submit their pipelines with ``run_pipeline`` and follow them with
``job_status``. The RNA-seq scenario runs here for real: a simulated experiment is uploaded,
the runner's job service runs the pipeline as a subprocess, and the genes planted in the
experiment are looked for in the finished table. The docking and ADMET checks are shown on
canned finished jobs, then on the runner refusing a job whose engine is not installed.
"""
from __future__ import annotations

import importlib.util
import json
import math

import pytest

from bioagent.validation import BLOCKED, FAIL, PASS, SKIPPED, UNVERIFIED, Record
from tcmstudio import scenarios
from tcmstudio.validate_jobs import AgentRunner, approvals_for


def _run(fn, runner, tier="offline"):
    rec = Record("scenario", "scenario")
    x = scenarios._Run(rec, {}, tier, runner=runner, jobs_timeout_s=900)
    try:
        fn(x)
    except scenarios._Stop:
        pass
    return rec, x


def test_the_approvals_are_the_ones_the_page_asks_for():
    assert approvals_for("run_pipeline", {"pipeline": "dock", "arguments": {}}, job=True,
                         network=False) == ["job", "first_runner_call"]
    assert approvals_for("run_pipeline", {"arguments": {"allow_remote": True}}, job=True,
                         network=True) == ["job", "network", "remote_upload", "first_runner_call"]
    assert approvals_for("job_status", {"job_id": "x"}, job=False,
                         network=False) == ["first_runner_call"]


def test_an_approved_rnaseq_job_runs_on_the_runner_and_finds_the_planted_genes(tmp_path):
    with AgentRunner(tmp_path / "home", tcmdb_root=tmp_path / "tcmdb", network=False,
                     poll_s=0.2) as runner:
        rec, x = _run(scenarios._rnaseq, runner)
        jobs = runner.server.jobs.list()
    assert rec.status == PASS, rec.as_dict()
    step = rec.check("step 1: run_pipeline")
    assert step.evidence["kind"] == "pipeline.rnaseq" and step.evidence["state"] == "succeeded"
    assert step.evidence["approvals"] == ["job", "first_runner_call"]
    assert "results" in step.evidence["artefacts"]
    assert rec.check("planted_up").status == PASS and rec.check("planted_down").status == PASS
    # the job is the runner's own, and every file came in as an upload
    assert [j["kind"] for j in jobs] == ["pipeline.rnaseq"] and jobs[0]["state"] == "succeeded"
    uploads = [c for c in x.calls if "upload" in c]
    assert len(uploads) == 12 + 3, "twelve FASTQ files, the sheet, the transcriptome, the GTF"
    assert any(c.get("tool") == "job_status" and c.get("state") == "succeeded" for c in x.calls)


def test_without_approval_no_job_is_followed_and_nothing_is_claimed(monkeypatch):
    from tcmstudio import dispatch
    monkeypatch.setattr(dispatch, "call", lambda tool, args, ctx: {
        "status": "job_submitted", "job": {"id": "job-1", "state": "queued"}})
    rec = Record("x", "scenario")
    scenarios._Run(rec, {}, "offline").step("run_pipeline", {"pipeline": "dock"}, job=True)
    assert rec.status == UNVERIFIED and "--approve-jobs" in rec.check("step 1: run_pipeline").detail


# ------------------------------------------------------------------ canned finished jobs
class _Finished:
    """A runner whose one job finished with ``result``: the shape job_status returns."""

    def __init__(self, kind, result, state="succeeded", outcome=None):
        self.kind, self.result, self.state, self.outcome = kind, result, state, outcome
        self.calls, self.uploaded = [], []

    def upload(self, path, name=None):
        self.uploaded.append(name or str(path).rsplit("/", 1)[-1])
        return {"id": f"u{len(self.uploaded)}", "name": self.uploaded[-1]}

    def call(self, tool, args, approvals=()):
        self.calls.append((tool, args, list(approvals)))
        return {"status": "job_submitted", "job": {"id": "j1", "state": "queued"}}

    def follow(self, job_id, timeout_s, on_poll=None):
        return {"status": "succeeded",
                "result": {"id": job_id, "kind": self.kind, "state": self.state,
                           "outcome": self.outcome or {}, "result": self.result,
                           "artefacts": [{"name": "run", "path": "run.json"}]}}


def _dock_result(rmsd=0.38, scores=(-6.0, -6.3, -4.4)):
    centre = scenarios._crystal_centroid(scenarios._pocket(), "BEN")
    names = [n for n, _ in scenarios._DOCK_LIGANDS]
    return {"validation": {"ligand": "BEN", "top_pose_rmsd": rmsd, "passed": rmsd <= 2.0,
                           "best_rmsd_in_top3": rmsd, "top_score": -6.0, "threshold": 2.0},
            "box": {"center": [c + 0.2 for c in centre], "size": [18, 18, 18],
                    "source": "site ligand BEN"},
            "scores.tsv": [{"ligand": n, "best_score": str(s)} for n, s in zip(names, scores)],
            "versions": {"vina": "1.2.6", "meeko": "0.6.1", "rdkit": "2025.03.1"}}


def test_the_docking_job_is_judged_by_the_crystal_pose_the_site_and_the_ranking():
    runner = _Finished("pipeline.dock", _dock_result())
    rec, _ = _run(scenarios._docking, runner)
    assert rec.status == PASS, rec.as_dict()
    tool, args, approvals = runner.calls[0]
    assert tool == "run_pipeline" and args["arguments"]["site_ligand"] == "BEN"
    assert args["arguments"]["site_ligand_smiles"] == "NC(=N)c1ccccc1"
    assert approvals == ["job", "first_runner_call"], "local file, no network, no third party"
    assert runner.uploaded == ["3ptb_pocket.pdb", "ligands.csv"]
    # a setup that does not reproduce the crystal pose is not passed, whatever it scores
    rec, _ = _run(scenarios._docking, _Finished("pipeline.dock", _dock_result(rmsd=3.2)))
    assert rec.check("redocking").status == FAIL and rec.status == FAIL
    rec, _ = _run(scenarios._docking, _Finished("pipeline.dock",
                                                _dock_result(scores=(-6.0, -6.3, -7.1))))
    assert rec.check("ranking").status == FAIL


def test_the_crystal_centroid_is_read_from_the_pdb_coordinates():
    # the nine heavy atoms of benzamidine (BEN) in 3PTB, averaged by hand
    centre = scenarios._crystal_centroid(scenarios._pocket(), "BEN")
    assert all(math.isclose(a, b, abs_tol=0.001)
               for a, b in zip(centre, (-1.7591, 14.4610, 16.9158)))


def _admet_rows():
    return [{"name": "aspirin", "MolWt": "180.2", "TPSA": "63.6", "lipinski_violations": "0"},
            {"name": "caffeine", "MolWt": "194.2", "TPSA": "61.82", "lipinski_violations": "0"},
            {"name": "tetracontane", "MolWt": "563.1", "TPSA": "0", "lipinski_violations": "2"}]


def test_the_admet_job_is_judged_by_atomic_weights_pubchem_and_tdc():
    rec, _ = _run(scenarios._admet, _Finished("pipeline.admet", {"admet.tsv": _admet_rows()}))
    assert rec.check("models").status == UNVERIFIED, "offline, no model is built"
    assert all(c.status == PASS for c in rec.checks if c.name != "models"), rec.as_dict()
    # the cards write the metric as TDC's leaderboard does, in lower case
    models = {"caco2_wang": {"metric": "mae", "test_score": 0.2821, "train": 728, "test": 182},
              "hia_hou": {"metric": "auroc", "test_score": 0.9239, "train": 461, "test": 117}}
    runner = _Finished("pipeline.admet", {"admet.tsv": _admet_rows(), "models": models})
    rec, _ = _run(scenarios._admet, runner, tier="live")
    assert rec.status == PASS, rec.as_dict()
    args, approvals = runner.calls[0][1]["arguments"], runner.calls[0][2]
    assert args["build_models"] is True and args["endpoints"] == ["caco2_wang", "hia_hou"]
    assert approvals == ["job", "network", "first_runner_call"]
    weak = dict(models, hia_hou=dict(models["hia_hou"], test_score=0.71))
    rec, _ = _run(scenarios._admet, _Finished("pipeline.admet", {"admet.tsv": _admet_rows(),
                                                                  "models": weak}), tier="live")
    assert rec.check("hia_hou_held_out").status == FAIL
    rows = _admet_rows()
    rows[0]["MolWt"] = "181.2"
    rec, _ = _run(scenarios._admet, _Finished("pipeline.admet", {"admet.tsv": rows}))
    assert rec.check("aspirin_weight").status == FAIL


def test_a_job_that_failed_on_the_runner_fails_the_scenario_with_its_reason():
    runner = _Finished("pipeline.dock", None, state="failed",
                       outcome={"error": "vina exited with code 1"})
    rec, _ = _run(scenarios._docking, runner)
    assert rec.status == FAIL and "vina exited" in rec.check("step 1: run_pipeline").detail


@pytest.mark.parametrize("error", [
    "exit 1; refused: could not download the TDC ADMET group from https://dataverse.harvard"
    ".edu/api/access/datafile/4426004: <urlopen error Tunnel connection failed: 403 Forbidden>",
    "exit 1; refused: could not download the TDC ADMET group from https://dataverse.harvard"
    ".edu/api/access/datafile/4426004: <urlopen error [Errno -2] Name or service not known>",
    "exit 1; refused: could not download the TDC ADMET group from https://dataverse.harvard"
    ".edu/api/access/datafile/4426004: <urlopen error timed out>",
])
def test_a_job_that_could_not_reach_its_host_is_blocked_not_failed(error):
    # what this container's proxy did to the ADMET job: the host was out of reach, and
    # nothing the job computed was wrong
    runner = _Finished("pipeline.admet", None, state="failed", outcome={"error": error})
    rec, _ = _run(scenarios._admet, runner, tier="live")
    step = rec.check("step 1: run_pipeline")
    assert rec.status == BLOCKED and step.status == BLOCKED, rec.as_dict()
    assert "dataverse.harvard.edu" in step.detail, "the reason is kept whole"


def test_the_blood_sample_needs_the_live_tier():
    rec, _ = _run(scenarios._pbmc, _Finished("pipeline.scrna", {}))
    assert rec.check("pbmc_1k").status == SKIPPED


@pytest.mark.skipif(importlib.util.find_spec("vina") is not None,
                    reason="the docking engine is installed here: the job would run")
def test_the_runner_refuses_a_docking_job_whose_engine_is_missing_and_says_how(tmp_path):
    with AgentRunner(tmp_path / "home", tcmdb_root=tmp_path / "tcmdb", network=False,
                     poll_s=0.2) as runner:
        rec, _ = _run(scenarios._docking, runner)
        assert runner.server.jobs.list() == [], "nothing was queued"
    assert rec.status == BLOCKED, rec.as_dict()
    detail = rec.check("step 1: run_pipeline").detail
    assert "tool_unavailable" in detail and "vina" in detail


# ------------------------------------------------------------------ the research loop (scenario 4)
def test_an_approved_research_job_runs_on_the_runner_and_its_claims_are_checked(tmp_path):
    """The research loop as the agent runs it (network_pharmacology_run), on a planted world
    whose answer is known: pathway A is where the constituents act. The finished run's claims
    are looked up in the runner's own ledger snapshots."""
    from bioagent.research.planted import PATHWAY, build_world
    from bioagent.sources.ledger import SnapshotLedger
    lake = tmp_path / "lake"
    build_world("selective", lake / "snapshots",
                ledger=SnapshotLedger(lake / "snapshots" / "ledger.jsonl"))
    with AgentRunner(tmp_path / "home", tcmdb_root=tmp_path / "tcmdb", network=False,
                     data_lake=lake, poll_s=0.2) as runner:
        assert runner.server.state.get()["allow_remote"] is False
        rec = Record("scenario.04", "scenario")
        x = scenarios._Run(rec, {}, "live", runner=runner, jobs_timeout_s=900)
        try:
            scenarios._measured_hypotheses(x, "葛根芩连汤", parameters={})
        except scenarios._Stop:
            pass
        jobs = runner.server.jobs.list()
    assert rec.status == PASS, rec.as_dict()
    assert [j["kind"] for j in jobs] == ["research.run"] and jobs[0]["state"] == "succeeded"
    step = rec.check("step 1: network_pharmacology_run")
    assert step.evidence["approvals"] == ["job", "first_runner_call"]
    assert rec.check("labelled").evidence["hypotheses"] == [PATHWAY]
    assert rec.check("measured_targets").evidence["targets"] > 0
    assert rec.check("cites_measured_edges").status == PASS
    assert rec.check("released").status == PASS


class _Research(_Finished):
    """A finished research run whose outputs are served as the runner serves them."""

    def __init__(self, files, summary):
        super().__init__("research.run", summary)
        self.files = files

    def file(self, job_id, rel):
        if rel not in self.files:
            raise RuntimeError(f"{rel} of job {job_id}: HTTP 404")
        return json.dumps(self.files[rel]).encode("utf-8")


def test_a_claim_worded_beyond_a_hypothesis_fails_the_scenario():
    claim = {"kind": "mechanism_hypothesis", "object": "reactome:R-HSA-1", "support": [],
             "statement": "This proves that the formula cures arrhythmia."}
    runner = _Research({"result.json": {"targets": [{"target": "uniprot:P1"}],
                                        "claims": [claim], "excluded": {}},
                        "rebuttal.json": {"survived": ["reactome:R-HSA-1"], "refuted": []}},
                       {"released": True, "snapshots": {}})
    rec, _ = _run(lambda x: scenarios._measured_hypotheses(x, "桂枝汤《伤寒论》"), runner,
                  tier="live")
    assert rec.check("labelled").status == FAIL and rec.status == FAIL
    tool, args, approvals = runner.calls[0]
    assert tool == "network_pharmacology_run" and args["formula"] == "桂枝汤《伤寒论》"
    assert args["parameters"] == {"activity": ["npass", "cmaup"], "optional": ["lotus", "string"]}
    # nothing survived: nothing is claimed, and that passes
    runner = _Research({"result.json": {"targets": [{"target": "uniprot:P1"}], "claims": [claim],
                                        "excluded": {}},
                        "rebuttal.json": {"survived": [], "refuted": [{"object": "x"}]}},
                       {"released": True, "snapshots": {}})
    rec, _ = _run(lambda x: scenarios._measured_hypotheses(x, "桂枝汤《伤寒论》"), runner,
                  tier="live")
    assert rec.status == PASS, rec.as_dict()
    assert "none is invented" in rec.check("labelled").detail


def test_without_approved_jobs_the_measured_path_says_how_it_runs():
    rec, _ = _run(scenarios._mechanism_hypothesis, None)
    assert rec.check("measured_hypotheses").status == UNVERIFIED, rec.as_dict()
    assert "--approve-jobs" in rec.check("measured_hypotheses").detail


# ------------------------------------------------------------------ engines installed by the agent
def test_an_engine_group_is_installed_by_the_runners_own_provision_job():
    runner = _Finished("env.provision", {"group": "docking", "status": "PASS",
                                         "checks": {"import": "PASS", "version": "PASS",
                                                    "known_answer": "PASS"}})
    [rec] = scenarios.run_provision(runner, ["docking"], jobs_timeout_s=900)
    assert rec.status == PASS and rec.component == "provision.docking", rec.as_dict()
    tool, args, approvals = runner.calls[0]
    assert tool == "call_tool" and args == {"tool": "job.env.provision",
                                            "arguments": {"group": "docking"}}
    assert approvals == ["job", "network", "first_runner_call"]
    # a group the runner could not verify after installing it is not passed
    runner = _Finished("env.provision", {"group": "docking", "status": "FAIL",
                                         "checks": {"vina:version": "FAIL"}})
    [rec] = scenarios.run_provision(runner, ["docking"], jobs_timeout_s=900)
    assert rec.check("verified").status == FAIL
    # Vina's known answer is a docking, left to the docking job by design: the group stays
    # UNVERIFIED and says so; only that may be accepted beside a passing docking scenario
    runner = _Finished("env.provision", {"group": "docking", "status": "UNVERIFIED", "checks": {
        "vina:import": "PASS", "vina:version": "PASS", "vina:calculation": "UNVERIFIED",
        "rdkit:import": "PASS", "rdkit:version": "PASS", "rdkit:calculation": "PASS"}})
    [rec] = scenarios.run_provision(runner, ["docking"], jobs_timeout_s=900)
    check = rec.check("verified")
    assert check.status == UNVERIFIED and check.evidence["deferred_only"] is True
    assert "prepared receptor" in check.detail


def test_provisioning_needs_the_operators_approval_of_jobs(tmp_path):
    from tcmstudio.cli import main
    with pytest.raises(SystemExit, match="--approve-jobs"):
        main(["validate", "--out", str(tmp_path), "--provision", "docking"])


def test_a_missing_command_line_tool_leaves_a_group_unverified_not_failed():
    """The provision job installs Python packages; salmon and its kind come from the
    system's package manager, so their absence is reported, and fails nothing."""
    runner = _Finished("env.provision", {"group": "transcriptomics", "status": "BLOCKED",
                                         "checks": {"scanpy:import": "PASS",
                                                    "scanpy:version": "PASS",
                                                    "scanpy:calculation": "PASS",
                                                    "salmon:binary": "BLOCKED"}})
    [rec] = scenarios.run_provision(runner, ["transcriptomics"], jobs_timeout_s=900)
    check = rec.check("verified")
    assert check.status == UNVERIFIED and check.evidence["deferred_only"] is False
    assert "salmon is not on the PATH" in check.detail


def test_the_provision_job_is_judged_on_the_packages_it_installs(monkeypatch):
    from bioagent.validation import BLOCKED as B, PASS as P, Record as R

    from tcmstudio import kinds

    def readiness(groups):
        rec = R("transcriptomics", "environment")
        rec.add("scanpy:import", P, "imports")
        rec.add("salmon:binary", B, "not on the PATH")
        return [rec]
    monkeypatch.setattr("bioagent.validation.dependencies.readiness", readiness)
    assert kinds._provision_verify({"group": "transcriptomics"}, {}) == []


@pytest.mark.parametrize("deferred_only,code", [(True, 0), (False, 1)])
def test_require_pass_accepts_a_group_unverified_only_for_a_calculation_left_to_its_job(
        tmp_path, monkeypatch, deferred_only, code):
    from tcmstudio import validate_jobs
    from tcmstudio.cli import main

    class _Runner:
        def __init__(self, *a, **kw):
            pass

        def close(self):
            pass

    def provision(runner, groups, **kw):
        rec = Record("provision.docking", "provision")
        rec.add("verified", UNVERIFIED, "vina:calculation left to the docking job",
                deferred_only=deferred_only)
        return [rec]

    def run_scenarios(ctx, **kw):
        rec = Record("scenario.07.molecular_docking", "scenario")
        rec.add("redocking", PASS, "0.42 Å")
        return [rec]
    monkeypatch.setattr(validate_jobs, "AgentRunner", _Runner)
    monkeypatch.setattr(scenarios, "run_provision", provision)
    monkeypatch.setattr(scenarios, "run_scenarios", run_scenarios)
    assert main(["validate", "--out", str(tmp_path), "--approve-jobs", "--provision", "docking",
                 "--scenarios", "7", "--require-pass"]) == code
    doc = json.loads((tmp_path / "agent_regression_report.json").read_text())
    assert doc["provisioned"] == ["docking"] and doc["scenarios_run"] == [7]
