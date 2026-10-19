// Exact integer sequence reductions; policy, input validation and model transforms stay in Python.
// A detected API alone is not an execution backend: only successfully read GPU counts earn WebGPU receipts.
export const WEBGPU_TOOLS = Object.freeze(["native.distance_matrix", "native.hamming_distance", "native.gc_content"]);
const MAX_BYTES = 64 * 1024 * 1024;
const MAX_COUNT_BYTES = 8 * 1024 * 1024; // Readback also becomes JSON/Python integers; bound that memory amplification.
const MAX_OPERATIONS = 128 * 1024 * 1024;
const MIN_OPERATIONS = 32 * 1024; // Small workloads are faster on the already-running CPU.
const U = { MAP_READ: 1, COPY_SRC: 4, COPY_DST: 8, UNIFORM: 64, STORAGE: 128, QUERY_RESOLVE: 512 };
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

// ------------------------------------------------------------------------------------------- what the adapter is

// The one classifier of WebGPU adapters: the probe, the runtime, the Worker's device report, the benchmark and the
// smoke harness all use it, so no two places can disagree about the same adapter.
//   software      a CPU renderer named as such (SwiftShader, llvmpipe, lavapipe, softpipe, WARP, the Microsoft Basic
//                 Render Driver, anything calling itself software), or an adapter the browser marks as a fallback
//                 adapter. Never GPU acceleration, whatever its timings.
//   physical      an adapter that names a hardware GPU vendor or product line. This is the browser's own report about
//                 the adapter, not a proof of the silicon: the ladder above it (dispatched, validated, verified,
//                 accelerated) is what a computation shows.
//   unidentified  everything else: no names at all (browsers may withhold them), or names this list does not know.
//                 It is never reported as a GPU and never as physical; its results are checked like any other.
const SOFTWARE_ADAPTER = /swiftshader|llvmpipe|lavapipe|softpipe|software|microsoft basic render|\bwarp\b/i;
const HARDWARE_VENDOR = /\b(?:nvidia|geforce|quadro|amd|radeon|intel|apple|qualcomm(?:-pci|-acpi)?|adreno|arm|mali|img-tec|imagination|powervr|samsung|xclipse|broadcom|videocore|vivante)\b/i;

/**
 * classifyAdapter(info, adapter?) → {kind: "software" | "physical" | "unidentified", vendor, architecture, device,
 * description, fallback, reason}. `info` is a GPUAdapterInfo (or a plain object of its fields, or nothing); `adapter`
 * is consulted only for the older `isFallbackAdapter` flag. Never throws.
 */
export function classifyAdapter(info, adapter = null) {
  const i = info && typeof info === "object" ? info : {};
  const text = (key) => { try { return String(i[key] || "").slice(0, 200); } catch { return ""; } };
  let fallback = false;
  try { fallback = Boolean(i.isFallbackAdapter || adapter?.isFallbackAdapter); } catch { fallback = false; }
  const out = { kind: "unidentified", vendor: text("vendor"), architecture: text("architecture"), device: text("device"), description: text("description"), fallback, reason: "" };
  const names = [out.vendor, out.architecture, out.device, out.description].filter(Boolean).join(" ");
  const software = names.match(SOFTWARE_ADAPTER);
  if (fallback) { out.kind = "software"; out.reason = "the browser marks it a fallback adapter (isFallbackAdapter), which is typically a CPU renderer"; }
  else if (software) { out.kind = "software"; out.reason = `it names a software renderer (${software[0]})`; }
  else if (!names) out.reason = "it reports no vendor, architecture, device or description";
  else if (HARDWARE_VENDOR.test(names)) { out.kind = "physical"; out.reason = "it names a hardware GPU vendor; that is the browser's report, not a measurement"; }
  else out.reason = "it names no GPU vendor this classifier knows";
  return out;
}

// ------------------------------------------------------------------------------------------- planning

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

// The known-answer test a device passes once, before any of its counts are used: one small input per shader mode
// (all three pairs of a distance matrix, a Hamming count, sliding GC windows wider than one 64-lane pass), each with
// its every count written down here. web/test/runtime/webgpu.test.mjs checks these constants against referenceCounts.
export const KNOWN_ANSWERS = Object.freeze([
  { tool: "native.distance_matrix", args: { sequences: ["ACGTN-RYAC".repeat(8), "AGGTACGTCA".repeat(8), "TTGCA-NACG".repeat(8)] },
    counts: [0, 0, 0, 0, 48, 24, 0, 0, 48, 40, 16, 0, 0, 0, 0, 0, 0, 0, 0, 0, 64, 40, 16, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0] },
  { tool: "native.hamming_distance", args: { a: "ACGTURYSN".repeat(8), b: "ACGAURYSC".repeat(8) }, counts: [16, 0, 0, 0] },
  { tool: "native.gc_content", args: { sequence: "GCSATNGCAT".repeat(9), window: 86 },
    counts: [45, 0, 0, 0, 43, 0, 0, 0, 43, 0, 0, 0, 43, 0, 0, 0, 42, 0, 0, 0, 42, 0, 0, 0] },
]);

