"""Boltz, Chai-1, ProteinMPNN and OpenMM: detect, refuse with the reason, run as a long job.

None of the four is a dependency of the harness; each lives in its own environment with
its own torch or CUDA build, and on most machines none is installed. An adapter does
three separable things, so the two that do not need the tool can be checked without it:

1. **Detect** (``probe``). Ask the interpreter the reviewed environment configuration
   names for the tool's project (``backends.environments``; the harness's own interpreter
   when none is named) whether the tool's modules are importable, and which version is
   installed. Absent is UNAVAILABLE, the reason names the interpreter, and the result's
   provenance says nothing ran (``EngineRun.ran is False``). No engine stands in for
   another.
2. **Prepare** (``prepare``). Check what the tool needs beyond the task — weights already
   on disk, so nothing is downloaded mid-run and the weights that ran are the weights
   recorded; a pinned version or weights digest — and render the task in the tool's own
   format (Boltz YAML, Chai-1 FASTA and restraints, ProteinMPNN's fixed-position file, an
   OpenMM script and its configuration), refusing what the tool cannot express.
3. **Read** (``read``). Turn the tool's output files into the task's result. Before that,
   collection (``backends.jobs``) has checked that each model holds the chains and
   sequences that were asked for, so a model of something else cannot become a result.

``run`` composes the three through the long-job protocol: a step is SUCCEEDED only after
its artefacts are collected and validated, and a job that outlives ``timeout_s`` is
TIMEOUT, still running, collectable later with ``resume``.

Formats were read from each project's documentation and source on 2026-10-07: Boltz
``docs/prediction.md`` and ``src/boltz/main.py``; chai-lab ``README.md``,
``examples/restraints/README.md`` and ``chai_lab/chai1.py``; ProteinMPNN ``README.md``,
``protein_mpnn_run.py`` and ``helper_scripts``. None of the tools is installed where this
was written, so none was run: the renderers and readers are tested against files in the
documented formats, not against the tools.
"""

from __future__ import annotations

import abc
import csv
import hashlib
import json
import math
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

import numpy as np

from ..backends.jobs import (OUTPUT_TOKEN, Artefact, ArtefactSpec, JobController, JobOutcome,
                             JobRef, JobSpec, LocalSubprocessJobs)
from ..status import ExecutionStatus
from .complex import (BondConstraint, ChainConfidence, ComplexPredictionResult,
                      ComplexPredictionTask, ContactConstraint, PocketConstraint, Token)
from .design import DesignedSequence, SequenceDesignResult, SequenceDesignTask
from .dynamics import DynamicsResult, DynamicsTask, EnergyRecord
from .mmcif import read_mmcif
from .pdbio import THREE_TO_ONE, Structure, read_pdb
from .tasks import EngineRun, TaskInvalid, sha256_file

__all__ = ["EngineProbe", "EngineRefusal", "Engine", "BoltzEngine", "ChaiEngine",
           "ProteinMPNNEngine", "OpenMMEngine", "ENGINES"]

#: Run in the tool's interpreter. ``find_spec`` locates a package without importing it,
#: so asking costs no torch start-up.
_PROBE = (
    "import importlib.util, importlib.metadata as md, json, platform, sys\n"
    "found = {m: importlib.util.find_spec(m) is not None for m in sys.argv[2:]}\n"
    "try:\n"
    "    version = md.version(sys.argv[1]) if sys.argv[1] else ''\n"
    "except Exception:\n"
    "    version = ''\n"
    "print(json.dumps({'found': found, 'version': version,\n"
    "                  'python': platform.python_version()}))\n")


@dataclass(frozen=True)
class EngineProbe:
    available: bool
    interpreter: str = ""
    environment: str = ""
    version: str = ""
    reason: str = ""
    root: str = ""


class EngineRefusal(RuntimeError):
    """The engine cannot run this task here; ``status`` says why in ExecutionStatus terms."""

    def __init__(self, message: str, status: ExecutionStatus = ExecutionStatus.UNAVAILABLE):
        super().__init__(message)
        self.status = status


@dataclass
class Prepared:
    spec: JobSpec
    validators: dict[str, Callable[[Path], "str | None"]]
    weights: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    context: dict[str, Any] = field(default_factory=dict)


#: Weight digests already computed, by (path, size, mtime): a checkpoint is gigabytes.
_DIGESTS: dict[tuple[str, int, float], str] = {}


def _weights_digest(path: Path) -> str:
    st = path.stat()
    key = (str(path.resolve()), st.st_size, st.st_mtime)
    if key not in _DIGESTS:
        _DIGESTS[key] = sha256_file(path)
    return _DIGESTS[key]


