// The browser docking job (js/scientific/pipelines/dock.js) through the job service, over fake-indexeddb, the in-memory
// OPFS of fixtures/fake-opfs.mjs, the page's project files (the prepared 3PTB fixture of
// BioScience-Harness/tests/fixtures/docking/prepared: the receptor, six ligands as MODEL blocks, the crystal BEN), the
// real Vina worker (js/scientific/dock/dock.worker.js, its own serve() and handlers, the pinned wasm bytes checked) with
// a scripted Vina module in place of the engine, and scripted job workers whose Python keeps tcmstudio.browser_jobs'
// docking contract (dock_read, run_dock, verify_pipeline as the spec states them): what is refused before anything is
// queued and why (and that it agrees with the catalog's runner_only rules); the stages; one checkpoint per Vina run and
// a resume that reuses the runs that stand; a cancel that terminates every Vina worker at once; Vina's own refusal as a
// result; the workers side by side; the receipt's engine, device note and memory; and the jobs runtime around them.
// The last test runs the real engine (the vendored AutoDock Vina 1.2.7 wasm) and, when $PYTHON has tcmstudio's docking
// functions, the real Python through `python -m tcmstudio.browser_jobs`.
import "../fixtures/setup.mjs";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, statSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import { IDBFactory } from "fake-indexeddb";
import { fakeOpfs } from "../fixtures/fake-opfs.mjs";
import { hex, projectStore, scienceWorkerClass } from "../fixtures/admet-fakes.mjs";
import { FILE_ID, runnerOnly } from "../../js/core/catalog.js";
import { canonicalJson } from "../../js/core/util.js";
import { ENGINE, VINA } from "../../js/scientific/dock/vina.js";
import { serve, workerEnv } from "../../js/scientific/dock/dock.worker.js";
import { OpfsStore } from "../../js/scientific/data/opfs-store.js";
import {
  DOCK, DOCK_HANDLERS, MAX_LIGANDS, PREPARED_LINE, VinaClient, computedLine, createDockHandlers, dockNeedsRunner, vinaWorkers,
} from "../../js/scientific/pipelines/dock.js";
import { ADMET } from "../../js/scientific/pipelines/admet.js";
import { BrowserJobService, jobDir } from "../../js/scientific/runtime/job-manager.js";
import { BrowserJobsRuntime, jobCall, projectFilesResolver } from "../../js/scientific/runtime/jobs-runtime.js";
import { ScienceWorkerPool } from "../../js/scientific/runtime/worker-pool.js";

const enc = new TextEncoder();
const dec = new TextDecoder();
const REPO = fileURLToPath(new URL("../../../../", import.meta.url));
const FIXTURE = path.join(REPO, "BioScience-Harness/tests/fixtures/docking/prepared");
const RECEPTOR = readFileSync(path.join(FIXTURE, "3ptb_receptor.pdbqt"));
const LIGANDS = readFileSync(path.join(FIXTURE, "ligands.pdbqt"));
const CRYSTAL = readFileSync(path.join(FIXTURE, "ben_crystal.pdbqt"));
const WASM = readFileSync(fileURLToPath(new URL("../../js/scientific/dock/vendor/vina-1.2.7.wasm", import.meta.url)));
const NAMES = ["benzamidine", "p_methylbenzamidine", "baicalein", "ibuprofen", "ferulic_acid", "paeonol"];
const BOOT = { schema: "tcmstudio.boot/1", pyodide: { version: "314.0.7", index_url: "https://cdn.example/pyodide/" }, bundle: { path: "b.tar.gz", sha256: "a".repeat(64) } };
/** boot.json's vina entry as tcmstudio.webbuild writes it (vina_build.json's pins). */
const BOOT_VINA = { version: VINA.version, loader: VINA.loader, url: VINA.wasm.url, sha256: VINA.wasm.sha256, bytes: VINA.wasm.bytes };
/** tcmstudio.catalog's runner_only rules for job.pipeline.dock (SPEC §3), as the router reads them without the job module. */
const DOCK_RUNNER_ONLY = Object.freeze([
  { arg: "receptor", not_suffix: [".pdbqt"], why: "a receptor that is not a prepared PDBQT is prepared with Meeko and gemmi, on the runner" },
  { arg: "ligands", not_suffix: [".pdbqt"], why: "ligands that are not a prepared PDBQT are prepared with RDKit and Meeko, on the runner" },
  { arg: "site_ligand", present: true, why: "a site from a co-crystal ligand's residue name needs the structure and its SMILES (RCSB), on the runner" },
  { arg: "allow_remote", is: true, why: "fetching structures from RCSB or AlphaFold DB runs on the runner" },
]);

// ------------------------------------------------------------------------------------------------- stand-ins

const isAtom = (l) => l.startsWith("ATOM  ") || l.startsWith("HETATM");
const adType = (l) => l.slice(77, 79).trim();
const heavy = (lines) => lines.filter((l) => isAtom(l) && !["H", "HD", "HS"].includes(adType(l)));
const base = (p) => String(p).split("/").at(-1);
const stem = (p) => base(p).replace(/\.pdbqt$/i, "");

/**
 * AutoDock Vina as the binding's run() answers (studio/vina-wasm/vina_wasm.cpp): for each dock, up to three poses of the
 * ligand's own atoms with energies from its name and the seed. `refuse`: names Vina refuses (its parse error); `memory`:
 * names it runs out of memory on (std::bad_alloc); `trap`: names whose first run traps (a C++ abort), once in all.
 */
function fakeVina({ refuse = [], memory = [], trap = [] } = {}) {
  const runs = [];
  const trapped = new Set();
  const factory = async () => ({
    run(mode, receptor, ligand, p) {
      const name = /^REMARK\s+Name\s*=\s*(.*)$/m.exec(ligand)?.[1]?.trim() || "ligand";
      runs.push({ mode, name, seed: p.seed, exhaustiveness: p.exhaustiveness });
      if (refuse.includes(name)) return { ok: false, error: `\n\nPDBQT parsing error: Unknown or inappropriate tag found in flex residue or ligand.\n > ${name}\n\n` };
      if (memory.includes(name)) return { ok: false, error: "\n\nstd::bad_alloc\n\n" };
      if (trap.includes(name) && !trapped.has(name)) {
        trapped.add(name);
        throw new Error("RuntimeError: unreachable executed");
      }
      const atoms = ligand.split("\n").filter(isAtom);
      const h = [...name].reduce((a, c) => (a * 31 + c.charCodeAt(0)) % 997, 7);
      const energies = Array.from({ length: Math.min(p.n_poses, 3) }, (_, k) => {
        const total = -5 - h / 1000 - (p.seed % 7) / 100 + k * 0.25;
        return [total, total - 0.3125, -0.2, 0.1, -0.2];
      });
      const poses = energies.map((e, k) => `MODEL ${k + 1}\nREMARK VINA RESULT: ${e[0].toFixed(3)} 0.000 0.000\n${atoms.join("\n")}\nENDMDL\n`).join("");
      return { ok: true, seed: p.seed, energies, poses };
    },
  });
  factory.runs = runs;
  return factory;
}

/**
 * Vina workers: the real dock.worker.js (its serve() and handlers) in this process, with `factory` for the loader;
 * hold(payload) → a promise a dock request waits for before the worker sees it (null: none); alter(reply) changes what
 * the worker answers. `docks` counts the runs out at once.
 */
