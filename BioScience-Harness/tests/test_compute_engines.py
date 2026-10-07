"""Adapters for Boltz, Chai-1, ProteinMPNN and OpenMM, without running the tools.

What is tested: that each adapter refuses with its reason when its tool is absent, and its
result then says no model ran; how each renders a task in its tool's input format; and how
each reads its tool's output. The outputs in ``tests/fixtures/compute`` come from the first
real runs (docs/compute-tasks.md; 2026-10-07, CPU): ``boltz_ubq_aspirin_*`` (boltz 2.2.1,
ubiquitin and aspirin: the model gzipped, the confidence summary, Boltz's record of its
chain numbering, the PAE) and ``boltz_reorder_*`` (three chains Boltz numbers in another
order than they were given). ``complex_model.cif`` (two MKTAY chains and an ethanol in
python-ihm's layout) is still hand-made, for the Chai-1 reader and the mmCIF reader.

``test_structure_engines_real.py`` runs the tools themselves. Here, one test drives
``BoltzEngine.run`` end to end through the reviewed environment, the job protocol and
validation, against a *stand-in interpreter* that answers the probe and writes the real
run's files. Its assertions about provenance name the stand-in: what it proves is the
plumbing, not Boltz.
"""

from __future__ import annotations

import gzip
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pytest

from bioagent.backends.environments import ExecutionEnvironments, reviewed
from bioagent.backends.jobs import (Artefact, JobController, JobRef, LocalSubprocessJobs,
                                    open_jobs)
from bioagent.status import ExecutionStatus
from bioagent.structure.complex import (BondConstraint, Chain, ComplexPredictionTask,
                                        ContactConstraint, Ligand, Modification,
                                        PocketConstraint, Token)
from bioagent.structure.design import SequenceDesignTask
from bioagent.structure.dynamics import DynamicsTask
from bioagent.structure.engines import (_OPENMM_SCRIPT, _RUN_MPNN, BoltzEngine, ChaiEngine,
                                        EngineProbe, EngineRefusal, OpenMMEngine,
                                        ProteinMPNNEngine)
from bioagent.structure.tasks import ModelSpec, TaskInvalid

FIX = Path(__file__).parent / "fixtures"
COMPUTE = FIX / "compute"
BACKBONE = FIX / "structure" / "1ubq.pdb"
UBQ = "MQIFVKTLTGKTITLEVEPSDTIENVKAKIQDKEGIPPDQQRLIFAGKQLEDGRTLSDYNIQKESTLHLVLRLRGG"
ASPIRIN = "CC(=O)OC1=CC=CC=C1C(=O)O"
PROBE = EngineProbe(True, "/env/bin/python", "a reviewed environment", "2.2.1")


def mini(**kw) -> ComplexPredictionTask:
    """The complex the hand-made model holds: MKTAY twice, and ethanol."""
    base = dict(name="mini", chains=(Chain(("A", "B"), "MKTAY"),),
                ligands=(Ligand("L", smiles="CCO"),))
    base.update(kw)
    return ComplexPredictionTask(**base)


def ubq_aspirin(**kw) -> ComplexPredictionTask:
    """The complex Boltz-2 predicted for the real fixtures: ubiquitin and aspirin."""
    base = dict(name="ubq_aspirin", chains=(Chain("A", UBQ),),
                ligands=(Ligand("L", smiles=ASPIRIN),), recycling_steps=1, sampling_steps=50)
    base.update(kw)
    return ComplexPredictionTask(**base)


def boltz_files(prefix: str, tmp_path: Path) -> dict[str, Path]:
    """A real Boltz run's outputs from the fixtures, the model unzipped as Boltz wrote it."""
    model = tmp_path / f"{prefix}_model_0.cif"
    model.write_bytes(gzip.decompress((COMPUTE / f"{prefix}_model_0.cif.gz").read_bytes()))
    files = {"model": model, "confidence": COMPUTE / f"{prefix}_confidence.json",
             "record": COMPUTE / f"{prefix}_record.json"}
    if (COMPUTE / f"{prefix}_pae.npz").is_file():
        files["pae"] = COMPUTE / f"{prefix}_pae.npz"
    return files


def boltz_cache(tmp_path: Path) -> Path:
    cache = tmp_path / "boltz-cache"
    (cache / "mols").mkdir(parents=True)
    (cache / "mols.tar").write_bytes(b"not really the CCD molecules")
    (cache / "boltz2_conf.ckpt").write_bytes(b"not really weights")
    (cache / "boltz2_aff.ckpt").write_bytes(b"not really affinity weights")
    return cache


def arts(**paths: Path) -> dict[str, Artefact]:
    out = {}
    for name, path in paths.items():
        data = path.read_bytes()
        out[name] = Artefact(name, path, hashlib.sha256(data).hexdigest(), len(data))
    return out


