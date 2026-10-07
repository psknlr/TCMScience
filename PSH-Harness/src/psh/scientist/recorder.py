"""Writing an inquiry into the world model as it goes.

An inquiry's trail is enough to recompute it. What the trail does not give a project is
the *relational* record the rest of the scientist plane reads: the hypothesis refuted in
March that ``ScientificWorldModel.briefing`` puts first in June, before anyone proposes it
again; the observation ``why_believe`` walks back from; the protocol an observation was
taken under. ``WorldModelRecorder`` is the inquiry's recorder port, and it writes through
the same doors every other scientific record uses::

    opened    -> a Hypothesis record per named explanation, with ALTERNATIVE_TO edges
    declared  -> a Protocol preregistered against every explanation it predicts for, whose
                 statistical-test field names the sealed predictions' digest; and, when the
                 predictions differ, an experiment node with TESTS edges
    observed  -> an Observation under that protocol, CORROBORATES and REFUTES edges, and
                 one turn of the ScientificCycle, which refuses a turn whose predictions
                 were not sealed first
    admitted  -> Hypothesis records for the late explanations

and an ``inquiry_<event>`` audit event carrying each trail entry's digest.

The catch-all is not a hypothesis anyone proposed, so it gets no record; its probability
is in the trail. A refutation edge means what it means elsewhere in the world model, that
an outcome matched a falsifier: here, an outcome the explanation's sealed prediction gave
probability zero. An observation that only lowered an explanation's probability draws no
edge; its Bayes factor is in the trail.

Write-ahead, like every recorder: the inquiry calls it before its state moves, so a refusal
by the persistence gateway leaves belief where it was. The converse is not guaranteed. A
write that lands before a later write of the same step is refused stays in the graph, so the
world model may hold a record of a step the inquiry did not take, and never the reverse.
"""

from __future__ import annotations

from typing import Any, Mapping

from ..contracts import RunEnvelope, content_hash
from .cycle import ScientificCycle, Stage
from .inquiry import CATCH_ALL, Conclusion, Inquiry
from .models import Hypothesis, Observation, Protocol

__all__ = ["WorldModelRecorder"]

#: One turn of the cycle per observation. The inquiry refuses an observation whose
#: predictions were not sealed (INQ110), and the cycle refuses EXECUTED before
#: PREREGISTERED unless the turn is marked exploratory: two independent statements of the
#: same invariant, so a fault in one is caught by the other.
_TURN = (Stage.HYPOTHESIS, Stage.PREDICTION, Stage.DESIGN, Stage.PREREGISTERED,
         Stage.EXECUTED, Stage.OBSERVED, Stage.ANALYSED, Stage.INTERPRETED,
         Stage.BELIEF_UPDATED, Stage.CLOSED)


