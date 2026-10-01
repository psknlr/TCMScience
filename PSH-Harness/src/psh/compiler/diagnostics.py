"""Diagnostics: every refusal has a code, a remedy and a place it came from.

A compiler that says "the plan is invalid" teaches nobody anything, and it is worse than
useless where the author is a model: ``ModelPlanner`` feeds each refusal back as the next
attempt's input, so the message *is* the repair instruction. Every diagnostic below names
the node, the rule and what to write instead.

The registry is the second half. A code that is emitted and not registered is a rule
nobody documented and nothing can be configured against; ``tests/test_compiler.py``
asserts the two sets match. Registration also gives each code a default severity that a
profile may raise but — deliberately — the *program being compiled* may not lower. A plan
that could downgrade the check refusing it is not being checked.

Code families, chosen so the family alone says which layer objected:

    SIR   the graph is not a program at all
    TYP   the scientific types do not fit (evidence does not license the claim)
    EFF   the effects need authority this run does not hold
    IFC   information flow: sensitivity, provenance, licence terms
    STAT  the statistical design will not support the inference
    REP   the result would not be reproducible
    RES   the run cannot afford it
    SCI   the scientific method is incomplete (a hypothesis nothing could refute)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Iterable, Mapping, Sequence

__all__ = ["Severity", "Diagnostic", "DiagnosticSpec", "REGISTRY", "Diagnostics",
           "severity_of", "register"]


class Severity(IntEnum):
    """Ordered, so "the worst thing in this report" is a ``max``."""

    INFO = 0
    WARNING = 1
    ERROR = 2

    @property
    def label(self) -> str:
        return self.name.lower()


@dataclass(frozen=True, slots=True)
class DiagnosticSpec:
    """One rule: what it is called, how bad it is by default, and how to satisfy it."""

    code: str
    title: str
    severity: Severity
    remedy: str = ""


def _spec(code: str, title: str, severity: Severity, remedy: str = "") -> DiagnosticSpec:
    return DiagnosticSpec(code=code, title=title, severity=severity, remedy=remedy)


E, W, I = Severity.ERROR, Severity.WARNING, Severity.INFO

_SPECS: tuple[DiagnosticSpec, ...] = (
    # ---------------------------------------------------------------- structure
    _spec("SIR001", "dependency names a node that is not in this program", E,
          "add the node, or correct the id"),
    _spec("SIR002", "the dependency graph has a cycle", E,
          "break the cycle; a step cannot depend on its own output"),
    _spec("SIR003", "an executing node names no objective", E,
          "say what the step achieves"),
    _spec("SIR004", "a tool node names no component", E,
          "name a component_id from the registry"),
    _spec("SIR005", "a model node must declare exactly one model effect", E,
          "declare one of model.local, model.trusted_remote, model.public_remote"),
    _spec("SIR006", "a delegate node must declare the delegate effect", E,
          "declare effects: [\"delegate\"]"),
    _spec("SIR007", "a tool node declares no effects", E,
          "declare what the tool does; a step with no declared effect cannot be gated "
          "before it runs"),
    _spec("SIR008", "a declaration declares effects", E,
          "a hypothesis or a prediction performs nothing; remove the effects or make it "
          "an executing node"),
    _spec("SIR009", "a port reads a node that produces no value", E,
          "read from an executing node; a declaration has no result"),
    _spec("SIR010", "the program has no executable node", E,
          "add at least one model, tool or delegate node"),
    _spec("SIR011", "the program states no acceptance condition", E,
          "add acceptance: there is otherwise no definition of done"),
    _spec("SIR012", "a payload literal reads like a reference to another node", E,
          "declare an input port instead; a payload is never resolved"),
    _spec("SIR013", "the node's role and its specification disagree", E,
          "a hypothesis node carries a hypothesis spec, a prediction node a prediction "
          "spec, an analysis node an analysis spec"),
    _spec("SIR014", "the declared repeat semantics contradict a declared effect", E,
          "a step that commits laboratory or clinical resources is non_repeatable"),
    _spec("SIR015", "a fan-out iterates a node that is not in this program", E,
          "name the node whose result is iterated"),
    _spec("SIR017", "a node requires evidence and declares no output schema", E,
          "an evidence requirement implies a structured return; declare output_schema"),

    # ------------------------------------------------------------------- types
    _spec("TYP101", "a claim node states no claim type", E,
          "state produces.claim: what kind of assertion, about whom, how firmly"),
    _spec("TYP102", "no input evidence licenses this claim", E,
          "weaken the claim, change its kind, or add a source whose design reaches it"),
    _spec("TYP103", "the claim reaches beyond its evidence", W,
          "set claim.extrapolation_declared so the release records the stretch, or "
          "narrow the claim"),
    _spec("TYP104", "evidence provenance is unstated or unevidential", E,
          "state how the source was obtained; a value the system generated is not "
          "evidence for its own conclusion"),
    _spec("TYP105", "a retrieval node produces no evidence type", E,
          "state produces.evidence, including the study design"),
    _spec("TYP106", "a claim node reads no evidence", E,
          "bind the claim to the step that produced its evidence"),
    _spec("TYP107", "a source is marked retracted", E,
          "remove it; a retracted source cannot support a claim"),
    _spec("TYP108", "an input's expected type does not match what the source produces", W,
          "correct the expected_type, or the pointer"),

    # ----------------------------------------------------------------- effects
    _spec("EFF201", "an effect needs a destination this run does not permit", E,
          "run under a profile that permits it, or use a capability that does not "
          "require it"),
    _spec("EFF202", "an effect needs more autonomy than this run holds", E,
          "raise the run's autonomy, or remove the effect"),
    _spec("EFF203", "an effect exceeds the run's risk ceiling", E,
          "run under a profile whose risk ceiling covers it"),
    _spec("EFF204", "an effect requires human approval and this run cannot obtain one", E,
          "run at act_with_approval or above with an approval handler configured"),
    _spec("EFF205", "a consequential effect is declared with no acceptance condition "
                    "covering it", W,
          "state how the program will know the action succeeded"),

    # --------------------------------------------------------- information flow
    _spec("IFC301", "data flows into an operation whose destination may not receive it", E,
          "de-identify before this step, or route it to a destination the label permits"),
    _spec("IFC302", "a declassification states no method", E,
          "name the process that lowers the label (de-identification, aggregation)"),
    _spec("IFC303", "this policy does not permit declassification here", E,
          "a named declassifier in the policy must perform it"),
    _spec("IFC304", "a derived value claims a lower sensitivity than its inputs", E,
          "add an explicit declassification, or carry the inputs' label forward"),
    _spec("IFC305", "a licence term forbids the destination this step reaches", E,
          "a research-only source may not be published or sent to a public provider"),
    _spec("IFC306", "a value's label exceeds the run's data ceiling", E,
          "run under a profile whose ceiling covers it"),
    _spec("IFC307", "provenance weakens along this path", I,
          "recorded so the claim's certainty can reflect it"),

    # -------------------------------------------------------------- statistics
    _spec("STAT401", "multiple tests with no multiplicity correction", E,
          "declare multiplicity: bonferroni, holm, bh or permutation"),
    _spec("STAT402", "the number of tests is not stated", W,
          "state analysis.tests so multiplicity can be judged"),
    _spec("STAT403", "a confirmatory analysis states neither sample size nor power", W,
          "state analysis.sample_size and analysis.power"),
    _spec("STAT404", "a survival analysis states no censoring strategy", E,
          "state analysis.censoring"),
    _spec("STAT405", "a proportional-hazards model does not check its assumption", W,
          "set analysis.proportional_hazards_checked once the check is in the plan"),
    _spec("STAT406", "feature selection uses the evaluation data", E,
          "set analysis.feature_selection: train_only"),
    _spec("STAT407", "a predictive model is evaluated with no held-out data", E,
          "declare analysis.data_split: train_test, cross_validation or external"),
    _spec("STAT408", "a subgroup analysis was not preregistered", W,
          "preregister the subgroups, or report the analysis as exploratory"),
    _spec("STAT409", "a discovery analysis declares no null model", E,
          "declare analysis.null_model: permutation, random_features or "
          "negative_control; a significant result is not a finding until it beats one"),
    _spec("STAT410", "a batch variable is declared and not adjusted for", W,
          "set analysis.batch_adjusted, or state why the batch cannot confound"),
    _spec("STAT411", "missing data handling is unstated", W,
          "state analysis.missingness"),
    _spec("STAT412", "no interval estimate is reported", W,
          "set analysis.reports_interval; a point estimate without one is not a result"),
    _spec("STAT413", "a discovery claim rests on a single unreplicated analysis", W,
          "declare analysis.replication, or narrow the claim"),
    _spec("STAT414", "the significance threshold is implausible", W,
          "alpha is a probability; state the one the analysis will use"),
    _spec("STAT415", "more tests are declared than the data could support", W,
          "check analysis.tests against the rows the input produces"),

    # ---------------------------------------------------------- reproducibility
    _spec("REP501", "a non-deterministic step declares no seed", W,
          "set repro.seed, or state why the step cannot be seeded"),
    _spec("REP502", "a dataset version is unpinned", W,
          "set repro.dataset_version so the run can be repeated against the same data"),
    _spec("REP503", "a tool version is unpinned", W, "set repro.tool_version"),
    _spec("REP504", "an isolated component declares no environment", W,
          "set repro.container_digest or repro.environment"),
    _spec("REP505", "the program requires preregistration and declares no protocol", E,
          "add a protocol node and freeze it before the analysis runs"),
    _spec("REP506", "an analysis runs before the protocol it claims to follow is frozen", E,
          "make the analysis depend on the protocol node"),

    # ----------------------------------------------------------------- resources
    _spec("RES601", "the program's token estimate exceeds the run's ceiling", E,
          "reduce the plan, or run with a larger budget"),
    _spec("RES602", "the program's cost estimate exceeds the run's ceiling", E,
          "reduce the plan, or run with a larger budget"),
    _spec("RES603", "the critical path exceeds the run's time ceiling", E,
          "shorten the chain, or run with a longer deadline"),
    _spec("RES604", "the program needs more calls than the run's ceiling allows", E,
          "reduce the number of steps, or run with a larger budget"),
    _spec("RES605", "a fan-out states no bound", E,
          "set fan_out.max_items; an unbounded map over a produced list is an unbounded "
          "budget"),
    _spec("RES606", "a bounded fan-out still exceeds the run's ceiling", E,
          "lower the bound, or run with a larger budget"),

    # ------------------------------------------------------- scientific method
    _spec("SCI701", "a hypothesis has no prediction", W,
          "state what the hypothesis implies that could be observed"),
    _spec("SCI702", "a prediction has no falsifier", E,
          "state the observation that would refute it; a prediction nothing could "
          "contradict generates work and no knowledge"),
    _spec("SCI703", "a hypothesis has no alternative", W,
          "name the competing explanation the work is meant to rule out"),
    _spec("SCI704", "an experiment tests a hypothesis that is not in this program", E,
          "declare the hypothesis, or correct the id"),
    _spec("SCI705", "an experiment discriminates between no hypotheses", W,
          "say which hypotheses the result would tell apart"),
    _spec("SCI706", "a claim answers a question that is not in this program", E,
          "declare the question, or correct the id"),
    _spec("SCI707", "the program asserts a mechanism or efficacy claim and states no "
                    "hypothesis", W,
          "state the hypothesis the claim rests on, so it can be argued against"),
    _spec("SCI708", "a prediction is never tested", W,
          "add an analysis or experiment that reads it, or remove it"),
)

#: code -> spec. The single source of truth for what a code means.
REGISTRY: Mapping[str, DiagnosticSpec] = {s.code: s for s in _SPECS}


def register(spec: DiagnosticSpec) -> None:
    """Add a code at runtime. For an extension that adds a pass, not for a program."""
    if spec.code in REGISTRY:
        raise ValueError(f"diagnostic code {spec.code} is already registered")
    dict.__setitem__(REGISTRY, spec.code, spec)  # type: ignore[arg-type]


def severity_of(code: str) -> Severity:
    spec = REGISTRY.get(code)
    if spec is None:
        raise KeyError(
            f"diagnostic code {code!r} is not registered; a rule nobody documented cannot "
            "be configured or tested against")
    return spec.severity


@dataclass(frozen=True, slots=True)
class Diagnostic:
    """One finding: what rule, which node, why, and what to do about it."""

    code: str
    severity: Severity
    message: str
    node_id: str = ""
    pass_name: str = ""
    remedy: str = ""
    detail: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.message.strip():
            raise ValueError(f"diagnostic {self.code} states no message")
        if self.code not in REGISTRY:
            raise ValueError(
                f"diagnostic code {self.code!r} is not registered in "
                "psh.compiler.diagnostics.REGISTRY")
        if not self.remedy:
            object.__setattr__(self, "remedy", REGISTRY[self.code].remedy)

    @property
    def blocking(self) -> bool:
        return self.severity is Severity.ERROR

    def __str__(self) -> str:
        where = f" [{self.node_id}]" if self.node_id else ""
        remedy = f" — {self.remedy}" if self.remedy else ""
        return f"{self.severity.label.upper()} {self.code}{where}: {self.message}{remedy}"

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "severity": self.severity.label,
                "message": self.message, "node_id": self.node_id,
                "pass": self.pass_name, "remedy": self.remedy, "detail": dict(self.detail)}


class Diagnostics:
    """A pass's output buffer, and the report the compiler hands back.

    ``severity_overrides`` exists for the one legitimate reason to change a rule's weight:
    a profile. An exploratory-analysis profile may run the statistical pass at WARNING
    while a confirmatory one runs it at ERROR, and both are policy decisions taken where
    policy lives. The program being compiled cannot reach this, which is the point.
    """

    def __init__(self, *, pass_name: str = "",
                 severity_overrides: Mapping[str, Severity] | None = None) -> None:
        self.pass_name = pass_name
        self.overrides = dict(severity_overrides or {})
        self.items: list[Diagnostic] = []

    def emit(self, code: str, message: str, *, node_id: str = "",
             severity: Severity | None = None, remedy: str = "",
             **detail: Any) -> Diagnostic:
        effective = (severity if severity is not None
                     else self.overrides.get(code, severity_of(code)))
        item = Diagnostic(code=code, severity=effective, message=message,
                          node_id=node_id, pass_name=self.pass_name, remedy=remedy,
                          detail=detail)
        self.items.append(item)
        return item

    def extend(self, items: Iterable[Diagnostic]) -> None:
        self.items.extend(items)

    # ------------------------------------------------------------------ report
    @property
    def errors(self) -> list[Diagnostic]:
        return [d for d in self.items if d.severity is Severity.ERROR]

    @property
    def warnings(self) -> list[Diagnostic]:
        return [d for d in self.items if d.severity is Severity.WARNING]

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def worst(self) -> Severity:
        return max((d.severity for d in self.items), default=Severity.INFO)

    def codes(self) -> tuple[str, ...]:
        return tuple(d.code for d in self.items)

    def by_code(self, code: str) -> list[Diagnostic]:
        return [d for d in self.items if d.code == code]

    def has(self, code: str) -> bool:
        return any(d.code == code for d in self.items)

    def summary(self) -> str:
        if not self.items:
            return "no diagnostics"
        counts: dict[str, int] = {}
        for item in self.items:
            counts[item.severity.label] = counts.get(item.severity.label, 0) + 1
        return ", ".join(f"{n} {label}" for label, n in sorted(counts.items()))

    def render(self, limit: int = 12) -> str:
        shown = sorted(self.items, key=lambda d: (-int(d.severity), d.code))[:limit]
        more = len(self.items) - len(shown)
        text = "\n".join(str(d) for d in shown)
        return text + (f"\n… {more} more" if more > 0 else "")

    def __len__(self) -> int:
        return len(self.items)

    def __bool__(self) -> bool:
        """Always true, because ``__len__`` made an empty buffer falsy and that was a bug.

        Every pass took its output buffer as ``diagnostics or Diagnostics(...)``, which is
        the ordinary Python idiom and which — for a container with ``__len__`` — silently
        builds a *new* buffer whenever the caller passes an empty one. The passes then
        wrote their findings into an object nobody read, so the pipeline compiled a
        program whose claim asserted human efficacy from a mouse study and reported no
        diagnostics at all.

        The call sites are fixed to ``is not None``. This makes the idiom safe as well,
        because the next person to write it should not have to know. Emptiness is asked
        for with ``len(...)`` or ``.ok``.
        """
        return True

    def __iter__(self):
        return iter(self.items)
