# Analysis pipelines

These pipelines take your own data or molecules through a complete analysis in one
call. Each one records every parameter, tool version and file digest, so a result can
be traced back to what produced it. Each also states what its result is and what it is
not. The governed skills that wrap them are **candidates**: they run as development
runs, which are recorded but never released, until a person reviews and promotes them.

| Pipeline | Command | Status |
|---|---|---|
| RNA-seq: FASTQ to differential expression | `bioagent rnaseq` | this document |

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

## 中文摘要

一键分析流程从用户自己的数据或分子出发完成整套分析。每一步都记录参数、工具版本和文件摘要，并说明结果是什么、不是什么。包装这些流程的受治理 Skill 都是**候选**：在有人审核并晋级之前，只能作为开发运行，会被记录但不会被发布。

**RNA-seq**（`bioagent rnaseq`）：从 nf-core 格式的样本表出发，依次完成以下步骤：
- 质控：FastQC 的各模块与阈值；
- 修剪：fastp，或内置的 cutadapt 式修剪；
- 定量：salmon、kallisto 或内置的 k-mer 伪比对与 EM；也可用 HISAT2 + featureCounts；
- 基因汇总：tximport 的规则；
- 差异表达：DESeq2 方法；
- 探索分析：VST、PCA 和样本间距离；
- 报告：带 SVG 图的 Markdown/HTML 报告，以及记录全部摘要的 `run.json`，可用 `--verify` 复核。

内置定量器对双端测序增加了片段长度一致性检查，纠正了外显子跳跃异构体被低估的问题。

验证有三层：
- 按方法定义逐项核对；
- 用已知答案的模拟实验检验：内置路径及 salmon、kallisto、fastp、HISAT2 均找回全部 7 个预设差异基因；
- 与独立实现 PyDESeq2 对比：倍数变化相关 0.99999，p 值相关 0.9995，显著基因几乎完全重合，实测 FDR 为 0.048。

结果只是本次实验内的统计关联，不证明机制、因果或任何临床效果。人体样本只能支持 `association`（相关性），细胞或动物实验只能支持 `mechanism_hypothesis`（机制假说）。
