// The job workers (studio/docs/BROWSER_JOBS.md §4): a fixed number of slots, each holding at most one Pyodide worker
// (../engines/science.worker.js), started only when a job first needs it and kept for the next one. A stage takes a
// worker with lease(), sends it operations through the lease and gives it back with release(); while it holds the
// lease no other stage can reach that worker, so one interrupt buffer and one operation at a time belong to one job.
//
// The protocol is the tool worker's (../../runtime/browser.js ↔ pyodide.worker.js): requests are JSON strings
// {id, op, payload}; replies {id, ok, ms, result | fatal, error}; {type:"event", id, event} while an operation runs;
// one structured message, {type:"interrupt-buffer", buffer}, carries a 1-byte SharedArrayBuffer when the page is
// cross-origin isolated.
//
// Stopping a call. On an isolated page the lease writes SIGINT (2) into the interrupt buffer, so Python raises
// KeyboardInterrupt at its next check and the worker survives; when it has not answered within the grace period
// (3 s), or when the page is not isolated, the worker is terminated. A worker that crashed, timed out, hit a Pyodide
// fatal error or was terminated is gone: its slot boots a new one at the next lease, never behind the caller's back.

import { abortError } from "../../core/util.js";

/** Workers never outnumber this: each holds a whole CPython heap, and the page needs the main thread's core. */
export const MAX_POOL_SIZE = 4;
/** The memory one worker is planned with: Pyodide, the bundle, pyarrow and a research run's tables. */
export const GB_PER_WORKER = 1.5;
const ASSUMED_CORES = 2;
const ASSUMED_MEMORY_GB = 2;

/**
 * How many job workers this device can run side by side → {size, reason, cores, memory_gb, assumed, interruptible}:
 * clamp(min(cores - 1, floor(memoryGb / 1.5)), 1, 4). A figure the browser does not report is assumed (2 cores,
 * 2 GB) and the reason says so; navigator.deviceMemory is rounded down and capped at 8 by the browser itself.
 * isolated only changes how a cancel stops a worker, which the reason states.
 */
export function recommendedPoolSize({ cores, memoryGb, isolated } = {}) {
  const assumed = [];
  let c = Math.floor(Number(cores));
  if (!Number.isFinite(c) || c < 1) {
    c = ASSUMED_CORES;
    assumed.push(`the CPU core count is not reported, ${ASSUMED_CORES} assumed`);
  }
  let m = Number(memoryGb);
  if (!Number.isFinite(m) || m <= 0) {
    m = ASSUMED_MEMORY_GB;
    assumed.push(`the device memory is not reported, ${ASSUMED_MEMORY_GB} GB assumed`);
  }
  const byCores = c - 1;
  const byMemory = Math.floor(m / GB_PER_WORKER);
  const size = Math.max(1, Math.min(byCores, byMemory, MAX_POOL_SIZE));
  const parts = [
    `${size} job worker${size === 1 ? "" : "s"}: ${c} core${c === 1 ? "" : "s"} leave ${Math.max(0, byCores)} beside the page`,
    `${m} GB allows ${byMemory} at ${GB_PER_WORKER} GB each`,
    `at most ${MAX_POOL_SIZE}`,
    ...assumed,
    isolated ? "a cancel interrupts Python in place (cross-origin isolated)" : "a cancel terminates the worker (the page is not cross-origin isolated)",
  ];
  return { size, reason: parts.join("; "), cores: c, memory_gb: m, assumed, interruptible: Boolean(isolated) };
}

/** An Error with the protocol's error type (and anything else the caller should see). */
function typedError(type, message, extra = {}) {
  const err = new Error(message);
  err.type = type;
  return Object.assign(err, extra);
}

/** A stopped call or wait: an AbortError (core/util.js isAbort) whose type is "cancelled". */
function cancelled(message = "the call was cancelled") {
  const err = abortError(message);
  try { err.type = "cancelled"; } catch { /* a frozen DOMException: isAbort still knows it */ }
  return err;
}

const now = () => (globalThis.performance?.now ? performance.now() : Date.now());

// what the pool keeps about each lease, out of the caller's reach: its slot, the call in flight, whether it was revoked
const INTERNAL = new WeakMap();

/**
 * A worker held by one caller until release(). id is the slot's ("science-1"); info is what the worker said when it
 * started (runtime, versions, platform, machine, pyodide, packages), kept after release for the job's receipt.
 */
export class Lease {
  #ctl;

  constructor(slot, ctl) {
    this.id = slot.id;
    this.#ctl = ctl;
    this.released = false;
    this.calls = 0;
    this.busy_ms = 0;
    this.boot_ms = null;
    this.info = null;
    this.packages = [];
    INTERNAL.set(this, { slot, current: null, revoked: false });
  }

