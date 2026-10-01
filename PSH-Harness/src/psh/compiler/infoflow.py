"""Information flow: what travels with a value, checked before the value exists.

``psh.labels`` is already an information-flow lattice and the runtime already joins it at
every edge. This pass does two things that a runtime join cannot.

**It sees the whole path at once.** A runtime join answers "may *this* value go *there*"
at the moment of the call. A workflow's real question is "does any path in this program
carry identifiable data to a public provider", and that is a property of the graph. The
answer is available before anything runs, so a plan that would be refused at step six is
refused at step zero.

**It carries three dimensions the runtime label does not.** Sensitivity is the one
``DataLabel`` tracks. A research workflow loses three more at every hop:

* **provenance** — a table computed from pasted text is no better attributed than the text,
  and by the time it is a table it looks like a measurement.
* **designs** — which study designs fed this value, so the type pass can ask whether any of
  them licenses the claim at the end of the chain.
* **licence terms** — a dataset licensed for research only taints everything derived from
  it, and the obligation has to survive three transformations and a summary.

The rule that makes the whole thing worth having is the one about going *down*:

> A derived value's label is the join of its inputs. It goes down only through an explicit,
> named, policy-permitted declassification.

A count derived from a PHI cohort is PHI until somebody says, on the record, which process
de-identified it. That is the difference between a taint analysis and laundering.
"""

from __future__ import annotations

from typing import Any, Mapping

from ..contracts import RunEnvelope
from ..labels import Sensitivity
from ..sir import (
    EFFECTS, Effect, FlowLabel, Provenance, SIRNode, SIRProgram, ceiling_for,
    join_labels, provenance_rank,
)
from .diagnostics import Diagnostics

__all__ = ["check_flow", "FlowResult", "LICENCE_FORBIDS"]

#: Licence terms and the effects they forbid. A source licensed for research only may be
#: analysed locally and may not be published or handed to a public provider — a condition
#: that is invisible to a sensitivity lattice, because the data may be perfectly public.
LICENCE_FORBIDS: Mapping[str, frozenset[Effect]] = {
    "research_only": frozenset({Effect.EXTERNAL_PUBLISH, Effect.REMOTE_MODEL_PUBLIC}),
    "no_redistribution": frozenset({Effect.EXTERNAL_PUBLISH}),
    "no_commercial": frozenset({Effect.EXTERNAL_PUBLISH}),
    "internal_only": frozenset({Effect.EXTERNAL_PUBLISH, Effect.REMOTE_MODEL_PUBLIC,
                                Effect.NETWORK_PUBLIC}),
}


class FlowResult:
    """The labels the pass derived, kept so lowering and the world model can use them."""

    def __init__(self, labels: Mapping[str, FlowLabel],
                 incoming: Mapping[str, FlowLabel]) -> None:
        #: The label of the value each node *produces*.
        self.labels = dict(labels)
        #: The join of what each node *receives*. Different from ``labels`` exactly where
        #: a declassification sits, which is the only place they may differ.
        self.incoming = dict(incoming)

    def label_of(self, node_id: str) -> FlowLabel:
        return self.labels.get(node_id, FlowLabel())

    def as_dict(self) -> dict[str, Any]:
        return {nid: label.as_dict() for nid, label in sorted(self.labels.items())}


def check_flow(program: SIRProgram, envelope: RunEnvelope, *, policy: Any = None,
               diagnostics: Diagnostics | None = None) -> tuple[Diagnostics, FlowResult]:
    """Propagate labels through the graph and report every illegal flow."""
    out = diagnostics if diagnostics is not None else Diagnostics(pass_name="infoflow")
    labels: dict[str, FlowLabel] = {}
    incoming: dict[str, FlowLabel] = {}

    declassifiers = tuple(getattr(policy, "declassifiers", ()) or ())
    floor = getattr(policy, "declassify_floor", Sensitivity.PUBLIC)

    # Nodes in dependency order, so a node's inputs are labelled before it is. A node in a
    # cycle is skipped — the structure pass has already refused the program, and labelling
    # around a cycle would either not terminate or invent an answer.
    for node_id in program.order():
        node = program.node(node_id)
        if node is None:
            continue
        received = join_labels(
            labels[d] for d in node.dependencies if d in labels)
        incoming[node_id] = received

        produced = _produced_label(node, received, out, declassifiers, floor)
        labels[node_id] = produced

        _check_node_flow(node, received, produced, envelope, out)

    return out, FlowResult(labels, incoming)


