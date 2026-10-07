"""Reviewed execution environments: which interpreter runs a provider, what a container sees.

Two defects made this necessary. ``SubprocessBackend`` always ran ``sys.executable -I``, so
an upstream project installed in its own virtualenv or conda environment (ProteinMPNN's
``mlfold``, a Boltz install with its own torch) could not be selected: the call ran in the
harness's interpreter, failed to import, and was recorded as the component failing. And
``ContainerBackend`` ran ``docker run --rm --network none`` with nothing else: no GPU, no
data, nowhere to write, so a GPU tool could not run in a container at all.

Both are facts about this machine that widen what a component can touch (an interpreter,
a GPU, a host directory, a writable path), so they are not read from the component's
manifest, which a proposal can edit. They come from one JSON file a person reviews, and
the review binds to the content: the file carries the SHA-256 of its own body, and a body
that no longer matches (a mount added after review, an interpreter re-pointed) is refused
as a whole rather than partly honoured. The digest proves the configuration is the one
that was reviewed. It cannot prove who reviewed it.

    {"interpreters": {"ProteinMPNN": {"prefix": "/opt/conda/envs/mlfold",
                                      "root": "/opt/ProteinMPNN"}},
     "containers": {"structure.tool.boltz": {"gpus": {"devices": ["0"]},
                                              "data": {"msa": "/srv/msa"},
                                              "output": "/scratch/boltz"}},
     "review": {"by": "...", "on": "2026-10-07", "sha256": "<digest of the two keys above>"}}

``python`` names an interpreter; ``prefix`` names a virtualenv or conda environment whose
``bin/python`` is used. Data roots are mounted read-only at ``/data/<name>``, the one
output directory read-write at ``/out``. ``network`` is ``none`` unless the reviewed
profile says ``bridge``; host networking is never offered.

Only the standard library is used: this module is imported by the backends, which every
skill's import closure reaches.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

__all__ = ["EnvironmentConfigError", "Interpreter", "GPURequest", "ContainerProfile",
           "ExecutionEnvironments", "review_digest", "reviewed", "ENV_ENVIRONMENTS"]

#: Environment variable naming the reviewed configuration file.
ENV_ENVIRONMENTS = "BIOAGENT_ENVIRONMENTS"

_NAME = re.compile(r"[A-Za-z0-9_.-]+")
_NETWORKS = ("none", "bridge")
_BODY_KEYS = ("interpreters", "containers")


class EnvironmentConfigError(ValueError):
    """The configuration is malformed, unreviewed, or changed since it was reviewed."""


def review_digest(body: Mapping[str, Any]) -> str:
    """SHA-256 of the reviewed part of a configuration (everything but ``review``)."""
    import hashlib

    blob = json.dumps({k: body.get(k, {}) for k in _BODY_KEYS}, sort_keys=True,
                      separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def reviewed(body: Mapping[str, Any], *, by: str, on: str) -> dict[str, Any]:
    """The document a reviewer signs off: ``body`` with a review block bound to its digest.

    For the reviewer's tooling and for tests. Calling it is the claim that ``by`` read the
    body; nothing here can check that claim.
    """
    if not by.strip() or not on.strip():
        raise EnvironmentConfigError("a review names who reviewed it and when")
    doc = {k: body.get(k, {}) for k in _BODY_KEYS}
    doc["review"] = {"by": by, "on": on, "sha256": review_digest(doc)}
    return doc


def _absolute(raw: Any, what: str) -> Path:
    text = str(raw or "").strip()
    if not text:
        raise EnvironmentConfigError(f"{what} is empty")
    path = Path(text).expanduser()
    if not path.is_absolute():
        # A relative path means whatever the working directory makes of it, which is not
        # something a reviewer can have read.
        raise EnvironmentConfigError(f"{what} {text!r} is not an absolute path")
    return path


#: Interpreter versions already measured, by (path, mtime): one process per interpreter
#: for the life of this one, and a re-pointed or reinstalled interpreter is measured again.
_VERSIONS: dict[tuple[str, float], str] = {}


@dataclass(frozen=True)
class Interpreter:
    """The Python a provider project runs in, and the checkout it runs from (if any)."""

    project: str
    python: Path
    root: Path | None = None
    declared_as: str = "python"          # "python" or "prefix"

    def problem(self) -> str:
        """Why this interpreter cannot be used here, or "" when it can."""
        if not self.python.exists():
            return (f"the reviewed environment names {self.python} as the interpreter for "
                    f"{self.project!r}, and it does not exist on this machine")
        if not os.access(self.python, os.X_OK):
            return f"{self.python} (interpreter for {self.project!r}) is not executable"
        if self.root is not None and not self.root.is_dir():
            return (f"the reviewed environment names {self.root} as the checkout of "
                    f"{self.project!r}, and it is not a directory")
        return ""

    def version(self, *, timeout_s: float = 20.0) -> tuple[str, str]:
        """(Python version, problem). Running it is the only proof the interpreter works.

        A virtualenv whose base interpreter was removed still has a ``bin/python`` that
        exists and is executable; it fails only when started.
        """
        problem = self.problem()
        if problem:
            return "", problem
        try:
            key = (str(self.python), self.python.stat().st_mtime)
        except OSError as exc:
            return "", f"{self.python} cannot be read: {exc}"
        if key in _VERSIONS:
            return _VERSIONS[key], ""
        try:
            proc = subprocess.run(  # noqa: S603 - a reviewed interpreter, no shell
                [str(self.python), "-I", "-c",
                 "import platform; print(platform.python_version())"],
                capture_output=True, text=True, timeout=timeout_s, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return "", f"{self.python} did not start: {type(exc).__name__}: {exc}"
        if proc.returncode != 0 or not proc.stdout.strip():
            tail = (proc.stderr or "").strip().splitlines()
            return "", (f"{self.python} did not start"
                        + (f": {tail[-1][:200]}" if tail else ""))
        _VERSIONS[key] = proc.stdout.strip()
        return _VERSIONS[key], ""


@dataclass(frozen=True)
class GPURequest:
    """GPUs for a container: a count, or named devices — never both."""

    count: int = 0
    devices: tuple[str, ...] = ()

    def argv(self, runtime: str) -> list[str]:
        """The flags for ``runtime`` (docker, podman or nerdctl).

        Docker and nerdctl take ``--gpus``; a device list must reach them as one quoted
        CSV value (``"device=0,1"``), or the comma splits it into two malformed options.
        Podman selects GPUs through CDI device names, which have no count form, so a count
        is refused there rather than silently becoming "all".
        """
        name = Path(runtime).name
        if name == "podman":
            if not self.devices:
                raise EnvironmentConfigError(
                    "podman selects GPUs by CDI device (nvidia.com/gpu=<index>); the "
                    "reviewed profile gives a count, which podman cannot express")
            out: list[str] = []
            for dev in self.devices:
                out += ["--device", f"nvidia.com/gpu={dev}"]
            return out
        if self.devices:
            return ["--gpus", '"device=' + ",".join(self.devices) + '"']
        return ["--gpus", f"count={self.count}"]


@dataclass(frozen=True)
class ContainerProfile:
    """What one container component may see beyond its image: GPUs, data, one output."""

    component_id: str
    gpus: GPURequest | None = None
    data: Mapping[str, Path] = field(default_factory=dict)
    output: Path | None = None
    network: str = "none"

    def problems(self) -> list[str]:
        """Host-side facts that make the profile unusable here, each with its reason."""
        out = []
        for name, path in sorted(self.data.items()):
            if not path.is_dir():
                out.append(f"data root {name!r} ({path}) is not a directory on this machine")
        if self.output is not None and not self.output.is_dir():
            out.append(f"the output directory {self.output} does not exist on this machine")
        return out

    def mount_argv(self) -> list[str]:
        """``--mount`` flags: data read-only at /data/<name>, the output read-write at /out.

        ``readonly=true`` rather than ``-v ...:ro`` because ``--mount`` refuses a source
        that does not exist, where ``-v`` creates an empty directory and the component
        reads nothing without being told.
        """
        out: list[str] = []
        for name, path in sorted(self.data.items()):
            out += ["--mount", f"type=bind,source={path},target=/data/{name},readonly=true"]
        if self.output is not None:
            out += ["--mount", f"type=bind,source={self.output},target=/out"]
        return out


def _gpus(raw: Any, where: str) -> GPURequest | None:
    if raw in (None, {}):
        return None
    if not isinstance(raw, Mapping):
        raise EnvironmentConfigError(f"{where}.gpus is an object with 'count' or 'devices'")
    count, devices = raw.get("count"), raw.get("devices")
    if (count is None) == (devices is None):
        raise EnvironmentConfigError(f"{where}.gpus gives exactly one of 'count' and "
                                     "'devices'")
    if count is not None:
        if type(count) is not int or count < 1:
            raise EnvironmentConfigError(f"{where}.gpus.count must be a positive integer")
        return GPURequest(count=count)
    if (not isinstance(devices, list) or not devices
            or not all(isinstance(d, (str, int)) and _NAME.fullmatch(str(d))
                       for d in devices)):
        raise EnvironmentConfigError(f"{where}.gpus.devices must be a non-empty list of "
                                     "device indices or UUIDs")
    names = tuple(str(d) for d in devices)
    if len(set(names)) != len(names):
        raise EnvironmentConfigError(f"{where}.gpus.devices names a device twice")
    return GPURequest(devices=names)


def _mount_path(raw: Any, what: str) -> Path:
    path = _absolute(raw, what)
    if any(ch in str(path) for ch in ',"\n'):
        # --mount is a CSV value: a comma or quote in the path would end the field and the
        # rest would be read as further options.
        raise EnvironmentConfigError(f"{what} {path} contains a comma, quote or newline, "
                                     "which a --mount value cannot carry")
    return path


def _container(component_id: str, raw: Any) -> ContainerProfile:
    where = f"containers[{component_id!r}]"
    if not isinstance(raw, Mapping):
        raise EnvironmentConfigError(f"{where} is an object")
    unknown = set(raw) - {"gpus", "data", "output", "network", "note"}
    if unknown:
        raise EnvironmentConfigError(f"{where} has unknown keys {sorted(unknown)}; a key "
                                     "this version does not understand is not ignored")
    data_raw = raw.get("data") or {}
    if not isinstance(data_raw, Mapping):
        raise EnvironmentConfigError(f"{where}.data maps a name to a host directory")
    data = {}
    for name, path in data_raw.items():
        if not _NAME.fullmatch(str(name)):
            raise EnvironmentConfigError(f"{where}.data name {name!r} is not a plain name")
        data[str(name)] = _mount_path(path, f"{where}.data[{name!r}]")
    output = _mount_path(raw["output"], f"{where}.output") if raw.get("output") else None
    if output is not None:
        for name, root in data.items():
            if output == root or root in output.parents or output in root.parents:
                # One mount read-only and an overlapping one writable would let the
                # component write into the data it was promised it could only read.
                raise EnvironmentConfigError(
                    f"{where}: the output directory {output} overlaps data root {name!r} "
                    f"({root}); the writable directory must be separate from the data")
    network = str(raw.get("network") or "none")
    if network not in _NETWORKS:
        raise EnvironmentConfigError(f"{where}.network is one of {_NETWORKS}, not "
                                     f"{network!r}")
    return ContainerProfile(component_id=component_id, gpus=_gpus(raw.get("gpus"), where),
                            data=data, output=output, network=network)


def _interpreter(project: str, raw: Any) -> Interpreter:
    where = f"interpreters[{project!r}]"
    if not isinstance(raw, Mapping):
        raise EnvironmentConfigError(f"{where} is an object with 'python' or 'prefix'")
    unknown = set(raw) - {"python", "prefix", "root", "note"}
    if unknown:
        raise EnvironmentConfigError(f"{where} has unknown keys {sorted(unknown)}")
    if bool(raw.get("python")) == bool(raw.get("prefix")):
        raise EnvironmentConfigError(f"{where} gives exactly one of 'python' and 'prefix'")
    root = _absolute(raw["root"], f"{where}.root") if raw.get("root") else None
    if raw.get("python"):
        return Interpreter(project, _absolute(raw["python"], f"{where}.python"), root)
    prefix = _absolute(raw["prefix"], f"{where}.prefix")
    python = prefix / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    return Interpreter(project, python, root, declared_as="prefix")


@dataclass(frozen=True)
class ExecutionEnvironments:
    """A reviewed configuration, accepted whole or not at all."""

    interpreters: Mapping[str, Interpreter] = field(default_factory=dict)
    containers: Mapping[str, ContainerProfile] = field(default_factory=dict)
    digest: str = ""
    reviewed_by: str = ""
    reviewed_on: str = ""
    source: str = ""

    @classmethod
    def parse(cls, doc: Mapping[str, Any], *, source: str = "") -> "ExecutionEnvironments":
        if not isinstance(doc, Mapping):
            raise EnvironmentConfigError("the configuration is a JSON object")
        unknown = set(doc) - {*_BODY_KEYS, "review"}
        if unknown:
            raise EnvironmentConfigError(f"unknown top-level keys {sorted(unknown)}")
        review = doc.get("review")
        if not isinstance(review, Mapping) or not review.get("sha256"):
            raise EnvironmentConfigError(
                "the configuration carries no review (review.by, review.on, review.sha256); "
                "an unreviewed configuration is not used")
        digest = review_digest(doc)
        if str(review["sha256"]).lower() != digest:
            raise EnvironmentConfigError(
                f"the configuration has changed since it was reviewed (its body hashes to "
                f"{digest[:12]}…, the review approved {str(review['sha256'])[:12]}…); "
                "review it again")
        interpreters = doc.get("interpreters") or {}
        containers = doc.get("containers") or {}
        if not isinstance(interpreters, Mapping) or not isinstance(containers, Mapping):
            raise EnvironmentConfigError("'interpreters' and 'containers' are objects")
        return cls(
            interpreters={str(p): _interpreter(str(p), v) for p, v in interpreters.items()},
            containers={str(c): _container(str(c), v) for c, v in containers.items()},
            digest=digest, reviewed_by=str(review.get("by") or ""),
            reviewed_on=str(review.get("on") or ""), source=source)

    @classmethod
    def load(cls, path: str | os.PathLike[str]) -> "ExecutionEnvironments":
        p = Path(path)
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise EnvironmentConfigError(f"{p} cannot be read as JSON: {exc}") from exc
        return cls.parse(doc, source=str(p))

    @classmethod
    def from_env(cls) -> "ExecutionEnvironments | None":
        """The file ``$BIOAGENT_ENVIRONMENTS`` names, or None when it names none."""
        raw = os.environ.get(ENV_ENVIRONMENTS, "").strip()
        return cls.load(raw) if raw else None

    def interpreter(self, project: str) -> Interpreter | None:
        return self.interpreters.get(project)

    def container(self, component_id: str) -> ContainerProfile | None:
        return self.containers.get(component_id)

    def describe(self) -> str:
        """How a result names the configuration it ran under."""
        who = f" by {self.reviewed_by}" if self.reviewed_by else ""
        when = f" on {self.reviewed_on}" if self.reviewed_on else ""
        return f"reviewed environment {self.digest[:12]}{who}{when}"
