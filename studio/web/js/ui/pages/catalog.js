// The tool catalog (CONTRACTS §2): 13 categories, search (zh/en words, tags, ids), filters, and an entry's detail —
// its parameters, example, and for a governed skill its version, pin, claim kinds and content hash. Read from
// core/catalog.js.
//
// Each entry's state is a row of separate fields in a fixed order (DESIGN §4.5 StateRow), never one mixed badge:
//   已注册 Registered · 可执行 Ready to execute (per runtime, core/catalog.js entryState, with the reason when not; the
//   compute preference and a store not read yet are added here: pageReadiness) ·
//   测试已验证 Verified by test (entry.verification: which level and when; a fixture or mock is never called live) ·
//   已成功执行 Executed successfully (real receipts seen from this browser only: ui/runlog.js) · 需要数据 Requires data ·
//   需要本机 Runner Requires Runner · 需要远程计算 Requires remote compute (nothing requires it: allow_remote makes it
//   optional) · 需要批准 Requires approval (the entry's static approvals, in words).
// Every chip has an icon and words; jade is only for ready. Filters are aria-pressed chips for the state fields and
// selects for runtime, dataset, verification level and local backend; they live in app.state.catalogUI and in the
// address (ui/router.js CATALOG_PARAMS), and every count is computed from the catalog the page has.

import { COMPUTE_VALUES, entryState, readinessOf, requirement, unknownCompute } from "../../core/catalog.js";
import { safeUrl } from "../../core/governance.js";
import { glossary, lang, t } from "../../core/i18n.js";
import { clear, debounce, fill, formatBytes, formatDateTime, guessLang, h } from "../dom.js";
import { hashBadge } from "../governance.js";
import { icon } from "../icons.js";
import { button, chip, copyButton, emptyState, keyValue, selectField } from "../primitives.js";
import { CATALOG_PARAMS, routeHref } from "../router.js";
import { readRunlog, runOf } from "../runlog.js";
import { computeBackendLabel } from "../toolcards.js";
import { runnerIsRemote } from "../panels.js";

const CAT_ICON = {
  tcm_knowledge: "leaf", tcm_safety: "shieldAlert", clinic: "stethoscope", tcm_data: "database", live_sources: "globe",
  netpharm: "network", study_design: "sigma", clinical_calc: "calculator", seq_genomics: "dna", structure_molecules: "atom",
  omics: "layers", literature: "bookText", system: "fingerprint",
};

/** The eight state fields, in their fixed order. */
export const STATE_FIELDS = ["registered", "ready", "verified", "executed", "needs_data", "needs_runner", "remote", "approval"];
/** The state chips a filter can require (each one a field above that an entry can have or not). */
export const STATE_FLAGS = ["ready", "verified", "executed", "needs_data", "needs_runner", "remote_optional", "needs_approval"];
/** runtime select: any | in the browser | on the runner only | through the site's gateway | a runner job. */
export const RUNTIME_FILTERS = ["browser", "runner_only", "gateway", "job"];
/** local backend select: the browser's compute backends (execution.browser_compute), and Pyodide for every browser entry. */
export const BACKEND_FILTERS = ["webgpu", "wasm-simd", "js", "pyodide"];
/** verification levels, most direct first (bioagent.validation): what a row names when several passed. */
export const VERIFY_LEVELS = ["live_upstream", "live_gateway", "reference_output", "kernel_parity", "pyodide_parity", "offline_invocation", "lock_pin", "gateway_mock"];
const VERIFY_STATUS = ["PASS", "FAIL", "BLOCKED", "UNVERIFIED", "SKIPPED", "NOT_APPLICABLE"];
const READINESS_ICON = { ready: "check", needs_runner: "laptop", missing_dependency: "box", needs_dataset: "database", network_unavailable: "wifiOff", needs_authentication: "key", unsupported: "ban" };

/** The data an entry reads: its declared keys ("*" = any built store) and, for data_from, the argument that names it. */
function dataOf(e) {
  const keys = Array.isArray(e?.data) ? e.data.map(String) : [];
  const param = e?.data_from && typeof e.data_from === "object" ? String(e.data_from.param || "") : "";
  return { keys, param };
}

/** The verification an entry carries, normalized: {status, best: {level, status, at, record}|null, levels: [...]}. */
export function verificationOf(e, records = {}) {
  const v = e?.verification && typeof e.verification === "object" ? e.verification : {};
  const status = VERIFY_STATUS.includes(v.status) ? v.status : "UNVERIFIED";
  const raw = v.levels && typeof v.levels === "object" ? v.levels : {};
  const order = [...VERIFY_LEVELS, ...Object.keys(raw).filter((k) => !VERIFY_LEVELS.includes(k)).sort()];
  const levels = order.filter((k) => raw[k] && typeof raw[k] === "object").map((k) => ({
    level: k, status: VERIFY_STATUS.includes(raw[k].status) ? raw[k].status : "UNVERIFIED", at: raw[k].at || null,
    record: raw[k].record || records?.[k] || null,
  }));
  const best = levels.find((l) => l.status === "PASS") || levels[0] || null;
  return { status, best, levels };
}

/**
 * What only this page knows, over core entryState's answer (pure): the compute preference, and whether this browser's
 * store was read. A runtime the preference leaves out is not ready (compute_setting), as the router never sends a call
 * there; "remote", which no backend serves, leaves out both (remote_backend); a value Studio does not know, both
 * (unknownCompute). A browser store not read yet (Settings → Data reads it only on a press: it loads Python) is not
 * known to lack a dataset: the browser's dataset requirements become `unchecked` (their names), and the runtime is not
 * called ready while they are. ctx: {compute, dataKnown}. → the state, with readiness changed where it applies.
 */
