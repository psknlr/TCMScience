// The telemetry endpoints a page of this site calls (studio/docs/TELEMETRY.md), next to /api/sources/* and in the same
// conventions: JSON errors {error: {message, type}}, Chinese messages, every 429 with Retry-After.
//
//   GET  /api/telemetry/health                 what is taken, the consent notice's version, retention (also in /v1/health)
//   POST /api/telemetry/events                 a batch of usage events {v, batch, events} → 204
//   POST /api/telemetry/conversations          one finished turn of a visitor who agreed → 201 stored | 200 not stored
//   POST /api/telemetry/conversations/delete   {key}: every record made with this deletion key, deleted → {deleted}
//
// Only this site's own pages may call them (Origin = this host, Sec-Fetch-Site same-origin); nothing is sent back that a
// page did not already know. Checks run cheapest first: https → route → origin → the ten-second gate → body size →
// JSON → contract → the visitor's limits and the store (one Durable Object call). A failure of the store is the page's
// to ignore (503): telemetry never stands in the way of a conversation.

import { CONSENT_VERSION, OWNER_KEY, RECORD_VERSION, normalizeRecord, ownerOf } from "../../../web/js/core/telemetry-contract.js";
import { redactText, sensitiveAny } from "../../../web/js/core/sensitive.js";
import { callerKey, clientIp, fail, json, LOCAL, readText, sameSite, sha256Hex } from "../http.js";
import { config as relayConfig, ipKey } from "../relay.js";
import { reportDay, telemetryConfig, telemetryPublic } from "./config.js";
import { requestFacts } from "./security.js";

const ROUTES = {
  "/api/telemetry/health": "GET",
  "/api/telemetry/events": "POST",
  "/api/telemetry/conversations": "POST",
  "/api/telemetry/conversations/delete": "POST",
};
const MSG = {
  https_required: "请使用 https 访问",
  not_found: "没有这个接口",
  method_not_allowed: "这个接口不接受这种请求方法",
  forbidden_origin: "只接受本站网页发来的记录",
  burst: "请求太频繁，请 10 秒后再试",
  rate_limited: "记录太频繁，请稍后再试",
  analytics_off: "本站已停止收集使用统计",
  records_off: "本站已停止收集对话记录",
  too_large: "记录过大",
  bad_json: "请求不是有效的 JSON",
  bad_batch: "使用统计的格式不对",
  bad_record: "对话记录的格式不对",
  consent_outdated: "对话记录说明已更新：请重新阅读并选择是否同意后再发送",
  bad_key: "删除密钥的格式不对",
  unavailable: "记录服务暂时不可用，请稍后再试",
};

/** Where a request comes from, as coarse as TELEMETRY_GEO says: a country or territory code (CN), or XX (off, unknown). */
export function placeOf(request, geo) {
  return geo === "off" ? "XX" : requestFacts(request).country;
}

/**
 * deps: {usage() → UsageLog stub | null, conversations() → ConversationLog stub | null, security() → SecurityLog stub |
 * null, burst(key) → boolean, defer(promise), now() → ms}. A missing binding turns its part off (health says so).
 */
export async function handleTelemetry(request, env, deps) {
  const cfg = telemetryConfig(env);
  const url = new URL(request.url);
  if (url.protocol === "http:" && !LOCAL.has(url.hostname)) return fail(403, "https_required", MSG.https_required);
  const want = ROUTES[url.pathname];
  if (!want) return fail(404, "not_found", MSG.not_found);
  // no other site's page may use these: a preflight is never answered
  if (request.method === "OPTIONS") return new Response(null, { status: 403 });
  const method = request.method === "HEAD" && want === "GET" ? "GET" : request.method;
  if (method !== want) return fail(405, "method_not_allowed", MSG.method_not_allowed, { Allow: want === "GET" ? "GET, HEAD" : want });
  if (url.pathname === "/api/telemetry/health") {
    return json({ ok: true, service: "tcmscience-telemetry", version: 1, ...telemetryPublic(env) });
  }
  const now = deps.now();
  const day = reportDay(now, cfg.offset);
  const facts = requestFacts(request);
  const note = (kind, status, detail) => {
    if (!deps.security) return;
    deps.defer(Promise.resolve().then(() => deps.security().record({ kind, route: "telemetry", status, detail, facts, day }, { rawIp: cfg.rawIp, now })));
  };
  try {
    if (!sameSite(request, url, relayConfig(env).origins)) {
      note("forbidden_origin", 403, url.pathname.slice(15));
      return fail(403, "forbidden_origin", MSG.forbidden_origin);
    }
    if (deps.burst && !(await deps.burst(`telemetry:${ipKey(clientIp(request))}`))) {
      note("rate_limited", 429, "burst");
      return fail(429, "rate_limited", MSG.burst, { "Retry-After": "10" });
    }
    if (url.pathname === "/api/telemetry/events") return await events(request, env, cfg, deps, { now, day, note });
    if (url.pathname === "/api/telemetry/conversations") return await conversations(request, env, cfg, deps, { now, day, note });
    return await forget(request, env, deps, { now, day, note });
  } catch (err) {
    console.error("telemetry", err?.name || "Error"); // never the request: it may carry what a visitor wrote
    return fail(503, "unavailable", MSG.unavailable, { "Retry-After": "60" });
  }
}

