// Docking in this browser (studio/docs/BROWSER_JOBS.md): the runner's pipeline.dock (studio/runner/src/tcmstudio/kinds.py)
// in its prepared mode, run here as bioagent.docking.prepared itself. The receptor is a rigid PDBQT file and the ligands
// a PDBQT file of one ligand or several (MODEL/ENDMDL blocks), prepared elsewhere; the site is a box (center and size) or
// one built around reference_ligand, the co-crystal ligand as a prepared PDBQT in its crystal pose, which is also docked
// back to validate the site. The Python does the reading, the analysis and the checks in a job worker holding Pyodide's
// numpy (tcmstudio.browser_jobs dock_read, run_dock, verify_pipeline); the docking is AutoDock Vina 1.2.7 compiled to
// WebAssembly (../dock/vina.js, the build studio/scripts/build_vina_wasm.py makes), one Vina run per call in dedicated
// workers of its own (../dock/dock.worker.js), whose results Python reads back (PrecomputedVina) and holds to the texts
// and parameters it asked for.
//
// Storage (OPFS, under tcmstudio/jobs/<id>/):
//   inputs/                 the project's receptor, ligands and reference ligand, copied and held to their SHA-256
//   read/plan/              what dock_read wrote: the receptor, each ligand's and the reference's exact Vina input
//                           (ligand-<i>.pdbqt, reference.pdbqt) and dock_plan.json (tcmstudio.dock-plan/1)
//   results/<key>.json      one Vina run's result (tcmstudio.vina-result/1; key "reference" or "ligand-<i>"), the
//                           bytes the Vina worker hashed, hashed again by this page as it writes them
//   checkpoints/dock.<key>.json   each run's checkpoint: one stage, a checkpoint per Vina run
//   out/                    run_dock's outputs (run.json, scores.tsv, report.html, limitations.md, poses/ …)
//
// pipeline.dock: inputs → read → dock → analysis → verify. The dock stage runs the reference (when there is one) and
// every ligand, in the plan's order, on N workers side by side (N = clamp(min(cores − 1, ⌊(memory − 1.5 GB) / 2.25 GB⌋,
// 4), 1, runs), the 1.5 GB being the job's own Pyodide worker, held throughout): each worker its own Vina module,
// compiled from the same wasm bytes this page fetched once and checked
// against the pin. A run whose result still re-hashes to its checkpoint, made from the same receptor, ligand,
// parameters, box and wasm, is reused. Vina cannot be interrupted in the middle of a run: a cancel terminates every Vina worker at once, and the
// runs already finished keep their checkpoints. Refused before anything is queued, with the runner named as what would
// run it: a receptor or ligands that are not PDBQT (preparing them needs Meeko and RDKit), site_ligand and
// allow_remote; more ligands than a run here takes (memory). Arguments that mix the two modes (a .pdbqt receptor with
// SMILES, CSV or SDF ligands, site_ligand or allow_remote; a PDB receptor with .pdbqt ligands or reference_ligand) are
// refused as bad arguments: the runner refuses them too (kinds.py), so it is not named as what would run them.

import { abortError, canonicalJson, isPlainObject, sha256Hex } from "../../core/util.js";
import { FILE_ID } from "../../core/catalog.js";
import { Sha256 } from "../data/digest.js";
import { ENGINE, RESULT_SCHEMA, SEARCH, VINA, VinaError, fetchWasm, vinaParams, vinaResultText } from "../dock/vina.js";
import { bareSha256 } from "../engines/evidence.js";
import { GB_PER_WORKER } from "../runtime/worker-pool.js";
import {
  digestMismatch, limitationLines, memoryRefusal, projectFile, pythonCall, refusal, safeName, storageRefusal, work,
} from "./shared.js";
import { LIMIT_PREDICTED } from "./admet.js";

/** Pyodide packages bioagent.docking.prepared imports (beyond the standard library). */
const PACKAGES = ["numpy"];
/**
 * The most ligands one pipeline.dock run takes here: tcmstudio.browser_jobs.DOCK_LIGANDS_MAX, the bound Python holds
 * dock_read to (it refuses more with need "memory"; measured there: about 3.5 min for 100 fixture-sized ligands on four
 * Vina workers); the page refuses such a run before it is queued, counting the ligands file's MODEL records
 * (web/test/scientific/dock.test.mjs holds the two numbers to each other).
 */
export const MAX_LIGANDS = 100;
/** Vina workers side by side at most: each holds a module of its own, and the page needs the main thread's core. */
export const MAX_VINA_WORKERS = 4;
/**
 * The memory one Vina worker is planned with: above the most one instance took within what dock_read lets through
 * (tcmstudio.browser_jobs, measured: 2,059 MiB for a ligand of 79 atoms in a 60 Å box at exhaustiveness 64 with a
 * receptor of 100,243 atoms, 1,957 MiB with the 701-atom pocket; 357-429 MB for the fixture's ligands in an 18 Å box).
 * The job's own Pyodide worker (GB_PER_WORKER) is held through the docking and planned beside them; the page and the
 * browser itself are not planned for, so the plan bounds the workers' share of the device, not the device.
 */
export const GB_PER_VINA = 2.25;
const ASSUMED_CORES = 2;
const ASSUMED_MEMORY_GB = 2;
const PLAN_SCHEMA = "tcmstudio.dock-plan/1";
const SUFFIX = ".pdbqt";
const RUNNER = "Connect the local runner";
const PREPARE_HINT = `${RUNNER}: it prepares receptors (a PDB file, PDB:<id>, UniProt:<acc>) and ligands (SMILES, CSV, SDF, .smi) with Meeko and RDKit and docks them with AutoDock Vina. Or give a prepared receptor and ligands as PDBQT files of this project.`;
/** What the docking says of its inputs' preparation, which nothing here checks. */
export const PREPARED_LINE = "Prepared inputs: the receptor's and the ligands' preparation (protonation states, partial charges, AutoDock atom types, torsion trees) is the user's and was not checked here.";
/** The parameters pipeline.dock takes (the runner's catalog entry); those of its runner mode are judged by needsRunner. */
const PARAMS = ["receptor", "ligands", "reference_ligand", "center", "size", "scoring", "exhaustiveness", "poses", "seed", "site_ligand", "chains", "allow_remote"];
/** The parameters Python's dock_read and run_dock take beside the files (the job's own names). */
const PY_PARAMS = ["center", "size", "scoring", "exhaustiveness", "poses", "seed"];

