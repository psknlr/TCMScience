"""Per-operation governance inside a governed skill run: one door for every external call.

:func:`~bioagent.governed.run_governed` admits a skill by its manifest, its entrypoint and
its pin, and verifies the artifact the skill returns. In between it called
``fn(**arguments)``, and what the skill did in there was nobody's business: a sequence sent
to the ESM Atlas, an entry fetched from RCSB, a ColabFold run talking to its MSA server
were not checked against the hosts the manifest declares, passed neither BioScience's
policy kernel nor PSH's egress gate, and left no record. A skill that declared
``network: []`` could reach any host, and its artifact was released all the same.
Artifact governance is not execution governance.

This module is the one door those calls go through. A pipeline names each external call by
the component that performs it::

    raw = operation(ESMATLAS_FOLD, esmatlas_fold, sequence=seq, timeout=timeout)

Outside a governed run nothing is installed, and the implementation runs exactly as it
always has: the command line and direct library calls are unchanged. Inside one,
:func:`run_governed` installs an :class:`OperationBroker` for as long as the skill runs —
a context variable, so code deep in a pipeline reaches it without a parameter threaded
through every signature — and each call is, in order:

1. **checked against the declaration.** The component must be one this module knows
   (:func:`operation_components`); the implementation the call passes must be the
   entrypoint the component declares, so the code that runs is the code recorded; every
   host the component reaches, and a subprocess if it starts one, must be declared in the
   skill's ``permissions``; a ``url`` argument must name one of the component's hosts.
   Anything else is refused before it runs.
2. **executed through both kernels.** The component is admitted by the PSH bridge and the
   call made with ``ExecutionBroker.call_tool``: the payload is classified and the egress
   gate rules on the component's real destinations, the budget is charged, an approval
   is asked for where the risk requires one. The bridged component then executes through
   BioScience's ``Runtime.invoke``: resolver, policy kernel (licence, the trusted
   profile's host allowlist), backend, event log.
3. **recorded.** One execution-evidence entry per call — refused calls included — in the
   PSH audit chain and in the artifact's provenance: what the contract required
   (component, hosts, destinations, label ceiling), which gates ruled, the resulting
   ``ExecutionStatus`` and the sha256 of the output. The entry is read back from the chain
   before it counts as recorded.

A call that is refused or fails raises :class:`OperationError` in the skill. It also bars
the run's release whether or not the skill catches it and carries on: the run is not
attested, ``contracts.artifact`` reports ART118, and the attestor checks the ledger an
artifact declares against the chain (:mod:`bioagent.contracts.attestation`).

**The egress guard.** Were the door only a convention, a pipeline that opened a connection
itself would never meet it. While a broker is installed, a Python audit hook watches
``urllib.Request`` and ``socket.connect``. Outside an operation, a ``urllib`` request or
an IP socket connection (loopback included) is refused and recorded as an unbrokered
operation; inside one, a ``urllib`` request to a host the operation does not declare is
refused, a redirect included. What the guard cannot see
is stated rather than implied: the sockets of a subprocess (which is why a ColabFold run is
an operation of its own), threads the skill starts (a context variable does not follow
them), and the host behind a raw socket inside an operation (behind a proxy the address is
the proxy's, so only ``urllib`` requests are matched to hosts there).
"""

from __future__ import annotations

import contextlib
import contextvars
import hashlib
import json
import socket
import sys
import threading
import urllib.parse
import urllib.request
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Iterator, Mapping

from .contracts.artifact import operation_blockers
from .contracts.attestation import OPERATION_EVENT, operation_digest
from .status import ExecutionStatus

__all__ = ["ALPHAFOLD_MODEL", "COLABFOLD_BATCH", "ESMATLAS_FOLD", "ESMFOLD_WEIGHTS",
           "RCSB_CHEMCOMP", "RCSB_ENTRY", "OperationBroker", "OperationError",
           "OperationEvidence", "OperationRefused", "current_broker", "fetch_bytes",
           "operation", "operation_components"]

ESMATLAS_FOLD = "structure.esmatlas.fold"
RCSB_ENTRY = "structure.rcsb.entry"
ALPHAFOLD_MODEL = "structure.alphafold.model"
RCSB_CHEMCOMP = "docking.rcsb.chemcomp"
COLABFOLD_BATCH = "structure.colabfold.batch"
ESMFOLD_WEIGHTS = "structure.esmfold.weights"

