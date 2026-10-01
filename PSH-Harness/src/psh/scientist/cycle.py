"""The scientific cycle as a state machine, rather than as a task list.

``TaskState`` says whether a step ran. It cannot say whether the project is at the point
where an experiment may be designed, or whether the protocol was frozen before the data
came back — and those are orderings, not steps. Robin's published closed loop makes the
same distinction: hypothesis, experiment proposal, human execution, data, analysis,
interpretation, revised hypothesis. Each arrow is a commitment about what may follow what.

Writing it down as transitions buys one thing that comments cannot:

> The illegal ordering is refused rather than noticed.

``OBSERVED -> PREREGISTERED`` is the one that matters. Preregistering after the numbers
arrive is not preregistration, and it is not a mistake anyone makes on purpose — it is what
happens when the plan is a list and somebody reorders it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Sequence

from ..contracts import PolicyDenied, new_id, utc_now

__all__ = ["Stage", "CycleViolation", "ScientificCycle", "TRANSITIONS"]


class Stage(str, Enum):
    """Where one turn of the cycle stands."""

    QUESTION = "question"
    HYPOTHESIS = "hypothesis"
    PREDICTION = "prediction"
    DESIGN = "design"                  # an experiment or analysis is being designed
    PREREGISTERED = "preregistered"    # the protocol is frozen
    EXECUTED = "executed"              # the work ran
    OBSERVED = "observed"              # results are in, uninterpreted
    ANALYSED = "analysed"
    INTERPRETED = "interpreted"
    BELIEF_UPDATED = "belief_updated"
    CLOSED = "closed"
    ABANDONED = "abandoned"


S = Stage

#: What may follow what. Every stage may be abandoned, because a project that cannot be
#: stopped is not a project; nothing may go back to ``PREREGISTERED``.
TRANSITIONS: Mapping[Stage, frozenset[Stage]] = {
    S.QUESTION: frozenset({S.HYPOTHESIS, S.ABANDONED}),
    S.HYPOTHESIS: frozenset({S.PREDICTION, S.HYPOTHESIS, S.ABANDONED}),
    S.PREDICTION: frozenset({S.DESIGN, S.PREDICTION, S.HYPOTHESIS, S.ABANDONED}),
    # A design may be executed without preregistration — exploratory work is legitimate —
    # and the result then carries that fact, because the cycle records which arrow it took.
    S.DESIGN: frozenset({S.PREREGISTERED, S.EXECUTED, S.DESIGN, S.ABANDONED}),
    S.PREREGISTERED: frozenset({S.EXECUTED, S.ABANDONED}),
    S.EXECUTED: frozenset({S.OBSERVED, S.ABANDONED}),
    S.OBSERVED: frozenset({S.ANALYSED, S.ABANDONED}),
    S.ANALYSED: frozenset({S.INTERPRETED, S.ANALYSED, S.ABANDONED}),
    S.INTERPRETED: frozenset({S.BELIEF_UPDATED, S.ABANDONED}),
    # The loop closes here: a belief update either ends the cycle or starts the next one
    # with a revised hypothesis.
    S.BELIEF_UPDATED: frozenset({S.HYPOTHESIS, S.CLOSED, S.ABANDONED}),
    S.CLOSED: frozenset(),
    S.ABANDONED: frozenset(),
}


class CycleViolation(PolicyDenied):
    """An ordering the scientific cycle does not permit."""


@dataclass(frozen=True, slots=True)
class Transition:
    """One move, kept so the path a cycle took is answerable afterwards."""

    frm: Stage
    to: Stage
    at: float = field(default_factory=utc_now)
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"from": self.frm.value, "to": self.to.value, "at": self.at,
                "note": self.note}


class ScientificCycle:
    """One turn of question -> hypothesis -> ... -> belief update.

    Cheap and serialisable on purpose: a cycle is state a project carries for months, so it
    has to survive a checkpoint and a process restart like anything else here.
    """

    def __init__(self, *, cycle_id: str = "", stage: Stage = Stage.QUESTION,
                 audit: Any = None, run_id: str = "") -> None:
        self.cycle_id = cycle_id or new_id("cyc")
        self.stage = stage
        self.history: list[Transition] = []
        self._audit = audit
        self.run_id = run_id

    # ------------------------------------------------------------------ moves
    def may(self, to: Stage) -> bool:
        return to in TRANSITIONS.get(self.stage, frozenset())

    def advance(self, to: Stage, *, note: str = "") -> Stage:
        """Move to the next stage, or refuse and say which arrow does not exist."""
        if not self.may(to):
            allowed = sorted(s.value for s in TRANSITIONS.get(self.stage, frozenset()))
            raise CycleViolation(
                f"cycle {self.cycle_id}: {self.stage.value} -> {to.value} is not a "
                f"transition of the scientific cycle; from {self.stage.value} the "
                f"permitted next stages are {allowed}"
                + (". Freezing a protocol after the results are in is not "
                   "preregistration" if to is Stage.PREREGISTERED else ""))
        transition = Transition(frm=self.stage, to=to, note=note)
        self.history.append(transition)
        self.stage = to
        if self._audit is not None:
            try:
                self._audit("cycle_advanced", run_id=self.run_id,
                            detail={"cycle_id": self.cycle_id, "from": transition.frm.value,
                                    "to": to.value})
            except Exception:  # noqa: BLE001 - an audit failure must not end the work
                pass
        return self.stage

    @property
    def preregistered(self) -> bool:
        """Whether this cycle passed through the freeze before it executed."""
        return any(t.to is Stage.PREREGISTERED for t in self.history)

    @property
    def exploratory(self) -> bool:
        """Executed without a freeze. Not a fault — a fact the result should carry."""
        return any(t.frm is Stage.DESIGN and t.to is Stage.EXECUTED
                   for t in self.history)

    @property
    def terminal(self) -> bool:
        return not TRANSITIONS.get(self.stage, frozenset())

    def path(self) -> tuple[str, ...]:
        if not self.history:
            return (self.stage.value,)
        return (self.history[0].frm.value,) + tuple(t.to.value for t in self.history)

    def summary(self) -> str:
        return (f"cycle {self.cycle_id[:12]} at {self.stage.value} via "
                + " -> ".join(self.path())
                + ("" if self.preregistered else " (exploratory)"))

    # ---------------------------------------------------------- serialisation
    def as_dict(self) -> dict[str, Any]:
        return {"cycle_id": self.cycle_id, "stage": self.stage.value,
                "run_id": self.run_id,
                "history": [t.as_dict() for t in self.history]}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any], *, audit: Any = None) -> "ScientificCycle":
        cycle = cls(cycle_id=str(data.get("cycle_id") or ""),
                    stage=Stage(data.get("stage") or Stage.QUESTION.value),
                    audit=audit, run_id=str(data.get("run_id") or ""))
        cycle.history = [
            Transition(frm=Stage(t["from"]), to=Stage(t["to"]),
                       at=float(t.get("at") or utc_now()), note=str(t.get("note") or ""))
            for t in data.get("history") or ()]
        return cycle
