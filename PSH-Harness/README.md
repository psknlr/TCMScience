# psh — Physician-Scientist Harness

A governed control plane for physician-scientist work. Not a bigger agent: the layer your
agents, tools and harnesses run *through*.

Positioning, stated precisely, because the earlier "Kubernetes for AI agents" framing
promised hard isolation, distributed scheduling and multi-tenancy that this does not have:

> **A policy-enforced control plane for biomedical scientific agents.** Research prototype.

## v0.5.1 — gate composition closure

A second review accepted the v0.5 premise and attacked the next layer: what happens where
two controls meet. Seven of its findings were real, two of them P0. They share a shape, and
it is not the v0.5 shape:

> v0.5 — *the mechanism exists and the main path does not call it.*
>
> v0.5.1 — **the mechanism exists, is called, and a second, shorter copy of it was
> hand-written somewhere else.**

| Closed | What was wrong |
| --- | --- |
| **One policy ceiling** (P0) | `Runner.run(policy=…)` minted the run envelope from whatever snapshot the caller passed, never compared to `TrustedKernel.policy`. A kernel forbidding public providers executed a run that reached one and released the output. A per-run policy is now refused unless the kernel's contains it (`clamp_policy=True` meets instead), by a new `PolicyLattice`. |
| **All destinations, not `[0]`** (P0) | `ToolGateway` gated on `manifest.destinations[0]`, so `PHI` + `(LOCAL_COMPUTE, PUBLIC_REMOTE)` was allowed — while `IsolatedExecutor` read the *same* tuple with `any(…)`, saw `PUBLIC_REMOTE` and opened the component's network reach. Every destination is checked; the decision names the most exposed one; approval records list all of them. |
| **Delegation uses the lattice** | `DelegationGateway` hand-checked four of the lattice's fourteen dimensions, so an `R1`/`SUGGEST` parent could delegate `R4_KERNEL`/`ACT`/unrestricted/999× budget and get `allowed=True`. It calls `AuthorityLattice.violations` now. |
| **Policy narrowing is a lattice** | `with_()` was documented as refusing any widening and compared three of twelve dimensions. `SUGGEST→ACT`, `require_isolated_tools True→False` and `tokens_hard 10→999` were all accepted. |
| **The sandbox contains the workdir** | `manifest.id` was validated only for being non-empty and was joined onto the sandbox root, so `id="../../escaped"` put the child's cwd outside it and an absolute id discarded the root. Ids are one path component, and containment is re-checked after resolution. |
| **Timeout kills the process group** | `start_new_session=True` created a group that nothing ever signalled: a grandchild outlived the timeout by seconds and went on writing files. `killpg` is sent now. |
| **Security walkers fail closed** | Past depth 8 the path walker returned `[]`, so nesting a payload deep enough made the filesystem boundary disappear. A walk that hit its cap now refuses, as `labels.walk_values` already did. |
| **Split brain closed** | The run envelope came from one policy while `OutputGate`, `broker.require_isolation` and `PersistenceGateway` came from another. `require_isolated_tools` travels on the envelope; the gate and the store take per-call ceilings that can only tighten. |

Also: `requires_network` validation was a chained comparison that could never be true;
`StuckLoop` counted every run of a `Runner` in one bucket and refused three identical
top-level requests; `min_autonomy` was declared and never read (and defaulted to the
strongest requirement); an isolated component breaking the stdout JSON protocol was
silently accepted. See `docs/V5_1_GATE_COMPOSITION_CLOSURE.md`.

The delegation property test that should have caught the lattice bypass was **vacuous**:
its child widened every dimension at once and `tokens_hard = 10**9` always exceeded the
parent strategy's `100_000`, so the first budget comparison refused every example and no
other dimension was ever the reason for a verdict. It is now one dimension at a time, with
a test asserting the properties actually reach their assertions — which immediately found a
dimension with zero coverage.

## v0.5.1 — a bounded agent loop, through the kernel

The same release adds the component both reviews named as the most valuable next one, under
the rule they both stated: **the loop must not bypass the trusted kernel.**

```
psh/runtime/
├── runner.py          the single governed pass (unchanged)
├── plan.py            typed Plan / PlanTask / TestSpec / RetryPolicy
├── plan_validator.py  graph / authority / dataflow / budget / scientific
├── execgraph.py       ExecutionGraph — transient, deliberately NOT the WorkGraph
├── evaluator.py       structural / execution / evidence / goal
└── loop.py            AgentLoopController — plan, act, observe, evaluate, bounded
```

`validate_plan` used to record `"placeholder planner: no typed plan to validate"`, which was
honest and was also why the stage could enforce nothing: there is no dimension of
`"summarise the HFpEF evidence"` to compare against a risk ceiling. A plan is typed now and
each task carries the authority it intends to use, so a plan whose fourth step needs a
destination the run forbids is refused *at the plan*, not discovered at step four with three
steps' side effects already committed.

