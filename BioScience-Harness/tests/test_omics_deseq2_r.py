"""The built-in DESeq2 implementation and PyDESeq2 against R's DESeq2 (optional).

R's DESeq2 is the method's reference implementation. It runs here through ``Rscript``
on deseq2_reference.R; without R or the package the tests skip, and they fail instead
under BIOAGENT_REQUIRE_TOOLS. Two cases (docs/omics-backends.md):

* the B-cell pseudobulk of sc_world, two samples per condition: two residual degrees of
  freedom, where the two Python implementations disagree about which genes to call;
* 1,500 simulated genes, four samples per condition and a batch term: five residual
  degrees of freedom, the control.

With two residual degrees of freedom R departs from the textbook procedure that both
Python implementations follow: it estimates the variance of the dispersion prior by
simulation, not as the residuals' MAD variance less trigamma((m - p) / 2). The
attribution test shows that, given R's gene-wise dispersions and that prior variance,
the built-in implementation's remaining steps give R's answer; it calls this package's
private steps for that, and so tests them, not only the public result.
"""

from __future__ import annotations

import csv
import math
import subprocess
from pathlib import Path

import numpy as np
import pytest
from scipy import special

from bioagent.omics import de_backends as B
from bioagent.omics import deseq as D
from bioagent.omics.sc import io, qc
from omics_world import need_module, need_r_package, simulate_counts
from sc_world import make_sc_world

pytestmark = pytest.mark.unit

CONTRAST = ("condition", "treated", "control")
SCRIPT = Path(__file__).with_name("deseq2_reference.R")
FLOOR = 100 * D.MIN_DISP          # a gene-wise estimate below this sits at the floor


def run_r(counts, genes, ids, info, design, out: Path):
    """R's DESeq2 on the same counts and design: per-gene columns and run values."""
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "counts.tsv", "w") as fh:
        fh.write("gene\t" + "\t".join(ids) + "\n")
        fh.writelines(f"{g}\t" + "\t".join(str(int(v)) for v in row) + "\n"
                      for g, row in zip(genes, counts))
    keys = sorted({k for s in ids for k in info[s]})
    with open(out / "samples.tsv", "w") as fh:
        fh.write("sample\t" + "\t".join(keys) + "\n")
        fh.writelines(f"{s}\t" + "\t".join(str(info[s][k]) for k in keys) + "\n"
                      for s in ids)
    done = subprocess.run(["Rscript", str(SCRIPT), str(out / "counts.tsv"),
                           str(out / "samples.tsv"), design, *CONTRAST, "0.05",
                           str(out / "genes.tsv"), str(out / "run.tsv")],
                          capture_output=True, text=True)
    assert done.returncode == 0, done.stderr[-2000:]
    rows = list(csv.DictReader(open(out / "genes.tsv"), delimiter="\t"))
    assert [r["gene"] for r in rows] == list(genes)
    columns = {name: np.array([float("nan") if r[name] == "NA" else float(r[name])
                               for r in rows])
               for name in ("baseMean", "log2FoldChange", "pvalue", "padj",
                            "dispGeneEst", "dispFit", "dispersion")}
    run = {r["key"]: r["value"] for r in csv.DictReader(open(out / "run.tsv"),
                                                        delimiter="\t")}
    return columns, run


def called(padj, genes):
    return {g for g, q in zip(genes, padj) if np.isfinite(q) and q < 0.05}


def corr(a, b):
    ok = np.isfinite(a) & np.isfinite(b)
    return float(np.corrcoef(a[ok], b[ok])[0, 1])


@pytest.fixture(scope="module")
def deseq2():
    """R with DESeq2, checked once, before any data are built."""
    need_r_package("DESeq2")


@pytest.fixture(scope="module")
def bcell(deseq2, tmp_path_factory):
    """The B-cell pseudobulk of sc_world, as the pseudobulk test sums it."""
    world = make_sc_world(tmp_path_factory.mktemp("r-world"))
    info = {r["sample"]: {"condition": r["condition"]}
            for r in csv.DictReader(open(world.sheet))}
    m = io.concatenate([(s, io.read_counts(world.root / s)) for s in info], info)
    m = qc.filter_cells(m).matrix
    b = np.array([world.truth[c]["type"] == "B" for c in m.cells])
    ids = sorted(info)
    counts = np.column_stack([np.asarray(m.counts[b & (m.obs["sample"] == s)].sum(axis=0))
                              .ravel() for s in ids])
    return counts, [str(g) for g in m.gene_names], ids, info, set(world.condition_genes)


@pytest.fixture(scope="module")
def bcell_r(bcell, tmp_path_factory):
    counts, genes, ids, info, _ = bcell
    return run_r(counts, genes, ids, info, "~ condition", tmp_path_factory.mktemp("r-b"))


def _de(case, backend, design="~ condition"):
    counts, genes, ids, info = case[:4]
    return B.run_de(counts, genes, ids, info, design=design, contrast=CONTRAST,
                    backend=backend)


def test_two_replicates_the_builtin_calls_rs_genes_and_more(bcell, bcell_r):
    counts, genes, ids, info, planted = bcell
    r, run = bcell_r
    ours = _de(bcell, "builtin")
    assert run["residualDF"] == "2"
    assert np.allclose(ours.size_factors, [float(run[f"sizeFactor:{s}"]) for s in ids])
    assert np.allclose(ours.base_mean, r["baseMean"], rtol=1e-12)
    assert corr(ours.log2_fold_change, r["log2FoldChange"]) > 0.9999
    by_r, by_us = called(r["padj"], genes), called(ours.p_adjusted, genes)
    assert by_r and by_r <= planted and by_r <= by_us <= planted
    # R leaves more gene-wise estimates at the floor; above it the two agree
    gene_wise = ours.gene_diagnostics["dispersion_gene_wise"]
    assert np.sum(gene_wise < FLOOR) < np.sum(r["dispGeneEst"] < FLOOR)
    above = (gene_wise >= FLOOR) & (r["dispGeneEst"] >= FLOOR)
    assert np.median(np.abs(np.log(gene_wise[above] / r["dispGeneEst"][above]))) < 0.01
    # R's prior variance is not the MAD estimate at two residual degrees of freedom,
    # and the built-in result says that it uses the MAD estimate there
    mad = max(float(run["varLogDispEsts"]) - float(special.polygamma(1, 1.0)), 0.25)
    assert abs(float(run["dispPriorVar"]) - mad) > 0.1
    assert any(n.startswith("2 residual degrees of freedom") for n in ours.notes)


