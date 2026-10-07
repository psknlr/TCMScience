# MCP transport: reviewed servers, connected for real

Until this change, MCP support stopped at the routing layer. `MCPBackend` took a dispatcher
that nothing supplied, so an MCP component resolved and nothing ran. PSH's `MCPToolAdapter`
admitted a server's tools as governed components over a transport that nothing supplied.
The isolated child that PSH's `IsolatedExecutor` starts rebuilt its runtime from scratch,
so nothing injected in the parent could reach it. A call that works only in the parent
process does not count as integrated.

`bioagent.mcp` is the missing transport, built on the official MCP Python SDK (1.x). The
same reviewed configuration and the same connection serve every path:

```
registry/mcp_servers.yaml ──> MCPServerRegistry ──> MCPDispatcher ──> MCPConnection ──> server
   (reviewed entries)          (validated)          (MCPBackend's      (SDK session on its
                                                     dispatcher)        own event loop)
Runtime.invoke ──> MCPBackend ──> dispatcher ............................... in process
PSH broker ──> BridgedComponent ──> Runtime.invoke ──> MCPBackend .......... in process
PSH broker ──> IsolatedExecutor ──> exec.py --mcp-config F --mcp-config-digest D
                                      └─> verifies D, rebuilds the dispatcher . child process
PSH broker ──> MCPToolAdapter component ──> the same MCPConnection ......... in process
```

## What a reviewed entry says

A server describes its own tools in `tools/list`. That description is the server's claim,
not a policy. An entry in `BioScience-Harness/registry/mcp_servers.yaml` records what the
server cannot be trusted to say about itself:

- **Which tools may be called.** `tools` is an allowlist. A tool the server offers that the
  entry does not name is never exposed and never called. The connection records it as
  hidden, and an adapter is never offered it.
- **What those tools were when someone read them.** Each allowlisted tool carries the
  SHA-256 of its canonical `inputSchema`. When the tool has an `outputSchema`, annotations
  or a description, their digests are pinned as well. Canonical means sorted keys, compact
  separators, UTF-8 and no NaN, computed over what the server sent rather than the SDK's
  model of it. The description is pinned because it is the text a model reads, so a
  rewritten description is the usual way to turn a reviewed tool into a different one.
- **Where a call goes.** `destination` is `trusted_remote` or `public_remote`, and it
  becomes the PSH destination the gates rule on. An MCP server is a process this kernel
  does not own, so it is never `LOCAL_COMPUTE`. PHI cannot reach a public server, by the
  same rule that keeps it from a public model.
- **What was reviewed.** `package` and `version` are required, and every result records
  them.
- **How long to wait.** `call_timeout_s` and `connect_timeout_s`.

No secret is ever written in the file. `env` maps a variable the server process receives
to the *name* of a credential, for example `{GITHUB_TOKEN: GITHUB_PAT}`. The process that
starts the server resolves the name, by default from its own environment. A value that is
not a plain upper-case name is refused, so a pasted token fails validation instead of
being committed. Unknown keys are refused for the same reason: a misspelt `tools` would
otherwise drop the allowlist it was meant to carry. Other refusals:

- a relative `command` or `cwd`;
- plain `http` to anything but loopback;
- credentials inside a URL;
- variables that choose the code a server runs (`LD_*`, `PYTHONPATH`, ...);
- the proxy variables.

A malformed entry is refused on its own, with every reason, and the rest of the file stays
usable. A server whose entry was refused answers with that reason instead of "not
configured". An id declared twice admits neither copy. The **config digest**, SHA-256 over
the canonical entry, identifies exactly what was admitted. It is recorded on every result
and in the bridge's admission audit event, and it is handed to the isolated child.

The shipped registry configures no server, so nothing changes for existing users. With no
reviewed server, `default_runtime()` binds no dispatcher, and MCP components stay
unresolvable with "no MCP dispatcher bound", as before.

## The trust rules, at connect and at call

Opening a connection runs `initialize` and pages through `tools/list`, then compares the
result with the entry:

