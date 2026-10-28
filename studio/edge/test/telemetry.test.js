// The telemetry and admin endpoints through the Worker's entry, with its Durable Object classes on node:sqlite and fake
// bindings: who may send what, what is stored and what is refused, deletion, the admin's sign-in and scopes, the
// dashboard's pages, exports, the relay's and the gateway's own counts, and that nothing sensitive reaches the logs.
// `cloudflare:workers` exists only in the Workers runtime; a module hook stands in for it here.
import assert from "node:assert/strict";
import { register } from "node:module";
import test from "node:test";

import { CONSENT_VERSION, ownerOf } from "../../web/js/core/telemetry-contract.js";
import { csvCell, toCsv, verifySession } from "../src/admin.js";
import { FLUSH_MS } from "../src/telemetry/buffer.js";
import { telemetryConfig } from "../src/telemetry/config.js";
import { KEY, PAGE, quiet, sqlStore, tidy } from "./helpers.js";

const STUB = "data:text/javascript,export class DurableObject { constructor(ctx, env) { this.ctx = ctx; this.env = env; } }";
register(`data:text/javascript,${encodeURIComponent(`
export async function resolve(specifier, context, next) {
  if (specifier === "cloudflare:workers") return { url: ${JSON.stringify(STUB)}, shortCircuit: true };
  return next(specifier, context);
}`)}`);
const { default: worker, ConversationLog, Limiter, SecurityLog, UsageLog, flushTelemetry, nextPrune, resetTelemetryBuffers } = await import("../src/index.js");

const ADMIN = "admin-token-for-tests-0123456789abcdef";
const EXPORT = "export-token-for-tests-0123456789abcdef";
const UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36";

function fakeCtx() {
  let alarm = null;
  return { storage: { sql: sqlStore(), getAlarm: async () => alarm, setAlarm: async (t) => { alarm = t; } }, alarmAt: () => alarm };
}

/** The Worker with every binding faked; `env` overrides variables and secrets. */
function setup({ env = {}, burst = () => ({ success: true }), assets, failing = [] } = {}) {
  resetTelemetryBuffers(); // the Worker's in-memory counts are this isolate's: none of an earlier test's reach this one
  const ctxs = { usage: fakeCtx(), conversations: fakeCtx(), security: fakeCtx(), limiter: fakeCtx() };
  const fullEnv = { MINIMAX_API_KEY: KEY, ADMIN_TOKEN: ADMIN, EXPORT_TOKEN: EXPORT, ...env };
  const objects = {
    usage: new UsageLog(ctxs.usage, fullEnv), conversations: new ConversationLog(ctxs.conversations, fullEnv),
    security: new SecurityLog(ctxs.security, fullEnv), limiter: new Limiter(ctxs.limiter, fullEnv),
  };
  const binding = (name) => ({
    idFromName: (n) => n,
    get: () => {
      if (failing.includes(name)) throw new Error(`Durable Object ${name}: overloaded`);
      return objects[name];
    },
  });
  const assetCalls = [];
  Object.assign(fullEnv, {
    LIMITER: binding("limiter"), USAGE: binding("usage"), CONVERSATIONS: binding("conversations"), SECURITY: binding("security"),
    BURST: { limit: async (a) => burst(a) },
    ASSETS: {
      fetch: async (req) => {
        const p = new URL(req.url).pathname;
        assetCalls.push(p);
        if (assets) return assets(req);
        const type = p.endsWith(".js") ? "text/javascript" : p.endsWith(".css") ? "text/css" : "text/html; charset=utf-8";
        return new Response(`asset ${p}`, { headers: { "Content-Type": type, "Content-Security-Policy-Report-Only": "default-src 'self'" } });
      },
    },
  });
  for (const k of Object.keys(env)) if (env[k] === undefined) delete fullEnv[k];
  const waits = [];
  const ctx = { waitUntil: (p) => waits.push(p) };
  const call = async (path, init = {}) => {
    const res = await worker.fetch(new Request(`https://science.impf.ai${path}`, init), fullEnv, ctx);
    return res;
  };
  // what the Worker holds in memory is sent at its next flush; a test asks for it now
  const drain = async () => {
    while (waits.length) await Promise.all(waits.splice(0));
  };
  const settle = async () => {
    flushTelemetry(fullEnv, ctx, { force: true });
    await drain();
  };
  return { call, settle, drain, objects, env: fullEnv, assetCalls, ctxs };
}

const page = (body, headers = {}) => ({
  method: "POST", body: typeof body === "string" ? body : JSON.stringify(body),
  headers: { Origin: PAGE, "Sec-Fetch-Site": "same-origin", "Content-Type": "text/plain;charset=UTF-8", "CF-Connecting-IP": "203.0.113.9", "User-Agent": UA, ...headers },
});
const bearer = (token = ADMIN, headers = {}) => ({ headers: { Authorization: `Bearer ${token}`, "CF-Connecting-IP": "198.51.100.1", ...headers } });
let seq = 0;
const batch = (events, id = `batch-${++seq}`) => ({ v: 1, batch: id, events });
const summary = async (w, q = "days=7", token = ADMIN) => (await w.call(`/api/admin/telemetry/summary?${q}`, bearer(token))).json();

async function record(id, key, extra = {}) {
  return { v: 1, id, conversation: "c1", turn: 1, owner: await ownerOf(key), consent: CONSENT_VERSION, started: "2026-10-11T04:00:00.000Z",
    finished: "2026-10-11T04:00:09.000Z", ms: 9000, status: "completed", question: "桂枝汤主治什么？", answer: "太阳中风表虚证 [E1]",
    provider: "tao", model: "Tao-S1", steps: 2, tools: [{ name: "tcm_formula", kind: "native", where: "browser", status: "succeeded", ms: 30, arg_keys: ["name"] }],
    citations: { total: 1, verified: 1, used: 1 }, usage: { input: 900, output: 120 }, ...extra };
}
const KEY_A = "a".repeat(43);
const KEY_B = "b".repeat(43);