// absent (core/catalog.js runnerOnly): not given, null or empty; anything else is given
const given = (v) => v !== undefined && v !== null && v !== "";
const present = (v) => v !== undefined && v !== null;
const sha12 = (sha) => String(sha).slice(0, 12);
const plural = (n, one, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;
const ms = () => (globalThis.performance?.now ? globalThis.performance.now() : Date.now());
const exact = new TextDecoder("utf-8", { fatal: true, ignoreBOM: true });

// ------------------------------------------------------------------------------------------------- arguments

function bad(message) {
  return refusal("bad_arguments", message, "arguments");
}

function runnerRefusal(why) {
  return refusal("unavailable", why.message, "runner", why.hint);
}

/** A receptor or ligands value that names something only the runner prepares (not a PDBQT file), by its text alone. */
function notPrepared(arg, value) {
  const v = typeof value === "string" ? value.trim() : "";
  if (!v || FILE_ID.test(v) || v.toLowerCase().endsWith(SUFFIX)) return null;
  return preparationNeed(arg, v);
}

function preparationNeed(arg, v) {
  if (arg === "receptor") {
    return {
      message: `receptor: ${JSON.stringify(v)} is not a prepared PDBQT receptor; preparing one from a PDB file, PDB:<id> or UniProt:<acc> (Meeko, gemmi) runs only on the runner. This browser docks a rigid receptor given as a PDBQT file`,
      hint: PREPARE_HINT,
    };
  }
  return {
    message: `ligands: ${JSON.stringify(v)} is not a prepared PDBQT file; ligands given as SMILES, CSV, SDF or .smi are prepared with RDKit and Meeko, which run only on the runner. This browser docks ligands given as a PDBQT file (one ligand, or several as MODEL blocks)`,
    hint: PREPARE_HINT,
  };
}

/** A receptor or ligands value by its text → "prepared" (a .pdbqt file's name), "other" (another name, a PDB:/UniProt:
 * id, a SMILES), or null (not given, or a file id: its name is known once the file is found). */
function nameMode(value) {
  const v = typeof value === "string" ? value.trim() : "";
  if (!v || FILE_ID.test(v)) return null;
  return v.toLowerCase().endsWith(SUFFIX) ? "prepared" : "other";
}

/**
 * Arguments that mix the two modes → why, or null. The runner prepares only for a PDB receptor and docks a .pdbqt
 * receptor only with .pdbqt ligands (kinds.py refuses the rest when a call is submitted, in these words), so such a
 * call runs nowhere: a bad argument, not the runner's. receptor, ligands: their names, as a call gives them or as the
 * project's files are named once found (null while not known).
 */
function mixedModes(p, receptor, ligands) {
  const r = nameMode(receptor);
  const l = nameMode(ligands);
  if (r === "other") {
    if (given(p.reference_ligand)) return "reference_ligand: for a prepared receptor (.pdbqt); with a PDB receptor name the co-crystal ligand with site_ligand (on the runner)";
    if (l === "prepared") return "ligands: prepared .pdbqt ligands dock against a prepared .pdbqt receptor; give the receptor as a .pdbqt, or the ligands as SMILES, CSV, SDF or .smi for the runner to prepare";
  }
  if (r === "prepared") {
    if (l === "other") return "ligands: with a .pdbqt receptor, ligands is a .pdbqt file as well (prepared ligands, one or several as MODEL blocks); preparing ligands from SMILES, CSV or SDF needs a PDB receptor, on the runner";
    if (given(p.site_ligand)) return "site_ligand: a .pdbqt receptor is used as prepared, so site_ligand cannot apply; name the site with center and size, or give the co-crystal ligand prepared in its crystal pose as reference_ligand";
    if (p.allow_remote === true) return "allow_remote: a .pdbqt receptor is used as prepared, so allow_remote cannot apply; nothing is fetched: the receptor is the .pdbqt given";
  }
  return null;
}

/**
 * Why this pipeline.dock call needs the runner, from its parameters alone → {message, hint}, or null: the catalog's
 * runner_only rules (receptor and ligands not_suffix .pdbqt, site_ligand present, allow_remote true; core/catalog.js
 * runnerOnly), but for a call that mixes the modes (mixedModes), which the runner refuses too: null, and the job
 * refuses it as bad arguments. The catalog's rules, read without this module, cannot see that. A file named by its id
 * is judged once the job has found it (preflight, and the inputs stage).
 */
export function dockNeedsRunner(params) {
  const p = isPlainObject(params) ? params : {};
  if (mixedModes(p, p.receptor, p.ligands)) return null;
  for (const arg of ["receptor", "ligands"]) {
    const why = notPrepared(arg, p[arg]);
    if (why) return why;
  }
  if (given(p.site_ligand)) {
    return {
      message: "site_ligand: a site taken from a co-crystal ligand's residue name needs the structure's PDB file and that ligand's SMILES (RCSB), which only the runner reads; here the site is center and size, or reference_ligand",
      hint: `${RUNNER}, or give center and size (Å), or reference_ligand: the co-crystal ligand as a prepared PDBQT in its crystal pose.`,
    };
  }
  if (p.allow_remote === true) {
    return {
      message: "allow_remote: fetching a structure from RCSB or AlphaFold DB, and preparing it, runs only on the runner; this browser docks prepared PDBQT files of the project",
      hint: PREPARE_HINT,
    };
  }
  return null;
}

function checkInt(name, value, [low, high], why = "") {
  if (typeof value !== "number" || !Number.isInteger(value)) throw bad(`${name}: a whole number is needed, not ${JSON.stringify(value)}`);
  if (value < low || value > high) throw bad(`${name}: between ${low} and ${high}, not ${value}${why}`);
  return value;
}

function checkTriple(name, value, { positive = false } = {}) {
  if (!Array.isArray(value) || value.length !== 3 || !value.every((x) => typeof x === "number" && Number.isFinite(x) && (!positive || x > 0))) {
    throw bad(`${name}: three finite numbers (x, y, z; Å${positive ? "; each > 0" : ""}), not ${JSON.stringify(value)}`);
  }
}

function checkFile(name, value) {
  if (typeof value !== "string" || !value.trim()) throw bad(`${name}: a PDBQT file of this project, by its name or id`);
}

/** Unknown parameters refused (one the runner would read differently would change the result). */
function checkKnown(p) {
  const extra = Object.keys(p).filter((k) => given(p[k]) && !PARAMS.includes(k)).sort();
  if (extra.length) throw bad(`unknown parameter${extra.length > 1 ? "s" : ""} ${extra.join(", ")}; pipeline.dock takes ${PARAMS.join(", ")}`);
}

/**
 * A prepared-mode call's parameters, checked as bioagent.docking.prepared checks them (PreparedDockConfig, site_box):
 * Python checks them again when it reads the inputs. The bounds are the runner's (SEARCH: vina.js), but for the seed:
 * 0 is refused, for Vina would pick a random one.
 */
function checkPrepared(p) {
  checkFile("receptor", p.receptor);
  checkFile("ligands", p.ligands);
  if (given(p.reference_ligand)) checkFile("reference_ligand", p.reference_ligand);
  if (given(p.chains)) throw bad("chains: selects the chains of a PDB receptor the runner prepares; a prepared PDBQT receptor is docked as it is given");
  if (present(p.scoring) && !SEARCH.scoring.includes(p.scoring)) throw bad(`scoring: ${SEARCH.scoring.join(" or ")}, not ${JSON.stringify(p.scoring)}`);
  if (present(p.exhaustiveness)) checkInt("exhaustiveness", p.exhaustiveness, SEARCH.exhaustiveness);
  if (present(p.poses)) checkInt("poses", p.poses, SEARCH.n_poses);
  if (present(p.seed)) checkInt("seed", p.seed, SEARCH.seed, p.seed === 0 ? " (0 would let Vina pick a random seed: the run could not be repeated)" : "");
  if (present(p.center)) checkTriple("center", p.center);
  if (present(p.size)) checkTriple("size", p.size, { positive: true });
  if (present(p.center) && !present(p.size)) throw bad("center: give size with it (three numbers, Å)");
  if (!present(p.center) && !given(p.reference_ligand)) {
    throw bad(`give the docking site: center and size (Å), or reference_ligand (the co-crystal ligand as a prepared PDBQT in its crystal pose; the box is built around it${present(p.size) ? ", with the size given" : ""})`);
  }
}

/** The parameters, checked → whether they are this browser's to run (prepared mode). Throws a refusal otherwise. */
function checkDock(p) {
  checkKnown(p);
  const mixed = mixedModes(p, p.receptor, p.ligands);
  if (mixed) throw bad(mixed);
  const tied = dockNeedsRunner(p);
  if (tied) throw runnerRefusal(tied);
  checkPrepared(p);
}

/** The arguments of dock_read and run_dock beside the files: the job's own, those given. */
function pyParams(p) {
  return Object.fromEntries(PY_PARAMS.filter((k) => present(p[k])).map((k) => [k, p[k]]));
}

// ------------------------------------------------------------------------------------------------- the Vina workers

function cancelled() {
  const e = abortError("the job was cancelled");
  try { e.type = "cancelled"; } catch { /* a frozen DOMException */ }
  return e;
}

function dockError(type, message, extra = {}) {
  const e = new Error(message);
  e.type = type;
  return Object.assign(e, extra);
}

const WASM_HINT = `The docking loads AutoDock Vina ${VINA.version}'s wasm (${(VINA.wasm.bytes / 1000).toFixed(0)} kB, from this site) once, at its pinned SHA-256: reload the page, or ${RUNNER.toLowerCase()} (native Vina).`;

/** Vina running out of memory, in a run (std::bad_alloc, the module's own abort) or before it starts: V8 refusing to
 * instantiate a module ("Out of memory: Cannot allocate Wasm memory for new instance"), or to grow its memory. */
const VINA_OUT_OF_MEMORY = /out of (WebAssembly )?memory|bad_alloc|Cannot allocate Wasm memory|Cannot enlarge memory|Maximum memory size exceeded|Memory\.grow/i;

/** The Vina worker's error as the job says it: a wasm that is not the pin, or one that could not be fetched, is the
 * engine being unavailable here; Vina running out of memory (in a run, or starting) is a refusal the runner answers;
 * Vina failing (a trap) is a failure of the run. */
function vinaOpError(e) {
  const message = `the browser docking: ${e.message || e.type || "failed"}`;
  if (e.type === "bad_wasm") return dockError("unavailable", message, { need: "network", hint: WASM_HINT });
  if (e.type === "vina" && VINA_OUT_OF_MEMORY.test(String(e.message))) {
    return dockError("refused", message, { need: "memory", hint: `${RUNNER}: it docks natively, without the browser's memory limit.` });
  }
  if (e.type === "vina") return dockError("runtime_error", `${message} (AutoDock Vina itself failed; its memory is no longer trusted, so the job stopped)`);
  return dockError("runtime_error", message);
}

/**
 * How many Vina workers dock side by side → {n, reason}: clamp(min(cores − 1, ⌊(memory − 1.5 GB) / 2.25 GB⌋, 4), 1,
 * runs), the job's own Pyodide worker (planned at GB_PER_WORKER, ../runtime/worker-pool.js) holding its share
 * throughout. A figure the browser does not report is assumed (2 cores, 2 GB), and the reason says so;
 * navigator.deviceMemory is rounded down and capped at 8 by the browser itself.
 */
export function vinaWorkers({ cores, memoryGb, runs }) {
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
  const r = Math.max(1, Math.floor(Number(runs)) || 1);
  const byMemory = Math.floor(Math.max(0, m - GB_PER_WORKER) / GB_PER_VINA);
  const n = Math.max(1, Math.min(c - 1, byMemory, MAX_VINA_WORKERS, r));
  const parts = [
    `${plural(n, "Vina worker")}: ${plural(c, "core")} leave ${Math.max(0, c - 1)} beside the page`,
    `${m} GB less the job worker's ${GB_PER_WORKER} GB allows ${byMemory} at ${GB_PER_VINA} GB each`,
    `at most ${MAX_VINA_WORKERS}`,
    `${plural(r, "run")} to do`,
    ...assumed,
  ];
  return { n, reason: parts.join("; ") };
}

/**
 * The page's side of one ../dock/dock.worker.js: a module worker, AutoDock Vina started in it from the bytes the page
 * checked, one Vina run per call. A run cannot be interrupted: on its signal's abort the worker is terminated at once.
 */
export class VinaClient {
  #Worker; #url; #name;
  #worker = null; #seq = 0; #pending = new Map(); #info = null;

  /** Worker: the Worker constructor; url: dock.worker.js; name: the worker's name (its compute facts say it). */
  constructor({ Worker = globalThis.Worker, url, name = "vina" } = {}) {
    this.#Worker = Worker;
    this.#url = url;
    this.#name = name;
  }

  get id() { return this.#name; }
  /** What init reported ({engine, wasm, ms}), once started. */
  get info() { return this.#info; }

  /** Start the worker and Vina in it from `bytes` (a copy goes to the worker) → init's result. */
  async start(bytes, { sha256 = VINA.wasm.sha256, signal } = {}) {
    if (typeof this.#Worker !== "function") throw dockError("unavailable", "this browser cannot start the docking worker (no Web Worker)");
    try {
      this.#worker = new this.#Worker(this.#url, { type: "module", name: `tcmstudio-${this.#name}` });
    } catch (err) {
      throw dockError("unavailable", `the docking worker could not start (${err?.message || err})`);
    }
    this.#worker.onmessage = (ev) => this.#onMessage(ev.data);
    this.#worker.onerror = (ev) => this.#crash(`the docking worker failed: ${ev?.message || "an error it did not report"}`);
    const copy = bytes.slice();
    this.#info = await this.#call("init", { wasmBytes: copy, sha256 }, { signal, transfer: [copy.buffer] });
    return this.#info;
  }

  /** One Vina run: {key, receptor, ligand, params} → the worker's tcmstudio.vina-result/1 object (with result_sha256). */
  dock(payload, { signal } = {}) {
    return this.#call("dock", payload, { signal });
  }

  terminate() {
    const w = this.#worker;
    this.#worker = null;
    if (w) { try { w.terminate(); } catch { /* gone */ } }
    const pending = [...this.#pending.values()];
    this.#pending.clear();
    for (const p of pending) p.reject(p.signal?.aborted ? cancelled() : dockError("runtime_error", "the docking worker was stopped before it answered"));
  }

  #call(op, payload, { signal, transfer = [] } = {}) {
    const worker = this.#worker;
    if (!worker) return Promise.reject(dockError("runtime_error", "the docking worker is not running"));
    if (signal?.aborted) {
      this.terminate();
      return Promise.reject(cancelled());
    }
    const id = ++this.#seq;
    return new Promise((resolve, reject) => {
      // Vina blocks its worker until the run ends: nothing but terminating it stops the run
      const onAbort = () => this.terminate();
      this.#pending.set(id, {
        resolve: (v) => { signal?.removeEventListener?.("abort", onAbort); resolve(v); },
        reject: (e) => { signal?.removeEventListener?.("abort", onAbort); reject(e); },
        signal,
      });
      signal?.addEventListener?.("abort", onAbort, { once: true });
      worker.postMessage({ id, op, payload }, transfer);
    });
  }

  #onMessage(msg) {
    if (!msg || typeof msg !== "object") return;
    const p = this.#pending.get(msg.id);
    if (!p) return;
    this.#pending.delete(msg.id);
    if (msg.ok) { p.resolve(msg.result); return; }
    if (p.signal?.aborted) { p.reject(cancelled()); return; }
    p.reject(vinaOpError(msg.error || {}));
  }

  #crash(message) {
    const pending = [...this.#pending.values()];
    this.#pending.clear();
    this.terminate();
    for (const p of pending) p.reject(dockError("runtime_error", message));
  }
}

/** The wasm the docking loads: boot.json's vina entry (checked to be this code's pin), or null when the site names none. */
function vinaWasm(vina, siteUrl) {
  if (!isPlainObject(vina)) return null;
  const url = typeof vina.url === "string" && vina.url ? vina.url : VINA.wasm.url;
  return {
    url: siteUrl ? new URL(url, siteUrl).href : url, sha256: bareSha256(vina.sha256), bytes: vina.bytes ?? null,
    version: vina.version ?? null, loader: vina.loader ?? null,
  };
}

/** Why the site's AutoDock Vina pin is not the one this page's docking is written for, or null. */
function vinaMismatch(wasm) {
  if (wasm.sha256 !== VINA.wasm.sha256 || (wasm.bytes !== null && wasm.bytes !== VINA.wasm.bytes) || (wasm.version && wasm.version !== VINA.version) || (wasm.loader && wasm.loader !== VINA.loader)) {
    return `the site's boot.json pins AutoDock Vina ${wasm.version ?? "?"} (wasm sha256 ${sha12(wasm.sha256)}…), but this page's docking is written for AutoDock Vina ${VINA.version} (${sha12(VINA.wasm.sha256)}…): reload the page`;
  }
  return null;
}

/** The engine as the job's record and receipt name it (receipt.engine, as the runner's lists its engines). */
function engineRecord() {
  return [{ id: ENGINE.name, name: ENGINE.name, version: ENGINE.version, build: ENGINE.build, wasm_sha256: ENGINE.wasm_sha256, stages: ["dock"] }];
}

// ------------------------------------------------------------------------------------------------- files

/** The ligands a PDBQT file holds at most, as Python counts them: its MODEL records, else one. Streamed: never the
 * whole file in memory as text. */
async function ligandCount(blob) {
  let n = 0, head = "";
  const reader = blob.stream().getReader();
  const end = () => {
    if (/^MODEL(\s|$)/.test(head)) n += 1;
    head = "";
  };
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    for (const c of value) {
      if (c === 10 || c === 13) end();
      else if (head.length < 6) head += String.fromCharCode(c);
    }
  }
  end();
  return Math.max(1, n);
}

