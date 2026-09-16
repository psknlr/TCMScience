"""Checkpoint and resume, with one rule that makes it safe.

``Runner``'s ``checkpoint`` stage wrote an audit event and nothing else, so "checkpoint"
named a record of having finished rather than a state a run could be continued from. This
is the real thing, and it is short, because ``LoopState`` was written to be the object a
checkpoint copies.

The rule is the whole design:

> **A resumed run re-meets its authority against the policy in force *now*.**

Restoring the envelope a run held is the obvious implementation and it is a hole. An
envelope is a grant, and a grant that outlives the policy that issued it is a capability
the kernel did not agree to — exactly the P0-1 defect, arriving through a file instead of a
keyword argument. A run checkpointed under a broad policy, resumed after the kernel's
policy narrows, would hold the old authority and every gateway would honour it, because a
gateway checks the envelope it is handed.

So resuming computes ``AuthorityLattice.meet(stored, current_ceiling)``. The resumed run is
never wider than the stored one and never wider than the policy in force, whichever is
narrower. Any dimension that was narrowed is named in an audit event, because a run that
quietly loses authority mid-flight is as confusing as one that quietly gains it.

Two further things follow, and both are refusals rather than best-effort continuations:

* **The record is verified before it is trusted.** A checkpoint carries a content hash and
  is refused if it does not match. Same posture as the event chain: tamper-evident, not
  tamper-proof.
* **Remaining work is re-authorised, one task at a time.** If the narrowed envelope no
  longer admits a task that has not run, the resume is refused and names it. Completed
  tasks are history and are not re-checked; a task that has not run yet needs authority it
  may no longer have.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from ..contracts import (
    Autonomy, Budget, PolicyDenied, Principal, RiskTier, RunEnvelope, content_hash, new_id,
)
from ..labels import DataLabel, Destination, Sensitivity
from .execgraph import ExecutionGraph, TaskState
from .loop import LoopState
from .plan import Plan
from .plan_validator import task_envelope

__all__ = ["Checkpoint", "CheckpointStore", "ResumeRefused", "capture", "resume"]


class ResumeRefused(PolicyDenied):
    """A checkpoint could not be resumed. Says which of the three reasons applies."""


# --------------------------------------------------------------- the record

def _envelope_to_dict(envelope: RunEnvelope) -> dict[str, Any]:
    return {
        "run_id": envelope.run_id, "task_id": envelope.task_id,
        "project_id": envelope.project_id,
        "principal": {"id": envelope.principal.id, "kind": envelope.principal.kind,
                      "display_name": envelope.principal.display_name},
        "risk": envelope.risk.name, "autonomy": envelope.autonomy.value,
        "max_label": envelope.max_label.sensitivity.name,
        "allowed_destinations": sorted(d.name for d in envelope.allowed_destinations),
        "allowed_capabilities": list(envelope.allowed_capabilities),
        "denied_capabilities": list(envelope.denied_capabilities),
        "require_isolated_tools": envelope.require_isolated_tools,
        "allowed_integration_modes": list(envelope.allowed_integration_modes),
        "allowed_license_classes": list(envelope.allowed_license_classes),
        "budget": {f: getattr(envelope.budget, f) for f in (
            "tokens_soft", "tokens_hard", "usd_soft", "usd_hard", "seconds_soft",
            "seconds_hard", "max_model_calls", "max_tool_calls", "max_delegations")},
        "deadline": envelope.deadline, "profile": envelope.profile,
        "parent_run_id": envelope.parent_run_id,
    }


def _envelope_from_dict(data: Mapping[str, Any]) -> RunEnvelope:
    principal = data.get("principal") or {}
    return RunEnvelope(
        run_id=data["run_id"], task_id=data.get("task_id", ""),
        project_id=data.get("project_id", ""),
        principal=Principal(id=principal.get("id", "local"),
                            kind=principal.get("kind", "human"),
                            display_name=principal.get("display_name", "")),
        risk=RiskTier[data["risk"]], autonomy=Autonomy(data["autonomy"]),
        max_label=DataLabel(Sensitivity[data["max_label"]]),
        allowed_destinations=frozenset(Destination[d]
                                       for d in data["allowed_destinations"]),
        allowed_capabilities=tuple(data.get("allowed_capabilities") or ()),
        denied_capabilities=tuple(data.get("denied_capabilities") or ()),
        require_isolated_tools=bool(data.get("require_isolated_tools")),
        allowed_integration_modes=tuple(data.get("allowed_integration_modes") or ()),
        allowed_license_classes=tuple(data.get("allowed_license_classes") or ()),
        budget=Budget(**(data.get("budget") or {})),
        deadline=data.get("deadline"), profile=data.get("profile", "default"),
        parent_run_id=data.get("parent_run_id"))


@dataclass(frozen=True, slots=True)
class Checkpoint:
    """Everything needed to continue a loop, plus the hash that proves it is intact."""

    loop_id: str
    run_id: str
    objective: str
    envelope: Mapping[str, Any]
    plan: Mapping[str, Any]
    task_states: Mapping[str, Mapping[str, Any]]
    results: Mapping[str, Any]
    iteration: int = 0
    replans: int = 0
    digests: tuple[str, ...] = ()
    policy: Mapping[str, Any] = field(default_factory=dict)
    checkpoint_id: str = field(default_factory=lambda: new_id("ckpt"))
    created_at: float = field(default_factory=time.time)
    hash: str = ""

    def __post_init__(self) -> None:
        if not self.hash:
            object.__setattr__(self, "hash", self.compute_hash())

    def _body(self) -> dict[str, Any]:
        return {"checkpoint_id": self.checkpoint_id, "loop_id": self.loop_id,
                "run_id": self.run_id, "objective": self.objective,
                "envelope": dict(self.envelope), "plan": dict(self.plan),
                "task_states": {k: dict(v) for k, v in self.task_states.items()},
                "results": dict(self.results), "iteration": self.iteration,
                "replans": self.replans, "digests": list(self.digests),
                "policy": dict(self.policy), "created_at": self.created_at}

    def compute_hash(self) -> str:
        return content_hash(self._body())

    @property
    def intact(self) -> bool:
        return self.hash == self.compute_hash()

    def to_dict(self) -> dict[str, Any]:
        return {**self._body(), "hash": self.hash}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Checkpoint":
        return cls(
            loop_id=data["loop_id"], run_id=data["run_id"],
            objective=data.get("objective", ""), envelope=data["envelope"],
            plan=data["plan"], task_states=data.get("task_states") or {},
            results=data.get("results") or {}, iteration=int(data.get("iteration") or 0),
            replans=int(data.get("replans") or 0),
            digests=tuple(data.get("digests") or ()), policy=data.get("policy") or {},
            checkpoint_id=data.get("checkpoint_id") or new_id("ckpt"),
            created_at=float(data.get("created_at") or time.time()),
            hash=data.get("hash", ""))


class CheckpointStore:
    """Content-hashed checkpoints on disk. One file per checkpoint, newest last."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, checkpoint_id: str) -> Path:
        return self.root / f"{checkpoint_id}.json"

    def save(self, checkpoint: Checkpoint) -> Path:
        path = self.path_for(checkpoint.checkpoint_id)
        # Written whole then moved, so a crash mid-write leaves the previous checkpoint
        # rather than a truncated one that would fail its own hash check on resume.
        temporary = path.with_suffix(".json.partial")
        temporary.write_text(json.dumps(checkpoint.to_dict(), indent=2, default=str))
        temporary.replace(path)
        return path

    def load(self, checkpoint_id: str) -> Checkpoint:
        path = self.path_for(checkpoint_id)
        if not path.exists():
            raise ResumeRefused(f"no checkpoint {checkpoint_id!r} in {self.root}")
        checkpoint = Checkpoint.from_dict(json.loads(path.read_text()))
        if not checkpoint.intact:
            raise ResumeRefused(
                f"checkpoint {checkpoint_id!r} does not match its own hash; it has been "
                "modified or truncated since it was written, and a run may not be resumed "
                "from a record that cannot be verified")
        return checkpoint

    def latest_for(self, loop_id: str) -> Checkpoint | None:
        found = [c for c in self.all() if c.loop_id == loop_id]
        return max(found, key=lambda c: c.created_at) if found else None

    def all(self) -> list[Checkpoint]:
        out: list[Checkpoint] = []
        for path in sorted(self.root.glob("*.json")):
            try:
                out.append(Checkpoint.from_dict(json.loads(path.read_text())))
            except (json.JSONDecodeError, KeyError):   # pragma: no cover - corrupt file
                continue
        return out


