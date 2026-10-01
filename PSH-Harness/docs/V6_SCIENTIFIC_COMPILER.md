# v0.6 — a scientific compiler, and a runtime that remembers what the project believes

The three previous releases answered one question: *may this agent do this?* They answered
it well enough that the honest description of v0.5.3 is a policy-enforced control plane
with a bounded, governed loop on top. This release answers a different question, and it is
the one a research system is actually for:

> **Is this science, and does the evidence reach the conclusion?**

Nothing here weakens the first answer. Every path still runs through `PlanValidator`,
`AgentLoopController`, `ExecutionBroker` and the release gate. What is added is a layer
**in front** of them that refuses a research programme before it spends anything, and a
layer **beside** them that remembers what the project concluded and why.

## 1. The shape

```
        SIRProgram                     a typed scientific programme:
            |                          question, hypotheses, predictions,
            |                          evidence types, analysis designs, effects
   ┌────────┴────────┐
   │ psh.compiler    │   structure  →  method  →  typecheck →  effects
   │                 │   infoflow   →  statistics → reproducibility → resources
   └────────┬────────┘
            |  lowering
     psh.runtime.plan.Plan            the object that already existed
            |
     PlanValidator → AgentLoopController → ExecutionBroker → TrustedKernel
            |
        Finalizer                     the release gate that already existed
            |
   ┌────────┴────────┐
   │ psh.scientist   │   hypotheses, predictions, falsifiers, observations,
   │ psh.durable     │   protocols, deviations — and the journal an amendment replays
   └─────────────────┘
```

Four new packages, one new service, and **no new execution path**:

| Package | What it is |
| --- | --- |
| `psh.sir` | The Scientific IR: scientific types, an effect system, the programme graph |
| `psh.compiler` | Eight passes over the IR, 71 diagnostic codes, and lowering to `Plan` |
| `psh.scientist` (extended) | The world model and the scientific cycle, over the existing record ledger |
| `psh.durable` | An append-only workflow journal and incremental recompute |
| `psh.runtime.scientific` | `ScientificRunService`: compile, run, release |
| `psh.compiler.bridge` | The seam to `psh.workflow`, so a programme is checked by both |

`psh.scientist` is the one that was already there. `ScientificLedger` writes the records —
hypotheses, protocols, observations, content-hashed and governed; this release adds the
*relational* half beside it, and writes no records of its own. See §8.

## 2. Why an IR rather than a language

ZCode's dynamic workflow compiles a TypeScript programme: the agent writes a script, a
virtual TypeScript host typechecks it against an embedded facade, and a taint fixpoint plus
a temporal walk recover the dependency structure from the AST. It is a good architecture
and its central claim is the right one —

> the model writes scripts, the compiler recovers rigor.

Adopting it wholesale would bind every analysis to one surface syntax. A research plan
arrives as a model's JSON, a Python builder, a declarative file, or an imported CWL
workflow, and those should not each need their own compiler. So the analysable object is
the **IR**, frontends produce it, and every pass reads only `psh.sir`. A CWL importer is a
frontend, not a second compiler.

What *is* adopted, deliberately:

| From ZCode's `@zcode/dynamic-workflow` | Here |
| --- | --- |
| Compiler in front of an LLM-authored workflow | `psh.compiler`, eight passes |
| Taint fixpoint over the workflow | `psh.compiler.infoflow`, four dimensions |
| Static fan-out cardinality | `psh.compiler.resources`, `RES605`/`RES606` |
| Diagnostics written to be repaired by the author | every code carries a remedy |
| Journal replay, content-keyed rather than positional | `psh.durable.journal` |
| Amend a partially executed workflow | `psh.durable.delta` |
| Purity of the analysis package | `test_the_compiler_holds_no_execution_path` |

## 3. The scientific type system

An ordinary agent type system knows `str`, `float`, `DataFrame`, `File`. That vocabulary
cannot express the distinction this harness exists to keep:

```
Evidence[design=animal, subject=animal, outcome=viral_entry]
      ──X──>  Claim[kind=efficacy, subject=human]
```

