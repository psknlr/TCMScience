"""What every compute task shares: refusal with reasons, the model that ran, digested files.

Complex prediction, sequence design and molecular dynamics are different scientific tasks
with different inputs and outputs (``complex``, ``design``, ``dynamics``). Three things are
the same for all of them, and each is here because its absence produced a wrong record:

* **A task is checked before anything runs, and refused with every reason at once**
  (``TaskInvalid``). A GPU job that fails twenty minutes in because a constraint names a
  residue the chain does not have has spent the twenty minutes and says less than the
  check would have.
* **The model is part of the task** (``ModelSpec``): name, version, the SHA-256 of the
  weights, the environment it runs in. "Boltz" is not a method; Boltz-2 2.2.0 with a given
  checkpoint is. A pinned version or digest that the installation does not match is a
  refusal, not a warning.
* **Provenance says whether the tool ran at all** (``EngineRun.ran``). An adapter for a
  tool that is not installed returns a result whose status is UNAVAILABLE and whose
  provenance says nothing was run, so no number in it can be mistaken for a prediction.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

__all__ = ["TaskInvalid", "ModelSpec", "EngineRun", "sha256_file", "smiles_problem"]

_SHA256 = re.compile(r"[0-9a-f]{64}")


class TaskInvalid(ValueError):
    """A task that cannot be run as given, with every reason found."""

    def __init__(self, task: str, problems: Sequence[str]) -> None:
        self.task = task
        self.problems = tuple(problems)
        super().__init__(f"{task}: " + "; ".join(self.problems))


@dataclass(frozen=True)
class ModelSpec:
    """Which model a task asks for, as precisely as it chooses to pin it.

    ``version`` and ``weights_sha256`` left empty mean "record whatever is installed";
    given, they are requirements the adapter checks before running. ``environment`` names
    the provider project whose interpreter the reviewed environment configuration gives.
    ``options`` holds engine-specific settings; an engine refuses options it does not
    know rather than ignoring them.
    """

    name: str
    version: str = ""
    weights_sha256: str = ""
    environment: str = ""
    options: Mapping[str, Any] = field(default_factory=dict)

    def problems(self) -> list[str]:
        out = []
        if not self.name.strip():
            out.append("the model has no name")
        if self.weights_sha256 and not _SHA256.fullmatch(self.weights_sha256.lower()):
            out.append(f"weights_sha256 {self.weights_sha256!r} is not a SHA-256 digest")
        return out

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["options"] = dict(self.options)
        return d


@dataclass(frozen=True)
class EngineRun:
    """What ran, in which environment, from which weights — or that nothing did."""

    engine: str
    ran: bool
    version: str = ""
    interpreter: str = ""
    environment: str = ""
    weights: Mapping[str, str] = field(default_factory=dict)    # file -> sha256
    command: tuple[str, ...] = ()
    job: Mapping[str, Any] | None = None
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["weights"] = dict(self.weights)
        d["command"] = list(self.command)
        d["job"] = dict(self.job) if self.job else None
        return d


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def smiles_problem(smiles: str) -> tuple[str | None, bool]:
    """(problem or None, whether RDKit checked it).

    RDKit is optional. Without it a SMILES is not parsed here, and the caller records that
    it was not checked rather than treating it as valid.
    """
    if not smiles.strip():
        return "the SMILES is empty", True
    if any(ch.isspace() for ch in smiles.strip()):
        return f"{smiles!r} contains whitespace; a SMILES is one token", True
    try:
        from rdkit import Chem, RDLogger
    except ImportError:
        return None, False
    RDLogger.DisableLog("rdApp.*")
    try:
        mol = Chem.MolFromSmiles(smiles)
    finally:
        RDLogger.EnableLog("rdApp.*")
    if mol is None:
        return f"RDKit cannot parse the SMILES {smiles!r}", True
    return None, True
