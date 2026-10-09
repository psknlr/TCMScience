// The Python worker pool (docs/V2.md §13.2) and the accelerator hand-off (§13.4), against scripted workers: who runs
// what, when a secondary is booted and retired, the shared compiled module, per-worker interrupts, Stop, failures,
// settings, and the accelerate → accelerated → resume loop with a stub accelerator host.
import { resetStorage } from "./fixtures/setup.mjs";
import assert from "node:assert/strict";
import { beforeEach, test } from "node:test";
import { updateSettings } from "../js/core/settings.js";
import { BrowserRuntime } from "../js/runtime/browser.js";
import { idleMsFor, pickWorker, poolStats, wantsSpawn } from "../js/runtime/pool.js";
import { EMPTY_WASM, envelope, site, tick, until, workerClass } from "./runtime/fake-worker.mjs";

beforeEach(() => resetStorage());

const DESKTOP = { cores: 8, memory_gb: 8, mobile: false, ios: false, tablet: false };

function runtime(opts = {}, behavior = {}) {
  const s = site(opts.site);
  const Worker = workerClass(behavior);
  const rt = new BrowserRuntime({
    bootUrl: "https://science.impf.ai/runtime/boot.json", fetch: s.fetch, Worker, warm: false, interruptGraceMs: 50,
    spawnDelayMs: 0, device: DESKTOP, accel: false, ...opts,
  });
  const statuses = [];
  rt.onStatus((ev) => statuses.push(ev));
  return { rt, Worker, statuses, fetchCalls: s.calls };
}

/** A call behaviour that holds each call until release(tool) is called; records how many run at once. */
function held() {
  const waiting = new Map();
  const stats = { inFlight: 0, max: 0 };
  const behavior = {
    call(w, { id, payload }) {
      stats.inFlight++;
      stats.max = Math.max(stats.max, stats.inFlight);
      const key = payload.arguments?.arguments?.key ?? payload.tool;
      waiting.set(key, () => { stats.inFlight--; w.reply(id, envelope(payload.tool, { result: { key, worker: w.opts.name } })); });
    },
  };
  return { behavior, stats, release: (k) => { waiting.get(k)?.(); waiting.delete(k); }, waiting };
}

const job = (k) => ({ stateful: false, internal: false, k });

test("the rules: stateful and internal work needs the primary; stateless takes the primary first, then a free secondary", () => {
  const primary = { primary: true, state: "ready", job: null };
  const second = { primary: false, state: "ready", job: null };
  const booting = { primary: false, state: "booting", job: null };
  assert.equal(pickWorker(job(), [primary, second]), primary);
  assert.equal(pickWorker({ stateful: true }, [{ ...primary, job: {} }, second]), null);
  assert.equal(pickWorker(job(), [{ ...primary, job: {} }, second]), second);
  assert.equal(pickWorker(job(), [{ ...primary, job: {} }, booting]), null);
  assert.equal(pickWorker({ internal: true }, [primary, second], { primaryReady: false }), null);
  assert.equal(pickWorker(job(), [primary, second], { primaryReady: false }), second, "a secondary serves while the primary restarts");

  const busy = { ...primary, job: {} };
  assert.equal(wantsSpawn({ queue: [job()], workers: [busy], size: 2 }), true);
  assert.equal(wantsSpawn({ queue: [{ stateful: true }], workers: [busy], size: 2 }), false, "a stateful call waits for the primary");
  assert.equal(wantsSpawn({ queue: [job()], workers: [busy], size: 1 }), false);
  assert.equal(wantsSpawn({ queue: [job()], workers: [busy, booting], size: 3 }), false, "one boot at a time");
  assert.equal(wantsSpawn({ queue: [], workers: [busy], size: 2 }), false);

  const stats = poolStats([busy, second, booting, { primary: false, state: "dead", job: null }], { size: 3, cls: "desktop", cap: 7 });
  assert.deepEqual({ size: stats.size, live: stats.live, busy: stats.busy, booting: stats.booting, cls: stats.cls }, { size: 3, live: 2, busy: 1, booting: 1, cls: "desktop" });
  assert.equal(stats.workers.length, 3);
  assert.equal(idleMsFor("phone"), 60_000);
  assert.equal(idleMsFor("tablet"), 60_000);
  assert.equal(idleMsFor("desktop"), 600_000);
});

