"""Lowering: a compiled program becomes the ``Plan`` the existing runtime already governs.

This is the most important file in the package and the shortest interesting one, because
of what it does *not* do. It builds no executor, opens no socket, holds no provider and
adds no gate. It produces a ``psh.runtime.plan.Plan`` — the same object ``ModelPlanner``
produces — which ``PlanValidator`` then validates and ``AgentLoopController`` executes
through the broker, exactly as before.

That is the whole integration strategy. The compiler is an *additional* refusal in front of
a runtime that already refuses; it is never a substitute for one. If every pass in this
package were deleted, nothing would become permitted that is not permitted today.

Two translations carry real information rather than reshaping fields.

**Authority is derived from effects.** ``PlanTask`` asks its author to state destinations,
risk and autonomy. A ``SIRNode`` states what it *does*, and this computes what that
requires from the one effect table. An author cannot understate a step's authority and be
refused at the gate half way through, because the author never states it.

**Declarations are dependencies, not steps.** A hypothesis is a node in the science graph
and not a job, so an analysis that depends on a hypothesis which depends on a retrieval
lowers to a task that depends on the retrieval. The science graph keeps its shape; the
execution graph gets only what executes.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from ..contracts import ContractViolation, RunEnvelope
from ..labels import Sensitivity
from ..runtime.plan import (
    Criterion, InputBinding, Plan, PlanTask, RetryPolicy, TaskKind, TestSpec,
)
from ..sir import (
    ExecKind, FlowLabel, SIRNode, SIRProgram, ceiling_for, destinations_for,
    min_autonomy_for, min_risk_for,
)
from .infoflow import FlowResult

__all__ = ["lower", "LoweringError", "executable_dependencies"]


class LoweringError(ContractViolation):
    """A compiled program could not be lowered. A bug here, not an author's mistake."""


_KINDS = {ExecKind.MODEL: TaskKind.MODEL, ExecKind.TOOL: TaskKind.TOOL,
          ExecKind.DELEGATE: TaskKind.DELEGATE}


def executable_dependencies(program: SIRProgram, node: SIRNode) -> tuple[str, ...]:
    """A node's dependencies with declarations collapsed through to what executes.

    Order-preserving and deduplicated: the lowered plan has to be stable across
    compilations of the same program, because a content hash over it is what the
    incremental recompute in ``psh.durable`` compares.
    """
    out: list[str] = []
    seen: set[str] = set()

    def walk(node_id: str, depth: int = 0) -> None:
        if node_id in seen or depth > 64:
            return
        seen.add(node_id)
        source = program.node(node_id)
        if source is None:
            return
        if source.executes:
            if node_id not in out:
                out.append(node_id)
            return
        for dependency in source.dependencies:
            walk(dependency, depth + 1)

    for dependency in node.dependencies:
        walk(dependency)
    return tuple(out)


def lower(program: SIRProgram, envelope: RunEnvelope, *,
          flow: FlowResult | None = None) -> Plan:
    """Turn a compiled program into a typed ``Plan``.

    ``flow`` is the information-flow pass's result. Without it every task declares the
    run's own ceiling, which is correct and loose; with it each task declares the ceiling
    of what actually reaches it, so a step that only ever sees public data runs under an
    envelope that could not carry anything else.
    """
    tasks: list[PlanTask] = []
    for node in program.nodes:
        if not node.executes:
            continue
        tasks.append(_task(program, node, envelope, flow))

    criteria = tuple(Criterion(description=a.description, kind=a.kind)
                     for a in program.acceptance)
    return Plan(objective=program.question, tasks=tuple(tasks),
                assumptions=tuple(program.assumptions), completion_criteria=criteria,
                plan_id=program.program_id,
                produced_by=f"sir:{program.produced_by}")


