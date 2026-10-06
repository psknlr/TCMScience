"""Build hook: ship the reviewed skills and registry inside the wheel.

Everything else about the package is declared in ``pyproject.toml``. This file exists for
one step a declaration cannot express. A governed skill run reads three things that live
beside the package rather than in it: the skill manifests (``skills/``), the lockfile that
pins them and the rest of the reviewed registry state (``registry/``). A wheel built from
``src/`` alone carried none of them, so an installed package could not run one governed
skill: the CLI refused for want of a skill directory (audit AUD-25).

They are copied as they are into ``bioagent/_bundled/`` of the built package, and
``bioagent.config`` reads them there when there is no source tree. The copy keeps the tree's
layout, so the skill hash and the lockfile that pins it are the ones in the repository, and
the governed runner finds the lockfile above the skill directory exactly as it does in a
checkout. An editable install reads the source tree itself and gets no copy.
"""

from __future__ import annotations

import fnmatch
import shutil
from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py

HERE = Path(__file__).resolve().parent

#: Directories beside ``src/`` that the installed package needs, bundled under these names.
BUNDLED = ("skills", "registry")

#: Never shipped: derived files and operating-system metadata (see scripts/make_release.py).
EXCLUDED = ("__pycache__", "*.py[cod]", "._*", ".DS_Store", "Thumbs.db", "__MACOSX",
            ".ipynb_checkpoints")


def _excluded(directory: str, names: list[str]) -> set[str]:
    return {n for n in names if any(fnmatch.fnmatch(n, p) for p in EXCLUDED)}


class build_py_with_resources(build_py):
    """``build_py``, then the reviewed skills and registry into ``bioagent/_bundled/``."""

    def run(self) -> None:
        super().run()
        if getattr(self, "editable_mode", False):
            return                       # an editable install reads the source tree
        target = Path(self.build_lib) / "bioagent" / "_bundled"
        for name in BUNDLED:
            source = HERE / name
            if not source.is_dir():
                raise FileNotFoundError(
                    f"{source} is missing; a package built without it cannot run a "
                    "governed skill")
            destination = target / name
            if destination.exists():
                shutil.rmtree(destination)
            shutil.copytree(source, destination, ignore=_excluded)


setup(cmdclass={"build_py": build_py_with_resources})
