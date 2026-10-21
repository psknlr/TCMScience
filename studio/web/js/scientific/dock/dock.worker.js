// The browser docking's module worker: AutoDock Vina 1.2.7 (WebAssembly, ./vina.js) started from verified wasm bytes,
// one Vina run per call, off the page. Start it with new Worker(new URL("./dock.worker.js", import.meta.url),
// {type: "module"}); the docking job (pipelines/dock.js) starts several, each its own module from the same bytes.
//
// Protocol (as ../chem/chem.worker.js). Requests {id, op, payload}; replies {id, ok: true, ms, result} |
// {id, ok: false, ms, error: {type, message}}. Ops run one at a time, in arrival order. A Vina run cannot be
// interrupted: it blocks this worker until it ends, so a call is stopped by terminating the worker (the page's cancel),
// and nothing here listens for cancel messages.
//
// Ops:
//   init {wasmUrl | wasmBytes, sha256}
//       → {engine: {name, version, build: "wasm", wasm_sha256}, wasm: {sha256, bytes, source}, ms}
//       The wasm must be the pinned build (vina.js VINA.wasm); sha256 must be the pin. A relative wasmUrl is the
//       site's (boot.json's vina.url). init again starts a fresh module.
//   dock {key, receptor, ligand, params}
//       → the tcmstudio.vina-result/1 object bioagent.docking.prepared.PrecomputedVina reads:
//         {schema, key, receptor_sha256, ligand_sha256, params, engine, energies, poses, seconds} — or, when Vina
//         refuses the input, {schema, key, receptor_sha256, ligand_sha256, params, engine, error, seconds} (error:
//         Vina's message, trimmed): a result, not an op error — followed by result_sha256, the SHA-256 of the UTF-8
//         bytes of JSON.stringify of the object without result_sha256 (vina.js vinaResultText): exactly what the page
//         writes to results/<key>.json.
//       key: "reference" or "ligand-<index>"; receptor and ligand: the PDBQT texts Vina reads (their SHA-256 are of
//       these texts, UTF-8); params: {scoring, exhaustiveness, n_poses, seed, energy_range, center, size, spacing,
//       min_rmsd, max_evals} (vina.js vinaParams; the last three default to native's 0.375, 1.0, 0). energies are
//       Vina's raw rows [total, inter, intra, torsions, intra_best_pose], unrounded; poses its multi-MODEL PDBQT.
//   score {receptor, ligand, params}, optimize {receptor, ligand, params}
//       → {mode, receptor_sha256, ligand_sha256, params, engine, energies, pose? (optimize), seconds} (or error in
//       place of energies and pose): Vina.score() and Vina.optimize(max_steps) on the ligand's pose as given, for the
//       parity tests; params {scoring, center, size, spacing?, seed?, max_steps? (optimize)}.
//   info {} → init's result, or null before init.
// An error of type "vina" means the engine itself failed (it did not start, a run trapped, or it ran out of memory):
// every later run gets the same error until init starts a fresh module. "bad_arguments": a call refused before Vina
// saw it.

import { ENGINE, RESULT_SCHEMA, VINA, VinaError, fetchWasm, loadVina, runVina, sha256Hex, vinaParams } from "./vina.js";

const KEY = /^(reference|ligand-(0|[1-9]\d{0,5}))$/;

class OpError extends Error {
  constructor(type, message) {
    super(message);
    this.type = type;
  }
}

/** What the handlers need, so the tests can give fakes: fetch, factory (the Vina loader; null: the vendored one),
 * now(). */
export function workerEnv({ fetch = globalThis.fetch?.bind(globalThis), factory = null, now = () => performance.now() } = {}) {
  return {
    fetch, factory, now,
    state: null, // {V, info, broken: null | the message of the failure that broke it}
  };
}

function errorOf(err) {
  if (err instanceof OpError) return { type: err.type, message: err.message };
  if (err instanceof VinaError) return { type: err.code, message: err.message };
  if (err instanceof TypeError) return { type: "bad_arguments", message: err.message };
  return { type: "internal", message: String(err?.message || err) };
}