def stand_in(tmp_path: Path, *, found: bool = True, mode: str = "ok",
             version: str = "2.2.1") -> Path:
    """An executable that answers like a Python with Boltz installed, and is not one: it
    writes the outputs of the real ubiquitin-aspirin run where Boltz would write them."""
    script = tmp_path / "stand_in.py"
    script.write_text(f'''import gzip, json, shutil, sys, time
from pathlib import Path
FIX, FOUND, MODE, VERSION = Path({str(COMPUTE)!r}), {found!r}, {mode!r}, {version!r}
args = sys.argv[1:]
if args[:1] == ["-I"]:
    args = args[1:]
if args[0] == "-c":
    if "find_spec" in args[1]:
        print(json.dumps({{"found": {{m: FOUND for m in args[3:]}},
                          "version": VERSION if FOUND else "", "python": "3.12"}}))
    else:
        sys.argv = ["-c", *args[2:]]
        exec(compile(args[1], "<stand-in>", "exec"))
    sys.exit(0)
if args[:3] == ["-m", "boltz.main", "predict"]:
    if MODE == "crash":
        sys.stderr.write("RuntimeError: CUDA out of memory\\n")
        sys.exit(1)
    if MODE == "slow":
        time.sleep(1.5)
    stem = Path(args[3]).stem
    out = Path(args[args.index("--out_dir") + 1]) / f"boltz_results_{{stem}}"
    pred = out / "predictions" / stem
    pred.mkdir(parents=True)
    (out / "processed" / "records").mkdir(parents=True)
    (pred / f"{{stem}}_model_0.cif").write_bytes(
        gzip.decompress((FIX / "boltz_ubq_aspirin_model_0.cif.gz").read_bytes()))
    shutil.copy(FIX / "boltz_ubq_aspirin_confidence.json",
                pred / f"confidence_{{stem}}_model_0.json")
    shutil.copy(FIX / "boltz_ubq_aspirin_record.json",
                out / "processed" / "records" / f"{{stem}}.json")
    sys.exit(0)
sys.exit("stand-in: unexpected arguments %r" % (args,))
''')
    wrapper = tmp_path / "python"
    wrapper.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n')
    wrapper.chmod(0o755)
    return wrapper


def environments(project: str, python: Path, root: Path | None = None
                 ) -> ExecutionEnvironments:
    entry = {"python": str(python), **({"root": str(root)} if root else {})}
    return ExecutionEnvironments.parse(reviewed({"interpreters": {project: entry}},
                                                by="tests", on="2026-10-07"))


# ------------------------------------------------------------------ refusals
@pytest.mark.parametrize("engine,task", [
    (BoltzEngine, mini()),
    (ChaiEngine, mini(model=ModelSpec("chai-1"))),
    (ProteinMPNNEngine, SequenceDesignTask("d", str(BACKBONE), "A")),
    (OpenMMEngine, DynamicsTask("md", str(BACKBONE), duration_ps=0)),
])
def test_an_absent_tool_is_unavailable_and_nothing_is_reported_as_run(engine, task,
                                                                     tmp_path):
    if any(importlib.util.find_spec(m) for m in engine.modules if m not in ("numpy",)):
        pytest.skip(f"{engine.name}'s modules are importable here")
    result = engine().run(task, tmp_path)
    assert result.status is ExecutionStatus.UNAVAILABLE
    assert "cannot be imported there" in result.reason
    assert result.reason.endswith("no model was run")
    assert result.provenance.ran is False and result.provenance.job is None
    assert sys.executable in result.reason, "the reason names the interpreter it asked"
    assert not (tmp_path / task.name / "jobs").exists(), "no job was submitted"
    assert json.loads(json.dumps(result.to_dict()))["provenance"]["ran"] is False


def test_a_reviewed_interpreter_without_the_tool_is_named_in_the_refusal(tmp_path):
    python = stand_in(tmp_path, found=False)
    result = BoltzEngine(environments("boltz", python)).run(mini(), tmp_path / "work")
    assert result.status is ExecutionStatus.UNAVAILABLE
    assert f"boltz is not installed in {python} (reviewed environment" in result.reason
    missing = environments("boltz", tmp_path / "nowhere" / "python")
    gone = BoltzEngine(missing).run(mini(), tmp_path / "work")
    assert gone.status is ExecutionStatus.UNAVAILABLE and "does not exist" in gone.reason


def test_a_task_for_another_model_or_with_unknown_options_is_refused_before_detection():
    with pytest.raises(TaskInvalid, match="chai-1 runs chai-1, not 'boltz-2'"):
        ChaiEngine().check(mini())
    with pytest.raises(TaskInvalid, match=r"has no options \['msa_depth'\]"):
        BoltzEngine().check(mini(model=ModelSpec("boltz-2", options={"msa_depth": 3})))
    with pytest.raises(TaskInvalid):
        BoltzEngine().check(mini(seed=-1))