/** A file's bytes as the exact text they hold (UTF-8, a byte-order mark kept), or null when they are not UTF-8. */
function textOf(bytes) {
  try {
    return exact.decode(bytes);
  } catch {
    return null;
  }
}

/**
 * dock_plan.json held to the shape tcmstudio.browser_jobs dock_read writes (tcmstudio.dock-plan/1) → the plan. What
 * this page reads of it is checked; anything else is Python's.
 */
function checkPlan(doc) {
  const fail = (why) => dockError("failed", `dock_read wrote a plan this page cannot use: ${why}`);
  if (!isPlainObject(doc) || doc.schema !== PLAN_SCHEMA) throw fail(`not ${PLAN_SCHEMA}`);
  const file = (x) => isPlainObject(x) && typeof x.file === "string" && x.file && bareSha256(x.sha256);
  if (!file(doc.receptor)) throw fail("no receptor file and SHA-256");
  if (!Array.isArray(doc.ligands) || !doc.ligands.length) throw fail("no ligand");
  doc.ligands.forEach((l, i) => {
    if (!file(l) || l.index !== i || l.key !== `ligand-${i}` || typeof l.name !== "string") throw fail(`ligand ${i} is not {index, key: "ligand-${i}", name, file, sha256}`);
  });
  if (present(doc.reference) && !(file(doc.reference) && doc.reference.key === "reference")) throw fail("its reference is not {key: \"reference\", file, sha256}");
  const triple = (v) => Array.isArray(v) && v.length === 3 && v.every((x) => typeof x === "number" && Number.isFinite(x));
  if (!isPlainObject(doc.box) || !triple(doc.box.center) || !triple(doc.box.size)) throw fail("no box (center, size)");
  if (!isPlainObject(doc.params)) throw fail("no parameters");
  return doc;
}

