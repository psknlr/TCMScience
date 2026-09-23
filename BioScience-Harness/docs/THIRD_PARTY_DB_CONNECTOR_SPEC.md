# 第三方公开数据库接入方案 v2

> 本文件取代 v1（commit `c647588`，经 PR #18 合并）。v1 以"在线查询接口"为中心；v2 改为
> **快照优先、证据两轴、Skill 只读数据**，并吸收了一份外部"Skill 化路线"方案中正确的部分。
> 文中关于仓库现状的描述均于 2026-09-23 对照代码核实过，外部事实附来源（见末尾"参考"）。

---

## 0. 结论

**对外部方案的判断**：方向正确。它主张把计算预测和实验证据分开、以快照为准、保留记录级许可证、不做规避限制的抓取，这些都采纳。但它有四个问题：

- 它是一份"科研 Skill 平台总路线"，数据接入只是其中一部分；
- 提议新建的 8 类数据对象，大多数在 PSH 里已有等价实现；
- 把数据源白名单写在 agent 可写的 Skill 目录里，存在自我授权的漏洞；
- 首批数据源遗漏了仓库里已有、许可清晰的天然产物实验数据（LOTUS / NPASS / CMAUP）。

**对 v1 规范的修正**：v1 把"算法预测"映射到 `PRECLINICAL`，这是错的；v1 把网页抓取当作常规通道，这需要收紧。

**v2 的三条核心设计**：

1. **快照优先**：分析只读取经过质量检查、带哈希的本地快照；在线 API 只用于 ID 解析和单条补查。
2. **证据两轴**：第一轴是这条断言怎么产生的（Biolink `knowledge_level` + `agent_type`），第二轴是背后是什么研究（研究设计 → `EvidenceTier`）。纯预测最多只能支持"机制假说"。
3. **Skill 只读数据、权限只能收窄**：Skill 声明它要用的快照版本；实际生效的数据源取 Skill 声明、源注册表、权限配置三者的交集。

**交付节奏**：三个里程碑（契约与修正 → 数据快照 → 首个端到端 Skill），见 §4。组学分析和临床方法学不在本方案范围内。

---

## 1. 现状核查：仓库里已经有什么

| 能力 | 已有（已核实） | 缺口 |
| --- | --- | --- |
| 在线接口 | 58 个源、153 个声明式操作（`PublicSource` / `Operation`）。2026-09-17 全部实测通过，记录在 `data/connector_live_verification.csv`。PubChem、ChEMBL、UniProt、HGNC、Open Targets、GWAS Catalog、Monarch、STRING、OmniPath、Reactome、g:Profiler、PANTHER、Europe PMC、PubTator、ClinicalTrials.gov、openFDA、DailyMed、MeSH、Wikidata 等均已接入 | 中医药库一个都没有。编写下载表时，HERB 的文件链接返回的是 HTML，BATMAN-TCM 下载页返回 503，TCMSP/HIT/TCMBank 无响应，因此被有意排除。BindingDB、ICD-11、ChiCTR 也没有 |
| 批量下载 | 24 个 `AcquisitionSpec`：NPASS ×6、CMAUP ×5、LOTUS ×2（2026-04-13 冻结版，md5 已锁定）、NP Atlas、STRING、Reactome、ChEMBL 映射等 | **下载之后没有任何代码解析这些文件**；`default_runtime` 不加载 `BulkDatasetProvider`；24 个文件中只有 5 个锁定了校验值 |
| 传输层 | `HTTPBackend`：逐主机限速、429/5xx 指数退避、磁盘缓存、64 MiB 上限、未声明主机一律 `DENIED` | 不读 `Retry-After`；缓存默认关闭、没有 TTL、缓存键不含数据版本；重试耗尽后报 `FAILED`；文本超过 20 万字符会被截断，此时只在返回值里带一个 `truncated` 标志，状态仍是 `SUCCEEDED` |
| 溯源 | 调用级：`Event` 记录输入输出哈希和 `dataset_hash`，`ProvenanceEntry.licenses` 记录许可证；下载文件有 `.downloads.json` | 没有记录级的许可证、数据版本和抓取时间。`ProvenanceCapsule` 有 `random_seed`、`dataset_hashes`、`container_digest` 字段，但**从未被填写** |
| 科研记录 | `psh.scientist`：`Hypothesis`、带指纹的 `Protocol`、`Observation`、`Deviation`；`ScientificClaim` + `ValidationStatus.CANDIDATE`；可签名（HMAC）的 `EvidenceRecord`；`EvidenceSpec`（研究设计、人群、干预、结局） | 基本完备 |
| 编译与发布 | `ScientificCompiler` 把 `ScientificProgram`（JSON）编译成 `Plan`；`_SUPPORTS` 按研究设计限制可支持的主张类型；`OutputGate` / `Finalizer` 负责发布；`RunEnvelope.allowed_capabilities` 提供白名单 | 研究设计词表 `DESIGNS` **没有 `in_silico`**，计算预测无处登记；没有偏倚风险评价，也没有 GRADE 分级 |
| TCM 证据分级 | `EvidenceTier` 共 7 级，配合 `CLAIM_KINDS` | 有两个问题。一是 `PRECLINICAL` 把网络药理学、分子对接和体外/动物实验混在一起。二是 `applicability` 按大小比较（`relation.tier < required`），所以纯预测不仅能支持 `mechanism`，还能支持 `traditional_use` 和 `attribution` 主张。PSH 编译器则是按集合判断的，机制主张只认 `in_vitro` / `animal`，两层不一致 |
| 分析工具 | `tools/stats.py` 有超几何检验、Fisher 检验和富集分析；`tools/tcm.py` 有 8 个 TCM 工具 | 没有"药材→成分→靶点"、ADME、网络拓扑类工具；`tools/pharmacology.py` 只做药代和剂量计算 |

