#!/usr/bin/env node
// Runs browser research jobs (studio/docs/BROWSER_JOBS.md) in headless Chromium and compares what they wrote with
// native CPython.
//
//   node studio/web/test/runtime/science.mjs [--mode selftest|gqd] [--site DIR] [--pyodide-dir DIR] [--chromium PATH]
//        [--raw DIR --native-out DIR] [--json FILE] [--keep] [--timeout-min N]
//
// selftest  the planted-world known answer (tcmstudio.browser_selftest) in a job worker: no download, a few
//           seconds of CPU; its tables must equal the committed answer native CPython wrote
// service   the job service end to end on the planted world: research.run and the self-test as browser jobs,
//           checkpoints, receipts and their digests
// fixture   the snapshot build on files mounted from OPFS: the parser tests' fixture files, built in a job worker and
//           natively with the same checksums put in place of the reviewed ones; the snapshot ids must be equal
// gqd       the 葛根芩连汤 research case on real files: the raw files in --raw (as the live-data workflow downloaded
//           them) are imported into the page's private storage, research.snapshot builds the snapshots and
//           research.run studies the formula, both through the browser job service; every export is then compared
//           with the native run's exports in --native-out (`bioagent research … --out DIR` on the same files)
//
// Without --site the site is built first (build_web.py --dev). Pyodide comes from boot.json's index_url (jsDelivr)
// unless --pyodide-dir serves a copy at /pyodide/. Exit 0 only when every check passed.

import { spawnSync } from "node:child_process";
import { createReadStream, existsSync, mkdtempSync, readFileSync, readdirSync, rmSync, statSync, writeFileSync } from "node:fs";
import { createServer } from "node:http";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { compareExports } from "./compare.mjs";
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
    else if (k === "--help" || k === "-h") { console.log(readFileSync(fileURLToPath(import.meta.url), "utf8").split("\n").slice(1, 18).join("\n")); process.exit(0); }
    else throw new Error(`unknown argument ${k}`);
  }
  if (!["selftest", "service", "fixture", "gqd"].includes(a.mode)) throw new Error("--mode is selftest, service, fixture or gqd");
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
  const srv = await serve({ site, pyodideDir, raw: fx ? fx.raw : args.raw ? path.resolve(args.raw) : null });
  const { chromium } = await loadPlaywright();
  const proxy = !pyodideDir && (process.env.HTTPS_PROXY || process.env.https_proxy);
  const browser = await chromium.launch({
    executablePath: args.chromium || process.env.CHROMIUM_PATH || undefined,
    args: proxy ? [`--proxy-server=${proxy}`, "--proxy-bypass-list=127.0.0.1;localhost"] : [],
  });
  const timeout = (args.timeoutMin || (args.mode === "gqd" ? 90 : 10)) * 60_000;
  try {
    const ctx = await browser.newContext();
    const q = `?mode=${args.mode}${index ? `&index=${encodeURIComponent(index)}` : ""}`;
    const res = await runPage(ctx, `${srv.origin}/test/runtime/science.html${q}`, timeout);
    report.page = res;
    if (!res.ok) report.ok = false;
    for (const c of res.checks || []) console.error(`${c.ok ? "ok    " : "FAILED"} ${c.label}${c.detail ? `: ${c.detail}` : ""}`);
    if (fx) {
      report.comparison = [...compareFixture(res.report, fx.report), ...compareFixture(res.service_report, fx.report, "research.snapshot job: ")];
      for (const r of report.comparison) {
        console.error(`${r.same ? "same  " : "DIFFER"} ${r.name}: ${r.why}`);
        if (!r.same) report.ok = false;
      }
      if (!res.report || !res.service_report) report.ok = false;
    }
    if (args.mode === "gqd") {
      const nativeOut = path.resolve(args.nativeOut);
      report.comparison = compareExports(res.files || {}, (name) => (existsSync(path.join(nativeOut, name)) ? readFileSync(path.join(nativeOut, name)) : null));
      for (const r of report.comparison) {
        console.error(`${r.status === "identical" ? "same  " : r.ok ? "near  " : "DIFFER"} ${r.name}: ${r.why}`);
        if (!r.ok) report.ok = false;
      }
      report.identical = report.comparison.every((r) => r.status === "identical" || r.status === "same_apart_from_execution" || r.name === "report.html");
    }
  } finally {
    await browser.close();
    srv.server.close();
  }
  fx?.cleanup();
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