# --------------------------------------------------------------------- Boltz
def test_boltz_renders_copies_modifications_ligands_and_constraints():
    task = mini(chains=(Chain(("A", "B"), "MKTAY", modifications=(Modification(3, "TPO"),)),
                        Chain("D", "ACGT", kind="dna")),
                ligands=(Ligand("L", smiles="CCO"), Ligand(("H", "I"), ccd="HEM")),
                constraints=(PocketConstraint("L", (Token("A", 2), Token("B", 4))),
                             ContactConstraint(Token("A", 1), Token("H", 1, "FE"), 8.0),
                             BondConstraint(Token("A", 3), Token("L", 1, "C1"))))
    doc = BoltzEngine().render(task)
    assert doc["version"] == 1
    protein, dna, ethanol, heme = doc["sequences"]
    assert protein == {"protein": {"id": ["A", "B"], "sequence": "MKTAY", "msa": "empty",
                                   "modifications": [{"position": 3, "ccd": "TPO"}]}}
    assert dna == {"dna": {"id": "D", "sequence": "ACGT"}}, "only proteins take an MSA"
    assert ethanol == {"ligand": {"id": "L", "smiles": "CCO"}}
    assert heme == {"ligand": {"id": ["H", "I"], "ccd": "HEM"}}
    pocket, contact, bond = doc["constraints"]
    assert pocket == {"pocket": {"binder": "L", "max_distance": 6.0,
                                 "contacts": [["A", 2], ["B", 4]]}}
    assert contact == {"contact": {"token1": ["A", 1], "token2": ["H", "FE"],
                                   "max_distance": 8.0}}
    assert bond == {"bond": {"atom1": ["A", 3, ""], "atom2": ["L", 1, "C1"]}}
    with_server = mini(use_msa_server=True, allow_remote=True)
    assert "msa" not in BoltzEngine().render(with_server)["sequences"][0]["protein"]


def test_boltz_refuses_constraints_it_cannot_express():
    unnamed = mini(constraints=(ContactConstraint(Token("A", 1), Token("L", 1)),))
    with pytest.raises(EngineRefusal, match="names a ligand contact by atom name"):
        BoltzEngine().render(unnamed)
    far = mini(constraints=(PocketConstraint("L", (Token("A", 1),), max_distance=25.0),))
    with pytest.raises(EngineRefusal, match="from 4 to 20 Å, not 25.0"):
        BoltzEngine().render(far)
    old = mini(model=ModelSpec("boltz-1"),
               constraints=(ContactConstraint(Token("A", 1), Token("B", 1)),))
    with pytest.raises(EngineRefusal, match="contact constraints are a Boltz-2 feature"):
        BoltzEngine().render(old)


def test_boltz_runs_only_from_weights_already_on_disk(tmp_path):
    engine = BoltzEngine()
    with pytest.raises(EngineRefusal, match="name the cache that already holds them"):
        engine.prepare(mini(), tmp_path, PROBE)
    cache = boltz_cache(tmp_path)
    (cache / "boltz2_aff.ckpt").unlink()
    task = mini(model=ModelSpec("boltz-2", options={"cache": str(cache)}))
    with pytest.raises(EngineRefusal, match="lacks boltz2_aff.ckpt; Boltz would download"):
        engine.prepare(task, tmp_path, PROBE)
    (cache / "boltz2_aff.ckpt").write_bytes(b"x")
    # boltz 2.2.1 tests for mols.tar before mols/: without the tar it downloads 1.86 GB
    # at start-up even though the extracted molecules are there.
    (cache / "mols.tar").unlink()
    with pytest.raises(EngineRefusal, match="lacks mols.tar; Boltz would download"):
        engine.prepare(task, tmp_path, PROBE)
    (cache / "mols.tar").write_bytes(b"x")
    prepared = engine.prepare(task, tmp_path, PROBE)
    digest = hashlib.sha256(b"not really weights").hexdigest()
    assert prepared.weights == {"boltz2_conf.ckpt": digest}
    argv = list(prepared.spec.argv)
    assert argv[:6] == ["/env/bin/python", "-I", "-m", "boltz.main", "predict",
                        str(tmp_path / "mini.yaml")]
    for flag, value in (("--out_dir", "{output}"), ("--cache", str(cache.resolve())),
                        ("--model", "boltz2"), ("--seed", "42"), ("--output_format", "mmcif"),
                        ("--accelerator", "gpu")):
        assert argv[argv.index(flag) + 1] == value
    assert json.loads((tmp_path / "mini.yaml").read_text()) == engine.render(task)
    assert any("single-sequence mode" in w for w in prepared.warnings)
    pinned = mini(model=ModelSpec("boltz-2", weights_sha256="0" * 64,
                                  options={"cache": str(cache)}))
    with pytest.raises(EngineRefusal, match="hashes to"):
        engine.prepare(pinned, tmp_path, PROBE)


#: SHA-256 of the model the real ubiquitin-aspirin run wrote (docs/compute-tasks.md).
UBQ_ASPIRIN_MODEL = "d6768a18df4db118ce4dfb405767043cf251a6c9373947cfc678eac7d06b3480"