test("two calls run at once on a desktop: the second boots a secondary (no persistence, never saves compiled modules)", async () => {
  const h = held();
  const { rt, Worker } = runtime({ workers: 2 }, h.behavior);
  const a = rt.call("call_tool", { tool: "native.edit_distance", arguments: { key: "a" } });
  const b = rt.call("call_tool", { tool: "native.edit_distance", arguments: { key: "b" } });
  await until(() => h.waiting.size === 2);
  assert.equal(h.stats.max, 2, "both calls are running");
  assert.equal(Worker.instances.length, 2);
  const [primary, secondary] = Worker.instances;
  assert.equal(primary.opts.name, "tcmstudio-python");
  assert.equal(secondary.opts.name, "tcmstudio-python-1");
  const init2 = secondary.received.find((m) => m.op === "init").payload;
  assert.equal(init2.persist, false);
  assert.equal(init2.role, "secondary");
  assert.equal(primary.received[0].payload.role, "primary");
  assert.equal(primary.received[0].payload.persist, true);
  const pool = rt.pool();
  assert.deepEqual({ size: pool.size, live: pool.live, busy: pool.busy, cls: pool.cls }, { size: 2, live: 2, busy: 2, cls: "desktop" });
  h.release("a");
  h.release("b");
  const [ea, eb] = await Promise.all([a, b]);
  assert.notEqual(ea.result.worker, eb.result.worker, "one call on each worker");
  assert.equal((await rt.info()).pool.busy, 0);
});

test("a pool of one never spawns; the calls queue on the primary", async () => {
  const h = held();
  const { rt, Worker } = runtime({ workers: 1 }, h.behavior);
  const calls = ["a", "b", "c"].map((k) => rt.call("call_tool", { tool: "native.edit_distance", arguments: { key: k } }));
  await until(() => h.waiting.size === 1);
  await tick(20);
  assert.equal(Worker.instances.length, 1);
  for (const k of ["a", "b", "c"]) {
    await until(() => h.waiting.has(k));
    h.release(k);
  }
  assert.ok((await Promise.all(calls)).every((e) => e.status === "succeeded"));
  assert.equal(h.stats.max, 1);
});

test("stateful calls run on the primary only, even while a secondary is free", async () => {
  const h = held();
  const { rt, Worker } = runtime({ workers: 2 }, h.behavior);
  const a = rt.call("call_tool", { tool: "native.edit_distance", arguments: { key: "a" } });
  const b = rt.call("call_tool", { tool: "native.edit_distance", arguments: { key: "b" } });
  await until(() => h.waiting.size === 2);
  h.release("b"); // the secondary (b went there) is free again
  await b;
  const s = rt.call("tcm_safety_report", { subject: "甘草" });
  await tick(20);
  assert.equal(h.waiting.has("tcm_safety_report"), false, "the governed call waits for the primary");
  h.release("a");
  await a;
  await until(() => h.waiting.has("tcm_safety_report"));
  const governedOn = Worker.instances.find((w) => w.received.some((m) => m.op === "call" && m.payload.tool === "tcm_safety_report"));
  assert.equal(governedOn, Worker.instances[0]);
  h.release("tcm_safety_report");
  assert.equal((await s).status, "succeeded");
});

test("the page compiles Pyodide's WebAssembly once and posts the module to each worker before init", async () => {
  const h = held();
  const { rt, Worker, fetchCalls } = runtime({ workers: 2, site: { wasm: EMPTY_WASM } }, h.behavior);
  const a = rt.call("call_tool", { tool: "native.edit_distance", arguments: { key: "a" } });
  const b = rt.call("call_tool", { tool: "native.edit_distance", arguments: { key: "b" } });
  await until(() => h.waiting.size === 2);
  for (const w of Worker.instances) {
    assert.ok(w.module instanceof WebAssembly.Module, "a compiled module");
    assert.ok(w.log.indexOf("wasm-module") < w.log.indexOf("init"), "before init");
  }
  assert.equal(Worker.instances[0].module, Worker.instances[1].module, "the same module");
  assert.equal(fetchCalls.filter((u) => u.endsWith("/pyodide.asm.wasm")).length, 1, "fetched and compiled once");
  h.release("a");
  h.release("b");
  await Promise.all([a, b]);
});

