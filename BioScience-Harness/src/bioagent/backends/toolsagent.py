"""SciToolAgent's ToolsAgent service as components: one fixed function each, typed arguments.

ToolsAgent (``ToolsAgent/`` in github.com/HICAI-ZJU/SciToolAgent, MIT) serves 500+ tools
behind one FastAPI endpoint. As ``main.py`` and ``tool_runner.py`` read on 2026-10-07:

    POST /run-func?func_name=<name>&func_args=<one string>
    {"file_path_list": ["/path/on/the/service/host/a.pdb", ...]}      required; may be []
    200 {"status_code": 200, "result": <whatever the function returned>}
    400 {"detail": "..."}   ValueError or ImportError, an unknown tool module among them
    422                     a request of the wrong shape
    500                     any other exception; an unknown func_name is a KeyError

The README shows the three fields in one JSON body; the code reads ``func_name`` and
``func_args`` as query parameters, and the code is what answers.

Three properties of the service decide this adapter:

* **One endpoint runs any function.** Whoever chooses ``func_name`` can run any of the
  500 tools, including ones that send data from the service's host to a third party. So
  each function is its own component with ``func_name`` fixed, the hosts its data reaches
  declared, and the policy kernel rules on that component rather than on "ToolsAgent".
* **Every function takes one string.** A multi-argument tool splits it on a separator of
  its own choosing. ``MolSimilarity`` splits on ``.``, the SMILES disconnection operator,
  so a salt's SMILES becomes three molecules. Arguments are structured here and rendered
  by each tool's declared rule; a value containing the separator is refused unsent.
* **Errors come back as answers.** Tools catch their own exceptions and return ``None``,
  "Invalid SMILES string" or a Markdown "### Error" with HTTP 200. A 200 is not a result
  until the answer matches the tool's declared result shape (``accept``); anything else
  is FAILED, with the text the tool returned.

Files are references on the *service's* filesystem and are never uploaded. The service
resolves every name against the directory of the *last* path and keeps only the files
with the last path's extension, so a list mixing directories or extensions is refused
instead of being silently cut short. A file a tool writes is a ``ServerFile``: digested
and validated only where ``shared_dirs`` says this machine can read it, DEGRADED (made,
not verified) otherwise.

ToolsAgent has no job API: ``run-func`` holds the connection until the function returns.
``ToolsAgentJobs`` runs a long call through the job protocol (``backends.jobs``) so the
caller is not blocked and the call is recorded, and it says plainly what it cannot do:
there is nothing to cancel on the service, and a call from a process that has ended
cannot be recovered.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
import urllib.parse
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterator, Mapping, Sequence

from ..adapters.base import CallResult
from ..runtime.component import (ComponentManifest, LicenseSpec, Permissions, Provider,
                                 RuntimeSpec)
from ..status import ExecutionStatus
from .jobs import (ArtefactSpec, JobExecutor, JobFetchError, JobRef, JobSpec, JobState,
                   JobStatus, JobSubmitError)

__all__ = ["ToolsAgentTool", "ServerFile", "ToolsAgentRequestError", "ToolsAgentClient",
           "ToolsAgentJobs", "interpret", "TOOLS", "SMILES_TO_WEIGHT", "MOL_SIMILARITY",
           "DOUBLE_SEQUENCE_GLOBAL_ALIGNMENT", "RUN_FUNC"]

RUN_FUNC = "run-func"
_OUTPUTS = ("text", "number", "server_file")


class ToolsAgentRequestError(ValueError):
    """The arguments cannot be expressed as this tool's request; nothing was sent."""


@dataclass(frozen=True)
class ServerFile:
    """A path on the ToolsAgent host: absolute, or relative to the service's directory."""

    path: str

    def __post_init__(self) -> None:
        if not self.path or "\x00" in self.path or "\n" in self.path:
            raise ToolsAgentRequestError(f"{self.path!r} is not a usable path")


