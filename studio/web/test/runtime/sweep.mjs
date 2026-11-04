#!/usr/bin/env node
// The browser sweep: every tool the browser runs, called in headless Chromium through BrowserRuntime as the router
// calls it (sweep.html, sweep-page.mjs), the same calls made natively as `python3 -m tcmstudio call … --where browser
// --no-network` makes them (sweep_native.py, one process), and each pair of envelopes held to the runtime check's own
// comparison (run.mjs compareEnvelopes). The calls are sweep_calls.py's: every browser entry (compatibility classes
// 1, 2, 3 and 5) and core tool with the input `tcmstudio validate` gives it, every connector with web access off
// (both sides must refuse it as network_off), and the groups beyond validate's inputs (extra, invalid, reference,
// direct, kernel, and the workbook sequence on a page of its own); its docstring says what each is.
//
//   node studio/web/test/runtime/sweep.mjs [--site DIR] [--pyodide-dir DIR | --index URL] [--chromium PATH]
//        [--groups entry,core,…] [--keys k1,k2] [--timeout MS] [--json FILE] [--keep-envelopes DIR] [--keep]
//
// Without --site the site is built (build_web.py --dev) into a temporary directory; a given site must be a --dev build
// (it serves the sweep's page). It is served by run.mjs's server with the site's own _headers. Pyodide comes from
// boot.json's index_url (jsDelivr) unless --pyodide-dir serves a local copy at /pyodide/. Each sequence runs on a
// page of its own (a new browser context: no storage from another) under its own project, and natively in its own
// state root; a page that stops making progress is replaced and the list resumed after the call it was stuck on.
//
// The two sides may differ only as KNOWN declares, by entry id and field, with the reason: any other difference, a
// call that failed on one side only or did not finish, or a connector not refused as network_off on both sides fails
// the sweep (exit 1). Exit 2 when it could not run.

import { spawnSync } from "node:child_process";
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { RELATIVE, compareJson, compareText } from "./compare.mjs";
import { buildSite, compareEnvelopes, loadPlaywright, serve, stable } from "./run.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const PYTHON = process.env.PYTHON || "python3";

/** The browser runtime's state root (pyodide.worker.js STATE_ROOT_DEFAULT); natively it is a temporary directory. */
export const BROWSER_STATE_ROOT = "/persist";
const STATE = "<state root>";
const MARK = "<set aside>";

const STATE_ROOT = "the project's state root is in the result: /persist in the browser, the native run's temporary "
  + "directory natively; compared with each side's root written <state root>";
const HOST_FACTS = {
  "result.runtime": "the runtime string names the interpreter (Pyodide's CPython 3.14 in the browser, the host's natively)",
  "result.versions.python": "the interpreter's version",
  "result.dependencies": "which optional packages import here: the host's own installation natively; in the browser "
    + "Pyodide loads numpy, scipy and pandas on first use and the engines are runner-only, so they read false",
  "result.formulas.workbook": "the complete formula workbook: available natively, loaded on demand in the browser "
    + "(the page fetches it, checked by SHA-256, with the first formula, herb or syndrome lookup)",
  "result.host": "the browser's own device report (cores, memory, WebGPU, WebNN), which only the browser has",
};
const RUN_SCOPED = {
  "result.manifest.started": "the run's own start time",
  "result.manifest.finished": "the run's own end time",
  "result.manifest.python": "the interpreter's version, recorded in the session manifest",
  "result.session_id": "each assessment opens a session of its own",
};

/**
 * The differences the sweep expects, by entry id (the core tool's name for a core tool), wherever that entry is
 * called. Each says what is set aside and why; anything else must be identical (run.mjs compareEnvelopes):
 *   roots   each side's state root written <state root> in every string of the envelope
 *   leaves  {path: why}: these values set aside on both sides ("a.b"; "outputs[NAME]" the element whose path is NAME),
 *           and a string value set aside in `text` too; an output whose sha256 is set aside is compared by its content
 *   masks   [[pattern, why]]: what the pattern (global) matches in the summary and the text set aside on both sides
 *   text    "head": the text compared up to its "Result:" line; what follows is the result's JSON fitted to a budget
 *           (tcmstudio.envelope), compared through `result` leaf by leaf
 *   numbers [tolerance, why]: the numbers of result and text compared within that relative tolerance, the rest exactly
 * receipt.output_sha256 hashes the result as written: it is set aside exactly when the result differs only as declared.
 * A declaration not observed in a run is reported, not failed.
 */
