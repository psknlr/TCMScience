"""The Scientific IR: one graph the compiler analyses and the runtime executes.

The decision this module encodes is **IR-first rather than language-first**. ZCode's
dynamic workflow compiles a TypeScript program: the model writes a script, a virtual
TypeScript host typechecks it, and a fixpoint recovers the dependency and taint structure
from the AST. That is the right architecture for an agent whose users write code, and
adopting it here would bind every analysis to one surface syntax.

A research plan arrives in at least four shapes — a model's JSON, a Python builder, a
declarative file, an imported CWL workflow — and they should not each need their own
compiler. So the analysable object is the IR, frontends produce it, and the passes in
``psh.compiler`` read only this module. Adding the CWL importer later adds a frontend, not
a second compiler.

The graph has two sorts of node and the distinction is the reason it can carry science at
all:

* **Declarations** (``ExecKind.DECLARATION``) — a question, a hypothesis, a prediction, a
  falsifier, a preregistered protocol. They execute nothing. They exist so the compiler
  can check that an analysis tests a stated prediction, that a hypothesis has something
  that could refute it, and that a claim is licensed by the evidence a step actually
  produced. A conventional plan has nowhere to put any of that, which is why a
  conventional plan cannot be checked for it.
* **Operations** (``MODEL`` / ``TOOL`` / ``DELEGATE``) — the three things
  ``AgentLoopController`` can dispatch. Lowering maps each to exactly one ``PlanTask``, so
  the executed object is the one the existing gates already govern.

Authority is **derived, not declared.** A node states what it does scientifically and what
effects it performs; the compiler computes the destinations, risk tier and autonomy that
follow from those effects. ``PlanTask`` asks an author to state authority directly, which
invites the two failure modes this repository keeps closing — a step that asks for more
than it needs, and a step that asks for less than it uses and is refused at the gate half
way through the run.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Mapping, Sequence

from ..contracts import content_hash, new_id
from .effects import Effect, SideEffectClass, effect_set
from .values import FlowLabel, ScientificType

__all__ = [
    "Role", "ExecKind", "Port", "FanOut", "AnalysisSpec", "ExperimentSpec",
    "HypothesisSpec", "PredictionSpec", "ClaimSpec", "ReproSpec", "SIRNode",
    "SIRProgram", "Acceptance",
]


class Role(str, Enum):
    """What a node *is* scientifically. Orthogonal to how (or whether) it executes."""

    QUESTION = "question"
    HYPOTHESIS = "hypothesis"
    PREDICTION = "prediction"
    PROTOCOL = "protocol"            # a preregistered plan, frozen before execution
    EXPERIMENT = "experiment"        # a designed test of a prediction
    RETRIEVAL = "retrieval"          # acquiring evidence
    TRANSFORM = "transform"          # data preparation with no inferential content
    ANALYSIS = "analysis"            # statistical or computational inference
    OBSERVATION = "observation"      # what came back, before interpretation
    CLAIM = "claim"                  # an assertion drawn from evidence
    REPORT = "report"                # the deliverable


class ExecKind(str, Enum):
    """How a node executes. ``DECLARATION`` is the one that does not."""

    DECLARATION = "declaration"
    MODEL = "model"
    TOOL = "tool"
    DELEGATE = "delegate"


#: Roles that never execute. A hypothesis is not a job.
DECLARATIVE_ROLES: frozenset[Role] = frozenset({
    Role.QUESTION, Role.HYPOTHESIS, Role.PREDICTION, Role.PROTOCOL})


@dataclass(frozen=True, slots=True)
class Port:
    """One input of a node: an argument filled from a place in another node's result.

    The same shape as ``runtime.plan.InputBinding``, and deliberately so — lowering maps
    one onto the other without interpretation. It lives here as well because the compiler
    analyses data flow before a ``Plan`` exists, and an analysis that had to build its own
    notion of "where does this value come from" would be a second answer to a question the
    executor already answers.
    """

    argument: str
    source: str
    pointer: str = ""
    expected_type: str = ""
    cardinality: str = "one"
    required: bool = True

    def __post_init__(self) -> None:
        if not self.argument.strip():
            raise ValueError("a port must name the argument it fills")
        if not self.source.strip():
            raise ValueError(f"port {self.argument!r} must name the node it reads from")
        if self.cardinality not in ("one", "many"):
            raise ValueError(
                f"port {self.argument!r} has cardinality {self.cardinality!r}; it is "
                "'one' or 'many'")

    def as_dict(self) -> dict[str, Any]:
        return {"argument": self.argument, "source": self.source, "pointer": self.pointer,
                "expected_type": self.expected_type, "cardinality": self.cardinality,
                "required": self.required}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Port":
        return cls(argument=str(data.get("argument") or ""),
                   source=str(data.get("source") or ""),
                   pointer=str(data.get("pointer") or ""),
                   expected_type=str(data.get("expected_type") or data.get("type") or ""),
                   cardinality=str(data.get("cardinality") or "one"),
                   required=bool(data.get("required", True)))


@dataclass(frozen=True, slots=True)
class FanOut:
    """This node runs once per item of an upstream list.

    ``max_items`` is the part that matters. ZCode's analyser estimates fan-out cardinality
    statically because an unbounded map over a model-produced list is how a bounded budget
    becomes an unbounded one, and the same applies here with a sharper edge: the list is
    often "every gene that passed the filter", whose length is not known until the filter
    has run. A node that cannot state a bound is refused by the resource pass rather than
    discovering the bound by exhausting the budget.
    """

    source: str
    pointer: str = ""
    max_items: int | None = None

    def __post_init__(self) -> None:
        if not self.source.strip():
            raise ValueError("a fan-out must name the node whose result it iterates")
        if self.max_items is not None and self.max_items < 1:
            raise ValueError("a fan-out bound must be at least 1")

    def as_dict(self) -> dict[str, Any]:
        return {"source": self.source, "pointer": self.pointer, "max_items": self.max_items}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "FanOut":
        return cls(source=str(data.get("source") or ""),
                   pointer=str(data.get("pointer") or ""),
                   max_items=data.get("max_items"))


@dataclass(frozen=True, slots=True)
class AnalysisSpec:
    """The statistical design of an analysis step, as fields rather than as prose.

    Every field exists because a pass in ``psh.compiler.statistics`` reads it. The list is
    the set of questions a methods reviewer asks and an agent routinely does not answer:
    how many tests, corrected how, powered for what, split how, censored how, adjusted for
    what, and compared against which null.

    An unstated field is ``""``/``None`` and is reported as *unstated*, never assumed
    satisfied. That asymmetry is the same one ``ScopeVerdict.incomplete`` encodes: a check
    that did not run must not read as a check that passed.
    """

    method: str = ""
    family: str = ""                 # descriptive | screen | regression | survival | classification
    tests: int | None = None
    multiplicity: str = ""           # none | bonferroni | holm | bh | permutation
    alpha: float = 0.05
    primary_endpoint: str = ""
    secondary_endpoints: tuple[str, ...] = ()
    sample_size: int | None = None
    power: float | None = None
    effect_size: float | None = None
    covariates: tuple[str, ...] = ()
    confounders_adjusted: bool = False
    subgroups: tuple[str, ...] = ()
    preregistered: bool = False
    data_split: str = ""             # none | train_test | cross_validation | external
    feature_selection: str = ""      # none | train_only | all_data
    censoring: str = ""              # none | right | interval | administrative
    proportional_hazards_checked: bool = False
    batch_variable: str = ""
    batch_adjusted: bool = False
    missingness: str = ""            # complete_case | imputed | none
    null_model: str = ""             # none | permutation | random_features | negative_control
    replication: str = ""            # none | internal | external
    sensitivity_analysis: bool = False
    reports_interval: bool = False
    notes: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"method": self.method, "family": self.family, "tests": self.tests,
                "multiplicity": self.multiplicity, "alpha": self.alpha,
                "primary_endpoint": self.primary_endpoint,
                "secondary_endpoints": list(self.secondary_endpoints),
                "sample_size": self.sample_size, "power": self.power,
                "effect_size": self.effect_size, "covariates": list(self.covariates),
                "confounders_adjusted": self.confounders_adjusted,
                "subgroups": list(self.subgroups), "preregistered": self.preregistered,
                "data_split": self.data_split, "feature_selection": self.feature_selection,
                "censoring": self.censoring,
                "proportional_hazards_checked": self.proportional_hazards_checked,
                "batch_variable": self.batch_variable,
                "batch_adjusted": self.batch_adjusted, "missingness": self.missingness,
                "null_model": self.null_model, "replication": self.replication,
                "sensitivity_analysis": self.sensitivity_analysis,
                "reports_interval": self.reports_interval, "notes": self.notes}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "AnalysisSpec":
        return cls(
            method=str(data.get("method") or ""), family=str(data.get("family") or ""),
            tests=(int(data["tests"]) if data.get("tests") is not None else None),
            multiplicity=str(data.get("multiplicity") or ""),
            alpha=float(data.get("alpha") if data.get("alpha") is not None else 0.05),
            primary_endpoint=str(data.get("primary_endpoint") or ""),
            secondary_endpoints=tuple(data.get("secondary_endpoints") or ()),
            sample_size=data.get("sample_size"), power=data.get("power"),
            effect_size=data.get("effect_size"),
            covariates=tuple(data.get("covariates") or ()),
            confounders_adjusted=bool(data.get("confounders_adjusted")),
            subgroups=tuple(data.get("subgroups") or ()),
            preregistered=bool(data.get("preregistered")),
            data_split=str(data.get("data_split") or ""),
            feature_selection=str(data.get("feature_selection") or ""),
            censoring=str(data.get("censoring") or ""),
            proportional_hazards_checked=bool(data.get("proportional_hazards_checked")),
            batch_variable=str(data.get("batch_variable") or ""),
            batch_adjusted=bool(data.get("batch_adjusted")),
            missingness=str(data.get("missingness") or ""),
            null_model=str(data.get("null_model") or ""),
            replication=str(data.get("replication") or ""),
            sensitivity_analysis=bool(data.get("sensitivity_analysis")),
            reports_interval=bool(data.get("reports_interval")),
            notes=str(data.get("notes") or ""))


@dataclass(frozen=True, slots=True)
class ExperimentSpec:
    """What an experiment would tell us, at what cost — so choosing between two is a
    decision rather than a preference.

    The planner that says "I think we should do a Western blot next" has made a
    recommendation nobody can argue with, because it cites nothing. These fields let a
    reader ask the only question that matters between two candidate experiments: *which of
    them better separates the hypotheses on the table?* The compiler does not score
    experiments for you — a single utility number pretending to combine information gain
    with wet-lab risk would be false precision. It refuses an experiment that claims to
    test a hypothesis while discriminating nothing.
    """

    tests: tuple[str, ...] = ()           # hypothesis node ids this experiment tests
    discriminates: tuple[str, ...] = ()   # hypotheses it tells apart from each other
    information_gain: float | None = None  # expected bits, when the author can state it
    cost_usd: float = 0.0
    duration_days: float = 0.0
    feasibility: float = 1.0              # 0..1
    risk: str = "low"                     # low | moderate | high
    power: float | None = None
    notes: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"tests": list(self.tests), "discriminates": list(self.discriminates),
                "information_gain": self.information_gain, "cost_usd": self.cost_usd,
                "duration_days": self.duration_days, "feasibility": self.feasibility,
                "risk": self.risk, "power": self.power, "notes": self.notes}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ExperimentSpec":
        return cls(tests=tuple(data.get("tests") or ()),
                   discriminates=tuple(data.get("discriminates") or ()),
                   information_gain=data.get("information_gain"),
                   cost_usd=float(data.get("cost_usd") or 0.0),
                   duration_days=float(data.get("duration_days") or 0.0),
                   feasibility=float(data.get("feasibility") if data.get("feasibility")
                                     is not None else 1.0),
                   risk=str(data.get("risk") or "low"), power=data.get("power"),
                   notes=str(data.get("notes") or ""))


@dataclass(frozen=True, slots=True)
class HypothesisSpec:
    """A hypothesis as an object with a mechanism, a scope and things that would refute it."""

    proposition: str
    mechanism: str = ""
    scope: str = ""
    prior: float | None = None
    assumptions: tuple[str, ...] = ()
    alternative_to: tuple[str, ...] = ()   # node ids of hypotheses this one competes with

    def __post_init__(self) -> None:
        if not self.proposition.strip():
            raise ValueError("a hypothesis must state its proposition")
        if self.prior is not None and not 0.0 <= self.prior <= 1.0:
            raise ValueError("a prior must be a probability in [0, 1]")

    def as_dict(self) -> dict[str, Any]:
        return {"proposition": self.proposition, "mechanism": self.mechanism,
                "scope": self.scope, "prior": self.prior,
                "assumptions": list(self.assumptions),
                "alternative_to": list(self.alternative_to)}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "HypothesisSpec":
        return cls(proposition=str(data.get("proposition") or ""),
                   mechanism=str(data.get("mechanism") or ""),
                   scope=str(data.get("scope") or ""), prior=data.get("prior"),
                   assumptions=tuple(data.get("assumptions") or ()),
                   alternative_to=tuple(data.get("alternative_to") or ()))


@dataclass(frozen=True, slots=True)
class PredictionSpec:
    """What a hypothesis implies, and what observation would refute it.

    ``falsifier`` is required. A hypothesis whose every prediction is compatible with every
    outcome generates work and no knowledge, and the cheapest moment to notice is before
    the work is scheduled.
    """

    statement: str
    hypothesis: str = ""                   # node id
    falsifier: str = ""
    direction: str = ""                    # increase | decrease | no_effect | association

    def __post_init__(self) -> None:
        if not self.statement.strip():
            raise ValueError("a prediction must state what it predicts")

    def as_dict(self) -> dict[str, Any]:
        return {"statement": self.statement, "hypothesis": self.hypothesis,
                "falsifier": self.falsifier, "direction": self.direction}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "PredictionSpec":
        return cls(statement=str(data.get("statement") or ""),
                   hypothesis=str(data.get("hypothesis") or ""),
                   falsifier=str(data.get("falsifier") or ""),
                   direction=str(data.get("direction") or ""))


@dataclass(frozen=True, slots=True)
class ClaimSpec:
    """An assertion the program intends to make, and whether its stretch is declared.

    ``extrapolation_declared`` is the author saying "yes, this reaches beyond the source,
    and the text will say so". It turns an ``EXTRAPOLATED`` verdict from an error into a
    recorded, releasable limitation — and leaves ``UNLICENSED`` an error either way, because
    declaring an inference does not make the source support it.
    """

    statement: str
    extrapolation_declared: bool = False
    answers: str = ""                      # question node id

    def __post_init__(self) -> None:
        if not self.statement.strip():
            raise ValueError("a claim must state what it asserts")

    def as_dict(self) -> dict[str, Any]:
        return {"statement": self.statement, "answers": self.answers,
                "extrapolation_declared": self.extrapolation_declared}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ClaimSpec":
        return cls(statement=str(data.get("statement") or ""),
                   answers=str(data.get("answers") or ""),
                   extrapolation_declared=bool(data.get("extrapolation_declared")))


@dataclass(frozen=True, slots=True)
class ReproSpec:
    """What a third party needs to get this step's result again.

    Absence is the finding. A stochastic step with no seed is not reproducible, and saying
    so before the run is cheaper than discovering it when the figure will not regenerate.
    """

    seed: int | None = None
    dataset_version: str = ""
    tool_version: str = ""
    environment: str = ""
    container_digest: str = ""

    @property
    def stated(self) -> bool:
        return any((self.seed is not None, self.dataset_version, self.tool_version,
                    self.environment, self.container_digest))

    def as_dict(self) -> dict[str, Any]:
        return {"seed": self.seed, "dataset_version": self.dataset_version,
                "tool_version": self.tool_version, "environment": self.environment,
                "container_digest": self.container_digest}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ReproSpec":
        return cls(seed=data.get("seed"),
                   dataset_version=str(data.get("dataset_version") or ""),
                   tool_version=str(data.get("tool_version") or ""),
                   environment=str(data.get("environment") or ""),
                   container_digest=str(data.get("container_digest") or ""))


@dataclass(frozen=True, slots=True)
class Acceptance:
    """One condition for the program's objective to count as met."""

    description: str
    kind: str = "manual"                   # task | evidence | claim | artifact | manual

    def __post_init__(self) -> None:
        if not self.description.strip():
            raise ValueError("an acceptance condition must describe what it requires")

    def as_dict(self) -> dict[str, Any]:
        return {"description": self.description, "kind": self.kind}


