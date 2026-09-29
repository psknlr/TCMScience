"""Does this run hold the authority the program's effects require?

``ToolGateway`` and ``ModelGateway`` ask this at the call and their answer is the one that
counts. This pass asks it of every node before the first one runs, and the difference is
not a second opinion but a different failure mode: a plan refused at step six has already
spent five steps of budget and performed five steps of side effects, and — in research —
may already have ordered reagents.

The direction of the check matters. Declaring an effect **obliges** the run to hold the
authority for it; it never confers any. A node that declares fewer effects than its
component actually performs is not thereby permitted more, because the gateway rules from
the manifest at the call and the node cannot edit a manifest. So the worst a mis-declared
node achieves is passing this pass and being refused later — exactly where it would have
been refused without this pass at all.
"""

from __future__ import annotations

from typing import Any

from ..contracts import Autonomy, RunEnvelope, _autonomy_rank
from ..sir import EFFECTS, Effect, SIRProgram, min_autonomy_for, min_risk_for, needs_approval
from .diagnostics import Diagnostics

__all__ = ["check_effects", "CONSEQUENTIAL"]

#: Effects whose consequences are not undone by a retry or a refund. A program performing
#: one should say how it will know the action succeeded, which is what an acceptance
#: condition is for.
CONSEQUENTIAL: frozenset[Effect] = frozenset({
    Effect.CLINICAL_ACTION, Effect.WETLAB_ACTION, Effect.EXTERNAL_PUBLISH})


def check_effects(program: SIRProgram, envelope: RunEnvelope, *,
                  approval_available: bool = True,
                  diagnostics: Diagnostics | None = None) -> Diagnostics:
    """Check every node's declared effects against the run's envelope.

    ``approval_available`` is whether the kernel has an approval handler wired. An effect
    requiring approval under a run that cannot obtain one is refused here rather than
    raising ``ApprovalRequired`` half way through — the kernel already fails closed on it,
    and failing closed at step six is still a failure at step six.
    """
    out = diagnostics if diagnostics is not None else Diagnostics(pass_name="effects")
    has_completion = any(a.kind in ("task", "artifact", "evidence")
                         for a in program.acceptance)

    for node in program.nodes:
        if not node.effects:
            continue
        for effect in sorted(node.effects, key=lambda e: e.value):
            spec = EFFECTS[effect]
            if spec.destination is not None and \
                    not envelope.permits_destination(spec.destination):
                out.emit("EFF201",
                         f"node {node.node_id!r} declares {effect.value}, which reaches "
                         f"{spec.destination.name}, and this run permits "
                         f"{sorted(d.name for d in envelope.allowed_destinations)}",
                         node_id=node.node_id, effect=effect.value,
                         destination=spec.destination.name)

        required_autonomy = min_autonomy_for(node.effects)
        if _autonomy_rank(envelope.autonomy) > _autonomy_rank(required_autonomy):
            out.emit("EFF202",
                     f"node {node.node_id!r} needs at least {required_autonomy.value} "
                     f"autonomy and this run holds {envelope.autonomy.value}",
                     node_id=node.node_id, required=required_autonomy.value,
                     held=envelope.autonomy.value)

        required_risk = min_risk_for(node.effects)
        if required_risk > envelope.risk:
            out.emit("EFF203",
                     f"node {node.node_id!r} incurs {required_risk.name} and this run's "
                     f"ceiling is {envelope.risk.name}", node_id=node.node_id,
                     required=required_risk.name, ceiling=envelope.risk.name)

        if needs_approval(node.effects):
            blocked = envelope.autonomy in (Autonomy.OBSERVE, Autonomy.SUGGEST)
            if blocked or not approval_available:
                reason = ("this run's autonomy is "
                          f"{envelope.autonomy.value}, which may not act" if blocked
                          else "no approval handler is configured, so the kernel fails "
                               "closed on the request")
                out.emit("EFF204",
                         f"node {node.node_id!r} declares an effect that requires human "
                         f"approval and {reason}", node_id=node.node_id)

        consequential = node.effects & CONSEQUENTIAL
        if consequential and not has_completion:
            out.emit("EFF205",
                     f"node {node.node_id!r} performs {sorted(e.value for e in consequential)} "
                     "and the program states no acceptance condition that could tell "
                     "whether it worked", node_id=node.node_id,
                     effects=sorted(e.value for e in consequential))
    return out
