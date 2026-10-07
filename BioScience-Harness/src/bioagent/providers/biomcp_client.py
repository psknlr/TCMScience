"""BioMCP through the reviewed MCP transport: started, checked, called, and read.

``bioagent.providers.biomcp`` holds what review found of BioMCP 0.7.3, as data: which tools
are admitted, their schemas, the arguments the server accepts and drops, how to read its
replies. ``bioagent.mcp`` connects to reviewed servers. This module joins them, in the order
the review requires, because each step guards against something the next cannot see:

1. **The installed release is the reviewed one** (``check_installed``). BioMCP's own
   ``initialize`` answer reports the MCP SDK's version, so a different BioMCP would be
   recorded as the reviewed one and nothing else would notice.
2. **The server starts from the reviewed command, in a directory the run owns**, with its
   HTTP cache inside it (the entry's ``settings``). A cache shared between runs lets one
   run's answer be recorded as another run's retrieval.
3. **``tools/list`` is checked twice**: by the transport against its snapshot (allowlist,
   schemas, descriptions) and by the provider against its own (``check_listing``). Either
   refusal stops the server; the two pin sets are the same review, read two ways, and a
   disagreement between them means one of them is stale.
4. **Each call sends ``arguments_for``** (fixed arguments applied, the ones 0.7.3 drops
   refused before anything is sent) **and returns ``adapt``'s reading of the reply** with
   the transport's provenance. BioMCP reports some errors as successful replies, so the
   protocol's ``isError`` alone is not the call's status.

While the configuration's ``status`` is ``draft`` the client serves only a development run
that asks for it (``allow_draft=True``), and every result says so. A person reviews the
configuration and sets ``status: reviewed``; nothing here does that for them.

To put BioMCP's tools behind PSH's gates rather than call them directly, admit the open
connection: ``bioagent.psh.admit_mcp_server(kernel, client.connection)``. The kernel then
rules on each call (destination ``PUBLIC_REMOTE``), and its replies are read with ``adapt``.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from ..status import ExecutionStatus
from .biomcp import (AdaptedReply, ServerConfig, adapt, arguments_for, check_installed,
                     check_listing, load_server_config)

__all__ = ["BioMCPCall", "BioMCPClient"]

#: The placeholders ``registry/biomcp_server.yaml`` uses, and nothing else.
_PYTHON, _RUN_DIR = "{python}", "{run_dir}"


@dataclass(frozen=True)
class BioMCPCall:
    """One call: the adapted reply, and what the transport records about who answered."""

    reply: AdaptedReply
    provenance: Mapping[str, Any] = field(default_factory=dict)

    @property
    def status(self) -> ExecutionStatus:
        return self.reply.status


class BioMCPClient:
    """One BioMCP server for one run. Use as a context manager, or ``open`` and ``close``.

    ``run_dir`` is an empty directory the run owns: the server's working directory and its
    cache. ``python`` is the interpreter whose environment has the reviewed BioMCP (by
    default this one); it is checked before the server starts.
    """

    def __init__(self, run_dir: str | Path, *, python: str | None = None,
                 config: ServerConfig | None = None, allow_draft: bool = False,
                 connect_timeout_s: float = 30.0, call_timeout_s: float = 60.0) -> None:
        self.config = config or load_server_config()
        self.run_dir = Path(run_dir).resolve()
        self.python = python or sys.executable
        self.allow_draft = allow_draft
        self.connect_timeout_s = connect_timeout_s
        self.call_timeout_s = call_timeout_s
        self.connection: Any = None

    # ------------------------------------------------------------ the entry
    def server_config(self) -> Any:
        """The transport's reviewed entry for this run: the provider's review, filled in.

        Refuses a configuration the transport cannot honour as written: a placeholder it
        does not know, or an inherited variable the transport does not hand on.
        """
        from ..mcp import MCPConfigError, MCPServerConfig, ToolSnapshot
        from ..mcp.connection import PROXY_VARIABLES

        cfg = self.config
        problems = [f"env.pass names {var!r}, which the MCP transport does not hand on"
                    for var in cfg.env_pass if var not in PROXY_VARIABLES]
        if not cfg.command:
            problems.append("server.command is empty")
        if problems:
            raise MCPConfigError(f"BioMCP configuration {cfg.path}", problems)
        command = self.python if cfg.command[0] == _PYTHON else self._fill(cfg.command[0])
        return MCPServerConfig(
            id=cfg.id, transport="stdio", command=command,
            args=tuple(self._fill(a) for a in cfg.command[1:]),
            cwd=self._fill(cfg.cwd) if cfg.cwd else str(self.run_dir),
            settings={k: self._fill(v) for k, v in cfg.env_set.items()},
            destination=cfg.destination.lower(), package=cfg.package, version=cfg.version,
            connect_timeout_s=self.connect_timeout_s, call_timeout_s=self.call_timeout_s,
            tools={name: ToolSnapshot(input_schema=policy.input_schema_sha256,
                                      output_schema=policy.output_schema_sha256,
                                      annotations=policy.annotations_sha256,
                                      description=policy.description_sha256)
                   for name, policy in cfg.tools.items()},
            note=f"from {Path(cfg.path).name} ({cfg.status}, reviewed {cfg.reviewed_on})")

    def _fill(self, value: str) -> str:
        filled = value.replace(_RUN_DIR, str(self.run_dir))
        if "{" in filled:
            from ..mcp import MCPConfigError
            raise MCPConfigError(f"BioMCP configuration {self.config.path}",
                                 [f"{value!r} has a placeholder other than {_RUN_DIR}"])
        return filled

    # ------------------------------------------------------------ lifecycle
    def open(self) -> "BioMCPClient":
        """Check the release, start the server, and check its tools both ways.

        Raises ``MCPCallError``: UNAVAILABLE when the reviewed BioMCP is not what is
        installed or the server cannot be started, DENIED when the configuration is a draft
        and no development run asked for it, or when either check refuses a tool.
        """
        from ..backends.concrete import MCPCallError
        from ..mcp import MCPConnection

        if self.connection is not None:
            return self
        meta = {"server": self.config.id, "configuration": self.config.status}
        if self.config.status != "reviewed" and not self.allow_draft:
            raise MCPCallError(
                f"the BioMCP configuration ({self.config.path}) is a {self.config.status}; "
                "a person reviews it and sets status: reviewed before it serves a run, or a "
                "development run passes allow_draft=True", status=ExecutionStatus.DENIED,
                metadata=meta)
        why = self._installed_problem()
        if why:
            raise MCPCallError(f"BioMCP cannot be started as reviewed: {why}",
                               status=ExecutionStatus.UNAVAILABLE, metadata=meta)
        self.run_dir.mkdir(parents=True, exist_ok=True)
        connection = MCPConnection(self.server_config()).open()
        listing = check_listing(connection.descriptors(), self.config)
        refusals = [f"{name}: {why}" for name, why in sorted(connection.refused.items())]
        refusals += [f"{name}: {why}" for name, why in listing.refused]
        refusals += [f"{name}: not listed" for name in listing.missing]
        if refusals:
            connection.close()
            raise MCPCallError("BioMCP's tools do not match their review: "
                               + "; ".join(refusals), status=ExecutionStatus.DENIED,
                               metadata=meta)
        self.connection = connection
        return self

    def _installed_problem(self) -> str:
        """Why the BioMCP in ``python``'s environment is not the reviewed release."""
        if Path(self.python).resolve() == Path(sys.executable).resolve():
            return check_installed(self.config)
        probe = ("import importlib.metadata as m, sys\n"
                 "try:\n    print(m.version(sys.argv[1]))\n"
                 "except m.PackageNotFoundError:\n    print('')\n")
        try:
            done = subprocess.run([self.python, "-I", "-c", probe, self.config.package],
                                  capture_output=True, text=True, timeout=60, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return f"{self.python} could not be asked ({type(exc).__name__}: {exc})"
        version = done.stdout.strip()
        if not version:
            return f"{self.config.package} is not installed for {self.python}"
        if version != self.config.version:
            return (f"{self.config.package} {version} is installed for {self.python}; the "
                    f"configuration was reviewed against {self.config.version}")
        return ""

    def close(self) -> None:
        if self.connection is not None:
            self.connection.close()
            self.connection = None

    def __enter__(self) -> "BioMCPClient":
        return self.open()

    def __exit__(self, *exc: object) -> None:
        self.close()

    @property
    def admitted(self) -> tuple[str, ...]:
        return tuple(self.connection.tools) if self.connection is not None else ()

    # ----------------------------------------------------------------- calls
    def call(self, tool: str, arguments: Mapping[str, Any] | None = None) -> BioMCPCall:
        """Call one admitted tool and read its reply. Every outcome is returned, not raised.

        Arguments are shaped before anything is sent, so a call the review refuses (an
        argument 0.7.3 drops, a tool not admitted) is DENIED without reaching the server.
        """
        from ..backends.concrete import MCPCallError

        given = dict(arguments or {})
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        meta: dict[str, Any] = {"server": self.config.id, "tool": tool,
                                "configuration": self.config.status}
        try:
            sent = arguments_for(tool, given, self.config)
        except ValueError as exc:
            return self._refused(tool, given, now, ExecutionStatus.DENIED, str(exc), meta)
        if self.connection is None:
            return self._refused(tool, sent, now, ExecutionStatus.UNAVAILABLE,
                                 "the BioMCP client is not open", meta)
        meta = {**self.connection.provenance(tool), "configuration": self.config.status}
        try:
            raw = self.connection.call_tool(tool, sent)
        except MCPCallError as exc:
            return self._refused(tool, sent, now, exc.status, exc.reason,
                                 {**meta, **exc.metadata})
        return BioMCPCall(adapt(tool, raw, arguments=sent, retrieved_at=now,
                                config=self.config), meta)

    def _refused(self, tool: str, query: Mapping[str, Any], when: str,
                 status: ExecutionStatus, reason: str,
                 meta: Mapping[str, Any]) -> BioMCPCall:
        return BioMCPCall(AdaptedReply(tool, dict(query), when, self.config.server, status,
                                       reason[:600], (), ""), dict(meta))
