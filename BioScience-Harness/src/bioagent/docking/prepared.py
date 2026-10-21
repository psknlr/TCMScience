"""Docking prepared inputs: a rigid receptor and ligands as PDBQT, a box, AutoDock Vina 1.2.7.

    from bioagent.docking.prepared import PreparedDockConfig, run_prepared_docking
    run = run_prepared_docking("receptor.pdbqt", "ligands.pdbqt", PreparedDockConfig(),
                               "out/", reference_path="crystal_ligand.pdbqt")

The receptor and the ligands come prepared (protonation states, partial charges, AutoDock
atom types, the ligands' torsion trees), as PDBQT: the receptor one rigid model, the ligands
one ligand or several as MODEL/ENDMDL blocks. Nothing here prepares or changes them: they are
read, held to the records AutoDock Vina 1.2.7 reads (an input Vina would refuse is refused
here, with its line), and handed to Vina as they are.

1. **Site**: a box given as centre and size, or built around a reference ligand (the
   co-crystal ligand in its crystal pose, prepared like the ligands): 8 Å of padding, at
   least 18 Å a side, as ``pipeline`` builds one around a co-crystal ligand.
2. **Validation**: the reference ligand is docked back. The setup counts as validated when
   the top pose's heavy atoms lie within 2 Å RMSD of the reference's, compared in atom order
   (Vina writes a pose's atoms in its input's order). Without RDKit there is no symmetry
   matching, so this RMSD is an upper bound of the symmetry-aware one: it never flatters a
   pose. The check itself is easier than ``pipeline``'s: the reference is docked from its
   crystal conformer, whose bond lengths, angles and ring shapes Vina keeps, where
   ``pipeline`` docks a conformer it generated from SMILES.
3. **Docking** of each ligand; Vina's poses are written as it gives them, PDBQT.
4. **Pose checks** of every pose (``posecheck``), the best pose deciding, as in ``pipeline``.
5. **Report**: scores (with ligand efficiency), the validation, ``report.md`` /
   ``report.html``, ``limitations.md`` and ``run.json``.

This is the docking a browser runs (``tcmstudio.browser_jobs``): the same Python in Pyodide,
with Vina compiled to WebAssembly run by the page and its results read back here through
:class:`PrecomputedVina`; natively :class:`NativeVina` calls the vina package. So this module
imports only the standard library and numpy (and siblings that do the same); vina is
imported when a NativeVina is made. Either engine makes the same calls: maps of spacing
0.375 Å over the box, ``global_search(exhaustiveness, n_poses, min_rmsd 1.0, max_evals 0)``,
the poses and their energies within ``energy_range``; the energies are kept unrounded.

Every stage is recorded (``bioagent.runrecord``; the names are ``STAGES``) as ``pipeline``
records its own. The run is incomplete, and ``bioagent dock`` exits 1, when no ligand was
docked, when redocking the reference ligand misses 2 Å, or when a ligand's best pose fails
its checks. A docking score is the scoring function's estimate, not a measured affinity.
"""

from __future__ import annotations

import hashlib
import json
import math
import platform
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from .. import __version__ as _VERSION
from ..omics import svgplot
from ..report_html import render as _html
from ..runrecord import (LIMITATIONS_FILE, STAGES_FILE, StageRecorder, output_problems,
                         record_problems, stage_limitations, tsv_shape, write_limitations)
from . import posecheck
from .engine import Box
from .prep import DockingError, _need

__all__ = ["PIPELINE", "STAGES", "SCORES_HEADER", "REDOCK_RMSD", "LIMITATIONS", "AD_ELEMENT",
           "VINA_VERSION", "RESULT_SCHEMA", "REFERENCE_KEY", "SPACING", "MIN_RMSD",
           "MAX_EVALS", "RMSD_METHOD", "Receptor", "Ligand", "read_receptor", "read_ligands",
           "PreparedDockConfig", "site_box", "vina_params", "text_sha256", "vina_score",
           "pose_models", "pose_heavy_xyz", "atom_order_rmsd", "VinaResult", "VinaRefused",
           "NativeVina", "PrecomputedVina", "engine_record", "PreparedResult",
           "PreparedDockRun", "run_prepared_docking", "verify_run"]

PIPELINE = "bioagent.docking.prepared"
#: The stages of a run, in order. ``redock_validation`` is skipped without a reference
#: ligand (the setup is then not validated), and is a FAIL when the top pose misses
#: REDOCK_RMSD; ``pose_checks`` is a FAIL when a ligand's best pose fails them.
STAGES = ("inputs", "site", "redock_validation", "dock", "pose_checks", "report", "verify")
SCORES_HEADER = ("ligand", "best_score", "ligand_efficiency", "heavy_atoms", "torsions",
                 "poses", "pose_check", "min_receptor_distance")
REDOCK_RMSD = 2.0
#: The browser's engine is this release (studio/scripts/build_vina_wasm.py), and so is the
#: native one a run may use: the same scoring function, the same search.
VINA_VERSION = "1.2.7"
RESULT_SCHEMA = "tcmstudio.vina-result/1"
REFERENCE_KEY = "reference"
SPACING = 0.375                 # Å, Vina's grid spacing
MIN_RMSD = 1.0                  # Å, Vina's: poses closer than this count as one
MAX_EVALS = 0                   # Vina sets the Monte Carlo steps from the ligand
RMSD_METHOD = "atom order (upper bound of the symmetry-aware RMSD)"
#: What every result of this pipeline is not, one line each (``limitations.md``). The
#: first three are ``pipeline.LIMITATIONS``, word for word (that module needs scipy).
LIMITATIONS = (
    "A docking score is the scoring function's estimate in kcal/mol, not a measured binding "
    "affinity: across benchmark sets it tracks measured affinities with a correlation near "
    "0.5 and errors of about 2 kcal/mol.",
    "A pose is a hypothesis about how a molecule could sit in the site, docked into a rigid "
    "receptor with protonation states as given (no pKa model); a hit needs an assay.",
    "The pose checks (finite coordinates, heavy atoms inside the box, no ligand-receptor "
    "heavy-atom pair closer than 2.2 Å) are sanity checks, not a validation of binding.",
    "The inputs' preparation (protonation states, partial charges, AutoDock atom types, the "
    "ligands' torsion trees) is the user's: it was used as given and not checked, beyond the "
    "records AutoDock Vina 1.2.7 reads.",
    "This mode has no contact analysis and writes no SDF poses (both need RDKit): the poses "
    "are Vina's PDBQT.",
    "The redocking RMSD compares heavy atoms in atom order, without symmetry: an upper bound "
    "of the symmetry-aware RMSD, so the RMSD never flatters a pose, but a symmetric ligand "
    "can fail where a symmetry-aware RMSD would pass.",
    "The reference ligand is redocked from its crystal conformer, whose bond lengths, angles "
    "and ring shapes Vina keeps: an easier check than docking a conformer generated from "
    "SMILES, as the full pipeline does, most of all for a ligand with a puckered ring or a "
    "macrocycle.",
    "A seeded Vina search repeats on one engine and CPU, not across them: the Monte Carlo "
    "search amplifies last-bit differences in floating-point maths, so the same seed can "
    "give other poses in the browser (WebAssembly) and natively, or on another CPU. What was "
    "measured is a median: on the reference set, each ligand's median best score over seeds "
    "1-8 agreed within 0.10 kcal/mol between the engines. One run's best score can differ by "
    "more (0.15 kcal/mol for ibuprofen at seed 42; 0.20 kcal/mol between its own seeds 1-8 "
    "natively), so ligands a few tenths of a kcal/mol apart are not ranked by one run: dock "
    "them with several seeds.",
)
_UNLISTED = {"run.json", STAGES_FILE}

