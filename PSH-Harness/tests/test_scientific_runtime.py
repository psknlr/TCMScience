"""End to end: a scientific program compiled, run, released — and amended.

Everything here goes through ``ScientificRunService``, which is the one door for a
program. Its job is to add three things in front of the existing path — compilation,
ingestion into the world model, and journalling — and to add **no** way to execute
anything. The tests that matter most are the ones asserting the second half:

* a program that does not compile runs nothing at all;
* a program that *does* compile is still judged by the release gate, so compiling is not a
  way to release an unsupported claim;
* an observer that throws does not end the run, because a watcher is not a participant.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from psh.capabilities import CapabilityRegistry
from psh.compiler import CompileOptions
from psh.config import PSHConfig
from psh.contracts import (
    Autonomy, ComponentKind, ComponentManifest, ModelProfile, RiskTier,
)
from psh.durable import WorkflowJournal
from psh.evidence.support import Certainty
from psh.kernel import TrustedKernel
from psh.labels import Destination, Sensitivity
from psh.policy import PolicySnapshot
from psh.runtime import ScientificRunService, TaskEvent
from psh.scientist import HypothesisStatus, ScientificLedger, ScientificWorldModel
from psh.sir import (
    Acceptance, ClaimKind, ClaimSpec, ClaimType, Effect, EvidenceType, ExecKind,
    HypothesisSpec, Port, PredictionSpec, Provenance, ReproSpec, Role, SIRNode, SIRProgram,
    ScientificType, StudyDesign, Subject,
)

WIDE = (Destination.LOCAL_COMPUTE, Destination.LOCAL_MODEL, Destination.USER_OUTPUT,
        Destination.PERSISTENT, Destination.PUBLIC_REMOTE)
SCHEMA = {"type": "object", "required": ["finding", "evidence"]}
ABSTRACT = "In mice, a Piezo1 agonist increased HSV-1 entry 2.4-fold."
ANSWER = ('{"finding": "In mice a Piezo1 agonist increased HSV-1 entry (PMID: 40011222)",'
          ' "evidence": ["40011222"]}')


class Tool:
    def __init__(self, component_id, result, **manifest_kw):
        params = dict(id=component_id, name=component_id, kind=ComponentKind.TOOL,
                      max_label=Sensitivity.PHI,
                      destinations=(Destination.PUBLIC_REMOTE,), requires_network=True)
        params.update(manifest_kw)
        self.manifest = ComponentManifest(**params)
        self.calls: list = []
        self._result = result

    def invoke(self, payload, envelope):
        self.calls.append(payload)
        return self._result


def policy(**kw):
    params = dict(profile_id="sci_runtime", max_data_label=Sensitivity.PHI,
                  allowed_destinations=WIDE, autonomy=Autonomy.ACT,
                  risk_ceiling=RiskTier.R3_CLINICAL, require_claim_support=False)
    params.update(kw)
    return PolicySnapshot(**params)


@pytest.fixture
def bench(tmp_path):
    kernel = TrustedKernel(PSHConfig(state_dir=tmp_path / "k").ensure_dirs(),
                           policy=policy())
    registry = CapabilityRegistry()
    tool = Tool("pubmed", {"abstract": ABSTRACT, "pmid": "40011222"})
    registry.register(tool)
    model = ModelProfile(id="local-8b", provider="local",
                         destination=Destination.LOCAL_MODEL, max_label=Sensitivity.PHI,
                         usd_per_1k_input=0.0, usd_per_1k_output=0.0)
    journal = WorkflowJournal(tmp_path / "journal.db")
    project = kernel.graph.project("Piezo1 and HSV-1")
    world = ScientificWorldModel(ScientificLedger(kernel, project.id))
    service = ScientificRunService(
        kernel, registry=registry, model=model, model_invoke=lambda prompt: ANSWER,
        journal=journal, world_model=world)
    yield service, kernel, tool, journal, world, project.id
    kernel.close()
    journal.close()


def program(*, objective="state what the evidence supports", claim_kind=ClaimKind.MECHANISM,
            subject=Subject.ANIMAL, project_id="", program_id="prog_piezo1"):
    return SIRProgram(
        question="Does Piezo1 activation promote HSV-1 entry?", project_id=project_id,
        program_id=program_id,
        acceptance=(Acceptance(description="a claim is supported by evidence",
                               kind="claim"),),
        nodes=(
            SIRNode(node_id="h1", role=Role.HYPOTHESIS, hypothesis=HypothesisSpec(
                proposition="Piezo1 activation promotes HSV-1 entry",
                mechanism="mechanotransduction", alternative_to=("h2",))),
            SIRNode(node_id="h2", role=Role.HYPOTHESIS, hypothesis=HypothesisSpec(
                proposition="the effect is mediated by interferon signalling",
                alternative_to=("h1",))),
            SIRNode(node_id="p1", role=Role.PREDICTION, depends_on=("h1",),
                    prediction=PredictionSpec(statement="agonism increases early entry",
                                              hypothesis="h1",
                                              falsifier="entry unchanged under agonism")),
            SIRNode(node_id="p2", role=Role.PREDICTION, depends_on=("h2",),
                    prediction=PredictionSpec(statement="the IFN response changes first",
                                              hypothesis="h2",
                                              falsifier="the IFN response is unchanged")),
            SIRNode(node_id="fetch", role=Role.RETRIEVAL, kind=ExecKind.TOOL,
                    objective="retrieve the mouse study", component_id="pubmed",
                    effects={Effect.NETWORK_PUBLIC}, about=("p1", "p2"),
                    produces=ScientificType(evidence=EvidenceType(
                        design=StudyDesign.ANIMAL, subject=Subject.ANIMAL,
                        intervention="piezo1 agonist", outcome="viral entry",
                        provenance=Provenance.RETRIEVED_VERIFIED, identifier="40011222")),
                    repro=ReproSpec(dataset_version="2026-09", tool_version="1.2"),
                    estimated_tokens=100, estimated_seconds=2),
            SIRNode(node_id="claim", role=Role.CLAIM, kind=ExecKind.MODEL,
                    objective=objective, depends_on=("fetch",),
                    effects={Effect.LOCAL_MODEL}, about=("h1",),
                    inputs=(Port(argument="evidence", source="fetch",
                                 pointer="/abstract"),),
                    produces=ScientificType(claim=ClaimType(
                        kind=claim_kind, subject=subject, intervention="piezo1 agonist",
                        outcome="viral entry", certainty=Certainty.TENTATIVE)),
                    claim=ClaimSpec(statement="Piezo1 agonism increases entry in mice"),
                    output_schema=SCHEMA, acceptance_tests=({"kind": "output_schema"},),
                    estimated_tokens=300, estimated_seconds=4)))


# ==================================================================== the run

def test_a_program_compiles_runs_and_releases(bench):
    service, kernel, tool, journal, world, project = bench
    envelope = kernel.envelope(project_id=project)
    result = service.run(program(project_id=project), envelope=envelope,
                         project_id=project, sources={"40011222": ABSTRACT})

    assert result.ok, result.summary()
    assert "Piezo1" in result.released_output
    assert len(tool.calls) == 1
    assert sorted(result.world_model_nodes) == ["h1", "h2"], (
        "the hypotheses are the records; a prediction is a field of one")

    replay = journal.replay(envelope.run_id)
    assert set(replay.succeeded()) == {"fetch", "claim"}
    assert replay.has_result("fetch")

    states = [world.belief(nid, envelope) for nid in
              (result.world_model_nodes["h1"], result.world_model_nodes["h2"])]
    assert all(s.status is HypothesisStatus.PROPOSED for s in states), (
        "a programme predicts; only an observation moves a hypothesis")
    # The competition survived the crossing: each hypothesis names the other.
    assert [n.id for n in world.competing(result.world_model_nodes["h1"])] == \
        [result.world_model_nodes["h2"]]
    assert states[0].falsifiers == ("entry unchanged under agonism",)


def test_a_program_that_does_not_compile_executes_nothing(bench):
    """The mouse study cannot license a human efficacy claim, and the tool never runs."""
    service, kernel, tool, journal, world, project = bench
    envelope = kernel.envelope(project_id=project)
    result = service.run(program(claim_kind=ClaimKind.EFFICACY, subject=Subject.HUMAN,
                                 project_id=project),
                         envelope=envelope, project_id=project)

    assert result.status == "not_compiled"
    assert result.refused_at == "compile"
    assert "TYP102" in result.refusal_kind
    assert tool.calls == [], "a refused program must not have run anything"
    assert result.released_output is None
    assert world.hypotheses() == [], "a refused program's hypotheses are not knowledge"
    assert len(journal) == 0


def test_compiling_is_not_a_way_past_the_release_gate(tmp_path):
    """A compiled program whose deliverable cites nothing is still refused at the gate.

    The property that keeps the compiler a refusal layer rather than an approval one. The
    program below is scientifically well formed — every pass passes — and the model returns
    an unsupported clinical sentence. The gate rules on it exactly as it rules on a single
    pass's output.
    """
    strict = policy(require_claim_support=True, require_citation=True)
    kernel = TrustedKernel(PSHConfig(state_dir=tmp_path / "k").ensure_dirs(), policy=strict)
    registry = CapabilityRegistry()
    tool = Tool("pubmed", {"abstract": ABSTRACT, "pmid": "40011222"})
    registry.register(tool)
    model = ModelProfile(id="local-8b", provider="local",
                         destination=Destination.LOCAL_MODEL, max_label=Sensitivity.PHI,
                         usd_per_1k_input=0.0, usd_per_1k_output=0.0)
    service = ScientificRunService(
        kernel, registry=registry, model=model,
        model_invoke=lambda prompt: (
            '{"finding": "The agonist cures herpes in all patients (PMID: 40011222).",'
            ' "evidence": ["40011222"]}'))
    project = kernel.graph.project("p")
    envelope = kernel.envelope(project_id=project.id)
    result = service.run(program(project_id=project.id), envelope=envelope,
                         project_id=project.id, sources={"40011222": ABSTRACT})

    assert result.compiled.ok, "the program itself is well formed"
    assert not result.ok
    assert result.refused_at == "release_gate"
    assert result.released_output is None
    kernel.close()


def test_the_compiler_warnings_become_limitations_of_the_release(bench):
    service, kernel, tool, journal, world, project = bench
    prog = program(project_id=project)
    # Drop the alternative hypothesis: SCI703 is a warning, so the program still runs.
    nodes = tuple(n for n in prog.nodes if n.node_id not in ("h2", "p2"))
    nodes = tuple(
        n if n.node_id != "h1" else
        SIRNode(node_id="h1", role=Role.HYPOTHESIS,
                hypothesis=HypothesisSpec(proposition="Piezo1 promotes entry"))
        for n in nodes)
    envelope = kernel.envelope(project_id=project)
    result = service.run(prog.with_nodes(nodes), envelope=envelope, project_id=project,
                         sources={"40011222": ABSTRACT})
    assert result.ok, result.summary()
    assert any("SCI703" in text for text in result.limitations())


# ================================================================== the amend

def test_an_amendment_reuses_the_unchanged_retrieval(bench):
    service, kernel, tool, journal, world, project = bench
    first_envelope = kernel.envelope(project_id=project)
    original = program(project_id=project)
    first = service.run(original, envelope=first_envelope, project_id=project,
                        sources={"40011222": ABSTRACT})
    assert first.ok and len(tool.calls) == 1

    changed = program(objective="state the finding, with the effect size",
                      project_id=project)
    second = service.amend(changed, previous_run_id=first_envelope.run_id,
                           envelope=kernel.envelope(project_id=project),
                           previous=original, project_id=project,
                           sources={"40011222": ABSTRACT})

    assert second.ok, second.summary()
    assert second.reused == ("fetch",)
    assert second.delta.recomputed == ("claim",)
    assert len(tool.calls) == 1, "the retrieval was reused, so the tool ran once in total"
    assert len(world.hypotheses()) == 2, "ingestion did not duplicate the hypotheses"


def test_an_amendment_needs_a_journal(tmp_path):
    kernel = TrustedKernel(PSHConfig(state_dir=tmp_path / "k").ensure_dirs(),
                           policy=policy())
    service = ScientificRunService(kernel, registry=CapabilityRegistry())
    with pytest.raises(ValueError, match="needs a journal"):
        service.amend(program(), previous_run_id="nothing")
    kernel.close()


def test_an_unsafe_amendment_is_refused_before_anything_runs(bench):
    """An in-doubt wet-lab order is for whoever owns it to reconcile, not for a retry."""
    from psh.durable import RecordKind
    from psh.sir import SideEffectClass

    service, kernel, tool, journal, world, project = bench
    from psh.sir import ExperimentSpec

    order = SIRNode(node_id="order", role=Role.EXPERIMENT, kind=ExecKind.TOOL,
                    objective="submit the assay order", component_id="pubmed",
                    effects={Effect.WETLAB_ACTION},
                    side_effect=SideEffectClass.NON_REPEATABLE,
                    experiment=ExperimentSpec(discriminates=("h1", "h2")),
                    repro=ReproSpec(tool_version="1"))
    prog = SIRProgram(question="q", project_id=project, program_id="prog_order",
                      acceptance=(Acceptance(description="the order was placed",
                                             kind="task"),),
                      nodes=(order,))
    journal.append(RecordKind.NODE_STARTED, "earlier_run", node_id="order",
                   signature=prog.signature("order"))

    result = service.amend(prog, previous_run_id="earlier_run",
                           envelope=kernel.envelope(project_id=project),
                           project_id=project)
    assert result.status == "refused"
    assert result.refused_at == "amend"
    assert result.refusal_kind == "UnsafeReplay"
    assert tool.calls == []


# =============================================================== the observer

def test_an_observer_that_throws_does_not_end_the_run(bench):
    """A watcher is not a participant. The contract is one-directional by construction."""
    from psh.runtime import AgentLoopController, StaticPlanner

    service, kernel, tool, journal, world, project = bench
    envelope = kernel.envelope(project_id=project)
    compiled = service.compile(program(project_id=project), envelope)
    assert compiled.ok

    seen: list[str] = []

    def exploding(event: TaskEvent) -> None:
        seen.append(event.state)
        raise RuntimeError("the observer is broken")

    loop = AgentLoopController(
        kernel, planner=StaticPlanner(compiled.plan), registry=service.registry,
        model=service.model, model_invoke=service.model_invoke, sleep=lambda s: None,
        observer=exploding)
    result = loop.run(compiled.program.question, envelope)
    assert result.ok, result.summary()
    assert "started" in seen and "succeeded" in seen


def test_the_service_adds_no_execution_path():
    """Structural: the service reaches the world only through the loop and the broker."""
    forbidden = {"subprocess", "socket", "urllib", "requests", "httpx", "asyncio"}
    source = (Path(__file__).resolve().parents[1]
              / "src" / "psh" / "runtime" / "scientific.py").read_text()
    tree = ast.parse(source)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[0])
    assert not imported & forbidden, sorted(imported & forbidden)


def test_compile_alone_runs_nothing(bench):
    service, kernel, tool, journal, world, project = bench
    envelope = kernel.envelope(project_id=project)
    compiled = service.compile(program(project_id=project), envelope)
    assert compiled.ok
    assert tool.calls == []
    assert len(journal) == 0
    assert world.hypotheses() == []
