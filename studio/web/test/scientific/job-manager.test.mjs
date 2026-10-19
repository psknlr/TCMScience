// BrowserJobService (js/scientific/runtime/job-manager.js) over fake-indexeddb, an in-memory stand-in for the OPFS
// store, scripted Web Locks and a ScienceWorkerPool of scripted workers: a job submitted, run and kept with its
// receipt; failures and refusals; cancelling queued and running jobs; jobs left running by a closed page marked
// interrupted; resume reusing the stages whose checkpoints verify; the queue limit; the order of events.
import "../fixtures/setup.mjs";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { test } from "node:test";
import { IDBFactory } from "fake-indexeddb";
import { isAbort } from "../../js/core/util.js";
import { BrowserJobService, CHECKPOINT_SCHEMA, JOBS_DB, LOG_KEEP, envelopeFor, jobDir, lockName } from "../../js/scientific/runtime/job-manager.js";
import { ScienceWorkerPool } from "../../js/scientific/runtime/worker-pool.js";

const enc = new TextEncoder();
const hex = (bytes) => createHash("sha256").update(bytes).digest("hex");
const BOOT = { schema: "tcmstudio.boot/1", pyodide: { version: "314.0.7", index_url: "https://cdn.example/pyodide/" }, bundle: { path: "b.tar.gz", sha256: "a".repeat(64) } };
const INFO = { runtime: "pyodide-314.0.7 / CPython 3.14.2", platform: "emscripten", machine: "wasm32", packages: ["pyyaml"] };

// ------------------------------------------------------------------------------------------------------- fakes

/** The OpfsStore calls the service makes, over a Map of path → bytes (no other implementation is assumed). */
class MemoryStore {
  base = ["tcmstudio"];
  files = new Map();
  key(path) { return path.join("/"); }
  async stat(path) {
    const k = this.key(path);
    if (this.files.has(k)) return { kind: "file", bytes: this.files.get(k).length, lastModified: 0 };
    return [...this.files.keys()].some((f) => f.startsWith(`${k}/`)) ? { kind: "directory", bytes: null, lastModified: null } : null;
  }
  async exists(path) { return (await this.stat(path)) !== null; }
  async readFile(path) {
    const b = this.files.get(this.key(path));
    return b ? new File([b], path[path.length - 1]) : null;
  }
  async readText(path) { const f = await this.readFile(path); return f ? f.text() : null; }
  async readJson(path) { const t = await this.readText(path); return t === null ? null : JSON.parse(t); }
  async writeBytes(path, bytes) { this.files.set(this.key(path), new Uint8Array(bytes)); return bytes.length; }
  async writeText(path, text) { return this.writeBytes(path, enc.encode(String(text))); }
  async writeJson(path, value) { return this.writeText(path, `${JSON.stringify(value, null, 2)}\n`); }
  async list(path) {
    const k = `${this.key(path)}/`;
    const names = new Map();
    for (const f of this.files.keys()) if (f.startsWith(k)) { const rest = f.slice(k.length).split("/"); names.set(rest[0], rest.length > 1 ? "directory" : "file"); }
    return [...names].map(([name, kind]) => ({ name, kind })).sort((a, b) => a.name.localeCompare(b.name));
  }
  async remove(path, { recursive = false } = {}) {
    const k = this.key(path);
    let any = this.files.delete(k);
    for (const f of [...this.files.keys()]) if (f.startsWith(`${k}/`)) { if (!recursive) throw new Error("not empty"); this.files.delete(f); any = true; }
    return any;
  }
  bytesAt(path) { return this.files.get(this.key(path)); }
}

/** Web Locks for several "tabs" of one origin: a shared registry of held names. */
function locksFor(registry, client) {
  return {
    request(name, opts, cb) {
      if (registry.has(name)) return Promise.resolve(cb(null));
      registry.set(name, client);
      return Promise.resolve(cb({ name, mode: "exclusive" })).finally(() => registry.delete(name));
    },
    async query() {
      return { held: [...registry].map(([name, clientId]) => ({ name, clientId, mode: "exclusive" })), pending: [] };
    },
  };
}

/** Scripted job workers: "run" answers what the test's `run` callback returns (or never, for "hang"). */
function workerClass(run) {
  const instances = [];
  class FakeWorker {
    constructor() { this.terminated = false; this.received = []; instances.push(this); }
    postMessage(data) {
      if (data?.type === "interrupt-buffer") this.buffer = new Uint8Array(data.buffer);
      if (this.terminated || typeof data !== "string") return;
      const msg = JSON.parse(data);
      this.received.push(msg);
      setTimeout(() => this.#answer(msg), 0);
    }
    async #answer({ id, op, payload }) {
      const reply = (result) => this.send({ id, ok: true, ms: 1, result });
      if (op === "init") return reply(INFO);
      if (op === "packages") return reply({ packages: [...INFO.packages, ...payload.names] });
      if (op === "memory") return reply({ wasm_bytes: 96 << 20 });
      if (op === "free") return reply({});
      if (op === "run") {
        this.send({ type: "event", id, event: { kind: "log", level: "info", text: `python: ${payload.fn}` } });
        const out = await run(payload, this);
        if (out === "hang") return undefined;
        return reply(out);
      }
      return this.send({ id, ok: false, ms: 1, error: { type: "bad_request", message: op } });
    }
    send(obj) { setTimeout(() => { if (!this.terminated) this.onmessage?.({ data: JSON.stringify(obj) }); }, 0); }
    terminate() { this.terminated = true; }
  }
  FakeWorker.instances = instances;
  return FakeWorker;
}