#: AutoDock atom type -> element, for the types AutoDock Vina 1.2.7 reads (atom_constants.h:
#: atom_kind_data, the equivalence Se -> S, and the metals outside AutoDock's set, which Vina
#: types as metal donors). Types are case-sensitive, as in Vina: "CL" or "NA" for sodium is
#: not "Cl" or "Na". None marks a pseudo-atom (G0-G3, the closure dummies of a macrocycle;
#: W, a water of hydrated docking), which is no heavy atom.
AD_ELEMENT: dict[str, str | None] = {
    "C": "C", "A": "C", "N": "N", "NA": "N", "O": "O", "OA": "O", "S": "S", "SA": "S",
    "P": "P", "H": "H", "HD": "H", "F": "F", "I": "I", "Cl": "Cl", "Br": "Br", "Si": "Si",
    "At": "At", "Se": "Se", "Mg": "Mg", "Mn": "Mn", "Zn": "Zn", "Ca": "Ca", "Fe": "Fe",
    "Cu": "Cu", "Na": "Na", "K": "K", "Hg": "Hg", "Co": "Co", "U": "U", "Cd": "Cd",
    "Ni": "Ni", "CG0": "C", "CG1": "C", "CG2": "C", "CG3": "C",
    "G0": None, "G1": None, "G2": None, "G3": None, "W": None,
}
_ATOM_RECORDS = ("ATOM  ", "HETATM")
_TREE_RECORDS = ("ROOT", "ENDROOT", "BRANCH", "ENDBRANCH", "TORSDOF", "BEGIN_RES", "END_RES")
_C_SPACE = " \t\n\r\x0b\x0c"    # what C's isspace() skips, as Vina's parser does
#: A number as boost::lexical_cast<double> reads one: no blank after it, no "_" (Python's
#: float() takes both), ASCII digits only.
_NUMBER = re.compile(r"[+-]?(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][+-]?[0-9]+)?")
_VINA_RESULT = re.compile(r"^REMARK VINA RESULT:\s*(\S+)")
_NAME_REMARK = re.compile(r"^REMARK\s+Name\s*=\s*(.*?)\s*$")
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")
_NAME_MAX = 100
_RESULT_KEY = re.compile(r"^(reference|ligand-\d+)$")


class VinaRefused(DockingError):
    """Vina refused a ligand (its own message): that ligand has no result, the run goes on."""


def text_sha256(text: str) -> str:
    """SHA-256 of a text's UTF-8 bytes: the digest of what Vina was given."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def vina_score(raw: float) -> float:
    """The score a run reports for Vina's raw total: rounded as vina's Python package rounds
    its energies (``np.around(..., 3)``), the value ``pipeline`` reports."""
    return float(np.around(float(raw), 3))


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _lf(text: str) -> str:
    """Line ends as \\n: Vina reads a line with a stray \\r as a record it does not know."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _safe(name: str) -> str:
    """A name usable as a file name: [A-Za-z0-9._-] kept, anything else _, no leading dot."""
    return _UNSAFE.sub("_", name)[:_NAME_MAX].lstrip(".")


# ------------------------------------------------------------------ reading PDBQT

def _atom(line: str, where: str) -> tuple[int, tuple[float, float, float], str]:
    """(serial, xyz, AutoDock type) of an ATOM/HETATM line, held to what Vina parses
    (parse_pdbqt.cpp): an atom number in columns 7-11, coordinates in 31-54 and a charge (if
    any) in 69-76, each with blanks before it but none after (boost::lexical_cast), and a
    type it knows from column 78 to the end of the line (the closure types CG0-CG3 of a
    macrocycle take three columns; anything after the type makes it another one)."""
    if len(line.rstrip(_C_SPACE)) < 78:
        raise DockingError(f"{where}: no AutoDock atom type from column 78: {line.strip()}")
    ad = line[77:].strip(_C_SPACE)
    if ad not in AD_ELEMENT:
        raise DockingError(f"{where}: atom type {ad!r} is not one AutoDock Vina 1.2.7 reads "
                           f"(types are case-sensitive): {line.strip()}")
    serial = line[6:11].lstrip(_C_SPACE)
    if not _uint(serial):
        raise DockingError(f"{where}: the atom number (columns 7-11) is not a number: "
                           f"{line.strip()}")
    fields = [line[30:38], line[38:46], line[46:54]]
    if line[68:76].strip(_C_SPACE):
        fields.append(line[68:76])
    if not all(_NUMBER.fullmatch(f.lstrip(_C_SPACE)) for f in fields):
        raise DockingError(f"{where}: the coordinates (columns 31-54) or the charge (69-76) "
                           f"are not numbers: {line.strip()}")
    xyz = (float(fields[0]), float(fields[1]), float(fields[2]))
    if not all(math.isfinite(v) for v in xyz):
        raise DockingError(f"{where}: a coordinate is not finite: {line.strip()}")
    return int(serial), xyz, ad


def _uint(text: str) -> bool:
    return text.isascii() and text.isdigit()


def _heavy(ad: str) -> bool:
    element = AD_ELEMENT[ad]
    return element is not None and element != "H"


@dataclass(eq=False)
class Receptor:
    """A rigid receptor as Vina reads it."""
    text: str                    # what Vina is given: the file's text, line ends as \n
    sha256: str                  # of ``text`` (UTF-8)
    atoms: int
    heavy_atoms: int             # neither hydrogen nor pseudo-atom; metal ions counted
    chains: list[str]
    residues: int
    heavy_xyz: np.ndarray        # heavy atoms without metal ions: the pose checks' receptor


def read_receptor(text: str) -> Receptor:
    """A rigid receptor from PDBQT: ATOM/HETATM records, and REMARK, TER, END and blank
    lines, which Vina passes over. A MODEL (several receptors) or a torsion tree (a ligand,
    or flexible residues) is refused, as is any other record Vina refuses in a rigid
    receptor (CONECT among them)."""
    text = _lf(text)
    atoms = heavy = 0
    xyz: list[tuple[float, float, float]] = []
    chains: list[str] = []
    residues: set[tuple[str, str]] = set()
    for k, line in enumerate(text.split("\n"), 1):
        where = f"receptor line {k}"
        words = line.split()
        if line == "" or line.startswith(("TER", "WARNING", "REMARK")):
            continue
        if line.startswith("MODEL"):
            raise DockingError(f"{where}: MODEL: a receptor of several models is not supported; "
                               "give one rigid model")
        if words and words[0] in _TREE_RECORDS:
            raise DockingError(f"{where}: {words[0]}: a torsion tree belongs to a ligand or to "
                               "flexible residues; the receptor must be one rigid model")
        if line.startswith("END"):
            continue
        if line[:6] not in _ATOM_RECORDS:
            raise DockingError(f"{where}: AutoDock Vina 1.2.7 does not read this record in a "
                               f"rigid receptor: {line.strip()[:80]}")
        _, pos, ad = _atom(line, where)
        atoms += 1
        element = AD_ELEMENT[ad]
        if _heavy(ad):
            heavy += 1
            if element.upper() not in posecheck.METALS:
                xyz.append(pos)
        chain = line[21:22].strip()
        if chain not in chains:
            chains.append(chain)
        residues.add((chain, line[22:27]))
    if not atoms:
        raise DockingError("the receptor has no atoms (ATOM or HETATM records)")
    return Receptor(text=text, sha256=text_sha256(text), atoms=atoms, heavy_atoms=heavy,
                    chains=chains, residues=len(residues),
                    heavy_xyz=np.array(xyz, dtype=float).reshape(-1, 3))


