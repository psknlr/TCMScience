# From control plane to governed agent runtime

Two reviews asked a question the enforcement work does not answer: **how much of an agent
runtime is actually here?** Their conclusion was blunt and correct —

> PSH-Harness is a *Governed Agent Control Plane + Execution Broker*, not a multi-agent
> runtime. It is "single governed execution + a delegation primitive", not "a recursive
> multi-agent loop".

This document is the honest map, what v0.5.1 added against it, and the order to build the
rest in. It is deliberately separate from `V5_1_GATE_COMPOSITION_CLOSURE.md`, because the
sequencing matters: **a loop multiplies whatever the gates get wrong.** Every defect the
second review found would have fired once per iteration rather than once per run. Gates
first, then iteration.

## 1. Where it actually stands

Measured against the executing path, not the README.

| Mechanism | v0.5 | v0.5.1 | Note |
| --- | --- | --- | --- |
| Single governed execution pipeline | ✅ | ✅ | `Runner.run`, 16 stages |
| Typed plan | ❌ placeholder | ✅ | `runtime/plan.py` |
| Model-backed planner | ❌ | ✅ | `runtime/planner.py`, validator-driven correction |
| Plan validation | ❌ `"no typed plan to validate"` | ✅ | `runtime/plan_validator.py`, five families |
| Plan/act/observe/evaluate loop | ❌ | ✅ | `runtime/loop.py`, bounded |
| Tool-use loop integrated with the runtime | ❌ | ✅ | `TaskKind.TOOL` through the broker |
| Iterative replanning | ❌ | ✅ | bounded by `max_replans` |
| Loop termination controller | ❌ repeat counter | ✅ | ten named `Termination` reasons |
| Retry / backoff | ❌ config only | ✅ | `RetryPolicy` per task, charged to the budget |
| Layered evaluator | ❌ | ✅ | structural / execution / evidence / goal |
| Acceptance tests executed | ❌ declarative | ✅ | `ACCEPTANCE_CHECKS`, unknown kind = plan error |
| Output-schema validation | ❌ declarative | ✅ | `check_output_schema` |
| Delegation contract | ✅ | ✅ | `DelegationContract` |
| Child authority narrowing | ✅/🟡 | ✅ | gateway now defers to `AuthorityLattice` |
| Automatic subagent selection | ❌ | ❌ | the planner does not emit delegations |
| Fan-out / fan-in | ❌ | ❌ | the scheduler is deterministic and serial |
| Supervisor / worker pool | ❌ | ❌ | — |
| Heartbeat / lease | ❌ | ❌ | — |
| Cancellation propagation | 🟡 interface | 🟡 | `ExecutionGraph.cancel_all` is in-process only |
| Checkpoint / resume | 🟡 audit event | ✅ | `runtime/checkpoint.py`, authority re-met on resume |
| Parallel execution | ❌ | ❌ | — |
| A2A / MCP | ❌ | ❌ | explicitly not implemented |
| Persistent WorkGraph, provenance, quarantine, audit | ✅ | ✅ | the package's strongest layer |

So the honest summary for v0.5.1 is: **a bounded, governed, single-threaded agent loop.**
Not a multi-agent runtime. The row that matters most is the one that has not changed —
there is still no supervisor, no worker pool and no parallelism — and the next section says
why that ordering is deliberate.

## 2. What v0.5.1 added, and the one rule it was built under

```
psh/runtime/
├── runner.py          the single governed pass (unchanged)
├── plan.py            Plan, PlanTask, TestSpec, Criterion, RetryPolicy, RetryBudget
├── plan_validator.py  graph / authority / dataflow / budget / scientific
├── execgraph.py       ExecutionGraph, TaskState — transient, NOT the WorkGraph
├── evaluator.py       four layers, three deterministic
└── loop.py            AgentLoopController, LoopState, Termination
```

The rule, which the review states and which is the whole reason this was built here rather
than adopted from elsewhere:

> **The LoopController must never bypass the TrustedKernel.**

```
        AgentLoopController                 what most agent runtimes are
                 |                                     |
          ExecutionBroker                              |-- calls a provider
                 |                                     |-- spawns a subprocess
          TrustedKernel                                |-- opens a socket
                 |                                     '-- spawns an agent
    model / tool / delegate
```

