// Fakes shared by the web-core tests: a streaming fetch, scripted model streams, runtimes and catalogs.

import { readFileSync } from "node:fs";

const enc = new TextEncoder();

/** A Response whose body streams `chunks` (strings or Uint8Arrays) one read at a time. */
export function streamResponse(chunks, { status = 200, headers = { "content-type": "text/event-stream" }, onCancel } = {}) {
  let i = 0;
  const body = new ReadableStream({
    pull(controller) {
      if (i >= chunks.length) { controller.close(); return; }
      const c = chunks[i++];
      if (c instanceof Error) { controller.error(c); return; }
      controller.enqueue(typeof c === "string" ? enc.encode(c) : c);
    },
    cancel() { onCancel?.(); },
  });
  return new Response(body, { status, headers });
}

/** SSE text for a list of JSON payloads (data: lines), ending with [DONE] unless done:false. */
export function sse(payloads, { done = true, eol = "\n" } = {}) {
  const out = payloads.map((p) => `data: ${typeof p === "string" ? p : JSON.stringify(p)}${eol}${eol}`);
  if (done) out.push(`data: [DONE]${eol}${eol}`);
  return out.join("");
}

/** Anthropic SSE: [{event, data}] → text with event: lines. */
export function anthropicSse(events) {
  return events.map((e) => `event: ${e.type}\ndata: ${JSON.stringify(e)}\n\n`).join("");
}

/** Split a string into pieces of `n` bytes (to cut lines, CRLFs and UTF-8 sequences anywhere). */
export function bytePieces(text, n) {
  const bytes = enc.encode(text);
  const out = [];
  for (let i = 0; i < bytes.length; i += n) out.push(bytes.slice(i, i + n));
  return out;
}

/** fetch that records requests and answers with handler(url, init, n). */
export function fakeFetch(handler) {
  const calls = [];
  const fn = async (url, init = {}) => {
    const call = { url: String(url), init, body: init.body ? safeJson(init.body) : null, rawBody: init.body, headers: { ...(init.headers || {}) } };
    calls.push(call);
    if (init.signal?.aborted) throw abortError();
    return handler(call.url, init, calls.length - 1, call);
  };
  fn.calls = calls;
  return fn;
}

function safeJson(s) { try { return JSON.parse(s); } catch { return s; } }

export function abortError() {
  return new DOMException("aborted", "AbortError");
}

/** A scripted model library for the agent: each call to streamChat takes the next step. */
export function scriptedLLM(steps, { record } = {}) {
  let n = 0;
  const calls = [];
  const lib = {
    calls,
    async *streamChat(params) {
      const k = n++;
      calls.push({ ...params, messages: structuredClone(params.messages) });
      record?.(params);
      let step = steps[Math.min(k, steps.length - 1)];
      if (typeof step === "function") step = await step(params, k);
      if (step instanceof Error) throw step;
      for (const ev of step) {
        if (ev instanceof Error) throw ev;
        if (ev && ev.wait) { await ev.wait(params); continue; }
        if (params.signal?.aborted) throw abortError();
        yield ev;
      }
    },
    toolResultMessages: (results) => results.map((r) => ({ role: "tool", tool_call_id: r.id, content: r.text })),
  };
  return lib;
}

/** OpenAI-format events for one model step. */
export function openaiStep({ text = "", reasoning = "", calls = [], usage = { input: 100, output: 10 }, finish } = {}) {
  const evs = [];
  if (reasoning) evs.push({ type: "reasoning", delta: reasoning });
  if (text) evs.push({ type: "text", delta: text });
  for (const c of calls) evs.push({ type: "tool_call", id: c.id, name: c.name, arguments: c.args || {}, raw: JSON.stringify(c.args || {}), ...(c.error ? { error: c.error } : {}) });
  if (usage) evs.push({ type: "usage", ...usage });
  const wire = { role: "assistant", content: text };
  if (calls.length) wire.tool_calls = calls.map((c) => ({ id: c.id, type: "function", function: { name: c.name, arguments: JSON.stringify(c.args || {}) } }));
  evs.push({ type: "done", finish_reason: finish || (calls.length ? "tool_calls" : "stop"), wire });
  return evs;
}