def _produced_label(node: SIRNode, received: FlowLabel, out: Diagnostics,
                    declassifiers: tuple[str, ...], floor: Sensitivity) -> FlowLabel:
    """The label of what this node produces: its inputs' join, or what it declares."""
    declared = node.declared_label

    if declared is not None and node.dependencies:
        # A node that both reads other nodes and declares its own label is claiming to
        # know something about a derived value. That is fine upward — it may be more
        # sensitive than its inputs — and is laundering downward.
        if declared.sensitivity < received.sensitivity:
            out.emit("IFC304",
                     f"node {node.node_id!r} declares {declared.sensitivity.name} and "
                     f"derives from values classified {received.sensitivity.name}; a "
                     "value does not become less sensitive by being computed",
                     node_id=node.node_id, declared=declared.sensitivity.name,
                     derived=received.sensitivity.name)
        label = received.join(declared)
    elif declared is not None:
        label = declared
    else:
        label = received

    if node.declassify_to is None:
        return label

    # ---- the one path downward -------------------------------------------
    target: Sensitivity = node.declassify_to
    if not node.declassify_method.strip():
        out.emit("IFC302",
                 f"node {node.node_id!r} lowers its label to {target.name} and names no "
                 "method; a declassification nobody can describe is not one",
                 node_id=node.node_id)
        return label
    if target > label.sensitivity:
        # Not a declassification at all. Treat it as the join rather than silently
        # raising, and say nothing: raising a label is always permitted.
        return label
    if not declassifiers:
        out.emit("IFC303",
                 f"node {node.node_id!r} lowers {label.sensitivity.name} to {target.name} "
                 "and this policy names no principal permitted to declassify",
                 node_id=node.node_id, method=node.declassify_method)
        return label
    if target < floor:
        out.emit("IFC303",
                 f"node {node.node_id!r} lowers to {target.name} and this policy's "
                 f"declassification floor is {floor.name}", node_id=node.node_id,
                 floor=floor.name)
        return label
    return label.with_sensitivity(target)


def _check_node_flow(node: SIRNode, received: FlowLabel, produced: FlowLabel,
                     envelope: RunEnvelope, out: Diagnostics) -> None:
    if node.effects:
        # What flows *into* this operation is what its effects must be able to receive.
        # The ceiling is the minimum over the declared effects, so a step that reads PHI
        # and prompts a public model is bounded by the public model — which is the
        # combination this pass exists to refuse.
        ceiling = ceiling_for(node.effects)
        if received.sensitivity > ceiling:
            reaching = sorted(
                e.value for e in node.effects
                if EFFECTS[e].max_sensitivity() < received.sensitivity)
            out.emit("IFC301",
                     f"node {node.node_id!r} receives values classified "
                     f"{received.sensitivity.name} and declares {reaching}, which may "
                     f"receive at most {ceiling.name}", node_id=node.node_id,
                     received=received.sensitivity.name, ceiling=ceiling.name,
                     effects=reaching)

        for term in sorted(received.license_terms | produced.license_terms):
            forbidden = LICENCE_FORBIDS.get(term, frozenset()) & node.effects
            if forbidden:
                out.emit("IFC305",
                         f"node {node.node_id!r} performs "
                         f"{sorted(e.value for e in forbidden)} on a value licensed "
                         f"{term!r}", node_id=node.node_id, term=term,
                         effects=sorted(e.value for e in forbidden))

    if produced.sensitivity > envelope.max_label.sensitivity:
        out.emit("IFC306",
                 f"node {node.node_id!r} produces a value classified "
                 f"{produced.sensitivity.name} and this run's data ceiling is "
                 f"{envelope.max_label.sensitivity.name}", node_id=node.node_id,
                 produced=produced.sensitivity.name)

    if node.dependencies and produced.provenance is not Provenance.RETRIEVED_VERIFIED:
        weakest = min((d for d in (received.provenance,)), key=provenance_rank)
        if provenance_rank(weakest) < provenance_rank(Provenance.RETRIEVED) and (
                node.produces.is_claim or node.produces.is_evidence):
            out.emit("IFC307",
                     f"node {node.node_id!r} produces "
                     f"{'a claim' if node.produces.is_claim else 'evidence'} whose "
                     f"weakest input provenance is {weakest.value}",
                     node_id=node.node_id, provenance=weakest.value)
