// The page-side accelerator of np-null-mt/1 (docs/V2.md §13.4) against CPython's own numbers (fixtures.json, made by
// make_fixtures.py): the MT19937 port and random.sample, the single-thread kernel, the js-workers engine (its real
// worker script, under worker_threads), the WGSL kernel's CPU emulation, runWebGPU on a WebGPU stand-in, the host
// (engine choice, input checks, the GPU cross-check and its fallback, cancellation, the digest) and the benchmark.
import "../fixtures/setup.mjs";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { after, describe, test } from "node:test";
import { runBenchmark, worldInput } from "../../js/accel/bench.js";
import { batchArrays, batchSize, gpuLayout, openGpu, runWebGPU, WGSL } from "../../js/accel/gpu.js";
import { chooseEngine, createAccelHost, GPU_MIN_PATHWAYS, jsWorkerCount, kernelDigest } from "../../js/accel/host.js";
import { fromInt, fromString, MT19937, sample, setsize, strSeedKey } from "../../js/accel/mt19937.js";
import { checkInput, countRange, KernelInputError, nullCounts, prepare, subPlan } from "../../js/accel/npnull.js";
import { chunkSize, runJsWorkers } from "../../js/accel/workers.js";
import { fakeGpu } from "./fake-webgpu.mjs";
import { nodeWorker, started } from "./node-worker.mjs";
import { dispatch } from "./wgsl-emu.mjs";

const FIX = JSON.parse(readFileSync(new URL("./fixtures.json", import.meta.url), "utf8"));
const WORLD = JSON.parse(readFileSync(new URL("../../js/accel/world-1399.json", import.meta.url), "utf8"));
const worldNamed = (name) => FIX.worlds.find((w) => w.name === name);

after(() => {
  for (const w of started) w.terminate();
});

describe("CPython's random, ported", () => {
  test("Random(str): the first outputs, _randbelow, sample() and random() equal CPython's", async () => {
    assert.ok(FIX.mt.str.length >= 5);
    for (const v of FIX.mt.str) {
      let g = await fromString(v.seed);
      assert.deepEqual(Array.from({ length: v.first.length }, () => g.next()), v.first, `first outputs of ${JSON.stringify(v.seed)}`);
      g = await fromString(v.seed);
      assert.deepEqual(v.below_n.map((n) => g.randbelow(n)), v.below, `_randbelow for ${JSON.stringify(v.seed)}`);
      g = await fromString(v.seed);
      for (const [n, k, want] of v.samples) {
        const population = Array.from({ length: n }, (_, i) => i);
        assert.deepEqual(sample(g, population, k), want, `sample(range(${n}), ${k}) for ${JSON.stringify(v.seed)}`);
      }
      g = await fromString(v.seed);
      assert.deepEqual(v.random.map(() => g.random()), v.random);
    }
  });

  test("the documented first outputs of Random('20260923:reactome:R-HSA-00001')", async () => {
    const g = await fromString("20260923:reactome:R-HSA-00001");
    assert.deepEqual([g.next(), g.next(), g.next()], [1535181293, 2556489721, 1295649565]);
  });

  test("Random(int) seeds like CPython (0, 1, a 64-bit value)", () => {
    for (const v of FIX.mt.int) {
      const g = fromInt(v.seed);
      assert.deepEqual(v.first.map(() => g.next()), v.first, `Random(${v.seed})`);
    }
  });

  test("a leading NUL byte is dropped from the seed integer, as int.from_bytes does", async () => {
    const key = await strSeedKey("\u0000\u0000lead");
    const plain = await strSeedKey("lead");
    assert.notDeepEqual(Array.from(key), Array.from(plain), "the SHA-512 part differs");
    assert.ok(key.length >= 1 && key[key.length - 1] !== 0, "no zero word above the top bit");
  });

  test("setsize: the exact integer exponent equals CPython's float log for every k up to 100,000", () => {
    assert.equal(setsize(0), 21);
    assert.equal(setsize(5), 21);
    assert.equal(setsize(6), 85);
    assert.equal(setsize(10), 85);
    assert.equal(setsize(22), 277);
    assert.equal(setsize(24), 277);
    for (let k = 6; k <= 100_000; k++) assert.equal(setsize(k), 21 + 4 ** Math.ceil(Math.log(k * 3) / Math.log(4)), `k=${k}`);
  });

  test("sample() refuses k outside 0..n, as CPython raises", () => {
    const g = new MT19937();
    g.initGenrand(5);
    assert.throws(() => sample(g, [1, 2], 3), RangeError);
    assert.throws(() => sample(g, [1, 2], -1), RangeError);
    assert.deepEqual(sample(g, [1, 2], 0), []);
  });
});

