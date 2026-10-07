"""Compute task contracts: complex prediction, sequence design, molecular dynamics.

What a task must say before anything runs, and the reason it is refused with when it
says it wrongly. No model runs in these tests.
"""

from __future__ import annotations

import builtins
from pathlib import Path

import pytest

from bioagent.structure.complex import (BondConstraint, Chain, ComplexPredictionTask,
                                        ContactConstraint, Ligand, Modification,
                                        PocketConstraint, Token)
from bioagent.structure.design import SequenceDesignTask, backbone_chains
from bioagent.structure.dynamics import DynamicsTask
from bioagent.structure.mmcif import parse_mmcif, read_mmcif
from bioagent.structure.tasks import ModelSpec, TaskInvalid, smiles_problem
from omics_world import need_module

FIX = Path(__file__).parent / "fixtures"
BACKBONE = FIX / "structure" / "1ubq.pdb"
UBQ = "MQIFVKTLTGKTITLEVEPSDTIENVKAKIQDKEGIPPDQQRLIFAGKQLEDGRTLSDYNIQKESTLHLVLRLRGG"


def complex_task(**kw) -> ComplexPredictionTask:
    base = dict(name="ubq_dimer", chains=(Chain(("A", "B"), UBQ),),
                ligands=(Ligand("L", smiles="c1ccccc1O"),),
                constraints=(PocketConstraint("L", (Token("A", 8), Token("A", 44))),))
    base.update(kw)
    return ComplexPredictionTask(**base)


# ------------------------------------------------------------- complex prediction
def test_a_complex_task_holds_copies_ligands_and_constraints():
    task = complex_task()
    assert task.validate() == []
    assert task.chains[0].stoichiometry == 2
    assert task.chain_kinds() == {"A": "protein", "B": "protein", "L": "ligand"}
    assert task.digest() == complex_task().digest()
    assert task.digest() != complex_task(seed=7).digest(), "the seed is part of the task"
    assert Chain("A", "mq if").ids == ("A",) and Chain("A", "mq if").sequence == "MQIF"


def test_chain_ids_are_unique_and_every_entity_has_a_copy():
    task = complex_task(chains=(Chain(("A", "B"), UBQ), Chain(("B",), "MKV"),
                                Chain((), "MK"), Chain(("C", "C"), "MK")),
                        ligands=(Ligand("A", ccd="HEM"),), constraints=())
    problems = task.validate()
    joined = "\n".join(problems)
    assert "'B' is used by entity 1 and entity 2" in joined
    assert "'C' is used twice in entity 4" in joined
    assert "'A' is used by entity 1 and entity 5" in joined
    assert "entity 3 has no chain ids: stoichiometry must be at least 1" in joined
    with pytest.raises(TaskInvalid) as refused:
        task.require_valid()
    assert refused.value.problems == tuple(problems), "every reason, not the first"


def test_residues_modifications_and_constraint_positions_are_checked():
    task = complex_task(
        chains=(Chain("A", UBQ + "B", modifications=(Modification(99, "SEP"),
                                                     Modification(5, "sep"))),
                Chain("C", "ACGU", kind="dna")),
        constraints=(PocketConstraint("L", (Token("A", 200),)),
                     ContactConstraint(Token("A", 3), Token("Z", 1)),
                     BondConstraint(Token("A", 3), Token("L", 1, "C1")),
                     PocketConstraint("L", (Token("L", 1),), max_distance=-2)))
    joined = "\n".join(task.validate())
    assert "B are not protein residues" in joined
    assert "modification position 99 is outside 1..77" in joined
    assert "'sep' is not a CCD code" in joined
    assert "U are not dna residues" in joined
    assert "residue 200 is outside chain A (1..77)" in joined
    assert "names chain 'Z', which the task does not have" in joined
    assert "a bond joins two named atoms" in joined
    assert "contacts lie on other chains than its binder" in joined
    assert "max_distance must be a positive number" in joined


def test_a_ligand_is_a_smiles_or_a_ccd_code_never_both_nor_neither():
    task = complex_task(ligands=(Ligand("L", smiles="CCO", ccd="EOH"), Ligand("M"),
                                 Ligand("N", ccd="toolong")), constraints=())
    joined = "\n".join(task.validate())
    assert joined.count("give exactly one of a SMILES and a CCD code") == 2
    assert "'toolong' is not a CCD code" in joined


