// The tool catalog (CONTRACTS §2): the built web/runtime/catalog.json merged with the connected runner's
// /api/catalog (the runner knows what it can run: its exec, available and missing fields win), search over ids, zh/en
// titles, tags and summaries, the conversion to provider tool definitions, and the two catalog tools the page can
// answer by itself when no runtime is up (catalog_search, capabilities_status).
//
// Capability state, per runtime (CONTRACTS §2 and §6): entryState(entry, ctx) → {implementation, readiness: {browser,
// runner}}. The built catalog carries only state.implementation; the browser's readiness is decided here, from what the
// page knows (the browser runtime's status, the project's web access, the packages and datasets known to be there),
// and the runner's comes from the runner's own catalog (state.readiness.runner), which a merge never lets stand in for
// the browser's. Each reason it is not ready is a Requirement {need, detail, remedy, names?} (requirement()).

import { CATEGORIES } from "./glossary.js";
import { baseReceipt } from "./receipt.js";
import { canonicalJson, isLoopbackUrl, isPlainObject, nowIso, sha256Hex } from "./util.js";

export const CATALOG_SCHEMA = "tcmstudio.catalog/1";
const LOCAL_TOOLS = new Set(["catalog_search", "capabilities_status"]);

const looseKey = (name) => String(name || "").trim().toLowerCase().replace(/[\s._-]+/g, "_").replace(/^_+|_+$/g, "");

export class Catalog {
  constructor(doc = {}) {
    this.schema = doc.schema || CATALOG_SCHEMA;
    this.versions = { ...(doc.versions || {}) };
    this.core = (doc.core || []).filter((c) => c && c.name);
    this.entries = (doc.entries || []).filter((e) => e && e.id);
    this.byId = Object.create(null);
    for (const e of this.entries) this.byId[e.id] = e;
    this.coreByName = Object.create(null);
    for (const c of this.core) this.coreByName[c.name] = c;
    this.categories = categoriesWithCounts(doc.categories, this.entries);
    this.sources = doc.sources || [];
    // acts reserved for a person ({name: message}), refused by name (router.js HUMAN_ONLY)
    this.neverOffered = isPlainObject(doc.never_offered) ? { ...doc.never_offered } : {};
    // the largest request one call takes, per runtime ({max_request_bytes: {runner, browser}}); an entry's own wins
    this.limits = isPlainObject(doc.limits) ? structuredClone(doc.limits) : null;
    // the committed file each verification level rests on ({level: path}), for the catalog page (CONTRACTS §2)
    this.verificationRecords = isPlainObject(doc.verification_records) ? { ...doc.verification_records } : {};
    this._index = null;
  }

  /** A core tool by name, an entry by id, or a core tool's mapped entry. */
  entry(idOrName) {
    if (!idOrName) return null;
    if (this.byId[idOrName]) return this.byId[idOrName];
    const core = this.coreByName[idOrName];
    return core?.maps_to ? this.byId[core.maps_to] || null : null;
  }

  coreTool(name) {
    return this.coreByName[name] || null;
  }

  /**
   * Ranked entries for a query (zh or en words, tags, ids). Filters: category, kind, runnableNow + isRunnable(entry),
   * and for the catalog page: where ("browser"|"runner": the entry's exec has it), readiness (a readiness state or a
   * list of them, in `where` when given, else in either runtime, as entryState(entry, context) decides), needsDataset
   * (true/false: has a data need or not; a dataset key: needs that dataset), verified (a verification level such as
   * "live_upstream" that passed, a verification status such as "PASS" or "UNVERIFIED", or true/false for PASS / not)
   * and backend (a browser compute backend, an engine id or a device: "webgpu", "esmfold", "gpu").
   */
  search(q = "", { category, kind, runnableNow = false, isRunnable, limit = 25, where, readiness, needsDataset, verified, backend, context = {} } = {}) {
    const index = this._index || (this._index = this.entries.map((e) => [e, indexEntry(e, this)]));
    const words = queryWords(q);
    const states = readiness === undefined || readiness === null || readiness === "" ? null : new Set([readiness].flat().map(String));
    const out = [];
    for (const [e, idx] of index) {
      if (category && e.category !== category) continue;
      if (kind && e.kind !== kind) continue;
      if (runnableNow && isRunnable && !isRunnable(e)) continue;
      if (where && !(Array.isArray(e.exec) && e.exec.includes(where))) continue;
      if (states) {
        const r = entryState(e, context).readiness;
        const got = where ? [r[where]?.state] : [r.browser?.state, r.runner?.state];
        if (!got.some((x) => states.has(x))) continue;
      }
      if (needsDataset !== undefined && needsDataset !== null && !matchesDataset(e, needsDataset)) continue;
      if (verified !== undefined && verified !== null && verified !== "" && !matchesVerified(e, verified)) continue;
      if (backend && !matchesBackend(e, backend)) continue;
      const score = words.length ? scoreEntry(idx, words) : 1;
      if (score > 0) out.push([score, e]);
    }
    out.sort((a, b) => b[0] - a[0] || a[1].id.localeCompare(b[1].id));
    return out.slice(0, Math.max(1, Math.min(Number(limit) || 25, 200))).map(([score, e]) => ({ ...e, score }));
  }

