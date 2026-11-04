# TCMScience Studio — architecture

**TCMScience Studio** is the web workbench for TCMScience, served at **https://science.impf.ai**.
It gives every TCMScience capability (150 native tools, the governed skills, the clinic, the TCM
data hub, 117 public connectors, the pipelines and structure engines, and the research loop) to a
conversational agent. The agent's every claim stays inside what its evidence licenses.

The server stays thin by design: **the computation runs on the user's own machine**, in the browser or on the local
runner. The one exception is a runner job the user allows to call a third-party service (`allow_remote`, approved per
call: structure prediction on ESM Atlas, for example). No remote compute environment is deployed: under the compute
setting `remote` every call fails (status `failed`, `error.type` `unavailable`, `error.reason` `remote_not_configured`),
except `catalog_search` and `capabilities_status`, which the page answers itself. The contract a remote backend would
have to meet is in [REMOTE_COMPUTE.md](REMOTE_COMPUTE.md) (designed, not deployed).

```
                        ┌──────────────────────── science.impf.ai (one Cloudflare Worker) ───────────────────────┐
 Browser                │  static app (Workers Static Assets: free, uncounted)                                     │
 ┌──────────────────┐   │  /v1/health  /v1/models  /v1/chat/completions  ── Tao-S1 relay ──▶ upstream model API    │
 │ UI  (studio/web) │──▶│      origin allowlist · model renamed · key held as a Worker secret · per-visitor limits │
 │                  │   │  /api/sources/*  database gateway ── approved queries ──▶ registered public APIs         │
 │                  │   │  /api/datasets/files/*  dataset relay ── registered raw files, streamed, not stored      │
 │ agent loop       │   └──────────────────────────────────────────────────────────────────────────────────────────┘
 │ IndexedDB        │
 │  projects        │── or ─▶ the user's own model API (OpenAI-compatible / Anthropic), key kept in this browser
 │  conversations   │── or ─▶ a local model server (Ollama · LM Studio · vLLM · llama.cpp), directly or via the runner
 │  files           │
 │                  │      tools
 │ ToolRouter ──────┼──▶ BrowserRuntime: Pyodide in a Web Worker — the real bioagent + psh + tcmstudio code,
 │                  │      pinned and hash-checked; 150 native tools, the 4 pinned governed skills, the clinic (CPU);
 │                  │      connectors through /api/sources; the data hub over packaged or imported stores;
 │                  │      the page's job service: browser jobs in Pyodide job workers (research, RNA-seq counts,
 │                  │      single-cell count tables, ADMET, docking from prepared inputs), their files in OPFS
 │                  └──▶ RunnerRuntime: `tcmstudio serve` on 127.0.0.1 (the user's CPU / GPU)
 └──────────────────┘      everything: connectors, TCM data hub, jobs (pipelines, structure engines, research),
                           governed runs with a durable audit chain, local model proxy
```

## Placement rules

