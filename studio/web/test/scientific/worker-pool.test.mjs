// ScienceWorkerPool (js/scientific/runtime/worker-pool.js) against scripted job workers: sizing from the device, lazy
// boots, package loading, the request/reply bookkeeping, timeouts, cancellation with and without the interrupt
// buffer, crashes and fatal errors, and slots that boot a new worker only when the next lease needs one. The real
// worker (engines/science.worker.js) runs in Chromium through test/runtime/science-check.mjs.
import "../fixtures/setup.mjs";
import assert from "node:assert/strict";
import { test } from "node:test";
import { isAbort } from "../../js/core/util.js";
import { MAX_POOL_SIZE, ScienceWorkerPool, recommendedPoolSize } from "../../js/scientific/runtime/worker-pool.js";

const BOOT = { schema: "tcmstudio.boot/1", pyodide: { version: "314.0.7", index_url: "https://cdn.example/pyodide/" }, bundle: { path: "runtime/b.tar.gz", sha256: "a".repeat(64) } };
const INFO = { runtime: "pyodide-314.0.7 / CPython 3.14.2", platform: "emscripten", machine: "wasm32", packages: ["pyyaml", "packaging"] };

/** A Worker class whose instances answer the job-worker protocol, with per-op overrides. */
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
      assert.equal(typeof data, "string", "requests cross the boundary as JSON strings");
      const msg = JSON.parse(data);
      this.received.push(msg);
      const handler = behavior[msg.op] || DEFAULTS[msg.op] || ((w, m) => w.fail(m.id, "bad_request", `unknown operation: ${m.op}`));
      setTimeout(() => handler(this, msg), 0);
    }
    send(obj) {
      if (this.terminated) return;
      setTimeout(() => { if (!this.terminated) this.onmessage?.({ data: typeof obj === "string" ? obj : JSON.stringify(obj) }); }, 0);
    }
    reply(id, result) { this.send({ id, ok: true, ms: 1, result }); }
    fail(id, type, message, extra = {}) { this.send({ id, ok: false, ms: 1, fatal: false, error: { type, message }, ...extra }); }
    event(id, event) { this.send({ type: "event", id, event }); }
    crash(message = "boom") { setTimeout(() => { if (!this.terminated) this.onerror?.({ message, preventDefault() {} }); }, 0); }
    terminate() { this.terminated = true; }
    ops() { return this.received.map((m) => m.op); }
  }
  FakeWorker.instances = instances;
  return FakeWorker;
}

const DEFAULTS = {
  init(w, { id }) {
    w.send({ type: "progress", progress: 40, stage: "bundle", message: "bundle" });
    w.reply(id, { ...INFO });
  },
  packages(w, { id, payload }) {
    const unknown = payload.names.filter((n) => n === "nosuchpkg");
    if (unknown.length) return w.fail(id, "unavailable", `Pyodide 314.0.7 has no package ${unknown.join(", ")}`);
    w.reply(id, { packages: [...INFO.packages, ...payload.names] });
  },
  run(w, { id, payload }) {
    w.event(id, { kind: "progress", fraction: 0.5, message: "half" });
    w.event(id, { kind: "log", level: "info", text: `ran ${payload.fn}` });
    w.reply(id, { ok: true, result: { fn: payload.fn } });
  },
  memory(w, { id }) { w.reply(id, { wasm_bytes: 64 << 20 }); },
  free(w, { id }) { w.reply(id, { freed: true }); },
};

function pool(opts = {}, behavior = {}) {
  const Worker = workerClass(behavior);
  const p = new ScienceWorkerPool({ size: 1, workerUrl: "https://science.impf.ai/js/scientific/engines/science.worker.js", Worker, boot: BOOT, siteUrl: "https://science.impf.ai/", indexUrl: "/pyodide/", interruptGraceMs: 30, ...opts });
  return { p, Worker };
}

const tick = (ms = 5) => new Promise((r) => setTimeout(r, ms));

function isolated(fn) {
  return async () => {
    globalThis.crossOriginIsolated = true;
    try { await fn(); } finally { delete globalThis.crossOriginIsolated; }
  };
}

