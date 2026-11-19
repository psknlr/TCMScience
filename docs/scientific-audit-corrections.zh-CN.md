# 网络药理与生物信息分析修正记录

本轮复核以提交 `83f27a555e0dfc9e15e28b9d250057b050eaa606` 为对照。下表沿用审核报告编号，便于将问题、改动和验证对应起来。

| 编号 | 会改变分析或解释的修正 | 验证入口 |
| --- | --- | --- |
| 01–02 | 从归档 JSON 分别统计游离暴露比的上下界。旧版 PubChem 九通路案例标记为待复算。另用历史 CI 留下的原始文件哈希重做葛根芩连汤效价分析。两条证据各自报告。 | [小檗碱区间](studies/gqd_berberine.md) · [效价案例重算](studies/gqd-curated/comparison.json) |
| 03–06 | 阈值边界、单位和方向决定效价是否可判定。效价检验考虑每个蛋白被测成分数。PubChem 将检测记录、唯一成分–蛋白对和矛盾结果分别计数，仅在具有可比较的检测与成分组合时检验通路。 | [检测机会与矛盾读数测试](../BioScience-Harness/tests/test_network_assay_opportunities.py) |
| 07–09 | 逐味保留药用部位及其证据，避免同一植物不同部位互相借证。报告区分来源靶标与人类参考集。STRING 与疾病关联各自采用相应来源的映射。 | [药用部位测试](../BioScience-Harness/tests/test_composition_parts.py) · [网络分析测试](../BioScience-Harness/tests/test_network_pharmacology.py) |
| 10–13 | 删除证据后无从检验时保留“不可检验”。敏感性分析检查所用参数，重连按独立成分–靶标边处理。按实际算法报告统计量，保留 BH 精度，分别统计实验、文献与缺引文的记录。 | [研究工作流](../BioScience-Harness/tests/test_research_loop.py) · [统计量及敏感性测试](../BioScience-Harness/tests/test_network_assay_opportunities.py) |
| 14–20 | 零抑制读数、蛋白复合体、药材炮制形式、资料转载和来源引文保留各自的证据含义。Open Targets 区分基因与蛋白映射。空快照需有明确的筛选范围和有效输入。Reactome 的可变下载按内容生成版本。 | [证据归并](../BioScience-Harness/tests/test_tcmdb.py) · [来源解析](../BioScience-Harness/tests/test_source_parsers.py) · [Open Targets](../BioScience-Harness/tests/test_opentargets.py) |
| 21–27 | 排除未收敛拟合和被标为 Cook 离群的基因对推断的影响。单细胞保留供体、条件和批次，将技术重复合并为独立实验单位，检查混杂、共同测量的基因和输出目录。Bulk 检查有效长度处理与逐样本链特异性。 | [组学后端与真实数据](omics-backends.md) · [单细胞输入与设计](../BioScience-Harness/tests/test_omics_study_inputs.py) |
| 28–29 | 对常数、非有限值和无定义统计量给出明确结果。二等位基因工具拒绝无法表示的基因型，并识别 HGVS 编码区上游坐标。 | [原生工具测试](../BioScience-Harness/tests/test_native_tools.py) |
| 30–32 | 未实际测到的通路不能因“消失”增强结论。独立复核需要可追溯且互不重叠的实验；更换随机种子只检验数值稳定性。通路成员关系不能传播激活或抑制方向。 | [研究问询](inquiry.md) · [结论放行测试](../BioScience-Harness/tests/test_release.py) |
| 33–37 | 保留不同剂量、时间、物种及来源的暴露值。联用分析要求细胞毒性对照并处理多重比较。重复研究标识不能充当独立试验，效应差和残余区间纳入相应的不确定性。研究方案锁保护嵌套参数并检查运行输入。 | [研究设计与统计测试](../BioScience-Harness/tests/test_studies.py) |
| 38–41 | 对接位点绑定受体链、模型和配体实例。重放按快照锁恢复来源集合。研究入口区分方剂、对照与疾病，Studio 和命令行支持 PubChem 与指定疾病的 Open Targets 补充输入；导出的富集表可按计数复核。 | [对接测试](../BioScience-Harness/tests/test_docking.py) · [快照工作流](../BioScience-Harness/tests/test_snapshot_pipeline.py) · [使用说明](../USAGE.md) |
| 42 | 治理基准报告原始草稿和派生变体的分母。ADMET 模型卡按适用域分别报告独立留出集数量和性能，分类端点另报 Brier 分数、概率分箱和实际阳性率。 | [治理基准](governance-ablation.md) · [ADMET 模型卡测试](../BioScience-Harness/tests/test_admet_build.py) |

## 怎样解读验证结果

- **葛根芩连汤效价分析。** 17 份原始文件逐一匹配历史 CI 哈希。修正前后的入方成分均为 1,792 个，可进入通路检验的 Reactome 背景从 391 个蛋白变为 382 个，通路从 966 条变为 955 条，两次均无通路通过 BH 阈值。修正后的 369 个效价命中靶标中有 244 个获人类来源确认，其余 125 个为非人源或存在物种记录冲突。该次分析使用 NPASS、CMAUP、LOTUS、Reactome、UniProt 和 STRING。历史 PubChem 九通路结论仍缺当次完整逐检测输入。[效价结果与验证命令](studies/gqd-curated/comparison.json)
- **葛根芩连汤小檗碱暴露。** 归档的大鼠血浆暴露与体外效价比值，在游离比例 1%–100% 的设定下，三组均无读数的下界达到 1。完整方、去甘草方和黄连单用组分别有 1、4 和 15 个读数的上界达到 1。游离比例和靶组织暴露仍未测定。[归档表及来源](studies/gqd_berberine.md)
- **Bulk RNA-seq。** 在 7 个公开 pasilla 样本、同一设计矩阵上，内置后端与 PyDESeq2 的效应量秩相关为 0.99994。两者分别报告 1,284 和 1,119 个 BH q < 0.05 的基因，其中 1,112 个共有且方向相同。这个差异应在解释显著基因清单时保留。[输入、命令和统计口径](omics-backends.md)
- **单细胞。** 从两位健康供体的公开外周血单核细胞数据各取 256 个条形码，454 个细胞通过质控。该验证覆盖计数读取、质控和聚类；样本只有一种健康状态，所以没有疾病差异表达结果。[数据与复验脚本](../BioScience-Harness/scripts/validate_wilk_pbmc.py)
- **ADMET。** 四个端点使用锁定的 TDC 原始归档重训，共检查 584 条留出记录。半衰期预测的 Spearman 相关系数在域内 88 个分子上为 0.6730，在域外 47 个分子上为 0.0307。其余 18 个端点本轮没有重训。[四个端点的结果与复跑命令](analysis-pipelines.md#applicability-domain-results-on-the-official-tdc-test-sets)

治理变体来自同一组草稿，现有结果只描述该基准上的表现。独立盲评草稿、未报告的动物个体数据、人体暴露和临床药物相互作用仍需相应的外部证据。
