"""Is this graph a program at all?

The cheapest family to explain and therefore the first to run. Everything downstream
assumes the graph is acyclic, that every reference resolves and that each node's kind
matches what it declares it does; a type error reported against a node inside a cycle is
noise, so this pass runs first and the pipeline stops if it fails.

It also carries the one check lifted wholesale from the executor's history. The
2026-09-18 review found a plan whose payload read ``"symbol": "$fetch.gene"`` and whose
tool received that string verbatim, because a payload is data and nothing resolves it.
``PlanValidator`` catches it at the plan; catching it here means the *author* — usually a
model — is told at the point where it still has the program in hand.
"""

from __future__ import annotations

import re
from typing import Any, Mapping

from ..sir import (
    DECLARATIVE_ROLES, Effect, ExecKind, IMPLIED_SIDE_EFFECT, Role, SIRNode, SIRProgram,
    SideEffectClass,
)
from .diagnostics import Diagnostics

__all__ = ["check_structure", "MODEL_EFFECTS"]

#: The three effects that mean "this step prompts a model". Exactly one belongs on a model
#: node: the destination a model call reaches is the thing the gateway rules on, and a node
#: declaring two of them is describing two different calls.
MODEL_EFFECTS: frozenset[Effect] = frozenset({
    Effect.LOCAL_MODEL, Effect.REMOTE_MODEL_TRUSTED, Effect.REMOTE_MODEL_PUBLIC})

#: A payload string that reads like a reference to another node's result.
_REFERENCE = re.compile(r"^\$\{?([A-Za-z_][\w-]*)(?:[./][\w-]+)*\}?$")

#: role -> the specification field that role requires. A hypothesis node with no
#: hypothesis spec is a node whose role is decoration, and every later pass that reads the
#: spec would silently skip it.
_ROLE_SPEC: Mapping[Role, str] = {
    Role.HYPOTHESIS: "hypothesis",
    Role.PREDICTION: "prediction",
    Role.ANALYSIS: "analysis",
    Role.EXPERIMENT: "experiment",
    Role.CLAIM: "claim",
}


def check_structure(program: SIRProgram, *,
                    diagnostics: Diagnostics | None = None) -> Diagnostics:
    """Graph validity, node coherence and unresolvable references."""
    out = diagnostics if diagnostics is not None else Diagnostics(pass_name="structure")
    ids = program.ids

    # ------------------------------------------------------------------ graph
    for node in program.nodes:
        for dependency in node.dependencies:
            if dependency not in ids:
                out.emit("SIR001",
                         f"node {node.node_id!r} depends on {dependency!r}, which is not "
                         "in this program", node_id=node.node_id, missing=dependency)
    ordered = program.order()
    if len(ordered) < len(program.nodes):
        stuck = sorted(n.node_id for n in program.nodes if n.node_id not in ordered)
        out.emit("SIR002",
                 f"these nodes are in a dependency cycle: {stuck}", nodes=stuck)

    if not program.executable():
        out.emit("SIR010",
                 "the program declares hypotheses and produces nothing: no node executes, "
                 "so nothing could answer the question")
    if not program.acceptance:
        out.emit("SIR011",
                 "the program states no acceptance condition, so the loop could only stop "
                 "on exhaustion")

    # ------------------------------------------------------------------ nodes
    for node in program.nodes:
        _check_node(node, program, out)
    return out


