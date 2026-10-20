// Which kernel computes the ADMET applicability-domain search, and the facts the job's evidence keeps about it.
//
//   preference "reference"  no browser kernel: Python's own tanimoto_best computes the similarity (result null)
//   preference "cpu"        WebAssembly SIMD where it runs, else JavaScript
//   preference "auto"       the GPU only when this device's calibration (compute/calibration.js, written by its
//                           device check) says a physical adapter that passed its known-answer test was exact and
//                           measured faster than the CPU kernel at this size; else as "cpu"
// "faster" is never a cost model: no calibration, or a calibration of another adapter or browser, keeps the work on the
// CPU. A GPU result is used only after it passes validateTanimoto on this CPU (every index a reference, every count
// those of its reference, and the full search of a sample of queries), and Python checks it again
// (bioagent.admet.engine.PrecomputedSimilarity); one that fails is not used, the CPU computes the call, and the device
// is set aside for the session. There is no remote fallback: every backend here runs in this browser.
//
// Whether a call was accelerated is decided for that call: its own GPU time (set-up included when it was the device's
// first use in this session, the cold case, and the CPU check of its result) must be below the CPU kernel's time that
// the device check measured at a size no larger than the call's. The CPU kernel's time only grows with the
// comparisons, so that is a bound, not an estimate: a call it does not decide is not called accelerated. The
// calibration's warm timings choose the GPU; they never speak for a call that did not show it.
//
// force "webgpu" is a debug switch for test harnesses (web/test/runtime/science.mjs --mode admet runs the GPU path on
// a software adapter with it): the GPU is used without a calibration's verdict, its result still checked, and the facts
// say it was forced; such a call is never accelerated. Studio never sets it: neither the UI nor a job's parameters
// reach it (pipelines/admet.js takes it only as a handler option at construction).
//
// facts (one per call, JSON): {op, kernel, versions (scienceKernelVersions), placement, where, preference, size {queries, refs, words, pairs}, backend
// ("webgpu" | "wasm-simd" | "js" | "python"), considered [{backend, ok, reason}], reason, gpu (null unless the GPU was
// tried: {adapter, known_answer, dispatched, validated, verified, cold, setup_ms, kernel_ms, search_ms, total_ms (this
// call's GPU time: set-up, search and its CPU check), bands, pieces, rejected, error}), validation (the check of a GPU result), cpu_baseline ({backend, ms, at, source}), calibration_id,
// accelerated (true only for a GPU result that is physical, verified, validated, calibrated faster and faster on this call
// (above); false for another GPU result; null when the GPU computed nothing), basis (why accelerated is what it is), evidence (the
// GPU's rung on the ladder of compute/capabilities.js, or null), forced ("webgpu" when the debug switch chose it), ms}.

import { evidenceLevel } from "./capabilities.js";
import { calibrationVerdict, deviceFingerprint, sha256Text } from "./calibration.js";
import { FP_WORDS, TANIMOTO_KERNEL, scienceSimdStatus, searchShape, tanimotoBestJS, tanimotoBestWasm, validateTanimoto } from "./science-cpu.js";
import { TANIMOTO_PARTIAL, TANIMOTO_REDUCE } from "./science-gpu.js";
import { SCIENCE_WASM } from "./wasm/science.wasm.js";

export const TANIMOTO_OP = "tanimoto.best";
export const SCIENCE_PREFERENCES = Object.freeze(["auto", "cpu", "reference"]);

let versions = null;

/**
 * The kernels' identity, as the facts and the device check record it → {kernel, wasm_sha256 (the WebAssembly module's
 * bytes), wgsl_sha256 (the two shaders' text, joined by a NUL)}.
 */
export function scienceKernelVersions() {
  versions ??= (async () => {
    const s = atob(SCIENCE_WASM);
    const bytes = new Uint8Array(s.length);
    for (let i = 0; i < s.length; i++) bytes[i] = s.charCodeAt(i);
    const d = new Uint8Array(await globalThis.crypto.subtle.digest("SHA-256", bytes));
    return {
      kernel: TANIMOTO_KERNEL, wasm_sha256: Array.from(d, (b) => b.toString(16).padStart(2, "0")).join(""),
      wgsl_sha256: await sha256Text(`${TANIMOTO_PARTIAL}\u0000${TANIMOTO_REDUCE}`),
    };
  })();
  return versions;
}

const now = () => (globalThis.performance?.now ? globalThis.performance.now() : Date.now());
const ms3 = (x) => (Number.isFinite(x) ? Math.round(x * 1000) / 1000 : null);
const where = () => (typeof globalThis.WorkerGlobalScope !== "undefined" && globalThis instanceof globalThis.WorkerGlobalScope ? "worker" : "page");
const fmt = (n) => Number(n).toLocaleString("en-US");

