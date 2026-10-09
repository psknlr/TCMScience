"""The runner's home directory and its settings file, ``<home>/runner.json``.

``runner.json`` holds the pairing token (made once, on the first start) and the settings the
page edits through ``GET/PUT /api/settings`` (CONTRACTS §4)::

    {device: "auto"|"cpu"|"cuda:N"|"rocm:N"|"mps", threads: int, max_jobs: int,
     network: {enabled: bool, profile: "offline-analysis"|"biomedical-research"},
     purpose: "academic"|"commercial", allow_remote: bool}

The file is written atomically and readable only by its owner, because the token in it
lets any page that holds it run tools on this machine.

The home also gives bioagent its data directories: ``BIOAGENT_DATA_LAKE``,
``BIOAGENT_TCMDB`` and ``BIOAGENT_WORKSPACE`` point under it unless the user already set
them, so the runner never writes into a source checkout.

A runner put there by the one-line installer (docs/V2.md §16) finds ``<home>/install.json``
beside it: ``{schema, method: "uv-tool", version, site, os, options: {cn, extras, gpu,
autostart, port}, …}``. Its environment has no pip, so a missing engine is installed by running
the install command again with ``--extras``; :func:`reinstall_command` writes that command.
"""

from __future__ import annotations

import contextlib
import copy
import json
import os
import re
import secrets
import sys
import threading
import time
from pathlib import Path
from typing import Any, Iterable, Mapping, MutableMapping

__all__ = ["DEFAULT_HOME", "SettingsError", "HomeInUse", "HomeLock", "RunnerState",
           "default_settings", "validate", "home_paths", "data_environment", "apply_environment",
           "prepare_home", "INSTALL_SCHEMA", "ENGINE_EXTRAS", "install_record",
           "reinstall_command", "extras_for"]

DEFAULT_HOME = Path("~/.tcmscience/studio")
SCHEMA = "tcmstudio.runner/1"
PROFILES = ("offline-analysis", "biomedical-research")
PURPOSES = ("academic", "commercial")
DEVICE_RE = re.compile(r"^(auto|cpu|mps|cuda:\d{1,2}|rocm:\d{1,2})$")
MAX_THREADS = 512
MAX_JOBS = 32


class SettingsError(ValueError):
    """A setting the runner cannot accept; the message says which and why."""


class HomeInUse(RuntimeError):
    """Another runner process holds this home."""


class HomeLock:
    """One runner per home: two would start the same queued jobs and collect the same
    finished ones. An exclusive ``flock`` on ``<home>/runner.lock`` for the process's life
    (POSIX; jobs need POSIX anyway)."""

    def __init__(self, home: str | Path) -> None:
        self.path = Path(home).expanduser().resolve() / "runner.lock"
        self._fh: Any = None

    def acquire(self) -> None:
        try:
            import fcntl
        except ImportError:
            # Windows: no jobs and no lock, but the pid is written all the same: the installer
            # reads it to stop this runner before it replaces or removes the installation.
            with contextlib.suppress(OSError):
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self.path.write_text(str(os.getpid()), encoding="utf-8")
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(self.path, "a+", encoding="utf-8")            # noqa: SIM115 - held open
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fh.seek(0)
            holder = fh.read().strip() or "?"
            fh.close()
            raise HomeInUse(f"another runner (pid {holder}) is using {self.path.parent}; stop it, "
                            "or start this one with another --home") from None
        fh.seek(0)
        fh.truncate()
        fh.write(str(os.getpid()))
        fh.flush()
        self._fh = fh

    def release(self) -> None:
        if self._fh is not None:
            with contextlib.suppress(OSError):
                self._fh.close()
            self._fh = None


def _cores() -> int:
    return max(1, os.cpu_count() or 1)


def default_settings() -> dict[str, Any]:
    cores = _cores()
    threads = max(1, min(4, cores))
    return {
        "device": "auto",
        "threads": threads,
        # one job at a time unless the machine has room for two of the default size
        "max_jobs": 2 if cores >= 8 else 1,
        "network": {"enabled": False, "profile": "biomedical-research"},
        "purpose": "academic",
        "allow_remote": False,
    }


