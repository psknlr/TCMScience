// ADMET in this browser (studio/docs/BROWSER_JOBS.md): the runner's pipeline.admet and admet.models
// (studio/runner/src/tcmstudio/kinds.py) run here as bioagent.admet itself, not as another model. The same Python does
// the work in a job worker holding Pyodide's numpy, scipy and scikit-learn (tcmstudio.browser_jobs admet_split,
// admet_train, admet_read, run_admet, verify_pipeline); the chemistry it would do with RDKit is done in this page's
// chemistry worker (../chem/chem.worker.js: RDKit.js 2026.09.1 and bioagent's ports, held to native RDKit on a parity
// corpus) and handed to Python as prepared sets (../chem/prepared-set.js, bioagent.admet.engine.PrecomputedEngine);
// the applicability-domain search runs on this device's kernels (../../compute/science-scheduler.js: the GPU only when
// this device's own calibration measured it faster, else WebAssembly SIMD) and is handed over as similarities, which
// Python checks before it uses them (PrecomputedSimilarity) and recomputes on its CPU when they do not hold.
//
// Nothing trained is shipped: admet.models builds the models here, from the TDC ADMET group archive (fetched by this
// browser through the site's dataset relay, registry group "admet", or given as a project file), into this origin's
// private storage, where every later pipeline.admet reads them.
//
// Storage (OPFS, under tcmstudio/):
//   admet/archive/<sha12>.zip     the archive, kept once its SHA-256 is known (the bytes are hashed as they are copied)
//   admet/models/                 the models every job here shares: model_cards.json, <endpoint>.pkl,
//                                 <endpoint>_domain.npy, exactly what bioagent's build_models writes
//   jobs/<id>/split/<ep>/         an endpoint's train_val.json and test.json ({smiles, y}, the archive's order)
//   jobs/<id>/prepared/<ep>/<split>/   the prepared set of that split (removed once its model is in admet/models)
//   jobs/<id>/models/<ep>/        what admet_train wrote for the endpoint, before it is copied into admet/models
//   jobs/<id>/{inputs,read,prepared,similarity,out}/   a pipeline.admet run's files
//
// admet.models: archive → split → for each endpoint: chemistry.<ep> → train.<ep> → verify. An endpoint whose model in
// admet/models was built from the same archive with the same settings, scikit-learn and chemistry, and whose files
// still re-hash to its card's digests, is kept (both its stages reused); a resumed job also reuses an endpoint's
// prepared sets while they re-hash to their checkpoint. A molecule the browser chemistry refuses (a standardisation
// step it does not reproduce exactly) is left out of training and counted on the card (dropped_unstandardisable), so a
// model built here can differ from a runner's: each card says the chemistry it was built with.
//
// pipeline.admet: inputs → read → chemistry → domain → analysis → verify. Refused before anything is queued: an SDF or
// MOL file (RDKit's SDMolSupplier is not ported: the runner), models of the endpoints asked for that are not built here
// (job.admet.models builds them), more molecules than a run here takes (memory).

import { RDKIT } from "../chem/rdkit.js";
import { ENGINE, PREPARED_SCHEMA, jsonRow, smilesSha256 } from "../chem/prepared-set.js";
import { N_BITS_BYTES, N_FEATURES } from "../chem/features.js";
import { currentCalibration } from "../../compute/calibration.js";
import { packedWords, tanimotoRows } from "../../compute/science-cpu.js";
import { ScienceGPU } from "../../compute/science-gpu.js";
import { SCIENCE_PREFERENCES, runTanimoto } from "../../compute/science-scheduler.js";
import { abortError, canonicalJson, isPlainObject, sha256Hex } from "../../core/util.js";
import { Sha256 } from "../data/digest.js";
import { bareSha256 } from "../engines/evidence.js";
import {
  digestMismatch, limitationLines, memoryRefusal, projectFile, pythonCall, refusal, safeName, storageRefusal, work,
} from "./shared.js";

/** bioagent.admet.models.ENDPOINTS, in its order: the 22 endpoints of the TDC ADMET benchmark group. */
export const ENDPOINTS = Object.freeze([
  "caco2_wang", "hia_hou", "pgp_broccatelli", "bioavailability_ma", "lipophilicity_astrazeneca", "solubility_aqsoldb",
  "bbb_martins", "ppbr_az", "vdss_lombardo", "cyp2c9_veith", "cyp2d6_veith", "cyp3a4_veith",
  "cyp2c9_substrate_carbonmangels", "cyp2d6_substrate_carbonmangels", "cyp3a4_substrate_carbonmangels",
  "half_life_obach", "clearance_hepatocyte_az", "clearance_microsome_az", "ld50_zhu", "herg", "ames", "dili",
]);
/** bioagent.admet.models.TDC_ADMET_SHA256: the archive the models' published splits are pinned to. */
export const TDC_ADMET_SHA256 = "bd0005246cb6f1672333c28ad202112a8bf071ab65d847848715030308faf1f1";
/** Pyodide packages the models import (beyond the standard library). */
const PACKAGES = ["numpy", "scipy", "scikit-learn"];
/** tcmstudio.governance.LIMIT_PREDICTED: what the runner adds to every predictive job's result. */
export const LIMIT_PREDICTED = "Predicted ≠ measured: a computational prediction or network inference supports a mechanism hypothesis at most, never a mechanism or efficacy.";
/**
 * The most molecules one pipeline.admet run takes here: tcmstudio.browser_jobs.ADMET_MOLECULES_MAX, the bound Python
 * itself holds a run in a job worker to (it refuses more with need "memory", measured there); the page refuses such a
 * run before it is queued, counting a file's lines as Python counts them (web/test/scientific/admet.test.mjs holds the
 * two numbers to each other).
 */
