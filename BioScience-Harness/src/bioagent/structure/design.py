"""Sequence design as a task: a backbone, the chains to redesign, the positions to keep.

Inverse folding (ProteinMPNN; Dauparas et al. 2022, *Science* 378:49) runs the other way
from prediction: a backbone goes in, sequences come out. Its inputs are a structure,
which of its chains to redesign (the others stay as fixed context), which positions in
those chains to keep, a sampling temperature, how many sequences, and a seed. Its result
is a set of sequences, each with the model's score (the mean negative log-probability
over the designed positions; lower is more confident) and its recovery of the native
sequence at those positions.

Three of ProteinMPNN's conventions are enforced here because each, got wrong, runs
without complaint and answers a different question:

* **Fixed positions are 1-based indices into the chain as ProteinMPNN reads it**, not
  residue numbers: residue numbers from the chain's first to its last, a gap filling each
  missing number, insertion codes adding positions (``backbone_chains``). A fixed position
  given as a residue number on a chain that does not start at 1 keeps the wrong residue.
* **The seed is at least 1.** ``--seed 0`` means "pick one at random", so a run meant to
  repeat would not.
* **The number of sequences is a multiple of the batch size.** ProteinMPNN makes
  ``num_sequences // batch_size`` batches, so eight sequences in batches of three is six.

What a result is: candidate sequences the model finds compatible with the backbone. A
good score and a high recovery are not evidence that a sequence folds, binds or
expresses; that takes refolding the design and then experiment.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping

from ..backends.jobs import Artefact
from ..runtime.events import content_hash
from ..status import ExecutionStatus
from .tasks import EngineRun, ModelSpec, TaskInvalid, sha256_file

__all__ = ["SequenceDesignTask", "DesignedSequence", "SequenceDesignResult",
           "backbone_chains", "GAP"]

#: How ProteinMPNN writes a missing or non-standard residue in a parsed chain.
GAP = "-"
_THREE = {"ALA": "A", "ARG": "R", "ASN": "N", "ASP": "D", "CYS": "C", "GLN": "Q",
          "GLU": "E", "GLY": "G", "HIS": "H", "ILE": "I", "LEU": "L", "LYS": "K",
          "MET": "M", "PHE": "F", "PRO": "P", "SER": "S", "THR": "T", "TRP": "W",
          "TYR": "Y", "VAL": "V"}


def backbone_chains(path: str | Path) -> dict[str, str]:
    """Each chain's sequence as ProteinMPNN's ``parse_PDB_biounits`` builds it.

    ATOM records (and MSE as methionine); residue numbers from the chain's lowest to its
    highest, ``-`` for each missing number and for residue names it does not know,
    insertion-coded residues in code order after their number. Position *k* of the
    returned string is ProteinMPNN's position *k*.
    """
    residues: dict[str, dict[int, dict[str, str]]] = {}
    for raw in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.rstrip()
        if line[:6] == "HETATM" and line[17:20] == "MSE":
            line = "ATOM  " + line[6:17] + "MET" + line[20:]
        if line[:4] != "ATOM" or len(line) < 27:
            continue
        number = line[22:27].strip()
        icode = number[-1] if number and number[-1].isalpha() else ""
        try:
            resseq = int(number[:-1] if icode else number)
        except ValueError:
            continue
        residues.setdefault(line[21], {}).setdefault(resseq, {}).setdefault(
            icode, _THREE.get(line[17:20], GAP))
    out = {}
    for chain, by_number in residues.items():
        seq = []
        for n in range(min(by_number), max(by_number) + 1):
            if n in by_number:
                seq += [by_number[n][code] for code in sorted(by_number[n])]
            else:
                seq.append(GAP)
        out[chain] = "".join(seq)
    return out


@dataclass(frozen=True)
class SequenceDesignTask:
    """One backbone to design sequences for, and the model to design them with.

    ``fixed_positions`` maps a designed chain to the positions in it to keep; positions in
    chains that are not designed are kept already. ``model.options`` may name the
    ProteinMPNN weights (``model_name``, default ``v_48_020``) and ``use_soluble_model``.
    """

    name: str
    backbone: str
    design_chains: tuple[str, ...]
    fixed_positions: Mapping[str, tuple[int, ...]] = field(default_factory=dict)
    temperature: float = 0.1
    num_sequences: int = 8
    seed: int = 37
    batch_size: int = 1
    model: ModelSpec = field(default_factory=lambda: ModelSpec("proteinmpnn"))

    def __post_init__(self) -> None:
        chains = self.design_chains
        object.__setattr__(self, "design_chains",
                           (chains,) if isinstance(chains, str) else tuple(chains))
        object.__setattr__(self, "fixed_positions",
                           {str(k): tuple(v) for k, v in dict(self.fixed_positions).items()})

    def chains(self) -> dict[str, str]:
        return backbone_chains(self.backbone)

    def validate(self) -> list[str]:
        problems: list[str] = []
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", self.name):
            problems.append(f"name {self.name!r} must be 1-64 letters, digits, '_', '.' or "
                            "'-' (it names files)")
        path = Path(self.backbone)
        if not path.is_file():
            return problems + [f"the backbone {self.backbone} does not exist"]
        if path.suffix.lower() != ".pdb":
            # ProteinMPNN names the design after the file by dropping its last four
            # characters, and reads PDB columns only.
            problems.append(f"the backbone must be a .pdb file, not {path.name}")
        text = path.read_text(encoding="utf-8", errors="replace")
        if sum(1 for ln in text.splitlines() if ln.startswith("MODEL")) > 1:
            problems.append("the backbone holds several MODELs, which ProteinMPNN would read "
                            "as one; give a single model")
        chains = self.chains()
        if not chains:
            return problems + [f"{path.name} has no protein ATOM records"]
        if not self.design_chains:
            problems.append("name at least one chain to design")
        if len(set(self.design_chains)) != len(self.design_chains):
            problems.append("a chain is named twice in design_chains")
        for chain in self.design_chains:
            if chain not in chains:
                problems.append(f"chain {chain!r} is not in the backbone (it has "
                                f"{', '.join(sorted(chains))})")
        for chain, positions in self.fixed_positions.items():
            if chain not in self.design_chains:
                problems.append(f"fixed positions on chain {chain!r}, which is not designed; "
                                "its whole sequence is kept already")
                continue
            if chain not in chains:
                continue
            length = len(chains[chain])
            if len(set(positions)) != len(positions):
                problems.append(f"chain {chain}: a fixed position is listed twice")
            outside = [p for p in positions if type(p) is not int or not 1 <= p <= length]
            if outside:
                problems.append(f"chain {chain}: fixed positions {outside[:5]} are outside "
                                f"1..{length} (positions count from the chain's first "
                                "residue, not residue numbers)")
            elif len(set(positions)) == length:
                problems.append(f"chain {chain}: every position is fixed, so nothing would be "
                                "designed; leave it out of design_chains")
        if not isinstance(self.temperature, (int, float)) or not 0 < self.temperature <= 1:
            problems.append("temperature must be in (0, 1]; ProteinMPNN suggests 0.1-0.3")
        for name in ("num_sequences", "batch_size"):
            if type(getattr(self, name)) is not int or getattr(self, name) < 1:
                problems.append(f"{name} must be a positive integer")
        if (type(self.num_sequences) is int and type(self.batch_size) is int
                and self.batch_size >= 1 and self.num_sequences % self.batch_size):
            problems.append(f"num_sequences ({self.num_sequences}) is not a multiple of "
                            f"batch_size ({self.batch_size}); ProteinMPNN would make "
                            f"{self.num_sequences // self.batch_size * self.batch_size}")
        if type(self.seed) is not int or self.seed < 1:
            problems.append("seed must be at least 1: ProteinMPNN reads 0 as 'pick a random "
                            "seed', and the run would not repeat")
        problems += self.model.problems()
        return problems

    def require_valid(self) -> "SequenceDesignTask":
        problems = self.validate()
        if problems:
            raise TaskInvalid(self.name, problems)
        return self

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["design_chains"] = list(self.design_chains)
        d["fixed_positions"] = {k: list(v) for k, v in self.fixed_positions.items()}
        d["model"] = self.model.to_dict()
        backbone = Path(self.backbone)
        d["backbone_sha256"] = sha256_file(backbone) if backbone.is_file() else ""
        return d

    def digest(self) -> str:
        return content_hash(self.to_dict())


@dataclass(frozen=True)
class DesignedSequence:
    sample: int
    sequences: Mapping[str, str]       # designed chain -> sequence
    score: float                       # mean -log p over the designed positions
    global_score: float                # mean -log p over every position
    recovery: float                    # identity to the native at the designed positions
    temperature: float


@dataclass
class SequenceDesignResult:
    """The designs, the native sequence and its score, or why there are none."""

    task: str
    task_digest: str
    status: ExecutionStatus
    reason: str = ""
    native: dict[str, str] = field(default_factory=dict)
    native_score: float | None = None
    native_global_score: float | None = None
    designs: list[DesignedSequence] = field(default_factory=list)
    fasta: Artefact | None = None
    model_metrics: dict[str, Any] = field(default_factory=dict)
    provenance: EngineRun | None = None
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"task": self.task, "task_digest": self.task_digest,
                "status": self.status.value, "reason": self.reason, "native": self.native,
                "native_score": self.native_score,
                "native_global_score": self.native_global_score,
                "designs": [{**asdict(d), "sequences": dict(d.sequences)}
                            for d in self.designs],
                "fasta": None if self.fasta is None else {
                    "path": str(self.fasta.path), "sha256": self.fasta.sha256,
                    "size": self.fasta.size},
                "model_metrics": self.model_metrics,
                "provenance": self.provenance.to_dict() if self.provenance else None,
                "warnings": list(self.warnings)}

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, ensure_ascii=False, default=str)
