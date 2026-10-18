import assert from "node:assert/strict";
import { test } from "node:test";
import { KNOWN_ANSWERS, SequenceCompute, WEBGPU_TOOLS, classifyAdapter, planSequenceCompute, referenceCounts } from "../../js/runtime/webgpu.js";

/** The WGSL kernel's semantics on the CPU, from the buffers it was bound to: what a working device computes. */
function emulate(bases, params) {
  const [width, n, mode, window, records] = params;
  const counts = new Uint32Array(records * 4);
  for (let record = 0; record < records; record++) {
    const i = Math.floor(record / n), j = record % n;
    if (mode === 0 && i >= j) continue;
    const start = mode === 2 && record > 0 ? record - 1 : 0, w = mode === 2 && record > 0 ? window : width;
    for (let k = 0; k < w; k++) {
      if (mode === 0) {
        const a = bases[i * width + k], b = bases[j * width + k];
        if (!a || !b) continue;
        counts[record * 4]++;
        if (a !== b) { counts[record * 4 + 1]++; if ((a === 65 && b === 71) || (a === 71 && b === 65) || (a === 67 && b === 84) || (a === 84 && b === 67)) counts[record * 4 + 2]++; }
      } else if (mode === 1) { if (bases[k] !== bases[width + k]) counts[record * 4]++; }
      else if ([71, 67, 83].includes(bases[start + k])) counts[record * 4]++;
    }
  }
  return counts;
}

/**
 * A WebGPU device double that runs the recorded commands on submit: the dispatch (emulated), buffer copies and
 * timestamp resolves. corruptFrom: dispatches after this many come back with one count wrong (0: all of them).
 */
function fakeGPU({ fail = false, lost = false, corrupt = false, corruptFrom = 0, slow = false, limit = 2 ** 28, info = { vendor: "nvidia", architecture: "ampere" }, timestamps = false } = {}) {
  let resolveLoss;
  const destroyed = [], submitted = [], deviceRequests = [];
  const device = {
    features: new Set(timestamps ? ["timestamp-query"] : []),
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
    createQuerySet({ type, count }) { return { type, count, destroy() {} }; },
    createBindGroup({ entries }) { return { entries }; },
    createCommandEncoder() {
      const commands = [];
      return {
        beginComputePass(descriptor) {
          const pass = { op: "dispatch", descriptor };
          return { setPipeline() {}, setBindGroup(i, group) { pass.group = group; }, dispatchWorkgroups(x, y) { pass.x = x; pass.y = y; }, end() { commands.push(pass); } };
        },
        copyBufferToBuffer(source, sourceOffset, target, targetOffset, size) { commands.push({ op: "copy", source, sourceOffset, target, targetOffset, size }); },
        resolveQuerySet(querySet, first, count, target, offset) { commands.push({ op: "resolve", target, offset }); },
        finish() { return { commands }; },
      };
    },
    queue: {
      writeBuffer(buffer, offset, array) { new Uint8Array(buffer.data).set(new Uint8Array(array.buffer, array.byteOffset, array.byteLength), offset); },
      submit([commandBuffer]) {
        for (const c of commandBuffer.commands) {
          if (c.op === "dispatch") {
            submitted.push([c.x, c.y]);
            const [input, output, params] = c.group.entries.map((e) => e.resource.buffer);
            const counts = emulate(new Uint32Array(input.data), new Uint32Array(params.data));
            if ((corrupt || corruptFrom) && submitted.length > corruptFrom) counts[4]++;
            new Uint32Array(output.data).set(counts);
          } else if (c.op === "copy") {
            new Uint8Array(c.target.data, c.targetOffset, c.size).set(new Uint8Array(c.source.data, c.sourceOffset, c.size));
          } else if (c.op === "resolve") new BigUint64Array(c.target.data, c.offset, 2).set([1_000_000n, 3_500_000n]);
        }
      },
    }, destroy() { resolveLoss({ reason: "destroyed" }); },
  };
  let adapters = 0;
  const adapter = { info, features: new Set(timestamps ? ["timestamp-query"] : []), async requestDevice(descriptor) { deviceRequests.push(descriptor); return device; } };
  return { navigator: { gpu: { async requestAdapter() { adapters++; return adapter; } } },
    device, destroyed, submitted, deviceRequests, get adapters() { return adapters; } };
}