describe("np-null-mt/1 on one thread", () => {
  test("every fixture world: at_least equals CPython's (step 4 of network_pharmacology)", async () => {
    assert.ok(FIX.worlds.length >= 12);
    for (const w of FIX.worlds) {
      assert.deepEqual(await nullCounts(w.input), w.at_least, w.name);
    }
  });

  test("the pipeline's own worlds: equal to run_network_pharmacology's empirical_p", async () => {
    const piped = FIX.worlds.filter((w) => w.at_least_from === "run_network_pharmacology");
    assert.ok(piped.length >= 3);
    for (const w of piped) {
      const counts = await nullCounts(w.input);
      assert.deepEqual(counts, w.at_least, w.name);
      // empirical p = (1 + at_least) / (1 + permutations): the same IEEE double in JS and Python
      assert.ok(counts.every((c) => Number.isFinite((1 + c) / (1 + w.input.permutations))));
    }
  });

  test("the fixtures cover both sample() branches, their boundaries and the edge cases", () => {
    const branches = (name) => Array.from(prepare(worldNamed(name).input).binPool);
    assert.deepEqual(branches("pool-small-k"), [1, 1, 1, 1], "n <= 21 with k <= 5: pool");
    assert.deepEqual(branches("set-small-k"), [0, 0, 0], "n > 21 with k <= 5: set");
    assert.deepEqual(branches("k-gt-5-boundaries"), [1, 0, 1, 0], "n == setsize: pool; setsize + 1: set");
    const w = worldNamed("observed-edges");
    const byId = Object.fromEntries(w.input.pathways.map((p, i) => [p.id, w.at_least[i]]));
    assert.equal(byId["reactome:R-HSA-OBS0"], w.input.permutations, "observed 0: every permutation counts");
    assert.equal(byId["reactome:R-HSA-HIGH"], 0, "an unreachable observed count: none");
    assert.equal(byId["reactome:R-HSA-EMPTY1"], 0);
    assert.deepEqual(worldNamed("zero-permutations").at_least, [0, 0, 0]);
    const synthetic = prepare(worldNamed("synthetic-mini").input);
    assert.ok(synthetic.binPool.includes(1) && synthetic.binPool.includes(0), "the synthetic world uses both branches");
  });

  test("the 1,399-pathway world: the first 48 pathways equal CPython's counts", async () => {
    const { input, cpython } = worldInput(WORLD, 48);
    assert.equal(WORLD.ids.length, 1399);
    assert.deepEqual(await nullCounts(input), cpython);
  });

  test("countRange and subPlan compute the same pathways independently of the rest", async () => {
    const plan = prepare(worldNamed("synthetic-mini").input);
    const all = await countRange(plan);
    const picked = [5, 0, plan.ids.length - 1, 77];
    assert.deepEqual(await countRange(subPlan(plan, picked)), picked.map((p) => all[p]));
    assert.deepEqual(await countRange(plan, 10, 20), all.slice(10, 20));
  });
});

