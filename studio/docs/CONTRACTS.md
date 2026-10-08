# TCMScience Studio — contracts

Binding interfaces between the parts of Studio. If code and this file disagree, fix one of them in the
same change. Paths are relative to `studio/` unless they start with the repository root.

Contents: §1 Relay (edge) · §2 Tool catalog · §3 Call envelope · §4 Runner HTTP API · §5 Browser
runtime (Pyodide worker) · §6 Web core modules (JS API used by the UI) · §7 Storage · §8 Settings ·
§9 System prompt and identity · §10 Ownership.

---

## 1. Relay — `science.impf.ai/v1/*` (`studio/edge`)

Same origin as the app. OpenAI-compatible. The browser never sends a key.

| Method | Path | Answer |
|---|---|---|
| GET | `/v1/health` | `{ok, service:"tcmscience-studio", version, model:"Tao-S1", models:["Tao-S1"], max_output_tokens, limits:{per_minute, per_day, tokens_per_day}}`. `ok:false` when `MINIMAX_API_KEY` is not set. |
| GET | `/v1/models` | `{object:"list", data:[{id:"Tao-S1", object:"model", owned_by:"impf"}]}` |
| POST | `/v1/chat/completions` | OpenAI chat-completions request/stream. `model` must be `"Tao-S1"` (or absent). Write `model` as the **first** JSON key (the relay edits the text). Streaming SSE is returned with `model` renamed to `Tao-S1`, reasoning formats `MiniMax-…` → `Tao-…`, and vendor-only fields removed. `thinking:{type:"disabled"}` in the request removes all reasoning from the answer; the relay sets it itself when the last user message asks about the assistant's identity or model (the page's own detector, run again as defence in depth). |

Errors are always `{error:{message, type}}`, with a Chinese `message` that tells the visitor what to do. Types: `forbidden_origin`, `not_configured`, `bad_request`,
`too_large`, `model_not_allowed`, `rate_limited`, `daily_limit`, `total_limit`, `blocked` (reserved; not emitted in v1),
`unavailable`, `upstream_auth`, `upstream_rate`, `upstream_quota`, `upstream_error`,
`upstream_rejected`, `upstream_unreachable`, `relay_error`, `https_required`, `not_found`, `method_not_allowed`. A `429`
carries `Retry-After` (exposed to other allowed origins). An error the upstream reports inside a stream arrives as one
event `data: {"error":{message, type}}`; the client treats it as a model error. The upstream's own model names are
refused like any other (`model_not_allowed`). `RELAY = "off"` (a var) stops Tao-S1: `health.ok:false`, chat `503
not_configured`. `/v1/health` answers `200` in every state, with `ok` saying whether Tao-S1 can be used.

Allowed origins (env `ALLOWED_ORIGINS`): `https://science.impf.ai`, `http://127.0.0.1:8765`,
`http://localhost:8765` (the app as served by the local runner). Output tokens are capped at
`MAX_OUTPUT_TOKENS` (default 8192).

---

## 2. Tool catalog (`tcmstudio.catalog`)

Generated from the code by `python -m tcmstudio catalog --out web/runtime/catalog.json` (the build
does this) and served live by the runner at `GET /api/catalog`. One JSON document:

```jsonc
{
  "schema": "tcmstudio.catalog/1",
  "versions": {"tcmstudio": "0.1.0", "bioagent": "0.2.7", "psh": "0.6.0"},
  "categories": [ {"id": "tcm_knowledge", "zh": "中医知识", "en": "TCM Knowledge", "count": 9}, ... ],   // 13 ids, see below
  "core": [ CoreTool, ... ],        // offered to the model directly every turn
  "entries": [ Entry, ... ],         // everything reachable through call_tool; searched by catalog_search
  "never_offered": {"clinic.sign": "…", ...}   // acts reserved for a person {name: message}: never entries; refused by name
}
```

**Category ids** (in this order): `tcm_knowledge 中医知识`, `tcm_safety 安全与配伍`,
`clinic 临床辨证`, `tcm_data 中医药数据枢纽`, `live_sources 在线数据源`,
`netpharm 网络药理与研究闭环`, `study_design 统计与研究设计`, `clinical_calc 临床计算与药理`,
`seq_genomics 序列与基因组`, `structure_molecules 结构与分子`, `omics 组学流程`,
`literature 文献证据`, `system 审计与环境`.

**CoreTool** (neutral definition; the web client converts it to OpenAI `function` / Anthropic `input_schema`):