export const MAX_MOLECULES = 20_000;
/** Molecules per call to the chemistry worker: its progress and a cancel reach the page between calls. */
const BATCH = 2000;
/** The runner's bounds for what it passes to build_models (tcmstudio.kinds). */
const SEED = [0, 2 ** 31 - 1];
const MAX_ITER = [10, 5000];
/** The ADMET kinds' molecules: a project file, or one SMILES as the runner's kind takes it (tcmstudio.kinds._SMILES). */
const SMILES = /^[A-Za-z0-9@+\-[\]()=#$%/\\.:*~]{1,2000}$/;
const ENDPOINT_NAME = /^[A-Za-z0-9_.]{1,64}$/;
/** What names a molecule file rather than a molecule (tcmstudio.browser_jobs admet_read). */
const FILE_SUFFIX = /\.(sdf|sdf\.gz|mol|csv|tsv|smi|txt)$/i;
/** What RDKit's SDMolSupplier reads (bioagent.docking.prep.read_ligands), which the browser chemistry does not port. */
export const SDF_SUFFIXES = Object.freeze([".sdf", ".sdf.gz", ".mol"]);
const RUNNER = "Connect the local runner";
const MODELS_LOCK = "tcmstudio.admet.models";
const BUILD_HINT = "Build them first in this browser with job.admet.models (it gets the TDC ADMET group archive through the site's relay, or reads it from a project file, and trains the models here), or connect the local runner.";

const given = (v) => v !== undefined && v !== null && v !== "";
const present = (v) => v !== undefined && v !== null;
const sha12 = (sha) => String(sha).slice(0, 12);
const plural = (n, one, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;
const LITTLE = new Uint8Array(new Uint16Array([1]).buffer)[0] === 1;

// ------------------------------------------------------------------------------------------------- storage layout

/** tcmstudio/admet: the archive and the models every ADMET job here shares. */
export const admetDir = (store) => [...(store?.base || ["tcmstudio"]), "admet"];
export const modelsDir = (store) => [...admetDir(store), "models"];
const archivePath = (store, sha) => [...admetDir(store), "archive", `${sha12(sha)}.zip`];

/**
 * The model cards of admet/models (model_cards.json), or {} when no model was built here. A file that is not JSON is
 * treated as no models, and the next build writes it again.
 */
export async function readCards(store) {
  if (!store) return {};
  let doc = null;
  try {
    doc = await store.readJson([...modelsDir(store), "model_cards.json"]);
  } catch {
    return {};
  }
  return isPlainObject(doc) ? doc : {};
}

/**
 * The ADMET models built in this browser → {endpoints: [their names, in ENDPOINTS order], cards} when at least one is,
 * else null. A card counts when its model and domain files are there (their digests are checked where they are used).
 */
export async function admetModelsBuilt(store) {
  const cards = await readCards(store);
  const endpoints = [];
  for (const e of ENDPOINTS) {
    const c = cards[e];
    if (!isPlainObject(c) || typeof c.model_file !== "string" || typeof c.domain_file !== "string") continue;
    const there = await Promise.all([c.model_file, c.domain_file].map((f) => store.stat([...modelsDir(store), f]).catch(() => null)));
    if (there.every((st) => st?.kind === "file")) endpoints.push(e);
  }
  return endpoints.length ? { endpoints, cards: Object.fromEntries(endpoints.map((e) => [e, cards[e]])) } : null;
}

/** The chemistry a card built here names (bioagent.admet.engine.identity of the browser's engine). */
export const CHEMISTRY = Object.freeze({ engine: ENGINE.name, version: ENGINE.version, detail: ENGINE.detail });

// ------------------------------------------------------------------------------------------------- arguments

function bad(message) {
  return refusal("bad_arguments", message, "arguments");
}

function checkKnown(kind, allowed, p) {
  const extra = Object.keys(p).filter((k) => present(p[k]) && !allowed.includes(k)).sort();
  if (extra.length) throw bad(`unknown parameter${extra.length > 1 ? "s" : ""} ${extra.join(", ")}; ${kind} takes ${allowed.join(", ")}`);
}

function checkInt(name, value, [low, high]) {
  if (typeof value !== "number" || !Number.isInteger(value)) throw bad(`${name}: a whole number is needed, not ${JSON.stringify(value)}`);
  if (value < low || value > high) throw bad(`${name}: between ${low} and ${high}, not ${value}`);
  return value;
}

/** endpoints as the runner's kind takes them, each one of the 22 → the list (empty: every one). */
function checkEndpoints(value) {
  if (!present(value)) return [];
  if (!Array.isArray(value)) throw bad("endpoints: a list of endpoint names");
  const out = [];
  for (const e of value) {
    if (typeof e !== "string" || !ENDPOINT_NAME.test(e.trim())) throw bad(`endpoints: ${JSON.stringify(e)} is not an endpoint name`);
    const name = e.trim();
    if (!ENDPOINTS.includes(name)) throw bad(`endpoints: no endpoint ${JSON.stringify(name)}; the TDC ADMET group has ${ENDPOINTS.join(", ")}`);
    if (!out.includes(name)) out.push(name);
  }
  return out;
}

/** The molecules a pipeline.admet call names, from its value alone: a file whose suffix only the runner reads. */
export function admetNeedsRunner(params) {
  const p = isPlainObject(params) ? params : {};
  const m = typeof p.molecules === "string" ? p.molecules.trim().toLowerCase() : "";
  if (m && SDF_SUFFIXES.some((s) => m.endsWith(s))) return sdfNeed();
  return null;
}

function sdfNeed() {
  return {
    message: "molecules: an SDF or MOL file is read with RDKit's SDMolSupplier, which this browser's chemistry (RDKit.js and its ports) does not port; CSV, TSV or SMI files and single SMILES are read here",
    hint: `${RUNNER}: it reads SDF and MOL files with native RDKit. Or give the molecules as a CSV (name,smiles), TSV or SMI file.`,
  };
}

function runnerRefusal(why) {
  return refusal("unavailable", why.message, "runner", why.hint);
}

// ------------------------------------------------------------------------------------------------- the chemistry worker

function cancelled() {
  const e = abortError("the job was cancelled");
  try { e.type = "cancelled"; } catch { /* a frozen DOMException */ }
  return e;
}

/**
 * The page's side of ../chem/chem.worker.js: one module worker, RDKit.js started in it from the pinned wasm (the URL
 * and SHA-256 boot.json's rdkit entry names; nothing else is tried), molecules prepared in batches with progress, a
 * cancel reaching the worker between molecules.
 */
export class ChemClient {
  #Worker; #url; #wasmUrl; #sha256;
  #worker = null; #seq = 0; #pending = new Map(); #info = null; #interrupt = null; #startP = null;

  /** Worker: the Worker constructor; url: chem.worker.js; wasm: {url, sha256} of RDKit_minimal.wasm. */
  constructor({ Worker = globalThis.Worker, url, wasm = RDKIT.wasm } = {}) {
    this.#Worker = Worker;
    this.#url = url;
    this.#wasmUrl = wasm?.url || RDKIT.wasm.url;
    this.#sha256 = wasm?.sha256 || RDKIT.wasm.sha256;
  }

  /** What init reported ({engine, rdkit, wasm, catalogs, ms}), once started. */
  get info() { return this.#info; }

  /** Start the worker and RDKit.js in it (once) → init's result. */
  start({ signal } = {}) {
    if (this.#info) return Promise.resolve(this.#info);
    this.#startP ??= (async () => {
      if (typeof this.#Worker !== "function") throw chemError("unavailable", "this browser cannot start the chemistry worker (no Web Worker)");
      try {
        this.#worker = new this.#Worker(this.#url, { type: "module", name: "tcmstudio-chem" });
      } catch (err) {
        throw chemError("unavailable", `the chemistry worker could not start (${err?.message || err})`);
      }
      this.#worker.onmessage = (ev) => this.#onMessage(ev.data);
      this.#worker.onerror = (ev) => this.#crash(`the chemistry worker failed: ${ev?.message || "an error it did not report"}`);
      const sab = globalThis.crossOriginIsolated && typeof SharedArrayBuffer === "function" ? new SharedArrayBuffer(1) : null;
      this.#interrupt = sab ? new Uint8Array(sab) : null;
      if (sab) this.#worker.postMessage({ type: "interrupt-buffer", buffer: sab });
      this.#info = await this.#call("init", { wasmUrl: this.#wasmUrl, sha256: this.#sha256 }, { signal });
      return this.#info;
    })().catch((err) => {
      this.#startP = null;
      this.terminate();
      throw err;
    });
    return this.#startP;
  }

  /**
   * `smiles` prepared in batches → {rows (one per input, in order), features: [Float32Array] (the ok rows', in order,
   * in pieces), bits: [Uint8Array], n, n_ok, ms}. onProgress(done, total) after each batch.
   */
  async prepareAll(smiles, { alerts = true, signal, onProgress } = {}) {
    await this.start({ signal });
    const rows = [];
    const features = [];
    const bits = [];
    let nOk = 0;
    const t0 = Date.now();
    for (let at = 0; at < smiles.length; at += BATCH) {
      if (signal?.aborted) throw cancelled();
      const part = smiles.slice(at, at + BATCH);
      const r = await this.#call("prepare", { molecules: part.map((s, i) => ({ name: String(at + i), smiles: s })), alerts }, { signal });
      if (!Array.isArray(r?.rows) || r.rows.length !== part.length) throw chemError("runtime_error", "the chemistry worker answered a batch with another number of rows");
      for (const row of r.rows) rows.push(row);
      features.push(r.features);
      bits.push(r.bits);
      nOk += r.n_ok;
      onProgress?.(Math.min(smiles.length, at + part.length), smiles.length);
    }
    return { rows, features, bits, n: smiles.length, n_ok: nOk, ms: Date.now() - t0 };
  }

  terminate() {
    const w = this.#worker;
    this.#worker = null;
    this.#info = null;
    if (w) { try { w.terminate(); } catch { /* gone */ } }
    for (const p of this.#pending.values()) p.reject(chemError("runtime_error", "the chemistry worker was stopped before it answered"));
    this.#pending.clear();
  }

  #call(op, payload, { signal } = {}) {
    const worker = this.#worker;
    if (!worker) return Promise.reject(chemError("runtime_error", "the chemistry worker is not running"));
    const id = ++this.#seq;
    if (this.#interrupt) Atomics.store(this.#interrupt, 0, 0);
    return new Promise((resolve, reject) => {
      const onAbort = () => {
        if (this.#interrupt) Atomics.store(this.#interrupt, 0, 1);
        worker.postMessage({ type: "cancel", id });
      };
      this.#pending.set(id, {
        resolve: (v) => { signal?.removeEventListener?.("abort", onAbort); resolve(v); },
        reject: (e) => { signal?.removeEventListener?.("abort", onAbort); reject(e); },
        signal,
      });
      signal?.addEventListener?.("abort", onAbort, { once: true });
      worker.postMessage({ id, op, payload });
      if (signal?.aborted) onAbort();
    });
  }

  #onMessage(msg) {
    if (!msg || typeof msg !== "object" || msg.type === "event") return;
    const p = this.#pending.get(msg.id);
    if (!p) return;
    this.#pending.delete(msg.id);
    if (msg.ok) { p.resolve(msg.result); return; }
    const e = msg.error || {};
    if (e.type === "cancelled" || p.signal?.aborted) { p.reject(cancelled()); return; }
    p.reject(chemOpError(e));
  }

  #crash(message) {
    const pending = [...this.#pending.values()];
    this.#pending.clear();
    this.terminate();
    for (const p of pending) p.reject(chemError("runtime_error", message));
  }
}

function chemError(type, message, extra = {}) {
  const e = new Error(message);
  e.type = type;
  return Object.assign(e, extra);
}

/** The chemistry worker's error as the job says it: a wasm that is not the pin, or one that could not be fetched, is
 * the chemistry being unavailable here; RDKit.js failing is a failure of the run. */
function chemOpError(e) {
  const message = `the browser chemistry: ${e.message || e.type || "failed"}`;
  if (e.type === "bad_wasm") {
    return chemError("unavailable", message, {
      need: "network",
      hint: "The browser chemistry loads RDKit.js's wasm (7.4 MB, its exact pinned version) once; allow the site to reach it, or connect the local runner (native RDKit).",
    });
  }
  if (e.type === "rdkit") return chemError("runtime_error", `${message} (RDKit.js itself failed; its memory is no longer trusted, so the job stopped)`);
  if (e.type === "too_large") return chemError("refused", message, { need: "memory" });
  return chemError("runtime_error", message);
}

/** The wasm the chemistry loads: boot.json's rdkit entry when the site names one (checked to be this code's pin). */
function rdkitWasm(rdkit) {
  if (!rdkit) return { url: RDKIT.wasm.url, sha256: RDKIT.wasm.sha256, bytes: RDKIT.wasm.bytes, from: "pinned" };
  const sha = bareSha256(rdkit.sha256);
  return { url: typeof rdkit.url === "string" && rdkit.url ? rdkit.url : RDKIT.wasm.url, sha256: sha, bytes: rdkit.bytes ?? null, version: rdkit.version ?? null, from: "boot.json" };
}

/** Why the site's RDKit.js pin is not the one this page's chemistry is written for, or null. */
function rdkitMismatch(wasm) {
  if (wasm.sha256 !== RDKIT.wasm.sha256 || (wasm.bytes !== null && wasm.bytes !== RDKIT.wasm.bytes) || (wasm.version && wasm.version !== RDKIT.version)) {
    return `the site's boot.json pins RDKit.js ${wasm.version ?? "?"} (wasm sha256 ${String(wasm.sha256).slice(0, 12)}…), but this page's chemistry is written for RDKit.js ${RDKIT.version} (${RDKIT.wasm.sha256.slice(0, 12)}…): reload the page`;
  }
  return null;
}

// ------------------------------------------------------------------------------------------------- files on disk

/** Float32 values as little-endian bytes (the view itself on a little-endian machine). */
function leFloat32(f) {
  if (LITTLE) return new Uint8Array(f.buffer, f.byteOffset, f.byteLength);
  const out = new Uint8Array(f.length * 4);
  const view = new DataView(out.buffer);
  for (let i = 0; i < f.length; i++) view.setFloat32(4 * i, f[i], true);
  return out;
}

/** `pieces` written as one file at `path`, hashed as written → {bytes, sha256}. */
async function writePieces(store, path, pieces, signal) {
  const h = new Sha256();
  const bytes = await store.writeBlob(path, new Blob(pieces), { signal, onChunk: (b) => h.update(b) });
  return { bytes, sha256: h.digestHex() };
}

/**
 * The prepared set of `smiles` (what ChemClient.prepareAll returned for them) in the format bioagent's
 * PrecomputedEngine reads (../chem/prepared-set.js: prepared.json, features.f32, bits.u8) at `dir` → [{path, bytes,
 * sha256}] of the three files (path relative to the job's directory when `rel` is given).
 */
async function writePrepared(store, dir, smiles, prep, { signal, rel = null } = {}) {
  if (prep.rows.length !== smiles.length) throw new Error(`a prepared set needs one row per input (${smiles.length} inputs, ${prep.rows.length} rows)`);
  const nOk = prep.rows.filter((r) => r.ok).length;
  const nf = prep.features.reduce((a, f) => a + f.length, 0);
  const nb = prep.bits.reduce((a, b) => a + b.length, 0);
  if (nf !== nOk * N_FEATURES || nb !== nOk * N_BITS_BYTES) throw new Error(`the prepared arrays do not hold the ${nOk} prepared rows (${nf} features, ${nb} bit bytes)`);
  const doc = { schema: PREPARED_SCHEMA, engine: { ...ENGINE }, n: smiles.length, smiles_sha256: smilesSha256(smiles.map(String)), rows: prep.rows.map(jsonRow) };
  const text = `${JSON.stringify(doc)}\n`;
  await store.writeText([...dir, "prepared.json"], text);
  const files = [{ name: "prepared.json", bytes: new TextEncoder().encode(text).length, sha256: await sha256Hex(text) }];
  files.push({ name: "features.f32", ...(await writePieces(store, [...dir, "features.f32"], prep.features.map(leFloat32), signal)) });
  files.push({ name: "bits.u8", ...(await writePieces(store, [...dir, "bits.u8"], prep.bits, signal)) });
  return files.map((f) => ({ path: [...(rel || dir), f.name], bytes: f.bytes, sha256: f.sha256 }));
}

/** What a prepared set's rows say: {n, ok, refused (a step the browser chemistry does not port), invalid, missing: {step: n}}. */
function preparedCounts(rows) {
  const missing = {};
  let ok = 0, refused = 0;
  for (const r of rows) {
    if (r.ok) ok += 1;
    else if (r.refused) {
      refused += 1;
      const step = String(r.refused.missing || "a step");
      missing[step] = (missing[step] || 0) + 1;
    }
  }
  return { n: rows.length, ok, refused, invalid: rows.length - ok - refused, missing };
}

const ms = () => (globalThis.performance?.now ? globalThis.performance.now() : Date.now());

/** A chemistry stage's compute facts (the job receipt's compute section): what prepared its molecules, and where. */
function chemFacts(molecules, took) {
  return {
    op: "chem.prepare", kernel: ENGINE.detail, backend: "rdkit.js", placement: "browser", where: "worker", size: { molecules },
    gpu: null, accelerated: null, basis: "the chemistry worker's RDKit.js (WebAssembly) on the CPU", ms: Math.round(took * 1000) / 1000,
  };
}

function countsText(c) {
  const parts = [];
  if (c.refused) parts.push(`${c.refused} refused (${Object.entries(c.missing).map(([k, v]) => `${k}: ${v}`).join(", ")}; the runner's native RDKit reads them)`);
  if (c.invalid) parts.push(`${c.invalid} not read`);
  return `${c.ok} of ${c.n} prepared${parts.length ? `; ${parts.join("; ")}` : ""}`;
}

/**
 * The rows of a .npy file of uint8 (n, width) in C order, as np.save writes the domain files → {n, width, data (a view
 * of the rows' bytes)}. Anything else is refused: it would not be the bits bioagent's ModelSet reads.
 */
export function npyRows(bytes, width = N_BITS_BYTES) {
  const b = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
  const magic = [0x93, 0x4e, 0x55, 0x4d, 0x50, 0x59];
  if (b.length < 10 || magic.some((x, i) => b[i] !== x)) throw new Error("not a .npy file");
  const major = b[6];
  if (![1, 2, 3].includes(major)) throw new Error(`.npy format version ${major} is not one numpy writes`);
  const view = new DataView(b.buffer, b.byteOffset, b.byteLength);
  const start = major === 1 ? 10 : 12;
  const hlen = major === 1 ? view.getUint16(8, true) : view.getUint32(8, true);
  if (start + hlen > b.length) throw new Error("the .npy header runs past the file");
  const header = String.fromCharCode(...b.subarray(start, start + hlen));
  const descr = /'descr':\s*'([^']*)'/.exec(header)?.[1];
  const fortran = /'fortran_order':\s*(True|False)/.exec(header)?.[1];
  const shape = /'shape':\s*\(\s*(\d+)\s*,\s*(\d+)\s*,?\s*\)/.exec(header);
  if (!["|u1", "<u1", ">u1", "u1"].includes(descr)) throw new Error(`the .npy file holds ${descr ?? "an unknown type"}, not uint8`);
  if (fortran !== "False") throw new Error("the .npy file is in Fortran order");
  if (!shape) throw new Error("the .npy file is not a two-dimensional array");
  const n = Number(shape[1]), w = Number(shape[2]);
  if (w !== width) throw new Error(`the .npy rows are ${w} bytes, not ${width}`);
  const data = b.subarray(start + hlen);
  if (data.length !== n * w) throw new Error(`the .npy file holds ${data.length} bytes of data, not ${n} × ${w}`);
  return { n, width: w, data };
}