  /**
   * The core tool or entry id a name means when it differs from it only in case and separators: "tcmdb_status" is
   * "tcmdb.status" (function-calling APIs allow no dots in a tool's name, so models write them otherwise). null when
   * nothing matches, or more than one id does.
   */
  canonicalName(name) {
    if (!this._loose) {
      this._loose = new Map();
      for (const id of [...this.core.map((c) => c.name), ...this.entries.map((e) => e.id)]) {
        const k = looseKey(id);
        this._loose.set(k, (this._loose.has(k) ? this._loose.get(k) : id) === id ? id : null);
      }
    }
    return this._loose.get(looseKey(name)) || null;
  }

  /** Close ids for a name that does not exist (did-you-mean). */
  suggest(name, limit = 3) {
    const n = String(name || "").toLowerCase();
    if (!n) return [];
    const ids = [...this.core.map((c) => c.name), ...this.entries.map((e) => e.id)];
    return ids
      .map((id) => [similarity(n, id.toLowerCase()), id])
      .filter(([s]) => s >= 0.5)
      .sort((a, b) => b[0] - a[0])
      .slice(0, limit)
      .map(([, id]) => id);
  }

  toJSON() {
    return {
      schema: this.schema, versions: this.versions, categories: this.categories, core: this.core, entries: this.entries,
      ...(Object.keys(this.neverOffered).length ? { never_offered: this.neverOffered } : {}),
      ...(this.limits ? { limits: this.limits } : {}),
      ...(Object.keys(this.verificationRecords).length ? { verification_records: this.verificationRecords } : {}),
    };
  }
}

/**
 * Load and merge the catalogs the runtimes can give. The built catalog comes from the browser runtime (its boot
 * manifest) or the static file next to the app; the runner's, when it is connected, is merged on top.
 * opts: {fetch, url, built, runner} — `built`/`runner` documents may be passed directly (tests, a cached copy).
 */
export async function loadCatalog(runtimes = {}, opts = {}) {
  let built = opts.built || null;
  if (!built && typeof runtimes.browser?.catalog === "function") {
    try { built = await runtimes.browser.catalog(); } catch { built = null; }
  }
  if (!built) built = await fetchBuilt(opts);
  let runnerDoc = opts.runner || null;
  const runner = runtimes.runner;
  if (!runnerDoc && runner && runner.status === "ready" && typeof runner.catalog === "function") {
    try { runnerDoc = await runner.catalog(); } catch { runnerDoc = null; }
  }
  if (!built && !runnerDoc) return new Catalog({ schema: CATALOG_SCHEMA, core: [], entries: [], categories: [] });
  return new Catalog(mergeCatalogs(built, runnerDoc));
}

async function fetchBuilt({ fetch: fetchImpl = globalThis.fetch, url } = {}) {
  const href = url || defaultCatalogUrl();
  if (!href || !fetchImpl) return null;
  try {
    const r = await fetchImpl(href, { headers: { Accept: "application/json" } });
    if (!r.ok) return null;
    return await r.json();
  } catch {
    return null;
  }
}

function defaultCatalogUrl() {
  try { return new URL("../../runtime/catalog.json", import.meta.url).href; } catch { return null; }
}

/**
 * Entries and core tools by id/name; the runner's view of where each one runs wins. What only one side can know stays
 * that side's: the runner's readiness (state.readiness.runner) and engines come from the runner, while the browser's
 * readiness, its compute backends (execution.browser_compute) and its request limit (limits.max_request_bytes.browser)
 * are never replaced by the runner's.
 */