function adapterFacts(a) {
  return a ? { kind: a.kind ?? null, vendor: a.vendor ?? "", architecture: a.architecture ?? "", description: a.description ?? "", fallback: Boolean(a.fallback) } : null;
}

/**
 * The similarity search for `queries` against `refs` (packed words, compute/science-cpu.js) → {result: {arg, inter,
 * union} | null (preference "reference": Python computes it), facts}. calibration: this device's device check report
 * (loadCalibration), or null; gpu: a ScienceGPU, or null where there is none; navigator: for the device fingerprint.
 * A cancel throws (code "cancelled") and is never retried on another backend; invalid input throws as the kernels do,
 * and so does a preference that is none of SCIENCE_PREFERENCES (it is never taken for "auto").
 */
export async function runTanimoto({ queries, refs, words = FP_WORDS, preference = "auto", calibration = null, gpu = null, navigator = globalThis.navigator, isCancelled, force = null } = {}) {
  const t0 = now();
  const { nq, nr } = searchShape(queries, refs, { words });
  const pref = preference ?? "auto";
  if (!SCIENCE_PREFERENCES.includes(pref)) throw new RangeError(`the similarity search's preference is ${SCIENCE_PREFERENCES.map((p) => `"${p}"`).join(", ")}, not ${JSON.stringify(pref)}`);
  if (force !== null && force !== undefined && force !== "webgpu") throw new RangeError(`force is "webgpu" or nothing, not ${JSON.stringify(force)}`);
  const forced = force === "webgpu" && pref === "auto";
  const pairs = nq * nr;
  const facts = {
    op: TANIMOTO_OP, kernel: TANIMOTO_KERNEL, versions: await scienceKernelVersions(), placement: "browser", where: where(), preference: pref,
    size: { queries: nq, refs: nr, words, pairs }, backend: null, considered: [], reason: "", gpu: null, validation: null,
    cpu_baseline: null, calibration_id: calibration?.id ?? null, accelerated: null, basis: "", evidence: null, ms: null,
    ...(forced ? { forced: "webgpu" } : {}),
  };
  const consider = (backend, ok, reason) => facts.considered.push({ backend, ok, reason });
  const done = (result) => { facts.ms = ms3(now() - t0); return { result, facts }; };

  if (pref === "reference") {
    for (const b of ["webgpu", "wasm-simd", "js"]) consider(b, false, "the reference implementation was asked for");
    consider("python", true, "the reference implementation was asked for");
    Object.assign(facts, { backend: "python", reason: "the reference implementation was asked for: Python's tanimoto_best computes the similarity itself", basis: "no browser kernel ran" });
    return done(null);
  }

  let result = null;
  let verdict = null;
  if (pref === "cpu") consider("webgpu", false, "CPU only was asked for");
  else if (!nq) consider("webgpu", false, "no queries: nothing to compute");
  else {
    verdict = calibrationVerdict(calibration, { op: TANIMOTO_OP, pairs, versions: facts.versions });
    if (forced) verdict = { ...verdict, use_gpu: true, accelerated: null, reason: `forced by a test harness's debug switch, without the device check's verdict (${verdict.reason})` };
    if (!verdict.use_gpu) consider("webgpu", false, verdict.reason);
    else if (!gpu) consider("webgpu", false, "this context has no WebGPU runtime");
    else result = await tryGpu({ facts, consider, verdict, calibration, gpu, navigator, queries, refs, words, isCancelled, forced });
  }
  if (result) {
    facts.backend = "webgpu";
    facts.reason = verdict.reason;
  } else {
    const t = now();
    const simd = scienceSimdStatus();
    let backend = "js";
    if (words > FP_WORDS) consider("wasm-simd", false, `the WebAssembly kernel takes fingerprints of up to ${FP_WORDS * 32} bits`);
    else if (!simd.available) consider("wasm-simd", false, `this engine has no WebAssembly SIMD (${simd.reason})`);
    else { result = tanimotoBestWasm(queries, refs, { words, isCancelled }); backend = "wasm-simd"; consider("wasm-simd", true, "the CPU kernel"); }
    if (!result) { result = tanimotoBestJS(queries, refs, { words, isCancelled }); consider("js", true, "the CPU reference kernel"); }
    facts.backend = backend;
    facts.reason = pref === "cpu" ? "CPU only was asked for" : facts.considered.find((c) => c.backend === "webgpu")?.reason || "the CPU kernel";
    facts.cpu_baseline = { backend, ms: ms3(now() - t), at: { queries: nq, refs: nr, pairs }, source: "this call" };
  }
  // the GPU's rung for this call, from what happened (compute/capabilities.js evidenceLevel): faster only when this
  // call's own GPU time, set-up included when it was cold, is below what the CPU kernel took at a size no larger
  const g = facts.gpu;
  const faster = facts.backend === "webgpu" && !forced && verdict?.accelerated === true && Number.isFinite(g?.total_ms) && Number.isFinite(verdict.cpu_ms) && g.total_ms < verdict.cpu_ms;
  if (g) {
    facts.evidence = evidenceLevel({
      selected: true, reported: Boolean(g.adapter), dispatched: g.dispatched === true, validated: g.validated === true, verified: g.verified === true,
      accelerated: faster, adapter_kind: g.adapter?.kind ?? null,
    });
  }
  if (facts.backend === "webgpu") {
    facts.accelerated = facts.evidence === "accelerated";
    facts.cpu_baseline = verdict.size
      ? { backend: verdict.cpu_backend, ms: verdict.cpu_ms, at: verdict.size, source: `calibration ${String(calibration.id).slice(0, 12)}` }
      : null;
    const own = `this call took ${g.total_ms} ms on the GPU (${g.cold ? "set-up included: the device's first use in this session" : "warm"}, its CPU check included)`;
    const cpu = verdict.size ? `the CPU kernel took ${verdict.cpu_ms} ms (${verdict.cpu_backend}) in the device check at ${fmt(verdict.size.queries)} × ${fmt(verdict.size.refs)}, a size no larger than this call's` : null;
    if (facts.accelerated) facts.basis = `a physical adapter that passed its known-answer test, this result checked on the CPU, and faster than the CPU here: ${own}; ${cpu}`;
    else if (forced) facts.basis = `the GPU was forced by a test harness's debug switch: never acceleration (reached: ${facts.evidence}); ${own}`;
    else if (verdict.accelerated === true && cpu && !faster) facts.basis = `not faster on this call: ${own}; ${cpu}`;
    else facts.basis = `the GPU computed this result, but not every rung below "accelerated" holds (reached: ${facts.evidence})`;
  } else {
    facts.basis = g ? `the GPU was tried and its result not used: ${g.rejected || g.error}` : "the GPU computed nothing for this call";
  }
  return done(result);
}