/**
 * The molecules a CSV, TSV or .smi file holds at most, as tcmstudio.browser_jobs counts them before it reads one: its
 * lines that are not blank, less a header (a first line with a comma or a tab, in a file that is not .smi). Streamed:
 * never the whole file in memory as text.
 */
async function moleculeRows(blob, name) {
  let n = 0, inLine = false, first = [], firstDone = false;
  const reader = blob.stream().getReader();
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    for (const c of value) {
      if (c === 10 || c === 13) {
        if (inLine) { n += 1; firstDone = true; }
        inLine = false;
      } else {
        if (c !== 32 && c !== 9) inLine = true;
        if (!firstDone && first.length < 65536) first.push(c);
      }
    }
  }
  if (inLine) n += 1;
  const head = new TextDecoder().decode(new Uint8Array(first));
  const header = n > 0 && !String(name).toLowerCase().endsWith(".smi") && (head.includes(",") || head.trim().includes("\t"));
  return n - (header ? 1 : 0);
}

/** A lock over admet/models while a job changes it (Web Locks where there are; another tab's build waits). */
async function withModelsLock(locks, fn) {
  if (!locks || typeof locks.request !== "function") return fn();
  return locks.request(MODELS_LOCK, { mode: "exclusive" }, () => fn());
}

// ------------------------------------------------------------------------------------------------- the handlers

const TITLE_MODELS = { zh: "构建 ADMET 模型（TDC，浏览器）", en: "Build the ADMET models (TDC, browser)" };
const TITLE_PIPELINE = { zh: "ADMET 预测（浏览器）", en: "ADMET prediction (browser)" };
const STAGE = {
  archive: { zh: "获取并核对 TDC ADMET 数据包", en: "Get and check the TDC ADMET archive" },
  split: { zh: "读取各终点的训练集与测试集", en: "Read each endpoint's training and test sets" },
  chemistry: (e) => ({ zh: `化学处理：${e}（RDKit.js）`, en: `Chemistry: ${e} (RDKit.js)` }),
  train: (e) => ({ zh: `训练模型：${e}`, en: `Train the model: ${e}` }),
  verifyModels: { zh: "核对模型卡与文件", en: "Check the model cards and files" },
  inputs: { zh: "复制并核对输入文件", en: "Copy and check the input file" },
  read: { zh: "读取分子", en: "Read the molecules" },
  prepare: { zh: "标准化、特征与警示结构（RDKit.js）", en: "Standardise, featurise and find alerts (RDKit.js)" },
  domain: { zh: "适用域检索（Tanimoto）", en: "Applicability-domain search (Tanimoto)" },
  analysis: { zh: "规则、预测与报告", en: "Rules, predictions and report" },
  verify: { zh: "按 Runner 的要求核对输出", en: "Check the outputs as the runner does" },
};
/** The runner's names of the outputs pipeline.admet requires (kinds.py). */
const ARTEFACTS = Object.freeze({ table: "admet.tsv", run: "run.json", limitations: "limitations.md" });

/**
 * The browser's line about where the numbers were computed (kept under the 320 characters a job's limitation keeps when
 * the model reads it: jobs-runtime limitsText). `similarity`: what run.json says searched the domain.
 */
export function computedLine({ sklearn, archives = [], similarity }) {
  const sk = sklearn ? `scikit-learn ${sklearn}` : "scikit-learn (version not reported)";
  const shas = [...new Set(archives.map(bareSha256).filter(Boolean))].map(sha12);
  const arc = shas.length ? `the TDC archive (${shas.join(", ")})` : "the TDC archive";
  const head = `Computed in this browser: chemistry RDKit.js ${ENGINE.version} + ports, models ${sk} built in this browser from ${arc}; similarity `;
  const room = LINE_MAX - head.length - 1;
  const sim = String(similarity);
  return `${head}${sim.length <= room ? sim : `${sim.slice(0, room - 1)}…`}.`;
}
/** A job's limitation the model reads is held to this many characters (jobs-runtime.js limitsText). */
const LINE_MAX = 320;

/** How run.json's similarity record reads in that line. */
function similarityWords(rec, backends) {
  if (!isPlainObject(rec)) return "not searched (nothing was predicted)";
  if (rec.rejected) {
    const why = String(rec.rejected);
    return `recomputed on the CPU by Python: the browser's was rejected (${why.length <= 90 ? why : `${why.slice(0, 89)}…`})`;
  }
  if (rec.backend === "cpu") return "Python's own search on the CPU";
  const names = [...new Set(Object.values(backends || {}))];
  const used = names.length ? names.join(" and ") : String(rec.backend);
  return `${used}, checked by Python against the exact search${names.includes("webgpu") ? " (the GPU's results also checked on the CPU here)" : ""}`;
}

