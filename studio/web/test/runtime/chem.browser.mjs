#!/usr/bin/env node
// The browser chemistry in headless Chromium: the module worker (js/scientific/chem/chem.worker.js) imports the
// vendored RDKit.js loader, takes the wasm from a local mirror of the npm tarball, checks it against the pin, and
// prepares every molecule of the parity corpus (web/test/fixtures/chem_parity.json); its answers must be native RDKit
// 2026.09.1's, as chem.test.mjs requires of the same code under Node. The page is cross-origin isolated (COOP/COEP),
// as Studio's is, and the run also checks that a tampered wasm is refused, that a cancel message and the interrupt
// buffer stop a batch, and reports the times per molecule in the worker.
//
//   node studio/web/test/runtime/chem.browser.mjs [--chromium PATH] [--rdkit-dir DIR] [--json FILE]
//
// --rdkit-dir is the directory holding RDKit_minimal.wasm (default: npm's copy, studio/node_modules/@rdkit/rdkit/
// dist); jsDelivr, where Studio fetches it from, is never asked. Exit 0 only when every check passed.
import assert from "node:assert/strict";
import { readFileSync, writeFileSync } from "node:fs";
import { createServer } from "node:http";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { MIME, loadPlaywright } from "./run.mjs";
import { RDKIT } from "../../js/scientific/chem/rdkit.js";
import { FEATURE_KEYS, FP_BITS, N_FEATURES } from "../../js/scientific/chem/features.js";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const WEB = path.resolve(HERE, "../..");
const args = process.argv.slice(2);
const opt = (name) => { const i = args.indexOf(name); return i >= 0 ? args[i + 1] : null; };
const executablePath = opt("--chromium") || process.env.CHROMIUM_PATH || undefined;
const rdkitDir = opt("--rdkit-dir") || path.dirname(createRequire(path.join(WEB, "..", "package.json")).resolve("@rdkit/rdkit"));
const jsonOut = opt("--json");
const FIXTURE = JSON.parse(readFileSync(path.join(WEB, "test", "fixtures", "chem_parity.json"), "utf8"));
const WASM = readFileSync(path.join(rdkitDir, "RDKit_minimal.wasm"));
const TAMPERED = Buffer.from(WASM);
TAMPERED[123456] ^= 1;

const server = createServer((req, res) => {
  const url = new URL(req.url, "http://localhost");
  res.setHeader("Cross-Origin-Opener-Policy", "same-origin");
  res.setHeader("Cross-Origin-Embedder-Policy", "require-corp");
  res.setHeader("Cross-Origin-Resource-Policy", "same-origin");
  const send = (type, body) => { res.setHeader("Content-Type", type); res.end(body); };
  if (url.pathname === "/") return send(MIME[".html"], "<!doctype html><title>TCMScience browser chemistry</title><link rel=icon href=data:,>");
  if (url.pathname === "/mirror/RDKit_minimal.wasm") return send(MIME[".wasm"], WASM);
  if (url.pathname === "/mirror/tampered.wasm") return send(MIME[".wasm"], TAMPERED);
  if (url.pathname.startsWith("/js/")) {
    const file = path.join(WEB, path.normalize(url.pathname).replace(/^\/+/, ""));
    if (file.startsWith(path.join(WEB, "js") + path.sep)) {
      try { return send(MIME[path.extname(file)] || "application/octet-stream", readFileSync(file)); } catch { /* 404 */ }
    }
  }
  res.statusCode = 404;
  res.end("not found");
});
await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
const origin = `http://127.0.0.1:${server.address().port}`;