export const KNOWN = Object.freeze({
  "native.hk_cmm_dna_lookup": { roots: STATE_ROOT },
  "native.hkbu_formula_lookup": { roots: STATE_ROOT },
  "native.hkcmms_standard_lookup": { roots: STATE_ROOT },
  "tcmdb.status": { roots: STATE_ROOT },
  "system.audit_verify": {
    roots: STATE_ROOT,
    leaves: { "result.head_hash": "each record carries its own time and run id, so the chain's head hash differs on every run (natively too); the records, the events by kind and intact are compared" },
  },
  "system.capabilities": { roots: STATE_ROOT, leaves: HOST_FACTS, text: "head" },
  "capabilities_status": { roots: STATE_ROOT, leaves: HOST_FACTS, text: "head" },
  "system.capability_audit": {
    // BS-06: by design and disclosed in the envelope's limitations; the site's catalog.json is built where the record is
    masks: [
      [/已提交核验记录为通过的 \d+ 项/g, "how many entries the committed connector live-verification record (BioScience-Harness/data/connector_live_verification.csv) passes: the browser runtime's bundle does not carry the record, so its 444 connectors read UNVERIFIED there"],
      [/\n- This runtime does not carry the committed connector verification record[^\n]*/g, "the limitation that says so, which only the browser runtime's envelope has"],
    ],
    leaves: {
      "result.counts.by_verification": "the connectors' verification, read from the record the bundle does not carry",
      "result.counts.by_implementation": "an entry is 'tested' when its verification passes: the same record",
    },
    text: "head",
  },
  // BS-04, measured on its runner-test input: 11 numbers, at most 2.8e-14 relative (the interaction's lower confidence
  // limit), 1.2e-15 absolute; study.contrast_formula_monomer, also numpy, is identical and declares nothing
  "study.effect_modification": {
    numbers: [RELATIVE, "numpy.linalg.lstsq and inv (OpenBLAS natively, Pyodide's numpy in the browser) differ in the last bits of float64"],
  },
  "clinic.assess": { leaves: RUN_SCOPED },
  "clinic_assess": { leaves: RUN_SCOPED },
  "skill.draft-tcm-prescription": {
    leaves: {
      "governance.outputs[clinic_summary.json].sha256": "the file records its own run directory (out_dir), so its bytes differ on every run, natively too",
      "governance.outputs[clinic_summary.json].bytes": "the same file's size",
      "governance.outputs[clinic_summary.json].content.out_dir": "the run's own directory (its run id)",
      "governance.artifact.composite_version.source": "the source cards carry their snapshot_at times, so this digest differs between two native runs too (the skill is unpinned and never released on either side)",
    },
  },
});

// ------------------------------------------------------------------------------------------------- the rule

const clone = (v) => (v === undefined ? undefined : JSON.parse(JSON.stringify(v)));
const CAP = 4_000;
function capped(v) {
  const s = JSON.stringify(v ?? null);
  return s.length <= CAP ? v : { truncated: true, chars: s.length, head: s.slice(0, CAP) };
}
function strings(v, fn) {
  if (typeof v === "string") return fn(v);
  if (Array.isArray(v)) return v.map((x) => strings(x, fn));
  if (v && typeof v === "object") return Object.fromEntries(Object.entries(v).map(([k, x]) => [k, strings(x, fn)]));
  return v;
}

/** A declared path ("a.b", "outputs[NAME]": the element whose path is NAME) in `root` → {parent, key, value} (value
 * undefined when the last key is absent), or null when what holds it is absent. */
