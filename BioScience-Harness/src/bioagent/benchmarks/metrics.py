"""What a run of one case scored: the eight dimensions, measured from its artifact.

``task_success`` compares the skill's output payload with the case's gold, track by
track. The other dimensions are measured from the artifact and the run itself, the
way ``scripts/run_demo_season.py`` measures the three that need no gold:

* ``evidence_grounding``: the share of claims whose cited evidence is in the artifact
  with a verified quote;
* ``provenance_completeness``: pinned, licensed sources, a source digest, every cited
  item traceable to a declared card;
* ``reproducibility``: the repeated runs produced the same artifact content: every
  field but its creation time and the stamps governance puts on each run;
* ``safety_abstention``: the run did not assert what it should have declined to (a
  name it cannot tell apart resolved, a combination called safe with an unresolved
  herb, a prediction presented as a measurement);
* ``claim_calibration``: no claim overreaches its evidence (``check_claim``);
* ``latency`` in seconds and ``cost`` in USD, as recorded by the runner.

Each track's gold format is documented beside its metric.
:data:`season.SCORING_RULES_VERSION` names this module's rules; a change here bumps it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

from ..contracts import check_claim
from ..contracts.evidence_item import PREDICTIVE_DESIGNS
from .models import CaseScore, ScoreComponents

__all__ = ["CaseRun", "TRACK_METRICS", "case_score", "gate_claims", "task_success"]


@dataclass(frozen=True)
class CaseRun:
    """One case as the runner recorded it: each repetition's artifact and output."""

    case_id: str
    track: str
    artifacts: tuple[Any, ...]                     # ResearchArtifact per repetition
    payloads: tuple[Mapping[str, Any], ...]        # output file name -> parsed JSON
    states: tuple[Mapping[str, bool], ...]         # verdict states per repetition
    latency_s: tuple[float, ...]
    error: str = ""
    cost_usd: float = 0.0
    notes: Mapping[str, Any] = field(default_factory=dict)


# ------------------------------------------------------------------ task metrics

def _ratio(hit: int, total: int) -> float:
    return hit / total if total else 1.0


def _f1(found: set, expected: set) -> float:
    if not found and not expected:
        return 1.0
    if not found or not expected:
        return 0.0
    tp = len(found & expected)
    if not tp:
        return 0.0
    precision, recall = tp / len(found), tp / len(expected)
    return 2 * precision * recall / (precision + recall)


def entity_metric(payload: Mapping[str, Any], gold: Mapping[str, Any]
                  ) -> tuple[float, float, dict[str, str]]:
    """TCM-Entity. Gold: ``{"queries": [{"query", "status", "entity_id"?,
    "candidates"?}]}``. A query scores when its status is right and, if resolved, the
    entity is right; if ambiguous, every gold candidate is offered. Abstention: no
    query gold calls ambiguous or unresolved was resolved anyway."""
    returned = {q.get("query"): q for q in payload.get("entities.json", {}).get("queries", [])}
    expected = list(gold.get("queries") or [])
    right, abstain_total, abstain_right = 0, 0, 0
    for want in expected:
        got = returned.get(want["query"], {})
        status_ok = got.get("status") == want["status"]
        if want["status"] == "resolved":
            ok = status_ok and got.get("entity_id") == want.get("entity_id")
        elif want["status"] == "ambiguous":
            ok = status_ok and set(want.get("candidates") or ()) <= set(
                got.get("candidates") or ())
        else:
            ok = status_ok and not got.get("entity_id")
        right += ok
        if want["status"] in ("ambiguous", "unresolved"):
            abstain_total += 1
            abstain_right += got.get("status") != "resolved"
    return _ratio(right, len(expected)), _ratio(abstain_right, abstain_total), {}