const now = () => (globalThis.performance?.now ? globalThis.performance.now() : Date.now());
const ms3 = (x) => (Number.isFinite(x) ? Math.round(x * 1000) / 1000 : null);

// ------------------------------------------------------------------------------------------- the runtime

export class SequenceCompute {
  #nav; #device = null; #pipeline = null; #deviceP = null; #lastReason = null; #timeout; #allowSoftware; #timestamps;
  #adapter = null;       // classifyAdapter() of the adapter last requested
  #knownAnswer = null;   // {ok, ms, reason?} of the current device's known-answer test; null until it answered
  #knownDevice = null;   // the device that result belongs to
  #last = null;          // what the last prepare() did (lastRun)
  // allowSoftware is used only by the explicit WGSL smoke harness and the benchmark; the runtime never enables it.
  // timestamps asks the device for timestamp queries, so lastRun.kernel_ms is the GPU's own time (benchmark only).
  constructor({ navigator = globalThis.navigator, timeoutMs = 10_000, allowSoftware = false, timestamps = false } = {}) {
    this.#nav = navigator; this.#timeout = timeoutMs; this.#allowSoftware = allowSoftware; this.#timestamps = timestamps;
  }
  get fallbackReason() { return this.#lastReason; }
  get allowsSoftware() { return this.#allowSoftware; }
  /** The adapter as classifyAdapter() saw it, once one was requested; null before. */
  get adapter() { return this.#adapter ? { ...this.#adapter } : null; }
  /**
   * The current device's known-answer test: {ok, ms, reason?}, or null when it has not run to an answer on this device
   * (ok: false only for a wrong count; a test that could not run keeps no verdict).
   */
  get knownAnswer() { return this.#knownAnswer && this.#knownDevice && this.#knownDevice === this.#device ? { ...this.#knownAnswer } : null; }
  /**
   * What the last prepare() did, or null when it did not reach the GPU: {dispatched (the call's own kernel was
   * submitted), checked (its counts passed the CPU sample check), cold (this call created the device, the pipeline or
   * ran the known-answer test), setup_ms, pack_ms, transfer_ms (upload, dispatch and readback), check_ms, work_ms (pack +
   * transfer + check: what the call cost once set up), kernel_ms (GPU timestamps; null unless asked for and supported),
   * kernel_note?}.
   */
  get lastRun() { return this.#last ? { ...this.#last } : null; }

  async #getDevice() {
    if (this.#device) return this.#device;
    if (!this.#deviceP) {
      this.#deviceP = (async () => {
        if (!this.#nav?.gpu?.requestAdapter) throw new Error("WebGPU API unavailable");
        const adapter = await limited(this.#nav.gpu.requestAdapter({ powerPreference: "high-performance" }), this.#timeout);
        if (!adapter) throw new Error("No WebGPU adapter available");
        let info = adapter.info;
        if (!info && typeof adapter.requestAdapterInfo === "function") info = await limited(adapter.requestAdapterInfo(), this.#timeout).catch(() => null);
        this.#adapter = classifyAdapter(info, adapter);
        if (!this.#allowSoftware && this.#adapter.kind === "software") {
          throw new Error("WebGPU adapter is a software CPU renderer");
        }
        let timestamps = false;
        try { timestamps = this.#timestamps && Boolean(adapter.features?.has?.("timestamp-query")); } catch { timestamps = false; }
        const request = timestamps ? adapter.requestDevice({ requiredFeatures: ["timestamp-query"] }) : adapter.requestDevice();
        const device = await limited(request, this.#timeout, (late) => late?.destroy());
        this.#device = device;
        device.lost.then(() => { if (this.#device === device) { this.#device = null; this.#pipeline = null; } });
        return device;
      })().finally(() => { this.#deviceP = null; });
    }
    return this.#deviceP;
  }

  /** The device, its pipeline, and the known-answer test passed on it: what a call needs before its own work. */
  async #ready(isCancelled) {
    const device = await this.#getDevice();
    cancelled(isCancelled);
    if (!this.#pipeline) {
      let scoped = false;
      try {
        device.pushErrorScope("validation"); scoped = true;
        const module = device.createShaderModule({ code: SHADER, label: "TCMScience exact sequence counts" });
        const pipeline = await limited(device.createComputePipelineAsync({ layout: "auto", compute: { module, entryPoint: "main" } }), this.#timeout);
        const validation = await device.popErrorScope(); scoped = false;
        if (validation) throw new Error(validation.message);
        if (this.#device !== device) throw new Error("WebGPU device was lost during shader compilation");
        this.#pipeline = pipeline;
      } finally {
        if (scoped) await device.popErrorScope().catch(() => {});
      }
    }
    if (this.#knownDevice !== device || !this.#knownAnswer) {
      const t0 = now();
      let result;
      try {
        for (const k of KNOWN_ANSWERS) {
          const counts = await this.#run(device, planSequenceCompute(k.tool, k.args), isCancelled, null);
          const at = k.counts.findIndex((v, i) => counts[i] !== v);
          if (counts.length !== k.counts.length || at >= 0) throw wrongAnswer(`${k.tool} count ${at} is ${counts[at]}, not ${k.counts[at]}`);
        }
        result = { ok: true, ms: ms3(now() - t0) };
      } catch (error) {
        if (error?.code === "cancelled" || isCancelled?.()) throw error;
        // Only a wrong answer is a verdict on the device. A timeout, a lost device, a failed readback or an allocation
        // that was refused says nothing about what it computes: no verdict is kept, this call falls back, and the
        // next call runs the test again (the scheduler sets the GPU aside if that keeps failing).
        if (error?.code !== "wrong_answer") {
          const lost = this.#device !== device ? " (the device was lost)" : "";
          throw new Error(`WebGPU known-answer test could not run${lost}: ${String(error?.message || error).slice(0, 300)}`);
        }
        result = { ok: false, ms: ms3(now() - t0), reason: String(error?.message || error).slice(0, 300) };
      }
      if (this.#device !== device) throw new Error("WebGPU device was lost during its known-answer test");
      this.#knownAnswer = result; this.#knownDevice = device;
    }
    if (!this.#knownAnswer.ok) throw Object.assign(new Error(`WebGPU known-answer test failed on this device: ${this.#knownAnswer.reason}`), { code: "known_answer_failed" });
    return device;
  }

  async prepare(tool, args, { preference = "auto", isCancelled, force = false } = {}) {
    this.#lastReason = null; this.#last = null;
    cancelled(isCancelled);
    if (preference === "cpu") { this.#lastReason = "CPU selected"; return null; }
    const plan = planSequenceCompute(tool, args);
    if (!plan) return null;
    if (!force && plan.operations < MIN_OPERATIONS) { this.#lastReason = "Small workload runs on CPU"; return null; }
    const last = { dispatched: false, checked: false, cold: false, setup_ms: null, pack_ms: null, transfer_ms: null, check_ms: null, work_ms: null, kernel_ms: null };
    try {
      const t0 = now();
      last.cold = !this.#device || !this.#pipeline || this.#knownDevice !== this.#device;
      this.#last = last;
      const device = await this.#ready(isCancelled);
      last.setup_ms = ms3(now() - t0);
      cancelled(isCancelled);
      const t1 = now();
      const counts = await this.#run(device, plan, isCancelled, last);
      last.work_ms = ms3(now() - t1);
      cancelled(isCancelled);
      return { tool, width: plan.width, n: plan.n, window: plan.window, counts: Array.from(counts), backend: "webgpu", precision: "uint32 exact counts; Python float64 transforms" };
    } catch (error) {
      if (error?.code === "cancelled" || isCancelled?.()) { cancelled(() => true); }
      this.#lastReason = String(error?.message || error);
      return null; // The worker dispatches the original Python implementation.
    }
  }

  /** One dispatch of `plan`. `last` (the call's lastRun, or null for the known-answer test) receives its marks. */
  async #run(device, plan, isCancelled, last) {
    const maxBuffer = device.limits.maxStorageBufferBindingSize;
    const maxSize = device.limits.maxBufferSize;
    const maxGroups = device.limits.maxComputeWorkgroupsPerDimension;
    if (plan.inputBytes > maxBuffer || plan.outputBytes > maxBuffer || Math.max(plan.inputBytes, plan.outputBytes) > maxSize || plan.records > maxGroups ** 2) throw new Error("Workload exceeds this GPU's buffer/dispatch limits");
    const dispatchX = Math.min(plan.records, maxGroups), dispatchY = Math.ceil(plan.records / dispatchX);
    const buffers = [];
    const allocate = (size, usage) => { const b = device.createBuffer({ size, usage }); buffers.push(b); return b; };
    let timed = false;
    try { timed = Boolean(last) && this.#timestamps && Boolean(device.features?.has?.("timestamp-query")); } catch { timed = false; }
    if (last && this.#timestamps && !timed) last.kernel_note = "this device was not given timestamp queries (unsupported or not offered)";
    let scoped = false, querySet = null;
    try {
      device.pushErrorScope("validation"); scoped = true;
      cancelled(isCancelled);
      const t0 = now();
      const values = new Uint32Array(plan.n * plan.width);
      for (let i = 0; i < plan.n; i++) for (let k = 0; k < plan.width; k++) {
        if ((k & 4095) === 0) cancelled(isCancelled);
        const base = plan.seqs[i][k];
        values[i * plan.width + k] = plan.mode === 0 && !"ACGT".includes(base) ? 0 : base.charCodeAt(0);
      }
      const t1 = now();
      const input = allocate(plan.inputBytes, U.STORAGE | U.COPY_DST);
      const output = allocate(plan.outputBytes, U.STORAGE | U.COPY_SRC | U.COPY_DST);
      const params = allocate(32, U.UNIFORM | U.COPY_DST);
      const read = allocate(plan.outputBytes, U.COPY_DST | U.MAP_READ);
      let stamps = null;
      if (timed) querySet = device.createQuerySet({ type: "timestamp", count: 2 });
      device.queue.writeBuffer(input, 0, values);
      device.queue.writeBuffer(params, 0, new Uint32Array([plan.width, plan.n, plan.mode, plan.window, plan.records, dispatchX, 0, 0]));
      const group = device.createBindGroup({ layout: this.#pipeline.getBindGroupLayout(0), entries: [
        { binding: 0, resource: { buffer: input } }, { binding: 1, resource: { buffer: output } }, { binding: 2, resource: { buffer: params } },
      ] });
      const encoder = device.createCommandEncoder();
      const pass = encoder.beginComputePass(timed ? { timestampWrites: { querySet, beginningOfPassWriteIndex: 0, endOfPassWriteIndex: 1 } } : undefined);
      pass.setPipeline(this.#pipeline); pass.setBindGroup(0, group); pass.dispatchWorkgroups(dispatchX, dispatchY); pass.end();
      encoder.copyBufferToBuffer(output, 0, read, 0, plan.outputBytes);
      if (timed) {
        const resolved = allocate(16, U.QUERY_RESOLVE | U.COPY_SRC);
        stamps = allocate(16, U.COPY_DST | U.MAP_READ);
        encoder.resolveQuerySet(querySet, 0, 2, resolved, 0);
        encoder.copyBufferToBuffer(resolved, 0, stamps, 0, 16);
      }
      device.queue.submit([encoder.finish()]);
      if (last) last.dispatched = true;
      await this.#readback(read, device, isCancelled);
      const counts = new Uint32Array(read.getMappedRange().slice(0));
      read.unmap();
      if (stamps) {
        await this.#readback(stamps, device, isCancelled);
        const [begin, end] = new BigUint64Array(stamps.getMappedRange().slice(0));
        stamps.unmap();
        if (end > begin) last.kernel_ms = ms3(Number(end - begin) / 1e6);
        else last.kernel_note = "the GPU timestamps did not advance (browsers quantize them)";
      }
      const validation = await device.popErrorScope(); scoped = false;
      if (validation) throw new Error(validation.message);
      const t2 = now();
      // Cheap deterministic samples detect failed/corrupt kernels without repeating the full pairwise workload.
      verifyCounts(plan, counts);
      if (last) {
        const t3 = now();
        Object.assign(last, { checked: true, pack_ms: ms3(t1 - t0), transfer_ms: ms3(t2 - t1), check_ms: ms3(t3 - t2) });
      }
      return counts;
    } finally {
      if (scoped) await device.popErrorScope().catch(() => {});
      try { querySet?.destroy?.(); } catch { /* already gone with its device */ }
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
  stop() { this.#device?.destroy(); this.#device = null; this.#pipeline = null; this.#knownAnswer = null; this.#knownDevice = null; }
}

/** A count that differs from what the CPU computes: the one failure that is a verdict on the device. */
function wrongAnswer(message) {
  return Object.assign(new Error(message), { code: "wrong_answer" });
}

function verifyCounts(plan, counts) {
  if (counts.length !== plan.records * 4) throw wrongAnswer("GPU returned an invalid count buffer");
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
    if (counts[at] !== total || counts[at + 1] !== diffs || counts[at + 2] !== transitions) throw wrongAnswer("GPU integer counts failed their CPU sample check");
  }
}

function limited(promise, timeoutMs, onLate) {
  let timer, expired = false;
  const operation = Promise.resolve(promise).then((result) => { if (expired) onLate?.(result); return result; });
  return Promise.race([operation, new Promise((_, reject) => { timer = setTimeout(() => { expired = true; reject(new Error("WebGPU operation timed out")); }, timeoutMs); })]).finally(() => clearTimeout(timer));
}
