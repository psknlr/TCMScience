# RNA-seq differential expression · RNA-seq 差异表达分析

`rnaseq-differential-expression` · implementation:
`bioagent.skills.omics.rnaseq:rnaseq_differential_expression` · **candidate, not in the
stable lockfile**

> This file is documentation; `skill.yaml` is the contract. A governed run refuses this
> skill until a person reviews and promotes it; until then it runs only as a development
> run, which is recorded but never released.

<!-- zh -->
> 本文件是说明文档，契约是 `skill.yaml`。在有人审核并晋级之前，受治理运行会拒绝本 Skill；目前只能以开发运行方式执行，运行会被记录，但不会被发布。

From a sample sheet of FASTQ files to a differential-expression report, in one call:

1. QC of every file with FastQC's modules and limits;
2. trimming (fastp when installed, otherwise a built-in cutadapt-style trimmer);
3. quantification (salmon, kallisto or the built-in k-mer pseudoaligner with
   kallisto's EM; or HISAT2 and featureCounts against a genome);
4. gene totals with tximport's rules;
5. the DESeq2 method: size factors, shrunken dispersions, NB GLM, Wald test, Cook's
   distances, independent filtering, Benjamini–Hochberg;
6. VST, PCA and sample distances; a Markdown and HTML report with SVG figures; and a
   `run.json` holding every parameter, tool version and file digest.

<!-- zh -->
一次调用即可完成从 FASTQ 样本表到差异表达报告的全过程：

1. 按 FastQC 的模块和阈值对每个文件做质控；
2. 修剪（已安装 fastp 时用 fastp，否则用内置的 cutadapt 式修剪）；
3. 定量（salmon、kallisto 或内置的 k-mer 伪比对加 kallisto 的 EM 算法；也可用 HISAT2 比对到基因组后由 featureCounts 计数）；
4. 按 tximport 的规则汇总到基因；
5. DESeq2 方法：大小因子、收缩离散度、负二项 GLM、Wald 检验、Cook 距离、独立过滤、Benjamini–Hochberg 校正；
6. VST、PCA 和样本间距离；生成带 SVG 图的 Markdown 和 HTML 报告，以及记录全部参数、工具版本和文件摘要的 `run.json`。

## What it may claim · 可以声称什么

One claim, made only when some gene differs: in these samples, these genes differ
between the two groups. An observational study of human samples supports an
`association`; cells or animals support a `mechanism_hypothesis`, and nothing about
people. It never claims a mechanism, an effect or a recommendation.

<!-- zh -->
只有存在差异基因时，才会给出唯一一条结论：在这些样本中，这些基因在两组之间存在差异。人体样本的观察性研究支持 `association`（相关性）；细胞或动物实验支持 `mechanism_hypothesis`（机制假说），且不涉及人。本 Skill 从不声称机制已确立、存在疗效，也不提供任何建议。