@dataclass(frozen=True)
class ToolsAgentTool:
    """One ToolsAgent function, with how to render its arguments and recognise its answer.

    ``accept`` is a regular expression a successful answer contains; for ``number`` and
    ``server_file`` outputs its first group is the value. It is required: a tool whose
    successful answer cannot be told from its error text cannot report SUCCEEDED.
    """

    func_name: str
    arguments: tuple[str, ...]
    accept: str
    separator: str = ""
    output: str = "text"
    takes_files: bool = False
    description: str = ""
    #: what the answer is, scientifically: the quantity, the method, the caveats
    note: str = ""
    #: hosts the service contacts on this tool's behalf; the data reaches them too
    onward_hosts: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", self.func_name):
            raise ValueError(f"func_name {self.func_name!r} is not a function name")
        if not self.arguments:
            raise ValueError(f"{self.func_name}: a tool takes at least one argument")
        if len(self.arguments) > 1 and not self.separator:
            raise ValueError(f"{self.func_name}: several arguments need the separator the "
                             "tool splits its string on")
        if self.output not in _OUTPUTS:
            raise ValueError(f"{self.func_name}: output is one of {_OUTPUTS}")
        pattern = re.compile(self.accept)
        if self.output != "text" and pattern.groups < 1:
            raise ValueError(f"{self.func_name}: accept needs a group for the "
                             f"{self.output} it returns")

    @property
    def component_id(self) -> str:
        """``SMILESToWeight`` -> ``scitoolagent.tool.smiles_to_weight``."""
        words = re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", "_",
                       self.func_name)
        return "scitoolagent.tool." + words.lower()

    def manifest(self, server: str) -> ComponentManifest:
        """This function as an ``http`` component of the service at ``server``."""
        host = urllib.parse.urlsplit(server).hostname or ""
        text = self.description + (f". {self.note}" if self.note else "")
        return ComponentManifest(
            id=self.component_id, kind="tool", name=self.func_name, version="0.1.0",
            description=text[:400],
            provider=Provider(project="SciToolAgent", repo="HICAI-ZJU/SciToolAgent",
                              source_path="ToolsAgent/main.py"),
            runtime=RuntimeSpec(backend="http", server=server.rstrip("/"),
                                entrypoint=f"{RUN_FUNC}:{self.func_name}"),
            inputs={"protocol": "scitoolagent.run-func", "arguments": list(self.arguments),
                    "separator": self.separator, "takes_files": self.takes_files},
            outputs={"type": self.output, "accept": self.accept},
            permissions=Permissions(network=tuple(dict.fromkeys((host, *self.onward_hosts)))),
            license=LicenseSpec(spdx="MIT", integration_mode="federated",
                                note=("SciToolAgent's code is MIT; the tool it wraps keeps "
                                      "its own licence and terms of use")),
            keywords=("SciToolAgent", "ToolsAgent", self.func_name))

    def render(self, arguments: Mapping[str, Any],
               files: Sequence[ServerFile] = ()) -> dict[str, Any]:
        """Keyword arguments for ``HTTPBackend.invoke``; raises before anything is sent."""
        missing = [a for a in self.arguments if a not in arguments]
        unknown = sorted(set(arguments) - set(self.arguments))
        if missing or unknown:
            raise ToolsAgentRequestError(
                f"{self.func_name} takes {list(self.arguments)}"
                + (f"; missing {missing}" if missing else "")
                + (f"; unknown {unknown}" if unknown else ""))
        values = []
        for name in self.arguments:
            value = str(arguments[name])
            if not value.strip():
                raise ToolsAgentRequestError(f"{self.func_name}: {name} is empty")
            if self.separator and self.separator in value:
                raise ToolsAgentRequestError(
                    f"{self.func_name}: {name} contains {self.separator!r}, the separator the "
                    "tool splits its argument on, so the service would read it as more than "
                    "one value")
            values.append(value)
        return {"path": RUN_FUNC, "method": "POST",
                "params": {"func_name": self.func_name,
                           "func_args": self.separator.join(values)},
                "json_body": {"file_path_list": self._files(files)},
                "accept": "application/json", "use_cache": False}

    def _files(self, files: Sequence[ServerFile]) -> list[str]:
        paths = [f.path for f in files]
        if not self.takes_files:
            if paths:
                raise ToolsAgentRequestError(f"{self.func_name} reads no files")
            return []
        if not paths:
            raise ToolsAgentRequestError(f"{self.func_name} reads files; name at least one")
        last = PurePosixPath(paths[-1])
        for raw in paths:
            p = PurePosixPath(raw)
            if not p.is_absolute():
                raise ToolsAgentRequestError(f"{raw!r} is not an absolute path on the "
                                             "service's host")
            if p.parent != last.parent or p.suffix != last.suffix:
                # main.set_upload_file_info keeps the last file's directory and extension
                # and drops every name that does not share the extension.
                raise ToolsAgentRequestError(
                    f"{raw!r}: the service reads every file from the last file's directory "
                    f"({last.parent}) and keeps only its extension ({last.suffix or 'none'}); "
                    "this one would be dropped or misread")
        return paths


