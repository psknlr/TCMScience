// The tool catalog (CONTRACTS §2): the built web/runtime/catalog.json merged with the connected runner's
// /api/catalog (the runner knows what it can run: its exec, available and missing fields win), search over ids, zh/en
// titles, tags and summaries, the conversion to provider tool definitions, and the two catalog tools the page can
// answer by itself when no runtime is up (catalog_search, capabilities_status).

import { CATEGORIES } from "./glossary.js";
import { canonicalJson, isPlainObject, nowIso, sha256Hex } from "./util.js";

export const CATALOG_SCHEMA = "tcmstudio.catalog/1";
const LOCAL_TOOLS = new Set(["catalog_search", "capabilities_status"]);

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

  /** Ranked entries for a query (zh or en words, tags, ids). Filters: category, kind, runnableNow + isRunnable(entry). */
  search(q = "", { category, kind, runnableNow = false, isRunnable, limit = 25 } = {}) {
    const index = this._index || (this._index = this.entries.map((e) => [e, indexEntry(e, this)]));
    const words = queryWords(q);
    const out = [];
    for (const [e, idx] of index) {
      if (category && e.category !== category) continue;
      if (kind && e.kind !== kind) continue;
      if (runnableNow && isRunnable && !isRunnable(e)) continue;
      const score = words.length ? scoreEntry(idx, words) : 1;
      if (score > 0) out.push([score, e]);
    }
    out.sort((a, b) => b[0] - a[0] || a[1].id.localeCompare(b[1].id));
    return out.slice(0, Math.max(1, Math.min(Number(limit) || 25, 200))).map(([score, e]) => ({ ...e, score }));
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
    return { schema: this.schema, versions: this.versions, categories: this.categories, core: this.core, entries: this.entries };
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

/** Entries and core tools by id/name; the runner's view of where each one runs wins. */
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
    sources: ["built", "runner"],
  };
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
      runner: { status: status.runner || "offline", connected: status.runner === "ready", url: status.runnerUrl || null },
      compute: status.compute || "auto",
      web_access: Boolean(status.web),
      counts: { core: catalog?.core.length || 0, entries: entries.length, runnable_now: runnable.length },
      by_place: {
        browser: runnable.filter((e) => where(e) === "browser").length,
        runner: runnable.filter((e) => where(e) === "runner").length,
      },
      note: "Answered by the page; the runner (when connected) also reports devices and installed engines.",
    };
    summary = ctx.t ? ctx.t("core.local.capabilities", { browser: result.browser.status, runner: result.runner.status }) : "capabilities";
    text = JSON.stringify(result);
  }
  return {
    ok: true, tool: name, via: name === "catalog_search" ? "system.catalog_search" : "system.capabilities",
    status: "succeeded", duration_ms: Date.now() - started, summary, text, result, citations: [],
    governance: { kind: "system", released: null, artifact: null, verdict: null, claims: [], evidence: [], refusals: [], labels: [], licences: [], limitations: [], outputs: [] },
    receipt: {
      where: "browser", runtime: "studio-js", device: "cpu", versions: { ...(catalog?.versions || {}) },
      composite_version: null, content_hash: null, audit_head: null,
      input_sha256: await sha256Hex(canonicalJson(args || {})), output_sha256: await sha256Hex(canonicalJson(result)),
      started_at: startedAt, local: true,
    },
    job: null, approval: null, error: null,
  };
}

function needsOf(e, status) {
  const out = [];
  const exec = e.exec || [];
  if (!exec.includes("browser") && status.runner !== "ready") out.push("local runner (tcmstudio serve)");
  if (e.available === false && e.missing?.length) out.push(`install: ${e.missing.join(", ")}`);
  if (e.network && !status.web) out.push("web access for this project");
  if (status.compute === "browser" && !exec.includes("browser")) out.push("compute set to runner or auto");
  return out;
}