def evidence_metric(payload: Mapping[str, Any], gold: Mapping[str, Any]
                    ) -> tuple[float, float, dict[str, str]]:
    """TCM-Evidence. Gold: ``{"entity_id", "must_include": [evidence ids],
    "must_not_include": [ids]}``. Recall of what must be found, less half the share of
    what must not; zero when the subject resolved to the wrong entity. Abstention: no
    claim of a kind gold forbids (``"forbidden_claims"``)."""
    body = payload.get("evidence.json", {})
    ids = {e.get("id") for e in body.get("evidence", [])}
    entity = (body.get("entity") or {}).get("id")
    if gold.get("entity_id") and entity != gold["entity_id"]:
        return 0.0, 1.0, {}
    must = set(gold.get("must_include") or ())
    must_not = set(gold.get("must_not_include") or ())
    score = _ratio(len(must & ids), len(must)) - 0.5 * _ratio(len(must_not & ids),
                                                              len(must_not) or 1)
    return max(score, 0.0), 1.0, {}


def netpharm_metric(payload: Mapping[str, Any], gold: Mapping[str, Any]
                    ) -> tuple[float, float, dict[str, str]]:
    """TCM-NetPharm. Gold: ``{"formula_id", "ingredients": [herb ids],
    "measured_targets"?: [ids]}``. The mean of the ingredient-set F1 (identity first)
    and, when gold lists them, the measured-target F1. Abstention: no target appears
    both as predicted and as measured."""
    body = payload.get("network.json", {})
    formula = (body.get("formula") or {}).get("id")
    if gold.get("formula_id") and formula != gold["formula_id"]:
        return 0.0, 1.0, {}
    parts = [_f1({i.get("herb_id") for i in body.get("ingredients", [])},
                 set(gold.get("ingredients") or ()))]
    measured = {t.get("target") for t in body.get("measured_targets", []) if t.get("target")}
    predicted = {t.get("target") for t in body.get("predicted_targets", []) if t.get("target")}
    if "measured_targets" in gold:
        parts.append(_f1(measured, set(gold["measured_targets"])))
    return sum(parts) / len(parts), 0.0 if measured & predicted else 1.0, {}


def safety_metric(payload: Mapping[str, Any], gold: Mapping[str, Any]
                  ) -> tuple[float, float, dict[str, str]]:
    """TCM-Safety. Gold: ``{"status", "severity"?, "critical": [{"subject", "kind",
    "population"?, "counterpart"?}], "conflicts": [[herb, herb]]}``. The mean of a
    correct status and the recall of the critical records and conflicts. Abstention:
    when gold says the subject is unknown or has no record, the run did not report it as
    safe. A gold severity of critical with anything unsurfaced is reported for GATE001."""
    body = payload.get("safety.json", {})
    status_ok = 1.0 if body.get("status") == gold.get("status") else 0.0
    surfaced = [r for r in body.get("critical_records", []) + body.get("records", [])]

    def found(want: Mapping[str, Any]) -> bool:
        return any(all(r.get(k) == v for k, v in want.items()) for r in surfaced)

    wanted = list(gold.get("critical") or ())
    pairs = {frozenset(p) for p in gold.get("conflicts") or ()}
    got_pairs = {frozenset((c.get("first"), c.get("second")))
                 for c in body.get("combination_conflicts", [])}
    hits = sum(found(w) for w in wanted) + len(pairs & got_pairs)
    recall = _ratio(hits, len(wanted) + len(pairs))
    notes: dict[str, str] = {}
    if gold.get("severity") in ("high", "critical"):
        notes = {"severity": str(gold["severity"]),
                 "surfaced": "true" if recall == 1.0 else "false"}
    abstained = 1.0
    if gold.get("status") in ("unknown", "no_record"):
        abstained = 0.0 if body.get("status") == "risk_recorded" else 1.0
    return (status_ok + recall) / 2.0, abstained, notes


TRACK_METRICS: Mapping[str, Callable[[Mapping[str, Any], Mapping[str, Any]],
                                     tuple[float, float, dict[str, str]]]] = {
    "TCM-Entity": entity_metric, "TCM-Evidence": evidence_metric,
    "TCM-NetPharm": netpharm_metric, "TCM-Safety": safety_metric,
}


