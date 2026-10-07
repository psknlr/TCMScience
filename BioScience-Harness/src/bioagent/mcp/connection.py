"""A synchronous connection to one reviewed MCP server, over the official SDK.

The SDK is asynchronous and the runtime that calls it is not, and the two obvious bridges
are both wrong. ``asyncio.run`` per call starts and stops a server for every call, and
fails outright inside a running loop. Driving a loop by hand between calls leaves the SDK's
context managers half-entered, which is how a server process comes to outlive its session.
So each connection owns one event-loop thread; the session lives in one task on it, inside
the SDK's own ``async with`` blocks; and every operation is submitted with
``run_coroutine_threadsafe(...).result(timeout)``, so the caller waits for the SDK to
finish, or for the deadline, and never for less.

Opening a connection is when trust is checked. ``tools/list`` is compared with the
reviewed entry: a tool the entry does not allow is never exposed or callable, and an
allowed tool that is missing, offered twice, or whose digests differ from the reviewed
snapshot is refused with the difference named. A server that announces its tool list has
changed is listed and checked again before the next call is sent.

The connection notices when its server goes away. The server's messages pass through a
forwarder that marks the connection ended when the server closes its end: a call in flight
fails as the SDK reports it, the next call is refused at once, and the dispatcher opens a
fresh connection rather than reusing a dead one.

Closing runs the SDK's shutdown in the session's own task: the server's stdin is closed,
it is given time to exit, and its process group is terminated if it does not. Any
connection still open at interpreter exit is closed then, so a caller that forgets does
not leave a server process behind.
"""

from __future__ import annotations

import asyncio
import atexit
import concurrent.futures
import os
import tempfile
import threading
import weakref
from typing import Any, Mapping, NamedTuple

from ..backends.concrete import MCPCallError
from ..status import ExecutionStatus
from .config import MCPServerConfig, ToolSnapshot

__all__ = ["MCPConnection", "PROXY_VARIABLES"]

#: Variables a stdio server inherits from the process that starts it. The SDK starts a
#: server with a minimal environment (HOME, PATH, ...) that drops them; in PSH's isolated
#: child they are the kernel's egress proxy, so dropping them would let a proxy-honouring
#: server reach hosts its component never declared.
PROXY_VARIABLES = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
                   "http_proxy", "https_proxy", "all_proxy", "no_proxy")

#: Characters of a server's stderr quoted in a reason.
_STDERR_TAIL_CHARS = 400
#: How much longer than an SDK-level deadline the calling thread waits for the loop
#: thread to report it, before deciding the loop itself is stuck.
_GRACE_S = 5.0
#: A server that keeps returning a next cursor is not listing tools.
_MAX_LIST_PAGES = 64

_LIVE: "weakref.WeakSet[MCPConnection]" = weakref.WeakSet()

_UNAVAILABLE = ExecutionStatus.UNAVAILABLE
_DENIED = ExecutionStatus.DENIED


class _Refusal(NamedTuple):
    status: ExecutionStatus
    reason: str


def _root_cause(exc: BaseException) -> BaseException:
    """The first concrete exception inside anyio's exception groups."""
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        inner = [e for e in exc.exceptions if not isinstance(e, asyncio.CancelledError)]
        exc = (inner or list(exc.exceptions))[0]
    return exc


async def _drain() -> None:
    """Cancel whatever is still pending on a closing loop: an abandoned call, a reader."""
    current = asyncio.current_task()
    pending = [task for task in asyncio.all_tasks() if task is not current]
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
    await asyncio.get_running_loop().shutdown_asyncgens()


