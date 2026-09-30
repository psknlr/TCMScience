"""The effect system: what an operation does to the world, not what it returns.

A type system answers *what is this value?*. It cannot answer the question a governed
research runtime actually has to answer before it runs a step:

    What will this operation do — and is this run allowed to have that done?

``ComponentManifest`` already carries part of the answer (``destinations``, ``mutates``,
``requires_network``, ``human_approval``), and the gateways check it **at the call**. That
is the right place for the final word and the wrong place for the only word: a plan whose
sixth step will call a public model with identifiable data is refusable at step one, and
finding out at step six means five steps of budget and five steps of side effects have
already happened.

So a node declares its effects, and the compiler checks the declaration against the run's
authority before anything executes. Three properties make that safe rather than
decorative:

* **Effects are a lower bound, never a grant.** Declaring ``NETWORK_PUBLIC`` does not
  obtain the network; it obliges the run to hold it. A node that declares fewer effects
  than its component actually has is not thereby permitted more — the gateway still rules
  at the call, from the manifest, which the node cannot edit.
* **The table is static.** ``EFFECTS`` below maps each effect to the destination it
  reaches, the autonomy and risk it needs, whether it requires approval, and the highest
  sensitivity that may flow *into* it. One table, so the compiler and the audit trail
  cannot disagree about what an effect means.
* **Side effects have a repeat semantics.** "Exactly once" is a promise no distributed
  system keeps, so ``SideEffectClass`` says what may actually be done to a call whose
  outcome is unknown: re-run it, re-run it with a key, compensate it, or refuse to touch
  it and escalate. A literature search may be retried; submitting a wet-lab order may not.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable, Mapping

from ..contracts import Autonomy, RiskTier
from ..labels import DEFAULT_CEILINGS, Destination, Sensitivity

__all__ = [
    "Effect", "EffectSpec", "EFFECTS", "SideEffectClass", "IMPLIED_SIDE_EFFECT",
    "effect_set", "destinations_for", "ceiling_for", "min_autonomy_for", "min_risk_for",
    "needs_approval", "mutating", "describe_effects",
]


class Effect(str, Enum):
    """One thing an operation does. A node declares the set of them it performs."""

    # ---- compute and storage
    READ_LOCAL = "read_local"
    WRITE_LOCAL = "write_local"
    ARTIFACT_WRITE = "artifact_write"
    PERSISTENT_MEMORY = "persistent_memory"

    # ---- network and models
    NETWORK_TRUSTED = "network.trusted"
    NETWORK_PUBLIC = "network.public"
    LOCAL_MODEL = "model.local"
    REMOTE_MODEL_TRUSTED = "model.trusted_remote"
    REMOTE_MODEL_PUBLIC = "model.public_remote"
    DELEGATE = "delegate"

    # ---- data the operation touches
    PHI_READ = "phi_read"
    PHI_WRITE = "phi_write"
    SECRET_ACCESS = "secret_access"

    # ---- consequential acts, which are the reason this enum is not just "network or not"
    CLINICAL_ACTION = "clinical_action"
    WETLAB_ACTION = "wetlab_action"
    EXTERNAL_PUBLISH = "external_publish"
    PATIENT_IDENTIFIABLE_OUTPUT = "patient_identifiable_output"


class SideEffectClass(str, Enum):
    """What may be done to a call whose outcome is not known.

    ``ComponentManifest.idempotent`` is a boolean, and a boolean cannot distinguish "safe
    to retry" from "safe to retry **with the same key**" from "must never be retried, ask
    a human". The operation ledger already records that an attempt is *in doubt*; this
    says what the runtime may do about it.
    """

    PURE = "pure"                    # deterministic, no observable effect: free to re-run
    IDEMPOTENT = "idempotent"        # re-running produces the same world
    AT_LEAST_ONCE = "at_least_once"  # duplicates are acceptable
    COMPENSATABLE = "compensatable"  # re-runnable once the compensation has run
    AT_MOST_ONCE = "at_most_once"    # a duplicate is a defect; never re-run in doubt
    NON_REPEATABLE = "non_repeatable"  # a duplicate is harmful; a human reconciles it

    @property
    def retryable(self) -> bool:
        """Whether a *failed* attempt may be tried again automatically."""
        return self in (SideEffectClass.PURE, SideEffectClass.IDEMPOTENT,
                        SideEffectClass.AT_LEAST_ONCE, SideEffectClass.COMPENSATABLE)

    @property
    def replayable(self) -> bool:
        """Whether an attempt **in doubt** may be run again without asking anyone.

        The distinction from ``retryable`` is the whole point. A failed call is known not
        to have happened; a call in doubt may have happened, and for ``AT_MOST_ONCE`` the
        two have opposite correct responses.
        """
        return self in (SideEffectClass.PURE, SideEffectClass.IDEMPOTENT,
                        SideEffectClass.AT_LEAST_ONCE)

    @property
    def cacheable(self) -> bool:
        """Whether a recorded result may stand in for running the call again.

        Everything is cacheable in this sense — reusing a recorded result performs no new
        effect — *except* a call whose value is its freshness. That is a property of the
        component, not of this class, so the default is permissive and a node turns it off
        with ``deterministic=False`` plus ``cache=False``.
        """
        return True


@dataclass(frozen=True, slots=True)
class EffectSpec:
    """What one effect requires of the run that performs it."""

    effect: Effect
    description: str
    destination: Destination | None = None
    min_autonomy: Autonomy = Autonomy.OBSERVE
    min_risk: RiskTier = RiskTier.R0_TRIVIAL
    approval: bool = False
    mutates: bool = False
    #: The highest sensitivity that may flow **into** an operation with this effect.
    #: ``None`` means "take the destination's ceiling", so the table below states a value
    #: only where it differs from ``labels.DEFAULT_CEILINGS`` — one place per decision.
    ceiling: Sensitivity | None = None
    #: The weakest repeat semantics this effect is compatible with. A node declaring
    #: ``WETLAB_ACTION`` and ``side_effect=PURE`` is stating two incompatible things.
    implies: "SideEffectClass | None" = None

    def max_sensitivity(self) -> Sensitivity:
        if self.ceiling is not None:
            return self.ceiling
        if self.destination is not None:
            return DEFAULT_CEILINGS.get(self.destination, Sensitivity.PUBLIC)
        return Sensitivity.SECRET


E = Effect
A = Autonomy
R = RiskTier
S = Sensitivity
DST = Destination

#: The one table. Adding an effect means adding a row here; every pass reads this rather
#: than carrying its own opinion about what an effect needs.
EFFECTS: Mapping[Effect, EffectSpec] = {
    E.READ_LOCAL: EffectSpec(
        E.READ_LOCAL, "reads local files or in-process data",
        destination=DST.LOCAL_COMPUTE),
    E.WRITE_LOCAL: EffectSpec(
        E.WRITE_LOCAL, "writes local files", destination=DST.LOCAL_COMPUTE,
        min_autonomy=A.ACT_WITH_APPROVAL, min_risk=R.R1_ROUTINE, mutates=True,
        implies=SideEffectClass.IDEMPOTENT),
    E.ARTIFACT_WRITE: EffectSpec(
        E.ARTIFACT_WRITE, "writes a content-addressed artifact",
        destination=DST.PERSISTENT, min_autonomy=A.ACT_WITH_APPROVAL, mutates=True,
        implies=SideEffectClass.IDEMPOTENT),
    E.PERSISTENT_MEMORY: EffectSpec(
        E.PERSISTENT_MEMORY, "commits to durable project memory",
        destination=DST.PERSISTENT, min_autonomy=A.ACT_WITH_APPROVAL,
        min_risk=R.R1_ROUTINE, mutates=True, implies=SideEffectClass.IDEMPOTENT),
    E.NETWORK_TRUSTED: EffectSpec(
        E.NETWORK_TRUSTED, "calls a service under an institutional agreement",
        destination=DST.TRUSTED_REMOTE, min_risk=R.R1_ROUTINE),
    E.NETWORK_PUBLIC: EffectSpec(
        E.NETWORK_PUBLIC, "calls a public service", destination=DST.PUBLIC_REMOTE,
        min_risk=R.R1_ROUTINE),
    E.LOCAL_MODEL: EffectSpec(
        E.LOCAL_MODEL, "prompts a model on local hardware", destination=DST.LOCAL_MODEL),
    E.REMOTE_MODEL_TRUSTED: EffectSpec(
        E.REMOTE_MODEL_TRUSTED, "prompts a model at a provider under agreement",
        destination=DST.TRUSTED_REMOTE, min_risk=R.R1_ROUTINE),
    E.REMOTE_MODEL_PUBLIC: EffectSpec(
        E.REMOTE_MODEL_PUBLIC, "prompts a model at a public provider",
        destination=DST.PUBLIC_REMOTE, min_risk=R.R1_ROUTINE),
    E.DELEGATE: EffectSpec(
        E.DELEGATE, "hands a sub-objective to a child agent",
        min_autonomy=A.SUGGEST, min_risk=R.R1_ROUTINE, ceiling=S.SECRET),
    E.PHI_READ: EffectSpec(
        E.PHI_READ, "reads protected health information",
        destination=DST.LOCAL_COMPUTE, min_risk=R.R3_CLINICAL, ceiling=S.PHI),
    E.PHI_WRITE: EffectSpec(
        E.PHI_WRITE, "writes protected health information",
        destination=DST.PERSISTENT, min_autonomy=A.ACT_WITH_APPROVAL,
        min_risk=R.R3_CLINICAL, mutates=True, ceiling=S.PHI,
        implies=SideEffectClass.IDEMPOTENT),
    E.SECRET_ACCESS: EffectSpec(
        E.SECRET_ACCESS, "reads credential material", destination=DST.LOCAL_COMPUTE,
        min_autonomy=A.ACT_WITH_APPROVAL, min_risk=R.R2_CONSEQUENTIAL, approval=True,
        ceiling=S.SECRET),
    E.CLINICAL_ACTION: EffectSpec(
        E.CLINICAL_ACTION, "changes something about a patient's care",
        min_autonomy=A.ACT_WITH_APPROVAL, min_risk=R.R3_CLINICAL, approval=True,
        mutates=True, ceiling=S.PHI, implies=SideEffectClass.NON_REPEATABLE),
    E.WETLAB_ACTION: EffectSpec(
        E.WETLAB_ACTION, "commits laboratory resources (an order, a sample, an instrument)",
        min_autonomy=A.ACT_WITH_APPROVAL, min_risk=R.R2_CONSEQUENTIAL, approval=True,
        mutates=True, ceiling=S.SENSITIVE, implies=SideEffectClass.NON_REPEATABLE),
    E.EXTERNAL_PUBLISH: EffectSpec(
        E.EXTERNAL_PUBLISH, "sends content outside the institution",
        destination=DST.PUBLIC_REMOTE, min_autonomy=A.ACT_WITH_APPROVAL,
        min_risk=R.R2_CONSEQUENTIAL, approval=True, mutates=True,
        implies=SideEffectClass.AT_MOST_ONCE),
    E.PATIENT_IDENTIFIABLE_OUTPUT: EffectSpec(
        E.PATIENT_IDENTIFIABLE_OUTPUT, "returns identifiable content to a person",
        destination=DST.USER_OUTPUT, min_risk=R.R3_CLINICAL, ceiling=S.PHI),
}

#: Effects that force a repeat semantics regardless of what the node claims.
IMPLIED_SIDE_EFFECT: Mapping[Effect, SideEffectClass] = {
    effect: spec.implies for effect, spec in EFFECTS.items() if spec.implies is not None
}


def effect_set(values: Iterable[Any]) -> frozenset[Effect]:
    """Parse a declared effect list, refusing names the table does not define.

    An unknown effect is an error rather than a warning, for the reason ``PlanValidator``
    refuses an unknown acceptance-test kind: an effect nobody can check reads exactly like
    an effect that was checked and passed.
    """
    out: set[Effect] = set()
    for value in values or ():
        if isinstance(value, Effect):
            out.add(value)
            continue
        name = str(value).strip()
        try:
            out.add(Effect(name))
        except ValueError:
            try:
                out.add(Effect[name.upper()])
            except KeyError:
                raise ValueError(
                    f"unknown effect {name!r}; declared effects must be among "
                    f"{sorted(e.value for e in Effect)}") from None
    return frozenset(out)


def destinations_for(effects: Iterable[Effect]) -> frozenset[Destination]:
    """Every destination the declared effects reach."""
    return frozenset(EFFECTS[e].destination for e in effects
                     if EFFECTS[e].destination is not None)


def ceiling_for(effects: Iterable[Effect]) -> Sensitivity:
    """The highest sensitivity that may flow into an operation performing these effects.

    The minimum over the effects, because performing two effects means satisfying both:
    a step that reads PHI *and* prompts a public model is bounded by the public model.
    That combination is exactly the one the compiler exists to refuse before it runs.
    """
    ceilings = [EFFECTS[e].max_sensitivity() for e in effects]
    return min(ceilings) if ceilings else Sensitivity.SECRET


def min_autonomy_for(effects: Iterable[Effect]) -> Autonomy:
    """The strongest autonomy any declared effect requires."""
    from ..contracts import _autonomy_rank

    required = [EFFECTS[e].min_autonomy for e in effects] or [Autonomy.OBSERVE]
    return min(required, key=_autonomy_rank)


def min_risk_for(effects: Iterable[Effect]) -> RiskTier:
    """The highest risk tier any declared effect incurs."""
    tiers = [EFFECTS[e].min_risk for e in effects]
    return max(tiers) if tiers else RiskTier.R0_TRIVIAL


def needs_approval(effects: Iterable[Effect]) -> bool:
    return any(EFFECTS[e].approval for e in effects)


def mutating(effects: Iterable[Effect]) -> bool:
    return any(EFFECTS[e].mutates for e in effects)


def describe_effects(effects: Iterable[Effect]) -> str:
    ordered = sorted(effects, key=lambda e: e.value)
    return ", ".join(e.value for e in ordered) or "none declared"
