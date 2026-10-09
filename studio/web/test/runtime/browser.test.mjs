// BrowserRuntime against a scripted worker: the lifecycle, the queue, cancellation (interrupt buffer, terminate and
// restart), fatal errors, boot failures. These run the pool with one worker (the v1 behaviour, and a phone's); the
// pool itself and the accelerator loop are in pool.test.mjs. The real worker and Pyodide are exercised in Chromium
// by run.mjs and e2e/pool.spec.mjs.
import "../fixtures/setup.mjs";
import assert from "node:assert/strict";
import { test } from "node:test";
import { BrowserRuntime, detectDevice } from "../../js/runtime/browser.js";

const SHA = "a".repeat(64);
const BOOT = {
  schema: "tcmstudio.boot/1",
  pyodide: { version: "314.0.7", index_url: "https://cdn.jsdelivr.net/pyodide/v314.0.7/full/", packages: ["pyyaml", "packaging", "sqlite3"] },
  bundle: { path: "runtime/tcms-py.aaaaaaaaaaaa.tar.gz", sha256: SHA, bytes: 1000, format: "gztar", extract_dir: "/opt/tcms/site", python_path: ["/opt/tcms/site"] },
  catalog: "runtime/catalog.json",
  state_root: "/persist",
  versions: { tcmstudio: "0.1.0", bioagent: "0.2.7", psh: "0.6.0" },
};
const CATALOG = {
  schema: "tcmstudio.catalog/1",
  core: [
    { name: "tcm_compatibility", maps_to: "native.tcm_compatibility" },
    { name: "tcm_safety_report", maps_to: "skill.assess-tcm-safety" },
    { name: "clinic_assess", maps_to: "clinic.assess" },
    { name: "capabilities_status", maps_to: "system.capabilities" },
    { name: "call_tool", maps_to: null },
  ],
  entries: [
    { id: "native.tcm_compatibility", kind: "native" },
    { id: "native.edit_distance", kind: "native" },
    { id: "skill.assess-tcm-safety", kind: "skill" },
    { id: "clinic.assess", kind: "clinic", pyodide_packages: ["numpy", "scipy"] },
    { id: "system.capabilities", kind: "system" },
    { id: "system.audit_verify", kind: "system" },
  ],
};

const json = (body, status = 200) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

function site({ boot = BOOT, bootStatus = 200 } = {}) {
  const calls = [];
  const fetch = async (url) => {
    calls.push(String(url));
    const p = new URL(url).pathname;
    if (p === "/runtime/boot.json") return bootStatus === 200 ? json(boot) : new Response("no", { status: bootStatus });
    if (p === "/runtime/catalog.json") return json(CATALOG);
    return new Response("missing", { status: 404 });
  };
  return { fetch, calls };
}

const envelope = (tool, extra = {}) => ({
  ok: true, tool, via: tool, status: "succeeded", duration_ms: 1, summary: `${tool} done`, text: "ok", result: { tool },
  citations: [], governance: { kind: "native", released: null, refusals: [], outputs: [] },
  receipt: { where: "browser", runtime: "pyodide-314.0.7 / CPython 3.14.2", device: "cpu", durable: true },
  job: null, approval: null, error: null, ...extra,
});

/** A Worker class whose instances answer the protocol the way pyodide.worker.js does, with per-op overrides. */
function workerClass(behavior = {}) {
  const instances = [];
  class FakeWorker {
    constructor(url, opts) {
      this.url = String(url);
      this.opts = opts;
      this.received = [];
      this.terminated = false;
      this.buffer = null;
      instances.push(this);
    }
    postMessage(data) {
      if (this.terminated) return;
      if (data && typeof data === "object" && data.type === "interrupt-buffer") {
        this.buffer = new Uint8Array(data.buffer);
        return;
      }
      if (data && typeof data === "object" && data.type === "wasm-module") {
        this.module = data.module;
        return;
      }
      assert.equal(typeof data, "string", "requests cross the boundary as JSON strings");
      const msg = JSON.parse(data);
      if (msg.type) {
        this.messages = [...(this.messages || []), msg];
        behavior.message?.(this, msg);
        return;
      }
      this.received.push(msg);
      const handler = behavior[msg.op] || DEFAULTS[msg.op];
      setTimeout(() => handler(this, msg), 0);
    }
    send(obj) {
      if (this.terminated) return;
      setTimeout(() => { if (!this.terminated) this.onmessage?.({ data: typeof obj === "string" ? obj : JSON.stringify(obj) }); }, 0);
    }
    reply(id, result, extra = {}) { this.send({ id, ok: true, ms: 1, result, ...extra }); }
    terminate() { this.terminated = true; }
  }
  FakeWorker.instances = instances;
  return FakeWorker;
}

