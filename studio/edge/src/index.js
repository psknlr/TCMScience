// TCMScience Studio — the one Worker for https://science.impf.ai.
//   The app's files: Workers Static Assets (binding ASSETS, directory ../_site). Cloudflare serves them without
//     running this Worker (free and not counted), with the headers in _headers. A path that matches no file comes
//     here: a browser navigating to one of the app's routes gets index.html, anything else 404 (a scanner's probe of a
//     path no one else asks for is noted as a security event).
//   /v1/*: the Tao-S1 relay (relay.js). /api/sources/*: registered read-only database transport (sources.js).
//   /api/datasets/files/*: the registered raw dataset files, streamed from their reviewed hosts (datasets.js).
//   /api/telemetry/*: usage statistics and consented conversation records from the page (telemetry/routes.js).
//   /api/admin/* and /admin/*: the owner's analytics dashboard and its API (admin.js), off until ADMIN_TOKEN is set.
//   The Worker runs first on all of these (run_worker_first in wrangler.toml).
//   Durable Objects, each one SQLite-backed object for the whole service, each with its own storage:
//     Limiter          the counters behind the relay's and the gateway's limits
//     UsageLog         usage statistics (telemetry/usage.js): counts and per-turn / per-tool rows, no text
//     ConversationLog  conversation records of visitors who agreed (telemetry/conversations.js)
//     SecurityLog      security events and the admin audit trail (telemetry/security.js)
//   Each telemetry object deletes what is past its retention period once a day (its own alarm), and on demand.
//   What the Worker counts itself (relay calls, gateway requests, refused requests) is summed in memory and sent to
//   those objects at most every 20 seconds (telemetry/buffer.js), never once per request: on the Free plan they share
//   one daily budget with the Limiter, which refuses service once it is spent.
import { DurableObject } from "cloudflare:workers";
import { adminSecure, handleAdmin } from "./admin.js";
import { handleDatasets } from "./datasets.js";
import { LimiterCore } from "./limiter.js";
import { handle } from "./relay.js";
import { redirectHttps, secure } from "./site.js";
import { handleSources } from "./sources.js";
import { markSpent, resetSpent, spentFor } from "./telemetry/budget.js";
import { FLUSH_MS, MetricBuffer, SecurityBuffer } from "./telemetry/buffer.js";
import { reportDay, telemetryConfig } from "./telemetry/config.js";
import { ConversationCore } from "./telemetry/conversations.js";
import { handleTelemetry } from "./telemetry/routes.js";
import { isHoneypot, requestFacts, SecurityCore } from "./telemetry/security.js";
import { UsageCore } from "./telemetry/usage.js";

export class Limiter extends DurableObject {
  constructor(ctx, env) {
    super(ctx, env);
    this.core = new LimiterCore(ctx.storage.sql);
  }

  hit(who, kind, limits, reserve) {
    return this.core.hit(who, kind, limits, Date.now(), reserve);
  }

  spend(who, kind, tokens) {
    return this.core.spend(who, kind, tokens);
  }

  sourceSlot(host, rps, prefixes) {
    return this.core.sourceSlot(host, rps, Date.now(), prefixes);
  }
}

/** When the next daily retention pass runs: 03:17 UTC (quiet in Beijing and in Europe). */
export function nextPrune(now = Date.now()) {
  const d = new Date(now);
  const at = Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate(), 3, 17);
  return at > now + 60000 ? at : at + 86400000;
}

/** The part every telemetry object shares: its daily alarm, set on first use, which applies the retention periods. */
class TelemetryObject extends DurableObject {
  async ensureAlarm() {
    if (this.alarmSet) return;
    this.alarmSet = true;
    try {
      if ((await this.ctx.storage.getAlarm()) === null) await this.ctx.storage.setAlarm(nextPrune());
    } catch (err) {
      this.alarmSet = false;
      console.error("alarm", err?.name || "Error");
    }
  }

  async alarm() {
    try {
      const cfg = telemetryConfig(this.env);
      this.retain(reportDay(Date.now(), cfg.offset), cfg);
    } finally {
      await this.ctx.storage.setAlarm(nextPrune());
    }
  }
}