test("recommendedPoolSize: one spare core and 1.5 GB per worker, between 1 and 4, assumptions said", () => {
  assert.equal(recommendedPoolSize({ cores: 8, memoryGb: 8 }).size, MAX_POOL_SIZE);
  assert.equal(recommendedPoolSize({ cores: 16, memoryGb: 4 }).size, 2, "memory bounds it: floor(4 / 1.5)");
  assert.equal(recommendedPoolSize({ cores: 3, memoryGb: 8 }).size, 2, "cores bound it: one stays with the page");
  assert.equal(recommendedPoolSize({ cores: 1, memoryGb: 0.5 }).size, 1, "never fewer than one");
  const unknown = recommendedPoolSize({});
  assert.equal(unknown.size, 1, "2 cores and 2 GB assumed");
  assert.match(unknown.reason, /not reported, 2 GB assumed/);
  assert.match(unknown.reason, /core count is not reported/);
  assert.equal(unknown.assumed.length, 2);
  assert.match(recommendedPoolSize({ cores: 8, memoryGb: 8, isolated: true }).reason, /interrupts Python in place/);
  assert.match(recommendedPoolSize({ cores: 8, memoryGb: 8, isolated: false }).reason, /terminates the worker/);
  assert.equal(recommendedPoolSize({ cores: 8, memoryGb: 8 }).interruptible, false);
});

test("a lease boots a worker lazily (init with boot, siteUrl, indexUrl), loads missing packages, and is reused", async () => {
  const { p, Worker } = pool({ size: 2 });
  assert.equal(Worker.instances.length, 0, "nothing starts before a lease");
  assert.equal(p.stats().live, 0);
  const progress = [];
  const lease = await p.lease({ packages: ["pyarrow"], onEvent: (e) => progress.push(e) });
  assert.equal(Worker.instances.length, 1);
  const w = Worker.instances[0];
  assert.deepEqual(w.ops(), ["init", "packages"]);
  assert.deepEqual(w.received[0].payload, { boot: BOOT, siteUrl: "https://science.impf.ai/", indexUrl: "/pyodide/" });
  assert.deepEqual(w.received[1].payload, { names: ["pyarrow"] }, "only the packages the worker lacks");
  assert.equal(w.opts.type, "module");
  assert.deepEqual(progress, [{ kind: "progress", stage: "bundle", fraction: 0.4, message: "bundle" }]);
  assert.equal(lease.id, "science-1");
  assert.equal(lease.info.runtime, INFO.runtime);
  assert.deepEqual(lease.packages, ["packaging", "pyarrow", "pyyaml"]);
  assert.ok(Number.isFinite(lease.boot_ms));
  let s = p.stats();
  assert.equal(s.live, 1);
  assert.equal(s.busy, 1);
  assert.equal(s.workers[0].state, "busy");
  assert.equal(s.workers[1].state, "empty");

  const events = [];
  const result = await lease.call("run", { fn: "plan_snapshots", args: {} }, { onEvent: (e) => events.push(e) });
  assert.deepEqual(result, { ok: true, result: { fn: "plan_snapshots" } });
  assert.deepEqual(events.map((e) => e.kind), ["progress", "log"]);
  assert.equal(lease.stats().calls, 1);
  lease.release();
  assert.equal(p.stats().workers[0].state, "idle");
  assert.equal(p.stats().workers[0].calls, 1);

  // the next lease takes the running worker that has the packages: no new boot, no package load
  const again = await p.lease({ packages: ["pyarrow"] });
  assert.equal(again.id, "science-1");
  assert.equal(Worker.instances.length, 1);
  assert.deepEqual(w.ops(), ["init", "packages", "run"]);
  assert.equal(again.boot_ms, null, "it did not boot for this lease");
  // a second, concurrent lease boots the second slot
  const other = await p.lease();
  assert.equal(other.id, "science-2");
  assert.equal(Worker.instances.length, 2);
  assert.deepEqual(Worker.instances[1].ops(), ["init"]);
  again.release();
  other.release();
  await assert.rejects(other.call("run", { fn: "x" }), { type: "released" });
});

test("leases wait for a free worker, first come first served; a wait can be cancelled", async () => {
  const { p } = pool({ size: 1 });
  const first = await p.lease();
  let second = null;
  const waiting = p.lease().then((l) => { second = l; return l; });
  const ac = new AbortController();
  const third = p.lease({ signal: ac.signal });
  await tick(10);
  assert.equal(second, null, "the only worker is leased");
  assert.equal(p.stats().waiting, 2);
  ac.abort();
  await assert.rejects(third, (err) => isAbort(err) && err.type === "cancelled");
  first.release();
  const l2 = await waiting;
  assert.equal(l2.id, "science-1");
  assert.equal(p.stats().waiting, 0);
  l2.release();
});

test("a package the Pyodide lock lacks: the lease is refused as unavailable and the worker stays for the next one", async () => {
  const { p, Worker } = pool();
  await assert.rejects(p.lease({ packages: ["nosuchpkg"] }), { type: "unavailable", message: /no package nosuchpkg/ });
  assert.equal(p.stats().workers[0].state, "idle", "a refused package does not cost the booted worker");
  const lease = await p.lease();
  assert.equal(Worker.instances.length, 1);
  lease.release();
});