def test_an_unparseable_smiles_is_refused_when_rdkit_is_present():
    need_module("rdkit")
    task = complex_task(ligands=(Ligand("L", smiles="c1ccccc1("),), constraints=())
    assert any("RDKit cannot parse the SMILES" in p for p in task.validate())
    assert complex_task().smiles_checked() is True


def test_without_rdkit_a_smiles_is_recorded_as_unchecked_not_as_valid(monkeypatch):
    real = builtins.__import__

    def no_rdkit(name, *args, **kwargs):
        if name == "rdkit" or name.startswith("rdkit."):
            raise ImportError(name)
        return real(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_rdkit)
    assert smiles_problem("c1ccccc1(") == (None, False)
    assert smiles_problem("C C") == ("'C C' contains whitespace; a SMILES is one token", True)
    assert complex_task().smiles_checked() is False


def test_the_msa_server_sends_sequences_away_and_must_be_allowed():
    assert any("allow_remote" in p for p in complex_task(use_msa_server=True).validate())
    assert complex_task(use_msa_server=True, allow_remote=True).validate() == []
    missing = complex_task(chains=(Chain("A", UBQ, msa="/no/such/msa.a3m"),))
    assert any("the MSA /no/such/msa.a3m does not exist" in p for p in missing.validate())


def test_a_pinned_weights_digest_must_be_a_digest():
    task = complex_task(model=ModelSpec("boltz-2", weights_sha256="abc123"))
    assert any("is not a SHA-256 digest" in p for p in task.validate())
    assert complex_task(model=ModelSpec("boltz-2", weights_sha256="a" * 64)).validate() == []


# ---------------------------------------------------------------- sequence design
def test_backbone_chains_follow_proteinmpnns_reading(tmp_path):
    assert backbone_chains(BACKBONE) == {"A": UBQ[:76]}
    lines = BACKBONE.read_text().splitlines()
    edited = []
    for line in lines:
        if line.startswith("ATOM") and int(line[22:26]) == 10:
            continue                                     # residue 10 missing: a gap
        edited.append(line)
        if line.startswith("ATOM") and int(line[22:26]) == 30:
            edited.append(line[:26] + "A" + line[27:])   # 30A inserted after 30
    path = tmp_path / "gapped.pdb"
    path.write_text("\n".join(edited) + "\n")
    chain = backbone_chains(path)["A"]
    assert len(chain) == 77 and chain[9] == "-", "a missing number is a position"
    assert chain[29:31] == UBQ[29] * 2, "an insertion code adds a position after its number"


def test_fixed_positions_count_from_the_chains_first_residue(tmp_path):
    renumbered = tmp_path / "ubq101.pdb"
    renumbered.write_text("\n".join(
        (ln[:22] + f"{int(ln[22:26]) + 100:4d}" + ln[26:]) if ln.startswith("ATOM") else ln
        for ln in BACKBONE.read_text().splitlines()) + "\n")
    assert SequenceDesignTask("d", str(renumbered), "A",
                              fixed_positions={"A": (1, 76)}).validate() == []
    by_number = SequenceDesignTask("d", str(renumbered), "A", fixed_positions={"A": (101,)})
    assert any("outside 1..76 (positions count from the chain's first residue" in p
               for p in by_number.validate())


def test_design_refuses_what_proteinmpnn_would_silently_do_differently():
    task = SequenceDesignTask("d", str(BACKBONE), ("A", "B", "A"),
                              fixed_positions={"C": (1,)}, seed=0, num_sequences=8,
                              batch_size=3, temperature=0)
    joined = "\n".join(task.validate())
    assert "chain 'B' is not in the backbone" in joined
    assert "a chain is named twice" in joined
    assert "fixed positions on chain 'C', which is not designed" in joined
    assert "seed must be at least 1" in joined
    assert "ProteinMPNN would make 6" in joined
    assert "temperature must be in (0, 1]" in joined
    every = SequenceDesignTask("d", str(BACKBONE), "A",
                               fixed_positions={"A": tuple(range(1, 77))})
    assert any("every position is fixed" in p for p in every.validate())
    assert SequenceDesignTask("d", "/no/such.pdb", "A").validate() == [
        "the backbone /no/such.pdb does not exist"]


