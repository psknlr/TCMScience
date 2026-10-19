// The administrators' analytics dashboard (studio/docs/TELEMETRY.md, "管理后台"). Served only to a signed-in
// administrator (edge/src/admin.js) and reads /api/admin/telemetry/* with the session cookie. Every name a page reported
// and everything a visitor wrote is set as text, never as markup. The text of conversations is fetched only when the
// administrator asks for it, and each fetch is written to the administrator log.

import { columnChart, dataTable, el, hbars, lineChart, resetCharts, sparkline, stackBar } from "./charts.js";
import { lang, setLang, t } from "./i18n.js";

const PERIODS = [7, 30, 90];
const STATUSES = ["completed", "cancelled", "interrupted", "failed"];
// a turn's ending as a state: its colour, icon and word always together
const OUTCOME = {
  completed: { cls: "good", state: "good" },
  cancelled: { cls: "other", state: "none" },
  interrupted: { cls: "serious", state: "serious" },
  failed: { cls: "critical", state: "critical" },
};
const DATASETS = ["daily", "turns", "tools", "connectors", "geo", "security", "conversations"];
const ADMIN_ONLY = new Set(["security", "conversations"]);
const FILTER = /^[A-Za-z0-9_.:@/+-]{1,80}$/;

const state = { days: 7, provider: "", model: "", country: "", seq: 0, data: null };
// the conversation records on screen, kept across a redraw (a change of language) but not across a change of period
const records = { key: "", items: [], next: null, status: "", owner: "", loaded: false };
const REFERENCE = /^[0-9a-f]{12,64}$/;
const $ = (id) => document.getElementById(id);

class Expired extends Error {}

// ------------------------------------------------------------------------------------------------- formatting

const locale = () => (lang() === "zh" ? "zh-CN" : "en-US");
const num = (v) => (typeof v === "number" && Number.isFinite(v) ? v : null);
const fmt = {
  int: (v) => (num(v) === null ? "—" : new Intl.NumberFormat(locale()).format(Math.round(v))),
  compact: (v) => (num(v) === null ? "—" : new Intl.NumberFormat(locale(), { notation: "compact", maximumFractionDigits: 1 }).format(v)),
  pct: (v, digits = 1) => (num(v) === null ? "—" : new Intl.NumberFormat(locale(), { style: "percent", maximumFractionDigits: digits }).format(v)),
  ms: (v) => {
    if (num(v) === null) return "—";
    if (v < 1000) return `${Math.round(v)} ms`;
    if (v < 60000) return `${(v / 1000).toFixed(v < 10000 ? 1 : 0)} s`;
    const m = Math.floor(v / 60000);
    const s = Math.round((v % 60000) / 1000);
    return lang() === "zh" ? `${m} 分 ${s} 秒` : `${m} min ${s} s`;
  },
  time: (iso) => {
    const d = new Date(iso);
    return Number.isNaN(d.getTime()) ? "—" : d.toLocaleString(locale(), { hour12: false });
  },
};
const clip = (s, n) => {
  const str = String(s || "");
  return str.length > n ? `${str.slice(0, n - 1)}…` : str;
};
const list = (items) => items.join(lang() === "zh" ? "、" : ", ");

let regions = null;
function countryName(code) {
  if (!code || code === "XX") return t("unknown_place");
  try {
    if (!regions || regions.lang !== lang()) regions = { lang: lang(), names: new Intl.DisplayNames([locale()], { type: "region" }) };
    const name = regions.names.of(code);
    return name && name !== code ? `${name} (${code})` : code;
  } catch {
    return code;
  }
}

// ------------------------------------------------------------------------------------------------- the server

async function api(path, init = {}) {
  const res = await fetch(path, { credentials: "same-origin", cache: "no-store", ...init, headers: { Accept: "application/json", ...(init.headers || {}) } });
  if (res.status === 401) {
    expired();
    throw new Expired("expired");
  }
  let body = null;
  try {
    body = await res.json();
  } catch {
    body = null;
  }
  if (!res.ok) {
    const e = body?.error;
    throw new Error((lang() === "zh" ? e?.message : e?.type) || `HTTP ${res.status}`);
  }
  return body;
}

function say(text, error = false) {
  const s = $("status");
  s.className = `status-line${error ? " is-error" : ""}`;
  s.textContent = text;
}

function expired() {
  const s = $("status");
  s.className = "status-line is-error";
  s.replaceChildren(t("expired"), " ", el("a", { href: "/admin/", text: t("signin_again") }));
}

// ------------------------------------------------------------------------------------------------- the address

function readUrl() {
  const q = new URLSearchParams(location.search);
  const d = Number(q.get("days"));
  if (PERIODS.includes(d)) state.days = d;
  for (const k of ["provider", "model", "country"]) {
    const v = q.get(k) || "";
    state[k] = FILTER.test(v) ? v : "";
  }
}

function writeUrl() {
  const q = new URLSearchParams();
  if (state.days !== 7) q.set("days", String(state.days));
  for (const k of ["provider", "model", "country"]) if (state[k]) q.set(k, state[k]);
  const s = q.toString();
  history.replaceState(null, "", `/admin/${s ? `?${s}` : ""}`);
}

// ------------------------------------------------------------------------------------------------- loading

async function load() {
  const seq = ++state.seq;
  const content = $("content");
  content.classList.add("is-loading");
  content.setAttribute("aria-busy", "true");
  say(t("loading"));
  try {
    const q = new URLSearchParams({ days: String(state.days) });
    for (const k of ["provider", "model", "country"]) if (state[k]) q.set(k, state[k]);
    const data = await api(`/api/admin/telemetry/summary?${q}`);
    if (seq !== state.seq) return; // a newer request has been made since
    state.data = data;
    render();
    say("");
  } catch (err) {
    if (seq !== state.seq || err instanceof Expired) return;
    say(t("load_failed", { reason: err.message }), true);
  } finally {
    if (seq === state.seq) {
      content.classList.remove("is-loading");
      content.setAttribute("aria-busy", "false");
    }
  }
}

