"""Path configuration — no machine-specific absolute paths in source.

Resolution order for every location: explicit argument, then environment
variable, then a repository-relative default. This keeps the package portable
and lets the unit test tier run on a clean checkout with no data present.
"""

from __future__ import annotations

import os
from pathlib import Path

#: Repository root (this file is <repo>/src/bioagent/config.py).
REPO_ROOT = Path(__file__).resolve().parents[2]

#: Whether this module is running from a source checkout rather than an installed package.
#: Installed, ``REPO_ROOT`` is a directory of the interpreter's (``lib/python3.x``), so
#: anything found under it would be there by accident.
SOURCE_TREE = (REPO_ROOT / "pyproject.toml").is_file() and \
    Path(__file__).resolve().parent == REPO_ROOT / "src" / "bioagent"

#: The skills and registry an installed package carries (copied by ``setup.py``).
BUNDLED_ROOT = Path(__file__).resolve().parent / "_bundled"

ENV_DATA_LAKE = "BIOAGENT_DATA_LAKE"
ENV_WORKSPACE = "BIOAGENT_WORKSPACE"
ENV_CATALOGUE = "BIOAGENT_CATALOGUE"
ENV_SKILLS = "BIOAGENT_SKILLS"
ENV_TCMDB = "BIOAGENT_TCMDB"


def data_lake_dir(explicit: str | os.PathLike | None = None) -> Path:
    if explicit:
        return Path(explicit).expanduser()
    env = os.environ.get(ENV_DATA_LAKE)
    if env:
        return Path(env).expanduser()
    return REPO_ROOT.parent / "data" / "biomni_lake"


def workspace_dir(explicit: str | os.PathLike | None = None) -> Path:
    if explicit:
        return Path(explicit).expanduser()
    env = os.environ.get(ENV_WORKSPACE)
    if env:
        return Path(env).expanduser()
    return REPO_ROOT / "workspace"


def tcmdb_dir(explicit: str | os.PathLike | None = None) -> Path:
    """Where the TCM data hub keeps its raw files and built stores (``tcmdb.TCMDataHub``).

    ``$BIOAGENT_TCMDB``, else ``<data lake>/tcmdb``. A governed call may read it only when
    it lies inside a root the permission profile grants (the data lake or the workspace),
    which ``<data lake>/tcmdb`` always does.
    """
    if explicit:
        return Path(explicit).expanduser()
    env = os.environ.get(ENV_TCMDB)
    if env:
        return Path(env).expanduser()
    return data_lake_dir() / "tcmdb"


def _shipped(name: str) -> Path:
    """``<repo>/<name>`` in a source checkout, else the installed package's bundled copy.

    Before the copy existed these locations resolved only in a checkout: an installed
    wheel looked for ``skills/`` beside the interpreter's library directory, found
    nothing, and could not run a governed skill (audit AUD-25).
    """
    if SOURCE_TREE or not (BUNDLED_ROOT / name).is_dir():
        return REPO_ROOT / name
    return BUNDLED_ROOT / name


def skills_dir(explicit: str | os.PathLike | None = None) -> Path:
    """Reviewed research skills shipped with the project (``<repo>/skills``).

    Installed, the copy inside the package. Skills an agent drafts at run time live in the
    workspace's ``skills/`` instead; they are untrusted until the evolution pipeline
    promotes them.
    """
    if explicit:
        return Path(explicit).expanduser()
    env = os.environ.get(ENV_SKILLS)
    if env:
        return Path(env).expanduser()
    return _shipped("skills")


def registry_dir(explicit: str | os.PathLike | None = None) -> Path:
    """The reviewed registry state (``<repo>/registry``): the lockfile pinning the skills,
    the declared skill sources, verified taxa. Installed, the copy inside the package.

    The governed runner does not read this to find its pin: it looks for
    ``registry/skills.lock.yaml`` above the skill directory it was given, so a skill tree
    and the lockfile beside it travel together (``governed.run_governed``).
    """
    if explicit:
        return Path(explicit).expanduser()
    return _shipped("registry")


#: Legacy location: the catalogue lived only here before it was packaged.
LEGACY_CATALOGUE = REPO_ROOT / "data" / "unified_capability_catalogue.csv"


def catalogue_path(explicit: str | os.PathLike | None = None) -> Path:
    """Where the capability catalogue lives.

    Resolution order: explicit argument, `$BIOAGENT_CATALOGUE`, the copy shipped
    inside the package, then the pre-packaging repository location. The packaged
    copy is what makes an installed wheel usable — before it existed this
    returned a `<repo>/data/...` path that only ever resolves in a source
    checkout, so every wheel install found nothing.
    """
    if explicit:
        return Path(explicit).expanduser()
    env = os.environ.get(ENV_CATALOGUE)
    if env:
        return Path(env).expanduser()
    from .data import CATALOGUE_CSV

    if CATALOGUE_CSV.exists():
        return CATALOGUE_CSV
    return LEGACY_CATALOGUE


def data_lake_available(explicit: str | os.PathLike | None = None) -> bool:
    d = data_lake_dir(explicit)
    return d.is_dir() and any(d.iterdir())
