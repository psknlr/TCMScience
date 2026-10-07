"""Receptor and ligand preparation for AutoDock Vina.

**Receptors.** A PDB file (or an entry fetched from RCSB) is reduced to the chosen protein
chains: waters and hetero groups are dropped unless named as cofactors, the first
alternate location is kept. meeko then matches each residue to its chemical template,
adds the polar hydrogens the template defines, assigns Gasteiger charges and AutoDock
atom types, and writes the rigid PDBQT. Residues meeko cannot match are reported, never
silently guessed.

**Ligands.** From SMILES (or an SDF), RDKit adds hydrogens, embeds a 3D conformer
(ETKDG v3, seeded) and relaxes it with MMFF94; meeko writes the PDBQT with its torsion
tree. The protonation state is the one given: a SMILES with a charged amine stays
charged, a neutral one stays neutral. No pKa model is applied, and the report says so.

**Co-crystal ligands** are read from the receptor file by residue name, their bond
orders taken from a SMILES template (the RCSB Chemical Component Dictionary's when none
is given), for defining the box and for the redocking check.
"""

from __future__ import annotations

import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

import numpy as np

__all__ = ["DockingError", "Receptor", "Ligand", "prepare_receptor", "prepare_ligand",
           "read_ligands", "cocrystal_ligand", "fetch_pdb", "ccd_smiles", "WATER"]

WATER = {"HOH", "WAT", "DOD", "H2O"}


class DockingError(RuntimeError):
    """An input docking cannot use as given."""


def _need(module: str):
    import importlib
    try:
        return importlib.import_module(module)
    except ImportError as exc:
        raise DockingError(f"docking needs {module} (pip install 'bioagent[docking]': rdkit, "
                           "meeko, vina)") from exc


@dataclass
class Receptor:
    pdbqt: str
    pdb: str                         # the cleaned protein the PDBQT was made from
    chains: list[str]
    residues: int
    cofactors: list[str]
    unmatched: list[str] = field(default_factory=list)
    source: dict[str, Any] = field(default_factory=dict)


@dataclass
class Ligand:
    name: str
    smiles: str
    pdbqt: str
    mol: Any                         # RDKit Mol with the embedded conformer
    heavy_atoms: int
    rotatable_bonds: int
    formal_charge: int


def fetch_pdb(code: str, *, timeout: float = 120.0) -> str:
    url = f"https://files.rcsb.org/download/{code.upper()}.pdb"
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")


def ccd_smiles(resname: str, *, timeout: float = 60.0) -> str:
    """The Chemical Component Dictionary's SMILES for a ligand code (RCSB)."""
    import json
    url = f"https://data.rcsb.org/rest/v1/core/chemcomp/{resname.upper()}"
    with urllib.request.urlopen(url, timeout=timeout) as r:
        doc = json.loads(r.read())
    desc = doc.get("rcsb_chem_comp_descriptor", {})
    smi = desc.get("smiles_stereo") or desc.get("smiles")
    if not smi:
        raise DockingError(f"the Chemical Component Dictionary has no SMILES for {resname}")
    return smi


def _clean(pdb_text: str, chains: Sequence[str] | None, cofactors: Sequence[str]
           ) -> tuple[str, list[str], int, list[str]]:
    keep_chains = set(chains) if chains else None
    cof = {c.upper() for c in cofactors}
    out, seen_res, found_chains, kept_cof = [], set(), [], set()
    seen_atom: set[tuple] = set()
    for line in pdb_text.splitlines():
        rec = line[:6]
        if rec.startswith("ENDMDL"):
            break
        if rec not in ("ATOM  ", "HETATM"):
            continue
        resname = line[17:20].strip()
        chain = line[21]
        if keep_chains is not None and chain not in keep_chains:
            continue
        if rec == "HETATM":
            if resname in WATER or resname.upper() not in cof:
                continue
            kept_cof.add(resname)
        alt = line[16]
        key = (chain, line[22:27], line[12:16])
        if alt not in (" ", "A") or key in seen_atom:
            continue
        seen_atom.add(key)
        out.append(line[:16] + " " + line[17:])
        if chain not in found_chains:
            found_chains.append(chain)
        seen_res.add((chain, line[22:27]))
    if not out:
        raise DockingError("no protein atoms remain after cleaning; check the chain ids")
    return "\n".join(out) + "\nEND\n", found_chains, len(seen_res), sorted(kept_cof)


def prepare_receptor(pdb_text: str, *, chains: Sequence[str] | None = None,
                     cofactors: Sequence[str] = (), source: dict[str, Any] | None = None
                     ) -> Receptor:
    meeko = _need("meeko")
    clean, found, n_res, kept = _clean(pdb_text, chains, cofactors)
    templates = meeko.ResidueChemTemplates.create_from_defaults()
    try:
        polymer = meeko.Polymer.from_pdb_string(clean, templates, meeko.MoleculePreparation(),
                                                allow_bad_res=True)
    except Exception as exc:                                   # noqa: BLE001
        raise DockingError(f"meeko could not build the receptor: {exc}") from exc
    rigid, _ = meeko.PDBQTWriterLegacy.write_from_polymer(polymer)
    unmatched = []
    for key, monomer in getattr(polymer, "monomers", {}).items():
        if getattr(monomer, "rdkit_mol", 1) is None:
            unmatched.append(str(key))
    return Receptor(pdbqt=rigid, pdb=clean, chains=found, residues=n_res, cofactors=kept,
                    unmatched=unmatched, source=dict(source or {}))


