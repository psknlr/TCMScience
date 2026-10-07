// The Tao-S1 relay under https://science.impf.ai/v1/* (CONTRACTS.md §1). A port of TaoChronos relay/src/relay.js
// without its sentinel, usage statistics and conversation records.
//   GET  /v1/health            {ok, service, version, model, models, max_output_tokens, limits}
//   GET  /v1/models            the public model only
//   POST /v1/chat/completions  forwarded (streamed) to the upstream with the key, which only this Worker holds
// The upstream is never named to a visitor: the model is renamed both ways, its own fields are dropped, and its errors
// are told in this service's words. Errors are OpenAI-style {error: {message, type}}; the message is Chinese and says
// what to do, the page may localize by `type`.

import { day } from "./limiter.js";

export const VERSION = "1";
export const SERVICE = "tcmscience-studio";
const LOCAL = new Set(["localhost", "127.0.0.1", "[::1]"]);

/** The Worker's variables when unset or empty; wrangler.toml sets the same (test/config.test.js checks they agree). */
export const DEFAULTS = {
  RELAY: "on",
  UPSTREAM_BASE: "https://api.minimax.cn/v1",
  MODELS: "MiniMax-M3",
  PUBLIC_MODEL: "Tao-S1",
  UPSTREAM_FIELDS: '{"reasoning_split":true}',
  FORMAT_PREFIX: "MiniMax-",
  MAX_OUTPUT_TOKENS: "8192",
  MAX_BODY_BYTES: "2000000",
  PER_MINUTE: "40",
  PER_DAY: "1200",
  TOTAL_PER_DAY: "20000",
  TOKENS_PER_DAY: "10000000",
  TOTAL_TOKENS_PER_DAY: "200000000",
  ALLOWED_ORIGINS: "https://science.impf.ai,http://127.0.0.1:8765,http://localhost:8765",
};

export function config(env = {}) {
  const get = (k) => (env[k] !== undefined && env[k] !== null && String(env[k]).trim() !== "" ? String(env[k]).trim() : DEFAULTS[k]);
  // a mistyped number must not lift a limit (NaN compares false with everything): it falls back to the default
  const num = (k, min = 0) => {
    const v = Number(get(k));
    return Number.isFinite(v) && v >= min ? Math.floor(v) : Number(DEFAULTS[k]);
  };
  const list = (k) => String(get(k)).split(",").map((s) => s.trim()).filter(Boolean);
  const models = list("MODELS");
  let fields = {};
  try {
    const v = JSON.parse(String(get("UPSTREAM_FIELDS")));
    if (v && typeof v === "object" && !Array.isArray(v)) fields = v;
  } catch { /* none */ }
  return {
    enabled: !/^(off|0|false|no)$/i.test(get("RELAY")),
    upstream: String(get("UPSTREAM_BASE")).replace(/\/+$/, ""),
    model: models[0] || DEFAULTS.MODELS,
    publicModel: String(get("PUBLIC_MODEL")),
    upstreamFields: fields,
    formatPrefix: String(get("FORMAT_PREFIX")),
    maxTokens: num("MAX_OUTPUT_TOKENS", 1),
    maxBody: num("MAX_BODY_BYTES", 1),
    origins: list("ALLOWED_ORIGINS"),
    chat: {
      perMinute: num("PER_MINUTE"), perDay: num("PER_DAY"), total: num("TOTAL_PER_DAY"),
      tokensPerDay: num("TOKENS_PER_DAY"), totalTokens: num("TOTAL_TOKENS_PER_DAY"),
    },
  };
}

