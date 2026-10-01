"""The compiler: the ordered passes, and what comes out of them.

```
    SIRProgram
        |
    structure      is this a program?                 SIR
        |          (stops here on error: every later pass assumes an acyclic graph)
    method         is the science complete?           SCI
    typecheck      does the evidence license it?      TYP
    effects        may this run do that?              EFF
    infoflow       where may these values go?         IFC
    statistics     will the design support it?        STAT
    reproducibility could anyone repeat it?           REP
    resources      can this run afford it?            RES
        |
    lowering       -> psh.runtime.plan.Plan
        |
    PlanValidator -> AgentLoopController -> ExecutionBroker -> TrustedKernel
```

The ordering is by how cheap a failure is to explain, which is the order
``PlanValidator`` already uses for its five families. A type error reported against a node
inside a dependency cycle is noise, so structure runs first and alone.

The property that keeps this honest is stated in ``lowering``: **the compiler refuses, it
never permits.** Its output is a plan the existing validator validates and the existing
gates gate. Deleting this package would make nothing permitted that is forbidden today —
which is the test in ``tests/test_compiler.py`` that matters most.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from ..contracts import PolicyDenied, RunEnvelope
from ..runtime.plan import Plan
from ..sir import SIRProgram
from .diagnostics import Diagnostic, Diagnostics, Severity
from .effects import check_effects
from .infoflow import FlowResult, check_flow
from .lowering import lower
from .method import check_method
from .reproducibility import check_reproducibility
from .resources import check_resources, multipliers
from .statistics import check_statistics
from .structure import check_structure
from .typecheck import check_types

__all__ = ["CompileOptions", "CompiledProgram", "CompileRejected", "compile_program",
           "compile_or_raise", "PASS_ORDER"]

#: The passes, in order. Named so a report can say which layer objected, and so a test can
#: assert the order rather than infer it from the code.
PASS_ORDER: tuple[str, ...] = (
    "structure", "method", "typecheck", "effects", "infoflow", "statistics",
    "reproducibility", "resources")


@dataclass(frozen=True, slots=True)
class CompileOptions:
    """How strictly to compile. Set by a profile, never by the program being compiled.

    ``severity_overrides`` is the one knob that changes a verdict, and it exists for a
    real distinction: an exploratory analysis legitimately runs without a preregistered
    subgroup list, while a confirmatory one does not. Both are policy decisions, so they
    are made where policy is made. A program cannot reach this object — a plan that could
    downgrade the rule refusing it is not being checked.
    """

    severity_overrides: Mapping[str, Severity] = field(default_factory=dict)
    require_preregistration: bool = False
    approval_available: bool = True
    #: Skip the statistical family entirely. For a program with no analysis in it, this
    #: changes nothing; for one with an analysis it is a deliberate decision to stop
    #: checking, so it is recorded on the result.
    check_statistics: bool = True
    check_reproducibility: bool = True

    def exploratory(self) -> "CompileOptions":
        """The same options with the statistical errors softened to warnings.

        Exploratory work is not sloppy work: leakage and an uncorrected screen still
        invalidate a result, so ``STAT406`` and ``STAT401`` stay errors. What softens is
        the family that assumes a confirmatory frame — power, preregistered subgroups,
        replication.
        """
        from dataclasses import replace as _replace

        softened = dict(self.severity_overrides)
        for code in ("STAT403", "STAT404", "STAT407", "STAT409", "STAT413"):
            softened.setdefault(code, Severity.WARNING)
        return _replace(self, severity_overrides=softened)


@dataclass(frozen=True, slots=True)
class CompiledProgram:
    """What a compilation produced: the diagnostics, and the plan if there is one."""

    program: SIRProgram
    diagnostics: Diagnostics
    flow: FlowResult | None = None
    plan: Plan | None = None
    signatures: Mapping[str, str] = field(default_factory=dict)
    fan_out: Mapping[str, int] = field(default_factory=dict)
    options: CompileOptions = field(default_factory=CompileOptions)

    @property
    def ok(self) -> bool:
        """True when nothing blocking was found **and** a plan was produced."""
        return self.diagnostics.ok and self.plan is not None

    @property
    def errors(self) -> list[Diagnostic]:
        return self.diagnostics.errors

    @property
    def warnings(self) -> list[Diagnostic]:
        return self.diagnostics.warnings

    def limitations(self) -> tuple[str, ...]:
        """Warnings, phrased for the release.

        A compilation that passes with warnings has not been found clean; it has been
        found releasable with things worth saying. ``Finalizer`` lists them as limitations
        of the output, which is where a reader of the result can actually see them.
        """
        return tuple(f"{d.code}: {d.message}" for d in self.diagnostics.warnings)

    def summary(self) -> str:
        head = "compiled" if self.ok else "refused"
        return (f"{head}: {self.program.summary()}; {self.diagnostics.summary()}")

    def report(self) -> dict[str, Any]:
        return {"program_id": self.program.program_id, "ok": self.ok,
                "question": self.program.question,
                "nodes": len(self.program.nodes),
                "tasks": len(self.plan.tasks) if self.plan is not None else 0,
                "diagnostics": [d.as_dict() for d in self.diagnostics],
                "fan_out": dict(self.fan_out),
                "signatures": dict(self.signatures)}


class CompileRejected(PolicyDenied):
    """A program was refused before execution. Carries every diagnostic, not the first.

    A subclass of ``PolicyDenied`` so the loop's existing handling treats it as the
    refusal it is — ``Termination.POLICY_DENIED`` rather than an unrecoverable error — and
    so a caller that already handles policy refusals needs no new branch.
    """

    def __init__(self, message: str, result: "CompiledProgram") -> None:
        super().__init__(message)
        self.result = result
        self.diagnostics = tuple(result.diagnostics)
        self.violations = tuple(result.errors)


def compile_program(program: SIRProgram, envelope: RunEnvelope, *, policy: Any = None,
                    registry: Any = None,
                    options: CompileOptions | None = None) -> CompiledProgram:
    """Run every pass and return the result. Never raises for an author's mistake."""
    options = options or CompileOptions()
    overrides = dict(options.severity_overrides)
    collected = Diagnostics(pass_name="compile", severity_overrides=overrides)

    structure = Diagnostics(pass_name="structure", severity_overrides=overrides)
    check_structure(program, diagnostics=structure)
    collected.extend(structure.items)
    if not structure.ok:
        # Every later pass assumes the graph resolves and does not loop. Reporting type
        # errors against a node inside a cycle would bury the one finding that matters.
        return CompiledProgram(program=program, diagnostics=collected, options=options)

    for name, run in (("method", lambda d: check_method(program, diagnostics=d)),
                      ("typecheck", lambda d: check_types(program, diagnostics=d)),
                      ("effects", lambda d: check_effects(
                          program, envelope,
                          approval_available=options.approval_available, diagnostics=d))):
        buffer = Diagnostics(pass_name=name, severity_overrides=overrides)
        run(buffer)
        collected.extend(buffer.items)

    flow_buffer = Diagnostics(pass_name="infoflow", severity_overrides=overrides)
    _, flow = check_flow(program, envelope, policy=policy, diagnostics=flow_buffer)
    collected.extend(flow_buffer.items)

    if options.check_statistics:
        buffer = Diagnostics(pass_name="statistics", severity_overrides=overrides)
        check_statistics(program, diagnostics=buffer)
        collected.extend(buffer.items)
    if options.check_reproducibility:
        buffer = Diagnostics(pass_name="reproducibility", severity_overrides=overrides)
        check_reproducibility(program, registry=registry,
                              require_preregistration=options.require_preregistration,
                              diagnostics=buffer)
        collected.extend(buffer.items)

    buffer = Diagnostics(pass_name="resources", severity_overrides=overrides)
    check_resources(program, envelope, diagnostics=buffer)
    collected.extend(buffer.items)

    fan_out = multipliers(program)
    signatures = program.signatures()
    plan = None
    if collected.ok:
        plan = lower(program, envelope, flow=flow)

    return CompiledProgram(program=program, diagnostics=collected, flow=flow, plan=plan,
                           signatures=signatures, fan_out=fan_out, options=options)


def compile_or_raise(program: SIRProgram, envelope: RunEnvelope, *, policy: Any = None,
                     registry: Any = None,
                     options: CompileOptions | None = None) -> CompiledProgram:
    """Compile, or raise ``CompileRejected`` naming every blocking diagnostic.

    The message is written to be fed back to whatever wrote the program. ``ModelPlanner``
    already turns a refusal into the next attempt's input, and a message reading "the
    program is invalid" would make that loop produce the same program again.
    """
    result = compile_program(program, envelope, policy=policy, registry=registry,
                             options=options)
    if result.ok:
        return result
    errors = result.errors
    head = (f"program {program.program_id} is not executable under run {envelope.run_id}: "
            f"{len(errors)} blocking diagnostic(s)")
    raise CompileRejected(head + "\n" + result.diagnostics.render(), result)
