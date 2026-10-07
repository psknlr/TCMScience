# MCP transport: reviewed servers, connected for real

Until this change, MCP support stopped at the routing layer. `MCPBackend` took a dispatcher
that nothing supplied, so an MCP component resolved and nothing ran. PSH's `MCPToolAdapter`
admitted a server's tools as governed components over a transport that nothing supplied.
The isolated child that PSH's `IsolatedExecutor` starts rebuilt its runtime from scratch,
so nothing injected in the parent could reach it. A call that works only in the parent
process does not count as integrated.

`bioagent.mcp` is the missing transport, built on the official MCP Python SDK, 1.x or 2.x.
The same reviewed configuration and the same connection serve every path:

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
otherwise drop the allowlist it was meant to carry.

`settings` holds the server's non-secret variables a review fixes, for example a cache
directory the run owns (`{XDG_CACHE_HOME: /runs/r1/cache}`). Values are paths, flags and
numbers on one line. A value that starts like a provider's token (`ghp_`, `sk-`, `AKIA`,
`Bearer `) or, outside an absolute path, holds a long unbroken run of token characters is
refused, and so is a name that is also a credential variable. Settings are part of the
entry, so the config digest covers them. Other refusals:

- a relative `command` or `cwd`;
- plain `http` to anything but loopback;
- credentials inside a URL;
- variables that choose the code a server runs (`LD_*`, `PYTHONPATH`, ...);
- the proxy variables, the CA variables (`SSL_CERT_FILE`, `SSL_CERT_DIR`,
  `REQUESTS_CA_BUNDLE`, `CURL_CA_BUNDLE`), and the variables that turn a server's
  certificate checks off or write out its TLS keys (`NODE_TLS_REJECT_UNAUTHORIZED`,
  `NODE_EXTRA_CA_CERTS`, `PYTHONHTTPSVERIFY`, `SSLKEYLOGFILE`): an entry that set any of
  them could send a server's traffic elsewhere, or let it be read on the way.

A malformed entry is refused on its own, with every reason, and the rest of the file stays
usable. A server whose entry was refused answers with that reason instead of "not
configured". An id declared twice admits neither copy. The **config digest**, SHA-256 over
the canonical entry, identifies exactly what was admitted. It is recorded on every result
and in the bridge's admission audit event. The isolated child is handed the digest of the
one-server registry it receives, and refuses a file that does not match it.

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
| success | `SUCCEEDED` | metadata: server, tool, transport, schema digest, reviewed package and version, the version the server reported, protocol version, config digest, the client SDK's version |

A server that sends `notifications/tools/list_changed` is listed and checked again before
the next call is sent. A tool that changed during the session is refused then, not after a
restart. `MCPBackend` also treats any reply in the `tools/call` shape that carries
`isError` as `FAILED`, whichever dispatcher delivered it. The value of a successful reply
follows the rule of PSH's adapter: the structured content, else the single text part, else
the parts. A tool therefore returns the same value whichever path admitted it.

Through PSH, the statuses become the exceptions the loop reasons in: `DENIED` becomes
`PolicyDenied`, `UNAVAILABLE` becomes `CapabilityUnavailable`, `TIMEOUT` becomes
`ToolTimeout`, and anything else becomes `ContractViolation`. The bridge admits an MCP
component by the rules PSH's adapter applies to an MCP tool, and gives an isolated run room
for the entry's deadlines:

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
at once. The forwarder also notices `notifications/tools/list_changed`, in the order the
server sent it, before it reads the reply that follows. A notification callback would not
do: the 2.x SDK runs each one as a task of its own, which can run after that reply, and a
call sent in the gap would reach a changed tool unchecked.

`close()` runs the SDK's shutdown in the session task. The server's stdin is closed, it has
two seconds to exit, and then its process group is terminated. An `atexit` hook closes any
connection still open, so a caller that forgets does not leave a server behind. The tests
check both with a server that ignores the end of its input.

## Two SDK majors