export class UsageLog extends TelemetryObject {
  constructor(ctx, env) {
    super(ctx, env);
    this.core = new UsageCore(ctx.storage.sql);
  }

  retain(today, cfg) { return this.core.prune(today, { events: cfg.retention.events, aggregates: cfg.retention.aggregates }); }

  async ingest(body, ctx) { await this.ensureAlarm(); return this.core.ingest(body, ctx); }

  async observe(list, ctx) { await this.ensureAlarm(); return this.core.observe(list, ctx); }

  stats(q) { return this.core.stats(q); }

  exportRows(dataset, from, to, page) { return this.core.exportRows(dataset, from, to, page); }

  prune(today, retention) { return this.core.prune(today, retention); }
}

export class ConversationLog extends TelemetryObject {
  constructor(ctx, env) {
    super(ctx, env);
    this.core = new ConversationCore(ctx.storage.sql);
  }

  retain(today, cfg) { return this.core.prune(today, { days: cfg.retention.conversations }); }

  async accept(record, ctx) { await this.ensureAlarm(); return this.core.accept(record, ctx); }

  forget(owner, ctx) { return this.core.forget(owner, ctx); }

  list(q) { return this.core.list(q); }

  exportRows(from, to, page) { return this.core.exportRows(from, to, page); }

  deleteIds(ids, opts) { return this.core.deleteIds(ids, opts); }

  deleteOwner(owner, opts) { return this.core.deleteOwner(owner, opts); }

  stats(from, to, budget) { return this.core.stats(from, to, budget); }

  prune(today, opts) { return this.core.prune(today, opts); }
}

export class SecurityLog extends TelemetryObject {
  constructor(ctx, env) {
    super(ctx, env);
    this.core = new SecurityCore(ctx.storage.sql);
  }

  retain(today, cfg) { return this.core.prune(today, cfg.retention.security); }

  async record(ev, opts) { await this.ensureAlarm(); return this.core.record(ev, opts); }

  async recordBatch(batch, opts) { await this.ensureAlarm(); return this.core.recordBatch(batch, opts); }

  authState(ip, now) { return this.core.authState(ip, now); }

  authFailed(ip, now) { return this.core.authFailed(ip, now); }

  sessionEpoch() { return this.core.sessionEpoch(); }

  endSessions(now) { return this.core.endSessions(now); }

  authSucceeded(ip, now) { return this.core.authSucceeded(ip, now); }

  summary(from, to, budget) { return this.core.summary(from, to, budget); }

  exportRows(from, to, page) { return this.core.exportRows(from, to, page); }

  prune(today, days) { return this.core.prune(today, days); }
}

/** A binding's single object, by name, as a function (null when the binding is missing: that part is then off). */
const objectOf = (env, binding, name) => (env[binding] ? () => env[binding].get(env[binding].idFromName(name)) : null);

// this isolate's own counts, between flushes (one isolate serves one Worker's bindings), and its one pending trailing
// flush: what is still held after a flush goes when the window ends, even if no request comes by to send it (an
// isolate with no requests is soon retired, and what it held with it)
const metricBuffer = new MetricBuffer();
const securityBuffer = new SecurityBuffer();
let trailing = null; // {timer, done}

/** Send what the buffers hold when it is due (or everything, with force) → the calls, as promises that never reject. */
function sendHeld(env, now, force) {
  const sends = [];
  const send = (call) => {
    try {
      sends.push(Promise.resolve(call()).catch((err) => console.error("flush", err?.name || "Error")));
    } catch (err) {
      console.error("flush", err?.name || "Error"); // a binding that cannot even give a stub: the counts are lost
    }
  };
  const cfg = telemetryConfig(env);
  if (metricBuffer.counts.size && (force || metricBuffer.due(now))) {
    const batches = metricBuffer.take(now);
    const usage = objectOf(env, "USAGE", "usage");
    // the usage object's day of rows written: its counts are dropped here, not sent, until 00:00 UTC
    if (usage && cfg.analytics && !spentFor("usage", now)) {
      for (const { day, list } of batches) {
        send(() => Promise.resolve(usage().observe(list, { day, budget: cfg.budgets.usage, now })).then((res) => {
          if (res?.reason === "budget") markSpent("usage", res.retry);
        }));
      }
    }
  }
  if (securityBuffer.counts.size && (force || securityBuffer.due(now))) {
    const batch = securityBuffer.take(now);
    const security = objectOf(env, "SECURITY", "security");
    // every event counted whatever the budget (the object stores them in full only within it)
    if (security) send(() => security().recordBatch(batch, { rawIp: cfg.rawIp, now, budget: cfg.budgets.security }));
  }
  return sends;
}

