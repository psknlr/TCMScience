"""Reviewed MCP servers: what this installation may connect to, pinned to what review saw.

A server describes its own tools (``tools/list``), and that description is the server's
claim, not a policy — ``psh.protocols.mcp`` says the same of tool annotations. An entry
here is the operator's statement of what a server cannot be trusted to say about itself:

* **which tools may be called.** ``tools`` is an allowlist. A tool the server offers and
  the entry does not name is never exposed and never called.
* **what those tools were when someone read them.** Each allowlisted tool carries the
  SHA-256 of its canonical ``inputSchema``, and of its ``outputSchema``, annotations and
  description when it has them. An upgrade that changes any of them is refused, with the
  change named, until the entry is reviewed again. Without the pin, a review of one
  version of a tool would vouch for whatever the server offers later — including a
  description rewritten to steer the model that reads it.
* **where a call goes.** ``destination`` (``trusted_remote`` | ``public_remote``) becomes
  the PSH destination the gates rule on, so PHI cannot reach a public server, by the same
  rule that keeps it from a public model.

No secret is ever written here. ``env`` maps a variable the server process receives to
the *name* of a credential the process starting the server resolves, and a value that is
not a plain upper-case name is refused, so a pasted token fails validation instead of
being committed. Unknown keys are refused for the same reason: a misspelt ``tools`` would
otherwise drop the allowlist it was meant to carry, silently.

``MCPServerConfig.digest`` — SHA-256 over the canonical entry — identifies exactly what was
admitted. It is recorded on every result, and the isolated child refuses a configuration
whose digest is not the one it was handed.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Iterable, Mapping

__all__ = ["API_VERSION", "DESTINATIONS", "MCPConfigError", "MCPServerConfig",
           "MCPServerRegistry", "REGISTRY_FILE", "TRANSPORTS", "ToolSnapshot",
           "default_registry_path", "digest", "load_registry"]

API_VERSION = "1"
REGISTRY_FILE = "mcp_servers.yaml"
TRANSPORTS = ("stdio", "streamable_http")
DESTINATIONS = ("trusted_remote", "public_remote")

#: A server id is one plain identifier: it becomes part of component ids
#: (``mcp.<server>.<tool>``), so a dot in it would make those ambiguous.
_ID = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")
#: The MCP specification's tool-name alphabet and length.
_TOOL = re.compile(r"[A-Za-z0-9_.-]{1,128}")
_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}")
#: A credential *name*. Upper-case only, so the usual token shapes (``ghp_…``, ``sk-…``,
#: anything with lower case or punctuation) cannot pass as a name by accident.
_CREDENTIAL = re.compile(r"[A-Z][A-Z0-9_]{0,127}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_LOOPBACK = frozenset({"localhost", "127.0.0.1", "::1"})
_MAX_TIMEOUT_S = 3600.0

#: Variables a reviewed entry may not set for a server process. The proxy variables are
#: the egress route the starting process hands on (in PSH's isolated child, the kernel's
#: proxy); the loader and interpreter variables decide which code the server runs. PSH's
#: isolated runner reserves the same families for its own children.
_RESERVED_ENV = frozenset({
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "FTP_PROXY",
    "PATH", "PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP", "IFS", "BASH_ENV", "ENV",
    "SHELLOPTS", "GLIBC_TUNABLES", "NODE_OPTIONS", "PERL5OPT", "RUBYOPT"})
_RESERVED_PREFIXES = ("LD_", "DYLD_")

_SERVER_KEYS = frozenset({"id", "transport", "command", "args", "cwd", "url", "env",
                          "destination", "call_timeout_s", "connect_timeout_s", "package",
                          "version", "tools", "note"})
_SNAPSHOT_PARTS = (("input_schema", "inputSchema"), ("output_schema", "outputSchema"),
                   ("annotations", "annotations"), ("description", "description"))


def digest(value: Any) -> str:
    """SHA-256 over canonical JSON: sorted keys, compact separators, UTF-8, no NaN.

    The rule of ``contracts.source_card.canonical_hash`` without its ``default=str``: a
    value that is not JSON is an error here, not a string that happens to hash.
    """
    blob = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _short(value: str) -> str:
    return value[:12] if value else "none"


class MCPConfigError(ValueError):
    """An entry or a registry that cannot be admitted, with every reason at once."""

    def __init__(self, subject: str, problems: Iterable[str]) -> None:
        self.subject = subject
        self.problems = tuple(problems)
        super().__init__(f"{subject}: " + "; ".join(self.problems))


@dataclass(frozen=True)
class ToolSnapshot:
    """What review saw of one tool, as digests. Empty means the tool declared no such part."""

    input_schema: str
    output_schema: str = ""
    annotations: str = ""
    description: str = ""

    @classmethod
    def of(cls, tool: Mapping[str, Any]) -> "ToolSnapshot":
        """The snapshot of a tool as ``tools/list`` sends it (the protocol's camelCase)."""
        def part(key: str) -> str:
            value = tool.get(key)
            return digest(value) if value not in (None, "", {}) else ""
        return cls(input_schema=digest(tool.get("inputSchema") or {}),
                   output_schema=part("outputSchema"), annotations=part("annotations"),
                   description=part("description"))

    def drift(self, offered: "ToolSnapshot") -> tuple[str, ...]:
        """Each part of ``offered`` that is not what review saw, named with both digests."""
        return tuple(f"{wire} sha256 reviewed {_short(getattr(self, key))}, offered "
                     f"{_short(getattr(offered, key))}"
                     for key, wire in _SNAPSHOT_PARTS
                     if getattr(self, key) != getattr(offered, key))

    def to_dict(self) -> dict[str, str]:
        return {key: getattr(self, key) for key, _ in _SNAPSHOT_PARTS if getattr(self, key)}

    @classmethod
    def parse(cls, raw: Any, where: str) -> tuple["ToolSnapshot | None", list[str]]:
        if not isinstance(raw, Mapping):
            return None, [f"{where} must map snapshot parts to sha256 digests"]
        problems: list[str] = []
        unknown = sorted(set(map(str, raw)) - {key for key, _ in _SNAPSHOT_PARTS})
        if unknown:
            problems.append(f"{where} has unknown keys {unknown}")
        values: dict[str, str] = {}
        for key, _ in _SNAPSHOT_PARTS:
            value = raw.get(key) or ""
            if key == "input_schema" and not value:
                problems.append(f"{where}.input_schema is required")
            elif value and not (isinstance(value, str) and _SHA256.fullmatch(value)):
                problems.append(f"{where}.{key} must be a lower-case sha256 hex digest")
            else:
                values[key] = value
        return (None if problems else cls(**values)), problems


@dataclass(frozen=True)
class MCPServerConfig:
    """One reviewed server: how to reach it, where calls go, and what may be called."""

    id: str
    transport: str
    tools: Mapping[str, ToolSnapshot]
    package: str
    version: str
    destination: str = "public_remote"
    command: str = ""
    args: tuple[str, ...] = ()
    cwd: str = ""
    url: str = ""
    env: Mapping[str, str] = field(default_factory=dict)
    call_timeout_s: float = 30.0
    connect_timeout_s: float = 15.0
    note: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "args", tuple(str(a) for a in self.args))
        object.__setattr__(self, "tools", MappingProxyType(dict(sorted(self.tools.items()))))
        object.__setattr__(self, "env", MappingProxyType(dict(sorted(self.env.items()))))
        for key in ("call_timeout_s", "connect_timeout_s"):
            value = getattr(self, key)
            if isinstance(value, int) and not isinstance(value, bool):
                object.__setattr__(self, key, float(value))
        problems = self._problems()
        if problems:
            raise MCPConfigError(f"MCP server {self.id!r}", problems)

    def _problems(self) -> list[str]:
        out: list[str] = []
        if not _ID.fullmatch(self.id):
            out.append("id must be a plain lower-case identifier ([a-z0-9][a-z0-9_-]*, "
                       "at most 64 characters)")
        if self.transport not in TRANSPORTS:
            out.append(f"transport {self.transport!r} is not one of {list(TRANSPORTS)}")
        if self.destination not in DESTINATIONS:
            out.append(f"destination {self.destination!r} is not one of {list(DESTINATIONS)}"
                       "; an MCP server is a process this kernel does not own, never local")
        if self.transport == "stdio":
            out += self._stdio_problems()
        elif self.transport == "streamable_http":
            out += self._http_problems()
        for var, credential in self.env.items():
            if not _ENV_NAME.fullmatch(var) or var.upper() in _RESERVED_ENV \
                    or var.upper().startswith(_RESERVED_PREFIXES):
                out.append(f"env variable {var!r} is not a name a reviewed entry may set")
            if not _CREDENTIAL.fullmatch(credential):
                out.append(f"env {var!r} must name a credential (UPPER_SNAKE_CASE), never "
                           "hold a value; the starting process resolves the name")
        for label, value in (("call_timeout_s", self.call_timeout_s),
                             ("connect_timeout_s", self.connect_timeout_s)):
            if isinstance(value, bool) or not isinstance(value, (int, float)) \
                    or not math.isfinite(value) or not 0 < value <= _MAX_TIMEOUT_S:
                out.append(f"{label} must be a number of seconds in (0, {_MAX_TIMEOUT_S:g}]")
        if not self.package or not self.version:
            out.append("package and version are required: they record which server was "
                       "reviewed, and every result carries them")
        if not self.tools:
            out.append("tools must allowlist at least one tool, each with its reviewed "
                       "snapshot")
        for name, snapshot in self.tools.items():
            if not _TOOL.fullmatch(name):
                out.append(f"tool name {name!r} is not a valid MCP tool name")
            if not isinstance(snapshot, ToolSnapshot):
                out.append(f"tools.{name} is not a ToolSnapshot")
        return out

    def _stdio_problems(self) -> list[str]:
        out = []
        if not self.command:
            out.append("a stdio server needs a command")
        elif "/" in self.command and not Path(self.command).is_absolute():
            # The isolated child runs in its own working directory, so a relative path would
            # name a different file there than where the entry was reviewed.
            out.append("command must be a bare name found on PATH or an absolute path")
        if self.cwd and not Path(self.cwd).is_absolute():
            out.append("cwd must be an absolute path")
        if self.url:
            out.append("a stdio server takes no url")
        return out

    def _http_problems(self) -> list[str]:
        out = []
        if self.command or self.args or self.cwd:
            out.append("a streamable_http server takes a url, not a command, args or cwd")
        if self.env:
            out.append("env sets a server process's variables, and a streamable_http server "
                       "is not a process this harness starts")
        parts = urllib.parse.urlsplit(self.url)
        host = (parts.hostname or "").lower()
        if parts.scheme not in ("https", "http") or not host:
            out.append("url must be an http(s) URL with a host")
        elif parts.scheme == "http" and host not in _LOOPBACK:
            out.append("plain http is accepted only for a loopback server; tool arguments "
                       "would otherwise cross the network in clear text")
        if parts.username or parts.password:
            out.append("url must not carry credentials")
        return out

    # ---------------------------------------------------------------- derived
    @property
    def allowlist(self) -> tuple[str, ...]:
        return tuple(self.tools)

    @property
    def credentials(self) -> tuple[str, ...]:
        """The credential names this server needs, never their values."""
        return tuple(dict.fromkeys(self.env.values()))

    @property
    def host(self) -> str:
        """The host a streamable_http server is reached at; empty for stdio."""
        return (urllib.parse.urlsplit(self.url).hostname or "").lower() if self.url else ""

    @property
    def digest(self) -> str:
        return digest(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        """The entry as plain data: what the registry file holds and what the digest covers."""
        out: dict[str, Any] = {"id": self.id, "transport": self.transport}
        if self.transport == "stdio":
            out["command"] = self.command
            if self.args:
                out["args"] = list(self.args)
            if self.cwd:
                out["cwd"] = self.cwd
        else:
            out["url"] = self.url
        if self.env:
            out["env"] = dict(self.env)
        out.update(destination=self.destination, call_timeout_s=self.call_timeout_s,
                   connect_timeout_s=self.connect_timeout_s, package=self.package,
                   version=self.version,
                   tools={name: snap.to_dict() for name, snap in self.tools.items()})
        if self.note:
            out["note"] = self.note
        return out

    @classmethod
    def from_dict(cls, raw: Any) -> "MCPServerConfig":
        """Parse one registry entry, refusing it with every problem found."""
        if not isinstance(raw, Mapping):
            raise MCPConfigError("MCP server entry", ["an entry must be a mapping"])
        subject = f"MCP server {raw.get('id')!r}"
        problems: list[str] = []
        unknown = sorted(set(map(str, raw)) - _SERVER_KEYS)
        if unknown:
            problems.append(f"unknown keys {unknown}")

        def text(key: str) -> str:
            value = raw.get(key, "")
            if value is None:
                return ""
            if not isinstance(value, str):
                problems.append(f"{key} must be a string")
                return ""
            return value

        args = raw.get("args") or []
        if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
            problems.append("args must be a list of strings")
            args = []
        env = raw.get("env") or {}
        if not isinstance(env, Mapping) or not all(
                isinstance(k, str) and isinstance(v, str) for k, v in env.items()):
            problems.append("env must map variable names to credential names (strings)")
            env = {}
        tools: dict[str, ToolSnapshot] = {}
        tools_raw = raw.get("tools")
        if not isinstance(tools_raw, Mapping):
            problems.append("tools must map each allowlisted tool name to its reviewed "
                            "snapshot")
        else:
            for name, snapshot_raw in tools_raw.items():
                snapshot, why = ToolSnapshot.parse(snapshot_raw, f"tools.{name}")
                problems += why
                if snapshot is not None:
                    tools[str(name)] = snapshot
        timeouts = {key: raw.get(key, default) for key, default in
                    (("call_timeout_s", 30.0), ("connect_timeout_s", 15.0))}
        fields = {key: text(key) for key in ("id", "transport", "command", "cwd", "url",
                                             "package", "version", "note")}
        destination = text("destination") or "public_remote"
        if problems:
            raise MCPConfigError(subject, problems)
        return cls(tools=tools, args=tuple(args), env=dict(env), destination=destination,
                   **timeouts, **fields)


class MCPServerRegistry:
    """The reviewed servers, and every entry that was refused and why.

    A malformed entry is refused on its own and the rest stay usable; a server whose entry
    was refused answers with that reason rather than "not configured". A registry whose
    file could not be read as a whole carries ``error`` instead, and refuses every server
    with it.
    """

    def __init__(self, servers: Iterable[MCPServerConfig] = (), *,
                 refused: Iterable[tuple[str, str]] = (), source: str = "",
                 error: str = "") -> None:
        held: dict[str, MCPServerConfig] = {}
        refusals = [(str(sid), str(why)) for sid, why in refused]
        duplicated: set[str] = set()
        for config in servers:
            if config.id in held or config.id in duplicated:
                duplicated.add(config.id)
                held.pop(config.id, None)
            else:
                held[config.id] = config
        for sid in sorted(duplicated):
            # Neither copy is admitted: keeping one would let file order decide which
            # reviewed entry is real.
            refusals.append((sid, "declared more than once; no copy is admitted"))
        self.servers: Mapping[str, MCPServerConfig] = MappingProxyType(
            dict(sorted(held.items())))
        self.refused = tuple(refusals)
        self.source = source
        self.error = error

    def get(self, server: str) -> MCPServerConfig | None:
        return self.servers.get(server)

    @property
    def empty(self) -> bool:
        """Nothing configured at all: no server, no refused entry, no unreadable file."""
        return not (self.servers or self.refused or self.error)

    def why_not(self, server: str) -> str:
        """Why ``server`` is not served, in words a caller can act on."""
        where = self.source or "the registry given in memory"
        if self.error:
            return f"the reviewed MCP registry {where} was refused as a whole: {self.error}"
        for sid, why in self.refused:
            if sid == server:
                return f"MCP server {server!r} was refused when {where} was loaded: {why}"
        return (f"MCP server {server!r} is not in the reviewed MCP registry ({where}); "
                "only reviewed servers are connected")

    def subset(self, servers: Iterable[str]) -> "MCPServerRegistry":
        wanted = set(servers)
        return MCPServerRegistry([c for sid, c in self.servers.items() if sid in wanted],
                                 source=self.source)

    def to_dict(self) -> dict[str, Any]:
        return {"api_version": API_VERSION,
                "servers": [config.to_dict() for config in self.servers.values()]}

    @property
    def digest(self) -> str:
        """SHA-256 over the admitted entries: what a process holding them may reach."""
        return digest(self.to_dict())

    def write(self, path: str | Path) -> Path:
        """Write the admitted entries, owner-only, as JSON (which ``load_registry`` reads)."""
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False) + "\n",
                          encoding="utf-8")
        try:
            os.chmod(target, 0o600)
        except OSError:                                   # pragma: no cover - platform
            pass
        return target

    @classmethod
    def from_dict(cls, raw: Any, *, source: str = "") -> "MCPServerRegistry":
        """A registry from parsed file content. A file-level problem raises; a bad entry is
        refused on its own."""
        if not isinstance(raw, Mapping):
            raise MCPConfigError(source or "MCP registry", ["the file must hold a mapping"])
        problems = []
        unknown = sorted(set(map(str, raw)) - {"api_version", "servers"})
        if unknown:
            problems.append(f"unknown keys {unknown}")
        if str(raw.get("api_version")) != API_VERSION:
            problems.append(f"api_version must be {API_VERSION!r}")
        entries = raw.get("servers")
        if not isinstance(entries, list):
            problems.append("servers must be a list (empty when nothing is configured)")
        if problems:
            raise MCPConfigError(source or "MCP registry", problems)
        configs: list[MCPServerConfig] = []
        refused: list[tuple[str, str]] = []
        for index, entry in enumerate(entries):
            try:
                configs.append(MCPServerConfig.from_dict(entry))
            except MCPConfigError as exc:
                sid = entry.get("id") if isinstance(entry, Mapping) else None
                refused.append((str(sid) if sid else f"#{index}", "; ".join(exc.problems)))
        return cls(configs, refused=refused, source=source)

    def __len__(self) -> int:
        return len(self.servers)

    def __repr__(self) -> str:  # pragma: no cover - diagnostics
        return (f"<MCPServerRegistry {sorted(self.servers)} refused={len(self.refused)} "
                f"source={self.source!r}>")


def default_registry_path() -> Path:
    """``registry/mcp_servers.yaml`` beside the reviewed skills (installed: the package's
    copy)."""
    from ..config import registry_dir

    return registry_dir() / REGISTRY_FILE


def load_registry(path: str | Path | None = None) -> MCPServerRegistry:
    """Read and validate a registry file; ``None`` reads the shipped one.

    The shipped file may be absent and then configures nothing. A path given explicitly
    must exist: asking for a file that is not there is a mistake to report, not an empty
    registry to proceed with.
    """
    import yaml

    target = Path(path) if path is not None else default_registry_path()
    if not target.is_file():
        if path is not None:
            raise MCPConfigError(str(target), ["no such registry file"])
        return MCPServerRegistry(source=str(target))
    try:
        raw = yaml.safe_load(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise MCPConfigError(str(target), [f"unreadable: {type(exc).__name__}: {exc}"]) \
            from None
    return MCPServerRegistry.from_dict(raw, source=str(target))