function setup({ run = async (p) => ({ ok: true, result: p.fn }), idb = new IDBFactory(), registry = new Map(), client = "tab-1", store = new MemoryStore(), handlers, maxConcurrent = 1, size = 2, ...rest } = {}) {
  const Worker = workerClass(run);
  const pool = new ScienceWorkerPool({ size, Worker, workerUrl: "w.js", boot: BOOT, interruptGraceMs: 20 });
  let t = Date.parse("2026-10-19T10:00:00Z");
  const now = () => new Date((t += 250));
  const hashFile = async (path) => { const b = store.bytesAt(path); return b ? hex(b) : null; };
  const svc = new BrowserJobService({
    indexedDB: idb, locks: locksFor(registry, client), pool, store, handlers: handlers || [computeHandler()], now, maxConcurrent, hashFile,
    navigator: { userAgent: "test", hardwareConcurrency: 4, deviceMemory: 8 }, ...rest,
  });
  return { svc, pool, Worker, store, idb, registry };
}

/** Write a file the way a job worker's persist does, and return its artefact record. */
async function written(ctx, name, text) {
  const path = ["out", name];
  const bytes = enc.encode(text);
  await ctx.store.writeBytes([...ctx.dir, ...path], bytes);
  return { name, path, bytes: bytes.length, sha256: hex(bytes) };
}

/** compute (a worker runs Python, writes table.tsv) → verify (says it verified). */
function computeHandler({ kind = "test.compute", gate = null, fail = null, preflight } = {}) {
  return {
    kind,
    title: { zh: "测试", en: "Test" },
    stages: [{ id: "compute", title: { zh: "计算", en: "Compute" } }, { id: "verify", title: { zh: "核对", en: "Verify" } }],
    preflight,
    async run(ctx) {
      const lease = await ctx.pool.lease({ packages: ["pyarrow"] });
      try {
        await ctx.stage("compute", async () => {
          const out = await lease.call("run", { fn: "compute", args: ctx.params }, { onEvent: (ev) => ev.kind === "log" && ctx.log("info", ev.text) });
          ctx.progress(0.5, "computed");
          if (gate) await gate(ctx);
          ctx.artefact(await written(ctx, "table.tsv", `x\t${out.result}\n`));
          return { worker: lease.id, detail: "1 table" };
        });
        await ctx.stage("verify", async () => {
          if (fail) throw fail;
          ctx.outcome({ checks: ["recomputed"] });
          return { worker: lease.id, verified: true };
        });
      } finally {
        lease.release();
      }
      return { result: { rows: 1 } };
    },
  };
}

async function settle(svc, id, ms = 3000) {
  const t0 = Date.now();
  for (;;) {
    const job = await svc.get(id);
    if (["succeeded", "failed", "cancelled", "interrupted"].includes(job?.state)) return job;
    if (Date.now() - t0 > ms) throw new Error(`job ${id} did not end: ${job?.state}`);
    await new Promise((r) => setTimeout(r, 5));
  }
}

function deferred() {
  let resolve;
  const promise = new Promise((r) => { resolve = r; });
  return { promise, resolve };
}

// ------------------------------------------------------------------------------------------------------- tests

