# The inquiry: what to find out next, and what the answer licenses

The kernel already decides what an agent may *run*, and what a released claim may *say*.
Between the two sits the decision an autonomous scientist makes most often and a reviewer
checks least: **which analysis to run next, how far its result should move belief, and when
enough is known to stop.** Left to a model, all three drift the same way: towards the
analysis that will confirm, the update that flatters, and the stop that comes as soon as the
story is good.

`psh.scientist.inquiry` turns them into computations over commitments made in advance:

```
explanations + catch-all ──> sealed predictions P(outcome | explanation, analysis)
        │                                   │
        │      expected information gain <──┘
        v                    │
     belief <── Bayes ── observe <── run the analysis the engine chose
        │
        ├── leader above threshold? ── severe test against every live rival
        │                                └── replicated?
        v
   conclusion ── capped by the licensing table, not by the posterior
```

The model proposes the explanations, their priors, the analyses, and what each explanation
predicts each analysis will show. The engine decides which analysis is worth running, what
an outcome does to belief, when to stop, and how strongly the result may be stated. **The
model may say what it expects; it does not decide what was learned.**

## Five rules

1. **Predictions are sealed before the outcome exists.** A likelihood declared after the
   result is an accommodation, and is refused (`INQ115`). A sealed one cannot be changed
   (`INQ116`). The serialised inquiry *is* its hash-chained trail. Loading it replays every
   step, and a recorded belief that does not recompute from the sealed predictions and the
   outcomes is refused (`INQ150`). Rewriting the hash chain does not get past this.
2. **A design that cannot license a claim kind cannot move belief in it.** A docking run may
   not carry a prediction about clinical efficacy (`INQ113`). The engine holds such an
   explanation's probability fixed by giving it the predictive distribution of the others,
   which is the exact Bayesian statement that the observation is irrelevant to it. The table
   is `psh.sir.values.LICENSING`, the same one the compiler and the release gate enforce.
   Licensing now constrains the likelihood as well as the claim.
3. **The world is never closed.** Every inquiry carries a catch-all, "none of the named
   explanations", with a floor on its prior (`INQ101`) and a fixed uniform prediction. When
   outcomes surprise every named explanation, mass moves to the catch-all, and past an alarm
   the engine asks for new explanations instead of more analyses. A new explanation is carved
   out of the catch-all's *current* mass, so it gets no credit for the data it was invented
   to fit.
4. **A high posterior is not enough to stop.** The leader is accepted only after it has
   passed a **severe test** against every live rival and each such test has been
   **replicated**. A severe test would probably fail the leader if the rival were true, and
   probably pass it if the leader were (both at 0.8 by default), so passing one carries a
   likelihood ratio of at least 4. Both numbers come from the sealed predictions alone, not
   from the priors. A run of weakly informative confirmatory analyses raises a posterior and
   cannot meet this rule.
5. **The posterior is not a licence.** How strongly a conclusion may be stated is capped by
   the designs of the observations that bore on it (`MAX_CERTAINTY`) and by the claim kind
   those designs reach (`LICENSING`). Ten in-silico analyses at a posterior of 0.99 license
   a *tentative mechanism hypothesis*, which is what one of them licenses.

An analysis that cannot run at all (the data it needs are absent) is **withdrawn** with its
reason, and never given an outcome. The conclusion then lists what could not be run.

## A pathway signal in 葛根芩连汤: three explanations, seven analyses

`bioagent.research.inquiry` applies the engine to the question the network-pharmacology
validation suite exists for: *the formula's measured targets concentrate in a pathway; which
explanation of that is true?* Each robustness check is the test of one rival explanation, so
the rivals come first.

| Explanation | Kind | What it says |
| --- | --- | --- |
| `target` | mechanism hypothesis | the constituents act selectively on the pathway |
| `coverage` | artefact | the concentration is which proteins were assayed |
| `promiscuity` | artefact | the pathway's proteins are hit by many compounds, so any formula reaches it |

The predictions below are sealed before anything runs. Each row reads "if this explanation is
true, the analysis comes out …".

| Analysis (runs) | Outcomes | `target` | `coverage` | `promiscuity` |
| --- | --- | --- | --- | --- |
| whole-Reactome enrichment, the usual analysis (1) | enriched / not | .90 / .10 | .90 / .10 | .90 / .10 |
| assayed-background enrichment (1), and a reseeded replicate (1) | enriched / not | .85 / .15 | .05 / .95 | .85 / .15 |
| random herb combinations (21), and a second draw (21) | specific / not specific / not enriched | .80 / .10 / .10 | .05 / .05 / .90 | .10 / .80 / .10 |
| degree-preserving rewiring (11), and a second draw (11) | beyond wiring / explained by degree / not enriched | .80 / .10 / .10 | .05 / .05 / .90 | .40 / .50 / .10 |

