// The dataset relay: GET|HEAD /api/datasets/files/<group>/<name> streams one raw file that the build-generated registry
// (runtime/datasets.json, schema tcmstudio.datasets/1, tcmstudio.datasets.registry_document()) names, from its reviewed
// URL, for a browser that cannot download it from the host itself (the host sends no CORS headers, or the network
// blocks it). The registry is the allow-list: a request names a file, never a URL, and a redirect may only go to the
// file's own host or a host the registry lists for it. The bytes pass through as they arrive, never buffered and never
// stored here (the upstream fetch asks Cloudflare's cache not to keep them either: cache "no-store"); the browser checks
// them against the registry's size and checksum.
//   Request policy: same-origin pages only (Sec-Fetch-Site, when the browser sends it, and an Origin, when there is one,
//   from ALLOWED_ORIGINS); only Range and If-Range are passed on, checked, with a fixed User-Agent and no cookies or
//   credentials. Limits: the BURST binding (key datasets:<ip>), then the Limiter Durable Object's per-visitor counters
//   (kind "datasets", with the gateway's SOURCE_PER_* numbers) and its per-host pacing, as the source gateway uses them.
//   SOURCES = "off" pauses this relay as well as the gateway: both carry third-party data under the same switch.
import { callerKey, clientIp, LOCAL, readText } from "./http.js";
import { ipKey } from "./relay.js";
import { sourceConfig } from "./sources.js";

export const REGISTRY_PATH = "/runtime/datasets.json";
export const REGISTRY_SCHEMA = "tcmstudio.datasets/1";
// a file the registry has no size for may be at most this large: the browser's own limit for such a file
// (studio/web/js/scientific/data/data.worker.js MAX_UNKNOWN_BYTES)
export const MAX_UNSIZED_BYTES = 2 * 1024 ** 3;
export const MAX_HOPS = 3;
export const USER_AGENT = "bioagent-harness/studio-datasets (+https://github.com/psknlr/TCMScience; research use)";
// one request a second to a host, across every visitor, unless the registry gives the file a rate of its own
// (rate_rps): bioagent's acquisition paces a host with no declared rate at 1 a second too (acquisition/downloader.py
// Pacer), but slower where a host asks for it (zenodo.org, 0.1: its robots.txt Crawl-delay), and the registry is where
// that rate reaches the relay
const HOST_RPS = 1;
const MAX_RPS = 20; // the Limiter's own ceiling (limiter.js sourceSlot)
const MAX_REGISTRY_BYTES = 4194304;
const COPY_HEADERS = ["Content-Type", "Content-Length", "Content-Range", "Accept-Ranges", "ETag", "Last-Modified"];
// the content codings fetch() decodes itself: Node's fetch does (keeping Content-Encoding and the encoded
// Content-Length), and so does the Workers runtime by its documentation (unless a request asks for
// encodeResponseBody "manual"), which has not been observed from here. The body that arrives is then the file's own
// bytes, and the upstream's Content-Length (of the encoded body) is not its length. The relay asks for identity; no
// reviewed host has been seen to encode anyway.
const DECODED = new Set(["gzip", "x-gzip", "br"]);
const PREFIX = "/api/datasets/files/";
const registries = new WeakMap(); // env.ASSETS → the compiled registry (a promise), once per isolate

class Refused extends Error {
  constructor(status, type, message, extra = {}) {
    super(message);
    this.status = status;
    this.type = type;
    this.extra = extra;
  }
}
const refuse = (status, type, message, extra) => { throw new Refused(status, type, message, extra); };
const object = (value) => value !== null && typeof value === "object" && !Array.isArray(value);

// ------------------------------------------------------------------ the registry

/** A DNS name, lower case, that is not a local, internal or test name. IP literals are not accepted: every reviewed
 * download host is a name, and a name keeps the private-address question out of the registry. */
