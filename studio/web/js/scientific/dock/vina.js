// AutoDock Vina 1.2.7 for the browser docking (studio/docs/BROWSER_JOBS.md, docking): the WebAssembly build that
// studio/scripts/build_vina_wasm.py makes from pinned inputs, started only from wasm bytes whose SHA-256 is the pin.
//
// The loader (vendor/vina-1.2.7.mjs, Emscripten's ES module: export default createVina) and the wasm
// (vendor/vina-1.2.7.wasm) are the build's outputs, vendored as built; vina_build.json records the pins, flags and
// every input's and output's digest, and the build's --check rebuilds them and compares, byte for byte. Vina's and
// Boost's licences are vendor/LICENSES-vina.txt. The binding (studio/vina-wasm/vina_wasm.cpp) has one function,
// run(mode, receptor, ligand, params): Vina's own C++ calls (score, optimize, or global_search + get_poses), with cpu 1
// (the build has no threads) and the seed given; it never throws for bad input, it answers {ok: false, error}.
// The compiled module reaches the loader through instantiateWasm, so the bytes checked here are the bytes that run
// (the loader would otherwise fetch vina-1.2.7.wasm itself, from next to itself, unchecked).

import { Sha256 } from "../data/digest.js";

// The pin. Paths are the site's (boot.json's vina entry repeats them); the licence's is this directory's.
// web/test/scientific/vina.test.mjs holds it equal to vina_build.json's outputs.
export const VINA = Object.freeze({
  version: "1.2.7",
  loader: "js/scientific/dock/vendor/vina-1.2.7.mjs",
  wasm: Object.freeze({
    url: "js/scientific/dock/vendor/vina-1.2.7.wasm",
    sha256: "94807d4aeb358234fc4506ddd4c61b050d6b056fac8da412798eaf615ef09232",
    bytes: 492557,
  }),
  licence: "vendor/LICENSES-vina.txt",
});

/** The engine as a result and the receipt name it. */
export const ENGINE = Object.freeze({ name: "AutoDock Vina", version: VINA.version, build: "wasm", wasm_sha256: VINA.wasm.sha256 });

/** The schema of one Vina run's result file (results/<key>.json; bioagent.docking.prepared.PrecomputedVina reads it). */
export const RESULT_SCHEMA = "tcmstudio.vina-result/1";

/** What native bioagent.docking.prepared fixes and the pipeline's config bounds (as the runner's _dock_prepare). */
export const SEARCH = Object.freeze({
  spacing: 0.375, min_rmsd: 1.0, max_evals: 0,
  exhaustiveness: Object.freeze([1, 64]), n_poses: Object.freeze([1, 20]), seed: Object.freeze([1, 2 ** 31 - 1]),
  scoring: Object.freeze(["vina", "vinardo"]),
});

// this file is <site>/js/scientific/dock/vina.js: the pin's paths are relative to <site>/
const SITE = new URL("../../../", import.meta.url);

/** An argument, a wasm or a run the browser docking cannot take. `code`: "bad_wasm" (bytes that are not the pinned
 * build, or that could not be fetched), "bad_arguments" (a call this code refuses before Vina sees it), "vina" (the
 * engine itself failed: it did not start, or a run trapped; its memory is no longer trusted). Vina refusing an input
 * (its parse errors) is not an error here: run answers {ok: false, error} with Vina's message. */
export class VinaError extends Error {
  constructor(code, message, details = {}) {
    super(message);
    this.name = "VinaError";
    this.code = code;
    Object.assign(this, details);
  }
}

const HEX = Array.from({ length: 256 }, (_, i) => i.toString(16).padStart(2, "0"));
const utf8 = new TextEncoder();

/** SHA-256 of `input` (a string, as UTF-8, or bytes) as lower-case hex: Web Crypto where the context has it, else
 * ../data/digest.js. */
export async function sha256Hex(input) {
  let view;
  if (typeof input === "string") view = utf8.encode(input);
  else if (input instanceof Uint8Array) view = input;
  else if (ArrayBuffer.isView(input)) view = new Uint8Array(input.buffer, input.byteOffset, input.byteLength);
  else view = new Uint8Array(input);
  if (globalThis.crypto?.subtle) {
    const d = new Uint8Array(await globalThis.crypto.subtle.digest("SHA-256", view));
    let s = "";
    for (const b of d) s += HEX[b];
    return s;
  }
  return new Sha256().update(view).digestHex();
}

/** `bytes` (ArrayBuffer or a view), checked to be the pinned vina-1.2.7.wasm → Uint8Array. `sha256` is what the caller
 * expects; anything but the pin is refused before the bytes are looked at, since this code is written for that one
 * build. */
