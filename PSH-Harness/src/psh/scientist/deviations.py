"""Grading a protocol departure, because the differences are not comparable.

``ScientificLedger.observe`` already does the load-bearing half: it computes which
protocol fields changed, refuses a changed analysis that states no reason, and stores the
departure **inside** the observation record atomically — so there is no interval in which a
changed analysis looks preregistered. That is the property that matters and it is not this
module's to improve.

What it does not do is say how much a departure changes what the result *means*, and a
reader given a flat list of changed fields has to work that out for themselves every time.
Adding a covariate is a footnote. Changing the primary endpoint after the data are in is
the finding. Grading them the same way is how a critical departure reaches a manuscript as
a line in a table.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from enum import IntEnum
from typing import Any, Iterable, Mapping

from .models import Protocol

__all__ = ["DeviationSeverity", "GradedDeviation", "grade", "grade_changes", "worst"]


class DeviationSeverity(IntEnum):
    """How much a departure changes what the result means. Ordered, so ``max`` works."""

    MINOR = 0        # a footnote: an added covariate, a reworded exclusion
    MAJOR = 1        # changes the inference's strength: the test, the sample-size argument
    CRITICAL = 2     # changes what was tested: the primary endpoint, the stopping rule

    @property
    def label(self) -> str:
        return self.name.lower()


#: Protocol field -> how much changing it changes the meaning of the result.
#:
#: ``primary_endpoint`` is critical for the obvious reason. ``stopping_criteria`` is
#: critical for a less obvious one: stopping when the result looks good is the departure
#: that manufactures significance without any single step looking wrong.
SEVERITY: Mapping[str, DeviationSeverity] = {
    "primary_endpoint": DeviationSeverity.CRITICAL,
    "stopping_criteria": DeviationSeverity.CRITICAL,
    "exclusion_criteria": DeviationSeverity.CRITICAL,
    "statistical_test": DeviationSeverity.MAJOR,
    "sample_size_assumptions": DeviationSeverity.MAJOR,
    "subgroup_plan": DeviationSeverity.MAJOR,
    "secondary_endpoints": DeviationSeverity.MINOR,
    "covariates": DeviationSeverity.MINOR,
}


@dataclass(frozen=True, slots=True)
class GradedDeviation:
    """One departure, with the weight a reader needs and no protocol content quoted.

    ``planned`` and ``actual`` are deliberately absent. A deviation record travels into
    diagnostics and summaries, and a protocol's exclusion criteria can describe a cohort
    closely enough to matter; the field name and the severity are what a reader acts on.
    ``ScientificLedger`` holds the values, under the persistence gateway, where they
    belong.
    """

    field: str
    severity: DeviationSeverity
    added: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()

    def __str__(self) -> str:
        detail = ""
        if self.added or self.removed:
            parts = []
            if self.added:
                parts.append(f"{len(self.added)} added")
            if self.removed:
                parts.append(f"{len(self.removed)} removed")
            detail = f" ({', '.join(parts)})"
        return f"{self.severity.label}: {self.field} departs from the protocol{detail}"

    def as_dict(self) -> dict[str, Any]:
        return {"field": self.field, "severity": self.severity.label,
                "added": list(self.added), "removed": list(self.removed)}


_SEQUENCES = ("secondary_endpoints", "covariates")


def grade(planned: Protocol, actual: Protocol) -> tuple[GradedDeviation, ...]:
    """Every field in which ``actual`` departs from ``planned``, graded.

    Both arguments are ``Protocol``s rather than a protocol and a loose mapping, so a
    caller cannot compare against something that was never a valid protocol.
    """
    if not isinstance(planned, Protocol) or not isinstance(actual, Protocol):
        raise ValueError("grading compares two Protocols")
    out: list[GradedDeviation] = []
    for field in fields(Protocol):
        name = field.name
        before, after = getattr(planned, name), getattr(actual, name)
        if before == after:
            continue
        severity = SEVERITY.get(name, DeviationSeverity.MINOR)
        if name in _SEQUENCES:
            added = tuple(v for v in after if v not in before)
            removed = tuple(v for v in before if v not in after)
            # An *added* secondary endpoint is the post-hoc shape and outranks a dropped
            # one, which is usually a measurement that could not be taken.
            if name == "secondary_endpoints" and added:
                severity = DeviationSeverity.MAJOR
            out.append(GradedDeviation(field=name, severity=severity, added=added,
                                       removed=removed))
        else:
            out.append(GradedDeviation(field=name, severity=severity))
    return tuple(out)


def grade_changes(changed: Mapping[str, Mapping[str, Any]]) -> tuple[GradedDeviation, ...]:
    """Grade the ``changed_fields`` mapping ``ScientificLedger.observe`` already computes.

    The ledger has done the comparison; this reads its result rather than repeating it, so
    the two cannot come to different conclusions about which fields moved.
    """
    out: list[GradedDeviation] = []
    for name in sorted(changed):
        planned = changed[name].get("planned")
        actual = changed[name].get("actual")
        severity = SEVERITY.get(name, DeviationSeverity.MINOR)
        added: tuple[str, ...] = ()
        removed: tuple[str, ...] = ()
        if name in _SEQUENCES and isinstance(planned, (list, tuple)) \
                and isinstance(actual, (list, tuple)):
            added = tuple(v for v in actual if v not in planned)
            removed = tuple(v for v in planned if v not in actual)
            if name == "secondary_endpoints" and added:
                severity = DeviationSeverity.MAJOR
        out.append(GradedDeviation(field=name, severity=severity, added=added,
                                   removed=removed))
    return tuple(out)


def worst(deviations: Iterable[GradedDeviation]) -> DeviationSeverity | None:
    """The severity a reader should be told first, or ``None`` when nothing departed."""
    found = [d.severity for d in deviations]
    return max(found) if found else None
