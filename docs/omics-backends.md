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
| `integration_method` | `harmony`, `none`, `scvi` | `ScConfig`, `--integration`, `scrna-cell-atlas` | `run.json`: `integration` |
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
  `gene_wise_at_floor`). R's DESeq2 was not run here, so neither answer is "the"
  DESeq2 result. With two samples per condition, the gene calls depend on the
  implementation, and the run record says which one made them.

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
  of the input with the input. harmonypy 2 also changed its defaults (lambda estimated
  per cluster, 4 clustering iterations, looser tolerances), so its effective parameters
  are recorded as read from its own signature.
- **`scvi`**: scVI's latent space (scvi-tools), trained on the raw counts of the highly
  variable genes with the batch as covariate, on the CPU so that the seed reproduces
  it. The model settings, training settings, seed and the digests of the saved model
  (`scvi_model/` in the run directory) are recorded. **This path is unverified.**
  scvi-tools is not installed here or in CI. The tests check the refusal without it,
  and they check what is recorded against a stand-in. They do not run scVI.
- **`none`**: the PCA embedding.

With a single batch nothing is integrated. The record says which method was requested
and why it did not run.

On the simulated atlas, the Scanpy backend recovers the four cell types (ARI above 0.9
against the truth; 1.0 in the recorded run) and names them correctly. It agrees with
the built-in clusters (ARI above 0.9), and harmonypy raises batch mixing from 0.21 to
1.00. A rerun gives identical tables.

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
pip install 'bioagent[scvi]'       # scvi-tools (PyTorch); not tested anywhere
```

The tests are `tests/test_omics_de_backends.py`, `tests/test_omics_sc_backends.py` and
`tests/test_omics_gsea.py`. A test that needs an optional package skips without it.
The unit tier passes with skips, and its refusal and alignment tests run there. In
CI's job "Analysis pipelines with the standard tools", the extra is installed and
`BIOAGENT_REQUIRE_TOOLS=1` turns a missing package into a failure, so all of them run.

## Limits

- PyDESeq2 fits each gene with SciPy's L-BFGS-B. In these runs it was over ten times
  slower than the built-in implementation, single-threaded on a shared machine.
  `RNASeqConfig.threads` sets its worker processes; the pseudobulk test uses one.
- The scVI path has not been run against scvi-tools.
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

**单细胞（三个独立选项）：**
- **`analysis_backend`：** `builtin` 或 `scanpy`，决定从归一化到标记基因的各步由谁执行，每步的参数和种子都有记录；CellMatrix 与 AnnData 互转时保留原始计数（`layers["counts"]`）、ID 和样本注释。
- **`integration_method`：** `harmony`、`none` 或 `scvi`。Harmony 有两个实现，记录写明实际运行的是哪个。Scanpy 1.12 自带的 harmony 封装在 harmonypy 2 下会转置出错，因此直接调用 harmonypy，并核对输出方向。scVI 未安装时被拒绝；安装时记录训练配置、种子和模型摘要，但**这条路径尚未验证**。
- **`de_backend`：** 伪批量检验的实现。

**预排序 GSEA（`bioagent.omics.gsea`，GSEApy）：**
- **排序方法须声明。**
- **基因集来自本地 GMT 文件快照：** 记录 SHA-256、来源、版本和许可证。
- **ID 精确匹配：** 物种和 ID 类型须一致，否则拒绝。GSEApy 会把大小写混合的基因名转成大写去匹配（可能把小鼠基因映射到人类基因），这里先精确匹配再以中性编码交给 GSEApy，杜绝此类启发式。
- **输出：** NES、p 值、FDR、前沿基因和映射报告。

GSEA 不作为 Skill 的一步：它需要第三方基因集来源及其许可证，结论类型也不同，而且需要流程本身不做的 ID 映射。

**安装与测试：**
- **安装：** `bioagent[analysis]` 新增 GSEApy 与 harmonypy；`bioagent[scvi]` 提供 scvi-tools（未测试）。
- **测试：** 三个新测试文件在单元层自动跳过需要可选包的部分，在 CI 的 analysis-tools 作业中全部运行。