// ------------------------------------------------------------------------------------------------- health

test("health: what is recorded, the notice's version and retention, in /api/telemetry/health and /v1/health", async () => {
  const w = setup();
  const h = await (await w.call("/api/telemetry/health")).json();
  assert.deepEqual(h, {
    ok: true, service: "tcmscience-telemetry", version: 1, analytics: true, conversations: true, consent_version: CONSENT_VERSION, geo: "country",
    retention_days: { conversations: 90, events: 90, aggregates: 400, security: 30 },
  });
  assert.deepEqual((await (await w.call("/v1/health")).json()).telemetry, (({ ok, service, version, ...rest }) => rest)(h));
  const off = setup({ env: { TELEMETRY: "off", TELEMETRY_CONVERSATIONS: "off" } });
  const hOff = await (await off.call("/api/telemetry/health")).json();
  assert.deepEqual([hOff.analytics, hOff.conversations], [false, false]);
  // a Worker deployed without the bindings takes nothing
  const bare = setup();
  delete bare.env.USAGE;
  delete bare.env.CONVERSATIONS;
  const hBare = await (await bare.call("/v1/health")).json();
  assert.deepEqual([hBare.telemetry.analytics, hBare.telemetry.conversations], [false, false]);
  assert.doesNotMatch(JSON.stringify([h, hBare]), new RegExp(`${ADMIN}|${EXPORT}`));
});

// ------------------------------------------------------------------------------------------------- events

test("events from this site's page are counted: views, visits, visitors, turns, calls and tools apart; a retry once", async () => {
  const w = setup();
  const b1 = batch([
    { type: "app_init", layout: "mobile", lang: "en", browser: "safari", mobile: true },
    { type: "visit", route: "home" }, { type: "pageview", route: "home" }, { type: "pageview", route: "conversation" },
    { type: "turn_start", provider: "tao", model: "Tao-S1", first: true },
    { type: "turn_end", turn: "t1", provider: "tao", model: "Tao-S1", status: "completed", steps: 2, calls: 3, ms: 4200, tokens_in: 1000, tokens_out: 100, first: true },
    { type: "tool_end", call: "t1:c1", tool: "tcm_herb", kind: "native", place: "browser", status: "succeeded", ms: 50 },
  ]);
  assert.equal((await w.call("/api/telemetry/events", page(b1))).status, 204);
  assert.equal((await w.call("/api/telemetry/events", page(b1))).status, 204); // the same batch again (a retry)
  // the same visitor once more, and a second visitor (another address)
  assert.equal((await w.call("/api/telemetry/events", page(batch([{ type: "pageview", route: "about" }])))).status, 204);
  assert.equal((await w.call("/api/telemetry/events", page(batch([{ type: "pageview", route: "home" }]), { "CF-Connecting-IP": "198.51.100.200" }))).status, 204);
  await w.settle();
  const s = await summary(w);
  assert.deepEqual(s.usage.traffic.totals, { pageviews: 4, visits: 1, visitor_days: 2, asker_days: 1, app_inits: 1 });
  assert.deepEqual([s.usage.conversations.totals.turns_started, s.usage.conversations.totals.completed], [1, 1]);
  assert.deepEqual(s.usage.models.totals, { calls: 3, tokens_in: 1000, tokens_out: 100 });
  assert.equal(s.usage.tools.totals.runs, 1);
  assert.deepEqual(s.usage.traffic.clients.browsers, [{ a: "safari", b: "mobile", n: 1 }]);
  assert.deepEqual(s.period.days, 7);
});

test("events: only this site's page, within size, as JSON, by the contract, within the visitor's limits", async () => {
  const w = setup();
  const ok = batch([{ type: "pageview", route: "home" }]);
  const refusals = [
    [{ Origin: "https://evil.example.org" }, 403], [{ Origin: "" }, 403], [{ "Sec-Fetch-Site": "cross-site" }, 403],
    [{ Origin: "http://127.0.0.1:8765" }, 403], // an allowed origin of the relay, but not this site's own page
  ];
  for (const [headers, status] of refusals) {
    const res = await w.call("/api/telemetry/events", page(ok, headers));
    assert.equal(res.status, status, JSON.stringify(headers));
    assert.equal((await res.json()).error.type, "forbidden_origin");
  }
  assert.equal((await w.call("/api/telemetry/events", page("x".repeat(40000)))).status, 413);
  assert.equal((await w.call("/api/telemetry/events", page("{not json"))).status, 400);
  assert.equal((await w.call("/api/telemetry/events", page({ v: 1, batch: "x", events: [] }))).status, 400);
  assert.equal((await w.call("/api/telemetry/events", page({ v: 2, batch: "x", events: [{ type: "pageview", route: "home" }] }))).status, 400);
  assert.equal((await w.call("/api/telemetry/events", { method: "GET" })).status, 405);
  assert.equal((await w.call("/api/telemetry/events", { method: "OPTIONS", headers: { Origin: "https://evil.example.org" } })).status, 403);
  assert.equal((await w.call("/api/telemetry/nope", page(ok))).status, 404);
  // http (not loopback) is refused outright
  assert.equal((await worker.fetch(new Request("http://science.impf.ai/api/telemetry/events", page(ok)), w.env, { waitUntil() {} })).status, 403);
  // the refusals are security events (one Worker's counts are its isolate's: settled before the next setup)
  await w.settle();
  assert.deepEqual((await summary(w)).security.by_kind, { forbidden_origin: 4, oversized: 1, invalid_payload: 3 });
  // per visitor per minute (every request counts, a malformed one too)
  const lim = setup({ env: { TELEMETRY_PER_MINUTE: "2" } });
  assert.equal((await lim.call("/api/telemetry/events", page(batch([{ type: "pageview", route: "home" }])))).status, 204);
  assert.equal((await lim.call("/api/telemetry/events", page(batch([{ type: "pageview", route: "home" }])))).status, 204);
  const limited = await lim.call("/api/telemetry/events", page(batch([{ type: "pageview", route: "home" }])));
  assert.equal(limited.status, 429);
  assert.ok(Number(limited.headers.get("Retry-After")) > 0);
  assert.equal((await lim.call("/api/telemetry/events", page(batch([{ type: "pageview", route: "home" }]), { "CF-Connecting-IP": "198.51.100.99" }))).status, 204);
  await lim.settle();
  assert.deepEqual((await summary(lim)).security.by_kind, { rate_limited: 1 });
  const burst = setup({ burst: () => ({ success: false }) });
  assert.equal((await burst.call("/api/telemetry/events", page(ok))).status, 429);
  await burst.settle();
  assert.deepEqual((await summary(burst)).security.by_kind, { rate_limited: 1 });
  // off: refused, and nothing taken
  const off = setup({ env: { TELEMETRY: "off" } });
  assert.deepEqual((await (await off.call("/api/telemetry/events", page(ok))).json()).error.type, "analytics_off");
});

