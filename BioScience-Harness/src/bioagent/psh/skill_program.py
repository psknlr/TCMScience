"""A skill contract as a PSH ``ScientificProgram``, so PSH's compiler checks it.

``skill.yaml`` (``providers.skills.SkillContract``) says which tools a skill runs in which
order, which steps produce evidence of which study design, and the strongest claim it may
make. This adapter turns that into the program PSH already knows how to compile: one tool
task per step, an ``EvidenceSpec`` on each evidence step, and one claim task whose
``ClaimSpec`` names the evidence steps. The PSH compiler then refuses what it refuses
everywhere — a claim type the designs cannot license (``in_silico`` supports only a
mechanism hypothesis), evidence that is not a direct dependency, a scope mismatch — and no
new compiler is written.

Where a step's data goes is read from the component that runs it, not assumed. Every tool
task used to be written ``Destination.LOCAL_COMPUTE`` with a ``PUBLIC`` ceiling whatever
the component did, so a step that sends its query to a public web service was compiled as
local compute and PSH's egress and label checks ruled on a destination that was false.
Each step's destinations, label ceiling, risk and effects now come from the admitted
component its tool names (a PSH manifest: what :meth:`BioScienceBridge.admit` returns, or
:func:`~bioagent.psh.manifest.bridge_manifest` derives by the same rules), met with the
run's envelope.

The adapter refuses, before PSH sees the program: a claim kind above the skill's ceiling;
a skill with no evidence step; a step whose tool is not an admitted component; a step whose
component reaches a destination, or needs authority, the run does not hold. Without a
component registry or an envelope it refuses outright — guessing local compute is exactly
the error this replaces, and a guess that is wrong in the safe direction for one caller is
wrong in the unsafe direction for the next.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

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


def _names(destinations: Iterable[Any]) -> str:
    return ", ".join(sorted(d.name for d in destinations)) or "none"


def skill_program(contract: Any, scope: ClaimScope, *,
                  components: Mapping[str, Any] | None = None, envelope: Any = None,
                  claim_kind: str | None = None, provenance: Iterable[str] = (),
                  objective: str = "") -> Any:
    """The ``ScientificProgram`` for running ``contract`` to a ``claim_kind`` claim.

    ``components`` maps each tool a step names to the admitted component that runs it (a
    PSH ``ComponentManifest``). ``envelope`` is the ``RunEnvelope`` the run executes under:
    a step is refused if its component reaches a destination the envelope does not permit
    or is incompatible with it otherwise (risk, mutation, licence), and its label ceiling
    is the lowest of the component's, the envelope's and its destinations'. ``provenance``
    names what the evidence rests on — the snapshot ids the run loaded — and becomes each
    ``EvidenceSpec``'s provenance.
    """
    from psh.labels import DEFAULT_CEILINGS, Destination, Sensitivity
    from psh.runtime import Criterion, Plan, PlanTask
    from psh.workflow import (ClaimSpec, ClaimType, Effect, EvidenceSpec, ScientificProgram,
                              SideEffect, TaskContract)

    effect_for = {Destination.LOCAL_COMPUTE: Effect.LOCAL_COMPUTE,
                  Destination.LOCAL_MODEL: Effect.LOCAL_MODEL,
                  Destination.TRUSTED_REMOTE: Effect.TRUSTED_REMOTE,
                  Destination.PUBLIC_REMOTE: Effect.PUBLIC_REMOTE,
                  Destination.PERSISTENT: Effect.PERSIST,
                  Destination.USER_OUTPUT: Effect.USER_OUTPUT}

    kind = claim_kind or contract.max_claim_kind
    if kind not in CLAIM_TYPES:
        raise SkillProgramError(f"claim kind {kind!r} has no PSH claim type")
    if not contract.permits(kind):
        raise SkillProgramError(f"skill {contract.id} may not make {kind} claims "
                                f"(ceiling {contract.max_claim_kind})")
    if components is None:
        raise SkillProgramError(
            f"skill {contract.id}: no component registry was given, so where its steps send "
            "data cannot be derived; pass components={tool: admitted manifest} rather than "
            "let every step be assumed local compute")
    if envelope is None:
        raise SkillProgramError(
            f"skill {contract.id}: no run envelope was given, so whether the run may reach "
            "what its steps reach cannot be checked")
    steps = {k: dict(v) for k, v in contract.steps.items()}
    evidence_steps = [s for s in _order(steps) if steps[s].get("design")]
    if not evidence_steps:
        raise SkillProgramError(f"skill {contract.id} declares no evidence step")
    provenance = tuple(provenance) or (f"skill:{contract.id}@{contract.version}",)
    run_ceiling = Sensitivity(getattr(envelope.max_label, "sensitivity", envelope.max_label))

    tasks, contracts = [], {}
    for step in _order(steps):
        spec = steps[step]
        tool = spec["tool"]
        component = components.get(tool)
        if component is None:
            raise SkillProgramError(
                f"step {step!r} runs tool {tool!r}, which is not an admitted component "
                f"(admitted: {', '.join(sorted(components)) or 'none'})")
        reach = tuple(dict.fromkeys(component.destinations))
        if not reach:
            raise SkillProgramError(
                f"step {step!r}: component {component.id!r} for tool {tool!r} names no "
                "destination, so where the step sends data is unknown")
        outside = [d for d in reach if not envelope.permits_destination(d)]
        if outside:
            raise SkillProgramError(
                f"step {step!r} runs tool {tool!r} on component {component.id!r}, which "
                f"reaches {_names(outside)}; the run authorises "
                f"{_names(envelope.allowed_destinations)}")
        ok, why = component.compatible_with(envelope)
        if not ok:
            raise SkillProgramError(f"step {step!r} runs tool {tool!r} on component "
                                    f"{component.id!r}, which the run does not admit: {why}")
        # The lowest of the component's ceiling, the run's and every destination's: a
        # step reaching a public service holds no more than that service may receive.
        ceiling = min(Sensitivity(component.max_label), run_ceiling,
                      *(DEFAULT_CEILINGS[d] for d in reach))
        tasks.append(PlanTask(task_id=step, objective=f"{contract.id}: {step} ({tool})",
                              kind="tool", component_id=component.id,
                              dependencies=tuple(spec.get("after") or ()),
                              destinations=reach, max_risk=component.risk_tier,
                              max_label=ceiling))
        evidence = None
        if spec.get("design"):
            evidence = EvidenceSpec(spec["design"], scope.population, scope.intervention,
                                    scope.outcome, provenance)
        contracts[step] = TaskContract(sensitivity=Sensitivity.PUBLIC,
                                       effects=tuple(effect_for[d] for d in reach),
                                       side_effect=SideEffect.NON_REPEATABLE,
                                       evidence=evidence)
    # Stating the claim is a model call; it runs on a local model, so the evidence and the
    # draft claim do not leave the machine before the release check.
    if not envelope.permits_destination(Destination.LOCAL_MODEL):
        raise SkillProgramError(
            f"step 'claim' states the claim on a local model (LOCAL_MODEL); the run "
            f"authorises {_names(envelope.allowed_destinations)}")
    tasks.append(PlanTask(task_id="claim", objective=f"{contract.id}: state the {kind} claim",
                          dependencies=tuple(evidence_steps),
                          destinations=(Destination.LOCAL_MODEL,),
                          max_label=Sensitivity.PUBLIC))
    contracts["claim"] = TaskContract(
        sensitivity=Sensitivity.PUBLIC, effects=(Effect.LOCAL_MODEL,),
        side_effect=SideEffect.NON_REPEATABLE,
        claim=ClaimSpec(ClaimType(CLAIM_TYPES[kind]), scope.population, scope.intervention,
                        scope.outcome, tuple(evidence_steps)))
    plan = Plan(objective=objective or f"{contract.id} ({kind})", tasks=tuple(tasks),
                completion_criteria=(Criterion(f"{kind} claim stated", "claim"),),
                produced_by=f"skill:{contract.id}@{contract.version}")
    return ScientificProgram(plan, contracts)
