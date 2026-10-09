// The page's accelerator host (docs/V2.md §13.4). A browser call's Python hands a hot loop to the page; the host
// runs it on the fastest engine this device allows and returns the integers; Python verifies a sample natively
// before it trusts them and computes every float itself.
//
//   createAccelHost({device, settings}) → {kernels(), run(kernel, input, {signal, onProgress}), dispose()}
//
// `device` is the report of runtime/device.js ({cls, js_workers, gpu: "hardware" | "software" | "none", …}),
// `settings` holds settings.compute.browser ({workers, gpu: "auto" | "off"}, optionally prefer: "auto" | "gpu" |
// "cpu"). Engines, all computing exactly what CPython computes:
//
//   js-workers  the exact JS port in a pool of plain module workers (device.js_workers of them)
//   webgpu      the WGSL transliteration, one invocation per pathway; chosen on "auto" only with a hardware adapter
//               and at least 64 pathways (a software adapter, which emulates a GPU on the CPU, never on "auto");
//               the host recomputes the first and the last pathway with the JS kernel and, on any difference,
//               discards the GPU result and runs js-workers, saying so in `fallback`
//   js          the kernel on this thread: only where no worker can be started and this is itself a worker
//
// Settings gpu "off" never uses the GPU. A malformed input is refused with an error naming the field (the call then
// computes in Python).

import { abortError, isAbort, sha256Hex } from "../core/util.js";
import { SOURCES as GPU_SOURCES, openGpu, runWebGPU } from "./gpu.js";
import { SOURCES as MT_SOURCES } from "./mt19937.js";
import { KERNEL, SOURCES as NP_SOURCES, countRange, prepare, subPlan } from "./npnull.js";
import { gpuMeasuredFaster } from "./speed.js";
import { runJsWorkers } from "./workers.js";

export const KERNELS = Object.freeze([KERNEL]);
/** On "auto", fewer pathways than this stay on the CPU: the GPU's setup costs more than it saves. */
export const GPU_MIN_PATHWAYS = 64;
const ENGINES = ["auto", "webgpu", "js-workers", "js"];

let digestP = null;

/**
 * "sha256:" + hex of the kernel's sources: its name, the WGSL text and the source text (Function.prototype.toString,
 * the code exactly as loaded) of every function and class that decides its numbers, in a fixed order. The same on
 * every load and in every engine; a change to any of that code changes it.
 */
export function kernelDigest() {
  if (!digestP) {
    const parts = [KERNEL, ...MT_SOURCES, ...NP_SOURCES, ...GPU_SOURCES].map((s) => (typeof s === "string" ? s : Function.prototype.toString.call(s)));
    digestP = sha256Hex(parts.join("\n\u001e\n")).then((hex) => `sha256:${hex}`);
    digestP.catch(() => { digestP = null; });
  }
  return digestP;
}

/** settings.compute.browser (or settings.computeBrowser, or the object itself), checked. */
export function browserPrefs(settings) {
  const s = settings?.compute?.browser ?? settings?.computeBrowser ?? settings ?? {};
  const n = Number(s.workers);
  const gpu = s.gpu === "off" || s.gpu === "on" ? s.gpu : "auto";
  // "on" (prefer the GPU) is the user's explicit choice; an explicit prefer still wins
  const prefer = [s.prefer, settings?.prefer].find((v) => v === "auto" || v === "gpu" || v === "cpu") || (gpu === "on" ? "gpu" : "auto");
  return { workers: s.workers !== "auto" && Number.isInteger(n) && n >= 1 ? n : "auto", gpu, prefer };
}

/**
 * Which engine a run should try first: "webgpu" or "js-workers". Pure. `gpu` is what is known of the adapter
 * ("hardware" | "software" | "none" | "unknown": not probed yet, which may still turn out to be hardware).
 * `measured` is what this device's benchmark showed (speed.js): true = the GPU was faster, false = slower, null =
 * never measured. On "auto" the GPU is used only when it was measured faster here: its speed for this kernel cannot
 * be told from its name, and the exact JS workers are never the wrong answer.
 */