It is enforced three ways, not asserted once:

1. `_dispatch` has exactly three branches, each a broker call.
2. `test_the_loop_cannot_act_except_through_the_broker` balances the broker's counters
   against what the loop did.
3. `test_the_loop_module_holds_no_route_to_the_outside_world` parses `loop.py` and fails if
   it ever imports `subprocess`, `socket`, `urllib`, `asyncio`, `os` or a HTTP client — a
   behavioural test sees the paths a test took; this sees the paths that *exist*. That
   distinction is exactly how `IsolatedRunner` shipped in v0.4 while `call_tool` ran
   everything in process.

Three design decisions worth stating because they differ from the review's sketch:

**`ExecutionGraph` is not the `WorkGraph`.** The review recommended separating them and it
is right: a scheduling structure whose nodes go `RUNNING` and `FAILED` and get retried
cannot be the same object as a provenance record, or a retry rewrites history. Outcomes
reach the WorkGraph through the persistence gateway; task states never leave memory except
as audit events.

**Task authority is `restrict()`, not a new comparison.** `task_envelope()` narrows the run
envelope with the existing lattice, and the *validator returns the envelopes the loop then
uses*. Recomputing them at execution time would put a second construction path beside the
validated one — the precise shape of defect this release spent its time closing.

**Three deterministic evaluator layers before the model-shaped one.** A single "LLM critic:
looks good?" is both the least reliable check and the one that gets asked first. Schema,
execution state and evidence are pure functions of loop state; only the goal layer may
consult a model, and when it does it goes through the broker.

## 3. What to build next, in order

The ordering is by engineering dependency, not by appeal. In particular **do not start with
MCP or A2A**: both are adapters onto a runtime, and adapting an incomplete runtime means
building the adapter twice.

### v0.6 — finish the single-agent runtime

* **A real planner.** ~~`StaticPlanner` takes the plan from the caller, which is honest but
  is not planning.~~ **Done** (`psh/runtime/planner.py`). `ModelPlanner` emits a typed
  `Plan` through the broker, with PydanticAI-style validator-driven correction: each
  refusal is fed back as the next attempt's input, bounded by `max_attempts`. The load-
  bearing test is that an *escalating* plan is refused rather than obeyed — a planner's
  output is a program, not an answer, so trusting it because a model produced it would make
  every control in the package reachable by asking.
* **Context compaction.** Absent entirely, and the thing that stops long research runs.
  DeepSeek Harness's treatment is the right shape: compaction is a capability, it emits a
  summary event and shadows the old events, rather than truncating the window. Without it
  "read 500 papers, run 40 tool calls, delegate 6 times" is not reachable regardless of how
  good the loop is.
* **Checkpoint / resume for real.** ~~`LoopState` was written to be the thing a checkpoint
  copies.~~ **Done** (`psh/runtime/checkpoint.py`). The load-bearing rule held: a resumed
  run computes `AuthorityLattice.meet(stored, current_ceiling)`, so it is never wider than
  it was *or* than the policy in force, and every narrowed dimension is audited. Restoring
  the stored envelope would have been P0-1 through a file rather than a keyword argument —
  the same defect's second vector, which is the argument for the meet living in one place
  and every entry point going through it. Checkpoints are content-hashed and refused if
  they do not verify; unfinished tasks are re-authorised individually and finished ones are
  not re-checked.

  Building it surfaced a defect in `task_envelope`: `risk` was met with `min()` while
  `max_label` and `autonomy` were passed through unclamped, so a ceiling applied on one
  dimension and refused on the next. The distinction is now explicit — `max_risk`,
  `max_label` and `autonomy` are *self-imposed ceilings* and meet the run's;
  `destinations` and `capability_requirements` are *required reach* and are refused if the
  run does not hold them.

### v0.7 — multi-agent

`Supervisor`, `WorkerPool`, fan-out/fan-in, a result reducer, child lifecycle and
cancellation propagation. One rule governs all of it:

> **A supervisor decides *what* to do. It never decides what is *allowed*.**

```
Supervisor --propose--> TrustedKernel --authorize--> Worker
```

