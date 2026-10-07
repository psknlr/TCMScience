# The four-arm comparison: protocol

> **Status: no model has been run, and this repository contains no result of this
> comparison.** What exists is the harness (`bioagent.benchmarks.comparison`), its
> command (`scripts/run_comparison.py`), its tests, and two synthetic fixture tasks run by
> a scripted client. Every report built from those says **Not a result** at the top. A
> result needs three things this repository does not have: tasks with gold annotations
> written by people who did not write the gates, a model and credentials chosen by whoever
> runs the experiment, and blinded reviewers.

## The question

The [governance ablation](governance-ablation.md) puts identical outputs through the gates
with each part switched on or off. It shows which gate catches which error. It cannot show
whether a model writes better science inside the governance, because that also depends on
what the model writes when it knows it will be checked, and on what checking costs in
correct claims withheld.

This comparison asks that question with everything else held fixed: the same model, the
same sources, the same budget. Each arm adds one layer of what TCMScience provides.

## Arms

| Arm | What the model is asked for | Checked | Rounds |
| --- | --- | --- | --- |
| **A** | prose findings with citations, then next steps | no: everything is released | 1 call |
| **A+** | as A, then one ordinary self-review ("review your answer for errors of scope, wording, citation and study design") | no | 2 calls |
| **B** | TCMScience's structured claims: kind, subject, population, outcome, direction, certainty, citation | no | 1 call |
| **C** | as B, told that claims are checked; the runtime's gates are applied and a failing claim is withheld | yes | 1 call |
| **D** | as C, plus rival explanations and contradicting evidence, and one revision round in which the withheld claims and their reasons go back to the model | yes | up to 2 calls |

C and D are checked by the gates the ablation measures, called the way the runtime calls
them:

- ingest, with identifier binding and the signature re-checked;
- the kernel's output gate;
- licensing, the compiler's type check;
- the claim contract, the CLM codes;
- the release check over snapshot paths.

A claim whose citation names none of the task's sources is withheld before these gates run.

**A+ exists because of D.** D gets a second call and A, B and C do not. Without A+, any
advantage D shows could simply come from that extra call. A+ spends the same second call on
an ordinary self-review. The comparison D − A+ asks whether rival explanations and
reason-giving refusal do better than plain review at the same budget. A+ always uses its
second call; D uses it only when something was withheld. The calls each arm actually
spent are reported next to its results.

## What is held fixed

- **Model.** One model id serves every arm. `run` requires `--model` and has no default:
  which model is tested is the experiment's decision, and it is recorded in
  `manifest.json` with the SDK version and the date.
- **Request settings.** Effort, thinking and the like are passed once (`--params`) and are
  identical for every arm.
- **Sources.** Every arm's prompt carries the same source block: identifier, design,
  subject, population, outcome and text, in the same words.
- **Tools.** No arm has tools or retrieval, so no arm can see more than the others. The
  sources are in the prompt.
- **Budget.** `max_tokens` is the same per call. The number of calls is fixed by each
  arm's rounds, as in the table.
- **Tasks.** The tasks file is fixed before the run; its SHA-256 goes in the manifest.

## Outcomes

All outcomes are counted per task. Repeats are averaged within a task, so a task counts
once however often it is run.

| Dimension | Metric | Primary? |
| --- | --- | --- |
| Correctness | errors released: claims that gold or a reviewer judges wrong | primary |
| Correctness | correct claims released | co-primary |
| Utility | coverage: the share of the task's expected findings released correctly | co-primary |
| Constraint cost | withheld claims, and of them those gold licenses (correct claims the gates lost); calls; output tokens | secondary |
| Evidence quality | released claims with no citation; released claims citing an identifier that is none of the sources given | secondary |
| Reproducibility | stability: across repeats of a task, the mean pairwise Jaccard similarity of what was released (as gold and reviews identify it) | secondary, needs repeats ≥ 2 |
| — | replies that could not be parsed | reported |

Rival explanations and contradicting evidence (arm D) are recorded in the transcripts and
described. They have no gold, so no score is computed for them.

## Judging claims

1. **Gold first.** Each task carries gold claims written by an annotator: phrases that
   identify a claim the sources license, and phrases that identify an error, such as a
   population, outcome or level the sources do not reach. A claim matching an error phrase
   is an error, even if it also matches a licensed one. Matching ignores case, spaces and
   punctuation; `|` separates alternatives.
