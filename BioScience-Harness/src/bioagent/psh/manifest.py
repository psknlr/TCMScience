"""Derive a PSH manifest from a BioScience one.

The two manifests describe different things. BioScience's says how a component runs
(backend, entrypoint, image), what it needs and what it may touch. PSH's says what the
gates need to rule: which destinations a call reaches, the highest label the component
may receive, its risk tier, whether it mutates, its licence class and integration mode.
The derivation is the whole security argument of the bridge, so each rule is stated:

* **destinations** follow the mechanism. An ``http`` connector reaches its declared hosts
  and nothing local; a local backend reaches ``LOCAL_COMPUTE``, plus each declared host,
  plus ``PERSISTENT`` if it declares writes. A host is ``PUBLIC_REMOTE`` unless the
  operator's ``HostPolicy`` names it trusted — a public research API is a third party
  that receives the query, whatever its reputation.
* **an MCP component reaches its server**, which is a process this kernel does not own:
  the server's reviewed destination (``registry/mcp_servers.yaml``), or ``PUBLIC_REMOTE``
  when no reviewed entry names it — never ``LOCAL_COMPUTE``, which would exempt every
  call from the egress checks. It is the rule ``psh.protocols.MCPToolAdapter`` applies
  to the same servers, so a tool is gated alike whichever way it is admitted.
* **the ceiling** is the lowest of the destination ceilings the component reaches, capped
  by the operator's local ceiling. A PubMed search therefore accepts at most
  ``RESEARCH_DEIDENTIFIED``: PHI in a query would leave the machine, and the gate refuses
  it by the same rule that refuses PHI to a public model.
* **risk and mutation** follow what the component may do: a subprocess, a container, a
  declared write or an MCP tool is consequential (R2) and mutating; a read-only query is
  routine (R1). An MCP tool counts as mutating because nothing but the server says
  otherwise, and the MCP adapter believes no server's ``readOnlyHint`` either. A mutating
  component requires at least ``ACT_WITH_APPROVAL`` autonomy.
* **licence** strings are normalised onto SPDX ids the PSH lattice knows. BioScience
  records data licences as free text ("Public-domain (US Gov)"); an exact alias table
  maps the ones this package ships, and anything else stays unlicensed — which the
  lattice permits in ``native`` and ``federated`` modes and refuses for ``vendor``.
  The raw string is kept in provenance so nothing is lost, only nothing is guessed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from psh.contracts import Autonomy, ComponentKind, ComponentManifest as PSHManifest, RiskTier
from psh.labels import DEFAULT_CEILINGS, Destination, Sensitivity
from psh.licensing import normalise_mode

from ..runtime.component import ComponentManifest as BioManifest

__all__ = ["HostPolicy", "KIND_MAP", "SPDX_ALIASES", "bridge_manifest", "ceiling_for",
           "destinations_for", "mcp_destination", "mcp_server_name", "normalise_spdx",
           "psh_id_for", "risk_for"]

_VALID_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
_UNSAFE = re.compile(r"[^A-Za-z0-9_.-]+")
_SPDX_SHAPE = re.compile(r"[A-Za-z0-9][A-Za-z0-9.+-]{0,63}")


def psh_id_for(bio_id: str) -> str:
    """A BioScience id as a PSH component id: one safe path component, or a refusal."""
    cleaned = _UNSAFE.sub("-", str(bio_id)).strip("-.")[:128]
    if not cleaned or not _VALID_ID.fullmatch(cleaned):
        raise ValueError(f"BioScience id {bio_id!r} cannot be made a PSH component id")
    return cleaned


#: BioScience kinds onto PSH kinds. ``connector`` is a tool from the gate's point of
#: view; a ``database`` or ``dataset`` is knowledge the run may read.
KIND_MAP: Mapping[str, ComponentKind] = {
    "tool": ComponentKind.TOOL, "connector": ComponentKind.TOOL,
    "software": ComponentKind.TOOL, "skill": ComponentKind.SKILL,
    "workflow": ComponentKind.WORKFLOW, "agent": ComponentKind.AGENT,
    "agent_role": ComponentKind.AGENT, "planner": ComponentKind.AGENT,
    "evaluator": ComponentKind.EVALUATOR, "benchmark": ComponentKind.EVALUATOR,
    "dataset": ComponentKind.DATASET, "database": ComponentKind.KNOWLEDGE,
    "memory": ComponentKind.MEMORY_PROVIDER,
}

#: Exact BioScience licence strings onto SPDX ids. Conservative on purpose: a string
#: that says "mixed", "per article" or "per source" is not one licence and maps to none.
SPDX_ALIASES: Mapping[str, str] = {
    "Public-domain (US Gov)": "US-Gov-Public-Domain",
    "Public (UCSC terms)": "",
    "Public (NCI GDC open data)": "US-Gov-Public-Domain",
    "CC0-1.0 (data) / CC-BY-4.0": "CC-BY-4.0",
    "CC0-1.0 (metadata)": "CC0-1.0",
    "CC-BY-4.0 (metadata)": "CC-BY-4.0",
    "MIT (code) / CC-BY-4.0 (data)": "CC-BY-4.0",
    "Apache-2.0 (service)": "Apache-2.0",
    "BSD-3-Clause (service)": "BSD-3-Clause",
    "CC-BY": "CC-BY-4.0",
    "EMBL-EBI terms of use (open)": "",
    "Open (EMBL-EBI terms)": "",
    "Open (INSDC)": "",
    "Open (GTEx terms)": "",
    "CC-BY / mixed per article": "",
    "Apache-2.0 (service) / per ontology": "",
    "BSD-3-Clause (service) / per source": "",
    "Mixed per source": "",
    "Mixed per resource (some CC-BY-NC-SA)": "",
    "HPO licence (free with attribution)": "",
    "Free for research use (PANTHER terms)": "",
    "Academic use free; commercial requires license": "",
    "ODbL / per-study terms": "",
}


def normalise_spdx(text: str | None) -> str:
    """An SPDX id the PSH lattice can classify, or "" for anything it should not guess."""
    value = (text or "").strip()
    if not value:
        return ""
    if value in SPDX_ALIASES:
        return SPDX_ALIASES[value]
    return value if _SPDX_SHAPE.fullmatch(value) else ""


@dataclass(frozen=True)
class HostPolicy:
    """Which hosts the operator treats as trusted rather than public. Default: none."""

    trusted_hosts: frozenset[str] = field(default_factory=frozenset)

    def destination_for(self, host: str) -> Destination:
        return (Destination.TRUSTED_REMOTE if host.lower() in self.trusted_hosts
                else Destination.PUBLIC_REMOTE)


def mcp_server_name(bio: BioManifest) -> str:
    """The server an MCP component calls, read exactly as ``MCPBackend`` reads it."""
    return bio.runtime.server or (bio.native_connectors[0] if bio.native_connectors else "")


def mcp_destination(mcp_server: Any = None) -> Destination:
    """Where a call to an MCP server goes: its reviewed destination, else the public one."""
    if mcp_server is not None and getattr(mcp_server, "destination", "") == "trusted_remote":
        return Destination.TRUSTED_REMOTE
    return Destination.PUBLIC_REMOTE


def destinations_for(bio: BioManifest, host_policy: HostPolicy | None = None, *,
                     mcp_server: Any = None) -> tuple[Destination, ...]:
    """Every destination a call to this component reaches. Never empty.

    ``mcp_server`` is the reviewed entry of the server an MCP component calls, when the
    runtime holds one.
    """
    policy = host_policy or HostPolicy()
    hosts = tuple(h for h in bio.permissions.network if h)
    remote = tuple(dict.fromkeys(policy.destination_for(h) for h in hosts))
    if bio.runtime.backend == "http":
        return remote or (Destination.PUBLIC_REMOTE,)
    if bio.runtime.backend == "mcp":
        return tuple(dict.fromkeys((mcp_destination(mcp_server), *remote)))
    out: list[Destination] = [Destination.LOCAL_COMPUTE, *remote]
    if bio.permissions.filesystem_write:
        out.append(Destination.PERSISTENT)
    return tuple(dict.fromkeys(out))


def ceiling_for(destinations: Sequence[Destination], *,
                local_ceiling: Sensitivity = Sensitivity.PHI) -> Sensitivity:
    """The highest label the component may receive: the lowest ceiling it reaches."""
    ceilings = [DEFAULT_CEILINGS[d] for d in destinations]
    return min([*ceilings, local_ceiling])


def _mutates(bio: BioManifest) -> bool:
    return bool(bio.permissions.filesystem_write or bio.permissions.subprocess
                or bio.runtime.backend in ("subprocess", "container", "mcp"))


def risk_for(bio: BioManifest) -> RiskTier:
    return RiskTier.R2_CONSEQUENTIAL if _mutates(bio) else RiskTier.R1_ROUTINE


#: Beyond an MCP entry's connect and call deadlines, what an isolated run needs to start
#: the child and to let the server shut down when the call is over.
_MCP_MARGIN_S = 30.0


def _mcp_provenance(bio: BioManifest, mcp_server: Any) -> dict[str, Any]:
    """What an MCP component's manifest records about the server it calls."""
    if mcp_server is None:
        return {"mcp_server": mcp_server_name(bio),
                "mcp_config": "no reviewed entry in this runtime's registry"}
    return {"mcp_server": mcp_server.id, "mcp_config_digest": mcp_server.digest,
            "mcp_transport": mcp_server.transport,
            "mcp_package": f"{mcp_server.package} {mcp_server.version}"}