| Situation | Call status | Why |
| --- | --- | --- |
| tool not on the allowlist | `DENIED` | the reviewed entry does not admit it; the server never sees the request |
| allowlisted tool whose digests changed | `DENIED` | the reason names each changed part with both digests |
| allowlisted tool offered twice | `DENIED` | refused rather than guessing which one would run |
| allowlisted tool the server no longer offers | `UNAVAILABLE` | nothing to call |
| server not in the registry, refused at load, or the file unreadable | `UNAVAILABLE` | with the registry's reason |
| cannot start or reach the server, or it exits during `initialize` | `UNAVAILABLE` | the reason quotes the end of the server's stderr |
| a credential the entry names does not resolve | `UNAVAILABLE` | the server is not started without it; the reason names the credential, never a value |
| reply with `isError: true` | `FAILED` | the server's own text, bounded to 300 characters |
| JSON-RPC error (for example `-32042`) | `FAILED` | the code and the server's message |
| server closes the connection while a call runs | `FAILED` | the next call is refused at once, and the dispatcher reconnects |
| no answer within `call_timeout_s` | `TIMEOUT` | the call may still have done its work |
| success | `SUCCEEDED` | metadata: server, tool, transport, schema digest, reviewed package and version, the version the server reported, protocol version, config digest |

A server that sends `notifications/tools/list_changed` is listed and checked again before
the next call is sent. A tool that changed during the session is refused then, not after a
restart. `MCPBackend` also treats any reply in the `tools/call` shape that carries
`isError` as `FAILED`, whichever dispatcher delivered it. The value of a successful reply
follows the rule of PSH's adapter: the structured content, else the single text part, else
the parts. A tool therefore returns the same value whichever path admitted it.

Through PSH, the statuses become the exceptions the loop reasons in: `DENIED` becomes
`PolicyDenied`, `UNAVAILABLE` becomes `CapabilityUnavailable`, `TIMEOUT` becomes
`ToolTimeout`, and anything else becomes `ContractViolation`. The bridge admits an MCP
component as PSH's adapter admits an MCP tool:

- its destination is the server's reviewed destination, or `PUBLIC_REMOTE` when no entry
  names the server;
- it is mutating and consequential (`R2`), because only the server says otherwise;
- its run deadline is at least `connect_timeout_s + call_timeout_s + 30` seconds, so the
  kernel never kills a child that is still within the entry's own deadlines.

`bioagent.psh.admit_mcp_server(kernel, connection)` opens the same `MCPConnection` and
offers `MCPToolAdapter` only the tools it verified. The adapter's transport is the
connection's `call_tool`, with statuses translated as above.

## How a connection is run

The SDK is asynchronous and the runtime is not. Each connection owns one event-loop
thread. The session lives in one task on it, inside the SDK's own `async with` blocks
(`stdio_client` or `streamable_http_client`, then `ClientSession`). Every operation is
submitted with `run_coroutine_threadsafe(...).result(timeout)`, so the caller waits for
the SDK or for the deadline, and never for less. The call deadline is enforced inside the
loop, so a `TIMEOUT` is never a server's error code mistaken for a timeout.

A stdio server's stderr goes to a file. Its last lines are quoted in reasons, and it never
reaches the caller's stderr, which the isolated child reserves for the one line the kernel
records. The server's messages pass through a forwarder, so a server that exits is noticed
at once.

`close()` runs the SDK's shutdown in the session task. The server's stdin is closed, it has
two seconds to exit, and then its process group is terminated. An `atexit` hook closes any
connection still open, so a caller that forgets does not leave a server behind. The tests
check both with a server that ignores the end of its input.

## Adding a server

1. Write a draft entry whose `tools` lists the names to allow:

   ```yaml
   id: example
   transport: stdio
   command: uvx
   args: ["example-mcp-server@1.4.0"]
   destination: public_remote
   package: example-mcp-server
   version: 1.4.0
   tools: [compound_search, get_bioactivity]
   ```

2. Run `python -m bioagent.mcp review draft.yaml`. It connects, lists the tools without
   calling any, and prints the reviewed entry. Above the entry it prints every allowlisted
   tool in full: description, input and output schema, annotations. Read them, because the
   digests vouch for exactly that text.
