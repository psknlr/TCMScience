// Server-sent events over a fetch body, and the error type both model clients throw.

import { t } from "../i18n.js";
import { abortError, isLoopbackUrl, isPrivateNetworkUrl } from "../util.js";

/** How long a model stream may send nothing at all before it counts as stalled. */
export const IDLE_MS = 90_000;
/**
 * A model server on the user's own computer, or on another machine of their network, can be silent for minutes while
 * it reads a long prompt on a CPU.
 */
export const LOCAL_IDLE_MS = 600_000;
/** The longest limit a timer keeps (2^31 − 1 ms, about 24.8 days): setTimeout fires a longer delay at once. */
export const MAX_IDLE_MS = 2 ** 31 - 1;

/**
 * The idle limit for a provider's streams, in ms: its own `idleMs` (a preset's, a custom endpoint's or the one set in
 * Settings; 0: none), else the default for where it runs.
 */
export function idleTimeout(provider = {}) {
  const own = idleMsOf(provider.idleMs);
  if (own !== null) return own;
  const url = provider.baseUrl || "";
  return provider.local || isLoopbackUrl(url) || isPrivateNetworkUrl(url) ? LOCAL_IDLE_MS : IDLE_MS;
}

/** An idle limit as given (a number or numeric text, in ms) → whole ms at most MAX_IDLE_MS, or null when it sets none. */
export function idleMsOf(v) {
  if (typeof v === "string" ? !v.trim() : typeof v !== "number") return null;
  const n = Number(v);
  return Number.isFinite(n) && n >= 0 ? Math.min(Math.round(n), MAX_IDLE_MS) : null;
}

/**
 * Parse an SSE byte stream into events {event, data, id}. Follows the WHATWG rules that matter for model APIs: lines
 * end in LF, CRLF or CR (a CRLF split across two chunks is one line end); several `data:` lines join with "\n"; a line
 * starting with ":" is a comment (keep-alive); a blank line dispatches. A provider that streams bare JSON lines
 * (no "data:") gets each line treated as one event. The stream may end without a final blank line.
 *
 * `idleMs` (0: none): when no byte at all arrives for that long (any chunk counts, a keep-alive comment or a piece of
 * reasoning as much as answer text), the read stops with a retryable network ProviderError of type "stream_stalled",
 * and the body is cancelled, which aborts its fetch. An abort of `signal` stops a read that is waiting at once.
 */
export async function* readSSE(body, { signal, idleMs = 0 } = {}) {
  if (!body) return;
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  let data = [];
  let event = "";
  let id = "";
  let pendingCR = false;
  const flush = () => {
    if (!data.length) { event = ""; return null; }
    const out = { event: event || "message", data: data.join("\n"), id };
    data = [];
    event = "";
    return out;
  };
  const onLine = (line) => {
    if (line === "") return flush();
    if (line[0] === ":") return null;
    const colon = line.indexOf(":");
    const field = colon < 0 ? line : line.slice(0, colon);
    let value = colon < 0 ? "" : line.slice(colon + 1);
    if (value[0] === " ") value = value.slice(1);
    if (field === "data") data.push(value);
    else if (field === "event") event = value;
    else if (field === "id") id = value;
    else if (colon < 0 || field[0] === "{" || field[0] === "[") {
      // not a field: a bare JSON line from a provider that skips the SSE framing
      const trimmed = line.trim();
      if (trimmed.startsWith("{") || trimmed.startsWith("[")) { data.push(trimmed); return flush(); }
    }
    return null;
  };
  // one read, raced against the idle limit and the signal (a stalled body may never settle a read on its own)
  const read = () => new Promise((resolve, reject) => {
    let timer = null;
    const onAbort = () => { cleanup(); reject(abortError()); };
    const cleanup = () => { if (timer !== null) clearTimeout(timer); signal?.removeEventListener?.("abort", onAbort); };
    if (idleMs > 0) timer = setTimeout(() => { cleanup(); reject(stalled(idleMs)); }, Math.min(idleMs, MAX_IDLE_MS));
    signal?.addEventListener?.("abort", onAbort, { once: true });
    reader.read().then((r) => { cleanup(); resolve(r); }, (err) => { cleanup(); reject(err); });
  });
  try {
    for (;;) {
      if (signal?.aborted) throw abortError();
      const { value, done } = await read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      if (pendingCR && buf[0] === "\n") buf = buf.slice(1);
      pendingCR = false;
      let start = 0;
      for (let i = 0; i < buf.length; i++) {
        const c = buf[i];
        if (c !== "\n" && c !== "\r") continue;
        const line = buf.slice(start, i);
        if (c === "\r") {
          if (i + 1 < buf.length) { if (buf[i + 1] === "\n") i++; }
          else pendingCR = true;
        }
        start = i + 1;
        const ev = onLine(line);
        if (ev) yield ev;
        if (signal?.aborted) throw abortError();
      }
      buf = buf.slice(start);
    }
    buf += decoder.decode();
    if (buf) { const ev = onLine(buf); if (ev) yield ev; }
    const last = flush();
    if (last) yield last;
  } finally {
    try { await reader.cancel(); } catch { /* already closed */ }
    try { reader.releaseLock(); } catch { /* released */ }
  }
}