test("without the compiled module (no network for it) each worker compiles its own: no module message", async () => {
  const { rt, Worker } = runtime({ workers: 1 });
  await rt.call("tcm_compatibility", {});
  assert.deepEqual(Worker.instances[0].log.slice(0, 2), ["init", "call"]);
});

test("an idle secondary retires after the class's idle time; the primary stays", async () => {
  const h = held();
  const { rt, Worker } = runtime({ workers: 2, idleMs: 30 }, h.behavior);
  const a = rt.call("call_tool", { tool: "native.edit_distance", arguments: { key: "a" } });
  const b = rt.call("call_tool", { tool: "native.edit_distance", arguments: { key: "b" } });
  await until(() => h.waiting.size === 2);
  h.release("a");
  h.release("b");
  await Promise.all([a, b]);
  await until(() => Worker.instances[1].terminated);
  assert.equal(Worker.instances[0].terminated, false);
  assert.equal(rt.pool().live, 1);
});

test("a secondary that does not boot in time is tried once more, then the pool shrinks", async () => {
  const h = held();
  let inits = 0;
  const { rt, Worker } = runtime({ workers: 2, bootTimeoutMs: 40 }, {
    ...h.behavior,
    init(w, msg) {
      inits++;
      if (msg.payload.role === "secondary") return; // never answers: a hung boot
      w.reply(msg.id, { runtime: "pyodide-314.0.7", persist: { mode: "idbfs", durable: true }, packages: [] });
    },
  });
  const a = rt.call("call_tool", { tool: "native.edit_distance", arguments: { key: "a" } });
  const b = rt.call("call_tool", { tool: "native.edit_distance", arguments: { key: "b" } });
  await until(() => rt.pool().size === 1, 3000);
  assert.equal(inits, 3, "the primary, a secondary, and its one respawn");
  assert.equal(Worker.instances.length, 3);
  assert.ok(Worker.instances[1].terminated && Worker.instances[2].terminated);
  assert.equal(rt.pool().shrunk, 1);
  h.release("a");
  await until(() => h.waiting.has("b"));
  h.release("b");
  assert.ok((await Promise.all([a, b])).every((e) => e.status === "succeeded"), "the waiting call ran on the primary");
});

test("a primary that does not boot in time is tried once more before the runtime reports an error", async () => {
  let inits = 0;
  const { rt, Worker } = runtime({ workers: 1, bootTimeoutMs: 30 }, {
    init(w, msg) {
      inits++;
      if (inits === 1) return;
      w.reply(msg.id, { runtime: "pyodide-314.0.7", persist: { mode: "idbfs", durable: true }, packages: [] });
    },
  });
  await rt.start();
  assert.equal(rt.status, "ready");
  assert.equal(Worker.instances.length, 2);
  assert.equal(Worker.instances[0].terminated, true);
});

test("Stop on a secondary, not isolated: that worker is terminated, the primary keeps running", async () => {
  const h = held();
  const { rt, Worker } = runtime({ workers: 2 }, h.behavior);
  const a = rt.call("call_tool", { tool: "native.edit_distance", arguments: { key: "a" } });
  const ac = new AbortController();
  const b = rt.call("call_tool", { tool: "native.edit_distance", arguments: { key: "b" } }, { signal: ac.signal });
  await until(() => h.waiting.size === 2);
  ac.abort();
  const eb = await b;
  assert.equal(eb.status, "cancelled");
  assert.equal(Worker.instances[1].terminated, true);
  assert.equal(Worker.instances[0].terminated, false);
  assert.equal(rt.status, "ready", "no restart");
  h.release("a");
  assert.equal((await a).status, "succeeded");
  assert.equal(rt.pool().shrunk, 0, "a stop does not shrink the pool");
});