The transport runs on the 1.x SDK (from 1.29) and on the 2.x SDK (tested on 2.2.0 and
2.3.0). 2.x renamed what this code touches: `FastMCP` became `MCPServer`, model fields
went from camelCase to snake_case, `McpError` became `MCPError`, and a message no longer
arrives wrapped in a root model. Each difference is detected where it is used, not looked
up by version, so the same code runs under both:

- protocol objects are read in their wire form (`model_dump(by_alias=True)`), which has
  the protocol's camelCase names under either major;
- the JSON-RPC error class is whichever of the two names the SDK has;
- a message's method is read through the 1.x wrapper when there is one.

A later major imports too, and could fail in ways that read as the server's fault. So a
connection refuses a 3.x SDK as UNAVAILABLE before any server starts, naming the
installed version and the fix. The tests' `need_mcp_sdk()` skips on it, and fails on it
under `BIOAGENT_REQUIRE_TOOLS=1`.

The digests do not depend on the major. They are taken over a tool's wire form, and the same
live server reviewed under 1.29.0, 1.30.0, 2.2.0 and 2.3.0 gave identical entries
(DeepWiki, below), so an entry reviewed under one major is checked under the other. Three
differences remain.
The provenance of a call (`MCPConnection.provenance`, the metadata of a `Runtime.invoke`
result) records `client_sdk_version`, so a reader can tell which applied:

- **Fields the protocol does not define.** 1.x keeps them, on a tool and inside its
  annotations; 2.x drops them. A tool whose annotations carry such a field digests
  differently under the two majors. It is refused as drifted, with the part named, until
  it is reviewed under the SDK that runs it.
- **A call that times out.** The 2.x client sends `notifications/cancelled`, and a server
  that honours it stops. The 1.x client sends nothing, and the server may finish the work.
  Checked against an SDK server: under 2.3.0 the tool was interrupted, under 1.30.0 it ran
  to the end.
- **The reply.** Under 2.x it also carries `resultType: complete`, the SDK's default where
  a 2025-era server sends none.

BioMCP and ToolUniverse pin `mcp<2` themselves, so an installation that includes either of
them runs 1.x.

## Over HTTPS

A `streamable_http` entry with an `https` URL is reached on the HTTP client the SDK makes.
That client checks the server's certificate chain and its name. It trusts the SDK's
default CAs (certifi's bundle under 1.x, the system's store under 2.x). When
`SSL_CERT_FILE` or `SSL_CERT_DIR` is set in the process's environment, it trusts the CAs
they name instead. These are CA variables the transport also hands on to the stdio
servers it starts. A certificate that does not verify is refused as UNAVAILABLE before
anything is sent, with OpenSSL's words: `CERTIFICATE_VERIFY_FAILED`, then
`unable to get local issuer certificate` or `IP address mismatch`.

Nothing in the transport can turn the check off:

- No entry key reaches the HTTP client. An unknown key, `verify: false` for one, refuses the
  entry.
- The environment decides only which CAs are trusted, never whether certificates are
  checked.
- The client follows a redirect only within the origin: the same scheme, host and port,
  or an upgrade from http to https. It never follows one down to plain http. The 1.29 SDK
  follows any redirect, from https to plain http on another host included, with the tool's
  arguments in the body. The transport therefore turns redirects off on the client it hands
  the SDK. 1.30 and later ignore that setting, because they apply the origin rule
  themselves.
- A stdio server cannot be given the variables that turn its own checks off (listed above).

The tests serve the fixture over HTTPS, with a certificate from a CA they create and hand to
the client through `SSL_CERT_FILE`. Two variants of the same server must be refused: one
whose certificate names another host, and one whose certificate comes from a CA the client
was not given. A further test sends a redirect to a working server on another origin, and
checks that the client does not follow it. Under 1.29.0 without the redirect setting, the
client followed an https redirect to plain http and the call was answered there.

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
(`mcp>=1.29,<3`). On its own the extra resolves to the latest 2.x. Installed beside BioMCP
or ToolUniverse, which require `mcp<2`, it resolves to 1.x. To choose a major, add
`"mcp<2"` or `"mcp>=2.2,<3"` to the install line.