### 1.1 有没有专门存放 Skill 的区域？

**仓库源码里没有，运行时工作区里有，但两者都还没有接到执行链路上。**

- **仓库**：全仓库没有任何 `SKILL.md`，也没有 `skills/` 目录。`providers/skills.py` 里的 `SkillDirectoryProvider` 能把 `SKILL.md` 的元数据读成组件清单，但它定义了却**从未被实例化**，后端也固定为 `none`。能力目录里的 405 条 `skill` 行来自上游项目（K-Dense、ClawBio、PantheonOS），同样无法执行，调用时会报 `UNAVAILABLE`。
- **运行时工作区**：`workspace/filesystem.py` 的 `Workspace.init()` 会创建 `skills/`，与 `agents/`、`workflows/`、`memory/`、`artifacts/`、`runs/`、`data/` 同属 **agent 可写区**，可以用 `GitLayer` 做版本管理；`kernel/`、`policies/`、`audit/` 等属于不可写区。agent 对 Skill 的修改应当走 `EvolutionPipeline`（提议 → 边界检查 → 测试 → 基准 → 策略 → 晋升）。
- **结论**：随项目发布、经过评审的 Skill 放在仓库的 `BioScience-Harness/skills/`（新建）；agent 运行中起草的 Skill 放在工作区 `skills/`。**数据源白名单两处都不能放**，见 §3.8。

---

## 2. 评估

### 2.1 外部方案逐项评估

| 外部方案的主张 | 结论 | 理由或修改 |
| --- | --- | --- |
| 网络药理预测不是 `PRECLINICAL`，应单列为计算预测 | **采纳** | 代码核实属实：BioScience 的 `EvidenceTier` 确有此问题，v1 规范也犯了同样的错。而且由于按大小比较，问题比外部方案说的更大，见 §1。修正见 §3.5 |
| 实测靶点与预测靶点分开 | **采纳，改实现方式** | 不用"分两列"，而是同一个谓词加证据列区分，避免谓词越分越多 |
| 快照、记录级许可证、原始记录 ID | **采纳** | 这正是现有代码最缺的部分，见 §1 |
| 新增 BindingDB | **采纳** | 已核实：每月更新 TSV 下载并附 md5；BindingDB 自行整理的数据为 CC BY 4.0，转录自 ChEMBL 的数据为 CC BY-SA 3.0，需记录级许可证 |
| 默认不用 GeneCards、DrugBank、《中国药典》全文；"逆向接口"改称"适配器" | **采纳** | 落实为 `manual` 和默认关闭的 `web` 两种访问方式，见 §3.3 |
| 方剂要绑定具体组成版本；用固定案例打通全链路 | **采纳** | 组成版本用 `Protocol` 的指纹锁定；案例取"葛根芩连汤—2 型糖尿病" |
| 新建 8 类数据对象（SourceCard 等） | **修改** | 其中 5 类已有等价实现：`ProtocolRecord`≈`Protocol`；`RunRecord`≈`Event` / `OperationRecord` / `ProvenanceCapsule`；`EvidenceItem`≈`EvidenceRecord` / `EvidenceSpec` / `StudyEvidence`；`CandidateClaim`≈`ScientificClaim` + `CANDIDATE`；`ResearchArtifact`≈`ReleasedResult` / `ArtifactRef`（后者已定义但还没有地方用到）。另外 `QualityAssessment` 部分有（`ClaimSupport`），`EntityMapping` 只有 TCM 名称解析（`resolve`）。**真正要新增的只有三样**：数据源卡片（只管治理信息）、快照 manifest、节点/边表 |
| 新建 Skill Compiler | **修改** | 编译器已经有了（`ScientificCompiler`，输入是 JSON）。只需写一个把 `skill.yaml` 转成 `ScientificProgram` 的薄适配器 |
| 白名单写在 `skill.yaml` 的 `allowed_components` 里 | **修改（安全问题）** | Skill 目录是 agent 可写区，白名单只写在那里等于允许 agent 自我授权。改为取交集，见 §3.8 |
| 用 GRADE / RoB 2 代替单条线性分级 | **部分采纳** | 同意单条线性分级不够，但偏倚评价和证据确定性分级不属于接入层。接入层只提供两轴标签和做这些判断所需的原始字段 |
| 每条证据带 14 个溯源字段 | **修改** | 拆成两层：数据源、记录 ID、版本、快照、许可证、抓取时间放在**记录级**；工具摘要、参数、随机种子放在**运行级**，填进 `ProvenanceCapsule` 已有的空字段，不在每条记录上重复 |
| 首批 P0：HERB、ETCM、HKCMMS、ICD-11 TM | **修改** | 机制链路优先用已有、许可清晰、能批量获取的 LOTUS / NPASS / CMAUP / ChEMBL，再加 BindingDB。HERB / ETCM 负责中药名称和方剂层；HKCMMS 是质量标准，不在机制链路上，降为 P1 |
| 12 周计划（含组学、临床方法学） | **修改** | 数据接入只需三个里程碑（§4）。组学管线和临床方法学各自都是独立项目 |
| `agents/openai.yaml` | **可选** | 只是宿主界面的显示信息，不纳入契约 |

