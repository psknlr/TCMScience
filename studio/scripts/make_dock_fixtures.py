#!/usr/bin/env python3
"""The prepared-docking fixtures and the browser Vina's native reference (studio/web/js/scientific/dock).

The browser docks prepared inputs with AutoDock Vina 1.2.7 compiled to WebAssembly (studio/scripts/build_vina_wasm.py),
and the Python stages are bioagent.docking.prepared's. Its fixtures are made here from 3PTB's pocket with native
RDKit 2026.09.1, Meeko 0.8.0 and Vina 1.2.7 (any other version is refused: the browser's engine is that Vina, and the
inputs are prepared as bioagent.docking.prep prepares them with those pins), never by hand:

  BioScience-Harness/tests/fixtures/docking/prepared/
      3ptb_receptor.pdbqt   the pocket's protein as bioagent.docking.prep.prepare_receptor writes it (waters and the
                            ligand BEN removed; meeko residue templates, polar hydrogens, Gasteiger charges)
      ligands.pdbqt         six ligands as MODEL blocks, each named by a "REMARK  Name = <name>" line, from SMILES with
                            prep.prepare_ligand(name, smiles, seed=42) (ETKDG v3, MMFF94, meeko)
      ben_crystal.pdbqt     BEN in its crystal pose: the crystal's heavy atoms with bond orders from NC(=N)c1ccccc1
                            (prep.cocrystal_ligand), Chem.AddHs(addCoords=True), meeko, coordinates kept
      README.md             their provenance and SHA-256s
  studio/web/test/fixtures/vina_parity.json
      what native Vina 1.2.7 gives on them (raw C++ values, unrounded), for the WebAssembly engine to be held to:
      per ligand, score() and optimize() of a fixed pose (the top pose of the seed-1 dock) with the optimised pose's
      text; the dock (exhaustiveness 8, 9 poses) for seeds 1-8: every energy row and the poses' SHA-256; and the
      crystal BEN redocked for seeds 1-8: the top pose's heavy-atom RMSD in atom order (bioagent.docking.prepared)
  studio/web/test/fixtures/vina/<ligand>.pdbqt, <ligand>.top.pdbqt
      each ligand's text exactly as Vina is given it, and the fixed pose score() and optimize() start from

score() and optimize() of a given pose are deterministic, and the WebAssembly build reproduces them bit for bit. A
seeded dock is not reproducible across machines (the Monte Carlo search amplifies last-bit differences of libm and
floating-point contraction), so the docks are recorded with the machine they ran on ("native") and --check leaves
them out: it regenerates the inputs, the per-ligand texts and score()/optimize() on the committed poses, and fails
when any of them differs.

    PYTHONPATH=BioScience-Harness/src python studio/scripts/make_dock_fixtures.py  # writes everything (minutes)
    PYTHONPATH=BioScience-Harness/src python studio/scripts/make_dock_fixtures.py --check  # the deterministic parts
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

STUDIO = Path(__file__).resolve().parents[1]
REPO = STUDIO.parent
BSH = REPO / "BioScience-Harness"
POCKET = BSH / "tests" / "fixtures" / "docking" / "3ptb_pocket.pdb"
PREPARED = BSH / "tests" / "fixtures" / "docking" / "prepared"
RECEPTOR = PREPARED / "3ptb_receptor.pdbqt"
LIGANDS = PREPARED / "ligands.pdbqt"
CRYSTAL = PREPARED / "ben_crystal.pdbqt"
README = PREPARED / "README.md"
WEB_FIX = STUDIO / "web" / "test" / "fixtures"
PARITY = WEB_FIX / "vina_parity.json"
POSES = WEB_FIX / "vina"

BSH_SRC = BSH / "src"
if BSH_SRC.is_dir() and str(BSH_SRC) not in sys.path:
    sys.path.insert(0, str(BSH_SRC))

PINS = {"rdkit": "2026.09.1", "meeko": "0.8.0", "vina": "1.2.7"}
BENZAMIDINE = "NC(=N)c1ccccc1"
CRYSTAL_NAME = "BEN"
LIGAND_SEED = 42                      # the docking pipeline embeds its ligands with seed 42
# 3PTB's own ligand and an analogue, and four small TCM constituents of different sizes and torsion counts
LIGAND_SMILES = (("benzamidine", BENZAMIDINE), ("p_methylbenzamidine", "Cc1ccc(cc1)C(N)=N"),
                 ("baicalein", "O=c1cc(-c2ccccc2)oc2cc(O)c(O)c(O)c12"),
                 ("ibuprofen", "CC(C)Cc1ccc(cc1)C(C)C(=O)O"), ("ferulic_acid", "COc1cc(/C=C/C(=O)O)ccc1O"),
                 ("paeonol", "COc1ccc(C(C)=O)c(O)c1"))
SEEDS = tuple(range(1, 9))
SCORE_SEED = 1                        # score()/optimize() start from the top pose of this seed's dock
DOCK = {"scoring": "vina", "exhaustiveness": 8, "n_poses": 9, "energy_range": 3.0}


class FixtureError(RuntimeError):
    pass


def _rel(path: Path) -> str:
    return path.relative_to(REPO).as_posix()


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def tools() -> dict[str, Any]:
    """The pinned native modules, or a refusal naming the pin."""
    import importlib
    found = {}
    for name, pin in PINS.items():
        try:
            mod = importlib.import_module(name)
        except ImportError as exc:
            raise FixtureError(f"needs {name} {pin} (pip install rdkit==2026.9.1 meeko==0.8.0 vina==1.2.7 "
                               "gemmi==0.7.5)") from exc
        version = str(getattr(mod, "__version__", ""))
        if version != pin:
            raise FixtureError(f"needs {name} {pin}, the version the browser's engine and preparation are pinned to; "
                               f"found {version or 'no version'}")
        found[name] = mod
    from rdkit import RDLogger
    RDLogger.DisableLog("rdApp.*")
    return found


# ------------------------------------------------------------------------------------------------- inputs

def prepared_inputs() -> dict[Path, str]:
    """The three PDBQT fixtures, as bioagent.docking.prep makes them (deterministic for the pinned versions)."""
    import meeko
    from rdkit import Chem
    from bioagent.docking import prep

    pocket = POCKET.read_text(encoding="utf-8")
    receptor = prep.prepare_receptor(pocket)
    if receptor.unmatched:
        raise FixtureError(f"meeko matched no template for residues {receptor.unmatched}")
    blocks = []
    for k, (name, smiles) in enumerate(LIGAND_SMILES, 1):
        lig = prep.prepare_ligand(name, smiles, seed=LIGAND_SEED)
        blocks.append(f"MODEL {k}\nREMARK  Name = {name}\n{lig.pdbqt.rstrip(chr(10))}\nENDMDL\n")
    crystal, _ = prep.cocrystal_ligand(pocket, CRYSTAL_NAME, smiles=BENZAMIDINE)
    with_h = Chem.AddHs(crystal, addCoords=True)
    setup = meeko.MoleculePreparation().prepare(with_h)[0]
    text, ok, err = meeko.PDBQTWriterLegacy.write_string(setup)
    if not ok:
        raise FixtureError(f"meeko could not write the crystal ligand: {err}")
    return {RECEPTOR: receptor.pdbqt.rstrip("\n") + "\n", LIGANDS: "".join(blocks),
            CRYSTAL: f"REMARK  Name = {CRYSTAL_NAME}\n{text.rstrip(chr(10))}\n"}


def read_back(texts: dict[Path, str]) -> tuple[Any, list[Any], Any, Any]:
    """The inputs as bioagent.docking.prepared reads them, the box the pipeline builds around the crystal ligand,
    checked against the crystal's own coordinates."""
    import numpy as np
    from bioagent.docking import prepared as P
    from bioagent.docking.engine import Box

    receptor = P.read_receptor(texts[RECEPTOR])
    ligands = P.read_ligands(texts[LIGANDS], "ligands")
    (reference,) = P.read_ligands(texts[CRYSTAL], "ben_crystal")
    if [lig.name for lig in ligands] != [n for n, _ in LIGAND_SMILES] or reference.name != CRYSTAL_NAME:
        raise FixtureError("the ligands do not read back under their names")
    pdb = np.array([[float(ln[30:38]), float(ln[38:46]), float(ln[46:54])]
                    for ln in POCKET.read_text(encoding="utf-8").splitlines()
                    if ln.startswith("HETATM") and ln[17:20] == CRYSTAL_NAME])
    if sorted(map(tuple, pdb.tolist())) != sorted(map(tuple, reference.heavy_xyz.tolist())):
        raise FixtureError("ben_crystal.pdbqt's heavy atoms are not the crystal's coordinates")
    box = P.site_box(P.PreparedDockConfig(), reference)
    if (box.center, box.size) != (Box.around(pdb).center, Box.around(pdb).size):
        raise FixtureError("the box around ben_crystal.pdbqt is not the box around the crystal ligand")
    return receptor, ligands, reference, box


