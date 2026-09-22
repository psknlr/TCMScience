"""Bounded, caller-authored scientific stage graphs with journaled routing.

Stages are ordinary ScientificPrograms. A JSON-value comparison selects an edge
only after a stage succeeds. Cycles are legal, unbounded execution is not.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import json
from typing import Any

from ..contracts import PolicyDenied, new_id
from ..kernel.authority import AuthorityLattice
from ..labels import DataLabel, Destination, combine
from ..runtime.bindings import BindingError, resolve_pointer
from ..runtime.loop import AgentLoopController, LoopLimits, LoopResult, classify_with
from .compiler import ScientificCompiler, ScientificPlanner
from .events import ReplayRefused, ReplayState, RunEventJournal
from .ir import ScientificProgram, digest


@dataclass(frozen=True)
class Branch:
    source: str
    equals: Any
    if_true: str | None
    if_false: str | None
    pointer: str = ""

    def __post_init__(self):
        if not self.source or (self.pointer and not self.pointer.startswith("/")):
            raise ValueError("branch requires a source and a JSON pointer")
        if self.equals is not None and type(self.equals) not in (str, bool, int, float):
            raise ValueError("branch comparison must be a JSON scalar")
        json.dumps(self.equals, allow_nan=False)

    def to_dict(self):
        return dict(source=self.source, equals=self.equals, if_true=self.if_true,
                    if_false=self.if_false, pointer=self.pointer)


@dataclass(frozen=True)
class WorkflowStage:
    stage_id: str
    program: ScientificProgram
    next_stage: str | None = None
    branch: Branch | None = None

    def to_dict(self):
        return dict(stage_id=self.stage_id, program=self.program.to_dict(),
                    next_stage=self.next_stage,
                    branch=self.branch.to_dict() if self.branch else None)


@dataclass(frozen=True)
class DynamicWorkflow:
    stages: tuple[WorkflowStage, ...]
    entry: str
    max_visits: int = 16

    def __post_init__(self):
        if type(self.max_visits) is not int or not 1 <= self.max_visits <= 1024:
            raise ValueError("max_visits must be between 1 and 1024")
        if not 1 <= len(self.stages) <= 256:
            raise ValueError("workflow requires between 1 and 256 stages")
        ids = [s.stage_id for s in self.stages]
        if any(not isinstance(i, str) or not i.strip() for i in ids) or len(set(ids)) != len(ids):
            raise ValueError("stage IDs must be nonempty and unique")
        if self.entry not in ids:
            raise ValueError("workflow entry does not exist")
        for stage in self.stages:
            targets = [stage.next_stage]
            if stage.branch:
                if stage.next_stage is not None:
                    raise ValueError("stage must specify either branch or next_stage")
                if stage.branch.source not in stage.program.contracts:
                    raise ValueError("branch source must be a task in this stage")
                targets = [stage.branch.if_true, stage.branch.if_false]
            if any(t is not None and t not in ids for t in targets):
                raise ValueError("workflow edge names an unknown stage")

    def to_dict(self):
        return dict(schema_version=1, stages=[s.to_dict() for s in self.stages],
                    entry=self.entry, max_visits=self.max_visits)

    def snapshot(self):
        # Frozen dataclasses may still contain caller-owned dictionaries in programs.
        body = json.loads(json.dumps(self.to_dict(), allow_nan=False))
        return DynamicWorkflow(tuple(WorkflowStage(
            s["stage_id"], ScientificProgram.from_dict(s["program"]), s["next_stage"],
            Branch(**s["branch"]) if s["branch"] else None) for s in body["stages"]),
            body["entry"], body["max_visits"])

    @property
    def fingerprint(self):
        body = self.to_dict()
        for stage in body["stages"]:
            stage["program"]["plan"].pop("plan_id", None)
        return digest(body)


@dataclass(frozen=True)
class DynamicResult:
    state: ReplayState
    stages: tuple[LoopResult, ...]
    label: DataLabel

    @property
    def ok(self):
        return self.state.reason == "completed"


class DynamicWorkflowController:
    """Use the existing brokered loop for every stage, with one shared run budget.

    Replay is inspection, not resume. A nonempty journal is always refused by run:
    an uncertain stage must never be redispatched just because history was loaded.
    Persisted events contain only indices/status, but even branch indices disclose
    information, so writes must satisfy the cumulative information-flow label.
    """
    def __init__(self, kernel, *, journal: RunEventJournal, registry=None,
                 scientific_ledger=None, model=None, model_invoke=None,
                 operations=None, limits: LoopLimits | None = None, cancellation=None):
        self.kernel = kernel
        self.journal = journal
        self.registry = registry
        self.scientific_ledger = scientific_ledger
        self.model = model
        self.model_invoke = model_invoke
        self.operations = operations
        self.limits = replace(limits or LoopLimits(), max_replans=0)
        self.cancellation = cancellation

    def _effective(self, envelope):
        return AuthorityLattice.meet(envelope, self.kernel.policy.ceiling())

    def _append(self, event, state, envelope, label):
        effective = self._effective(envelope)
        ceiling = self.kernel.persistence.max_label
        if (not effective.permits_destination(Destination.PERSISTENT)
                or not label.permits(Destination.PERSISTENT)
                or label.sensitivity > min(ceiling, effective.max_label.sensitivity)):
            raise PolicyDenied("dynamic event persistence is not authorized")
        return self.journal.append(event, expected_sequence=state.sequence)

    def run(self, workflow: DynamicWorkflow, envelope, *, input_label=None):
        workflow = workflow.snapshot()
        state = self.journal.replay()
        if state.sequence:
            raise ReplayRefused("nonempty journal: replay does not authorize re-execution")
        label = classify_with(self.kernel, workflow.to_dict(), origin="dynamic_workflow")
        label = combine(label, input_label or DataLabel(), *[
            DataLabel(c.sensitivity) for s in workflow.stages for c in s.program.contracts.values()],
            *[DataLabel(t.input_sensitivity) for s in workflow.stages for t in s.program.plan.tasks])
        # Check every declared branch before any dispatch, including unselected branches.
        compiler = ScientificCompiler(self.registry, scientific_ledger=self.scientific_ledger)
        for stage in workflow.stages:
            compiled = compiler.compile(stage.program, self._effective(envelope),
                                        policy=self.kernel.policy, input_label=label)
            label = combine(label, *[DataLabel(s) for s in compiled.sensitivities.values()])
        ids = [s.stage_id for s in workflow.stages]
        state = self._append(dict(kind="started", fingerprint=workflow.fingerprint,
            entry=ids.index(workflow.entry), stages=len(ids), max_visits=workflow.max_visits),
            state, envelope, label)
        namespace = new_id("dynamic")
        results = []

        def end(reason):
            final = self._append(dict(kind="ended", reason=reason), state, envelope, label)
            return DynamicResult(final, tuple(results), label)

        while state.next_stage is not None:
            if self.cancellation is not None and self.cancellation.cancelled:
                return end("cancelled")
            if state.visits >= workflow.max_visits:
                return end("max_visits")
            stage = workflow.stages[state.next_stage]
            state = self._append(dict(kind="stage_started", stage=state.next_stage,
                visit=state.visits + 1), state, envelope, label)
            planner = ScientificPlanner(stage.program, registry=self.registry,
                policy=self.kernel.policy, scientific_ledger=self.scientific_ledger)
            controller = AgentLoopController(self.kernel, planner=planner,
                registry=self.registry, model=self.model, model_invoke=self.model_invoke,
                operations=self.operations, limits=self.limits, cancellation=self.cancellation,
                operation_namespace=f"{namespace}:{state.visits}")
            result = controller.run(stage.program.plan.objective, self._effective(envelope),
                                    objective_label=label)
            results.append(result)
            label = combine(label, result.label or DataLabel(),
                classify_with(self.kernel, {"results": result.results, "reason": result.reason,
                    "failures": result.failures, "caveats": result.caveats}, origin="dynamic_results"))
            state = self._append(dict(kind="stage_finished", ok=result.ok), state, envelope, label)
            if not result.ok:
                return end("stage_failed")
            target = stage.next_stage
            if stage.branch:
                branch = stage.branch
                try:
                    value = resolve_pointer(result.results[branch.source], branch.pointer)
                    # JSON type identity: true is not the number 1.
                    matches = type(value) is type(branch.equals) and value == branch.equals
                    target = branch.if_true if matches else branch.if_false
                except (BindingError, KeyError, ValueError, TypeError):
                    return end("route_failed")
            state = self._append(dict(kind="routed", target=ids.index(target)
                if target is not None else None), state, envelope, label)
        return end("completed")