class MCPConnection:
    """One session with one reviewed server, usable from synchronous code.

    ``env`` holds the resolved values of the entry's credential references (the server
    variable each is delivered as); the dispatcher resolves them, this class never looks a
    credential up. ``review=True`` opens a connection that lists tools without checking
    them and calls nothing: it is how a snapshot is taken, never how a server is used.
    """

    def __init__(self, config: MCPServerConfig, *, env: Mapping[str, str] | None = None,
                 review: bool = False, shutdown_timeout_s: float = 10.0) -> None:
        self.config = config
        self.review = review
        self.shutdown_timeout_s = shutdown_timeout_s
        self._env = dict(env or {})
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._task: asyncio.Task[None] | None = None
        self._closing: asyncio.Event | None = None
        self._recheck_lock: asyncio.Lock | None = None
        self._ended = threading.Event()
        self._gone = threading.Event()
        self._end_reason = ""
        self._session: Any = None
        self._used = False
        self._stale = False
        self._stderr: Any = None
        self._stderr_path = ""
        self.server_info: dict[str, str] = {}
        self.protocol_version = ""
        #: Everything ``tools/list`` returned, kept only by a reviewing connection.
        self.offered: dict[str, list[dict[str, Any]]] = {}
        #: Offered tools that are not on the allowlist: named here, never exposed.
        self.hidden: tuple[str, ...] = ()
        self._admitted: dict[str, dict[str, Any]] = {}
        self._refused: dict[str, _Refusal] = {}

    # ---------------------------------------------------------------- state
    @property
    def alive(self) -> bool:
        return (self._session is not None and not self._ended.is_set()
                and not self._gone.is_set())

    @property
    def tools(self) -> tuple[str, ...]:
        """The allowlisted tools that matched their reviewed snapshot: the callable ones."""
        return tuple(sorted(self._admitted))

    @property
    def refused(self) -> dict[str, str]:
        """Allowlisted tools refused at the last check, each with the reason."""
        return {name: refusal.reason for name, refusal in self._refused.items()}

    def descriptors(self) -> list[dict[str, Any]]:
        """The admitted tools as ``tools/list`` sent them — never a hidden or refused one."""
        return [dict(self._admitted[name]) for name in sorted(self._admitted)]

    def provenance(self, tool: str) -> dict[str, Any]:
        """What a result from ``tool`` records about the server that produced it."""
        snapshot = self.config.tools.get(tool)
        return {**self._meta(tool), "schema_digest": snapshot.input_schema if snapshot else "",
                "server_package": self.config.package, "server_version": self.config.version,
                "server_reported": dict(self.server_info),
                "protocol_version": self.protocol_version}

    def _meta(self, tool: str = "") -> dict[str, Any]:
        out = {"server": self.config.id, "transport": self.config.transport,
               "config_digest": self.config.digest}
        if tool:
            out["tool"] = tool
        return out

    def stderr_tail(self) -> str:
        """The end of what a stdio server wrote on stderr, on one line; empty otherwise."""
        if not self._stderr_path:
            return ""
        try:
            with open(self._stderr_path, "rb") as fh:
                fh.seek(0, os.SEEK_END)
                fh.seek(max(0, fh.tell() - 4 * _STDERR_TAIL_CHARS))
                data = fh.read()
        except OSError:
            return ""
        return " ".join(data.decode("utf-8", "replace").split())[-_STDERR_TAIL_CHARS:]

    # ----------------------------------------------------------------- open
    def open(self) -> "MCPConnection":
        """Start (or reach) the server, initialise, and check its tools against the entry.

        Idempotent while the session lives. A connection is used once: when it has closed
        or its server has gone, open a new one. Every failure here is UNAVAILABLE — no tool
        was called — and quotes the server's stderr when it wrote any.
        """
        with self._lock:
            if self.alive:
                return self
            if self._used:
                raise MCPCallError(self._closed_reason(), status=_UNAVAILABLE,
                                   metadata=self._meta())
            self._used = True
            try:
                from mcp import ClientSession  # noqa: F401 - the SDK, where it is needed
            except ImportError as exc:
                raise MCPCallError(
                    f"the MCP Python SDK is not importable here ({exc}); install the 'mcp' "
                    "extra (pip install 'bioagent[mcp]')", status=_UNAVAILABLE,
                    metadata=self._meta()) from None
            self._start()
        return self

    def _start(self) -> None:
        if self.config.transport == "stdio":
            # A file, not a pipe: nothing has to drain it, and its end is what a reason
            # quotes. The server's stderr must not reach this process's stderr, which an
            # isolated child reserves for the one line the kernel records.
            handle = tempfile.NamedTemporaryFile(prefix=f"mcp-{self.config.id}-",
                                                 suffix=".stderr", delete=False)
            self._stderr, self._stderr_path = handle, handle.name
        loop = asyncio.new_event_loop()
        thread = threading.Thread(target=loop.run_forever, name=f"mcp-{self.config.id}",
                                  daemon=True)
        thread.start()
        self._loop, self._thread = loop, thread
        _LIVE.add(self)
        ready: concurrent.futures.Future[None] = concurrent.futures.Future()
        asyncio.run_coroutine_threadsafe(self._run(ready), loop)
        try:
            ready.result(timeout=self.config.connect_timeout_s + _GRACE_S)
        except Exception as exc:  # noqa: BLE001 - every failure to connect is reported
            reason = self._connect_failure(exc)
            self._shutdown()
            raise MCPCallError(reason, status=_UNAVAILABLE, metadata=self._meta()) from None
        except BaseException:
            self._shutdown()
            raise

    def _connect_failure(self, exc: BaseException) -> str:
        from mcp.types import CONNECTION_CLOSED

        cause = _root_cause(exc)
        if isinstance(cause, TimeoutError):
            why = f"it did not finish initialising within {self.config.connect_timeout_s:g}s"
        elif isinstance(cause, OSError):
            why = f"it could not be started: {cause}"
        elif getattr(getattr(cause, "error", None), "code", None) == CONNECTION_CLOSED:
            why = "it closed the connection before initialising (the process exited?)"
        else:
            why = f"{type(cause).__name__}: {str(cause)[:300]}"
        tail = self.stderr_tail()
        return (f"cannot connect to MCP server {self.config.id!r}: {why}"
                + (f"; its stderr ends: {tail}" if tail else ""))

    async def _run(self, ready: "concurrent.futures.Future[None]") -> None:
        """The session's whole life, in one task, inside the SDK's context managers."""
        import anyio
        from mcp import ClientSession

        self._task = asyncio.current_task()
        self._closing = asyncio.Event()
        self._recheck_lock = asyncio.Lock()
        try:
            async with self._transport() as streams:
                feed, session_reads = anyio.create_memory_object_stream(0)
                async with anyio.create_task_group() as forwarding:
                    forwarding.start_soon(self._forward, streams[0], feed)
                    async with ClientSession(session_reads, streams[1],
                                             message_handler=self._on_message) as session:
                        with anyio.fail_after(self.config.connect_timeout_s):
                            init = await session.initialize()
                            await self._check_tools(session)
                        self.server_info = {"name": str(init.serverInfo.name),
                                            "version": str(init.serverInfo.version)}
                        self.protocol_version = str(init.protocolVersion)
                        self._session = session
                        ready.set_result(None)
                        await self._closing.wait()
                    forwarding.cancel_scope.cancel()
        except BaseException as exc:
            if not ready.done():
                ready.set_exception(exc if isinstance(exc, Exception) else RuntimeError(
                    f"the connection was cancelled ({type(exc).__name__})"))
            elif not self._end_reason:
                cause = _root_cause(exc)
                self._end_reason = f"the session ended: {type(cause).__name__}: {cause}"
            if not isinstance(exc, Exception):
                raise
        finally:
            self._session = None
            self._ended.set()

    async def _forward(self, source: Any, sink: Any) -> None:
        """Hand the server's messages to the session, and mark the connection gone after.

        The session is left to see the end of the stream itself. Ending it here instead
        cancelled the SDK's reader before it could answer the call in flight, which then
        waited out its whole deadline and was reported as a TIMEOUT; left alone, the reader
        fails that call with "connection closed" (FAILED: it was running), and the mark
        refuses the next one at once.
        """
        import anyio

        async with sink:
            try:
                async for message in source:
                    await sink.send(message)
            except (anyio.BrokenResourceError, anyio.ClosedResourceError):
                return               # the session stopped reading: it is closing, not gone
        if not self._end_reason:
            tail = self.stderr_tail()
            self._end_reason = ("the server closed the connection"
                                + (f"; its stderr ends: {tail}" if tail else ""))
        self._gone.set()

    def _transport(self) -> Any:
        if self.config.transport == "stdio":
            from mcp import StdioServerParameters
            from mcp.client.stdio import stdio_client

            env = {key: os.environ[key] for key in PROXY_VARIABLES if key in os.environ}
            env.update(self._env)
            return stdio_client(StdioServerParameters(
                command=self.config.command, args=list(self.config.args), env=env,
                cwd=self.config.cwd or None), errlog=self._stderr)
        from mcp.client.streamable_http import streamable_http_client

        return streamable_http_client(self.config.url)

    async def _on_message(self, message: Any) -> None:
        from mcp import types

        if isinstance(message, types.ServerNotification) and isinstance(
                message.root, types.ToolListChangedNotification):
            self._stale = True

    # ---------------------------------------------------------- the check
    async def _check_tools(self, session: Any) -> None:
        from mcp import types

        listed: list[Any] = []
        cursor = None
        for _ in range(_MAX_LIST_PAGES):
            page = await session.list_tools(
                params=types.PaginatedRequestParams(cursor=cursor) if cursor else None)
            listed.extend(page.tools)
            cursor = page.nextCursor
            if not cursor:
                break
        else:
            raise RuntimeError(f"tools/list did not end within {_MAX_LIST_PAGES} pages")
        offered: dict[str, list[dict[str, Any]]] = {}
        for tool in listed:
            # exclude_unset: what the server sent, not the client model's defaults, so an
            # SDK upgrade that adds a default field cannot read as a server-side change.
            offered.setdefault(tool.name, []).append(
                tool.model_dump(mode="json", by_alias=True, exclude_unset=True))
        self._judge(offered)

    def _judge(self, offered: dict[str, list[dict[str, Any]]]) -> None:
        sid = self.config.id
        admitted: dict[str, dict[str, Any]] = {}
        refused: dict[str, _Refusal] = {}
        for name, reviewed in ({} if self.review else self.config.tools).items():
            copies = offered.get(name, [])
            if not copies:
                refused[name] = _Refusal(_UNAVAILABLE, (
                    f"allowlisted tool {name!r} is not offered by MCP server {sid!r}: the "
                    "server drifted from the reviewed snapshot (the tool is missing)"))
            elif len(copies) > 1:
                refused[name] = _Refusal(_DENIED, (
                    f"MCP server {sid!r} offers tool {name!r} {len(copies)} times; refusing "
                    "rather than guessing which one would run"))
            else:
                drift = reviewed.drift(ToolSnapshot.of(copies[0]))
                if drift:
                    refused[name] = _Refusal(_DENIED, (
                        f"tool {name!r} on MCP server {sid!r} drifted from the reviewed "
                        f"snapshot: {'; '.join(drift)}; it is not called until it is "
                        "reviewed again"))
                else:
                    admitted[name] = copies[0]
        self.offered = offered if self.review else {}
        self.hidden = tuple(sorted(name for name in offered if name not in self.config.tools))
        self._admitted, self._refused = admitted, refused

    def _refusal(self, name: str) -> _Refusal | None:
        if self.review:
            return _Refusal(_DENIED, "a reviewing connection calls nothing")
        if name in self._admitted:
            return None
        if name in self._refused:
            return self._refused[name]
        return _Refusal(_DENIED, (
            f"tool {name!r} is not on the reviewed allowlist of MCP server "
            f"{self.config.id!r} ({', '.join(self.config.allowlist)}); a tool outside it "
            "is never exposed or called"))

    async def _recheck(self, session: Any) -> None:
        """List and check the tools again after the server said they changed."""
        import anyio

        assert self._recheck_lock is not None
        async with self._recheck_lock:
            if not self._stale:
                return
            self._stale = False
            try:
                with anyio.fail_after(self.config.connect_timeout_s):
                    await self._check_tools(session)
            except Exception as exc:  # noqa: BLE001 - reported as the call's status
                self._stale = True
                cause = _root_cause(exc)
                raise MCPCallError(
                    f"MCP server {self.config.id!r} announced a changed tool list and could "
                    f"not be checked again: {type(cause).__name__}: {cause}",
                    status=_UNAVAILABLE, metadata=self._meta()) from None

    # ----------------------------------------------------------------- call
    def call_tool(self, name: str, arguments: Mapping[str, Any] | None = None
                  ) -> dict[str, Any]:
        """Call one admitted tool and return its reply in the protocol's wire shape.

        ``{"content": [...], "structuredContent": ..., "isError": bool}`` — the shape
        ``psh.protocols.MCPToolAdapter`` normalises, so this method is the transport an
        adapter can take as it is. An ``isError`` reply is returned, not raised: it is the
        server's answer, and the caller decides what it means. Everything that kept the
        call from producing an answer raises ``MCPCallError`` with its status.
        """
        loop = self._loop
        if loop is None or not self.alive:
            raise MCPCallError(self._closed_reason(), status=_UNAVAILABLE,
                               metadata=self._meta(name))
        future = asyncio.run_coroutine_threadsafe(
            self._call(name, dict(arguments or {})), loop)
        deadline = self.config.call_timeout_s + _GRACE_S + (
            self.config.connect_timeout_s if self._stale else 0.0)
        try:
            return future.result(timeout=deadline)
        except concurrent.futures.TimeoutError:
            future.cancel()
            raise MCPCallError(
                f"MCP tool {name!r} on server {self.config.id!r} produced no answer within "
                f"{deadline:g}s and the loop serving it did not report one",
                status=ExecutionStatus.TIMEOUT, metadata=self._meta(name)) from None

    async def _call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        import anyio
        from mcp.shared.exceptions import McpError
        from mcp.types import CONNECTION_CLOSED

        session, sid, meta = self._session, self.config.id, self._meta(name)
        if session is None:
            raise MCPCallError(self._closed_reason(), status=_UNAVAILABLE, metadata=meta)
        if self._stale:
            await self._recheck(session)
        refusal = self._refusal(name)
        if refusal is not None:
            raise MCPCallError(refusal.reason, status=refusal.status, metadata=meta)
        try:
            with anyio.fail_after(self.config.call_timeout_s):
                result = await session.call_tool(name, arguments)
        except TimeoutError:
            raise MCPCallError(
                f"MCP tool {name!r} on server {sid!r} did not answer within "
                f"{self.config.call_timeout_s:g}s; it may still have done its work",
                status=ExecutionStatus.TIMEOUT, metadata=meta) from None
        except McpError as exc:
            if exc.error.code == CONNECTION_CLOSED:
                tail = self.stderr_tail()
                raise MCPCallError(
                    f"MCP server {sid!r} closed the connection while {name!r} was running"
                    + (f"; its stderr ends: {tail}" if tail else ""),
                    status=ExecutionStatus.FAILED, metadata=meta) from None
            raise MCPCallError(
                f"MCP server {sid!r} answered {name!r} with protocol error "
                f"{exc.error.code}: {exc.error.message[:300]}",
                status=ExecutionStatus.FAILED, metadata=meta) from None
        except (anyio.ClosedResourceError, anyio.BrokenResourceError):
            raise MCPCallError(
                f"the connection to MCP server {sid!r} closed before {name!r} was sent",
                status=_UNAVAILABLE, metadata=meta) from None
        except Exception as exc:  # noqa: BLE001 - the SDK refused the reply, say why
            raise MCPCallError(
                f"the reply of MCP tool {name!r} on server {sid!r} was refused by the "
                f"client: {type(exc).__name__}: {str(exc)[:300]}",
                status=ExecutionStatus.FAILED, metadata=meta) from None
        return result.model_dump(mode="json", by_alias=True, exclude_none=True)

    def _closed_reason(self) -> str:
        if not self._used:
            return f"the connection to MCP server {self.config.id!r} has not been opened"
        why = self._end_reason or "it was closed"
        return f"the connection to MCP server {self.config.id!r} has ended: {why}"

    # ---------------------------------------------------------------- close
    def close(self) -> None:
        """End the session and stop the server. Safe to call twice, and at exit."""
        with self._lock:
            self._shutdown()

    def _shutdown(self) -> None:
        loop, thread = self._loop, self._thread
        self._loop = self._thread = None
        _LIVE.discard(self)
        if loop is not None:
            if not self._ended.is_set():
                closing, task = self._closing, self._task
                if closing is not None:
                    loop.call_soon_threadsafe(closing.set)
                if not self._ended.wait(self.shutdown_timeout_s) and task is not None:
                    # The session did not leave its context managers in time: cancelling it
                    # still runs the SDK's shutdown, which terminates the server's group.
                    loop.call_soon_threadsafe(task.cancel)
                    self._ended.wait(self.shutdown_timeout_s)
            try:
                asyncio.run_coroutine_threadsafe(_drain(), loop).result(
                    timeout=self.shutdown_timeout_s)
            except Exception:  # noqa: BLE001, S110 - the loop is stopped below regardless
                pass
            loop.call_soon_threadsafe(loop.stop)
            if thread is not None:
                thread.join(self.shutdown_timeout_s)
            if thread is None or not thread.is_alive():
                loop.close()
        if not self._end_reason:
            self._end_reason = "it was closed"
        handle, path = self._stderr, self._stderr_path
        self._stderr, self._stderr_path = None, ""
        if handle is not None:
            handle.close()
        if path:
            try:
                os.unlink(path)
            except OSError:                                # pragma: no cover - raced
                pass

    def __enter__(self) -> "MCPConnection":
        return self.open()

    def __exit__(self, *exc: object) -> None:
        self.close()

    def __repr__(self) -> str:  # pragma: no cover - diagnostics
        state = "open" if self.alive else ("ended" if self._used else "new")
        return f"<MCPConnection {self.config.id} {self.config.transport} {state}>"


def _close_all() -> None:
    """Close every connection still open at interpreter exit, so no server outlives it."""
    for connection in list(_LIVE):
        try:
            connection.close()
        except Exception:  # noqa: BLE001 - one failed close must not leave the rest open
            continue


atexit.register(_close_all)