test("a worker that cannot start: the lease rejects with its error type, and the next lease tries a new one", async () => {
  let fail = true;
  const { p, Worker } = pool({}, {
    init(w, { id }) {
      if (fail) { fail = false; return w.fail(id, "bundle_hash", "the bundle's SHA-256 does not match boot.json"); }
      w.reply(id, { ...INFO });
    },
  });
  await assert.rejects(p.lease(), { type: "bundle_hash" });
  assert.equal(Worker.instances[0].terminated, true, "a worker that did not finish init is not kept");
  assert.equal(p.stats().workers[0].state, "empty");
  const lease = await p.lease();
  assert.equal(Worker.instances.length, 2);
  lease.release();
  // boot.json from a function, and a boot that times out
  const slow = pool({ boot: async () => BOOT, bootTimeoutMs: 20 }, { init() {} });
  await assert.rejects(slow.p.lease(), { type: "timeout" });
  assert.equal(slow.Worker.instances[0].terminated, true);
});

test("a call that times out rejects as timeout and its worker is replaced at the next lease", async () => {
  const { p, Worker } = pool({}, { run() {} });
  const lease = await p.lease();
  await assert.rejects(lease.call("run", { fn: "stuck" }, { timeoutMs: 20 }), { type: "timeout" });
  assert.equal(Worker.instances[0].terminated, true, "it may still be inside Python");
  assert.equal(lease.alive, false);
  assert.equal(p.stats().workers[0].state, "dead");
  await assert.rejects(lease.call("free", {}), { type: "fatal" }, "a dead worker answers nothing");
  lease.release();
  assert.equal(p.stats().workers[0].state, "empty");
  const next = await p.lease();
  assert.equal(Worker.instances.length, 2, "booted again only now");
  next.release();
});

test("not isolated: a cancel terminates the worker at once; the call rejects as an AbortError", async () => {
  const { p, Worker } = pool({}, { run() {} });
  const lease = await p.lease();
  const ac = new AbortController();
  const call = lease.call("run", { fn: "slow" }, { signal: ac.signal });
  await tick(5);
  ac.abort();
  await assert.rejects(call, (err) => isAbort(err) && err.type === "cancelled");
  assert.equal(Worker.instances[0].terminated, true);
  assert.equal(Worker.instances[0].buffer, null, "no interrupt buffer without isolation");
  lease.release();
  // interrupt() does the same for the call in progress
  const l2 = await p.lease();
  const c2 = l2.call("run", { fn: "slow" });
  await tick(5);
  assert.equal(l2.interrupt(), true);
  await assert.rejects(c2, (err) => isAbort(err));
  assert.equal(Worker.instances[1].terminated, true);
  assert.equal(l2.interrupt(), false, "nothing left to interrupt");
  l2.release();
  // a signal that is already aborted never reaches the worker
  const l3 = await p.lease();
  const pre = new AbortController();
  pre.abort();
  await assert.rejects(l3.call("run", { fn: "x" }, { signal: pre.signal }), (err) => isAbort(err));
  assert.equal(Worker.instances[2].ops().includes("run"), false);
  l3.release();
});

test("isolated: a cancel writes SIGINT to the interrupt buffer; Python stops and the worker is kept", isolated(async () => {
  const { p, Worker } = pool({}, {
    run(w, { id }) {
      const poll = setInterval(() => {
        if (w.buffer[0] === 2) {
          clearInterval(poll);
          w.buffer[0] = 0; // Pyodide resets it when it raises KeyboardInterrupt
          w.reply(id, { ok: false, error: { type: "cancelled", message: "cancelled" } });
        }
      }, 2);
    },
  });
  const lease = await p.lease();
  assert.ok(Worker.instances[0].buffer instanceof Uint8Array, "the 1-byte interrupt buffer was sent");
  const ac = new AbortController();
  const call = lease.call("run", { fn: "slow" }, { signal: ac.signal });
  await tick(5);
  ac.abort();
  await assert.rejects(call, (err) => isAbort(err) && err.type === "cancelled");
  assert.equal(Worker.instances[0].terminated, false, "interrupted in place");
  assert.equal(lease.alive, true);
  assert.deepEqual(await lease.call("memory", {}), { wasm_bytes: 64 << 20 }, "the same worker answers the next call");
  lease.release();
}));