export function pageReadiness(st, { compute, dataKnown = true } = {}) {
  const out = { ...(st || {}), readiness: { ...(st?.readiness || {}) } };
  const browser = out.readiness.browser;
  if (!dataKnown && browser && Array.isArray(browser.requires) && browser.requires.some((q) => q?.need === "dataset")) {
    const data = browser.requires.filter((q) => q?.need === "dataset");
    const requires = browser.requires.filter((q) => q?.need !== "dataset");
    out.readiness.browser = { ...browser, requires, state: readinessOf(requires), unchecked: data.flatMap((q) => (Array.isArray(q.names) ? q.names : [])) };
  }
  const c = compute === undefined || compute === null || compute === "" ? "auto" : String(compute);
  const add = (where, req) => {
    const r = out.readiness[where];
    if (!r) return;
    const requires = [...(Array.isArray(r.requires) ? r.requires : []), req];
    out.readiness[where] = { ...r, requires, state: readinessOf(requires, r.state) };
  };
  if (!COMPUTE_VALUES.includes(c)) { add("browser", unknownCompute(c)); add("runner", unknownCompute(c)); }
  else if (c === "remote") { add("browser", requirement("remote_backend")); add("runner", requirement("remote_backend")); }
  else if (c === "browser") add("runner", requirement("compute_setting"));
  else if (c === "runner") {
    add("browser", requirement("compute_setting", {
      detail: "compute set to browser or auto",
      remedy: "Set compute to Auto or Browser (Settings → Compute, or the project's default).",
    }));
  }
  return out;
}

/** A runtime's reason in words (and its icon) when it is not ready. */
function readinessReason(r) {
  if (r.unchecked && r.state === "ready") return { label: t("ui.catalog.readiness.data_unchecked"), icon: "database" };
  if (r.state === "unsupported") {
    const q = r.requires.find((x) => x.need === "remote_backend" || x.need === "compute_setting");
    if (q?.need === "remote_backend") return { label: t("ui.catalog.readiness.remote_backend"), icon: "globe" };
    if (q) return { label: t("ui.catalog.readiness.compute_setting"), icon: "sliders" };
  }
  // needs_runner is also the state of a job service that is not running (a connected runner's that cannot start, or
  // this browser's for the kinds it runs): said as that, by its place, not "connect"
  if (r.state === "needs_runner" && r.requires.some((x) => x.need === "job_service") && !r.requires.some((x) => x.need === "runner")) {
    return { label: t(r.where === "browser" ? "ui.catalog.readiness.job_service_browser" : "ui.catalog.readiness.job_service"), icon: READINESS_ICON.needs_runner || "circleDashed" };
  }
  return { label: t(`ui.catalog.readiness.${r.state}`), icon: READINESS_ICON[r.state] || "circleDashed" };
}

/**
 * The eight fields of an entry here (pure; no DOM). ctx: {state (core entryState's answer, through pageReadiness),
 * runlog (ui/runlog.js), records (verification_records)}. → {registered, ready, verified, executed, needs_data,
 * needs_runner, remote, approval}.
 */
export function entryFields(e, { state, runlog, records } = {}) {
  const exec = Array.isArray(e?.exec) ? e.exec : [];
  const st = state || entryState(e, {});
  const runtimes = ["browser", "runner"].filter((w) => exec.includes(w)).map((w) => {
    const r = st.readiness?.[w] || { state: "unsupported", requires: [] };
    const unchecked = Array.isArray(r.unchecked) ? r.unchecked : null;
    return { where: w, state: r.state, ok: r.state === "ready" && !unchecked, unchecked, requires: Array.isArray(r.requires) ? r.requires : [], derived: Boolean(r.derived) };
  });
  const { keys, param } = dataOf(e);
  const approvals = (Array.isArray(e?.approvals) ? e.approvals : []).filter((a) => a && a.reason)
    .map((a) => ({ reason: String(a.reason), optional: a.reason === "remote_upload" }));
  return {
    registered: { implementation: st.implementation || "registered" },
    ready: { any: runtimes.some((r) => r.ok), runtimes },
    verified: verificationOf(e, records),
    executed: runOf(e?.id, runlog || { entries: {} }),
    needs_data: { keys, param, any: keys.length > 0 || Boolean(param) },
    needs_runner: !exec.includes("browser"),
    remote: e?.network_if === "allow_remote" ? "optional" : null,
    approval: approvals,
  };
}

/** Does an entry have a state flag (STATE_FLAGS)? */
export function hasFlag(fields, flag) {
  switch (flag) {
    case "ready": return fields.ready.any;
    case "verified": return fields.verified.status === "PASS";
    case "executed": return Boolean(fields.executed);
    case "needs_data": return fields.needs_data.any;
    case "needs_runner": return fields.needs_runner;
    case "remote_optional": return fields.remote === "optional";
    case "needs_approval": return fields.approval.some((a) => !a.optional);
    default: return true;
  }
}

/** The runtime select's test (RUNTIME_FILTERS). gateway: a network entry the browser runs (through the site's gateway). */
export function matchesRuntime(e, rt) {
  const exec = Array.isArray(e?.exec) ? e.exec : [];
  if (!rt) return true;
  if (rt === "browser") return exec.includes("browser");
  if (rt === "runner_only") return exec.includes("runner") && !exec.includes("browser");
  if (rt === "gateway") return Boolean(e.network) && exec.includes("browser");
  if (rt === "job") return Boolean(e.job);
  return true;
}

/** The dataset select's test: the entry names the key in data, takes it as its dataset argument, reads any store, or is its connector. */
export function matchesData(e, key) {
  if (!key) return true;
  const { keys, param } = dataOf(e);
  if (keys.includes(key) || keys.includes("*")) return true;
  if (e?.connector?.key === key) return true;
  if (!param) return false;
  const values = e.parameters?.properties?.[param]?.enum;
  return Array.isArray(values) ? values.includes(key) : true;
}

/** The verification select's test: a level that passed, "unverified" (no passing test: unverified, failed or blocked alike; labelled so), or any status word. */
export function matchesVerify(e, want) {
  if (!want) return true;
  const v = verificationOf(e);
  if (want === "unverified") return v.status !== "PASS";
  if (VERIFY_STATUS.includes(want)) return v.status === want;
  return v.levels.some((l) => l.level === want && l.status === "PASS");
}

/** The local backend select's test: a browser compute backend the entry lists; pyodide: every entry the browser runs. */
export function matchesBackend(e, be) {
  if (!be) return true;
  const exec = Array.isArray(e?.exec) ? e.exec : [];
  if (be === "pyodide") return exec.includes("browser");
  const backends = e?.execution?.browser_compute?.backends;
  return Array.isArray(backends) && backends.includes(be);
}