  /** Whether calls can still reach the worker: held, not released, and the worker is running. */
  get alive() {
    const { slot } = INTERNAL.get(this);
    return !this.released && slot.lease === this && Boolean(slot.worker);
  }

  /** Whether a call is running on this lease now. */
  get busy() {
    return Boolean(INTERNAL.get(this).current);
  }

  /**
   * Run one operation → its result. Rejects with an Error whose type is the worker's error type, "timeout" (the
   * worker is then terminated), "fatal" (a crash or a Pyodide fatal error), "terminated", or an AbortError
   * (type "cancelled") when `signal` aborts. One call at a time per lease.
   */
  call(op, payload = {}, { signal, onEvent, timeoutMs } = {}) {
    return this.#ctl.call(this, op, payload, { signal, onEvent, timeoutMs });
  }

  /** Stop the call in progress (SIGINT when isolated, then terminate after the grace period) → whether one ran. */
  interrupt() {
    return this.#ctl.interrupt(this);
  }

  /** Give the worker back. A call still running finishes first; the worker returns to the pool after it. */
  release() {
    this.#ctl.release(this);
  }

  /** Terminate the worker now (its slot starts a new one at the next lease) and release the lease. */
  terminate() {
    this.#ctl.terminate(this);
  }

  /** What this lease used: {id, calls, busy_ms, boot_ms} (boot_ms only when the worker started for this lease). */
  stats() {
    return { id: this.id, calls: this.calls, busy_ms: Math.round(this.busy_ms), boot_ms: this.boot_ms };
  }
}

