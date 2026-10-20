"""The docking job on prepared inputs (PDBQT): the Python a browser job calls (dock_read, run_dock,
verify_pipeline), and the runner's pipeline.dock in that mode.

In the browser each Vina run is computed by the page (AutoDock Vina 1.2.7 compiled to WebAssembly)
and written as a ``tcmstudio.vina-result/1`` file that ``run_dock`` reads back through
``bioagent.docking.prepared.PrecomputedVina``. Vina is not needed here: the results are written
from the native top poses the parity fixture records (studio/web/test/fixtures/vina, seed 1 of
native vina 1.2.7 on the same receptor, ligands and box), one pose a ligand, and the crystal pose
for the reference ligand. They are fixtures, not measurements; the WebAssembly engine itself is run
by studio/web/test/scientific/vina.test.mjs and the browser job by studio/web/test/runtime.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path

import pytest

from tcmstudio import browser_jobs
from tcmstudio.browser_jobs import call

ROOT = Path(__file__).resolve().parents[3]
FIX = ROOT / "BioScience-Harness" / "tests" / "fixtures" / "docking" / "prepared"
WEB_FIX = ROOT / "studio" / "web" / "test" / "fixtures"
NAMES = ["benzamidine", "p_methylbenzamidine", "baicalein", "ibuprofen", "ferulic_acid", "paeonol"]
# the box the pipeline builds around the reference ligand (the fixture's README)
CENTER, SIZE = [-1.8555000000000001, 14.366, 16.747999999999998], [18.0, 18.0, 18.0]

pytestmark = pytest.mark.skipif(not FIX.is_dir(), reason="the BioScience-Harness fixtures are not beside this checkout")


def _ok(text: str) -> dict:
    doc = json.loads(text)
    assert doc["ok"] is True, doc
    return doc["result"]


def _error(text: str) -> dict:
    doc = json.loads(text)
    assert doc["ok"] is False, doc
    return doc["error"]


@pytest.fixture()
def inputs(tmp_path):
    """The prepared fixture's files, copied as the page copies project files into a job."""
    d = tmp_path / "inputs"
    d.mkdir()
    for name in ("3ptb_receptor.pdbqt", "ligands.pdbqt", "ben_crystal.pdbqt"):
        shutil.copyfile(FIX / name, d / name)
    return {"receptor": str(d / "3ptb_receptor.pdbqt"), "ligands": str(d / "ligands.pdbqt"),
            "reference_ligand": str(d / "ben_crystal.pdbqt")}


def _results(plan: dict, work: Path, results: Path, *, change: dict | None = None) -> None:
    """One tcmstudio.vina-result/1 file per run of the plan, as the page writes them: each ligand's native top
    pose (seed 1) and its energies, the reference ligand in its crystal pose. ``change`` maps a key to a function
    that edits that result."""
    parity = {lig["name"]: lig for lig in json.loads((WEB_FIX / "vina_parity.json").read_text())["ligands"]}
    results.mkdir(parents=True, exist_ok=True)
    runs = ([plan["reference"]] if "reference" in plan else []) + plan["ligands"]
    for run in runs:
        text = (work / run["file"]).read_text()
        if run["key"] == "reference":
            # the crystal pose as Vina would write it: its score in the remark (bioagent.docking.prepared holds the
            # energies to it)
            pose, energies = "REMARK VINA RESULT:    -6.180      0.000      0.000\n" + text, [[-6.18, -6.5, -0.03, 0.35, -0.03]]
        else:
            pose = (WEB_FIX / "vina" / f"{run['name']}.top.pdbqt").read_text()
            energies = [parity[run["name"]]["dock"]["1"]["energies_raw"][0]]
        doc = {"schema": "tcmstudio.vina-result/1", "key": run["key"],
               "receptor_sha256": plan["receptor"]["sha256"], "ligand_sha256": hashlib.sha256(text.encode()).hexdigest(),
               "params": {**plan["params"], "center": plan["box"]["center"], "size": plan["box"]["size"]},
               "engine": {"name": "AutoDock Vina", "version": "1.2.7", "build": "native"},
               "energies": energies, "poses": f"MODEL 1\n{pose}ENDMDL\n", "seconds": 1.5}
        if change and run["key"] in change:
            doc = change[run["key"]](doc)
        (results / f"{run['key']}.json").write_text(json.dumps(doc))


