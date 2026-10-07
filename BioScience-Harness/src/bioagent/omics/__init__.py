"""Analysis pipelines: sequencing reads to quantified, tested and reported results.

``fastq``      reading, quality control and trimming of sequencing files
``quant``      transcript quantification (k-mer pseudoalignment and EM) and gene totals
``deseq``      differential expression with a negative binomial GLM (the DESeq2 method)
``de_backends`` one entry for differential expression by the built-in implementation
               or PyDESeq2: one design, one contrast, one result contract
``gsea``       preranked gene set enrichment analysis with GSEApy, on a GMT snapshot
``optional``   importing a selected optional package, or refusing with the reason
``backends``   the standard command-line tools (fastp, salmon, kallisto, HISAT2,
               featureCounts) when they are installed
``rnaseq``     the RNA-seq pipeline from a sample sheet to a report
``sc``         single-cell building blocks (QC, doublets, Harmony, Leiden, UMAP,
               markers, annotation, trajectories, pseudobulk)
``scrna``      the single-cell pipeline from count matrices to annotated clusters
``svgplot``    dependency-free SVG figures for the reports
``commands``   the ``bioagent`` subcommands

Every step records its parameters, the versions of the tools that ran, and digests of
its inputs and outputs, so a reported result can be traced to what produced it. See
``docs/analysis-pipelines.md``, and ``docs/omics-backends.md`` for the selectable
implementations.
"""