@dataclass(eq=False)
class Ligand:
    """One prepared ligand as Vina reads it."""
    index: int                   # 0-based, in the file's order
    name: str                    # unique in the file, usable as a file name
    text: str                    # what Vina is given (no MODEL/ENDMDL; \n line ends)
    sha256: str                  # of ``text`` (UTF-8)
    heavy_atoms: int
    torsions: int                # TORSDOF
    heavy_xyz: np.ndarray        # the heavy atoms in atom order

    @property
    def key(self) -> str:
        """Its engine call's name (``PrecomputedVina``): ``ligand-<index>``."""
        return f"ligand-{self.index}"


def _ligand_block(lines: Sequence[str], first: int) -> tuple[str | None, int, list]:
    """(name from ``REMARK  Name =``, TORSDOF, [(AutoDock type, xyz)]) of one ligand,
    held to the grammar Vina reads: REMARK/WARNING/blank lines anywhere, ROOT, its atoms,
    ENDROOT, then BRANCH a b ... ENDBRANCH a b (each opened at an atom of the part around
    it and holding atom b) and TORSDOF once, outside any branch."""
    name: str | None = None
    state = "head"
    torsdof: int | None = None
    atoms: list[tuple[str, tuple[float, float, float]]] = []
    root: list[int] = []
    stack: list[tuple[int, int, list[int]]] = []          # (a, b, serials of that branch)
    for off, line in enumerate(lines):
        where = f"line {first + off}"
        if line == "" or line.startswith(("WARNING", "REMARK")):
            m = _NAME_REMARK.match(line)
            if name is None and m and m.group(1):
                name = m.group(1)
            continue
        if state == "head":
            if not line.startswith("ROOT"):
                raise DockingError(f"{where}: a ligand begins with ROOT (after any REMARK "
                                   f"lines), not: {line.strip()[:80]}")
            state = "root"
        elif line[:6] in _ATOM_RECORDS:
            if state == "tree" and not stack:
                raise DockingError(f"{where}: an atom outside ROOT and every BRANCH: "
                                   f"{line.strip()}")
            serial, xyz, ad = _atom(line, where)
            atoms.append((ad, xyz))
            (stack[-1][2] if stack else root).append(serial)
        elif state == "root":
            if not line.startswith("ENDROOT"):
                raise DockingError(f"{where}: only atoms belong between ROOT and ENDROOT, "
                                   f"not: {line.strip()[:80]}")
            state = "tree"
        elif line.startswith(("BRANCH", "ENDBRANCH")):
            word = "ENDBRANCH" if line.startswith("ENDBRANCH") else "BRANCH"
            nums = line[len(word):].split()[:2]
            if len(nums) != 2 or not all(_uint(n) for n in nums):
                raise DockingError(f"{where}: {word} takes two atom numbers: {line.strip()}")
            a, b = int(nums[0]), int(nums[1])
            if word == "BRANCH":
                if a not in (stack[-1][2] if stack else root):
                    raise DockingError(f"{where}: atom {a} of this BRANCH is not in the part "
                                       f"it branches from: {line.strip()}")
                stack.append((a, b, []))
            else:
                if not stack or stack[-1][:2] != (a, b):
                    raise DockingError(f"{where}: this ENDBRANCH closes no open BRANCH {a} {b}")
                if b not in stack[-1][2]:
                    raise DockingError(f"{where}: atom {b} is not in the branch {a} {b}")
                stack.pop()
        elif line.startswith("TORSDOF"):
            if stack:
                raise DockingError(f"{where}: TORSDOF inside a BRANCH")
            if torsdof is not None:
                raise DockingError(f"{where}: a second TORSDOF")
            value = line[len("TORSDOF"):].split()[:1]
            if not value or not _uint(value[0]):
                raise DockingError(f"{where}: TORSDOF takes a number: {line.strip()}")
            torsdof = int(value[0])
        else:
            raise DockingError(f"{where}: AutoDock Vina 1.2.7 does not read this record in a "
                               f"ligand: {line.strip()[:80]}")
    if state == "head":
        raise DockingError(f"line {first}: a ligand without ROOT")
    if state == "root":
        raise DockingError(f"line {first}: ROOT without ENDROOT")
    if stack:
        raise DockingError(f"line {first}: BRANCH {stack[-1][0]} {stack[-1][1]} has no ENDBRANCH")
    if torsdof is None:
        raise DockingError(f"line {first}: a ligand without TORSDOF")
    if not atoms:
        raise DockingError(f"line {first}: a ligand without atoms")
    return name, torsdof, atoms


def read_ligands(text: str, default_name: str = "ligand") -> list[Ligand]:
    """The ligands of a PDBQT: one, or several as MODEL/ENDMDL blocks (each block a ligand;
    the MODEL and ENDMDL lines are not given to Vina; between blocks only blank, REMARK,
    WARNING and END lines). A ligand is named by its ``REMARK  Name = <name>`` line when it
    has one, else ``default_name`` (one ligand) or ``<default_name>_<k>`` (k from 1); names
    are made file-safe and unique (``_2``, ``_3``...) without regard to case, for they name
    files (``poses/<name>.pdbqt``), and on macOS or Windows "Ben" and "BEN" are one file. Its
    text for Vina has \\n line ends and ends with \\n."""
    lines = _lf(text).split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    blocks: list[tuple[int, list[str]]] = []
    if any(line.startswith("MODEL") for line in lines):
        current: tuple[int, list[str]] | None = None
        for k, line in enumerate(lines, 1):
            if line.startswith("MODEL"):
                if current is not None:
                    raise DockingError(f"line {k}: MODEL inside a MODEL block (no ENDMDL "
                                       "before it)")
                current = (k + 1, [])
            elif line.startswith("ENDMDL"):
                if current is None:
                    raise DockingError(f"line {k}: ENDMDL without MODEL")
                blocks.append(current)
                current = None
            elif current is not None:
                current[1].append(line)
            elif not (line.strip() in ("", "END") or line.startswith(("REMARK", "WARNING"))):
                raise DockingError(f"line {k}: outside every MODEL block: {line.strip()[:80]}")
        if current is not None:
            raise DockingError(f"line {current[0] - 1}: MODEL without ENDMDL")
    else:
        blocks = [(1, lines)]
    if not blocks:
        raise DockingError("no ligand: the file holds no MODEL block")
    base = _safe(default_name) or "ligand"
    taken: set[str] = set()
    out: list[Ligand] = []
    for k, (first, block) in enumerate(blocks):
        given, torsdof, atoms = _ligand_block(block, first)
        name = _safe(given or "") or (base if len(blocks) == 1 else f"{base}_{k + 1}")
        unique, n = name, 1
        while unique.casefold() in taken:
            n += 1
            unique = f"{name}_{n}"
        taken.add(unique.casefold())
        body = "\n".join(block) + "\n"
        heavy = [xyz for ad, xyz in atoms if _heavy(ad)]
        out.append(Ligand(index=k, name=unique, text=body, sha256=text_sha256(body),
                          heavy_atoms=len(heavy), torsions=torsdof,
                          heavy_xyz=np.array(heavy, dtype=float).reshape(-1, 3)))
    return out