**外部方案里需要更正的事实**：

- **论文许可证不等于数据许可证**。例如 HERB 2.0 论文是 CC BY-NC，但数据的使用条款要以网站为准，接入前必须单独核对。
- **ChiCTR 没有公开 API**。可以通过 WHO ICTRP（ChiCTR 定期向它提交数据）检索，或者人工导出。
- **STRICTA 是针刺试验的报告规范**，方剂试验应使用 CONSORT-CHM Formulas 2017。试验方案方面，除 SPIRIT-TCM 2018 外，已有 SPIRIT 中药方剂扩展 2025 版；系统评价用 PRISMA-CHM 2020。使用时要锁定具体版本。
- **ICD-11 的 API 需要注册 OAuth2 客户端**，许可证是 CC BY-ND 3.0 IGO，也可以本地部署官方容器。为了可复现，应锁定具体的 release 版本。
- **ETCM 2.0 的靶点分两类**：一类是已证实的，另一类是基于二维配体相似性预测的潜在靶点。必须按记录区分，分不清时标为预测。
- **TCMToxDB** 既整合了文献中的毒性研究结果，也内置了 5 种靶点预测算法。算法得到的毒性靶点必须标为预测。
- **HERB 2.0 的临床结论**是从配套论文中人工整理的二手摘要，必须回溯到原文核对。
- **LingShu** 是 2026-07-29 提交的 arXiv 预印本，规模数字是论文自述。外部方案把它定为 P2 实验性来源，这个定位是合理的。
- **外部方案遗漏了"药材 ≠ 物种"**：天然产物库按物种索引，中药研究的对象却是药材（基原 + 部位 + 炮制），见 §3.6。

### 2.2 v1 规范的自我修正

| v1 的做法 | 问题 | v2 的处理 |
| --- | --- | --- |
| 以 `search / get / related` 在线查询为中心 | 分析结果依赖实时网络，无法复现，还会给上游施压 | 改为快照优先（§3.1） |
| `predicted` / `text_mined` 映射到 `PRECLINICAL` | 预测和实验混为一级 | 改为证据两轴（§3.5） |
| L2/L3 网页抓取作为常规通道 | 合规风险高，接口也容易随改版失效 | 改为默认关闭、逐源审批（§3.3） |
| 自定义了一整套状态映射（如 429 映射为 `UNAVAILABLE`） | 与现有 `HTTPBackend` 的实际行为不一致 | 沿用现有后端，只把问题列为 M1 修复项 |
| 540 行，规定过细 | 实现成本高，容易与代码脱节 | 只规定必须统一的部分 |

---

## 3. v2 方案

### 3.1 数据流：一条流水线，四个产物

```
SourceCard ──► fetch ──► raw/        不可变原始文件 + sha256（批量下载 / API 响应 / 人工导入文件）
                 │
                 ▼
               parse      每个源一个纯函数：raw → 行（不联网、不做筛选）
                 │
                 ▼
             normalize    ID 归一（InChIKey / UniProt / NCBITaxon / MONDO …）、单位统一、打证据两轴标签
                 │
                 ▼
              validate    质量检查门（§3.7）：不通过就不发布
                 │
                 ▼
          snapshot/<key>/<version>/   nodes.parquet · edges.parquet · manifest.json · qc.json
                 │
                 ▼
     load_snapshot（只读，每次加载都重新校验）──► Skill / psh.workflow 任务 ──► 候选主张 ──► 发布门
```

- **分析只读取快照，不在分析过程中实时调用第三方接口。**实时 API 只用于 ID 解析和补查快照里缺的单条记录，结果同样缓存并记录溯源。
- 这个模式借鉴 Monarch 的 Koza（声明式配置加转换函数，产出 KGX 格式），但**不引入 Koza 依赖**，只用仓库已有的 `pandas` + `pyarrow`。

### 3.2 SourceCard：每个数据源一张卡片

实现为 `bioagent/sources/cards.py` 中的 `SourceCard`（下面是示意）。它与 `PublicSource` / `AcquisitionSpec` 并存：卡片只管治理信息，包括访问方式、许可证和证据默认值；具体的请求和下载仍由原来这两者执行。所以卡片不会重复定义接口：

```yaml
key: bindingdb                    # 用作 CURIE 前缀和快照目录名
name: BindingDB
citation: doi:10.1093/nar/gkae1075
access:                           # 按优先级排列，取第一个可用的
  - {mode: bulk, url: "https://www.bindingdb.org/…/BindingDB_All_{version}_tsv.zip",
     checksum: provider-md5, cadence: monthly}
  - {mode: api, base_url: "https://www.bindingdb.org/rest", rps: 1}
license:
  default: CC-BY-4.0
  per_record: {field: curation_source, ChEMBL: CC-BY-SA-3.0}   # 记录级许可证
  terms_url: https://www.bindingdb.org
provides:                         # 本源产出的边及其默认证据标签（§3.5）
  - {predicate: targets, subject: ingredient, object: target,
     knowledge_level: knowledge_assertion, agent_type: manual_agent, study_design: in_vitro}
enabled: true                     # web 方式默认为 false，见 §3.3
reviewed: {by: "<维护人>", at: 2026-09-23}
```

