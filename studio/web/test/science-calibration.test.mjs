// The device check (compute/device-check.js) and the calibration it leaves (compute/calibration.js): the device
// fingerprint, the IndexedDB store (fake-indexeddb here), what a calibration allows, and the report
// (tcmstudio.device-check/1) on a physical, a software, a missing and a failing adapter.
import assert from "node:assert/strict";
import { test } from "node:test";
import { IDBFactory } from "fake-indexeddb";
import {
  CALIBRATION_DB, CalibrationStore, DEVICE_CHECK_SCHEMA, calibrationVerdict, canonicalJson, currentCalibration, deviceFingerprint, loadCalibration, saveCalibration,
  sha256Text,
} from "../js/compute/calibration.js";
import { deviceCheck, seededInputs } from "../js/compute/device-check.js";
import { fingerprintBits, tanimotoBestJS } from "../js/compute/science-cpu.js";
import { classifyAdapter } from "../js/runtime/webgpu.js";
import { calibrationFor, fakeScienceGPU } from "./science-fakes.mjs";

const SIZES = [[8, 300], [40, 700]];
const nav = (version = "1") => ({ userAgent: `TestBrowser/${version}`, userAgentData: { brands: [{ brand: "Test", version }], platform: "Test", mobile: false } });

test("the device fingerprint: the adapter and the browser, hashed; another of either is another device", async () => {
  const physical = classifyAdapter({ vendor: "apple", architecture: "metal-3" });
  const a = await deviceFingerprint({ adapter: physical, navigator: nav() });
  assert.match(a.id, /^[0-9a-f]{64}$/);
  assert.equal(a.id, await sha256Text(canonicalJson({ adapter: a.adapter, browser: a.browser })));
  assert.deepEqual(a.adapter, { kind: "physical", vendor: "apple", architecture: "metal-3", device: "", description: "", fallback: false });
  assert.deepEqual(a.browser, { brands: [{ brand: "Test", version: "1" }], platform: "Test", mobile: false, user_agent: "TestBrowser/1" });
  assert.equal((await deviceFingerprint({ adapter: physical, navigator: nav() })).id, a.id, "the same device, the same id");
  assert.notEqual((await deviceFingerprint({ adapter: physical, navigator: nav("2") })).id, a.id, "a browser update");
  assert.notEqual((await deviceFingerprint({ adapter: classifyAdapter({ vendor: "apple", architecture: "metal-3", isFallbackAdapter: true }), navigator: nav() })).id, a.id);
  const cpuOnly = await deviceFingerprint({ adapter: null, navigator: { userAgent: "X" } });
  assert.equal(cpuOnly.adapter, null);
  assert.deepEqual(cpuOnly.browser, { brands: null, platform: null, mobile: null, user_agent: "X" });
});

test("the calibration store: one report per device fingerprint, in this origin's IndexedDB", async () => {
  const indexedDB = new IDBFactory();
  const one = await calibrationFor({ navigator: nav(), sizes: [{ queries: 1, refs: 1, accelerated: true, gpu_ms: 1, cpu_ms: 2 }] });
  const key = await saveCalibration(one, { indexedDB });
  assert.equal(key, one.device.fingerprint.id);
  assert.deepEqual(await loadCalibration(key, { indexedDB }), one);
  assert.deepEqual(await loadCalibration(one.device.fingerprint, { indexedDB }), one, "by the fingerprint itself too");
  const newer = { ...one, id: "d".repeat(64), measured_at: "2026-10-20T00:00:00.000Z" };
  await saveCalibration(newer, { indexedDB });
  const other = await calibrationFor({ navigator: nav("2"), id: "e".repeat(64) });
  await saveCalibration(other, { indexedDB });
  const store = await CalibrationStore.open({ indexedDB });
  assert.deepEqual((await store.all()).map((r) => r.id), ["d".repeat(64), "e".repeat(64)], "the newer replaced the older; newest first");
  await assert.rejects(store.put({ schema: "x" }), /a calibration is a tcmstudio.device-check\/1 report/);
  await store.delete(key);
  assert.equal(await store.get(key), null);
  store.close();
  assert.equal(await loadCalibration("nope", { indexedDB }), null);
  await assert.rejects(loadCalibration(key, { indexedDB: null }), /keeps no IndexedDB/);
  assert.equal(CALIBRATION_DB, "tcmstudio-compute");
});