/** The dataset keys the catalog names (data, data_from enums, connector keys), with how many entries name each. */
export function datasetOptions(entries) {
  const counts = new Map();
  const add = (k) => { if (k && k !== "*") counts.set(k, (counts.get(k) || 0) + 1); };
  for (const e of entries) {
    const { keys, param } = dataOf(e);
    keys.forEach(add);
    const values = param ? e.parameters?.properties?.[param]?.enum : null;
    if (Array.isArray(values)) values.forEach(add);
    if (e.connector?.key) add(String(e.connector.key));
  }
  return [...counts.entries()].sort((a, b) => a[0].localeCompare(b[0]));
}

const NEED_ICON = { runner: "laptop", browser_runtime: "globe", dependency: "box", dataset: "database", network: "wifiOff", job_service: "workflow", compute_setting: "sliders", remote_backend: "globe", memory: "memory", authentication: "key" };

/**
 * A readiness reason in words: the need, its names, its detail when it says more than the need's default (a size, a
 * setting), and the remedy. The requirement's own sentences are English (written for the model). A Chinese page says
 * the remedy in the need's own words only when the requirement carries that need's default remedy (core/catalog.js
 * requirement()); a remedy of its own (the runner's "Place its files by hand …" for a dataset that cannot be
 * downloaded, a preference that leaves the browser out) is shown as written, marked English, never replaced by a
 * generic sentence that may not apply. where: the runtime it is a reason of ("browser" names this browser's job service).
 */
export function requirementView(r, where) {
  const need = typeof r?.need === "string" ? r.need : "dependency";
  const names = Array.isArray(r?.names) ? r.names.filter(Boolean) : [];
  const key = need === "job_service" && where === "browser" ? "ui.need.job_service_browser" : `ui.need.${need}`;
  const label = t(key) !== key ? t(key) : need;
  const base = requirement(need, { names });
  const remedyKey = `ui.need.remedy.${need}`;
  const own = String(r?.remedy || "");
  const generic = lang() !== "en" && t(remedyKey) !== remedyKey && (!own || own === base.remedy);
  const remedy = generic ? t(remedyKey) : own;
  const detail = typeof r?.detail === "string" && r.detail && r.detail !== base.detail ? r.detail : "";
  return h("li.need",
    icon(NEED_ICON[need] || "info", { size: 14 }),
    h("div.need__text",
      h("p.need__label", label, names.length ? h("span", " ", h("code", names.join(", "))) : null),
      detail ? h("p.need__detail.muted", { lang: "en" }, detail) : null,
      remedy ? h("p.need__remedy", { lang: generic ? null : "en" }, remedy) : null));
}

/** A verification level in words: 实时上游 2026-09-17 / 参考输出 / 网关模拟（非实时）… */
function levelLabel(l) {
  const key = `ui.catalog.level.${l.level}`;
  const name = t(key) !== key ? t(key) : l.level;
  return l.at ? `${name} ${String(l.at).slice(0, 10)}` : name;
}

/**
 * The state row: the eight fields as chips (icon + words), in order; compact rows leave out the "no" ones after the
 * fourth. remoteRunner: the paired runner is on another machine (never called 本机).
 */
export function stateRow(f, { compact = false, remoteRunner = false } = {}) {
  const out = [];
  // 1 registered (and how far: implemented, tested, deprecated)
  const impl = f.registered.implementation;
  out.push(chip({ label: impl === "registered" ? t("ui.catalog.state.registered") : `${t("ui.catalog.state.registered")} · ${t(`ui.catalog.impl.${impl}`)}`, icon: impl === "deprecated" ? "archive" : "list", tone: impl === "deprecated" ? "ochre" : "neutral", attrs: { "data-field": "registered" } }));
  // 2 ready, per runtime the entry runs in
  if (!f.ready.runtimes.length) out.push(chip({ label: t("ui.catalog.state.not_ready", { reason: t("ui.catalog.readiness.unsupported") }), icon: "ban", tone: "slate", dashed: true, attrs: { "data-field": "ready" } }));
  for (const r of f.ready.runtimes) {
    const where = whereLabel(r.where, remoteRunner);
    out.push(r.ok
      ? chip({ label: t("ui.catalog.state.ready", { where }), icon: "check", tone: "jade", attrs: { "data-field": "ready", "data-where": r.where } })
      : chip({ label: t("ui.catalog.state.ready_not", { where, reason: readinessReason(r).label }), icon: readinessReason(r).icon, tone: "slate", dashed: true, attrs: { "data-field": "ready", "data-where": r.where } }));
  }
  // 3 verified by test
  const v = f.verified;
  if (v.status === "PASS" && v.best) out.push(chip({ label: t("ui.catalog.state.verified", { level: levelLabel(v.best) }), icon: "shieldCheck", tone: "navy", attrs: { "data-field": "verified" } }));
  else out.push(chip({ label: t(`ui.catalog.verify.${v.status}`), icon: "circleDashed", tone: v.status === "FAIL" ? "vermilion" : "slate", dashed: true, attrs: { "data-field": "verified" } }));
  // 4 executed successfully, in this browser
  out.push(f.executed
    ? chip({ label: t("ui.catalog.state.executed", { n: f.executed.n }), icon: "circleCheck", tone: "navy", attrs: { "data-field": "executed" } })
    : chip({ label: t("ui.catalog.state.not_executed"), icon: "circleDashed", tone: "slate", dashed: true, attrs: { "data-field": "executed" } }));
  // 5 requires data
  if (f.needs_data.any) out.push(chip({ label: t("ui.catalog.state.needs_data", { names: dataNames(f.needs_data) }), icon: "database", tone: "ochre", attrs: { "data-field": "needs_data" } }));
  else if (!compact) out.push(chip({ label: t("ui.catalog.state.no_data"), icon: "database", tone: "neutral", attrs: { "data-field": "needs_data" } }));
  // 6 requires the runner
  if (f.needs_runner) out.push(chip({ label: t("ui.catalog.state.needs_runner"), icon: "laptop", tone: "ochre", attrs: { "data-field": "needs_runner" } }));
  else if (!compact) out.push(chip({ label: t("ui.catalog.state.no_runner"), icon: "laptop", tone: "neutral", attrs: { "data-field": "needs_runner" } }));
  // 7 remote compute: never required; optional where allow_remote sends data to a third-party service
  if (f.remote === "optional") out.push(chip({ label: t("ui.catalog.state.remote_optional"), icon: "globe", tone: "slate", dashed: true, attrs: { "data-field": "remote" } }));
  else if (!compact) out.push(chip({ label: t("ui.catalog.state.no_remote"), icon: "globe", tone: "neutral", attrs: { "data-field": "remote" } }));
  // 8 approvals, in words
  const asked = f.approval.filter((a) => !a.optional);
  if (asked.length) out.push(chip({ label: t("ui.catalog.state.needs_approval", { what: asked.map((a) => t(`ui.catalog.approval.${a.reason}`)).join(t("ui.catalog.list_sep")) }), icon: "shieldAlert", tone: "ochre", attrs: { "data-field": "approval" } }));
  else if (!compact) out.push(chip({ label: t("ui.catalog.state.no_approval"), icon: "shield", tone: "neutral", attrs: { "data-field": "approval" } }));
  return out;
}

