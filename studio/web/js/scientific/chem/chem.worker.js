// The browser chemistry's module worker (ADMET): RDKit.js 2026.09.1 started from verified wasm bytes, and
// bioagent.admet's molecule preparation (prepare.js) for batches of molecules, off the page. Start it with
// new Worker(new URL("./chem.worker.js", import.meta.url), {type: "module"}).
//
// Protocol. Messages are structured, not the JSON strings of the other workers: the wasm and the feature buffers
// travel as ArrayBuffers, and the results' buffers are transferred. Requests {id, op, payload}; replies
// {id, ok: true, ms, result} | {id, ok: false, ms, error: {type, message}}; while prepare runs,
// {type: "event", id, event: {kind: "progress", done, total, ok, failed}} at most every 200 ms and once at the end.
// {type: "cancel", id} stops that op (queued: before it starts; running: at the next pause, at most `yieldMs` (50 ms)
// of work away). {type: "interrupt-buffer", buffer}: a 1-byte SharedArrayBuffer the page sets non-zero to stop the
// running op before its next molecule (lowered as each op starts). Ops run one at a time, in arrival order.
//
// Ops:
//   init {wasmUrl | wasmBytes, sha256}
//       → {engine, rdkit, wasm: {sha256, bytes, source}, catalogs: {entries, unported}, ms}
//       The wasm must be the pinned build (rdkit.js RDKIT.wasm); sha256 must be the pin. rdkit_tables.json and
//       filter_catalogs.json are read from beside this file. init again restarts the chemistry.
//   prepare {molecules: [{name, smiles}], alerts = true}
//       → {rows: [{name, smiles, ok, error, refused, canonical, descriptors, atoms_with_h, alerts}], n, n_ok,
//          features: Float32Array (n_ok × 2073), bits: Uint8Array (n_ok × 256), timing: {total_ms, per_molecule_ms}}
//       rows follow the molecules; features and bits hold the ok rows in that order (prepared-set.js writes the
//       directory from them). At most MAX_MOLECULES per call. An error of type "rdkit" means RDKit.js itself failed
//       (a trap: its memory is no longer trusted); every later prepare gets the same error until init starts it again.
//   info {} → init's result, or null before init.

import { ChemError, RDKIT, fetchWasm, loadRDKit } from "./rdkit.js";
import { N_BITS_BYTES, N_FEATURES } from "./features.js";
import { ENGINE } from "./prepared-set.js";
import { Preparer } from "./prepare.js";

/** A batch's features alone are 8.3 KB a molecule; a call is held to what a worker keeps comfortably (166 MB). */
export const MAX_MOLECULES = 20_000;
const PROGRESS_MS = 200;
const YIELD_MS = 50;
const DATA = Object.freeze({ tables: "rdkit_tables.json", catalogs: "filter_catalogs.json" });

class OpError extends Error {
  constructor(type, message) {
    super(message);
    this.type = type;
  }
}

/** What the handlers need, so the tests can give fakes: fetch, readData(name) → the parsed JSON file beside this module,
 * factory (the RDKit.js loader; null: the vendored one), post(message, transfer), now(), yieldMs. */
export function workerEnv({ fetch = globalThis.fetch?.bind(globalThis), readData = null, factory = null, post = () => {},
  now = () => performance.now(), yieldMs = YIELD_MS } = {}) {
  return {
    fetch, factory, post, now, yieldMs,
    readData: readData || (async (name) => {
      const res = await fetch(new URL(`./${name}`, import.meta.url));
      if (!res.ok) throw new OpError("bad_data", `${name}: HTTP ${res.status}`);
      return res.json();
    }),
    state: null, // {RDKit, preparer, info}
    interrupt: null,
    cancelled: new Set(),
  };
}

export function cancel(env, id) {
  env.cancelled.add(id);
}

function errorOf(err) {
  if (err instanceof OpError) return { type: err.type, message: err.message };
  if (err instanceof ChemError) return { type: err.code, message: err.message };
  if (err instanceof TypeError) return { type: "bad_arguments", message: err.message };
  return { type: "internal", message: String(err?.message || err) };
}

async function init(payload, env) {
  const { wasmUrl = null, wasmBytes = null, sha256 = RDKIT.wasm.sha256 } = payload || {};
  if (!wasmUrl === !wasmBytes) throw new OpError("bad_arguments", "init takes wasmUrl or wasmBytes (one of them)");
  const bytes = wasmBytes ? wasmBytes : await fetchWasm(String(wasmUrl), { fetch: env.fetch, sha256 });
  const [tables, catalogs] = await Promise.all([env.readData(DATA.tables), env.readData(DATA.catalogs)]);
  for (const [name, doc] of [[DATA.tables, tables], [DATA.catalogs, catalogs]]) {
    if (doc?.rdkit !== RDKIT.release) throw new OpError("bad_data", `${name} is for RDKit ${doc?.rdkit}, not ${RDKIT.release}`);
  }
  if (env.state) {
    env.state.preparer.free();
    env.state = null;
  }
  const RDKit = await loadRDKit(bytes, { sha256, factory: env.factory });
  const preparer = new Preparer(RDKit, { tables, catalogs });
  const info = {
    engine: { ...ENGINE },
    rdkit: RDKit.version(),
    wasm: { sha256: RDKIT.wasm.sha256, bytes: RDKIT.wasm.bytes, source: wasmBytes ? "bytes" : String(wasmUrl) }, // as loadRDKit checked
    catalogs: { entries: preparer.catalog.entries.length, unported: preparer.catalog.unported },
  };
  env.state = { RDKit, preparer, info };
  return info;
}

