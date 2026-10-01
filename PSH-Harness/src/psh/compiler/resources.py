"""Can this run afford the program, including the parts whose size is not known yet?

``PlanValidator`` already compares a plan's estimates against the envelope's budget, and
this pass would be redundant with it but for one thing: **fan-out**.

A step that runs once per item of a list another step produced has a cost of one times an
unknown. ZCode's analyser estimates fan-out cardinality statically for the same reason, and
in a research workflow the unknown is usually "every gene that passed the filter" — a
number nobody can state until the filter has run, and one that turns a plan estimated at
four model calls into one that makes four hundred.

The rule is therefore a refusal rather than an estimate: a fan-out with no declared bound
is an error. An author who genuinely does not know the size writes the bound they are
willing to pay for, which is a decision someone has made rather than a number the run
discovers by exhausting its budget.
"""

from __future__ import annotations

from typing import Mapping

from ..contracts import RunEnvelope
from ..sir import ExecKind, SIRProgram
from .diagnostics import Diagnostics

__all__ = ["check_resources", "multipliers", "UNBOUNDED"]

#: The multiplier of a node whose fan-out states no bound. Not a large number — a number
#: that is not a number, so nothing downstream can accidentally arithmetic with it.
UNBOUNDED = -1


def multipliers(program: SIRProgram) -> dict[str, int]:
    """How many times each node runs, given the declared fan-out bounds.

    A node that iterates a source runs ``max_items`` times *per run of that source*, so
    nested fan-outs multiply. Anything unbounded anywhere in the chain is unbounded.
    """
    out: dict[str, int] = {}

    def resolve(node_id: str, seen: frozenset[str]) -> int:
        if node_id in out:
            return out[node_id]
        node = program.node(node_id)
        if node is None or node_id in seen:
            return 1
        if node.fan_out is None:
            value = 1
        elif node.fan_out.max_items is None:
            value = UNBOUNDED
        else:
            upstream = resolve(node.fan_out.source, seen | {node_id})
            value = (UNBOUNDED if upstream == UNBOUNDED
                     else node.fan_out.max_items * upstream)
        out[node_id] = value
        return value

    for node in program.nodes:
        resolve(node.node_id, frozenset())
    return out


def check_resources(program: SIRProgram, envelope: RunEnvelope, *,
                    diagnostics: Diagnostics | None = None) -> Diagnostics:
    out = diagnostics if diagnostics is not None else Diagnostics(pass_name="resources")
    budget = envelope.budget
    counts = multipliers(program)

    for node in program.nodes:
        if node.fan_out is not None and node.fan_out.max_items is None:
            out.emit("RES605",
                     f"node {node.node_id!r} runs once per item of {node.fan_out.source!r} "
                     "and states no bound; the list is produced at run time, so this is an "
                     "unbounded number of calls against a bounded budget",
                     node_id=node.node_id, source=node.fan_out.source)

    unbounded = {nid for nid, n in counts.items() if n == UNBOUNDED}

    tokens = sum(n.estimated_tokens * max(counts.get(n.node_id, 1), 1)
                 for n in program.nodes if n.node_id not in unbounded)
    usd = sum(n.estimated_usd * max(counts.get(n.node_id, 1), 1)
              for n in program.nodes if n.node_id not in unbounded)

    if tokens > budget.tokens_hard:
        out.emit("RES601",
                 f"the program estimates {tokens} tokens (fan-out included) against a hard "
                 f"ceiling of {budget.tokens_hard}", estimate=tokens,
                 ceiling=budget.tokens_hard)
    if usd > budget.usd_hard:
        out.emit("RES602",
                 f"the program estimates ${usd:.2f} (fan-out included) against a hard "
                 f"ceiling of ${budget.usd_hard:.2f}", estimate=round(usd, 4),
                 ceiling=budget.usd_hard)

    critical = _critical_path(program, counts)
    if critical > budget.seconds_hard:
        out.emit("RES603",
                 f"the program's critical path is {critical:.0f}s against a hard ceiling "
                 f"of {budget.seconds_hard:.0f}s", estimate=round(critical, 1),
                 ceiling=budget.seconds_hard)

    per_kind = {ExecKind.MODEL: 0, ExecKind.TOOL: 0, ExecKind.DELEGATE: 0}
    bounded_over = False
    for node in program.executable():
        multiplier = counts.get(node.node_id, 1)
        if multiplier == UNBOUNDED:
            continue
        per_kind[node.kind] = per_kind.get(node.kind, 0) + multiplier
    ceilings = ((ExecKind.MODEL, budget.max_model_calls, "model call"),
                (ExecKind.TOOL, budget.max_tool_calls, "tool call"),
                (ExecKind.DELEGATE, budget.max_delegations, "delegation"))
    for kind, ceiling, what in ceilings:
        needed = per_kind.get(kind, 0)
        if needed <= ceiling:
            continue
        fanned = any(counts.get(n.node_id, 1) > 1 for n in program.executable()
                     if n.kind is kind)
        bounded_over = bounded_over or fanned
        # Two literal emissions rather than one computed code. A code assembled at runtime
        # cannot be found by grep, and ``test_every_emitted_code_is_registered`` reads the
        # source — a rule the registry claims to have and nothing emits is coverage that
        # is not there.
        if fanned:
            out.emit("RES606",
                     f"the program needs {needed} {what}(s) once fan-out is expanded, "
                     f"against a ceiling of {ceiling}",
                     needed=needed, ceiling=ceiling, kind=kind.value)
        else:
            out.emit("RES604",
                     f"the program needs {needed} {what}(s) against a ceiling of {ceiling}",
                     needed=needed, ceiling=ceiling, kind=kind.value)
    return out


def _critical_path(program: SIRProgram, counts: Mapping[str, int]) -> float:
    """The longest dependency chain by estimated seconds, fan-out included.

    The chain rather than the sum: independent branches do not add wall-clock, and
    charging a program for a parallelism it is permitted to have would refuse programs
    that are in fact feasible. A fanned-out node contributes its bound's worth, because
    the loop's parallelism is bounded and cannot be assumed.
    """
    memo: dict[str, float] = {}

    def cost(node_id: str, seen: frozenset[str]) -> float:
        if node_id in seen:
            return 0.0
        if node_id in memo:
            return memo[node_id]
        node = program.node(node_id)
        if node is None:
            return 0.0
        multiplier = counts.get(node_id, 1)
        own = node.estimated_seconds * (1 if multiplier == UNBOUNDED else max(multiplier, 1))
        upstream = max((cost(d, seen | {node_id}) for d in node.dependencies), default=0.0)
        memo[node_id] = upstream + own
        return memo[node_id]

    return max((cost(n.node_id, frozenset()) for n in program.nodes), default=0.0)