/**
 * The two ADMET handlers → [admet.models, pipeline.admet]. options: rdkit (boot.json's rdkit entry {version, url,
 * sha256, bytes}; without one the pinned build is fetched from its jsDelivr URL); ChemWorker and chemWorkerUrl (the
 * chemistry worker; default the page's Worker and ../chem/chem.worker.js); preference () → "auto" | "cpu" |
 * "reference" (the person's acceleration setting; default "auto"); gpu (a ScienceGPU; default one made when first
 * needed); calibration () → this device's calibration or null (default compute/calibration.js currentCalibration);
 * navigator; locks (Web Locks, over admet/models); debug {forceSoftwareGpu: true}: a test harness's switch that runs the
 * domain search on the GPU without a calibration, a software adapter allowed (never set by Studio: no job parameter
 * and no setting reaches it).
 */
export function createAdmetHandlers({
  rdkit = null, ChemWorker = globalThis.Worker, chemWorkerUrl = new URL("../chem/chem.worker.js", import.meta.url).href,
  preference = () => "auto", gpu = null, calibration = null, navigator: nav = globalThis.navigator, locks = globalThis.navigator?.locks,
  debug = null,
} = {}) {
  const wasm = rdkitWasm(rdkit);
  const force = debug?.forceSoftwareGpu === true;
  let science = gpu;
  const scienceGpu = () => (science ??= new ScienceGPU({ navigator: nav, timestamps: true, allowSoftware: force }));
  const chemClient = () => new ChemClient({ Worker: ChemWorker, url: chemWorkerUrl, wasm });
  const chemUnavailable = () => {
    const why = rdkitMismatch(wasm);
    if (why) return refusal("unavailable", why, "job_service", "Reload the page so that its code and the site's boot.json are of the same build.");
    if (typeof ChemWorker !== "function") return refusal("unavailable", "This browser cannot start the chemistry worker (no Web Worker).", "job_service", "Use a current Chromium-based browser such as Chrome or Edge, or connect the local runner.");
    return null;
  };
  const deviceCalibration = async () => {
    try {
      if (typeof calibration === "function") return (await calibration()) ?? null;
      return (await currentCalibration({ navigator: nav })).calibration ?? null;
    } catch {
      return null;
    }
  };
  const preferenceNow = () => {
    let p = "auto";
    try { p = typeof preference === "function" ? preference() : preference; } catch { p = "auto"; }
    return SCIENCE_PREFERENCES.includes(p) ? p : "auto";
  };
  const shared = { wasm, force, nav, scienceGpu, chemClient, chemUnavailable, deviceCalibration, preferenceNow, locks };
  return [modelsHandler(shared), pipelineHandler(shared)];
}

// ------------------------------------------------------------------------------------------------- admet.models

const MODELS_PARAMS = ["archive", "endpoints", "seed", "max_iter", "rebuild"];

/** admet.models' parameters, checked → {endpoints (empty: every one), seed, max_iter, rebuild} (build_models' defaults). */
function checkModels(p) {
  checkKnown("admet.models", MODELS_PARAMS, p);
  if (present(p.rebuild) && typeof p.rebuild !== "boolean") throw bad("rebuild: true or false");
  if (given(p.archive) && typeof p.archive !== "string") throw bad("archive: name a file of this project (the TDC ADMET group zip), or leave it out to get it through the site's relay");
  return {
    endpoints: checkEndpoints(p.endpoints),
    seed: present(p.seed) ? checkInt("seed", p.seed, SEED) : 0,
    max_iter: present(p.max_iter) ? checkInt("max_iter", p.max_iter, MAX_ITER) : 600,
    rebuild: p.rebuild === true,
  };
}

/** The archive a job reads: a project file, or the registry's file of group "admet" → {rec} | {ws, row}. */
async function archiveSource(p, svc, projectId) {
  if (given(p.archive)) {
    const rec = await projectFile(svc.projectFiles, projectId, "archive", p.archive);
    if (!rec) throw bad(`archive: no file named ${JSON.stringify(String(p.archive).trim())} in this project${projectId ? "" : " (the job belongs to no project, so only a file id can name one)"}`);
    return { rec };
  }
  const ws = typeof svc.datasetsFor === "function" ? svc.datasetsFor("admet") : null;
  const noRegistry = (why) => refusal("unavailable", `The TDC ADMET group archive cannot be fetched here: ${why}.`, "dataset",
    "Give the archive as a project file (archive: the TDC ADMET group zip from Harvard Dataverse, datafile 4426004), or connect the local runner (it downloads it natively).");
  if (!ws) throw noRegistry("this page has no dataset workspace for it");
  let reg;
  try {
    reg = await ws.registry();
  } catch (err) {
    throw noRegistry(err?.message || String(err));
  }
  const row = (reg.files || [])[0];
  if (!row) throw noRegistry("the site's dataset registry lists no file in the admet group");
  return { ws, row };
}