describe("checkInput refuses malformed input, naming the field", () => {
  const good = () => structuredClone(worldNamed("pool-small-k").input);
  const cases = [
    ["not an object", () => null, /not an object/],
    ["seed", (i) => ({ ...i, seed: "" }), /seed/],
    ["seed float", (i) => ({ ...i, seed: 1.5 }), /seed/],
    ["permutations", (i) => ({ ...i, permutations: -1 }), /permutations/],
    ["permutations float", (i) => ({ ...i, permutations: 10.5 }), /permutations/],
    ["pools", (i) => ({ ...i, pools: "x" }), /pools/],
    ["pool entry", (i) => ({ ...i, pools: [[0, "a"], ...i.pools.slice(1)] }), /pools\[0\]/],
    ["shared protein", (i) => ({ ...i, pools: [[0, 1, 2], [2, 3]], wanted: [[0, 1], [1, 1]] }), /protein 2 is in pools\[0\] and pools\[1\]/],
    ["repeated protein", (i) => ({ ...i, pools: [[0, 0, 1]], wanted: [[0, 1]] }), /twice/],
    ["wanted unsorted", (i) => ({ ...i, wanted: [[1, 1], [0, 1]] }), /sorted/],
    ["wanted repeated", (i) => ({ ...i, wanted: [[0, 1], [0, 1]] }), /sorted/],
    ["wanted k > n", (i) => ({ ...i, wanted: [[0, 99]] }), /asks for 99/],
    ["wanted pool missing", (i) => ({ ...i, wanted: [[9, 1]] }), /pool 9/],
    ["wanted pair", (i) => ({ ...i, wanted: [[0]] }), /wanted\[0\]/],
    ["pathways", (i) => ({ ...i, pathways: {} }), /pathways/],
    ["pathway id", (i) => ({ ...i, pathways: [{ members: [], observed: 0 }] }), /pathways\[0\]\.id/],
    ["lone surrogate", (i) => ({ ...i, pathways: [{ id: "a\uD800", members: [], observed: 0 }] }), /pathways\[0\]\.id/],
    ["members", (i) => ({ ...i, pathways: [{ id: "x", members: [-1], observed: 0 }] }), /members/],
    ["observed", (i) => ({ ...i, pathways: [{ id: "x", members: [], observed: -2 }] }), /observed/],
  ];
  for (const [name, mutate, re] of cases) {
    test(name, () => {
      assert.throws(() => checkInput(mutate(good())), (err) => err instanceof KernelInputError && re.test(err.message) && /^np-null-mt\/1: /.test(err.message));
    });
  }

  test("a numeric seed is formatted as Python's f-string does", () => {
    assert.equal(checkInput({ ...good(), seed: 20260923 }).seed, "20260923");
  });
});

describe("the js-workers engine (the real worker script under worker_threads)", () => {
  test("3 workers, small chunks: equal to CPython on the synthetic world, progress reaches 1", async () => {
    const w = worldNamed("synthetic-mini");
    const seen = [];
    const counts = await runJsWorkers(prepare(w.input), { workers: 3, chunk: 7, createWorker: nodeWorker, onProgress: (f) => seen.push(f) });
    assert.deepEqual(counts, w.at_least);
    assert.equal(seen.at(-1), 1);
    assert.ok(seen.every((f, i) => i === 0 || f >= seen[i - 1]), "progress never goes back");
  });

  test("every fixture world, 2 workers", async () => {
    for (const w of FIX.worlds) {
      assert.deepEqual(await runJsWorkers(prepare(w.input), { workers: 2, createWorker: nodeWorker }), w.at_least, w.name);
    }
  });

  test("more workers than pathways: one per pathway", async () => {
    const before = started.length;
    const w = worldNamed("pipeline-three");
    assert.deepEqual(await runJsWorkers(prepare(w.input), { workers: 8, createWorker: nodeWorker }), w.at_least);
    assert.equal(started.length - before, 3);
  });

  test("an abort rejects with AbortError and terminates every worker", async () => {
    const before = started.length;
    const ctrl = new AbortController();
    const run = runJsWorkers(prepare(worldInput(WORLD, 400).input), { workers: 2, createWorker: nodeWorker, signal: ctrl.signal });
    setTimeout(() => ctrl.abort(), 150);
    await assert.rejects(run, (err) => err.name === "AbortError");
    const mine = started.slice(before);
    assert.equal(mine.length, 2);
    await new Promise((r) => setTimeout(r, 200));
    assert.ok(mine.every((w) => w.exited), "the workers were terminated");
  });

  test("a worker that fails rejects the run", async () => {
    const plan = prepare(worldNamed("pool-small-k").input);
    const broken = () => nodeWorker(new URL("data:text/javascript,throw new Error('boom')"));
    await assert.rejects(runJsWorkers(plan, { workers: 2, createWorker: broken }), /worker failed: boom/);
  });

  test("chunks: about six per worker, between 1 and 16", () => {
    assert.equal(chunkSize(1399, 4), 16);
    assert.equal(chunkSize(30, 4), 2);
    assert.equal(chunkSize(1, 8), 1);
  });
});

