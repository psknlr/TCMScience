// The Python worker pool of the browser runtime (docs/V2.md §13.2), used by ./browser.js.
//
// Worker 0 is the primary: it runs every stateful call (governed skills, clinic sessions, the audit check: their
// state lives in its IndexedDB-backed file system) and everything while the pool has one worker. Secondaries run
// stateless calls only, with no persistence; one is spawned only when a call has waited while every live worker was
// busy and the pool is below its size, one boot at a time, and an idle one is terminated after a while (a phone gets
// its memory back sooner than a desktop). The page compiles pyodide.asm.wasm once and hands the compiled module to
// every worker, so no two workers compile it at the same time (several concurrent compiles made some workers hang
// forever in the investigation, with the error swallowed inside Pyodide's loader).
//
// This module holds the transport of one worker (PoolWorker) and the pure rules (which worker takes a job, when to
// add one, how long an idle one lives); the runtime owns the lifecycle and the envelopes.

/** How long an idle secondary lives, by device class: phones and tablets give memory back after a minute. */
export const IDLE_MS = Object.freeze({ "phone-low": 60_000, phone: 60_000, tablet: 60_000, desktop: 600_000 });
/** A worker that has not booted in this time is terminated; it is respawned once, then the pool shrinks. */
export const BOOT_TIMEOUT_MS = 90_000;

export function idleMsFor(cls) {
  return IDLE_MS[cls] ?? IDLE_MS.desktop;
}

/**
 * Which worker takes `job` now, or null. A stateful or internal job needs the primary; a stateless one takes the
 * primary when it is free (it is warm and has every package already loaded), else any free booted secondary.
 */
export function pickWorker(job, workers, { primaryReady = true } = {}) {
  const free = (w) => w && w.state === "ready" && !w.job;
  const primary = workers.find((w) => w.primary) || null;
  const primaryFree = primaryReady && free(primary);
  if (job.internal || job.stateful) return primaryFree ? primary : null;
  if (primaryFree) return primary;
  return workers.find((w) => !w.primary && free(w)) || null;
}

/**
 * Whether to boot another secondary: a stateless call is waiting (so every live worker that could take it is busy),
 * the pool is below its size, and nothing else is booting (boots are staggered).
 */
export function wantsSpawn({ queue, workers, size, primaryReady = true }) {
  if (!primaryReady) return false;
  if (workers.some((w) => w.state === "booting")) return false;
  const live = workers.filter((w) => w.state !== "dead").length;
  if (live >= size) return false;
  return queue.some((j) => !j.internal && !j.stateful);
}

/** {size, live, busy, booting, cls, workers} for info() and the status events. */
export function poolStats(workers, { size, cls, cap = null, shrunk = 0 }) {
  const alive = workers.filter((w) => w.state !== "dead");
  return {
    size, cls,
    live: alive.filter((w) => w.state === "ready").length,
    busy: alive.filter((w) => w.job).length,
    booting: alive.filter((w) => w.state === "booting").length,
    cap, shrunk,
    workers: alive.map((w) => ({ index: w.index, primary: w.primary, state: w.state, busy: Boolean(w.job) })),
  };
}

/**
 * One Pyodide worker: a module Worker speaking the protocol of ./pyodide.worker.js (JSON-string requests and replies
 * by id), its own interrupt buffer when the page is cross-origin isolated, and what it is doing.
 *
 * `state`: "booting" → "ready" → "dead". `job` is the call it is running. Messages that are not replies (boot
 * progress, an accelerator request) go to `onMessage(worker, msg)`; a crash (an error event) fails whatever it was
 * answering and goes to `onCrash(worker, err)`.
 */
export class PoolWorker {
  state = "booting";
  job = null;
  info = null;
  idleTimer = null;
  interrupt = null;

  #worker;
  #pending = new Map();
  #nextId;
  #onMessage;
  #onCrash;