function vinaWorkerClass({ factory = fakeVina(), hold = () => null, alter = (m) => m } = {}) {
  const instances = [];
  const docks = { active: 0, max: 0, sent: [] };
  class VinaWorker {
    constructor(url, opts) {
      Object.assign(this, { url, opts, terminated: false, received: [], open: new Set() });
      instances.push(this);
      this.scope = {
        postMessage: (m) => setTimeout(() => {
          if (this.terminated) return;
          if (this.open.delete(m.id)) docks.active -= 1;
          this.onmessage?.({ data: alter(structuredClone(m)) });
        }, 0),
      };
      serve(this.scope, workerEnv({ factory, fetch: async () => { throw new Error("this worker is given its bytes"); } }));
    }
    postMessage(m) {
      if (this.terminated) return;
      const msg = structuredClone(m);
      this.received.push(msg);
      if (msg.op === "dock") {
        this.open.add(msg.id);
        docks.active += 1;
        docks.max = Math.max(docks.max, docks.active);
        docks.sent.push(msg.payload.key);
      }
      Promise.resolve(msg.op === "dock" ? hold(msg.payload) : null).then(() => {
        if (!this.terminated) this.scope.onmessage?.({ data: msg });
      });
    }
    terminate() {
      if (this.terminated) return;
      this.terminated = true;
      docks.active -= this.open.size;
      this.open.clear();
    }
  }
  VinaWorker.instances = instances;
  VinaWorker.docks = docks;
  return VinaWorker;
}

const refused = (message, need) => ({ ok: false, error: { type: "refused", message, ...(need ? { need } : {}) } });
const put = (fs, p, value) => fs.set(p, typeof value === "string" ? enc.encode(value) : value);
const json = (fs, p) => JSON.parse(dec.decode(fs.get(p)));
const lf = (bytes) => dec.decode(bytes).replace(/\r\n?/g, "\n");

/** The ligands of a PDBQT text as bioagent.docking.prepared.read_ligands gives them: MODEL blocks (or the whole). */
function splitLigands(text, name) {
  const lines = text.split("\n");
  const blocks = [];
  let cur = null;
  for (const l of lines) {
    if (/^MODEL(\s|$)/.test(l)) { cur = []; continue; }
    if (/^ENDMDL/.test(l)) { blocks.push(cur); cur = null; continue; }
    if (cur) cur.push(l);
  }
  const raw = blocks.length ? blocks : [lines.filter((l) => l !== "")];
  return raw.map((b, k) => {
    const body = `${b.filter((l) => l !== "").join("\n")}\n`;
    const named = /^REMARK\s+Name\s*=\s*(.*)$/m.exec(body)?.[1]?.trim();
    return { name: named || (raw.length > 1 ? `${name}_${k + 1}` : name), text: body, heavy: heavy(b).length, torsions: Number(/^TORSDOF\s+(\d+)/m.exec(body)?.[1] ?? 0) };
  });
}

const DOCK_READ = ["receptor", "ligands", "reference_ligand", "center", "size", "scoring", "exhaustiveness", "poses", "seed", "out_dir"];
const RUN_DOCK = [...DOCK_READ, "plan_dir", "results_dir"];

/**
 * tcmstudio.browser_jobs' docking functions as the spec states them, each call recorded in .calls: dock_read writes the
 * plan (out_dir/plan: receptor.pdbqt, ligand-<i>.pdbqt, reference.pdbqt, dock_plan.json); run_dock reads every Vina
 * result as PrecomputedVina does (refusing one of another receptor, ligand or parameters: the run incomplete) and writes
 * the outputs; verify_pipeline. Options: failAnalysisOnce (the first run_dock ends incomplete), limitations (the lines of
 * limitations.md), maxLigands.
 */
function dockPython({ failAnalysisOnce = false, verify = "PASS", maxLigands = MAX_LIGANDS, limitations = null } = {}) {
  const calls = [];
  let failedOnce = false;
  const only = (args, allowed) => {
    const extra = Object.keys(args).filter((k) => !allowed.includes(k));
    return extra.length ? { ok: false, error: { type: "bad_arguments", message: `unknown argument(s) ${extra.join(", ")}` } } : null;
  };
  const fn = async (name, args, fs) => {
    calls.push({ name, args: JSON.parse(JSON.stringify(args)) });
    if (name === "dock_read") {
      const wrong = only(args, DOCK_READ);
      if (wrong) return wrong;
      const rec = lf(fs.get(args.receptor));
      if (/^MODEL/m.test(rec)) return refused(`${base(args.receptor)}: a receptor of several models (MODEL) is not supported: give one rigid model`);
      const ligs = splitLigands(lf(fs.get(args.ligands)), stem(args.ligands));
      if (ligs.length > maxLigands) return refused(`${ligs.length} ligands exceed the ${maxLigands} a browser worker can hold`, "memory");
      const ref = args.reference_ligand ? splitLigands(lf(fs.get(args.reference_ligand)), stem(args.reference_ligand))[0] : null;
      let box;
      if (args.center) box = { center: args.center, size: args.size, source: "given" };
      else {
        const xyz = heavy(ref.text.split("\n")).map((l) => [30, 38, 46].map((c) => Number(l.slice(c, c + 8))));
        box = { center: [0, 1, 2].map((k) => xyz.reduce((a, v) => a + v[k], 0) / xyz.length), size: args.size ?? [18, 18, 18], source: "reference ligand" };
      }
      const dir = `${args.out_dir}/plan`;
      put(fs, `${dir}/receptor.pdbqt`, rec);
      ligs.forEach((l, i) => put(fs, `${dir}/ligand-${i}.pdbqt`, l.text));
      if (ref) put(fs, `${dir}/reference.pdbqt`, ref.text);
      const lines = rec.split("\n").filter(isAtom);
      const plan = {
        schema: "tcmstudio.dock-plan/1",
        receptor: { file: "receptor.pdbqt", sha256: hex(enc.encode(rec)), atoms: lines.length, heavy_atoms: heavy(lines).length, chains: ["A"], residues: 1 },
        ligands: ligs.map((l, i) => ({ index: i, key: `ligand-${i}`, name: l.name, file: `ligand-${i}.pdbqt`, sha256: hex(enc.encode(l.text)), heavy_atoms: l.heavy, torsions: l.torsions })),
        ...(ref ? { reference: { key: "reference", name: ref.name, file: "reference.pdbqt", sha256: hex(enc.encode(ref.text)), heavy_atoms: ref.heavy, torsions: ref.torsions } } : {}),
        box,
        params: { scoring: args.scoring ?? "vina", exhaustiveness: args.exhaustiveness ?? 8, n_poses: args.poses ?? 9, seed: args.seed ?? 42, energy_range: 3, spacing: 0.375, min_rmsd: 1, max_evals: 0 },
      };
      put(fs, `${dir}/dock_plan.json`, JSON.stringify(plan, null, 1));
      return plan;
    }
    if (name === "run_dock") {
      const wrong = only(args, RUN_DOCK);
      if (wrong) return wrong;
      if (failAnalysisOnce && !failedOnce) {
        failedOnce = true;
        return { exit: 1, summary: null, failed: ["report"], reasons: { report: "a stand-in failure" }, refused: null, need: null };
      }
      const plan = json(fs, `${args.plan_dir}/dock_plan.json`);
      const want = { ...plan.params, center: plan.box.center, size: plan.box.size };
      const tasks = [...(plan.reference ? [plan.reference] : []), ...plan.ligands];
      const results = {};
      for (const t of tasks) {
        const p = `${args.results_dir}/${t.key}.json`;
        const doc = fs.has(p) ? json(fs, p) : null;
        const why = !doc ? "no Vina result" : doc.schema !== "tcmstudio.vina-result/1" ? "not a Vina result" : doc.key !== t.key ? "another run's result"
          : doc.receptor_sha256 !== plan.receptor.sha256 ? "computed on another receptor" : doc.ligand_sha256 !== t.sha256 ? "computed on another ligand"
            : canonicalJson(doc.params) !== canonicalJson(want) ? "computed with other parameters"
              : doc.engine?.build !== "wasm" || !/^[0-9a-f]{64}$/.test(doc.engine?.wasm_sha256 || "") ? "no WebAssembly engine named" : null;
        if (why) return { exit: 1, summary: null, failed: ["dock"], reasons: { dock: `${t.key}.json: ${why}` }, refused: null, need: null };
        results[t.key] = doc;
      }
      const out = args.out_dir;
      const docked = plan.ligands.filter((l) => !results[l.key].error);
      const failures = Object.fromEntries(plan.ligands.filter((l) => results[l.key].error).map((l) => [l.name, results[l.key].error]));
      const rows = docked.map((l) => [l.name, results[l.key].energies[0][0].toFixed(3), l.heavy_atoms, l.torsions]).sort((a, b) => Number(a[1]) - Number(b[1]));
      put(fs, `${out}/scores.tsv`, `ligand\tbest_score\theavy_atoms\ttorsions\n${rows.map((r) => r.join("\t")).join("\n")}\n`);
      for (const l of docked) put(fs, `${out}/poses/${l.name}.pdbqt`, results[l.key].poses);
      put(fs, `${out}/report.html`, "<!doctype html><title>docking</title>");
      put(fs, `${out}/limitations.md`, `${(limitations ?? [
        "A docking score is the scoring function's estimate in kcal/mol, not a measured binding affinity.",
        "The inputs' preparation (protonation states, partial charges, AutoDock atom types, the ligands' torsion trees) is the user's: it was used as given and not checked.",
      ]).join("\n")}\n`);
      const engine = Object.values(results)[0].engine;
      put(fs, `${out}/run.json`, JSON.stringify({ pipeline: "bioagent.docking.prepared", mode: "prepared", complete: true, engine, failures, validation: plan.reference ? { passed: !results.reference.error } : null, inputs: { [args.receptor]: hex(fs.get(args.receptor)) } }));
      return { exit: 0, summary: { ligands: plan.ligands.length, docked: docked.length, failures: Object.keys(failures), validated: plan.reference ? !results.reference.error : null, complete: true }, failed: [], reasons: {}, refused: null, need: null };
    }
    if (name === "verify_pipeline") {
      const status = args.kind === "pipeline.dock" && fs.has(`${args.out_dir}/run.json`) ? verify : "FAIL";
      return { status, checks: [{ name: "verify_run", status }, { name: "required_artefacts", status }, { name: "validator:run", status }], problems: status === "PASS" ? [] : ["the run did not verify"], report: {} };
    }
    return { ok: false, error: { type: "bad_arguments", message: `no function ${name}` } };
  };
  fn.calls = calls;
  return fn;
}

