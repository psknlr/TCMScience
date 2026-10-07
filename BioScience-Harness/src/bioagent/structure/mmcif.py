"""mmCIF coordinates: the ``_atom_site`` table of the first model, as a ``pdbio.Structure``.

Boltz and Chai-1 write their models as mmCIF, with per-token or per-atom pLDDT x 100 in
``B_iso_or_equiv``. Validating such a model (does it hold the chains that were asked for,
with their sequences?) and reading its confidence needs the coordinates, and gemmi is not
a dependency of the harness. This reads exactly one category: the ``_atom_site`` loop,
author chain and residue numbering preferred (``auth_asym_id``, ``auth_seq_id``), the first
model and the first alternate location only. Any other category is skipped, and a file
with no ``_atom_site`` loop is refused rather than read as an empty structure.
"""

from __future__ import annotations

import re
from pathlib import Path

from .pdbio import Atom, Structure

__all__ = ["parse_mmcif", "read_mmcif"]

_TOKEN = re.compile(r"'(?:[^']|'(?=\S))*'|\"(?:[^\"]|\"(?=\S))*\"|\S+")


def _tokens(line: str) -> list[str]:
    out = []
    for tok in _TOKEN.findall(line):
        if len(tok) >= 2 and tok[0] == tok[-1] and tok[0] in "'\"":
            tok = tok[1:-1]
        out.append(tok)
    return out


def _blank(value: str) -> bool:
    return value in (".", "?", "")


def parse_mmcif(text: str, *, source: str = "") -> Structure:
    lines = text.splitlines()
    i, n = 0, len(lines)
    columns: list[str] = []
    while i < n:
        if lines[i].strip() == "loop_" and i + 1 < n and \
                lines[i + 1].strip().startswith("_atom_site."):
            i += 1
            while i < n and lines[i].strip().startswith("_atom_site."):
                columns.append(lines[i].strip().split(".", 1)[1].split()[0])
                i += 1
            break
        i += 1
    if not columns:
        raise ValueError(f"{source or 'the text'} has no _atom_site loop")
    col = {name: k for k, name in enumerate(columns)}
    for need in ("Cartn_x", "Cartn_y", "Cartn_z"):
        if need not in col:
            raise ValueError(f"{source or 'mmCIF'}: _atom_site has no {need}")
    rows: list[list[str]] = []
    pending: list[str] = []
    while i < n:
        raw = lines[i]
        stripped = raw.strip()
        if stripped.startswith(("_", "loop_", "data_")) or stripped == "#":
            break
        if stripped:
            pending.extend(_tokens(raw))
            while len(pending) >= len(columns):
                rows.append(pending[:len(columns)])
                pending = pending[len(columns):]
        i += 1
    if pending:
        raise ValueError(f"{source or 'mmCIF'}: a truncated _atom_site row")

    def get(row: list[str], *names: str, default: str = "") -> str:
        for name in names:
            k = col.get(name)
            if k is not None and not _blank(row[k]):
                return row[k]
        return default

    atoms: list[Atom] = []
    first_model = None
    seen: set[tuple[str, str, str, str]] = set()
    for row in rows:
        model = get(row, "pdbx_PDB_model_num", default="1")
        if first_model is None:
            first_model = model
        if model != first_model:
            continue
        chain = get(row, "auth_asym_id", "label_asym_id", default="A")
        resseq_text = get(row, "auth_seq_id", "label_seq_id", default="1")
        name = get(row, "label_atom_id", "auth_atom_id")
        icode = get(row, "pdbx_PDB_ins_code")
        key = (chain, resseq_text, icode, name)
        if key in seen:                       # a later alternate location of the same atom
            continue
        seen.add(key)
        try:
            resseq = int(resseq_text)
            xyz = (float(get(row, "Cartn_x")), float(get(row, "Cartn_y")),
                   float(get(row, "Cartn_z")))
            bfactor = float(get(row, "B_iso_or_equiv", default="0"))
            occupancy = float(get(row, "occupancy", default="1"))
        except ValueError as exc:
            raise ValueError(f"{source or 'mmCIF'}: an unreadable _atom_site row "
                             f"({exc})") from exc
        atoms.append(Atom(serial=len(atoms) + 1, name=name,
                          resname=get(row, "label_comp_id", "auth_comp_id"), chain=chain,
                          resseq=resseq, icode=icode, xyz=xyz, occupancy=occupancy,
                          bfactor=bfactor, element=get(row, "type_symbol", default=name[:1]),
                          hetero=get(row, "group_PDB") == "HETATM"))
    if not atoms:
        raise ValueError(f"{source or 'mmCIF'} holds no atoms")
    return Structure(atoms=atoms, source=source)


def read_mmcif(path: str | Path) -> Structure:
    """An mmCIF file, plain or gzipped."""
    import gzip

    p = Path(path)
    raw = p.read_bytes()
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    return parse_mmcif(raw.decode("utf-8", errors="replace"), source=str(p))
