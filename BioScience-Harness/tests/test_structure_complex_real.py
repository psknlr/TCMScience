"""Boltz-2 and Chai-1 run for real, through their adapters. Not for CI.

Like ``test_structure_engines_real.py``, each test runs only when the reviewed environments
file (``$BIOAGENT_ENVIRONMENTS``) names the tool's project and the tool answers the probe
there, and it also needs the weights already on disk: ``$BIOAGENT_BOLTZ_CACHE`` (Boltz-2:
``boltz2_conf.ckpt``, ``boltz2_aff.ckpt``, ``mols.tar`` and ``mols/``, 7.8 GB) and
``$BIOAGENT_CHAI_DOWNLOADS`` (Chai-1: ``models_v2/`` and ``conformers_v1.apkl``, 1.3 GB).
Without them a test skips, or fails under ``BIOAGENT_REQUIRE_TOOLS=1``. On the shared
four-core machine of docs/compute-tasks.md Boltz-2 took 6 minutes and 4.7 GB, Chai-1 46
minutes and 11.3 GB; a GPU is faster. ``$BIOAGENT_COMPLEX_DEVICE`` chooses the device
(``cpu``, the default, or a CUDA device such as ``cuda:0``).

The case is the one the fixtures came from: ubiquitin (76 residues, no MSA) and aspirin
by SMILES, 50 sampling steps, seed 42. The assertions are structural: the chains and their
sizes, every confidence on its scale. The values depend on the sampled model and are not
pinned here; the fixtures pin how the adapter reads them.
"""

from __future__ import annotations

import os

from bioagent.status import ExecutionStatus
from bioagent.structure.complex import Chain, ComplexPredictionTask, Ligand
from bioagent.structure.engines import BoltzEngine, ChaiEngine
from bioagent.structure.tasks import ModelSpec
from omics_world import need_dir, need_reviewed_engine

UBQ = "MQIFVKTLTGKTITLEVEPSDTIENVKAKIQDKEGIPPDQQRLIFAGKQLEDGRTLSDYNIQKESTLHLVLRLRGG"
ASPIRIN = "CC(=O)OC1=CC=CC=C1C(=O)O"
DEVICE = os.environ.get("BIOAGENT_COMPLEX_DEVICE", "cpu")


def ubq_aspirin(model: ModelSpec, *, recycling_steps: int = 1) -> ComplexPredictionTask:
    return ComplexPredictionTask("ubq_aspirin", chains=(Chain("A", UBQ),),
                                 ligands=(Ligand("L", smiles=ASPIRIN),), model=model,
                                 seed=42, recycling_steps=recycling_steps,
                                 sampling_steps=50)


def check_complex(result) -> None:
    assert result.status is ExecutionStatus.SUCCEEDED, result.reason
    assert result.provenance.ran
    assert {c: (v.kind, v.units) for c, v in result.chains.items()} == {
        "A": ("protein", 76), "L": ("ligand", 13)}
    assert all(0 < c.plddt <= 100 for c in result.chains.values())
    assert 0 <= result.ptm <= 1 and 0 <= result.iptm <= 1
    assert set(result.pair_iptm) == {"A", "L"}
    assert all(0 <= v <= 1 for row in result.pair_iptm.values() for v in row.values())


def test_chai1_predicts_ubiquitin_with_aspirin(tmp_path):
    """One trunk pass (``recycling_steps=0``), 50 diffusion steps, no ESM embeddings.

    On the CPU of docs/compute-tasks.md this task took 46 minutes and 11.3 GB, and was
    collected with ``resume`` after the 45-minute limit of the run that started it; this
    function itself, which waits up to three hours, was not run there.
    """
    engine, probe = need_reviewed_engine(ChaiEngine)
    downloads = need_dir("BIOAGENT_CHAI_DOWNLOADS", "a directory with Chai-1's weights")
    task = ubq_aspirin(ModelSpec("chai-1", options={"downloads": str(downloads),
                                                    "device": DEVICE}), recycling_steps=0)
    result = engine.run(task, tmp_path / "work", timeout_s=3 * 3600, poll_s=10.0)
    check_complex(result)
    assert result.provenance.version == probe.version
    assert "combined" in result.provenance.weights
    assert result.model_metrics["chosen_sample"] == 0


def test_boltz2_predicts_ubiquitin_with_aspirin(tmp_path):
    engine, probe = need_reviewed_engine(BoltzEngine)
    cache = need_dir("BIOAGENT_BOLTZ_CACHE", "a Boltz cache holding the Boltz-2 weights")
    accelerator = "gpu" if DEVICE.startswith("cuda") else "cpu"
    task = ubq_aspirin(ModelSpec("boltz-2", options={"cache": str(cache),
                                                     "accelerator": accelerator}))
    result = engine.run(task, tmp_path / "work", timeout_s=3 * 3600, poll_s=5.0)
    check_complex(result)
    assert result.provenance.version == probe.version
    assert list(result.provenance.weights) == ["boltz2_conf.ckpt"]
    assert result.pae is not None and result.model_metrics["mean_pae"] > 0
    assert any("single-sequence mode" in w for w in result.warnings)