def test_boltz_reads_a_real_run(tmp_path):
    """boltz 2.2.1 on a CPU: ubiquitin and aspirin, no MSA, one recycle, 50 steps."""
    cache = {"cache": str(boltz_cache(tmp_path))}
    task = ubq_aspirin(model=ModelSpec("boltz-2", options=cache))
    engine = BoltzEngine()
    prepared = engine.prepare(task, tmp_path, PROBE)
    files = boltz_files("boltz_ubq_aspirin", tmp_path)
    for name, path in files.items():
        check = prepared.validators.get(name)
        assert check is None or check(path) is None, name
    found = arts(**files)
    assert found["model"].sha256 == UBQ_ASPIRIN_MODEL
    result = engine.read(task, found, engine._unrun(PROBE, "fixture"), prepared)
    assert result.status is ExecutionStatus.SUCCEEDED
    # pLDDT x 100 in B_iso_or_equiv: one value per residue for the protein, per atom for
    # the ligand (aspirin's 13 heavy atoms, residue LIG1)
    assert {c: (v.units, v.plddt) for c, v in result.chains.items()} == {
        "A": (76, 93.43), "L": (13, 32.1)}
    # ... which is what Boltz's own token mean (complex_plddt, 0-1) says
    assert result.model_metrics["complex_plddt"] * 100 == pytest.approx(
        (76 * 93.43 + 13 * 32.1) / 89, abs=0.01)
    assert (round(result.ptm, 4), round(result.iptm, 4)) == (0.8792, 0.5324)
    assert round(result.chains["A"].ptm, 4) == 0.9388
    assert round(result.pair_iptm["L"]["A"], 4) == 0.5324
    assert round(result.pair_iptm["A"]["L"], 4) == 0.1826, "Boltz's pair ipTM is asymmetric"
    metrics = result.model_metrics
    assert metrics["protein_iptm"] == 0.0, "one protein chain has no protein interface"
    assert metrics["mean_pae"] == 4.547 and round(metrics["confidence_score"], 4) == 0.7823
    record = json.loads(json.dumps(result.to_dict()))
    assert record["chains"]["A"]["plddt"] == 93.43 and record["structure"]["sha256"]


def test_boltz_numbers_chains_by_entity_and_its_record_says_how(tmp_path):
    """Chains A=X, B=Y, C=X: boltz 2.2.1 ran them as A, C, B and indexed its scores so."""
    x, y = UBQ[:20], "GSHMKELLKKAEELLKRLG"
    task = ComplexPredictionTask(
        "reorder", chains=(Chain("A", x), Chain("B", y), Chain("C", x)),
        model=ModelSpec("boltz-2", options={"cache": str(boltz_cache(tmp_path))}),
        recycling_steps=1, sampling_steps=50)
    engine = BoltzEngine()
    prepared = engine.prepare(task, tmp_path, PROBE)
    files = boltz_files("boltz_reorder", tmp_path)
    assert all(prepared.validators[n](p) is None for n, p in files.items())
    record = json.loads(files["record"].read_text())
    assert [c["chain_name"] for c in record["chains"]] == ["A", "C", "B"]
    conf = json.loads(files["confidence"].read_text())
    result = engine.read(task, arts(**files), engine._unrun(PROBE, "fixture"), prepared)
    assert result.status is ExecutionStatus.SUCCEEDED
    # Index 1 is C and 2 is B; by input order they would have been swapped, silently.
    assert result.chains["C"].ptm == conf["chains_ptm"]["1"]
    assert result.chains["B"].ptm == conf["chains_ptm"]["2"]
    assert result.pair_iptm["B"]["C"] == conf["pair_chains_iptm"]["2"]["1"]
    assert {c: v.units for c, v in result.chains.items()} == {"A": 20, "B": 19, "C": 20}
    partial = tmp_path / "partial.json"
    partial.write_text(json.dumps({**record, "chains": record["chains"][:2]}))
    assert ("Boltz's record numbers chains ['A', 'C']; the task asked for ['A', 'B', 'C']"
            in prepared.validators["record"](partial))
    twice = tmp_path / "twice.json"
    twice.write_text(json.dumps({**record, "chains": record["chains"] + record["chains"][:1]}))
    assert "lists a chain id or name twice" in prepared.validators["record"](twice)


def test_a_model_of_something_else_does_not_validate(tmp_path):
    model = boltz_files("boltz_ubq_aspirin", tmp_path)["model"]
    cache = {"cache": str(boltz_cache(tmp_path))}
    mutant = ubq_aspirin(chains=(Chain("A", UBQ[:4] + "W" + UBQ[5:]),),
                         model=ModelSpec("boltz-2", options=cache))
    check = BoltzEngine().prepare(mutant, tmp_path, PROBE).validators["model"]
    assert "residue 5 is VAL in the model and W in the task" in check(model)
    apo = ubq_aspirin(ligands=(), model=ModelSpec("boltz-2", options=cache))
    check = BoltzEngine().prepare(apo, tmp_path, PROBE).validators["model"]
    assert "the model holds chains ['A', 'L']; the task asked for ['A']" in check(model)