// ------------------------------------------------------------------ what a visitor is told
const OWN = "或在“设置 → 模型”里改用自己的模型";
const RESET = "北京时间每天 8:00（UTC 零点）重置";
const MSG = {
  https_required: "请使用 https 访问",
  not_found: "没有这个接口",
  method_not_allowed: "这个接口不接受这种请求方法",
  forbidden_origin: "Tao-S1 只供 TCMScience Studio 网页（science.impf.ai）使用",
  not_configured: `Tao-S1 目前没有开放：请在“设置 → 模型”里改用自己的模型`,
  bad_json: "请求不是有效的 JSON",
  no_messages: "请求缺少 messages",
  too_large: (bytes) => `对话太长，超过了 Tao-S1 的上限（${bytes >= 1e6 ? `${Math.round(bytes / 1e5) / 10} MB` : `${Math.round(bytes / 1e3)} KB`}）：请新开一个对话，${OWN}`,
  model_not_allowed: (name) => `这里只提供 ${name}：请在请求里选用 ${name}，${OWN}`,
  burst: "请求太频繁，请 10 秒后再试",
  minute: (n, s) => `请求太频繁：每分钟最多 ${n} 次模型调用，请 ${s} 秒后再试`,
  day: (n) => `今天的 Tao-S1 免费额度已用完（每人每天 ${n} 次模型调用，${RESET}）：请到时再来，${OWN}`,
  tokens: `今天的 Tao-S1 免费额度已用完（按用量计，${RESET}）：请到时再来，${OWN}`,
  total: `Tao-S1 今天的总额度已用完（全体访客，${RESET}）：请到时再来，${OWN}`,
  total_tokens: `Tao-S1 今天的总额度已用完（全体访客，按用量计，${RESET}）：请到时再来，${OWN}`,
  unavailable: `Tao-S1 暂时不可用，请稍后再试，${OWN}`,
  upstream_unreachable: `Tao-S1 暂时连不上模型服务，请稍后再试，${OWN}`,
  upstream_auth: `Tao-S1 暂时不可用（模型服务拒绝了中继的密钥），请稍后再试，${OWN}`,
  upstream_rate: `Tao-S1 的模型服务暂时繁忙，请稍后再试，${OWN}`,
  upstream_quota: `Tao-S1 的模型额度暂时用完了，请稍后再试，${OWN}`,
  upstream_error: `Tao-S1 出错了，请稍后再试，${OWN}`,
  upstream_rejected: "Tao-S1 无法完成这个请求：请检查“设置 → 模型”里的温度、最大输出等选项，或新开一个对话后重试",
  content_rejected: `Tao-S1 无法回答这个请求（内容未通过模型服务的审核）：请换一种说法，${OWN}`,
  relay_error: `Tao-S1 中继出错了，请稍后再试，${OWN}`,
};

// The upstream's failures, by kind: [status, error.type, message]. HTTP statuses map in chat(); MiniMax also reports
// failures as base_resp.status_code inside a 200 reply (even to a stream request), and inside a stream.
const FAILURE = {
  upstream_auth: [503, "upstream_auth", MSG.upstream_auth],
  upstream_rate: [429, "upstream_rate", MSG.upstream_rate],
  upstream_quota: [503, "upstream_quota", MSG.upstream_quota],
  upstream_error: [502, "upstream_error", MSG.upstream_error],
  upstream_unreachable: [502, "upstream_unreachable", MSG.upstream_unreachable],
  upstream_rejected: [400, "upstream_rejected", MSG.upstream_rejected],
  content_rejected: [400, "upstream_rejected", MSG.content_rejected],
};
const BASE_RESP = {
  1002: "upstream_rate", 1039: "upstream_rate", // requests / tokens per minute
  1004: "upstream_auth", 2049: "upstream_auth", // authentication failed, invalid key
  1008: "upstream_quota", // insufficient balance
  1026: "content_rejected", 1027: "content_rejected", // the input / the output did not pass moderation
  2013: "upstream_rejected", // invalid parameters
};
const failureOf = (code) => FAILURE[BASE_RESP[code] || "upstream_error"];

// ------------------------------------------------------------------ the handler
const ROUTES = { "/v1/health": "GET", "/v1/models": "GET", "/v1/chat/completions": "POST" };

/**
 * deps: {gate({who, kind, limits, reserve}) → {ok, reason?, retry?, remaining?, reserved?}, spend(who, kind, tokens),
 * burst(key) → boolean, defer(promise), fetch(url, init), now() → ms}. The Worker (index.js) binds them to the Durable
 * Object, the ratelimit binding and ctx.waitUntil; the tests bind them to fakes.
 */