function cancelTrailing() {
  if (!trailing) return;
  clearTimeout(trailing.timer);
  trailing.done();
  trailing = null;
}

/** One flush when the current window ends, kept alive by this request's waitUntil (which allows 30 seconds after the
 * answer: the window, FLUSH_MS, is shorter). */
function armTrailing(env, ctx, now) {
  if (trailing) return;
  const ends = [metricBuffer, securityBuffer].filter((b) => b.counts.size).map((b) => b.last + FLUSH_MS);
  const t = { timer: null, done: null };
  const finished = new Promise((resolve) => { t.done = resolve; });
  t.timer = setTimeout(() => {
    if (trailing !== t) return;
    trailing = null;
    t.done(Promise.all(sendHeld(env, Date.now(), true)));
  }, Math.min(FLUSH_MS, Math.max(0, Math.min(...ends) - now)));
  trailing = t;
  deferOf(ctx)(finished);
}

/** Send what the buffers hold when it is due (or everything, with force): one call per object, after the answer; what
 * is left goes with the trailing flush. */
export function flushTelemetry(env, ctx, { force = false } = {}) {
  try {
    const now = Date.now();
    const defer = deferOf(ctx);
    for (const p of sendHeld(env, now, force)) defer(p);
    if (!metricBuffer.counts.size && !securityBuffer.counts.size) cancelTrailing();
    else armTrailing(env, ctx, now);
  } catch (err) {
    console.error("flush", err?.name || "Error");
  }
}

/** For tests: forget what the buffers hold (one test's counts never reach another's objects). */
export function resetTelemetryBuffers() {
  cancelTrailing();
  resetSpent();
  metricBuffer.take(0);
  metricBuffer.last = -Infinity;
  securityBuffer.take(0);
  securityBuffer.last = -Infinity;
}

/** A security event, summed in memory and sent with the next flush (index.js flushTelemetry). */
const securityEventOf = (env, ctx, cfg) => (env.SECURITY
  ? (kind, request, { route = "", status = null, detail = "" } = {}) => {
    const at = Date.now();
    securityBuffer.add({ kind, route, status, detail, facts: requestFacts(request), day: reportDay(at, cfg.offset), at });
    flushTelemetry(env, ctx);
  }
  : null);

/** The dependencies the telemetry and admin handlers use. */
export function telemetryDeps(env, ctx) {
  return {
    usage: objectOf(env, "USAGE", "usage"),
    conversations: objectOf(env, "CONVERSATIONS", "conversations"),
    security: objectOf(env, "SECURITY", "security"),
    securityEvent: securityEventOf(env, ctx, telemetryConfig(env)),
    assets: env.ASSETS || null,
    burst: burstOf(env),
    defer: deferOf(ctx),
    now: () => Date.now(),
  };
}

function burstOf(env) {
  // the ten-second gate is a cheap, approximate pre-filter in front of the Durable Object, which still counts every
  // call: if the binding itself fails, the call goes on to the object rather than being refused
  return async (key) => {
    if (!env.BURST) return true;
    try {
      return (await env.BURST.limit({ key })).success;
    } catch (err) {
      console.error("burst", err?.name || "Error");
      return true;
    }
  };
}

const deferOf = (ctx) => (promise) => ctx.waitUntil(Promise.resolve(promise).catch((err) => console.error("deferred", err?.name || "Error")));