describe("the WGSL kernel", () => {
  test("its CPU emulation, over gpu.js's own buffers, equals CPython on every world (odd batches and passes)", async () => {
    for (const w of FIX.worlds) {
      const plan = prepare(w.input);
      const layout = gpuLayout(plan);
      const counts = [];
      const n = plan.ids.length;
      for (let from = 0; from < n; from += 5) {
        const to = Math.min(n, from + 5);
        const buf = await batchArrays(plan, layout, from, to);
        // passes of 37, 100, 1: the state persists between them
        for (let done = 0, i = 0; done < plan.permutations; i++) {
          const count = Math.min([37, 100, 1][i % 3], plan.permutations - done);
          buf.params[2] = count;
          dispatch(buf, Math.ceil(buf.B / 64) * 64);
          done += count;
        }
        counts.push(...buf.atLeast);
      }
      assert.deepEqual(counts, w.at_least, w.name);
    }
  });

  test("u32 only: no float type, no f16/f64, no atomics on floats", () => {
    assert.doesNotMatch(WGSL, /\bf(16|32|64)\b/);
    assert.match(WGSL, /@workgroup_size\(64\)/);
  });

  test("batches respect the binding limit (never assumed above 128 MiB)", () => {
    const plan = prepare(worldInput(WORLD).input);
    const layout = gpuLayout(plan);
    assert.equal(batchSize(plan, layout, {}), 1399);
    const small = batchSize(plan, layout, { maxStorageBufferBindingSize: 64 * 1024 });
    assert.ok(small < 1399 && small * 4 * Math.max(layout.stateRows, layout.workRows, layout.words) <= 64 * 1024);
    assert.equal(batchSize(plan, layout, { maxStorageBufferBindingSize: 4 * 1024 * 1024 * 1024 }), 1399, "a larger limit is not trusted beyond 128 MiB");
    assert.throws(() => batchSize(plan, layout, { maxStorageBufferBindingSize: 64 }), /bytes in one binding/);
  });

  test("runWebGPU on the stand-in: batches, passes of at most 100, readback, exact", async () => {
    const { gpu, stats } = fakeGpu({ limits: { maxStorageBufferBindingSize: 32 * 1024 } });
    const ctx = await openGpu({ gpu });
    assert.equal(ctx.ok, true);
    const w = worldNamed("synthetic-mini");
    const seen = [];
    const counts = await runWebGPU(prepare(w.input), ctx, { onProgress: (f) => seen.push(f) });
    assert.deepEqual(counts, w.at_least);
    assert.ok(stats.dispatches > 10, "several batches of several passes");
    assert.equal(stats.passes[0], 10, "a short first pass times the device");
    assert.ok(stats.passes.every((p) => p >= 1 && p <= 100));
    // a pass that takes long makes the next ones shorter; the numbers do not change
    const slow = await runWebGPU(prepare(w.input), ctx, { passTargetMs: 0.000001 });
    assert.deepEqual(slow, w.at_least);
    assert.ok(stats.passes.slice(-5).every((p) => p === 1));
    assert.ok(Math.abs(seen.at(-1) - 1) < 1e-12);
  });

  test("a software adapter is refused unless asked for", async () => {
    const { gpu } = fakeGpu({ software: true, architecture: "swiftshader" });
    const refused = await openGpu({ gpu });
    assert.equal(refused.ok, false);
    assert.equal(refused.software, true);
    const allowed = await openGpu({ gpu, allowSoftware: true });
    assert.equal(allowed.ok, true);
    assert.match(allowed.label, /^gpu \(software\): /);
  });
});

