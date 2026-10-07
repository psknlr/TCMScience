# Three end-to-end cases

Each case takes a question from inputs to the claims they can carry, through the pieces
connected in this round. It reports every step: its status, the implementation and
version that ran, and what the step produced. It then puts drafted claims through the
claim contract and reports each verdict with its codes.

These are tests of the chain, not studies. The inputs are fixtures, and each report
names them.

```bash
cd BioScience-Harness
pip install -e '.[analysis,literature,docking,admet]'
python scripts/run_end_to_end_cases.py --out RUN/cases                      # all three
python scripts/run_end_to_end_cases.py --out RUN/cases --de-backend pydeseq2
python scripts/run_end_to_end_cases.py --out RUN/cases --only compound \
    --admet-models ~/.cache/bioagent/admet        # after `bioagent admet --build-models`
```

The script writes `report.md` and `report.json` for each case. It exits non-zero if any
claim gets a verdict other than the one the case expects. A step whose implementation is
missing is reported UNAVAILABLE and the case stops there. No other implementation runs in
its place.

The results below were recorded on 2026-10-07, in an environment where every
implementation except Boltz was installed.

## 1. RNA-seq counts → differential expression → preranked GSEA → claims

`bioagent.cases.rnaseq_gsea`

| Step | Implementation | What happened |
| --- | --- | --- |
| differential expression | built-in DESeq2 method, or PyDESeq2 0.5.4 (`--de-backend`) | design `~ batch + condition`, contrast treated vs control. All 80 planted genes are called with their sign: 88 genes at FDR < 0.05 built-in, 86 with PyDESeq2 |
| preranked GSEA | GSEApy 1.3.1 on a GMT snapshot with its SHA-256 | ranked by the Wald statistic. PLANTED_UP has NES 2.70 and PLANTED_DOWN NES −2.86. None of the six untouched sets reaches FDR < 0.25 |
| evidence items | the contract | a gene's fold change becomes an `in_vitro` item. The set's enrichment becomes a `pathway_enrichment` item, which is a computational prediction. Each item is a quote located in the run's own result table |

The input is a simulated experiment with a known answer: 600 genes, 4 control and 4
treated samples in two batches, and two gene sets planted four-fold up and down.

| Drafted claim | Verdict |
| --- | --- |
| In these cells, treatment T raised the expression of a planted gene (mechanism) | allowed |
| Treatment T may act through the PLANTED_UP pathway, a hypothesis for testing (mechanism hypothesis) | allowed |
| Treatment T activates the PLANTED_UP pathway (mechanism, citing the enrichment) | refused, CLM005 |
| … at concentrations reached in patients | refused, CLM017 |
| Treatment T improves outcomes in patients (efficacy) | refused, CLM004 and CLM005 |

## 2. Literature → PaperQA2 → typed evidence items → claims

`bioagent.cases.literature_claims`

| Step | Implementation | What happened |
| --- | --- | --- |
| index the corpus | paper-qa 2026.8.12, local sparse embedding | 3 documents, each read only after its digest matched. No model and no network were used |
| retrieve passages | the same | two lexical questions find 3 passages, each located at its offset |
| type the passages | fixed rules (`bioagent.literature`) | the 葛根芩连汤 trial is typed `randomized_trial` (population 成人2型糖尿病患者, outcome 糖化血红蛋白) and the cohort `observational`. The EMPEROR-Preserved sentence states no design, so it is **withheld**, and nothing is filled in |
| reviewer assessment | a person, stated in the case fixture | the trial's risk of bias is assessed LOW. No rule assesses it, and the case names this as a human input |

The corpus is three abstracts from the governance ablation. Two are fixtures under the
reserved DOI prefix 10.5555 and describe no real study. The third is the EMPEROR-Preserved
result sentence.

| Drafted claim | Verdict |
| --- | --- |
| 葛根芩连汤可降低成人2型糖尿病患者的糖化血红蛋白, before the risk of bias is assessed | refused, CLM006 |
| the same claim, after the reviewer's assessment | allowed |
| … 儿童2型糖尿病患者 (a population the trial did not enrol) | refused, CLM013 and CLM009 |
| … 且未见明显不良反应 (an outcome the abstract does not report, stated as absent) | refused, CLM019 |
| a claim citing the withheld EMPEROR passage | refused, CLM002: it is no evidence item until a person types it |
| Higher serum C-telopeptide was associated with all-cause mortality in adults aged 65 or older (association) | allowed: an unassessed risk of bias blocks efficacy, not association |

## 3. A compound and its target → Open Targets → docking and ADMET → a hypothesis

`bioagent.cases.compound_hypothesis`

| Step | Implementation | What happened |
| --- | --- | --- |
| Open Targets evidence by datatype | a recorded answer (API 26.9.0, data 26.09, recorded 2026-10-07) | the disease is chosen by its *genetic* score: hereditary chronic pancreatitis, genetic association 0.917, literature 0.244. "pancreatitis" is flagged as led by literature co-mention rather than genetics |
| docking | Vina 1.2.7, Meeko 0.8.0, RDKit 2026.3.6, gemmi 0.7.5 | redocking benzamidine into 3PTB reproduces the crystal pose at 0.38 Å, so the setup is validated. 4-aminobenzamidine's top score is −6.41 kcal/mol (Vina's estimate) |
| ADMET | RDKit, scikit-learn | rules and alerts only. No models are built here, and the report says so. With `--admet-models`, the TDC endpoint models built there also predict, each prediction with its applicability domain and its model card's held-out score; no drafted claim cites a prediction, and the verdicts do not change |
| complex prediction | Boltz (boltz-2) | **UNAVAILABLE**: Boltz is not installed, so no model was run and none was approximated |

