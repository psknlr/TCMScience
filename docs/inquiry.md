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
   outcomes is refused (`INQ150`), as is an entry the replay cannot apply. Recomputing the
   hash chain does not get an edited belief past this. It does not stop an edited
   prediction: the digests are not keyed, so whoever can write the stored file can change a
   sealed prediction, replay the outcomes and recompute every digest, and the trail loads.
   That a prediction was sealed in advance rests on who can write the store. In Studio the
   runtime keeps it and the agent reaches it only through the tool. A recorder (below) also
   writes each entry's digest to the kernel's audit chain, which loading does not consult.
   A saved inquiry names the version of the rules it was begun under (`engine`; none before
   version 2). One begun under earlier rules keeps the advice its trail recorded (each
   observation's purpose, rival and recommended step, and each plan), because the rules
   that chose it have changed since. Its belief, predictions and outcomes are recomputed as
   for any other, and what it concludes now follows the current rules.
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
   passed a **severe test** against every live rival and, against each rival, one such
   test has been **replicated**. A severe test would probably fail the leader if the rival
   were true, and probably pass it if the leader were (both at 0.8 by default), so passing
   one carries a likelihood ratio of at least 4. Both numbers come from the sealed
   predictions alone, not from the priors. A run of weakly informative confirmatory
   analyses raises a posterior and cannot meet this rule. A rival refuted by an outcome it
   gave probability 0 is tested by that analysis, and the refutation must be replicated
   like any other test, so one confident zero cannot end an inquiry. The replicate repeats
   it against the prediction the rival seals for the replicate, so a replicate needs a
   prediction from every explanation that predicted its original, refuted or not
   (`INQ110`), and the engine asks for it. A replicate that cannot carry the rival's
   prediction (its design cannot license the rival's claim) is not run as that test's
   replication. A test counts as replicated when more of its replicates favour the leader
   than do not: running replicates until one agrees does not replicate it. Every
   replication that went the other way is listed in the conclusion (`failed_replications`
   and the limitations), whatever the verdict.
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

## Running analyses together, and how sure the predictions were

**Batches.** An agent that can call several tools at once should not spend them on analyses
that would tell the same story. `Inquiry.batch(k)`, for k up to 4, starts with the engine's
next step, so a contradiction or a replication the leader needs still comes first. It then
adds, one at a time, the ready analysis that adds the most information per unit cost
*together with* those already chosen. That joint information is `joint_gain`: the expected
information of the set under the engine's own updates (the chain rule of mutual
information), which equals the average over every joint outcome applied by `observe`. A
replicate adds less to its original than its gain alone suggests, so a batch prefers an
analysis that tells a different story. The budget, the step limit and `min_gain` apply as
they do to single steps. `Inquiry.plan(k)` seals the batch into the trail before any of it
runs. Each of its analyses is then recorded as part of the engine's batch
(`Purpose.BATCH`), not as unrecommended. A replay recomputes the plan from the state it was
made in, so a plan written after the fact is refused (`INQ150`). An inquiry begun under
earlier rules keeps the plans it recorded (rule 1).

Measured on the eight scaffolds of the Studio chat ([below](#in-the-studio-chat)), in the
noisy worlds of their calibration (100 per rival, 2,400 in all). Every batch size starts
from the calibration's seeds, so the first row is the calibration itself. Outcomes are
drawn as analyses run, so a larger batch, which runs them in another order, meets other
draws: the rows share seeds, not worlds.
`BioScience-Harness/scripts/measure_inquiry_batches.py` prints this table:

| Analyses per round | Rounds (mean) | Cost (mean) | True rival accepted | Wrong rival accepted |
| --- | ---: | ---: | ---: | ---: |
| 1, the engine's next step | 3.97 | 8.88 | 1,300 | 7 |
| 2 | 2.27 | 9.71 | 1,237 | 6 |
| 3 | 1.72 | 10.91 | 1,162 | 5 |
| 4 | 1.37 | 10.34 | 1,150 | 6 |

Batches of two take 43% fewer rounds for 9% more cost. A batch is chosen before any of its
outcomes is known, so it cannot change course on them. It decides somewhat less often (5%
fewer true acceptances), and a wrong rival stays as rare. `tests/test_rivals.py` checks
both for every rival.

**A two-step lookahead was tried and is not used.** It chose each analysis by what it and
the best follow-up would yield together. In 960 noisy worlds (40 per rival) it accepted the
true rival 365 times, against 528 for the one-step choice: less often on seven scaffolds and
as often on the eighth. The engine stays one step ahead, or one batch.

**Sensitivity.** The posterior is conditional on the sealed predictions, and how sure they
are is the proposer's choice. `Inquiry.sensitivity(tempers)` replays the recorded outcomes
with every sealed prediction mixed with the uniform distribution over its outcomes,
`(1 − t)·q + t/n`. This keeps the outcome each explanation favours and shrinks every
likelihood ratio towards 1. A leader that holds as t grows is carried by which outcome each
explanation favoured. A leader that changes is carried by how sure the predictions were:
typically a prediction of 0 that refuted an explanation the other outcomes favoured. The
verdict is not recomputed, because tempered predictions are less severe by construction. Of
1,303 conclusions accepted in the worlds above, a recomputed verdict kept 217 at t = 0.1 and
none at t = 0.2, which measures the severity threshold rather than the evidence. The leader
held in all 1,303 at t = 0.1, 0.2 and 0.3, and its posterior fell by 0.013, 0.032 and 0.060
on average. Those worlds were drawn from the scaffolds' own predictions, which avoid zeros,
so that is the expected result. The check matters most for an inquiry an agent opens with
predictions of its own.

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

## In the Studio chat

The Studio agent runs the same engine through the core tool `research_inquiry` (catalog entry
`study.inquiry`, in the browser and on the runner). A model that answers a research question
in one breath reports the first explanation that fits. The tool makes it write down the
rivals and what each predicts first, and leaves belief, the next step and the stop to the
engine.

**Scaffolds.** `bioagent.research.rivals` writes eight questions TCM research asks often in
the form the engine needs. Each has rivals, tests, sealed predictions and the tools that
produce each test's result. `action: "scaffolds"` lists them, and `open` with a scaffold and
its values starts an inquiry. An agent can also open one from its own rivals, analyses and
predictions, and the engine's refusals apply to it unchanged. The core tool `research_routes`
names, for each kind of research question, the scaffold that decides between its explanations
and every source that can produce the evidence ([studio/docs/ROUTES.md](../studio/docs/ROUTES.md)).

| Scaffold | Question | Rivals | Tests | Who classifies an outcome |
| --- | --- | --- | ---: | --- |
| `pathway_signal` | a pathway signal: mechanism or artefact? | target · coverage · promiscuity | 7 | the agent, citing the run (the controls run on the runner only) |
| `formula_vs_constituent` | does the formula do more than its main constituent? | formula adds · constituent accounts · formula attenuates | 3 | the runtime, from `study.contrast_formula_monomer` |
| `combination` | more than each agent alone predicts? | synergy · independent · cytotoxicity | 3 | the runtime, from `study.combination` |
| `exposure` | does the bench activity happen at the exposure reached? | parent reaches · not reached · metabolite carries | 4 | the runtime, from `study.exposure_screen` |
| `efficacy` | benefit or bias? | effective · ineffective · bias | 4 | the runtime, from `native.meta_analysis` |
| `safety_signal` | is the adverse event the herb's? | causal · confounded · wrong material | 5 | the agent, citing the reports |
| `herb_drug_interaction` | does the herb change the drug? | PK · PD · none | 5 | the runtime from the published interval (clinical tests); the agent (in vitro) |
| `target_engagement` | does the compound bind the target? | binds · indirect · interference | 6 | the agent, citing the assays |

**What decides an outcome is sealed too.** An equivalence margin, the smallest important
benefit, a combination's reference model and a clinically relevant change are values of the
scaffold. They are written into the tests' titles, so they enter the trail with the
predictions. Data analysed under other criteria are refused, as is a trail whose stored
values do not match it.

**Who classified an outcome is in the trail.** With `data`, the tool the scaffold names runs
in the same call. Its arguments are validated first, and the runtime classifies the result
by the scaffold's rule, recording `runtime:<tool>:<digest>` as the evidence. Only a tool that
needs no approval, job or network runs this way, so nothing nested skips an approval. The same data
recorded again (a replicate that is not one) is refused. What is compared is the numbers the
classifier reads, so relabelled trials, an extra key or 1 written as 1.0 are still the same
data. Each trial's estimate stays with its standard error and each cell's numbers with their
keys, so the same numbers paired differently are other data. A test without a classifier
takes an `outcome` with `evidence_ref`, recorded as `agent:<ref>`. A replicate may not cite
its original's source, whoever classified either: a PMID or DOI is the same source however
it is written (with a label or without, as a URL, in full-width characters, with
punctuation or a note after it). The conclusion counts the outcomes each way. A test
with a classifier never takes a reported outcome, and neither does a replicate an agent adds
to it: that replicate is classified by its original's rule, from data.

**The bar can be raised, never lowered.** A `rule` may ask for a higher acceptance
threshold, severity or catch-all floor, a lower alarm, or fewer steps. It cannot turn
replication off or loosen anything. Inquiries are kept per project, in
`<state>/<project>/inquiries/<id>.json`. In the browser that is `/persist` in IndexedDB,
synced around each call under the same lock as governed runs. Each load replays the trail
(`INQ150`).

**Withdrawing is a finding.** A test nobody can run is withdrawn with the reason. The
conclusion lists it under `to_decide`, with every unrun test that would be severe against a
live rival. Those are the studies that would settle the question. `pathway_signal`'s
random-herb and rewiring controls are not Studio tools. In the chat they are withdrawn,
so its conclusion says the test against promiscuity is missing. A safety signal does not
wait for acceptance: a provisional or undetermined verdict is not reassurance.

**Running analyses at once.** `action: "plan"` with a `size` of 2 to 4 seals the engine's
batch. It returns each analysis with the tool that runs it and the bits it adds, and the
agent then runs them in parallel and records each. An empty batch records nothing and says
what the next step is instead. Every conclusion carries `sensitivity`: the leader and the
belief at t = 0.1, 0.2 and 0.3, and how far the leader holds. A leader that changes is the
first limitation after the scaffold's warnings, and the summary says so in one line.

**What the scaffolds were checked for.** `tests/test_rivals.py` plants each rival's most
likely outcomes for every test, and the engine accepts that rival in all 24 such worlds. It
also checks that every pair of rivals has a severe test and a replicate of it. In noisy
worlds the outcomes are drawn at random from the true rival's own sealed predictions, 100
worlds per rival. There a wrong rival was accepted 7 times in 2,400 worlds, at most 2 for
any one rival (the test allows 5). The true one was accepted in 9 to 90 of 100 worlds,
depending on the rival. The rest ended undecided, with the studies that would decide them.
`efficacy`'s "ineffective" is the hardest: 9 of 100, because an absent benefit is seldom
shown severely. These worlds come from the scaffolds' own predictions. They check that the
numbers are consistent and that a wrong answer is rare when the predictions are right. They
say nothing about whether the predictions are right.

**A conclusion is not a release or a recommendation.** Its certainty is capped by the
designs that bore on it, as everywhere in this document. A claim about patients still goes
through `tcm_applicability` and the release gate, and the tool's summary says so.

## Refusals

| Code | Refused |
| --- | --- |
| INQ101 | a closed world: the catch-all opens below its floor |
| INQ102 | fewer than two named explanations |
| INQ103 / 104 / 105 | a malformed explanation, analysis or stopping rule |
| INQ110 | running an analysis before every live explanation it bears on, and for a replicate every explanation that predicted its original, has a sealed prediction |
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
- **Information gain looks one step ahead.** That step is one analysis, or one batch
  chosen before any of its outcomes is known. A two-step lookahead did worse on the
  scaffolds ([above](#running-analyses-together-and-how-sure-the-predictions-were)).
- **Outcomes are discrete.** Each analysis declares an exhaustive set, and its executor
  classifies the result into one of them with a fixed rule. The classifier is code in the
  repository, not a model's judgement.
- **Execution is in-process here.** The pathway inquiry runs its analyses in this process
  and says so (`governed_execution: false`). The executor is the port where the kernel's
  execution broker goes, as it does in the research loop.

Tests: `PSH-Harness/tests/test_scientist_inquiry.py` (engine), `…_property.py` (information
gain bounds, belief as a martingale, exact hold-fixed for unlicensed explanations, replay,
joint information against the engine's own updates), `…_batch.py` (joint information,
batches, plans and their replay, sensitivity), `…_recorder.py` (world model, audit,
write-ahead refusal),
`BioScience-Harness/tests/test_research_inquiry.py` (planted worlds), `…/test_rivals.py`
(the scaffolds and their classifiers), `…/test_inquiries.py` (the per-project store) and
`studio/runner/tests/test_inquiry_tool.py` (the chat tool, in both runtimes).

## 中文摘要

**问题：** 内核已经决定智能体能运行什么、发布的结论能说什么。但两者之间还有一类决定：下一步做哪个分析、结果该让信念移动多少、何时可以停止。这类决定自主科研最常做，审稿人最难核查。

**做法：** `psh.scientist.inquiry` 把这些决定变成对预先承诺的计算。分工如下：
- **模型负责提出：** 竞争性解释、先验、可做的分析，以及每个解释对每个分析结果的预测。
- **内核负责决定：** 按期望信息增益（每单位成本）选择下一个分析；只用预先封存的预测做贝叶斯更新；按停止规则决定何时结束；结论的强度由证据许可表封顶，不由后验概率决定。

**五条规则：**
- **预测先于结果封存。** 事后声明的似然被拒绝（INQ115），已封存的预测不可修改（INQ116）。审计轨迹可以重放，信念无法复算、或某一条目无法重放即被拒绝（INQ150）。重算哈希链不能让改过的信念通过，但哈希不带密钥：能写入存储文件的人可以改动已封存的预测，重放结果并重算全部哈希，轨迹仍能加载。所以预测是否确实事先封存，取决于谁能写入存储。在 Studio 中由运行时保存，智能体只能通过工具访问；记录器另把每个条目的哈希写入内核审计链，但加载时不核对它。保存的探究注明它开始时所用的规则版本（`engine`；版本 2 之前没有这一字段）。按早期规则开始的探究，重放时沿用审计轨迹记录的建议（每次观察的目的、针对的对手和推荐的步骤，以及每个计划），因为选出这些建议的规则已经改变；其信念、预测和结果照常重算，现在的结论按现行规则得出。
- **证据类型不能许可的主张，其信念不能被它移动。** 例如对接结果不能携带对临床疗效的预测（INQ113）。该解释的概率被精确固定，等价于"这一观察与它无关"。
- **世界永不封闭。** 始终保留"以上皆非"兜底项，开局有概率下限（INQ101）。结果出乎所有已有解释的预料时，概率流向兜底项，超过警戒线后引擎要求提出新解释。新解释只能从兜底项的当前概率中分得份额，不能为它事后拟合的数据邀功。
- **后验高不足以停止。** 领先解释必须通过针对每个存活对手的严格检验：对手为真时大概率不利于领先者，领先者为真时大概率有利于它。针对每个对手，至少有一个这样的检验还须被重复。对手若被一个它给出概率 0 的结果否定，这一分析就算对它的检验，这一否定同样须被重复，所以一个过于自信的 0 不能结束探究。重复检验对照的是对手为重复检验封存的预测，所以凡对原检验作过预测的解释，无论是否已被否定，都须对重复检验作出预测（INQ110），引擎会要求补上；设计无法许可对手主张、因而无法带有其预测的重复检验，不作为该检验的重复来运行。一个检验只有在支持领先者的重复多于不支持的重复时才算被重复，反复做重复直到有一次一致并不算数。未能支持领先者的重复，无论裁决如何，都列在结论中（`failed_replications` 和局限）。
- **后验不是许可。** 十个计算机模拟分析即使使后验达到 0.99，也只许可一个"试探性的机制假说"。

**葛根芩连汤通路信号的应用：**
- **竞争解释：** 选择性作用（机制假说）、检测覆盖伪影、化合物"滥交"性。
- **七个分析：** 全注释背景富集（常规分析）、已检测蛋白背景富集、随机药材组合、度保持重连，后三者各带一个重复。
- **常规分析几乎不提供信息：** 三个解释都预测它会富集，所以开始前它只值 0.06 比特，已检测背景富集值 0.43 比特。
- **在三个已知答案的人工世界中：** 引擎三次都选出了正确解释。只有在机制确实为真的世界里，才发布一条（试探性）机制假说。所用网络药理运行次数分别为 45、14、45 次，全部检查需要 67 次。
- **人工世界的局限：** 它们展示机制、防止回归，不证明引擎能正确判断真实问题。

**同时运行与敏感性：**
- **批次：** `Inquiry.batch(k)`（k 最多为 4）以引擎的下一步开头，再逐个加入与已选分析合起来每单位成本信息量最多的分析。合起来的信息量（`joint_gain`）按引擎自身的更新计算；重复检验对原检验补充的信息少于它单独的增益，所以批次更倾向讲述不同故事的分析。`plan` 在运行前把批次封存进审计轨迹，重放时重新计算，事后写入的计划被拒绝（INQ150）；按早期规则开始的探究沿用其记录的计划。
- **实测：** 在八套模板的 2,400 个噪声世界中（各批次大小都从模板校准的随机种子开始，所以第一行就是校准本身；结果在分析运行时抽取，批次越大运行顺序越不同，抽到的结果也不同，因此各行共享的是种子而非世界），两项一批使轮数减少 43%，成本增加 9%。批次在看到任何结果之前选定，无法随结果调整，因此真实解释被接受的次数少 5%，错误解释被接受同样罕见（6 对 7 次）。表格由 `BioScience-Harness/scripts/measure_inquiry_batches.py` 生成。
- **两步前瞻不采用：** 在 960 个噪声世界中，两步前瞻接受真实解释 365 次，一步选择为 528 次（七套模板更少，一套持平）。
- **对预测把握程度的敏感性：** `sensitivity` 把每个封存的预测与其结果上的均匀分布按权重 t 混合，再重放已记录的结果：各解释偏向的结果不变，似然比向 1 收缩。领先者随 t 增大保持不变，说明结论来自各解释偏向哪个结果；领先者改变，说明结论来自预测有多确定，常见情形是一个为 0 的预测否定了其他结果都支持的解释。裁决不重算：放宽的预测按构造就不再严格，重算只反映严格度门槛。在上述世界中被接受的 1,303 个结论，领先者在 t = 0.1、0.2、0.3 下全部不变。

**记录与执行：**
- **记录到世界模型：** 设置 `state_dir` 后，推理过程经持久化网关写入 PSH 世界模型，包括假说、预注册方案、观察、支持/反驳边、科学循环和审计事件。写入先于信念更新，写入被拒则信念不变。
- **执行方式：** 分析在本进程内执行，并如实标注 `governed_execution: false`；执行器端口留给内核的执行代理。

**在 Studio 对话中（`research_inquiry`）：**
- **工具：** 核心工具 `research_inquiry`（目录条目 `study.inquiry`，浏览器和 Runner 均可运行）让对话中的智能体使用同一引擎。它先写下竞争解释和各自的预测，信念、下一步和停止由引擎决定。
- **八套模板：** `bioagent.research.rivals` 为常见问题准备了竞争解释、检验、封存的预测和产生结果的工具，包括通路信号、复方与主要成分、联合用药、体内暴露、疗效、安全信号、药材–药物相互作用、靶点结合。`action: "scaffolds"` 列出全部模板，也可以自己提出竞争解释。
- **判定标准同样封存：** 等效界值、最小重要获益、联合作用的参照模型、临床相关变化都是模板参数，写入检验标题，与预测一起进入审计轨迹。按其他标准分析的数据被拒绝。
- **谁判定的结果写在轨迹里：** 提供 `data` 时，模板指定的工具在同一次调用中运行，由运行时按模板规则判定，证据记为 `runtime:<工具>:<摘要>`。同一数据不能记录两次：比较的是判定器读取的数字，改换试验标签、多加字段或把 1 写成 1.0 仍是同一数据；每个试验的估计值与其标准误、每个孔的数值与其字段保持配对，同样的数字换一种配对则是另一组数据。没有判定器的检验由智能体报告结果，必须给出 `evidence_ref`，记为 `agent:`；重复检验不能引用原检验的来源（不论由谁判定；PMID 或 DOI 无论怎样书写，带不带标签、写成网址、用全角字符或后附标点、说明，都视为同一来源）。智能体为运行时判定的检验添加的重复检验，同样按原检验的规则从数据判定，不接受报告的结果。结论分别计数两类结果。
- **门槛只能提高：** `rule` 可以提高接受阈值、严格度或兜底下限，降低警戒线或减少步数，但不能关闭重复要求，也不能放宽任何一项。探究按项目保存，每次加载都重放审计轨迹（INQ150）。
- **撤回也是发现：** 无法运行的检验连同原因撤回，结论在 `to_decide` 中列出它们，以及其他能严格区分存活解释、尚未运行的检验，也就是能解决问题的研究。安全信号不等待接受：暂定或未决不等于安全。
- **同时运行：** `action: "plan"`（`size` 为 2 到 4）封存引擎的批次，并给出每项分析的运行工具和增加的信息量；智能体并行运行后逐项记录。每个结论附带 `sensitivity`；领先者改变时，这一点排在模板警示之后的第一条局限，摘要也用一句话说明。
- **模板检验了什么：** 在每个解释最可能的结果下，24 个解释全部被接受；每对解释都有严格检验及其重复。按真实解释自身的预测随机抽取结果时（每个解释 100 个世界），错误解释被接受 7 次（共 2,400 个世界，单个解释最多 2 次），真实解释被接受 9 到 90 次不等，其余以未决结束并给出能解决问题的研究。这只检验数字的自洽，不检验预测本身是否正确。
- **结论不是发布，也不是建议：** 结论强度受证据设计封顶；关于患者的主张仍须经过 `tcm_applicability` 和发布关卡。
