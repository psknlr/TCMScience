"""Relaxation and molecular dynamics as a task: structure, force field, solvent, time, heat.

Minimisation and a short MD run (OpenMM; Eastman et al. 2017, *PLOS Comput Biol*
13:e1005659) answer a different question from prediction: whether a model holds together
under a physical force field, not whether it is right. Their inputs are physical choices
that change the answer, so each is part of the task and of its digest: the force-field
files, explicit, implicit or no solvent, the box padding, the ionic strength and ion
species, the protonation pH, the time step, the temperature and the thermostat's
friction, the duration, and the thermostat's seed.

Refused rather than defaulted, because each default makes a run unrepeatable or
unreadable:

* **seed 0**, which OpenMM reads as "choose a seed", so the run would not repeat;
* **explicit solvent without a water model** among the force-field files, and implicit
  solvent without an implicit-solvent file: the system would be built in vacuum, or not
  at all, under a label that says otherwise;
* **water molecules in the structure with no water model to describe them** (a crystal
  structure's waters in an implicit-solvent or vacuum run): OpenMM has no template for
  them, and the run would fail only after it had started;
* **a time step above 2 fs**: that needs hydrogen-mass repartitioning, which this task
  does not set up;
* **a duration or report interval that is not a whole number of steps**: the run would
  be silently shorter than asked.

What a result is: the force field's energies, in kJ/mol, for this force field, water
model and box; they are not comparable across those choices. A trajectory of tens of
picoseconds samples the neighbourhood of the starting model and says nothing about
folding, binding or rare conformational change.
"""

from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ..backends.jobs import Artefact
from ..runtime.events import content_hash
from ..status import ExecutionStatus
from .tasks import EngineRun, ModelSpec, TaskInvalid, sha256_file

__all__ = ["DynamicsTask", "EnergyRecord", "DynamicsResult", "SOLVENTS",
           "POSITIVE_IONS", "NEGATIVE_IONS", "PLATFORMS", "count_waters"]

SOLVENTS = ("explicit", "implicit", "vacuum")
#: The ions OpenMM's Modeller.addSolvent can place.
POSITIVE_IONS = ("Na+", "K+", "Li+", "Rb+", "Cs+")
NEGATIVE_IONS = ("Cl-", "Br-", "F-", "I-")
PLATFORMS = ("", "Reference", "CPU", "CUDA", "OpenCL", "HIP")
_WATER = re.compile(r"tip3p|tip4p|tip5p|spce|opc", re.IGNORECASE)
#: Residue names OpenMM reads as water (its pdbNames.xml), as a PDB's three residue-name
#: columns hold them ("TIP3" reads as "TIP").
_WATER_NAMES = frozenset({"HOH", "H2O", "WAT", "SOL", "TIP", "TP3", "T4P", "SPC"})


def count_waters(path: str | Path) -> int:
    """The water molecules in a PDB or mmCIF structure; 0 when it cannot be read here.

    A file this reader cannot parse is left to OpenMM, which reads it and says why not.
    """
    from .mmcif import read_mmcif
    from .pdbio import read_pdb

    p = Path(path)
    try:
        model = read_mmcif(p) if p.suffix.lower() in (".cif", ".mmcif") else read_pdb(p)
    except (OSError, ValueError):
        return 0
    return len({(a.chain, a.resseq, a.icode) for a in model.atoms
                if a.resname in _WATER_NAMES})