/** A runtime that answers every call with an envelope from `answer(tool, args, ctx)`. */
export function fakeRuntime(kind, { status = "ready", answer, delayMs = 0 } = {}) {
  const calls = [];
  return {
    kind, label: kind, status, calls,
    onStatus: () => () => {},
    async start() { this.status = "ready"; },
    async info() { return {}; },
    async catalog() { return null; },
    async device() { return {}; },
    async call(tool, args, ctx) {
      calls.push({ tool, args, ctx });
      if (delayMs) await new Promise((r, j) => {
        const timer = setTimeout(r, delayMs);
        ctx?.signal?.addEventListener?.("abort", () => { clearTimeout(timer); j(abortError()); }, { once: true });
      });
      if (answer) return answer(tool, args, ctx);
      return envelopeFor(tool, kind);
    },
  };
}

export function envelopeFor(tool, where = "browser", extra = {}) {
  return {
    ok: true, tool, via: `native.${tool}`, status: "succeeded", duration_ms: 3, summary: `${tool} ok`,
    text: JSON.stringify({ tool, ok: true }), result: { ok: true }, citations: [],
    governance: { kind: "native", released: null, artifact: null, verdict: null, claims: [], evidence: [], refusals: [], labels: [], licences: [], limitations: [], outputs: [] },
    receipt: { where, runtime: "test", device: "cpu", versions: {}, input_sha256: "x", output_sha256: "y", started_at: "2026-10-07T12:00:00Z" },
    job: null, approval: null, error: null, ...extra,
  };
}

