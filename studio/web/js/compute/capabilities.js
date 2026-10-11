// What this browser can compute with, found by trying rather than by its name (no user-agent sniffing). Works in a
// page and in a worker; never throws; every probe has a time limit.
//
// Three things are kept apart, because browsers blur them:
//   requested  what the page asked for (a WebNN context for "npu", a high-performance GPU adapter)
//   reported   what the API answered (a context was created, an adapter named itself)
//   verified   what a real computation showed (a kernel ran here and its output matched the CPU reference)
// A WebNN context created for "npu" is reported, not verified: Chromium creates one on machines that have no NPU and
// runs the graph on the CPU. Nothing here claims that a particular piece of silicon did the work; receipts name the
// backend that ran and whether its output was checked.

export const CAPABILITIES_SCHEMA = "tcmstudio.capabilities/1";

// Tiny modules from wasm-feature-detect (Apache-2.0): WebAssembly.validate says whether the engine knows the feature.
const WASM = {
  // (func (result v128) i32.const 0 i8x16.splat i8x16.popcnt)
  simd: [0, 97, 115, 109, 1, 0, 0, 0, 1, 5, 1, 96, 0, 1, 123, 3, 2, 1, 0, 10, 10, 1, 8, 0, 65, 0, 253, 15, 253, 98, 11],
  // (func (result v128) … i8x16.relaxed_swizzle)
  relaxed_simd: [0, 97, 115, 109, 1, 0, 0, 0, 1, 5, 1, 96, 0, 1, 123, 3, 2, 1, 0, 10, 15, 1, 13, 0, 65, 1, 253, 15, 65, 2, 253, 15, 253, 128, 2, 11],
  // (memory 1 1 shared): threads also need SharedArrayBuffer, which needs a cross-origin isolated page
  threads: [0, 97, 115, 109, 1, 0, 0, 0, 5, 4, 1, 3, 1, 1],
  // (memory 1) (func i32.const 0 i32.const 0 i32.const 0 memory.copy)
  bulk_memory: [0, 97, 115, 109, 1, 0, 0, 0, 1, 4, 1, 96, 0, 0, 3, 2, 1, 0, 5, 3, 1, 0, 1, 10, 14, 1, 12, 0, 65, 0, 65, 0, 65, 0, 252, 10, 0, 0, 11],
};

// The GPU limits that decide whether a workload fits (WebGPU spec names).
const GPU_LIMITS = ["maxBufferSize", "maxStorageBufferBindingSize", "maxComputeWorkgroupStorageSize", "maxComputeInvocationsPerWorkgroup",
  "maxComputeWorkgroupSizeX", "maxComputeWorkgroupsPerDimension", "maxStorageBuffersPerShaderStage"];

const SOFTWARE_GPU = /swiftshader|llvmpipe|lavapipe|software|microsoft basic render|warp/i;

// The WebNN contexts asked for. deviceType is what Chromium accepted when this was written; the specification has
// since moved to powerPreference, and an engine may ignore either: the answer is recorded, never assumed.
const WEBNN_REQUESTS = [
  { label: "default", options: {} },
  { label: "cpu", options: { deviceType: "cpu" } },
  { label: "gpu", options: { deviceType: "gpu" } },
  { label: "npu", options: { deviceType: "npu" } },
  { label: "low-power", options: { powerPreference: "low-power" } },
];

/**
 * Probe what is here. opts: {scope (navigator, WebAssembly, crossOriginIsolated, SharedArrayBuffer, matchMedia,
 * caches, indexedDB), timeoutMs, webgpu: true, webnn: true, deep: false (also build and run tiny WebNN graphs and
 * check their output), now}. → a plain, JSON-ready object (schema CAPABILITIES_SCHEMA).
 */
export async function probeCapabilities(opts = {}) {
  const g = opts.scope || globalThis;
  const nav = g.navigator || {};
  const now = opts.now || (() => (g.performance?.now ? g.performance.now() : Date.now()));
  const timeoutMs = opts.timeoutMs ?? 3000;
  const t0 = now();
  const isolated = Boolean(g.crossOriginIsolated);
  const sab = typeof g.SharedArrayBuffer === "function";
  const [webgpu, webnn, storage] = await Promise.all([
    opts.webgpu === false ? skipped("not probed") : probeWebGPU(nav, timeoutMs),
    opts.webnn === false ? skipped("not probed") : probeWebNN(g, nav, { timeoutMs, deep: Boolean(opts.deep) }),
    probeStorage(g, nav, timeoutMs),
  ]);
  const wasm = probeWasm(g.WebAssembly, { isolated, sab });
  return {
    schema: CAPABILITIES_SCHEMA,
    scope: typeof g.document === "object" && g.document ? "window" : "worker",
    cpu: {
      logical_cores: positive(nav.hardwareConcurrency),
      // a privacy-rounded hint (Chromium reports 0.25 … 8), not a measurement of free memory
      device_memory_gb: typeof nav.deviceMemory === "number" ? nav.deviceMemory : null,
      form: formFactor(g, nav),
    },
    isolation: { cross_origin_isolated: isolated, shared_array_buffer: sab },
    wasm,
    webgpu,
    webnn,
    storage,
    network: {
      online: nav.onLine !== false,
      effective_type: nav.connection?.effectiveType || null,
      save_data: nav.connection?.saveData === true,
    },
    timer: { resolution_ms: timerResolution(now) },
    probe_ms: Math.round(now() - t0),
  };
}

