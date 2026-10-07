"""Differential expression by either backend (bioagent.omics.de_backends).

What must mean the same in both implementations is checked directly: which sample a
count column is paired with, which way a fold change points, which design is fitted,
which trend the VST used, and what an unavailable backend does. On the simulated
experiment of omics_world (G000-G003 four-fold up in treated samples, G004-G006
four-fold down), the built-in implementation and PyDESeq2 are then compared gene by
gene. The tests that need PyDESeq2 skip without it, and fail instead in CI's analysis
job.
"""

from __future__ import annotations

import json
import sys
import warnings
from importlib import metadata

import numpy as np
import pytest

from bioagent.omics import de_backends as B
from bioagent.omics import deseq
from bioagent.omics import rnaseq as R
from bioagent.omics.deseq import DESeqError
from bioagent.omics.optional import BackendUnavailable
from bioagent.status import ExecutionStatus
from omics_world import DOWN, UP, make_world, need_module, simulate_counts

pytestmark = pytest.mark.unit

CONTRAST = ("condition", "treated", "control")


def hide(monkeypatch, package):
    """Make ``package`` unimportable for this test, as if it were not installed."""
    for name in [n for n in sys.modules if n == package or n.startswith(package + ".")]:
        monkeypatch.setitem(sys.modules, name, None)
    monkeypatch.setitem(sys.modules, package, None)


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    return make_world(tmp_path_factory.mktemp("de-world"))


@pytest.fixture(scope="module")
def builtin_run(world, tmp_path_factory):
    config = R.RNASeqConfig(transcripts=world.transcripts, annotation=world.gtf,
                            engine="builtin", trimmer="builtin")
    return R.run_rnaseq(world.sheet, config, tmp_path_factory.mktemp("de-builtin"))


@pytest.fixture(scope="module")
def experiment(builtin_run):
    """The world's gene counts, as the pipeline quantified them, and its sample sheet."""
    genes = builtin_run.genes
    meta = {s.name: dict(s.attributes) for s in builtin_run.samples}
    return np.round(genes.counts), list(genes.genes), list(genes.samples), meta


def _run(experiment, backend, **kw):
    counts, genes, ids, meta = experiment
    kw.setdefault("contrast", CONTRAST)
    return B.run_de(counts, genes, ids, meta, design="~ condition", backend=backend, **kw)


def _needs(backend):
    if backend == "pydeseq2":
        need_module("pydeseq2")


# ---------------------------------------------------------------- the two backends agree

def test_both_backends_find_the_planted_genes_pointing_the_same_way(experiment):
    need_module("pydeseq2")
    res = {b: _run(experiment, b) for b in B.BACKENDS}
    for r in res.values():
        lfc = dict(zip(r.genes, r.log2_fold_change))
        padj = dict(zip(r.genes, r.p_adjusted))
        assert all(lfc[g] > 1 and padj[g] < 0.05 for g in UP), r.backend
        assert all(lfc[g] < -1 and padj[g] < 0.05 for g in DOWN), r.backend
    a, b = res["builtin"], res["pydeseq2"]
    ok = np.isfinite(a.log2_fold_change) & np.isfinite(b.log2_fold_change)
    assert np.corrcoef(a.log2_fold_change[ok], b.log2_fold_change[ok])[0, 1] > 0.999
    planted = [a.genes.index(g) for g in UP + DOWN]
    assert np.array_equal(np.sign(a.stat[planted]), np.sign(b.stat[planted]))
    # the calls differ at most at the threshold: a gene called by one backend only is
    # borderline in both (in the recorded run, G021 at FDR 0.046 and 0.051)
    for i in set(a.significant()) ^ set(b.significant()):
        assert 0.01 < a.p_adjusted[i] < 0.1 and 0.01 < b.p_adjusted[i] < 0.1, a.genes[i]
    assert np.allclose(a.size_factors, b.size_factors)
    assert np.allclose(a.base_mean, b.base_mean)
    # one design and one contrast, whoever fitted them
    assert a.design_columns == b.design_columns == ("Intercept", "condition[treated]")
    assert a.contrast_vector == b.contrast_vector == (0.0, 1.0)
    assert a.reference_levels == b.reference_levels == {"condition": "control"}
    assert a.backend == "builtin" and a.version.startswith("bioagent.omics.deseq sha256:")
    assert b.backend == "pydeseq2" and b.version == metadata.version("pydeseq2")


