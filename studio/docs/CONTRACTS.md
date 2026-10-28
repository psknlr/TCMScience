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
| GET | `/v1/health` | `{ok, service:"tcmscience-studio", version, model:"Tao-S1", models:["Tao-S1"], max_output_tokens, limits:{per_minute, per_day, tokens_per_day}, telemetry:{analytics, conversations, consent_version, geo, retention_days:{conversations, events, aggregates, security}}}`. `ok:false` when `MINIMAX_API_KEY` is not set. `telemetry` says what the site takes (`docs/TELEMETRY.md`); a page sends usage events and asks for consent only when the health of its **own** origin says so (the runner's health has no `telemetry`: nothing is sent). |
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

Telemetry and the admin API live beside the relay on the same origin: `/api/telemetry/{health,events,conversations,
conversations/delete}` (same-origin pages only) and `/api/admin/*` with the dashboard at `/admin/` (the owner's
`ADMIN_TOKEN`); their shapes, the record's fields and the event types are in `docs/TELEMETRY.md`, and the shared
contract both sides read is `web/js/core/telemetry-contract.js`.

Allowed origins (env `ALLOWED_ORIGINS`): `https://science.impf.ai`, `http://127.0.0.1:8765`,
`http://localhost:8765` (the app as served by the local runner). A model call forwarded by a Worker of another Cloudflare
zone (`CF-Worker` naming another zone, or the address Cloudflare gives every cross-zone subrequest,
`2a06:98c0:3600::103`) is refused with `forbidden_origin` whatever its `Origin`: a Worker writes any `Origin` it likes, and
the page's calls never pass through one. Output tokens are capped at `MAX_OUTPUT_TOKENS` (default 8192).

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
  },
  "implementation": {"ref": "bioagent.tools.sequence:reverse_complement", "version": "0.2.7"},
                                                 // natives: the tool's entrypoint; skills: entrypoint + skill version
                                                 // + content_hash (null unpinned); connectors:
                                                 // bioagent.providers.public_apis:<key>.<op>; clinic/tcmdb/study/system:
                                                 // tcmstudio.dispatch:_exec_<kind>#<op>; jobs: tcmstudio.kinds:KINDS#<kind>
  "state": {"implementation": "tested",          // registered | implemented (the ref resolves; a hand-written op only if
                                                 // dispatch.HANDLED_OPS lists it) | tested (a verification level is
                                                 // PASS) | deprecated (catalog.DEPRECATED; "deprecated": why)
            "readiness": {"runner": {"state": "ready", "requires": []}}},
                                                 // the running runner's catalog only (/api/catalog), see below; never
                                                 // in the browser catalog, nor in a probed build that is not told the
                                                 // runner's network switch and job service (catalog.entries)
  "verification": {"status": "PASS",             // bioagent.validation: PASS|FAIL|BLOCKED|UNVERIFIED|SKIPPED|NOT_APPLICABLE
                   "levels": {"reference_output": {"status": "PASS"}}},
                                                 // levels ⊂ live_upstream {status, at} (connectors) | reference_output
                                                 // (natives with an independent reference) | kernel_parity (the 7 kernel
                                                 // tools) | lock_pin (pinned skills); none → UNVERIFIED, levels {}
  "approvals": [{"reason": "network"}],          // what a call may ask for: confirm | job | network | remote_upload
                                                 // (hosts: `hosts`, or `hosts_by` for the argument)
  "execution": {                                 // only where it applies
    "browser_compute": {"backends": ["webgpu", "wasm-simd", "js"], "kernel": "sequence.counts",
                        "precision": "…", "workload": "…"},     // the 7 kernel tools (tcmstudio.browser_compute)
    "engines": [{"id": "esmfold", "selected_by": {"arg": "method", "value": "esmfold", "with": {…}?},
                 "requires": [Requirement], "device": "cpu|gpu|remote",
                 "readiness": {"runner": R}}],                  // pipeline jobs and skills; readiness on the runner only
    "browser_job": {"service": "studio-jobs/1", "runtime": "pyodide", "workers": "…",
                    "storage": "origin private file system", "pyodide_packages": ["numpy", "scipy"],
                    "inputs": "…", "engines": ["builtin"],
                    "chemistry": {"engine": "rdkit.js", "package": "@rdkit/rdkit", "version": "2026.9.1",
                                  "release": "2026.09.1"},        // the ADMET kinds: the page's chemistry worker
                    "models": "…",                               // admet.models: what it builds and keeps (private)
                    "limits": {"molecules": 20000},              // pipeline.admet: more is refused for memory
                    "runner_only": [{"arg": "counts", "absent": true, "why": "…"},
                                    {"arg": "de_backend", "is": "pydeseq2", "why": "…"}]}
                                                 // the job kinds the page's job service runs (catalog
                                                 // BROWSER_JOB_KINDS: research.snapshot, research.run [pyarrow],
                                                 // pipeline.rnaseq, pipeline.scrna [numpy, scipy], admet.models,
                                                 // pipeline.admet [numpy, scipy, scikit-learn; chemistry rdkit.js]);
                                                 // the three pipelines run part of their kind: runner_only lists the
                                                 // arguments that need the runner ({arg, absent: true | is: value |
                                                 // suffix: [...], why}; capability.runner_only), BROWSER_JOBS.md §3.1,
                                                 // §3.2
  },
  "hosts_by": {"dataset": {"herb2": ["47.92.70.12"]}},           // job.tcmdb.fetch (per dataset), system.provider_call
                                                 // ({tool: hosts}, the reviewed allowlists): the hosts of one call
  "data": ["*"], "data_from": {"param": "dataset"},              // the stores read: keys, '*' = any built store;
                                                 // data_from: the argument that names it (tcmdb.tables/query/unresolved)
  "limits": {"max_request_bytes": {"runner": 8388608, "browser": 100663296}}
                                                 // only where an entry differs from the document's `limits`
}
```

The document also carries `limits: {max_request_bytes: {runner: 8388608, browser: 33554432}}` (the runner's HTTP body
limit; a conservative estimate for one call in the browser worker, not a measurement) and `verification_records:
{<level>: <committed file>}` (a level names its own `record` only when it rests on another file). Verification comes
from committed records only, joined when the catalog is built (the browser runtime's bundle carries no
`BioScience-Harness/data`, so its in-worker catalog reads connectors as UNVERIFIED; catalog.json carries the join);
nothing from a fixture is labelled live, and no level is dated by the clock.

**Readiness** (`tcmstudio.capability`, one function for the preflight, `catalog_search`, `system.capabilities`, the
runner catalog and the audit): `R = {state, requires: [Requirement]}`, `Requirement = {need, detail, remedy, names?}`
(§3 `error.requires`). `state` is the first that applies of `needs_runner` (runner, job_service) · `missing_dependency`
· `needs_dataset` (a declared dataset not built in that runtime's data hub, or the research snapshots) ·
`network_unavailable` · `needs_authentication` · `unsupported`, else `ready`. An optional engine that is missing does
not make its entry unready; the engine's own readiness says so, and a call that chooses it is refused before submit.
`ready` for an entry that reaches a host means web access is on, never that the host answers. The browser's
readiness is decided by the page (§6 `entryState`). **`system.capability_audit`** `{state?, kind?, category?,
limit≤1000 (100)}` (B R) returns every entry against the runtime that answers: `{where, network, jobs,
datasets_built, counts:{entries, by_readiness, by_implementation, by_verification}, filters, matched,
entries:[{id, kind, category, implementation, readiness, requires, verification:{status, at}}], note}`; the runner
serves the same at `GET /api/capabilities/audit` (§4).

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
| `research_routes` {route?, question?} | system.routes | B R | the map from a research question to every source that answers part of it ([ROUTES.md](ROUTES.md), generated from `tcmstudio/routes.py`): for each kind of question the steps, and for each step every source (core tools, API operations, data hub relation kinds, native tools, jobs) with its call and the kind of evidence it gives; each source judged runnable here as catalog_search judges it (a relation kind: one of its datasets built; a job the browser runs on some inputs only, docking, RNA-seq and single-cell, by the inputs its call names, each such job named once per kind of input: `job.pipeline.dock (prepared inputs)`), one that cannot run naming the source in the same step that covers it and what that changes, and a step with nothing runnable said to be unanswered here, with what would answer it; with `question` alone, the routes its words match, the best in full; no argument lists them. It runs nothing, and every call it names keeps its own approvals, network permission and governance |
| `research_inquiry` {action, inquiry_id?, scaffold?, values?, question?, explanations?, analyses?, predictions?, rule?, size?, analysis_id?, analysis?, outcome?, evidence_ref?, data?, reason?, shares?} | study.inquiry | B R | rival explanations of one question compete in PSH's inquiry engine ([docs/inquiry.md](../../docs/inquiry.md#in-the-studio-chat)); kept per project as its verified trail; predictions sealed before any outcome; `plan` seals the engine's batch of 2–4 analyses to run at once, valued jointly; with `data`, the scaffold's classifier tool runs in the same call and the runtime classifies the outcome; a reported outcome needs `evidence_ref` and is counted apart; the stopping rule can be made stricter, never laxer; a conclusion says whether its leader holds with less sure predictions, and is not a release |
| `network_pharmacology_run` {formula, disease?, parameters?} | job.research.run | B R ★ | job; snapshot pipeline; in the browser a job of the page's job service ([BROWSER_JOBS.md](BROWSER_JOBS.md)) |
| `run_pipeline` {pipeline: rnaseq·scrna·fold·dock·admet, arguments} | job.pipeline.* | B R ★ | job; GPU-capable kinds honour the device setting. In the browser `rnaseq` with `counts` and `scrna` on count tables are jobs of the page's job service ([BROWSER_JOBS.md §3.1](BROWSER_JOBS.md#31-rna-seq-and-single-cell-from-count-tables)), and so is `admet` on a CSV/TSV/`.smi` file or one SMILES once its models are built here ([BROWSER_JOBS.md §3.2](BROWSER_JOBS.md#32-admet)); the router places the call by the kind its `pipeline` names, and one the browser cannot run (`fold`, `dock`; FASTQ, PyDESeq2, Scanpy, scVI, `.h5`, `.h5ad`, an ADMET SDF/`.mol` file, or ADMET models not built here) goes to a connected runner, or is refused with reason `needs_runner` and why (models not built here: `needs_dataset`; under compute Browser with the runner connected, `compute_browser` and why); a `pipeline.admet` call the page's job service refuses before queueing carries its need's reason: `needs_runner` (an SDF/`.mol` file named by its id), `needs_dataset` (models not built here) or `too_large_for_browser` (more than 20,000 molecules, counted in the file). `arguments` is an object, or the JSON text of one (read as that object, as the dispatcher reads it); a call without a `pipeline` of the catalog, with `arguments` that are not an object, or with the kind's parameters beside `arguments` is `bad_arguments` before it is placed or approved, the pipelines listed |
| `job_status` {job_id, wait_s≤60} | runner jobs; browser jobs (ids `bj-…`) | B R | a browser job's status is the page's to answer, whatever the placement |
| `capabilities_status` {} | system.capabilities | B R | what can run where, now; `remote_compute: {configured:false, reason}` (no remote backend exists; the page's answer says the same, §6) |
| `catalog_search` {query?, category?, kind?, runnable_now?, limit≤25} | the catalog | B R | |
| `call_tool` {tool, arguments} | any Entry id | per entry | the entry's own `confirm`/`network` apply; `arguments` that are not an object (JSON text that does not parse, or parses to something else) is `bad_arguments` in the dispatcher's words, before placement or approval |

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
    "where": "browser",                 // browser | runner | page (page only in JS: decided by the router, nothing ran; §6)
    "runtime": "pyodide-314.0.7 / CPython 3.14",
    "device": "cpu",                    // cpu | cuda:0 | mps | rocm:0 | webgpu (only for a JS/ONNX path);
                                        // a job's envelope: the device the runner assigned the job, once it has
    "versions": {"tcmstudio":"0.1.0","bioagent":"0.2.7","psh":"0.6.0"},
    "composite_version": "...",         // artifact.composite_version_string when present
    "content_hash": "e525e3ab…",        // skill content hash when present
    "audit_head": "…" | null,
    "input_sha256": "…",                // sha256 of canonical JSON arguments
    "output_sha256": "…",               // sha256 of canonical JSON result
    "started_at": "2026-10-07T12:00:00Z",
    // receipt/1 (additive; rules below). Absent `schema` = a legacy receipt, read as it was.
    "schema": "tcmstudio.receipt/1",
    "finished_at": "2026-10-07T12:00:01Z",   // ≥ started_at, same second-truncated UTC form
    "execution": {
      "runtime": "pyodide",             // cpython (runner) | pyodide (browser) | page (JS-made, §6)
      "kernel": "webgpu" | null,        // receipt.compute.backend verbatim (the 7 kernel tools), else null
      "engine": {"id":"salmon","version":"1.10.3"|null,"selected_by":{"arg":"engine","value":"salmon"},
                 "device":"cpu|gpu|remote","with"?:[…]} | null,   // the engine the arguments (or the run) chose
      "implementation": {"ref":"bioagent.tools.sequence:gc_content","version":"0.2.7", …} | null,
                                        // §2 entry.implementation + natives {component, entrypoint,
                                        // manifest_version, deterministic}; skills {content_hash, pinned};
                                        // connectors {key, operation, host}; jobs {job_kind, job_id?, pipeline?,
                                        // code_digest?}; null when nothing ran
      "validation": {"ok":true,"schema":"entry|core","repairs":["…"],"normalized_sha256":"…"}
                    | {"ok":false,"schema":"…","repairs":[…],"problems":["…"]} | null,
      "device": {"requested":"cpu","assigned":"cpu","used":"cpu"|null,
                 "evidence":"selected|reported|dispatched|validated|verified|accelerated"|null,"note"?:"…"}
      // JS only (router): "placement": {…}, "approvals": [{reason, scope, hosts}]   (§6)
    },
    "provenance": {
      "datasets": [{"id":"tcmscience.tcm.seed","version"?:"0.2.6","sha256"?:"…","retrieved_at"?:"…",
                    "licence"?:"MIT","release":"0.2.6"|null, …}],   // release null when the source states none
      "seeds": {"seed": 42},
      "models": [{"id":"esmfold","version":"…","requests":[{name, request_sha256, response_sha256}]}]
    },
    "result": {
      "kind": "record|computation|prediction|draft|fixture|system" | null,
      "outcome": "not_executed|queued|running|succeeded|validated|failed|cancelled",
      "assurance": "release_authorized|outputs_verified|none",
      "checks": [{"id":"schema_valid","ok":true,"detail"?:"…"}],
      "artifacts": {"expected":["…"],"missing":["…"],"hash_mismatch":["…"]},
      "fallbacks": [{"what":"device|compute|degraded","reason":"…"}],
      "reference": {…} | null           // only a comparison the call itself made against a reference
    }
  },
  "job": null | {"id":"j_…","kind":"pipeline.rnaseq","state":"queued"},
  "approval": null | {"reason":"network|job|confirm|remote_upload|first_runner_call|remote_runner","what":"…","hosts":["…"],"decision"?:"deny"},
  "error": null | {"type":"bad_arguments|unavailable|not_found|refused|declined|runtime_error|timeout|network_off","message":"…","hint":"…",
                   "requires"?: [Requirement], "reason"?: "needs_runner|no_runtime|missing_deps|missing_engine|too_large_for_browser|network_off|needs_dataset|needs_job_service|remote_upload_off"}
                                        // additive: the dispatcher's preflight names every missing requirement
                                        // (§2 Readiness) and the reason code; hint is the first remedy.
                                        // A job the runner's job service refuses keeps error.missing and mirrors it
                                        // in requires, in the words of the runner's catalog (packages: dependency;
                                        // the research snapshots: dataset, needs_dataset; any other sentence:
                                        // job_service, needs_job_service); allow_remote off is compute_setting
                                        // (remote_upload_off). The job service's own hint, when it gives one, stays.
                                        // shape() keeps them only when well formed (a list of {need: str, …}; a code)
                                        // the page's router adds its own codes: compute_browser, runner_offline,
                                        // unknown_compute, remote_not_configured, not_found (§6 PLACEMENT_REASONS)
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
  `input_sha256` hashes the arguments as the dispatcher received them, in canonical JSON with Python's number spelling,
  so it is comparable between calls whose client wrote the numbers the same way: JavaScript has one number type, so a
  page sends `3` where a Python caller (the CLI, `validate`, an MCP client in Python) may send `3.0`, and the two hashes
  differ for the same value (`native.anion_gap`'s catalog example, `albumin_g_dl: 3.0`). The browser runtime check and
  the sweep (`web/test/runtime/run.mjs`, `sweep.mjs`) give the native side the arguments as JavaScript writes them.
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

**Receipt `tcmstudio.receipt/1`** (`envelope.RECEIPT_SCHEMA`; built by `envelope.shape` from what `dispatch._finish`
gives it, so the runner and the browser runtime write the same fields with the same code):
- **Additive.** Every older receipt field keeps its meaning (`device`, `compute`, `kernel`, `adapter`, `request`,
  …). Nothing in it is hashed: `result`, `governance.outputs` and `output_sha256` are byte for byte what they were. A
  receipt without `schema` is a legacy one (stored before this version); nothing is derived for it as if recorded.
  `shape` keeps a given `execution`/`provenance`/`result` only where well formed, and it replaces an outcome the
  status does not admit with the status's own. That means `refused`/`needs_approval` → `not_executed`,
  `job_submitted` → `queued`, `cancelled` → `cancelled`, and `failed` → `failed` or `not_executed`. A `succeeded`
  `job_status` poll may report any job state.
- **`execution.validation`** is the check of the arguments against the entry's schema (`schema:"entry"`), or the core
  tool's when it failed there (`"core"`). `repairs` are the harmless slips it fixed. `normalized_sha256` is the SHA-256
  of the canonical JSON of the arguments the executor was given, so it is equal across key orders and repairs, unlike
  `input_sha256` (the raw call). For an entry that carries patient data (`phi`, or one that takes a clinic `intake`)
  repairs and problems name the argument's path only, never its value. No receipt field copies such an argument.
- **`execution.device` and the evidence ladder** `selected < reported < dispatched < validated < verified <
  accelerated`. An in-process call has the CPU it ran on, `selected`. When the browser used the GPU counts, `assigned` and
  `used` are `webgpu`, as `receipt.device` says, with `evidence:"validated"` (Python's recount), raised to `verified`
  (the device's known-answer test) or `accelerated` (measured faster than a CPU kernel on that device, physical adapter
  only) by the Worker from `receipt.compute.gpu`, never from an estimate (CONTRACT_NOTES, browser-compute). A job has `requested` (the runner's device setting), `assigned`
  (`job.device`, set when it starts; `null` and a note while queued) and `evidence:"selected"`. `used` is `cpu` only
  for a CPU job, and `null` for a GPU job: hiding the other GPUs is a selection, and no job reports the device its
  engine used. `receipt.device` of a job envelope is that assigned device, and `null` until the runner assigns one (a
  queued job, every `job_submitted` envelope), so a GPU job is never reported as `cpu`.
- **`execution.kernel`** mirrors `receipt.compute.backend` (`browser_compute._mirror`): `pyodide` for a call that
  failed whatever helped it, `null` where no compute receipt was written. `receipt.compute` stays the authority on
  the kernels.
- **`provenance`** comes only from what the call holds: a governed run's source cards (`artifact.sources`, with
  `snapshot_hash` as `sha256`); the seed corpus card for the knowledge-base lookups, with the formula workbook's
  `source_sha256` when its records were read; a live source's `host` and `retrieved_at` (`request.fetched_at`)
  with `release: null`, because the APIs state none. It also takes the dataset (and its version) of each data-hub row,
  a job's `inputs` with their SHA-256 (`role:"input"`), and seeds (arguments named `seed`, `random_seed`,
  `random_state`, `*_seed`, with the schema default the code uses). From a succeeded pipeline's `run.json` it takes
  the seeds of its configuration, its fold models (method, version, request and response digests) or ADMET model-card
  digests. That record is read through `context.jobs.run_manifest(job_id)`, only at the digest recorded when the job
  was collected (§4 `outcome.checks`/`expected`).
- **`result.kind`**, set only when the call produced a result (`succeeded`/`validated`), by precedence:

  | kind | when |
  |---|---|
  | `fixture` | a source declares itself one: a version with `+planted` or `+fx` (bioagent.research.planted) |
  | `draft` | the clinic, or `LIMIT_DRAFT` in the limitations (the prescription draft skill) |
  | `prediction` | `LIMIT_PREDICTED` in the limitations, or a predictive job (fold, dock, ADMET, a predictive skill) |
  | `record` | connectors, literature search, `system.provider_call`, the data hub, the knowledge-base lookups and hub-backed natives, record skills (normalize, evidence, safety), the data jobs (`tcmdb.fetch`/`build`, `research.snapshot`) |
  | `system` | system operations other than `job_status` (which takes its job's kind), `env.provision` |
  | `computation` | everything else |

- **`result.outcome`**: `not_executed` for a call no executor ran: refused, awaiting approval, arguments that failed
  validation (or that the job service refused before queuing anything), or not runnable here (`unavailable`,
  `network_off`, an unknown tool), as the page's own envelopes say (`pageOutcome`); a job's own state for a job
  (`queued`, `running`, …); `failed` for one that ran and failed (an executor's own `bad_arguments` or `not_found`
  included); `validated` ⇔ `succeeded` and an assurance other than `none`.
- **`result.assurance`** is never "verified" alone. `release_authorized` means the governed verdict says released and
  all six states (`schema_valid` … `release_authorized`) are true: the kernel licensed the claims as stated. It is not
  a statement that the conclusion is true: a prediction stays a prediction. `outputs_verified` means a succeeded
  job's declared outputs were digested at collection (`outcome.checks` names `controller_digests` or
  `runner_digests`), none it requires is missing, and none is known to have changed since: `run.json` and a
  `skill.run` job's `envelope.json` are read back at their recorded digests when the job is polled; other artefacts
  are not re-hashed then (a mismatch at collection already fails the job). One read back that fails its check is not
  verified, whatever was recorded: a changed file is listed in `artifacts.hash_mismatch`, one that can no longer be
  read (deleted or moved since) in `artifacts.missing`, and the assurance is `none`. That covers hashes only, not the
  science. Anything else is `none`. An envelope stored before `receipt.result` existed is derived in the page
  (`resultOf`, `derived: true`) as `outputs_verified` only for a collected job whose artefacts each carry a `sha256`.
- **`result.checks`**: a governed skill's six verdict states, then `skill_pinned` and `outputs_present`. A job adds
  the checks its collection passed (`outcome.checks`: `controller_digests`, `validator:<artefact>`, `kind.verify`,
  …), or one `collection` failure per problem. It may also add `run_manifest` or `envelope_digest` (read back at the
  recorded digest, or not). **`artifacts`**: `expected` (the governed outputs, the kind's required artefacts), `missing`,
  `hash_mismatch` (an output whose digest no longer matches). **`fallbacks`**: `device` (a job's `device_note`
  when the selected device was not available), `compute` (WebGPU counts Python rejected, `compute.gpu_rejected`; and,
  added by the browser Worker, a GPU that was tried and failed or set aside for the session; never a CPU path chosen
  by preference, size, input shape or a software adapter), `degraded` (a kernel call that succeeded in a degraded mode). **`reference`**: only a comparison the call made
  against a reference (a fold compared with a given structure), never filled in otherwise.

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
| GET | `/api/catalog` | the catalog (§2), with `exec` reflecting this runner (heavy deps probed: `available:false` + `missing:[…]` per entry) and each entry's `state.readiness.runner` and engines' `readiness.runner`, brought up to date on every read (this runner's network switch; its job service and what each kind lacks, and the datasets built in its data hub, re-read every few seconds). A change makes a new document; one already being sent is never changed |
| GET | `/api/capabilities/audit` | `?state=&kind=&category=&limit=` (≤ 1000) → `{where:"runner", network:{enabled}, jobs, datasets_built, counts:{entries, by_readiness, by_implementation, by_verification}, filters, matched, entries:[{id, kind, category, implementation, readiness, requires:[Requirement], verification:{status, at}}], note, versions}`: the same function as `system.capability_audit` (§2), over this runner's catalog. Filters narrow `entries`, never `counts`. No request is made to any host: `ready` for an entry that reaches one means web access is on. `400` for an unknown `state` or a bad `limit` |
| POST | `/api/call` | `{tool, arguments, context:{project_id?, conversation_id?, approvals?:[…]}}` → envelope (§3) |
| GET | `/api/devices` | `{devices:[Device], selected:"auto"}` where `Device = {id:"cpu"|"cuda:0"|"mps"|"rocm:0", kind, name, memory_gb?, driver?, available:bool, note?, evidence:"reported", reported_by}`: `evidence` is the rung of the evidence ladder (§3) the runner reaches for a device, `reported` at most (nothing in the runner runs a kernel on it), and `reported_by` what reported it (`operating system`, `nvidia-smi`, `rocm-smi`, `platform (Apple Silicon)`, `PyTorch <version>`) |
| GET | `/api/settings` / PUT | `Settings = {device:"auto"|id, threads:int, max_jobs:int, network:{enabled:bool, profile:"offline-analysis"|"biomedical-research"}, purpose:"academic"|"commercial", allow_remote:bool}` |
| GET | `/api/kinds` | `[{kind, title:{zh,en}, parameters:JSONSchema, available, missing:[…], gpu, duration:"seconds"|"minutes"|"hours"}]` |
| POST | `/api/jobs` | `{kind, params, project_id?, submission_id?}` → `201 Job` (same `submission_id` → the existing job) |
| GET | `/api/jobs` | `?project_id=&state=` → `{jobs:[Job]}` |
| GET | `/api/jobs/{id}` | `Job` |
| DELETE | `/api/jobs/{id}` | cancel → `Job` (409 if already finished) |
| GET | `/api/jobs/{id}/events` | SSE: `event: state|log|progress|stages|artefact|done`, `data: JSON`, each written as it happens; keep-alive comment every 15 s; `?token=`. A (re)connection starts with `state` and up to 80 recent `log` lines. `progress` carries `Job.progress`; `stages` `{stages}`, `Job.stages` each time a running pipeline's `stages.json` moves on. |
| GET | `/api/jobs/{id}/files` | `{files:[{path, bytes, sha256, media_type}]}` |
| GET | `/api/jobs/{id}/files/{path}` | bytes; HTML is served with `Content-Security-Policy: sandbox` |
| POST | `/api/uploads` | raw body; headers `X-Filename`, `Content-Type` → `{id, name, bytes, sha256, media_type}` |
| GET | `/api/uploads` | `{uploads:[…]}` |
| GET | `/api/datasets` | `{count, datasets:[{key, name, access, version, license, licence_class, commercial_use, redistribution, hosts, files_default, bytes_default, acquirable, readiness:{state, next_step}, local:{built, files_present, files_default, missing_default, verification}, live_record}], network:{enabled}}`: every registered hub dataset (`tcmdb.datasets`). `local` is what this runner holds; `live_record` (also in the manifest) is CI's committed record of real downloads of the dataset's files (`bioagent/data/live_download_verification.json`): `{files_pass, files_recorded, files, run, at, record}`, or `null` when no live run reached any of them. It says what that run found on its day, not what is on this runner |
| GET | `/api/datasets/{key}/manifest` | `{dataset, name, access, version, homepage, license, licence_class, commercial_use, redistribution, licence_record?, hosts, files:[{name, url, host, table, fmt, optional, expected_bytes, reviewed_checksum, local:{present, bytes?, verification?, record?, part_bytes?, resumable?}}], size_gate_bytes, store:{built, files?, build?}, live_record, audit:[its audit lines, the last 20], instructions?}`; `404` for an unknown key |
| GET | `/api/datasets/{key}/readiness` | `{dataset, access, state, next_step, missing?, local, network:{enabled}, acquire?:{method, path, body}, check?:{ok, problems, warnings}, requires:[Requirement]}`; `state` is one of the six of `tcmdb.status` |
| POST | `/api/datasets/acquisitions` | `{dataset, include_optional?, project_id?, submission_id?}` → `201 {acquisition, plan:{files, download_bytes, unknown_sizes, hosts, size_gate_bytes}}` + `Location: /api/datasets/acquisitions/{id}` (same `submission_id` → `200`, the existing one). Creates a `tcmdb.fetch` job. `400 bad_arguments` for any other field (no URL, no size-gate override), a key that is not registered, one that is not downloadable, or one whose every file is optional asked for without `include_optional: true` (it would fetch nothing; the Data page and `…/readiness`'s `acquire.body` pass it, and `job.tcmdb.fetch` refuses the same call); `409 conflict` (`error.job`) while the dataset is already being acquired (a queued or running `tcmdb.fetch` of it), or when `submission_id` names another job (another kind, dataset or `include_optional`); `422 network_off` when the runner's network is off |
| GET | `/api/datasets/acquisitions` | `?project_id=&state=` → `{acquisitions:[{id, dataset, state, url, job}]}` |
| GET | `/api/datasets/acquisitions/{id}` | `{id, dataset, state, url, job:Job, files:[{file, url, host, state, have_bytes, total_bytes, expected_bytes, reviewed_checksum, verification?, check?, reason?, last_attempt?}], states, summary:{bytes, files, verified, fraction?, stage?}}` (`fraction` and `stage` only while the job runs, as in its progress); `404` for a job that is not a `tcmdb.fetch` |
| DELETE | `/api/datasets/acquisitions/{id}` | cancel → the acquisition (`409` once finished). `POST /api/datasets/acquisitions/{id}/cancel` is the same |
| GET | `/api/llm/local` | probes local model servers → `{servers:[{id:"ollama"|"lmstudio"|"vllm"|"llamacpp", base_url, ok, models:[…]}]}` |
| POST | `/api/llm` | model proxy: header `X-TCM-Target: <full URL>`, body forwarded, response streamed back. The target must be loopback, or a host in the provider allowlist (OpenAI, Anthropic, DeepSeek, DashScope, Moonshot, Zhipu, SiliconFlow, OpenRouter, and `--allow-host`). The key travels in the request's own `Authorization`/`x-api-key` and is never stored or logged. |
| GET | `/` and static paths | the web app (bundled `web/` or `--web DIR`), so `http://127.0.0.1:8765/` works offline |
| GET | `/v1/health` | `200 {ok:false, service:"tcmstudio", relay:false, error:{type:"not_relay", code:"runner_no_relay", port:8765, message, message_en}}`: the runner never relays Tao-S1 (`message` is Chinese, `message_en` English; the page localises by `type`). A page it serves on a port other than 8765 looks for the relay on its own origin and reads this; other `/v1/*` paths are `404 not_relay` with the same `code`, `port` and `message_en`. |

