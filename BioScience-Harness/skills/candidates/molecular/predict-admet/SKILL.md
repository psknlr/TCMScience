# ADMET prediction · ADMET 预测

`predict-admet` · implementation: `bioagent.skills.molecular.admet:predict_admet` ·
**candidate, not in the stable lockfile**

> This file is documentation; `skill.yaml` is the contract. A governed run refuses this
> skill until a person reviews and promotes it.

<!-- zh -->
> 本文件是说明文档，契约是 `skill.yaml`。在有人审核并晋级之前，受治理运行会拒绝本 Skill。

For each molecule the skill reports:
- the standardised structure (salts stripped, charges neutralised where they can be);
- physicochemical descriptors;
- the Lipinski, Veber, Egan and Ghose rules;
- PAINS, Brenk and NIH structural alerts;
- when the models are built (`bioagent admet --build-models`), 22 ADMET endpoints:
  - each from a model trained on the Therapeutics Data Commons benchmark group;
  - each with its model card's held-out score on TDC's scaffold split;
  - each with a flag for whether the molecule lies in the model's applicability domain.

None of it is a measurement, so the skill makes no claim.

<!-- zh -->
本 Skill 对每个分子报告：
- 标准化结构：去除盐，可中和的电荷予以中和；
- 理化描述符；
- Lipinski、Veber、Egan、Ghose 规则；
- PAINS、Brenk、NIH 结构警示；
- 模型已构建时（`bioagent admet --build-models`），22 个 ADMET 终点：
  - 每个终点都由在 Therapeutics Data Commons 基准集上训练的模型给出；
  - 每个终点都附有模型卡记录的、在 TDC 骨架划分测试集上的成绩；
  - 每个终点都标明该分子是否在模型的适用域内。

这些都不是实测值，因此本 Skill 不提出任何结论。