/** A plan file's name inside the plan directory (its last segment: the plan names files, never other places). */
const planFile = (name) => String(name).split(/[\\/]/).filter(Boolean).at(-1);

/** One run's compute facts (the job receipt's compute section): which Vina ran it, where, on what size. */
function vinaFacts(task, n, worker, took) {
  return {
    op: "vina.dock", kernel: `AutoDock Vina ${VINA.version} global search`, backend: "vina-wasm", placement: "browser", where: "worker", worker,
    size: { heavy_atoms: task.heavy_atoms ?? null, torsions: task.torsions ?? null, exhaustiveness: n },
    gpu: null, accelerated: null, basis: "CPU, one thread per worker", ms: Math.round(took * 1000) / 1000,
  };
}

/** The browser's line about where the numbers were computed (kept under the 320 characters a job's limitation keeps
 * when the model reads it: jobs-runtime limitsText). */
export function computedLine(numpy) {
  return `Computed in this browser: the docking by AutoDock Vina ${VINA.version} compiled to WebAssembly (wasm SHA-256 ${sha12(VINA.wasm.sha256)}…, one thread per worker), the reading and the analysis by bioagent.docking.prepared in Pyodide (numpy ${numpy || "version not reported"}).`;
}

// ------------------------------------------------------------------------------------------------- the handler

