"""Differential expression with the DESeq2 method (bioagent.omics.deseq).

The method's pieces are checked against their definitions, the whole against simulated
counts with a known answer, and, when PyDESeq2 is installed, against that independent
implementation of the same method.
"""

from __future__ import annotations

import warnings

import numpy as np
import pytest
from scipy import stats

from bioagent.omics import deseq as D
from omics_world import need_module

pytestmark = pytest.mark.unit


def simulate(n_genes=2000, reps=(4, 4), *, seed=0, frac_de=0.1, batch=False):
    rng = np.random.default_rng(seed)
    base = rng.lognormal(mean=4.0, sigma=1.8, size=n_genes)
    disp = (0.04 + 1.5 / base) * rng.lognormal(0, 0.4, n_genes)
    lfc = np.zeros(n_genes)
    de = rng.random(n_genes) < frac_de
    lfc[de] = rng.choice([-1, 1], de.sum()) * rng.uniform(1.0, 3.0, de.sum())
    cols, samples = [], []
    sf = rng.uniform(0.6, 1.6, sum(reps))
    k = 0
    for g, n in enumerate(reps):
        for r in range(n):
            treated = g == 1
            mu = base * sf[k] * (2 ** (lfc if treated else 0))
            if batch and r % 2:
                mu = mu * 1.5
            size = 1 / disp
            cols.append(rng.negative_binomial(size, size / (size + mu)))
            samples.append({"condition": "treated" if treated else "control",
                            "batch": "b2" if r % 2 else "b1"})
            k += 1
    counts = np.column_stack(cols)
    counts[:5] = 0
    return counts, samples, de


def _run(counts, samples, design="~ condition"):
    return D.run_deseq(counts, [f"g{i}" for i in range(counts.shape[0])], samples,
                       design=design, contrast=("condition", "treated", "control"))


def test_size_factors_are_the_median_of_ratios():
    counts = np.array([[10, 20, 40], [5, 10, 20], [100, 150, 300], [0, 3, 4]])
    sf, method = D.size_factors(counts)
    usable = counts[:3].astype(float)
    geo = np.exp(np.log(usable).mean(axis=1))
    assert method == "ratio"
    assert np.allclose(sf, np.median(usable / geo[:, None], axis=0))


def test_poscounts_when_every_gene_has_a_zero():
    counts = np.array([[0, 5, 9], [4, 0, 8], [7, 6, 0]])
    sf, method = D.size_factors(counts)
    assert method == "poscounts" and np.isclose(np.exp(np.log(sf).mean()), 1.0)


def test_benjamini_hochberg_matches_scipy():
    rng = np.random.default_rng(1)
    p = rng.random(500) ** 2
    p[::7] = np.nan
    ours = D.benjamini_hochberg(p)
    ok = ~np.isnan(p)
    assert np.allclose(ours[ok], stats.false_discovery_control(p[ok], method="bh"))
    assert np.isnan(ours[~ok]).all()


def test_designs_that_cannot_be_fitted_are_refused():
    rows = [{"condition": "a"}, {"condition": "b"}]
    with pytest.raises(D.DESeqError, match="add replicates"):
        D.design_matrix(rows, "~ condition")
    rows = [{"condition": c, "batch": c} for c in "aabb"]
    with pytest.raises(D.DESeqError, match="full rank"):
        D.design_matrix(rows, "~ batch + condition")
    with pytest.raises(D.DESeqError, match="additive"):
        D.design_matrix(rows, "~ batch * condition")


@pytest.mark.parametrize("bad,phrase", [
    (np.array([[1.5, 2, 3, 4]] * 3), "integers"),
    (np.array([[-1, 2, 3, 4]] * 3), "non-negative"),
    (np.array([[0, 2, 3, 4]] * 3), "no counts at all"),
])
def test_bad_counts_are_refused(bad, phrase):
    rows = [{"condition": c} for c in "aabb"]
    with pytest.raises(D.DESeqError, match=phrase):
        D.run_deseq(bad, ["g1", "g2", "g3"], rows, design="~ condition",
                    contrast=("condition", "b", "a"))


def test_planted_differences_are_found_at_the_stated_error_rate():
    counts, samples, de = simulate(seed=0)
    res = _run(counts, samples)
    called = np.isfinite(res.p_adjusted) & (res.p_adjusted < 0.05)
    true_pos = int((called & de).sum())
    assert true_pos / de.sum() > 0.7
    assert (called & ~de).sum() / max(called.sum(), 1) < 0.1
    assert np.isnan(res.p_value[:5]).all() and (res.base_mean[:5] == 0).all()
    assert res.trend["kind"] == "parametric"


def test_no_difference_gives_few_discoveries():
    counts, samples, _ = simulate(seed=3, frac_de=0.0)
    res = _run(counts, samples)
    assert len(res.significant()) <= 2
    p = res.p_value[np.isfinite(res.p_value) & (res.base_mean > 10)]
    assert stats.kstest(p, "uniform").pvalue > 1e-3       # null p-values are uniform


def test_a_batch_term_is_accounted_for():
    counts, samples, de = simulate(seed=5, batch=True)
    plain = _run(counts, samples)
    adjusted = _run(counts, samples, "~ batch + condition")
    assert "batch[b2]" in adjusted.design.columns
    found = lambda r: int((np.isfinite(r.p_adjusted) & (r.p_adjusted < 0.05) & de).sum())
    assert found(adjusted) >= found(plain)


def test_the_variance_stabilised_scale_is_log2_for_large_counts():
    counts, samples, _ = simulate(seed=2)
    res = _run(counts, samples)
    v = D.vst(np.array([[1000.0, 2000.0, 4000.0]]), res.trend)[0]
    assert np.allclose(np.diff(v), 1.0, atol=0.05)


def test_agrees_with_pydeseq2():
    """Fold changes agree to four nines. P-values agree a little less: for some genes
    PyDESeq2's bounded optimiser stops at the dispersion floor (1e-8) where this one
    finds a higher adjusted profile likelihood, which moves those genes' tests."""
    need_module("pydeseq2")
    import pandas as pd
    from pydeseq2.dds import DeseqDataSet
    from pydeseq2.ds import DeseqStats
    counts, samples, _ = simulate(seed=0)
    res = _run(counts, samples)
    names = [f"s{j}" for j in range(counts.shape[1])]
    meta = pd.DataFrame(samples, index=names)
    df = pd.DataFrame(counts.T, index=names, columns=[f"g{i}" for i in range(len(counts))])
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        dds = DeseqDataSet(counts=df, metadata=meta, design="~ condition",
                           refit_cooks=False, quiet=True)
        dds.deseq2()
        st = DeseqStats(dds, contrast=["condition", "treated", "control"], quiet=True)
        st.summary()
    ref = st.results_df
    ok = np.isfinite(res.p_value) & ref["pvalue"].notna().values
    assert np.corrcoef(res.log2_fold_change[ok], ref["log2FoldChange"].values[ok])[0, 1] > 0.9999
    lp, lr = -np.log10(res.p_value[ok]), -np.log10(ref["pvalue"].values[ok])
    assert np.corrcoef(lp, lr)[0, 1] > 0.995
    ours = set(np.flatnonzero(res.p_adjusted < 0.05))
    theirs = set(np.flatnonzero(ref["padj"].values < 0.05))
    assert len(ours & theirs) >= 0.95 * max(len(ours), len(theirs))