Every explanation predicts the usual analysis comes out enriched, so before anything runs it
is worth 0.06 bits against 0.43 for the assayed background. The 葛根芩连汤 case study in the
README made that argument with data; here the engine computes it before spending a run.

### On planted worlds, where the answer is known

`bioagent.research.planted` builds three synthetic worlds on the real 葛根芩连汤 herb
layer, each making one explanation true. The inquiry runs unchanged on each:

| World | True explanation | Verdict | Leader | Posterior | Mechanism claims released | Network-pharmacology runs used / all |
| --- | --- | --- | --- | ---: | ---: | ---: |
| selective | `target` | accepted | `target` | 0.974 | 1 | 45 / 67 |
| coverage | `coverage` | accepted | `coverage` | 0.976 | 0 | 14 / 67 |
| promiscuous | `promiscuity` | accepted | `promiscuity` | 0.974 | 0 | 45 / 67 |

In the selective world the released claim is the pipeline's own, which had to pass its
snapshot-edge release check as well. It is licensed as a mechanism hypothesis and stated at
most *tentatively*. In the other two the conclusion is a finding about the analysis, and no
mechanism claim leaves. In the coverage world the first analysis already refutes the
mechanism, and the engine stops after 14 runs. Without control herbs the specificity test is
withdrawn, `target` cannot be accepted, and nothing is released.

These are fixtures: three worlds built to have an answer. They show the mechanics and catch
regressions. They are not evidence that the engine decides real questions correctly.

## Recording

With `state_dir`, `psh.scientist.WorldModelRecorder` writes the inquiry into the kernel's
world model as it runs, through the persistence gateway and under the run's authority:

- each explanation becomes a `Hypothesis` record, with `ALTERNATIVE_TO` edges to its rivals;
- each analysis gets a `Protocol` preregistered against every explanation it predicts for,
  and an experiment node with `TESTS` edges;
- each observation becomes an `Observation` under that protocol, with `CORROBORATES` and
  `REFUTES` edges, and one turn of the `ScientificCycle` that refuses a turn whose
  predictions were not sealed first;
- each step gets an `inquiry_*` audit event.

The recorder is called **before** the inquiry's state moves. A refusal by the gateway (an
envelope without `PERSISTENT` authority, for instance) leaves belief where it was.
`ScientificWorldModel.briefing()` then shows a later reader the refuted explanations first,
so nobody proposes them again next month.

## Use

```bash
cd BioScience-Harness
python scripts/run_inquiry.py --planted all --out RUN/inquiry            # the table above
python scripts/run_inquiry.py --snapshots SNAP --ledger SNAP/audit/snapshots.jsonl \
    --lock RUN/snapshot_lock.json --pathway reactome:R-HSA-1234567 --out RUN/inquiry \
    --controls 50 --rewirings 20 --state RUN/state
```

```python
from psh.scientist import Inquiry, Explanation, Analysis, Role, run_inquiry, Executed
from psh.sir.values import ClaimKind, StudyDesign

inquiry = Inquiry("…", [Explanation("target", "…", ClaimKind.MECHANISM_HYPOTHESIS, prior=0.3),
                        Explanation("coverage", "…", role=Role.ARTEFACT, prior=0.3)],
                  [Analysis("assayed", "…", StudyDesign.IN_SILICO, ("enriched", "not_enriched"))])
inquiry.declare("assayed", {"target": {"enriched": .85, "not_enriched": .15},
                            "coverage": {"enriched": .05, "not_enriched": .95}})
conclusion = run_inquiry(inquiry, lambda analysis, step: Executed(run(analysis)))
conclusion.verdict, conclusion.claim_kind, conclusion.licensing, conclusion.certainty
```

## Refusals