def test_from_rs_dispersion_estimates_the_builtin_steps_give_rs_answer(bcell, bcell_r):
    """The trend, MAP dispersions, GLM, Wald test and independent filtering of
    deseq.run_deseq, run from R's gene-wise dispersions and R's prior variance."""
    counts, genes, ids, info, _ = bcell
    r, run = bcell_r
    y_all = counts.astype(float)
    dm = D.design_matrix([info[s] for s in ids], "~ condition",
                         references={"condition": "control"})
    X = dm.matrix
    sf, _ = D.size_factors(y_all)
    base_mean = (y_all / sf).mean(axis=1)
    nz = base_mean > 0
    y, gene_wise = y_all[nz], r["dispGeneEst"][nz]
    fit = gene_wise >= FLOOR
    a0, a1 = D._parametric_trend(base_mean[nz][fit], gene_wise[fit])
    assert a0 == pytest.approx(float(run["asymptDisp"]), rel=1e-5)
    assert a1 == pytest.approx(float(run["extraPois"]), rel=1e-5)
    trend = a0 + a1 / base_mean[nz]
    coef = np.linalg.lstsq(X, (y / sf).T, rcond=None)[0]
    mu = np.maximum(sf * (X @ coef).T, D.MIN_MU)
    log_map = D._maximise_log_alpha(y, mu, X, math.log(D.MIN_DISP / 10), math.log(10.0),
                                    prior_mean=np.log(trend),
                                    prior_var=float(run["dispPriorVar"]))
    var_log = float(run["varLogDispEsts"])
    outlier = np.log(gene_wise) > np.log(trend) + 2 * math.sqrt(var_log)
    final = np.where(outlier, gene_wise, np.clip(np.exp(log_map), D.MIN_DISP, 10.0))
    assert np.max(np.abs(np.log(final / r["dispersion"][nz]))) < 0.01
    beta, _, cov, _ = D._fit_glm(y, sf, X, final)
    vector = dm.contrast(*CONTRAST)
    se = np.sqrt(np.einsum("k,gkl,l->g", vector, cov, vector))
    p = np.full(len(genes), np.nan)
    p[nz] = 2 * special.ndtr(-np.abs(beta @ vector / se))
    padj, threshold = D._independent_filter(base_mean, p, 0.05)
    assert threshold == pytest.approx(float(run["filterThreshold"]), rel=1e-9)
    assert called(padj, genes) == called(r["padj"], genes)
    assert np.nanmax(np.abs(padj - r["padj"])) < 1e-3


def test_two_replicates_pydeseq2_calls_a_subset_of_rs_genes(bcell, bcell_r):
    need_module("pydeseq2")
    counts, genes, ids, info, planted = bcell
    r, run = bcell_r
    theirs = _de(bcell, "pydeseq2")
    gene_wise = theirs.gene_diagnostics["dispersion_gene_wise"]
    # the same gene-wise estimates sit at the floor in R and in PyDESeq2
    assert np.array_equal(gene_wise < FLOOR, r["dispGeneEst"] < FLOOR)
    assert corr(theirs.log2_fold_change, r["log2FoldChange"]) > 0.99999
    assert corr(-np.log10(theirs.p_value), -np.log10(r["pvalue"])) > 0.99
    assert called(theirs.p_adjusted, genes) <= called(r["padj"], genes) <= planted


def test_five_residual_degrees_of_freedom_all_three_nearly_agree(deseq2, tmp_path):
    counts, genes, ids, meta = simulate_counts(1500)
    info = {s: {"condition": meta[s]["condition"], "batch": meta[s]["batch"]} for s in ids}
    r, run = run_r(counts, genes, ids, info, "~ batch + condition", tmp_path)
    assert run["residualDF"] == "5"
    mad = max(float(run["varLogDispEsts"]) - float(special.polygamma(1, 2.5)), 0.25)
    assert float(run["dispPriorVar"]) == pytest.approx(mad)
    # from R's p-values, the built-in independent filtering gives R's adjusted ones
    padj, threshold = D._independent_filter(r["baseMean"], r["pvalue"], 0.05)
    assert threshold == pytest.approx(float(run["filterThreshold"]), rel=1e-9)
    assert np.nanmax(np.abs(padj - r["padj"])) < 1e-12
    by_r = called(r["padj"], genes)
    ours = _de((counts, genes, ids, info), "builtin", "~ batch + condition")
    assert not any("residual degrees of freedom" in n for n in ours.notes)
    assert corr(ours.log2_fold_change, r["log2FoldChange"]) > 0.9999
    assert corr(-np.log10(ours.p_value), -np.log10(r["pvalue"])) > 0.999
    assert by_r <= called(ours.p_adjusted, genes)
    need_module("pydeseq2")
    theirs = _de((counts, genes, ids, info), "pydeseq2", "~ batch + condition")
    assert corr(-np.log10(theirs.p_value), -np.log10(r["pvalue"])) > 0.99999
    assert called(theirs.p_adjusted, genes) <= by_r