#: Characters of a component's error text an evidence entry keeps: it is text the
#: component produced, recorded in an audit chain, so it is bounded.
_REASON_LIMIT = 300
_SUCCESSFUL = (ExecutionStatus.SUCCEEDED, ExecutionStatus.DEGRADED)
_NETWORK_SCHEMES = frozenset({"http", "https", "ftp"})


def fetch_bytes(url: str, *, timeout: float = 120.0) -> bytes:
    """The body of one GET: the transport behind every download operation.

    The plain ``urlopen`` the pipelines called inline before, so routing a download through
    the broker changes what is checked and recorded, not what is sent.
    """
    with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310
        return response.read()


def operation_components() -> dict[str, Any]:
    """Every external operation a skill pipeline performs, as a BioScience component.

    Built afresh on each call: a manifest carries the lifecycle state the resolver
    advances, and one run's state must not leak into the next. Each declares the hosts it
    reaches and the licence of what comes back, which is what the declaration check, the
    BioScience policy kernel and the PSH bridge rule on.

    The fold declares its ``sequence`` a protein sequence (PSH's ``PROTEIN_SEQUENCE``
    format). PSH's classifier otherwise floors a sequence of about a hundred residues as
    uninspectable content, which its egress gate will not send to a public service; with
    the declaration it labels a value that validates against the residue alphabet as
    research data, and refuses the call when the value is anything else.
    """
    from psh.labels import PROTEIN_SEQUENCE

    from .runtime.component import (ComponentManifest, LicenseSpec, Permissions, Provider,
                                    RuntimeSpec)

    def component(cid: str, name: str, entrypoint: str, host: str, spdx: str,
                  description: str, *, subprocess: bool = False,
                  inputs: Mapping[str, Any] | None = None) -> Any:
        module = entrypoint.split(":", 1)[0]
        return ComponentManifest(
            id=cid, kind="tool", name=name, version="1.0.0", description=description,
            domain="skill operations",
            provider=Provider(project="bioagent",
                              source_path=f"src/{module.replace('.', '/')}.py"),
            runtime=RuntimeSpec(backend="python", entrypoint=entrypoint),
            inputs=dict(inputs or {}),
            permissions=Permissions(network=(host,), subprocess=subprocess),
            license=LicenseSpec(spdx=spdx, integration_mode="federated"))

    fetch = "bioagent.operations:fetch_bytes"
    fold_inputs = {"type": "object", "required": ["sequence", "timeout"],
                   "properties": {"sequence": {"type": "string", "format": PROTEIN_SEQUENCE,
                                               "description": "the sequence to fold"},
                                  "timeout": {"type": "number"}}}
    built = (
        component(ESMATLAS_FOLD, "ESM Atlas: fold one sequence",
                  "bioagent.structure.predict:esmatlas_fold", "api.esmatlas.com", "MIT",
                  "ESMFold through Meta's ESM Atlas service; the sequence is sent to it",
                  inputs=fold_inputs),
        component(RCSB_ENTRY, "RCSB PDB: one entry as PDB text", fetch, "files.rcsb.org",
                  "CC0-1.0", "an experimental structure from the Protein Data Bank"),
        component(ALPHAFOLD_MODEL, "AlphaFold DB: one predicted model", fetch,
                  "alphafold.ebi.ac.uk", "CC-BY-4.0",
                  "the AlphaFold DB record of a UniProt accession, and its model file"),
        component(RCSB_CHEMCOMP, "RCSB Chemical Component Dictionary: one ligand", fetch,
                  "data.rcsb.org", "CC0-1.0",
                  "the SMILES the Chemical Component Dictionary records for a ligand code"),
        component(COLABFOLD_BATCH, "ColabFold: one prediction",
                  "bioagent.structure.predict:colabfold_batch", "api.colabfold.com", "MIT",
                  "colabfold_batch, which sends the sequence to ColabFold's MSA server",
                  subprocess=True),
        component(ESMFOLD_WEIGHTS, "Hugging Face Hub: the ESMFold weights",
                  "bioagent.structure.predict:load_esmfold", "huggingface.co", "MIT",
                  "facebook/esmfold_v1, from the local cache or the Hub"),
    )
    return {c.id: c for c in built}


