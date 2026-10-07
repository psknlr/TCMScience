// Small helpers shared by the core modules. No DOM, no third-party code.

/** A random id (RFC 4122 v4). crypto.randomUUID needs a secure context; the fallback covers http://localhost pages. */
export function uuid() {
  const c = globalThis.crypto;
  if (c?.randomUUID) return c.randomUUID();
  const b = new Uint8Array(16);
  if (c?.getRandomValues) c.getRandomValues(b);
  else for (let i = 0; i < 16; i++) b[i] = Math.floor(Math.random() * 256);
  b[6] = (b[6] & 0x0f) | 0x40;
  b[8] = (b[8] & 0x3f) | 0x80;
  const h = [...b].map((x) => x.toString(16).padStart(2, "0")).join("");
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
}

export const nowIso = () => new Date().toISOString().replace(/\.\d{3}Z$/, "Z");

/** Resolve after `ms`, or reject with an AbortError as soon as `signal` aborts. */
export function sleep(ms, signal) {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) return reject(abortError());
    const timer = setTimeout(() => { signal?.removeEventListener?.("abort", onAbort); resolve(); }, ms);
    const onAbort = () => { clearTimeout(timer); reject(abortError()); };
    signal?.addEventListener?.("abort", onAbort, { once: true });
  });
}

export function abortError(message = "aborted") {
  try { return new DOMException(message, "AbortError"); } catch {
    const e = new Error(message);
    e.name = "AbortError";
    return e;
  }
}

export const isAbort = (err) => err?.name === "AbortError";

/**
 * JSON with sorted keys and no insignificant whitespace, non-ASCII kept as is: the same bytes as Python's
 * json.dumps(v, sort_keys=True, separators=(",", ":"), ensure_ascii=False), so hashes agree with the runner's.
 */
export function canonicalJson(value) {
  if (value === null || typeof value !== "object") {
    if (typeof value === "number" && !Number.isFinite(value)) return "null";
    return JSON.stringify(value) ?? "null";
  }
  if (Array.isArray(value)) return `[${value.map((v) => (v === undefined ? "null" : canonicalJson(v))).join(",")}]`;
  const keys = Object.keys(value).filter((k) => value[k] !== undefined).sort();
  return `{${keys.map((k) => `${JSON.stringify(k)}:${canonicalJson(value[k])}`).join(",")}}`;
}

const enc = new TextEncoder();

/** SHA-256 as lower-case hex, of a string (UTF-8), bytes, an ArrayBuffer or a Blob. */
export async function sha256Hex(input) {
  let bytes;
  if (typeof input === "string") bytes = enc.encode(input);
  else if (input instanceof ArrayBuffer) bytes = new Uint8Array(input);
  else if (ArrayBuffer.isView(input)) bytes = new Uint8Array(input.buffer, input.byteOffset, input.byteLength);
  else if (input && typeof input.arrayBuffer === "function") bytes = new Uint8Array(await input.arrayBuffer());
  else throw new TypeError("sha256Hex: unsupported input");
  const digest = await globalThis.crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(digest)].map((x) => x.toString(16).padStart(2, "0")).join("");
}

export const utf8Bytes = (s) => enc.encode(String(s ?? "")).length;

/** Cut a string to `max` characters, saying how long it was. */
export function clip(text, max, note = (n) => `… [truncated; ${n} characters in full]`) {
  const s = String(text ?? "");
  return s.length <= max ? s : s.slice(0, max) + note(s.length);
}

export function isPlainObject(v) {
  return v !== null && typeof v === "object" && !Array.isArray(v);
}

/** A loopback host name: what the runner and local model servers bind to. */
export function isLoopbackHost(host) {
  const h = String(host || "").toLowerCase().replace(/^\[|\]$/g, "");
  return h === "localhost" || h.endsWith(".localhost") || h === "::1" || /^127(?:\.\d{1,3}){3}$/.test(h);
}

export function isLoopbackUrl(url) {
  try { return isLoopbackHost(new URL(url).hostname); } catch { return false; }
}

export function trimSlash(url) {
  return String(url || "").trim().replace(/\/+$/, "");
}

/** base64url (with or without padding) → UTF-8 string. */
export function fromBase64Url(s) {
  const b64 = String(s).replace(/-/g, "+").replace(/_/g, "/");
  const padded = b64 + "=".repeat((4 - (b64.length % 4)) % 4);
  const bin = atob(padded);
  const bytes = Uint8Array.from(bin, (c) => c.charCodeAt(0));
  return new TextDecoder().decode(bytes);
}

export function toBase64Url(s) {
  const bytes = enc.encode(String(s));
  let bin = "";
  for (const b of bytes) bin += String.fromCharCode(b);
  return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

export async function blobToBase64(blob) {
  const bytes = new Uint8Array(await blob.arrayBuffer());
  let bin = "";
  const CHUNK = 0x8000;
  for (let i = 0; i < bytes.length; i += CHUNK) bin += String.fromCharCode.apply(null, bytes.subarray(i, i + CHUNK));
  return btoa(bin);
}

export function base64ToBytes(b64) {
  const bin = atob(b64);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

/** localStorage that never throws (private windows, disabled storage, Node). */
export const storage = {
  get(key, area = "local") {
    try {
      const raw = globalThis[`${area}Storage`]?.getItem(key);
      return raw == null ? null : raw;
    } catch { return null; }
  },
  set(key, value, area = "local") {
    try { globalThis[`${area}Storage`]?.setItem(key, value); return true; } catch { return false; }
  },
  remove(key, area = "local") {
    try { globalThis[`${area}Storage`]?.removeItem(key); } catch { /* unavailable */ }
  },
  getJSON(key, area = "local") {
    const raw = storage.get(key, area);
    if (raw == null) return null;
    try { return JSON.parse(raw); } catch { return null; }
  },
  setJSON(key, value, area = "local") {
    return storage.set(key, JSON.stringify(value), area);
  },
};

/** fetch with a timeout that composes with an outer signal. */
export async function fetchWithTimeout(fetchImpl, url, init = {}, timeoutMs = 0) {
  if (!timeoutMs) return fetchImpl(url, init);
  const ctl = new AbortController();
  const outer = init.signal;
  const onAbort = () => ctl.abort(outer.reason);
  if (outer) {
    if (outer.aborted) ctl.abort(outer.reason);
    else outer.addEventListener("abort", onAbort, { once: true });
  }
  const timer = setTimeout(() => ctl.abort(timeoutError()), timeoutMs);
  try {
    return await fetchImpl(url, { ...init, signal: ctl.signal });
  } catch (err) {
    if (ctl.signal.reason?.name === "TimeoutError" && !outer?.aborted) throw timeoutError();
    throw err;
  } finally {
    clearTimeout(timer);
    outer?.removeEventListener?.("abort", onAbort);
  }
}

function timeoutError() {
  try { return new DOMException("timed out", "TimeoutError"); } catch {
    const e = new Error("timed out");
    e.name = "TimeoutError";
    return e;
  }
}
