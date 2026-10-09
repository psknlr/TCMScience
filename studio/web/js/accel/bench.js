// The browser compute benchmark (docs/V2.md §13.4; Settings → Compute → 浏览器算力测试): the investigation's synthetic
// Reactome-like world (world-1399.json, made by web/test/accel/make_fixtures.py --bench with CPython's counts) through
// np-null-mt/1 on every engine this browser has, each compared with the single-thread JS reference, integer for
// integer. The reference itself is compared with CPython's counts shipped in the world.
//
//   runBenchmark({device, settings, onProgress, signal}) → {rows: [{engine, device, ms, equal, note}], world}
//
// A row with ms null was not run (its note says why). A software WebGPU adapter is a CPU emulation: it is run only
// when the caller allows it (allowSoftwareGpu), and labelled as such; it says nothing about GPU speed. Phones run the
// first 256 pathways unless `pathways` says otherwise (the whole world takes minutes on one phone core).

import { registerStrings, t } from "../core/i18n.js";
import { abortError } from "../core/util.js";
import { openGpu, runWebGPU } from "./gpu.js";
import { browserPrefs, jsWorkerCount } from "./host.js";
import { prepare } from "./npnull.js";
import { recordSpeed } from "./speed.js";
import { runJsWorkers } from "./workers.js";

registerStrings("zh", {
  "accel.bench.world": "合成通路世界（{pathways} 条通路 × {permutations} 次置换）",
  "accel.bench.note.reference": "1 个 JS 线程（参照）",
  "accel.bench.note.cpython-equal": "与 CPython 的计数逐一相同",
  "accel.bench.note.cpython-differ": "与 CPython 的计数不同",
  "accel.bench.note.workers": "{n} 个 JS 线程",
  "accel.bench.note.one-worker": "这台设备只用 1 个 JS 线程，与参照相同，未单独测试",
  "accel.bench.note.gpu": "WebGPU：{name}",
  "accel.bench.note.software": "软件模拟的 WebGPU（{name}）：只检验结果，不代表显卡速度",
  "accel.bench.note.software-skipped": "只有软件模拟的 WebGPU（{name}），未测试",
  "accel.bench.note.no-gpu": "没有可用的 WebGPU：{reason}",
  "accel.bench.note.gpu-off": "设置中已关闭 GPU",
  "accel.bench.note.failed": "运行失败：{error}",
  "accel.bench.note.subset": "（前 {n} 条通路）",
});

registerStrings("en", {
  "accel.bench.world": "Synthetic pathway world ({pathways} pathways × {permutations} permutations)",
  "accel.bench.note.reference": "1 JS thread (reference)",
  "accel.bench.note.cpython-equal": "equal to CPython's counts, one by one",
  "accel.bench.note.cpython-differ": "differs from CPython's counts",
  "accel.bench.note.workers": "{n} JS threads",
  "accel.bench.note.one-worker": "this device uses 1 JS thread, the same as the reference; not run separately",
  "accel.bench.note.gpu": "WebGPU: {name}",
  "accel.bench.note.software": "Software WebGPU ({name}): checks the result only, says nothing about GPU speed",
  "accel.bench.note.software-skipped": "Only a software WebGPU adapter ({name}); not run",
  "accel.bench.note.no-gpu": "No usable WebGPU: {reason}",
  "accel.bench.note.gpu-off": "The GPU is off in Settings",
  "accel.bench.note.failed": "Failed: {error}",
  "accel.bench.note.subset": " (first {n} pathways)",
});

/** Phones and low-memory phones run this many pathways by default. */
export const PHONE_PATHWAYS = 256;

/** world-1399.json, fetched next to this module. */
export async function loadWorld(fetchImpl = globalThis.fetch) {
  const res = await fetchImpl(new URL("./world-1399.json", import.meta.url));
  if (!res.ok) throw new Error(`the benchmark world could not be loaded (HTTP ${res.status})`);
  return res.json();
}

/**
 * The kernel input of a world file (its first `limit` pathways): pools are consecutive protein indices, members
 * are delta-encoded. Also returns CPython's counts for those pathways.
 */
export function worldInput(world, limit = Infinity) {
  let start = 0;
  const pools = world.pool_sizes.map((n) => {
    const pool = Array.from({ length: n }, (_, i) => start + i);
    start += n;
    return pool;
  });
  const count = Math.max(0, Math.min(world.ids.length, limit));
  const pathways = [];
  for (let i = 0; i < count; i++) {
    let x = 0;
    pathways.push({ id: world.ids[i], observed: world.observed[i], members: world.members_delta[i].map((d) => (x += d)) });
  }
  return {
    input: { seed: world.seed, permutations: world.permutations, pools, wanted: world.wanted, pathways },
    cpython: Array.isArray(world.cpython_at_least) ? world.cpython_at_least.slice(0, count) : null,
  };
}

const same = (a, b) => Array.isArray(a) && Array.isArray(b) && a.length === b.length && a.every((v, i) => v === b[i]);
const now = () => (globalThis.performance?.now ? performance.now() : Date.now());
const message = (err) => String(err?.message || err || "error");

