"""Amend a running program: decide what to keep, what to redo, and what is unsafe to touch.

The case this exists for is the ordinary one in research and the awkward one for a task
runner. A workflow has run its retrieval, its preprocessing and its differential
expression; the scientist changes the fold-change threshold; the pathway analysis and
everything after it are now wrong and the retrieval is not. Re-running the whole program
pays for the retrieval again and, where a step has side effects, does something to the
world a second time. Re-running nothing is worse.

So this is an incremental build system with two rules an ordinary one does not need.

**Identity is content, and the content includes the upstream.**
``SIRProgram.signature(node)`` hashes a node's definition together with the signatures of
everything it depends on, so a changed threshold three steps back changes the signature
here. "May I reuse this?" is then one string comparison rather than a graph walk that can
disagree with the graph.

**A decision to re-run is a decision to act again.** ``SideEffectClass.replayable`` says
whether that is allowed for a step whose earlier attempt is *in doubt* — the process died
inside it, so neither "it ran" nor "it did not" can be asserted. Re-running a literature
search there is free; re-running a submitted wet-lab order is a second order. Those nodes
come back as ``UNSAFE`` and the run stops for a human, which is the same posture
``OperationUnresolved`` already takes one level down.

Reusing a recorded result, by contrast, is always safe: it performs no new effect. The
asymmetry is the whole design — reuse is cheap and safe, recompute is where the risk is.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Mapping, Sequence

from ..sir import SIRProgram
from .journal import NodeOutcome, ReplayState

__all__ = ["Decision", "NodeDelta", "WorkflowDelta", "diff", "seed_graph"]


class Decision(str, Enum):
    """What to do with one node of the amended program."""

    REUSE = "reuse"            # a recorded result for this exact signature is available
    RECOMPUTE = "recompute"    # changed, never ran, or its result was not stored
    NEW = "new"                # did not exist in the previous program
    REMOVED = "removed"        # existed before and does not now
    UNSAFE = "unsafe"          # must re-run, and re-running it is not this system's call


@dataclass(frozen=True, slots=True)
class NodeDelta:
    """One node's fate, with the reason — which is what a reviewer actually reads."""

    node_id: str
    decision: Decision
    reason: str
    signature: str = ""
    previous_signature: str = ""

    @property
    def changed(self) -> bool:
        return bool(self.previous_signature and self.signature != self.previous_signature)

    def __str__(self) -> str:
        return f"{self.decision.value}: {self.node_id} — {self.reason}"

    def as_dict(self) -> dict[str, Any]:
        return {"node_id": self.node_id, "decision": self.decision.value,
                "reason": self.reason, "signature": self.signature,
                "previous_signature": self.previous_signature}


@dataclass(frozen=True, slots=True)
class WorkflowDelta:
    """The plan for an amended run: what survives, what runs again, what blocks it."""

    program_id: str
    nodes: tuple[NodeDelta, ...] = ()

    def _ids(self, decision: Decision) -> tuple[str, ...]:
        return tuple(d.node_id for d in self.nodes if d.decision is decision)

    @property
    def reused(self) -> tuple[str, ...]:
        return self._ids(Decision.REUSE)

    @property
    def recomputed(self) -> tuple[str, ...]:
        return self._ids(Decision.RECOMPUTE)

    @property
    def new(self) -> tuple[str, ...]:
        return self._ids(Decision.NEW)

    @property
    def removed(self) -> tuple[str, ...]:
        return self._ids(Decision.REMOVED)

    @property
    def unsafe(self) -> tuple[NodeDelta, ...]:
        return tuple(d for d in self.nodes if d.decision is Decision.UNSAFE)

    @property
    def safe(self) -> bool:
        """Whether the amendment can proceed without a human reconciling something."""
        return not self.unsafe

    def delta_for(self, node_id: str) -> NodeDelta | None:
        for node in self.nodes:
            if node.node_id == node_id:
                return node
        return None

    def summary(self) -> str:
        return (f"{len(self.reused)} reused, {len(self.recomputed)} recomputed, "
                f"{len(self.new)} new, {len(self.removed)} removed"
                + (f", {len(self.unsafe)} unsafe" if self.unsafe else ""))

    def as_dict(self) -> dict[str, Any]:
        return {"program_id": self.program_id,
                "nodes": [d.as_dict() for d in self.nodes],
                "summary": self.summary()}


