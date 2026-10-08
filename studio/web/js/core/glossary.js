// The governance vocabulary, zh and en, in the words the TCMScience site uses (DESIGN.md §2.3, §6, §7.4).
//
// The English texts of the codes are the strings of the Python tables they come from, so a code reads the same in the
// Studio as in a report (web/test/i18n.test.mjs compares them):
//   ART*      bioagent/contracts/artifact.py::_CODES
//   CLM*      bioagent/contracts/candidate_claim.py::CLAIM_REASONS
//   SKILL*    bioagent/skills/compiler.py::COMPILE_CODES
//   GATE*     bioagent/benchmarks/scorers.py::GATES
//   INQ*      psh/scientist/inquiry.py::INQUIRY_CODES (title, remedy)
// The PSH compiler has no table: EVIDENCE*, EFFECT*, FLOW*, PROTOCOL*, RESOURCE*, RETRY* are its reject(...) messages
// in psh/workflow/compiler.py, with their {placeholders} put into words.
// Tiers, designs and quality values come from bioagent.tcm.model.EvidenceTier (.chinese), bioagent.contracts.
// evidence_item and bioagent.contracts.quality; claim kinds from bioagent.tcm.model.CLAIM_KINDS and
// psh.workflow.ir.ClaimType.

/** EvidenceTier (IntEnum). `family` is the Studio's evidence-colour family (DESIGN §6.1). */
export const TIERS = [
  { rank: 0, id: "computational_prediction", name: "COMPUTATIONAL_PREDICTION", zh: "计算预测", en: "Computational prediction", family: "predicted" },
  { rank: 1, id: "classical_text", name: "CLASSICAL_TEXT", zh: "经典文献记载", en: "Classical text", family: "tradition" },
  { rank: 2, id: "expert_experience", name: "EXPERT_EXPERIENCE", zh: "名医经验/专家共识", en: "Expert experience / consensus", family: "tradition" },
  { rank: 3, id: "preclinical", name: "PRECLINICAL", zh: "临床前研究", en: "Preclinical study", family: "bench" },
  { rank: 4, id: "case_report", name: "CASE_REPORT", zh: "病例报告/病例系列", en: "Case report / series", family: "clinical" },
  { rank: 5, id: "observational", name: "OBSERVATIONAL", zh: "观察性研究", en: "Observational study", family: "clinical" },
  { rank: 6, id: "randomized_trial", name: "RANDOMIZED_TRIAL", zh: "随机对照试验", en: "Randomized controlled trial", family: "clinical" },
  { rank: 7, id: "systematic_review", name: "SYSTEMATIC_REVIEW", zh: "系统评价/荟萃分析", en: "Systematic review / meta-analysis", family: "clinical" },
];

/** Which tiers license which claim kind: a set, not a threshold (bioagent.tcm.model.CLAIM_SUPPORT). */
export const CLAIM_SUPPORT = {
  attribution: [1],
  traditional_use: [1, 2],
  mechanism_hypothesis: [0, 3],
  mechanism: [3],
  safety_signal: [4, 5, 6, 7],
  association: [5, 6, 7],
  efficacy: [6, 7],
  recommendation: [7],
};

export const CLINICAL_CLAIM_KINDS = ["efficacy", "association", "safety_signal", "recommendation"];

export const FAMILIES = {
  // short: 传统, not 经典 — the family holds expert consensus (tier 2) as well as classical texts (tier 1)
  tradition: { zh: "经典与经验", en: "Tradition", short: { zh: "传统", en: "Tradition" } },
  bench: { zh: "临床前", en: "Bench", short: { zh: "临床前", en: "Bench" } },
  clinical: { zh: "临床", en: "Clinical", short: { zh: "临床", en: "Clinical" } },
  predicted: { zh: "计算预测", en: "Predicted", short: { zh: "预测", en: "Predicted" } },
};

/**
 * Study designs, both vocabularies: bioagent EvidenceItem.design (randomized_trial, with a z) and PSH StudyDesign
 * (randomised_trial, with an s; cohort, case_control …). `tier` is the EvidenceTier rank the design maps to.
 */
