// Server-sent events over a fetch body, and the error type both model clients throw.

import { abortError } from "../util.js";

/**
 * Parse an SSE byte stream into events {event, data, id}. Follows the WHATWG rules that matter for model APIs: lines
 * end in LF, CRLF or CR (a CRLF split across two chunks is one line end); several `data:` lines join with "\n"; a line
 * starting with ":" is a comment (keep-alive); a blank line dispatches. A provider that streams bare JSON lines
 * (no "data:") gets each line treated as one event. The stream may end without a final blank line.
 */
export async function* readSSE(body, { signal } = {}) {
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
  try {
    for (;;) {
      if (signal?.aborted) throw abortError();
      const { value, done } = await reader.read();
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
 * A model call that failed. `retryable`: a network failure, 429 or 5xx (the agent retries twice). `kind`: "network"
 * (never reached the service) or "model" (the service answered with an error).
 */
export class ProviderError extends Error {
  constructor(message, { status = 0, type = "", retryable = false, kind = "model", retryAfter = null, body = "" } = {}) {
    super(message);
    this.name = "ProviderError";
    this.status = status;
    this.type = type;
    this.retryable = retryable;
    this.kind = kind;
    this.retryAfter = retryAfter;
    this.body = body;
  }
}

/** Relay error types that are not transient: retrying them only spends the visitor's quota. */
const PERMANENT_TYPES = new Set([
  "forbidden_origin", "not_configured", "bad_request", "too_large", "model_not_allowed", "daily_limit", "total_limit",
  "blocked", "upstream_auth", "upstream_quota", "upstream_rejected", "https_required", "not_found",
]);

export function isRetryableStatus(status, type = "") {
  if (PERMANENT_TYPES.has(type)) return false;
  return status === 408 || status === 409 || status === 429 || status >= 500;
}

/** Pull a message and type out of an error body of any common shape. */
export function parseErrorBody(text) {
  let message = String(text || "").trim();
  let type = "";
  try {
    const j = JSON.parse(text);
    const e = j.error ?? j;
    if (typeof e === "string") message = e;
    else {
      message = e.message || e.msg || j.message || j.base_resp?.status_msg || j.detail || message;
      type = e.type || e.code || j.type || "";
    }
    if (typeof message !== "string") message = JSON.stringify(message);
  } catch { /* not JSON */ }
  return { message: message.slice(0, 600), type: typeof type === "string" ? type : String(type) };
}

export function retryAfterSeconds(headers) {
  const v = headers?.get?.("retry-after");
  if (!v) return null;
  const n = Number(v);
  if (Number.isFinite(n)) return n;
  const when = Date.parse(v);
  return Number.isFinite(when) ? Math.max(0, Math.round((when - Date.now()) / 1000)) : null;
}