test("submit → run → succeeded: the record kept in IndexedDB, the receipt written and its digest recorded", async () => {
  const { svc, store, idb, Worker } = setup();
  assert.equal(await svc.start(), "ready");
  assert.deepEqual(svc.kinds().map((k) => [k.kind, k.stages.map((s) => s.id)]), [["test.compute", ["compute", "verify"]]]);
  const job = await svc.submit("test.compute", { formula: "葛根芩连汤" }, { project_id: "p1" });
  assert.match(job.id, /^bj-[0-9a-f]{16}$/);
  assert.equal(job.state, "queued");
  assert.equal(job.where, "browser");
  assert.equal(job.attempts, 1);
  assert.deepEqual(job.stages.map((s) => s.status), ["pending", "pending"]);
  const done = await settle(svc, job.id);
  assert.equal(done.state, "succeeded", JSON.stringify(done.error));
  assert.deepEqual(done.result, { rows: 1 });
  assert.equal(done.progress, null);
  assert.ok(done.started_at && done.finished_at);
  assert.deepEqual(done.stages.map((s) => [s.id, s.status, s.worker, s.reused]), [["compute", "succeeded", "science-1", false], ["verify", "succeeded", "science-1", false]]);
  assert.ok(done.stages.every((s) => Number.isFinite(s.seconds) && s.started_at && s.finished_at));
  assert.deepEqual(done.outcome.checks, ["recomputed", "worker_digests", "page_digests"]);
  assert.deepEqual(done.artefacts.map((a) => a.name), ["table.tsv"]);
  assert.ok(done.logs.some((l) => l.text === "python: compute"), "the worker's log events reach the job's log");
  assert.equal(Worker.instances.length, 1);

  // the receipt: its digest is of exactly the bytes in storage
  const bytes = store.bytesAt([...jobDir(job.id), "out", "job_receipt.json"]);
  assert.equal(done.receipt.path, "job_receipt.json");
  assert.equal(done.receipt.sha256, hex(bytes));
  const r = await svc.receipt(job.id);
  assert.equal(r.matches, true);
  assert.equal(r.doc.schema, "tcmstudio.job-receipt/1");
  assert.equal(r.doc.placement, "browser");
  assert.equal(r.doc.runtime, INFO.runtime);
  assert.equal(r.doc.platform.python, "emscripten/wasm32");
  assert.equal(r.doc.device.evidence, "verified");
  assert.equal(r.doc.assurance, "outputs_verified");
  assert.deepEqual(r.doc.workers.map((w) => [w.id, w.stages]), [["science-1", ["compute", "verify"]]]);
  assert.equal(r.doc.workers[0].calls, 1, "the service's memory reading is not counted as the job's work");
  assert.equal(r.doc.resources.memory.wasm_peak_bytes, 96 << 20);
  assert.match(r.doc.resources.memory.measured_by, /only grows/);
  assert.equal(r.doc.digests.page["table.tsv"], r.doc.digests.worker["table.tsv"]);
  assert.equal(r.doc.params_sha256.length, 64);
  // the receipt changed after the fact: said so
  store.files.set(store.key([...jobDir(job.id), "out", "job_receipt.json"]), enc.encode("{}"));
  assert.equal((await svc.receipt(job.id)).matches, false);

  // files and reading them back
  const files = await svc.files(job.id);
  assert.deepEqual(files.map((f) => [f.name, f.path]), [["table.tsv", ["out", "table.tsv"]]]);
  assert.equal(await (await svc.readFile(job.id, ["out", "table.tsv"])).text(), "x\tcompute\n");
  assert.equal(await (await svc.readFile(job.id, "table.tsv")).text(), "x\tcompute\n", "by its name");
  await assert.rejects(svc.readFile(job.id, ["tcmstudio", "datasets", "x"]), { type: "bad_arguments" });
  await assert.rejects(svc.readFile(job.id, ["out", "nope"]), { type: "not_found" });

  // another tab (a new service over the same database) reads the same record
  const other = setup({ idb, registry: new Map(), client: "tab-2", store }).svc;
  const kept = await other.get(job.id);
  assert.equal(kept.state, "succeeded");
  assert.deepEqual(kept.receipt, done.receipt);
  assert.deepEqual((await other.list({ project_id: "p1" })).map((j) => j.id), [job.id]);
  assert.deepEqual(await other.list({ project_id: "p2" }), []);
});

test("events: state (running), stages, progress, log, artefact … state (succeeded), done — in order", async () => {
  const { svc } = setup();
  const job = await svc.submit("test.compute", {});
  const events = [];
  const off = svc.events(job.id, (ev) => events.push(ev));
  await settle(svc, job.id);
  await new Promise((r) => setTimeout(r, 5));
  off();
  const types = events.map((e) => e.type);
  assert.equal(types[0], "state");
  assert.equal(events[0].job.state, "running");
  assert.deepEqual(types.slice(-2), ["state", "done"]);
  assert.equal(events.at(-1).job.state, "succeeded");
  const order = (t) => types.indexOf(t);
  assert.ok(order("stages") > 0 && order("stages") < order("progress"), types.join(" "));
  assert.ok(order("progress") < order("artefact"));
  assert.ok(types.includes("log"));
  assert.ok(!types.includes("error"));
  assert.deepEqual(events.find((e) => e.type === "artefact").data.path, ["out", "table.tsv"]);
  assert.equal(events.find((e) => e.type === "progress").data.stage, "compute");
  // each event's job is a copy: changing it changes nothing
  events[0].job.state = "mangled";
  assert.equal((await svc.get(job.id)).state, "succeeded");
});

test("a handler that throws: failed with its error type and message; the receipt says the work ran, unverified", async () => {
  const fail = Object.assign(new Error("the exports did not recompute"), { type: "failed", hint: "see verify.json" });
  const { svc } = setup({ handlers: [computeHandler({ fail })] });
  const job = await svc.submit("test.compute", {});
  const events = [];
  svc.events(job.id, (ev) => events.push(ev.type));
  const done = await settle(svc, job.id);
  assert.equal(done.state, "failed");
  assert.deepEqual(done.error, { type: "failed", message: "the exports did not recompute", hint: "see verify.json" });
  assert.equal(done.result, null);
  assert.deepEqual(done.stages.map((s) => s.status), ["succeeded", "failed"]);
  assert.equal(done.stages[1].detail, "the exports did not recompute");
  assert.deepEqual(done.outcome.checks, [], "a failed job claims no checks");
  await new Promise((r) => setTimeout(r, 5));
  assert.deepEqual(events.slice(-3), ["state", "error", "done"]);
  const r = await svc.receipt(done.id);
  assert.equal(r.matches, true);
  assert.equal(r.doc.state, "failed");
  assert.equal(r.doc.device.evidence, "dispatched");
  assert.equal(r.doc.assurance, "none");
  assert.equal(r.doc.error, "the exports did not recompute");
});

