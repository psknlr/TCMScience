"""Choosing what to find out next, and what the answer licenses.

``ScientificWorldModel`` says which explanations compete and which observations bore on
which. ``ScientificCycle`` says what may follow what. Neither says **which analysis to run
next**, how far a result should move belief, or when a project knows enough to stop, and
those are the decisions an autonomous scientist makes most often and a reviewer can check
least. Left to the model, all three drift the same way: towards the analysis that will
confirm, the update that flatters, and the stop that comes as soon as the story is good.

This module turns them into computations over commitments made in advance::

    explanations + catch-all ──> sealed predictions P(outcome | explanation, analysis)
            │                                     │
            │       expected information gain <───┘
            v                     │
         belief <── Bayes ── observe <── run the analysis the engine chose
            │
            ├── leader above threshold?  ── severe test against every live rival
            │                                  └── replicated?
            v
       conclusion ── capped by the licensing table, not by the posterior

The model proposes: the explanations, their priors, the analyses, and what each
explanation predicts each analysis will show. The engine decides: which analysis is worth
running, what an outcome does to belief, when to stop, and how strongly the result may be
stated. That is the separation the kernel already keeps for tools and claims, applied to
belief: **the model may say what it expects; it does not decide what was learned.**

Five rules carry the weight.

**1. Predictions are sealed before the outcome exists.** A likelihood declared after the
result is an accommodation, not a prediction, and is refused (``INQ115``); a sealed one
cannot be changed (``INQ116``). Belief is therefore a function of what was committed in
advance and what came back. The serialised inquiry *is* its trail, and loading it replays
every step; a posterior that does not recompute is refused (``INQ150``).

**2. A design that cannot license a claim kind cannot move belief in it.** A docking score
says nothing about clinical efficacy, so a docking analysis may not carry a prediction for
an efficacy hypothesis (``INQ113``). The engine holds such a hypothesis fixed through the
update by giving it the predictive distribution of the others, which is the exact Bayesian
form of "this observation is irrelevant to it": its probability neither grows nor shrinks
however many docking runs come back. The table is ``psh.sir.values.LICENSING``, the one
the compiler and the release gate already enforce, so licensing now constrains the
likelihood as well as the claim.

**3. The world is never closed.** Every inquiry carries an explicit catch-all, "none of
the named explanations", with a floor on its prior (``INQ101``) and a fixed uniform
prediction. Without it the posterior concentrates on the best of a bad set. With it, a run
of outcomes no named explanation predicted moves mass to the catch-all, and past an alarm
the engine stops asking which explanation is right and asks for new ones. A new
explanation is carved out of the catch-all's *current* mass, so it gets no credit for the
data it was invented to fit: it earns its posterior on analyses it predicted.

**4. A high posterior is not enough to stop.** The leader is accepted only after it has
passed a **severe test** against every live rival (an analysis that, had the rival been
true, would probably not have favoured the leader), and only once each test the acceptance
rests on has been **replicated**. Severity is computed from the sealed predictions alone, so
it does not inherit the priors a posterior does. A string of weakly informative,
confirmatory analyses raises a posterior; it cannot satisfy this rule.

**5. The posterior is not a licence.** How strongly a conclusion may be stated is capped by
the designs of the observations that bore on it (``MAX_CERTAINTY``) and by the claim kind
those designs can reach (``LICENSING``). Ten in-silico analyses at a posterior of 0.99
license a tentative mechanism hypothesis, which is what one of them licenses.

What this is not. The likelihoods are the proposer's commitments, not measurements, and
the posterior is conditional on them; every conclusion says so. Information gain is
myopic, one step ahead. Outcomes are discrete and declared exhaustively per analysis. The
engine does not decide whether an analysis may *run*: that is the execution broker's
question, asked by whoever executes the step.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Mapping, Protocol as StructuralProtocol, Sequence

from ..contracts import PolicyDenied, new_id
from ..evidence.support import Certainty
from ..sir.values import LICENSING, MAX_CERTAINTY, ClaimKind, Licensing, StudyDesign

__all__ = [
    "CATCH_ALL", "INQUIRY_CODES", "Role", "Purpose", "Verdict", "Explanation", "Analysis",
    "StoppingRule", "Option", "Step", "Update", "Conclusion", "Executed", "Inquiry",
    "InquiryRefused", "InquiryRecorder", "AnalysisUnavailable",
    "expected_information_gain", "run_inquiry",
]

#: The id the engine gives the catch-all. Reserved: no proposed explanation may take it.
CATCH_ALL = "catch-all"

#: Every refusal the engine can make, with the instruction that repairs it. The message is
#: the repair instruction when the author is a model, so each names what to write instead.
INQUIRY_CODES: Mapping[str, tuple[str, str]] = {
    "INQ101": ("the world is closed",
               "leave prior mass for an explanation nobody has named: lower the named "
               "priors until the catch-all keeps at least the rule's floor"),
    "INQ102": ("fewer than two named explanations",
               "name at least two explanations; one hypothesis and its absence is a "
               "test, not a comparison"),
    "INQ103": ("malformed explanation",
               "give each explanation a unique id, a proposition, a role, a prior in "
               "(0, 1) and, when it asserts something about the world, a claim kind"),
    "INQ104": ("malformed analysis",
               "give each analysis a unique id, a title, a study design, at least two "
               "distinct outcomes and a positive cost; a replicate names an analysis "
               "with the same outcomes"),
    "INQ105": ("malformed stopping rule",
               "accept_at in (0.5, 1), min_severity in (0, 1), "
               "0 < catch_all_floor < catch_all_alarm <= 1, min_gain >= 0, a positive "
               "budget or none, max_steps >= 1"),
    "INQ110": ("the analysis has no sealed prediction for an explanation it bears on",
               "declare, before running it, what every live explanation it bears on "
               "predicts it will show"),
    "INQ111": ("unknown analysis or explanation", "use an id the inquiry has recorded"),
    "INQ112": ("a prediction is not a probability distribution over the outcomes",
               "give one finite probability per declared outcome, summing to 1"),
    "INQ113": ("the analysis's design cannot license this explanation's claim kind",
               "remove the prediction: the engine holds this explanation fixed through "
               "the analysis; a design that can license the claim is needed to move it"),
    "INQ114": ("a prediction was declared for the catch-all",
               "remove it; the catch-all's prediction is fixed (uniform) by the engine"),
    "INQ115": ("a prediction was declared after the outcome",
               "an outcome already observed cannot be predicted; declare predictions "
               "for analyses not yet run"),
    "INQ116": ("a sealed prediction cannot be changed",
               "add a new analysis if the plan changed; the sealed prediction stands"),
    "INQ130": ("the outcome is not one the analysis declared",
               "classify the result into one of the declared outcomes; an outcome "
               "nobody predicted cannot update a belief"),
    "INQ131": ("the analysis was already observed",
               "record a replicate as its own analysis, naming this one in replicates"),
    "INQ132": ("the inquiry's budget or step limit would be exceeded",
               "conclude with what is known, or open a new inquiry with a declared "
               "larger budget"),
    "INQ133": ("the analysis was withdrawn, or cannot be",
               "a withdrawn analysis is never run; an observed one stays observed. "
               "Withdraw with a stated reason before the analysis has an outcome"),
    "INQ140": ("the new explanations cannot be admitted",
               "admit new explanations with shares of the catch-all's current mass that "
               "leave it at least the rule's floor of that mass"),
    "INQ150": ("the inquiry does not verify",
               "the trail was altered, or a recorded belief does not follow from the "
               "sealed predictions and outcomes; reload from an unaltered trail"),
}


class InquiryRefused(PolicyDenied):
    """An inquiry operation was refused. ``code`` names the rule; ``remedy`` repairs it."""

    def __init__(self, code: str, detail: str) -> None:
        title, remedy = INQUIRY_CODES[code]
        self.code = code
        self.detail = detail
        self.remedy = remedy
        super().__init__(f"{code} {title}: {detail}. Remedy: {remedy}")


class AnalysisUnavailable(Exception):
    """Raised by an executor when an analysis cannot be run at all (the data it needs are
    absent, a tool failed). ``run_inquiry`` withdraws the analysis with the message as the
    reason: an analysis that could not run has no outcome, and inventing one would be
    worse than leaving the question open."""


class Role(str, Enum):
    """What an explanation is about."""

    #: Asserts something about the world, so it has a claim kind and licensing applies.
    SUBSTANTIVE = "substantive"
    #: The signal is produced by the method or the data. A finding about the analysis,
    #: which any analysis can bear on, and which licenses no claim about the world.
    ARTEFACT = "artefact"
    #: None of the named explanations. Created and predicted by the engine only.
    CATCH_ALL = "catch_all"


class Purpose(str, Enum):
    """Why the engine chose a step."""

    DISCRIMINATE = "discriminate"    # the most expected information per unit cost
    CONTRADICT = "contradict"        # a severe test of the leader against one rival
    REPLICATE = "replicate"          # repeat a test the acceptance rests on
    UNRECOMMENDED = "unrecommended"  # the caller ran something else; recorded as such


class Verdict(str, Enum):
    ACCEPTED = "accepted"                    # every condition of the stopping rule is met
    PROVISIONAL = "provisional"              # leads, but untested or unreplicated
    UNDETERMINED = "undetermined"            # nothing decided it
    NEEDS_HYPOTHESES = "needs_hypotheses"    # the named explanations fit poorly


_TOLERANCE = 1e-6
_REPLAY_TOLERANCE = 1e-9


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _real(value: Any) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def expected_information_gain(belief: Mapping[str, float],
                              likelihoods: Mapping[str, Mapping[str, float]]) -> float:
    """The mutual information between the explanations and an analysis's outcome, in bits.

    ``belief`` maps each explanation to its probability; ``likelihoods`` maps each to its
    distribution over the same outcomes. Computed as ``sum_h p(h) KL(q_h || m)`` with
    ``m`` the predictive mixture, which is non-negative term by term, so rounding can only
    produce a tiny negative, clipped to zero.
    """
    outcomes: tuple[str, ...] = ()
    for row in likelihoods.values():
        outcomes = tuple(row)
        break
    mixture = {o: sum(belief[h] * likelihoods[h][o] for h in belief) for o in outcomes}
    gain = 0.0
    for h, p in belief.items():
        if p <= 0.0:
            continue
        for o in outcomes:
            q = likelihoods[h][o]
            if q > 0.0:
                gain += p * q * math.log2(q / mixture[o])
    return max(0.0, gain)


# ----------------------------------------------------------------- value objects
@dataclass(frozen=True, slots=True)
class Explanation:
    """One competing explanation: what it says, what kind of claim, how plausible a priori.

    ``prior`` is set when the inquiry opens. An explanation admitted later leaves it at
    0.0 and is given a share of the catch-all instead (``Inquiry.admit``).
    """

    id: str
    proposition: str
    claim_kind: ClaimKind | None = None
    role: Role = Role.SUBSTANTIVE
    prior: float = 0.0

    def __post_init__(self) -> None:
        if not _text(self.id) or self.id != self.id.strip():
            raise InquiryRefused("INQ103", f"explanation id {self.id!r} is not a clean id")
        if not isinstance(self.role, Role):
            raise InquiryRefused("INQ103", f"{self.id}: role must be a Role")
        if (self.id == CATCH_ALL) != (self.role is Role.CATCH_ALL):
            raise InquiryRefused("INQ103", f"{self.id}: the id {CATCH_ALL!r} and the "
                                 "catch-all role are reserved for the engine")
        if not _text(self.proposition):
            raise InquiryRefused("INQ103", f"{self.id}: states no proposition")
        if self.role is Role.SUBSTANTIVE and not isinstance(self.claim_kind, ClaimKind):
            raise InquiryRefused("INQ103", f"{self.id}: an explanation about the world "
                                 "needs a claim kind, which is what licensing reads")
        if self.role is not Role.SUBSTANTIVE and self.claim_kind is not None:
            raise InquiryRefused("INQ103", f"{self.id}: a {self.role.value} explanation "
                                 "asserts nothing about the world and has no claim kind")
        if not _real(self.prior) or not 0.0 <= self.prior < 1.0:
            raise InquiryRefused("INQ103", f"{self.id}: prior {self.prior!r} is not a "
                                 "probability below 1")

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "proposition": self.proposition,
                "claim_kind": self.claim_kind.value if self.claim_kind else None,
                "role": self.role.value, "prior": self.prior}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Explanation":
        kind = data.get("claim_kind")
        return cls(id=str(data.get("id") or ""), proposition=str(data.get("proposition") or ""),
                   claim_kind=ClaimKind(kind) if kind else None,
                   role=Role(data.get("role") or Role.SUBSTANTIVE.value),
                   prior=float(data.get("prior") or 0.0))


@dataclass(frozen=True, slots=True)
class Analysis:
    """Something the inquiry can run, the design of the evidence it yields, and its outcomes.

    ``outcomes`` are discrete and exhaustive: whoever executes the analysis classifies its
    result into exactly one of them. ``replicates`` names the analysis this one repeats
    under changed conditions (a seed, a threshold, a second source), with the same outcomes.
    """

    id: str
    title: str
    design: StudyDesign
    outcomes: tuple[str, ...]
    cost: float = 1.0
    replicates: str = ""

    def __post_init__(self) -> None:
        if not _text(self.id) or self.id != self.id.strip():
            raise InquiryRefused("INQ104", f"analysis id {self.id!r} is not a clean id")
        if not _text(self.title):
            raise InquiryRefused("INQ104", f"{self.id}: has no title")
        if not isinstance(self.design, StudyDesign):
            raise InquiryRefused("INQ104", f"{self.id}: design must be a StudyDesign")
        if (not isinstance(self.outcomes, tuple) or len(self.outcomes) < 2
                or len(set(self.outcomes)) != len(self.outcomes)
                or not all(_text(o) for o in self.outcomes)):
            raise InquiryRefused("INQ104", f"{self.id}: needs a tuple of at least two "
                                 "distinct, named outcomes")
        if not _real(self.cost) or self.cost <= 0.0:
            raise InquiryRefused("INQ104", f"{self.id}: cost must be positive")
        if not isinstance(self.replicates, str) or self.replicates == self.id:
            raise InquiryRefused("INQ104", f"{self.id}: cannot replicate itself")

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "title": self.title, "design": self.design.value,
                "outcomes": list(self.outcomes), "cost": self.cost,
                "replicates": self.replicates}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Analysis":
        return cls(id=str(data.get("id") or ""), title=str(data.get("title") or ""),
                   design=StudyDesign(data.get("design") or StudyDesign.UNKNOWN.value),
                   outcomes=tuple(data.get("outcomes") or ()),
                   cost=float(data.get("cost") or 0.0),
                   replicates=str(data.get("replicates") or ""))


@dataclass(frozen=True, slots=True)
class StoppingRule:
    """When the inquiry may stop, declared before it starts and sealed into its trail.

    ``catch_all_floor`` is the least prior the catch-all may open with, and the least
    fraction of its current mass it keeps when new explanations are carved out of it.
    """

    accept_at: float = 0.95
    min_severity: float = 0.8
    require_replication: bool = True
    catch_all_floor: float = 0.05
    catch_all_alarm: float = 0.5
    min_gain: float = 0.01
    budget: float | None = None
    max_steps: int = 64

    def __post_init__(self) -> None:
        problems = []
        if not _real(self.accept_at) or not 0.5 < self.accept_at < 1.0:
            problems.append("accept_at")
        if not _real(self.min_severity) or not 0.0 < self.min_severity < 1.0:
            problems.append("min_severity")
        if not isinstance(self.require_replication, bool):
            problems.append("require_replication")
        if (not _real(self.catch_all_floor) or not _real(self.catch_all_alarm)
                or not 0.0 < self.catch_all_floor < self.catch_all_alarm <= 1.0):
            problems.append("catch_all_floor/catch_all_alarm")
        if not _real(self.min_gain) or self.min_gain < 0.0:
            problems.append("min_gain")
        if self.budget is not None and (not _real(self.budget) or self.budget <= 0.0):
            problems.append("budget")
        if (not isinstance(self.max_steps, int) or isinstance(self.max_steps, bool)
                or self.max_steps < 1):
            problems.append("max_steps")
        if problems:
            raise InquiryRefused("INQ105", ", ".join(problems))

    def as_dict(self) -> dict[str, Any]:
        return {"accept_at": self.accept_at, "min_severity": self.min_severity,
                "require_replication": self.require_replication,
                "catch_all_floor": self.catch_all_floor,
                "catch_all_alarm": self.catch_all_alarm, "min_gain": self.min_gain,
                "budget": self.budget, "max_steps": self.max_steps}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "StoppingRule":
        return cls(**{k: data[k] for k in cls.__dataclass_fields__ if k in data})


@dataclass(frozen=True, slots=True)
class Option:
    """A runnable analysis and what the engine expects to learn from it."""

    analysis_id: str
    expected_gain: float
    cost: float

    @property
    def gain_per_cost(self) -> float:
        return self.expected_gain / self.cost


@dataclass(frozen=True, slots=True)
class Step:
    """What the inquiry should do next: run, declare, propose, or stop."""

    action: str                       # run | declare | propose | stop
    analysis_id: str = ""
    purpose: Purpose | None = None
    against: str = ""                 # the rival a contradiction test is aimed at
    expected_gain: float = 0.0
    severity: float = 0.0
    pending: tuple[str, ...] = ()     # analyses that lack sealed predictions
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"action": self.action, "analysis_id": self.analysis_id,
                "purpose": self.purpose.value if self.purpose else "",
                "against": self.against, "expected_gain": self.expected_gain,
                "severity": self.severity, "pending": list(self.pending),
                "reason": self.reason}


@dataclass(frozen=True, slots=True)
class Update:
    """One observation and exactly what it did to belief."""

    step: int
    analysis_id: str
    design: StudyDesign
    outcome: str
    purpose: Purpose
    against: str
    recommended: str
    expected_gain: float
    information: float                       # KL(posterior || prior), bits
    prior: Mapping[str, float]
    posterior: Mapping[str, float]
    likelihood: Mapping[str, float]          # effective P(outcome | explanation)
    bayes_factor: Mapping[str, float | None]  # against the rest, live explanations only;
                                             # None when unbounded
    held_fixed: tuple[str, ...]              # explanations the design cannot bear on
    refuted: tuple[str, ...]                 # declared this outcome impossible
    licensing: Mapping[str, str]             # per substantive explanation
    cost: float
    evidence_ref: str = ""
    result_digest: str = ""
    declaration: str = ""                    # digest of the sealed predictions applied

    def as_dict(self) -> dict[str, Any]:
        return {"step": self.step, "analysis_id": self.analysis_id,
                "design": self.design.value, "outcome": self.outcome,
                "purpose": self.purpose.value, "against": self.against,
                "recommended": self.recommended, "expected_gain": self.expected_gain,
                "information": self.information, "prior": dict(self.prior),
                "posterior": dict(self.posterior), "likelihood": dict(self.likelihood),
                "bayes_factor": dict(self.bayes_factor),
                "held_fixed": list(self.held_fixed), "refuted": list(self.refuted),
                "licensing": dict(self.licensing), "cost": self.cost,
                "evidence_ref": self.evidence_ref, "result_digest": self.result_digest,
                "declaration": self.declaration}


@dataclass(frozen=True, slots=True)
class Conclusion:
    """Where the inquiry stands, and the strongest statement it licenses.

    ``posterior`` is conditional on the sealed predictions. ``claim_kind``,
    ``licensing`` and ``certainty`` are what a release may say: they come from the designs
    of the observations that bore on the leader, never from the posterior alone.
    """

    verdict: Verdict
    question: str
    leader: str
    proposition: str
    leader_posterior: float
    posterior: Mapping[str, float]
    role: Role | None
    claim_kind: ClaimKind | None
    licensing: Licensing | None
    certainty: Certainty
    designs: tuple[StudyDesign, ...]
    severe_tests: Mapping[str, tuple[str, ...]]
    replications: Mapping[str, tuple[str, ...]]
    untested: tuple[str, ...]
    unreplicated: tuple[str, ...]
    needs: Mapping[str, tuple[str, ...]]     # rival -> designs that could license a test
    final: bool                              # the stopping rule, not the caller, stopped it
    reason: str
    limitations: tuple[str, ...]
    steps: int
    spent: float
    trail_head: str

    @property
    def accepted(self) -> bool:
        return self.verdict is Verdict.ACCEPTED

    def as_dict(self) -> dict[str, Any]:
        body = {"verdict": self.verdict.value, "question": self.question,
                "leader": self.leader, "proposition": self.proposition,
                "leader_posterior": self.leader_posterior,
                "posterior": dict(self.posterior),
                "role": self.role.value if self.role else None,
                "claim_kind": self.claim_kind.value if self.claim_kind else None,
                "licensing": self.licensing.value if self.licensing else None,
                "certainty": self.certainty.value,
                "designs": [d.value for d in self.designs],
                "severe_tests": {k: list(v) for k, v in self.severe_tests.items()},
                "replications": {k: list(v) for k, v in self.replications.items()},
                "untested": list(self.untested), "unreplicated": list(self.unreplicated),
                "needs": {k: list(v) for k, v in self.needs.items()},
                "final": self.final, "reason": self.reason,
                "limitations": list(self.limitations), "steps": self.steps,
                "spent": self.spent, "trail_head": self.trail_head}
        body["digest"] = _digest(body)
        return body

    def describe(self) -> str:
        if not self.leader:
            return f"{self.verdict.value}: {self.reason}"
        kind = self.claim_kind.value if self.claim_kind else "a finding about the analysis"
        licence = f", licensed {self.licensing.value}" if self.licensing else ""
        return (f"{self.verdict.value}: {self.leader} ({self.proposition}) at "
                f"p={self.leader_posterior:.3f}; {kind}{licence}; stated at most "
                f"{self.certainty.value}. {self.reason}")


@dataclass(frozen=True, slots=True)
class Executed:
    """What an executor returns: the declared outcome the result was classified into."""

    outcome: str
    evidence_ref: str = ""
    result_digest: str = ""


class InquiryRecorder(StructuralProtocol):
    """Receives each trail entry *before* the inquiry's state moves.

    Write-ahead on purpose: a recorder that refuses (the kernel's persistence gateway, an
    audit chain that cannot be written) leaves the inquiry exactly as it was, so there is
    no interval in which belief moved and the record of why did not.
    """

    def record(self, inquiry: "Inquiry", event: str, entry: Mapping[str, Any]) -> None:
        ...


@dataclass(slots=True)
class _Standing:
    leader: str
    posterior: float
    rivals: tuple[str, ...]
    tested: dict[str, list[str]] = field(default_factory=dict)
    replicated: dict[str, list[str]] = field(default_factory=dict)
    failed: dict[str, list[str]] = field(default_factory=dict)   # replications that did not
    untested: list[str] = field(default_factory=list)
    unreplicated: list[str] = field(default_factory=list)


# ------------------------------------------------------------------- the engine
class Inquiry:
    """One question, its competing explanations, and the analyses that could decide it."""

    def __init__(self, question: str, explanations: Sequence[Explanation],
                 analyses: Sequence[Analysis] = (), rule: StoppingRule | None = None, *,
                 recorder: InquiryRecorder | None = None, inquiry_id: str = "") -> None:
        if not _text(question):
            raise InquiryRefused("INQ103", "the inquiry states no question")
        rule = rule or StoppingRule()
        if not isinstance(rule, StoppingRule):
            raise InquiryRefused("INQ105", "rule must be a StoppingRule")
        named = list(explanations)
        if any(not isinstance(e, Explanation) for e in named):
            raise InquiryRefused("INQ103", "explanations must be Explanation objects")
        if any(e.role is Role.CATCH_ALL for e in named):
            raise InquiryRefused("INQ103", "the catch-all is the engine's, not a proposal")
        if len(named) < 2:
            raise InquiryRefused("INQ102", f"{len(named)} named")
        ids = [e.id for e in named]
        if len(set(ids)) != len(ids):
            raise InquiryRefused("INQ103", f"duplicate ids in {sorted(ids)}")
        if any(e.prior <= 0.0 for e in named):
            raise InquiryRefused("INQ103", "every explanation opens with a positive prior: "
                                 + ", ".join(e.id for e in named if e.prior <= 0.0))
        remainder = 1.0 - math.fsum(e.prior for e in named)
        if remainder < rule.catch_all_floor - _TOLERANCE:
            raise InquiryRefused("INQ101", f"the catch-all would open at "
                                 f"{max(remainder, 0.0):.4f}, below the floor "
                                 f"{rule.catch_all_floor}")
        self.question = question
        self.inquiry_id = inquiry_id or new_id("inq")
        self.rule = rule
        self._recorder = recorder
        catch_all = Explanation(CATCH_ALL, "none of the named explanations",
                                role=Role.CATCH_ALL)
        self._explanations: dict[str, Explanation] = {e.id: e for e in named}
        self._explanations[CATCH_ALL] = catch_all
        self._belief: dict[str, float] = {e.id: e.prior for e in named}
        self._belief[CATCH_ALL] = remainder
        self._analyses: dict[str, Analysis] = {}
        self._sealed: dict[str, dict[str, dict[str, float]]] = {}     # as declared
        self._declared: dict[str, dict[str, dict[str, float]]] = {}   # normalised
        self._observed: dict[str, Update] = {}
        self._withdrawn: dict[str, str] = {}
        self._spent = 0.0
        self._trail: list[dict[str, Any]] = []
        for analysis in analyses:
            self._check_analysis(analysis)
            self._analyses[analysis.id] = analysis
        self._commit("opened", {
            "inquiry_id": self.inquiry_id, "question": question, "rule": rule.as_dict(),
            "explanations": [e.as_dict() for e in named],
            "analyses": [a.as_dict() for a in self._analyses.values()],
            "belief": dict(self._belief)})

    # ------------------------------------------------------------------- reads
    @property
    def belief(self) -> dict[str, float]:
        return dict(self._belief)

    @property
    def explanations(self) -> tuple[Explanation, ...]:
        return tuple(self._explanations.values())

    @property
    def analyses(self) -> tuple[Analysis, ...]:
        return tuple(self._analyses.values())

    @property
    def updates(self) -> tuple[Update, ...]:
        return tuple(self._observed.values())

    @property
    def trail(self) -> tuple[dict[str, Any], ...]:
        return tuple(json.loads(_canonical(entry)) for entry in self._trail)

    @property
    def head(self) -> str:
        return self._trail[-1]["digest"] if self._trail else ""

    @property
    def spent(self) -> float:
        return self._spent

    def explanation(self, explanation_id: str) -> Explanation:
        if explanation_id not in self._explanations:
            raise InquiryRefused("INQ111", f"explanation {explanation_id!r}")
        return self._explanations[explanation_id]

    def analysis(self, analysis_id: str) -> Analysis:
        if analysis_id not in self._analyses:
            raise InquiryRefused("INQ111", f"analysis {analysis_id!r}")
        return self._analyses[analysis_id]

    def predictions(self, analysis_id: str) -> dict[str, dict[str, float]]:
        self.analysis(analysis_id)
        return {h: dict(row) for h, row in self._declared.get(analysis_id, {}).items()}

    def observed(self, analysis_id: str) -> bool:
        return analysis_id in self._observed

    @property
    def leader(self) -> str:
        """The named explanation with the most belief; ties go to the lower id."""
        live = [(p, h) for h, p in self._belief.items() if h != CATCH_ALL and p > 0.0]
        if not live:
            return ""
        return min(live, key=lambda item: (-item[0], item[1]))[1]

    def grade(self, explanation_id: str, analysis_id: str) -> Licensing | None:
        """How the analysis's design stands to the explanation's claim kind.

        ``None`` when licensing does not apply: an artefact or the catch-all is about the
        analysis, not about the world, so any design may bear on it.
        """
        explanation = self.explanation(explanation_id)
        analysis = self.analysis(analysis_id)
        if explanation.role is not Role.SUBSTANTIVE:
            return None
        return LICENSING.get(explanation.claim_kind, {}).get(analysis.design,
                                                              Licensing.UNLICENSED)

    def bears_on(self, explanation_id: str, analysis_id: str) -> bool:
        grade = self.grade(explanation_id, analysis_id)
        return grade is None or grade is not Licensing.UNLICENSED

    def missing(self, analysis_id: str) -> tuple[str, ...]:
        """Live explanations the analysis bears on that have no sealed prediction for it."""
        self.analysis(analysis_id)
        rows = self._declared.get(analysis_id, {})
        return tuple(h for h, e in self._explanations.items()
                     if e.role is not Role.CATCH_ALL and self._belief[h] > 0.0
                     and h not in rows and self.bears_on(h, analysis_id))

    def ready(self, analysis_id: str) -> bool:
        return (analysis_id not in self._observed and analysis_id not in self._withdrawn
                and not self.missing(analysis_id))

    @property
    def withdrawn(self) -> dict[str, str]:
        return dict(self._withdrawn)

    def _affordable(self, analysis: Analysis) -> bool:
        return (self.rule.budget is None
                or self._spent + analysis.cost <= self.rule.budget + _TOLERANCE)

    def _table(self, analysis: Analysis) -> dict[str, dict[str, float]]:
        """Every explanation's effective prediction, exactly as an update applies it.

        The catch-all predicts uniformly. An explanation with a sealed prediction uses it.
        Every other explanation (one the design cannot license, or one already refuted)
        gets the predictive mixture of the explanations that do bear on the analysis,
        which leaves its probability unchanged by any outcome.
        """
        rows = self._declared.get(analysis.id, {})
        size = len(analysis.outcomes)
        effective: dict[str, dict[str, float]] = {}
        mass = 0.0
        mixture = dict.fromkeys(analysis.outcomes, 0.0)
        for h, e in self._explanations.items():
            if e.role is Role.CATCH_ALL:
                q = {o: 1.0 / size for o in analysis.outcomes}
            elif h in rows and self.bears_on(h, analysis.id):
                q = rows[h]
            else:
                continue
            effective[h] = q
            mass += self._belief[h]
            for o in analysis.outcomes:
                mixture[o] += self._belief[h] * q[o]
        predictive = {o: mixture[o] / mass for o in analysis.outcomes}
        for h in self._explanations:
            effective.setdefault(h, predictive)
        return effective

    def expected_gain(self, analysis_id: str) -> float:
        """Bits the analysis is expected to yield under current belief; 0.0 when not ready."""
        analysis = self.analysis(analysis_id)
        if not self.ready(analysis_id):
            return 0.0
        return expected_information_gain(self._belief, self._table(analysis))

    def _pair(self, leader: str, rival: str, analysis_id: str
              ) -> tuple[dict[str, float], dict[str, float]] | None:
        rows = self._declared.get(analysis_id, {})
        for h in (leader, rival):
            if (h not in rows or self.explanation(h).role is Role.CATCH_ALL
                    or not self.bears_on(h, analysis_id)):
                return None
        return rows[leader], rows[rival]

    def severity(self, leader: str, rival: str, analysis_id: str) -> float:
        """P(the analysis does not favour ``leader`` over ``rival`` | ``rival`` is true).

        Read off the two sealed predictions alone. Zero when either explanation has no
        prediction for the analysis or the analysis cannot bear on it: a test that cannot
        tell the two apart is not a test of one against the other.
        """
        pair = self._pair(leader, rival, analysis_id)
        if pair is None:
            return 0.0
        q_leader, q_rival = pair
        return math.fsum(q_rival[o] for o in self._analyses[analysis_id].outcomes
                         if not q_leader[o] > q_rival[o])

    def power(self, leader: str, rival: str, analysis_id: str) -> float:
        """P(the analysis favours ``leader`` over ``rival`` | ``leader`` is true).

        Severity alone is not enough: when the two predict the same, no outcome favours
        the leader, and the test "fails it" with certainty under the rival because it
        cannot pass it at all.
        """
        pair = self._pair(leader, rival, analysis_id)
        if pair is None:
            return 0.0
        q_leader, q_rival = pair
        return math.fsum(q_leader[o] for o in self._analyses[analysis_id].outcomes
                         if q_leader[o] > q_rival[o])

    def severe(self, leader: str, rival: str, analysis_id: str) -> bool:
        """A severe test of ``leader`` against ``rival``: it would probably fail the leader
        if the rival were true, and probably pass it if the leader were.

        Both at ``min_severity``, so passing one carries a likelihood ratio of at least
        ``min_severity / (1 - min_severity)`` (4 at the default) for the leader.
        """
        bar = self.rule.min_severity
        return (self.severity(leader, rival, analysis_id) >= bar
                and self.power(leader, rival, analysis_id) >= bar)

    def _favours(self, leader: str, rival: str, analysis_id: str, outcome: str) -> bool:
        rows = self._declared.get(analysis_id, {})
        if leader not in rows or rival not in rows:
            return False
        return rows[leader][outcome] > rows[rival][outcome]

    def ranked(self) -> list[Option]:
        """Runnable analyses, the most expected information per unit cost first."""
        options = [Option(a.id, self.expected_gain(a.id), a.cost)
                   for a in self._analyses.values()
                   if self.ready(a.id) and self._affordable(a)]
        options.sort(key=lambda o: (-o.gain_per_cost, -o.expected_gain, o.cost,
                                    o.analysis_id))
        return options

    def _standing(self) -> _Standing | None:
        leader = self.leader
        if not leader:
            return None
        rivals = sorted((h for h, e in self._explanations.items()
                         if h != leader and e.role is not Role.CATCH_ALL
                         and self._belief[h] > 0.0),
                        key=lambda h: (-self._belief[h], h))
        standing = _Standing(leader=leader, posterior=self._belief[leader],
                             rivals=tuple(rivals))
        for rival in rivals:
            passed = [a for a, update in self._observed.items()
                      if self.severe(leader, rival, a)
                      and self._favours(leader, rival, a, update.outcome)]
            standing.tested[rival] = passed
            if not passed:
                standing.untested.append(rival)
                continue
            repeats = [a for a in self._observed if self._analyses[a].replicates in passed]
            standing.replicated[rival] = [
                a for a in repeats
                if self._favours(leader, rival, a, self._observed[a].outcome)]
            standing.failed[rival] = [
                a for a in repeats if self._pair(leader, rival, a) is not None
                and not self._favours(leader, rival, a, self._observed[a].outcome)]
            if not standing.replicated[rival]:
                standing.unreplicated.append(rival)
        return standing

    # ------------------------------------------------------------- decisions
    def next_step(self) -> Step:
        """The engine's decision about what happens next. Pure: nothing moves."""
        rule = self.rule
        if len(self._observed) >= rule.max_steps:
            return Step("stop", reason=f"the step limit ({rule.max_steps}) is reached")
        if self._belief[CATCH_ALL] >= rule.catch_all_alarm:
            return Step("propose", reason=(
                f"the catch-all holds {self._belief[CATCH_ALL]:.3f} (alarm "
                f"{rule.catch_all_alarm}): the named explanations predicted the outcomes "
                "poorly, so the next step is new explanations, not more analyses"))
        unrun = [a for a in self._analyses.values()
                 if a.id not in self._observed and a.id not in self._withdrawn
                 and self._affordable(a)]
        pending = tuple(a.id for a in unrun if self.missing(a.id))
        standing = self._standing()
        if standing is not None and standing.posterior >= rule.accept_at:
            leader = standing.leader
            for rival in standing.untested:
                options = [(self.severity(leader, rival, a.id), a) for a in unrun
                           if self.ready(a.id) and self.severe(leader, rival, a.id)]
                if options:
                    severity, analysis = min(options, key=lambda item: (
                        -item[0], -self.power(leader, rival, item[1].id),
                        -self.expected_gain(item[1].id) / item[1].cost, item[1].id))
                    return Step("run", analysis.id, Purpose.CONTRADICT, against=rival,
                                expected_gain=self.expected_gain(analysis.id),
                                severity=severity, reason=(
                                    f"{leader} leads at {standing.posterior:.3f} but has "
                                    f"not been severely tested against {rival}"))
            if rule.require_replication:
                for rival in standing.unreplicated:
                    tests = set(standing.tested.get(rival, ()))
                    options = [a for a in unrun if a.replicates in tests
                               and self.ready(a.id)]
                    if options:
                        analysis = min(options, key=lambda a: (
                            -self.expected_gain(a.id) / a.cost, a.id))
                        return Step("run", analysis.id, Purpose.REPLICATE, against=rival,
                                    expected_gain=self.expected_gain(analysis.id),
                                    reason=(f"the test of {leader} against {rival} "
                                            f"({analysis.replicates}) is not yet "
                                            "replicated"))
            # A leader no observation has borne on stands on its prior: go on looking for
            # an analysis that bears on it rather than stopping there.
            unborne = (self._explanations[leader].role is Role.SUBSTANTIVE
                       and not self._bearing_designs(leader))
            blocked = bool(standing.untested) or (
                rule.require_replication and bool(standing.unreplicated))
            if blocked and pending:
                return Step("declare", pending=pending, reason=(
                    "the leader still needs a severe test or a replication, and these "
                    "analyses cannot be valued until their predictions are sealed"))
            if not unborne:
                return Step("stop", reason="the stopping rule is met" if not blocked else
                            "the leader is above the threshold and no available analysis "
                            "can supply the missing test")
        ranked = self.ranked()
        if ranked and ranked[0].gain_per_cost >= rule.min_gain:
            best = ranked[0]
            return Step("run", best.analysis_id, Purpose.DISCRIMINATE,
                        expected_gain=best.expected_gain,
                        reason=f"{best.expected_gain:.3f} bits expected at cost "
                               f"{best.cost:g}")
        if pending:
            return Step("declare", pending=pending, reason=(
                "no ready analysis is worth running, and these cannot be valued until "
                "their predictions are sealed"))
        return Step("stop", reason="no remaining analysis is expected to yield "
                    f"{rule.min_gain} bits per unit of cost")

    # ------------------------------------------------------------------ writes
    def add_analysis(self, analysis: Analysis) -> None:
        """Record an analysis the inquiry may run. It needs predictions before it can."""
        self._check_analysis(analysis)
        self._commit("analysis_added", {"analysis": analysis.as_dict()})
        self._analyses[analysis.id] = analysis

    def declare(self, analysis_id: str,
                predictions: Mapping[str, Mapping[str, float]]) -> str:
        """Seal what each explanation predicts the analysis will show. Returns the digest."""
        analysis = self.analysis(analysis_id)
        if analysis_id in self._observed:
            raise InquiryRefused("INQ115", f"{analysis_id} was observed at step "
                                 f"{self._observed[analysis_id].step}")
        if not predictions:
            raise InquiryRefused("INQ112", f"{analysis_id}: no predictions given")
        existing = self._declared.get(analysis_id, {})
        for h in predictions:
            self.explanation(h)
        # In the inquiry's own order, never the caller's: a replay reads the predictions
        # back from canonical JSON, whose keys are sorted, and must rebuild the same state.
        order = list(self._explanations)
        sealed: dict[str, dict[str, float]] = {}
        for h in sorted(predictions, key=order.index):
            row = predictions[h]
            explanation = self.explanation(h)
            if explanation.role is Role.CATCH_ALL:
                raise InquiryRefused("INQ114", f"{analysis_id}")
            if not self.bears_on(h, analysis_id):
                raise InquiryRefused("INQ113", (
                    f"{_article(analysis.design.value)} {analysis.design.value} analysis "
                    f"({analysis_id}) cannot license {_article(explanation.claim_kind.value)} "
                    f"{explanation.claim_kind.value} claim ({h})"))
            if h in existing:
                raise InquiryRefused("INQ116", f"{h} on {analysis_id}")
            sealed[h] = self._distribution(analysis, h, row)
        # The values are sealed exactly as declared and normalised from them on use. Sealing
        # the normalised values instead would make a replay normalise them a second time,
        # and a last-bit change is enough to break the digest.
        digest = _digest({"analysis": analysis_id, "predictions": sealed})
        self._commit("declared", {"analysis_id": analysis_id, "predictions": sealed,
                                  "digest": digest})
        self._sealed.setdefault(analysis_id, {}).update(sealed)
        self._declared.setdefault(analysis_id, {}).update(
            {h: _normalised(row) for h, row in sealed.items()})
        return digest

    def observe(self, analysis_id: str, outcome: str, *, evidence_ref: str = "",
                result_digest: str = "") -> Update:
        """Apply one outcome to belief, using the sealed predictions and nothing else."""
        analysis = self.analysis(analysis_id)
        if analysis_id in self._observed:
            raise InquiryRefused("INQ131", f"{analysis_id} at step "
                                 f"{self._observed[analysis_id].step}")
        if analysis_id in self._withdrawn:
            raise InquiryRefused("INQ133", f"{analysis_id} was withdrawn: "
                                 f"{self._withdrawn[analysis_id]}")
        if outcome not in analysis.outcomes:
            raise InquiryRefused("INQ130", f"{outcome!r} is not among "
                                 f"{list(analysis.outcomes)}")
        missing = self.missing(analysis_id)
        if missing:
            raise InquiryRefused("INQ110", f"{analysis_id} lacks predictions for "
                                 f"{list(missing)}")
        if len(self._observed) >= self.rule.max_steps or not self._affordable(analysis):
            raise InquiryRefused("INQ132", f"spent {self._spent:g}, step "
                                 f"{len(self._observed)}; {analysis_id} costs "
                                 f"{analysis.cost:g}")
        recommended = self.next_step()
        if recommended.action == "run" and recommended.analysis_id == analysis_id:
            purpose, against = recommended.purpose, recommended.against
        else:
            purpose, against = Purpose.UNRECOMMENDED, ""
        table = self._table(analysis)
        expected = expected_information_gain(self._belief, table)
        prior = dict(self._belief)
        joint = {h: prior[h] * table[h][outcome] for h in prior}
        total = math.fsum(joint.values())
        posterior = {h: joint[h] / total for h in prior}
        rows = self._declared.get(analysis_id, {})
        held = tuple(h for h, e in self._explanations.items()
                     if e.role is Role.SUBSTANTIVE and not self.bears_on(h, analysis_id))
        refuted = tuple(h for h in self._explanations
                        if h in rows and prior[h] > 0.0 and rows[h][outcome] == 0.0)
        # Against the rest, for every explanation still alive. A refuted explanation has
        # no factor at all (it cannot be moved); None is an unbounded one.
        bayes: dict[str, float | None] = {}
        for h in prior:
            rest = 1.0 - prior[h]
            if prior[h] <= 0.0 or rest <= 0.0:
                continue
            against_h = (total - joint[h]) / rest
            factor = table[h][outcome] / against_h if against_h > 0.0 else math.inf
            bayes[h] = factor if math.isfinite(factor) else None
        information = math.fsum(posterior[h] * math.log2(posterior[h] / prior[h])
                                for h in prior if posterior[h] > 0.0 and prior[h] > 0.0)
        licensing = {h: (self.grade(h, analysis_id) or Licensing.DIRECT).value
                     for h, e in self._explanations.items() if e.role is Role.SUBSTANTIVE}
        update = Update(
            step=len(self._observed) + 1, analysis_id=analysis_id, design=analysis.design,
            outcome=outcome, purpose=purpose, against=against,
            recommended=recommended.analysis_id if recommended.action == "run" else "",
            expected_gain=expected, information=max(0.0, information), prior=prior,
            posterior=posterior, likelihood={h: table[h][outcome] for h in prior},
            bayes_factor=bayes, held_fixed=held, refuted=refuted, licensing=licensing,
            cost=analysis.cost, evidence_ref=str(evidence_ref or ""),
            result_digest=str(result_digest or ""),
            declaration=_digest({"analysis": analysis_id,
                                 "predictions": self._sealed.get(analysis_id, {})}))
        self._commit("observed", update.as_dict())
        self._observed[analysis_id] = update
        self._belief = posterior
        self._spent += analysis.cost
        return update

    def withdraw(self, analysis_id: str, reason: str) -> None:
        """Take an analysis out of the inquiry because it cannot be run, saying why.

        Withdrawing changes no belief: an analysis that never ran has no outcome. It does
        change what the inquiry can still learn, and the conclusion lists it.
        """
        self.analysis(analysis_id)
        if analysis_id in self._observed or analysis_id in self._withdrawn:
            raise InquiryRefused("INQ133", f"{analysis_id} already has an outcome or "
                                 "was already withdrawn")
        if not _text(reason):
            raise InquiryRefused("INQ133", f"{analysis_id}: no reason given")
        self._commit("withdrawn", {"analysis_id": analysis_id, "reason": reason})
        self._withdrawn[analysis_id] = reason

    def admit(self, explanations: Sequence[Explanation],
              shares: Mapping[str, float]) -> None:
        """Add explanations, each carved out of the catch-all's current mass.

        A new explanation is given no likelihood for analyses already observed: the data
        it was invented to fit are already counted in the catch-all's mass, and its share
        of that mass is all the credit it gets for them.
        """
        new = list(explanations)
        if not new:
            raise InquiryRefused("INQ140", "no explanations given")
        for e in new:
            if not isinstance(e, Explanation) or e.role is Role.CATCH_ALL:
                raise InquiryRefused("INQ140", "only named explanations can be admitted")
            if e.id in self._explanations:
                raise InquiryRefused("INQ140", f"{e.id} is already recorded")
            if e.prior != 0.0:
                raise InquiryRefused("INQ140", f"{e.id}: a late explanation takes a share "
                                     "of the catch-all, not a prior of its own")
        if set(shares) != {e.id for e in new} or len({e.id for e in new}) != len(new):
            raise InquiryRefused("INQ140", "give exactly one share per new explanation")
        if any(not _real(s) or s <= 0.0 for s in shares.values()):
            raise InquiryRefused("INQ140", "every share must be a positive number")
        total = math.fsum(shares.values())
        if total > 1.0 - self.rule.catch_all_floor + _TOLERANCE:
            raise InquiryRefused("INQ140", f"shares sum to {total:.4f}; the catch-all "
                                 f"must keep at least {self.rule.catch_all_floor} of its "
                                 "mass")
        mass = self._belief[CATCH_ALL]
        belief = dict(self._belief)
        for e in new:
            belief[e.id] = shares[e.id] * mass
        belief[CATCH_ALL] = mass * (1.0 - total)
        self._commit("admitted", {"explanations": [e.as_dict() for e in new],
                                  "shares": dict(shares), "belief": belief})
        for e in new:
            self._explanations[e.id] = e
        # The catch-all stays last, so iteration order (and every digest) is stable.
        self._explanations[CATCH_ALL] = self._explanations.pop(CATCH_ALL)
        self._belief = {h: belief[h] for h in self._explanations}

    # -------------------------------------------------------------- conclusion
    def conclude(self) -> Conclusion:
        """The verdict the stopping rule supports now, and what it licenses."""
        rule = self.rule
        step = self.next_step()
        final = step.action != "run"
        standing = self._standing()
        belief = dict(self._belief)
        limitations = [
            "the posterior is conditional on the predictions sealed before each analysis "
            "ran; they are the proposer's commitments, not measurements"]
        catch_all = belief[CATCH_ALL]
        if catch_all > 0.0:
            limitations.append(f"the catch-all keeps {catch_all:.3f}: an explanation "
                               "nobody named is not ruled out")
        needs: dict[str, tuple[str, ...]] = {}
        if standing is None:
            return self._conclusion(Verdict.NEEDS_HYPOTHESES, None, belief, (), {}, {},
                                    (), (), needs, final,
                                    "every named explanation has been refuted",
                                    limitations)
        leader = self._explanations[standing.leader]
        designs = self._bearing_designs(leader.id)
        licensing: Licensing | None = None
        if leader.role is Role.SUBSTANTIVE:
            grades = {LICENSING[leader.claim_kind][d] for d in designs}
            licensing = (Licensing.UNLICENSED if not designs
                         else Licensing.EXTRAPOLATED if Licensing.EXTRAPOLATED in grades
                         else Licensing.DIRECT)
        for rival in standing.untested:
            explanation = self._explanations[rival]
            if explanation.role is Role.SUBSTANTIVE and not any(
                    self.bears_on(rival, a) for a in self._analyses):
                needs[rival] = tuple(sorted(
                    d.value for d, g in LICENSING.get(explanation.claim_kind, {}).items()
                    if g is Licensing.DIRECT))
        reasons: list[str] = []
        if catch_all >= rule.catch_all_alarm:
            verdict = Verdict.NEEDS_HYPOTHESES
            reasons.append(f"the catch-all holds {catch_all:.3f}, at or above the alarm "
                           f"{rule.catch_all_alarm}")
        elif standing.posterior >= rule.accept_at:
            if standing.untested:
                reasons.append("not severely tested against "
                               + ", ".join(standing.untested))
            if rule.require_replication and standing.unreplicated:
                reasons.append("the tests against " + ", ".join(standing.unreplicated)
                               + " were not replicated")
                failed = [a for r in standing.unreplicated for a in standing.failed[r]]
                if failed:
                    reasons.append("replications that did not favour it: "
                                   + ", ".join(dict.fromkeys(failed)))
            if licensing is Licensing.UNLICENSED:
                reasons.append("no observation bore on it; its standing is its prior")
            verdict = Verdict.PROVISIONAL if reasons else Verdict.ACCEPTED
            if verdict is Verdict.ACCEPTED:
                reasons.append(f"above {rule.accept_at}, severely tested against every "
                               "live rival"
                               + (" and replicated" if rule.require_replication else ""))
        else:
            verdict = Verdict.UNDETERMINED
            reasons.append(f"the leader holds {standing.posterior:.3f}, below "
                           f"{rule.accept_at}")
        if not final:
            reasons.append(f"stopped before the stopping rule was met; the engine's next "
                           f"step was to run {step.analysis_id}")
        elif step.reason and verdict in (Verdict.PROVISIONAL, Verdict.UNDETERMINED):
            reasons.append(step.reason)
        for rival, wanted in needs.items():
            limitations.append(
                f"{rival} could not be tested here: no analysis's design licenses a "
                f"{self._explanations[rival].claim_kind.value} claim; that needs one of "
                f"{list(wanted)}")
        if licensing is Licensing.EXTRAPOLATED:
            limitations.append("part of the evidence reaches the claim only by "
                               "extrapolation, which a release must declare")
        for analysis_id, why in self._withdrawn.items():
            limitations.append(f"{analysis_id} could not be run: {why}")
        return self._conclusion(
            verdict, leader, belief, designs, standing.tested, standing.replicated,
            tuple(standing.untested), tuple(standing.unreplicated), needs, final,
            "; ".join(reasons), limitations, licensing)

    def _bearing_designs(self, explanation_id: str) -> tuple[StudyDesign, ...]:
        """Designs of the observations that actually moved this explanation's belief."""
        designs: list[StudyDesign] = []
        for update in self._observed.values():
            if (not self.bears_on(explanation_id, update.analysis_id)
                    or explanation_id not in update.bayes_factor):
                continue
            factor = update.bayes_factor[explanation_id]
            # None is an unbounded factor and 0.0 a refutation: both moved belief.
            moved = factor is None or factor == 0.0 or abs(math.log(factor)) > 1e-9
            if moved and update.design not in designs:
                designs.append(update.design)
        return tuple(designs)

    def _conclusion(self, verdict: Verdict, leader: Explanation | None,
                    belief: Mapping[str, float], designs: tuple[StudyDesign, ...],
                    tested: Mapping[str, Sequence[str]],
                    replicated: Mapping[str, Sequence[str]], untested: tuple[str, ...],
                    unreplicated: tuple[str, ...], needs: Mapping[str, tuple[str, ...]],
                    final: bool, reason: str, limitations: Sequence[str],
                    licensing: Licensing | None = None) -> Conclusion:
        p = belief[leader.id] if leader is not None else 0.0
        certainty = _from_posterior(p, self.rule.accept_at)
        if leader is not None and leader.role is Role.SUBSTANTIVE:
            if not designs:
                certainty = Certainty.UNCERTAIN
            else:
                cap = min((MAX_CERTAINTY.get(d, Certainty.UNCERTAIN) for d in designs),
                          key=lambda c: c.rank)
                certainty = _weaker(certainty, cap)
        if verdict is Verdict.PROVISIONAL:
            certainty = _weaker(certainty, Certainty.TENTATIVE)
        elif verdict is not Verdict.ACCEPTED:
            certainty = Certainty.UNCERTAIN
        return Conclusion(
            verdict=verdict, question=self.question,
            leader=leader.id if leader is not None else "",
            proposition=leader.proposition if leader is not None else "",
            leader_posterior=p, posterior=dict(belief),
            role=leader.role if leader is not None else None,
            claim_kind=leader.claim_kind if leader is not None else None,
            licensing=licensing, certainty=certainty, designs=designs,
            severe_tests={k: tuple(v) for k, v in tested.items() if v},
            replications={k: tuple(v) for k, v in replicated.items() if v},
            untested=untested, unreplicated=unreplicated, needs=dict(needs), final=final,
            reason=reason, limitations=tuple(limitations), steps=len(self._observed),
            spent=self._spent, trail_head=self.head)

    # ---------------------------------------------------------- serialisation
    def as_dict(self) -> dict[str, Any]:
        """The trail is the state: ``from_dict`` rebuilds everything else by replay."""
        return {"schema": 1, "inquiry_id": self.inquiry_id, "trail": list(self.trail),
                "belief": dict(self._belief), "head": self.head}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any], *,
                  recorder: InquiryRecorder | None = None) -> "Inquiry":
        """Replay a trail, refusing it if any entry or the final belief does not recompute."""
        trail = list(data.get("trail") or ())
        if data.get("schema") != 1 or not trail or trail[0].get("event") != "opened":
            raise InquiryRefused("INQ150", "not an inquiry trail")
        prev = ""
        for seq, entry in enumerate(trail):
            body = {"seq": entry.get("seq"), "event": entry.get("event"),
                    "body": entry.get("body"), "prev": entry.get("prev")}
            if entry.get("seq") != seq or entry.get("prev") != prev \
                    or entry.get("digest") != _digest(body):
                raise InquiryRefused("INQ150", f"trail entry {seq} does not verify")
            prev = entry["digest"]
        opened = trail[0]["body"]
        try:
            inquiry = cls(opened["question"],
                          [Explanation.from_dict(e) for e in opened["explanations"]],
                          [Analysis.from_dict(a) for a in opened["analyses"]],
                          StoppingRule.from_dict(opened["rule"]),
                          inquiry_id=opened["inquiry_id"])
        except (KeyError, TypeError, ValueError) as exc:
            raise InquiryRefused("INQ150", f"the opening entry is malformed: {exc}") from exc
        _same(inquiry._trail[0]["body"], opened, "opened")
        for entry in trail[1:]:
            event, body = entry["event"], entry["body"]
            if event == "analysis_added":
                inquiry.add_analysis(Analysis.from_dict(body["analysis"]))
            elif event == "declared":
                inquiry.declare(body["analysis_id"], body["predictions"])
            elif event == "observed":
                inquiry.observe(body["analysis_id"], body["outcome"],
                                evidence_ref=body.get("evidence_ref", ""),
                                result_digest=body.get("result_digest", ""))
            elif event == "admitted":
                inquiry.admit([Explanation.from_dict(e) for e in body["explanations"]],
                              body["shares"])
            elif event == "withdrawn":
                inquiry.withdraw(body["analysis_id"], body["reason"])
            else:
                raise InquiryRefused("INQ150", f"unknown trail event {event!r}")
            _same(inquiry._trail[-1]["body"], body, event)
        if "belief" in data:
            _same(inquiry.belief, data["belief"], "final belief")
        # Every body recomputed to within float rounding, so the stored chain stands: a
        # last-bit difference in a logarithm on another platform must not move the head.
        inquiry._trail = [json.loads(_canonical(entry)) for entry in trail]
        if data.get("head") not in (None, "", inquiry.head):
            raise InquiryRefused("INQ150", "the recorded head is not the trail's head")
        inquiry._recorder = recorder
        return inquiry

    def verify(self) -> None:
        """Recompute everything from the trail; raises ``InquiryRefused`` on a mismatch."""
        type(self).from_dict(self.as_dict())

    def describe(self) -> str:
        lines = [f"inquiry {self.inquiry_id}: {self.question}",
                 "  belief: " + " · ".join(f"{h} {p:.3f}" for h, p in sorted(
                     self._belief.items(), key=lambda item: (-item[1], item[0])))]
        for update in self._observed.values():
            lines.append(f"  {update.step}. {update.analysis_id} -> {update.outcome} "
                         f"({update.purpose.value}, {update.information:.3f} of "
                         f"{update.expected_gain:.3f} bits expected)")
        return "\n".join(lines)

    # --------------------------------------------------------------- internals
    def _check_analysis(self, analysis: Analysis) -> None:
        if not isinstance(analysis, Analysis):
            raise InquiryRefused("INQ104", "analyses must be Analysis objects")
        if analysis.id in self._analyses:
            raise InquiryRefused("INQ104", f"{analysis.id} is already recorded")
        if analysis.replicates:
            original = self._analyses.get(analysis.replicates)
            if original is None:
                raise InquiryRefused("INQ104", f"{analysis.id} replicates "
                                     f"{analysis.replicates!r}, which is not recorded")
            if original.outcomes != analysis.outcomes:
                raise InquiryRefused("INQ104", f"{analysis.id} replicates "
                                     f"{analysis.replicates} with different outcomes")

    def _distribution(self, analysis: Analysis, explanation_id: str,
                      row: Mapping[str, float]) -> dict[str, float]:
        if not isinstance(row, Mapping) or set(row) != set(analysis.outcomes):
            raise InquiryRefused("INQ112", f"{explanation_id} on {analysis.id}: outcomes "
                                 f"must be exactly {list(analysis.outcomes)}")
        values = [row[o] for o in analysis.outcomes]
        if not all(_real(v) and 0.0 <= v <= 1.0 for v in values):
            raise InquiryRefused("INQ112", f"{explanation_id} on {analysis.id}: every "
                                 "probability must be a finite number in [0, 1]")
        total = math.fsum(values)
        if abs(total - 1.0) > _TOLERANCE:
            raise InquiryRefused("INQ112", f"{explanation_id} on {analysis.id}: sums to "
                                 f"{total:.6f}")
        return {o: float(row[o]) for o in analysis.outcomes}

    def _commit(self, event: str, body: Mapping[str, Any]) -> None:
        body = json.loads(_canonical(body))
        entry: dict[str, Any] = {"seq": len(self._trail), "event": event, "body": body,
                                 "prev": self.head}
        entry["digest"] = _digest(entry)
        if self._recorder is not None:
            self._recorder.record(self, event, json.loads(_canonical(entry)))
        self._trail.append(entry)