// ------------------------------------------------------------------------------------------------- conversations

test("conversation records: stored under the current notice only, once, redacted; identifying text never stored", async () => {
  const w = setup();
  const send = async (r) => w.call("/api/telemetry/conversations", page(r));
  let res = await send(await record("m1", KEY_A));
  assert.deepEqual([res.status, await res.json()], [201, { ok: true, stored: true }]);
  res = await send(await record("m1", KEY_A));
  assert.deepEqual([res.status, await res.json()], [200, { ok: true, stored: false, reason: "duplicate" }]);
  res = await send(await record("m2", KEY_A, { consent: CONSENT_VERSION - 1 }));
  assert.deepEqual([res.status, (await res.json()).error.type], [409, "consent_outdated"]);
  res = await send(await record("m3", KEY_A, { question: "患者，女，32岁，身份证号 110101199003077777，月经不调" }));
  assert.deepEqual([res.status, await res.json()], [200, { ok: true, stored: false, reason: "excluded" }]);
  res = await send(await record("m4", KEY_A, { answer: "用 sk-proj-abcdefghijklmnopqrstuvwx 调用，或写信给 a.b@example.org" }));
  assert.equal(res.status, 201);
  res = await send({ ...(await record("m5", KEY_A)), owner: "not-a-digest" });
  assert.equal(res.status, 400);
  assert.equal((await send({ ...(await record("m6", KEY_A)), question: "" })).status, 400);
  const list = await (await w.call("/api/admin/telemetry/conversations?days=7", bearer())).json();
  assert.deepEqual(list.items.map((i) => i.record.id), ["m4", "m1"]);
  const m4 = list.items[0].record;
  assert.equal(m4.answer, "用 [redacted:api_key] 调用，或写信给 [redacted:email]");
  assert.equal(m4.redacted, 2);
  assert.doesNotMatch(JSON.stringify(list), /110101199003077777|32岁/);
  // the contract keeps only the known fields: whatever else a page sends is not stored
  const extra = await send({ ...(await record("m7", KEY_A)), ip: "203.0.113.9", settings: { key: "sk-x" }, tools: [{ name: "x", args: { secret: 1 } }] });
  assert.equal(extra.status, 201);
  const m7 = (await (await w.call("/api/admin/telemetry/conversations?days=7", bearer())).json()).items[0].record;
  assert.equal(m7.id, "m7");
  assert.deepEqual(Object.keys(m7).filter((k) => ["ip", "settings"].includes(k)), []);
  assert.deepEqual(m7.tools, [{ name: "x", kind: "unknown", where: "unknown", status: "unknown" }]);
  const s = await summary(w);
  assert.deepEqual([s.conversations.totals.stored, s.conversations.totals.duplicate, s.conversations.totals["excluded:identifier"]], [3, 1, 1]);
  const off = setup({ env: { TELEMETRY_CONVERSATIONS: "off" } });
  assert.equal((await (await off.call("/api/telemetry/conversations", page(await record("m1", KEY_A)))).json()).error.type, "records_off");
});

test("a visitor's deletion key deletes their records and only theirs, even with collection paused", async () => {
  const w = setup();
  for (const [id, key] of [["a1", KEY_A], ["a2", KEY_A], ["b1", KEY_B]]) assert.equal((await w.call("/api/telemetry/conversations", page(await record(id, key)))).status, 201);
  const paused = setup({ env: { TELEMETRY_CONVERSATIONS: "off" } });
  paused.objects.conversations = w.objects.conversations; // the same store, collection now off
  Object.assign(paused.env, { CONVERSATIONS: w.env.CONVERSATIONS });
  let res = await paused.call("/api/telemetry/conversations/delete", page({ key: KEY_A }));
  assert.deepEqual([res.status, await res.json()], [200, { ok: true, deleted: 2 }]);
  assert.equal((await w.call("/api/telemetry/conversations/delete", page({ key: "short" }))).status, 400);
  assert.equal((await w.call("/api/telemetry/conversations/delete", page({ key: KEY_A }, { Origin: "https://evil.example.org" }))).status, 403);
  res = await w.call("/api/telemetry/conversations/delete", page({ key: KEY_A }));
  assert.deepEqual(await res.json(), { ok: true, deleted: 0 });
  const left = await (await w.call("/api/admin/telemetry/conversations?days=7", bearer())).json();
  assert.deepEqual(left.items.map((i) => i.record.id), ["b1"]);
  const s = await summary(w);
  assert.equal(s.conversations.deletion_requests, 2);
});

// ------------------------------------------------------------------------------------------------- admin

