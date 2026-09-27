"""Test tiering: tests are unit by default; lake/network ones opt into integration.

Also the monorepo shim: the bridge tests need PSH-Harness importable. An installed
``psh`` is used as is; otherwise the sibling checkout's ``src`` is added, so the two
packages test together from a plain clone without an install step.
"""

import importlib.util
import os
import sys
from pathlib import Path

import pytest

if importlib.util.find_spec("psh") is None:
    _sibling = Path(__file__).resolve().parents[2] / "PSH-Harness" / "src"
    if (_sibling / "psh").is_dir():
        sys.path.insert(0, str(_sibling))


# The research loop falls back to the 14 MB formula table at the repository root when a
# question names a formula no registered table holds. Tests pass their own synthetic
# tables; none should read the real one by accident.
os.environ.setdefault("BIOAGENT_FORMULA_TABLE", "none")


def pytest_collection_modifyitems(config, items):
    """Any test not explicitly marked integration counts as a unit test."""
    for item in items:
        if "integration" not in item.keywords:
            item.add_marker(pytest.mark.unit)