`psh.evidence.scope` already answers this — about a *sentence*, after the run produced it.
By then the analysis has run and the budget is spent; the only remedy left is to refuse the
output. `psh.compiler.typecheck` asks the same question of the **programme**, where the
remedy is to weaken the claim or add the study that would license it.

Two decisions are load-bearing.

`BioScience-Harness` uses it: `bioagent.psh.epistemics` maps the TCM evidence tiers
(经典记载 → 系统评价) onto `StudyDesign` and `ClaimKind`, so a TCM relation can be ruled on
by the matrix as well as by the tier ladder. The two disagree in exactly three places —
`attribution`, `traditional_use` and `safety_signal` — and all three have one shape: a text
is a legitimate record of something, and a ladder whose rungs are clinical designs has no
rung for "the 伤寒论 says so". The divergences are named and tested rather than reconciled.

**The matrix is two-dimensional, not a ladder.** A single evidence ordering says a
classical text is the weakest evidence there is. That is true of a clinical efficacy claim
and false of an attribution: for *the Shanghan Lun prescribes 桂枝汤 for this presentation*
the classical text is the only thing that can license it, and a randomised trial cannot.
`LICENSING` is therefore a table over (design, claim kind), and `StudyDesign` has no
ordering at all.

**A mismatch is a finding.** Every rule returns `UNLICENSED` or `EXTRAPOLATED` with the
dimension that decided it. The same argument `scope.py` makes: an extrapolated claim reuses
its source's exact vocabulary, so any similarity measure scores it *higher* than a
cautiously worded true claim. Only structure sees it.

## 4. Effects, and authority that is derived rather than declared

A node declares what it *does* — `network.public`, `phi_read`, `model.public_remote`,
`wetlab_action` — and the compiler computes the destinations, risk tier and autonomy those
effects require, from one table. `PlanTask` asks an author to state authority directly,
which invites two failure modes: a step that asks for more than it needs, and a step that
asks for less than it uses and is refused at the gate half way through the run.

Declaring an effect is an **obligation, never a grant**. A node that under-declares is not
thereby permitted more, because the gateway still rules from the manifest at the call.

`SideEffectClass` replaces the boolean `idempotent`, because a boolean cannot distinguish
*safe to retry* from *safe to retry with the same key* from *never re-run, ask a human*.
It decides the lowered task's retry policy: a wet-lab order gets one attempt whatever it
asked for.

## 5. Information flow in four dimensions

`psh.labels` is already a lattice and the runtime already joins it at every edge. The pass
adds two things a runtime join cannot: it sees the whole path before anything runs, and it
carries three dimensions the sensitivity label does not — **provenance**, the set of
contributing **study designs**, and **licence terms**. The rule that makes it worth having
is the one about going down:

> A derived value's label is the join of its inputs. It goes down only through an
> explicit, named, policy-permitted declassification.

A count derived from a PHI cohort is PHI until somebody says, on the record, which process
de-identified it. That is the difference between a taint analysis and laundering.

One subtlety cost a false refusal before it was stated correctly: **a derived flow label is
a lower bound on sensitivity, not an upper one.** The runtime classifier may find more — a
public accession number is a public input to a tool that returns a chart note. So a task
ceiling is never taken from the derived label; it comes from the destination's own ceiling
and from what the author explicitly declared.

## 6. The statistical pass

The family with no counterpart in a coding agent's compiler, and the one that decides
whether an autonomous scientist produces findings or produces significant numbers. Fifteen
codes, each a question a methods reviewer asks:

| | |
| --- | --- |
| `STAT401/402` | how many tests, corrected how |
| `STAT403` | powered for what |
| `STAT404/405` | censored how, assumption checked |
| `STAT406/407` | features selected on which data, evaluated on what held out |
| `STAT408` | which subgroups, declared when |
| `STAT409` | compared against which null |
| `STAT410–415` | batch, missingness, intervals, replication, alpha, plausibility |

`STAT409` is the one worth naming. An independent null-model re-analysis of an autonomous
discovery system found part of its output did not beat a random baseline; a significant
value is not a finding until a permutation, a random feature set or a negative control has
been shown not to produce the same result. The pass refuses a discovery analysis that
declares no null.

