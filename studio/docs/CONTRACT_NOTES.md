# Contract notes

Append-only notes where a part assumes something CONTRACTS.md does not say. One section per role and topic.

## edge: the relay as built (additions to §1)

- `model` must be `"Tao-S1"` or absent. The upstream's own model names are refused too (`model_not_allowed`), so the
  relay cannot be used to confirm what is behind Tao-S1.
- Error `type`s beyond §1's list: `https_required` (403), `not_found` (404), `method_not_allowed` (405, with `Allow`).
  `blocked` is reserved and never emitted (there is no sentinel in v1).
- Upstream statuses: 401/403 → 503 `upstream_auth`; 429 → 429 `upstream_rate` (`Retry-After`: the upstream's, else 30);
  ≥500 and 404 → 502 `upstream_error`; other 4xx → 400 `upstream_rejected`; unreachable → 502 `upstream_unreachable`.
  A refusal on content moderation is 400 `upstream_rejected` with a message that says to rephrase.
- An error the upstream reports *inside* a stream arrives as one SSE event `data: {"error":{"message","type"}}` (then
  usually `data: [DONE]`). web-core's `llm/openai.js` should surface it as a model error, not as text.
- A page on another allowed origin (the runner's `http://127.0.0.1:8765`) can read `Retry-After`
  (`Access-Control-Expose-Headers`). Every 429 carries it.
- What the client should send, to keep the relay on its cheap path (it edits the request as text instead of
  re-serializing the conversation): `model` as the first key; `max_completion_tokens` ≤ `health.max_output_tokens`;
  never `n`; `stream_options: {include_usage: true}` when streaming; no key. Replay each assistant turn verbatim,
  `reasoning_details` with their `format: "Tao-…"` included (the relay restores the upstream's names). The upstream may
  stream `content` / `reasoning_details` cumulatively rather than as increments; the client should accept both.
- Kill switch besides a missing key: `RELAY = "off"` in `studio/edge/wrangler.toml` → `health.ok:false`, chat 503
  `not_configured`.

## edge: static serving, for web-ui and browser-runtime

- A path that matches no file is answered by the Worker: a browser **navigation** (`Sec-Fetch-Mode: navigate`, or
  `Accept: text/html` without Fetch Metadata) gets `index.html` with 200; anything else gets a real 404 (never the
  app's HTML in place of a missing script or JSON file). So History-API routes such as `/p/<id>/c/<id>` work, **provided
  the page references its own files by absolute path** (`/js/main.js`, `/runtime/boot.json`): a relative path would
  resolve under the route and 404. `<base href>` is not an option (`base-uri 'none'` in the CSP). Hash routing also works.
- Every file is served with `Cross-Origin-Opener-Policy: same-origin` and `Cross-Origin-Embedder-Policy: require-corp`
  (`crossOriginIsolated` is true; verified in Chromium with a module worker importing Pyodide from jsDelivr). Under
  `require-corp` a cross-origin subresource loads only with CORS or a `Cross-Origin-Resource-Policy` header:
  - load anything from another origin with `fetch()` (CORS) and show it through a `blob:` URL, rather than
    `<img src="https://…">` / `<img src="http://127.0.0.1:8765/…">`;
  - an `<iframe>` of a runner URL is blocked unless the runner sends `Cross-Origin-Resource-Policy: cross-origin` and,
    for the HTML, `Cross-Origin-Embedder-Policy: require-corp` (see the runner-service note below); a `blob:` iframe of
    fetched bytes needs neither.
- `Content-Security-Policy-Report-Only` (in `studio/edge/_headers` and `studio/edge/src/site.js`): `script-src 'self'
  https://cdn.jsdelivr.net 'wasm-unsafe-eval'` — no inline `<script>` and no `eval`/`new Function` (they would be
  reported now and blocked when the policy is enforced); inline `style` attributes are allowed; `img-src 'self' data:
  blob:`; `connect-src 'self' https: http://127.0.0.1:* http://localhost:* ws://127.0.0.1:*`; `worker-src 'self' blob:`;
  `frame-src 'self' http://127.0.0.1:* http://localhost:* blob:`; `font-src 'self' https://cdn.jsdelivr.net data:`.
- Caching: `/runtime/*.tar.gz` is `immutable` for a year, so the bundle's name must change whenever its content does
  (the `<sha12>` in `runtime/tcms-py.<sha12>.tar.gz`, §5). Everything else is `no-cache` (revalidated each load).
- `_headers` lives in `studio/edge/`; the workflow copies it into `studio/_site/` after `build_web.py`. browser-runtime:
  please have `build_web.py` copy `studio/edge/_headers` into `<out>/_headers` as well when it exists, so that a manual
  `wrangler deploy` gets the headers too (same file; the copy in the workflow is then a no-op).
- Workers Static Assets limits: 25 MiB per file, 20 000 files (the build job fails early beyond them).

## edge: runner-service — isolation and Tao-S1 for the app served by the runner

- If the runner serves the app (`http://127.0.0.1:8765/`) and the browser runtime should be interruptible there too, its
  static responses need the same `Cross-Origin-Opener-Policy: same-origin` and `Cross-Origin-Embedder-Policy:
  require-corp` (loopback is a secure context). Job files meant to be embedded by the science.impf.ai page need
  `Cross-Origin-Resource-Policy: cross-origin` (+ COEP on HTML shown in an iframe).
- The relay accepts exactly `http://127.0.0.1:8765` and `http://localhost:8765`: a runner on another port can serve the
  app, but that page cannot use Tao-S1 unless `ALLOWED_ORIGINS` in `studio/edge/wrangler.toml` lists its origin.
- The relay refuses requests without a page `Origin` (§1); the runner itself (Python) cannot call Tao-S1.

## edge: what CI runs (.github/workflows/studio.yml)

- **when**: a push or PR that changes `studio/**`, the workflow itself, or what the site bundles and tcmstudio imports
  from the harnesses (`PSH-Harness/src/**`, `PSH-Harness/pyproject.toml`, `BioScience-Harness/{src,skills,registry}/**`,
  `BioScience-Harness/{pyproject.toml,setup.py}`; not their docs, tests or data); or a manual run.
- **test**: `node --test studio/edge/test/*.test.js`; then, when `studio/runner/pyproject.toml` exists,
  `pip install -e PSH-Harness -e BioScience-Harness -e "studio/runner[test]" pytest` and
  `python -m pytest -q studio/runner/tests` (runner-core: put any further test-only dependency in a `test` extra);
  then, when `studio/package.json` exists, `cd studio && (npm ci || npm install) && npm test`. Python 3.12, Node 22.
- **build**: `pip install -e PSH-Harness -e BioScience-Harness -e studio/runner`,
  `python3 studio/scripts/build_web.py --out studio/_site`, `_headers` copied in, `wrangler deploy --dry-run`; then, if
  `studio/package.json` defines a `test:e2e` script: `npx playwright install --with-deps chromium` and
  `npm run test:e2e` with `STUDIO_SITE` = the absolute path of the built site (integration: define `test:e2e`; the
  build's output is there to use). In CI the relay spec fails, never skips, when `wrangler dev` cannot start: the
  dry run has already fetched the same wrangler, so a failure there is the Worker's (outside CI it skips only when
  npm's registry cannot be reached).
- **deploy**: main only (push or manual run), after test and build. `wrangler deploy` in CI replaces an existing DNS
  record or another Worker's custom domain for science.impf.ai without asking (stdout is not a terminal): there is no
  safeguard against that, only the owner's check before the first deployment (SETUP.md step 7). The check after it
  requires a Tao-S1 answer when the `MINIMAX_API_KEY` secret is set, unless `RELAY = "off"` in `wrangler.toml`
  (the persistent pause: health `ok:false` is then expected; the key is still put, so unpausing is `RELAY = "on"`).

## web-core: the JS core as built (additions to §6–§9)

**Ownership.** web-core also keeps `studio/web/test/fixtures/**` (test setup, fakes, and governance fixture envelopes
generated from the real kernel by `fixtures/make_governance_fixtures.py`). `studio/package-lock.json` goes with
`package.json`. Dev-only dependencies: `fake-indexeddb`, `linkedom` (and, for `e2e/a11y.spec.mjs`, `axe-core`, pinned). `web/test/i18n.test.mjs` checks the glossary
against the Python tables when `python3 -c "import bioagent, psh"` works (CI's test job installs them), else skips that
one test.

**Agent (`core/agent.js`).**
- `runTurn` takes, besides §6: `onApproval(request)` (optional), `tools` (core tools; default `catalog.core`), and for
  tests `llm`, `retryDelays`, `sleep`. `history` is the stored branch ending with the new user message.
- Events beyond §6 (additive; ignore what you do not use): `tool.pending {index, name, step}` while the model is still
  writing a call; `model.retry {step, attempt, delay_ms, message, notice}` — **drop the partial text/reasoning of that
  step**, the attempt is repeated; `step` on text/reasoning/tool.*; `usage` carries running totals plus `round`.
  `tool.start` for unparseable arguments has `args: {}` and `raw`. A stop emits `error {kind:"aborted"}` then
  `done {status:"stopped"}`.
- `tool.approval {callId, request}`: `request = {callId, tool, entry, reason, reasons[], what, hosts[], args, projectId,
  text, respond?}`. Without `onApproval`, answer with `request.respond("once"|"project"|"deny")`; a stop cancels it.
- Max steps: never more model calls than `settings.maxSteps` (16). The last allowed call is made with
  `tool_choice: "none"`; tools asked for anyway are recorded as not run (envelope `cancelled`) and the turn ends with
  `status: "max_steps"`.
- `TurnResult.assistant` (§7 message) also has: `segments: [{type:"reasoning"|"text"|"tools", step, text|callIds}]`
  (render order), `wireFormat` ("openai"|"anthropic"), `steps`, `finishReason`, and when relevant `hideReasoning`
  (identity turn), `limitReached` + `notice` (max steps; `status` stays "ok"), `refusal {category, explanation}` (the
  model declined: a result), `servedBy`/`fallback` (another model answered), `error {message, kind, status?, type?}`.
  `content` is all text segments joined with a blank line. `toolMessages[i]`: `{id, conversationId, parentId:
  assistant.id, role:"tool", toolCallId, name, args, content: envelope.text, envelope, step, where, status}`, in call
  order. A user message may carry `attachmentNames` (replayed to the model as "[attached: …]").
- Replay: a turn is replayed from `wire` only to a provider of the same `wireFormat`; Anthropic thinking blocks and
  `reasoning_details` only to the same provider id; `reasoning_content`/`reasoning` never from earlier turns; inline
  `<think>` always stripped; earlier tool results cut to 2 500 characters.

**Router (`core/router.js`).**
- Approval reasons: `network | job | confirm | remote_upload | first_runner_call | remote_runner` (`confirm` = a
  confirm entry that is not a job; `remote_runner` = the first call on a runner whose address is not loopback).
  Keys stored in `project.approvals` as `"project"`: the entry id (confirm/job), `<entry id>:network@<sha12>` (the
  hosts of the call: CONTRACTS §6), `<entry id>:remote_upload`, `runner` (first call on the loopback runner) and
  `runner@<host>` (a remote runner). A `<entry id>:network` key from before still stands, but only for calls whose
  hosts are among the entry's static hosts. Core tools are keyed by the entry they map to, so an approval through
  `call_tool` covers the core tool too. A family core tool (`run_pipeline` → `job.pipeline.*`, `connector_call` →
  `connector.*`) is keyed by its own name. Its hosts, engines and limits are those of the entry its arguments name.
  "once" on the first call of a runner holds for the page's life. That runner's, for a remote one. A clinic (`phi`)
  call to a remote runner is asked about every time and is never stored, and so is a network call whose hosts are
  not known in advance (`hosts_by` does not map its argument: `system.provider_call` with an unlisted `tool`); no
  `<entry id>:network` key from before stands for it. Without Web Crypto the network key is
  `<entry id>:network@<sorted hosts, comma-joined>`. A "project" answer that could not be stored (no project or no
  store) is recorded with scope `page`, not `project`.
- Network entries need `project.defaults.web` (else `settings.web` when there is no project; both on by default);
  off → `failed` / `network_off`, no prompt. On, each network entry still asks before its first call (`network`
  approval) unless the project stands approved for it. Hosts shown come from `entry.hosts` or `entry.skill.network`.
  A context that does not say (`network` absent) means on; a context the dispatcher cannot read means off.
- Runtimes are called as `call(name, args, {project_id, conversation_id, approvals: [granted reasons], signal})`;
  the runner forwards the first three as §4's `context`. A runtime that throws gives `failed/runtime_error`; a stop
  gives `cancelled` (the browser call itself may go on; its result is discarded).
- A tool name that differs from one id only in case and separators (`tcmdb_status` for `tcmdb.status`: function-calling
  APIs allow no dots) runs as that id, directly or through `call_tool`, here and in the dispatcher, and its `text`
  ends `Note: '<name>' is written '<id>'; call it so.`; a spelling two ids share stays `not_found`.
- In a turn, the agent answers a call repeated with the same arguments from the first (`reused: {step, callId}`, a
  `text` that points to it, no citations) when the target is a native, connector or skill entry, `tcmdb.formulas` or
  `tcmdb.materia`, and not a job or a confirmed act; a job, a confirmed act, or a clinic, tcmdb or study call starts
  the count again, and a first answer that did not settle (a runtime error, a stop) is run again.
- `catalog_search` / `capabilities_status` are answered by the page when the placed runtime is not `ready`
  (`receipt: {where:"browser", runtime:"studio-js", local:true}`), so the model never waits for Pyodide to search.
  Under compute `browser` (or `remote`, or an unknown value) they are never sent to a connected runner; under
  `remote` or an unknown value the page answers them even when Pyodide is ready (`where()` → `"page"`), so the
  entries it lists as not runnable, with that preference's need, are the ones the router refuses.
- Router-made envelopes: `receipt.where "page"`, `runtime "studio-router"`, `decided_by: "router"`, `device: null`,
  `output_sha256: null`, `input_sha256` = sha256 of canonical JSON (sorted keys, `(",",":")`, non-ASCII kept — the
  same bytes as Python's `json.dumps(sort_keys=True, separators=(",",":"), ensure_ascii=False)` except for floats
  such as `1.0`), plus `core/receipt.js baseReceipt`'s `schema`, `finished_at`, `execution.runtime "page"` and
  `result {kind:null, outcome, assurance:"none"}`. The runtime clients' own envelopes use the same builder but keep
  `where` = the runtime they stand for: `runtime "studio-runner-client"` (where `runner`, decided_by
  `runner-client`), `"studio-unavailable-runtime"` (where its kind, decided_by `unavailable-runtime`), and the
  worker's runtime string (where `browser`, decided_by `browser-runtime`). The UI's place badge reads `where`, and
  only `decided_by: "router"` is shown as "not run" there today: a `where:"page"` on these would label a call that
  targeted the runner 「浏览器」. A runner-client envelope is `not_executed` only for mixed content, the
  local-network permission, an origin the runner refuses, or a 4xx; a timeout, a dropped connection or a 5xx is
  `failed` (the runner may have started it), and its text does not say "Not run". `call()` never rejects.
- Placement reasons beyond the first set: `unknown_compute`, `remote_not_configured`, `too_large_for_browser`,
  `missing_engine` (CONTRACTS §6, with their requirements). Their messages are in `core/i18n.js`
  (`core.router.<reason>`, zh and en), along with `core.approval.remote_runner`, `core.approval.remote_runner_phi` and
  `core.runner.label_remote` (「远程 Runner」: a runner on another machine is never called 本机). Also
  `core.approval.job_remote` (a job on a remote runner, its host named), `core.approval.network_unknown`,
  `core.router.missing_deps_remote`, `core.router.too_large_for_browser_only` (an entry only the browser runs),
  `core.runner.{not_running,timeout,http}_remote` and `core.local.capabilities_remote`. `PLACEMENT_REASONS` also
  lists `human_only` (placement provenance of a HUMAN_ONLY refusal only). What the connected runner lacks takes the
  reason of its first need (`needs_dataset`, `needs_job_service` from `RUNTIME_REASONS`, or `missing_deps`), worded
  by `core.router.runner_lacks` / `core.router.runner_lacks_remote`; `core/catalog.js` exports `missingRequirements`
  (a runner's `missing` list classified as `tcmstudio.capability` does it) and `runnerLacks(entry)`. The UI's
  `ui.catalog.readiness.job_service` says needs_runner when only the runner's job service is missing.
- Extras: `whereCall(name, args)`, `placement(x, args?) → {where, reason, message, requires}`, `resolve(name, args)`,
  `storeApprovals(store)`, `normalizeEnvelope`, `synthEnvelope` (async; `where` default `"page"`, `decided_by`,
  `outcome`), `hostsOf(entry, args)`, `hostsDigest(hosts)`, `PLACEMENT_REASONS`.
- Settings (`core/settings.js`): `compute` accepts `remote`. Another text value is kept as saved (a newer version's,
  or one edited by hand) so the router can report it as `unknown_compute`. Only a missing, empty or non-text value
  becomes `auto`.

**Model clients (`core/llm/*`).** `streamChat` also takes `toolChoice` ("none" on the last step); yields an extra
`tool_call_delta {index, name}`; `tool_call` may carry `error` (bad JSON / not an object / cut off at max_tokens) and
`raw`; `done` may carry `model`, `refusal`, `fallback`. Anthropic on api.anthropic.com: the newest models
(`claude-fable-5-1`, `claude-opus-5-5`, `claude-opus-5`, `claude-sonnet-5-5`) are sent `fallbacks: "default"` with
`anthropic-beta: server-side-fallback-2026-07-01`; thinking is `{type:"adaptive", display:"summarized"}` (budget on
Haiku 4.5 and older) and is never sent as `disabled`. **runner-service:** the `/api/llm` proxy must forward
`anthropic-version`, `anthropic-beta`, `anthropic-dangerous-direct-browser-access`, `x-api-key`, `authorization`,
`content-type`, `accept`; `X-TCM-Target` is the full URL (`…/v1/messages`, `…/chat/completions`).

**Providers (`core/providers.js`).** Preset fields beyond §6: `stream_usage`, `thinking_off`, `max_output`, `tools`,
`docs`, `fix` (i18n key of the CORS fix), `custom`. Ids: `tao, openai, anthropic, deepseek, qwen, moonshot, zhipu,
siliconflow, openrouter, ollama, lmstudio, vllm, llamacpp, custom_openai, custom_anthropic` (+ `settings.customProviders`).
All cloud presets answered a CORS preflight from https://science.impf.ai on 2026-10-07 (`cors:"ok"`); local servers are
`needs-config`. `activeProvider(settings, override)` override: `{provider, model, runner:{url, token, connected},
maxOutput}` (`maxOutput` = relay `health.max_output_tokens`); the result also has `needsKey`, `maxTokens`,
`temperature`, `runner` (when routed through it). The relay's base URL: `${location.origin}/v1`, except pages served
from `http://127.0.0.1:8765` / `http://localhost:8765`, which use `https://science.impf.ai/v1`
(`settings.baseUrls.tao` overrides, e.g. for e2e).

**Runtimes (`runtime/index.js`, `runtime/runner.js`).**
- browser-runtime: `createRuntimes` does `new BrowserRuntime({bootUrl, settings, ...opts.browser})` from
  `./browser.js` (`bootUrl` = absolute URL of `/runtime/boot.json`); if the module or constructor fails, an
  `UnavailableRuntime` (status `offline`, every call a `failed/unavailable` envelope) stands in. `loadCatalog` first
  asks `browser.catalog()` for the built catalog document (resolve before Pyodide has loaded; throw or return null if you
  cannot), then falls back to fetching `/runtime/catalog.json`. The router treats a browser runtime with status other
  than `offline` as usable (it starts on the first call).
- Runner: `start({interactive})` — `interactive:true` (a button press) waits up to 60 s, so Chrome's Local Network
  Access prompt can be answered; background probes give up after 3 s. The background probe runs only when the page is
  served by the runner or a token is saved (`shouldAutoConnect`). Status `error` = needs the user (token, LNA denied,
  origin not allowed); `offline` = not reachable. Errors are `RunnerError {code}` with codes `not_running |
  lna_denied | mixed_content | cors | timeout | token | not_runner | http | forbidden`, messages in the UI language;
  `lnaNotice()` is the sentence to show before the first connect.
- **runner-service:** `X-Filename` on `POST /api/uploads` is **percent-encoded UTF-8** (`encodeURIComponent`; decode
  with `urllib.parse.unquote`), because headers cannot carry Chinese file names.
- `#pair=` links are accepted only for a loopback runner URL (a link must not pair the page to another machine);
  `takePairing()` removes the fragment with `history.replaceState`.
- Extras: `kinds()`, `uploads()`, `stop()`, `describe(err)`, exported `parsePairFragment`, `takePairing`,
  `lnaPermission`, `describeConnectError`, `lnaNotice`, `DEFAULT_RUNNER_URL`.

**Store (`core/store.js`).** Extras: `store.persistent` (false = in-memory fallback, say so in the UI), `openError`,
`clearAll()`, `close()`, `messages.path(conversationId, leafId?)`, `files.update(id, patch)` (sha256/bytes/blob are
immutable); `projects.create` also takes `web` and `defaults`; `projects.list({archived:null})` lists all. Exported
helpers for branches: `threadPath(messages, leafId)`, `siblingsOf(messages, id)` (the ‹1/2› switcher),
`latestLeaf(messages, fromId)`, `toolMessagesOf(messages, assistantId)`. Updates are read-modify-write inside one
IndexedDB transaction, so concurrent writers (a debounced message save and a title edit) do not lose fields.
Export format `tcmstudio.project/1` (files base64 with sha256; import re-ids everything, refuses mismatched bytes, and
does not carry over `approvals` or web access: the importing user decides those again).

**Governance and glossary (for web-ui).** `core/glossary.js` holds the code dictionary DESIGN §2.3 asks for: every
`ART/CLM/SKILL/GATE` code with the Python's English text and a zh explanation, `INQ*` with remedies, the PSH compiler
codes (`EVIDENCE*, EFFECT*, FLOW*, PROTOCOL*, RESOURCE*, RETRY*`), tiers, both design and claim-kind vocabularies,
quality labels, the 13 categories. Use `glossary.code(id)` / `glossary.codeRemedy(id)` from `core/i18n.js`.
`core/governance.js` also exports `refusalsOf`, `licensingLadder(kind, weakestTier)` (cells + the §6.4 caption),
`shortHash` (`sha256:0da551c0…626d`), `compositeVersionOf`, `identifierUrl`, `isPredicted`, `qualityOf`,
`sourceView`, `RELEASE_STATES`. `verdictBadge(x, vocabulary?, lang?)` tones: `released, consistent, caveat, refused,
declaration, prediction, allowed, draft, neutral, warning, jade, navy, slate, ochre`.
**runner-core:** the extractors read `governance.artifact` = `ResearchArtifact.document()`, `governance.verdict` =
`ArtifactVerdict.as_dict()`, `governance.claims` / `governance.evidence` = the document's lists, and
`citations[].evidence_ref` = an evidence item id (see `web/test/fixtures/envelope_*.json` for real examples).

**Markdown (`core/markdown.js`).** `renderMarkdown(text, {onCitation, streaming, document, headingOffset})`; `#`
becomes `<h2>` by default. Classes for web-ui: `md-code` (`md-code-head`, `md-code-lang`, `button.md-code-copy
[data-copy-code]` — handle copy by delegation), `md-table` (scroll wrapper, `tabindex=0`), `md-cites` > `md-cite
[data-cite]` (default chip when `onCitation` returns nothing), `md-math` / `md-math-block` with TeX in `data-tex`
(typeset with KaTeX if wanted; the text is the TeX), `md-task` / `md-task-box`, `md-caret`, `md-image-link` (images
are never loaded). `<br>` in model text becomes a line break; all other HTML is shown as text.

**Settings / i18n.** Extras: `updateSettings(patch)`, `clearKeys()`, `detectLang()`; `setLang` also saves
`settings.lang`. With `rememberKeys:false`, keys live in `sessionStorage["tcmstudio.keys"]`.

## web-ui: what the UI assumes about the runtimes, the runner and the stored records

- **Browser runtime status.** `browser.onStatus(fn)` may call `fn` with a status string or with
  `{status, progress?: 0–100, message?, error?}`; the compute panel shows `progress` as the Pyodide loading bar and
  `message` under it. browser-runtime: please emit `{status: "loading", progress, message}` from the worker's
  `{type:"progress"}` messages. `browser.device()` should return the CONTRACTS §5 device report; when it is missing
  or fails, the UI runs its own detection (cores, memory, `crossOriginIsolated`, WebGPU adapter with `software`
  flagged for SwiftShader / llvmpipe / fallback adapters).
- **Runner `/api/info` engines.** Rendered as `{id, name, installed | available, version?, gpu?, missing?: [..],
  install?: "<command>"}`. The install command is shown (with a copy button) only when the runner gives one; the UI
  never invents a package name. Device ids `cpu`, `cuda:N`, `rocm:N`, `mps` are shown as CPU / CUDA:N / ROCm:N / MPS.
  `PUT /api/settings` is sent the full current settings object with the changed field merged in.
- **Job events.** `runner.jobs.events(id, onEvent)` is read as `onEvent({type, data})` where `state` / `done` carry a
  Job (or `{job}`), `progress` carries `{fraction?, message?}`, `log` carries a string or `{line}`. A job card shows
  files only when `state === "succeeded"`.
- **Citation ids are per envelope.** Every envelope numbers its citations from `E1`. The thread resolves `[E#]` against
  the envelopes of the same assistant turn in call order, the later envelope winning, and marks an id no envelope
  has as unknown (dotted chip). Two tools in one turn that both return `E1` are ambiguous to the model as well;
  web-core may want the router to renumber citations across a turn (E1…En) before the text reaches the model.
- **Stored assistant messages carry `ui`.** `{thinkingMs: {step: ms}, approvals: {callId: "once"|"project"|"deny"},
  startedAt, endedAt}`. While a turn streams, a draft assistant record (`draft: true`, `status: "stopped"`) is saved
  every 1.5 s under the id the final record will use; at the end `runTurn`'s assistant is saved under that id and its
  tool messages are re-parented to it, so a reload mid-stream keeps what was said and no stray branch is left.
- **Project compute default.** `project.defaults.compute` (when set) overrides `settings.compute` for that project:
  the UI calls `toolRouter.setSettings({...settings, compute})` on project change.
- **Reproduce command.** The provenance tab copies
  `python3 -c '…from tcmstudio.dispatch import call; print(json.dumps(call("<tool>", <args>, {})…'` (the dispatcher of
  CONTRACTS §5). runner-core: if `tcmstudio call` gets a stable flag syntax, tell web-ui and the command can use it.
- **Math.** Without a third-party typesetter, `$…$` / `$$…$$` from `core/markdown.js` are shown as quiet monospace TeX.
- **Webfonts.** Latin faces (Inter, Source Serif 4, JetBrains Mono) are self-hosted in `web/assets/fonts` (OFL);
  Noto Serif SC (serif headings only) loads by unicode-range slice from jsDelivr (`font-src` allows it).
- **UI tests** live in `web/dev/test/*.test.mjs` (linkedom). web-core / integration: please extend the `test` script
  to `node --test web/test/*.test.mjs web/dev/test/*.test.mjs`.
- **What the build should publish from `web/dev/`.** `dev/gallery.html` (+ `gallery.js`, `gallery-app.js`,
  `gallery.css`, `demo.js`, `fixtures/*.json`) is the design-QA surface and is safe to publish (noindex; it writes
  nothing to the visitor's IndexedDB: the demo uses an in-memory store). `dev/test/` and
  `dev/fixtures/make_fixtures.py` need not be published.
- **Hooks for e2e** (integration): `globalThis.__studio` is the App; stable selectors are `#composer-input`,
  `.composer__send`, `.example`, `.permission .btn--primary|--secondary|--ghost` (once / project / deny),
  `.msg--assistant:not(.is-live)` (a finished answer), `#insp-run|evidence|claims|files|provenance`,
  `.topbar__inspector`. The pairing fragment is taken with `runtime/runner.js` `takePairing()` before routing.

## runner-core: the catalog and the dispatcher as built (additions to §2–§4)

**Python API (one surface, both runtimes).**
- `tcmstudio.dispatch.call(tool, arguments, context) -> envelope` never raises (a `KeyboardInterrupt`, e.g. Pyodide's
  interrupt buffer, gives `status:"cancelled"`). `tool` is a core name, `call_tool`, or an entry id; `arguments` may be a
  dict or JSON text. For the worker: `tcmstudio.dispatch.call_json(tool, arguments_json, context_json) -> str` (JSON in
  and out, nothing else crosses the boundary).
- `context` (a dict or `tcmstudio.dispatch.Context`): `where` ("browser"|"runner"; default from `sys.platform`),
  `network` (bool, or the runner's `{enabled, profile}`), `purpose` ("academic"|"commercial"), `state_root`,
  `project_id`, `conversation_id`, `approvals` (recorded in `receipt.approvals`; Python does not enforce them — the
  router does), `device` (goes to `receipt.device`), `jobs` (object or dict with `submit(kind, params, project_id) ->
  Job` and `get(job_id, wait_s) -> Job`), `tcmdb_root` (data hub root; default `$BIOAGENT_TCMDB`), `durable` (browser:
  false when IDBFS is not working), `capabilities` (host facts — devices, engines, WebGPU — echoed under
  `result.host` of `system.capabilities`). Unknown keys are ignored.
- **`state_root` is the parent of the per-project directories**: `<state_root>/<project_id>/psh` (the project's PSH
  audit chain: every governed run of the project extends it), `/runs/<run_id>/out` (governed-run outputs),
  `/clinic/<session_id>` (clinic sessions). runner-service: pass `<home>/projects`; browser-runtime: `/persist`. Without
  `state_root` a per-process temporary directory is used and `receipt.durable` is false. A project id with characters
  outside `[A-Za-z0-9_.-]` is mapped to a safe name plus a hash. One governed run at a time per audit chain (a lock).
- The chain records runs under the skill id (bioagent's `run_id`); `receipt.run_anchor` (the chain head when the run
  started) and `receipt.run_id` (our output folder) tell runs of one project apart.
- `tcmstudio.catalog.build_catalog(where, probe)`, `catalog_search(query, category, kind, runnable_now, limit, where,
  network=, jobs=)`, `get_entry(id)`, `JOB_KINDS`, `NEVER_OFFERED`.

**Catalog document additions.** Top level: `where`, `counts {core, entries, by_kind}`, and `problems` only if a source
section failed to load. Core tool `parameters` have `additionalProperties:false` (a misspelt argument gets a hint).
`maps_to` is informational: `"connector.*"` (connector_call), `"job.pipeline.*"` (run_pipeline), `null` (call_tool);
`literature_search` carries `hosts`. Entry fields beyond §2: `hosts` (network entries), `network_if:"allow_remote"`
(the entry reaches `hosts` only when that argument is true: `job.pipeline.fold|dock`, `skill.predict-protein-structure`,
`skill.dock-ligands`; their `network` is false), `pyodide_packages` (see below), `optional` (optional engines) and on
the runner `optional_present`, `available`/`missing` (runner + probe only; never in the browser catalog), `doc` (full
docstring, native), `domain`, `data` + `needs` (the three hk* lookups, which run in both runtimes on a store built or
imported there; `data: ["*"]` on the tcmdb queries that read any built store), `data_from {param}` (tcmdb.tables,
query, unresolved: the `dataset` argument names the store), `connector {key, name, operation, method, host, license,
transport, rate_note, docs, domain}`, `skill {…§2, risk, min_autonomy, set}`, `phi:true` (clinic entries that carry
patient data), `duration` (jobs), and on every entry `implementation`, `state`, `verification` and `approvals`
(CONTRACTS §2; with `execution.engines`, `hosts_by` and per-entry `limits` where they apply; the document adds
`limits` and `verification_records`). 650 entries: native 150, skill 11, connector 444, clinic 5
(template/assess/check/followup/verify), tcmdb 15, study 4, job 11, system 10. `clinic.sign`, `clinic.agreement`,
registry release/promotion and any shell are not entries; called by name they are `refused` with code `HUMAN_ONLY`.

**browser-runtime.**
- Before `call`, load the Pyodide packages the entry lists in `pyodide_packages`: `clinic.assess` and
  `skill.draft-tcm-prescription` need `numpy` **and** `scipy` (the session report is written by the omics report code);
  `study.contrast_formula_monomer|combination|effect_modification` need `numpy`. Without them the call returns
  `failed/unavailable` naming the package (never a partial result). Everything else in the browser catalog needs only
  pyyaml (+ packaging).
- Measured in Node + Pyodide 314.0.7 with the source-tree bundle plus `studio/runner/src` on `sys.path`:
  `import tcmstudio.dispatch` 0.3 s; first native call 0.56 s (builds the kernel runtime and the native section), then
  ~1 ms; each P0 governed run 1.3–1.6 s, `released:true`, content hashes equal to the lockfile; `clinic.assess` with
  numpy+scipy 7 s on first use; one thread throughout. Building the whole catalog in Pyodide takes ~2.5 s — use the
  build-time `catalog.json`; the dispatcher builds only the section an entry belongs to.
- Versions without installed metadata come from `__version__`, then the package's `pyproject.toml`
  (`receipt.versions.tcmstudio` is `0.1.0` from the bundle). `receipt.runtime` is `pyodide-314.0.7 / CPython 3.14.2`.

**runner-service.**
- Job params are `JOB_KINDS[kind]["parameters"]` (importable data; reuse it for `/api/kinds` validation). Files are
  upload ids (or runner-allowed paths) for the runner to resolve. `network_pharmacology_run` submits `research.run` with
  `{question: "<formula>的实测靶点集中在哪些通路？", formula, disease?, background?, hits?, permutations?, activity?,
  optional?}`. Candidate skills that are heavy (`dock-ligands`, `predict-admet`, `predict-protein-structure`,
  `rnaseq-differential-expression`, `scrna-cell-atlas`, `retrieve-literature-evidence`) submit `skill.run` with
  `{skill_id, arguments, allow_unpinned}`. `tcmdb.fetch` has no `confirm` (above the size gate is the user's act in
  the UI). `submit` raising `ValueError`/`KeyError`/`TypeError` → `bad_arguments`; a Job returned in state `failed` →
  `status:"failed"`. `job_status` passes `wait_s` (0–60) to `get`.
- `system.capabilities` merges `context.capabilities` as `result.host`; `system.doctor` runs `bioagent doctor` (≈5 s).
- The process-global content store of bioagent (`contracts.receipts.default_store`) grows with every governed run;
  restart a long-lived runner now and then (or run governed calls in a worker process).

**Envelope additions (§3).** `governance.label` = PSH's advisory label of the model view (`{sensitivity, categories,
classifier, shareable, permits:{DESTINATION: bool}, advisory:true}`; `labels` is `[sensitivity]`), never enforced.
`governance.policy` = the policy kernel's rulings for native and connector calls. Refusals may carry `claim_id` and
`claim`; codes are the kernel's (`CLM…`, `ART…`), policy rule ids (`perm.network.denied`, `usage.*`, `license.*`), and
`HUMAN_ONLY`, `UNPINNED` (a candidate skill's development run: recorded, never released), `GovernedRunRefused`
(web-core: zh labels for these in the glossary). `governance.outputs[].content` is parsed JSON (≤ 512 KB) or text for
`text/*` other than HTML (≤ 64 KB), else null; `missing`/`hash_mismatch` flag problems. `receipt` extras: `profile`,
`purpose`, `project_id`, `approvals`, `durable`, `run_id`, `run_anchor`, `skill_pinned`, `session_id`, `kernel`,
`adapter`, `request {url, method, host, http_status, cached, attempts, fetched_at}` (connectors); `versions` also has
`python`. Citations: evidence items first (`evidence_ref` = item id), then identifiers found in the result, one per
record (PMID preferred over its DOI), classical passages labelled `《书名》篇章`; at most 40. Harmless argument slips
(a numeral as a string, one value for a list, `null` for an optional argument) are repaired and reported as
`Note:` lines in `text`.

**CLI (web-ui's reproduce command).** `tcmstudio call TOOL --args JSON|@FILE|- [--where browser|runner] [--no-network]
[--purpose academic|commercial] [--project ID] [--state-root DIR] [--tcmdb-root DIR] [--compact|--text]` prints the
envelope (exit 0 for succeeded/job_submitted, 1 otherwise). `tcmstudio catalog [--where] [--out FILE] [--no-probe]
[--indent N]`. `tcmstudio serve|webbuild …` hand every following argument to the module's own parser.

## runner-service: the runner as built (additions to §4)

**Errors.** Always `{error:{type, message, hint?, …}}`. 400 `bad_arguments` | `bad_request`; 401 `token` (body also
has `token_required:true`); 403 `forbidden_origin`, `forbidden_target` (model proxy); 404 `not_found`; 405
`method_not_allowed` (`error.allow`); 409 `conflict` (cancel of a finished job; `error.job`); 411 `length_required`
(uploads need a Content-Length); 413 `too_large`; 421 `forbidden_host` (DNS rebinding); 422 job refusals,
`unavailable` (with `error.missing`) or `network_off`; 502 `upstream_unreachable`; 503 `unavailable` (`/runtime/*`
cannot be made); 500 `runner_error`. CORS headers are sent on every answer to an allowed origin, errors included.

**Health and pairing.** `GET /api/health` → `paired` is true when this request carries a valid token (or none is
needed), so a page can check its saved token without a 401. The pairing link's `url` is `http://127.0.0.1:<port>`
(the banner also prints `http://127.0.0.1:<port>/#pair=…` for the app served by the runner). A non-loopback bind is
paired by address and token. `--port 0` lets the system pick a port (the banner shows it).

**Settings.** Defaults: `device:"auto"`, `threads: min(4, cores)`, `max_jobs: 1` (2 on ≥ 8 cores),
`network:{enabled:true, profile:"biomedical-research"}`, `purpose:"academic"`, `allow_remote:false`. PUT merges, ignores
unknown keys, accepts `network: true|false` as the enabled switch, and refuses a bad value with 400 (nothing changed).
`--device/--threads/--max-jobs/--network|--no-network` are applied and saved. A `runner.json` of schema
`tcmstudio.runner/1` (network off by default then) is read once with network on, unless its profile is
`offline-analysis`, and the banner says so. Two switches gate the network: the project's web
access (the router) and the runner's own `settings.network` (passed to the dispatcher as `context.network`). When the
runner's is off, a `network_off` envelope's `error.hint` says so first.

**`allow_remote`** (runner setting) gates jobs that send the user's data to a third party: fold with
`allow_remote` (esmatlas, colabfold, `PDB:`/`UniProt:` references), dock with a remote receptor or `site_ligand`, and
`skill.run` of a skill whose `network_if` argument is set → 422 `unavailable` while it is off. fold `esmatlas`/`colabfold`
without `allow_remote:true` in the params is 400 (it cannot run locally).

**Files in job parameters.** A file parameter is an upload id (`u_` + 16 hex), the file name of an upload (the newest;
uploads sent with `X-Project-Id` or `?project_id=` are preferred for that project), or an absolute path under the
runner's home, `BIOAGENT_DATA_LAKE`, `BIOAGENT_TCMDB` or `BIOAGENT_WORKSPACE`. Sample sheets are rewritten into the
job's `inputs/` with every file column resolved (rnaseq `fastq_1/fastq_2`, scrna `path`), so the sheet may name its
FASTQs by their uploaded file names. Any string containing `{output}` is refused. `Job.inputs` records what each file
parameter resolved to (`{upload, name, sha256, bytes}` or `{path}`).

**Job** (beyond §4): `title {zh,en}`, `submission_id`, `inputs`, `command` (the argv, set when it starts),
`device_note` (when the selected device could not be used), `artefacts[].path`, and `result` once `succeeded`: the
pipeline's own `--json` summary (rnaseq, scrna, fold, research), the first rows of `scores.tsv` / `admet.tsv` (dock,
admet), the hub status (tcmdb.*), or for `skill.run` `{status, summary, released, text, content_hash, audit_head,
envelope:"envelope.json"}`. `outcome`: `{status: succeeded|failed|timeout|cancelled|…, error?, problems?, exit_code?,
timed_out?, note?}`. `progress` only while running. `GET /api/jobs/{id}?wait_s=N` (≤ 60) waits for a state change.
`GET /api/jobs/{id}/files` → `{files, state}`; file links take `?download=1` (attachment). Job files carry
`Cross-Origin-Resource-Policy: cross-origin`; HTML/SVG also `Content-Security-Policy: sandbox` and COEP.
POST `/api/jobs` answers 201 for a new job and 200 for an existing `submission_id`.

**Events** (`/api/jobs/{id}/events`): `retry: 3000`, then `state` (Job), the recent `log` lines, `progress` if any;
live: `state` (Job), `log` `{stream:"stdout"|"stderr", line}`, `progress` `{fraction?, message?, stage?, bytes?,
files?, verified?}` (the whole `Job.progress` of §4; `{}` when cleared; `stage` is a pipeline's stage from its
`stages.json`, or `"verifying"` while the job is collected; `bytes`, `files` and `verified` a `tcmdb.fetch`'s),
`stages` `{stages}` (`Job.stages`, each time a running pipeline's `stages.json` moves on, so a card's stage list follows
it; the page's `JobTracker` merges it), `artefact` `{path, bytes, media_type}` (a new file in the output directory, not
yet verified), `done` (Job; the stream then closes); `: keep-alive` comments every 15 s. A finished job's stream is
`state` + `done` at once.

**Kinds.** `research.run` reads `<data lake>/snapshots` and the ledger `<data lake>/snapshots/ledger.jsonl` (missing →
`available:false`, `missing:["snapshots (…)"]`, also in `/api/catalog`'s `job.research.run`); its state directory is
`<home>/projects/<project>/research/<job id>`; exit 1 (release not authorised) is `succeeded` with `outcome.note` — a
result, not a failure; exit 3 (a workflow stage FAILed its own check) is `failed`, with the stage named in `problems`.
Its `formula` parameter is passed as `--formula` (it was validated and then dropped); a question naming another formula
is refused with `bad_arguments` before the job is queued when the runner can tell without the formula workbook (the
question names a hand-checked formula), and by the loop otherwise. Required on both exits, beside `artifact.json` and `result.json`: the nine exports
`compounds.tsv`, `targets.tsv`, `disease_targets.tsv`, `enrichment.tsv`, `network.tsv`, `provenance.json`,
`run_manifest.json` (`tcmscience.run-manifest/1`), `limitations.md`, `report.html` (artefact names `compounds`, `targets`,
`disease_targets`, `enrichment`, `network`, `provenance`, `manifest`, `limitations`, `report`). The `manifest` validator
re-reads every digest the manifest records, `artifact.json`'s included (a symlink or a non-regular file is refused,
never opened), and fails the job for any stage the manifest lists as `FAIL`; `out/stages.json` carries the sixteen
workflow stages (job `stages`; a stage that had nothing to test is `not_applicable`, so such a run is not `complete`),
and the result (the command's summary) adds `formula`, `coverage`, `stages`, `exports`, `complete` and `problems`.
`skill.run` runs `python -I -m tcmstudio.kinds skill-run`, which calls the dispatcher in the job's
own process (with the entry's `job` flag switched off there) on `<home>/projects` and writes `out/envelope.json` and
`out/outputs/…`; a refused governed run fails the job (`refused: …`). A candidate skill needs `allow_unpinned:true`.
tcmdb jobs are verified against the hub's status after they exit. GPU-capable: fold with `esmfold`, and `skill.run`
of `predict-protein-structure`; at most one job uses a GPU at a time; CPU jobs get `CUDA_VISIBLE_DEVICES=""`.
Jobs inherit only bioagent's job environment plus `BIOAGENT_*`, the thread variables, the device variables, and the
proxy and CA variables when the kind needs the network. On Windows (no POSIX process groups) jobs are unavailable
(`/api/info` → `jobs.available:false` with a note) and calls still work.
**runner-core:** (1) please keep `_exec_skill` keyed on `entry["job"]` (or add a context flag to run a job entry
inline); the skill.run worker relies on it. (2) `JobsHook.submit` raises `JobRefused` (a `RuntimeError` with `.type`
`unavailable|network_off` and `.hint`); `_submit_job` now reports it as `runtime_error` "the job could not be
submitted: …" — mapping an exception's `.type`/`.hint` would give the model the precise error type.

**`/api/info`** also has `url`, `token_required`, `web` (whether it serves the app), `jobs {queued, running, succeeded,
failed, cancelled, available, note?}`, `paths {data_lake, tcmdb, workspace, uploads, projects}`, `notes`, `cpu.arch`.
`engines[]`: `{id, name, installed, gpu, used_by:[kind…], version?, missing?, install?, note?}` (ids `omics-builtin,
pydeseq2, salmon, kallisto, hisat2, fastp, scanpy, scvi, esmfold, colabfold, vina, admet, pyarrow`; `install` only
where there is a standard command). `/api/devices` also has `resolved` (the device jobs would use now),
`engine_options` (Boltz `accelerator`, Chai-1 `device`, OpenMM `platform` for it), `usage {memory_available_gb, load_1m,
gpus:{id:{memory_used_gb, utilization}}}`, `torch`, `torch_probe`, `notes`; `?refresh=1` probes again. PyTorch is probed
in a child interpreter, in the background, never imported into the runner. Uploads also have `GET/DELETE
/api/uploads/{id}`; the same bytes under the same name are stored once (200 instead of 201).

**`/runtime/*`** is served from `<web>/runtime/` when the served web directory has one (`--web studio/_site`), else made
in memory by `tcmstudio.webbuild.runtime_file(name)` (browser-runtime's function; the first request takes a few
seconds). Without webbuild, `catalog.json` is still served (the browser catalog) and the rest is 503 with how to build.
Static files carry COOP `same-origin`, COEP `require-corp`, CORP `cross-origin`; a navigation to an unknown path gets
`index.html`. **web-core:** the app served by a runner on a port other than 8765 asks the runner for `/v1/health` (404):
a loopback-served page could always use `https://science.impf.ai/v1` (the relay still decides by its origin list).

**Model proxy.** Forwards `content-type, accept, authorization, x-api-key, api-key, anthropic-version, anthropic-beta,
anthropic-dangerous-direct-browser-access, http-referer, x-title, openai-organization, openai-project`; never cookies,
`Origin`, or the runner's token. Passes back the status, `content-type`, `retry-after`, `x-request-id`, `request-id`,
`x-ratelimit-remaining-*`, chunked as it arrives; an upstream error keeps its status and body. Targets: loopback (any
port), `https://` on 443 for `api.openai.com, api.anthropic.com, api.deepseek.com, dashscope(-intl).aliyuncs.com,
api.moonshot.cn|ai, open.bigmodel.cn, api.siliconflow.cn|com, openrouter.ai`, and `--allow-host` (`host` = https:443, or
`scheme://host:port`). The system proxy variables are honoured for remote targets (verified live through this
environment's proxy: OpenAI and Anthropic answered 401 to a fake key); loopback is always direct. The upstream is closed
as soon as the page drops the connection. `/api/llm/local` entries also have `label`, `error` ("not running" when the
port refuses) and `probed`; probes run in parallel with a 1.5 s timeout.

**Logging.** Method, path and status of API requests that change something, and of every error; never a query string
(it may hold the token) or a body. `--quiet` turns it off.

**Restarts and one runner per home.** Each job keeps `request.json` (and `outcome.json` once finished) beside
bioagent's `job.json`; on start the runner re-attaches to jobs whose supervisors kept running, collects those that
finished meanwhile, queues again those that never started, and also follows any job the controller's trace
(`open_jobs`) still holds open without a record. A second runner on the same `--home` refuses to start (exit 1;
`<home>/runner.lock`), since two would start and collect the same jobs. integration: give each e2e runner its own
`--home` (and `--port 0` if you like; the banner prints the port).

**Datasets (`/api/datasets`).** Added: the dataset list, a dataset's manifest and readiness, and acquisitions
(`POST /api/datasets/acquisitions` → a `tcmdb.fetch` job; `GET` one or all; `DELETE`, or `POST …/{id}/cancel`, to
cancel). The spec's `POST …/cancel` is accepted as written and is the same as the `DELETE`. Errors are §3's: 400
`bad_arguments` (any field but `dataset, include_optional, project_id, submission_id`, so never a URL or a size-gate
confirmation; an unregistered or non-downloadable key), 404 `not_found` (an unknown key; a job that is not a
`tcmdb.fetch`), 409 `conflict` (cancelling a finished acquisition; a dataset already being acquired; a
`submission_id` that names another job; `error.job` names it), 422 `network_off`. A `tcmdb.fetch` job's `progress` carries `bytes`, `files` and
`verified` from the files on disk, and keeps them while it is collected (`stage:"verifying"`). Not added: object
storage (R2) or signed-URL sharing of downloaded files.

## browser-runtime: the site build and the in-browser runtime as built (additions to §5, §6, §10)

**Build.** `python3 studio/scripts/build_web.py --out DIR [--dev] [--web DIR] [--pyodide-index-url URL] [--json]` is
`tcmstudio webbuild …` (exit 2 with the reason on failure). The site: `studio/web` without `test/` and `dev/` (both
published with `--dev`), without a stray `web/runtime/`, dotfiles, `__pycache__` or `node_modules`; plus
`runtime/{boot.json, catalog.json, tcms-py.<sha12>.tar.gz}` and `_headers` (copied from `studio/edge/_headers`). It is
assembled in a sibling directory and swapped in; a non-empty `--out` that does not hold an earlier build
(`runtime/boot.json` + `index.html`) is refused, never deleted. **web-ui:** `dev/gallery.html` is therefore not on
science.impf.ai unless the workflow passes `--dev` (which also publishes `/test/runtime/`, a noindex check page that
writes its own test projects into the visitor's IndexedDB) — the lead's call.

**Bundle.** Laid out as installed packages at the archive root: `bioagent/` (with `bioagent/_bundled/{skills,registry}`
copied from the checkout exactly as bioagent's `setup.py` does for a wheel; `data/unified_capability_catalogue.csv`
left out, nothing in the browser reads it), `psh/`, `tcmstudio/` (without a packaged `tcmstudio/web/`), and
`<dist>-<version>.dist-info/{METADATA, INSTALLER, top_level.txt}` so `importlib.metadata` and bioagent's environment
record give the real versions. Files per package come from `git ls-files --cached --others --exclude-standard` (a clean
checkout, such as CI's, ships exactly the tracked tree; a working copy ships what it would commit), else a directory
walk (an installed wheel). Sorted entries, mtime 2020-01-01, uid/gid 0, mode 0644, gzip mtime 0 without a name: the
same sources and the same zlib give the same bytes and the same name. The worker unpacks it to `/opt/tcms/site` (the
one `sys.path` entry); `bioagent.config.SOURCE_TREE` is then false and skills load from `_bundled`, with the lockfile's
content hashes (checked natively by `runner/tests/test_webbuild.py` — browser-runtime's test file — and in Chromium).

**`boot.json`** `{schema:"tcmstudio.boot/1", pyodide:{version, index_url, packages:["pyyaml","packaging","sqlite3"]},
bundle:{path, sha256, bytes, format:"gztar", files, extract_dir, python_path}, catalog:"runtime/catalog.json",
catalog_sha256, state_root:"/persist", versions}`. Paths are relative to the site root (the directory above
`runtime/`). `index_url` may be site-relative for a self-hosted Pyodide (`--pyodide-index-url`, or
`$TCMSTUDIO_PYODIDE_INDEX_URL`). The worker loads only the names its Pyodide lockfile lists (`sqlite3` is in 314's
standard library) and then imports sqlite3. `catalog.json` = `tcmstudio catalog --where browser --no-probe`.

**runner-service:** `tcmstudio.webbuild.runtime_file(name) -> (bytes, media_type) | None` (`boot.json`,
`catalog.json`, `tcms-py.<sha12>.tar.gz`, with or without `runtime/`; None for anything else, a stale bundle name
included); built once per process (≈ 1.5 s), thread-safe; `runtime_files()`, `clear_cache()`, `default_web_dir()`. The
bundle may be cached as immutable; the other two need revalidation. The runner-served page still loads Pyodide from
jsDelivr (needs internet) unless `$TCMSTUDIO_PYODIDE_INDEX_URL` points elsewhere.

**Worker protocol (beyond §5).** All messages are JSON strings except `{type:"interrupt-buffer", buffer}` (a
SharedArrayBuffer, posted before `init` when the page is cross-origin isolated). Ops: `init {boot, siteUrl, indexUrl?,
persist, debug}` → `{runtime, versions, platform, python_path, pyodide{version, index_url}, packages, persist{mode:
"idbfs"|"memory", durable, error, root}, interruptible, cross_origin_isolated, bundle{path, sha256, bytes, from_cache},
pycache{restored}, timings{pyodide, bundle (both from boot start), packages, unpack, persist, pycache, import},
boot_ms}`; `call {tool, arguments, context, packages, stateful}` → `{id, ok, ms, persist{mode, durable, error?},
result}` where `result` is the envelope text exactly as `call_json` wrote it; `info`; `device`; `warm` (one
`native.gc_content` call that builds the native section and the kernel runtime; writes nothing); `debug
{action:"fatal"}` only after `init` with `debug:true`. Errors: `{id, ok:false, ms, fatal, error{type, message}}` with
types `bundle_hash | bundle_missing | bundle_http | bundle_network | bad_boot | unavailable` (an on-demand package)
`| not_ready | fatal | runtime_error`. Progress: `{type:"progress", progress, stage, vars, message}`, stages `pyodide,
pyodide_ready, bundle, packages, unpack, persist, import, packages_on_demand, ready`. The worker adds `where:"browser"`,
`state_root:"/persist"` and `durable` to the context. After a fatal error every op answers `fatal`.

**Persistence and caches.** `/persist` is IDBFS (IndexedDB database `/persist`), proven by a write at boot, else
memory (`durable:false`). Calls whose entry is `skill`, `clinic` or `system.audit_verify` run under the Web Lock
`tcmstudio.persist` with `syncfs(true)` before and `syncfs(false)` after, so two tabs extend one chain instead of
overwriting each other; a failed sync sets `receipt.durable:false` and `receipt.persist_error`. Cache Storage
`tcmstudio-runtime-v1` holds the bundle (re-verified by SHA-256 on every boot; older bundles pruned) and the compiled
modules: `sys.pycache_prefix = "/pycache"`, saved after a call that imported new modules (after its reply) under the
key `runtime/.pycache/<sha16>-pyodide-<version>.tar` (never fetched; its own SHA-256 in a header) and restored before
the imports — about a second off the first call of a later visit. numpy/scipy are loaded on demand from the entry's
`pyodide_packages`; scipy gets `scipy.io._fast_matrix_market.PARALLELISM = 1`.

**BrowserRuntime (beyond §6).** Options `{bootUrl, siteUrl?, workerUrl?, indexUrl?, persist:true, warm:true,
callTimeoutMs:600000, bootTimeoutMs:180000, interruptGraceMs:3000, debug:false, Worker?, fetch?}` (`settings` is
accepted and unused). Status events are `{status, progress, message, error}`; messages come from `registerStrings` keys
`runtime.browser.*` (zh/en). `idle → loading → ready`; `error` = the boot failed (an explicit `start()` retries; a call
retries too, unless the failure would repeat: bundle hash mismatch, bad boot.json, three restarts within 60 s);
`offline` = no Worker/WebAssembly, or `runtime/boot.json` is 404. `catalog()`, `device()` (exported `detectDevice()`,
on the page) and `info()` never start Python. Extras: `stop()`, `interruptible`, `debug(action)` (test pages). `call()`
resolves the entry from the catalog (core → `maps_to`; `call_tool` → `arguments.tool`) for `packages` and `stateful`;
only `capabilities_status` gets `context.capabilities = {browser:{device, interruptible, durable, packages}}`. One call
at a time; aborted while queued → `cancelled`, never run; running and isolated → SIGINT, Python's own `cancelled`
envelope, terminate + restart if Python has not stopped after `interruptGraceMs`; not isolated → terminate + restart.
Fatal → `failed/runtime_error` + restart; past `callTimeoutMs` → `failed/timeout` + restart; a non-fatal worker failure
(e.g. numpy could not be downloaded) → `failed/unavailable` or `runtime_error`. Envelopes made in JS carry
`receipt.decided_by:"browser-runtime"` and `input_sha256`.

**Tests.** `web/test/runtime/browser.test.mjs` (scripted Worker). **web-core:** please extend the `test` script to
`node --test web/test/*.test.mjs web/test/runtime/*.test.mjs` (with web-ui's `web/dev/test/*.test.mjs`).
`web/test/runtime/run.mjs [--site DIR] [--chromium PATH] [--pyodide-dir DIR] [--modes full,reload,nonisolated,tamper]
[--sci] [--json FILE]` builds the site (`--dev`) unless given one, serves it on ephemeral ports with the site's own
`_headers` (and once without COOP/COEP), runs `test/runtime/index.html` in Chromium and compares the envelopes with
`python3 -m tcmstudio call … --where browser`; exit 0 = all passed. **integration:** usable as `test:e2e` (needs
jsDelivr, or `--pyodide-dir` with a local Pyodide 314.0.7 dist). Measured (headless Chromium 141, 4 vCPU, jsDelivr):
ready in 4.9 s cold / 4.4 s on reload; first call 2.7 s cold (includes the warm-up) / 1.7 s with restored pycs;
governed P0 runs 1.2–2.4 s, released, hashes = native; native tools 3–8 ms; cancel by interrupt 0.6 s with no restart;
`clinic_assess` with numpy+scipy loaded on first use 9.9 s.

**Job refusals through `/api/call`.** When the job service refuses a submission (`unavailable` with `missing`, or
`network_off`), the runner restates the dispatcher's `runtime_error` envelope as that refusal: `error {type, message,
hint, missing?}`, the summary (`…：此处不可运行（…）` / `…：未联网（…）`) and the `Error (…)` / `Remedy:` lines of `text`.
runner-core: if `_submit_job` mapped an exception's `.type`/`.hint` itself, this restatement becomes a no-op (it only
touches `runtime_error`). `/api/kinds` reports `pipeline.scrna` with `gpu:false` (bioagent runs scVI on the CPU by
design); the catalog's `job.pipeline.scrna` says `gpu:true` — runner-core may want to align it.

## browser-compute: local backends in the Worker (additions to §3 and §5)

How the browser runtime computes faster than Pyodide alone, and what it reports. The design, the rules and the
measurements are in [BROWSER_COMPUTE.md](BROWSER_COMPUTE.md).

**`receipt.compute`** is on the envelopes of the tools that have a local backend (`native.distance_matrix`,
`hamming_distance`, `gc_content`, `global_alignment`, `local_alignment`, `protein_alignment`, `edit_distance`) when they
run in the browser:

```jsonc
"compute": {
  "backend": "webgpu" | "wasm-simd" | "js" | "pyodide",  // what produced the numbers (decided in Python, after its checks)
  "operation": "native.distance_matrix",
  "precision": "exact integer counts (WebAssembly SIMD, CPU); Python float64 transforms",
  "kernels": [{"kernel": "sequence.counts", "outcome": "used" | "declined" | "rejected",
               "reason"?: "…", "backend"?: "wasm-simd" | "js", "ms"?: 1.234}],
  "gpu_rejected"?: "why a prepared GPU packet was not used",
  "requested": "auto" | "cpu" | "reference",               // Settings → Compute → Browser acceleration
  "plan"?: {"considered": ["wasm-simd", "webgpu", "js", "python"], "passed_over": [{"id", "reason"}],
            "tried": [{"id", "outcome", "reason"?}], "chosen": "wasm-simd" | null},
  "fallback_reason"?: "…",                                  // why the GPU did not compute (any reason; see below)
  "gpu"?: {                                                 // the three count tools (compute/accelerator.js settle)
    "adapter": {"kind": "software" | "physical" | "unidentified", "vendor", "architecture"} | null,
    "dispatched": true, "validated": true,                  // the call's kernel was submitted; Python used its counts
    "verified": true | false | null,                        // the device's known-answer test (null: not run)
    "gpu_ms": 12.3 | null, "cold": false,                   // this call's GPU path, set-up included when cold
    "kernel_ms"?: 0.4,                                      // GPU timestamps, only where asked for (benchmark)
    "cpu_baseline_ms": 20.1 | null, "cpu_baseline_backend": "wasm-simd" | "js" | null,  // measured here, same tool and size class
    "accelerated": false,                                   // true only from that measurement, on a physical adapter
    "basis": "…",                                           // why accelerated is what it is
    "fallback_reason": "…" | null,                          // why the GPU's counts were not used
    "evidence": "selected" | "reported" | "dispatched" | "validated" | "verified" | "accelerated" | null,
    "handoff_ms": 0.8                                       // written by Python: the crossing from the Worker's sent_at mark, parsing and binding the packet
  }
}
```

`backend` is the fact and `plan` the scheduler's prediction made before the call (for a sequence tool). They can
differ: the plan may choose a CPU kernel that Python then finds not worth calling for so small an input. `receipt.device`
is `webgpu` only when GPU counts were used (§3). A failed call earns no accelerator credit: its `backend` is `pyodide`,
and it has no `gpu`. Python writes `gpu: {validated, handoff_ms}` whenever a packet arrived; the Worker completes it
after Python returns (`decorateReceipt`), so none of it is in the result or its hashes. `receipt.execution.kernel`
mirrors `compute.backend` (Python's `_mirror`, and the Worker whenever it writes `compute`); `execution.device.requested`
is `compute.requested`; `execution.device.evidence` is `validated` when Python used GPU counts (its own recount), raised
by the Worker to `verified` or `accelerated` only as far as `gpu.evidence` shows, and the CPU's stays `selected`. The
ladder, the adapter classifier and the like-for-like timing are in BROWSER_COMPUTE §5.1. `result.fallbacks` gains a
`{what: "compute", reason}` entry only for a degradation (`accelerator.gpuDegradation`): the GPU was tried and failed, set
aside after a failure, or its counts were rejected by Python. A CPU path chosen by preference, size, input shape, a
cheaper estimate or a software adapter has `compute.fallback_reason` only. With a packet, the Worker's `options_json` for
`browser_compute.call_json` adds `sent_at` (its `performance.now()` just before the call) so Python can count the
crossing in `handoff_ms`; it never affects the result.

**Device report** (§5) additions: `python {device:"cpu", threads:1, note}` and `compute {webgpu_tools, webgpu_adapter
(true only for a physical adapter, by `classifyAdapter`, with `webgpu_adapter_kind`: CONTRACTS §5, BROWSER_COMPUTE
§5.1), cpu_kernels {"wasm-simd": [tools], js: [tools]}, precision, fallback}`. The Worker's
own device report (`op: "device"`) classifies its adapter with `classifyAdapter` (`adapter.kind`, `reason`, `device`;
`software` true / false / null as in the probe) and adds `compute_evidence`, the session's `webgpuEvidence`.
`system.capabilities` also carries `browser.compute`, the probe of `compute/capabilities.js` (schema
`tcmstudio.capabilities/1`: WebAssembly features, WebGPU adapter, features and limits, WebNN contexts as requested and
reported, storage, timer resolution). Additive in that schema (it stays `/1`): `webgpu.adapter` is `classifyAdapter`'s
answer `{kind, vendor, architecture, device, description, fallback, reason}`; `webgpu.software` is `true` / `false` /
`null` (unidentified, never "not software"); `webgpu.evidence` is `{ladder, level, api, adapter_reported,
adapter_kind, dispatched, validated, rejected, verified, accelerated, reason}` (the probe alone reaches `reported`; the
counts are `null` without a session); `webnn` adds `context_created`, `device_type_requested` and `npu: {status:
"unverified", label_en, label_zh, reason}`, whose label is "WebNN detected; NPU execution unverified" (检测到 WebNN；NPU
执行未经验证) with the API and "No verified NPU backend is available" (没有可用的已验证 NPU 后端) without it. Nothing
reports an NPU as used. When WebNN is not probed (`probeCapabilities({webnn: false})`), `npu.reason` says "WebNN was not
probed", never that the API is missing.

**Runner devices.** Each entry of the runner's `devices` carries `evidence: "reported"` and `reported_by`
(`operating system`, `nvidia-smi`, `rocm-smi`, `platform (Apple Silicon)`, `PyTorch <version>`): nothing in the runner
runs a kernel on a device. `DeviceProbe.facts()` adds `accelerator_runtimes: [{id: openvino | onnxruntime | coreml |
directml, name, module, installed, status: not_installed | detected | probing | probe_failed, label ("detected, not used
by any tool"), version?, reported?: {devices | providers | device | compute_units}, evidence: "reported" | null,
execution: "unverified", used_by: [], error?}]`, detection only, never part of `devices`, so `resolve()` cannot place a
job on it. (The runner's `/devices` and host facts pass on `devices` only; carrying `accelerator_runtimes` there is a
server change still to make.)

**The complete formula workbook.** `tcm_formula` and `tcm_lookup` add `repository_records` (the workbook's source
versions); `tcm_herb` adds `repository_formulas` `{herb: {term, searched, field, not_within, materia?}, total, count,
offset, next_offset, corpus_rows, records: [{id, row, name, source, composition}], scope, more?: {tool, arguments}}`
(formulas named after the herb first, then workbook order) and, for a seed herb, `formulas_scope`; `tcm_syndrome` adds
`repository_formulas` `{indication: {terms, field, not_searched}, total, count, offset, next_offset, corpus_rows, records:
[{id, row, name, source, actions}], scope, more?}`, the workbook formulas whose recorded 功效 (`actions`) mentions the
syndrome's names without their closing 证 (脾胃气虚证 as 脾胃气虚; never matched in the 注意 `cautions`), beside the seed's
curated `formulas`; a syndrome outside the seed stays `not_found`, its summary and hint saying how many workbook formulas
mention it and how to list them; `tcm_compatibility`
adds `expanded_formulas` `[{formula, row, name, source, composition, herbs}]` for a formula outside the seed that the
workbook holds once and `ambiguous_formulas` `[{formula, versions, hint}]` for one it holds several times (a name the
materia table knows is checked as that herb, never as a workbook formula of the same name); `tcmdb.formulas` takes
`herb` and `indication` (space-separated terms, any of them in the 功效 text). When the workbook cannot be read,
`repository_records` (`tcm_formula`, `tcm_lookup`) or `repository_formulas` (`tcm_herb`, `tcm_syndrome`,
`tcm_compatibility`) is `{status: "unavailable", reason, fix}`, the first limitation says only the seed was searched,
and the summary says so. `system.capabilities.formulas`
is `{seed: {formulas, scope}, workbook: {status: "loaded" | "available" | "on_demand" | "unavailable", rows?, fix?}}`.
In the browser the Worker loads the asset before the five lookups that always read it, and otherwise when
`tcmstudio.repository_data.take_formula_store_wanted()` reports, after a call, that the call wanted it: the Worker loads
it and runs the call once more (only calls that do not change state).

**The kernel boundary.** At boot the Worker calls `tcmstudio.browser_compute.install_js_kernels(js)` with
`compute/bridge.js pythonKernels()`: one method, `call(name, payload_json) -> result_json`, returning `"null"` to
decline. `sequence.counts` → `{counts: [u32 …], backend}`; `align.nw_linear` / `align.sw_linear` →
`{aligned_x, aligned_y, score_bits, score_int, start_a?, end_a?, start_b?, end_b?}`, the score as the 16 hex digits of its
IEEE-754 bits with whether Python holds it as an int; `sequence.levenshtein` → `{distance}`. Nothing crosses as a
proxy object, so no number changes type on the way.

**Data sources and their readiness.** `tcmdb.catalog` cards, `tcmdb.datasets` and `tcmdb.status` rows each carry
`readiness: {state, next_step, missing?, live?}`, `state` one of `catalogued` (described, nothing integrated), `live` (a
connector or a live dataset), `supported` (a dataset spec, nothing downloaded), `partial`, `downloaded` (files present,
not built), `built` (answers `tcmdb.query`, `tcmdb.relations`, `tcmdb.evidence_for`); `next_step` names the job
(`job.tcmdb.fetch {dataset}`, `job.tcmdb.build`) on the runner, or the runner and `tcmdb.import_store` in the browser;
`live` names a card's connector while its stored copy is not built. The summaries count cards by state. NPASS 2.0
(card 5), CMAUP 2.0 (6) and LOTUS (137) are datasets `npass`, `cmaup`, `lotus` (`bioagent.tcmdb.extra.np_activity`):
their files are the acquisition table's (URL, size, checksum), read as the snapshot parsers read them, into
`organism_compound` and `compound_target` relations; `hub.check` compares each raw file with its reviewed checksum.

**Literature and research jobs.** `literature_search` takes `source: "clinicaltrials"` (ClinicalTrials.gov v2:
`hits: [{title, nct, status, phase, conditions}]`, a registered trial being a record of a study, not its result). On
the runner, `network_pharmacology_run` with a formula name the workbook holds under several compositions and no source
book is `bad_arguments` before any job is queued, naming the books that tell them apart (`桂枝汤《伤寒论》`), as the
research loop's own `parse_question` would refuse it after the job started. Turning a runner's network on
(`network: {enabled: true}`, the switch, `--network`) lifts an `offline-analysis` profile; a profile asked for by name
is kept.

## receipt/1: what the Python side records, by executor (additions to §3 and §4)

The schema, the precedence table and the meaning of each field are in CONTRACTS §3 (**Receipt
`tcmstudio.receipt/1`**). This is where each value comes from in `tcmstudio`. `envelope.shape` adds `schema`,
`finished_at` and the defaults to every receipt (`_fallback` too). `dispatch._finish` → `_receipt_parts` fills them
from the catalog entry, the `Outcome` (`validation`, `executed_args`, `execution`, `provenance`, `result_info`) and
the job record. Nothing is probed, and nothing is added to `result`. A failure in the receipt code leaves the defaults
and keeps the call's envelope.

| executor | implementation | provenance | result |
|---|---|---|---|
| native | entry `implementation` + registry manifest `{component, entrypoint, manifest_version, deterministic}` (read from a runtime already built, never built for it) | KB lookups: the seed corpus card (`seed_source_card`: id, version, snapshot hash); `bioagent.sources.materia` for an identity-only answer; the workbook (`source_sha256`, rows) when `repository_records`/`repository_formulas` were read; hub-backed lookups: their datasets | `record` for the KB lookups and hub-backed tools, else `computation`; `degraded` fallback from a DEGRADED call |
| connector | `{key, operation, host}` | `{id: key, host, retrieved_at: request.fetched_at, licence, release: null}`, only when a request was made | `record`; a refusal offline is `not_executed` with no dataset |
| skill | `{ref: entrypoint, version: skill version, content_hash, pinned}` | `artifact.sources` (snapshot hash as `sha256`, `snapshot_at`, licence, `release` = the card's version) | the six states + `skill_pinned` + `outputs_present`; `release_authorized` only when released with all six true |
| clinic | `tcmstudio.dispatch:_exec_clinic#<op>` | none: no argument value is copied; validation repairs and problems name paths only | `draft` |
| tcmdb | `_exec_tcmdb#<op>` | the `dataset` argument and every row's `source`, each with `bioagent.tcmdb.dataset(key).version` as `version`/`release`; the workbook for `tcmdb.formulas` | `record` (or `prediction` when the rows carry `LIMIT_PREDICTED`) |
| study | `_exec_study#<op>` | seeds (`study.combination`'s `seed`, default 0) | `computation` |
| job (submit) | the job kind's (or the skill's) + `job_kind`; the engine the arguments choose | inputs with SHA-256; seeds with the schema default (= kinds.py's) | `queued`, no kind; device `assigned: null` with a note |
| `job_status` | the job kind's or, for `skill.run`, the skill's + `job_id`; a pipeline's `{pipeline, code_digest}` from run.json | inputs; run.json's config seeds, fold models (method, version, request/response digests) and ADMET model cards; a `skill.run` job's own receipt provenance | the job's state; checks from `outcome.checks`, `run_manifest`/`envelope_digest`; `artifacts.expected` from `outcome.expected`; `outputs_verified` when digested; the engine the run chose (run.json `engine`/`trimmer`, not the parameters' `auto`) |
| system | `_exec_system#<op>`; `provider_call` adds `{provider, component}` | `provider_call`: the request's host and time | `system` (`provider_call`: `record`); `system.capabilities` adds `remote_compute {configured:false, reason}` |

`receipt.device` (legacy) on a job envelope is the job's assigned device once it has one (the poll used to say `cpu`
for a GPU job). `browser_compute._mirror` copies `compute.backend` into `execution.kernel`, the GPU device into
`execution.device.assigned/used`, and a rejected packet into a `compute` fallback, whenever `call_json` writes
`compute`. `jobs.JobService._collect_once` records `outcome.checks` (succeeded jobs) and `outcome.expected`.
`JobService.run_manifest(job_id)` and `JobsHook.run_manifest` read `out/run.json` at its recorded digest. They share
`_recorded_json` with `envelope(job_id)`, which now reads its artefact's recorded path.

## integration: how the notes above were resolved (CONTRACTS.md updated where the implementation is better)

- **Placement (runner-core, web-core, web-ui).** CONTRACTS §2 said `exec` order is the preference (browser first);
  ARCHITECTURE's placement table says pure-Python tools run in the browser *when no runner is connected, otherwise on the
  runner*. ARCHITECTURE wins: with `compute: "auto"`, `ToolRouter.placement` now sends everything a connected runner has
  to the runner (native CPython, the project's durable audit chain, the user's devices) and uses the browser otherwise.
  §2's comment and the compute strings (zh/en) say so; `web/test/router.test.mjs` tests it.
- **`/v1/*` on the runner (edge, web-core, runner-service).** A page served by a runner on a port other than 8765 looks
  for Tao-S1 on its own origin and got a 404 (a console error on every boot). The runner now answers `GET /v1/health`
  with `200 {ok:false, relay:false, error:{type:"not_relay", message}}` (a Chinese sentence saying where Tao-S1 works) and
  other `/v1/*` with `404 not_relay` (§4; `test_server.py`). A health answer without its own message (the Worker without
  a key) is shown as `core.relay.off` instead of the raw `not_configured`.
- **Pairing a runner-served page.** The banner prints `本机页面 Local app http://127.0.0.1:<port>/#pair=…` whether or not a
  token is required, so the app a runner serves pairs with that runner on any port.
- **Page-made envelopes.** `synthEnvelope` now starts `text` with the dispatcher's first line `<tool> → <via>: <status>`
  and, when there is an error, `Error (<type>). Hint: <hint>` (a refusal reads `run_pipeline → run_pipeline: refused`),
  so the model reads the precise outcome as it does from Python; the max-steps placeholder replays that same text.
- **Citations across a turn (web-ui's request).** `renumberCitations` in `core/agent.js` shifts each later envelope's
  `E#` past those already used in the turn, in call order, in `citations[].id` and in the `[E#]` marks of `text`; the
  first envelope is unchanged. An event `tool.update {callId, envelope}` tells the live view. Unit test in
  `agent.test.mjs`.
- **One job card per job.** A turn that follows a job with `job_status` shows the job's card once (on the call that
  started it); the later envelopes of the same job update it.
- **Settings edits were lost.** `pages/settings.js` saved field edits through a plain debounce that kept only the last
  call: typing an endpoint and then a model within 300 ms dropped the endpoint, and "Test" right after typing tested the
  previous settings. Edits are now merged until saved, and Test / Make default write what is pending first.
- **Reproduce command (runner-core's CLI).** The provenance tab copies `python3 -m tcmstudio call <tool> --where
  <browser|runner> --args '<json>'`. Its note no longer says the output hash will match: `receipt.output_sha256` hashes
  the whole result (run id, time, audit-chain position) and differs on every run; the output files' sha256 match
  (checked in e2e: browser, runner and native runs of `tcm_safety_report` give the same `safety.json` digest).
- **Message time.** The time under a user message was `formatDateTime(…).slice(11, 16)`, which with the time-zone name
  gave "UTC 2"; it is now `formatClock()`.
- **`job.pipeline.scrna` GPU.** The catalog said `gpu:true`, `/api/kinds` `false`; the catalog now says `false` (bioagent
  runs scVI on the CPU by design).
- **Tests.** `npm test` runs `web/test/*.test.mjs web/test/runtime/*.test.mjs web/dev/test/*.test.mjs`; `npm run test:e2e`
  runs `studio/e2e` (Playwright 1.56.1, a devDependency of `studio/package.json`, so CI's `npx playwright install` gets the
  matching Chromium); `npm run test:runtime` runs browser-runtime's Chromium check. `wrangler dev` presents requests to the
  Worker as `https://science.impf.ai` only with `--local-protocol https` (plain http gets `https_required`), so the e2e
  Worker listens with https and the page ignores the self-signed certificate.
- **Not changed (then).** Envelopes decided in the page still carried `receipt.where: "browser"` with `decided_by:
  "router"` (the UI shows "not run" for them). Since receipt/1 they carry `receipt.where: "page"` (web-core Router
  above). `--dev` publishing of `dev/gallery.html` stays the lead's call.

## integration (review round): what changed across parts after the review fixes

- **Envelopes (runner-core → all).** Every runner and Pyodide envelope carries `summary_en` (the summary in English;
  corpus names stay as written); an English page shows it on the tool card and the Run tab, with the line's `lang`
  set to the language of the string shown. Page-made envelopes may lack it and fall back to `summary`. A name the seed
  corpus does not hold is `error.type:"not_found"` (no longer `bad_arguments`). A person's decline is
  `status:"refused"`, `error.type:"declined"` (CONTRACTS §3).
- **Skill jobs (runner-core, runner-service → web).** A succeeded `skill.run` `job_status` carries the governed run's
  governance (`kind:"skill"`) from the job's `out/envelope.json`, read only at the digest recorded at collection
  (`released:false` with a limitation when it cannot be read), and the run's receipt fields (`content_hash`,
  `audit_head`, `composite_version`, `skill_pinned`, `run_id`, `run_anchor`). `context.jobs` may expose
  `envelope(job_id)`. The thread and the Claims tab show a job's governance once, under its last succeeded poll. A
  finished job reads 「已完成（输出哈希已核验）」 / "Finished (output hashes verified)" with a neutral ✓, never the
  release verdict's colour; its files in the Files tab are 「哈希已核验」 (unchanged since collection), not 「已核验」.
- **Job events (runner-service).** `/api/jobs/{id}/events` writes each event as it happens (it buffered the whole
  stream until the job ended; `test_jobs.py::test_events_are_written_live_not_at_the_end`). `RunnerRuntime.jobs.events`
  (web-core) polls every followed job for `state`/`done` and opens an event stream (for logs) for at most
  `maxJobStreams` (1) running job; a reconnect's replay of up to 80 lines is merged by `conversation.js mergeLogs`.
  Not done (optional): SSE `id:` sequence numbers and `Last-Event-ID`.
- **Catalog (runner-core → web-core).** `catalog.json` and `/api/catalog` carry `never_offered: {name: message}` (the
  acts reserved for a person); the page's `Catalog`/`ToolRouter` merge it over their built-in `HUMAN_ONLY` list.
- **Assistant records (web-core).** They gain `citeTop` (the highest `E#` the turn used: numbering runs on across the
  path, and the UI resolves `[E#]` in the turn first, then in earlier turns, never by guessing) and `error.permanent` /
  `error.lang` (a daily limit, a conversation too long, a relay that is not configured: no Retry button; the relay's
  own Chinese text on an English page is marked `lang="zh-Hans"`). Provider presets gain `stream` (how a service
  streams content and reasoning).
- **Governance views (web-core).** `licensingLadder(kindOrClaim, weakestTier, lang, {codes, cited, evidence})`: with a
  claim view and its evidence the ladder licenses by the whole cited set and its codes (a CLM005 refusal is never ✓).
  `releaseOf` gives `release_authorized` its reasons in unmet wording (「输出未核验」) with the state ids in `unmet`;
  `audit_head` is the artifact's own attestation only, and the runner's chain head (the receipt's) is `chain_head`.
  The glossary explains `UNPINNED`.
- **Relay health (edge, runner-service → web).** `relayHealth` localises a known `error.type` (and `not_relay`), prefers
  the runner's `message_en` on an English page, and returns `error_type`/`error_lang`. On Settings → Models the line
  drops its own 「设置 → 模型」 pointer. The runner's health error is `{type:"not_relay", code:"runner_no_relay", port:8765,
  message, message_en}`.
- **Identity questions (edge).** The relay runs the page's detector (`edge/src/identity.js`, kept equal to
  `web/js/core/prompt.js isIdentityQuestion` by `edge/test/identity.test.js`) on the last user message and forces
  `thinking:{type:"disabled"}`, so the answer passes the reasoning filter whatever the client sent.
- **Files (web-core, web-ui).** `store.files.add(projectId, blob, {sha256})` takes the composer's digest and skips
  reading the file again. Text files under 8 KB in a project are inlined in the system prompt and sent with every
  message (README's privacy section says so; the Files list marks them 「发送给模型」). Not done: a per-file
  "send to model" switch.
- **e2e Worker.** `startWorker` pins the relay's `[vars]` the specs depend on in `.dev.vars` (`RELAY=on`, the upstream
  model and format prefix, the output cap, limits off), so a committed pause or limit change in `wrangler.toml` does not
  fail the build job's relay spec.
- **Temporary directories (runner-core).** The dispatcher's skill view (`tcmstudio-skills-*`) and the non-durable
  project state (`tcmstudio-state-*`, used without a state root) are removed when the process ends; every runner start
  and CLI call used to leave both in the system's temp directory (`test_dispatch.py`).
