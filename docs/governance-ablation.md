# Governance ablation: does the governance reduce scientific error, and which part does?

A governed architecture is only an advantage if the same outputs, put through it, come out
with fewer scientific errors than they went in with, and at an acceptable cost in correct
outputs refused. That is a counterfactual about the same outputs, the same tools and the same
tasks, with each part of the governance switched on or off. This benchmark measures it on the
runtime's own checks.

## Method: scientific mutation testing

- **Base cases.** Ten outputs a careful reviewer would let through, six in Chinese and four in
  English: three randomised trials, two cohorts, a case report, a bench assay, a docking run,
  and a passage of the 伤寒论. Each case has a statement, a structured claim and the source it
  cites (`bioagent.benchmarks.ablation_corpus`).
- **Mutants.** Each mutation operator turns a base case into an output with one known
  scientific error (`ablation.MUTATIONS`), giving 111 mutants in 21 classes:
  - a finding carried to a population, outcome or intervention the study did not have;
  - its direction inverted, or stated as proven, always or curative;
  - a claim kind the evidence cannot reach: an association stated as efficacy, a docking score
    or a mouse study cited for patients, a passage of the 伤寒论 cited as a clinical result;
  - a claim of cure followed by a plan clause;
  - a near-name herb (制白附子 for 制附子, 白首乌 for 何首乌), or a dose read ten times too high;
  - a direction no edge on the claim's path records;
  - a retracted record, a record edited after it was signed, the record of one paper supplied
    under another's identifier, or a citation with no record behind it.

  Five of the classes appear twice. The `*_text` variants change only the sentence a reader
  sees and leave the structured claim naming what was studied: an agent's summary drifting
  from its own structured claim.
- **Gates.** The checks the runtime runs, called as it calls them:

  | Gate | Layer | What it is |
  | --- | --- | --- |
  | `provenance` | kernel | `ingest_evidence`: identifier binding, and the signature re-checked so a tampered record is marked |
  | `output` | kernel | `OutputGate`: per-sentence support, scope, citations and plan clauses |
  | `licensing` | kernel | `psh.sir.licenses`, the compiler's TYP rules, on the structured claim |
  | `claim_contract` | domain | `check_claim`: CLM codes, including the TCM near-name check |
  | `release_path` | domain | `check_release`: the claim's path of snapshot edges, its direction and licensing |

- **Configurations.** Every combination worth reading: no governance at all, each layer
  alone, the full stack, the full stack minus each gate, and each gate alone. An output is
  released when no enabled gate refuses it. Each gate runs once per case and every
  configuration is read off the same results. The one dependency is kept as the runtime
  has it: with ingest on, the output gate sees the records ingest re-checked; with ingest off,
  a record edited after signing looks intact to it.

## Results

`python scripts/run_governance_ablation.py --out benchmarks/ablation` (1.5 s; committed as
`BioScience-Harness/benchmarks/ablation/results.json` and `results.md`):

| Configuration | Errors released | 95% CI | Correct outputs refused |
| --- | ---: | --- | ---: |
| no governance | 111/111 (100%) | 97–100% | 0/10 |
| kernel only | 14/111 (13%) | 8–20% | 0/10 |
| domain layer only | 69/111 (62%) | 53–71% | 0/10 |
| **full stack** | **10/111 (9%)** | **5–16%** | **0/10** |
| full stack without ingest | 20/111 (18%) | 12–26% | 0/10 |
| full stack without the output gate | 48/111 (43%) | 34–53% | 0/10 |
| full stack without licensing | 13/111 (12%) | 7–19% | 0/10 |
| full stack without the claim contract | 13/111 (12%) | 7–19% | 0/10 |
| full stack without the release check | 11/111 (10%) | 6–18% | 0/10 |

**What each gate adds** (leave-one-out: errors the full stack refuses and releases once that
gate is off):

| Gate | Errors only it stops | Which |
| --- | ---: | --- |
| output gate | 38 | inverted directions, every text-only drift it catches, the ×10 dose, invented citations, and (with ingest) the tampered records |
| ingest | 10 | all nine tampered records the output gate would cite, and a misattributed passage |
| licensing | 3 | an over-certain mechanism claim, two misattributions in structured claims |
| claim contract | 3 | both near-name herbs in the text, an over-certain hypothesis |
| release check | 1 | the borrowed direction |

The layers are not redundant. The kernel's text gate does most of the work. The structured
checks (licensing and the claim contract) each catch every structured population, outcome,
kind, animal-evidence and docking-evidence error. The domain layer is the only part that
catches a near-name herb written in the text or reads a snapshot path, and the kernel is the
only part that knows a record was edited after signing.

### What this change closed

Run on the code before this change (commit `2a0753e`), the full stack released **19/111
(17%)** of the same mutants. It now releases **10/111 (9%)**, and still refuses none of the
base cases. Three changes, each found by the benchmark; applied in the order listed they close
6, 1 and 2 of the 9:

1. **Citations in non-clinical sentences** (`psh.kernel.output_gate`). The output gate
   skipped any sentence that makes no claim about patients, citations included. A mechanism,
   a hypothesis or a hedged safety sentence citing a retracted record, another paper's record,
   or no record went out. Support is still checked only for claims about patients; now the
   citation itself is checked in every sentence: the record must be supplied, be the record of
   the identifier cited, and be usable.
2. **Chinese association and safety wording** (`_CLINICAL_ZH`). 有关 is the counterpart of
   相关, and 不良事件, 毒性, 肝损伤 and 肾损伤 are the counterparts of 不良反应. Without them,
   "服用含何首乌的制剂可能与成人药物性肝损伤有关" was read as making no claim at all.
