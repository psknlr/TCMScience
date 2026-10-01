"""A programme compiled by ``psh.compiler`` and checked again by ``psh.workflow``.

The two compilers sit at opposite ends of one pipeline — declarations in, plan out;
plan in, verdict out — and this file is the evidence that the seam holds. The tests that
matter are the refusals: crossing must be **narrowing**, so a programme cannot become
acceptable by changing compilers, and a vocabulary item with no counterpart must stop at
the bridge instead of arriving as its nearest neighbour.
"""

from __future__ import annotations

import pytest

from psh.capabilities import CapabilityRegistry
from psh.compiler import BridgeRefused, compile_program, to_scientific_program
from psh.config import PSHConfig
from psh.contracts import (
    Autonomy, ComponentKind, ComponentManifest, RiskTier,
)
from psh.evidence.support import Certainty
from psh.kernel import TrustedKernel
from psh.labels import Destination, Sensitivity
from psh.policy import PolicySnapshot
from psh.runtime.plan_validator import PlanRejected
from psh.sir import (
    Acceptance, ClaimKind, ClaimSpec, ClaimType, Effect, EvidenceType, ExecKind,
    HypothesisSpec, Port, PredictionSpec, Provenance, ReproSpec, Role, SIRNode, SIRProgram,
    ScientificType, SideEffectClass, StudyDesign, Subject,
)
from psh.workflow import ScientificCompiler, ScientificProgram

WIDE = (Destination.LOCAL_COMPUTE, Destination.LOCAL_MODEL, Destination.USER_OUTPUT,
        Destination.PERSISTENT, Destination.PUBLIC_REMOTE)
SCHEMA = {"type": "object", "required": ["finding", "evidence"]}

POPULATION = "adults with chronic heart failure"
INTERVENTION = "huangqi decoction"
OUTCOME = "6-minute walk distance"


@pytest.fixture
def bench(tmp_path):
    policy = PolicySnapshot(profile_id="bridge", max_data_label=Sensitivity.PHI,
                            allowed_destinations=WIDE, autonomy=Autonomy.ACT,
                            risk_ceiling=RiskTier.R3_CLINICAL,
                            require_claim_support=False)
    kernel = TrustedKernel(PSHConfig(state_dir=tmp_path / "k").ensure_dirs(),
                           policy=policy)
    registry = CapabilityRegistry()
    registry.register(_Tool("pubmed"))
    envelope = kernel.envelope(project_id=kernel.graph.project("bridge").id)
    yield kernel, registry, envelope, policy
    kernel.close()


class _Tool:
    def __init__(self, component_id: str, *, idempotent: bool = True):
        self.manifest = ComponentManifest(
            id=component_id, name=component_id, kind=ComponentKind.TOOL,
            max_label=Sensitivity.PHI, destinations=(Destination.PUBLIC_REMOTE,),
            requires_network=True, idempotent=idempotent)

    def invoke(self, payload, envelope):  # pragma: no cover - never executed here
        return {}


def program(*, design=StudyDesign.RANDOMISED_TRIAL, claim_kind=ClaimKind.EFFICACY,
            claim_population=POPULATION, effects=None,
            claim_side_effect=SideEffectClass.PURE,
            identifier="40011222", outcome=OUTCOME):
    """A minimal two-node programme: retrieve a trial, state what it supports."""
    return SIRProgram(
        question="Does huangqi decoction improve walking distance?",
        project_id="", program_id="prog_bridge",
        acceptance=(Acceptance(description="a claim is supported by evidence",
                               kind="claim"),),
        nodes=(
            SIRNode(node_id="h1", role=Role.HYPOTHESIS, hypothesis=HypothesisSpec(
                proposition="huangqi decoction improves exercise tolerance",
                scope=POPULATION, alternative_to=("h2",))),
            SIRNode(node_id="h2", role=Role.HYPOTHESIS, hypothesis=HypothesisSpec(
                proposition="the improvement is a placebo response",
                scope=POPULATION, alternative_to=("h1",))),
            SIRNode(node_id="p1", role=Role.PREDICTION, depends_on=("h1",),
                    prediction=PredictionSpec(statement="walk distance rises",
                                              hypothesis="h1",
                                              falsifier="walk distance is unchanged")),
            SIRNode(node_id="fetch", role=Role.RETRIEVAL, kind=ExecKind.TOOL,
                    objective="retrieve the trial", component_id="pubmed",
                    effects={Effect.NETWORK_PUBLIC}, about=("p1",),
                    side_effect=SideEffectClass.IDEMPOTENT,
                    produces=ScientificType(evidence=EvidenceType(
                        design=design, subject=Subject.HUMAN, population=POPULATION,
                        intervention=INTERVENTION, outcome=outcome,
                        provenance=Provenance.RETRIEVED_VERIFIED,
                        identifier=identifier)),
                    repro=ReproSpec(dataset_version="2026-09", tool_version="1.2"),
                    estimated_tokens=100, estimated_seconds=2),
            SIRNode(node_id="claim", role=Role.CLAIM, kind=ExecKind.MODEL,
                    objective="state what the trial supports", depends_on=("fetch",),
                    effects=set(effects) if effects else {Effect.LOCAL_MODEL},
                    side_effect=claim_side_effect, about=("h1",),
                    inputs=(Port(argument="evidence", source="fetch",
                                 pointer="/abstract"),),
                    produces=ScientificType(claim=ClaimType(
                        kind=claim_kind, subject=Subject.HUMAN,
                        population=claim_population, intervention=INTERVENTION,
                        outcome=OUTCOME, certainty=Certainty.MODERATE)),
                    claim=ClaimSpec(statement="huangqi decoction improves walk distance"),
                    output_schema=SCHEMA, acceptance_tests=({"kind": "output_schema"},),
                    estimated_tokens=300, estimated_seconds=4)))