test("admin: off without a strong ADMIN_TOKEN; bearer tokens by scope; failures audited, then locked", async () => {
  for (const token of [undefined, "", "short-token"]) {
    const w = setup({ env: { ADMIN_TOKEN: token } });
    const res = await w.call("/api/admin/telemetry/summary", bearer("short-token"));
    assert.deepEqual([res.status, (await res.json()).error.type], [503, "admin_off"]);
    assert.equal((await w.call("/admin/")).status, 503);
  }
  const w = setup();
  assert.equal((await w.call("/api/admin/telemetry/summary")).status, 401);
  assert.equal((await w.call("/api/admin/telemetry/summary", bearer("wrong-token-wrong-token-wrong-token"))).status, 401);
  // the export token: the summary (without security sources) and aggregated exports, nothing with text or addresses
  const ex = await summary(w, "days=30", EXPORT);
  assert.equal(ex.scope, "export");
  assert.deepEqual(Object.keys(ex.security).sort(), ["budget", "by_kind", "daily"]); // counts only: no source, no audit trail
  assert.deepEqual(Object.keys(ex.security.budget).sort(), ["day", "limit", "rows"]);
  assert.equal((await w.call("/api/admin/telemetry/export?dataset=daily&format=csv", bearer(EXPORT))).status, 200);
  for (const ds of ["conversations", "security"]) assert.equal((await w.call(`/api/admin/telemetry/export?dataset=${ds}`, bearer(EXPORT))).status, 403);
  assert.equal((await w.call("/api/admin/telemetry/conversations", bearer(EXPORT))).status, 403);
  const admin = await summary(w);
  assert.equal(admin.scope, "admin");
  assert.ok(Array.isArray(admin.security.sources));
  // failures from one source: audited, and the eighth locks it out, the right token included
  const lock = setup();
  for (let i = 0; i < 8; i++) await lock.call("/api/admin/telemetry/summary", bearer(`wrong-token-${i}-wrong-token-wrong-token`, { "CF-Connecting-IP": "192.0.2.66" }));
  const locked = await lock.call("/api/admin/telemetry/summary", bearer(ADMIN, { "CF-Connecting-IP": "192.0.2.66" }));
  assert.deepEqual([locked.status, (await locked.json()).error.type], [429, "admin_locked"]);
  assert.equal((await lock.call("/api/admin/telemetry/summary", bearer(ADMIN, { "CF-Connecting-IP": "192.0.2.67" }))).status, 200);
  const audit = await summary(lock);
  assert.equal(audit.security.by_kind.admin_auth_failed, 7);
  assert.equal(audit.security.by_kind.admin_locked, 1);
  assert.doesNotMatch(JSON.stringify(audit), /wrong-token|192\.0\.2\.66/);
});

test("admin lock-out: a signed-in admin is not locked out by others failing from the same address; failures never crowd the audit list", async () => {
  const w = setup();
  const ip = "192.0.2.80"; // a shared address (carrier NAT, a campus): the admin and someone guessing
  const res = await w.call("/api/admin/session", { method: "POST", body: JSON.stringify({ token: ADMIN }), headers: { Origin: PAGE, "Content-Type": "application/json", "CF-Connecting-IP": ip } });
  const cookie = res.headers.get("Set-Cookie").split(";")[0];
  // 72 failures from eight rotating /64s and the shared address
  for (let i = 0; i < 72; i++) {
    const from = i % 9 === 8 ? ip : `2001:db8:${i % 8}::${i}`;
    await w.call("/api/admin/telemetry/summary", bearer(`wrong-token-${i}-wrong-token-wrong-token`, { "CF-Connecting-IP": from }));
  }
  const locked = await w.call("/api/admin/telemetry/summary", bearer(ADMIN, { "CF-Connecting-IP": ip }));
  assert.equal(locked.status, 429, "a token is still refused from a locked address, the right one included");
  // the admin's session goes on, from the same address: viewing and signing out
  const mine = await w.call("/api/admin/telemetry/summary?days=7", { headers: { Cookie: cookie, "CF-Connecting-IP": ip } });
  assert.equal(mine.status, 200);
  const s = await mine.json();
  assert.ok(s.security.audit.some((a) => a.kind === "admin_login"), "the sign-in is still in the audit list");
  assert.ok(s.security.audit.every((a) => !a.kind.startsWith("admin_auth") && a.kind !== "admin_locked"), "failures are not actions");
  assert.ok(s.security.sources.some((x) => x.kinds.includes("admin_auth_failed")), "failures show among the sources");
  assert.equal(s.security.by_kind.admin_auth_failed + (s.security.by_kind.admin_locked || 0), 72, "every failure counted");
  const out = await w.call("/api/admin/session/logout", { method: "POST", headers: { Cookie: cookie, Origin: PAGE, "CF-Connecting-IP": ip } });
  assert.equal(out.status, 200);
  // a form sign-in while locked goes back to the sign-in page with the reason, not to a JSON error
  const form = await w.call("/api/admin/session", { method: "POST", body: `token=${encodeURIComponent(ADMIN)}`, headers: { Origin: PAGE, "Content-Type": "application/x-www-form-urlencoded", "CF-Connecting-IP": ip } });
  assert.deepEqual([form.status, form.headers.get("Location")], [303, "/admin/?e=locked"]);
});