/** In the page: the worker's whole run, its replies as plain data (the float32 rows as base64). */
async function inPage({ molecules, sha256 }) {
  const worker = new Worker("/js/scientific/chem/chem.worker.js", { type: "module" });
  const errors = [];
  worker.onerror = (e) => errors.push(String(e.message || e));
  let next = 1;
  const pending = new Map();
  worker.onmessage = (ev) => {
    const m = ev.data;
    if (m?.type === "event") { pending.get(m.id)?.onEvent?.(m.event); return; }
    pending.get(m.id)?.resolve(m);
    pending.delete(m.id);
  };
  const call = (op, payload, { transfer = [], onEvent } = {}) => {
    const id = next++;
    const reply = new Promise((resolve) => pending.set(id, { resolve, onEvent: onEvent && ((event) => onEvent(event, id)) }));
    worker.postMessage({ id, op, payload }, transfer);
    return reply;
  };
  const b64 = (u8) => { let s = ""; for (let i = 0; i < u8.length; i += 0x8000) s += String.fromCharCode(...u8.subarray(i, i + 0x8000)); return btoa(s); };
  const out = { isolated: globalThis.crossOriginIsolated === true, errors };
  out.tampered = await call("init", { wasmUrl: "/mirror/tampered.wasm", sha256 });
  const t0 = performance.now();
  out.init = await call("init", { wasmUrl: "/mirror/RDKit_minimal.wasm", sha256 });
  out.init_ms = performance.now() - t0;
  const t1 = performance.now();
  out.progress = [];
  const r = await call("prepare", { molecules }, { onEvent: (event) => out.progress.push(event) });
  out.prepare_wall_ms = performance.now() - t1;
  if (r.ok) {
    const { rows, n_ok, timing } = r.result;
    out.prepare = { ok: true, n_ok, timing, rows,
      features: b64(new Uint8Array(r.result.features.buffer, r.result.features.byteOffset, r.result.features.byteLength)),
      bits: b64(r.result.bits) };
  } else {
    out.prepare = r;
  }
  // a cancel message stops a batch at its next pause
  const many = Array.from({ length: 2000 }, (_, i) => molecules[i % molecules.length]);
  out.cancel = await call("prepare", { molecules: many }, { onEvent: (event, id) => worker.postMessage({ type: "cancel", id }) });
  // the interrupt buffer, which needs cross-origin isolation for its SharedArrayBuffer
  const flag = new Uint8Array(new SharedArrayBuffer(1));
  worker.postMessage({ type: "interrupt-buffer", buffer: flag.buffer });
  out.interrupt = await call("prepare", { molecules: many }, { onEvent: () => Atomics.store(flag, 0, 1) });
  out.after = await call("prepare", { molecules: molecules.slice(0, 3) });
  worker.terminate();
  return out;
}