function changed() {
  writeUrl();
  load();
}

// ------------------------------------------------------------------------------------------------- the frame

function statics() {
  document.title = t("title");
  $("title").textContent = t("title");
  $("refresh").textContent = t("refresh");
  $("signout").textContent = t("signout");
  const other = $("lang");
  other.textContent = t("language");
  other.lang = lang() === "zh" ? "en" : "zh-Hans";
  $("filters").setAttribute("aria-label", t("filters"));
}

function head(data) {
  const off = Number(data.config?.utc_offset ?? 8);
  const tz = `UTC${off >= 0 ? "+" : "−"}${Math.abs(off)}`;
  $("sub").textContent = t("sub", { from: data.period.from, to: data.period.to, tz, at: fmt.time(data.generated) });
}

function filters(data) {
  const f = data.usage?.filters || { providers: [], models: [], countries: [] };
  const seg = el("div", { class: "seg", role: "group", "aria-label": t("period") }, PERIODS.map((d) => el("button", {
    type: "button", "aria-pressed": String(d === state.days), text: t("days", { n: d }),
    onclick: () => {
      if (state.days === d) return;
      state.days = d;
      changed();
    },
  })));
  const select = (key, options, label, name = (v) => v) => {
    const values = [...new Set([...(options || []), state[key]].filter(Boolean))];
    const sel = el("select", { class: "select" }, el("option", { value: "", text: t("all"), selected: !state[key] }),
      values.map((v) => el("option", { value: v, text: name(v), selected: v === state[key] })));
    sel.addEventListener("change", () => {
      state[key] = FILTER.test(sel.value) ? sel.value : "";
      changed();
    });
    return el("label", {}, label, sel);
  };
  $("filters").replaceChildren(
    el("span", { class: "filters__label", text: t("period") }), seg,
    select("provider", f.providers, t("provider")), select("model", f.models, t("model")), select("country", f.countries, t("country"), countryName),
  );
}

function card(title, { note = "", wide = false, tools = null } = {}, ...body) {
  return el("section", { class: `card${wide ? " card--wide" : ""}`, "aria-label": title },
    el("div", { class: "card__head" }, el("h2", { class: "card__title", text: title }), tools ? el("div", { class: "card__tools" }, tools) : null),
    note ? el("p", { class: "card__note", text: note }) : null,
    ...body);
}

const sub = (title, body) => el("div", { class: "sub" }, el("h3", { class: "sub__title", text: title }), body);
const empty = (key = "none") => el("p", { class: "empty", text: t(key) });
const facts = (text) => el("p", { class: "facts", text });
const unavailable = () => el("p", { class: "unavailable", text: t("unavailable") });

function render() {
  const data = state.data;
  if (!data) return;
  resetCharts();
  statics();
  head(data);
  filters(data);
  const u = data.usage && !data.usage.unavailable ? data.usage : null;
  const days = u?.days || daysOf(data.period);
  const blocks = [];
  if (u) blocks.push(kpis(data, u));
  const notes = notesFor(data);
  if (notes.length) blocks.push(el("div", { class: "notes" }, notes.map((n) => el("p", { class: "notice", text: n }))));
  const grid = el("div", { class: "grid" });
  if (u) {
    grid.append(trafficCard(u, days), turnsCard(u, days), outcomesCard(u), modelsCard(u, days), latencyCard(u), toolsCard(u),
      connectorsCard(u), relayCard(u, days), workflowsCard(u), evidenceCard(u), geographyCard(u), errorsCard(u, days));
  } else {
    grid.append(card(t("traffic"), { wide: true }, unavailable()));
  }
  grid.append(consentCard(u, data.conversations), securityCard(data.security, days), recordsCard(data), exportsCard(data), configCard(data));
  blocks.push(grid);
  $("content").replaceChildren(...blocks);
}

function daysOf({ from, to }) {
  const out = [];
  const end = new Date(`${to}T00:00:00Z`);
  for (let d = new Date(`${from}T00:00:00Z`); d <= end && out.length < 400; d.setUTCDate(d.getUTCDate() + 1)) out.push(d.toISOString().slice(0, 10));
  return out;
}

function notesFor(data) {
  const out = [];
  if (state.provider || state.model || state.country) out.push(t("filters_note"));
  const kept = data.config?.retention?.events;
  if (kept && state.days > kept) out.push(t("rows_partial", { n: kept }));
  if (data.config && !data.config.analytics) out.push(t("analytics_off"));
  return out;
}

// ------------------------------------------------------------------------------------------------- headline numbers

function change(cur, prev, upIsGood = true) {
  if (!(prev > 0) || num(cur) === null) return el("span", { class: "tile__delta", text: t("no_previous") });
  const r = (cur - prev) / prev;
  const arrow = r > 0 ? "▲" : r < 0 ? "▼" : "→";
  const cls = r === 0 ? "" : (r > 0) === upIsGood ? " is-good" : " is-bad";
  return el("span", { class: `tile__delta${cls}`, text: t("vs_previous", { n: state.days, delta: `${arrow} ${fmt.pct(Math.abs(r), 0)}` }) });
}

function pointsChange(cur, prev) {
  if (num(cur) === null || num(prev) === null) return el("span", { class: "tile__delta", text: t("no_previous") });
  const d = (cur - prev) * 100;
  const arrow = d > 0 ? "▲" : d < 0 ? "▼" : "→";
  const cls = d === 0 ? "" : d > 0 ? " is-good" : " is-bad";
  return el("span", { class: `tile__delta${cls}`, text: t("vs_previous", { n: state.days, delta: `${arrow} ${t("pp", { n: Math.abs(d).toFixed(1) })}` }) });
}

function tile({ label, value, title = null, sub: note = "", delta = null, spark = null }) {
  return el("div", { class: "tile" },
    el("span", { class: "tile__label", text: label }),
    el("span", { class: "tile__value", text: value, title }),
    note ? el("span", { class: "tile__sub", text: note }) : null,
    delta,
    spark ? sparkline(spark) : null);
}