def _read(inputs: dict, out: Path, **more) -> dict:
    return _ok(call("dock_read", json.dumps({**inputs, "seed": 1, **more, "out_dir": str(out)})))


def _dock(inputs: dict, tmp_path: Path, *, change: dict | None = None, **more) -> tuple[dict, Path]:
    work = tmp_path / "work"
    plan = _read(inputs, work, **more)
    _results(plan, work, tmp_path / "results", change=change)
    out = tmp_path / "out"
    ended = _ok(call("run_dock", json.dumps({**inputs, "seed": 1, **more, "plan_dir": str(work / "plan"),
                                            "results_dir": str(tmp_path / "results"), "out_dir": str(out)})))
    return ended, out


# ------------------------------------------------------------------ dock_read

def test_dock_read_writes_the_plan_of_vina_runs(inputs, tmp_path):
    from bioagent.docking.prepared import read_ligands, read_receptor

    logs: list = []
    plan = _ok(call("dock_read", json.dumps({**inputs, "out_dir": str(tmp_path / "work")}),
                    lambda kind, data: logs.append((kind, data))))
    assert plan["schema"] == browser_jobs.DOCK_PLAN_SCHEMA == "tcmstudio.dock-plan/1"
    receptor = read_receptor((FIX / "3ptb_receptor.pdbqt").read_text())
    # the fixture's README records the receptor's digest; its line ends are \n, so it is also the file's
    assert plan["receptor"] == {"file": "plan/receptor.pdbqt", "sha256": receptor.sha256, "atoms": receptor.atoms,
                                "heavy_atoms": receptor.heavy_atoms, "chains": receptor.chains,
                                "residues": receptor.residues}
    assert receptor.sha256 == "f0728cc12f64538310570ded883b305896df6f6cb86e50c72a419ba1386edd13"
    ligands = read_ligands((FIX / "ligands.pdbqt").read_text(), "ligands")
    assert [lig["name"] for lig in plan["ligands"]] == NAMES == [lig.name for lig in ligands]
    assert plan["ligands"][2] == {"index": 2, "key": "ligand-2", "name": "baicalein", "file": "plan/ligand-2.pdbqt",
                                  "sha256": ligands[2].sha256, "heavy_atoms": ligands[2].heavy_atoms,
                                  "torsions": ligands[2].torsions}
    assert plan["reference"] == {"key": "reference", "name": "BEN", "file": "plan/reference.pdbqt",
                                 "sha256": "a58af3f6373183be9034a71766ea56658736daef13bd7cfef8733b8b766bf5b2",
                                 "heavy_atoms": 9, "torsions": 1}
    assert plan["box"] == {"center": CENTER, "size": SIZE, "source": "reference ligand"}
    assert plan["params"] == {"scoring": "vina", "exhaustiveness": 8, "n_poses": 9, "seed": 42,
                              "energy_range": 3.0, "spacing": 0.375, "min_rmsd": 1.0, "max_evals": 0}
    # each file is the exact text Vina is given, at the digest the plan names; the plan is written as returned
    work = tmp_path / "work"
    for run in [plan["receptor"], plan["reference"], *plan["ligands"]]:
        assert hashlib.sha256((work / run["file"]).read_bytes()).hexdigest() == run["sha256"], run["file"]
    assert (work / "plan" / "receptor.pdbqt").read_bytes() == (FIX / "3ptb_receptor.pdbqt").read_bytes()
    assert json.loads((work / "plan" / "dock_plan.json").read_text()) == plan
    assert sorted(p.name for p in (work / "plan").iterdir()) == sorted(
        ["dock_plan.json", "receptor.pdbqt", "reference.pdbqt", *(f"ligand-{k}.pdbqt" for k in range(6))])
    # the box and the settings are what the vina parity fixture records for the same files
    parity = json.loads((WEB_FIX / "vina_parity.json").read_text())
    assert parity["box"] == plan["box"] and parity["receptor_sha256"] == plan["receptor"]["sha256"]
    assert [lig["sha256"] for lig in parity["ligands"]] == [lig["sha256"] for lig in plan["ligands"]]
    assert logs == [("log", {"level": "info", "text": "dock read: receptor 701 atoms, 6 ligands, reference BEN; "
                                                      "box reference ligand; 7 Vina runs to compute"})]