3. **Near names in claim text** (`check_claim`, CLM014). `materia.names_in` reads drug names
   out of running text, and treats 川, 大, 小, 白, 土 and 水 before a known name as part of
   another name: 白首乌 is not read as 首乌, which is an alias of 何首乌. The exceptions are
   a character that ends an everyday word (减小附子 reads 附子, 加大黄芪 reads 黄芪) and the
   start of a formula or preparation (小柴胡汤, 小柴胡口服液). A claim naming a drug that
   shares a drug name with the one its evidence studied, but is not it, is refused.

### What the full stack still releases

Listed, not hidden. Each is a known gap with a named cause:

| Mutant | Why it passes |
| --- | --- |
| `A1/population_text` (children, from a cohort of adults aged 65 or older) | the population parser reads nothing in "adults aged 65 or older", so the source's population is unstated, and the scope check notes an unstated population rather than checking against it |
| `A2/population_text`, `A2/outcome_text` (儿童; 全因死亡率) | the Chinese claim parser does not find the subject of "长期服用X与…相关", so it does not check the scope |
| `S1/population_text` (儿童) | the same, for "服用X可能与…有关" |
| `E2/outcome_text` (all-cause mortality, from a trial of cardiovascular death) | both normalise to "mortality": outcome granularity is too coarse |
| `M1/certainty_text` ("always completely inhibits") | a mechanism sentence is not checked for support or certainty, and "always" is not in the claim contract's over-reach vocabulary |
| `H1/subject_text` (berberine, from a baicalin docking run) | non-clinical sentences are checked for citation integrity, not for support |
| `C1/subject_text`, `C1/tampered`, `C1/fabricated_citation` (a passage of the 伤寒论) | passage citations (`shanghanlun:34`) are not an identifier scheme the output gate recognises, so their records are never looked up |

The first five need better scope parsing. The last four need a passage-citation scheme, and a
support check for non-clinical sentences that does not refuse ordinary methods citations.

## What this is not

- **Construction, not field rates.** The base cases and the operators were written by the
  people who wrote the gates. An error class nobody thought to generate is not measured, and
  the class balance is set by how many operators apply to how many cases, not by how often each
  error occurs in real output. These are regression numbers with a stated construction, like
  `PSH-Harness/benchmarks/bench_claims.py`, and CI refuses a regression against them
  (`--check`).
- **Same outputs, not the same model.** Every configuration judges identical outputs. The
  question "does a model under this governance write better science" also involves what the
  model writes when it knows it will be checked, which this does not measure.
- **Ten base cases.** The intervals are wide on purpose; read them.

## Your own outputs

The same gates and configurations run on any labelled set of outputs. Write one case per
line in the `Case.from_dict` form, with `gold` set to `release` or `refuse` and an
`error_class` for refusals. For example: one model's answers to the same tasks, labelled by a
reviewer.

```bash
python scripts/run_governance_ablation.py --drafts drafts.jsonl --out RUN/ablation
```

Tests: `BioScience-Harness/tests/test_governance_ablation.py`, `tests/test_near_names.py`,
`PSH-Harness/tests/test_output_gate_citations.py`.

## 中文摘要

**要回答的问题：** 治理架构是否真能减少科学错误？哪一部分起作用？关键是：在相同输出、相同工具、相同任务下，逐一开关治理组件。

**方法（科学变异测试）：**
- **基础案例：** 10 个审稿人会放行的输出，6 个中文、4 个英文。覆盖随机对照试验、队列研究、病例报告、体外实验、分子对接，以及《伤寒论》条文。
- **变异体：** 每个变异算子注入一种已知科学错误，共 21 类、111 个变异体。错误包括：
  - 人群、终点、对象外推，方向反转，确定性夸大；
  - 证据类型越级，例如对接、动物实验或经典条文被当作临床证据；
  - 用计划从句掩护疗效断言；
  - 近名药（制白附子冒充制附子、白首乌冒充何首乌），剂量错读 10 倍；
  - 路径上没有记录的作用方向；
  - 已撤稿、签名后被篡改、张冠李戴或凭空捏造的引用。

  其中 5 类另有"仅改正文、不改结构化字段"的版本，模拟摘要与结构化主张不一致。
- **闸门：** 均为运行时真实调用的检查。内核有三个：证据摄入（标识绑定、签名复核）、输出闸门、许可类型检查。领域层有两个：主张契约（含近名药检查）、快照路径发布检查。

**结果（全部在 10 个正确输出 0 误拒的前提下）：**

| 配置 | 放行的错误 |
| --- | ---: |
| 无治理 | 111/111（100%） |
| 仅内核 | 14/111（13%） |
| 仅领域层 | 69/111（62%） |
| 完整治理 | 10/111（9%，95% 置信区间 5–16%） |

留一法显示各层互不冗余：
- 输出闸门独自拦下 38 个错误；
- 证据摄入独自拦下 10 个，均为签名后被篡改的记录；
- 许可检查与主张契约各独自拦下 3 个；
- 快照路径检查独自拦下 1 个。

**本次改动（均由该基准发现）：** 改动前（2a0753e），同一基准下完整治理放行 19/111（17%），现在为 10/111（9%）。
- **非临床句中的引用也须指向可用记录：** 必须提供记录，标识一致，且未撤稿、未被篡改。
- **补全中文临床词汇：** 增加"有关"和不良事件类词语。
- **主张正文中的近名药判为不同药物：** 新增 CLM014。「减小附子」这类日常词、「小柴胡汤」这类方剂名不算近名药。

**仍放行的 10 个错误都已列明原因：**
- 部分人群、终点表述未被解析；
- "全因死亡"与"心血管死亡"未区分；
- 非临床句只查引用、不查支持；
- 《伤寒论》条文引用尚无可机读的标识方案。

**局限：** 本基准是有明确构造的回归基准，不是对真实错误率的估计。CI 会拒绝任何回退。
