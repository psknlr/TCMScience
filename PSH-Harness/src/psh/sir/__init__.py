"""The Scientific IR: the object the compiler analyses and the runtime executes.

``psh.runtime.plan`` is the executable plan — three task kinds, authority per task, the
thing ``AgentLoopController`` dispatches. It is complete as an execution format and it has
no way to say *why* a step exists, what hypothesis it tests, what evidence it produces or
what claim rests on it. Those are the properties a research harness has to check, so they
need a representation, and this is it.

Lowering (``psh.compiler.lowering``) turns a compiled program into exactly that ``Plan``.
The IR therefore adds a layer of analysis and **no** execution path: everything still runs
through the broker, the gates and the release gate.
"""

from .effects import (
    EFFECTS, IMPLIED_SIDE_EFFECT, Effect, EffectSpec, SideEffectClass, ceiling_for,
    describe_effects, destinations_for, effect_set, min_autonomy_for, min_risk_for,
    mutating, needs_approval,
)
from .frontend import ProgramBuilder, ProgramParseError, parse_program
from .nodes import (
    Acceptance, AnalysisSpec, ClaimSpec, DECLARATIVE_ROLES, ExecKind, ExperimentSpec,
    FanOut, HypothesisSpec, Port, PredictionSpec, ReproSpec, Role, SIRNode, SIRProgram,
)
from .values import (
    CLINICAL_KINDS, LICENSING, MAX_CERTAINTY, UNEVIDENTIAL, ClaimKind, ClaimType,
    DataType, EvidenceType, FlowLabel, LicenseVerdict, Licensing, Provenance,
    ScientificType, StudyDesign, Subject, join_labels, licenses, normalise_term,
    provenance_rank,
)

__all__ = [
    # values
    "StudyDesign", "Subject", "Provenance", "ClaimKind", "Licensing", "LicenseVerdict",
    "EvidenceType", "ClaimType", "DataType", "ScientificType", "FlowLabel",
    "LICENSING", "MAX_CERTAINTY", "CLINICAL_KINDS", "UNEVIDENTIAL",
    "licenses", "provenance_rank", "normalise_term", "join_labels",
    # effects
    "Effect", "EffectSpec", "EFFECTS", "SideEffectClass", "IMPLIED_SIDE_EFFECT",
    "effect_set", "destinations_for", "ceiling_for", "min_autonomy_for", "min_risk_for",
    "needs_approval", "mutating", "describe_effects",
    # nodes
    "Role", "ExecKind", "Port", "FanOut", "AnalysisSpec", "ExperimentSpec",
    "HypothesisSpec", "PredictionSpec", "ClaimSpec", "ReproSpec", "SIRNode", "SIRProgram",
    "Acceptance", "DECLARATIVE_ROLES",
    # frontends
    "parse_program", "ProgramParseError", "ProgramBuilder",
]