export const DESIGNS = {
  // bioagent (EVIDENCE_TIER_FOR_DESIGN)
  systematic_review: { zh: "系统评价/荟萃分析", en: "Systematic review", tier: 7 },
  randomized_trial: { zh: "随机对照试验", en: "Randomized trial", tier: 6 },
  observational: { zh: "观察性研究", en: "Observational study", tier: 5 },
  case_report: { zh: "病例报告", en: "Case report", tier: 4 },
  animal: { zh: "动物实验", en: "Animal study", tier: 3 },
  in_vitro: { zh: "体外实验", en: "In vitro study", tier: 3 },
  expert_consensus: { zh: "专家共识", en: "Expert consensus", tier: 2 },
  classical_text: { zh: "经典文献", en: "Classical text", tier: 1 },
  in_silico: { zh: "计算模拟", en: "In silico", tier: 0, predicted: true },
  network_prediction: { zh: "网络预测", en: "Network prediction", tier: 0, predicted: true },
  docking: { zh: "分子对接", en: "Docking", tier: 0, predicted: true },
  molecular_dynamics: { zh: "分子动力学", en: "Molecular dynamics", tier: 0, predicted: true },
  target_prediction: { zh: "靶点预测", en: "Target prediction", tier: 0, predicted: true },
  pathway_enrichment: { zh: "通路富集", en: "Pathway enrichment", tier: 0, predicted: true },
  // PSH StudyDesign (psh.sir.values)
  commentary: { zh: "注家评注", en: "Commentary", tier: 1 },
  case_series: { zh: "病例系列", en: "Case series", tier: 4 },
  cross_sectional: { zh: "横断面研究", en: "Cross-sectional study", tier: 5 },
  case_control: { zh: "病例对照研究", en: "Case-control study", tier: 5 },
  cohort: { zh: "队列研究", en: "Cohort study", tier: 5 },
  non_randomised_trial: { zh: "非随机对照试验", en: "Non-randomised trial", tier: 5 },
  randomised_trial: { zh: "随机对照试验", en: "Randomised trial", tier: 6 },
  guideline: { zh: "临床指南", en: "Guideline", tier: 7 },
  unknown: { zh: "未知设计", en: "Unknown design", tier: null },
};

/** One label set for both claim-kind vocabularies (bioagent CLAIM_KINDS and PSH ClaimType). */
export const CLAIM_KINDS = {
  attribution: { zh: "记载", en: "Attribution" },
  classical_attribution: { zh: "记载", en: "Attribution", canonical: "attribution" },
  traditional_use: { zh: "传统应用", en: "Traditional use" },
  mechanism_hypothesis: { zh: "机制假说", en: "Mechanism hypothesis" },
  mechanism: { zh: "机制", en: "Mechanism" },
  association: { zh: "关联", en: "Association" },
  efficacy: { zh: "疗效", en: "Efficacy" },
  clinical_efficacy: { zh: "疗效", en: "Efficacy", canonical: "efficacy" },
  safety_signal: { zh: "安全信号", en: "Safety signal" },
  recommendation: { zh: "推荐", en: "Recommendation" },
};

