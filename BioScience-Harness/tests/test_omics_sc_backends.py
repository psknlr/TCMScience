"""The single-cell pipeline's selectable implementations (bioagent.omics.sc.analysis).

On the simulated atlas of sc_world: the AnnData conversion, Scanpy against the built-in
stages, the two Harmony implementations, scVI's refusal and its record, and the
pseudobulk test by PyDESeq2. Tests that need an optional package skip without it and
fail instead in CI's analysis job. That job does not install scvi-tools, so here its
refusal is tested by hiding it, and its record against stand-ins, which tests what this
package writes down. test_omics_scvi.py runs scVI itself.
"""

from __future__ import annotations

import csv
import hashlib
import json
import sys
import types
from importlib import metadata
from pathlib import Path

import numpy as np
import pytest

from bioagent.omics import scrna as S
from bioagent.omics.de_backends import BACKENDS
from bioagent.omics.optional import BackendUnavailable
from bioagent.omics.sc import analysis as A
from bioagent.omics.sc import io, qc
from bioagent.omics.sc.pseudobulk import pseudobulk_de
from bioagent.status import ExecutionStatus
from omics_world import need_module
from sc_world import EXPECTED, make_sc_world

pytestmark = pytest.mark.unit


def hide(monkeypatch, package):
    """Make ``package`` unimportable for this test, as if it were not installed."""
    for name in [n for n in sys.modules if n == package or n.startswith(package + ".")]:
        monkeypatch.setitem(sys.modules, name, None)
    monkeypatch.setitem(sys.modules, package, None)


def _ari(a, b):
    return need_module("sklearn.metrics").adjusted_rand_score(a, b)


def _truth(world, cells, key="type"):
    return np.array([world.truth[c][key] for c in cells], dtype=object)


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    return make_sc_world(tmp_path_factory.mktemp("scb-world"))


@pytest.fixture(scope="module")
def info(world):
    return {r["sample"]: {"condition": r["condition"], "batch": r["batch"]}
            for r in csv.DictReader(open(world.sheet))}


@pytest.fixture(scope="module")
def matrix(world, info):
    parts = [(s, io.read_counts(world.root / s)) for s in info]
    return io.concatenate(parts, info)


@pytest.fixture(scope="module")
def builtin(world, tmp_path_factory):
    return S.run_scrna(world.sheet, S.ScConfig(n_top_genes=600),
                       tmp_path_factory.mktemp("scb-builtin"))


@pytest.fixture(scope="module")
def scanpy_run(world, tmp_path_factory):
    need_module("scanpy")
    need_module("harmonypy")
    return S.run_scrna(world.sheet, S.ScConfig(n_top_genes=600, analysis_backend="scanpy"),
                       tmp_path_factory.mktemp("scb-scanpy"))


# ---------------------------------------------------------------- CellMatrix <-> AnnData

def test_anndata_round_trip_keeps_the_counts_and_every_label(matrix):
    need_module("anndata")
    ad = io.to_anndata(matrix)
    assert ad.shape == matrix.shape
    assert list(ad.obs_names) == list(matrix.cells)
    assert list(ad.var_names) == list(matrix.gene_names)
    assert list(ad.var["gene_ids"]) == list(matrix.gene_ids)
    assert (ad.layers["counts"] != matrix.counts).nnz == 0
    assert set(ad.obs.columns) == {"sample", "condition", "batch"}
    assert str(ad.obs["batch"].dtype) == "category"
    # an analysis normalises X in place; the counts come back from their layer
    ad.X = ad.X * 0.37
    back = io.from_anndata(ad)
    assert (back.counts != matrix.counts).nnz == 0
    assert list(back.cells) == list(matrix.cells)
    assert list(back.gene_ids) == list(matrix.gene_ids)
    assert list(back.gene_names) == list(matrix.gene_names)
    for key, values in matrix.obs.items():
        assert list(back.obs[key]) == list(values), key
    del ad.layers["counts"]
    with pytest.raises(io.ScIOError, match="raw counts"):
        io.from_anndata(ad)                     # normalised X is refused, not rounded


def test_repeated_names_are_refused_before_conversion(matrix):
    need_module("anndata")
    m = matrix.subset(cells=np.arange(10))
    m.cells[1] = m.cells[0]
    with pytest.raises(io.ScIOError, match="cell barcodes repeat"):
        io.to_anndata(m)


# ---------------------------------------------------------------- Scanpy as the backend

