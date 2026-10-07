"""What a case reports: each step as it ran, and each drafted claim as the contract judged it."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from ..status import ExecutionStatus

__all__ = ["CaseReport", "CaseStep", "ClaimCheck", "check_drafts"]


@dataclass(frozen=True)
class CaseStep:
    """One step: what it did, what ran it (name and version), and what it produced."""

    name: str
    status: ExecutionStatus
    implementation: str = ""
    detail: str = ""
    record: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "status": self.status.value,
                "implementation": self.implementation, "detail": self.detail,
                "record": dict(self.record)}


@dataclass(frozen=True)
class ClaimCheck:
    """A drafted claim, why it is in the case, and the contract's verdict on it."""

    text: str
    kind: str
    purpose: str
    allowed: bool
    codes: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()
    expected: bool | None = None

    @property
    def as_expected(self) -> bool:
        return self.expected is None or self.allowed == self.expected

    def as_dict(self) -> dict[str, Any]:
        return {"text": self.text, "kind": self.kind, "purpose": self.purpose,
                "allowed": self.allowed, "codes": list(self.codes),
                "reasons": list(self.reasons), "expected": self.expected}


def check_drafts(drafts: Iterable[tuple[Any, str, bool | None]],
                 evidence: Mapping[str, Any]) -> list[ClaimCheck]:
    """Each ``(CandidateClaim, purpose, expected)`` through ``check_claim``."""
    from ..contracts import check_claim

    out = []
    for claim, purpose, expected in drafts:
        verdict = check_claim(claim, evidence)
        out.append(ClaimCheck(text=claim.text, kind=claim.claim_kind, purpose=purpose,
                              allowed=verdict.allowed, codes=tuple(verdict.codes),
                              reasons=tuple(verdict.reason_text), expected=expected))
    return out


@dataclass
class CaseReport:
    """A case's steps and claims, and the inputs it says it ran on."""

    case: str
    title: str
    inputs: str
    steps: list[CaseStep] = field(default_factory=list)
    claims: list[ClaimCheck] = field(default_factory=list)
    limits: list[str] = field(default_factory=list)

    def step(self, name: str) -> CaseStep:
        return next(s for s in self.steps if s.name == name)

    @property
    def claims_as_expected(self) -> bool:
        return all(c.as_expected for c in self.claims)

    def as_dict(self) -> dict[str, Any]:
        return {"case": self.case, "title": self.title, "inputs": self.inputs,
                "steps": [s.as_dict() for s in self.steps],
                "claims": [c.as_dict() for c in self.claims], "limits": self.limits,
                "claims_as_expected": self.claims_as_expected}

    def to_json(self) -> str:
        return json.dumps(self.as_dict(), ensure_ascii=False, indent=1, default=str)

    def markdown(self) -> str:
        out = [f"# {self.title}", "", f"Inputs: {self.inputs}", "", "## Steps", "",
               "| Step | Status | Implementation | Detail |", "| --- | --- | --- | --- |"]
        for s in self.steps:
            out.append(f"| {s.name} | {s.status.value} | {s.implementation or '—'} | "
                       f"{_cell(s.detail)} |")
        out += ["", "## Claims", "",
                "| Claim | Kind | Why it is here | Verdict | Codes |",
                "| --- | --- | --- | --- | --- |"]
        for c in self.claims:
            verdict = "allowed" if c.allowed else "refused"
            out.append(f"| {_cell(c.text)} | {c.kind} | {_cell(c.purpose)} | {verdict} | "
                       f"{', '.join(c.codes) or '—'} |")
        refused = [c for c in self.claims if not c.allowed]
        if refused:
            out += ["", "### Why each refused claim was refused", ""]
            for c in refused:
                out.append(f"- **{c.text}**")
                out += [f"  - {r}" for r in c.reasons]
        if self.limits:
            out += ["", "## What this case does not show", ""]
            out += [f"- {line}" for line in self.limits]
        return "\n".join(out) + "\n"


def _cell(text: str) -> str:
    return " ".join(str(text).split()).replace("|", "\\|")


def _versions(*names: str) -> dict[str, str]:
    """The installed versions of ``names``; absent packages are left out."""
    import importlib.metadata as metadata

    out = {}
    for name in names:
        try:
            out[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            continue
    return out
