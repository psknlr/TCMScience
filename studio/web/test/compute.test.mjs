// The hardware-adaptive layer: capability probing (by trying, never by browser name), the scheduler's choices and
// fallbacks, and the Worker's decision whether sequence counts are prepared on the GPU.
import assert from "node:assert/strict";
import { test } from "node:test";
import { CAPABILITIES_SCHEMA, EVIDENCE, NPU_STATUS, evidenceLevel, fingerprint, probeCapabilities, summarizeCapabilities, webgpuEvidence } from "../js/compute/capabilities.js";
import { ComputeScheduler, SchedulerError } from "../js/compute/scheduler.js";
import { GPU_MIN_OPERATIONS, SequenceAccelerator, countsOp, decorateReceipt, gpuDegradation, inputDigest, kernelOptions, normalizePreference, planSummary } from "../js/compute/accelerator.js";
import { SequenceCompute, planSequenceCompute, referenceCounts } from "../js/runtime/webgpu.js";
import { detectDevice } from "../js/runtime/browser.js";

// ------------------------------------------------------------------------------------------------- capabilities

test("capabilities in Node: WebAssembly features found by validation; no GPU, no WebNN; never throws", async () => {
  const caps = await probeCapabilities({ timeoutMs: 200 });
  assert.equal(caps.schema, CAPABILITIES_SCHEMA);
  assert.equal(caps.wasm.available, true);
  assert.equal(caps.wasm.simd, true, "Node 20+ validates SIMD");
  assert.equal(typeof caps.wasm.threads, "boolean");
  assert.equal(caps.webgpu.api, false);
  assert.equal(caps.webnn.api, false);
  assert.ok(caps.timer.resolution_ms === null || caps.timer.resolution_ms >= 0);
  assert.ok(JSON.parse(JSON.stringify(caps)), "JSON-ready");
});

test("threads count as usable only with SharedArrayBuffer on a cross-origin isolated page", async () => {
  const base = { WebAssembly, navigator: {}, performance };
  const isolated = await probeCapabilities({ scope: { ...base, crossOriginIsolated: true, SharedArrayBuffer }, webgpu: false, webnn: false });
  const open = await probeCapabilities({ scope: { ...base, crossOriginIsolated: false, SharedArrayBuffer }, webgpu: false, webnn: false });
  assert.equal(isolated.wasm.threads, true);
  assert.equal(isolated.wasm.threads_usable, true);
  assert.equal(open.wasm.threads_usable, false);
});

function fakeAdapter(info, extra = {}) {
  return { info, features: new Set(["timestamp-query", "shader-f16"]), limits: { maxBufferSize: 2 ** 30, maxStorageBufferBindingSize: 2 ** 28, maxComputeWorkgroupSizeX: 256 }, ...extra };
}

test("a WebGPU adapter is reported with its features and limits; a software renderer is named as such", async () => {
  const hw = await probeCapabilities({ scope: { navigator: { gpu: { requestAdapter: async () => fakeAdapter({ vendor: "apple", architecture: "metal-3" }) } } }, webnn: false });
  assert.equal(hw.webgpu.software, false);
  assert.deepEqual(hw.webgpu.features, ["shader-f16", "timestamp-query"]);
  assert.equal(hw.webgpu.limits.maxStorageBufferBindingSize, 2 ** 28);
  const sw = await probeCapabilities({ scope: { navigator: { gpu: { requestAdapter: async () => fakeAdapter({ vendor: "google", architecture: "swiftshader" }) } } }, webnn: false });
  assert.equal(sw.webgpu.software, true);
  const fallback = await probeCapabilities({ scope: { navigator: { gpu: { requestAdapter: async () => fakeAdapter({ vendor: "x" }, { isFallbackAdapter: true }) } } }, webnn: false });
  assert.equal(fallback.webgpu.software, true);
  const none = await probeCapabilities({ scope: { navigator: { gpu: { requestAdapter: async () => null } } }, webnn: false });
  assert.equal(none.webgpu.adapter, null);
  const hung = await probeCapabilities({ scope: { navigator: { gpu: { requestAdapter: () => new Promise(() => {}) } } }, webnn: false, timeoutMs: 30 });
  assert.match(hung.webgpu.error, /timed out/);
  const lines = summarizeCapabilities(sw);
  assert.match(lines.find((l) => l.backend === "webgpu").detail, /software renderer/);
  assert.equal(lines.find((l) => l.backend === "webgpu").available, false);
});

test("the probe classifies with the one classifier: unidentified is neither software nor a GPU", async () => {
  const probe = (info, extra) => probeCapabilities({ scope: { navigator: { gpu: { requestAdapter: async () => fakeAdapter(info, extra) } } }, webnn: false });
  const cases = [
    [{}, "unidentified", null],
    [{ vendor: "mesa", device: "llvmpipe (LLVM 17.0.6, 256 bits)" }, "software", true],
    [{ description: "Microsoft Basic Render Driver" }, "software", true],
    [{ vendor: "nvidia", architecture: "ampere" }, "physical", false],
    [{ vendor: "intel", architecture: "gen-12lp" }, "physical", false],
  ];
  for (const [info, kind, software] of cases) {
    const caps = await probe(info);
    assert.equal(caps.webgpu.adapter.kind, kind, JSON.stringify(info));
    assert.equal(caps.webgpu.software, software, JSON.stringify(info));
    assert.equal(caps.webgpu.evidence.level, "reported", "the probe alone reaches 'reported' at most");
    assert.equal(caps.webgpu.evidence.adapter_kind, kind);
    assert.equal(caps.webgpu.evidence.accelerated, null, "nothing measured");
    assert.equal(caps.webgpu.evidence.dispatched, null, "no session");
    const line = summarizeCapabilities(caps).find((l) => l.backend === "webgpu");
    assert.equal(line.available, kind === "physical", JSON.stringify(info));
  }
  const empty = await probe({});
  assert.match(summarizeCapabilities(empty).find((l) => l.backend === "webgpu").detail, /unidentified: not reported as a GPU/);
  assert.match(empty.webgpu.evidence.reason, /unidentified/);
  const none = await probeCapabilities({ scope: { navigator: {} }, webnn: false });
  assert.equal(none.webgpu.evidence.level, null);
  assert.match(none.webgpu.evidence.reason, /no WebGPU API/);
});