function hostName(value) {
  return typeof value === "string" && value.length <= 253
    && /^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{0,62}$/.test(value)
    && !/(?:^|\.)(?:localhost|local|internal|test|invalid)$/.test(value);
}

/** An https URL to a named host on the default port, without credentials or a fragment; else null. */
function httpsURL(raw) {
  if (typeof raw !== "string" || raw.length > 4096 || /[\x00-\x20\x7f\\]/.test(raw)) return null;
  let url;
  try { url = new URL(raw); } catch { return null; }
  if (url.protocol !== "https:" || url.username || url.password || url.port || url.hash || !hostName(url.hostname)) return null;
  return url;
}

/** A file name as the registry gives it: one path segment. */
const fileName = (value) => typeof value === "string" && value.length >= 1 && value.length <= 255
  && !/[/\\\x00-\x1f\x7f]/.test(value) && value !== "." && value !== "..";

/**
 * runtime/datasets.json → {groups: Map(group → Map(name → {name, url, host, hosts, bytes, rps, relay})), files}. Throws
 * on anything malformed: the document is generated by the build, so a bad entry is a build defect, and the relay then
 * serves nothing (503) rather than guess which entries to trust. rate_rps (optional; HOST_RPS when absent) is the pace
 * of requests to the file's own host.
 */
export function compileDatasets(document) {
  if (!object(document) || document.schema !== REGISTRY_SCHEMA || !object(document.groups)) throw new Error("invalid dataset registry");
  const groups = new Map();
  let count = 0;
  for (const [key, group] of Object.entries(document.groups)) {
    if (!/^[a-z0-9][a-z0-9_-]{0,63}$/.test(key) || !object(group) || !Array.isArray(group.files) || group.files.length > 2000) {
      throw new Error(`invalid dataset group ${JSON.stringify(key)}`);
    }
    const files = new Map();
    for (const f of group.files) {
      if (!object(f) || !fileName(f.name) || files.has(f.name)) throw new Error(`invalid dataset file in ${key}`);
      const url = httpsURL(f.url);
      if (!url || f.host !== url.hostname) throw new Error(`invalid dataset URL for ${f.name}`);
      const redirect = f.redirect_hosts ?? [];
      if (!Array.isArray(redirect) || redirect.length > 16 || !redirect.every(hostName)) throw new Error(`invalid redirect hosts for ${f.name}`);
      const bytes = f.bytes ?? null;
      if (bytes !== null && !(Number.isSafeInteger(bytes) && bytes >= 0)) throw new Error(`invalid size for ${f.name}`);
      const rps = f.rate_rps ?? HOST_RPS;
      if (!(typeof rps === "number" && Number.isFinite(rps) && rps > 0)) throw new Error(`invalid rate for ${f.name}`);
      files.set(f.name, {
        name: f.name, url: url.href, host: url.hostname, hosts: new Set([url.hostname, ...redirect]), bytes,
        rps: Math.min(rps, MAX_RPS), relay: f.relay === true,
      });
    }
    groups.set(key, files);
    count += files.size;
  }
  if (!count) throw new Error("empty dataset registry");
  return { groups, files: count };
}

/** The deployed registry, read from the site's own files once per isolate (a failure is not kept: the next request
 * reads it again). */
async function registryFor(request, env) {
  if (!env.ASSETS) throw new Error("dataset registry asset unavailable");
  let promise = registries.get(env.ASSETS);
  if (!promise) {
    promise = (async () => {
      const res = await env.ASSETS.fetch(new Request(new URL(REGISTRY_PATH, request.url)));
      if (!res.ok || !(res.headers.get("Content-Type") || "").includes("json")) {
        await res.body?.cancel().catch(() => {});
        throw new Error("dataset registry asset unavailable");
      }
      const text = await readText(res, MAX_REGISTRY_BYTES);
      if (text === null) throw new Error("dataset registry too large");
      return compileDatasets(JSON.parse(text));
    })();
    registries.set(env.ASSETS, promise);
    promise.catch(() => registries.delete(env.ASSETS));
  }
  return promise;
}