def readme(texts: dict[Path, str], box: Any) -> str:
    rows = "\n".join(f"| {name} | `{smiles}` |" for name, smiles in LIGAND_SMILES)
    sha = {p.name: _sha(t) for p, t in texts.items()}
    return f"""# Prepared docking fixtures (PDBQT)

Written by `studio/scripts/make_dock_fixtures.py` from `../3ptb_pocket.pdb` (PDB entry 3PTB, bovine trypsin with
benzamidine; wwPDB data are CC0 1.0) with RDKit {PINS['rdkit']}, Meeko {PINS['meeko']} and
`bioagent.docking.prep`; `--check` regenerates them and fails on any difference. They are the inputs of the prepared
mode (`bioagent.docking.prepared`, `bioagent dock RECEPTOR.pdbqt LIGANDS.pdbqt`) and of the browser's docking job.

- `3ptb_receptor.pdbqt`: the pocket's protein as `prep.prepare_receptor` writes it (waters and BEN removed; meeko
  residue templates, polar hydrogens, Gasteiger charges, AutoDock atom types). SHA-256
  `{sha['3ptb_receptor.pdbqt']}`.
- `ligands.pdbqt`: six ligands as MODEL blocks, each named by a `REMARK  Name = <name>` line, embedded from SMILES
  with `prep.prepare_ligand(name, smiles, seed={LIGAND_SEED})` (ETKDG v3, MMFF94, meeko; protonation as written).
  SHA-256 `{sha['ligands.pdbqt']}`.
- `ben_crystal.pdbqt`: BEN in its crystal pose, the reference ligand: the crystal's heavy atoms with bond orders from
  `{BENZAMIDINE}` (`prep.cocrystal_ligand`), hydrogens added at computed positions (`Chem.AddHs(addCoords=True)`),
  meeko's PDBQT with the coordinates kept. SHA-256 `{sha['ben_crystal.pdbqt']}`.

| ligand | SMILES |
|---|---|
{rows}

The box the pipeline builds around the reference (8 Å of padding, at least 18 Å a side): centre
({', '.join(f'{c:g}' for c in box.center)}), size ({', '.join(f'{c:g}' for c in box.size)}) Å.

What native AutoDock Vina 1.2.7 gives on these files is in `studio/web/test/fixtures/vina_parity.json`.
"""