test("WebNN: a created context is recorded as requested and reported, never as proof of an NPU", async () => {
  const limits = { matmul: { a: { dataTypes: ["float32", "float16"] } } };
  const ml = {
    async createContext(options) {
      if (options.powerPreference === "low-power") throw new Error("NotSupportedError: no such adapter");
      return { opSupportLimits: () => limits, destroy() {} };
    },
  };
  const caps = await probeCapabilities({ scope: { navigator: { ml } }, webgpu: false });
  assert.equal(caps.webnn.api, true);
  const npu = caps.webnn.contexts.find((c) => c.label === "npu");
  assert.deepEqual(npu.requested, { deviceType: "npu" });
  assert.equal(npu.created, true);
  assert.equal(npu.verified, null, "not deep-probed");
  assert.equal(npu.float16_matmul, true);
  assert.match(caps.webnn.note, /does not show which processor/);
  assert.equal(caps.webnn.distinct_backends_hint, 1, "every context reports the same limits: probably one backend");
  assert.match(caps.webnn.contexts.find((c) => c.label === "low-power").error, /NotSupported/);
  assert.match(summarizeCapabilities(caps).find((l) => l.backend === "webnn").detail, /not reported/);
  // the structured status: what was asked and answered, and an NPU that stays unverified
  assert.equal(caps.webnn.context_created, true);
  assert.deepEqual(caps.webnn.device_type_requested, ["cpu", "gpu", "npu"]);
  assert.deepEqual(caps.webnn.npu, { status: "unverified", label_en: "WebNN detected; NPU execution unverified", label_zh: "检测到 WebNN；NPU 执行未经验证", reason: NPU_STATUS.detected.reason });
  assert.match(summarizeCapabilities(caps).find((l) => l.backend === "webnn").detail, /^WebNN detected; NPU execution unverified/);
});