export async function checkWasm(bytes, sha256 = VINA.wasm.sha256) {
  if (String(sha256 || "").toLowerCase() !== VINA.wasm.sha256) {
    throw new VinaError("bad_wasm", `the browser docking is pinned to AutoDock Vina ${VINA.version} (vina-1.2.7.wasm sha256 ` +
      `${VINA.wasm.sha256}); it cannot run a build with sha256 ${sha256 || "(none given)"}`);
  }
  let view;
  if (bytes instanceof Uint8Array) view = bytes;
  else if (ArrayBuffer.isView(bytes)) view = new Uint8Array(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  else if (bytes instanceof ArrayBuffer) view = new Uint8Array(bytes);
  else throw new VinaError("bad_wasm", "vina-1.2.7.wasm must be given as bytes (an ArrayBuffer or a typed array)");
  if (view.byteLength !== VINA.wasm.bytes) {
    throw new VinaError("bad_wasm", `vina-1.2.7.wasm is ${view.byteLength} bytes; the pinned build's is ${VINA.wasm.bytes}`);
  }
  const got = await sha256Hex(view);
  if (got !== VINA.wasm.sha256) {
    throw new VinaError("bad_wasm", `vina-1.2.7.wasm has sha256 ${got}, not the pinned build's ${VINA.wasm.sha256}: it is not used`);
  }
  return view;
}

/** The wasm from `url` (default the pin's; a relative URL is the site's, as boot.json writes it, wherever this runs),
 * checked → Uint8Array. Nothing else is tried when it fails. */
export async function fetchWasm(url = VINA.wasm.url, { fetch = globalThis.fetch, signal, sha256 = VINA.wasm.sha256 } = {}) {
  if (typeof fetch !== "function") throw new VinaError("bad_wasm", "no fetch here to download vina-1.2.7.wasm");
  const href = new URL(String(url), SITE).href;
  let res;
  try {
    res = await fetch(href, { signal, credentials: "same-origin" });
  } catch (err) {
    if (err?.name === "AbortError") throw err;
    throw new VinaError("bad_wasm", `vina-1.2.7.wasm could not be downloaded from ${href}: ${err?.message || err}`);
  }
  if (!res.ok) throw new VinaError("bad_wasm", `vina-1.2.7.wasm: ${href} answered HTTP ${res.status}`);
  return checkWasm(await res.arrayBuffer(), sha256);
}

const quiet = () => {};

/**
 * Vina started from `wasmBytes` (checked against the pin first) → the module (its run is the binding's). `factory` is
 * the loader's createVina (default: the vendored ES module). Vina's stdout and stderr are silenced (the binding asks
 * for verbosity 0; what is left is the loader's own noise).
 */
export async function loadVina(wasmBytes, { sha256 = VINA.wasm.sha256, factory = null } = {}) {
  const bytes = await checkWasm(wasmBytes, sha256);
  const compiled = await WebAssembly.compile(bytes);
  const init = factory || (await import("./vendor/vina-1.2.7.mjs")).default;
  let failed;
  const instantiateFailed = new Promise((_, reject) => { failed = reject; });
  const V = await Promise.race([
    init({
      print: quiet,
      printErr: quiet,
      // the loader awaits the callback forever when instantiation throws, so a failure is raced against it
      instantiateWasm(imports, receive) {
        WebAssembly.instantiate(compiled, imports).then((instance) => receive(instance), (err) => failed(err));
        return {};
      },
    }),
    instantiateFailed,
  ]).catch((err) => {
    throw err instanceof VinaError ? err : new VinaError("vina", `AutoDock Vina ${VINA.version} did not start: ${err?.message || err}`);
  });
  if (typeof V?.run !== "function") {
    throw new VinaError("vina", `the loader started a module without the binding's run: not the pinned AutoDock Vina ${VINA.version} build`);
  }
  return V;
}

const bad = (message) => new VinaError("bad_arguments", message);

function whole(p, key, [lo, hi], fallback) {
  const x = p[key] ?? fallback;
  if (!Number.isInteger(x) || x < lo || x > hi) throw bad(`${key}: a whole number from ${lo} to ${hi} (got ${JSON.stringify(p[key])})`);
  return x;
}

function positive(p, key, fallback, { zero = false } = {}) {
  const x = p[key] ?? fallback;
  if (typeof x !== "number" || !Number.isFinite(x) || x < 0 || (!zero && x === 0)) {
    throw bad(`${key}: a finite number ${zero ? "≥" : ">"} 0 (got ${JSON.stringify(p[key])})`);
  }
  return x;
}

function triple(p, key, { positive: pos = false } = {}) {
  const v = p[key];
  if (!Array.isArray(v) || v.length !== 3 || !v.every((x) => typeof x === "number" && Number.isFinite(x) && (!pos || x > 0))) {
    throw bad(`${key}: three finite numbers (x, y, z; Å${pos ? "; each > 0" : ""}) (got ${JSON.stringify(v)})`);
  }
  return [v[0], v[1], v[2]];
}

const INT32 = [0, 2 ** 31 - 1];

/**
 * A run's parameters, checked → the plain object a result records, in the order bioagent.docking.prepared's
 * PrecomputedVina compares (floats exactly, so nothing here rounds). `mode` "dock": {scoring, exhaustiveness, n_poses,
 * seed, energy_range, center, size, spacing, min_rmsd, max_evals}, every one required but the last three (native's
 * 0.375, 1.0 and 0). "score" | "optimize": {scoring, seed, center, size, spacing} (+ max_steps for optimize, default 0:
 * Vina's own count); the seed (default 42, the pipeline's) seeds a generator score and optimize never draw from.
 */
export function vinaParams(mode, params) {
  const p = params && typeof params === "object" && !Array.isArray(params) ? params : null;
  if (!p) throw bad("params: an object");
  if (!SEARCH.scoring.includes(p.scoring)) throw bad(`scoring: ${SEARCH.scoring.join(" or ")} (got ${JSON.stringify(p.scoring)})`);
  if (mode === "dock") {
    return {
      scoring: p.scoring,
      exhaustiveness: whole(p, "exhaustiveness", SEARCH.exhaustiveness),
      n_poses: whole(p, "n_poses", SEARCH.n_poses),
      seed: whole(p, "seed", SEARCH.seed),
      energy_range: positive(p, "energy_range"),
      center: triple(p, "center"),
      size: triple(p, "size", { positive: true }),
      spacing: positive(p, "spacing", SEARCH.spacing),
      min_rmsd: positive(p, "min_rmsd", SEARCH.min_rmsd, { zero: true }),
      max_evals: whole(p, "max_evals", INT32, SEARCH.max_evals),
    };
  }
  if (mode === "score" || mode === "optimize") {
    const out = {
      scoring: p.scoring, seed: whole(p, "seed", SEARCH.seed, 42),
      center: triple(p, "center"), size: triple(p, "size", { positive: true }), spacing: positive(p, "spacing", SEARCH.spacing),
    };
    if (mode === "optimize") out.max_steps = whole(p, "max_steps", INT32, 0);
    return out;
  }
  throw bad(`mode ${JSON.stringify(mode)}: score, optimize or dock`);
}

/**
 * One Vina run on module `V` → the binding's answer: {ok: true, seed, energies, poses? (dock), pose? (optimize)} or
 * {ok: false, error: Vina's message} when Vina refuses the input. `params` as vinaParams returns them (checked again
 * here). A run that throws (a trap, or anything the binding did not catch) is VinaError "vina" with `broken` set: the
 * module's memory is not trusted after it. So is one that ran out of memory (std::bad_alloc; `memory` set too).
 */
export function runVina(V, mode, receptor, ligand, params) {
  const p = vinaParams(mode, params);
  for (const [name, text] of [["receptor", receptor], ["ligand", ligand]]) {
    if (typeof text !== "string" || !text.trim()) throw bad(`${name}: the PDBQT text Vina reads (a non-empty string)`);
  }
  const args = {
    sf: p.scoring, seed: p.seed, center: p.center, size: p.size, spacing: p.spacing,
    exhaustiveness: p.exhaustiveness ?? 8, n_poses: p.n_poses ?? 9, min_rmsd: p.min_rmsd ?? SEARCH.min_rmsd,
    max_evals: p.max_evals ?? SEARCH.max_evals, energy_range: p.energy_range ?? 3.0, max_steps: p.max_steps ?? 0,
  };
  let r;
  try {
    r = V.run(mode, receptor, ligand, args);
  } catch (err) {
    throw new VinaError("vina", `AutoDock Vina ${VINA.version} failed during ${mode} (${err?.message || err}); this module is not used again`,
      { broken: true });
  }
  if (!r || typeof r !== "object" || typeof r.ok !== "boolean") throw new VinaError("vina", `the binding's run answered ${JSON.stringify(r)}`, { broken: true });
  if (r.ok && r.seed !== p.seed) throw new VinaError("vina", `Vina ran with seed ${r.seed}, not the ${p.seed} it was given`, { broken: true });
  // the binding catches std::bad_alloc with Vina's own errors; running out of memory is not Vina refusing the input
  if (!r.ok && /\bbad_alloc\b/.test(String(r.error))) {
    throw new VinaError("vina", `AutoDock Vina ${VINA.version} ran out of WebAssembly memory during ${mode} (${String(r.error).trim()}); ` +
      "this module is not used again", { broken: true, memory: true });
  }
  return r;
}

/** The exact text a result file holds (and its result_sha256 is of): the worker's result without its result_sha256,
 * as JSON.stringify writes it (keys in the worker's order; no trailing newline). */
export function vinaResultText(result) {
  const { result_sha256: _sha, ...doc } = result;
  return JSON.stringify(doc);
}