const DEFAULTS = {
  init(w, { id }) {
    w.send({ type: "progress", progress: 3, stage: "pyodide", vars: { version: "314.0.7" }, message: "Loading Pyodide" });
    w.send({ type: "progress", progress: 76, stage: "packages", vars: { names: "pyyaml, packaging" }, message: "packages" });
    w.send({ type: "progress", progress: 89, stage: "import", vars: {}, message: "import" });
    w.reply(id, {
      runtime: "pyodide-314.0.7 / CPython 3.14.2", versions: { tcmstudio: "0.1.0", bioagent: "0.2.7", psh: "0.6.0", python: "3.14.2" },
      packages: ["pyyaml", "packaging"], persist: { mode: "idbfs", durable: true, error: null, root: "/persist" },
      bundle: { sha256: SHA, from_cache: false }, pycache: { restored: false }, timings: { pyodide: 5 }, boot_ms: 7,
    });
  },
  warm(w, { id }) { w.reply(id, { status: "succeeded", ms: 1 }); },
  call(w, { id, payload }) { w.send(`{"id":${id},"ok":true,"ms":2,"persist":{"mode":"idbfs","durable":true},"result":${JSON.stringify(envelope(payload.tool))}}`); },
  device(w, { id }) { w.reply(id, { cores: 1 }); },
  debug(w, { id }) { w.reply(id, { induced: "fatal" }); },
};

function runtime(opts = {}, behavior = {}) {
  const s = site(opts.site);
  const Worker = workerClass(behavior);
  const rt = new BrowserRuntime({ bootUrl: "https://science.impf.ai/runtime/boot.json", fetch: s.fetch, Worker, warm: false, interruptGraceMs: 50, workers: 1, accel: false, ...opts });
  const statuses = [];
  rt.onStatus((ev) => statuses.push(ev));
  return { rt, Worker, statuses, fetchCalls: s.calls };
}

const tick = (ms = 5) => new Promise((r) => setTimeout(r, ms));
async function until(cond, ms = 2000) {
  const t0 = Date.now();
  while (!cond()) {
    if (Date.now() - t0 > ms) throw new Error("condition not met in time");
    await tick(2);
  }
}

test("catalog() and device() answer without starting Python", async () => {
  const { rt, Worker, fetchCalls } = runtime();
  const doc = await rt.catalog();
  assert.equal(doc.entries.length, CATALOG.entries.length);
  assert.equal(rt.status, "idle");
  assert.equal(Worker.instances.length, 0);
  assert.deepEqual(fetchCalls, ["https://science.impf.ai/runtime/boot.json", "https://science.impf.ai/runtime/catalog.json"]);
  const dev = await rt.device();
  assert.equal(typeof dev.cross_origin_isolated, "boolean");
  assert.equal(dev.python.threads, 1);
  assert.equal(Worker.instances.length, 0);
});

