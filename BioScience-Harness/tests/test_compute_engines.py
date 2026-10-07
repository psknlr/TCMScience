"""Adapters for Boltz, Chai-1, ProteinMPNN and OpenMM, none of which is installed here.

What is tested: that each adapter refuses with its reason when its tool is absent, and its
result then says no model ran; how each renders a task in its tool's input format; and how
each reads its tool's output format. The outputs are hand-made files in
``tests/fixtures/compute`` that follow each tool's documentation and source as read on
2026-10-07 (``complex_model.cif``: a python-ihm-style mmCIF of two MKTAY chains and an
ethanol, pLDDT x 100 as B-factors; ``boltz_confidence.json``; ``mpnn_1ubq.fa``: random
substitutions in ProteinMPNN's FASTA layout, not designs; ``openmm_*``: StateDataReporter
and run records). No file there was produced by the tool it imitates.

One test drives ``BoltzEngine.run`` end to end through the reviewed environment, the job
protocol and validation, against a *stand-in interpreter* that answers the probe and
writes those fixture files. Its assertions about provenance name the stand-in: what it
proves is the plumbing, not Boltz.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pytest

from bioagent.backends.environments import ExecutionEnvironments, reviewed
from bioagent.backends.jobs import Artefact, open_jobs
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
PROBE = EngineProbe(True, "/env/bin/python", "a reviewed environment", "2.2.0")


def mini(**kw) -> ComplexPredictionTask:
    """The complex the fixture model holds: MKTAY twice, and ethanol."""
    base = dict(name="mini", chains=(Chain(("A", "B"), "MKTAY"),),
                ligands=(Ligand("L", smiles="CCO"),))
    base.update(kw)
    return ComplexPredictionTask(**base)


def boltz_cache(tmp_path: Path) -> Path:
    cache = tmp_path / "boltz-cache"
    (cache / "mols").mkdir(parents=True)
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
             version: str = "2.2.0") -> Path:
    """An executable that answers like a Python with Boltz installed, and is not one."""
    script = tmp_path / "stand_in.py"
    script.write_text(f'''import json, shutil, sys
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
    stem = Path(args[3]).stem
    out = Path(args[args.index("--out_dir") + 1]) / f"boltz_results_{{stem}}"
    pred = out / "predictions" / stem
    pred.mkdir(parents=True)
    shutil.copy(FIX / "complex_model.cif", pred / f"{{stem}}_model_0.cif")
    shutil.copy(FIX / "boltz_confidence.json", pred / f"confidence_{{stem}}_model_0.json")
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


def test_boltz_reads_its_documented_outputs(tmp_path):
    task = mini(model=ModelSpec("boltz-2", options={"cache": str(boltz_cache(tmp_path))}))
    engine = BoltzEngine()
    prepared = engine.prepare(task, tmp_path, PROBE)
    model, confidence = COMPUTE / "complex_model.cif", COMPUTE / "boltz_confidence.json"
    assert prepared.validators["model"](model) is None
    assert prepared.validators["confidence"](confidence) is None
    result = engine.read(task, arts(model=model, confidence=confidence),
                         engine._unrun(PROBE, "fixture"), prepared)
    assert result.status is ExecutionStatus.SUCCEEDED
    assert {c: v.plddt for c, v in result.chains.items()} == {"A": 80.0, "B": 64.0,
                                                               "L": 52.0}
    assert (result.chains["A"].units, result.chains["L"].units) == (5, 3)
    assert (result.ptm, result.iptm) == (0.7034, 0.6215)
    assert result.chains["L"].ptm == 0.3514
    assert result.pair_iptm["A"]["B"] == 0.6611 and result.pair_iptm["B"]["A"] == 0.6502
    assert result.model_metrics["confidence_score"] == 0.7512
    assert "complex_pde" in result.model_metrics, "model-specific numbers keep their names"
    record = json.loads(json.dumps(result.to_dict()))
    assert record["chains"]["A"]["plddt"] == 80.0 and record["structure"]["sha256"]


def test_a_model_of_something_else_does_not_validate(tmp_path):
    model = COMPUTE / "complex_model.cif"
    other = mini(chains=(Chain(("A", "B"), "MKTAW"),),
                 model=ModelSpec("boltz-2", options={"cache": str(boltz_cache(tmp_path))}))
    check = BoltzEngine().prepare(other, tmp_path, PROBE).validators["model"]
    assert "residue 5 is TYR in the model and W in the task" in check(model)
    fewer = mini(ligands=(), model=other.model)
    check = BoltzEngine().prepare(fewer, tmp_path, PROBE).validators["model"]
    assert "the model holds chains ['A', 'B', 'L']; the task asked for ['A', 'B']" in check(
        model)


def test_boltz_end_to_end_through_the_job_protocol_with_a_stand_in(tmp_path):
    python = stand_in(tmp_path)
    cache = boltz_cache(tmp_path)
    engine = BoltzEngine(environments("boltz", python))
    task = mini(model=ModelSpec("boltz-2", version="2.2.0", options={"cache": str(cache)}))
    result = engine.run(task, tmp_path / "work", timeout_s=60, poll_s=0.05)
    assert result.status is ExecutionStatus.SUCCEEDED, result.reason
    assert result.chains["A"].plddt == 80.0 and result.iptm == 0.6215
    run = result.provenance
    assert run.ran and run.interpreter == str(python), "the stand-in is what ran"
    assert run.version == "2.2.0" and "reviewed environment" in run.environment
    assert run.job["executor"] == "local-subprocess"
    assert result.structure.sha256 == hashlib.sha256(
        (COMPUTE / "complex_model.cif").read_bytes()).hexdigest()
    trace = json.loads((tmp_path / "work" / "mini" / "trace.json").read_text())
    kinds = [e["event_type"] for e in trace["events"]]
    assert kinds[:2] == ["JobRequested", "JobSubmitted"] and "JobCollected" in kinds
    assert open_jobs(trace).jobs == (), "collected: nothing left to reconcile"

    pinned = mini(name="pinned", model=ModelSpec("boltz-2", version="2.1.0",
                                                 options={"cache": str(cache)}))
    refused = engine.run(pinned, tmp_path / "work")
    assert refused.status is ExecutionStatus.UNAVAILABLE
    assert "boltz 2.2.0 is installed; the task requires 2.1.0" in refused.reason
    assert not refused.provenance.ran

    other = mini(name="other", chains=(Chain(("A", "B"), "MKTAW"),), model=task.model)
    wrong = engine.run(other, tmp_path / "work", timeout_s=60, poll_s=0.05)
    assert wrong.status is ExecutionStatus.FAILED and wrong.provenance.ran
    assert "a model of another sequence" in wrong.reason
    assert wrong.structure is None and wrong.chains == {}, "no number from a bad model"


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
    fasta = COMPUTE / "mpnn_1ubq.fa"

    def read(**kw):
        task = SequenceDesignTask("ubq", str(BACKBONE), "A", num_sequences=2, **kw)
        prepared = engine.prepare(task, tmp_path, probe)
        assert prepared.validators["sequences"](fasta) is None
        return engine.read(task, arts(sequences=fasta), engine._unrun(probe, "fixture"),
                           prepared)

    result = read(fixed_positions={"A": (1, 2, 3)})
    assert result.status is ExecutionStatus.SUCCEEDED
    assert result.native == {"A": UBQ[:76]} and result.native_score == 1.4351
    assert [d.sample for d in result.designs] == [1, 2]
    assert result.designs[0].recovery == 0.5068 and result.designs[0].temperature == 0.1
    assert result.designs[1].sequences["A"].startswith("MQIF")
    assert result.model_metrics["git_hash"].startswith("8907e667")
    assert json.loads(json.dumps(result.to_dict()))["designs"][0]["sequences"]["A"]
    with pytest.raises(ValueError, match="ran with seed 37, not 38"):
        read(seed=38)
    with pytest.raises(ValueError, match="changed fixed position A5"):
        read(fixed_positions={"A": (5,)})
    three = SequenceDesignTask("ubq", str(BACKBONE), "A", num_sequences=3)
    check = engine.prepare(three, tmp_path, probe).validators["sequences"]
    assert "expected the native and 3 designs" in check(fasta)


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
