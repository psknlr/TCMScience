// The choice of kernel for the domain search (compute/science-scheduler.js): reference, cpu and auto; the GPU only on
// a calibration of this very device that measured it faster at this size; every GPU result checked on the CPU before
// it is used; and the facts a job's evidence keeps, with "accelerated" only for physical + verified + validated +
// calibrated faster.
import assert from "node:assert/strict";
import { test } from "node:test";
import { tanimotoBestJS, wordsFromBits } from "../js/compute/science-cpu.js";
import { ScienceGPU } from "../js/compute/science-gpu.js";
import { runTanimoto } from "../js/compute/science-scheduler.js";
import { calibrationFor, fakeScienceGPU } from "./science-fakes.mjs";

function random(seed) {
  let s = seed >>> 0;
  return () => (s = (Math.imul(s, 1664525) + 1013904223) >>> 0) / 2 ** 32;
}
function fingerprints(r, n, density = 0.025) {
  return wordsFromBits(Array.from({ length: n }, () => { const s = []; for (let b = 0; b < 2048; b++) if (r() < density) s.push(b); return s; }));
}
const plain = (x) => ({ arg: Array.from(x.arg), inter: Array.from(x.inter), union: Array.from(x.union) });
const r = random(20261019);
const Q = fingerprints(r, 40), R = fingerprints(r, 600);
const WANT = plain(tanimotoBestJS(Q, R));
const FASTER = [{ queries: 8, refs: 300, accelerated: false, gpu_ms: 5, cpu_ms: 1 }, { queries: 32, refs: 600, accelerated: true, gpu_ms: 2, cpu_ms: 9 }];

