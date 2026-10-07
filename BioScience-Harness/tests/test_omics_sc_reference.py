"""The single-cell parts against Scanpy and leidenalg, when they are installed.

Skipped otherwise; the CI job "Analysis pipelines with the standard tools" installs both
and fails instead of skipping.
"""

from __future__ import annotations

import warnings

import numpy as np
import pytest
from scipy import sparse

from bioagent.omics.sc import graph, leiden, markers, preprocess
from omics_world import need_module

pytestmark = pytest.mark.unit


def _data(seed=0, cells=600, genes=800):
    rng = np.random.default_rng(seed)
    groups = rng.integers(0, 3, cells)
    base = rng.gamma(0.6, 1.5, genes)
    lam = np.tile(base, (cells, 1))
    for g in range(3):
        idx = rng.choice(genes, 40, replace=False)
        lam[np.ix_(groups == g, idx)] *= 5
    lib = rng.lognormal(0, 0.3, cells)[:, None]
    return sparse.csr_matrix(rng.poisson(lam * lib).astype(float)), groups


def test_hvg_and_markers_match_scanpy():
    sc = need_module("scanpy")
    import anndata
    counts, groups = _data()
    logx = preprocess.normalize_log1p(counts)
    ours = preprocess.highly_variable_genes(logx, n_top=200)["highly_variable"]
    ad = anndata.AnnData(X=logx.copy())
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        sc.pp.highly_variable_genes(ad, n_top_genes=200, flavor="seurat")
    theirs = ad.var["highly_variable"].to_numpy()
    assert (ours & theirs).sum() >= 0.95 * theirs.sum()
    ad.obs["g"] = [str(g) for g in groups]
    ad.obs["g"] = ad.obs["g"].astype("category")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        sc.tl.rank_genes_groups(ad, "g", method="wilcoxon", tie_correct=True)
    res = markers.rank_genes_groups(logx, groups, np.array([str(i) for i in range(800)]))
    for k, name in enumerate(res.groups):
        ref = {n: s for n, s in zip(ad.uns["rank_genes_groups"]["names"][name],
                                    ad.uns["rank_genes_groups"]["scores"][name])}
        mine = res.scores[k]
        theirs_scores = np.array([ref[str(j)] for j in range(800)])
        assert np.corrcoef(mine, theirs_scores)[0, 1] > 0.999


def test_leiden_quality_matches_leidenalg():
    leidenalg = need_module("leidenalg")
    ig = need_module("igraph")
    counts, _ = _data(seed=1)
    logx = preprocess.normalize_log1p(counts)
    z = preprocess.scale(logx)
    pcs, _, _ = preprocess.pca(z, 20)
    idx, dist = graph.knn(pcs, 15)
    conn = graph.umap_connectivities(idx, dist)
    ours = leiden.leiden(conn, seed=0)
    coo = sparse.triu(conn, 1).tocoo()
    g = ig.Graph(n=conn.shape[0], edges=list(zip(coo.row.tolist(), coo.col.tolist())),
                 edge_attrs={"weight": coo.data.tolist()})
    part = leidenalg.find_partition(g, leidenalg.RBConfigurationVertexPartition,
                                    weights="weight", resolution_parameter=1.0,
                                    n_iterations=-1, seed=0)
    q_ref = leiden.modularity(conn, np.array(part.membership))
    assert ours.quality >= 0.98 * q_ref


def test_connectivities_match_umap_learn():
    umap_ = need_module("umap.umap_")
    counts, _ = _data(seed=4)
    logx = preprocess.normalize_log1p(counts)
    pcs, _, _ = preprocess.pca(preprocess.scale(logx), 20)
    idx, dist = graph.knn(pcs, 15)
    ours = graph.umap_connectivities(idx, dist)
    theirs = umap_.fuzzy_simplicial_set(pcs, 15, random_state=0, metric="euclidean",
                                        knn_indices=idx, knn_dists=dist)[0].tocsr()
    assert ours.nnz == theirs.nnz
    assert abs(ours - theirs).max() < 1e-4


def test_h5ad_and_cell_ranger_h5_are_read(tmp_path):
    anndata = need_module("anndata")
    h5py = need_module("h5py")
    from bioagent.omics.sc import io
    counts, _ = _data(seed=5, cells=50, genes=60)
    ad = anndata.AnnData(X=counts.copy())
    ad.var_names = [f"G{i}" for i in range(60)]
    ad.obs_names = [f"c{i}" for i in range(50)]
    ad.obs["donor"] = ["d1"] * 25 + ["d2"] * 25
    ad.write_h5ad(tmp_path / "a.h5ad")
    m = io.read_counts(tmp_path / "a.h5ad")
    assert m.shape == (50, 60) and (m.counts != counts).nnz == 0
    assert list(m.obs["donor"][:2]) == ["d1", "d1"]
    csc = counts.T.tocsc()                                  # Cell Ranger stores genes x cells
    with h5py.File(tmp_path / "f.h5", "w") as f:
        g = f.create_group("matrix")
        g["data"], g["indices"], g["indptr"] = csc.data, csc.indices, csc.indptr
        g["shape"] = np.array([60, 50])
        g["barcodes"] = np.array([f"c{i}".encode() for i in range(50)])
        feat = g.create_group("features")
        feat["id"] = np.array([f"E{i}".encode() for i in range(60)])
        feat["name"] = np.array([f"G{i}".encode() for i in range(60)])
        feat["feature_type"] = np.array([b"Gene Expression"] * 59 + [b"Antibody Capture"])
    m = io.read_counts(tmp_path / "f.h5")
    assert m.shape == (50, 59) and m.gene_names[0] == "G0"
