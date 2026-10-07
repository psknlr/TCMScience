# Single-cell cell atlas · 单细胞图谱

`scrna-cell-atlas` · implementation: `bioagent.skills.omics.scrna:scrna_cell_atlas` ·
**candidate, not in the stable lockfile**

> This file is documentation; `skill.yaml` is the contract. A governed run refuses this
> skill until a person reviews and promotes it.

<!-- zh -->
> 本文件是说明文档，契约是 `skill.yaml`。在有人审核并晋级之前，受治理运行会拒绝本 Skill。

The skill runs the single-cell pipeline (`bioagent scrna`) from count matrices to an
annotated atlas:
- QC with MAD outliers per sample;
- Scrublet doublets;
- Seurat-method HVGs and PCA;
- Harmony integration;
- Leiden clusters and a UMAP layout;
- Wilcoxon markers;
- marker-panel annotation;
- PAGA and, from a named root, diffusion pseudotime;
- pseudobulk DESeq2 between conditions per cell type.

<!-- zh -->
本 Skill 运行单细胞流程（`bioagent scrna`），从计数矩阵出发生成注释好的细胞图谱：
- 质控：按样本做 MAD 离群剔除；
- Scrublet 双细胞检测；
- Seurat 方法选取高变基因，并做 PCA；
- Harmony 批次整合；
- Leiden 聚类与 UMAP 布局；
- Wilcoxon 检验找标记基因；
- 基于标记基因面板的细胞类型注释；
- PAGA 轨迹，以及从指定起点出发的扩散伪时间；
- 按细胞类型做条件间的伪批量 DESeq2 比较。

## Implementations · 实现

Three independent choices, each defaulting to what ran before:
- `analysis_backend`: `builtin` or `scanpy` runs normalisation to markers;
- `integration_method`: `none`, `harmony` or `scvi` (scVI needs scvi-tools and is
  unverified);
- `de_backend`: `builtin` or `pydeseq2` runs the pseudobulk test.

A choice that is not installed is refused before any count is read, never replaced.
`run.json` records which implementation ran each stage, with versions, parameters
and seeds (`docs/omics-backends.md`).

<!-- zh -->
三个相互独立的选项，默认值均与原来一致：
- `analysis_backend`：`builtin` 或 `scanpy`，决定从归一化到标记基因的各步由谁执行；
- `integration_method`：`none`、`harmony` 或 `scvi`（scVI 需要 scvi-tools，尚未验证）；
- `de_backend`：`builtin` 或 `pydeseq2`，决定伪批量检验由谁执行。

未安装的选项会在读取任何计数之前被拒绝，绝不会被替换。`run.json` 记录每一步实际运行的实现及其版本、参数和随机种子（见 `docs/omics-backends.md`）。

## What it may claim · 可以声称什么

Clusters, labels and pseudotime are outputs, not claims. A cell-type label is an
inference from marker expression, and pseudotime is an ordering by similarity.

A claim is made only for a difference between conditions within a cell type, tested
on samples (pseudobulk), never on cells. Human samples support an `association`;
cells or animals support a `mechanism_hypothesis`.

<!-- zh -->
聚类、细胞类型标签和伪时间都是输出，而不是结论。细胞类型标签只是依据标记基因表达做出的推断，伪时间只是按相似度给细胞排序。

只有同一细胞类型内、不同条件之间的差异才会形成结论，而且检验的单位是样本（伪批量），从不是单个细胞。人体样本支持 `association`（相关性）；细胞或动物实验支持 `mechanism_hypothesis`（机制假说）。
