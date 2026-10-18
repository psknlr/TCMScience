// The Worker's decision, before a sequence tool runs, whether its integer counts are prepared on the GPU (WebGPU,
// asynchronous) or left to Python, which then counts with its CPU kernel (WebAssembly SIMD where the engine has it,
// else JavaScript; synchronous) or itself. The scheduler weighs them by a cost model and by what this device has
// measured; the prepared packet carries the SHA-256 of the input it was computed from, and Python uses it only for
// that input (tcmstudio.browser_compute).
//
// What the GPU did is evidence on the ladder of compute/capabilities.js (dispatched, validated, verified, accelerated),
// written per call into receipt.compute.gpu and receipt.execution.device.evidence (decorateReceipt). "accelerated"
// comes only from a measurement on this device: this call's GPU time against a CPU kernel's measured time for the same
// tool and size class, both including their transfer into and out of Python. The cost model's GPU constants below are
// guesses that order the first tries; they never make anything "accelerated".

import { ComputeScheduler } from "./scheduler.js";
import { simdStatus } from "./simd.js";
import { EVIDENCE, evidenceLevel } from "./capabilities.js";
import { SequenceCompute, WEBGPU_TOOLS, planSequenceCompute } from "../runtime/webgpu.js";

export const GPU_MIN_OPERATIONS = 32 * 1024; // below this the GPU's set-up costs more than the work
const OP = "sequence.counts";
const CPU_KERNELS = ["wasm-simd", "js"]; // the CPU baseline a GPU run is compared with (never the slower Python)
const SOFTWARE_REFUSED = "WebGPU adapter is a software CPU renderer"; // SequenceCompute's refusal, and supports()'s

// Rough costs in milliseconds, replaced by what this device measures (scheduler.record). Python's per-comparison
// cost is Pyodide's; every accelerated path also pays to hand its counts to Python (JSON, then a check). The CPU
// constants were measured in Chromium (BROWSER_COMPUTE.md §5); GPU_MS_PER_OP and GPU_FIXED_MS were not (no hardware GPU).
const PY_MS_PER_OP = { "native.distance_matrix": 1.5e-4, "native.hamming_distance": 8e-5, "native.gc_content": 3e-6 };
const JS_MS_PER_OP = 1.5e-6;
const SIMD_MS_PER_OP = 2e-7;
const GPU_MS_PER_OP = 1e-7;
const GPU_FIXED_MS = 3;
const HANDOFF_MS_PER_COUNT = 2.5e-4;

/**
 * The scheduler's operation for one tool's counts ("sequence.counts:distance_matrix"). Each tool's comparisons cost
 * differently (a pair of sequences, a pair of letters, a window), so what was measured, and every comparison drawn from
 * it, is kept per tool: a CPU kernel timed on gc_content never stands for distance_matrix at the same size.
 */
export function countsOp(tool) {
  return `${OP}:${String(tool).replace(/^native\./, "")}`;
}

/** What the Python call is told: whether its CPU kernels may run. "reference" asks for the Python implementation. */
export function kernelOptions(preference) {
  return { kernels: preference !== "reference" };
}

export function normalizePreference(p) {
  return p === "cpu" || p === "reference" ? p : "auto";
}

/** The SHA-256 Python recomputes (tcmstudio.browser_compute.input_digest). */
export async function inputDigest(tool, sequences, window = 0, subtle = globalThis.crypto?.subtle) {
  const text = `${sequences.join("\n")}\u0000${tool}\u0000${window}`;
  const d = new Uint8Array(await subtle.digest("SHA-256", new TextEncoder().encode(text)));
  let s = "";
  for (const b of d) s += b.toString(16).padStart(2, "0");
  return s;
}

const now = () => (globalThis.performance?.now ? globalThis.performance.now() : Date.now());
const ms3 = (x) => (Number.isFinite(x) ? Math.round(x * 1000) / 1000 : null);