test("detectDevice reports a software WebGPU adapter as such", async () => {
  const nav = {
    hardwareConcurrency: 8, deviceMemory: 16,
    gpu: { requestAdapter: async () => ({ isFallbackAdapter: true, info: { vendor: "google", architecture: "swiftshader", description: "" } }) },
  };
  const d = await detectDevice({ navigator: nav, isolated: true });
  assert.deepEqual(d.webgpu.adapter, { vendor: "google", architecture: "swiftshader", description: "", software: true, fallback: true });
  assert.equal(d.cores, 8);
  assert.equal(d.memory_gb, 16);
  const hw = await detectDevice({ navigator: { gpu: { requestAdapter: async () => ({ info: { vendor: "nvidia", architecture: "ada", description: "RTX 4090" } }) } } });
  assert.equal(hw.webgpu.adapter.software, false);
  const none = await detectDevice({ navigator: {} });
  assert.deepEqual(none.webgpu, { api: false, adapter: null });
});

test("start(): the boot manifest goes to the worker; progress becomes loading events; then ready", async () => {
  const { rt, Worker, statuses } = runtime({ indexUrl: "/pyodide/" });
  const info = await rt.start();
  assert.equal(rt.status, "ready");
  assert.equal(info.runtime, "pyodide-314.0.7 / CPython 3.14.2");
  const w = Worker.instances[0];
  assert.equal(w.opts.type, "module");
  assert.match(w.url, /pyodide\.worker\.js$/);
  const init = w.received[0];
  assert.equal(init.op, "init");
  assert.deepEqual(init.payload.boot, BOOT);
  assert.equal(init.payload.siteUrl, "https://science.impf.ai/");
  assert.equal(init.payload.indexUrl, "/pyodide/");
  assert.equal(init.payload.persist, true);
  const loading = statuses.filter((s) => s.status === "loading");
  assert.ok(loading.length >= 4);
  assert.ok(loading.every((s, i) => i === 0 || s.progress >= loading[i - 1].progress), "progress never goes back");
  assert.match(loading.find((s) => s.progress === 3).message, /Pyodide 314\.0\.7/);
  assert.equal(statuses.at(-1).status, "ready");
  assert.equal(statuses.at(-1).progress, 100);
  assert.equal(await rt.start(), info, "a second start() is the same boot");
  const i = await rt.info();
  assert.equal(i.status, "ready");
  assert.equal(i.bundle.sha256, SHA);
  assert.equal(i.persist.durable, true);
  assert.deepEqual(i.packages, ["pyyaml", "packaging"]);
});

test("call(): starts lazily; the catalog decides packages and state; the envelope comes back as Python wrote it", async () => {
  const { rt, Worker } = runtime();
  const env = await rt.call("clinic_assess", { intake: { a: 1 } }, { project_id: "p1", conversation_id: "c1", approvals: ["once"] });
  assert.equal(env.status, "succeeded");
  assert.equal(env.receipt.where, "browser");
  const call = Worker.instances[0].received.find((m) => m.op === "call");
  assert.deepEqual(call.payload.packages, ["numpy", "scipy"]);
  assert.equal(call.payload.stateful, true);
  assert.deepEqual(call.payload.context, { project_id: "p1", conversation_id: "c1", approvals: ["once"], device: "cpu" });
  await rt.call("call_tool", { tool: "skill.assess-tcm-safety", arguments: {} });
  await rt.call("call_tool", { tool: "native.edit_distance", arguments: {} });
  await rt.call("capabilities_status", {});
  const calls = Worker.instances[0].received.filter((m) => m.op === "call");
  assert.equal(calls[1].payload.stateful, true, "call_tool resolves to its entry");
  assert.equal(calls[2].payload.stateful, false);
  assert.deepEqual(calls[2].payload.packages, []);
  assert.ok(calls[3].payload.context.capabilities.browser.device, "capabilities_status gets the host facts");
  assert.deepEqual((await rt.info()).packages.sort(), ["numpy", "packaging", "pyyaml", "scipy"]);
});

test("a failed sync to IndexedDB marks the receipt durable:false", async () => {
  const { rt } = runtime({}, {
    call(w, { id, payload }) {
      w.send(`{"id":${id},"ok":true,"ms":2,"persist":{"mode":"idbfs","durable":false,"error":"sync to IndexedDB failed: quota"},"result":${JSON.stringify(envelope(payload.tool))}}`);
    },
  });
  const env = await rt.call("tcm_safety_report", { subject: "甘草" });
  assert.equal(env.receipt.durable, false);
  assert.match(env.receipt.persist_error, /quota/);
  assert.equal((await rt.info()).persist.durable, false);
});