@pytest.mark.parametrize("backend", B.BACKENDS)
def test_the_contrast_is_the_numerator_over_the_denominator(experiment, backend):
    _needs(backend)
    forward = _run(experiment, backend)
    reverse = _run(experiment, backend, contrast=("condition", "control", "treated"))
    assert np.allclose(forward.log2_fold_change, -reverse.log2_fold_change, atol=1e-4,
                       equal_nan=True)
    assert np.allclose(forward.stat, -reverse.stat, atol=1e-3, equal_nan=True)
    assert np.allclose(forward.p_value, reverse.p_value, rtol=1e-3, equal_nan=True)
    assert forward.reference_levels == {"condition": "control"}
    assert reverse.reference_levels == {"condition": "treated"}
    up = [forward.genes.index(g) for g in UP]
    assert (forward.log2_fold_change[up] > 1).all()           # higher in treated


@pytest.mark.parametrize("backend", B.BACKENDS)
def test_the_order_of_the_samples_does_not_change_the_answer(experiment, backend):
    _needs(backend)
    counts, genes, ids, meta = experiment
    first = B.run_de(counts, genes, ids, meta, design="~ condition", contrast=CONTRAST,
                     backend=backend, vst=True)
    perm = np.random.default_rng(7).permutation(len(ids))
    shuffled_meta = {s: meta[s] for s in reversed(ids)}        # yet another order
    second = B.run_de(counts[:, perm], genes, [ids[j] for j in perm], shuffled_meta,
                      design="~ condition", contrast=CONTRAST, backend=backend, vst=True)
    assert second.samples == tuple(ids[j] for j in perm)
    for _, attr, _ in B.CONTRACT:
        assert np.allclose(getattr(first, attr), getattr(second, attr), rtol=1e-6,
                           atol=1e-9, equal_nan=True), attr
    assert np.allclose(first.size_factors[perm], second.size_factors)
    assert np.allclose(first.vst[:, perm], second.vst, rtol=1e-6)


def test_metadata_meet_the_counts_by_sample_id_not_by_position():
    counts, genes, ids, meta = simulate_counts(60)
    aligned = B.run_de(counts, genes, ids, meta, design="~ condition", contrast=CONTRAST)
    # every position now holds another sample's row: matched by position this would
    # flip half the conditions, and the test would answer a different question
    rotated = [(ids[(k + 1) % len(ids)], meta[ids[(k + 1) % len(ids)]])
               for k in range(len(ids))]
    by_id = B.run_de(counts, genes, ids, rotated, design="~ condition", contrast=CONTRAST)
    assert np.allclose(aligned.log2_fold_change, by_id.log2_fold_change, equal_nan=True)
    assert B.align_samples(ids, rotated) == [meta[s] for s in ids]


@pytest.mark.parametrize("change,phrase", [
    ("repeat a count column", "count columns name a sample more than once"),
    ("repeat a metadata row", "metadata name a sample more than once"),
    ("drop a metadata row", "no metadata for sample"),
    ("add a metadata row", "with no count column"),
    ("transpose the counts", "transpose it"),
])
def test_samples_that_do_not_line_up_are_refused(change, phrase):
    counts, genes, ids, meta = simulate_counts(12)
    pairs = list(meta.items())
    if change == "repeat a count column":
        ids = ids[:-1] + ids[:1]
    elif change == "repeat a metadata row":
        pairs = pairs + pairs[:1]
    elif change == "drop a metadata row":
        pairs = pairs[1:]
    elif change == "add a metadata row":
        pairs = pairs + [("s99", {"condition": "control", "batch": "b1"})]
    else:
        counts = counts.T
    with pytest.raises(DESeqError, match=phrase):
        B.run_de(counts, genes, ids, pairs, design="~ condition", contrast=CONTRAST)


