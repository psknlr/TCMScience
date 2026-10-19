// The browser compute benchmark, in a real browser (bench.mjs drives it). Every timing below is of a computation
// whose output was checked against the reference on the same input: a backend that answered differently is a
// failure, not a data point. Nothing is extrapolated; a backend that is not here is reported as absent.
//
//   tools:   the real tool calls through the Worker (Pyodide), with the original Python ("reference") and with the
//            local backends ("auto"), alternating, `reps` times each; the worker's own duration_ms is the timing.
//   kernels: the JavaScript and WebAssembly SIMD kernels alone, in the page, on the same inputs (no Python, no JSON);
//            each SIMD result is compared with the JavaScript one before it is timed.
//   webgpu:  the WGSL count kernel, with exact parity against the CPU reference, beside the CPU kernels on the same
//            input: one row per workload with its dimensions, the CPU baseline (each kernel alone, and as Python calls
//            it, with JSON both ways), the GPU's first call on a fresh device (device, pipeline, known-answer test:
//            each workload gets its own SequenceCompute), its warm end-to-end time (packing, upload, dispatch,
//            readback, the sample check, the digest and the packet's JSON), its kernel time from GPU timestamps where
//            the device has them, memory, correctness, the adapter as the runtime's classifier sees it, and the reason
//            when it fell back. A software or unidentified adapter's rows carry no speed-up and no "accelerated".
//   webnn:   which contexts can be created here, and whether a small graph computes correctly on each.
// ?tools=0 skips the tool calls (and Pyodide).
//
// Schema tcmstudio.browser-bench/2: a WebGPU row is the object built in webgpuBench (workload, adapter {kind, …},
// cpu_baseline_ms, gpu_cold_ms, gpu_e2e_ms, …). /1 rows (2026-10-10) had {size, cells, adapter: "<name>", ms, exact,
// software, note}; bench.mjs still renders both.

import { BrowserRuntime } from "../../js/runtime/browser.js";
import { probeCapabilities } from "../../js/compute/capabilities.js";
import { inputDigest } from "../../js/compute/accelerator.js";
import { pythonKernels } from "../../js/compute/bridge.js";
import { levenshtein, needlemanWunsch, sequenceCounts, smithWaterman } from "../../js/compute/kernels.js";
import { sequenceCountsSIMD, simdStatus } from "../../js/compute/simd.js";
import { SequenceCompute, planSequenceCompute, referenceCounts } from "../../js/runtime/webgpu.js";

const q = new URLSearchParams(location.search);
const REPS = Math.max(1, Number(q.get("reps") || 3));
const QUICK = q.get("quick") === "1";
const TOOLS = q.get("tools") !== "0";
const indexUrl = q.get("index") || undefined;
const bootUrl = new URL("/runtime/boot.json", location.href).href;
const $status = document.getElementById("status");
const say = (s) => { $status.textContent = s; };

const out = {
  schema: "tcmstudio.browser-bench/2", ok: true, failures: [], user_agent: navigator.userAgent, browser: null,
  isolated: Boolean(globalThis.crossOriginIsolated), reps: REPS, quick: QUICK, tools_run: TOOLS,
  caps: null, boot: null, tools: [], kernels: [], webgpu: [], webnn: null,
};

/** What the browser says about itself and its OS, where it says more than its user-agent string (Chromium). */
async function browserInfo() {
  const info = { user_agent: navigator.userAgent, brands: null, platform: null, platform_version: null, architecture: null, model: null };
  try {
    const d = navigator.userAgentData;
    if (d?.getHighEntropyValues) {
      const v = await d.getHighEntropyValues(["platform", "platformVersion", "architecture", "model", "fullVersionList"]);
      Object.assign(info, { brands: v.fullVersionList || d.brands || null, platform: v.platform || null, platform_version: v.platformVersion || null, architecture: v.architecture || null, model: v.model || null });
    }
  } catch { /* not offered here */ }
  return info;
}