  constructor({ Worker, url, index, primary, isolated = false, nextId, onMessage, onCrash }) {
    this.index = index;
    this.primary = Boolean(primary);
    this.#nextId = nextId;
    this.#onMessage = onMessage || (() => {});
    this.#onCrash = onCrash || (() => {});
    this.#worker = new Worker(url, { type: "module", name: this.primary ? "tcmstudio-python" : `tcmstudio-python-${index}` });
    this.#worker.onmessage = (ev) => this.#message(ev.data);
    this.#worker.onerror = (ev) => this.#crash(new Error(ev?.message || "the Python worker failed"));
    this.#worker.onmessageerror = () => this.#crash(new Error("a message from the Python worker could not be read"));
    const sab = isolated && typeof SharedArrayBuffer === "function" ? new SharedArrayBuffer(1) : null;
    this.interrupt = sab ? new Uint8Array(sab) : null;
    if (sab) this.#worker.postMessage({ type: "interrupt-buffer", buffer: sab });
  }

  get dead() { return this.state === "dead"; }

  /** A structured message (the compiled module) or a JSON string (an accelerator's answer). */
  post(message) {
    if (this.dead) return;
    try { this.#worker.postMessage(message); } catch { /* terminated between the check and the post */ }
  }

  /**
   * Send a request. Returns {id, done}: `done` resolves with the reply, or with {timeout:true} after `timeoutMs`
   * (0 = none), {fatal:true} on a crash, {terminated:true} when the worker is stopped first. It never rejects.
   */
  send(op, payload, timeoutMs = 0) {
    const id = this.#nextId();
    const done = new Promise((resolve) => {
      if (this.dead) {
        resolve({ id, ok: false, terminated: true, error: { type: "runtime_error", message: "the worker was stopped" } });
        return;
      }
      const rec = { op, resolve, timer: null };
      if (timeoutMs > 0) {
        rec.timer = setTimeout(() => {
          this.#pending.delete(id);
          resolve({ id, ok: false, timeout: true, error: { type: "timeout", message: `${op} took longer than ${Math.round(timeoutMs / 1000)} s` } });
        }, timeoutMs);
      }
      this.#pending.set(id, rec);
      if (this.interrupt) Atomics.store(this.interrupt, 0, 0);
      this.post(JSON.stringify({ id, op, payload }));
    });
    return { id, done };
  }

  /** SIGINT: Python raises KeyboardInterrupt at its next check. */
  signalInterrupt() {
    if (!this.interrupt) return false;
    Atomics.store(this.interrupt, 0, 2);
    return true;
  }

  /** Stop the worker; whatever it was answering resolves as {terminated:true}. */
  terminate(err) {
    if (this.dead) return;
    this.state = "dead";
    if (this.idleTimer) clearTimeout(this.idleTimer);
    this.idleTimer = null;
    try { this.#worker.terminate(); } catch { /* already gone */ }
    const pending = [...this.#pending.values()];
    this.#pending.clear();
    for (const rec of pending) {
      if (rec.timer) clearTimeout(rec.timer);
      rec.resolve({ ok: false, fatal: false, terminated: true, error: { type: "runtime_error", message: err?.message || "terminated" } });
    }
  }

  #message(data) {
    if (this.dead) return;
    let msg;
    try {
      msg = typeof data === "string" ? JSON.parse(data) : data;
    } catch (err) {
      this.#crash(new Error(`unreadable reply from the Python worker: ${err.message}`));
      return;
    }
    // replies carry no type; progress and accelerator requests do
    if (msg && typeof msg.type === "string") {
      this.#onMessage(this, msg);
      return;
    }
    const rec = this.#pending.get(msg?.id);
    if (!rec) return;
    this.#pending.delete(msg.id);
    if (rec.timer) clearTimeout(rec.timer);
    rec.resolve(msg);
  }

  #crash(err) {
    if (this.dead) return;
    const pending = [...this.#pending.values()];
    this.#pending.clear();
    for (const rec of pending) {
      if (rec.timer) clearTimeout(rec.timer);
      rec.resolve({ ok: false, fatal: true, error: { type: "fatal", message: err.message } });
    }
    this.#onCrash(this, err, { answered: pending.length > 0 });
  }
}
