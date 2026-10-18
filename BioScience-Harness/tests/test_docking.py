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


# ------------------------------------------------------------ pose checks and the record

def test_pose_checks_catch_impossible_poses():
    from bioagent.docking import posecheck as PC
    receptor = np.array([[0.0, 0.0, 0.0], [5.0, 0.0, 0.0]])
    box = ((2.0, 2.0, 2.0), (10.0, 10.0, 10.0))
    clean = PC.pose_checks([[2.5, 3.0, 0.0], [3.0, 3.5, 0.0]], receptor, *box)
    assert clean["passed"] and clean["clashes"] == 0 and clean["outside_box"] == 0
    assert clean["min_receptor_distance"] == pytest.approx(3.905, abs=1e-3)
    clash = PC.pose_checks([[0.0, 1.5, 0.0]], receptor, *box)          # 1.5 Å from an atom
    assert not clash["passed"] and clash["failed"] == ["no_clash"] and clash["clashes"] == 1
    outside = PC.pose_checks([[2.0, 2.0, 7.6]], receptor, *box)        # 0.6 Å past the face
    assert outside["failed"] == ["in_box"] and outside["outside_box"] == 1
    assert PC.pose_checks([[2.0, 2.0, 7.4]], receptor, *box)["passed"]  # within tolerance
    broken = PC.pose_checks([[np.nan, 0.0, 0.0]], receptor, *box)
    assert not broken["passed"] and broken["failed"] == ["finite"]
    assert PC.pose_checks(np.zeros((0, 3)), receptor, *box)["failed"] == ["finite"]
    # a metal of a kept cofactor is left out: its coordinating atoms sit about 2 Å from it
    pdb = ("HETATM    1 ZN    ZN A 301       0.000   0.000   0.000  1.00  0.00          ZN\n"
           "ATOM      2  CA  ALA A   1       5.000   0.000   0.000  1.00  0.00           C\n")
    assert PC.receptor_heavy_atoms(pdb).tolist() == [[5.0, 0.0, 0.0]]


def _stand_in(shift: float = 0.0, fail: tuple[str, ...] = (), clash: tuple[str, ...] = ()):
    """Vina replaced by a stand-in: benzamidine (and the redocked ligand) is put at the
    crystal pose, moved by ``shift`` Å; another ligand at the box centre, or, if named in
    ``clash``, centred on the receptor heavy atom nearest the box centre (a pose that
    cannot be right). This exercises the pipeline's stages and checks, not docking."""
    from rdkit import Chem
    from bioagent.docking import engine, posecheck
    ref, _ = prep.cocrystal_ligand(POCKET.read_text(), "BEN", smiles=BENZAMIDINE)
    key = Chem.MolToSmiles(ref)

    def dock(receptor, ligand, box, *, scoring="vina", exhaustiveness=8, n_poses=9, seed=42):
        if ligand.name in fail:
            raise prep.DockingError(f"{ligand.name}: no pose")
        if Chem.MolToSmiles(Chem.RemoveHs(ligand.mol)) == key:
            mol = Chem.AddHs(ref, addCoords=True)
            target = mol.GetConformer().GetPositions() + np.array([shift, 0.0, 0.0])
        else:
            mol = Chem.Mol(ligand.mol)
            pos = mol.GetConformer().GetPositions()
            centre = np.array(box.center)
            if ligand.name in clash:
                atoms = posecheck.receptor_heavy_atoms(receptor.pdb)
                centre = atoms[np.argmin(np.linalg.norm(atoms - centre, axis=1))]
            target = pos - pos.mean(axis=0) + centre
        conf = mol.GetConformer()
        for i, p in enumerate(target):
            conf.SetAtomPosition(i, p.tolist())
        return engine.DockResult(ligand=ligand, poses=[engine.Pose(1, -6.0, mol)],
                                 scoring=scoring, exhaustiveness=exhaustiveness, seed=seed,
                                 seconds=0.0)
    return dock


def _modules():
    need_module("rdkit")
    need_module("meeko")


def test_the_stages_with_a_stand_in_for_vina(tmp_path, monkeypatch):
    _modules()
    from bioagent.docking import engine
    from bioagent.docking import pipeline as DP
    monkeypatch.setattr(engine, "dock", _stand_in())
    run = run_docking(str(POCKET), [("benzamidine", BENZAMIDINE), ("ethanol", "CCO"),
                                    ("broken", "C1CC(")],
                      DockConfig(site_ligand="BEN", site_ligand_smiles=BENZAMIDINE),
                      tmp_path / "run")
    manifest = json.loads((tmp_path / "run" / "run.json").read_text())
    stages = {s["name"]: s for s in manifest["stages"]}
    assert list(stages) == list(DP.STAGES)
    assert stages["redock_validation"]["check"] == "PASS"
    assert stages["pose_checks"]["check"] == "PASS"
    assert "not a validation of binding" in stages["pose_checks"]["reason"]
    assert run.complete and manifest["complete"]
    assert manifest["inputs"] == {str(POCKET): manifest["receptor"]["sha256"]}
    best = {r["ligand"]: r["pose_checks"]["best"] for r in manifest["results"]}
    assert best["benzamidine"]["min_receptor_distance"] > 2.2     # the crystal pose passes
    lines = (tmp_path / "run" / "limitations.md").read_text().splitlines()
    assert lines == manifest["limitations"]
    assert any("not a measured binding affinity" in x for x in lines)
    assert any("could not be prepared or docked" in x and "broken" in x for x in lines)
    header = (tmp_path / "run" / "scores.tsv").read_text().splitlines()[0].split("\t")
    assert header[-2:] == ["pose_check", "min_receptor_distance"]
    assert verify_run(tmp_path / "run") == (True, [])