class Engine(abc.ABC):
    """One external tool: how to find it, feed it, run it and read it."""

    name = ""
    project = ""                       # the provider project the reviewed config names
    distribution = ""                  # the package whose installed version is recorded
    modules: tuple[str, ...] = ()      # what must be importable in its interpreter
    models: tuple[str, ...] = ()       # the ModelSpec names this engine runs
    options: frozenset[str] = frozenset()

    def __init__(self, environments: Any = None, *, probe_timeout_s: float = 60.0) -> None:
        self.environments = environments
        self.probe_timeout_s = probe_timeout_s

    # ------------------------------------------------------------------ detect
    def probe(self) -> EngineProbe:
        chosen = (self.environments.interpreter(self.project)
                  if self.environments is not None else None)
        if chosen is None:
            python = sys.executable
            env = (f"the harness's own interpreter; no reviewed environment names one for "
                   f"{self.project!r}")
            root = ""
        else:
            python, env = str(chosen.python), self.environments.describe()
            root = str(chosen.root or "")
            _, problem = chosen.version()
            if problem:
                return EngineProbe(False, python, env, reason=problem)
        try:
            proc = subprocess.run(  # noqa: S603 - a reviewed interpreter, no shell
                [python, "-I", "-c", _PROBE, self.distribution, *self.modules],
                capture_output=True, text=True, timeout=self.probe_timeout_s, check=False)
            doc = json.loads(proc.stdout.strip().splitlines()[-1])
        except (OSError, subprocess.TimeoutExpired, ValueError, IndexError) as exc:
            return EngineProbe(False, python, env,
                               reason=f"{python} could not be asked about {self.name}: {exc}")
        missing = [m for m, ok in doc.get("found", {}).items() if not ok]
        if missing:
            return EngineProbe(False, python, env, reason=(
                f"{self.name} is not installed in {python} ({env}): "
                f"{', '.join(missing)} cannot be imported there"))
        return self._installed(EngineProbe(True, python, env, str(doc.get("version") or ""),
                                           root=root))

    def _installed(self, probe: EngineProbe) -> EngineProbe:
        return probe

    # --------------------------------------------------------------------- run
    def check(self, task: Any) -> None:
        """The task is valid and asks for something this engine runs; raises otherwise."""
        task.require_valid()
        problems = []
        if task.model.name not in self.models:
            problems.append(f"{self.name} runs {', '.join(self.models)}, not "
                            f"{task.model.name!r}")
        unknown = sorted(set(task.model.options) - self.options)
        if unknown:
            problems.append(f"{self.name} has no options {unknown}; it knows "
                            f"{sorted(self.options)}")
        if problems:
            raise TaskInvalid(task.name, problems)

    def run(self, task: Any, workdir: str | Path, *, controller: JobController | None = None,
            timeout_s: float = 6 * 3600.0, poll_s: float = 10.0,
            idempotency_key: str = "") -> Any:
        """Check, detect, prepare, run as a job, collect, read. Never runs a stand-in."""
        self.check(task)
        probe = self.probe()
        if not probe.available:
            return self.result(task, ExecutionStatus.UNAVAILABLE,
                               f"{probe.reason}; no model was run",
                               self._unrun(probe, "not installed"))
        work = Path(workdir).resolve() / task.name
        try:
            self._check_version(task, probe)
            work.mkdir(parents=True, exist_ok=True)
            prepared = self.prepare(task, work, probe)
        except EngineRefusal as exc:
            return self.result(task, exc.status, f"{exc}; no model was run",
                               self._unrun(probe, "refused before running"))
        controller = controller or self.controller(work)
        outcome = controller.run(prepared.spec, timeout_s=timeout_s, poll_s=poll_s,
                                 validators=prepared.validators,
                                 idempotency_key=idempotency_key)
        return self.finish(task, outcome, probe, prepared)

    def resume(self, task: Any, ref: JobRef, workdir: str | Path,
               controller: JobController) -> Any:
        """Collect a job ``run`` left running (TIMEOUT), from its recorded reference."""
        self.check(task)
        probe = self.probe()
        work = Path(workdir).resolve() / task.name
        try:
            prepared = self.prepare(task, work, probe)
        except EngineRefusal as exc:
            return self.result(task, exc.status, str(exc), self._unrun(probe, str(exc)))
        outcome = controller.collect(ref, validators=prepared.validators)
        return self.finish(task, outcome, probe, prepared)

    @staticmethod
    def controller(work: Path) -> JobController:
        return JobController(LocalSubprocessJobs(work / "jobs"),
                             trace_path=work / "trace.json")

    def finish(self, task: Any, outcome: JobOutcome, probe: EngineProbe,
               prepared: Prepared) -> Any:
        run = EngineRun(engine=self.name, ran=outcome.ref is not None, version=probe.version,
                        interpreter=probe.interpreter, environment=probe.environment,
                        weights=prepared.weights, command=tuple(prepared.spec.argv),
                        job=outcome.ref.to_dict() if outcome.ref else None)
        if not outcome.ok:
            return self.result(task, outcome.status, outcome.error or outcome.status.value,
                               run, warnings=prepared.warnings)
        try:
            return self.read(task, outcome.artefacts, run, prepared)
        except (ValueError, KeyError, IndexError, OSError) as exc:
            return self.result(task, ExecutionStatus.FAILED,
                               f"the tool's outputs could not be read: {exc}", run,
                               warnings=prepared.warnings)

    def _unrun(self, probe: EngineProbe, note: str) -> EngineRun:
        return EngineRun(engine=self.name, ran=False, version=probe.version,
                         interpreter=probe.interpreter, environment=probe.environment,
                         note=note)

    def _check_version(self, task: Any, probe: EngineProbe) -> None:
        wanted = task.model.version
        if not wanted:
            return
        if not probe.version:
            raise EngineRefusal(f"the task requires {self.name} {wanted}, and the installed "
                                "version cannot be determined")
        if probe.version != wanted:
            raise EngineRefusal(f"{self.name} {probe.version} is installed; the task "
                                f"requires {wanted}")

    @staticmethod
    def _pinned(task: Any, digest: str, what: str) -> None:
        wanted = task.model.weights_sha256.lower()
        if wanted and wanted != digest:
            raise EngineRefusal(f"{what} hashes to {digest[:12]}…; the task requires "
                                f"{wanted[:12]}…")

    @abc.abstractmethod
    def prepare(self, task: Any, work: Path, probe: EngineProbe) -> Prepared:
        """The job that runs ``task``, or ``EngineRefusal``."""

    @abc.abstractmethod
    def read(self, task: Any, artefacts: Mapping[str, Artefact], run: EngineRun,
             prepared: Prepared) -> Any:
        """The task's result from validated artefacts."""

    @abc.abstractmethod
    def result(self, task: Any, status: ExecutionStatus, reason: str, run: EngineRun, *,
               warnings: list[str] | None = None) -> Any:
        """A result that carries no numbers: the status, the reason, the provenance."""


# ------------------------------------------------------------ complex prediction

def _residues(model: Structure, chain: str) -> list[tuple[tuple[int, str], str, list[float]]]:
    """((number, insertion code), residue name, atom B-factors) per residue, in order."""
    out: dict[tuple[int, str], tuple[str, list[float]]] = {}
    for a in model.atoms:
        if a.chain == chain:
            out.setdefault((a.resseq, a.icode), (a.resname, []))[1].append(a.bfactor)
    return [(k, name, bs) for k, (name, bs) in out.items()]


def _structure_check(task: ComplexPredictionTask,
                     mapping: Mapping[str, str]) -> Callable[[Path], "str | None"]:
    """A validator: the model holds exactly the task's chains, with their sequences."""
    def check(path: Path) -> str | None:
        try:
            model = read_mmcif(path)
        except ValueError as exc:
            return f"not a readable mmCIF model: {exc}"
        present = set(model.chains())
        expected = {mapping[c] for c in task.chain_kinds()}
        if present != expected:
            return (f"the model holds chains {sorted(present)}; the task asked for "
                    f"{sorted(expected)}")
        for cid, kind in task.chain_kinds().items():
            residues = _residues(model, mapping[cid])
            if kind == "ligand":
                continue
            chain = task.polymer(cid)
            if len(residues) != len(chain.sequence):
                return (f"chain {cid} has {len(residues)} residues in the model and "
                        f"{len(chain.sequence)} in the task")
            if kind == "protein":
                modified = {m.position for m in chain.modifications}
                for k, ((_, name, _), want) in enumerate(zip(residues, chain.sequence), 1):
                    if k not in modified and THREE_TO_ONE.get(name, "X") != want:
                        return (f"chain {cid} residue {k} is {name} in the model and {want} "
                                "in the task: this is a model of another sequence")
        return None
    return check


