"""Molecular docking: preparation, the redocking check, scores, contacts, the skill."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from bioagent.docking import prep
from bioagent.docking.engine import Box
from bioagent.docking.pipeline import DockConfig, run_docking, verify_run
from omics_world import need_module

pytestmark = pytest.mark.unit

FIX = Path(__file__).parent / "fixtures" / "docking"
POCKET = FIX / "3ptb_pocket.pdb"
BENZAMIDINE = "NC(=N)c1ccccc1"
CANDIDATES = Path(__file__).resolve().parents[1] / "skills" / "candidates" / "molecular"


def _tools():
    for m in ("rdkit", "meeko", "vina", "gemmi"):
        need_module(m)


def test_ligands_are_read_from_tables_and_lines(tmp_path):
    csv = tmp_path / "l.csv"
    csv.write_text("name,smiles\nbza,NC(=N)c1ccccc1\nmeth,Cc1ccc(cc1)C(N)=N\n")
    assert prep.read_ligands(csv) == [("bza", BENZAMIDINE), ("meth", "Cc1ccc(cc1)C(N)=N")]
    smi = tmp_path / "l.smi"
    smi.write_text("CCO ethanol\nc1ccccc1\n")
    assert prep.read_ligands(smi) == [("ethanol", "CCO"), ("ligand2", "c1ccccc1")]
    assert prep.read_ligands("CCO") == [("ligand1", "CCO")]
    dup = tmp_path / "d.csv"
    dup.write_text("name,smiles\na,CCO\na,CCN\n")
    with pytest.raises(prep.DockingError, match="share a name"):
        prep.read_ligands(dup)


def test_a_box_around_coordinates_has_padding_and_a_minimum():
    box = Box.around(np.array([[0.0, 0.0, 0.0], [4.0, 2.0, 30.0]]))
    assert box.center == (2.0, 1.0, 15.0)
    assert box.size == (18.0, 18.0, 38.0)                  # at least 18 Å; 30 + 8 padding


def test_a_remote_receptor_must_be_asked_for(tmp_path):
    with pytest.raises(prep.DockingError, match="allow_remote"):
        run_docking("PDB:3PTB", [("x", "CCO")], DockConfig(site_ligand="BEN"), tmp_path)


def test_the_receptor_is_cleaned_to_protein_atoms():
    clean, chains, residues, cofactors = prep._clean(POCKET.read_text(), None, ())
    assert "HOH" not in clean and " BEN " not in clean
    assert chains == ["A"] and residues == 81 and cofactors == []
    kept, _, _, cof = prep._clean(POCKET.read_text(), None, ("BEN",))
    assert cof == ["BEN"] and " BEN " in kept


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    _tools()
    out = tmp_path_factory.mktemp("dock")
    return run_docking(str(POCKET), [("benzamidine", BENZAMIDINE),
                                     ("4-methylbenzamidine", "Cc1ccc(cc1)C(N)=N"),
                                     ("octane", "CCCCCCCC"), ("broken", "C1CC(")],
                       DockConfig(site_ligand="BEN", site_ligand_smiles=BENZAMIDINE),
                       out / "run")


def test_redocking_the_crystal_ligand_validates_the_setup(run):
    v = run.validation
    assert v["passed"] and v["top_pose_rmsd"] < 2.0 and run.validated
    assert v["top_score"] < -5.0
    assert run.box.source == "co-crystal ligand BEN"


def test_the_amidine_sits_against_asp189(run):
    best = {r.ligand.name: r.best for r in run.results}
    assert best["benzamidine"].score < best["octane"].score       # the cation beats the alkane
    residues = {c.residue for c in run.contacts["benzamidine"] if c.kind == "hbond"}
    assert "ASP189:A" in residues


def test_an_unreadable_ligand_is_a_failure_not_a_crash(run):
    assert "broken" in run.failures and "broken" not in {r.ligand.name for r in run.results}
    assert any("could not be docked" in w for w in run.warnings)


def test_the_run_writes_poses_a_report_and_verifies(run):
    out = run.out_dir
    table = (out / "scores.tsv").read_text().splitlines()
    assert table[0].startswith("ligand\tsmiles\tbest_score")
    assert len(table) == 4                                        # three docked ligands
    from rdkit import Chem
    poses = [m for m in Chem.SDMolSupplier(str(out / "poses" / "benzamidine.sdf"))]
    assert poses and all(m.HasProp("vina_score") for m in poses)
    md = (out / "report.md").read_text()
    assert "**Validated**" in md and "not a measured affinity" in md
    manifest = json.loads((out / "run.json").read_text())
    assert manifest["validation"]["passed"] and manifest["versions"]["vina"] != "unknown"
    ok, problems = verify_run(out)
    assert ok, problems


def test_an_unvalidated_site_is_said_to_be(tmp_path):
    _tools()
    run = run_docking(str(POCKET), [("benzamidine", BENZAMIDINE)],
                      DockConfig(site_residues=("A:189", "A:190", "A:195", "A:213"),
                                 exhaustiveness=4), tmp_path)
    assert run.validation is None and not run.validated
    assert any("not validated" in w for w in run.warnings)
    assert "**Not validated**" in (tmp_path / "report.md").read_text()


def test_the_skill_claims_a_hypothesis_only_from_a_validated_setup(tmp_path):
    _tools()
    from bioagent.contracts import check_claim
    from bioagent.governed import GovernedRunRefused, run_governed

    ligands = tmp_path / "l.csv"
    ligands.write_text(f"name,smiles\nbenzamidine,{BENZAMIDINE}\n")
    args = {"receptor": str(POCKET), "ligands": str(ligands), "site_ligand": "BEN",
            "site_ligand_smiles": BENZAMIDINE, "exhaustiveness": 4,
            "out_dir": str(tmp_path / "run")}
    with pytest.raises(GovernedRunRefused, match="no lockfile pins skill"):
        run_governed("dock-ligands", args, skill_dir=CANDIDATES, state_dir=tmp_path / "p0")
    run = run_governed("dock-ligands", args, skill_dir=CANDIDATES, state_dir=tmp_path / "psh",
                       output_dir=tmp_path / "out", allow_unpinned=True)
    art = run.artifact
    (claim,) = art.claims
    assert claim.claim_kind == "mechanism_hypothesis" and claim.subject == "benzamidine"
    assert check_claim(claim, {e.id: e for e in art.evidence}).allowed
    assert all(e.design == "docking" and e.has_quote_receipt for e in art.evidence)
    assert run.verdict.publishable, run.verdict.codes
    assert not run.released