SourceCard 属于**代码层**：随程序包发布，改动要经过代码评审，运行时 agent 不能直接改写（见 §3.8）。其中的启用开关（`enabled` 和 `web` 方式）还应纳入演化边界（`KernelBoundary`）的保护范围，让 agent 连"提议开启"都做不到。

### 3.3 访问方式：四种，按优先级

| 方式 | 用途 | 规则 |
| --- | --- | --- |
| `bulk` | 首选。官方下载文件，带版本号和校验值 | 进入快照流水线 |
| `api` | 有文档的接口，用于 ID 解析和单条补查 | 经过 `HTTPBackend`（限速、重试、缓存） |
| `manual` | 用户自己取得的导出文件，例如机构授权的 DrugBank、按协议获得的 ETCM/HERB 导出、HKCMMS 表格 | 走同一套解析和质量检查；溯源记录文件哈希、获取人和适用条款 |
| `web` | 既没有下载也没有 API 的公开页面，按需查询单条记录 | **默认关闭**。只有在服务条款不禁止自动访问、且维护人在 SourceCard 中开启后才可用；每秒不超过 1 次请求；不做批量抓取；不绕过任何访问控制 |

外部方案主张"没有明确授权时只支持人工导入"，v2 用 `manual` 方式落实这一点。`web` 方式保留下来，但只作为默认关闭、逐源审批的例外，不再是常规通道。

### 3.4 统一数据模型：KGX 风格的节点表和边表

不再为每类实体设计单独的类。每个快照统一成两张 Parquet 表，列名与 KGX 兼容（KGX 是 Monarch 知识图谱等采用的交换格式）：

**nodes（节点表）**：`id`（CURIE。有标准全局标识的实体直接用它：化合物用 `inchikey:`，蛋白用 `uniprot:`，物种用 `ncbitaxon:`；这样不同来源的同一实体读在一起时就是同一个节点。没有全局标识的才用 `<来源>:<来源 ID>`。来源自己的 ID 一律保留在 `xrefs` 中）· `category`（herb / formula / ingredient / target / disease / syndrome / symptom / pathway / publication）· `name` · `names`（中文 / 拼音 / 拉丁 / 英文 / 别名）· `xrefs` · `source` · `snapshot_id`

**edges（边表）**：

| 列 | 说明 |
| --- | --- |
| `subject` `predicate` `object` | 谓词沿用 `tcm.model.PREDICATES`，按需补充 `associated_with` / `participates_in` / `manifests_as` |
| `primary_knowledge_source` | 最初给出这条断言的库；如果是从别的库转引来的，另记 `aggregator_knowledge_source` |
| `knowledge_level` · `agent_type` | 证据第一轴（§3.5），使用 Biolink 标准枚举 |
| `study_design` · `evidence_tier` | 证据第二轴（§3.5） |
| `publications` | PMID / DOI；当 `evidence_tier.needs_citation` 为真时不能为空 |
| `measure` | 结构化测量值，如 `{type: Ki, relation: "=", value: 12, unit: nM}`；原始值保留在 `raw` 列 |
| `score` · `score_name` | 上游给出的原始分数（如 OB、DL、combined_score）。**接入层不做阈值筛选** |
| `composition_level` | 仅用于"药材含成分"类的边，取值见 §3.6 |
| `license` · `source_record_id` · `snapshot_id` | 记录级许可证、上游原始 ID、所属快照 |

"实验测得"和"算法预测"使用**同一个谓词**（`targets`），靠证据列区分。

### 3.5 证据两轴：取代单条线性分级

| 轴 | 回答的问题 | 取值 |
| --- | --- | --- |
| 知识层级 `knowledge_level` + `agent_type`（Biolink 标准） | 这条断言**是怎么产生的** | `knowledge_assertion`（基于直接证据的人工整理）· `prediction`（算法预测）· `statistical_association` · `text_co_occurrence` · `observation`；产生者：`manual_agent` · `computational_model` · `text_mining_agent` · `data_analysis_pipeline` 等 |
| 研究设计 `study_design` → `EvidenceTier` | 背后**是什么研究** | `classical_text` · `expert_consensus` · **`in_silico`** · `in_vitro` · `animal` · `case_report` · `observational` · `randomized_trial` · `systematic_review`（与 PSH 的 `DESIGNS` 一致）；另有 `chemical_analysis`（成分分离或检测），只用于"含有"类的边，不对应任何证据等级，不支持任何主张 |

对现有代码的修正（在 M1 中完成）：

1. **把"最低等级"改为"可支持的研究设计集合"**：`CLAIM_KINDS` 不再给每类主张规定一个最低 `EvidenceTier`，而是列出能支持它的研究设计集合，写法与 PSH 的 `_SUPPORTS` 相同。`applicability` 中的 `relation.tier < required` 相应改为集合判断。线性比较表达不了"计算预测只能支持机制假说"：新等级插得高，就会顺带支持"传统应用""文献记载"等门槛更低的主张；插在最低，古籍记载等所有其他证据反过来又都能支持"机制假说"。
2. BioScience 的 `EvidenceTier` 新增 `COMPUTATIONAL_PREDICTION = 0`，现有数值不变。取 0 是为了让 `clinical`、`needs_citation` 这类仍按大小判断的属性不会把它误判为临床证据。同时删掉 `PRECLINICAL` 注释里的"网络药理学、分子对接"。
3. 新增主张类型 `mechanism_hypothesis`，可由 `{in_silico, in_vitro, animal}` 支持；`mechanism` 只认 `{in_vitro, animal}`。PSH 这边：`DESIGNS` 加入 `in_silico`，`ClaimType` 加入 `MECHANISM_HYPOTHESIS` 并登记进 `_SUPPORTS`。这样两层的规则就完全一致了。
4. 发布门增加一条规则：如果一条主张的支持边全部是 `knowledge_level=prediction`，它最高只能是 `mechanism_hypothesis`。

