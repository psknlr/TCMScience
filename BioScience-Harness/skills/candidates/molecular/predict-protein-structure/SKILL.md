# Protein structure prediction · 蛋白质结构预测

`predict-protein-structure` · implementation:
`bioagent.skills.molecular.structure:predict_protein_structure` · **candidate, not in the
stable lockfile**

> This file is documentation; `skill.yaml` is the contract. A governed run refuses this
> skill until a person reviews and promotes it.

<!-- zh -->
> 本文件是说明文档，契约是 `skill.yaml`。在有人审核并晋级之前，受治理运行会拒绝本 Skill。

The skill folds protein sequences with one of three methods:
- ESMFold through the ESM Atlas service, which needs `allow_remote` because the
  sequence is sent to Meta's server;
- ESMFold run locally;
- ColabFold.

For each model it reports:
- per-residue pLDDT and the low-confidence segments;
- DSSP secondary structure;
- geometry checks;
- when a reference is named, the TM-score, RMSD and GDT-TS against it.

The reference can be a PDB entry, an AlphaFold DB model or a file.

<!-- zh -->
本 Skill 可用三种方法预测蛋白质结构：
- 通过 ESM Atlas 服务调用 ESMFold：序列会发送到 Meta 的服务器，因此需要显式指定 `allow_remote`；
- 在本地运行 ESMFold；
- 使用 ColabFold。

对每个模型，它报告：
- 逐残基 pLDDT 与低置信度片段；
- DSSP 二级结构；
- 几何检查；
- 指定参考结构时，与参考结构的 TM-score、RMSD 和 GDT-TS。

参考结构可以是 PDB 条目、AlphaFold DB 模型或本地文件。

## What it may claim · 可以声称什么

Nothing. A predicted structure is a computational result, so the models and their checks
are outputs.

<!-- zh -->
不做任何结论。预测结构属于计算结果，模型及其检查只作为输出提供。
