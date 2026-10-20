# Analysis pipelines

These pipelines take your own data or molecules through a complete analysis in one
call. Each one records every parameter, tool version and file digest, so a result can
be traced back to what produced it. Each also states what its result is and what it is
not. The governed skills that wrap them are **candidates**: they run as development
runs, which are recorded but never released, until a person reviews and promotes them.

| Pipeline | Command | Status |
|---|---|---|
| RNA-seq: FASTQ, or a count matrix, to differential expression | `bioagent rnaseq` | below |
| Single-cell: count matrices to an annotated atlas | `bioagent scrna` | below |
| Protein structure: sequences to checked models | `bioagent fold` | below |
| Docking: a receptor and ligands to validated poses | `bioagent dock` | below |
| ADMET: structures to properties, alerts and predicted endpoints | `bioagent admet` | below |
| Network pharmacology: a formula to bounded mechanism hypotheses | `bioagent research` | below |

## What is verified where

Every pipeline is implemented and runs on the person's own runner (in Studio, as a runner
job); network pharmacology also runs as browser jobs, with no runner (checked on a known answer and on fixture files;
and on the real 葛根芩连汤 files, whose exports were the native run's bytes in `live-data` #18, #19 and #21:
[BROWSER_JOBS.md](../studio/docs/BROWSER_JOBS.md)). So, in part, do RNA-seq and single cell: RNA-seq from a count
matrix and single cell from count tables, with the built-in engines, up to 6,000 cells (66,000,000 cells × genes in a
table) or 10,000,000 genes × samples
([In the browser](#in-the-browser-count-matrices-and-count-tables), below); they were compared output by output with
the native command on their known-answer references in headless Chromium, locally and in Studio's CI (`studio` #91,
2026-10-19). So, in part, does ADMET: the model build (`admet.models`) and the predictions (`pipeline.admet`) from a
CSV/TSV/`.smi` file or one SMILES, with its chemistry in RDKit.js 2026.9.1 and its models built in the browser from the
TDC archive ([In the browser](#in-the-browser), below); they were compared with the native command in headless
Chromium on a known-answer archive (locally and in Studio's CI, `studio` #95, 2026-10-20), on a synthetic one at the
TDC sizes (locally) and on the real TDC archive (`live-data` #24: run 39386727431, commit 0882e95, 2026-10-20); the GPU
domain search has not been run on a physical GPU. Structure and docking do not run in the browser, and no pipeline has
a record from a deployed service. What has been exercised differs by engine:

| Pipeline | Engines | Exercised where this was written | CI job with the engines | Reference dataset |
|---|---|---|---|---|
| RNA-seq | built-in QC, trimming, quantification and DE; fastp, salmon, kallisto, HISAT2 + featureCounts; PyDESeq2; R's DESeq2 as the reference | the built-in path, from FASTQ and from a count matrix (also in Pyodide in headless Chromium); PyDESeq2 in a separate environment | "Analysis pipelines with the standard tools" | `omics_world` v1 and, for the count-matrix mode, `omics_counts_world` v1: **simulated**, known answer |
| Single cell | built-in or Scanpy; Harmony (built-in or harmonypy) or scVI; built-in DE or PyDESeq2 for pseudobulk | the built-in path (also in Pyodide in headless Chromium, on count tables), Scanpy, harmonypy | the same job; scVI in "scVI, trained on the CPU" | `sc_world` v1: **simulated**, known answer |
| Structure | ESM Atlas (remote, `--allow-remote`), local ESMFold, ColabFold | the 1UBQ reference from the stored ESM Atlas model, offline; no local ESMFold weights, no ColabFold | "Analysis pipelines with the standard tools" (TM-align comparison) | PDB 1UBQ (public) |
| Docking | AutoDock Vina 1.2, meeko, RDKit, gemmi | RDKit and meeko, with a stand-in for Vina that the tests name | "Molecular modelling with the standard tools" (real Vina, 3PTB redocking) | the 3PTB pocket (public) |
| ADMET | RDKit, scikit-learn, models trained from the TDC archive; in the browser, RDKit.js 2026.9.1 + ports and scikit-learn 1.8.0 in Pyodide | RDKit and scikit-learn; the archive only where `BIOAGENT_TDC_ARCHIVE` names a copy; the browser mode in headless Chromium, locally | "Molecular modelling with the standard tools"; the browser mode on the known-answer archive in Studio's CI (`studio` #95, 2026-10-20), and on the real TDC archive in `live-data.yml`: passed in `live-data` #24 (run 39386727431, 2026-10-20), not on a physical GPU | the TDC ADMET group (public, a download) |
| Network pharmacology | `bioagent.research` on ledger-verified snapshots | planted worlds and fixtures only (`+planted`, `+fx`) | the unit tier; real data in `live-data.yml` (葛根芩连汤): passed in `live-data` #16 and #17 (2026-10-19) | planted worlds: **simulated**, known answer |

Since stages and references were added (2026-10-18), the engine jobs also run
`tests/test_runrecord.py` and `tests/test_pipeline_references.py`. Those two files need no
engine (the RNA-seq and single-cell references pin the built-in backends; the docking one
compares its fixture's digest), so they check the records and the reference results, not
the engines; the engines are checked by each job's own tests. From `ci` #282 (2026-10-11, 20:06
UTC) until 2026-10-19 no CI run was given a runner; in `ci` #317 (2026-10-19) every engine job
passed, these two files included, all on CPU runners ([validation.md](validation.md#not-verified-here-and-why)).
The count-matrix mode's tests (`tests/test_omics_rnaseq_counts.py`, and its reference in
`tests/test_pipeline_references.py`) joined that job with commit 65fa6aa; it passed in `ci` #322 (run 39286930329, 2026-10-19: 231 passed, 2
skipped).
Those jobs run each engine on a small reference or fixture, not on real data. Remote compute for these pipelines is designed, not
deployed ([studio/docs/REMOTE_COMPUTE.md](../studio/docs/REMOTE_COMPUTE.md)).

Three things hold whatever the engine:
- The built-in DESeq2 implementation, PyDESeq2 and R's DESeq2 implement the same method
  but do not give numerically identical results; how far they agree, and where they
  differ, is measured in [omics-backends.md](omics-backends.md), and each run's
  `limitations.md` says so.
- A docking score is the scoring function's estimate, never a measured affinity.
- An ADMET prediction outside its model's applicability domain is flagged and unreliable,
  and a structure prediction is never replaced by a retrieved structure: a reference is
  only compared with.

## RNA-seq: FASTQ to differential expression

```bash
bioagent rnaseq --samples samples.csv --transcripts transcripts.fa \
    --annotation genes.gtf --design "~ condition" \
    --contrast condition,treated,control --out results/
bioagent rnaseq --verify results/           # re-check every digest in run.json
```

The sample sheet follows nf-core/rnaseq. It has `sample,fastq_1,fastq_2` columns and
then the design variables. Rows with the same sample name are lanes of one library and
are merged. A design variable is a factor unless it is named with `--covariate`, so a
batch coded 1, 2, 3 is not read as a number.

```
sample,fastq_1,fastq_2,condition,batch
ctrl1,ctrl1_R1.fastq.gz,ctrl1_R2.fastq.gz,control,1
trt1,trt1_R1.fastq.gz,trt1_R2.fastq.gz,treated,1
```

**From a count matrix** (`bioagent.omics.rnaseq_counts`), for counts made elsewhere (featureCounts, salmon through
tximport, a published matrix):

```bash
bioagent rnaseq --counts counts.tsv --samples samples.tsv --design "~ condition" \
    --contrast condition,treated,control --out results/
```

- `--counts` is a gene × sample table, TSV or CSV (gzipped or not): `gene_id`, an optional `gene_name`, then one
  column per sample, every value a non-negative integer. Refused, with the line and sample named: a value that is not
  a number, negative, not finite or not an integer ("round estimated counts first"), 2^53 or more, a duplicated gene
  id or sample name, fewer than two samples, a sample with no counts at all, a tab or line break inside a name; and a
  gzipped table whose stream is corrupt or cut short ("… cannot be read to its end (…)"), never a crash.
- `--samples` is then a sample table: a `sample` column and the design variables, one row per count column. A FASTQ
  sheet (`fastq_1`/`fastq_2`) is refused: reads go through the full pipeline.
- The FASTQ options (`--transcripts`, `--genome`, `--annotation`, `--engine`, `--trimmer`, `--fragment-mean`,
  `--fragment-sd`, `--strand`) are refused (exit 2), never ignored. `--design`, `--contrast`, `--covariate`,
  `--de-backend` and `--alpha` mean what they mean for FASTQ.
- From the counts on, the steps are the full pipeline's, by the same code: differential expression, VST, PCA, sample
  distances, the same tables and figures, `report.md`/`report.html`, `limitations.md` and `run.json`. No read was
  seen, so there is no QC, trimming or quantification, and `limitations.md` says the counts were analysed as given.
  On the FASTQ reference's counts, rounded into a table, this mode writes the full pipeline's `deseq2_results.tsv`,
  `de_diagnostics.tsv` and `vst.tsv` byte for byte (a test checks it).
- `run.json` names the pipeline `bioagent.omics.rnaseq_counts`, the input kind `counts`, each input (path, SHA-256,
  size) and the content digests of the canonical count and sample tables; `bioagent rnaseq --verify` checks either
  mode. Its stages are `validate_counts`, `differential_expression`, `explore`, `report`, `verify`.

### Steps

| Step | Built in | Standard tool, used when installed |
|---|---|---|
| QC | FastQC's modules and pass/warn/fail limits: per-base and per-read quality, GC (with FastQC's GC model), N, adapters, duplication, over-represented sequences | — |
| Trimming | cutadapt's algorithms: BWA-style 3' quality trimming; adapter search with a 10% error rate, no errors under ten bases, 3' overhang; poly-G; pairs kept or dropped together | fastp |
| Quantification | kallisto's method: canonical k-mer index, equivalence classes, kallisto's EM and convergence rule, effective lengths from a truncated fragment distribution (estimated from pairs) | salmon, kallisto |
| Alignment | — | HISAT2 + samtools + featureCounts (`--engine hisat2`, with `--genome`) |
| Gene totals | tximport's `summarizeToGene` rules, `lengthScaledTPM` by default | — |
| Differential expression | the DESeq2 method (Love et al. 2014): median-of-ratios size factors, Cox–Reid dispersions shrunk to a parametric trend, NB GLM, Wald test, Cook's distances, independent filtering, Benjamini–Hochberg | — |
| Exploration | VST, PCA of the 500 most variable genes, sample distances | — |
| Report | `report.md`, `report.html` with SVG figures (quality, assignment, PCA, distances, MA, volcano, p-values, dispersions), `run.json` | — |

`--engine auto` uses salmon, then kallisto, then the built-in quantifier, whichever is
installed first. `--trimmer auto` uses fastp, then the built-in trimmer.

`--de-backend pydeseq2` runs the test with PyDESeq2 instead, on the same design matrix
and contrast, into the same result columns. If PyDESeq2 is not installed the run is
refused, not switched to the built-in test.
[omics-backends.md](omics-backends.md) says how the two are kept equivalent and where
they differ.

One built-in step goes further than kallisto. By k-mers alone, a read pair whose mates
both lie in shared exons is also compatible with an isoform that has an extra exon
between them, where the fragment would be longer by that exon. The quantifier
therefore drops a transcript from a pair's class when the fragment it implies there is
longer than the estimated distribution's mean + 5 SD. On the simulated
exon-skipping gene in the tests, this moves the estimate for the skipping isoform from
27.6% to 29.0% of TPM (truth 30%).

### How it is checked

- **Against the method's definitions**: size factors, poscounts, Benjamini–Hochberg
  against SciPy, kallisto's effective lengths, the EM on a hand-computable split,
  cutadapt's documented quality-trimming example, FastQC's limits.
- **Against a simulated experiment with a known answer** (`tests/omics_world.py`): 30
  genes on a synthetic chromosome, exon-skipping isoforms, six libraries with negative
  binomial counts, and seven genes planted four-fold up or down. The built-in path,
  salmon, kallisto, fastp and HISAT2 + featureCounts each recover all seven, with at
  most one false discovery. A re-run gives byte-identical tables, and an edited table
  fails `verify_run`.
- **Against PyDESeq2**, an independent implementation of the same method. On 3,000
  simulated genes (4 vs 4, 10% planted):
  - size factors are identical;
  - log2 fold changes correlate at 0.99999;
  - −log10 p-values correlate at 0.9995;
  - 247 of PyDESeq2's 247 significant genes are among this implementation's 251;
  - the observed FDR is 0.048 at a nominal 0.05.

  Where gene-wise dispersions differ, PyDESeq2's bounded optimiser stopped at the floor
  (1e-8) and this implementation found a higher Cox–Reid adjusted profile likelihood.
- In CI, the job "Analysis pipelines with the standard tools" installs the tools and
  PyDESeq2. In that job a missing tool fails the run rather than skipping it. The
  job's tests also pass with Ubuntu 24.04's packages, installed where this was built:
  fastp 0.23.4, salmon 1.10.2, kallisto 0.48.0, HISAT2 2.2.1, featureCounts (subread)
  2.0.6 and samtools 1.19.2. Each run records the version it found.
- **Against R's DESeq2** (1.42.0): with four samples per condition the built-in
  implementation calls all of R's genes and a few more. With two, it does not
  reproduce R's calls, for reasons traced in [omics-backends.md](omics-backends.md).

### What a result is

It is a statistical association within the experiment analysed: which genes differ
between the groups of these samples, with this reference and these settings. It does
not establish a mechanism, a cause or any clinical effect.

The governed skill `rnaseq-differential-expression` (`skills/candidates/omics/`) makes
at most one claim, and its kind follows from the design you declare:
- `observational` (human samples): an `association`;
- `in_vitro` or `animal`: a `mechanism_hypothesis`, and nothing about people.

Its evidence is the result table itself: each cited row carries a receipt that locates
it in the table.

### Limits

- Fold changes are maximum-likelihood estimates; no apeglm/ashr shrinkage is applied.
- The built-in quantifier has no sequence- or GC-bias correction, and its index holds
  about 25 bytes per k-mer occurrence. It suits gene panels, small genomes and test
  data; for a mammalian transcriptome, install salmon or kallisto.
- The built-in steps read about 10^5 reads per second. For large libraries, use the
  standard tools.
- The design is additive only: no interaction terms or LRT.
- The built-in test, PyDESeq2 and R's DESeq2 are not numerically identical; with three or
  fewer residual degrees of freedom the built-in test does not reproduce R's calls
  ([omics-backends.md](omics-backends.md)).
- The count-matrix mode cannot check how its counts were made: library preparation, aligner or quantifier,
  annotation and filtering are unknown to it.

## Single-cell: count matrices to an annotated atlas

```bash
bioagent scrna samples.csv --out atlas/ --root "NK cell"      # or one matrix instead of a sheet
bioagent scrna --verify atlas/
```

The sample sheet lists `sample,path` and any sample variables (`condition`, `batch`, ...).
`path` may be:
- a Cell Ranger directory or HDF5 file;
- an `.h5ad`;
- a genes x cells CSV/TSV.

Every input must be raw counts; normalised values are refused. A CSV/TSV table is read a row at a time, keeping each
row's non-zero counts, so it needs memory for its counts rather than for its text (the matrix is the one a parse of
the whole text gives; for one table of 1,356 cells × 33,000 genes in Pyodide, 1.55 GB instead of 2.0 GB at the run's
peak; after reading, the doublet scores' dense matrix dominates). A blank line is skipped; a row with the wrong number
of values is refused ("rows have N values for M cells"); a gzipped table or Cell Ranger file whose stream is corrupt
or cut short is refused with the reason ("… cannot be read to its end (…)"); a sheet line with an empty `path` is
refused ("line N of <sheet> names no path").

### Steps

| Step | Method (defaults as in Scanpy or single-cell best practice) |
|---|---|
| QC | genes, counts, mitochondrial and ribosomal shares; outliers beyond 5 MADs per sample (3 for mitochondria, above 8%), ≥ 200 genes per cell, ≥ 3 cells per gene |
| Doublets | Scrublet: simulated doublets, kNN score with Scrublet's Bayesian form, threshold at the histogram minimum (no call when it is not bimodal); removed, or kept and flagged |
| Normalisation | 10,000 counts per cell, log1p; 2,000 HVGs by the Seurat method (batch-aware); scaling clipped at ±10; PCA |
| Integration | Harmony on the PCA embedding over `batch` (else the samples); mixing reported before and after |
| Graph and clusters | exact 15-NN, UMAP's fuzzy connectivities, Leiden (modularity, resolution 1) |
| Layout | UMAP: spectral start, a/b fitted to min_dist 0.5; edges due in an epoch updated together |
| Markers | Wilcoxon rank-sum per cluster against the rest, tie-corrected, BH |
| Annotation | Scanpy's `score_genes` against a marker panel (30 human types shipped with their sources, or your own); `unassigned` when no type leads |
| Trajectory | PAGA connectivity and tree; diffusion pseudotime from a named root, over the clusters PAGA joins to it |
| Conditions | pseudobulk per sample and cell type, DESeq2 method; the samples are the replicates |

Three choices are independent of one another:
- `--analysis-backend scanpy` runs normalisation to markers with Scanpy;
- `--integration none|harmony|scvi` sets the batch integration (for scVI,
  `--scvi-epochs` and `--scvi-threads` set its training);
- `--de-backend pydeseq2` runs the pseudobulk test with PyDESeq2.

`run.json` records which implementation ran each stage. See
[omics-backends.md](omics-backends.md).

### How it is checked

- **Against the reference implementations.** In CI's tools job:
  - Scanpy's Seurat-method HVGs: 200 of 200 identical;
  - Scanpy's tie-corrected Wilcoxon scores: identical;
  - leidenalg's modularity: identical on three graphs;
  - umap-learn's fuzzy connectivities on the same neighbours: within 5×10⁻⁶.

  The Wilcoxon p-value of one gene also equals SciPy's Mann–Whitney.
- **Against a simulated experiment with a known answer** (`tests/sc_world.py`). It has
  four samples in two batches, four cell types marked by real marker genes, a path of
  cells between two types, 5% low-quality cells, 6% doublets, and a condition effect in
  one type.
  - Every low-quality cell is removed.
  - More than 70% of doublets are found, while fewer than 6% of cells of the discrete
    types are called doublets.
  - Batch mixing rises from below 0.3 to above 0.8.
  - Clusters match the types with an ARI above 0.9, and each cluster is named its true
    type.
  - Pseudotime follows the planted path (Spearman above 0.85), and cells off the path
    get none.
  - Pseudobulk finds the planted condition effect in B cells, and at most one gene in
    each of the other types (none in the recorded run).
  - A re-run gives identical tables.

### What a result is

- **Clusters** are groups of transcriptionally similar cells in this data set.
- **A cell-type label** is an inference from marker expression against a panel.
- **Pseudotime** orders cells by similarity from a root you choose, and is not time.
- **A pseudobulk difference** is an association between conditions in these samples.

The governed skill `scrna-cell-atlas` (a candidate) reports clusters, labels and
pseudotime as outputs. Its only claims are pseudobulk comparisons, of the kind the
declared design allows.

### Limits

- Scrublet also scores cells in a continuous transition between two types as doublets.
  When over twice the expected rate is called, the report says so; rerun with
  `--doublets flag` to keep them.
- Neighbours are found exactly and the scaled matrix is dense. Both suit tens of
  thousands of cells, not millions.
- The UMAP coordinates are not umap-learn's. The layout is a picture of the graph, not
  a measurement.
- `--n-top-genes` (default 2,000; the runner's `n_top_genes`, 50 to 10,000) sets how many highly variable genes are
  kept; the reference runs with 600.

## In the browser: count matrices and count tables

In Studio, two of these pipelines also run as browser jobs, with no runner, in Pyodide job workers holding numpy and
scipy ([BROWSER_JOBS.md §3.1](../studio/docs/BROWSER_JOBS.md#31-rna-seq-and-single-cell-from-count-tables)):

- `pipeline.rnaseq` in its count-matrix mode (`counts` and `samples`), with the built-in DESeq2 implementation;
- `pipeline.scrna` on count tables (CSV, TSV or TXT, gzipped or not, or a `.csv`/`.tsv` sample sheet of them), with the
  built-in analysis and built-in Harmony or none.

The inputs are files of the person's project, copied into the job's private storage and checked against their
recorded SHA-256. Refused there with the reason, for the runner: a FASTQ run, PyDESeq2, Scanpy, scVI, 10x HDF5 and
`.h5ad` sources, and more than 6,000 cells, a table of more than 66,000,000 cells × genes (the doublet scores hold one
sample's cells × genes densely) or a count matrix of more than 10,000,000 genes × samples (a browser worker's wasm32
memory holds about 4 GB; measured in Pyodide 314.0.7 under Node, one run each, not in a browser tab: four tables of
1,356 cells × 33,000 genes took 1.9 GB, one table of 2,000 × 33,000 2.2 GB, 10 M counts 1.8–2.0 GB; a `MemoryError` is
the same refusal). `counts` with a FASTQ sample sheet as `samples` is refused as bad arguments, as the runner refuses
it. On `omics_counts_world` v1 and `sc_world` v1 (as count tables), compared
output by output with the native command on the same files at one BLAS thread (`studio/web/test/runtime/science.mjs
--mode omics`):

- identical bytes: RNA-seq `counts.tsv`, `vst.tsv`, `report.md`, `limitations.md`; single-cell `clusters.tsv`,
  `markers.tsv`, `paga.tsv`, `report.md`, `limitations.md`, `cells.tsv` apart from the UMAP coordinates;
- within a stated tolerance (|a − b| ≤ 1e-6 + 1e-4·max + one unit in the sixth significant digit; the largest
  difference 0.133 of it): `deseq2_results.tsv`, `de_diagnostics.tsv` and the four pseudobulk tables, all DESeq2 fits,
  whose dispersion search turns last-bit differences between numpy builds into printed digits;
- not compared, with the reason: the UMAP coordinates and five figures; the layout's structure is checked instead
  (each cell's 15 nearest UMAP neighbours in its cluster: browser 0.9985, native 0.9983);
- exactly the same: the 90 genes called, the pseudobulk calls, each of the 1,077 cells' cluster and cell type;
- the known answers on the browser's files: all 80 planted genes found in their direction with 10 false discoveries
  among 90 calls; ARI 1.0, the four cell types, 16 of 20 planted B-cell genes, mixing 1.03.

Tested locally in headless Chromium 141 with Pyodide 314.0.7 (numpy 2.4.6, scipy 1.18.0), against native Python
3.13.16 with numpy 2.5.4 and scipy 1.18.1; and in Studio's CI with Pyodide's numpy and scipy from jsDelivr, against
CI's Python 3.12.15 (`studio` #91, 2026-10-19), with the same results but the native UMAP agreement, 0.9981 there. The
tolerance was fitted on these two simulated references and is not established for other data; other settings (a
pseudotime root, `integration: none`, a marker table of one's own) were not compared in a browser, and a person's own
run is compared with nothing: its limitations say that a value near a threshold (an adjusted p-value near alpha) may
differ.

## Protein structure: sequences to checked models

```bash
bioagent fold proteins.fasta --out models/ --allow-remote --reference ubq=PDB:1UBQ:A
bioagent fold proteins.fasta --out models/ --method esmfold      # locally, no network
bioagent fold --verify models/
```

Three predictors are available:

| `--method` | What runs | Where the sequence goes |
|---|---|---|
| `esmatlas` (default) | ESMFold (Lin et al. 2023) through Meta's ESM Atlas service; up to 400 residues | to `api.esmatlas.com`, so it needs `--allow-remote` |
| `esmfold` | ESMFold locally through Hugging Face `transformers` (`pip install 'bioagent[fold]'`; a GPU helps) | nowhere |
| `colabfold` | AlphaFold2 through `colabfold_batch`, when it is installed | to ColabFold's MSA server unless it is configured otherwise, so it also needs `--allow-remote` |

A sequence that must stay confidential should be folded with `esmfold`. A reference
(`--reference NAME=REF`) may be an experimental entry (`PDB:<id>[:chain]`), an AlphaFold DB
model (`UniProt:<accession>`) or a local file; fetching one also needs `--allow-remote`.

### Steps

| Step | What is computed |
|---|---|
| Model | the predicted chain, written as PDB with pLDDT (0–100) in the B-factor column |
| Confidence | mean pLDDT; the shares at ≥ 90, ≥ 70 and < 50; the segments below 70 and below 50; PAE when the method gives it |
| Secondary structure | DSSP from backbone hydrogen bonds (Kabsch & Sander 1983) |
| Geometry | radius of gyration; consecutive CA–CA distances off 3.8 Å; heavy-atom clashes; backbone dihedrals outside the allowed regions |
| Agreement | against the reference: sequence alignment (BLOSUM62, affine gaps, free end gaps), TM-score normalised by each length, RMSD and GDT-TS, and the per-residue distance after superposition |
| Report | the pLDDT, contact-map, PAE and distance figures; `report.md` / `report.html`; `run.json` with the request and response digests of every prediction |

### How it is checked

- **TM-score against TM-align.** In CI's tools job, the TM-score of a model against the
  crystal structure equals TM-align's (`tmtools`) to within 0.005.
- **DSSP against a known assignment.** Ubiquitin's (1UBQ) α-helix 23–34 and its four
  long β-strands (2–7, 12–16, 41–45, 66–71) are recovered.
- **Superposition** recovers a rigid motion exactly: RMSD 0, TM-score and GDT-TS 1.
- **A stored prediction against the crystal structure.** The ESMFold model of ubiquitin
  (from the service, 2026-10-07) scores TM-score 0.958 and RMSD 0.83 Å against 1UBQ
  offline. The same holds live (`tests/test_structure_live.py`, marked `integration`).
- **The run records itself.** `verify_run` finds a changed model file.

### What a result is

A computational prediction. pLDDT is the method's confidence in each residue's local
structure, not a measurement, and says nothing about how domains sit relative to each
other (PAE does). Regions below 70 should not be interpreted. A model is one chain,
without ligands, cofactors, modifications or partners.

The governed skill `predict-protein-structure` (a candidate) records the models and
their checks as outputs and makes no claim. It declares the three hosts it may reach,
and it reaches them only when the call passes `allow_remote`.

### Limits

- The service folds at most 400 residues; longer chains need `esmfold` or `colabfold`.
- DSSP here leaves out beta bulges and bends, so a strand broken by a bulge is reported
  as two strands.
- Only single chains are predicted: complexes, and models with ligands, are outside
  this pipeline.

## Docking: a receptor and ligands to validated poses

```bash
bioagent dock 3PTB.pdb ligands.csv --site-ligand BEN --out dock/            # a local file
bioagent dock PDB:3PTB:A ligands.csv --site-ligand BEN --allow-remote --out dock/
bioagent dock receptor.pdb ligands.sdf --center 10,12,8 --size 22,22,22 --out dock/
bioagent dock --verify dock/
```

The receptor can be a PDB file, an RCSB entry (`PDB:<id>[:chains]`) or an AlphaFold DB
model (`UniProt:<accession>`); fetching either of the last two needs `--allow-remote`.
The ligands can be a CSV (`name,smiles`), an SDF, a `.smi` file or one SMILES. The site
can be a co-crystal ligand (`--site-ligand`), residues (`--site-residue A:189`) or an
explicit box (`--center`, `--size`).

### Steps

| Step | What is done |
|---|---|
| Receptor | waters and unnamed hetero groups removed, the first alternate location kept; meeko's residue templates add polar hydrogens, Gasteiger charges and AutoDock types; residues that match no template are reported |
| Ligands | the largest fragment; RDKit ETKDG v3 conformer (seeded) and MMFF94 relaxation; meeko's PDBQT and torsion tree; protonation as given |
| Site | a box around the co-crystal ligand (8 Å of padding, at least 18 Å a side), around residues, or as given |
| Validation | the co-crystal ligand, re-embedded from its SMILES (the RCSB Chemical Component Dictionary's when none is given), is docked back; the setup is validated when the top pose is within 2 Å heavy-atom RMSD (symmetry-aware) of the crystal pose |
| Docking | AutoDock Vina 1.2 (Vina or Vinardo scoring), exhaustiveness 8, nine poses, a fixed seed |
| Contacts | hydrogen bonds (3.5 Å), salt bridges (4.0 Å) and hydrophobic contacts (4.0 Å) of each best pose, by residue |
| Report | scores with ligand efficiency, the validation, contacts, 2D depictions, every pose as SDF, `run.json` with the receptor's digest, the box, the seed and every output's digest |

### How it is checked

**Redocking known complexes** (full receptors, defaults):

| Complex | Top pose, heavy-atom RMSD to the crystal pose | Score |
|---|---|---|
| trypsin–benzamidine (3PTB) | 0.37 Å | −6.0 kcal/mol |
| streptavidin–biotin (1STP) | 0.61 Å | −7.5 kcal/mol |

The test fixture, the 12 Å pocket of 3PTB, gives 0.38 Å. In it, benzamidine's amidine
hydrogen-bonds Asp189 at the bottom of the S1 pocket, as in the crystal, and
benzamidine outscores octane.

Also checked: an unreadable ligand is recorded as a failure and the run goes on; a site
without a co-crystal ligand is reported as not validated; `verify_run` finds a changed
output. The CI tools job installs Vina, meeko and RDKit and runs these tests.

### What a result is

A docking score is the scoring function's estimate, not a measured affinity. Across
benchmark sets it tracks measured binding with a correlation near 0.5 and errors of
about 2 kcal/mol. A pose is a hypothesis about how a molecule could sit in the site.

The governed skill `dock-ligands` (a candidate) claims a `mechanism_hypothesis` per
ligand only when the setup was validated by redocking; otherwise it claims nothing.

### Limits

- The receptor is rigid: induced fit, waters and metal coordination are not modelled.
- Protonation states and tautomers are as given; no pKa model is applied.
- One conformer is embedded per ligand. Vina searches its torsions, but ring
  conformations stay as embedded.

## ADMET: structures to properties, alerts and predicted endpoints

```bash
bioagent admet --build-models                  # once: fetch the TDC archive, train the models
bioagent admet molecules.csv --out admet/
bioagent admet --verify admet/
```

### Steps

| Step | What is done |
|---|---|
| Standardisation | RDKit MolStandardize: clean-up, the largest organic fragment (salts and solvents dropped), neutralisation of charges that can be neutralised |
| Properties | 24 RDKit descriptors (MW, Crippen logP, TPSA, H-bond donors and acceptors, rotatable bonds, rings, QED and others) |
| Rules | Lipinski (one violation allowed), Veber, Egan and Ghose, on those descriptors |
| Alerts | RDKit's filter catalogues: PAINS A/B/C (Baell & Holloway 2010), Brenk (2008), NIH (Jadhav 2010) |
| Endpoints | one model per endpoint of the Therapeutics Data Commons ADMET benchmark group (Huang et al. 2021): a gradient-boosted tree over a 2048-bit Morgan count fingerprint and the descriptors, trained on TDC's `train_val` split with early stopping. Targets spanning orders of magnitude are modelled as log10 |
| Domain | the largest Tanimoto similarity of the query to the model's training molecules; below 0.3 the prediction is flagged as outside the model's applicability domain. The search was rewritten over a `uint64` view with `np.bitwise_count`, chunked so the temporary stays small: measured on this machine (numpy 2.4.6) with random 2048-bit fingerprints, 1,000 queries against 10,504 references (the size of cyp2d6_veith's training set) take about 0.43 s natively (the old code took about 15.0 s at a chunk of 64, and 35 s with a 6.7 GB peak at its default chunk of 512), and about 0.7 s in Pyodide 314.0.7 under Node, where the old default chunk raised `ValueError: array is too big`. The outputs are unchanged (the nearest training molecule, with ties broken by the smallest index as numpy's `argmax` does) |
| Report | per molecule: properties, rules, alerts and predictions; the model cards; `run.json` with each model's digest |

The archive is about 1.5 MB, fetched once from Harvard Dataverse and held to its pinned
SHA-256. The models are built where they run, and nothing trained is shipped. Each model
card records the data, the archive's digest, the train and test sizes, the TDC metric on
the test set, the features, the library versions, the seed and iteration cap, and the
SHA-256 of the model file and of its applicability-domain file, both checked before they
are loaded.

A full build takes from minutes to most of an hour, depending on the machine. Each card is
written as soon as its model is saved, so an interrupted build keeps what it finished, and
running `--build-models` again trains only the endpoints that are missing or no longer
stand: a model is kept only when both files match its card and the card was built from
the same archive, seed, iteration cap, scikit-learn and RDKit. `--rebuild` trains every
endpoint asked for again.

### How it is checked

**Held-out scores on TDC's scaffold-split test sets** — molecules whose scaffolds the
model never saw — from one build (seed 0) on this machine. The splits and metrics are
TDC's own, so these can be read against its leaderboard.

| group | endpoint | what it is | metric | held-out score | train | test |
|---|---|---|---|---|---|---|
| absorption | `caco2_wang` | Caco-2 cell permeability | MAE | 0.2818 | 728 | 182 |
| absorption | `hia_hou` | human intestinal absorption (absorbed) | AUROC | 0.9239 | 461 | 117 |
| absorption | `pgp_broccatelli` | P-glycoprotein inhibition | AUROC | 0.8984 | 973 | 245 |
| absorption | `bioavailability_ma` | oral bioavailability above 20% | AUROC | 0.7509 | 512 | 128 |
| absorption | `lipophilicity_astrazeneca` | lipophilicity | MAE | 0.5491 | 3360 | 840 |
| absorption | `solubility_aqsoldb` | aqueous solubility | MAE | 0.7961 | 7985 | 1995 |
| distribution | `bbb_martins` | blood-brain barrier penetration | AUROC | 0.8948 | 1624 | 406 |
| distribution | `ppbr_az` | plasma protein binding | MAE | 8.2276 | 2231 | 559 |
| distribution | `vdss_lombardo` | volume of distribution at steady state | SPEARMAN | 0.652 | 904 | 226 |
| metabolism | `cyp2c9_veith` | CYP2C9 inhibition | AUPRC | 0.7736 | 9673 | 2419 |
| metabolism | `cyp2d6_veith` | CYP2D6 inhibition | AUPRC | 0.6808 | 10504 | 2626 |
| metabolism | `cyp3a4_veith` | CYP3A4 inhibition | AUPRC | 0.8758 | 9861 | 2467 |
| metabolism | `cyp2c9_substrate_carbonmangels` | CYP2C9 substrate | AUPRC | 0.3824 | 534 | 135 |
| metabolism | `cyp2d6_substrate_carbonmangels` | CYP2D6 substrate | AUPRC | 0.6799 | 532 | 135 |
| metabolism | `cyp3a4_substrate_carbonmangels` | CYP3A4 substrate | AUROC | 0.6552 | 535 | 135 |
| excretion | `half_life_obach` | half-life | SPEARMAN | 0.452 | 532 | 135 |
| excretion | `clearance_hepatocyte_az` | hepatocyte clearance | SPEARMAN | 0.3335 | 970 | 243 |
| excretion | `clearance_microsome_az` | microsomal clearance | SPEARMAN | 0.5299 | 881 | 221 |
| toxicity | `ld50_zhu` | acute oral toxicity (rat LD50) | MAE | 0.6271 | 5907 | 1478 |
| toxicity | `herg` | hERG channel blockade | AUROC | 0.8445 | 523 | 132 |
| toxicity | `ames` | Ames mutagenicity | AUROC | 0.8574 | 5821 | 1457 |
| toxicity | `dili` | drug-induced liver injury | AUROC | 0.88 | 379 | 96 |

The metric is TDC's for that endpoint: MAE for a regression endpoint (lower is better),
AUROC or AUPRC for a classification endpoint (higher is better), Spearman for the
endpoints TDC ranks by correlation.

The tests also check, on a small synthetic archive with known answers, that the
standardisation strips salts and keeps charges that cannot be neutralised; that the
rules and the alert catalogues fire where they should and stay quiet where they should;
that a model file altered after it was carded is refused; that an archive whose digest
does not match the pin is refused and not written; that a prediction for a molecule
unlike anything in the training set is flagged as outside the domain; and that
`verify_run` finds a changed output.

**Why the domain flag matters here.** TDC's training sets are drug-like synthetic
compounds, and much of what this repository studies is not. Largest Tanimoto similarity
to each model's training set, for four molecules, two of them constituents of medicinal plants:

| Molecule | hERG | Ames | Caco-2 |
|---|---|---|---|
| aspirin | 0.31 | 1.00 | 0.36 |
| quercetin | 0.67 | 1.00 | 0.52 |
| berberine | 0.24 — **outside** | 0.43 | 0.79 |
| a protopanaxadiol-like triterpene | 0.16 — **outside** | 0.33 | 0.50 |

The two flagged predictions are reported with "(outside domain)" and should not be read
as predictions at all. A model that has seen nothing like a saponin has nothing to say
about one.

### What a result is

Every number is a model's prediction, with the error its model card states on unseen
scaffolds, or a published rule applied to computed descriptors. None is a measurement.
Outside a model's applicability domain a prediction is unreliable. A structural alert
marks a substructure often behind assay interference or reactivity — a reason to look
closer, not a verdict; many natural products, flavonoids and catechols especially, carry
PAINS motifs.

The governed skill `predict-admet` (a candidate) therefore makes no claim: properties,
rules, alerts and predictions are outputs. They rank compounds for testing.

### Limits

- The models see a 2D structure. Stereochemistry beyond what the fingerprint encodes,
  conformation and formulation are not modelled.
- The training sets are small for some endpoints, and their held-out scores say so.
- A prediction is for the standardised parent structure, not for a salt or a prodrug as
  administered.

### In the browser

In Studio, ADMET also runs as browser jobs, with no runner
([BROWSER_JOBS.md §3.2](../studio/docs/BROWSER_JOBS.md#32-admet)): `admet.models` builds the 22 endpoint models and
`pipeline.admet` predicts with them. They are `bioagent.admet` itself, through an engine seam
(`bioagent.admet.engine`): natively the default `RDKitEngine` wraps `chem.standardize`/`featurize`/`bits`/`descriptors`
and `rules.alerts`, and the native outputs are unchanged by the seam (byte for byte on a before/after run of the small
archive; `run.json` and the cards gain `chemistry`, `refused` and `dropped_unstandardisable`). In the browser Pyodide
has scikit-learn 1.8.0 but no RDKit, so the page computes the chemistry itself in RDKit.js 2026.9.1 (the version
`registry/scientific_environments.json` pins) plus bioagent's ports of the standardisation, the count fingerprint, the
missing descriptors and the `FilterCatalog` alerts, held to native RDKit on a parity corpus; it writes the prepared
features and bits, and a `PrecomputedEngine` reads them back. The models are trained in the browser from the TDC
archive (fetched through the site's dataset relay or given as a project file; in CI it is given as a project file, and
a download through the relay in a browser has not been run) and kept in the origin's private storage, so nothing
trained is shipped; a model card names the chemistry it was built with.

The browser **refuses** (never approximates) a molecule it cannot standardise exactly as native RDKit would — one that
would need `MetalDisconnector`, `Reionizer` or the `Uncharger`'s canonical atom ranking, a `Normalizer` product RDKit
cannot sanitise (which native RDKit carries forward unsanitised), a dative or zero bond, or more than 400 atoms, or on
which RDKit.js throws an exception (the chemistry then goes on with a fresh module) — naming the step; in a build such a training molecule is dropped and counted on the card
(`dropped_unstandardisable`), and in a prediction run the molecule is reported as refused and read by the runner. An
SDF or `.mol` file (RDKit's `SDMolSupplier` is not ported) and more than 20,000 molecules per run also need the runner.
The applicability-domain search (the Tanimoto above) runs on this device's WebAssembly SIMD kernel, or on its GPU only
where this device's own check measured a physical adapter exact and faster, and Python checks the result before it uses it.
Compared with the native command (RDKit 2026.9.1, scikit-learn 1.8.0, one thread) in headless Chromium, on a
known-answer archive and a synthetic one at the TDC sizes, the domain files and `admet.tsv` were the native run's
bytes, the model cards the native ones but for the model file's digest, the seconds and the chemistry's name, and every
prediction was exact at full precision apart from values that pass through `expit` or `10 **`,
which may differ by a few units in the last place of float64 (the C library's `exp`, or numpy's own AVX-512 power).
That was tested locally; on the known-answer archive it also passed in Studio's CI (`studio` #95, 2026-10-20).

On the real TDC archive the same comparison passed in `live-data` #24 (run 39386727431, commit 0882e95, 2026-10-20),
in headless Chromium on GitHub's runner. The archive (1,472,501 bytes, held to its pinned SHA-256; 22 endpoints, 81,809
molecules over their splits) was downloaded natively from Harvard Dataverse and given to the page as a project file.
The browser's chemistry refused 202 of the 81,809 molecules (0.25%, 150 distinct SMILES; the 202 by step: `Reionizer`
96, `MetalDisconnector` 83, the `Uncharger`'s canonical atom ranking 18, an unsanitisable `Normalizer` product 5), and
the native models were trained without them too; the 22 model cards were the native ones but for the two file digests,
the seconds and the chemistry's name, and the 22 domain files were the native bytes. Of the 16,353 test-set
predictions, every raw model output and every domain search was identical, and 36 final ones (through `10 **` or
`expit`) differed by 1 unit in the last place of float64. `pipeline.admet` with these models on a 39-molecule set
refused the 9 molecules expected, each for its step, did not read 1 (as natively), and for the 29 it kept wrote the
native run's `admet.tsv` bytes. The domain search has not been run on a physical GPU, here or in CI (that run's
forced-GPU rerun used SwiftShader, a software adapter, which is not evidence of a GPU), so P3's acceptance criterion
([BROWSER_COMPUTE.md §6.1](../studio/docs/BROWSER_COMPUTE.md#61-the-science-kernels-and-the-device-check-p3)),
verified execution on a physical GPU with measured performance, is not met.

## Network pharmacology: a formula to bounded mechanism hypotheses

`bioagent research "<question>" --formula <formula> --snapshots DIR --ledger FILE
--state-dir DIR --out DIR` runs the closed research loop (`bioagent.research.loop`): a
frozen protocol, ledger-verified snapshots, the analysis as an isolated PSH tool call,
pre-registered rebuttals and an attested release. In Studio it is the job `research.run`
(core tool `network_pharmacology_run`), on the runner or in the browser's own job workers
with no runner: there the analysis runs in-process through the same PSH broker (a worker
cannot start processes) and the artifact records `isolation: "in_process"`
([studio/docs/BROWSER_JOBS.md](../studio/docs/BROWSER_JOBS.md)). `scripts/run_network_pharmacology.py` runs the same
analysis under the run contract without the rebuttals. Neither is the seed skill
`analyze-tcm-network-pharmacology`, which is a *limited seed-corpus network hypothesis
analysis*: it reads only the curated seed corpus, with no database coverage and no measured
activity, and draws no permutation null.

`--formula` names the formula separately from the question (a name, a name with its source
book, or an id). It decides what is studied; a question whose text names another formula is
refused.

### Steps

The analysis runs as one call. `bioagent.research.workflow` reads it back as sixteen
recorded stages, each with a validation status (PASS, FAIL, BLOCKED, UNVERIFIED, SKIPPED,
NOT_APPLICABLE), its reason, its counts and the command that reproduces the run:

| # | Stage | What it records | When it is not PASS |
|---|---|---|---|
| 1 | Formula identity | the composition the herb layer records, by fingerprint | FAIL if the herb layer records another |
| 2 | Herb composition | components and their verified source species | UNVERIFIED when a component has none |
| 3 | Herb to compound | constituents per herb and source, composition level | UNVERIFIED when a herb with a species has none |
| 4 | Structure identity | deduplication by identical source-supplied standard InChIKey; keys sharing a connectivity block are counted, not merged | always UNVERIFIED: the snapshots carry no structure, so nothing is re-standardised |
| 5 | Compound to target | measured potencies at or below the cut-off, each re-read from its snapshot | UNVERIFIED with no measured target; FAIL if a target rests on no measured edge |
| 6 | Measured vs predicted | predicted edges counted and left out; hypotheses cite measured edges | FAIL if a target or a citation rests on a prediction; UNVERIFIED with no target to check |
| 7 | Disease genes | the Open Targets gene set at the stated datatype and score | NOT_APPLICABLE without a disease; SKIPPED, with the refusal, when the run refused the Open Targets snapshot its contract requested |
| 8 | Intersections | measured targets x disease genes x background (descriptive) | as stage 7 |
| 9 | PPI network | the STRING edges at or above the cut-off, re-read from the snapshot | UNVERIFIED when STRING was dropped, or read with no measured target in the background (no network could be built); SKIPPED when it was not asked for or was refused |
| 10 | Pathway annotation | Reactome | GO SKIPPED (no GO snapshot is registered); KEGG BLOCKED (licence required) |
| 11 | Enrichment | the background, stated in the protocol, and the pathways tested | UNVERIFIED when nothing could be tested |
| 12 | Multiple testing | Benjamini-Hochberg over every pathway tested, recomputed | UNVERIFIED when nothing could be tested |
| 13 | Permutation and sensitivity | the degree-matched null for each BH-significant pathway; the rebuttals | UNVERIFIED when nothing could be tested or a rebuttal could not run; NOT_APPLICABLE when no pathway passed BH (nothing to permute) |
| 14 | Topology | degree, betweenness, components, descriptive only | as stage 9 |
| 15 | Bounded interpretation | claims are `mechanism_hypothesis`, worded as one | UNVERIFIED when nothing could be tested; FAIL on a stronger kind or wording |
| 16 | Reproducible export | every export read back with its recorded SHA-256 and size | FAIL on a missing or changed file |

A source the protocol dropped makes every stage that reads it UNVERIFIED, with the
deviation attached. A source the run's contract requested and the run refused (an
`--allow-source` list, a commercial run's terms) is named by every stage that reads it and
in one line of `limitations.md`. A stage whose data were not read is never PASS, an empty
result never passes, and a missing dataset is never negative evidence.

In `stages.json` (the run-record form Studio reads) a stage that had nothing to test is
`not_applicable`, so a run in which nothing could be tested is not `complete`. A run that
tested pathways and found none significant is complete: its stage 13 had nothing to permute
and says so.

### What it writes

| File | Holds |
|---|---|
| `compounds.tsv` | compound, name, InChIKey, connectivity block, `id_kind` (inchikey or source_local), level, herbs, sources |
| `targets.tsv` | target, name, `evidence_class` (measured), measurements, distinct publications, compounds, herbs, in background, disease score, degree, betweenness |
| `disease_targets.tsv` | the disease gene set: target, name, score, datatypes, is a measured target, in background |
| `enrichment.tsv` | library, pathway, size, members in the background, overlap, p, BH q, permutation p, fold, targets |
| `network.tsv` | the PPI edge list: a, b, score, the STRING record |
| `provenance.json` | snapshot ids, parameters, seed, digests, coverage, and every edge the tables rest on as (snapshot id, record id) |
| `limitations.md` | one limitation per line: the analysis's, the rebuttals', the deviations and one line per stage that is not PASS |
| `report.html` | stages, coverage, enrichment, hypotheses, limitations, snapshots and file digests |
| `run_manifest.json` | `tcmscience.run-manifest/1`: the sixteen stages, every output's SHA-256 and size, snapshot ids, parameters, seed, code digest, protocol digest, the reproduce command, and an `execution` block (audit head, `artifact.json` digest) |

A table with nothing in it is written with its header. The research loop also writes
`protocol.json`, `result.json`, `enrichment.jsonl`, `rebuttal.json`, `artifact.json` and
`stages.json`.

### How it is checked

- Every export but the manifest is an output of the research artifact, so
  `validate_artifact` checks its digest. The manifest is written last, after
  `artifact.json`, and records the digest of every output and of the artifact.
- No export carries a timestamp. Two runs in fresh state directories give byte-identical
  exports. The manifest differs only in its `execution` block, which names the run's audit
  chain.
- `python -m bioagent.analysis.np_exports verify RUN --snapshots DIR --ledger FILE`
  re-checks the manifest's digests. It recomputes each p- and q-value of `enrichment.tsv`
  from the table's own columns, and re-runs the analysis on the snapshots the manifest
  names; the tables must come out byte for byte. It exits 0 when every check passed, 1 when
  one contradicts the run, and 3 when none does but one could not be made (no snapshots
  given, or an empty enrichment table with nothing to recompute).
- A stage whose own check FAILs is not a result: `bioagent research` exits 3, and
  `scripts/run_network_pharmacology.py` too, whatever the release check said.
- Studio's `research.run` requires the nine exports on exit 0 and exit 1 alike; exit 3
  fails the job. Its validator re-reads every digest in `run_manifest.json` (never through
  a symlink, and only regular files), so a table edited after the run fails the job's
  check, and so does a stage the manifest lists as FAIL.
- The manifest's reproduce command says whether it reproduces the run exactly
  (`reproduce_exact`): false when the run used a choice the command cannot carry (a
  parameter the command has no option for, another skill directory, a formula it cannot
  look up by id). Whether a lock was given is in the `execution` block, so the command,
  which passes one, writes the same exports.
- `tests/test_np_workflow.py` runs on the planted worlds (`bioagent.research.planted`) and
  the fixture of `tests/test_network_pharmacology.py`. They are fixtures, built under
  `+planted` and `+fx` versions, not data.
- Real data: the `live-data` CI workflow downloads the real NPASS, CMAUP, LOTUS, STRING and
  Reactome files on GitHub's runners, runs 葛根芩连汤 through the research job, uploads the
  nine exports and runs `verify` on them. That step passed in `live-data` #16 and #17
  (2026-10-19). It checks exit codes, that the nine exports exist and are not empty, and
  that `verify` exits 0; it asserts no count or pathway ([validation.md](validation.md#not-verified-here-and-why)).
  Nothing in this repository's offline tests is a real-data result.

### What a result is

A released hypothesis is a `mechanism_hypothesis`: a computational hypothesis awaiting
experimental test, citing the snapshot edges of one path. The indication overlap and the
topology are descriptive, not claims. An empty result says which kind it is:

- "No measured target was found for the N constituents in these snapshots, so no enrichment
  could be tested; an empty result, not evidence of inactivity."
- "Pathways were tested and none passed both BH and the permutation null": an absence of
  evidence for enrichment, not evidence that any pathway is uninvolved.

### Limits

- Composition is species-level (C1/C2), so every claim stops at a hypothesis.
- Targets are what happened to be measured: absence of a measurement is not absence of
  activity, and screening panels bias the assayed background.
- Identity is the source-supplied InChIKey only. Stereo, isotope and charge variants that
  share a connectivity block are reported, not merged.
- Pathways are Reactome's lowest-level annotation. GO is not tested: no GO snapshot is
  registered, and adding its source card is a person's decision. KEGG is not tested: its
  membership requires a licence, and none is downloaded or bundled.
- Without a STRING snapshot the network has no edges because none were read, not because
  none exist.

## Run records: stages, completeness and limitations

Every pipeline records its stages as it runs (`bioagent.runrecord`). A stage is
`{name, required, status, check, reason, engine, version, device, seconds, outputs}`:

- `status` is `succeeded`, `failed`, `skipped` (chosen not to run), `unavailable` (could
  not run here: a model, package or tool is missing) or `not_applicable` (nothing to do);
- `check` is a validation status (PASS, FAIL, …) when the stage tests something;
- `device` is where the stage computed, as observed (below);
- `outputs` are the files under the output directory that hold the stage's result.

A run is **complete** only when every required stage succeeded and no check is FAIL.

- `stages.json` in the output directory is rewritten atomically when a stage starts and
  when it ends. A run that stops part way leaves its failed stage there, with the error.
  The Studio runner reads it for a job's progress (the stage running, the share of
  required stages done) and shows the stages on the job.
- At the end, `run.json` gains `stages`, `complete` and `limitations`. Its file name and
  existing keys are unchanged.
- `limitations.md` has one limitation per line: what every result of the pipeline is
  not, and what this run did not do (a stage skipped, not applicable or unavailable,
  with its reason). Its lines equal `run.json`'s `limitations`. It holds no timestamp,
  so a rerun gives the same bytes; timings are only in `stages.json` and `run.json`.
- `verify_run` (`--verify`) re-hashes the inputs and outputs as before. For a `run.json`
  with stages it also checks completeness and content:
  - completeness: every required stage succeeded, no check failed, the run is recorded
    as complete, and each stage's outputs are recorded outputs that exist;
  - content: `deseq2_results.tsv`, `counts.tsv`, `tpm.tsv` and `vst.tsv` have their
    columns and one row per gene the test saw; `cells.tsv` has one row per analysed
    cell and `clusters.tsv` one per cluster; `scores.tsv` has one row per docked ligand
    (at least one), each with its poses; `admet.tsv` has one row per molecule read and
    the columns of the models recorded; `summary.json` has one entry and one model per
    sequence.

  A `run.json` written before stages were recorded gets the digest checks only.
- **An incomplete run makes the command exit 1**, after writing its files so they can
  be inspected. That happens when:
  - no ligand docked, redocking missed 2 Å, or a ligand's best pose failed its checks;
  - an ADMET run has no models, or none for an endpoint asked for by name;
  - any check failed.

  As a Studio job it then fails with the stages at fault as its problems. It never
  becomes a clean `succeeded`. The candidate skills that wrap these pipelines draft no
  claim from such a run, and their limitations name the stages at fault.

| Pipeline | Stages, in order |
|---|---|
| RNA-seq | `validate_reference`, `ingest`, `validate_fastq`, `qc`, `trim` (skipped with `--trimmer none`), `quantify`, `summarize_genes`, `differential_expression`, `explore`, `report`, `verify` |
| RNA-seq from a count matrix | `validate_counts`, `differential_expression`, `explore`, `report`, `verify` |
| Single cell | `validate_input`, `qc`, `doublets` (skipped when off), `normalize`, `hvg`, `pca`, `integrate` (not applicable with one batch or `none`), `neighbors` (the kNN graph, UMAP connectivities and layout), `cluster` (Leiden, and PAGA's connectivity between the clusters), `markers`, `annotate`, `trajectory` (pseudotime; not applicable without a root), `pseudobulk_de` (not applicable without two conditions), `report`, `verify` |
| Structure | `input_check`, `predict`, `sequence_identity`, `confidence`, `geometry`, `compare` (not applicable without a reference), `report`, `verify` |
| Docking | `receptor_prep`, `ligand_prep`, `site`, `redock_validation` (skipped without a co-crystal ligand; FAIL beyond 2 Å), `dock`, `pose_checks`, `contacts`, `report`, `verify` |
| ADMET | `standardize`, `descriptors`, `rules`, `alerts`, `models` (unavailable without them), `predictions`, `report`, `verify` |

**RNA-seq input checks, before QC and trimming spend time.** `validate_reference`
runs first, before the lanes of a sample are merged. It requires:
- unique transcript identifiers, no empty sequence, nucleotide codes only;
- at least one transcript of the FASTA in the annotation;
- for `--engine hisat2`, a GTF annotation whose every sequence name is a contig of the
  genome (a GTF on `chr1` and a genome on `1` would count nothing).

`validate_fastq` then reads every record, not QC's sample of the first 200,000. It
requires:
- an `@` header with a name and a `+` separator;
- as many quality values as bases;
- bases A, C, G, T or N, and quality characters within Phred+offset;
- the mates of a pair in step and of the same name;
- a gzip stream that is complete.

A failure stops the run and names the line. Both records go under `run.json`'s
`validation`; `inputs` keeps its keys.

**Devices, as observed.**
- The built-in, Scanpy, Vina and command-line tool stages compute on the CPU and say
  `cpu`.
- scVI trains with accelerator `cpu` by design, so that its seed reproduces the model,
  whatever device the job was given.
- Local ESMFold records the device its parameters were on: `cuda:0`, or `cpu`. It moves
  to a GPU only through CUDA, so a job given Apple's MPS computes on the CPU and says so.
- The ESM Atlas service is `remote:api.esmatlas.com`.
- ColabFold records what its own log says (`gpu` or `cpu`), and otherwise null with a
  note. `gpu` is ColabFold's alone: its log reports a GPU without naming one, and a
  note says the device is as reported, not observed.

**Structure checkpoints.**
- Local ESMFold records the Hub model id, its revision, a SHA-256 of the weights
  themselves (computed once per revision in a process) and, on CUDA, the peak GPU
  memory. The peak memory varies between runs, so it is in `run.json`, not in
  `summary.json`.
- ColabFold records its version, its model type and the digest of the `config.json` it
  wrote. The AlphaFold2 parameters are not digested, so its checkpoint is recorded as
  `verifiable: false` with `scope: config_only`. Only a checkpoint whose weights were
  digested is verifiable.
- The ESM Atlas service's weights cannot be observed from here. They are recorded as
  `verifiable: false`, and no digest is claimed for them.

Whatever the method, a model of a sequence other than the one asked for is refused
(`predict` and the `sequence_identity` stage). A retrieved structure is only compared
with; it never stands in for a prediction.

**Docking pose checks.** Each pose is checked for:
- finite coordinates;
- every ligand heavy atom inside the box (within 0.5 Å);
- no ligand–receptor heavy-atom pair closer than 2.2 Å, metal ions left out.

The results go into `run.json`'s `results` and `scores.tsv` (`pose_check`,
`min_receptor_distance`). Benzamidine's crystal pose in 3PTB sits 2.8 Å from its nearest
receptor atom and passes. These are sanity checks, not PoseBusters, and passing them
does not validate binding. Vina's ranking is kept as it is.

`tests/test_docking.py` exercises the stages and checks with RDKit, meeko and a
stand-in for Vina, and says so; real Vina is run by CI's molecular-modelling job, which
passed with these checks in `ci` #317 (2026-10-19). The
`dock-ligands` skill drafts a hypothesis only from a complete run, and only for a ligand
whose best pose passed these checks.

**ADMET model identity.** `run.json`'s `models` keeps its six fields per endpoint and
adds:
- whether the archive is the pinned one, and the training set;
- the model class and its iterations, and the features;
- the applicability domain's digest and threshold;
- the scikit-learn and RDKit versions, and the seed.

With no model, the `models` and `predictions` stages are unavailable and the run is
incomplete, though descriptors, rules and alerts are still written.

### Reference datasets

`bioagent/data/pipeline_references.json` (version 2026.10.2) names one reference
dataset per pipeline (two for RNA-seq, one per mode): its content digests, the parameters it is run with, and the
values expected from it. It also holds output digests, which are compared only in the
environment they were recorded in (Python, numpy, scipy, CPU architecture and the CPU features
numpy dispatches to): a table of floats can differ in its last digits on another machine with the
same library versions, so elsewhere the expected values are the check. `tests/test_pipeline_references.py` re-runs the
RNA-seq (both modes) and single-cell references with the built-in backends and the structure reference
offline, compares the docking reference's fixture digest (the redocking itself is in
`tests/test_docking.py`, which needs Vina), and runs the ADMET reference only where its
archive is given.

| Pipeline | Reference | Kind | Where it is checked |
|---|---|---|---|
| RNA-seq | `omics_world` v1 (`tests/omics_world.py`, seed 1, 5% adapters) | simulated, known answer | here: the seven planted genes, at most one false discovery, 30 genes tested, outputs byte-identical |
| RNA-seq, count matrix | `omics_counts_world` v1 (`tests/omics_world.py:make_counts_world`, seed 1: 2,000 genes × 6 samples, 40 genes four-fold up and 40 down) | simulated, known answer | here: at least 38 planted genes found in each direction (40 and 40 when recorded), none in the wrong direction, at most 15 false discoveries (10 among 90 calls when recorded), 1,999 genes tested, outputs byte-identical; in a browser, the same values (above) |
| Single cell | `sc_world` v1 (`tests/sc_world.py`, seed 0) | simulated, known answer | here: ARI above 0.9 (1.0), the four cell types named, at least 12 planted genes in B-cell pseudobulk |
| Structure | PDB 1UBQ and the stored ESM Atlas model | public | here, offline: TM-score 0.958, RMSD 0.83 Å; a new prediction requires the network or torch and transformers |
| Docking | the 3PTB pocket | public (CC0) | requires vina, meeko and RDKit: CI's molecular-modelling job (redocking under 2 Å; 0.38 Å documented), passed there in `ci` #317 (2026-10-19) |
| ADMET | the TDC ADMET group | public | requires RDKit, scikit-learn and the archive (`BIOAGENT_TDC_ARCHIVE`): held-out scores within 0.05 of the table above for scores on [0, 1], and within 5% for errors in the endpoint's unit |

The three simulated references are not public data. A run on one of them is recognised by
its content digests, and its `limitations.md` says "simulated, known answer … not public
data".

## 中文摘要

一键分析流程从用户自己的数据或分子出发完成整套分析。每一步都记录参数、工具版本和文件摘要，并说明结果是什么、不是什么。包装这些流程的受治理 Skill 都是**候选**：在有人审核并晋级之前，只能作为开发运行，会被记录但不会被发布。

**在哪里验证过（见上文「What is verified where」表）：** 所有流程都已实现，在用户自己的 Runner 上运行（Studio 中为 Runner 任务）；网络药理学也能作为浏览器任务运行，无需 Runner（已在已知答案、夹具文件和葛根芩连汤真实文件上检验；真实数据的导出在 `live-data` #18、#19 与 #21 中与原生运行逐字节一致，见 [BROWSER_JOBS.md](../studio/docs/BROWSER_JOBS.md)）；RNA-seq 与单细胞也部分如此：从计数矩阵出发的 RNA-seq 与从计数表出发的单细胞分析，使用内置引擎，至多 6,000 个细胞（单个表 66,000,000 个细胞 × 基因）或 10,000,000 个基因 × 样本（见下文「浏览器中」），已在无头 Chromium 中（本地及 Studio 的 CI，`studio` #91，2026-10-19）与原生命令在其已知答案参考上逐个输出比对；ADMET 也部分如此：模型构建（`admet.models`）与从 CSV/TSV/`.smi` 文件或单个 SMILES 出发的预测（`pipeline.admet`），化学用 RDKit.js 2026.9.1、模型在浏览器中由 TDC 归档训练（见下文「浏览器中」），已在无头 Chromium 中与原生命令比对：已知答案归档（本地及 Studio 的 CI，`studio` #95，2026-10-20）、TDC 规模的合成归档（仅本地）与真实 TDC 归档（`live-data` #24：运行 39386727431，提交 0882e95，2026-10-20），适用域的 GPU 检索尚未在实体 GPU 上运行；结构预测与对接不在浏览器中运行，所有流程都没有来自已部署服务的记录。撰写本文的环境中实际运行过：RNA-seq（从 FASTQ 与从计数矩阵）与单细胞的内置路径（也在无头 Chromium 的 Pyodide 中运行过），以及另一环境中的 Scanpy、harmonypy、PyDESeq2、RDKit、meeko 与 scikit-learn；没有运行过：真实的 AutoDock Vina、scVI、本地 ESMFold 权重、ColabFold 和 RNA-seq 的外部命令行工具，它们由 CI 的引擎作业运行。阶段与参考数据集的测试（`tests/test_runrecord.py`、`tests/test_pipeline_references.py`）已加入引擎作业；它们不需要任何引擎（RNA-seq 与单细胞参考固定使用内置后端，对接参考只比对夹具摘要），检查的是运行记录与参考结果，而不是引擎，引擎由各作业自己的测试检查。从 `ci` #282（2026-10-11 20:06 UTC）到 2026-10-19，CI 运行都没有分配到运行器；在 `ci` #317（2026-10-19）中，各引擎作业全部通过，包括这两个文件，都在 CPU 运行器上。计数矩阵模式的测试（`tests/test_omics_rnaseq_counts.py` 及其在 `tests/test_pipeline_references.py` 中的参考）随提交 65fa6aa 加入该作业；该作业在 `ci` #322（运行 39286930329，2026-10-19）中通过：231 个通过，2 个跳过。这些作业在小型参考数据或夹具上运行各引擎，不是真实数据。RNA-seq 与单细胞的参考数据集是**模拟数据（已知答案）**，不是公开数据。远程计算已设计、未部署。无论用哪个引擎：内置 DESeq2 实现、PyDESeq2 与 R 的 DESeq2 方法相同但数值并不相同；对接打分不是实测亲和力；ADMET 在适用域外的预测会被标注且不可靠；结构预测绝不以检索到的结构替代。

**RNA-seq**（`bioagent rnaseq`）：从 nf-core 格式的样本表出发（或用 `--counts` 从计数矩阵出发，见下文），依次完成以下步骤：
- 质控：FastQC 的各模块与阈值；
- 修剪：fastp，或内置的 cutadapt 式修剪；
- 定量：salmon、kallisto 或内置的 k-mer 伪比对与 EM；也可用 HISAT2 + featureCounts；
- 基因汇总：tximport 的规则；
- 差异表达：DESeq2 方法，内置实现或 PyDESeq2（`--de-backend`，见 omics-backends.md）；
- 探索分析：VST、PCA 和样本间距离；
- 报告：带 SVG 图的 Markdown/HTML 报告，以及记录全部摘要的 `run.json`，可用 `--verify` 复核。

内置定量器对双端测序增加了片段长度一致性检查，纠正了外显子跳跃异构体被低估的问题。

**从计数矩阵出发**（`bioagent rnaseq --counts counts.tsv --samples samples.tsv --out results/`，模块 `bioagent.omics.rnaseq_counts`），用于在别处得到的计数（featureCounts、经 tximport 的 salmon、已发表的矩阵）：
- `--counts` 是基因 × 样本的 TSV 或 CSV 表（可 gzip）：`gene_id`、可选 `gene_name`，然后每个样本一列，每个值都必须是非负整数。以下情况被拒绝，并指明行与样本：非数字、负数、非有限值、非整数（「请先把估计计数取整」）、2^53 及以上、重复的基因 ID 或样本名、少于两个样本、某样本计数全为零、gzip 数据流损坏或被截断的表（「… cannot be read to its end (…)」，不会崩溃）、名称中含制表符或换行。
- 此时 `--samples` 是样本表：`sample` 列加设计变量，每个计数列一行。FASTQ 样本表（含 `fastq_1`/`fastq_2`）被拒绝：读段要走完整流程。
- FASTQ 专用选项（`--transcripts`、`--genome`、`--annotation`、`--engine`、`--trimmer`、`--fragment-mean`、`--fragment-sd`、`--strand`）被拒绝（退出码 2），绝不忽略。`--design`、`--contrast`、`--covariate`、`--de-backend` 与 `--alpha` 含义与 FASTQ 模式相同。
- 从计数开始，步骤与完整流程相同、代码相同：差异表达、VST、PCA、样本距离、相同的表格与图、`report.md`/`report.html`、`limitations.md` 与 `run.json`。没有看到任何读段，因此没有质控、修剪和定量，`limitations.md` 注明计数按原样分析。把 FASTQ 参考的计数取整成表后，这一模式写出的 `deseq2_results.tsv`、`de_diagnostics.tsv` 与 `vst.tsv` 与完整流程逐字节相同（有测试检查）。
- `run.json` 把流程记为 `bioagent.omics.rnaseq_counts`、输入类型 `counts`，记录每个输入（路径、SHA-256、大小）以及规范化计数表和样本表的内容摘要；`bioagent rnaseq --verify` 两种模式都能复核。阶段为 `validate_counts`、`differential_expression`、`explore`、`report`、`verify`。它无法检查计数是如何得到的（建库、比对或定量工具、注释、过滤都不可知）。

验证有三层：
- 按方法定义逐项核对；
- 用已知答案的模拟实验检验：内置路径及 salmon、kallisto、fastp、HISAT2 均找回全部 7 个预设差异基因；
- 与独立实现 PyDESeq2 对比：倍数变化相关 0.99999，p 值相关 0.9995，显著基因几乎完全重合，实测 FDR 为 0.048。
- 与 R 的 DESeq2 1.42.0 对照：每组 4 个样本时内置实现找到 R 的全部基因并略多几个；每组 2 个样本时与 R 的结果不同，原因已逐步查明（见 omics-backends.md）。

上述工具测试在本机用 Ubuntu 24.04 软件包通过：fastp 0.23.4、salmon 1.10.2、kallisto 0.48.0、HISAT2 2.2.1、featureCounts（subread）2.0.6、samtools 1.19.2。

结果只是本次实验内的统计关联，不证明机制、因果或任何临床效果。人体样本只能支持 `association`（相关性），细胞或动物实验只能支持 `mechanism_hypothesis`（机制假说）。

**单细胞**（`bioagent scrna`）：输入为 10x 目录、HDF5、h5ad 或计数表，依次完成：
- 质控：按样本做 MAD 离群剔除；
- 双细胞检测：Scrublet；
- 预处理：Seurat 方法选取高变基因，并做 PCA；
- 批次整合：Harmony；
- 聚类与可视化：Leiden 聚类，UMAP 布局；
- 标记基因：Wilcoxon 检验；
- 细胞类型注释：基于标记基因面板，无明确领先者时标为 unassigned；
- 轨迹分析：PAGA，以及从指定起点出发的扩散伪时间；
- 条件间比较：按细胞类型做伪批量 DESeq2。

计数表逐行读取，只保留每行的非零计数，所需内存取决于计数而非文本（矩阵与整体解析相同；在 Pyodide 中，单个 1,356 个细胞 × 33,000 个基因的表在运行峰值时用 1.55 GB 而非 2.0 GB；读入之后占主导的是双细胞评分的稠密矩阵）。空行跳过；值的个数不对的行被拒绝（「rows have N values for M cells」）；gzip 数据流损坏或被截断的计数表或 Cell Ranger 文件被拒绝并说明原因（「… cannot be read to its end (…)」）；样本表中 `path` 为空的行被拒绝（「line N of <样本表> names no path」）。

分析后端（内置或 Scanpy）、批次整合方法（none、harmony 或 scvi）和伪批量检验实现（内置或 PyDESeq2）三者可独立选择，见 omics-backends.md。

验证有两类：
- 与参考实现对比：高变基因、Wilcoxon 分数和 Leiden 模块度与 Scanpy/leidenalg 一致，UMAP 连接度与 umap-learn 的差异在 5×10⁻⁶ 以内；
- 用已知答案的模拟数据检验：低质量细胞全部剔除，聚类 ARI 大于 0.9，注释正确，伪时间沿预设路径（Spearman 大于 0.85），伪批量只在预设的细胞类型中找到差异。

聚类、细胞类型标签和伪时间都只是输出，不构成结论；只有条件间的伪批量比较才会形成结论。`--n-top-genes`（默认 2,000；Runner 的 `n_top_genes`，50 到 10,000）设定保留的高变基因数，参考数据用 600。

**浏览器中：计数矩阵与计数表**（[BROWSER_JOBS.md §3.1](../studio/docs/BROWSER_JOBS.md#31-rna-seq-and-single-cell-from-count-tables)）。在 Studio 中，这两个流程也能作为浏览器任务运行，无需 Runner，在装有 numpy 与 scipy 的 Pyodide 任务工作线程中：
- `pipeline.rnaseq` 的计数矩阵模式（`counts` 与 `samples`），使用内置 DESeq2 实现；
- `pipeline.scrna` 处理计数表（CSV、TSV 或 TXT，可 gzip，或由它们组成的 `.csv`/`.tsv` 样本表），使用内置分析与内置 Harmony 或不整合。

输入是用户项目中的文件，复制到任务的私有存储并按记录的 SHA-256 核对。以下情况在浏览器中被拒绝并说明原因，交给 Runner：从 FASTQ 出发的运行、PyDESeq2、Scanpy、scVI、10x HDF5 与 `.h5ad` 来源，以及超过 6,000 个细胞、单个表超过 66,000,000 个细胞 × 基因（双细胞评分以稠密方式保存一个样本的细胞 × 基因）或计数矩阵超过 10,000,000 个基因 × 样本（浏览器工作线程的 wasm32 内存约 4 GB；在 Node 下的 Pyodide 314.0.7 中测得，每项一次，不是在浏览器标签页中：四个 1,356 个细胞 × 33,000 个基因的表用 1.9 GB，单个 2,000 × 33,000 的表 2.2 GB，10 M 个计数 1.8–2.0 GB；`MemoryError` 也是同样的拒绝）。`counts` 与作为 `samples` 的 FASTQ 样本表同时给出时，与 Runner 一样作为参数错误拒绝。在 `omics_counts_world` v1 与写成计数表的 `sc_world` v1 上，与同一批文件上单个 BLAS 线程的原生命令逐个输出比对（`studio/web/test/runtime/science.mjs --mode omics`）：
- 逐字节一致：RNA-seq 的 `counts.tsv`、`vst.tsv`、`report.md`、`limitations.md`；单细胞的 `clusters.tsv`、`markers.tsv`、`paga.tsv`、`report.md`、`limitations.md`，以及除 UMAP 坐标外的 `cells.tsv`；
- 在给定容差内（|a − b| ≤ 1e-6 + 1e-4·max + 第六位有效数字的一个单位；最大差异用到容差的 0.133）：`deseq2_results.tsv`、`de_diagnostics.tsv` 与四个伪批量表，都是 DESeq2 拟合的输出，其离散度搜索会把不同 numpy 构建之间末位的差异放大到打印出的数字；
- 不比较并说明原因：UMAP 坐标与五张图；改为检查布局结构（每个细胞 15 个最近 UMAP 邻居中同簇的比例：浏览器 0.9985，原生 0.9983）；
- 完全相同：显著的 90 个基因、各伪批量检验的显著基因、全部 1,077 个细胞的聚类与细胞类型；
- 浏览器写出的文件上的已知答案：80 个预设基因全部按方向找到，90 个显著中假发现 10 个；ARI 1.0、四种细胞类型、B 细胞 20 个预设基因中找到 16 个、混合度 1.03。

已在本地无头 Chromium 141 中用 Pyodide 314.0.7（numpy 2.4.6、scipy 1.18.0）测试，对照原生 Python 3.13.16、numpy 2.5.4、scipy 1.18.1；Studio 的 CI 中同样通过（`studio` #91，2026-10-19，使用 jsDelivr 上 Pyodide 的 numpy 与 scipy，原生为 CI 的 Python 3.12.15），结果相同，只是原生的 UMAP 同簇比例为 0.9981。这一容差是在这两个模拟参考上拟合的，对其他数据未经确立；其他设置（伪时间起点、`integration: none`、自备的标记基因表）没有在浏览器中比对过；用户自己的运行不与任何结果比较，其局限说明写明接近阈值的值（接近 alpha 的校正 p 值）可能不同。

**蛋白结构预测**（`bioagent fold`）：输入 FASTA 或单条序列，可选三种预测器：
- `esmatlas`（默认）：通过 Meta 的 ESM Atlas 服务运行 ESMFold，最长 400 个残基。序列会发往第三方，须显式加 `--allow-remote`；
- `esmfold`：用 `transformers` 在本地运行 ESMFold，序列不出本机，适合保密序列；
- `colabfold`：本机装有 `colabfold_batch` 时运行 AlphaFold2。

每个模型都报告：
- 置信度：pLDDT 均值、各区间占比、低置信片段，以及方法给出时的 PAE；
- DSSP 二级结构；
- 几何检查：回转半径、CA–CA 距离异常、原子碰撞、二面角；
- 与参考结构的一致性（可选）：参考可以是 PDB 实验结构、AlphaFold DB 模型或本地文件，比较 TM-score、RMSD、GDT-TS 和逐残基偏差。

验证：
- TM-score 与 TM-align 一致；
- DSSP 复现泛素 1UBQ 已知的二级结构；
- 泛素的 ESMFold 预测与晶体结构相比，TM-score 0.958，RMSD 0.83 Å（离线测试与实时服务均通过）。

预测结构只是计算结果，不是实验结构。pLDDT 低于 70 的区域不宜解读；模型为单链，不含配体、辅因子和相互作用伙伴。对应的候选 Skill `predict-protein-structure` 只记录模型和检查结果，不提出任何结论。

**分子对接**（`bioagent dock`）：

输入：
- 受体：PDB 文件、RCSB 条目或 AlphaFold DB 模型；
- 配体：CSV、SDF、`.smi` 或单个 SMILES。

流程：
- 受体处理：用 meeko 模板加极性氢、Gasteiger 电荷和原子类型；
- 配体处理：RDKit 生成构象并做 MMFF94 优化；
- 对接盒：围绕共晶配体、指定残基，或按给定中心与尺寸设定；
- 重对接验证：若位点来自共晶配体，先把它重新对接回去，最优构象与晶体构象的 RMSD 不超过 2 Å 才算验证通过；
- 对接：AutoDock Vina 1.2，固定随机种子；
- 输出：打分、配体效率、氢键/盐桥/疏水接触、全部构象（SDF）和带摘要的 `run.json`。

验证：
- 胰蛋白酶–苯甲脒（3PTB）重对接 0.37 Å，链霉亲和素–生物素（1STP）0.61 Å；
- 苯甲脒的脒基与 S1 口袋底部的 Asp189 形成氢键，与晶体结构一致。

对接打分只是打分函数的估计，不是实测亲和力（与实测值的相关系数约 0.5，误差约 2 kcal/mol）。受体为刚性，质子化状态按输入处理。候选 Skill `dock-ligands` 只有在重对接验证通过时，才对每个配体给出 `mechanism_hypothesis`（机制假说）。

**ADMET 预测**（`bioagent admet`）：输入 CSV、SDF、`.smi` 或单个 SMILES，对每个分子给出：
- 标准化结构：去盐、取最大有机片段、可中和的电荷予以中和；
- 理化描述符与成药性规则：Lipinski、Veber、Egan、Ghose；
- 结构警示：PAINS、Brenk、NIH；
- 22 个 ADMET 终点（需先运行一次 `bioagent admet --build-models`）：吸收、分布、代谢、排泄、毒性各项，每项都给出模型卡记录的留出成绩，以及该分子是否落在模型适用域内（与训练集的最大 Tanimoto 相似度低于 0.3 即判为域外）。

模型训练数据为 Therapeutics Data Commons 的 ADMET 基准集，采用其官方骨架划分，因此留出成绩可与 TDC 排行榜对照。数据包约 1.5 MB，只下载一次并按固定 SHA-256 校验；模型在本机训练，不随代码分发。模型文件与适用域文件都带摘要，加载前校验。每个终点训练完即写入模型卡，构建中断后已完成的部分保留，再次运行只训练缺失或不再匹配的终点（归档、种子、迭代上限、scikit-learn 与 RDKit 版本任一不同即重训）；`--rebuild` 全部重训。

**适用域对中药成分尤其重要**：TDC 训练集以类药合成化合物为主，而本仓库关注的许多分子不是。以各模型训练集的最大 Tanimoto 相似度计：阿司匹林在 hERG 模型为 0.31、槲皮素 0.67，均在域内；小檗碱 0.24、原人参二醇型三萜 0.16，均在域外，其预测会标注「outside domain」，不应作为预测结果解读——模型没见过皂苷，就对皂苷无话可说。

所有数值都是模型预测或规则判定，不是实测值；域外预测不可靠；结构警示只是提示需要进一步查看，并非定论（许多天然产物，尤其是黄酮和儿茶酚类，都会命中 PAINS 规则）。因此候选 Skill `predict-admet` 不提出任何结论，只输出这些结果，用于排序筛选、指导实验。

适用域检索（上文的最大 Tanimoto）已在 `uint64` 视图上用 `np.bitwise_count` 重写，并分块使临时数组很小：在本机（numpy 2.4.6）用随机的 2048 位指纹测得：1,000 个查询对 10,504 个参考（cyp2d6_veith 训练集的规模）原生约 0.43 秒（旧代码按 64 分块约 15.0 秒，按其默认的 512 分块更达 35 秒、峰值 6.7 GB），在 Node 下的 Pyodide 314.0.7 中约 0.7 秒，而旧的默认分块在那里会抛出 `ValueError: array is too big`；输出不变（最近训练分子，平局按最小下标，与 numpy 的 `argmax` 一致）。

**浏览器中**（[BROWSER_JOBS.md §3.2](../studio/docs/BROWSER_JOBS.md#32-admet)）：在 Studio 中，ADMET 也作为浏览器任务运行，无需 Runner——`admet.models` 构建 22 个终点模型，`pipeline.admet` 用它们预测。它们就是 `bioagent.admet` 本身，经由引擎接缝（`bioagent.admet.engine`）：原生默认的 `RDKitEngine` 封装 `chem.standardize`/`featurize`/`bits`/`descriptors` 与 `rules.alerts`，接缝不改变原生输出（小归档前后对比逐字节一致；`run.json` 与模型卡新增 `chemistry`、`refused`、`dropped_unstandardisable`）。浏览器中 Pyodide 有 scikit-learn 1.8.0 但没有 RDKit，故页面自己用 RDKit.js 2026.9.1（`registry/scientific_environments.json` 锁定的版本）加 bioagent 对标准化、计数指纹、所缺描述符与 FilterCatalog 结构警示的移植（在一致性语料上与原生 RDKit 对齐）算出化学，写出预备特征与位，由 `PrecomputedEngine` 读回；模型在浏览器中由 TDC 归档（经本站中转或作为项目文件给出；CI 中作为项目文件给出，尚未在浏览器中经本站中转下载过）训练并保存在本源私有存储中，不随代码分发，模型卡注明其化学来源。浏览器会**拒绝**（绝不近似）它无法像原生 RDKit 那样精确标准化的分子——需要 MetalDisconnector、Reionizer 或 Uncharger 规范原子排序、某条 Normalizer 变换的产物无法被 RDKit 消毒（原生 RDKit 带着未消毒的分子继续）、含配位键或零键、或原子数超过 400 的分子，或 RDKit.js 在其上抛出异常的分子（化学随后换用新的模块继续）——并指出所在步骤；构建时这样的训练分子被剔除并计入模型卡（`dropped_unstandardisable`），预测时该分子如实报告为被拒绝、由 Runner 读取。SDF 或 `.mol` 文件（RDKit 的 SDMolSupplier 未移植）与每次超过 20,000 个分子也需要 Runner。适用域检索在本设备的 WebAssembly SIMD 内核上运行，仅当本设备自身的检查测得实体适配器既精确又更快时才用 GPU，且 Python 在使用前核验其结果。与原生命令（RDKit 2026.9.1、scikit-learn 1.8.0、单线程）在无头 Chromium 中比对，在已知答案归档与 TDC 规模的合成归档上，适用域文件与 `admet.tsv` 都是原生运行的字节，模型卡除模型文件摘要、秒数与化学名称外与原生一致，每个预测在全精度下一致，只有经过 `expit` 或 `10 **` 的值可能在 float64 末位相差几个单位（C 库的 `exp`，或 numpy 自带的 AVX-512 幂运算）。以上在本地测试；已知答案归档上的比对也已在 Studio 的 CI（`studio` #95，2026-10-20）中通过。

在真实 TDC 归档上，同样的比对已在 GitHub 运行器上的无头 Chromium 中通过：`live-data` #24（运行 39386727431，提交 0882e95，2026-10-20）。归档（1,472,501 字节，按锁定的 SHA-256 校验；22 个终点，各划分合计 81,809 个分子）由原生代码从 Harvard Dataverse 下载，作为项目文件交给页面。浏览器的化学步骤拒绝了 81,809 个分子中的 202 个（0.25%，150 个不同的 SMILES；这 202 个按步骤为：Reionizer 96 个、MetalDisconnector 83 个、Uncharger 规范原子排序 18 个、Normalizer 产物无法消毒 5 个），原生模型训练时同样不用它们；22 张模型卡除两个文件摘要、秒数与化学名称外与原生一致，22 个适用域文件是原生运行的字节。16,353 个测试集预测中，模型原始输出与适用域检索全部一致，36 个经 `10 **` 或 `expit` 的最终预测在 float64 末位相差 1 个单位。`pipeline.admet` 用这些模型处理 39 个分子的集合：拒绝了预期的 9 个，各在其对应的步骤；1 个未读入，与原生相同；对保留的 29 个分子，写出的 `admet.tsv` 是原生运行的字节。适用域检索尚未在实体 GPU 上运行，本机与 CI 都没有（该次运行中强制 GPU 的重跑用的是 SwiftShader 软件适配器，不是 GPU 的证据），故 P3 的验收标准（在实体 GPU 上经验证的执行与实测性能，见 [BROWSER_COMPUTE.md §6.1](../studio/docs/BROWSER_COMPUTE.md#61-the-science-kernels-and-the-device-check-p3)）尚未满足。

**网络药理学**（`bioagent research "<问题>" --formula <方剂> …`；Studio 任务 `research.run`，核心工具 `network_pharmacology_run`）：在经台账核验的数据快照上运行闭环研究：冻结方案、检索、以隔离的 PSH 工具调用执行分析、预注册反驳、经审计链证明后发布。在 Studio 中它也能作为浏览器任务在任务工作线程里运行，无需 Runner：此时分析经同一个 PSH 代理在进程内执行（Worker 不能启动子进程），产物记录 `isolation: "in_process"`（[studio/docs/BROWSER_JOBS.md](../studio/docs/BROWSER_JOBS.md)）。`--formula` 单独指定所研究的方剂（名称、带出处的名称或 ID）；问题文本若写的是另一个方剂，运行被拒绝。它不同于种子技能 `analyze-tcm-network-pharmacology`：后者是**有限种子语料网络假说分析**，只读取策展种子语料，不覆盖任何数据库，没有实测活性，也不做置换检验。
- 十六个工作流阶段（`bioagent.research.workflow`）逐一记录：状态（PASS、FAIL、BLOCKED、UNVERIFIED、SKIPPED、NOT_APPLICABLE）、原因、计数与复现命令。依次为：方剂身份、药材组成与基原物种、药材到化合物、结构身份与去重（按来源提供的标准 InChIKey，共享连接块的不同键只报告、不合并；快照不含结构，因此未做重新标准化，该阶段恒为 UNVERIFIED）、化合物到实测靶点、实测与预测分离、疾病基因、交集、PPI 网络、通路注释（Reactome；GO 因未登记 GO 快照而 SKIPPED；KEGG 因需要许可而 BLOCKED）、显式背景下的富集、覆盖全部检验通路的 BH 校正、度匹配置换与敏感性、拓扑（仅描述）、有界解释、可复现导出。
- 被方案丢弃的数据源会使读取它的每个阶段为 UNVERIFIED 并附上偏离记录；技能合同请求、但本次运行拒绝的数据源（如 `--allow-source` 未列入）由读取它的每个阶段注明，并在 `limitations.md` 中占一行。未读到数据的阶段绝不会是 PASS，空结果也绝不会是 PASS（读到 STRING 但背景中没有实测靶点时，PPI 与拓扑阶段为 UNVERIFIED：无法构建网络，不是“无相互作用”的证据），缺失的数据集也绝不当作阴性证据。未指定疾病时，疾病阶段为 NOT_APPLICABLE；Open Targets 快照被拒绝时为 SKIPPED。在 `stages.json` 中，无可检验对象的阶段记为 `not_applicable`，因此“什么都无法检验”的运行不是 `complete`；检验了通路但无一显著的运行是完整的。
- 九个导出文件：`compounds.tsv`、`targets.tsv`、`disease_targets.tsv`、`enrichment.tsv`、`network.tsv`（STRING 阈值以上的边列表，而非枢纽列表）、`provenance.json`（每条边追溯到快照记录）、`limitations.md`、`report.html` 与最后写出的 `run_manifest.json`（`tcmscience.run-manifest/1`：十六个阶段、每个输出的 SHA-256 与大小、快照 ID、参数、种子、代码摘要、方案摘要、复现命令，以及只记录本次执行身份的 `execution` 块，即审计链头与 `artifact.json` 摘要）。导出不含时间戳，两次全新运行字节相同；空表只写表头。
- 复核：`python -m bioagent.analysis.np_exports verify RUN --snapshots DIR --ledger FILE` 复核清单摘要，并从表格自身各列重算每个 p 值与 q 值，再在清单所列快照上重跑分析，表格须逐字节一致。`verify` 全部通过时退出 0，有检查与运行相矛盾时退出 1，无矛盾但有检查无法进行（未给快照，或富集表为空、无可重算）时退出 3。任一阶段自身检查为 FAIL 时，`bioagent research` 退出 3。Studio 的 `research.run` 在退出码 0 与 1 时都要求这九个文件，退出码 3 使任务失败；其校验器重读清单中的每个摘要（不经符号链接，只读普通文件），运行后被改动的表格、或清单中列为 FAIL 的阶段，都会使任务检查失败。
- 测试在植入世界（`bioagent.research.planted`）与测试夹具上运行，它们是夹具，不是数据。`live-data` CI 工作流中的真实数据用例：在 GitHub 运行器上下载真实的 NPASS、CMAUP、LOTUS、STRING 与 Reactome 文件，对葛根芩连汤运行研究任务，上传九个导出并执行 `verify`。这一步已在 `live-data` #16 与 #17（2026-10-19）中通过。它检查退出码、九个导出文件存在且非空，以及 `verify` 退出 0；它不断言任何计数或通路（见 [validation.md](validation.md#not-verified-here-and-why)）。
- 空结果会说明属于哪一种：「在这些快照中未找到 N 个成分的任何实测靶点，因此无法检验富集；这是空结果，不是无活性的证据」，或「已检验通路，但没有通路同时通过 BH 与置换检验」——这是缺乏富集证据，而不是任何通路无关的证据。被发布的结论只能是 `mechanism_hypothesis`（机制假说）；适应证重叠与拓扑只作描述，不构成结论。

**运行记录：阶段、完整性与局限。** 每个流程在运行中逐阶段记录（`bioagent.runrecord`）。每个阶段记录以下字段：
- 名称、是否必需、状态（成功 / 失败 / 跳过 / 不可用 / 不适用）；
- 检查结果（PASS、FAIL 等）与原因；
- 引擎与版本、实际计算设备、耗时和产出文件。

只有全部必需阶段成功、且没有任何检查为 FAIL 时，运行才算完整。
- `stages.json` 在每个阶段开始和结束时原子地重写；中途失败的运行会留下失败阶段及其错误。Studio 用它显示任务进度和各阶段。
- 运行结束时，`run.json` 增加 `stages`、`complete` 和 `limitations`，文件名和原有键不变。
- `limitations.md` 每行一条局限，与 `run.json` 的 `limitations` 一致，不含时间戳，重跑字节相同。
- `--verify` 除复核摘要外，对带阶段的记录还检查完整性和表格内容（列名、行数与记录一致）。旧记录只做摘要复核。
- 运行不完整时命令以退出码 1 结束，文件仍保留供查看。例如：没有配体对接成功、重对接超过 2 Å、最优构象未通过检查、ADMET 缺少模型。作为 Studio 任务，它会以失败告终，并列出出问题的阶段，绝不会显示为干净的「成功」。包装这些流程的候选 Skill 不会据此提出结论，并在局限中列出出问题的阶段。

各流程的阶段见上表。补充几点：
- **RNA-seq：** 在合并多泳道文件、质控和修剪之前先校验参考（转录本 ID 唯一、注释能对上；hisat2 时 GTF 序列名都在基因组中），再逐条校验全部 FASTQ 记录（而不只是质控抽样的前 20 万条），包括双端配对的读段名。
- **设备按实际记录：** 内置、Scanpy、Vina 和命令行工具都在 CPU 上。scVI 按设计在 CPU 上训练。本地 ESMFold 记录参数所在设备，它只通过 CUDA 使用 GPU，分配到 MPS 时实际在 CPU 上运行，并如实记录。ESM Atlas 记为远程。ColabFold 按其日志记录 `gpu` 或 `cpu`；`gpu` 只用于 ColabFold，因为其日志只说用了 GPU、不说是哪一块，并附说明：设备是日志报告的，不是本地观察到的。
- **结构预测检查点：** 本地 ESMFold 记录模型 ID、版本和权重本身的 SHA-256（同一进程中每个版本只计算一次），CUDA 上的峰值显存只写入 `run.json`、不写入 `summary.json`；ColabFold 记录版本与 `config.json` 摘要，AlphaFold2 参数本身未做摘要，因此其检查点记为「不可验证」（`verifiable: false`，`scope: config_only`）；只有对权重本身做了摘要的检查点才算可验证；ESM Atlas 的权重无法从本地观察，记为「不可验证」，不声称任何摘要。任何方法得到的模型，序列都必须与输入一致；检索到的结构只用于比较，绝不替代预测。
- **对接构象检查：** 坐标有限、重原子都在对接盒内、配体与受体重原子间距不小于 2.2 Å。这只是合理性检查，不是 PoseBusters，也不验证结合。`tests/test_docking.py` 用 RDKit、meeko 和 Vina 的替身测试各阶段与检查（测试中已注明），真实的 Vina 运行在 CI 的分子建模任务中，该任务连同这些检查在 `ci` #317（2026-10-19）中通过。`dock-ligands` Skill 只在运行完整时提出假说，且只针对最优构象通过这些检查的配体。
- **ADMET：** 每个终点记录完整的模型身份：模型和归档的摘要、训练集、指标、适用域阈值与摘要、库版本。缺少模型时，模型与预测阶段为「不可用」，运行不完整。

**参考数据集**（`bioagent/data/pipeline_references.json`，版本 2026.10.2）：每个流程一份（RNA-seq 每种模式各一份），记录内容摘要、参数和预期结果。
- RNA-seq（`omics_world` v1；计数矩阵模式为 `omics_counts_world` v1：2,000 个基因 × 6 个样本，上调、下调各预设 40 个四倍差异基因，要求每个方向至少找到 38 个、方向错误 0 个、假发现至多 15 个、检验 1,999 个基因）与单细胞的参考是**模拟数据（已知答案）**，不是公开数据，在本机重跑核对；在其上的运行会在 `limitations.md` 中注明。
- 结构参考为 PDB 1UBQ 与存档的 ESMFold 模型，离线核对。
- 对接参考（3PTB）需要 Vina，在 CI 的分子建模任务中核对，在 `ci` #317（2026-10-19）中通过。
- ADMET 参考需要 TDC 归档，仅在提供归档时核对。
