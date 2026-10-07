# Selectable implementations: PyDESeq2, Scanpy, harmonypy, scVI and GSEApy

By default the analysis pipelines run their own implementations, written with numpy and
scipy and checked against the reference tools ([analysis-pipelines.md](analysis-pipelines.md)).
A run may now choose an established implementation instead for three stages, and run
preranked GSEA. Two rules hold for every choice:

1. **An implementation that is not installed is refused, with the reason.** The run
   stops before it reads any data and is never handed to the built-in implementation.
   A silent fallback would publish one method's numbers under another's name. The
   refusal is `bioagent.omics.optional.BackendUnavailable`, whose status is
   `ExecutionStatus.UNAVAILABLE`, and it says what to install.
2. **What ran is recorded.** Every result names its implementation and version, and
   `run.json` holds them with the parameters and seeds.

| Choice | Values (default first) | Where it is set | Where it is recorded |
|---|---|---|---|
| `de_backend` | `builtin`, `pydeseq2` | `RNASeqConfig`, `ScConfig` (pseudobulk), `--de-backend`, both omics skills | `run.json`: `differential_expression` (RNA-seq), `pseudobulk` (single cell) |
| `analysis_backend` | `builtin`, `scanpy` | `ScConfig`, `--analysis-backend`, `scrna-cell-atlas` | `run.json`: `analysis.stages` |
| `integration_method` | `harmony`, `none`, `scvi` | `ScConfig` (scVI: `scvi_epochs`, `scvi_threads`), `--integration`, `scrna-cell-atlas` | `run.json`: `integration` |
| preranked GSEA | GSEApy | `bioagent.omics.gsea` | `GSEAResult.record()` |

The defaults are what the pipelines did before, so an existing call gives the same result.

## Differential expression: one design, one contrast, one contract

`bioagent.omics.de_backends.run_de` is the single entry point. `run_rnaseq` calls it,
and so does the pseudobulk test of `run_scrna`. Where two implementations of DESeq2
could disagree about what a fold change means, the disagreement is settled once, before
either runs:

| Hazard | What would go wrong | What is done |
|---|---|---|
| Orientation | counts here are genes x samples; PyDESeq2 takes samples x genes | transposed once, with IDs attached; results read back by gene ID, never by position |
| Sample alignment | metadata matched by position pair a sample's counts with another sample's condition | matched by sample ID and put in the counts' order; a repeated, missing or extra ID is refused; a transposed matrix is named as such |
| Design | PyDESeq2's formula parser picks reference levels itself and decides which columns are numbers from their data types | one model matrix is built (`deseq.design_matrix`) and given to both; PyDESeq2 receives the matrix, not the formula |
| Reference level | a different reference changes which coefficient is the contrast | the contrast factor's reference is its denominator; other factors' references are their first level in sorted order; recorded as `reference_levels` |
| Contrast direction | numerator and denominator swapped invert every fold change | one numeric contrast vector over the named columns (+1 numerator, -1 denominator); recorded |
| Size factors | PyDESeq2 switches to an iterative estimator when every gene has a zero | median-of-ratios, or poscounts in that case, in both |
| Cook's outliers | PyDESeq2 replaces outlier counts at 7+ replicates and refits; the built-in implementation has no replacement step | in both, an outlier loses its p-value and no count is replaced |
| VST | one implementation's trend applied to the other's counts | each backend stabilises its own normalised counts with its own design-aware trend (DESeq2's `blind=FALSE`); `vst_detail` records which and the coefficients |

Both backends fill one result contract, with DESeq2's meanings:

| DESeq2 / PyDESeq2 | here | meaning |
|---|---|---|
| `baseMean` | `base_mean` | mean over all samples of the counts divided by the size factors |
| `log2FoldChange` | `log2_fold_change` | maximum-likelihood log2(numerator / denominator), not shrunken |
| `lfcSE` | `lfc_se` | its standard error |
| `stat` | `stat` | Wald statistic |
| `pvalue` | `p_value` | two-sided Wald p; missing for genes without counts and for Cook's outliers |
| `padj` | `p_adjusted` | Benjamini-Hochberg after independent filtering on baseMean |

Each backend's own estimates are kept apart from the contract: the gene-wise, trend and
final dispersions, the Cook's flags, convergence, the trend coefficients, and the prior
variance of the log dispersions. They go to `de_diagnostics.tsv` and to `diagnostics`
in `run.json`. The two optimisers reach these by different routes, so the estimates are
not presented as interchangeable. `deseq2_results.tsv` holds the contract columns and
nothing else, whichever backend ran.

### How the two compare

- **On the simulated experiment** (`tests/omics_world.py`: 30 genes, 3 vs 3, G000-G003
  four-fold up and G004-G006 four-fold down), both backends call all seven planted
  genes with the same sign. Their fold changes correlate at 0.999999. The one call
  they disagree on, G021, is at the threshold in both (FDR 0.046 built-in, 0.051
  PyDESeq2). Reversing the contrast negates every fold change, and permuting the
  samples changes nothing, in both backends.
- **On 1,500 simulated genes** (4 vs 4, with a batch term), the size factors and base
  means are identical. Fold changes correlate at 0.999994 and -log10 p at 0.99991. The
  132 genes PyDESeq2 calls are all among the built-in implementation's 134.
- **With two samples per condition they differ.** On the B-cell pseudobulk of
  `tests/sc_world.py`, the fold changes still correlate at 0.999994. The built-in
  implementation calls 16 genes and PyDESeq2 calls 7, all among the 20 planted ones.
  The cause is dispersion shrinkage with two residual degrees of freedom. PyDESeq2's
  bounded optimiser leaves 737 of 1,200 gene-wise estimates at the floor, and the
  built-in search leaves 487. For each of the 250 genes that only PyDESeq2 leaves
  there, the built-in estimate has the higher Cox-Reid adjusted profile likelihood
  (at the same means). Floor estimates are left out of the prior's spread, so
  PyDESeq2's prior variance is 0.43 and the built-in one's 1.71. PyDESeq2 shrinks
  harder, towards a higher trend. PyDESeq2 itself warns that below three residual
  degrees of freedom the prior is poorly estimated, and the warning is kept in the
  record. Both quantities are recorded (`prior_dispersion_variance`,
  `gene_wise_at_floor`). R's DESeq2, run on the same counts, gives neither answer
  (next section).

### Against R's DESeq2

R 4.3.3 with DESeq2 1.42.0 (Ubuntu 24.04's `r-base-core` and `r-bioc-deseq2`) ran on
the same counts and design through `tests/deseq2_reference.R`. `DESeq()` and
`results()` kept their defaults, except `alpha = 0.05`: independent filtering then
optimises the FDR these pipelines call genes at.

**Two samples per condition**, the B-cell pseudobulk above (1,200 genes, two residual
degrees of freedom):

| | built-in | PyDESeq2 0.5.4 | R DESeq2 1.42.0 |
|---|---|---|---|
| genes at FDR < 0.05, all planted | 16 | 7 | 8 |
| log2 fold changes, correlation with R's | 0.999992 | 0.999999 | |
| −log10 p, correlation with R's | 0.970 | 0.998 | |
| gene-wise dispersions at the floor | 487 | 737 | 737, the same genes as PyDESeq2 |
| dispersion trend a0, a1 | 0.080, 1.23 | 0.105, 1.50 | 0.112, 2.09 |
| prior variance of log dispersions | 1.71 | 0.43 | 0.64 |
| final dispersions, median \|log ratio\| to R's | 0.74 | 0.065 | |

R calls PyDESeq2's seven genes and one more (GENE0317). The built-in implementation's
16 include all eight. Size factors and base means are the same in all three, to
1e-14. Each departure from R was traced to one step:

- **Gene-wise dispersions.** R starts each gene's line search at the smaller of the
  rough and moments estimates, and a gene that starts at the floor stays there: its
  737 floor genes are exactly those whose starting value is at the floor. PyDESeq2's
  optimiser stops at the same 737. The built-in search covers the whole range and
  finds an interior maximum for 250 of them, with a higher adjusted profile
  likelihood: a better optimum of the same function, but not what DESeq2 computes.
  Above the floor, both Python implementations' estimates agree with R's to a median
  0.1%.
- **The trend.** Given R's gene-wise estimates, the built-in trend fit returns R's
  coefficients to within 3e-6. PyDESeq2's fit starts from every non-zero gene, the
  floor estimates included, and drops outliers for good. Run on R's own estimates, its
  loop gives 0.105 and 1.49, close to its own 0.105 and 1.50. Started from the genes
  above the floor, as R starts, it gives R's coefficients.
- **The prior variance.** With one to three residual degrees of freedom, R does not
  use the residuals' MAD variance less trigamma((m − p)/2), which both Python
  implementations use. On R's own residuals that would give 0.32. R instead matches
  the residuals' distribution by simulation (`set.seed(2)`, a KL divergence over a
  grid of variances), which gives 0.64. Neither Python implementation does this:
  PyDESeq2 warns, and the built-in implementation now notes it in the result.
- **Every later step matches.** Given R's gene-wise dispersions and R's prior variance,
  the built-in MAP dispersions, GLM, Wald test and independent filtering give R's eight
  genes. Its final dispersions are then within 0.2% of R's and its adjusted p-values
  within 4e-4. Given R's p-values, its independent filtering gives R's threshold and
  adjusted p-values to within 3e-15, in this case and in the control below.

**The control: five residual degrees of freedom.** On 1,500 genes of
`omics_world.simulate_counts` (seed 0; 4 vs 4 with a batch term), R estimates the
prior variance as both Python implementations do, and all three are at the minimum,
0.25. R calls 144 genes. PyDESeq2 calls 143, all among R's, and the built-in
implementation 149, including all of R's. Their −log10 p correlate with R's at
0.9999999 (PyDESeq2) and 0.9996 (built-in).

With two samples per condition, then, neither Python implementation gives DESeq2's
answer, and PyDESeq2's is the nearer. To reproduce R there, the built-in
implementation would need DESeq2's line search from the rough estimate and the
simulation-based prior variance; it has neither. A run records which implementation
called its genes, the prior variance and the floor count. `tests/test_omics_deseq2_r.py`
repeats the comparison, the built-in implementation's attribution and the control
wherever R and DESeq2 are installed (two to four minutes here).

## Single cell: three independent choices

`analysis_backend` decides who runs steps 3 to 6 of `run_scrna`. QC, doublets,
annotation, PAGA, pseudotime and the report are the same built-in steps either way, so
both backends analyse the same cells.

| Stage | `builtin` | `scanpy` (parameters and seed recorded per stage) |
|---|---|---|
| normalisation, log1p | `preprocess.normalize_log1p` | `pp.normalize_total(target_sum=1e4)`, `pp.log1p` |
| highly variable genes | Seurat method, batch-aware | `pp.highly_variable_genes(flavor="seurat", batch_key=…)` |
| scaling, PCA | `preprocess.scale`, `preprocess.pca` | `pp.scale(max_value=10)`, `pp.pca(random_state=seed)` |
| neighbours | exact kNN, UMAP connectivities | `pp.neighbors(transformer=…)`: exact below 8,192 cells, PyNNDescent above, passed explicitly |
| clusters | `leiden.leiden` | `tl.leiden(flavor="leidenalg", n_iterations=-1)` |
| layout | `umap.umap_layout` | `tl.umap` (umap-learn) |
| markers | tie-corrected Wilcoxon | `tl.rank_genes_groups(method="wilcoxon", tie_correct=True)`, all genes |

The data cross into Scanpy through `sc.io.to_anndata` and back through `from_anndata`.
The raw counts travel in `layers["counts"]` as well as `X`, because Scanpy normalises `X`
in place. Barcodes, gene IDs, gene names and every per-cell annotation (sample, donor,
batch, condition) are carried by name. Repeated barcodes or gene names are refused,
and so is a normalised matrix without a counts layer. The modularity in the report is
computed by this package's own function from each backend's graph, so the two numbers
mean the same thing.

`integration_method` decides what the neighbour graph is built on:

- **`harmony`**: Harmony has two implementations here, and the record names the one
  that ran. Under `builtin` it is `sc.harmony`, which follows the original R package
  (lambda 1, 20 clustering iterations per round). Under `scanpy` it is harmonypy, which
  Scanpy's own `external.pp.harmony_integrate` wraps. That wrapper is not used: under
  Scanpy 1.12 with harmonypy 2 it transposes a result that is already cells x PCs, and
  fails. harmonypy is called directly, and its output is oriented by comparing its copy
  of the input with the input: harmonypy 0.0.10 returns PCs x cells, and 0.1.0 and
  later cells x PCs. harmonypy 2 also changed its defaults (lambda estimated per
  cluster, 4 clustering iterations, looser tolerances), so its effective parameters
  are recorded as read from its own signature.
- **`scvi`**: scVI's latent space (scvi-tools), the posterior mean, trained on the raw
  counts of the highly variable genes with the batch as covariate. Training runs on
  the CPU from the run's seed, on `scvi_threads` torch threads (1 by default), for
  `scvi_epochs` epochs (400, scVI's own choice for up to 20,000 cells). The record holds
  scvi-tools' and torch's versions, the seed and threads, the model's and `train()`'s
  effective parameters (read from their signatures, with what was passed laid over
  them), the epochs trained, the train/validation split, the training history's last
  values, a digest of the trained weights, and the digests of the saved model
  (`scvi_model/` in the run directory). See [scVI, run](#scvi-run) below.
- **`none`**: the PCA embedding.

With a single batch nothing is integrated. The record says which method was requested
and why it did not run.

On the simulated atlas, the Scanpy backend recovers the four cell types (ARI above 0.9
against the truth; 1.0 in the recorded run) and names them correctly. It agrees with
the built-in clusters (ARI above 0.9), and harmonypy raises batch mixing from 0.21 to
1.00. A rerun gives identical tables. With harmonypy 0.0.10 instead of 2.1.0, mixing
rises from 0.21 to 1.03 and the ARI is 1.0.

### scVI, run

scvi-tools 1.5.1, with torch 2.14.1 (the CPU wheel) and Lightning 2.6.6, ran on the
simulated atlas: 1,077 cells after QC and doublets, 600 highly variable genes, two
batches. The built-in stages ran around it, with seed 0 and one thread. Batch mixing
is 0.21 on the PCA embedding; 1 means as many neighbours from the other batch as
perfect mixing would give, so a little above 1 is possible:

| epochs | mixing after | ARI, the four types | clusters | `run_scrna` time |
|---|---|---|---|---|
| 30 | 0.64 | 0.83 | 8: NK cells and platelets each split in two | 36 s |
| 60 | 0.89 | 1.0 | 5: the four types, named right, and the path cells | 50 s |
| 100 | 0.95 | 1.0 | 5, as at 60 | 63 s |
| 400, the default | 1.03 | 1.0 | 5, as at 60 | 115 s |

The times are for the whole pipeline on a shared four-core machine. Under the Scanpy
backend at 100 epochs, scVI receives the same counts (Scanpy's Seurat-method genes are
the built-in ones) and trains to the same weights, and the clusters table is
identical.

- **A rerun reproduces it.** Three runs at 400 epochs, each in its own process and
  under different loads (115 s to 248 s), gave the same weights digest (`b7c8480a…`),
  training history and tables. So did two runs at 100 epochs in one process (the
  test).
- **The saved model's digest is not reproducible, and is not meant to be compared.**
  Saving the same weights twice gives two different files: torch writes a random
  serialization id into each, and scvi-tools stores a fresh UUID for the data setup
  and the time of the run. The record therefore also digests the weights themselves:
  each tensor's name, type, shape and bytes.
- **The thread count is part of the computation.** With two threads instead of one,
  the latent space moved by up to 2e-6 after 20 epochs. It also sets the speed: on
  this busy four-core machine, one epoch over 1,000 cells and 600 genes took 0.3 s on
  one thread and 23 s on four, because torch's threads spin while they wait for each
  other. The pipeline sets the thread count for training, records it, and restores
  the caller's afterwards.

The reproduction was shown on one machine with one set of versions. Another CPU, or
another torch build, may give other weights. `tests/test_omics_scvi.py` repeats the
100-epoch run and the rerun wherever scvi-tools is installed; it took 2.5 minutes here.

## Preranked GSEA

`bioagent.omics.gsea` runs GSEApy (BSD-3-Clause) by gene-set permutation:

```python
ranked = gsea.ranked_from_de(result, method="stat", species="human", id_type="symbol")
library = gsea.read_gmt("sets.gmt", source="…", version="…", licence="…",
                        species="human", id_type="symbol")
res = gsea.run_prerank(ranked, library, min_size=15, max_size=500,
                       permutations=1000, seed=0)
```

- **The ranking is declared**: `stat`, `signed_log10_p` or `log2fc`. A positive score
  means higher in the contrast's numerator, whichever backend produced the result.
  Genes without a value are dropped and listed. p-values that underflowed to 0 are
  capped and counted. Ties are broken by gene ID, and counted.
- **The gene sets are a snapshot**: a local GMT file whose SHA-256 is recorded with the
  source, version and licence you declare. These are required; the licence is yours to
  check against the provider's terms. GSEApy reads a `gene_sets` string that is not a
  file as an Enrichr library name and downloads it. Here the sets are always parsed
  from the file, so nothing is fetched.
- **Identifiers are matched exactly.** The ranking and the library each declare a
  species and an identifier type, and a mismatch is refused. GSEApy upper-cases a
  mixed-case ranked list when the sets look upper-case, which maps mouse symbols onto
  human ones without a word. GSEApy therefore receives neutral codes after exact
  matching, and that heuristic cannot apply. The mapping report lists every set gene
  that was not found, with each set's size and matched size.
- **GSEApy's own clean-up is not relied on.** It would rename duplicate IDs, replace
  infinities with neighbouring values, and order ties arbitrarily with an unstable sort.
  Here duplicates and infinities are refused and the order is passed as is.

The output, for each tested set, is NES, the nominal p-value, FDR, FWER and the
leading-edge genes. The sets that were not tested are listed with the reason. A
nominal p of 0 means p < 1/permutations, and that resolution is recorded. In the tests
a set of the 40 top-ranked genes comes out at NES > 2 and FDR < 0.01, a set of the
bottom 40 at NES < -2, and five random sets are not significant (p > 0.05,
FDR > 0.25). A fixed seed gives identical numbers.

GSEA is a separate function and not a step of the `rnaseq-differential-expression`
skill, because it does not fit that manifest cleanly. It needs a third-party gene-set
file, which the skill would have to declare and pin as a source with its own licence.
Its finding is a statement about gene sets, a different claim with different evidence
from the skill's one claim about genes. And the pipeline's gene IDs come from the
annotation (usually Ensembl), while gene-set files usually list symbols, so the
pipeline would need an ID mapping it does not do. Run it on the skill's result table
or on `RNASeqRun.result`.

## Installing, and where the tests run

```bash
pip install 'bioagent[analysis]'   # PyDESeq2, Scanpy, leidenalg, umap-learn, harmonypy, GSEApy
pip install torch --index-url https://download.pytorch.org/whl/cpu   # CPU build, 196 MB
pip install 'bioagent[scvi]'       # scvi-tools 1.5.1 or later
sudo apt-get install --no-install-recommends r-base-core r-bioc-deseq2   # the R cross-check
```

The analysis extra's floors are releases that passed the tests. GSEApy 1.1.6 passes
`tests/test_omics_gsea.py`; 1.1.0 and 1.1.5 fail it, because before 1.1.6 prerank sorts
the ranking itself and stops on `ascending=None`, so `run_prerank` now refuses them
with that reason. harmonypy 0.0.10 passes `tests/test_omics_sc_backends.py`, through
the transposed-output branch above. The pipeline's harmonypy call was also probed
with 0.1.0, 0.2.0, 2.0.0 and 2.1.0, and placed each result the right way round. Of
these, 0.2.0 alone requires torch, which from PyPI is the CUDA build, 5.2 GB installed;
a resolver installs the newest release unless told otherwise.
Without PyTorch's CPU index, `bioagent[scvi]` brings PyTorch's CUDA build from PyPI;
with it, scvi-tools and torch add about 0.9 GB to an analysis install (40 packages,
torch 711 MB).

The tests:

- `tests/test_omics_de_backends.py`, `tests/test_omics_sc_backends.py` and
  `tests/test_omics_gsea.py` need the analysis extra. In CI's job "Analysis pipelines
  with the standard tools", it is installed and `BIOAGENT_REQUIRE_TOOLS=1` turns a
  missing package into a failure, so all of them run.
- `tests/test_omics_scvi.py` needs scvi-tools as well, and `tests/test_omics_deseq2_r.py`
  needs `Rscript` with DESeq2 (and PyDESeq2 for its PyDESeq2 checks). Neither is in
  that job's list.

A test that needs an optional package or tool skips without it, and fails instead
under `BIOAGENT_REQUIRE_TOOLS=1`. The unit tier passes with skips, and its refusal
and alignment tests run there; scVI's record is checked there against stand-ins.

## Limits

- PyDESeq2 fits each gene with SciPy's L-BFGS-B. In these runs it was over ten times
  slower than the built-in implementation, single-threaded on a shared machine.
  `RNASeqConfig.threads` sets its worker processes; the pseudobulk test uses one.
- scVI was run with one release of scvi-tools and torch, on one machine. Its
  reproduction holds for the same versions, CPU and thread count; nothing else was
  tested.
- With three or fewer residual degrees of freedom, the built-in DESeq2 implementation
  does not reproduce R's DESeq2 (see above); the result says so in its notes.
- Trajectories (PAGA, diffusion pseudotime) are built-in under either analysis backend.
  They run on the backend's neighbour graph.
- Leiden runs leidenalg's modularity under Scanpy, as the built-in implementation
  does. igraph's faster flavour is not used.

## 中文摘要

**问题：** 流程默认使用本项目自己的实现。PyDESeq2、Scanpy 等成熟实现此前只用于对照测试。现在三个环节可以选择成熟实现，并新增预排序 GSEA。两条规则对每个选项都成立：未安装的实现会被拒绝并说明原因（`ExecutionStatus.UNAVAILABLE`），绝不静默改用内置实现；实际运行的实现及其版本写入结果和 `run.json`。默认值与原来一致。

**差异表达（`de_backend`：`builtin` / `pydeseq2`）：** 唯一入口是 `de_backends.run_de`，供 RNA-seq 流程和单细胞伪批量检验共用。两个实现可能在以下几处悄悄不一致，这里在运行前统一处理：
- **矩阵方向：** 只转置一次，带着 ID 走，结果按基因 ID 读回。
- **样本对齐：** 元数据按样本 ID 匹配，重复、缺失或多余的 ID 均被拒绝。
- **设计矩阵：** 只构建一次，交给两个实现；PyDESeq2 拿到的是矩阵而不是公式。
- **参照水平与对比方向：** 对比因子的参照水平是分母；分子 +1、分母 −1，log2FC > 0 表示分子组更高。
- **大小因子与 Cook 离群值：** 规则一致；离群值只去掉 p 值，不替换计数。
- **VST：** 各自用自己的趋势计算。

结果契约是 DESeq2 的六列（baseMean、log2FoldChange、lfcSE、stat、pvalue、padj），离散度等各自的中间估计单独存放。实测：
- **模拟实验（每组 3 个样本）：** 两者都找到全部 7 个预设基因，方向一致，倍数变化相关 0.999999；交换对比方向结果取反，打乱样本顺序结果不变。
- **每组只有 2 个样本时：** 倍数变化仍一致，但显著基因数不同（内置 16 个，PyDESeq2 7 个，均属预设基因）。原因是离散度收缩：PyDESeq2 的优化器把更多基因的估计停在下限，先验方差因而更小（0.43 对 1.71）。这两个量都被记录。

**与 R 的 DESeq2 对照（R 4.3.3，DESeq2 1.42.0，Ubuntu 软件包）：** 在同一份 B 细胞伪批量计数和同一设计上，R 找到 8 个基因（均属预设基因），即 PyDESeq2 的 7 个再加 1 个；内置实现的 16 个包含全部 8 个。所以每组 2 个样本时，两个 Python 实现都没有给出 DESeq2 的答案，PyDESeq2 更接近。差异逐步归因：
- **逐基因离散度：** R 从粗估值出发做线搜索，起点在下限的基因就停在下限（737 个，与 PyDESeq2 完全相同）；内置实现在整个区间搜索，为其中 250 个基因找到似然更高的内点。
- **趋势：** 用 R 的逐基因估计，内置趋势拟合得到 R 的系数；PyDESeq2 从全部非零基因（含下限基因）起拟合并永久剔除离群点，因而趋势偏低。
- **先验方差：** 残差自由度为 1 到 3 时，R 不用 MAD 方差减 trigamma，而是用模拟匹配残差分布（得 0.64）；两个 Python 实现都不这样做，内置实现现在会在结果备注中说明。
- **其余步骤一致：** 给定 R 的逐基因离散度和先验方差，内置实现的 MAP、GLM、Wald 检验和独立过滤得到与 R 完全相同的 8 个基因。

对照组（1,500 个模拟基因，每组 4 个样本加批次项，残差自由度 5）：三者先验方差相同，R 找到 144 个基因，PyDESeq2 的 143 个全在其中，内置实现的 149 个包含 R 的全部。`tests/test_omics_deseq2_r.py` 在装有 R 和 DESeq2 的环境中重复这些比较。

**单细胞（三个独立选项）：**
- **`analysis_backend`：** `builtin` 或 `scanpy`，决定从归一化到标记基因的各步由谁执行，每步的参数和种子都有记录；CellMatrix 与 AnnData 互转时保留原始计数（`layers["counts"]`）、ID 和样本注释。
- **`integration_method`：** `harmony`、`none` 或 `scvi`。Harmony 有两个实现，记录写明实际运行的是哪个。Scanpy 1.12 自带的 harmony 封装在 harmonypy 2 下会转置出错，因此直接调用 harmonypy，并核对输出方向（harmonypy 0.0.10 输出为 PC × 细胞，0.1.0 起为细胞 × PC）。scVI 未安装时被拒绝。
- **`de_backend`：** 伪批量检验的实现。

**scVI 已实际运行（scvi-tools 1.5.1，torch 2.14.1 CPU 版）：** 在模拟图谱上（1,077 个细胞，600 个高变基因，两个批次），种子 0、单线程：100 轮训练时批次混合度从 0.21 升到 0.95，400 轮（默认）升到 1.03；两者对四种细胞类型的 ARI 均为 1.0，注释正确；30 轮则不够（ARI 0.83）。记录内容包括 scvi-tools 与 torch 版本、种子、线程数、模型与训练的实际参数、训练轮数、训练/验证划分、训练历史末值、权重摘要和模型文件摘要。
- **重跑可复现：** 同一进程或不同进程重跑，权重摘要、训练历史和各表格完全相同。
- **模型文件摘要每次都不同：** torch 每次保存写入随机序列号，scvi-tools 写入新的 UUID 和运行时间，因此另记权重本身的摘要，用于比对。
- **线程数是计算的一部分：** 两个线程与一个线程的潜空间相差可达 2e-6；在繁忙的四核机器上，四线程每轮 23 秒，单线程 0.3 秒。流程在训练时设定线程数（默认 1）、记录并在结束后恢复。

**预排序 GSEA（`bioagent.omics.gsea`，GSEApy）：**
- **排序方法须声明。**
- **基因集来自本地 GMT 文件快照：** 记录 SHA-256、来源、版本和许可证。
- **ID 精确匹配：** 物种和 ID 类型须一致，否则拒绝。GSEApy 会把大小写混合的基因名转成大写去匹配（可能把小鼠基因映射到人类基因），这里先精确匹配再以中性编码交给 GSEApy，杜绝此类启发式。
- **输出：** NES、p 值、FDR、前沿基因和映射报告。

GSEA 不作为 Skill 的一步：它需要第三方基因集来源及其许可证，结论类型也不同，而且需要流程本身不做的 ID 映射。

**安装与测试：**
- **安装：** `bioagent[analysis]` 提供 GSEApy 与 harmonypy，下限为实测通过的版本：GSEApy 1.1.6（1.1.0 和 1.1.5 未通过，现在会被拒绝并说明原因），harmonypy 0.0.10。`bioagent[scvi]` 提供 scvi-tools 1.5.1 及以上；先从 PyTorch 的 CPU 源安装 torch，可避免从 PyPI 装入 CUDA 版。
- **测试：** 三个测试文件在单元层自动跳过需要可选包的部分，在 CI 的 analysis-tools 作业中全部运行。`tests/test_omics_scvi.py` 还需要 scvi-tools，`tests/test_omics_deseq2_r.py` 还需要 R 和 DESeq2，它们不在该作业的列表中。