/** The JavaScript heap in use, where the browser reports it (Chromium's performance.memory), else null. */
function heapBytes() {
  try { return Number.isFinite(performance.memory?.usedJSHeapSize) ? performance.memory.usedJSHeapSize : null; } catch { return null; }
}

function fail(what) { out.ok = false; out.failures.push(what); }

function dnaOf(n, seed, letters = "ACGT") {
  let x = seed >>> 0;
  let s = "";
  for (let i = 0; i < n; i++) { x = (Math.imul(x, 1103515245) + 12345) >>> 0; s += letters[(x >>> 16) % letters.length]; }
  return s;
}

const median = (xs) => { const s = [...xs].sort((a, b) => a - b); const m = s.length >> 1; return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2; };
const round = (x, d = 2) => (Number.isFinite(x) ? Math.round(x * 10 ** d) / 10 ** d : null);

// ------------------------------------------------------------------------------------------------- workloads

const PROTEIN = "ARNDCQEGHILKMFPSTWYV";
function workloads() {
  const align = QUICK ? [300, 1000] : [300, 1000, 2000];
  const lev = QUICK ? [500, 2000] : [500, 2000, 4000];
  const dist = QUICK ? [[20, 500], [60, 1000]] : [[20, 500], [60, 1000], [120, 1000]];
  const list = [];
  for (const n of align) {
    list.push({ tool: "native.global_alignment", size: `${n}×${n}`, units: n * n, args: { a: dnaOf(n, 1), b: dnaOf(n, 2) } });
    list.push({ tool: "native.local_alignment", size: `${n}×${n}`, units: n * n, args: { a: dnaOf(n, 3), b: dnaOf(n, 4), match: 2, mismatch: -1, gap: -2 } });
  }
  list.push({ tool: "native.protein_alignment", size: "600×600", units: 360000, args: { a: dnaOf(600, 5, PROTEIN), b: dnaOf(600, 6, PROTEIN), mode: "local" } });
  for (const n of lev) list.push({ tool: "native.edit_distance", size: `${n}×${n}`, units: n * n, args: { a: dnaOf(n, 7, "ACGTN"), b: dnaOf(n, 8, "ACGTN") } });
  for (const [n, w] of dist) {
    const sequences = Object.fromEntries(Array.from({ length: n }, (_, i) => [`s${i}`, dnaOf(w, 100 + i, "ACGTN-")]));
    list.push({ tool: "native.distance_matrix", size: `${n} seqs × ${w}`, units: n * (n - 1) / 2 * w, args: { sequences, model: "k2p" } });
  }
  list.push({ tool: "native.hamming_distance", size: "200 000", units: 200000, args: { a: dnaOf(200000, 9, "ACGTN"), b: dnaOf(200000, 10, "ACGTN") } });
  list.push({ tool: "native.gc_content", size: "20 000, windows of 500", units: (20000 - 500 + 1) * 500, args: { sequence: dnaOf(20000, 11, "ACGTSWN"), window: 500 } });
  // a call with no kernel at all: the fixed cost of a tool call in the Worker
  list.push({ tool: "native.fisher_exact", size: "2×2", units: 1, args: { a: 8, b: 2, c: 1, d: 5 } });
  return list;
}

// ------------------------------------------------------------------------------------------------- the tool calls

