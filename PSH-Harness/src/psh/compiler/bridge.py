"""From this compiler's output to ``psh.workflow``'s, so a programme is checked twice.

The repository holds two scientific compilers and they are not rivals. They sit at
different ends of the same pipeline:

* ``psh.sir`` + ``psh.compiler`` (this package) is the **front half**. Its input is a
  programme of *declarations* — hypotheses, predictions, falsifiers, claims — alongside the
  operations, and it answers questions that only exist before a plan does: is this a
  scientific method at all, does the evidence licence the claim, does the analysis follow
  a protocol it depends on, what does a changed node force to be recomputed. Its output is
  a ``Plan``.
* ``psh.workflow`` is the **back half**. Its input is a ``Plan`` plus a ``TaskContract``
  per task, and it answers questions that only have answers once there is a plan and a
  registry: does this tool's *manifest* attest idempotency, does the bound protocol still
  match the one in the ledger, do the declared effects and the plan's destinations agree.

So a programme should cross. ``to_scientific_program`` takes a ``CompiledProgram`` and
produces a ``psh.workflow.ScientificProgram`` over the same plan, which
``ScientificCompiler`` then checks under the same envelope — and a programme that survives
both has been ruled on by every check either compiler knows.

**Crossing is strictly narrowing, never widening.** Three rules keep it that way:

1. *A vocabulary item with no equivalent is refused, never approximated.* A
   ``RECOMMENDATION`` claim and a ``GUIDELINE`` design have no counterpart in
   ``psh.workflow``'s enums. Mapping them to the nearest neighbour would let a guideline
   license a traditional-use claim, which is the exact substitution both matrices exist to
   stop. ``BridgeRefused`` names what could not cross.
2. *Where the two disagree, the stricter one decides.* ``EVIDENCE104`` requires evidence
   and claim to agree exactly on population, intervention and outcome; this compiler grades
   a population mismatch ``EXTRAPOLATED`` and continues. A programme this compiler passed
   with an extrapolation is therefore rejected on the far side — correctly, because both
   answers are on the record and the caller asked for the conjunction.
3. *Nothing is synthesised.* ``TaskContract.statistics`` needs a ``Protocol`` and
   ``protocol_binding`` needs a ledger record id and its fingerprint. This compiler has an
   ``AnalysisSpec``, which is a plan for an analysis and not a registered protocol.
   Manufacturing a ``Protocol`` from it would put content into a preregistration field that
   nobody preregistered, so the bridge leaves both fields empty and says so here. A caller
   that *has* a registered protocol attaches it to the contract itself.
"""

from __future__ import annotations

from typing import Any, Mapping

from ..contracts import ContractViolation
from ..sir import ClaimKind, Effect, SIRNode, SIRProgram, SideEffectClass, StudyDesign
from .lowering import executable_dependencies
from .pipeline import CompiledProgram

__all__ = ["BridgeRefused", "to_scientific_program", "DESIGN_NAMES", "CLAIM_KINDS",
           "EFFECT_NAMES", "SIDE_EFFECTS"]


class BridgeRefused(ContractViolation):
    """A compiled programme says something ``psh.workflow``'s vocabulary cannot carry."""


#: ``StudyDesign`` -> ``psh.workflow.ir.DESIGNS``. Absent members cannot cross.
#:
#: ``CASE_SERIES`` and ``NON_RANDOMISED_TRIAL`` map to the coarser name that admits the
#: *fewest* claims: a case series becomes ``case_report`` rather than ``observational``, and
#: a non-randomised trial becomes ``observational`` rather than ``randomized_trial``, so no
#: mapping can make a claim licensable that was not licensable before. ``COMMENTARY``,
#: ``GUIDELINE`` and ``UNKNOWN`` have no counterpart and are refused rather than rounded.
DESIGN_NAMES: Mapping[StudyDesign, str] = {
    StudyDesign.CLASSICAL_TEXT: "classical_text",
    StudyDesign.EXPERT_CONSENSUS: "expert_consensus",
    StudyDesign.IN_SILICO: "in_silico",
    StudyDesign.IN_VITRO: "in_vitro",
    StudyDesign.ANIMAL: "animal",
    StudyDesign.CASE_REPORT: "case_report",
    StudyDesign.CASE_SERIES: "case_report",
    StudyDesign.CROSS_SECTIONAL: "observational",
    StudyDesign.CASE_CONTROL: "observational",
    StudyDesign.COHORT: "observational",
    StudyDesign.NON_RANDOMISED_TRIAL: "observational",
    StudyDesign.RANDOMISED_TRIAL: "randomized_trial",
    StudyDesign.SYSTEMATIC_REVIEW: "systematic_review",
}