function modelsHandler(sh) {
  return {
    kind: "admet.models",
    title: TITLE_MODELS,
    stages: [{ id: "archive", title: STAGE.archive }, { id: "split", title: STAGE.split }, { id: "verify", title: STAGE.verifyModels }],
    /** Two stages per endpoint, between split and verify. */
    stagesFor(params) {
      let eps;
      try { eps = checkEndpoints(params?.endpoints); } catch { eps = []; }
      return [
        { id: "archive", title: STAGE.archive }, { id: "split", title: STAGE.split },
        ...(eps.length ? eps : ENDPOINTS).flatMap((e) => [{ id: `chemistry.${e}`, title: STAGE.chemistry(e) }, { id: `train.${e}`, title: STAGE.train(e) }]),
        { id: "verify", title: STAGE.verifyModels },
      ];
    },
    /** No output of the job's own, as the runner's kind: the models are in admet/models (each endpoint's model and
     * domain are the job's artefacts there, read back like any other), and its verify stage holds them to their cards. */
    expected: [],
    /** Nothing of a build needs the runner: the archive comes through the site's relay or from the project. */
    needsRunner() { return null; },

    async preflight(params, svc, { project_id = null } = {}) {
      const p = isPlainObject(params) ? params : {};
      checkModels(p);
      const chem = sh.chemUnavailable();
      if (chem) throw chem;
      if (!svc.store) throw await storageRefusal(null);
      if (!svc.pool) throw refusal("unavailable", "This browser cannot start job workers.", "job_service");
      const src = await archiveSource(p, svc, project_id);
      const archiveBytes = src.rec ? Number(src.rec.bytes ?? src.rec.blob.size) || 0 : Number(src.row.bytes) || 0;
      // the archive kept twice for a moment (its copy into admet/archive), the largest endpoint's prepared sets (cyp3a4:
      // about 12,300 molecules × 8.6 KB) and the models (about 30 MB for the 22)
      const refused = await storageRefusal(svc.store, 2 * archiveBytes + 150e6, src.row && src.row.bytes === null ? [src.row.name] : []);
      if (refused) throw refused;
    },

    async run(ctx) {
      const p = isPlainObject(ctx.params) ? ctx.params : {};
      const settings = checkModels(p);
      const endpoints = settings.endpoints.length ? settings.endpoints : [...ENDPOINTS];
      const root = work(ctx.job);
      const store = ctx.store;
      const span = 0.85 / endpoints.length;

      // ---------------------------------------------------------------- archive
      let archive = null;
      await ctx.stage("archive", async (s) => {
        archive = await stageArchive(ctx, p);
        s.record({ archive_sha256: archive.sha256, pinned: archive.pinned, source: archive.source });
        return { detail: `${archive.how}; SHA-256 ${sha12(archive.sha256)}…${archive.pinned ? ", the pinned TDC ADMET group" : ", NOT the pinned TDC ADMET group's (the models' held-out scores are then not comparable with TDC's leaderboard)"}` };
      });
      ctx.progress(0.05, "archive checked");

      const lease = await ctx.pool.lease({ packages: PACKAGES });
      const chem = sh.chemClient();
      const result = { models: modelsDir(store).join("/"), archive_sha256: archive.sha256, pinned: archive.pinned, endpoints: {} };
      let exhausted = false;
      try {
        const loaded = await lease.call("packages", { names: PACKAGES }, { signal: ctx.signal });
        const versions = Object.fromEntries(PACKAGES.map((n) => [n, loaded?.versions?.[n] ?? null]));
        const runtime = { bundle: lease.info?.bundle?.sha256 ?? null, pyodide: lease.info?.pyodide?.version ?? null, packages: versions };
        const zip = `${root}/archive/${sha12(archive.sha256)}.zip`;
        await lease.call("mount", { root: `${root}/archive`, dir: archivePath(store, archive.sha256).slice(0, -1), files: [`${sha12(archive.sha256)}.zip`] }, { signal: ctx.signal });

        // -------------------------------------------------------------- split
        let split = null;
        const splitDigest = `sha256:${await sha256Hex(canonicalJson({ archive: archive.sha256, endpoints, runtime }))}`;
        await ctx.stage("split", async (s) => {
          let out;
          try {
            out = await pythonCall(ctx, lease, "admet_split", { archive: zip, out_dir: `${root}/split`, endpoints }, [0.05, 0.05], "reading the archive");
          } catch (err) {
            // Python names the copy it read (<sha12>.zip in this browser's storage): say the file the person gave
            const own = archive.source?.name || archive.source?.file;
            if (own && typeof err?.message === "string") { try { err.message = err.message.split(`${sha12(archive.sha256)}.zip`).join(own); } catch { /* frozen */ } }
            throw err;
          }
          if (bareSha256(out?.archive_sha256) !== archive.sha256) throw Object.assign(new Error(`Python read the archive as SHA-256 ${String(out?.archive_sha256).slice(0, 12)}…, not the ${sha12(archive.sha256)}… this page copied`), { type: "integrity" });
          const kept = await lease.call("persist", { from: `${root}/split`, dir: [...ctx.dir, "split"], clean: true }, { signal: ctx.signal });
          split = out.endpoints || {};
          await store.writeJson([...ctx.dir, "state", "split.json"], { split, archive_sha256: archive.sha256 });
          s.record({ endpoints: split, worker: lease.id });
          const missing = endpoints.filter((e) => !isPlainObject(split[e]));
          if (missing.length) throw Object.assign(new Error(`the archive has no ${missing.join(", ")}`), { type: "failed" });
          return {
            detail: `${endpoints.length} endpoints: ${endpoints.map((e) => `${e} ${split[e].train_val}/${split[e].test}`).join(", ")} (train_val/test)`.slice(0, 1000),
            worker: lease.id, checkpoint: { inputs_digest: splitDigest, outputs: kept.files.map((f) => ({ path: ["split", ...f.path], bytes: f.bytes, sha256: f.sha256 })) },
          };
        }, {
          reuse: async () => {
            const check = await ctx.checkpoint.reusable("split", { inputs_digest: splitDigest });
            if (!check.ok) return check;
            const kept = await store.readJson([...ctx.dir, "state", "split.json"]).catch(() => null);
            if (!isPlainObject(kept?.split)) return { ok: false, problems: ["the earlier attempt's split record is gone"] };
            split = kept.split;
            return { ok: true, detail: `reused: its ${check.checked} files re-hash to the digests recorded when it ran` };
          },
        });

        // -------------------------------------------------------------- each endpoint
        const engineKey = { engine: CHEMISTRY, wasm: sh.wasm.sha256 };
        for (const [i, e] of endpoints.entries()) {
          ctx.throwIfCancelled();
          const base = 0.1 + i * span;
          const cards = await readCards(store);
          const keep = settings.rebuild ? null : await keptModel(store, cards[e], { archive_sha256: archive.sha256, seed: settings.seed, max_iter: settings.max_iter, sklearn: versions["scikit-learn"], rdkit: ENGINE.version });
          if (keep) {
            const why = `kept: its model in this browser was built from the same archive with the same settings, scikit-learn ${versions["scikit-learn"]} and chemistry, and its files re-hash to its card's digests`;
            await ctx.stage(`chemistry.${e}`, async () => null, { reuse: async () => ({ ok: true, detail: why }) });
            await ctx.stage(`train.${e}`, async () => null, { reuse: async () => ({ ok: true, detail: why }) });
            for (const [file, sha] of [[cards[e].model_file, cards[e].model_sha256], [cards[e].domain_file, cards[e].domain_sha256]]) {
              ctx.artefact({ name: file, path: [...modelsDir(store), file], bytes: keep[file], sha256: sha });
            }
            result.endpoints[e] = { kept: true, ...cardSummary(cards[e]) };
            ctx.progress(base + span, `${e}: kept`);
            continue;
          }
          const sets = { train_val: await splitSmiles(store, ctx.dir, e, "train_val"), test: await splitSmiles(store, ctx.dir, e, "test") };
          const cDigest = `sha256:${await sha256Hex(canonicalJson({ endpoint: e, archive: archive.sha256, smiles: { train_val: sets.train_val.sha, test: sets.test.sha }, ...engineKey }))}`;
          await ctx.stage(`chemistry.${e}`, async (s) => {
            const t0 = ms();
            const outputs = [];
            const counts = {};
            for (const k of ["train_val", "test"]) {
              const { smiles } = sets[k];
              const prep = await chem.prepareAll(smiles, {
                alerts: false, signal: ctx.signal,
                onProgress: (done, total) => ctx.progress(base + span * 0.4 * (k === "test" ? 0.8 + 0.2 * (done / total) : 0.8 * (done / total)), `${e} ${k}: ${done} of ${total} molecules prepared`),
              });
              const dir = [...ctx.dir, "prepared", e, k];
              await store.remove(dir, { recursive: true }).catch(() => false);
              outputs.push(...(await writePrepared(store, dir, smiles, prep, { signal: ctx.signal, rel: ["prepared", e, k] })));
              counts[k] = preparedCounts(prep.rows);
            }
            s.record({ prepared: counts, engine: CHEMISTRY, rdkit: chem.info?.rdkit ?? null });
            return {
              detail: `train_val: ${countsText(counts.train_val)}; test: ${countsText(counts.test)}`.slice(0, 1000),
              checkpoint: { inputs_digest: cDigest, outputs }, compute: chemFacts(counts.train_val.n + counts.test.n, ms() - t0),
            };
          }, {
            reuse: async () => {
              const check = await ctx.checkpoint.reusable(`chemistry.${e}`, { inputs_digest: cDigest });
              return check.ok ? { ok: true, detail: `reused: its ${check.checked} prepared files re-hash to the digests recorded when they were written` } : check;
            },
          });

          await ctx.stage(`train.${e}`, async (s) => {
            const at = `${root}/prepared/${e}`;
            for (const k of ["train_val", "test"]) {
              await lease.call("mount", { root: `${at}/${k}`, dir: [...ctx.dir, "prepared", e, k], files: ["prepared.json", "features.f32", "bits.u8"] }, { signal: ctx.signal });
            }
            // a models directory (<cache>/models) of this endpoint's own, holding the cards built so far, which
            // admet_train merges this endpoint's into as build_models merges them; this page has decided to train it
            // (the model here does not stand), so admet_train is told to (rebuild)
            const out = [...ctx.dir, "models", e];
            const mem = `${root}/train/${e}/models`;
            await store.remove(out, { recursive: true }).catch(() => false);
            const cardsNow = await store.readBytes([...modelsDir(store), "model_cards.json"]).catch(() => null);
            if (cardsNow) await store.writeBytes([...out, "model_cards.json"], cardsNow);
            await lease.call("free", { paths: [`${root}/train/${e}`] }, { signal: ctx.signal });
            if (cardsNow) await lease.call("restore", { dir: out, to: mem }, { signal: ctx.signal });
            const trained = await pythonCall(ctx, lease, "admet_train", {
              archive: zip, prepared_dir: `${root}/prepared`, models_dir: mem, endpoint: e, seed: settings.seed, max_iter: settings.max_iter, rebuild: true,
            }, [base + span * 0.4, span * 0.55], `training ${e}`);
            const card = isPlainObject(trained?.card) ? trained.card : trained;
            const kept = await lease.call("persist", { from: mem, dir: out, clean: true }, { signal: ctx.signal });
            const installed = await installModel(ctx, sh.locks, e, card, kept.files, cardsNow);
            for (const a of installed) ctx.artefact(a);
            await lease.call("free", { paths: [at, `${root}/train/${e}`] }, { signal: ctx.signal });
            // the prepared sets and the copy have served: the model is in admet/models
            await store.remove([...ctx.dir, "prepared", e], { recursive: true }).catch(() => false);
            await store.remove(out, { recursive: true }).catch(() => false);
            result.endpoints[e] = { kept: false, ...cardSummary(card) };
            s.record({ card: cardSummary(card), worker: lease.id });
            ctx.progress(base + span, `${e}: ${card.metric} ${card.test_score}`);
            return {
              detail: `${card.metric} ${card.test_score} on ${plural(card.test, "test molecule")}; trained on ${plural(card.train, "molecule")} (dropped: ${card.dropped_unreadable ?? 0} unreadable, ${card.dropped_unstandardisable ?? 0} refused by the browser chemistry); ${card.iterations} iterations; model and domain in this browser's models`.slice(0, 1000),
              worker: lease.id,
            };
          });
        }

        // every endpoint's prepared sets have served (each was removed once its model was in admet/models)
        for (const d of ["prepared", "models"]) await store.remove([...ctx.dir, d], { recursive: true }).catch(() => false);

        // -------------------------------------------------------------- verify
        await ctx.stage("verify", async (s) => {
          const cards = await readCards(store);
          const files = ["model_cards.json", ...endpoints.flatMap((e) => [cards[e]?.model_file, cards[e]?.domain_file]).filter(Boolean)];
          await lease.call("free", { paths: [`${root}/shared`] }, { signal: ctx.signal });
          await lease.call("mount", { root: `${root}/shared/models`, dir: modelsDir(store), files: [...new Set(files)] }, { signal: ctx.signal });
          const v = await pythonCall(ctx, lease, "verify_pipeline", { kind: "admet.models", models_dir: `${root}/shared/models`, endpoints, archive_sha256: archive.sha256 }, [0.95, 0.05], "the check of the models");
          s.record({ status: v.status, worker: lease.id });
          // each endpoint as the runner's job of this kind gives it (tcmstudio.kinds _admet_models_result), with whether
          // this job kept it
          const report = isPlainObject(v.report?.endpoints) ? v.report.endpoints : {};
          for (const e of endpoints) {
            const c = { ...cards[e], ...(isPlainObject(report[e]) ? report[e] : {}) };
            result.endpoints[e] = { ...Object.fromEntries(RESULT_KEYS.map((k) => [k, c[k] ?? null])), kept: result.endpoints[e]?.kept === true };
          }
          ctx.outcome({ checks: (v.checks || []).filter((c) => c.status === "PASS").map((c) => c.name), problems: v.problems || [], complete: v.status === "PASS" });
          if (v.status !== "PASS") throw Object.assign(new Error(`the models did not verify: ${(v.problems || []).slice(0, 3).join("; ") || v.status}`), { type: "failed" });
          return { detail: "every card's model and domain file re-hashed to its digest, the archive's digest recorded", worker: lease.id, verified: true };
        });
        const dropped = Object.values(result.endpoints).reduce((a, c) => a + (Number(c.dropped_unstandardisable) || 0), 0);
        const lines = [
          `Models built in this browser: chemistry RDKit.js ${ENGINE.version} + ports, models scikit-learn ${versions["scikit-learn"] ?? "(version not reported)"} in Pyodide. Molecules the browser chemistry refused to standardise are left out of training and counted on each card (${dropped} in all): a model built here can differ from a runner-built one by those molecules.`,
        ];
        if (!archive.pinned) lines.push("The models were trained on an archive whose SHA-256 is not the pinned TDC ADMET group's, so their held-out scores are not comparable with TDC's leaderboard.");
        ctx.outcome({ limitations: lines });
      } catch (err) {
        if (err?.need === "memory") exhausted = true;
        throw err;
      } finally {
        chem.terminate();
        if (exhausted) lease.terminate();
        else {
          await lease.call("free", { paths: [root] }).catch(() => {});
          lease.release();
        }
      }
      return { result };
    },
  };
}