def test_the_site_is_the_box_given_or_the_one_built_around_the_reference(inputs, tmp_path):
    given = _read(inputs, tmp_path / "a", center=[1, 2.5, -3], size=[20, 22, 24])
    assert given["box"] == {"center": [1.0, 2.5, -3.0], "size": [20.0, 22.0, 24.0], "source": "given"}
    # a size alone sets the sides of the box built around the reference ligand
    sized = _read(inputs, tmp_path / "b", size=[24, 24, 24])
    assert sized["box"] == {"center": CENTER, "size": [24.0, 24.0, 24.0], "source": "reference ligand"}
    without = {k: v for k, v in inputs.items() if k != "reference_ligand"}
    plain = _read(without, tmp_path / "c", center=CENTER, size=SIZE)
    assert "reference" not in plain and len(plain["ligands"]) == 6
    assert not (tmp_path / "c" / "plan" / "reference.pdbqt").exists()
    for args, needle in (({}, "name the site"), ({"center": [0, 0, 0]}, "center needs size"),
                         ({"center": [0, 0], "size": [1, 1, 1]}, "center: three"),
                         ({"center": [0, 0, 0], "size": [0, 1, 1]}, "longer than 0")):
        error = _error(call("dock_read", json.dumps({**without, **args, "out_dir": str(tmp_path / "d")})))
        assert error["type"] == "bad_arguments" and needle in error["message"], (args, error)


@pytest.mark.parametrize("args, needle", [
    ({"seed": 0}, "seed: between 1 and 2147483647"),
    ({"exhaustiveness": 65}, "exhaustiveness: between 1 and 64"),
    ({"poses": 0}, "poses: between 1 and 20"),
    ({"scoring": "ad4"}, "scoring: one of vina, vinardo"),
    ({"seed": "42"}, "seed: a whole number"),
    ({"site_ligand": "BEN"}, "unknown argument(s) site_ligand"),
    ({"energy_range": 4}, "unknown argument(s) energy_range"),
    ({"receptor": None}, "receptor: required"),
])
def test_settings_are_held_to_the_runners_checks(inputs, tmp_path, args, needle):
    error = _error(call("dock_read", json.dumps({**inputs, **args, "out_dir": str(tmp_path)})))
    assert error["type"] == "bad_arguments" and needle in error["message"], error
    assert not (tmp_path / "plan").exists()


def test_inputs_that_need_preparing_are_refused_for_the_runner(inputs, tmp_path):
    pdb = tmp_path / "3ptb_pocket.pdb"
    shutil.copyfile(ROOT / "BioScience-Harness" / "tests" / "fixtures" / "docking" / "3ptb_pocket.pdb", pdb)
    smi = tmp_path / "set.smi"
    smi.write_text("NC(=N)c1ccccc1 benzamidine\n")
    for args, needle in (({"receptor": str(pdb)}, "receptor: 3ptb_pocket.pdb is not a prepared .pdbqt"),
                         ({"ligands": str(smi)}, "ligands: set.smi is not a prepared .pdbqt"),
                         ({"ligands": "CCO"}, "ligands: CCO is not a prepared .pdbqt")):
        error = _error(call("dock_read", json.dumps({**inputs, **args, "out_dir": str(tmp_path / "o")})))
        assert error["type"] == "refused" and error["need"] == "runner" and needle in error["message"], error
        assert "the runner prepares and docks them" in error["message"]
    # a reference ligand that is not a .pdbqt is refused as the runner refuses it, as bad arguments
    error = _error(call("dock_read", json.dumps({**inputs, "reference_ligand": str(smi), "out_dir": str(tmp_path)})))
    assert error["type"] == "bad_arguments" and "reference_ligand: set.smi is not a .pdbqt" in error["message"]
    error = _error(call("dock_read", json.dumps({**inputs, "ligands": str(tmp_path / "gone.pdbqt"),
                                                 "out_dir": str(tmp_path)})))
    assert error["type"] == "bad_arguments" and "gone.pdbqt is not a file here" in error["message"]


