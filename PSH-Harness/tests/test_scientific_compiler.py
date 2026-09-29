"""The scientific compiler: what each pass refuses, and why it is refused before running.

The property the whole package rests on is asserted twice here, first and last:

    the compiler REFUSES. It never permits.

Everything it produces is a ``Plan`` that ``PlanValidator`` validates and the kernel
executes. If every pass were deleted, nothing would become permitted that is forbidden
today — and ``test_the_compiler_holds_no_execution_path`` parses the package to check that
the claim is structural rather than behavioural, the same way
``test_the_loop_module_holds_no_route_to_the_outside_world`` does for the loop.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from psh.compiler import (
    REGISTRY, CompileOptions, CompileRejected, Diagnostics, PASS_ORDER, Severity,
    check_effects, check_flow, check_method, check_reproducibility, check_resources,
    check_statistics, check_structure, check_types, compile_or_raise, compile_program,
    lower, multipliers,
)
from psh.contracts import Autonomy, Budget, RiskTier
from psh.evidence.support import Certainty
from psh.labels import Destination, Sensitivity
from psh.policy import PolicySnapshot
from psh.runtime import PlanValidator, TaskKind
from psh.sir import (
    Acceptance, AnalysisSpec, ClaimKind, ClaimSpec, ClaimType, DataType, Effect,
    EvidenceType, ExecKind, ExperimentSpec, FanOut, FlowLabel, HypothesisSpec, Port,
    PredictionSpec, Provenance, ReproSpec, Role, SIRNode, SIRProgram, ScientificType,
    SideEffectClass, StudyDesign, Subject,
)

WIDE = (Destination.LOCAL_COMPUTE, Destination.LOCAL_MODEL, Destination.USER_OUTPUT,
        Destination.PERSISTENT, Destination.PUBLIC_REMOTE, Destination.TRUSTED_REMOTE)
LOCAL = (Destination.LOCAL_COMPUTE, Destination.LOCAL_MODEL, Destination.USER_OUTPUT,
         Destination.PERSISTENT)
SCHEMA = {"type": "object", "required": ["finding"]}


def policy(**kw):
    params = dict(profile_id="compiler_bench", max_data_label=Sensitivity.PHI,
                  allowed_destinations=WIDE, autonomy=Autonomy.ACT,
                  risk_ceiling=RiskTier.R3_CLINICAL, require_claim_support=False)
    params.update(kw)
    return PolicySnapshot(**params)


def envelope(**kw):
    return policy(**kw).envelope()


def retrieval(node_id="fetch", *, design=StudyDesign.RANDOMISED_TRIAL,
              subject=Subject.HUMAN, provenance=Provenance.RETRIEVED_VERIFIED, **kw):
    params = dict(
        node_id=node_id, role=Role.RETRIEVAL, kind=ExecKind.TOOL,
        objective="retrieve the source", component_id="pubmed",
        effects={Effect.NETWORK_PUBLIC},
        produces=ScientificType(evidence=EvidenceType(
            design=design, subject=subject, provenance=provenance)),
        repro=ReproSpec(dataset_version="2026-09", tool_version="1.0"))
    params.update(kw)
    return SIRNode(**params)


def claim(node_id="claim", *, kind=ClaimKind.EFFICACY, subject=Subject.HUMAN,
          certainty=Certainty.STRONG, depends_on=("fetch",), **kw):
    params = dict(
        node_id=node_id, role=Role.CLAIM, kind_=None)
    params.pop("kind_")
    node = dict(
        node_id=node_id, role=Role.CLAIM, kind=ExecKind.MODEL,
        objective="state the finding", depends_on=depends_on,
        effects={Effect.LOCAL_MODEL}, output_schema=SCHEMA,
        produces=ScientificType(claim=ClaimType(kind=kind, subject=subject,
                                                certainty=certainty)),
        claim=ClaimSpec(statement="the drug improves survival"))
    node.update(kw)
    return SIRNode(**node)


def program(*nodes, question="does the drug work?", acceptance=("a claim is supported",),
            **kw):
    return SIRProgram(
        question=question, nodes=tuple(nodes),
        acceptance=tuple(Acceptance(description=a, kind="claim") for a in acceptance),
        **kw)


def codes(diagnostics) -> set[str]:
    return {d.code for d in diagnostics}


# ============================================================ the central property

def test_the_compiler_holds_no_execution_path():
    """Parse the package: no transport, no process spawner, no provider, anywhere.

    A behavioural test sees the paths a test took; this sees the paths that *exist*. It is
    the distinction that let ``IsolatedRunner`` ship in v0.4 while ``call_tool`` ran
    everything in process, and it is the reason the compiler can be described as a refusal
    layer rather than trusted to be one.
    """
    forbidden = {"subprocess", "socket", "urllib", "requests", "httpx", "asyncio",
                 "multiprocessing", "http"}
    root = Path(__file__).resolve().parents[1] / "src" / "psh" / "compiler"
    offenders: list[str] = []
    for path in sorted(root.glob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom):
                names = {(node.module or "").split(".")[0]}
            else:
                continue
            hit = names & forbidden
            if hit:
                offenders.append(f"{path.name}: {sorted(hit)}")
    assert not offenders, (
        "the compiler must hold no route to the outside world; found " + "; ".join(offenders))


def test_lowering_produces_a_plan_the_existing_validator_accepts():
    """The integration property: the compiler's output is the runtime's ordinary input."""
    prog = program(retrieval(), claim())
    env = envelope()
    result = compile_program(prog, env, policy=policy())
    assert result.ok, result.diagnostics.render()

    validated = PlanValidator().validate(result.plan, env, policy=policy())
    assert set(validated.order) == {"fetch", "claim"}
    assert [t.kind for t in result.plan.tasks] == [TaskKind.TOOL, TaskKind.MODEL]


# ================================================================ the registry

def test_every_emitted_code_is_registered_and_every_registered_code_is_emitted():
    """The registry and the source agree in both directions.

    One direction stops a rule being enforced that nobody documented; the other stops the
    registry accumulating codes no pass emits, which read as coverage and are not.
    """
    root = Path(__file__).resolve().parents[1] / "src" / "psh" / "compiler"
    emitted: set[str] = set()
    for path in root.glob("*.py"):
        if path.name == "diagnostics.py":
            continue
        emitted |= set(re.findall(r'emit\(\s*"([A-Z]+\d+)"', path.read_text()))

    assert emitted <= set(REGISTRY), (
        f"emitted but unregistered: {sorted(emitted - set(REGISTRY))}")
    assert set(REGISTRY) <= emitted, (
        f"registered but never emitted: {sorted(set(REGISTRY) - emitted)}")


def test_every_registered_code_states_a_remedy():
    missing = sorted(code for code, spec in REGISTRY.items() if not spec.remedy)
    assert not missing, f"codes with no remedy: {missing}"


def test_a_pass_writes_into_the_buffer_it_was_given():
    """The defect that made the whole pipeline silently report nothing.

    ``Diagnostics`` defines ``__len__``, so an *empty* buffer is falsy, and every pass took
    its output as ``diagnostics or Diagnostics(...)``. The passes wrote into an object the
    pipeline never read, and a program whose claim asserted human efficacy from a mouse
    study compiled with no diagnostics at all.
    """
    buffer = Diagnostics(pass_name="probe")
    assert bool(buffer) is True and len(buffer) == 0
    prog = program(retrieval(design=StudyDesign.ANIMAL, subject=Subject.ANIMAL), claim())
    check_types(prog, diagnostics=buffer)
    assert "TYP102" in codes(buffer), "the pass built its own buffer and discarded this one"


def test_the_pass_order_runs_structure_first():
    assert PASS_ORDER[0] == "structure"
    assert set(PASS_ORDER) == {"structure", "method", "typecheck", "effects", "infoflow",
                               "statistics", "reproducibility", "resources"}


# ================================================================== structure

def test_a_cycle_stops_the_pipeline_before_any_other_pass_runs():
    a = SIRNode(node_id="a", kind=ExecKind.TOOL, objective="a", component_id="x",
                depends_on=("b",), effects={Effect.READ_LOCAL})
    b = SIRNode(node_id="b", kind=ExecKind.TOOL, objective="b", component_id="x",
                depends_on=("a",), effects={Effect.READ_LOCAL})
    result = compile_program(program(a, b), envelope(), policy=policy())
    assert "SIR002" in codes(result.diagnostics)
    assert result.plan is None
    # Nothing downstream ran: a type family finding against a node inside a cycle is noise.
    assert not any(d.code.startswith(("TYP", "STAT", "IFC")) for d in result.diagnostics)


@pytest.mark.parametrize("node,expected", [
    (SIRNode(node_id="n", kind=ExecKind.TOOL, objective="o", effects={Effect.READ_LOCAL}),
     "SIR004"),
    (SIRNode(node_id="n", kind=ExecKind.MODEL, objective="o"), "SIR005"),
    (SIRNode(node_id="n", kind=ExecKind.MODEL, objective="o",
             effects={Effect.LOCAL_MODEL, Effect.REMOTE_MODEL_PUBLIC}), "SIR005"),
    (SIRNode(node_id="n", kind=ExecKind.DELEGATE, objective="o"), "SIR006"),
    (SIRNode(node_id="n", kind=ExecKind.TOOL, objective="o", component_id="x"), "SIR007"),
    (SIRNode(node_id="n", role=Role.HYPOTHESIS, effects={Effect.READ_LOCAL},
             hypothesis=HypothesisSpec(proposition="p")), "SIR008"),
    (SIRNode(node_id="n", kind=ExecKind.TOOL, component_id="x",
             effects={Effect.READ_LOCAL}), "SIR003"),
    (SIRNode(node_id="n", role=Role.ANALYSIS, kind=ExecKind.TOOL, objective="o",
             component_id="x", effects={Effect.READ_LOCAL}), "SIR013"),
    (SIRNode(node_id="n", kind=ExecKind.TOOL, objective="o", component_id="x",
             effects={Effect.WETLAB_ACTION}, side_effect=SideEffectClass.PURE), "SIR014"),
    (SIRNode(node_id="n", kind=ExecKind.TOOL, objective="o", component_id="x",
             effects={Effect.READ_LOCAL}, evidence_required=True), "SIR017"),
])
def test_structure_refuses_an_incoherent_node(node, expected):
    found = codes(check_structure(program(node)))
    assert expected in found, f"expected {expected}, got {sorted(found)}"


def test_a_payload_that_reads_like_a_reference_is_refused_at_the_program():
    """The ``$fetch.gene`` defect, moved one layer earlier than ``PlanValidator``."""
    node = SIRNode(node_id="n", kind=ExecKind.TOOL, objective="o", component_id="x",
                   effects={Effect.READ_LOCAL}, payload={"symbol": "$fetch.gene"})
    found = check_structure(program(retrieval(), node))
    assert "SIR012" in codes(found)
    assert "$fetch.gene" in found.by_code("SIR012")[0].message


def test_a_port_may_not_read_a_declaration():
    hypothesis = SIRNode(node_id="h", role=Role.HYPOTHESIS,
                         hypothesis=HypothesisSpec(proposition="p"))
    reader = SIRNode(node_id="n", kind=ExecKind.TOOL, objective="o", component_id="x",
                     effects={Effect.READ_LOCAL},
                     inputs=(Port(argument="a", source="h"),))
    assert "SIR009" in codes(check_structure(program(hypothesis, reader)))


def test_an_unknown_dependency_and_an_empty_program_are_both_reported():
    node = SIRNode(node_id="n", kind=ExecKind.TOOL, objective="o", component_id="x",
                   effects={Effect.READ_LOCAL}, depends_on=("nowhere",))
    found = codes(check_structure(program(node)))
    assert "SIR001" in found
    only_declarations = program(SIRNode(node_id="h", role=Role.HYPOTHESIS,
                                        hypothesis=HypothesisSpec(proposition="p")))
    assert "SIR010" in codes(check_structure(only_declarations))
    no_acceptance = SIRProgram(question="q", nodes=(retrieval(),))
    assert "SIR011" in codes(check_structure(no_acceptance))


def test_an_unknown_effect_is_refused_at_construction():
    with pytest.raises(ValueError, match="unknown effect"):
        SIRNode(node_id="n", kind=ExecKind.TOOL, objective="o", component_id="x",
                effects={"do_whatever"})


# ==================================================================== method

def test_a_prediction_with_no_falsifier_is_an_error():
    h = SIRNode(node_id="h", role=Role.HYPOTHESIS,
                hypothesis=HypothesisSpec(proposition="p", alternative_to=("h2",)))
    p = SIRNode(node_id="p", role=Role.PREDICTION, depends_on=("h",),
                prediction=PredictionSpec(statement="s", hypothesis="h", falsifier=""))
    found = check_method(program(h, p, retrieval()))
    assert "SCI702" in codes(found)
    assert found.by_code("SCI702")[0].severity is Severity.ERROR


def test_an_experiment_that_discriminates_nothing_is_reported():
    h = SIRNode(node_id="h", role=Role.HYPOTHESIS, hypothesis=HypothesisSpec(proposition="p"))
    e = SIRNode(node_id="e", role=Role.EXPERIMENT, kind=ExecKind.TOOL, objective="assay",
                component_id="x", effects={Effect.READ_LOCAL},
                experiment=ExperimentSpec(tests=("h",), discriminates=("h",)))
    found = codes(check_method(program(h, e)))
    assert "SCI705" in found


def test_an_experiment_testing_an_unknown_hypothesis_is_an_error():
    e = SIRNode(node_id="e", role=Role.EXPERIMENT, kind=ExecKind.TOOL, objective="assay",
                component_id="x", effects={Effect.READ_LOCAL},
                experiment=ExperimentSpec(tests=("ghost",), discriminates=("a", "b")))
    assert "SCI704" in codes(check_method(program(e)))


def test_the_remaining_method_rules_fire():
    lonely = SIRNode(node_id="h", role=Role.HYPOTHESIS,
                     hypothesis=HypothesisSpec(proposition="p"))
    found = codes(check_method(program(lonely, retrieval())))
    assert {"SCI701", "SCI703"} <= found          # no prediction, no alternative

    untested = SIRNode(node_id="p", role=Role.PREDICTION,
                       prediction=PredictionSpec(statement="s", falsifier="f"))
    assert "SCI708" in codes(check_method(program(untested, retrieval())))

    answering = claim(claim=ClaimSpec(statement="s", answers="no_such_question"))
    assert "SCI706" in codes(check_method(program(retrieval(), answering)))

    assert "SCI707" in codes(check_method(program(retrieval(), claim())))


# ================================================================== typecheck

def test_a_mouse_study_cannot_license_a_human_efficacy_claim():
    """The EvidenceScopeError, which is the reason the type system exists."""
    prog = program(retrieval(design=StudyDesign.ANIMAL, subject=Subject.ANIMAL), claim())
    found = check_types(prog)
    assert "TYP102" in codes(found)
    message = found.by_code("TYP102")[0].message
    assert "animal" in message and "efficacy" in message


def test_a_classical_passage_licenses_an_attribution_and_not_an_efficacy_claim():
    """The reason the matrix is not an ordered tier.

    A classical text is the *only* source that can license an attribution, and a
    randomised trial cannot license one at all. A single "evidence strength" ordering gets
    both of those backwards.
    """
    passage = retrieval(design=StudyDesign.CLASSICAL_TEXT, subject=Subject.TEXT)
    attribution = claim(kind=ClaimKind.ATTRIBUTION, subject=Subject.TEXT,
                        certainty=Certainty.STRONG)
    assert "TYP102" not in codes(check_types(program(passage, attribution)))

    efficacy = claim(kind=ClaimKind.EFFICACY, subject=Subject.HUMAN)
    assert "TYP102" in codes(check_types(program(passage, efficacy)))

    trial = retrieval(design=StudyDesign.RANDOMISED_TRIAL, subject=Subject.HUMAN)
    assert "TYP102" in codes(check_types(program(trial, attribution)))


def test_evidence_reaches_a_claim_through_intermediate_steps():
    """Two hops of analysis do not launder a mouse experiment into a human trial."""
    analysis = SIRNode(node_id="mid", role=Role.ANALYSIS, kind=ExecKind.TOOL,
                       objective="summarise", component_id="stats",
                       depends_on=("fetch",), effects={Effect.READ_LOCAL},
                       analysis=AnalysisSpec(family="descriptive"))
    prog = program(retrieval(design=StudyDesign.ANIMAL, subject=Subject.ANIMAL), analysis,
                   claim(depends_on=("mid",)))
    assert "TYP102" in codes(check_types(prog))


def test_an_undeclared_extrapolation_is_an_error_and_a_declared_one_is_a_warning():
    surrogate = retrieval(design=StudyDesign.RANDOMISED_TRIAL, subject=Subject.HUMAN)
    surrogate = SIRNode(**{**surrogate.as_dict(), "produces": None}) if False else surrogate
    surrogate_node = retrieval(
        produces=ScientificType(evidence=EvidenceType(
            design=StudyDesign.RANDOMISED_TRIAL, subject=Subject.HUMAN,
            outcome="bone density", outcome_is_surrogate=True,
            provenance=Provenance.RETRIEVED_VERIFIED)))
    target = claim(produces=ScientificType(claim=ClaimType(
        kind=ClaimKind.EFFICACY, subject=Subject.HUMAN, outcome="bone density",
        certainty=Certainty.STRONG)))
    found = check_types(program(surrogate_node, target))
    assert found.by_code("TYP103")[0].severity is Severity.ERROR

    declared = claim(produces=target.produces,
                     claim=ClaimSpec(statement="s", extrapolation_declared=True))
    found = check_types(program(surrogate_node, declared))
    assert found.by_code("TYP103")[0].severity is Severity.WARNING


def test_a_model_generated_source_is_not_evidence():
    prog = program(retrieval(provenance=Provenance.GENERATED), claim())
    found = codes(check_types(prog))
    assert "TYP104" in found


def test_a_retracted_source_and_a_claim_with_no_evidence_are_refused():
    retracted = retrieval(produces=ScientificType(evidence=EvidenceType(
        design=StudyDesign.RANDOMISED_TRIAL, subject=Subject.HUMAN,
        provenance=Provenance.RETRIEVED_VERIFIED, identifier="1", retracted=True)))
    assert "TYP107" in codes(check_types(program(retracted, claim())))

    alone = claim(depends_on=())
    assert "TYP106" in codes(check_types(program(alone)))

    unstated = retrieval(produces=ScientificType())
    assert "TYP105" in codes(check_types(program(unstated)))


def test_a_port_type_that_contradicts_its_source_is_reported():
    table = SIRNode(node_id="t", kind=ExecKind.TOOL, objective="make a table",
                    component_id="x", effects={Effect.READ_LOCAL},
                    produces=ScientificType(data=DataType(kind="table", rows=10)))
    reader = SIRNode(node_id="r", kind=ExecKind.TOOL, objective="read it",
                     component_id="y", effects={Effect.READ_LOCAL},
                     inputs=(Port(argument="a", source="t", expected_type="string"),))
    assert "TYP108" in codes(check_types(program(table, reader)))


# ==================================================================== effects

def test_an_effect_needing_a_destination_this_run_forbids_is_refused_before_it_runs():
    prog = program(retrieval(), claim())
    found = check_effects(prog, envelope(allowed_destinations=LOCAL))
    assert "EFF201" in codes(found)
    assert "PUBLIC_REMOTE" in found.by_code("EFF201")[0].message


def test_autonomy_risk_and_approval_are_all_checked():
    mutating = SIRNode(node_id="w", kind=ExecKind.TOOL, objective="write", component_id="x",
                       effects={Effect.WRITE_LOCAL},
                       side_effect=SideEffectClass.IDEMPOTENT)
    found = codes(check_effects(program(mutating), envelope(autonomy=Autonomy.SUGGEST)))
    assert "EFF202" in found

    clinical = SIRNode(node_id="c", kind=ExecKind.TOOL, objective="order", component_id="x",
                       effects={Effect.CLINICAL_ACTION},
                       side_effect=SideEffectClass.NON_REPEATABLE)
    found = codes(check_effects(program(clinical),
                                envelope(risk_ceiling=RiskTier.R1_ROUTINE)))
    assert "EFF203" in found

    found = codes(check_effects(program(clinical), envelope(), approval_available=False))
    assert "EFF204" in found


def test_a_consequential_effect_with_no_completion_condition_is_reported():
    order = SIRNode(node_id="o", kind=ExecKind.TOOL, objective="order the assay",
                    component_id="lims", effects={Effect.WETLAB_ACTION},
                    side_effect=SideEffectClass.NON_REPEATABLE)
    prog = SIRProgram(question="q", nodes=(order,),
                      acceptance=(Acceptance(description="a claim", kind="claim"),))
    assert "EFF205" in codes(check_effects(prog, envelope()))


# ============================================================== information flow

def test_phi_may_not_reach_a_public_model_and_the_program_is_refused_at_compile():
    """The combination the effect ceiling exists for, refused before step one."""
    chart = SIRNode(node_id="chart", role=Role.RETRIEVAL, kind=ExecKind.TOOL,
                    objective="read the chart", component_id="ehr",
                    effects={Effect.PHI_READ},
                    declared_label=FlowLabel(sensitivity=Sensitivity.PHI),
                    produces=ScientificType(evidence=EvidenceType(
                        design=StudyDesign.COHORT, subject=Subject.HUMAN,
                        provenance=Provenance.RETRIEVED_VERIFIED)),
                    repro=ReproSpec(dataset_version="1", tool_version="1"))
    summarise = SIRNode(node_id="sum", kind=ExecKind.MODEL, objective="summarise",
                        depends_on=("chart",), effects={Effect.REMOTE_MODEL_PUBLIC})
    _, flow = check_flow(program(chart, summarise), envelope())
    result = compile_program(program(chart, summarise), envelope(), policy=policy())
    assert "IFC301" in codes(result.diagnostics)
    assert result.plan is None
    assert flow.labels["sum"].sensitivity is Sensitivity.PHI


def test_a_derived_value_may_not_declare_itself_less_sensitive_than_its_inputs():
    chart = SIRNode(node_id="chart", kind=ExecKind.TOOL, objective="read", component_id="ehr",
                    effects={Effect.PHI_READ},
                    declared_label=FlowLabel(sensitivity=Sensitivity.PHI))
    count = SIRNode(node_id="count", kind=ExecKind.TOOL, objective="count", component_id="x",
                    depends_on=("chart",), effects={Effect.READ_LOCAL},
                    declared_label=FlowLabel(sensitivity=Sensitivity.PUBLIC))
    found, _ = check_flow(program(chart, count), envelope())
    assert "IFC304" in codes(found)


def test_declassification_needs_a_method_and_a_permitted_principal():
    chart = SIRNode(node_id="chart", kind=ExecKind.TOOL, objective="read", component_id="ehr",
                    effects={Effect.PHI_READ},
                    declared_label=FlowLabel(sensitivity=Sensitivity.PHI))
    silent = SIRNode(node_id="d", kind=ExecKind.TOOL, objective="deidentify",
                     component_id="deid", depends_on=("chart",),
                     effects={Effect.READ_LOCAL},
                     declassify_to=Sensitivity.RESEARCH_DEIDENTIFIED)
    found, _ = check_flow(program(chart, silent), envelope())
    assert "IFC302" in codes(found)

    named = SIRNode(node_id="d", kind=ExecKind.TOOL, objective="deidentify",
                    component_id="deid", depends_on=("chart",), effects={Effect.READ_LOCAL},
                    declassify_to=Sensitivity.RESEARCH_DEIDENTIFIED,
                    declassify_method="safe-harbour removal of 18 identifiers")
    found, _ = check_flow(program(chart, named), envelope())
    assert "IFC303" in codes(found), "no declassifier is named in the policy"

    permitted = policy(declassifiers=("deid_service",))
    found, flow = check_flow(program(chart, named), permitted.envelope(), policy=permitted)
    assert "IFC303" not in codes(found)
    assert flow.labels["d"].sensitivity is Sensitivity.RESEARCH_DEIDENTIFIED


def test_a_licence_term_travels_with_a_derived_value():
    """A research-only dataset taints its summary, and the summary may not be published."""
    dataset = SIRNode(node_id="ds", kind=ExecKind.TOOL, objective="load", component_id="x",
                      effects={Effect.READ_LOCAL},
                      declared_label=FlowLabel(license_terms=frozenset({"research_only"})))
    publish = SIRNode(node_id="pub", kind=ExecKind.TOOL, objective="publish",
                      component_id="y", depends_on=("ds",),
                      effects={Effect.EXTERNAL_PUBLISH},
                      side_effect=SideEffectClass.AT_MOST_ONCE)
    found, _ = check_flow(program(dataset, publish), envelope())
    assert "IFC305" in codes(found)


def test_a_value_above_the_run_ceiling_and_a_weakening_provenance_are_reported():
    chart = SIRNode(node_id="chart", kind=ExecKind.TOOL, objective="read", component_id="ehr",
                    effects={Effect.PHI_READ},
                    declared_label=FlowLabel(sensitivity=Sensitivity.PHI))
    found, _ = check_flow(program(chart), envelope(max_data_label=Sensitivity.INTERNAL))
    assert "IFC306" in codes(found)

    supplied = retrieval(provenance=Provenance.SUPPLIED,
                         declared_label=FlowLabel(provenance=Provenance.SUPPLIED))
    downstream = claim(produces=ScientificType(evidence=EvidenceType(
        design=StudyDesign.COHORT, subject=Subject.HUMAN,
        provenance=Provenance.SUPPLIED)))
    found, _ = check_flow(program(supplied, downstream), envelope())
    assert "IFC307" in codes(found)


# ================================================================== statistics

def analysis_node(node_id="a", **spec_kw):
    spec = dict(family="screen", tests=20000, multiplicity="bh", null_model="permutation",
                missingness="complete_case", reports_interval=True, replication="internal")
    spec.update(spec_kw)
    return SIRNode(node_id=node_id, role=Role.ANALYSIS, kind=ExecKind.TOOL,
                   objective="analyse", component_id="stats", depends_on=("fetch",),
                   effects={Effect.READ_LOCAL}, analysis=AnalysisSpec(**spec))


def test_twenty_thousand_tests_with_no_correction_is_an_error():
    found = check_statistics(program(retrieval(), analysis_node(multiplicity="none")))
    assert "STAT401" in codes(found)
    assert "1000.0 false positives" in found.by_code("STAT401")[0].message


def test_a_discovery_analysis_with_no_null_model_is_an_error():
    """The Kosmos lesson: a significant result is not a finding until random beats it."""
    found = check_statistics(program(retrieval(), analysis_node(null_model="none")))
    assert "STAT409" in codes(found)


def test_feature_selection_on_the_evaluation_data_is_an_error():
    found = check_statistics(program(retrieval(), analysis_node(
        family="classification", tests=1, feature_selection="all_data",
        data_split="train_test")))
    assert "STAT406" in codes(found)


def test_survival_prediction_and_subgroup_rules():
    found = codes(check_statistics(program(retrieval(), analysis_node(
        family="survival", tests=1, method="cox proportional hazards", censoring=""))))
    assert {"STAT404", "STAT405"} <= found

    found = codes(check_statistics(program(retrieval(), analysis_node(
        family="classification", tests=1, data_split="none"))))
    assert "STAT407" in found

    found = codes(check_statistics(program(retrieval(), analysis_node(
        tests=1, subgroups=("elderly",), preregistered=False))))
    assert "STAT408" in found


def test_the_remaining_statistical_rules_fire():
    found = codes(check_statistics(program(retrieval(), analysis_node(tests=None))))
    assert "STAT402" in found

    found = codes(check_statistics(program(retrieval(), analysis_node(
        family="survival", tests=1, censoring="right", sample_size=None, power=None))))
    assert "STAT403" in found

    found = codes(check_statistics(program(retrieval(), analysis_node(
        tests=1, batch_variable="plate", batch_adjusted=False))))
    assert "STAT410" in found

    found = codes(check_statistics(program(retrieval(), analysis_node(
        tests=1, missingness="", reports_interval=False, alpha=7.0))))
    assert {"STAT411", "STAT412", "STAT414"} <= found

    with_claim = program(retrieval(), analysis_node(replication="none"),
                         claim(depends_on=("a",)))
    assert "STAT413" in codes(check_statistics(with_claim))

    small = retrieval(produces=ScientificType(data=DataType(kind="table", rows=12)))
    assert "STAT415" in codes(check_statistics(program(small, analysis_node(tests=9000))))


def test_a_profile_may_soften_the_statistical_family_and_never_the_leakage_rule():
    exploratory = CompileOptions().exploratory()
    prog = program(retrieval(), analysis_node(null_model="none",
                                              feature_selection="all_data"),
                   claim(depends_on=("a",)))
    result = compile_program(prog, envelope(), policy=policy(), options=exploratory)
    by_code = {d.code: d.severity for d in result.diagnostics}
    assert by_code["STAT409"] is Severity.WARNING
    assert by_code["STAT406"] is Severity.ERROR, "leakage invalidates the result either way"


# ============================================================== reproducibility

def test_reproducibility_rules_fire():
    stochastic = SIRNode(node_id="s", kind=ExecKind.TOOL, objective="sample",
                         component_id="x", effects={Effect.READ_LOCAL},
                         deterministic=False, repro=ReproSpec(tool_version="1"))
    found = codes(check_reproducibility(program(stochastic)))
    assert "REP501" in found

    unpinned = retrieval(repro=ReproSpec())
    found = codes(check_reproducibility(program(unpinned)))
    assert {"REP502", "REP503"} <= found


def test_preregistration_is_an_ordering_not_a_promise():
    """An analysis that claims to follow a protocol must *depend* on it."""
    protocol = SIRNode(node_id="proto", role=Role.PROTOCOL)
    floating = SIRNode(node_id="a", role=Role.ANALYSIS, kind=ExecKind.TOOL,
                       objective="analyse", component_id="stats",
                       effects={Effect.READ_LOCAL}, repro=ReproSpec(tool_version="1"),
                       analysis=AnalysisSpec(family="survival", preregistered=True,
                                             censoring="right", tests=1))
    found = check_reproducibility(program(protocol, floating))
    assert "REP506" in codes(found)

    ordered = SIRNode(**{**{k: v for k, v in
                            (("node_id", "a"), ("role", Role.ANALYSIS),
                             ("kind", ExecKind.TOOL), ("objective", "analyse"),
                             ("component_id", "stats"), ("effects", {Effect.READ_LOCAL}),
                             ("repro", ReproSpec(tool_version="1")),
                             ("analysis", AnalysisSpec(family="survival", preregistered=True,
                                                       censoring="right", tests=1)),
                             ("depends_on", ("proto",)))}})
    assert "REP506" not in codes(check_reproducibility(program(protocol, ordered)))


def test_a_policy_that_requires_preregistration_refuses_a_program_without_a_protocol():
    options = CompileOptions(require_preregistration=True)
    found = check_reproducibility(program(retrieval()), require_preregistration=True)
    assert "REP505" in codes(found)
    result = compile_program(program(retrieval(), claim()), envelope(), policy=policy(),
                             options=options)
    assert "REP505" in codes(result.diagnostics) and result.plan is None


def test_an_isolated_component_with_no_environment_is_reported():
    class Registry:
        def manifest_for(self, component_id):
            from psh.contracts import ComponentKind, ComponentManifest
            return ComponentManifest(id=component_id, name=component_id,
                                     kind=ComponentKind.TOOL, backend="subprocess",
                                     entrypoint="run.sh")

    node = retrieval(repro=ReproSpec(dataset_version="1", tool_version="1"))
    assert "REP504" in codes(check_reproducibility(program(node), registry=Registry()))


# ================================================================== resources

def test_an_unbounded_fan_out_is_refused():
    source = retrieval()
    mapped = SIRNode(node_id="per_gene", kind=ExecKind.MODEL, objective="interpret",
                     depends_on=("fetch",), effects={Effect.LOCAL_MODEL},
                     fan_out=FanOut(source="fetch"))
    found = check_resources(program(source, mapped), envelope())
    assert "RES605" in codes(found)


def test_a_bounded_fan_out_multiplies_the_budget_and_may_exceed_it():
    source = retrieval()
    mapped = SIRNode(node_id="per_gene", kind=ExecKind.MODEL, objective="interpret",
                     depends_on=("fetch",), effects={Effect.LOCAL_MODEL},
                     fan_out=FanOut(source="fetch", max_items=500),
                     estimated_tokens=1000)
    prog = program(source, mapped)
    assert multipliers(prog)["per_gene"] == 500
    found = codes(check_resources(prog, envelope()))
    assert "RES601" in found          # 500 x 1000 tokens against the default ceiling
    assert "RES606" in found          # 500 model calls against the ceiling


def test_plain_budget_ceilings_are_reported():
    heavy = retrieval(estimated_tokens=10 ** 7, estimated_usd=10 ** 4,
                      estimated_seconds=10 ** 7)
    found = codes(check_resources(program(heavy), envelope()))
    assert {"RES601", "RES602", "RES603"} <= found

    many = [SIRNode(node_id=f"t{i}", kind=ExecKind.TOOL, objective="o", component_id="x",
                    effects={Effect.READ_LOCAL}) for i in range(6)]
    budget = Budget(max_tool_calls=3)
    env = policy().envelope(budget=budget)
    assert "RES604" in codes(check_resources(program(*many), env))


# ==================================================================== lowering

def test_authority_is_derived_from_effects_rather_than_declared():
    """An author says what a step *does*; the compiler says what that requires."""
    prog = program(retrieval(), claim())
    result = compile_program(prog, envelope(), policy=policy())
    fetch = next(t for t in result.plan.tasks if t.task_id == "fetch")
    assert fetch.destinations == (Destination.PUBLIC_REMOTE,)
    assert fetch.max_risk is RiskTier.R1_ROUTINE
    # A public destination caps the label whatever the run's own ceiling is.
    assert fetch.max_label is Sensitivity.RESEARCH_DEIDENTIFIED


def test_declarations_collapse_into_the_dependencies_of_what_executes():
    h = SIRNode(node_id="h", role=Role.HYPOTHESIS,
                hypothesis=HypothesisSpec(proposition="p", alternative_to=("h2",)))
    h2 = SIRNode(node_id="h2", role=Role.HYPOTHESIS,
                 hypothesis=HypothesisSpec(proposition="q", alternative_to=("h",)))
    p = SIRNode(node_id="p", role=Role.PREDICTION, depends_on=("h",),
                prediction=PredictionSpec(statement="s", hypothesis="h", falsifier="f"))
    fetch = retrieval(depends_on=("p",), about=("p",))
    result = compile_program(program(h, h2, p, fetch, claim()), envelope(), policy=policy())
    assert result.ok, result.diagnostics.render()
    assert {t.task_id for t in result.plan.tasks} == {"fetch", "claim"}
    fetch_task = next(t for t in result.plan.tasks if t.task_id == "fetch")
    assert fetch_task.dependencies == (), "a hypothesis is not a job to wait for"


def test_the_repeat_semantics_decide_the_retry_policy():
    once = SIRNode(node_id="order", kind=ExecKind.TOOL, objective="order the assay",
                   component_id="lims", effects={Effect.WETLAB_ACTION},
                   side_effect=SideEffectClass.NON_REPEATABLE, max_attempts=5)
    retryable = SIRNode(node_id="search", kind=ExecKind.TOOL, objective="search",
                        component_id="pubmed", effects={Effect.NETWORK_PUBLIC},
                        side_effect=SideEffectClass.IDEMPOTENT, max_attempts=3)
    prog = SIRProgram(question="q", nodes=(once, retryable),
                      acceptance=(Acceptance(description="ran", kind="task"),))
    plan = lower(prog, envelope())
    by_id = {t.task_id: t for t in plan.tasks}
    assert by_id["order"].retry.max_attempts == 1, "a wet-lab order is not retried"
    assert by_id["search"].retry.max_attempts == 3


def test_a_claim_node_carries_an_evidence_requirement_into_the_plan():
    result = compile_program(program(retrieval(), claim()), envelope(), policy=policy())
    task = next(t for t in result.plan.tasks if t.task_id == "claim")
    assert task.evidence_required is True
    assert task.output_schema == SCHEMA


# ================================================================== the entrance

def test_compile_or_raise_names_every_blocking_diagnostic():
    prog = program(retrieval(design=StudyDesign.ANIMAL, subject=Subject.ANIMAL), claim())
    with pytest.raises(CompileRejected) as excinfo:
        compile_or_raise(prog, envelope(), policy=policy())
    assert "TYP102" in str(excinfo.value)
    assert excinfo.value.result.plan is None


def test_warnings_pass_and_become_limitations_of_the_release():
    lonely = SIRNode(node_id="h", role=Role.HYPOTHESIS,
                     hypothesis=HypothesisSpec(proposition="p"))
    p = SIRNode(node_id="p", role=Role.PREDICTION, depends_on=("h",),
                prediction=PredictionSpec(statement="s", hypothesis="h", falsifier="f"))
    result = compile_program(program(lonely, p, retrieval(about=("p",)), claim()),
                             envelope(), policy=policy())
    assert result.ok, result.diagnostics.render()
    assert result.warnings, "SCI703 should have been raised: no alternative hypothesis"
    assert any("SCI703" in text for text in result.limitations())


def test_a_signature_covers_a_node_and_everything_upstream_of_it():
    first = program(retrieval(), claim())
    changed = program(retrieval(design=StudyDesign.COHORT, subject=Subject.HUMAN), claim())
    assert first.signature("fetch") != changed.signature("fetch")
    assert first.signature("claim") != changed.signature("claim"), (
        "a change upstream must change the signature downstream, or reuse is unsound")