function webgpuBackend(gpu) {
  return {
    id: "webgpu", kind: "webgpu", exact: true,
    // Timed after the call (SequenceAccelerator.settle), not around prepare(): the GPU's own work, the digest and the
    // hand-off of its counts into Python, as a CPU kernel's time includes its own trip both ways. A cold run (device,
    // pipeline, known-answer test) and a packet Python did not use are not recorded.
    defers: true,
    supports(task) {
      if (task.units < GPU_MIN_OPERATIONS) return { ok: false, reason: "Small workload runs on CPU" };
      // what an earlier call found out about this adapter, so it is not asked again
      if (gpu.adapter?.kind === "software" && !gpu.allowsSoftware) return { ok: false, reason: SOFTWARE_REFUSED };
      if (gpu.knownAnswer?.ok === false) return { ok: false, reason: `WebGPU known-answer test failed on this device: ${gpu.knownAnswer.reason}` };
      return { ok: true };
    },
    cost(task) { return GPU_FIXED_MS + task.units * GPU_MS_PER_OP + task.counts * HANDOFF_MS_PER_COUNT; },
    async run(task, input) {
      const out = await gpu.prepare(input.tool, input.args, { isCancelled: input.isCancelled, force: true });
      if (!out) {
        const reason = gpu.fallbackReason || "WebGPU produced no counts";
        const code = /device (was )?lost/i.test(reason) ? "device_lost" : gpu.knownAnswer?.ok === false ? "known_answer_failed" : "webgpu";
        throw Object.assign(new Error(reason), { code });
      }
      return out;
    },
  };
}

// The paths that run inside Python: run() prepares nothing, the call computes in Python, and the Worker reports what
// that took (learn). compute/bridge.js uses the SIMD kernel whenever the engine has SIMD, so "js" is chosen only
// where "wasm-simd" cannot run.
function simdBackend(status) {
  return {
    id: "wasm-simd", kind: "wasm-simd", exact: true, defers: true,
    supports() {
      const s = status();
      return s.available ? { ok: true } : { ok: false, reason: `No WebAssembly SIMD here (${s.reason || "unknown"})` };
    },
    cost(task) { return task.units * SIMD_MS_PER_OP + task.counts * HANDOFF_MS_PER_COUNT; },
    run: async () => null,
  };
}
const cpuKernelBackend = {
  id: "js", kind: "js", exact: true, defers: true,
  supports: () => ({ ok: true }),
  cost(task) { return task.units * JS_MS_PER_OP + task.counts * HANDOFF_MS_PER_COUNT; },
  run: async () => null,
};
const referenceBackend = {
  id: "python", kind: "python", exact: true, defers: true,
  supports: () => ({ ok: true }),
  cost(task) { return task.units * (PY_MS_PER_OP[task.tool] ?? 1e-4); },
  run: async () => null,
};

const IN_PYTHON = new Set(["wasm-simd", "js", "python"]);

export class SequenceAccelerator {
  #session = { dispatched: 0, validated: 0, rejected: 0, measured: 0, accelerated: 0, last_reason: null };

  #now;

  constructor({ gpu = new SequenceCompute(), caps = null, now: clock, simd = simdStatus } = {}) {
    this.gpu = gpu;
    this.#now = clock || now;
    this.scheduler = new ComputeScheduler({
      caps, now: clock, backends: [webgpuBackend(gpu), simdBackend(simd), cpuKernelBackend, referenceBackend],
    });
  }