def test_inputs_vina_would_not_read_are_refused_wherever_they_run(inputs, tmp_path):
    text = (FIX / "3ptb_receptor.pdbqt").read_text()
    bad = tmp_path / "models.pdbqt"
    bad.write_text("MODEL 1\n" + text + "ENDMDL\n")
    error = _error(call("dock_read", json.dumps({**inputs, "receptor": str(bad), "out_dir": str(tmp_path)})))
    assert error["type"] == "refused" and "need" not in error and "MODEL" in error["message"], error
    two = tmp_path / "two.pdbqt"
    two.write_text((FIX / "ligands.pdbqt").read_text())
    error = _error(call("dock_read", json.dumps({**inputs, "reference_ligand": str(two), "out_dir": str(tmp_path)})))
    assert error["type"] == "refused" and "holds 6 ligands; a reference is one" in error["message"], error
    binary = tmp_path / "binary.pdbqt"
    binary.write_bytes(b"\xff\xfe\x00ATOM")
    error = _error(call("dock_read", json.dumps({**inputs, "ligands": str(binary), "out_dir": str(tmp_path)})))
    assert error["type"] == "refused" and "not a text file (UTF-8)" in error["message"], error


def test_more_than_a_browser_worker_holds_is_refused_for_memory(inputs, tmp_path, monkeypatch):
    def refused(**args) -> dict:
        error = _error(call("dock_read", json.dumps({**inputs, **args, "out_dir": str(tmp_path)})))
        assert error["type"] == "refused" and error["need"] == "memory", error
        return error
    monkeypatch.setattr(browser_jobs, "DOCK_LIGANDS_MAX", 5)
    assert "6 ligands exceed the 5 of one run" in refused()["message"]
    monkeypatch.setattr(browser_jobs, "DOCK_LIGANDS_MAX", 100)
    monkeypatch.setattr(browser_jobs, "DOCK_RECEPTOR_ATOMS_MAX", 700)
    assert "the receptor's 701 atoms exceed the 700" in refused()["message"]
    monkeypatch.setattr(browser_jobs, "DOCK_RECEPTOR_ATOMS_MAX", 100_000)
    # a ligand's atoms as its PDBQT lists them (hydrogens included); the reference ligand too
    monkeypatch.setattr(browser_jobs, "DOCK_LIGAND_ATOMS_MAX", 20)
    message = refused()["message"]
    assert "ligands of more than 20 atoms" in message and "baicalein (23)" in message, message
    assert "BEN" not in message
    monkeypatch.setattr(browser_jobs, "DOCK_LIGAND_ATOMS_MAX", 80)
    message = refused(center=[0, 0, 0], size=[61, 60, 60])["message"]
    assert "a box of 61 x 60 x 60 Å (219,600 Å³) exceeds the 216,000 Å³" in message
    assert _read(inputs, tmp_path / "ok", center=[0, 0, 0], size=[60, 60, 60])["box"]["size"] == [60.0, 60.0, 60.0]
    # counted before anything is read in full
    monkeypatch.setattr(browser_jobs, "DOCK_LIGANDS_MAX", 5)
    monkeypatch.setattr("bioagent.docking.prepared.read_ligands", lambda *a, **k: pytest.fail("read in full"))
    refused()


# ------------------------------------------------------------------ run_dock

def test_run_dock_runs_the_prepared_pipeline_on_the_pages_vina_results(inputs, tmp_path):
    from bioagent.docking.prepared import PIPELINE, PrecomputedVina, PreparedDockConfig, run_prepared_docking

    ended, out = _dock(inputs, tmp_path)
    assert ended["exit"] == 0 and ended["failed"] == [] and ended["refused"] is None and ended["need"] is None
    summary = ended["summary"]
    assert summary["ligands"] == summary["docked"] == 6 and summary["failures"] == {}
    assert summary["validated"] is True and summary["complete"] is True
    assert summary["validation"] == {"ligand": "BEN", "top_pose_rmsd": 0.0,
                                     "rmsd": "atom order (upper bound of the symmetry-aware RMSD)",
                                     "threshold": 2.0, "passed": True}
    assert summary["engine"] == {"name": "AutoDock Vina", "version": "1.2.7", "build": "native"}
    assert summary["box"] == {"center": CENTER, "size": SIZE, "source": "reference ligand"}
    parity = {lig["name"]: lig for lig in json.loads((WEB_FIX / "vina_parity.json").read_text())["ligands"]}
    best = round(parity["baicalein"]["dock"]["1"]["best_raw"], 3)
    assert summary["best"][0] == {"ligand": "baicalein", "best_score": best}
    assert [b["ligand"] for b in summary["best"]] == sorted(
        NAMES, key=lambda n: parity[n]["dock"]["1"]["energies_raw"][0][0])
    run = json.loads((out / "run.json").read_text())
    assert run["pipeline"] == PIPELINE and run["mode"] == "prepared" and run["complete"] is True
    assert run["config"]["seed"] == 1 and set(run["inputs"]) == set(inputs.values())
    rows = (out / "scores.tsv").read_text().splitlines()
    assert rows[0].split("\t")[:2] == ["ligand", "best_score"] and len(rows) == 7
    assert sorted(p.name for p in (out / "poses").iterdir()) == sorted(f"{n}.pdbqt" for n in NAMES)
    # the same pipeline called directly on the same results writes the same files
    direct = tmp_path / "direct"
    run_prepared_docking(inputs["receptor"], inputs["ligands"], PreparedDockConfig(seed=1), direct,
                         engine=PrecomputedVina(tmp_path / "results"), reference_path=inputs["reference_ligand"])
    for name in ("scores.tsv", "report.md", "report.html", "limitations.md", "poses/ibuprofen.pdbqt"):
        assert (out / name).read_bytes() == (direct / name).read_bytes(), name