/** What the runner's admet.models job says of each endpoint (tcmstudio.kinds _admet_models_result). */
const RESULT_KEYS = ["metric", "test_score", "train", "test", "dropped_unreadable", "dropped_unstandardisable", "archive_is_pinned", "card_sha256", "model_sha256"];

/** What a model card says of its model, as a job's result lists it. */
function cardSummary(c) {
  const keys = ["metric", "test_score", "train", "test", "dropped_unreadable", "dropped_unstandardisable", "iterations", "model_sha256", "domain_sha256", "archive_sha256", "sklearn", "chemistry"];
  return Object.fromEntries(keys.filter((k) => c?.[k] !== undefined).map((k) => [k, c[k]]));
}

/** An endpoint's SMILES as admet_split wrote them → {smiles, sha} (sha: the digest a prepared set names them by). */
async function splitSmiles(store, dir, e, split) {
  const doc = await store.readJson([...dir, "split", e, `${split}.json`]);
  if (!Array.isArray(doc?.smiles) || doc.smiles.some((x) => typeof x !== "string")) throw Object.assign(new Error(`split/${e}/${split}.json holds no SMILES list`), { type: "failed" });
  return { smiles: doc.smiles, sha: smilesSha256(doc.smiles) };
}

/**
 * Whether the model of admet/models for this endpoint can stand (bioagent.admet.models._kept): built from the same
 * archive with the same settings, scikit-learn and chemistry, and its files re-hash to its card's digests →
 * {<model file>: bytes, <domain file>: bytes} | null.
 */
async function keptModel(store, card, settings) {
  if (!isPlainObject(card) || Object.entries(settings).some(([k, v]) => v === null || card[k] !== v)) return null;
  if (canonicalJson(card.chemistry ?? null) !== canonicalJson(CHEMISTRY)) return null;
  const out = {};
  for (const [file, sha] of [[card.model_file, card.model_sha256], [card.domain_file, card.domain_sha256]]) {
    if (typeof file !== "string" || !bareSha256(sha)) return null;
    const f = await store.readFile([...modelsDir(store), file]).catch(() => null);
    if (!f) return null;
    const h = new Sha256();
    h.update(new Uint8Array(await f.arrayBuffer()));
    if (h.digestHex() !== bareSha256(sha)) return null;
    out[file] = f.size;
  }
  return out;
}

/**
 * Copy what admet_train wrote for endpoint `e` (the job's models/<e>/: its model, its domain and the merged
 * model_cards.json, with the digests the worker took) into admet/models, under the models lock, each copy re-hashed →
 * the artefacts. The card must name the files the worker wrote, at their digests, and this chemistry.
 */
async function installModel(ctx, locks, e, card, files, cardsBefore) {
  const store = ctx.store;
  const fail = (message) => Object.assign(new Error(`${e}: ${message}`), { type: "integrity" });
  if (!isPlainObject(card) || card.endpoint !== e) throw fail("admet_train returned no card for it");
  if (canonicalJson(card.chemistry ?? null) !== canonicalJson(CHEMISTRY)) throw fail(`its card names the chemistry ${JSON.stringify(card.chemistry)}, not this browser's`);
  const byName = new Map(files.map((f) => [f.path.join("/"), f]));
  for (const [file, sha] of [[card.model_file, card.model_sha256], [card.domain_file, card.domain_sha256]]) {
    const f = byName.get(String(file));
    if (!f) throw fail(`${file} was not written`);
    if (f.sha256 !== bareSha256(sha)) throw fail(`${file} has SHA-256 ${f.sha256.slice(0, 12)}… as the worker wrote it, its card says ${String(sha).slice(0, 12)}…`);
  }
  const cards = byName.get("model_cards.json");
  if (!cards) throw fail("model_cards.json was not written");
  return withModelsLock(locks, async () => {
    // another tab's build changed the cards since this one read them: its card would be lost
    const now = await store.readBytes([...modelsDir(store), "model_cards.json"]).catch(() => null);
    if ((now ? await sha256Hex(now) : null) !== (cardsBefore ? await sha256Hex(cardsBefore) : null)) {
      throw Object.assign(new Error("the models of this browser changed while this endpoint trained (another tab built some): run the job again"), { type: "conflict" });
    }
    const out = [];
    // not cut short by a cancel: a copy that is stopped removes the file it was replacing, and model_cards.json is
    // every endpoint's (the install takes a moment; the cancel takes effect once it is done). The cards go last, so a
    // tab closed half-way leaves files whose cards' digests no longer match: the next build trains them again
    for (const name of [card.model_file, card.domain_file, "model_cards.json"]) {
      const f = byName.get(name);
      const to = [...modelsDir(store), name];
      await store.copy([...ctx.dir, "models", e, name], to);
      out.push({ name, path: to, bytes: f.bytes, sha256: f.sha256 });
    }
    return out;
  });
}

/**
 * The archive in admet/archive/<sha12>.zip, its bytes hashed as they are copied → {sha256, pinned, how, source}. A
 * project file is held to the SHA-256 the project recorded; the registry's file is fetched (or found) by the dataset
 * workspace, which checks it against the registry's reviewed checksum.
 */
async function stageArchive(ctx, p) {
  const store = ctx.store;
  const src = await archiveSource(p, ctx, ctx.job.project_id);
  let file, sha, how, source, fetched = null;
  if (src.rec) {
    file = src.rec.blob;
    source = { project_file: src.rec.id, name: src.rec.name };
    how = `${src.rec.name} from the project`;
  } else {
    const rows = await src.ws.acquire({
      names: [src.row.name], transport: "auto", signal: ctx.signal,
      onProgress: (ev) => ctx.progress(0.05 * (ev.total ? ev.bytes / ev.total : 0), `${src.row.name}: ${(ev.bytes / 1e6).toFixed(1)} MB`),
    });
    const r = rows[0];
    if (!r || (r.status !== "PASS" && r.status !== "UNVERIFIED")) {
      const blocked = r?.status === "BLOCKED" || r?.status === "SKIPPED";
      throw Object.assign(new Error(`${src.row.name}: ${r?.reason || "it could not be fetched"}`), {
        type: blocked ? "unavailable" : "integrity", need: blocked ? "network" : undefined,
        hint: "Give the archive as a project file, or connect the local runner (it downloads it natively).",
      });
    }
    fetched = bareSha256(r.sha256);
    file = await store.readFile([...src.ws.rawDir(), src.row.name]);
    if (!file) throw Object.assign(new Error(`${src.row.name} is not in this browser's storage after it was fetched`), { type: "storage" });
    source = { dataset: "admet", file: src.row.name, url: src.row.url, route: r.route, transport: r.transport, verified: r.verified };
    how = `${src.row.name} ${r.route === "cache" ? "already in this browser" : r.route === "relay" ? "through the site's relay" : r.route === "import" ? "imported" : "from its host"}${r.status === "PASS" ? `, verified by ${r.verified}` : ", no reviewed checksum"}`;
  }
  // copied into admet/ under a name of this job, then given its digest's name
  const tmp = [...admetDir(store), "archive", `.${ctx.job.id}.part`];
  const h = new Sha256();
  const bytes = await store.writeBlob(tmp, file, { signal: ctx.signal, onChunk: (b) => h.update(b) });
  sha = h.digestHex();
  if (src.rec && sha !== bareSha256(src.rec.sha256)) {
    await store.remove(tmp).catch(() => false);
    throw digestMismatch(src.rec.name, sha, src.rec.sha256);
  }
  if (!src.rec && fetched !== sha) {
    await store.remove(tmp).catch(() => false);
    throw Object.assign(new Error(`${src.row.name}: its copy has SHA-256 ${sha.slice(0, 12)}…, not the ${String(fetched).slice(0, 12)}… it was fetched with`), { type: "integrity" });
  }
  // an earlier job's copy of the same bytes stays (a running job may have it open); else this one takes its name
  const dest = archivePath(store, sha);
  const there = await store.readFile(dest).catch(() => null);
  let same = false;
  if (there && there.size === bytes) {
    const k = new Sha256();
    k.update(new Uint8Array(await there.arrayBuffer()));
    same = k.digestHex() === sha;
  }
  if (same) await store.remove(tmp).catch(() => false);
  else await store.move(tmp, dest, { signal: ctx.signal });
  ctx.input({ name: "archive", sha256: sha, bytes, source });
  return { sha256: sha, pinned: sha === TDC_ADMET_SHA256, how, source };
}

// ------------------------------------------------------------------------------------------------- pipeline.admet

const PIPELINE_PARAMS = ["molecules", "endpoints"];

function checkPipeline(p) {
  checkKnown("pipeline.admet", PIPELINE_PARAMS, p);
  if (!given(p.molecules) || typeof p.molecules !== "string" || !p.molecules.trim()) {
    throw bad("molecules: a file of this project (CSV name,smiles, TSV or SMI) by its name or id, or one SMILES");
  }
  return { endpoints: checkEndpoints(p.endpoints) };
}

/** The molecules a call names → {rec} (a project file) | {smiles} (one SMILES), as the runner's kind resolves them. */
async function moleculesSource(p, projectFiles, projectId) {
  const ref = p.molecules.trim();
  const rec = projectFiles ? await projectFile(projectFiles, projectId, "molecules", ref) : null;
  if (rec) {
    const name = rec.name.toLowerCase();
    if (SDF_SUFFIXES.some((s) => name.endsWith(s))) throw runnerRefusal(sdfNeed());
    return { rec };
  }
  // a file's name is not a SMILES (tcmstudio.browser_jobs admet_read refuses it so too)
  if (!FILE_SUFFIX.test(ref) && SMILES.test(ref) && !ref.startsWith("-")) return { smiles: ref };
  throw bad(`molecules: no file named ${JSON.stringify(ref)} in this project${projectId ? "" : " (the job belongs to no project, so only a file id can name one)"}, and it is not one SMILES`);
}