/**
 * Run the benchmark. Options besides the interface: pathways (how many of the world's), allowSoftwareGpu,
 * createWorker and navigatorGpu (tests), world (an already loaded world-1399.json object), fetch.
 */
export async function runBenchmark({ device = {}, settings = {}, onProgress, signal, pathways, allowSoftwareGpu = false, createWorker, navigatorGpu, world, fetch: fetchImpl } = {}) {
  const prefs = browserPrefs(settings);
  const data = world || await loadWorld(fetchImpl);
  if (signal?.aborted) throw abortError();
  const phone = device?.cls === "phone" || device?.cls === "phone-low";
  const limit = Number.isInteger(pathways) && pathways > 0 ? pathways : phone ? PHONE_PATHWAYS : Infinity;
  const { input, cpython } = worldInput(data, limit);
  const plan = prepare(input);
  const n = plan.ids.length;
  const workers = jsWorkerCount(device, prefs);
  const stages = 1 + (workers > 1 ? 1 : 0) + (prefs.gpu === "off" ? 0 : 1);
  let stage = 0;
  const progress = (f) => onProgress?.(Math.min(1, (stage + Math.max(0, Math.min(1, f))) / stages));
  const subset = n < data.ids.length ? t("accel.bench.note.subset", { n }) : "";
  const rows = [];

  // the reference: the exact kernel in one worker
  let t0 = now();
  const reference = await runJsWorkers(plan, { workers: 1, signal, onProgress: progress, createWorker });
  const cpythonEqual = cpython ? same(reference, cpython) : null;
  rows.push({
    engine: "js", device: "cpu: 1 JS worker", ms: Math.round(now() - t0), equal: cpythonEqual,
    note: `${t("accel.bench.note.reference")}${subset}; ${t(cpythonEqual === false ? "accel.bench.note.cpython-differ" : "accel.bench.note.cpython-equal")}`,
  });
  stage++;

  if (workers > 1) {
    t0 = now();
    try {
      const got = await runJsWorkers(plan, { workers, signal, onProgress: progress, createWorker });
      rows.push({ engine: "js-workers", device: `cpu: ${workers} JS workers`, ms: Math.round(now() - t0), equal: same(got, reference), note: t("accel.bench.note.workers", { n: workers }) });
    } catch (err) {
      if (signal?.aborted) throw abortError();
      rows.push({ engine: "js-workers", device: `cpu: ${workers} JS workers`, ms: null, equal: null, note: t("accel.bench.note.failed", { error: message(err) }) });
    }
    stage++;
  } else {
    rows.push({ engine: "js-workers", device: "cpu: 1 JS worker", ms: null, equal: null, note: t("accel.bench.note.one-worker") });
  }

  if (prefs.gpu === "off") {
    rows.push({ engine: "webgpu", device: "", ms: null, equal: null, note: t("accel.bench.note.gpu-off") });
  } else {
    const ctx = await openGpu({ allowSoftware: allowSoftwareGpu, gpu: navigatorGpu ?? globalThis.navigator?.gpu });
    if (!ctx.ok) {
      const name = [ctx.info?.vendor, ctx.info?.architecture].filter(Boolean).join(" ") || "fallback";
      rows.push({ engine: "webgpu", device: "", ms: null, equal: null, note: ctx.software ? t("accel.bench.note.software-skipped", { name }) : t("accel.bench.note.no-gpu", { reason: ctx.reason }) });
    } else {
      const name = ctx.label.replace(/^gpu( \(software\))?: /, "");
      t0 = now();
      try {
        const got = await runWebGPU(plan, ctx, { signal, onProgress: progress });
        const ms = Math.round(now() - t0);
        const equal = same(got, reference);
        rows.push({ engine: "webgpu", device: ctx.label, ms, equal, note: t(ctx.info.software ? "accel.bench.note.software" : "accel.bench.note.gpu", { name }) });
        // what "auto" will use from now on: a hardware GPU that agreed and beat the fastest CPU row (speed.js)
        const cpu = rows.filter((r) => r.engine !== "webgpu" && Number.isFinite(r.ms) && r.equal !== false).sort((a, b) => a.ms - b.ms)[0];
        if (!ctx.info.software && equal && cpu) {
          recordSpeed({ adapter: name, gpu_ms: ms, cpu_ms: cpu.ms, cpu_engine: cpu.engine, workers: cpu.engine === "js-workers" ? workers : 1, pathways: n });
        }
      } catch (err) {
        if (signal?.aborted) throw abortError();
        rows.push({ engine: "webgpu", device: ctx.label, ms: null, equal: null, note: t("accel.bench.note.failed", { error: message(err) }) });
      } finally {
        try { ctx.device.destroy?.(); } catch { /* gone */ }
      }
    }
    stage++;
  }
  onProgress?.(1);
  return {
    rows,
    world: {
      name: data.name, title: t("accel.bench.world", { pathways: n, permutations: plan.permutations }),
      pathways: n, of: data.ids.length, permutations: plan.permutations, bins: plan.binN.length,
      draws_per_permutation: Array.from(plan.binK).reduce((a, b) => a + b, 0), reference_equals_cpython: cpythonEqual,
    },
  };
}
