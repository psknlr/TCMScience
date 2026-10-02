"""Spatial transcriptomics on processed data: read, check, QC, cluster, neighbours, statistics.

What this package does, and what it does not:

* It reads **processed** outputs (Space Ranger Visium / Visium HD bins, or an ``.h5ad``
  with tissue coordinates), checks that expression, positions, scale factors and image
  agree, and refuses data that cannot support a spatial analysis.
* It runs a deterministic minimal workflow (``pipeline.run_spatial``): QC, normalisation,
  variable genes, PCA, expression clusters, markers, a per-section neighbour graph,
  Moran's I and neighbourhood enrichment, and writes a fixed output contract with
  provenance and limitations.
* It does **not** process raw sequencing, segment cells, register sections, reconstruct
  3D tissue, deconvolve spots into cell types or infer cell–cell communication. Those
  catalogue entries remain unavailable.

The core needs numpy only (Matrix Market input); ``h5py`` (the ``spatial`` extra) adds the
10x HDF5 and ``.h5ad`` readers and the ``.h5ad`` output. ``runner.run_isolated`` executes
a run in a separate interpreter with a timeout and memory limit, and returns only the
summary and output paths.
"""

from .graph import SpatialGraph, assert_section_isolated, build_graph
from .io import SpatialInputError, SpatialSection, read_h5ad_spatial, read_visium
from .matrix import CSR
from .pipeline import SpatialConfig, load_config, run_spatial
from .statistics import morans_i, neighbourhood_enrichment

__all__ = ["CSR", "SpatialInputError", "SpatialSection", "read_visium", "read_h5ad_spatial",
           "SpatialGraph", "build_graph", "assert_section_isolated", "morans_i",
           "neighbourhood_enrichment", "SpatialConfig", "load_config", "run_spatial"]
