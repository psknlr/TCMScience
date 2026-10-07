"""Complex structure prediction as a task: chains and their copies, ligands, constraints.

``structure.predict.Prediction`` is one sequence, one PDB, pLDDT and PAE: the shape of an
ESMFold or AlphaFold2 monomer. Boltz and Chai-1 predict *complexes*, and what they are
used for lives in what that shape cannot hold. A task has to be able to say "two copies of
this chain, a heme by its CCD code, a phosphoserine at position 15, and the ligand sits
against these residues"; a result has to keep the model as mmCIF (a complex with ligands
does not fit PDB's fixed columns), pLDDT per chain, and the interface confidence (ipTM,
and ipTM per pair of chains), because a confident monomer docked into a wrong interface
is the typical failure and only the interface scores see it.

Numbers one model reports and another does not (Boltz's PDE and affinity, Chai-1's clash
flags and aggregate score) stay in ``model_metrics`` under their own names. They are not
mapped onto a common scale they do not share.

Positions are 1-based indices into the chain's sequence as given here, the convention of
both Boltz and Chai-1, not residue numbers from a PDB file. Validation checks every one
against its chain before anything runs (``ComplexPredictionTask.validate``).

What a result is: a computational model. pLDDT and pTM/ipTM are the model's confidence in
itself, not measurements; a high ipTM for a ligand pose is not evidence of binding, and a
constraint the task supplied makes the interface it describes an input, not a finding.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Union

from ..backends.jobs import Artefact
from ..runtime.events import content_hash
from ..status import ExecutionStatus
from .tasks import EngineRun, ModelSpec, TaskInvalid, smiles_problem

__all__ = ["Chain", "Ligand", "Modification", "Token", "PocketConstraint",
           "ContactConstraint", "BondConstraint", "ComplexPredictionTask",
           "ChainConfidence", "ComplexPredictionResult", "POLYMER_KINDS", "ALPHABETS"]

POLYMER_KINDS = ("protein", "dna", "rna")
ALPHABETS = {"protein": frozenset("ACDEFGHIKLMNPQRSTVWY"),
             "dna": frozenset("ACGTN"), "rna": frozenset("ACGUN")}
_CHAIN_ID = re.compile(r"[A-Za-z0-9]{1,4}")
_CCD = re.compile(r"[A-Z0-9]{1,5}")
_ATOM = re.compile(r"[A-Za-z0-9'\"*]{1,6}")


def _ids(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    return tuple(str(v) for v in value)


@dataclass(frozen=True)
class Modification:
    """A modified residue: its 1-based position, and the CCD code of what it becomes."""

    position: int
    ccd: str


@dataclass(frozen=True)
class Chain:
    """A polymer entity, and the chain id of every copy: ``ids=("A", "B")`` is a dimer.

    ``msa`` is the path of a precomputed alignment for a protein, or ``""`` for none
    (single-sequence mode, which lowers accuracy and is recorded as a warning).
    """

    ids: tuple[str, ...]
    sequence: str
    kind: str = "protein"
    modifications: tuple[Modification, ...] = ()
    msa: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "ids", _ids(self.ids))
        object.__setattr__(self, "sequence", "".join(self.sequence.split()).upper())

    @property
    def stoichiometry(self) -> int:
        return len(self.ids)


@dataclass(frozen=True)
class Ligand:
    """A small molecule by SMILES or by Chemical Component Dictionary code — not both."""

    ids: tuple[str, ...]
    smiles: str = ""
    ccd: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "ids", _ids(self.ids))

    @property
    def stoichiometry(self) -> int:
        return len(self.ids)


@dataclass(frozen=True)
class Token:
    """A residue (by 1-based index in its chain) or, with ``atom``, one of its atoms.

    A ligand's index is 1; name its atoms by their CCD atom names.
    """

    chain: str
    index: int
    atom: str = ""


@dataclass(frozen=True)
class PocketConstraint:
    """The ``binder`` (a ligand or chain id) sits within ``max_distance`` Å of each contact."""

    binder: str
    contacts: tuple[Token, ...]
    max_distance: float = 6.0


@dataclass(frozen=True)
class ContactConstraint:
    """Two residues (or atoms) lie within ``max_distance`` Å of each other."""

    first: Token
    second: Token
    max_distance: float = 6.0


@dataclass(frozen=True)
class BondConstraint:
    """A covalent bond between two named atoms (a covalent ligand, a glycan)."""

    first: Token
    second: Token


Constraint = Union[PocketConstraint, ContactConstraint, BondConstraint]


@dataclass(frozen=True)
class ComplexPredictionTask:
    """One complex to predict, the model to predict it with, and how hard to try.

    ``use_msa_server`` sends every protein sequence to a third-party MSA server (ColabFold's
    MMseqs2 by default) and so needs ``allow_remote``; a confidential sequence must not be
    sent. ``seed`` is always set: Boltz without ``--seed`` does not repeat itself.
    """

    name: str
    chains: tuple[Chain, ...]
    ligands: tuple[Ligand, ...] = ()
    constraints: tuple[Constraint, ...] = ()
    model: ModelSpec = field(default_factory=lambda: ModelSpec("boltz-2"))
    seed: int = 42
    samples: int = 1
    recycling_steps: int = 3
    sampling_steps: int = 200
    use_msa_server: bool = False
    allow_remote: bool = False

    # ---------------------------------------------------------------- shape
    def chain_kinds(self) -> dict[str, str]:
        """Every chain id in input order, with its kind (``ligand`` for small molecules)."""
        out: dict[str, str] = {}
        for c in self.chains:
            for cid in c.ids:
                out[cid] = c.kind
        for lig in self.ligands:
            for cid in lig.ids:
                out[cid] = "ligand"
        return out

    def polymer(self, chain_id: str) -> Chain | None:
        return next((c for c in self.chains if chain_id in c.ids), None)

    def digest(self) -> str:
        return content_hash(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        def token(t: Token) -> dict[str, Any]:
            return {"chain": t.chain, "index": t.index, "atom": t.atom}

        constraints = []
        for c in self.constraints:
            if isinstance(c, PocketConstraint):
                constraints.append({"pocket": {"binder": c.binder, "max_distance":
                                               c.max_distance, "contacts":
                                               [token(t) for t in c.contacts]}})
            elif isinstance(c, ContactConstraint):
                constraints.append({"contact": {"first": token(c.first),
                                                "second": token(c.second),
                                                "max_distance": c.max_distance}})
            else:
                constraints.append({"bond": {"first": token(c.first),
                                             "second": token(c.second)}})
        return {"name": self.name,
                "chains": [{"ids": list(c.ids), "kind": c.kind, "sequence": c.sequence,
                            "modifications": [[m.position, m.ccd] for m in c.modifications],
                            "msa": c.msa} for c in self.chains],
                "ligands": [{"ids": list(lg.ids), "smiles": lg.smiles, "ccd": lg.ccd}
                            for lg in self.ligands],
                "constraints": constraints, "model": self.model.to_dict(),
                "seed": self.seed, "samples": self.samples,
                "recycling_steps": self.recycling_steps,
                "sampling_steps": self.sampling_steps,
                "use_msa_server": self.use_msa_server, "allow_remote": self.allow_remote}

    # ----------------------------------------------------------- validation
    def validate(self) -> list[str]:
        """Every reason this task cannot run, in the order found; empty when it can."""
        problems: list[str] = []
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", self.name):
            problems.append(f"name {self.name!r} must be 1-64 letters, digits, '_', '.' or "
                            "'-' (it names files)")
        if not self.chains:
            problems.append("a complex needs at least one polymer chain")
        seen: dict[str, str] = {}
        for k, entity in enumerate([*self.chains, *self.ligands], start=1):
            label = f"entity {k}"
            if entity.stoichiometry < 1:
                problems.append(f"{label} has no chain ids: stoichiometry must be at least 1")
            for cid in entity.ids:
                if not _CHAIN_ID.fullmatch(cid):
                    problems.append(f"{label}: chain id {cid!r} is not 1-4 letters or digits")
                if cid in seen:
                    where = (f"twice in {label}" if seen[cid] == label
                             else f"by {seen[cid]} and {label}")
                    problems.append(f"chain id {cid!r} is used {where}; chain ids must be "
                                    "unique")
                seen[cid] = label
        for c in self.chains:
            problems += self._polymer_problems(c)
        for lig in self.ligands:
            problems += self._ligand_problems(lig)
        problems += self._constraint_problems()
        problems += self.model.problems()
        if type(self.seed) is not int or self.seed < 0:
            problems.append("seed must be a non-negative integer")
        for name in ("samples", "sampling_steps"):
            if type(getattr(self, name)) is not int or getattr(self, name) < 1:
                problems.append(f"{name} must be a positive integer")
        if type(self.recycling_steps) is not int or self.recycling_steps < 0:
            problems.append("recycling_steps must be a non-negative integer")
        if self.use_msa_server and not self.allow_remote:
            problems.append("use_msa_server sends every protein sequence to a third-party "
                            "MSA server; set allow_remote to permit it, or give each chain "
                            "an MSA file")
        return problems

    def require_valid(self) -> "ComplexPredictionTask":
        problems = self.validate()
        if problems:
            raise TaskInvalid(self.name, problems)
        return self

    def smiles_checked(self) -> bool:
        """Whether RDKit was available to parse the ligand SMILES."""
        return all(smiles_problem(lig.smiles)[1] for lig in self.ligands if lig.smiles)

    def _polymer_problems(self, c: Chain) -> list[str]:
        where = f"chain {'/'.join(c.ids)}"
        if c.kind not in POLYMER_KINDS:
            return [f"{where}: kind {c.kind!r} is not one of {POLYMER_KINDS}"]
        out = []
        if not c.sequence:
            out.append(f"{where} has an empty sequence")
        bad = sorted(set(c.sequence) - ALPHABETS[c.kind])
        if bad:
            out.append(f"{where}: {', '.join(bad)} are not {c.kind} residues; give a modified "
                       "residue as its parent residue plus a Modification")
        positions = [m.position for m in c.modifications]
        if len(set(positions)) != len(positions):
            out.append(f"{where}: two modifications at one position")
        for m in c.modifications:
            if type(m.position) is not int or not 1 <= m.position <= len(c.sequence):
                out.append(f"{where}: modification position {m.position} is outside "
                           f"1..{len(c.sequence)}")
            if not _CCD.fullmatch(m.ccd or ""):
                out.append(f"{where}: modification {m.ccd!r} is not a CCD code")
        if c.msa:
            if c.kind != "protein":
                out.append(f"{where}: only protein chains take an MSA")
            elif not Path(c.msa).is_file():
                out.append(f"{where}: the MSA {c.msa} does not exist")
        return out

    def _ligand_problems(self, lig: Ligand) -> list[str]:
        where = f"ligand {'/'.join(lig.ids)}"
        if bool(lig.smiles) == bool(lig.ccd):
            return [f"{where}: give exactly one of a SMILES and a CCD code"]
        if lig.ccd:
            return [] if _CCD.fullmatch(lig.ccd) else [f"{where}: {lig.ccd!r} is not a CCD "
                                                        "code (1-5 capitals or digits)"]
        problem, _ = smiles_problem(lig.smiles)
        return [f"{where}: {problem}"] if problem else []

    def _token_problem(self, t: Token, what: str) -> str | None:
        kinds = self.chain_kinds()
        if t.chain not in kinds:
            return f"{what} names chain {t.chain!r}, which the task does not have"
        if kinds[t.chain] == "ligand":
            if t.index != 1:
                return f"{what}: a ligand's index is 1, not {t.index}"
            return None
        length = len(self.polymer(t.chain).sequence)
        if type(t.index) is not int or not 1 <= t.index <= length:
            return f"{what}: residue {t.index} is outside chain {t.chain} (1..{length})"
        if t.atom and not _ATOM.fullmatch(t.atom):
            return f"{what}: {t.atom!r} is not an atom name"
        return None

    def _constraint_problems(self) -> list[str]:
        out = []
        kinds = self.chain_kinds()
        for k, c in enumerate(self.constraints, start=1):
            what = f"constraint {k}"
            tokens: list[Token] = []
            if isinstance(c, PocketConstraint):
                if c.binder not in kinds:
                    out.append(f"{what}: the binder {c.binder!r} is not a chain of the task")
                if not c.contacts:
                    out.append(f"{what}: a pocket names at least one contact residue")
                if any(t.chain == c.binder for t in c.contacts):
                    out.append(f"{what}: a pocket's contacts lie on other chains than its "
                               "binder")
                tokens = list(c.contacts)
            elif isinstance(c, ContactConstraint):
                tokens = [c.first, c.second]
            elif isinstance(c, BondConstraint):
                tokens = [c.first, c.second]
                if not (c.first.atom and c.second.atom):
                    out.append(f"{what}: a bond joins two named atoms")
            else:
                out.append(f"{what}: {type(c).__name__} is not a constraint this task knows")
                continue
            for t in tokens:
                problem = self._token_problem(t, what)
                if problem:
                    out.append(problem)
            distance = getattr(c, "max_distance", None)
            if distance is not None and not (isinstance(distance, (int, float))
                                             and distance > 0):
                out.append(f"{what}: max_distance must be a positive number of Å")
        return out


@dataclass(frozen=True)
class ChainConfidence:
    chain: str
    kind: str
    units: int                   # residues for a polymer, atoms for a ligand
    plddt: float | None          # mean pLDDT, 0-100
    ptm: float | None = None     # the model's pTM for this chain, when it reports one


@dataclass
class ComplexPredictionResult:
    """What a prediction produced, or why there is nothing.

    ``status`` is SUCCEEDED only when the model file exists, holds the chains the task
    asked for with their sequences, and the confidence file parsed. Any other status
    leaves every number empty.
    """

    task: str
    task_digest: str
    status: ExecutionStatus
    reason: str = ""
    structure: Artefact | None = None
    chains: dict[str, ChainConfidence] = field(default_factory=dict)
    ptm: float | None = None
    iptm: float | None = None
    pair_iptm: dict[str, dict[str, float]] = field(default_factory=dict)
    pae: Artefact | None = None
    model_metrics: dict[str, Any] = field(default_factory=dict)
    provenance: EngineRun | None = None
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        def art(a: Artefact | None) -> Any:
            return None if a is None else {"path": str(a.path), "sha256": a.sha256,
                                           "size": a.size}

        return {"task": self.task, "task_digest": self.task_digest,
                "status": self.status.value, "reason": self.reason,
                "structure": art(self.structure),
                "chains": {k: vars(v) for k, v in self.chains.items()},
                "ptm": self.ptm, "iptm": self.iptm, "pair_iptm": self.pair_iptm,
                "pae": art(self.pae), "model_metrics": self.model_metrics,
                "provenance": self.provenance.to_dict() if self.provenance else None,
                "warnings": list(self.warnings)}

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, ensure_ascii=False, default=str)