export async function handle(request, env, deps) {
  const cfg = config(env);
  const url = new URL(request.url);
  const origin = request.headers.get("Origin") || "";
  const allowed = origin !== "" && cfg.origins.includes(origin);
  // Retry-After is not a CORS-safelisted response header: a page on another allowed origin (the local runner's) could
  // not read it otherwise
  const cors = allowed
    ? { "Access-Control-Allow-Origin": origin, "Access-Control-Expose-Headers": "Retry-After", Vary: "Origin" }
    : { Vary: "Origin" };
  if (url.protocol === "http:" && !LOCAL.has(url.hostname)) return error(403, MSG.https_required, "https_required", cors);
  if (request.method === "OPTIONS") {
    if (!allowed) return new Response(null, { status: 403, headers: cors });
    return new Response(null, {
      status: 204,
      headers: {
        ...cors, "Access-Control-Allow-Methods": "GET, HEAD, POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type, Accept, Authorization", "Access-Control-Max-Age": "86400",
      },
    });
  }
  try {
    const want = ROUTES[url.pathname];
    if (!want) return error(404, MSG.not_found, "not_found", cors);
    const method = request.method === "HEAD" && want === "GET" ? "GET" : request.method;
    if (method !== want) {
      return error(405, MSG.method_not_allowed, "method_not_allowed", { ...cors, Allow: want === "GET" ? "GET, HEAD" : want });
    }
    if (url.pathname === "/v1/health") return health(env, cfg, cors);
    if (url.pathname === "/v1/models") return json({ object: "list", data: [{ id: cfg.publicModel, object: "model", owned_by: "impf" }] }, 200, cors);
    return await chat(request, env, cfg, cors, allowed, deps);
  } catch (err) {
    console.error("relay error", err?.name || "Error"); // never the request: it carries what a visitor wrote
    return error(500, MSG.relay_error, "relay_error", cors);
  }
}

// ------------------------------------------------------------------ helpers
const json = (data, status = 200, headers = {}) => new Response(JSON.stringify(data), {
  status, headers: { ...headers, "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store" },
});
const error = (status, message, type, headers = {}) => json({ error: { message, type } }, status, headers);
const encoder = new TextEncoder();
const hex = (bytes) => [...new Uint8Array(bytes)].map((b) => b.toString(16).padStart(2, "0")).join("");

/** IPv4 as it is; IPv6 by its /64 (privacy addresses rotate the interface half). */
export function ipKey(ip) {
  if (!ip || !ip.includes(":")) return ip || "unknown";
  const [head, tail = ""] = ip.split("::");
  const left = head ? head.split(":") : [];
  const right = tail ? tail.split(":") : [];
  const groups = [...left, ...Array(Math.max(0, 8 - left.length - right.length)).fill("0"), ...right].slice(0, 8)
    .map((g) => (g || "0").toLowerCase().replace(/^0+(?=.)/, ""));
  return `${groups.slice(0, 4).join(":")}::/64`;
}

/** The visitor, for the counters: sha256(salt | ipKey | UTC day), 24 hex characters. The address is kept nowhere. */
async function caller(request, env, now) {
  const ip = request.headers.get("CF-Connecting-IP") || "unknown";
  const digest = await crypto.subtle.digest("SHA-256", encoder.encode(`${env.VISITOR_SALT || ""}|${ipKey(ip)}|${day(now)}`));
  return hex(digest).slice(0, 24);
}

/** The body's text, or null when it is over `max` bytes (by its declared length, else while it is read). */
async function readBody(request, max) {
  const declared = request.headers.get("Content-Length");
  if (declared !== null && Number(declared) > max) return null;
  if (!request.body) return "";
  const reader = request.body.getReader();
  const parts = [];
  let size = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    size += value.byteLength;
    if (size > max) {
      await reader.cancel().catch(() => {});
      return null;
    }
    parts.push(value);
  }
  const bytes = new Uint8Array(size);
  let at = 0;
  for (const part of parts) {
    bytes.set(part, at);
    at += part.byteLength;
  }
  return new TextDecoder().decode(bytes);
}

async function bursting(request, deps) {
  if (!deps.burst) return false;
  return !(await deps.burst(ipKey(request.headers.get("CF-Connecting-IP") || "unknown")));
}

function limited(gate, limits, cors) {
  const retry = Math.max(1, Math.ceil(Number(gate.retry) || 60));
  const headers = { ...cors, "Retry-After": String(retry) };
  switch (gate.reason) {
    case "burst": return error(429, MSG.burst, "rate_limited", { ...cors, "Retry-After": "10" });
    case "minute": return error(429, MSG.minute(limits.perMinute, retry), "rate_limited", headers);
    case "day": return error(429, MSG.day(limits.perDay), "daily_limit", headers);
    case "tokens": return error(429, MSG.tokens, "daily_limit", headers);
    case "total_tokens": return error(429, MSG.total_tokens, "total_limit", headers);
    default: return error(429, MSG.total, "total_limit", headers);
  }
}