def prepare_ligand(name: str, smiles: str, *, seed: int = 7) -> Ligand:
    Chem = _need("rdkit.Chem")
    AllChem = _need("rdkit.Chem.AllChem")
    rdMolDescriptors = _need("rdkit.Chem.rdMolDescriptors")
    meeko = _need("meeko")
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        raise DockingError(f"{name}: {smiles!r} is not a valid SMILES")
    frags = Chem.GetMolFrags(mol, asMols=True)
    if len(frags) > 1:                       # keep the largest fragment (drop counter-ions)
        mol = max(frags, key=lambda m: m.GetNumHeavyAtoms())
    mol = Chem.AddHs(mol)
    params = AllChem.ETKDGv3()
    params.randomSeed = seed
    if AllChem.EmbedMolecule(mol, params) != 0:
        raise DockingError(f"{name}: RDKit could not embed a 3D conformer")
    if AllChem.MMFFHasAllMoleculeParams(mol):
        AllChem.MMFFOptimizeMolecule(mol, maxIters=2000)
    else:
        AllChem.UFFOptimizeMolecule(mol, maxIters=2000)
    setups = meeko.MoleculePreparation().prepare(mol)
    pdbqt, ok, err = meeko.PDBQTWriterLegacy.write_string(setups[0])
    if not ok:
        raise DockingError(f"{name}: meeko could not write the ligand: {err}")
    return Ligand(name=name, smiles=Chem.MolToSmiles(Chem.RemoveHs(mol)), pdbqt=pdbqt,
                  mol=mol, heavy_atoms=mol.GetNumHeavyAtoms(),
                  rotatable_bonds=rdMolDescriptors.CalcNumRotatableBonds(mol),
                  formal_charge=Chem.GetFormalCharge(mol))


def read_ligands(source: str | Path) -> list[tuple[str, str]]:
    """(name, SMILES) from a CSV/TSV (name,smiles), an SDF, a .smi file or one SMILES."""
    p = Path(str(source))
    if not p.is_file():
        return [("ligand1", str(source).strip())]
    name = p.name.lower()
    if name.endswith((".sdf", ".sdf.gz", ".mol")):
        Chem = _need("rdkit.Chem")
        out = []
        for k, mol in enumerate(Chem.SDMolSupplier(str(p))):
            if mol is None:
                raise DockingError(f"{p}: record {k + 1} could not be read")
            title = mol.GetProp("_Name") if mol.HasProp("_Name") and mol.GetProp("_Name") \
                else f"ligand{k + 1}"
            out.append((title, Chem.MolToSmiles(mol)))
        return out
    import csv
    text = p.read_text(encoding="utf-8")
    first = text.splitlines()[0] if text.strip() else ""
    if name.endswith(".smi") or ("," not in first and "\t" not in first.strip()):
        rows = [ln.split() for ln in text.splitlines() if ln.strip()]
        return [(r[1] if len(r) > 1 else f"ligand{k + 1}", r[0]) for k, r in enumerate(rows)]
    dialect = "excel-tab" if first.count("\t") > first.count(",") else "excel"
    out = []
    for k, row in enumerate(csv.DictReader(text.splitlines(), dialect=dialect)):
        row = {(key or "").strip().lower(): (v or "").strip() for key, v in row.items()}
        smi = row.get("smiles") or row.get("canonical_smiles")
        if not smi:
            raise DockingError(f"{p}: needs a 'smiles' column")
        out.append((row.get("name") or row.get("id") or f"ligand{k + 1}", smi))
    names = [n for n, _ in out]
    if len(set(names)) != len(names):
        raise DockingError(f"{p}: two ligands share a name")
    return out


def cocrystal_ligand(pdb_text: str, resname: str, *, smiles: str | None = None,
                     chain: str | None = None) -> tuple[Any, np.ndarray]:
    """(RDKit Mol with the crystal coordinates, heavy-atom coordinates)."""
    Chem = _need("rdkit.Chem")
    AllChem = _need("rdkit.Chem.AllChem")
    lines = [ln for ln in pdb_text.splitlines()
             if ln.startswith("HETATM") and ln[17:20].strip() == resname.upper()
             and (chain is None or ln[21] == chain)]
    if not lines:
        raise DockingError(f"the structure has no ligand {resname}")
    first = (lines[0][21], lines[0][22:27])
    lines = [ln for ln in lines if (ln[21], ln[22:27]) == first and ln[16] in (" ", "A")]
    block = "\n".join(ln[:16] + " " + ln[17:] for ln in lines) + "\nEND\n"
    mol = Chem.MolFromPDBBlock(block, removeHs=True, sanitize=False)
    if mol is None:
        raise DockingError(f"ligand {resname} could not be read")
    template = Chem.MolFromSmiles(smiles or ccd_smiles(resname))
    try:
        mol = AllChem.AssignBondOrdersFromTemplate(template, mol)
    except Exception as exc:                                   # noqa: BLE001
        raise DockingError(f"ligand {resname}: the SMILES template does not match its "
                           f"atoms ({exc})") from exc
    return mol, mol.GetConformer().GetPositions()
