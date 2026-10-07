# Molecular docking · 分子对接

`dock-ligands` · implementation: `bioagent.skills.molecular.docking:dock_ligands` ·
**candidate, not in the stable lockfile**

> This file is documentation; `skill.yaml` is the contract. A governed run refuses this
> skill until a person reviews and promotes it.

<!-- zh -->
> 本文件是说明文档，契约是 `skill.yaml`。在有人审核并晋级之前，受治理运行会拒绝本 Skill。

The skill docks ligands into a receptor with AutoDock Vina:
- the receptor is prepared with meeko's residue templates (polar hydrogens, Gasteiger
  charges, AutoDock types);
- each ligand gets an RDKit conformer and meeko's torsion tree;
- the site is a box around a co-crystal ligand, given residues, or a given centre and
  size.

When the site comes from a co-crystal ligand, that ligand is redocked first. The setup
counts as validated only if the top pose lands within 2 Å of the crystal pose, and only a
validated setup supports a claim: a `mechanism_hypothesis` per ligand, with its row of
`scores.tsv` as evidence. A score is the scoring function's estimate, not an affinity.

<!-- zh -->
本 Skill 用 AutoDock Vina 将配体对接到受体：
- 受体用 meeko 残基模板处理（加极性氢、Gasteiger 电荷、AutoDock 原子类型）；
- 配体由 RDKit 生成三维构象，再由 meeko 生成扭转树；
- 对接盒可以围绕共晶配体、指定残基，或按给定中心与尺寸设定。

若对接位点来自共晶配体，会先把该配体重新对接回去：最优构象与晶体构象的 RMSD 不超过 2 Å 才算验证通过。只有验证通过时才给出结论，即每个配体一条 `mechanism_hypothesis`（机制假说），证据为 `scores.tsv` 中该配体的行。打分只是打分函数的估计，不是亲和力。