test("calls run one at a time, in order", async () => {
  let inFlight = 0;
  let maxInFlight = 0;
  const { rt } = runtime({}, {
    call(w, { id, payload }) {
      inFlight++;
      maxInFlight = Math.max(maxInFlight, inFlight);
      setTimeout(() => { inFlight--; w.reply(id, envelope(payload.tool)); }, 5);
    },
  });
  const order = [];
  await Promise.all(["a", "b", "c"].map((x) => rt.call("call_tool", { tool: `native.${x}` }).then((e) => order.push(e.tool))));
  assert.equal(maxInFlight, 1);
  assert.equal(order.length, 3);
});

test("a queued call that is aborted is cancelled and never sent", async () => {
  const { rt, Worker } = runtime({}, {
    call(w, { id, payload }) { setTimeout(() => w.reply(id, envelope(payload.tool)), 30); },
  });
  await rt.start();
  const first = rt.call("tcm_compatibility", { herbs: ["a", "b"] });
  const ac = new AbortController();
  const second = rt.call("tcm_compatibility", { herbs: ["c", "d"] }, { signal: ac.signal });
  await tick(5);
  ac.abort();
  const env = await second;
  assert.equal(env.status, "cancelled");
  assert.equal(env.receipt.where, "browser");
  assert.match(env.receipt.input_sha256, /^[0-9a-f]{64}$/);
  assert.equal((await first).status, "succeeded");
  assert.equal(Worker.instances[0].received.filter((m) => m.op === "call").length, 1);
});

test("an already aborted signal is not run", async () => {
  const { rt, Worker } = runtime();
  const ac = new AbortController();
  ac.abort();
  const env = await rt.call("tcm_compatibility", {}, { signal: ac.signal });
  assert.equal(env.status, "cancelled");
  assert.equal(Worker.instances.length, 0);
});

test("not isolated: aborting a running call terminates the worker, and a new one takes the queue", async () => {
  const { rt, Worker, statuses } = runtime({}, {
    call(w, { id, payload }) {
      if (payload.arguments?.arguments?.slow) return; // never answers: Python is busy
      w.reply(id, envelope(payload.tool));
    },
  });
  await rt.start();
  assert.equal(rt.interruptible, false);
  const ac = new AbortController();
  const slow = rt.call("call_tool", { tool: "native.edit_distance", arguments: { slow: true } }, { signal: ac.signal });
  const next = rt.call("tcm_compatibility", { herbs: ["a", "b"] });
  await tick(10);
  ac.abort();
  const env = await slow;
  assert.equal(env.status, "cancelled");
  assert.equal(Worker.instances[0].terminated, true);
  assert.equal((await next).status, "succeeded", "the queued call runs on the new worker");
  assert.equal(Worker.instances.length, 2);
  assert.ok(statuses.some((s) => s.status === "loading" && /重新启动|Restarting/.test(s.message)));
});

test("isolated: abort writes SIGINT to the interrupt buffer and returns Python's cancelled envelope", async () => {
  globalThis.crossOriginIsolated = true;
  try {
    const { rt, Worker } = runtime({}, {
      call(w, { id, payload }) {
        const poll = setInterval(() => {
          if (w.buffer[0] === 2) {
            clearInterval(poll);
            w.buffer[0] = 0; // Pyodide resets it when it raises KeyboardInterrupt
            w.reply(id, envelope(payload.tool, { ok: false, status: "cancelled", summary: "已取消", error: { type: "runtime_error", message: "interrupted", hint: "" } }));
          }
        }, 2);
      },
    });
    await rt.start();
    assert.equal(rt.interruptible, true);
    const ac = new AbortController();
    const p = rt.call("call_tool", { tool: "native.edit_distance", arguments: {} }, { signal: ac.signal });
    await tick(10);
    ac.abort();
    const env = await p;
    assert.equal(env.status, "cancelled");
    assert.equal(env.summary, "已取消", "the dispatcher's own envelope");
    assert.equal(Worker.instances.length, 1, "no restart");
    assert.equal(Worker.instances[0].terminated, false);
  } finally {
    delete globalThis.crossOriginIsolated;
  }
});