def test_a_failed_redocking_makes_the_run_incomplete_and_the_cli_exit_1(tmp_path, monkeypatch,
                                                                       capsys):
    _modules()
    from bioagent import cli
    from bioagent.docking import engine
    monkeypatch.setattr(engine, "dock", _stand_in(shift=3.0))
    ligands = tmp_path / "l.csv"
    ligands.write_text(f"name,smiles\nbenzamidine,{BENZAMIDINE}\n")
    code = cli.main(["dock", str(POCKET), str(ligands), "--out", str(tmp_path / "run"),
                     "--site-ligand", "BEN", "--site-ligand-smiles", BENZAMIDINE])
    assert code == 1
    err = capsys.readouterr().err
    assert "incomplete: stage redock_validation: its check failed: top pose 3.00 Å" in err
    manifest = json.loads((tmp_path / "run" / "run.json").read_text())
    assert manifest["complete"] is False and manifest["validation"]["passed"] is False
    ok, problems = verify_run(tmp_path / "run")
    assert not ok and "run.json records the run as incomplete" in problems


def test_no_ligand_docked_is_an_incomplete_run(tmp_path, monkeypatch):
    _modules()
    from bioagent.docking import engine
    monkeypatch.setattr(engine, "dock", _stand_in(fail=("ethanol",)))
    run = run_docking(str(POCKET), [("broken", "C1CC("), ("ethanol", "CCO")],
                      DockConfig(box_center=(-1.9, 14.4, 16.7), box_size=(18, 18, 18)),
                      tmp_path / "run")
    stages = {s["name"]: s for s in run.stages}
    assert stages["dock"]["status"] == "failed" and "no ligand was docked" in \
        stages["dock"]["reason"]
    assert stages["redock_validation"]["status"] == "skipped"
    assert stages["pose_checks"]["status"] == "not_applicable"
    assert not run.complete
    ok, problems = verify_run(tmp_path / "run")
    assert not ok and "no ligand was docked" in problems


def test_the_skill_claims_only_poses_that_pass_from_a_complete_run(tmp_path, monkeypatch):
    """dock-ligands with the Vina stand-in: a complete run with passing poses gives one
    hypothesis per ligand; a best pose clashing with the receptor makes the run
    incomplete, and then no ligand gets a claim and the limitations say why."""
    _modules()
    from bioagent.docking import engine
    from bioagent.skills.molecular.docking import dock_ligands
    ligands = tmp_path / "l.csv"
    ligands.write_text(f"name,smiles\nbenzamidine,{BENZAMIDINE}\nethanol,CCO\n")
    site = {"site_ligand": "BEN", "site_ligand_smiles": BENZAMIDINE}
    monkeypatch.setattr(engine, "dock", _stand_in())
    art = dock_ligands(str(POCKET), str(ligands), out_dir=str(tmp_path / "clean"), **site)
    assert sorted(c.subject for c in art.claims) == ["benzamidine", "ethanol"]
    assert art.provenance["complete"] is True
    monkeypatch.setattr(engine, "dock", _stand_in(clash=("ethanol",)))
    art = dock_ligands(str(POCKET), str(ligands), out_dir=str(tmp_path / "clash"), **site)
    manifest = json.loads((tmp_path / "clash" / "run.json").read_text())
    best = {r["ligand"]: r["pose_checks"]["best"] for r in manifest["results"]}
    assert "no_clash" in best["ethanol"]["failed"] and best["benzamidine"]["passed"]
    assert manifest["complete"] is False and art.provenance["validated"] is True
    assert not art.claims
    text = " ".join(art.limitations)
    assert "the run is incomplete (stage pose_checks: its check failed" in text
    assert "the best pose of ethanol failed the pose checks (" in text
    assert "no claim is made for it" in text


def test_the_reference_manifest_names_this_fixture(run):
    from bioagent.runrecord import pipeline_references
    ref = next(r for r in pipeline_references()["references"] if r["id"] == "3ptb_pocket")
    import hashlib
    assert hashlib.sha256(POCKET.read_bytes()).hexdigest() == ref["sha256"]
    want = ref["expected"]["values"]
    assert run.validation["top_pose_rmsd"] < want["redock_top_pose_rmsd_below"]
    assert run.complete is want["complete"]
    assert all(r["pose_checks"]["best"]["passed"] for r in run.manifest["results"])
