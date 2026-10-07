"""MCP servers, connected for real: reviewed, checked, and governed like any other call.

Until this package, MCP support stopped at the routing layer. ``MCPBackend`` took a
dispatcher nobody supplied, so an MCP component resolved and nothing ran; PSH's
``MCPToolAdapter`` took a transport nobody supplied. Here are both, over the official SDK:

* ``config`` — the reviewed registry (``registry/mcp_servers.yaml``): which servers may be
  reached, how, where their calls go, which tools may be called, and the digests of what
  those tools were when someone read them;
* ``connection`` — one session with one server, synchronous to its caller, checked
  against the reviewed entry when it opens, closed with its server process;
* ``dispatcher`` — what ``MCPBackend`` calls, opening connections on first use;
* ``review`` — how an operator pins a server's tools before admitting it.

The SDK is imported only when a connection opens, so none of this costs anything in a
process that configures no server — which is every process, until someone reviews one.
"""

from __future__ import annotations

from ..backends.concrete import MCPCallError, MCPReply
from .config import (API_VERSION, DESTINATIONS, REGISTRY_FILE, TRANSPORTS, MCPConfigError,
                     MCPServerConfig, MCPServerRegistry, ToolSnapshot, default_registry_path,
                     digest, load_registry)
from .connection import PROXY_VARIABLES, MCPConnection
from .dispatcher import (CredentialSource, MCPDispatcher, environment_credentials,
                         resolve_credentials)
from .review import render_entry, review

__all__ = [
    "API_VERSION", "DESTINATIONS", "REGISTRY_FILE", "TRANSPORTS", "PROXY_VARIABLES",
    "CredentialSource", "MCPCallError", "MCPConfigError", "MCPConnection", "MCPDispatcher",
    "MCPReply", "MCPServerConfig", "MCPServerRegistry", "ToolSnapshot",
    "default_registry_path", "digest", "environment_credentials", "load_registry",
    "render_entry", "resolve_credentials", "review",
]