async function toolBench(rt) {
  for (const w of workloads()) {
    say(`tools: ${w.tool} ${w.size}`);
    const times = { reference: [], auto: [] };
    let backend = null, refHash = null, fastHash = null, refStatus = null;
    for (let r = 0; r < REPS; r++) {
      // alternate the order, so neither side always runs on a warmer engine
      for (const acceleration of r % 2 ? ["auto", "reference"] : ["reference", "auto"]) {
        const env = await rt.call("call_tool", { tool: w.tool, arguments: w.args }, { acceleration, project_id: "bench", conversation_id: "bench", approvals: [] });
        if (env?.status !== "succeeded") { fail(`${w.tool} ${w.size} ${acceleration}: ${env?.status} ${env?.error?.message || ""}`); continue; }
        times[acceleration].push(env.duration_ms);
        if (acceleration === "reference") { refHash = env.receipt?.output_sha256; refStatus = env.status; }
        else { fastHash = env.receipt?.output_sha256; backend = env.receipt?.compute?.backend || "pyodide"; }
      }
    }
    const same = Boolean(refHash) && refHash === fastHash;
    if (!same) fail(`${w.tool} ${w.size}: the outputs differ between the reference and the local backend`);
    const ref = median(times.reference), fast = median(times.auto);
    out.tools.push({
      tool: w.tool, size: w.size, units: w.units, backend, same_output: same, status: refStatus, args: w.args, output_sha256: refHash,
      reference_ms: round(ref, 1), local_ms: round(fast, 1), speedup: round(ref / fast, 2),
      reference_all: times.reference, local_all: times.auto,
    });
  }
}

// ------------------------------------------------------------------------------------------------- the kernels alone

const DNA_TABLE = [2, -1, -1, -1, -1, 2, -1, -1, -1, -1, 2, -1, -1, -1, -1, 2];
function time(fn, reps = REPS) {
  fn(); // one warm-up: the JIT compiles the loop
  const ts = [];
  for (let i = 0; i < reps; i++) { const t0 = performance.now(); fn(); ts.push(performance.now() - t0); }
  return median(ts);
}

function kernelBench() {
  for (const n of QUICK ? [1000, 2000] : [1000, 2000, 4000]) {
    const a = dnaOf(n, 1), b = dnaOf(n, 2);
    out.kernels.push({ kernel: "needleman-wunsch", size: `${n}×${n}`, cells: n * n, ms: round(time(() => needlemanWunsch(a, b, "ACGT", "ACGT", DNA_TABLE, Array(16).fill(false), -2, false)), 2) });
    out.kernels.push({ kernel: "smith-waterman", size: `${n}×${n}`, cells: n * n, ms: round(time(() => smithWaterman(a, b, "ACGT", "ACGT", DNA_TABLE, Array(16).fill(false), -2, false)), 2) });
    out.kernels.push({ kernel: "levenshtein", size: `${n}×${n}`, cells: n * n, ms: round(time(() => levenshtein(a, b)), 2) });
  }
  const simd = simdStatus().available;
  const counts = (kernel, size, cells, tool, seqs, window = 0) => {
    out.kernels.push({ kernel: `${kernel} (JavaScript)`, size, cells, ms: round(time(() => sequenceCounts(tool, seqs, window)), 2) });
    if (!simd) { out.kernels.push({ kernel: `${kernel} (WebAssembly SIMD)`, size, cells, ms: null, note: "no SIMD here" }); return; }
    const same = JSON.stringify(Array.from(sequenceCountsSIMD(tool, seqs, window))) === JSON.stringify(Array.from(sequenceCounts(tool, seqs, window)));
    if (!same) fail(`SIMD ${kernel} ${size} differs from the JavaScript kernel`);
    out.kernels.push({ kernel: `${kernel} (WebAssembly SIMD)`, size, cells, exact: same, ms: same ? round(time(() => sequenceCountsSIMD(tool, seqs, window)), 2) : null });
  };
  for (const [n, w] of QUICK ? [[60, 1000], [250, 1000]] : [[60, 1000], [120, 1000], [250, 1000], [40, 8000]]) {
    const seqs = Array.from({ length: n }, (_, i) => dnaOf(w, 100 + i, "ACGTN-"));
    counts("distance counts", `${n} seqs × ${w}`, n * (n - 1) / 2 * w, "native.distance_matrix", seqs);
  }
  const long = [dnaOf(1_000_000, 3, "ACGTN"), dnaOf(1_000_000, 4, "ACGTN")];
  counts("hamming counts", "2 × 1 000 000", 1_000_000, "native.hamming_distance", long);
  counts("GC count", "1 000 000", 1_000_000, "native.gc_content", [long[0]]);
  for (const k of out.kernels) k.ns_per_cell = k.ms === null ? null : round((k.ms * 1e6) / k.cells, 3);
}