def _normalised(row: Mapping[str, float]) -> dict[str, float]:
    total = math.fsum(row.values())
    return {o: p / total for o, p in row.items()}


def _article(word: str) -> str:
    return "an" if str(word)[:1].lower() in "aeiou" else "a"


def _weaker(first: Certainty, second: Certainty) -> Certainty:
    return first if first.rank >= second.rank else second


def _from_posterior(p: float, accept_at: float) -> Certainty:
    if p >= accept_at:
        return Certainty.STRONG
    if p >= 0.8:
        return Certainty.MODERATE
    if p >= 0.5:
        return Certainty.TENTATIVE
    return Certainty.UNCERTAIN


def _same(left: Any, right: Any, where: str) -> None:
    """Equal up to float rounding, recursively; anything else is a replay mismatch."""
    if isinstance(left, Mapping) and isinstance(right, Mapping):
        if set(left) != set(right):
            raise InquiryRefused("INQ150", f"{where}: fields differ")
        for key in left:
            _same(left[key], right[key], where)
        return
    if isinstance(left, (list, tuple)) and isinstance(right, (list, tuple)):
        if len(left) != len(right):
            raise InquiryRefused("INQ150", f"{where}: lengths differ")
        for a, b in zip(left, right):
            _same(a, b, where)
        return
    if _real(left) and _real(right):
        if abs(float(left) - float(right)) > _REPLAY_TOLERANCE:
            raise InquiryRefused("INQ150", f"{where}: {left!r} recomputes as {right!r}")
        return
    if left != right:
        raise InquiryRefused("INQ150", f"{where}: {left!r} recomputes as {right!r}")


