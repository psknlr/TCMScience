#!/usr/bin/env node
// Runs browser research jobs (studio/docs/BROWSER_JOBS.md) in headless Chromium and compares what they wrote with
// native CPython.
//
//   node studio/web/test/runtime/science.mjs [--mode selftest|service|fixture|omics|admet|dock|gqd] [--site DIR] [--pyodide-dir DIR]
//        [--chromium PATH] [--raw DIR --native-out DIR] [--rdkit-dir DIR] [--archive FILE [--endpoints a,b]]
//        [--json FILE] [--keep] [--timeout-min N]
//
// selftest  the planted-world known answer (tcmstudio.browser_selftest) in a job worker: no download, a few
//           seconds of CPU; its tables must equal the committed answer native CPython wrote
// service   the job service end to end on the planted world: research.run and the self-test as browser jobs,
//           checkpoints, receipts and their digests
// fixture   the snapshot build on files mounted from OPFS: the parser tests' fixture files, built in a job worker and
//           natively with the same checksums put in place of the reviewed ones; the snapshot ids must be equal
// omics     the RNA-seq (count matrix) and single-cell jobs: omics_native.py writes their reference inputs (the
//           omics_counts_world matrix; sc_world as gzipped count tables and a sample sheet) and runs the native command
//           on each with one BLAS thread; the page adds the same files to a test project and runs pipeline.rnaseq and
//           pipeline.scrna as browser jobs with the same parameters (omics-plan.mjs). Every output is compared with the
//           native one (compare.mjs: identical, or within its stated tolerance, or not compared and said so; the
//           significant genes, clusters and cell types exactly; the UMAP's structure), the receipts' inputs with the
//           files' digests, and the browser's files are held to the known answer natively (omics_native.py values)
// admet     the ADMET jobs: admet_native.py writes their inputs (the known-answer archive of BioScience-Harness/tests/
//           admet_world.py, byte-reproducible, or with --archive a real TDC ADMET group archive; and a molecule set:
//           drug-like molecules, TCM natural products, salts, molecules the browser chemistry refuses with the step it
//           lacks, one that is not a SMILES); the page adds them to a test project and runs admet.models (the archive a
//           project file) and pipeline.admet as browser jobs (admet-plan.mjs), RDKit.js from --rdkit-dir when given;
//           then the native side runs (RDKit 2026.9.1, scikit-learn 1.8.0, one thread): the models trained on the
//           molecules the browser kept, the pipeline on every molecule and on the kept ones. compare.mjs holds the
//           browser to it exactly: the cards (apart from file digests, seconds and the chemistry's name), the domains,
//           the test-set predictions at full precision, admet.tsv and the plot as bytes, the reports apart from the
//           lines naming the refused molecules, each molecule's chemistry and alerts, its predictions and domain search,
//           the refusals the set expects (each read by the runner), run.json's stages. pipeline.admet runs again with
//           the GPU forced on SwiftShader (the handlers' harness-only switch; Chromium is launched with WebGPU and
//           SwiftShader): its outputs must be the first run's, its compute facts webgpu on a software adapter. With
//           the known-answer archive both sides' cards are also held to its answer (admet_native.py values)
// dock      the docking job: dock_native.py copies the prepared fixtures (BioScience-Harness/tests/fixtures/docking/
//           prepared: 3PTB's pocket as a rigid PDBQT receptor, six ligands, the crystal ligand BEN) after checking that
//           native AutoDock Vina 1.2.7 is here; the page adds them to a test project and runs pipeline.dock as a browser
//           job (dock-plan.mjs: BEN the reference ligand, seed 42), the Python stages in Pyodide and AutoDock Vina 1.2.7
//           compiled to WebAssembly in workers of its own, then scores each run's top pose again with the WebAssembly
//           engine. Natively (dock_native.py) dock_read writes the plan, run_dock reads the browser's own Vina results
//           (PrecomputedVina) in CPython, the runner's command (`bioagent dock RECEPTOR.pdbqt LIGANDS.pdbqt
//           --reference-ligand …`, native Vina) docks the same files with the same seed, and native Vina scores the
//           browser's top poses. compare.mjs compareDock: the plan and the texts Vina was given, the box, the inputs'
//           digests and the ligand table identical; every output of the analysis the browser's bytes (run.json and
//           stages.json apart from the process's own fields); each top pose's Vina.score() the same float64 bits in
//           WebAssembly and natively; both searches complete, both redock BEN within 2 Å, the same ligands docked, each
//           ligand's best score against native's at the same seed reported (not counted: a seeded search is not
//           bit-reproducible across engines); the receipt's engine, compute facts (vina-wasm), device note and the
//           dedicated workers' memory (not measured, and said so)
// gqd       the 葛根芩连汤 research case on real files: the raw files in --raw (as the live-data workflow downloaded
//           them) are imported into the page's private storage, research.snapshot builds the snapshots and
//           research.run studies the formula, both through the browser job service; every export is then compared
//           with the native run's exports in --native-out (`bioagent research … --out DIR` on the same files)
//
// Without --site the site is built first (build_web.py --dev). Pyodide comes from boot.json's index_url (jsDelivr)
// unless --pyodide-dir serves a copy at /pyodide/; RDKit.js's wasm from boot.json's rdkit url (jsDelivr) unless
// --rdkit-dir serves the directory holding RDKit_minimal.wasm (npm's @rdkit/rdkit dist) at /rdkit/. $PYTHON (default
// python3) builds the site and runs the native side: it needs bioagent and tcmstudio installed, pytest for fixture and
// omics, rdkit==2026.9.1 and scikit-learn==1.8.0 for admet, vina==1.2.7 for dock (meeko==0.8.0 and gemmi==0.7.5 beside
// it, as bioagent's docking extra installs them); the Vina wasm is the site's own, at boot.json's pin. Exit 0 only when
// every check passed.
// When the page fails, its console is printed; when it gives no result at all (a timeout, a hang in a worker's boot or
// a fetch, an error before its result), its failures and the rows it had shown too, and the comparison says that no
// result came back rather than a row per file. --json keeps all of it.

