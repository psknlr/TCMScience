"""Analysis pipelines: sequencing reads to quantified, tested and reported results.

``fastq``      reading, quality control and trimming of sequencing files
``quant``      transcript quantification (k-mer pseudoalignment and EM) and gene totals
``deseq``      differential expression with a negative binomial GLM (the DESeq2 method)
``backends``   the standard command-line tools (fastp, salmon, kallisto, HISAT2,
               featureCounts) when they are installed
``rnaseq``     the RNA-seq pipeline from a sample sheet to a report
``svgplot``    dependency-free SVG figures for the reports
``commands``   the ``bioagent`` subcommands

Every step records its parameters, the versions of the tools that ran, and digests of
its inputs and outputs, so a reported result can be traced to what produced it. See
``docs/analysis-pipelines.md``.
"""
