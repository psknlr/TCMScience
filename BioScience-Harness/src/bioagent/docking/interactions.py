"""Protein–ligand contacts of a pose, by geometry.

Heavy atoms only, with plain distance cut-offs (the PLIP conventions, without its angle
criteria): hydrogen bonds between N/O donors and acceptors within 3.5 Å, salt bridges
between charged groups within 4.0 Å, hydrophobic contacts between carbons within
4.0 Å. Each contact names the residue, so a pose can be read against what is known of
the site. A list of contacts describes the pose; it is not evidence of binding.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

__all__ = ["Contact", "contacts", "receptor_atoms"]

POSITIVE = {("LYS", "NZ"), ("ARG", "NH1"), ("ARG", "NH2"), ("ARG", "NE"), ("HIS", "NE2"),
            ("HIS", "ND1")}
NEGATIVE = {("ASP", "OD1"), ("ASP", "OD2"), ("GLU", "OE1"), ("GLU", "OE2")}


@dataclass(frozen=True)
class Contact:
    kind: str                 # hbond | salt_bridge | hydrophobic
    residue: str              # e.g. "ASP189:A"
    protein_atom: str
    ligand_atom: int
    distance: float


def receptor_atoms(pdb_text: str) -> list[tuple[str, str, str, int, np.ndarray]]:
    """(element, atom name, residue name, residue number with chain, xyz) per heavy atom."""
    out = []
    for line in pdb_text.splitlines():
        if line[:6] not in ("ATOM  ", "HETATM"):
            continue
        element = (line[76:78].strip() if len(line) >= 78 else "") or line[12:16].strip()[:1]
        if element.upper() == "H":
            continue
        out.append((element.upper(), line[12:16].strip(), line[17:20].strip(),
                    f"{line[17:20].strip()}{int(line[22:26])}:{line[21]}",
                    np.array([float(line[30:38]), float(line[38:46]), float(line[46:54])])))
    return out


def contacts(pose_mol, receptor_pdb: str) -> list[Contact]:
    from rdkit import Chem
    from scipy.spatial import cKDTree
    lig = Chem.RemoveHs(pose_mol)
    pos = lig.GetConformer().GetPositions()
    atoms = receptor_atoms(receptor_pdb)
    xyz = np.array([a[4] for a in atoms])
    tree = cKDTree(xyz)
    found: list[Contact] = []
    charge = {a.GetIdx(): a.GetFormalCharge() for a in lig.GetAtoms()}
    for i, atom in enumerate(lig.GetAtoms()):
        el = atom.GetSymbol().upper()
        for j in tree.query_ball_point(pos[i], 4.0):
            pel, pname, pres, rid, pxyz = atoms[j]
            d = float(np.linalg.norm(pos[i] - pxyz))
            if el in ("N", "O") and pel in ("N", "O") and d <= 3.5:
                found.append(Contact("hbond", rid, pname, i, round(d, 2)))
            if charge.get(i, 0) > 0 and (pres, pname) in NEGATIVE:
                found.append(Contact("salt_bridge", rid, pname, i, round(d, 2)))
            elif charge.get(i, 0) < 0 and (pres, pname) in POSITIVE:
                found.append(Contact("salt_bridge", rid, pname, i, round(d, 2)))
            if el == "C" and pel == "C" and d <= 4.0:
                found.append(Contact("hydrophobic", rid, pname, i, round(d, 2)))
    return found