@dataclass(frozen=True, slots=True)
class SIRNode:
    """One node of the scientific program.

    Authority is absent from this type on purpose. A node says what it does
    (``effects``), what it is (``role``), what it produces (``produces``) and what it
    reads (``inputs``); ``psh.compiler.lowering`` derives the destinations, risk tier and
    autonomy those effects require. An author who could state authority directly could
    understate it, and the gate would then refuse the step mid-run — which is the failure
    mode ``PlanValidator`` exists to move earlier, applied one level further up.
    """

    node_id: str
    role: Role = Role.TRANSFORM
    kind: ExecKind = ExecKind.DECLARATION
    objective: str = ""
    component_id: str = ""
    payload: Mapping[str, Any] = field(default_factory=dict)
    inputs: tuple[Port, ...] = ()
    depends_on: tuple[str, ...] = ()
    produces: ScientificType = field(default_factory=ScientificType)
    #: The label of a value this node **introduces** (a retrieval, a dataset read). For a
    #: node whose output is computed from its inputs the compiler derives the label and
    #: this stays ``None``; stating one here is how a source declares what it brought in.
    declared_label: FlowLabel | None = None
    #: An explicit, attributable lowering of the derived sensitivity — the only way a
    #: value's classification goes down. ``method`` names the process (de-identification,
    #: aggregation with a k-anonymity threshold) and the compiler records it.
    declassify_to: Any = None              # Sensitivity | None
    declassify_method: str = ""
    effects: frozenset[Effect] = frozenset()
    side_effect: SideEffectClass = SideEffectClass.PURE
    deterministic: bool = True
    cache: bool = True
    fan_out: FanOut | None = None
    analysis: AnalysisSpec | None = None
    experiment: ExperimentSpec | None = None
    hypothesis: HypothesisSpec | None = None
    prediction: PredictionSpec | None = None
    claim: ClaimSpec | None = None
    repro: ReproSpec = field(default_factory=ReproSpec)
    about: tuple[str, ...] = ()            # hypothesis / prediction ids this concerns
    output_schema: Mapping[str, Any] = field(default_factory=dict)
    acceptance_tests: tuple[Mapping[str, Any], ...] = ()
    evidence_required: bool = False
    estimated_tokens: int = 0
    estimated_usd: float = 0.0
    estimated_seconds: float = 0.0
    max_attempts: int = 1
    notes: str = ""

    def __post_init__(self) -> None:
        if not self.node_id or not str(self.node_id).strip():
            raise ValueError("a node requires an id")
        if self.node_id in self.depends_on:
            raise ValueError(f"node {self.node_id!r} depends on itself")
        object.__setattr__(self, "effects", effect_set(self.effects))
        seen: set[str] = set()
        for port in self.inputs:
            if port.source == self.node_id:
                raise ValueError(
                    f"node {self.node_id!r} binds {port.argument!r} to its own result")
            if port.argument in seen:
                raise ValueError(
                    f"node {self.node_id!r} binds argument {port.argument!r} twice")
            seen.add(port.argument)
        # An executing node with no objective is a compiler diagnostic (SIR003), not a
        # constructor error: the IR stores what a frontend produced, and the compiler is
        # the one place that judges it. Raising here would mean a malformed program could
        # not be *reported*, only crashed on — and a model cannot repair a traceback.

    # ------------------------------------------------------------------ graph
    @property
    def dependencies(self) -> tuple[str, ...]:
        """Every node that must finish before this one: explicit plus every port source.

        Order-preserving and deduplicated, so the lowered plan's dependency list is stable
        across compilations — which is what lets a content hash mean anything.
        """
        found = list(self.depends_on)
        for port in self.inputs:
            if port.source not in found:
                found.append(port.source)
        if self.fan_out is not None and self.fan_out.source not in found:
            found.append(self.fan_out.source)
        return tuple(found)

    @property
    def executes(self) -> bool:
        return self.kind is not ExecKind.DECLARATION

    @property
    def introduces_value(self) -> bool:
        """Whether this node brings a value in from outside rather than deriving one."""
        return self.declared_label is not None or not self.inputs

    def fingerprint(self) -> str:
        """A hash of what this node *is*, for incremental recompute.

        Identity is content, never position. ZCode's engine makes the same choice and
        states the reason: resolving a cached result by its ordinal means inserting a step
        at the top silently re-points every later result. So this covers the definition and
        nothing about where the node sits or when it ran.
        """
        return content_hash(self.as_dict())[:32]

    # ------------------------------------------------------------- serialisation
    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "node_id": self.node_id, "role": self.role.value, "kind": self.kind.value,
            "objective": self.objective, "component_id": self.component_id,
            "payload": dict(self.payload),
            "inputs": [p.as_dict() for p in self.inputs],
            "depends_on": list(self.depends_on),
            "produces": self.produces.as_dict(),
            "declared_label": (self.declared_label.as_dict()
                               if self.declared_label is not None else None),
            "declassify_to": (self.declassify_to.name
                              if self.declassify_to is not None else None),
            "declassify_method": self.declassify_method,
            "effects": sorted(e.value for e in self.effects),
            "side_effect": self.side_effect.value,
            "deterministic": self.deterministic, "cache": self.cache,
            "fan_out": self.fan_out.as_dict() if self.fan_out is not None else None,
            "analysis": self.analysis.as_dict() if self.analysis is not None else None,
            "experiment": (self.experiment.as_dict()
                           if self.experiment is not None else None),
            "hypothesis": (self.hypothesis.as_dict()
                           if self.hypothesis is not None else None),
            "prediction": (self.prediction.as_dict()
                           if self.prediction is not None else None),
            "claim": self.claim.as_dict() if self.claim is not None else None,
            "repro": self.repro.as_dict(), "about": list(self.about),
            "output_schema": dict(self.output_schema),
            "acceptance_tests": [dict(t) for t in self.acceptance_tests],
            "evidence_required": self.evidence_required,
            "estimated_tokens": self.estimated_tokens,
            "estimated_usd": self.estimated_usd,
            "estimated_seconds": self.estimated_seconds,
            "max_attempts": self.max_attempts, "notes": self.notes,
        }
        return out

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SIRNode":
        from ..labels import Sensitivity

        declassify = data.get("declassify_to")
        return cls(
            node_id=str(data.get("node_id") or data.get("id") or ""),
            role=Role(data.get("role") or Role.TRANSFORM.value),
            kind=ExecKind(data.get("kind") or ExecKind.DECLARATION.value),
            objective=str(data.get("objective") or ""),
            component_id=str(data.get("component_id") or ""),
            payload=dict(data.get("payload") or {}),
            inputs=tuple(Port.from_dict(p) for p in data.get("inputs") or ()),
            depends_on=tuple(str(d) for d in data.get("depends_on") or ()),
            produces=ScientificType.from_dict(data.get("produces") or {}),
            declared_label=(FlowLabel.from_dict(data["declared_label"])
                            if data.get("declared_label") else None),
            declassify_to=(Sensitivity[declassify] if declassify else None),
            declassify_method=str(data.get("declassify_method") or ""),
            effects=effect_set(data.get("effects") or ()),
            side_effect=SideEffectClass(data.get("side_effect")
                                        or SideEffectClass.PURE.value),
            deterministic=bool(data.get("deterministic", True)),
            cache=bool(data.get("cache", True)),
            fan_out=FanOut.from_dict(data["fan_out"]) if data.get("fan_out") else None,
            analysis=(AnalysisSpec.from_dict(data["analysis"])
                      if data.get("analysis") else None),
            experiment=(ExperimentSpec.from_dict(data["experiment"])
                        if data.get("experiment") else None),
            hypothesis=(HypothesisSpec.from_dict(data["hypothesis"])
                        if data.get("hypothesis") else None),
            prediction=(PredictionSpec.from_dict(data["prediction"])
                        if data.get("prediction") else None),
            claim=ClaimSpec.from_dict(data["claim"]) if data.get("claim") else None,
            repro=ReproSpec.from_dict(data.get("repro") or {}),
            about=tuple(str(a) for a in data.get("about") or ()),
            output_schema=dict(data.get("output_schema") or {}),
            acceptance_tests=tuple(dict(t) for t in data.get("acceptance_tests") or ()),
            evidence_required=bool(data.get("evidence_required")),
            estimated_tokens=int(data.get("estimated_tokens") or 0),
            estimated_usd=float(data.get("estimated_usd") or 0.0),
            estimated_seconds=float(data.get("estimated_seconds") or 0.0),
            max_attempts=int(data.get("max_attempts") or 1),
            notes=str(data.get("notes") or ""))


