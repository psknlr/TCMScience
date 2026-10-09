// Same-origin transport for the browser's existing HTTP backend. The build-generated
// registry is trusted; a caller's URL, host list and body are never an allow-list.
import { config as relayConfig, ipKey } from "./relay.js";
import { day } from "./limiter.js";

export const SOURCE_DEFAULTS = {
  SOURCES: "on", SOURCE_MAX_BODY_BYTES: "393216", SOURCE_MAX_RESPONSE_BYTES: "8388608",
  SOURCE_TIMEOUT_MS: "30000", SOURCE_PER_MINUTE: "120", SOURCE_PER_DAY: "3000", SOURCE_TOTAL_PER_DAY: "50000",
};
const LOCAL = new Set(["localhost", "127.0.0.1", "[::1]"]);
const MAX_URL = 16384;
const MAX_VALUE = 8192;
const FORWARD_HEADERS = new Set(["accept", "content-type", "authorization", "x-api-key", "api-key"]);
const REPLY_HEADERS = ["Content-Type", "Retry-After", "ETag", "Last-Modified", "Content-Disposition"];
const encoder = new TextEncoder();
const decoder = new TextDecoder("utf-8", { fatal: true });
const caches = new WeakMap();

export function sourceConfig(env = {}) {
  const get = (key) => String(env[key] ?? "").trim() || SOURCE_DEFAULTS[key];
  const num = (key, min, max) => {
    const n = Number(get(key));
    return Number.isInteger(n) && n >= min && n <= max ? n : Number(SOURCE_DEFAULTS[key]);
  };
  return {
    enabled: !/^(off|0|false|no)$/i.test(get("SOURCES")),
    maxBody: num("SOURCE_MAX_BODY_BYTES", 1024, 1048576),
    maxResponse: num("SOURCE_MAX_RESPONSE_BYTES", 1024, 16777216),
    timeout: num("SOURCE_TIMEOUT_MS", 1000, 30000),
    limits: {
      perMinute: num("SOURCE_PER_MINUTE", 1, 10000), perDay: num("SOURCE_PER_DAY", 1, 100000),
      total: num("SOURCE_TOTAL_PER_DAY", 1, 1000000), tokensPerDay: 0, totalTokens: 0,
    },
    origins: relayConfig(env).origins,
  };
}

const json = (value, status = 200, headers = {}) => new Response(JSON.stringify(value), {
  status, headers: { ...headers, "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store" },
});
class Refused extends Error {
  constructor(status, type, message) { super(message); this.status = status; this.type = type; }
}
const refuse = (status, type, message) => { throw new Refused(status, type, message); };
const object = (value) => value !== null && typeof value === "object" && !Array.isArray(value);
const escape = (text) => text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

