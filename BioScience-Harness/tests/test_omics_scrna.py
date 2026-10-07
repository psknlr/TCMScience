"""The single-cell pipeline and its parts, on a simulated experiment with a known answer."""

from __future__ import annotations

import json
import shutil

import numpy as np
import pytest
from scipy import sparse
from scipy.stats import spearmanr

from bioagent.omics import scrna as S
from bioagent.omics.sc import doublets, io, leiden, markers, preprocess, qc
from sc_world import EXPECTED, make_sc_world

pytestmark = pytest.mark.unit


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    return make_sc_world(tmp_path_factory.mktemp("sc-world"))


@pytest.fixture(scope="module")
def matrix(world):
    import csv
    rows = list(csv.DictReader(open(world.sheet)))
    parts = [(r["sample"], io.read_counts(world.root / r["path"])) for r in rows]
    return io.concatenate(parts, {r["sample"]: {"condition": r["condition"],
                                                "batch": r["batch"]} for r in rows})


@pytest.fixture(scope="module")
def flagged(world, tmp_path_factory):
    """doublets='flag' keeps the transition between NK cells and platelets."""
    return S.run_scrna(world.sheet, S.ScConfig(root="NK cell", n_top_genes=600,
                                               doublets="flag"),
                       tmp_path_factory.mktemp("sc-flag"))


@pytest.fixture(scope="module")
def removed(world, tmp_path_factory):
    return S.run_scrna(world.sheet, S.ScConfig(n_top_genes=600),
                       tmp_path_factory.mktemp("sc-remove"))


def _truth(world, cells, key="type"):
    return np.array([world.truth[c][key] for c in cells], dtype=object)


# ---------------------------------------------------------------- reading

def test_ten_x_directories_are_read_and_combined(matrix, world):
    assert matrix.shape[1] == len(world.genes)
    assert set(matrix.obs["sample"]) == {"ctrl1", "ctrl2", "trt1", "trt2"}
    assert matrix.cells[0].startswith(("ctrl1:", "ctrl2:", "trt1:", "trt2:"))
    assert set(matrix.obs["batch"]) == {"b1", "b2"}


def test_normalised_values_are_refused(tmp_path):
    p = tmp_path / "m.csv"
    p.write_text("gene,c1,c2\nG1,0.5,1.2\nG2,3,4\n")
    with pytest.raises(io.ScIOError, match="raw counts"):
        io.read_counts(p)


# ---------------------------------------------------------------- QC and doublets

def test_low_quality_cells_are_removed(matrix, world):
    res = qc.filter_cells(matrix)
    flags = _truth(world, res.matrix.cells, "flag")
    before = (_truth(world, matrix.cells, "flag") == "low_quality").sum()
    assert before > 20 and (flags == "low_quality").sum() == 0
    kept_good = (_truth(world, matrix.cells, "flag") == "").sum()
    assert (flags == "").sum() >= 0.97 * kept_good


def test_scrublet_finds_doublets_of_distinct_types(matrix, world):
    m = qc.filter_cells(matrix).matrix
    called = np.zeros(m.shape[0], dtype=bool)
    for k, s in enumerate(dict.fromkeys(m.obs["sample"])):
        idx = np.flatnonzero(m.obs["sample"] == s)
        called[idx] = doublets.scrublet(m.counts[idx], seed=k).predicted
    kind = _truth(world, m.cells)
    real = kind == "doublet"
    assert (called & real).sum() / real.sum() > 0.7
    discrete = np.isin(kind, ["B", "Mono"])               # far from any transition
    assert (called & discrete).sum() / discrete.sum() < 0.06


def test_a_flat_score_histogram_calls_no_doublets():
    assert doublets._threshold(np.full(500, 0.1)) is None


# ---------------------------------------------------------------- parts

def test_hvg_matches_the_seurat_definition():
    rng = np.random.default_rng(0)
    counts = sparse.csr_matrix(rng.poisson(rng.gamma(0.5, 2, 300), size=(400, 300)))
    logx = preprocess.normalize_log1p(counts)
    hv = preprocess.highly_variable_genes(logx, n_top=50)
    assert hv["highly_variable"].sum() == 50
    top = np.argsort(-np.nan_to_num(hv["norm_dispersion"], nan=-np.inf))[:50]
    assert set(top) == set(np.flatnonzero(hv["highly_variable"]))


def test_leiden_finds_planted_communities():
    rng = np.random.default_rng(1)
    sizes = [40, 30, 30]
    labels = np.repeat(np.arange(3), sizes)
    n = len(labels)
    p = np.where(labels[:, None] == labels[None, :], 0.3, 0.01)
    a = (rng.random((n, n)) < p).astype(float)
    a = np.triu(a, 1)
    a = sparse.csr_matrix(a + a.T)
    res = leiden.leiden(a, seed=0)
    from collections import Counter
    for c in range(3):
        top = Counter(res.membership[labels == c]).most_common(1)[0][1]
        assert top / sizes[c] > 0.9
    assert res.quality == pytest.approx(leiden.modularity(a, res.membership))


def test_harmony_mixes_the_batches(flagged):
    """Mixing is the share of neighbours from the other batch over the share perfect
    mixing gives; the clusters' agreement with the types is checked below."""
    assert flagged.integration["method"] == "harmony"
    assert flagged.integration["mixing_before"] < 0.3
    assert flagged.integration["mixing_after"] > 0.8