test("reference: no browser kernel ran; Python computes the similarity itself", async () => {
  const gpu = fakeScienceGPU();
  const { result, facts } = await runTanimoto({ queries: Q, refs: R, preference: "reference", gpu: new ScienceGPU(gpu), calibration: await calibrationFor({ navigator: gpu.navigator, sizes: FASTER }) });
  assert.equal(result, null);
  assert.equal(facts.backend, "python");
  assert.match(facts.reason, /Python's tanimoto_best computes the similarity itself/);
  assert.equal(facts.gpu, null);
  assert.equal(facts.accelerated, null);
  assert.equal(gpu.state.adapters, 0, "the GPU was not asked for");
});

test("cpu: WebAssembly SIMD; the GPU is never asked for, whatever the calibration says", async () => {
  const gpu = fakeScienceGPU();
  const { result, facts } = await runTanimoto({ queries: Q, refs: R, preference: "cpu", gpu: new ScienceGPU(gpu), calibration: await calibrationFor({ navigator: gpu.navigator, sizes: FASTER }), navigator: gpu.navigator });
  assert.deepEqual(plain(result), WANT);
  assert.equal(facts.backend, "wasm-simd");
  assert.equal(facts.reason, "CPU only was asked for");
  assert.deepEqual(facts.considered[0], { backend: "webgpu", ok: false, reason: "CPU only was asked for" });
  assert.equal(facts.cpu_baseline.source, "this call");
  assert.equal(gpu.state.adapters, 0);
  assert.deepEqual(facts.size, { queries: 40, refs: 600, words: 64, pairs: 24000 });
  assert.equal(facts.placement, "browser");
  assert.equal(facts.where, "page");
});

test("auto without a calibration, or one that says not faster, or smaller than every calibrated size: the CPU", async () => {
  const gpu = fakeScienceGPU(), g = new ScienceGPU(gpu);
  const none = await runTanimoto({ queries: Q, refs: R, gpu: g, navigator: gpu.navigator });
  assert.equal(none.facts.backend, "wasm-simd");
  assert.equal(none.facts.reason, "no device check has been run on this device");
  const slower = await calibrationFor({ navigator: gpu.navigator, sizes: [{ queries: 32, refs: 600, accelerated: false, gpu_ms: 30, cpu_ms: 9 }] });
  const no = await runTanimoto({ queries: Q, refs: R, gpu: g, calibration: slower, navigator: gpu.navigator });
  assert.equal(no.facts.backend, "wasm-simd");
  assert.match(no.facts.reason, /measured the GPU no faster than the CPU kernel at 32 × 600/);
  const big = await calibrationFor({ navigator: gpu.navigator, sizes: [{ queries: 1000, refs: 10000, accelerated: true, gpu_ms: 3, cpu_ms: 90 }] });
  const small = await runTanimoto({ queries: Q, refs: R, gpu: g, calibration: big, navigator: gpu.navigator });
  assert.equal(small.facts.backend, "wasm-simd");
  assert.match(small.facts.reason, /smaller than every size the device check measured/);
  assert.equal(gpu.state.adapters, 0, "none of these asked for the GPU");
  assert.deepEqual(plain(small.result), WANT);
  const noGpu = await runTanimoto({ queries: Q, refs: R, calibration: await calibrationFor({ navigator: gpu.navigator, sizes: FASTER }) });
  assert.equal(noGpu.facts.reason, "this context has no WebGPU runtime");
});

test("auto on a calibrated device that measured the GPU faster: the GPU's result, checked, and accelerated", async () => {
  const gpu = fakeScienceGPU(), g = new ScienceGPU(gpu);
  const calibration = await calibrationFor({ navigator: gpu.navigator, sizes: FASTER });
  const { result, facts } = await runTanimoto({ queries: Q, refs: R, gpu: g, calibration, navigator: gpu.navigator });
  assert.deepEqual(plain(result), WANT);
  assert.equal(facts.backend, "webgpu");
  assert.match(facts.reason, /measured the GPU faster than the CPU kernel at 32 × 600 \(19,200 comparisons\): 2 ms against 9 ms/);
  assert.equal(facts.validation.ok, true);
  assert.equal(facts.validation.checked, 10, "the first, the last and 8 more searched in full on the CPU");
  assert.equal(facts.gpu.adapter.kind, "physical");
  assert.deepEqual([facts.gpu.dispatched, facts.gpu.validated, facts.gpu.verified], [true, true, true]);
  assert.equal(facts.gpu.known_answer.ok, true);
  assert.equal(facts.evidence, "accelerated");
  assert.equal(facts.accelerated, true);
  assert.equal(facts.calibration_id, calibration.id);
  assert.deepEqual(facts.cpu_baseline, { backend: "wasm-simd", ms: 9, at: { queries: 32, refs: 600, pairs: 19200 }, source: `calibration ${"c".repeat(12)}` });
  assert.match(facts.basis, /physical adapter that passed its known-answer test, this result checked on the CPU/);
  assert.equal(facts.versions.kernel, "tanimoto.best/1");
  assert.match(facts.versions.wasm_sha256, /^[0-9a-f]{64}$/);
  assert.match(facts.versions.wgsl_sha256, /^[0-9a-f]{64}$/);
  assert.deepEqual(JSON.parse(JSON.stringify(facts)), facts, "facts are plain JSON");
});

test("a calibration of another adapter or browser, or of a software or unidentified adapter, never selects the GPU", async () => {
  const gpu = fakeScienceGPU(), g = new ScienceGPU(gpu);
  const other = await calibrationFor({ navigator: { ...gpu.navigator, userAgentData: { brands: [{ brand: "Test", version: "2" }], platform: "Test", mobile: false } }, sizes: FASTER });
  const moved = await runTanimoto({ queries: Q, refs: R, gpu: g, calibration: other, navigator: gpu.navigator });
  assert.equal(moved.facts.backend, "wasm-simd");
  assert.equal(moved.facts.gpu.rejected, "the calibration was measured on another adapter or browser");
  assert.equal(moved.facts.gpu.dispatched, false, "nothing of the call was dispatched");
  assert.equal(moved.facts.gpu.verified, true, "the device passed its known-answer test …");
  assert.equal(moved.facts.evidence, "reported", "… but this call's work was never dispatched, so its rung stops below");
  assert.equal(moved.facts.accelerated, null);
  for (const kind of ["software", "unidentified"]) {
    const forged = await calibrationFor({ navigator: gpu.navigator, sizes: FASTER, kind });
    const { facts } = await runTanimoto({ queries: Q, refs: R, gpu: g, calibration: forged, navigator: gpu.navigator });
    assert.equal(facts.backend, "wasm-simd");
    assert.match(facts.reason, new RegExp(`the device check's adapter is ${kind}: not evidence of a GPU`));
  }
  const failed = await calibrationFor({ navigator: gpu.navigator, sizes: FASTER, knownAnswer: { ok: false, ms: 1, reason: "query 0" } });
  assert.match((await runTanimoto({ queries: Q, refs: R, gpu: g, calibration: failed, navigator: gpu.navigator })).facts.reason, /did not pass its known-answer test/);
  // a calibration of other kernels (before a site update changed the shaders) says nothing about these
  const before = await calibrationFor({ navigator: gpu.navigator, sizes: FASTER, versions: { wgsl_sha256: "0".repeat(64) } });
  const stale = await runTanimoto({ queries: Q, refs: R, gpu: g, calibration: before, navigator: gpu.navigator });
  assert.equal(stale.facts.backend, "wasm-simd");
  assert.equal(stale.facts.reason, "the device check measured other kernels than these (wgsl_sha256 differ): it must run again");
  assert.equal(gpu.state.adapters, 1, "only the calibration of another browser asked for the GPU");
});

test("a GPU result that fails its check on the CPU is not used: the CPU computes it, and the device is set aside", async () => {
  // the known-answer test's 4 reduce passes are right; from the call on, query 0 gets another reference with that
  // reference's own counts, which only a full search finds (query 0 is always in the sample)
  const gpu = fakeScienceGPU({ corrupt: { from: 4, kind: "arg" } }), g = new ScienceGPU(gpu);
  const calibration = await calibrationFor({ navigator: gpu.navigator, sizes: FASTER });
  const { result, facts } = await runTanimoto({ queries: Q, refs: R, gpu: g, calibration, navigator: gpu.navigator });
  assert.deepEqual(plain(result), WANT, "the CPU's answer");
  assert.equal(facts.backend, "wasm-simd");
  assert.match(facts.gpu.rejected, /its result failed the check on the CPU: query 0: the exact search gives reference/);
  assert.equal(facts.validation.ok, false);
  assert.deepEqual([facts.gpu.dispatched, facts.gpu.validated, facts.gpu.verified], [true, false, true]);
  assert.equal(facts.evidence, "dispatched");
  assert.equal(facts.accelerated, null, "the GPU computed nothing that was used");
  assert.match(facts.basis, /the GPU was tried and its result not used/);
  const again = await runTanimoto({ queries: Q, refs: R, gpu: g, calibration, navigator: gpu.navigator });
  assert.equal(again.facts.backend, "wasm-simd");
  assert.match(again.facts.gpu.error, /this WebGPU device was set aside: query 0/);
});

test("a GPU that cannot answer (its known-answer test failed, a time limit) leaves the call to the CPU and says why", async () => {
  const bad = fakeScienceGPU({ corrupt: { from: 0, kind: "arg" } });
  const calibration = await calibrationFor({ navigator: bad.navigator, sizes: FASTER });
  const { result, facts } = await runTanimoto({ queries: Q, refs: R, gpu: new ScienceGPU(bad), calibration, navigator: bad.navigator });
  assert.deepEqual(plain(result), WANT);
  assert.match(facts.gpu.error, /known-answer test failed on this device/);
  assert.equal(facts.gpu.verified, false);
  assert.equal(facts.evidence, "reported");
  const slow = fakeScienceGPU({ delayMs: 50 });
  const late = await runTanimoto({ queries: Q, refs: R, gpu: new ScienceGPU({ ...slow, timeoutMs: 10 }), calibration, navigator: slow.navigator });
  assert.equal(late.facts.backend, "wasm-simd");
  assert.match(late.facts.considered[0].reason, /the GPU could not compute it: WebGPU known-answer test could not run: WebGPU operation timed out/);
});

test("a cancel is never retried on another backend; invalid input fails as the kernels do", async () => {
  const gpu = fakeScienceGPU(), g = new ScienceGPU(gpu);
  const calibration = await calibrationFor({ navigator: gpu.navigator, sizes: FASTER });
  await assert.rejects(runTanimoto({ queries: Q, refs: R, gpu: g, calibration, navigator: gpu.navigator, isCancelled: () => true }), (e) => e.code === "cancelled");
  await assert.rejects(runTanimoto({ queries: Q, refs: R, preference: "cpu", isCancelled: () => true }), (e) => e.code === "cancelled");
  await assert.rejects(runTanimoto({ queries: Q, refs: new Uint32Array(0) }), /there are no reference fingerprints to compare with/);
  await assert.rejects(runTanimoto({ queries: new Uint8Array(4), refs: R }), /queries must be a Uint32Array/);
  // a preference it does not know is refused, never taken for "auto"
  await assert.rejects(runTanimoto({ queries: Q, refs: R, preference: "webgpu" }), /preference is "auto", "cpu", "reference", not "webgpu"/);
  assert.equal((await runTanimoto({ queries: Q, refs: R, preference: undefined })).facts.preference, "auto");
  const empty = await runTanimoto({ queries: new Uint32Array(0), refs: R, gpu: g, calibration, navigator: gpu.navigator });
  assert.deepEqual(plain(empty.result), { arg: [], inter: [], union: [] });
  assert.equal(empty.facts.considered[0].reason, "no queries: nothing to compute");
});