/** Verdict and state vocabularies, grouped by where they come from. */
export const STATES = {
  release: {
    schema_valid: { zh: "结构有效", en: "Schema valid" },
    evidence_verified: { zh: "引文已核验", en: "Evidence verified" },
    outputs_verified: { zh: "输出已核验", en: "Outputs verified" },
    execution_declared: { zh: "已声明执行", en: "Execution declared" },
    execution_attested: { zh: "执行已证明", en: "Execution attested" },
    release_authorized: { zh: "准予发布", en: "Release authorized" },
  },
  release_summary: {
    released: { zh: "已准予发布", en: "Release authorized" },
    consistent: { zh: "内部一致 · 未核验", en: "Consistent · not verified" },
    refused: { zh: "已拒绝", en: "Refused" },
  },
  artifact: {
    draft: { zh: "草稿", en: "Draft" },
    validated: { zh: "已验证", en: "Validated" },
    refused: { zh: "已拒绝", en: "Refused" },
    experimental: { zh: "实验性", en: "Experimental" },
  },
  claim: {
    allowed: { zh: "允许", en: "Allowed" },
    allowed_with_caveats: { zh: "允许 · 附说明", en: "Allowed · with caveats" },
    refused: { zh: "已拒绝", en: "Refused" },
    needs_declaration: { zh: "需声明外推", en: "Needs a declared limit" },
    prediction_as_fact: { zh: "预测被当作事实", en: "Prediction stated as fact" },
    unchecked: { zh: "未判定", en: "Not checked" },
  },
  inquiry: {
    accepted: { zh: "已接受", en: "Accepted" },
    provisional: { zh: "暂定", en: "Provisional" },
    undetermined: { zh: "未决", en: "Undetermined" },
    needs_hypotheses: { zh: "需补充假说", en: "Needs hypotheses" },
  },
  goal: {
    verified: { zh: "目标已核验", en: "Goal verified" },
    pending_manual: { zh: "待人工判定", en: "Pending manual review" },
    unverified: { zh: "目标未核验", en: "Goal not verified" },
  },
  call: {
    succeeded: { zh: "完成", en: "Succeeded" },
    failed: { zh: "未完成", en: "Failed" },
    refused: { zh: "已拒绝", en: "Refused" },
    needs_approval: { zh: "待批准", en: "Needs approval" },
    job_submitted: { zh: "已提交任务", en: "Job submitted" },
    cancelled: { zh: "已取消", en: "Cancelled" },
    running: { zh: "运行中", en: "Running" },
  },
  job: {
    queued: { zh: "排队中", en: "Queued" },
    running: { zh: "运行中", en: "Running" },
    // the runner checked each output's hash at collection; that is not a release (the kernel's verdict says that)
    succeeded: { zh: "已完成（输出哈希已核验）", en: "Finished (output hashes verified)" },
    failed: { zh: "未完成", en: "Failed" },
    cancelled: { zh: "已取消", en: "Cancelled" },
  },
  retraction: {
    not_retracted: { zh: "未撤稿", en: "Not retracted" },
    retracted: { zh: "已撤稿", en: "Retracted" },
    expression_of_concern: { zh: "关注声明", en: "Expression of concern" },
    unverified: { zh: "未核查撤稿", en: "Retraction not checked" },
  },
  licensing: {
    direct: { zh: "直接支撑", en: "Direct" },
    extrapolated: { zh: "外推支撑", en: "Extrapolated" },
    unlicensed: { zh: "不支撑", en: "Unlicensed" },
  },
};

/** EvidenceQuality: four dimensions, no aggregate. JSON carries the name (document()) or the integer (the enum). */
export const QUALITY = {
  risk_of_bias: {
    label: { zh: "偏倚风险", en: "Risk of bias" },
    values: [
      ["low", "低偏倚风险", "Low risk of bias"],
      ["some_concerns", "存在一定问题", "Some concerns"],
      ["high", "高偏倚风险", "High risk of bias"],
      ["critical", "严重偏倚风险", "Critical risk of bias"],
      ["not_assessed", "未评估", "Not assessed"],
    ],
  },
  directness: {
    label: { zh: "直接性", en: "Directness" },
    values: [
      ["direct", "直接证据", "Direct"],
      ["partial", "替代终点", "Partial (surrogate)"],
      ["extrapolated", "外推", "Extrapolated"],
      ["not_assessed", "未评估", "Not assessed"],
    ],
  },
  precision: {
    label: { zh: "精确性", en: "Precision" },
    values: [
      ["precise", "精确", "Precise"],
      ["imprecise", "不精确", "Imprecise"],
      ["not_assessed", "未评估", "Not assessed"],
    ],
  },
  consistency: {
    label: { zh: "一致性", en: "Consistency" },
    values: [
      ["consistent", "一致", "Consistent"],
      ["inconsistent", "不一致", "Inconsistent"],
      ["single_study", "单一研究", "Single study"],
      ["not_assessed", "未评估", "Not assessed"],
    ],
  },
};