test("admin sign-in: an HttpOnly, Secure, SameSite=Strict session; changes only from this site; sign-out; rotation", async () => {
  const w = setup();
  const signIn = (token, headers = {}) => w.call("/api/admin/session", {
    method: "POST", body: JSON.stringify({ token }), headers: { Origin: PAGE, "Content-Type": "application/json", "CF-Connecting-IP": "198.51.100.9", ...headers },
  });
  assert.equal((await signIn(ADMIN, { Origin: "https://evil.example.org" })).status, 403);
  assert.equal((await signIn("wrong")).status, 401);
  const res = await signIn(ADMIN);
  assert.equal(res.status, 200);
  const set = res.headers.get("Set-Cookie");
  assert.match(set, /^__Host-tcms_admin=v1\.\d+\.[\w-]+\.[\w-]+; Path=\/; Max-Age=3600; HttpOnly; Secure; SameSite=Strict$/);
  assert.ok(!set.includes(ADMIN));
  const cookie = set.split(";")[0];
  const withCookie = (init = {}) => ({ ...init, headers: { Cookie: cookie, "CF-Connecting-IP": "198.51.100.9", ...(init.headers || {}) } });
  assert.equal((await (await w.call("/api/admin/session", withCookie())).json()).via, "session");
  assert.equal((await w.call("/api/admin/telemetry/summary?days=90", withCookie())).status, 200);
  // a state-changing request with the cookie but from another site's page (CSRF) is refused; from this site it goes
  const del = (origin) => w.call("/api/admin/telemetry/conversations/delete", withCookie({ method: "POST", body: JSON.stringify({ ids: ["x"] }), headers: { Origin: origin, "Content-Type": "application/json" } }));
  assert.deepEqual([(await del("https://evil.example.org")).status], [403]);
  assert.deepEqual(await (await del(PAGE)).json(), { ok: true, deleted: 0 });
  // tampered, expired, or signed with a token that has since been rotated: not a session
  const cfg = telemetryConfig(w.env);
  const value = cookie.split("=")[1];
  assert.ok(await verifySession(value, cfg));
  assert.equal(await verifySession(`${value.slice(0, -2)}xx`, cfg), 0);
  assert.equal(await verifySession(value, cfg, Date.now() + 2 * 3600 * 1000), 0);
  assert.equal(await verifySession(value, telemetryConfig({ ...w.env, ADMIN_TOKEN: `${ADMIN}-rotated` })), 0);
  const out = await w.call("/api/admin/session/logout", withCookie({ method: "POST", headers: { Origin: PAGE } }));
  assert.match(out.headers.get("Set-Cookie"), /^__Host-tcms_admin=; Path=\/; Max-Age=0; HttpOnly; Secure; SameSite=Strict$/);
  // a form sign-in (no script) redirects back to the dashboard, or to the sign-in page with the reason
  const form = (token) => w.call("/api/admin/session", { method: "POST", body: `token=${encodeURIComponent(token)}`, headers: { Origin: PAGE, "Content-Type": "application/x-www-form-urlencoded", "CF-Connecting-IP": "198.51.100.10" } });
  const good = await form(ADMIN);
  assert.deepEqual([good.status, good.headers.get("Location")], [303, "/admin/"]);
  const bad = await form("nope");
  assert.deepEqual([bad.status, bad.headers.get("Location")], [303, "/admin/?e=token"]);
  const s = await summary(w);
  assert.equal(s.security.audit.filter((a) => a.kind === "admin_login").length, 2);
});

test("signing out ends every session on the server: a copy of the cookie, or another browser's, is no longer one", async () => {
  const w = setup();
  const signIn = async (ip) => {
    const res = await w.call("/api/admin/session", { method: "POST", body: JSON.stringify({ token: ADMIN }), headers: { Origin: PAGE, "Content-Type": "application/json", "CF-Connecting-IP": ip } });
    return res.headers.get("Set-Cookie").split(";")[0];
  };
  const here = await signIn("198.51.100.20");
  const elsewhere = await signIn("198.51.100.21"); // the same admin in another browser
  const asks = (cookie) => w.call("/api/admin/telemetry/summary", { headers: { Cookie: cookie, "CF-Connecting-IP": "198.51.100.20" } });
  assert.equal((await asks(here)).status, 200);
  assert.equal((await asks(elsewhere)).status, 200);
  const out = await w.call("/api/admin/session/logout", { method: "POST", headers: { Cookie: here, Origin: PAGE, "CF-Connecting-IP": "198.51.100.20" } });
  assert.equal(out.status, 200);
  assert.equal((await asks(here)).status, 401, "a copy of the cookie kept after signing out");
  assert.equal((await asks(elsewhere)).status, 401, "every session ends");
  const page = await w.call("/admin/", { headers: { Cookie: here, Accept: "text/html" } });
  assert.doesNotMatch(await page.text(), /dashboard\.js/); // the sign-in page, not the dashboard
  const again = await signIn("198.51.100.20");
  assert.equal((await asks(again)).status, 200, "a new sign-in works");
  // a sign-out without a session ends nothing (anyone can post to it from this site's page)
  assert.equal((await w.call("/api/admin/session/logout", { method: "POST", headers: { Origin: PAGE, "CF-Connecting-IP": "203.0.113.5" } })).status, 200);
  assert.equal((await asks(again)).status, 200);
});

test("the dashboard: sign-in page or dashboard, its code only with a session, strict headers, never cached", async () => {
  const w = setup();
  const r308 = await w.call("/admin");
  assert.deepEqual([r308.status, r308.headers.get("Location")], [308, "/admin/"]);
  const login = await w.call("/admin/");
  assert.equal(await login.text(), "asset /admin/login");
  for (const [k, v] of [["Content-Security-Policy", /default-src 'none'; script-src 'self'/], ["X-Frame-Options", /^DENY$/], ["Cache-Control", /^no-store$/],
    ["X-Robots-Tag", /noindex/], ["Referrer-Policy", /^same-origin$/], ["X-Content-Type-Options", /^nosniff$/]]) assert.match(login.headers.get(k) || "", v, k);
  assert.equal(login.headers.get("Content-Security-Policy-Report-Only"), null);
  assert.equal((await w.call("/admin/dashboard.js")).status, 401);
  assert.equal((await w.call("/admin/admin.css")).status, 200);
  assert.equal((await w.call("/admin/login.js")).status, 200);
  assert.equal((await w.call("/admin/secret.json")).status, 404);
  assert.equal((await w.call("/admin/", { method: "POST" })).status, 405);
  const res = await w.call("/api/admin/session", { method: "POST", body: JSON.stringify({ token: ADMIN }), headers: { Origin: PAGE, "Content-Type": "application/json" } });
  const cookie = res.headers.get("Set-Cookie").split(";")[0];
  assert.equal(await (await w.call("/admin/", { headers: { Cookie: cookie } })).text(), "asset /admin/");
  assert.equal((await w.call("/admin/dashboard.js", { headers: { Cookie: cookie } })).status, 200);
  assert.deepEqual(w.assetCalls, ["/admin/login", "/admin/admin.css", "/admin/login.js", "/admin/", "/admin/dashboard.js"]);
});

