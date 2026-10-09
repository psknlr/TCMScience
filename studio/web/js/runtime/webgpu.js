// Exact integer sequence reductions; policy, input validation and model transforms stay in Python.
// A detected API alone is not an execution backend: only successfully read GPU counts earn WebGPU receipts.
export const WEBGPU_TOOLS = Object.freeze(["native.distance_matrix", "native.hamming_distance", "native.gc_content"]);
const MAX_BYTES = 64 * 1024 * 1024;
const MAX_COUNT_BYTES = 8 * 1024 * 1024; // Readback also becomes JSON/Python integers; bound that memory amplification.
const MAX_OPERATIONS = 128 * 1024 * 1024;
const MIN_OPERATIONS = 32 * 1024; // Small workloads are faster on the already-running CPU.
const U = { MAP_READ: 1, COPY_SRC: 4, COPY_DST: 8, UNIFORM: 64, STORAGE: 128 };
const SHADER = `
struct Params { width: u32, n: u32, mode: u32, window: u32, records: u32, dispatch_x: u32, p0: u32, p1: u32 }
@group(0) @binding(0) var<storage, read> bases: array<u32>;
@group(0) @binding(1) var<storage, read_write> counts: array<vec4<u32>>;
@group(0) @binding(2) var<uniform> params: Params;
var<workgroup> partial: array<vec4<u32>, 64>;
@compute @workgroup_size(64)
fn main(@builtin(workgroup_id) group: vec3<u32>, @builtin(local_invocation_id) local: vec3<u32>) {
  let record = group.x + group.y * params.dispatch_x;
  if (record >= params.records) { return; }
  let i = record / params.n;
  let j = record % params.n;
  if (params.mode == 0u && i >= j) { return; }
  var start = 0u;
  var width = params.width;
  if (params.mode == 2u && record > 0u) { start = record - 1u; width = params.window; }
  var tally = vec4<u32>(0u);
  for (var k = local.x; k < width; k += 64u) {
    if (params.mode == 0u) {
      let a = bases[i * params.width + k];
      let b = bases[j * params.width + k];
      if (a != 0u && b != 0u) {
        tally.x += 1u;
        if (a != b) {
          tally.y += 1u;
          if ((a == 65u && b == 71u) || (a == 71u && b == 65u) ||
              (a == 67u && b == 84u) || (a == 84u && b == 67u)) { tally.z += 1u; }
        }
      }
    } else if (params.mode == 1u) {
      if (bases[k] != bases[params.width + k]) { tally.x += 1u; }
    } else {
      let a = bases[start + k];
      if (a == 71u || a == 67u || a == 83u) { tally.x += 1u; }
    }
  }
  partial[local.x] = tally;
  workgroupBarrier();
  for (var stride = 32u; stride > 0u; stride /= 2u) {
    if (local.x < stride) { partial[local.x] += partial[local.x + stride]; }
    workgroupBarrier();
  }
  if (local.x == 0u) { counts[record] = partial[0]; }
}`;

function cancelled(check) {
  if (check?.()) { const error = new Error("sequence computation cancelled"); error.code = "cancelled"; throw error; }
}
function clean(s, distance = false) {
  if (typeof s !== "string" || !s.trim()) return null;
  const normalized = s.replace(/\s/g, "").toUpperCase();
  return distance ? normalized.replaceAll("U", "T") : normalized;
}

/** Planning rejects unsupported/invalid inputs without masking the Python validator's actual error. */
export function planSequenceCompute(tool, args = {}) {
  if (!WEBGPU_TOOLS.includes(tool) || !args || typeof args !== "object") return null;
  let mode, seqs, window = 0;
  if (tool === "native.distance_matrix") {
    if (!Array.isArray(args.sequences) && (!args.sequences || typeof args.sequences !== "object")) return null;
    const source = Array.isArray(args.sequences) ? args.sequences : Object.values(args.sequences);
    seqs = source.map((s) => clean(s, true));
    if (seqs.length < 2 || !["p", "jc69", "k2p"].includes(args.model ?? "p")) return null;
    mode = 0;
  } else if (tool === "native.hamming_distance") { mode = 1; seqs = [clean(args.a), clean(args.b)]; }
  else { mode = 2; seqs = [clean(args.sequence)]; window = args.window ?? 0; }
  if (seqs.some((s) => !s) || seqs.some((s) => s.length !== seqs[0].length)) return null;
  if (mode !== 0 && seqs.some((s) => !/^[ACGTUNRYKMSWBDHV]+$/.test(s))) return null;
  if (!Number.isSafeInteger(window) || window < 0 || window > seqs[0].length) return null;
  const width = seqs[0].length, n = seqs.length;
  const records = mode === 0 ? n * n : mode === 2 && window > 0 ? width - window + 2 : 1;
  const operations = mode === 0 ? n * (n - 1) / 2 * width : mode === 2 ? width + (records - 1) * window : width;
  const inputBytes = n * width * 4, outputBytes = records * 16;
  if (!Number.isSafeInteger(operations) || operations > MAX_OPERATIONS || inputBytes > MAX_BYTES || outputBytes > MAX_COUNT_BYTES || width >= 2 ** 32) return null;
  return { tool, mode, seqs, width, n, window, records, operations, inputBytes, outputBytes };
}

