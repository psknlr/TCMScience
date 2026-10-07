"""A reviewed subset of ToolUniverse, admitted as governed components.

ToolUniverse already registers and discovers more than two thousand scientific tools; this
module does not compete with that. It admits the few that TCMScience's workflows use, each
under a fixed id (``tooluniverse.<upstream name>``), with the schema, hosts and licence a
reviewer read, and refuses everything else. The review lives in
``registry/tooluniverse_allowlist.yaml``; this module checks the installed package against
it and runs only what it names.

Discovery reads the package's JSON tool configuration without importing the package. Each
allowlisted tool becomes a ``python`` component: its ``parameter`` schema is the
component's inputs (with its ``test_examples`` as JSON Schema ``examples``, the one the
review names being the smoke test), its ``return_schema`` the outputs, the reviewed hosts
its ``permissions.network``. The data licence is what the configuration states, which for every
tool reviewed so far is nothing, so it is ``unknown``: the records come from UniProt,
ChEMBL or PubChem under their own terms, and a configuration that does not say which terms
is not taken to have said permissive ones.

A tool is quarantined, never quietly used, when what is installed is not what was reviewed:
another package version, a configuration entry whose digest differs, an implementation
class that changed, an endpoint on a host nobody declared, or an API key now required. The
reason names the difference.

Execution is a ``python`` entrypoint per tool (``run_<name>``, resolved lazily below). The
first call imports ToolUniverse, builds an engine holding that one tool
(``ToolUniverse(load_workspace=False)`` then ``load_tools(include_tools=[name])``), checks
that the loaded configuration and implementation are the reviewed ones, and keeps the
engine for later calls. Four things ToolUniverse 1.5.6 does by default would make the
record wrong, and each is handled:

* its result cache would answer from ``~/.tooluniverse/cache.sqlite`` — a write outside the
  component's declared permissions, and a stale answer recorded as a fresh call;
* its logger writes to standard output, which the PSH isolated executor reads as the
  result;
* ``load_workspace=False`` stops the ``.env`` and profile loading but not the scan of
  ``./.tooluniverse`` for user tools, whose Python files it imports — code from the
  working directory would run inside the call, and a JSON file there replaces a built-in
  tool of the same name. The engine is given no workspace to scan;
* ``run_one_function`` reports failures as values (``{"status": "error", ...}``), not
  exceptions; :func:`classify` maps them to an :class:`ExecutionStatus`, and a failed call
  raises :class:`ToolUniverseCallError` carrying it, so a failure is never recorded as a
  result.
"""

from __future__ import annotations

import contextlib
import hashlib
import importlib.metadata
import importlib.util
import json
import os
import re
import sys
import threading
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping

from ..runtime.component import (ComponentManifest, LicenseSpec, Permissions, Provider,
                                 Requirements, RuntimeSpec, Validation)
from ..status import ExecutionStatus, LifecycleState
from .base import Provider as ProviderBase

__all__ = ["PACKAGE", "NAMESPACE", "ReviewedTool", "Allowlist", "Installed",
           "ToolUniverseProvider", "ToolUniverseCallError", "load_allowlist", "installed",
           "config_entry", "entry_digest", "classify", "call", "review", "smoke_arguments"]

PACKAGE = "tooluniverse"
NAMESPACE = "tooluniverse."
#: Entrypoint attributes are ``run_<upstream name>`` of this module; see :func:`__getattr__`.
#: Spelled out rather than ``__name__``, which is ``__main__`` when the module is run.
_MODULE = "bioagent.providers.tooluniverse"
ENTRY_PREFIX = "run_"
#: Keys ToolUniverse adds to a configuration entry when it loads it. Anything else that
#: differs from the file is not the reviewed configuration.
_LOADER_KEYS = frozenset({"source_file", "category", "mcp_annotations"})
#: Error types of ToolUniverse 1.5.6 (``tooluniverse.exceptions``) that mean the tool could
#: not be reached or set up, rather than that it ran and failed.
_UNAVAILABLE_TYPES = frozenset({"ToolUnavailableError", "ToolAuthError",
                                "ToolDependencyError", "ToolConfigError"})