// ------------------------------------------------------------------------------------------- the classifier

test("one classifier: software renderers, physical vendors, and an adapter that says nothing is unidentified", () => {
  const table = [
    [{ vendor: "google", architecture: "swiftshader" }, "software"],                          // Chromium's SwiftShader
    [{ vendor: "mesa", device: "llvmpipe (LLVM 17.0.6, 256 bits)" }, "software"],              // Mesa, in the device field
    [{ vendor: "mesa", architecture: "lavapipe" }, "software"],
    [{ vendor: "", description: "Microsoft Basic Render Driver" }, "software"],               // Windows without a driver
    [{ vendor: "microsoft", architecture: "warp" }, "software"],
    [{ vendor: "nvidia", architecture: "ampere", isFallbackAdapter: true }, "software"],       // the flag wins over a name
    [{ vendor: "apple", architecture: "metal-3" }, "physical"],
    [{ vendor: "nvidia", architecture: "ampere" }, "physical"],
    [{ vendor: "amd", architecture: "rdna-3" }, "physical"],
    [{ vendor: "intel", architecture: "gen-12lp" }, "physical"],
    [{ vendor: "", description: "NVIDIA GeForce RTX 4090" }, "physical"],
    [{ vendor: "qualcomm-acpi", architecture: "adreno-7xx" }, "physical"],
    [{}, "unidentified"],
    [{ vendor: "", architecture: "", device: "", description: "" }, "unidentified"],
    [{ vendor: "mesa" }, "unidentified"],                                                      // a software vendor id
    [{ vendor: "test", architecture: "hardware" }, "unidentified"],                            // a name nobody knows
  ];
  for (const [info, kind] of table) {
    const got = classifyAdapter(info);
    assert.equal(got.kind, kind, JSON.stringify(info));
    assert.ok(got.reason, "every answer says why");
  }
  assert.equal(classifyAdapter(null).kind, "unidentified");
  assert.equal(classifyAdapter(undefined).kind, "unidentified");
  assert.equal(classifyAdapter({ vendor: "nvidia" }, { isFallbackAdapter: true }).kind, "software", "the older flag on the adapter");
  assert.deepEqual(Object.keys(classifyAdapter({})), ["kind", "vendor", "architecture", "device", "description", "fallback", "reason"]);
  assert.match(classifyAdapter({ vendor: "apple" }).reason, /not a measurement/);
});

test("the known answers are what the CPU reference computes for their inputs", () => {
  assert.equal(KNOWN_ANSWERS.length, 3, "one per shader mode");
  assert.deepEqual(KNOWN_ANSWERS.map((k) => k.tool).sort(), [...WEBGPU_TOOLS].sort());
  for (const k of KNOWN_ANSWERS) {
    const plan = planSequenceCompute(k.tool, k.args);
    assert.ok(plan, k.tool);
    assert.deepEqual(Array.from(referenceCounts(plan)), k.counts, k.tool);
    assert.ok(k.counts.some((v, i) => i % 4 === 2 && v > 0) || plan.mode !== 0, "transitions are exercised");
  }
  assert.ok(planSequenceCompute(KNOWN_ANSWERS[2].tool, KNOWN_ANSWERS[2].args).window > 64, "a window wider than one 64-lane pass");
});

// ------------------------------------------------------------------------------------------- the runtime

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
  const gpu = fakeGPU();
  const compute = new SequenceCompute(gpu);
  assert.equal(await compute.prepare("native.distance_matrix", args, { preference: "cpu" }), null);
  assert.equal(await compute.prepare("native.gc_content", { sequence: "ACGT" }), null);
  assert.equal(await compute.prepare("native.fisher_exact", {}), null);
  assert.equal(gpu.adapters, 0);
  assert.equal(compute.lastRun, null, "nothing reached the GPU");
  assert.equal(compute.adapter, null);
});

