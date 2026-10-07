"""Long jobs under PSH: a job tool admitted through the bridge, its grants and its restarts.

A job tool (``bioagent.backends.jobtool``) is admitted like any component, with
``BioScienceBridge.admit(manifest, jobs=JobTool(...))``, in process or in an isolated
child. Its calls then go through PSH's broker like any other: classified, gated, budgeted,
audited. What is particular to it:

* **A call whose job has not finished is pending, not a result and not a violation.** The
  bridge turns the runtime's RUNNING answer, which names its job, into PSH's
  ``PendingResult``; the broker records ``tool_call_pending`` with the job reference in the
  audit chain and raises ``ResultPending``; the loop keeps the task WAITING and collects it
  later by naming the reference's digest. Collected and validated, the artefacts come back
  as the call's value with their SHA-256 digests.
* **A cancellation needs the recorded grant.** :func:`grant_cancellation` records who
  permits cancelling which job and why, in PSH's audit chain first and then in the tool's
  trace, and returns the grant's id; a cancel call names that id, and the tool refuses a
  cancellation whose grant it does not hold (DENIED, so ``PolicyDenied`` in PSH).
* **A restart finds the open jobs and resumes collecting them.** :func:`reconcile` reads
  the trace (``open_jobs``) and records, in the operation ledger, every open job of a run
  whose call died before it could report the job as pending. The resumed loop then collects
  those jobs instead of refusing them as in doubt, and never submits them again.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping

from psh.contracts import ContractViolation

from ..backends.jobs import JobRef, open_jobs
from ..backends.jobtool import JobTool, reference_digest
from .arguments import ArgumentError, job_arguments
from .component import BridgedComponent

__all__ = ["BridgedJobComponent", "Reconciliation", "GRANT_EVENT", "RECONCILED_EVENT",
           "grant_cancellation", "reconcile"]

#: The audit-chain events this module writes.
GRANT_EVENT = "job_cancel_granted"
RECONCILED_EVENT = "job_reconciled"


class BridgedJobComponent(BridgedComponent):
    """The PSH-facing side of a job tool: each call goes through the tool's own runtime.

    ``runtime`` is ``JobTool.runtime``, where the tool's mechanism is served by the job
    backend; resolution, the BioScience policy kernel and the event log apply to every call
    as they do to any component. In an isolated bridge the kernel runs the child instead and
    this object keeps the tool, which is where grants are recorded.
    """

    def __init__(self, bio: Any, manifest: Any, runtime: Any, *, spec: Any, tool: JobTool,
                 events: Any = None) -> None:
        super().__init__(bio, manifest, runtime, spec=spec, events=events)
        self.tool = tool

    def invoke(self, payload: Any, envelope: Any = None) -> Any:
        try:
            kwargs = job_arguments(payload, component_id=self.manifest.id)
        except ArgumentError as exc:
            raise ContractViolation(str(exc)) from None
        key = payload.get("_psh_idempotency_key")
        self.calls += 1
        bookkeeping = {"idempotency_key": str(key)} if key else {}
        result = self.runtime.invoke(self.bio.id, spec=self.spec, events=self.events,
                                     **bookkeeping, **kwargs)
        self.last_status = result.status
        return self.value_of(result)


def _tool_of(component: Any) -> JobTool:
    tool = getattr(component, "tool", None)
    if not isinstance(tool, JobTool):
        raise ValueError(f"{getattr(component, 'manifest', component)!r} is not a job tool")
    return tool


def _in_chain(kernel: Any, run_id: str, event: str, key: str, value: str) -> bool:
    """Whether the run's audit chain holds ``event`` with ``detail[key] == value``."""
    for row in reversed(kernel.events.by_run(run_id)):
        if row["event_type"] != event:
            continue
        try:
            if json.loads(row["detail"] or "{}").get(key) == value:
                return True
        except (TypeError, ValueError):
            continue
    return False