def pose_models(text: str) -> list[list[str]]:
    """The poses of Vina's output, one list of lines per MODEL (all of it when it has no
    MODEL record)."""
    lines = _lf(text).split("\n")
    if not any(line.startswith("MODEL") for line in lines):
        return [[line for line in lines if line]]
    models: list[list[str]] = []
    current: list[str] | None = None
    for line in lines:
        if line.startswith("MODEL"):
            current = []
        elif line.startswith("ENDMDL"):
            if current is not None:
                models.append(current)
            current = None
        elif current is not None:
            current.append(line)
    return models


def pose_heavy_xyz(lines: Sequence[str]) -> np.ndarray:
    """The heavy atoms of one pose, in atom order (their AutoDock types decide)."""
    xyz = []
    for k, line in enumerate(lines, 1):
        if line[:6] in _ATOM_RECORDS:
            _, pos, ad = _atom(line, f"pose line {k}")
            if _heavy(ad):
                xyz.append(pos)
    return np.array(xyz, dtype=float).reshape(-1, 3)


def atom_order_rmsd(pose: Any, reference: Any) -> float | None:
    """Heavy-atom RMSD in place (no superposition), atom i to atom i; None when the two do
    not have the same number of atoms. Without symmetry it bounds the symmetry-aware RMSD
    from above."""
    a = np.asarray(pose, dtype=float).reshape(-1, 3)
    b = np.asarray(reference, dtype=float).reshape(-1, 3)
    if len(a) != len(b) or not len(a):
        return None
    return round(float(np.sqrt(((a - b) ** 2).sum(axis=1).mean())), 3)


# ------------------------------------------------------------------ configuration and site

def _triple(name: str, value: Any, *, positive: bool = False) -> tuple[float, float, float]:
    try:
        if isinstance(value, (str, bytes)):
            raise TypeError(name)
        parts = tuple(float(v) for v in value)
    except (TypeError, ValueError):
        raise DockingError(f"{name}: three numbers (x, y, z; Å)") from None
    if len(parts) != 3 or any(isinstance(v, bool) for v in value) or \
            not all(math.isfinite(v) for v in parts):
        raise DockingError(f"{name}: three finite numbers (x, y, z; Å)")
    if positive and min(parts) <= 0:
        raise DockingError(f"{name}: every side of the box must be longer than 0 Å")
    return parts[0], parts[1], parts[2]


def _int(name: str, value: Any, lo: int, hi: int, why: str = "") -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
        raise DockingError(f"{name}: an integer from {lo} to {hi}{why}")
    return value


@dataclass(frozen=True)
class PreparedDockConfig:
    """The run's settings, checked as they are made: the bounds of the Studio's
    ``pipeline.dock`` (scoring vina or vinardo, exhaustiveness 1-64, 1-20 poses), and a
    seed from 1, since Vina takes seed 0 as "pick one at random"."""
    center: tuple[float, float, float] | None = None
    size: tuple[float, float, float] | None = None
    scoring: str = "vina"
    exhaustiveness: int = 8
    n_poses: int = 9
    seed: int = 42
    energy_range: float = 3.0      # kcal/mol above the best pose

    def __post_init__(self) -> None:
        if self.center is not None:
            object.__setattr__(self, "center", _triple("center", self.center))
        if self.size is not None:
            object.__setattr__(self, "size", _triple("size", self.size, positive=True))
        if self.scoring not in ("vina", "vinardo"):
            raise DockingError("scoring: 'vina' or 'vinardo'")
        _int("exhaustiveness", self.exhaustiveness, 1, 64)
        _int("n_poses", self.n_poses, 1, 20)
        _int("seed", self.seed, 1, 2**31 - 1, " (0 would let Vina pick a random seed)")
        er = self.energy_range
        if isinstance(er, bool) or not isinstance(er, (int, float)) or not math.isfinite(er) \
                or er <= 0:
            raise DockingError("energy_range: a number of kcal/mol above 0")
        object.__setattr__(self, "energy_range", float(er))

    def as_dict(self) -> dict[str, Any]:
        doc = asdict(self)
        for key in ("center", "size"):
            if doc[key] is not None:
                doc[key] = list(doc[key])
        return doc


def site_box(config: PreparedDockConfig, reference: Ligand | None = None) -> Box:
    """The search box: ``center`` and ``size`` as given; else around the reference ligand's
    heavy atoms (8 Å of padding, at least 18 Å a side, as ``pipeline`` builds one around a
    co-crystal ligand), a given ``size`` taking the place of the built one. A centre without
    a size, or neither box nor reference, is refused."""
    if config.center is not None and config.size is not None:
        return Box(config.center, config.size, "given")
    if config.center is not None:
        raise DockingError("center needs size (or give neither and a reference ligand, to "
                           "build the box around it)")
    if reference is None:
        raise DockingError("name the site: a box (center and size) or a reference ligand")
    if not reference.heavy_atoms:
        raise DockingError(f"the reference ligand {reference.name} has no heavy atom to build "
                           "the box around")
    box = Box.around(reference.heavy_xyz, source="reference ligand")
    if config.size is not None:
        box = Box(box.center, config.size, box.source)
    return box


def vina_params(config: PreparedDockConfig, box: Box) -> dict[str, Any]:
    """Every parameter of one Vina run, as a result records them (``RESULT_SCHEMA``)."""
    return _params(config.scoring, config.exhaustiveness, config.n_poses, config.seed,
                   config.energy_range, box)


def _params(scoring: str, exhaustiveness: int, n_poses: int, seed: int, energy_range: float,
            box: Box) -> dict[str, Any]:
    return {"scoring": scoring, "exhaustiveness": exhaustiveness, "n_poses": n_poses,
            "seed": seed, "energy_range": energy_range,
            "center": [float(v) for v in box.center], "size": [float(v) for v in box.size],
            "spacing": SPACING, "min_rmsd": MIN_RMSD, "max_evals": MAX_EVALS}


# ------------------------------------------------------------------ engines

@dataclass
class VinaResult:
    energies: list[list[float]]  # per pose: total, inter, intra, torsions, intra of the best
    poses: str                   # Vina's PDBQT, one MODEL per pose
    seconds: float


def engine_record(engine: Any) -> dict[str, Any]:
    """{name, version, build[, wasm_sha256]} of an engine, as run.json records it."""
    doc = {"name": engine.name, "version": engine.version, "build": engine.build}
    if getattr(engine, "wasm_sha256", None):
        doc["wasm_sha256"] = engine.wasm_sha256
    return doc


class NativeVina:
    """AutoDock Vina 1.2.7 through its Python package, on this machine's CPUs (all of them:
    Vina draws every Monte Carlo task's seed before the run and merges the tasks in order,
    so the thread count does not change the result)."""

    name = "AutoDock Vina"
    build = "native"
    wasm_sha256 = None

    def __init__(self) -> None:
        self._vina = _need("vina")
        self.version = str(getattr(self._vina, "__version__", ""))
        if self.version != VINA_VERSION:
            raise DockingError(f"prepared docking runs AutoDock Vina {VINA_VERSION} (the "
                               f"browser's engine is that release); this is vina "
                               f"{self.version or 'of an unknown version'} (pip install "
                               f"vina=={VINA_VERSION})")

    def dock(self, receptor_text: str, ligand_text: str, box: Box, *, scoring: str,
             exhaustiveness: int, n_poses: int, seed: int, energy_range: float,
             key: str | None = None) -> VinaResult:
        """One Vina run; ``key`` names it for an engine that looks results up and is not
        used here. Vina's refusal of an input is a :class:`VinaRefused`."""
        import tempfile
        try:
            v = self._vina.Vina(sf_name=scoring, seed=seed, verbosity=0)
            with tempfile.TemporaryDirectory() as tmp:
                rigid = Path(tmp) / "receptor.pdbqt"
                rigid.write_bytes(receptor_text.encode("utf-8"))   # Vina reads a file only
                v.set_receptor(rigid_pdbqt_filename=str(rigid))
            v.set_ligand_from_string(ligand_text)
            v.compute_vina_maps(center=list(box.center), box_size=list(box.size),
                                spacing=SPACING)
            t0 = time.perf_counter()
            # the raw calls: vina's Python wrapper rounds energies to 3 decimals
            v._vina.global_search(exhaustiveness, n_poses, MIN_RMSD, MAX_EVALS)
            seconds = round(time.perf_counter() - t0, 3)
            energies = [[float(x) for x in row]
                        for row in v._vina.get_poses_energies(n_poses, energy_range)]
            poses = v._vina.get_poses(n_poses, energy_range)
        except (TypeError, RuntimeError, ValueError) as exc:     # Vina's errors, through SWIG
            raise VinaRefused(" ".join(str(exc).split()) or type(exc).__name__) from exc
        return VinaResult(energies=energies, poses=poses, seconds=seconds)