def _bool(name: str, value: Any) -> bool:
    if isinstance(value, bool):
        return value
    raise SettingsError(f"{name} must be true or false, not {json.dumps(value, ensure_ascii=False)}")


def _int(name: str, value: Any, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or int(value) != value:
        raise SettingsError(f"{name} must be a whole number, not {json.dumps(value, ensure_ascii=False)}")
    value = int(value)
    if not low <= value <= high:
        raise SettingsError(f"{name} must be between {low} and {high}, not {value}")
    return value


def validate(patch: Mapping[str, Any], current: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """``current`` with ``patch`` merged in, checked field by field.

    Keys the runner does not know are ignored (a newer page may send more). A bad value is
    refused with :class:`SettingsError` and nothing is changed."""
    if not isinstance(patch, Mapping):
        raise SettingsError("settings must be a JSON object")
    out = copy.deepcopy(dict(current or default_settings()))
    if "device" in patch:
        device = patch["device"]
        if not isinstance(device, str) or not DEVICE_RE.match(device.strip().lower()):
            raise SettingsError("device must be auto, cpu, mps, cuda:N or rocm:N, not "
                                f"{json.dumps(device, ensure_ascii=False)}")
        out["device"] = device.strip().lower()
    if "threads" in patch:
        out["threads"] = _int("threads", patch["threads"], 1, MAX_THREADS)
    if "max_jobs" in patch:
        out["max_jobs"] = _int("max_jobs", patch["max_jobs"], 1, MAX_JOBS)
    if "network" in patch:
        net = patch["network"]
        if isinstance(net, bool):           # a bare switch is accepted for the enabled flag
            net = {"enabled": net}
        if not isinstance(net, Mapping):
            raise SettingsError("network must be {enabled, profile}")
        merged = dict(out.get("network") or {})
        if "enabled" in net:
            merged["enabled"] = _bool("network.enabled", net["enabled"])
        if "profile" in net:
            if net["profile"] not in PROFILES:
                raise SettingsError(f"network.profile must be one of {', '.join(PROFILES)}")
            merged["profile"] = net["profile"]
        out["network"] = {"enabled": bool(merged.get("enabled", False)),
                          "profile": merged.get("profile") or "biomedical-research"}
    if "purpose" in patch:
        if patch["purpose"] not in PURPOSES:
            raise SettingsError(f"purpose must be one of {', '.join(PURPOSES)}")
        out["purpose"] = patch["purpose"]
    if "allow_remote" in patch:
        out["allow_remote"] = _bool("allow_remote", patch["allow_remote"])
    return out


def _clean(stored: Any) -> dict[str, Any]:
    """Settings read from disk: every valid field kept, anything else back to its default
    (a hand-edited file must not stop the runner from starting)."""
    out = default_settings()
    if not isinstance(stored, Mapping):
        return out
    for key in out:
        if key in stored:
            with contextlib.suppress(SettingsError):
                out = validate({key: stored[key]}, out)
    return out


class RunnerState:
    """``<home>/runner.json``: the token and the settings, shared by the request threads."""

    def __init__(self, home: str | Path) -> None:
        self.home = Path(home).expanduser().resolve()
        self.path = self.home / "runner.json"
        self._lock = threading.RLock()
        self.token = ""
        self.created_at = ""
        self.settings = default_settings()
        self.notes: list[str] = []
        self._load()

    def _load(self) -> None:
        doc: Any = None
        if self.path.is_file():
            try:
                doc = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                # Keep the unreadable file for the user to look at; start with a new token.
                kept = self.path.with_name(f"runner.json.unreadable-{int(time.time())}")
                try:
                    self.path.replace(kept)
                    self.notes.append(f"{self.path} could not be read ({exc}); kept as {kept.name}, "
                                      "and a new pairing token was made")
                except OSError:
                    self.notes.append(f"{self.path} could not be read ({exc})")
                doc = None
        doc = doc if isinstance(doc, Mapping) else {}
        token = doc.get("token")
        fresh = not (isinstance(token, str) and len(token) >= 16)
        self.token = secrets.token_urlsafe(24) if fresh else token
        self.created_at = str(doc.get("created_at") or time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                                      time.gmtime()))
        self.settings = _clean(doc.get("settings"))
        if fresh or doc.get("settings") != self.settings or doc.get("schema") != SCHEMA:
            self.save()

    def save(self) -> None:
        with self._lock:
            self.home.mkdir(parents=True, exist_ok=True)
            doc = {"schema": SCHEMA, "token": self.token, "created_at": self.created_at,
                   "settings": self.settings}
            tmp = self.path.with_name(f".runner.json.{os.getpid()}.tmp")
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(doc, fh, ensure_ascii=False, indent=2)
                fh.write("\n")
            os.replace(tmp, self.path)
            with contextlib.suppress(OSError):                  # e.g. a FAT volume
                os.chmod(self.path, 0o600)

    def get(self) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self.settings)

    def update(self, patch: Mapping[str, Any]) -> dict[str, Any]:
        """Merge, validate and save; returns the new settings. Raises SettingsError."""
        with self._lock:
            new = validate(patch, self.settings)
            if new != self.settings:
                self.settings = new
                self.save()
            return copy.deepcopy(self.settings)