/** The GPU's answer when it can be used (after its check), else null with facts.gpu and `considered` saying why. */
async function tryGpu({ facts, consider, verdict, calibration, gpu, navigator, queries, refs, words, isCancelled, forced = false }) {
  const g = { adapter: null, known_answer: null, dispatched: false, validated: false, verified: false, cold: null, setup_ms: null, kernel_ms: null, search_ms: null, total_ms: null,
    bands: null, pieces: null, rejected: null, error: null };
  facts.gpu = g;
  const t = now();
  try {
    // the set-up a cold device needs (its adapter, pipelines and known-answer test) is this call's time too
    const ready = await gpu.ready({ isCancelled });
    g.cold = ready?.cold === true;
    g.setup_ms = ms3(now() - t);
    g.adapter = adapterFacts(gpu.adapter);
    g.known_answer = gpu.knownAnswer;
    g.verified = g.known_answer?.ok === true;
    // the calibration speaks for the adapter and browser it was measured on, and for no other (a forced call uses
    // none: it is never acceleration)
    const fp = await deviceFingerprint({ adapter: gpu.adapter, navigator });
    if (!forced && calibration?.device?.fingerprint?.id !== fp.id) {
      g.rejected = "the calibration was measured on another adapter or browser";
      consider("webgpu", false, g.rejected);
      return null;
    }
    const out = await gpu.tanimotoBest(queries, refs, { words, isCancelled });
    const run = out.run || gpu.lastRun || {};
    Object.assign(g, { dispatched: run.dispatched === true, cold: g.cold || run.cold === true, kernel_ms: run.kernel_ms ?? null, search_ms: run.work_ms ?? null, bands: run.bands ?? null, pieces: run.pieces ?? null });
    const check = validateTanimoto(queries, refs, out, { words, isCancelled });
    facts.validation = check;
    g.total_ms = ms3(now() - t);
    if (!check.ok) {
      g.rejected = `its result failed the check on the CPU: ${check.reason}`;
      gpu.setAsideFor(check.reason);
      consider("webgpu", false, g.rejected);
      return null;
    }
    g.validated = true;
    consider("webgpu", true, verdict.reason);
    return out;
  } catch (err) {
    if (err?.code === "cancelled" || isCancelled?.()) throw err;
    g.adapter ??= adapterFacts(gpu.adapter);
    g.known_answer ??= gpu.knownAnswer;
    g.verified = g.known_answer?.ok === true;
    g.dispatched = (err?.run ?? gpu.lastRun)?.dispatched === true;
    g.error = String(err?.message || err).slice(0, 300);
    g.total_ms = ms3(now() - t);
    consider("webgpu", false, `the GPU could not compute it: ${g.error}`);
    return null;
  }
}