```jsonc
{
  "name": "tcm_safety_report",                // [a-z0-9_]{1,64}; never dots
  "title": {"zh": "安全性与配伍禁忌", "en": "Safety & contraindications"},
  "description": "...",                        // English, for the model; ≤ 1000 chars; states limits ("no record ≠ safe")
  "parameters": { JSON Schema object },        // arrays have items, objects have properties
  "category": "tcm_safety",
  "exec": ["browser", "runner"],               // where it can run. compute "auto": a connected runner first, else the browser
  "network": false,                            // true: reaches the internet (needs the project's web access)
  "confirm": false,                            // true: the user must approve each call (or for the project)
  "job": false,                                // true: starts a runner job; result carries job {id,state}
  "maps_to": "skill.assess-tcm-safety"         // the Entry id it dispatches to (informational)
}
```

**Entry** (one per reachable capability):

```jsonc
{
  "id": "native.reverse_complement",            // kinds: native.<name> · skill.<id> · connector.<key>.<op>
                                                 //        clinic.<op> · tcmdb.<op> · study.<op> · job.<kind> · system.<op>
  "kind": "native",                              // native | skill | connector | clinic | tcmdb | study | job | system
  "title": {"zh": "...", "en": "..."},           // zh may equal en when no translation exists
  "summary": "Reverse complement of a DNA or RNA sequence (IUPAC ambiguity codes honoured).",
  "category": "seq_genomics",
  "parameters": { JSON Schema object },
  "example": { ... } | null,
  "exec": ["browser", "runner"],
  "network": false, "confirm": false, "job": false,
  "gpu": false,                                  // benefits from a GPU
  "heavy": [],                                   // optional deps needed, e.g. ["rdkit","vina"]; empty = core install
  "tags": ["序列", "DNA"],                         // search keywords (zh + en)
  "skill": {                                     // only for kind=skill
    "version": "0.2.0", "pinned": true, "claim_kinds": [...], "forbidden_claims": [...],
    "max_evidence_tier": "...", "network": [...hosts], "content_hash": "e525e3ab…"
  }
}
```

**The core tools** (names are fixed. `exec` uses B = browser and R = runner, and a ★ marks `confirm: true`):

| name | maps to | exec | notes |
|---|---|---|---|
| `tcm_lookup` {name, kind?} | native.tcm_lookup | B R | herb/formula/syndrome/any |
| `tcm_herb` {name} | native.tcm_herb | B R | |
| `tcm_formula` {name} | native.tcm_formula | B R | label the formula universe (seed corpus) |
| `tcm_syndrome` {name} | native.tcm_syndrome | B R | |
| `tcm_classical_search` {query, limit=5} | native.tcm_classical_search | B R | |
| `tcm_compatibility` {herbs[≥2]} | native.tcm_compatibility | B R | 十八反 / 十九畏 as recorded relations |
| `tcm_applicability` {subject, object, claim_kind, population?, condition?} | native.tcm_applicability | B R | what kinds of claim the evidence licenses |
| `tcm_normalize` {names[]} | skill.normalize-tcm-entities | B R | governed; ambiguity preserved |
| `tcm_evidence` {subject, ...} | skill.retrieve-tcm-evidence | B R | governed; grouped by evidence kind |
| `tcm_safety_report` {subject, co_administered[], population?} | skill.assess-tcm-safety | B R | governed |
| `tcm_network_hypothesis` {formula_name} | skill.analyze-tcm-network-pharmacology | B R | governed; seed corpus; mechanism *hypothesis* only |
| `clinic_assess` {intake, modifications?, apply_textbook?, days?} | clinic.assess | B R | draft for a licensed practitioner; never signs |
| `clinic_check_prescription` {intake, herbs[{herb,grams}]} | clinic.check | B R | |
| `clinic_followup` {syndrome?, baseline, current} | clinic.followup | B R | |
| `tcmdb_catalog` {query?, module?, access?} | tcmdb.catalog | B R | status (downloaded/built) only on R |
| `tcmdb_relations` {kind?, subject?, object?, contains?, evidence?, sources?, commercial?, limit?} | tcmdb.relations | R | needs a built local store |
| `tcmdb_consensus` {kind, subject?, object?, min_support?, limit?} | tcmdb.consensus | R | |
| `connector_call` {connector, operation, arguments} | connector.<key>.<op> | R | network |
| `literature_search` {query, source=europepmc, limit=10} | connector.europepmc / ncbi_eutils / crossref | R | network |
| `network_pharmacology_run` {formula, disease?, parameters?} | job.research.run | R ★ | job; snapshot pipeline |
| `run_pipeline` {pipeline: rnaseq·scrna·fold·dock·admet, arguments} | job.pipeline.* | R ★ | job; GPU-capable kinds honour the device setting |
| `job_status` {job_id, wait_s≤60} | runner jobs | R | |
| `capabilities_status` {} | system.capabilities | B R | what can run where, now |
| `catalog_search` {query?, category?, kind?, runnable_now?, limit≤25} | the catalog | B R | |
| `call_tool` {tool, arguments} | any Entry id | per entry | the entry's own `confirm`/`network` apply |