export function mergeCatalogs(built, runner) {
  if (!runner) return { ...built, sources: ["built"] };
  if (!built) return { ...runner, sources: ["runner"] };
  const RUNNER_WINS = ["exec", "available", "missing", "gpu", "heavy", "network", "confirm", "job"];
  const mergeOne = (a, b) => {
    const out = { ...a, ...b };
    for (const k of Object.keys(a)) if (b[k] === undefined || b[k] === null || b[k] === "") out[k] = a[k];
    for (const k of RUNNER_WINS) if (b[k] !== undefined) out[k] = b[k];
    if (isPlainObject(a.title) && isPlainObject(b.title)) out.title = { ...a.title, ...dropEmpty(b.title) };
    if (Array.isArray(a.tags) || Array.isArray(b.tags)) out.tags = [...new Set([...(a.tags || []), ...(b.tags || [])])];
    if (isPlainObject(a.state) || isPlainObject(b.state)) out.state = mergeState(a.state, b.state);
    // state.implementation and verification are one pair (tested ⇔ a committed level passed): both from the side the
    // implementation came from, the built side when it has one (it carries the committed records' join)
    const builtImpl = isPlainObject(a.state) && a.state.implementation !== undefined;
    if (builtImpl && a.verification !== undefined) out.verification = a.verification;
    else if (!builtImpl && b.verification !== undefined) out.verification = b.verification;
    if (isPlainObject(a.execution) && isPlainObject(b.execution)) {
      out.execution = { ...a.execution, ...b.execution };
      if (a.execution.browser_compute !== undefined) out.execution.browser_compute = a.execution.browser_compute;
    }
    if (isPlainObject(a.limits) && isPlainObject(b.limits)) out.limits = mergeLimits(a.limits, b.limits);
    return out;
  };
  const byId = new Map((built.entries || []).map((e) => [e.id, e]));
  for (const e of runner.entries || []) byId.set(e.id, byId.has(e.id) ? mergeOne(byId.get(e.id), e) : e);
  const byName = new Map((built.core || []).map((c) => [c.name, c]));
  for (const c of runner.core || []) byName.set(c.name, byName.has(c.name) ? mergeOne(byName.get(c.name), c) : c);
  return {
    schema: runner.schema || built.schema,
    versions: { ...(built.versions || {}), ...(runner.versions || {}) },
    categories: runner.categories?.length ? runner.categories : built.categories,
    core: [...byName.values()],
    entries: [...byId.values()],
    ...(built.never_offered || runner.never_offered ? { never_offered: { ...(built.never_offered || {}), ...(runner.never_offered || {}) } } : {}),
    ...(isPlainObject(built.limits) || isPlainObject(runner.limits) ? { limits: mergeLimits(built.limits, runner.limits) } : {}),
    ...(isPlainObject(built.verification_records) || isPlainObject(runner.verification_records)
      ? { verification_records: { ...(runner.verification_records || {}), ...(built.verification_records || {}) } } : {}),
    sources: ["built", "runner"],
  };
}

/** limits of a merged document or entry: the runner's own, the browser's from the built side. */
function mergeLimits(a, b) {
  const la = isPlainObject(a) ? a : {};
  const lb = isPlainObject(b) ? b : {};
  const am = isPlainObject(la.max_request_bytes) ? la.max_request_bytes : {};
  const bm = isPlainObject(lb.max_request_bytes) ? lb.max_request_bytes : {};
  return { ...la, ...lb, max_request_bytes: { ...am, ...bm, ...(am.browser !== undefined ? { browser: am.browser } : {}) } };
}

/** state of a merged entry: the built side's implementation and browser readiness, the runner's runner readiness. */
function mergeState(a, b) {
  const sa = isPlainObject(a) ? a : {};
  const sb = isPlainObject(b) ? b : {};
  const ra = isPlainObject(sa.readiness) ? sa.readiness : {};
  const rb = isPlainObject(sb.readiness) ? sb.readiness : {};
  const readiness = {};
  if (ra.browser !== undefined) readiness.browser = ra.browser;
  if (rb.runner !== undefined) readiness.runner = rb.runner;
  else if (ra.runner !== undefined) readiness.runner = ra.runner;
  const out = { ...sb, ...sa };
  if (Object.keys(readiness).length) out.readiness = readiness;
  else delete out.readiness;
  return out;
}

function dropEmpty(o) {
  return Object.fromEntries(Object.entries(o).filter(([, v]) => v !== undefined && v !== null && v !== ""));
}

function categoriesWithCounts(declared, entries) {
  const counts = new Map();
  for (const e of entries) counts.set(e.category, (counts.get(e.category) || 0) + 1);
  const base = Array.isArray(declared) && declared.length ? declared : CATEGORIES;
  return base.map((c) => ({ id: c.id, zh: c.zh, en: c.en, count: counts.get(c.id) || 0 }));
}

// ------------------------------------------------------------------------------------------------- search

const CJK = /[㐀-鿿豈-﫿\u{20000}-\u{2fa1f}]/u;
const CJK_RUN = /[㐀-鿿豈-﫿\u{20000}-\u{2fa1f}]+/gu;

function norm(s) {
  return String(s ?? "").normalize("NFKC").toLowerCase();
}

/** Latin words (ids split at . _ - too) and CJK runs. */
function words(text) {
  const s = norm(text);
  const out = [];
  for (const m of s.matchAll(/[a-z0-9]+(?:[._-][a-z0-9]+)*/g)) {
    out.push(m[0]);
    if (/[._-]/.test(m[0])) out.push(...m[0].split(/[._-]+/));
  }
  for (const m of s.matchAll(CJK_RUN)) out.push(m[0]);
  return out;
}