The question is whether 4-aminobenzamidine is worth testing against trypsin-1 (PRSS1),
whose gain-of-function variants cause hereditary pancreatitis. 3PTB is bovine trypsin, a
model of the human target, and the hypothesis says so.

| Drafted claim | Verdict |
| --- | --- |
| Docking suggests 4-aminobenzamidine may bind the S1 pocket of trypsin (bovine 3PTB, a model of human PRSS1), a hypothesis for an enzyme assay | allowed |
| 4-Aminobenzamidine inhibits human trypsin-1 (mechanism, citing docking) | refused, CLM005: true or not, a docking score does not show it |
| 4-Aminobenzamidine reduces attacks of hereditary chronic pancreatitis | refused, CLM004, CLM005, CLM013 and CLM009 |
| … may bind trypsin at concentrations reached in patients | refused, CLM017 |

The Open Targets association is an aggregate score. It chose the disease, but no claim
may cite it as evidence.

With `--admet-models`, the case was also run on 2026-10-07 against all 22 TDC endpoint
models, built from the pinned archive (scikit-learn 1.9.1, RDKit 2026.3.6, seed 0; every
held-out score equal to the one `docs/analysis-pipelines.md` records). The ADMET step
predicted all 22 and flagged three as outside their applicability domain: hepatocyte and
microsomal clearance and plasma protein binding, where no training molecule reaches a
Tanimoto similarity of 0.3. Among the others, the Ames model gives 0.72; the compound is an
aromatic amine, and the Brenk filters flag its aniline. No drafted claim cites a
prediction, and the four verdicts are the ones above.

## What the cases are not

- **Not studies.** The RNA-seq experiment is simulated, the literature corpus is mostly
  fixtures, and the docking target is a bovine model. Each case tests that the steps run
  as recorded, and that the evidence each step produces licenses what it should and
  nothing more.
- **Not an agent's writing.** No model wrote the drafted claims; each case states them.
  How a model writes under these checks is the
  [four-arm comparison](comparison.md), which has not been run.
- **Not every implementation.** Boltz, Chai-1, ProteinMPNN and OpenMM are not installed
  here, and neither is scVI. The cases record those steps as UNAVAILABLE and do not
  replace them.

Tests: `BioScience-Harness/tests/test_end_to_end_cases.py`. Without the optional
implementations they skip, and under `BIOAGENT_REQUIRE_TOOLS=1` they fail.

## 中文摘要

三个端到端案例，各从输入出发，走完本轮接通的各环节。每一步记录状态和实际运行的实现及版本；最后把起草的主张送入主张契约，记录每条的裁决和代码。它们检验的是链条本身，不是研究，输入都是标明来源的夹具。以下为 2026-10-07 的记录（除 Boltz 外所有实现均已安装）。

**1. RNA-seq：计数 → 差异表达 → 预排序 GSEA → 主张。**
- **输入：** 模拟实验，已知答案（600 个基因，4 对 4，两个批次，两组基因集分别上调、下调 4 倍）。
- **差异表达：** 内置实现或 PyDESeq2 0.5.4，80 个预设基因全部检出且方向正确。
- **GSEA：** GSEApy 1.3.1，基因集文件带 SHA-256；预设集 NES 分别为 2.70 和 −2.86，6 个未处理集均不显著。
- **主张裁决：**
  - 允许：细胞中单个基因的表达变化（机制）；通路富集表述为假说。
  - 拒绝：同一富集写成已确立的机制（CLM005）；外推到人体暴露浓度（CLM017）；外推到患者疗效（CLM004）。

**2. 文献：PaperQA2 → 定位、标注的证据条目 → 主张。**
- **检索：** 本地稀疏嵌入，不联网、不调用模型。
- **标注：**
  - 葛根芩连汤试验标注为随机对照试验，队列标注为观察性研究。
  - EMPEROR 句子未写明研究设计，因此被搁置，不作任何填充。
- **审阅：** 偏倚风险由审阅者评估，作为人工输入写明。
- **主张裁决：**
  - 评估前，疗效主张被拒（CLM006）；评估后同一主张被允许。
  - 推到儿童被拒（CLM013、CLM009）。
  - 添加「未见明显不良反应」被拒（CLM019）。
  - 引用被搁置段落被拒（CLM002）。
  - 队列上的关联主张被允许。

**3. 化合物与靶点：Open Targets → 对接与 ADMET → 假说报告。**
- **Open Targets：** 按遗传证据选择疾病，即遗传性慢性胰腺炎（遗传 0.917，文献 0.244），并标出以文献共现为主的关联。
- **对接：** 先以苯甲脒重对接验证（RMSD 0.38 Å），再对接 4-氨基苯甲脒（−6.41 kcal/mol）。所用结构 3PTB 是牛胰蛋白酶，作为人源靶点的模型，假说中写明。
- **ADMET：** 默认只给出规则与警示；指定 `--admet-models` 时，已构建的 TDC 终点模型同时给出预测，每项附适用域判断与模型卡上的留出集成绩。2026-10-07 用全部 22 个模型（由固定归档构建，留出成绩与文档记录完全一致）运行：22 项均有预测，肝细胞清除率、微粒体清除率与血浆蛋白结合 3 项超出适用域被标出。预测不支撑任何主张，裁决不变。
- **复合物预测：** Boltz 未安装，状态为 UNAVAILABLE，没有任何替代。
- **主张裁决：**
  - 允许：对接假说。
  - 拒绝：同一结果写成机制（CLM005）；写成疗效（CLM004 等）；外推到人体浓度（CLM017）。
