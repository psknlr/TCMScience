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

``inquiry`` decides the rest: which analysis to run next (expected information gain over
predictions sealed in advance), what its outcome does to belief, when the project may stop
(severe tests, replication, a catch-all that keeps the world open), and how strongly the
answer may be stated (the licensing table, not the posterior). ``recorder`` writes an
inquiry into the world model and the cycle as it goes.
"""

from .cycle import TRANSITIONS, CycleViolation, ScientificCycle, Stage
from .deviations import (
    SEVERITY, DeviationSeverity, GradedDeviation, grade, grade_changes, worst,
)
from .inquiry import (
    CATCH_ALL, INQUIRY_CODES, Analysis, AnalysisUnavailable, Conclusion, Executed,
    Explanation, Inquiry, InquiryRecorder, InquiryRefused, Option, Purpose, Role, Step,
    StoppingRule, Update, Verdict, expected_information_gain, run_inquiry,
)
from .models import Deviation, Hypothesis, Observation, Protocol
from .ports import ProtocolResolver
from .recorder import WorldModelRecorder
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
    # the inquiry: what to run next, what was learned, what it licenses
    "Inquiry", "Explanation", "Analysis", "StoppingRule", "Role", "Purpose", "Verdict",
    "Option", "Step", "Update", "Conclusion", "Executed", "InquiryRecorder",
    "InquiryRefused", "INQUIRY_CODES", "CATCH_ALL", "expected_information_gain",
    "run_inquiry", "AnalysisUnavailable", "WorldModelRecorder",
]


def __getattr__(name):
    if name == "ScientificLedger":
        from .records import ScientificLedger
        globals()[name] = ScientificLedger
        return ScientificLedger
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return sorted(set(globals()) | set(__all__))