function skipped(reason) {
  return { api: null, skipped: reason };
}

function positive(n) {
  return Number.isFinite(n) && n > 0 ? n : null;
}

/** "mobile" | "desktop" | "unknown", from what the browser says about itself and its pointer: a hint for sizing. */
function formFactor(g, nav) {
  if (typeof nav.userAgentData?.mobile === "boolean") return nav.userAgentData.mobile ? "mobile" : "desktop";
  try {
    if (g.matchMedia) {
      const coarse = g.matchMedia("(pointer: coarse)").matches;
      const narrow = g.matchMedia("(max-width: 820px)").matches;
      return coarse && narrow ? "mobile" : coarse ? "unknown" : "desktop";
    }
  } catch { /* no media queries in this scope */ }
  return "unknown";
}

function probeWasm(W, { isolated, sab }) {
  if (!W?.validate) return { available: false, simd: false, relaxed_simd: false, threads: false, threads_usable: false, bulk_memory: false };
  const ok = (bytes) => { try { return W.validate(new Uint8Array(bytes)); } catch { return false; } };
  const threads = ok(WASM.threads);
  return {
    available: true,
    simd: ok(WASM.simd),
    relaxed_simd: ok(WASM.relaxed_simd),
    threads,
    // threads that can actually be started: shared memory needs SharedArrayBuffer, which needs isolation
    threads_usable: threads && sab && isolated,
    bulk_memory: ok(WASM.bulk_memory),
  };
}

async function probeWebGPU(nav, timeoutMs) {
  const out = { api: Boolean(nav.gpu?.requestAdapter), adapter: null, software: null, features: [], limits: {}, error: null };
  if (!out.api) return out;
  let adapter;
  try {
    adapter = await within(nav.gpu.requestAdapter({ powerPreference: "high-performance" }), timeoutMs, "requestAdapter");
  } catch (err) {
    out.error = message(err);
    return out;
  }
  if (!adapter) { out.error = "no adapter"; return out; }
  let info = adapter.info;
  try { if (!info && adapter.requestAdapterInfo) info = await within(adapter.requestAdapterInfo(), timeoutMs, "requestAdapterInfo"); } catch { info = null; }
  info ||= {};
  const fallback = Boolean(adapter.isFallbackAdapter ?? info.isFallbackAdapter);
  out.adapter = {
    vendor: String(info.vendor || ""), architecture: String(info.architecture || ""),
    device: String(info.device || ""), description: String(info.description || ""), fallback,
  };
  out.software = fallback || SOFTWARE_GPU.test(`${info.vendor} ${info.architecture} ${info.device} ${info.description}`);
  try { out.features = [...(adapter.features || [])].map(String).sort(); } catch { out.features = []; }
  for (const k of GPU_LIMITS) {
    const v = adapter.limits?.[k];
    if (typeof v === "number") out.limits[k] = v;
  }
  return out;
}

/**
 * Which WebNN contexts can be created, and what each says it supports. With `deep`, a 2×2 float32 matmul is built,
 * dispatched and read back on each context: that proves the API computes correctly, not which hardware ran it.
 */
async function probeWebNN(g, nav, { timeoutMs, deep }) {
  const out = {
    api: Boolean(nav.ml?.createContext),
    contexts: [],
    note: "A created context shows that the browser accepted the request; it does not show which processor ran the graph.",
  };
  if (!out.api) return out;
  for (const { label, options } of WEBNN_REQUESTS) {
    const row = { label, requested: options, created: false, error: null, limits_fingerprint: null, float16_matmul: null, verified: null };
    let ctx = null;
    try {
      ctx = await within(nav.ml.createContext(options), timeoutMs, "createContext");
      row.created = Boolean(ctx);
    } catch (err) {
      row.error = message(err);
    }
    if (ctx) {
      try {
        const limits = typeof ctx.opSupportLimits === "function" ? ctx.opSupportLimits() : null;
        if (limits) {
          row.limits_fingerprint = fingerprint(limits);
          const types = limits.matmul?.a?.dataTypes || limits.matmul?.inputs?.dataTypes;
          row.float16_matmul = Array.isArray(types) ? types.includes("float16") : null;
        }
      } catch { /* opSupportLimits is newer than createContext */ }
      if (deep) row.verified = await matmulCheck(g, ctx, timeoutMs);
      try { ctx.destroy?.(); } catch { /* not destroyable in this version */ }
    }
    out.contexts.push(row);
  }
  // contexts that report identical operator limits are probably served by the same backend
  const prints = out.contexts.filter((c) => c.limits_fingerprint).map((c) => c.limits_fingerprint);
  out.distinct_backends_hint = prints.length ? new Set(prints).size : null;
  return out;
}

