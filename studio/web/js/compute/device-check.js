// The device check: what this device's science kernels compute and how fast, measured here (Settings → Compute, and
// the harness web/test/runtime/gpu-science.mjs). For each kernel, on fixed seeded inputs at a few sizes:
//   known answer  the GPU's known-answer test (compute/science-gpu.js KNOWN_ANSWER)
//   parity        every backend's answer against the JavaScript reference, exactly: WebAssembly SIMD, and the GPU's
//                 (each GPU answer also passes the scheduler's own check, validateTanimoto, as it would in a job)
//   timings       JavaScript once, WebAssembly SIMD and the GPU `reps` times each (medians); the GPU's as the scheduler
//                 runs it (search and check), after one first call that is reported but not counted
//   verdict       per size: accelerated true (a physical adapter that passed its known answer, exact here, and faster
//                 than the CPU kernel, WebAssembly SIMD where it runs, else JavaScript), false (all that, but not
//                 faster), or null with the reason (no adapter, a software or unidentified one, a failed test, an
//                 answer that was not exact)
// The report (schema tcmstudio.device-check/1) is kept as this device's calibration (compute/calibration.js), which
// the scheduler and the job receipts then cite. A software adapter is used only with allowSoftware, and its report
// says "software, not evidence"; without it, a software-only machine's report says "no physical adapter" (its CPU
// kernels are still checked and timed). Nothing is extrapolated: a size that was not measured has no verdict.

import { DEVICE_CHECK_SCHEMA, CALIBRATION_DB, canonicalJson, deviceFingerprint, saveCalibration, sha256Text } from "./calibration.js";
import { FP_WORDS, TANIMOTO_KERNEL, scienceSimdStatus, tanimotoBestJS, tanimotoBestWasm, tanimotoRows, validateTanimoto } from "./science-cpu.js";
import { ScienceGPU } from "./science-gpu.js";
import { scienceKernelVersions } from "./science-scheduler.js";

/** [queries, references]: 10,504 is the largest endpoint's training set (cyp2d6_veith), the domain the search covers. */
export const DEVICE_CHECK_SIZES = Object.freeze([[32, 2048], [256, 10504], [1024, 10504], [4096, 10504]]);
export const QUICK_SIZES = Object.freeze([[32, 2048], [256, 10504]]);
export const DEVICE_CHECK_SEED = 20261019;
const OP = "tanimoto.best";

const now = () => (globalThis.performance?.now ? globalThis.performance.now() : Date.now());
const ms3 = (x) => (Number.isFinite(x) ? Math.round(x * 1000) / 1000 : null);
const median = (xs) => { const s = [...xs].sort((a, b) => a - b); const m = s.length >> 1; return s.length ? (s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2) : null; };
const where = () => (typeof globalThis.WorkerGlobalScope !== "undefined" && globalThis instanceof globalThis.WorkerGlobalScope ? "worker" : "page");
const same = (a, b) => a.arg.length === b.arg.length && ["arg", "inter", "union"].every((k) => a[k].every((v, i) => v === b[k][i]));

function lcg(seed) {
  let s = seed >>> 0;
  return () => (s = (Math.imul(s, 1664525) + 1013904223) >>> 0) / 2 ** 32;
}

/**
 * Fixed inputs shaped like the domain search's: references in families (sparse 2048-bit fingerprints, ~48 bits set,
 * members of a family a few bits apart), queries drawn from the same families, an empty and an all-ones fingerprint on
 * each side, and a reference repeated far from its first copy (a tie). The same seed gives the same bits everywhere.
 */
export function seededInputs(nq, nr, seed = DEVICE_CHECK_SEED) {
  const words = FP_WORDS;
  const rand = lcg(seed);
  const bases = Array.from({ length: 64 }, () => Array.from({ length: 48 }, () => Math.floor(rand() * 2048)));
  const set = (a, i, b) => { a[i * words + (b >>> 5)] |= 1 << (8 * ((b >>> 3) & 3) + 7 - (b & 7)); };
  const fill = (n) => {
    const out = new Uint32Array(n * words);
    for (let i = 0; i < n; i++) {
      for (const b of bases[Math.floor(rand() * bases.length)]) if (rand() > 0.08) set(out, i, b);
      for (let k = Math.floor(rand() * 4); k > 0; k--) set(out, i, Math.floor(rand() * 2048));
    }
    if (n > 2) { out.fill(0, words, 2 * words); out.fill(0xffffffff, 2 * words, 3 * words); }
    return out;
  };
  const refs = fill(nr), queries = fill(nq);
  if (nr > 10) refs.copyWithin((nr - 1) * words, 3 * words, 4 * words);
  return { queries, refs, words };
}

async function sha256Bytes(bytes) {
  const d = new Uint8Array(await globalThis.crypto.subtle.digest("SHA-256", bytes));
  return Array.from(d, (b) => b.toString(16).padStart(2, "0")).join("");
}