// Placeholders accept bounded values. Everything else, including SQL/GraphQL text,
// API paths, fixed query fields and JSON object keys, must match the registered template.
function stringTemplate(template, value) {
  if (typeof value !== "string" || value.length > MAX_VALUE || /[\x00-\x1f\x7f]/.test(value)) return false;
  const pattern = String(template).split(/(\{\w+\})/g).map((part) => /^\{\w+\}$/.test(part) ? "[\\s\\S]*" : escape(part)).join("");
  return new RegExp(`^${pattern}$`).test(value);
}
function inlineQueryTemplate(template, value) {
  if (value.length > MAX_VALUE) return false;
  // RQL expressions registered inside a path use arguments as values, never as a
  // new expression or an extra query field. Preserve encoded spaces in fixed text.
  const pattern = template.split(/(\{\w+\})/g).map((part) => /^\{\w+\}$/.test(part) ? "[^&=(),]*" : escape(part)).join("");
  return new RegExp(`^${pattern}$`).test(value);
}
function bounded(value, depth = 0) {
  if (depth > 8) return false;
  if (value === null || typeof value === "boolean") return true;
  if (typeof value === "number") return Number.isFinite(value);
  if (typeof value === "string") return value.length <= MAX_VALUE && !/[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]/.test(value);
  if (Array.isArray(value)) return value.length <= 512 && value.every((v) => bounded(v, depth + 1));
  return object(value) && Object.keys(value).length <= 128 && Object.entries(value).every(([k, v]) => k.length <= 256 && bounded(v, depth + 1));
}
function matches(template, value) {
  if (typeof template === "string") {
    if (/^\{\w+\}$/.test(template)) return bounded(value);
    return stringTemplate(template, value);
  }
  if (Array.isArray(template)) return Array.isArray(value) && value.length === template.length && template.every((v, i) => matches(v, value[i]));
  if (object(template)) return object(value) && Object.keys(template).length === Object.keys(value).length
    && Object.keys(template).every((key) => Object.hasOwn(value, key) && matches(template[key], value[key]));
  return template === value;
}
const pythonString = (v) => v === true ? "True" : v === false ? "False" : String(v);
function matchesFields(template, fields) {
  if ([...fields.keys()].some((k) => !Object.hasOwn(template, k))) return false;
  return Object.entries(template).every(([key, t]) => {
    const values = fields.getAll(key);
    if (t === null) return values.length === 0;
    if (Array.isArray(t)) return values.length === t.length && t.every((v, i) => stringTemplate(pythonString(v), values[i]));
    // A whole placeholder can be a typed list, encoded with doseq=True by Python.
    if (typeof t === "string" && /^\{\w+\}$/.test(t)) return values.length <= 512 && values.every((v) => stringTemplate(t, v));
    return values.length === 1 && stringTemplate(pythonString(t), values[0]);
  });
}

// Only public addresses and literal DNS names from registered bases. No local IP,
// link-local metadata endpoint, credentials or nonstandard port can be requested.
function publicIP(host) {
  if (!/^\d+\.\d+\.\d+\.\d+$/.test(host)) return false;
  const [a, b, c] = host.split(".").map(Number);
  return a !== 0 && a !== 10 && a !== 127 && a < 224 && !(a === 100 && b >= 64 && b <= 127)
    && !(a === 169 && b === 254) && !(a === 172 && b >= 16 && b <= 31)
    && !(a === 192 && b === 168) && !(a === 192 && b === 0 && [0, 2].includes(c))
    && !(a === 192 && b === 88 && c === 99) && !(a === 198 && [18, 19].includes(b))
    && !(a === 198 && b === 51 && c === 100) && !(a === 203 && b === 0 && c === 113);
}
function safeURL(raw) {
  if (typeof raw !== "string" || raw.length > MAX_URL || /[\x00-\x20\x7f\\]/.test(raw)) return null;
  let url;
  try { url = new URL(raw); } catch { return null; }
  if (!["https:", "http:"].includes(url.protocol) || url.username || url.password || url.hash || url.port) return null;
  if (!(publicIP(url.hostname) || /^(?:[a-z0-9-]+\.)+[a-z][a-z0-9-]*$/i.test(url.hostname))
      || /(?:^|\.)(?:localhost|local|internal|test|invalid)$/.test(url.hostname)) return null;
  // URL() normalizes literal/encoded dot segments; inspect the original too. DOI paths
  // may contain slashes, but a component argument must never escape an API base.
  const originalPath = raw.replace(/^https?:\/\/[^/]+/i, "").split("?")[0];
  let decoded;
  try { decoded = decodeURIComponent(originalPath); } catch { return null; }
  if (decoded.includes("\\") || /(?:^|\/)\.{1,2}(?:\/|$)/.test(decoded) || /%2e|%2f|%5c/i.test(decoded)) return null;
  return url;
}

