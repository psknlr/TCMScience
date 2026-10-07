# Analysis pipelines

These pipelines take your own data or molecules through a complete analysis in one
call. Each one records every parameter, tool version and file digest, so a result can
be traced back to what produced it. Each also states what its result is and what it is
not. The governed skills that wrap them are **candidates**: they run as development
runs, which are recorded but never released, until a person reviews and promotes them.

| Pipeline | Command | Status |
|---|---|---|
| RNA-seq: FASTQ to differential expression | `bioagent rnaseq` | below |
| Single-cell: count matrices to an annotated atlas | `bioagent scrna` | below |
| Protein structure: sequences to checked models | `bioagent fold` | below |
| Docking: a receptor and ligands to validated poses | `bioagent dock` | below |
| ADMET: structures to properties, alerts and predicted endpoints | `bioagent admet` | below |

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
  PyDESeq2. In that job a missing tool fails the run rather than skipping it.

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

Every input must be raw counts; normalised values are refused.

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
- `--integration none|harmony|scvi` sets the batch integration;
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
| Domain | the largest Tanimoto similarity of the query to the model's training molecules; below 0.3 the prediction is flagged as outside the model's applicability domain |
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

## 中文摘要

一键分析流程从用户自己的数据或分子出发完成整套分析。每一步都记录参数、工具版本和文件摘要，并说明结果是什么、不是什么。包装这些流程的受治理 Skill 都是**候选**：在有人审核并晋级之前，只能作为开发运行，会被记录但不会被发布。

**RNA-seq**（`bioagent rnaseq`）：从 nf-core 格式的样本表出发，依次完成以下步骤：
- 质控：FastQC 的各模块与阈值；
- 修剪：fastp，或内置的 cutadapt 式修剪；
- 定量：salmon、kallisto 或内置的 k-mer 伪比对与 EM；也可用 HISAT2 + featureCounts；
- 基因汇总：tximport 的规则；
- 差异表达：DESeq2 方法，内置实现或 PyDESeq2（`--de-backend`，见 omics-backends.md）；
- 探索分析：VST、PCA 和样本间距离；
- 报告：带 SVG 图的 Markdown/HTML 报告，以及记录全部摘要的 `run.json`，可用 `--verify` 复核。

内置定量器对双端测序增加了片段长度一致性检查，纠正了外显子跳跃异构体被低估的问题。

验证有三层：
- 按方法定义逐项核对；
- 用已知答案的模拟实验检验：内置路径及 salmon、kallisto、fastp、HISAT2 均找回全部 7 个预设差异基因；
- 与独立实现 PyDESeq2 对比：倍数变化相关 0.99999，p 值相关 0.9995，显著基因几乎完全重合，实测 FDR 为 0.048。

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

分析后端（内置或 Scanpy）、批次整合方法（none、harmony 或 scvi）和伪批量检验实现（内置或 PyDESeq2）三者可独立选择，见 omics-backends.md。

验证有两类：
- 与参考实现对比：高变基因、Wilcoxon 分数和 Leiden 模块度与 Scanpy/leidenalg 一致，UMAP 连接度与 umap-learn 的差异在 5×10⁻⁶ 以内；
- 用已知答案的模拟数据检验：低质量细胞全部剔除，聚类 ARI 大于 0.9，注释正确，伪时间沿预设路径（Spearman 大于 0.85），伪批量只在预设的细胞类型中找到差异。

聚类、细胞类型标签和伪时间都只是输出，不构成结论；只有条件间的伪批量比较才会形成结论。

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
