"""Docking prepared inputs (bioagent.docking.prepared): reading PDBQT, the site, the engines,
the pipeline with a stand-in for Vina and with AutoDock Vina 1.2.7, the CLI's prepared mode."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

import bioagent
from bioagent.docking import prepared as P
from bioagent.docking.engine import Box
from bioagent.docking.prep import DockingError
from omics_world import need_module

pytestmark = pytest.mark.unit

FIX = Path(__file__).parent / "fixtures" / "docking" / "prepared"
RECEPTOR = FIX / "3ptb_receptor.pdbqt"
LIGANDS = FIX / "ligands.pdbqt"
CRYSTAL = FIX / "ben_crystal.pdbqt"
NAMES = ["benzamidine", "p_methylbenzamidine", "baicalein", "ibuprofen", "ferulic_acid",
         "paeonol"]
WASM = "ab" * 32


def atom(serial: int, x: float, y: float, z: float, ad: str = "C", name: str = "C",
         res: str = "UNL", chain: str = " ", resnum: int = 1, record: str = "ATOM") -> str:
    """One PDBQT atom line, in the columns Vina reads."""
    return (f"{record:<6s}{serial:5d} {name:<4s} {res:>3s} {chain:1s}{resnum:4d}    "
            f"{x:8.3f}{y:8.3f}{z:8.3f}  1.00  0.00    {0.0:+6.3f} {ad:<2s}")


# a small world: a receptor of five atoms around the origin, a few ligands near it
RECEPTOR_TEXT = "\n".join([
    "REMARK  a five-atom receptor",
    atom(1, 6.0, 0.0, 0.0, "C", "CA", "ALA", "A", 1),
    atom(2, 0.0, 6.0, 0.0, "N", "N", "ALA", "A", 1),
    atom(3, 0.0, 5.0, 0.0, "HD", "H", "ALA", "A", 1),
    atom(4, 0.0, 0.0, 6.0, "OA", "O", "SER", "B", 2),
    atom(5, 0.0, 0.0, -4.0, "Zn", "ZN", "ZN", "B", 3, record="HETATM"),
    "TER", "END", ""])
ALPHA = "\n".join(["REMARK  Name = alpha", "ROOT", atom(1, 0.0, 0.0, 0.0, "C"),
                   atom(2, 1.2, 0.0, 0.0, "OA", "O"), atom(3, 1.6, 0.8, 0.0, "HD", "H"),
                   "ENDROOT", "TORSDOF 0", ""])
BETA = "\n".join(["REMARK  Name = beta", "ROOT", atom(1, 0.0, 0.0, 0.0, "A"), "ENDROOT",
                  "BRANCH   1   2", atom(2, 0.0, 1.5, 0.0, "C"), atom(3, 0.0, 2.2, 1.0, "NA", "N"),
                  "ENDBRANCH   1   2", "TORSDOF 1", ""])
REF = "\n".join(["REMARK  Name = ref", "ROOT", atom(1, 0.5, 0.5, 0.5, "A"),
                 atom(2, 1.5, 0.5, 0.5, "A"), atom(3, 2.0, 1.0, 0.5, "N", "N"),
                 atom(4, 2.5, 1.0, 0.5, "HD", "H"), "ENDROOT", "TORSDOF 0", ""])


def models(*bodies: str) -> str:
    return "".join(f"MODEL {k}\n{b}ENDMDL\n" for k, b in enumerate(bodies, 1))


def write_world(tmp_path: Path, ligands: str | None = None) -> tuple[Path, Path, Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    rec, ligs, ref = tmp_path / "rec.pdbqt", tmp_path / "ligs.pdbqt", tmp_path / "ref.pdbqt"
    rec.write_text(RECEPTOR_TEXT)
    ligs.write_text(models(ALPHA, BETA) if ligands is None else ligands)
    ref.write_text(REF)
    return rec, ligs, ref


def _moved(line: str, d: tuple[float, float, float]) -> str:
    if line[:6] not in ("ATOM  ", "HETATM"):
        return line
    xyz = [float(line[30:38]) + d[0], float(line[38:46]) + d[1], float(line[46:54]) + d[2]]
    return line[:30] + "".join(f"{v:8.3f}" for v in xyz) + line[54:]


class FakeVina:
    """Vina replaced by a stand-in: each pose is the input conformer where it is (the
    reference moved by ``shift`` Å along x), energies a function of the call; ``refuse``
    names calls Vina refuses, ``clash`` calls put onto a receptor atom, ``bad_atoms`` calls
    whose poses lose an atom. This exercises the stages, not docking."""

    name, version, build, wasm_sha256 = "AutoDock Vina", "1.2.7", "wasm", WASM

    def __init__(self, shift: float = 0.0, refuse=(), clash=(), bad_atoms=(), poses: int = 3):
        self.shift, self.refuse, self.clash, self.bad_atoms = shift, refuse, clash, bad_atoms
        self.n = poses
        self.calls: list[tuple[str, dict]] = []

    def dock(self, receptor_text, ligand_text, box, *, scoring, exhaustiveness, n_poses, seed,
             energy_range, key):
        self.calls.append((key, {"scoring": scoring, "exhaustiveness": exhaustiveness,
                                 "n_poses": n_poses, "seed": seed, "energy_range": energy_range,
                                 "center": box.center, "size": box.size}))
        if key in self.refuse:
            raise P.VinaRefused("PDBQT parsing error: Atom type Q is not a valid AutoDock type")
        d = ((self.shift, 0.0, 0.0) if key == P.REFERENCE_KEY else
             (6.0, 0.0, 0.0) if key in self.clash else (0.0, 0.0, 0.0))
        lines = [_moved(ln, d) for ln in ligand_text.split("\n") if ln]
        if key in self.bad_atoms:
            lines = [ln for ln in lines if not ln.startswith("ATOM      1 ")]
        k = int(key.split("-")[1]) + 1 if key.startswith("ligand-") else 0
        energies = [[-5.0 - 0.1234567 * k + 0.5 * j, -6.1, -0.2, 0.3, -0.2] for j in range(self.n)]
        poses = "".join(f"MODEL {j + 1}\nREMARK VINA RESULT: {e[0]:9.3f}\n" + "\n".join(lines)
                        + "\nENDMDL\n" for j, e in enumerate(energies))
        return P.VinaResult(energies=energies, poses=poses, seconds=0.25)


def run_world(tmp_path: Path, engine=None, *, reference: bool = True, ligands: str | None = None,
              **config) -> P.PreparedDockRun:
    rec, ligs, ref = write_world(tmp_path, ligands)
    return P.run_prepared_docking(rec, ligs, P.PreparedDockConfig(**config), tmp_path / "run",
                                  engine=engine or FakeVina(),
                                  reference_path=ref if reference else None)


# ------------------------------------------------------------------ the module itself

def test_the_module_imports_with_the_standard_library_and_numpy_only():
    """It runs in the browser's Python: vina, RDKit, meeko, gemmi and scipy must not be
    needed to import it (vina is imported when a NativeVina is made)."""
    code = ("import sys\n"
            "HEAVY = ('vina', 'rdkit', 'meeko', 'gemmi', 'scipy', 'pandas', 'sklearn')\n"
            "for name in HEAVY:\n"
            "    sys.modules[name] = None\n"
            "import bioagent.docking.prepared as P\n"
            "loaded = {m.partition('.')[0] for m, v in sys.modules.items() if v is not None}\n"
            "assert not loaded & set(HEAVY), loaded & set(HEAVY)\n"
            "assert len(P.read_ligands(open(sys.argv[1]).read())) == 6\n")
    src = str(Path(bioagent.__file__).resolve().parents[1])
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(
        [src] + ([os.environ["PYTHONPATH"]] if os.environ.get("PYTHONPATH") else [])))
    done = subprocess.run([sys.executable, "-c", code, str(LIGANDS)], env=env,
                          capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr


def test_the_limitations_shared_with_the_full_pipeline_are_its_words():
    from bioagent.docking import pipeline
    assert P.LIMITATIONS[:len(pipeline.LIMITATIONS)] == pipeline.LIMITATIONS


def test_the_fixture_readme_names_each_file_by_its_digest():
    readme = (FIX / "README.md").read_text()
    for path in (RECEPTOR, LIGANDS, CRYSTAL):
        assert hashlib.sha256(path.read_bytes()).hexdigest() in readme, path.name


# ------------------------------------------------------------------ reading PDBQT

def test_the_fixture_receptor_is_read_as_vina_reads_it():
    text = RECEPTOR.read_text()
    r = P.read_receptor(text)
    assert (r.atoms, r.chains, r.residues) == (701, ["A"], 81)
    hydrogens = sum(1 for ln in text.splitlines() if ln[77:79].strip() in ("H", "HD"))
    assert r.heavy_atoms == r.atoms - hydrogens == len(r.heavy_xyz)
    assert r.text == text and r.sha256 == hashlib.sha256(RECEPTOR.read_bytes()).hexdigest()


def test_a_receptor_counts_metals_as_heavy_atoms_but_leaves_them_out_of_the_pose_checks():
    r = P.read_receptor(RECEPTOR_TEXT)
    assert (r.atoms, r.heavy_atoms, r.chains, r.residues) == (5, 4, ["A", "B"], 3)
    assert r.heavy_xyz.tolist() == [[6.0, 0.0, 0.0], [0.0, 6.0, 0.0], [0.0, 0.0, 6.0]]
    # sodium (Na) is a metal; NA is a nitrogen acceptor: types are case-sensitive
    na = P.read_receptor(atom(1, 1, 1, 1, "Na", "NA", "NA") + "\n" + atom(2, 2, 2, 2, "NA", "N"))
    assert na.heavy_atoms == 2 and na.heavy_xyz.tolist() == [[2.0, 2.0, 2.0]]
    assert P.AD_ELEMENT["Na"] == "Na" and P.AD_ELEMENT["NA"] == "N"


def test_line_ends_do_not_change_what_vina_is_given():
    crlf = RECEPTOR_TEXT.replace("\n", "\r\n")
    assert P.read_receptor(crlf).sha256 == P.read_receptor(RECEPTOR_TEXT).sha256
    (a,) = P.read_ligands(ALPHA.replace("\n", "\r\n"))
    assert a.text == ALPHA and a.sha256 == hashlib.sha256(ALPHA.encode()).hexdigest()


@pytest.mark.parametrize("text, why", [
    ("", "no atoms"),
    ("REMARK only\nTER\nEND\n", "no atoms"),
    ("MODEL 1\n" + atom(1, 0, 0, 0) + "\nENDMDL\n", "several models"),
    ("ROOT\n" + atom(1, 0, 0, 0) + "\nENDROOT\nTORSDOF 0\n", "torsion tree"),
    (atom(1, 0, 0, 0) + "\nBEGIN_RES ALA A 1\n", "torsion tree"),
    (atom(1, 0, 0, 0) + "\nCONECT    1    2\n", "does not read this record"),
    (atom(1, 0, 0, 0) + "\n   \n", "does not read this record"),
    (atom(1, 0, 0, 0, "CL"), "'CL' is not one AutoDock Vina 1.2.7 reads"),
    (atom(1, 0, 0, 0, "B"), "'B' is not one"),
    (atom(1, 0, 0, 0)[:70], "no AutoDock atom type"),
    (atom(1, 0, 0, 0).replace("   0.000   0.000", "     abc   0.000", 1), "not numbers"),
    (atom(1, 0, 0, 0).replace("    1 C ", "   x1 C ", 1), "atom number"),
])
def test_a_receptor_vina_would_refuse_is_refused_with_its_line(text, why):
    with pytest.raises(DockingError, match=why):
        P.read_receptor(text)


def test_the_fixture_ligands_are_read_one_per_model_named_by_their_remarks():
    ligands = P.read_ligands(LIGANDS.read_text(), "ligands")
    assert [lig.name for lig in ligands] == NAMES
    assert [lig.key for lig in ligands] == [f"ligand-{k}" for k in range(6)]
    first = ligands[0]
    assert (first.heavy_atoms, first.torsions, len(first.heavy_xyz)) == (9, 1, 9)
    for lig in ligands:
        assert lig.text.startswith(f"REMARK  Name = {lig.name}\n") and lig.text.endswith("\n")
        assert "MODEL" not in lig.text and "ENDMDL" not in lig.text
        assert lig.sha256 == hashlib.sha256(lig.text.encode()).hexdigest()
    assert LIGANDS.read_text() == models(*(lig.text for lig in ligands))
    (ben,) = P.read_ligands(CRYSTAL.read_text(), "ben_crystal")
    assert (ben.name, ben.heavy_atoms, ben.torsions) == ("BEN", 9, 1)


def test_ligand_names_default_unique_and_safe_as_file_names():
    plain = ALPHA.replace("REMARK  Name = alpha\n", "")
    named = lambda n: ALPHA.replace("alpha", n)                           # noqa: E731
    text = models(named("a b/c"), plain, plain, named("x"), named("x"), named(".."),
                  named("x_2"))
    assert [lig.name for lig in P.read_ligands(text, "set")] == \
        ["a_b_c", "set_2", "set_3", "x", "x_2", "set_6", "x_2_2"]
    assert [lig.name for lig in P.read_ligands(plain, "my lig")] == ["my_lig"]
    assert [lig.index for lig in P.read_ligands(text, "set")] == list(range(7))
    # between MODEL blocks: blank, REMARK and END lines only, not given to Vina
    spaced = "REMARK  two ligands\n\n" + models(ALPHA, BETA).replace("ENDMDL\n", "ENDMDL\nEND\n")
    assert [lig.text for lig in P.read_ligands(spaced)] == [ALPHA, BETA]


@pytest.mark.parametrize("text, why", [
    ("", "without ROOT"),
    (atom(1, 0, 0, 0) + "\nTORSDOF 0\n", "begins with ROOT"),
    ("ROOT\n" + atom(1, 0, 0, 0) + "\n", "ROOT without ENDROOT"),
    ("ROOT\n" + atom(1, 0, 0, 0) + "\nTORSDOF 0\n", "only atoms belong between ROOT"),
    ("ROOT\n" + atom(1, 0, 0, 0) + "\nENDROOT\n", "without TORSDOF"),
    ("ROOT\nENDROOT\nTORSDOF 0\n", "without atoms"),
    ("ROOT\n" + atom(1, 0, 0, 0) + "\nENDROOT\nTORSDOF 0\nTORSDOF 0\n", "second TORSDOF"),
    ("ROOT\n" + atom(1, 0, 0, 0) + "\nENDROOT\nTORSDOF x\n", "TORSDOF takes a number"),
    ("ROOT\n" + atom(1, 0, 0, 0) + "\nENDROOT\n" + atom(2, 1, 0, 0) + "\nTORSDOF 0\n",
     "outside ROOT and every BRANCH"),
    ("ROOT\n" + atom(1, 0, 0, 0) + "\nENDROOT\nBRANCH 1 2\n" + atom(2, 1, 0, 0) + "\nTORSDOF 1\n",
     "TORSDOF inside a BRANCH"),
    ("ROOT\n" + atom(1, 0, 0, 0) + "\nENDROOT\nBRANCH 1 2\n" + atom(2, 1, 0, 0) + "\n",
     "has no ENDBRANCH"),
    ("ROOT\n" + atom(1, 0, 0, 0) + "\nENDROOT\nBRANCH 5 2\n" + atom(2, 1, 0, 0)
     + "\nENDBRANCH 5 2\nTORSDOF 1\n", "atom 5 of this BRANCH is not in the part"),
    ("ROOT\n" + atom(1, 0, 0, 0) + "\nENDROOT\nBRANCH 1 2\n" + atom(2, 1, 0, 0)
     + "\nENDBRANCH 1 3\nTORSDOF 1\n", "closes no open BRANCH"),
    ("ROOT\n" + atom(1, 0, 0, 0) + "\nENDROOT\nBRANCH 1 2\n" + atom(3, 1, 0, 0)
     + "\nENDBRANCH 1 2\nTORSDOF 1\n", "atom 2 is not in the branch"),
    ("ROOT\n" + atom(1, 0, 0, 0) + "\nENDROOT\nBRANCH 1\n", "two atom numbers"),
    ("ROOT\n" + atom(1, 0, 0, 0) + "\nENDROOT\nTORSDOF 0\nEND\n", "does not read this record"),
    ("ROOT\n" + atom(1, 0, 0, 0, "Q") + "\nENDROOT\nTORSDOF 0\n", "'Q' is not one"),
    ("MODEL 1\n" + ALPHA, "MODEL without ENDMDL"),
    (ALPHA + "ENDMDL\nMODEL 1\n" + ALPHA + "ENDMDL\n", "outside every MODEL block"),
    ("ENDMDL\nMODEL 1\n" + ALPHA + "ENDMDL\n", "ENDMDL without MODEL"),
    ("MODEL 1\n" + ALPHA + "MODEL 2\n" + ALPHA + "ENDMDL\n", "MODEL inside a MODEL block"),
    ("MODEL 1\n" + ALPHA + "ENDMDL\nMODEL 2\n" + BETA.replace("TORSDOF 1\n", "")
     + "ENDMDL\n", r"line 11: a ligand without TORSDOF"),
])
def test_a_ligand_vina_would_refuse_is_refused_with_its_line(text, why):
    with pytest.raises(DockingError, match=why):
        P.read_ligands(text)


def test_poses_split_by_model_and_rmsd_in_atom_order():
    two = models(ALPHA, ALPHA.replace("   0.000   0.000   0.000", "   3.000   0.000   0.000"))
    split = P.pose_models(two)
    assert len(split) == 2 and split[0] == ALPHA.rstrip("\n").split("\n")
    assert P.pose_models(ALPHA) == [ALPHA.rstrip("\n").split("\n")]
    a = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]])
    assert P.atom_order_rmsd(a, a) == 0.0
    assert P.atom_order_rmsd(a, a[::-1]) == 1.0          # swapped atoms count: no symmetry
    assert P.atom_order_rmsd(a, a[:1]) is None


# ------------------------------------------------------------------ settings and site

@pytest.mark.parametrize("kwargs, why", [
    ({"seed": 0}, "seed: an integer from 1"), ({"seed": 2**31}, "seed"),
    ({"exhaustiveness": 0}, "exhaustiveness"), ({"exhaustiveness": 65}, "exhaustiveness"),
    ({"n_poses": 21}, "n_poses"), ({"n_poses": True}, "n_poses"),
    ({"scoring": "ad4"}, "scoring"), ({"energy_range": 0}, "energy_range"),
    ({"energy_range": float("inf")}, "energy_range"),
    ({"center": (1, 2)}, "center: three finite numbers"),
    ({"center": (1, 2, float("nan"))}, "center: three finite numbers"),
    ({"center": "abc"}, "center: three numbers"), ({"center": "123"}, "center: three numbers"),
    ({"size": (18, 18, 0)}, "longer than 0"),
])
def test_settings_outside_the_bounds_are_refused(kwargs, why):
    with pytest.raises(DockingError, match=why):
        P.PreparedDockConfig(**kwargs)


def test_settings_keep_numbers_as_floats():
    c = P.PreparedDockConfig(center=[1, 2, 3], size=(18, 18, 18), energy_range=3)
    assert c.center == (1.0, 2.0, 3.0) and c.energy_range == 3.0
    assert c.as_dict()["center"] == [1.0, 2.0, 3.0]


def test_the_site_is_the_given_box_or_one_around_the_reference_ligand():
    (ben,) = P.read_ligands(CRYSTAL.read_text())
    around = P.site_box(P.PreparedDockConfig(), ben)
    assert around == Box((-1.8555000000000001, 14.366, 16.747999999999998),
                         (18.0, 18.0, 18.0), "reference ligand")
    given = P.site_box(P.PreparedDockConfig(center=(1, 2, 3), size=(20, 20, 20)), ben)
    assert given == Box((1.0, 2.0, 3.0), (20.0, 20.0, 20.0), "given")
    sized = P.site_box(P.PreparedDockConfig(size=(22, 22, 22)), ben)
    assert sized.center == around.center and sized.size == (22.0, 22.0, 22.0)
    with pytest.raises(DockingError, match="center needs size"):
        P.site_box(P.PreparedDockConfig(center=(1, 2, 3)), ben)
    with pytest.raises(DockingError, match="name the site"):
        P.site_box(P.PreparedDockConfig())
    (hydrogen,) = P.read_ligands("ROOT\n" + atom(1, 0, 0, 0, "HD", "H") + "\nENDROOT\nTORSDOF 0\n")
    with pytest.raises(DockingError, match="no heavy atom to build the box around"):
        P.site_box(P.PreparedDockConfig(), hydrogen)
    assert P.vina_params(P.PreparedDockConfig(), around) == {
        "scoring": "vina", "exhaustiveness": 8, "n_poses": 9, "seed": 42, "energy_range": 3.0,
        "center": [-1.8555000000000001, 14.366, 16.747999999999998],
        "size": [18.0, 18.0, 18.0], "spacing": 0.375, "min_rmsd": 1.0, "max_evals": 0}


# ------------------------------------------------------------------ PrecomputedVina

def write_results(directory: Path, receptor_text: str, ligands, reference, box: Box,
                  config: P.PreparedDockConfig, engine=None, **edits) -> Path:
    """What a browser writes: one result per call, from a stand-in engine."""
    engine = engine or FakeVina()
    directory.mkdir(parents=True, exist_ok=True)
    calls = ([(P.REFERENCE_KEY, reference)] if reference else []) + [(x.key, x) for x in ligands]
    for key, lig in calls:
        doc = {"schema": P.RESULT_SCHEMA, "key": key,
               "receptor_sha256": P.text_sha256(receptor_text), "ligand_sha256": lig.sha256,
               "params": P.vina_params(config, box),
               "engine": {"name": "AutoDock Vina", "version": "1.2.7", "build": "wasm",
                          "wasm_sha256": WASM}}
        try:
            res = engine.dock(receptor_text, lig.text, box, scoring=config.scoring,
                              exhaustiveness=config.exhaustiveness, n_poses=config.n_poses,
                              seed=config.seed, energy_range=config.energy_range, key=key)
        except P.VinaRefused as exc:
            doc["error"] = str(exc)
        else:
            doc.update({"energies": res.energies, "poses": res.poses, "seconds": res.seconds})
        for k, v in edits.get(key, {}).items():
            doc[k] = v
        (directory / f"{key}.json").write_text(json.dumps(doc))
    return directory


def _one(tmp_path, **edits):
    (lig,) = P.read_ligands(ALPHA)
    config = P.PreparedDockConfig(center=(0, 0, 0), size=(10, 10, 10))
    box = P.site_box(config)
    d = write_results(tmp_path / "results", RECEPTOR_TEXT, [lig], None, box, config, **edits)
    kw = dict(scoring="vina", exhaustiveness=8, n_poses=9, seed=42, energy_range=3.0)
    return P.PrecomputedVina(d), lig, box, kw


def test_precomputed_results_are_given_back_for_exactly_their_run(tmp_path):
    vina, lig, box, kw = _one(tmp_path)
    assert (vina.name, vina.version, vina.build, vina.wasm_sha256) == \
        ("AutoDock Vina", "1.2.7", "wasm", WASM)
    res = vina.dock(RECEPTOR_TEXT, lig.text, box, key="ligand-0", **kw)
    assert res.energies[0] == [-5.1234567, -6.1, -0.2, 0.3, -0.2] and res.seconds == 0.25
    assert P.engine_record(vina) == {"name": "AutoDock Vina", "version": "1.2.7",
                                     "build": "wasm", "wasm_sha256": WASM}
    with pytest.raises(DockingError, match="no Vina result for ligand-1"):
        vina.dock(RECEPTOR_TEXT, lig.text, box, key="ligand-1", **kw)
    with pytest.raises(DockingError, match="another receptor"):
        vina.dock(RECEPTOR_TEXT + "REMARK\n", lig.text, box, key="ligand-0", **kw)
    with pytest.raises(DockingError, match="another ligand"):
        vina.dock(RECEPTOR_TEXT, lig.text + "REMARK\n", box, key="ligand-0", **kw)
    with pytest.raises(DockingError, match="other parameters: seed"):
        vina.dock(RECEPTOR_TEXT, lig.text, box, key="ligand-0", **{**kw, "seed": 43})
    off = Box((0.0, 0.0, float(np.nextafter(0.0, 1.0))), box.size, box.source)  # one ulp
    with pytest.raises(DockingError, match="other parameters: center"):
        vina.dock(RECEPTOR_TEXT, lig.text, off, key="ligand-0", **kw)


def test_precomputed_numbers_compare_by_value_as_json_writes_them(tmp_path):
    params = P.vina_params(P.PreparedDockConfig(center=(0, 0, 0), size=(10, 10, 10)),
                           Box((0.0, 0.0, 0.0), (10.0, 10.0, 10.0)))
    as_js = {**params, "energy_range": 3, "center": [0, 0, 0], "size": [10, 10, 10],
             "min_rmsd": 1}                                # JSON.stringify(3.0) is "3"
    vina, lig, box, kw = _one(tmp_path, **{"ligand-0": {"params": as_js}})
    assert vina.dock(RECEPTOR_TEXT, lig.text, box, key="ligand-0", **kw).energies
    vina, lig, box, kw = _one(tmp_path / "b", **{"ligand-0": {"params": {**params,
                                                                          "seed": True}}})
    with pytest.raises(DockingError, match="other parameters: seed"):
        vina.dock(RECEPTOR_TEXT, lig.text, box, key="ligand-0", **{**kw, "seed": 1})
    vina, lig, box, kw = _one(tmp_path / "c", **{"ligand-0": {"params": {**params,
                                                                          "extra": 1}}})
    with pytest.raises(DockingError, match="other parameters: extra"):
        vina.dock(RECEPTOR_TEXT, lig.text, box, key="ligand-0", **kw)


def test_a_ligand_vina_refused_comes_back_as_its_refusal(tmp_path):
    vina, lig, box, kw = _one(tmp_path, **{"ligand-0": {"error": "Atom type Q is not valid"}})
    with pytest.raises(P.VinaRefused, match="Atom type Q"):
        vina.dock(RECEPTOR_TEXT, lig.text, box, key="ligand-0", **kw)


@pytest.mark.parametrize("edit, why", [
    ({"engine": {"name": "AutoDock Vina", "version": "1.2.6", "build": "native"}},
     "AutoDock Vina 1.2.6, not 1.2.7"),
    ({"engine": {"name": "AutoDock Vina", "version": "1.2.7", "build": "wasm"}},
     "WebAssembly build without its SHA-256"),
    ({"engine": None}, "its engine is not named"),
    ({"schema": "other/1"}, "is not a Vina result"),
])
def test_results_from_another_engine_or_format_are_refused(tmp_path, edit, why):
    with pytest.raises(DockingError, match=why):
        _one(tmp_path, **{"ligand-0": edit})


def test_results_must_come_from_one_engine_and_be_well_formed(tmp_path):
    (a, b) = P.read_ligands(models(ALPHA, BETA))
    config = P.PreparedDockConfig(center=(0, 0, 0), size=(10, 10, 10))
    box = P.site_box(config)
    native = {"name": "AutoDock Vina", "version": "1.2.7", "build": "native"}
    d = write_results(tmp_path / "mixed", RECEPTOR_TEXT, [a, b], None, box, config,
                      **{"ligand-1": {"engine": native}})
    with pytest.raises(DockingError, match="different engines"):
        P.PrecomputedVina(d)
    kw = dict(scoring="vina", exhaustiveness=8, n_poses=9, seed=42, energy_range=3.0)
    d = write_results(tmp_path / "bad", RECEPTOR_TEXT, [a, b], None, box, config,
                      **{"ligand-0": {"energies": [[1, 2, 3]]}, "ligand-1": {"key": "ligand-0"}})
    vina = P.PrecomputedVina(d)
    with pytest.raises(DockingError, match="rows of five numbers"):
        vina.dock(RECEPTOR_TEXT, a.text, box, key="ligand-0", **kw)
    with pytest.raises(DockingError, match="holds the result of 'ligand-0', not ligand-1"):
        vina.dock(RECEPTOR_TEXT, b.text, box, key="ligand-1", **kw)
    with pytest.raises(DockingError, match="not a directory"):
        P.PrecomputedVina(tmp_path / "none")
    (tmp_path / "empty").mkdir()
    (tmp_path / "empty" / "notes.json").write_text("{}")
    with pytest.raises(DockingError, match="holds no Vina result"):
        P.PrecomputedVina(tmp_path / "empty")


# ------------------------------------------------------------------ the pipeline

def test_the_stages_with_a_stand_in_for_vina(tmp_path):
    fake = FakeVina()
    run = run_world(tmp_path, fake)
    out = tmp_path / "run"
    manifest = json.loads((out / "run.json").read_text())
    stages = {s["name"]: s for s in manifest["stages"]}
    assert list(stages) == list(P.STAGES)
    assert all(s["status"] == "succeeded" for s in stages.values())
    assert stages["redock_validation"]["check"] == "PASS"
    assert stages["redock_validation"]["engine"] == "AutoDock Vina"
    assert stages["pose_checks"]["check"] == "PASS" and stages["verify"]["check"] == "PASS"
    assert run.complete and manifest["complete"] and run.validated
    # every call: the reference first, then the ligands in the file's order, one setting
    assert [k for k, _ in fake.calls] == ["reference", "ligand-0", "ligand-1"]
    assert {json.dumps(c, sort_keys=True) for _, c in fake.calls} == {json.dumps(
        {"scoring": "vina", "exhaustiveness": 8, "n_poses": 9, "seed": 42, "energy_range": 3.0,
         "center": run.box.center, "size": run.box.size}, sort_keys=True)}
    assert run.box.source == "reference ligand" and run.box.size == (18.0, 18.0, 18.0)
    v = manifest["validation"]
    assert v["top_pose_rmsd"] == 0.0 and v["passed"] and v["rmsd"] == P.RMSD_METHOD
    assert [p["rank"] for p in v["poses"]] == [1, 2, 3] and v["ligand"] == "ref"
    table = (out / "scores.tsv").read_text().splitlines()
    assert table[0].split("\t") == list(P.SCORES_HEADER)
    # ranked by the best score: beta (-5.247) before alpha (-5.123)
    assert table[1].split("\t") == ["beta", "-5.247", "1.749", "3", "1", "3", "pass", "3.93"]
    assert table[2].split("\t")[:6] == ["alpha", "-5.123", "2.562", "2", "0", "3"]
    for k, name in enumerate(("alpha", "beta")):
        assert (out / "poses" / f"{name}.pdbqt").read_text() == \
            fake.dock("", (ALPHA, BETA)[k], run.box, scoring="vina", exhaustiveness=8,
                      n_poses=9, seed=42, energy_range=3.0, key=f"ligand-{k}").poses
    assert (out / "redock" / "ref.pdbqt").is_file()
    assert manifest["mode"] == "prepared" and manifest["pipeline"] == P.PIPELINE
    assert manifest["engine"] == {"name": "AutoDock Vina", "version": "1.2.7", "build": "wasm",
                                  "wasm_sha256": WASM}
    assert manifest["versions"] == {"vina": "1.2.7"}
    raw = {r["ligand"]: r["energies"] for r in manifest["results"]}
    assert raw["beta"][0][0] == -5.2469134 and raw["alpha"][0][0] == -5.1234567
    assert [x["name"] for x in manifest["ligands"]] == ["alpha", "beta"]
    assert manifest["reference"]["key"] == "reference"
    assert manifest["receptor"]["atoms"] == 5 and manifest["receptor"]["chains"] == ["A", "B"]
    assert set(manifest["inputs"]) == {str(tmp_path / f) for f in ("rec.pdbqt", "ligs.pdbqt",
                                                                   "ref.pdbqt")}
    lines = (out / "limitations.md").read_text().splitlines()
    assert lines == manifest["limitations"]
    assert any("atom order, without symmetry" in x for x in lines)
    assert any("not checked" in x for x in lines)
    assert not any("not validated by redocking" in x for x in lines)
    md = (out / "report.md").read_text()
    assert "**Validated**" in md and "WebAssembly build, SHA-256 abababababab" in md
    assert "plots/scores.svg" in md and (out / "plots" / "scores.svg").is_file()
    assert P.verify_run(out) == (True, [])


def test_without_a_reference_the_site_is_the_box_and_the_setup_is_not_validated(tmp_path):
    run = run_world(tmp_path, reference=False, center=(0, 0, 0), size=(10, 10, 10))
    stages = {s["name"]: s for s in run.stages}
    assert stages["redock_validation"]["status"] == "skipped"
    assert run.validation is None and not run.validated and run.complete
    assert run.box == Box((0.0, 0.0, 0.0), (10.0, 10.0, 10.0), "given")
    assert any("not validated" in w for w in run.warnings)
    assert "**Not validated**" in (tmp_path / "run" / "report.md").read_text()
    assert any("not validated by redocking" in x for x in run.limitations)


def test_a_failed_redocking_makes_the_run_incomplete(tmp_path):
    run = run_world(tmp_path, FakeVina(shift=3.0))
    v = run.validation
    assert v["top_pose_rmsd"] == 3.0 and not v["passed"] and not run.complete
    stages = {s["name"]: s for s in run.stages}
    assert stages["redock_validation"]["check"] == "FAIL"
    ok, problems = P.verify_run(tmp_path / "run")
    assert not ok and "run.json records the run as incomplete" in problems


def test_vina_refusing_the_reference_fails_the_validation(tmp_path):
    run = run_world(tmp_path, FakeVina(refuse=("reference",)))
    stages = {s["name"]: s for s in run.stages}
    assert stages["redock_validation"]["status"] == "failed"
    assert "Vina refused the reference ligand ref" in stages["redock_validation"]["reason"]
    assert run.validation["error"].startswith("PDBQT parsing error") and not run.complete
    assert "Vina refused it" in (tmp_path / "run" / "report.md").read_text()


def test_a_ligand_vina_refuses_is_a_failure_and_the_others_go_on(tmp_path):
    run = run_world(tmp_path, FakeVina(refuse=("ligand-0",)))
    assert list(run.failures) == ["alpha"] and [r.ligand.name for r in run.results] == ["beta"]
    assert run.complete
    assert any("could not be docked" in w for w in run.warnings)
    assert any("could not be docked" in x and "alpha" in x for x in run.limitations)
    assert not (tmp_path / "run" / "poses" / "alpha.pdbqt").exists()
    assert P.verify_run(tmp_path / "run") == (True, [])


def test_no_ligand_docked_is_an_incomplete_run(tmp_path):
    run = run_world(tmp_path, FakeVina(refuse=("ligand-0", "ligand-1")))
    stages = {s["name"]: s for s in run.stages}
    assert stages["dock"]["status"] == "failed" and "Vina refused all 2" in \
        stages["dock"]["reason"]
    assert stages["pose_checks"]["status"] == "not_applicable" and not run.complete
    ok, problems = P.verify_run(tmp_path / "run")
    assert not ok and "no ligand was docked" in problems


def test_a_best_pose_on_a_receptor_atom_fails_its_checks(tmp_path):
    run = run_world(tmp_path, FakeVina(clash=("ligand-1",)))
    best = run.pose_checks["beta"]["best"]
    assert "no_clash" in best["failed"] and run.pose_checks["alpha"]["best"]["passed"]
    assert run.pose_checks["beta"]["poses_failed"] == [1, 2, 3] and not run.complete
    row = (tmp_path / "run" / "scores.tsv").read_text().splitlines()[1].split("\t")
    assert row[0] == "beta" and row[6].startswith("fail: ") and "no_clash" in row[6]


def test_a_result_that_is_not_the_ligand_is_refused_not_counted(tmp_path):
    with pytest.raises(DockingError, match="pose 1 does not have the ligand's 3 heavy atoms"):
        run_world(tmp_path, FakeVina(bad_atoms=("ligand-1",)))
    stages = json.loads((tmp_path / "run" / "stages.json").read_text())["stages"]
    assert stages[-1]["name"] == "dock" and stages[-1]["status"] == "failed"


def test_an_input_is_refused_before_anything_is_docked(tmp_path):
    fake = FakeVina()
    rec, ligs, ref = write_world(tmp_path)
    ref.write_text(models(ALPHA, BETA))
    with pytest.raises(DockingError, match="a reference is one ligand; this file holds 2"):
        P.run_prepared_docking(rec, ligs, P.PreparedDockConfig(), tmp_path / "run",
                               engine=fake, reference_path=ref)
    stages = json.loads((tmp_path / "run" / "stages.json").read_text())["stages"]
    assert [(x["name"], x["status"]) for x in stages] == [("inputs", "failed")]
    with pytest.raises(DockingError, match="name the site"):
        run_world(tmp_path / "b", fake, reference=False)
    assert fake.calls == []


def test_verifying_a_tampered_run_finds_what_changed(tmp_path):
    run_world(tmp_path)
    out = tmp_path / "run"
    (out / "scores.tsv").write_text((out / "scores.tsv").read_text().replace("-5.247", "-9.999"))
    (out / "poses" / "alpha.pdbqt").unlink()
    (tmp_path / "ligs.pdbqt").write_text(models(ALPHA))
    ok, problems = P.verify_run(out)
    assert not ok
    assert "output scores.tsv has changed" in problems
    assert "output poses/alpha.pdbqt is missing" in problems
    assert f"input {tmp_path / 'ligs.pdbqt'} has changed" in problems
    doc = json.loads((out / "run.json").read_text())
    (out / "run.json").write_text(json.dumps({**doc, "pipeline": "bioagent.docking"}))
    assert any("is not a bioagent.docking.prepared run" in p for p in P.verify_run(out)[1])


def test_what_a_browser_computed_gives_the_same_outputs_byte_for_byte(tmp_path):
    """The browser's analysis: the same stages on the results its Vina wrote
    (PrecomputedVina) give exactly the outputs of the run that called the engine."""
    fake = FakeVina(refuse=("ligand-0",))
    direct = run_world(tmp_path / "a", fake)
    rec, ligs, ref = (tmp_path / "a" / n for n in ("rec.pdbqt", "ligs.pdbqt", "ref.pdbqt"))
    ligands = P.read_ligands(ligs.read_text(), "ligs")
    (reference,) = P.read_ligands(ref.read_text(), "ref")
    config = P.PreparedDockConfig()
    results = write_results(tmp_path / "results", rec.read_text(), ligands, reference,
                            direct.box, config, engine=fake)
    replay = P.run_prepared_docking(rec, ligs, config, tmp_path / "b",
                                    engine=P.PrecomputedVina(results), reference_path=ref)
    assert replay.complete == direct.complete and replay.failures == direct.failures
    assert replay.manifest["outputs"] == direct.manifest["outputs"]
    for rel in direct.manifest["outputs"]:
        assert (tmp_path / "b" / rel).read_bytes() == (tmp_path / "a" / "run" / rel).read_bytes()
    assert replay.manifest["results"] == direct.manifest["results"]
    assert replay.manifest["validation"] == direct.manifest["validation"]


# ------------------------------------------------------------------ the CLI's prepared mode

@pytest.fixture
def cli(monkeypatch):
    from bioagent import cli as C
    monkeypatch.setattr(P, "NativeVina", FakeVina)
    return C


def test_a_pdbqt_receptor_switches_the_cli_to_the_prepared_mode(tmp_path, cli, capsys):
    rec, ligs, ref = write_world(tmp_path)
    upper = tmp_path / "REC.PDBQT"
    upper.write_text(rec.read_text())
    code = cli.main(["dock", str(upper), str(ligs), "--out", str(tmp_path / "run"),
                     "--reference-ligand", str(ref), "--seed", "7", "--poses", "3"])
    printed = capsys.readouterr().out
    assert code == 0, printed
    assert "redocked ref to 0.00 Å, heavy atoms in atom order (passed)" in printed
    assert "beta: -5.247 kcal/mol" in printed
    manifest = json.loads((tmp_path / "run" / "run.json").read_text())
    assert manifest["config"]["seed"] == 7 and manifest["config"]["n_poses"] == 3
    assert cli.main(["dock", "--verify", str(tmp_path / "run")]) == 0
    assert "verified: every output matches run.json" in capsys.readouterr().out
    (tmp_path / "run" / "scores.tsv").write_text("x\n")
    assert cli.main(["dock", "--verify", str(tmp_path / "run")]) == 1
    assert "output scores.tsv has changed" in capsys.readouterr().out


def test_the_cli_gives_a_box_and_exits_1_on_an_incomplete_run(tmp_path, cli, capsys,
                                                               monkeypatch):
    rec, ligs, ref = write_world(tmp_path)
    assert cli.main(["dock", str(rec), str(ligs), "--out", str(tmp_path / "a"),
                     "--center", "0,0,0", "--size", "10,10,10"]) == 0
    assert "validation: none (no reference ligand)" in capsys.readouterr().out
    monkeypatch.setattr(P, "NativeVina", lambda: FakeVina(shift=3.0))
    code = cli.main(["dock", str(rec), str(ligs), "--out", str(tmp_path / "b"),
                     "--reference-ligand", str(ref)])
    assert code == 1
    err = capsys.readouterr().err
    assert "incomplete: stage redock_validation: its check failed: top pose 3.00 Å" in err
    assert cli.main(["dock", str(rec), str(ligs), "--out", str(tmp_path / "c"),
                     "--seed", "0", "--reference-ligand", str(ref)]) == 1
    assert "refused: seed: an integer from 1" in capsys.readouterr().err


@pytest.mark.parametrize("extra", [["--site-ligand", "BEN"], ["--site-ligand-smiles", "CCO"],
                                   ["--site-residue", "A:189"], ["--chains", "A"],
                                   ["--cofactor", "HEM"], ["--allow-remote"]])
def test_the_prepared_mode_refuses_what_only_preparation_uses(tmp_path, cli, capsys, extra):
    rec, ligs, ref = write_world(tmp_path)
    code = cli.main(["dock", str(rec), str(ligs), "--out", str(tmp_path / "run"),
                     "--reference-ligand", str(ref), *extra])
    assert code == 2
    err = capsys.readouterr().err
    assert f"so {extra[0]} cannot apply" in err and "--reference-ligand" in err
    assert not (tmp_path / "run").exists()


def test_the_prepared_mode_takes_pdbqt_files_only(tmp_path, cli, capsys):
    rec, ligs, ref = write_world(tmp_path)
    (tmp_path / "l.csv").write_text("name,smiles\na,CCO\n")
    assert cli.main(["dock", str(rec), str(tmp_path / "l.csv"), "--out",
                     str(tmp_path / "run")]) == 2
    assert "the ligands must be a .pdbqt file" in capsys.readouterr().err
    assert cli.main(["dock", str(rec), str(ligs), "--out", str(tmp_path / "run"),
                     "--reference-ligand", str(tmp_path / "l.csv")]) == 2
    assert "--reference-ligand must be a .pdbqt file" in capsys.readouterr().err
    pdb = Path(__file__).parent / "fixtures" / "docking" / "3ptb_pocket.pdb"
    assert cli.main(["dock", str(pdb), str(tmp_path / "l.csv"), "--out", str(tmp_path / "run"),
                     "--reference-ligand", str(ref)]) == 2
    assert "--reference-ligand is for prepared inputs" in capsys.readouterr().err


# ------------------------------------------------------------------ AutoDock Vina 1.2.7

def _vina_127():
    vina = need_module("vina")
    if getattr(vina, "__version__", "") != P.VINA_VERSION:
        message = f"vina {getattr(vina, '__version__', '?')} is not {P.VINA_VERSION}"
        if os.environ.get("BIOAGENT_REQUIRE_TOOLS"):
            pytest.fail(message)
        pytest.skip(message)
    return vina


def test_native_vina_refuses_what_it_cannot_read():
    _vina_127()
    box = Box((-1.8555, 14.366, 16.748), (18.0, 18.0, 18.0))
    with pytest.raises(P.VinaRefused, match="PDBQT parsing error"):
        P.NativeVina().dock(RECEPTOR.read_text(), "ROOT\nENDROOT\nEND\n", box, scoring="vina",
                            exhaustiveness=1, n_poses=1, seed=1, energy_range=3.0)


def test_vina_docks_the_fixture_and_redocks_the_crystal_ligand(tmp_path):
    """The prepared fixture with AutoDock Vina 1.2.7 (seed 42, exhaustiveness 8): every
    ligand docks, BEN redocks within 2 Å (atom order) and benzamidine scores below -5."""
    _vina_127()
    run = P.run_prepared_docking(RECEPTOR, LIGANDS, P.PreparedDockConfig(), tmp_path / "run",
                                 reference_path=CRYSTAL)
    assert run.complete, run.stages
    assert run.validation["passed"] and run.validation["top_pose_rmsd"] <= P.REDOCK_RMSD
    assert run.engine == {"name": "AutoDock Vina", "version": "1.2.7", "build": "native"}
    best = {r.ligand.name: r.best_score for r in run.results}
    assert sorted(best) == sorted(NAMES) and not run.failures
    assert best["benzamidine"] < -5.0
    assert all(run.pose_checks[r.ligand.name]["best"]["passed"] for r in run.results)
    assert P.verify_run(tmp_path / "run") == (True, [])