function securityDaily(sec, days) {
  const idx = new Map(days.map((d, i) => [d, i]));
  const total = days.map(() => 0);
  const byKind = days.map(() => ({}));
  for (const r of sec?.daily || []) {
    if (!idx.has(r.day)) continue;
    const i = idx.get(r.day);
    total[i] += r.n;
    byKind[i][r.kind] = (byKind[i][r.kind] || 0) + r.n;
  }
  return { total, byKind };
}

function kpis(data, u) {
  const tr = u.traffic;
  const c = u.conversations;
  const m = u.models;
  const tl = u.tools;
  const finishedDaily = u.days.map((_, i) => STATUSES.reduce((a, s) => a + (c.daily[s]?.[i] || 0), 0));
  const before = c.previous?.finished || 0;
  const tokens = (m.totals.tokens_in || 0) + (m.totals.tokens_out || 0);
  const tokensBefore = (m.previous?.tokens_in || 0) + (m.previous?.tokens_out || 0);
  const sec = data.security && !data.security.unavailable ? data.security : null;
  const secDaily = sec ? securityDaily(sec, u.days).total : null;
  return el("div", { class: "kpis" },
    tile({ label: t("pageviews"), value: fmt.compact(tr.totals.pageviews), title: fmt.int(tr.totals.pageviews), delta: change(tr.totals.pageviews, tr.previous?.pageviews), spark: tr.daily.pageviews }),
    tile({ label: t("visits"), value: fmt.compact(tr.totals.visits), title: fmt.int(tr.totals.visits), delta: change(tr.totals.visits, tr.previous?.visits), spark: tr.daily.visits }),
    tile({ label: t("visitor_days"), value: fmt.compact(tr.totals.visitor_days), title: fmt.int(tr.totals.visitor_days), sub: t("visitor_days_note"), delta: change(tr.totals.visitor_days, tr.previous?.visitor_days), spark: tr.daily.visitors }),
    tile({ label: t("turns"), value: fmt.compact(c.totals.finished), title: fmt.int(c.totals.finished), sub: t("conversations_started", { n: fmt.int(c.conversations ?? 0) }), delta: change(c.totals.finished, before), spark: finishedDaily }),
    tile({ label: t("success"), value: fmt.pct(c.success_rate), sub: t("success_note"), delta: pointsChange(c.success_rate, before ? (c.previous.completed || 0) / before : null) }),
    tile({ label: t("calls"), value: fmt.compact(m.totals.calls), title: fmt.int(m.totals.calls), sub: t("calls_note"), delta: change(m.totals.calls, m.previous?.calls), spark: m.daily.calls }),
    tile({ label: t("tokens"), value: fmt.compact(tokens), title: fmt.int(tokens), sub: t("tokens_note", { in: fmt.compact(m.totals.tokens_in), out: fmt.compact(m.totals.tokens_out) }), delta: change(tokens, tokensBefore) }),
    tile({ label: t("latency"), value: fmt.ms(u.latency.total.p50), sub: t("latency_note", { p90: fmt.ms(u.latency.total.p90), p95: fmt.ms(u.latency.total.p95) }) }),
    tile({ label: t("tool_runs"), value: fmt.compact(tl.totals.runs), title: fmt.int(tl.totals.runs), sub: t("tool_failures", { n: fmt.int(tl.totals.failed), rate: fmt.pct(tl.totals.runs ? tl.totals.failed / tl.totals.runs : null) }), delta: change(tl.totals.runs, tl.previous?.runs), spark: tl.daily.runs }),
    tile({ label: t("errors"), value: fmt.compact(u.errors.total ?? 0), title: fmt.int(u.errors.total ?? 0), delta: change(u.errors.total ?? 0, u.errors.previous, false), spark: u.errors.daily }),
    sec ? tile({ label: t("security"), value: fmt.compact(secDaily.reduce((a, b) => a + b, 0)), spark: secDaily }) : null);
}

// ------------------------------------------------------------------------------------------------- sections

function trafficCard(u, days) {
  const tr = u.traffic;
  const routes = Object.entries(tr.routes || {}).sort((a, b) => b[1] - a[1]).map(([route, n]) => ({ route, n }));
  const browsers = (tr.clients?.browsers || []).map((r) => ({ browser: r.a, device: r.b, n: r.n }));
  const layouts = (tr.clients?.layouts || []).map((r) => ({ layout: r.a, lang: r.b, n: r.n }));
  const n = (key) => ({ key, label: t("col_n"), num: true, format: fmt.int });
  return card(t("traffic"), { wide: true, note: t("traffic_note") },
    lineChart({
      days, format: fmt.int,
      label: `${t("traffic")}: ${t("pageviews")} ${fmt.int(tr.totals.pageviews)}, ${t("visits")} ${fmt.int(tr.totals.visits)}, ${t("visitor_days")} ${fmt.int(tr.totals.visitor_days)}`,
      series: [
        { name: t("pageviews"), values: tr.daily.pageviews, slot: 1 },
        { name: t("visits"), values: tr.daily.visits, slot: 2 },
        { name: t("visitors"), values: tr.daily.visitors, slot: 3 },
      ],
    }),
    el("div", { class: "split" },
      sub(t("routes_title"), routes.length ? dataTable([{ key: "route", label: t("col_route"), format: (v) => t(`routes.${v}`) }, { ...n("n"), label: t("col_pageviews") }], routes) : empty()),
      sub(t("clients"), browsers.length ? dataTable([{ key: "browser", label: t("col_browser"), format: (v) => t(`browsers.${v}`) }, { key: "device", label: t("col_device"), format: (v) => t(`devices.${v}`) }, n("n")], browsers) : empty()),
      sub(t("layouts_title"), layouts.length ? dataTable([{ key: "layout", label: t("col_layout"), format: (v) => t(`layouts.${v}`) }, { key: "lang", label: t("col_lang"), format: (v) => t(`langs.${v}`) }, n("n")], layouts) : empty())));
}