export function chooseEngine({ paths = 0, prefer = "auto", gpuSetting = "auto", gpu = "unknown", allowSoftware = false, engine = "auto", measured = null } = {}) {
  if (gpuSetting === "off" || engine === "js-workers" || engine === "js") return "js-workers";
  const usable = gpu === "hardware" || gpu === "unknown" || (gpu === "software" && allowSoftware);
  if (!usable) return "js-workers";
  if (engine === "webgpu" || prefer === "gpu") return "webgpu";
  if (prefer === "cpu") return "js-workers";
  return gpu !== "software" && paths >= GPU_MIN_PATHWAYS && measured === true ? "webgpu" : "js-workers";
}

/** How many JS workers: the device's js_workers (cores − 1 when unknown), lowered by a manual setting, at least 1. */
export function jsWorkerCount(device = {}, prefs = {}) {
  const cores = Number(globalThis.navigator?.hardwareConcurrency) || 2;
  let n = Math.floor(Number(device?.js_workers)) || Math.min(8, Math.max(1, cores - 1));
  if (Number.isInteger(prefs.workers)) n = Math.min(n, prefs.workers);
  return Math.max(1, n);
}

/**
 * The accelerator host. Extra options for tests and the benchmark: allowSoftwareGpu (let an explicit "gpu" request
 * or engine use a software adapter), createWorker (a Web Worker-like factory for js-workers), navigatorGpu.
 */
