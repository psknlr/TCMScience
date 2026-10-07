"""scVI integration run for real: ``integration_method="scvi"`` against scvi-tools.

On the simulated atlas of sc_world, trained for 100 epochs on one CPU thread: scVI mixes
the two batches, the clusters on its latent space recover the four cell types and are
named, what determines the model is recorded, and a rerun reproduces the weights and
every table. Needs scvi-tools (``pip install 'bioagent[scvi]'``, PyTorch's CPU wheel is
enough): the tests skip without it and fail instead under BIOAGENT_REQUIRE_TOOLS. They
are kept out of test_omics_sc_backends.py so that a job which installs the analysis
extra but not this one is not failed by them.
"""

from __future__ import annotations

import hashlib
import json
from importlib import metadata

import numpy as np
import pytest

from bioagent.omics import scrna as S
from omics_world import need_module
from sc_world import EXPECTED, make_sc_world

pytestmark = pytest.mark.unit

# 100 epochs mixed the batches to 0.95 here with every type recovered; 30 left the
# types split into eight clusters, and scVI's own default for this size is 400.
EPOCHS = 100


def _config() -> S.ScConfig:
    return S.ScConfig(n_top_genes=600, integration_method="scvi", scvi_epochs=EPOCHS)


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    need_module("scvi")
    return make_sc_world(tmp_path_factory.mktemp("scvi-world"))


@pytest.fixture(scope="module")
def first(world, tmp_path_factory):
    return S.run_scrna(world.sheet, _config(), tmp_path_factory.mktemp("scvi-run"))


def test_scvi_mixes_the_batches_and_keeps_the_cell_types(first, world):
    integration = first.integration
    assert integration["method"] == "scvi"
    assert integration["mixing_before"] < 0.3 < 0.8 < integration["mixing_after"]
    kind = np.array([world.truth[c]["type"] for c in first.cells], dtype=object)
    keep = np.isin(kind, list(EXPECTED))
    ari = need_module("sklearn.metrics").adjusted_rand_score
    assert ari(kind[keep], first.clusters[keep]) > 0.9
    for c, label in first.cell_types.items():
        values, counts = np.unique(kind[first.clusters.astype(str) == c], return_counts=True)
        major = values[np.argmax(counts)]
        if major in EXPECTED:
            assert label == EXPECTED[major], (c, major, label)


def test_what_determines_the_model_is_recorded(first):
    manifest = json.loads((first.out_dir / "run.json").read_text())
    record = manifest["integration"]
    assert record["implementation"] == "scvi-tools"
    assert record["version"] == metadata.version("scvi-tools")
    assert record["torch"] == metadata.version("torch")
    assert manifest["analysis"]["versions"]["scvi-tools"] == record["version"]
    assert record["seed"] == 0 and record["threads"] == 1
    model, training = record["model"], record["training"]
    assert model["n_latent"] == 10 and model["gene_likelihood"] == "nb"
    assert {"dropout_rate", "dispersion", "latent_distribution"} <= set(model)  # defaults
    assert training["max_epochs"] == EPOCHS == record["epochs_trained"]
    assert training["accelerator"] == "cpu" and training["batch_size"] == 128
    assert sum(record["split"].values()) == first.n_cells
    assert np.isfinite(record["history_last"]["elbo_train"])
    assert record["artefact"]
    for path, digest in record["artefact"].items():
        assert hashlib.sha256((first.out_dir / path).read_bytes()).hexdigest() == digest
        assert manifest["outputs"][path] == digest
    assert "scvi (scvi-tools" in (first.out_dir / "report.md").read_text()
    ok, problems = S.verify_run(first.out_dir)
    assert ok, problems


def test_a_rerun_reproduces_the_weights_and_every_table(world, first, tmp_path):
    """The saved model file differs between the two runs (torch and scvi-tools stamp
    each save), so the weights are compared by their own digest."""
    torch = need_module("torch")
    threads = torch.get_num_threads()
    again = S.run_scrna(world.sheet, _config(), tmp_path)
    assert torch.get_num_threads() == threads             # restored after training
    assert again.integration["weights_sha256"] == first.integration["weights_sha256"]
    assert again.integration["history_last"] == first.integration["history_last"]
    for name in ("cells.tsv", "markers.tsv", "clusters.tsv"):
        assert (tmp_path / name).read_text() == (first.out_dir / name).read_text(), name