function turnsCard(u, days) {
  const c = u.conversations;
  const finishedDaily = days.map((_, i) => STATUSES.reduce((a, s) => a + (c.daily[s]?.[i] || 0), 0));
  const line = state.country
    ? t("turns_line_country", { f: fmt.int(c.totals.finished), c: fmt.int(c.conversations ?? 0) })
    : t("turns_line", { s: fmt.int(c.totals.turns_started), f: fmt.int(c.totals.finished), c: fmt.int(c.conversations ?? 0) });
  return card(t("conversations"), { note: t("conversations_note") },
    facts(line),
    c.totals.finished ? columnChart({
      days, values: finishedDaily, name: t("turns_finished"), format: fmt.int, label: `${t("turns_finished")} ${fmt.int(c.totals.finished)}`,
      breakdown: (i) => STATUSES.map((s) => ({ name: t(s), value: c.daily[s]?.[i] || 0, cls: OUTCOME[s].cls })),
    }) : empty(),
    facts(t("steps_line", { avg: c.avg_steps ?? "—", limit: fmt.int(c.limit_reached ?? 0) })));
}

function outcomesCard(u) {
  const c = u.conversations;
  const reasons = (c.excluded_by || []).map((r) => ({ reason: r.reason, n: r.n }));
  return card(t("outcomes"), {},
    c.totals.finished
      ? stackBar(STATUSES.map((s) => ({ name: t(s), value: c.totals[s] || 0, ...OUTCOME[s] })), { format: fmt.int, pct: (x) => fmt.pct(x) })
      : empty(),
    facts(t("recorded", { n: fmt.int(c.recorded ?? 0), m: fmt.int(c.excluded ?? 0) })),
    reasons.length ? sub(t("excluded_by"), dataTable([{ key: "reason", label: t("col_reason"), format: (v) => t(`reasons.${v}`) }, { key: "n", label: t("col_turns"), num: true, format: fmt.int }], reasons)) : null,
    (c.errors || []).length
      ? sub(t("failures"), dataTable([{ key: "kind", label: t("col_category"), format: (v) => (v ? t(`error_kinds.${v}`) : "—") }, { key: "type", label: t("col_name"), mono: true }, { key: "n", label: t("col_turns"), num: true, format: fmt.int }], c.errors))
      : null);
}

function modelsCard(u, days) {
  const m = u.models;
  return card(t("models"), { note: t("models_note") },
    m.totals.calls ? columnChart({ days, values: m.daily.calls, name: t("calls"), format: fmt.int, label: `${t("calls")} ${fmt.int(m.totals.calls)}` }) : null,
    m.list.length ? dataTable([
      { key: "provider", label: t("col_provider"), mono: true },
      { key: "model", label: t("col_model"), mono: true },
      { key: "turns", label: t("col_turns"), num: true, format: fmt.int },
      { key: "completed", label: t("col_success"), num: true, format: (v, r) => fmt.pct(r.turns ? r.completed / r.turns : null) },
      { key: "calls", label: t("col_calls"), num: true, format: fmt.int },
      { key: "tokens_in", label: t("col_tokens_in"), num: true, format: fmt.int },
      { key: "tokens_out", label: t("col_tokens_out"), num: true, format: fmt.int },
      { key: "avg_ms", label: t("col_avg"), num: true, format: fmt.ms },
    ], m.list) : empty());
}

function latencyCard(u) {
  const L = u.latency;
  const rows = [{ series: t("latency_total"), ...L.total }, { series: t("latency_first"), ...L.first_token }];
  return card(t("latency_title"), { note: t("latency_scope") },
    L.total.n ? dataTable([
      { key: "series", label: t("col_series") },
      { key: "n", label: t("col_turns"), num: true, format: fmt.int },
      ...["avg", "p50", "p90", "p95", "p99", "max"].map((k) => ({ key: k, label: t(`col_${k === "avg" ? "mean" : k}`), num: true, format: fmt.ms })),
    ], rows) : empty());
}

function toolsCard(u) {
  const tl = u.tools;
  const box = el("input", { class: "input", type: "search", placeholder: t("filter_tools"), "aria-label": t("filter_tools") });
  const holder = el("div");
  const cols = [
    { key: "tool", label: t("col_tool"), mono: true },
    { key: "kind", label: t("col_kind"), format: (v) => t(`tool_kinds.${v}`) },
    { key: "runs", label: t("col_runs"), num: true, format: fmt.int },
    { key: "failed", label: t("col_failed"), num: true, format: fmt.int },
    { key: "refused", label: t("col_refused"), num: true, format: fmt.int },
    { key: "failure_rate", label: t("col_rate"), num: true, format: (v) => fmt.pct(v) },
    { key: "p50_ms", label: t("col_p50"), num: true, format: fmt.ms },
    { key: "p90_ms", label: t("col_p90"), num: true, format: fmt.ms },
  ];
  const show = () => {
    const q = box.value.trim().toLowerCase();
    const rows = tl.list.filter((r) => !q || r.tool.toLowerCase().includes(q) || String(r.kind).toLowerCase().includes(q));
    holder.replaceChildren(rows.length ? dataTable(cols, rows) : empty());
  };
  box.addEventListener("input", show);
  show();
  const places = new Map();
  for (const r of tl.by_place || []) {
    const p = places.get(r.place) || { place: r.place, runs: 0, failed: 0 };
    p.runs += r.n;
    if (r.status === "failed") p.failed += r.n;
    places.set(r.place, p);
  }
  const placeRows = [...places.values()].sort((a, b) => b.runs - a.runs);
  return card(t("tools"), { wide: true, note: t("tools_note"), tools: tl.list.length ? box : null },
    holder,
    placeRows.length ? sub(t("by_place"), dataTable([
      { key: "place", label: t("col_where"), format: (v) => t(`places.${v}`) },
      { key: "runs", label: t("col_runs"), num: true, format: fmt.int },
      { key: "failed", label: t("col_failed"), num: true, format: fmt.int },
      { key: "rate", label: t("col_rate"), num: true, format: (v, r) => fmt.pct(r.runs ? r.failed / r.runs : null) },
    ], placeRows)) : null);
}