/** A small catalog in the CONTRACTS §2 shape. */
export function testCatalogDoc() {
  const obj = (props = {}, required = []) => ({ type: "object", properties: props, required });
  return {
    schema: "tcmstudio.catalog/1",
    versions: { tcmstudio: "0.1.0", bioagent: "0.2.7", psh: "0.6.0" },
    categories: [
      { id: "tcm_knowledge", zh: "中医知识", en: "TCM Knowledge" },
      { id: "tcm_safety", zh: "安全与配伍", en: "Safety & Compatibility" },
      { id: "seq_genomics", zh: "序列与基因组", en: "Sequences & Genomics" },
      { id: "literature", zh: "文献证据", en: "Literature & Evidence" },
      { id: "omics", zh: "组学流程", en: "Omics Pipelines" },
      { id: "system", zh: "审计与环境", en: "Provenance & Environment" },
    ],
    core: [
      { name: "tcm_herb", title: { zh: "本草", en: "Herb" }, description: "Look up a herb in the seed corpus.", parameters: obj({ name: { type: "string" } }, ["name"]), category: "tcm_knowledge", exec: ["browser", "runner"], network: false, confirm: false, job: false, maps_to: "native.tcm_herb" },
      { name: "tcm_compatibility", title: { zh: "配伍", en: "Compatibility" }, description: "十八反/十九畏 as recorded relations.", parameters: obj({ herbs: { type: "array" } }, ["herbs"]), category: "tcm_safety", exec: ["browser", "runner"], network: false, confirm: false, job: false, maps_to: "native.tcm_compatibility" },
      { name: "literature_search", title: { zh: "文献检索", en: "Literature search" }, description: "Search Europe PMC.", parameters: obj({ query: { type: "string" } }, ["query"]), category: "literature", exec: ["runner"], network: true, confirm: false, job: false, maps_to: "connector.europepmc.search" },
      { name: "run_pipeline", title: { zh: "运行流程", en: "Run pipeline" }, description: "Start a pipeline job.", parameters: obj({ pipeline: { type: "string", enum: ["rnaseq", "fold"] }, arguments: { type: "object" } }, ["pipeline"]), category: "omics", exec: ["runner"], network: false, confirm: true, job: true, maps_to: "job.pipeline" },
      { name: "catalog_search", title: { zh: "目录检索", en: "Catalog search" }, description: "Search the catalog.", parameters: obj({ query: { type: "string" }, limit: { type: "integer" } }), category: "system", exec: ["browser", "runner"], network: false, confirm: false, job: false, maps_to: "system.catalog_search" },
      { name: "capabilities_status", title: { zh: "能力状态", en: "Capabilities" }, description: "What can run where.", parameters: obj(), category: "system", exec: ["browser", "runner"], network: false, confirm: false, job: false, maps_to: "system.capabilities" },
      { name: "call_tool", title: { zh: "调用工具", en: "Call tool" }, description: "Run any catalog entry.", parameters: obj({ tool: { type: "string" }, arguments: { type: "object" } }, ["tool"]), category: "system", exec: ["browser", "runner"], network: false, confirm: false, job: false, maps_to: "" },
    ],
    entries: [
      { id: "native.tcm_herb", kind: "native", title: { zh: "本草查询", en: "Herb lookup" }, summary: "Herb monograph from the seed corpus (性味归经).", category: "tcm_knowledge", parameters: obj({ name: { type: "string" } }, ["name"]), example: { name: "甘草" }, exec: ["browser", "runner"], network: false, confirm: false, job: false, gpu: false, heavy: [], tags: ["本草", "中药", "herb", "性味归经"] },
      { id: "native.tcm_compatibility", kind: "native", title: { zh: "配伍禁忌", en: "Compatibility check" }, summary: "Recorded incompatibilities (十八反, 十九畏) between herbs.", category: "tcm_safety", parameters: obj({ herbs: { type: "array", items: { type: "string" } } }, ["herbs"]), example: null, exec: ["browser", "runner"], network: false, confirm: false, job: false, gpu: false, heavy: [], tags: ["配伍禁忌", "十八反", "十九畏", "safety"] },
      { id: "native.reverse_complement", kind: "native", title: { zh: "反向互补", en: "Reverse complement" }, summary: "Reverse complement of a DNA or RNA sequence (IUPAC ambiguity codes honoured).", category: "seq_genomics", parameters: obj({ sequence: { type: "string" } }, ["sequence"]), example: { sequence: "ATGC" }, exec: ["browser", "runner"], network: false, confirm: false, job: false, gpu: false, heavy: [], tags: ["序列", "DNA"] },
      { id: "connector.europepmc.search", kind: "connector", title: { zh: "Europe PMC 检索", en: "Europe PMC search" }, summary: "Search Europe PMC for articles.", category: "literature", parameters: obj({ query: { type: "string" } }, ["query"]), example: null, exec: ["runner"], network: true, confirm: false, job: false, gpu: false, heavy: [], tags: ["文献", "literature", "pubmed"], hosts: ["www.ebi.ac.uk"] },
      { id: "job.pipeline", kind: "job", title: { zh: "流程任务", en: "Pipeline job" }, summary: "A pipeline as a runner job.", category: "omics", parameters: obj({ pipeline: { type: "string" } }), example: null, exec: ["runner"], network: false, confirm: true, job: true, gpu: true, heavy: [], tags: ["pipeline", "job"] },
      { id: "native.scrna_cluster", kind: "native", title: { zh: "单细胞聚类", en: "Single-cell clustering" }, summary: "Cluster a small single-cell matrix.", category: "omics", parameters: obj({ upload: { type: "string" } }), example: null, exec: ["browser", "runner"], network: false, confirm: false, job: false, gpu: false, heavy: ["scanpy"], tags: ["单细胞", "scRNA"] },
      { id: "native.dock", kind: "native", title: { zh: "分子对接", en: "Docking" }, summary: "Dock ligands with Vina.", category: "omics", parameters: obj({}), example: null, exec: ["runner"], network: false, confirm: false, job: false, gpu: false, heavy: ["vina"], tags: ["对接", "docking"], available: false, missing: ["vina", "meeko"] },
      { id: "system.catalog_search", kind: "system", title: { zh: "目录检索", en: "Catalog search" }, summary: "Search the catalog.", category: "system", parameters: obj(), example: null, exec: ["browser", "runner"], network: false, confirm: false, job: false, gpu: false, heavy: [], tags: [] },
      { id: "system.capabilities", kind: "system", title: { zh: "能力状态", en: "Capabilities" }, summary: "What can run where.", category: "system", parameters: obj(), example: null, exec: ["browser", "runner"], network: false, confirm: false, job: false, gpu: false, heavy: [], tags: [] },
    ],
  };
}

export function loadFixture(name) {
  return JSON.parse(readFileSync(new URL(`./${name}`, import.meta.url), "utf8"));
}

/** A fake IndexedDB-free approvals adapter backed by a plain object. */
export function memoryApprovals(projects = {}) {
  return {
    projects,
    async get(id) { return projects[id] || null; },
    async grant(id, keys) {
      const p = projects[id] || (projects[id] = { web: false, approvals: {} });
      for (const k of keys) p.approvals[k] = "project";
    },
  };
}