/** The relay's dependencies, bound to this Worker's bindings. */
export function relayDeps(env, ctx) {
  const limiter = () => env.LIMITER.get(env.LIMITER.idFromName("limits")); // one object: exact counts for everyone
  const defer = deferOf(ctx);
  const cfg = telemetryConfig(env);
  return {
    // throws when the object cannot be reached: the relay then refuses the call (fail closed)
    gate: ({ who, kind, limits, reserve }) => limiter().hit(who, kind, limits, reserve),
    spend: (who, kind, tokens) => limiter().spend(who, kind, tokens),
    pace: (host, rps, prefixes) => limiter().sourceSlot(host, rps, prefixes),
    burst: burstOf(env),
    defer,
    fetch: (input, init) => fetch(input, init),
    now: () => Date.now(),
    // counts the Worker takes itself (Tao-S1 calls and tokens, database gateway requests): summed in memory and sent
    // with the next flush, never in a call's way, never with anything a visitor wrote
    metric: env.USAGE && cfg.analytics ? (list) => {
      metricBuffer.add(list, reportDay(Date.now(), cfg.offset));
      flushTelemetry(env, ctx);
    } : null,
    securityEvent: securityEventOf(env, ctx, cfg),
  };
}

/** Whether a request is a browser navigating to a page (a deep link into the app), rather than fetching a file. */
export function isNavigation(request) {
  const mode = request.headers.get("Sec-Fetch-Mode");
  if (mode) return mode === "navigate";
  return (request.headers.get("Accept") || "").includes("text/html"); // browsers without Fetch Metadata
}

/** A path that matched no file (not_found_handling = "none" sends it here). A browser navigating to one of the app's
 * own routes gets the app; anything else (a script, a JSON file the app asked for) is told the truth: 404, never the
 * app's HTML in its place. A scanner's probe (/wp-login.php, /.env …) is a 404 too, noted as a security event. */
async function missing(request, env, url, deps) {
  const redirect = redirectHttps(url);
  if (redirect) return redirect;
  if (isHoneypot(url.pathname)) {
    deps.securityEvent?.("scanner", request, { route: "page", status: 404, detail: url.pathname.slice(0, 80) });
    return new Response("Not Found\n", { status: 404, headers: { "Content-Type": "text/plain; charset=utf-8", "Cache-Control": "no-cache" } });
  }
  if (request.method !== "GET" && request.method !== "HEAD") {
    return new Response("Method Not Allowed\n", { status: 405, headers: { Allow: "GET, HEAD", "Content-Type": "text/plain; charset=utf-8" } });
  }
  if (isNavigation(request) && env.ASSETS) {
    const page = await env.ASSETS.fetch(new Request(new URL("/", url), { method: request.method, headers: request.headers }));
    // 304: the browser revalidated the copy of the app it keeps for this address (a reload), and it is still current
    if (page.ok || page.status === 304) return page;
  }
  return new Response(request.method === "HEAD" ? null : "Not Found\n", {
    status: 404, headers: { "Content-Type": "text/plain; charset=utf-8", "Cache-Control": "no-cache" },
  });
}

const under = (path, prefix) => path === prefix || path.startsWith(`${prefix}/`);

async function route(request, env, ctx) {
  const url = new URL(request.url);
  const path = url.pathname;
  if (under(path, "/v1")) return secure(await handle(request, env, relayDeps(env, ctx)), env);
  if (under(path, "/api/sources")) return secure(await handleSources(request, env, relayDeps(env, ctx)), env);
  if (under(path, "/api/datasets")) return secure(await handleDatasets(request, env, relayDeps(env, ctx)), env);
  if (under(path, "/api/telemetry")) return secure(await handleTelemetry(request, env, telemetryDeps(env, ctx)), env);
  if (under(path, "/api/admin") || under(path, "/admin")) return adminSecure(secure(await handleAdmin(request, env, telemetryDeps(env, ctx)), env));
  return secure(await missing(request, env, url, relayDeps(env, ctx)), env);
}

export default {
  async fetch(request, env, ctx) {
    const res = await route(request, env, ctx);
    flushTelemetry(env, ctx); // what earlier requests left in memory, once it is due
    return res;
  },
};