| Code | Refused |
| --- | --- |
| INQ101 | a closed world: the catch-all opens below its floor |
| INQ102 | fewer than two named explanations |
| INQ103 / 104 / 105 | a malformed explanation, analysis or stopping rule |
| INQ110 | running an analysis before every live explanation it bears on has a sealed prediction |
| INQ111 | an unknown analysis or explanation |
| INQ112 | a prediction that is not a distribution over the declared outcomes |
| INQ113 | a prediction from a design that cannot license the explanation's claim kind |
| INQ114 | a prediction for the catch-all |
| INQ115 / 116 | a prediction after the outcome, or a change to a sealed one |
| INQ130 / 131 | an outcome nobody declared; an analysis observed twice |
| INQ132 | exceeding the declared budget or step limit |
| INQ133 | running a withdrawn analysis, or withdrawing an observed one |
| INQ140 | new explanations that would close the world |
| INQ150 | a trail that does not recompute |

## What this is not

- **The likelihoods are commitments, not measurements.** The posterior is conditional on
  them, and every conclusion says so. A proposer who seals bad predictions gets a bad
  posterior. The engine guarantees that the predictions were made in advance and applied as
  sealed. It does not guarantee that they were good.
- **Information gain is myopic.** It looks one analysis ahead.
- **Outcomes are discrete.** Each analysis declares an exhaustive set, and its executor
  classifies the result into one of them with a fixed rule. The classifier is code in the
  repository, not a model's judgement.
- **Execution is in-process here.** The pathway inquiry runs its analyses in this process
  and says so (`governed_execution: false`). The executor is the port where the kernel's
  execution broker goes, as it does in the research loop.

Tests: `PSH-Harness/tests/test_scientist_inquiry.py` (engine), `…_property.py` (information
gain bounds, belief as a martingale, exact hold-fixed for unlicensed explanations, replay),
`…_recorder.py` (world model, audit, write-ahead refusal), and
`BioScience-Harness/tests/test_research_inquiry.py` (planted worlds).

## 中文摘要

**问题：** 内核已经决定智能体能运行什么、发布的结论能说什么。但两者之间还有一类决定：下一步做哪个分析、结果该让信念移动多少、何时可以停止。这类决定自主科研最常做，审稿人最难核查。

**做法：** `psh.scientist.inquiry` 把这些决定变成对预先承诺的计算。分工如下：
- **模型负责提出：** 竞争性解释、先验、可做的分析，以及每个解释对每个分析结果的预测。
- **内核负责决定：** 按期望信息增益（每单位成本）选择下一个分析；只用预先封存的预测做贝叶斯更新；按停止规则决定何时结束；结论的强度由证据许可表封顶，不由后验概率决定。

**五条规则：**
- **预测先于结果封存。** 事后声明的似然被拒绝（INQ115），已封存的预测不可修改（INQ116）。审计轨迹可以重放，信念无法复算即被拒绝（INQ150）。
- **证据类型不能许可的主张，其信念不能被它移动。** 例如对接结果不能携带对临床疗效的预测（INQ113）。该解释的概率被精确固定，等价于"这一观察与它无关"。
- **世界永不封闭。** 始终保留"以上皆非"兜底项，开局有概率下限（INQ101）。结果出乎所有已有解释的预料时，概率流向兜底项，超过警戒线后引擎要求提出新解释。新解释只能从兜底项的当前概率中分得份额，不能为它事后拟合的数据邀功。
- **后验高不足以停止。** 领先解释必须通过针对每个存活对手的严格检验：对手为真时大概率不利于领先者，领先者为真时大概率有利于它。每个这样的检验还须被重复。
- **后验不是许可。** 十个计算机模拟分析即使使后验达到 0.99，也只许可一个"试探性的机制假说"。

**葛根芩连汤通路信号的应用：**
- **竞争解释：** 选择性作用（机制假说）、检测覆盖伪影、化合物"滥交"性。
- **七个分析：** 全注释背景富集（常规分析）、已检测蛋白背景富集、随机药材组合、度保持重连，后三者各带一个重复。
- **常规分析几乎不提供信息：** 三个解释都预测它会富集，所以开始前它只值 0.06 比特，已检测背景富集值 0.43 比特。
- **在三个已知答案的人工世界中：** 引擎三次都选出了正确解释。只有在机制确实为真的世界里，才发布一条（试探性）机制假说。所用网络药理运行次数分别为 45、14、45 次，全部检查需要 67 次。
- **人工世界的局限：** 它们展示机制、防止回归，不证明引擎能正确判断真实问题。

**记录与执行：**
- **记录到世界模型：** 设置 `state_dir` 后，推理过程经持久化网关写入 PSH 世界模型，包括假说、预注册方案、观察、支持/反驳边、科学循环和审计事件。写入先于信念更新，写入被拒则信念不变。
- **执行方式：** 分析在本进程内执行，并如实标注 `governed_execution: false`；执行器端口留给内核的执行代理。
