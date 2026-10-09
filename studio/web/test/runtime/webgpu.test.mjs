import assert from "node:assert/strict";
import { test } from "node:test";
import { SequenceCompute, WEBGPU_TOOLS, planSequenceCompute, referenceCounts } from "../../js/runtime/webgpu.js";

function fakeGPU(plan, { fail = false, lost = false, corrupt = false, slow = false, limit = 2 ** 28 } = {}) {
  let resolveLoss;
  const destroyed = [], submitted = [];
  const device = {
    lost: new Promise((resolve) => { resolveLoss = resolve; }),
    limits: { maxStorageBufferBindingSize: limit, maxBufferSize: limit, maxComputeWorkgroupsPerDimension: 65535 },
    pushErrorScope() {}, async popErrorScope() { return fail ? { message: "shader validation failed" } : null; },
    createShaderModule({ code }) { assert.match(code, /@compute @workgroup_size\(64\)/); return {}; },
    async createComputePipelineAsync() { return { getBindGroupLayout() { return {}; } }; },
    createBuffer({ size, usage }) {
      return { data: new ArrayBuffer(size), usage, destroy() { destroyed.push(this); }, unmap() {},
        async mapAsync() { if (slow) await new Promise((r) => setTimeout(r, 100)); if (lost) { resolveLoss({ message: "GPU disconnected" }); throw new Error("lost"); } },
        getMappedRange() { return this.data; },
      };
    },
    createBindGroup() { return {}; },
    createCommandEncoder() { return {
      beginComputePass() { return { setPipeline() {}, setBindGroup() {}, dispatchWorkgroups(x, y) { submitted.push([x, y]); }, end() {} }; },
      copyBufferToBuffer(source, a, target) { this.target = target; }, finish() { return this; },
    }; },
    queue: { writeBuffer(buffer, offset, array) { new Uint8Array(buffer.data).set(new Uint8Array(array.buffer, array.byteOffset, array.byteLength), offset); },
      submit(encoders) { const counts = referenceCounts(plan); if (corrupt) counts[4]++; new Uint32Array(encoders[0].target.data).set(counts); },
    }, destroy() { resolveLoss({ reason: "destroyed" }); },
  };
  let adapters = 0;
  return { navigator: { gpu: { async requestAdapter() { adapters++; return { info: { vendor: "test", architecture: "hardware" }, async requestDevice() { return device; } }; } } },
    device, destroyed, submitted, get adapters() { return adapters; } };
}

test("planner covers all existing sequence tools and preserves IUPAC semantics", () => {
  assert.equal(WEBGPU_TOOLS.length, 3);
  const distance = planSequenceCompute("native.distance_matrix", { sequences: { a: "a c uN-", b: "AGTAA" }, model: "k2p" });
  assert.deepEqual(distance.seqs, ["ACTN-", "AGTAA"]);
  assert.deepEqual(Array.from(referenceCounts(distance)), [0, 0, 0, 0, 3, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]);
  const hamming = planSequenceCompute("native.hamming_distance", { a: "aNu", b: "ACT" });
  assert.equal(referenceCounts(hamming)[0], 2, "hamming keeps ambiguity and U distinct");
  const gc = planSequenceCompute("native.gc_content", { sequence: "GCASN", window: 2 });
  assert.deepEqual(Array.from(referenceCounts(gc)), [3, 0, 0, 0, 2, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0]);
  for (const [tool, args] of [["native.distance_matrix", { sequences: ["A", "AA"] }], ["native.gc_content", { sequence: "X" }], ["native.gc_content", { sequence: "A", window: 2 }]]) assert.equal(planSequenceCompute(tool, args), null);
  assert.equal(planSequenceCompute("native.gc_content", { sequence: "A".repeat(100000), window: 50000 }), null, "bounded quadratic windows use Python");
});

test("CPU preference, small workloads and unsupported tools never allocate a GPU", async () => {
  const args = { sequences: ["A".repeat(100000), "G".repeat(100000)] };
  const gpu = fakeGPU(planSequenceCompute("native.distance_matrix", args));
  const compute = new SequenceCompute(gpu);
  assert.equal(await compute.prepare("native.distance_matrix", args, { preference: "cpu" }), null);
  assert.equal(await compute.prepare("native.gc_content", { sequence: "ACGT" }), null);
  assert.equal(await compute.prepare("native.fisher_exact", {}), null);
  assert.equal(gpu.adapters, 0);
});

test("successful dispatch reads exact counts and releases all GPU buffers", async () => {
  const args = { sequences: ["AGCTN".repeat(10000), "GGTTN".repeat(10000)] };
  const plan = planSequenceCompute("native.distance_matrix", args);
  const gpu = fakeGPU(plan), compute = new SequenceCompute(gpu);
  const out = await compute.prepare(plan.tool, args);
  assert.equal(out.backend, "webgpu");
  assert.deepEqual(out.counts, Array.from(referenceCounts(plan)));
  assert.equal(gpu.submitted.length, 1);
  assert.equal(gpu.destroyed.length, 4);
  await compute.prepare(plan.tool, args);
  assert.equal(gpu.adapters, 1, "device reused for eligible calls");
  compute.stop();
});

test("adapter, shader, buffer-limit, corrupt-output and device-loss failures fall back to CPU", async () => {
  const args = { sequences: ["AGCT".repeat(10000), "GGTT".repeat(10000)] };
  const plan = planSequenceCompute("native.distance_matrix", args);
  for (const options of [{ fail: true }, { corrupt: true }, { lost: true }, { limit: 4 }]) {
    const gpu = fakeGPU(plan, options), compute = new SequenceCompute(gpu);
    assert.equal(await compute.prepare(plan.tool, args), null);
    assert.ok(compute.fallbackReason);
    compute.stop();
  }
  const compute = new SequenceCompute({ navigator: { gpu: { requestAdapter: async () => null } } });
  assert.equal(await compute.prepare(plan.tool, args), null);
  assert.match(compute.fallbackReason, /No WebGPU adapter/);
  const software = new SequenceCompute({ navigator: { gpu: { requestAdapter: async () => ({ info: { vendor: "SwiftShader" } }) } } });
  assert.equal(await software.prepare(plan.tool, args), null);
  assert.match(software.fallbackReason, /software/);
});

test("cancellation during readback releases buffers and never silently retries on CPU", async () => {
  const args = { sequence: "GCSN".repeat(10000) }, plan = planSequenceCompute("native.gc_content", args);
  const gpu = fakeGPU(plan, { slow: true }), compute = new SequenceCompute(gpu);
  let aborted = false;
  const timer = setTimeout(() => { aborted = true; }, 5);
  await assert.rejects(compute.prepare(plan.tool, args, { isCancelled: () => aborted }), (e) => e.code === "cancelled");
  clearTimeout(timer);
  assert.equal(gpu.destroyed.length, 4);
  compute.stop();
});

test("unresponsive adapter timeout falls back instead of hanging a scientific call", async () => {
  const compute = new SequenceCompute({ navigator: { gpu: { requestAdapter: () => new Promise(() => {}) } }, timeoutMs: 20 });
  assert.equal(await compute.prepare("native.gc_content", { sequence: "A".repeat(40000) }), null);
  assert.match(compute.fallbackReason, /timed out/);
});
