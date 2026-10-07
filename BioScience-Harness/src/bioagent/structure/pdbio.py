"""PDB coordinate files: read the atoms of a model, write them back.

Fixed-column ATOM/HETATM records of the PDB format (v3.3). Only the first model of a
multi-model file is read, alternate locations other than the first are dropped, and
hydrogens are kept (they are skipped where a calculation needs heavy atoms only).
B-factors hold per-residue confidence in predicted models: pLDDT on a 0-100 scale
(AlphaFold, ColabFold) or 0-1 (the ESM Atlas service); :func:`plddt` reports 0-100.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

__all__ = ["Atom", "Structure", "read_pdb", "parse_pdb", "write_pdb", "THREE_TO_ONE"]

THREE_TO_ONE = {
    "ALA": "A", "ARG": "R", "ASN": "N", "ASP": "D", "CYS": "C", "GLN": "Q", "GLU": "E",
    "GLY": "G", "HIS": "H", "ILE": "I", "LEU": "L", "LYS": "K", "MET": "M", "PHE": "F",
    "PRO": "P", "SER": "S", "THR": "T", "TRP": "W", "TYR": "Y", "VAL": "V",
    "MSE": "M", "SEC": "U", "PYL": "O",
}


@dataclass(frozen=True)
class Atom:
    serial: int
    name: str
    resname: str
    chain: str
    resseq: int
    icode: str
    xyz: tuple[float, float, float]
    occupancy: float
    bfactor: float
    element: str
    hetero: bool = False


@dataclass
class Structure:
    atoms: list[Atom]
    title: str = ""
    source: str = ""
    remarks: list[str] = field(default_factory=list)

    def chains(self) -> list[str]:
        return list(dict.fromkeys(a.chain for a in self.atoms))

    def residues(self, chain: str | None = None) -> list[tuple[str, int, str, str]]:
        """(chain, resseq, icode, resname) of each protein residue, in file order."""
        seen: dict[tuple[str, int, str], str] = {}
        for a in self.atoms:
            if a.hetero and a.resname not in THREE_TO_ONE:
                continue
            if chain is not None and a.chain != chain:
                continue
            seen.setdefault((a.chain, a.resseq, a.icode), a.resname)
        return [(c, r, i, n) for (c, r, i), n in seen.items()]

    def sequence(self, chain: str | None = None) -> str:
        return "".join(THREE_TO_ONE.get(n, "X") for *_, n in self.residues(chain))

    def backbone(self, chain: str | None = None) -> dict[str, np.ndarray]:
        """Per residue: N, CA, C, O coordinates (NaN when missing) and CB (CA for Gly)."""
        res = self.residues(chain)
        index = {(c, r, i): k for k, (c, r, i, _) in enumerate(res)}
        out = {k: np.full((len(res), 3), np.nan) for k in ("N", "CA", "C", "O", "CB")}
        bf = np.full(len(res), np.nan)
        for a in self.atoms:
            k = index.get((a.chain, a.resseq, a.icode))
            if k is None or a.name not in out:
                continue
            out[a.name][k] = a.xyz
            if a.name == "CA":
                bf[k] = a.bfactor
        missing_cb = np.isnan(out["CB"]).any(axis=1)
        out["CB"][missing_cb] = out["CA"][missing_cb]
        out["bfactor"] = bf
        return out

    def plddt(self, chain: str | None = None) -> np.ndarray:
        """Per-residue confidence, 0-100 (from the CA B-factors)."""
        b = self.backbone(chain)["bfactor"]
        finite = b[np.isfinite(b)]
        return b * 100.0 if len(finite) and finite.max() <= 1.0 + 1e-9 else b


def parse_pdb(text: str, *, source: str = "") -> Structure:
    atoms: list[Atom] = []
    title, remarks = [], []
    seen_alt: set[tuple[str, int, str, str]] = set()
    for line in text.splitlines():
        record = line[:6].strip()
        if record == "ENDMDL":
            break
        if record == "TITLE":
            title.append(line[10:].strip())
        elif record == "REMARK":
            remarks.append(line[6:].rstrip())
        elif record in ("ATOM", "HETATM"):
            if len(line) < 54:
                raise ValueError(f"{source or 'PDB'}: a short coordinate record: {line!r}")
            name = line[12:16].strip()
            alt = line[16].strip()
            chain = line[21].strip() or "A"
            resseq = int(line[22:26])
            icode = line[26].strip()
            key = (chain, resseq, icode, name)
            if alt and key in seen_alt:
                continue
            seen_alt.add(key)
            element = line[76:78].strip() if len(line) >= 78 else ""
            atoms.append(Atom(
                serial=int(line[6:11]) if line[6:11].strip() else len(atoms) + 1,
                name=name, resname=line[17:20].strip(), chain=chain, resseq=resseq,
                icode=icode, xyz=(float(line[30:38]), float(line[38:46]), float(line[46:54])),
                occupancy=float(line[54:60]) if line[54:60].strip() else 1.0,
                bfactor=float(line[60:66]) if line[60:66].strip() else 0.0,
                element=element or name[:1], hetero=record == "HETATM"))
    if not atoms:
        raise ValueError(f"{source or 'the text'} holds no ATOM records")
    return Structure(atoms=atoms, title=" ".join(title), source=source, remarks=remarks)


def read_pdb(path: str | Path) -> Structure:
    """A PDB file, plain or gzipped."""
    import gzip
    p = Path(path)
    raw = p.read_bytes()
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    return parse_pdb(raw.decode("utf-8", errors="replace"), source=str(p))


def write_pdb(s: Structure, path: str | Path, *, bfactors: np.ndarray | None = None) -> Path:
    """The structure as PDB; ``bfactors`` per residue (e.g. pLDDT) replace the atoms'."""
    p = Path(path)
    res_index = {(c, r, i): k for k, (c, r, i, _) in enumerate(s.residues())}
    lines = []
    if s.title:
        lines.append(f"TITLE     {s.title[:70]}")
    for n, a in enumerate(s.atoms, start=1):
        b = a.bfactor
        if bfactors is not None:
            k = res_index.get((a.chain, a.resseq, a.icode))
            if k is not None:
                b = float(bfactors[k])
        name = a.name if len(a.name) == 4 else f" {a.name:<3}"
        lines.append(f"{'HETATM' if a.hetero else 'ATOM  '}{n:5d} {name}{' '}{a.resname:>3} "
                     f"{a.chain[:1]}{a.resseq:4d}{a.icode or ' ':1}   "
                     f"{a.xyz[0]:8.3f}{a.xyz[1]:8.3f}{a.xyz[2]:8.3f}{a.occupancy:6.2f}"
                     f"{b:6.2f}          {a.element:>2}")
    lines.append("END")
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p