/** The verdict at one size, from what was measured there → {accelerated, reason}. */
function sizeVerdict(row, { adapter, gpuError, knownAnswer, allowSoftware }) {
  if (!adapter) return { accelerated: null, reason: `no WebGPU adapter${gpuError ? ` (${gpuError.message})` : ""}` };
  if (adapter.kind === "software" && !allowSoftware) return { accelerated: null, reason: "the only adapter is a software renderer, which is refused: no physical adapter" };
  if (knownAnswer?.ok === false) return { accelerated: null, reason: `the adapter failed its known-answer test: ${knownAnswer.reason}` };
  if (gpuError) return { accelerated: null, reason: `the GPU could not run: ${gpuError.message}` };
  if (row.gpu_error) return { accelerated: null, reason: `the GPU could not run at this size: ${row.gpu_error.message}` };
  if (row.exact.webgpu !== true) return { accelerated: null, reason: "the GPU's answers at this size were not exact, so it is never counted as acceleration" };
  if (adapter.kind === "software") return { accelerated: null, reason: "a software adapter: its timings are the CPU's, not evidence of a GPU" };
  if (adapter.kind !== "physical") return { accelerated: null, reason: `an unidentified adapter (${adapter.reason}): not evidence of a GPU` };
  const gpu = row.timings.webgpu?.ms, cpu = row.timings[row.cpu_baseline]?.ms;
  if (!Number.isFinite(gpu) || !Number.isFinite(cpu)) return { accelerated: null, reason: "not measured" };
  const label = row.cpu_baseline === "wasm-simd" ? "WebAssembly SIMD" : "JavaScript";
  return gpu < cpu
    ? { accelerated: true, reason: `the GPU took ${gpu} ms (search and check), ${label} ${cpu} ms: faster here` }
    : { accelerated: false, reason: `the GPU took ${gpu} ms (search and check), ${label} ${cpu} ms: not faster here` };
}

/**
 * Run the device check → the report (schema tcmstudio.device-check/1), kept as this device's calibration in IndexedDB
 * unless save is false (report.stored says whether it was). options: navigator; allowSoftware (tests and the harness's
 * --software only); sizes ([queries, refs] pairs); reps; timestamps (GPU timestamp queries where offered); timeoutMs
 * (each GPU step); indexedDB; save; gpu (a ScienceGPU to use instead of a new one); onProgress({stage, size, fraction});
 * isCancelled; seed.
 */