function bigrams(run) {
  const chars = [...run];
  if (chars.length < 2) return chars;
  const out = [];
  for (let i = 0; i < chars.length - 1; i++) out.push(chars[i] + chars[i + 1]);
  return out;
}

function queryWords(q) {
  const s = norm(q).trim();
  if (!s) return [];
  return [...new Set(words(s))].filter((w) => w.length > 1 || CJK.test(w));
}

function indexEntry(e, catalog) {
  const cat = CATEGORIES.find((c) => c.id === e.category);
  const coreNames = catalog.core.filter((c) => c.maps_to === e.id).map((c) => c.name);
  const fields = [
    [8, [e.id, ...coreNames].join(" ")],
    [6, [e.title?.zh, e.title?.en].filter(Boolean).join(" ")],
    [6, (e.tags || []).join(" ")],
    [2, [e.category, cat?.zh, cat?.en, e.kind].filter(Boolean).join(" ")],
    [1, [e.summary, e.description, e.skill?.claim_kinds?.join(" ")].filter(Boolean).join(" ")],
  ];
  return fields.map(([w, text]) => ({ w, text: norm(text), words: new Set(words(text)) }));
}

function scoreEntry(idx, qwords) {
  let total = 0;
  let matched = 0;
  for (const q of qwords) {
    let best = 0;
    const cjk = CJK.test(q);
    for (const f of idx) {
      let s = 0;
      if (f.words.has(q)) s = f.w * 3;
      else if (f.text.includes(q)) s = f.w * 2;
      else if (cjk && [...q].length > 2) {
        // a longer Chinese phrase: credit the share of its bigrams found (配伍禁忌 ~ 十八反配伍)
        const grams = bigrams(q);
        const hit = grams.filter((g) => f.text.includes(g)).length;
        if (hit / grams.length >= 0.5) s = f.w * (hit / grams.length);
      }
      if (s > best) best = s;
    }
    if (best > 0) matched++;
    total += best;
  }
  if (!matched) return 0;
  return total * (matched / qwords.length) + (matched === qwords.length ? 1 : 0);
}

function similarity(a, b) {
  if (a === b) return 1;
  if (b.endsWith(`.${a}`) || b.endsWith(`_${a}`) || a.endsWith(b)) return 0.9;
  const d = levenshtein(a, b);
  return 1 - d / Math.max(a.length, b.length);
}

function levenshtein(a, b) {
  const m = a.length, n = b.length;
  if (!m) return n;
  if (!n) return m;
  let prev = Array.from({ length: n + 1 }, (_, j) => j);
  for (let i = 1; i <= m; i++) {
    const cur = [i];
    for (let j = 1; j <= n; j++) cur[j] = Math.min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1));
    prev = cur;
  }
  return prev[n];
}

// ------------------------------------------------------------------------------------------------- provider tools

/** Core tools → OpenAI `function` tools or Anthropic `input_schema` tools. */
export function toProviderTools(coreTools, format = "openai") {
  return (coreTools || []).map((c) => {
    const parameters = cleanSchema(c.parameters);
    const description = String(c.description || c.summary || "").slice(0, 1000);
    if (format === "anthropic") return { name: c.name, description, input_schema: parameters };
    return { type: "function", function: { name: c.name, description, parameters } };
  });
}

/** Providers reject an object without properties or an array without items: add them. */
export function cleanSchema(schema) {
  const s = isPlainObject(schema) ? structuredClone(schema) : {};
  if (!s.type) s.type = "object";
  const walk = (node) => {
    if (!isPlainObject(node)) return;
    if (node.type === "object" && !isPlainObject(node.properties)) node.properties = {};
    if (node.type === "array" && !isPlainObject(node.items) && !Array.isArray(node.items)) node.items = {};
    for (const v of Object.values(node.properties || {})) walk(v);
    if (isPlainObject(node.items)) walk(node.items);
    for (const k of ["anyOf", "oneOf", "allOf"]) if (Array.isArray(node[k])) node[k].forEach(walk);
  };
  walk(s);
  return s;
}

// ------------------------------------------------------------------------------------------------- local answers

export function isLocalTool(name) {
  return LOCAL_TOOLS.has(name);
}

/**
 * Answer catalog_search or capabilities_status in the page. ctx: {catalog, where(entry)→"browser"|"runner"|null,
 * status:{browser, runner, runnerUrl?, compute, web}, lang}. Returns an envelope (CONTRACTS §3) or null.
 */