**偏倚风险（RoB 2）和证据确定性（GRADE）不在接入层计算。**这两者都是针对一个具体问题、对一组研究做的判断，需要人工完成，或交给专门的证据综合 Skill。接入层只负责把判断所需的原始字段带齐：研究设计、人群、样本量、对照、文献 ID。连接器**不得**生成质量评级。

### 3.6 药材 ≠ 物种：成分证据链

LOTUS、NPASS、CMAUP 等天然产物库是按**物种**索引的，而中药研究的对象是**药材**（基原物种 + 药用部位 + 炮制）和**方剂**（配伍 + 煎煮）。因此，"某药材含某成分"这类断言必须标注证据层级：

```
C0  数据库预测
C1  文献报道存在于基原物种（任一部位）
C2  在该药用部位或药材样品中检出
C3  在炮制品或方剂煎液中检出
C4  入血成分（给药后在血浆或组织中检出）
```

实现只需要两样东西：

- 一张**人工校验的"药材 → 基原物种 + 药用部位"映射表**，复用 `tcm.model.Herb` 已有的 `species` 和 `part` 字段，首批只覆盖金标准案例涉及的药材；
- edges 表上的 `composition_level` 列。

部位对不上的成分标为 `part_unverified`，不能默认当作"药材成分"。

### 3.7 快照、复现与质量检查门

- 快照 ID 的格式是 `<key>@<version>#<manifest sha256 前 12 位>`。manifest 记录原始文件哈希、解析函数的代码哈希、输出表哈希、质量检查结果、许可证和引用。
- **同样的原始文件必须产生同一个快照哈希**（构建是确定性的），这一条写进测试。
- 快照存放在工作区的 `data/` 下，而这个目录 agent 可写。因此快照哈希在入库时写进审计和溯源日志，加载时重新校验；发现被改动就拒绝加载。
- 质量检查门（不通过就不发布）：
  1. **结构**：必填字段齐全、CURIE 格式合法、每条边的两端节点都存在；
  2. **覆盖率**：成分映射到 InChIKey、靶点映射到 UniProt 的比例写入 `qc.json`；低于该源设定的阈值时标黄，需要人工确认；
  3. **漂移**：与上一版快照比较行数和 ID 的增删，变化超过设定比例时需要人工确认；
  4. **许可证完整**：每条边都有 `license`；
  5. **金标准**：固定案例（见 M2）的映射结果必须与人工校验结果一致。

### 3.8 Skill 放在哪里、怎样使用数据

| 位置 | 放什么 | 信任级别 |
| --- | --- | --- |
| 仓库 `BioScience-Harness/skills/<domain>/<name>/` | 随项目发布、经过代码评审的科研 Skill | 评审通过后可信 |
| 运行时工作区 `<workspace>/skills/` | agent 在运行中起草或修改的 Skill | 不可信，要经过 `EvolutionPipeline` 的测试、基准和策略检查后才能晋升 |
| `.claude/skills/` 等 | 给编码助手用的开发类 Skill | 与科研运行时无关，不要混放 |

Skill 目录结构沿用 Agent Skills 的通行格式：`SKILL.md`（什么时候用、怎么做）、`skill.yaml`（机器可读的契约）、`scripts/`（确定性的计算步骤）、`references/`（参考资料）。

`skill.yaml` 只需要五项。请用块状 YAML 书写：仓库不依赖 PyYAML，内置解析器不支持 `[a, b]` 和 `{a: b}` 这类行内写法。

```yaml
id: tcm.network-pharmacology
version: 0.1.0
inputs:
  formula: FormulaRef
  indication: DiseaseOrSyndromeRef
requires:
  sources:
    - npass@2.0
    - lotus@2026-04-13
    - bindingdb
  tools:
    - stats.enrichment_analysis
max_claim_kind: mechanism_hypothesis     # 本 Skill 最多能产出的主张类型
```

`max_claim_kind` 是上限：某类主张只有在它能接受上限所接受的全部证据等级时，才算不强于上限，才被允许。所以上限设为 `mechanism_hypothesis` 时，Skill 不能产出 `mechanism` 主张；上限设为 `efficacy` 时，可以产出 `association` 和 `safety_signal`，但不能产出 `recommendation`。

**权限只能收窄，不能放大。**实际生效的数据源 = `skill.yaml` 的声明 ∩ SourceCard 中已启用的源 ∩ 当前权限配置。原因是：如果白名单只写在 `skill.yaml` 里，而 `skills/` 是 agent 可写区，agent 改一下文件就能给自己授权。

执行时不另写编译器。用一个很薄的适配器把 `skill.yaml` 转成 `ScientificProgram`，再交给已有的编译、执行、审计和发布门。

### 3.9 首批数据源

选择顺序：先看能否回答核心问题（药材 → 成分 → 靶点 → 疾病），再看许可证是否清晰，最后看能否批量获取。

