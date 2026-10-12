// Small pieces the telemetry and admin endpoints share (the relay and the source gateway keep their own, older copies).
import { day } from "./limiter.js";
import { ipKey } from "./relay.js";

export const LOCAL = new Set(["localhost", "127.0.0.1", "[::1]"]);
const encoder = new TextEncoder();

export const json = (data, status = 200, headers = {}) => new Response(JSON.stringify(data), {
  status, headers: { ...headers, "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store" },
});

/** {error: {message, type}}, as every endpoint of this Worker answers a refusal. */
export const fail = (status, type, message, headers = {}) => json({ error: { message, type } }, status, headers);

export const hex = (bytes) => [...new Uint8Array(bytes)].map((b) => b.toString(16).padStart(2, "0")).join("");

export async function sha256Hex(text) {
  return hex(await crypto.subtle.digest("SHA-256", encoder.encode(String(text))));
}

/** Two secrets compared in constant time: both hashed first, so neither their contents nor their lengths show. */
export async function sameSecret(given, expected) {
  if (!given || !expected) return false;
  const [a, b] = await Promise.all([
    crypto.subtle.digest("SHA-256", encoder.encode(String(given))),
    crypto.subtle.digest("SHA-256", encoder.encode(String(expected))),
  ]);
  const x = new Uint8Array(a);
  const y = new Uint8Array(b);
  let diff = 0;
  for (let i = 0; i < x.length; i++) diff |= x[i] ^ y[i];
  return diff === 0;
}

/** The client's address as the edge saw it (never a header a client could set itself). */
export const clientIp = (request) => request.headers.get("CF-Connecting-IP") || "unknown";

/** The visitor for the per-visitor limits: sha256(VISITOR_SALT | address (IPv6 by /64) | UTC day), as the relay keys
 * its own counters. The address itself is kept nowhere. */
export async function callerKey(request, env, now) {
  return (await sha256Hex(`${env.VISITOR_SALT || ""}|${ipKey(clientIp(request))}|${day(now)}`)).slice(0, 24);
}

/** The body's bytes as text, or null when it is over `max` bytes (by its declared length, else while it is read). */
export async function readText(request, max) {
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

/** A request from a page of this same site: its Origin is exactly this host's origin (and an allowed one), and the
 * browser, when it says, calls it same-origin. Scripts and other sites are refused. */
export function sameSite(request, url, origins) {
  const origin = request.headers.get("Origin") || "";
  if (!origin || origin !== url.origin || !origins.includes(origin)) return false;
  const site = request.headers.get("Sec-Fetch-Site");
  return !site || site === "same-origin";
}

/** One cookie's value from the Cookie header, or "". */
export function cookie(request, name) {
  for (const part of (request.headers.get("Cookie") || "").split(";")) {
    const at = part.indexOf("=");
    if (at > 0 && part.slice(0, at).trim() === name) return part.slice(at + 1).trim();
  }
  return "";
}