# ------------------------------------------------------------- molecular dynamics
def test_dynamics_refuses_runs_that_would_not_repeat_or_not_be_what_they_say():
    task = DynamicsTask("md", str(BACKBONE), force_field=("amber14-all.xml",), seed=0,
                        timestep_fs=4.0)
    joined = "\n".join(task.validate())
    assert "explicit solvent needs a water model" in joined
    assert "seed must be at least 1" in joined
    assert "timestep_fs must be in (0, 2]" in joined
    vacuum = DynamicsTask("md", str(BACKBONE), solvent="vacuum")
    assert any("ions are added only with explicit solvent" in p for p in vacuum.validate())
    implicit = DynamicsTask("md", str(BACKBONE), solvent="implicit", ionic_strength_molar=0)
    assert any("implicit-solvent file" in p for p in implicit.validate())
    ragged = DynamicsTask("md", str(BACKBONE), duration_ps=10.001)
    assert any("not a whole number of" in p for p in ragged.validate())
    uneven = DynamicsTask("md", str(BACKBONE), duration_ps=10, report_interval_ps=3)
    assert any("divides the run" in p for p in uneven.validate())


def test_crystal_waters_without_a_water_model_are_refused_before_anything_runs(tmp_path):
    # OpenMM 8.6.1 ran this task and stopped in createSystem: "No template found for
    # residue 76 (HOH)". 1UBQ holds 58 crystallographic waters.
    implicit = DynamicsTask("md", str(BACKBONE), solvent="implicit", ionic_strength_molar=0,
                            force_field=("amber14-all.xml", "implicit/gbn2.xml"))
    assert any("holds 58 water molecules and no water model" in p
               for p in implicit.validate())
    vacuum = DynamicsTask("md", str(BACKBONE), solvent="vacuum", ionic_strength_molar=0,
                          force_field=("amber14-all.xml",))
    assert any("in a vacuum system; remove them" in p for p in vacuum.validate())
    dry = tmp_path / "1ubq_protein.pdb"
    dry.write_text("".join(ln for ln in BACKBONE.read_text().splitlines(keepends=True)
                           if ln.startswith(("ATOM", "TER", "END"))))
    assert DynamicsTask("md", str(dry), solvent="implicit", ionic_strength_molar=0,
                        force_field=implicit.force_field).validate() == []
    assert DynamicsTask("md", str(BACKBONE)).validate() == [], "explicit solvent keeps them"


def test_a_dynamics_task_counts_its_steps():
    task = DynamicsTask("md", str(BACKBONE), duration_ps=10.0).require_valid()
    assert (task.steps, task.report_steps) == (5000, 500)
    relax = DynamicsTask("relax", str(BACKBONE), duration_ps=0)
    assert relax.validate() == [] and relax.steps == 0
    assert task.to_dict()["structure_sha256"], "the structure's digest is part of the task"


# --------------------------------------------------------------------- mmCIF
def test_the_mmcif_reader_keeps_the_first_model_and_author_chains():
    model = read_mmcif(FIX / "compute" / "complex_model.cif")
    assert model.chains() == ["A", "B", "L"]
    assert model.sequence("A") == model.sequence("B") == "MKTAY"
    assert list(model.plddt("A")) == [90.0, 85.0, 80.0, 75.0, 70.0]
    text = (FIX / "compute" / "complex_model.cif").read_text()
    rows = [ln for ln in text.splitlines() if ln.startswith(("ATOM", "HETATM"))]
    second = [ln[:-1] + "2" for ln in rows]                 # the same atoms as model 2
    twice = text.replace(rows[-1] + "\n", rows[-1] + "\n" + "\n".join(second) + "\n")
    assert len(parse_mmcif(twice).atoms) == len(model.atoms), "model 2 is not read"
    with pytest.raises(ValueError, match="no _atom_site loop"):
        parse_mmcif("data_x\n_cell.length_a 10\n")


def test_the_mmcif_reader_reads_a_model_boltz_wrote():
    # boltz 2.2.1 through python-ihm: the ligand is HETATM LIG1 with no label_seq_id
    model = read_mmcif(FIX / "compute" / "boltz_ubq_aspirin_model_0.cif.gz")
    assert model.chains() == ["A", "L"] and model.sequence("A") == UBQ
    ligand = [a for a in model.atoms if a.chain == "L"]
    assert len(ligand) == 13 and {(a.resname, a.resseq) for a in ligand} == {("LIG1", 1)}
    assert all(a.hetero for a in ligand) and not any(a.hetero for a in model.atoms[:-13])
    plddt = [a.bfactor for a in model.atoms]
    assert 0 < min(plddt) and max(plddt) <= 100, "pLDDT x 100"
