"""Helpers for the omics tests: the simulated RNA-seq experiment with a known answer
(``bioagent.omics.reference_world``, which the Studio validation uploads to the runner
too), simulated counts, and skips for tools and packages that are not installed.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from bioagent.omics.reference_world import (DOWN, READ_LEN, UP, World, make_world,  # noqa: F401
                                             revcomp)


def need_tools(*names: str) -> None:
    """Skip unless every tool is on PATH; fail instead when BIOAGENT_REQUIRE_TOOLS is
    set, so a CI job that installs the tools cannot pass by skipping."""
    missing = [n for n in names if shutil.which(n) is None]
    if missing:
        message = f"not installed: {', '.join(missing)}"
        if os.environ.get("BIOAGENT_REQUIRE_TOOLS"):
            pytest.fail(message)
        pytest.skip(message)


def need_module(name: str):
    """The module, or a skip (a failure under BIOAGENT_REQUIRE_TOOLS)."""
    import importlib
    try:
        return importlib.import_module(name)
    except ImportError:
        if os.environ.get("BIOAGENT_REQUIRE_TOOLS"):
            pytest.fail(f"not installed: {name}")
        pytest.skip(f"not installed: {name}")


def need_mcp_sdk():
    """The MCP SDK, if it is a major the transport and its fixture server are written for
    (1.x or 2.x); else a skip (a failure under BIOAGENT_REQUIRE_TOOLS). A later major
    imports too, so ``need_module("mcp")`` would let these tests run against APIs it may
    have renamed, as 2.x renamed 1.x's."""
    from importlib import metadata
    module = need_module("mcp")
    version = metadata.version("mcp")
    if version.split(".", 1)[0] not in ("1", "2"):
        why = f"mcp {version} is installed; these tests need the 1.x or 2.x SDK (mcp<3)"
        if os.environ.get("BIOAGENT_REQUIRE_TOOLS"):
            pytest.fail(why)
        pytest.skip(why)
    return module


def need_r_package(name: str) -> None:
    """Skip unless ``Rscript`` can load the R package ``name``; fail instead under
    BIOAGENT_REQUIRE_TOOLS. R on PATH without the package is as unusable as no R."""
    need_tools("Rscript")
    done = subprocess.run(["Rscript", "-e", f"suppressMessages(library({name}))"],
                          capture_output=True, text=True)
    if done.returncode:
        why = f"R package {name} cannot be loaded: {done.stderr.strip()[-300:]}"
        if os.environ.get("BIOAGENT_REQUIRE_TOOLS"):
            pytest.fail(why)
        pytest.skip(why)


def simulate_counts(n_genes: int = 300, *, seed: int = 0, numeric: bool = False):
    """Gene counts for eight samples with a batch factor and an age covariate; 10% of
    genes respond to treatment (log2 fold change +-1.5).

    Samples alternate control and treated; the last four are batch b2, 1.4-fold higher.
    Negative binomial, dispersion 0.05; with ``numeric`` the means follow age too.
    Returns genes x samples counts, gene IDs, sample IDs and each sample's variables.
    """
    rng = np.random.default_rng(seed)
    base = rng.lognormal(4.0, 1.2, n_genes)
    lfc = np.where(rng.random(n_genes) < 0.1, rng.choice([-1.5, 1.5], n_genes), 0.0)
    ids, meta, cols = [], {}, []
    for k in range(8):
        condition = "treated" if k % 2 else "control"
        batch = "b2" if k >= 4 else "b1"
        age = 40.0 + 3 * k
        mu = base * 2 ** (lfc * (condition == "treated")) * (1.4 if batch == "b2" else 1.0)
        if numeric:
            mu = mu * np.exp(0.01 * (age - 50))
        size = 1 / 0.05
        cols.append(rng.negative_binomial(size, size / (size + mu)))
        ids.append(f"s{k}")
        meta[f"s{k}"] = {"condition": condition, "batch": batch, "age": str(age)}
    return np.column_stack(cols), [f"g{i}" for i in range(n_genes)], ids, meta


def need_reviewed_engine(engine_cls):
    """(engine, probe) for a structure engine in the interpreter the reviewed environments
    file ($BIOAGENT_ENVIRONMENTS) names for its project; else a skip (a failure under
    BIOAGENT_REQUIRE_TOOLS). A named file that fails its review raises: that is an error
    in the configuration, not a missing tool."""
    from bioagent.backends.environments import ExecutionEnvironments

    envs = ExecutionEnvironments.from_env()
    why = ""
    if envs is None or envs.interpreter(engine_cls.project) is None:
        why = f"no reviewed environment names {engine_cls.project!r} ($BIOAGENT_ENVIRONMENTS)"
    else:
        engine = engine_cls(envs)
        probe = engine.probe()
        if probe.available:
            return engine, probe
        why = probe.reason
    if os.environ.get("BIOAGENT_REQUIRE_TOOLS"):
        pytest.fail(why)
    pytest.skip(why)


def need_dir(variable: str, what: str) -> Path:
    """The directory an environment variable names; else a skip (a failure under
    BIOAGENT_REQUIRE_TOOLS)."""
    raw = os.environ.get(variable, "").strip()
    why = f"${variable} does not name {what}" if not raw else (
        "" if Path(raw).is_dir() else f"${variable} names {raw}, which is not a directory")
    if not why:
        return Path(raw)
    if os.environ.get("BIOAGENT_REQUIRE_TOOLS"):
        pytest.fail(why)
    pytest.skip(why)


