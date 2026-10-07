"""psh — Physician-Scientist Harness: a governed control plane.

psh is not a bigger agent. It is the control plane a physician-scientist's work runs
through: a trusted kernel that classifies data and enforces where it may travel, a durable
WorkGraph that outlives models and agents, and a composition boundary so specialised
harnesses plug in rather than being merged.

Two defects in the predecessor design motivated it, both demonstrated in code before any of
this was written:

1. A PHI boundary enforced on tool arguments let a chart note reach a model provider
   through the context. Fixed by labelling data rather than call sites, and by treating a
   model call as egress.
2. Citation checking verified that a source existed, not that it supported the sentence.
   Fixed by ``ClaimSupport``.

Quick start::

    from psh import TrustedKernel, PSHConfig, Runner, get_profile
    from psh.protocols import SableAdapter

    profile = get_profile("clinical_research")
    kernel = TrustedKernel(PSHConfig(**profile.as_config_kwargs()))
    runner = Runner(kernel)
    result = runner.run("...", **profile.as_envelope_kwargs())
"""

from .config import PSHConfig, default_state_dir
from .environment import environment_report
from .policy import PolicyLattice, PolicySnapshot, PolicyViolation
from .contracts import (
    ApprovalDenied, ApprovalRequired, ArtifactRef, Autonomy, Budget, BudgetExhausted,
    CapabilityUnavailable, ComponentKind, ComponentManifest, ContextItem,
    ContextProjection, ContractViolation, DelegationContract, EgressDenied, EventEnvelope,
    ModelProfile, PendingResult, PolicyDenied, Principal, PSHError, ResultPending, RiskTier,
    RunEnvelope, VerificationFailed,
)
from .evidence import (
    Certainty, Claim, ClaimSupport, ClaimSupportVerifier, Directness, Evidence,
    EvidenceRecord, Relationship, RetractionStatus, SourceType, VerifiedSpan,
)
from .kernel import (
    AuthorityLattice, ExecutionResult, IngressGateway, ModelCallResult, ModelUsage,
    PendingOutcome, PersistenceGateway, Quarantine, TrustedKernel, ValidationStatus,
)
from .labels import (
    DNA_SEQUENCE, PROTEIN_SEQUENCE, RNA_SEQUENCE, SEQUENCE_FORMATS, DataLabel,
    Declassification, Destination, Labeled, Sensitivity, combine, declared_sequence_problems,
    deep_label_of, sequence_problem, unwrap_deep,
)
from .licensing import (
    INTEGRATION_MODES, LICENSE_CLASSES, LicenseClass, LicenseDecision, LicenseRuling,
    classify_license, license_ruling,
)
from .profiles import PROFILES, WorkProfile, get_profile, profile_names
from .runtime import (
    DEFAULT_SYSTEM_PROMPT, ResearchRunService, RunResult, Runner,
    ScientificResult, ScientificRunService,
)
from .workgraph import EdgeKind, NodeKind, WorkGraph
from .compiler import (
    CompiledProgram, CompileOptions, CompileRejected, Diagnostic, Severity,
    compile_program,
)
from .durable import WorkflowDelta, WorkflowJournal
from .scientist import (
    BeliefState, Deviation, HypothesisStatus, Protocol, ScientificCycle,
    ScientificWorldModel, Stage,
)
from .sir import (
    ClaimKind, ClaimType, Effect, EvidenceType, ExecKind, Role, SIRNode, SIRProgram,
    ScientificType, SideEffectClass, StudyDesign, Subject, licenses,
)

#: Kept in step with ``pyproject.toml`` by ``tests/test_review_v5_1.py``. It read
#: "0.4.0" through the whole of v0.5, so ``psh.__version__`` named a release two
#: behind the package it was reporting on.
__version__ = "0.6.0"

__all__ = [
    "TrustedKernel", "PSHConfig", "Runner", "RunResult", "WorkGraph", "NodeKind",
    "EdgeKind", "RunEnvelope", "Budget", "Principal", "RiskTier", "Autonomy",
    "ComponentManifest", "ComponentKind", "ContextItem", "ContextProjection",
    "DelegationContract", "EventEnvelope", "ModelProfile", "ArtifactRef",
    "DataLabel", "Labeled", "Declassification", "Sensitivity", "Destination", "combine",
    "Claim", "Evidence", "ClaimSupport", "ClaimSupportVerifier", "Certainty",
    "Relationship", "Directness",
    "WorkProfile", "PROFILES", "get_profile", "profile_names",
    "PSHError", "PolicyDenied", "EgressDenied", "BudgetExhausted", "ApprovalRequired",
    "ApprovalDenied", "VerificationFailed", "CapabilityUnavailable", "ContractViolation",
    "DEFAULT_SYSTEM_PROMPT", "default_state_dir", "environment_report", "__version__",
    "PolicySnapshot", "PolicyLattice", "PolicyViolation", "AuthorityLattice", "IngressGateway", "PersistenceGateway",
    "Quarantine", "ExecutionResult", "ModelCallResult", "ModelUsage", "ValidationStatus",
    # long jobs: work a call started and has not finished is pending, never a result
    "PendingResult", "PendingOutcome", "ResultPending",
    "EvidenceRecord", "VerifiedSpan", "SourceType", "RetractionStatus",
    "deep_label_of", "unwrap_deep",
    # typed inputs: a component declares which of its fields hold biological sequences
    "PROTEIN_SEQUENCE", "DNA_SEQUENCE", "RNA_SEQUENCE", "SEQUENCE_FORMATS",
    "sequence_problem", "declared_sequence_problems",
    "LicenseClass", "LicenseDecision", "LicenseRuling", "classify_license",
    "license_ruling", "INTEGRATION_MODES", "LICENSE_CLASSES",
    # v0.6 — the scientific compiler and the scientist plane
    "SIRProgram", "SIRNode", "Role", "ExecKind", "Effect", "SideEffectClass",
    "ScientificType", "EvidenceType", "ClaimType", "StudyDesign", "Subject", "ClaimKind",
    "licenses",
    "compile_program", "CompiledProgram", "CompileOptions", "CompileRejected",
    "Diagnostic", "Severity",
    "ScientificRunService", "ScientificResult", "ResearchRunService",
    "ScientificWorldModel", "BeliefState", "HypothesisStatus", "ScientificCycle", "Stage",
    "Protocol", "Deviation",
    "WorkflowJournal", "WorkflowDelta",
]
