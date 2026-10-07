"""A small MCP server for tests/test_mcp_transport.py: the real protocol, known answers.

    python mcp_fixture_server.py [--marker FILE]            # stdio
    python mcp_fixture_server.py --drift                    # echo changed since review
    python mcp_fixture_server.py --linger SECONDS           # outlives its stdin closing
    python mcp_fixture_server.py --http PORT                # streamable HTTP, loopback

Built with the SDK's own FastMCP, so every reply the tests judge is one a real server
sends: a structured result, an ``isError`` result from a tool that raises, a JSON-RPC
error, a tool slow enough to time out. ``unreviewed`` is never put on an allowlist and
writes ``--marker`` if it ever runs, which is how a test proves it did not. ``--linger``
makes a server that ignores the end of its input, as a badly behaved one would: only a
client that terminates it stops it, so a test of shutdown cannot pass by politeness.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import time
from pathlib import Path
from typing import TypedDict

from mcp.server.fastmcp import Context, FastMCP
from mcp.shared.exceptions import UrlElicitationRequiredError
from mcp.types import ElicitRequestURLParams


class Sum(TypedDict):
    sum: int


def build(*, drift: bool, marker: str, port: int = 8000) -> FastMCP:
    server = FastMCP("bioagent-test-fixture", log_level="WARNING", port=port)

    if drift:
        @server.tool()
        def echo(text: str, upper: bool = False) -> str:
            """Return the text, upper-cased on request."""
            return text.upper() if upper else text
    else:
        @server.tool()
        def echo(text: str) -> str:
            """Return the text unchanged."""
            return text

    @server.tool()
    def add(a: int, b: int) -> Sum:
        """Add two integers."""
        return {"sum": a + b}

    @server.tool()
    def fail() -> str:
        """Always raise, so the server answers with isError."""
        raise ValueError("the fixture failed on purpose")

    @server.tool()
    async def slow(seconds: float) -> str:
        """Sleep, then answer."""
        await asyncio.sleep(seconds)
        return f"slept {seconds}"

    @server.tool()
    def needs_authorisation() -> str:
        """Answer with a JSON-RPC error rather than a result."""
        raise UrlElicitationRequiredError([ElicitRequestURLParams(
            mode="url", message="sign in first", url="https://auth.invalid/sign-in",
            elicitationId="fixture")])

    @server.tool()
    def pid() -> int:
        """This server's process id."""
        return os.getpid()

    @server.tool()
    def exit_now() -> str:
        """End this server's process in the middle of the call."""
        os._exit(7)

    @server.tool()
    def environment() -> dict[str, str]:
        """Whether the fixture token arrived, and the proxy route, CA bundle and reviewed
        setting this process was given."""
        return {"token": "set" if os.environ.get("FIXTURE_TOKEN") else "unset",
                "https_proxy": os.environ.get("HTTPS_PROXY", ""),
                "ca_bundle": os.environ.get("SSL_CERT_FILE", ""),
                "setting": os.environ.get("FIXTURE_SETTING", "")}

    @server.tool()
    async def change_echo(ctx: Context) -> str:
        """Replace echo with a different schema and announce that the tool list changed."""
        server.remove_tool("echo")

        def echo(text: str, times: int = 1) -> str:
            return text * times

        server.add_tool(echo, name="echo", description="Return the text, repeated.")
        await ctx.session.send_tool_list_changed()
        return "changed"

    @server.tool()
    def unreviewed() -> str:
        """Never on an allowlist. Writes the marker if it ever runs."""
        if marker:
            Path(marker).write_text("ran", encoding="utf-8")
        return "this must never run"

    return server


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--drift", action="store_true")
    parser.add_argument("--marker", default="")
    parser.add_argument("--http", type=int, default=0, metavar="PORT")
    parser.add_argument("--linger", type=float, default=0.0, metavar="SECONDS")
    args = parser.parse_args()
    server = build(drift=args.drift, marker=args.marker, port=args.http or 8000)
    server.run(transport="streamable-http" if args.http else "stdio")
    time.sleep(args.linger)


if __name__ == "__main__":
    main()
