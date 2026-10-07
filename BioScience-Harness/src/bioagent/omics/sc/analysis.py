"""Single-cell pipeline steps 3 to 6 (normalisation to markers), by a chosen backend.

Two choices are made independently, and each run records what it used:

``analysis_backend`` decides which implementation runs every stage:

* ``builtin``: this package's parts (:mod:`.preprocess`, :mod:`.graph`, :mod:`.leiden`,
  :mod:`.umap`, :mod:`.markers`), which reproduce Scanpy's defaults with numpy and
  scipy alone;
* ``scanpy``: Scanpy itself, on an AnnData converted from the cell matrix
  (:func:`.io.to_anndata`). The stages are ``pp.normalize_total``, ``pp.log1p``,
  ``pp.highly_variable_genes`` (Seurat flavour), ``pp.scale``, ``pp.pca``,
  ``pp.neighbors``, ``tl.leiden`` (leidenalg's modularity), ``tl.umap`` and
  ``tl.rank_genes_groups`` (tie-corrected Wilcoxon). Each is called with its parameters
  and seed spelled out, and the record lists them. The neighbour search is named too:
  exact below 8,192 cells and PyNNDescent above, as Scanpy would choose for the
  Euclidean metric, but passed explicitly so that the record is not a guess about
  Scanpy's defaults.

``integration_method`` decides what the neighbour graph is built on:

* ``none``: the PCA embedding;
* ``harmony``: Harmony on the PCA embedding (Korsunsky et al. 2019). It has two
  implementations, and the record names the one that ran. The built-in backend uses
  :mod:`.harmony`, which follows the original R package's defaults (lambda 1, 20
  clustering iterations per round). The scanpy backend uses harmonypy, which Scanpy's
  ``external.pp.harmony_integrate`` wraps. That wrapper is not used: under Scanpy
  1.12 it transposes harmonypy 2's cells x PCs result and fails. harmonypy is
  called directly, and its output is oriented by comparing its copy of the input
  with the input. harmonypy 2 also changed its defaults (lambda estimated per cluster,
  4 clustering iterations), so its effective parameters are recorded, read from its
  own signature;
* ``scvi``: the latent space of scVI (Lopez et al. 2018, *Nature Methods* 15:1053),
  trained on the raw counts of the highly variable genes with the batch as covariate.
  scvi-tools is optional and heavy. When it cannot be imported, the run is refused
  with the reason. When it is present, the model settings, seed, training settings
  and the digests of the saved model are recorded. **This path is unverified:** it has
  not been run against scvi-tools itself.

With a single batch there is nothing to integrate, so no method is applied; the record
says which was requested and why it did not run.

Whichever backend runs, cluster 0 is the largest, the markers cover every gene, and
the graph handed on to PAGA and pseudotime is the one the clusters were found on.
"""

from __future__ import annotations

import hashlib
import inspect
import warnings
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
from scipy import sparse

from ..optional import require, version
from . import graph as sc_graph
from . import harmony as sc_harmony
from . import io as sc_io
from . import leiden as sc_leiden
from . import markers as sc_markers
from . import preprocess as sc_pre
from . import umap as sc_umap

__all__ = ["ANALYSIS_BACKENDS", "INTEGRATION_METHODS", "StageSettings", "Analysis",
           "check", "run"]

ANALYSIS_BACKENDS = ("builtin", "scanpy")
INTEGRATION_METHODS = ("none", "harmony", "scvi")
EXACT_NEIGHBOURS_BELOW = 8192          # cells; Scanpy's own cut-off for Euclidean kNN


@dataclass(frozen=True)
class StageSettings:
    n_top_genes: int = 2000
    n_pcs: int = 30
    n_neighbors: int = 15
    resolution: float = 1.0
    seed: int = 0
    target_sum: float = 1e4
    max_value: float = 10.0            # scaled values are clipped to +-max_value
    min_dist: float = 0.5              # UMAP
    scvi_latent: int = 10
    scvi_epochs: int = 400