def test_scanpy_recovers_and_names_the_planted_types(scanpy_run, world):
    kind = _truth(world, scanpy_run.cells)
    keep = np.isin(kind, list(EXPECTED))
    assert _ari(kind[keep], scanpy_run.clusters[keep]) > 0.9
    for c, label in scanpy_run.cell_types.items():
        values, counts = np.unique(kind[scanpy_run.clusters.astype(str) == c],
                                   return_counts=True)
        major = values[np.argmax(counts)]
        if major in EXPECTED:
            assert label == EXPECTED[major], (c, major, label)


def test_the_backends_agree_on_the_same_cells(builtin, scanpy_run):
    # QC and doublets are the same built-in steps, so both analyse the same cells
    assert np.array_equal(builtin.cells, scanpy_run.cells)
    assert _ari(builtin.clusters, scanpy_run.clusters) > 0.9


def test_every_scanpy_stage_is_recorded_with_its_parameters_and_seed(scanpy_run):
    manifest = json.loads((scanpy_run.out_dir / "run.json").read_text())
    analysis = manifest["analysis"]
    assert analysis["backend"] == "scanpy" == manifest["config"]["analysis_backend"]
    assert analysis["versions"]["scanpy"] == metadata.version("scanpy")
    stages = {s["stage"]: s for s in analysis["stages"]}
    assert [s["call"] for s in analysis["stages"]] == [
        "scanpy.pp.normalize_total", "scanpy.pp.log1p", "scanpy.pp.highly_variable_genes",
        "scanpy.pp.scale", "scanpy.pp.pca", "scanpy.pp.neighbors", "scanpy.tl.leiden",
        "scanpy.tl.umap", "scanpy.tl.rank_genes_groups"]
    for stage in ("PCA", "neighbours", "clusters", "layout"):
        assert stages[stage]["params"]["random_state"] == 0, stage
    assert stages["neighbours"]["params"]["transformer"] == "sklearn"   # exact search
    assert stages["clusters"]["params"]["flavor"] == "leidenalg"
    assert stages["highly variable genes"]["params"]["batch_key"] == "batch"
    md = (scanpy_run.out_dir / "report.md").read_text()
    assert f"Scanpy {metadata.version('scanpy')}" in md
    ok, problems = S.verify_run(scanpy_run.out_dir)
    assert ok, problems


def test_the_harmony_that_ran_is_named(builtin, scanpy_run):
    ours, theirs = builtin.integration, scanpy_run.integration
    assert ours["method"] == theirs["method"] == "harmony"
    assert ours["implementation"] == "bioagent.omics.sc.harmony" and ours["lambda"] == 1.0
    assert theirs["implementation"] == "harmonypy"
    assert theirs["version"] == metadata.version("harmonypy")
    assert theirs["parameters"]["random_state"] == 0
    assert "max_iter_harmony" in theirs["parameters"]
    for run in (builtin, scanpy_run):
        assert run.integration["mixing_before"] < 0.3 < 0.8 < run.integration["mixing_after"]
    assert "harmonypy" in (scanpy_run.out_dir / "report.md").read_text()


def test_a_scanpy_rerun_is_identical(world, scanpy_run, tmp_path):
    again = S.run_scrna(world.sheet, S.ScConfig(n_top_genes=600, analysis_backend="scanpy"),
                        tmp_path)
    for name in ("cells.tsv", "markers.tsv", "clusters.tsv"):
        assert (tmp_path / name).read_text() == (scanpy_run.out_dir / name).read_text()
    assert np.array_equal(again.clusters, scanpy_run.clusters)


def test_a_single_batch_is_not_integrated_and_the_record_says_why(matrix, tmp_path):
    m = qc.filter_cells(matrix).matrix
    one = m.subset(cells=np.flatnonzero(m.obs["sample"] == "ctrl1"))
    an = A.run(one, None, "sample", backend="builtin", integration="harmony",
               settings=A.StageSettings(n_top_genes=300, n_pcs=20), out_dir=tmp_path)
    assert an.integration == {"method": "none", "requested": "harmony", "batch_key": "sample",
                              "reason": "a single batch: nothing to integrate"}
    assert np.array_equal(an.embedding, an.pcs)


# ---------------------------------------------------------------- scVI