#: Error text of a request that never reached its host (requests and urllib3 wording).
_UNREACHABLE = ("proxyerror", "connection refused", "failed to establish",
                "name resolution", "max retries exceeded", "network is unreachable",
                "connectionerror")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class ToolUniverseCallError(RuntimeError):
    """A call that did not succeed, with the status it ended in.

    ``execution_status`` is what ``PythonBackend`` records, so a timeout stays a timeout
    and an unreachable host stays UNAVAILABLE instead of becoming a generic FAILED.
    """

    def __init__(self, status: ExecutionStatus, reason: str) -> None:
        super().__init__(f"{status.value}: {reason}")
        self.execution_status = status
        self.reason = reason


# ------------------------------------------------------------------------- the review
@dataclass(frozen=True)
class ReviewedTool:
    """One allowlist entry: what a reviewer read and admitted."""

    name: str
    config_file: str                 # under the package's data/ directory
    entry_sha256: str                # the tool's configuration entry, canonical JSON
    implementation: str              # module.Class that runs it
    hosts: tuple[str, ...]
    domain: str
    purpose: str
    data_terms: str = ""             # where the source states its terms, for the reader
    smoke: int = 0                   # index into the configuration's test_examples
    timeout_s: float = 60.0
    live_check: Mapping[str, Any] = field(default_factory=dict)

    @property
    def component_id(self) -> str:
        return NAMESPACE + self.name


@dataclass(frozen=True)
class Allowlist:
    """The reviewed set, and the package release it was reviewed against."""

    reviewed_version: str
    reviewed_on: str
    package_licence: str
    repo: str
    tools: tuple[ReviewedTool, ...]
    config_files: Mapping[str, str] = field(default_factory=dict)
    path: str = ""

    def get(self, name: str) -> ReviewedTool | None:
        return next((t for t in self.tools if t.name == name), None)


def default_allowlist_path() -> Path:
    from ..config import registry_dir
    return registry_dir() / "tooluniverse_allowlist.yaml"


_ALLOWLISTS: dict[tuple[str, float], Allowlist] = {}


def load_allowlist(path: str | Path | None = None) -> Allowlist:
    """Read and check the allowlist. A malformed entry is an error, not a skipped tool."""
    import yaml

    target = Path(path) if path else default_allowlist_path()
    key = (str(target.resolve()), target.stat().st_mtime)
    if key in _ALLOWLISTS:
        return _ALLOWLISTS[key]
    doc = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    if doc.get("package") != PACKAGE:
        raise ValueError(f"{target}: not a {PACKAGE} allowlist")
    tools = []
    for raw in doc.get("tools") or ():
        missing = [k for k in ("name", "config_file", "entry_sha256", "implementation",
                               "hosts", "domain", "purpose") if not raw.get(k)]
        if missing:
            raise ValueError(f"{target}: entry {raw.get('name')!r} lacks {missing}")
        if not re.fullmatch(r"[0-9a-f]{64}", str(raw["entry_sha256"])):
            raise ValueError(f"{target}: {raw['name']}: entry_sha256 is not a sha256")
        tools.append(ReviewedTool(
            name=str(raw["name"]), config_file=str(raw["config_file"]),
            entry_sha256=str(raw["entry_sha256"]), implementation=str(raw["implementation"]),
            hosts=tuple(str(h) for h in raw["hosts"]), domain=str(raw["domain"]),
            purpose=" ".join(str(raw["purpose"]).split()),
            data_terms=str(raw.get("data_terms") or ""), smoke=int(raw.get("smoke") or 0),
            timeout_s=float(raw.get("timeout_s") or 60.0),
            live_check=dict(raw.get("live_check") or {})))
    names = [t.name for t in tools]
    if len(set(names)) != len(names):
        raise ValueError(f"{target}: a tool is listed twice")
    allow = Allowlist(reviewed_version=str(doc["reviewed_version"]),
                      reviewed_on=str(doc["reviewed_on"]),
                      package_licence=str(doc["package_licence"]), repo=str(doc["repo"]),
                      tools=tuple(tools), config_files=dict(doc.get("config_files") or {}),
                      path=str(target))
    _ALLOWLISTS[key] = allow
    return allow


