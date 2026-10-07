"""Single-cell RNA-seq building blocks, used by ``bioagent.omics.scrna``.

``io``          count matrices: 10x directories and HDF5, AnnData, text tables
``analysis``    steps 3 to 6 by the built-in parts or by Scanpy, with Harmony or scVI
``qc``          per-cell metrics and MAD-based filtering
``doublets``    Scrublet's simulated-doublet kNN score
``preprocess``  normalisation, highly variable genes, scaling, PCA
``harmony``     Harmony batch integration of the PCA embedding
``graph``       kNN graph and UMAP's fuzzy connectivities
``leiden``      Leiden community detection
``umap``        UMAP layout
``markers``     Wilcoxon rank-sum marker genes
``annotate``    marker-based cell-type annotation
``trajectory``  diffusion maps, diffusion pseudotime and PAGA
``pseudobulk``  per-sample, per-cell-type DESeq2 between conditions (either backend)
"""