@dataclass(frozen=True, slots=True)
class SIRProgram:
    """A whole scientific program: the question, the nodes, and what would finish it."""

    question: str
    nodes: tuple[SIRNode, ...] = ()
    acceptance: tuple[Acceptance, ...] = ()
    assumptions: tuple[str, ...] = ()
    project_id: str = ""
    program_id: str = field(default_factory=lambda: new_id("sirp"))
    produced_by: str = "unknown"
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.question.strip():
            raise ValueError("a program requires a research question")
        seen: set[str] = set()
        for node in self.nodes:
            if node.node_id in seen:
                raise ValueError(f"duplicate node id {node.node_id!r}")
            seen.add(node.node_id)

    # ------------------------------------------------------------------ access
    def node(self, node_id: str) -> SIRNode | None:
        for node in self.nodes:
            if node.node_id == node_id:
                return node
        return None

    @property
    def ids(self) -> frozenset[str]:
        return frozenset(n.node_id for n in self.nodes)

    def by_role(self, role: Role) -> tuple[SIRNode, ...]:
        return tuple(n for n in self.nodes if n.role is role)

    def executable(self) -> tuple[SIRNode, ...]:
        return tuple(n for n in self.nodes if n.executes)

    def dependents_of(self, node_id: str) -> tuple[str, ...]:
        return tuple(n.node_id for n in self.nodes if node_id in n.dependencies)

    def order(self) -> tuple[str, ...]:
        """A topological order of the nodes. Nodes inside a cycle are omitted.

        Reporting the cycle is the graph pass's job — this returns what it could order, so
        a caller can see how far the ordering got rather than being handed an exception it
        cannot explain.
        """
        pending = {n.node_id: [d for d in n.dependencies if d in self.ids]
                   for n in self.nodes}
        ordered: list[str] = []
        ready = [nid for nid, deps in pending.items() if not deps]
        while ready:
            current = ready.pop(0)
            ordered.append(current)
            for nid, deps in pending.items():
                if current in deps:
                    deps.remove(current)
                    if not deps and nid not in ordered and nid not in ready:
                        ready.append(nid)
        return tuple(ordered)

    def upstream_of(self, node_id: str) -> frozenset[str]:
        """Every node this one transitively depends on."""
        seen: set[str] = set()
        frontier = [node_id]
        while frontier:
            current = frontier.pop()
            node = self.node(current)
            if node is None:
                continue
            for dependency in node.dependencies:
                if dependency not in seen and dependency in self.ids:
                    seen.add(dependency)
                    frontier.append(dependency)
        return frozenset(seen)

    def downstream_of(self, node_id: str) -> frozenset[str]:
        """Every node that transitively depends on this one."""
        seen: set[str] = set()
        frontier = [node_id]
        while frontier:
            current = frontier.pop()
            for dependent in self.dependents_of(current):
                if dependent not in seen:
                    seen.add(dependent)
                    frontier.append(dependent)
        return frozenset(seen)

    # ------------------------------------------------------------ fingerprints
    def signature(self, node_id: str, _seen: frozenset[str] | None = None) -> str:
        """The hash of a node **and everything it depends on**.

        This is the value incremental recompute compares. A node whose own definition is
        unchanged but whose upstream changed has a changed signature, so "may I reuse the
        recorded result?" is one string comparison rather than a graph walk at reuse time.
        Cycles resolve to the node's own fingerprint rather than recursing forever; the
        graph pass refuses the program in that case anyway.
        """
        seen = _seen or frozenset()
        node = self.node(node_id)
        if node is None or node_id in seen:
            return ""
        upstream = [self.signature(d, seen | {node_id}) for d in node.dependencies]
        return content_hash({"self": node.fingerprint(), "upstream": upstream})[:32]

    def signatures(self) -> dict[str, str]:
        return {n.node_id: self.signature(n.node_id) for n in self.nodes}

    @property
    def estimated_tokens(self) -> int:
        return sum(n.estimated_tokens for n in self.nodes)

    @property
    def estimated_usd(self) -> float:
        return sum(n.estimated_usd for n in self.nodes)

    def summary(self) -> str:
        roles: dict[str, int] = {}
        for node in self.nodes:
            roles[node.role.value] = roles.get(node.role.value, 0) + 1
        return (f"{len(self.nodes)} node(s) {roles}; {len(self.executable())} executable, "
                f"~{self.estimated_tokens} tokens, ~${self.estimated_usd:.2f}")

    # ------------------------------------------------------------ serialisation
    def as_dict(self) -> dict[str, Any]:
        return {"program_id": self.program_id, "question": self.question,
                "project_id": self.project_id, "produced_by": self.produced_by,
                "assumptions": list(self.assumptions),
                "acceptance": [a.as_dict() for a in self.acceptance],
                "nodes": [n.as_dict() for n in self.nodes],
                "metadata": dict(self.metadata)}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SIRProgram":
        return cls(
            question=str(data.get("question") or ""),
            nodes=tuple(SIRNode.from_dict(n) for n in data.get("nodes") or ()),
            acceptance=tuple(
                Acceptance(description=str(a.get("description") or ""),
                           kind=str(a.get("kind") or "manual"))
                for a in data.get("acceptance") or ()),
            assumptions=tuple(str(a) for a in data.get("assumptions") or ()),
            project_id=str(data.get("project_id") or ""),
            program_id=str(data.get("program_id") or new_id("sirp")),
            produced_by=str(data.get("produced_by") or "unknown"),
            metadata=dict(data.get("metadata") or {}))

    def with_nodes(self, nodes: Sequence[SIRNode]) -> "SIRProgram":
        """A copy carrying different nodes — how an amendment is expressed.

        The program id is preserved: an amended program is the same program at a later
        moment, which is what lets the delta compare the two.
        """
        from dataclasses import replace as _replace

        return _replace(self, nodes=tuple(nodes))