# --------------------------------------------------------------------- the installation
@dataclass(frozen=True)
class Installed:
    version: str
    data_dir: Path


def installed() -> Installed | None:
    """The installed package's version and configuration directory, without importing it.

    Importing ToolUniverse takes seconds and registers every tool class; reading what it
    ships needs neither.
    """
    try:
        spec = importlib.util.find_spec(PACKAGE)
        version = importlib.metadata.version(PACKAGE)
    except (ImportError, ValueError, importlib.metadata.PackageNotFoundError):
        return None
    if spec is None or not spec.submodule_search_locations:
        return None
    return Installed(version, Path(next(iter(spec.submodule_search_locations))) / "data")


def entry_digest(entry: Mapping[str, Any]) -> str:
    """SHA-256 of a configuration entry as canonical JSON: what the allowlist pins."""
    blob = json.dumps(entry, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def config_entry(data_dir: Path, config_file: str, name: str
                 ) -> tuple[dict[str, Any], str, str]:
    """``(entry, sha256 of the file, sha256 of the entry)`` for tool ``name``.

    Raises ``LookupError`` when the file or the tool is not there.
    """
    path = Path(data_dir) / config_file
    if Path(config_file).name != config_file or not path.is_file():
        raise LookupError(f"no configuration file {config_file!r} in {data_dir}")
    raw = path.read_bytes()
    data = json.loads(raw)
    entries = [e for e in (data if isinstance(data, list) else [data])
               if isinstance(e, dict) and e.get("name") == name]
    if len(entries) != 1:
        raise LookupError(f"{config_file} holds {len(entries)} entries named {name!r}")
    return entries[0], hashlib.sha256(raw).hexdigest(), entry_digest(entries[0])


def _urls(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        if value.startswith(("http://", "https://")):
            yield value
    elif isinstance(value, Mapping):
        for v in value.values():
            yield from _urls(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from _urls(v)


def _config_hosts(entry: Mapping[str, Any]) -> set[str]:
    """Hosts the configuration itself names as endpoints (not in prose or examples)."""
    skip = {"description", "parameter", "return_schema", "test_examples"}
    return {urllib.parse.urlsplit(u).hostname or "" for k, v in entry.items()
            if k not in skip for u in _urls(v)} - {""}


def _drift(tool: ReviewedTool, allow: Allowlist, inst: Installed
           ) -> tuple[str, dict[str, Any] | None]:
    """Why the installed tool is not the reviewed one ("" when it is), and its entry."""
    if inst.version != allow.reviewed_version:
        return (f"{PACKAGE} {inst.version} is installed; the allowlist was reviewed against "
                f"{allow.reviewed_version}. Re-review before use."), None
    try:
        entry, _, digest = config_entry(inst.data_dir, tool.config_file, tool.name)
    except (LookupError, ValueError) as exc:
        return f"the reviewed configuration is gone: {exc}", None
    if digest != tool.entry_sha256:
        return (f"the configuration of {tool.name} in {tool.config_file} has sha256 "
                f"{digest[:16]}…, not the reviewed {tool.entry_sha256[:16]}…"), None
    if str(entry.get("type")) != tool.implementation.rsplit(".", 1)[-1]:
        return (f"{tool.name} is now implemented by {entry.get('type')!r}, not the "
                f"reviewed {tool.implementation}"), None
    undeclared = sorted(h for h in _config_hosts(entry)
                        if not any(h == d or h.endswith("." + d) for d in tool.hosts))
    if undeclared:
        return (f"the configuration contacts {undeclared}, which the review did not "
                "declare"), None
    if entry.get("required_api_keys"):
        return (f"{tool.name} requires API keys {entry['required_api_keys']}; only tools "
                "that run without credentials are admitted"), None
    return "", entry


# ------------------------------------------------------------------------- discovery
class ToolUniverseProvider(ProviderBase):
    """Yields one ``python`` component per allowlisted ToolUniverse tool, and no other."""

    name = "tooluniverse"

    def __init__(self, allowlist: Allowlist | str | Path | None = None,
                 install: Installed | None | Callable[[], Installed | None] = installed
                 ) -> None:
        self.allowlist = (allowlist if isinstance(allowlist, Allowlist)
                          else load_allowlist(allowlist))
        self._install = install

    def _installed(self) -> Installed | None:
        return self._install() if callable(self._install) else self._install

    def available(self) -> bool:
        return self._installed() is not None

    def discover(self) -> Iterator[ComponentManifest]:
        inst = self._installed()
        for tool in self.allowlist.tools:
            yield self.manifest(tool, inst)

    def manifest(self, tool: ReviewedTool, inst: Installed | None) -> ComponentManifest:
        allow = self.allowlist
        m = ComponentManifest(
            id=tool.component_id, kind="tool", name=tool.name,
            version=inst.version if inst else allow.reviewed_version,
            description=tool.purpose, domain=tool.domain,
            provider=Provider(project=PACKAGE, repo=allow.repo,
                              commit=f"sha256:{tool.entry_sha256}",
                              source_path=f"{PACKAGE}/data/{tool.config_file}"),
            runtime=RuntimeSpec(backend="python", entrypoint=(
                f"{_MODULE}:{ENTRY_PREFIX}{tool.name}"), deterministic=False,
                timeout_s=tool.timeout_s),
            requires=Requirements(python=(PACKAGE,)),
            permissions=Permissions(network=tool.hosts),
            license=LicenseSpec(
                spdx=allow.package_licence, integration_mode="federated",
                note=(f"{allow.package_licence} is the licence of the {PACKAGE} code, which "
                      "runs in place and is never copied. The configuration states no "
                      "licence for the records the tool returns"
                      + (f"; the source's terms: {tool.data_terms}" if tool.data_terms
                         else "")),
                data="unknown"),
            validation=Validation(
                smoke_test=f"{PACKAGE}:{tool.name}:test_examples[{tool.smoke}]",
                last_validated=str(tool.live_check.get("on") or "")),
            offline_capable=False, keywords=(PACKAGE, tool.domain))
        if inst is None:
            return m                 # the resolver reports the missing package
        reason, entry = _drift(tool, allow, inst)
        if entry is None:
            m.state = LifecycleState.QUARANTINED
            m.blocking_reason = reason
            return m
        stated = entry.get("license") if isinstance(entry.get("license"), str) else ""
        parameter = dict(entry.get("parameter") or {})
        m.description = _CONTROL.sub("", str(entry.get("description") or tool.purpose))[:400]
        m.inputs = {**parameter, "examples": list(entry.get("test_examples") or ())}
        m.outputs = dict(entry.get("return_schema") or {})
        m.signature = ", ".join(parameter.get("properties") or ())
        m.license.data = stated or "unknown"
        return m


def smoke_arguments(manifest: ComponentManifest) -> dict[str, Any]:
    """The arguments of a component's smoke test: the test example its review names."""
    match = re.fullmatch(rf"{PACKAGE}:.+:test_examples\[(\d+)\]",
                         manifest.validation.smoke_test)
    examples = manifest.inputs.get("examples") or []
    if match is None or int(match.group(1)) >= len(examples):
        raise LookupError(f"{manifest.id} has no smoke example to run")
    return dict(examples[int(match.group(1))])


# ------------------------------------------------------------------------- execution
def _is_error(result: Any) -> bool:
    """ToolUniverse 1.5.6's own rule (``ToolUniverse._is_error_result``) for a failure
    reported as a value, restated here so the classification does not depend on a private
    method that a release may rename."""
    if isinstance(result, dict):
        status = result.get("status")
        return status == "error" or (status is None and bool(result.get("error")))
    return (isinstance(result, list) and len(result) > 0 and isinstance(result[0], dict)
            and "error" in result[0] and "term" not in result[0])


def classify(result: Any) -> tuple[ExecutionStatus, str]:
    """The status a ToolUniverse result ends in, and a bounded reason when it failed.

    Typed errors carry their ToolUniverse class; a tool's own error dict often carries only
    text, and is read the way ``HTTPBackend`` reads a failed request: a timeout is TIMEOUT,
    a host that could not be reached is UNAVAILABLE, anything else is FAILED. Nothing that
    reports an error is ever SUCCEEDED.
    """
    if result is None:
        return ExecutionStatus.FAILED, "the tool returned nothing"
    if not _is_error(result):
        return ExecutionStatus.SUCCEEDED, ""
    first = result[0] if isinstance(result, list) else result
    details = first.get("error_details") if isinstance(first.get("error_details"),
                                                       Mapping) else {}
    kind = str(details.get("type") or "")
    text = " ".join(str(first.get(k) or "") for k in ("error", "detail")).strip()
    text = _CONTROL.sub(" ", text)[:300] or "the tool reported an error and no message"
    if kind in _UNAVAILABLE_TYPES:
        return ExecutionStatus.UNAVAILABLE, f"{kind}: {text}"
    if kind:
        return ExecutionStatus.FAILED, f"{kind}: {text}"
    lowered = text.lower()
    if "timed out" in lowered or "timeout" in lowered:
        return ExecutionStatus.TIMEOUT, text
    if any(k in lowered for k in _UNREACHABLE):
        return ExecutionStatus.UNAVAILABLE, f"the source could not be reached: {text}"
    return ExecutionStatus.FAILED, text


_LOCK = threading.RLock()
#: One engine per tool, each holding only that tool.
_ENGINES: dict[str, Any] = {}


@contextlib.contextmanager
def _without_result_cache() -> Iterator[None]:
    """ToolUniverse reads these two variables once, when an engine is built."""
    names = ("TOOLUNIVERSE_CACHE_ENABLED", "TOOLUNIVERSE_CACHE_PERSIST")
    saved = {n: os.environ.get(n) for n in names}
    os.environ.update({n: "false" for n in names})
    try:
        yield
    finally:
        for n, v in saved.items():
            if v is None:
                os.environ.pop(n, None)
            else:
                os.environ[n] = v


def _logs_to_stderr() -> None:
    import logging
    for handler in logging.getLogger(PACKAGE).handlers:
        if isinstance(handler, logging.StreamHandler) and handler.stream in (
                sys.stdout, sys.__stdout__):
            handler.setStream(sys.stderr)


def _engine(tool: ReviewedTool, allow: Allowlist) -> Any:
    """The engine holding ``tool`` alone, built on first use and checked against review."""
    with _LOCK:
        if tool.name in _ENGINES:
            return _ENGINES[tool.name]
        inst = installed()
        if inst is None:
            raise ToolUniverseCallError(ExecutionStatus.UNAVAILABLE,
                                        f"{PACKAGE} is not installed")
        reason, entry = _drift(tool, allow, inst)
        if entry is None:
            raise ToolUniverseCallError(ExecutionStatus.DENIED, reason)
        try:
            engine = _build_engine(tool.name, inst)
            _check_loaded(engine, tool, entry, inst)
        except ToolUniverseCallError:
            raise
        except Exception as exc:  # noqa: BLE001 - the tool never ran, so not FAILED
            raise ToolUniverseCallError(ExecutionStatus.UNAVAILABLE, (
                f"the {PACKAGE} engine for {tool.name} could not be built: "
                f"{type(exc).__name__}: {str(exc)[:200]}")) from exc
        _ENGINES[tool.name] = engine
        return engine


def _build_engine(name: str, inst: Installed) -> Any:
    from tooluniverse import ToolUniverse

    _logs_to_stderr()
    with _without_result_cache():
        engine = ToolUniverse(load_workspace=False, log_level="WARNING")
    # load_workspace=False does not stop load_tools() from importing the Python files and
    # reading the JSON files under ./.tooluniverse; this engine scans nothing.
    if not callable(getattr(engine, "_get_user_tool_files", None)):
        raise ToolUniverseCallError(ExecutionStatus.DENIED, (
            f"{PACKAGE} {inst.version} has no _get_user_tool_files to disable; the "
            "workspace scan cannot be turned off"))
    engine._get_user_tool_files = lambda: ([], [])
    engine.load_tools(include_tools=[name])
    return engine


def _check_loaded(engine: Any, tool: ReviewedTool, entry: Mapping[str, Any],
                  inst: Installed) -> None:
    """Refuse an engine whose tool is not, byte for byte, the one reviewed."""
    loaded = dict(engine.all_tool_dict.get(tool.name) or {})
    others = sorted(set(engine.all_tool_dict) - {tool.name})
    problems = []
    if not loaded:
        problems.append("the tool did not load")
    if others:
        problems.append(f"the engine also loaded {others[:3]}")
    if loaded and (entry_digest({k: loaded.get(k) for k in entry}) != tool.entry_sha256
                   or set(loaded) - set(entry) - _LOADER_KEYS):
        problems.append("the loaded configuration differs from the reviewed one")
    source = loaded.get("source_file")
    if source and Path(source).resolve() != (inst.data_dir / tool.config_file).resolve():
        problems.append(f"it was loaded from {source}, not the package's {tool.config_file}")
    if not problems:
        instance = engine._get_tool_instance(tool.name, cache=True)
        ran = f"{type(instance).__module__}.{type(instance).__qualname__}"
        if ran != tool.implementation:
            problems.append(f"it would run {ran}, not the reviewed {tool.implementation}")
    if problems:
        raise ToolUniverseCallError(ExecutionStatus.DENIED, "; ".join(problems))


def call(name: str, arguments: Mapping[str, Any] | None = None, *,
         allowlist: Allowlist | None = None) -> Any:
    """Run allowlisted tool ``name``; its result, or :class:`ToolUniverseCallError`."""
    allow = allowlist or load_allowlist()
    tool = allow.get(name)
    if tool is None:
        raise ToolUniverseCallError(ExecutionStatus.DENIED, (
            f"{name!r} is not in the reviewed {PACKAGE} allowlist ({allow.path})"))
    engine = _engine(tool, allow)
    result = engine.run_one_function({"name": name, "arguments": dict(arguments or {})},
                                     use_cache=False, validate=True)
    status, reason = classify(result)
    if status is not ExecutionStatus.SUCCEEDED:
        raise ToolUniverseCallError(status, reason)
    return result


_ENTRYPOINTS: dict[str, Callable[..., Any]] = {}


def __getattr__(attribute: str) -> Callable[..., Any]:
    """``run_<name>`` for an allowlisted tool: the entrypoint its manifest names.

    Resolved on access (PEP 562) so the allowlist is read when a component is loaded, not
    when this module is imported, and a name outside it does not exist to be loaded.
    """
    name = attribute[len(ENTRY_PREFIX):] if attribute.startswith(ENTRY_PREFIX) else ""
    if not name or load_allowlist().get(name) is None:
        raise AttributeError(f"module {__name__!r} has no attribute {attribute!r}")
    if name not in _ENTRYPOINTS:
        def entrypoint(**arguments: Any) -> Any:
            return call(name, arguments)

        entrypoint.__name__ = entrypoint.__qualname__ = attribute
        entrypoint.__doc__ = f"Run the reviewed {PACKAGE} tool {name}."
        _ENTRYPOINTS[name] = entrypoint
    return _ENTRYPOINTS[name]


# ---------------------------------------------------------------------------- review
def review(allowlist: Allowlist | str | Path | None = None) -> list[dict[str, Any]]:
    """What the installed package holds for each allowlisted tool, for a reviewer.

    Re-reviewing after an upgrade means reading these entries, then writing the new
    digests into the allowlist; nothing here edits it.
    """
    allow = allowlist if isinstance(allowlist, Allowlist) else load_allowlist(allowlist)
    inst = installed()
    rows = []
    for tool in allow.tools:
        row: dict[str, Any] = {"name": tool.name, "installed": inst.version if inst else None,
                               "reviewed": allow.reviewed_version}
        if inst is not None:
            try:
                entry, file_digest, digest = config_entry(inst.data_dir, tool.config_file,
                                                          tool.name)
                reason, _ = _drift(tool, allow, inst)
                row.update(config_file=tool.config_file, file_sha256=file_digest,
                           entry_sha256=digest, type=entry.get("type"),
                           hosts_in_config=sorted(_config_hosts(entry)),
                           required_api_keys=entry.get("required_api_keys") or [],
                           test_examples=len(entry.get("test_examples") or ()),
                           drift=reason)
            except (LookupError, ValueError) as exc:
                row["drift"] = str(exc)
        rows.append(row)
    return rows


if __name__ == "__main__":                                    # pragma: no cover
    print(json.dumps(review(sys.argv[1] if len(sys.argv) > 1 else None), indent=1))