// ------------------------------------------------------------------ the request

/** "bytes=N-" or "bytes=N-M" → {start, end|null}; null when absent; false for anything else (several ranges, a suffix
 * range, other units): the browser's resume asks for exactly one range from an offset, nothing else is passed on. */
export function rangeOf(value) {
  if (value === null) return null;
  const m = /^bytes=(\d{1,16})-(\d{0,16})$/.exec(value);
  if (!m) return false;
  const start = Number(m[1]);
  const end = m[2] === "" ? null : Number(m[2]);
  if (!Number.isSafeInteger(start) || (end !== null && (!Number.isSafeInteger(end) || end < start))) return false;
  return { start, end };
}

/** If-Range: a strong entity tag (quoted, or the bare token some servers send) or an HTTP date; a weak tag is not a
 * validator If-Range may carry (RFC 9110 §13.1.5). */
export function ifRangeOk(value) {
  if (typeof value !== "string" || !value || value.length > 256) return false;
  if (/^"[\x21\x23-\x7e]*"$/.test(value) || /^[\x21\x23-\x7e]+$/.test(value)) return !/^W\//i.test(value);
  return /^[A-Za-z0-9 ,:-]{1,64}$/.test(value) && /[A-Za-z]/.test(value) && !Number.isNaN(Date.parse(value));
}

/** "bytes 100-199/1000" → {start, end, total|null ("*")}; null for anything else. */
function contentRange(value) {
  const m = /^\s*bytes\s+(\d{1,16})-(\d{1,16})\/(\d{1,16}|\*)\s*$/i.exec(value || "");
  if (!m) return null;
  const [start, end] = [Number(m[1]), Number(m[2])];
  const total = m[3] === "*" ? null : Number(m[3]);
  if (end < start || (total !== null && end >= total)) return null;
  return { start, end, total };
}

const lengthOf = (value) => (/^\s*\d{1,16}\s*$/.test(value || "") ? Number(value) : null);

function wait(ms, signal) {
  if (!ms) return Promise.resolve();
  return new Promise((resolve, reject) => {
    if (signal.aborted) { reject(signal.reason); return; }
    const abort = () => { clearTimeout(timer); reject(signal.reason); };
    const timer = setTimeout(() => { signal.removeEventListener("abort", abort); resolve(); }, ms);
    signal.addEventListener("abort", abort, { once: true });
  });
}

/** The body, counted as it passes: past `max` bytes the stream is errored (the browser sees a failed download, never a
 * file cut short that looks whole) and the upstream body is cancelled. Nothing is held beyond the chunk in hand. */
export function capped(body, max) {
  let seen = 0;
  return body.pipeThrough(new TransformStream({
    transform(chunk, controller) {
      seen += chunk.byteLength;
      if (seen > max) controller.error(new Error(`the dataset body is larger than ${max} bytes`));
      else controller.enqueue(chunk);
    },
  }));
}

/** The headers every answer of the relay carries: nothing cached (here, in the browser or between), not readable by
 * another site's page, and no type sniffing. */
const BASE = {
  "Cache-Control": "no-store",
  "Cross-Origin-Resource-Policy": "same-origin",
  "X-Content-Type-Options": "nosniff",
};

function reply(request, value, status, headers = {}) {
  const res = new Response(request.method === "HEAD" ? null : JSON.stringify(value), {
    status, headers: { ...headers, ...BASE, "Content-Type": "application/json; charset=utf-8" },
  });
  res.datasetType = value?.error?.type || ""; // for the security events below; never sent
  return res;
}

/**
 * The relay. deps (src/index.js relayDeps): fetch, burst(key), gate({who, kind, limits, reserve}), pace(host, rps,
 * prefixes), securityEvent(kind, request, info), wait(ms, signal) (tests), now().
 * Refusals are JSON {error: {type, message}} (no body to HEAD); an upstream answer other than 200/206 is told with the
 * upstream's status (a 4xx/5xx kept as it is, anything else 502) and error.upstream_status.
 */