def test_the_result_contract_is_the_same_record_for_every_backend():
    counts, genes, ids, meta = simulate_counts(60)
    res = B.run_de(counts, genes, ids, meta, design="~ condition", contrast=CONTRAST)
    row = res.table()[0]
    assert set(row) == {"gene"} | {attr for _, attr, _ in B.CONTRACT}
    assert set(res.gene_diagnostics) == set(B.GENE_DIAGNOSTICS)
    record = json.loads(json.dumps(res.record()))
    assert record["backend"] == "builtin" and record["status"] == "SUCCEEDED"
    assert record["contract"]["log2FoldChange"] == "log2_fold_change"
    assert record["contrast_vector"] == [0.0, 1.0]


# ---------------------------------------------------------------- the design

def test_factors_covariates_and_reference_levels_are_one_design_for_both():
    need_module("pydeseq2")
    counts, genes, ids, meta = simulate_counts(150, numeric=True)
    res = {b: B.run_de(counts, genes, ids, meta, design="~ batch + age + condition",
                       contrast=CONTRAST, covariates=("age",), backend=b)
           for b in B.BACKENDS}
    a, b = res["builtin"], res["pydeseq2"]
    columns = ("Intercept", "batch[b2]", "age", "condition[treated]")
    assert a.design_columns == b.design_columns == columns
    assert a.contrast_vector == b.contrast_vector == (0.0, 0.0, 0.0, 1.0)
    assert a.reference_levels == b.reference_levels == {"batch": "b1",
                                                        "condition": "control"}
    ok = np.isfinite(a.log2_fold_change) & np.isfinite(b.log2_fold_change)
    assert np.corrcoef(a.log2_fold_change[ok], b.log2_fold_change[ok])[0, 1] > 0.999


def test_pydeseq2_given_the_matrix_answers_as_with_its_own_formula():
    """Handing PyDESeq2 the design matrix changes nothing it would have computed from
    the formula: the same reference level, the same coefficients, the same test."""
    need_module("pydeseq2")
    import pandas as pd
    from pydeseq2.dds import DeseqDataSet
    from pydeseq2.default_inference import DefaultInference
    from pydeseq2.ds import DeseqStats
    counts, genes, ids, meta = simulate_counts(150)
    ours = B.run_de(counts, genes, ids, meta, design="~ batch + condition",
                    contrast=CONTRAST, backend="pydeseq2")
    frame = pd.DataFrame(counts.T, index=ids, columns=genes)
    sheet = pd.DataFrame([meta[s] for s in ids], index=ids)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        dds = DeseqDataSet(counts=frame, metadata=sheet, design="~ batch + condition",
                           refit_cooks=False, inference=DefaultInference(n_cpus=1),
                           quiet=True)
        dds.deseq2()
        stats = DeseqStats(dds, contrast=list(CONTRAST),
                           inference=DefaultInference(n_cpus=1), quiet=True)
        stats.summary()
    theirs = stats.results_df.loc[genes]
    assert np.allclose(ours.log2_fold_change, theirs["log2FoldChange"], rtol=1e-6,
                       equal_nan=True)
    assert np.allclose(ours.p_value, theirs["pvalue"], rtol=1e-6, equal_nan=True)


# ---------------------------------------------------------------- the VST

def test_each_backend_stabilises_with_its_own_trend(experiment):
    need_module("pydeseq2")
    counts = experiment[0]
    res = {b: _run(experiment, b, vst=True) for b in B.BACKENDS}
    for name, r in res.items():
        assert r.vst_detail["computed_by"] == name and r.vst_detail["blind"] is False
        assert r.vst.shape == counts.shape
        # the recorded trend, on this backend's own normalised counts, is the VST
        again = deseq.vst(counts / r.size_factors, r.vst_detail["trend"])
        assert np.allclose(again, r.vst, rtol=1e-6), name
    a, b = res["builtin"], res["pydeseq2"]
    assert a.vst_detail["trend"] != b.vst_detail["trend"]      # fitted twice, not shared
    assert np.corrcoef(a.vst.ravel(), b.vst.ravel())[0, 1] > 0.999