@dataclass(frozen=True)
class DynamicsTask:
    """One structure to relax and, if ``duration_ps`` > 0, to simulate.

    ``add_hydrogens_ph`` protonates the structure at that pH (OpenMM's residue templates,
    not a pKa model); ``None`` means the structure already carries its hydrogens. An
    empty ``platform`` lets OpenMM choose, and the result records which it chose.
    """

    name: str
    structure: str
    force_field: tuple[str, ...] = ("amber14-all.xml", "amber14/tip3pfb.xml")
    solvent: str = "explicit"
    padding_nm: float = 1.0
    ionic_strength_molar: float = 0.15
    positive_ion: str = "Na+"
    negative_ion: str = "Cl-"
    add_hydrogens_ph: float | None = 7.0
    minimise: bool = True
    duration_ps: float = 100.0
    timestep_fs: float = 2.0
    temperature_k: float = 300.0
    friction_per_ps: float = 1.0
    report_interval_ps: float = 1.0
    seed: int = 1
    platform: str = ""
    model: ModelSpec = field(default_factory=lambda: ModelSpec("openmm"))

    def __post_init__(self) -> None:
        ff = self.force_field
        object.__setattr__(self, "force_field", (ff,) if isinstance(ff, str) else tuple(ff))

    @property
    def steps(self) -> int:
        return round(self.duration_ps * 1000.0 / self.timestep_fs)

    @property
    def report_steps(self) -> int:
        return round(self.report_interval_ps * 1000.0 / self.timestep_fs)

    def validate(self) -> list[str]:
        problems: list[str] = []
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", self.name):
            problems.append(f"name {self.name!r} must be 1-64 letters, digits, '_', '.' or "
                            "'-' (it names files)")
        path = Path(self.structure)
        if not path.is_file():
            problems.append(f"the structure {self.structure} does not exist")
        elif path.suffix.lower() not in (".pdb", ".cif", ".mmcif"):
            problems.append(f"the structure must be PDB or mmCIF, not {path.name}")
        if not self.force_field or not all(isinstance(f, str) and f.endswith(".xml")
                                           for f in self.force_field):
            problems.append("force_field lists OpenMM force-field .xml files")
        if self.solvent not in SOLVENTS:
            problems.append(f"solvent is one of {SOLVENTS}, not {self.solvent!r}")
        elif self.solvent == "explicit":
            if not any(_WATER.search(f) for f in self.force_field):
                problems.append("explicit solvent needs a water model among the force-field "
                                "files (e.g. amber14/tip3pfb.xml)")
            if not _positive(self.padding_nm):
                problems.append("padding_nm must be a positive distance")
            if not isinstance(self.ionic_strength_molar, (int, float)) or \
                    self.ionic_strength_molar < 0:
                problems.append("ionic_strength_molar must not be negative")
            if self.positive_ion not in POSITIVE_IONS:
                problems.append(f"positive_ion is one of {POSITIVE_IONS}")
            if self.negative_ion not in NEGATIVE_IONS:
                problems.append(f"negative_ion is one of {NEGATIVE_IONS}")
        else:
            if self.solvent == "implicit" and not any("implicit" in f
                                                      for f in self.force_field):
                problems.append("implicit solvent needs an implicit-solvent file among the "
                                "force-field files (e.g. implicit/gbn2.xml)")
            if self.ionic_strength_molar:
                problems.append(f"ions are added only with explicit solvent; set "
                                f"ionic_strength_molar to 0 for {self.solvent}")
            waters = count_waters(path) if path.is_file() else 0
            if waters and not any(_WATER.search(f) for f in self.force_field):
                problems.append(
                    f"the structure holds {waters} water molecules and no water model is "
                    f"among the force-field files, so OpenMM has no template for them in a "
                    f"{self.solvent} system; remove them, or use explicit solvent")
        if self.add_hydrogens_ph is not None and not (
                isinstance(self.add_hydrogens_ph, (int, float))
                and 0 <= self.add_hydrogens_ph <= 14):
            problems.append("add_hydrogens_ph is a pH between 0 and 14, or None")
        problems += self._time_problems()
        if not _positive(self.temperature_k):
            problems.append("temperature_k must be positive")
        if not _positive(self.friction_per_ps):
            problems.append("friction_per_ps must be positive")
        if type(self.seed) is not int or self.seed < 1:
            problems.append("seed must be at least 1: OpenMM reads 0 as 'choose a seed', "
                            "and the run would not repeat")
        if self.platform not in PLATFORMS:
            problems.append(f"platform is one of {PLATFORMS[1:]}, or empty for OpenMM's "
                            "choice")
        problems += self.model.problems()
        return problems

    def _time_problems(self) -> list[str]:
        out = []
        if not _positive(self.timestep_fs) or self.timestep_fs > 2.0:
            return ["timestep_fs must be in (0, 2]: longer steps need hydrogen-mass "
                    "repartitioning, which this task does not set up"]
        if not isinstance(self.duration_ps, (int, float)) or self.duration_ps < 0:
            return ["duration_ps must not be negative"]
        if not self.minimise and self.duration_ps == 0:
            out.append("nothing to do: no minimisation and no dynamics")
        if not _whole(self.duration_ps * 1000.0 / self.timestep_fs):
            out.append(f"duration_ps {self.duration_ps} is not a whole number of "
                       f"{self.timestep_fs} fs steps")
        if self.duration_ps > 0:
            if not _positive(self.report_interval_ps) or \
                    self.report_interval_ps > self.duration_ps:
                out.append("report_interval_ps must be positive and no longer than the run")
            elif not _whole(self.report_interval_ps * 1000.0 / self.timestep_fs) or \
                    self.steps % max(self.report_steps, 1):
                out.append("report_interval_ps must be a whole number of steps that divides "
                           "the run")
        return out

    def require_valid(self) -> "DynamicsTask":
        problems = self.validate()
        if problems:
            raise TaskInvalid(self.name, problems)
        return self

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["force_field"] = list(self.force_field)
        d["model"] = self.model.to_dict()
        path = Path(self.structure)
        d["structure_sha256"] = sha256_file(path) if path.is_file() else ""
        return d

    def digest(self) -> str:
        return content_hash(self.to_dict())


def _positive(x: Any) -> bool:
    return isinstance(x, (int, float)) and math.isfinite(x) and x > 0


def _whole(x: float) -> bool:
    return abs(x - round(x)) < 1e-6


@dataclass(frozen=True)
class EnergyRecord:
    step: int
    time_ps: float
    potential_kj_mol: float
    kinetic_kj_mol: float
    temperature_k: float
    volume_nm3: float | None = None


@dataclass
class DynamicsResult:
    """The relaxed structure, the trajectory and its energies, or why there are none."""

    task: str
    task_digest: str
    status: ExecutionStatus
    reason: str = ""
    initial_energy_kj_mol: float | None = None
    minimised_energy_kj_mol: float | None = None
    relaxed: Artefact | None = None
    final: Artefact | None = None
    trajectory: Artefact | None = None
    energy_table: Artefact | None = None
    energies: list[EnergyRecord] = field(default_factory=list)
    model_metrics: dict[str, Any] = field(default_factory=dict)
    provenance: EngineRun | None = None
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        def art(a: Artefact | None) -> Any:
            return None if a is None else {"path": str(a.path), "sha256": a.sha256,
                                           "size": a.size}

        return {"task": self.task, "task_digest": self.task_digest,
                "status": self.status.value, "reason": self.reason,
                "initial_energy_kj_mol": self.initial_energy_kj_mol,
                "minimised_energy_kj_mol": self.minimised_energy_kj_mol,
                "relaxed": art(self.relaxed), "final": art(self.final),
                "trajectory": art(self.trajectory), "energy_table": art(self.energy_table),
                "energies": [asdict(e) for e in self.energies],
                "model_metrics": self.model_metrics,
                "provenance": self.provenance.to_dict() if self.provenance else None,
                "warnings": list(self.warnings)}