def diff(current: SIRProgram, replay: ReplayState, *,
         previous: SIRProgram | None = None) -> WorkflowDelta:
    """Decide each node's fate from the journal and the two programs' signatures.

    ``previous`` is optional: the journal records the signature each node ran under, so a
    comparison is possible without the old program in hand. Supplying it adds only the
    ability to report which nodes were *removed*, which nothing depends on and a reviewer
    wants to see.
    """
    deltas: list[NodeDelta] = []
    signatures = current.signatures()

    for node in current.nodes:
        if not node.executes:
            # A declaration runs nothing, so it has nothing to reuse or redo and saying
            # "recompute the hypothesis" would be noise in the one report a reviewer
            # reads. Its content still matters: a changed hypothesis changes the
            # *signature* of every node that depends on it, which is what invalidates the
            # work downstream.
            continue
        signature = signatures.get(node.node_id, "")
        recorded = replay.signature(node.node_id)
        outcome = replay.outcome(node.node_id)

        if not recorded and outcome is NodeOutcome.UNKNOWN and node.node_id not in \
                replay.succeeded():
            # Nothing in the journal mentions this node at all.
            known = bool(previous is not None and previous.node(node.node_id) is not None)
            deltas.append(NodeDelta(
                node.node_id, Decision.NEW if not known else Decision.RECOMPUTE,
                "this node is not in the journal" if not known
                else "the previous run never recorded it", signature=signature))
            continue

        if signature != recorded:
            reason = ("its definition changed" if previous is not None
                      and previous.node(node.node_id) is not None
                      and previous.node(node.node_id).fingerprint() != node.fingerprint()
                      else "something it depends on changed")
            deltas.append(_recompute_or_unsafe(node, outcome, reason, signature, recorded))
            continue

        if outcome is NodeOutcome.UNKNOWN:
            deltas.append(_recompute_or_unsafe(
                node, outcome,
                "the previous attempt started and never reported, so what it did is "
                "unknown", signature, recorded))
            continue
        if outcome is NodeOutcome.FAILED:
            deltas.append(_recompute_or_unsafe(node, outcome, "the previous attempt failed",
                                               signature, recorded))
            continue
        if not node.cache:
            deltas.append(_recompute_or_unsafe(
                node, outcome, "the node declares cache=false, so its value is only valid "
                "when it is fresh", signature, recorded))
            continue
        if not replay.has_result(node.node_id):
            withheld = replay.withheld_reason(node.node_id)
            deltas.append(_recompute_or_unsafe(
                node, outcome,
                f"it succeeded and its result was not stored ({withheld})" if withheld
                else "it succeeded and no result is available to reuse",
                signature, recorded))
            continue

        deltas.append(NodeDelta(
            node.node_id, Decision.REUSE,
            "unchanged, and the journal holds its result", signature=signature,
            previous_signature=recorded))

    if previous is not None:
        for node in previous.nodes:
            if node.executes and current.node(node.node_id) is None:
                deltas.append(NodeDelta(
                    node.node_id, Decision.REMOVED,
                    "the amended program no longer contains this node",
                    previous_signature=previous.signature(node.node_id)))

    return WorkflowDelta(program_id=current.program_id, nodes=tuple(deltas))


def _recompute_or_unsafe(node: Any, outcome: NodeOutcome, reason: str,
                         signature: str, recorded: str) -> NodeDelta:
    """Recompute, unless doing so would repeat an effect nobody can undo.

    Only an attempt **in doubt** blocks. A node that plainly failed did not do its work, so
    re-running it repeats nothing; a node that never reported may have done all of it.
    """
    if outcome is NodeOutcome.UNKNOWN and not node.side_effect.replayable:
        return NodeDelta(
            node.node_id, Decision.UNSAFE,
            f"{reason}, and this node is {node.side_effect.value}: re-running it could "
            "repeat an effect that cannot be taken back, so it is for whoever owns that "
            "effect to reconcile", signature=signature, previous_signature=recorded)
    return NodeDelta(node.node_id, Decision.RECOMPUTE, reason, signature=signature,
                     previous_signature=recorded)


def seed_graph(graph: Any, delta: WorkflowDelta, replay: ReplayState, *,
               at: float | None = None) -> tuple[str, ...]:
    """Mark every reusable node of an ``ExecutionGraph`` as already succeeded.

    The point where incremental recompute meets the executor. The loop then sees those
    tasks as finished, schedules only what is ready after them, and everything else — the
    gates, the budget, the labels — behaves exactly as it does on a fresh run. The restored
    label comes from the journal, so a reused PHI result is PHI and not PUBLIC.
    """
    import time as _time

    when = at if at is not None else _time.time()
    seeded: list[str] = []
    for node_delta in delta.nodes:
        if node_delta.decision is not Decision.REUSE:
            continue
        node = graph.nodes.get(node_delta.node_id)
        if node is None:
            continue
        graph.mark_succeeded(node_delta.node_id, replay.result(node_delta.node_id),
                             at=when, label=replay.label(node_delta.node_id))
        seeded.append(node_delta.node_id)
    return tuple(seeded)
