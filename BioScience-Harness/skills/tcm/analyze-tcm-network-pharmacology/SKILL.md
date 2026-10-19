# Limited seed-corpus network hypothesis analysis · 有限种子语料网络假说分析

`analyze-tcm-network-pharmacology` · implementation: `bioagent.skills.p0:analyze_tcm_network_pharmacology`

> This file is documentation. It is **not** parsed for authority and never
> compiled — `skill.yaml` is the contract (see `docs/adr/0003`). It is attached
> to each artifact so a reader can see what the skill claims to do.

<!-- zh -->
> 本文件是说明文档。它**不**作为权威依据被解析，也从不参与编译——契约是 `skill.yaml`（见 `docs/adr/0003`）。它被附在每个科研产物（research artifact, RA）上，让读者看到该 Skill 声称做了什么。

A limited seed-corpus network hypothesis analysis: no database coverage, no measured
activity. It builds a herb–target network for a formula from the curated seed corpus
only, keeping predicted and measured edges strictly apart. The seed corpus records no
molecular target for any of its formulas, so on it the skill builds no target network
and makes no claim. It draws no permutation null and computes no topology.

The data-driven workflow is a different program: the research loop (`bioagent research`,
the Studio job `research.run`, core tool `network_pharmacology_run`) reads measured
activities from NPASS, CMAUP and LOTUS snapshots, tests Reactome enrichment against a
stated background with a degree-matched permutation null, records sixteen workflow stages
and writes nine exports (see `docs/analysis-pipelines.md`).

<!-- zh -->
有限种子语料网络假说分析：不覆盖任何数据库，没有实测活性。它只从策展种子语料为一个方剂构建药材–靶点网络，把预测边与实测边严格分开。种子语料没有为其中任何方剂记录分子靶点，因此在种子语料上本技能不构建靶点网络，也不提出任何主张；它不做置换检验，也不计算拓扑。

数据驱动的工作流是另一个程序：研究闭环（`bioagent research`，Studio 任务 `research.run`，核心工具 `network_pharmacology_run`）从 NPASS、CMAUP 与 LOTUS 快照读取实测活性，在显式背景下检验 Reactome 富集并做度匹配置换检验，记录十六个工作流阶段，并写出九个导出文件（见 `docs/analysis-pipelines.md`）。

## The separation, which is the point · 分离本身就是重点

`predicted_targets` and `measured_targets` are **different keys**. There is no
combined `targets` list, so a consumer cannot accidentally read a prediction as a
measurement. Every predicted edge also carries a predictive evidence design
(`network_prediction`). Its evidence is marked `directness: DIRECT`, because it is direct
evidence that the corpus records the relation. The predictive design is what keeps the
edge from being restated as a measurement: it licenses a mechanism hypothesis and nothing
stronger. (An earlier build marked these edges EXTRAPOLATED, which blocked the very
hypothesis they exist to support.)

<!-- zh -->
`predicted_targets` 与 `measured_targets` 是**两个不同的键**。不存在一个合并的 `targets` 列表，因此使用方不可能不小心把一条预测读成一次测量。每一条预测边还携带一个预测性研究设计（`network_prediction`）。其证据被标记为 `directness: DIRECT`，因为它直接证明语料记录了这条关系；让这条边不能被改述为测量的，是预测性研究设计：它只许可机制假说，不许可更强的主张。（早先的版本把这些边标为 EXTRAPOLATED，反而挡住了它们本应支持的假说。）

## Why it cannot make a clinical claim · 为什么它无法做出临床主张

The manifest permits `mechanism_hypothesis` and forbids `mechanism`, `efficacy`,
`association`, `safety_signal`, `recommendation`, `attribution` and
`traditional_use`. The kernel enforces the same thing independently: in its
claim-support table, predictive designs appear in exactly **one** row,
MECHANISM_HYPOTHESIS. A mechanism or clinical claim from this skill is therefore
refused twice — by its own manifest and by the
kernel's type system. Neither refusal depends on the model behaving.

<!-- zh -->
清单（manifest）允许 `mechanism_hypothesis`，禁止 `mechanism`、`efficacy`、`association`、`safety_signal`、`recommendation`、`attribution` 与 `traditional_use`。内核（kernel）独立地强制执行同一件事：在内核的主张–支持表里，预测性研究设计只出现在**一行**，即 MECHANISM_HYPOTHESIS。因此，来自本 Skill 的机制主张或临床主张会被拒绝两次——一次被它自己的清单拒绝，一次被内核的类型系统拒绝。这两道拒绝都不依赖模型是否守规矩。

## Inputs · 输入

```json
{"formula_name": "桂枝汤"}
```

## Outputs · 输出

`network.json` with `ingredients`, `composition_edges`, `predicted_targets`,
`measured_targets`, `pathway_enrichment`, `provenance_summary` and
`randomisation`.

<!-- zh -->
`network.json`，包含 `ingredients`、`composition_edges`、`predicted_targets`、`measured_targets`、`pathway_enrichment`、`provenance_summary` 与 `randomisation`。

## Limits · 局限

Every target edge is a corpus-recorded relation or a network inference; **none is
a binding measurement**. No docking, assay or structure-based prediction was run.
Enrichment is a ranked list of recorded predicates, not a background-corrected
analysis — no network randomisation was performed, so no p-value is reported. The
network is built from the seed corpus only; no external target database
(BindingDB, ChEMBL) was queried.

<!-- zh -->
每一条靶点边，要么是语料库中已记录的关系，要么是一次网络推断；**没有一条是结合测量（binding measurement）**。未运行任何分子对接、实验测定或基于结构的预测。富集分析是一份已记录谓词的排序列表，而不是经过背景校正的分析——没有执行网络随机化，因此不报告 p 值。网络仅由种子语料库（seed corpus）构建；未查询任何外部靶点数据库（BindingDB、ChEMBL）。

A predicted edge is a hypothesis about a mechanism, not evidence that the formula
treats any condition in any patient.

<!-- zh -->
一条预测边，是关于某种机制的假设，而不是该方剂能治疗任何患者任何病症的证据。
