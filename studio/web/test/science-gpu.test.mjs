// The WebGPU Tanimoto kernel (compute/science-gpu.js) on a device double that computes what the WGSL computes
// (science-fakes.mjs): the score key's exactness up to 2048 bits, the known answers against the CPU kernels and a
// brute force, the adapter rules (software refused, limits asked of the adapter up to a cap), pieces and bands within
// the device's limits, timestamps, time limits, cancellation, device loss, and every buffer released.
import assert from "node:assert/strict";
import { test } from "node:test";
import { FP_WORDS, tanimotoBestJS, tanimotoBestWasm, wordsFromBits } from "../js/compute/science-cpu.js";
import { KNOWN_ANSWER, SOFTWARE_REFUSED, ScienceGPU, TANIMOTO_PARTIAL, TANIMOTO_REDUCE, knownAnswerInput, tanimotoKey } from "../js/compute/science-gpu.js";
import { fakeScienceGPU } from "./science-fakes.mjs";

function random(seed) {
  let s = seed >>> 0;
  return () => (s = (Math.imul(s, 1664525) + 1013904223) >>> 0) / 2 ** 32;
}
function fingerprints(r, n, density = 0.025, bits = 2048) {
  return wordsFromBits(Array.from({ length: n }, () => { const s = []; for (let b = 0; b < bits; b++) if (r() < density) s.push(b); return s; }), { words: bits / 32 });
}
const plain = (x) => ({ arg: Array.from(x.arg), inter: Array.from(x.inter), union: Array.from(x.union) });
const KAT = knownAnswerInput();

/** Dispatches of the known-answer test on a fresh device: it runs before every device's first call. */
async function katDispatches() {
  const gpu = fakeScienceGPU(), g = new ScienceGPU(gpu);
  await g.ready();
  return gpu.state.dispatches.length;
}

// ------------------------------------------------------------------------------------------------- the key

test("the score key orders every ratio c/u with u ≤ 2048 exactly, and would not beyond", () => {
  const exact = (c, u) => { const num = c * 4194304; return (num - (num % u)) / u; };
  const fr = [];
  for (let u = 1; u <= 2048; u++) for (let c = 0; c <= u; c++) fr.push([c, u]);
  let mismatches = 0;
  for (const [c, u] of fr) if (tanimotoKey(c, u) !== exact(c, u)) mismatches++;
  assert.equal(mismatches, 0, "the long division is floor(c · 2^22 / u)");
  fr.sort((a, b) => a[0] * b[1] - b[0] * a[1]);
  let violations = 0, distinct = 1;
  for (let i = 1; i < fr.length; i++) {
    const [c1, u1] = fr[i - 1], [c2, u2] = fr[i];
    const same = c1 * u2 === c2 * u1, k1 = tanimotoKey(c1, u1), k2 = tanimotoKey(c2, u2);
    if (same ? k1 !== k2 : !(k2 > k1)) violations++;
    if (!same) distinct++;
  }
  assert.equal(fr.length, 2_100_224);
  assert.equal(distinct, 1_275_587);
  assert.equal(violations, 0, "different ratios, different keys in the same order; equal ratios, equal keys");
  assert.equal(tanimotoKey(0, 0), 0, "an empty union scores 0");
  // beyond 2048 bits two different ratios share a key: why longer fingerprints are refused
  let collision = null;
  for (let u = 4000; u <= 4096 && !collision; u++) for (let c = (u >> 1) - 4; c <= (u >> 1) + 4 && !collision; c++) {
    for (let v = u - 3; v < u && !collision; v++) for (let d = (v >> 1) - 4; d <= (v >> 1) + 4; d++) {
      if (c * v !== d * u && tanimotoKey(c, u) === tanimotoKey(d, v)) { collision = [c, u, d, v]; break; }
    }
  }
  assert.ok(collision, "a collision exists at 4096 bits");
});