export async function localCall(name, args = {}, ctx = {}) {
  if (!LOCAL_TOOLS.has(name)) return null;
  const started = Date.now();
  const startedAt = nowIso();
  const { catalog, where = () => null, status = {} } = ctx;
  let result, summary, text;
  if (name === "catalog_search") {
    const limit = Math.max(1, Math.min(Number(args.limit) || 10, 25));
    const hits = catalog ? catalog.search(args.query || "", {
      category: args.category || undefined, kind: args.kind || undefined, limit,
      runnableNow: Boolean(args.runnable_now), isRunnable: (e) => Boolean(where(e)),
    }) : [];
    const matched = hits.map((e) => {
      const place = where(e);
      return {
        id: e.id, kind: e.kind, category: e.category, title: e.title, summary: e.summary,
        parameters: e.parameters || { type: "object", properties: {} }, example: e.example ?? null,
        exec: e.exec || [], runnable_now: Boolean(place), where: place,
        needs: place ? [] : needsOf(e, status),
        network: Boolean(e.network), confirm: Boolean(e.confirm), job: Boolean(e.job),
      };
    });
    const categories = Object.fromEntries((catalog?.categories || []).map((c) => [c.id, { zh: c.zh, en: c.en, count: c.count }]));
    result = { query: args.query || "", matched, categories, how: "Run an entry with call_tool {\"tool\": \"<id>\", \"arguments\": {…}} using its parameters schema." };
    summary = ctx.t ? ctx.t("core.local.catalog", { n: matched.length }) : `catalog search: ${matched.length} matches`;
    text = JSON.stringify({
      matched: matched.map((m) => ({ id: m.id, summary: m.summary, parameters: m.parameters, runnable_now: m.runnable_now, ...(m.needs.length ? { needs: m.needs } : {}), ...(m.confirm ? { confirm: true } : {}), ...(m.network ? { network: true } : {}) })),
      how: result.how,
      note: matched.length ? undefined : "No entry matched. Try other words (Chinese or English), a category, or no filters.",
    });
  } else {
    const entries = catalog?.entries || [];
    const runnable = entries.filter((e) => where(e));
    result = {
      browser: { status: status.browser || "offline", runs: "Python tools on the CPU (single thread, WebAssembly)" },
      runner: {
        status: status.runner || "offline", connected: status.runner === "ready", url: status.runnerUrl || null,
        // a runner on another machine is a remote one: data sent to it leaves this computer
        loopback: status.runnerUrl ? isLoopbackUrl(status.runnerUrl) : null,
      },
      compute: status.compute || "auto",
      remote_compute: { ...REMOTE_COMPUTE },
      web_access: Boolean(status.web),
      counts: { core: catalog?.core.length || 0, entries: entries.length, runnable_now: runnable.length },
      by_place: {
        browser: runnable.filter((e) => where(e) === "browser").length,
        runner: runnable.filter((e) => where(e) === "runner").length,
      },
      note: "Answered by the page; the runner (when connected) also reports devices and installed engines.",
    };
    // a runner on another machine is never called the local one
    const key = result.runner.loopback === false ? "core.local.capabilities_remote" : "core.local.capabilities";
    summary = ctx.t ? ctx.t(key, { browser: result.browser.status, runner: result.runner.status }) : "capabilities";
    text = JSON.stringify(result);
  }
  return {
    ok: true, tool: name, via: name === "catalog_search" ? "system.catalog_search" : "system.capabilities",
    status: "succeeded", duration_ms: Date.now() - started, summary, text, result, citations: [],
    governance: { kind: "system", released: null, artifact: null, verdict: null, claims: [], evidence: [], refusals: [], labels: [], licences: [], limitations: [], outputs: [] },
    // an answer the page computed (no decision, nothing withheld): where "browser", made by this page's JavaScript
    receipt: {
      ...baseReceipt({
        where: "browser", runtime: "studio-js", device: "cpu", versions: catalog?.versions || {}, started_at: startedAt,
        input_sha256: await hashOf(args || {}), output_sha256: await hashOf(result),
        outcome: "succeeded", kind: "system",
      }),
      local: true,
    },
    job: null, approval: null, error: null,
  };
}

/** sha256 of the canonical JSON, or null where this page has no Web Crypto (an http page on another address). */
async function hashOf(value) {
  try { return await sha256Hex(canonicalJson(value)); } catch { return null; }
}

/** No remote compute backend exists (CONTRACTS §6: the "remote" preference runs nothing, and nothing falls back). */
export const REMOTE_COMPUTE = Object.freeze({
  configured: false,
  reason: "no remote compute backend is registered; calls run in this browser or on the paired runner (runner.loopback says whether that runner is this computer)",
});

/**
 * What an entry the model cannot run now would need, as catalog_search lists it: the details of its requirements.
 * These strings are what the model reads; keep them byte for byte (web/test/catalog.test.mjs).
 */
function needsOf(e, status) {
  return requirementsOf(e, status).map((r) => r.detail);
}