async function init(payload, env) {
  const { wasmUrl = null, wasmBytes = null, sha256 = VINA.wasm.sha256 } = payload || {};
  if (!wasmUrl === !wasmBytes) throw new OpError("bad_arguments", "init takes wasmUrl or wasmBytes (one of them)");
  const bytes = wasmBytes ? wasmBytes : await fetchWasm(String(wasmUrl), { fetch: env.fetch, sha256 });
  env.state = null;
  const V = await loadVina(bytes, { sha256, factory: env.factory });
  const info = {
    engine: { ...ENGINE },
    wasm: { sha256: VINA.wasm.sha256, bytes: VINA.wasm.bytes, source: wasmBytes ? "bytes" : String(wasmUrl) }, // as loadVina checked
  };
  env.state = { V, info, broken: null };
  return info;
}

function engineOf(env) {
  if (!env.state) throw new OpError("not_initialised", "Vina is not started: send init first");
  if (env.state.broken) {
    throw new OpError("vina", `AutoDock Vina stopped working earlier in this worker (${env.state.broken}); init starts a fresh module`);
  }
  return env.state.V;
}

/** One run of `mode` → {texts' digests, params, energies…, seconds} in the result's key order (or error). */
async function run(mode, payload, env) {
  const V = engineOf(env);
  const { receptor, ligand } = payload || {};
  const params = vinaParams(mode, payload?.params);
  const t0 = env.now();
  let r;
  try {
    r = runVina(V, mode, receptor, ligand, params);
  } catch (err) {
    if (err instanceof VinaError && err.broken) env.state.broken = err.message;
    throw err;
  }
  const seconds = Math.round(env.now() - t0) / 1000;
  const head = { receptor_sha256: await sha256Hex(receptor), ligand_sha256: await sha256Hex(ligand), params, engine: { ...ENGINE } };
  if (!r.ok) return { head, body: { error: String(r.error).trim() }, seconds }; // Vina wraps its message in blank lines
  const body = mode === "dock" ? { energies: r.energies, poses: r.poses }
    : mode === "optimize" ? { energies: r.energies, pose: r.pose } : { energies: r.energies };
  return { head, body, seconds };
}

async function dock(payload, env) {
  const key = payload?.key;
  if (typeof key !== "string" || !KEY.test(key)) {
    throw new OpError("bad_arguments", `dock: key is "reference" or "ligand-<index>" (got ${JSON.stringify(key)})`);
  }
  const { head, body, seconds } = await run("dock", payload, env);
  const doc = { schema: RESULT_SCHEMA, key, ...head, ...body, seconds };
  return { ...doc, result_sha256: await sha256Hex(JSON.stringify(doc)) };
}

async function single(mode, payload, env) {
  const { head, body, seconds } = await run(mode, payload, env);
  return { mode, ...head, ...body, seconds };
}

/** One request → the reply. Never throws. */
export async function handle(msg, env) {
  const t0 = env.now();
  const { id, op, payload } = msg || {};
  const ms = () => Math.round(env.now() - t0);
  try {
    let result;
    if (op === "init") result = { ...(await init(payload, env)), ms: ms() };
    else if (op === "dock") result = await dock(payload, env);
    else if (op === "score" || op === "optimize") result = await single(op, payload, env);
    else if (op === "info") result = env.state ? env.state.info : null;
    else throw new OpError("bad_arguments", `no op ${JSON.stringify(op)} here (init, dock, score, optimize, info)`);
    return { id, ok: true, ms: ms(), result };
  } catch (err) {
    return { id, ok: false, ms: ms(), error: errorOf(err) };
  }
}

/** The message loop of a worker scope (self, or a test's stand-in). */
export function serve(scope, env) {
  let chain = Promise.resolve();
  scope.onmessage = (ev) => {
    const msg = ev.data;
    if (!msg || typeof msg !== "object" || msg.id === undefined || msg.id === null) return;
    chain = chain.then(async () => scope.postMessage(await handle(msg, env))).catch(() => {});
  };
}

function isWorkerMain() {
  if (typeof self === "undefined" || typeof WorkerGlobalScope === "undefined" || !(self instanceof WorkerGlobalScope)) return false;
  const bare = (u) => String(u).split("#")[0];
  return bare(self.location?.href) === bare(import.meta.url);
}

if (isWorkerMain()) {
  serve(self, workerEnv());
}