The loop holds no provider, no subprocess and no socket. It acts through exactly three
broker calls, and that is checked three ways: the dispatch has three branches; a test
balances the broker's counters against what the loop did; and a test parses `loop.py` and
fails if it ever imports a transport or a process spawner — because a behavioural test sees
the paths a test took, not the paths that exist, which is exactly how `IsolatedRunner`
shipped in v0.4 while `call_tool` ran everything in process.

Termination is a controller rather than a repeat counter: `goal_satisfied`, `max_iterations`,
`budget_exhausted`, `deadline`, `no_progress`, `max_replans`, `plan_rejected`,
`policy_denied`, `escalated`, `unrecoverable_error` — always named, never inferred. Retries
are per task and are charged to the same budget as the first attempt; a policy *refusal* is
never retried, because it is an answer rather than a fault.

What this is **not**: a multi-agent runtime. There is no supervisor, no worker pool, no
fan-out and no parallelism, and `docs/ROADMAP_AGENT_RUNTIME.md` says so in a table rather
than in prose. Gates first, then iteration — a loop multiplies whatever the gates get wrong.

### Licence provenance — the first BioScience convergence step

`psh/licensing.py` adds the one dimension where BioScience-Harness's policy model is
stronger than this one: whether a capability's licence permits the **way** it is
integrated. The same licence gives different answers —

```
vendor      copy the upstream implementation in     -> redistribution
native      call an independent equivalent          -> no upstream code at all
federated   invoke upstream in its own process      -> use, not redistribution
```

— so unlicensed code may be invoked and may not be copied, which a single allow/deny per
licence cannot say. A fixed table states what is permissible in principle (the same shape
as `labels.DEFAULT_CEILINGS`), and a profile narrows which classes and modes it permits at
all; `coding` does not permit vendoring, because that is the mode that produces code the
group then distributes.

It is also the test of whether the lattice work paid for itself. Adding a governed
dimension cost naming it in `AuthorityLattice`, `PolicyLattice` and the property test's
dimension list — `restrict`, `with_`, the meet, delegation and `ToolGateway` govern it
without being told.

```bash
python -m pytest tests/ -q          # 372 pass
python -m compileall -q src         # clean
```

## v0.5 — enforcement closure

No new subsystems. This release closes the gap an external review measured between what the
package *implements* and what its *executing path* uses. The defects shared one shape — a
control that exists, a main path that does not call it, and a README describing the
control — and each is now closed with a test that drives the main path:

| Closed | What was wrong |
| --- | --- |
| **Policy is a ceiling** | `PolicySnapshot.envelope()` read `kw.pop("risk", self.risk_ceiling)`, so the ceiling applied only to callers who declined to state a value. `R2`/`SUGGEST` policies minted `R4`/`ACT` envelopes on request. Every dimension is now checked against the ceiling by `AuthorityLattice`; widening raises, `clamp=True` narrows instead. |
| **One minting path** | `TrustedKernel.envelope()` never consulted `self.policy` at all — it minted from hard-coded defaults (`PHI`, `ACT_WITH_APPROVAL`, four destinations). It now delegates to the policy, so a `peer_review` kernel cannot hand out a network envelope. |
| **Isolation on the real path** | `IsolatedRunner` shipped in v0.4 and `ExecutionBroker.call_tool` ran every component with `component.invoke(...)` *inside the kernel process* — a tool could read `os.environ` and open its own socket. Components declaring `backend="subprocess"` now run through the runner; the two paths are counted separately, the audit event records which ran, and `require_isolated_tools` refuses the in-process one outright. |
| **Approvals key on the action** | A session approval was keyed on the string `"run shell"`, so approving `git push origin main` for the session also pre-approved `curl … | sh`. The key is now a digest over request kind, component, normalised argv (or payload digest), targets and risk. |
| **Grants cannot unset the boundary** | `build_child_environment` wrote the proxy variables and then applied caller grants over them, so `HTTP_PROXY=http://evil:9999` / `NO_PROXY=*` disabled the egress boundary from inside. Kernel-reserved names are applied last and refused as grants. |
| **No DNS rebinding, ports are capabilities** | The proxy validated a hostname and then handed the *name* to `create_connection`, which resolved it again. It now connects to the addresses the decision vetted, refuses names that do not resolve, treats `host` as ports 80/443 (`host:8443`, `host:*` to say otherwise), and derives the forwarded `Host:` from the vetted URI instead of the client's header. |
| **Filesystem containment after resolution** | Path checks were `fnmatch` over the payload's own string, so `/allowed/../secret` and a symlink out of `/allowed` both passed. Paths are now resolved (`..`, symlinks, `~`) and must land inside an allowed root; nested payloads are walked. |
| **External hooks are contained** | A `PreToolUse` command hook received the fully unwrapped payload and ran under a bare `subprocess.run` — PHI reaching a third-party process with unsupervised network access, through a door the broker does not watch. External hooks now run through the same isolated runner (no network by default) and receive a redacted view; in-process hooks are trusted and unchanged. |
| **The output gate is not English-only** | Clinical assertions in Chinese and reported statistics (`AUC`, odds ratio, `p < 0.001`, 敏感性/死亡率) were invisible to the gate, and CJK text split into a single "sentence". Both are recognised now. |