def test_run_dock_says_what_it_does_as_it_does_it(inputs, tmp_path):
    work = tmp_path / "work"
    plan = _read(inputs, work)
    _results(plan, work, tmp_path / "results")
    events: list = []
    _ok(call("run_dock", json.dumps({**inputs, "seed": 1, "plan_dir": str(work / "plan"),
                                    "results_dir": str(tmp_path / "results"), "out_dir": str(tmp_path / "out")}),
             lambda kind, data: events.append((kind, data))))
    logs = [data["text"] for kind, data in events if kind == "log"]
    assert logs == ["dock: 6 ligands and the reference BEN on the Vina results in results", "dock: complete"]
    fractions = [data["fraction"] for kind, data in events if kind == "progress"]
    assert fractions == sorted(fractions) and fractions[-1] == 1.0


def test_a_ligand_vina_refused_is_a_failure_and_a_refused_reference_an_unvalidated_run(inputs, tmp_path):
    def refuse(doc):
        return {k: v for k, v in doc.items() if k not in ("energies", "poses", "seconds")} | {
            "error": "PDBQT parsing error: Unknown or inappropriate tag"}
    ended, out = _dock(inputs, tmp_path / "a", change={"ligand-3": refuse})
    assert ended["exit"] == 0 and ended["summary"]["docked"] == 5
    assert ended["summary"]["failures"] == {"ibuprofen": "PDBQT parsing error: Unknown or inappropriate tag"}
    assert not (out / "poses" / "ibuprofen.pdbqt").exists()
    ended, _ = _dock(inputs, tmp_path / "b", change={"reference": refuse})
    assert ended["exit"] == 1 and ended["failed"] == ["redock_validation"]
    assert ended["summary"]["validated"] is False and "error" in ended["summary"]["validation"]
    assert "Vina refused the reference ligand BEN" in ended["reasons"]["redock_validation"]


