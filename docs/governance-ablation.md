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
  scientific error (`ablation.MUTATIONS`), giving 121 mutants in 26 classes:
  - a finding carried to a population, outcome or intervention the study did not have;
  - its direction inverted, or stated as proven, always or curative;
  - a claim kind the evidence cannot reach: an association stated as efficacy, a docking score
    or a mouse study cited for patients, a passage of the 伤寒论 cited as a clinical result;
  - a claim of cure followed by a plan clause;
  - a near-name herb (制白附子 for 制附子, 白首乌 for 何首乌), or a dose read ten times too high;
  - a direction no edge on the claim's path records;
  - a retracted record, a record edited after it was signed, the record of one paper supplied
    under another's identifier, or a citation with no record behind it;
  - what a TCM finding is about beyond its named subject: one constituent's evidence carried
    to the whole formula or the formula's credited to one herb, one processing state's
    evidence carried to another (制附子 to 生附子), a bench result placed at the
    concentrations patients reach, and one study reached through two databases counted as
    two independent ones;
  - an outcome nobody measured stated as absent: "no adverse reactions" or 无毒 over a trial
    that recorded only its efficacy outcome, "not associated with mortality" over a cohort
    that followed only kidney failure, "does not inhibit T2" over an assay of T1.

  Five of the classes appear twice. The `*_text` variants change only the sentence a reader
  sees and leave the structured claim naming what was studied: an agent's summary drifting
  from its own structured claim.
- **Gates.** The checks the runtime runs, called as it calls them:

  | Gate | Layer | What it is |
  | --- | --- | --- |
  | `provenance` | kernel | `ingest_evidence`: identifier binding, and the signature re-checked so a tampered record is marked |
  | `output` | kernel | the kernel's own `OutputGate`: per-sentence support, scope, citations, quotations and plan clauses; a mechanism sentence is held to its record |
  | `licensing` | kernel | `psh.sir.licenses`, the compiler's TYP rules, on the structured claim |
  | `claim_contract` | domain | `check_claim`: CLM codes, including the TCM near-name, level, processing, exposure and source-count checks |
  | `release_path` | domain | `check_release`: the claim's path of snapshot edges, its direction and licensing |

- **Configurations.** Every combination worth reading: no governance at all, each layer
  alone, the full stack, the full stack minus each gate, and each gate alone. An output is
  released when no enabled gate refuses it. Each gate runs once per case and every
  configuration is read off the same results. The one dependency is kept as the runtime
  has it: with ingest on, the output gate sees the records ingest re-checked; with ingest off,
  a record edited after signing looks intact to it.

## Results

`python scripts/run_governance_ablation.py --out benchmarks/ablation` (a few seconds;
committed as `BioScience-Harness/benchmarks/ablation/results.json` and `results.md`):

| Configuration | Errors released | 95% CI | Correct outputs refused |
| --- | ---: | --- | ---: |
| no governance | 121/121 (100%) | 97–100% | 0/10 |
| kernel only | 7/121 (6%) | 3–11% | 0/10 |
| domain layer only | 57/121 (47%) | 38–56% | 0/10 |
| **full stack** | **0/121 (0%)** | **0–3%** | **0/10** |
| full stack without ingest | 10/121 (8%) | 5–15% | 0/10 |
| full stack without the output gate | 42/121 (35%) | 27–44% | 0/10 |
| full stack without licensing | 0/121 (0%) | 0–3% | 0/10 |
| full stack without the claim contract | 6/121 (5%) | 2–10% | 0/10 |
| full stack without the release check | 1/121 (1%) | 0–5% | 0/10 |

**What each gate adds** (leave-one-out: errors the full stack refuses and releases once that
gate is off):

| Gate | Errors only it stops | Which |
| --- | ---: | --- |
| output gate | 42 | inverted directions, every text-only drift, the ×10 dose, invented citations, and (with ingest) the tampered records |
| ingest | 10 | the ten tampered records, the passage of the 伤寒论 included |
| claim contract | 6 | both duplicate sources, a near-name herb in the text, the exposure claim, "no adverse reactions" and 无毒 over trials that measured neither |
| release check | 1 | the borrowed direction |
| licensing | 0 | nothing the others do not also catch: every licensing refusal is now also a claim-contract or output-gate refusal |

