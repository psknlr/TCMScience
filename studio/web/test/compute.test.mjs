// The hardware-adaptive layer: capability probing (by trying, never by browser name), the scheduler's choices and
// fallbacks, and the Worker's decision whether sequence counts are prepared on the GPU.
import assert from "node:assert/strict";
import { test } from "node:test";
import { CAPABILITIES_SCHEMA, fingerprint, probeCapabilities, summarizeCapabilities } from "../js/compute/capabilities.js";
import { ComputeScheduler, SchedulerError } from "../js/compute/scheduler.js";
import { GPU_MIN_OPERATIONS, SequenceAccelerator, inputDigest, kernelOptions, normalizePreference } from "../js/compute/accelerator.js";
import { SequenceCompute, planSequenceCompute, referenceCounts } from "../js/runtime/webgpu.js";

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

function fakeGPU({ fail = null } = {}) {
  // a SequenceCompute double that computes the exact counts on the CPU, or fails like a real one
  return {
    fallbackReason: null, stopped: false,
    async prepare(tool, args) {
      if (fail) { this.fallbackReason = fail; return null; }
      const plan = planSequenceCompute(tool, args);
      return { tool, width: plan.width, n: plan.n, window: plan.window, counts: Array.from(referenceCounts(plan)), backend: "webgpu", precision: "uint32 exact counts; Python float64 transforms" };
    },
    stop() { this.stopped = true; },
  };
}

// two sequences of 80 000: the CPU kernel's ~0.1 ms beats the GPU's set-up; forty of 8 000 (6.2 M comparisons,
// quadratic in the number of sequences) is where the GPU pays for itself
const big = { sequences: { a: "ACGT".repeat(20000), b: "ACGA".repeat(20000) }, model: "p" };
const huge = { sequences: Array.from({ length: 40 }, (_, i) => "ACGT".repeat(2000).slice(i) + "ACGT".slice(0, i % 4).padEnd(i, "A").slice(0, i)), model: "p" };

test("the accelerator: large work goes to the GPU with a digest Python can check; CPU-only and reference never do", async () => {
  const acc = new SequenceAccelerator({ gpu: fakeGPU() });
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
  const acc = new SequenceAccelerator({ gpu: fakeGPU() });
  const small = await acc.prepare("native.hamming_distance", { a: "ACGT", b: "ACGA" });
  assert.equal(small.packet, null);
  assert.match(small.plan.passed_over.find((r) => r.id === "webgpu").reason, /Small workload/);
  assert.ok(GPU_MIN_OPERATIONS > 4);
  const broken = new SequenceAccelerator({ gpu: fakeGPU({ fail: "WebGPU device lost: GPU process crashed" }) });
  const o = await broken.prepare("native.distance_matrix", huge);
  assert.equal(o.packet, null);
  assert.equal(o.plan.tried[0].outcome, "error");
  assert.equal(broken.scheduler.health("webgpu").setAside, true, "a lost device is not tried again this session");
  assert.equal((await broken.prepare("native.fisher_exact", {})).packet, null, "not a sequence tool");
  assert.equal((await broken.prepare("native.gc_content", { sequence: "XYZ" })).plan.executed, null, "input the kernels do not take");
});

test("the accelerator learns from what ran inside Python", async () => {
  const acc = new SequenceAccelerator({ gpu: fakeGPU() });
  assert.equal((await acc.prepare("native.distance_matrix", huge)).plan.executed.id, "webgpu");
  // on this device the CPU kernel turned out much faster than the cost model thought, and the GPU slower
  const ops = planSequenceCompute("native.distance_matrix", huge).operations;
  acc.learn("native.distance_matrix", huge, "js", 0.01);
  acc.scheduler.record("webgpu", "sequence.counts", ops, 500);
  assert.equal((await acc.prepare("native.distance_matrix", huge)).plan.executed.id, "js");
});

test("the real SequenceCompute rejects software adapters, so the scheduler falls back on SwiftShader", async () => {
  const gpu = new SequenceCompute({ navigator: { gpu: { requestAdapter: async () => ({ info: { vendor: "google", architecture: "swiftshader" } }) } } });
  const acc = new SequenceAccelerator({ gpu });
  const o = await acc.prepare("native.distance_matrix", huge);
  assert.equal(o.packet, null);
  assert.match(o.plan.tried[0].reason, /software/);
  assert.equal(o.plan.executed.id, "js", "the counting falls to the CPU kernel");
});