def grant_cancellation(kernel: Any, component: Any, job_id: str, *, granted_by: str,
                       reason: str, run_id: str = "") -> str:
    """Record permission to cancel one job: in PSH's audit chain, then in the tool's trace.

    The chain is written first and read back. If it does not hold the grant, nothing is
    written to the trace and nothing may be cancelled: a permission the tamper-evident log
    cannot show was never given. Returns the grant id a cancel call names.
    """
    tool = _tool_of(component)

    def witness(grant_id: str, ref: JobRef) -> None:
        kernel.audit(GRANT_EVENT, run_id=run_id, component_id=component.manifest.id,
                     detail={"grant_id": grant_id, "job_id": ref.job_id,
                             "granted_by": granted_by[:200], "reason": reason[:300],
                             "reference_sha256": reference_digest(ref.to_dict())})
        if not _in_chain(kernel, run_id, GRANT_EVENT, "grant_id", grant_id):
            raise RuntimeError(f"the audit chain does not hold grant {grant_id}; refusing "
                               "to record a permission the chain cannot show")

    return tool.grant_cancellation(job_id, granted_by=granted_by, reason=reason,
                                   witness=witness)


@dataclass(frozen=True)
class Reconciliation:
    """What a restart learned from a job tool's trace about one run's calls."""

    #: ledger keys moved from in doubt to pending: their jobs exist and will be collected
    pending: tuple[str, ...] = ()
    #: ledger keys already pending under the job's own reference: nothing to do
    already: tuple[str, ...] = ()
    #: ledger keys whose record disagrees with the trace (another reference, or finished)
    conflicts: tuple[str, ...] = ()
    #: open jobs of the run that the ledger never recorded a call for
    unrecorded: tuple[str, ...] = ()
    #: requests the trace holds with no answer: the job may or may not exist, and only the
    #: call that made the request can submit it again with the same submission id
    unconfirmed: tuple[Mapping[str, str], ...] = ()


def reconcile(ledger: Any, component: Any, *, run_id: str,
              audit: Any = None) -> Reconciliation:
    """Match a run's open jobs (``open_jobs``) to the operation ledger after a restart.

    A call that died between the executor accepting a job and the loop recording it as
    pending leaves the ledger RUNNING or UNKNOWN — in doubt — and a resumed loop refuses to
    run it again rather than guess. The trace knows better: it holds the job, under the
    call's key. Such a record becomes PENDING with the job's reference digest, so the
    resumed loop collects that job. Nothing else changes: a record that disagrees with the
    trace is reported, never overwritten, and a request with no answer stays in doubt.
    ``audit`` (the kernel's sink) records each change as ``job_reconciled``.
    """
    from psh.runtime import OperationState

    tool = _tool_of(component)
    prefix = f"{run_id}:"
    pending, already, conflicts, unrecorded = [], [], [], []
    with tool.open() as controller:
        found = open_jobs(controller.events)
    for ref in found.jobs:
        key = found.keys.get(ref.job_id, "")
        if not key.startswith(prefix):
            continue
        digest = reference_digest(ref.to_dict())
        record = ledger.get(key)
        if record is None:
            unrecorded.append(ref.job_id)
        elif record.state is OperationState.PENDING:
            (already if record.result_digest == digest else conflicts).append(key)
        elif record.in_doubt:
            ledger.pend(key, digest)
            pending.append(key)
            if audit is not None:
                audit(RECONCILED_EVENT, run_id=run_id, component_id=component.manifest.id,
                      detail={"operation": key, "job_id": ref.job_id,
                              "was": record.state.value, "reference_sha256": digest})
        else:
            conflicts.append(key)
    return Reconciliation(
        pending=tuple(pending), already=tuple(already), conflicts=tuple(conflicts),
        unrecorded=tuple(unrecorded),
        unconfirmed=tuple(r for r in found.unconfirmed
                          if str(r.get("idempotency_key", "")).startswith(prefix)))
