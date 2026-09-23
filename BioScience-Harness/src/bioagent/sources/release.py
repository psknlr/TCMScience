"""The release check: a candidate claim leaves only if the snapshots carry it.

A skill's candidate claim names its kind, its subject and object, and the edges it rests
on (snapshot id + source record id). Before release it must pass four checks:

1. **the edges exist** in snapshots that were loaded and verified (by hash and, with a
   ledger, against the recorded id) — a claim cannot cite an edge nobody can find;
2. **the edges connect** the claim's subject to its object, so the evidence is about
   this claim and not merely present;
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


def _connected(subject: str, obj: str, edges: Iterable[Mapping[str, Any]]) -> bool:
    graph: dict[str, set[str]] = defaultdict(set)
    for e in edges:
        graph[e["subject"]].add(e["object"])
        graph[e["object"]].add(e["subject"])
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
            refuse(f"the supporting edges do not connect {claim.subject} to {claim.object}")
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