test("exports: aggregated or full by scope, CSV safe for spreadsheets, JSON with the period, every export audited", async () => {
  const w = setup();
  await w.call("/api/telemetry/events", page(batch([{ type: "pageview", route: "home" }, { type: "turn_end", turn: "t1", provider: "tao", status: "completed", ms: 1000 }])));
  await w.call("/api/telemetry/conversations", page(await record("m1", KEY_A, { question: "=HYPERLINK(\"http://x\")，桂枝汤？" })));
  await w.settle();
  const csv = await w.call("/api/admin/telemetry/export?dataset=conversations&format=csv&days=30", bearer());
  assert.equal(csv.status, 200);
  assert.match(csv.headers.get("Content-Disposition"), /^attachment; filename="tcmscience-conversations-\d{4}-\d{2}-\d{2}_\d{4}-\d{2}-\d{2}\.csv"$/);
  assert.match(csv.headers.get("Content-Type"), /^text\/csv/);
  const bytes = new Uint8Array(await csv.arrayBuffer());
  assert.deepEqual([...bytes.slice(0, 3)], [0xef, 0xbb, 0xbf]); // a BOM, so spreadsheet programs read UTF-8
  const text = new TextDecoder().decode(bytes);
  assert.match(text, /"'=HYPERLINK\(""http:\/\/x""\)，桂枝汤？"/); // a formula is never a formula in a spreadsheet
  const js = await (await w.call("/api/admin/telemetry/export?dataset=turns&format=json&days=90", bearer())).json();
  assert.deepEqual([js.dataset, js.period.days, js.rows.length, js.rows[0].status], ["turns", 90, 1, "completed"]);
  assert.equal((await w.call("/api/admin/telemetry/export?dataset=passwords", bearer())).status, 400);
  // a name every object has is not a dataset (400, not a failed call to the store)
  for (const ds of ["constructor", "__proto__", "toString", "hasOwnProperty"]) {
    const res = await w.call(`/api/admin/telemetry/export?dataset=${ds}`, bearer());
    assert.deepEqual([res.status, (await res.json()).error.type], [400, "bad_dataset"], ds);
  }
  assert.equal((await w.call("/api/admin/telemetry/export?dataset=daily&cursor=%%%", bearer())).status, 400);
  // a period other than 7, 30 or 90 days is read as 7
  assert.equal((await (await w.call("/api/admin/telemetry/export?dataset=daily&days=3650", bearer())).json()).period.days, 7);
  const s = await summary(w);
  assert.deepEqual(s.security.audit.filter((a) => a.kind === "admin_export").map((a) => a.detail).sort(), ["conversations.csv:30d", "daily.json:7d", "turns.json:90d"]); // a refused cursor is no export
  assert.equal(csvCell("+1"), "'+1");
  assert.equal(csvCell("-sum"), "'-sum");
  assert.equal(csvCell("@cmd"), "'@cmd");
  // no formula: the connector counts' "-" (no registered source) and a number, negative or not, are written as they are
  assert.equal(csvCell("-"), "-");
  assert.equal(csvCell(-3), "-3");
  assert.equal(csvCell(0), "0");
  assert.equal(csvCell("a,b"), '"a,b"');
  assert.equal(csvCell({ x: 1 }), '"{""x"":1}"');
  assert.equal(toCsv([{ a: 1 }, { b: "x\ny" }]), '﻿a,b\r\n1,\r\n,"x\ny"\r\n');
});

test("exports come in pages: every row once, the CSV's header once, the export audited once", async () => {
  const w = setup();
  // 2,345 counters of one day (more than two pages of a thousand)
  const day = (await summary(w)).period.to;
  const list = Array.from({ length: 2345 }, (_, i) => ({ metric: "connector", a: `host-${String(i).padStart(4, "0")}.example.org`, b: "2xx", n: i + 1 }));
  w.objects.usage.observe(list.slice(0, 1500), { day }); // a flush sends 2,000 counters at most
  w.objects.usage.observe(list.slice(1500), { day });
  const pages = async (format) => {
    const out = [];
    let cursor = "";
    for (let i = 0; i < 10; i++) {
      const res = await w.call(`/api/admin/telemetry/export?dataset=connectors&format=${format}&days=7${cursor ? `&cursor=${cursor}` : ""}`, bearer());
      assert.equal(res.status, 200);
      const next = format === "csv" ? res.headers.get("X-Next-Cursor") : null;
      if (format === "csv") {
        out.push(new TextDecoder("utf-8", { ignoreBOM: true }).decode(await res.arrayBuffer())); // text() would drop the BOM
        if (next) assert.match(res.headers.get("Link"), /^<\/api\/admin\/telemetry\/export\?dataset=connectors&format=csv&days=7&cursor=[\w-]+>; rel="next"$/);
        cursor = next || "";
      } else {
        const body = await res.json();
        out.push(body);
        cursor = body.next || "";
      }
      if (!cursor) break;
    }
    return out;
  };
  const csv = await pages("csv");
  assert.equal(csv.length, 3);
  const text = csv.join("");
  assert.equal(text.split("\ufeff").length, 2, "one BOM, at the start");
  const lines = text.replace(/^\ufeff/, "").trim().split("\r\n");
  assert.equal(lines[0], "day,source,outcome,n");
  assert.equal(lines.length, 2346);
  assert.equal(new Set(lines.slice(1)).size, 2345, "no row twice");
  const json = await pages("json");
  assert.deepEqual(json.map((p) => p.rows.length), [1000, 1000, 345]);
  assert.equal(json[2].next, null);
  assert.equal(json.flatMap((p) => p.rows).reduce((a, r) => a + r.n, 0), (2345 * 2346) / 2);
  const audit = (await summary(w)).security.audit.filter((a) => a.kind === "admin_export").map((a) => a.detail);
  assert.deepEqual(audit, ["connectors.json:7d", "connectors.csv:7d"]); // newest first: once each, not once a page
  // a cursor for another dataset's rows is refused, not read as something else
  const other = await w.call(`/api/admin/telemetry/export?dataset=daily&format=json&days=7&cursor=${json[0].next}`, bearer());
  assert.equal(other.status, 400);
});

