// Small helpers shared by the eval: where things are, digests, canonical JSON, and a JSON path reader.

import { createHash } from "node:crypto";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
export const EVAL = path.resolve(HERE, "..");
export const STUDIO = path.resolve(EVAL, "..");
export const WEB = path.join(STUDIO, "web");
export const REPO = path.resolve(STUDIO, "..");

/** The date the system prompt states (prompt.js puts today's date in it): pinned, so prompts and cassettes repeat. */
export const PINNED_DATE = "2026-11-06";

export function sha256(text) {
  return createHash("sha256").update(typeof text === "string" ? text : JSON.stringify(text)).digest("hex");
}

/** JSON with object keys sorted at every level: the same value always gives the same text (and digest). */
export function canonical(value) {
  return JSON.stringify(sortKeys(value));
}

function sortKeys(v) {
  if (Array.isArray(v)) return v.map(sortKeys);
  if (v && typeof v === "object") return Object.fromEntries(Object.keys(v).sort().map((k) => [k, sortKeys(v[k])]));
  return v;
}

/** get(obj, "result.conclusion.verdict") → the value, or undefined when any step is missing. */
export function get(obj, dotted) {
  let cur = obj;
  for (const key of String(dotted || "").split(".").filter(Boolean)) {
    if (cur === null || cur === undefined) return undefined;
    cur = Array.isArray(cur) && /^\d+$/.test(key) ? cur[Number(key)] : cur[key];
  }
  return cur;
}

/** "job.*" matches job.pipeline; a plain name matches itself. */
export function matches(pattern, name) {
  const p = String(pattern);
  if (p.endsWith("*")) return String(name).startsWith(p.slice(0, -1));
  return p === String(name);
}

export const isTrue = (v) => v === true || /^(1|true|yes)$/i.test(String(v ?? "").trim());