export function createAccelHost({ device = {}, settings = {}, allowSoftwareGpu = false, createWorker, navigatorGpu } = {}) {
  const prefs = browserPrefs(settings);
  const active = new Set();
  let gpuP = null; // {software: bool} → openGpu() result, once per host (again after a lost device)
  let gpuChain = Promise.resolve(); // one GPU run at a time: error scopes and buffers are per device
  let distrust = null; // why this host no longer uses the GPU: a GPU result here once differed from the JS kernel
  let disposed = false;
  const canWorker = () => typeof globalThis.Worker === "function" || Boolean(createWorker);
  const inWorker = () => typeof globalThis.document === "undefined" && typeof globalThis.WorkerGlobalScope !== "undefined";

  function gpuContext(allowSoftware) {
    if (gpuP) {
      return gpuP.then((ctx) => {
        if (ctx.ok && ctx.lost) { gpuP = null; return gpuContext(allowSoftware); }
        if (!ctx.ok && ctx.software && allowSoftware) { gpuP = null; return gpuContext(allowSoftware); }
        if (ctx.ok && ctx.info.software && !allowSoftware) return { ok: false, software: true, info: ctx.info, reason: "only a software adapter" };
        return ctx;
      });
    }
    gpuP = openGpu({ allowSoftware, gpu: navigatorGpu ?? globalThis.navigator?.gpu });
    return gpuP;
  }

  /** The kernel on JS: a worker pool, or this thread when this is a worker that cannot start workers. */
  async function onCpu(plan, { signal, onProgress, engine }) {
    const workers = jsWorkerCount(device, prefs);
    if (engine !== "js" && canWorker()) {
      const used = Math.max(1, Math.min(workers, plan.ids.length));
      const counts = await runJsWorkers(plan, { workers: used, signal, onProgress, createWorker });
      return { counts, engine: "js-workers", device: `cpu: ${used} JS worker${used > 1 ? "s" : ""}` };
    }
    if (engine !== "js" && !inWorker()) throw new Error("this page cannot start a worker; the kernel would block the page");
    let done = 0;
    const counts = await countRange(plan, 0, plan.ids.length, {
      shouldStop: () => signal?.aborted,
      onPathway: () => onProgress?.(++done / plan.ids.length),
    });
    if (signal?.aborted) throw abortError();
    return { counts, engine: "js", device: "cpu: 1 thread" };
  }

  /**
   * The GPU run plus the host's own check of the first and the last pathway: {counts, …}, or {unavailable} when there
   * is no usable adapter, or {mismatch} when the check disagrees (the GPU's counts are then discarded).
   */
  async function onGpu(plan, { signal, onProgress, allowSoftware }) {
    const ctx = await gpuContext(allowSoftware);
    if (!ctx.ok) return { unavailable: ctx.reason || "no WebGPU adapter" };
    const turn = gpuChain.then(() => runWebGPU(plan, ctx, { signal, onProgress: (f) => onProgress?.(0.98 * f) }));
    gpuChain = turn.catch(() => {});
    const counts = await turn;
    const n = plan.ids.length;
    const sample = n > 1 ? [0, n - 1] : n === 1 ? [0] : [];
    const check = sample.length ? await onCpu(subPlan(plan, sample), { signal, engine: canWorker() ? "js-workers" : "js" }) : { counts: [] };
    const diff = sample.findIndex((p, i) => check.counts[i] !== counts[p]);
    if (diff >= 0) {
      const p = sample[diff];
      return { mismatch: `${plan.ids[p]}: the GPU counted ${counts[p]}, the JS kernel ${check.counts[diff]}` };
    }
    onProgress?.(1);
    return { counts, engine: "webgpu", device: ctx.label, checked: sample.map((p) => plan.ids[p]) };
  }

  return {
    /** The kernels this host runs: none where SHA-512 (crypto.subtle) is missing, i.e. outside a secure context. */
    kernels() {
      return !disposed && globalThis.crypto?.subtle ? [...KERNELS] : [];
    },

    /**
     * Run `kernel` on `input`: {at_least, engine, device, kernel_digest, ms} (+ checked: the pathways the host
     * recomputed after a GPU run; fallback: {from, reason} when the first engine was not used). Options: signal,
     * onProgress(fraction), prefer ("auto" | "gpu" | "cpu"), engine ("auto" | "webgpu" | "js-workers" | "js").
     */
    async run(kernel, input, { signal, onProgress, prefer, engine = "auto" } = {}) {
      const t0 = now();
      if (disposed) throw new Error("the accelerator host was closed");
      if (kernel !== KERNEL) throw new Error(`unknown kernel ${JSON.stringify(kernel)}; this host runs ${KERNELS.join(", ")}`);
      if (!ENGINES.includes(engine)) throw new Error(`unknown engine ${JSON.stringify(engine)}`);
      const plan = prepare(input); // throws KernelInputError, naming the field
      const controller = new AbortController();
      const onAbort = () => controller.abort();
      if (signal?.aborted) throw abortError();
      signal?.addEventListener?.("abort", onAbort, { once: true });
      active.add(controller);
      const opts = { signal: controller.signal, onProgress, engine };
      try {
        const digest = kernelDigest();
        const want = chooseEngine({
          paths: plan.ids.length, prefer: prefer || prefs.prefer, gpuSetting: prefs.gpu, engine,
          gpu: ["hardware", "software", "none"].includes(device?.gpu) ? device.gpu : "unknown", allowSoftware: allowSoftwareGpu,
          measured: gpuMeasuredFaster(),
        });
        let out = null;
        let fallback = null;
        if (want === "webgpu" && distrust) {
          fallback = { from: "webgpu", reason: `not used on this page since an earlier mismatch (${distrust})` };
        } else if (want === "webgpu") {
          // software only when asked for by name (prefer "gpu" or engine "webgpu") and allowed; never on "auto"
          const asked = engine === "webgpu" || (prefer || prefs.prefer) === "gpu";
          try {
            const got = await onGpu(plan, { ...opts, allowSoftware: allowSoftwareGpu && asked });
            if (got.counts) out = got;
            else if (got.mismatch) {
              distrust = got.mismatch;
              fallback = { from: "webgpu", reason: `mismatch: ${got.mismatch}` };
            }
            else if (engine === "webgpu" || asked) fallback = { from: "webgpu", reason: got.unavailable };
          } catch (err) {
            if (isAbort(err) || controller.signal.aborted) throw abortError();
            fallback = { from: "webgpu", reason: String(err?.message || err) };
          }
        }
        if (!out) out = await onCpu(plan, opts);
        if (controller.signal.aborted) throw abortError();
        const result = { at_least: out.counts, engine: out.engine, device: out.device, kernel_digest: await digest, ms: Math.round(now() - t0) };
        if (out.checked) result.checked = out.checked;
        if (fallback) result.fallback = fallback;
        return result;
      } catch (err) {
        if (controller.signal.aborted || isAbort(err)) throw abortError();
        throw err;
      } finally {
        active.delete(controller);
        signal?.removeEventListener?.("abort", onAbort);
      }
    },

    /** Stop every run and free the GPU device. */
    dispose() {
      disposed = true;
      for (const c of active) c.abort();
      active.clear();
      const old = gpuP;
      gpuP = null;
      old?.then((ctx) => gpuChain.then(() => { try { ctx.device?.destroy?.(); } catch { /* gone */ } })).catch(() => {});
    },
  };
}

function now() {
  return globalThis.performance?.now ? performance.now() : Date.now();
}