export function locate(root, spec) {
  let parent = null;
  let key = null;
  let cur = root;
  for (const t of spec.match(/\[[^\]]*\]|[^.[\]]+/g)) {
    if (cur === null || typeof cur !== "object") return null;
    parent = cur;
    if (t.startsWith("[")) {
      key = Array.isArray(cur) ? cur.findIndex((x) => x?.path === t.slice(1, -1)) : -1;
      if (key < 0) return null;
    } else key = t;
    cur = parent[key];
  }
  return parent ? { parent, key, value: cur } : null;
}

/** The largest relative difference between the numbers of two JSON values of the same shape. */
function largestRelative(a, b) {
  if (typeof a === "number" && typeof b === "number") return a === b ? 0 : Math.abs(a - b) / Math.max(Math.abs(a), Math.abs(b));
  if (a && b && typeof a === "object" && typeof b === "object") return Math.max(0, ...Object.keys(a).map((k) => largestRelative(a[k], b[k])));
  return 0;
}

/**
 * The browser's and the native envelope of one call under its declaration (KNOWN[id], or none) → {diffs: the fields
 * that differ and are not declared [{field, browser, native}], declared: what differed as declared [{field, why,
 * browser?, native?}]}. `roots` = {browser, native}: each side's state root.
 */
export function compareDeclared(id, b, n, roots = { browser: BROWSER_STATE_ROOT, native: null }) {
  const rule = KNOWN[id];
  if (!rule) return { diffs: compareEnvelopes(b, n), declared: [] };
  let B = clone(b);
  let N = clone(n);
  const declared = [];
  if (rule.roots && roots.native) {
    const at = (root) => (s) => s.split(`${root}/`).join(`${STATE}/`);
    if (stable(B).includes(`${roots.browser}/`) || stable(N).includes(`${roots.native}/`)) declared.push({ field: "the state root", why: rule.roots });
    B = strings(B, at(roots.browser));
    N = strings(N, at(roots.native));
  }
  const contentOf = [];
  for (const [spec, why] of Object.entries(rule.leaves || {})) {
    const lb = locate(B, spec);
    const ln = locate(N, spec);
    if (stable(lb?.value) === stable(ln?.value)) continue;
    declared.push({ field: spec, why, browser: capped(lb?.value ?? null), native: capped(ln?.value ?? null) });
    for (const [l, e] of [[lb, B], [ln, N]]) {
      if (!l) continue;
      if (typeof l.value === "string" && l.value && typeof e.text === "string") e.text = e.text.split(l.value).join(MARK);
      l.parent[l.key] = MARK;
    }
    const output = spec.match(/^(governance\.outputs\[[^\]]+\])\.sha256$/);
    if (output) contentOf.push(output[1]);
  }
  for (const [pattern, why] of rule.masks || []) {
    const matched = (e) => ["summary", "text"].map((f) => (typeof e[f] === "string" ? e[f].match(pattern) || [] : []));
    if (stable(matched(B)) !== stable(matched(N))) declared.push({ field: `summary and text: ${pattern.source}`, why, browser: matched(B).flat(), native: matched(N).flat() });
    for (const e of [B, N]) for (const f of ["summary", "text"]) if (typeof e[f] === "string") e[f] = e[f].replace(pattern, "");
  }
  if (rule.text === "head") {
    const head = (t) => (typeof t === "string" ? t.split("\nResult: ")[0] : t);
    if (B.text !== N.text && head(B.text) === head(N.text)) declared.push({ field: "text after its Result: line", why: "the result's JSON fitted to a budget, compared leaf by leaf through result" });
    B.text = head(B.text);
    N.text = head(N.text);
  }
  let diffs = compareEnvelopes(B, N);
  for (const spec of contentOf) {
    const cb = locate(B, `${spec}.content`)?.value;
    const cn = locate(N, `${spec}.content`)?.value;
    if (stable(cb) !== stable(cn)) diffs.push({ field: `${spec}.content`, browser: cb, native: cn });
  }
  if (rule.numbers && diffs.some((d) => d.field === "result" || d.field === "text")) {
    const [tolerance, why] = rule.numbers;
    const near = (x, y) => x === y || Math.abs(x - y) / Math.max(Math.abs(x), Math.abs(y)) <= tolerance;
    if (compareJson(B.result, N.result, [], tolerance).same && (typeof B.text !== "string" || compareText(B.text, N.text, near).same)) {
      declared.push({ field: "the numbers of result and text", why: `${why}: within ${tolerance} relative`, largest_relative: largestRelative(B.result, N.result) });
      diffs = diffs.filter((d) => d.field !== "result" && d.field !== "text");
    }
  }
  if (diffs.some((d) => d.field === "receipt.output_sha256") && !diffs.some((d) => d.field === "result") && stable(b.result) !== stable(n.result)) {
    declared.push({ field: "receipt.output_sha256", why: "the result's digest: it differs because the result does, as declared" });
    diffs = diffs.filter((d) => d.field !== "receipt.output_sha256");
  }
  return { diffs, declared };
}