import { spawnSync } from "node:child_process";
import { createReadStream, existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, statSync, writeFileSync } from "node:fs";
import { createServer } from "node:http";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createHash } from "node:crypto";
import { ENGINE } from "../../js/scientific/dock/vina.js";
import { admetPlan } from "./admet-plan.mjs";
import { compareAdmet, compareDock, compareExports, compareOmics } from "./compare.mjs";
import { dockPlan } from "./dock-plan.mjs";
import { chromiumFlags } from "./gpu-science.mjs";
import { OMICS_JOBS } from "./omics-plan.mjs";
import { MIME, buildSite, headersFor, loadPlaywright, parseHeaders, runPage } from "./run.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(HERE, "../../../..");
const PYTHON = process.env.PYTHON || "python3";

/** The parser tests' fixture raw files written into a new directory, and the same build run natively there. */
function nativeFixture() {
  const dir = mkdtempSync(path.join(tmpdir(), "tcms-fixture-"));
  const raw = path.join(dir, "raw");
  const code = [
    "import json, runpy, sys",
    "from pathlib import Path",
    `sys.path.insert(0, ${JSON.stringify(path.join(REPO, "BioScience-Harness", "tests"))})`,
    "from test_snapshot_pipeline import _raw",
    "raw = Path(sys.argv[1]); _raw(raw)",
    `runpy.run_path(${JSON.stringify(path.join(HERE, "fixture_specs.py"))})["patch"](str(raw))`,
    "from tcmstudio.browser_jobs import call",
    "out = Path(sys.argv[2])",
    "args = {'raw_dir': str(raw), 'snapshot_root': str(out / 'snap'), 'ledger': str(out / 'snap' / 'ledger.jsonl'), 'report_path': str(out / 'report.json')}",
    "print(call('build_snapshots', json.dumps(args)))",
  ].join("\n");
  const r = spawnSync(PYTHON, ["-c", code, raw, dir], { encoding: "utf8", maxBuffer: 64 << 20 });
  if (r.status !== 0) throw new Error(`the native fixture build failed: ${r.stderr.slice(-2000)}`);
  const doc = JSON.parse(r.stdout.trim().split("\n").at(-1));
  if (!doc.ok) throw new Error(`the native fixture build failed: ${JSON.stringify(doc.error)}`);
  return { dir, raw, report: doc.result, cleanup: () => rmSync(dir, { recursive: true, force: true }) };
}

/** The browser build's snapshot ids (and, for the worker's own report, its stage statuses) against the native build's;
 * `label` prefixes the rows of a second browser build (research.snapshot as a job, whose result has no stages). */
function compareFixture(browser, native, label = "") {
  const ids = (doc) => Object.fromEntries(Object.entries(doc?.snapshots || {}).map(([k, v]) => [k, v.snapshot_id]));
  const stages = (doc) => (doc?.stages || []).map((x) => `${x.stage}:${x.status}`);
  const b = ids(browser);
  const n = ids(native);
  const rows = [...new Set([...Object.keys(b), ...Object.keys(n)])].sort().map((k) => ({ name: `${label}${k}`, same: b[k] === n[k], why: b[k] === n[k] ? b[k] : `browser ${b[k]} / native ${n[k]}` }));
  if (!label) rows.push({ name: "stages", same: JSON.stringify(stages(browser)) === JSON.stringify(stages(native)), why: stages(browser).join(" ") });
  return rows;
}

/** The omics jobs' inputs in a new directory and the native command run on each (omics_native.py prepare). */
function nativeOmics() {
  const dir = mkdtempSync(path.join(tmpdir(), "tcms-omics-"));
  const r = spawnSync(PYTHON, [path.join(HERE, "omics_native.py"), "prepare", dir, JSON.stringify(OMICS_JOBS)], { encoding: "utf8", maxBuffer: 64 << 20 });
  if (r.status !== 0) {
    rmSync(dir, { recursive: true, force: true });
    throw new Error(`the native omics runs failed (${PYTHON}: it needs bioagent, tcmstudio and pytest; set $PYTHON): ${(r.stderr || r.error?.message || "").slice(-2000)}`);
  }
  return { dir, raw: path.join(dir, "raw"), report: JSON.parse(r.stdout.trim().split("\n").at(-1)), cleanup: () => rmSync(dir, { recursive: true, force: true }) };
}

/** The native side at one thread, as a browser worker has (BLAS, OpenMP: scikit-learn's boosting). */
const ONE_THREAD = { OPENBLAS_NUM_THREADS: "1", OMP_NUM_THREADS: "1", MKL_NUM_THREADS: "1" };

/** The ADMET jobs' inputs in a new directory (admet_native.py prepare): the archive (`archive`, a real one, or the
 * known-answer one built there) and the molecule set. */
function nativeAdmet(plan, archive) {
  const dir = mkdtempSync(path.join(tmpdir(), "tcms-admet-"));
  const argv = [path.join(HERE, "admet_native.py"), "prepare", dir, JSON.stringify(plan), ...(archive ? [path.resolve(archive)] : [])];
  const r = spawnSync(PYTHON, argv, { encoding: "utf8", maxBuffer: 64 << 20, env: { ...process.env, ...ONE_THREAD } });
  if (r.status !== 0) {
    rmSync(dir, { recursive: true, force: true });
    throw new Error(`the ADMET inputs could not be written (${PYTHON}: it needs bioagent and tcmstudio, rdkit==2026.9.1 and scikit-learn==1.8.0; set $PYTHON): ${(r.stderr || r.error?.message || "").slice(-2000)}`);
  }
  return {
    dir, raw: path.join(dir, "raw"), report: JSON.parse(r.stdout.trim().split("\n").at(-1)),
    expected: JSON.parse(readFileSync(path.join(dir, "expected.json"), "utf8")), cleanup: () => rmSync(dir, { recursive: true, force: true }),
  };
}