/** The wasm's download as the page asks for it: the vendored bytes (or `bytes`), every URL recorded. */
function wasmFetch(bytes = WASM) {
  const fetched = [];
  const f = async (url) => {
    fetched.push(String(url));
    const b = bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength);
    return { ok: true, status: 200, arrayBuffer: async () => b };
  };
  f.fetched = fetched;
  return f;
}

/** A job service over the stand-ins, its project holding the fixture; handlers: createDockHandlers options beyond them. */
async function setup({ python = dockPython(), files = null, handlers = {}, fake = fakeOpfs(), idb = new IDBFactory(), cores = 8, memory = 8, vina = {} } = {}) {
  const store = new OpfsStore({ root: fake.root, storage: fake.storage });
  const Worker = scienceWorkerClass(store, python);
  const VinaWorker = vinaWorkerClass(vina);
  const pool = new ScienceWorkerPool({ size: 1, Worker, workerUrl: "w.js", boot: BOOT, interruptGraceMs: 20 });
  const project = files ?? withFixture(projectStore());
  const fetch = wasmFetch();
  const nav = { userAgent: "test", hardwareConcurrency: cores, deviceMemory: memory };
  const dock = createDockHandlers({ vina: BOOT_VINA, VinaWorker, vinaWorkerUrl: "dock.worker.js", fetch, navigator: nav, ...handlers });
  const svc = new BrowserJobService({
    indexedDB: idb, locks: null, pool, store, projectFiles: projectFilesResolver(project), handlers: dock, navigator: nav,
  });
  assert.equal(await svc.start(), "ready");
  return { svc, store, files: project, python, Worker, VinaWorker, fetch, fake, idb, dock };
}

function withFixture(files) {
  files.add("p1", "3ptb_receptor.pdbqt", RECEPTOR);
  files.add("p1", "ligands.pdbqt", LIGANDS);
  files.add("p1", "ben_crystal.pdbqt", CRYSTAL);
  return files;
}

const RUN = { receptor: "3ptb_receptor.pdbqt", ligands: "ligands.pdbqt", reference_ligand: "ben_crystal.pdbqt" };

async function settle(svc, id, ms = 60000) {
  const t0 = Date.now();
  for (;;) {
    const job = await svc.get(id);
    if (["succeeded", "failed", "cancelled", "interrupted"].includes(job?.state)) return job;
    if (Date.now() - t0 > ms) throw new Error(`job ${id} did not end: ${job?.state}`);
    await new Promise((r) => setTimeout(r, 5));
  }
}

async function until(fn, ms = 20000) {
  const t0 = Date.now();
  while (!(await fn())) {
    if (Date.now() - t0 > ms) throw new Error("timed out waiting");
    await new Promise((r) => setTimeout(r, 5));
  }
}

async function refusedBy(handler, params, svc, projectId = "p1") {
  try {
    await handler.preflight(params, svc, { project_id: projectId });
  } catch (err) {
    return { type: err.type, need: err.need, message: err.message, hint: err.hint };
  }
  return null;
}

async function docked(opts = {}, params = RUN) {
  const s = await setup(opts);
  const job = await s.svc.submit("pipeline.dock", params, { project_id: "p1" });
  return { ...s, job: await settle(s.svc, job.id) };
}

// ------------------------------------------------------------------------------------------------- refusals

test("needsRunner: a receptor or ligands that are not PDBQT, site_ligand and allow_remote are the runner's; the catalog's runner_only rules say the same, a file id judged later", () => {
  const entry = { execution: { browser_job: { runner_only: DOCK_RUNNER_ONLY } } };
  const cases = [
    {}, RUN, { ...RUN, receptor: "3PTB.PDB" }, { ...RUN, receptor: "PDB:3PTB" }, { ...RUN, receptor: "UniProt:P00760" }, { ...RUN, receptor: " r.PDBQT " },
    { ...RUN, ligands: "CCO" }, { ...RUN, ligands: "ligands.sdf" }, { ...RUN, ligands: "ligands.csv" }, { ...RUN, ligands: "" }, { ...RUN, ligands: null },
    { ...RUN, ligands: "u_0123456789abcdef" }, { ...RUN, receptor: "0f8fad5b-d9cb-469f-a165-70867728950e" }, { ...RUN, receptor: 5 },
    { ...RUN, site_ligand: "BEN" }, { ...RUN, site_ligand: "" }, { ...RUN, site_ligand: null }, { ...RUN, allow_remote: true }, { ...RUN, allow_remote: false },
  ];
  for (const params of cases) {
    assert.equal(Boolean(dockNeedsRunner(params)), runnerOnly(entry, params).length > 0, JSON.stringify(params));
  }
  assert.deepEqual(runnerOnly(entry, undefined), [], "without a call's arguments nothing is judged");
  assert.match(dockNeedsRunner({ ...RUN, receptor: "PDB:3PTB" }).message, /^receptor: "PDB:3PTB" is not a prepared PDBQT receptor/);
  assert.match(dockNeedsRunner({ ...RUN, ligands: "CCO" }).message, /RDKit and Meeko, which run only on the runner/);
  assert.match(dockNeedsRunner({ ...RUN, site_ligand: "BEN" }).hint, /reference_ligand/);
  assert.match(dockNeedsRunner({ ...RUN, receptor: "x.pdb" }).hint, /^Connect the local runner/);
  assert.equal(dockNeedsRunner({ ...RUN, ligands: "u_0123456789abcdef" }), null, "a file id: judged once the file is found");
  assert.ok(FILE_ID.test("0F8FAD5B-D9CB-469F-A165-70867728950E") && FILE_ID.test("u_0123456789abcdef") && !FILE_ID.test("ligands.pdbqt"));
  assert.equal(DOCK_HANDLERS[0].needsRunner, dockNeedsRunner);
  // the jobs runtime answers it from the handler, even where its service cannot start
  const rt = new BrowserJobsRuntime({ service: { status: "unsupported", kinds: () => [] } });
  assert.match(rt.needsRunner("pipeline.dock", { ...RUN, ligands: "CCO" }).message, /^ligands:/);
  assert.equal(rt.needsRunner("pipeline.dock", RUN), null);
});