def test_boltz_end_to_end_through_the_job_protocol_with_a_stand_in(tmp_path):
    python = stand_in(tmp_path)
    cache = boltz_cache(tmp_path)
    engine = BoltzEngine(environments("boltz", python))
    task = ubq_aspirin(model=ModelSpec("boltz-2", version="2.2.1",
                                       options={"cache": str(cache)}))
    result = engine.run(task, tmp_path / "work", timeout_s=60, poll_s=0.05)
    assert result.status is ExecutionStatus.SUCCEEDED, result.reason
    assert result.chains["A"].plddt == 93.43 and round(result.iptm, 4) == 0.5324
    run = result.provenance
    assert run.ran and run.interpreter == str(python), "the stand-in is what ran"
    assert run.version == "2.2.1" and "reviewed environment" in run.environment
    assert run.job["executor"] == "local-subprocess"
    assert result.structure.sha256 == UBQ_ASPIRIN_MODEL
    trace = json.loads((tmp_path / "work" / "ubq_aspirin" / "trace.json").read_text())
    kinds = [e["event_type"] for e in trace["events"]]
    assert kinds[:2] == ["JobRequested", "JobSubmitted"] and "JobCollected" in kinds
    assert open_jobs(trace).jobs == (), "collected: nothing left to reconcile"

    pinned = ubq_aspirin(name="pinned", model=ModelSpec("boltz-2", version="2.1.0",
                                                        options={"cache": str(cache)}))
    refused = engine.run(pinned, tmp_path / "work")
    assert refused.status is ExecutionStatus.UNAVAILABLE
    assert "boltz 2.2.1 is installed; the task requires 2.1.0" in refused.reason
    assert not refused.provenance.ran

    other = ubq_aspirin(name="other", chains=(Chain("A", UBQ[:4] + "W" + UBQ[5:]),),
                        model=task.model)
    wrong = engine.run(other, tmp_path / "work", timeout_s=60, poll_s=0.05)
    assert wrong.status is ExecutionStatus.FAILED and wrong.provenance.ran
    assert "a model of another sequence" in wrong.reason
    assert wrong.structure is None and wrong.chains == {}, "no number from a bad model"


def test_a_job_left_running_is_collected_only_as_the_task_it_ran(tmp_path):
    # Chai-1 overran its budget here and was collected with resume after the adapter had
    # changed: rendered again, the task would have recorded a command that did not run.
    engine = BoltzEngine(environments("boltz", stand_in(tmp_path, mode="slow")))
    task = ubq_aspirin(model=ModelSpec("boltz-2",
                                       options={"cache": str(boltz_cache(tmp_path))}))
    left = engine.run(task, tmp_path / "work", timeout_s=0.2, poll_s=0.05)
    assert left.status is ExecutionStatus.TIMEOUT and left.provenance.ran
    ref = JobRef.from_dict(left.provenance.job)
    jobs = JobController(LocalSubprocessJobs(Path(ref.location).parent))
    assert jobs.wait(ref, timeout_s=60, poll_s=0.05).state.finished
    # Another ligand under the same name renders the same command line; the task's digest
    # bound into the spec tells the two apart.
    other = ubq_aspirin(ligands=(Ligand("L", smiles="CCO"),), model=task.model)
    refused = engine.resume(other, ref, tmp_path / "work", jobs)
    assert refused.status is ExecutionStatus.FAILED
    assert "submitted as another spec" in refused.reason and refused.chains == {}
    done = engine.resume(task, ref, tmp_path / "work", jobs)
    assert done.status is ExecutionStatus.SUCCEEDED, done.reason
    assert done.structure.sha256 == UBQ_ASPIRIN_MODEL
    assert done.provenance.command == left.provenance.command, "the command that ran"


def test_a_tool_that_fails_is_failed_with_its_own_words(tmp_path):
    engine = BoltzEngine(environments("boltz", stand_in(tmp_path, mode="crash")))
    task = mini(model=ModelSpec("boltz-2", options={"cache": str(boltz_cache(tmp_path))}))
    result = engine.run(task, tmp_path / "work", timeout_s=60, poll_s=0.05)
    assert result.status is ExecutionStatus.FAILED
    assert "CUDA out of memory" in result.reason and result.provenance.ran


# ------------------------------------------------------------------- Chai-1
def chai_downloads(tmp_path: Path) -> Path:
    root = tmp_path / "chai-downloads"
    (root / "models_v2").mkdir(parents=True)
    for name in ChaiEngine.COMPONENTS:
        (root / "models_v2" / name).write_bytes(name.encode())
    (root / "conformers_v1.apkl").write_bytes(b"conformers")
    return root


def test_chai_letters_chains_and_refuses_what_it_cannot_take(tmp_path):
    task = mini(chains=(Chain(("H", "K"), "MKTAY", modifications=(Modification(3, "TPO"),)),),
                ligands=(Ligand("Q", smiles="CCO"),),
                constraints=(PocketConstraint("Q", (Token("H", 2),)),
                             ContactConstraint(Token("H", 5), Token("K", 5), 9.0)),
                model=ModelSpec("chai-1"))
    assert ChaiEngine.mapping(task) == {"H": "A", "K": "B", "Q": "C"}
    assert ChaiEngine.render_fasta(task) == (">protein|name=H\nMK(TPO)AY\n"
                                             ">protein|name=K\nMK(TPO)AY\n"
                                             ">ligand|name=Q\nCCO\n")
    assert ChaiEngine.render_restraints(task, ChaiEngine.mapping(task)) == [
        ["pocket1_0", "C", "", "A", "K2", "pocket", 1.0, 0.0, 6.0, ""],
        ["contact2", "A", "Y5", "B", "Y5", "contact", 1.0, 0.0, 9.0, ""]]
    with pytest.raises(EngineRefusal, match="takes ligands as SMILES"):
        ChaiEngine.render_fasta(mini(ligands=(Ligand("L", ccd="HEM"),)))
    same = mini(constraints=(ContactConstraint(Token("A", 1), Token("A", 5)),))
    with pytest.raises(EngineRefusal, match="contacts join two chains"):
        ChaiEngine.render_restraints(same, ChaiEngine.mapping(same))
    bonded = mini(constraints=(BondConstraint(Token("A", 1, "N"), Token("L", 1, "C1")),))
    with pytest.raises(EngineRefusal, match="covalent bonds through its Python API"):
        ChaiEngine.render_restraints(bonded, ChaiEngine.mapping(bonded))
    with pytest.raises(EngineRefusal, match="weights at run time"):
        ChaiEngine().prepare(task, tmp_path, PROBE)