function failed(env) {
  return env?.status === "failed" && ["runtime_error", "unavailable", "timeout", "fatal"].includes(env?.error?.type);
}

/** One call: its browser record ({envelope | timeout | threw, ms}) and native record ({envelope_json | error, ms}) →
 * the report's row; row.ok is what the verdict counts. */
export function judge(call, b, n, roots) {
  const row = { key: call.key, group: call.group, id: call.id, tool: call.tool, input: call.input, args: capped(call.args), browser_ms: b?.ms ?? null, native_ms: n?.ms ?? null };
  const benv = b?.envelope ?? null;
  let nenv = null;
  if (n?.envelope_json) {
    try { nenv = JSON.parse(n.envelope_json); } catch (err) { row.native_error = `not JSON: ${err.message}`; }
  } else if (n) row.native_error = n.error || null;
  row.status = { browser: benv?.status ?? null, native: nenv?.status ?? null };
  row.error = { browser: benv?.error?.type ?? (b?.threw ? `threw: ${String(b.threw).slice(0, 300)}` : null), native: nenv?.error?.type ?? null };
  if (!b || !n) row.result = "not_run";
  else if (b.timeout || n.timeout || benv?.error?.type === "timeout" || nenv?.error?.type === "timeout") row.result = "timeout";
  else if (!benv) row.result = "browser_failure";
  else if (!nenv) row.result = "native_failure";
  if (benv && nenv) {
    const c = compareDeclared(call.id, benv, nenv, roots);
    row.diffs = c.diffs.map((d) => ({ field: d.field, browser: capped(d.browser), native: capped(d.native) }));
    row.declared = c.declared;
    if (!row.result) {
      if (failed(benv) && !failed(nenv)) row.result = "browser_failure";
      else if (failed(nenv) && !failed(benv)) row.result = "native_failure";
      else row.result = c.diffs.length ? "differs" : c.declared.length ? "declared" : "same";
    }
  }
  if (call.group === "connector") {
    const off = (e) => e?.status === "failed" && e?.error?.type === "network_off";
    row.network_off = { browser: off(benv), native: off(nenv) };
    if (["same", "declared"].includes(row.result) && !(row.network_off.browser && row.network_off.native)) row.result = "not_network_off";
  }
  row.ok = ["same", "declared"].includes(row.result);
  return row;
}

// ------------------------------------------------------------------------------------------------- the browser