| What | Where | Why |
|---|---|---|
| UI, conversation state, projects, files, API keys | the browser (IndexedDB / localStorage) | nothing personal is kept on the server unless the visitor agrees to the recording notice |
| Usage statistics, consented conversation records, security events | three Durable Objects of the Worker (`docs/TELEMETRY.md`) | counts without text; a record only with consent, never with files, clinical tools or identifiers; readable by the site's administrators at `/admin/` |
| Default model **Tao-S1** | the Worker relay on the same origin | the upstream key stays a Worker secret; the page never names the upstream vendor |
| User's own model APIs | browser → provider directly, or via the runner's `/api/llm` proxy when the provider blocks browser calls (CORS) | keys never pass through science.impf.ai |
| Local models | browser → local server directly (needs CORS on that server), or via the runner | inference on the user's GPU/CPU |
| Pure-Python tools and pinned governed skills | **browser** (Pyodide) when no runner is connected, otherwise the runner | no install needed; the same code (compared with native CPython by the manual runtime check for a named subset, and by the browser sweep for every browser tool: [BROWSER_COMPUTE.md §10](BROWSER_COMPUTE.md#10-validation)) |
| Public-database connectors | the **runner** when one is connected (`auto`); otherwise the **browser**, through the Worker's bounded `/api/sources` gateway when web access is on and the call approved | the request, its policy and the parsing run in Python on the device; only HTTP crosses the gateway ([BROWSER_ACCESS.md](BROWSER_ACCESS.md); levels per operation: [compat/connectors.md](compat/connectors.md)) |
| TCM data hub queries | the **runner** when one is connected (`auto`); otherwise the **browser**, over the packaged stores and stores the user imports | building NPASS, CMAUP or LOTUS from their downloads is a runner job |
| Long jobs | the **runner** when one is connected (`auto`); otherwise, for the kinds the catalog marks browser jobs (`research.snapshot` and `research.run`, `pipeline.rnaseq` from a count matrix, `pipeline.scrna` on count tables, `admet.models` and `pipeline.admet`, `pipeline.dock` from prepared inputs), the **browser**'s job service: Pyodide job workers, the raw files and outputs in the site's private storage (OPFS) | the runner's Python in Pyodide (docking's engine: AutoDock Vina compiled to WebAssembly); a call's arguments decide whether it can run there; one that cannot goes to a connected runner, or is refused with the reason ([BROWSER_JOBS.md](BROWSER_JOBS.md)) |
| Data hub store builds, native engines, FASTQ runs, raw-structure preparation, structure prediction, native GPU engines | **runner** only | files, subprocesses, devices; a runner GPU is reported by its driver, not verified by a computation |
| Remote compute | **nowhere**: no backend is deployed | `remote` answers `remote_not_configured` and never falls back ([REMOTE_COMPUTE.md](REMOTE_COMPUTE.md)) |

**The server never computes.** The Worker relays model calls, serves files, and forwards approved database queries
through its bounded gateway: their URLs, bodies and supported headers, and the responses, pass through it and are not
retained (BROWSER_ACCESS.md). For the browser's research jobs it also relays the raw files the dataset registry names
(`/api/datasets/files/<group>/<name>`), from their reviewed hosts only, streamed and not stored; the browser checks
their size and checksum (BROWSER_JOBS.md). It runs no tool and stores no uploaded file. What the page sends to Tao-S1 (the messages,
the tool results and the project's text files under 8 KB, which go with every message) passes through its relay, which
does not keep it. It stores a conversation only when its visitor agreed to the recording notice (`docs/TELEMETRY.md`):
the page sends the record of each finished turn, without files, tool arguments or identifiers, and anonymous usage
counts.

## Components (directories)

| Directory | What it is | Language |
|---|---|---|
| `studio/web/` | the single-page app: no framework, no build step (ES modules) | JS / CSS |
| `studio/runner/` | `tcmstudio`: the shared tool catalog and dispatcher (pure Python, also runs in Pyodide) plus the local runner service (`tcmstudio serve`) | Python ≥ 3.11 |
| `studio/edge/` | the Cloudflare Worker for science.impf.ai: the static assets, the Tao-S1 relay, the database gateway, telemetry and the admin API | JS (Workers) |
| `studio/web/admin/` | the owner's analytics dashboard, served by the Worker to a signed-in administrator only | JS / CSS |
| `studio/scripts/` | `build_web.py`: assembles `_site/` from `studio/web` plus the generated runtime files (catalog, Python bundle) | Python |
| `studio/e2e/` | Playwright end-to-end tests with a scripted mock model, the real runner, and Pyodide | JS |
| `.github/workflows/studio.yml` | test, build and deploy (deploy only when the Cloudflare secrets exist) | YAML |

## Key decisions

1. **One tool surface, two runtimes.** `tcmstudio.catalog` builds the tool catalog from the code
   itself (type hints, skill manifests, connector templates). `tcmstudio.dispatch` executes a call
   and returns one **envelope** shape (`CONTRACTS.md §3`). The runner and the Pyodide worker import the same
   two modules, so a tool behaves the same in both, and so do its governance fields.
2. **Core tools + catalog/call.** The model is offered 27 core tools, `catalog_search` and `call_tool`
   among them, which reach every other entry (653). The full set (~58k tokens) is never sent
   every turn. `research_routes` maps each kind of research question to the entries that answer
   it, step by step, with the evidence kind each gives and what covers one that cannot run here
   ([ROUTES.md](ROUTES.md)).
3. **Governance is the product.** Every envelope carries what the kernel decided: the evidence kinds, the claim
   verdicts with codes, the six release states, the hashes, and the licences. The UI renders these first.
   A refusal is a result that is shown, never hidden.
4. **The agent loop runs in the browser**, with native tool calling (OpenAI-compatible and
   Anthropic). Long work becomes a **job** that the conversation follows (`job_status`, a job card in
   the thread): the runner's, or, for the kinds the browser runs, a job of the page's job service (ids `bj-…`).
5. **Confirmation before reaching out.** Jobs, network calls, sending a sequence to a third-party
   service (`allow_remote`) and the first call to a runner require the user's explicit approval:
   once, for this project, or never. Signing a clinic draft is a human act and is never a tool.
6. **No vendor named for Tao-S1.** The relay renames the model in both directions and rewrites
   upstream errors in its own words (the TaoChronos pattern).
7. **`auto` is runner-first, and nothing falls back to a remote place.** A connected runner takes every
   call it can run, so a project's governed runs write one durable audit chain. A remote compute
   environment, when one exists, is chosen explicitly and approved separately (REMOTE_COMPUTE.md); today
   none is deployed.

See `CONTRACTS.md` for the wire formats and module interfaces, and `DESIGN.md` for the design system.