def test_chai_reads_a_real_run(tmp_path):
    """chai_lab 0.6.1 on a CPU: ubiquitin and aspirin, no MSA, no ESM embeddings, one
    trunk pass, 50 diffusion steps, one sample, seed 42 (46 minutes)."""
    downloads = chai_downloads(tmp_path)
    task = ubq_aspirin(recycling_steps=0, model=ModelSpec(
        "chai-1", options={"downloads": str(downloads), "device": "cpu"}))
    engine = ChaiEngine()
    prepared = engine.prepare(task, tmp_path, PROBE)
    argv = list(prepared.spec.argv)
    assert argv[argv.index("--num-trunk-recycles") + 1] == "1", "the one pass that ran"
    model = tmp_path / "pred.model_idx_0.cif"
    model.write_bytes(gzip.decompress(
        (COMPUTE / "chai_ubq_aspirin_model_0.cif.gz").read_bytes()))
    scores = COMPUTE / "chai_ubq_aspirin_scores_0.npz"
    assert prepared.validators["model_0"](model) is None
    assert prepared.validators["scores_0"](scores) is None
    found = arts(model_0=model, scores_0=scores)
    assert found["model_0"].sha256 == (
        "ef5bab2f05487e428585bdd1ab501b08e7a8e79860cd65698f8d8669512a5265")
    result = engine.read(task, found, engine._unrun(PROBE, "fixture"), prepared)
    assert result.status is ExecutionStatus.SUCCEEDED
    # Chai-1 letters the ligand B in its model; the result names it L, as the task does.
    assert ChaiEngine.mapping(task) == {"A": "A", "L": "B"}
    assert {c: (v.units, v.plddt) for c, v in result.chains.items()} == {
        "A": (76, 57.77), "L": (13, 35.79)}
    assert (round(result.ptm, 4), round(result.iptm, 4)) == (0.5063, 0.1027)
    assert round(result.chains["L"].ptm, 4) == 0.2545
    assert round(result.pair_iptm["L"]["A"], 4) == 0.1027
    assert round(result.pair_iptm["A"]["L"], 4) == 0.0049
    metrics = result.model_metrics
    assert (round(metrics["aggregate_score"], 4), metrics["chosen_sample"]) == (0.1834, 0)
    assert metrics["has_inter_chain_clashes"] is False


def test_chai_reports_its_best_sample_not_its_first(tmp_path):
    downloads = chai_downloads(tmp_path)
    task = mini(chains=(Chain(("H", "K"), "MKTAY"),), ligands=(Ligand("Q", smiles="CCO"),),
                samples=2, model=ModelSpec("chai-1", options={"downloads": str(downloads)}))
    engine = ChaiEngine()
    prepared = engine.prepare(task, tmp_path, PROBE)
    assert prepared.spec.env == {"CHAI_DOWNLOADS_DIR": str(downloads.resolve())}
    argv = list(prepared.spec.argv)
    assert argv[2:6] == ["-m", "chai_lab.main", "fold", str(tmp_path / "mini.fasta")]
    assert "--no-use-esm-embeddings" in argv and "--constraint-path" not in argv
    assert set(prepared.weights) == {*(f"models_v2/{c}" for c in ChaiEngine.COMPONENTS),
                                     "combined"}
    # chai_lab counts trunk passes, Boltz (and the task) recycles after the first: the
    # run printed "Trunk recycles: 0/1" for --num-trunk-recycles 1, and 0 would skip the
    # trunk. Three recycles are four passes in both.
    assert argv[argv.index("--num-trunk-recycles") + 1] == "4"
    once = engine.prepare(mini(chains=task.chains, ligands=task.ligands, recycling_steps=0,
                               model=task.model), tmp_path, PROBE)
    passes = list(once.spec.argv)
    assert passes[passes.index("--num-trunk-recycles") + 1] == "1"
    # Chai-1 letters the chains A, B, C: the ethanol is chain C in its model file.
    text = (COMPUTE / "complex_model.cif").read_text().replace(" L ", " C ")
    for i in (0, 1):
        (tmp_path / f"pred.model_idx_{i}.cif").write_text(text)
    n = 3
    for i, (aggregate, clashes) in enumerate(((0.41, False), (0.73, True))):
        np.savez(tmp_path / f"scores.model_idx_{i}.npz",
                 aggregate_score=np.array([aggregate]), ptm=np.array([0.6 + i / 10]),
                 iptm=np.array([0.5 + i / 10]),
                 per_chain_ptm=np.array([[0.8, 0.7, 0.3]]),
                 per_chain_pair_iptm=np.arange(n * n, dtype=float).reshape(1, n, n) / 10,
                 has_inter_chain_clashes=np.array([clashes]),
                 chain_chain_clashes=np.zeros((1, n, n), dtype=bool))
    paths = {f"model_{i}": tmp_path / f"pred.model_idx_{i}.cif" for i in (0, 1)}
    paths.update({f"scores_{i}": tmp_path / f"scores.model_idx_{i}.npz" for i in (0, 1)})
    assert all(prepared.validators[k](p) is None for k, p in paths.items())
    result = engine.read(task, arts(**paths), engine._unrun(PROBE, "fixture"), prepared)
    assert result.model_metrics["chosen_sample"] == 1
    assert result.structure.path == paths["model_1"]
    assert (result.ptm, result.iptm) == (0.7, 0.6)
    assert {c: v.ptm for c, v in result.chains.items()} == {"H": 0.8, "K": 0.7, "Q": 0.3}
    assert result.pair_iptm["H"]["Q"] == 0.2 and result.pair_iptm["Q"]["H"] == 0.6
    assert any("inter-chain clashes" in w for w in result.warnings)


