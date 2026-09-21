"""Research records are proposals/observations, never automatically verified knowledge."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, fields

from ..contracts import PolicyDenied, RunEnvelope
from ..kernel.persistence import ValidationStatus
from ..labels import DataLabel, Destination
from ..workgraph import EdgeKind, Node, NodeKind


def _text(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("expected nonempty text")


def _texts(value):
    if not isinstance(value, tuple) or not value:
        raise ValueError("expected a nonempty tuple of text")
    for item in value:
        _text(item)


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False)


def _hash(value):
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Hypothesis:
    proposition: str
    population: str
    predictions: tuple[str, ...]
    falsifiers: tuple[str, ...]
    alternatives: tuple[str, ...]

    def __post_init__(self):
        _text(self.proposition)
        _text(self.population)
        for value in (self.predictions, self.falsifiers, self.alternatives):
            _texts(value)


@dataclass(frozen=True)
class Protocol:
    primary_endpoint: str
    secondary_endpoints: tuple[str, ...]
    exclusion_criteria: str
    statistical_test: str
    sample_size_assumptions: str
    covariates: tuple[str, ...]
    subgroup_plan: str
    stopping_criteria: str

    def __post_init__(self):
        for name in ("primary_endpoint", "exclusion_criteria", "statistical_test",
                     "sample_size_assumptions", "subgroup_plan", "stopping_criteria"):
            _text(getattr(self, name))
        for value in (self.secondary_endpoints, self.covariates):
            if not isinstance(value, tuple):
                raise ValueError("protocol sequences must be tuples")
            for item in value:
                _text(item)

    @property
    def fingerprint(self):
        return _hash(asdict(self))


@dataclass(frozen=True)
class Observation:
    summary: str
    artifact_refs: tuple[str, ...]
    outcome: str  # observed | negative | inconclusive

    def __post_init__(self):
        _text(self.summary)
        _texts(self.artifact_refs)
        if self.outcome not in {"observed", "negative", "inconclusive"}:
            raise ValueError("unknown observation outcome")


@dataclass(frozen=True)
class Deviation:
    reason: str
    actual_protocol: Protocol

    def __post_init__(self):
        _text(self.reason)
        if not isinstance(self.actual_protocol, Protocol):
            raise ValueError("actual_protocol must be a Protocol")


_TYPES = {NodeKind.HYPOTHESIS: Hypothesis, NodeKind.PROTOCOL: Protocol,
          NodeKind.OBSERVATION: Observation}


class ScientificLedger:
    """One project, explicit run authority, immutable records with content hashes.

    Callers are responsible for temporal truth (e.g. preregistering before collecting
    data). Hashes detect accidental edits; they are not signatures or remote attestation.
    Stored source edges express provenance, not scientific support or verification.
    """

    def __init__(self, kernel, project_id: str):
        self.kernel = kernel
        self.graph = kernel.graph
        self.project_id = project_id

    def _node(self, node_id, kind, envelope):
        node = self.graph.get(node_id)
        if node is None or node.kind != kind or node.project_id != self.project_id:
            raise ValueError("missing, wrong-kind or cross-project scientific reference")
        if node.label.sensitivity > envelope.max_label.sensitivity:
            raise PolicyDenied("scientific record exceeds this run's data ceiling")
        return node

    def _authority(self, envelope):
        from ..kernel.authority import AuthorityLattice
        effective = AuthorityLattice.meet(envelope, self.kernel.policy.ceiling())
        if not effective.permits_destination(Destination.PERSISTENT):
            raise PolicyDenied("scientific records require PERSISTENT authority")
        if effective.project_id and effective.project_id != self.project_id:
            raise PolicyDenied("run belongs to another project")
        return effective

    def read(self, node_id: str, envelope: RunEnvelope) -> dict:
        effective = self._authority(envelope)
        node = self.graph.get(node_id)
        if node is None or node.kind not in _TYPES:
            raise ValueError("not a scientific record")
        node = self._node(node_id, node.kind, effective)
        body = json.loads(node.body)
        if node.ref != "sha256:" + _hash(body):
            raise ValueError("scientific record content hash mismatch")
        if body.get("schema_version") != 1 or body.get("kind") != node.kind.value:
            raise ValueError("unsupported scientific record schema")
        return body

    def _write(self, record, kind, envelope, sources=(), extra=None, label=None):
        effective = self._authority(envelope)
        project = self._node(self.project_id, NodeKind.PROJECT, effective)
        inherited = project.label.merged_with(label or DataLabel())
        for source in sources:
            inherited = inherited.merged_with(source.label)
        content = dict(schema_version=1, kind=kind.value, record=asdict(record),
                       source_ids=[s.id for s in sources], **(extra or {}))
        return self.kernel.persistence.commit_node(
            kind=kind, title=f"Scientific {kind.value}", body=_json(content),
            project_id=self.project_id, principal=effective.principal.id,
            source_run=effective.run_id, status="recorded",
            validation_status=ValidationStatus.CANDIDATE,
            inherited_label=inherited, max_label=effective.max_label.sensitivity,
            ref="sha256:" + _hash(content),
            links=tuple((s.id, EdgeKind.DERIVED_FROM) for s in sources))

    def hypothesize(self, hypothesis: Hypothesis, envelope: RunEnvelope, *,
                    label: DataLabel | None = None) -> Node:
        if not isinstance(hypothesis, Hypothesis):
            raise ValueError("expected Hypothesis")
        return self._write(hypothesis, NodeKind.HYPOTHESIS, envelope, label=label)

    def preregister(self, hypothesis_id: str, protocol: Protocol, envelope: RunEnvelope,
                    *, label: DataLabel | None = None) -> Node:
        if not isinstance(protocol, Protocol):
            raise ValueError("expected Protocol")
        effective = self._authority(envelope)
        source = self._node(hypothesis_id, NodeKind.HYPOTHESIS, effective)
        self.read(source.id, effective)
        return self._write(protocol, NodeKind.PROTOCOL, effective, (source,),
                           {"protocol_hash": protocol.fingerprint}, label)

    def observe(self, protocol_id: str, observation: Observation, actual_protocol: Protocol,
                envelope: RunEnvelope, *, deviation_reason: str = "",
                label: DataLabel | None = None) -> Node:
        if not isinstance(observation, Observation) or not isinstance(actual_protocol, Protocol):
            raise ValueError("expected Observation and actual Protocol")
        effective = self._authority(envelope)
        source = self._node(protocol_id, NodeKind.PROTOCOL, effective)
        original = self.read(source.id, effective)
        actual = json.loads(_json(asdict(actual_protocol)))
        changed = {f.name: {"planned": original["record"][f.name],
                            "actual": actual[f.name]}
                   for f in fields(Protocol)
                   if original["record"][f.name] != actual[f.name]}
        if changed and not deviation_reason.strip():
            raise ValueError("changed analysis requires an explicit deviation reason")
        # A single observation node stores the deviation atomically with its result;
        # there is no interval where a changed analysis looks preregistered.
        deviation = (dict(record=asdict(Deviation(deviation_reason, actual_protocol)),
                          changed_fields=changed) if changed else None)
        return self._write(observation, NodeKind.OBSERVATION, effective, (source,),
                           {"protocol_hash": original["protocol_hash"],
                            "actual_protocol_hash": actual_protocol.fingerprint,
                            "deviation": deviation}, label)