function availabilityState(x) {
  if (num(x) === null) return el("span", { class: "state state--none", text: "—" });
  const s = x >= 0.99 ? "good" : x >= 0.95 ? "warning" : "critical";
  return el("span", { class: `state state--${s}`, text: `${fmt.pct(x)} · ${t(`avail.${s}`)}` });
}

function connectorsCard(u) {
  const rows = u.connectors || [];
  return card(t("connectors"), { note: t("connectors_note") },
    rows.length ? dataTable([
      { key: "source", label: t("col_database"), mono: true },
      { key: "requests", label: t("col_requests"), num: true, format: fmt.int },
      { key: "ok", label: t("col_ok"), num: true, format: fmt.int },
      { key: "client_errors", label: t("col_4xx"), num: true, format: fmt.int },
      { key: "server_errors", label: t("col_5xx"), num: true, format: fmt.int },
      { key: "refused", label: t("col_refused"), num: true, format: fmt.int },
      { key: "failed", label: t("col_failed"), num: true, format: fmt.int },
      { key: "cancelled", label: t("col_cancelled"), num: true, format: fmt.int },
      { key: "availability", label: t("col_avail"), format: (v) => availabilityState(v) },
      { key: "avg_ms", label: t("col_avg"), num: true, format: fmt.ms },
    ], rows) : empty());
}

function relayCard(u, days) {
  const r = u.relay;
  const total = (r.outcomes || []).reduce((a, o) => a + o.n, 0);
  return card(t("relay"), { note: t("relay_note") },
    total ? columnChart({ days, values: r.daily, name: t("relay_calls"), format: fmt.int, label: `${t("relay_calls")} ${fmt.int(total)}` }) : null,
    (r.outcomes || []).length ? dataTable([{ key: "outcome", label: t("col_outcome"), mono: true }, { key: "n", label: t("col_n"), num: true, format: fmt.int }], r.outcomes) : empty(),
    facts(t("relay_tokens", { in: fmt.int(r.tokens?.in || 0), out: fmt.int(r.tokens?.out || 0) })));
}

function workflowsCard(u) {
  const rows = (u.workflows || []).map((w) => {
    const [kind, ...rest] = String(w.workflow).split(":");
    return { ...w, kind, name: rest.join(":") || "—", ended: w.completed + w.failed + w.refused + w.cancelled };
  });
  return card(t("workflows"), { note: t("workflows_note") },
    rows.length ? dataTable([
      { key: "kind", label: t("col_kind"), format: (v) => t(`wf_kinds.${v}`) },
      { key: "name", label: t("col_workflow"), mono: true },
      ...["started", "submitted", "completed", "failed", "refused", "cancelled"].map((k) => ({ key: k, label: t(`col_${k}`), num: true, format: fmt.int })),
      { key: "ended", label: t("col_done_rate"), num: true, format: (v, r) => fmt.pct(v ? r.completed / v : null) },
    ], rows) : empty());
}

function evidenceCard(u) {
  const e = u.evidence;
  const share = (a, b) => (b ? ` (${fmt.pct(a / b)})` : "");
  const rows = [
    { k: t("ev_citations"), v: fmt.int(e.citations) },
    { k: t("ev_verified"), v: `${fmt.int(e.verified)}${share(e.verified, e.citations)}` },
    { k: t("ev_used"), v: fmt.int(e.used) },
    { k: t("ev_claims"), v: fmt.int(e.claims) },
    { k: t("ev_refused"), v: `${fmt.int(e.claims_refused)}${share(e.claims_refused, e.claims)}` },
  ];
  return card(t("evidence"), { note: t("evidence_note") }, dataTable([{ key: "k", label: t("col_series") }, { key: "v", label: t("col_value"), num: true }], rows));
}

function geographyCard(u) {
  const rows = (u.geography || []).map((g) => ({ ...g, name: countryName(g.country) }));
  const top = rows.filter((g) => g.pageviews > 0).slice(0, 10);
  return card(t("geography"), { note: t("geography_note") },
    top.length ? hbars(top.map((g) => ({ label: g.name, value: g.pageviews })), { format: fmt.int, label: `${t("geography")} · ${t("col_pageviews")}` }) : empty(),
    rows.length ? el("details", { class: "more" }, el("summary", { text: t("geo_all") }), dataTable([
      { key: "name", label: t("col_country") },
      { key: "pageviews", label: t("col_pageviews"), num: true, format: fmt.int },
      { key: "visits", label: t("col_visits"), num: true, format: fmt.int },
      { key: "visitors", label: t("col_visitors"), num: true, format: fmt.int },
      { key: "turns", label: t("col_turns"), num: true, format: fmt.int },
    ], rows)) : null);
}

function errorsCard(u, days) {
  const errors = u.errors.list || [];
  const avail = u.availability || [];
  return card(t("errors_title"), {},
    u.errors.total ? columnChart({ days, values: u.errors.daily, name: t("errors"), format: fmt.int, label: `${t("errors")} ${fmt.int(u.errors.total)}` }) : null,
    sub(t("errors_list"), errors.length ? dataTable([
      { key: "category", label: t("col_category"), format: (v) => t(`error_categories.${v}`) },
      { key: "name", label: t("col_name"), mono: true },
      { key: "n", label: t("col_n"), num: true, format: fmt.int },
    ], errors) : empty("no_errors")),
    sub(t("availability"), avail.length ? dataTable([
      { key: "service", label: t("col_service"), format: (v) => t(`services.${v}`) },
      { key: "checks", label: t("col_checks"), num: true, format: fmt.int },
      { key: "up", label: t("col_up"), num: true, format: fmt.int },
      { key: "down", label: t("col_down"), num: true, format: fmt.int },
      { key: "rate", label: t("col_avail"), format: (v, r) => availabilityState(r.checks ? r.up / r.checks : null) },
    ], avail) : empty()));
}