/** 浏览器 / 本机 Runner / 远程 Runner. */
function whereLabel(where, remoteRunner) {
  return t(where === "runner" && remoteRunner ? "ui.catalog.where.runner_remote" : where === "runner" ? "ui.catalog.where.runner" : "ui.catalog.where.browser");
}

function dataNames(d) {
  const names = d.keys.map((k) => (k === "*" ? t("ui.catalog.data_any") : k));
  if (d.param) names.push(t("ui.catalog.data_param", { param: d.param }));
  return names.join(t("ui.catalog.list_sep"));
}

export function mountCatalog(app, main) {
  const page = h("div.page.page--catalog");
  fill(main, page);
  // kept on the app, so leaving the catalog and coming back finds the same search and filters; the address wins
  const ui = (app.state.catalogUI ||= { category: "", query: "", flags: [], rt: "", ds: "", ver: "", be: "" });
  ui.flags ||= [];
  /** An address that names any catalog param sets the whole view (the ones it leaves out are cleared). → changed? */
  const applyParams = (p = {}) => {
    if (!CATALOG_PARAMS.some((k) => p[k] !== undefined)) return false;
    const next = {
      category: String(p.category ?? ""), query: String(p.q ?? ""),
      flags: String(p.f ?? "").split(",").filter((x) => STATE_FLAGS.includes(x)),
      rt: String(p.rt ?? ""), ds: String(p.ds ?? ""), ver: String(p.ver ?? ""), be: String(p.be ?? ""),
    };
    const same = next.category === ui.category && next.query === ui.query && next.flags.join(",") === ui.flags.join(",") && ["rt", "ds", "ver", "be"].every((k) => next[k] === ui[k]);
    Object.assign(ui, next);
    return !same;
  };
  applyParams(app.state.route.params || {});
  let category = ui.category || "";
  let query = ui.query || "";
  const input = h("input.input.catalog__search-input", { type: "search", placeholder: t("ui.catalog.search_ph"), "aria-label": t("ui.catalog.search"), autocomplete: "off", spellcheck: "false" });
  input.value = query;
  const filters = h("div.catalog__filters");
  const results = h("div.catalog__results", { "aria-live": "polite" });
  const detail = h("aside.catalog__detail", { "aria-label": t("ui.catalog.detail") });

  const params = () => ({ q: query.trim(), category, f: ui.flags.join(","), rt: ui.rt, ds: ui.ds, ver: ui.ver, be: ui.be });
  const href = (entryId) => routeHref({ name: "catalog", entryId: entryId || null, params: params() });
  /** The view in the address, without a history entry or a hashchange (the page is already showing it). */
  const syncUrl = () => {
    const next = href(app.state.route.entryId);
    try {
      if (location.hash !== next) history.replaceState(history.state, "", `${location.pathname}${location.search}${next}`);
      if (app.router) app.router.hash = next;
    } catch { /* no history here (a test DOM) */ }
    app.state.route = { ...app.state.route, params: Object.fromEntries(Object.entries(params()).filter(([, v]) => v)) };
  };

  // the context of every entry's state: the runtimes, this project's web access and compute preference, and what this
  // browser's hub holds for this project (known only once Settings → Data read it, for the project open now)
  const stateCtx = () => {
    const bd = app.state.browserData;
    const dataKnown = Boolean(bd) && (bd.projectId || null) === (app.state.project?.id || null);
    return {
      browser: app.state.browser?.status, runner: app.state.runner?.status,
      web: typeof app.webOn === "function" ? app.webOn() : undefined,
      built: dataKnown && Array.isArray(bd.built) ? bd.built : [], dataKnown,
      compute: typeof app.compute === "function" ? app.compute() : app.state.settings?.compute,
      // the browser's job service: which job kinds it runs here, whether the research snapshots are built in it, and
      // which ADMET models
      jobs: app.runtimes?.jobs ? { status: app.runtimes.jobs.status, kinds: app.runtimes.jobs.kinds?.() || [], snapshots: app.state.browserJobs?.snapshots ?? null, admet_models: app.state.browserJobs?.admet ?? null } : undefined,
    };
  };
  let fieldsCache = null;
  const fieldsOf = (e) => {
    if (!fieldsCache) {
      const ctx = stateCtx();
      const runlog = readRunlog();
      const records = app.catalog?.verificationRecords || {};
      fieldsCache = { ctx, runlog, records, map: new Map() };
    }
    let f = fieldsCache.map.get(e.id);
    if (!f) {
      f = entryFields(e, { state: pageReadiness(entryState(e, fieldsCache.ctx), fieldsCache.ctx), runlog: fieldsCache.runlog, records: fieldsCache.records });
      fieldsCache.map.set(e.id, f);
    }
    return f;
  };
  const invalidate = () => { fieldsCache = null; };

  function render() {
    const cat = app.catalog;
    invalidate();
    if (!cat || !cat.entries.length) {
      fill(page, h("div.page__inner", header(cat), emptyState({ icon: "library", title: t("ui.catalog.empty_title"), body: t("ui.catalog.empty"), actions: [button({ label: t("ui.runner.connect"), icon: "plug", onClick: () => app.navigate({ name: "settings", tab: "compute" }) })] })));
      return;
    }
    const nav = h("nav.catalog__cats", { "aria-label": t("ui.catalog.categories") },
      catButton("", t("ui.catalog.all"), cat.entries.length, "library"),
      cat.categories.map((c) => catButton(c.id, glossary.category(c.id), c.count, CAT_ICON[c.id] || "box")));
    fill(page, h("div.catalog",
      header(cat),
      h("div.catalog__search", icon("search", { size: 16 }), input),
      filters,
      h("div.catalog__grid", nav, results, detail)));
    renderResults();
    renderDetail();
  }

  function header(cat) {
    return h("header.catalog__head",
      h("h1.catalog__title.serif", t("ui.nav.catalog")),
      h("p.catalog__lede", cat ? t("ui.catalog.lede", { entries: cat.entries.length, core: cat.core.length }) : t("ui.catalog.lede_empty")));
  }

  function catButton(id, label, count, ic) {
    return h("button", { type: "button", class: ["catalog__cat", category === id && "is-on"], "aria-pressed": String(category === id), onClick: () => { category = id; ui.category = id; syncUrl(); render(); } },
      icon(ic, { size: 16 }), h("span.catalog__cat-label", label), h("span.catalog__cat-n.num", String(count)));
  }

  /** The entries of the category and search, through the selects (the chips are counted over these). */
  function baseHits() {
    const cat = app.catalog;
    // no words: every entry of the category in catalog order; words: the 200 best matches
    const hits = query.trim() ? cat.search(query, { category: category || undefined, limit: 200 }) : cat.entries.filter((e) => !category || e.category === category);
    return hits.filter((e) => matchesRuntime(e, ui.rt) && matchesData(e, ui.ds) && matchesVerify(e, ui.ver) && matchesBackend(e, ui.be));
  }

  function renderFilters(base) {
    const cat = app.catalog;
    const counts = Object.fromEntries(STATE_FLAGS.map((x) => [x, 0]));
    for (const e of base) { const f = fieldsOf(e); for (const x of STATE_FLAGS) if (hasFlag(f, x)) counts[x] += 1; }
    const chips = h("div.filter-chips.catalog__flags", { role: "group", "aria-label": t("ui.catalog.filter.flags") }, STATE_FLAGS.map((x) => {
      const on = ui.flags.includes(x);
      return h("button", {
        type: "button", class: ["filter-chip", on && "is-on"], "aria-pressed": String(on), "data-flag": x,
        onClick: () => { ui.flags = on ? ui.flags.filter((y) => y !== x) : [...ui.flags, x]; syncUrl(); renderResults(); },
      }, icon(FLAG_ICON[x], { size: 12 }), h("span", t(`ui.catalog.flag.${x}`)), h("span.filter-chip__n.num", String(counts[x])));
    }));
    const levels = new Set();
    for (const e of cat.entries) for (const l of verificationOf(e).levels) if (l.status === "PASS") levels.add(l.level);
    const sel = (key, label, options) => {
      const field = selectField({ label, value: ui[key], options: [{ value: "", label: t("ui.catalog.filter.any") }, ...options], onChange: (v) => { ui[key] = v; syncUrl(); renderResults(); } });
      field.input.dataset.filter = key;
      return field;
    };
    fill(filters,
      chips,
      h("div.catalog__selects",
        sel("rt", t("ui.catalog.filter.runtime"), RUNTIME_FILTERS.map((x) => ({ value: x, label: `${t(`ui.catalog.rt.${x}`)} (${cat.entries.filter((e) => matchesRuntime(e, x)).length})` }))),
        sel("ds", t("ui.catalog.filter.dataset"), datasetOptions(cat.entries).map(([k, n]) => ({ value: k, label: `${k} (${n})` }))),
        sel("ver", t("ui.catalog.filter.verified"), [...VERIFY_LEVELS.filter((l) => levels.has(l)).map((l) => ({ value: l, label: `${t(`ui.catalog.level.${l}`)} (${cat.entries.filter((e) => matchesVerify(e, l)).length})` })),
          { value: "unverified", label: `${t("ui.catalog.filter.unverified")} (${cat.entries.filter((e) => matchesVerify(e, "unverified")).length})` }]),
        sel("be", t("ui.catalog.filter.backend"), BACKEND_FILTERS.map((b) => ({ value: b, label: `${computeBackendLabel(b)} (${cat.entries.filter((e) => matchesBackend(e, b)).length})` })))),
      ui.flags.length || ui.rt || ui.ds || ui.ver || ui.be ? h("div.row", button({ label: t("ui.catalog.filter.clear"), icon: "x", size: "sm", variant: "ghost", onClick: () => { ui.flags = []; ui.rt = ui.ds = ui.ver = ui.be = ""; syncUrl(); renderResults(); } })) : null);
  }

  function renderResults() {
    const base = baseHits();
    renderFilters(base);
    const hits = base.filter((e) => ui.flags.every((x) => hasFlag(fieldsOf(e), x)));
    fill(results,
      h("p.catalog__count", hits.length >= 200 && query.trim() ? t("ui.catalog.count_top", { n: hits.length }) : t("ui.catalog.count", { n: hits.length })),
      hits.length ? h("ul.catalog__list", { role: "list" }, hits.map((e) => entryRow(e))) : h("p.muted", t("ui.catalog.no_match")));
  }

  function entryRow(e) {
    const on = app.state.route.entryId === e.id;
    return h("li", h("a", { href: href(e.id), class: ["entry-row", on && "is-on"], "aria-current": on ? "true" : null, "data-entry": e.id },
      h("div.entry-row__head",
        h("span.entry-row__title", titleOf(e)),
        h("span.entry-row__kind", t(`ui.catalog.kind.${e.kind}`) !== `ui.catalog.kind.${e.kind}` ? t(`ui.catalog.kind.${e.kind}`) : e.kind)),
      h("code.entry-row__id", e.id),
      e.summary ? h("p.entry-row__summary", { lang: guessLang(e.summary) || null }, inlineLiterals(e.summary)) : null,
      h("div.entry-row__badges.state-row", stateRow(fieldsOf(e), { compact: true, remoteRunner: runnerIsRemote(app) }))));
  }

  function renderDetail() {
    const id = app.state.route.entryId;
    const e = id ? app.catalog.byId[id] || app.catalog.entry(id) : null;
    detail.hidden = !e;
    page.querySelector(".catalog")?.classList.toggle("has-detail", Boolean(e));
    if (!e) { clear(detail); return; }
    const f = fieldsOf(e);
    const core = app.catalog.core.filter((c) => c.maps_to === e.id);
    const props = e.parameters?.properties || {};
    const req = new Set(e.parameters?.required || []);
    const rows = Object.entries(props);
    const example = e.example ? JSON.stringify(e.example, null, 2) : "";
    fill(detail, h("div.entry",
      h("div.entry__top",
        h("a.icon-btn.icon-btn--ghost.icon-btn--sm", { href: href(null), "aria-label": t("ui.action.close") }, icon("x", { size: 14 }))),
      h("p.kicker", glossary.category(e.category)),
      h("h2.entry__title", titleOf(e)),
      h("code.entry__id", e.id),
      e.summary ? h("p.entry__summary", { lang: guessLang(e.summary) || null }, inlineLiterals(e.summary)) : null,
      stateSection(f),
      readinessSection(e, f),
      verificationSection(f),
      implementationSection(e, f),
      backendsSection(e),
      limitsSection(e),
      e.kind === "connector" && e.connector ? sourceSection(e) : null,
      e.available === false && e.missing?.length ? h("p.entry__missing", icon("alert", { size: 14 }), t("ui.catalog.missing", { deps: e.missing.join(", ") })) : null,
      core.length ? h("p.entry__core", icon("sparkles", { size: 14 }), t("ui.catalog.core", { names: core.map((c) => c.name).join(", ") })) : null,
      h("section.entry__sec", h("h3", t("ui.catalog.params")),
        rows.length ? h("div.tablewrap", { tabindex: "0", role: "region", "aria-label": t("ui.catalog.params") }, h("table.params",
          h("thead", h("tr", h("th", t("ui.catalog.param")), h("th", t("ui.catalog.type")), h("th", t("ui.catalog.desc")))),
          h("tbody", rows.map(([name, sch]) => h("tr",
            h("td", h("code", name), req.has(name) ? h("span.params__req", { "aria-label": t("ui.catalog.required") }, " *") : null),
            h("td.params__type", typeOf(sch)),
            h("td", { lang: guessLang(sch.description) || null }, inlineLiterals(sch.description || ""), sch.default !== undefined ? h("span.muted", ` (${t("ui.catalog.default")} ${JSON.stringify(sch.default)})`) : null, sch.enum ? h("span.muted", ` · ${sch.enum.join(" / ")}`) : null)))))) : h("p.muted.small", t("ui.catalog.no_params"))),
      // a scrolling <pre> is reachable by keyboard (tabindex=0) and named (WCAG 2.1.1)
      example ? h("section.entry__sec", h("div.entry__sec-head", h("h3", t("ui.catalog.example")), copyButton(example)), h("pre.code-block", { tabindex: "0", role: "region", "aria-label": t("ui.catalog.example") }, example)) : null,
      e.skill ? h("section.entry__sec", h("h3", t("ui.catalog.skill")), keyValue([
        [t("ui.catalog.version"), e.skill.version || "—"],
        [t("ui.catalog.pinned"), e.skill.pinned ? t("ui.common.yes") : t("ui.common.no")],
        [t("ui.catalog.claim_kinds"), (e.skill.claim_kinds || []).map((k) => glossary.claimKind(k)).join("、") || "—"],
        [t("ui.catalog.forbidden"), (e.skill.forbidden_claims || []).map((k) => glossary.claimKind(k)).join("、") || "—"],
        [t("ui.catalog.max_tier"), e.skill.max_evidence_tier ? glossary.evidenceTier(e.skill.max_evidence_tier) : "—"],
        [t("ui.catalog.hosts"), (e.skill.network || []).join(", ") || t("ui.catalog.no_network")],
        [t("ui.catalog.content_hash"), e.skill.content_hash ? hashBadge(e.skill.content_hash) : "—"],
      ])) : null,
      h("div.entry__actions", button({ label: t("ui.catalog.try"), variant: "primary", icon: "message", onClick: () => {
        app.state.pendingPrompt = t("ui.catalog.try_prompt", { title: titleOf(e), id: e.id });
        app.newConversation();
      } }))));
  }

  /** 状态: the eight fields, each with its value in words (none left out here). */
  function stateSection(f) {
    const chips = stateRow(f, { compact: false, remoteRunner: runnerIsRemote(app) });
    const byField = (name) => chips.filter((c) => c.getAttribute("data-field") === name);
    const last = f.executed?.last;
    return h("section.entry__sec.entry__state", h("h3", t("ui.catalog.sec.state")),
      h("dl.kv.state-fields", STATE_FIELDS.map((name) => [
        h("dt", t(`ui.catalog.field.${name}`)),
        h("dd", h("div.state-row", byField(name)),
          name === "executed" && last ? h("p.muted.small.state-fields__last", lastRunLine(last)) : null),
      ])));
  }

  /** 最近一次：浏览器 · WebGPU，2026-10-19 09:12 (where, the compute backend when one was recorded, and when). */
  function lastRunLine(last) {
    const where = last.where === "browser" || last.where === "runner" ? whereLabel(last.where, runnerIsRemote(app)) : "—";
    const kernel = last.kernel ? ` · ${computeBackendLabel(last.kernel)}` : "";
    const at = (Number.isFinite(last.at) && formatDateTime(last.at)) || "—";
    return t("ui.catalog.last_run", { where, kernel, at });
  }

  /**
   * 就绪情况: per runtime, the state and each reason with its remedy; and where a call would run now. The router's
   * placement does not weigh web access or data (core/router.js: the call's own envelope says those), so a place it
   * picks whose readiness here is not ready is said as not running, with that runtime's reason.
   */
  function readinessSection(e, f) {
    const placement = app.toolRouter?.placement?.(e) || null;
    const remote = runnerIsRemote(app);
    const items = f.ready.runtimes.map((r) => h("div.readiness",
      h("p.readiness__head", icon(r.ok ? "check" : readinessReason(r).icon, { size: 14 }),
        h("span", whereLabel(r.where, remote)), h("span.muted", ` · ${r.ok ? t("ui.catalog.readiness.ready") : readinessReason(r).label}`),
        r.derived ? h("span.muted", ` · ${t("ui.catalog.derived")}`) : null),
      r.requires.length ? h("ul.needs", { role: "list" }, r.requires.map((q) => requirementView(q, r.where))) : null,
      r.unchecked ? h("p.need__remedy.readiness__unchecked", icon("database", { size: 12 }), " ",
        r.unchecked.length ? h("code", r.unchecked.join(", ")) : null, r.unchecked.length ? " " : null, t("ui.catalog.data_unchecked_note")) : null));
    let line = null;
    if (placement) {
      const rt = placement.where === "browser" || placement.where === "runner" ? f.ready.runtimes.find((r) => r.where === placement.where) : null;
      const where = placement.where === "page" ? t("ui.catalog.where.page") : placement.where ? whereLabel(placement.where, remote) : "";
      if (!placement.where) line = { run: false, text: placement.message || t("ui.catalog.would_not_run") };
      else if (rt && !rt.ok && rt.unchecked && rt.state === "ready") line = { run: true, text: t("ui.catalog.would_run_unchecked", { where }) };
      else if (rt && !rt.ok) line = { run: false, text: t("ui.catalog.would_stop", { where, reason: readinessReason(rt).label }) };
      else line = { run: true, text: t("ui.catalog.would_run", { where }) };
    }
    return h("section.entry__sec", h("h3", t("ui.catalog.sec.readiness")),
      items.length ? items : h("p.muted.small", t("ui.catalog.readiness.unsupported")),
      line ? h("p.entry__placement", { "data-would-run": String(line.run) }, icon(line.run ? "play" : "circleDashed", { size: 14 }), line.text) : null,
      f.approval.length ? h("p.entry__approvals", icon("shieldAlert", { size: 14 }), t("ui.catalog.approvals_line", { what: f.approval.map((a) => t(`ui.catalog.approval_long.${a.reason}`) !== `ui.catalog.approval_long.${a.reason}` ? t(`ui.catalog.approval_long.${a.reason}`) : a.reason).join(t("ui.catalog.list_sep")) })) : null);
  }

  /** 测试验证: each level, its status, date and the committed record it rests on. */
  function verificationSection(f) {
    const v = f.verified;
    return h("section.entry__sec", h("h3", t("ui.catalog.sec.verification")),
      v.levels.length ? h("ul.verify-levels", { role: "list" }, v.levels.map((l) => h("li",
        icon(l.status === "PASS" ? "check" : "circleDashed", { size: 14 }),
        h("span", levelLabel(l)), h("span.muted", ` · ${t(`ui.catalog.verify.${l.status}`)}`),
        l.record ? h("span.muted", " · ", h("code", l.record)) : null)))
        : h("p.muted.small", t("ui.catalog.verify.none")),
      h("p.inspector__note", t("ui.catalog.verify_note")));
  }

  function implementationSection(e, f) {
    const impl = e.implementation && typeof e.implementation === "object" ? e.implementation : null;
    return h("section.entry__sec", h("h3", t("ui.catalog.sec.implementation")), keyValue([
      [t("ui.catalog.impl_state"), t(`ui.catalog.impl.${f.registered.implementation}`)],
      [t("ui.catalog.impl_ref"), impl?.ref ? h("code.kv__code", impl.ref) : "—"],
      [t("ui.catalog.version"), impl?.version ? h("code.kv__code", impl.version) : "—"],
      impl?.content_hash ? [t("ui.catalog.content_hash"), hashBadge(impl.content_hash)] : null,
      e.deprecated ? [t("ui.catalog.impl.deprecated"), String(e.deprecated)] : null,
    ]));
  }

  /** 后端: the browser's compute backends and the engines (with their readiness on the runner), GPU, heavy packages. */
  function backendsSection(e) {
    const bc = e.execution?.browser_compute;
    const engines = Array.isArray(e.execution?.engines) ? e.execution.engines.filter((g) => g && g.id) : [];
    const rows = [
      bc && Array.isArray(bc.backends) ? [t("ui.catalog.backends_browser"), h("span", bc.backends.map((b) => computeBackendLabel(b)).join(" · "), bc.kernel ? h("span.muted", " · ", h("code", bc.kernel)) : null)] : null,
      bc?.precision ? [t("ui.catalog.precision"), h("span", { lang: "en" }, bc.precision)] : null,
      engines.length ? [t("ui.catalog.engines"), h("ul.engine-list", { role: "list" }, engines.map((g) => {
        const r = g.readiness?.runner || (typeof g.readiness?.state === "string" ? g.readiness : null);
        return h("li", h("code", g.id),
          g.selected_by?.arg ? h("span.muted", ` · ${g.selected_by.arg}=${g.selected_by.value}`) : null,
          g.device ? h("span.muted", ` · ${t(`ui.catalog.device.${["cpu", "gpu", "remote"].includes(g.device) ? g.device : "cpu"}`)}`) : null,
          r?.state ? h("span.muted", ` · ${t("ui.catalog.on_runner", { state: t(`ui.catalog.readiness.${r.state}`) })}`) : h("span.muted", ` · ${t("ui.catalog.engine_unknown")}`));
      }))] : null,
      e.gpu ? [t("ui.catalog.gpu"), t("ui.catalog.gpu_yes")] : null,
      (e.heavy || []).length ? [t("ui.catalog.heavy"), h("code.kv__code", e.heavy.join(", "))] : null,
      (e.pyodide_packages || []).length ? [t("ui.catalog.pyodide_packages"), h("code.kv__code", e.pyodide_packages.join(", "))] : null,
      ...browserJobRows(e),
    ];
    if (!rows.some(Boolean)) return null;
    return h("section.entry__sec", h("h3", t("ui.catalog.sec.backends")), keyValue(rows));
  }

  /** A browser job kind's own words (entry.execution.browser_job): the packages its job worker loads, the chemistry it
   * runs in the page (the ADMET kinds: RDKit.js and its ports), and what it reads here; the rest needs the runner. */
  function browserJobRows(e) {
    const bj = e.execution?.browser_job;
    if (!bj || typeof bj !== "object") return [];
    const words = (v) => (typeof v === "string" ? v : v && typeof v === "object" ? [v.engine || v.name, v.version].filter(Boolean).join(" ") : "");
    const packages = Array.isArray(bj.pyodide_packages) ? bj.pyodide_packages : Array.isArray(bj.packages) ? bj.packages : [];
    return [
      packages.length && !(e.pyodide_packages || []).length ? [t("ui.catalog.browser_job"), h("code.kv__code", packages.join(", "))] : null,
      words(bj.chemistry) ? [t("ui.catalog.browser_chemistry"), h("span", { lang: "en" }, words(bj.chemistry))] : null,
      typeof bj.inputs === "string" && bj.inputs ? [t("ui.catalog.browser_inputs"), h("span", { lang: "en" }, bj.inputs)] : null,
    ];
  }

  /** 限制: the largest request one call takes, per runtime (the entry's own, else the catalog's). */
  function limitsSection(e) {
    const own = e.limits?.max_request_bytes || {};
    const doc = app.catalog?.limits?.max_request_bytes || {};
    const exec = Array.isArray(e.exec) ? e.exec : [];
    const rows = ["browser", "runner"].filter((w) => exec.includes(w)).map((w) => {
      const n = Number(own[w] ?? doc[w]);
      return Number.isFinite(n) && n > 0 ? [whereLabel(w, runnerIsRemote(app)), t("ui.catalog.max_request", { size: formatBytes(n) })] : null;
    });
    if (!rows.some(Boolean)) return null;
    return h("section.entry__sec", h("h3", t("ui.catalog.sec.limits")), keyValue(rows));
  }

  /** 数据源: a connector's source, host, licence (未声明许可 when it states none), docs and how it is reached. */
  function sourceSection(e) {
    const c = e.connector;
    const exec = Array.isArray(e.exec) ? e.exec : [];
    return h("section.entry__sec", h("h3", t("ui.catalog.sec.source")), keyValue([
      [t("ui.catalog.source_name"), c.name || c.key],
      [t("ui.catalog.source_host"), h("code.kv__code", c.host || (e.hosts || []).join(", ") || "—")],
      [t("ui.catalog.source_licence"), c.license ? c.license : h("span.muted", t("ui.source.unlicensed"))],
      // a link only for an http(s) address (core/governance.js safeUrl): the merged catalog carries the runner's text
      c.docs ? [t("ui.catalog.source_docs"), safeUrl(c.docs) ? h("a", { href: safeUrl(c.docs), target: "_blank", rel: "noopener noreferrer" }, c.docs, icon("external", { size: 12 })) : h("span.mono", String(c.docs))] : null,
      [t("ui.catalog.source_how"), exec.includes("browser") ? t("ui.catalog.source_gateway") : t("ui.catalog.source_runner")],
      [t("ui.catalog.source_data"), h("a", { href: "#/settings/data" }, t("ui.catalog.source_data_link"))],
    ]));
  }

  input.addEventListener("input", debounce(() => { query = input.value; ui.query = query; syncUrl(); renderResults(); }, 120));
  const rerenderResults = () => { if (app.catalog?.entries.length) { invalidate(); renderResults(); renderDetail(); } };
  const offs = [
    app.on("catalog", render), app.on("runtime", rerenderResults), app.on("evidence", rerenderResults), app.on("web", rerenderResults),
    // the compute preference (global or the project's default) and the project (its own store) change readiness
    app.on("settings", (ev) => { if (!Array.isArray(ev?.changed) || ev.changed.includes("compute") || ev.changed.includes("*")) rerenderResults(); }),
    app.on("project", rerenderResults),
  ];
  render();
  if (!app.state.route.entryId && app.state.layout !== "mobile") requestAnimationFrame(() => input.focus({ preventScroll: true }));
  return {
    el: page,
    update() {
      if (!app.catalog?.entries.length) { render(); return; }
      // an address with other filters (a link, the back button): the whole view follows it
      if (applyParams(app.state.route.params || {})) {
        category = ui.category; query = ui.query; input.value = query;
        render();
        return;
      }
      for (const a of results.querySelectorAll(".entry-row")) {
        const on = a.getAttribute("data-entry") === app.state.route.entryId;
        a.classList.toggle("is-on", on);
        if (on) a.setAttribute("aria-current", "true"); else a.removeAttribute("aria-current");
      }
      renderDetail();
      if (app.state.layout === "mobile") detail.scrollIntoView?.({ block: "start" });
    },
    destroy: () => offs.forEach((off) => off()),
  };
}

const FLAG_ICON = { ready: "check", verified: "shieldCheck", executed: "circleCheck", needs_data: "database", needs_runner: "laptop", remote_optional: "globe", needs_approval: "shieldAlert" };

/**
 * Docstring text with its reStructuredText inline literals (``X``) as <code>: built as nodes (never innerHTML), the
 * text itself unchanged.
 */
export function inlineLiterals(text) {
  const s = String(text || "");
  const out = [];
  let last = 0;
  for (const m of s.matchAll(/``([^`]+)``/g)) {
    if (m.index > last) out.push(s.slice(last, m.index));
    out.push(h("code", m[1]));
    last = m.index + m[0].length;
  }
  if (last < s.length) out.push(s.slice(last));
  return out;
}

function titleOf(e) {
  const tt = e.title;
  if (tt && typeof tt === "object") return tt[lang()] || tt.en || tt.zh || e.id;
  return tt || e.id;
}

function typeOf(s) {
  if (!s) return "";
  if (s.type === "array") return `${typeOf(s.items) || "any"}[]`;
  if (Array.isArray(s.type)) return s.type.join(" | ");
  if (s.anyOf) return s.anyOf.map(typeOf).join(" | ");
  return s.type || (s.enum ? "enum" : "any");
}

