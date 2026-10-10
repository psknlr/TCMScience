// The adaptive scheduler: which backend runs a piece of work, and what happens when it cannot.
//
// A task names an operation ("sequence.counts", "align.global", …), its size, and what its result must be: exact
// (bit for bit what the reference computes) or approximate within a stated tolerance. Backends say whether they
// support it here and roughly what it costs; the scheduler orders them by cost (a cost model at first, then what
// this device has measured), runs the first, and on a failure runs the next. Every run returns its provenance:
// what was asked for, what was considered and why each was passed over, what ran, and whether its output was checked.
//
// Rules that do not bend for speed:
// - exact work goes only to backends that produce exact results (an approximate backend may only rank candidates
//   that an exact one then re-scores);
// - sensitive inputs never go to a remote backend unless the task carries the person's consent for it;
// - a cancelled task stops: it is not retried on another backend;
// - a backend that fails repeatedly (a lost GPU device, a broken driver) is set aside for the rest of the session.

export const BACKEND_KINDS = Object.freeze(["webnn", "webgpu", "wasm-simd", "wasm", "js", "python", "native", "runner", "remote"]);
const LOCAL = new Set(["webnn", "webgpu", "wasm-simd", "wasm", "js", "python", "native"]);
const FAILURES_TO_SET_ASIDE = 2;
const EWMA = 0.3;

export class SchedulerError extends Error {
  constructor(message, { code = "no_backend", provenance = null } = {}) {
    super(message);
    this.name = "SchedulerError";
    this.code = code;
    this.provenance = provenance;
  }
}

/**
 * A backend: {id, kind (BACKEND_KINDS), exact: true when its results are bit-identical to the reference,
 * supports(task, caps) → {ok, reason?}, cost(task, caps) → estimated ms (or null when it cannot say),
 * run(task, input, {signal}) → result}. `verify(task, input, result) → {ok, reason?}` is optional: when given, a run
 * whose output fails it counts as a failure and the next backend runs.
 */
export class ComputeScheduler {
  #backends = new Map();
  #history = new Map(); // `${backend}|${op}|${bucket}` → ms per unit (EWMA)
  #health = new Map();  // backend → {failures, setAside, reason}

  constructor({ caps = null, backends = [], now } = {}) {
    this.caps = caps;
    this.now = now || (() => (globalThis.performance?.now ? globalThis.performance.now() : Date.now()));
    for (const b of backends) this.register(b);
  }

  register(backend) {
    if (!backend?.id || !BACKEND_KINDS.includes(backend.kind)) throw new TypeError(`backend needs an id and one of the kinds ${BACKEND_KINDS.join(", ")}`);
    this.#backends.set(backend.id, backend);
    return this;
  }

  setCapabilities(caps) { this.caps = caps; }