export async function handleDatasets(request, env = {}, deps = {}) {
  const res = await serveDatasets(request, env, deps);
  try {
    // the visitor's own doing: another site's page, or too many requests (a host's answer is not an event)
    if (["forbidden_origin", "rate_limited"].includes(res.datasetType)) {
      deps.securityEvent?.(res.datasetType, request, { route: "datasets", status: res.status, detail: res.datasetType });
    }
  } catch { /* an event is never the visitor's concern */ }
  return res;
}

async function serveDatasets(request, env, deps) {
  const cfg = sourceConfig(env);
  const url = new URL(request.url);
  const method = request.method;
  let timer;
  const controller = new AbortController();
  const abort = () => controller.abort(new Refused(499, "cancelled", "数据集下载已取消"));
  request.signal?.addEventListener("abort", abort, { once: true });
  if (request.signal?.aborted) abort();
  try {
    if (controller.signal.aborted) throw controller.signal.reason;
    if (url.protocol !== "https:" && !LOCAL.has(url.hostname)) refuse(403, "https_required", "请使用 HTTPS 访问数据集中转");
    const parts = url.pathname.startsWith(PREFIX) ? url.pathname.slice(PREFIX.length).split("/") : [];
    if (parts.length !== 2 || !parts[0] || !parts[1]) refuse(404, "not_found", "没有这个数据集中转接口");
    if (method !== "GET" && method !== "HEAD") refuse(405, "method_not_allowed", "数据集中转只接受 GET 和 HEAD", { allow: true });
    // a same-origin page only: a browser says where a request comes from (Fetch Metadata), and a page of another site
    // cannot hide it; a client that is not a browser sends neither header and is held by the limits below
    const site = request.headers.get("Sec-Fetch-Site");
    const origin = request.headers.get("Origin");
    if (site !== null && site !== "same-origin") refuse(403, "forbidden_origin", "数据集中转只供本站网页使用");
    if (origin !== null && !cfg.origins.includes(origin)) refuse(403, "forbidden_origin", "数据集中转只供获准的 Studio 网页使用");
    if (!cfg.enabled) refuse(503, "not_configured", "数据集中转已暂停，请直接从数据库网站下载或导入数据文件");
    const range = rangeOf(request.headers.get("Range"));
    const ifRange = request.headers.get("If-Range");
    if (range === false) refuse(400, "invalid_range", "Range 只能是一个 bytes=起点- 或 bytes=起点-终点 的范围");
    if (ifRange !== null && (!range || !ifRangeOk(ifRange))) refuse(400, "invalid_range", "If-Range 需要与 Range 一起发送，且只能是强 ETag 或 HTTP 日期");
    if (deps.burst && !await deps.burst(`datasets:${ipKey(clientIp(request))}`)) refuse(429, "rate_limited", "数据集请求过于频繁，请稍后重试");
    let registry;
    try { registry = await registryFor(request, env); } catch { refuse(503, "not_configured", "数据集注册清单不可用，请重新部署完整网页和中转"); }
    let group;
    let name;
    try { [group, name] = parts.map(decodeURIComponent); } catch { refuse(404, "not_found", "数据集注册清单中没有这个文件"); }
    const entry = registry.groups.get(group)?.get(name);
    if (!entry) refuse(404, "not_found", "数据集注册清单中没有这个文件");
    if (!entry.relay) refuse(404, "not_relayed", "注册清单不允许本站中转这个文件，请直接从数据库网站下载");
    let gate;
    try { gate = await deps.gate({ who: await callerKey(request, env, (deps.now || Date.now)()), kind: "datasets", limits: cfg.limits, reserve: 0 }); }
    catch { refuse(503, "unavailable", "数据集中转限流服务暂时不可用，请稍后重试"); }
    if (!gate?.ok) refuse(429, "rate_limited", "数据集中转额度暂时用完，请稍后重试", { retry: gate?.retry || 60 });

    // the upstream's headers must arrive within the gateway's deadline; the body then takes as long as it takes (the
    // browser can stop it, and a stopped download stops the upstream's)
    timer = setTimeout(() => controller.abort(new Refused(504, "timeout", "数据集服务器响应超时，请稍后重试")), cfg.timeout);
    const headers = new Headers({ "User-Agent": USER_AGENT, "Accept-Encoding": "identity" });
    if (range) headers.set("Range", request.headers.get("Range"));
    if (ifRange !== null) headers.set("If-Range", ifRange);
    let target = new URL(entry.url);
    let response;
    for (let hop = 0; ; hop++) {
      let slot;
      // the file's rate is its own host's; a host it is redirected to keeps the default
      const rps = target.hostname === entry.host ? entry.rps : HOST_RPS;
      try { slot = await deps.pace(target.hostname, rps, {}); } catch { refuse(503, "unavailable", "数据集中转限流服务暂时不可用，请稍后重试"); }
      if (!slot?.ok) refuse(429, "rate_limited", "数据集服务器的下载队列已满，请稍后重试", { retry: slot?.retry || 10 });
      await (deps.wait || wait)(slot.delay || 0, controller.signal);
      if (controller.signal.aborted) throw controller.signal.reason;
      // cache "no-store": not kept in Cloudflare's cache on the way either (a cached copy would be a stored one, and its
      // answer to a Range would not be the host's)
      response = await (deps.fetch || fetch)(target.href, { method, headers, redirect: "manual", cache: "no-store", signal: controller.signal });
      if (![301, 302, 303, 307, 308].includes(response.status) || !response.headers.has("Location")) break;
      await response.body?.cancel().catch(() => {});
      if (hop === MAX_HOPS) refuse(502, "redirect_refused", `数据集服务器重定向超过 ${MAX_HOPS} 次`);
      let next = null;
      try { next = httpsURL(new URL(response.headers.get("Location"), target).href.replace(/#.*$/, "")); } catch { /* refused below */ }
      if (!next || !entry.hosts.has(next.hostname)) {
        refuse(502, "redirect_refused", `数据集服务器重定向到注册清单未列出的地址（只允许 HTTPS 到 ${[...entry.hosts].join("、")}）`);
      }
      target = next;
    }
    clearTimeout(timer);
    return relayed(request, response, entry, range, target);
  } catch (err) {
    if (controller.signal.aborted) err = controller.signal.reason;
    if (!(err instanceof Refused)) err = new Refused(502, "upstream_unreachable", "数据集服务器暂时无法连接，请稍后重试");
    const headers = {};
    if (err.extra.allow) headers.Allow = "GET, HEAD";
    if (err.status === 429) headers["Retry-After"] = String(Math.max(1, Math.ceil(err.extra.retry || 10)));
    return reply(request, { error: { type: err.type, message: err.message } }, err.status, headers);
  } finally {
    clearTimeout(timer);
    request.signal?.removeEventListener("abort", abort);
  }
}

/** The upstream's final answer, checked against the registry before any byte is sent, then passed through. */
async function relayed(request, response, entry, range, target) {
  const status = response.status;
  const h = response.headers;
  const upstream = target.hostname;
  const drop = () => response.body?.cancel().catch(() => {});
  if (status !== 200 && status !== 206) {
    await drop();
    // the upstream's status, so the browser's resume logic reads it as the host's (416 with "bytes */total" included)
    const headers = {};
    const retry = h.get("Retry-After");
    if ((status === 429 || status === 503) && retry && /^\d{1,6}$/.test(retry.trim())) headers["Retry-After"] = retry.trim();
    if (status === 416 && /^\s*bytes\s+\*\/\d{1,16}\s*$/i.test(h.get("Content-Range") || "")) headers["Content-Range"] = h.get("Content-Range").trim();
    return reply(request, { error: {
      type: "upstream_status", upstream_status: status,
      message: `数据集服务器 ${upstream} 返回 HTTP ${status}`,
    } }, status >= 400 && status <= 599 ? status : 502, headers);
  }
  const coding = (h.get("Content-Encoding") || "").trim().toLowerCase();
  const encoded = coding !== "" && coding !== "identity";
  const fail = async (code, type, message) => {
    await drop();
    return reply(request, { error: { type, message } }, code);
  };
  if (encoded && !DECODED.has(coding)) return fail(502, "encoded_response", `数据集服务器 ${upstream} 以 ${coding} 编码发送文件，中转无法还原原始字节`);
  const cap = entry.bytes ?? MAX_UNSIZED_BYTES;
  // the most bytes the body may carry: the whole file's cap for a 200, the part's own length for a 206
  let limit = cap;
  if (status === 206) {
    if (!range) return fail(502, "unexpected_partial", `数据集服务器 ${upstream} 对整个文件的请求返回了部分内容（HTTP 206）`);
    // a range of the encoded body cannot be joined to the file's bytes
    if (encoded) return fail(502, "encoded_response", `数据集服务器 ${upstream} 以 ${coding} 编码发送部分内容，无法续传`);
    const cr = contentRange(h.get("Content-Range"));
    if (!cr || cr.start !== range.start || (range.end !== null && cr.end > range.end)) {
      return fail(502, "bad_range", `数据集服务器 ${upstream} 返回的范围（${h.get("Content-Range") || "无 Content-Range"}）不是请求的范围`);
    }
    if (entry.bytes !== null && cr.total !== entry.bytes) {
      return fail(413, "size_mismatch", `数据集服务器 ${upstream} 说文件共 ${cr.total ?? "未知"} 字节，与注册清单的 ${entry.bytes} 字节不符`);
    }
    if (cr.total !== null && cr.total > cap) return fail(413, "too_large", `数据集服务器 ${upstream} 说文件共 ${cr.total} 字节，超过 ${cap} 字节的上限`);
    limit = cr.end - cr.start + 1;
  }
  // an encoded body's Content-Length counts the encoded bytes, not the file's: it is neither checked nor passed on
  const declared = encoded ? null : lengthOf(h.get("Content-Length"));
  if (status === 206 && declared !== null && declared !== limit) {
    return fail(502, "bad_range", `数据集服务器 ${upstream} 的 Content-Length（${declared}）不是其 Content-Range（${h.get("Content-Range")}）的长度`);
  }
  if (status === 200 && declared !== null && declared > cap) {
    return fail(413, "too_large", entry.bytes !== null
      ? `数据集服务器 ${upstream} 说文件有 ${declared} 字节，超过注册清单的 ${entry.bytes} 字节`
      : `数据集服务器 ${upstream} 说文件有 ${declared} 字节，超过未登记大小文件 ${cap} 字节的上限`);
  }
  const out = new Headers();
  for (const key of COPY_HEADERS) {
    if (key === "Content-Length" && declared === null) continue;
    const value = h.get(key);
    if (value !== null) out.set(key, value);
  }
  for (const [k, v] of Object.entries(BASE)) out.set(k, v);
  // a download, never a page of this site: a host's HTML (an error page sent as 200) cannot run here
  out.set("Content-Security-Policy", "default-src 'none'; sandbox");
  out.set("Content-Disposition", "attachment");
  out.set("X-Relay-Upstream", target.href);
  if (encoded) out.set("X-Relay-Decoded", coding);
  if (request.method === "HEAD") {
    await drop();
    return new Response(null, { status, headers: out });
  }
  // a body of known length passes through untouched; one of unknown length is counted on its way
  const body = response.body && declared === null ? capped(response.body, limit) : response.body;
  return new Response(body, { status, headers: out });
}
