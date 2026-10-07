# TCMScience Studio — architecture

**TCMScience Studio** is the web workbench for TCMScience, served at **https://science.impf.ai**.
It gives every TCMScience capability (150 native tools, the governed skills, the clinic, the TCM
data hub, 117 public connectors, the pipelines and structure engines, and the research loop) to a
conversational agent. The agent's every claim stays inside what its evidence licenses.

The server stays thin by design: **the computation runs on the user's own machine**.

```
                        ┌──────────────────────── science.impf.ai (one Cloudflare Worker) ───────────────────────┐
 Browser                │  static app (Workers Static Assets: free, uncounted)                                     │
 ┌──────────────────┐   │  /v1/health  /v1/models  /v1/chat/completions  ── Tao-S1 relay ──▶ upstream model API    │
 │ UI  (studio/web) │──▶│      origin allowlist · model renamed · key held as a Worker secret · per-visitor limits │
 │ agent loop       │   └──────────────────────────────────────────────────────────────────────────────────────────┘
 │ IndexedDB        │
 │  projects        │── or ─▶ the user's own model API (OpenAI-compatible / Anthropic), key kept in this browser
 │  conversations   │── or ─▶ a local model server (Ollama · LM Studio · vLLM · llama.cpp), directly or via the runner
 │  files           │
 │                  │      tools
 │ ToolRouter ──────┼──▶ BrowserRuntime: Pyodide in a Web Worker — the real bioagent + psh + tcmstudio code,
 │                  │      pinned and hash-checked; 150 native tools, the 4 pinned governed skills, the clinic (CPU)
 │                  └──▶ RunnerRuntime: `tcmstudio serve` on 127.0.0.1 (the user's CPU / GPU)
 └──────────────────┘      everything: connectors, TCM data hub, jobs (pipelines, structure engines, research),
                           governed runs with a durable audit chain, local model proxy
```

## Placement rules

| What | Where | Why |
|---|---|---|
| UI, conversation state, projects, files, API keys | the browser (IndexedDB / localStorage) | nothing personal is kept on the server |
| Default model **Tao-S1** | the Worker relay on the same origin | the upstream key stays a Worker secret; the page never names the upstream vendor |
| User's own model APIs | browser → provider directly, or via the runner's `/api/llm` proxy when the provider blocks browser calls (CORS) | keys never pass through science.impf.ai |
| Local models | browser → local server directly (needs CORS on that server), or via the runner | inference on the user's GPU/CPU |
| Pure-Python tools and pinned governed skills | **browser** (Pyodide) when no runner is connected, otherwise the runner | no install needed; same code, same content hashes |
| Network connectors, the TCM data hub, long jobs, GPU | **runner** only | CORS, files, subprocesses, devices |

**The server never computes.** The Worker relays model calls and serves files. It does not store
conversations, receive tool calls, or see uploaded data.

## Components (directories)

| Directory | What it is | Language |
|---|---|---|
| `studio/web/` | the single-page app: no framework, no build step (ES modules) | JS / CSS |
| `studio/runner/` | `tcmstudio`: the shared tool catalog and dispatcher (pure Python, also runs in Pyodide) plus the local runner service (`tcmstudio serve`) | Python ≥ 3.11 |
| `studio/edge/` | the Cloudflare Worker for science.impf.ai: the static assets and the Tao-S1 relay | JS (Workers) |
| `studio/scripts/` | `build_web.py`: assembles `_site/` from `studio/web` plus the generated runtime files (catalog, Python bundle) | Python |
| `studio/e2e/` | Playwright end-to-end tests with a scripted mock model, the real runner, and Pyodide | JS |
| `.github/workflows/studio.yml` | test, build and deploy (deploy only when the Cloudflare secrets exist) | YAML |

## Key decisions

1. **One tool surface, two runtimes.** `tcmstudio.catalog` builds the tool catalog from the code
   itself (type hints, skill manifests, connector templates). `tcmstudio.dispatch` executes a call
   and returns one **envelope** shape (`CONTRACTS.md §3`). The runner and the Pyodide worker import the same
   two modules, so a tool behaves the same in both, and so do its governance fields.
2. **Core tools + catalog/call.** The model is offered about 26 core tools plus `catalog_search` and
   `call_tool`, which reach every other entry (≈ 600). The full set (~58k tokens) is never sent
   every turn.
3. **Governance is the product.** Every envelope carries what the kernel decided: the evidence kinds, the claim
   verdicts with codes, the six release states, the hashes, and the licences. The UI renders these first.
   A refusal is a result that is shown, never hidden.
4. **The agent loop runs in the browser**, with native tool calling (OpenAI-compatible and
   Anthropic). Long work becomes a runner **job** that the conversation follows (`job_status`, a job
   card in the thread).
5. **Confirmation before reaching out.** Jobs, network calls, sending a sequence to a third-party
   service (`allow_remote`) and the first call to a runner require the user's explicit approval:
   once, for this project, or never. Signing a clinic draft is a human act and is never a tool.
6. **No vendor named for Tao-S1.** The relay renames the model in both directions and rewrites
   upstream errors in its own words (the TaoChronos pattern).

See `CONTRACTS.md` for the wire formats and module interfaces, and `DESIGN.md` for the design system.
