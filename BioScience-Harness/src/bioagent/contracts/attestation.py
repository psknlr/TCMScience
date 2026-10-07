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
  artifact's digest, that head and that policy;
* when the artifact declares the operations its skill performed (``provenance.governed``,
  written by :func:`bioagent.governed.run_governed`), the chain records exactly those, for
  that run, before its release: each entry's digest is recomputed from the artifact and
  must match a ``bioscience_operation`` event, and an operation the chain records for the
  run that the artifact leaves out is a refusal. A ledger is what the artifact says the
  skill did; this is the check that the run recorded it so.

The digest is recomputed from the artifact in hand, so an artifact edited after release (a
claim, its provenance, the head it names) no longer matches what the run recorded.

The attestor reads a PSH event store and nothing else. PSH is imported only to open one
from a state directory, through its public ``TrustedKernel``, so the contracts keep no
import-time dependency on PSH and never reach into the kernel's internals.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

from .source_card import canonical_hash

__all__ = ["ATTESTED_EVENTS", "AuditChainAttestor", "OPERATION_EVENT", "operation_digest"]

#: The events a governed run appends once its artifact is final, one per entry point.
ATTESTED_EVENTS = frozenset({"bioscience_artifact_attested", "research_artifact_attested"})

#: The event a governed run appends for each external operation its skill performed or
#: tried to perform (``bioagent.operations``), refused ones included.
OPERATION_EVENT = "bioscience_operation"

#: Fields of an operation entry that describe its record rather than the operation.
_RECORD_FIELDS = ("evidence_sha256", "recorded")


def operation_digest(entry: Mapping[str, Any]) -> str:
    """The digest a governed run records for one operation.

    Taken over the entry's content and not over the two fields that describe the record
    itself, so the run, the artifact and a reviewer holding only the artifact compute the
    same value from the same entry.
    """
    return canonical_hash({k: v for k, v in entry.items() if k not in _RECORD_FIELDS})


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
        for index in range(at + 1, len(records)):
            record = records[index]
            detail = record.detail or {}
            if (record.event_type in ATTESTED_EVENTS
                    and detail.get("artifact_digest") == digest
                    and detail.get("audit_head") == artifact.audit_head
                    and detail.get("policy_id") == artifact.policy_id):
                return _operations_recorded(records[:index], artifact.provenance)
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


def _operations_recorded(records: Sequence[Any], provenance: Any) -> tuple[bool, str]:
    """Whether ``records`` (the chain up to the release) hold the declared operation ledger.

    An artifact from before operations were recorded declares no ledger and is judged on
    its release record alone, so a result published then can still be re-checked.
    """
    governed = provenance.get("governed") if isinstance(provenance, Mapping) else None
    entries = governed.get("operations") if isinstance(governed, Mapping) else None
    if entries is None:
        return True, ""
    anchor = governed.get("run_anchor")
    start = next((i for i, r in enumerate(records) if anchor and r.hash == anchor), None)
    if start is None:
        return False, ("the artifact declares an operation ledger whose run start is not in "
                       "the audit chain before its release")
    chain = [r.detail.get("evidence_sha256") for r in records[start + 1:]
             if r.event_type == OPERATION_EVENT
             and (r.detail or {}).get("run_anchor") == anchor]
    declared = [operation_digest(e) if isinstance(e, Mapping) else "" for e in entries]
    for seq, entry_digest in enumerate(declared, 1):
        if entry_digest not in chain:
            return False, (f"operation {seq} of the artifact's ledger is not recorded in the "
                           "audit chain as the artifact states it")
        chain.remove(entry_digest)
    if chain:
        return False, (f"the audit chain records {len(chain)} operation(s) of this run that "
                       "the artifact's ledger leaves out")
    return True, ""