  backends() { return [...this.#backends.values()]; }

  /** A backend set aside for the session (device lost, repeated failures) with the reason. */
  setAside(id, reason) {
    const h = this.#healthOf(id);
    h.setAside = true;
    h.reason = String(reason || "set aside");
  }

  health(id) { return { ...this.#healthOf(id) }; }

  /**
   * The candidates for a task, in the order they would be tried, and the ones passed over with the reason.
   * task: {op, units (a size measure the cost model uses, such as cells or operations), bytes, exact: true,
   * sensitive: false, consent: {remote: false}, preference: "auto" | a kind | a backend id | "reference"}.
   */
  plan(task) {
    const pref = task.preference || "auto";
    const rows = [];
    for (const b of this.#backends.values()) {
      const why = this.#excluded(b, task, pref);
      if (why) { rows.push({ id: b.id, kind: b.kind, use: false, reason: why }); continue; }
      let support;
      try { support = b.supports(task, this.caps) || { ok: false, reason: "unsupported" }; } catch (err) { support = { ok: false, reason: `supports() failed: ${message(err)}` }; }
      if (!support.ok) { rows.push({ id: b.id, kind: b.kind, use: false, reason: support.reason || "unsupported" }); continue; }
      rows.push({ id: b.id, kind: b.kind, use: true, reason: support.reason || "", estimate_ms: this.#estimate(b, task) });
    }
    const usable = rows.filter((r) => r.use);
    const named = pref !== "auto" && pref !== "reference";
    usable.sort((a, z) => {
      // a backend asked for by id or kind goes first; otherwise the lowest estimate; ties keep registration order
      if (named) {
        const am = a.id === pref || a.kind === pref, zm = z.id === pref || z.kind === pref;
        if (am !== zm) return am ? -1 : 1;
      }
      return (a.estimate_ms ?? Infinity) - (z.estimate_ms ?? Infinity);
    });
    return { order: usable, passed_over: rows.filter((r) => !r.use) };
  }

  /**
   * Run a task: the planned backends in order until one succeeds (and passes its own check). Resolves with
   * {result, provenance}; rejects with SchedulerError (code "no_backend" or "all_failed", provenance attached), or
   * with the AbortError when the task was cancelled.
   */
  async run(task, input, { signal } = {}) {
    const { order, passed_over } = this.plan(task);
    const provenance = {
      op: task.op, requested: task.preference || "auto", exact: task.exact !== false,
      considered: order.map((r) => ({ id: r.id, kind: r.kind, estimate_ms: round(r.estimate_ms) })),
      passed_over, tried: [], executed: null, verified: null, fallback_reason: null,
    };
    if (!order.length) throw new SchedulerError(`no backend can run ${task.op} here`, { provenance });
    for (const row of order) {
      throwIfAborted(signal);
      const b = this.#backends.get(row.id);
      const t0 = this.now();
      try {
        const result = await raceAbort(b.run(task, input, { signal }), signal);
        const ms = this.now() - t0;
        let check = null;
        if (typeof b.verify === "function") {
          try { check = b.verify(task, input, result) || { ok: true }; } catch (err) { check = { ok: false, reason: `verify() failed: ${message(err)}` }; }
          if (!check.ok) throw Object.assign(new Error(`output failed its check: ${check.reason || "mismatch"}`), { code: "verify_failed" });
        }
        this.#succeeded(b, task, ms);
        provenance.tried.push({ id: b.id, kind: b.kind, outcome: "ok", ms: round(ms) });
        provenance.executed = { id: b.id, kind: b.kind, exact: b.exact !== false };
        provenance.verified = check ? check.ok === true : null;
        if (provenance.tried.length > 1) provenance.fallback_reason = provenance.tried[0].reason;
        return { result, provenance };
      } catch (err) {
        if (isAbort(err) || signal?.aborted) throw abortError();
        const ms = this.now() - t0;
        this.#failed(b, err);
        provenance.tried.push({ id: b.id, kind: b.kind, outcome: "error", reason: message(err), ms: round(ms) });
      }
    }
    provenance.fallback_reason = provenance.tried.at(-1)?.reason || null;
    throw new SchedulerError(`every backend failed for ${task.op}`, { code: "all_failed", provenance });
  }

  /** Feed a measurement taken outside run() (a benchmark): ms for `units` of `op` on backend `id`. */
  record(id, op, units, ms) {
    if (!(units > 0) || !(ms >= 0)) return;
    const key = `${id}|${op}|${bucket(units)}`;
    const perUnit = ms / units;
    const prev = this.#history.get(key);
    this.#history.set(key, prev === undefined ? perUnit : prev * (1 - EWMA) + perUnit * EWMA);
  }

  /** What has been measured, for a device profile or a report: [{backend, op, bucket, ms_per_unit}]. */
  measurements() {
    return [...this.#history.entries()].map(([k, v]) => {
      const [backend, op, b] = k.split("|");
      return { backend, op, bucket: Number(b), ms_per_unit: v };
    });
  }

  #excluded(b, task, pref) {
    const h = this.#healthOf(b.id);
    if (h.setAside) return `set aside for this session: ${h.reason}`;
    if (pref === "reference" && b.kind !== "python" && b.kind !== "native") return "the reference implementation was asked for";
    if (pref === "cpu" && (b.kind === "webgpu" || b.kind === "webnn")) return "CPU only was asked for";
    if (task.exact !== false && b.exact === false) return "the task needs exact results and this backend is approximate";
    if (!LOCAL.has(b.kind) && task.sensitive && task.consent?.remote !== true) {
      return "sensitive input is not sent off this device without the person's consent";
    }
    if (b.kind === "remote" && task.local_only) return "the task is local-only";
    return null;
  }

  #estimate(b, task) {
    const units = Number(task.units) || 0;
    const measured = this.#history.get(`${b.id}|${task.op}|${bucket(units)}`);
    if (measured !== undefined && units > 0) return measured * units;
    try {
      const est = b.cost?.(task, this.caps);
      return Number.isFinite(est) ? est : null;
    } catch {
      return null;
    }
  }

  #succeeded(b, task, ms) {
    const h = this.#healthOf(b.id);
    h.failures = 0;
    this.record(b.id, task.op, Number(task.units) || 0, ms);
  }

  #failed(b, err) {
    const h = this.#healthOf(b.id);
    h.failures += 1;
    if (err?.code === "device_lost" || h.failures >= FAILURES_TO_SET_ASIDE) {
      h.setAside = true;
      h.reason = err?.code === "device_lost" ? `device lost: ${message(err)}` : `failed ${h.failures} times: ${message(err)}`;
    }
  }

  #healthOf(id) {
    if (!this.#health.has(id)) this.#health.set(id, { failures: 0, setAside: false, reason: null });
    return this.#health.get(id);
  }
}

function bucket(units) {
  return units > 0 ? Math.round(Math.log2(units)) : 0;
}

function round(ms) {
  return typeof ms === "number" && Number.isFinite(ms) ? Math.round(ms * 1000) / 1000 : null;
}

function message(err) {
  return String(err?.message || err || "error").slice(0, 300);
}

function isAbort(err) {
  return err?.name === "AbortError" || err?.code === "cancelled";
}

function abortError() {
  try { return new DOMException("aborted", "AbortError"); } catch { const e = new Error("aborted"); e.name = "AbortError"; return e; }
}

function throwIfAborted(signal) {
  if (signal?.aborted) throw abortError();
}

function raceAbort(promise, signal) {
  if (!signal) return Promise.resolve(promise);
  return new Promise((resolve, reject) => {
    const onAbort = () => reject(abortError());
    if (signal.aborted) { onAbort(); return; }
    signal.addEventListener("abort", onAbort, { once: true });
    Promise.resolve(promise).then(
      (v) => { signal.removeEventListener("abort", onAbort); resolve(v); },
      (e) => { signal.removeEventListener("abort", onAbort); reject(e); },
    );
  });
}