test("what a calibration allows: the GPU only on a physical, verified adapter measured faster at a size no larger than the call", async () => {
  const sizes = [{ queries: 10, refs: 100, accelerated: false, gpu_ms: 3, cpu_ms: 1 }, { queries: 100, refs: 1000, accelerated: true, gpu_ms: 2, cpu_ms: 8 }, { queries: 1000, refs: 1000, accelerated: null, gpu_ms: null, cpu_ms: 80, reason: "the GPU could not run at this size" }];
  const c = await calibrationFor({ navigator: nav(), sizes });
  const at = (pairs, opts) => calibrationVerdict(c, { pairs, ...opts });
  assert.match(at(999).reason, /smaller than every size the device check measured/);
  assert.equal(at(1000).use_gpu, false);
  assert.match(at(5000).reason, /no faster than the CPU kernel at 10 × 100/);
  const v = at(100_000);
  assert.equal(v.use_gpu, true);
  assert.deepEqual([v.accelerated, v.gpu_ms, v.cpu_ms, v.cpu_backend, v.calibration_id], [true, 2, 8, "wasm-simd", c.id]);
  assert.deepEqual(v.size, { queries: 100, refs: 1000, pairs: 100_000 });
  assert.equal(at(999_999).use_gpu, true, "the largest calibrated size below the call");
  assert.match(at(2_000_000).reason, /no verdict at 1,000 × 1,000 \(1,000,000 comparisons\): the GPU could not run at this size/);
  assert.equal(at(100_000, { fingerprint: c.device.fingerprint.id }).use_gpu, true);
  assert.match(at(100_000, { fingerprint: "f".repeat(64) }).reason, /another adapter or browser/);
  assert.equal(at(100_000, { versions: c.versions }).use_gpu, true, "measured with these kernels");
  assert.match(at(100_000, { versions: { ...c.versions, wasm_sha256: "0".repeat(64), kernel: "tanimoto.best/2" } }).reason,
    /measured other kernels than these \(kernel, wasm_sha256 differ\): it must run again/);
  assert.equal(calibrationVerdict({ ...c, versions: undefined }, { pairs: 100_000, versions: c.versions }).use_gpu, false, "a report that names no kernels");
  assert.match(calibrationVerdict(null, { pairs: 1 }).reason, /no device check has been run/);
  assert.match(calibrationVerdict({ schema: "other" }, { pairs: 1 }).reason, /not a tcmstudio.device-check\/1 report/);
  assert.match(calibrationVerdict({ ...c, device: { ...c.device, adapter: null } }, { pairs: 100_000 }).reason, /found no WebGPU adapter/);
  assert.match(calibrationVerdict(c, { op: "matmul", pairs: 100_000 }).reason, /did not measure matmul/);
});

test("the seeded inputs are the same everywhere and hold what they are for", () => {
  const a = seededInputs(40, 700), b = seededInputs(40, 700);
  assert.deepEqual(Array.from(a.refs), Array.from(b.refs));
  assert.notDeepEqual(Array.from(seededInputs(40, 700, 1).refs), Array.from(a.refs), "another seed, other bits");
  assert.deepEqual(fingerprintBits(a.refs, 1), [], "an empty fingerprint");
  assert.equal(fingerprintBits(a.queries, 2).length, 2048, "an all-ones one");
  assert.deepEqual(fingerprintBits(a.refs, 699), fingerprintBits(a.refs, 3), "a reference repeated far from its first copy");
  const bits = fingerprintBits(a.refs, 5).length;
  assert.ok(bits > 30 && bits < 60, `sparse like a Morgan fingerprint: ${bits} bits`);
});