3. Paste the entry under `servers:` in `registry/mcp_servers.yaml`, then run
   `python -m bioagent.mcp check`. It lists every server's digest and every refusal, and
   it exits non-zero on a refusal.
4. Reach the server's tools in one of two ways. A component manifest can declare
   `runtime: {backend: mcp, server: example, entrypoint: compound_search}`. Or
   `admit_mcp_server` can admit the tools as PSH components directly.

After a server upgrade, review again. Until then, a changed tool is refused with the change
named. Install the SDK with the `mcp` extra: `pip install -e "BioScience-Harness[mcp]"`
(`mcp>=1.29,<2`, because BioMCP and ToolUniverse require `mcp<2`).

## What the isolated child gets

The bridge hands the child its MCP servers the way it hands over the admitted manifest. It
writes a registry holding **only the server the component calls**, owner-only, beside the
manifest under the kernel's state directory. It then adds
`--mcp-config <file> --mcp-config-digest <sha256>` to the command line it admits. In the
child, `exec.py` works as follows:

- It loads that file and refuses it (`ContractViolation`, before anything starts) if its
  digest is not the admitted one.
- It never reads the shipped registry. The child reaches exactly what was admitted, even
  if the file in the repository has changed since.
- It binds a dispatcher with **no credential source**.
- It closes the connection before it exits, so the server stops while the child can still
  stop it cleanly.

The child's environment is the one PSH builds: cleared, `HOME` and `TMPDIR` set to the
sandbox directory, and the proxy variables pointing at the kernel's egress proxy. The SDK
starts a stdio server with a minimal environment that would drop the proxy variables, so
they are handed on explicitly. A proxy-honouring server in the child can therefore reach
only the hosts its component declared.

**Credentials do not reach the child.** PSH's `IsolatedExecutor` does have a secret hook:
the kernel's `secret_resolver` resolves a manifest's `requires_secrets` into the child's
environment. But nothing reviews that path:

- no gate rules on which component may receive which secret;
- no policy field names them;
- the `isolated_run` audit event does not record what was granted;
- no PSH test exercises it.

Handing an MCP credential through that path would rest on an unreviewed mechanism. So a
server whose entry names credentials is refused twice:

- at admission, when the bridge isolates (`BridgeRefused`, with the reason);
- in the child itself (`UNAVAILABLE`), even when the credential is in the child's
  environment.

Such a server runs in-process only (`isolate=False`). Profiles that require isolation
(`restricted_research`, `sensitive_data`) therefore cannot use credentialed MCP servers.
That is the honest consequence until PSH gates and audits secret grants.

Through the kernel, the child's statuses travel as exit codes:

- exit 124 is a `TIMEOUT` and becomes `ToolTimeout`;
- any other failure becomes `ContractViolation`, whose text starts with the status
  (`FAILED: …`, `DENIED: …`, `UNAVAILABLE: …`).

## Limits

- **The snapshot pins what the server says, not what it does.** A server can change its
  behaviour without changing its listing. Review is evidence about the interface, and
  which server code to trust remains the operator's decision.
- `title`, `icons`, `_meta` and `execution` are not pinned.
- **A streamable HTTP server takes no credentials yet.** `env` is for a process, and there
  are no header references. Plain `http` is accepted only for loopback.
- **A local HTTP server cannot be reached from the isolated child.** The kernel's egress
  proxy refuses loopback and private addresses there, so a local HTTP server is reachable
  in-process only. A remote one would be reached through the proxy, whose allowlist then
  includes the entry's host. The tests do not exercise this, because they can serve only
  a loopback host.
- **A timed-out call is abandoned by the client.** The SDK sends no `notifications/cancelled`.
  The server may keep working until it finishes or its connection closes. In the child, it
  is stopped when the child exits.
- **If the process holding a connection is killed with SIGKILL, nothing runs its
  shutdown.** A server that also ignores the end of its stdin would survive, because the
  SDK starts it in a session of its own and the kernel's process-group kill does not reach
  it. The run deadline's margin keeps the kernel from killing a child that is still within
  the entry's deadlines. With `NoSandbox`, a server's raw sockets are not confined, as for
  any isolated component.
- **The resolver asks whether the MCP backend can run, not whether a given server is
  configured.** With any server reviewed, a component naming an unconfigured server
  resolves and is `UNAVAILABLE` at call time, with the reason.