#: ``ClaimKind`` -> ``psh.workflow.ir.ClaimType``. ``RECOMMENDATION`` is absent: nothing on
#: the far side means "should be given to these patients", and the nearest name
#: (``CLINICAL``) is a weaker assertion that a single trial licenses.
CLAIM_KINDS: Mapping[ClaimKind, str] = {
    ClaimKind.ATTRIBUTION: "classical_attribution",
    ClaimKind.TRADITIONAL_USE: "traditional_use",
    ClaimKind.MECHANISM: "mechanism",
    ClaimKind.MECHANISM_HYPOTHESIS: "mechanism_hypothesis",
    ClaimKind.ASSOCIATION: "association",
    ClaimKind.EFFICACY: "clinical_efficacy",
    ClaimKind.SAFETY_SIGNAL: "safety_signal",
}

#: ``psh.sir.Effect`` -> ``psh.workflow.ir.Effect``. Several of this compiler's effects
#: name the same destination, which is the point of the finer vocabulary: ``PHI_READ`` and
#: ``READ_LOCAL`` both reach ``LOCAL_COMPUTE`` and only one of them raises the risk tier.
#: The coarser side keeps the destination, so ``EFFECT103`` still agrees.
EFFECT_NAMES: Mapping[Effect, str] = {
    Effect.READ_LOCAL: "local_read",
    Effect.WRITE_LOCAL: "local_compute",
    Effect.ARTIFACT_WRITE: "persist",
    Effect.PERSISTENT_MEMORY: "persist",
    Effect.NETWORK_TRUSTED: "trusted_remote",
    Effect.NETWORK_PUBLIC: "public_remote",
    Effect.LOCAL_MODEL: "local_model",
    Effect.REMOTE_MODEL_TRUSTED: "trusted_remote",
    Effect.REMOTE_MODEL_PUBLIC: "public_remote",
    Effect.PHI_READ: "local_compute",
    Effect.PHI_WRITE: "persist",
    Effect.SECRET_ACCESS: "local_compute",
    Effect.PATIENT_IDENTIFIABLE_OUTPUT: "user_output",
}

#: ``SideEffectClass`` -> ``psh.workflow.ir.SideEffect``. The two vocabularies agree
#: member for member except for ``AT_MOST_ONCE``/``AT_LEAST_ONCE``, which agree in meaning.
SIDE_EFFECTS: Mapping[SideEffectClass, str] = {
    SideEffectClass.PURE: "pure",
    SideEffectClass.IDEMPOTENT: "idempotent",
    SideEffectClass.AT_LEAST_ONCE: "at_least_once",
    SideEffectClass.AT_MOST_ONCE: "at_most_once",
    SideEffectClass.COMPENSATABLE: "compensatable",
    SideEffectClass.NON_REPEATABLE: "non_repeatable",
}

#: Effects with nothing to map onto, and why. ``DELEGATE`` spawns a sub-run rather than
#: reaching a destination; the other three are consequential acts whose destination the
#: coarse enum cannot express, and calling a wet-lab order ``local_compute`` would hide
#: exactly what a reviewer is looking for.
_UNMAPPABLE_EFFECTS: Mapping[Effect, str] = {
    Effect.DELEGATE: "delegation has no destination on the far side",
    Effect.CLINICAL_ACTION: "a clinical act is not one of the seven coarse effects",
    Effect.WETLAB_ACTION: "a wet-lab act is not one of the seven coarse effects",
    Effect.EXTERNAL_PUBLISH: "publication is not one of the seven coarse effects",
}


def to_scientific_program(compiled: CompiledProgram) -> Any:
    """A ``psh.workflow.ScientificProgram`` over this compilation's plan.

    Raises ``BridgeRefused`` when the programme says something the far side's vocabulary
    cannot carry, or when a contract it requires cannot be filled from what was declared —
    an ``EvidenceSpec`` needs a population, an intervention, an outcome and at least one
    provenance reference, and a programme that left them blank has not said enough to be
    checked twice.

    **Every** reason is collected before raising, in node order, the way the passes
    collect diagnostics. A programme that cannot cross usually cannot cross for more than
    one reason, and finding them one run at a time is the difference between an afternoon
    and a minute.
    """
    from ..workflow import ClaimSpec, ClaimType, Effect as WEffect, EvidenceSpec
    from ..workflow import ScientificProgram, SideEffect, TaskContract

    if compiled.plan is None:
        raise BridgeRefused(
            "a programme that did not compile has no plan to carry across; "
            f"{len(compiled.errors)} error(s) came first")

    program = compiled.program
    contracts: dict[str, TaskContract] = {}
    refusals: list[str] = []
    for node in program.nodes:
        if not node.executes:
            continue
        effects: list[Any] = []
        for effect in sorted(node.effects, key=lambda e: e.value):
            why = _UNMAPPABLE_EFFECTS.get(effect)
            if why is not None:
                refusals.append(
                    f"node {node.node_id!r} declares {effect.value!r}, which psh.workflow "
                    f"cannot express: {why}")
                continue
            effects.append(WEffect(EFFECT_NAMES[effect]))
        evidence = claim = None
        try:
            evidence = _evidence(node, EvidenceSpec)
        except BridgeRefused as exc:
            refusals.append(str(exc))
        try:
            claim = _claim(program, node, ClaimSpec, ClaimType)
        except BridgeRefused as exc:
            refusals.append(str(exc))
        contracts[node.node_id] = TaskContract(
            sensitivity=_sensitivity(node, compiled),
            effects=tuple(effects),
            side_effect=SideEffect(SIDE_EFFECTS[node.side_effect]),
            evidence=evidence, claim=claim)
    if refusals:
        raise BridgeRefused(
            f"{len(refusals)} thing(s) in this programme cannot cross to psh.workflow:\n  - "
            + "\n  - ".join(refusals))
    return ScientificProgram(plan=compiled.plan, contracts=contracts)