test("isolated: each worker has its own interrupt buffer, and Stop signals only the worker running that call", async () => {
  globalThis.crossOriginIsolated = true;
  try {
    const answered = new Map();
    const { rt, Worker } = runtime({ workers: 2 }, {
      call(w, { id, payload }) {
        const key = payload.arguments?.arguments?.key;
        const poll = setInterval(() => {
          if (w.buffer[0] === 2) {
            clearInterval(poll);
            w.buffer[0] = 0;
            w.reply(id, envelope(payload.tool, { ok: false, status: "cancelled", summary: "已取消", error: { type: "runtime_error", message: "interrupted", hint: "" } }));
          }
        }, 2);
        answered.set(key, () => { clearInterval(poll); w.reply(id, envelope(payload.tool)); });
      },
    });
    const a = rt.call("call_tool", { tool: "native.edit_distance", arguments: { key: "a" } });
    const ac = new AbortController();
    const b = rt.call("call_tool", { tool: "native.edit_distance", arguments: { key: "b" } }, { signal: ac.signal });
    await until(() => answered.size === 2);
    const [w0, w1] = Worker.instances;
    assert.ok(w0.buffer && w1.buffer && w0.buffer.buffer !== w1.buffer.buffer, "two buffers");
    ac.abort();
    const eb = await b;
    assert.equal(eb.status, "cancelled");
    assert.equal(eb.summary, "已取消", "Python's own cancelled envelope");
    assert.equal(w0.buffer[0], 0, "the primary was not interrupted");
    assert.equal(w1.terminated, false);
    answered.get("a")();
    assert.equal((await a).status, "succeeded");
  } finally {
    delete globalThis.crossOriginIsolated;
  }
});

test("a fatal error on a secondary fails that call, retires the worker and shrinks the pool; the primary is untouched", async () => {
  const h = held();
  const { rt, Worker } = runtime({ workers: 3 }, {
    call(w, msg) {
      if (msg.payload.arguments?.arguments?.key === "boom") {
        w.send({ id: msg.id, ok: false, ms: 1, fatal: true, error: { type: "fatal", message: "Pyodide already fatally failed and can no longer be used." } });
        return;
      }
      h.behavior.call(w, msg);
    },
  });
  const a = rt.call("call_tool", { tool: "native.edit_distance", arguments: { key: "a" } });
  await until(() => h.waiting.has("a"));
  const boom = await rt.call("call_tool", { tool: "native.edit_distance", arguments: { key: "boom" } });
  assert.equal(boom.status, "failed");
  assert.equal(boom.error.type, "runtime_error");
  assert.equal(Worker.instances[1].terminated, true);
  assert.equal(Worker.instances[0].terminated, false);
  assert.equal(rt.pool().size, 2);
  assert.equal(rt.pool().shrunk, 1);
  assert.equal(rt.status, "ready");
  h.release("a");
  assert.equal((await a).status, "succeeded");
});

test("stop() terminates every worker; configure() resizes the pool and retires idle secondaries beyond it", async () => {
  const h = held();
  const { rt, Worker } = runtime({ workers: undefined, device: { cores: 8, memory_gb: 8 } }, h.behavior);
  assert.equal(rt.pool().size, 4, "a desktop with 8 cores and 8 GB");
  rt.configure({ workers: 2 });
  assert.equal(rt.pool().size, 2);
  const a = rt.call("call_tool", { tool: "native.edit_distance", arguments: { key: "a" } });
  const b = rt.call("call_tool", { tool: "native.edit_distance", arguments: { key: "b" } });
  await until(() => h.waiting.size === 2);
  h.release("b");
  await b;
  rt.configure({ workers: 1 });
  await until(() => Worker.instances[1].terminated);
  assert.equal(rt.pool().size, 1);
  rt.configure({ workers: 20 });
  assert.equal(rt.pool().size, 4, "an invalid number is auto");
  rt.stop();
  assert.ok(Worker.instances.every((w) => w.terminated));
  assert.equal((await a).status, "failed");
  assert.equal(rt.status, "idle");
});

