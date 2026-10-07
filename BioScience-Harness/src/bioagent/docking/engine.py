"""Docking with AutoDock Vina (Trott & Olson 2010; Eberhardt et al. 2021, Vina 1.2).

The receptor is rigid; the ligand's torsions, position and orientation are searched by
Vina's Monte Carlo and BFGS inside the box with the Vina (or Vinardo) scoring function.
Exhaustiveness 8 and 9 poses are Vina's defaults; the seed is fixed so a run repeats.
Poses come back as RDKit molecules with their hydrogens, through meeko.

A Vina score is an estimate in kcal/mol of the scoring function, not a measured binding
free energy: across benchmark sets it tracks measured affinities with a correlation of
roughly 0.5 and errors of about 2 kcal/mol.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

import numpy as np

from .prep import DockingError, Ligand, Receptor, _need

__all__ = ["Box", "Pose", "DockResult", "dock", "pose_rmsd"]


@dataclass(frozen=True)
class Box:
    center: tuple[float, float, float]
    size: tuple[float, float, float]
    source: str = "given"

    @classmethod
    def around(cls, coords: np.ndarray, *, padding: float = 8.0, minimum: float = 18.0,
               source: str = "") -> "Box":
        lo, hi = coords.min(axis=0), coords.max(axis=0)
        size = np.maximum(hi - lo + padding, minimum)
        centre = (lo + hi) / 2
        return cls(tuple(float(v) for v in centre), tuple(float(v) for v in size), source)


@dataclass
class Pose:
    rank: int
    score: float                     # kcal/mol (Vina's estimate)
    mol: Any                         # RDKit Mol of this pose, hydrogens included
    rmsd_to_reference: float | None = None


@dataclass
class DockResult:
    ligand: Ligand
    poses: list[Pose]
    scoring: str
    exhaustiveness: int
    seed: int
    seconds: float
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def best(self) -> Pose | None:
        return self.poses[0] if self.poses else None


def dock(receptor: Receptor, ligand: Ligand, box: Box, *, scoring: str = "vina",
         exhaustiveness: int = 8, n_poses: int = 9, seed: int = 42,
         energy_range: float = 3.0) -> DockResult:
    vina = _need("vina")
    meeko = _need("meeko")
    Chem = _need("rdkit.Chem")
    if scoring not in ("vina", "vinardo"):
        raise DockingError("scoring is 'vina' or 'vinardo'")
    v = vina.Vina(sf_name=scoring, seed=seed, verbosity=0)
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".pdbqt", delete=False) as fh:
        fh.write(receptor.pdbqt)                 # Vina reads a receptor from a file only
        rigid = fh.name
    try:
        v.set_receptor(rigid_pdbqt_filename=rigid)
    finally:
        Path(rigid).unlink(missing_ok=True)
    v.set_ligand_from_string(ligand.pdbqt)
    v.compute_vina_maps(center=list(box.center), box_size=list(box.size))
    t0 = time.time()
    v.dock(exhaustiveness=exhaustiveness, n_poses=n_poses, min_rmsd=1.0,
           max_evals=0)
    seconds = round(time.time() - t0, 2)
    energies = v.energies(n_poses=n_poses, energy_range=energy_range)
    text = v.poses(n_poses=n_poses, energy_range=energy_range)
    pmol = meeko.PDBQTMolecule(text, is_dlg=False, skip_typing=True)
    mols = meeko.RDKitMolCreate.from_pdbqt_mol(pmol)
    if not mols or mols[0] is None:
        raise DockingError(f"{ligand.name}: the poses could not be converted back to molecules")
    combined = mols[0]
    poses = []
    for k, conf in enumerate(combined.GetConformers()):
        single = Chem.Mol(combined)
        single.RemoveAllConformers()
        single.AddConformer(Chem.Conformer(conf), assignId=True)
        poses.append(Pose(rank=k + 1, score=round(float(energies[k][0]), 3), mol=single))
    return DockResult(ligand=ligand, poses=poses, scoring=scoring,
                      exhaustiveness=exhaustiveness, seed=seed, seconds=seconds,
                      detail={"box": {"center": box.center, "size": box.size,
                                      "source": box.source},
                              "vina_version": getattr(vina, "__version__", "")})


def pose_rmsd(pose_mol: Any, reference: Any) -> float:
    """Heavy-atom RMSD in place (no superposition), symmetry-aware (RDKit CalcRMS)."""
    Chem = _need("rdkit.Chem")
    rdMolAlign = _need("rdkit.Chem.rdMolAlign")
    probe = Chem.RemoveHs(pose_mol)
    ref = Chem.RemoveHs(reference)
    return round(float(rdMolAlign.CalcRMS(probe, ref)), 3)


def best_rmsd(result: DockResult, reference: Any, top: Sequence[int] = (1,)) -> float:
    return min(pose_rmsd(result.poses[k - 1].mol, reference) for k in top
               if k <= len(result.poses))