def _local(file: ServerFile, shared_dirs: Mapping[str, Path]) -> Path | None:
    """Where this machine can read a file on the service's host, if anywhere.

    ``shared_dirs`` maps a directory on the service's host to the same directory as seen
    here (a shared mount); the key ``"."`` is the service's working directory, against
    which ToolsAgent tools return bare file names.
    """
    path = PurePosixPath(file.path)
    if ".." in path.parts:
        return None
    for server_dir, local_dir in sorted(shared_dirs.items(), key=lambda kv: -len(kv[0])):
        if server_dir == ".":
            if path.is_absolute():
                continue
            candidate = Path(local_dir) / Path(*path.parts)
        else:
            base = PurePosixPath(server_dir)
            if not path.is_absolute() or (path != base and base not in path.parents):
                continue
            candidate = Path(local_dir) / Path(*path.relative_to(base).parts)
        if candidate.is_file():
            return candidate
    return None


def interpret(tool: ToolsAgentTool, res: CallResult, *,
              shared_dirs: Mapping[str, Path] | None = None) -> CallResult:
    """A transport result as this tool's result, with the ToolsAgent status mapping."""
    meta = {**dict(res.metadata or {}), "func_name": tool.func_name}
    code = meta.get("http_status")

    def out(status: ExecutionStatus, *, value: Any = None, error: str | None = None
            ) -> CallResult:
        return CallResult(capability=tool.component_id, adapter="toolsagent", status=status,
                          value=value, error=error, duration_s=res.duration_s,
                          metadata=meta, authorization=res.authorization)

    if res.status is not ExecutionStatus.SUCCEEDED:
        err = res.error or res.status.value
        if code == 400 and "not found" in err.lower():
            return out(ExecutionStatus.UNAVAILABLE,
                       error=f"the service has no {tool.func_name}: {err}"[:600])
        if code == 422:
            return out(ExecutionStatus.FAILED,
                       error=f"the service refused the request's shape (HTTP 422); the tool "
                             f"did not run: {err}"[:600])
        if code == 500:
            return out(ExecutionStatus.FAILED,
                       error=f"{tool.func_name} raised on the service (HTTP 500; an unknown "
                             f"func_name also answers 500): {err}"[:600])
        return out(res.status, error=f"{tool.func_name}: {err}"[:600])
    body = res.value
    if not isinstance(body, Mapping) or "result" not in body:
        return out(ExecutionStatus.FAILED, error=f"{tool.func_name}: the answer has no "
                                                 f"'result': {str(body)[:200]!r}")
    if body.get("status_code", 200) != 200:
        return out(ExecutionStatus.FAILED, error=f"{tool.func_name}: the answer reports "
                                                 f"status_code {body.get('status_code')}")
    result = body["result"]
    if result is None:
        return out(ExecutionStatus.FAILED,
                   error=(f"{tool.func_name} returned no result: ToolsAgent tools return "
                          "None when they catch their own exception"))
    text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False,
                                                             default=str)
    match = re.search(tool.accept, text)
    if match is None:
        return out(ExecutionStatus.FAILED,
                   error=(f"{tool.func_name} answered with text that is not its result: "
                          f"{text.strip()[:240]!r}"))
    value: dict[str, Any] = {"func_name": tool.func_name, "text": text}
    if tool.output == "number":
        try:
            value["value"] = float(match.group(1))
        except ValueError:
            return out(ExecutionStatus.FAILED,
                       error=f"{tool.func_name}: {match.group(1)!r} is not a number")
        if tool.note:
            value["note"] = tool.note
        return out(ExecutionStatus.SUCCEEDED, value=value)
    if tool.output == "server_file":
        produced = ServerFile(match.group(1).strip())
        value["server_file"] = produced.path
        local = _local(produced, shared_dirs or {})
        if local is None:
            return out(ExecutionStatus.DEGRADED, value=value,
                       error=(f"{tool.func_name} wrote {produced.path} on the service's host; "
                              "no shared directory makes it readable here, so it was neither "
                              "digested nor validated"))
        data = local.read_bytes()
        if not data:
            return out(ExecutionStatus.FAILED, value=value,
                       error=f"{tool.func_name} wrote an empty file ({produced.path})")
        value.update(local_path=str(local), sha256=hashlib.sha256(data).hexdigest(),
                     size=len(data))
    return out(ExecutionStatus.SUCCEEDED, value=value)