# -------------------------------------------------------------- ProteinMPNN
def mpnn_checkout(tmp_path: Path) -> Path:
    root = tmp_path / "ProteinMPNN"
    (root / "vanilla_model_weights").mkdir(parents=True)
    (root / "protein_mpnn_run.py").write_text("# stand-in checkout\n")
    (root / "vanilla_model_weights" / "v_48_020.pt").write_bytes(b"mpnn weights")
    (root / ".git" / "refs" / "heads").mkdir(parents=True)
    (root / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    (root / ".git" / "refs" / "heads" / "main").write_text("8907e6671bfbfc92\n")
    return root


def test_proteinmpnn_runs_from_a_reviewed_checkout(tmp_path):
    root = mpnn_checkout(tmp_path)
    engine = ProteinMPNNEngine()
    found = engine._installed(EngineProbe(True, "/env/bin/python", "env", root=str(root)))
    assert found.available and found.version == "8907e6671bfbfc92"
    absent = engine._installed(EngineProbe(True, "/env/bin/python", "env"))
    assert not absent.available and "runs from its checkout" in absent.reason
    task = SequenceDesignTask("ubq", str(BACKBONE), "A", fixed_positions={"A": (1, 2, 3)},
                              num_sequences=2)
    prepared = engine.prepare(task, tmp_path, found)
    argv = list(prepared.spec.argv)
    assert argv[:5] == ["/env/bin/python", "-I", "-c", _RUN_MPNN, str(root)]
    for flag, value in (("--pdb_path", str(BACKBONE.resolve())), ("--pdb_path_chains", "A"),
                        ("--out_folder", "{output}"), ("--num_seq_per_target", "2"),
                        ("--sampling_temp", "0.1"), ("--seed", "37"),
                        ("--model_name", "v_48_020"),
                        ("--path_to_model_weights", str(root / "vanilla_model_weights"))):
        assert argv[argv.index(flag) + 1] == value
    fixed = json.loads((tmp_path / "fixed_positions.jsonl").read_text())
    assert fixed == {"1ubq": {"A": [1, 2, 3]}}, "keyed by the name ProteinMPNN gives it"
    assert prepared.spec.artefacts[0].path == "seqs/1ubq.fa"
    compile(_RUN_MPNN, "<run-mpnn>", "exec")


def test_proteinmpnn_designs_are_read_and_checked_against_the_request(tmp_path):
    root = mpnn_checkout(tmp_path)
    engine = ProteinMPNNEngine()
    probe = EngineProbe(True, "/env/bin/python", "env", "8907e667", root=str(root))
    # The real run: 1UBQ chain A, positions 1, 2, 3, 44 and 68 fixed, 8 designs in
    # batches of 2, T = 0.1, seed 37, v_48_020, on a CPU.
    fasta = COMPUTE / "mpnn_1ubq.fa"
    fixed = (1, 2, 3, 44, 68)

    def read(**kw):
        task = SequenceDesignTask("ubq", str(BACKBONE), "A", num_sequences=8, batch_size=2,
                                  **kw)
        prepared = engine.prepare(task, tmp_path, probe)
        assert prepared.validators["sequences"](fasta) is None
        return engine.read(task, arts(sequences=fasta), engine._unrun(probe, "fixture"),
                           prepared)

    result = read(fixed_positions={"A": fixed})
    assert result.status is ExecutionStatus.SUCCEEDED
    assert result.native == {"A": UBQ} and result.native_score == 1.332
    assert result.native_global_score == 1.3366
    assert [d.sample for d in result.designs] == list(range(1, 9))
    first = result.designs[0]
    assert (first.score, first.global_score, first.recovery) == (0.8667, 0.9023, 0.5915)
    assert first.temperature == 0.1
    assert all(d.sequences["A"][p - 1] == UBQ[p - 1] for d in result.designs for p in fixed)
    assert result.model_metrics["git_hash"] == "8907e6671bfbfc92303b5f79c4b5e6ce47cdef57"
    assert result.model_metrics["fixed_chains"] == []
    assert json.loads(json.dumps(result.to_dict()))["designs"][0]["sequences"]["A"]
    with pytest.raises(ValueError, match="ran with seed 37, not 38"):
        read(seed=38)
    # position 7 is T in ubiquitin and F in the first design
    with pytest.raises(ValueError, match="design 1 changed fixed position A7"):
        read(fixed_positions={"A": (7,)})
    six = SequenceDesignTask("ubq", str(BACKBONE), "A", num_sequences=6, batch_size=2)
    check = engine.prepare(six, tmp_path, probe).validators["sequences"]
    assert "9 records; expected the native and 6 designs" in check(fasta)


# -------------------------------------------------------------------- OpenMM
def test_openmm_runs_a_script_from_the_task_and_reads_its_records(tmp_path):
    task = DynamicsTask("md", str(BACKBONE), duration_ps=10.0, seed=11)
    engine = OpenMMEngine()
    prepared = engine.prepare(task, tmp_path, PROBE)
    assert list(prepared.spec.argv) == ["/env/bin/python", "-I",
                                        str(tmp_path / "openmm_run.py"),
                                        str(tmp_path / "openmm_config.json"), "{output}"]
    config = json.loads((tmp_path / "openmm_config.json").read_text())
    assert (config["steps"], config["report_steps"], config["seed"]) == (5000, 500, 11)
    assert config["force_field"] == ["amber14-all.xml", "amber14/tip3pfb.xml"]
    compile((tmp_path / "openmm_run.py").read_text(), "openmm_run.py", "exec")
    assert (tmp_path / "openmm_run.py").read_text() == _OPENMM_SCRIPT
    dcd = tmp_path / "trajectory.dcd"
    dcd.write_bytes(b"\x54\x00\x00\x00CORD" + bytes(80))
    files = dict(relaxed=BACKBONE, final=BACKBONE, run=COMPUTE / "openmm_run.json",
                 energies=COMPUTE / "openmm_energies.csv", trajectory=dcd)
    for name, path in files.items():
        assert prepared.validators[name](path) is None, name
    result = engine.read(task, arts(**files), engine._unrun(PROBE, "fixture"), prepared)
    assert result.status is ExecutionStatus.SUCCEEDED
    assert len(result.energies) == 10 and result.energies[-1].step == 5000
    assert result.energies[0].temperature_k == 299.14
    assert result.minimised_energy_kj_mol < result.initial_energy_kj_mol
    assert result.model_metrics["platform"] == "CPU"
    assert any("no pKa model" in w for w in result.warnings)
    assert len(json.loads(json.dumps(result.to_dict()))["energies"]) == 10


def test_openmm_outputs_that_are_not_what_was_asked_do_not_validate(tmp_path):
    prepared = OpenMMEngine().prepare(DynamicsTask("md", str(BACKBONE), duration_ps=20.0),
                                      tmp_path, PROBE)
    assert "10 energy rows; expected 20" in prepared.validators["energies"](
        COMPUTE / "openmm_energies.csv")
    blown = tmp_path / "run.json"
    record = json.loads((COMPUTE / "openmm_run.json").read_text())
    blown.write_text(json.dumps({**record, "minimised_energy_kj_mol": float("nan")}))
    assert "the system blew up" in prepared.validators["run"](blown)
    not_dcd = tmp_path / "t.dcd"
    not_dcd.write_bytes(b"plain text, not a trajectory")
    assert "not a DCD trajectory" in prepared.validators["trajectory"](not_dcd)
    relax = OpenMMEngine().prepare(DynamicsTask("relax", str(BACKBONE), duration_ps=0),
                                   tmp_path, PROBE)
    assert [a.name for a in relax.spec.artefacts] == ["relaxed", "run"]


def test_openmm_reports_no_volume_for_a_system_without_a_box(tmp_path):
    """OpenMM 8.6.1: 1UBQ without waters, vacuum, 1 ps, CPU platform with one thread."""
    dry = tmp_path / "1ubq_protein.pdb"
    dry.write_text("".join(ln for ln in BACKBONE.read_text().splitlines(keepends=True)
                           if ln.startswith(("ATOM", "TER", "END"))))
    task = DynamicsTask("vacuum", str(dry), force_field=("amber14-all.xml",),
                        solvent="vacuum", ionic_strength_molar=0.0, duration_ps=1.0,
                        report_interval_ps=0.5, seed=11, platform="CPU")
    engine = OpenMMEngine()
    prepared = engine.prepare(task, tmp_path, PROBE)
    dcd = tmp_path / "trajectory.dcd"
    dcd.write_bytes(b"\x54\x00\x00\x00CORD" + bytes(80))
    files = dict(relaxed=dry, final=dry, run=COMPUTE / "openmm_vacuum_run.json",
                 energies=COMPUTE / "openmm_vacuum_energies.csv", trajectory=dcd)
    assert all(prepared.validators[n](p) is None for n, p in files.items())
    # Before the fix every row said 8.0 nm^3: the volume of OpenMM's default 2 nm box.
    assert "Box Volume" not in files["energies"].read_text()
    result = engine.read(task, arts(**files), engine._unrun(PROBE, "fixture"), prepared)
    assert [(e.step, e.volume_nm3) for e in result.energies] == [(250, None), (500, None)]
    metrics = result.model_metrics
    assert (metrics["periodic"], metrics["precision"]) == (False, None)
    assert metrics["threads"] == "1", "OPENMM_CPU_THREADS reached the job"
    assert (metrics["n_atoms"], metrics["steps"]) == (1231, 500)
    assert result.minimised_energy_kj_mol < result.initial_energy_kj_mol
    assert not any("water molecules" in w for w in result.warnings)