The layers are still not redundant. The kernel's text gate does most of the work. The domain
layer alone catches what needs domain knowledge or structure the text does not show: a
near-name herb written in the text, one study counted twice, a bench result placed at human
exposure, an unmeasured outcome stated as absent, a direction no snapshot edge records. Only the kernel knows a record was edited
after signing.

### What was closed, in two rounds

The benchmark found every gap it now refuses, and each fix is a change to a runtime check,
not to the benchmark.

**Round 1** (19 → 10 of 111). Run on commit `2a0753e`, the full stack released 19 of the
then 111 mutants:

1. **Citations in non-clinical sentences** (`psh.kernel.output_gate`). The output gate skipped
   any sentence that makes no claim about patients, citations included; now the citation is
   checked in every sentence: the record must be supplied, be the record of the identifier
   cited, and be usable.
2. **Chinese association and safety wording** (`_CLINICAL_ZH`): 有关, 不良事件, 毒性, 肝损伤, 肾损伤.
3. **Near names in claim text** (`check_claim`, CLM014). `materia.names_in` reads drug names
   out of running text and treats 川, 大, 小, 白, 土 and 水 before a known name as part of
   another name: 白首乌 is not read as 首乌, which is an alias of 何首乌. The exceptions are a
   character that ends an everyday word (减小附子 reads 附子, 加大黄芪 reads 黄芪) and the start
   of a formula or preparation (小柴胡汤, 小柴胡口服液).

**Round 2** (10 → 0). The ten that still passed, each with its fix:

| Mutant | Why it passed | Fix |
| --- | --- | --- |
| `E2/outcome_text` (all-cause mortality, from a trial of cardiovascular death) | "death" in "cardiovascular death" read as mortality, and a cardiovascular event licensed a mortality claim | cardiovascular death is a cardiovascular event and licenses no mortality claim (`psh.evidence.claims`, `scope`) |
| `A1/population_text` (children, from adults aged 65 or older) | "adults aged 65 or older" was not read as a population | age phrases of that form, and 65岁及以上, read as older adults |
| `A2/population_text`, `A2/outcome_text`, `S1/population_text` | the Chinese parser found no subject in 「长期服用X与…相关」 and 「服用X可能与…有关」, so scope was never checked | the exposure X is the subject of an association; 有关 is an association; kidney failure and liver injury are outcomes |
| `M1/certainty_text` ("always completely inhibits") | a mechanism sentence was checked for its citation, not against its record | a mechanism sentence citing a record must be supported by it at the strength written (`OutputGate`); "always", "invariably", 总是, 必然 are absolute language no claim kind licenses (CLM011) |
| `H1/subject_text` (berberine, from a baicalin docking run) | the same | the agent a mechanism sentence names must be one its record reports on |
| `C1/subject_text`, `C1/tampered`, `C1/fabricated_citation` | a passage citation was no identifier the gate recognised, so its record was never looked up | a deployment gives the kernel its citation shapes (`PSHConfig.citation_patterns`; the TCM layer cites passages as `passage.shl_34`), and quoted words must be in the record they cite |

The same round added four TCM domain checks, and the next change a fifth (CLM019), each with
an operator:

| Code | The error it refuses |
| --- | --- |
| CLM015 | a formula claimed from one constituent's evidence, or a constituent credited with the formula's (黄连 from a trial of 葛根芩连汤) |
| CLM016 | a processing state other than the one studied (生附子 on evidence about 制附子); the resolver strips processing to find the drug, so the near-name check cannot see it |
| CLM017 | relevance at human exposure ("at concentrations reached in patients") on evidence that measured no exposure in people |
| CLM018 | more independent sources counted than the evidence holds: the same trial through PubMed and a database is one study |
| CLM019 | an absence stated where nothing was tested: "no adverse reactions", 无毒, "not associated with mortality" or "does not inhibit T2" over evidence that never measured it. Unknown stays unknown: only an item that itself reports a tested absence licenses one, and a prediction never does (a docking run that finds no pose has not shown the compound does not bind) |

### What the full stack still releases

On this construction, nothing. Read that with the next section: it means the gaps this
benchmark can express are closed, not that the gates catch every error.

## What this is not

