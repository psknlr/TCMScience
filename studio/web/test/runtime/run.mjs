#!/usr/bin/env node
// Runs the browser runtime check (index.html + check.mjs) in headless Chromium and compares its envelopes with the
// same calls made natively (`python3 -m tcmstudio call … --where browser`).
//
//   node studio/web/test/runtime/run.mjs [--site DIR] [--chromium PATH] [--pyodide-dir DIR | --index URL]
//                                        [--modes full,reload,nonisolated,tamper] [--sci] [--json FILE] [--keep]
//
// Without --site the site is built first (studio/scripts/build_web.py --dev) into a temporary directory. The site is
// served on ephemeral loopback ports by a small static server that applies the site's own _headers (COOP/COEP, CSP),
// plus a second server without them for the not-isolated case. Pyodide comes from boot.json's index_url (jsDelivr)
// unless --pyodide-dir serves a local copy at /pyodide/. Playwright is taken from node_modules, $PLAYWRIGHT_MODULE, or
// a global install; Chromium from --chromium or $CHROMIUM_PATH, else Playwright's own.

import { spawnSync } from "node:child_process";
import { createServer } from "node:http";
import { mkdtempSync, readFileSync, rmSync, writeFileSync, existsSync, statSync } from "node:fs";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const STUDIO = path.resolve(HERE, "../../..");
const PYTHON = process.env.PYTHON || "python3";

const MIME = {
  ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".mjs": "text/javascript; charset=utf-8",
  ".json": "application/json; charset=utf-8", ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml",
  ".png": "image/png", ".webmanifest": "application/manifest+json", ".woff2": "font/woff2", ".wasm": "application/wasm",
  ".gz": "application/gzip", ".zip": "application/zip", ".whl": "application/zip", ".txt": "text/plain; charset=utf-8",
  ".md": "text/markdown; charset=utf-8",
};

function parseArgs(argv) {
  const a = { modes: ["full", "reload", "nonisolated", "tamper"], sci: false, keep: false };
  for (let i = 0; i < argv.length; i++) {
    const k = argv[i];
    const v = () => argv[++i];
    if (k === "--site") a.site = v();
    else if (k === "--chromium") a.chromium = v();
    else if (k === "--pyodide-dir") a.pyodideDir = v();
    else if (k === "--index") a.index = v();
    else if (k === "--modes") a.modes = v().split(",").filter(Boolean);
    else if (k === "--sci") a.sci = true;
    else if (k === "--json") a.json = v();
    else if (k === "--keep") a.keep = true;
    else if (k === "--no-compare") a.noCompare = true;
    else if (k === "--help" || k === "-h") { console.log(readFileSync(fileURLToPath(import.meta.url), "utf8").split("\n").slice(1, 14).join("\n")); process.exit(0); }
    else throw new Error(`unknown argument ${k}`);
  }
  return a;
}

/** Cloudflare's _headers: a path pattern line, then indented "Name: value" or "! Name" lines. */
export function parseHeaders(text) {
  const rules = [];
  let cur = null;
  for (const raw of text.split(/\r?\n/)) {
    if (!raw.trim() || raw.trim().startsWith("#")) continue;
    if (!/^\s/.test(raw)) {
      cur = { pattern: raw.trim(), set: [], remove: [] };
      rules.push(cur);
      continue;
    }
    if (!cur) continue;
    const line = raw.trim();
    if (line.startsWith("!")) cur.remove.push(line.slice(1).trim().toLowerCase());
    else {
      const i = line.indexOf(":");
      if (i > 0) cur.set.push([line.slice(0, i).trim(), line.slice(i + 1).trim()]);
    }
  }
  return rules.map((r) => ({ ...r, re: new RegExp(`^${r.pattern.split("*").map((s) => s.replace(/[.+?^${}()|[\]\\]/g, "\\$&").replace(/:\w+/g, "[^/]+")).join(".*")}$`) }));
}

function headersFor(rules, pathname) {
  const h = new Map();
  for (const r of rules) {
    if (!r.re.test(pathname)) continue;
    for (const name of r.remove) h.delete(name);
    for (const [name, value] of r.set) {
      const key = name.toLowerCase();
      h.set(key, h.has(key) ? `${h.get(key)}, ${value}` : value);
    }
  }
  return h;
}