test("isolated, but Python does not stop: hard cancel after the grace period", async () => {
  globalThis.crossOriginIsolated = true;
  try {
    const { rt, Worker } = runtime({ interruptGraceMs: 20 }, {
      call(w, { id, payload }) { if (!payload.arguments?.arguments?.stuck) w.reply(id, envelope(payload.tool)); },
    });
    await rt.start();
    const ac = new AbortController();
    const p = rt.call("call_tool", { tool: "native.edit_distance", arguments: { stuck: true } }, { signal: ac.signal });
    await tick(10);
    ac.abort();
    const env = await p;
    assert.equal(env.status, "cancelled");
    assert.equal(Worker.instances[0].terminated, true);
    await until(() => rt.status === "ready");
    assert.equal((await rt.call("tcm_compatibility", {})).status, "succeeded");
  } finally {
    delete globalThis.crossOriginIsolated;
  }
});

test("a fatal error fails the call as runtime_error and restarts the worker", async () => {
  let fatalOnce = true;
  const { rt, Worker } = runtime({}, {
    call(w, { id, payload }) {
      if (fatalOnce) {
        fatalOnce = false;
        w.send({ id, ok: false, ms: 1, fatal: true, error: { type: "fatal", message: "Pyodide already fatally failed and can no longer be used." } });
        return;
      }
      w.reply(id, envelope(payload.tool));
    },
  });
  const env = await rt.call("tcm_compatibility", { herbs: ["a", "b"] });
  assert.equal(env.status, "failed");
  assert.equal(env.error.type, "runtime_error");
  assert.match(env.error.message, /fatally failed/);
  assert.match(env.text, /no result/);
  const again = await rt.call("tcm_compatibility", { herbs: ["a", "b"] });
  assert.equal(again.status, "succeeded");
  assert.equal(Worker.instances.length, 2);
  assert.equal(Worker.instances[0].terminated, true);
});

test("a worker that crashes mid-call (error event) gives runtime_error and a restart", async () => {
  let crash = true;
  const { rt, Worker } = runtime({}, {
    call(w, { id, payload }) {
      if (crash) {
        crash = false;
        setTimeout(() => w.onerror?.({ message: "out of memory" }), 1);
        return;
      }
      w.reply(id, envelope(payload.tool));
    },
  });
  const env = await rt.call("tcm_compatibility", {});
  assert.equal(env.error.type, "runtime_error");
  assert.match(env.error.message, /out of memory/);
  assert.equal((await rt.call("tcm_compatibility", {})).status, "succeeded");
  assert.equal(Worker.instances.length, 2);
});

test("a worker that keeps failing is not restarted forever", async () => {
  const { rt, Worker } = runtime({}, {
    call(w, { id }) { w.send({ id, ok: false, ms: 1, fatal: true, error: { type: "fatal", message: "fatally failed" } }); },
  });
  for (let i = 0; i < 4; i++) {
    const env = await rt.call("tcm_compatibility", {});
    assert.equal(env.status, "failed");
    await until(() => rt.status !== "loading");
  }
  assert.equal(rt.status, "error");
  const before = Worker.instances.length;
  const env = await rt.call("tcm_compatibility", {});
  assert.equal(env.error.type, "unavailable");
  assert.equal(Worker.instances.length, before, "no new worker for a call");
});