const quantiles = (a) => {
  if (!a.length) return null;
  const s = Float64Array.from(a).sort();
  const at = (f) => s[Math.min(s.length - 1, Math.floor(f * s.length))];
  let sum = 0;
  for (const x of s) sum += x;
  const r = (x) => Math.round(x * 1000) / 1000;
  return { mean: r(sum / s.length), p50: r(at(0.5)), p95: r(at(0.95)), max: r(s[s.length - 1]) };
};

async function prepare(payload, env, cx) {
  if (!env.state) throw new OpError("not_initialised", "the chemistry is not started: send init first");
  const molecules = payload?.molecules;
  if (!Array.isArray(molecules)) throw new OpError("bad_arguments", "prepare takes {molecules: [{name, smiles}]}");
  if (molecules.length > MAX_MOLECULES) {
    throw new OpError("too_large", `${molecules.length} molecules in one call; at most ${MAX_MOLECULES} (send them in batches)`);
  }
  const alerts = payload.alerts !== false;
  const { preparer } = env.state;
  const n = molecules.length;
  const features = new Float32Array(n * N_FEATURES);
  const bits = new Uint8Array(n * N_BITS_BYTES);
  const rows = [];
  const times = [];
  let nOk = 0;
  let failed = 0;
  const t0 = env.now();
  let lastProgress = t0;
  let lastYield = t0;
  const progress = (done) => cx.emit({ kind: "progress", done, total: n, ok: nOk, failed });
  for (let i = 0; i < n; i++) {
    cx.check();
    const m = molecules[i];
    const smiles = typeof m?.smiles === "string" ? m.smiles : null;
    if (smiles === null) throw new OpError("bad_arguments", `molecule ${i} has no smiles string`);
    const t = env.now();
    let row;
    try {
      row = preparer.prepare(smiles, { alerts });
    } catch (err) {
      throw new OpError(err instanceof ChemError ? err.code : "internal",
        `molecule ${i} (${m.name ?? smiles}): ${err?.message || err}`);
    }
    times.push(env.now() - t);
    if (row.ok) {
      features.set(row.features, nOk * N_FEATURES);
      bits.set(row.bits, nOk * N_BITS_BYTES);
      nOk++;
    } else {
      failed++;
    }
    rows.push({ name: m.name ?? null, smiles, ok: row.ok, error: row.error, refused: row.refused, canonical: row.canonical,
      descriptors: row.descriptors, atoms_with_h: row.atoms_with_h, alerts: row.alerts });
    const now = env.now();
    if (now - lastProgress >= PROGRESS_MS) {
      lastProgress = now;
      progress(i + 1);
    }
    if (now - lastYield >= env.yieldMs) { // let a cancel message in
      await new Promise((resolve) => setTimeout(resolve, 0));
      lastYield = env.now();
    }
  }
  progress(n);
  const result = {
    rows, n, n_ok: nOk,
    features: new Float32Array(features.buffer, 0, nOk * N_FEATURES),
    bits: new Uint8Array(bits.buffer, 0, nOk * N_BITS_BYTES),
    timing: { total_ms: Math.round((env.now() - t0) * 1000) / 1000, per_molecule_ms: quantiles(times) },
  };
  return { result, transfer: [features.buffer, bits.buffer] };
}

/** One request → {reply, transfer}. Never throws. */
export async function handle(msg, env) {
  const t0 = env.now();
  const { id, op, payload } = msg || {};
  if (env.interrupt) Atomics.store(env.interrupt, 0, 0); // a flag left from the op before this one
  const cx = {
    check() {
      if (env.cancelled.has(id) || (env.interrupt && Atomics.load(env.interrupt, 0))) throw new OpError("cancelled", "cancelled before it finished");
    },
    emit(event) {
      env.post({ type: "event", id, event });
    },
  };
  const ms = () => Math.round(env.now() - t0);
  try {
    cx.check();
    let out;
    if (op === "init") out = { result: { ...(await init(payload, env)), ms: ms() }, transfer: [] };
    else if (op === "prepare") out = await prepare(payload, env, cx);
    else if (op === "info") out = { result: env.state ? env.state.info : null, transfer: [] };
    else throw new OpError("bad_arguments", `no op ${JSON.stringify(op)} here (init, prepare, info)`);
    return { reply: { id, ok: true, ms: ms(), result: out.result }, transfer: out.transfer };
  } catch (err) {
    return { reply: { id, ok: false, ms: ms(), error: errorOf(err) }, transfer: [] };
  } finally {
    env.cancelled.delete(id);
  }
}

/** The message loop of a worker scope (self, or a test's stand-in). */
export function serve(scope, env) {
  let chain = Promise.resolve();
  scope.onmessage = (ev) => {
    const msg = ev.data;
    if (!msg || typeof msg !== "object") return;
    if (msg.type === "interrupt-buffer") {
      env.interrupt = typeof SharedArrayBuffer === "function" && msg.buffer instanceof SharedArrayBuffer ? new Uint8Array(msg.buffer) : null;
      return;
    }
    if (msg.type === "cancel") {
      cancel(env, msg.id);
      return;
    }
    if (msg.id === undefined || msg.id === null) return;
    chain = chain.then(async () => {
      const { reply, transfer } = await handle(msg, env);
      scope.postMessage(reply, transfer);
    }).catch(() => {});
  };
}

function isWorkerMain() {
  if (typeof self === "undefined" || typeof WorkerGlobalScope === "undefined" || !(self instanceof WorkerGlobalScope)) return false;
  const bare = (u) => String(u).split("#")[0];
  return bare(self.location?.href) === bare(import.meta.url);
}

if (isWorkerMain()) {
  serve(self, workerEnv({ post: (m, transfer) => self.postMessage(m, transfer || []) }));
}