export function compileRegistry(document) {
  if (!object(document) || document.version !== 1 || !Array.isArray(document.sources) || document.sources.length > 2000) throw new Error("invalid source registry");
  const routes = [];
  for (const source of document.sources) {
    const base = safeURL(source.base_url);
    if (!base || base.search || source.host !== base.hostname || !Array.isArray(source.operations) || source.operations.length > 500) throw new Error("invalid source definition");
    for (const op of source.operations) {
      if (!object(op) || typeof op.path !== "string" || !["GET", "POST"].includes(op.method || "GET")) throw new Error("invalid source operation");
      const [operationPath, inlineQuery] = op.path.split("?");
      if (inlineQuery !== undefined && Object.keys(op.params || {}).length) throw new Error("mixed inline query and params are unsupported");
      const path = `${base.pathname.replace(/\/$/, "")}/${operationPath.replace(/^\//, "")}` || "/";
      // GraphQL query text is a fixed template, never caller-controlled; don't include mutations.
      if (op.graphql !== undefined && (typeof op.graphql !== "string" || !/^(?:\s|#[^\n]*\n)*(?:query\b|\{)/.test(op.graphql))) throw new Error("invalid read-only GraphQL operation");
      routes.push({ source, op, origin: base.origin, path: op.path ? path : base.pathname || "/", inlineQuery, method: op.method || "GET" });
    }
  }
  if (!routes.length) throw new Error("empty source registry");
  return { routes, sources: document.sources.length };
}

export function matchRequest(registry, url, method, body, headers, redirect = false) {
  let jsonBody;
  let formBody;
  for (const route of registry.routes) {
    if (route.origin !== url.origin || route.method !== method || !stringTemplate(route.path, url.pathname)) continue;
    if (route.inlineQuery !== undefined ? !inlineQueryTemplate(route.inlineQuery, url.search.slice(1))
      : !matchesFields(route.op.params || {}, url.searchParams)) continue;
    const op = route.op;
    if (redirect && method === "GET") return route;
    if (op.graphql !== undefined || op.json_body !== undefined && op.json_body !== null) {
      if (!(headers.get("Content-Type") || "").toLowerCase().startsWith("application/json")) continue;
      try { jsonBody ??= JSON.parse(decoder.decode(body)); } catch { continue; }
      if (op.graphql !== undefined) {
        if (!object(jsonBody) || Object.keys(jsonBody).length !== 2 || jsonBody.query !== op.graphql
            || !matches(op.variables || {}, jsonBody.variables)) continue;
      } else if (!matches(op.json_body, jsonBody)) continue;
    } else if (op.form !== undefined && op.form !== null) {
      if (!(headers.get("Content-Type") || "").toLowerCase().startsWith("application/x-www-form-urlencoded")) continue;
      try { formBody ??= new URLSearchParams(decoder.decode(body)); } catch { continue; }
      if (!matchesFields(op.form, formBody)) continue;
    } else if (body.byteLength) continue;
    return route;
  }
  return null;
}

async function registryFor(request, env, deps) {
  if (deps.manifest) return compileRegistry(await deps.manifest());
  if (!env.ASSETS) throw new Error("source registry asset unavailable");
  let promise = caches.get(env.ASSETS);
  if (!promise) {
    promise = (async () => {
      const res = await env.ASSETS.fetch(new Request(new URL("/runtime/source-gateway.json", request.url)));
      if (!res.ok || !(res.headers.get("Content-Type") || "").includes("json")) throw new Error("source registry asset unavailable");
      const raw = await readLimited(res, 4194304);
      return compileRegistry(JSON.parse(decoder.decode(raw)));
    })();
    caches.set(env.ASSETS, promise);
    promise.catch(() => caches.delete(env.ASSETS));
  }
  return promise;
}

function bytesFromBase64(value, max) {
  if (value === undefined || value === null || value === "") return new Uint8Array();
  if (typeof value !== "string" || value.length > Math.ceil(max / 3) * 4 || !/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(value)) refuse(400, "invalid_body", "请求正文不是有效的 base64");
  const raw = atob(value);
  if (raw.length > max) refuse(413, "too_large", "数据库查询正文过大");
  return Uint8Array.from(raw, (c) => c.charCodeAt(0));
}
function base64(bytes) {
  const parts = [];
  for (let at = 0; at < bytes.length; at += 8192) parts.push(String.fromCharCode(...bytes.subarray(at, at + 8192)));
  return btoa(parts.join(""));
}
async function readLimited(response, max, signal) {
  const length = response.headers.get("Content-Length");
  if (length !== null && Number(length) > max) {
    await response.body?.cancel().catch(() => {});
    refuse(413, "too_large", "数据超过浏览器网关的大小上限，请缩小查询范围或导入数据文件");
  }
  if (!response.body) return new Uint8Array();
  const reader = response.body.getReader();
  const parts = [];
  let size = 0;
  const aborted = () => { reader.cancel().catch(() => {}); };
  if (signal?.aborted) aborted();
  signal?.addEventListener("abort", aborted, { once: true });
  try {
    for (;;) {
      if (signal?.aborted) throw signal.reason;
      const { done, value } = await reader.read();
      if (signal?.aborted) throw signal.reason;
      if (done) break;
      size += value.byteLength;
      if (size > max) { await reader.cancel().catch(() => {}); refuse(413, "too_large", "数据超过浏览器网关的大小上限，请缩小查询范围或导入数据文件"); }
      parts.push(value);
    }
  } finally { signal?.removeEventListener("abort", aborted); reader.releaseLock(); }
  const bytes = new Uint8Array(size);
  let at = 0;
  for (const part of parts) { bytes.set(part, at); at += part.byteLength; }
  return bytes;
}

async function caller(request, env, now) {
  const raw = `${env.VISITOR_SALT || ""}|${ipKey(request.headers.get("CF-Connecting-IP") || "unknown")}|${day(now)}`;
  const bytes = new Uint8Array(await crypto.subtle.digest("SHA-256", encoder.encode(raw)));
  return [...bytes].map((b) => b.toString(16).padStart(2, "0")).join("").slice(0, 24);
}
function wait(ms, signal) {
  if (!ms) return Promise.resolve();
  return new Promise((resolve, reject) => {
    if (signal.aborted) { reject(signal.reason); return; }
    const abort = () => { clearTimeout(timer); reject(signal.reason); };
    const timer = setTimeout(() => { signal.removeEventListener("abort", abort); resolve(); }, ms);
    signal.addEventListener("abort", abort, { once: true });
  });
}

export async function handleSources(request, env = {}, deps = {}) {
  const cfg = sourceConfig(env);
  const url = new URL(request.url);
  const origin = request.headers.get("Origin") || "";
  const allowed = origin !== "" && cfg.origins.includes(origin);
  const cors = allowed ? { "Access-Control-Allow-Origin": origin, "Access-Control-Expose-Headers": "Retry-After", Vary: "Origin" } : { Vary: "Origin" };
  let timer;
  const controller = new AbortController();
  const abort = () => controller.abort(new Refused(499, "cancelled", "数据库查询已取消"));
  request.signal.addEventListener("abort", abort, { once: true });
  if (request.signal.aborted) abort();
  try {
    if (controller.signal.aborted) throw controller.signal.reason;
    if (url.protocol !== "https:" && !LOCAL.has(url.hostname)) refuse(403, "https_required", "请使用 HTTPS 访问数据库网关");
    if (request.method === "OPTIONS") {
      if (!allowed) refuse(403, "forbidden_origin", "数据库网关只供获准的 Studio 网页使用");
      return new Response(null, { status: 204, headers: { ...cors, "Access-Control-Allow-Methods": "GET, POST, OPTIONS", "Access-Control-Allow-Headers": "Content-Type", "Access-Control-Max-Age": "86400" } });
    }
    if (!["/api/sources/health", "/api/sources/request"].includes(url.pathname)) refuse(404, "not_found", "没有这个数据库网关接口");
    if (request.method !== (url.pathname.endsWith("/health") ? "GET" : "POST")) refuse(405, "method_not_allowed", "数据库网关不接受这种请求方法");
    if (!cfg.enabled) refuse(503, "not_configured", "数据库网关已暂停，请稍后重试或导入数据文件");
    let registry;
    try { registry = await registryFor(request, env, deps); } catch { refuse(503, "not_configured", "数据库注册清单不可用，请重新部署完整网页和网关"); }
    if (url.pathname.endsWith("/health")) return json({ ok: true, service: "tcmscience-sources", version: 1, sources: registry.sources, operations: registry.routes.length, max_response_bytes: cfg.maxResponse, timeout_ms: cfg.timeout, limits: cfg.limits }, 200, cors);
    if (!allowed) refuse(403, "forbidden_origin", "数据库网关只供获准的 Studio 网页使用");
    if (request.headers.get("Sec-Fetch-Site") === "cross-site" && origin === url.origin) refuse(403, "forbidden_origin", "数据库网关拒绝跨站请求");
    if (!(request.headers.get("Content-Type") || "").toLowerCase().startsWith("application/json")) refuse(415, "invalid_body", "请使用 JSON 数据库查询请求");
    if (deps.burst && !await deps.burst(`sources:${ipKey(request.headers.get("CF-Connecting-IP") || "unknown")}`)) refuse(429, "rate_limited", "数据库查询过于频繁，请稍后重试");
    timer = setTimeout(() => controller.abort(new Refused(504, "timeout", "数据库查询超时，请缩小查询范围或稍后重试")), cfg.timeout);
    let packet;
    try { packet = JSON.parse(decoder.decode(await readLimited(request, cfg.maxBody, controller.signal))); } catch (err) {
      if (err instanceof Refused) throw err;
      refuse(400, "invalid_body", "数据库查询请求不是有效的 JSON");
    }
    if (!object(packet) || !Array.isArray(packet.allowed_hosts) || !packet.allowed_hosts.length || packet.allowed_hosts.length > 16
        || !packet.allowed_hosts.every((h) => typeof h === "string" && h.length <= 253)) refuse(400, "invalid_body", "缺少本次查询获准访问的数据库主机");
    if (Number.isFinite(packet.timeout_s) && packet.timeout_s > 0) {
      clearTimeout(timer);
      timer = setTimeout(() => controller.abort(new Refused(504, "timeout", "数据库查询超时，请缩小查询范围或稍后重试")), Math.min(cfg.timeout, Math.max(1000, packet.timeout_s * 1000)));
    }
    let target = safeURL(packet.url);
    const allowedHosts = new Set(packet.allowed_hosts.map((h) => h.toLowerCase()));
    if (!target || !allowedHosts.has(target.hostname)) refuse(403, "source_not_allowed", "查询地址不在本次获准的数据库主机内");
    let method = packet.method || "GET";
    if (!["GET", "POST"].includes(method)) refuse(403, "source_not_allowed", "只允许已注册的只读数据库查询");
    let body = bytesFromBase64(packet.body_base64, Math.min(262144, Math.floor(cfg.maxBody * 0.7)));
    const headers = new Headers();
    if (!object(packet.headers || {}) || Object.keys(packet.headers || {}).length > 32) refuse(400, "invalid_body", "数据库请求头无效");
    for (const [key, value] of Object.entries(packet.headers || {})) {
      if (typeof value !== "string" || value.length > MAX_VALUE || /[\r\n\x00]/.test(value)) refuse(400, "invalid_body", "数据库请求头无效");
      if (FORWARD_HEADERS.has(key.toLowerCase())) headers.set(key, value);
    }
    headers.set("User-Agent", "bioagent-harness/studio (+https://github.com/psknlr/TCMScience; research use)");
    let route = matchRequest(registry, target, method, body, headers);
    if (!route) refuse(403, "source_not_allowed", "查询不匹配已注册的数据库操作模板");
    const initialScheme = target.protocol;
    if (initialScheme === "http:") { headers.delete("Authorization"); headers.delete("X-API-Key"); headers.delete("Api-Key"); }
    let gate;
    try { gate = await deps.gate({ who: await caller(request, env, (deps.now || Date.now)()), kind: "sources", limits: cfg.limits, reserve: 0 }); }
    catch { refuse(503, "unavailable", "数据库网关限流服务暂时不可用，请稍后重试"); }
    if (!gate?.ok) return json({ error: { type: "rate_limited", message: "数据库网关额度暂时用完，请稍后重试" } }, 429, { ...cors, "Retry-After": String(Math.max(1, gate?.retry || 60)) });
    for (let hop = 0; hop <= 3; hop++) {
      let slot;
      const prefixes = Object.fromEntries(Object.entries(route.source.rate_paths || {})
        .filter(([prefix, value]) => target.pathname.startsWith(prefix) && Number(value) > 0));
      try { slot = await deps.pace(target.hostname, Number(route.source.rate_rps) || 2, prefixes); }
      catch { refuse(503, "unavailable", "数据库网关限流服务暂时不可用，请稍后重试"); }
      if (!slot?.ok) return json({ error: { type: "rate_limited", message: "数据库服务查询队列已满，请稍后重试" } }, 429, { ...cors, "Retry-After": String(Math.max(1, slot?.retry || 10)) });
      await (deps.wait || wait)(slot.delay || 0, controller.signal);
      if (controller.signal.aborted) throw controller.signal.reason;
      const response = await (deps.fetch || fetch)(target.href, { method, headers, body: method === "POST" && body.byteLength ? body : undefined, redirect: "manual", signal: controller.signal });
      if ([301, 302, 303, 307, 308].includes(response.status) && response.headers.has("Location")) {
        const location = response.headers.get("Location");
        await response.body?.cancel().catch(() => {});
        if (hop === 3) refuse(502, "redirect_refused", "数据库重定向次数过多");
        let next;
        try { next = safeURL(new URL(location, target).href); } catch { /* rejected below */ }
        if (!next || !allowedHosts.has(next.hostname) || initialScheme === "https:" && next.protocol !== "https:") refuse(403, "redirect_refused", "数据库重定向超出本次获准的主机范围或降低了传输安全性");
        if (next.origin !== target.origin) { headers.delete("Authorization"); headers.delete("X-API-Key"); headers.delete("Api-Key"); }
        if (response.status === 303 || (method === "POST" && [301, 302].includes(response.status))) { method = "GET"; body = new Uint8Array(); headers.delete("Content-Type"); }
        route = matchRequest(registry, next, method, body, headers, true);
        if (!route) refuse(403, "redirect_refused", "数据库重定向不匹配已注册的查询模板");
        target = next;
        continue;
      }
      const bytes = await readLimited(response, cfg.maxResponse, controller.signal);
      const responseHeaders = {};
      for (const key of REPLY_HEADERS) if (response.headers.has(key)) responseHeaders[key] = response.headers.get(key);
      return json({ status: response.status, headers: responseHeaders, body_base64: base64(bytes), url: target.href, transport: target.protocol.slice(0, -1) }, 200, cors);
    }
  } catch (err) {
    if (controller.signal.aborted) err = controller.signal.reason;
    const known = err instanceof Refused;
    return json({ error: { type: known ? err.type : "upstream_unreachable", message: known ? err.message : "数据库服务暂时无法连接，请稍后重试" } }, known ? err.status : 502, { ...cors, ...(err?.status === 429 ? { "Retry-After": "10" } : {}) });
  } finally { clearTimeout(timer); request.signal.removeEventListener("abort", abort); }
}
