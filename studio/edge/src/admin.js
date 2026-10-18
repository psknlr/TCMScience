// The owner's side of the telemetry (studio/docs/TELEMETRY.md, "管理后台"): the analytics dashboard under /admin/ and
// its API under /api/admin/*. Off until the ADMIN_TOKEN secret is set (at least 32 characters).
//
//   POST /api/admin/session                       sign in: {token} (JSON) or a form field → an HttpOnly session cookie
//   POST /api/admin/session/logout                sign out
//   GET  /api/admin/session                       whether the session is valid, and until when
//   GET  /api/admin/telemetry/summary?days=7|30|90[&provider=&model=&country=]    everything the dashboard shows
//   GET  /api/admin/telemetry/export?dataset=…&format=csv|json&days=…            a download (see EXPORTS)
//   GET  /api/admin/telemetry/conversations?days=&status=&provider=&owner=&before=&limit=  consented records, with their
//                                                 text (owner: a visitor's record reference, 12+ hex characters)
//   POST /api/admin/telemetry/conversations/delete  {ids:[…]} or {owner}
//   POST /api/admin/telemetry/prune               apply the retention periods now
//   GET  /admin/                                  the dashboard when signed in, else the sign-in page
//
// Who may: the session cookie (signed with a key derived from ADMIN_TOKEN, so rotating the token signs everyone out), or
// `Authorization: Bearer ADMIN_TOKEN` for scripts — full access; `Authorization: Bearer EXPORT_TOKEN` — the summary and
// the aggregated exports only, never a conversation's text or a security event. A source that fails eight times in
// fifteen minutes is refused for fifteen minutes. A state-changing request signed in by cookie must come from this
// site's own page (Origin), so another site cannot make the admin's browser delete anything. Every sign-in, export,
// view of conversation text and deletion is written to the security log (the audit trail). Nothing here is cached.

import { CONSENT_VERSION } from "../../web/js/core/telemetry-contract.js";
import { clientIp, cookie, fail, json, LOCAL, readText, sameSecret, sameSite } from "./http.js";
import { reportDay, shiftDay, telemetryConfig, telemetryPublic } from "./telemetry/config.js";
import { requestFacts } from "./telemetry/security.js";

export const SESSION_COOKIE = "__Host-tcms_admin";
export const PERIODS = [7, 30, 90];
/** Datasets: the aggregated ones (EXPORT_TOKEN may take them) and those only an admin may (text, network data). */
export const EXPORTS = { daily: "export", turns: "export", tools: "export", connectors: "export", geo: "export", security: "admin", conversations: "admin" };
// each export's columns, in order: every page of a CSV the same, whichever fields its rows happen to have
export const EXPORT_COLUMNS = {
  daily: ["day", "metric", "a", "b", "n"],
  turns: ["id", "day", "ts", "provider", "model", "status", "first", "steps", "tool_rounds", "calls", "tools", "tool_failures", "ms", "first_ms",
    "tokens_in", "tokens_out", "citations", "citations_verified", "citations_used", "claims", "claims_refused", "recorded", "excluded", "error_kind",
    "error_type", "limit_reached", "country"],
  tools: ["id", "day", "ts", "tool", "kind", "place", "status", "ms", "error_type"],
  connectors: ["day", "source", "outcome", "n"],
  geo: ["day", "metric", "country", "n"],
  security: ["seq", "ts", "day", "kind", "route", "status", "who", "net", "ip", "country", "asn", "ua_class", "method", "path", "detail"],
  conversations: ["seq", "received", "v", "id", "conversation", "turn", "owner", "consent", "started", "finished", "ms", "first_ms", "status",
    "limit_reached", "question", "answer", "redacted", "provider", "model", "steps", "tool_rounds", "calls", "tools", "citations", "claims", "evidence",
    "usage", "error", "app", "runtime"],
};
const PUBLIC_ASSETS = new Set(["/admin/login.js", "/admin/admin.css"]);
const SESSION_ASSETS = new Set(["/admin/dashboard.js", "/admin/charts.js", "/admin/i18n.js"]);
const enc = new TextEncoder();

// the dashboard's own policy, enforced: its scripts and styles only, nothing from elsewhere, never framed
export const ADMIN_CSP = [
  "default-src 'none'", "script-src 'self'", "style-src 'self'", "img-src 'self' data:", "font-src 'self'", "connect-src 'self'",
  "form-action 'self'", "frame-ancestors 'none'", "base-uri 'none'",
].join("; ");