# ------------------------------------------------------------------------------------------------- native Vina

def _vina(receptor_text: str, ligand_text: str, box: Any):
    import vina
    v = vina.Vina(sf_name="vina", seed=42, verbosity=0)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "receptor.pdbqt"
        path.write_bytes(receptor_text.encode("utf-8"))
        v.set_receptor(rigid_pdbqt_filename=str(path))
    v.set_ligand_from_string(ligand_text)
    v.compute_vina_maps(center=list(box.center), box_size=list(box.size), spacing=0.375)
    return v


def score_and_optimize(receptor_text: str, pose: str, box: Any) -> dict[str, Any]:
    """score() and optimize(0) of a pose, raw (the C++ values; the Python wrapper rounds to 3 decimals), and the
    optimised pose as Vina writes it."""
    score = [float(x) for x in _vina(receptor_text, pose, box)._vina.score()]
    v = _vina(receptor_text, pose, box)
    optimize = [float(x) for x in v._vina.optimize(0)]
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "optimized.pdbqt"
        v.write_pose(str(path), overwrite=True)
        text = path.read_text(encoding="utf-8")
    return {"score_raw": score, "optimize_raw": optimize, "optimize_pose": text}


def first_pose(poses: str) -> str:
    from bioagent.docking.prepared import pose_models
    return "\n".join(pose_models(poses)[0]) + "\n"