def _chain_confidence(task: ComplexPredictionTask, model: Structure,
                      mapping: Mapping[str, str],
                      ptm: Mapping[str, float]) -> dict[str, ChainConfidence]:
    """Mean pLDDT per chain (0-100) from the model's B-factors, per residue then per chain."""
    values = [a.bfactor for a in model.atoms]
    scale = 100.0 if values and max(values) <= 1.0 + 1e-9 else 1.0
    out = {}
    for cid, kind in task.chain_kinds().items():
        residues = _residues(model, mapping[cid])
        if kind == "ligand":
            atoms = [b for _, _, bs in residues for b in bs]
            units, plddt = len(atoms), (float(np.mean(atoms)) * scale if atoms else None)
        else:
            per = [float(np.mean(bs)) for _, _, bs in residues if bs]
            units, plddt = len(residues), (float(np.mean(per)) * scale if per else None)
        out[cid] = ChainConfidence(cid, kind, units,
                                   None if plddt is None else round(plddt, 2), ptm.get(cid))
    return out


class _ComplexEngine(Engine):
    def result(self, task: ComplexPredictionTask, status: ExecutionStatus, reason: str,
               run: EngineRun, *, warnings: list[str] | None = None
               ) -> ComplexPredictionResult:
        return ComplexPredictionResult(task=task.name, task_digest=task.digest(),
                                       status=status, reason=reason[:1500], provenance=run,
                                       warnings=list(warnings or []))

    @staticmethod
    def _common_warnings(task: ComplexPredictionTask) -> list[str]:
        out = []
        if task.ligands and not task.smiles_checked():
            out.append("RDKit is not installed here, so the ligand SMILES were not parsed "
                       "before running")
        if task.constraints:
            out.append("the task supplies constraints: the interfaces they describe are "
                       "inputs to the model, not findings of it")
        return out


class BoltzEngine(_ComplexEngine):
    """Boltz-1 and Boltz-2 (``boltz predict``), with weights pinned in a local cache.

    ``model.options``: ``cache`` (required; the directory holding the checkpoints and CCD
    data, which Boltz otherwise downloads at start-up), ``accelerator`` (``gpu`` or
    ``cpu``; default ``gpu``) and ``use_potentials`` (inference-time steering potentials).
    """

    name = "boltz"
    project = "boltz"
    distribution = "boltz"
    modules = ("boltz", "torch")
    models = ("boltz-1", "boltz-2")
    options = frozenset({"cache", "accelerator", "use_potentials"})
    #: What ``download_boltz1`` / ``download_boltz2`` fetch when missing from the cache.
    CACHE = {"boltz1": ("boltz1_conf.ckpt", "ccd.pkl"),
             "boltz2": ("boltz2_conf.ckpt", "boltz2_aff.ckpt", "mols")}

    def prepare(self, task: ComplexPredictionTask, work: Path,
                probe: EngineProbe) -> Prepared:
        opts = task.model.options
        version = "boltz2" if task.model.name == "boltz-2" else "boltz1"
        if not opts.get("cache"):
            raise EngineRefusal("Boltz downloads its weights and CCD data into its cache when "
                                "it starts; name the cache that already holds them "
                                "(model.options['cache']), so the weights that run are the "
                                "weights recorded")
        cache = Path(str(opts["cache"])).expanduser().resolve()
        missing = [n for n in self.CACHE[version] if not (cache / n).exists()]
        if missing:
            raise EngineRefusal(f"the Boltz cache {cache} lacks {', '.join(missing)}; Boltz "
                                "would download them from the internet when it starts")
        accelerator = str(opts.get("accelerator", "gpu"))
        if accelerator not in ("gpu", "cpu"):
            raise EngineRefusal(f"accelerator is gpu or cpu, not {accelerator!r}",
                                ExecutionStatus.FAILED)
        checkpoint = cache / f"{version}_conf.ckpt"
        digest = _weights_digest(checkpoint)
        self._pinned(task, digest, str(checkpoint))
        path = work / f"{task.name}.yaml"
        # JSON is YAML, and every string in it is quoted: a SMILES holding '#', '[' or '@'
        # cannot be misread as YAML syntax the way a plain scalar can.
        path.write_text(json.dumps(self.render(task), indent=2), encoding="utf-8")
        argv = [probe.interpreter, "-I", "-m", "boltz.main", "predict", str(path),
                "--out_dir", OUTPUT_TOKEN, "--cache", str(cache), "--model", version,
                "--seed", str(task.seed), "--diffusion_samples", str(task.samples),
                "--recycling_steps", str(task.recycling_steps),
                "--sampling_steps", str(task.sampling_steps), "--output_format", "mmcif",
                "--accelerator", accelerator, "--write_full_pae"]
        if task.use_msa_server:
            argv.append("--use_msa_server")
        if opts.get("use_potentials"):
            argv.append("--use_potentials")
        base = f"boltz_results_{task.name}/predictions/{task.name}"
        identity = {c: c for c in task.chain_kinds()}
        warnings = self._common_warnings(task)
        single = [cid for c in task.chains if c.kind == "protein" and not c.msa
                  and not task.use_msa_server for cid in c.ids]
        if single:
            warnings.append(f"chains {', '.join(single)} run in single-sequence mode (no "
                            "MSA), which lowers Boltz's accuracy")
        artefacts = (ArtefactSpec("model", f"{base}/{task.name}_model_0.cif"),
                     ArtefactSpec("confidence", f"{base}/confidence_{task.name}_model_0.json"),
                     ArtefactSpec("pae", f"{base}/pae_{task.name}_model_0.npz",
                                  required=False))
        return Prepared(
            spec=JobSpec(component_id="structure.engine.boltz", argv=tuple(argv),
                         artefacts=artefacts),
            validators={"model": _structure_check(task, identity),
                        "confidence": _boltz_confidence_check},
            weights={checkpoint.name: digest}, warnings=warnings,
            context={"mapping": identity})

    @staticmethod
    def _token(task: ComplexPredictionTask, t: Token) -> list[Any]:
        if task.chain_kinds()[t.chain] == "ligand":
            if not t.atom:
                raise EngineRefusal(f"Boltz names a ligand contact by atom name; give one for "
                                    f"chain {t.chain}", ExecutionStatus.FAILED)
            return [t.chain, t.atom]
        return [t.chain, t.index]

    def render(self, task: ComplexPredictionTask) -> dict[str, Any]:
        """The Boltz input document (``version: 1`` schema of docs/prediction.md)."""
        sequences: list[dict[str, Any]] = []
        for c in task.chains:
            entry: dict[str, Any] = {"id": list(c.ids) if len(c.ids) > 1 else c.ids[0],
                                     "sequence": c.sequence}
            if c.kind == "protein" and not task.use_msa_server:
                entry["msa"] = str(Path(c.msa).resolve()) if c.msa else "empty"
            if c.modifications:
                entry["modifications"] = [{"position": m.position, "ccd": m.ccd}
                                          for m in c.modifications]
            sequences.append({c.kind: entry})
        for lig in task.ligands:
            entry = {"id": list(lig.ids) if len(lig.ids) > 1 else lig.ids[0]}
            entry.update({"smiles": lig.smiles} if lig.smiles else {"ccd": lig.ccd})
            sequences.append({"ligand": entry})
        constraints: list[dict[str, Any]] = []
        for k, c in enumerate(task.constraints, start=1):
            distance = getattr(c, "max_distance", None)
            if distance is not None and not 4.0 <= distance <= 20.0:
                raise EngineRefusal(f"constraint {k}: Boltz supports max_distance from 4 to "
                                    f"20 Å, not {distance}", ExecutionStatus.FAILED)
            if isinstance(c, PocketConstraint):
                constraints.append({"pocket": {
                    "binder": c.binder, "max_distance": c.max_distance,
                    "contacts": [self._token(task, t) for t in c.contacts]}})
            elif isinstance(c, ContactConstraint):
                if task.model.name != "boltz-2":
                    raise EngineRefusal(f"constraint {k}: contact constraints are a Boltz-2 "
                                        "feature", ExecutionStatus.FAILED)
                constraints.append({"contact": {"token1": self._token(task, c.first),
                                                "token2": self._token(task, c.second),
                                                "max_distance": c.max_distance}})
            elif isinstance(c, BondConstraint):
                constraints.append({"bond": {"atom1": [c.first.chain, c.first.index,
                                                       c.first.atom],
                                             "atom2": [c.second.chain, c.second.index,
                                                       c.second.atom]}})
        doc: dict[str, Any] = {"version": 1, "sequences": sequences}
        if constraints:
            doc["constraints"] = constraints
        return doc

    def read(self, task: ComplexPredictionTask, artefacts: Mapping[str, Artefact],
             run: EngineRun, prepared: Prepared) -> ComplexPredictionResult:
        model = read_mmcif(artefacts["model"].path)
        conf = json.loads(artefacts["confidence"].path.read_text(encoding="utf-8"))
        # Boltz indexes chains by asym id, which is the order the model file lists them.
        order = list(dict.fromkeys(a.chain for a in model.atoms))

        def chain(index: str) -> str:
            if not str(index).isdigit() or int(index) >= len(order):
                raise ValueError(f"the confidence file names chain index {index}; the model "
                                 f"has {len(order)} chains")
            return order[int(index)]

        chain_ptm = {chain(k): float(v) for k, v in (conf.get("chains_ptm") or {}).items()}
        pair = {chain(a): {chain(b): float(v) for b, v in row.items()}
                for a, row in (conf.get("pair_chains_iptm") or {}).items()}
        metrics = {k: conf[k] for k in ("confidence_score", "ligand_iptm", "protein_iptm",
                                        "complex_plddt", "complex_iplddt", "complex_pde",
                                        "complex_ipde") if k in conf}
        pae = artefacts.get("pae")
        if pae is not None:
            with np.load(pae.path) as data:
                metrics["mean_pae"] = round(float(np.mean(data[data.files[0]])), 3)
        metrics["ranking"] = "model_0 is Boltz's top sample by confidence_score"
        return ComplexPredictionResult(
            task=task.name, task_digest=task.digest(), status=ExecutionStatus.SUCCEEDED,
            structure=artefacts["model"],
            chains=_chain_confidence(task, model, prepared.context["mapping"], chain_ptm),
            ptm=float(conf["ptm"]), iptm=float(conf["iptm"]), pair_iptm=pair, pae=pae,
            model_metrics=metrics, provenance=run, warnings=list(prepared.warnings))