class PrecomputedVina:
    """What another AutoDock Vina 1.2.7 computed: one ``<key>.json`` per run in
    ``results_dir`` (``RESULT_SCHEMA``; a browser's dock worker writes them), ``key`` being
    ``reference`` or ``ligand-<index>``. ``dock`` gives back the result of exactly the run
    asked for, the same receptor and ligand text (SHA-256) and the same parameters (numbers
    compared exactly), or refuses; a ligand that Vina refused there is a
    :class:`VinaRefused` with Vina's message. Every result must come from one engine, whose
    name, version and build (and the WebAssembly's SHA-256) this one reports."""

    def __init__(self, results_dir: str | Path) -> None:
        self.directory = Path(results_dir)
        if not self.directory.is_dir():
            raise DockingError(f"{self.directory} is not a directory of Vina results")
        engines = set()
        for path in sorted(self.directory.glob("*.json")):
            if _RESULT_KEY.match(path.stem):
                engines.add(self._engine(self._read(path), path))
        if not engines:
            raise DockingError(f"{self.directory} holds no Vina result (reference.json, "
                               "ligand-<index>.json)")
        if len(engines) > 1:
            raise DockingError(f"the Vina results in {self.directory} come from different "
                               f"engines: {sorted(engines, key=str)}")
        self.name, self.version, self.build, self.wasm_sha256 = engines.pop()
        if self.version != VINA_VERSION:
            raise DockingError(f"the Vina results in {self.directory} are from AutoDock Vina "
                               f"{self.version}, not {VINA_VERSION}")

    @staticmethod
    def _read(path: Path) -> dict[str, Any]:
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise DockingError(f"{path.name} could not be read: {exc}") from exc
        if not isinstance(doc, dict) or doc.get("schema") != RESULT_SCHEMA:
            raise DockingError(f"{path.name} is not a Vina result ({RESULT_SCHEMA})")
        return doc

    @staticmethod
    def _engine(doc: Mapping[str, Any], path: Path) -> tuple[str, str, str, str | None]:
        e = doc.get("engine")
        if not isinstance(e, dict) or not all(isinstance(e.get(k), str) and e.get(k)
                                              for k in ("name", "version", "build")):
            raise DockingError(f"{path.name}: its engine is not named (name, version, build)")
        wasm = e.get("wasm_sha256")
        if e["build"] == "wasm" and not (isinstance(wasm, str) and
                                         re.fullmatch(r"[0-9a-f]{64}", wasm)):
            raise DockingError(f"{path.name}: a WebAssembly build without its SHA-256")
        return e["name"], e["version"], e["build"], wasm if isinstance(wasm, str) else None

    def dock(self, receptor_text: str, ligand_text: str, box: Box, *, scoring: str,
             exhaustiveness: int, n_poses: int, seed: int, energy_range: float,
             key: str) -> VinaResult:
        path = self.directory / f"{key}.json"
        if not _RESULT_KEY.match(key) or not path.is_file():
            raise DockingError(f"no Vina result for {key} in {self.directory}: the run was not "
                               "computed, or not written")
        doc = self._read(path)
        if doc.get("key") != key:
            raise DockingError(f"{path.name} holds the result of {doc.get('key')!r}, not {key}")
        if doc.get("receptor_sha256") != text_sha256(receptor_text):
            raise DockingError(f"{path.name} was computed on another receptor (SHA-256 "
                               f"{str(doc.get('receptor_sha256'))[:12]})")
        if doc.get("ligand_sha256") != text_sha256(ligand_text):
            raise DockingError(f"{path.name} was computed on another ligand (SHA-256 "
                               f"{str(doc.get('ligand_sha256'))[:12]})")
        want = _params(scoring, exhaustiveness, n_poses, seed, energy_range, box)
        got = doc.get("params")
        differ = (sorted(want) if not isinstance(got, dict) else
                  sorted(set(want) ^ set(got)) +
                  [k for k in sorted(set(want) & set(got)) if not _same(got[k], want[k])])
        if differ:
            raise DockingError(f"{path.name} was computed with other parameters: "
                               f"{', '.join(differ)}")
        if self._engine(doc, path) != (self.name, self.version, self.build, self.wasm_sha256):
            raise DockingError(f"{path.name} comes from another engine than the other results")
        if "error" in doc:
            raise VinaRefused(str(doc["error"]))
        energies, poses, seconds = doc.get("energies"), doc.get("poses"), doc.get("seconds")
        if not (isinstance(energies, list) and all(
                isinstance(row, list) and len(row) == 5 and all(_number(x) for x in row)
                for row in energies)):
            raise DockingError(f"{path.name}: energies are not rows of five numbers")
        if not isinstance(poses, str) or not _number(seconds) or seconds < 0:
            raise DockingError(f"{path.name}: poses (text) or seconds missing")
        return VinaResult(energies=[[float(x) for x in row] for row in energies], poses=poses,
                          seconds=float(seconds))


