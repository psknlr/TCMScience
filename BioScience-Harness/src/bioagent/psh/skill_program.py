"""A skill contract as a PSH ``ScientificProgram``, so PSH's compiler checks it.

``skill.yaml`` (``providers.skills.SkillContract``) says which tools a skill runs in which
order, which steps produce evidence of which study design, and the strongest claim it may
make. This adapter turns that into the program PSH already knows how to compile: one tool
task per step, an ``EvidenceSpec`` on each evidence step, and one claim task whose
``ClaimSpec`` names the evidence steps. The PSH compiler then refuses what it refuses
everywhere — a claim type the designs cannot license (``in_silico`` supports only a
mechanism hypothesis), evidence that is not a direct dependency, a scope mismatch — and no
new compiler is written.

The adapter adds two refusals of its own before PSH sees the program: a claim kind above
the skill's ceiling, and a skill with no evidence step at all.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

__all__ = ["ClaimScope", "SkillProgramError", "skill_program", "CLAIM_TYPES"]

#: bioagent claim kind -> PSH ClaimType value
CLAIM_TYPES = {
    "attribution": "classical_attribution", "traditional_use": "traditional_use",
    "mechanism_hypothesis": "mechanism_hypothesis", "mechanism": "mechanism",
    "association": "association", "efficacy": "clinical_efficacy",
    "safety_signal": "safety_signal",
}


class SkillProgramError(ValueError):
    """The skill cannot be expressed as a program for this claim."""


@dataclass(frozen=True)
class ClaimScope:
    """Who, what and which outcome the claim is about — the same for every evidence step,
    because PSH refuses evidence whose scope differs from the claim's (EVIDENCE104)."""

    population: str
    intervention: str
    outcome: str


def _order(steps: dict[str, Any]) -> list[str]:
    done: list[str] = []
    pending = dict(steps)
    while pending:
        ready = sorted(s for s, spec in pending.items()
                       if all(d in done for d in spec.get("after") or ()))
        if not ready:
            raise SkillProgramError(f"steps form a cycle: {sorted(pending)}")
        done.extend(ready)
        for s in ready:
            del pending[s]
    return done


def skill_program(contract: Any, scope: ClaimScope, *, claim_kind: str | None = None,
                  provenance: Iterable[str] = (), objective: str = "") -> Any:
    """The ``ScientificProgram`` for running ``contract`` to a ``claim_kind`` claim.

    ``provenance`` names what the evidence rests on — the snapshot ids the run loaded —
    and becomes each ``EvidenceSpec``'s provenance.
    """
    from psh.labels import Destination, Sensitivity
    from psh.runtime import Criterion, Plan, PlanTask
    from psh.workflow import (ClaimSpec, ClaimType, Effect, EvidenceSpec, ScientificProgram,
                              SideEffect, TaskContract)

    kind = claim_kind or contract.max_claim_kind
    if kind not in CLAIM_TYPES:
        raise SkillProgramError(f"claim kind {kind!r} has no PSH claim type")
    if not contract.permits(kind):
        raise SkillProgramError(f"skill {contract.id} may not make {kind} claims "
                                f"(ceiling {contract.max_claim_kind})")
    steps = {k: dict(v) for k, v in contract.steps.items()}
    evidence_steps = [s for s in _order(steps) if steps[s].get("design")]
    if not evidence_steps:
        raise SkillProgramError(f"skill {contract.id} declares no evidence step")
    provenance = tuple(provenance) or (f"skill:{contract.id}@{contract.version}",)

    base = TaskContract(sensitivity=Sensitivity.PUBLIC, effects=(Effect.LOCAL_COMPUTE,),
                        side_effect=SideEffect.NON_REPEATABLE)
    tasks, contracts = [], {}
    for step in _order(steps):
        spec = steps[step]
        tasks.append(PlanTask(task_id=step, objective=f"{contract.id}: {step}",
                              kind="tool", component_id=spec["tool"],
                              dependencies=tuple(spec.get("after") or ()),
                              destinations=(Destination.LOCAL_COMPUTE,),
                              max_label=Sensitivity.PUBLIC))
        evidence = None
        if spec.get("design"):
            evidence = EvidenceSpec(spec["design"], scope.population, scope.intervention,
                                    scope.outcome, provenance)
        contracts[step] = TaskContract(sensitivity=base.sensitivity, effects=base.effects,
                                       side_effect=base.side_effect, evidence=evidence)
    # Stating the claim is a model call; it runs on a local model, so the evidence and the
    # draft claim do not leave the machine before the release check.
    tasks.append(PlanTask(task_id="claim", objective=f"{contract.id}: state the {kind} claim",
                          dependencies=tuple(evidence_steps),
                          destinations=(Destination.LOCAL_MODEL,),
                          max_label=Sensitivity.PUBLIC))
    contracts["claim"] = TaskContract(
        sensitivity=base.sensitivity, effects=(Effect.LOCAL_MODEL,),
        side_effect=base.side_effect,
        claim=ClaimSpec(ClaimType(CLAIM_TYPES[kind]), scope.population, scope.intervention,
                        scope.outcome, tuple(evidence_steps)))
    plan = Plan(objective=objective or f"{contract.id} ({kind})", tasks=tuple(tasks),
                completion_criteria=(Criterion(f"{kind} claim stated", "claim"),),
                produced_by=f"skill:{contract.id}@{contract.version}")
    return ScientificProgram(plan, contracts)
