"""The release check: a candidate claim leaves only if the snapshots carry it.

A skill's candidate claim names its kind, its subject and object, and the edges it rests
on (snapshot id + source record id). Before release it must pass four checks:

1. **the edges exist** in snapshots that were loaded and verified (by hash and, with a
   ledger, against the recorded id) — a claim cannot cite an edge nobody can find;
2. **the edges connect** the claim's subject to its object *along their direction*, so
   the evidence is about this claim and not merely present. An undirected search let a
   claim about ``protein:B → compound:A`` be released on an edge recorded as
   ``compound:A targets protein:B`` (audit F04);
2a. **the statement says no more than the edges.** A statement that names a direction of
   effect ("activates", "inhibits", 抑制 …) needs an evidential edge that records that
   direction; a path through a ``tested_against`` edge (measured and found *inactive*)
   supports no effect at all; and wording that asserts efficacy, certainty or a
   universal population is refused as it is for any claim
   (``contracts.claim_language``);
3. **the kind is within the skill's ceiling** (``SkillContract.permits``);
4. **the evidence licenses the kind, by its weakest link.** Each evidential edge licenses
   the claim kinds its study design admits (``tcm.CLAIM_SUPPORT``); a path licenses only
   the kinds every one of its evidential edges licenses. Composition edges license no
   claim themselves, but one below C3 — a compound reported in the species or the crude
   drug, not shown in the preparation actually given — caps the path at
   ``mechanism_hypothesis``: an in-vitro activity of a compound the decoction may not
   contain is a reason to look, not a mechanism of the herb.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from ..tcm.model import CLAIM_SUPPORT
from .schema import STUDY_DESIGNS
from .snapshot import Snapshot

__all__ = ["CandidateClaim", "ReleaseVerdict", "ReleaseRefused", "check_release",
           "require_release", "path_licenses"]

#: Composition levels at which the compound is shown in what is actually administered.
_IN_PREPARATION = frozenset({"C3", "C4"})

#: Edges that say what a thing *is* (a herb's source species, a formula's herbs, a
#: processed form's crude drug), not what it does. They connect a path but carry no
#: evidence about the effect, so they neither license nor limit the claim.
_DEFINITIONAL = frozenset({"has_base_species", "processed_from", "recorded_in",
                           # pathway membership: what a protein takes part in, not what a
                           # compound does to it
                           "participates_in"})


def _definitional(edge: Mapping[str, Any]) -> bool:
    predicate = edge.get("predicate")
    return predicate in _DEFINITIONAL or (predicate == "contains"
                                          and not edge.get("composition_level"))


class ReleaseRefused(RuntimeError):
    def __init__(self, message: str, verdict: "ReleaseVerdict") -> None:
        super().__init__(message)
        self.verdict = verdict


@dataclass(frozen=True)
class CandidateClaim:
    kind: str
    subject: str
    object: str
    support: tuple[tuple[str, str], ...]         # (snapshot id, source record id)
    statement: str = ""

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "CandidateClaim":
        return cls(kind=str(data["kind"]), subject=str(data["subject"]),
                   object=str(data["object"]),
                   support=tuple((str(s), str(r)) for s, r in data.get("support") or ()),
                   statement=str(data.get("statement") or ""))


@dataclass
class ReleaseVerdict:
    released: list[CandidateClaim] = field(default_factory=list)
    refused: list[tuple[CandidateClaim, str]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.refused

    def as_dict(self) -> dict[str, Any]:
        return {"released": [c.__dict__ for c in self.released],
                "refused": [{"claim": c.__dict__, "reason": r} for c, r in self.refused]}


def path_licenses(edges: Sequence[Mapping[str, Any]]) -> frozenset[str]:
    """Claim kinds a set of supporting edges licenses together, by its weakest link."""
    licensed: set[str] | None = None
    for e in edges:
        tier = STUDY_DESIGNS.get(e.get("study_design"))
        if tier is None or _definitional(e):
            continue                          # composition or identity: no claim of its own
        kinds = {k for k, admitted in CLAIM_SUPPORT.items() if tier in admitted}
        licensed = kinds if licensed is None else licensed & kinds
    if licensed is None:
        return frozenset()
    if any(e.get("predicate") == "contains" and e.get("composition_level")
           and e.get("composition_level") not in _IN_PREPARATION for e in edges):
        licensed &= {"mechanism_hypothesis"}
    return frozenset(licensed)


#: Effect direction a statement may name → the edge facts that record it. Edge
#: predicates are mostly direction-free ("targets" says a compound acts on a protein,
#: not how), so a direction must come from the edge's own ``direction``/``effect``
#: field or from one of the two predicates that carry one.
_DIRECTION_TERMS: tuple[tuple[str, str], ...] = (
    ("increase", r"\b(?:activat\w*|agonis\w*|up-?regulat\w*|induc\w*|enhanc\w*|"
                 r"stimulat\w*|potentiat\w*|increas\w*)\b|激活|上调|促进|增强|诱导"),
    ("decrease", r"\b(?:inhibit\w*|antagonis\w*|antagoniz\w*|down-?regulat\w*|"
                 r"suppress\w*|block\w*|decreas\w*|reduc\w*)\b|抑制|下调|阻断|拮抗|降低"),
)
_PREDICATE_DIRECTION = {"potentiates": "increase", "antagonises": "decrease"}
_DIRECTION_WORDS = {"increase": {"increase", "activation", "activates", "up", "agonist",
                                 "positive", "induces", "upregulates"},
                    "decrease": {"decrease", "inhibition", "inhibits", "down", "antagonist",
                                 "negative", "suppresses", "downregulates"}}


def _stated_directions(statement: str) -> set[str]:
    import re
    return {d for d, pattern in _DIRECTION_TERMS
            if re.search(pattern, statement, re.IGNORECASE)}


def _edge_direction(edge: Mapping[str, Any]) -> str:
    if edge.get("predicate") in _PREDICATE_DIRECTION:
        return _PREDICATE_DIRECTION[edge["predicate"]]
    for key in ("direction", "effect_direction", "effect"):
        value = str(edge.get(key) or "").strip().lower()
        for direction, words in _DIRECTION_WORDS.items():
            if value in words:
                return direction
    return ""


def _statement_problem(claim: "CandidateClaim",
                       edges: Sequence[Mapping[str, Any]]) -> str:
    """Why the statement says more than its edges, or '' when it does not."""
    inactive = [e for e in edges if e.get("predicate") == "tested_against"]
    if inactive:
        return (f"the path runs through {len(inactive)} tested_against edge(s) — measured "
                "and found inactive — which support no effect")
    if not claim.statement:
        return ""
    from ..contracts.claim_language import overreaching_language
    findings = overreaching_language(claim.statement, claim.kind)
    if findings:
        f = findings[0]
        return (f"the statement uses {f.family} language ({f.phrase!r}) that a "
                f"{claim.kind} claim does not license")
    stated = _stated_directions(claim.statement)
    if stated:
        recorded = {_edge_direction(e) for e in edges if not _definitional(e)} - {""}
        unsupported = stated - recorded
        if unsupported:
            return (f"the statement asserts a direction of effect ({', '.join(sorted(unsupported))}) "
                    f"that no supporting edge records (edges record "
                    f"{sorted(recorded) or 'no direction'})")
    return ""


def _connected(subject: str, obj: str, edges: Iterable[Mapping[str, Any]]) -> bool:
    """Whether a *directed* path runs from ``subject`` to ``obj``.

    Edges are read in the direction they were recorded. "A targets B" is evidence
    about A acting on B; it is not a path from B to A.
    """
    graph: dict[str, set[str]] = defaultdict(set)
    for e in edges:
        graph[e["subject"]].add(e["object"])
    seen, frontier = {subject}, [subject]
    while frontier:
        node = frontier.pop()
        if node == obj:
            return True
        for nxt in graph[node] - seen:
            seen.add(nxt)
            frontier.append(nxt)
    return False


def check_release(claims: Iterable[CandidateClaim | Mapping[str, Any]],
                  snapshots: Iterable[Snapshot], *, contract: Any = None) -> ReleaseVerdict:
    by_id: dict[str, dict[str, list[Mapping[str, Any]]]] = {}
    for snap in snapshots:
        index: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
        for e in snap.edges:
            index[str(e.get("source_record_id"))].append(e)
        by_id[snap.snapshot_id] = index
    verdict = ReleaseVerdict()
    for raw in claims:
        claim = raw if isinstance(raw, CandidateClaim) else CandidateClaim.from_mapping(raw)

        def refuse(reason: str) -> None:
            verdict.refused.append((claim, reason))

        if claim.kind not in CLAIM_SUPPORT:
            refuse(f"unknown claim kind {claim.kind!r}")
            continue
        if contract is not None and not contract.permits(claim.kind):
            refuse(f"skill {contract.id} may not emit {claim.kind} claims "
                   f"(ceiling {contract.max_claim_kind})")
            continue
        if not claim.support:
            refuse("the claim cites no supporting edge")
            continue
        edges: list[Mapping[str, Any]] = []
        missing = []
        for snapshot_id, record in claim.support:
            found = by_id.get(snapshot_id, {}).get(record)
            if not found:
                missing.append(f"{snapshot_id}/{record}")
            edges.extend(found or ())
        if missing:
            refuse("supporting edges not found in the loaded snapshots: "
                   + ", ".join(missing[:5]))
            continue
        if not _connected(claim.subject, claim.object, edges):
            refuse(f"the supporting edges do not connect {claim.subject} to {claim.object} "
                   "in the direction they were recorded")
            continue
        problem = _statement_problem(claim, edges)
        if problem:
            refuse(problem)
            continue
        licensed = path_licenses(edges)
        if claim.kind not in licensed:
            refuse(f"the evidence licenses {sorted(licensed) or 'no claim'}, "
                   f"not {claim.kind}")
            continue
        verdict.released.append(claim)
    return verdict


def require_release(claims: Iterable[CandidateClaim | Mapping[str, Any]],
                    snapshots: Iterable[Snapshot], *, contract: Any = None) -> ReleaseVerdict:
    verdict = check_release(claims, snapshots, contract=contract)
    if not verdict.ok:
        raise ReleaseRefused(f"{len(verdict.refused)} claim(s) refused: " + "; ".join(
            reason for _, reason in verdict.refused[:3]), verdict)
    return verdict