test("the known answers are what the CPU kernels compute for their input, with what was planted in it", () => {
  const want = { arg: KNOWN_ANSWER.arg, inter: KNOWN_ANSWER.inter, union: KNOWN_ANSWER.union };
  assert.deepEqual(plain(tanimotoBestJS(KAT.queries, KAT.refs)), want);
  assert.deepEqual(plain(tanimotoBestWasm(KAT.queries, KAT.refs)), want);
  assert.deepEqual([want.arg[0], want.inter[0]], [0, 0], "the empty query");
  assert.deepEqual([want.arg[1], want.inter[1], want.union[1]], [500, 2048, 2048], "the all-ones query");
  assert.equal(want.arg[2], 10, "three equal maxima (10, 11, 650): the smallest index");
  assert.deepEqual([want.arg[3], want.union[3]], [0, 48], "the empty reference's copy");
  assert.deepEqual([want.arg[21], want.arg[22]], [256, 255], "either side of a workgroup's edge");
  assert.deepEqual([want.arg[38], want.inter[38], want.union[38]], [260, 25, 50]);
  assert.deepEqual([want.arg[39], want.inter[39], want.union[39]], [100, 20, 40]);
  assert.ok(KAT.refs.length / FP_WORDS > KNOWN_ANSWER.plan.pieceRefs && KAT.queries.length / FP_WORDS > KNOWN_ANSWER.plan.pieceQueries, "the test runs in pieces");
  assert.match(TANIMOTO_PARTIAL, /@compute @workgroup_size\(256\)\nfn partial\(/);
  assert.match(TANIMOTO_REDUCE, /@compute @workgroup_size\(64\)\nfn reduce\(/);
});

// ------------------------------------------------------------------------------------------------- the adapter

test("a software renderer is refused unless allowSoftware; no API, no adapter and an unresponsive one fail clearly", async () => {
  const q = fingerprints(random(1), 3), r = fingerprints(random(2), 10);
  for (const info of [{ vendor: "google", architecture: "swiftshader" }, { vendor: "mesa", device: "llvmpipe (LLVM 17.0.6, 256 bits)" }, { vendor: "nvidia", isFallbackAdapter: true }]) {
    const gpu = fakeScienceGPU({ info }), g = new ScienceGPU(gpu);
    await assert.rejects(g.tanimotoBest(q, r), (e) => e.code === "software_adapter" && e.message === SOFTWARE_REFUSED);
    assert.equal(g.adapter.kind, "software");
    assert.equal(gpu.state.deviceRequests.length, 0, "no device was asked of it");
  }
  const allowed = fakeScienceGPU({ info: { vendor: "google", architecture: "swiftshader" } });
  const g = new ScienceGPU({ ...allowed, allowSoftware: true });
  assert.deepEqual(plain(await g.tanimotoBest(q, r)), plain(tanimotoBestJS(q, r)), "the device check's software mode computes on it");
  assert.equal(g.adapter.kind, "software");
  await assert.rejects(new ScienceGPU({ navigator: {} }).tanimotoBest(q, r), (e) => e.code === "no_api");
  await assert.rejects(new ScienceGPU({ navigator: { gpu: { requestAdapter: async () => null } } }).tanimotoBest(q, r), (e) => e.code === "no_adapter");
  await assert.rejects(new ScienceGPU({ navigator: { gpu: { requestAdapter: () => new Promise(() => {}) } }, timeoutMs: 20 }).tanimotoBest(q, r), (e) => e.code === "timeout");
});

test("the device asks for the adapter's storage and buffer limits, up to a cap", async () => {
  const gpu = fakeScienceGPU({ adapterLimits: { maxStorageBufferBindingSize: 2 ** 31, maxBufferSize: 2 ** 32 } });
  const g = new ScienceGPU(gpu);
  await g.ready();
  assert.deepEqual(gpu.state.deviceRequests, [{ requiredLimits: { maxStorageBufferBindingSize: 2 ** 30, maxBufferSize: 2 ** 30 } }], "the cap: 1 GiB");
  assert.deepEqual(g.limits.granted, { maxStorageBufferBindingSize: 2 ** 30, maxBufferSize: 2 ** 30, maxComputeWorkgroupsPerDimension: 65535 });
  const small = fakeScienceGPU({ adapterLimits: { maxStorageBufferBindingSize: 65536, maxBufferSize: 65536 } });
  await new ScienceGPU(small).ready();
  assert.deepEqual(small.state.deviceRequests[0].requiredLimits, { maxStorageBufferBindingSize: 65536, maxBufferSize: 65536 }, "never more than the adapter has");
  const ts = fakeScienceGPU({ timestamps: true });
  await new ScienceGPU({ ...ts, timestamps: true }).ready();
  assert.deepEqual(ts.state.deviceRequests[0].requiredFeatures, ["timestamp-query"]);
});

// ------------------------------------------------------------------------------------------------- the runtime

test("a device passes its known-answer test once; then calls are exact, and every buffer is released", async () => {
  const r = random(20261019);
  const q = fingerprints(r, 70), refs = fingerprints(r, 1500);
  const gpu = fakeScienceGPU(), g = new ScienceGPU(gpu);
  const out = await g.tanimotoBest(q, refs);
  assert.deepEqual(plain(out), plain(tanimotoBestJS(q, refs)));
  assert.equal(g.knownAnswer.ok, true);
  assert.equal(g.adapter.kind, "physical");
  const kat = await katDispatches();
  assert.ok(kat >= 8, "the known-answer test runs in pieces and bands");
  // its bands start inside a piece in both directions, so the shader's offsets (c0, qb0) are what it tests too
  const parts = gpu.state.dispatches.slice(0, kat).filter((d) => d.entry === "partial");
  assert.ok(parts.some((d) => d.c0 > 0) && parts.some((d) => d.qb0 > 0), `bands at chunk and query-block offsets: ${JSON.stringify(parts.map((d) => [d.c0, d.qb0]))}`);
  const run = g.lastRun;
  assert.equal(run.dispatched, true);
  assert.equal(run.cold, true, "this call created the device and ran the known-answer test");
  assert.equal(run.pairs, 70 * 1500);
  assert.equal(run.pieces, 1);
  assert.equal(run.bands + 1, gpu.state.dispatches.length - kat, "its bands, then one reduce pass");
  for (const k of ["setup_ms", "upload_ms", "dispatch_ms", "readback_ms", "work_ms"]) assert.ok(run[k] >= 0, k);
  assert.equal(run.kernel_ms, null, "no timestamps unless asked for");
  assert.equal(gpu.state.destroyed.length, gpu.state.created.length, "every buffer of every search released");
  const before = gpu.state.dispatches.length;
  assert.deepEqual(plain(await g.tanimotoBest(q, refs)), plain(out));
  assert.equal(g.lastRun.cold, false);
  assert.equal(gpu.state.adapters, 1, "the device is reused");
  assert.equal(gpu.state.dispatches.length - before, g.lastRun.bands + 1, "no known-answer test again on the same device");
  g.stop();
  assert.equal(g.knownAnswer, null, "a stopped device's test does not carry over");
  // no queries: nothing to compute, no device asked for
  const idle = fakeScienceGPU();
  assert.deepEqual(plain(await new ScienceGPU(idle).tanimotoBest(new Uint32Array(0), refs)), { arg: [], inter: [], union: [] });
  assert.equal(idle.state.adapters, 0);
});

test("pieces fit the device's limits, and merge across them as one search (ties keep the smallest index)", async () => {
  const r = random(5);
  const q = fingerprints(r, 50), refs = fingerprints(r, 900);
  refs.set(refs.subarray(0, 64), 899 * 64);       // a tie between the first piece and the last
  refs.set(refs.subarray(64, 128), 450 * 64);
  q.set(refs.subarray(0, 64), 0);
  q.set(refs.subarray(64, 128), 64);
  // 64 KiB bindings: 256 references or queries per piece, and partial results for 256 chunks
  const gpu = fakeScienceGPU({ adapterLimits: { maxStorageBufferBindingSize: 65536, maxBufferSize: 65536 } });
  const g = new ScienceGPU(gpu);
  const out = await g.tanimotoBest(q, refs);
  assert.deepEqual(plain(out), plain(tanimotoBestJS(q, refs)));
  assert.deepEqual([out.arg[0], out.arg[1]], [0, 1], "the earlier piece keeps a tie");
  assert.equal(g.lastRun.pieces, 4, "900 references in pieces of 256");
  assert.ok(gpu.state.created.every((b) => b.size <= 65536), "no buffer beyond the limit");
  // pieces of queries too: 600 queries, 16 KiB of partial results per query piece at most
  const many = fingerprints(r, 600);
  assert.deepEqual(plain(await g.tanimotoBest(many, refs)), plain(tanimotoBestJS(many, refs)));
  assert.equal(g.lastRun.pieces, 3 * 4, "three pieces of queries, each against four of references");
  const tiny = new ScienceGPU(fakeScienceGPU({ adapterLimits: { maxStorageBufferBindingSize: 128, maxBufferSize: 128 } }));
  await assert.rejects(tiny.tanimotoBest(q, refs), /known-answer test could not run: Workload exceeds this GPU's buffer\/dispatch limits/);
});

test("bands: no dispatch beyond maxComputeWorkgroupsPerDimension, the first one small, the next sized from the measured rate", async () => {
  const r = random(9);
  const q = fingerprints(r, 100), refs = fingerprints(r, 3000);
  const narrow = fakeScienceGPU({ adapterLimits: { maxComputeWorkgroupsPerDimension: 3 } });
  const g = new ScienceGPU(narrow);
  assert.deepEqual(plain(await g.tanimotoBest(q, refs)), plain(tanimotoBestJS(q, refs)));
  assert.ok(narrow.state.dispatches.every((d) => d.x <= 3 && d.y <= 3));
  // a device that answers at once: bands grow (at most eightfold each time) from the first
  const fast = fakeScienceGPU(), f = new ScienceGPU({ ...fast, bandPairs: 4096 * 2 });
  await f.ready();
  const from = fast.state.dispatches.length;
  assert.deepEqual(plain(await f.tanimotoBest(q, refs)), plain(tanimotoBestJS(q, refs)));
  const cells = fast.state.dispatches.slice(from).filter((d) => d.entry === "partial").map((d) => d.x * d.y);
  assert.equal(cells[0], 2, "the first band is the one asked for");
  assert.ok(cells[1] > cells[0] && cells[1] <= 8 * 2, `then larger: ${cells.slice(0, 4)}`);
  // a slow device (every wait 20 ms) with a 10 ms target: bands shrink to one workgroup
  const slow = fakeScienceGPU(), s = new ScienceGPU({ ...slow, bandPairs: 4096 * 8, targetMs: 10 });
  await s.ready();
  slow.state.delayMs = 20;
  const at = slow.state.dispatches.length;
  const few = fingerprints(r, 16);
  assert.deepEqual(plain(await s.tanimotoBest(few, refs)), plain(tanimotoBestJS(few, refs)));
  const sizes = slow.state.dispatches.slice(at).filter((d) => d.entry === "partial").map((d) => d.x * d.y);
  assert.equal(sizes[0], 8);
  assert.ok(sizes[1] < sizes[0], `then smaller: ${sizes}`);
  assert.equal(sizes.at(-1), 1);
  assert.ok(s.lastRun.max_band_ms >= 20);
  assert.equal(s.lastRun.bands, sizes.length);
});

test("timestamps, when asked for and offered, are the GPU's own time over every pass", async () => {
  const r = random(3);
  const q = fingerprints(r, 20), refs = fingerprints(r, 600);
  const gpu = fakeScienceGPU({ timestamps: true }), g = new ScienceGPU({ ...gpu, timestamps: true });
  await g.tanimotoBest(q, refs);
  assert.equal(g.lastRun.kernel_ms, 2.5 * (g.lastRun.bands + 1), "2.5 ms per pass, the reduce included");
  const plainGpu = fakeScienceGPU(), p = new ScienceGPU({ ...plainGpu, timestamps: true });
  await p.tanimotoBest(q, refs);
  assert.equal(p.lastRun.kernel_ms, null);
  assert.match(p.lastRun.kernel_note, /timestamp/);
});

test("narrower fingerprints are zero-padded; longer than 2048 bits are refused", async () => {
  const r = random(4);
  const q = fingerprints(r, 9, 0.1, 96), refs = fingerprints(r, 300, 0.1, 96);
  const g = new ScienceGPU(fakeScienceGPU());
  assert.deepEqual(plain(await g.tanimotoBest(q, refs, { words: 3 })), plain(tanimotoBestJS(q, refs, { words: 3 })));
  await assert.rejects(g.tanimotoBest(new Uint32Array(65), new Uint32Array(65), { words: 65 }), /exact only up to 2048 bits \(64 words\), not 2080/);
  await assert.rejects(g.tanimotoBest(q, new Uint32Array(0), { words: 3 }), /there are no reference fingerprints to compare with/);
});

// ------------------------------------------------------------------------------------------------- failures

test("a device that fails its known-answer test is never used, and says so", async () => {
  const q = fingerprints(random(6), 5), refs = fingerprints(random(7), 300);
  const gpu = fakeScienceGPU({ corrupt: { from: 0, kind: "arg" } }), g = new ScienceGPU(gpu);
  await assert.rejects(g.tanimotoBest(q, refs), (e) => e.code === "known_answer_failed" && /known-answer test failed on this device: query 0: arg/.test(e.message));
  assert.equal(g.knownAnswer.ok, false);
  assert.equal(g.lastRun.dispatched, false, "the call's own search was never submitted");
  const n = gpu.state.dispatches.length;
  await assert.rejects(g.tanimotoBest(q, refs), (e) => e.code === "known_answer_failed");
  assert.equal(gpu.state.dispatches.length, n, "and the test is not run again on the same device");
});

test("an answer no reference can give is a wrong answer; a consistent wrong one is the scheduler's to find", async () => {
  const q = fingerprints(random(6), 5), refs = fingerprints(random(7), 300);
  const counts = new ScienceGPU(fakeScienceGPU({ corrupt: { from: 4, kind: "counts" } }));
  await assert.rejects(counts.tanimotoBest(q, refs), (e) => e.code === "wrong_answer" && /is not one a reference can give/.test(e.message));
  const arg = new ScienceGPU(fakeScienceGPU({ corrupt: { from: 4, kind: "arg" } }));
  const out = await arg.tanimotoBest(q, refs);
  assert.notDeepEqual(plain(out), plain(tanimotoBestJS(q, refs)), "another reference, with its own counts: passes the device-side check");
  arg.setAsideFor("query 0: the exact search gives another reference");
  await assert.rejects(arg.tanimotoBest(q, refs), (e) => e.code === "set_aside" && /set aside: query 0/.test(e.message));
  // for the session: a new device after a loss (it passes its known-answer test again) is not used either
  const lossy = fakeScienceGPU(), l = new ScienceGPU(lossy);
  await l.ready();
  l.setAsideFor("query 7: another reference");
  lossy.device.destroy();
  await new Promise((resolve) => setTimeout(resolve, 0));
  await assert.rejects(l.tanimotoBest(q, refs), (e) => e.code === "set_aside");
  assert.equal(lossy.state.deviceRequests.length, 2, "a new device, tested");
  assert.equal(l.knownAnswer.ok, true);
  l.stop();
  assert.deepEqual(plain(await l.tanimotoBest(q, refs)), plain(tanimotoBestJS(q, refs)), "stop() ends the session's verdict");
});

test("a known-answer test that could not run keeps no verdict: the next call tests again", async () => {
  const q = fingerprints(random(8), 5), refs = fingerprints(random(9), 300);
  const gpu = fakeScienceGPU({ delayMs: 60 }), g = new ScienceGPU({ ...gpu, timeoutMs: 20 });
  await assert.rejects(g.tanimotoBest(q, refs), /known-answer test could not run: WebGPU operation timed out/);
  assert.equal(g.knownAnswer, null, "a timeout is not a wrong answer");
  gpu.state.delayMs = 0;
  assert.deepEqual(plain(await g.tanimotoBest(q, refs)), plain(tanimotoBestJS(q, refs)));
  assert.equal(g.knownAnswer.ok, true);
  assert.equal(g.lastRun.cold, true);
  assert.equal(gpu.state.adapters, 1, "the same device");
  assert.equal(gpu.state.destroyed.length, gpu.state.created.length);
});

test("a cancel stops the search between bands and releases its buffers; a lost device and a failed shader fail clearly", async () => {
  const r = random(10);
  const q = fingerprints(r, 64), refs = fingerprints(r, 4000);
  const gpu = fakeScienceGPU(), g = new ScienceGPU({ ...gpu, bandPairs: 4096 });
  await g.ready();
  gpu.state.delayMs = 5;
  let stop = false;
  const timer = setTimeout(() => { stop = true; }, 30);
  await assert.rejects(g.tanimotoBest(q, refs, { isCancelled: () => stop }), (e) => e.code === "cancelled");
  clearTimeout(timer);
  assert.ok(g.lastRun.bands >= 1 && g.lastRun.bands < 64, "stopped part of the way");
  assert.equal(gpu.state.destroyed.length, gpu.state.created.length, "its buffers released");
  assert.equal(g.knownAnswer.ok, true, "a cancelled call leaves the device's verdict standing");
  const lost = new ScienceGPU(fakeScienceGPU({ lost: true }));
  await assert.rejects(lost.tanimotoBest(q, refs), /device lost|lost/);
  const failed = new ScienceGPU(fakeScienceGPU({ fail: true }));
  await assert.rejects(failed.tanimotoBest(q, refs), /shader validation failed/);
});

test("an error raised by a search's commands is an error, not a wrong answer: no verdict on the device", async () => {
  const q = fingerprints(random(12), 5), refs = fingerprints(random(13), 300);
  // compile pops 1; the known-answer test's first piece pops 2 and 3: an error there means the test could not run
  const gpu = fakeScienceGPU({ errorFromPop: 2 }), g = new ScienceGPU(gpu);
  await assert.rejects(g.tanimotoBest(q, refs), (e) => e.code === "validation" && /known-answer test could not run: Binding size is larger/.test(e.message));
  assert.equal(g.knownAnswer, null, "a validation error says nothing about what the device computes");
  assert.equal(gpu.state.destroyed.length, gpu.state.created.length);
  // after the test passed: the call's own search fails with that error
  const late = fakeScienceGPU(), l = new ScienceGPU(late);
  await l.ready();
  late.state.errorFromPop = late.state.pops + 1;
  await assert.rejects(l.tanimotoBest(q, refs), (e) => e.code === "validation");
  assert.equal(l.knownAnswer.ok, true);
});

test("calls on one device run one after another, in the order they came: two searches at once never interleave, and each says what it did", async () => {
  const gpu = fakeScienceGPU({ delayMs: 3 });
  const g = new ScienceGPU(gpu);
  const r = random(77);
  const q1 = fingerprints(r, 40), r1 = fingerprints(r, 600), q2 = fingerprints(r, 20), r2 = fingerprints(r, 300);
  // both asked for before either has its device: the first sets the device up (and runs its known-answer test), the
  // second waits for it
  const [a, b] = await Promise.all([g.tanimotoBest(q1, r1), g.tanimotoBest(q2, r2)]);
  assert.deepEqual(plain(a), plain(tanimotoBestJS(q1, r1)));
  assert.deepEqual(plain(b), plain(tanimotoBestJS(q2, r2)));
  assert.deepEqual([a.run.pairs, a.run.cold, b.run.pairs, b.run.cold], [40 * 600, true, 20 * 300, false], "each result carries its own call's marks");
  assert.equal(Object.keys(a).includes("run"), false, "not one of the result's own fields");
  const order = gpu.state.dispatches.map((d) => d.Q).filter((q) => q === 40 || q === 20);
  assert.deepEqual(order, [...order].sort((x, y) => y - x), `the second search's passes came after the first's: ${order.join(",")}`);
  assert.equal(g.lastRun.pairs, 20 * 300);
  // a failed call does not hold up the next, and says how far it got
  const bad = g.tanimotoBest(q1, new Uint32Array(0));
  const next = g.tanimotoBest(q2, r2);
  await assert.rejects(bad, /no reference fingerprints/);
  assert.deepEqual(plain(await next), plain(b));
});