@dataclass
class Analysis:
    """What steps 3 to 6 produced, whichever backend ran them."""

    logx: sparse.csr_matrix            # cells x genes, log1p of normalised counts
    highly_variable: np.ndarray
    pcs: np.ndarray
    variance_ratio: np.ndarray
    embedding: np.ndarray              # what the neighbour graph was built on
    adjacency: sparse.csr_matrix       # binary kNN graph, for PAGA
    connectivities: sparse.csr_matrix  # UMAP's fuzzy graph: clusters, layout, pseudotime
    clusters: np.ndarray               # integers, 0 the largest
    modularity: float                  # at the resolution used, computed here for both
    umap: np.ndarray
    markers: sc_markers.MarkerResult
    integration: dict[str, Any]
    record: dict[str, Any]


def _needs(backend: str, integration: str) -> list[tuple[str, str, str, str]]:
    """(module, distribution, extra, what needs it) for this combination."""
    needs = []
    if backend == "scanpy":
        why = "analysis_backend 'scanpy'"
        needs += [("scanpy", "scanpy", "analysis", why),
                  ("anndata", "anndata", "analysis", why),
                  ("leidenalg", "leidenalg", "analysis", why),
                  ("umap", "umap-learn", "analysis", why)]
        if integration == "harmony":
            needs.append(("harmonypy", "harmonypy", "analysis",
                          "integration_method 'harmony' under the scanpy backend"))
    if integration == "scvi":
        why = "integration_method 'scvi'"
        needs += [("scvi", "scvi-tools", "scvi", why), ("anndata", "anndata", "analysis", why)]
    return needs


def check(backend: str, integration: str) -> dict[str, str]:
    """The versions of the packages this combination will run; refuses an unknown name
    or a package that cannot be imported (``optional.BackendUnavailable``)."""
    if backend not in ANALYSIS_BACKENDS:
        raise ValueError(f"analysis_backend is one of {', '.join(ANALYSIS_BACKENDS)}, "
                         f"not {backend!r}")
    if integration not in INTEGRATION_METHODS:
        raise ValueError(f"integration_method is one of {', '.join(INTEGRATION_METHODS)}, "
                         f"not {integration!r}")
    found = {}
    for module, dist, extra, why in _needs(backend, integration):
        require(module, backend=why, distribution=dist, extra=extra)
        found[dist] = version(dist)
    return found


def _stage(stage: str, call: str, **params: Any) -> dict[str, Any]:
    return {"stage": stage, "call": call, "params": params}


# ============================================================== integration

def _integrate(pcs: np.ndarray, m: sc_io.CellMatrix, hv: np.ndarray,
               batches: np.ndarray | None, batch_key: str, method: str, backend: str,
               s: StageSettings, out_dir: Path) -> tuple[np.ndarray, dict[str, Any]]:
    if method == "none":
        return pcs, {"method": "none", "batch_key": batch_key}
    if batches is None:
        return pcs, {"method": "none", "requested": method, "batch_key": batch_key,
                     "reason": "a single batch: nothing to integrate"}
    if method == "scvi":
        return _scvi(m, hv, batches, batch_key, s, out_dir)
    if backend == "builtin":
        h = sc_harmony.harmony(pcs, batches, seed=s.seed)
        return h.embedding, {"method": "harmony", "implementation": "bioagent.omics.sc."
                             "harmony", "batch_key": batch_key, "seed": s.seed,
                             "rounds": h.rounds, "converged": h.converged,
                             "clusters": h.clusters, **h.detail}
    return _harmonypy(pcs, batches, batch_key, s)