function failed(kind, cors, retryAfter) {
  const [status, type, message] = FAILURE[kind];
  return error(status, message, type, status === 429 ? { ...cors, "Retry-After": retryAfter || "30" } : cors);
}

const cancel = (response) => response.body?.cancel().catch(() => {});

// ------------------------------------------------------------------ endpoints
function health(env, cfg, cors) {
  return json({
    ok: cfg.enabled && Boolean(env.MINIMAX_API_KEY),
    service: SERVICE, version: VERSION, model: cfg.publicModel, models: [cfg.publicModel],
    max_output_tokens: cfg.maxTokens,
    limits: { per_minute: cfg.chat.perMinute, per_day: cfg.chat.perDay, tokens_per_day: cfg.chat.tokensPerDay },
  }, 200, cors);
}

// Checks run cheapest first, and a refused request is never counted: origin → configured → ten-second gate (before
// the body is read) → body size → JSON → messages → model → the counters → the upstream.
async function chat(request, env, cfg, cors, allowed, deps) {
  if (!allowed) return error(403, MSG.forbidden_origin, "forbidden_origin", cors);
  if (!cfg.enabled || !env.MINIMAX_API_KEY) return error(503, MSG.not_configured, "not_configured", cors);
  if (await bursting(request, deps)) return limited({ reason: "burst" }, cfg.chat, cors);
  const raw = await readBody(request, cfg.maxBody);
  if (raw === null) return error(413, MSG.too_large(cfg.maxBody), "too_large", cors);
  let body;
  try {
    body = JSON.parse(raw);
  } catch {
    return error(400, MSG.bad_json, "bad_request", cors);
  }
  if (!body || typeof body !== "object" || Array.isArray(body) || !Array.isArray(body.messages) || !body.messages.length) {
    return error(400, MSG.no_messages, "bad_request", cors);
  }
  // only the public name (or none): accepting the upstream's own names would let anyone confirm what it is
  const asked = body.model;
  if (asked !== undefined && asked !== null && asked !== "" && asked !== cfg.publicModel) {
    return error(400, MSG.model_not_allowed(cfg.publicModel), "model_not_allowed", cors);
  }
  const fields = Object.fromEntries(Object.entries(cfg.upstreamFields).filter(([k]) => !(k in body)));
  let rewrite = false;
  let capped = false;
  for (const k of ["max_tokens", "max_completion_tokens"]) {
    if (k in body) {
      const v = Math.min(Math.max(1, Math.floor(Number(body[k]) || cfg.maxTokens)), cfg.maxTokens);
      if (v !== body[k]) {
        body[k] = v;
        rewrite = true;
      }
      capped = true;
    }
  }
  if (!capped) {
    body.max_completion_tokens = cfg.maxTokens;
    rewrite = true;
  }
  if ("n" in body) { // one answer per call
    delete body.n;
    rewrite = true;
  }
  let payload = rewrite ? null : upstreamText(raw, cfg.model, fields, cfg);
  if (payload === null) {
    Object.assign(body, fields);
    body.model = cfg.model;
    restoreFormats(body.messages, cfg);
    payload = JSON.stringify(body);
  }
  const noReasoning = body.thinking?.type === "disabled";

  const who = await caller(request, env, deps.now());
  const reserve = Math.ceil(raw.length / 3); // about a token per three bytes of request, charged now, settled by the answer
  let gate;
  try {
    gate = await deps.gate({ who, kind: "chat", limits: cfg.chat, reserve });
  } catch (err) {
    gate = null;
    console.error("gate", err?.name || "Error");
  }
  if (!gate || typeof gate.ok !== "boolean") { // fail closed: never an uncounted call
    return error(503, MSG.unavailable, "unavailable", { ...cors, "Retry-After": "60" });
  }
  if (!gate.ok) return limited(gate, cfg.chat, cors);

  const counting = cfg.chat.tokensPerDay > 0 || cfg.chat.totalTokens > 0;
  let charged = Number(gate.reserved) || 0;
  // usage is the answer's whole cost so far; charging the difference counts it once however often it is reported
  const spend = (usage) => {
    if (!counting) return;
    const tokens = (Number(usage?.prompt_tokens) || 0) + (Number(usage?.completion_tokens) || 0);
    if (tokens <= 0 || tokens === charged) return;
    const delta = tokens - charged;
    charged = tokens;
    deps.defer(Promise.resolve().then(() => deps.spend(who, "chat", delta)));
  };
  // a call that brought no answer costs the visitor no tokens (it still counts as a call)
  const refund = () => {
    if (!counting || charged <= 0) return;
    const back = -charged;
    charged = 0;
    deps.defer(Promise.resolve().then(() => deps.spend(who, "chat", back)));
  };

  let upstream;
  try {
    upstream = await deps.fetch(`${cfg.upstream}/chat/completions`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json", Accept: request.headers.get("Accept") || "application/json",
        Authorization: `Bearer ${env.MINIMAX_API_KEY}`,
      },
      body: payload,
      signal: request.signal, // the visitor stopping the answer, or leaving, ends the model call (enable_request_signal)
    });
  } catch (err) {
    if (!request.signal?.aborted) console.error("upstream unreachable", err?.name || "Error");
    refund();
    return failed("upstream_unreachable", cors);
  }
  const type = upstream.headers.get("Content-Type") || "";
  if (!upstream.ok) {
    await cancel(upstream); // its text names the vendor, the model, the parameters, sometimes the key's prefix
    console.error("upstream", upstream.status);
    refund();
    if (upstream.status === 401 || upstream.status === 403) return failed("upstream_auth", cors);
    if (upstream.status === 429) return failed("upstream_rate", cors, retryAfterOf(upstream));
    if (upstream.status >= 500 || upstream.status === 404) return failed("upstream_error", cors);
    return failed("upstream_rejected", cors);
  }
  const headers = { ...cors, "Content-Type": type, "Cache-Control": "no-store" };
  if (type.includes("event-stream") && upstream.body) {
    const source = noReasoning ? upstream.body.pipeThrough(reasoningFilter()) : upstream.body;
    return new Response(source.pipeThrough(publicFilter(cfg, spend)), { status: upstream.status, headers });
  }
  if (!type.includes("json")) {
    await cancel(upstream);
    console.error("upstream content type", type.split(";")[0] || "none");
    refund();
    return failed("upstream_error", cors);
  }
  const text = await upstream.text();
  let j = null;
  try {
    j = JSON.parse(text);
  } catch { /* told below */ }
  const code = j?.base_resp?.status_code;
  if (!j || typeof j !== "object" || Array.isArray(j) || code || j.error) {
    console.error("upstream error in a reply", code || "error");
    refund();
    return failed(BASE_RESP[code] || "upstream_error", cors);
  }
  const out = noReasoning ? JSON.stringify(withoutReasoning(j)) : text;
  return new Response(publicText(out, cfg, spend), { status: upstream.status, headers });
}