# ---------------------------------------------------------------------------
# evidence
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class OperationEvidence:
    """What one operation was required to be, what ruled on it, and how it ended."""

    seq: int
    operation: str                       # the component the call named
    implementation: str                  # module:function the call would run
    declared: bool                       # the skill's manifest declares all it reaches
    hosts: tuple[str, ...] = ()          # the contract: what the component reaches
    destinations: tuple[str, ...] = ()   # the contract: PSH destinations it reaches
    label_ceiling: str = ""              # the contract: the most sensitive label it takes
    checks: tuple[str, ...] = ()         # "gate:passed" / "gate:refused", in order
    status: str = ExecutionStatus.DENIED.value
    reason: str = ""
    arguments_sha256: str = ""
    output_sha256: str = ""
    recorded: bool = False               # read back from the audit chain

    def as_dict(self) -> dict[str, Any]:
        body = {"seq": self.seq, "operation": self.operation,
                "implementation": self.implementation, "declared": self.declared,
                "hosts": list(self.hosts), "destinations": list(self.destinations),
                "label_ceiling": self.label_ceiling, "checks": list(self.checks),
                "status": self.status, "reason": self.reason,
                "arguments_sha256": self.arguments_sha256,
                "output_sha256": self.output_sha256}
        return {**body, "evidence_sha256": operation_digest(body),
                "recorded": self.recorded}

    @property
    def digest(self) -> str:
        return self.as_dict()["evidence_sha256"]


class OperationError(RuntimeError):
    """An operation gave no usable result under governance; ``evidence`` says why."""

    def __init__(self, evidence: OperationEvidence) -> None:
        self.evidence = evidence
        if ExecutionStatus(evidence.status) in _SUCCESSFUL and not evidence.recorded:
            what = "ran, but the audit chain does not hold its record"
        else:
            what = f"{evidence.status.lower()}: {evidence.reason or 'no detail'}"
        super().__init__(f"operation {evidence.seq} ({evidence.operation}) {what}")

    @property
    def status(self) -> ExecutionStatus:
        return ExecutionStatus(self.evidence.status)


class OperationRefused(OperationError):
    """Refused by a gate: undeclared, refused by PSH's or BioScience's policy, or caught
    reaching a host it does not declare. The skill gets no result from it."""


# ---------------------------------------------------------------------------
# the door
# ---------------------------------------------------------------------------

_BROKER: contextvars.ContextVar["OperationBroker | None"] = contextvars.ContextVar(
    "bioagent_operation_broker", default=None)
_SCOPE: contextvars.ContextVar["_Scope | None"] = contextvars.ContextVar(
    "bioagent_operation_scope", default=None)


def operation(operation_id: str, implementation: Callable[..., Any], **arguments: Any) -> Any:
    """Perform one external operation: through the run's broker inside a governed run.

    ``implementation`` is the function the component ``operation_id`` declares as its
    entrypoint, named at the call site so the code reads as what it does. Outside a
    governed run it is called directly, as before; inside one the broker refuses it unless
    it is that entrypoint.
    """
    broker = _BROKER.get()
    if broker is None:
        return implementation(**arguments)
    return broker.invoke(operation_id, implementation, arguments)


def current_broker() -> "OperationBroker | None":
    """The broker of the governed run in progress in this context, if any."""
    return _BROKER.get()


@dataclass
class _Scope:
    """The operation in progress: the hosts it may reach, and any request it may not."""

    operation: str
    hosts: frozenset[str]
    violations: list[str] = field(default_factory=list)


