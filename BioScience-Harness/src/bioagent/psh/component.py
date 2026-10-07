"""A BioScience component as something PSH's broker can invoke.

``ExecutionBroker.call_tool`` needs an object with a ``manifest`` (the PSH one) and an
``invoke(payload, envelope)``. Everything below that line is BioScience: the payload
becomes keyword arguments, a connector payload names an ``operation`` that is rendered
through the source's typed template, and the call goes through ``Runtime.invoke`` — so
resolution, the BioScience policy kernel and the BioScience event log all still apply.

The one translation that matters is the status. BioScience reports what happened as an
``ExecutionStatus`` and never raises; PSH's loop reasons in exceptions and their types
decide whether a task is retried, refused or escalated. So DENIED is a ``PolicyDenied``
(a fact about authority, never retried), UNAVAILABLE is a ``CapabilityUnavailable``, and
everything else that did not succeed is a ``ContractViolation`` carrying the reason —
bounded, because a component's error text is data it produced. A result that names its
job (``metadata["job"]``, what a job tool answers) is the exception: RUNNING, or
UNAVAILABLE because the executor could not be asked, means the job exists and nothing has
been collected, which PSH records as pending (``PendingResult``) rather than as a violation
or an outage. RUNNING that names no job stays a violation: there would be nothing to
collect.
"""

from __future__ import annotations

from typing import Any, Mapping

from psh.contracts import (
    CapabilityUnavailable, ContractViolation, DegradedResult, PendingResult, PolicyDenied,
    ToolTimeout,
)

from ..status import ExecutionStatus
__all__ = ["BridgedComponent", "arguments_for"]

from .arguments import ArgumentError, arguments_for  # noqa: E402


class BridgedComponent:
    """The PSH-facing side of one BioScience component."""

    def __init__(self, bio: Any, manifest: Any, runtime: Any, *, spec: Any,
                 source: Any = None, events: Any = None) -> None:
        self.bio = bio
        self.manifest = manifest
        self.runtime = runtime
        self.spec = spec
        self.source = source
        self.events = events
        self.calls = 0
        self.last_status: ExecutionStatus | None = None

    # ----------------------------------------------------------------- invoke
    def invoke(self, payload: Any, envelope: Any = None) -> Any:
        # The loop's idempotency key rides in the payload under a ``_psh_`` name that
        # ``arguments_for`` strips, so it never becomes a keyword argument of the
        # entrypoint or a field on the wire. It is handed to the runtime on its own,
        # which records it where a side-effecting backend can recognise a replay.
        key = payload.get("_psh_idempotency_key") if isinstance(payload, Mapping) else None
        try:
            kwargs = arguments_for(payload, source=self.source, component_id=self.manifest.id)
        except ArgumentError as exc:
            raise ContractViolation(str(exc)) from None
        self.calls += 1
        bookkeeping = {"idempotency_key": str(key)} if key else {}
        result = self.runtime.invoke(self.bio.id, spec=self.spec, events=self.events,
                                     **bookkeeping, **kwargs)
        self.last_status = result.status
        return self.value_of(result)

    def value_of(self, result: Any) -> Any:
        """A BioScience ``CallResult`` as a value, or as the exception PSH expects."""
        status = result.status
        if status is ExecutionStatus.SUCCEEDED:
            return result.value
        detail = (result.error or "")[:300]
        name = self.bio.id
        if status is ExecutionStatus.DENIED:
            raise PolicyDenied(f"BioScience policy refused {name}: {detail}")
        pending = self.pending_of(result)
        if pending is not None:
            return pending
        if status is ExecutionStatus.UNAVAILABLE:
            raise CapabilityUnavailable(f"{name} cannot run here: {detail}")
        if status is ExecutionStatus.TIMEOUT:
            # Its own class: a call that timed out may have done its work, and the
            # loop's operation ledger records it as unknown rather than failed.
            raise ToolTimeout(f"{name} timed out: {detail}")
        if status is ExecutionStatus.DEGRADED:
            # Ran with a documented shortfall: a value with a caveat, not a failure —
            # and the caveat travels with the value instead of staying in this log.
            return DegradedResult(value=result.value,
                                  reason=detail or "ran with a documented shortfall")
        if status is ExecutionStatus.RUNNING:
            raise ContractViolation(f"{name} running: {detail or 'no detail'}; it names no "
                                    "job to collect, so it is not pending work")
        raise ContractViolation(f"{name} {status.value.lower()}: {detail or 'no detail'}")

    def pending_of(self, result: Any) -> PendingResult | None:
        """The pending work a result names, or None when it names none.

        Work is pending when the result names its job (``metadata["job"]``) and is RUNNING
        (the job has not finished) or UNAVAILABLE (its executor could not be asked, or an
        artefact could not be fetched, for now). In both, the job exists and nothing has
        been collected; failing the call on an outage would claim an outcome nobody saw.
        """
        metadata = result.metadata if isinstance(result.metadata, Mapping) else {}
        job = metadata.get("job")
        if result.status not in (ExecutionStatus.RUNNING, ExecutionStatus.UNAVAILABLE) \
                or not isinstance(job, Mapping) or not job:
            return None
        detail = (result.error or "")[:300]
        try:
            return PendingResult(reference=dict(job), reason=detail or (
                "the work has not finished" if result.status is ExecutionStatus.RUNNING
                else "the work could not be collected now"))
        except ValueError as exc:
            raise ContractViolation(f"{self.bio.id} reported pending work with a reference "
                                    f"PSH cannot record: {exc}") from None

    def __repr__(self) -> str:  # pragma: no cover - diagnostics
        return f"<BridgedComponent {self.manifest.id} via {self.bio.runtime.backend}>"