The errors are the ones that *invalidate* a result — leakage, an uncorrected screen, a
survival analysis with no censoring rule, a discovery with no null. The rest are warnings,
because exploratory work legitimately starts without half of them and a compiler that
refuses exploration gets switched off. `CompileOptions.exploratory()` softens the
confirmatory family and leaves leakage an error.

## 7. Preregistration as an ordering

`REP506` is the rule that makes preregistration a property of the graph rather than a
promise about the author: an analysis that claims to follow a protocol must **depend** on
the protocol node, or it can run before the freeze. `Protocol.fingerprint()` content-hashes
the registered protocol, and `psh.scientist.deviations.grade()` compares the protocol that
ran against the one that was registered, returning departures **graded** rather than
listed: changing the primary endpoint or a stopping criterion is `CRITICAL`, changing the
statistical test or the sample-size assumptions is `MAJOR`, adding a covariate is `MINOR`.

The grading matters more than the detection. A run that reports "8 deviations" tells a
reader to go and read eight diffs; a run that reports "1 critical, 7 minor" tells them
which one decides whether to believe the result. `GradedDeviation` quotes no protocol
content — only the field name and the severity — so a deviation report carries no
preregistered text into a summary that may go somewhere the protocol may not.

`ScientificCycle` refuses `OBSERVED → PREREGISTERED` outright. Nobody preregisters after
the results on purpose; it happens when the plan is a list and somebody reorders it.

## 8. The world model

`ScientificLedger` already wrote the records: hypotheses, protocols and observations as
immutable, content-hashed nodes under the persistence gateway, each linked to what it
derives from. That is the archival half and it is complete. What it does not hold is the
**relational** half — and that is what turns a pile of records into something a project can
be asked a question about.

The WorkGraph gained one node kind (`EXPERIMENT`) and four edge kinds, on top of the record
kinds that were already there:

```
Hypothesis <──alternative_to──> Hypothesis
    ^                               ^
    ├──corroborates── Observation ──┘
    └──refutes────────┘
              ^
              └── tests ── Experiment
```

Three properties:

* **This layer writes no records.** Every hypothesis, protocol and observation is created
  by the ledger, so the gateway classifies it and the content hash covers it. The only
  things `ScientificWorldModel` writes are edges, which carry no content of their own, and
  `EXPERIMENT` nodes — a plan rather than a finding — which still go through
  `PersistenceGateway.commit_node`. A structural test keeps it that way.
* **A refutation is an event, and a negative result is a node kind.** A refuted hypothesis
  stays in the graph so nothing proposes it again next month — the file-drawer problem
  solved in the data model rather than by asking people to be diligent.
* **There is no automatic posterior.** `BeliefState` reports counts and a status computed
  from edges. `bayes_update` exists for a caller who has a likelihood ratio and wants to
  say so; a number produced automatically from edge counts would be quoted as a probability
  by everyone who saw it and justified by nobody.

`belief()` takes a run envelope and reads the record back **through the ledger**, which
enforces the run's authority and verifies the content hash. A belief state assembled from a
node nobody re-verified would be a claim about a graph, not about the science it records —
and `briefing()` silently omits a hypothesis above the run's ceiling rather than
summarising it, which is the same posture the checkpoint takes.

## 9. The journal, and amending a running programme

The ordinary research case, and the awkward one for a task runner: a workflow has run its
retrieval, its preprocessing and its differential expression, and the scientist changes the
fold-change threshold. Re-running everything pays for the retrieval again; re-running
nothing is worse.

`SIRProgram.signature(node)` hashes a node's definition **together with the signatures of
everything it depends on**, so a changed threshold three steps back changes the signature
here and "may I reuse this?" is one string comparison. Identity is content, never position:
resolving a cached result by ordinal means inserting a step at the top silently re-points
every later result.

Two rules keep it safe:

* A result is stored only where `psh.runtime.checkpoint.withholding_reason` says it may be
  — the same function the checkpoint calls, so the journal cannot become a second,
  ungoverned copy of what the WorkGraph refused.