test("a bundle that fails its hash stops the boot; calls then fail at once without a new worker", async () => {
  const { rt, Worker, statuses } = runtime({}, {
    init(w, { id }) { w.send({ id, ok: false, ms: 1, error: { type: "bundle_hash", message: "app bundle hash mismatch" } }); },
  });
  await assert.rejects(rt.start(), (err) => err.code === "bundle_hash");
  assert.equal(rt.status, "error");
  assert.match(statuses.at(-1).message, /SHA-256/);
  assert.equal(Worker.instances[0].terminated, true);
  const env = await rt.call("tcm_compatibility", {});
  assert.equal(env.status, "failed");
  assert.equal(env.error.type, "unavailable");
  assert.match(env.summary, /SHA-256/);
  assert.equal(Worker.instances.length, 1);
});

test("no runtime/boot.json: offline, and calls say so", async () => {
  const { rt, Worker } = runtime({ site: { bootStatus: 404 } });
  await assert.rejects(rt.catalog());
  assert.equal(rt.status, "offline");
  const env = await rt.call("tcm_compatibility", {});
  assert.equal(env.status, "failed");
  assert.equal(env.error.type, "unavailable");
  assert.equal(Worker.instances.length, 0);
});

test("without Worker support the runtime is offline from the start", async () => {
  const rt = new BrowserRuntime({ bootUrl: "https://science.impf.ai/runtime/boot.json", Worker: null, fetch: site().fetch });
  const saved = globalThis.Worker;
  try {
    globalThis.Worker = undefined;
    const r2 = new BrowserRuntime({ bootUrl: "https://science.impf.ai/runtime/boot.json", fetch: site().fetch });
    assert.equal(r2.status, "offline");
    await assert.rejects(r2.start());
    const env = await r2.call("tcm_compatibility", {});
    assert.equal(env.error.type, "unavailable");
  } finally {
    globalThis.Worker = saved;
  }
  assert.equal(rt.status, "offline");
});

test("a call that runs past its timeout is stopped and the runtime restarted", async () => {
  const { rt, Worker } = runtime({ callTimeoutMs: 30 }, {
    call(w, { id, payload }) { if (!payload.arguments?.arguments?.forever) w.reply(id, envelope(payload.tool)); },
  });
  const env = await rt.call("call_tool", { tool: "native.edit_distance", arguments: { forever: true } });
  assert.equal(env.status, "failed");
  assert.equal(env.error.type, "timeout");
  assert.equal(Worker.instances[0].terminated, true);
  assert.equal((await rt.call("tcm_compatibility", {})).status, "succeeded");
});

test("a worker-side failure that is not fatal is reported, and the worker stays", async () => {
  const { rt, Worker } = runtime({}, {
    call(w, { id }) { w.send({ id, ok: false, ms: 1, fatal: false, error: { type: "unavailable", message: "could not load numpy, scipy: network" } }); },
  });
  const env = await rt.call("clinic_assess", {});
  assert.equal(env.status, "failed");
  assert.equal(env.error.type, "unavailable");
  assert.match(env.error.message, /numpy/);
  assert.equal(Worker.instances.length, 1);
  assert.equal(Worker.instances[0].terminated, false);
});

test("stop() terminates the worker and fails what was queued", async () => {
  const { rt, Worker } = runtime({}, { call() { /* busy */ } });
  await rt.start();
  const running = rt.call("tcm_compatibility", {});
  const queued = rt.call("tcm_compatibility", {});
  await tick(10);
  rt.stop();
  assert.equal(rt.status, "idle");
  assert.equal((await running).status, "failed");
  assert.equal((await queued).status, "failed");
  assert.equal(Worker.instances[0].terminated, true);
});

test("warm-up runs first after boot, and debug() exists only for test pages", async () => {
  const { rt, Worker } = runtime({ warm: true, debug: false });
  await rt.call("tcm_compatibility", {});
  const ops = Worker.instances[0].received.map((m) => m.op);
  assert.deepEqual(ops, ["init", "warm", "call"]);
  await assert.rejects(rt.debug("fatal"), /debug operations are off/);
  const t2 = runtime({ debug: true });
  const reply = await t2.rt.debug("fatal");
  assert.equal(reply.ok, true);
});