Also: `TrustedKernel.close()` releases the WorkGraph connection as well as the event store;
`hypothesis` is a declared test dependency (the authority property tests silently skipped
without it); `live` tests are deselected by default in `pyproject.toml` rather than by
remembering a flag; AppleDouble `._*` sidecars are gone from the tree and ignored (they
contain NUL bytes and break `python -m compileall`).

Run tests:

```bash
python -m pytest tests/ -q          # `-m live` opts into network tests
python -m compileall -q src         # clean
```

## What is enforced, and what is not

Enforced, with a test that drives the executing path:

* no value reaches a gateway unclassified, and a caller-supplied label is re-validated;
* no envelope is wider than the policy that minted it, on any of the lattice's dimensions;
* every model call, tool call and delegation passes the broker, which records an event;
* output is quarantined and `released_output` stays `None` unless the release gate passed;
* a `backend="subprocess"` component cannot read the kernel's environment.

**Not** enforced, stated plainly:

* a `backend="python"` component runs in the kernel process and is confined by nothing.
  This is the honest boundary: `require_isolated_tools=True` is how a policy refuses it.
* the egress proxy governs clients that honour proxy variables. A raw socket bypasses it.
  `SandboxBackend` is the seam for the OS layer (Seatbelt, bubblewrap+seccomp); only
  `NoSandbox` ships, and it says so in `describe()`.
* the audit chain is tamper-evident, not tamper-proof; classification is a safety net, not
  certified de-identification; claim support is lexical; the planner is a placeholder.

## Install

```bash
pip install -e .            # standalone; classification degrades and says so
pip install -e .[test]      # pytest + hypothesis
```

Run tests with `PYTHONPATH=src` (or `PYTHONPATH=../sable_pkg/src:src` for the composed
configuration) — editable installs may not survive a session restart.

## Use

```python
from psh import get_profile
from psh.kernel import TrustedKernel
from psh.runtime import Runner

policy = get_profile("clinical_research").freeze()   # local only, PHI ceiling
kernel = TrustedKernel(policy=policy)
runner = Runner(kernel, model=local_model, model_invoke=my_provider, policy=policy)

result = runner.run("Summarise the HFpEF evidence", sources={"34449189": abstract})
if result.released_output is None:
    print("refused:", result.error, "| quarantined as", result.quarantine_ref)
```

The policy is a ceiling: `runner.run(..., risk=RiskTier.R4_KERNEL)` under this profile is
refused at the `policy_snapshot` stage, not honoured. Ask for less than the profile grants
and you get it; ask for more and you get a `PolicyDenied` naming the dimension.

That applies to the *policy* as well as to the envelope. `runner.run(..., policy=wider)` is
refused, because until v0.5.1 it was the way to mint a run the kernel's own policy forbade:

```python
runner.run("…", policy=broader)              # PolicyDenied, naming each dimension
Runner(kernel, policy=broader, clamp_policy=True)   # narrowed to the meet instead
runner.run("…", policy=policy.with_(autonomy=Autonomy.OBSERVE))   # narrowing: honoured
```

`clinical_research` is deliberately unusable without a local model. That is the profile
working as intended.

## Earlier releases

* **v0.5** — enforcement closure: nine controls moved onto the path that actually
  executes. Policy became a real ceiling at minting, isolation reached `call_tool`,
  approvals keyed on the action, filesystem checks resolved before comparing.
* **v0.4** — six enforcement patterns adopted from Codex (read from source) and Claude Code
  (official docs), attributed per pattern in `PATTERN_ATTRIBUTION.md`: declarative
  self-testing execution policy, approval-to-rule amendments, absolute-deny evaluation
  order, the shared hook JSON protocol, default-deny child environments, a kernel-owned
  egress proxy refusing private ranges even when allowlisted.
* **v0.2** — sixteen externally-found defects closed as invariants: mandatory ingress, deep
  labelling, one authority predicate, release before exposure, classified persistence,
  evidence provenance, real token/cost accounting.

## Scope

Research prototype. Not clinical-safe, not production-ready.

`docs/V5_1_GATE_COMPOSITION_CLOSURE.md` — the review items this release closes, each with
its reproduction, and the ones it deliberately leaves open.
`docs/V5_ENFORCEMENT_CLOSURE.md` — the previous round.
`docs/ROADMAP_AGENT_RUNTIME.md` — what this is *not* yet: a governed agent runtime. The
planner is a placeholder, `Runner.run` is a single pass rather than a loop, and delegation
is a primitive rather than an orchestrator. That roadmap is deliberately separate from the
enforcement work, because adding a loop before the gates compose correctly multiplies
whatever the gates get wrong.

MIT licensed.