/** CPU reference for checking the GPU reduction, not the runtime's CPU fallback (which is original Python). */
export function referenceCounts(plan) {
  const counts = new Uint32Array(plan.records * 4);
  if (plan.mode === 0) {
    for (let i = 0; i < plan.n; i++) for (let j = i + 1; j < plan.n; j++) {
      const at = (i * plan.n + j) * 4;
      for (let k = 0; k < plan.width; k++) {
        const a = plan.seqs[i][k], b = plan.seqs[j][k];
        if (!"ACGT".includes(a) || !"ACGT".includes(b)) continue;
        counts[at]++;
        if (a !== b) { counts[at + 1]++; if (["AG", "GA", "CT", "TC"].includes(a + b)) counts[at + 2]++; }
      }
    }
  } else if (plan.mode === 1) {
    for (let k = 0; k < plan.width; k++) if (plan.seqs[0][k] !== plan.seqs[1][k]) counts[0]++;
  } else {
    for (let r = 0; r < plan.records; r++) {
      const start = r === 0 ? 0 : r - 1, width = r === 0 ? plan.width : plan.window;
      for (let k = 0; k < width; k++) if ("GCS".includes(plan.seqs[0][start + k])) counts[r * 4]++;
    }
  }
  return counts;
}

export class SequenceCompute {
  #nav; #device = null; #pipeline = null; #deviceP = null; #lastReason = null; #timeout; #allowSoftware;
  // allowSoftware is used only by the explicit WGSL smoke harness; the runtime never enables it.
  constructor({ navigator = globalThis.navigator, timeoutMs = 10_000, allowSoftware = false } = {}) {
    this.#nav = navigator; this.#timeout = timeoutMs; this.#allowSoftware = allowSoftware;
  }
  get fallbackReason() { return this.#lastReason; }
  async #getDevice() {
    if (this.#device) return this.#device;
    if (!this.#deviceP) {
      this.#deviceP = (async () => {
        if (!this.#nav?.gpu?.requestAdapter) throw new Error("WebGPU API unavailable");
        const adapter = await limited(this.#nav.gpu.requestAdapter({ powerPreference: "high-performance" }), this.#timeout);
        if (!adapter) throw new Error("No WebGPU adapter available");
        const info = adapter.info || {};
        if (!this.#allowSoftware && (adapter.isFallbackAdapter || info.isFallbackAdapter || /swiftshader|llvmpipe|lavapipe|software|microsoft basic render/i.test(`${info.vendor} ${info.architecture} ${info.description}`))) {
          throw new Error("WebGPU adapter is a software CPU renderer");
        }
        const request = adapter.requestDevice();
        const device = await limited(request, this.#timeout, (late) => late?.destroy());
        this.#device = device;
        device.lost.then(() => { if (this.#device === device) { this.#device = null; this.#pipeline = null; } });
        return device;
      })().finally(() => { this.#deviceP = null; });
    }
    return this.#deviceP;
  }

  async prepare(tool, args, { preference = "auto", isCancelled, force = false } = {}) {
    this.#lastReason = null;
    cancelled(isCancelled);
    if (preference === "cpu") { this.#lastReason = "CPU selected"; return null; }
    const plan = planSequenceCompute(tool, args);
    if (!plan) return null;
    if (!force && plan.operations < MIN_OPERATIONS) { this.#lastReason = "Small workload runs on CPU"; return null; }
    try {
      const device = await this.#getDevice();
      cancelled(isCancelled);
      const counts = await this.#run(device, plan, isCancelled);
      cancelled(isCancelled);
      return { tool, width: plan.width, n: plan.n, window: plan.window, counts: Array.from(counts), backend: "webgpu", precision: "uint32 exact counts; Python float64 transforms" };
    } catch (error) {
      if (error?.code === "cancelled" || isCancelled?.()) { cancelled(() => true); }
      this.#lastReason = String(error?.message || error);
      return null; // The worker dispatches the original Python implementation.
    }
  }

  async #run(device, plan, isCancelled) {
    const maxBuffer = device.limits.maxStorageBufferBindingSize;
    const maxSize = device.limits.maxBufferSize;
    const maxGroups = device.limits.maxComputeWorkgroupsPerDimension;
    if (plan.inputBytes > maxBuffer || plan.outputBytes > maxBuffer || Math.max(plan.inputBytes, plan.outputBytes) > maxSize || plan.records > maxGroups ** 2) throw new Error("Workload exceeds this GPU's buffer/dispatch limits");
    const dispatchX = Math.min(plan.records, maxGroups), dispatchY = Math.ceil(plan.records / dispatchX);
    const buffers = [];
    const allocate = (size, usage) => { const b = device.createBuffer({ size, usage }); buffers.push(b); return b; };
    let scoped = false;
    try {
      device.pushErrorScope("validation"); scoped = true;
      if (!this.#pipeline) {
        const module = device.createShaderModule({ code: SHADER, label: "TCMScience exact sequence counts" });
        this.#pipeline = await limited(device.createComputePipelineAsync({ layout: "auto", compute: { module, entryPoint: "main" } }), this.#timeout);
        if (this.#device !== device) { this.#pipeline = null; throw new Error("WebGPU device was lost during shader compilation"); }
      }
      cancelled(isCancelled);
      const values = new Uint32Array(plan.n * plan.width);
      for (let i = 0; i < plan.n; i++) for (let k = 0; k < plan.width; k++) {
        if ((k & 4095) === 0) cancelled(isCancelled);
        const base = plan.seqs[i][k];
        values[i * plan.width + k] = plan.mode === 0 && !"ACGT".includes(base) ? 0 : base.charCodeAt(0);
      }
      const input = allocate(plan.inputBytes, U.STORAGE | U.COPY_DST);
      const output = allocate(plan.outputBytes, U.STORAGE | U.COPY_SRC | U.COPY_DST);
      const params = allocate(32, U.UNIFORM | U.COPY_DST);
      const read = allocate(plan.outputBytes, U.COPY_DST | U.MAP_READ);
      device.queue.writeBuffer(input, 0, values);
      device.queue.writeBuffer(params, 0, new Uint32Array([plan.width, plan.n, plan.mode, plan.window, plan.records, dispatchX, 0, 0]));
      const group = device.createBindGroup({ layout: this.#pipeline.getBindGroupLayout(0), entries: [
        { binding: 0, resource: { buffer: input } }, { binding: 1, resource: { buffer: output } }, { binding: 2, resource: { buffer: params } },
      ] });
      const encoder = device.createCommandEncoder();
      const pass = encoder.beginComputePass();
      pass.setPipeline(this.#pipeline); pass.setBindGroup(0, group); pass.dispatchWorkgroups(dispatchX, dispatchY); pass.end();
      encoder.copyBufferToBuffer(output, 0, read, 0, plan.outputBytes);
      device.queue.submit([encoder.finish()]);
      await this.#readback(read, device, isCancelled);
      const counts = new Uint32Array(read.getMappedRange().slice(0));
      read.unmap();
      const validation = await device.popErrorScope(); scoped = false;
      if (validation) throw new Error(validation.message);
      // Cheap deterministic samples detect failed/corrupt kernels without repeating the full pairwise workload.
      verifyCounts(plan, counts);
      return counts;
    } finally {
      if (scoped) await device.popErrorScope().catch(() => {});
      for (const buffer of buffers) buffer.destroy();
    }
  }
  async #readback(read, device, check) {
    let timer;
    const abort = new Promise((_, reject) => {
      timer = setInterval(() => { try { cancelled(check); } catch (e) { reject(e); } }, 16);
    });
    try {
      await limited(Promise.race([read.mapAsync(1), abort, device.lost.then((info) => { throw new Error(`WebGPU device lost: ${info.message || info.reason}`); })]), this.#timeout);
    } finally { clearInterval(timer); }
  }
  stop() { this.#device?.destroy(); this.#device = null; this.#pipeline = null; }
}

function verifyCounts(plan, counts) {
  if (counts.length !== plan.records * 4) throw new Error("GPU returned an invalid count buffer");
  const records = plan.mode === 0 ? [1, (plan.n - 2) * plan.n + plan.n - 1] : [0, plan.records - 1];
  for (const record of new Set(records)) {
    let total = 0, diffs = 0, transitions = 0;
    const i = Math.floor(record / plan.n), j = record % plan.n;
    const width = plan.mode === 2 && record > 0 ? plan.window : plan.width;
    const start = plan.mode === 2 && record > 0 ? record - 1 : 0;
    for (let k = 0; k < width; k++) {
      if (plan.mode === 0) {
        const a = plan.seqs[i][k], b = plan.seqs[j][k];
        if (!"ACGT".includes(a) || !"ACGT".includes(b)) continue;
        total++;
        if (a !== b) { diffs++; if (["AG", "GA", "CT", "TC"].includes(a + b)) transitions++; }
      } else if (plan.mode === 1) { if (plan.seqs[0][k] !== plan.seqs[1][k]) total++; }
      else if ("GCS".includes(plan.seqs[0][start + k])) total++;
    }
    const at = record * 4;
    if (counts[at] !== total || counts[at + 1] !== diffs || counts[at + 2] !== transitions) throw new Error("GPU integer counts failed their CPU sample check");
  }
}

function limited(promise, timeoutMs, onLate) {
  let timer, expired = false;
  const operation = Promise.resolve(promise).then((result) => { if (expired) onLate?.(result); return result; });
  return Promise.race([operation, new Promise((_, reject) => { timer = setTimeout(() => { expired = true; reject(new Error("WebGPU operation timed out")); }, timeoutMs); })]).finally(() => clearTimeout(timer));
}