test("a device passes its known-answer test once, then the call is dispatched, checked and its buffers released", async () => {
  const args = { sequences: ["AGCTN".repeat(10000), "GGTTN".repeat(10000)] };
  const plan = planSequenceCompute("native.distance_matrix", args);
  const gpu = fakeGPU(), compute = new SequenceCompute(gpu);
  const out = await compute.prepare(plan.tool, args);
  assert.equal(out.backend, "webgpu");
  assert.deepEqual(out.counts, Array.from(referenceCounts(plan)));
  assert.equal(gpu.submitted.length, KNOWN_ANSWERS.length + 1, "three known answers, then the call");
  assert.equal(gpu.destroyed.length, 4 * gpu.submitted.length, "every buffer of every dispatch released");
  assert.equal(compute.knownAnswer.ok, true);
  assert.equal(compute.adapter.kind, "physical");
  const run = compute.lastRun;
  assert.equal(run.dispatched, true);
  assert.equal(run.checked, true);
  assert.equal(run.cold, true, "this call created the device and ran the known-answer test");
  for (const k of ["setup_ms", "pack_ms", "transfer_ms", "check_ms", "work_ms"]) assert.ok(run[k] >= 0, k);
  assert.equal(run.kernel_ms, null, "no timestamps unless asked for");
  await compute.prepare(plan.tool, args);
  assert.equal(gpu.adapters, 1, "device reused for eligible calls");
  assert.equal(gpu.submitted.length, KNOWN_ANSWERS.length + 2, "the known-answer test is not repeated on the same device");
  assert.equal(compute.lastRun.cold, false);
  assert.deepEqual(gpu.deviceRequests, [undefined], "production asks for no optional feature");
  compute.stop();
  assert.equal(compute.knownAnswer, null, "a stopped device's test does not carry over");
});

test("a device that fails its known-answer test is never used, and says so", async () => {
  const args = { sequences: ["AGCT".repeat(10000), "GGTT".repeat(10000)] };
  const gpu = fakeGPU({ corrupt: true }), compute = new SequenceCompute(gpu);
  assert.equal(await compute.prepare("native.distance_matrix", args), null);
  assert.match(compute.fallbackReason, /known-answer test failed/);
  assert.equal(compute.knownAnswer.ok, false);
  assert.equal(compute.lastRun.dispatched, false, "the call's own kernel was never submitted");
  assert.equal(gpu.submitted.length, 1, "the first wrong known answer stops the test");
  assert.equal(await compute.prepare("native.distance_matrix", args), null);
  assert.equal(gpu.submitted.length, 1, "and it is not retried on the same device");
});

test("a known-answer test that could not run keeps no verdict: this call falls back, the next one tests again", async () => {
  const args = { sequences: ["AGCT".repeat(10000), "GGTT".repeat(10000)] };
  const gpu = fakeGPU({ slow: true }), compute = new SequenceCompute({ ...gpu, timeoutMs: 20 });
  assert.equal(await compute.prepare("native.distance_matrix", args), null);
  assert.match(compute.fallbackReason, /known-answer test could not run: WebGPU operation timed out/);
  assert.equal(compute.knownAnswer, null, "a timeout is not a wrong answer");
  // the readback answers in time now: the test runs again on the same device and passes
  const createBuffer = gpu.device.createBuffer;
  gpu.device.createBuffer = function (d) { const b = createBuffer.call(this, d); b.mapAsync = async () => {}; return b; };
  assert.equal((await compute.prepare("native.distance_matrix", args)).backend, "webgpu");
  assert.equal(compute.knownAnswer.ok, true);
  assert.equal(compute.lastRun.cold, true, "the known-answer test ran with this call");
  assert.equal(gpu.adapters, 1, "the same device");
});

test("a call whose counts fail the sample check was dispatched, not checked", async () => {
  const args = { sequences: ["AGCT".repeat(10000), "GGTT".repeat(10000)] };
  const gpu = fakeGPU({ corruptFrom: KNOWN_ANSWERS.length }), compute = new SequenceCompute(gpu);
  assert.equal(await compute.prepare("native.distance_matrix", args), null);
  assert.match(compute.fallbackReason, /sample check/);
  assert.equal(compute.knownAnswer.ok, true);
  assert.equal(compute.lastRun.dispatched, true);
  assert.equal(compute.lastRun.checked, false);
});