## A public server

`test_a_public_server_is_reviewed_checked_and_called_over_https`, marked `integration`,
uses DeepWiki's public server at `https://mcp.deepwiki.com/mcp`, which takes no
credentials. The test reviews it with `python -m bioagent.mcp review` and checks the entry
with `check`. It then calls the read-only `read_wiki_structure` tool through
`MCPConnection` and through `Runtime.invoke`. The server's other two tools are not
allowlisted, so they stay hidden: calling one is DENIED and never sent. The same live
server, checked against a snapshot with a wrong description digest, is refused with that
part named.

The test reviews the server live rather than pinning its digests. A pinned snapshot would
fail whenever the service rewrites a description, which is the service changing, not the
transport. The shipped registry still configures no server.

On 2026-10-07, from a sandbox whose egress goes through an HTTPS proxy, the test passed under
mcp 1.29.0, 1.30.0, 2.2.0 and 2.3.0, in 10 to 15 s each. The four reviews printed the same
entry, byte for byte. These other public servers also answered `initialize` without
credentials that day:

- `https://docs.mcp.cloudflare.com/mcp`;
- `https://gitmcp.io/docs`, in 11 s;
- `https://learn.microsoft.com/api/mcp`;
- `https://mcp.context7.com/mcp`;
- `https://huggingface.co/mcp`.

`https://mcp.semgrep.ai/mcp` asked for a token, and `https://remote.mcpservers.org/fetch/mcp`
could not be reached through the proxy. Only DeepWiki was reviewed and called.

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
they are handed on explicitly, with the CA variables (behind a proxy that presents its own
certificate, a server without them reaches nothing). A proxy-honouring server in the child
can therefore reach only the hosts its component declared.

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