function consentCard(u, conv) {
  const cs = u?.consent || {};
  const parts = [facts(t("consent_counts", { g: fmt.int(cs.granted || 0), d: fmt.int(cs.declined || 0), w: fmt.int(cs.withdrawn || 0), x: fmt.int(cs.deleted || 0) }))];
  if (!conv || conv.unavailable) parts.push(unavailable());
  else {
    const totals = conv.totals || {};
    const reasons = Object.entries(totals).filter(([k]) => k.startsWith("excluded:")).map(([k, n]) => ({ reason: k.slice(9), n }));
    parts.push(facts(t("records_held", { n: fmt.int(conv.held ?? 0), oldest: conv.oldest || "—" })));
    parts.push(facts(t("records_flow", {
      s: fmt.int(totals.stored || 0), d: fmt.int(totals.duplicate || 0), x: fmt.int(reasons.reduce((a, r) => a + r.n, 0)),
      del: fmt.int(totals.deleted || 0), r: fmt.int(conv.deletion_requests || 0),
    })));
    if (reasons.length) parts.push(sub(t("server_excluded"), dataTable([{ key: "reason", label: t("col_reason"), format: (v) => t(`server_reasons.${v}`) }, { key: "n", label: t("col_n"), num: true, format: fmt.int }], reasons)));
  }
  return card(t("consent"), { note: t("consent_note") }, parts);
}

function securityCard(sec, days) {
  if (!sec || sec.unavailable) return card(t("security_title"), { wide: true }, unavailable());
  const { total, byKind: perDay } = securityDaily(sec, days);
  const byKind = Object.entries(sec.by_kind || {}).sort((a, b) => b[1] - a[1]).map(([kind, n]) => ({ kind, n }));
  const kinds = byKind.map((k) => k.kind);
  const sum = total.reduce((a, b) => a + b, 0);
  return card(t("security_title"), { wide: true, note: t("security_note") },
    sum ? columnChart({
      days, values: total, name: t("security"), format: fmt.int, label: `${t("security")} ${fmt.int(sum)}`,
      breakdown: (i) => kinds.map((k) => ({ name: t(`kinds.${k}`), value: perDay[i][k] || 0, cls: null })),
    }) : empty(),
    el("div", { class: "split" },
      sub(t("by_kind"), byKind.length ? dataTable([{ key: "kind", label: t("col_kind_sec"), format: (v) => t(`kinds.${v}`) }, { key: "n", label: t("col_n"), num: true, format: fmt.int }], byKind) : empty()),
      sec.sources ? sub(t("sources"), sec.sources.length ? dataTable([
        { key: "who", label: t("col_source"), mono: true },
        { key: "net", label: t("col_net"), mono: true },
        { key: "country", label: t("col_country"), format: (v) => countryName(v) },
        { key: "n", label: t("col_n"), num: true, format: fmt.int },
        { key: "kinds", label: t("col_kind_sec"), format: (v) => list((v || []).map((k) => t(`kinds.${k}`))) },
        { key: "last", label: t("col_last"), format: (v) => fmt.time(v) },
        { key: "with_ip", label: t("col_ip"), format: (v) => (v ? t("ip_kept") : "—") },
      ], sec.sources) : empty()) : null),
    sec.audit ? sub(t("audit"), sec.audit.length ? dataTable([
      { key: "ts", label: t("col_time"), format: (v) => fmt.time(v) },
      { key: "kind", label: t("col_kind_sec"), format: (v) => t(`kinds.${v}`) },
      { key: "detail", label: t("col_detail"), mono: true },
      { key: "who", label: t("col_source"), mono: true },
    ], sec.audit) : empty()) : null);
}

// ------------------------------------------------------------------------------------------------- conversation records

function recordItem(item, { onDeleted } = {}) {
  const r = item.record || {};
  const meta = [
    fmt.time(r.finished || item.received), t(r.status), [r.provider, r.model].filter(Boolean).join(" · "),
    num(r.ms) !== null ? fmt.ms(r.ms) : "",
    r.usage ? t("record_tokens", { in: fmt.int(r.usage.input ?? 0), out: fmt.int(r.usage.output ?? 0) }) : "",
  ].filter(Boolean).join(" · ");
  const lines = [
    t("record_steps", { steps: r.steps ?? "—", rounds: r.tool_rounds ?? "—", calls: r.calls ?? "—" }),
    r.citations ? t("record_citations", { total: r.citations.total ?? 0, verified: r.citations.verified ?? 0, used: r.citations.used ?? 0 }) : "",
    r.claims ? t("record_claims", { total: r.claims.total ?? 0, refused: r.claims.refused ?? 0 }) : "",
    r.redacted ? t("record_redacted", { n: r.redacted }) : "",
    r.error ? t("record_error", { kind: r.error.kind || "—", type: r.error.type || "—" }) : "",
    r.app ? t("record_versions", { v: `TCMStudio ${r.app.tcmstudio || "—"} · BioAgent ${r.app.bioagent || "—"} · PSH ${r.app.psh || "—"}` }) : "",
    r.runtime ? t("record_runtime", { v: [r.runtime.compute, r.runtime.browser, r.runtime.runner, r.runtime.layout, r.runtime.lang].filter(Boolean).join(" · ") }) : "",
    r.owner ? t("record_owner", { ref: String(r.owner).slice(0, 12) }) : "",
    `${t("record_id")} ${r.id || "—"}`,
  ].filter(Boolean);
  const tools = r.tools || [];
  const del = el("button", { class: "btn btn--small btn--danger", type: "button", text: t("delete") });
  const node = el("details", { class: "record" },
    el("summary", {}, el("span", { class: "record__q", text: clip(r.question, 140) }), el("span", { class: "record__meta", text: meta })),
    el("div", { class: "record__body" },
      el("p", { class: "record__k", text: t("question") }), el("pre", { class: "record__text", text: r.question || "" }),
      el("p", { class: "record__k", text: t("answer") }), el("pre", { class: "record__text", text: r.answer || "—" }),
      tools.length ? dataTable([
        { key: "name", label: t("col_tool"), mono: true },
        { key: "kind", label: t("col_kind"), format: (v) => t(`tool_kinds.${v}`) },
        { key: "where", label: t("col_where"), format: (v) => t(`places.${v}`) },
        { key: "status", label: t("col_status"), format: (v) => t(`tool_statuses.${v}`) },
        { key: "ms", label: t("col_time"), num: true, format: fmt.ms },
        { key: "error_type", label: t("col_error"), mono: true },
      ], tools) : null,
      el("ul", { class: "record__facts" }, lines.map((x) => el("li", { text: x }))),
      el("div", {}, del)));
  del.addEventListener("click", async () => {
    if (!window.confirm(t("delete_confirm"))) return;
    del.disabled = true;
    try {
      await api("/api/admin/telemetry/conversations/delete", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ids: [r.id] }) });
      records.items = records.items.filter((it) => it.record?.id !== r.id);
      node.replaceWith(el("p", { class: "record__meta", role: "status", text: t("deleted") }));
      onDeleted?.();
    } catch (err) {
      del.disabled = false;
      if (!(err instanceof Expired)) say(t("load_failed", { reason: err.message }), true);
    }
  });
  return node;
}