| 环节 | 数据源 | 访问方式 | 默认证据标签 | 现状 |
| --- | --- | --- | --- | --- |
| 药材与方剂的名称、组成 | ETCM 2.0、HERB 2.0 | `manual`（官网或协议导出）；`web` 仅按需查单条 | 组成：`knowledge_assertion`；ETCM 靶点按记录区分已证实与预测，分不清时标 `prediction` | 新增 |
| 物种 → 成分 | LOTUS 冻结导出（CC-BY-4.0，带文献）、CMAUP / NPASS 物种-成分对 | `bulk` | `knowledge_assertion`，`composition_level=C1`。NPASS 的物种对带分离部位，与药用部位一致时可标 C2 | M2 已解析 |
| 成分 → 靶点（实测） | BindingDB、ChEMBL、NPASS 活性数据 | `bulk` | `knowledge_assertion` + `in_vitro` | BindingDB 新增；其余缺解析 |
| 成分 → 靶点（预测） | ETCM、TCMSP、TCMToxDB 的预测靶点 | `manual` / `web` | `prediction` + `in_silico` | P1 |
| 靶点 → 疾病 | Open Targets（CC0，提供 Parquet）、GWAS Catalog、Monarch（提供 KGX） | `bulk` 优先，已有 `api` | `statistical_association` / `knowledge_assertion` | 已有 `api` |
| 互作与通路 | STRING（CC-BY-4.0）、Reactome（CC0） | `bulk` | STRING：`prediction`；Reactome：TAS 为 `expert_consensus`，IEA 为预测 | 已解析 |
| 术语 | ICD-11 传统医学章（API 或本地容器）、MeSH、MONDO（经 Monarch） | `api` / `bulk` | — | ICD-11 新增 |
| 临床证据索引 | HERB 2.0 临床试验与 Meta 分析、ClinicalTrials.gov、WHO ICTRP（含 ChiCTR）、Europe PMC | `manual` / `api` | 只作为指向原文的索引，结论以原文为准 | ClinicalTrials.gov 和 Europe PMC 已有 |
| 安全性 | openFDA、DailyMed；TCMToxDB（P1） | `api` / `manual` | — | 前两者已有 |
| 质量标准 | HKCMMS | `manual`（P1） | — | P1 |

**默认不接入**：GeneCards（有商业和再分发限制）、没有机构授权的 DrugBank、《中国药典》全文。BATMAN-TCM 作为外部计算服务，其结果一律标为预测；SymMap 和 LingShu 为 P2。

---

## 4. 里程碑

下表中的工期是估计值。

| 里程碑 | 内容 | 验收标准 |
| --- | --- | --- |
| **M1 契约与修正**（约 1–2 周） | SourceCard 字段；快照 manifest；节点/边表结构及校验器。证据两轴，包括 §3.5 的四处代码修正。`HTTPBackend`：支持 `Retry-After`、缓存键加入数据版本、截断时报 `DEGRADED`。接通 `SkillDirectoryProvider`，读取仓库 `skills/` 并解析 `skill.yaml`（这一步只做校验，不执行） | 单元测试通过；只有预测支持的 `mechanism` 主张会被编译器和发布门拒绝 |
| **M2 数据快照**（约 2–3 周） | 为 LOTUS、NPASS、CMAUP、BindingDB（新增下载规格）、STRING、Reactome 编写解析器；经 ChEMBL / PubChem 归一到 InChIKey；ETCM / HERB 的 `manual` 导入器；ICD-11 传统医学章查询；药材→基原映射表（首批：葛根、黄芩、黄连、炙甘草） | 同一原始文件构建两次，快照哈希相同；生成质量检查报告；金标准通过（葛根素、黄芩苷、小檗碱、甘草酸等锚点成分都能解析到正确的 InChIKey） |
| **M3 首个端到端 Skill**（约 1–2 周） | `skills/tcm/network-pharmacology/`：在指定的快照版本上运行，经 `psh.workflow` 编译执行；新增网络拓扑与度保持随机化工具（仅用标准库）；把快照 ID、工具版本、参数和随机种子填进 `ProvenanceCapsule` | 同一输入、同一快照，结果一致；实测靶点和预测靶点可以分开统计；试图产出疗效主张会被拒绝 |

**M1 状态（已实现）**：
- `tcm/model.py`：`COMPUTATIONAL_PREDICTION = 0`、`CLAIM_SUPPORT`（按集合判断）、`mechanism_hypothesis`、`licenses()`；
- PSH：`in_silico`、`MECHANISM_HYPOTHESIS`，并登记进 `_SUPPORTS`；有测试逐项核对两层规则一致；
- `bioagent/sources/`：`cards.py`（数据源卡片、访问方式、`effective_sources`；已纳入演化边界保护）、`schema.py`（节点/边表、两轴校验、`check_claim`）、`snapshot.py`（质量检查门、确定性快照 ID、加载时重新校验）；
- `HTTPBackend`：支持 `Retry-After`、缓存键加入 `version`、截断时报 `DEGRADED`，缓存命中时也带 `fetched_at`；
- `SkillContract`（`skill.yaml`），`default_runtime` 会发现仓库 `skills/` 下的 Skill。