test("the device check on a physical adapter: known answer, exact parity, timings, a verdict per size, kept as the calibration", async () => {
  const gpu = fakeScienceGPU({ timestamps: true });
  const indexedDB = new IDBFactory();
  const stages = [];
  const report = await deviceCheck({ navigator: gpu.navigator, sizes: SIZES, reps: 2, indexedDB, onProgress: (p) => stages.push(p.stage) });
  assert.equal(report.schema, DEVICE_CHECK_SCHEMA);
  assert.equal(report.ok, true);
  assert.equal(report.device.adapter.kind, "physical");
  assert.equal(report.device.fingerprint.id, (await deviceFingerprint({ adapter: report.device.adapter, navigator: gpu.navigator })).id);
  assert.deepEqual(report.device.limits.requested, { maxStorageBufferBindingSize: 2 ** 30, maxBufferSize: 2 ** 30 });
  assert.equal(report.device.wasm_simd.available, true);
  const k = report.kernels["tanimoto.best"];
  assert.equal(k.kernel, "tanimoto.best/1");
  assert.equal(k.known_answer.ok, true);
  assert.equal(k.sizes.length, 2);
  for (const [i, s] of k.sizes.entries()) {
    assert.deepEqual([s.queries, s.refs, s.pairs], [SIZES[i][0], SIZES[i][1], SIZES[i][0] * SIZES[i][1]]);
    assert.deepEqual(s.exact, { "wasm-simd": true, webgpu: true });
    assert.equal(s.timings["wasm-simd"].runs.length, 2);
    assert.equal(s.timings.webgpu.runs.length, 2);
    assert.ok(s.timings.webgpu.first_ms >= 0);
    assert.equal(s.timings.webgpu.kernel_ms, 2.5 * (s.timings.webgpu.bands + 1), "the GPU's own time, from its timestamps");
    assert.equal(s.cpu_baseline, "wasm-simd");
    assert.equal(typeof s.accelerated, "boolean", "a physical, verified, exact adapter gets a verdict");
    assert.match(s.reason, /the GPU took .* ms \(search and check\), WebAssembly SIMD .* ms: (not )?faster here/);
    const { queries, refs } = seededInputs(s.queries, s.refs, s.seed);
    assert.match(s.result_sha256, /^[0-9a-f]{64}$/);
    assert.equal(tanimotoBestJS(queries, refs).arg.length, s.queries);
  }
  // a double computes on this CPU in JavaScript: slower than WebAssembly, so not faster here
  assert.equal(report.verdict, "not faster than the CPU at any size");
  assert.equal(report.label, null);
  assert.match(report.versions.wasm_sha256, /^[0-9a-f]{64}$/);
  assert.match(report.versions.wgsl_sha256, /^[0-9a-f]{64}$/);
  assert.equal(report.id, await sha256Text(canonicalJson({ ...report, id: null, stored: null })));
  assert.deepEqual(report.stored, { ok: true, db: CALIBRATION_DB, key: report.device.fingerprint.id });
  const kept = await loadCalibration(report.device.fingerprint.id, { indexedDB });
  assert.equal(kept.id, report.id);
  assert.deepEqual(stages, ["gpu", "size", "size", "done"]);
  assert.equal(gpu.state.destroyed.length, gpu.state.created.length, "every buffer released");
});

test("a GPU measured faster gets accelerated: true, and the calibration then selects it", async () => {
  // a stand-in GPU that answers from memory after its first (uncounted) call: faster than any CPU kernel
  const fake = fakeScienceGPU();
  const real = new (await import("../js/compute/science-gpu.js")).ScienceGPU(fake);
  const memo = new Map();
  const fast = {
    ready: (o) => real.ready(o), get adapter() { return real.adapter; }, get knownAnswer() { return real.knownAnswer; }, get limits() { return real.limits; },
    get lastRun() { return { dispatched: true, work_ms: 0, bands: 1, max_band_ms: 0, pieces: 1, kernel_ms: null }; }, stop() { real.stop(); },
    async tanimotoBest(q, r, o) { if (!memo.has(q)) memo.set(q, await real.tanimotoBest(q, r, o)); return memo.get(q); },
  };
  const report = await deviceCheck({ navigator: fake.navigator, sizes: [[64, 2000]], reps: 2, gpu: fast, save: false });
  const s = report.kernels["tanimoto.best"].sizes[0];
  assert.equal(s.accelerated, true, s.reason);
  assert.match(s.reason, /faster here/);
  assert.equal(report.verdict, "accelerated at 1 of 1 sizes");
  assert.equal(report.stored, null, "not kept when save is false");
  assert.equal(calibrationVerdict(report, { pairs: 64 * 2000, fingerprint: report.device.fingerprint.id }).use_gpu, true);
});