/**
 * A model call that failed. `retryable`: a network failure, 429 or 5xx (the agent retries twice; `maxRetries`, when
 * set, retries it at most that often), never an error that is permanent or that the relay could not classify. `kind`:
 * "network" (never reached the service) or "model" (the service answered with an error). `type` and `code`: the
 * service's own (OpenAI sends both: a generic type and a specific code). `permanent`: a retry cannot fix it.
 * `compactable`: the request is more than the service takes, which a shorter conversation can fix (until the agent
 * shortens it itself, it is also permanent). `retryAfter`: seconds, from the Retry-After header.
 */
export class ProviderError extends Error {
  constructor(message, { status = 0, type = "", code = "", retryable = false, kind = "model", retryAfter = null, body = "", maxRetries = null } = {}) {
    super(message);
    this.name = "ProviderError";
    this.status = status;
    this.type = type;
    this.code = code;
    this.kind = kind;
    this.retryAfter = retryAfter;
    this.body = body;
    this.maxRetries = maxRetries;
    this.compactable = isCompactable(status, type, code, body);
    // retrying cannot help (a daily quota, an account out of credit, a request the service will never take): the page
    // offers no Retry
    this.permanent = this.compactable || isPermanent(status, type, code);
    this.retryable = Boolean(retryable) && !this.permanent && !NO_AUTO_RETRY.has(String(type || ""));
  }
}

/** A stream that sent nothing for `ms`: retried once, as a network failure. */
function stalled(ms) {
  return new ProviderError(t("core.provider.stalled", { seconds: Math.round(ms / 1000) }), { kind: "network", type: "stream_stalled", retryable: true, maxRetries: 1 });
}

/** Relay error types that are not transient: retrying them only spends the visitor's quota. */
const PERMANENT_TYPES = new Set([
  "forbidden_origin", "not_configured", "bad_request", "too_large", "model_not_allowed", "daily_limit", "total_limit",
  "blocked", "upstream_auth", "upstream_quota", "upstream_rejected", "https_required", "not_found", "not_relay",
  "method_not_allowed",
]);

/** A user's own account that cannot pay for the call, or a key the service does not know: a retry fails the same way. */
const ACCOUNT_TYPES = new Set([
  "insufficient_quota", "billing_hard_limit_reached", "billing_not_active", "billing_error", "quota_exceeded",
  "insufficient_balance", "exceeded_current_quota_error", "payment_required", "invalid_api_key", "authentication_error",
]);

/** The request is longer than the service takes (its context window, the relay's body limit). */
const OVERSIZE_TYPES = new Set(["context_length_exceeded", "too_large", "request_too_large", "exceed_context_size_error"]);
/** How a service without such a type says so in a 400 (Anthropic: "prompt is too long: … tokens > … maximum"). */
const OVERSIZE_WORDS = /maximum context length|context[ _-](?:length|window|size)|prompt is too long|exceeded model token limit/i;

/** Not retried by the agent, and not permanent either: the relay could not tell what failed, so the page keeps Retry. */
const NO_AUTO_RETRY = new Set(["upstream_unknown"]);

/** Is this error type one that a retry cannot fix? */
export function isPermanentType(type) {
  const s = String(type || "");
  return PERMANENT_TYPES.has(s) || ACCOUNT_TYPES.has(s) || OVERSIZE_TYPES.has(s);
}

/** A retry cannot fix it: a permanent type or code, or 402 (payment required). */
export function isPermanent(status, type = "", code = "") {
  return status === 402 || isPermanentType(type) || isPermanentType(code);
}

/**
 * The user's own account cannot make the call: an unknown key, or no credit or quota left (a 402). A retry fails the
 * same way; Settings → Models is where it is fixed. Takes a ProviderError or the error a turn record keeps.
 */
export function isAccountError({ status = 0, type = "", code = "" } = {}) {
  return status === 402 || ACCOUNT_TYPES.has(String(type || "")) || ACCOUNT_TYPES.has(String(code || ""));
}

/** Would a shorter conversation get through? An oversize type or code, a 413, or a 400 that says so in its words. */
export function isCompactable(status, type = "", code = "", detail = "") {
  if (OVERSIZE_TYPES.has(String(type || "")) || OVERSIZE_TYPES.has(String(code || "")) || status === 413) return true;
  return status === 400 && OVERSIZE_WORDS.test(String(detail || ""));
}

export function isRetryableStatus(status, type = "", code = "") {
  if (isPermanent(status, type, code) || NO_AUTO_RETRY.has(String(type || ""))) return false;
  return status === 408 || status === 409 || status === 429 || status >= 500;
}

/** Pull a message, type and code out of an error body of any common shape. */
export function parseErrorBody(text) {
  let message = String(text || "").trim();
  let type = "";
  let code = "";
  try {
    const j = JSON.parse(text);
    const e = j.error ?? j;
    if (typeof e === "string") message = e;
    else {
      message = e.message || e.msg || j.message || j.base_resp?.status_msg || j.detail || message;
      type = e.type || e.code || j.type || "";
      code = e.code ?? "";
    }
    if (typeof message !== "string") message = JSON.stringify(message);
  } catch { /* not JSON */ }
  return { message: message.slice(0, 600), type: typeof type === "string" ? type : String(type), code: code === null ? "" : String(code) };
}

export function retryAfterSeconds(headers) {
  const v = headers?.get?.("retry-after");
  if (!v) return null;
  const n = Number(v);
  if (Number.isFinite(n)) return n;
  const when = Date.parse(v);
  return Number.isFinite(when) ? Math.max(0, Math.round((when - Date.now()) / 1000)) : null;
}