// ------------------------------------------------------------------------------------------------- WebGPU

/** The CPU baseline for one input: each count kernel alone, and through the bridge as Python calls it (JSON both ways). */
function cpuBaseline(plan, expectedJson) {
  const payload = JSON.stringify({ tool: plan.tool, sequences: plan.seqs, window: plan.window });
  const row = { kernel_ms: {}, e2e_ms: {}, exact: {} };
  for (const [id, simd] of [["wasm-simd", true], ["js", false]]) {
    if (simd && !simdStatus().available) { row.kernel_ms[id] = row.e2e_ms[id] = row.exact[id] = null; continue; }
    const kernel = simd ? () => sequenceCountsSIMD(plan.tool, plan.seqs, plan.window) : () => sequenceCounts(plan.tool, plan.seqs, plan.window);
    const bridge = pythonKernels({ simd });
    const same = JSON.stringify(Array.from(kernel())) === expectedJson && JSON.stringify(JSON.parse(bridge.call("sequence.counts", payload)).counts) === expectedJson;
    row.exact[id] = same;
    if (!same) { fail(`${id} counts for ${plan.n} × ${plan.width} differ from the CPU reference`); row.kernel_ms[id] = row.e2e_ms[id] = null; continue; }
    row.kernel_ms[id] = round(time(kernel), 3);
    row.e2e_ms[id] = round(time(() => JSON.parse(bridge.call("sequence.counts", payload))), 3);
  }
  return row;
}