export async function deviceCheck({
  navigator = globalThis.navigator, allowSoftware = false, sizes = DEVICE_CHECK_SIZES, reps = 3, timestamps = true, timeoutMs = 30_000,
  indexedDB = globalThis.indexedDB, save = true, gpu = null, onProgress, isCancelled, seed = DEVICE_CHECK_SEED,
} = {}) {
  const t0 = now();
  const g = gpu || new ScienceGPU({ navigator, allowSoftware, timestamps, timeoutMs });
  const simd = scienceSimdStatus();
  const kernel = { kernel: TANIMOTO_KERNEL, known_answer: null, sizes: [] };
  const report = {
    schema: DEVICE_CHECK_SCHEMA, id: null, measured_at: new Date().toISOString(), ok: true, verdict: null, verdict_reason: null, label: null,
    software_allowed: Boolean(allowSoftware),
    device: { fingerprint: null, adapter: null, limits: null, gpu_error: null, wasm_simd: simd, placement: where(), cores: navigator?.hardwareConcurrency ?? null },
    kernels: { [OP]: kernel },
    versions: await scienceKernelVersions(),
    reps, seed, seconds: null, stored: null,
  };
  const progress = (stage, size, fraction) => { try { onProgress?.({ stage, size, fraction }); } catch { /* the caller's */ } };
  try {
    progress("gpu", null, 0);
    let gpuError = null;
    try { await g.ready({ isCancelled }); } catch (err) {
      if (err?.code === "cancelled" || isCancelled?.()) throw err;
      gpuError = { code: err?.code ?? null, message: String(err?.message || err).slice(0, 300) };
    }
    const adapter = g.adapter;
    kernel.known_answer = g.knownAnswer;
    Object.assign(report.device, { adapter, limits: g.limits, gpu_error: gpuError, fingerprint: await deviceFingerprint({ adapter, navigator }) });
    const useGpu = !gpuError;
    for (const [i, [nq, nr]] of sizes.entries()) {
      const size = { queries: nq, refs: nr };
      progress("size", size, i / sizes.length);
      const { queries, refs, words } = seededInputs(nq, nr, seed + i);
      const row = {
        queries: nq, refs: nr, words, pairs: nq * nr, seed: seed + i, result_sha256: null, exact: { "wasm-simd": null, webgpu: null },
        timings: { js: null, "wasm-simd": null, webgpu: null }, cpu_baseline: simd.available ? "wasm-simd" : "js", gpu_error: null, accelerated: null, reason: "",
      };
      // the reference: JavaScript, once
      let t = now();
      const want = tanimotoBestJS(queries, refs, { words, isCancelled });
      row.timings.js = { ms: ms3(now() - t), runs: [ms3(now() - t)] };
      row.result_sha256 = await sha256Bytes(tanimotoRows(want));
      if (simd.available) {
        const runs = [];
        let exact = true;
        for (let k = 0; k < reps; k++) {
          t = now();
          const got = tanimotoBestWasm(queries, refs, { words, isCancelled });
          runs.push(ms3(now() - t));
          exact &&= same(got, want);
        }
        row.exact["wasm-simd"] = exact;
        row.timings["wasm-simd"] = { ms: ms3(median(runs)), runs };
      }
      if (useGpu) {
        try {
          t = now();
          const first = await g.tanimotoBest(queries, refs, { words, isCancelled });
          const firstMs = ms3(now() - t);
          let exact = same(first, want);
          const runs = [], search = [], kernelMs = [], bands = [], maxBand = [];
          let pieces = null;
          for (let k = 0; k < reps; k++) {
            t = now();
            const got = await g.tanimotoBest(queries, refs, { words, isCancelled });
            const check = validateTanimoto(queries, refs, got, { words, isCancelled });
            runs.push(ms3(now() - t));
            const run = g.lastRun;
            search.push(run.work_ms); bands.push(run.bands); maxBand.push(run.max_band_ms); pieces = run.pieces;
            if (Number.isFinite(run.kernel_ms)) kernelMs.push(run.kernel_ms);
            exact &&= same(got, want) && check.ok;
          }
          row.exact.webgpu = exact;
          row.timings.webgpu = {
            ms: ms3(median(runs)), runs, search_ms: ms3(median(search)), kernel_ms: kernelMs.length === runs.length ? ms3(median(kernelMs)) : null,
            first_ms: firstMs, bands: median(bands), max_band_ms: ms3(Math.max(...maxBand)), pieces,
            note: kernelMs.length === runs.length ? "kernel_ms from GPU timestamps (the browser may quantize them)" : (g.lastRun?.kernel_note || "no GPU timestamps"),
          };
        } catch (err) {
          if (err?.code === "cancelled" || isCancelled?.()) throw err;
          row.gpu_error = { code: err?.code ?? null, message: String(err?.message || err).slice(0, 300) };
          if (err?.code === "wrong_answer") row.exact.webgpu = false;
        }
      }
      Object.assign(row, sizeVerdict(row, { adapter, gpuError, knownAnswer: g.knownAnswer, allowSoftware }));
      if (row.exact["wasm-simd"] === false || row.exact.webgpu === false) report.ok = false;
      kernel.sizes.push(row);
    }
    kernel.known_answer = g.knownAnswer;
    if (kernel.known_answer?.ok === false) report.ok = false;
    // the verdict of the whole check, in the words the harness prints
    const kind = adapter?.kind;
    const accelerated = kernel.sizes.filter((s) => s.accelerated === true).length;
    if (kind === "software") report.label = "software, not evidence";
    else if (adapter && kind !== "physical") report.label = "unidentified adapter, not evidence";
    if (!adapter || (kind === "software" && !allowSoftware)) {
      report.verdict = "no physical adapter";
      report.verdict_reason = !adapter ? `no WebGPU adapter${gpuError ? `: ${gpuError.message}` : ""}` : `the only adapter is a software renderer (${adapter.reason}), which is refused`;
    } else if (kernel.known_answer?.ok === false) {
      report.verdict = "known-answer test failed";
      report.verdict_reason = kernel.known_answer.reason;
    } else if (gpuError) {
      report.verdict = "the GPU could not run";
      report.verdict_reason = gpuError.message;
    } else if (kernel.sizes.some((s) => s.exact.webgpu === false)) {
      report.verdict = "GPU answers not exact";
      report.verdict_reason = "at least one size's GPU answers differ from the CPU reference";
    } else if (kind === "software") {
      report.verdict = "software adapter: not evidence";
      report.verdict_reason = `${adapter.reason}; its timings are the CPU's, and it never counts as acceleration`;
    } else if (kind !== "physical") {
      report.verdict = "unidentified adapter: not evidence";
      report.verdict_reason = adapter.reason;
    } else {
      report.verdict = accelerated ? `accelerated at ${accelerated} of ${kernel.sizes.length} sizes` : "not faster than the CPU at any size";
      report.verdict_reason = kernel.sizes.map((s) => `${s.queries} × ${s.refs}: ${s.reason}`).join("; ");
    }
  } finally {
    if (!gpu) g.stop();
  }
  report.seconds = ms3((now() - t0) / 1000);
  report.id = await sha256Text(canonicalJson({ ...report, id: null, stored: null }));
  progress("done", null, 1);
  if (save) {
    try {
      const key = await saveCalibration(report, { indexedDB });
      report.stored = { ok: true, db: CALIBRATION_DB, key };
    } catch (err) {
      report.stored = { ok: false, error: String(err?.message || err).slice(0, 300) };
    }
  }
  return report;
}