function recordsCard(data) {
  const key = `${state.days}|${state.provider}`;
  if (records.key !== key) Object.assign(records, { key, items: [], next: null, loaded: false });
  const box = el("div", { class: "records" });
  const status = el("select", { class: "select", "aria-label": t("records_status") },
    ["", ...STATUSES].map((s) => el("option", { value: s, text: s ? t(s) : t("all"), selected: s === records.status })));
  const show = el("button", { class: "btn btn--small", type: "button", text: records.loaded ? t("records_reload") : t("records_show") });
  const more = el("button", { class: "btn btn--small", type: "button", text: t("load_more"), hidden: !records.next });
  // a visitor's deletion request: their record reference (Settings → Privacy) finds every record of theirs, on any day
  const ref = el("input", { class: "input", type: "search", placeholder: t("records_ref"), "aria-label": t("records_ref"), value: records.owner || null,
    spellcheck: "false", autocomplete: "off" });
  const removeAll = el("button", { class: "btn btn--small btn--danger", type: "button", text: t("delete_owner"), hidden: true });
  // the card's controls after a change of the list (a record deleted keeps its "deleted" line in place)
  const controls = () => {
    more.hidden = !records.next;
    show.textContent = records.loaded ? t("records_reload") : t("records_show");
    const owners = new Set(records.items.map((it) => it.record?.owner).filter(Boolean));
    removeAll.hidden = !(records.owner && owners.size === 1);
    if (records.loaded && !records.items.length && !box.querySelector(".empty")) box.append(empty("no_records"));
  };
  const paint = () => {
    box.replaceChildren(...records.items.map((it) => recordItem(it, { onDeleted: controls })));
    controls();
  };
  // a page fetched while the dashboard was drawn again (another language, another period) goes to the card shown now
  records.paint = paint;
  let busy = false;
  const fetchPage = async (reset) => {
    if (busy) return;
    busy = true;
    show.disabled = true;
    more.disabled = true;
    try {
      const q = new URLSearchParams({ days: String(state.days), limit: "25" });
      if (records.status) q.set("status", records.status);
      if (state.provider) q.set("provider", state.provider);
      if (records.owner) q.set("owner", records.owner);
      if (!reset && records.next) q.set("before", String(records.next));
      const res = await api(`/api/admin/telemetry/conversations?${q}`);
      records.items = reset ? res.items || [] : [...records.items, ...(res.items || [])];
      records.next = res.next || null;
      records.loaded = true;
      (box.isConnected ? paint : records.paint || paint)();
    } catch (err) {
      if (!(err instanceof Expired)) say(t("load_failed", { reason: err.message }), true);
    } finally {
      busy = false;
      show.disabled = false;
      more.disabled = false;
    }
  };
  show.addEventListener("click", () => fetchPage(true));
  more.addEventListener("click", () => fetchPage(false));
  status.addEventListener("change", () => {
    records.status = STATUSES.includes(status.value) ? status.value : "";
    if (records.loaded) fetchPage(true);
  });
  ref.addEventListener("change", () => {
    const value = String(ref.value || "").trim().toLowerCase();
    if (value && !REFERENCE.test(value)) {
      say(t("records_ref_bad"), true);
      return;
    }
    say("");
    records.owner = value;
    fetchPage(true);
  });
  removeAll.addEventListener("click", async () => {
    const owner = records.items[0]?.record?.owner;
    if (!owner || !window.confirm(t("delete_owner_confirm"))) return;
    removeAll.disabled = true;
    try {
      const res = await api("/api/admin/telemetry/conversations/delete", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ owner }) });
      records.items = [];
      records.next = null;
      (box.isConnected ? paint : records.paint || paint)();
      say(t("deleted_owner", { n: fmt.int(res?.deleted ?? 0) }));
    } catch (err) {
      if (!(err instanceof Expired)) say(t("load_failed", { reason: err.message }), true);
    } finally {
      removeAll.disabled = false;
    }
  });
  paint();
  const off = data.config && !data.config.conversations ? facts(t("records_off")) : null;
  return card(t("records"), { wide: true, note: t("records_note"), tools: [ref, status, show] }, off, box, el("div", { class: "card__tools" }, more, removeAll));
}

// ------------------------------------------------------------------------------------------------- exports and settings

const MAX_EXPORT_PAGES = 2000; // an export comes in pages of up to a thousand rows (a hundred records)