def searches(receptor: Any, ligands: list[Any], reference: Any, box: Any) -> tuple[dict, dict]:
    """The seeded docks (this machine's), and the crystal ligand redocked per seed."""
    from bioagent.docking import prepared as P
    engine = P.NativeVina()
    docks: dict[str, dict[str, Any]] = {}
    for lig in ligands:
        docks[lig.name] = {}
        for seed in SEEDS:
            res = engine.dock(receptor.text, lig.text, box, seed=seed, key=lig.key, **DOCK)
            docks[lig.name][str(seed)] = {"best_raw": res.energies[0][0], "energies_raw": res.energies,
                                          "poses_sha256": _sha(res.poses), "poses": res.poses}
            print(f"  {lig.name} seed {seed}: {res.energies[0][0]:.3f} ({res.seconds:.1f} s)", flush=True)
    redock = {}
    for seed in SEEDS:
        res = engine.dock(receptor.text, reference.text, box, seed=seed, key=P.REFERENCE_KEY, **DOCK)
        top = P.pose_heavy_xyz(P.pose_models(res.poses)[0])
        redock[str(seed)] = P.atom_order_rmsd(top, reference.heavy_xyz)
        print(f"  {reference.name} redocked, seed {seed}: {redock[str(seed)]:.2f} Å", flush=True)
    return docks, redock


def native_machine() -> dict[str, Any]:
    import vina
    out: dict[str, Any] = {"vina": vina.__version__, "meeko": PINS["meeko"], "rdkit": PINS["rdkit"],
                           "python": platform.python_version(), "platform": platform.platform()}
    libc, version = platform.libc_ver()
    if libc == "glibc":
        out["glibc"] = version
    try:
        cpu = next((ln.split(":", 1)[1].strip() for ln in Path("/proc/cpuinfo").read_text().splitlines()
                    if ln.startswith("model name")), None)
    except OSError:
        cpu = None
    if cpu:
        out["cpu"] = cpu
    out["search"] = ("dock and crystal_redock ran here; a seeded search is not bit-reproducible across machines, "
                     "so they are not part of --check")
    return out


# ------------------------------------------------------------------------------------------------- the fixture

def deterministic(texts: dict[Path, str], poses: dict[str, str]) -> dict[str, Any]:
    """Everything of vina_parity.json that does not depend on the machine: the inputs, and score()/optimize() of the
    given fixed poses (one per ligand)."""
    receptor, ligands, reference, box = read_back(texts)
    rows = []
    for lig, (_, smiles) in zip(ligands, LIGAND_SMILES):
        pose = poses[lig.name]
        rows.append({"name": lig.name, "smiles": smiles, "sha256": lig.sha256, "heavy_atoms": lig.heavy_atoms,
                     "torsions": lig.torsions, "pdbqt": _rel(POSES / f"{lig.name}.pdbqt"),
                     "top_pose": _rel(POSES / f"{lig.name}.top.pdbqt"), "top_pose_sha256": _sha(pose),
                     **score_and_optimize(receptor.text, pose, box)})
    return {
        "schema": "tcmstudio.vina-parity/1",
        "files": {"receptor": _rel(RECEPTOR), "ligands": _rel(LIGANDS), "reference": _rel(CRYSTAL)},
        "params": {**DOCK, "spacing": 0.375, "min_rmsd": 1.0, "max_evals": 0, "seeds": list(SEEDS),
                   "score_input": f"the top pose of the seed-{SCORE_SEED} dock"},
        "box": {"center": list(box.center), "size": list(box.size), "source": box.source},
        "receptor_sha256": receptor.sha256,
        "reference": {"name": reference.name, "sha256": reference.sha256, "heavy_atoms": reference.heavy_atoms,
                      "torsions": reference.torsions},
        "ligands": rows,
    }