def cross(compiled):
    return to_scientific_program(compiled)


# ===================================================================== it crosses

def test_a_programme_compiled_here_is_accepted_by_the_other_compiler(bench):
    kernel, registry, envelope, policy = bench
    compiled = compile_program(program(), envelope, registry=registry, policy=policy)
    assert compiled.ok, [d.render() for d in compiled.errors]

    carried = cross(compiled)
    assert isinstance(carried, ScientificProgram)
    assert set(carried.contracts) == {"fetch", "claim"}, (
        "declarations are not tasks, so only the executable nodes get contracts")

    compilation = ScientificCompiler(registry).compile(carried, envelope, policy=policy)
    assert compilation.plan.plan_id == compiled.plan.plan_id
    assert compilation.fingerprint, "the far side produced a content fingerprint"


def test_the_carried_contracts_say_what_the_nodes_declared(bench):
    kernel, registry, envelope, policy = bench
    compiled = compile_program(program(), envelope, registry=registry, policy=policy)
    carried = cross(compiled)

    fetch = carried.contracts["fetch"]
    assert fetch.evidence is not None and fetch.claim is None
    assert fetch.evidence.design == "randomized_trial"
    assert fetch.evidence.provenance == ("40011222",)
    assert fetch.side_effect.value == "idempotent"
    assert [e.value for e in fetch.effects] == ["public_remote"]

    claim = carried.contracts["claim"]
    assert claim.evidence is None and claim.claim is not None
    assert claim.claim.kind.value == "clinical_efficacy"
    assert claim.claim.evidence_from == ("fetch",), (
        "the claim names the dependency that produced its evidence")

    assert all(c.statistics is None and c.protocol_binding is None
               for c in carried.contracts.values()), (
        "the bridge synthesises no preregistration it was not given")


# ================================================================ it only narrows

def test_the_far_side_catches_what_this_one_cannot_see(bench):
    """The reason crossing is worth doing, in one test.

    A node may declare ``IDEMPOTENT`` and this compiler believes it: a declaration is all
    it has, and it checks that the declaration is *consistent* — a step declared
    repeat-safe may be retried, one declared non-repeatable may not. What it cannot check
    is whether the declaration is *true*, because truth about a tool lives in that tool's
    manifest, which is the registry's business and not the IR's.

    ``psh.workflow`` has the registry. It refuses a repeat-safe declaration that the
    manifest does not attest (``EFFECT106``), so the conjunction of the two compilers
    catches an author who wrote ``idempotent`` on a tool that is not.
    """
    kernel, registry, envelope, policy = bench
    lying = CapabilityRegistry()
    lying.register(_Tool("pubmed", idempotent=False))

    compiled = compile_program(program(), envelope, registry=lying, policy=policy)
    assert compiled.ok, "the declaration is internally consistent, so this compiler passes"
    assert compiled.program.node("fetch").side_effect is SideEffectClass.IDEMPOTENT

    with pytest.raises(PlanRejected, match="EFFECT106"):
        ScientificCompiler(lying).compile(cross(compiled), envelope, policy=policy)