- **Two SDK majors, and no later one.** The transport is written and tested for 1.x (from
  1.29) and 2.x (`mcp>=1.29,<3`). A 3.x SDK is refused as UNAVAILABLE before any server
  starts. Where the majors differ (fields the protocol does not define, cancellation, the
  reply's `resultType`), a call's provenance records which one ran.
- **The snapshot pins what the server says, not what it does.** A server can change its
  behaviour without changing its listing. Review is evidence about the interface, and
  which server code to trust remains the operator's decision.
- `title`, `icons`, `_meta` and `execution` are not pinned.
- **A streamable HTTP server takes no credentials yet.** `env` is for a process, and there
  are no header references. Plain `http` is accepted only for loopback.
- **The variables refused for turning certificate checks off name the usual runtimes**
  (Node, Python, OpenSSL). A server whose runtime reads some other variable for that is not
  covered by the list.
- **The HTTP client comes from an SDK helper that is not public API**
  (`mcp.shared._httpx_utils.create_mcp_http_client`, present from 1.29 through 2.3). A
  release that moved it would make streamable HTTP connections fail as UNAVAILABLE, naming
  the import, rather than run with redirects on.
- **A local HTTP server cannot be reached from the isolated child.** The kernel's egress
  proxy refuses loopback and private addresses there, so a local HTTP server is reachable
  in-process only. A remote one would be reached through the proxy, whose allowlist then
  includes the entry's host. That path is not exercised: the public-server test calls
  DeepWiki in process, and the other tests can serve only a loopback host.
- **A timed-out call is abandoned by the 1.x client.** That SDK sends no
  `notifications/cancelled`, so the server may keep working until it finishes or its
  connection closes. The 2.x SDK sends it. In the child, the server is stopped when the
  child exits.
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

Tests: `BioScience-Harness/tests/test_mcp_transport.py`, against a server that the tests
start (`tests/mcp_fixture_server.py`). It is built with the SDK's own server class:
`FastMCP` under 1.x, `MCPServer` under 2.x. The protocol is not mocked. The tests cover:

- the configuration and the registry;
- the connection, over stdio, streamable HTTP and HTTPS;
- `Runtime.invoke` through `MCPBackend`;
- the PSH broker in process, through the bridge and through `MCPToolAdapter`;
- the isolated child;
- a public server over the internet (`integration`, not run in CI by default).

On those paths they check success, `isError`, a JSON-RPC error, timeout, no server, drift,
a tool outside the allowlist, credentials, shutdown and interpreter exit. Over HTTPS they
also check certificate refusals and a redirect off the origin. The tests that need the SDK
skip without it (`need_mcp_sdk()`) and fail under `BIOAGENT_REQUIRE_TOOLS=1`.

All but the public-server test use only loopback. They were run with
`BIOAGENT_REQUIRE_TOOLS=1` under mcp 1.29.0, 1.30.0, 2.2.0 and 2.3.0. To run them under each
major:

```
pip install -e "BioScience-Harness[dev,mcp]" "mcp>=1.29,<2"    # 1.x
pip install -e "BioScience-Harness[dev,mcp]" "mcp>=2.2,<3"     # 2.x
```

## 中文摘要

**问题：** MCP 支持此前只停在路由层。`MCPBackend` 需要一个分发器，却没有任何地方提供，因此 MCP 组件能解析却从未执行。PSH 的 `MCPToolAdapter` 也缺少传输层。隔离子进程在干净环境中重建运行时，父进程注入的任何东西都到不了它。只在父进程里成功不算真正接入。

**做法：** `bioagent.mcp` 基于官方 MCP Python SDK（1.x 或 2.x），提供审定配置、连接、分发器和审查工具。进程内调用、PSH 代理调用、`MCPToolAdapter` 和隔离子进程都使用同一份配置和同一种连接。

**信任规则：**
- **只连审定过的服务器。** `registry/mcp_servers.yaml` 默认为空，现有用户的行为不变。
- **工具白名单。** 白名单外的工具既不暴露也不可调用，状态为 DENIED。
- **审定快照。** 每个白名单工具记录 inputSchema 的 SHA-256，以及 outputSchema、annotations、description（如有）的 SHA-256。
  - 任何一项变化都按 DENIED 拒绝，并指明变化的部分。
  - 工具缺失为 UNAVAILABLE。
  - 同名工具重复出现为 DENIED。
  - 服务器发出 `list_changed` 后，下一次调用前重新核验。
- **去向由运维决定。** `trusted_remote` 或 `public_remote` 进入 PSH 的去向闸门，PHI 不会被送到公共服务器。
- **文件中不写密钥。** `env` 只写凭据名称，由启动进程解析，原因说明里也从不出现取值。`settings` 只放评审确定的非机密变量（如本次运行专属的缓存目录）；形似令牌的值、代理变量、CA 证书变量，以及关闭证书校验或导出 TLS 密钥的变量（`NODE_TLS_REJECT_UNAUTHORIZED`、`NODE_EXTRA_CA_CERTS`、`PYTHONHTTPSVERIFY`、`SSLKEYLOGFILE`）一律拒绝，设置也计入配置摘要。
- **配置摘要。** 每个结果、准入审计事件和隔离子进程都带有配置摘要，用来确认准入的正是这份配置。

**状态映射：**
- 成功为 SUCCEEDED，并附带服务器、工具、模式摘要、版本、配置摘要和客户端 SDK 版本。
- `isError` 或 JSON-RPC 错误为 FAILED，附服务器原文（有长度上限）。
- 超时为 TIMEOUT。
- 无法连接或未配置为 UNAVAILABLE。

**两个 SDK 大版本：**
- 同一份代码在 1.x（1.29 起）和 2.x 下运行。2.x 改名之处（`FastMCP`→`MCPServer`、驼峰字段→蛇形字段、`McpError`→`MCPError`、消息不再包在根模型里）在使用处按特征检测，而不是按版本号分支。
- 3.x 及以后的大版本在启动任何服务器之前即被拒绝（UNAVAILABLE），并说明版本和解决办法。
- 摘要与大版本无关：同一个线上服务器在 1.29.0、1.30.0、2.2.0 和 2.3.0 下审定，得到逐字节相同的条目。
- 仍有三点差异，因此调用的来源信息（`MCPConnection.provenance`，即 `Runtime.invoke` 结果的 metadata）记录 `client_sdk_version`：
  - 协议未定义的字段，1.x 保留、2.x 丢弃；annotations 中带此类字段的工具在两个大版本下摘要不同，会按漂移拒绝，直到在实际运行的 SDK 下重新审定。
  - 超时的调用，2.x 会发送 `notifications/cancelled`，1.x 不发送。
  - 2.x 的回复多带 `resultType`。
- `tools/list_changed` 通知由转发器按服务器发送的顺序识别：2.x 的通知回调是独立任务，可能晚于其后的回复执行。

**HTTPS：**
- 使用 SDK 自己创建的 HTTP 客户端，校验证书链和主机名；信任 SDK 默认的 CA（1.x 为 certifi，2.x 为系统证书库），或 `SSL_CERT_FILE`/`SSL_CERT_DIR` 指定的 CA。
- 校验失败时，在发送任何内容之前即以 UNAVAILABLE 拒绝，并给出 OpenSSL 的原因。
- 传输层没有任何关闭校验的途径：
  - 条目中的任何键都到不了 HTTP 客户端；
  - 环境变量只能改变信任哪些 CA；
  - 重定向只在同源内跟随，不会降级到明文 http。1.29 会跟随任意重定向，因此传输层在交给 SDK 的客户端上关闭了重定向。
- 测试用自建 CA 签发的证书，经 `SSL_CERT_FILE` 交给客户端；主机名不符的证书和来自未提供 CA 的证书都被拒绝；跨源重定向不被跟随。

**公共服务器：**
- 集成测试（`integration`）用 `python -m bioagent.mcp review` 审定 DeepWiki 的公共服务器（`https://mcp.deepwiki.com/mcp`，无需凭据），再经 `MCPConnection` 和 `Runtime.invoke` 调用只读工具 `read_wiki_structure`。
- 白名单外的工具不发送即被拒绝（DENIED）；摘要不符时按漂移拒绝。
- 2026-10-07 在 1.29.0、1.30.0、2.2.0 和 2.3.0 下均通过。当天可匿名访问的其他服务器列在上文；只审定并调用了 DeepWiki。

**隔离子进程：**
- 桥接器把只含该组件所需服务器的注册表文件写在清单旁，并在命令行传入 `--mcp-config` 和 `--mcp-config-digest`。
- 子进程先核对摘要再重建分发器，从不读取仓库里的注册表，并把内核的出口代理传给 stdio 服务器。
- **子进程不接收凭据。** PSH 的 `secret_resolver`/`requires_secrets` 机制没有闸门、没有策略、不记审计，也没有测试，不是经过审定的通道。
- 因此需要凭据的服务器在隔离准入时被拒绝，在子进程中为 UNAVAILABLE，只能在进程内运行。

**局限：**
- 支持 1.x 与 2.x SDK（`mcp>=1.29,<3`）；3.x 起直接拒绝（UNAVAILABLE）。
- 快照约束的是服务器的自我描述，而不是其行为。
- streamable HTTP 暂不支持凭据。
- 被拒绝的“关闭证书校验”变量只覆盖常见运行时（Node、Python、OpenSSL）。
- HTTP 客户端来自 SDK 的非公开辅助函数（`mcp.shared._httpx_utils.create_mcp_http_client`，1.29 至 2.3 均有）；若后续版本移走它，streamable HTTP 连接会以 UNAVAILABLE 失败并指明导入错误，而不会在开启重定向的情况下运行。
- 本地 HTTP 服务器在子进程中会被出口代理拒绝；经子进程访问远程服务器的路径尚未验证。
- 1.x 客户端在调用超时后不会通知服务器取消；2.x 会。
- 持有连接的进程若被 SIGKILL，忽略 stdin 结束的服务器可能残留。