class ToolsAgentClient:
    """Calls one ToolsAgent service, through ``HTTPBackend`` or a governed ``Runtime``.

    With ``runtime`` (and ``spec``), the call is ``runtime.invoke`` on the tool's
    component, so the policy kernel rules on it and the run's event log records it; the
    tool's manifest must be registered there. Without, the call goes straight to the
    ``HTTPBackend``, which still refuses a host the component does not declare. Either way
    one attempt is made: a 500 from ToolsAgent is the tool raising, and repeating it would
    run the tool again.
    """

    def __init__(self, server: str, *, http: Any = None,
                 shared_dirs: Mapping[str, str | Path] | None = None,
                 timeout_s: float = 120.0, runtime: Any = None, spec: Any = None,
                 events: Any = None) -> None:
        from .http import HTTPBackend

        if not server.startswith(("http://", "https://")):
            raise ValueError(f"a ToolsAgent service is an http(s) URL, not {server!r}")
        if runtime is not None and spec is None:
            raise ValueError("a governed call needs the AgentSpec it runs under")
        self.server = server.rstrip("/")
        self.http = http if http is not None else HTTPBackend(max_retries=1,
                                                              timeout_s=timeout_s)
        self.shared_dirs = {k: Path(v) for k, v in (shared_dirs or {}).items()}
        self.runtime, self.spec, self.events = runtime, spec, events
        self._guard = _FileCalls()

    def call(self, tool: ToolsAgentTool, *, files: Sequence[ServerFile] = (),
             **arguments: Any) -> CallResult:
        request = tool.render(arguments, files)
        with self._guard.hold(exclusive=bool(request["json_body"]["file_path_list"])):
            if self.runtime is not None:
                res = self.runtime.invoke(tool.component_id, spec=self.spec,
                                          events=self.events, **request)
            else:
                res = self.http.invoke(tool.manifest(self.server), **request)
        return interpret(tool, res, shared_dirs=self.shared_dirs)


class _FileCalls:
    """A call that sends files runs alone; calls without files may overlap each other.

    ToolsAgent's ``run-func`` writes each request's file list into a process-wide
    ``Config()`` singleton, which the tool reads while it runs; every request, with files
    or without, overwrites it. Two calls in flight at once can therefore hand a tool the
    other call's files, and the answer comes back with HTTP 200. This keeps this client's
    own calls apart. Calls from other clients of the same service can still collide.
    """

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._shared = 0
        self._exclusive = False

    @contextmanager
    def hold(self, *, exclusive: bool) -> Iterator[None]:
        with self._cond:
            if exclusive:
                self._cond.wait_for(lambda: not self._exclusive and self._shared == 0)
                self._exclusive = True
            else:
                self._cond.wait_for(lambda: not self._exclusive)
                self._shared += 1
        try:
            yield
        finally:
            with self._cond:
                if exclusive:
                    self._exclusive = False
                else:
                    self._shared -= 1
                self._cond.notify_all()


