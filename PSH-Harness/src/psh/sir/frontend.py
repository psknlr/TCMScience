"""Frontends: the ways a scientific program gets into the IR.

Two ship here. A **JSON frontend**, because that is what a model writes, and a **builder**,
because that is what a person or a library writes. A CWL or WDL importer is a third
frontend and needs no compiler change, which is the whole argument for the IR sitting
where it does.

The JSON frontend's error messages are the part worth care. ZCode's schema validator
reports violations as path / expected / got specifically so the model can repair its own
output, and ``ModelPlanner`` here already feeds each refusal back as the next attempt's
input. A message reading "invalid program" makes that loop do nothing useful, so every
refusal below names the path and the legal values.
"""

from __future__ import annotations

import json
import re
from typing import Any, Mapping, Sequence

from ..contracts import ContractViolation
from .effects import Effect, SideEffectClass
from .nodes import (
    Acceptance, AnalysisSpec, ClaimSpec, ExecKind, ExperimentSpec, FanOut, HypothesisSpec,
    Port, PredictionSpec, ReproSpec, Role, SIRNode, SIRProgram,
)
from .values import (
    ClaimType, DataType, EvidenceType, FlowLabel, Provenance, ScientificType, StudyDesign,
    Subject,
)

__all__ = ["ProgramParseError", "parse_program", "ProgramBuilder"]


class ProgramParseError(ContractViolation):
    """A program could not be read into the IR. The message names the path and the fix."""