Not the CrewAI/AutoGen shape where a manager grants its workers tools and budget. Child
authority is minted by the kernel from `AuthorityLattice.meet(parent, requested, ceiling)`,
never constructed by the supervisor. The aggregator matters too: for research work,
`"\n".join(worker_outputs)` is not a reducer. Fan-in should produce a typed
`AggregatedObservation` — facts, *conflicting* facts, evidence refs, unresolved questions —
so that two workers disagreeing is a recorded state rather than two paragraphs of prose
concatenated.

### v0.8 — durability

`CheckpointStore`, `ResumeManager`, lease/heartbeat, idempotency keys, a retry controller
whose budget is *charged to the run*. LangGraph's checkpointer and Temporal's cancellation
semantics are the references; Temporal's distinction between cancelling the task that
started a child and cancelling the running child itself is the one that matters for research
work, where "the parent was cancelled" should not always mean "kill the analysis that has
been running for five hours". `DelegationContract` should carry an explicit
`cancellation_policy` — `WAIT` / `DETACH` / `CASCADE`.

### v0.9 — interoperability

`MCPAdapter` for agent↔tool, `A2AAdapter` for agent↔agent. Both under one rule:

```
MCP server / remote AgentCard
        |
     adapter
        |
  IngressClassifier        <- external metadata is untrusted input
        |
  ComponentManifest / RemoteAgentManifest
        |
  ToolGateway / DelegationGateway
        |
    TrustedKernel
```

MCP's own documentation says its tool annotations (`readOnly`, `destructive`, `idempotent`)
are **hints, not security guarantees**, and A2A's samples say an `AgentCard` and everything
a remote agent returns must be treated as untrusted. Both fit this package's existing
position exactly: an `AgentCard` is not a trusted manifest, and a remote tool's
self-description is a claim to be classified, not a policy to be honoured.

## 4. What to borrow, and what not to

The reviews' survey, reduced to the decisions:

| Take from | What | Why not wholesale |
| --- | --- | --- |
| LangGraph | checkpointer, thread-scoped state vs. cross-thread store, `Send` map-reduce | its state model has no authority dimension |
| Google ADK | workflow graph, fan-out/fan-in, nested workflows | same |
| PydanticAI | typed output + validator + retry, *layered* retry budgets | `max_retries` as one number is the thing to avoid |
| Microsoft Agent Framework | `Handoff`, `Concurrent`, Magentic manager | manager-grants-tools is the anti-pattern here |
| OpenAI Agents SDK | the loop shape, sessions, handoff input filters | — |
| Temporal / Prefect | durable scheduling, cancellation semantics, task lifecycle | heavyweight for a research prototype; take the semantics |
| DeepSeek Harness | pluginised agent loop, durable session event log, **compaction** | — |
| Grok Build | subagent = independent child session returning a *summary*; worktree isolation; folder trust | — |
| Codex | execpolicy `allow/prompt/forbidden`, approval→amendment, sandbox/exec seams | already adopted; see `PATTERN_ATTRIBUTION.md` |

Two of these are worth calling out as directly applicable rather than aspirational:

**Grok's subagent model.** A child gets its own context window and returns a *summary*, not
its transcript. That is the same argument `ContextProjection` already makes — compiled
context belongs to one worker — applied to delegation, and it is what stops a parent's
window from accumulating every child's raw output. `DelegationContract` should return
`SubagentResult(summary, artifacts, claims, evidence)`.

**Grok's folder trust.** Repository-local hooks, skills and instructions go through a trust
gate. This package governs runtime authority well and has no notion of *where a
configuration came from*. A `SourceTrust` axis — `SYSTEM` / `USER` / `TRUSTED_PROJECT` /
`UNTRUSTED_PROJECT` / `REMOTE` — would stop a cloned research repository's `AGENTS.md` from
influencing policy. That matters as much for shared data repositories as for code.

And one to decline: **do not adopt another framework's runtime wholesale.** The reviews'
own conclusion is the right one — loop and multi-agent orchestration are commoditising,
while "a child's authority cannot float up, sensitive data cannot cross a boundary, a
scientific claim needs evidence, and the whole run is recoverable and auditable" is not.
That is the part worth keeping, and it is the part a drop-in runtime would replace.