def _harmonypy(pcs: np.ndarray, batches: np.ndarray, batch_key: str,
               s: StageSettings) -> tuple[np.ndarray, dict[str, Any]]:
    import pandas as pd
    harmonypy = require("harmonypy", backend="integration_method 'harmony' under the "
                        "scanpy backend")
    x = np.asarray(pcs, dtype=np.float64)
    meta = pd.DataFrame({batch_key: pd.Categorical([str(b) for b in batches])})
    passed = {"random_state": s.seed, "verbose": False}
    h = harmonypy.run_harmony(x, meta, batch_key, **passed)
    corrected, original = np.asarray(h.Z_corr, dtype=float), np.asarray(h.Z_orig, dtype=float)
    # harmonypy 0.x returns PCs x cells and 2.x cells x PCs; its copy of the input says which
    if original.shape == x.shape and np.allclose(original, x):
        embedding = corrected
    elif original.shape == x.T.shape and np.allclose(original, x.T):
        embedding = corrected.T
    else:
        raise RuntimeError("harmonypy's copy of the input matches the PCA embedding in "
                           "neither orientation; its result cannot be placed")
    params = {name: p.default for name, p in
              inspect.signature(harmonypy.run_harmony).parameters.items()
              if p.default is not inspect.Parameter.empty}
    params.update(passed)
    iterations = len(h.objective_harmony) - 1
    return embedding, {"method": "harmony", "implementation": "harmonypy",
                       "version": version("harmonypy"), "batch_key": batch_key,
                       "seed": s.seed, "parameters": params, "iterations": iterations,
                       "reached_iteration_limit": iterations >= params["max_iter_harmony"],
                       "clusters": int(h.K), "batches": sorted({str(b) for b in batches})}