/** One sequence on a page of its own (a new context), resumed after a call that hung or crashed the page → records. */
async function browserSequence(browser, origin, seq, { project, timeout, index, log }) {
  const calls = seq.calls;
  const url = `${origin}/test/runtime/sweep.html?project=${encodeURIComponent(project)}&timeout=${timeout}${index ? `&index=${encodeURIComponent(index)}` : ""}`;
  const records = new Array(calls.length).fill(null);
  const pages = [];
  const consoleLines = [];
  const context = await browser.newContext();
  try {
    let next = 0;
    while (next < calls.length) {
      const page = await context.newPage();
      page.on("console", (m) => { if (["error", "warning"].includes(m.type()) && consoleLines.length < 100) consoleLines.push(`${m.type()}: ${m.text()}`.slice(0, 400)); });
      page.on("pageerror", (e) => { if (consoleLines.length < 100) consoleLines.push(`pageerror: ${String(e)}`.slice(0, 400)); });
      const t0 = Date.now();
      await page.goto(url);
      await page.waitForFunction(() => globalThis.__sweep, null, { timeout: 60_000 });
      await page.evaluate(([list, from, ms]) => globalThis.__sweep.start(list, { timeoutMs: ms, from }), [calls, next, timeout]);
      let taken = 0;
      let lastProgress = Date.now();
      let st;
      let booted = false;
      for (;;) {
        await new Promise((r) => setTimeout(r, 250));
        st = await page.evaluate(() => globalThis.__sweep.state()).catch((err) => ({ crashed: String(err) }));
        if (st.crashed) break;
        booted ||= st.booted;
        const got = await page.evaluate((from) => globalThis.__sweep.take(from), taken);
        for (const r of got) {
          records[r.index] = r;
          if (!r.envelope) log(`  browser ${r.key}: ${r.timeout ? "did not finish" : `threw ${String(r.threw).slice(0, 200)}`}`);
          if ((r.index + 1) % 200 === 0) log(`  browser ${seq.name}: ${r.index + 1}/${calls.length}`);
        }
        if (got.length) lastProgress = Date.now();
        taken += got.length;
        if (st.done) break;
        // the page's own timer stops a call after `timeout` (+5 s): a page that makes no progress for far longer is stuck
        if (Date.now() - lastProgress > timeout + 60_000) { st = { ...st, stuck: true }; break; }
      }
      const done = st.next ?? next + taken;
      pages.push({ from: next, to: done, ms: Date.now() - t0, boot: st.boot ?? null, error: st.error ?? st.crashed ?? null, stuck: Boolean(st.stuck) });
      if (!booted && (st.error || st.crashed)) {
        // the runtime did not start: no later page would either, so every call left is recorded as not made
        log(`  browser ${seq.name}: the runtime did not start: ${String(st.error || st.crashed).slice(0, 300)}`);
        for (let i = next; i < calls.length; i++) records[i] ||= { key: calls[i].key, index: i, threw: `the runtime did not start: ${st.error || st.crashed}`, ms: 0 };
        await page.close().catch(() => {});
        break;
      }
      if (st.stuck || st.crashed || st.error) {
        const at = Math.max(done, next);
        if (at < calls.length && !records[at]) records[at] = { key: calls[at].key, index: at, timeout: Boolean(st.stuck), threw: st.crashed || st.error || null, ms: Date.now() - lastProgress };
        next = at + 1;
      } else next = calls.length;
      await page.close().catch(() => {});
    }
  } finally {
    await context.close();
  }
  return { records, pages, console: consoleLines };
}

// -------------------------------------------------------------------------------------------------- native side

/** One sequence natively, in a state root of its own → {byKey: Map(key → record), root}. */
function nativeSequence(seq, { project, timeoutS, log }) {
  const root = mkdtempSync(path.join(tmpdir(), "tcmstudio-sweep-native-"));
  const work = mkdtempSync(path.join(tmpdir(), "tcmstudio-sweep-work-"));
  const out = path.join(work, "native.jsonl");
  const byKey = new Map();
  try {
    // the arguments as JavaScript writes them, as the page hands them to the runtime and run.mjs to the CLI
    const callsFile = path.join(work, "calls.json");
    writeFileSync(callsFile, JSON.stringify({ calls: seq.calls.map(({ key, tool, args }) => ({ key, tool, args })) }));
    const r = spawnSync(PYTHON, [path.join(HERE, "sweep_native.py"), "--calls", callsFile, "--state-root", root, "--project", project, "--out", out, "--timeout", String(timeoutS)],
      { stdio: ["ignore", "inherit", "inherit"] });
    if (r.status !== 0) log(`native ${seq.name}: sweep_native.py exited ${r.status ?? r.signal}`);
    if (existsSync(out)) for (const line of readFileSync(out, "utf8").split("\n")) if (line.trim()) { const rec = JSON.parse(line); byKey.set(rec.key, rec); }
  } finally {
    rmSync(root, { recursive: true, force: true });
    rmSync(work, { recursive: true, force: true });
  }
  return { byKey, root };
}