/** The 13 catalog categories (CONTRACTS §2), in order. */
export const CATEGORIES = [
  { id: "tcm_knowledge", zh: "中医知识", en: "TCM knowledge" },
  { id: "tcm_safety", zh: "安全与配伍", en: "Safety & compatibility" },
  { id: "clinic", zh: "临床辨证", en: "Clinical differentiation" },
  { id: "tcm_data", zh: "中医药数据枢纽", en: "TCM data hub" },
  { id: "live_sources", zh: "在线数据源", en: "Live data sources" },
  { id: "netpharm", zh: "网络药理与研究闭环", en: "Network pharmacology & research loop" },
  { id: "study_design", zh: "统计与研究设计", en: "Statistics & study design" },
  { id: "clinical_calc", zh: "临床计算与药理", en: "Clinical calculators & pharmacology" },
  { id: "seq_genomics", zh: "序列与基因组", en: "Sequences & genomics" },
  { id: "structure_molecules", zh: "结构与分子", en: "Structure & molecules" },
  { id: "omics", zh: "组学流程", en: "Omics pipelines" },
  { id: "literature", zh: "文献证据", en: "Literature & evidence" },
  { id: "system", zh: "审计与环境", en: "Provenance & environment" },
];

/**
 * Refusal and violation codes: {en, zh, remedy?: {en, zh}}. Short, neutral: a refusal is a result, not a fault.
 */