describe("the accelerator host", () => {
  const W = worldNamed("synthetic-mini");

  test("chooseEngine: auto takes a hardware GPU from 64 pathways only when measured faster; software never on auto; off never", () => {
    assert.equal(chooseEngine({ paths: GPU_MIN_PATHWAYS, gpu: "hardware", measured: true }), "webgpu");
    assert.equal(chooseEngine({ paths: GPU_MIN_PATHWAYS, gpu: "hardware" }), "js-workers"); // never measured here
    assert.equal(chooseEngine({ paths: 5000, gpu: "hardware", measured: false }), "js-workers"); // measured slower
    assert.equal(chooseEngine({ paths: GPU_MIN_PATHWAYS - 1, gpu: "hardware", measured: true }), "js-workers");
    assert.equal(chooseEngine({ paths: 5000, gpu: "software", allowSoftware: true }), "js-workers");
    assert.equal(chooseEngine({ paths: 5000, gpu: "none" }), "js-workers");
    assert.equal(chooseEngine({ paths: 5000, gpu: "hardware", gpuSetting: "off" }), "js-workers");
    assert.equal(chooseEngine({ paths: 5000, gpu: "hardware", gpuSetting: "off", engine: "webgpu" }), "js-workers");
    assert.equal(chooseEngine({ paths: 5000, gpu: "hardware", prefer: "cpu" }), "js-workers");
    assert.equal(chooseEngine({ paths: 3, gpu: "hardware", prefer: "gpu" }), "webgpu");
    assert.equal(chooseEngine({ paths: 3, gpu: "software", prefer: "gpu" }), "js-workers");
    assert.equal(chooseEngine({ paths: 3, gpu: "software", prefer: "gpu", allowSoftware: true }), "webgpu");
    assert.equal(chooseEngine({ paths: 100, gpu: "unknown", measured: true }), "webgpu", "not probed yet: the host probes, and only hardware passes");
    assert.equal(chooseEngine({ paths: 100, gpu: "unknown" }), "js-workers");
    assert.equal(chooseEngine({ paths: 3, gpu: "hardware", gpuSetting: "on", prefer: "gpu" }), "webgpu");
  });

  test("JS workers: the device's js_workers, lowered by a manual setting, at least 1", () => {
    assert.equal(jsWorkerCount({ js_workers: 7 }, { workers: "auto" }), 7);
    assert.equal(jsWorkerCount({ js_workers: 7 }, { workers: 2 }), 2);
    assert.equal(jsWorkerCount({ js_workers: 0 }, { workers: "auto" }) >= 1, true);
  });

  test("run(): js-workers result, the output shape, the digest", async () => {
    const host = createAccelHost({ device: { cls: "desktop", js_workers: 3, gpu: "none" }, settings: { workers: "auto", gpu: "auto", compute: { browser: { workers: "auto", gpu: "auto" } } }, createWorker: nodeWorker });
    assert.deepEqual(host.kernels(), ["np-null-mt/1"]);
    const progress = [];
    const out = await host.run("np-null-mt/1", W.input, { onProgress: (f) => progress.push(f) });
    assert.deepEqual(out.at_least, W.at_least);
    assert.equal(out.engine, "js-workers");
    assert.equal(out.device, "cpu: 3 JS workers");
    assert.match(out.kernel_digest, /^sha256:[0-9a-f]{64}$/);
    assert.equal(out.kernel_digest, await kernelDigest(), "stable");
    assert.ok(Number.isInteger(out.ms) && out.ms >= 0);
    assert.equal(progress.at(-1), 1);
    assert.equal(out.fallback, undefined);
    host.dispose();
    assert.deepEqual(host.kernels(), []);
  });

  test("run(): refuses an unknown kernel and a malformed input with a clear error", async () => {
    const host = createAccelHost({ device: { js_workers: 1, gpu: "none" }, createWorker: nodeWorker });
    await assert.rejects(host.run("np-null-pcg/1", W.input), /unknown kernel "np-null-pcg\/1"/);
    await assert.rejects(host.run("np-null-mt/1", { ...W.input, wanted: [[0, 9999]] }), (err) => err instanceof KernelInputError && /wanted\[0\]/.test(err.message));
    await assert.rejects(host.run("np-null-mt/1", W.input, { engine: "cuda" }), /unknown engine/);
  });

  test("run(): a hardware GPU on auto with >= 64 pathways, once measured faster here; the host recomputes the first and last pathway", async (t) => {
    const { gpu } = fakeGpu();
    // never measured on this device: auto stays on the exact JS workers
    const unmeasured = createAccelHost({ device: { js_workers: 2, gpu: "hardware" }, createWorker: nodeWorker, navigatorGpu: gpu });
    assert.equal((await unmeasured.run("np-null-mt/1", W.input)).engine, "js-workers");
    unmeasured.dispose();
    t.after(measuredGpu({ gpu_ms: 1000, cpu_ms: 5000 }));
    const host = createAccelHost({ device: { js_workers: 2, gpu: "hardware" }, createWorker: nodeWorker, navigatorGpu: gpu });
    const out = await host.run("np-null-mt/1", W.input);
    assert.equal(out.engine, "webgpu");
    assert.deepEqual(out.at_least, W.at_least);
    assert.match(out.device, /^gpu: test emulated/);
    assert.deepEqual(out.checked, [W.input.pathways[0].id, W.input.pathways.at(-1).id]);
    // fewer than 64 pathways stay on the CPU
    const small = worldNamed("pool-small-k");
    assert.equal((await host.run("np-null-mt/1", small.input)).engine, "js-workers");
    host.dispose();
  });

  test("run(): a GPU that miscounts is discarded, js-workers computes, the fallback says why", async (t) => {
    t.after(measuredGpu({ gpu_ms: 1000, cpu_ms: 5000 }));
    const { gpu } = fakeGpu({ tamper: (counts) => { counts[counts.length - 1] += 1; } });
    const host = createAccelHost({ device: { js_workers: 2, gpu: "hardware" }, createWorker: nodeWorker, navigatorGpu: gpu });
    const out = await host.run("np-null-mt/1", W.input);
    assert.equal(out.engine, "js-workers");
    assert.deepEqual(out.at_least, W.at_least);
    assert.equal(out.fallback.from, "webgpu");
    assert.match(out.fallback.reason, /^mismatch: .+: the GPU counted \d+, the JS kernel \d+$/);
    // the page does not trust this GPU again
    const next = await host.run("np-null-mt/1", W.input);
    assert.equal(next.engine, "js-workers");
    assert.match(next.fallback.reason, /since an earlier mismatch/);
    host.dispose();
  });

  test("run(): a software adapter is never used on auto, and used when the GPU is asked for and allowed", async () => {
    const { gpu, stats } = fakeGpu({ software: true, architecture: "swiftshader" });
    let host = createAccelHost({ device: { js_workers: 2, gpu: "software" }, createWorker: nodeWorker, navigatorGpu: gpu, allowSoftwareGpu: true });
    assert.equal((await host.run("np-null-mt/1", W.input)).engine, "js-workers");
    assert.equal(stats.dispatches, 0);
    const forced = await host.run("np-null-mt/1", W.input, { engine: "webgpu" });
    assert.equal(forced.engine, "webgpu");
    assert.match(forced.device, /^gpu \(software\): /);
    assert.deepEqual(forced.at_least, W.at_least);
    host.dispose();
    // the device report says hardware, the adapter says software: auto still refuses it
    host = createAccelHost({ device: { js_workers: 2, gpu: "hardware" }, createWorker: nodeWorker, navigatorGpu: gpu });
    const auto = await host.run("np-null-mt/1", W.input);
    assert.equal(auto.engine, "js-workers");
    assert.equal(auto.fallback, undefined, "auto falling back to the CPU is not news");
    const asked = await host.run("np-null-mt/1", W.input, { engine: "webgpu" });
    assert.equal(asked.engine, "js-workers");
    assert.match(asked.fallback.reason, /software adapter/);
    host.dispose();
  });

  test("run(): settings gpu off never touches the GPU, even when asked by name", async () => {
    const { gpu, stats } = fakeGpu();
    const host = createAccelHost({ device: { js_workers: 2, gpu: "hardware" }, settings: { compute: { browser: { workers: "auto", gpu: "off" } } }, createWorker: nodeWorker, navigatorGpu: gpu });
    assert.equal((await host.run("np-null-mt/1", W.input, { engine: "webgpu", prefer: "gpu" })).engine, "js-workers");
    assert.equal(stats.devices, 0);
    host.dispose();
  });

  test("run(): no pathways is an empty answer on every engine", async () => {
    const { gpu } = fakeGpu();
    const host = createAccelHost({ device: { js_workers: 2, gpu: "hardware" }, createWorker: nodeWorker, navigatorGpu: gpu });
    const empty = { ...W.input, pathways: [] };
    for (const engine of ["auto", "js-workers", "webgpu"]) {
      const out = await host.run("np-null-mt/1", empty, { engine });
      assert.deepEqual(out.at_least, [], engine);
    }
    host.dispose();
  });

  test("run(): a device that cannot be opened falls back to the CPU", async () => {
    const { gpu } = fakeGpu({ failDevice: true });
    const host = createAccelHost({ device: { js_workers: 2, gpu: "hardware" }, createWorker: nodeWorker, navigatorGpu: gpu });
    const out = await host.run("np-null-mt/1", W.input, { prefer: "gpu" });
    assert.equal(out.engine, "js-workers");
    assert.match(out.fallback.reason, /requestDevice failed/);
    host.dispose();
  });

  test("run(): an abort (the call's Stop, or dispose) rejects with AbortError", async () => {
    const host = createAccelHost({ device: { js_workers: 2, gpu: "none" }, createWorker: nodeWorker });
    const ctrl = new AbortController();
    const run = host.run("np-null-mt/1", worldInput(WORLD, 300).input, { signal: ctrl.signal });
    setTimeout(() => ctrl.abort(), 100);
    await assert.rejects(run, (err) => err.name === "AbortError");
    const again = host.run("np-null-mt/1", worldInput(WORLD, 300).input);
    setTimeout(() => host.dispose(), 100);
    await assert.rejects(again, (err) => err.name === "AbortError");
  });
});