test("isolated, but Python does not stop: the worker is terminated after the grace period", isolated(async () => {
  const { p, Worker } = pool({ interruptGraceMs: 20 }, { run() {} });
  const lease = await p.lease();
  const ac = new AbortController();
  const call = lease.call("run", { fn: "stuck" }, { signal: ac.signal });
  await tick(5);
  ac.abort();
  assert.equal(Worker.instances[0].buffer[0], 2, "SIGINT first");
  assert.equal(Worker.instances[0].terminated, false);
  await assert.rejects(call, (err) => isAbort(err));
  assert.equal(Worker.instances[0].terminated, true, "then terminated");
  lease.release();
  const next = await p.lease();
  assert.equal(Worker.instances.length, 2);
  next.release();
}));

test("a worker error rejects the pending call as fatal; the slot boots a new worker at the next lease", async () => {
  const { p, Worker } = pool({}, { run(w) { w.crash("out of memory"); } });
  const lease = await p.lease();
  await assert.rejects(lease.call("run", { fn: "big" }), { type: "fatal", message: /out of memory/ });
  assert.equal(Worker.instances[0].terminated, true);
  assert.equal(p.stats().workers[0].state, "dead");
  lease.release();
  const next = await p.lease();
  assert.equal(Worker.instances.length, 2);
  assert.equal(p.stats().workers[0].instances, 2);
  next.release();
  // a Pyodide fatal error in a reply ends the worker too
  const f = pool({}, { run(w, { id }) { w.fail(id, "fatal", "Pyodide already fatally failed and can no longer be used.", { fatal: true }); } });
  const l = await f.p.lease();
  await assert.rejects(l.call("run", { fn: "x" }), { type: "fatal" });
  assert.equal(f.Worker.instances[0].terminated, true);
  l.release();
  // an ordinary error is the call's, not the worker's
  const e = pool({}, { run(w, { id }) { w.fail(id, "storage", "disk full"); } });
  const l3 = await e.p.lease();
  await assert.rejects(l3.call("run", { fn: "x" }), { type: "storage", message: "disk full" });
  assert.equal(l3.alive, true);
  l3.release();
});

test("release during a call returns the worker when the call ends; one call at a time per lease", async () => {
  let answer;
  const { p } = pool({}, { run(w, { id }) { answer = () => w.reply(id, { ok: true }); } });
  const lease = await p.lease();
  const call = lease.call("run", { fn: "x" });
  await assert.rejects(lease.call("free", {}), { type: "busy" });
  lease.release();
  await tick(5);
  assert.equal(p.stats().workers[0].state, "busy", "still running its call");
  answer();
  assert.deepEqual(await call, { ok: true });
  assert.equal(p.stats().workers[0].state, "idle");
});

test("terminate(id) revokes the lease; terminateAll turns away whoever waits", async () => {
  const { p, Worker } = pool();
  const lease = await p.lease();
  assert.equal(p.terminate("science-1"), true);
  assert.equal(Worker.instances[0].terminated, true);
  await assert.rejects(lease.call("run", { fn: "x" }), { type: "terminated" });
  const fresh = await p.lease();
  assert.equal(Worker.instances.length, 2);
  lease.release(); // a no-op: the slot is fresh's now
  assert.equal(p.stats().workers[0].state, "busy");
  const waiting = p.lease();
  p.terminateAll();
  await assert.rejects(waiting, { type: "terminated" });
  assert.equal(Worker.instances[1].terminated, true);
  assert.equal(p.stats().live, 0);
  assert.equal(p.terminate("nope"), false);
  fresh.release();
});

test("a worker terminated while it starts: the lease waiting behind it gets a new worker, not a dead one", async () => {
  let first = true;
  const { p, Worker } = pool({}, {
    init(w, { id }) {
      if (first) { first = false; return; } // the first worker never finishes starting
      w.reply(id, { ...INFO });
    },
  });
  const booting = p.lease();
  await tick(5);
  const waiting = p.lease();
  p.terminate("science-1");
  await assert.rejects(booting, { type: "terminated" });
  const lease = await waiting;
  assert.equal(lease.alive, true, "the failed boot did not end the worker started after it");
  assert.equal(Worker.instances.length, 2);
  assert.equal(Worker.instances[1].terminated, false);
  assert.deepEqual(await lease.call("memory", {}), { wasm_bytes: 64 << 20 });
  lease.release();
});

test("a lease's terminate() ends its worker; a page without Worker is told so", async () => {
  const { p, Worker } = pool();
  const lease = await p.lease();
  lease.terminate();
  assert.equal(Worker.instances[0].terminated, true);
  assert.equal(p.stats().workers[0].state, "empty");
  const none = new ScienceWorkerPool({ size: 2, Worker: undefined, boot: BOOT });
  assert.equal(none.available, false);
  await assert.rejects(none.lease(), { type: "unavailable" });
});