async function readJson(request, max, note, label) {
  const raw = await readText(request, max);
  if (raw === null) {
    note("oversized", 413, label);
    return { error: fail(413, "too_large", MSG.too_large) };
  }
  try {
    return { body: JSON.parse(raw) };
  } catch {
    note("invalid_payload", 400, `${label}_json`);
    return { error: fail(400, "bad_request", MSG.bad_json) };
  }
}

const limited = (res) => fail(429, "rate_limited", MSG.rate_limited, { "Retry-After": String(Math.max(1, Math.ceil(Number(res.retry) || 60))) });

async function events(request, env, cfg, deps, { now, day, note }) {
  if (!cfg.analytics || !deps.usage) return fail(503, "analytics_off", MSG.analytics_off);
  const { body, error } = await readJson(request, cfg.maxEventBytes, note, "events");
  if (error) return error;
  // the visitor, for the day's distinct count: a digest of address and browser, salted again (and then forgotten) in
  // the object; the address is never stored, and the digest itself is not either
  const visitor = await sha256Hex(`${ipKey(clientIp(request))}|${request.headers.get("User-Agent") || ""}`);
  const res = await deps.usage().ingest(body, {
    day, now, country: placeOf(request, cfg.geo), visitor, caller: await callerKey(request, env, now), limits: cfg.eventLimits,
  });
  if (!res.ok && res.reason === "rate_limited") {
    note("rate_limited", 429, "events");
    return limited(res);
  }
  if (!res.ok) {
    note("invalid_payload", 400, "events_batch");
    return fail(400, "bad_request", MSG.bad_batch);
  }
  return new Response(null, { status: 204 });
}

async function conversations(request, env, cfg, deps, { now, day, note }) {
  if (!cfg.conversations || !deps.conversations) return fail(503, "records_off", MSG.records_off);
  const { body, error } = await readJson(request, cfg.maxRecordBytes, note, "record");
  if (error) return error;
  const record = normalizeRecord(body, { consentVersion: CONSENT_VERSION });
  if (!record) {
    // a record made under an older notice: the page asks again; anything else is not a record
    if (body && typeof body === "object" && body.v === RECORD_VERSION && body.consent !== CONSENT_VERSION) {
      return fail(409, "consent_outdated", MSG.consent_outdated);
    }
    note("invalid_payload", 400, "record");
    return fail(400, "bad_request", MSG.bad_record);
  }
  // the page has already left out what it must; the Worker judges again and keeps nothing it would not have sent
  const excluded = sensitiveAny([record.question, record.answer]);
  if (!excluded) {
    const q = redactText(record.question);
    const a = redactText(record.answer || "");
    record.question = q.text;
    record.answer = a.text;
    if (q.hits + a.hits) record.redacted = (record.redacted || 0) + q.hits + a.hits;
  }
  const res = await deps.conversations().accept(excluded ? null : record, {
    caller: await callerKey(request, env, now), limits: cfg.recordLimits, received: new Date(now).toISOString(), day, keep: cfg.maxRecords,
    excluded, now,
  });
  if (!res.ok) {
    note("rate_limited", 429, "records");
    return limited(res);
  }
  return json({ ok: true, stored: Boolean(res.stored), ...(res.reason ? { reason: res.reason } : {}) }, res.stored ? 201 : 200);
}

async function forget(request, env, deps, { now, day, note }) {
  // deletion works while collection is paused: what was kept can always be taken back
  if (!deps.conversations) return fail(503, "records_off", MSG.records_off);
  const { body, error } = await readJson(request, 2048, note, "forget");
  if (error) return error;
  const key = body && typeof body === "object" ? body.key : null;
  if (!OWNER_KEY.test(String(key || ""))) return fail(400, "bad_key", MSG.bad_key);
  const owner = await ownerOf(key, crypto.subtle);
  const res = await deps.conversations().forget(owner, { caller: await callerKey(request, env, now), day, now });
  if (!res.ok) {
    note("rate_limited", 429, "forget");
    return limited(res);
  }
  return json({ ok: true, deleted: res.deleted });
}