def _number(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _same(got: Any, want: Any) -> bool:
    """Exactly equal: strings, numbers (an integer and a float of the same value are one
    number: JSON writes 3.0 as 3) and lists of them; no tolerance."""
    if isinstance(want, list):
        return isinstance(got, list) and len(got) == len(want) and \
            all(_same(g, w) for g, w in zip(got, want))
    if isinstance(want, str):
        return got == want
    return _number(got) and got == want


# ------------------------------------------------------------------ the run

@dataclass(eq=False)
class PreparedResult:
    ligand: Ligand
    energies: list[list[float]]  # raw, as Vina gives them
    poses: str                   # Vina's PDBQT
    seconds: float
    models: list[list[str]] = field(default_factory=list)

    @property
    def best_raw(self) -> float:
        return self.energies[0][0]

    @property
    def best_score(self) -> float:
        return vina_score(self.best_raw)

    @property
    def scores(self) -> list[float]:
        return [vina_score(row[0]) for row in self.energies]


@dataclass(eq=False)
class PreparedDockRun:
    out_dir: Path
    receptor: Receptor
    ligands: list[Ligand]
    reference: Ligand | None
    box: Box
    engine: dict[str, Any]
    results: list[PreparedResult]
    validation: dict[str, Any] | None
    failures: dict[str, str]
    warnings: list[str]
    manifest: dict[str, Any] = field(default_factory=dict)
    pose_checks: dict[str, dict[str, Any]] = field(default_factory=dict)
    stages: list[dict[str, Any]] = field(default_factory=list)
    complete: bool = False
    limitations: list[str] = field(default_factory=list)

    @property
    def validated(self) -> bool:
        return bool(self.validation and self.validation["passed"])


def _read_text(path: str | Path, inputs: dict[str, str]) -> str:
    p = Path(path)
    data = p.read_bytes()
    inputs[str(path)] = hashlib.sha256(data).hexdigest()
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        raise DockingError(f"{p.name}: not a text file (UTF-8)") from None


def _dock(engine: Any, receptor: Receptor, ligand: Ligand, box: Box,
          config: PreparedDockConfig, key: str) -> PreparedResult:
    """One engine run, held to the ligand it was asked for: every pose has the ligand's
    heavy atoms, there is one pose per energy row, and each pose's score as Vina wrote it
    (``REMARK VINA RESULT:``, three decimals) is its row's total energy. A result that is
    not is refused (an engine or results that cannot be trusted), not counted as a failed
    ligand."""
    res = engine.dock(receptor.text, ligand.text, box, scoring=config.scoring,
                      exhaustiveness=config.exhaustiveness, n_poses=config.n_poses,
                      seed=config.seed, energy_range=config.energy_range, key=key)
    if not res.energies:
        raise VinaRefused("Vina returned no pose")
    models = pose_models(res.poses)
    if len(models) != len(res.energies):
        raise DockingError(f"{ligand.name}: {len(models)} poses, where Vina's result has "
                           f"{len(res.energies)} energies")
    for k, (lines, row) in enumerate(zip(models, res.energies), 1):
        if len(pose_heavy_xyz(lines)) != ligand.heavy_atoms:
            raise DockingError(f"{ligand.name}: pose {k} does not have the ligand's "
                               f"{ligand.heavy_atoms} heavy atoms")
        written = _remark_score(lines)
        # Vina prints the total with %.3f: within half a unit of the third decimal of it
        if written is None or not abs(written - row[0]) <= 0.0005 + 1e-9:
            raise DockingError(f"{ligand.name}: pose {k}'s score as Vina wrote it ("
                               f"{'none' if written is None else written}) is not its energy "
                               f"{row[0]!r}")
    return PreparedResult(ligand=ligand, energies=[list(r) for r in res.energies],
                          poses=res.poses, seconds=res.seconds, models=models)


def _remark_score(lines: Sequence[str]) -> float | None:
    """The score of a pose as Vina writes it (its first ``REMARK VINA RESULT:`` number), or
    None when the pose has no such line."""
    for line in lines:
        m = _VINA_RESULT.match(line)
        if m:
            try:
                return float(m.group(1))
            except ValueError:
                return None
    return None


def _fmt(values: Sequence[float], spec: str) -> str:
    return ", ".join(format(v, spec) for v in values)


def run_prepared_docking(receptor_path: str | Path, ligands_path: str | Path,
                         config: PreparedDockConfig, out_dir: str | Path, *,
                         engine: Any = None,
                         reference_path: str | Path | None = None) -> PreparedDockRun:
    """Every stage, recorded as it runs (``runrecord.StageRecorder``). ``engine`` is a
    :class:`NativeVina` unless given (a :class:`PrecomputedVina` reads what a browser
    computed). The run is complete only when at least one ligand was docked, every best
    pose passed its checks and, with a reference ligand, redocking it came within
    ``REDOCK_RMSD``; ``PreparedDockRun.complete`` says which."""
    engine = engine if engine is not None else NativeVina()
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    rec = StageRecorder(out, PIPELINE, plan=STAGES)
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    warnings: list[str] = []
    inputs: dict[str, str] = {}

    with rec.stage("inputs", engine=PIPELINE, version=_VERSION) as st:
        receptor = read_receptor(_read_text(receptor_path, inputs))
        ligands = read_ligands(_read_text(ligands_path, inputs), Path(ligands_path).stem)
        reference = None
        if reference_path is not None:
            found = read_ligands(_read_text(reference_path, inputs), Path(reference_path).stem)
            if len(found) != 1:
                raise DockingError(f"{Path(reference_path).name}: a reference is one ligand; "
                                   f"this file holds {len(found)}")
            reference = found[0]
        st.reason = (f"receptor: {receptor.atoms} atoms ({receptor.heavy_atoms} heavy), chains "
                     f"{', '.join(receptor.chains) or '-'}, {receptor.residues} residues; "
                     f"{len(ligands)} ligands"
                     + (f"; reference ligand {reference.name}" if reference else "")
                     + "; read as prepared (PDBQT), not prepared here")

    with rec.stage("site", engine=PIPELINE, version=_VERSION) as st:
        box = site_box(config, reference)
        st.reason = (f"{box.source}: centre ({_fmt(box.center, '.1f')}), size "
                     f"({_fmt(box.size, '.0f')}) Å")

    validation = None
    redock: PreparedResult | None = None
    if reference is not None:
        with rec.stage("redock_validation", engine=engine.name, version=engine.version) as st:
            validation = {"ligand": reference.name, "top_pose_rmsd": None,
                          "rmsd": RMSD_METHOD, "top_score": None,
                          "threshold": REDOCK_RMSD, "passed": False, "poses": []}
            try:
                redock = _dock(engine, receptor, reference, box, config, REFERENCE_KEY)
            except VinaRefused as exc:
                validation["error"] = str(exc)
                st.status = "failed"
                st.reason = f"Vina refused the reference ligand {reference.name}: {exc}"
                warnings.append(f"redocking {reference.name} failed (Vina refused it: {exc}); "
                                "the setup is not validated")
            else:
                rmsds = [atom_order_rmsd(pose_heavy_xyz(m), reference.heavy_xyz)
                         for m in redock.models]
                top = rmsds[0]
                validation.update({
                    "top_pose_rmsd": top, "top_score": redock.best_score,
                    "passed": top is not None and top <= REDOCK_RMSD,
                    "poses": [{"rank": k + 1, "score": s, "rmsd": r}
                              for k, (s, r) in enumerate(zip(redock.scores, rmsds))]})
                (out / "redock").mkdir(exist_ok=True)
                rel = f"redock/{reference.name}.pdbqt"
                (out / rel).write_text(redock.poses, encoding="utf-8")
                st.outputs = [rel]
                st.check = "PASS" if validation["passed"] else "FAIL"
                st.reason = (f"top pose {top:.2f} Å from the reference pose of "
                             f"{reference.name} (heavy atoms in atom order, an upper bound of "
                             f"the symmetry-aware RMSD; threshold {REDOCK_RMSD} Å)")
                if not validation["passed"]:
                    warnings.append(f"redocking {reference.name} put the top pose {top:.2f} Å "
                                    f"from its reference pose (threshold {REDOCK_RMSD} Å); the "
                                    "setup is not validated and the scores below should not "
                                    "be ranked")
    else:
        warnings.append("no reference ligand to redock: the setup is not validated")
        rec.skip("redock_validation", "no reference ligand was given, so the setup was not "
                                      "checked against a known pose")

    results: list[PreparedResult] = []
    failures: dict[str, str] = {}
    with rec.stage("dock", engine=engine.name, version=engine.version) as st:
        (out / "poses").mkdir(exist_ok=True)
        for lig in ligands:
            try:
                res = _dock(engine, receptor, lig, box, config, lig.key)
            except VinaRefused as exc:
                failures[lig.name] = str(exc)
                continue
            results.append(res)
            rel = f"poses/{lig.name}.pdbqt"
            (out / rel).write_text(res.poses, encoding="utf-8")
            st.outputs.append(rel)
        st.reason = (f"{len(results)} of {len(ligands)} ligands docked ({config.scoring}, "
                     f"exhaustiveness {config.exhaustiveness}, seed {config.seed}; "
                     f"{engine.name} {engine.version}, {engine.build} build)")
        if not results:
            st.status = "failed"
            st.reason = f"no ligand was docked: Vina refused all {len(ligands)}"
    if failures:
        warnings.append(f"{len(failures)} ligands could not be docked: "
                        + "; ".join(f"{k}: {v}" for k, v in failures.items()))

    pose_report: dict[str, dict[str, Any]] = {}
    if results:
        with rec.stage("pose_checks", engine="bioagent.docking.posecheck",
                       version=_VERSION) as st:
            bad = []
            for res in results:
                checks = [posecheck.pose_checks(pose_heavy_xyz(m), receptor.heavy_xyz,
                                                box.center, box.size) for m in res.models]
                pose_report[res.ligand.name] = {
                    "best": checks[0],
                    "poses_failed": [k + 1 for k, c in enumerate(checks) if not c["passed"]]}
                if not checks[0]["passed"]:
                    bad.append(f"{res.ligand.name} ({', '.join(checks[0]['failed'])})")
            st.check = "FAIL" if bad else "PASS"
            st.reason = ((f"best pose of {', '.join(bad)} failed" if bad else
                          "the best pose of every ligand passed") +
                         f": finite coordinates, heavy atoms inside the box (+-"
                         f"{posecheck.BOX_TOLERANCE} Å), no ligand-receptor heavy-atom pair "
                         f"closer than {posecheck.CLASH_DISTANCE} Å (metals left out); a "
                         "sanity check, not a validation of binding")
            if bad:
                warnings.append("the best pose of " + ", ".join(bad) + " failed the pose "
                                "checks; its score describes a pose that cannot be right")
    else:
        rec.not_applicable("pose_checks", "no pose to check")
    run = PreparedDockRun(out_dir=out, receptor=receptor, ligands=ligands, reference=reference,
                          box=box, engine=engine_record(engine), results=results,
                          validation=validation, failures=failures, warnings=warnings)
    run.pose_checks = pose_report
    _write(run, config, started, receptor_path, rec, inputs)
    return run


def _efficiency(res: PreparedResult) -> float | None:
    if not res.ligand.heavy_atoms:
        return None
    return round(-res.best_score / res.ligand.heavy_atoms, 3)


def _limitations(run: PreparedDockRun, rec: StageRecorder) -> list[str]:
    lines = list(LIMITATIONS)
    if not run.validated:
        lines.append("The setup was not validated by redocking a reference ligand within "
                     f"{REDOCK_RMSD} Å, so the scores are not from a validated setup and "
                     "should not be ranked as such.")
    if run.failures:
        lines.append(f"{len(run.failures)} ligands could not be docked and have no score: "
                     + ", ".join(sorted(run.failures)) + ".")
    lines += stage_limitations(rec.stages)
    return lines


def _pose_cell(run: PreparedDockRun, name: str, key: str) -> str:
    best = (run.pose_checks.get(name) or {}).get("best") or {}
    if key == "check":
        return ("pass" if best.get("passed") else
                "fail: " + ", ".join(best.get("failed") or ["no pose"]))
    value = best.get("min_receptor_distance")
    return "NA" if value is None else f"{value:.2f}"


def _engine_text(e: Mapping[str, Any]) -> str:
    build = ("WebAssembly build, SHA-256 " + e["wasm_sha256"][:12] if e.get("wasm_sha256")
             else f"{e['build']} build")
    return f"{e['name']} {e['version']} ({build})"


def _write(run: PreparedDockRun, config: PreparedDockConfig, started: str,
           receptor_path: str | Path, rec: StageRecorder, inputs: Mapping[str, str]) -> None:
    out = run.out_dir
    order = {lig.name: lig.index for lig in run.ligands}
    ranked = sorted(run.results, key=lambda r: (r.best_raw, order[r.ligand.name]))
    with rec.stage("report", engine=PIPELINE, version=_VERSION) as st:
        with (out / "scores.tsv").open("w", encoding="utf-8") as fh:
            fh.write("\t".join(SCORES_HEADER) + "\n")
            for r in ranked:
                fh.write(f"{r.ligand.name}\t{r.best_score:.3f}\t{_efficiency(r)}\t"
                         f"{r.ligand.heavy_atoms}\t{r.ligand.torsions}\t{len(r.energies)}\t"
                         f"{_pose_cell(run, r.ligand.name, 'check')}\t"
                         f"{_pose_cell(run, r.ligand.name, 'distance')}\n")
        plots: dict[str, str] = {}
        if ranked:
            plots["scores"] = svgplot.bars([r.ligand.name for r in ranked],
                                           [-r.best_score for r in ranked],
                                           title="Best Vina score per ligand (higher bar = "
                                                 "more favourable)", ylabel="-score (kcal/mol)")
        md = _markdown(run, config, ranked)
        (out / "report.md").write_text(md, encoding="utf-8")
        (out / "report.html").write_text(_html(md, plots), encoding="utf-8")
        pdir = out / "plots"
        pdir.mkdir(exist_ok=True)
        for name, svg in plots.items():
            (pdir / f"{name}.svg").write_text(svg, encoding="utf-8")
        run.limitations = write_limitations(out, _limitations(run, rec))
        st.outputs = ["scores.tsv", "report.md", "report.html", LIMITATIONS_FILE,
                      *(f"plots/{name}.svg" for name in plots)]
    code = hashlib.sha256()
    for p in sorted(Path(__file__).parent.glob("*.py")):
        code.update(p.name.encode() + b"\0" + p.read_bytes())
    manifest: dict[str, Any] = {}
    r = run.receptor
    with rec.stage("verify", engine=PIPELINE, version=_VERSION) as st:
        outputs = {str(p.relative_to(out)): _sha256(p) for p in sorted(out.rglob("*"))
                   if p.is_file() and p.name not in _UNLISTED}
        manifest.update({
            "pipeline": PIPELINE, "mode": "prepared", "code_digest": code.hexdigest(),
            "started": started,
            "finished": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "python": platform.python_version(), "engine": run.engine,
            "versions": {"vina": run.engine["version"]}, "config": config.as_dict(),
            "receptor": {"file": Path(receptor_path).name, "sha256": r.sha256,
                         "atoms": r.atoms, "heavy_atoms": r.heavy_atoms, "chains": r.chains,
                         "residues": r.residues},
            "box": {"center": list(run.box.center), "size": list(run.box.size),
                    "source": run.box.source},
            "inputs": dict(inputs),
            "ligands": [{"index": lig.index, "key": lig.key, "name": lig.name,
                         "sha256": lig.sha256, "heavy_atoms": lig.heavy_atoms,
                         "torsions": lig.torsions} for lig in run.ligands],
            "reference": None if run.reference is None else {
                "key": REFERENCE_KEY, "name": run.reference.name,
                "sha256": run.reference.sha256, "heavy_atoms": run.reference.heavy_atoms,
                "torsions": run.reference.torsions},
            "validation": run.validation, "outputs": outputs, "warnings": run.warnings,
            "results": [{"ligand": x.ligand.name, "best_score": x.best_score,
                         "energies": x.energies, "poses": len(x.energies),
                         "seconds": x.seconds,
                         "pose_checks": run.pose_checks.get(x.ligand.name)}
                        for x in ranked],
            "failures": dict(run.failures),
            "pose_checks": {"clash_distance": posecheck.CLASH_DISTANCE,
                            "box_tolerance": posecheck.BOX_TOLERANCE,
                            "what": "finite coordinates, heavy atoms inside the box, no "
                                    "ligand-receptor heavy-atom pair closer than the clash "
                                    "distance (metal ions left out); not a validation of "
                                    "binding"}})
        found = _content_problems(out, manifest) + output_problems(rec.stages, outputs, out)
        st.check = "FAIL" if found else "PASS"
        st.reason = ("; ".join(found)[:600] if found else
                     f"{len(outputs)} outputs digested; scores.tsv agrees with the run record")
    run.stages = rec.as_list()
    run.complete = rec.complete
    manifest.update({"stages": run.stages, "complete": run.complete,
                     "limitations": run.limitations})
    run.manifest = manifest
    (out / "run.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2,
                                             default=str), encoding="utf-8")
    rec.finish()