// ---------------------------------------------------------------------------------------------------------- main

function parseArgs(argv) {
  const a = { timeout: 120_000, keep: false };
  for (let i = 0; i < argv.length; i++) {
    const k = argv[i];
    const v = () => argv[++i];
    if (k === "--site") a.site = v();
    else if (k === "--pyodide-dir") a.pyodideDir = v();
    else if (k === "--index") a.index = v();
    else if (k === "--chromium") a.chromium = v();
    else if (k === "--groups") a.groups = v();
    else if (k === "--keys") a.keys = new Set(v().split(",").filter(Boolean));
    else if (k === "--timeout") a.timeout = Number(v());
    else if (k === "--json") a.json = v();
    else if (k === "--keep-envelopes") a.keepEnvelopes = v();
    else if (k === "--keep") a.keep = true;
    else if (k === "--help" || k === "-h") { console.log(readFileSync(fileURLToPath(import.meta.url), "utf8").split("\n").slice(1, 21).join("\n")); process.exit(0); }
    else throw new Error(`unknown argument ${k}`);
  }
  return a;
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const t0 = Date.now();
  const log = (s) => console.error(s);
  const built = args.site ? null : buildSite();
  const site = path.resolve(args.site || built.out);
  if (!existsSync(path.join(site, "test", "runtime", "sweep.html"))) throw new Error(`${site} has no test pages: build it with build_web.py --dev`);
  const pyodideDir = args.pyodideDir ? path.resolve(args.pyodideDir) : null;
  const index = args.index || (pyodideDir ? "/pyodide/" : "");
  const list = spawnSync(PYTHON, [path.join(HERE, "sweep_calls.py"), "--site", site, ...(args.groups ? ["--groups", args.groups] : [])],
    { encoding: "utf8", maxBuffer: 256 * 1024 * 1024 });
  if (list.status !== 0) throw new Error(`sweep_calls.py failed: ${list.stderr.slice(-2000)}`);
  const doc = JSON.parse(list.stdout);
  const sequences = doc.sequences.map((s) => ({ ...s, calls: args.keys ? s.calls.filter((c) => args.keys.has(c.key)) : s.calls })).filter((s) => s.calls.length);
  const report = { site, index: index || JSON.parse(readFileSync(path.join(site, "runtime", "boot.json"), "utf8")).pyodide.index_url, timeout_ms: args.timeout, sequences: [], rows: [], counts: {}, ok: true };
  log(`sweep: ${sequences.map((s) => `${s.name} ${s.calls.length} calls`).join(", ")} (${Object.entries(doc.counts.calls).map(([g, c]) => `${g} ${c}`).join(", ")}; ${doc.counts.no_input} without an input)`);

  const iso = await serve({ site, isolated: true, pyodideDir });
  const { chromium } = await loadPlaywright();
  const proxy = !pyodideDir && (process.env.HTTPS_PROXY || process.env.https_proxy);
  const browser = await chromium.launch({
    executablePath: args.chromium || process.env.CHROMIUM_PATH || undefined,
    args: [...(proxy ? [`--proxy-server=${proxy}`, "--proxy-bypass-list=127.0.0.1;localhost"] : []), "--enable-unsafe-webgpu"],
  });
  try {
    for (const seq of sequences) {
      const project = `sweep-${seq.name}-${Date.now().toString(36)}`;
      const tb = Date.now();
      const bside = await browserSequence(browser, iso.origin, seq, { project, timeout: args.timeout, index, log });
      const browserMs = Date.now() - tb;
      const tn = Date.now();
      const nside = nativeSequence(seq, { project, timeoutS: Math.ceil(args.timeout / 1000), log });
      const nativeMs = Date.now() - tn;
      const roots = { browser: BROWSER_STATE_ROOT, native: nside.root };
      const rows = seq.calls.map((c, i) => ({ sequence: seq.name, ...judge(c, bside.records[i], nside.byKey.get(c.key) || null, roots) }));
      report.rows.push(...rows);
      report.sequences.push({ name: seq.name, project, calls: seq.calls.length, browser_ms: browserMs, native_ms: nativeMs, pages: bside.pages, console: bside.console, no_input: seq.no_input });
      log(`${seq.name}: browser ${(browserMs / 1000).toFixed(1)} s (boot ${bside.pages[0]?.boot?.wall_ms ?? "?"} ms), native ${(nativeMs / 1000).toFixed(1)} s`);
      if (args.keepEnvelopes) {
        mkdirSync(args.keepEnvelopes, { recursive: true });
        writeFileSync(path.join(args.keepEnvelopes, `${seq.name}.browser.jsonl`), bside.records.filter(Boolean).map((r) => JSON.stringify(r)).join("\n") + "\n");
        writeFileSync(path.join(args.keepEnvelopes, `${seq.name}.native.jsonl`), [...nside.byKey.values()].map((r) => JSON.stringify(r)).join("\n") + "\n");
      }
    }
  } finally {
    await browser.close();
    iso.server.close();
  }

  for (const r of report.rows) {
    report.counts[r.group] ||= {};
    report.counts[r.group][r.result] = (report.counts[r.group][r.result] || 0) + 1;
  }
  for (const [g, c] of Object.entries(report.counts)) log(`${g}: ${Object.entries(c).map(([k, v]) => `${k} ${v}`).join(", ")}`);
  // what differed as declared: per entry, the calls and each field with its reason
  const seen = new Map();
  for (const r of report.rows) {
    if (!r.declared?.length) continue;
    if (!seen.has(r.id)) seen.set(r.id, { calls: [], fields: new Map() });
    seen.get(r.id).calls.push(r.key);
    for (const d of r.declared) seen.get(r.id).fields.set(d.field, d.largest_relative === undefined ? d.why : `${d.why}; the largest here ${d.largest_relative.toExponential(2)}`);
  }
  report.declared = [...seen].map(([id, v]) => ({ id, calls: v.calls, fields: Object.fromEntries(v.fields) }));
  if (seen.size) log("differences declared in KNOWN:");
  for (const [id, v] of seen) {
    log(`  ${id} (${v.calls.join(", ")})`);
    for (const [field, why] of v.fields) log(`    ${field}: ${why}`);
  }
  const ids = new Set(report.rows.map((r) => r.id));
  report.not_observed = Object.keys(KNOWN).filter((id) => !report.rows.some((r) => r.id === id && r.declared?.length)).map((id) => ({ id, called: ids.has(id) }));
  for (const x of report.not_observed) log(`  declared but not observed: ${x.id}${x.called ? "" : " (not called in this sweep)"}`);
  const bad = report.rows.filter((r) => !r.ok);
  for (const r of bad) log(`FAILED ${r.result}: ${r.key}${r.diffs?.length ? ` [${r.diffs.map((d) => d.field).join(", ")}]` : ""}${r.error?.browser || r.error?.native ? ` (errors: browser ${r.error.browser}, native ${r.error.native})` : ""}${r.native_error ? ` (native: ${String(r.native_error).slice(0, 200)})` : ""}`);
  report.ok = bad.length === 0;
  report.total_ms = Date.now() - t0;
  const noInput = report.sequences.flatMap((s) => s.no_input || []);
  log(`${report.rows.length} calls compared (${report.rows.filter((r) => r.result === "same").length} identical, ${report.rows.filter((r) => r.result === "declared").length} differing only as declared, ${bad.length} failed); ${noInput.length} without an input (${noInput.filter((x) => !x.extra).length} not called in any group); ${(report.total_ms / 1000).toFixed(1)} s`);
  if (args.json) writeFileSync(args.json, JSON.stringify(report, null, 1));
  if (built && !args.keep) built.cleanup();
  log(report.ok ? "browser sweep: every envelope the native one, or different only as declared" : "browser sweep: FAILED");
  process.exit(report.ok ? 0 : 1);
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().catch((err) => {
    console.error(err?.stack || String(err));
    process.exit(2);
  });
}