const TITLE = { zh: "分子对接（预处理输入，浏览器）", en: "Molecular docking (prepared inputs, browser)" };
const STAGE = {
  inputs: { zh: "复制并核对输入文件", en: "Copy and check the input files" },
  read: { zh: "读取受体、配体与对接盒", en: "Read the receptor, the ligands and the box" },
  dock: { zh: `对接（AutoDock Vina ${VINA.version}，WebAssembly）`, en: `Dock (AutoDock Vina ${VINA.version}, WebAssembly)` },
  analysis: { zh: "重对接验证、姿势检查与报告", en: "Redocking check, pose checks and report" },
  verify: { zh: "按 Runner 的要求核对输出", en: "Check the outputs as the runner does" },
};
/** The runner's names of the outputs pipeline.dock requires (kinds.py). */
const ARTEFACTS = Object.freeze({ run: "run.json", scores: "scores.tsv", report: "report.html", limitations: "limitations.md" });
/** The project files a call names: [parameter, required]. */
const FILES = [["receptor", true], ["ligands", true], ["reference_ligand", false]];

/**
 * The docking handler → [pipeline.dock]. options: vina (boot.json's vina entry {version, loader, url, sha256, bytes};
 * null: the site names none, and the job is refused; default the code's own pin), VinaWorker and vinaWorkerUrl (the
 * Vina worker; default the page's Worker and ../dock/dock.worker.js), siteUrl (what a relative wasm URL is the site's
 * of), fetch (the wasm's download), navigator (its hardwareConcurrency and deviceMemory size the workers).
 */
export function createDockHandlers({
  vina = { version: VINA.version, loader: VINA.loader, url: VINA.wasm.url, sha256: VINA.wasm.sha256, bytes: VINA.wasm.bytes },
  VinaWorker = globalThis.Worker, vinaWorkerUrl = new URL("../dock/dock.worker.js", import.meta.url).href, siteUrl = null,
  fetch = globalThis.fetch?.bind(globalThis), navigator: nav = globalThis.navigator,
} = {}) {
  const wasm = vinaWasm(vina, siteUrl);
  const unavailable = () => {
    if (!wasm) return refusal("unavailable", "This site's runtime/boot.json names no AutoDock Vina build (its vina entry), so the browser docking cannot start.", "job_service", "Reload the page; if it stays so, the site was built without the docking engine: connect the local runner.");
    const why = vinaMismatch(wasm);
    if (why) return refusal("unavailable", why, "job_service", "Reload the page so that its code and the site's boot.json are of the same build.");
    if (typeof VinaWorker !== "function") return refusal("unavailable", "This browser cannot start the docking workers (no Web Worker).", "job_service", "Use a current Chromium-based browser such as Chrome or Edge, or connect the local runner.");
    return null;
  };
  const client = (i) => new VinaClient({ Worker: VinaWorker, url: vinaWorkerUrl, name: `vina-${i}` });
  const shared = { wasm, unavailable, client, fetch, device: () => ({ cores: nav?.hardwareConcurrency, memoryGb: nav?.deviceMemory }) };
  return [dockHandler(shared)];
}

/** The files a call names, found among the project's → {param: rec}; files that mix the modes refused as bad
 * arguments (mixedModes, on the files' own names), and a name that is not a .pdbqt refused for the runner (or, for the
 * reference, as a bad argument). */
async function dockFiles(p, projectFiles, projectId) {
  const out = {};
  for (const [param, required] of FILES) {
    if (!required && !given(p[param])) continue;
    const ref = String(p[param]).trim();
    const rec = await projectFile(projectFiles, projectId, param, ref);
    if (!rec) throw bad(`${param}: no file named ${JSON.stringify(ref)} in this project${projectId ? "" : " (the job belongs to no project, so only a file id can name one)"}`);
    out[param] = rec;
  }
  const mixed = mixedModes(p, out.receptor.name, out.ligands.name);
  if (mixed) throw bad(mixed);
  for (const [param, rec] of Object.entries(out)) {
    if (!rec.name.toLowerCase().endsWith(SUFFIX)) {
      if (param === "reference_ligand") throw bad(`reference_ligand: ${rec.name} is not a PDBQT file; give the co-crystal ligand as a prepared PDBQT in its crystal pose`);
      throw runnerRefusal(preparationNeed(param, rec.name));
    }
  }
  return out;
}