Never offered to the model: `clinic.sign` (the practitioner's act), `tcmdb.fetch --confirm` above its size gate, registry
release and promotion, raw shell. These appear in the UI only.

---

## 3. Call envelope (`tcmstudio.dispatch` → JSON)

Every tool call returns this, in both runtimes. A call **never raises**: failures are data.

```jsonc
{
  "ok": true,
  "tool": "tcm_safety_report",          // the name called (core name or call_tool)
  "via": "skill.assess-tcm-safety",     // the Entry actually executed
  "status": "succeeded",                // succeeded | failed | refused | needs_approval | job_submitted | cancelled
  "duration_ms": 1240,
  "summary": "甘草 + 甘遂：记载 1 处配伍禁忌（十八反等）；4 条安全性记录，3 条为高或严重级别（记载，非临床安全性结论） · 已准予发布",
  "summary_en": "甘草 + 甘遂: 1 recorded incompatibility (the eighteen antagonisms and others); 4 safety record(s), 3 at high or critical severity (records, not a clinical safety conclusion) · release authorized",
                                        // optional: the summary in English (corpus names stay as written); an English page shows it
  "text": "...",                        // compact text for the model, ≤ 16 000 chars (JSON or prose), states limits
  "result": { ... },                    // full JSON for the UI (may be large; the UI paginates)
  "citations": [ {"id":"E1","kind":"pmid|doi|nct|classical|source","label":"...","url":"https://...","evidence_ref":"..."} ],
  "governance": {
    "kind": "skill",                    // native | skill | connector | clinic | tcmdb | study | job | system
    "released": true,                   // skill: verdict.release_authorized
    "artifact": { ... } | null,         // ResearchArtifact.document()
    "verdict": { ... } | null,          // ArtifactVerdict.as_dict(): six states, codes, unverified reasons
    "claims": [ CandidateClaim... ],    // when produced
    "evidence": [ EvidenceItem... ],    // when produced
    "refusals": [ {"code":"CLM005","message":"...","remedy":"..."} ],
    "labels": ["INTERNAL"],             // PSH labels: advisory only
    "licences": [ {"asset":"...","licence":"...","commercial":false,"note":"..."} ],
    "limitations": ["..."],
    "outputs": [ {"path":"safety.json","sha256":"…","bytes":1234,"media_type":"application/json","content": <json|null>} ]
  },
  "receipt": {
    "where": "browser",                 // browser | runner
    "runtime": "pyodide-314.0.7 / CPython 3.14",
    "device": "cpu",                    // cpu | cuda:0 | mps | rocm:0 | webgpu (only for a JS/ONNX path)
    "versions": {"tcmstudio":"0.1.0","bioagent":"0.2.7","psh":"0.6.0"},
    "composite_version": "...",         // artifact.composite_version_string when present
    "content_hash": "e525e3ab…",        // skill content hash when present
    "audit_head": "…" | null,
    "input_sha256": "…",                // sha256 of canonical JSON arguments
    "output_sha256": "…",               // sha256 of canonical JSON result
    "started_at": "2026-10-07T12:00:00Z"
  },
  "job": null | {"id":"j_…","kind":"pipeline.rnaseq","state":"queued"},
  "approval": null | {"reason":"network|job|confirm|remote_upload|first_runner_call","what":"…","hosts":["…"],"decision"?:"deny"},
  "error": null | {"type":"bad_arguments|unavailable|not_found|refused|declined|runtime_error|timeout|network_off","message":"…","hint":"…"}
}
```

Rules:
- `text` starts with the line `<tool> → <via>: <status>` in every envelope, then `Error (<type>)…` when there is an
  error, including envelopes decided in the page (the router's `needs_approval`/`refused`/`unavailable`/`network_off`,
  which carry `receipt.decided_by: "router"` and no output hash).
- Citations are numbered `E1…` per envelope by the dispatcher; the agent renumbers them across one turn in call order
  (the second result's `E1` becomes the next free id, in `citations[].id` and in the `[E#]` marks of `text`), so an id
  is unique within a turn for the model and the reader. Numbering also runs on across the conversation path: a turn
  starts after the highest `E#` of the turns before (the assistant record keeps it as `citeTop`). The UI resolves `[E#]`
  against the envelopes of the same turn first, then the earlier turns on the path; an id found nowhere stays unresolved.
- `receipt.output_sha256` hashes the whole result, which includes the run id, the time and the audit-chain position: it
  differs on every run. What is reproducible is `input_sha256`, `content_hash` and each `governance.outputs[].sha256`.
- `text` is what the model reads. It must restate the limits (`absence of a record is not evidence
  of safety`, `predicted ≠ measured`, `draft for a licensed practitioner`) when the result has them.
- Wrong arguments produce `status:"failed"`, `error.type:"bad_arguments"`, and a hint such as "Did you mean 'names'?".
  A name the seed corpus does not hold ("names nothing in this knowledge base") is not a wrong argument: it is
  `error.type:"not_found"`, and the summary says that not found there is not absence.
- A person declining an approval gives `status:"refused"` with `error.type:"declined"` (decided in the page,
  `approval.decision:"deny"`): not run, and not the kernel's refusal.
- A succeeded `job_status` for a `skill.run` job carries the governed run's governance (`kind:"skill"`: claims,
  evidence, artifact, verdict, refusals, limitations, outputs with content), copied from the job's `out/envelope.json`
  and read only at the digest recorded when the job was collected; when it cannot be read, `released:false` with a
  limitation saying why. Its receipt also carries `content_hash`, `audit_head`, `composite_version`, `skill_pinned`,
  `run_id` and `run_anchor` of that run. The dispatcher's `context.jobs` may expose `envelope(job_id)` besides
  `submit`/`get` for this. The model may poll `job_status` again after success; the UI shows a job's governance once,
  under the last succeeded poll.
- A tool that can't run here (missing dep, needs runner, network off) returns `status:"failed"`
  with `error.type:"unavailable" | "network_off"` and a remedy. It never substitutes an approximation.
- `needs_approval` is returned by the *web client's router* (not by Python) when a `confirm` /
  `network` tool is called without a standing approval. The UI then shows the PermissionRequestCard.
  After approval, the call is retried with the approval attached.

---

## 4. Runner HTTP API (`tcmstudio serve`, default `http://127.0.0.1:8765`)

Standard library HTTP server (`ThreadingHTTPServer`). JSON in and out, UTF-8.

**Security**
- Binds to `127.0.0.1` by default. The `Host` header must name a loopback name, or the bound address (this guards against DNS rebinding).
- CORS: an `Origin` is allowed if it is `https://science.impf.ai`, loopback `http(s)://localhost|127.0.0.1|[::1]:*`,
  the runner's own origin, or listed with `--allow-origin`. Preflight answers `Access-Control-Allow-Private-Network: true`.
  Any other Origin gets `403`.
- **Pairing token:** generated on first start, stored in `<home>/runner.json`, and required on every
  `/api/*` request except `GET /api/health` (header `X-TCM-Token`, or `?token=` for EventSource and file
  links). `--no-token` disables it, and only when bound to loopback. On start, the runner prints
  and opens `https://science.impf.ai/#pair=<base64url(JSON{url,token})>`. The page stores the token
  and removes the fragment from the URL.
- Request bodies are capped (JSON 8 MB; uploads 4 GB, streamed to disk). Nothing logs request bodies.
- The banner also prints `http://127.0.0.1:<port>/#pair=…` (token or not) for the app it serves: opened through it, the
  page pairs with that runner and connects without a prompt beyond the first-call approval.
- `X-Filename` on `POST /api/uploads` is percent-encoded UTF-8 (`encodeURIComponent`), since headers cannot carry
  Chinese file names. The `/api/llm` proxy forwards `content-type, accept, authorization, x-api-key, api-key,
  anthropic-version, anthropic-beta, anthropic-dangerous-direct-browser-access, http-referer, x-title,
  openai-organization, openai-project`, never cookies, `Origin` or the runner's token.

**Home and workspace:** `--home` (default `~/.tcmscience/studio`). It holds `runner.json` (settings and token), `jobs/`,
`uploads/`, `projects/<project_id>/psh/` (one durable PSH state directory per project for
governed runs), and `data/` (`BIOAGENT_DATA_LAKE`, `BIOAGENT_TCMDB` point here unless already set).

| Method | Path | Body → Answer |
|---|---|---|
| GET | `/api/health` | `{name:"tcmstudio", version, token_required:bool, paired:bool}` (no token needed) |
| GET | `/api/info` | `{name, version, versions:{tcmstudio,bioagent,psh,python}, home, platform, cpu:{cores, model}, memory_gb, devices:[Device], engines:[Engine], settings:Settings, network:{enabled, profile}, purpose, counts:{core, entries}}` |
| GET | `/api/catalog` | the catalog (§2), with `exec` reflecting this runner (heavy deps probed: `available:false` + `missing:[…]` per entry) |
| POST | `/api/call` | `{tool, arguments, context:{project_id?, conversation_id?, approvals?:[…]}}` → envelope (§3) |
| GET | `/api/devices` | `{devices:[Device], selected:"auto"}` where `Device = {id:"cpu"|"cuda:0"|"mps"|"rocm:0", kind, name, memory_gb?, driver?, available:bool, note?}` |
| GET | `/api/settings` / PUT | `Settings = {device:"auto"|id, threads:int, max_jobs:int, network:{enabled:bool, profile:"offline-analysis"|"biomedical-research"}, purpose:"academic"|"commercial", allow_remote:bool}` |
| GET | `/api/kinds` | `[{kind, title:{zh,en}, parameters:JSONSchema, available, missing:[…], gpu, duration:"seconds"|"minutes"|"hours"}]` |
| POST | `/api/jobs` | `{kind, params, project_id?, submission_id?}` → `201 Job` (same `submission_id` → the existing job) |
| GET | `/api/jobs` | `?project_id=&state=` → `{jobs:[Job]}` |
| GET | `/api/jobs/{id}` | `Job` |
| DELETE | `/api/jobs/{id}` | cancel → `Job` (409 if already finished) |
| GET | `/api/jobs/{id}/events` | SSE: `event: state|log|progress|artefact|done`, `data: JSON`, each written as it happens; keep-alive comment every 15 s; `?token=`. A (re)connection starts with `state` and up to 80 recent `log` lines. |
| GET | `/api/jobs/{id}/files` | `{files:[{path, bytes, sha256, media_type}]}` |
| GET | `/api/jobs/{id}/files/{path}` | bytes; HTML is served with `Content-Security-Policy: sandbox` |
| POST | `/api/uploads` | raw body; headers `X-Filename`, `Content-Type` → `{id, name, bytes, sha256, media_type}` |
| GET | `/api/uploads` | `{uploads:[…]}` |
| GET | `/api/llm/local` | probes local model servers → `{servers:[{id:"ollama"|"lmstudio"|"vllm"|"llamacpp", base_url, ok, models:[…]}]}` |
| POST | `/api/llm` | model proxy: header `X-TCM-Target: <full URL>`, body forwarded, response streamed back. The target must be loopback, or a host in the provider allowlist (OpenAI, Anthropic, DeepSeek, DashScope, Moonshot, Zhipu, SiliconFlow, OpenRouter, and `--allow-host`). The key travels in the request's own `Authorization`/`x-api-key` and is never stored or logged. |
| GET | `/` and static paths | the web app (bundled `web/` or `--web DIR`), so `http://127.0.0.1:8765/` works offline |
| GET | `/v1/health` | `200 {ok:false, service:"tcmstudio", relay:false, error:{type:"not_relay", code:"runner_no_relay", port:8765, message, message_en}}`: the runner never relays Tao-S1 (`message` is Chinese, `message_en` English; the page localises by `type`). A page it serves on a port other than 8765 looks for the relay on its own origin and reads this; other `/v1/*` paths are `404 not_relay` with the same `code`, `port` and `message_en`. |

`Job = {id, kind, state:"queued"|"running"|"succeeded"|"failed"|"cancelled", params, project_id,
created_at, started_at, finished_at, progress:{fraction?, message?}|null, device, outcome:{status, error?, problems?}|null,
artefacts:[{name, sha256, bytes}], files_url}`. A job is `succeeded` only after its outputs are
collected and verified (digest + pipeline `verify_run`). Until then it is pending, and its result is never shown as an answer.

**Job kinds:** `pipeline.rnaseq`, `pipeline.scrna`, `pipeline.fold`, `pipeline.dock`, `pipeline.admet`,
`research.run`, `skill.run` (any governed skill as a background run), `tcmdb.fetch`, `tcmdb.build`.
Each kind maps typed params to a fixed `bioagent.cli` argv (never a raw command from the browser).
File parameters reference upload ids. The device setting becomes `CUDA_VISIBLE_DEVICES` (`""` forces
CPU) plus engine options, and threads become `OMP_NUM_THREADS` etc.

---

## 5. Browser runtime (`web/js/runtime/browser.js` + `web/js/runtime/pyodide.worker.js`)

- A module Web Worker loads **pinned Pyodide 314.0.7** from `https://cdn.jsdelivr.net/pyodide/v314.0.7/full/`.
  It loads `pyyaml` and `sqlite3`, then the app bundle `runtime/tcms-py.<sha12>.tar.gz` (a build
  artefact of `scripts/build_web.py` containing `psh`, `bioagent` (with its skills, registry and lockfiles) and
  `tcmstudio`). The bundle's SHA-256 is checked before unpacking, then cached in Cache Storage.
- **Boot manifest** `runtime/boot.json` (written by the build): `{pyodide:{version, index_url},
  bundle:{path, sha256, bytes}, catalog:"runtime/catalog.json", versions:{…}}`. The page reads the
  catalog at once, so tools can be offered before Python has loaded.
- Worker protocol (JSON strings across the boundary):
  - request `{id, op:"init"|"info"|"call"|"device", payload}`
  - reply `{id, ok, result|error, ms}`
  - progress `{type:"progress", progress:0..100, message}`
- `call` runs `tcmstudio.dispatch.call(tool, arguments, context)` in Python and returns the envelope (§3) with
  `receipt.where = "browser"`.
- Governed runs inside the worker write their PSH state to MEMFS under `/persist/<project_id>/psh`. If
  IDBFS works, it is synced after each run. Otherwise the run is still attested in memory, and `receipt` says
  `durable:false`.
- A Pyodide fatal error terminates and restarts the worker. The failed call returns `status:"failed"`,
  `error.type:"runtime_error"`.
- Device report: `{cores, memory_gb, cross_origin_isolated, webgpu:{api, adapter:{vendor, architecture, description, software:bool}|null}}`.
  The UI says "Python tools run on the CPU (single thread, WebAssembly)". A software WebGPU adapter is reported as such.

---

## 6. Web core modules — the JS API the UI uses (`web/js/core/*`, `web/js/runtime/*`)

All modules are ES modules with no third-party runtime dependencies. They contain no DOM code, except
`markdown.js`, which returns DOM nodes.

```js
// core/events.js — tiny emitter
export class Emitter { on(type, fn) → off(); emit(type, payload); }

// core/i18n.js
export function t(key, vars?) → string;  export function lang() → "zh"|"en";  export function setLang(l);
export const glossary = { evidenceTier(id), claimKind(id), verdictState(id), code(id), category(id) } // zh/en labels
export function onLangChange(fn) → off;  export function registerStrings(lang, dict);

// core/settings.js  (localStorage "tcmstudio.settings"; see §8)
export function loadSettings() → Settings; export function saveSettings(s); export const DEFAULTS;
export function onSettingsChange(fn) → off;

// core/store.js  (IndexedDB "tcmstudio", see §7; in-memory fallback when IndexedDB is unavailable)
export async function openStore() → Store
Store.projects: { list({archived?}), get(id), create({name, description?, instructions?, color?}), update(id, patch), remove(id) }
Store.conversations: { list(projectId), recent(limit), get(id), create({projectId, title?}), update(id, patch), remove(id) }
Store.messages: { list(conversationId), get(id), put(message), remove(id) }      // tree via parentId
Store.files: { list(projectId), get(id), add(projectId, File|Blob, {name, source}) → FileRecord (sha256 computed), remove(id) }
Store.search(query) → [{type:"conversation"|"message"|"project", id, conversationId?, projectId, snippet}]
Store.exportProject(projectId) → Blob (JSON);  Store.importProject(Blob) → projectId

// core/providers.js
export const PRESETS  // [{id, label, format:"openai"|"anthropic", base_url, models:[…], default_model, key_required,
                      //   local?:bool, relay?:bool, cors:"ok"|"blocked"|"needs-config", help?, max_tokens_param, reasoning}]
export function activeProvider(settings, override?) → Provider   // {…preset, model, baseUrl, apiKey, route:"direct"|"runner"}
export async function relayHealth() → {ok, model, limits, max_output_tokens}
export async function testConnection(provider) → {ok, latency_ms, tools:bool, error?}

// core/llm/openai.js, core/llm/anthropic.js
export async function* streamChat({provider, system, messages, tools, signal, thinking}) →
  yields {type:"text", delta} | {type:"reasoning", delta} | {type:"tool_call", id, name, arguments}
       | {type:"usage", input, output} | {type:"done", finish_reason, wire}   // wire = assistant message to replay to this provider

// core/catalog.js
export async function loadCatalog(runtimes) → Catalog   // merges runtime catalogs; Catalog.core, .entries, .byId, .search(q, opts)
export function toProviderTools(coreTools, format) → []  // OpenAI functions or Anthropic tools

// core/router.js
export class ToolRouter {
  constructor({catalog, runtimes, settings, approvals})
  where(toolOrEntry) → "browser"|"runner"|null              // choice + why
  async call(name, args, {projectId, conversationId, signal, onApproval}) → Envelope
    // enforces confirm/network approvals: if needed, awaits onApproval(request) → "once"|"project"|"deny"
}

// core/agent.js
export async function runTurn({provider, system, history, router, catalog, settings, signal, onEvent, projectId, conversationId}) → TurnResult
  // onEvent({type, ...}):
  //   "model.start" {step}            "text" {delta}            "reasoning" {delta}
  //   "tool.start" {callId, name, args, where}                  "tool.approval" {callId, request} (UI may show card)
  //   "tool.end" {callId, envelope}   "usage" {input, output}  "model.end" {step, finish_reason}
  //   "error" {message, kind:"model"|"network"|"runtime"|"aborted"}   "done" {status:"ok"|"stopped"|"error"|"max_steps"}
  // TurnResult = {assistant: Message, toolMessages: [...], usage, status}
  // max steps (model calls) default 16; tool results truncated to 2 500 chars in replayed history;
  // <think> stripped from replayed history; transient errors retried twice (1.5 s, 4 s).
  // Also (CONTRACT_NOTES "web-core"): onApproval(request) → decision; events "tool.pending" {index, name, step},
  // "model.retry" {step, …} (drop that step's partial text), "tool.update" {callId, envelope} (citations renumbered);
  // the last allowed model call is made with toolChoice "none".

// core/prompt.js
export function buildSystemPrompt({lang, provider, project, knowledge, env}) → string   // §9

// core/governance.js — pure functions over envelopes
export function citationsOf(envelope) → [Citation]
export function claimsOf(envelope) → [ClaimView]          // {kind, text, allowed, codes, weakest_tier, caveats, extrapolations, falsified_by, family}
export function evidenceOf(envelope) → [EvidenceView]     // {id, family:"tradition"|"bench"|"clinical"|"predicted", design, tier, citation, quote, quote_verified, quality, source}
export function releaseOf(envelope) → {states:[{id, ok, reason}], authorized, summary} | null
export function familyOfTier(tier) → family;  export function verdictBadge(...) → {tone, icon, label}

// core/markdown.js
export function renderMarkdown(text, {onCitation?}) → DocumentFragment   // safe: builds DOM, never parses HTML;
                                                                          // http/https/mailto links only; [E1] → citation chip

// runtime/index.js
export async function createRuntimes(settings) → {browser: BrowserRuntime, runner: RunnerRuntime}
// RuntimeClient interface (both):
//   kind, label, status ("idle"|"connecting"|"loading"|"ready"|"error"|"offline"), onStatus(fn) → off
//   async start(); async info(); async catalog(); async call(tool, args, ctx) → Envelope; async device()
// RunnerRuntime additionally: url, token, pair(url, token), async jobs.{submit,get,list,cancel,files}, jobs.events(id, onEvent) → close,
//   fileUrl(jobId, path), async upload(file, {onProgress}), async localModels(), async settings.{get,put}
```

---

## 7. Storage (IndexedDB `tcmstudio`, version 1)

| store | keyPath | indexes | record |
|---|---|---|---|
| `projects` | `id` | `updatedAt` | `{id, name, description, instructions, color, icon, archived:false, createdAt, updatedAt, defaults:{provider?, model?, compute?, web?:false}, approvals:{[toolOrEntryId]: "project"}}` |
| `conversations` | `id` | `projectId`, `updatedAt` | `{id, projectId, title, createdAt, updatedAt, leafId, model?, pinned?:false}` |
| `messages` | `id` | `conversationId` | `{id, conversationId, parentId, role:"user"|"assistant"|"tool", createdAt, content, attachments?:[fileId], reasoning?, toolCalls?:[{id,name,args}], toolCallId?, envelope?, provider, model, wire?, usage?, status?:"ok"|"stopped"|"error", error?}` |
| `files` | `id` | `projectId`, `sha256` | `{id, projectId, name, type, bytes, sha256, blob, source:"upload"|"tool", createdAt, runnerUploadId?}` |

IDs are `crypto.randomUUID()`. Branching: editing a user message creates a sibling with the same `parentId`.
`conversation.leafId` selects the shown branch. A turn is saved when it ends, and saves are
debounced during streaming.

---

## 8. Settings (`localStorage["tcmstudio.settings"]`)

```jsonc
{
  "lang": "zh", "theme": "system",                 // system | light | dark
  "provider": "tao",                               // preset id; "tao" = Tao-S1 relay
  "models": {}, "baseUrls": {}, "keys": {}, "rememberKeys": true,
  "customProviders": [],                           // {id,label,format,base_url,model,headers,extra_body,max_tokens_param}
  "route": "auto",                                 // auto | direct | runner (for model calls)
  "thinking": true, "temperature": null, "maxTokens": 8192, "maxSteps": 16,
  "compute": "auto",                               // auto | browser | runner
  "runner": {"url": "http://127.0.0.1:8765", "token": ""},
  "web": false,                                    // default web access for new projects
  "onboarded": false,
  "inspectorWidth": 400, "sidebarCollapsed": false
}
```

The language is also mirrored in `localStorage["tcmscience.lang"]`, matching the Arena.

---

## 9. System prompt and identity

`buildSystemPrompt` composes these parts, in this order:
1. Identity: *"You are Tao-S1, the research assistant of TCMScience Studio (IMPF-AI)."* This applies when the
   provider is the relay. For another provider: *"You are the research assistant of TCMScience Studio,
   running on <model>."* The upstream vendor of Tao-S1 is never named.
2. TCMScience governance rules: evidence kinds are types, not grades. A prediction is never stated as a fact. No
   record ≠ safe, and not significant ≠ irrelevant. Classical records are attributions, not clinical evidence. The clinic gives drafts for a
   licensed practitioner. Cite tool evidence as `[E1]` using the envelope citations. Say what was refused, and why.
3. Tool guidance: prefer the core tools. Use `catalog_search` then `call_tool` for the rest. Long work → a job
   (`job_status`). Never fabricate tool output. If a tool is unavailable, say what would be needed.
4. Environment: the language, the compute available now (browser/runner, device), whether web access is on,
   and the date.
5. Project instructions, and the knowledge index (names, sizes, sha256; texts under 8 kB are inlined).

Identity questions (`你是谁`, `what model are you`, …) to the relay are sent with
`thinking:{type:"disabled"}`, and no reasoning is shown or stored for that turn.

---

## 10. Ownership (who edits what)

| Path | Owner |
|---|---|
| `studio/edge/**` (incl. `_headers`), `.github/workflows/studio.yml` | edge |
| `studio/runner/pyproject.toml`, `runner/src/tcmstudio/{__init__,__main__,cli,catalog,core_tools,dispatch,envelope,governance}.py`, `runner/tests/test_{catalog,dispatch,envelope,governance}*.py` | runner-core |
| `runner/src/tcmstudio/{server,jobs,kinds,devices,llmproxy,security,settings}.py`, `runner/tests/test_{server,jobs,devices,llmproxy,security}*.py` | runner-service |
| `runner/src/tcmstudio/webbuild.py`, `studio/scripts/build_web.py`, `studio/web/js/runtime/{browser.js,pyodide.worker.js}`, `studio/web/test/runtime/**`, `runner/tests/test_webbuild.py` | browser-runtime |
| `studio/web/js/core/**`, `studio/web/js/runtime/{index,runner}.js`, `studio/web/test/*.test.mjs`, `studio/web/test/fixtures/**`, `studio/package.json` (dependencies) | web-core |
| `studio/web/index.html`, `studio/web/css/**`, `studio/web/js/ui/**`, `studio/web/js/main.js`, `studio/web/js/boot.js`, `studio/web/assets/**`, `studio/web/dev/**` (incl. `dev/test`) | web-ui |
| `studio/e2e/**`, `studio/README.md`, `studio/package.json` (`test` / `test:e2e` scripts), root README links, cross-part fixes | integration |

CLI wiring: `tcmstudio.cli` (runner-core) defines the subcommands `catalog`, `call`, `serve`, and `webbuild`.
`serve` delegates lazily to `tcmstudio.server.add_arguments(parser)` / `tcmstudio.server.run(args)`, and
`webbuild` delegates to `tcmstudio.webbuild.add_arguments(parser)` / `tcmstudio.webbuild.run(args)`.

Tests: `node --test studio/edge/test/*.test.js` (edge) · `python3 -m pytest -q studio/runner/tests` (runner) ·
`cd studio && npm test` (web core, UI, browser runtime with a scripted worker) · `cd studio && npm run test:e2e`
(Playwright end to end: the real runner, Pyodide from jsDelivr, `wrangler dev` of the Worker; see `studio/README.md`).

Build output: `python3 studio/scripts/build_web.py --out studio/_site` (git-ignored), served by the Worker
(`[assets] directory = "../_site"` from `studio/edge`). The repository's own `_site/` belongs to the GitHub
Pages project site and is not used by Studio.

UI strings: `core/i18n.js` owns the glossary and exposes `registerStrings(lang, dict)`. The UI keeps
its strings in `js/ui/strings.js` and registers them at startup.