## 5. Convergence with BioScience-Harness

The two packages are complementary rather than competing:

> **PSH** — can the agent do this? &nbsp;&nbsp;**BioScience** — what can the agent actually do?

The recommended shape, and the thing *not* to do:

```
             Agent Runtime  (loop, planner, supervisor, workers)
                    |  proposes
         PSH TrustedKernel  (authority, labels, budget, approval, audit, evidence)
                    |  authorizes
    BioScience Capability Plane  (registry, resolver, backends, acquisition, evolution)
```

Do **not** merge the two `Runtime`s, the two `Policy` objects or the two `Manifest`s. Keep
PSH's kernel as the immutable base; keep BioScience's registry, resolver, backends,
acquisition and evolution pipeline as the capability layer; write the orchestration layer
once, on top. Concretely:

* **One policy, PSH's.** BioScience's genuinely distinct contribution there is not its
  permission model but its **licence and integration provenance** (`MIT`, `Apache-2.0`,
  `NOASSERTION`, `GPL`, vendor / native / federated). That belongs as a *new dimension* of
  the PSH policy — and `PolicyLattice` is now the place to add it, which is most of why it
  exists.

  **Done** (`psh/licensing.py`). It is the first convergence step and it was deliberately
  taken first, because it is also the test of whether the lattice work paid for itself.
  The dimension asks "is this licence acceptable **for this integration mode**", which a
  single allow/deny per licence cannot express: unlicensed code may be *invoked* and may
  not be *copied*. So it is a fixed table — the same shape as `labels.DEFAULT_CEILINGS` —
  plus two subset dimensions a profile narrows.

  The cost of adding it is the number worth recording. Naming it in
  `AuthorityLattice.violations`/`meet`, in `PolicyLattice`, and in the property test's
  dimension list. That is all. `restrict`, `with_`, the meet, delegation and `ToolGateway`
  govern it without being told, because each defers to the one predicate; the
  anti-vacuity test confirmed both new dimensions immediately got real coverage. Before
  v0.5.1 the same change would have meant editing four hand-written comparisons and
  hoping they agreed.

  It did surface one thing: `WorkProfile.freeze()` did not carry the new fields, so a
  profile could declare a licence posture no gate would see. That is the *third* appearance
  of one defect — v0.1 dropped `require_citation` between the profile and the output gate,
  and `freeze()` exists because of it. A hand-written mapping that nothing checks is total
  will keep doing this, so `test_every_profile_field_reaches_the_snapshot` now checks it.
* **Both audit stores, not one.** PSH's hash chain is security truth; BioScience's causal
  event DAG is scientific provenance. They answer different questions and should not be
  flattened into one table.
* **Self-evolution, quarantined.** BioScience's propose → smoke → benchmark → regression →
  promote pipeline is the most forward-looking thing in either package, and under PSH it
  needs one hard boundary: an evolution agent may write `components/`, `skills/`,
  `prompts/`, `planners/` and may **never** write `kernel/`, `authority/`, the policy
  evaluator, the audit chain, the release gate or the sandbox. A component that can modify
  the thing enforcing policy is not governed by it — which is `contracts.py`'s opening
  argument, applied to the agent that edits the repository.

BioScience's test suite was reported as 133 passed / 3 failed / 6 skipped, with at least one
failure a genuine container-validation ordering bug rather than a missing dependency. That
should be closed before convergence starts, not during it.

## 6. Naming the honest state

v0.5.1 is:

> a **policy-enforced control plane** for biomedical scientific agents, with a **bounded,
> governed single-agent loop** on top of it.

It is not a multi-agent operating system, and the row in §1 that reads "Supervisor / worker
pool ❌" is the reason. The target the reviews describe — *typed planner → task graph →
loop controller → supervisor → delegation contract → worker pool → observation → evaluator
→ retry/replan → fan-in → evidence verification → release* — is reachable from here, and
§3 is the order to reach it in. What should not happen is the reverse: claiming the
destination while the middle rows are empty. That is the failure mode both reviews were
written to catch, and it has now cost two releases to correct.