`Job = {id, kind, state:"queued"|"running"|"succeeded"|"failed"|"cancelled", params, project_id,
created_at, started_at, finished_at, progress:{fraction?, message?, stage?, bytes?, files?, verified?}|null, device, outcome:{status, error?, problems?,
checks?, expected?, receipt?}|null, artefacts:[{name, sha256, bytes}], files_url, stages?, limitations?, receipt?}`. A job is
`succeeded` only after its outputs are collected and verified (digest + pipeline `verify_run`). Until then it is pending, and
its result is never shown as an answer.
- **Pipeline stages.** A pipeline job (`pipeline.*`) records its stages in `out/stages.json` (bioagent.runrecord,
  schema `bioagent.stages/1`). `stages:[{name, required, status, check, device, seconds}]` follows it while the job runs, and
  comes from `run.json` once collected. `status` is `succeeded|failed|skipped|unavailable|not_applicable`; `check` is a
  validation status or null.
- **Progress.** `progress.stage` is the stage running, or `"verifying"` while the job is collected. `progress.fraction` is
  the share of required stages finished, out of their number plus one: the runner's own verification counts as the last
  step, so a fraction never reaches 1 before the job succeeds.
- **Completeness.** A pipeline whose run is incomplete exits 1, or its `run.json` says `complete:false`, and the job is
  `failed`. Incomplete means a required stage did not succeed or a check failed (no ligand docked, redocking beyond 2 Å,
  ADMET models missing). `outcome.problems` then lists the stages at fault (`stage <name> …`).