- **BioScience's own `PolicyKernel` rules only on what a component declares.** It sees the
  component's `permissions.network`. The server's reach is ruled on by PSH's destination
  gates and, in the child, by the egress proxy.
- **Every MCP tool admitted by the bridge is treated as mutating,** as PSH's adapter
  treats it. The bridge has no per-tool operator override yet. The adapter keeps its
  `overrides`.

Tests: `BioScience-Harness/tests/test_mcp_transport.py`, against a FastMCP server that the
tests start (`tests/mcp_fixture_server.py`). The protocol is not mocked. The tests cover:

- the configuration and the registry;
- the connection, over stdio and streamable HTTP;
- `Runtime.invoke` through `MCPBackend`;
- the PSH broker in process, through the bridge and through `MCPToolAdapter`;
- the isolated child.

On those paths they check success, `isError`, a JSON-RPC error, timeout, no server, drift,
a tool outside the allowlist, credentials, shutdown and interpreter exit. The tests that
need the SDK skip without it (`need_module("mcp")`) and fail under
`BIOAGENT_REQUIRE_TOOLS=1`.

## 中文摘要

**问题：** MCP 支持此前只停在路由层。`MCPBackend` 需要一个分发器，却没有任何地方提供，因此 MCP 组件能解析却从未执行。PSH 的 `MCPToolAdapter` 也缺少传输层。隔离子进程在干净环境中重建运行时，父进程注入的任何东西都到不了它。只在父进程里成功不算真正接入。

**做法：** `bioagent.mcp` 基于官方 MCP Python SDK 1.x，提供审定配置、连接、分发器和审查工具。进程内调用、PSH 代理调用、`MCPToolAdapter` 和隔离子进程都使用同一份配置和同一种连接。

**信任规则：**
- **只连审定过的服务器。** `registry/mcp_servers.yaml` 默认为空，现有用户的行为不变。
- **工具白名单。** 白名单外的工具既不暴露也不可调用，状态为 DENIED。
- **审定快照。** 每个白名单工具记录 inputSchema 的 SHA-256，以及 outputSchema、annotations、description（如有）的 SHA-256。
  - 任何一项变化都按 DENIED 拒绝，并指明变化的部分。
  - 工具缺失为 UNAVAILABLE。
  - 同名工具重复出现为 DENIED。
  - 服务器发出 `list_changed` 后，下一次调用前重新核验。
- **去向由运维决定。** `trusted_remote` 或 `public_remote` 进入 PSH 的去向闸门，PHI 不会被送到公共服务器。
- **文件中不写密钥。** `env` 只写凭据名称，由启动进程解析，原因说明里也从不出现取值。
- **配置摘要。** 每个结果、准入审计事件和隔离子进程都带有配置摘要，用来确认准入的正是这份配置。

**状态映射：**
- 成功为 SUCCEEDED，并附带服务器、工具、模式摘要、版本和配置摘要。
- `isError` 或 JSON-RPC 错误为 FAILED，附服务器原文（有长度上限）。
- 超时为 TIMEOUT。
- 无法连接或未配置为 UNAVAILABLE。

**隔离子进程：**
- 桥接器把只含该组件所需服务器的注册表文件写在清单旁，并在命令行传入 `--mcp-config` 和 `--mcp-config-digest`。
- 子进程先核对摘要再重建分发器，从不读取仓库里的注册表，并把内核的出口代理传给 stdio 服务器。
- **子进程不接收凭据。** PSH 的 `secret_resolver`/`requires_secrets` 机制没有闸门、没有策略、不记审计，也没有测试，不是经过审定的通道。
- 因此需要凭据的服务器在隔离准入时被拒绝，在子进程中为 UNAVAILABLE，只能在进程内运行。

**局限：**
- 快照约束的是服务器的自我描述，而不是其行为。
- streamable HTTP 暂不支持凭据。
- 本地 HTTP 服务器在子进程中会被出口代理拒绝。
- 超时的调用不会通知服务器取消。
- 持有连接的进程若被 SIGKILL，忽略 stdin 结束的服务器可能残留。