**M2 状态（已实现）**：
- `sources/parsers/`：NPASS、CMAUP、LOTUS（冻结导出）、BindingDB（按用户下载的 TSV 导入，按 InChIKey 过滤）。都是纯函数，不联网、不做科学筛选；每个被丢弃的行按原因计数，计数写进快照 manifest；
- `sources/herbs.py`：葛根、黄芩、黄连、甘草的基原物种（NCBI Taxonomy 已核实）和药用部位；按《伤寒论》记载锁定葛根芩连汤的组成与剂量（带指纹）；金标准标志成分（InChIKey 已对照 PubChem 核实）；
- `sources/composition.py`：药材 → 基原物种 → 成分。一律从 C1 起；只有来源记录了分离部位、且正是药用部位时才升为 C2；
- `sources/build.py` 与 `scripts/build_source_snapshots.py`：构建快照并校验金标准；
- 数据源卡片新增 BindingDB（`api` + `manual`；记录级许可证）。LOTUS 冻结导出的许可证更正为 CC-BY-4.0。

**M2 真实数据结果（2026-09-23，限定在四味药的基原物种）**：
- 4 个快照都通过质量检查，金标准全部复现（葛根素、黄芩苷、小檗碱、甘草酸在 NPASS、CMAUP、LOTUS 三个来源中都能找到）；
- 同一批原始文件两次构建，快照 ID 完全相同；LOTUS 文件的 md5 与 Zenodo 公布值一致；
- 成分表共 1898 条（药材×成分），其中只有 22 条达到 C2，都是甘草。原因是 NPASS 只为这些物种的 9 个物种-成分对记录了分离部位，属于数据本身的缺口，不是匹配问题；
- 值得注意的例子：葛根素也出现在黄芩名下，只有 1 条文献支持。这正是 C1 单一来源断言需要人工复核的原因；
- NPASS 中有 43,297 条活性数据针对细胞系或整体生物，不是蛋白靶点，另有 202 条没有文献，都已丢弃并计数。

**M1 遗留三项（已完成）**：
- **快照账本**（`sources/ledger.py`）：只追加、哈希链式记录。每次构建都把快照 ID 记进去，`load_snapshot(..., ledger=...)` 以账本为准。这样即使有人把快照目录里的表、manifest 和 ID 一起改得前后一致，也能被识别出来。账本应放在 agent 不可写的 `audit/` 下。
- **发布检查**（`sources/release.py`）：这里**调整了原设计**。PSH 的 `OutputGate` 检查的是"文字句子是否有所引来源支持"，不是结构化的边，把边级检查塞进可信内核层次不对。现在分成两层：
  - 编译期：由下面的适配器把每一步的研究设计和主张类型声明进 `ScientificProgram`，PSH 编译器照旧拒绝不匹配的组合；
  - 发布前：`check_release` 在已校验的快照里逐条核对候选主张，要求①所引的边确实存在，②这些边把主语和宾语连起来，③主张类型不超过 Skill 的上限，④按"最弱一环"判定证据等级。定义性的边（药材→基原物种、方剂→药材）不参与判定。成分证据低于 C3 时（没有证明给药制剂中确实含有该成分），整条链最高只能支持 `mechanism_hypothesis`。
- **`skill.yaml` → `ScientificProgram` 适配器**（`bioagent/psh/skill_program.py`）：`skill.yaml` 新增可选的 `steps` 段，每步写明工具、依赖和研究设计，工具必须列在 `requires.tools` 里。测试证实：即使把 Skill 的上限调到 `mechanism`，PSH 编译器仍会以 EVIDENCE103 拒绝仅由 `in_silico` 支持的机制主张。

**STRING 与 Reactome 快照（已完成）**：
- `parsers/string_db.py`：只保留范围内蛋白之间的诱导子网络（也可选"含一阶邻居"）。STRING 的综合分数是对多种证据渠道算出的置信度，所以边一律标为 `prediction` / `in_silico`，分数原样保留，**不设阈值**。400/700/900 等阈值由分析方案决定；
- `parsers/reactome.py`：人工整理的 TAS 注释标为 `expert_consensus`，电子推断的 IEA 标为预测；默认只取人类数据。发布检查把"参与某通路"视为注释，不视为效应证据；
- `build_gold(..., network=True)` 或脚本的 `--network` 选项：把 STRING 和 Reactome 限定在金标准快照实际报告的靶点上。真实数据结果：STRING 634 个蛋白、45,931 条边；Reactome 4,833 条通路成员关系；两者都通过质量检查。另有 418 个范围内蛋白在范围内没有 STRING 连接，多为活性表中的非人源靶点，已计数；
- 更正：Reactome 数据的许可证是 CC0（见 reactome.org/license），原下载表里写的是 CC-BY-4.0，已改正。

