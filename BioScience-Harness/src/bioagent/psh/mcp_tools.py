"""A reviewed MCP server's tools as PSH components, over the connection bioagent holds.

``psh.protocols.MCPToolAdapter`` admits the entries of a server's ``tools/list`` as governed
components and normalises their replies, and takes its transport as a callable it never
provides. ``admit_mcp_server`` provides it: the ``MCPConnection`` the MCP backend uses,
opened and checked against the reviewed entry. The adapter is offered only the tools that
check admitted, so a tool outside the allowlist, or one that drifted from its snapshot,
never reaches the adapter to be admitted at all. The destination is the reviewed one, and
the adapter's own rules (an MCP server is never local; annotations only tighten) apply
unchanged.

The connection reports a call that produced no reply as an ``MCPCallError`` carrying a
BioScience status. PSH's loop reasons in exceptions whose types decide retry, refusal and
escalation, so the transport translates them as ``BridgedComponent.value_of`` does. An
``isError`` reply is passed through as the reply it is: the adapter's normaliser turns it
into a ``ContractViolation``, as it would for any transport.

(Not ``mcp.py``: ``exec.py`` runs as a script with this directory first on ``sys.path``,
where a module of that name would stand in for the MCP SDK.)
"""

from __future__ import annotations

from typing import Any, Callable, Mapping

from psh.contracts import CapabilityUnavailable, ContractViolation, PolicyDenied, ToolTimeout
from psh.labels import Sensitivity
from psh.protocols import MCPToolAdapter

from ..backends.concrete import MCPCallError
from ..status import ExecutionStatus
from .manifest import mcp_destination

__all__ = ["admit_mcp_server", "mcp_transport"]

_RAISES: Mapping[ExecutionStatus, type[Exception]] = {
    ExecutionStatus.DENIED: PolicyDenied,
    ExecutionStatus.UNAVAILABLE: CapabilityUnavailable,
    ExecutionStatus.TIMEOUT: ToolTimeout,
}


def _psh_error(exc: MCPCallError) -> Exception:
    return _RAISES.get(exc.status, ContractViolation)(exc.reason[:300])


def mcp_transport(connection: Any) -> Callable[[str, Mapping[str, Any]], Any]:
    """``connection.call_tool``, raising what PSH's loop expects, not ``MCPCallError``."""
    def call_tool(name: str, arguments: Mapping[str, Any]) -> Any:
        try:
            return connection.call_tool(name, arguments)
        except MCPCallError as exc:
            raise _psh_error(exc) from None
    return call_tool


def admit_mcp_server(kernel: Any, connection: Any, *, registry: Any = None,
                     max_label: Sensitivity | None = None,
                     overrides: Mapping[str, Mapping[str, Any]] | None = None
                     ) -> MCPToolAdapter:
    """Open ``connection`` and admit the tools it verified, as PSH components.

    Returns the adapter, whose ``admitted`` holds the components; with ``registry`` they
    are registered into it as well. ``max_label`` and ``overrides`` are the adapter's own
    operator controls, passed through unchanged.
    """
    try:
        connection.open()
    except MCPCallError as exc:
        raise _psh_error(exc) from None
    config = connection.config
    adapter = MCPToolAdapter(kernel, server=config.id, destination=mcp_destination(config),
                             call_tool=mcp_transport(connection),
                             allowed_hosts=(config.host,) if config.host else (),
                             max_label=max_label, overrides=overrides)
    descriptors = connection.descriptors()
    if registry is not None:
        adapter.register_into(registry, descriptors)
    else:
        adapter.admit_all(descriptors)
    return adapter