class OperationBroker:
    """Checks, executes and records the external operations of one governed skill run.

    ``spec`` is the skill's ``SkillSpec``: its ``permissions`` are the declaration every
    operation is checked against. ``kernel`` is the run's PSH ``TrustedKernel``. ``run_id``
    is the id the run's audit events carry, and ``anchor`` the hash of the event that
    started the run: each operation record names it, which is how the attestor finds this
    run's operations among everything else in a shared chain.
    """

    def __init__(self, spec: Any, kernel: Any, *, run_id: str, anchor: str,
                 permission_profile: str = "biomedical-research",
                 components: Mapping[str, Any] | None = None) -> None:
        self.spec = spec
        self.kernel = kernel
        self.run_id = run_id
        self.anchor = anchor
        self.permission_profile = permission_profile
        self.components = (dict(components) if components is not None
                           else operation_components())
        self.declared_hosts = frozenset(h.lower() for h in spec.permissions.network if h)
        self.declares_subprocess = bool(spec.permissions.subprocess)
        self.entries: list[OperationEvidence] = []
        self._lock = threading.RLock()
        self._bridge: Any = None
        self._events: Any = None
        self._envelope: Any = None

    # ------------------------------------------------------------ the run
    @contextlib.contextmanager
    def governing(self) -> Iterator["OperationBroker"]:
        """Install this broker, and the egress guard, for the code run inside the block."""
        _install_guard()
        token = _BROKER.set(self)
        try:
            yield self
        finally:
            _BROKER.reset(token)

    def evidence(self) -> list[dict[str, Any]]:
        """The ledger, as the artifact's provenance carries it."""
        with self._lock:
            return [e.as_dict() for e in self.entries]

    def release_blockers(self) -> tuple[str, ...]:
        """Why the run may not be released on account of its operations; empty if none.

        The same rule ``validate_artifact`` applies to the ledger (ART118), applied here so
        the run that knows it is blocked does not attest the artifact in the first place.
        """
        return operation_blockers({"governed": {"operations": self.evidence()}})

    # ------------------------------------------------------- one operation
    def invoke(self, operation_id: str, implementation: Callable[..., Any],
               arguments: Mapping[str, Any]) -> Any:
        args = dict(arguments)
        named = (f"{getattr(implementation, '__module__', '?')}:"
                 f"{getattr(implementation, '__qualname__', type(implementation).__name__)}")
        base = OperationEvidence(seq=0, operation=operation_id, implementation=named,
                                 declared=False, arguments_sha256=_sha256_of(args))
        component = self.components.get(operation_id)
        if component is None:
            raise self._refused(replace(base, checks=("skill_manifest:refused",), reason=(
                f"no operation {operation_id!r} is known; a skill may perform only the "
                f"operations of bioagent.operations ({', '.join(sorted(self.components))})")))
        base = replace(base, **self._contract(component))
        undeclared = self._undeclared(component, named, args)
        if undeclared:
            raise self._refused(replace(base, checks=("skill_manifest:refused",),
                                        reason=undeclared))
        return self._execute(component, replace(base, declared=True), args)

    def _contract(self, component: Any) -> dict[str, Any]:
        """What the component requires, by the rules the bridge admits it under."""
        from .psh.manifest import bridge_manifest

        derived = bridge_manifest(component)
        return {"hosts": tuple(h.lower() for h in component.permissions.network if h),
                "destinations": tuple(d.name for d in derived.destinations),
                "label_ceiling": derived.max_label.name}

    def _undeclared(self, component: Any, named: str, args: Mapping[str, Any]) -> str:
        """Why the skill's manifest does not cover this call, or "" when it does."""
        if named != component.runtime.entrypoint:
            return (f"the call would run {named}, but {component.id} declares "
                    f"{component.runtime.entrypoint}; the component that is recorded must "
                    "be the code that runs")
        hosts = tuple(h.lower() for h in component.permissions.network if h)
        if not hosts and not component.permissions.subprocess:
            return (f"{component.id} reaches no host and starts no subprocess, so there is "
                    "nothing external for the broker to govern; call it directly")
        missing = [h for h in hosts if h not in self.declared_hosts]
        if missing:
            return (f"{component.id} reaches {', '.join(missing)}, which skill "
                    f"{self.spec.id}'s manifest does not declare (permissions.network: "
                    f"{', '.join(sorted(self.declared_hosts)) or 'none'})")
        if component.permissions.subprocess and not self.declares_subprocess:
            return (f"{component.id} starts a subprocess and skill {self.spec.id}'s "
                    "manifest declares subprocess: false")
        if "url" in args:
            host = (urllib.parse.urlsplit(str(args["url"])).hostname or "").lower()
            if host not in hosts:
                return (f"the request names host {host or str(args['url'])!r}, which "
                        f"{component.id} does not declare ({', '.join(hosts)})")
        return ""

    def _execute(self, component: Any, base: OperationEvidence,
                 args: dict[str, Any]) -> Any:
        try:
            bridge = self._bridge_for()
            bridged = bridge.component(bridge.admit(component).id)
        except Exception as exc:                             # noqa: BLE001 - recorded
            raise self._refused(replace(
                base, checks=("skill_manifest:passed", "psh_bridge:refused"),
                reason=_bounded(f"{type(exc).__name__}: {exc}"))) from exc
        # Reset, so a refusal by PSH's gates (nothing executed) can be told from a result
        # BioScience reported (its status is set by the call that ran).
        bridged.last_status = None
        mark = len(self._events)
        scope = _Scope(component.id, frozenset(base.hosts))
        token = _SCOPE.set(scope)
        result: Any = None
        error: BaseException | None = None
        try:
            result = self.kernel.broker.call_tool(bridged, dict(args), self._envelope)
        except Exception as exc:                             # noqa: BLE001 - recorded
            error = exc
        finally:
            _SCOPE.reset(token)
        status, checks, reason = self._outcome(bridged, mark, scope, result, error)
        output = _sha256_of(getattr(result, "value", None)) if error is None else ""
        evidence = self._record(replace(base, checks=checks, status=status.value,
                                        reason=_bounded(reason), output_sha256=output))
        if status is ExecutionStatus.DENIED:
            raise OperationRefused(evidence)
        if status not in _SUCCESSFUL or not evidence.recorded:
            raise OperationError(evidence)
        return result.value

    def _outcome(self, bridged: Any, mark: int, scope: _Scope, result: Any,
                 error: BaseException | None) -> tuple[ExecutionStatus, tuple[str, ...], str]:
        """The status, the gates that ruled and the reason, from what the call left behind."""
        checks = ["skill_manifest:passed"]
        ran = bridged.last_status
        if ran is None:
            # PSH's broker stopped the call before the component was invoked: the egress
            # gate, the budget, an approval nobody gave, an isolation it could not provide.
            checks.append("psh_broker:refused")
            why = f"{type(error).__name__}: {error}" if error else "refused by PSH's broker"
            return ExecutionStatus.DENIED, tuple(checks), why
        checks.append("psh_broker:passed")
        checks += self._bioscience_checks(mark)
        if scope.violations:
            checks.append("egress_guard:refused")
            return ExecutionStatus.DENIED, tuple(checks), scope.violations[0]
        if error is not None:
            why = f"{type(error).__name__}: {error}"
            if ran in _SUCCESSFUL:
                # It ran, and PSH withheld the result afterwards (a post-call hook).
                checks.append("psh_broker:refused")
                return ExecutionStatus.DENIED, tuple(checks), why
            return ran, tuple(checks), why
        if getattr(result, "status", "ok") == "degraded":
            return (ExecutionStatus.DEGRADED, tuple(checks),
                    "; ".join(getattr(result, "warnings", ())) or "a documented shortfall")
        return ExecutionStatus.SUCCEEDED, tuple(checks), ""

    def _bioscience_checks(self, mark: int) -> list[str]:
        """How BioScience's resolver and policy kernel ruled, from its event log."""
        from .runtime.events import EventType

        out = []
        for event in self._events.events[mark:]:
            if event.event_type == EventType.COMPONENT_RESOLVED:
                blocked = event.status in ("UNAVAILABLE", "QUARANTINED")
                out.append(f"bioscience_resolver:{'refused' if blocked else 'passed'}")
            elif event.event_type == EventType.POLICY_CHECKED:
                ruled = "passed" if event.status == "ALLOW" else "refused"
                out.append(f"bioscience_policy:{ruled}")
        return out

    # ---------------------------------------------------------- records
    def _refused(self, evidence: OperationEvidence) -> OperationRefused:
        """Record a refusal; the exception for the caller to raise."""
        return OperationRefused(self._record(replace(evidence,
                                                     status=ExecutionStatus.DENIED.value)))

    def _record(self, evidence: OperationEvidence) -> OperationEvidence:
        """Number the entry, append it to the chain, and keep it with what the chain holds."""
        with self._lock:
            evidence = replace(evidence, seq=len(self.entries) + 1, recorded=False)
            detail = evidence.as_dict()
            detail.pop("recorded")
            try:
                self.kernel.audit(OPERATION_EVENT, run_id=self.run_id,
                                  detail={**detail, "run_anchor": self.anchor})
                recorded = self._in_chain(evidence.digest)
            except Exception:                                # noqa: BLE001 - reported below
                recorded = False
            evidence = replace(evidence, recorded=recorded)
            self.entries.append(evidence)
            return evidence

    def _in_chain(self, digest: str) -> bool:
        """Whether the run's latest audit record is this entry, as written."""
        rows = self.kernel.events.by_run(self.run_id)
        if not rows:
            return False
        last = rows[-1]
        try:
            detail = json.loads(last["detail"] or "{}")
        except (TypeError, ValueError):
            return False
        return (last["event_type"] == OPERATION_EVENT
                and detail.get("evidence_sha256") == digest
                and detail.get("run_anchor") == self.anchor)

    def _bridge_for(self) -> Any:
        """The bridge, runtime and envelope this run's operations execute under.

        Built on first use, so a run that performs no operation adds nothing to its chain.
        The components run in this process (``isolate=False``, recorded on each admission):
        the skill that calls them runs here too, and the egress guard is in-process.
        """
        if self._bridge is None:
            from .psh.assembly import default_runtime
            from .psh.bridge import BioScienceBridge
            from .runtime.agentspec import AgentSpec
            from .runtime.events import EventLog

            # Exactly the operations, whatever $BIOAGENT_TOOLUNIVERSE asks of other runtimes.
            runtime = default_runtime(catalogue=False, public_apis=False, native_tools=False,
                                      skills=False, tooluniverse=False,
                                      extra_manifests=tuple(self.components.values()))
            events = EventLog(run_id=self.run_id)
            bridge = BioScienceBridge(
                self.kernel, runtime, isolate=False, verification={}, events=events,
                spec=AgentSpec(name=f"governed:{self.run_id}",
                               permission_profile=self.permission_profile))
            envelope = self.kernel.envelope()
            # Kept only once all three exist: a half-built set would run the next call
            # under no envelope at all.
            self._events, self._envelope, self._bridge = events, envelope, bridge
        return self._bridge

    # ------------------------------------------------------- egress guard
    def _guard(self, event: str, args: tuple) -> None:
        """Rule on one network event raised while this broker is installed."""
        scope = _SCOPE.get()
        if event == "urllib.Request":
            parts = urllib.parse.urlsplit(str(args[0]) if args else "")
            if parts.scheme.lower() not in _NETWORK_SCHEMES:
                return
            host = (parts.hostname or "").lower()
            if scope is None:
                raise self._unbrokered(f"a request to {host} outside the operation broker")
            if host not in scope.hosts:
                why = (f"{scope.operation} requested {host}, which it does not declare "
                       f"({', '.join(sorted(scope.hosts))}); a redirect or an address the "
                       "service hands back does not widen what the operation may reach")
                scope.violations.append(why)
                raise PermissionError(why)
            return
        sock = args[0] if args else None
        if scope is not None or getattr(sock, "family", None) not in (socket.AF_INET,
                                                                      socket.AF_INET6):
            return
        address = args[1] if len(args) > 1 else ""
        raise self._unbrokered(f"a connection to {_address(address)} outside the operation "
                               "broker")

    def _unbrokered(self, what: str) -> PermissionError:
        """Record a connection made around the broker; the exception that refuses it."""
        why = (f"{what}: a governed skill reaches the network only through "
               "bioagent.operations.operation")
        self._record(OperationEvidence(seq=0, operation="unbrokered", implementation="",
                                       declared=False, checks=("egress_guard:refused",),
                                       status=ExecutionStatus.DENIED.value,
                                       reason=_bounded(why)))
        return PermissionError(why)