def test_a_scope_mismatch_is_refused_on_this_side_first(bench):
    """The mirror image: the far side's ``EVIDENCE104`` never gets the chance to fire.

    A claim about children resting on a trial in adults is ``TYP103`` here — an error, not
    an extrapolation — so no plan is produced and nothing crosses. Both compilers refuse
    it; this one refuses it earlier, with the populations named.
    """
    kernel, registry, envelope, policy = bench
    compiled = compile_program(program(claim_population="children"), envelope,
                               registry=registry, policy=policy)
    assert not compiled.ok
    assert [d.code for d in compiled.errors] == ["TYP103"]
    assert "children" in compiled.errors[0].message


def test_a_refusal_here_never_reaches_the_other_compiler(bench):
    """An unsupported claim has no plan, so there is nothing to carry."""
    kernel, registry, envelope, policy = bench
    compiled = compile_program(
        program(design=StudyDesign.ANIMAL, outcome=OUTCOME), envelope,
        registry=registry, policy=policy)
    assert not compiled.ok
    with pytest.raises(BridgeRefused, match="did not compile"):
        cross(compiled)


@pytest.mark.parametrize("design,claim_kind,fragment", [
    (StudyDesign.COMMENTARY, ClaimKind.TRADITIONAL_USE, "no counterpart"),
    (StudyDesign.SYSTEMATIC_REVIEW, ClaimKind.EFFICACY, "underlying study designs"),
])
def test_a_design_with_no_counterpart_stops_at_the_bridge(bench, design, claim_kind,
                                                          fragment):
    """Each pairing compiles cleanly here; only the crossing refuses it.

    A 注家 commentary licenses a traditional-use claim directly on this side and has no
    name at all on the other, where the nearest one (``expert_consensus``) licenses a
    different set of claims. A systematic review has a name on both sides, but the far
    side requires it to declare what it reviewed — a field this IR does not model, so
    there is nothing honest to put in it.
    """
    kernel, registry, envelope, policy = bench
    compiled = compile_program(program(design=design, claim_kind=claim_kind),
                               envelope, registry=registry, policy=policy)
    assert compiled.ok, [d.message for d in compiled.errors]
    with pytest.raises(BridgeRefused, match=fragment):
        cross(compiled)


def test_a_consequential_effect_stops_at_the_bridge(bench):
    """A wet-lab order carried across as ``local_compute`` would hide what it is."""
    kernel, registry, envelope, policy = bench
    compiled = compile_program(
        program(effects={Effect.LOCAL_MODEL, Effect.WETLAB_ACTION},
                claim_side_effect=SideEffectClass.NON_REPEATABLE),
        envelope, registry=registry, policy=policy)
    assert compiled.ok, [d.message for d in compiled.errors]
    with pytest.raises(BridgeRefused, match="wetlab_action"):
        cross(compiled)


def test_evidence_with_no_identifier_stops_at_the_bridge(bench):
    kernel, registry, envelope, policy = bench
    compiled = compile_program(program(identifier=""), envelope, registry=registry,
                               policy=policy)
    assert compiled.ok, [d.message for d in compiled.errors]
    with pytest.raises(BridgeRefused, match="provenance reference"):
        cross(compiled)


# ============================================================== the seam itself

def test_every_design_that_crosses_lands_on_a_name_the_far_side_knows():
    """Structural: no mapping invents a design, and none silently upgrades one."""
    from psh.compiler.bridge import CLAIM_KINDS, DESIGN_NAMES
    from psh.workflow.ir import DESIGNS, ClaimType as WorkflowClaimType

    assert set(DESIGN_NAMES.values()) <= DESIGNS, sorted(
        set(DESIGN_NAMES.values()) - DESIGNS)
    assert {c.value for c in WorkflowClaimType} >= set(CLAIM_KINDS.values())

    # The two coarsenings are the only ones, and both go to the name that admits fewer
    # claims rather than more.
    coarsened = {d: name for d, name in DESIGN_NAMES.items() if d.value != name}
    assert coarsened == {
        StudyDesign.CASE_SERIES: "case_report",
        StudyDesign.CROSS_SECTIONAL: "observational",
        StudyDesign.CASE_CONTROL: "observational",
        StudyDesign.COHORT: "observational",
        StudyDesign.NON_RANDOMISED_TRIAL: "observational",
        StudyDesign.RANDOMISED_TRIAL: "randomized_trial",   # spelling only
    }
