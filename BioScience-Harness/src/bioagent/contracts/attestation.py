"""Execution attestation: whether an artifact's run is recorded in a kernel's audit chain.

An artifact names the policy it ran under and the head of the audit chain at release
(``policy_id``, ``audit_head``). The independent validator read the two as an attestation
whenever both were non-empty, so an artifact naming ``policy-never-created`` and the audit
head ``not-a-real-hash`` was ``execution_attested`` and ``release_authorized`` (audit
AUD-13). Two strings are what an artifact says about its run, not what happened. They are
now reported as ``execution_declared``, and an artifact is attested only against an audit
chain:

* the chain verifies end to end;
* the head the artifact names is an event in it;
* after that event, the run recorded an ``…_artifact_attested`` event naming this
  artifact's digest, that head and that policy.

The digest is recomputed from the artifact in hand, so an artifact edited after release (a
claim, its provenance, the head it names) no longer matches what the run recorded.

The attestor reads a PSH event store and nothing else. PSH is imported only to open one
from a state directory, through its public ``TrustedKernel``, so the contracts keep no
import-time dependency on PSH and never reach into the kernel's internals.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

__all__ = ["ATTESTED_EVENTS", "AuditChainAttestor"]

#: The events a governed run appends once its artifact is final, one per entry point.
ATTESTED_EVENTS = frozenset({"bioscience_artifact_attested", "research_artifact_attested"})


class AuditChainAttestor:
    """Checks artifacts against one PSH event store (``TrustedKernel.events``)."""

    def __init__(self, events: Any, *, owner: Any = None) -> None:
        self.events = events
        self._owner = owner               # what close() closes: only what open() opened

    @classmethod
    def open(cls, state_dir: str | Path) -> "AuditChainAttestor":
        """The attestor for the kernel state kept under ``state_dir``."""
        if not (Path(state_dir) / "events.db").is_file():
            raise FileNotFoundError(f"no audit chain under {state_dir}")
        from psh import PSHConfig, TrustedKernel
        kernel = TrustedKernel(PSHConfig(state_dir=Path(state_dir)))
        return cls(kernel.events, owner=kernel)

    def attest(self, artifact: Any) -> tuple[bool, str]:
        """Whether the chain records ``artifact`` as released, and why not if it does not."""
        if not (artifact.policy_id and artifact.audit_head):
            return False, "the artifact names no policy_id/audit_head"
        chain = self.events.verify()
        if not chain:
            return False, f"the audit chain does not verify: {getattr(chain, 'detail', '')}"
        records = self.events.records()
        at = next((i for i, r in enumerate(records) if r.hash == artifact.audit_head), None)
        if at is None:
            return False, (f"the audit head {artifact.audit_head[:16]!r} is not an event in "
                           "the audit chain")
        digest = artifact.digest
        for record in records[at + 1:]:
            detail = record.detail or {}
            if (record.event_type in ATTESTED_EVENTS
                    and detail.get("artifact_digest") == digest
                    and detail.get("audit_head") == artifact.audit_head
                    and detail.get("policy_id") == artifact.policy_id):
                return True, ""
        return False, ("no event after the audit head records this artifact under this "
                       "policy: it was changed after its release, or never released here")

    def close(self) -> None:
        if self._owner is not None:
            self._owner.close()
            self._owner = None

    def __enter__(self) -> "AuditChainAttestor":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
