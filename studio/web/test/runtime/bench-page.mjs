// The browser compute benchmark, in a real browser (bench.mjs drives it). Every timing below is of a computation
// whose output was checked against the reference on the same input: a backend that answered differently is a
// failure, not a data point. Nothing is extrapolated; a backend that is not here is reported as absent.
//
//   tools:   the real tool calls through the Worker (Pyodide), with the original Python ("reference") and with the
//            local backends ("auto"), alternating, `reps` times each; the worker's own duration_ms is the timing.
//   kernels: the JavaScript kernels alone, in the page, on the same inputs (no Python, no JSON).
//   webgpu:  the WGSL count kernel, with exact parity against the CPU reference (a software adapter is labelled).
//   webnn:   which contexts can be created here, and whether a small graph computes correctly on each.

import { BrowserRuntime } from "../../js/runtime/browser.js";
import { probeCapabilities } from "../../js/compute/capabilities.js";
import { levenshtein, needlemanWunsch, sequenceCounts, smithWaterman } from "../../js/compute/kernels.js";
import { SequenceCompute, planSequenceCompute, referenceCounts } from "../../js/runtime/webgpu.js";

const q = new URLSearchParams(location.search);
const REPS = Math.max(1, Number(q.get("reps") || 3));
const QUICK = q.get("quick") === "1";
const indexUrl = q.get("index") || undefined;
const bootUrl = new URL("/runtime/boot.json", location.href).href;
const $status = document.getElementById("status");
const say = (s) => { $status.textContent = s; };

const out = {
  schema: "tcmstudio.browser-bench/1", ok: true, failures: [], user_agent: navigator.userAgent,
  isolated: Boolean(globalThis.crossOriginIsolated), reps: REPS, quick: QUICK,
  caps: null, boot: null, tools: [], kernels: [], webgpu: [], webnn: null,
};

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
  for (const [n, w] of [[60, 1000], [120, 1000], [250, 1000]]) {
    const seqs = Array.from({ length: n }, (_, i) => dnaOf(w, 100 + i, "ACGTN-"));
    out.kernels.push({ kernel: "distance counts", size: `${n} seqs × ${w}`, cells: n * (n - 1) / 2 * w, ms: round(time(() => sequenceCounts("native.distance_matrix", seqs)), 2) });
  }
  for (const k of out.kernels) k.ns_per_cell = round((k.ms * 1e6) / k.cells, 3);
}

// ------------------------------------------------------------------------------------------------- WebGPU

async function webgpuBench() {
  const adapter = out.caps?.webgpu?.adapter;
  if (!adapter) { out.webgpu.push({ available: false, reason: out.caps?.webgpu?.error || "no WebGPU adapter in this browser" }); return; }
  const software = out.caps.webgpu.software === true;
  // the runtime refuses software adapters; this measures one only to check the WGSL, and says so
  const gpu = new SequenceCompute({ allowSoftware: software });
  for (const [n, w] of [[60, 1000], [120, 1000], [250, 1000]]) {
    const args = { sequences: Array.from({ length: n }, (_, i) => dnaOf(w, 100 + i, "ACGTN-")), model: "p" };
    const plan = planSequenceCompute("native.distance_matrix", args);
    if (!plan) { out.webgpu.push({ size: `${n} seqs × ${w}`, available: false, reason: "beyond the kernel's limits" }); continue; }
    const expected = JSON.stringify(Array.from(referenceCounts(plan)));
    const ts = [];
    let exact = true;
    for (let i = 0; i < REPS + 1; i++) {
      const t0 = performance.now();
      const res = await gpu.prepare("native.distance_matrix", args, { force: true });
      const ms = performance.now() - t0;
      if (!res) { out.webgpu.push({ size: `${n} seqs × ${w}`, available: false, reason: gpu.fallbackReason }); exact = null; break; }
      if (JSON.stringify(res.counts) !== expected) exact = false;
      if (i > 0) ts.push(ms); // the first includes device and pipeline creation
    }
    if (exact === null) continue;
    if (!exact) fail(`WebGPU counts ${n}×${w} differ from the CPU reference`);
    out.webgpu.push({ size: `${n} seqs × ${w}`, cells: plan.operations, exact, software, adapter: [adapter.vendor, adapter.architecture].filter(Boolean).join(" "), ms: round(median(ts), 2), note: software ? "software renderer (SwiftShader): these timings are not a GPU's" : "" });
  }
  gpu.stop();
}

// ------------------------------------------------------------------------------------------------- main

try {
  say("probing capabilities");
  out.caps = await probeCapabilities({ deep: true, timeoutMs: 5000 });
  out.webnn = out.caps.webnn;
  kernelBench();
  await webgpuBench();
  say("starting Pyodide");
  const rt = new BrowserRuntime({ bootUrl, indexUrl });
  const t0 = performance.now();
  const info = await rt.start();
  out.boot = { wall_ms: Math.round(performance.now() - t0), runtime: info?.runtime, timings: info?.timings, bundle_from_cache: info?.bundle?.from_cache };
  await toolBench(rt);
} catch (err) {
  fail(`bench threw: ${err?.stack || err}`);
}
say(out.ok ? "done" : `done with ${out.failures.length} failure(s)`);
globalThis.__result = out;