test("a digest that does not survive the trip to storage fails the job (integrity), however the handler ended", async () => {
  const handler = {
    kind: "test.lie", title: { zh: "x", en: "x" }, stages: [{ id: "write", title: { zh: "x", en: "x" } }],
    async run(ctx) {
      await ctx.stage("write", async () => {
        const a = await written(ctx, "out.tsv", "real bytes\n");
        ctx.artefact({ ...a, sha256: "f".repeat(64) }); // what the worker said it wrote
      });
      return { result: { ok: true } };
    },
    expected: ["out.tsv", "report.html"],
  };
  const { svc } = setup({ handlers: [handler] });
  const job = await svc.submit("test.lie", {});
  const done = await settle(svc, job.id);
  assert.equal(done.state, "failed");
  assert.equal(done.error.type, "integrity");
  assert.match(done.error.message, /out\.tsv: read back from storage its SHA-256 is .*, the job worker recorded ffffffff/);
  assert.match(done.error.message, /report\.html is missing/);
  assert.deepEqual(done.outcome.missing, ["report.html"]);
  const r = await svc.receipt(job.id);
  assert.deepEqual(r.doc.digests.mismatched, ["out.tsv"]);
  assert.equal(r.doc.device.evidence, "selected", "no worker ran: nothing computed on the CPU");
});

test("a kind's preflight refusal throws with type, need and hint, and nothing is queued", async () => {
  const preflight = async (params) => {
    if (!params.ok) throw Object.assign(new Error("No research snapshots in this browser yet"), { type: "unavailable", need: "dataset", hint: "Build them first" });
  };
  const { svc } = setup({ handlers: [computeHandler({ preflight })] });
  await assert.rejects(svc.submit("test.compute", {}), { type: "unavailable", need: "dataset", hint: "Build them first" });
  assert.deepEqual(await svc.list(), []);
  await assert.rejects(svc.submit("test.unknown", {}), { type: "unavailable", need: "job_kind" });
  await assert.rejects(svc.submit("test.compute", "x"), { type: "bad_arguments" });
  await assert.rejects(svc.submit("test.compute", { ok: true }, { submission_id: "bad id!" }), { type: "bad_arguments" });
  // the same submission id gives back the job it made
  const a = await svc.submit("test.compute", { ok: true }, { submission_id: "turn-1:call-2" });
  const b = await svc.submit("test.compute", { ok: true }, { submission_id: "turn-1:call-2" });
  assert.equal(a.id, b.id);
  await settle(svc, a.id);
  assert.equal((await svc.list()).length, 1);
});

test("no IndexedDB, or no job workers: the service is unsupported and says why", async () => {
  const none = new BrowserJobService({ indexedDB: null, pool: {} });
  const seen = [];
  none.onStatus((s) => seen.push(s.status));
  assert.equal(await none.start(), "unsupported");
  assert.match(none.reason, /no IndexedDB/);
  assert.deepEqual(seen, ["unsupported"]);
  await assert.rejects(none.submit("test.compute", {}), { type: "unavailable", need: "job_service" });
  assert.equal(await none.get("bj-0000000000000000"), null);
  const noWorkers = new BrowserJobService({ indexedDB: new IDBFactory(), pool: new ScienceWorkerPool({ Worker: undefined, boot: BOOT }) });
  assert.equal(await noWorkers.start(), "unsupported");
  assert.match(noWorkers.reason, /no Web Worker/);
});

