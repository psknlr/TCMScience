"""Cheap sanity checks of a docked pose: finite coordinates, inside the box, no clash.

A pose can be wrong in ways no score shows. These checks catch the ones that are cheap to
see and impossible for a real complex:

* **finite**: every ligand coordinate is a number (a failed conversion back from PDBQT
  can leave NaN or nothing);
* **in the box**: every ligand heavy atom lies inside the search box Vina was given, to
  within ``BOX_TOLERANCE``; a pose outside it was not searched where the user asked;
* **no clash**: no ligand heavy atom is closer than ``CLASH_DISTANCE`` (2.2 Å) to a
  receptor heavy atom. Non-covalent contacts between heavy atoms are longer: a hydrogen
  bond puts its N and O about 2.6-3.2 Å apart, a carbon-carbon contact about 3.4 Å.
  Metal ions of a kept cofactor are left out, since a coordinating atom sits about
  2.0-2.2 Å from its metal.

They are sanity checks, not PoseBusters' test suite (no bond lengths, angles,
planarity, stereochemistry or internal energy), and passing them does not validate
binding: a pose that passes is possible, not shown to be right. Vina's ranking is kept
as Vina gives it; a pose that fails is reported, not dropped or moved down.

The core takes plain coordinates (numpy only); :func:`check_pose` reads an RDKit pose.
"""

from __future__ import annotations

from typing import Any, Sequence

import numpy as np

__all__ = ["CLASH_DISTANCE", "BOX_TOLERANCE", "METALS", "pose_checks", "check_pose",
           "receptor_heavy_atoms"]

CLASH_DISTANCE = 2.2            # Å, ligand heavy atom to receptor heavy atom
BOX_TOLERANCE = 0.5             # Å beyond the box's faces
METALS = frozenset({"LI", "NA", "K", "MG", "CA", "MN", "FE", "CO", "NI", "CU", "ZN", "CD",
                    "HG", "SR", "BA", "AL", "GA", "PT", "AU", "AG", "W", "MO", "V", "CR"})


def pose_checks(ligand_xyz: Any, receptor_xyz: Any, box_center: Sequence[float],
                box_size: Sequence[float], *, clash_distance: float = CLASH_DISTANCE,
                tolerance: float = BOX_TOLERANCE) -> dict[str, Any]:
    """The checks of one pose from its ligand heavy-atom coordinates (n x 3) and the
    receptor's heavy atoms (m x 3, metals already left out)."""
    lig = np.asarray(ligand_xyz, dtype=float).reshape(-1, 3)
    rec = np.asarray(receptor_xyz, dtype=float).reshape(-1, 3)
    out: dict[str, Any] = {"atoms": int(len(lig)), "finite": bool(len(lig)) and
                           bool(np.isfinite(lig).all())}
    if not out["finite"]:
        out.update({"outside_box": None, "min_receptor_distance": None, "clashes": None,
                    "passed": False, "failed": ["finite"]})
        return out
    centre, size = np.asarray(box_center, dtype=float), np.asarray(box_size, dtype=float)
    lo, hi = centre - size / 2 - tolerance, centre + size / 2 + tolerance
    out["outside_box"] = int((~((lig >= lo) & (lig <= hi)).all(axis=1)).sum())
    # only receptor atoms near the ligand can clash with it, and the nearest is among them
    near = rec[((rec >= lig.min(axis=0) - 8.0) & (rec <= lig.max(axis=0) + 8.0)).all(axis=1)]
    if len(rec):
        d = np.sqrt(((lig[:, None, :] - (near if len(near) else rec)[None, :, :]) ** 2)
                    .sum(axis=2))
        if d.min() > 8.0:                 # nothing near: the nearest may lie outside
            d = np.sqrt(((lig[:, None, :] - rec[None, :, :]) ** 2).sum(axis=2))
        out["min_receptor_distance"] = round(float(d.min()), 3)
        out["clashes"] = int((d < clash_distance).sum())
    else:
        out["min_receptor_distance"] = None
        out["clashes"] = 0
    failed = [name for name, bad in (("in_box", out["outside_box"] > 0),
                                     ("no_clash", out["clashes"] > 0)) if bad]
    out["passed"] = not failed
    out["failed"] = failed
    return out


def receptor_heavy_atoms(pdb_text: str) -> np.ndarray:
    """The receptor's heavy-atom coordinates, metal ions left out (m x 3)."""
    from .interactions import receptor_atoms
    xyz = [a[4] for a in receptor_atoms(pdb_text) if a[0] not in METALS]
    return np.array(xyz, dtype=float).reshape(-1, 3)


def check_pose(pose_mol: Any, receptor_xyz: np.ndarray, box: Any) -> dict[str, Any]:
    """The checks of one RDKit pose against a receptor (``receptor_heavy_atoms``) and the
    ``engine.Box`` it was docked in."""
    if pose_mol is None or pose_mol.GetNumConformers() == 0:
        return pose_checks(np.zeros((0, 3)), receptor_xyz, box.center, box.size)
    pos = pose_mol.GetConformer().GetPositions()
    heavy = [a.GetIdx() for a in pose_mol.GetAtoms() if a.GetAtomicNum() > 1]
    return pose_checks(pos[heavy], receptor_xyz, box.center, box.size)