function retryAfterOf(response) {
  const v = response.headers.get("Retry-After");
  return v && /^\d{1,4}$/.test(v.trim()) ? v.trim() : null;
}

// ------------------------------------------------------------------ what a visitor sees of the upstream: nothing
const PUBLIC_FORMAT = "Tao-";
const MODEL_FIELD = /"model"\s*:\s*"(?:[^"\\]|\\.)*"/g;
const LEADING_MODEL = /^\{\s*"model"\s*:\s*"(?:[^"\\]|\\.)*"\s*,/;
const PUBLIC_FORMAT_FIELD = /"format"\s*:\s*"Tao-/g;
// fields only the upstream sends (its status block, its content flags, a count in its usage), an error it reports
// inside a reply — and the usage itself (an object: the final event of a stream; "usage":null in the events before it
// is passed over), read for what the answer cost
const VENDOR = String.raw`"(?:base_resp|input_sensitive|output_sensitive|input_sensitive_type|output_sensitive_type|output_sensitive_int|total_characters|error)"\s*:|"usage"\s*:\s*\{`;
const VENDOR_FIELD = new RegExp(VENDOR);
const VENDOR_KEYS = ["base_resp", "input_sensitive", "output_sensitive", "input_sensitive_type", "output_sensitive_type", "output_sensitive_int"];
const patterns = new Map();
const escapeRe = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