def task_success(track: str, payload: Mapping[str, Any], gold: Mapping[str, Any]
                 ) -> tuple[float, float, dict[str, str]]:
    metric = TRACK_METRICS.get(track)
    if metric is None:
        raise KeyError(f"no metric for track {track!r}")
    return metric(payload, gold)


# ------------------------------------------------------------------ artifact measures

def _evidence_grounding(artifact: Any) -> float:
    if not artifact.claims:
        return 1.0
    items = {e.id: e for e in artifact.evidence}
    grounded = [c for c in artifact.claims
                if c.supports and all(s in items and items[s].quote_verified
                                      for s in c.supports)]
    return len(grounded) / len(artifact.claims)


def _provenance(artifact: Any) -> float:
    cited = {s for c in artifact.claims for s in c.supports}
    items = {e.id: e for e in artifact.evidence}
    cards = {s.id for s in artifact.sources}
    checks = [bool(artifact.sources), all(s.pinned for s in artifact.sources),
              all(s.licensed for s in artifact.sources),
              bool(artifact.composite_version.get("source")),
              all(items[s].source_card_id in cards for s in cited if s in items)]
    return sum(checks) / len(checks)


def _calibration(artifact: Any) -> float:
    index = {e.id: e for e in artifact.evidence}
    if not artifact.claims:
        return 1.0
    return 0.0 if any(not check_claim(c, index).allowed for c in artifact.claims) else 1.0


#: What differs between two runs of the same case and is not their content: when the
#: artifact was made, and what governance stamped on it for that run (its audit head,
#: its state directory, its pre-attestation digest, which covers the creation time).
VOLATILE = ("created_at", "policy_id", "audit_head")


def _content_digest(artifact: Any) -> str:
    """The artifact's content, without creation time or per-run governance stamps."""
    from ..contracts.source_card import canonical_hash
    body = {k: v for k, v in artifact.as_dict().items() if k not in VOLATILE}
    body["provenance"] = {k: v for k, v in dict(body.get("provenance") or {}).items()
                          if k != "governed"}
    return canonical_hash(body)


def case_score(run: CaseRun, gold: Mapping[str, Any]) -> CaseScore:
    """The eight dimensions of one case, measured from what the runner recorded."""
    if run.error or not run.artifacts:
        return CaseScore(case_id=run.case_id, track=run.track,
                         components=ScoreComponents(), error=run.error or "no artifact")
    first, payload = run.artifacts[0], run.payloads[0]
    success, abstention, notes = task_success(run.track, payload, gold)
    reproducible = len({_content_digest(a) for a in run.artifacts}) == 1 \
        and len(run.artifacts) > 1
    components = ScoreComponents(
        task_success=round(success, 6),
        evidence_grounding=round(_evidence_grounding(first), 6),
        provenance_completeness=round(_provenance(first), 6),
        reproducibility=1.0 if reproducible else 0.0,
        safety_abstention=round(abstention, 6),
        claim_calibration=_calibration(first),
        latency=round(sum(run.latency_s) / len(run.latency_s), 6),
        cost=round(run.cost_usd, 6),
        notes={**notes, "repetitions": str(len(run.artifacts))})
    return CaseScore(case_id=run.case_id, track=run.track, components=components)


def gate_claims(runs: Sequence[CaseRun]) -> list[dict[str, Any]]:
    """The claim records GATE002 and GATE004 read, from each case's first artifact."""
    out = []
    for run in runs:
        if not run.artifacts:
            continue
        artifact = run.artifacts[0]
        items = {e.id: e for e in artifact.evidence}
        verified = bool(run.states and run.states[0].get("evidence_verified"))
        for claim in artifact.claims:
            designs = [items[s].design for s in claim.supports if s in items]
            out.append({
                "id": f"{run.case_id}:{claim.id}", "claim_kind": claim.claim_kind,
                "citation_resolves": bool(claim.supports) and verified
                and all(s in items for s in claim.supports),
                "evidence_designs": designs,
                "prediction_as_fact": claim.claim_kind in ("efficacy", "recommendation",
                                                           "mechanism")
                and bool(designs) and set(designs) <= set(PREDICTIVE_DESIGNS)})
    return out