test("WebNN: even a deep-verified 'npu' context leaves the NPU unverified; without WebNN no NPU backend is claimed", async () => {
  class MLGraphBuilder {
    constructor() {}
    input() { return {}; } constant() { return {}; } matmul() { return {}; }
    async build() { return {}; }
  }
  const ml = {
    async createContext() {
      return { opSupportLimits: () => ({}), destroy() {}, async createTensor() { return {}; }, writeTensor() {}, dispatch() {},
        async readTensor() { return new Float32Array([1, 2, 3, 4]).buffer; } };
    },
  };
  const caps = await probeCapabilities({ scope: { navigator: { ml }, MLGraphBuilder }, webgpu: false, deep: true });
  assert.ok(caps.webnn.contexts.every((c) => c.created && c.verified?.ok === true), "every context computed the graph correctly");
  assert.equal(caps.webnn.npu.status, "unverified", "a correct graph on an 'npu' context does not show an NPU ran it");
  const absent = await probeCapabilities({ scope: { navigator: {} }, webgpu: false });
  assert.equal(absent.webnn.api, false);
  assert.equal(absent.webnn.context_created, false);
  assert.deepEqual(absent.webnn.device_type_requested, []);
  assert.deepEqual(absent.webnn.npu, { status: "unverified", label_en: "No verified NPU backend is available", label_zh: "没有可用的已验证 NPU 后端", reason: NPU_STATUS.absent.reason });
  const skipped = await probeCapabilities({ scope: { navigator: {} }, webgpu: false, webnn: false });
  assert.equal(skipped.webnn.npu.label_en, "No verified NPU backend is available");
  assert.equal(skipped.webnn.npu.reason, NPU_STATUS.not_probed.reason, "a probe that did not run does not say the API is missing");
  assert.match(skipped.webnn.npu.reason, /not probed/);
  // nowhere does anything say an NPU accelerated something
  for (const doc of [caps, absent, skipped, NPU_STATUS]) assert.doesNotMatch(JSON.stringify(doc), /npu\W*accelerat|accelerated[^"]*npu/i);
});

test("fingerprints are stable over key order", () => {
  assert.equal(fingerprint({ a: 1, b: [1, { c: 2 }] }), fingerprint({ b: [1, { c: 2 }], a: 1 }));
  assert.notEqual(fingerprint({ a: 1 }), fingerprint({ a: 2 }));
});

// ------------------------------------------------------------------------------------------------- scheduler

function backend(id, kind, { exact = true, cost = 1, fail = 0, result = id, code } = {}) {
  let failures = fail;
  return {
    id, kind, exact, runs: 0,
    supports: () => ({ ok: true }),
    cost: () => cost,
    async run() {
      this.runs++;
      if (failures > 0) { failures--; throw Object.assign(new Error(`${id} failed`), { code }); }
      return result;
    },
  };
}

test("a backend that defers its work is not timed by its hand-off; record() says what the work took", async () => {
  const s = new ComputeScheduler({ backends: [{ ...backend("simd", "wasm-simd", { cost: 1 }), defers: true }, backend("js", "js", { cost: 2 })] });
  const { provenance } = await s.run({ op: "x", units: 1000 }, null);
  assert.equal(provenance.executed.id, "simd");
  assert.deepEqual(s.measurements(), []);
  s.record("simd", "x", 1000, 5);
  assert.deepEqual(s.measurements(), [{ backend: "simd", op: "x", bucket: 10, ms_per_unit: 0.005 }]);
});

test("the cheapest supported backend runs; a failure falls back to the next, and provenance says so", async () => {
  const gpu = backend("webgpu", "webgpu", { cost: 1, fail: 1 });
  const js = backend("js", "js", { cost: 5 });
  const py = backend("python", "python", { cost: 100 });
  const s = new ComputeScheduler({ backends: [py, js, gpu] });
  const { result, provenance } = await s.run({ op: "x", units: 1000, exact: true }, null);
  assert.equal(result, "js");
  assert.deepEqual(provenance.considered.map((r) => r.id), ["webgpu", "js", "python"]);
  assert.deepEqual(provenance.tried.map((r) => [r.id, r.outcome]), [["webgpu", "error"], ["js", "ok"]]);
  assert.equal(provenance.executed.id, "js");
  assert.match(provenance.fallback_reason, /webgpu failed/);
});

test("exact work never goes to an approximate backend; 'cpu' excludes GPU and NPU; 'reference' leaves only Python", () => {
  const s = new ComputeScheduler({ backends: [backend("webnn", "webnn", { exact: false, cost: 0 }), backend("webgpu", "webgpu"), backend("js", "js"), backend("python", "python")] });
  const exact = s.plan({ op: "x", exact: true });
  assert.ok(!exact.order.some((r) => r.id === "webnn"));
  assert.match(exact.passed_over.find((r) => r.id === "webnn").reason, /exact/);
  assert.ok(s.plan({ op: "x", exact: false }).order.some((r) => r.id === "webnn"));
  assert.deepEqual(s.plan({ op: "x", exact: false, preference: "cpu" }).order.map((r) => r.id).sort(), ["js", "python"]);
  assert.deepEqual(s.plan({ op: "x", preference: "reference" }).order.map((r) => r.id), ["python"]);
});

test("sensitive input never goes to a remote backend without the person's consent", async () => {
  const remote = backend("remote", "remote", { cost: 0 });
  const local = backend("js", "js", { cost: 10 });
  const s = new ComputeScheduler({ backends: [remote, local] });
  const { result, provenance } = await s.run({ op: "x", sensitive: true, exact: true }, null);
  assert.equal(result, "js");
  assert.equal(remote.runs, 0);
  assert.match(provenance.passed_over.find((r) => r.id === "remote").reason, /consent/);
  assert.equal((await s.run({ op: "x", sensitive: true, consent: { remote: true } }, null)).result, "remote");
  // and with only the remote backend, sensitive work does not run at all rather than leave the device
  const only = new ComputeScheduler({ backends: [backend("remote", "remote")] });
  await assert.rejects(only.run({ op: "x", sensitive: true }, null), (e) => e instanceof SchedulerError && e.code === "no_backend");
});

test("a cancelled task stops: no fallback to another backend", async () => {
  const slow = { id: "webgpu", kind: "webgpu", exact: true, supports: () => ({ ok: true }), cost: () => 1, run: () => new Promise(() => {}) };
  const js = backend("js", "js", { cost: 2 });
  const s = new ComputeScheduler({ backends: [slow, js] });
  const ac = new AbortController();
  const p = s.run({ op: "x" }, null, { signal: ac.signal });
  setTimeout(() => ac.abort(), 5);
  await assert.rejects(p, (e) => e.name === "AbortError");
  assert.equal(js.runs, 0);
});

test("a lost device sets the backend aside at once; repeated failures set it aside after two", async () => {
  const lost = backend("webgpu", "webgpu", { cost: 1, fail: 1, code: "device_lost" });
  const js = backend("js", "js", { cost: 2 });
  const s = new ComputeScheduler({ backends: [lost, js] });
  await s.run({ op: "x" }, null);
  assert.equal(s.health("webgpu").setAside, true);
  assert.match(s.plan({ op: "x" }).passed_over[0].reason, /device lost/);
  const flaky = backend("webgpu", "webgpu", { cost: 1, fail: 5 });
  const s2 = new ComputeScheduler({ backends: [flaky, backend("js", "js", { cost: 2 })] });
  await s2.run({ op: "x" }, null);
  assert.equal(s2.health("webgpu").setAside, false);
  await s2.run({ op: "x" }, null);
  assert.equal(s2.health("webgpu").setAside, true);
});

test("a backend's own check failing counts as a failure", async () => {
  const wrong = { ...backend("webgpu", "webgpu", { cost: 1 }), verify: () => ({ ok: false, reason: "sample mismatch" }) };
  const s = new ComputeScheduler({ backends: [wrong, backend("js", "js", { cost: 2 })] });
  const { result, provenance } = await s.run({ op: "x" }, null);
  assert.equal(result, "js");
  assert.match(provenance.tried[0].reason, /sample mismatch/);
});

test("a comparison is a measurement or nothing: compare() never answers from cost()", () => {
  const s = new ComputeScheduler({ backends: [backend("webgpu", "webgpu", { cost: 1 }), backend("wasm-simd", "wasm-simd", { cost: 50 }), backend("js", "js", { cost: 90 })] });
  assert.equal(s.plan({ op: "x", units: 1e6 }).order[0].id, "webgpu", "the cost model prefers the GPU");
  assert.deepEqual(s.compare("x", 1e6, "webgpu", ["wasm-simd", "js"]).measured, false, "but nothing was measured");
  s.record("webgpu", "x", 1e6, 30);
  assert.match(s.compare("x", 1e6, "webgpu", ["wasm-simd", "js"]).reason, /no baseline/);
  s.record("js", "x", 1e6, 40);
  s.record("wasm-simd", "x", 1e6, 20);
  const c = s.compare("x", 1e6, "webgpu", ["wasm-simd", "js"]);
  assert.deepEqual(c, { measured: true, bucket: 20, ms: 30, baseline: { id: "wasm-simd", ms: 20 }, faster: false });
  assert.equal(s.compare("x", 10, "webgpu", ["wasm-simd"]).measured, false, "another size class has its own measurements");
  assert.equal(s.measured("js", "x", 1e6), 40);
  assert.equal(s.measured("js", "x", 10), null);
  assert.deepEqual(s.baseline("x", 1e6, ["js", "wasm-simd"]), { id: "wasm-simd", ms: 20 });
});

test("what this device measured outranks the cost model", () => {
  const s = new ComputeScheduler({ backends: [backend("webgpu", "webgpu", { cost: 1 }), backend("js", "js", { cost: 50 })] });
  assert.equal(s.plan({ op: "x", units: 1e6 }).order[0].id, "webgpu");
  // this GPU turned out slow for this size (an integrated GPU behind a busy compositor, say)
  s.record("webgpu", "x", 1e6, 900);
  s.record("js", "x", 1e6, 40);
  assert.equal(s.plan({ op: "x", units: 1e6 }).order[0].id, "js");
  assert.equal(s.plan({ op: "x", units: 10 }).order[0].id, "webgpu", "another size class: the model again");
  assert.equal(s.measurements().length, 2);
});

// ------------------------------------------------------------------------------------------------- accelerator

function fakeGPU({ fail = null, kind = "physical", knownAnswer = { ok: true, ms: 1 }, workMs = 5, cold = false } = {}) {
  // a SequenceCompute double that computes the exact counts on the CPU, or fails like a real one, and reports what a
  // real one reports about itself (adapter, known-answer test, what the last call did)
  return {
    fallbackReason: null, stopped: false, lastRun: null, calls: 0,
    adapter: kind ? { kind, vendor: kind === "software" ? "google" : "nvidia", architecture: kind === "software" ? "swiftshader" : "ampere", reason: "test" } : null,
    knownAnswer,
    async prepare(tool, args) {
      this.calls++;
      if (fail) { this.fallbackReason = fail; this.lastRun = { dispatched: false, cold, setup_ms: 1, work_ms: null }; return null; }
      this.lastRun = { dispatched: true, checked: true, cold, setup_ms: 40, work_ms: workMs, kernel_ms: null };
      const plan = planSequenceCompute(tool, args);
      return { tool, width: plan.width, n: plan.n, window: plan.window, counts: Array.from(referenceCounts(plan)), backend: "webgpu", precision: "uint32 exact counts; Python float64 transforms" };
    },
    stop() { this.stopped = true; },
  };
}

// two sequences of 80 000: the CPU kernel's ~0.1 ms beats the GPU's set-up; forty of 8 000 (6.2 M comparisons,
// quadratic in the number of sequences) is where the GPU pays for itself against the JavaScript kernel, and sixty-four
// of 40 000 (81 M) against WebAssembly SIMD
const big = { sequences: { a: "ACGT".repeat(20000), b: "ACGA".repeat(20000) }, model: "p" };
const huge = { sequences: Array.from({ length: 40 }, (_, i) => "ACGT".repeat(2000).slice(i) + "ACGT".slice(0, i % 4).padEnd(i, "A").slice(0, i)), model: "p" };
const giant = { sequences: Array.from({ length: 64 }, (_, i) => "ACGT".repeat(10000).slice(i) + "A".repeat(i)), model: "p" };
// an engine without WebAssembly SIMD (the GPU-against-JavaScript tests)
const noSIMD = () => ({ available: false, reason: "not in this test" });

function countTask(tool, args, preference = "auto") {
  const plan = planSequenceCompute(tool, args);
  return { op: countsOp(tool), tool, units: plan.operations, counts: plan.records * 4, exact: true, preference };
}

test("the accelerator: large work goes to the GPU with a digest Python can check; CPU-only and reference never do", async () => {
  const acc = new SequenceAccelerator({ gpu: fakeGPU(), simd: noSIMD });
  assert.equal((await acc.prepare("native.distance_matrix", big)).packet, null, "two long sequences: the CPU kernel is cheaper");
  const out = await acc.prepare("native.distance_matrix", huge);
  assert.equal(out.packet.backend, "webgpu");
  assert.equal(out.plan.executed.id, "webgpu");
  assert.equal(out.packet.input_sha256, await inputDigest("native.distance_matrix", huge.sequences, 0));
  for (const preference of ["cpu", "reference"]) {
    const o = await acc.prepare("native.distance_matrix", huge, { preference });
    assert.equal(o.packet, null, preference);
    assert.ok(o.plan.passed_over.some((r) => r.id === "webgpu"), preference);
  }
  assert.deepEqual(kernelOptions("reference"), { kernels: false });
  assert.deepEqual(kernelOptions("cpu"), { kernels: true });
  assert.equal(normalizePreference("gpu please"), "auto");
});

test("the accelerator: small work stays on the CPU; a GPU that fails leaves the counting to Python", async () => {
  const acc = new SequenceAccelerator({ gpu: fakeGPU(), simd: noSIMD });
  const small = await acc.prepare("native.hamming_distance", { a: "ACGT", b: "ACGA" });
  assert.equal(small.packet, null);
  assert.match(small.plan.passed_over.find((r) => r.id === "webgpu").reason, /Small workload/);
  assert.ok(GPU_MIN_OPERATIONS > 4);
  const broken = new SequenceAccelerator({ gpu: fakeGPU({ fail: "WebGPU device lost: GPU process crashed" }), simd: noSIMD });
  const o = await broken.prepare("native.distance_matrix", huge);
  assert.equal(o.packet, null);
  assert.equal(o.plan.tried[0].outcome, "error");
  assert.equal(broken.scheduler.health("webgpu").setAside, true, "a lost device is not tried again this session");
  assert.equal((await broken.prepare("native.fisher_exact", {})).packet, null, "not a sequence tool");
  const odd = await broken.prepare("native.gc_content", { sequence: "XYZ" });
  assert.equal(odd.plan.executed, null, "input the GPU kernel does not take");
  assert.match(odd.plan.fallback_reason, /CPU kernel where that applies/, "the CPU kernels may still take it: the plan does not say otherwise");
});

test("the accelerator learns from what ran inside Python", async () => {
  const acc = new SequenceAccelerator({ gpu: fakeGPU(), simd: noSIMD });
  assert.equal((await acc.prepare("native.distance_matrix", huge)).plan.executed.id, "webgpu");
  // on this device the CPU kernel turned out much faster than the cost model thought, and the GPU slower
  const ops = planSequenceCompute("native.distance_matrix", huge).operations;
  acc.learn("native.distance_matrix", huge, "js", 0.01);
  acc.scheduler.record("webgpu", countsOp("native.distance_matrix"), ops, 500);
  assert.equal((await acc.prepare("native.distance_matrix", huge)).plan.executed.id, "js");
});

test("the real SequenceCompute rejects software adapters, so the scheduler falls back on SwiftShader", async () => {
  const gpu = new SequenceCompute({ navigator: { gpu: { requestAdapter: async () => ({ info: { vendor: "google", architecture: "swiftshader" } }) } } });
  const acc = new SequenceAccelerator({ gpu, simd: noSIMD });
  const o = await acc.prepare("native.distance_matrix", huge);
  assert.equal(o.packet, null);
  assert.match(o.plan.tried[0].reason, /software/);
  assert.equal(o.plan.executed.id, "js", "the counting falls to the CPU kernel");
});

test("with WebAssembly SIMD the CPU kernel is SIMD: it takes what the GPU's set-up does not pay for", async () => {
  const acc = new SequenceAccelerator({ gpu: fakeGPU() }); // the real simdStatus(): Node has SIMD
  const o = await acc.prepare("native.distance_matrix", huge);
  assert.equal(o.packet, null, "nothing prepared: Python calls the SIMD kernel itself");
  assert.equal(o.plan.executed.id, "wasm-simd");
  assert.deepEqual(o.plan.considered.map((r) => r.id), ["wasm-simd", "webgpu", "js", "python"]);
  // sixty-four sequences of 40 000: enough work for the GPU to pay back its fixed cost
  assert.equal(acc.scheduler.plan(countTask("native.distance_matrix", giant)).order[0].id, "webgpu");
  // what it measures moves the choice: a SIMD run this fast keeps even the giant workload on the CPU
  const ops = planSequenceCompute("native.distance_matrix", giant).operations;
  acc.learn("native.distance_matrix", giant, "wasm-simd", ops * 5e-8);
  assert.equal(acc.scheduler.plan(countTask("native.distance_matrix", giant)).order[0].id, "wasm-simd");
});

test("a path that runs inside Python is timed by what it took there, never by its instant hand-off", async () => {
  const acc = new SequenceAccelerator({ gpu: fakeGPU() });
  await acc.prepare("native.distance_matrix", huge);
  assert.deepEqual(acc.scheduler.measurements(), [], "the hand-off is not a measurement");
  acc.learn("native.distance_matrix", huge, "wasm-simd", 1.5);
  acc.learn("native.distance_matrix", huge, "pyodide", 900); // not a backend the scheduler knows: ignored
  assert.deepEqual(acc.scheduler.measurements().map((m) => m.backend), ["wasm-simd"]);
});

test("without SIMD the plan says why, and the JavaScript kernel counts", async () => {
  const acc = new SequenceAccelerator({ gpu: fakeGPU(), simd: () => ({ available: false, reason: "this engine has no WebAssembly SIMD" }) });
  const o = await acc.prepare("native.distance_matrix", big);
  assert.equal(o.plan.executed.id, "js");
  assert.equal(o.plan.passed_over.find((r) => r.id === "wasm-simd").reason, "No WebAssembly SIMD here (this engine has no WebAssembly SIMD)");
});

test("the device report names the CPU kernels and what they fall back to", async () => {
  const d = await detectDevice({ navigator: { hardwareConcurrency: 4 }, isolated: true });
  assert.deepEqual(d.compute.cpu_kernels["wasm-simd"], ["native.distance_matrix", "native.hamming_distance", "native.gc_content"]);
  assert.deepEqual(d.compute.cpu_kernels.js, ["native.global_alignment", "native.local_alignment", "native.protein_alignment", "native.edit_distance"]);
  assert.equal(d.compute.fallback, "the CPU kernels, then the original Python");
  assert.equal(d.compute.webgpu_adapter, false, "no adapter in Node");
});

// ------------------------------------------------------------------------------------------------- the evidence ladder

test("the ladder: each rung needs the ones below it, and only a physical adapter can be 'accelerated'", () => {
  assert.deepEqual(EVIDENCE, ["selected", "reported", "dispatched", "validated", "verified", "accelerated"]);
  const all = { selected: true, reported: true, dispatched: true, validated: true, verified: true, accelerated: true };
  assert.equal(evidenceLevel({}), null);
  assert.equal(evidenceLevel({ selected: true }), "selected");
  assert.equal(evidenceLevel({ reported: true }), "reported");
  assert.equal(evidenceLevel({ ...all, dispatched: false }), "reported", "nothing above a missing rung counts");
  assert.equal(evidenceLevel({ ...all, validated: false }), "dispatched");
  assert.equal(evidenceLevel({ ...all, verified: false }), "validated");
  assert.equal(evidenceLevel({ ...all, adapter_kind: "physical" }), "accelerated");
  assert.equal(evidenceLevel({ ...all, adapter_kind: "software" }), "verified", "software is never acceleration");
  assert.equal(evidenceLevel({ ...all, adapter_kind: "unidentified" }), "verified");
  assert.equal(evidenceLevel({ ...all }), "verified", "no adapter kind, no acceleration");
});

test("webgpuEvidence over a session: dispatched, validated, verified, and accelerated only from a measurement", () => {
  const physical = { api: true, adapter: { kind: "physical", vendor: "nvidia", architecture: "ampere", reason: "names a vendor" } };
  const session = (extra) => ({ adapter: physical.adapter, dispatched: 0, validated: 0, rejected: 0, measured: 0, accelerated: 0, known_answer: null, ...extra });
  assert.equal(webgpuEvidence(physical).level, "reported");
  assert.equal(webgpuEvidence(physical, session({ dispatched: 1, rejected: 1, last_reason: "pair 0,1 differs" })).level, "dispatched");
  assert.match(webgpuEvidence(physical, session({ dispatched: 1, rejected: 1, last_reason: "pair 0,1 differs" })).reason, /pair 0,1/);
  assert.equal(webgpuEvidence(physical, session({ dispatched: 2, validated: 1 })).level, "validated");
  assert.equal(webgpuEvidence(physical, session({ dispatched: 2, validated: 1, known_answer: { ok: true } })).level, "verified");
  const notFaster = webgpuEvidence(physical, session({ dispatched: 2, validated: 2, known_answer: { ok: true }, measured: 1, accelerated: 0 }));
  assert.equal(notFaster.level, "verified");
  assert.equal(notFaster.accelerated, false);
  assert.match(notFaster.reason, /not faster/);
  const faster = webgpuEvidence(physical, session({ dispatched: 2, validated: 2, known_answer: { ok: true }, measured: 1, accelerated: 1 }));
  assert.equal(faster.level, "accelerated");
  assert.equal(faster.accelerated, true);
  // a software or unidentified adapter never reaches it, whatever the counters say
  for (const kind of ["software", "unidentified"]) {
    const adapter = { kind, vendor: "", architecture: "", reason: "test" };
    const e = webgpuEvidence({ api: true, adapter }, { ...session({ dispatched: 3, validated: 3, known_answer: { ok: true }, measured: 3, accelerated: 3 }), adapter });
    assert.notEqual(e.level, "accelerated", kind);
    assert.equal(e.accelerated, false, kind);
  }
  assert.equal(webgpuEvidence({ api: true, adapter: null, error: "no adapter" }).level, null);
  assert.equal(webgpuEvidence({ api: false }).reason, "this browser has no WebGPU API");
});

/** The compute receipt Python writes for a call, as far as the GPU goes (tcmstudio.browser_compute). */
function pythonCompute(used, { rejected = null, handoff = 2 } = {}) {
  return used ? { backend: "webgpu", operation: "distance_matrix", gpu: { validated: true, handoff_ms: handoff } }
    : { backend: "wasm-simd", operation: "native.distance_matrix", ...(rejected ? { gpu_rejected: rejected, gpu: { validated: false, handoff_ms: handoff } } : {}) };
}

test("settle: counts Python used are validated and verified, but not accelerated without a measured CPU baseline", async () => {
  let clock = 0;
  const acc = new SequenceAccelerator({ gpu: fakeGPU(), simd: noSIMD, now: () => (clock += 0.25) }); // a digest of 0.25 ms
  const { packet, gpu } = await acc.prepare("native.distance_matrix", huge);
  assert.ok(packet);
  const g = acc.settle("native.distance_matrix", huge, { gpu, compute: pythonCompute(true), stringifyMs: 1 });
  assert.equal(g.dispatched, true);
  assert.equal(g.validated, true);
  assert.equal(g.verified, true);
  assert.deepEqual(g.adapter, { kind: "physical", vendor: "nvidia", architecture: "ampere" });
  assert.equal(gpu.digest_ms, 0.25, "the digest is timed by the accelerator's clock");
  assert.ok(Math.abs(g.gpu_ms - (5 + 0.25 + 1 + 2)) < 1e-9, "the GPU's work, the digest, the packet's JSON and Python's binding");
  assert.equal(g.cpu_baseline_ms, null);
  assert.equal(g.accelerated, false);
  assert.match(g.basis, /no CPU kernel has been measured/);
  assert.equal(g.evidence, "verified");
  assert.equal(g.fallback_reason, null);
  // the scheduler learned the like-for-like time, not prepare()'s instant answer or the cost model (per unit, so the
  // product may differ from gpu_ms in the last bits of a double)
  const ops = planSequenceCompute("native.distance_matrix", huge).operations;
  assert.ok(Math.abs(acc.scheduler.measured("webgpu", countsOp("native.distance_matrix"), ops) - g.gpu_ms) < 1e-9);
});

test("settle: 'accelerated' only after this device measured a slower CPU kernel at the same size", async () => {
  const acc = new SequenceAccelerator({ gpu: fakeGPU({ workMs: 5 }), simd: noSIMD });
  const ops = planSequenceCompute("native.distance_matrix", huge).operations;
  acc.learn("native.distance_matrix", huge, "js", 200);         // the JavaScript kernel measured here: 200 ms
  const first = await acc.prepare("native.distance_matrix", huge);
  const g = acc.settle("native.distance_matrix", huge, { gpu: first.gpu, compute: pythonCompute(true), stringifyMs: 0.5 });
  assert.equal(g.accelerated, true);
  assert.equal(g.cpu_baseline_backend, "js");
  assert.equal(g.cpu_baseline_ms, 200);
  assert.match(g.basis, /measured on this device/);
  assert.equal(g.evidence, "accelerated");
  assert.equal(acc.evidence().accelerated, 1);
  // a CPU kernel that turns out faster here takes it away, and the scheduler moves the work there
  for (let i = 0; i < 15; i++) acc.learn("native.distance_matrix", huge, "js", 1); // the EWMA follows
  const op = countsOp("native.distance_matrix");
  assert.ok(acc.scheduler.measured("js", op, ops) < acc.scheduler.measured("webgpu", op, ops));
  assert.equal((await acc.prepare("native.distance_matrix", huge)).plan.executed.id, "js");
});

test("settle: a CPU kernel measured on another tool at the same size is never the baseline", async () => {
  const acc = new SequenceAccelerator({ gpu: fakeGPU({ workMs: 5 }), simd: noSIMD });
  const ops = planSequenceCompute("native.distance_matrix", huge).operations;
  const gc = { sequence: "GCAT".repeat(ops / 4) };                  // the same 6 240 000 units, of another tool
  assert.equal(planSequenceCompute("native.gc_content", gc).operations, ops);
  acc.learn("native.gc_content", gc, "js", 50);                       // a slow CPU kernel, but on gc_content
  const o = await acc.prepare("native.distance_matrix", huge);
  const g = acc.settle("native.distance_matrix", huge, { gpu: o.gpu, compute: pythonCompute(true), stringifyMs: 0.5 });
  assert.equal(g.accelerated, false);
  assert.equal(g.cpu_baseline_ms, null);
  assert.match(g.basis, /no CPU kernel has been measured for distance_matrix at this size/);
  assert.equal(acc.scheduler.compare(countsOp("native.distance_matrix"), ops, "webgpu", ["wasm-simd", "js"]).measured, false);
  // measured on distance_matrix itself, it counts, and the basis names the tool
  acc.learn("native.distance_matrix", huge, "js", 200);
  const again = await acc.prepare("native.distance_matrix", huge);
  const h = acc.settle("native.distance_matrix", huge, { gpu: again.gpu, compute: pythonCompute(true), stringifyMs: 0.5 });
  assert.equal(h.accelerated, true);
  assert.match(h.basis, /js kernel for distance_matrix at the same size class/);
  assert.deepEqual(acc.scheduler.measurements().map((m) => m.op).sort(),
    ["sequence.counts:distance_matrix", "sequence.counts:distance_matrix", "sequence.counts:gc_content"]);
});

test("settle: never accelerated from the cost model, a cold run, a software or an unidentified adapter", async () => {
  // the cost model alone puts the giant workload on the GPU; that is a prediction, not evidence
  const acc = new SequenceAccelerator({ gpu: fakeGPU({ cold: true }) });
  const o = await acc.prepare("native.distance_matrix", giant);
  assert.equal(o.plan.executed.id, "webgpu");
  const g = acc.settle("native.distance_matrix", giant, { gpu: o.gpu, compute: pythonCompute(true) });
  assert.equal(g.accelerated, false);
  assert.equal(g.cold, true);
  assert.ok(Math.abs(g.gpu_ms - (5 + 40 + o.gpu.digest_ms + 2)) < 1e-6, "a cold call's own time includes its set-up");
  assert.deepEqual(acc.scheduler.measurements(), [], "a cold run is not a measurement to learn from");
  for (const kind of ["software", "unidentified"]) {
    const a = new SequenceAccelerator({ gpu: fakeGPU({ kind }), simd: noSIMD });
    a.learn("native.distance_matrix", huge, "js", 10_000);     // a CPU kernel measured far slower
    const r = await a.prepare("native.distance_matrix", huge);
    const s = a.settle("native.distance_matrix", huge, { gpu: r.gpu, compute: pythonCompute(true) });
    assert.equal(s.accelerated, false, kind);
    assert.notEqual(s.evidence, "accelerated", kind);
    assert.match(s.basis, kind === "software" ? /software renderer is never GPU acceleration/ : /unidentified/);
  }
});

test("settle: a software adapter the runtime refused, and a packet Python rejected", async () => {
  const sw = new SequenceAccelerator({ gpu: new SequenceCompute({ navigator: { gpu: { requestAdapter: async () => ({ info: { vendor: "google", architecture: "swiftshader" } }) } } }), simd: noSIMD });
  const o = await sw.prepare("native.distance_matrix", huge);
  assert.equal(o.packet, null);
  const g = sw.settle("native.distance_matrix", huge, { gpu: o.gpu, compute: { backend: "js" } });
  assert.equal(g.dispatched, false);
  assert.equal(g.validated, false);
  assert.equal(g.accelerated, false);
  assert.equal(g.adapter.kind, "software");
  assert.match(g.fallback_reason, /software/);
  assert.equal(g.evidence, "reported");
  // the next call does not ask the software adapter again
  const again = await sw.prepare("native.distance_matrix", huge);
  assert.match(again.plan.passed_over.find((r) => r.id === "webgpu").reason, /software/);

  const acc = new SequenceAccelerator({ gpu: fakeGPU(), simd: noSIMD });
  const p = await acc.prepare("native.distance_matrix", huge);
  const r = acc.settle("native.distance_matrix", huge, { gpu: p.gpu, compute: pythonCompute(false, { rejected: "the counts of pair 0,1 differ from Python's" }) });
  assert.equal(r.dispatched, true);
  assert.equal(r.validated, false);
  assert.equal(r.evidence, "dispatched");
  assert.match(r.fallback_reason, /Python did not use the GPU's counts: the counts of pair 0,1/);
  assert.equal(acc.evidence().rejected, 1);
  assert.deepEqual(acc.scheduler.measurements(), [], "a rejected packet's time is not learned");
  assert.equal(acc.settle("native.global_alignment", {}, { gpu: null }), null, "no GPU path, no GPU evidence");
  // a call that failed after its counts were prepared earns nothing, and says why
  const q = await acc.prepare("native.distance_matrix", huge);
  const failed = acc.settle("native.distance_matrix", huge, { gpu: q.gpu, compute: { backend: "pyodide", operation: "native.distance_matrix" } });
  assert.equal(failed.validated, false);
  assert.equal(failed.accelerated, false);
  assert.match(failed.fallback_reason, /did not use the GPU's counts/);
});

test("like for like: with equal raw times the CPU kernel keeps the work when the GPU's hand-off makes it slower", async () => {
  const acc = new SequenceAccelerator({ gpu: fakeGPU({ workMs: 10 }), simd: noSIMD });
  const ops = planSequenceCompute("native.distance_matrix", huge).operations;
  const o = await acc.prepare("native.distance_matrix", huge);
  acc.settle("native.distance_matrix", huge, { gpu: o.gpu, compute: pythonCompute(true, { handoff: 6 }), stringifyMs: 2 });
  acc.learn("native.distance_matrix", huge, "js", 10);      // the CPU kernel took what the GPU kernel took
  const op = countsOp("native.distance_matrix");
  assert.ok(acc.scheduler.measured("webgpu", op, ops) > 10, "the GPU's time includes its hand-off");
  assert.equal(acc.scheduler.plan(countTask("native.distance_matrix", huge)).order[0].id, "js");
  assert.equal(acc.scheduler.compare(op, ops, "webgpu", ["wasm-simd", "js"]).faster, false);
});

test("a device that failed its known-answer test is set aside at once", async () => {
  const gpu = fakeGPU({ knownAnswer: null });
  gpu.prepare = async function () {
    // what SequenceCompute does when the device computes a known answer wrong
    this.knownAnswer = { ok: false, ms: 2, reason: "native.distance_matrix count 4 is 49, not 48" };
    this.fallbackReason = `WebGPU known-answer test failed on this device: ${this.knownAnswer.reason}`;
    this.lastRun = { dispatched: false, cold: true, setup_ms: 3, work_ms: null };
    return null;
  };
  const acc = new SequenceAccelerator({ gpu, simd: noSIMD });
  const o = await acc.prepare("native.distance_matrix", huge);
  assert.equal(o.packet, null);
  assert.equal(acc.scheduler.health("webgpu").setAside, true, "after one failure, not two");
  assert.match(acc.scheduler.health("webgpu").reason, /known-answer/);
  const g = acc.settle("native.distance_matrix", huge, { gpu: o.gpu, compute: { backend: "js" } });
  assert.equal(g.verified, false);
  assert.equal(g.dispatched, false);
  assert.match(g.fallback_reason, /known-answer/);
  assert.match(acc.evidence().set_aside, /known-answer/);
});

test("decorateReceipt: receipt/1 follows receipt.compute, evidence rises only for GPU counts used, only a degradation is a fallback", () => {
  const receipt = (extra = {}) => ({
    device: "cpu",
    execution: { runtime: "pyodide", kernel: null, device: { requested: null, assigned: "cpu", used: "cpu", evidence: "selected" } },
    result: { kind: "computation", outcome: "succeeded", fallbacks: [] }, ...extra,
  });
  // GPU counts used: Python set validated; the Worker raises it to what the session showed, no further
  const used = receipt({ device: "webgpu", compute: { backend: "webgpu", gpu: { validated: true, handoff_ms: 1 } } });
  used.execution.device = { requested: null, assigned: "webgpu", used: "webgpu", evidence: "validated" };
  decorateReceipt(used, { tool: "native.distance_matrix", preference: "auto", packet: {}, plan: { considered: [{ id: "webgpu" }], tried: [{ id: "webgpu", outcome: "ok", ms: 0 }], executed: { id: "webgpu" } }, gpu: { validated: true, evidence: "verified", accelerated: false } });
  assert.equal(used.execution.kernel, "webgpu");
  assert.equal(used.execution.device.requested, "auto");
  assert.equal(used.execution.device.evidence, "verified");
  assert.equal(used.compute.gpu.handoff_ms, 1, "what Python wrote is kept");
  assert.equal(used.compute.gpu.evidence, "verified");
  assert.deepEqual(used.compute.plan, { considered: ["webgpu"], passed_over: [], tried: [{ id: "webgpu", outcome: "ok" }], chosen: "webgpu" });
  assert.deepEqual(used.result.fallbacks, []);
  assert.equal(used.device, "webgpu", "receipt.device is Python's");
  // the GPU's evidence never moves the CPU's: counts not used, the device stays as Python wrote it
  const cpu = receipt({ compute: { backend: "wasm-simd" } });
  decorateReceipt(cpu, { tool: "native.distance_matrix", preference: "cpu", plan: { passed_over: [{ id: "webgpu", reason: "CPU only was asked for" }] }, gpu: { validated: false, evidence: "accelerated" } });
  assert.equal(cpu.execution.device.evidence, "selected");
  assert.equal(cpu.execution.kernel, "wasm-simd");
  assert.equal(cpu.execution.device.requested, "cpu");
  assert.equal(cpu.compute.fallback_reason, "CPU only was asked for");
  assert.deepEqual(cpu.result.fallbacks, [], "the person's own choice of the CPU is not a degradation");
  // no compute receipt from Python (a call that failed before computing): the Worker's pyodide one, mirrored
  const failed = receipt();
  decorateReceipt(failed, { tool: "native.distance_matrix", preference: "reference", plan: { fallback_reason: "Not an input for the GPU kernel" } });
  assert.equal(failed.compute.backend, "pyodide");
  assert.equal(failed.execution.kernel, "pyodide");
  assert.equal(failed.compute.requested, "reference");
  assert.equal(failed.compute.fallback_reason, "Not an input for the GPU kernel");
  assert.deepEqual(failed.result.fallbacks, [], "an input the GPU kernel does not take is not a degradation");
  // a GPU that was tried and failed is one, recorded once however often the receipt is decorated
  const lost = receipt({ compute: { backend: "js" } });
  const lostPlan = { tried: [{ id: "webgpu", outcome: "error", reason: "WebGPU device lost: GPU process crashed" }, { id: "js", outcome: "ok" }], executed: { id: "js" }, fallback_reason: "WebGPU device lost: GPU process crashed" };
  decorateReceipt(lost, { tool: "native.distance_matrix", plan: lostPlan });
  decorateReceipt(lost, { tool: "native.distance_matrix", plan: lostPlan });
  assert.deepEqual(lost.result.fallbacks, [{ what: "compute", reason: "WebGPU device lost: GPU process crashed" }]);
  // a legacy receipt without receipt/1 is only given its compute fields
  const legacy = { device: "cpu" };
  decorateReceipt(legacy, { tool: "native.edit_distance" });
  assert.equal(legacy.compute.backend, "pyodide");
  assert.equal(legacy.execution, undefined);
  assert.deepEqual(planSummary({}), { considered: [], passed_over: [], tried: [], chosen: null });
});

test("result.fallbacks: only a GPU that was tried and failed, set aside, or whose counts Python rejected", async () => {
  // routine CPU paths: small work, a preference, an input the kernel does not take, a cheaper kernel, a software adapter
  const acc = new SequenceAccelerator({ gpu: fakeGPU(), simd: noSIMD });
  for (const [tool, args, preference] of [["native.hamming_distance", { a: "ACGT", b: "ACGA" }, "auto"], ["native.distance_matrix", huge, "cpu"],
    ["native.distance_matrix", huge, "reference"], ["native.gc_content", { sequence: "XYZ" }, "auto"], ["native.distance_matrix", big, "auto"]]) {
    const o = await acc.prepare(tool, args, { preference });
    assert.equal(o.packet, null, `${tool} ${preference}`);
    assert.equal(gpuDegradation(o.plan, { backend: "js" }), null, `${tool} ${preference}: ${JSON.stringify(o.plan.passed_over)}`);
    const r = { compute: { backend: "js" }, execution: { device: { evidence: "selected" } }, result: { fallbacks: [] } };
    decorateReceipt(r, { tool, preference, plan: o.plan });
    assert.deepEqual(r.result.fallbacks, [], `${tool} ${preference}`);
  }
  const sw = new SequenceAccelerator({ gpu: new SequenceCompute({ navigator: { gpu: { requestAdapter: async () => ({ info: { vendor: "google", architecture: "swiftshader" } }) } } }), simd: noSIMD });
  const first = await sw.prepare("native.distance_matrix", huge), second = await sw.prepare("native.distance_matrix", huge);
  assert.equal(first.plan.tried[0].outcome, "error");
  assert.equal(gpuDegradation(first.plan, {}), null, "a machine whose only adapter is software has no GPU to fall back from");
  assert.equal(gpuDegradation(second.plan, {}), null);
  // degradations: a lost device, the session's set-aside after it, and counts Python rejected
  const broken = new SequenceAccelerator({ gpu: fakeGPU({ fail: "WebGPU device lost: GPU process crashed" }), simd: noSIMD });
  const lost = await broken.prepare("native.distance_matrix", huge);
  assert.match(gpuDegradation(lost.plan, {}), /device lost/);
  const later = await broken.prepare("native.distance_matrix", huge);
  assert.match(gpuDegradation(later.plan, {}), /^set aside for this session: device lost/);
  assert.match(gpuDegradation(null, { gpu_rejected: "the counts of pair 0,1 differ from Python's" }), /Python did not use the GPU's counts: the counts of pair 0,1/);
  // Python already recorded the rejection (browser_compute._mirror): the event is listed once
  const rejected = "the counts of pair 0,1 differ from Python's";
  const receipt = { compute: { backend: "pyodide", gpu_rejected: rejected }, execution: { device: {} },
    result: { fallbacks: [{ what: "compute", reason: `the WebGPU counts were not used: ${rejected}` }] } };
  decorateReceipt(receipt, { tool: "native.distance_matrix", packet: { counts: [] } });
  assert.deepEqual(receipt.result.fallbacks.map((f) => f.what), ["compute"]);
  const fresh = { compute: { backend: "pyodide", gpu_rejected: rejected }, execution: { device: {} }, result: { fallbacks: [] } };
  decorateReceipt(fresh, { tool: "native.distance_matrix", packet: { counts: [] } });
  assert.equal(fresh.result.fallbacks.length, 1, "without Python's entry the Worker still says it");
});