# ----------------------------------------------------------------------------- the home

def home_paths(home: str | Path) -> dict[str, Path]:
    # absolute: jobs run in their own processes and must not depend on the runner's cwd
    home = Path(home).expanduser().resolve()
    return {"home": home, "jobs": home / "jobs", "uploads": home / "uploads",
            "projects": home / "projects", "data": home / "data",
            "workspace": home / "workspace", "cache": home / "cache"}


def prepare_home(home: str | Path) -> dict[str, Path]:
    paths = home_paths(home)
    for p in paths.values():
        p.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        os.chmod(paths["home"], 0o700)
    return paths


def data_environment(home: str | Path, environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """``BIOAGENT_DATA_LAKE``, ``BIOAGENT_TCMDB`` and ``BIOAGENT_WORKSPACE`` as the runner
    uses them: the user's own values when set, else directories under the home."""
    env = os.environ if environ is None else environ
    paths = home_paths(home)
    def absolute(value: str) -> str:
        return str(Path(value).expanduser().resolve())
    lake = absolute(env.get("BIOAGENT_DATA_LAKE") or str(paths["data"]))
    return {"BIOAGENT_DATA_LAKE": lake,
            "BIOAGENT_TCMDB": absolute(env.get("BIOAGENT_TCMDB") or str(Path(lake) / "tcmdb")),
            "BIOAGENT_WORKSPACE": absolute(env.get("BIOAGENT_WORKSPACE") or str(paths["workspace"]))}


def apply_environment(home: str | Path,
                      environ: MutableMapping[str, str] | None = None) -> dict[str, str]:
    """Set the three variables in this process (in-process calls read them) where unset."""
    env = os.environ if environ is None else environ
    values = data_environment(home, env)
    for key, value in values.items():
        env.setdefault(key, value)
    return values


# ----------------------------------------------------------------------------- the installer

INSTALL_SCHEMA = "tcmstudio.install/1"
#: The bioagent extra that brings an engine's Python packages (BioScience-Harness pyproject). The
#: command-line tools (Salmon, kallisto, HISAT2, fastp, ColabFold) come from the system's package
#: manager and have none.
ENGINE_EXTRAS = {"pydeseq2": "analysis", "scanpy": "analysis", "scvi": "scvi", "esmfold": "fold",
                 "vina": "docking", "admet": "admet"}
#: The same by the module or distribution a tool reports missing.
_MODULE_EXTRAS = {"pydeseq2": "analysis", "scanpy": "analysis", "leidenalg": "analysis",
                  "igraph": "analysis", "umap": "analysis", "umap-learn": "analysis",
                  "anndata": "analysis", "h5py": "analysis", "tmtools": "analysis",
                  "gseapy": "analysis", "harmonypy": "analysis", "scvi": "scvi",
                  "scvi-tools": "scvi", "torch": "fold", "transformers": "fold",
                  "meeko": "docking", "vina": "docking", "gemmi": "docking",
                  "rdkit": "admet", "sklearn": "admet", "scikit-learn": "admet",
                  "openpyxl": "formulas", "mcp": "mcp", "paperqa": "literature",
                  "paper-qa": "literature"}
_EXTRA_RE = re.compile(r"^[a-z][a-z0-9-]{0,30}$")


def install_record(home: str | Path, prefix: str | Path | None = None) -> dict[str, Any] | None:
    """``<home>/install.json`` when it describes the installation this code runs from: written
    by the one-line installer (``method: "uv-tool"``) and read by a Python that lives in a
    ``uv tool`` environment (``uv-receipt.toml`` beside it). None otherwise (a checkout, pip, a
    hand-edited or unreadable file): the hints then stay as they are."""
    path = Path(home).expanduser() / "install.json"
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(doc, Mapping) or doc.get("method") != "uv-tool":
        return None
    if not (Path(prefix if prefix is not None else sys.prefix) / "uv-receipt.toml").is_file():
        return None
    options = doc.get("options") if isinstance(doc.get("options"), Mapping) else {}
    extras = options.get("extras")
    extras = [e for e in (extras if isinstance(extras, list) else []) if
              isinstance(e, str) and _EXTRA_RE.match(e)]
    site = doc.get("site") if isinstance(doc.get("site"), str) else ""
    if not re.match(r"^https?://[A-Za-z0-9.\-\[\]:]+$", site.rstrip("/")):
        site = "https://science.impf.ai"
    return {"method": "uv-tool", "version": str(doc.get("version") or ""),
            "site": site.rstrip("/"), "os": str(doc.get("os") or ""),
            "options": {"cn": options.get("cn") is True, "gpu": options.get("gpu") is True,
                        "autostart": options.get("autostart") is True, "extras": extras}}


def extras_for(names: Iterable[str]) -> list[str]:
    """The bioagent extras that bring these engines, modules or distributions (known ones only)."""
    out: list[str] = []
    for name in names:
        key = str(name or "").strip().lower()
        extra = ENGINE_EXTRAS.get(key) or _MODULE_EXTRAS.get(key) or _MODULE_EXTRAS.get(
            key.split(".", 1)[0])
        if extra and extra not in out:
            out.append(extra)
    return out


def reinstall_command(record: Mapping[str, Any], extras: Iterable[str] = ()) -> str:
    """The install command that adds ``extras`` to what ``record`` installed, keeping its other
    choices (the extras already there, --cn, --gpu, --autostart): an installed runner has no pip,
    and running the installer again is how it changes."""
    opts = record.get("options") or {}
    wanted = list(opts.get("extras") or [])
    for extra in extras:
        if _EXTRA_RE.match(str(extra)) and extra not in wanted:
            wanted.append(extra)
    site = str(record.get("site") or "https://science.impf.ai").rstrip("/")
    flags = []
    if opts.get("cn"):
        flags.append("cn")
    if opts.get("gpu"):
        flags.append("gpu")
        wanted = [e for e in wanted if e != "fold"]           # --gpu already brings it
    if opts.get("autostart"):
        flags.append("autostart")
    if record.get("os") == "windows":
        args = "".join(f" --{f}" for f in flags) + (f" --extras {','.join(wanted)}" if wanted else "")
        return (f'powershell -ExecutionPolicy ByPass -c "& ([scriptblock]::Create((irm '
                f'{site}/install.ps1))){args}"')
    args = "".join(f" --{f}" for f in flags) + (f" --extras {','.join(wanted)}" if wanted else "")
    return f"curl -LsSf {site}/install.sh | sh" + (f" -s --{args}" if args else "")