const MSG = {
  https_required: "请使用 https 访问",
  bad_cursor: "导出的分页位置无效，请从头重新导出",
  admin_off: "管理后台未启用：请先设置 ADMIN_TOKEN 机密（至少 32 个字符）",
  not_found: "没有这个接口",
  method_not_allowed: "这个接口不接受这种请求方法",
  unauthorized: "需要管理员登录",
  forbidden: "这个令牌没有此项权限",
  locked: "登录失败次数过多，请稍后再试",
  bad_token: "令牌不对",
  cross_site: "只接受本站管理页面发来的请求",
  bad_request: "请求的格式不对",
  bad_dataset: "没有这个数据集",
  unavailable: "统计服务暂时不可用，请稍后再试",
};

/** The headers every admin answer carries (over the site's own): enforced policy, no caching, no indexing, no framing. */
export function adminSecure(response) {
  const res = new Response(response.body, response);
  res.headers.delete("Content-Security-Policy-Report-Only");
  res.headers.set("Content-Security-Policy", ADMIN_CSP);
  res.headers.set("Cache-Control", "no-store");
  res.headers.set("X-Robots-Tag", "noindex, nofollow");
  res.headers.set("X-Frame-Options", "DENY");
  // same-origin keeps the Origin header on the dashboard's own requests (the CSRF check reads it)
  res.headers.set("Referrer-Policy", "same-origin");
  return res;
}

// ------------------------------------------------------------------------------------------------- sessions