/** One admet_native.py command in the work directory → its last line of output as JSON, or {error}. */
function admetNative(ad, args) {
  const r = spawnSync(PYTHON, [path.join(HERE, "admet_native.py"), ...args], { encoding: "utf8", maxBuffer: 256 << 20, env: { ...process.env, ...ONE_THREAD } });
  if (r.status !== 0) return { error: `admet_native.py ${args[0]} failed (exit ${r.status}): ${(r.stderr || r.error?.message || "").slice(-2000)}` };
  return JSON.parse(r.stdout.trim().split("\n").at(-1));
}

const sha256File = (file) => createHash("sha256").update(readFileSync(file)).digest("hex");

/** Every file under `dir` → {relative path: Buffer}. */
function readTree(dir, base = dir) {
  const out = {};
  for (const name of readdirSync(dir)) {
    const p = path.join(dir, name);
    if (statSync(p).isDirectory()) Object.assign(out, readTree(p, base));
    else out[path.relative(base, p).split(path.sep).join("/")] = readFileSync(p);
  }
  return out;
}

/** The files a job's parameters name in `raw`, a sample sheet's path cells among them → [names]. */
function namedInputs(params, raw) {
  const names = ["counts", "samples", "source", "markers"].map((k) => params[k]).filter((v) => typeof v === "string" && v);
  if (/\.(csv|tsv)$/i.test(params.source || "")) {
    const [head, ...lines] = readFileSync(path.join(raw, params.source), "utf8").trim().split(/\r?\n/);
    const sep = head.split("\t").length > head.split(",").length ? "\t" : ",";
    const col = head.split(sep).indexOf("path");
    if (col >= 0) names.push(...lines.map((l) => l.split(sep)[col]));
  }
  return names;
}

/** The page gave no result (runPage's own: a timeout, a hang in a worker's boot or a fetch, an error before
 * __result) → why, from its first failure; null when it did. */
function noResult(res) {
  return Array.isArray(res?.checks) ? null : `no result came back from the page (${res?.failures?.[0] ?? "no failure recorded"})`;
}

/** The states a job ends in (science-check.mjs waitFor); past its wait the page records a job in whatever state it is. */
const ENDED = ["succeeded", "failed", "cancelled", "interrupted"];

/** Why a job's receipt and outputs are not there, when they are not: no result from the page, no such job in its
 * result, a job that did not succeed, or one that had not ended when the page stopped waiting → text | null. */
export function lostJob(res, job, kind) {
  if (noResult(res)) return noResult(res);
  if (!job) return `the page's result has no ${kind} job (its checks say why)`;
  if (job.files) return null;
  if (!ENDED.includes(job.state)) return `the ${kind} job had not ended when the page stopped waiting for it (${job.state ? `still ${job.state}` : "no state recorded"}); its outputs were not read back`;
  return `the ${kind} job ended ${job.state} in the browser${job.error?.message ? ` (${job.error.message})` : ""}; its outputs were not read back`;
}

const PAGE_ROWS = 40;
const CONSOLE_LINES = 40;

/** What the page itself said, for a reader of the log: when it gave no result, its failures and the rows it had shown;
 * when it failed, its console (runPage keeps 60 lines of 400 characters; the last CONSOLE_LINES here) → lines. */
export function pageLines(res) {
  const lines = [];
  if (noResult(res)) {
    for (const f of res?.failures || []) lines.push(`FAILED page: ${f}`);
    const rows = String(res?.partial || "").split("\n").filter((l) => l.trim());
    lines.push(rows.length ? `page   its rows before it stopped${rows.length > PAGE_ROWS ? ` (the last ${PAGE_ROWS} of ${rows.length})` : ""}:` : "page   no rows shown before it stopped");
    for (const l of rows.slice(-PAGE_ROWS)) lines.push(`         ${l.split("\t").filter(Boolean).join(" | ").slice(0, 300)}`);
  }
  if (!res?.ok) {
    const out = res?.console || [];
    lines.push(out.length ? `page   its console${out.length > CONSOLE_LINES ? ` (the last ${CONSOLE_LINES} of ${out.length} lines kept)` : ""}:` : "page   nothing in its console");
    for (const l of out.slice(-CONSOLE_LINES)) lines.push(`         ${l}`);
  }
  return lines;
}

/**
 * One omics job of the page against the native run: its receipt's inputs against the files' digests on disk, its
 * outputs written out (WORK/browser/<key>, each at the digest the page read) and compared with the native command's,
 * then held to the known answer natively, the native run's outputs too → {rows, values, native_values}. When the
 * job's receipt and outputs did not come back (lostJob), one row says why.
 */
function checkOmicsJob(plan, job, om, res) {
  const rows = [];
  const values = (run) => {
    const r = spawnSync(PYTHON, [path.join(HERE, "omics_native.py"), "values", om.dir, plan.key, run], { encoding: "utf8", maxBuffer: 16 << 20 });
    if (r.status !== 0) return [{ label: "the known answer's checks ran", ok: false, detail: (r.stderr || "").slice(-1500) }];
    return JSON.parse(r.stdout.trim().split("\n").at(-1)).checks;
  };
  const native = path.join(om.dir, "native", plan.key);
  const lost = lostJob(res, job, plan.kind);
  if (lost) {
    rows.push({ name: "the browser job: its receipt and outputs", status: "missing", ok: false, counted: true, why: lost });
    return { rows, values: [{ label: "the browser's outputs", ok: false, detail: `none came back: ${lost}` }], native_values: values(native) };
  }
  const flat = Object.entries(job.receipt?.inputs || {}).flatMap(([k, v]) => (v && typeof v.sha256 === "string" ? [[k, v]] : Object.entries(v || {}).map(([kk, vv]) => [`${k} ${kk}`, vv])));
  const named = namedInputs(plan.params, om.raw);
  const unmatched = named.filter((name) => !flat.some(([, v]) => v?.source?.name === name && v.sha256 === sha256File(path.join(om.raw, name))));
  rows.push({ name: "inputs: the served files, at their digests", status: job.receipt && !unmatched.length && flat.length === named.length ? "same" : "different", counted: true,
    why: !job.receipt ? "no receipt came back from the browser job" : unmatched.length ? `not recorded at the file's digest: ${unmatched.join(", ")}` : flat.length !== named.length ? `${flat.length} inputs recorded for ${named.length} files` : `${named.length} inputs (${named.join(", ")}), each at the SHA-256 of the file served` });
  const dir = path.join(om.dir, "browser", plan.key);
  const files = job.files;
  const torn = [];
  for (const [rel, f] of Object.entries(files)) {
    const bytes = Buffer.from(f.text, "utf8");
    if (`sha256:${createHash("sha256").update(bytes).digest("hex")}` !== f.sha256) torn.push(rel);
    mkdirSync(path.dirname(path.join(dir, rel)), { recursive: true });
    writeFileSync(path.join(dir, rel), bytes);
  }
  if (torn.length) rows.push({ name: "outputs: their text", status: "different", counted: true, why: `not UTF-8 text that re-encodes to the bytes read: ${torn.join(", ")}` });
  rows.push(...compareOmics(plan.kind, files, readTree(native)));
  for (const r of rows) r.ok = r.ok ?? !["different", "missing"].includes(r.status);
  return { rows, values: Object.keys(files).length ? values(dir) : [{ label: "the browser's outputs", ok: false, detail: "none came back" }], native_values: values(native) };
}

