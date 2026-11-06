// Cassettes: a model's event streams (what streamChat yielded) recorded once and replayed in place of the model.
//
// Each recording is keyed by the case, the request's place in the turn, and the sha256 of the tool results the model
// was given by then; the cassette also keeps the digest of the system prompt and of the tool definitions it was
// recorded with, and the date the prompt stated (pinned: prompt.js writes today's date into it). Replaying under a
// prompt, a catalog or tool results that differ is cassette_stale: neither a pass nor a fail, since the recording no
// longer answers the question being asked. Run-to-run noise in a tool's text (times, generated ids) is masked before
// hashing; anything else that changes a result changes the key. The recorder refuses a case tagged hidden: a held-out
// case's answers are never written down where a prompt edit could be fitted to them.

import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { ProviderError } from "../../web/js/core/llm/sse.js";
import { PINNED_DATE, canonical, sha256 } from "../lib/util.mjs";
import { newMeter, providerFor, requestBytes } from "./scripted.mjs";

export const CASSETTE_SCHEMA = "tcmscience.eval.cassette/1";

export class CassetteStale extends Error {
  constructor(reason) { super(`cassette_stale: ${reason}`); this.name = "CassetteStale"; this.reason = reason; }
}

/** Times, durations and generated ids in a tool's text: they differ on every run and say nothing about the result. */
const NOISE = [
  [/\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|[+-]\d\d:?\d\d)?/g, "<time>"],
  [/"(duration_ms|seconds|elapsed_ms|ms)":\s*[\d.]+/g, '"$1":0'],
  [/\b([a-z]{1,4})_[0-9a-f]{8,}\b/g, "$1_<id>"], // inq_…, r_… (a governed run), j_… (a job)
  [/\bbj-[0-9a-z-]{6,}\b/g, "bj-<id>"],
  [/\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b/g, "<uuid>"],
];
export function denoise(text) {
  let s = String(text ?? "");
  for (const [re, to] of NOISE) s = s.replace(re, to);
  return s;
}

/** The tool results a request carries (openai tool messages or anthropic tool_result blocks), as the key hashes them. */
export function toolResultsOf(messages = []) {
  const out = [];
  for (const m of messages) {
    if (m?.role === "tool") out.push({ id: m.tool_call_id, content: denoise(m.content) });
    else if (m?.role === "user" && Array.isArray(m.content)) {
      for (const b of m.content) if (b?.type === "tool_result") out.push({ id: b.tool_use_id, content: denoise(typeof b.content === "string" ? b.content : canonical(b.content)) });
    }
  }
  return out;
}

export const keyOf = (params) => sha256(canonical(toolResultsOf(params.messages)));
export const promptDigest = (system) => sha256(String(system || ""));
export const toolsDigest = (tools) => sha256(canonical(tools || []));

/**
 * A model library that passes every call to `inner` and records what it yielded (or threw). `.cassette()` is the
 * document to save once the turn is over.
 */
export function recordingLib(inner, caseDoc, { source = "echo", provider = {} } = {}) {
  if ((caseDoc.tags || []).includes("hidden")) throw new Error(`case ${caseDoc.id} is tagged hidden: it is never recorded`);
  const doc = {
    schema: CASSETTE_SCHEMA, case: caseDoc.id, source, env_date: PINNED_DATE,
    provider: { format: provider.format || "openai", model: provider.model || "" },
    prompt_sha256: null, tools_sha256: null, recordings: [],
  };
  let k = 0;
  return {
    async *streamChat(params) {
      const step = k++;
      doc.prompt_sha256 ??= promptDigest(params.system);
      doc.tools_sha256 ??= toolsDigest(params.tools);
      const rec = { step, results_sha256: keyOf(params), tool_choice: params.toolChoice ?? null, events: [] };
      doc.recordings.push(rec);
      try {
        for await (const ev of inner.streamChat(params)) { rec.events.push(structuredClone(ev)); yield ev; }
      } catch (err) {
        if (err?.name !== "AbortError") rec.error = errorRecord(err);
        throw err;
      }
    },
    toolResultMessages: (...a) => inner.toolResultMessages(...a),
    cassette: () => doc,
  };
}

/** A model library that answers from a cassette; on a mismatch it records why (`.stale`) and ends the turn. */
export function replayLib(doc) {
  let k = 0;
  const lib = {
    stale: null,
    async *streamChat(params) {
      const step = k++;
      const why = mismatch(doc, params, step);
      if (why) { lib.stale ??= why; throw new CassetteStale(why); }
      const rec = doc.recordings.find((r) => r.step === step);
      for (const ev of rec.events) yield structuredClone(ev);
      if (rec.error) throw replayError(rec.error);
    },
    toolResultMessages: (results) => results.map((r) => ({ role: "tool", tool_call_id: r.id, content: r.text })),
  };
  return lib;
}

function mismatch(doc, params, step) {
  if (doc.schema !== CASSETTE_SCHEMA) return `not a ${CASSETTE_SCHEMA} cassette`;
  if (doc.prompt_sha256 !== promptDigest(params.system)) return "the system prompt differs from the one recorded";
  if (doc.tools_sha256 !== toolsDigest(params.tools)) return "the tool definitions differ from the ones recorded";
  const rec = doc.recordings.find((r) => r.step === step);
  if (!rec) return `no recording for request ${step + 1} of the turn`;
  if (rec.results_sha256 !== keyOf(params)) return `the tool results before request ${step + 1} differ from the ones recorded`;
  return null;
}

function errorRecord(err) {
  return {
    name: err?.name || "Error", message: err?.message || String(err), status: err?.status ?? 0, type: err?.type ?? "",
    retryable: Boolean(err?.retryable), kind: err?.kind ?? "model", retryAfter: err?.retryAfter ?? null,
  };
}

function replayError(e) {
  if (e.name !== "ProviderError") return new Error(e.message);
  return new ProviderError(e.message, { status: e.status, type: e.type, retryable: e.retryable, kind: e.kind, retryAfter: e.retryAfter });
}

/** {provider, llm, meter, model: "cassette"} replaying `doc`. */
export function cassetteDriver(doc, { preset = "tao" } = {}) {
  const provider = providerFor(preset, "http://127.0.0.1:9/v1");
  const meter = newMeter();
  const lib = replayLib(doc);
  const metered = {
    async *streamChat(params) {
      meter.request(requestBytes(provider, params), Boolean(params.tools?.length), params.toolChoice);
      yield* lib.streamChat(params);
    },
    toolResultMessages: lib.toolResultMessages,
  };
  return { kind: "cassette", model: "cassette", provider, llm: { openai: metered }, meter, stale: () => lib.stale, close: async () => {} };
}

export const cassettePath = (dir, caseId) => path.join(dir, `${caseId}.json`);

export function loadCassette(dir, caseId) {
  const file = cassettePath(dir, caseId);
  return existsSync(file) ? JSON.parse(readFileSync(file, "utf8")) : null;
}

export function saveCassette(dir, doc) {
  mkdirSync(dir, { recursive: true });
  writeFileSync(cassettePath(dir, doc.case), `${JSON.stringify(doc, null, 1)}\n`);
}