test("admin: retention on demand, and each object's daily alarm", async () => {
  const w = setup();
  w.objects.conversations.core.accept({ v: 1, id: "old", owner: await ownerOf(KEY_A), consent: 1, status: "completed", question: "q" }, { day: "2025-01-01", received: "x" });
  const res = await w.call("/api/admin/telemetry/prune", { method: "POST", headers: { Authorization: `Bearer ${ADMIN}`, "Content-Type": "application/json" } });
  const out = await res.json();
  assert.equal(out.conversations.removed, 1);
  assert.equal((await w.call("/api/admin/telemetry/prune", bearer(EXPORT, { "Content-Type": "application/json" }))).status, 405);
  // the first write to an object sets its daily alarm; the alarm prunes and sets the next one
  await w.call("/api/telemetry/events", page(batch([{ type: "pageview", route: "home" }])));
  const at = w.ctxs.usage.alarmAt();
  assert.ok(at > Date.now() && at - Date.now() <= 86400000 + 60000);
  assert.equal(new Date(at).getUTCHours(), 3);
  await w.objects.usage.alarm();
  assert.equal(w.ctxs.usage.alarmAt(), nextPrune());
});

// ------------------------------------------------------------------------------------------------- the Worker's own counts

test("the relay's calls and tokens, a gateway's request by host and outcome, and scanner probes are counted", async () => {
  const w = setup();
  const saved = globalThis.fetch;
  globalThis.fetch = async () => new Response([
    `data: ${JSON.stringify({ id: "a", choices: [{ index: 0, delta: { content: "好" } }], model: "MiniMax-M3" })}\n\n`,
    `data: ${JSON.stringify({ id: "a", choices: [{ index: 0, delta: {}, finish_reason: "stop" }], usage: { prompt_tokens: 20, completion_tokens: 3 } })}\n\n`,
    "data: [DONE]\n\n"].join(""), { headers: { "Content-Type": "text/event-stream" } });
  try {
    const chat = (origin) => w.call("/v1/chat/completions", { method: "POST", body: JSON.stringify(tidy), headers: { Origin: origin, "Content-Type": "application/json", "CF-Connecting-IP": "203.0.113.4" } });
    const ok = await chat(PAGE);
    assert.equal(ok.status, 200);
    await ok.text();
    assert.equal((await chat("https://evil.example.org")).status, 403);
    assert.equal((await w.call("/wp-login.php", { headers: { "CF-Connecting-IP": "203.0.113.4" } })).status, 404);
    assert.equal((await w.call("/.env", { headers: { "CF-Connecting-IP": "203.0.113.4" } })).status, 404);
    await w.settle();
  } finally {
    globalThis.fetch = saved;
  }
  const s = await summary(w);
  assert.deepEqual(s.usage.relay.outcomes, [{ outcome: "forbidden_origin", n: 1 }, { outcome: "ok", n: 1 }].sort((a, b) => b.n - a.n || 0));
  assert.deepEqual(s.usage.relay.tokens, { in: 20, out: 3 });
  assert.equal(s.security.by_kind.scanner, 2);
  assert.equal(s.security.by_kind.forbidden_origin, 1);
  assert.deepEqual(s.security.sources.map((x) => x.net), ["203.0.113.0/24"]);
});

test("a flood costs each telemetry object a call per flush, not one per request (the shared Durable Object budget)", async () => {
  const w = setup();
  const calls = { security: 0, usage: 0 };
  for (const [name, method] of [["security", "recordBatch"], ["usage", "observe"]]) {
    const obj = w.objects[name];
    const original = obj[method].bind(obj);
    obj[method] = (...a) => {
      calls[name]++;
      return original(...a);
    };
  }
  for (let i = 0; i < 300; i++) assert.equal((await w.call(`/wp-content/${i}.php`, { headers: { "CF-Connecting-IP": "203.0.113.4" } })).status, 404);
  for (let i = 0; i < 100; i++) {
    const res = await w.call("/v1/chat/completions", { method: "POST", body: JSON.stringify(tidy), headers: { Origin: "https://evil.example.org", "Content-Type": "application/json", "CF-Connecting-IP": "203.0.113.5" } });
    assert.equal(res.status, 403);
  }
  // the first of each went at once (nothing had been sent before), everything after it with the one flush asked for here
  await w.settle();
  assert.deepEqual(calls, { security: 2, usage: 2 });
  const s = await summary(w);
  assert.equal(s.security.by_kind.scanner, 300);
  assert.equal(s.security.by_kind.forbidden_origin, 100);
  assert.deepEqual(s.usage.relay.outcomes, [{ outcome: "forbidden_origin", n: 100 }]);
  assert.deepEqual(s.security.sources.map((x) => x.net).sort(), ["203.0.113.0/24"]);
});