test("software only: refused without allowSoftware (no physical adapter, CPU still checked); labelled with it", async () => {
  const swift = () => fakeScienceGPU({ info: { vendor: "google", architecture: "swiftshader", isFallbackAdapter: true } });
  const refused = swift();
  const report = await deviceCheck({ navigator: refused.navigator, sizes: SIZES, reps: 1, save: false });
  assert.equal(report.verdict, "no physical adapter");
  assert.match(report.verdict_reason, /the only adapter is a software renderer .*, which is refused/);
  assert.equal(report.ok, true, "not a parity failure");
  assert.equal(report.device.gpu_error.code, "software_adapter");
  assert.equal(refused.state.deviceRequests.length, 0, "no device was asked of it");
  for (const s of report.kernels["tanimoto.best"].sizes) {
    assert.deepEqual(s.exact, { "wasm-simd": true, webgpu: null });
    assert.equal(s.accelerated, null);
    assert.match(s.reason, /no physical adapter/);
  }
  const allowed = swift();
  const sw = await deviceCheck({ navigator: allowed.navigator, sizes: SIZES, reps: 1, allowSoftware: true, save: false });
  assert.equal(sw.verdict, "software adapter: not evidence");
  assert.equal(sw.label, "software, not evidence");
  assert.equal(sw.software_allowed, true);
  assert.equal(sw.kernels["tanimoto.best"].known_answer.ok, true);
  for (const s of sw.kernels["tanimoto.best"].sizes) {
    assert.equal(s.exact.webgpu, true, "the WGSL is exact on it");
    assert.equal(s.accelerated, null);
    assert.match(s.reason, /software adapter: its timings are the CPU's, not evidence of a GPU/);
  }
  assert.equal(calibrationVerdict(sw, { pairs: 1e9 }).use_gpu, false, "and its calibration never selects the GPU");
});

test("no adapter, a failed known-answer test, wrong answers and no IndexedDB: the report says so", async () => {
  const none = await deviceCheck({ navigator: { gpu: { requestAdapter: async () => null } }, sizes: [[8, 300]], reps: 1, save: false });
  assert.equal(none.verdict, "no physical adapter");
  assert.match(none.verdict_reason, /no WebGPU adapter: No WebGPU adapter available/);
  assert.equal(none.device.adapter, null);
  assert.equal(none.device.fingerprint.adapter, null);
  const noApi = await deviceCheck({ navigator: {}, sizes: [[8, 300]], reps: 1, save: false });
  assert.equal(noApi.verdict, "no physical adapter");
  const bad = fakeScienceGPU({ corrupt: { from: 0, kind: "arg" } });
  const failed = await deviceCheck({ navigator: bad.navigator, sizes: [[8, 300]], reps: 1, save: false });
  assert.equal(failed.verdict, "known-answer test failed");
  assert.equal(failed.ok, false);
  assert.match(failed.kernels["tanimoto.best"].sizes[0].reason, /failed its known-answer test/);
  // a failure is the verdict even on a software adapter that was allowed: the label stays beside it
  const swBad = fakeScienceGPU({ info: { vendor: "google", architecture: "swiftshader" }, corrupt: { from: 0, kind: "arg" } });
  const swFailed = await deviceCheck({ navigator: swBad.navigator, allowSoftware: true, sizes: [[8, 300]], reps: 1, save: false });
  assert.deepEqual([swFailed.verdict, swFailed.label, swFailed.ok], ["known-answer test failed", "software, not evidence", false]);
  assert.match(swFailed.kernels["tanimoto.best"].sizes[0].reason, /failed its known-answer test: query 0: arg/);
  const wrong = fakeScienceGPU({ corrupt: { from: 4, kind: "arg" } });
  const inexact = await deviceCheck({ navigator: wrong.navigator, sizes: [[8, 300]], reps: 1, save: false });
  assert.equal(inexact.verdict, "GPU answers not exact");
  assert.equal(inexact.ok, false);
  assert.equal(inexact.kernels["tanimoto.best"].sizes[0].exact.webgpu, false);
  assert.equal(inexact.kernels["tanimoto.best"].sizes[0].accelerated, null);
  // a GPU whose first call is right and a timed one is not: every answer counts, not only the first
  const later = fakeScienceGPU({ corrupt: { from: 5, kind: "arg" } });
  const lateWrong = await deviceCheck({ navigator: later.navigator, sizes: [[8, 300]], reps: 2, save: false });
  assert.deepEqual([lateWrong.verdict, lateWrong.ok, lateWrong.kernels["tanimoto.best"].sizes[0].exact.webgpu], ["GPU answers not exact", false, false]);
  const unkept = await deviceCheck({ navigator: {}, sizes: [[8, 300]], reps: 1, indexedDB: null });
  assert.equal(unkept.stored.ok, false);
  assert.match(unkept.stored.error, /keeps no IndexedDB/);
});

test("this device's calibration: the adapter offered now, its fingerprint, the report kept for it; never a throw", async () => {
  const gpu = fakeScienceGPU();
  const indexedDB = new IDBFactory();
  const before = await currentCalibration({ navigator: gpu.navigator, indexedDB });
  assert.equal(before.adapter.kind, "physical");
  assert.equal(before.calibration, null);
  assert.equal(before.reason, "no device check has been run on this device");
  assert.equal(gpu.state.deviceRequests.length, 0, "no device is created to find it");
  const report = await deviceCheck({ navigator: gpu.navigator, sizes: [[8, 300]], reps: 1, indexedDB });
  const now = await currentCalibration({ navigator: gpu.navigator, indexedDB });
  assert.equal(now.fingerprint.id, report.device.fingerprint.id);
  assert.equal(now.calibration.id, report.id);
  assert.equal(now.reason, null);
  const none = await currentCalibration({ navigator: {}, indexedDB: null });
  assert.deepEqual([none.adapter, none.calibration, none.reason], [null, null, "this browser has no WebGPU API"]);
  const hung = await currentCalibration({ navigator: { gpu: { requestAdapter: () => new Promise(() => {}) } }, indexedDB, timeoutMs: 10 });
  assert.equal(hung.reason, "requestAdapter timed out");
});