function requirementsOf(e, status) {
  const out = [];
  const exec = e.exec || [];
  if (!exec.includes("browser") && status.runner !== "ready") out.push(requirement("runner"));
  if (e.available === false && e.missing?.length) out.push(...missingRequirements(e.missing));
  if (e.network && !status.web) out.push(requirement("network"));
  if (status.compute === "browser" && !exec.includes("browser")) out.push(requirement("compute_setting"));
  // the two preferences that run nothing here (no string above changes for the others)
  if (status.compute === "remote") out.push(requirement("remote_backend"));
  else if (status.compute && !COMPUTE_VALUES.includes(status.compute)) out.push(unknownCompute(status.compute));
  return out;
}

// ------------------------------------------------------------------------------------------------- requirements

/** Requirement.need: what a call or an entry lacks (CONTRACTS §3 error.requires, §2 state.readiness.*.requires). */
export const NEEDS = Object.freeze(["runner", "browser_runtime", "dependency", "dataset", "network", "job_service", "compute_setting", "remote_backend", "memory", "authentication"]);

/** settings.compute values the router knows; any other is reported (unknown_compute), never read as auto. */
export const COMPUTE_VALUES = Object.freeze(["auto", "browser", "runner", "remote"]);

const listOf = (names) => (Array.isArray(names) ? names : names == null ? [] : [names]).map(String).filter(Boolean);

const REQUIREMENT_TEXT = {
  runner: () => ({ detail: "local runner (tcmstudio serve)", remedy: "Start the local runner (tcmstudio serve) and connect it under Settings → Compute." }),
  browser_runtime: () => ({ detail: "the browser runtime", remedy: "Reload the page so the in-browser runtime loads, or connect the local runner." }),
  dependency: (names) => ({ detail: `install: ${names.join(", ")}`, remedy: `Install ${names.join(", ") || "the missing packages"} in the runtime that runs it, then try again; nothing approximate is substituted.` }),
  // worded as tcmstudio.capability words them for the browser runtime
  dataset: (names) => (names.length
    ? { detail: `the ${names.join(", ")} dataset${names.length > 1 ? "s" : ""} built or imported in this runtime`, remedy: `Import a built ${names[0]} store here with tcmdb.import_store, or build it on the local runner (job.tcmdb.build {dataset: ${names[0]}}).` }
    : { detail: "a tcmdb store built or imported in this runtime", remedy: "Import a built store here with tcmdb.import_store, or fetch and build one on the local runner (job.tcmdb.fetch, then job.tcmdb.build)." }),
  network: () => ({ detail: "web access for this project", remedy: "Turn on web access for this project." }),
  job_service: () => ({ detail: "the runner's job service", remedy: "Connect the local runner: jobs run only there." }),
  compute_setting: () => ({ detail: "compute set to runner or auto", remedy: "Set compute to Auto or Local runner (Settings → Compute, or the project's default)." }),
  remote_backend: () => ({ detail: "a remote compute backend (none is configured)", remedy: "Studio has no remote compute backend: set compute to Auto, Browser or Local runner (Settings → Compute)." }),
  memory: () => ({ detail: "a smaller request for the browser runtime", remedy: "Connect the local runner (tcmstudio serve), or send a smaller input." }),
  authentication: () => ({ detail: "credentials for the service", remedy: "Provide the service's credentials where the call runs." }),
};

/**
 * A Requirement {need, detail, remedy, names?}: detail and remedy are English sentences (the model reads them; the UI
 * words a need itself). opts: {names, detail, remedy} override the defaults.
 */
export function requirement(need, { names, detail, remedy } = {}) {
  const n = NEEDS.includes(need) ? need : "dependency";
  const list = listOf(names);
  const base = REQUIREMENT_TEXT[n](list);
  return { need: n, detail: detail || base.detail, remedy: remedy || base.remedy, ...(list.length ? { names: list } : {}) };
}

/** The requirement of a compute preference this page does not know. */
export function unknownCompute(value) {
  return requirement("compute_setting", {
    detail: `compute set to auto, browser or runner (it is "${String(value).slice(0, 40)}", which Studio does not know)`,
    remedy: "Choose Auto, Browser or Local runner under Settings → Compute (or in the project's defaults).",
  });
}

const PACKAGE = /^[A-Za-z0-9][A-Za-z0-9_.+-]*$/;
const JOB_SERVICE_REMEDY = "Restart the runner where its job service can start (Linux or macOS, or WSL on Windows, with bioagent installed); the detail says why it cannot start here.";

/**
 * What a runner entry's `missing` list asks for, classified as tcmstudio.capability._classify classifies it (the
 * runner folds what its job service says a kind lacks into `missing`): package and command names are one dependency
 * requirement; the research snapshots a dataset (built with job.research.snapshot, never installed); any other
 * sentence, why the job layer cannot run there, the job service. A list of package names alone reads as before.
 */