2. **Blinded review for the rest.** A released claim that gold does not decide is exported
   with its arm hidden and the order shuffled (`export`). The reviewer sees the question,
   the claim, its citation and the cited source's text, and marks it `correct`, `error` or
   `not_a_claim` (for a prose answer's framing sentences). The key that maps items back to
   arms (`key.jsonl`) stays with the person running the experiment until the verdicts are
   in. An identical claim from two arms is reviewed once.
3. **Unadjudicated claims are reported, never guessed.** A verdict outside the three words
   is an error in the review file.

Gold phrase matching will miss some paraphrases. That is why review exists. The
unadjudicated column shows how much is left to it.

## Statistics

- **Paired by task.** Every arm runs on the same tasks. A difference is computed per task,
  then averaged.
- **Resampled by independent unit.** Each task names its unit: the dataset or primary study
  it rests on. The 95% interval of a difference comes from a cluster bootstrap over units,
  not over tasks or claims. One paper behind twenty tasks counts as one unit, and with a
  single unit no interval is reported.
- **Five planned comparisons**, each one layer apart:
  - B − A: the structured representation;
  - C − B: being checked, and the gates;
  - D − C: counter-evidence and revision;
  - D − A+: counter-evidence against ordinary review at the same budget;
  - D − A: the whole system.

  Anything else is exploratory and must be labelled so.

## What a reportable run needs

These are the minimum this protocol sets before any number from it is reported as a
result:

- **Two real scenarios**, for example:
  - a formula's efficacy and mechanism evidence (葛根芩连汤 in type 2 diabetes);
  - an herb's safety signal (何首乌 and liver injury).

  Sources must be real published records with identifiers.
- **At least 30 tasks from at least 15 independent units**, with gold written before any
  model output is seen, by annotators who did not write the gates or the prompts. A second
  annotator labels a sample so agreement can be reported.
- **Three repeats per task and arm**, so that stability can be measured.
- **Pre-registration of the run.** The tasks file, its hash, the arms, the model id, the
  request settings and the number of repeats are fixed and published before the run. The
  manifest is published with the results, and so are the transcripts.

## Threats to validity

- **Same authors for gates and prompts.** The gates, the claim format and the arm prompts
  were written by the same people. C and D are told what the gates check, and that is part
  of the treatment, not a leak. But a format that suits the gates may suit the authors'
  own sense of a good claim. Gold written by others is the guard.
- **The structured format can cost the model.** Asking for JSON can lower what a model
  writes. B − A measures this directly. It is not assumed away.
- **The gates' own errors.** A correct claim the gates withhold counts against C and D as
  `withheld_licensed`. It is not hidden.
- **Model drift.** A model id and date pin a run, but not forever. Results are for the
  model named in the manifest.
- **"Same tools" means no tools.** The comparison does not measure how an agent searches.
  Every arm gets the same sources.

## How to run

```bash
cd BioScience-Harness

# the harness end to end, offline: a scripted client on the fixture tasks (not a result)
python scripts/run_comparison.py selftest --out RUN/selftest

# an experiment: you name the model; there is no default
pip install anthropic          # the SDK resolves credentials itself
python scripts/run_comparison.py run --tasks tasks.jsonl --model MODEL_ID \
    --params '{"thinking": {"type": "adaptive"}}' --repeats 3 --out RUN/exp1

# blinded review of what gold does not decide; keep RUN/exp1/key.jsonl to yourself
python scripts/run_comparison.py export --run RUN/exp1 --tasks tasks.jsonl

# score, with the returned review file
python scripts/run_comparison.py score --run RUN/exp1 --tasks tasks.jsonl \
    --reviews RUN/exp1/review.jsonl
```

A task is one JSON object per line:

```json
{"id": "gqd-t2dm-01", "scenario": "formula_mechanism", "unit": "doi:10.xxxx/trial",
 "question": "...", "expected": ["trial"],
 "sources": [{"identifier": "doi:10.xxxx/trial", "design": "randomized_trial",
              "subject": "葛根芩连汤", "population": "成人2型糖尿病患者",
              "outcome": "糖化血红蛋白", "text": "..."}],
 "gold": [{"id": "trial", "all_of": ["葛根芩连汤", "糖化血红蛋白|HbA1c", "成人|adults"]},
          {"id": "children", "all_of": ["葛根芩连汤", "儿童|children"], "licensed": false}]}
```

Files:

- `BioScience-Harness/src/bioagent/benchmarks/comparison.py`
- `BioScience-Harness/scripts/run_comparison.py`
- `BioScience-Harness/tests/test_comparison.py`

## 中文摘要

**现状：尚未运行任何模型，本仓库中没有这项比较的任何结果。** 现有的是：

- 比较框架；
- 命令行；
- 测试；
- 两个合成夹具任务，由脚本客户端驱动。

凡由这些产生的报告，顶部都标注 **Not a result**。

**要回答的问题：** 消融实验用相同的输出比较各道闸门，但回答不了另一个问题：模型在治理之下，会不会写出更好的科学结论？这还取决于模型知道自己会被检查时写什么，也取决于检查误拦了多少正确结论。

**五个组：**

- **A**：普通散文作答，附引用，不做检查。
- **A+**：A 加一轮普通自我复核。
- **B**：TCMScience 结构化声明，不做检查。
- **C**：B 加告知会被检查，并施加运行时闸门。闸门依次为：摄取、内核输出闸门、许可、声明契约、发布路径检查。
- **D**：C 加竞争解释与反证，以及一轮带拒绝理由的修订。

**D − A+** 比较的是：同样多一次调用，反证与带理由的修订，是否优于普通复核。

**固定因素：**

- 同一模型：必须用 `--model` 指定，没有默认值，记录在 manifest 中。
- 同一请求参数。
- 同一来源文本。
- 不提供任何工具。
- 同一 `max_tokens` 预算。

**指标：**

- 正确性：放行的错误数（主要指标）、放行的正确声明数。
- 效用：预期发现的覆盖率。
- 约束成本：被拦下的声明中，gold 认可的有多少；调用次数；输出 token 数。
- 证据质量：无引用的声明；引用不在所给来源中的声明。
- 可复现性：重复运行之间放行内容的 Jaccard 相似度。

**判定与统计：**

- 先按 gold 判定，错误短语优先于正确短语。
- gold 判定不了的声明，隐去组别、打乱顺序后交给盲审。审阅者标注为正确、错误或非声明。
- 按任务配对比较，以独立单元（数据集、原始研究）做聚类自助法求 95% 区间。
- 计划比较共五项：B−A、C−B、D−C、D−A+、D−A。

**报告结果前至少需要：**

- 两个真实场景。
- 来自 15 个以上独立单元的 30 个以上任务。gold 由未参与编写闸门的人在看到任何模型输出之前写好。
- 每个任务每组重复 3 次。
- 事先公开任务文件哈希、模型与参数。