async function webgpuBench() {
  const caps = out.caps?.webgpu;
  const adapter = caps?.adapter; // classifyAdapter()'s answer, through the probe
  if (!adapter) { out.webgpu.push({ available: false, reason: caps?.error || "no WebGPU adapter in this browser" }); return; }
  const kind = adapter.kind;
  const sizes = QUICK ? [[60, 1000], [120, 1000]] : [[60, 1000], [120, 1000], [250, 1000]];
  for (const [n, w] of sizes) {
    // the runtime refuses software adapters; this runs one only to check the WGSL, and its rows say so. A fresh
    // SequenceCompute per workload, so its first call pays the device, the pipeline and the known-answer test again
    const gpu = new SequenceCompute({ allowSoftware: kind === "software", timestamps: true });
    const tool = "native.distance_matrix";
    const args = { sequences: Array.from({ length: n }, (_, i) => dnaOf(w, 100 + i, "ACGTN-")), model: "p" };
    const plan = planSequenceCompute(tool, args);
    const label = `${n} seqs × ${w}`;
    if (!plan) { out.webgpu.push({ size: label, available: false, reason: "beyond the kernel's limits" }); gpu.stop(); continue; }
    const workload = {
      tool, n, width: w, operations: plan.operations, records: plan.records, input_bytes: plan.inputBytes, output_bytes: plan.outputBytes,
      // input, counts, the uniform block and the readback copy: what the kernel allocates on the device for one call
      gpu_buffer_bytes: plan.inputBytes + 2 * plan.outputBytes + 32,
    };
    const expectedJson = JSON.stringify(Array.from(referenceCounts(plan)));
    const cpu = cpuBaseline(plan, expectedJson);
    const heapBefore = heapBytes();
    let heapPeak = heapBefore;
    const warm = [];
    let exact = true, fallback = null, cold = null;
    for (let i = 0; i < REPS + 1; i++) {
      const t0 = performance.now();
      const res = await gpu.prepare(tool, args, { force: true });
      if (!res) { fallback = gpu.fallbackReason; break; }
      const d0 = performance.now();
      const digest = await inputDigest(tool, plan.seqs, plan.window);
      const d1 = performance.now();
      // the packet's trip into Python, as the Worker makes it: its JSON written, and read back here in Python's place
      JSON.parse(JSON.stringify({ ...res, input_sha256: digest }));
      const d2 = performance.now();
      const h = heapBytes();
      if (h !== null && (heapPeak === null || h > heapPeak)) heapPeak = h;
      if (JSON.stringify(res.counts) !== expectedJson) exact = false;
      const run = gpu.lastRun;
      const row = { ms: d0 - t0, work_ms: run.work_ms, transfer_ms: run.transfer_ms, kernel_ms: run.kernel_ms, digest_ms: d1 - d0, handoff_ms: d2 - d1, kernel_note: run.kernel_note || null };
      // the first call is the cold one only when it really set something up (lastRun.cold); it is never a warm sample
      if (i === 0) cold = run.cold ? { ...row, setup_ms: run.setup_ms } : null;
      else warm.push(row);
    }
    if (!exact) fail(`WebGPU counts ${label} differ from the CPU reference`);
    const med = (k) => (warm.length && warm.every((r) => Number.isFinite(r[k])) ? round(median(warm.map((r) => r[k])), 3) : null);
    const e2e = warm.length ? round(median(warm.map((r) => r.work_ms + r.digest_ms + r.handoff_ms)), 3) : null;
    const cpuE2e = Object.values(cpu.e2e_ms).filter(Number.isFinite);
    const best = cpuE2e.length ? Math.min(...cpuE2e) : null;
    // only a physical adapter's measurement may say "faster", and only against the CPU kernels measured on this input
    const comparable = kind === "physical" && exact && e2e !== null && best !== null;
    out.webgpu.push({
      size: label, workload, exact: fallback ? null : exact,
      adapter: { ...adapter, features: caps.features || [] },
      software: kind === "software", known_answer: gpu.knownAnswer,
      cpu_baseline_ms: cpu.kernel_ms, cpu_e2e_ms: cpu.e2e_ms,
      gpu_cold_ms: cold ? round(cold.ms, 3) : null, gpu_setup_ms: cold?.setup_ms ?? null,
      gpu_cold_note: cold ? "a fresh device: adapter, device, pipeline and the known-answer test, then the call" : "no cold call was measured",
      gpu_e2e_ms: e2e, gpu_work_ms: med("work_ms"), gpu_transfer_ms: med("transfer_ms"), gpu_kernel_ms: med("kernel_ms"),
      gpu_kernel_note: med("kernel_ms") === null ? (warm.find((r) => r.kernel_note)?.kernel_note || "no GPU timestamps") : "GPU timestamps (the browser may quantize them)",
      handoff_ms: med("handoff_ms"), digest_ms: med("digest_ms"),
      peak_memory: { js_heap_bytes: heapPeak, js_heap_before_bytes: heapBefore, gpu_buffer_bytes: workload.gpu_buffer_bytes,
        note: heapPeak === null ? "this browser does not report its heap; the GPU figure is what the kernel allocates" : "Chromium's performance.memory (rounded by the browser); the GPU figure is what the kernel allocates" },
      fallback_reason: fallback,
      accelerated: comparable ? e2e < best : null,
      speedup: comparable ? round(best / e2e, 2) : null,
      note: kind === "software" ? "software renderer: these timings are not a GPU's, and no speed-up is derived from them"
        : kind === "unidentified" ? "unidentified adapter: not counted as a GPU, so no speed-up is derived" : "",
    });
    gpu.stop();
  }
}

// ------------------------------------------------------------------------------------------------- main

try {
  say("probing capabilities");
  out.browser = await browserInfo();
  out.caps = await probeCapabilities({ deep: true, timeoutMs: 5000 });
  out.webnn = out.caps.webnn;
  kernelBench();
  await webgpuBench();
  if (TOOLS) {
    say("starting Pyodide");
    const rt = new BrowserRuntime({ bootUrl, indexUrl });
    const t0 = performance.now();
    const info = await rt.start();
    out.boot = { wall_ms: Math.round(performance.now() - t0), runtime: info?.runtime, timings: info?.timings, bundle_from_cache: info?.bundle?.from_cache };
    await toolBench(rt);
  }
} catch (err) {
  fail(`bench threw: ${err?.stack || err}`);
}
say(out.ok ? "done" : `done with ${out.failures.length} failure(s)`);
globalThis.__result = out;