def run_inquiry(inquiry: Inquiry, execute: Callable[[Analysis, Step], Executed], *,
                declare: Callable[[Inquiry, Step], None] | None = None,
                propose: Callable[[Inquiry, Step], None] | None = None,
                max_proposals: int = 3, max_model_turns: int = 16) -> Conclusion:
    """Drive an inquiry until the engine stops it, and return its conclusion.

    ``execute`` runs one analysis and classifies the result into a declared outcome; it is
    where the execution broker sits. ``declare`` seals missing predictions and ``propose``
    admits new explanations; both are the model's turns, and both are bounded, because a
    model that keeps adding analyses would otherwise keep an inquiry open for ever.
    Without them, a step that needs the model ends the run, and the conclusion says why.
    """
    proposals = turns = 0
    while True:
        step = inquiry.next_step()
        if step.action == "run":
            try:
                result = execute(inquiry.analysis(step.analysis_id), step)
            except AnalysisUnavailable as exc:
                inquiry.withdraw(step.analysis_id, str(exc) or type(exc).__name__)
                continue
            inquiry.observe(step.analysis_id, result.outcome,
                            evidence_ref=result.evidence_ref,
                            result_digest=result.result_digest)
            continue
        if turns >= max_model_turns:
            break
        before = inquiry.head
        if step.action == "declare" and declare is not None:
            declare(inquiry, step)
        elif step.action == "propose" and propose is not None \
                and proposals < max_proposals:
            proposals += 1
            propose(inquiry, step)
        else:
            break
        turns += 1
        if inquiry.head == before:
            break                          # the model's turn changed nothing
    return inquiry.conclude()