def _markdown(run: PreparedDockRun, config: PreparedDockConfig,
              ranked: Sequence[PreparedResult]) -> str:
    v, r, e = run.validation, run.receptor, run.engine
    lines = ["# Molecular docking (prepared inputs)", "",
             f"Receptor: prepared, rigid (PDBQT, SHA-256 {r.sha256[:12]}): {r.atoms} atoms, "
             f"chains {', '.join(r.chains) or '-'}, {r.residues} residues. Ligands: "
             f"{len(run.ligands)} prepared (PDBQT). Site: {run.box.source}, centre "
             f"({_fmt(run.box.center, '.1f')}), size ({_fmt(run.box.size, '.0f')}) Å. "
             f"Scoring: {config.scoring}, exhaustiveness {config.exhaustiveness}, seed "
             f"{config.seed}. Engine: {_engine_text(e)}.", ""]
    lines += ["## Validation", ""]
    if v and v.get("error"):
        lines.append(f"Redocking the reference ligand {v['ligand']}: Vina refused it "
                     f"({v['error']}). **Not validated**.")
    elif v:
        lines.append(f"Redocking the reference ligand {v['ligand']}: top pose "
                     f"{v['top_pose_rmsd']:.2f} Å from its reference pose (heavy atoms in "
                     f"atom order, an upper bound of the symmetry-aware RMSD), score "
                     f"{v['top_score']:.3f}. **{'Validated' if v['passed'] else 'Not validated'}"
                     f"** (threshold {v['threshold']} Å).")
    else:
        lines.append("**Not validated**: no reference ligand was given, so the setup was not "
                     "checked against a known pose.")
    lines.append("")
    if run.warnings:
        lines += ["## Warnings", ""] + [f"- {w}" for w in run.warnings] + [""]
    lines += ["## Scores", "",
              "| ligand | best score (kcal/mol) | ligand efficiency | heavy atoms | torsions | "
              "poses | pose check |",
              "|---|---|---|---|---|---|---|"]
    for x in ranked:
        lines.append(f"| {x.ligand.name} | {x.best_score:.3f} | {_efficiency(x)} | "
                     f"{x.ligand.heavy_atoms} | {x.ligand.torsions} | {len(x.energies)} | "
                     f"{_pose_cell(run, x.ligand.name, 'check')} |")
    lines += ["", "Poses are in `poses/<ligand>.pdbqt`, as Vina writes them (every pose, its "
              "score in a REMARK line)"
              + ("; the reference ligand's redocked poses in `redock/`" if run.reference
                 else "") + ".", "", "![scores](plots/scores.svg)", "",
              "## Methods", "",
              "- Inputs: a rigid receptor and ligands prepared elsewhere (PDBQT: protonation "
              "states, partial charges, AutoDock atom types, torsion trees), used as given and "
              "checked only against the records AutoDock Vina 1.2.7 reads.",
              f"- {e['name']} {e['version']} ({config.scoring} scoring), rigid receptor, grid "
              f"spacing {SPACING} Å, at most {config.n_poses} poses within "
              f"{config.energy_range:g} kcal/mol of the best and at least {MIN_RMSD} Å apart.",
              "- Redocking: the reference ligand docked from its own prepared conformer, the "
              "crystal one (Vina keeps its bond lengths, angles and ring shapes, so this is an "
              "easier check than docking a conformer generated from SMILES); RMSD of the top "
              "pose's heavy atoms to the reference's, atom by atom in input order (Vina keeps "
              "it), without symmetry: an upper bound of the symmetry-aware RMSD, which never "
              "flatters a pose.",
              f"- Pose checks: finite coordinates, every ligand heavy atom inside the box "
              f"(within {posecheck.BOX_TOLERANCE} Å), no ligand-receptor heavy-atom pair "
              f"closer than {posecheck.CLASH_DISTANCE} Å (metal ions left out). They are "
              "sanity checks, not PoseBusters and not a validation of binding; Vina's ranking "
              "is kept as it is.", "",
              "## What a result is", "",
              "A docking score is the scoring function's estimate, not a measured affinity: "
              "it tracks measured binding with a correlation near 0.5 and errors of about "
              "2 kcal/mol. A pose is a hypothesis about how a molecule could sit in the site. "
              "The receptor is rigid and the inputs are as they were prepared, which this run "
              "does not check. A promising score is a reason to test a compound, not evidence "
              "that it binds.", "",
              "## Reproducing it", "",
              "`run.json` records the inputs' digests, the box, every ligand's digest, the "
              "engine, the seed, the raw energies, each output's digest and each stage's "
              "status, engine, device and time; `limitations.md` lists this run's limitations. "
              "`bioagent dock --verify <out>` re-checks the outputs, that every required stage "
              "succeeded and that scores.tsv agrees with the record.", ""]
    return "\n".join(lines)