def _scvi(m: sc_io.CellMatrix, hv: np.ndarray, batches: np.ndarray, batch_key: str,
          s: StageSettings, out_dir: Path) -> tuple[np.ndarray, dict[str, Any]]:
    """scVI's latent space; recorded in full, and marked unverified (see the module)."""
    import pandas as pd
    why = "integration_method 'scvi'"
    scvi = require("scvi", backend=why, distribution="scvi-tools", extra="scvi")
    anndata = require("anndata", backend=why)
    counts = sparse.csr_matrix(m.counts[:, hv])
    obs = pd.DataFrame({batch_key: pd.Categorical([str(b) for b in batches])},
                       index=pd.Index([str(c) for c in m.cells]))
    var = pd.DataFrame(index=pd.Index([str(g) for g in m.gene_names[hv]]))
    ad = anndata.AnnData(X=counts.copy(), obs=obs, var=var)
    ad.layers["counts"] = counts
    model_settings = {"n_latent": s.scvi_latent, "n_layers": 1, "n_hidden": 128,
                      "gene_likelihood": "nb"}
    # on the CPU, so that the seed reproduces the model
    training = {"max_epochs": s.scvi_epochs, "accelerator": "cpu"}
    scvi.settings.seed = s.seed
    scvi.model.SCVI.setup_anndata(ad, layer="counts", batch_key=batch_key)
    model = scvi.model.SCVI(ad, **model_settings)
    model.train(**training)
    latent = np.asarray(model.get_latent_representation(), dtype=float)
    if latent.shape != (m.shape[0], s.scvi_latent):
        raise RuntimeError(f"scVI returned a {latent.shape} latent space for {m.shape[0]} "
                           f"cells and {s.scvi_latent} dimensions")
    path = Path(out_dir) / "scvi_model"
    model.save(str(path), overwrite=True, save_anndata=False)
    artefact = {str(p.relative_to(out_dir)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted(path.rglob("*")) if p.is_file()}
    return latent, {"method": "scvi", "implementation": "scvi-tools",
                    "version": version("scvi-tools"), "batch_key": batch_key,
                    "seed": s.seed, "model": model_settings, "training": training,
                    "genes": int(hv.sum()), "artefact": artefact, "verified": False}


# ============================================================== the backends

def _builtin(m: sc_io.CellMatrix, batches: np.ndarray | None, batch_key: str,
             integration: str, s: StageSettings, out_dir: Path,
             versions: dict[str, str]) -> Analysis:
    name = "bioagent.omics.sc."
    logx = sc_pre.normalize_log1p(m.counts, target_sum=s.target_sum)
    hv = sc_pre.highly_variable_genes(logx, n_top=s.n_top_genes, batches=batches)
    z = sc_pre.scale(logx[:, hv["highly_variable"]], max_value=s.max_value)
    pcs, _, ratio = sc_pre.pca(z, s.n_pcs)
    emb, info = _integrate(pcs, m, hv["highly_variable"], batches, batch_key, integration,
                           "builtin", s, out_dir)
    idx, dist = sc_graph.knn(emb, s.n_neighbors)
    conn = sc_graph.umap_connectivities(idx, dist)
    lres = sc_leiden.leiden(conn, resolution=s.resolution, seed=s.seed)
    layout = sc_umap.umap_layout(conn, min_dist=s.min_dist, seed=s.seed)
    mk = sc_markers.rank_genes_groups(logx, lres.membership, m.gene_names)
    batch = batch_key if batches is not None else None
    stages = [
        _stage("normalise and log1p", name + "preprocess.normalize_log1p",
               target_sum=s.target_sum),
        _stage("highly variable genes", name + "preprocess.highly_variable_genes",
               n_top_genes=s.n_top_genes, flavor="seurat", batch_key=batch),
        _stage("scale", name + "preprocess.scale", max_value=s.max_value),
        _stage("PCA", name + "preprocess.pca", n_comps=int(pcs.shape[1])),
        _stage("neighbours", name + "graph.knn, graph.umap_connectivities",
               n_neighbors=s.n_neighbors, metric="euclidean", search="exact"),
        _stage("clusters", name + "leiden.leiden", resolution=s.resolution, seed=s.seed,
               iterations=lres.iterations),
        _stage("layout", name + "umap.umap_layout", min_dist=s.min_dist, spread=1.0,
               seed=s.seed),
        _stage("markers", name + "markers.rank_genes_groups", method="wilcoxon",
               tie_correct=True, corr_method="benjamini-hochberg")]
    return Analysis(logx=logx, highly_variable=hv["highly_variable"], pcs=pcs,
                    variance_ratio=ratio, embedding=emb, adjacency=sc_graph.knn_graph(idx),
                    connectivities=conn, clusters=lres.membership, modularity=lres.quality,
                    umap=layout, markers=mk, integration=info,
                    record={"backend": "builtin", "versions": versions, "stages": stages,
                            "settings": asdict(s)})


def _scanpy(m: sc_io.CellMatrix, batches: np.ndarray | None, batch_key: str,
            integration: str, s: StageSettings, out_dir: Path,
            versions: dict[str, str]) -> Analysis:
    sc = require("scanpy", backend="analysis_backend 'scanpy'")
    ad = sc_io.to_anndata(m)
    batch = batch_key if batches is not None else None
    search = "sklearn" if ad.n_obs < EXACT_NEIGHBOURS_BELOW else "pynndescent"
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        sc.pp.normalize_total(ad, target_sum=s.target_sum)
        sc.pp.log1p(ad)
        sc.pp.highly_variable_genes(ad, n_top_genes=s.n_top_genes, flavor="seurat",
                                    batch_key=batch)
        hv = ad.var["highly_variable"].to_numpy(dtype=bool)
        sub = ad[:, hv].copy()
        sc.pp.scale(sub, max_value=s.max_value)
        n_comps = min(s.n_pcs, min(sub.shape) - 1)
        sc.pp.pca(sub, n_comps=n_comps, random_state=s.seed, mask_var=None)
        pcs = np.asarray(sub.obsm["X_pca"], dtype=float)
        emb, info = _integrate(pcs, m, hv, batches, batch_key, integration, "scanpy", s,
                               out_dir)
        ad.obsm["X_bioagent"] = emb
        sc.pp.neighbors(ad, n_neighbors=s.n_neighbors, use_rep="X_bioagent",
                        metric="euclidean", transformer=search, random_state=s.seed)
        sc.tl.leiden(ad, resolution=s.resolution, random_state=s.seed, flavor="leidenalg",
                     n_iterations=-1, key_added="leiden")
        sc.tl.umap(ad, min_dist=s.min_dist, spread=1.0, random_state=s.seed)
        sc.tl.rank_genes_groups(ad, "leiden", method="wilcoxon", tie_correct=True,
                                pts=True, n_genes=ad.n_vars, use_raw=False,
                                corr_method="benjamini-hochberg")
    conn = sparse.csr_matrix(ad.obsp["connectivities"], dtype=float)
    adjacency = conn.copy()
    adjacency.data[:] = 1.0
    clusters = ad.obs["leiden"].astype(int).to_numpy()
    call = "scanpy."
    stages = [
        _stage("normalise", call + "pp.normalize_total", target_sum=s.target_sum),
        _stage("log1p", call + "pp.log1p"),
        _stage("highly variable genes", call + "pp.highly_variable_genes",
               n_top_genes=s.n_top_genes, flavor="seurat", batch_key=batch),
        _stage("scale", call + "pp.scale", max_value=s.max_value),
        _stage("PCA", call + "pp.pca", n_comps=n_comps, random_state=s.seed),
        _stage("neighbours", call + "pp.neighbors", n_neighbors=s.n_neighbors,
               metric="euclidean", transformer=search, random_state=s.seed),
        _stage("clusters", call + "tl.leiden", resolution=s.resolution, flavor="leidenalg",
               n_iterations=-1, random_state=s.seed),
        _stage("layout", call + "tl.umap", min_dist=s.min_dist, spread=1.0,
               random_state=s.seed),
        _stage("markers", call + "tl.rank_genes_groups", method="wilcoxon",
               tie_correct=True, corr_method="benjamini-hochberg")]
    return Analysis(
        logx=sparse.csr_matrix(ad.X), highly_variable=hv, pcs=pcs,
        variance_ratio=np.asarray(sub.uns["pca"]["variance_ratio"], dtype=float),
        embedding=emb, adjacency=adjacency, connectivities=conn, clusters=clusters,
        modularity=sc_leiden.modularity(conn, clusters, s.resolution),
        umap=np.asarray(ad.obsm["X_umap"], dtype=float),
        markers=_markers(ad, m.gene_names, versions.get("scanpy", "")), integration=info,
        record={"backend": "scanpy", "versions": versions, "stages": stages,
                "settings": asdict(s),
                "warnings": sorted({f"{w.category.__name__}: {w.message}"[:300]
                                    for w in caught})})


def _markers(ad: Any, genes: np.ndarray, scanpy_version: str) -> sc_markers.MarkerResult:
    """Scanpy's ranked lists, put back in gene order: groups x genes, as the built-in."""
    rgg = ad.uns["rank_genes_groups"]
    groups = sorted({str(v) for v in ad.obs["leiden"]}, key=lambda v: (len(v), v))
    column = {str(g): j for j, g in enumerate(ad.var_names)}
    fields = {k: np.full((len(groups), ad.n_vars), np.nan)
              for k in ("scores", "pvals", "pvals_adj", "logfoldchanges")}
    for k, group in enumerate(groups):
        cols = np.array([column[str(n)] for n in rgg["names"][group]])
        for key, values in fields.items():
            values[k, cols] = np.asarray(rgg[key][group], dtype=float)
    if any(np.isnan(v).any() for v in fields.values()):
        raise RuntimeError("Scanpy's marker lists do not cover every gene")
    names = [str(g) for g in ad.var_names]
    return sc_markers.MarkerResult(
        groups=groups, genes=np.asarray(genes), scores=fields["scores"],
        pvals=fields["pvals"], pvals_adj=fields["pvals_adj"],
        logfoldchanges=fields["logfoldchanges"],
        pct_in=rgg["pts"].loc[names, groups].to_numpy(dtype=float).T,
        pct_out=rgg["pts_rest"].loc[names, groups].to_numpy(dtype=float).T,
        detail={"method": "wilcoxon", "tie_correct": True,
                "implementation": f"scanpy {scanpy_version}", "cells": int(ad.n_obs),
                "genes": int(ad.n_vars)})


def run(m: sc_io.CellMatrix, batches: np.ndarray | None, batch_key: str, *,
        backend: str, integration: str, settings: StageSettings,
        out_dir: str | Path) -> Analysis:
    """Steps 3 to 6 on the QC-passed counts ``m``; ``batches`` per cell, or None."""
    versions = check(backend, integration)
    runner = _builtin if backend == "builtin" else _scanpy
    return runner(m, batches, batch_key, integration, settings, Path(out_dir), versions)