const b64url = (bytes) => btoa(String.fromCharCode(...new Uint8Array(bytes))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
const unb64url = (s) => Uint8Array.from(atob(s.replace(/-/g, "+").replace(/_/g, "/") + "===".slice((s.length + 3) % 4)), (c) => c.charCodeAt(0));

async function sessionKey(token) {
  const raw = await crypto.subtle.digest("SHA-256", enc.encode(`tcms-admin-session|${token}`));
  return crypto.subtle.importKey("raw", raw, { name: "HMAC", hash: "SHA-256" }, false, ["sign", "verify"]);
}

/** A new session: "v1.<expiry seconds>.<nonce>.<HMAC>". */
// what a session's signature covers: its body, and the sessions' epoch when it has moved on (signing out moves it:
// every session signed before is then no longer one)
const signed = (body, epoch) => enc.encode(epoch ? `${body}.e${epoch}` : body);

export async function makeSession(cfg, now = Date.now(), epoch = 0) {
  const exp = Math.floor(now / 1000) + cfg.sessionSeconds;
  const body = `v1.${exp}.${b64url(crypto.getRandomValues(new Uint8Array(18)))}`;
  const sig = b64url(await crypto.subtle.sign("HMAC", await sessionKey(cfg.adminToken), signed(body, epoch)));
  return { value: `${body}.${sig}`, exp };
}

/** The session's expiry (seconds) when it is genuine, of the current epoch, unexpired and within the configured
 * lifetime; else 0. */
export async function verifySession(value, cfg, now = Date.now(), epoch = 0) {
  const m = /^v1\.(\d{1,12})\.([A-Za-z0-9_-]{16,64})\.([A-Za-z0-9_-]{20,100})$/.exec(String(value || ""));
  if (!m || !cfg.adminToken) return 0;
  const exp = Number(m[1]);
  if (exp * 1000 <= now || exp * 1000 > now + (cfg.sessionSeconds + 60) * 1000) return 0;
  try {
    return (await crypto.subtle.verify("HMAC", await sessionKey(cfg.adminToken), unb64url(m[3]), signed(`v1.${m[1]}.${m[2]}`, epoch))) ? exp : 0;
  } catch {
    return 0;
  }
}

const setCookie = (value, maxAge) => `${SESSION_COOKIE}=${value}; Path=/; Max-Age=${maxAge}; HttpOnly; Secure; SameSite=Strict`;

/** Who is asking: {scope: "admin"|"export", via} or {scope: null, attempted} (attempted: a token was presented and wrong). */
export async function authenticate(request, cfg, now = Date.now(), epoch = 0) {
  const auth = request.headers.get("Authorization") || "";
  if (/^Bearer\s+/i.test(auth)) {
    const given = auth.replace(/^Bearer\s+/i, "").trim();
    if (await sameSecret(given, cfg.adminToken)) return { scope: "admin", via: "bearer" };
    if (cfg.exportToken && (await sameSecret(given, cfg.exportToken))) return { scope: "export", via: "bearer" };
    return { scope: null, attempted: true };
  }
  const value = cookie(request, SESSION_COOKIE);
  const exp = value ? await verifySession(value, cfg, now, epoch) : 0;
  return exp ? { scope: "admin", via: "session", exp } : { scope: null, attempted: false };
}

// ------------------------------------------------------------------------------------------------- CSV

/** One CSV cell: quoted when needed; a cell a spreadsheet would read as a formula is prefixed with ' (CSV injection). */
export function csvCell(v) {
  if (v === null || v === undefined) return "";
  let s = typeof v === "object" ? JSON.stringify(v) : String(v);
  if (/^[=+\-@\t\r]/.test(s)) s = `'${s}`;
  return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

/** Rows (objects) as CSV: `columns` in order (else their keys in first-seen order). The first page of an export has
 * the header, after a BOM for spreadsheet programs (UTF-8); a later page (`header: false`) only its rows, so that the
 * pages put one after another are the whole file. */
export function toCsv(rows, { columns = null, header = true } = {}) {
  const cols = columns ? [...columns] : [];
  if (!columns) for (const r of rows) for (const k of Object.keys(r)) if (!cols.includes(k)) cols.push(k);
  const lines = header ? [cols.map(csvCell).join(",")] : [];
  for (const r of rows) lines.push(cols.map((c) => csvCell(r[c])).join(","));
  if (!lines.length) return "";
  return `${header ? "﻿" : ""}${lines.join("\r\n")}\r\n`;
}

const encodeCursor = (v) => btoa(JSON.stringify(v)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
function decodeCursor(s) {
  if (!s) return { ok: true, value: null };
  if (!/^[A-Za-z0-9_-]{1,2000}$/.test(s)) return { ok: false };
  try {
    return { ok: true, value: JSON.parse(atob(s.replace(/-/g, "+").replace(/_/g, "/"))) };
  } catch {
    return { ok: false };
  }
}

// ------------------------------------------------------------------------------------------------- the handler

/** The reporting period a request asks for: days ∈ PERIODS (else 7), ending today. */
export function periodOf(url, cfg, now) {
  const days = PERIODS.includes(Number(url.searchParams.get("days"))) ? Number(url.searchParams.get("days")) : 7;
  const to = reportDay(now, cfg.offset);
  return { days, from: shiftDay(to, -(days - 1)), to };
}

const filterValue = (v) => (typeof v === "string" && /^[A-Za-z0-9_.:@/+-]{1,80}$/.test(v) ? v : "");

/**
 * deps: {usage(), conversations(), security() → Durable Object stubs (or null), assets: the ASSETS binding, now()}.
 */
export async function handleAdmin(request, env, deps) {
  const cfg = telemetryConfig(env);
  const url = new URL(request.url);
  const path = url.pathname;
  const isPage = path === "/admin" || path.startsWith("/admin/");
  if (url.protocol === "http:" && !LOCAL.has(url.hostname)) return fail(403, "https_required", MSG.https_required);
  if (!cfg.adminToken) {
    return isPage
      ? new Response(`${MSG.admin_off}\n`, { status: 503, headers: { "Content-Type": "text/plain; charset=utf-8" } })
      : fail(503, "admin_off", MSG.admin_off);
  }
  const now = deps.now();
  const day = reportDay(now, cfg.offset);
  const facts = requestFacts(request);
  const sec = () => (deps.security ? deps.security() : null);
  const audit = async (kind, detail, status = 200) => {
    try { await sec()?.record({ kind, route: "admin", status, detail, facts, day }, { rawIp: cfg.rawIp, now }); } catch (err) { console.error("audit", err?.name || "Error"); }
  };
  try {
    if (path === "/admin") return new Response(null, { status: 308, headers: { Location: "/admin/" } });
    if (isPage) return await page(request, cfg, deps, url, now);
    const signIn = path === "/api/admin/session" && request.method === "POST";
    // the lock-out of this source, and the epoch sessions are signed with (one call)
    const state = await sec()?.authState(clientIp(request), now);
    const epoch = Number(state?.epoch) || 0;
    const who = signIn ? { scope: null } : await authenticate(request, cfg, now, epoch);
    // a source refused for failing too often is refused whatever it presents — except an admin already signed in: a
    // session this Worker signed is not a guess, and others failing from the same address (a shared IPv4, a /64) must
    // not lock the admin out. A token is checked, but while locked even the right one is refused (no answer tells
    // a guess from the token).
    if (who.via !== "session" && state?.locked) {
      if (signIn && isForm(request)) return new Response(null, { status: 303, headers: { Location: "/admin/?e=locked" } });
      return fail(429, "admin_locked", MSG.locked, { "Retry-After": String(state.retry) });
    }
    if (signIn) return await login(request, cfg, deps, url, now, audit, epoch);
    if (path === "/api/admin/session/logout" && request.method === "POST") {
      if (!sameSite(request, url, [url.origin])) return fail(403, "cross_site", MSG.cross_site);
      // signing out ends every session, on the server too: a copy of the cookie is no longer one
      if (who.via === "session") await sec()?.endSessions(now);
      const res = isForm(request) ? new Response(null, { status: 303, headers: { Location: "/admin/" } }) : json({ ok: true });
      res.headers.append("Set-Cookie", setCookie("", 0));
      return res;
    }
    if (!who.scope) {
      if (who.attempted) await failed(request, deps, now, audit);
      return fail(401, "unauthorized", MSG.unauthorized, { "WWW-Authenticate": 'Bearer realm="tcmscience-admin"' });
    }
    // a change made with the session cookie must come from this site's admin page
    if (request.method !== "GET" && request.method !== "HEAD" && who.via === "session" && !sameSite(request, url, [url.origin])) {
      return fail(403, "cross_site", MSG.cross_site);
    }
    const route = `${request.method === "HEAD" ? "GET" : request.method} ${path}`;
    switch (route) {
      case "GET /api/admin/session": return json({ ok: true, scope: who.scope, via: who.via, expires: who.exp ? new Date(who.exp * 1000).toISOString() : null });
      case "GET /api/admin/telemetry/summary": return await summary(env, cfg, deps, url, now, who);
      case "GET /api/admin/telemetry/export": return await exportData(cfg, deps, url, now, who, audit);
      case "GET /api/admin/telemetry/conversations": return await conversations(cfg, deps, url, now, who, audit);
      case "POST /api/admin/telemetry/conversations/delete": return await deleteRecords(request, cfg, deps, now, who, audit);
      case "POST /api/admin/telemetry/prune": return await prune(cfg, deps, now, who, audit);
      default: break;
    }
    const known = ["/api/admin/session", "/api/admin/telemetry/summary", "/api/admin/telemetry/export", "/api/admin/telemetry/conversations",
      "/api/admin/telemetry/conversations/delete", "/api/admin/telemetry/prune", "/api/admin/session/logout"];
    return known.includes(path) ? fail(405, "method_not_allowed", MSG.method_not_allowed) : fail(404, "not_found", MSG.not_found);
  } catch (err) {
    console.error("admin", err?.name || "Error");
    return fail(503, "unavailable", MSG.unavailable, { "Retry-After": "30" });
  }
}

const isForm = (request) => (request.headers.get("Content-Type") || "").toLowerCase().startsWith("application/x-www-form-urlencoded");

async function failed(request, deps, now, audit) {
  const state = await deps.security?.()?.authFailed(clientIp(request), now);
  await audit(state?.locked ? "admin_locked" : "admin_auth_failed", state?.locked ? "locked" : "bad_token", 401);
  return state;
}

async function login(request, cfg, deps, url, now, audit, epoch = 0) {
  if (!sameSite(request, url, [url.origin])) return fail(403, "cross_site", MSG.cross_site);
  const form = isForm(request);
  const raw = await readText(request, 4096);
  let token = "";
  if (raw !== null) {
    if (form) token = new URLSearchParams(raw).get("token") || "";
    else {
      try { token = String(JSON.parse(raw)?.token || ""); } catch { token = ""; }
    }
  }
  token = token.replace(/\s+/g, "");
  if (!(await sameSecret(token, cfg.adminToken))) {
    const state = await failed(request, deps, now, audit);
    if (form) return new Response(null, { status: 303, headers: { Location: `/admin/?e=${state?.locked ? "locked" : "token"}` } });
    return state?.locked
      ? fail(429, "admin_locked", MSG.locked, { "Retry-After": String(state.retry) })
      : fail(401, "bad_token", MSG.bad_token);
  }
  await deps.security?.()?.authSucceeded(clientIp(request), now);
  await audit("admin_login", form ? "form" : "json");
  const session = await makeSession(cfg, now, epoch);
  const res = form ? new Response(null, { status: 303, headers: { Location: "/admin/" } }) : json({ ok: true, expires: new Date(session.exp * 1000).toISOString() });
  res.headers.append("Set-Cookie", setCookie(session.value, cfg.sessionSeconds));
  return res;
}

/** /admin/…: the sign-in page or the dashboard, and their files; the dashboard's code only to a signed-in admin. */
async function page(request, cfg, deps, url, now) {
  if (request.method !== "GET" && request.method !== "HEAD") {
    return new Response("Method Not Allowed\n", { status: 405, headers: { Allow: "GET, HEAD", "Content-Type": "text/plain; charset=utf-8" } });
  }
  const path = url.pathname;
  const epoch = Number(await deps.security?.()?.sessionEpoch?.()) || 0;
  const signedIn = Boolean(await verifySession(cookie(request, SESSION_COOKIE), cfg, now, epoch));
  let asset = null;
  if (PUBLIC_ASSETS.has(path)) asset = path;
  else if (path === "/admin/") asset = signedIn ? "/admin/" : "/admin/login";
  else if (SESSION_ASSETS.has(path)) {
    if (!signedIn) return new Response("Unauthorized\n", { status: 401, headers: { "Content-Type": "text/plain; charset=utf-8" } });
    asset = path;
  }
  if (!asset || !deps.assets) return new Response("Not Found\n", { status: 404, headers: { "Content-Type": "text/plain; charset=utf-8" } });
  const res = await deps.assets.fetch(new Request(new URL(asset, url), { method: request.method }));
  if (!res.ok) return new Response("Not Found\n", { status: 404, headers: { "Content-Type": "text/plain; charset=utf-8" } });
  return res;
}

async function summary(env, cfg, deps, url, now, who) {
  const p = periodOf(url, cfg, now);
  const filters = { provider: filterValue(url.searchParams.get("provider")), model: filterValue(url.searchParams.get("model")), country: filterValue(url.searchParams.get("country")) };
  const settle = async (fn) => {
    try { return await fn(); } catch (err) {
      console.error("summary", err?.name || "Error");
      return { unavailable: true };
    }
  };
  const [usage, conversations, security] = await Promise.all([
    deps.usage ? settle(() => deps.usage().stats({ from: p.from, to: p.to, ...filters, budget: cfg.budgets.usage })) : { unavailable: true },
    deps.conversations ? settle(() => deps.conversations().stats(p.from, p.to, cfg.budgets.conversations)) : { unavailable: true },
    deps.security ? settle(() => deps.security().summary(p.from, p.to, cfg.budgets.security)) : { unavailable: true },
  ]);
  return json({
    ok: true, generated: new Date(now).toISOString(), scope: who.scope, period: p, filters,
    config: { ...telemetryPublic(env), retention: cfg.retention, raw_ip: cfg.rawIp, utc_offset: cfg.offset, consent_version: CONSENT_VERSION },
    usage, conversations,
    // the export token sees how many security events there were, never their sources
    security: who.scope === "admin" || security.unavailable ? security : { daily: security.daily, by_kind: security.by_kind, budget: security.budget },
  });
}

/**
 * One page of an export (a page is one request to one Durable Object, small enough for the Worker's CPU and the RPC's
 * size): `cursor` is the previous page's `next`. JSON: {dataset, period, generated, rows, next}; CSV: the rows, with the
 * header on the first page only, and the next page in X-Next-Cursor and a Link header. The export is audited once, at
 * its first page.
 */
async function exportData(cfg, deps, url, now, who, audit) {
  const dataset = url.searchParams.get("dataset") || "";
  const format = url.searchParams.get("format") === "csv" ? "csv" : "json";
  const need = Object.hasOwn(EXPORTS, dataset) ? EXPORTS[dataset] : null; // never a property of every object
  if (!need) return fail(400, "bad_dataset", MSG.bad_dataset);
  if (need === "admin" && who.scope !== "admin") return fail(403, "forbidden", MSG.forbidden);
  const cursor = decodeCursor(url.searchParams.get("cursor") || "");
  if (!cursor.ok) return fail(400, "bad_cursor", MSG.bad_cursor);
  const p = periodOf(url, cfg, now);
  const page = { after: cursor.value ?? undefined };
  let res;
  if (dataset === "conversations") res = deps.conversations ? await deps.conversations().exportRows(p.from, p.to, page) : { rows: [], next: null };
  else if (dataset === "security") res = deps.security ? await deps.security().exportRows(p.from, p.to, page) : { rows: [], next: null };
  else res = deps.usage ? await deps.usage().exportRows(dataset, p.from, p.to, page) : { rows: [], next: null };
  if (!res) return fail(400, "bad_cursor", MSG.bad_cursor);
  const first = cursor.value === null;
  if (first) await audit("admin_export", `${dataset}.${format}:${p.days}d`);
  const next = res.next === null || res.next === undefined ? null : encodeCursor(res.next);
  const name = `tcmscience-${dataset}-${p.from}_${p.to}.${format}`;
  const headers = { "Content-Disposition": `attachment; filename="${name}"`, "Cache-Control": "no-store" };
  if (next) {
    const q = new URLSearchParams({ dataset, format, days: String(p.days), cursor: next });
    Object.assign(headers, { "X-Next-Cursor": next, Link: `</api/admin/telemetry/export?${q}>; rel="next"` });
  }
  if (format === "csv") {
    return new Response(toCsv(res.rows, { columns: EXPORT_COLUMNS[dataset], header: first }), { headers: { ...headers, "Content-Type": "text/csv; charset=utf-8" } });
  }
  return new Response(JSON.stringify({ dataset, period: p, generated: new Date(now).toISOString(), rows: res.rows, next }), {
    headers: { ...headers, "Content-Type": "application/json; charset=utf-8" },
  });
}

async function conversations(cfg, deps, url, now, who, audit) {
  if (who.scope !== "admin") return fail(403, "forbidden", MSG.forbidden);
  if (!deps.conversations) return json({ ok: true, items: [], next: null });
  const p = periodOf(url, cfg, now);
  const q = url.searchParams;
  const status = ["completed", "cancelled", "failed", "interrupted"].includes(q.get("status")) ? q.get("status") : "";
  const owner = /^[0-9a-f]{12,64}$/.test(q.get("owner") || "") ? q.get("owner") : "";
  const res = await deps.conversations().list({
    from: p.from, to: p.to, status, provider: filterValue(q.get("provider")), owner,
    before: Math.max(0, Math.floor(Number(q.get("before")) || 0)), limit: Math.min(100, Math.max(1, Math.floor(Number(q.get("limit")) || 25))),
  });
  await audit("admin_view", `conversations:${res.items.length}`);
  return json({ ok: true, period: p, ...res });
}

async function deleteRecords(request, cfg, deps, now, who, audit) {
  if (who.scope !== "admin") return fail(403, "forbidden", MSG.forbidden);
  if (!(request.headers.get("Content-Type") || "").toLowerCase().startsWith("application/json")) return fail(415, "bad_request", MSG.bad_request);
  const raw = await readText(request, 65536);
  let body = null;
  try { body = raw === null ? null : JSON.parse(raw); } catch { body = null; }
  const ids = Array.isArray(body?.ids) ? body.ids.filter((x) => typeof x === "string" && /^[A-Za-z0-9_.:-]{1,100}$/.test(x)).slice(0, 500) : [];
  const owner = typeof body?.owner === "string" && /^[0-9a-f]{64}$/.test(body.owner) ? body.owner : "";
  if (!ids.length && !owner) return fail(400, "bad_request", MSG.bad_request);
  if (!deps.conversations) return json({ ok: true, deleted: 0 });
  const at = new Date(now).toISOString();
  const day = reportDay(now, cfg.offset);
  const res = owner ? await deps.conversations().deleteOwner(owner, { by: "admin", at, day }) : await deps.conversations().deleteIds(ids, { by: "admin", at, day });
  await audit("admin_delete", `${owner ? "owner" : "ids"}:${res.deleted}`);
  return json({ ok: true, deleted: res.deleted });
}

async function prune(cfg, deps, now, who, audit) {
  if (who.scope !== "admin") return fail(403, "forbidden", MSG.forbidden);
  const today = reportDay(now, cfg.offset);
  const out = {};
  if (deps.usage) out.usage = await deps.usage().prune(today, { events: cfg.retention.events, aggregates: cfg.retention.aggregates });
  if (deps.conversations) out.conversations = await deps.conversations().prune(today, { days: cfg.retention.conversations });
  if (deps.security) out.security = await deps.security().prune(today, cfg.retention.security);
  await audit("admin_prune", Object.entries(out).map(([k, v]) => `${k}:${v?.removed ?? 0}`).join(","));
  return json({ ok: true, ...out });
}