test("cancel a queued job: cancelled at once, never run; cancel a running one: its signal aborts and its worker stops", async () => {
  const gate = deferred();
  let sawAbort = false;
  const { svc, Worker } = setup({
    run: async (p) => (p.fn === "compute" && p.args.hang ? "hang" : { ok: true, result: p.fn }),
    handlers: [computeHandler({ gate: async (ctx) => { if (ctx.params.wait) await gate.promise; } })],
  });
  const first = await svc.submit("test.compute", { wait: true });
  const second = await svc.submit("test.compute", {});
  await new Promise((r) => setTimeout(r, 30));
  assert.equal((await svc.get(first.id)).state, "running");
  assert.equal((await svc.get(second.id)).state, "queued", "maxConcurrent 1");
  const c = await svc.cancel(second.id);
  assert.equal(c.state, "cancelled");
  assert.equal(c.receipt, null, "it never started: no receipt");
  gate.resolve();
  assert.equal((await settle(svc, first.id)).state, "succeeded");
  assert.equal((await svc.get(second.id)).state, "cancelled", "it never ran");
  await assert.rejects(svc.cancel(first.id), { type: "conflict" }, "a finished job has nothing to cancel");

  // running: Python never answers (not isolated) → the worker is terminated, the job ends cancelled
  const handler = {
    kind: "test.hang", title: { zh: "x", en: "x" }, stages: [{ id: "work", title: { zh: "x", en: "x" } }],
    async run(ctx) {
      ctx.signal.addEventListener("abort", () => { sawAbort = true; });
      const lease = await ctx.pool.lease();
      try {
        await ctx.stage("work", async () => lease.call("run", { fn: "compute", args: { hang: true } }));
      } finally {
        lease.release();
      }
      return { result: null };
    },
  };
  const h = setup({ run: async () => "hang", handlers: [handler] });
  const job = await h.svc.submit("test.hang", {});
  await new Promise((r) => setTimeout(r, 30));
  assert.equal((await h.svc.get(job.id)).stages[0].status, "running");
  const view = await h.svc.cancel(job.id);
  assert.equal(view.state, "running", "it ends once the handler has stopped");
  const done = await settle(h.svc, job.id);
  assert.equal(done.state, "cancelled");
  assert.equal(sawAbort, true);
  assert.equal(h.Worker.instances[0].terminated, true, "the lease's worker was stopped");
  assert.equal(done.stages[0].status, "cancelled");
  assert.equal(done.error, null);
  assert.equal(h.registry.size, 0, "its Web Lock is released");
  const r = await h.svc.receipt(job.id);
  assert.equal(r.doc.state, "cancelled");
  assert.equal(r.doc.device.evidence, "dispatched");
  assert.equal(Worker.instances.length, 1);
});

test("a handler that ignores the cancel is ended as cancelled after the grace period", async () => {
  const never = deferred();
  const handler = {
    kind: "test.stubborn", title: { zh: "x", en: "x" }, stages: [{ id: "spin", title: { zh: "x", en: "x" } }],
    async run(ctx) {
      await ctx.stage("spin", () => never.promise);
      return { result: "late" };
    },
  };
  const { svc } = setup({ handlers: [handler], cancelGraceMs: 30 });
  const job = await svc.submit("test.stubborn", {});
  await new Promise((r) => setTimeout(r, 20));
  await svc.cancel(job.id);
  const done = await settle(svc, job.id);
  assert.equal(done.state, "cancelled");
  assert.ok(done.logs.some((l) => /did not stop within/.test(l.text)));
  never.resolve({ worker: "science-9" });
  await new Promise((r) => setTimeout(r, 20));
  const after = await svc.get(job.id);
  assert.equal(after.state, "cancelled");
  assert.equal(after.stages[0].status, "cancelled", "a stage that finishes after its job ended changes nothing");
});

test("start(): a job left queued or running without a live tab's lock is interrupted; another tab's is left alone", async () => {
  const idb = new IDBFactory();
  const registry = new Map();
  const base = { kind: "test.compute", where: "browser", params: {}, project_id: null, submission_id: null, title: { zh: "x", en: "x" }, started_at: null, finished_at: null, progress: null, artefacts: [], device: "cpu", outcome: null, result: null, receipt: null, error: null, logs: [], attempts: 1 };
  const stages = [{ id: "compute", title: { zh: "x", en: "x" }, status: "running", started_at: "2026-10-19T09:00:00.000Z", finished_at: null, seconds: null, worker: "science-1", detail: null, reused: false }];
  const orphan = { ...base, id: "bj-00000000000000a1", state: "running", created_at: "2026-10-19T09:00:00.000Z", stages };
  const queued = { ...base, id: "bj-00000000000000a2", state: "queued", created_at: "2026-10-19T09:00:01.000Z", stages: [] };
  const live = { ...base, id: "bj-00000000000000a3", state: "running", created_at: "2026-10-19T09:00:02.000Z", stages };
  await seed(idb, [orphan, queued, live]);
  registry.set(lockName(live.id), "tab-0"); // a tab that is still open runs it
  const { svc } = setup({ idb, registry, client: "tab-1" });
  await svc.start();
  const a = await svc.get(orphan.id);
  assert.equal(a.state, "interrupted");
  assert.equal(a.error.type, "interrupted");
  assert.match(a.error.message, /closed while the job was running/);
  assert.equal(a.stages[0].status, "cancelled");
  assert.match(a.stages[0].detail, /interrupted/);
  assert.equal(a.progress, null);
  assert.equal((await svc.get(queued.id)).state, "interrupted");
  assert.equal((await svc.get(live.id)).state, "running", "another tab holds its lock");
  await assert.rejects(svc.cancel(live.id), { type: "conflict", message: /another tab/ });
  await assert.rejects(svc.remove(live.id), { type: "conflict" });
  // an interrupted job can be given up
  assert.equal((await svc.cancel(queued.id)).state, "cancelled");
  // listed newest first
  assert.deepEqual((await svc.list()).map((j) => j.id), [live.id, queued.id, orphan.id]);

  // without Web Locks every such job is taken for an orphan, and its log says why nothing could tell
  const idb2 = new IDBFactory();
  await seed(idb2, [{ ...live, logs: [] }]);
  const noLocks = new BrowserJobService({ indexedDB: idb2, locks: null, pool: setup().pool, store: new MemoryStore() });
  assert.equal(await noLocks.start(), "ready");
  const guessed = await noLocks.get(live.id);
  assert.equal(guessed.state, "interrupted");
  assert.equal(guessed.logs.at(-1).level, "warn");
  assert.match(guessed.logs.at(-1).text, /Web Locks are missing here or could not be queried/);
});