test("the runtime follows settings.computeBrowser when it was created with the app's settings", async () => {
  const { rt } = runtime({ workers: undefined, settings: { computeBrowser: { workers: 3, gpu: "auto" } } });
  assert.equal(rt.pool().size, 3);
  updateSettings({ computeBrowser: { workers: 1 } });
  assert.equal(rt.pool().size, 1);
  updateSettings({ computeBrowser: { workers: "auto" } });
  assert.equal(rt.pool().size, 4);
});

test("status events and info() carry the pool", async () => {
  const { rt, statuses } = runtime({ workers: 2 });
  await rt.start();
  assert.deepEqual(Object.keys(statuses.at(-1).pool).sort(), ["booting", "busy", "cap", "cls", "live", "shrunk", "size", "workers"]);
  const info = await rt.info();
  assert.equal(info.pool.size, 2);
  assert.equal(info.pool.live, 1);
  assert.equal(info.pool.cls, "desktop");
  const pools = [];
  const off = rt.onPool((p) => pools.push(p.busy));
  await rt.call("tcm_compatibility", {});
  off();
  assert.ok(pools.includes(1) && pools.at(-1) === 0, "busy while the call ran, then free");
});

// ---------------------------------------------------------------------------------------------------- accelerators

/** A worker whose call asks the page to accelerate once, then answers with what came back (as Python would). */
function acceleratingWorker({ kernel = "np-null-mt/1", input = { n: 10 } } = {}) {
  const asked = new Map();
  return {
    asked,
    behavior: {
      call(w, { id, payload }) {
        asked.set(id, { w, payload });
        w.send({ type: "accelerate", id, token: `tok-${id}`, kernel, input });
      },
      message(w, msg) {
        if (msg.type !== "accelerated") return;
        const { payload } = asked.get(msg.id);
        if (msg.cancelled) {
          w.send({ id: msg.id, ok: false, ms: 1, fatal: false, error: { type: "cancelled", message: "stopped during the accelerated step" } });
          return;
        }
        w.reply(msg.id, envelope(payload.tool, { result: { accelerated: msg.ok, answer: msg.result ?? null, error: msg.error ?? null } }));
      },
    },
  };
}

function stubHost(run) {
  const seen = { runs: [], signals: [] };
  return {
    seen,
    host: {
      kernels: () => ["np-null-mt/1"],
      async run(kernel, input, { signal, onProgress }) {
        seen.runs.push({ kernel, input });
        seen.signals.push(signal);
        return run(kernel, input, { signal, onProgress });
      },
    },
  };
}

test("the accelerator loop: context.accel lists the host's kernels; the kernel runs on the page; the call resumes with its result", async () => {
  const wk = acceleratingWorker();
  const { host, seen } = stubHost(async (kernel, input, { onProgress }) => {
    onProgress(0.5);
    onProgress({ done: 3, total: 4 });
    return { sum: 45, engine: "js-workers" };
  });
  const { rt, Worker, statuses } = runtime({ workers: 1, accel: true, accelHost: host }, wk.behavior);
  const env = await rt.call("call_tool", { tool: "native.edit_distance", arguments: {} });
  assert.equal(env.status, "succeeded");
  assert.deepEqual(env.result, { accelerated: true, answer: { sum: 45, engine: "js-workers" }, error: null });
  const call = Worker.instances[0].received.find((m) => m.op === "call");
  assert.deepEqual(call.payload.context.accel, { kernels: ["np-null-mt/1"], prefer: "auto" });
  assert.deepEqual(seen.runs, [{ kernel: "np-null-mt/1", input: { n: 10 } }]);
  const answer = Worker.instances[0].messages.find((m) => m.type === "accelerated");
  assert.equal(answer.token, `tok-${call.id}`);
  assert.equal(answer.ok, true);
  assert.ok(statuses.some((s) => /度匹配置换零分布/.test(s.message) && /50%/.test(s.message)), "progress shows in the runtime status");
  assert.ok(statuses.some((s) => /75%/.test(s.message)));
  assert.match(statuses.at(-1).message, /就绪/);
});