/**
 * The page's request in the upstream's terms, edited as text (re-serializing a long conversation costs CPU, which is
 * counted per request): the model, the upstream's fields after it, the reasoning formats of the turns before. A quote
 * inside a JSON string is escaped, so these patterns meet only keys and their values. Null unless the text opens with
 * the model, as the page writes it (CONTRACTS.md §1).
 */
function upstreamText(raw, model, fields, cfg) {
  if (!LEADING_MODEL.test(raw)) return null;
  const extra = JSON.stringify(fields).slice(1, -1);
  let out = raw.replace(LEADING_MODEL, () => `{"model":${JSON.stringify(model)},${extra ? `${extra},` : ""}`);
  if (cfg.formatPrefix && out.includes(PUBLIC_FORMAT)) out = out.replace(PUBLIC_FORMAT_FIELD, () => `"format":"${cfg.formatPrefix}`);
  return out;
}

/** The reasoning a page sends back (the turns before), under the upstream's own format names again. */
function restoreFormats(messages, cfg) {
  if (!cfg.formatPrefix) return;
  for (const m of messages) {
    if (!Array.isArray(m?.reasoning_details)) continue;
    for (const d of m.reasoning_details) {
      if (typeof d?.format === "string" && d.format.startsWith(PUBLIC_FORMAT)) d.format = cfg.formatPrefix + d.format.slice(PUBLIC_FORMAT.length);
    }
  }
}

/** The upstream's model and reasoning-format names in a text, replaced by the public ones (a text pass). */
function renamed(text, cfg) {
  let out = text.replace(MODEL_FIELD, () => `"model":${JSON.stringify(cfg.publicModel)}`);
  if (cfg.formatPrefix && out.includes(cfg.formatPrefix)) {
    const key = `format:${cfg.formatPrefix}`;
    if (!patterns.has(key)) patterns.set(key, new RegExp(String.raw`"format"\s*:\s*"${escapeRe(cfg.formatPrefix)}`, "g"));
    out = out.replace(patterns.get(key), () => `"format":"${PUBLIC_FORMAT}`);
  }
  return out;
}

/**
 * A reply, or one event's data, as a visitor may see it: the public model name, the reasoning formats renamed, the
 * upstream's own fields dropped, and an error it reports inside a reply told in this service's words. Text only in the
 * common case. `onUsage` is told the usage a reply carries (what the answer cost).
 */
export function publicText(text, cfg, onUsage) {
  const out = renamed(text, cfg);
  if (!VENDOR_FIELD.test(out)) return out;
  let j;
  try {
    j = JSON.parse(out);
  } catch {
    return out;
  }
  if (!j || typeof j !== "object" || Array.isArray(j)) return out;
  const code = j.base_resp?.status_code;
  if (j.error || code) {
    console.error("upstream error in a stream", code || "error");
    const [, type, message] = failureOf(code);
    return JSON.stringify({ error: { message, type } });
  }
  for (const k of VENDOR_KEYS) delete j[k];
  for (const c of Array.isArray(j.choices) ? j.choices : []) {
    if (c && typeof c === "object") for (const k of VENDOR_KEYS) delete c[k];
  }
  if (j.usage && typeof j.usage === "object") {
    if (onUsage) {
      try {
        onUsage(j.usage);
      } catch { /* the count is not the visitor's concern */ }
    }
    delete j.usage.total_characters;
  }
  return JSON.stringify(j);
}

/** One pattern for all the stream's text may hold of the upstream: a model name, a reasoning format with its prefix,
 * one of its own fields; a single pass finds them all. */
function streamPattern(cfg) {
  const key = `stream:${cfg.formatPrefix}`;
  if (!patterns.has(key)) {
    const format = cfg.formatPrefix ? String.raw`|"format"\s*:\s*"${escapeRe(cfg.formatPrefix)}` : "";
    patterns.set(key, new RegExp(String.raw`"model"\s*:\s*"(?:[^"\\]|\\.)*"|${VENDOR}${format}`, "g"));
  }
  return patterns.get(key);
}

/**
 * The stream's events as a visitor may see them, passed on up to the last whole line that has arrived, in one pass
 * over the text: the model's and the reasoning formats' names replaced; a stretch naming one of the upstream's own
 * fields (its final usage, or an error) is read event by event (publicText). Every byte of an answer passes through
 * here and the Worker's CPU is counted per request, so nothing is done twice.
 */