test("what is still held after a flush goes when the window ends, though no request comes by to send it", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout", "Date"], now: Date.parse("2026-10-14T04:00:00Z") });
  const w = setup();
  const sent = [];
  const obj = w.objects.security;
  const original = obj.recordBatch.bind(obj);
  obj.recordBatch = (batch, opts) => {
    sent.push(batch.counts.reduce((n, c) => n + c.n, 0));
    return original(batch, opts);
  };
  const probe = async (i) => assert.equal((await w.call(`/wp-${i}.php`, { headers: { "CF-Connecting-IP": "203.0.113.4" } })).status, 404);
  await probe(0); // the first after a quiet spell: at once
  t.mock.timers.tick(5000);
  await probe(1);
  await probe(2); // held: the window is not over
  assert.deepEqual(sent, [1]);
  t.mock.timers.tick(FLUSH_MS - 5000 - 1);
  assert.deepEqual(sent, [1]);
  t.mock.timers.tick(1); // the window ends: the trailing flush sends them, within the request's waitUntil
  await w.drain();
  assert.deepEqual(sent, [1, 2]);
  t.mock.timers.tick(10 * FLUSH_MS); // nothing held: no flush, no timer
  assert.deepEqual(sent, [1, 2]);
  const s = await summary(w);
  assert.equal(s.security.by_kind.scanner, 3);
  // stored at the time each happened, one a source every ten seconds (probe 1 came 5 s after probe 0 and is counted only)
  const stored = w.objects.security.exportRows("2026-10-01", "2026-10-31").rows.map((e) => [e.path, e.ts]);
  assert.deepEqual(stored, [["/wp-0.php", "2026-10-14T04:00:00.000Z"]]);
});

test("a day's rows spent: events and records refused until 00:00 UTC without asking the objects again; deletion still works", async () => {
  const w = setup({ env: { TELEMETRY_DAILY_ROWS: "200" } }); // usage 120 rows, records 50, security 30
  const calls = { ingest: 0, accept: 0 };
  for (const [name, method] of [["usage", "ingest"], ["conversations", "accept"]]) {
    const obj = w.objects[name];
    const original = obj[method].bind(obj);
    obj[method] = (...a) => { calls[method]++; return original(...a); };
  }
  const tools = (i) => Array.from({ length: 10 }, (_, j) => ({ type: "tool_end", call: `t${i}-${j}`, tool: "tcm_herb", kind: "native", place: "browser", status: "succeeded", ms: 5 }));
  const statuses = [];
  for (let i = 0; i < 6; i++) {
    const res = await w.call("/api/telemetry/events", page(batch(tools(i)), { "CF-Connecting-IP": `198.51.100.${i}` }));
    statuses.push(res.status);
    if (res.status === 503) {
      const body = await res.json();
      assert.equal(body.error.type, "budget_spent");
      assert.ok(Number(res.headers.get("Retry-After")) > 0);
    }
  }
  const first = statuses.indexOf(503);
  assert.ok(first > 0, statuses.join(","));
  assert.ok(statuses.slice(first).every((x) => x === 503), statuses.join(","));
  assert.equal(calls.ingest, first + 1, "after the object said its day is spent, it is not asked again");
  // records the same way; a deletion request is never refused for it
  let refused = 0;
  for (let i = 0; i < 12; i++) {
    const res = await w.call("/api/telemetry/conversations", page(await record(`b${i}`, KEY_A), { "CF-Connecting-IP": `198.51.100.${20 + i}` }));
    if (res.status === 503) refused++;
  }
  assert.ok(refused > 0 && calls.accept < 12, `${refused} refused, ${calls.accept} asked`);
  const del = await w.call("/api/telemetry/conversations/delete", page({ key: KEY_A }));
  assert.equal(del.status, 200);
  assert.ok((await del.json()).deleted > 0);
  // the dashboard shows each object's rows today against its budget
  const s = await summary(w);
  assert.equal(s.usage.budget.limit, 120);
  assert.ok(s.usage.budget.rows >= 120);
  assert.equal(s.conversations.budget.limit, 50);
  assert.equal(s.security.budget.limit, 30);
});

test("the telemetry never stands in a conversation's way: its objects failing, the relay still answers", async () => {
  const w = setup({ failing: ["usage", "security", "conversations"] });
  const saved = globalThis.fetch;
  globalThis.fetch = async () => new Response(`data: ${JSON.stringify({ choices: [{ index: 0, delta: { content: "好" } }], usage: { prompt_tokens: 1, completion_tokens: 1 } })}\n\ndata: [DONE]\n\n`, { headers: { "Content-Type": "text/event-stream" } });
  const lines = [];
  const errors = console.error;
  console.error = (...a) => lines.push(a.join(" "));
  try {
    const res = await w.call("/v1/chat/completions", { method: "POST", body: JSON.stringify(tidy), headers: { Origin: PAGE, "Content-Type": "application/json", "CF-Connecting-IP": "203.0.113.4" } });
    assert.equal(res.status, 200);
    assert.match(await res.text(), /好/);
    const ev = await w.call("/api/telemetry/events", page(batch([{ type: "pageview", route: "home" }])));
    assert.deepEqual([ev.status, (await ev.json()).error.type], [503, "unavailable"]);
    const rec = await w.call("/api/telemetry/conversations", page(await record("m1", KEY_A, { question: "我的 token: sk-proj-zzzzzzzzzzzzzzzzzzzzzz 桂枝汤？" })));
    assert.equal(rec.status, 503);
    await w.settle();
  } finally {
    globalThis.fetch = saved;
    console.error = errors;
  }
  // the Worker's own log lines name what failed, never what was sent, an address or a token
  assert.ok(lines.length > 0);
  assert.doesNotMatch(lines.join("\n"), /桂枝汤|sk-proj|203\.0\.113|Durable Object usage|admin-token/);
});

test("many pages at once: every batch from every visitor is counted, none twice", async () => {
  const w = setup();
  await Promise.all(Array.from({ length: 30 }, (_, i) => w.call("/api/telemetry/events", page(batch([{ type: "pageview", route: "home" }, { type: "turn_end", turn: `t${i}`, provider: "tao", status: "completed", ms: 100 + i }]), {
    "CF-Connecting-IP": `198.51.100.${i % 10}`, "User-Agent": `${UA} ${i % 10}` }))));
  await w.settle();
  const s = await summary(w);
  assert.deepEqual([s.usage.traffic.totals.pageviews, s.usage.traffic.totals.visitor_days, s.usage.conversations.totals.completed], [30, 10, 30]);
  assert.equal(s.usage.latency.total.n, 30);
  assert.equal(s.usage.latency.total.p50, 114);
});

test("quiet() still silences the relay's own lines in other suites", async () => {
  await quiet(async () => { console.error("x"); });
});