# ---------------------------------------------------------------- availability

def test_an_unavailable_backend_is_refused_not_replaced(monkeypatch, world, tmp_path):
    hide(monkeypatch, "pydeseq2")
    status = B.backend_status("pydeseq2")
    assert status["status"] == "UNAVAILABLE" and "pydeseq2" in status["reason"]
    assert B.backend_status("builtin")["status"] == "READY"
    counts, genes, ids, meta = simulate_counts(12)
    with pytest.raises(BackendUnavailable) as caught:
        B.run_de(counts, genes, ids, meta, design="~ condition", contrast=CONTRAST,
                 backend="pydeseq2")
    assert caught.value.status is ExecutionStatus.UNAVAILABLE
    assert "pip install 'bioagent[analysis]'" in caught.value.reason
    # the pipeline stops before it reads a single file, and says why
    out = tmp_path / "run"
    config = R.RNASeqConfig(transcripts=world.transcripts, annotation=world.gtf,
                            engine="builtin", trimmer="builtin", de_backend="pydeseq2")
    with pytest.raises(BackendUnavailable, match="pydeseq2"):
        R.run_rnaseq(world.sheet, config, out)
    assert list(out.iterdir()) == []
    with pytest.raises(R.RNASeqError, match="de_backend is one of"):
        R.run_rnaseq(world.sheet, R.RNASeqConfig(transcripts=world.transcripts,
                                                  de_backend="edger"), out)


# ---------------------------------------------------------------- the pipeline and skill

def test_the_pipeline_records_the_implementation_that_ran(world, builtin_run, tmp_path):
    need_module("pydeseq2")
    config = R.RNASeqConfig(transcripts=world.transcripts, annotation=world.gtf,
                            engine="builtin", trimmer="builtin", de_backend="pydeseq2")
    run = R.run_rnaseq(world.sheet, config, tmp_path / "run")
    out = run.out_dir
    manifest = json.loads((out / "run.json").read_text())
    de = manifest["differential_expression"]
    assert de["backend"] == "pydeseq2" and de["version"] == metadata.version("pydeseq2")
    assert de["vst"]["computed_by"] == "pydeseq2"
    assert de["design_columns"] == ["Intercept", "condition[treated]"]
    assert manifest["summary"]["differential_expression"]["backend"] == "pydeseq2"
    built = json.loads((builtin_run.out_dir / "run.json").read_text())
    assert built["differential_expression"]["backend"] == "builtin"
    md = (out / "report.md").read_text()
    assert f"PyDESeq2 {metadata.version('pydeseq2')}" in md
    header = (out / "deseq2_results.tsv").read_text().splitlines()[0].split("\t")
    assert header == ["gene_id", "gene_name", "base_mean", "log2_fold_change", "lfc_se",
                      "stat", "p_value", "p_adjusted"]
    diag = (out / "de_diagnostics.tsv").read_text().splitlines()[0].split("\t")
    assert diag == ["gene_id", *B.GENE_DIAGNOSTICS]
    ok, problems = R.verify_run(out)
    assert ok, problems
    called = {g for g, q in zip(run.result.genes, run.result.p_adjusted) if q < 0.05}
    assert set(UP) | set(DOWN) <= called


def test_the_skill_takes_the_backend_and_names_it(world, tmp_path):
    need_module("pydeseq2")
    from bioagent.skills.omics import rnaseq_differential_expression
    art = rnaseq_differential_expression(str(world.sheet), transcripts=str(world.transcripts),
                                         annotation=str(world.gtf), engine="builtin",
                                         trimmer="builtin", de_backend="pydeseq2",
                                         out_dir=str(tmp_path / "run"))
    assert art.provenance["de_backend"] == {"name": "pydeseq2",
                                            "version": metadata.version("pydeseq2")}
    (claim,) = art.claims
    assert "pydeseq2 implementation" in claim.confidence_basis