export function serve({ site, isolated, pyodideDir }) {
  const rules = isolated && existsSync(path.join(site, "_headers")) ? parseHeaders(readFileSync(path.join(site, "_headers"), "utf8")) : [];
  const server = createServer((req, res) => {
    const url = new URL(req.url, "http://localhost");
    let pathname = decodeURIComponent(url.pathname);
    let root = site;
    if (pyodideDir && pathname.startsWith("/pyodide/")) {
      root = pyodideDir;
      pathname = pathname.slice("/pyodide".length);
    }
    let file = path.join(root, pathname);
    if (!file.startsWith(root)) { res.writeHead(403).end(); return; }
    if (existsSync(file) && statSync(file).isDirectory()) file = path.join(file, "index.html");
    const headers = headersFor(rules, url.pathname);
    if (isolated && !rules.length) {
      headers.set("cross-origin-opener-policy", "same-origin");
      headers.set("cross-origin-embedder-policy", "require-corp");
    }
    if (!existsSync(file) || path.basename(file) === "_headers") {
      res.writeHead(404, Object.fromEntries(headers)).end("not found");
      return;
    }
    headers.set("content-type", MIME[path.extname(file)] || "application/octet-stream");
    res.writeHead(200, Object.fromEntries(headers));
    res.end(readFileSync(file));
  });
  return new Promise((resolve) => server.listen(0, "127.0.0.1", () => resolve({ server, origin: `http://127.0.0.1:${server.address().port}` })));
}

export async function loadPlaywright() {
  const tries = [
    () => import("playwright"),
    () => (process.env.PLAYWRIGHT_MODULE ? createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE) : null),
    () => createRequire(import.meta.url)("/usr/local/lib/node_modules_global/playwright"),
    () => import("@playwright/test"),
  ];
  for (const t of tries) {
    try {
      const m = await t();
      if (m?.chromium) return m;
    } catch { /* next */ }
  }
  throw new Error("Playwright is not installed (npm i -D playwright, or set PLAYWRIGHT_MODULE)");
}

export function buildSite() {
  const dir = mkdtempSync(path.join(tmpdir(), "tcmstudio-site-"));
  const out = path.join(dir, "site");
  const r = spawnSync(PYTHON, [path.join(STUDIO, "scripts", "build_web.py"), "--out", out, "--dev"], { stdio: ["ignore", "inherit", "inherit"] });
  if (r.status !== 0) throw new Error("the site build failed");
  return { out, cleanup: () => rmSync(dir, { recursive: true, force: true }) };
}

export async function runPage(context, url, timeoutMs) {
  const page = await context.newPage();
  const console_ = [];
  page.on("console", (m) => console_.push(`${m.type()}: ${m.text()}`.slice(0, 400)));
  page.on("pageerror", (e) => console_.push(`pageerror: ${String(e)}`.slice(0, 400)));
  const t0 = Date.now();
  await page.goto(url);
  let result;
  try {
    await page.waitForFunction(() => globalThis.__result, null, { timeout: timeoutMs, polling: 250 });
    result = await page.evaluate(() => globalThis.__result);
  } catch (err) {
    const partial = await page.evaluate(() => document.getElementById("rows")?.innerText || "").catch(() => "");
    result = { ok: false, failures: [`no result within ${timeoutMs} ms: ${String(err).slice(0, 200)}`], partial };
  }
  result.page_ms = Date.now() - t0;
  result.console = console_.filter((l) => !/^debug:/.test(l)).slice(0, 60);
  await page.close();
  return result;
}

// --------------------------------------------------------------------------------------- native comparison

function nativeCall(tool, args, stateRoot, project) {
  const r = spawnSync(PYTHON, ["-m", "tcmstudio", "call", tool, "--args", JSON.stringify(args), "--where", "browser",
    "--state-root", stateRoot, "--project", project, "--compact"], { encoding: "utf8", maxBuffer: 64 * 1024 * 1024 });
  if (!r.stdout.trim()) throw new Error(`native call ${tool} printed nothing: ${r.stderr.slice(-500)}`);
  return JSON.parse(r.stdout);
}

const stable = (v) => JSON.stringify(sortKeys(v));
function sortKeys(v) {
  if (Array.isArray(v)) return v.map(sortKeys);
  if (v && typeof v === "object") return Object.fromEntries(Object.keys(v).sort().map((k) => [k, sortKeys(v[k])]));
  return v;
}

/** Fields that must match between the browser and the native dispatcher, and the ones that legitimately differ. */
function compareEnvelopes(b, n) {
  const diffs = [];
  const same = (label, x, y) => { if (stable(x) !== stable(y)) diffs.push({ field: label, browser: x, native: y }); };
  same("status", b.status, n.status);
  same("via", b.via, n.via);
  same("summary", b.summary, n.summary);
  same("error.type", b.error?.type ?? null, n.error?.type ?? null);
  same("governance.kind", b.governance?.kind, n.governance?.kind);
  same("governance.released", b.governance?.released, n.governance?.released);
  same("governance.refusals", (b.governance?.refusals || []).map((r) => r.code), (n.governance?.refusals || []).map((r) => r.code));
  same("receipt.content_hash", b.receipt?.content_hash, n.receipt?.content_hash);
  same("receipt.input_sha256", b.receipt?.input_sha256, n.receipt?.input_sha256);
  same("receipt.versions", { ...b.receipt?.versions, python: undefined }, { ...n.receipt?.versions, python: undefined });
  same("citations", (b.citations || []).map((c) => [c.id, c.kind, c.label]), (n.citations || []).map((c) => [c.id, c.kind, c.label]));
  if (b.governance?.kind === "skill") {
    // A governed run records its own time, run id and audit chain position; the claims, evidence and verdict do not.
    same("verdict.states", b.governance?.verdict?.states, n.governance?.verdict?.states);
    same("claims", (b.governance?.claims || []).map((c) => [c.id, c.claim_kind, c.text]), (n.governance?.claims || []).map((c) => [c.id, c.claim_kind, c.text]));
    same("evidence", (b.governance?.evidence || []).map((e) => [e.id, e.tier, e.content_hash]), (n.governance?.evidence || []).map((e) => [e.id, e.tier, e.content_hash]));
    same("outputs", (b.governance?.outputs || []).map((o) => [o.path, o.sha256]), (n.governance?.outputs || []).map((o) => [o.path, o.sha256]));
    same("artifact.composite_version", b.governance?.artifact?.composite_version, n.governance?.artifact?.composite_version);
  } else {
    same("result", b.result, n.result);
    same("receipt.output_sha256", b.receipt?.output_sha256, n.receipt?.output_sha256);
    same("text", b.text, n.text);
  }
  return diffs;
}