def test_scvi_without_scvi_tools_is_refused_before_any_work(monkeypatch, world, tmp_path):
    hide(monkeypatch, "scvi")
    out = tmp_path / "run"
    with pytest.raises(BackendUnavailable) as caught:
        S.run_scrna(world.sheet, S.ScConfig(integration_method="scvi"), out)
    assert caught.value.status is ExecutionStatus.UNAVAILABLE
    assert "scvi-tools" in caught.value.reason
    assert "pip install 'bioagent[scvi]'" in caught.value.reason
    assert not out.exists()


def test_scvi_records_its_settings_seed_and_model(monkeypatch, matrix, tmp_path):
    """Against stand-ins for scvi-tools and torch: this checks what is recorded and how
    the latent space is used, not scVI (test_omics_scvi.py runs scVI itself)."""
    need_module("anndata")
    import pandas as pd
    calls: dict = {}
    threads = {"n": 4}

    class Tensor:                       # what the weights digest reads of a tensor
        def __init__(self, values):
            self.values = np.asarray(values, dtype=np.float32)

        def detach(self):
            return self

        def cpu(self):
            return self

        def numpy(self):
            return self.values

    class StandInSCVI:
        @classmethod
        def setup_anndata(cls, adata, layer, batch_key):
            calls["setup"] = {"layer": layer, "batch_key": batch_key,
                              "counts": adata.layers[layer].copy()}

        def __init__(self, adata, **settings):
            self.n = adata.n_obs
            self.module = types.SimpleNamespace(state_dict=lambda: {"w": Tensor([1, 2])})
            calls["model"] = settings

        def train(self, **settings):
            calls["train"] = settings
            calls["threads while training"] = threads["n"]
            self.history = {"elbo_train": pd.DataFrame({"elbo_train": [3.0, 2.5]})}
            self.train_indices, self.validation_indices = range(self.n - 2), range(2)
            self.test_indices = range(0)

        def get_latent_representation(self, give_mean):
            calls["give_mean"] = give_mean
            rng = np.random.default_rng(scvi.settings.seed)
            return rng.normal(size=(self.n, calls["model"]["n_latent"]))

        def save(self, path, overwrite, save_anndata):
            Path(path).mkdir(parents=True, exist_ok=overwrite)
            (Path(path) / "model.pt").write_bytes(b"stand-in weights")

    scvi = types.SimpleNamespace(settings=types.SimpleNamespace(seed=None),
                                 model=types.SimpleNamespace(SCVI=StandInSCVI))
    torch = types.SimpleNamespace(get_num_threads=lambda: threads["n"],
                                  set_num_threads=lambda n: threads.update(n=n))
    monkeypatch.setitem(sys.modules, "scvi", scvi)
    monkeypatch.setitem(sys.modules, "torch", torch)
    m = qc.filter_cells(matrix).matrix
    m = m.subset(cells=np.arange(0, m.shape[0], 3))
    settings = A.StageSettings(n_top_genes=300, n_pcs=20, seed=5, scvi_latent=8)
    an = A.run(m, m.obs["batch"], "batch", backend="builtin", integration="scvi",
               settings=settings, out_dir=tmp_path)
    record = an.integration
    assert record["implementation"] == "scvi-tools"
    assert record["seed"] == 5 == scvi.settings.seed
    # trained on one thread, and the caller's thread count is restored afterwards
    assert record["threads"] == 1 == calls["threads while training"] and threads["n"] == 4
    # the stand-in declares no defaults, so the record holds what was passed
    assert record["model"] == calls["model"] and record["model"]["n_latent"] == 8
    assert record["training"] == calls["train"] and calls["train"]["accelerator"] == "cpu"
    assert calls["give_mean"] is True and record["latent"] == "posterior mean"
    assert record["epochs_trained"] == 2 and record["history_last"] == {"elbo_train": 2.5}
    assert record["split"] == {"train": m.shape[0] - 2, "validation": 2, "test": 0}
    weights = np.asarray([1, 2], dtype=np.float32)
    assert record["weights_sha256"] == hashlib.sha256(
        f"w\0{weights.dtype.str}\0{weights.shape}\0".encode() + weights.tobytes()).hexdigest()
    assert record["artefact"] == {
        "scvi_model/model.pt": hashlib.sha256(b"stand-in weights").hexdigest()}
    # scVI is given the raw counts of the highly variable genes, with the batch
    assert calls["setup"]["layer"] == "counts" and calls["setup"]["batch_key"] == "batch"
    raw = m.counts[:, an.highly_variable]
    assert (calls["setup"]["counts"] != raw).nnz == 0
    assert an.embedding.shape == (m.shape[0], 8)