**M3 首个端到端 Skill（已完成）**：`skills/tcm/network-pharmacology/`（`SKILL.md` 与 `skill.yaml`）+ `bioagent/analysis/`：
- 流程：组成（C1/C2）→ 实测靶点（IC50/Ki/Kd/EC50 ≤ 10 µM；预测靶点不用；">" 这类删失值不算达标）→ Reactome 富集。富集以完整的人类注释为背景，只检验大小在 5–500 的通路，BH 校正覆盖**全部**被检验的通路。BH 显著后还要通过**注释度匹配的置换零分布**（随机抽取的靶点在"注释到多少条通路"上与真实靶点匹配），以控制"研究得多的蛋白到处都显著"的偏倚 → STRING 拓扑（置信度 ≥ 0.7，只作描述）→ 候选主张 → 发布检查；
- 每条主张都引用一条完整路径上的快照边：方剂→药材→基原→成分→靶点→通路。同时记录这条路径最多能支持哪一类主张，以及为什么只能停在"假说"（成分证据低于 C3）；
- `scripts/run_network_pharmacology.py`：通过账本加载快照，按 Skill 契约授予数据源，编译为 PSH 程序，运行分析，写出 `compounds/targets/enrichment/network.tsv`、`claims.json`、`release.json`、`provenance.json`（快照 ID、参数、随机种子、代码摘要、PSH 程序指纹、结果摘要，即 `ProvenanceCapsule` 原本留空的那些字段）和 `limitations.md`；
- 真实数据结果（葛根芩连汤，2026-09-23）：1,792 个成分；370 个实测靶点，其中 237 个在 Reactome 人类背景内；检验 1,684 条通路；66 条同时通过 BH（q ≤ 0.05）和度匹配置换检验；66 条机制假说全部通过发布检查。STRING 子网络有 237 个节点、624 条边，最大连通分量 195 个节点，度数最高的依次是 EGFR、AKT、TNF、CYP3A4、PTGS2。两次独立运行的结果摘要完全一致；
- **结果本身暴露的偏倚**：排名最前的包括"二氧化碳可逆水合"（碳酸酐酶）和"阿巴卡韦跨膜转运"（药物转运体）。这反映的是天然产物常被拿去筛选的靶点组合，度匹配零分布控制不了这种偏倚。`limitations.md` 已写明：ADME 类和筛选组合类通路应先理解为"检测覆盖"，而不是生物学结论；
- **尚未关联疾病基因集**：目前的主张都是关于通路的，还没有涉及 2 型糖尿病。下一步需要疾病关联快照（例如 Open Targets）。

**仍未完成**：
- ETCM / HERB 导入器：需要拿到它们实际的导出文件后，按真实格式来写；
- BindingDB：解析器已按官方格式说明实现，并用合成文件测试过，但还没有用真实下载文件验证。下载页面需要人工操作，本环境无法代为完成；
- ICD-11 传统医学章：需要注册 OAuth 客户端；

后续另立项目：组学管线、临床方法学类 Skill、基于 RoB 2 / GRADE 的证据综合 Skill、每月数据源健康检查（在 `scripts/verify_connectors.py` 基础上增加快照漂移报告）。

---

## 5. 明确不做

- 不在分析过程中实时抓取第三方网站；
- 不绕过登录、验证码或限流，不做全库抓取；
- 连接器不产生质量评级，也不做 OB / DL 等阈值筛选；
- 不在仓库中再分发上游数据：快照放在工作区 `data/`，不纳入版本库；
- 不为每个数据库单独写一套实体类；
- 组学分析和临床方法学不在本方案范围内。

---

## 参考

- HERB 2.0：[NAR 2025, doi:10.1093/nar/gkae1037](https://pmc.ncbi.nlm.nih.gov/articles/PMC11701625/)
- ETCM v2.0：[Acta Pharm Sin B 2023](https://www.sciencedirect.com/science/article/pii/S2211383523001016)
- BindingDB 2024（许可证、月度下载、md5、REST）：[NAR 2025, doi:10.1093/nar/gkae1075](https://pmc.ncbi.nlm.nih.gov/articles/PMC11701568/)
- TCMToxDB：[Database 2026, baag019](https://academic.oup.com/database/article/doi/10.1093/database/baag019/8654706)
- LingShu：[arXiv:2608.20402](https://arxiv.org/abs/2608.20402)
- ICD-API 认证：[icd.who.int/docs/icd-api/API-Authentication](https://icd.who.int/docs/icd-api/API-Authentication)；ICD-11 许可证：[CC BY-ND 3.0 IGO](https://icd.who.int/docs/icd-api/license)
- ChiCTR 与 WHO ICTRP：[WHO 一级注册机构说明](https://www.who.int/tools/clinical-trials-registry-platform/network/primary-registries/chinese-clinical-trial-registry-(chictr))
- 报告规范：[CONSORT-CHM Formulas 2017](https://www.acpjournals.org/doi/10.7326/M16-2977) · [SPIRIT-TCM 2018](https://pubmed.ncbi.nlm.nih.gov/30484022/) · [SPIRIT 中药方剂扩展 2025](https://www.researchgate.net/publication/402106264_SPIRIT_Extension_for_Chinese_Herbal_Medicine_Formula_2025_Recommendations_explanation_and_elaboration) · [PRISMA-CHM 2020](https://www.equator-network.org/reporting-guidelines/prisma-chinese-herbal-medicines-2020/) · [STRICTA（针刺）](https://www.equator-network.org/reporting-guidelines/consort-stricta/)
- Biolink：[KnowledgeLevelEnum](https://biolink.github.io/biolink-model/KnowledgeLevelEnum/) · [AgentTypeEnum](https://biolink.github.io/biolink-model/AgentTypeEnum/)
- Monarch KG（KGX 格式下载）与 Koza：[monarch-ingest](https://monarch-initiative.github.io/monarch-ingest/KG-Build-Process/kg-build-process/)
- Open Targets 许可证（CC0）：[platform-docs.opentargets.org/licence](https://platform-docs.opentargets.org/licence)
- LOTUS：[eLife 2022](https://elifesciences.org/articles/70780)；Zenodo 冻结导出（CC-BY-4.0，Wikidata 上的陈述为 CC0）：[zenodo.org/records/19360665](https://zenodo.org/records/19360665)