const TAG = { identical: "same  ", same: "same  ", same_apart_from_excluded: "same  ", same_apart_from_figures: "same  ", same_apart_from_refused: "same  ", structure_held: "same  ", within_tolerance: "near  ", flips_near_alpha: "near  ", not_compared: "skip  ", reported: "info  " };

/**
 * The ADMET jobs of the page against the native side: the training molecules the browser refused handed to
 * admet_native.py native (the native models are trained without them), its runs read back, compareAdmet's rows, and
 * with the known-answer archive both sides' cards held to its answer → {rows, values, native, times}. When the page's
 * jobs did not come back, one row says why and nothing native is run.
 */
function checkAdmet(plan, res, ad) {
  const a = res?.admet;
  const lost = noResult(res) || (!a?.models?.ok ? `the admet.models job ${a?.models ? `ended ${a.models.state}${a.models.error?.message ? ` (${a.models.error.message})` : ""}` : "did not run (the page's checks say why)"}; nothing to compare` : null);
  if (lost) return { rows: [{ name: "the ADMET browser jobs", status: "missing", ok: false, counted: true, why: lost }], values: [], native: null, times: null };
  const kept = { training: Object.fromEntries(Object.entries(a.rechem || {}).map(([e, r]) => [e, { train_val: r.train_val.refused.map(([s, why]) => [s, why]), test: r.test.refused.map(([s, why]) => [s, why]) }])) };
  writeFileSync(path.join(ad.dir, "kept.json"), JSON.stringify(kept));
  const done = admetNative(ad, ["native", ad.dir, JSON.stringify(plan), path.join(ad.dir, "kept.json")]);
  if (done.error) return { rows: [{ name: "the native runs", status: "missing", ok: false, counted: true, why: done.error }], values: [], native: null, times: null };
  const native = JSON.parse(readFileSync(done.written, "utf8"));
  const nativeFiles = {};
  for (const key of ["runner", "kept"]) {
    const run = native.runs[key];
    nativeFiles[key] = existsSync(run.out) ? readTree(run.out) : {};
    if (run.exit !== 0) console.error(`native ${key} run: exit ${run.exit}: ${run.stderr.slice(-400)}`);
  }
  // the browser's files written out (kept with --keep), each at the digest the page read
  for (const [key, job] of [["pipeline", a.pipeline], ["forced", a.forced]]) {
    for (const [rel, f] of Object.entries(job?.files || {})) {
      if (typeof f.text !== "string") continue;
      mkdirSync(path.dirname(path.join(ad.dir, "browser", key, rel)), { recursive: true });
      writeFileSync(path.join(ad.dir, "browser", key, rel), f.text);
    }
  }
  const rows = compareAdmet({ browser: a, native, nativeFiles, expected: ad.expected, real: plan.real });
  const values = [];
  if (!plan.real) {
    mkdirSync(path.join(ad.dir, "browser"), { recursive: true });
    writeFileSync(path.join(ad.dir, "browser", "model_cards.json"), JSON.stringify(a.models.cards || {}));
    for (const [who, file] of [["browser", path.join(ad.dir, "browser", "model_cards.json")], ["native", path.join(native.models_dir, "model_cards.json")]]) {
      const v = admetNative(ad, ["values", ad.dir, file]);
      for (const c of v.checks || [{ label: "the known answer's checks ran", ok: false, detail: v.error }]) values.push({ ...c, who });
    }
  }
  const stage = (job, id) => job?.stages?.find((x) => x.id === id)?.seconds ?? null;
  const eps = Object.keys(native.cards || {});
  const times = {
    models: { browser_job_s: a.models.receipt?.duration_s ?? null, browser_ms: a.models.ms, chemistry_s: Object.fromEntries(eps.map((e) => [e, stage(a.models, `chemistry.${e}`)])), train_s: Object.fromEntries(eps.map((e) => [e, stage(a.models, `train.${e}`)])), wasm_peak_bytes: a.models.memory?.wasm_peak_bytes ?? null, native_s: native.runs.models.seconds },
    pipeline: { browser_job_s: a.pipeline?.receipt?.duration_s ?? null, chemistry_s: stage(a.pipeline, "chemistry"), domain_s: stage(a.pipeline, "domain"), analysis_s: stage(a.pipeline, "analysis"), wasm_peak_bytes: a.pipeline?.memory?.wasm_peak_bytes ?? null, native_s: native.runs.kept.seconds },
    forced: { browser_job_s: a.forced?.receipt?.duration_s ?? null, domain_s: stage(a.forced, "domain") },
    rechem_ms: Object.fromEntries(Object.entries(a.rechem || {}).map(([e, r]) => [e, r.train_val.ms + r.test.ms])),
    probe_wasm_bytes: a.probe?.memory?.wasm_bytes ?? null,
  };
  return { rows, values, native, times };
}

/** The docking job's inputs in a new directory (dock_native.py prepare): the prepared fixtures, copied, once native
 * AutoDock Vina 1.2.7 is found here. */
