"""The scientist plane: the records a project keeps, and the relations between them.

Two halves, deliberately separate:

* ``records.ScientificLedger`` — immutable, content-hashed hypotheses, protocols and
  observations, written through the persistence gateway, each linked to what it derives
  from. The archival half.
* ``worldmodel.ScientificWorldModel`` — which explanations compete, and which observations
  corroborate or refute which hypothesis. The relational half, which is what makes "where
  does this stand, and why?" answerable by traversal.

``cycle`` orders the turns a project takes through them, and ``deviations`` grades a
departure from a preregistered protocol so a reader is told which one mattered.
"""

from .cycle import TRANSITIONS, CycleViolation, ScientificCycle, Stage
from .deviations import (
    SEVERITY, DeviationSeverity, GradedDeviation, grade, grade_changes, worst,
)
from .models import Deviation, Hypothesis, Observation, Protocol
from .ports import ProtocolResolver
from .worldmodel import (
    BeliefState, HypothesisStatus, ScientificWorldModel, WorldModelRefused, bayes_update,
)

__all__ = [
    # records (the ledger loads lazily; importing a model must not pull persistence in)
    "Hypothesis", "Protocol", "Observation", "Deviation", "ScientificLedger",
    "ProtocolResolver",
    # relations
    "ScientificWorldModel", "BeliefState", "HypothesisStatus", "WorldModelRefused",
    "bayes_update",
    # departures
    "GradedDeviation", "DeviationSeverity", "SEVERITY", "grade", "grade_changes", "worst",
    # the cycle
    "ScientificCycle", "Stage", "TRANSITIONS", "CycleViolation",
]


def __getattr__(name):
    if name == "ScientificLedger":
        from .records import ScientificLedger
        globals()[name] = ScientificLedger
        return ScientificLedger
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return sorted(set(globals()) | set(__all__))
