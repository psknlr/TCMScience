"""Does the evidence this program produces license the claims it intends to make?

``psh.evidence.scope`` asks this of a finished sentence against a retrieved source. It is
the right check and it happens at the worst possible moment: the analysis has run, the
budget is spent, and the only available remedy is to refuse the output. Asking the same
question of the *program* moves it to the moment where the remedy is cheap — weaken the
claim, change its kind, or add the study that would license it.

The check is graph-reachability plus the licensing matrix. A claim node's evidence is
every ``EvidenceType`` produced anywhere upstream of it, not only at its own ports: a
claim that reads a summary that read a table that read a mouse experiment rests on a
mouse experiment, and each hop is exactly where that fact usually gets lost.

The verdict is the **best** grade any single upstream source reaches, which is the correct
quantifier and worth saying why. A claim supported by a randomised trial is licensed even
though the program also read a blog post; requiring every source to license it would
refuse ordinary, careful work. What the pass does *not* do is add grades together — three
extrapolations do not make a direct one.
"""

from __future__ import annotations

from typing import Iterable

from ..sir import (
    ClaimType, EvidenceType, Licensing, Provenance, Role, SIRNode, SIRProgram, UNEVIDENTIAL,
    licenses,
)
from .diagnostics import Diagnostics, Severity

__all__ = ["check_types", "upstream_evidence"]

_GRADE_ORDER = {Licensing.UNLICENSED: 0, Licensing.EXTRAPOLATED: 1, Licensing.DIRECT: 2}

#: The data kinds a port's ``expected_type`` can be compared against. A port asking for an
#: ``array`` from a node that produces a ``table`` is not an error — the runtime binding
#: resolves a pointer into the value — so only a stated, contradictory pair is reported.
_DATA_KIND_TYPES = {"table": "array", "sequence": "array", "model": "object",
                    "figure": "string", "object": "object"}


def upstream_evidence(program: SIRProgram, node_id: str) -> list[tuple[str, EvidenceType]]:
    """Every piece of evidence reachable upstream of a node, nearest first by order."""
    found: list[tuple[str, EvidenceType]] = []
    for candidate in program.upstream_of(node_id):
        node = program.node(candidate)
        if node is None:
            continue
        evidence = node.produces.evidence
        if evidence is not None:
            found.append((candidate, evidence))
    found.sort(key=lambda pair: pair[0])
    return found


def check_types(program: SIRProgram, *,
                diagnostics: Diagnostics | None = None) -> Diagnostics:
    out = diagnostics if diagnostics is not None else Diagnostics(pass_name="typecheck")

    for node in program.nodes:
        _check_evidence(node, out)
        _check_ports(node, program, out)

    for node in program.by_role(Role.CLAIM):
        _check_claim(node, program, out)
    return out


def _check_evidence(node: SIRNode, out: Diagnostics) -> None:
    evidence = node.produces.evidence
    if evidence is None:
        if node.role is Role.RETRIEVAL:
            out.emit("TYP105",
                     f"node {node.node_id!r} retrieves and states no evidence type; "
                     "without a study design nothing downstream can tell what its result "
                     "licenses", node_id=node.node_id)
        return
    if evidence.retracted is True:
        out.emit("TYP107",
                 f"node {node.node_id!r} produces evidence from "
                 f"{evidence.identifier or 'a retracted source'}, which is retracted",
                 node_id=node.node_id, identifier=evidence.identifier)
    if evidence.provenance in UNEVIDENTIAL:
        out.emit("TYP104",
                 f"node {node.node_id!r} produces evidence whose provenance is "
                 f"{evidence.provenance.value}: "
                 + ("a value this system generated cannot be evidence for this system's "
                    "own conclusion" if evidence.provenance is Provenance.GENERATED
                    else "a source nobody can attribute is not a source"),
                 node_id=node.node_id, provenance=evidence.provenance.value)


def _check_ports(node: SIRNode, program: SIRProgram, out: Diagnostics) -> None:
    for port in node.inputs:
        source = program.node(port.source)
        if source is None or not port.expected_type:
            continue
        produced = source.produces.data
        if produced is None or not produced.kind:
            continue
        # Only a *whole result* binding can be compared. A pointer reaches inside the
        # value, and what is inside a table is not a table.
        if port.pointer:
            continue
        expected = _DATA_KIND_TYPES.get(produced.kind)
        if expected is not None and port.expected_type != expected \
                and port.cardinality == "one":
            out.emit("TYP108",
                     f"node {node.node_id!r} expects {port.expected_type!r} for "
                     f"{port.argument!r} and {port.source!r} produces a "
                     f"{produced.kind} ({expected})", node_id=node.node_id,
                     argument=port.argument)


def _check_claim(node: SIRNode, program: SIRProgram, out: Diagnostics) -> None:
    claim: ClaimType | None = node.produces.claim
    if claim is None:
        out.emit("TYP101",
                 f"node {node.node_id!r} has role claim and states no claim type; what it "
                 "asserts, about whom and how firmly is what decides which evidence could "
                 "license it", node_id=node.node_id)
        return

    evidence = upstream_evidence(program, node.node_id)
    if not evidence:
        out.emit("TYP106",
                 f"claim {node.node_id!r} ({claim.describe()}) reads no evidence: nothing "
                 "upstream of it produces a source", node_id=node.node_id)
        return

    best_grade = Licensing.UNLICENSED
    best_source = ""
    best_reasons: tuple[str, ...] = ()
    per_source: list[str] = []
    for source_id, source_type in evidence:
        verdict = licenses(source_type, claim)
        per_source.append(f"{source_id} ({source_type.design.value}): "
                          f"{verdict.grade.value}")
        if _GRADE_ORDER[verdict.grade] > _GRADE_ORDER[best_grade]:
            best_grade, best_source, best_reasons = verdict.grade, source_id, verdict.reasons

    if best_grade is Licensing.UNLICENSED:
        # Report the reasons of the nearest source rather than all of them: the author has
        # to change one thing, and a list of every source's objection buries which.
        nearest_id, nearest_type = evidence[0]
        reasons = licenses(nearest_type, claim).reasons
        out.emit("TYP102",
                 f"claim {node.node_id!r} asserts {claim.describe()} and no source "
                 f"upstream of it licenses that: "
                 + ("; ".join(reasons[:2]) if reasons else "; ".join(per_source[:3])),
                 node_id=node.node_id, sources=per_source[:6], nearest=nearest_id)
        return

    if best_grade is Licensing.EXTRAPOLATED:
        declared = bool(node.claim is not None and node.claim.extrapolation_declared)
        out.emit("TYP103",
                 f"claim {node.node_id!r} reaches beyond {best_source!r}: "
                 + "; ".join(best_reasons[:2]),
                 node_id=node.node_id,
                 severity=(Severity.WARNING if declared else Severity.ERROR),
                 remedy=("recorded as a limitation of the release" if declared else
                         "set claim.extrapolation_declared to record the stretch, or "
                         "narrow the claim"),
                 source=best_source, declared=declared)