def _task(program: SIRProgram, node: SIRNode, envelope: RunEnvelope,
          flow: FlowResult | None) -> PlanTask:
    kind = _KINDS.get(node.kind)
    if kind is None:                           # pragma: no cover - structure pass refuses
        raise LoweringError(f"node {node.node_id!r} has no executable kind")

    destinations = tuple(sorted(destinations_for(node.effects), key=lambda d: d.value))
    label = _max_label(node, envelope, flow)

    bindings = tuple(
        InputBinding(argument=p.argument, source=p.source, pointer=p.pointer,
                     expected_type=p.expected_type, cardinality=p.cardinality,
                     required=p.required)
        for p in node.inputs if program.node(p.source) is not None
        and program.node(p.source).executes)  # type: ignore[union-attr]

    tests = tuple(TestSpec(kind=str(t.get("kind")), detail=dict(t.get("detail") or {}))
                  for t in node.acceptance_tests if t.get("kind"))

    # The repeat semantics decide the retry policy. A step that may not be re-run in doubt
    # gets one attempt, whatever it asked for: ``RetryPolicy`` is where the loop learns
    # that, and letting an author set three attempts on a wet-lab order would make the
    # declaration decorative.
    attempts = node.max_attempts if node.side_effect.retryable else 1

    dependencies = executable_dependencies(program, node)
    # A binding whose source is not a dependency is refused by ``PlanTask``; after the
    # collapse above every binding source is executable and therefore present.
    missing = [b.source for b in bindings if b.source not in dependencies]
    if missing:                                # pragma: no cover - defensive
        raise LoweringError(
            f"node {node.node_id!r} binds {missing}, which the dependency collapse did "
            "not preserve")

    try:
        return PlanTask(
            task_id=node.node_id, objective=node.objective, kind=kind,
            dependencies=dependencies, component_id=node.component_id,
            payload=dict(node.payload), max_risk=min_risk_for(node.effects),
            max_label=label, destinations=destinations,
            autonomy=min_autonomy_for(node.effects),
            estimated_tokens=node.estimated_tokens, estimated_usd=node.estimated_usd,
            estimated_seconds=node.estimated_seconds,
            output_schema=dict(node.output_schema), acceptance_tests=tests,
            evidence_required=node.evidence_required or node.produces.is_claim,
            inputs=bindings,
            retry=RetryPolicy(max_attempts=max(1, attempts)))
    except ValueError as exc:                  # pragma: no cover - structure pass refuses
        raise LoweringError(f"node {node.node_id!r} cannot be lowered: {exc}") from exc


def _max_label(node: SIRNode, envelope: RunEnvelope,
               flow: FlowResult | None) -> Sensitivity:
    """The data ceiling this task declares.

    The subtle rule, and getting it wrong the first time made a legitimate run refusable:

    > **A derived flow label is a lower bound on sensitivity, not an upper one.**

    The information-flow pass computes what a value is *at least* as sensitive as, from
    what fed it. The runtime classifier may find more — a public accession number is a
    perfectly public input to a tool that returns a chart note. So a task ceiling taken
    from the derived label would refuse that tool's own result at the gate, and the run
    would fail for a reason that is not a policy decision but a wrong prediction.

    A **declared** label is the other thing: an author saying what this step handles. That
    is a statement about the ceiling and is honoured, clamped to the run's own, so
    least-privilege stays available where somebody has actually thought about it. Where
    nobody has, the task inherits the run's ceiling and the gates rule as they always did.

    The derived label is not wasted: ``IFC301`` and ``IFC306`` use it to refuse the flows
    that are wrong *by construction*, which is a sound use of a lower bound.
    """
    run_ceiling = envelope.max_label.sensitivity
    # The destination's own ceiling, from ``labels.DEFAULT_CEILINGS`` via the effect
    # table. This is not a prediction about the data — it is the policy fact that a step
    # reaching a public provider may not handle identifiable content, and stating it on
    # the task is what makes the task's declared authority self-consistent. Without it a
    # task inheriting a PHI run ceiling while declaring PUBLIC_REMOTE is refused by
    # ``PlanValidator``'s dataflow family, correctly, for a contradiction the compiler
    # wrote rather than the author.
    effect_ceiling = ceiling_for(node.effects) if node.effects else run_ceiling
    ceiling = min(run_ceiling, effect_ceiling)

    declared = node.declared_label
    if declared is not None:
        return min(declared.sensitivity, ceiling)
    if node.declassify_to is not None and flow is not None:
        # An explicit, policy-permitted declassification is also a statement about the
        # ceiling, and the pass has already ruled on whether it was allowed.
        return min(flow.labels.get(node.node_id, FlowLabel()).sensitivity, ceiling)
    return ceiling