let browser;
let report = null;
try {
  const { chromium } = await loadPlaywright();
  browser = await chromium.launch({ executablePath, headless: true });
  const page = await browser.newPage();
  const consoleLines = [];
  page.on("console", (m) => consoleLines.push(`${m.type()}: ${m.text()}`));
  page.on("pageerror", (e) => consoleLines.push(`pageerror: ${e.message}`));
  await page.goto(`${origin}/`);
  const molecules = FIXTURE.molecules.map((m) => ({ name: m.id, smiles: m.smiles }));
  const out = await page.evaluate(inPage, { molecules, sha256: RDKIT.wasm.sha256 });
  const problems = [];
  const counts = { ok: 0, refused: 0, error: 0 };
  if (!out.prepare?.ok) problems.push(`prepare failed: ${JSON.stringify(out.prepare?.error)}`);
  else {
    const features = Buffer.from(out.prepare.features, "base64");
    const bits = Buffer.from(out.prepare.bits, "base64");
    let k = 0;
    out.prepare.rows.forEach((r, i) => {
      const m = FIXTURE.molecules[i];
      const where = `${m.id} ${m.smiles}`;
      if (m.expect === "error") { if (r.ok || r.error !== m.error) problems.push(`${where}: ${r.error}`); else counts.error++; return; }
      if (m.expect === "refused") {
        if (r.ok || r.refused?.missing !== m.refusal) problems.push(`${where}: expected ${m.refusal}, got ${r.ok ? "ok" : r.refused?.missing ?? r.error}`);
        else counts.refused++;
        return;
      }
      if (!r.ok) { problems.push(`${where}: ${r.error}`); return; }
      const row = new Float32Array(N_FEATURES);
      for (const [j, c] of Object.entries(m.counts)) row[Number(j)] = Math.min(c, 255);
      const tail = Buffer.from(m.row_tail_f32, "hex");
      for (let j = 0; j < FEATURE_KEYS.length; j++) row[FP_BITS + j] = tail.readFloatBE(j * 4);
      const got = new Float32Array(features.buffer.slice(features.byteOffset + k * N_FEATURES * 4, features.byteOffset + (k + 1) * N_FEATURES * 4));
      if (!got.every((v, j) => Object.is(v, row[j]))) problems.push(`${where}: feature row differs`);
      if (bits.subarray(k * 256, (k + 1) * 256).toString("hex") !== m.bits) problems.push(`${where}: bits differ`);
      if (r.canonical !== m.canonical) problems.push(`${where}: canonical ${r.canonical}`);
      if (r.atoms_with_h !== m.atoms_with_h) problems.push(`${where}: atoms_with_h ${r.atoms_with_h}`);
      if (JSON.stringify(r.alerts) !== JSON.stringify(m.alerts)) problems.push(`${where}: alerts ${JSON.stringify(r.alerts)}`);
      for (const key of FEATURE_KEYS) {
        const a = r.descriptors[key];
        const b = m.descriptors[key];
        // NaN crosses the worker boundary as NaN (the fixture's JSON has null for it)
        const same = b === null ? Number.isNaN(a) : a === b || ((key === "qed" || key === "BertzCT") && Math.abs(a - b) <= 4 * Number.EPSILON * Math.abs(b));
        if (!same) problems.push(`${where}: ${key} ${a} vs ${b}`);
      }
      counts.ok++;
      k++;
    });
  }
  report = {
    chromium: browser.version(), cross_origin_isolated: out.isolated, wasm_from: `${origin}/mirror/RDKit_minimal.wasm (${rdkitDir})`,
    init: out.init.ok ? { ...out.init.result, page_ms: Math.round(out.init_ms) } : out.init,
    tampered_refused: !out.tampered.ok && out.tampered.error?.type === "bad_wasm" ? out.tampered.error.message : false,
    molecules: FIXTURE.molecules.length, counts, problems,
    worker_timing: out.prepare?.timing, page_wall_ms: Math.round(out.prepare_wall_ms),
    progress_events: out.progress?.length, last_progress: out.progress?.at(-1),
    cancel: out.cancel?.error?.type ?? "not cancelled", interrupt: out.interrupt?.error?.type ?? "not interrupted",
    after_interrupt_ok: out.after?.ok === true, worker_errors: out.errors,
  };
  console.log(JSON.stringify(report, null, 2));
  if (consoleLines.length) console.error(consoleLines.join("\n"));
  if (jsonOut) writeFileSync(jsonOut, JSON.stringify(report, null, 2));
  assert.equal(out.isolated, true, "the page is cross-origin isolated");
  assert.equal(out.init.ok, true, "the worker started RDKit.js from the mirrored wasm");
  assert.equal(out.init.result.rdkit, RDKIT.release);
  assert.ok(report.tampered_refused, "a tampered wasm is refused");
  assert.deepEqual(problems, [], "every molecule: native's answers or the expected refusal");
  assert.deepEqual(counts, FIXTURE.counts);
  assert.equal(report.cancel, "cancelled");
  assert.equal(report.interrupt, "cancelled");
  assert.equal(report.after_interrupt_ok, true, "the next batch runs after an interrupt");
  assert.deepEqual(out.errors, []);
} finally {
  await browser?.close();
  await new Promise((resolve) => server.close(resolve));
}