def _boltz_confidence_check(path: Path) -> str | None:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return f"not JSON: {exc}"
    missing = [k for k in ("confidence_score", "ptm", "iptm", "complex_plddt")
               if not isinstance(doc.get(k), (int, float))]
    return f"no numeric {', '.join(missing)}" if missing else None


class ChaiEngine(_ComplexEngine):
    """Chai-1 (``chai-lab fold``), with its exported weights pinned in a local directory.

    ``model.options``: ``downloads`` (required; ``CHAI_DOWNLOADS_DIR``, holding
    ``models_v2/*.pt`` and ``conformers_v1.apkl``, which Chai-1 otherwise downloads),
    ``device`` (default ``cuda:0``) and ``use_esm_embeddings`` (default False here,
    because Chai-1 downloads the ESM-2 weights it needs when they are absent).

    Chai-1 names chains A, B, C ... in input order whatever they are called, takes
    ligands as SMILES only, and writes its samples in sampling order, not ranked; the
    adapter maps chain ids both ways, refuses CCD-coded ligands rather than converting
    them, and reports the sample with the highest aggregate score.
    """

    name = "chai-1"
    project = "chai_lab"
    distribution = "chai_lab"
    modules = ("chai_lab", "torch")
    models = ("chai-1",)
    options = frozenset({"downloads", "device", "use_esm_embeddings"})
    COMPONENTS = ("feature_embedding.pt", "bond_loss_input_proj.pt", "token_embedder.pt",
                  "trunk.pt", "diffusion_module.pt", "confidence_head.pt")
    SCORES = ("aggregate_score", "ptm", "iptm", "per_chain_ptm", "per_chain_pair_iptm")

    def prepare(self, task: ComplexPredictionTask, work: Path,
                probe: EngineProbe) -> Prepared:
        opts = task.model.options
        if not opts.get("downloads"):
            raise EngineRefusal("Chai-1 downloads its weights at run time; name the directory "
                                "that already holds them (model.options['downloads'], its "
                                "CHAI_DOWNLOADS_DIR)")
        downloads = Path(str(opts["downloads"])).expanduser().resolve()
        files = [downloads / "models_v2" / c for c in self.COMPONENTS]
        missing = [str(p.relative_to(downloads)) for p in
                   [*files, downloads / "conformers_v1.apkl"] if not p.is_file()]
        if missing:
            raise EngineRefusal(f"{downloads} lacks {', '.join(missing)}; Chai-1 would "
                                "download them when it starts")
        weights = {f"models_v2/{p.name}": _weights_digest(p) for p in files}
        # One digest for the set: SHA-256 over "name:digest" lines in name order.
        combined = hashlib.sha256("".join(f"{k}:{v}\n" for k, v in sorted(weights.items()))
                                  .encode("utf-8")).hexdigest()
        self._pinned(task, combined, "the Chai-1 weights (combined digest)")
        mapping = self.mapping(task)
        fasta = work / f"{task.name}.fasta"
        fasta.write_text(self.render_fasta(task), encoding="utf-8")
        argv = [probe.interpreter, "-I", "-m", "chai_lab.main", "fold", str(fasta),
                OUTPUT_TOKEN, "--seed", str(task.seed),
                "--num-diffn-samples", str(task.samples),
                "--num-trunk-recycles", str(task.recycling_steps),
                "--num-diffn-timesteps", str(task.sampling_steps),
                "--device", str(opts.get("device", "cuda:0")),
                "--use-esm-embeddings" if opts.get("use_esm_embeddings")
                else "--no-use-esm-embeddings"]
        if task.use_msa_server:
            argv.append("--use-msa-server")
        rows = self.render_restraints(task, mapping)
        if rows:
            restraints = work / f"{task.name}.restraints.csv"
            with restraints.open("w", newline="", encoding="utf-8") as fh:
                writer = csv.writer(fh)
                writer.writerow(["restraint_id", "chainA", "res_idxA", "chainB", "res_idxB",
                                 "connection_type", "confidence", "min_distance_angstrom",
                                 "max_distance_angstrom", "comment"])
                writer.writerows(rows)
            argv += ["--constraint-path", str(restraints)]
        artefacts, validators = [], {}
        check = _structure_check(task, mapping)
        for i in range(task.samples):
            artefacts += [ArtefactSpec(f"model_{i}", f"pred.model_idx_{i}.cif"),
                          ArtefactSpec(f"scores_{i}", f"scores.model_idx_{i}.npz")]
            validators[f"model_{i}"] = check
            validators[f"scores_{i}"] = self._scores_check
        warnings = self._common_warnings(task)
        if not task.use_msa_server:
            warnings.append("no MSA: Chai-1 runs on single sequences"
                            + (" with ESM-2 embeddings" if opts.get("use_esm_embeddings")
                               else " without ESM-2 embeddings, which lowers its accuracy"))
        return Prepared(
            spec=JobSpec(component_id="structure.engine.chai-1", argv=tuple(argv),
                         artefacts=tuple(artefacts),
                         env={"CHAI_DOWNLOADS_DIR": str(downloads)}),
            validators=validators, weights={**weights, "combined": combined},
            warnings=warnings, context={"mapping": mapping})

    @staticmethod
    def mapping(task: ComplexPredictionTask) -> dict[str, str]:
        """Task chain id -> the letter Chai-1 gives it (A, B, C ... in input order)."""
        ids = list(task.chain_kinds())
        if len(ids) > 26:
            raise EngineRefusal(f"Chai-1 letters chains A-Z; the task has {len(ids)}",
                                ExecutionStatus.FAILED)
        return {cid: chr(ord("A") + k) for k, cid in enumerate(ids)}

    @staticmethod
    def render_fasta(task: ComplexPredictionTask) -> str:
        lines = []
        for c in task.chains:
            if c.msa:
                raise EngineRefusal("this adapter does not pass MSA files to Chai-1, which "
                                    "reads them as aligned.pqt; drop the MSA or use "
                                    "use_msa_server", ExecutionStatus.FAILED)
            seq = list(c.sequence)
            for m in c.modifications:
                seq[m.position - 1] = f"({m.ccd})"
            for cid in c.ids:
                lines += [f">{c.kind}|name={cid}", "".join(seq)]
        for lig in task.ligands:
            if not lig.smiles:
                raise EngineRefusal(
                    f"Chai-1 takes ligands as SMILES; ligand {'/'.join(lig.ids)} is given by "
                    f"CCD code {lig.ccd}, and converting it here would choose its "
                    "protonation and stereochemistry", ExecutionStatus.FAILED)
            for cid in lig.ids:
                lines += [f">ligand|name={cid}", lig.smiles]
        return "\n".join(lines) + "\n"

    @staticmethod
    def render_restraints(task: ComplexPredictionTask,
                          mapping: Mapping[str, str]) -> list[list[Any]]:
        """Rows of Chai-1's restraint table (examples/restraints/README.md)."""
        kinds = task.chain_kinds()

        def residue(t: Token, k: int) -> str:
            if kinds[t.chain] != "protein":
                raise EngineRefusal(f"constraint {k}: this adapter renders Chai-1 restraints "
                                    "on protein residues only", ExecutionStatus.FAILED)
            return f"{task.polymer(t.chain).sequence[t.index - 1]}{t.index}"

        rows: list[list[Any]] = []
        for k, c in enumerate(task.constraints, start=1):
            if isinstance(c, PocketConstraint):
                for j, t in enumerate(c.contacts):
                    rows.append([f"pocket{k}_{j}", mapping[c.binder], "", mapping[t.chain],
                                 residue(t, k), "pocket", 1.0, 0.0, c.max_distance, ""])
            elif isinstance(c, ContactConstraint):
                if c.first.chain == c.second.chain:
                    raise EngineRefusal(f"constraint {k}: Chai-1 contacts join two chains",
                                        ExecutionStatus.FAILED)
                rows.append([f"contact{k}", mapping[c.first.chain], residue(c.first, k),
                             mapping[c.second.chain], residue(c.second, k), "contact", 1.0,
                             0.0, c.max_distance, ""])
            else:
                raise EngineRefusal(f"constraint {k}: Chai-1 takes covalent bonds through its "
                                    "Python API, which this adapter does not use",
                                    ExecutionStatus.FAILED)
        return rows

    @classmethod
    def _scores_check(cls, path: Path) -> str | None:
        try:
            with np.load(path) as data:
                missing = [k for k in cls.SCORES if k not in data.files]
        except (OSError, ValueError) as exc:
            return f"not a readable npz: {exc}"
        return f"no {', '.join(missing)}" if missing else None

    def read(self, task: ComplexPredictionTask, artefacts: Mapping[str, Artefact],
             run: EngineRun, prepared: Prepared) -> ComplexPredictionResult:
        mapping = prepared.context["mapping"]
        letters = {v: k for k, v in mapping.items()}
        scores = []
        for i in range(task.samples):
            with np.load(artefacts[f"scores_{i}"].path) as data:
                scores.append({k: np.asarray(data[k]) for k in data.files})
        aggregate = [float(s["aggregate_score"].reshape(-1)[0]) for s in scores]
        best = int(np.argmax(aggregate))
        s = scores[best]
        order = sorted(letters)                     # Chai-1's chain index order: A, B, ...
        per_chain = s["per_chain_ptm"].reshape(-1)
        pair = s["per_chain_pair_iptm"].reshape(len(order), len(order))
        chain_ptm = {letters[order[i]]: float(per_chain[i]) for i in range(len(order))}
        model = read_mmcif(artefacts[f"model_{best}"].path)
        warnings = list(prepared.warnings)
        metrics: dict[str, Any] = {
            "aggregate_score": aggregate[best], "aggregate_scores": aggregate,
            "chosen_sample": best,
            "ranking": ("Chai-1 writes samples in sampling order; the reported model is the "
                        "sample with the highest aggregate score")}
        if "has_inter_chain_clashes" in s:
            clashes = bool(s["has_inter_chain_clashes"].reshape(-1)[0])
            metrics["has_inter_chain_clashes"] = clashes
            if clashes:
                warnings.append("Chai-1 flags inter-chain clashes in the reported model")
        return ComplexPredictionResult(
            task=task.name, task_digest=task.digest(), status=ExecutionStatus.SUCCEEDED,
            structure=artefacts[f"model_{best}"],
            chains=_chain_confidence(task, model, mapping, chain_ptm),
            ptm=float(s["ptm"].reshape(-1)[0]), iptm=float(s["iptm"].reshape(-1)[0]),
            pair_iptm={letters[order[i]]: {letters[order[j]]: float(pair[i, j])
                                           for j in range(len(order))}
                       for i in range(len(order))},
            model_metrics=metrics, provenance=run, warnings=warnings)