function nativeDock(plan) {
  const dir = mkdtempSync(path.join(tmpdir(), "tcms-dock-"));
  const r = spawnSync(PYTHON, [path.join(HERE, "dock_native.py"), "prepare", dir, JSON.stringify(plan)], { encoding: "utf8", maxBuffer: 64 << 20 });
  if (r.status !== 0) {
    rmSync(dir, { recursive: true, force: true });
    throw new Error(`the docking inputs could not be prepared (${PYTHON}: it needs bioagent and tcmstudio, and vina==1.2.7 with meeko==0.8.0 and gemmi==0.7.5; set $PYTHON): ${(r.stderr || r.error?.message || "").slice(-2000)}`);
  }
  return { dir, raw: path.join(dir, "raw"), report: JSON.parse(r.stdout.trim().split("\n").at(-1)), cleanup: () => rmSync(dir, { recursive: true, force: true }) };
}

/** `files` ({path: {sha256, text}}) written under `dir`, each held to the digest the page read → the paths whose text
 * does not re-encode to those bytes. */
function writeBack(dir, files) {
  const torn = [];
  for (const [rel, f] of Object.entries(files || {})) {
    if (typeof f?.text !== "string") continue;
    const bytes = Buffer.from(f.text, "utf8");
    if (`sha256:${createHash("sha256").update(bytes).digest("hex")}` !== f.sha256) torn.push(rel);
    mkdirSync(path.dirname(path.join(dir, rel)), { recursive: true });
    writeFileSync(path.join(dir, rel), bytes);
  }
  return torn;
}

/**
 * The docking job of the page against the native side: the browser's outputs, Vina results and plan written out
 * (WORK/browser; the results are what run_dock reads natively), the poses it scored (WORK/browser/scored.json),
 * dock_native.py native run, compareDock's rows → {rows, native, times}. When the page's job did not come back, one row
 * says why and nothing native is run.
 */
function checkDock(plan, res, dk) {
  const job = res?.dock?.job;
  const lost = noResult(res) || (!job?.ok ? `the pipeline.dock job ${job ? `ended ${job.state}${job.error?.message ? ` (${job.error.message})` : ""}` : "did not run (the page's checks say why)"}; nothing to compare` : null);
  if (lost) return { rows: [{ name: "the pipeline.dock browser job", status: "missing", ok: false, counted: true, why: lost }], native: null, times: null };
  const browser = path.join(dk.dir, "browser");
  const torn = [
    ...writeBack(path.join(browser, "out"), job.files),
    ...writeBack(path.join(browser, "results"), Object.fromEntries(Object.entries(job.results || {}).map(([k, f]) => [`${k}.json`, f]))),
    ...writeBack(path.join(browser, "read", "plan"), job.plan),
  ];
  const scored = (res.dock.scored || []).filter((s) => s.pose && s.params).map((s) => ({ key: s.key, name: s.name, pose: s.pose, params: s.params }));
  writeFileSync(path.join(browser, "scored.json"), JSON.stringify(scored));
  const r = spawnSync(PYTHON, [path.join(HERE, "dock_native.py"), "native", dk.dir, JSON.stringify(plan)], { encoding: "utf8", maxBuffer: 256 << 20 });
  if (r.status !== 0) return { rows: [{ name: "the native runs", status: "missing", ok: false, counted: true, why: `dock_native.py native failed (exit ${r.status}): ${(r.stderr || r.error?.message || "").slice(-2000)}` }], native: null, times: null };
  const native = JSON.parse(readFileSync(JSON.parse(r.stdout.trim().split("\n").at(-1)).written, "utf8"));
  const tree = (dir) => (existsSync(dir) ? readTree(dir) : {});
  const nativeFiles = { plan: tree(native.plan_dir), analysis: tree(native.runs.analysis.out), runner: tree(native.runs.runner.out) };
  const rows = [
    ...(torn.length ? [{ name: "the browser's files: their text", status: "different", ok: false, counted: true, why: `not UTF-8 text that re-encodes to the bytes read: ${torn.join(", ")}` }] : []),
    ...compareDock({ browser: res.dock, native, nativeFiles, expected: { files: dk.report.files, engine: ENGINE } }),
  ];
  const stage = (id) => job.stages?.find((x) => x.id === id);
  const facts = [].concat(stage("dock")?.compute || []);
  const times = {
    browser_job_s: job.receipt?.duration_s ?? null, browser_ms: job.ms,
    stages_s: Object.fromEntries((job.stages || []).map((x) => [x.id, x.seconds])),
    dock: { runs: facts.length, workers: stage("dock")?.record?.workers ?? null, why: stage("dock")?.record?.why ?? null, run_ms: facts.map((f) => f.ms) },
    wasm_score_ms: (res.dock.scored || []).map((s) => s.ms ?? null),
    native_runner_s: native.runs.runner.seconds, native_analysis_s: native.runs.analysis.seconds,
    native_score_ms: (native.scores || []).map((s) => s.ms),
  };
  return { rows, native, times };
}

function parseArgs(argv) {
  const a = { mode: "selftest", keep: false, timeoutMin: null };
  for (let i = 0; i < argv.length; i++) {
    const k = argv[i];
    const v = () => argv[++i];
    if (k === "--mode") a.mode = v();
    else if (k === "--site") a.site = v();
    else if (k === "--pyodide-dir") a.pyodideDir = v();
    else if (k === "--chromium") a.chromium = v();
    else if (k === "--raw") a.raw = v();
    else if (k === "--native-out") a.nativeOut = v();
    else if (k === "--rdkit-dir") a.rdkitDir = v();
    else if (k === "--archive") a.archive = v();
    else if (k === "--endpoints") a.endpoints = v().split(",").map((x) => x.trim()).filter(Boolean);
    else if (k === "--json") a.json = v();
    else if (k === "--keep") a.keep = true;
    else if (k === "--timeout-min") a.timeoutMin = Number(v());
    else if (k === "--help" || k === "-h") { console.log(readFileSync(fileURLToPath(import.meta.url), "utf8").split("\n\n")[0].split("\n").slice(1).join("\n")); process.exit(0); }
    else throw new Error(`unknown argument ${k}`);
  }
  if (!["selftest", "service", "fixture", "omics", "admet", "dock", "gqd"].includes(a.mode)) throw new Error("--mode is selftest, service, fixture, omics, admet, dock or gqd");
  if ((a.archive || a.endpoints || a.rdkitDir) && a.mode !== "admet") throw new Error("--archive, --endpoints and --rdkit-dir are for --mode admet");
  if (a.endpoints && !a.archive) throw new Error("--endpoints chooses among a real archive's endpoints (--archive FILE)");
  if (a.rdkitDir && !existsSync(path.join(a.rdkitDir, "RDKit_minimal.wasm"))) throw new Error(`--rdkit-dir: no RDKit_minimal.wasm in ${a.rdkitDir}`);
  if (a.mode === "gqd" && (!a.raw || !a.nativeOut)) throw new Error("--mode gqd needs --raw DIR and --native-out DIR");
  return a;
}

