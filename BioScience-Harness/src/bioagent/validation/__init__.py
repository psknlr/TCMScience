"""Validation records: what was tested, on what input, and what actually happened.

A registry entry says a capability exists; a passing unit test says a function behaves on
the inputs its author chose. Neither says that the capability ran *here*, on real input,
and gave the right answer. This package is how that is recorded instead of assumed: each
component gets a :class:`Record` of :class:`Check` results, each with one of six statuses,
and the records of a run are written as one report.

    PASS            the check ran, and its outcome is what an independent expectation says
    FAIL            the check ran, and its outcome contradicts the expectation: a defect
    BLOCKED         something outside the code stopped the check (a network policy, an
                    unreachable or rate-limiting host, a credential nobody here holds)
    SKIPPED         the check was deliberately not run in this tier (a live download in
                    the offline tier)
    UNVERIFIED      the check could run, but nothing here establishes its outcome (no
                    reference output, no probe for it)
    NOT_APPLICABLE  the check does not apply to this component

A record is PASS only when every check that applies to it passed. Anything else ranks
above PASS — FAIL, then BLOCKED, UNVERIFIED and SKIPPED — so a skipped or blocked live
download can never be read as a successful one, and a component nobody could check is
never counted as working.
"""

from __future__ import annotations

import json
import os
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

__all__ = ["STATUSES", "PASS", "FAIL", "BLOCKED", "SKIPPED", "UNVERIFIED", "NOT_APPLICABLE",
           "SCHEMA", "Check", "Record", "aggregate", "report", "write_report", "load_report",
           "utc_now"]

PASS, FAIL, BLOCKED, SKIPPED, UNVERIFIED, NOT_APPLICABLE = (
    "PASS", "FAIL", "BLOCKED", "SKIPPED", "UNVERIFIED", "NOT_APPLICABLE")
STATUSES = (PASS, FAIL, BLOCKED, SKIPPED, UNVERIFIED, NOT_APPLICABLE)
SCHEMA = "tcmscience.validation/1"

#: The order in which a check's status dominates a record's: the worst news first.
_RANK = {FAIL: 0, BLOCKED: 1, UNVERIFIED: 2, SKIPPED: 3, PASS: 4, NOT_APPLICABLE: 5}


def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def aggregate(statuses: Iterable[str]) -> str:
    """The status of a set of checks: PASS only if every applicable one passed."""
    seen = [s for s in statuses if s in _RANK]
    applicable = [s for s in seen if s != NOT_APPLICABLE]
    if not applicable:
        return NOT_APPLICABLE
    return min(applicable, key=_RANK.__getitem__)


@dataclass
class Check:
    """One test of one component. ``detail`` says what happened in a sentence; ``evidence``
    holds what it rests on (status codes, digests, counts), so a reader can check it."""

    name: str
    status: str
    detail: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)
    duration_s: float | None = None

    def __post_init__(self) -> None:
        if self.status not in _RANK:
            raise ValueError(f"unknown status {self.status!r}; one of {STATUSES}")

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"name": self.name, "status": self.status, "detail": self.detail}
        if self.evidence:
            out["evidence"] = self.evidence
        if self.duration_s is not None:
            out["duration_s"] = round(self.duration_s, 3)
        return out


@dataclass
class Record:
    """Everything known about one component after a validation run."""

    component: str
    kind: str
    version: str = ""
    input: Any = None
    checks: list[Check] = field(default_factory=list)
    provenance: dict[str, Any] = field(default_factory=dict)
    reproduce: str = ""
    runtime_s: float | None = None
    resources: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def add(self, name: str, status: str, detail: str = "", *,
            duration_s: float | None = None, **evidence: Any) -> Check:
        check = Check(name, status, detail, dict(evidence), duration_s)
        self.checks.append(check)
        return check

    @property
    def status(self) -> str:
        return aggregate(c.status for c in self.checks)

    @property
    def failure_reason(self) -> str | None:
        """The first reason the record is not PASS, from its worst checks."""
        status = self.status
        if status in (PASS, NOT_APPLICABLE):
            return None
        for c in self.checks:
            if c.status == status:
                return f"{c.name}: {c.detail}" if c.detail else c.name
        return None

    def check(self, name: str) -> Check | None:
        return next((c for c in self.checks if c.name == name), None)

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"component": self.component, "kind": self.kind,
                               "version": self.version, "status": self.status}
        reason = self.failure_reason
        if reason:
            out["failure_reason"] = reason
        if self.input is not None:
            out["input"] = self.input
        out["checks"] = [c.as_dict() for c in self.checks]
        for key in ("provenance", "resources"):
            value = getattr(self, key)
            if value:
                out[key] = value
        if self.runtime_s is not None:
            out["runtime_s"] = round(self.runtime_s, 3)
        if self.reproduce:
            out["reproduce"] = self.reproduce
        if self.notes:
            out["notes"] = list(self.notes)
        return out


def _summary(records: list[Record]) -> dict[str, Any]:
    by_status = Counter(r.status for r in records)
    by_check: dict[str, Counter] = {}
    for r in records:
        for c in r.checks:
            by_check.setdefault(c.name, Counter())[c.status] += 1
    return {"records": len(records),
            "status": {s: by_status.get(s, 0) for s in STATUSES},
            "checks": {name: {s: n.get(s, 0) for s in STATUSES if n.get(s)}
                       for name, n in sorted(by_check.items())}}


def report(name: str, records: Iterable[Record], *, tier: str = "",
           environment: Mapping[str, Any] | None = None,
           notes: Iterable[str] = (), extra: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """One report document: the records of a run, their counts, and where it ran."""
    items = list(records)
    doc: dict[str, Any] = {"schema": SCHEMA, "report": name, "generated_at": utc_now(),
                           "tier": tier, "summary": _summary(items)}
    if environment is not None:
        doc["environment"] = dict(environment)
    if notes:
        doc["notes"] = list(notes)
    if extra:
        doc.update(dict(extra))
    doc["records"] = [r.as_dict() for r in items]
    return doc


def write_report(path: str | Path, doc: Mapping[str, Any]) -> Path:
    """Write a report atomically (a reader never sees half of one)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return path


def load_report(path: str | Path) -> dict[str, Any]:
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    if doc.get("schema") != SCHEMA:
        raise ValueError(f"{path} is not a {SCHEMA} report (schema {doc.get('schema')!r})")
    return doc