def _sensitivity(node: SIRNode, compiled: CompiledProgram) -> Any:
    """The declared data *floor* for this task.

    ``TaskContract.sensitivity`` is a floor, and the flow pass's derived label is a lower
    bound on what a node handles — it is the join of everything that reached it, so the
    value can be no *less* sensitive than that. The two meanings line up exactly, which is
    why the derived label may be used here and may not be used as a ceiling (that mistake
    is documented in ``lowering._max_label``). A node that declares its own label wins,
    since a declaration can only raise the floor above what the graph could infer.
    """
    if node.declared_label is not None:
        return node.declared_label.sensitivity
    if compiled.flow is not None:
        return compiled.flow.label_of(node.node_id).sensitivity
    from ..labels import Sensitivity
    return Sensitivity.PUBLIC


def _evidence(node: SIRNode, spec: type) -> Any:
    """The ``EvidenceSpec`` for a node that produces evidence, or ``None``."""
    evidence = node.produces.evidence
    if evidence is None:
        return None
    design = DESIGN_NAMES.get(evidence.design)
    if design is None:
        raise BridgeRefused(
            f"node {node.node_id!r} produces {evidence.design.value!r} evidence, which has "
            "no counterpart in psh.workflow's design vocabulary; mapping it to a "
            "neighbour would change what it licenses")
    missing = [name for name in ("population", "intervention", "outcome")
               if not str(getattr(evidence, name, "")).strip()]
    if missing:
        raise BridgeRefused(
            f"node {node.node_id!r} produces evidence with no {', '.join(missing)}; "
            "psh.workflow requires all three, and filling them in here would invent the "
            "scope of a study")
    if not evidence.identifier.strip():
        raise BridgeRefused(
            f"node {node.node_id!r} produces evidence with no identifier; psh.workflow "
            "requires a provenance reference and there is nothing to reference")
    underlying: tuple[str, ...] = ()
    if design == "systematic_review":
        # The far side requires a review to name what it reviewed. This compiler does not
        # model the underlying designs, so there is nothing honest to put here.
        raise BridgeRefused(
            f"node {node.node_id!r} produces a systematic review; psh.workflow requires it "
            "to declare its underlying study designs, which this IR does not model")
    return spec(design=design, population=evidence.population,
                intervention=evidence.intervention, outcome=evidence.outcome,
                provenance=(evidence.identifier.strip(),),
                underlying_designs=underlying)


def _claim(program: SIRProgram, node: SIRNode, spec: type, kinds: type) -> Any:
    """The ``ClaimSpec`` for a node that makes a claim, or ``None``."""
    claim = node.produces.claim
    if claim is None:
        return None
    name = CLAIM_KINDS.get(claim.kind)
    if name is None:
        raise BridgeRefused(
            f"node {node.node_id!r} makes {claim.kind.value!r} claim, which psh.workflow "
            "has no name for; the nearest one asserts something weaker, so the claim "
            "stays on this compiler")
    sources = tuple(
        dep for dep in executable_dependencies(program, node)
        if (n := program.node(dep)) is not None and n.produces.evidence is not None)
    if not sources:
        raise BridgeRefused(
            f"node {node.node_id!r} makes a claim with no evidence-producing dependency; "
            "psh.workflow requires a claim to name where its evidence came from")
    missing = [name_ for name_ in ("population", "intervention", "outcome")
               if not str(getattr(claim, name_, "")).strip()]
    if missing:
        raise BridgeRefused(
            f"node {node.node_id!r} makes a claim with no {', '.join(missing)}; "
            "psh.workflow requires all three of a claim as well as of its evidence")
    return spec(kind=kinds(name), population=claim.population,
                intervention=claim.intervention, outcome=claim.outcome,
                evidence_from=sources)