def _content_problems(out: Path, manifest: Mapping[str, Any]) -> list[str]:
    """scores.tsv against the run record: this mode's columns, one row per docked ligand (at
    least one), and the poses of each."""
    found = []
    results = manifest.get("results") or []
    path = out / "scores.tsv"
    if not path.is_file():
        return ["scores.tsv is missing"]
    header, rows = tsv_shape(path)
    if header != list(SCORES_HEADER):
        found.append(f"scores.tsv: its columns are not {', '.join(SCORES_HEADER)}")
    if rows != len(results):
        found.append(f"scores.tsv: {rows} rows, where the run record has {len(results)} "
                     "docked ligands")
    if not results:
        found.append("no ligand was docked")
    for r in results:
        if f"poses/{r.get('ligand')}.pdbqt" not in (manifest.get("outputs") or {}):
            found.append(f"poses/{r.get('ligand')}.pdbqt is not among the outputs")
    return found


def verify_run(out_dir: str | Path) -> tuple[bool, list[str]]:
    """Re-hash every recorded output and input file; check that the record is this
    pipeline's, that the run is complete and that scores.tsv agrees with it."""
    out = Path(out_dir)
    manifest = json.loads((out / "run.json").read_text(encoding="utf-8"))
    problems = []
    if manifest.get("pipeline") != PIPELINE:
        problems.append(f"run.json is not a {PIPELINE} run ({manifest.get('pipeline')!r})")
    for path, digest in (manifest.get("inputs") or {}).items():
        p = Path(path)
        if not p.is_file():
            problems.append(f"input {path} is missing")
        elif _sha256(p) != digest:
            problems.append(f"input {path} has changed")
    for rel, digest in (manifest.get("outputs") or {}).items():
        p = out / rel
        if not p.is_file():
            problems.append(f"output {rel} is missing")
        elif _sha256(p) != digest:
            problems.append(f"output {rel} has changed")
    problems += record_problems(manifest, out) if "stages" in manifest else \
        ["run.json records no stages"]
    problems += _content_problems(out, manifest)
    return not problems, problems