# ---------------------------------------------------------------- refusals

def test_unknown_or_unavailable_choices_are_refused(monkeypatch, world, tmp_path):
    for field, value in (("analysis_backend", "seurat"), ("integration_method", "bbknn"),
                         ("de_backend", "edger")):
        with pytest.raises(S.ScError, match=f"{field} is one of"):
            S.run_scrna(world.sheet, S.ScConfig(**{field: value}), tmp_path / field)
    for field in ("scvi_epochs", "scvi_threads"):
        with pytest.raises(S.ScError, match="at least 1"):
            S.run_scrna(world.sheet, S.ScConfig(**{field: 0}), tmp_path / field)
    hide(monkeypatch, "scanpy")
    with pytest.raises(BackendUnavailable, match="scanpy"):
        S.run_scrna(world.sheet, S.ScConfig(analysis_backend="scanpy"), tmp_path / "a")
    hide(monkeypatch, "pydeseq2")
    with pytest.raises(BackendUnavailable, match="pydeseq2"):
        S.run_scrna(world.sheet, S.ScConfig(de_backend="pydeseq2"), tmp_path / "b")
    assert not (tmp_path / "a").exists() and not (tmp_path / "b").exists()


# ---------------------------------------------------------------- pseudobulk by PyDESeq2

def test_pseudobulk_by_either_backend_points_the_same_way(matrix, world, info):
    """At two samples per condition the backends agree on fold changes and differ in
    how far they shrink dispersions (docs/omics-backends.md): PyDESeq2's optimiser
    leaves more gene-wise estimates at the floor, its prior is narrower, and it calls
    fewer genes. Neither calls a gene that was not planted."""
    need_module("pydeseq2")
    m = qc.filter_cells(matrix).matrix
    kind = _truth(world, m.cells)
    b_cells = kind == "B"
    res = {backend: pseudobulk_de(m.counts[b_cells], list(m.gene_names), kind[b_cells],
                                  m.obs["sample"][b_cells], info, design="~ condition",
                                  contrast=("condition", "treated", "control"),
                                  de_backend=backend).results["B"]
           for backend in BACKENDS}
    ours, theirs = res["builtin"], res["pydeseq2"]
    planted = set(world.condition_genes)
    idx = [ours.genes.index(g) for g in sorted(planted)]
    assert (ours.log2_fold_change[idx] > 0.5).all()
    assert (theirs.log2_fold_change[idx] > 0.5).all()
    ok = np.isfinite(ours.log2_fold_change) & np.isfinite(theirs.log2_fold_change)
    assert np.corrcoef(ours.log2_fold_change[ok], theirs.log2_fold_change[ok])[0, 1] > 0.999
    found = {name: {r.genes[i] for i in r.significant()} for name, r in res.items()}
    assert found["builtin"] <= planted and found["pydeseq2"] <= planted
    assert len(found["builtin"]) >= 12 and len(found["pydeseq2"]) >= 5
    assert (ours.diagnostics["prior_dispersion_variance"]
            > theirs.diagnostics["prior_dispersion_variance"])
    assert theirs.diagnostics["gene_wise_at_floor"] > ours.diagnostics["gene_wise_at_floor"]


def test_the_skill_passes_the_three_choices_through(monkeypatch, world, tmp_path):
    """The pseudobulk test by PyDESeq2 costs minutes over a whole atlas, so the skill's
    de_backend is followed here to the refusal that only the pipeline can give."""
    need_module("scanpy")
    from bioagent.skills.omics import scrna_cell_atlas
    art = scrna_cell_atlas(str(world.sheet), analysis_backend="scanpy",
                           integration_method="none", out_dir=str(tmp_path / "run"))
    prov = art.provenance
    assert prov["analysis"]["backend"] == "scanpy"
    assert prov["analysis"]["versions"]["scanpy"] == metadata.version("scanpy")
    assert prov["integration"]["method"] == "none"
    assert prov["de_backend"]["name"] == "builtin"
    assert art.claims and all("builtin DESeq2 implementation" in c.confidence_basis
                              for c in art.claims)
    hide(monkeypatch, "pydeseq2")
    with pytest.raises(BackendUnavailable, match="pydeseq2"):
        scrna_cell_atlas(str(world.sheet), de_backend="pydeseq2",
                         out_dir=str(tmp_path / "refused"))
