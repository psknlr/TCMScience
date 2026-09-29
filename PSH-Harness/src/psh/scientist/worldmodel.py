"""The relations between scientific records: what competes, and what bears on what.

``ScientificLedger`` writes the records — a hypothesis, the protocol preregistered against
it, the observation that came back — as immutable, content-hashed nodes under the
persistence gateway. That is the archival half, and it is complete: each record links to
what it derives from, so a protocol's provenance is answerable.

What it does not hold is the *relational* half, and that is what turns a pile of records
into something a project can be asked a question about:

```
    Hypothesis <──alternative_to──> Hypothesis
        ^                               ^
        ├──corroborates── Observation ──┘
        └──refutes────────┘
```

Three things follow only from the relations:

* **What else could explain this?** ``Hypothesis.alternatives`` names competitors as text
  inside the record, which answers the question from one end only. An edge answers it from
  either, which is what a reader arriving at the *other* hypothesis needs.
* **Where does this hypothesis stand?** Corroboration and refutation are edges an
  observation draws, so standing is computed from the graph rather than remembered as
  somebody's opinion — and a refuted hypothesis *stays*, so nothing proposes it again next
  month.
* **Why do we believe it?** The walk runs backwards through the observation, its protocol
  and the hypothesis that protocol was registered against.

This module writes **no records**. Every node is created by the ledger, so the gateway
classifies it and the content hash covers it; the only things written here are edges, which
carry no content of their own. A structural test keeps it that way.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Mapping, Sequence

from ..contracts import PolicyDenied, RunEnvelope
from ..workgraph import EdgeKind, Node, NodeKind
from .models import Hypothesis, Observation, Protocol

__all__ = ["HypothesisStatus", "BeliefState", "ScientificWorldModel", "WorldModelRefused",
           "bayes_update"]


class HypothesisStatus(str, Enum):
    """Where a hypothesis stands. Derived from edges, never stored as an opinion."""

    PROPOSED = "proposed"            # recorded; no observation bears on it yet
    UNDER_TEST = "under_test"        # an experiment names it, nothing has come back
    CORROBORATED = "corroborated"    # observations matched and none refuted
    REFUTED = "refuted"              # an observation matched a falsifier
    CONTESTED = "contested"          # both, which is a state and not an error


class WorldModelRefused(PolicyDenied):
    """A world-model operation was refused, usually by the ledger or the gateway."""


@dataclass(frozen=True, slots=True)
class BeliefState:
    """What the graph says about one hypothesis, with the counts behind it.

    There is no posterior here, and its absence is deliberate. Turning "two corroborating
    observations and one refutation" into 0.63 needs likelihood ratios nobody has, and the
    number would then be quoted without them. ``bayes_update`` is available for a caller
    who *does* have one and wants to say so.
    """

    hypothesis_id: str
    proposition: str
    status: HypothesisStatus
    corroborations: int = 0
    refutations: int = 0
    negative_observations: int = 0
    predictions: tuple[str, ...] = ()
    falsifiers: tuple[str, ...] = ()
    alternatives: tuple[str, ...] = ()
    experiments: tuple[str, ...] = ()

    @property
    def tested(self) -> bool:
        return bool(self.corroborations or self.refutations)

    def describe(self) -> str:
        return (f"{self.hypothesis_id}: {self.status.value} "
                f"(+{self.corroborations}/-{self.refutations}, "
                f"{len(self.alternatives)} alternative(s) on record)")

    def as_dict(self) -> dict[str, Any]:
        return {"hypothesis_id": self.hypothesis_id, "proposition": self.proposition,
                "status": self.status.value, "corroborations": self.corroborations,
                "refutations": self.refutations,
                "negative_observations": self.negative_observations,
                "predictions": list(self.predictions),
                "falsifiers": list(self.falsifiers),
                "alternatives": list(self.alternatives),
                "experiments": list(self.experiments)}


class ScientificWorldModel:
    """Relations over a ``ScientificLedger``'s records, for one project."""

    def __init__(self, ledger: Any) -> None:
        #: A ``psh.scientist.records.ScientificLedger``. Taken rather than constructed so
        #: this class cannot be handed a kernel and quietly start writing records itself.
        self.ledger = ledger
        self.graph = ledger.graph
        self.project_id = ledger.project_id
        self._audit = getattr(ledger.kernel, "audit", None)

    # ------------------------------------------------------------------ writes
    def declare(self, hypothesis: Hypothesis, envelope: RunEnvelope, *,
                alternative_to: Sequence[str] = (), label: Any = None) -> Node:
        """Record a hypothesis through the ledger, and draw its competition edges.

        ``Hypothesis`` already requires predictions, falsifiers and alternatives to be
        non-empty, so a hypothesis nothing could refute cannot be constructed. What this
        adds is the *edges* to the competing hypotheses that are already recorded, so the
        relation is traversable from both ends.
        """
        try:
            node = self.ledger.hypothesize(hypothesis, envelope, label=label)
        except (PolicyDenied, ValueError) as exc:
            raise WorldModelRefused(f"the ledger refused a hypothesis: {exc}") from exc
        for other in alternative_to:
            competitor = self.graph.get(other)
            if competitor is None or competitor.kind is not NodeKind.HYPOTHESIS:
                raise WorldModelRefused(
                    f"{other!r} is not a recorded hypothesis in this project")
            self.graph.link(node.id, other, EdgeKind.ALTERNATIVE_TO)
            self.graph.link(other, node.id, EdgeKind.ALTERNATIVE_TO)
        self._record("hypothesis_declared", envelope, alternatives=len(alternative_to))
        return node

    def design(self, title: str, envelope: RunEnvelope, *,
               tests: Sequence[str] = (), discriminates: Sequence[str] = ()) -> Node:
        """Record an experiment and what it is meant to tell apart.

        ``discriminates`` is checked rather than stored as prose: an experiment naming
        fewer than two hypotheses cannot decide between them, and saying so here is
        cheaper than discovering it from the result.
        """
        if len(set(discriminates)) < 2:
            raise WorldModelRefused(
                f"experiment {title[:60]!r} discriminates between "
                f"{len(set(discriminates))} hypothesis(es): its result would be compatible "
                "with the alternatives, so it cannot decide between them")
        unknown = [h for h in set(tests) | set(discriminates)
                   if self.graph.get(h) is None]
        if unknown:
            raise WorldModelRefused(
                f"experiment names {sorted(unknown)}, which are not recorded hypotheses")
        node = self._experiment(title, envelope)
        for hypothesis_id in dict.fromkeys(tests):
            self.graph.link(node.id, hypothesis_id, EdgeKind.TESTS)
        self._record("experiment_designed", envelope, tests=len(set(tests)))
        return node

    def _experiment(self, title: str, envelope: RunEnvelope) -> Node:
        """Write the experiment node through the persistence gateway.

        The ledger has no ``record_experiment`` of its own — an experiment is a plan, not
        an immutable finding — so this is the one node kind written here, and it goes
        through the same gateway every other write does.
        """
        try:
            return self.ledger.kernel.persistence.commit_node(
                kind=NodeKind.EXPERIMENT, title=title[:140],
                principal=envelope.principal.id, source_run=envelope.run_id,
                project_id=self.project_id, validation_status="candidate",
                max_label=envelope.max_label.sensitivity)
        except PolicyDenied as exc:
            raise WorldModelRefused(
                f"the persistence gateway refused an experiment node: {exc}") from exc

    def corroborate(self, observation_id: str, hypothesis_id: str,
                    envelope: RunEnvelope, *, note: str = "") -> None:
        self._relate(observation_id, hypothesis_id, EdgeKind.CORROBORATES, envelope, note)

    def refute(self, observation_id: str, hypothesis_id: str, envelope: RunEnvelope, *,
               note: str = "") -> None:
        """Record that an observation matched a falsifier.

        Refuting does not delete the hypothesis. A refuted hypothesis that stays in the
        graph is how the project remembers what it already ruled out — and a system that
        forgets will propose it again next month.
        """
        self._relate(observation_id, hypothesis_id, EdgeKind.REFUTES, envelope, note)

    def _relate(self, observation_id: str, hypothesis_id: str, kind: EdgeKind,
                envelope: RunEnvelope, note: str) -> None:
        observation = self.graph.get(observation_id)
        hypothesis = self.graph.get(hypothesis_id)
        for node, expected, name in ((observation, NodeKind.OBSERVATION, "observation"),
                                     (hypothesis, NodeKind.HYPOTHESIS, "hypothesis")):
            if node is None or node.kind is not expected \
                    or node.project_id != self.project_id:
                raise WorldModelRefused(
                    f"missing, wrong-kind or cross-project {name} reference")
        self.graph.link(observation_id, hypothesis_id, kind, note=note[:160])
        self._record(f"hypothesis_{kind.value}", envelope)

    # ------------------------------------------------------------------- reads
    def hypotheses(self) -> list[Node]:
        return self.graph.nodes(kind=NodeKind.HYPOTHESIS, project_id=self.project_id)

    def competing(self, hypothesis_id: str) -> list[Node]:
        return [node for _, node in self.graph.neighbours(
            hypothesis_id, kind=EdgeKind.ALTERNATIVE_TO, direction="out")]

    def belief(self, hypothesis_id: str, envelope: RunEnvelope) -> BeliefState:
        """The standing of one hypothesis, computed from edges rather than remembered.

        The envelope is required because reading the record goes through the ledger, which
        enforces the run's authority and verifies the content hash — a belief state built
        from a record nobody re-verified would be a claim about a node, not about the
        science it records.
        """
        node = self.graph.get(hypothesis_id)
        if node is None or node.kind is not NodeKind.HYPOTHESIS:
            raise WorldModelRefused(f"{hypothesis_id!r} is not a hypothesis in this graph")
        try:
            body = self.ledger.read(hypothesis_id, envelope)
        except (PolicyDenied, ValueError) as exc:
            raise WorldModelRefused(f"the hypothesis record is unreadable: {exc}") from exc
        record = body.get("record") or {}

        corroborating = self.graph.neighbours(hypothesis_id, kind=EdgeKind.CORROBORATES,
                                              direction="in")
        refuting = self.graph.neighbours(hypothesis_id, kind=EdgeKind.REFUTES,
                                         direction="in")
        experiments = [n.id for _, n in self.graph.neighbours(
            hypothesis_id, kind=EdgeKind.TESTS, direction="in")]

        negatives = 0
        for _, observation in corroborating + refuting:
            try:
                outcome = (self.ledger.read(observation.id, envelope).get("record") or {})
            except (PolicyDenied, ValueError):
                continue
            if outcome.get("outcome") == "negative":
                negatives += 1

        if corroborating and refuting:
            status = HypothesisStatus.CONTESTED
        elif refuting:
            status = HypothesisStatus.REFUTED
        elif corroborating:
            status = HypothesisStatus.CORROBORATED
        elif experiments:
            status = HypothesisStatus.UNDER_TEST
        else:
            status = HypothesisStatus.PROPOSED

        return BeliefState(
            hypothesis_id=hypothesis_id, proposition=str(record.get("proposition") or ""),
            status=status, corroborations=len(corroborating), refutations=len(refuting),
            negative_observations=negatives,
            predictions=tuple(record.get("predictions") or ()),
            falsifiers=tuple(record.get("falsifiers") or ()),
            alternatives=tuple(n.id for n in self.competing(hypothesis_id)),
            experiments=tuple(sorted(experiments)))

    def why_believe(self, hypothesis_id: str, *, max_depth: int = 8) -> list[str]:
        """The provenance walk: observations, the protocol each was taken under, and back."""
        return self.graph.why(hypothesis_id, max_depth=max_depth)

    def briefing(self, envelope: RunEnvelope) -> str:
        """What a reader resuming this project in three months needs first.

        Contested and refuted hypotheses lead, because those are the ones a fresh reader
        will otherwise re-propose.
        """
        states: list[BeliefState] = []
        for node in self.hypotheses():
            try:
                states.append(self.belief(node.id, envelope))
            except WorldModelRefused:
                continue          # above this run's ceiling: withheld, not summarised
        if not states:
            return "no readable hypotheses are recorded for this project"
        order = {HypothesisStatus.CONTESTED: 0, HypothesisStatus.REFUTED: 1,
                 HypothesisStatus.UNDER_TEST: 2, HypothesisStatus.CORROBORATED: 3,
                 HypothesisStatus.PROPOSED: 4}
        states.sort(key=lambda s: (order.get(s.status, 9), s.hypothesis_id))
        lines = [f"{len(states)} hypothesis(es):"]
        for state in states:
            lines.append(f"  - [{state.status.value}] {state.proposition}"
                         f" (+{state.corroborations}/-{state.refutations})")
            if state.status is HypothesisStatus.PROPOSED and state.alternatives:
                lines.append(f"      competes with {len(state.alternatives)} recorded "
                             "alternative(s)")
        return "\n".join(lines)

    # ------------------------------------------------------------------ audit
    def _record(self, event: str, envelope: RunEnvelope, **detail: Any) -> None:
        if self._audit is None:
            return
        try:
            self._audit(event, run_id=envelope.run_id, detail=dict(detail))
        except Exception:  # noqa: BLE001 - an audit failure must not end the work
            pass


def bayes_update(prior: float, likelihood_ratio: float) -> float:
    """Posterior from a prior and a likelihood ratio, when a caller has both.

    A function rather than a field on ``BeliefState``, on purpose. A number produced
    automatically from edge counts would be quoted as a probability by everyone who saw it
    and justified by nobody, and the likelihood ratio is exactly the quantity nobody has
    for "a mouse experiment came out the way we expected".
    """
    if not 0.0 < prior < 1.0:
        raise ValueError("a prior must be strictly between 0 and 1")
    if likelihood_ratio <= 0:
        raise ValueError("a likelihood ratio must be positive")
    odds = prior / (1.0 - prior) * likelihood_ratio
    return odds / (1.0 + odds)