def _dump(doc: dict[str, Any]) -> str:
    """JSON with one space of indent and every innermost list of numbers on one line."""
    text = json.dumps(doc, indent=1, ensure_ascii=False)
    number = r"-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?"
    return re.sub(rf"\[\s*({number}(?:,\s*{number})*)\s*\]",
                  lambda m: "[" + ", ".join(x.strip() for x in m.group(1).split(",")) + "]", text) + "\n"


def write() -> list[Path]:
    texts = prepared_inputs()
    receptor, ligands, reference, box = read_back(texts)
    print(f"docking {len(ligands)} ligands and the crystal ligand, seeds {SEEDS[0]}-{SEEDS[-1]}", flush=True)
    docks, redock = searches(receptor, ligands, reference, box)
    poses = {lig.name: first_pose(docks[lig.name][str(SCORE_SEED)]["poses"]) for lig in ligands}
    doc = deterministic(texts, poses)
    doc["native"] = native_machine()
    for row in doc["ligands"]:
        row["dock"] = {seed: {k: v for k, v in d.items() if k != "poses"} for seed, d in docks[row["name"]].items()}
    doc["crystal_redock"] = redock
    files = {**texts, README: readme(texts, box), PARITY: _dump(doc)}
    for lig in ligands:
        files[POSES / f"{lig.name}.pdbqt"] = lig.text
        files[POSES / f"{lig.name}.top.pdbqt"] = poses[lig.name]
    for path, text in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        print(f"wrote {_rel(path)}")
    return list(files)


def check() -> int:
    if not PARITY.is_file():
        raise FixtureError(f"{_rel(PARITY)} does not exist yet; run without --check")
    texts = prepared_inputs()
    _, ligands, _, box = read_back(texts)
    stale = []
    for path, text in {**texts, README: readme(texts, box),
                       **{POSES / f"{lig.name}.pdbqt": lig.text for lig in ligands}}.items():
        if not path.is_file() or path.read_text(encoding="utf-8") != text:
            stale.append(_rel(path))
    committed = json.loads(PARITY.read_text(encoding="utf-8"))
    poses = {}
    for lig in ligands:
        path = POSES / f"{lig.name}.top.pdbqt"
        if not path.is_file():
            raise FixtureError(f"{_rel(path)} is missing; run without --check")
        poses[lig.name] = path.read_text(encoding="utf-8")
    fresh = deterministic(texts, poses)
    keep = {k: committed.get(k) for k in fresh}
    keep["ligands"] = [{k: row.get(k) for k in fresh["ligands"][0]} for row in committed.get("ligands") or []]
    if keep != fresh:
        differ = [k for k in fresh if k != "ligands" and keep[k] != fresh[k]]
        differ += [f"ligands[{r['name']}].{k}" for r, c in zip(fresh["ligands"], keep["ligands"]) for k in r
                   if c.get(k) != r[k]]
        if len(keep["ligands"]) != len(fresh["ligands"]):
            differ.append("ligands")
        stale.append(f"{_rel(PARITY)} ({', '.join(differ)})")
    for item in stale:
        print(f"out of date: {item}", file=sys.stderr)
    if not stale:
        print("the inputs, the ligand texts and score()/optimize() on the committed poses are as committed")
    return 1 if stale else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--check", action="store_true",
                    help="regenerate the deterministic parts (inputs, score and optimize) and fail when any differs")
    args = ap.parse_args(argv)
    try:
        tools()
        if args.check:
            return check()
        write()
    except FixtureError as exc:
        print(f"make_dock_fixtures: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
