# Bounded dynamic workflows and stage-event replay

`psh.workflow.DynamicWorkflowController` executes caller-authored graphs of
`ScientificProgram` stages. This is the first dynamic execution increment, not
the complete ZCode dynamic-workflow engine or a replacement for task checkpoints.

## Execution model

- `WorkflowStage` contains a scientific program and either `next_stage` or a
  `Branch`. `None` is the terminal edge.
- `Branch` reads a task result in the just-completed stage, optionally through
  the existing runtime JSON-pointer resolver. A type-strict JSON-scalar equality
  selects `if_true` or `if_false`. No `eval`, model-authored Python or routing
  callback is executed. Missing paths fail routing instead of silently choosing.
  Model results are strings; JSON text is **not** implicitly parsed into objects.
- Cycles are explicit. `max_visits` bounds total stage executions (1–1024),
  including repeats. A graph holds at most 256 stages. Each stage also obeys
  `LoopLimits`; implicit replanning is disabled. Cancellation is cooperative.
- Each stage remains an ordinary DAG: existing bounded independent-task
  concurrency and dependency joins work via `LoopLimits(max_parallel=...)`.
  Dynamic map expansion, cross-stage input binding, live graph amendment and
  model generation of dynamic graphs are not implemented by this adapter.
- The input graph is JSON-snapshotted before execution. All declared stages,
  even unselected ones, pass scientific preflight before dispatch. Selected
  stages compile again against current policy. Registered-protocol checks use
  the supplied `scientific_ledger`, as with `ScientificPlanner`.
- All visits retain the original envelope's run ID, budget and deadline. Tool
  operation keys gain a workflow-instance/visit namespace, so two intentionally
  repeated stage visits do not collide in `OperationLedger`. A repeated visit is
  a **new invocation**, not a retry or cache hit; authors must approve repeated
  side effects when declaring a cycle. Existing task retry restrictions remain.

## Usage

Here `screen_program` and `review_program` are previously constructed
`ScientificProgram` objects; `screen_program` includes a task named `decision`.

```python
from psh.workflow import (
    Branch, WorkflowStage, DynamicWorkflow, DynamicWorkflowController,
    RunEventJournal,
)

workflow = DynamicWorkflow(
    stages=(
        WorkflowStage("screen", screen_program,
            branch=Branch("decision", "review", "review", None)),
        WorkflowStage("review", review_program),
    ),
    entry="screen",
    max_visits=4,
)

# Use a NEW database for each execution. The path is chosen by the application,
# never by model-generated program content.
with RunEventJournal("E:/Codex/TCMScience/runs/example/events.sqlite") as journal:
    controller = DynamicWorkflowController(
        kernel, journal=journal, registry=registry,
        scientific_ledger=scientific_ledger,
        model=local_model_profile, model_invoke=invoke_local_model,
        operations=operation_ledger,
    )
    result = controller.run(workflow, envelope)
    anchor = journal.anchor()  # retain separately for suffix-loss detection

# Inspection after reopening: this does NOT invoke any model or tool.
with RunEventJournal("E:/Codex/TCMScience/runs/example/events.sqlite") as journal:
    state = journal.replay(expected_anchor=anchor,
                           fingerprint=workflow.fingerprint)
    print(state.phase, state.visits, state.reason, state.in_doubt)
```

Persistence must be authorized by the original envelope **and** current policy.
The store's ceiling is also enforced. The controller conservatively propagates
declared and classified workflow sensitivity, registered-protocol sensitivity,
caller `input_label`, and all completed stage result labels. Routing indices
are derived information, not automatically public metadata. If a newly sensitive
result cannot be persisted, the controller stops without writing a branch or
dispatching another stage; the journal may deliberately remain `running`.
The journal adapter itself is a trusted low-level storage port, like the existing
checkpoint adapter, not a public persistence gateway. Its readers require
filesystem access appropriate to the run. There is no encryption/key management.

## Replay semantics and failure boundaries

Events are `started`, `stage_started`, `stage_finished`, `routed`, `ended`.
The pure reducer validates schema, ordering, stage indices, visit counts and
terminal conditions. Replay rebuilds stage control state, not task outputs or
scientific evidence. Only structural indices/status and a workflow fingerprint
are persisted; no prompt, result, branch comparison literal or error text.

SQLite WAL/FULL commits each event atomically. `BEGIN IMMEDIATE` plus an expected
sequence prevents concurrent writers from advancing the same state. The hash
chain detects altered/interior-deleted events; an external anchor detects suffix
loss. Hashes are not signatures and cannot defeat full database rewriting.
Every read/append verifies history, currently O(events), appropriate to the
bounded first implementation rather than a high-throughput streaming journal.

The intent event commits **before** a stage runs. If the process dies after that
commit but before stage completion commits, replay reports `in_doubt=True`.
It does not guess whether a tool or model ran. Failure to write an event stops
execution; unlike best-effort audit logging, it never silently falls back to RAM.
A completed stage whose route was not recorded remains in `routing`.

`run()` rejects every nonempty journal, including completed histories. There is
no automatic resume/redispatch from these events, no persisted result cache, no
exactly-once guarantee, and no atomic transaction with external tool effects.
Use the existing operation ledger for effect investigation and the existing
governed checkpoint APIs for their separate task-level recovery workflow.
This journal is authoritative for **stage scheduling history only**, not a
universal replacement for audit logs, task checkpoints or operation records.

`DynamicResult.ok` means all visited stages met their loop completion criteria
and a terminal edge was reached. It does not certify scientific truth or clinical
validity. Per-stage goal status and caveats remain on `result.stages`.

## Validation

`tests/test_dynamic_workflow.py` covers both branch choices, conditional and
bounded cycles, preflight rejection, persistent reopen/replay, crash windows,
current-policy and sensitivity checks, shared budget exhaustion, distinct
nonrepeatable operation identities, cancellation, caller mutation isolation,
invalid transitions, stale writers, corruption, suffix anchors and fail-closed
storage errors. Tests use synthetic model callbacks and tools, not live providers.