describe("the benchmark", () => {
  test("rows for the reference, the workers and the GPU, all equal; the reference equals CPython", async () => {
    const { gpu } = fakeGpu();
    const seen = [];
    const out = await runBenchmark({
      device: { cls: "desktop", js_workers: 3, gpu: "hardware" }, settings: { workers: "auto", gpu: "auto" },
      world: WORLD, pathways: 24, createWorker: nodeWorker, navigatorGpu: gpu, onProgress: (f) => seen.push(f),
    });
    assert.deepEqual(out.rows.map((r) => r.engine), ["js", "js-workers", "webgpu"]);
    assert.ok(out.rows.every((r) => r.equal === true), JSON.stringify(out.rows));
    assert.ok(out.rows.every((r) => Number.isInteger(r.ms) && typeof r.note === "string" && r.note));
    assert.equal(out.world.pathways, 24);
    assert.equal(out.world.of, 1399);
    assert.equal(out.world.reference_equals_cpython, true);
    assert.equal(out.world.draws_per_permutation, 250);
    assert.match(out.rows[0].note, /参照/);
    assert.equal(seen.at(-1), 1);
  });

  test("GPU off, one worker, a software adapter not allowed: rows say why they were not run", async () => {
    let out = await runBenchmark({ device: { cls: "phone", js_workers: 1 }, settings: { gpu: "off" }, world: WORLD, pathways: 4, createWorker: nodeWorker });
    assert.deepEqual(out.rows.map((r) => [r.engine, r.ms === null]), [["js", false], ["js-workers", true], ["webgpu", true]]);
    assert.match(out.rows[2].note, /关闭 GPU/);
    const { gpu } = fakeGpu({ software: true, architecture: "swiftshader" });
    out = await runBenchmark({ device: { cls: "desktop", js_workers: 2 }, world: WORLD, pathways: 4, createWorker: nodeWorker, navigatorGpu: gpu });
    assert.equal(out.rows[2].ms, null);
    assert.match(out.rows[2].note, /软件模拟/);
    out = await runBenchmark({ device: { cls: "desktop", js_workers: 2 }, world: WORLD, pathways: 4, createWorker: nodeWorker, navigatorGpu: gpu, allowSoftwareGpu: true });
    assert.equal(out.rows[2].equal, true);
    assert.match(out.rows[2].note, /只检验结果/);
  });

  test("phones run the first 256 pathways by default", () => {
    assert.equal(worldInput(WORLD, 256).input.pathways.length, 256);
  });
});

/** A localStorage holding this device's benchmark result (web/js/accel/speed.js); returns the restore function. */
function measuredGpu(speed) {
  const before = Object.getOwnPropertyDescriptor(globalThis, "localStorage");
  const data = new Map([["tcmstudio.accel.speed/1", JSON.stringify(speed)]]);
  Object.defineProperty(globalThis, "localStorage", {
    configurable: true,
    value: { getItem: (k) => data.get(k) ?? null, setItem: (k, v) => data.set(k, String(v)), removeItem: (k) => data.delete(k) },
  });
  return () => {
    if (before) Object.defineProperty(globalThis, "localStorage", before);
    else delete globalThis.localStorage;
  };
}