/** Records written straight into the database, as an earlier page left them. */
function seed(idb, records) {
  return new Promise((resolve, reject) => {
    const r = idb.open(JOBS_DB, 1);
    r.onupgradeneeded = () => {
      const s = r.result.createObjectStore("jobs", { keyPath: "id" });
      s.createIndex("project_id", "project_id");
      s.createIndex("created_at", "created_at");
    };
    r.onsuccess = () => {
      const tx = r.result.transaction("jobs", "readwrite");
      for (const j of records) tx.objectStore("jobs").put(j);
      tx.oncomplete = () => { r.result.close(); resolve(); };
      tx.onerror = () => reject(tx.error);
    };
    r.onerror = () => reject(r.error);
  });
}

test("resume reuses a stage whose checkpoint verifies and runs again one whose output changed", async () => {
  const ran = [];
  let attempt = 0;
  const handler = {
    kind: "test.resume", title: { zh: "x", en: "x" },
    stages: [{ id: "a", title: { zh: "甲", en: "A" } }, { id: "b", title: { zh: "乙", en: "B" } }, { id: "c", title: { zh: "丙", en: "C" } }],
    async run(ctx) {
      attempt += 1;
      for (const id of ["a", "b"]) {
        await ctx.stage(id, async () => {
          ran.push(`${id}${attempt}`);
          const art = await written(ctx, `${id}.txt`, `${id} from attempt ${attempt}\n`);
          ctx.artefact(art);
          return { checkpoint: { inputs_digest: "in", outputs: [art] } };
        }, { reuse: () => ctx.checkpoint.reusable(id, { inputs_digest: "in" }) });
      }
      await ctx.stage("c", async () => {
        ran.push(`c${attempt}`);
        if (attempt === 1) throw Object.assign(new Error("the browser ran out of memory"), { type: "runtime_error" });
      });
      return { result: { attempt } };
    },
  };
  const { svc, store } = setup({ handlers: [handler] });
  const job = await svc.submit("test.resume", {});
  const failed = await settle(svc, job.id);
  assert.equal(failed.state, "failed");
  const cp = await store.readJson([...jobDir(job.id), "checkpoints", "a.json"]);
  assert.equal(cp.schema, CHECKPOINT_SCHEMA);
  assert.deepEqual(cp.outputs.map((o) => o.path), [["out", "a.txt"]]);
  // b's output is changed behind the job's back (same size, other bytes): its checkpoint no longer verifies
  await store.writeText([...jobDir(job.id), "out", "b.txt"], "b from attempt X\n");
  const resumed = await svc.resume(job.id);
  assert.equal(resumed.state, "queued");
  assert.equal(resumed.attempts, 2);
  assert.equal(resumed.error, null);
  assert.deepEqual(resumed.stages.map((s) => s.status), ["pending", "pending", "pending"]);
  const done = await settle(svc, job.id);
  assert.equal(done.state, "succeeded", JSON.stringify(done.error));
  assert.deepEqual(ran, ["a1", "b1", "c1", "b2", "c2"], "a was reused, b ran again");
  assert.deepEqual(done.stages.map((s) => [s.id, s.status, s.reused]), [["a", "succeeded", true], ["b", "succeeded", false], ["c", "succeeded", false]]);
  assert.match(done.stages[0].detail, /reused/);
  assert.ok(done.logs.some((l) => l.text === "b: its checkpoint does not verify, so it runs again (out/b.txt changed since its checkpoint)"), done.logs.map((l) => l.text).join("\n"));
  assert.ok(!done.logs.some((l) => /^a: /.test(l.text)), "a stage that never ran is not said to run again");
  assert.deepEqual(done.result, { attempt: 2 });
  // a's artefact from attempt 1 is still checked against storage, and agrees
  assert.deepEqual(done.outcome.checks, ["worker_digests", "page_digests"]);
  assert.equal(await (await svc.readFile(job.id, "a.txt")).text(), "a from attempt 1\n");
  // only interrupted or failed jobs resume
  await assert.rejects(svc.resume(job.id), { type: "conflict" });
  await assert.rejects(svc.resume("bj-ffffffffffffffff"), { type: "not_found" });
});