def test_wilcoxon_matches_scipy_on_one_gene():
    from scipy.stats import mannwhitneyu
    rng = np.random.default_rng(2)
    x = np.vstack([rng.poisson(1.0, (60, 1)), rng.poisson(2.5, (40, 1))]).astype(float)
    logx = sparse.csr_matrix(np.log1p(x))
    labels = ["a"] * 60 + ["b"] * 40
    res = markers.rank_genes_groups(logx, labels, np.array(["g"]))
    ref = mannwhitneyu(np.log1p(x[60:, 0]), np.log1p(x[:60, 0]), method="asymptotic",
                       use_continuity=False)
    assert res.pvals[res.groups.index("b"), 0] == pytest.approx(ref.pvalue, rel=1e-6)


# ---------------------------------------------------------------- the pipeline

def test_clusters_recover_the_cell_types(removed, world):
    kind = _truth(world, removed.cells)
    keep = np.isin(kind, list(EXPECTED))
    ari = _ari(kind[keep], removed.clusters[keep])
    assert ari > 0.9


def _ari(a, b):
    from collections import Counter
    from math import comb
    pairs = Counter(zip(a, b))
    rows, cols = Counter(a), Counter(b)
    n = len(a)
    index = sum(comb(v, 2) for v in pairs.values())
    ra, cb = sum(comb(v, 2) for v in rows.values()), sum(comb(v, 2) for v in cols.values())
    expected = ra * cb / comb(n, 2)
    return (index - expected) / ((ra + cb) / 2 - expected)


def test_clusters_are_named_by_their_markers(removed, world):
    kind = _truth(world, removed.cells)
    for c, label in removed.cell_types.items():
        members = kind[removed.clusters.astype(str) == c]
        values, counts = np.unique(members, return_counts=True)
        major = values[np.argmax(counts)]
        if major in EXPECTED:
            assert label == EXPECTED[major], (c, major, label)


def test_pseudotime_follows_the_planted_path(flagged, world):
    t = np.array([world.truth[c]["t"] for c in flagged.cells], dtype=float)
    on_path = np.isfinite(t) & np.isfinite(flagged.pseudotime)
    assert on_path.sum() > 100
    assert spearmanr(flagged.pseudotime[on_path], t[on_path]).correlation > 0.85
    kind = _truth(world, flagged.cells)
    assert np.isnan(flagged.pseudotime[kind == "B"]).all()     # not on the trajectory


def test_the_paga_tree_runs_through_the_transition(flagged):
    names = {c: t for c, t in flagged.cell_types.items()}
    joined = {(names[a], names[b]) for a, b, w in flagged.paga.tree if w >= 0.05}
    assert any("Platelet" in pair for pair in joined)


def test_pseudobulk_finds_the_condition_effect_only_where_planted(removed, world):
    pb = removed.pseudobulk
    assert pb is not None and "B cell" in pb.results
    b = pb.results["B cell"]
    found = {b.genes[i] for i in b.significant()}
    assert len(found & set(world.condition_genes)) >= 12
    for ct, res in pb.results.items():
        if ct != "B cell":
            assert len(res.significant()) <= 1


def test_without_a_root_nothing_is_ordered(removed):
    assert removed.pseudotime is None
    assert "No pseudotime" in (removed.out_dir / "report.md").read_text()


def test_the_report_and_its_digests(removed, tmp_path):
    out = removed.out_dir
    for name in ("cells.tsv", "markers.tsv", "clusters.tsv", "paga.tsv", "report.md",
                 "report.html", "run.json"):
        assert (out / name).is_file(), name
    manifest = json.loads((out / "run.json").read_text())
    assert manifest["integration"]["method"] == "harmony"
    ok, problems = S.verify_run(out)
    assert ok, problems
    copy = tmp_path / "copy"
    shutil.copytree(out, copy)
    (copy / "clusters.tsv").write_text("tampered\n")
    ok, problems = S.verify_run(copy)
    assert not ok and any("clusters.tsv" in p for p in problems)


def test_a_rerun_is_identical(world, removed, tmp_path):
    again = S.run_scrna(world.sheet, S.ScConfig(n_top_genes=600), tmp_path)
    for name in ("cells.tsv", "markers.tsv", "clusters.tsv"):
        assert (tmp_path / name).read_text() == (removed.out_dir / name).read_text()
    assert np.array_equal(again.clusters, removed.clusters)


def test_an_unknown_root_is_refused(world, tmp_path):
    with pytest.raises(S.ScError, match="neither a cluster nor"):
        S.run_scrna(world.sheet, S.ScConfig(root="Hepatocyte", n_top_genes=600), tmp_path)


# ---------------------------------------------------------------- the governed skill

def test_the_candidate_skill_claims_only_the_condition_comparison(world, tmp_path):
    from pathlib import Path

    from bioagent.contracts import check_claim
    from bioagent.governed import GovernedRunRefused, run_governed
    candidates = Path(__file__).resolve().parents[1] / "skills" / "candidates" / "omics"
    args = {"source": str(world.sheet), "experiment_design": "observational",
            "out_dir": str(tmp_path / "run")}
    with pytest.raises(GovernedRunRefused, match="no lockfile pins skill"):
        run_governed("scrna-cell-atlas", args, skill_dir=candidates,
                     state_dir=tmp_path / "psh0")
    run = run_governed("scrna-cell-atlas", args, skill_dir=candidates,
                       state_dir=tmp_path / "psh", output_dir=tmp_path / "out",
                       allow_unpinned=True)
    assert not run.released and run.verdict.publishable, run.verdict.codes
    claims = run.artifact.claims
    assert [c.id for c in claims] == ["scrna.pseudobulk.B_cell"]
    assert claims[0].claim_kind == "association"
    assert check_claim(claims[0], {e.id: e for e in run.artifact.evidence}).allowed
    assert "inferred from marker expression" in " ".join(run.artifact.limitations)