  /**
   * → {packet (for Python, or null), plan (the scheduler's provenance, for the receipt), gpu (what the GPU did before
   * the call, for settle(); null for a tool without a GPU path)}. Throws only when the call was cancelled; every other
   * failure leaves the counting to Python.
   */
  async prepare(tool, args, { preference = "auto", isCancelled } = {}) {
    if (!WEBGPU_TOOLS.includes(tool)) return { packet: null, plan: null, gpu: null };
    const pref = normalizePreference(preference);
    const plan = planSequenceCompute(tool, args);
    if (!plan) {
      const provenance = { op: countsOp(tool), requested: pref, executed: null, fallback_reason: "Not an input for the GPU kernel (its letters or its size); Python counts it, with its CPU kernel where that applies" };
      return { packet: null, plan: provenance, gpu: this.#before(provenance, null) };
    }
    const task = {
      op: countsOp(tool), tool, units: plan.operations, counts: plan.records * 4, bytes: plan.inputBytes + plan.outputBytes,
      exact: true, preference: pref,
    };
    let outcome;
    try {
      outcome = await this.scheduler.run(task, { tool, args, isCancelled });
    } catch (err) {
      if (err?.name === "AbortError" || err?.code === "cancelled" || isCancelled?.()) throw Object.assign(new Error("sequence computation cancelled"), { code: "cancelled" });
      const provenance = err?.provenance || null;
      return { packet: null, plan: provenance, gpu: this.#before(provenance, null) };
    }
    const { result, provenance } = outcome;
    if (!result) return { packet: null, plan: provenance, gpu: this.#before(provenance, null) };
    const t0 = this.#now();
    const packet = { ...result, input_sha256: await inputDigest(tool, plan.seqs, plan.window) };
    return { packet, plan: provenance, gpu: this.#before(provenance, ms3(this.#now() - t0)) };
  }

  /** A snapshot of what the GPU did for this call, before Python sees its counts. */
  #before(provenance, digestMs) {
    const tried = (provenance?.tried || []).find((r) => r.id === "webgpu") || null;
    return {
      considered: Boolean((provenance?.considered || []).some((r) => r.id === "webgpu")),
      tried: tried ? { outcome: tried.outcome, ...(tried.reason ? { reason: tried.reason } : {}) } : null,
      passed_over: (provenance?.passed_over || []).find((r) => r.id === "webgpu")?.reason || null,
      adapter: this.gpu.adapter || null,
      known_answer: this.gpu.knownAnswer || null,
      run: tried ? this.gpu.lastRun || null : null,
      digest_ms: digestMs,
    };
  }

  /**
   * A measured run of a path that ran inside Python, so later choices learn: a CPU kernel by its own time (what the
   * GPU's preparation is weighed against), Python by the whole call.
   */
  learn(tool, args, backend, ms) {
    const plan = planSequenceCompute(tool, args);
    if (plan && IN_PYTHON.has(backend)) this.scheduler.record(backend, countsOp(tool), plan.operations, ms);
  }

  /**
   * After Python returned: what the GPU did for this call (the object written to receipt.compute.gpu), and, for a warm
   * run whose counts Python used, the measurement the scheduler learns. `gpu` is prepare()'s, `compute` the receipt's
   * compute (Python wrote compute.gpu.handoff_ms when a packet arrived), `stringifyMs` the time to write the packet as
   * JSON. → null for a tool without a GPU path.
   *
   * gpu_ms is this call's GPU path as it happened: set-up when cold, packing, upload, dispatch, readback, the sample
   * check, the digest, the JSON of the packet, its crossing into Python and Python's parse and binding of it (the last
   * two are Python's handoff_ms; not Python's recount, which the CPU kernels' counts get too). accelerated is true only when the adapter is physical, its known-answer test passed, Python
   * used these counts, and a CPU kernel's time measured here for the same tool and size class is longer.
   */
  settle(tool, args, { gpu = null, compute = null, stringifyMs = 0 } = {}) {
    if (!gpu || !WEBGPU_TOOLS.includes(tool)) return null;
    const plan = planSequenceCompute(tool, args);
    const op = countsOp(tool);
    const run = gpu.run;
    const used = compute?.backend === "webgpu";
    const handoff = compute?.gpu?.handoff_ms;
    const s = this.#session;
    let gpuMs = null;
    if (run?.dispatched && Number.isFinite(run.work_ms)) {
      const parts = [run.work_ms, run.cold ? run.setup_ms : 0, gpu.digest_ms ?? 0, stringifyMs, Number.isFinite(handoff) ? handoff : 0];
      gpuMs = ms3(parts.reduce((a, b) => a + (Number.isFinite(b) ? b : 0), 0));
    }
    // a complete, warm measurement of counts Python used is what the scheduler learns
    const complete = used && gpuMs !== null && Number.isFinite(handoff) && Number.isFinite(gpu.digest_ms);
    if (complete && !run.cold && plan) this.scheduler.record("webgpu", op, plan.operations, gpuMs);
    // the baseline is a CPU kernel measured on this same tool: another tool's time at the same size is not comparable
    const base = plan ? this.scheduler.baseline(op, plan.operations, CPU_KERNELS) : null;
    const adapter = gpu.adapter || null;
    const ka = gpu.known_answer || this.gpu.knownAnswer || null;
    let accelerated = false, basis;
    if (!used) basis = "the GPU's counts were not used for this call";
    else if (adapter?.kind !== "physical") basis = adapter?.kind === "software" ? "a software renderer is never GPU acceleration" : "the adapter is unidentified, so it is not counted as a GPU";
    else if (ka?.ok !== true) basis = "the device has not passed its known-answer test";
    else if (!complete) basis = "this call's GPU time is incomplete (a step was not timed), so it is not compared";
    else if (!base) basis = `no CPU kernel has been measured for ${op.slice(OP.length + 1)} at this size on this device, so there is nothing to compare with`;
    else {
      accelerated = gpuMs < base.ms;
      basis = `measured on this device: ${gpuMs} ms on the GPU${run.cold ? " (including its one-time set-up)" : ""} against ${ms3(base.ms)} ms by the ${base.id} kernel for ${op.slice(OP.length + 1)} at the same size class, both including their transfer into and out of Python`;
    }
    const fallback = used ? null
      : compute?.gpu_rejected ? `Python did not use the GPU's counts: ${compute.gpu_rejected}`
      : run?.dispatched && gpu.tried?.outcome === "ok" ? "Python did not use the GPU's counts (the call ended before it reached them, or did not succeed)"
      : gpu.tried?.reason || gpu.passed_over
      || (gpu.considered && !gpu.tried ? "another backend was estimated cheaper for this size" : null)
      || (gpu.considered ? null : "the GPU was not considered for this input");
    const facts = {
      selected: Boolean(gpu.tried), reported: Boolean(adapter), dispatched: Boolean(run?.dispatched), validated: used,
      verified: used && ka?.ok === true, accelerated, adapter_kind: adapter?.kind || null,
    };
    if (run?.dispatched) s.dispatched += 1;
    if (used) s.validated += 1;
    if (run?.dispatched && compute?.gpu_rejected) s.rejected += 1;
    if (used && base && complete) s.measured += 1;
    if (accelerated) s.accelerated += 1;
    if (fallback) s.last_reason = String(fallback).slice(0, 300);
    return {
      adapter: adapter ? { kind: adapter.kind, vendor: adapter.vendor, architecture: adapter.architecture } : null,
      dispatched: facts.dispatched, validated: used, verified: ka ? ka.ok === true : null,
      gpu_ms: gpuMs, cold: Boolean(run?.cold),
      ...(Number.isFinite(run?.kernel_ms) ? { kernel_ms: run.kernel_ms } : {}),
      cpu_baseline_ms: base ? ms3(base.ms) : null, cpu_baseline_backend: base ? base.id : null,
      accelerated, basis, fallback_reason: fallback, evidence: evidenceLevel(facts),
    };
  }

  /** This session's GPU evidence (compute/capabilities.js webgpuEvidence reads it). */
  evidence() {
    const h = this.scheduler.health("webgpu");
    return { adapter: this.gpu.adapter || null, known_answer: this.gpu.knownAnswer || null, ...this.#session, set_aside: h.setAside ? h.reason : null };
  }

  stop() { this.gpu.stop(); }
}

/** The scheduler's provenance, short enough for a receipt: what was considered, passed over, tried and run. */
export function planSummary(p) {
  return {
    considered: (p.considered || []).map((r) => r.id),
    passed_over: (p.passed_over || []).map((r) => ({ id: r.id, reason: r.reason })),
    tried: (p.tried || []).map((r) => ({ id: r.id, outcome: r.outcome, ...(r.reason ? { reason: r.reason } : {}) })),
    chosen: p.executed?.id || null,
  };
}

function higher(a, b) {
  return EVIDENCE.indexOf(b) > EVIDENCE.indexOf(a) ? b : a;
}

const DEGRADED_PASS = /^set aside for this session|known-answer test failed/;

/**
 * Why this call's GPU path degraded, or null: the GPU was tried and failed (a lost device, a failed known-answer test,
 * a timeout or another error), was set aside after such a failure earlier in the session, or computed counts Python
 * then rejected. A path that was never the GPU's to take is not a degradation and stays compute.fallback_reason only:
 * CPU only or the reference asked for, a workload too small, an input the kernel does not take, a cheaper CPU kernel,
 * and a software adapter (refused on its first call, passed over after): a machine whose only adapter is a CPU
 * renderer has no GPU to fall back from.
 */
export function gpuDegradation(plan, compute) {
  if (compute?.gpu_rejected) return `Python did not use the GPU's counts: ${compute.gpu_rejected}`;
  const tried = (plan?.tried || []).find((r) => r.id === "webgpu" && r.outcome === "error" && !String(r.reason || "").startsWith(SOFTWARE_REFUSED));
  if (tried) return tried.reason || "the GPU failed";
  const aside = (plan?.passed_over || []).find((r) => r.id === "webgpu" && DEGRADED_PASS.test(String(r.reason || "")));
  return aside ? aside.reason : null;
}

/**
 * What the Worker adds to a compute tool's receipt once Python has returned (receipt.compute is written in Python; this
 * runs after the result was hashed, so no output changes). compute gains requested, plan, fallback_reason and gpu (the
 * settle() object, merged over what Python wrote there); receipt/1 says what compute says: execution.kernel is
 * compute.backend, execution.device.requested the acceleration preference, its evidence raised along the ladder only
 * when the GPU's counts were used and only as far as gpu.evidence shows. result.fallbacks gains a {what: "compute"}
 * entry only for a degradation (gpuDegradation): a routine CPU path is not one. → the receipt (changed in place).
 */
export function decorateReceipt(receipt, { tool, preference = "auto", plan = null, packet = null, gpu = null } = {}) {
  if (!receipt || typeof receipt !== "object") return receipt;
  const c = receipt.compute ??= { backend: "pyodide", operation: tool, precision: "Python float64" };
  c.requested = preference;
  if (plan) c.plan = planSummary(plan);
  if (!packet && plan?.fallback_reason) c.fallback_reason = plan.fallback_reason;
  else if (!packet && plan?.passed_over?.length) c.fallback_reason = plan.passed_over.find((r) => r.id === "webgpu")?.reason;
  if (!c.fallback_reason) delete c.fallback_reason;
  if (gpu) c.gpu = { ...(c.gpu && typeof c.gpu === "object" ? c.gpu : {}), ...gpu };
  const ex = receipt.execution;
  if (ex && typeof ex === "object") {
    ex.kernel = c.backend ?? null;
    const d = ex.device;
    if (d && typeof d === "object") {
      d.requested = preference;
      if (c.backend === "webgpu" && gpu?.validated && EVIDENCE.includes(gpu.evidence)) d.evidence = higher(d.evidence, gpu.evidence);
    }
  }
  const r = receipt.result;
  const degraded = gpuDegradation(plan, c);
  if (degraded && r && typeof r === "object") {
    const fallbacks = Array.isArray(r.fallbacks) ? r.fallbacks : (r.fallbacks = []);
    const reason = String(degraded).slice(0, 600);
    if (!fallbacks.some((f) => f?.what === "compute" && f.reason === reason)) fallbacks.push({ what: "compute", reason });
  }
  return receipt;
}