export class ScienceWorkerPool {
  #opts;
  #slots;
  #waiters = [];
  #seq = 0;
  // what a Lease calls back into (it holds no reference to the pool's private methods)
  #control = {
    call: (lease, op, payload, opts) => this.#call(lease, op, payload, { ...opts, count: true }),
    interrupt: (lease) => {
      const { slot, current } = INTERNAL.get(lease);
      if (!current || slot.lease !== lease) return false;
      this.#abort(slot, current);
      return true;
    },
    release: (lease) => this.#release(lease),
    terminate: (lease) => {
      const { slot } = INTERNAL.get(lease);
      if (slot.lease === lease) this.#kill(slot, "terminated by its lease");
      this.#release(lease);
    },
  };

  /**
   * size: how many workers at most (recommendedPoolSize); workerUrl: ../engines/science.worker.js; Worker: the
   * Worker constructor; boot: the boot.json document, or an async function giving it; siteUrl, indexUrl: passed to
   * init; bootTimeoutMs: init and package loading (180 s); interruptGraceMs: how long Python has to stop after
   * SIGINT before its worker is terminated (3 s); callTimeoutMs: the default limit of a call (0: none).
   */
  constructor({
    size = 1, workerUrl, Worker = globalThis.Worker, boot, siteUrl = null, indexUrl = null, bootTimeoutMs = 180_000,
    interruptGraceMs = 3000, callTimeoutMs = 0, name = "tcmstudio-science",
  } = {}) {
    const n = Math.max(1, Math.floor(Number(size)) || 1);
    this.#opts = { workerUrl, Worker, boot, siteUrl, indexUrl, bootTimeoutMs, interruptGraceMs, callTimeoutMs, name };
    this.#slots = Array.from({ length: n }, (_, i) => ({
      id: `science-${i + 1}`, worker: null, gen: 0, info: null, packages: new Set(), interrupt: null, pending: new Map(),
      lease: null, phase: null, calls: 0, busy_ms: 0, boot_ms: null, instances: 0, onProgress: null, error: null,
    }));
  }

  get size() { return this.#slots.length; }

  /** Whether this browser can start job workers at all. */
  get available() { return typeof this.#opts.Worker === "function"; }

  /**
   * A worker with `packages` loaded → Lease. Waits for a free slot (FIFO); boots the slot's worker when it has none
   * (op init) and loads the packages it lacks (op packages). onEvent receives the boot's progress
   * ({kind:"progress", stage, fraction, message}). Rejects with the worker's error type when it cannot start
   * ("bundle_hash", "bad_boot", "timeout", "fatal") or lacks a package ("unavailable"), and with an AbortError when
   * `signal` aborts first.
   */
  async lease({ packages = [], signal, onEvent } = {}) {
    if (!this.available) throw typedError("unavailable", "this browser cannot start job workers (no Web Worker)");
    if (signal?.aborted) throw cancelled("the job was cancelled before it had a worker");
    const names = [...new Set((Array.isArray(packages) ? packages : []).map(String))];
    const lease = await this.#acquire(names, signal);
    const { slot } = INTERNAL.get(lease);
    try {
      if (!slot.worker) await this.#boot(slot, lease, { signal, onEvent });
      const missing = names.filter((n) => !slot.packages.has(n));
      if (missing.length) await this.#loadPackages(slot, lease, missing, { signal, onEvent });
    } catch (err) {
      lease.release();
      throw err;
    }
    slot.phase = null;
    lease.info = slot.info;
    lease.packages = [...slot.packages].sort();
    return lease;
  }

  /** {size, live, busy, waiting, workers: [{id, state, packages, calls, busy_ms, boot_ms, instances, runtime}]}. */
  stats() {
    const workers = this.#slots.map((s) => ({
      id: s.id, state: stateOf(s), packages: [...s.packages].sort(), calls: s.calls, busy_ms: Math.round(s.busy_ms),
      boot_ms: s.boot_ms, instances: s.instances, runtime: s.info?.runtime ?? null,
    }));
    return {
      size: this.#slots.length,
      live: this.#slots.filter((s) => s.worker).length,
      busy: this.#slots.filter((s) => s.lease).length,
      waiting: this.#waiters.length,
      workers,
    };
  }

  /** Terminate one slot's worker; a lease on it is revoked (its calls fail "terminated"). → whether one ran. */
  terminate(id) {
    const slot = this.#slots.find((s) => s.id === id);
    if (!slot) return false;
    const had = Boolean(slot.worker);
    this.#kill(slot, "the pool terminated the worker");
    if (slot.lease) this.#revoke(slot);
    this.#pump();
    return had;
  }

  /** Terminate every worker, revoke every lease and turn away whoever waits (the pool can be leased from again). */
  terminateAll() {
    for (const w of this.#waiters.splice(0)) {
      if (w.onAbort) w.signal?.removeEventListener("abort", w.onAbort);
      w.reject(typedError("terminated", "the job workers were stopped"));
    }
    for (const slot of this.#slots) {
      this.#kill(slot, "the pool terminated every worker");
      if (slot.lease) this.#revoke(slot);
    }
  }

  // ------------------------------------------------------------------------------------------------------ leasing

  #acquire(packages, signal) {
    return new Promise((resolve, reject) => {
      const waiter = { packages, resolve, reject, signal, onAbort: null };
      if (signal) {
        waiter.onAbort = () => {
          const i = this.#waiters.indexOf(waiter);
          if (i >= 0) this.#waiters.splice(i, 1);
          reject(cancelled("the job was cancelled while it waited for a worker"));
        };
        signal.addEventListener("abort", waiter.onAbort, { once: true });
      }
      this.#waiters.push(waiter);
      this.#pump();
    });
  }

  /** Hand free slots to waiters, first come first served: a running worker with the packages, then any running one,
   * then an empty slot (booted by the lease). */
  #pump() {
    while (this.#waiters.length) {
      const free = this.#slots.filter((s) => !s.lease);
      if (!free.length) return;
      const waiter = this.#waiters.shift();
      if (waiter.onAbort) waiter.signal?.removeEventListener("abort", waiter.onAbort);
      const has = (s) => waiter.packages.filter((p) => s.packages.has(p)).length;
      const live = free.filter((s) => s.worker).sort((a, b) => has(b) - has(a));
      const slot = live[0] || free[0];
      const lease = new Lease(slot, this.#control);
      slot.lease = lease;
      waiter.resolve(lease);
    }
  }

  #release(lease) {
    if (lease.released) return;
    lease.released = true;
    // a call still running keeps the worker until it settles (#call frees it then)
    if (!INTERNAL.get(lease).current) this.#free(lease);
  }

  #free(lease) {
    const { slot } = INTERNAL.get(lease);
    if (slot.lease !== lease) return; // revoked: the slot is someone else's now
    slot.lease = null;
    slot.phase = null;
    this.#pump();
  }

  /** Take the slot away from its lease holder (terminate/terminateAll): its later calls fail, its release is a no-op. */
  #revoke(slot) {
    const lease = slot.lease;
    slot.lease = null;
    slot.phase = null;
    if (lease) {
      lease.released = true;
      INTERNAL.get(lease).revoked = true;
    }
  }

  // ------------------------------------------------------------------------------------------------------- boot

  async #boot(slot, lease, { signal, onEvent }) {
    slot.phase = "booting";
    const o = this.#opts;
    let boot;
    try {
      boot = typeof o.boot === "function" ? await o.boot() : o.boot;
    } catch (err) {
      throw typedError("boot_missing", `boot.json could not be read: ${err?.message || err}`);
    }
    if (!boot || typeof boot !== "object") throw typedError("boot_missing", "the pool has no boot.json document to start a worker with");
    const gen = ++slot.gen;
    let worker;
    try {
      worker = new o.Worker(o.workerUrl, { type: "module", name: `${o.name}-${slot.id}` });
    } catch (err) {
      throw typedError("unavailable", `the job worker could not be created: ${err?.message || err}`);
    }
    slot.worker = worker;
    slot.instances += 1;
    slot.info = null;
    slot.packages = new Set();
    slot.error = null;
    worker.onmessage = (ev) => this.#onMessage(slot, gen, ev?.data);
    worker.onerror = (ev) => {
      ev?.preventDefault?.();
      this.#onCrash(slot, gen, ev?.message || "the job worker failed");
    };
    worker.onmessageerror = () => this.#onCrash(slot, gen, "a message from the job worker could not be read");
    const sab = globalThis.crossOriginIsolated && typeof SharedArrayBuffer === "function" ? new SharedArrayBuffer(1) : null;
    slot.interrupt = sab ? new Uint8Array(sab) : null;
    if (sab) worker.postMessage({ type: "interrupt-buffer", buffer: sab });
    // a pyodide.worker.js-style progress message carries no id: it belongs to the boot of this slot
    slot.onProgress = onEvent || null;
    const t0 = now();
    try {
      const info = await this.#call(lease, "init", { boot, siteUrl: o.siteUrl, indexUrl: o.indexUrl }, { signal, onEvent, timeoutMs: o.bootTimeoutMs, count: false });
      slot.info = info && typeof info === "object" ? info : {};
      for (const p of slot.info.packages || []) slot.packages.add(String(p));
      slot.boot_ms = Math.round(now() - t0);
      lease.boot_ms = slot.boot_ms;
    } catch (err) {
      // a worker that did not finish init is in no known state: it is not kept. Only this boot's worker, though: one
      // terminated from outside (terminate, terminateAll) may already have been followed by the next lease's.
      if (slot.gen === gen) {
        this.#kill(slot, `init failed: ${err?.message || err}`);
        slot.error = { type: err?.type || "boot_error", message: err?.message || String(err) };
      }
      throw err;
    } finally {
      if (slot.gen === gen) slot.onProgress = null;
    }
  }

  async #loadPackages(slot, lease, names, { signal, onEvent }) {
    slot.phase = "loading";
    const r = await this.#call(lease, "packages", { names }, { signal, onEvent, timeoutMs: this.#opts.bootTimeoutMs, count: false });
    const loaded = Array.isArray(r?.packages) ? r.packages : names;
    for (const p of loaded) slot.packages.add(String(p));
  }

  // ------------------------------------------------------------------------------------------------------ calls

  async #call(lease, op, payload, { signal, onEvent, timeoutMs, count }) {
    const own = INTERNAL.get(lease);
    const { slot } = own;
    if (own.revoked) throw typedError("terminated", `${lease.id} was taken from this lease (the pool terminated it)`);
    if (lease.released || slot.lease !== lease) throw typedError("released", `the lease on ${lease.id} was released`);
    if (!slot.worker) throw typedError("fatal", `the job worker ${lease.id} is gone; lease another one`);
    if (own.current) throw typedError("busy", `${lease.id} is still running ${own.current.op}: one call at a time per lease`);
    if (signal?.aborted) throw cancelled();
    // a worker that has not finished starting has nothing to interrupt yet: a stop then terminates it at once
    const call = { op, id: ++this.#seq, aborted: false, grace: null, hard: op === "init" };
    own.current = call;
    const t0 = now();
    const reply = this.#send(slot, call.id, op, payload, { timeoutMs: timeoutMs ?? this.#opts.callTimeoutMs, onEvent });
    const onAbort = () => this.#abort(slot, call);
    signal?.addEventListener("abort", onAbort, { once: true });
    let r;
    try {
      r = await reply;
    } finally {
      signal?.removeEventListener("abort", onAbort);
      clearTimeout(call.grace);
      own.current = null;
      if (count) {
        const ms = now() - t0;
        slot.calls += 1;
        slot.busy_ms += ms;
        lease.calls += 1;
        lease.busy_ms += ms;
      }
      if (lease.released) this.#free(lease);
    }
    // a stopped call has no result: whatever came back (Python's KeyboardInterrupt, a reply that raced the
    // interrupt, the termination) is not one
    if (call.aborted) throw cancelled();
    if (r.ok) return r.result;
    if (r.timeout) {
      // the worker may still be inside Python: it cannot be handed to anyone else
      this.#kill(slot, `${op} timed out`);
      throw typedError("timeout", r.error.message);
    }
    if (r.fatal) {
      this.#kill(slot, r.error?.message || "fatal error");
      throw typedError("fatal", r.error?.message || "the job worker hit a fatal error", { ms: r.ms });
    }
    throw typedError(r.error?.type || "runtime_error", r.error?.message || `${op} failed`, { ms: r.ms });
  }

  /** Send one request → a promise of its reply (never rejects; a timeout, crash or termination is a reply too). */
  #send(slot, id, op, payload, { timeoutMs, onEvent }) {
    return new Promise((resolve) => {
      const rec = { op, resolve, onEvent, timer: null };
      if (timeoutMs > 0) {
        rec.timer = setTimeout(() => {
          slot.pending.delete(id);
          resolve({ id, ok: false, timeout: true, error: { type: "timeout", message: `${op} took longer than ${Math.round(timeoutMs / 1000)} s` } });
        }, timeoutMs);
      }
      slot.pending.set(id, rec);
      // a SIGINT left over from an earlier call must not stop this one
      if (slot.interrupt) Atomics.store(slot.interrupt, 0, 0);
      try {
        slot.worker.postMessage(JSON.stringify({ id, op, payload }));
      } catch (err) {
        slot.pending.delete(id);
        if (rec.timer) clearTimeout(rec.timer);
        resolve({ id, ok: false, fatal: true, error: { type: "fatal", message: `the request could not be sent: ${err?.message || err}` } });
      }
    });
  }

  #abort(slot, call) {
    if (call.aborted) return;
    call.aborted = true;
    if (slot.interrupt && slot.worker && !call.hard) {
      Atomics.store(slot.interrupt, 0, 2); // SIGINT: KeyboardInterrupt at Python's next check
      call.grace = setTimeout(() => this.#kill(slot, `${call.op} did not stop within ${this.#opts.interruptGraceMs} ms of the interrupt`), this.#opts.interruptGraceMs);
    } else {
      this.#kill(slot, `${call.op} was cancelled`);
    }
  }

  #onMessage(slot, gen, data) {
    if (gen !== slot.gen) return;
    let msg;
    try {
      msg = typeof data === "string" ? JSON.parse(data) : data;
    } catch (err) {
      this.#onCrash(slot, gen, `unreadable reply from the job worker: ${err.message}`);
      return;
    }
    if (!msg || typeof msg !== "object") return;
    if (msg.type === "event") {
      const rec = slot.pending.get(msg.id);
      if (rec?.onEvent && msg.event && typeof msg.event === "object") callSafely(rec.onEvent, msg.event);
      return;
    }
    if (msg.type === "progress") {
      if (slot.onProgress) {
        const fraction = Number.isFinite(msg.progress) ? Math.max(0, Math.min(1, msg.progress / 100)) : null;
        callSafely(slot.onProgress, { kind: "progress", stage: msg.stage || null, fraction, message: msg.message || "" });
      }
      return;
    }
    const rec = slot.pending.get(msg.id);
    if (!rec) return;
    slot.pending.delete(msg.id);
    if (rec.timer) clearTimeout(rec.timer);
    rec.resolve(msg);
  }

  #onCrash(slot, gen, message) {
    if (gen !== slot.gen) return;
    this.#kill(slot, message, { fatal: true });
  }

  /**
   * End the slot's worker: terminate it, drop its later messages, and answer every pending request (fatal for a
   * crash, terminated otherwise). The slot keeps its lease, if any, until the holder releases it.
   */
  #kill(slot, message, { fatal = false } = {}) {
    slot.gen += 1;
    const w = slot.worker;
    slot.worker = null;
    slot.interrupt = null;
    slot.info = null;
    slot.packages = new Set();
    if (w) {
      try { w.terminate(); } catch { /* already gone */ }
    }
    const pending = [...slot.pending.values()];
    slot.pending.clear();
    for (const rec of pending) {
      if (rec.timer) clearTimeout(rec.timer);
      rec.resolve(fatal
        ? { ok: false, fatal: true, error: { type: "fatal", message } }
        : { ok: false, terminated: true, error: { type: "terminated", message } });
    }
    if (fatal) slot.error = { type: "fatal", message };
  }
}

function stateOf(slot) {
  if (slot.worker) return slot.lease ? slot.phase || "busy" : "idle";
  return slot.lease ? (slot.phase === "booting" ? "booting" : "dead") : "empty";
}

function callSafely(fn, arg) {
  try {
    fn(arg);
  } catch (err) {
    (globalThis.reportError || console.error)(err);
  }
}
