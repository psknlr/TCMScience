// The Worker's decision, before a sequence tool runs, whether its integer counts are prepared on the GPU (WebGPU,
// asynchronous) or left to Python, which then counts with its CPU kernel (WebAssembly SIMD where the engine has it,
// else JavaScript; synchronous) or itself. The scheduler weighs them by a cost model and by what this device has
// measured; the prepared packet carries the SHA-256 of the input it was computed from, and Python uses it only for
// that input (tcmstudio.browser_compute).

import { ComputeScheduler } from "./scheduler.js";
import { simdStatus } from "./simd.js";
import { SequenceCompute, WEBGPU_TOOLS, planSequenceCompute } from "../runtime/webgpu.js";

export const GPU_MIN_OPERATIONS = 32 * 1024; // below this the GPU's set-up costs more than the work

// Rough costs in milliseconds, replaced by what this device measures (scheduler.record). Python's per-comparison
// cost is Pyodide's; every accelerated path also pays to hand its counts to Python (JSON, then a check).
const PY_MS_PER_OP = { "native.distance_matrix": 1.5e-4, "native.hamming_distance": 8e-5, "native.gc_content": 3e-6 };
const JS_MS_PER_OP = 1.5e-6;
const SIMD_MS_PER_OP = 2e-7;
const GPU_MS_PER_OP = 1e-7;
const GPU_FIXED_MS = 3;
const HANDOFF_MS_PER_COUNT = 2.5e-4;

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

function webgpuBackend(gpu) {
  return {
    id: "webgpu", kind: "webgpu", exact: true,
    supports(task) {
      if (task.units < GPU_MIN_OPERATIONS) return { ok: false, reason: "Small workload runs on CPU" };
      return { ok: true };
    },
    cost(task) { return GPU_FIXED_MS + task.units * GPU_MS_PER_OP + task.counts * HANDOFF_MS_PER_COUNT; },
    async run(task, input) {
      const out = await gpu.prepare(input.tool, input.args, { isCancelled: input.isCancelled, force: true });
      if (!out) {
        const reason = gpu.fallbackReason || "WebGPU produced no counts";
        throw Object.assign(new Error(reason), { code: /device (was )?lost/i.test(reason) ? "device_lost" : "webgpu" });
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
  constructor({ gpu = new SequenceCompute(), caps = null, now, simd = simdStatus } = {}) {
    this.gpu = gpu;
    this.scheduler = new ComputeScheduler({
      caps, now, backends: [webgpuBackend(gpu), simdBackend(simd), cpuKernelBackend, referenceBackend],
    });
  }

  /**
   * → {packet (for Python, or null), plan (the scheduler's provenance, for the receipt)}. Throws only when the call
   * was cancelled; every other failure leaves the counting to Python.
   */
  async prepare(tool, args, { preference = "auto", isCancelled } = {}) {
    if (!WEBGPU_TOOLS.includes(tool)) return { packet: null, plan: null };
    const pref = normalizePreference(preference);
    const plan = planSequenceCompute(tool, args);
    if (!plan) return { packet: null, plan: { op: "sequence.counts", requested: pref, executed: null, fallback_reason: "Not an input for the GPU kernel (its letters or its size); Python counts it, with its CPU kernel where that applies" } };
    const task = {
      op: "sequence.counts", tool, units: plan.operations, counts: plan.records * 4, bytes: plan.inputBytes + plan.outputBytes,
      exact: true, preference: pref,
    };
    let outcome;
    try {
      outcome = await this.scheduler.run(task, { tool, args, isCancelled });
    } catch (err) {
      if (err?.name === "AbortError" || err?.code === "cancelled" || isCancelled?.()) throw Object.assign(new Error("sequence computation cancelled"), { code: "cancelled" });
      return { packet: null, plan: err?.provenance || null };
    }
    const { result, provenance } = outcome;
    if (!result) return { packet: null, plan: provenance };
    const packet = { ...result, input_sha256: await inputDigest(tool, plan.seqs, plan.window) };
    return { packet, plan: provenance };
  }

  /**
   * A measured run of a path that ran inside Python, so later choices learn: a CPU kernel by its own time (what the
   * GPU's preparation is weighed against), Python by the whole call.
   */
  learn(tool, args, backend, ms) {
    const plan = planSequenceCompute(tool, args);
    if (plan && IN_PYTHON.has(backend)) this.scheduler.record(backend, "sequence.counts", plan.operations, ms);
  }

  stop() { this.gpu.stop(); }
}