test("an accelerator that fails answers ok:false with the error (Python then computes natively)", async () => {
  const wk = acceleratingWorker();
  const { host } = stubHost(async () => { throw new Error("WebGPU device lost"); });
  const { rt } = runtime({ workers: 1, accel: true, accelHost: host }, wk.behavior);
  const env = await rt.call("call_tool", { tool: "native.edit_distance", arguments: {} });
  assert.equal(env.status, "succeeded");
  assert.equal(env.result.accelerated, false);
  assert.match(env.result.error, /device lost/);
});

test("Stop during an accelerated step aborts the host's signal and cancels the call without terminating the worker", async () => {
  const wk = acceleratingWorker();
  let started;
  const begun = new Promise((r) => { started = r; });
  const { host, seen } = stubHost((kernel, input, { signal }) => new Promise((resolve, reject) => {
    started();
    signal.addEventListener("abort", () => setTimeout(() => reject(new DOMException("aborted", "AbortError")), 200));
  }));
  const { rt, Worker } = runtime({ workers: 1, accel: true, accelHost: host, interruptGraceMs: 2000 }, wk.behavior);
  const ac = new AbortController();
  const p = rt.call("call_tool", { tool: "native.edit_distance", arguments: {} }, { signal: ac.signal });
  await begun;
  const t0 = Date.now();
  ac.abort();
  const env = await p;
  assert.equal(env.status, "cancelled");
  assert.ok(Date.now() - t0 < 150, "answered at once, not after the kernel noticed");
  assert.equal(seen.signals[0].aborted, true);
  assert.equal(Worker.instances.length, 1);
  assert.equal(Worker.instances[0].terminated, false);
  const answer = Worker.instances[0].messages.find((m) => m.type === "accelerated");
  assert.equal(answer.cancelled, true);
  assert.equal(Worker.instances[0].messages.filter((m) => m.type === "accelerated").length, 1, "answered once");
});

test("the GPU setting reaches the call (prefer cpu), and a page without an accelerator lists no kernels", async () => {
  const { host } = stubHost(async () => ({}));
  const off = runtime({ workers: 1, accel: true, accelHost: host, settings: { computeBrowser: { gpu: "off" } } });
  await off.rt.call("tcm_compatibility", {});
  assert.deepEqual(off.Worker.instances[0].received.find((m) => m.op === "call").payload.context.accel, { kernels: ["np-null-mt/1"], prefer: "cpu" });
  const none = runtime({ workers: 1, accel: true, importAccel: async () => { throw new Error("no module"); } });
  await none.rt.call("tcm_compatibility", {});
  assert.equal(none.Worker.instances[0].received.find((m) => m.op === "call").payload.context.accel, undefined);
});

test("the host module gets the classified device and the browser settings", async () => {
  let got = null;
  const { rt } = runtime({
    workers: 1, accel: true, settings: { computeBrowser: { workers: 1, gpu: "off" } },
    importAccel: async () => ({ createAccelHost: (args) => { got = args; return { kernels: () => ["np-null-mt/1"], run: async () => ({}) }; } }),
  });
  await rt.call("tcm_compatibility", {});
  assert.ok(got.device.cls, "device class");
  assert.equal(typeof got.device.js_workers, "number");
  assert.equal(got.device.gpu_enabled, false);
  assert.equal(got.settings.gpu, "off");
  assert.deepEqual(got.settings.compute.browser, { workers: 1, gpu: "off" });
});

test("a screen wake lock is held while an accelerated step runs, and released after", async () => {
  const events = [];
  const saved = Object.getOwnPropertyDescriptor(globalThis, "navigator");
  Object.defineProperty(globalThis, "navigator", {
    configurable: true,
    value: { hardwareConcurrency: 8, userAgent: "test", wakeLock: { request: async () => { events.push("lock"); return { release: () => events.push("release") }; } } },
  });
  try {
    const wk = acceleratingWorker();
    const { host } = stubHost(async () => { events.push("run"); return { sum: 1 }; });
    const { rt } = runtime({ workers: 1, accel: true, accelHost: host }, wk.behavior);
    await rt.call("call_tool", { tool: "native.edit_distance", arguments: {} });
    await until(() => events.includes("release"));
    assert.deepEqual(events, ["lock", "run", "release"]);
  } finally {
    Object.defineProperty(globalThis, "navigator", saved);
  }
});