- **Construction, not field rates.** The base cases and the operators were written by the
  people who wrote the gates, and the round-2 fixes were written knowing which mutants
  passed. An error class nobody thought to generate is not measured, and the class balance is
  set by how many operators apply to how many cases, not by how often each error occurs in
  real output. These are regression numbers with a stated construction, like
  `PSH-Harness/benchmarks/bench_claims.py`, and CI refuses a regression against them
  (`--check`).
- **A benchmark with no survivors has stopped discriminating.** From here it guards against
  regressions. Measuring what the gates miss needs outputs nobody here wrote: real drafts,
  labelled by a reviewer (below), and new operators from the errors those drafts contain.
- **Same outputs, not the same model.** Every configuration judges identical outputs. The
  question "does a model under this governance write better science" also involves what the
  model writes when it knows it will be checked, which this does not measure; that is what
  the four-arm comparison in [comparison.md](comparison.md) is designed for.
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
`tests/test_tcm_domain_checks.py`, `PSH-Harness/tests/test_output_gate_citations.py`,
`PSH-Harness/tests/test_scope_round2.py`.

## 中文摘要

**要回答的问题：** 治理架构是否真能减少科学错误？哪一部分起作用？做法是在相同输出、相同工具、相同任务下，逐一开关治理组件。

**方法（科学变异测试）：**
- **基础案例：** 10 个审稿人会放行的输出，6 个中文、4 个英文。覆盖随机对照试验、队列研究、病例报告、体外实验、分子对接，以及《伤寒论》条文。
- **变异体：** 每个变异算子注入一种已知科学错误，共 26 类、121 个变异体。错误包括：
  - 人群、终点、对象外推，方向反转，确定性夸大；
  - 证据类型越级，例如对接、动物实验或经典条文被当作临床证据；
  - 用计划从句掩护疗效断言；
  - 近名药（制白附子冒充制附子、白首乌冒充何首乌），剂量错读 10 倍；
  - 路径上没有记录的作用方向；
  - 已撤稿、签名后被篡改、张冠李戴或凭空捏造的引用；
  - 中医药特有的五类：单一成分的证据推到整方（或反之）、一种炮制品的证据用于另一种（制附子→生附子）、把体外结果说成人体暴露下成立、同一研究经两个数据库被算作两项独立研究、把无人测量的结局说成“未见不良反应”“无毒”“与死亡无关”（未知当作阴性）。
- **闸门：** 均为运行时真实调用的检查。内核三个：证据摄入（标识绑定、签名复核）、输出闸门、许可类型检查。领域层两个：主张契约、快照路径发布检查。

**结果（10 个正确输出均无误拒）：**

| 配置 | 放行的错误 |
| --- | ---: |
| 无治理 | 121/121（100%） |
| 仅内核 | 7/121（6%） |
| 仅领域层 | 57/121（47%） |
| 完整治理 | 0/121（0%，95% 置信区间 0–3%） |

留一法：输出闸门独自拦下 42 个，证据摄入 10 个（均为被篡改的记录），主张契约 6 个，路径检查 1 个；许可检查不再有独有的拦截。

**两轮修补（均由该基准发现，改的都是运行时检查，而不是基准）：**
- 第一轮（19→10）：非临床句中的引用也要核查；补全中文临床词汇；主张正文中的近名药（CLM014）。
- 第二轮（10→0）：
  - 心血管死亡不等于全因死亡；
  - 「65岁及以上」等年龄表述能被解析；
  - 「长期服用X与…相关/有关」中的暴露因素 X 能被识别为主语；
  - 机制句必须得到所引记录的支持，所述对象也要与记录一致；
  - 「总是」「必然」等绝对化措辞被拒绝；
  - 《伤寒论》条文按记录编号（passage.shl_34）引用，可以查核，引文须与原文一致。
- 新增五项中医药领域检查：CLM015（成分与整方互推）、CLM016（炮制品互推）、CLM017（无人体暴露证据却声称在人体浓度下成立）、CLM018（同一来源被计为多项独立研究）、CLM019（未测量的结局被说成阴性：未知仍须是未知；预测找不到结合，不等于证明不结合）。

**局限：**
- 这是有明确构造的回归基准，不是真实错误率；第二轮修补是在已知哪些变异体漏网之后写的。
- 没有漏网者，意味着这个基准已不再有区分力，此后只用于防止回退；要发现新的漏洞，需要用真实模型输出（由审稿人标注）和由此提炼的新算子。
- CI 会拒绝任何回退。