def _check_node(node: SIRNode, program: SIRProgram, out: Diagnostics) -> None:
    ids = program.ids

    # --- kind coherence ---------------------------------------------------
    if node.executes and not node.objective.strip():
        out.emit("SIR003",
                 f"node {node.node_id!r} is a {node.kind.value} step and says nothing "
                 "about what it achieves", node_id=node.node_id)

    if node.kind is ExecKind.TOOL:
        if not node.component_id:
            out.emit("SIR004",
                     f"node {node.node_id!r} calls a tool and names no component",
                     node_id=node.node_id)
        if not node.effects:
            out.emit("SIR007",
                     f"node {node.node_id!r} invokes {node.component_id or 'a component'} "
                     "and declares no effects; a step whose effects are unknown cannot be "
                     "checked against this run's authority before it runs",
                     node_id=node.node_id)
    elif node.kind is ExecKind.MODEL:
        model_effects = node.effects & MODEL_EFFECTS
        if len(model_effects) != 1:
            out.emit("SIR005",
                     f"node {node.node_id!r} is a model call and declares "
                     f"{sorted(e.value for e in model_effects) or 'no model effect'}; "
                     "exactly one says where the model runs, which is what the gateway "
                     "rules on", node_id=node.node_id,
                     declared=sorted(e.value for e in model_effects))
    elif node.kind is ExecKind.DELEGATE:
        if Effect.DELEGATE not in node.effects:
            out.emit("SIR006",
                     f"node {node.node_id!r} delegates and does not declare the delegate "
                     "effect", node_id=node.node_id)
    else:  # DECLARATION
        if node.effects:
            out.emit("SIR008",
                     f"node {node.node_id!r} is a {node.role.value} and declares effects "
                     f"{sorted(e.value for e in node.effects)}; a declaration performs "
                     "nothing", node_id=node.node_id)

    # --- role / spec ------------------------------------------------------
    required = _ROLE_SPEC.get(node.role)
    if required is not None and getattr(node, required, None) is None:
        out.emit("SIR013",
                 f"node {node.node_id!r} has role {node.role.value} and carries no "
                 f"{required} specification, so every check that reads one would skip it",
                 node_id=node.node_id, expected=required)
    if node.role in DECLARATIVE_ROLES and node.executes:
        out.emit("SIR013",
                 f"node {node.node_id!r} has role {node.role.value}, which declares rather "
                 f"than executes, and kind {node.kind.value}", node_id=node.node_id)

    # --- repeat semantics -------------------------------------------------
    for effect in sorted(node.effects, key=lambda e: e.value):
        implied = IMPLIED_SIDE_EFFECT.get(effect)
        if implied is None:
            continue
        if _weaker(node.side_effect, implied):
            out.emit("SIR014",
                     f"node {node.node_id!r} declares the effect {effect.value} and the "
                     f"repeat semantics {node.side_effect.value}; that effect is at most "
                     f"{implied.value}, because re-running it changes the world again",
                     node_id=node.node_id, effect=effect.value,
                     required=implied.value)

    if (node.evidence_required or node.produces.is_claim) and not node.output_schema:
        # ``PlanTask`` refuses this combination at construction, and a constructor error
        # during lowering is a crash rather than a diagnostic — so the rule is checked
        # where it can be reported and repaired.
        out.emit("SIR017",
                 f"node {node.node_id!r} "
                 + ("produces a claim, so it must return the evidence for it, and"
                    if node.produces.is_claim else "requires evidence and")
                 + " declares no output schema; there is nothing to return it in",
                 node_id=node.node_id)

    # --- ports ------------------------------------------------------------
    for port in node.inputs:
        source = program.node(port.source)
        if source is None:
            continue                       # already reported as SIR001
        if not source.executes:
            out.emit("SIR009",
                     f"node {node.node_id!r} binds {port.argument!r} to {port.source!r}, "
                     f"which is a {source.role.value} declaration and produces no result",
                     node_id=node.node_id, source=port.source)

    if node.fan_out is not None and node.fan_out.source not in ids:
        out.emit("SIR015",
                 f"node {node.node_id!r} iterates {node.fan_out.source!r}, which is not in "
                 "this program", node_id=node.node_id)

    # --- payload literals -------------------------------------------------
    bound = {p.argument for p in node.inputs}
    for key, value in (node.payload or {}).items():
        if key in bound:
            out.emit("SIR012",
                     f"node {node.node_id!r} gives {key!r} both a payload literal and an "
                     "input port; a value has one source", node_id=node.node_id,
                     argument=key)
            continue
        if not isinstance(value, str):
            continue
        match = _REFERENCE.match(value.strip())
        if match:
            out.emit("SIR012",
                     f"node {node.node_id!r} passes the literal string {value!r} as "
                     f"{key!r}, which reads like a reference to {match.group(1)!r}; "
                     "nothing resolves a payload, so the component receives those "
                     "characters", node_id=node.node_id, argument=key,
                     looks_like=match.group(1))


#: Repeat semantics from freest to most constrained. A node may always declare something
#: *stronger* than an effect implies; declaring something weaker is the contradiction.
_ORDER: tuple[SideEffectClass, ...] = (
    SideEffectClass.PURE, SideEffectClass.IDEMPOTENT, SideEffectClass.AT_LEAST_ONCE,
    SideEffectClass.COMPENSATABLE, SideEffectClass.AT_MOST_ONCE,
    SideEffectClass.NON_REPEATABLE)


def _weaker(declared: SideEffectClass, required: SideEffectClass) -> bool:
    return _ORDER.index(declared) < _ORDER.index(required)