test("adapter, shader, buffer-limit, corrupt-output and device-loss failures fall back to CPU", async () => {
  const args = { sequences: ["AGCT".repeat(10000), "GGTT".repeat(10000)] };
  const plan = planSequenceCompute("native.distance_matrix", args);
  for (const options of [{ fail: true }, { corrupt: true }, { lost: true }, { limit: 4 }]) {
    const gpu = fakeGPU(options), compute = new SequenceCompute(gpu);
    assert.equal(await compute.prepare(plan.tool, args), null);
    assert.ok(compute.fallbackReason);
    assert.equal(compute.lastRun?.dispatched, false, `${JSON.stringify(options)}: the call's kernel was not submitted`);
    compute.stop();
  }
  const compute = new SequenceCompute({ navigator: { gpu: { requestAdapter: async () => null } } });
  assert.equal(await compute.prepare(plan.tool, args), null);
  assert.match(compute.fallbackReason, /No WebGPU adapter/);
  assert.equal(compute.adapter, null);
  const software = new SequenceCompute({ navigator: { gpu: { requestAdapter: async () => ({ info: { vendor: "SwiftShader" } }) } } });
  assert.equal(await software.prepare(plan.tool, args), null);
  assert.match(software.fallbackReason, /software/);
  assert.equal(software.adapter.kind, "software");
  // a software renderer named only in the device field is refused too
  const llvmpipe = fakeGPU({ info: { vendor: "mesa", device: "llvmpipe (LLVM 17.0.6, 256 bits)" } });
  const refused = new SequenceCompute(llvmpipe);
  assert.equal(await refused.prepare(plan.tool, args), null);
  assert.match(refused.fallbackReason, /software/);
  assert.equal(llvmpipe.submitted.length, 0, "nothing was dispatched to it");
  // the smoke harness may use it explicitly
  const allowed = new SequenceCompute({ ...fakeGPU({ info: { vendor: "google", architecture: "swiftshader" } }), allowSoftware: true });
  assert.equal((await allowed.prepare(plan.tool, args)).backend, "webgpu");
  assert.equal(allowed.adapter.kind, "software");
});

test("an unidentified adapter is used like any other (its counts are checked) but never classed as physical", async () => {
  const args = { sequences: ["AGCT".repeat(10000), "GGTT".repeat(10000)] };
  const gpu = fakeGPU({ info: {} }), compute = new SequenceCompute(gpu);
  assert.equal((await compute.prepare("native.distance_matrix", args)).backend, "webgpu");
  assert.equal(compute.adapter.kind, "unidentified");
});

test("timestamps, when asked for and supported, give the GPU's own kernel time", async () => {
  const args = { sequences: ["AGCT".repeat(10000), "GGTT".repeat(10000)] };
  const gpu = fakeGPU({ timestamps: true }), compute = new SequenceCompute({ ...gpu, timestamps: true });
  assert.ok(await compute.prepare("native.distance_matrix", args));
  assert.deepEqual(gpu.deviceRequests, [{ requiredFeatures: ["timestamp-query"] }]);
  assert.equal(compute.lastRun.kernel_ms, 2.5, "(3 500 000 - 1 000 000) ns");
  const plain = fakeGPU(), without = new SequenceCompute({ ...plain, timestamps: true });
  assert.ok(await without.prepare("native.distance_matrix", args));
  assert.equal(without.lastRun.kernel_ms, null);
  assert.match(without.lastRun.kernel_note, /timestamp/);
});

test("cancellation during readback releases buffers and never silently retries on CPU", async () => {
  const args = { sequence: "GCSN".repeat(10000) };
  const gpu = fakeGPU({ slow: true }), compute = new SequenceCompute(gpu);
  let aborted = false;
  const timer = setTimeout(() => { aborted = true; }, 5);
  await assert.rejects(compute.prepare("native.gc_content", args, { isCancelled: () => aborted }), (e) => e.code === "cancelled");
  clearTimeout(timer);
  assert.equal(gpu.destroyed.length, 4);
  compute.stop();
});

test("unresponsive adapter timeout falls back instead of hanging a scientific call", async () => {
  const compute = new SequenceCompute({ navigator: { gpu: { requestAdapter: () => new Promise(() => {}) } }, timeoutMs: 20 });
  assert.equal(await compute.prepare("native.gc_content", { sequence: "A".repeat(40000) }), null);
  assert.match(compute.fallbackReason, /timed out/);
});