- **Limitations.** `limitations` holds the lines of the collected `out/limitations.md`, one limitation per line (at
  most 100 lines of at most 1,000 characters; a longer line is cut with `…`, and a last line says how many more are in
  the file). `system.job_status` appends them to `governance.limitations` for a succeeded pipeline job.
- **Reading the job's own record.** The runner reads `out/stages.json`, `out/limitations.md` (at most 1 MB each) and
  `out/run.json` (at most 16 MB) only as regular files: a symlink, or a file over its budget, is not read and counts as
  absent. Hidden temporary files (`.<name>.tmp`, a writer's atomic rename) are not announced as `artefact` events. A
  stage record that cannot be read (an entry that is not an object, a version that is not text) never stalls
  collection: such entries are left out, and a receipt that still cannot be written fails the job with that as a
  problem.
- **Job receipt.** Collection writes `out/job_receipt.json` for every job that started, failed and cancelled ones too.
  `outcome.receipt` and `Job.receipt` are `{path:"job_receipt.json", sha256}`. Its schema is `tcmstudio.job-receipt/1`
  (never named `run_manifest.json`):

  `{schema, job_id, kind, state, project_id, submission_id, params_sha256, inputs:{param:{sha256, bytes}}, engine:[{id,
  version, stages}]|null, device:{assigned, evidence:"selected", note, stages:{stage: device}|null}, started_at,
  finished_at, duration_s, exit_code, timed_out, artefacts:[{name, path, sha256, bytes}], expected, missing, checks,
  problems, error, stages, complete, run_json_sha256, limitations, resources}`.

  `resources` is `{peak_rss_mb, cpu_user_s, cpu_system_s, source, scope}`, from the job supervisor's
  `getrusage(RUSAGE_CHILDREN)` (`exit.json`). The peak is that of the largest single process the job waited for, not the
  sum over its process tree. Without that record it is `{peak_rss_mb:null, reason}`. `JobService.job_receipt(job_id)` (and
  the hook's) reads it back only at its recorded digest.
`outcome.checks` (succeeded jobs) lists what the collection passed, in order: `controller_digests` (every declared
artefact digested by the controller; `controller_exit` for a kind that declares none), or `runner_digests` and
`required_artefacts` for an exit code that is a result; then `validator:<artefact>` (only for an artefact that was
there to validate: an absent optional one is not listed) and `kind.verify`.
`outcome.expected` names the artefacts the kind requires. `JobService.run_manifest(job_id)` (and the hook's) reads a
succeeded job's `run.json` only while it has the digest recorded at collection, like `envelope(job_id)` (§3 receipt/1).

- **Dataset acquisitions** (`tcmstudio.datasets`, `bioagent.acquisition.service`). An acquisition is a `tcmdb.fetch`
  job: `bioagent tcmdb fetch <key> --report={output}/acquisition.json`, run on the runner, never through the page or the
  bounded `/api/sources` gateway (whose 8 MiB / 30 s limits stay). Only a registered key's reviewed files are fetched.
  Each file goes through `acquisition.transfer.VerifiedTransfer`: a partial file is resumed (`Range` + `If-Range`, and
  only a `206` whose `Content-Range` is `bytes N-(total-1)/total` and whose ETag, or Last-Modified, is the recorded
  one) only onto the same file — a strong ETag, or a Last-Modified date its answer's `Date` shows to be strong (RFC 9110
  §8.8.2.2), recorded beside it in `<name>.part.json`, or a reviewed checksum that checks the joined file; otherwise
  it restarts from byte 0. A partial file already whole is checked as it is, not fetched again. Timeouts, dropped
  connections, 408/425/429/5xx are retried (a `Retry-After` up to 60 s; a longer one ends the transfer); 404/410 fail
  at once; a full disk stops at once and keeps the partial file. The disk is checked against the server's size before
  any GET; a second transfer of the same file is refused while one holds its lock (and a second acquisition of the
  same dataset with `409`); a cancelled fetch stops at the next block that arrives and its process is ended 10 s after
  the cancel at the latest; gzip/bzip2/xz/zip/xlsx files are read through their CRC and Parquet checked for its magic
  before a file is kept. A file with a reviewed checksum that the research snapshots' raw directory already holds is
  adopted as a hard link (route `local-mirror`), not downloaded again, and `research.snapshot` adopts the hub's copies
  the same way, so NPASS, CMAUP and LOTUS are stored once. `files[].state` is `pending` (a kept `.part` resumes),
  `transferring`, `kept`, `refused` (it never arrived) or `rejected` (it arrived and `check` — checksum, size,
  decompression, format or html — refused it). The job's `result.acquisition` lists each file's status, verification,
  route and `resumed_from`; the `.downloads.json` record and `audit/downloads.jsonl` add `sha256`, `resumed`,
  `resumed_from`, `etag`, `last_modified`, `final_url`, `server`, `retrieved_at`, `release`, `restarts`. The job
  fails when a default file is missing or a file is recorded as `mismatch` (not the reviewed file).
- **Fetch progress.** A `tcmdb.fetch` job's `progress` is read from the bytes on disk:
  `{fraction, bytes:{done, total, unknown?}, files:{done, total}, verified:{checksum, size, none, mismatch?},
  stage:"transferring"|"verifying", message}` (`mismatch`, present only when non-zero, counts kept files recorded with
  another checksum than the reviewed one; the job then fails). `fraction` is bytes done over bytes expected when every
  file's size is known (declared, or the server's for a file whose transfer started), the share of files otherwise,
  and stays below 1 until the job is collected; while it is, `bytes`, `files` and `verified` stay and `stage` is
  `"verifying"`.
- **Licences.** `redistribution` is a reviewed licence record's statement (`registry/licence_records.yaml`, the same
  dataset, and a record version that names its release as a whole token; never for a dataset with no release or a
  rolling one); without one it is `"unknown — downloading is not permission to redistribute"`. Files stay in
  the runner's data directory. There is no object storage (R2 or other) and no signed-URL sharing: not implemented,
  since nothing could be deployed or verified here and a shared copy of an unlicensed dataset would be redistribution.