export const CODES = {
  // artifact layer — bioagent/contracts/artifact.py::_CODES
  ART101: { en: "artifact declares no sources", zh: "产物未声明任何数据源" },
  ART102: { en: "a publishable claim rests on a source that is not pinned to a snapshot", zh: "可发布的主张依赖未固定到快照的数据源" },
  ART103: { en: "a publishable claim rests on a source with no identified licence", zh: "可发布的主张依赖未标明许可的数据源" },
  ART104: { en: "a claim cites evidence id that is not present in the artifact", zh: "主张引用的证据编号不在产物中" },
  ART105: { en: "a claim is not supported by its evidence", zh: "主张得不到其证据的支撑" },
  ART106: { en: "a clinical claim is supported only by computational prediction", zh: "临床主张仅由计算预测支撑" },
  ART107: { en: "a declared output file has no content hash", zh: "声明的输出文件没有内容哈希" },
  ART108: { en: "composite_version is incomplete", zh: "复合版本不完整" },
  ART109: { en: "a claim cites retracted evidence", zh: "主张引用了已撤稿的证据" },
  ART110: { en: "no limitations are stated", zh: "未声明局限" },
  ART111: { en: "cited evidence names no source card, or one that is not in the artifact", zh: "所引证据未注明数据源卡片，或该卡片不在产物中" },
  ART112: { en: "a claim declares a confidence basis but evidence quality is unassessed", zh: "主张声明了置信依据，但证据质量未评估" },
  ART113: { en: "a declared output file is not present where the artifact says it is", zh: "声明的输出文件不在产物所称的位置" },
  ART114: { en: "a declared output file does not match its content hash", zh: "声明的输出文件与其内容哈希不符" },
  ART115: { en: "a quote receipt does not verify against the content it names", zh: "引文回执与其所指内容核对不上" },
  ART116: { en: "a claim rests on an extrapolation that is declared but not validated", zh: "主张依赖已声明、但未经证据验证的外推" },
  ART117: { en: "the artifact declares an attestation that its audit chain does not record", zh: "产物声明的执行证明未记录在其审计链中" },
  ART118: { en: "an operation of the run was refused, failed, or is not in its audit chain", zh: "本次运行有操作被拒绝、未完成或未记入审计链" },

  // claim layer — bioagent/contracts/candidate_claim.py::CLAIM_REASONS
  CLM001: { en: "claim cites no evidence", zh: "主张未引用任何证据" },
  CLM002: { en: "a cited evidence item is not present", zh: "所引的某条证据不存在" },
  CLM003: { en: "all supporting evidence is retracted", zh: "支撑证据均已撤稿" },
  CLM004: { en: "a clinical claim rests only on computational prediction", zh: "临床主张仅依赖计算预测（预测被当作事实）" },
  CLM005: { en: "evidence tier is below the floor for this claim kind", zh: "证据等级不能支撑该主张类型" },
  CLM006: { en: "evidence quality blocks this claim", zh: "证据质量不足以支撑该主张" },
  CLM007: { en: "a normative claim does not use claim_kind 'recommendation'", zh: "规范性主张未使用「推荐」类型" },
  CLM008: { en: "cross-species extrapolation is not declared", zh: "跨物种外推未声明" },
  CLM009: { en: "an extrapolation beyond the evidence is not declared", zh: "超出证据范围的外推未声明" },
  CLM010: { en: "a supporting quote was not located in its source", zh: "支撑引文未在其来源中找到" },
  CLM011: { en: "the claim's wording asserts more than its declared claim_kind", zh: "措辞超出所声明的主张类型" },
  CLM012: { en: "an extrapolation is marked validated by evidence that is not present", zh: "外推被标为已验证，但验证它的证据不存在" },
  CLM013: { en: "the claim's stated evidence scope is not what its cited evidence covers", zh: "主张所称的证据范围与所引证据不符" },
  CLM014: { en: "the claim names a drug by a near name of the one its evidence studied", zh: "主张所称药物与证据所研究的药物仅名称相近" },
  CLM015: { en: "the claim is about a formula and its evidence about a constituent, or the reverse", zh: "主张针对方剂而证据针对其成分，或者相反" },
  CLM016: { en: "the claim names a processing state other than the one its evidence studied", zh: "主张所称炮制品与证据所研究的不同" },
  CLM017: { en: "the claim asserts relevance at human exposure that no cited evidence measured", zh: "主张断言人体暴露水平下的相关性，但所引证据均未测量" },
  CLM018: { en: "the claim counts more independent sources than its evidence holds", zh: "主张所称的独立来源多于证据实际所有" },
  CLM019: { en: "the claim states an absence (no effect, no harm, no relation) that no cited evidence tested", zh: "主张断言「没有」（无效、无害、无关联），但所引证据均未检验" },

  // skill compiler — bioagent/skills/compiler.py::COMPILE_CODES
  SKILL101: { en: "skill requests authority the run envelope does not hold", zh: "Skill 申请的权限超出本次运行所持有的" },
  SKILL102: { en: "skill declares a claim kind its own evidence policy forbids", zh: "Skill 声明了其自身证据策略禁止的主张类型" },
  SKILL103: { en: "skill declares no outputs to compile", zh: "Skill 未声明可编译的输出" },
  SKILL104: { en: "skill reaches the network but declares no hosts", zh: "Skill 访问网络但未声明主机" },
  SKILL105: { en: "skill's license does not permit its declared integration mode", zh: "Skill 的许可不允许其声明的集成方式" },
  SKILL106: { en: "skill requires secrets, which the compiler cannot grant", zh: "Skill 需要密钥，编译器不能授予" },
  SKILL107: { en: "skill mutates but requests a non-mutating autonomy", zh: "Skill 会修改状态，却申请了不修改状态的自主级别" },

  // benchmark gates — bioagent/benchmarks/scorers.py::GATES
  GATE001: { en: "severe safety false-negative rate above threshold", zh: "严重安全性漏报率超过阈值" },
  GATE002: { en: "fabricated citation rate is not zero", zh: "虚构引文率不为零" },
  GATE003: { en: "no re-runnable artifact was produced", zh: "未产生可重跑的产物" },
  GATE004: { en: "a clinical efficacy conclusion rested on prediction-only evidence", zh: "临床疗效结论仅依赖预测性证据" },

  // PSH scientific compiler — psh/workflow/compiler.py (refused before anything runs)
  EVIDENCE101: { en: "evidence source must be a direct dependency", zh: "证据来源必须是直接依赖" },
  EVIDENCE102: { en: "referenced task declares no evidence", zh: "所引用的任务未声明证据" },
  EVIDENCE103: { en: "the evidence designs cannot license this claim kind", zh: "证据的研究设计不能支撑该主张类型" },
  EVIDENCE104: { en: "evidence and claim differ in population, intervention or outcome", zh: "证据与主张在人群、干预或结局上不一致" },
  EFFECT101: { en: "scientific tasks must explicitly declare destinations", zh: "科学任务必须明确声明数据去向" },
  EFFECT102: { en: "scientific tasks must explicitly declare effects", zh: "科学任务必须明确声明效应" },
  EFFECT103: { en: "effects and execution destinations must agree", zh: "声明的效应与执行去向必须一致" },
  EFFECT104: { en: "model task requires a model effect", zh: "模型任务必须声明模型效应" },
  EFFECT105: { en: "a pure task cannot declare externally visible effects", zh: "纯计算任务不能声明对外可见的效应" },
  EFFECT106: { en: "repeat-safe declaration requires trusted manifest idempotency", zh: "声明可安全重复执行，需要受信清单证明其幂等" },
  FLOW101: { en: "derived sensitivity exceeds the task/run ceiling", zh: "派生数据的敏感级别超过任务或运行的上限" },
  FLOW102: { en: "derived data may not reach this destination", zh: "派生数据不得流向该去向" },
  PROTOCOL101: { en: "bound task requires a statistical design", zh: "绑定方案的任务需要统计设计" },
  PROTOCOL102: { en: "bound task requires a scientific ledger", zh: "绑定方案的任务需要科学账本" },
  PROTOCOL103: { en: "registered protocol unavailable, invalid or unauthorized", zh: "注册的方案不可用、无效或未获授权" },
  PROTOCOL104: { en: "registered protocol fingerprint differs from binding", zh: "注册方案的指纹与绑定不符" },
  PROTOCOL105: { en: "analysis protocol differs from registered protocol", zh: "分析方案与注册方案不符" },
  RESOURCE101: { en: "resource estimates cannot be negative", zh: "资源估计不能为负" },
  RETRY101: { en: "this side-effect class does not permit automatic retries", zh: "该副作用类别不允许自动重试" },
  RETRY102: { en: "automatic tool retries require trusted manifest idempotency", zh: "自动重试工具调用需要受信清单证明其幂等" },

  // inquiry engine — psh/scientist/inquiry.py::INQUIRY_CODES (title + the remedy that repairs it)
  INQ101: {
    en: "the world is closed", zh: "世界被封闭：没有给未命名的解释留出先验",
    remedy: { en: "leave prior mass for an explanation nobody has named: lower the named priors until the catch-all keeps at least the rule's floor", zh: "为尚未命名的解释保留先验：降低具名解释的先验，直到兜底解释至少保有规则规定的下限" },
  },
  INQ102: {
    en: "fewer than two named explanations", zh: "具名解释少于两个",
    remedy: { en: "name at least two explanations; one hypothesis and its absence is a test, not a comparison", zh: "至少给出两个解释；一个假说与它的否定只是检验，不是比较" },
  },
  INQ103: {
    en: "malformed explanation", zh: "解释的格式不正确",
    remedy: { en: "give each explanation a unique id, a proposition, a role, a prior in (0, 1) and, when it asserts something about the world, a claim kind", zh: "为每个解释给出唯一编号、命题、角色、(0, 1) 之间的先验；断言现实时还要给出主张类型" },
  },
  INQ104: {
    en: "malformed analysis", zh: "分析的格式不正确",
    remedy: { en: "give each analysis a unique id, a title, a study design, at least two distinct outcomes and a positive cost; a replicate names an analysis with the same outcomes", zh: "为每项分析给出唯一编号、标题、研究设计、至少两个不同结果和正的成本；重复分析须指向结果相同的分析" },
  },
  INQ105: {
    en: "malformed stopping rule", zh: "停止规则的格式不正确",
    remedy: { en: "accept_at in (0.5, 1), min_severity in (0, 1), 0 < catch_all_floor < catch_all_alarm <= 1, min_gain >= 0, a positive budget or none, max_steps >= 1", zh: "accept_at ∈ (0.5, 1)，min_severity ∈ (0, 1)，0 < catch_all_floor < catch_all_alarm ≤ 1，min_gain ≥ 0，预算为正或不设，max_steps ≥ 1" },
  },
  INQ110: {
    en: "the analysis has no sealed prediction for an explanation it bears on", zh: "分析缺少对其相关解释的密封预测",
    remedy: { en: "declare, before running it, what every live explanation it bears on predicts it will show", zh: "在运行前，声明每个相关解释预测它会显示什么" },
  },
  INQ111: {
    en: "unknown analysis or explanation", zh: "未知的分析或解释",
    remedy: { en: "use an id the inquiry has recorded", zh: "使用探究中已记录的编号" },
  },
  INQ112: {
    en: "a prediction is not a probability distribution over the outcomes", zh: "预测不是各结果上的概率分布",
    remedy: { en: "give one finite probability per declared outcome, summing to 1", zh: "为每个声明的结果给出一个有限概率，总和为 1" },
  },
  INQ113: {
    en: "the analysis's design cannot license this explanation's claim kind", zh: "该分析的研究设计不能支撑此解释的主张类型",
    remedy: { en: "remove the prediction: the engine holds this explanation fixed through the analysis; a design that can license the claim is needed to move it", zh: "移除该预测：引擎在本次分析中保持该解释不变；要改变它，需要能支撑该主张的研究设计" },
  },
  INQ114: {
    en: "a prediction was declared for the catch-all", zh: "为兜底解释声明了预测",
    remedy: { en: "remove it; the catch-all's prediction is fixed (uniform) by the engine", zh: "移除它；兜底解释的预测由引擎固定为均匀分布" },
  },
  INQ115: {
    en: "a prediction was declared after the outcome", zh: "在结果出现之后才声明预测",
    remedy: { en: "an outcome already observed cannot be predicted; declare predictions for analyses not yet run", zh: "已观察到的结果不能再预测；请为尚未运行的分析声明预测" },
  },
  INQ116: {
    en: "a sealed prediction cannot be changed", zh: "已密封的预测不可更改",
    remedy: { en: "add a new analysis if the plan changed; the sealed prediction stands", zh: "计划改变时请新增分析；已密封的预测保持不变" },
  },
  INQ130: {
    en: "the outcome is not one the analysis declared", zh: "结果不在该分析声明的结果之中",
    remedy: { en: "classify the result into one of the declared outcomes; an outcome nobody predicted cannot update a belief", zh: "把结果归入声明的结果之一；无人预测过的结果不能更新信念" },
  },
  INQ131: {
    en: "the analysis was already observed", zh: "该分析已经观察过",
    remedy: { en: "record a replicate as its own analysis, naming this one in replicates", zh: "把重复实验记为单独的分析，并在 replicates 中指向本分析" },
  },
  INQ132: {
    en: "the inquiry's budget or step limit would be exceeded", zh: "将超出探究的预算或步数上限",
    remedy: { en: "conclude with what is known, or open a new inquiry with a declared larger budget", zh: "依据已知结果作结，或以声明的更大预算开启新的探究" },
  },
  INQ133: {
    en: "the analysis was withdrawn, or cannot be", zh: "该分析已撤回，或不能撤回",
    remedy: { en: "a withdrawn analysis is never run; an observed one stays observed. Withdraw with a stated reason before the analysis has an outcome", zh: "已撤回的分析不会运行，已观察的分析保持已观察；须在出结果前注明理由撤回" },
  },
  INQ140: {
    en: "the new explanations cannot be admitted", zh: "新解释不能纳入",
    remedy: { en: "admit new explanations with shares of the catch-all's current mass that leave it at least the rule's floor of that mass", zh: "从兜底解释当前的概率中分出份额纳入新解释，且兜底解释仍须保有规则规定的下限" },
  },
  INQ150: {
    en: "the inquiry does not verify", zh: "探究无法核验",
    remedy: { en: "the trail was altered, or a recorded belief does not follow from the sealed predictions and outcomes; reload from an unaltered trail", zh: "记录被改动，或某个记录的信念不能由密封预测与结果推出；请从未改动的记录重新载入" },
  },

  // Studio — a Skill no lockfile pins (the runner's receipt says skill_pinned: false): it runs as a candidate
  UNPINNED: {
    en: "Candidate Skill: no lockfile pins it, so the run is recorded but its result is not released", zh: "候选 Skill：未被锁定文件固定，运行会记录但结果不会发布",
    remedy: { en: "the maintainers pin a reviewed version in the registry lockfile; until then its results stay unreleased candidates", zh: "由维护者在注册表锁定文件中固定经审核的版本；在此之前，其结果只是未发布的候选结果" },
  },

  // Studio — the acts reserved for a person (tcmstudio.catalog.NEVER_OFFERED), refused by name in the page and the runner
  HUMAN_ONLY: {
    en: "an act reserved for a person, never a tool call", zh: "只能由人完成的操作，不是工具调用",
    remedy: { en: "a person does this, not the model: a licensed practitioner signs a clinic draft; the maintainers review a release", zh: "此操作由人完成，不由模型调用：临床草案由执业医师本人签署，发布由维护者审核" },
  },
};