# -------------------------------------------------------------- sequence design

#: Runs ProteinMPNN's script from its checkout under ``-I``, which (since Python 3.11)
#: no longer puts the script's directory on the path; the checkout is added explicitly.
_RUN_MPNN = (
    "import runpy, sys\n"
    "root = sys.argv[1]\n"
    "sys.path.insert(0, root)\n"
    "sys.argv = [root + '/protein_mpnn_run.py'] + sys.argv[2:]\n"
    "runpy.run_path(sys.argv[0], run_name='__main__')\n")

_HEADER = re.compile(r"(\w+)=(\[[^\]]*\]|[^,]*)")


def _mpnn_fields(header: str) -> dict[str, str]:
    return {k: v.strip() for k, v in _HEADER.findall(header)}


def _chains_listed(text: str) -> list[str]:
    return re.findall(r"'([^']*)'", text)


def _fasta(path: Path) -> list[tuple[str, str]]:
    records: list[tuple[str, str]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(">"):
            records.append((line[1:].strip(), ""))
        elif line.strip() and records:
            records[-1] = (records[-1][0], records[-1][1] + line.strip())
    return records


class ProteinMPNNEngine(Engine):
    """ProteinMPNN from its checkout (``protein_mpnn_run.py``), in its own environment.

    The reviewed environment names the interpreter and, as ``root``, the checkout, whose
    git commit is recorded as the version. ``model.options``: ``model_name``
    (``v_48_002`` ... ``v_48_030``; default ``v_48_020``) and ``use_soluble_model``.
    """

    name = "proteinmpnn"
    project = "ProteinMPNN"
    modules = ("torch", "numpy")
    models = ("proteinmpnn",)
    options = frozenset({"model_name", "use_soluble_model"})
    MODEL_NAMES = ("v_48_002", "v_48_010", "v_48_020", "v_48_030")
    SOLUBLE = ("v_48_010", "v_48_020")

    def _installed(self, probe: EngineProbe) -> EngineProbe:
        root = Path(probe.root) if probe.root else None
        if root is None or not (root / "protein_mpnn_run.py").is_file():
            return EngineProbe(False, probe.interpreter, probe.environment, reason=(
                "ProteinMPNN runs from its checkout, and no reviewed environment names one "
                f"(interpreters[{self.project!r}].root holding protein_mpnn_run.py)"))
        head = root / ".git" / "HEAD"
        version = "unknown commit (not a git checkout)"
        if head.is_file():
            ref = head.read_text(encoding="utf-8").strip()
            target = root / ".git" / ref[5:] if ref.startswith("ref: ") else None
            version = (target.read_text(encoding="utf-8").strip()
                       if target is not None and target.is_file() else ref)
        return EngineProbe(True, probe.interpreter, probe.environment, version,
                           root=str(root))

    def result(self, task: SequenceDesignTask, status: ExecutionStatus, reason: str,
               run: EngineRun, *, warnings: list[str] | None = None) -> SequenceDesignResult:
        return SequenceDesignResult(task=task.name, task_digest=task.digest(), status=status,
                                    reason=reason[:1500], provenance=run,
                                    warnings=list(warnings or []))

    def prepare(self, task: SequenceDesignTask, work: Path, probe: EngineProbe) -> Prepared:
        opts = task.model.options
        model_name = str(opts.get("model_name", "v_48_020"))
        soluble = bool(opts.get("use_soluble_model"))
        allowed = self.SOLUBLE if soluble else self.MODEL_NAMES
        if model_name not in allowed:
            raise EngineRefusal(f"model_name is one of {allowed}"
                                + (" for the soluble weights" if soluble else ""),
                                ExecutionStatus.FAILED)
        root = Path(probe.root)
        weights_dir = root / ("soluble_model_weights" if soluble else "vanilla_model_weights")
        weights = weights_dir / f"{model_name}.pt"
        if not weights.is_file():
            raise EngineRefusal(f"the ProteinMPNN weights {weights} are not in the checkout")
        digest = _weights_digest(weights)
        self._pinned(task, digest, str(weights))
        backbone = Path(task.backbone).resolve()
        name = backbone.name[:-4]                  # how ProteinMPNN names the design
        argv = [probe.interpreter, "-I", "-c", _RUN_MPNN, str(root),
                "--pdb_path", str(backbone), "--pdb_path_chains", " ".join(task.design_chains),
                "--out_folder", OUTPUT_TOKEN, "--num_seq_per_target", str(task.num_sequences),
                "--sampling_temp", f"{task.temperature:g}", "--seed", str(task.seed),
                "--batch_size", str(task.batch_size), "--model_name", model_name,
                "--path_to_model_weights", str(weights_dir)]
        if task.fixed_positions:
            # Every designed chain needs an entry, empty or not: tied_featurize looks each
            # one up by name.
            fixed = {name: {c: sorted(task.fixed_positions.get(c, ()))
                            for c in task.chains()}}
            path = work / "fixed_positions.jsonl"
            path.write_text(json.dumps(fixed) + "\n", encoding="utf-8")
            argv += ["--fixed_positions_jsonl", str(path)]
        expected = 1 + task.num_sequences
        return Prepared(
            spec=JobSpec(component_id="structure.engine.proteinmpnn", argv=tuple(argv),
                         artefacts=(ArtefactSpec("sequences", f"seqs/{name}.fa"),)),
            validators={"sequences": lambda p: None if len(_fasta(p)) == expected else (
                f"{len(_fasta(p))} records; expected the native and {task.num_sequences} "
                "designs")},
            weights={f"{weights_dir.name}/{weights.name}": digest},
            context={"model_name": model_name, "soluble": soluble})

    def read(self, task: SequenceDesignTask, artefacts: Mapping[str, Artefact],
             run: EngineRun, prepared: Prepared) -> SequenceDesignResult:
        records = _fasta(artefacts["sequences"].path)
        native_fields = _mpnn_fields(records[0][0])
        designed = sorted(task.design_chains)    # ProteinMPNN writes them in letter order
        reported = _chains_listed(native_fields.get("designed_chains", ""))
        if reported != designed:
            raise ValueError(f"ProteinMPNN designed chains {reported}, not {designed}")
        if native_fields.get("seed") != str(task.seed):
            raise ValueError(f"ProteinMPNN ran with seed {native_fields.get('seed')}, not "
                             f"{task.seed}")
        if native_fields.get("model_name") != prepared.context["model_name"]:
            raise ValueError(f"ProteinMPNN ran {native_fields.get('model_name')}, not "
                             f"{prepared.context['model_name']}")
        native = dict(zip(designed, records[0][1].split("/")))
        designs = []
        for header, seq in records[1:]:
            fields = _mpnn_fields(header)
            parts = seq.split("/")
            if len(parts) != len(designed):
                raise ValueError(f"a design holds {len(parts)} chains; {len(designed)} were "
                                 "designed")
            chains = dict(zip(designed, parts))
            for chain, positions in task.fixed_positions.items():
                for p in positions:
                    if chains[chain][p - 1] != native[chain][p - 1]:
                        raise ValueError(f"design {fields.get('sample')} changed fixed "
                                         f"position {chain}{p}: the fixed positions were not "
                                         "applied")
            designs.append(DesignedSequence(
                sample=int(fields["sample"]), sequences=chains, score=float(fields["score"]),
                global_score=float(fields["global_score"]),
                recovery=float(fields["seq_recovery"]), temperature=float(fields["T"])))
        return SequenceDesignResult(
            task=task.name, task_digest=task.digest(), status=ExecutionStatus.SUCCEEDED,
            native=native, native_score=float(native_fields["score"]),
            native_global_score=float(native_fields["global_score"]), designs=designs,
            fasta=artefacts["sequences"],
            model_metrics={"model_name": prepared.context["model_name"],
                           "soluble_weights": prepared.context["soluble"],
                           "git_hash": native_fields.get("git_hash", ""),
                           "fixed_chains": _chains_listed(native_fields.get("fixed_chains",
                                                                            ""))},
            provenance=run, warnings=list(prepared.warnings))


# ----------------------------------------------------------- molecular dynamics

#: The protocol OpenMM runs, as a script for OpenMM's own interpreter; it reads the task
#: from the configuration file and writes what ``OpenMMEngine.read`` parses.
_OPENMM_SCRIPT = '''"""Relaxation and dynamics for one bioagent DynamicsTask."""
import json
import sys

import openmm
from openmm import app, unit

cfg = json.load(open(sys.argv[1], encoding="utf-8"))
out = sys.argv[2]
path = cfg["structure"]
pdb = app.PDBxFile(path) if path.lower().endswith((".cif", ".mmcif")) else app.PDBFile(path)
ff = app.ForceField(*cfg["force_field"])
modeller = app.Modeller(pdb.topology, pdb.positions)
if cfg["add_hydrogens_ph"] is not None:
    modeller.addHydrogens(ff, pH=cfg["add_hydrogens_ph"])
if cfg["solvent"] == "explicit":
    modeller.addSolvent(ff, padding=cfg["padding_nm"] * unit.nanometer,
                        ionicStrength=cfg["ionic_strength_molar"] * unit.molar,
                        positiveIon=cfg["positive_ion"], negativeIon=cfg["negative_ion"])
    system = ff.createSystem(modeller.topology, nonbondedMethod=app.PME,
                             nonbondedCutoff=1.0 * unit.nanometer, constraints=app.HBonds)
else:
    system = ff.createSystem(modeller.topology, nonbondedMethod=app.NoCutoff,
                             constraints=app.HBonds)
integrator = openmm.LangevinMiddleIntegrator(cfg["temperature_k"] * unit.kelvin,
                                             cfg["friction_per_ps"] / unit.picosecond,
                                             cfg["timestep_fs"] * unit.femtoseconds)
integrator.setRandomNumberSeed(cfg["seed"])
if cfg["platform"]:
    simulation = app.Simulation(modeller.topology, system, integrator,
                                openmm.Platform.getPlatformByName(cfg["platform"]))
else:
    simulation = app.Simulation(modeller.topology, system, integrator)
simulation.context.setPositions(modeller.positions)


def energy():
    state = simulation.context.getState(getEnergy=True)
    return state.getPotentialEnergy().value_in_unit(unit.kilojoule_per_mole)


def write(name):
    state = simulation.context.getState(getPositions=True)
    with open(out + "/" + name, "w", encoding="utf-8") as fh:
        app.PDBFile.writeFile(simulation.topology, state.getPositions(), fh, keepIds=True)


initial = energy()
if cfg["minimise"]:
    simulation.minimizeEnergy()
minimised = energy()
write("relaxed.pdb")
if cfg["steps"]:
    simulation.context.setVelocitiesToTemperature(cfg["temperature_k"] * unit.kelvin,
                                                  cfg["seed"])
    simulation.reporters.append(app.DCDReporter(out + "/trajectory.dcd", cfg["report_steps"]))
    simulation.reporters.append(app.StateDataReporter(
        out + "/energies.csv", cfg["report_steps"], step=True, time=True,
        potentialEnergy=True, kineticEnergy=True, temperature=True, volume=True))
    simulation.step(cfg["steps"])
    write("final.pdb")
platform = simulation.context.getPlatform()
precision = (platform.getPropertyValue(simulation.context, "Precision")
             if "Precision" in platform.getPropertyNames() else "")
with open(out + "/run.json", "w", encoding="utf-8") as fh:
    json.dump({"openmm": getattr(openmm, "__version__", "") or openmm.version.version,
               "platform": platform.getName(), "precision": precision,
               "initial_energy_kj_mol": initial, "minimised_energy_kj_mol": minimised,
               "n_atoms": system.getNumParticles(), "steps": cfg["steps"]}, fh)
'''

_RUN_KEYS = ("openmm", "platform", "initial_energy_kj_mol", "minimised_energy_kj_mol",
             "n_atoms", "steps")


def _energies(path: Path) -> list[EnergyRecord]:
    """Rows of OpenMM's StateDataReporter CSV (``#"Step","Time (ps)",...`` header)."""
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or not lines[0].startswith("#"):
        raise ValueError("no StateDataReporter header")
    header = next(csv.reader([lines[0][1:]]))
    col = {name.split(" (")[0].strip().lower(): k for k, name in enumerate(header)}
    need = ("step", "time", "potential energy", "kinetic energy", "temperature")
    if any(n not in col for n in need):
        raise ValueError(f"the energy table lacks {[n for n in need if n not in col]}")
    out = []
    for row in csv.reader(lines[1:]):
        if not row:
            continue
        values = [float(v) for v in row]
        if not all(math.isfinite(v) for v in values):
            raise ValueError(f"a non-finite energy at step {row[col['step']]}")
        out.append(EnergyRecord(step=int(values[col["step"]]), time_ps=values[col["time"]],
                                potential_kj_mol=values[col["potential energy"]],
                                kinetic_kj_mol=values[col["kinetic energy"]],
                                temperature_k=values[col["temperature"]],
                                volume_nm3=values[col["box volume"]] if "box volume" in col
                                else None))
    return out


def _check_json(path: Path) -> str | None:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return f"not JSON: {exc}"
    missing = [k for k in _RUN_KEYS if k not in doc]
    if missing:
        return f"lacks {', '.join(missing)}"
    if not all(math.isfinite(float(doc[k])) for k in ("initial_energy_kj_mol",
                                                       "minimised_energy_kj_mol")):
        return "a non-finite energy: the system blew up"
    return None


def _check_pdb(path: Path) -> str | None:
    try:
        read_pdb(path)
    except (ValueError, OSError) as exc:
        return f"not a readable PDB: {exc}"
    return None


def _check_dcd(path: Path) -> str | None:
    with path.open("rb") as fh:
        head = fh.read(8)
    # A DCD file opens with a Fortran record marker (84) and the magic word CORD.
    return None if head[4:8] == b"CORD" else "not a DCD trajectory (no CORD header)"


class OpenMMEngine(Engine):
    """Minimisation and Langevin dynamics with OpenMM, in OpenMM's own interpreter."""

    name = "openmm"
    project = "openmm"
    distribution = "openmm"
    modules = ("openmm",)
    models = ("openmm",)

    def result(self, task: DynamicsTask, status: ExecutionStatus, reason: str,
               run: EngineRun, *, warnings: list[str] | None = None) -> DynamicsResult:
        return DynamicsResult(task=task.name, task_digest=task.digest(), status=status,
                              reason=reason[:1500], provenance=run,
                              warnings=list(warnings or []))

    def prepare(self, task: DynamicsTask, work: Path, probe: EngineProbe) -> Prepared:
        config = {k: getattr(task, k) for k in (
            "force_field", "solvent", "padding_nm", "ionic_strength_molar", "positive_ion",
            "negative_ion", "add_hydrogens_ph", "minimise", "timestep_fs", "temperature_k",
            "friction_per_ps", "seed", "platform")}
        config.update(structure=str(Path(task.structure).resolve()),
                      force_field=list(task.force_field), steps=task.steps,
                      report_steps=task.report_steps)
        config_path = work / "openmm_config.json"
        config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
        script = work / "openmm_run.py"
        script.write_text(_OPENMM_SCRIPT, encoding="utf-8")
        artefacts = [ArtefactSpec("relaxed", "relaxed.pdb"), ArtefactSpec("run", "run.json")]
        validators: dict[str, Callable[[Path], str | None]] = {
            "relaxed": _check_pdb, "run": _check_json}
        if task.steps:
            frames = task.steps // task.report_steps
            artefacts += [ArtefactSpec("trajectory", "trajectory.dcd"),
                          ArtefactSpec("energies", "energies.csv"),
                          ArtefactSpec("final", "final.pdb")]
            validators.update(
                trajectory=_check_dcd, final=_check_pdb,
                energies=lambda p: None if len(_energies(p)) == frames
                else f"{len(_energies(p))} energy rows; expected {frames}")
        warnings = []
        if task.add_hydrogens_ph is not None:
            warnings.append(f"protonation from OpenMM's residue templates at pH "
                            f"{task.add_hydrogens_ph:g}; no pKa model was applied")
        return Prepared(
            spec=JobSpec(component_id="structure.engine.openmm",
                         argv=(probe.interpreter, "-I", str(script), str(config_path),
                               OUTPUT_TOKEN), artefacts=tuple(artefacts)),
            validators=validators, warnings=warnings)

    def read(self, task: DynamicsTask, artefacts: Mapping[str, Artefact], run: EngineRun,
             prepared: Prepared) -> DynamicsResult:
        doc = json.loads(artefacts["run"].path.read_text(encoding="utf-8"))
        energies = _energies(artefacts["energies"].path) if "energies" in artefacts else []
        return DynamicsResult(
            task=task.name, task_digest=task.digest(), status=ExecutionStatus.SUCCEEDED,
            initial_energy_kj_mol=float(doc["initial_energy_kj_mol"]),
            minimised_energy_kj_mol=float(doc["minimised_energy_kj_mol"]),
            relaxed=artefacts["relaxed"], final=artefacts.get("final"),
            trajectory=artefacts.get("trajectory"), energy_table=artefacts.get("energies"),
            energies=energies,
            model_metrics={"openmm": doc["openmm"], "platform": doc["platform"],
                           "precision": doc.get("precision", ""), "n_atoms": doc["n_atoms"],
                           "steps": doc["steps"]},
            provenance=run, warnings=list(prepared.warnings))


ENGINES: dict[str, type[Engine]] = {"boltz": BoltzEngine, "chai-1": ChaiEngine,
                                    "proteinmpnn": ProteinMPNNEngine, "openmm": OpenMMEngine}