export function missingRequirements(missing) {
  const items = [...new Set(listOf(missing))];
  const deps = items.filter((x) => PACKAGE.test(x));
  const out = deps.length ? [requirement("dependency", { names: deps })] : [];
  for (const x of items) {
    if (x.startsWith("snapshots") && !PACKAGE.test(x)) {
      out.push(requirement("dataset", { names: ["research snapshots"], detail: `the research ${x} built on this runner`, remedy: "Build them first: job.research.snapshot (one recorded run downloads, verifies and records every snapshot)." }));
    }
  }
  for (const x of items) if (!PACKAGE.test(x) && !x.startsWith("snapshots")) out.push(requirement("job_service", { detail: x, remedy: JOB_SERVICE_REMEDY }));
  return out;
}

/**
 * What an entry lacks on the connected runner: the runner catalog's own state.readiness.runner requirements when it
 * gives them (it classified them), else its `missing` list classified here (an older runner), else an unnamed
 * dependency.
 */
export function runnerLacks(entry) {
  const given = isPlainObject(entry?.state?.readiness?.runner) ? entry.state.readiness.runner : null;
  if (given && given.state !== "ready") {
    const requires = (Array.isArray(given.requires) ? given.requires : []).filter(isRequirement);
    if (requires.length) return requires.map((q) => ({ ...q }));
  }
  const classified = missingRequirements(entry?.missing);
  return classified.length ? classified : [requirement("dependency")];
}

/** Is this a Requirement (from a catalog or an envelope written elsewhere)? */
export function isRequirement(r) {
  return isPlainObject(r) && typeof r.need === "string" && typeof r.detail === "string";
}

// ------------------------------------------------------------------------------------------------- capability state

/** state.implementation values; tested ⇔ a committed verification level passed; implemented ⇔ the ref resolves. */
export const IMPLEMENTATION = Object.freeze(["registered", "implemented", "tested", "deprecated"]);

/** state.readiness.*.state, in precedence order: the first that applies is the state. needs_approval is not one. */
export const READINESS = Object.freeze(["ready", "needs_runner", "missing_dependency", "needs_dataset", "network_unavailable", "needs_authentication", "unsupported"]);

const STATE_OF_NEED = {
  runner: "needs_runner", job_service: "needs_runner", dependency: "missing_dependency", dataset: "needs_dataset",
  network: "network_unavailable", authentication: "needs_authentication", browser_runtime: "unsupported",
  compute_setting: "unsupported", remote_backend: "unsupported", memory: "unsupported",
};

/** The state a list of requirements comes to: the first in READINESS order that one of them (or `given`) is. */
function stateOf(requires, given) {
  const ranks = requires.map((r) => READINESS.indexOf(STATE_OF_NEED[r.need] || "unsupported"));
  if (READINESS.includes(given) && given !== "ready") ranks.push(READINESS.indexOf(given));
  return ranks.length ? READINESS[Math.min(...ranks)] : "ready";
}

/**
 * The readiness state Requirements come to (and `given`, a state already decided), by the same precedence: for a view
 * that adds what only it knows (ui/pages/catalog.js: the compute preference, a store not read yet).
 */
export function readinessOf(requires, given) {
  return stateOf((Array.isArray(requires) ? requires : []).filter(isRequirement), given);
}

const toSet = (x) => (x instanceof Set ? x : new Set(x == null ? [] : [...x].map(String)));
const isUp = (status) => status !== undefined && status !== null && status !== "offline";

/**
 * The datasets an entry reads (tcmstudio.capability.datasets_for): entry.data (dataset keys; "*" means any built
 * store), and for an entry whose dataset is an argument (data_from: {param}) the one the arguments name, or any built
 * store when no call is in view.
 */
export function datasetsOf(entry, args) {
  const data = Array.isArray(entry?.data) ? entry.data.map(String) : [];
  const param = isPlainObject(entry?.data_from) ? entry.data_from.param : null;
  if (param) {
    const v = isPlainObject(args) ? args[param] : undefined;
    data.push(typeof v === "string" && v ? v : "*");
  }
  return data;
}

/**
 * The capability state of an entry here: {implementation, readiness: {browser: R, runner: R}}, R = {state, requires}.
 * ctx: {browser, runner (runtime statuses), web (the project's web access), built (dataset keys known built in the
 * browser: tcmdb status), packages (Pyodide packages this browser can load, when known), args (a call's arguments)}.
 *
 * browser: decided here. A dataset counts as present only when it is known built (ctx.built); a package list that is
 * not known is not held against the entry (the call itself reports one it cannot load). runner: the runner catalog's
 * state.readiness.runner when the runner is connected (`derived: true` when an older runner gave only
 * available/missing); needs_runner otherwise. Both add the project's web access, which only the page knows.
 */