* **Reuse is always safe; recompute is where the risk is.** Reusing a recorded result
  performs no new effect. Re-running a node whose earlier attempt is *in doubt* may repeat
  one, so a node that is not `replayable` comes back as `UNSAFE` and the amendment stops
  for a human — the posture `OperationUnresolved` already takes for a single tool call.

## 10. Two compilers, and the seam between them

`psh.workflow` is the other scientific compiler in this repository, and it is not a rival.
The two sit at opposite ends of one pipeline:

| | `psh.sir` + `psh.compiler` | `psh.workflow` |
| --- | --- | --- |
| Input | a programme of declarations *and* operations | a `Plan` + a `TaskContract` per task |
| Output | a `Plan` | a validated, fingerprinted compilation |
| Sees | hypotheses, predictions, falsifiers, the graph | the registry, the protocol ledger, the policy |
| Answers | is this a method, does the evidence license the claim, what must be recomputed | is this tool's idempotency *attested*, does the bound protocol still match the ledger |

Neither subsumes the other. The front half cannot check a tool manifest, because a manifest
is the registry's business and the IR has no registry. The back half cannot check that an
analysis depends on its protocol node, because by the time it runs, the declarations have
already been compiled away.

So a programme should cross, and `psh.compiler.bridge.to_scientific_program` carries it:
same plan, one `TaskContract` per executable node, then `ScientificCompiler` under the same
envelope. A programme that survives both has been ruled on by every check either knows.

**Crossing is strictly narrowing.** Three rules, each with a test:

1. *A vocabulary item with no counterpart is refused, never approximated.* A `GUIDELINE`
   design and a `RECOMMENDATION` claim have no name on the far side, and mapping either to
   its nearest neighbour would let a guideline license a traditional-use claim — the exact
   substitution both matrices exist to stop. The two coarsenings that *are* made
   (`CASE_SERIES` → `case_report`, `COHORT` → `observational`) both go to the name that
   admits **fewer** claims, so no mapping can make something licensable that was not.
2. *Where the two disagree, the stricter one decides.* Crossing is a conjunction, not a
   second opinion.
3. *Nothing is synthesised.* `TaskContract.statistics` needs a `Protocol` and
   `protocol_binding` needs a ledger record id. This side has an `AnalysisSpec`, which is a
   plan for an analysis and not a registered protocol; manufacturing one would put content
   into a preregistration field that nobody preregistered. The bridge leaves both empty.

The worked example is `test_the_far_side_catches_what_this_one_cannot_see`: a node declares
`IDEMPOTENT`, this compiler accepts it — the declaration is internally consistent, which is
all a declaration can be — and `psh.workflow` refuses it with `EFFECT106`, because the
tool's manifest does not attest idempotency. Neither compiler alone catches that.

**Recommendation.** Keep both, keep the bridge, and do not merge them. Merging would mean
either teaching the IR about registries (which makes a programme unwritable without one) or
teaching the plan-level compiler about declarations it has already lowered away. The seam
is where the two vocabularies have to be reconciled anyway; making it explicit, tested and
refusing-by-default is cheaper than pretending one table can serve both ends.

## 11. What this release does not do

Stated plainly, because the failure mode both earlier reviews were written to catch is
claiming the destination while the middle rows are empty.

* **No OS-level sandbox.** Unchanged from v0.5.3: `NoSandbox` still ships, and
  `require_os_isolation` is still a requirement the kernel refuses when it cannot meet it.
* **No CWL, WDL, Nextflow or GA4GH WES/TES.** The IR was built so those are frontends and
  backends rather than a second compiler. None is written.
* **No W3C PROV or RO-Crate export.** The WorkGraph's structure maps onto PROV's
  Entity/Activity/Agent cleanly; nothing emits it yet.
* **No OPA policy backend, no secret broker, no network capability beyond the existing
  proxy.**