**Job kinds:** `pipeline.rnaseq`, `pipeline.scrna`, `pipeline.fold`, `pipeline.dock`, `pipeline.admet`,
`admet.models` (build the ADMET models), `research.run`, `research.snapshot` (download, verify and build the research snapshots), `env.provision` (install
one group of optional scientific packages), `skill.run` (any governed skill as a background run), `tcmdb.fetch`,
`tcmdb.build`.
Each kind maps typed params to a fixed `bioagent.cli` argv (never a raw command from the browser).

- **`pipeline.rnaseq`** has two modes. Without `counts`: a FASTQ sample sheet (`samples`) with `transcripts`,
  `annotation`, `genome`, `engine`, `trimmer` as before. With `counts` (a gene × sample count table, TSV/CSV, integers):
  the count-matrix mode, `samples` being the sample table (`sample` and the design columns); both are plain files
  (upload ids or file names), `transcripts`, `genome`, `annotation`, `engine` and `trimmer` are refused
  (`bad_arguments`, "a count-matrix run takes no FASTQ reference or engine: …"), and so is a FASTQ sheet given as
  `samples` ("samples: with counts this is the sample table (sample and the design columns); a FASTQ sample sheet runs
  without counts", `kinds.RNASEQ_COUNTS_FASTQ_SHEET`, the browser's words too). The FASTQ-option check reads no
  file (`Kind.check`): the dispatcher makes it before it looks for an engine, so a count-matrix call naming a FASTQ engine
  is `bad_arguments` wherever it would run, never a tool to install. `design`, `contrast`, `de_backend` (`builtin`·`pydeseq2`) and `alpha` (0.0001–0.5) are common to both. Its
  argv: `bioagent.cli rnaseq --counts=… --samples=… --design=… --de-backend=… --alpha=… --threads=<job threads>
  [--contrast=…] --out={output} --json`; the same artefacts (`run`, `report`, `results`, `limitations`) and validator.
- **`pipeline.scrna`** takes `n_top_genes` (highly variable genes, an integer 50–10000, default 2000), passed as
  `--n-top-genes`, with `source`, `doublets`, `analysis_backend`, `integration`, `resolution` (0.05–5), `markers`,
  `root`, `contrast` and `seed` as before.
- **`pipeline.admet`** takes `molecules` (a CSV/TSV `name,smiles` file, an `.smi` file, one SMILES, or — on the runner —
  an SDF/`.mol` file) and `endpoints` (endpoint names; on the runner one without a model makes the run incomplete, as
  `bioagent admet --endpoint` does, and the browser refuses a name that is not one of the 22 TDC endpoints as
  `bad_arguments`). Its argv: `bioagent.cli admet {molecules} --out={output} --cache=<lake>/admet [--endpoint=…]`;
  artefacts `table` (admet.tsv), `run` (run.json) and `limitations` (limitations.md), the run validated by
  `bioagent.admet.pipeline`'s verifier.
- **`admet.models`** builds the models `pipeline.admet` predicts with (`bioagent.cli admet --build-models
  --cache=<lake>/admet --max-iter=… --seed=… [--archive=…] [--rebuild] [--endpoint=…]`): `endpoints` (TDC endpoint
  names, refused as `bad_arguments` when not one of the 22), `archive` (a local copy of the TDC ADMET group zip; without
  it the archive is downloaded, so the entry's `network` is true unless `archive` is given, `network_unless:
  "archive"`), `max_iter` (10–5000, default 600), `seed` (0–2³¹−1), `rebuild`. Each endpoint's card,
  model and applicability-domain file are written to the cache; a finished endpoint whose model still matches its card
  is kept unless `rebuild`; after the job ends the cards are checked against their files (`verify_models`).

**Browser jobs.** `research.snapshot` and `research.run` also run in the page's own job service
(`studio/web/js/scientific`, [BROWSER_JOBS.md](BROWSER_JOBS.md)): the same typed params, the same Python functions
(`tcmstudio.browser_jobs`), job records in IndexedDB `tcmstudio-jobs`, files in OPFS, states `queued`, `running`,
`succeeded`, `failed`, `cancelled` and `interrupted` (the page closed while it ran; resumable), and the same
`tcmstudio.job-receipt/1` with `placement: "browser"`, the workers and two independent readings of every output's
digest. So do `pipeline.rnaseq` in its count-matrix mode and `pipeline.scrna` on count tables
([BROWSER_JOBS.md §3.1](BROWSER_JOBS.md#31-rna-seq-and-single-cell-from-count-tables)), through
`tcmstudio.browser_jobs.run_rnaseq_counts` (`{counts, samples, design?, contrast?, alpha?, de_backend?, out_dir}`),
`run_scrna` (`{source, doublets?, analysis_backend?, integration?, resolution?, seed?, markers?, root?, contrast?,
n_top_genes?, out_dir}`; a directory `source` is need `runner`, one without a Cell Ranger output's files refused as the
command refuses it) (each → `{exit, summary, failed, refused, need}`: exit 0 complete, 1 incomplete, 2 refused,
`need` `runner`, `memory` or null) and
`verify_pipeline` (`{kind, out_dir}` → `{status, checks, problems, report}`, the runner's checks under the runner's
names). Their file params name files of the job's project (an id, or a name: the newest file of that name), copied
into the job's OPFS directory and refused unless they have the SHA-256 the project recorded; the receipt's `inputs`
records each (`{sha256, bytes, source}`; a sample sheet's tables under `source.files`). Refused before anything is
queued: what `execution.browser_job.runner_only` names (`unavailable`, need `runner`); the runner's bounds, unknown
params, `counts` with a FASTQ sample sheet as `samples` (the runner's words) and a name that is no file of the project
(`bad_arguments`; for a `source` that is no table, `.h5` or `.h5ad`, the refusal adds that a Cell Ranger directory is
read by the runner: a directory cannot be a project file). Refused for memory (type `refused`, need `memory`), each
input counted a line at a time before any is read in full: more than `SC_CELLS_MAX` (6,000) cells, a table of more
than `SC_TABLE_COUNTS_MAX` (66,000,000) cells × genes, a count matrix of more than `RNASEQ_COUNTS_MAX` (10,000,000)
genes × samples or of more than 100,000 genes or 200 samples, and a `MemoryError` during the run; the worker is then
retired. A gzipped input that is corrupt or cut short, and a single-cell table that is not UTF-8, are refused with
the reason (`refused`, need null), as the command refuses them. The analysis stage's checkpoint digest covers its
params, its inputs' digests and what computes it (the bundle's SHA-256, Pyodide's version, the numpy and scipy the
worker reports): a job resumed on a changed runtime runs the analysis again.

So do `admet.models` and `pipeline.admet` ([BROWSER_JOBS.md §3.2](BROWSER_JOBS.md#32-admet)). The page computes two
parts itself (the chemistry, in a module worker with RDKit.js 2026.9.1 and bioagent's ports, written as prepared sets
`bioagent.admet.engine.PrecomputedEngine` reads back; and the applicability-domain Tanimoto search, on this device's
WebAssembly SIMD or WebGPU kernel, written as similarities `PrecomputedSimilarity` checks before use), and calls
bioagent's Python through `tcmstudio.browser_jobs` `admet_split` (`{archive, out_dir, endpoints?}` → the endpoints and
their split sizes, the archive's SHA-256 and whether it is the pin), `admet_train` (`{archive, prepared_dir,
models_dir, endpoint, seed?, max_iter?, rebuild?}` → the endpoint's card; it merges the model, domain file and card
into `models_dir`), `admet_read` (`{molecules, out_dir}` → `{n, file, smiles_sha256}`; an SDF/`.mol` file is need
`runner`, more than 20,000 molecules need `memory`), `run_admet` (`{molecules_file, prepared_dir, similarity_dir?,
models_dir, endpoints?, out_dir}` → `{exit, summary, failed, refused, need}`, exit 0/1/2 as the CLI ends, a molecule
the chemistry refused listed under `refused`) and `verify_pipeline` (for `admet.models`: `{kind, models_dir,
endpoints?, archive_sha256?}` → the cards' model and domain files re-hashed; for `pipeline.admet`: `{kind, out_dir}`).
A `pipeline.admet` call is refused before queueing when its models are not built here (need `dataset`, the hint naming
`job.admet.models`), for an SDF/`.mol` file (need `runner`) or for more than 20,000 molecules (need `memory`); the
molecules are checked before the models. The models live in OPFS `tcmstudio/admet/models/`, shared by every ADMET job
here; a stage whose kernel calls ran the domain search carries their compute facts, so the job receipt gains `compute`
and, when a stage used a GPU result, `device.used` is `cpu+webgpu` (§3 receipt/1).

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
- Device report: `{cores, memory_gb, cross_origin_isolated, webgpu:{api, adapter:{kind, vendor, architecture, device, description, fallback, reason, software}|null}}`:
  `adapter` is `classifyAdapter()`'s answer (runtime/webgpu.js), `kind` software | physical | unidentified, and `software` is true, false, or
  null for an unidentified adapter; `compute.webgpu_adapter` is true only for a physical one and `compute.webgpu_adapter_kind` names the kind,
  plus `python` and `compute` (the local backends: CONTRACT_NOTES, browser-compute).
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
export function onSettingsChange(fn) → off;  export const COMPUTE_TARGETS  // ["auto","browser","runner","remote"]

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
Catalog.search(q, {category, kind, runnableNow, isRunnable, limit,
                   where, readiness, needsDataset, verified, backend, context})   // filters for the catalog page:
  // where "browser"|"runner" (exec has it); readiness: a state (or list) in `where`, else in either runtime, as
  // entryState(e, context) decides; needsDataset true|false|<dataset key>; verified: a level that passed
  // ("live_upstream") | a status ("PASS", "UNVERIFIED") | true/false; backend: a browser compute backend, an engine id
  // or a device ("webgpu", "esmfold", "gpu")
export function entryState(entry, ctx) → {implementation, readiness: {browser: R, runner: R}}   // R = {state, requires}
  // ctx: {browser, runner (statuses), web, built (dataset keys known built in the browser), packages (Pyodide packages
  // this browser can load, when known), args}. browser: decided in the page (a dataset counts only when known built;
  // an unknown package list is not held against the entry); runner: the runner catalog's state.readiness.runner while
  // it is connected (derived:true from available/missing for an older runner), needs_runner otherwise; both add the
  // project's web access. implementation: state.implementation, else derived conservatively (registered/implemented/tested)
export function requirement(need, {names?, detail?, remedy?}) → Requirement   // {need, detail, remedy, names?}, English
export function readinessOf(requires, given) → state   // the readiness precedence over a list of Requirements (READINESS order)
export const NEEDS, READINESS, IMPLEMENTATION, COMPUTE_VALUES, REMOTE_COMPUTE   // REMOTE_COMPUTE = {configured:false, reason}
export function mergeCatalogs(built, runner)   // the runner wins on exec/available/missing/…; never on the browser's
  // (the document's verification_records, Catalog.verificationRecords, are kept by the constructor, toJSON and the merge)
  // readiness (state.readiness.browser), its compute backends (execution.browser_compute) or its request limit
  // (limits.max_request_bytes.browser, per entry and for the document)

// core/receipt.js — the receipt of every envelope made in the page (§3)
export const RECEIPT_SCHEMA = "tcmstudio.receipt/1";  export const DECIDERS, OUTCOMES, ASSURANCES, RESULT_KINDS;
export function baseReceipt({where, runtime, device, versions, decided_by, input_sha256, started_at, output_sha256?,
                            outcome?, kind?}) → receipt   // + schema, finished_at, execution {runtime:"page"}, result
export function pageOutcome(status, error) → "not_executed"|"failed"|"cancelled"

// core/router.js
export class ToolRouter {
  constructor({catalog, runtimes, settings, approvals})
  where(toolOrEntry, args?) → "browser"|"runner"|"page"|null   // "page": the two catalog tools, answered by the page
  placement(toolOrEntry, args?) → {where, reason, message, requires: [Requirement]}   // choice + why + what it needs
    // reason: ok | not_found | missing_deps | compute_browser | no_runtime | runner_offline | needs_runner
    //       | unknown_compute | remote_not_configured | too_large_for_browser | missing_engine   (PLACEMENT_REASONS,
    //       which also lists human_only: placement provenance of a HUMAN_ONLY refusal, never an unavailable reason);
    //       what the connected runner lacks may also give needs_dataset | needs_job_service (RUNTIME_REASONS, §6)
  async call(name, args, {projectId, conversationId, signal, onApproval}) → Envelope   // never rejects
    // enforces confirm/network approvals: if needed, awaits onApproval(request) → "once"|"project"|"deny"
}
export function hostsOf(entry, args?) → [host]; export async function hostsDigest(hosts) → sha12

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
export function resultOf(envelope) → receipt.result   // {kind, outcome, assurance, …}; derived:true for a legacy envelope
export function resultKindOf(envelope) → kind|null
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

Router rules (`core/router.js`):
- **Placement.** `compute` `browser` and `runner` are strict. `auto` sends everything a connected runner can run to
  the runner, and the rest to the browser. It is runner-first on purpose: a project's skill and clinic runs write one
  durable audit chain, and splitting a project across two runtimes would fork that chain. It also keeps one set of
  installed versions and one dataset store per project. `remote` is a valid preference that no backend serves yet:
  every call is `unavailable` with reason `remote_not_configured` (need `remote_backend`). It never falls back to the
  runner or the browser. A value the page does not know (for example `gpu`, or one saved by a newer version) gives
  `unknown_compute` (need `compute_setting`); it is never read as `auto`. `catalog_search` and `capabilities_status`
  are always answered in this browser under `browser`, `remote` or an unknown value (nothing leaves the page). Only
  `runner` or `auto` may send them to a connected runner. Under `remote` or an unknown value the page answers them
  itself even when the browser runtime is ready (`where:"page"`; that runtime does not know the preference and would
  list entries as runnable that the router refuses), so `runnable_now` and `needs` agree with what the router does.
  `call()` never rejects: a fault in the page itself gives a `failed`/`runtime_error` envelope that says so.
- **Requirements.** A placement with no place carries `requires: [Requirement]`, where `Requirement = {need, detail,
  remedy, names?}`. `detail` and `remedy` are English sentences. `need` is one of `runner | browser_runtime |
  dependency | dataset | network | job_service | compute_setting | remote_backend | memory | authentication`. Each
  reason maps to one need: `needs_runner`/`runner_offline` → runner; `missing_deps`/`missing_engine` → dependency;
  `compute_browser`/`unknown_compute` → compute_setting; `no_runtime` → browser_runtime; `remote_not_configured` →
  remote_backend; `too_large_for_browser` → memory. What an entry lacks on the connected runner is read as the
  runner's catalog classifies it (`state.readiness.runner`, else its `missing` list classified as
  `tcmstudio.capability` does it: package names a dependency, the research snapshots a dataset, any other sentence the
  job service), and the reason is that of its first need: `missing_deps` → dependency, `needs_dataset` → dataset,
  `needs_job_service` → job_service (core.router.runner_lacks); so a dataset is never a package to install. The
  unavailable envelope has `error = {type:"unavailable", message, hint: <first remedy>, reason, requires}` and its
  `text` adds `Needs: <details>`. A `network_off` envelope has `error.reason: "network_off"`, `requires:
  [{need:"network", names: <the hosts it would reach>}]` and the first remedy as `hint`, as the dispatcher's has.
  `catalog_search` `needs` are the details, byte for byte as before for package names. `remote` and an unknown value
  add their own.
- **too_large_for_browser.** The router measures the UTF-8 length of the canonical JSON of the arguments the entry
  receives. It compares that with `limits.max_request_bytes.browser` (the entry's own value, else the catalog
  document's). A larger call is never sent to the browser. Under `auto` the runner, when connected, takes it as
  before. Under `browser` it is unavailable, with the need for another compute setting. For an entry only the browser
  runs, the one remedy is a smaller input (`core.router.too_large_for_browser_only`). Not yet one measure: the
  Python `_preflight` counts `json.dumps(args, ensure_ascii=False)`, whose `", "`/`": "` separators make it a little
  larger, so a request within that margin of the limit passes here and is refused in Pyodide (with its own size).
- **missing_engine.** The engines the arguments select (`execution.engines[].selected_by {arg, value, with?}`; an
  absent argument counts as its schema default) must not be marked not ready on the runner
  (`engine.readiness.runner`, or `available:false`). If one is, the call is refused before any approval is asked,
  with the missing packages named. A catalog that does not say leaves it to the runner's own refusal.
- **Approval keys** (`project.approvals`, §7): the entry id (confirm/job); `<entry>:network@<sha12>`, where sha12 is
  the first 12 hex digits of sha256 of the canonical JSON array of the call's hosts (sorted, lower-cased, from
  `hosts_by[param][value]` for the argument value, else `hosts`/`skill.network`). So a grant never covers other
  hosts. A key stored before this change, `<entry>:network`, still counts, but only while the call's hosts are among
  the entry's static hosts. Other keys: `<entry>:remote_upload`; `runner` (the first call on a loopback runner); and
  `runner@<host>` (reason `remote_runner`, the first call on a runner on another machine, which is never covered by
  `runner` and never called local). A clinic or `phi` entry on a remote runner, or one that takes a clinic `intake`
  (§3; `skill.draft-tcm-prescription`), and `job.skill.run` of such a skill (its `skill_id`), is asked about for every
  call (`needs[].per_call`), as patient data (`core.approval.remote_runner_phi`). Nothing stands for it, and "project" does not store it. The approval request lists each
  reason with its own hosts (`request.needs: [{reason, hosts, per_call?}]`). A network call whose hosts are not known
  in advance (an entry with `hosts_by` whose argument value it does not map, such as `system.provider_call` with an
  unlisted `tool`, or a network entry that names no hosts) is asked about for every call too (`per_call`, hosts `[]`,
  `core.approval.network_unknown`): no grant is stored for it, and no `<entry>:network` key from before stands for
  it. A page without Web Crypto (an http page opened at another address) keys a network grant
  `<entry>:network@<the sorted hosts, comma-joined>`: still for those hosts only. A job on a remote runner is asked
  as `core.approval.job_remote` (that runner's host named, never 本机).
- **Receipt additions.** Every envelope the router returns gets `receipt.execution.placement = {requested,
  chosen: browser|runner|page|null, reason, runner: {host, loopback}|null}` and `receipt.execution.approvals =
  [{reason, scope: once|project|standing|page, hosts, legacy?}]`. `standing` means a grant in the project record;
  `page` means an earlier answer in this page, or a "project" answer that could not be stored (no project, no
  store), which holds for this page's life; `project` only when the grant was stored. These are added to whatever
  the runtime wrote, and the runtime's own fields are kept. The router never invents a `schema` or a `result` for a
  runtime's envelope.
- **Page-made envelopes** (`core/receipt.js baseReceipt`) have `decided_by`. The router's (needs_approval, declined,
  unavailable, network_off, not_found, HUMAN_ONLY, a runtime that threw; `decided_by:"router"`) have
  `receipt.where:"page"`: nothing ran anywhere. A runtime client's own keep `where` = the runtime it stands for, with
  `decided_by` saying that runtime did not answer: `browser-runtime` (`where:"browser"`: a cancel, crash, timeout or
  boot failure in the worker), `runner-client` (`where:"runner"`: a runner that could not be reached, or answered
  with an HTTP error) or `unavailable-runtime` (`where` of its kind: a runtime module that never loaded). A
  runner-client envelope is `not_executed` only when the call provably never reached the dispatcher (the browser would
  not send it: mixed content, the local-network permission, an origin the runner does not allow; or a 4xx answer);
  a timeout, a dropped connection or a 5xx is `failed`, since the runner may have started it. All of them
  also carry `schema:"tcmstudio.receipt/1"`, `finished_at`, `execution.runtime:"page"` and `result: {kind:null,
  outcome:"not_executed"|"failed"|"cancelled", assurance:"none"}`. The page's own answers (`catalog_search`,
  `capabilities_status`) keep `where:"browser"`, `local:true`, and have `result.kind:"system"`,
  `outcome:"succeeded"`. The capabilities answer includes `remote_compute: {configured:false, reason}` and
  `runner.loopback`; its summary names a runner on another machine 远程 Runner / remote runner, as do the runner
  client's messages (`core.runner.{not_running,timeout,http}_remote`) and `core.router.missing_deps_remote`.

---

## 7. Storage (IndexedDB `tcmstudio`, version 1)

| store | keyPath | indexes | record |
|---|---|---|---|
| `projects` | `id` | `updatedAt` | `{id, name, description, instructions, color, icon, archived:false, createdAt, updatedAt, webDefault:2, defaults:{provider?, model?, compute?, web?:true}, approvals:{[toolOrEntryId]: "project"}}` |
| `conversations` | `id` | `projectId`, `updatedAt` | `{id, projectId, title, createdAt, updatedAt, leafId, model?, pinned?:false}` |
| `messages` | `id` | `conversationId` | `{id, conversationId, parentId, role:"user"|"assistant"|"tool", createdAt, content, attachments?:[fileId], reasoning?, toolCalls?:[{id,name,args}], toolCallId?, envelope?, provider, model, wire?, usage?, status?:"ok"|"stopped"|"error", error?}` |
| `files` | `id` | `projectId`, `sha256` | `{id, projectId, name, type, bytes, sha256, blob, source:"upload"|"tool", createdAt, runnerUploadId?}` |

Web access is on by default. A project record without `webDefault: 2` was written while it was off by default: when
the app starts it is read once as it reads now (`projects.adoptWebDefault`), its `defaults.web` turned from `false` to
`true` unless it holds a standing `<entry>:network` or `<entry>:remote_upload` approval (its user turned web access off
after granting it), `updatedAt` kept, and the mark added. A mark per record rather than a new database version: an
upgrade would close the database under every tab still running the previous release.

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
  "compute": "auto",                               // auto | browser | runner | remote (no backend: every call
                                                   //   unavailable); another text value is kept and reported
                                                   //   (unknown_compute), never read as auto
  "runner": {"url": "http://127.0.0.1:8765", "token": ""},
  "web": true,                                     // default web access for new projects (on; each host still asks)
  "webDefault": 2,                                 // read without it, a saved "web": false is the old default: on
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
3. Tool guidance: prefer the core tools. Use `catalog_search` then `call_tool` for the rest. For a question that
   needs several sources, `research_routes` first; calls that do not depend on each other in one response (they
   run together). Long work → a job (`job_status`). Never fabricate tool output. If a tool is unavailable, say what
   would be needed.
   Research questions: past the curated corpus, and a method: frame the question (exact entities, population,
   outcome, claim kind), gather every runnable source of each step of its route (a source that cannot run is
   covered and named, never switched silently), name rival explanations with an artefact among them, tell them
   apart (`research_inquiry`, predictions sealed first), and conclude at the strength the evidence licenses. A
   safety signal is reported when it appears.
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