def test_results_that_are_not_this_runs_are_refused(inputs, tmp_path):
    work = tmp_path / "work"
    plan = _read(inputs, work)
    base = {**inputs, "seed": 1, "plan_dir": str(work / "plan"), "out_dir": str(tmp_path / "out")}

    def ended(results: Path, **more) -> dict:
        return _ok(call("run_dock", json.dumps({**base, "results_dir": str(results), **more})))
    # a run that was never computed
    _results(plan, work, tmp_path / "r1")
    (tmp_path / "r1" / "ligand-4.json").unlink()
    gone = ended(tmp_path / "r1")
    assert gone["exit"] == 2 and "no Vina result for ligand-4" in gone["refused"] and gone["need"] is None
    # computed with another seed, or on another ligand
    _results(plan, work, tmp_path / "r2", change={"ligand-0": lambda d: {**d, "params": {**d["params"], "seed": 2}}})
    assert "computed with other parameters: seed" in ended(tmp_path / "r2")["refused"]
    _results(plan, work, tmp_path / "r3", change={"ligand-1": lambda d: {**d, "ligand_sha256": "0" * 64}})
    assert "computed on another ligand" in ended(tmp_path / "r3")["refused"]
    # energies that are not their poses' (an edited result): refused, not ranked first
    _results(plan, work, tmp_path / "r5", change={"ligand-5": lambda d: {**d, "energies": [[-12.5, *d["energies"][0][1:]]]}})
    assert "paeonol: pose 1's score as Vina wrote it (-5.632) is not its energy -12.5" in ended(tmp_path / "r5")["refused"]
    # a WebAssembly build's results: only the pinned build's (run.json names it as the engine)
    from tcmstudio.catalog import VINA_WASM

    def wasm(sha):
        return lambda d: {**d, "engine": {**d["engine"], "build": "wasm", "wasm_sha256": sha}}
    _results(plan, work, tmp_path / "r6", change={k: wasm("0" * 64) for k in ["reference", *(f"ligand-{i}" for i in range(6))]})
    other_build = ended(tmp_path / "r6")
    assert other_build["exit"] == 2 and "a WebAssembly build with SHA-256 000000000000…, not the pinned" in other_build["refused"]
    _results(plan, work, tmp_path / "r7", change={k: wasm(VINA_WASM["sha256"]) for k in ["reference", *(f"ligand-{i}" for i in range(6))]})
    pinned = ended(tmp_path / "r7")
    assert pinned["exit"] == 0 and pinned["summary"]["engine"]["wasm_sha256"] == VINA_WASM["sha256"]
    # the arguments must still be the ones the plan was made for
    _results(plan, work, tmp_path / "r4")
    other = ended(tmp_path / "r4", seed=2)
    assert other["exit"] == 2 and "not the ones the Vina runs were planned for" in other["refused"]
    assert "params" in other["refused"]
    without = ended(tmp_path / "r4", reference_ligand=None, center=CENTER, size=SIZE)
    assert without["exit"] == 2 and "(dock_plan.json: box, reference differ)" in without["refused"]
    missing = _ok(call("run_dock", json.dumps({**base, "plan_dir": str(tmp_path), "results_dir": str(tmp_path / "r4")})))
    assert missing["exit"] == 2 and "dock_plan.json cannot be read" in missing["refused"]
    # more than a worker holds is refused here too
    error = _ok(call("run_dock", json.dumps({**base, "results_dir": str(tmp_path / "r4"), "size": [70, 70, 70]})))
    assert error["exit"] == 2 and error["need"] == "memory"


def test_verify_pipeline_checks_a_docking_run_as_the_runner_does(inputs, tmp_path):
    from types import SimpleNamespace

    from tcmstudio.jobs import JobService
    from tcmstudio.kinds import KINDS

    _, out = _dock(inputs, tmp_path)
    job = tmp_path / "job"
    shutil.move(str(out), str(job / "out"))
    # the run records its inputs where it read them: they stay where they were
    checked = _ok(call("verify_pipeline", json.dumps({"kind": "pipeline.dock", "out_dir": str(job / "out")})))
    assert checked["status"] == "PASS" and checked["problems"] == [], checked["problems"]
    assert [c["name"] for c in checked["checks"]] == ["verify_run", "required_artefacts", "validator:run"]
    assert checked["report"]["pipeline"] == "bioagent.docking.prepared" and checked["report"]["complete"] is True
    assert JobService._check_artefacts(None, SimpleNamespace(dir=job), KINDS["pipeline.dock"]) == []
    # an edited table fails the pipeline's check and the runner's validator alike
    scores = job / "out" / "scores.tsv"
    lines = scores.read_text().splitlines(keepends=True)
    scores.write_text("".join([lines[0], lines[1].replace("\t-", "\t-1"), *lines[2:]]))
    checked = _ok(call("verify_pipeline", json.dumps({"kind": "pipeline.dock", "out_dir": str(job / "out")})))
    status = {c["name"]: c["status"] for c in checked["checks"]}
    assert checked["status"] == "FAIL" and status["verify_run"] == status["validator:run"] == "FAIL"
    assert "output scores.tsv has changed" in checked["problems"]


# ------------------------------------------------------------------ the runner's pipeline.dock