# ---------------------------------------------------------------------------
# the egress guard's hook
# ---------------------------------------------------------------------------

_GUARD_LOCK = threading.Lock()
_GUARD_INSTALLED = False
_WATCHED = frozenset({"urllib.Request", "socket.connect"})


def _install_guard() -> None:
    """Add the audit hook once per process. A hook cannot be removed, so it does nothing
    unless a broker is installed in the context of the event."""
    global _GUARD_INSTALLED
    with _GUARD_LOCK:
        if not _GUARD_INSTALLED:
            sys.addaudithook(_audit_hook)
            _GUARD_INSTALLED = True


def _audit_hook(event: str, args: tuple) -> None:
    if event not in _WATCHED:
        return
    broker = _BROKER.get()
    if broker is not None:
        broker._guard(event, args)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _bounded(text: str) -> str:
    return str(text)[:_REASON_LIMIT]


def _address(address: Any) -> str:
    if isinstance(address, tuple) and address:
        return ":".join(str(part) for part in address[:2])
    return str(address)


def _sha256_of(value: Any) -> str:
    """The sha256 of a value: its bytes, its UTF-8 text, or its sorted-key JSON."""
    if isinstance(value, (bytes, bytearray)):
        return hashlib.sha256(bytes(value)).hexdigest()
    if not isinstance(value, str):
        value = json.dumps(value, sort_keys=True, ensure_ascii=False,
                           separators=(",", ":"), default=str)
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