def _extract_json(text: str) -> dict[str, Any]:
    """Find the program object in whatever a model wrapped it in.

    Tolerant about the envelope and unforgiving about the contents — the same split
    ``runtime.planner`` makes, and for the same reason: being strict about fences buys
    nothing, while being lax about fields buys a program nobody checked.
    """
    stripped = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.+?)```", stripped, re.S)
    if fenced:
        stripped = fenced.group(1).strip()
    start = stripped.find("{")
    if start == -1:
        raise ProgramParseError("the frontend received no JSON object at all")
    depth, in_string, escaped, body = 0, False, False, ""
    for index, char in enumerate(stripped[start:], start=start):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                body = stripped[start:index + 1]
                break
    if not body:
        raise ProgramParseError("the program's JSON object is unterminated")
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ProgramParseError(f"the program's JSON does not parse: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ProgramParseError(f"expected a JSON object, got {type(parsed).__name__}")
    return parsed


def _enum(cls: Any, value: Any, path: str, default: Any = None) -> Any:
    """Resolve an enum member, reporting the path and every legal value on failure."""
    if value is None or value == "":
        if default is not None:
            return default
        raise ProgramParseError(
            f"{path} is required; legal values are {sorted(m.value for m in cls)}")
    try:
        return cls(str(value).strip())
    except ValueError:
        pass
    try:
        return cls[str(value).strip().upper()]
    except KeyError:
        raise ProgramParseError(
            f"{path} is {value!r}; legal values are {sorted(m.value for m in cls)}"
        ) from None


def _scientific_type(data: Mapping[str, Any], path: str) -> ScientificType:
    stated = [k for k in ("evidence", "claim", "data") if data.get(k)]
    if len(stated) > 1:
        raise ProgramParseError(
            f"{path} states {stated}; a value is evidence, a claim or data — a step that "
            "produces two of them needs two nodes")
    if data.get("evidence"):
        raw = data["evidence"]
        return ScientificType(evidence=EvidenceType(
            design=_enum(StudyDesign, raw.get("design"), f"{path}.evidence.design",
                         StudyDesign.UNKNOWN),
            subject=_enum(Subject, raw.get("subject"), f"{path}.evidence.subject",
                          Subject.UNSPECIFIED),
            population=str(raw.get("population") or ""),
            intervention=str(raw.get("intervention") or ""),
            comparator=str(raw.get("comparator") or ""),
            outcome=str(raw.get("outcome") or ""),
            outcome_is_surrogate=bool(raw.get("outcome_is_surrogate")),
            provenance=_enum(Provenance, raw.get("provenance"),
                             f"{path}.evidence.provenance", Provenance.UNKNOWN),
            identifier=str(raw.get("identifier") or ""),
            retracted=raw.get("retracted")))
    if data.get("claim"):
        raw = data["claim"]
        from ..evidence.support import Certainty
        from .values import ClaimKind

        return ScientificType(claim=ClaimType(
            kind=_enum(ClaimKind, raw.get("kind"), f"{path}.claim.kind"),
            subject=_enum(Subject, raw.get("subject"), f"{path}.claim.subject",
                          Subject.UNSPECIFIED),
            population=str(raw.get("population") or ""),
            intervention=str(raw.get("intervention") or ""),
            outcome=str(raw.get("outcome") or ""),
            certainty=_enum(Certainty, raw.get("certainty"), f"{path}.claim.certainty",
                            Certainty.MODERATE)))
    if data.get("data"):
        return ScientificType(data=DataType.from_dict(data["data"]))
    return ScientificType()


def _node(raw: Mapping[str, Any], index: int) -> SIRNode:
    path = f"nodes[{index}]"
    node_id = str(raw.get("node_id") or raw.get("id") or "").strip()
    if not node_id:
        raise ProgramParseError(f"{path}.node_id is required")
    try:
        effects = frozenset(
            _enum(Effect, e, f"{path}.effects[{i}]") for i, e in enumerate(raw.get("effects") or ()))
    except ProgramParseError:
        raise
    from ..labels import Sensitivity

    declassify = raw.get("declassify_to")
    if declassify and str(declassify).upper() not in Sensitivity.__members__:
        raise ProgramParseError(
            f"{path}.declassify_to is {declassify!r}; legal values are "
            f"{sorted(Sensitivity.__members__)}")
    try:
        return SIRNode(
            node_id=node_id,
            role=_enum(Role, raw.get("role"), f"{path}.role", Role.TRANSFORM),
            kind=_enum(ExecKind, raw.get("kind"), f"{path}.kind", ExecKind.DECLARATION),
            objective=str(raw.get("objective") or ""),
            component_id=str(raw.get("component_id") or ""),
            payload=dict(raw.get("payload") or {}),
            inputs=tuple(Port.from_dict(p) for p in raw.get("inputs") or ()),
            depends_on=tuple(str(d) for d in raw.get("depends_on") or ()),
            produces=_scientific_type(raw.get("produces") or {}, f"{path}.produces"),
            declared_label=(FlowLabel.from_dict(raw["declared_label"])
                            if raw.get("declared_label") else None),
            declassify_to=(Sensitivity[str(declassify).upper()] if declassify else None),
            declassify_method=str(raw.get("declassify_method") or ""),
            effects=effects,
            side_effect=_enum(SideEffectClass, raw.get("side_effect"),
                              f"{path}.side_effect", SideEffectClass.PURE),
            deterministic=bool(raw.get("deterministic", True)),
            cache=bool(raw.get("cache", True)),
            fan_out=FanOut.from_dict(raw["fan_out"]) if raw.get("fan_out") else None,
            analysis=(AnalysisSpec.from_dict(raw["analysis"])
                      if raw.get("analysis") else None),
            experiment=(ExperimentSpec.from_dict(raw["experiment"])
                        if raw.get("experiment") else None),
            hypothesis=(HypothesisSpec.from_dict(raw["hypothesis"])
                        if raw.get("hypothesis") else None),
            prediction=(PredictionSpec.from_dict(raw["prediction"])
                        if raw.get("prediction") else None),
            claim=ClaimSpec.from_dict(raw["claim"]) if raw.get("claim") else None,
            repro=ReproSpec.from_dict(raw.get("repro") or {}),
            about=tuple(str(a) for a in raw.get("about") or ()),
            output_schema=dict(raw.get("output_schema") or {}),
            acceptance_tests=tuple(dict(t) if isinstance(t, Mapping) else {"kind": str(t)}
                                   for t in raw.get("acceptance_tests") or ()),
            evidence_required=bool(raw.get("evidence_required")),
            estimated_tokens=int(raw.get("estimated_tokens") or 0),
            estimated_usd=float(raw.get("estimated_usd") or 0.0),
            estimated_seconds=float(raw.get("estimated_seconds") or 0.0),
            max_attempts=max(1, int(raw.get("max_attempts") or 1)),
            notes=str(raw.get("notes") or ""))
    except (ValueError, TypeError) as exc:
        # The node's own construction rules: self-dependency, a duplicate argument, an
        # executing node with no objective. Their messages already name the node.
        raise ProgramParseError(f"{path}: {exc}") from exc


def parse_program(source: Any, *, produced_by: str = "frontend") -> SIRProgram:
    """Read a program from JSON text or a mapping. Refuses anything it cannot execute."""
    raw = _extract_json(source) if isinstance(source, str) else dict(source or {})
    question = str(raw.get("question") or raw.get("objective") or "").strip()
    if not question:
        raise ProgramParseError(
            "question is required: a program without a research question has nothing to "
            "be judged against")
    nodes_raw = raw.get("nodes")
    if not isinstance(nodes_raw, Sequence) or isinstance(nodes_raw, (str, bytes)) \
            or not nodes_raw:
        raise ProgramParseError("nodes must be a non-empty array")
    nodes = tuple(_node(n, i) for i, n in enumerate(nodes_raw)
                  if isinstance(n, Mapping) or _raise_node(n, i))
    acceptance = tuple(
        Acceptance(description=str(a.get("description") or ""),
                   kind=str(a.get("kind") or "manual"))
        if isinstance(a, Mapping) else Acceptance(description=str(a))
        for a in raw.get("acceptance") or raw.get("completion_criteria") or ())
    try:
        return SIRProgram(
            question=question, nodes=nodes, acceptance=acceptance,
            assumptions=tuple(str(a) for a in raw.get("assumptions") or ()),
            project_id=str(raw.get("project_id") or ""),
            produced_by=str(raw.get("produced_by") or produced_by),
            metadata=dict(raw.get("metadata") or {}))
    except ValueError as exc:
        raise ProgramParseError(str(exc)) from exc


def _raise_node(value: Any, index: int) -> bool:
    raise ProgramParseError(f"nodes[{index}] is not an object, it is a "
                            f"{type(value).__name__}")


class ProgramBuilder:
    """A small fluent builder, so a Python caller does not assemble dictionaries.

    It validates nothing the IR does not already validate — the compiler is the judge, and
    a builder that refused things the compiler allows would be a second, quieter policy.
    """

    def __init__(self, question: str, *, project_id: str = "",
                 produced_by: str = "builder") -> None:
        self.question = question
        self.project_id = project_id
        self.produced_by = produced_by
        self._nodes: list[SIRNode] = []
        self._acceptance: list[Acceptance] = []
        self._assumptions: list[str] = []

    def add(self, node: SIRNode) -> "ProgramBuilder":
        self._nodes.append(node)
        return self

    def node(self, node_id: str, **kw: Any) -> "ProgramBuilder":
        return self.add(SIRNode(node_id=node_id, **kw))

    def hypothesis(self, node_id: str, proposition: str, **kw: Any) -> "ProgramBuilder":
        spec = HypothesisSpec(proposition=proposition,
                              mechanism=kw.pop("mechanism", ""),
                              scope=kw.pop("scope", ""), prior=kw.pop("prior", None),
                              assumptions=tuple(kw.pop("assumptions", ())),
                              alternative_to=tuple(kw.pop("alternative_to", ())))
        return self.node(node_id, role=Role.HYPOTHESIS, kind=ExecKind.DECLARATION,
                         hypothesis=spec, **kw)

    def prediction(self, node_id: str, statement: str, *, hypothesis: str,
                   falsifier: str = "", **kw: Any) -> "ProgramBuilder":
        spec = PredictionSpec(statement=statement, hypothesis=hypothesis,
                              falsifier=falsifier, direction=kw.pop("direction", ""))
        return self.node(node_id, role=Role.PREDICTION, kind=ExecKind.DECLARATION,
                         prediction=spec, depends_on=(hypothesis,), **kw)

    def accept(self, description: str, kind: str = "manual") -> "ProgramBuilder":
        self._acceptance.append(Acceptance(description=description, kind=kind))
        return self

    def assume(self, assumption: str) -> "ProgramBuilder":
        self._assumptions.append(assumption)
        return self

    def build(self) -> SIRProgram:
        return SIRProgram(
            question=self.question, nodes=tuple(self._nodes),
            acceptance=tuple(self._acceptance), assumptions=tuple(self._assumptions),
            project_id=self.project_id, produced_by=self.produced_by)