/** Code families, for grouping in the UI and a fallback explanation of an unknown code. */
export const CODE_FAMILIES = {
  ART: { zh: "产物校验", en: "Artifact check" },
  CLM: { zh: "主张校验", en: "Claim check" },
  INQ: { zh: "探究引擎", en: "Inquiry engine" },
  EVIDENCE: { zh: "编译期证据检查", en: "Compile-time evidence check" },
  SKILL: { zh: "Skill 编译", en: "Skill compiler" },
  GATE: { zh: "基准门槛", en: "Benchmark gate" },
  EFFECT: { zh: "编译期效应检查", en: "Compile-time effect check" },
  FLOW: { zh: "数据流向检查", en: "Data-flow check" },
  PROTOCOL: { zh: "方案检查", en: "Protocol check" },
  RESOURCE: { zh: "资源检查", en: "Resource check" },
  RETRY: { zh: "重试检查", en: "Retry check" },
};

// ------------------------------------------------------------------------------------------------- lookups

const TIER_BY = new Map();
for (const t of TIERS) {
  TIER_BY.set(t.rank, t);
  TIER_BY.set(String(t.rank), t);
  TIER_BY.set(t.id, t);
  TIER_BY.set(t.name, t);
  TIER_BY.set(t.name.toLowerCase(), t);
}