test("pipeline.dock preflight: bad arguments, the runner's arguments, the files, too many ligands, the engine pin", async () => {
  const { svc, files, dock } = await setup();
  const [h] = dock;
  const r = (params, s = svc) => refusedBy(h, params, s);
  assert.equal(await r(RUN), null);
  assert.equal(await r({ ...RUN, reference_ligand: undefined, center: [-1.8, 14.4, 16.7], size: [18, 18, 18] }), null);
  for (const [params, why] of [
    [{ ...RUN, extra: 1 }, /unknown parameter extra/],
    [{ ...RUN, seed: 0 }, /seed: between 1 and 2147483647, not 0 \(0 would let Vina pick a random seed/],
    [{ ...RUN, seed: 1.5 }, /seed: a whole number/],
    [{ ...RUN, exhaustiveness: 65 }, /exhaustiveness: between 1 and 64/],
    [{ ...RUN, poses: 0 }, /poses: between 1 and 20/],
    [{ ...RUN, scoring: "ad4" }, /scoring: vina or vinardo/],
    [{ ...RUN, center: [1, 2, 3] }, /center: give size with it/],
    [{ ...RUN, size: [18, 0, 18] }, /size: three finite numbers .*each > 0/],
    [{ receptor: RUN.receptor, ligands: RUN.ligands }, /give the docking site: center and size/],
    [{ receptor: RUN.receptor, ligands: RUN.ligands, size: [20, 20, 20] }, /give the docking site.*with the size given/],
    [{ ...RUN, chains: "A" }, /chains: selects the chains of a PDB receptor/],
    [{ ...RUN, receptor: "gone.pdbqt" }, /receptor: no file named "gone\.pdbqt" in this project/],
    [{ ligands: RUN.ligands, center: [0, 0, 0], size: [18, 18, 18] }, /receptor: a PDBQT file of this project/],
  ]) {
    const got = await r(params);
    assert.equal(got?.type, "bad_arguments", JSON.stringify(params));
    assert.match(got.message, why, JSON.stringify(params));
  }
  for (const params of [{ ...RUN, receptor: "PDB:3PTB" }, { ...RUN, ligands: "NC(=N)c1ccccc1" }, { ...RUN, site_ligand: "BEN" }, { ...RUN, allow_remote: true }]) {
    assert.deepEqual([(await r(params)).type, (await r(params)).need], ["unavailable", "runner"], JSON.stringify(params));
  }
  // a file named by its id is judged once it is found: a PDB file needs the runner, a reference that is no PDBQT is wrong
  const pdb = files.add("p1", "3ptb.pdb", "ATOM\n");
  pdb.id = "0f8fad5b-d9cb-469f-a165-70867728950e"; // the page's file ids are uuids (core/store.js)
  assert.equal(dockNeedsRunner({ ...RUN, receptor: pdb.id }), null, "not judged by its id");
  const byId = await r({ ...RUN, receptor: pdb.id });
  assert.deepEqual([byId.type, byId.need], ["unavailable", "runner"]);
  assert.match(byId.message, /receptor: "3ptb\.pdb" is not a prepared PDBQT receptor/);
  files.add("p1", "ben.sdf", "x");
  assert.match((await r({ ...RUN, reference_ligand: "ben.sdf" })).message, /reference_ligand: ben\.sdf is not a PDBQT file/);
  // more ligands than a run here takes, counted as Python counts them (MODEL records)
  const block = (i) => `MODEL ${i + 1}\nREMARK  Name = l${i}\nROOT\nATOM      1  C   UNL     1       0.000   0.000   0.000  1.00  0.00    +0.000 C \nENDROOT\nTORSDOF 0\nENDMDL\n`;
  files.add("p1", "many.pdbqt", Array.from({ length: MAX_LIGANDS + 1 }, (_, i) => block(i)).join(""));
  files.add("p1", "fits.pdbqt", Array.from({ length: MAX_LIGANDS }, (_, i) => block(i)).join(""));
  const many = await r({ ...RUN, ligands: "many.pdbqt" });
  assert.deepEqual([many.type, many.need], ["unavailable", "memory"]);
  assert.match(many.message, new RegExp(`holds up to ${(MAX_LIGANDS + 1).toLocaleString("en-US")} ligands, more than the ${MAX_LIGANDS.toLocaleString("en-US")}`));
  assert.equal(await r({ ...RUN, ligands: "fits.pdbqt" }), null);
  // the site's boot.json: no vina entry, another build, no Web Worker
  const [none] = createDockHandlers({ vina: null, VinaWorker: class {} });
  assert.match((await refusedBy(none, RUN, svc)).message, /boot\.json names no AutoDock Vina build/);
  const [skew] = createDockHandlers({ vina: { ...BOOT_VINA, sha256: "0".repeat(64) }, VinaWorker: class {} });
  const s = await refusedBy(skew, RUN, svc);
  assert.deepEqual([s.type, s.need], ["unavailable", "job_service"]);
  assert.match(s.message, /boot\.json pins AutoDock Vina 1\.2\.7 \(wasm sha256 000000000000…\), but this page's docking is written for AutoDock Vina 1\.2\.7/);
  assert.match(s.hint, /Reload the page/);
  const [noWorker] = createDockHandlers({ vina: BOOT_VINA, VinaWorker: null });
  assert.match((await refusedBy(noWorker, RUN, svc)).message, /cannot start the docking workers/);
});

// ------------------------------------------------------------------------------------------------- the run

test("pipeline.dock: inputs, read, the reference and every ligand docked by Vina workers side by side, the analysis, verified", async () => {
  const { svc, store, python, VinaWorker, fetch, job } = await docked();
  assert.equal(job.state, "succeeded", JSON.stringify(job.error));
  assert.deepEqual(job.stages.map((s) => [s.id, s.status, s.reused]), ["inputs", "read", "dock", "analysis", "verify"].map((id) => [id, "succeeded", false]));
  // the inputs, copied and held to the project's digests
  assert.deepEqual(Object.keys(job.inputs), ["receptor", "ligands", "reference_ligand"]);
  assert.equal(job.inputs.ligands.sha256, hex(LIGANDS));
  // Python read the copies (by their own names: a lone ligand is named after its file), with the parameters given
  const read = python.calls.find((c) => c.name === "dock_read");
  assert.deepEqual(Object.keys(read.args).sort(), ["ligands", "out_dir", "receptor", "reference_ligand"]);
  assert.match(read.args.ligands, /\/inputs\/ligands\.pdbqt$/);
  // seven runs on four workers (8 cores leave 7; 8 GB allow 4; at most 4), never more than four out at once
  const dock = job.stages.find((s) => s.id === "dock");
  assert.deepEqual([dock.record.runs, dock.record.reused, dock.record.workers], [7, 0, 4]);
  assert.match(dock.record.why, /^4 Vina workers: 8 cores leave 7 beside the page; 8 GB allows 4 at 2 GB each; at most 4; 7 runs to do$/);
  assert.equal(VinaWorker.instances.length, 4);
  assert.equal(VinaWorker.docks.max, 4);
  // every run once (taken in the plan's order; four workers send them as each is ready)
  assert.deepEqual([...VinaWorker.docks.sent].sort(), ["reference", ...NAMES.map((_, i) => `ligand-${i}`)].sort());
  assert.ok(VinaWorker.instances.every((w) => w.terminated), "every Vina worker stopped when the job ended");
  // the wasm fetched once, from boot.json's URL, and each worker started from its own copy of the checked bytes
  assert.equal(fetch.fetched.length, 1);
  assert.match(fetch.fetched[0], /js\/scientific\/dock\/vendor\/vina-1\.2\.7\.wasm$/);
  for (const w of VinaWorker.instances) {
    const init = w.received.find((m) => m.op === "init");
    assert.equal(init.payload.sha256, VINA.wasm.sha256);
    assert.equal(hex(init.payload.wasmBytes), VINA.wasm.sha256);
    assert.equal(w.opts.type, "module");
  }
  // one result per run, the bytes the worker hashed (the page's reading agrees), and its checkpoint
  const plan = await store.readJson([...jobDir(job.id), "read", "plan", "dock_plan.json"]);
  for (const key of ["reference", ...NAMES.map((_, i) => `ligand-${i}`)]) {
    const bytes = await store.readBytes([...jobDir(job.id), "results", `${key}.json`]);
    const doc = JSON.parse(dec.decode(bytes));
    assert.deepEqual(Object.keys(doc), ["schema", "key", "receptor_sha256", "ligand_sha256", "params", "engine", "energies", "poses", "seconds"]);
    assert.equal(doc.receptor_sha256, plan.receptor.sha256);
    assert.deepEqual(doc.engine, { ...ENGINE });
    assert.deepEqual(doc.params, { scoring: "vina", exhaustiveness: 8, n_poses: 9, seed: 42, energy_range: 3, center: plan.box.center, size: plan.box.size, spacing: 0.375, min_rmsd: 1, max_evals: 0 });
    const art = job.artefacts.find((a) => a.name === `results/${key}.json`);
    assert.equal(art.sha256, hex(bytes), key);
    const cp = await store.readJson([...jobDir(job.id), "checkpoints", `dock.${key}.json`]);
    assert.deepEqual([cp.stage, cp.outputs.length, cp.outputs[0].sha256], [`dock.${key}`, 1, hex(bytes)]);
    assert.match(cp.inputs_digest, /^sha256:[0-9a-f]{64}$/);
  }
  // one compute fact per run: Vina on the CPU in a worker, its size, which worker
  assert.equal(dock.compute.length, 7);
  assert.ok(dock.compute.every((f) => f.op === "vina.dock" && f.backend === "vina-wasm" && f.placement === "browser" && f.where === "worker" && f.gpu === null && f.accelerated === null && f.basis === "CPU, one thread per worker"));
  assert.deepEqual([...new Set(dock.compute.map((f) => f.worker))].sort(), ["vina-1", "vina-2", "vina-3", "vina-4"]);
  assert.deepEqual(dock.compute.find((f) => f.size.heavy_atoms === 9).size, { heavy_atoms: 9, torsions: 1, exhaustiveness: 8 });
  // the analysis read the plan, the results and the copies
  const run = python.calls.find((c) => c.name === "run_dock");
  assert.deepEqual(Object.keys(run.args).sort(), ["ligands", "out_dir", "plan_dir", "receptor", "reference_ligand", "results_dir"]);
  assert.match(run.args.plan_dir, /\/read\/plan$/);
  assert.equal(python.calls.find((c) => c.name === "verify_pipeline").args.kind, "pipeline.dock");
  assert.deepEqual(job.artefacts.filter((a) => ["run", "scores", "report", "limitations"].includes(a.name)).map((a) => a.name).sort(), ["limitations", "report", "run", "scores"]);
  assert.ok(job.artefacts.some((a) => a.name === "poses/benzamidine.pdbqt"));
  assert.deepEqual(job.result, { ligands: 6, docked: 6, failures: [], validated: true, complete: true });
  // the limitations: the run's own (its preparation line, not said twice), where it was computed, predicted ≠ measured
  assert.equal(job.limitations.at(-1), DOCK.limitPredicted);
  assert.equal(DOCK.limitPredicted, ADMET.limitPredicted);
  assert.equal(job.limitations.at(-2), computedLine("2.4.6"));
  assert.ok(job.limitations.at(-2).length <= 320);
  assert.equal(job.limitations.filter((l) => /preparation/.test(l)).length, 1);
  // the record and the receipt name the engine that ran, and where
  assert.deepEqual(job.engine, [{ id: "AutoDock Vina", name: "AutoDock Vina", version: "1.2.7", build: "wasm", wasm_sha256: VINA.wasm.sha256, stages: ["dock"] }]);
  assert.match(job.device_note, /\(Pyodide\), on the CPU; AutoDock Vina 1\.2\.7 \(WebAssembly\) in 4 dedicated Web Workers, one thread each$/);
  const receipt = (await svc.receipt(job.id)).doc;
  assert.equal(receipt.assurance, "outputs_verified");
  assert.equal(receipt.device.evidence, "verified");
  assert.equal(receipt.device.used, "cpu");
  assert.deepEqual(receipt.engine, [{ id: "AutoDock Vina", version: "1.2.7", stages: ["dock"], name: "AutoDock Vina", build: "wasm", wasm_sha256: VINA.wasm.sha256 }]);
  assert.match(receipt.device.note, /^computed on this device's CPU: CPython compiled to WebAssembly \(Pyodide\) in 1 dedicated Web Worker, one thread each; AutoDock Vina 1\.2\.7 \(WebAssembly\) in 4 dedicated Web Workers, one thread each; nothing ran on a GPU$/);
  assert.equal(receipt.compute.stages.dock.backend, "vina-wasm");
  assert.equal(receipt.compute.stages.dock.calls, 7);
  assert.equal(receipt.device.stages.dock, "cpu");
  assert.deepEqual(receipt.resources.memory.dedicated, [{ engine: "AutoDock Vina 1.2.7 (WebAssembly)", workers: 4, wasm_peak_bytes: null, measured_by: null, reason: receipt.resources.memory.dedicated[0].reason }]);
  assert.match(receipt.resources.memory.dedicated[0].reason, /^not measured/);
  assert.equal(receipt.resources.memory.wasm_peak_bytes, 300 << 20, "the job worker's memory is still read");
  assert.ok(receipt.checks.includes("page_digests"));
});

test("the parameters reach Python and Vina as given; a site given as centre and size; a limitations.md without the preparation line gets the page's", async () => {
  const center = [-1.8555000000000001, 14.366, 16.747999999999998];
  const params = { receptor: RUN.receptor, ligands: RUN.ligands, center, size: [20, 20.5, 18], scoring: "vinardo", exhaustiveness: 2, poses: 3, seed: 7 };
  const { job, python, store, VinaWorker } = await docked({ python: dockPython({ limitations: ["A docking score is an estimate."] }) }, params);
  assert.equal(job.state, "succeeded", JSON.stringify(job.error));
  const read = python.calls.find((c) => c.name === "dock_read").args;
  assert.deepEqual([read.center, read.size, read.scoring, read.exhaustiveness, read.poses, read.seed], [center, [20, 20.5, 18], "vinardo", 2, 3, 7]);
  assert.equal(read.reference_ligand, undefined);
  assert.deepEqual([...VinaWorker.docks.sent].sort(), NAMES.map((_, i) => `ligand-${i}`), "no reference: no redocking");
  const doc = await store.readJson([...jobDir(job.id), "results", "ligand-0.json"]);
  assert.deepEqual(doc.params, { scoring: "vinardo", exhaustiveness: 2, n_poses: 3, seed: 7, energy_range: 3, center, size: [20, 20.5, 18], spacing: 0.375, min_rmsd: 1, max_evals: 0 });
  assert.equal(doc.params.center[0], -1.8555000000000001, "floats as they are, to the last bit");
  assert.ok(job.limitations.includes(PREPARED_LINE));
  const run = python.calls.find((c) => c.name === "run_dock").args;
  assert.deepEqual([run.scoring, run.exhaustiveness, run.poses, run.seed], ["vinardo", 2, 3, 7]);
});

test("the Vina workers side by side: clamp(min(cores − 1, ⌊memory / 2 GB⌋, 4), 1, runs), the reason recorded", async () => {
  assert.equal(vinaWorkers({ cores: 8, memoryGb: 8, runs: 7 }).n, 4);
  assert.equal(vinaWorkers({ cores: 3, memoryGb: 8, runs: 7 }).n, 2);
  assert.equal(vinaWorkers({ cores: 1, memoryGb: 8, runs: 7 }).n, 1);
  assert.equal(vinaWorkers({ cores: 16, memoryGb: 8, runs: 2 }).n, 2);
  assert.equal(vinaWorkers({ cores: 16, memoryGb: 4, runs: 7 }).n, 2, "each worker planned with 2 GB (the most one took within dock_read's bounds)");
  assert.equal(vinaWorkers({ cores: 16, memoryGb: 0.5, runs: 7 }).n, 1);
  const unknown = vinaWorkers({ cores: undefined, memoryGb: undefined, runs: 7 });
  assert.equal(unknown.n, 1);
  assert.match(unknown.reason, /the CPU core count is not reported, 2 assumed; the device memory is not reported, 2 GB assumed$/);
  const { job, VinaWorker } = await docked({ cores: 3 });
  assert.match(job.stages.find((s) => s.id === "dock").record.why, /^2 Vina workers: 3 cores leave 2/);
  assert.equal(job.state, "succeeded", JSON.stringify(job.error));
  assert.equal(VinaWorker.instances.length, 2);
  assert.equal(VinaWorker.docks.max, 2);
  assert.match(job.device_note, /in 2 dedicated Web Workers/);
  const one = await docked({ cores: null, memory: null });
  assert.equal(one.VinaWorker.instances.length, 1);
  assert.match(one.job.stages.find((s) => s.id === "dock").record.why, /2 assumed/);
});

test("Vina refusing a ligand is that ligand's result (its message), not a failure of the job", async () => {
  const { job, store, python } = await docked({ vina: { factory: fakeVina({ refuse: ["ibuprofen"] }) } });
  assert.equal(job.state, "succeeded", JSON.stringify(job.error));
  const doc = await store.readJson([...jobDir(job.id), "results", "ligand-3.json"]);
  assert.deepEqual(Object.keys(doc), ["schema", "key", "receptor_sha256", "ligand_sha256", "params", "engine", "error", "seconds"]);
  assert.match(doc.error, /^PDBQT parsing error: Unknown or inappropriate tag/);
  assert.match(job.stages.find((s) => s.id === "dock").detail, /7 Vina runs on 4 workers; 1 refused by Vina/);
  assert.deepEqual(job.result.failures, ["ibuprofen"]);
  assert.equal(python.calls.filter((c) => c.name === "run_dock").length, 1);
});

test("refused or failed while it runs: Python refusing the receptor, Vina out of memory, Vina trapping, a wasm that is not the pin, two readings that disagree", async () => {
  // Python's refusal of an input (a receptor of several models): refused, nothing docked
  const files = projectStore();
  files.add("p1", "3ptb_receptor.pdbqt", `MODEL 1\n${dec.decode(RECEPTOR)}ENDMDL\n`);
  files.add("p1", "ligands.pdbqt", LIGANDS);
  files.add("p1", "ben_crystal.pdbqt", CRYSTAL);
  const multi = await docked({ files });
  assert.deepEqual([multi.job.state, multi.job.error.type], ["failed", "refused"]);
  assert.match(multi.job.error.message, /a receptor of several models \(MODEL\) is not supported/);
  assert.equal(multi.VinaWorker.instances.length, 0);
  // Vina out of memory: refused for the runner (need memory)
  const oom = await docked({ vina: { factory: fakeVina({ memory: ["baicalein"] }) } });
  assert.deepEqual([oom.job.state, oom.job.error.type, oom.job.error.need], ["failed", "refused", "memory"]);
  assert.match(oom.job.error.message, /ran out of WebAssembly memory/);
  assert.ok(oom.VinaWorker.instances.every((w) => w.terminated), "every worker stopped at the first failure");
  // Vina trapping: the run fails, its module not trusted
  const trap = await docked({ vina: { factory: fakeVina({ trap: ["paeonol"] }) } });
  assert.deepEqual([trap.job.state, trap.job.error.type], ["failed", "runtime_error"]);
  assert.match(trap.job.error.message, /AutoDock Vina itself failed; its memory is no longer trusted/);
  // a wasm whose bytes are not the pin's: refused before any worker starts
  const tampered = new Uint8Array(WASM);
  tampered[1000] ^= 1;
  const s = await setup({ handlers: { fetch: wasmFetch(tampered) } });
  const bad = await settle(s.svc, (await s.svc.submit("pipeline.dock", RUN, { project_id: "p1" })).id);
  assert.deepEqual([bad.state, bad.error.type, bad.error.need], ["failed", "unavailable", "network"]);
  assert.match(bad.error.message, /vina-1\.2\.7\.wasm has sha256 [0-9a-f]{64}, not the pinned build's/);
  assert.equal(s.VinaWorker.instances.length, 0);
  // the page's reading of a result disagreeing with the worker's: not used
  const alter = (m) => (m.ok && m.result?.key === "ligand-1" ? { ...m, result: { ...m.result, result_sha256: "0".repeat(64) } } : m);
  const two = await docked({ vina: { alter } });
  assert.deepEqual([two.job.state, two.job.error.type], ["failed", "integrity"]);
  assert.match(two.job.error.message, /ligand-1: written here its SHA-256 is [0-9a-f]{12}…, the docking worker hashed 000000000000…/);
  assert.equal(await two.store.exists([...jobDir(two.job.id), "results", "ligand-1.json"]), false, "the file is not kept");
  // a result of another ligand text than the one sent
  const other = await docked({ vina: { alter: (m) => (m.ok && m.result?.key === "ligand-2" ? { ...m, result: { ...m.result, ligand_sha256: "1".repeat(64) } } : m) } });
  assert.match(other.job.error.message, /ligand-2: the docking worker's result is of another receptor or ligand text/);
});

// ------------------------------------------------------------------------------------------------- checkpoints

test("resumed after the analysis failed: every Vina run reused from its checkpoint, nothing docked again", async () => {
  const s = await docked({ python: dockPython({ failAnalysisOnce: true }) });
  assert.deepEqual([s.job.state, s.job.error.type], ["failed", "failed"]);
  assert.match(s.job.error.message, /the pipeline did not complete: report \(a stand-in failure\)/);
  const runs = s.VinaWorker.docks.sent.length;
  await s.svc.resume(s.job.id);
  const again = await settle(s.svc, s.job.id);
  assert.equal(again.state, "succeeded", JSON.stringify(again.error));
  assert.equal(s.VinaWorker.docks.sent.length, runs, "no Vina run again");
  const dock = again.stages.find((x) => x.id === "dock");
  assert.equal(dock.reused, true);
  assert.match(dock.detail, /^reused: its 7 Vina results re-hash/);
  // the results are the job's artefacts all the same; the engine still the receipt's, but no worker computed this time
  assert.equal(again.artefacts.filter((a) => a.name.startsWith("results/")).length, 7);
  const receipt = (await s.svc.receipt(again.id)).doc;
  assert.deepEqual(receipt.engine.map((e) => e.id), ["AutoDock Vina"]);
  assert.doesNotMatch(receipt.device.note, /AutoDock Vina/, "nothing ran in a Vina worker in this attempt");
  assert.equal(receipt.resources.memory.dedicated, undefined);
  assert.equal(receipt.device.evidence, "verified");
});

test("resumed after Vina failed on one ligand: the runs that stand are reused one by one, a changed result is run again, the rest computed", async () => {
  const factory = fakeVina({ trap: ["ibuprofen"] });
  // one worker: the runs in order, so the failure leaves exactly the four before it
  const s = await docked({ cores: 2, vina: { factory } });
  assert.deepEqual([s.job.state, s.job.error.type], ["failed", "runtime_error"]);
  assert.deepEqual(s.VinaWorker.docks.sent, ["reference", "ligand-0", "ligand-1", "ligand-2", "ligand-3"]);
  for (const key of ["reference", "ligand-0", "ligand-1", "ligand-2"]) assert.ok(await s.store.exists([...jobDir(s.job.id), "checkpoints", `dock.${key}.json`]), key);
  assert.equal(await s.store.exists([...jobDir(s.job.id), "checkpoints", "dock.ligand-3.json"]), false);
  // a result changed in storage since: its run is not reused
  await s.store.writeText([...jobDir(s.job.id), "results", "ligand-1.json"], "{}");
  await s.svc.resume(s.job.id);
  const again = await settle(s.svc, s.job.id);
  assert.equal(again.state, "succeeded", JSON.stringify(again.error));
  assert.deepEqual(s.VinaWorker.docks.sent.slice(5), ["ligand-1", "ligand-3", "ligand-4", "ligand-5"]);
  const dock = again.stages.find((x) => x.id === "dock");
  assert.equal(dock.reused, false);
  assert.deepEqual([dock.record.runs, dock.record.reused], [7, 3]);
  assert.match(dock.detail, /^4 Vina runs on 1 worker, 3 reused$/);
  assert.equal(dock.compute.length, 4, "compute facts only for the runs made in this attempt");
  assert.ok(again.logs.some((l) => /dock: its checkpoint does not verify, so it runs again \(ligand-1: results\/ligand-1\.json/.test(l.text)), JSON.stringify(again.logs.map((l) => l.text)));
  assert.equal(again.artefacts.filter((a) => a.name.startsWith("results/")).length, 7);
  assert.match(again.device_note, /AutoDock Vina 1\.2\.7 \(WebAssembly\) in 1 dedicated Web Worker, one thread each/);
});

test("cancelled while Vina runs: every Vina worker terminated at once (a run cannot be interrupted), the finished runs' checkpoints kept", async () => {
  const gate = new Promise(() => {}); // a run that never ends by itself
  const late = new Set(["ligand-2", "ligand-3", "ligand-4", "ligand-5"]);
  const s = await setup({ vina: { hold: (p) => (late.has(p.key) ? gate : null) } });
  const job = await s.svc.submit("pipeline.dock", RUN, { project_id: "p1" });
  const dir = jobDir(job.id);
  await until(async () => (await Promise.all(["reference", "ligand-0", "ligand-1"].map((k) => s.store.exists([...dir, "checkpoints", `dock.${k}.json`])))).every(Boolean)
    && s.VinaWorker.docks.active === 4);
  const t0 = Date.now();
  await s.svc.cancel(job.id);
  const done = await settle(s.svc, job.id);
  const took = Date.now() - t0;
  assert.equal(done.state, "cancelled");
  assert.ok(took < 3000, `ended ${took} ms after the cancel, not after the service's grace (10 s)`);
  assert.ok(s.VinaWorker.instances.length === 4 && s.VinaWorker.instances.every((w) => w.terminated));
  assert.equal(s.VinaWorker.docks.active, 0);
  assert.equal(done.stages.find((x) => x.id === "dock").status, "cancelled");
  for (const k of ["reference", "ligand-0", "ligand-1"]) assert.ok(await s.store.exists([...dir, "checkpoints", `dock.${k}.json`]), k);
  for (const k of late) assert.equal(await s.store.exists([...dir, "checkpoints", `dock.${k}.json`]), false, k);
});

test("VinaClient: init gives the worker a copy of the bytes (transferred), a dock is one call, an aborted call terminates the worker at once", async () => {
  const gate = new Promise(() => {});
  const VinaWorker = vinaWorkerClass({ hold: () => gate });
  const c = new VinaClient({ Worker: VinaWorker, url: "dock.worker.js", name: "vina-9" });
  const bytes = new Uint8Array(WASM);
  const info = await c.start(bytes);
  assert.deepEqual(info.engine, { ...ENGINE });
  assert.equal(bytes.byteLength, WASM.byteLength, "the page's bytes stay its own");
  assert.equal(VinaWorker.instances[0].opts.name, "tcmstudio-vina-9");
  const ac = new AbortController();
  const p = c.dock({ key: "ligand-0", receptor: "x", ligand: "y", params: {} }, { signal: ac.signal });
  ac.abort();
  await assert.rejects(p, (err) => err.name === "AbortError" && err.type === "cancelled");
  assert.ok(VinaWorker.instances[0].terminated);
  await assert.rejects(c.dock({ key: "ligand-0" }), /the docking worker is not running/);
  // a call refused by the worker before Vina saw it is a failure of the run, not Vina's refusal
  const d = new VinaClient({ Worker: vinaWorkerClass(), url: "dock.worker.js" });
  await d.start(bytes);
  await assert.rejects(d.dock({ key: "bogus", receptor: "x", ligand: "y", params: {} }), (err) => err.type === "runtime_error" && /key is "reference" or "ligand-<index>"/.test(err.message));
  d.terminate();
});

// ------------------------------------------------------------------------------------------------- the jobs runtime

test("jobs runtime: run_pipeline dock and call_tool job.pipeline.dock are job calls; a refusal says its reason (needs_runner, too_large_for_browser)", async () => {
  assert.deepEqual(jobCall("run_pipeline", { pipeline: "dock", arguments: RUN }), { kind: "pipeline.dock", params: RUN });
  assert.deepEqual(jobCall("call_tool", { tool: "job.pipeline.dock", arguments: RUN }), { kind: "pipeline.dock", params: RUN });
  const { svc, files } = await setup();
  const block = (i) => `MODEL ${i + 1}\nROOT\nENDROOT\nTORSDOF 0\nENDMDL\n`;
  files.add("p1", "many.pdbqt", Array.from({ length: MAX_LIGANDS + 1 }, (_, i) => block(i)).join(""));
  const rt = new BrowserJobsRuntime({ service: svc });
  const call = (args) => rt.call("run_pipeline", { pipeline: "dock", arguments: args }, { project_id: "p1" });
  const pdb = await call({ ...RUN, receptor: "PDB:3PTB" });
  assert.deepEqual([pdb.status, pdb.error.reason, pdb.error.requires[0].need], ["failed", "needs_runner", "runner"]);
  const big = await call({ ...RUN, ligands: "many.pdbqt" });
  assert.deepEqual([big.error.reason, big.error.requires[0].need], ["too_large_for_browser", "memory"]);
  const seed = await call({ ...RUN, seed: 0 });
  assert.equal(seed.error.type, "bad_arguments");
  const env = await call(RUN);
  assert.equal(env.status, "job_submitted", JSON.stringify(env.error));
  const done = await settle(svc, env.job.id);
  assert.equal(done.state, "succeeded", JSON.stringify(done.error));
  const status = await rt.call("job_status", { job_id: done.id });
  assert.ok(status.governance.limitations.includes(DOCK.limitPredicted));
});

test("createJobsRuntime: the docking kind with the AutoDock Vina build boot.json pins; a site whose boot.json names none refuses it", async () => {
  const { createJobsRuntime } = await import("../../js/scientific/runtime/jobs-runtime.js");
  const make = async (vina) => createJobsRuntime({
    siteUrl: "https://science.example/", Worker: class { postMessage() {} terminate() {} }, indexedDB: new IDBFactory(), storage: fakeOpfs().storage,
    fetch: async () => ({ ok: true, json: async () => ({ ...BOOT, datasets: { path: "runtime/datasets.json" }, ...(vina ? { vina } : {}) }) }),
  });
  const rt = await make(BOOT_VINA);
  assert.equal(rt.status, "ready");
  assert.ok(rt.kinds().includes("pipeline.dock"));
  // no project file store here: the refusal is about the files, so the engine pin passed
  const env = await rt.call("run_pipeline", { pipeline: "dock", arguments: RUN });
  assert.match(env.error.message, /no project files/);
  const none = await make(null);
  const refused = await none.call("run_pipeline", { pipeline: "dock", arguments: RUN });
  assert.match(refused.error.message, /boot\.json names no AutoDock Vina build/);
  assert.equal(refused.error.reason, "needs_job_service");
});

// ------------------------------------------------------------------------------------------------- Python's own

/** What tcmstudio says, when $PYTHON (or python3) imports it with the docking functions: null otherwise. */
function pythonFacts() {
  const code = [
    "import json",
    "from tcmstudio import browser_jobs",
    "from tcmstudio.catalog import get_entry",
    "assert 'dock_read' in browser_jobs.FUNCTIONS and 'run_dock' in browser_jobs.FUNCTIONS",
    "bj = get_entry('job.pipeline.dock')['execution']['browser_job']",
    "print(json.dumps({'max': browser_jobs.DOCK_LIGANDS_MAX, 'rules': bj['runner_only'], 'engines': bj.get('engines')}))",
  ].join("\n");
  for (const py of [process.env.PYTHON, "python3"].filter(Boolean)) {
    try { return { py, ...JSON.parse(execFileSync(py, ["-I", "-c", code], { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"], timeout: 60000 })) }; } catch { /* the next one */ }
  }
  return null;
}
const PY = pythonFacts();
const NO_PY = "tcmstudio with the docking functions is not importable by $PYTHON or python3";

test("the page's docking constants are tcmstudio's: the ligands a run takes, the catalog's runner_only rules, its engine", { skip: PY ? false : NO_PY }, () => {
  assert.equal(MAX_LIGANDS, PY.max);
  assert.deepEqual(PY.rules.map(({ why: _w, ...r }) => r), DOCK_RUNNER_ONLY.map(({ why: _w, ...r }) => r));
  const entry = { execution: { browser_job: { runner_only: PY.rules } } };
  for (const params of [RUN, { ...RUN, receptor: "PDB:3PTB" }, { ...RUN, ligands: "CCO" }, { ...RUN, ligands: "u_0123456789abcdef" }, { ...RUN, site_ligand: "BEN" }, { ...RUN, allow_remote: true }]) {
    assert.equal(Boolean(dockNeedsRunner(params)), runnerOnly(entry, params).length > 0, JSON.stringify(params));
  }
  const vina = Object.values(PY.engines || {})[0];
  assert.deepEqual([vina?.engine, vina?.version, vina?.wasm_sha256], [ENGINE.name, ENGINE.version, VINA.wasm.sha256]);
});

/**
 * The job workers' Python as tcmstudio.browser_jobs itself, run natively (`python -m tcmstudio.browser_jobs FN --args
 * @file`) on a copy of the worker's memory file system under one directory (the same for every call, so the paths a run
 * records stay its own): the paths the page gives are made the copy's, and what the function writes is read back into
 * the worker's memory. fn.close() removes the directory.
 */
function nativePython(py) {
  const calls = [];
  const root = mkdtempSync(path.join(tmpdir(), "tcms-dock-"));
  const fn = async (name, args, fs) => {
    calls.push({ name, args: JSON.parse(JSON.stringify(args)) });
    rmSync(path.join(root, "work"), { recursive: true, force: true });
    {
      const real = (p) => path.join(root, p);
      for (const [p, b] of fs) {
        mkdirSync(path.dirname(real(p)), { recursive: true });
        writeFileSync(real(p), b);
      }
      const mapped = JSON.parse(JSON.stringify(args), (k, v) => (typeof v === "string" && v.startsWith("/work/") ? real(v) : v));
      writeFileSync(path.join(root, "args.json"), JSON.stringify(mapped));
      let out;
      try {
        out = execFileSync(py, ["-I", "-m", "tcmstudio.browser_jobs", name, "--args", `@${path.join(root, "args.json")}`], { encoding: "utf8", stdio: ["ignore", "pipe", "pipe"], timeout: 300000 });
      } catch (err) {
        out = String(err.stdout || "");
      }
      const walk = (dir) => {
        for (const e of readdirSync(dir)) {
          const full = path.join(dir, e);
          if (statSync(full).isDirectory()) walk(full);
          else fs.set(full.slice(root.length).split(path.sep).join("/"), new Uint8Array(readFileSync(full)));
        }
      };
      if (readdirSync(root).includes("work")) walk(path.join(root, "work"));
      const doc = JSON.parse(out.trim().split("\n").at(-1));
      return JSON.parse(JSON.stringify(doc), (k, v) => (typeof v === "string" && v.startsWith(root) ? v.slice(root.length) : v));
    }
  };
  fn.calls = calls;
  fn.close = () => rmSync(root, { recursive: true, force: true });
  return fn;
}

test("the real engine: AutoDock Vina 1.2.7 (the vendored wasm) docks benzamidine and redocks the crystal BEN; tcmstudio's Python reads the page's results when it is here", { timeout: 300000 }, async () => {
  const files = projectStore();
  files.add("p1", "3ptb_receptor.pdbqt", RECEPTOR);
  // benzamidine alone, from the fixture's first MODEL block
  const first = dec.decode(LIGANDS).split("ENDMDL\n")[0];
  files.add("p1", "benzamidine.pdbqt", `${first}ENDMDL\n`);
  files.add("p1", "ben_crystal.pdbqt", CRYSTAL);
  const { default: createVina } = await import("../../js/scientific/dock/vendor/vina-1.2.7.mjs");
  const python = PY ? nativePython(PY.py) : dockPython();
  const s = await setup({ files, python, vina: { factory: createVina } });
  let job;
  try {
    job = await settle(s.svc, (await s.svc.submit("pipeline.dock", { receptor: "3ptb_receptor.pdbqt", ligands: "benzamidine.pdbqt", reference_ligand: "ben_crystal.pdbqt", exhaustiveness: 2, poses: 3, seed: 2 }, { project_id: "p1" })).id, 300000);
  } finally {
    python.close?.();
  }
  assert.equal(job.state, "succeeded", JSON.stringify(job.error));
  const ref = await s.store.readJson([...jobDir(job.id), "results", "reference.json"]);
  const lig = await s.store.readJson([...jobDir(job.id), "results", "ligand-0.json"]);
  for (const doc of [ref, lig]) {
    assert.deepEqual(doc.engine, { ...ENGINE });
    assert.ok(doc.energies.length >= 1 && doc.energies.length <= 3 && doc.energies.every((row) => row.length === 5));
    assert.ok(doc.energies[0][0] < -4, `a benzamidine pose in the S1 pocket scores well (${doc.energies[0][0]})`);
  }
  assert.equal(job.stages.find((x) => x.id === "dock").compute.length, 2);
  if (PY) {
    // the page's results were read by PrecomputedVina (texts and parameters to the bit), and the run verified as the
    // runner's job of this kind requires
    assert.deepEqual(python.calls.map((c) => c.name), ["dock_read", "run_dock", "verify_pipeline"]);
    const record = await s.store.readJson([...jobDir(job.id), "out", "run.json"]);
    assert.equal(record.pipeline, "bioagent.docking.prepared");
    assert.equal(record.mode, "prepared");
    assert.deepEqual(record.engine, { ...ENGINE });
    assert.equal(record.complete, true);
    // seed 2: the top pose 1.13 Å from the crystal pose in atom order (the WebAssembly engine repeats a seeded search
    // to the bit on any CPU; seeds that flip the amidine give 1.9 Å, too near the 2 Å threshold for a test)
    assert.equal(record.validation.passed, true, `the crystal BEN redocked within 2 Å (${record.validation.top_pose_rmsd} Å)`);
    assert.ok(record.validation.top_pose_rmsd < 1.5, String(record.validation.top_pose_rmsd));
    assert.deepEqual(record.results.map((r) => r.energies[0][0]), [lig.energies[0][0]], "the raw energy the page's Vina gave, unrounded");
    assert.deepEqual(job.result.best.map((b) => b.ligand), ["benzamidine"]);
    assert.ok(job.outcome.checks.includes("verify_run") && job.outcome.checks.includes("page_digests"));
    const receipt = (await s.svc.receipt(job.id)).doc;
    assert.equal(receipt.device.evidence, "verified");
    assert.equal(receipt.assurance, "outputs_verified");
  }
});