test("maxConcurrent: jobs beyond it wait in order; two may run at once when allowed", async () => {
  const gates = [deferred(), deferred(), deferred()];
  let n = 0;
  const handler = {
    kind: "test.wait", title: { zh: "x", en: "x" }, stages: [],
    async run(ctx) {
      const i = n++;
      await gates[i].promise;
      return { result: i };
    },
  };
  const one = setup({ handlers: [handler], maxConcurrent: 1 });
  const ids = [];
  for (let i = 0; i < 3; i++) ids.push((await one.svc.submit("test.wait", {})).id);
  await new Promise((r) => setTimeout(r, 20));
  assert.deepEqual(await Promise.all(ids.map(async (id) => (await one.svc.get(id)).state)), ["running", "queued", "queued"]);
  gates[0].resolve();
  await settle(one.svc, ids[0]);
  await new Promise((r) => setTimeout(r, 20));
  assert.deepEqual(await Promise.all(ids.map(async (id) => (await one.svc.get(id)).state)), ["succeeded", "running", "queued"]);
  gates[1].resolve();
  gates[2].resolve();
  await settle(one.svc, ids[2]);
  assert.deepEqual((await one.svc.get(ids[2])).result, 2, "first in, first run");

  n = 0;
  const g2 = [deferred(), deferred()];
  const two = setup({ handlers: [{ ...handler, run: async () => { const i = n++; await g2[i].promise; return { result: i }; } }], maxConcurrent: 2 });
  const a = await two.svc.submit("test.wait", {});
  const b = await two.svc.submit("test.wait", {});
  await new Promise((r) => setTimeout(r, 20));
  assert.equal((await two.svc.get(a.id)).state, "running");
  assert.equal((await two.svc.get(b.id)).state, "running");
  g2.forEach((g) => g.resolve());
  await settle(two.svc, b.id);
});

test("logs keep the last 200 lines; remove() deletes the record and the files", async () => {
  const handler = {
    kind: "test.chatty", title: { zh: "x", en: "x" }, stages: [],
    async run(ctx) {
      for (let i = 0; i < 300; i++) ctx.log("info", `line ${i}`);
      ctx.log("loud", "an unknown level is info");
      await ctx.store.writeText([...ctx.dir, "out", "x.txt"], "x");
      return { result: null };
    },
  };
  const { svc, store, idb } = setup({ handlers: [handler] });
  const job = await svc.submit("test.chatty", {});
  const done = await settle(svc, job.id);
  assert.equal(done.logs.length, LOG_KEEP);
  assert.equal(done.logs.at(-1).text, "succeeded");
  assert.equal(done.logs.at(-2).level, "info");
  const kept = await setup({ idb, store }).svc.get(job.id);
  assert.equal(kept.logs.length, LOG_KEEP, "as stored");
  assert.ok(await store.exists([...jobDir(job.id), "out", "x.txt"]));
  assert.equal(await svc.remove(job.id), true);
  assert.equal(await svc.get(job.id), null);
  assert.equal(await store.exists(jobDir(job.id)), false);
  assert.equal(await svc.remove(job.id), false);
});

test("a job that leases a worker and is cancelled while it waits for one stops waiting", async () => {
  const gate = deferred();
  const handler = {
    kind: "test.lease", title: { zh: "x", en: "x" }, stages: [],
    async run(ctx) {
      const lease = await ctx.pool.lease();
      try { if (ctx.params.hold) await gate.promise; } finally { lease.release(); }
      return { result: null };
    },
  };
  const { svc, pool } = setup({ handlers: [handler], maxConcurrent: 2, size: 1 });
  const holder = await svc.submit("test.lease", { hold: true });
  const waiter = await svc.submit("test.lease", {});
  await new Promise((r) => setTimeout(r, 30));
  assert.equal(pool.stats().waiting, 1);
  await svc.cancel(waiter.id);
  const w = await settle(svc, waiter.id);
  assert.equal(w.state, "cancelled");
  assert.equal(pool.stats().waiting, 0);
  gate.resolve();
  assert.equal((await settle(svc, holder.id)).state, "succeeded");
});

