#!/usr/bin/env python3
"""Build the Studio site: ``python3 studio/scripts/build_web.py --out studio/_site``.

A thin wrapper around ``tcmstudio webbuild`` (``tcmstudio.webbuild``), usable from a fresh
checkout: when the three packages are not installed, their source trees are put on the
path. The site is the web app plus ``runtime/catalog.json``, ``runtime/boot.json`` and the
hash-named Python bundle; ``--dev`` also publishes the test pages. ``--help`` for the rest.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

STUDIO = Path(__file__).resolve().parents[1]
REPO = STUDIO.parent

for module, src in (("tcmstudio", STUDIO / "runner" / "src"),
                    ("bioagent", REPO / "BioScience-Harness" / "src"),
                    ("psh", REPO / "PSH-Harness" / "src")):
    if importlib.util.find_spec(module) is None and src.is_dir():
        sys.path.insert(0, str(src))

from tcmstudio.webbuild import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