class ToolsAgentJobs(JobExecutor):
    """Long ToolsAgent calls through the job protocol.

    ``submit`` starts the call on a worker thread and returns at once; ``status`` reports
    it running until the service answers; ``collect`` validates ``result.json`` (and the
    tool's output file, which must be readable through ``shared_dirs``). Two limits are
    stated rather than hidden: ToolsAgent has no cancellation, so ``cancel`` stops nothing
    and says so; and it keeps no job record, so a call whose process has ended is LOST.
    """

    name = "toolsagent"

    def __init__(self, client: ToolsAgentClient, tools: Sequence[ToolsAgentTool]) -> None:
        self.client = client
        self.tools = {t.func_name: t for t in tools}
        self._calls: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    @staticmethod
    def spec(tool: ToolsAgentTool, *, files: Sequence[ServerFile] = (),
             **arguments: Any) -> JobSpec:
        """The job for one call; refuses unrenderable arguments before anything is sent."""
        tool.render(arguments, files)
        artefacts = [ArtefactSpec("result", "result.json")]
        if tool.output == "server_file":
            artefacts.append(ArtefactSpec("file", "file"))
        return JobSpec(component_id=tool.component_id,
                       payload={"func_name": tool.func_name,
                                "arguments": {k: str(v) for k, v in arguments.items()},
                                "files": [f.path for f in files]},
                       artefacts=tuple(artefacts))

    def submit(self, spec: JobSpec, *, submission_id: str) -> JobRef:
        tool = self.tools.get(str(spec.payload.get("func_name")))
        if tool is None:
            raise JobSubmitError(f"{spec.payload.get('func_name')!r} is not a tool this "
                                 "executor was given")
        files = [ServerFile(p) for p in spec.payload.get("files") or ()]
        arguments = dict(spec.payload.get("arguments") or {})
        ref = JobRef(job_id=submission_id, executor=self.name, location=self.client.server,
                     component_id=spec.component_id, spec_digest=spec.digest(),
                     submission_id=submission_id, submitted_at=time.time(),
                     artefacts=tuple(spec.artefacts))
        with self._lock:
            if submission_id in self._calls:
                return self._calls[submission_id]["ref"]
            call: dict[str, Any] = {"ref": ref, "result": None}

            def work() -> None:
                try:
                    call["result"] = self.client.call(tool, files=files, **arguments)
                except Exception as exc:  # noqa: BLE001 - reported as the job's failure
                    call["result"] = CallResult(
                        capability=tool.component_id, adapter="toolsagent",
                        status=ExecutionStatus.FAILED, error=f"{type(exc).__name__}: {exc}")

            call["thread"] = threading.Thread(target=work, daemon=True,
                                              name=f"toolsagent-{submission_id[:8]}")
            self._calls[submission_id] = call
            call["thread"].start()
        return ref

    def status(self, ref: JobRef) -> JobStatus:
        call = self._calls.get(ref.job_id)
        if call is None:
            return JobStatus(JobState.LOST,
                             "ToolsAgent keeps no job records, and this call ran in a process "
                             "that has ended: its outcome cannot be recovered")
        if call["thread"].is_alive():
            return JobStatus(JobState.RUNNING)
        res: CallResult = call["result"]
        if res.status in (ExecutionStatus.SUCCEEDED, ExecutionStatus.DEGRADED):
            return JobStatus(JobState.COMPLETED, res.error or "")
        return JobStatus(JobState.FAILED, res.error or res.status.value,
                         timed_out=res.status is ExecutionStatus.TIMEOUT)

    def fetch(self, ref: JobRef, artefact: ArtefactSpec, dest_dir: Path) -> Path | None:
        call = self._calls.get(ref.job_id)
        res: CallResult | None = call["result"] if call else None
        if res is None:
            return None
        value = res.value if isinstance(res.value, dict) else {}
        if artefact.name == "file":
            if not value.get("local_path"):
                raise JobFetchError(
                    f"{value.get('server_file', 'the output file')} is on the ToolsAgent host "
                    "and no shared directory makes it readable here")
            return Path(value["local_path"])
        dest_dir.mkdir(parents=True, exist_ok=True)
        path = dest_dir / "result.json"
        path.write_text(json.dumps({"status": res.status.value, "value": res.value,
                                    "error": res.error}, ensure_ascii=False, indent=2,
                                   default=str), encoding="utf-8")
        return path

    def cancel(self, ref: JobRef, *, reason: str) -> JobStatus:
        current = self.status(ref)
        if current.state is JobState.RUNNING:
            return JobStatus(JobState.RUNNING,
                             "ToolsAgent offers no cancellation: the call runs to completion "
                             "on the service, and nothing was stopped")
        return current


# --------------------------------------------------------------- reviewed functions
# Each was read in the ToolsAgent source on 2026-10-07: what it returns on success and on
# error, how it splits its argument, and what the number it returns is.

SMILES_TO_WEIGHT = ToolsAgentTool(
    func_name="SMILESToWeight", arguments=("smiles",), output="number",
    accept=r"\*\*Weight\*\*: `([0-9]+(?:\.[0-9]+)?) g/mol`",
    description="Mass of a molecule from its SMILES, computed by RDKit on the service",
    note=("RDKit CalcExactMolWt, the monoisotopic mass, which the service labels "
          "'Molecular Weight' in g/mol; it is not the average molecular weight"))

MOL_SIMILARITY = ToolsAgentTool(
    func_name="MolSimilarity", arguments=("smiles1", "smiles2"), separator=".",
    output="number", accept=r"\*\*Tanimoto Similarity\*\*: `([^`]+)`",
    description="Tanimoto similarity of two molecules from their SMILES",
    note=("Morgan fingerprints, radius 2, 2048 bits. The service answers identical "
          "molecules with an error, so a similarity of 1 is never returned; '.' separates "
          "the two SMILES, so neither may contain a disconnected component such as a salt"))

DOUBLE_SEQUENCE_GLOBAL_ALIGNMENT = ToolsAgentTool(
    func_name="DoubleSequenceGlobalAlignment", arguments=("sequence1", "sequence2"),
    separator=".", output="text", accept=r"\*\*\*Alignment of two sequences\*\*\*",
    description="Global (Needleman-Wunsch) alignment of two sequences",
    note=("computed by NovoPro's web service, to which the ToolsAgent host sends both "
          "sequences"),
    onward_hosts=("www.novopro.cn",))

TOOLS: tuple[ToolsAgentTool, ...] = (SMILES_TO_WEIGHT, MOL_SIMILARITY,
                                     DOUBLE_SEQUENCE_GLOBAL_ALIGNMENT)