export function entryState(entry, ctx = {}) {
  const e = isPlainObject(entry) ? entry : {};
  const exec = Array.isArray(e.exec) ? e.exec : [];
  const args = isPlainObject(ctx.args) ? ctx.args : {};
  const netOff = ctx.web === false && (Boolean(e.network) || (typeof e.network_if === "string" && args[e.network_if] === true));

  // browser
  const b = [];
  if (!exec.includes("browser")) b.push(requirement("runner"));
  if (ctx.packages !== undefined && ctx.packages !== null) {
    const have = toSet(ctx.packages);
    const lack = (Array.isArray(e.pyodide_packages) ? e.pyodide_packages : []).filter((p) => !have.has(String(p)));
    if (lack.length) {
      b.push(requirement("dependency", {
        names: lack, detail: `${lack.join(", ")} loaded in the browser runtime`,
        remedy: "The browser runtime loads the Pyodide packages an entry lists in pyodide_packages before calling it; or run it on the local runner.",
      }));
    }
  }
  const data = datasetsOf(e, args);
  if (data.length) {
    const built = toSet(ctx.built);
    const lack = data.includes("*") ? (built.size ? [] : ["*"]) : data.filter((d) => !built.has(d));
    if (lack.length) b.push(requirement("dataset", { names: lack.filter((d) => d !== "*") }));
  }
  if (netOff) b.push(requirement("network"));
  if (ctx.browser !== undefined && !isUp(ctx.browser)) b.push(requirement("browser_runtime"));

  // runner
  let runner;
  const given = isPlainObject(e.state?.readiness?.runner) ? e.state.readiness.runner : null;
  if (!exec.includes("runner")) {
    runner = { state: "unsupported", requires: [requirement("browser_runtime", { remedy: "It runs only in the browser runtime." })] };
  } else if (ctx.runner !== "ready") {
    runner = { state: "needs_runner", requires: [requirement("runner")] };
  } else if (given && READINESS.includes(given.state)) {
    const requires = (Array.isArray(given.requires) ? given.requires : []).filter(isRequirement);
    runner = { ...given, requires: [...requires] };
  } else {
    const requires = e.available === false ? runnerLacks({ missing: e.missing }) : [];
    runner = { state: stateOf(requires), requires, derived: true };
  }
  if (netOff && runner.state !== "unsupported" && !runner.requires.some((r) => r.need === "network")) {
    runner = { ...runner, requires: [...runner.requires, requirement("network")] };
    runner.state = stateOf(runner.requires, runner.state);
  }

  return {
    implementation: implementationOf(e),
    readiness: { browser: { state: stateOf(b), requires: b }, runner },
  };
}

/** state.implementation as the catalog gives it; derived (conservatively) for a catalog written before it had one. */
function implementationOf(e) {
  if (IMPLEMENTATION.includes(e.state?.implementation)) return e.state.implementation;
  if (e.deprecated === true) return "deprecated";
  const levels = isPlainObject(e.verification?.levels) ? Object.values(e.verification.levels) : [];
  if (levels.some((l) => l?.status === "PASS")) return "tested";
  if (e.implementation?.ref) return "implemented";
  return "registered";
}

// ------------------------------------------------------------------------------------------------- search filters

const VERIFY_STATUSES = ["PASS", "FAIL", "BLOCKED", "UNVERIFIED", "SKIPPED", "NOT_APPLICABLE"];

function matchesDataset(e, want) {
  const data = Array.isArray(e.data) ? e.data.map(String) : [];
  const fromParam = isPlainObject(e.data_from) ? e.data_from.param : null;
  const has = data.length > 0 || Boolean(fromParam);
  if (want === true) return has;
  if (want === false) return !has;
  const key = String(want);
  if (data.includes(key) || data.includes("*")) return true;
  if (!fromParam) return false;
  const values = e.parameters?.properties?.[fromParam]?.enum;
  return Array.isArray(values) ? values.includes(key) : true;
}

function matchesVerified(e, want) {
  const v = isPlainObject(e.verification) ? e.verification : {};
  const status = VERIFY_STATUSES.includes(v.status) ? v.status : "UNVERIFIED";
  if (want === true) return status === "PASS";
  if (want === false) return status !== "PASS";
  const w = String(want);
  if (VERIFY_STATUSES.includes(w.toUpperCase())) return status === w.toUpperCase();
  return v.levels?.[w]?.status === "PASS";
}

function matchesBackend(e, want) {
  const w = String(want).toLowerCase();
  const ex = isPlainObject(e.execution) ? e.execution : {};
  const browser = Array.isArray(ex.browser_compute?.backends) ? ex.browser_compute.backends : [];
  if (browser.map((x) => String(x).toLowerCase()).includes(w)) return true;
  const engines = Array.isArray(ex.engines) ? ex.engines : [];
  if (engines.some((g) => String(g?.id || "").toLowerCase() === w || String(g?.device || "").toLowerCase() === w)) return true;
  return w === "gpu" && e.gpu === true;
}