def test_the_runners_validator_checks_either_mode_by_the_pipeline_run_json_names(inputs, tmp_path, monkeypatch):
    import bioagent.docking.pipeline as full

    from tcmstudio.kinds import DOCK_PIPELINES, KINDS

    check = KINDS["pipeline.dock"].validators["run"]
    _, out = _dock(inputs, tmp_path)
    assert check(out / "run.json") is None
    other = tmp_path / "other"
    other.mkdir()
    (other / "run.json").write_text(json.dumps({"pipeline": "bioagent.omics.scrna"}))
    assert check(other / "run.json") == ("run.json records the pipeline 'bioagent.omics.scrna', not one of "
                                         "bioagent.docking, bioagent.docking.prepared")
    # a run of the full pipeline is checked by that pipeline's own verify_run
    seen = []
    monkeypatch.setattr(full, "verify_run", lambda d: (seen.append(Path(d)), (True, []))[1])
    (other / "run.json").write_text(json.dumps({"pipeline": "bioagent.docking"}))
    assert check(other / "run.json") is None and seen == [other]
    assert DOCK_PIPELINES == {"bioagent.docking": "bioagent.docking.pipeline",
                              "bioagent.docking.prepared": "bioagent.docking.prepared"}
    (other / "run.json").write_text("[1, 2]")
    assert "not one of" in check(other / "run.json")


@pytest.fixture()
def runner(tmp_path):
    from tcmstudio.kinds import PrepareContext, StartContext

    files = {}
    for name in ("3ptb_receptor.pdbqt", "ligands.pdbqt", "ben_crystal.pdbqt", "3ptb_pocket.pdb"):
        src = FIX / name if name.endswith(".pdbqt") else FIX.parent / name
        dst = tmp_path / "uploads" / name
        dst.parent.mkdir(exist_ok=True)
        shutil.copyfile(src, dst)
        files[name] = (dst, {"upload": f"u_{len(files):016x}", "name": name, "sha256": "0" * 64,
                             "bytes": dst.stat().st_size})
    ctx = PrepareContext(job_id="j1", job_dir=tmp_path / "job", project_id=None, paths={}, data_env={},
                         find=files.get, settings={})
    start = StartContext(job_id="j1", job_dir=tmp_path / "job", project_id=None, paths={}, data_env={},
                         settings={}, device="cpu", threads=2)
    return ctx, start, {k: v[0] for k, v in files.items()}


def test_the_runners_prepared_mode_is_bioagent_dock_on_the_pdbqt_files(runner):
    from tcmstudio.kinds import KINDS, OUTPUT_TOKEN

    ctx, start, paths = runner
    kind = KINDS["pipeline.dock"]
    prepared = kind.prepare({"receptor": "3ptb_receptor.pdbqt", "ligands": "ligands.pdbqt",
                             "reference_ligand": "ben_crystal.pdbqt", "seed": 7}, ctx)
    assert prepared == {"receptor": str(paths["3ptb_receptor.pdbqt"]), "ligands": str(paths["ligands.pdbqt"]),
                        "allow_remote": False, "scoring": "vina", "exhaustiveness": 8, "poses": 9, "seed": 7,
                        "reference_ligand": str(paths["ben_crystal.pdbqt"])}
    assert set(ctx.inputs) == {"receptor", "ligands", "reference_ligand"}
    argv = kind.command(prepared, start)
    assert argv[3:] == ["bioagent.cli", "dock", str(paths["3ptb_receptor.pdbqt"]), str(paths["ligands.pdbqt"]),
                        f"--out={OUTPUT_TOKEN}", "--scoring=vina", "--exhaustiveness=8", "--poses=9", "--seed=7",
                        f"--reference-ligand={paths['ben_crystal.pdbqt']}"]
    assert not kind.needs_network(prepared) and not kind.sends_remote(prepared)
    # the box exactly as given: a coordinate the short form would round is passed in full
    boxed = kind.prepare({"receptor": "3ptb_receptor.pdbqt", "ligands": "ligands.pdbqt",
                          "center": CENTER, "size": [18, 18, 20.25]}, ctx)
    argv = kind.command(boxed, start)
    assert "--center=-1.8555000000000001,14.366,16.747999999999998" in argv and "--size=18,18,20.25" in argv
    assert not any(a.startswith("--reference-ligand") for a in argv)
    # a size alone sets the sides of the box built around the reference ligand
    sized = kind.prepare({"receptor": "3ptb_receptor.pdbqt", "ligands": "ligands.pdbqt",
                          "reference_ligand": "ben_crystal.pdbqt", "size": [24, 24, 24]}, ctx)
    assert sized["size"] == "24,24,24" and "center" not in sized