class WorldModelRecorder:
    """Records an inquiry in a ``ScientificWorldModel`` under one run's authority."""

    def __init__(self, world: Any, envelope: RunEnvelope, *, population: str) -> None:
        if not isinstance(population, str) or not population.strip():
            raise ValueError("an inquiry's hypotheses need the population they are about")
        self.world = world
        self.ledger = world.ledger
        self.envelope = envelope
        self.population = population
        self.hypotheses: dict[str, str] = {}               # explanation id -> node id
        self.protocols: dict[str, tuple[str, Protocol]] = {}   # analysis id -> first
        self.experiments: dict[str, str] = {}
        self.observations: dict[str, str] = {}
        self.cycles: dict[str, ScientificCycle] = {}
        self._audit = getattr(self.ledger.kernel, "audit", None)

    # -------------------------------------------------------------- the port
    def record(self, inquiry: Inquiry, event: str, entry: Mapping[str, Any]) -> None:
        body = entry["body"]
        handler = getattr(self, f"_on_{event}", None)
        detail: dict[str, Any] = handler(inquiry, body) if handler else {}
        self._emit(f"inquiry_{event}", inquiry, entry, **(detail or {}))

    def close(self, inquiry: Inquiry, conclusion: Conclusion) -> None:
        """Record the conclusion an inquiry reached. Writes no record of its own: the
        verdict is a reading of the trail, and the audit names the trail head it read."""
        body = conclusion.as_dict()
        self._emit("inquiry_concluded", inquiry, {"seq": len(inquiry.trail),
                                                  "digest": body["digest"]},
                   verdict=conclusion.verdict.value, leader=conclusion.leader,
                   claim_kind=body["claim_kind"], licensing=body["licensing"],
                   certainty=body["certainty"], trail_head=conclusion.trail_head,
                   final=conclusion.final)

    # --------------------------------------------------------------- events
    def _on_opened(self, inquiry: Inquiry, body: Mapping[str, Any]) -> dict[str, Any]:
        self._declare(inquiry, body["explanations"], everyone=body["explanations"])
        return {"explanations": len(body["explanations"])}

    def _on_admitted(self, inquiry: Inquiry, body: Mapping[str, Any]) -> dict[str, Any]:
        everyone = [e.as_dict() for e in inquiry.explanations if e.id != CATCH_ALL]
        self._declare(inquiry, body["explanations"], everyone=everyone
                      + list(body["explanations"]))
        return {"explanations": len(body["explanations"])}

    def _on_declared(self, inquiry: Inquiry, body: Mapping[str, Any]) -> dict[str, Any]:
        analysis = inquiry.analysis(body["analysis_id"])
        predicted = list(body["predictions"])
        ids = [self.hypotheses[h] for h in predicted]
        protocol = Protocol(
            primary_endpoint=f"{analysis.title}: one of {', '.join(analysis.outcomes)}",
            secondary_endpoints=(),
            exclusion_criteria="fixed by the analysis's code and parameters",
            statistical_test=("Bayesian update over the sealed predictions "
                              f"sha256:{body['digest']}"),
            sample_size_assumptions=f"{analysis.design.value} design; cost {analysis.cost:g}",
            covariates=(), subgroup_plan="none",
            stopping_criteria=("the inquiry's stopping rule "
                               f"sha256:{content_hash(inquiry.rule.as_dict())}"))
        node = self.ledger.preregister(ids[0], protocol, self.envelope,
                                       also=tuple(ids[1:]))
        self.protocols.setdefault(analysis.id, (node.id, protocol))
        rows = {tuple(sorted(row.items())) for row in body["predictions"].values()}
        if len(rows) >= 2 and analysis.id not in self.experiments:
            experiment = self.world.design(analysis.title, self.envelope, tests=ids,
                                           discriminates=ids)
            self.experiments[analysis.id] = experiment.id
        return {"analysis_id": analysis.id, "protocol": node.id,
                "predictions": body["digest"]}

    def _on_observed(self, inquiry: Inquiry, body: Mapping[str, Any]) -> dict[str, Any]:
        analysis = inquiry.analysis(body["analysis_id"])
        cycle = ScientificCycle(run_id=self.envelope.run_id)
        registered = self.protocols.get(analysis.id)
        for stage in _TURN:
            if stage is Stage.PREREGISTERED and registered is None:
                continue      # nothing named could be predicted: the turn is exploratory
            if stage is Stage.CLOSED:
                break
            cycle.advance(stage, note=f"{analysis.id} step {body['step']}")
        self.cycles[analysis.id] = cycle
        if registered is None:
            cycle.advance(Stage.CLOSED, note="nothing named could be predicted")
            return {"analysis_id": analysis.id, "outcome": body["outcome"],
                    "exploratory": cycle.exploratory, "path": list(cycle.path())}
        protocol_id, protocol = registered
        observation = Observation(
            summary=(f"{analysis.title}: {body['outcome']} (step {body['step']}, "
                     f"{body['purpose']})"),
            artifact_refs=(body["evidence_ref"]
                           or f"inquiry:{inquiry.inquiry_id}#step-{body['step']}",),
            outcome="observed")
        node = self.ledger.observe(protocol_id, observation, protocol, self.envelope)
        self.observations[analysis.id] = node.id
        edges = {"corroborates": 0, "refutes": 0}
        for h, factor in body["bayes_factor"].items():
            if h not in self.hypotheses or h in body["held_fixed"]:
                continue
            note = f"inquiry {inquiry.inquiry_id} step {body['step']}"
            if h in body["refuted"]:
                self.world.refute(node.id, self.hypotheses[h], self.envelope,
                                  note=f"{note}: declared impossible")
                edges["refutes"] += 1
            elif factor is None or factor > 1.0 + 1e-9:
                self.world.corroborate(node.id, self.hypotheses[h], self.envelope,
                                       note=f"{note}: Bayes factor "
                                            f"{'unbounded' if factor is None else f'{factor:.3g}'}")
                edges["corroborates"] += 1
        cycle.advance(Stage.CLOSED, note="belief updated from the sealed predictions")
        return {"analysis_id": analysis.id, "outcome": body["outcome"],
                "observation": node.id, "preregistered": cycle.preregistered,
                "path": list(cycle.path()), **edges}

    # ------------------------------------------------------------- helpers
    def _declare(self, inquiry: Inquiry, explanations: Any, *, everyone: Any) -> None:
        for e in explanations:
            others = tuple(x["proposition"] for x in everyone if x["id"] != e["id"])
            hypothesis = Hypothesis(
                proposition=e["proposition"], population=self.population,
                predictions=(f"sealed per analysis in inquiry {inquiry.inquiry_id}",),
                falsifiers=("an outcome its sealed prediction gives probability zero",),
                alternatives=others + ("none of the named explanations",))
            node = self.world.declare(hypothesis, self.envelope,
                                      alternative_to=list(self.hypotheses.values()))
            self.hypotheses[e["id"]] = node.id

    def _emit(self, event: str, inquiry: Inquiry, entry: Mapping[str, Any],
              **detail: Any) -> None:
        if self._audit is None:
            return
        # Ids and digests only: the propositions are in the records, under the gateway's
        # labels, and an audit chain is read by people who may not hold those labels.
        self._audit(event, run_id=self.envelope.run_id,
                    detail={"inquiry_id": inquiry.inquiry_id, "seq": entry.get("seq"),
                            "digest": entry.get("digest"), **detail})