/** The endpoints a run predicts: those asked for, else every one built here (run_admet's own rule). */
function predicted(asked, cards) {
  return (asked.length ? asked : ENDPOINTS).filter((e) => isPlainObject(cards[e]));
}

function pipelineHandler(sh) {
  return {
    kind: "pipeline.admet",
    title: TITLE_PIPELINE,
    stages: [
      { id: "inputs", title: STAGE.inputs }, { id: "read", title: STAGE.read }, { id: "chemistry", title: STAGE.prepare },
      { id: "domain", title: STAGE.domain }, { id: "analysis", title: STAGE.analysis }, { id: "verify", title: STAGE.verify },
    ],
    expected: Object.keys(ARTEFACTS),
    needsRunner: admetNeedsRunner,

    async preflight(params, svc, { project_id = null } = {}) {
      const p = isPlainObject(params) ? params : {};
      const { endpoints } = checkPipeline(p);
      const tied = admetNeedsRunner(p);
      if (tied) throw runnerRefusal(tied);
      const chem = sh.chemUnavailable();
      if (chem) throw chem;
      if (!svc.store) throw await storageRefusal(null);
      if (!svc.pool) throw refusal("unavailable", "This browser cannot start job workers.", "job_service");
      // the molecules first (an SDF named by its file id, a file that is not there, too many molecules): building the
      // models would not make such a run go ahead here
      const src = await moleculesSource(p, svc.projectFiles, project_id);
      let bytes = 0;
      if (src.rec) {
        bytes = Number(src.rec.bytes ?? src.rec.blob.size) || 0;
        const rows = await moleculeRows(src.rec.blob, src.rec.name);
        if (rows > MAX_MOLECULES) {
          throw refusal("unavailable", `${src.rec.name} holds up to ${rows.toLocaleString("en-US")} molecules, more than the ${MAX_MOLECULES.toLocaleString("en-US")} a run in this browser takes.`, "memory",
            `${RUNNER}: it runs the same pipeline natively without that limit; or split the file into parts of at most ${MAX_MOLECULES.toLocaleString("en-US")} molecules.`);
        }
      }
      const built = await admetModelsBuilt(svc.store);
      if (!built) throw refusal("unavailable", "No ADMET model is built in this browser.", "dataset", BUILD_HINT);
      const missing = endpoints.filter((e) => !built.endpoints.includes(e));
      if (missing.length) {
        throw refusal("unavailable", `The ADMET model${missing.length > 1 ? "s" : ""} of ${missing.join(", ")} ${missing.length > 1 ? "are" : "is"} not built in this browser (built here: ${built.endpoints.join(", ")}).`, "dataset",
          `Build ${missing.length > 1 ? "them" : "it"} first in this browser with job.admet.models (endpoints: ${JSON.stringify(missing)}), or connect the local runner.`);
      }
      const refused = await storageRefusal(svc.store, 3 * bytes + 20e6);
      if (refused) throw refused;
    },

    async run(ctx) {
      const p = isPlainObject(ctx.params) ? ctx.params : {};
      const { endpoints } = checkPipeline(p);
      const root = work(ctx.job);
      const store = ctx.store;
      const SUMMARY = [...ctx.dir, "summary.json"];
      const digests = {};
      let source = null;

      // ---------------------------------------------------------------- inputs
      await ctx.stage("inputs", async (s) => {
        source = await moleculesSource(p, ctx.projectFiles, ctx.job.project_id);
        const dir = [...ctx.dir, "inputs"];
        await store.remove(dir, { recursive: true }).catch(() => false);
        if (source.smiles) {
          s.record({ molecules: "one SMILES" });
          return { detail: "one SMILES, given in the call (no file)" };
        }
        const name = safeName(source.rec.name);
        const h = new Sha256();
        const bytes = await store.writeBlob([...dir, name], source.rec.blob, { signal: ctx.signal, onChunk: (b) => h.update(b) });
        const sha = h.digestHex();
        if (sha !== bareSha256(source.rec.sha256)) {
          await store.remove([...dir, name]).catch(() => false);
          throw digestMismatch(source.rec.name, sha, source.rec.sha256);
        }
        source.name = name;
        digests.molecules = sha;
        ctx.input({ name: "molecules", sha256: sha, bytes, source: { project_file: source.rec.id, name: source.rec.name } });
        s.record({ inputs: { molecules: sha } });
        return { detail: `${source.rec.name} copied and checked against the project's SHA-256` };
      });

      const lease = await ctx.pool.lease({ packages: PACKAGES });
      const chem = sh.chemClient();
      let summary = null;
      let exhausted = false;
      try {
        const loaded = await lease.call("packages", { names: PACKAGES }, { signal: ctx.signal });
        const versions = Object.fromEntries(PACKAGES.map((n) => [n, loaded?.versions?.[n] ?? null]));
        const runtime = { bundle: lease.info?.bundle?.sha256 ?? null, pyodide: lease.info?.pyodide?.version ?? null, packages: versions };
        if (source.rec) await lease.call("mount", { root: `${root}/inputs`, dir: [...ctx.dir, "inputs"], files: [source.name] }, { signal: ctx.signal });
        const built = await admetModelsBuilt(store);
        const cards = built?.cards || {};
        const eps = predicted(endpoints, cards);
        if (!eps.length) throw refusal("unavailable", "No ADMET model is built in this browser.", "dataset", BUILD_HINT);
        // a card whose files are gone (cleared storage) cannot be predicted with: the run names the endpoints that can
        const all = Object.keys(await readCards(store));
        const asked = endpoints.length ? endpoints : all.length === built.endpoints.length ? [] : built.endpoints;
        const modelFiles = ["model_cards.json", ...eps.flatMap((e) => [cards[e].model_file, cards[e].domain_file])];

        // -------------------------------------------------------------- read
        let molecules = null;
        await ctx.stage("read", async (s) => {
          const out = await pythonCall(ctx, lease, "admet_read", { molecules: source.rec ? `${root}/inputs/${source.name}` : source.smiles, out_dir: `${root}/read` }, [0.05, 0.05], "reading the molecules");
          if (out && typeof out.refused === "string" && out.refused) {
            throw Object.assign(new Error(`refused: ${out.refused}`), { type: "refused", need: out.need || undefined, hint: out.need === "runner" ? `${RUNNER}: it reads this file natively.` : undefined });
          }
          const kept = await lease.call("persist", { from: `${root}/read`, dir: [...ctx.dir, "read"], clean: true }, { signal: ctx.signal });
          molecules = await store.readJson([...ctx.dir, "read", "molecules.json"]);
          if (!Array.isArray(molecules?.smiles) || !Array.isArray(molecules?.names) || molecules.smiles.length !== molecules.names.length) {
            throw Object.assign(new Error("admet_read wrote no molecules.json of names and SMILES"), { type: "failed" });
          }
          if (molecules.smiles.length > MAX_MOLECULES) {
            throw refusal("refused", `${molecules.smiles.length} molecules, more than the ${MAX_MOLECULES.toLocaleString("en-US")} a run in this browser takes`, "memory",
              `${RUNNER}: it runs the same pipeline natively without that limit; or split the file.`);
          }
          digests.read = kept.files.find((f) => f.path.join("/") === "molecules.json")?.sha256 ?? null;
          s.record({ n: molecules.smiles.length, worker: lease.id });
          return { detail: `${plural(molecules.smiles.length, "molecule")} read`, worker: lease.id };
        });

        // -------------------------------------------------------------- chemistry
        const pdir = [...ctx.dir, "prepared"];
        const cDigest = `sha256:${await sha256Hex(canonicalJson({ smiles: smilesSha256(molecules.smiles), engine: CHEMISTRY, wasm: sh.wasm.sha256 }))}`;
        let counts = null;
        await ctx.stage("chemistry", async (s) => {
          const t0 = ms();
          const prep = await chem.prepareAll(molecules.smiles, {
            alerts: true, signal: ctx.signal, onProgress: (done, total) => ctx.progress(0.1 + 0.3 * (done / total), `${done} of ${total} molecules prepared`),
          });
          await store.remove(pdir, { recursive: true }).catch(() => false);
          const outputs = await writePrepared(store, pdir, molecules.smiles, prep, { signal: ctx.signal, rel: ["prepared"] });
          counts = preparedCounts(prep.rows);
          await store.writeJson([...ctx.dir, "state", "chemistry.json"], { counts });
          s.record({ prepared: counts, engine: CHEMISTRY, rdkit: chem.info?.rdkit ?? null, ms: prep.ms });
          return { detail: countsText(counts), checkpoint: { inputs_digest: cDigest, outputs }, compute: chemFacts(counts.n, ms() - t0) };
        }, {
          reuse: async () => {
            const check = await ctx.checkpoint.reusable("chemistry", { inputs_digest: cDigest });
            if (!check.ok) return check;
            counts = (await store.readJson([...ctx.dir, "state", "chemistry.json"]).catch(() => null))?.counts ?? null;
            return { ok: true, detail: `reused: its ${check.checked} prepared files re-hash to the digests recorded when they were written` };
          },
        });
        const prepared = (await ctx.checkpoint.read("chemistry"))?.outputs || [];
        for (const f of prepared) digests[`prepared/${f.path.at(-1)}`] = f.sha256;

        // -------------------------------------------------------------- domain
        const backends = {};
        let similarity = false;
        await ctx.stage("domain", async (s) => {
          const pref = sh.preferenceNow();
          const bits = await store.readBytes([...pdir, "bits.u8"]);
          const nq = bits ? bits.length / N_BITS_BYTES : 0;
          s.record({ preference: pref, endpoints: eps, queries: nq });
          if (pref === "reference") return { detail: "the reference implementation was asked for: Python's own search computes the similarities in the analysis" };
          if (!nq) return { detail: "no molecule was prepared: nothing to search" };
          const calibration = pref === "auto" && !sh.force ? await sh.deviceCalibration() : null;
          const gpu = pref === "auto" ? sh.scienceGpu() : null;
          const queries = packedWords(bits);
          const sdir = [...ctx.dir, "similarity"];
          await store.remove(sdir, { recursive: true }).catch(() => false);
          const entries = {};
          for (const [i, e] of eps.entries()) {
            ctx.throwIfCancelled();
            const card = cards[e];
            const file = await store.readBytes([...modelsDir(store), card.domain_file]);
            if (!file || (await sha256Hex(file)) !== bareSha256(card.domain_sha256)) {
              throw Object.assign(new Error(`${card.domain_file} does not match its model card: rebuild the models (job.admet.models with rebuild true)`), { type: "integrity" });
            }
            const rows = npyRows(file);
            const { result, facts } = await runTanimoto({
              queries, refs: packedWords(rows.data), preference: pref, calibration, gpu, navigator: sh.nav, isCancelled: () => ctx.signal.aborted, force: sh.force ? "webgpu" : null,
            });
            s.compute(facts);
            backends[e] = facts.backend;
            await store.writeBytes([...sdir, `${e}.u32`], tanimotoRows(result));
            entries[e] = { refs_sha256: await sha256Hex(rows.data), n_query: nq, n_ref: rows.n, backend: facts.backend };
            ctx.progress(0.4 + 0.15 * ((i + 1) / eps.length), `${e}: ${facts.backend}`);
          }
          const names = [...new Set(Object.values(backends))].sort();
          await store.writeJson([...sdir, "similarity.json"], { schema: "tcmstudio.admet-similarity/1", backend: names.join("+"), endpoints: entries });
          similarity = true;
          const by = names.map((b) => `${b} for ${Object.values(backends).filter((x) => x === b).length}`).join(", ");
          return { detail: `${plural(eps.length, "endpoint")} × ${plural(nq, "molecule")}: ${by}`.slice(0, 1000) };
        });
        const simFiles = similarity ? ["similarity.json", ...eps.map((e) => `${e}.u32`)] : [];
        for (const f of simFiles) digests[`similarity/${f}`] = await sha256Hex((await store.readBytes([...ctx.dir, "similarity", f])) || new Uint8Array(0));

        // -------------------------------------------------------------- analysis
        // the models mounted only now: a mounted file is held open by the worker (a sync access handle), and the domain
        // stage above reads the domains from here
        await lease.call("mount", { root: `${root}/models`, dir: modelsDir(store), files: modelFiles }, { signal: ctx.signal });
        await lease.call("mount", { root: `${root}/prepared`, dir: pdir, files: ["prepared.json", "features.f32", "bits.u8"] }, { signal: ctx.signal });
        if (similarity) await lease.call("mount", { root: `${root}/similarity`, dir: [...ctx.dir, "similarity"], files: simFiles }, { signal: ctx.signal });
        const modelDigests = Object.fromEntries(eps.map((e) => [e, [cards[e].model_sha256, cards[e].domain_sha256]]));
        const aDigest = `sha256:${await sha256Hex(canonicalJson({ params: p, inputs: digests, models: modelDigests, runtime }))}`;
        const args = {
          molecules_file: `${root}/read/molecules.json`, prepared_dir: `${root}/prepared`, ...(similarity ? { similarity_dir: `${root}/similarity` } : {}),
          models_dir: `${root}/models`, ...(asked.length ? { endpoints: asked } : {}), out_dir: `${root}/out`,
        };
        const limits = async (s) => {
          const record = await store.readJson([...ctx.dir, "out", "run.json"]).catch(() => null);
          const md = await store.readBytes([...ctx.dir, "out", "limitations.md"]).catch(() => null);
          const lines = md ? limitationLines(new TextDecoder().decode(md)) : [];
          lines.push(computedLine({ sklearn: versions["scikit-learn"], archives: eps.map((e) => cards[e].archive_sha256), similarity: similarityWords(record?.similarity, backends) }));
          if (!lines.includes(LIMIT_PREDICTED)) lines.push(LIMIT_PREDICTED);
          s?.record({ packages: versions, similarity: record?.similarity ?? null });
          // Python did not use the similarities this page offered (its check of them failed) and searched the domain
          // itself: the domain stage's kernel results are not what the outputs rest on, so they count for nothing (no
          // WebGPU, no "accelerated" for a result the run threw away)
          if (similarity && isPlainObject(record?.similarity) && record.similarity.rejected) {
            ctx.discardCompute("domain", `not used: Python rejected these similarities and searched the domain on its CPU (${String(record.similarity.rejected).slice(0, 200)})`);
          }
          const models = Object.entries(isPlainObject(record?.models) ? record.models : {}).sort(([a], [b]) => (a < b ? -1 : 1))
            .map(([id, c]) => ({ id, sha256: c?.model_sha256 ?? null, archive_sha256: c?.archive_sha256 ?? null, metric: c?.metric ?? null, test_score: c?.test_score ?? null }));
          ctx.outcome({ limitations: lines, ...(models.length ? { provenance: { models } } : {}) });
        };
        await ctx.stage("analysis", async (s) => {
          const out = await pythonCall(ctx, lease, "run_admet", args, [0.55, 0.3], "the analysis");
          s.record({ exit: out.exit, failed: out.failed || [], worker: lease.id });
          if (out.exit === 2) {
            const e = new Error(`refused: ${out.refused || "the pipeline refused its inputs"}`);
            e.type = "refused";
            if (typeof out.need === "string" && out.need) e.need = out.need;
            throw memoryRefusal(e, "the analysis") || e;
          }
          summary = out.summary ?? null;
          const kept = await lease.call("persist", { from: `${root}/out`, dir: [...ctx.dir, "out"], clean: true }, { signal: ctx.signal });
          ctx.forget(["out"]);
          for (const f of kept.files) ctx.artefact(artefactOf(f.path, f));
          await limits(s);
          if (out.exit !== 0) {
            // each stage at fault with what its record says (the command prints the same reasons)
            const why = (out.failed || []).map((name) => (out.reasons?.[name] ? `${name} (${out.reasons[name]})` : name));
            throw Object.assign(new Error(`the pipeline did not complete${why.length ? `: ${why.join("; ")}` : ` (exit ${out.exit})`}`), { type: "failed" });
          }
          await store.writeJson(SUMMARY, { summary });
          return {
            detail: `${kept.files.length} output files`, worker: lease.id,
            checkpoint: { inputs_digest: aDigest, outputs: kept.files.map((f) => ({ path: ["out", ...f.path], bytes: f.bytes, sha256: f.sha256 })) },
          };
        }, {
          reuse: async (s) => {
            const check = await ctx.checkpoint.reusable("analysis", { inputs_digest: aDigest });
            if (!check.ok) return check;
            const kept = await store.readJson(SUMMARY).catch(() => null);
            if (!isPlainObject(kept)) return { ok: false, problems: ["the earlier attempt's summary is gone"] };
            summary = kept.summary ?? null;
            await lease.call("restore", { dir: [...ctx.dir, "out"], to: `${root}/out` }, { signal: ctx.signal });
            await lease.call("free", { paths: [`${root}/out/job_receipt.json`] }, { signal: ctx.signal });
            const rec = await ctx.checkpoint.read("analysis");
            for (const o of rec.outputs) ctx.artefact(artefactOf(o.path.slice(1), o));
            s.worker = lease.id;
            await limits(s);
            return { ok: true, detail: `reused: its ${check.checked} outputs re-hash to the digests recorded when it ran` };
          },
        });

        // -------------------------------------------------------------- verify
        await ctx.stage("verify", async (s) => {
          const v = await pythonCall(ctx, lease, "verify_pipeline", { kind: "pipeline.admet", out_dir: `${root}/out` }, [0.88, 0.12], "the check of the outputs");
          s.record({ status: v.status, worker: lease.id });
          const record = await store.readJson([...ctx.dir, "out", "run.json"]).catch(() => null);
          ctx.outcome({
            checks: (v.checks || []).filter((c) => c.status === "PASS").map((c) => c.name), problems: v.problems || [],
            ...(typeof record?.complete === "boolean" ? { complete: record.complete } : {}),
          });
          if (v.status !== "PASS") throw Object.assign(new Error(`the outputs did not verify: ${(v.problems || []).slice(0, 3).join("; ") || v.status}`), { type: "failed" });
          return { detail: "the run re-verified (its outputs and inputs re-hashed, its table checked) and held to the runner's checks", worker: lease.id, verified: true };
        });
      } catch (err) {
        if (err?.need === "memory") exhausted = true;
        throw err;
      } finally {
        chem.terminate();
        if (exhausted) lease.terminate();
        else {
          await lease.call("free", { paths: [root] }).catch(() => {});
          lease.release();
        }
      }
      return { result: summary };
    },
  };
}

/** An output as the job records it: the runner's artefact name for a file the kind requires, else its path. */
function artefactOf(rel, f) {
  const file = rel.join("/");
  const named = Object.entries(ARTEFACTS).find(([, path]) => path === file);
  return { name: named ? named[0] : file, path: ["out", ...rel], bytes: f.bytes, sha256: f.sha256 };
}

/** The handlers as this module gives them without a site (the pinned RDKit.js, the page's Worker): what the router and
 * the jobs runtime ask needsRunner of; createJobsRuntime makes its own with boot.json's rdkit entry. */
export const ADMET_HANDLERS = createAdmetHandlers();

/** What tests, the harness and the router read. */
export const ADMET = Object.freeze({
  packages: PACKAGES, artefacts: ARTEFACTS, endpoints: ENDPOINTS, maxMolecules: MAX_MOLECULES, limitPredicted: LIMIT_PREDICTED,
  sdfSuffixes: SDF_SUFFIXES, chemistry: CHEMISTRY, computedLine,
});
