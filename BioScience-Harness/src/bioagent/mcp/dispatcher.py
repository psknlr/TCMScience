"""The dispatcher an ``MCPBackend`` calls: reviewed servers, connected on first use.

``MCPBackend`` has always taken a ``dispatcher(server, tool, **arguments)``, and nothing
supplied one, so an MCP component resolved and nothing ran. This is that dispatcher, and it
does nothing a reviewed entry does not say:

* a server outside the registry is UNAVAILABLE with the registry's reason, never
  improvised from the component's say-so;
* a server whose entry names credentials is started only when every one resolves, and it
  receives them as the variables the entry names — the SDK's minimal environment and the
  proxy route are all it gets besides. With no credential source (``credentials=None``,
  what the isolated child runs with) such a server is refused, by name, before anything
  starts;
* a connection is opened on the first call, checked against the reviewed snapshot, and
  reused until it ends; a connection that has ended is replaced on the next call and
  checked again.

Nothing is connected when a dispatcher is built. ``default_runtime()`` builds one whenever
servers are configured, in processes that may never call them, so construction must cost
nothing and start nothing.
"""

from __future__ import annotations

import os
import threading
from typing import Any, Callable

from ..backends.concrete import MCPCallError, MCPReply
from ..status import ExecutionStatus
from .config import MCPServerConfig, MCPServerRegistry
from .connection import MCPConnection

__all__ = ["CredentialSource", "MCPDispatcher", "environment_credentials",
           "resolve_credentials"]

#: Resolves a credential name to its value, or None when it has none to give.
CredentialSource = Callable[[str], "str | None"]


def environment_credentials(name: str) -> str | None:
    """A credential from this process's environment, by the name the reviewed entry gives."""
    return os.environ.get(name) or None


def resolve_credentials(config: MCPServerConfig,
                        credentials: CredentialSource | None) -> dict[str, str]:
    """The server variables an entry names, with their values; UNAVAILABLE if any is missing.

    Reasons name credentials, never values. A server is not started short of one: a
    server run without its key fails in a way that looks like the server's fault.
    """
    if not config.env:
        return {}
    meta = {"server": config.id, "config_digest": config.digest}
    names = ", ".join(config.credentials)
    if credentials is None:
        raise MCPCallError(
            f"MCP server {config.id!r} needs credentials ({names}) and this process holds no "
            "credential source; an isolated BioScience child is never handed one, so a "
            "credentialed server runs in-process only (docs/mcp-transport.md)",
            status=ExecutionStatus.UNAVAILABLE, metadata=meta)
    values: dict[str, str] = {}
    missing: list[str] = []
    for variable, name in config.env.items():
        value = credentials(name)
        if value:
            values[variable] = value
        elif name not in missing:
            missing.append(name)
    if missing:
        raise MCPCallError(
            f"credential(s) {', '.join(missing)} for MCP server {config.id!r} are not "
            "available to this process; the server is not started without them",
            status=ExecutionStatus.UNAVAILABLE, metadata=meta)
    return values


class MCPDispatcher:
    """Calls a tool on a reviewed server: ``dispatcher(server, tool, **arguments)``."""

    def __init__(self, registry: MCPServerRegistry, *,
                 credentials: CredentialSource | None = environment_credentials) -> None:
        self.registry = registry
        self.credentials = credentials
        self._lock = threading.Lock()
        self._server_locks: dict[str, threading.Lock] = {}
        self._connections: dict[str, MCPConnection] = {}

    def serves(self, server: str) -> bool:
        return self.registry.get(server) is not None

    def connection(self, server: str) -> MCPConnection:
        """The open, checked connection to ``server``, opening one if there is none."""
        config = self.registry.get(server)
        if config is None:
            raise MCPCallError(self.registry.why_not(server),
                               status=ExecutionStatus.UNAVAILABLE, metadata={"server": server})
        with self._lock:
            server_lock = self._server_locks.setdefault(server, threading.Lock())
        with server_lock:
            current = self._connections.get(server)
            if current is not None and current.alive:
                return current
            if current is not None:
                current.close()                  # ended: release it before replacing it
            env = resolve_credentials(config, self.credentials)
            connection = MCPConnection(config, env=env).open()
            self._connections[server] = connection
            return connection

    def __call__(self, server: str, tool: str, **arguments: Any) -> MCPReply:
        connection = self.connection(server)
        reply = connection.call_tool(tool, arguments)
        return MCPReply(reply, metadata=connection.provenance(tool))

    def close(self) -> None:
        """Close every connection this dispatcher opened; it reopens on the next call."""
        with self._lock:
            connections = list(self._connections.values())
            self._connections.clear()
        for connection in connections:
            connection.close()