test("envelopeFor: the job_submitted envelope a job call returns, with the page's receipt for the browser", async () => {
  const { svc } = setup();
  const job = await svc.submit("test.compute", { formula: "x" }, { project_id: "p" });
  const env = envelopeFor(job, { tool: "call_tool", input_sha256: "e".repeat(64) });
  assert.equal(env.status, "job_submitted");
  assert.equal(env.ok, true);
  assert.equal(env.via, "job.test.compute");
  assert.deepEqual(env.job, { id: job.id, kind: "test.compute", state: "queued", where: "browser" });
  assert.equal(env.receipt.where, "browser");
  assert.equal(env.receipt.runtime, "studio-jobs/1");
  assert.equal(env.receipt.device, "cpu");
  assert.equal(env.receipt.input_sha256, "e".repeat(64));
  assert.equal(env.receipt.decided_by, undefined, "the job service took it: not a page decision");
  assert.deepEqual(env.receipt.result, { kind: null, outcome: "queued", assurance: "none" });
  assert.match(env.text, /^call_tool → job\.test\.compute: job_submitted\n/);
  assert.match(env.text, /job_status \{"job_id": "bj-/);
  assert.deepEqual(env.governance.limitations, ["A job that has not succeeded is pending work, not a result."]);
  assert.equal(env.error, null);
  assert.equal(env.result.id, job.id);
  assert.equal(envelopeFor({ ...job, state: "running" }).receipt.result.outcome, "running");
  const failed = envelopeFor({ ...job, state: "failed", error: { type: "integrity", message: "x changed" } }, { tool: "t" });
  assert.equal(failed.status, "failed");
  assert.equal(failed.ok, false);
  assert.deepEqual(failed.error, { type: "runtime_error", message: "x changed", hint: "" });
  assert.equal(failed.receipt.result.outcome, "failed");
  assert.equal(envelopeFor({ ...job, state: "cancelled" }).status, "cancelled");
  await settle(svc, job.id);
});

test("a job worker that crashes mid-stage fails the job with the worker's fatal error", async () => {
  const { svc, Worker } = setup({ run: async (p, w) => { setTimeout(() => w.onerror?.({ message: "RangeError: out of memory", preventDefault() {} }), 0); return "hang"; } });
  const job = await svc.submit("test.compute", {});
  const done = await settle(svc, job.id);
  assert.equal(done.state, "failed");
  assert.equal(done.error.type, "fatal");
  assert.match(done.error.message, /out of memory/);
  assert.equal(Worker.instances[0].terminated, true);
  assert.ok(!isAbort(done.error));
});

test("a cancel that arrives while the job's end is being recorded changes nothing: the job ends as it ran", async () => {
  const store = new MemoryStore();
  const reading = deferred();
  const go = deferred();
  // the service is reading the artefacts back when the cancel comes
  const hashFile = async (path) => { reading.resolve(); await go.promise; const b = store.bytesAt(path); return b ? hex(b) : null; };
  const { svc } = setup({ store, hashFile, cancelGraceMs: 20 });
  const job = await svc.submit("test.compute", {});
  await reading.promise;
  const cancelling = svc.cancel(job.id);
  go.resolve();
  await assert.rejects(cancelling, { type: "conflict", message: /already finished \(succeeded\)/ });
  await new Promise((r) => setTimeout(r, 50)); // past the cancel grace
  const done = await svc.get(job.id);
  assert.equal(done.state, "succeeded");
  assert.equal((await svc.receipt(job.id)).doc.state, "succeeded");
  assert.ok(!done.logs.some((l) => /cancel|did not stop/.test(l.text)), done.logs.map((l) => l.text).join("\n"));
});

test("a result that cannot be kept as JSON fails its job, and the queue goes on", async () => {
  const circular = { rows: 1 };
  circular.self = circular;
  let n = 0;
  const handler = { kind: "test.loop", title: { zh: "x", en: "x" }, stages: [], async run() { return { result: n++ === 0 ? circular : { rows: 2 } }; } };
  const { svc, registry } = setup({ handlers: [handler] });
  const first = await svc.submit("test.loop", {});
  const second = await svc.submit("test.loop", {});
  const a = await settle(svc, first.id);
  assert.equal(a.state, "failed");
  assert.match(a.error.message, /cannot be kept as JSON/);
  assert.equal(a.result, null);
  assert.equal((await settle(svc, second.id)).state, "succeeded", "the next job still ran");
  assert.equal(registry.size, 0, "both locks released");
});

test("an artefact the page cannot hash when it reads it back fails the job: no digest, no agreement", async () => {
  const { svc } = setup({ hashFile: async () => null });
  const job = await svc.submit("test.compute", {});
  const done = await settle(svc, job.id);
  assert.equal(done.state, "failed");
  assert.equal(done.error.type, "integrity");
  assert.match(done.error.message, /table\.tsv: reading it back from storage gave no SHA-256/);
  assert.deepEqual(done.outcome.checks, []);
  const r = await svc.receipt(job.id);
  assert.deepEqual(r.doc.digests.unread, ["table.tsv"]);
  assert.equal(r.doc.assurance, "none");
});

test("isolated: Python stops in place on a cancel, yet the cancelled job's worker is not handed to the next job", async () => {
  globalThis.crossOriginIsolated = true;
  try {
    const run = (p, w) => new Promise((resolve) => {
      if (p.fn !== "compute" || !p.args.hang) return resolve({ ok: true, result: p.fn });
      const poll = setInterval(() => {
        if (w.buffer?.[0] !== 2) return;
        clearInterval(poll);
        w.buffer[0] = 0; // KeyboardInterrupt raised: Python is back at the top
        resolve({ ok: false, error: { type: "cancelled", message: "KeyboardInterrupt" } });
      }, 2);
    });
    const { svc, Worker } = setup({ run, size: 1 });
    const job = await svc.submit("test.compute", { hang: true });
    await new Promise((r) => setTimeout(r, 30));
    await svc.cancel(job.id);
    assert.equal((await settle(svc, job.id)).state, "cancelled");
    assert.equal(Worker.instances[0].terminated, true, "whatever the job left in the worker's memory goes with it");
    const next = await svc.submit("test.compute", {});
    assert.equal((await settle(svc, next.id)).state, "succeeded");
    assert.equal(Worker.instances.length, 2, "the next job booted a fresh worker");
  } finally {
    delete globalThis.crossOriginIsolated;
  }
});