// ---------------------------------------------------------------------------------------------------- main

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const built = args.site ? null : buildSite();
  const site = path.resolve(args.site || built.out);
  const pyodideDir = args.pyodideDir ? path.resolve(args.pyodideDir) : null;
  const index = args.index || (pyodideDir ? "/pyodide/" : "");
  const report = { site, index: index || JSON.parse(readFileSync(path.join(site, "runtime", "boot.json"), "utf8")).pyodide.index_url, modes: {}, comparisons: [], ok: true };

  const iso = await serve({ site, isolated: true, pyodideDir });
  const plain = await serve({ site, isolated: false, pyodideDir });
  const { chromium } = await loadPlaywright();
  const proxy = !pyodideDir && (process.env.HTTPS_PROXY || process.env.https_proxy);
  const browser = await chromium.launch({
    executablePath: args.chromium || process.env.CHROMIUM_PATH || undefined,
    args: [...(proxy ? [`--proxy-server=${proxy}`, "--proxy-bypass-list=127.0.0.1;localhost"] : []), "--enable-unsafe-webgpu"],
  });
  const project = `rt-${Date.now().toString(36)}`;
  const q = (mode) => `?mode=${mode}&project=${project}${index ? `&index=${encodeURIComponent(index)}` : ""}${args.sci ? "&sci=1" : ""}`;
  const timeout = args.sci ? 600_000 : 300_000;
  try {
    const main = await browser.newContext();
    for (const mode of args.modes) {
      const ctx = mode === "tamper" ? await browser.newContext() : main;
      const origin = mode === "nonisolated" ? plain.origin : iso.origin;
      const res = await runPage(ctx, `${origin}/test/runtime/index.html${q(mode)}`, timeout);
      report.modes[mode] = res;
      if (!res.ok) report.ok = false;
      if (ctx !== main) await ctx.close();
      const passed = (res.checks || []).filter((c) => c.ok).length;
      console.error(`${mode}: ${res.ok ? "ok" : "FAILED"} (${passed}/${(res.checks || []).length} checks, ${res.page_ms} ms)`);
      for (const f of res.failures || []) console.error(`  - ${f}`);
    }
  } finally {
    await browser.close();
    iso.server.close();
    plain.server.close();
  }

  if (!args.noCompare) {
    const stateRoot = mkdtempSync(path.join(tmpdir(), "tcmstudio-native-"));
    try {
      for (const mode of ["full", "reload"]) {
        for (const c of report.modes[mode]?.calls || []) {
          if (!c.compare || !c.envelope) continue;
          const native = nativeCall(c.tool, c.args, stateRoot, project);
          const diffs = compareEnvelopes(c.envelope, native);
          report.comparisons.push({ label: c.label, tool: c.tool, same: !diffs.length, diffs, browser_ms: c.ms, native_ms: Math.round(native.duration_ms), content_hash: c.content_hash });
          if (diffs.length) report.ok = false;
          console.error(`compare ${c.label}: ${diffs.length ? `DIFFERS in ${diffs.map((d) => d.field).join(", ")}` : "same envelope"}`);
        }
      }
    } finally {
      rmSync(stateRoot, { recursive: true, force: true });
    }
  }

  for (const m of Object.values(report.modes)) for (const c of m.calls || []) delete c.envelope;
  if (args.json) writeFileSync(args.json, JSON.stringify(report, null, 1));
  const full = report.modes.full;
  if (full?.boot) {
    console.error(`boot: ${full.boot.wall_ms} ms wall (worker ${full.boot.boot_ms} ms: ${Object.entries(full.boot.timings || {}).map(([k, v]) => `${k} ${v}`).join(", ")})`);
  }
  if (built && !args.keep) built.cleanup();
  console.error(report.ok ? "browser runtime: all checks passed" : "browser runtime: FAILED");
  process.exit(report.ok ? 0 : 1);
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().catch((err) => {
    console.error(err?.stack || String(err));
    process.exit(2);
  });
}