# ----------------------------------------------------------------- capture

def capture(state: LoopState, *, policy: Any = None) -> Checkpoint:
    """Snapshot a loop. Pure: it reads ``state`` and writes nothing."""
    graph = state.graph
    return Checkpoint(
        loop_id=state.loop_id, run_id=state.envelope.run_id, objective=state.objective,
        envelope=_envelope_to_dict(state.envelope),
        plan=state.plan.to_dict() if state.plan else {},
        task_states={
            node.id: {"state": node.state.value, "attempts": node.attempts,
                      "error": node.error}
            for node in (graph.nodes.values() if graph else ())},
        results=({node.id: node.result for node in graph.succeeded} if graph else {}),
        iteration=state.iteration, replans=state.replans,
        digests=tuple(state.digests),
        policy=policy.as_dict() if policy is not None and hasattr(policy, "as_dict")
        else {})


# ------------------------------------------------------------------ resume

@dataclass(frozen=True, slots=True)
class _Narrowing:
    dimension: str
    was: Any
    now: Any


def resume(checkpoint: Checkpoint, kernel: Any, *, policy: Any = None) -> LoopState:
    """Rebuild a ``LoopState`` under **today's** authority, or refuse and say why."""
    from ..kernel.authority import AuthorityLattice

    if not checkpoint.intact:
        raise ResumeRefused(
            f"checkpoint {checkpoint.checkpoint_id!r} does not match its own hash")
    if not checkpoint.plan:
        raise ResumeRefused(
            f"checkpoint {checkpoint.checkpoint_id!r} holds no plan; there is nothing to "
            "continue")

    stored = _envelope_from_dict(checkpoint.envelope)
    effective_policy = policy if policy is not None else getattr(kernel, "policy", None)
    if effective_policy is None:
        raise ResumeRefused("resuming needs a policy to re-authorise the run against")

    # THE rule. Not `stored`, and not the current ceiling either: the meet of both, so the
    # resumed run is no wider than it was and no wider than the policy in force now.
    ceiling = effective_policy.ceiling()
    resumed = AuthorityLattice.meet(stored, ceiling)

    narrowed = [_Narrowing(v.dimension, v.child, v.parent)
                for v in AuthorityLattice.violations(stored, ceiling)]

    plan = Plan.from_dict(checkpoint.plan)
    graph = ExecutionGraph(plan)
    for task_id, record in checkpoint.task_states.items():
        node = graph.nodes.get(task_id)
        if node is None:                      # the plan changed under the checkpoint
            continue
        node.state = TaskState(record.get("state", TaskState.PENDING.value))
        node.attempts = int(record.get("attempts") or 0)
        node.error = str(record.get("error") or "")
        if node.state is TaskState.SUCCEEDED:
            node.result = checkpoint.results.get(task_id)
        elif node.state is TaskState.RUNNING:
            # Nothing is running after a restart. Treat it as retryable rather than
            # succeeded: the process died mid-call and what it did is not known.
            node.state = TaskState.RETRYABLE
            node.error = node.error or "the process ended while this task was running"

    # Re-authorise only what has not run. Completed tasks are history; a task that has yet
    # to execute needs authority it may no longer have. The envelopes are kept rather than
    # recomputed later, so the loop executes the very ones this check ruled on.
    inadmissible: list[str] = []
    envelopes: dict[str, RunEnvelope] = {}
    for node in graph.nodes.values():
        try:
            envelopes[node.id] = task_envelope(node.task, resumed)
        except PolicyDenied as exc:
            if node.state is not TaskState.SUCCEEDED:
                inadmissible.append(f"{node.id}: {exc}")
    if inadmissible:
        raise ResumeRefused(
            f"the policy in force no longer permits {len(inadmissible)} unfinished task(s) "
            f"of checkpoint {checkpoint.checkpoint_id!r}; refusing rather than continuing a "
            "plan that would not be admitted today: " + "; ".join(inadmissible[:4]))

    audit = getattr(kernel, "audit", None)
    if audit is not None:
        audit("loop_resumed", run_id=resumed.run_id,
              detail={"loop_id": checkpoint.loop_id,
                      "checkpoint_id": checkpoint.checkpoint_id,
                      "iteration": checkpoint.iteration,
                      "narrowed": [n.dimension for n in narrowed],
                      "policy": getattr(effective_policy, "profile_id", "")})

    from .plan_validator import ValidatedPlan

    state = LoopState(loop_id=checkpoint.loop_id, objective=checkpoint.objective,
                      envelope=resumed, iteration=checkpoint.iteration,
                      replans=checkpoint.replans, plan=plan, graph=graph,
                      validated=ValidatedPlan(plan=plan, envelopes=envelopes,
                                              order=tuple(graph.nodes)),
                      digests=list(checkpoint.digests))
    state.observe(resumed_from=checkpoint.checkpoint_id,
                  narrowed=[n.dimension for n in narrowed])
    return state
