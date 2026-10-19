#!/usr/bin/env node
// Runs browser research jobs (studio/docs/BROWSER_JOBS.md) in headless Chromium and compares what they wrote with
// native CPython.
//
//   node studio/web/test/runtime/science.mjs [--mode selftest|service|fixture|omics|gqd] [--site DIR] [--pyodide-dir DIR]
//        [--chromium PATH] [--raw DIR --native-out DIR] [--json FILE] [--keep] [--timeout-min N]
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
// gqd       the 葛根芩连汤 research case on real files: the raw files in --raw (as the live-data workflow downloaded
//           them) are imported into the page's private storage, research.snapshot builds the snapshots and
//           research.run studies the formula, both through the browser job service; every export is then compared
//           with the native run's exports in --native-out (`bioagent research … --out DIR` on the same files)
//
// Without --site the site is built first (build_web.py --dev). Pyodide comes from boot.json's index_url (jsDelivr)
// unless --pyodide-dir serves a copy at /pyodide/. $PYTHON (default python3) builds the site and runs the native side:
// it needs bioagent and tcmstudio installed, and pytest for fixture and omics. Exit 0 only when every check passed.
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
import { compareExports, compareOmics } from "./compare.mjs";
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

/** Why a job's receipt and outputs are not there, when they are not: no result from the page, no such job in its
 * result, or a job that did not succeed → text | null. */
export function lostJob(res, job, kind) {
  if (noResult(res)) return noResult(res);
  if (!job) return `the page's result has no ${kind} job (its checks say why)`;
  if (!job.files) return `the ${kind} job ended ${job.state ?? "without a state"} in the browser${job.error?.message ? ` (${job.error.message})` : ""}; its outputs were not read back`;
  return null;
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

const TAG = { identical: "same  ", same: "same  ", same_apart_from_excluded: "same  ", same_apart_from_figures: "same  ", structure_held: "same  ", within_tolerance: "near  ", flips_near_alpha: "near  ", not_compared: "skip  " };

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
    else if (k === "--json") a.json = v();
    else if (k === "--keep") a.keep = true;
    else if (k === "--timeout-min") a.timeoutMin = Number(v());
    else if (k === "--help" || k === "-h") { console.log(readFileSync(fileURLToPath(import.meta.url), "utf8").split("\n\n")[0].split("\n").slice(1).join("\n")); process.exit(0); }
    else throw new Error(`unknown argument ${k}`);
  }
  if (!["selftest", "service", "fixture", "omics", "gqd"].includes(a.mode)) throw new Error("--mode is selftest, service, fixture, omics or gqd");
  if (a.mode === "gqd" && (!a.raw || !a.nativeOut)) throw new Error("--mode gqd needs --raw DIR and --native-out DIR");
  return a;
}

/** The built site with its _headers, a Pyodide copy at /pyodide/ and the raw files at /raw/ (streamed, not read whole). */
function serve({ site, pyodideDir, raw }) {
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
  const srv = await serve({ site, pyodideDir, raw: fx ? fx.raw : om ? om.raw : args.raw ? path.resolve(args.raw) : null });
  const { chromium } = await loadPlaywright();
  const proxy = !pyodideDir && (process.env.HTTPS_PROXY || process.env.https_proxy);
  const browser = await chromium.launch({
    executablePath: args.chromium || process.env.CHROMIUM_PATH || undefined,
    args: proxy ? [`--proxy-server=${proxy}`, "--proxy-bypass-list=127.0.0.1;localhost"] : [],
  });
  const timeout = (args.timeoutMin || (args.mode === "gqd" ? 90 : args.mode === "omics" ? 20 : 10)) * 60_000;
  try {
    const ctx = await browser.newContext();
    const q = `?mode=${args.mode}${index ? `&index=${encodeURIComponent(index)}` : ""}`;
    const res = await runPage(ctx, `${srv.origin}/test/runtime/science.html${q}`, timeout);
    report.page = res;
    if (!res.ok) report.ok = false;
    for (const c of res.checks || []) console.error(`${c.ok ? "ok    " : "FAILED"} ${c.label}${c.detail ? `: ${c.detail}` : ""}`);
    for (const line of pageLines(res)) console.error(line);
    if (fx) {
      report.comparison = [...compareFixture(res.report, fx.report), ...compareFixture(res.service_report, fx.report, "research.snapshot job: ")];
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