@pytest.mark.parametrize("params, needle", [
    ({"ligands": "CCO"}, "ligands: with a .pdbqt receptor, ligands is a .pdbqt file as well"),
    ({"ligands": "3ptb_pocket.pdb"}, "ligands: with a .pdbqt receptor"),
    ({"reference_ligand": "3ptb_pocket.pdb"}, "reference_ligand: with a .pdbqt receptor"),
    ({"site_ligand": "BEN"}, "site_ligand: a .pdbqt receptor is used as prepared"),
    ({"chains": "A"}, "chains: a .pdbqt receptor is used as prepared"),
    ({"allow_remote": True}, "allow_remote: a .pdbqt receptor is used as prepared"),
    ({"seed": 0}, "seed: between 1 and 2147483647"),
    ({"reference_ligand": None}, "give the docking site: center and size, or reference_ligand"),
    ({"center": [0, 0, 0]}, "center and size: three numbers each"),
    ({"reference_ligand": None, "size": [20, 20, 20]}, "center and size: three numbers each"),
    # the box as bioagent dock and dock_read hold it: finite, sides longer than 0, an empty centre no centre
    ({"size": [0, 18, 18]}, "size: every side of the box must be longer than 0 Å"),
    ({"center": CENTER, "size": [-1, 18, 18]}, "size: every side of the box must be longer than 0 Å"),
    ({"size": [float("nan"), 1, 1]}, "size: three finite numbers"),
    ({"center": [0, float("inf"), 0], "size": [18, 18, 18]}, "center: three finite numbers"),
    ({"center": []}, "center and size: three numbers each"),
])
def test_the_runner_refuses_what_the_prepared_mode_cannot_use(runner, params, needle):
    from tcmstudio.kinds import KINDS, BadParams

    ctx, _, _ = runner
    base = {"receptor": "3ptb_receptor.pdbqt", "ligands": "ligands.pdbqt", "reference_ligand": "ben_crystal.pdbqt"}
    with pytest.raises(BadParams, match=needle.replace("(", r"\(").replace(".", r"\.")):
        KINDS["pipeline.dock"].prepare({**base, **params}, ctx)


def test_a_pdb_receptor_keeps_the_full_pipeline(runner):
    from tcmstudio.kinds import KINDS, BadParams

    ctx, start, paths = runner
    kind = KINDS["pipeline.dock"]
    full = kind.prepare({"receptor": "3ptb_pocket.pdb", "ligands": "CCO", "center": [1.5, 2, 3],
                         "size": [18, 18, 18], "seed": 0}, ctx)
    assert full["seed"] == 0 and full["center"] == "1.5,2,3" and full["ligands"] == "CCO"
    assert kind.command(full, start)[5:7] == [str(paths["3ptb_pocket.pdb"]), "CCO"]
    with pytest.raises(BadParams, match="reference_ligand: for a prepared receptor"):
        kind.prepare({"receptor": "3ptb_pocket.pdb", "ligands": "CCO", "reference_ligand": "ben_crystal.pdbqt",
                      "center": [0, 0, 0], "size": [18, 18, 18]}, ctx)
    # prepared ligands with a PDB receptor: refused when submitted (bioagent dock would read the PDBQT as a .smi file)
    with pytest.raises(BadParams, match=r"ligands: prepared \.pdbqt ligands dock against a prepared \.pdbqt receptor"):
        kind.prepare({"receptor": "3ptb_pocket.pdb", "ligands": "ligands.pdbqt", "center": [0, 0, 0],
                      "size": [18, 18, 18]}, ctx)
    with pytest.raises(BadParams, match="size: every side of the box must be longer than 0"):
        kind.prepare({"receptor": "3ptb_pocket.pdb", "ligands": "CCO", "center": [0, 0, 0], "size": [0, 18, 18]}, ctx)


def test_the_command_line_runs_dock_read(inputs, tmp_path):
    import os
    import subprocess
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(p for p in sys.path if p)}
    res = subprocess.run([sys.executable, "-m", "tcmstudio.browser_jobs", "dock_read", "--args",
                          json.dumps({**inputs, "out_dir": str(tmp_path)})], capture_output=True, text=True,
                         timeout=120, env=env, cwd=tmp_path)
    assert res.returncode == 0, res.stderr
    assert json.loads(res.stdout)["result"] == json.loads((tmp_path / "plan" / "dock_plan.json").read_text())