* **No hypothesis competition and no experiment selection.** `ExperimentSpec` carries the
  fields a decision would need — information gain, cost, discrimination — and the compiler
  refuses an experiment that discriminates nothing. It does not *rank* experiments, and a
  single utility number combining information gain with wet-lab risk would be false
  precision.
* **No benchmark.** The claim this release makes is architectural: a research programme is
  refused before it runs for reasons a reviewer would give. Whether that produces better
  science than a harness without it is a question for a same-model, same-budget evaluation,
  and this document does not answer it.
* **The extraction limits are unchanged.** Scientific types are declared by the programme's
  author, not inferred from prose. A programme that declares its mouse study as a
  randomised human trial compiles, and the runtime gates still apply — garbage in the
  declaration is garbage in the check.

## 12. What was found while building it

Five defects, each closed with the test that would have caught it:

**The pipeline reported nothing.** `Diagnostics` defines `__len__`, so an *empty* buffer is
falsy, and every pass took its output as `diagnostics or Diagnostics(...)` — the ordinary
Python idiom, which silently builds a new buffer when the caller passes an empty one. The
passes wrote into an object nothing read, and a programme whose claim asserted human
efficacy from a mouse study compiled clean. Fixed at every call site with `is not None`,
and `Diagnostics.__bool__` now returns `True` so the idiom is safe for the next person.
`test_a_pass_writes_into_the_buffer_it_was_given`.

**A model task with an output schema could never satisfy it.** A model call returns text; a
task declaring an object schema has promised a structure, and both readers of the result —
the schema check and the evaluator's evidence layer — see a string and fail. The loop
replanned, the model returned the same JSON, and the run escalated having done everything
right. `loop._structured` parses where a schema is declared and leaves an unparseable reply
exactly as it came back.

**The journal stored results that could not be read back.** `json.dumps(..., default=str)`
stringifies whatever will not serialise, so a value that could not round-trip was recorded
as `"<object object at 0x7f…>"` and offered for reuse. The stored body now serialises
strictly and an unserialisable result is recorded as withheld.

**A task ceiling was computed from a derived flow label.** The information-flow pass's
label is the join of everything that reached a node, which makes it a **lower** bound on
sensitivity — "this value is at least this sensitive". Lowering used it as the task's
`max_label`, a **ceiling**. The two readings coincide often enough to pass the first test
and diverge on the ordinary case: a public accession number feeding a tool whose result is
a chart note derived a `PUBLIC` ceiling for a task that then legitimately handled PHI, and
the run was refused for doing the right thing. The ceiling now comes from the destinations
the declared effects reach, meeting the run's own ceiling; the derived label is used where
a *floor* is wanted, which is what `psh.workflow`'s `TaskContract.sensitivity` asks for and
what the bridge passes it.

**A test fixture mutated a process-wide singleton.** `bioagent.tcm.default_knowledge()`
memoises one knowledge base for the whole process, and the epistemics tests called it
`fresh` and then added a synthetic trial to it. Every later test — and every later *tool*
in the same process — read a knowledge base containing an invented RCT, in a repository
whose seed is documented as holding none. The fixture now calls `seed()`, which builds a
new one. The bug is worth recording because nothing failed loudly: the pollution only
showed up as an unrelated assertion three files away.

## 13. Numbers

```bash
python -m pytest tests/ -q          # 1084 pass
python -m compileall -q src         # clean
```

| | |
| --- | --- |
| Diagnostic codes | 71 (SIR 16, TYP 8, EFF 5, IFC 7, STAT 15, REP 6, RES 6, SCI 8) |
| Study designs × claim kinds | 16 × 8, as a table rather than a comparison |
| Effects | 17, each mapped to a destination, minimum autonomy and risk tier |
| New tests in this release | 122 in PSH-Harness, 24 in BioScience-Harness |

| | |
| --- | --- |
| New source modules | 21 (`sir` 4, `compiler` 10, `scientist` 4, `durable` 3) |
| Diagnostic codes | 71, each with a remedy, registry checked in both directions |
| New WorkGraph node kinds | 9 (22 in total) |
| New WorkGraph edge kinds | 8 (21 in total) |
| New tests | 110 here, 23 in BioScience-Harness |