/** The built site with its _headers, a Pyodide copy at /pyodide/, RDKit.js's wasm at /rdkit/ and the raw files at /raw/
 * (streamed, not read whole). */
function serve({ site, pyodideDir, raw, rdkitDir = null }) {
  const rules = existsSync(path.join(site, "_headers")) ? parseHeaders(readFileSync(path.join(site, "_headers"), "utf8")) : [];
  const server = createServer((req, res) => {
    const url = new URL(req.url, "http://localhost");
    let pathname = decodeURIComponent(url.pathname);
    let root = site;
    const headers = headersFor(rules, url.pathname);
    if (!rules.length) {
      headers.set("cross-origin-opener-policy", "same-origin");
      headers.set("cross-origin-embedder-policy", "require-corp");
    }
    if (raw && pathname === "/raw/index.json") {
      const names = readdirSync(raw).filter((n) => !n.startsWith(".") && statSync(path.join(raw, n)).isFile()).sort();
      headers.set("content-type", "application/json");
      res.writeHead(200, Object.fromEntries(headers)).end(JSON.stringify(names.map((name) => ({ name, bytes: statSync(path.join(raw, name)).size }))));
      return;
    }
    if (pyodideDir && pathname.startsWith("/pyodide/")) { root = pyodideDir; pathname = pathname.slice("/pyodide".length); }
    else if (rdkitDir && pathname.startsWith("/rdkit/")) { root = rdkitDir; pathname = pathname.slice("/rdkit".length); }
    else if (raw && pathname.startsWith("/raw/")) { root = raw; pathname = pathname.slice("/raw".length); }
    const file = path.join(root, pathname);
    if (!file.startsWith(root) || !existsSync(file) || !statSync(file).isFile() || path.basename(file) === "_headers") {
      res.writeHead(404, Object.fromEntries(headers)).end("not found");
      return;
    }
    headers.set("content-type", MIME[path.extname(file)] || "application/octet-stream");
    headers.set("content-length", String(statSync(file).size));
    res.writeHead(200, Object.fromEntries(headers));
    createReadStream(file).pipe(res);
  });
  return new Promise((resolve) => server.listen(0, "127.0.0.1", () => resolve({ server, origin: `http://127.0.0.1:${server.address().port}` })));
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const built = args.site ? null : buildSite();
  const site = path.resolve(args.site || built.out);
  const pyodideDir = args.pyodideDir ? path.resolve(args.pyodideDir) : null;
  const index = pyodideDir ? "/pyodide/" : "";
  const report = { mode: args.mode, site, ok: true, checks: [] };
  const fx = args.mode === "fixture" ? nativeFixture() : null;
  const om = args.mode === "omics" ? nativeOmics() : null;
  const plan = args.mode === "admet" ? admetPlan({ real: Boolean(args.archive), endpoints: args.endpoints || null }) : null;
  const ad = plan ? nativeAdmet(plan, args.archive) : null;
  const dp = args.mode === "dock" ? dockPlan() : null;
  const dk = dp ? nativeDock(dp) : null;
  const rdkitDir = args.rdkitDir ? path.resolve(args.rdkitDir) : null;
  const srv = await serve({ site, pyodideDir, rdkitDir, raw: fx ? fx.raw : om ? om.raw : ad ? ad.raw : dk ? dk.raw : args.raw ? path.resolve(args.raw) : null });
  const { chromium } = await loadPlaywright();
  // the proxy, when something is fetched from the network (Pyodide, or RDKit.js's wasm, from jsDelivr)
  const remote = !pyodideDir || (ad && !rdkitDir);
  const proxy = remote && (process.env.HTTPS_PROXY || process.env.https_proxy);
  const browser = await chromium.launch({
    executablePath: args.chromium || process.env.CHROMIUM_PATH || undefined,
    // admet: WebGPU on, SwiftShader forced, for the run whose domain search the harness switch puts on the GPU
    args: [...(proxy ? [`--proxy-server=${proxy}`, "--proxy-bypass-list=127.0.0.1;localhost"] : []), ...(ad ? chromiumFlags({ software: true }) : [])],
  });
  const timeout = (args.timeoutMin || (args.mode === "gqd" ? 90 : args.mode === "omics" || args.mode === "dock" ? 20 : args.mode === "admet" ? (plan.real ? 210 : 25) : 10)) * 60_000;
  try {
    const ctx = await browser.newContext();
    const extra = ad ? `${rdkitDir ? "&rdkit=/rdkit/RDKit_minimal.wasm" : ""}${plan.real ? "&real=1" : ""}${args.endpoints ? `&endpoints=${encodeURIComponent(args.endpoints.join(","))}` : ""}` : "";
    const q = `?mode=${args.mode}${index ? `&index=${encodeURIComponent(index)}` : ""}${extra}`;
    const res = await runPage(ctx, `${srv.origin}/test/runtime/science.html${q}`, timeout);
    report.page = res;
    if (!res.ok) report.ok = false;
    for (const c of res.checks || []) console.error(`${c.ok ? "ok    " : "FAILED"} ${c.label}${c.detail ? `: ${c.detail}` : ""}`);
    for (const line of pageLines(res)) console.error(line);
    if (fx) {
      // no result from the page: one row saying so, not a snapshot each "browser undefined"
      report.comparison = noResult(res) ? [{ name: "the snapshots", same: false, why: noResult(res) }]
        : [...compareFixture(res.report, fx.report), ...compareFixture(res.service_report, fx.report, "research.snapshot job: ")];
      for (const r of report.comparison) {
        console.error(`${r.same ? "same  " : "DIFFER"} ${r.name}: ${r.why}`);
        if (!r.same) report.ok = false;
      }
      if (!res.report || !res.service_report) report.ok = false;
    }
    if (om) {
      report.native = om.report;
      report.omics = {};
      for (const plan of OMICS_JOBS) {
        const job = res.jobs?.[plan.key];
        const c = checkOmicsJob(plan, job, om, res);
        report.omics[plan.key] = c;
        for (const r of c.rows) {
          console.error(`${TAG[r.status] || (r.ok ? "near  " : "DIFFER")} ${plan.key} ${r.name}: ${r.why}`);
          if (!r.ok) report.ok = false;
        }
        for (const [who, list] of [["browser", c.values], ["native", c.native_values]]) {
          for (const v of list) {
            console.error(`${v.ok ? "ok    " : "FAILED"} ${plan.key} known answer (${who}'s files): ${v.label}: ${v.detail}`);
            if (!v.ok) report.ok = false;
          }
        }
        const nat = om.report.runs?.[plan.key];
        const recorded = Object.entries(nat?.recorded_digests || {});
        if (recorded.length) {
          // for the record, not counted: the manifest's sc_world digests were taken at OpenBLAS's default thread count
          const at = recorded.filter(([, v]) => v === "the recorded digest").map(([k]) => k);
          const not = recorded.filter(([, v]) => v !== "the recorded digest").map(([k]) => k);
          console.error(`info   ${plan.key} native outputs, in the environment the reference's digests were recorded in: ${at.length ? `${at.join(", ")} at them` : ""}${at.length && not.length ? "; " : ""}${not.length ? `${not.join(", ")} not${not.includes("cells.tsv") ? " (cells.tsv holds the UMAP layout, recorded at OpenBLAS's default thread count; here one thread)" : ""}` : ""}`);
        }
        const analysis = (job?.stages || []).find((x) => x.id === "analysis");
        const memory = job?.memory?.wasm_peak_bytes;
        const boot = (job?.receipt?.workers || []).map((w) => w.boot_ms).filter(Number.isFinite);
        c.times = { browser_job_s: job?.receipt?.duration_s ?? null, browser_analysis_s: analysis?.seconds ?? null, worker_boot_ms: boot.length ? boot : null, native_s: nat?.seconds ?? null, wasm_peak_bytes: memory ?? null };
        console.error(`time   ${plan.key}: browser job ${c.times.browser_job_s ?? "?"} s from start to end (analysis stage ${c.times.browser_analysis_s ?? "?"} s${boot.length ? `; its worker booted in ${boot.map((b) => `${(b / 1000).toFixed(1)} s`).join(", ")}` : ""}; worker memory ${memory ? `${(memory / 2 ** 20).toFixed(0)} MiB` : "not reported"}), native command ${c.times.native_s ?? "?"} s`);
      }
      // the texts were compared and written out (kept with --keep); the report keeps each file's digest and size
      for (const job of Object.values(res.jobs || {})) for (const f of Object.values(job.files || {})) delete f.text;
      const env = om.report.environment || {};
      console.error(`native: Python ${env.python}, numpy ${env.numpy}, scipy ${env.scipy}, ${env.machine}, ${Object.entries(env.threads || {}).map(([k, v]) => `${k}=${v}`).join(" ")}`);
      if (!res.jobs) report.ok = false;
    }
    if (ad) {
      report.native = ad.report;
      const c = checkAdmet(plan, res, ad);
      report.admet = { plan, rows: c.rows, values: c.values, times: c.times, native: c.native ? { environment: c.native.environment, how: c.native.how, runs: Object.fromEntries(Object.entries(c.native.runs).map(([k, r]) => [k, { command: r.command, exit: r.exit, seconds: r.seconds }])) } : null };
      const arc = ad.report.archive;
      console.error(`input  archive ${arc.name}: ${arc.bytes} bytes, SHA-256 ${arc.sha256.slice(0, 12)}…${arc.pinned ? " (the pinned TDC ADMET group)" : ""}${arc.reproducible === null ? "" : arc.reproducible ? "; built twice to the same bytes" : "; NOT the same bytes when built twice"}`);
      if (arc.reproducible === false) report.ok = false;
      for (const r of c.rows) {
        console.error(`${TAG[r.status] || (r.ok ? "near  " : "DIFFER")} ${r.name}: ${r.why}`);
        if (!r.ok) report.ok = false;
      }
      for (const v of c.values) {
        console.error(`${v.ok ? "ok    " : "FAILED"} known answer (${v.who}'s cards): ${v.label}: ${v.detail}`);
        if (!v.ok) report.ok = false;
      }
      const refused = Object.entries(res.admet?.rechem || {}).map(([e, r]) => [e, r.train_val.refused.length + r.test.refused.length, r.train_val.n + r.test.n]);
      if (refused.length) {
        const total = refused.reduce((x, [, k]) => x + k, 0);
        console.error(`info   training molecules the browser chemistry refused: ${total} of ${refused.reduce((x, [, , m]) => x + m, 0)}${total ? ` (${refused.filter(([, k]) => k).map(([e, k, m]) => `${e} ${k}/${m}`).join(", ")})` : ""}`);
      }
      if (c.times) {
        const t = c.times;
        // a job worker's WebAssembly memory never shrinks: a worker the pipeline reuses still shows the build's peak
        const mib = (b) => (b ? `${(b / 2 ** 20).toFixed(0)} MiB` : "not reported");
        const sum = (o) => Object.values(o).reduce((x, y) => x + (Number(y) || 0), 0).toFixed(1);
        console.error(`time   admet.models: browser job ${t.models.browser_job_s ?? "?"} s (chemistry ${sum(t.models.chemistry_s)} s, training ${sum(t.models.train_s)} s over ${Object.keys(t.models.train_s).length} endpoints; worker memory ${mib(t.models.wasm_peak_bytes)}), native build ${t.models.native_s} s`);
        console.error(`time   pipeline.admet: browser job ${t.pipeline.browser_job_s ?? "?"} s (chemistry ${t.pipeline.chemistry_s ?? "?"} s, domain ${t.pipeline.domain_s ?? "?"} s, analysis ${t.pipeline.analysis_s ?? "?"} s; its worker's memory ${mib(t.pipeline.wasm_peak_bytes)}, which a reused worker carries over from the build), native command ${t.pipeline.native_s} s; forced GPU run ${t.forced.browser_job_s ?? "?"} s (domain ${t.forced.domain_s ?? "?"} s, software adapter: not evidence of speed)`);
      }
      const env = c.native?.environment || ad.report.environment || {};
      console.error(`native: Python ${env.python}, numpy ${env.numpy}, scipy ${env.scipy}, scikit-learn ${env.sklearn}, RDKit ${env.rdkit}, ${env.machine}, ${Object.entries(env.threads || {}).map(([k, v]) => `${k}=${v}`).join(" ")}${c.native ? `; models: ${c.native.how}` : ""}`);
      // the texts were compared and written out; the report keeps each file's digest and size
      for (const job of [res.admet?.pipeline, res.admet?.forced, res.admet?.models]) for (const f of Object.values(job?.files || {})) delete f.text;
      if (!res.admet?.models?.ok || !res.admet?.pipeline?.ok || !res.admet?.forced?.ok) report.ok = false;
    }
    if (dk) {
      report.native = dk.report;
      const c = checkDock(dp, res, dk);
      report.dock = { plan: dp, rows: c.rows, times: c.times, native: c.native ? { environment: c.native.environment, runs: Object.fromEntries(Object.entries(c.native.runs).map(([k, r]) => [k, { command: r.command, exit: r.exit, seconds: r.seconds, ...(r.checks ? { checks: r.checks } : {}) }])), scores: c.native.scores } : null };
      for (const [name, f] of Object.entries(dk.report.files || {})) console.error(`input  ${name}: ${f.bytes} bytes, SHA-256 ${f.sha256.slice(0, 12)}…`);
      for (const r of c.rows) {
        console.error(`${TAG[r.status] || (r.ok ? "near  " : "DIFFER")} ${r.name}: ${r.why}`);
        if (!r.ok) report.ok = false;
      }
      if (c.times) {
        const t = c.times;
        const sum = (xs) => xs.reduce((a, x) => a + (Number(x) || 0), 0);
        console.error(`time   pipeline.dock: browser job ${t.browser_job_s ?? "?"} s (${Object.entries(t.stages_s).map(([k, v]) => `${k} ${v ?? "?"} s`).join(", ")}); ${t.dock.runs} Vina runs, ${(sum(t.dock.run_ms) / 1000).toFixed(1)} s of Vina in all (the longest ${(Math.max(0, ...t.dock.run_ms) / 1000).toFixed(1)} s) on ${t.dock.workers ?? "?"} workers (${t.dock.why ?? "no reason recorded"}); the WebAssembly scores ${sum(t.wasm_score_ms)} ms; native: the runner's command ${t.native_runner_s} s, run_dock on the browser's results ${t.native_analysis_s} s, the scores ${sum(t.native_score_ms).toFixed(0)} ms`);
      }
      const env = c.native?.environment || dk.report.environment || {};
      console.error(`native: Python ${env.python}, numpy ${env.numpy}, vina ${env.vina}, ${env.machine}${env.cpu ? ` (${env.cpu}, ${env.cores} cores)` : ""}${env.glibc ? `, glibc ${env.glibc}` : ""}; browser: ${res.device?.cores ?? "?"} cores, ${res.device?.memory_gb ?? "?"} GB reported`);
      // the texts were compared and written out; the report keeps each file's digest and size
      const job = res.dock?.job;
      for (const group of [job?.files, job?.results, job?.plan]) for (const f of Object.values(group || {})) delete f.text;
      if (!job?.ok) report.ok = false;
    }
    if (args.mode === "gqd") {
      const nativeOut = path.resolve(args.nativeOut);
      // no result from the page: one row saying so, not an export each "not written in the browser"
      report.comparison = noResult(res) ? [{ name: "the exports", status: "missing", ok: false, why: noResult(res) }]
        : compareExports(res.files || {}, (name) => (existsSync(path.join(nativeOut, name)) ? readFileSync(path.join(nativeOut, name)) : null));
      for (const r of report.comparison) {
        console.error(`${["identical", "same_apart_from_execution", "same_as_json"].includes(r.status) ? "same  " : r.ok ? "near  " : "DIFFER"} ${r.name}: ${r.why}`);
        if (!r.ok) report.ok = false;
      }
      report.identical = report.comparison.every((r) => r.status === "identical" || r.status === "same_apart_from_execution" || r.name === "report.html");
    }
  } finally {
    await browser.close();
    srv.server.close();
  }
  fx?.cleanup();
  if (om && args.keep) console.error(`the omics inputs, native runs and the browser's outputs are kept in ${om.dir}`);
  else om?.cleanup();
  if (ad && args.keep) console.error(`the ADMET inputs, native runs and the browser's outputs are kept in ${ad.dir}`);
  else ad?.cleanup();
  if (dk && args.keep) console.error(`the docking inputs, native runs and the browser's outputs are kept in ${dk.dir}`);
  else dk?.cleanup();
  if (args.json) writeFileSync(args.json, JSON.stringify(report, null, 1));
  if (built && !args.keep) built.cleanup();
  console.error(report.ok ? `browser research jobs (${args.mode}): all checks passed` : `browser research jobs (${args.mode}): FAILED`);
  process.exit(report.ok ? 0 : 1);
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().catch((err) => {
    console.error(err?.stack || String(err));
    process.exit(2);
  });
}