export function publicFilter(cfg, onUsage) {
  const decoder = new TextDecoder();
  const pattern = streamPattern(cfg);
  const model = `"model":${JSON.stringify(cfg.publicModel)}`;
  const format = `"format":"${PUBLIC_FORMAT}`;
  let buffer = "";
  const pass = (text) => {
    let vendor = false;
    // the match starts with a quote: "m(odel), "f(ormat), or else a vendor field
    const out = text.replace(pattern, (m) => (m.charCodeAt(1) === 0x6d ? model : m.charCodeAt(1) === 0x66 ? format : ((vendor = true), m)));
    if (!vendor) return encoder.encode(out);
    return encoder.encode(out.split(/\r?\n/).map((line) => {
      const m = /^data:\s?(.*)$/.exec(line);
      if (!m || m[1].trim() === "[DONE]") return line;
      return `data: ${publicText(m[1], cfg, onUsage)}`;
    }).join("\n"));
  };
  return new TransformStream({
    transform(chunk, controller) {
      buffer += decoder.decode(chunk, { stream: true });
      const end = buffer.lastIndexOf("\n");
      if (end < 0) return;
      controller.enqueue(pass(buffer.slice(0, end + 1)));
      buffer = buffer.slice(end + 1);
    },
    flush(controller) {
      buffer += decoder.decode();
      if (buffer) controller.enqueue(pass(buffer));
    },
  });
}

/**
 * A completion, or one chunk of a stream, without the model's reasoning: its reasoning fields, and whatever of its
 * content lies inside <think>…</think>. `open` carries, per choice, whether the stream is inside such a block: chunks
 * may bring the content piece by piece, or (as MiniMax's examples read) all of it so far each time.
 */
export function withoutReasoning(j, open = new Map()) {
  for (const [k, choice] of (Array.isArray(j?.choices) ? j.choices : []).entries()) {
    for (const part of [choice?.delta, choice?.message]) {
      if (!part || typeof part !== "object") continue;
      delete part.reasoning_details;
      delete part.reasoning_content;
      delete part.reasoning;
      if (typeof part.content !== "string") continue;
      const index = choice.index ?? k;
      let text = part.content;
      let out = "";
      if (open.get(index) && !text.startsWith("<think>")) { // inside a block: drop up to its end
        const close = text.indexOf("</think>");
        if (close < 0) text = "";
        else {
          text = text.slice(close + 8).replace(/^\s+/, "");
          open.set(index, false);
        }
      }
      for (;;) {
        const at = text.indexOf("<think>");
        if (at < 0) {
          out += text;
          break;
        }
        out += text.slice(0, at);
        const close = text.indexOf("</think>", at);
        if (close < 0) {
          open.set(index, true);
          break;
        }
        open.set(index, false);
        text = text.slice(close + 8).replace(/^\s+/, "");
      }
      part.content = out;
    }
  }
  return j;
}

/** The stream's events, each without the model's reasoning (an event is read whole: its lines up to a blank line). */
export function reasoningFilter() {
  const decoder = new TextDecoder();
  let buffer = "";
  const open = new Map();
  const clean = (event) => event.split("\n").map((line) => {
    const m = /^data:\s?(.*)$/.exec(line);
    if (!m || m[1].trim() === "[DONE]") return line;
    try {
      return `data: ${JSON.stringify(withoutReasoning(JSON.parse(m[1]), open))}`;
    } catch {
      return line;
    }
  }).join("\n");
  return new TransformStream({
    transform(chunk, controller) {
      // normalized over the whole buffer: a \r\n may be split between two chunks
      buffer = (buffer + decoder.decode(chunk, { stream: true })).replace(/\r\n/g, "\n");
      let end;
      while ((end = buffer.indexOf("\n\n")) >= 0) {
        controller.enqueue(encoder.encode(`${clean(buffer.slice(0, end))}\n\n`));
        buffer = buffer.slice(end + 2);
      }
    },
    flush(controller) {
      buffer = (buffer + decoder.decode()).replace(/\r\n/g, "\n");
      if (buffer) controller.enqueue(encoder.encode(clean(buffer)));
    },
  });
}