def _operations_schema(operations: Sequence[Any]) -> dict[str, Any]:
    if not operations:
        return {}
    properties: dict[str, Any] = {
        "operation": {"type": "string", "enum": [op.name for op in operations],
                      "description": "which typed operation of this source to call"}}
    for op in operations:
        for arg in op.args:
            properties.setdefault(arg, {"description": f"required by {op.name}"})
    return {"type": "object", "required": ["operation"], "properties": properties,
            "operations": {op.name: {"description": op.description, "args": list(op.args),
                                     "example": dict(op.example)} for op in operations}}


def bridge_manifest(bio: BioManifest, *, host_policy: HostPolicy | None = None,
                    local_ceiling: Sensitivity = Sensitivity.PHI,
                    backend: str = "python", entrypoint: str = "",
                    operations: Sequence[Any] = (), verification: Mapping[str, Any] | None = None,
                    description_sensitivity: str = "",
                    extra_provenance: Mapping[str, Any] | None = None,
                    mcp_server: Any = None) -> PSHManifest:
    """The PSH manifest for a BioScience component. Pure; admission adds the audit.

    ``mcp_server`` is the reviewed entry of the server an MCP component calls. Its host (a
    streamable HTTP server's) joins the hosts the egress proxy may open, and the run's
    deadline is stretched to cover the entry's own connect and call deadlines, so the
    kernel never kills a child that is still within them and leaves its server orphaned.
    """
    destinations = destinations_for(bio, host_policy, mcp_server=mcp_server)
    hosts = tuple(h for h in bio.permissions.network if h)
    if mcp_server is not None and getattr(mcp_server, "host", ""):
        hosts = tuple(dict.fromkeys((*hosts, mcp_server.host)))
    mutates = _mutates(bio)
    timeout_s = float(getattr(bio.runtime, "timeout_s", 0) or 0) or 120.0
    if mcp_server is not None:
        timeout_s = max(timeout_s, mcp_server.connect_timeout_s + mcp_server.call_timeout_s
                        + _MCP_MARGIN_S)
    kind = KIND_MAP.get(bio.kind, ComponentKind.TOOL)
    verified = dict(verification or {})
    latency_s = float(verified.get("latency_ms") or 0) / 1000.0 or (2.0 if hosts else 1.0)
    provenance: dict[str, Any] = {
        "bridge": "bioscience", "bio_id": bio.id, "bio_kind": bio.kind,
        "bio_backend": bio.runtime.backend, "project": bio.provider.project,
        "commit": bio.provider.commit, "source_path": bio.provider.source_path,
        # where the component is described, and what its entrypoint rests on (a reviewed
        # binding, a static check against the tree, or a derivation the resolver refuses)
        "description_path": bio.provider.description_path,
        "entrypoint_basis": bio.runtime.entrypoint_basis,
        # a component whose data is licensed apart from its code says so; the code's
        # licence is then not reported as the data's
        "data_license": bio.license.data or bio.license.spdx,
        "license_note": bio.license.note,
        "integration_mode_declared": bio.license.integration_mode,
        "docs": bio.provider.repo,
        "verified_at": verified.get("verified_at", ""),
        "verification": (f"{verified.get('ok', 0)}/{verified.get('total', 0)} operations live"
                         if verified else "unverified"),
        "operations": [op.name for op in operations],
    }
    if bio.license.data:
        provenance["code_license"] = bio.license.spdx
    if bio.license.catalogue or bio.license.record:
        # the catalogue row's licence is provenance of the row; the record is the reviewed
        # source of ``license_spdx`` when there is one
        provenance["license_catalogue"] = bio.license.catalogue
        provenance["license_record"] = bio.license.record
    if description_sensitivity:
        provenance["description_sensitivity"] = description_sensitivity
    if bio.runtime.backend == "mcp":
        provenance.update(_mcp_provenance(bio, mcp_server))
    provenance.update(dict(extra_provenance or {}))
    return PSHManifest(
        id=psh_id_for(bio.id), name=bio.name or bio.id, kind=kind, version=bio.version,
        publisher=bio.provider.project or "bioscience",
        description=(bio.description or bio.name or bio.id)[:400],
        intents=tuple(op.name for op in operations)[:12],
        domain=bio.domain, tags=tuple(t for t in (bio.omics_type, bio.kind) if t),
        input_schema=_operations_schema(operations) or dict(bio.inputs),
        output_schema=dict(bio.outputs),
        backend=backend, entrypoint=entrypoint,
        allowed_hosts=hosts,
        timeout_s=timeout_s,
        memory_mb=int(getattr(bio.runtime, "memory_mb", 0) or 0) or 2048,
        max_output_chars=int(getattr(bio.runtime, "max_output_chars", 0) or 0) or 200_000,
        idempotent=(bio.runtime.backend == "http" or bio.runtime.deterministic) and not mutates,
        max_label=ceiling_for(destinations, local_ceiling=local_ceiling),
        destinations=destinations,
        requires_network=bool(hosts) or bio.runtime.backend in ("http", "mcp"),
        requires_filesystem=bool(bio.permissions.filesystem_read
                                 or bio.permissions.filesystem_write),
        mutates=mutates,
        min_autonomy=Autonomy.ACT_WITH_APPROVAL if mutates else Autonomy.OBSERVE,
        risk_tier=risk_for(bio),
        expected_latency_s=latency_s,
        known_limits=tuple(x for x in (bio.license.note,) if x),
        license_spdx=normalise_spdx(bio.license.spdx),
        integration_mode=normalise_mode(bio.license.integration_mode),
        provenance=provenance)