function dockHandler(sh) {
  return {
    kind: "pipeline.dock",
    title: TITLE,
    stages: ["inputs", "read", "dock", "analysis", "verify"].map((id) => ({ id, title: STAGE[id] })),
    expected: Object.keys(ARTEFACTS),
    needsRunner: dockNeedsRunner,

    async preflight(params, svc, { project_id = null } = {}) {
      const p = isPlainObject(params) ? params : {};
      checkDock(p);
      const engine = sh.unavailable();
      if (engine) throw engine;
      if (!svc.store) throw await storageRefusal(null);
      if (!svc.pool) throw refusal("unavailable", "This browser cannot start job workers.", "job_service");
      const files = await dockFiles(p, svc.projectFiles, project_id);
      const n = await ligandCount(files.ligands.blob);
      if (n > MAX_LIGANDS) {
        throw refusal("unavailable", `${files.ligands.name} holds up to ${n.toLocaleString("en-US")} ligands, more than the ${MAX_LIGANDS.toLocaleString("en-US")} a docking run in this browser takes.`, "memory",
          `${RUNNER}: it docks natively without that limit; or split the file into parts of at most ${MAX_LIGANDS.toLocaleString("en-US")} ligands.`);
      }
      // the inputs copied, Vina's texts of them written by dock_read, the results and the outputs (each pose file
      // about the size of its result)
      const bytes = Object.values(files).reduce((a, r) => a + (Number(r.bytes ?? r.blob.size) || 0), 0);
      const refused = await storageRefusal(svc.store, 3 * bytes + 2 * n * 40e3 + 20e6);
      if (refused) throw refused;
    },

    async run(ctx) {
      const p = isPlainObject(ctx.params) ? ctx.params : {};
      checkDock(p);
      const root = work(ctx.job);
      const store = ctx.store;
      const SUMMARY = [...ctx.dir, "summary.json"];
      const names = {};
      const digests = {};

      // ---------------------------------------------------------------- inputs
      await ctx.stage("inputs", async (s) => {
        const files = await dockFiles(p, ctx.projectFiles, ctx.job.project_id);
        const dir = [...ctx.dir, "inputs"];
        await store.remove(dir, { recursive: true }).catch(() => false);
        const taken = new Set();
        for (const [param, rec] of Object.entries(files)) {
          // the file's own name (Python names a lone ligand after its file); another parameter's file of the same
          // name gets the parameter's prefix
          let name = safeName(rec.name);
          if (taken.has(name)) name = `${param}-${name}`;
          taken.add(name);
          const h = new Sha256();
          const bytes = await store.writeBlob([...dir, name], rec.blob, { signal: ctx.signal, onChunk: (b) => h.update(b) });
          const sha = h.digestHex();
          if (sha !== bareSha256(rec.sha256)) {
            await store.remove([...dir, name]).catch(() => false);
            throw digestMismatch(rec.name, sha, rec.sha256);
          }
          names[param] = name;
          digests[param] = sha;
          ctx.input({ name: param, sha256: sha, bytes, source: { project_file: rec.id, name: rec.name } });
        }
        s.record({ inputs: { ...digests } });
        return { detail: `${Object.values(files).map((r) => r.name).join(", ")} copied and checked against the project's SHA-256` };
      });

      const lease = await ctx.pool.lease({ packages: PACKAGES });
      const clients = [];
      let summary = null;
      let exhausted = false;
      try {
        const loaded = await lease.call("packages", { names: PACKAGES }, { signal: ctx.signal });
        const versions = Object.fromEntries(PACKAGES.map((n) => [n, loaded?.versions?.[n] ?? null]));
        const runtime = { bundle: lease.info?.bundle?.sha256 ?? null, pyodide: lease.info?.pyodide?.version ?? null, packages: versions };
        await lease.call("mount", { root: `${root}/inputs`, dir: [...ctx.dir, "inputs"], files: Object.values(names) }, { signal: ctx.signal });
        const fileArgs = Object.fromEntries(Object.entries(names).map(([param, name]) => [param, `${root}/inputs/${name}`]));

        // -------------------------------------------------------------- read
        let plan = null;
        let planDir = null; // the plan's directory, in segments under read/
        let planSha = null;
        await ctx.stage("read", async (s) => {
          let out;
          try {
            out = await pythonCall(ctx, lease, "dock_read", { ...fileArgs, ...pyParams(p), out_dir: `${root}/read` }, [0.05, 0.05], "reading the inputs");
          } catch (err) {
            // Python's refusal for the runner (inputs that need preparing) says what would run them
            if (err?.need === "runner" && !err.hint) err.hint = PREPARE_HINT;
            throw err;
          }
          if (out && typeof out.refused === "string" && out.refused) {
            throw Object.assign(new Error(`refused: ${out.refused}`), { type: "refused", need: out.need || undefined, hint: out.need === "runner" ? PREPARE_HINT : undefined });
          }
          const kept = await lease.call("persist", { from: `${root}/read`, dir: [...ctx.dir, "read"], clean: true }, { signal: ctx.signal });
          const doc = kept.files.find((f) => f.path.at(-1) === "dock_plan.json");
          if (!doc) throw dockError("failed", "dock_read wrote no dock_plan.json");
          planDir = doc.path.slice(0, -1);
          planSha = doc.sha256;
          plan = checkPlan(await store.readJson([...ctx.dir, "read", ...planDir, "dock_plan.json"]));
          if (plan.ligands.length > MAX_LIGANDS) {
            throw refusal("refused", `${plan.ligands.length} ligands, more than the ${MAX_LIGANDS.toLocaleString("en-US")} a docking run in this browser takes`, "memory",
              `${RUNNER}: it docks natively without that limit; or split the file.`);
          }
          s.record({ ligands: plan.ligands.length, reference: Boolean(plan.reference), box: plan.box, worker: lease.id });
          const box = (v) => v.map((x) => Math.round(x * 1000) / 1000).join(", ");
          return {
            detail: `${plural(plan.ligands.length, "ligand")}${plan.reference ? " and the reference ligand" : ""}; box centre (${box(plan.box.center)}), size (${box(plan.box.size)}) Å${plan.box.source ? ` (${plan.box.source})` : ""}`,
            worker: lease.id,
          };
        });

        // -------------------------------------------------------------- dock
        const planPath = (file) => [...ctx.dir, "read", ...planDir, planFile(file)];
        const params = vinaParams("dock", { ...plan.params, center: plan.box.center, size: plan.box.size });
        const tasks = [
          ...(plan.reference ? [{ key: "reference", name: plan.reference.name ?? "reference", ...plan.reference }] : []),
          ...plan.ligands,
        ].map((t) => ({ key: t.key, name: t.name, file: t.file, sha256: bareSha256(t.sha256), heavy_atoms: t.heavy_atoms ?? null, torsions: t.torsions ?? null }));
        for (const t of tasks) {
          t.digest = `sha256:${await sha256Hex(canonicalJson({ key: t.key, receptor: bareSha256(plan.receptor.sha256), ligand: t.sha256, params, wasm: VINA.wasm.sha256 }))}`;
        }
        const checks = new Map();
        const reusable = async (t) => {
          if (!checks.has(t.key)) checks.set(t.key, await ctx.checkpoint.reusable(`dock.${t.key}`, { inputs_digest: t.digest }));
          return checks.get(t.key);
        };
        // a run kept from an earlier attempt is the job's output all the same, read back at its checkpoint's digest
        const keep = async (t) => {
          const rec = await ctx.checkpoint.read(`dock.${t.key}`);
          const o = rec.outputs[0];
          ctx.artefact({ name: `results/${t.key}.json`, path: o.path, bytes: o.bytes, sha256: o.sha256 });
        };
        const resultFiles = tasks.map((t) => `${t.key}.json`);
        await ctx.stage("dock", async (s) => {
          // the results this attempt lists: those of this plan's runs (reused or computed), no other attempt's
          ctx.forget(["results"]);
          const todo = [];
          for (const t of tasks) {
            if ((await reusable(t)).ok) await keep(t);
            else todo.push(t);
          }
          const reused = tasks.length - todo.length;
          if (!todo.length) return { detail: `${plural(tasks.length, "Vina run")}, every one reused` };
          const sized = vinaWorkers({ ...sh.device(), runs: todo.length });
          s.record({ runs: tasks.length, reused, workers: sized.n, why: sized.reason });
          ctx.log("info", `dock: ${sized.reason}`);
          let bytes;
          try {
            bytes = await fetchWasm(sh.wasm.url, { fetch: sh.fetch, signal: ctx.signal, sha256: sh.wasm.sha256 });
          } catch (err) {
            if (ctx.signal.aborted) throw cancelled();
            throw err instanceof VinaError ? vinaOpError({ type: err.code, message: err.message }) : err;
          }
          for (let i = 1; i <= sized.n; i++) clients.push(sh.client(i));
          // a cancel stops every Vina worker at once (each client terminates its own on the signal)
          const stopAll = () => { for (const c of clients) c.terminate(); };
          ctx.signal.addEventListener("abort", stopAll, { once: true });
          try {
            await Promise.all(clients.map((c) => c.start(bytes, { sha256: sh.wasm.sha256, signal: ctx.signal })));
            for (const c of clients) {
              if (canonicalJson(c.info?.engine ?? null) !== canonicalJson(ENGINE)) {
                throw dockError("integrity", `the docking worker reports the engine ${JSON.stringify(c.info?.engine)}, not ${JSON.stringify(ENGINE)}`);
              }
            }
            ctx.outcome({ engine: engineRecord() });
            const receptor = textOf(await store.readBytes(planPath(plan.receptor.file)) || new Uint8Array(0));
            if (receptor === null || (await sha256Hex(receptor)) !== bareSha256(plan.receptor.sha256)) {
              throw dockError("integrity", `${planFile(plan.receptor.file)}: its text is not the one dock_read recorded (SHA-256 ${sha12(bareSha256(plan.receptor.sha256))}…)`);
            }
            let done = reused;
            const ran = await runAll(clients, todo, ctx.signal, async (t, client) => {
              const ligand = textOf(await store.readBytes(planPath(t.file)) || new Uint8Array(0));
              if (ligand === null || (await sha256Hex(ligand)) !== t.sha256) {
                throw dockError("integrity", `${planFile(t.file)}: its text is not the one dock_read recorded (SHA-256 ${sha12(t.sha256)}…)`);
              }
              const t0 = ms();
              const result = await client.dock({ key: t.key, receptor, ligand, params }, { signal: ctx.signal });
              const took = ms() - t0;
              await writeResult(ctx, t, result, { receptor: bareSha256(plan.receptor.sha256), params });
              s.compute(vinaFacts(t, params.exhaustiveness, client.id, took));
              done += 1;
              ctx.progress(0.12 + 0.68 * (done / tasks.length), `${done} of ${tasks.length} Vina runs (${t.name})`);
              return result;
            });
            const refused = ran.filter((r) => r.error !== null).length;
            const workers = new Set(ran.map((r) => r.worker)).size;
            return {
              detail: `${plural(todo.length, "Vina run")} on ${plural(workers, "worker")}${reused ? `, ${reused} reused` : ""}${refused ? `; ${refused} refused by Vina (their message is their result)` : ""}`,
            };
          } finally {
            ctx.signal.removeEventListener("abort", stopAll);
            stopAll();
          }
        }, {
          reuse: async () => {
            const why = [];
            let all = true;
            for (const t of tasks) {
              const c = await reusable(t);
              if (c.ok) continue;
              all = false;
              // a run that never ran has no checkpoint: nothing to say about it
              for (const x of c.problems) if (x !== "no checkpoint") why.push(`${t.key}: ${x}`);
            }
            if (!all) return { ok: false, problems: why.length ? why : ["no checkpoint"] };
            ctx.forget(["results"]);
            for (const t of tasks) await keep(t);
            ctx.outcome({ engine: engineRecord() });
            return { ok: true, detail: `reused: its ${plural(tasks.length, "Vina result")} re-hash to the digests recorded when they were written` };
          },
        });

        // -------------------------------------------------------------- analysis
        const results = {};
        for (const t of tasks) results[t.key] = (await ctx.checkpoint.read(`dock.${t.key}`))?.outputs?.[0]?.sha256 ?? null;
        await lease.call("free", { paths: [`${root}/results`] }, { signal: ctx.signal });
        await lease.call("mount", { root: `${root}/results`, dir: [...ctx.dir, "results"], files: resultFiles }, { signal: ctx.signal });
        const aDigest = `sha256:${await sha256Hex(canonicalJson({ params: p, inputs: digests, plan: planSha, results, runtime }))}`;
        const args = {
          ...fileArgs, plan_dir: [`${root}/read`, ...planDir].join("/"), results_dir: `${root}/results`, out_dir: `${root}/out`, ...pyParams(p),
        };
        const limits = async (s) => {
          const md = await store.readBytes([...ctx.dir, "out", "limitations.md"]).catch(() => null);
          const lines = md ? limitationLines(new TextDecoder().decode(md)) : [];
          if (!lines.some((l) => /preparation/i.test(l))) lines.push(PREPARED_LINE);
          lines.push(computedLine(versions.numpy));
          if (!lines.includes(LIMIT_PREDICTED)) lines.push(LIMIT_PREDICTED);
          s?.record({ packages: versions });
          ctx.outcome({ limitations: lines, engine: engineRecord() });
        };
        await ctx.stage("analysis", async (s) => {
          const out = await pythonCall(ctx, lease, "run_dock", args, [0.8, 0.12], "the analysis");
          s.record({ exit: out.exit, failed: out.failed || [], worker: lease.id });
          if (out.exit === 2) {
            const e = new Error(`refused: ${out.refused || "the pipeline refused its inputs"}`);
            e.type = "refused";
            if (typeof out.need === "string" && out.need) e.need = out.need;
            throw memoryRefusal(e, "the analysis") || e;
          }
          summary = out.summary ?? null;
          // an incomplete run's outputs are kept as its record, when it wrote any
          let kept = { files: [] };
          try {
            kept = await lease.call("persist", { from: `${root}/out`, dir: [...ctx.dir, "out"], clean: true }, { signal: ctx.signal });
          } catch (err) {
            if (out.exit === 0 || ctx.signal.aborted) throw err;
          }
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
          const v = await pythonCall(ctx, lease, "verify_pipeline", { kind: "pipeline.dock", out_dir: `${root}/out` }, [0.92, 0.08], "the check of the outputs");
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
        for (const c of clients) c.terminate();
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

/**
 * The runs `tasks` on `clients`, each client taking the next run when its last one is done → [{…result, worker}] in the
 * order they ended. fn(task, client) makes one run. The first failure stops every other client at once (Vina runs
 * cannot be interrupted) and is what this throws; the job's cancel is a cancel.
 */
async function runAll(clients, tasks, signal, fn) {
  const stop = new AbortController();
  const relay = () => stop.abort();
  signal.addEventListener("abort", relay, { once: true });
  let next = 0;
  let first = null;
  const out = [];
  const lane = async (client) => {
    while (!stop.signal.aborted && next < tasks.length) {
      const task = tasks[next++];
      const r = await fn(task, client);
      out.push({ key: task.key, error: typeof r?.error === "string" ? r.error : null, worker: client.id });
    }
  };
  try {
    await Promise.allSettled(clients.map((c) => lane(c).catch((err) => {
      if (!first && !signal.aborted) first = err;
      stop.abort();
      for (const x of clients) x.terminate();
    })));
  } finally {
    signal.removeEventListener("abort", relay);
  }
  if (signal.aborted) throw cancelled();
  if (first) throw first;
  return out;
}

/**
 * One run's result held to what this page asked (its key, the receptor's and the ligand's SHA-256, the parameters) and
 * written to results/<key>.json as vina.js vinaResultText gives it: the bytes the worker hashed. The page hashes them
 * as it writes them; the two readings must agree. Then the run's artefact and its checkpoint.
 */
async function writeResult(ctx, t, result, { receptor, params }) {
  const fail = (why) => dockError("integrity", `${t.key}: ${why}`);
  if (!isPlainObject(result) || result.schema !== RESULT_SCHEMA || result.key !== t.key) throw fail(`the docking worker answered no ${RESULT_SCHEMA} result for it`);
  if (result.receptor_sha256 !== receptor || result.ligand_sha256 !== t.sha256) throw fail("the docking worker's result is of another receptor or ligand text than the one it was given");
  if (canonicalJson(result.params) !== canonicalJson(params)) throw fail("the docking worker's result records other parameters than the ones it was given");
  const want = bareSha256(result.result_sha256);
  if (!want) throw fail("the docking worker gave no SHA-256 of its result");
  const path = ["results", `${t.key}.json`];
  const h = new Sha256();
  const bytes = await ctx.store.writeBlob([...ctx.dir, ...path], new Blob([vinaResultText(result)]), { onChunk: (b) => h.update(b) });
  const got = h.digestHex();
  if (got !== want) {
    await ctx.store.remove([...ctx.dir, ...path]).catch(() => false);
    throw fail(`written here its SHA-256 is ${sha12(got)}…, the docking worker hashed ${sha12(want)}…`);
  }
  ctx.artefact({ name: `results/${t.key}.json`, path, bytes, sha256: want });
  // a run that ended as the job was cancelled is kept for a resume all the same; one after the job ended is not
  if (!ctx.signal.aborted) await ctx.checkpoint.write(`dock.${t.key}`, { inputs_digest: t.digest, outputs: [{ path, bytes, sha256: got }] });
}

/** An output as the job records it: the runner's artefact name for a file the kind requires, else its path. */
function artefactOf(rel, f) {
  const file = rel.join("/");
  const named = Object.entries(ARTEFACTS).find(([, path]) => path === file);
  return { name: named ? named[0] : file, path: ["out", ...rel], bytes: f.bytes, sha256: f.sha256 };
}

/** The handler as this module gives it without a site (the code's pin, the page's Worker): what the router and the
 * jobs runtime ask needsRunner of; createJobsRuntime makes its own with boot.json's vina entry. */
export const DOCK_HANDLERS = createDockHandlers();

/** What tests, the harness and the router read. */
export const DOCK = Object.freeze({
  packages: PACKAGES, artefacts: ARTEFACTS, maxLigands: MAX_LIGANDS, maxWorkers: MAX_VINA_WORKERS, gbPerWorker: GB_PER_VINA, limitPredicted: LIMIT_PREDICTED,
  preparedLine: PREPARED_LINE, planSchema: PLAN_SCHEMA, engine: ENGINE, vina: VINA, computedLine, workers: vinaWorkers,
});