/** Every page of an export (each one request: the Worker's limits per request are small), put together and saved as
 * one file, the same file the export script writes. */
async function downloadExport(dataset, format, days) {
  const parts = [];
  const rows = [];
  let meta = null;
  let name = `tcmscience-${dataset}.${format}`;
  let cursor = "";
  for (let page = 1; page <= MAX_EXPORT_PAGES; page++) {
    const q = new URLSearchParams({ dataset, format, days: String(days), ...(cursor ? { cursor } : {}) });
    const res = await fetch(`/api/admin/telemetry/export?${q}`, { credentials: "same-origin", cache: "no-store" });
    if (res.status === 401) {
      expired();
      throw new Expired("expired");
    }
    if (!res.ok) {
      let e = null;
      try { e = (await res.json())?.error; } catch { e = null; }
      throw new Error((lang() === "zh" ? e?.message : e?.type) || `HTTP ${res.status}`);
    }
    if (page === 1) name = /filename="([A-Za-z0-9_.-]+)"/.exec(res.headers.get("Content-Disposition") || "")?.[1] || name;
    if (format === "csv") {
      parts.push(await res.text()); // text() drops the first page's BOM: it is put back once, below
      cursor = res.headers.get("X-Next-Cursor") || "";
    } else {
      const body = await res.json();
      meta ||= { dataset: body.dataset, period: body.period, generated: body.generated };
      rows.push(...(body.rows || []));
      cursor = body.next || "";
    }
    if (!cursor) break;
    say(t("exporting", { n: fmt.int(page) }));
  }
  if (cursor) throw new Error(t("export_too_large"));
  const blob = format === "csv" ? new Blob(["\ufeff", ...parts], { type: "text/csv;charset=utf-8" })
    : new Blob([JSON.stringify({ ...meta, rows })], { type: "application/json;charset=utf-8" });
  const href = URL.createObjectURL(blob);
  const a = el("a", { href, download: name, hidden: "" });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(href), 60000);
  say(t("exported", { name }));
}

function exportsCard(data) {
  const admin = data.scope === "admin";
  const groups = DATASETS.filter((d) => admin || !ADMIN_ONLY.has(d)).map((d) => el("span", { class: "exports__group" },
    el("span", { text: t(`datasets.${d}`) }),
    ["csv", "json"].map((f) => {
      // the link is the export's first page (enough for most periods, and what a browser without script gets); a
      // click takes every page and saves them as one file
      const link = el("a", {
        class: "btn btn--small", download: "", text: f.toUpperCase(), "aria-label": `${t(`datasets.${d}`)} · ${f.toUpperCase()}`,
        href: `/api/admin/telemetry/export?${new URLSearchParams({ dataset: d, format: f, days: String(state.days) })}`,
      });
      link.addEventListener("click", async (e) => {
        e.preventDefault();
        if (link.getAttribute("aria-busy") === "true") return;
        link.setAttribute("aria-busy", "true");
        try {
          await downloadExport(d, f, state.days);
        } catch (err) {
          if (!(err instanceof Expired)) say(t("load_failed", { reason: err.message }), true);
        } finally {
          link.removeAttribute("aria-busy");
        }
      });
      return link;
    })));
  return card(t("exports"), { wide: true, note: t("exports_note", { n: state.days }) }, el("div", { class: "exports" }, groups));
}

/** Today's rows written by one telemetry object, and its daily budget (Durable Object rows: docs/TELEMETRY.md). */
function rowsToday(kind, b) {
  if (!b) return t("rows_none", { k: t(`rows_${kind}`) });
  return b.limit ? t("rows_of", { k: t(`rows_${kind}`), n: fmt.int(b.rows ?? 0), max: fmt.int(b.limit) }) : t("rows_free", { k: t(`rows_${kind}`), n: fmt.int(b.rows ?? 0) });
}

function configCard(data) {
  const c = data.config || {};
  const r = c.retention || {};
  const onOff = (v) => (v ? t("on") : t("off"));
  const lines = [
    t("cfg_analytics", { v: onOff(c.analytics) }),
    t("cfg_records", { v: onOff(c.conversations) }),
    t("cfg_consent", { v: c.consent_version ?? "—" }),
    t("cfg_geo", { v: c.geo === "off" ? t("geo_off") : t("geo_country") }),
    t("cfg_ip", { v: c.raw_ip === "flagged" ? t("ip_flagged") : t("ip_off") }),
    t("cfg_retention", { e: r.events ?? "—", a: r.aggregates ?? "—", c: r.conversations ?? "—", s: r.security ?? "—" }),
    t("cfg_rows", { v: ["usage", "conversations", "security"].map((k) => rowsToday(k, data[k]?.budget)).join(" · ") }),
  ];
  let prune = null;
  if (data.scope === "admin") {
    prune = el("button", { class: "btn btn--small", type: "button", text: t("prune") });
    prune.addEventListener("click", async () => {
      if (!window.confirm(t("prune_confirm"))) return;
      prune.disabled = true;
      try {
        const res = await api("/api/admin/telemetry/prune", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
        const n = ["usage", "conversations", "security"].reduce((a, k) => a + (res?.[k]?.removed || 0), 0);
        say(t("pruned", { n: fmt.int(n) }));
      } catch (err) {
        if (!(err instanceof Expired)) say(t("load_failed", { reason: err.message }), true);
      } finally {
        prune.disabled = false;
      }
    });
  }
  return card(t("config"), { wide: true, note: t("config_note"), tools: prune }, el("ul", { class: "facts-list" }, lines.map((x) => el("li", { text: x }))));
}

// ------------------------------------------------------------------------------------------------- start

function start() {
  readUrl();
  setLang(lang());
  statics();
  $("refresh").addEventListener("click", () => load());
  $("lang").addEventListener("click", () => {
    setLang(lang() === "zh" ? "en" : "zh");
    if (state.data) render();
    else statics();
  });
  load();
}

if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
else start();