/**
 * The tier record for a rank (0–7), an enum name (EXPERT_EXPERIENCE), a document() tier id (expert_experience), or a
 * study design of either vocabulary (in_vitro → preclinical). null when unknown.
 */
export function tierOf(x) {
  if (x === null || x === undefined || x === "") return null;
  if (typeof x === "object") return tierOf(x.tier ?? x.tier_rank ?? x.design);
  const direct = TIER_BY.get(x) || TIER_BY.get(String(x).trim().toLowerCase()) || TIER_BY.get(String(x).trim().toUpperCase());
  if (direct) return direct;
  const d = DESIGNS[String(x).trim().toLowerCase()];
  return d && d.tier !== null ? TIER_BY.get(d.tier) : null;
}

export function codeFamily(code) {
  const m = /^([A-Z]+)\d+$/.exec(String(code || "").trim());
  return m ? m[1] : null;
}

/** {code, family, en, zh, remedy?, known} for any code; an unknown code is described by its family. */
export function codeInfo(code) {
  const id = String(code || "").trim().toUpperCase();
  const known = CODES[id];
  const fam = codeFamily(id);
  if (known) return { code: id, family: fam, known: true, ...known };
  const famInfo = CODE_FAMILIES[fam];
  return {
    code: id, family: fam, known: false,
    en: famInfo ? `${famInfo.en} (${id})` : id,
    zh: famInfo ? `${famInfo.zh}（${id}）` : id,
  };
}

export function claimKindInfo(kind) {
  const k = String(kind || "").trim().toLowerCase();
  const info = CLAIM_KINDS[k];
  if (!info) return null;
  const canonical = info.canonical || k;
  return { id: k, canonical, zh: info.zh, en: info.en, licensedBy: CLAIM_SUPPORT[canonical] || [], clinical: CLINICAL_CLAIM_KINDS.includes(canonical) };
}

export function qualityLabel(dimension, value) {
  const dim = QUALITY[dimension];
  if (!dim) return null;
  let row;
  if (typeof value === "number") row = dim.values[value];
  else row = dim.values.find(([id]) => id === String(value || "").toLowerCase());
  if (!row) return null;
  return { id: row[0], zh: row[1], en: row[2], assessed: row[0] !== "not_assessed" };
}
