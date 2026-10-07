"""OpenMM and ProteinMPNN run for real, through their adapters, on a CPU.

``test_compute_engines.py`` checks each adapter against its tool's output files. Here the
tools run, each in the interpreter that the reviewed environments file
(``$BIOAGENT_ENVIRONMENTS``) names for its project. A test whose project is not named
there, or whose tool does not answer the probe, skips; under ``BIOAGENT_REQUIRE_TOOLS=1``
it fails instead, so a CI job that installs the tools cannot pass by skipping.

Both are cheap enough for CI: ubiquitin in vacuum (minimisation and 1 ps), and four
designs for ubiquitin. Boltz-2 and Chai-1, which need gigabytes of weights and many
minutes, are in ``test_structure_complex_real.py``.

What the assertions pin is what the first real runs showed (docs/compute-tasks.md): the
atoms OpenMM builds, the columns it reports, that a system without a box reports no
volume, that ProteinMPNN keeps the fixed positions and reports the checkout and weights it
ran. Values that depend on the sampled trajectory or designs are checked for range and
consistency, never for value.
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from bioagent.status import ExecutionStatus
from bioagent.structure.design import SequenceDesignTask
from bioagent.structure.dynamics import DynamicsTask
from bioagent.structure.engines import OpenMMEngine, ProteinMPNNEngine
from bioagent.structure.pdbio import read_pdb
from omics_world import need_reviewed_engine

FIX = Path(__file__).parent / "fixtures"
CRYSTAL = FIX / "structure" / "1ubq.pdb"
UBQ = "MQIFVKTLTGKTITLEVEPSDTIENVKAKIQDKEGIPPDQQRLIFAGKQLEDGRTLSDYNIQKESTLHLVLRLRGG"


def protein_only(tmp_path: Path) -> Path:
    """1UBQ without its 58 crystal waters: a vacuum system has no template for them."""
    dry = tmp_path / "1ubq_protein.pdb"
    dry.write_text("".join(ln for ln in CRYSTAL.read_text().splitlines(keepends=True)
                           if ln.startswith(("ATOM", "TER", "END"))))
    return dry


# -------------------------------------------------------------------- OpenMM
def test_openmm_relaxes_and_simulates_ubiquitin(tmp_path):
    engine, probe = need_reviewed_engine(OpenMMEngine)
    task = DynamicsTask("ubq_vacuum", str(protein_only(tmp_path)),
                        force_field=("amber14-all.xml",), solvent="vacuum",
                        ionic_strength_molar=0.0, duration_ps=1.0, report_interval_ps=0.5,
                        seed=11, platform="CPU")
    result = engine.run(task, tmp_path / "work", timeout_s=3600, poll_s=1.0)
    assert result.status is ExecutionStatus.SUCCEEDED, result.reason
    run = result.provenance
    assert run.ran and run.interpreter == probe.interpreter
    assert run.version == probe.version == result.model_metrics["openmm"]
    metrics = result.model_metrics
    assert metrics["platform"] == "CPU" and metrics["steps"] == 500
    # Hydrogens at pH 7 from OpenMM's templates: 1231 atoms for the 76 residues.
    assert metrics["n_atoms"] == 1231
    assert metrics["periodic"] is False
    assert [e.step for e in result.energies] == [250, 500]
    assert [e.time_ps for e in result.energies] == pytest.approx([0.5, 1.0])
    # A system without a box has no volume; OpenMM would report its default 8 nm^3 cube.
    assert all(e.volume_nm3 is None for e in result.energies)
    assert all(math.isfinite(e.potential_kj_mol) and e.temperature_k > 0
               for e in result.energies)
    assert result.minimised_energy_kj_mol < result.initial_energy_kj_mol
    for model in (result.relaxed, result.final):
        assert read_pdb(model.path).sequence() == UBQ
    assert result.trajectory.path.read_bytes()[4:8] == b"CORD"


# --------------------------------------------------------------- ProteinMPNN
def test_proteinmpnn_designs_ubiquitin_keeping_the_fixed_positions(tmp_path):
    engine, probe = need_reviewed_engine(ProteinMPNNEngine)
    fixed = (1, 2, 3, 44, 68)
    task = SequenceDesignTask("ubq_design", str(CRYSTAL), "A",
                              fixed_positions={"A": fixed}, num_sequences=4, batch_size=2,
                              seed=37, temperature=0.1)
    result = engine.run(task, tmp_path / "work", timeout_s=3600, poll_s=1.0)
    assert result.status is ExecutionStatus.SUCCEEDED, result.reason
    assert result.provenance.ran and result.provenance.version == probe.version
    assert result.model_metrics["git_hash"] == probe.version, "the checkout that ran"
    assert result.native == {"A": UBQ}
    assert [d.sample for d in result.designs] == [1, 2, 3, 4]
    for design in result.designs:
        seq = design.sequences["A"]
        assert len(seq) == len(UBQ) and set(seq) <= set("ACDEFGHIKLMNPQRSTVWY")
        assert all(seq[p - 1] == UBQ[p - 1] for p in fixed)
        assert 0.0 <= design.recovery <= 1.0 and design.temperature == 0.1
        assert design.score > 0 and design.global_score > 0
    weights = result.provenance.weights
    assert list(weights) == ["vanilla_model_weights/v_48_020.pt"]
