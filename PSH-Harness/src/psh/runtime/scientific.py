"""The application-facing door for a compiled scientific program.

``ResearchRunService`` is the one entrance for an *objective*: plan, loop, release. This is
the one entrance for a *program* — a typed scientific object with hypotheses, evidence
types, an analysis design and declared effects — and it adds exactly three things in front
of that same path:

1. **Compilation.** The program is refused, with codes and remedies, before anything runs.
2. **Ingestion.** A program that compiled writes its hypotheses and predictions into the
   durable world model, so the next run starts from what this one committed to.
3. **Journalling.** Every node's start and outcome is recorded, so a later amendment can
   keep what is still valid instead of paying for the whole program again.

What it does **not** add is a way to execute anything. The plan the compiler lowers goes
through ``PlanValidator``, ``AgentLoopController``, ``ExecutionBroker`` and ``Finalizer``
unchanged — the same objects, in the same order, under the same policy. A program that
compiles is not thereby released: the release gate rules on its output exactly as it rules
on a single pass's, and a compiled program whose deliverable cites nothing is refused at
the gate like any other.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

from ..compiler import (
    CompiledProgram, CompileOptions, CompileRejected, compile_program,
)
from ..contracts import RunEnvelope
from ..labels import Destination, Sensitivity
from ..sir import SIRProgram
from .finalize import Finalizer, ReleasedResult
from .loop import AgentLoopController, LoopLimits, LoopResult, LoopState, StaticPlanner, TaskEvent
from .plan_validator import PlanValidator

__all__ = ["ScientificRunService", "ScientificResult"]


@dataclass(frozen=True, slots=True)
class ScientificResult:
    """What a scientific run produced: the release, and how it was arrived at.

    ``released`` is the only field that can carry run text, and only when the gate passed —
    the property ``ReleasedResult`` already enforces. Everything else here is structure: the
    diagnostics, the reuse decisions, the world-model nodes written.
    """

    status: str                                   # released | refused | not_compiled
    run_id: str
    compiled: CompiledProgram | None = None
    released: ReleasedResult | None = None
    delta: Any = None
    reused: tuple[str, ...] = ()
    world_model_nodes: Mapping[str, str] = field(default_factory=dict)
    refused_at: str = ""                          # compile | amend | execution | release_gate
    refusal_kind: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "released"

    @property
    def released_output(self) -> str | None:
        return self.released.released_output if self.released is not None else None

    def limitations(self) -> tuple[str, ...]:
        """Everything a reader of this result should be told, from both layers.

        The compiler's warnings belong here rather than in a log. A program that passed
        with ``STAT412`` produced a point estimate with no interval, and that is a fact
        about the finding, not about the build.
        """
        out: list[str] = []
        if self.compiled is not None:
            out.extend(self.compiled.limitations())
        if self.released is not None:
            out.extend(self.released.limitations)
        return tuple(out)

    def summary(self) -> str:
        head = f"{self.status}"
        if self.refused_at:
            head += f" at {self.refused_at}" + (f": {self.refusal_kind}"
                                                if self.refusal_kind else "")
        if self.compiled is not None:
            head += f"; {self.compiled.diagnostics.summary()}"
        if self.reused:
            head += f"; {len(self.reused)} node(s) reused"
        return head


class ScientificRunService:
    """Compile a program, run what it lowers to, release only what the gate passes."""

    def __init__(self, kernel: Any, *, registry: Any = None, model: Any = None,
                 model_invoke: Callable[[str], str] | None = None,
                 evaluator: Any = None, validator: Any = None,
                 limits: LoopLimits | None = None,
                 delegate_backend: Callable[[Any], Any] | None = None,
                 checkpoints: Any = None, memory: Any = None, operations: Any = None,
                 journal: Any = None, world_model: Any = None, policy: Any = None,
                 options: CompileOptions | None = None) -> None:
        self.kernel = kernel
        self.policy = policy if policy is not None else kernel.policy
        if policy is not None and hasattr(kernel, "check_requirements"):
            kernel.check_requirements(self.policy)
        self.registry = registry
        self.model = model
        self.model_invoke = model_invoke
        self.evaluator = evaluator
        self.validator = validator or PlanValidator(registry=registry)
        self.limits = limits
        self.delegate_backend = delegate_backend
        self.checkpoints = checkpoints
        self.memory = memory
        self.operations = operations
        #: Optional ``WorkflowJournal``. Without one the run executes identically and an
        #: amendment later has nothing to reuse, which the amend path says rather than
        #: silently recomputing everything.
        self.journal = journal
        #: Optional ``ScientificWorldModel``. Without one the hypotheses stay in the
        #: program and do not become durable project state.
        self.world_model = world_model
        self.options = options or CompileOptions()
        self.finalizer = Finalizer(kernel, policy=self.policy)
        self.last_compiled: CompiledProgram | None = None
        self.last_loop_result: LoopResult | None = None

    # ------------------------------------------------------------------ public
    def compile(self, program: SIRProgram, envelope: RunEnvelope, *,
                options: CompileOptions | None = None) -> CompiledProgram:
        """Compile without running. The same passes the run path uses, and no others."""
        result = compile_program(
            program, envelope, policy=self.policy, registry=self.registry,
            options=options or self.options)
        self.last_compiled = result
        return result

    def run(self, program: SIRProgram, *, envelope: RunEnvelope | None = None,
            sources: Mapping[str, Any] | None = None, project_id: str = "",
            options: CompileOptions | None = None,
            require_goal_verification: bool | None = None) -> ScientificResult:
        envelope = envelope or self.policy.envelope(project_id=project_id)
        compiled = self.compile(program, envelope, options=options)
        if not compiled.ok:
            self._audit(envelope, "program_refused", program=program.program_id,
                        errors=len(compiled.errors))
            return ScientificResult(
                status="not_compiled", run_id=envelope.run_id, compiled=compiled,
                refused_at="compile",
                refusal_kind=", ".join(sorted({d.code for d in compiled.errors})))
        return self._execute(compiled, envelope, sources=sources, project_id=project_id,
                             require_goal_verification=require_goal_verification)

    def amend(self, program: SIRProgram, *, previous_run_id: str,
              envelope: RunEnvelope | None = None,
              previous: SIRProgram | None = None,
              sources: Mapping[str, Any] | None = None, project_id: str = "",
              options: CompileOptions | None = None,
              require_goal_verification: bool | None = None) -> ScientificResult:
        """Run a changed program, keeping every node whose signature is unchanged.

        Refuses rather than proceeding when a node whose earlier attempt is in doubt would
        have to run again and its effect cannot be taken back. That decision belongs to
        whoever owns the effect — the same posture ``OperationUnresolved`` takes for a
        single tool call, applied to a whole amended program.
        """
        from ..durable import Decision, diff

        envelope = envelope or self.policy.envelope(project_id=project_id)
        compiled = self.compile(program, envelope, options=options)
        if not compiled.ok:
            return ScientificResult(
                status="not_compiled", run_id=envelope.run_id, compiled=compiled,
                refused_at="compile",
                refusal_kind=", ".join(sorted({d.code for d in compiled.errors})))
        if self.journal is None:
            raise ValueError(
                "amending needs a journal: without one there is no record of what the "
                "previous run did, so nothing can be shown to be still valid")

        replay = self.journal.replay(previous_run_id)
        delta = diff(program, replay, previous=previous)
        if not delta.safe:
            self._audit(envelope, "amendment_refused", program=program.program_id,
                        unsafe=[d.node_id for d in delta.unsafe])
            return ScientificResult(
                status="refused", run_id=envelope.run_id, compiled=compiled, delta=delta,
                refused_at="amend", refusal_kind="UnsafeReplay")
        return self._execute(compiled, envelope, sources=sources, project_id=project_id,
                             delta=delta, replay=replay,
                             require_goal_verification=require_goal_verification)

    # ----------------------------------------------------------------- internals
    def _execute(self, compiled: CompiledProgram, envelope: RunEnvelope, *,
                 sources: Mapping[str, Any] | None = None, project_id: str = "",
                 delta: Any = None, replay: Any = None,
                 require_goal_verification: bool | None = None) -> ScientificResult:
        # Imported here rather than at module scope: ``psh.durable`` imports
        # ``psh.runtime.checkpoint``, so a top-level import would close a cycle through
        # ``psh.runtime.__init__``.
        from ..durable import Decision, RecordKind, seed_graph

        program = compiled.program
        plan = compiled.plan
        assert plan is not None                     # compiled.ok guarantees it

        written: dict[str, str] = {}
        if self.world_model is not None and envelope.project_id:
            try:
                written = self._ingest(compiled, envelope)
            except Exception as exc:  # noqa: BLE001 - the science record is not the run
                self._audit(envelope, "world_model_ingest_failed",
                            error_type=type(exc).__name__)

        if self.journal is not None:
            self.journal.append(RecordKind.RUN_STARTED, envelope.run_id,
                                program_id=program.program_id)
            self.journal.append(
                RecordKind.PROGRAM_AMENDED if delta is not None
                else RecordKind.PROGRAM_COMPILED,
                envelope.run_id, program_id=program.program_id,
                nodes=len(program.nodes), warnings=len(compiled.warnings))

        state = None
        reused: tuple[str, ...] = ()
        if delta is not None and replay is not None:
            state = self._seeded_state(compiled, envelope)
            reused = seed_graph(state.graph, delta, replay)
            if self.journal is not None:
                for node_id in reused:
                    self.journal.append(RecordKind.NODE_REUSED, envelope.run_id,
                                        node_id=node_id,
                                        signature=compiled.signatures.get(node_id, ""))
                for node_delta in delta.nodes:
                    if node_delta.decision is Decision.RECOMPUTE:
                        self.journal.append(
                            RecordKind.NODE_INVALIDATED, envelope.run_id,
                            node_id=node_delta.node_id, signature=node_delta.signature,
                            reason=node_delta.reason[:120])

        loop = AgentLoopController(
            self.kernel, planner=StaticPlanner(plan), registry=self.registry,
            model=self.model, model_invoke=self.model_invoke, evaluator=self.evaluator,
            validator=self.validator, limits=self.limits,
            delegate_backend=self.delegate_backend, checkpoints=self.checkpoints,
            memory=self.memory, operations=self.operations, sleep=lambda s: None,
            observer=self._journal_observer(envelope, compiled))
        result = loop.run(program.question, envelope, resume_from=state)
        self.last_loop_result = result

        if self.journal is not None:
            self.journal.append(RecordKind.RUN_FINISHED, envelope.run_id,
                                program_id=program.program_id,
                                termination=result.termination.value)

        released = self.finalizer.finalize(
            result, envelope, sources=sources,
            project_id=project_id or envelope.project_id,
            require_goal_verification=require_goal_verification)
        return ScientificResult(
            status="released" if released.ok else "refused", run_id=envelope.run_id,
            compiled=compiled, released=released, delta=delta, reused=reused,
            world_model_nodes=written,
            refused_at="" if released.ok else (released.refused_at or "execution"),
            refusal_kind="" if released.ok else released.refusal_kind)

    def _ingest(self, compiled: CompiledProgram,
                envelope: RunEnvelope) -> dict[str, str]:
        """Write a compiled programme's hypotheses into the ledger, through the world model.

        The mapping lives here rather than in either of them. ``psh.scientist`` must not
        know what an ``SIRProgram`` is — it records science, not workflows — and the
        compiler must not know what a ledger requires, or a programme would be refused for
        a persistence rule. So the service, which already depends on both, is where the
        two vocabularies meet.

        ``Hypothesis`` requires a population, predictions, falsifiers and alternatives, all
        non-empty. A programme that declares a hypothesis with no scope, or no prediction
        carrying a falsifier, cannot be recorded — and that refusal is reported as a
        failure to ingest rather than as a failure of the run, because the run is
        governed either way.
        """
        from ..scientist import Hypothesis
        from ..sir import Role

        program = compiled.program
        predictions: dict[str, list[Any]] = {}
        for node in program.by_role(Role.PREDICTION):
            spec = node.prediction
            if spec is not None and spec.hypothesis:
                predictions.setdefault(spec.hypothesis, []).append(spec)

        written: dict[str, str] = {}
        order = [n for n in program.by_role(Role.HYPOTHESIS) if n.hypothesis is not None]
        for node in order:
            spec = node.hypothesis
            mine = predictions.get(node.node_id, [])
            record = Hypothesis(
                proposition=spec.proposition,
                population=spec.scope or spec.mechanism or "unstated",
                predictions=tuple(p.statement for p in mine) or (spec.proposition,),
                falsifiers=tuple(p.falsifier for p in mine if p.falsifier)
                or ("unstated",),
                alternatives=tuple(spec.alternative_to) or ("none recorded",))
            # Re-running a programme — the normal case after an amendment — must not
            # record the same hypothesis again. The ledger's records are append-only by
            # design, so a second identical write is a second hypothesis as far as every
            # later query is concerned, and a project with the same hypothesis four times
            # cannot answer "where does this stand": each copy accumulates its own edges
            # and none of them is the one.
            existing = self._recorded(record, envelope)
            if existing is not None:
                written[node.node_id] = existing
                continue
            written[node.node_id] = self.world_model.declare(
                record, envelope,
                alternative_to=[written[a] for a in spec.alternative_to
                                if a in written]).id
        self._audit(envelope, "world_model_ingested", program=program.program_id,
                    hypotheses=len(written))
        return written

    def _recorded(self, record: Any, envelope: RunEnvelope) -> str | None:
        """The node id of an identical hypothesis already in this project, if there is one.

        Compared on the record's own content, read back through the ledger — so a record
        whose stored body no longer matches its hash is not silently treated as a match.
        """
        from dataclasses import asdict

        wanted = asdict(record)
        for node in self.world_model.hypotheses():
            try:
                body = self.world_model.ledger.read(node.id, envelope)
            except Exception:  # noqa: BLE001 - unreadable here means "not a match"
                continue
            stored = dict(body.get("record") or {})
            for key, value in list(stored.items()):
                if isinstance(value, list):
                    stored[key] = tuple(value)
            if stored == {k: (tuple(v) if isinstance(v, (list, tuple)) else v)
                          for k, v in wanted.items()}:
                return node.id
        return None

    def _seeded_state(self, compiled: CompiledProgram,
                      envelope: RunEnvelope) -> LoopState:
        """A loop state holding the compiled plan, ready for reused results to be seeded.

        Built the way ``checkpoint.resume`` builds one — the plan validated first, so the
        envelopes the loop executes under are the ones the validator ruled on — because a
        second construction path for task envelopes is the defect this package has closed
        twice.
        """
        from ..contracts import new_id
        from .execgraph import ExecutionGraph

        plan = compiled.plan
        validated = self.validator.validate(plan, envelope, policy=self.policy)
        return LoopState(loop_id=new_id("loop"), objective=compiled.program.question,
                         envelope=envelope, plan=plan, graph=ExecutionGraph(plan),
                         validated=validated)

    def _journal_observer(self, envelope: RunEnvelope,
                          compiled: CompiledProgram) -> Callable[[TaskEvent], None] | None:
        """Write each task's start and outcome to the journal, under the storage rules."""
        if self.journal is None:
            return None
        from ..durable import RecordKind

        persistence = getattr(self.kernel, "persistence", None)
        ceiling = getattr(persistence, "max_label", None)
        allow = envelope.permits_destination(Destination.PERSISTENT)
        signatures = dict(compiled.signatures)
        program_id = compiled.program.program_id

        def observe(event: TaskEvent) -> None:
            signature = signatures.get(event.task_id, "")
            if event.state == "started":
                self.journal.append(RecordKind.NODE_STARTED, envelope.run_id,
                                    node_id=event.task_id, signature=signature,
                                    program_id=program_id, attempt=event.attempt)
            elif event.state == "succeeded":
                self.journal.record_success(
                    envelope.run_id, event.task_id, signature=signature,
                    result=event.result, label=event.label, ceiling=ceiling,
                    allow_results=allow, program_id=program_id)
            else:
                self.journal.append(RecordKind.NODE_FAILED, envelope.run_id,
                                    node_id=event.task_id, signature=signature,
                                    program_id=program_id, state=event.state,
                                    error_type=event.error_type)

        return observe

    def _audit(self, envelope: RunEnvelope, event: str, **detail: Any) -> None:
        audit = getattr(self.kernel, "audit", None)
        if audit is not None:
            try:
                audit(event, run_id=envelope.run_id, detail=dict(detail))
            except Exception:  # noqa: BLE001
                pass