async function matmulCheck(g, ctx, timeoutMs) {
  try {
    const Builder = g.MLGraphBuilder;
    if (typeof Builder !== "function") return { ok: false, reason: "no MLGraphBuilder" };
    const b = new Builder(ctx);
    const desc = { dataType: "float32", shape: [2, 2] };
    const a = b.input("a", desc);
    const w = b.constant(desc, new Float32Array([1, 2, 3, 4]));
    const graph = await within(b.build({ y: b.matmul(a, w) }), timeoutMs, "build");
    const ta = await ctx.createTensor({ ...desc, writable: true });
    const ty = await ctx.createTensor({ ...desc, readable: true });
    ctx.writeTensor(ta, new Float32Array([1, 0, 0, 1]));
    ctx.dispatch(graph, { a: ta }, { y: ty });
    const y = new Float32Array(await within(ctx.readTensor(ty), timeoutMs, "readTensor"));
    const ok = y.length === 4 && y[0] === 1 && y[1] === 2 && y[2] === 3 && y[3] === 4;
    return ok ? { ok: true } : { ok: false, reason: `wrong output ${Array.from(y).join(",")}` };
  } catch (err) {
    return { ok: false, reason: message(err) };
  }
}

async function probeStorage(g, nav, timeoutMs) {
  const out = {
    quota_bytes: null, usage_bytes: null, persisted: null,
    opfs: typeof nav.storage?.getDirectory === "function",
    cache_api: typeof g.caches?.open === "function",
    indexeddb: typeof g.indexedDB?.open === "function",
    service_worker: Boolean(nav.serviceWorker?.register),
  };
  try {
    const e = nav.storage?.estimate ? await within(nav.storage.estimate(), timeoutMs, "estimate") : null;
    if (e) { out.quota_bytes = e.quota ?? null; out.usage_bytes = e.usage ?? null; }
  } catch { /* estimate refused (private mode) */ }
  try {
    if (nav.storage?.persisted) out.persisted = await within(nav.storage.persisted(), timeoutMs, "persisted");
  } catch { /* not in a worker, or refused */ }
  return out;
}

/** The smallest step performance.now() takes here (coarsened without cross-origin isolation): timings below it mean little. */
function timerResolution(now) {
  let best = Infinity;
  for (let i = 0; i < 5; i++) {
    const a = now();
    let b = now();
    let guard = 0;
    while (b === a && guard++ < 1e6) b = now();
    if (b > a) best = Math.min(best, b - a);
  }
  return Number.isFinite(best) ? Math.round(best * 1e4) / 1e4 : null;
}

function within(promise, ms, what) {
  let timer;
  return Promise.race([
    Promise.resolve(promise),
    new Promise((_, reject) => { timer = setTimeout(() => reject(new Error(`${what} timed out after ${ms} ms`)), ms); }),
  ]).finally(() => clearTimeout(timer));
}

function message(err) {
  return String(err?.message || err || "error").slice(0, 300);
}

/** A short stable fingerprint of a JSON-able value (FNV-1a over its canonical JSON). */
export function fingerprint(value) {
  const text = canonical(value);
  let h = 0x811c9dc5;
  for (let i = 0; i < text.length; i++) {
    h ^= text.charCodeAt(i);
    h = Math.imul(h, 0x01000193) >>> 0;
  }
  return h.toString(16).padStart(8, "0");
}

function canonical(v) {
  if (Array.isArray(v)) return `[${v.map(canonical).join(",")}]`;
  if (v && typeof v === "object") return `{${Object.keys(v).sort().map((k) => `${JSON.stringify(k)}:${canonical(v[k])}`).join(",")}}`;
  return JSON.stringify(v ?? null);
}

/** One line per backend for people: what is here, in plain words, without claiming more than was shown. */
export function summarizeCapabilities(caps) {
  if (!caps) return [];
  const lines = [];
  const w = caps.wasm || {};
  lines.push({ backend: "wasm", available: Boolean(w.available), detail: [w.simd ? "SIMD" : "no SIMD", w.threads_usable ? "threads" : w.threads ? "threads need isolation" : "no threads"].join(", ") });
  const gpu = caps.webgpu || {};
  lines.push({
    backend: "webgpu", available: Boolean(gpu.adapter) && gpu.software === false,
    detail: !gpu.api ? "no WebGPU API" : !gpu.adapter ? `no adapter${gpu.error ? ` (${gpu.error})` : ""}` : `${[gpu.adapter.vendor, gpu.adapter.architecture].filter(Boolean).join(" ") || "adapter"}${gpu.software ? " (software renderer: not used for acceleration)" : ""}`,
  });
  const nn = caps.webnn || {};
  const created = (nn.contexts || []).filter((c) => c.created).map((c) => c.label);
  lines.push({ backend: "webnn", available: created.length > 0, detail: !nn.api ? "no WebNN API" : created.length ? `contexts created: ${created.join(", ")} (the processor that runs a graph is not reported)` : "no context could be created" });
  return lines;
}
