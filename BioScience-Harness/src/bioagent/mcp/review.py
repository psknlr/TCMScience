"""Review a server before it is admitted: pin what it offers now, for a person to read.

A snapshot vouches for exactly what someone read, so the reading has to happen. ``review``
connects to the server a draft entry describes, lists its tools without checking them and
without calling any, and returns the entry with each drafted tool's digests filled in —
together with each of those tools in full, so the person who commits the entry reads the
schemas and descriptions they are vouching for rather than a column of hashes.
``python -m bioagent.mcp review DRAFT`` prints both, ready to paste into the registry.
"""

from __future__ import annotations

import dataclasses
import json
from typing import Any, Mapping

from .config import MCPConfigError, MCPServerConfig, ToolSnapshot
from .connection import MCPConnection
from .dispatcher import CredentialSource, environment_credentials, resolve_credentials

__all__ = ["render_entry", "review"]

#: Stands in for a digest while a draft is validated; never written out.
_UNREVIEWED = "0" * 64


def review(draft: Mapping[str, Any], *,
           credentials: CredentialSource | None = environment_credentials
           ) -> tuple[MCPServerConfig, dict[str, dict[str, Any]]]:
    """The reviewed entry for ``draft``, and each drafted tool as the server offers it now.

    ``draft`` is a registry entry whose ``tools`` lists the names to allow (a mapping is
    read for its keys; any digests in it are ignored). A drafted tool the server does not
    offer, or offers more than once, is an error: there is no single thing to pin.
    """
    raw = dict(draft)
    names = [str(name) for name in (raw.get("tools") or [])]
    config = MCPServerConfig.from_dict(
        {**raw, "tools": {name: {"input_schema": _UNREVIEWED} for name in names}})
    env = resolve_credentials(config, credentials)
    with MCPConnection(config, env=env, review=True) as connection:
        offered = connection.offered
    problems = [f"tool {name!r} is offered {len(offered.get(name, []))} times, not once"
                for name in names if len(offered.get(name, [])) != 1]
    if problems:
        raise MCPConfigError(f"review of MCP server {config.id!r}", problems)
    listing = {name: offered[name][0] for name in names}
    reviewed = dataclasses.replace(
        config, tools={name: ToolSnapshot.of(tool) for name, tool in listing.items()})
    return reviewed, listing


def render_entry(config: MCPServerConfig, listing: Mapping[str, Mapping[str, Any]]) -> str:
    """The entry as YAML for the registry, after each tool it pins, in full, as comments."""
    import yaml

    lines = [f"# Reviewed entry for MCP server {config.id!r} ({config.package} "
             f"{config.version}). Read each tool below before committing it."]
    for name, tool in listing.items():
        lines.append(f"# --- {name}")
        for key in ("description", "inputSchema", "outputSchema", "annotations"):
            if key in tool:
                value = json.dumps(tool[key], ensure_ascii=False, sort_keys=True)
                lines.append(f"#   {key}: {value}")
    body = yaml.safe_dump([config.to_dict()], sort_keys=False, allow_unicode=True)
    return "\n".join(lines) + "\n" + body
