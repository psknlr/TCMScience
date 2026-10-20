// The browser chemistry's module worker (js/scientific/chem/chem.worker.js) under Node, through its own handlers and
// message loop: init only from the pinned wasm, prepare's rows and transferred buffers (the same answers as prepare.js
// gives directly), progress events, a cancel message or the interrupt buffer stopping a batch between molecules, and
// clear errors for a call it cannot take. The real thing in Chromium: web/test/runtime/chem.browser.mjs.
import "../fixtures/setup.mjs";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import { MAX_MOLECULES, cancel, handle, serve, workerEnv } from "../../js/scientific/chem/chem.worker.js";
import { RDKIT, loadRDKit } from "../../js/scientific/chem/rdkit.js";
import { Preparer } from "../../js/scientific/chem/prepare.js";
import { N_FEATURES } from "../../js/scientific/chem/features.js";
import { preparedFiles } from "../../js/scientific/chem/prepared-set.js";

const CHEM = fileURLToPath(new URL("../../js/scientific/chem/", import.meta.url));
const require = createRequire(fileURLToPath(new URL("../../../package.json", import.meta.url)));
const factory = require("@rdkit/rdkit");
const WASM = readFileSync(require.resolve("@rdkit/rdkit/RDKit_minimal.wasm"));
const readData = async (name) => JSON.parse(readFileSync(path.join(CHEM, name), "utf8"));
const FIXTURE = JSON.parse(readFileSync(fileURLToPath(new URL("../fixtures/chem_parity.json", import.meta.url)), "utf8"));
const wasmBuffer = () => WASM.buffer.slice(WASM.byteOffset, WASM.byteOffset + WASM.byteLength);
const molecules = (n) => FIXTURE.molecules.slice(0, n).map((m) => ({ name: m.id, smiles: m.smiles }));

function env(extra = {}) {
  const events = [];
  const e = workerEnv({ readData, factory, post: (m) => events.push(m), ...extra });
  e.events = events;
  return e;
}

async function started(extra) {
  const e = env(extra);
  const { reply } = await handle({ id: "i", op: "init", payload: { wasmBytes: wasmBuffer(), sha256: RDKIT.wasm.sha256 } }, e);
  assert.equal(reply.ok, true, JSON.stringify(reply.error));
  return e;
}

test("init starts RDKit.js only from the pinned wasm, given as bytes or fetched; anything else is a clear error", async () => {
  const e = env();
  let r = (await handle({ id: 1, op: "prepare", payload: { molecules: [] } }, e)).reply;
  assert.deepEqual([r.ok, r.error.type], [false, "not_initialised"]);
  r = (await handle({ id: 2, op: "init", payload: { wasmBytes: wasmBuffer(), sha256: "00".repeat(32) } }, e)).reply;
  assert.deepEqual([r.ok, r.error.type], [false, "bad_wasm"]);
  assert.match(r.error.message, /pinned to RDKit\.js 2026\.9\.1/);
  const tampered = new Uint8Array(wasmBuffer());
  tampered[5000] ^= 0xff;
  r = (await handle({ id: 3, op: "init", payload: { wasmBytes: tampered.buffer } }, e)).reply;
  assert.deepEqual([r.ok, r.error.type], [false, "bad_wasm"]);
  r = (await handle({ id: 4, op: "init", payload: {} }, e)).reply;
  assert.deepEqual([r.ok, r.error.type], [false, "bad_arguments"]);
  r = (await handle({ id: 5, op: "init", payload: { wasmBytes: wasmBuffer(), wasmUrl: "https://x.test/a.wasm" } }, e)).reply;
  assert.deepEqual([r.ok, r.error.type], [false, "bad_arguments"]);
  assert.equal((await handle({ id: 6, op: "info" }, e)).reply.result, null, "nothing started by the refused inits");

  r = (await handle({ id: 7, op: "init", payload: { wasmBytes: wasmBuffer(), sha256: RDKIT.wasm.sha256 } }, e)).reply;
  assert.equal(r.ok, true, JSON.stringify(r.error));
  assert.equal(r.result.rdkit, "2026.09.1");
  assert.deepEqual(r.result.engine, { name: "rdkit.js", version: "2026.09.1", detail: "RDKit.js 2026.09.1 MinimalLib + bioagent ports 1" });
  assert.deepEqual(r.result.wasm, { sha256: RDKIT.wasm.sha256, bytes: RDKIT.wasm.bytes, source: "bytes" });
  assert.deepEqual(r.result.catalogs, { entries: 765, unported: [] });
  const { ms: _ms, ...info } = r.result;
  assert.deepEqual((await handle({ id: 8, op: "info" }, e)).reply.result, info);

  const asked = [];
  const fetched = env({ fetch: async (url, init) => { asked.push([url, init.credentials]); return { ok: true, status: 200, arrayBuffer: async () => wasmBuffer() }; } });
  r = (await handle({ id: 10, op: "init", payload: { wasmUrl: "http://127.0.0.1:9/mirror/RDKit_minimal.wasm", sha256: RDKIT.wasm.sha256 } }, fetched)).reply;
  assert.equal(r.ok, true, JSON.stringify(r.error));
  assert.equal(r.result.wasm.source, "http://127.0.0.1:9/mirror/RDKit_minimal.wasm");
  assert.deepEqual(asked, [["http://127.0.0.1:9/mirror/RDKit_minimal.wasm", "omit"]], "one request, to the URL given, no other host");
  const refused = env({ fetch: async () => ({ ok: false, status: 404, arrayBuffer: async () => new ArrayBuffer(0) }) });
  r = (await handle({ id: 11, op: "init", payload: { wasmUrl: "http://127.0.0.1:9/nothing.wasm" } }, refused)).reply;
  assert.deepEqual([r.ok, r.error.type], [false, "bad_wasm"]);
  const stale = env({ readData: async (name) => ({ ...(await readData(name)), rdkit: "2025.09.1" }) });
  r = (await handle({ id: 12, op: "init", payload: { wasmBytes: wasmBuffer() } }, stale)).reply;
  assert.deepEqual([r.ok, r.error.type], [false, "bad_data"]);
});

test("prepare: one row per molecule, the ok rows' features and bits packed in order, as prepare.js gives them", async () => {
  const e = await started();
  const batch = molecules(60);
  const { reply, transfer } = await handle({ id: "p", op: "prepare", payload: { molecules: batch } }, e);
  assert.equal(reply.ok, true, JSON.stringify(reply.error));
  const res = reply.result;
  assert.equal(res.n, 60);
  assert.equal(res.rows.length, 60);
  assert.deepEqual(res.rows.map((r) => r.name), batch.map((m) => m.name));
  assert.equal(res.n_ok, res.rows.filter((r) => r.ok).length);
  assert.ok(res.features instanceof Float32Array && res.features.length === res.n_ok * N_FEATURES);
  assert.ok(res.bits instanceof Uint8Array && res.bits.length === res.n_ok * 256);
  assert.deepEqual(transfer, [res.features.buffer, res.bits.buffer], "the buffers are transferred, not copied");
  assert.ok(res.timing.per_molecule_ms.mean > 0 && res.timing.total_ms >= res.timing.per_molecule_ms.max);
  const direct = new Preparer(await loadRDKit(WASM, { factory }), { tables: await readData("rdkit_tables.json"), catalogs: await readData("filter_catalogs.json") });
  let k = 0;
  for (const [i, m] of batch.entries()) {
    const d = direct.prepare(m.smiles);
    const r = res.rows[i];
    assert.deepEqual([r.ok, r.error, r.canonical, r.alerts, r.atoms_with_h], [d.ok, d.error, d.canonical, d.alerts, d.atoms_with_h], m.name);
    if (d.ok) {
      assert.deepEqual(res.features.subarray(k * N_FEATURES, (k + 1) * N_FEATURES), d.features);
      assert.deepEqual(res.bits.subarray(k * 256, (k + 1) * 256), d.bits);
      k++;
    }
  }
  direct.free();
  const last = e.events.filter((m) => m.type === "event" && m.id === "p").at(-1);
  assert.deepEqual(last.event, { kind: "progress", done: 60, total: 60, ok: res.n_ok, failed: 60 - res.n_ok });
  const files = preparedFiles({ smiles: batch.map((m) => m.smiles), rows: res.rows, features: res.features, bits: res.bits });
  assert.equal(files.n_ok, res.n_ok);
  assert.equal(JSON.parse(files.json).rows.length, 60);

  const noAlerts = (await handle({ id: "q", op: "prepare", payload: { molecules: batch.slice(0, 3), alerts: false } }, e)).reply.result;
  assert.deepEqual(noAlerts.rows.map((r) => r.alerts), [null, null, null], "a training set is prepared without alerts");
});

test("a cancel message or the interrupt buffer stops a batch before its next molecule; a stale flag stops nothing", async () => {
  const e = await started({ yieldMs: 0 });
  const batch = molecules(40);
  setTimeout(() => cancel(e, "c"), 0); // arrives at the first pause, after one molecule
  let r = (await handle({ id: "c", op: "prepare", payload: { molecules: batch } }, e)).reply;
  assert.deepEqual([r.ok, r.error.type], [false, "cancelled"]);
  setTimeout(() => cancel(e, "queued"), 0);
  await new Promise((resolve) => setTimeout(resolve, 5));
  r = (await handle({ id: "queued", op: "prepare", payload: { molecules: batch } }, e)).reply;
  assert.deepEqual([r.ok, r.error.type], [false, "cancelled"], "a cancel for an op not started yet stops it at its start");

  e.interrupt = new Uint8Array(new SharedArrayBuffer(1));
  setTimeout(() => Atomics.store(e.interrupt, 0, 1), 0);
  r = (await handle({ id: "i", op: "prepare", payload: { molecules: batch } }, e)).reply;
  assert.deepEqual([r.ok, r.error.type], [false, "cancelled"]);
  Atomics.store(e.interrupt, 0, 1); // left raised by the op before
  r = (await handle({ id: "next", op: "prepare", payload: { molecules: batch.slice(0, 2) } }, e)).reply;
  assert.equal(r.ok, true, "a flag raised before an op starts was meant for the op before it");
});

test("calls it cannot take: too many molecules, a molecule without SMILES, an unknown op", async () => {
  const e = await started();
  let r = (await handle({ id: 1, op: "prepare", payload: { molecules: new Array(MAX_MOLECULES + 1).fill({ name: "x", smiles: "C" }) } }, e)).reply;
  assert.deepEqual([r.ok, r.error.type], [false, "too_large"]);
  assert.match(r.error.message, /at most 20000/);
  r = (await handle({ id: 2, op: "prepare", payload: { molecules: [{ name: "a", smiles: "C" }, { name: "b" }] } }, e)).reply;
  assert.deepEqual([r.ok, r.error.type], [false, "bad_arguments"]);
  assert.match(r.error.message, /molecule 1 has no smiles/);
  r = (await handle({ id: 3, op: "prepare", payload: { smiles: ["C"] } }, e)).reply;
  assert.deepEqual([r.ok, r.error.type], [false, "bad_arguments"]);
  r = (await handle({ id: 4, op: "train" }, e)).reply;
  assert.deepEqual([r.ok, r.error.type], [false, "bad_arguments"]);
});

test("when RDKit.js itself fails, the batch fails as \"rdkit\", later batches are refused, and init starts it again", async () => {
  const e = await started();
  const ctx = e.state.preparer.ctx;
  const real = ctx.RDKit;
  ctx.RDKit = Object.assign(Object.create(real), { get_mol: () => { throw new WebAssembly.RuntimeError("memory access out of bounds"); } });
  let r = (await handle({ id: 1, op: "prepare", payload: { molecules: molecules(3) } }, e)).reply;
  assert.deepEqual([r.ok, r.error.type], [false, "rdkit"]);
  assert.match(r.error.message, /^molecule 0 \(.+\): RDKit\.js failed while preparing .* \(memory access out of bounds\)/);
  ctx.RDKit = real;
  r = (await handle({ id: 2, op: "prepare", payload: { molecules: molecules(3) } }, e)).reply;
  assert.deepEqual([r.ok, r.error.type], [false, "rdkit"]);
  assert.match(r.error.message, /stopped working earlier/);
  assert.equal((await handle({ id: 3, op: "init", payload: { wasmBytes: wasmBuffer() } }, e)).reply.ok, true);
  r = (await handle({ id: 4, op: "prepare", payload: { molecules: molecules(3) } }, e)).reply;
  assert.equal(r.ok, true, JSON.stringify(r.error));
});

test("the message loop: requests in order, each reply posted with its buffers as the transfer list", async () => {
  const posted = [];
  const scope = { onmessage: null, postMessage: (m, transfer) => posted.push([m, transfer]) };
  const e = env();
  serve(scope, e);
  scope.onmessage({ data: { id: 1, op: "init", payload: { wasmBytes: wasmBuffer() } } });
  scope.onmessage({ data: { id: 2, op: "prepare", payload: { molecules: molecules(5) } } });
  scope.onmessage({ data: "not ours" });
  scope.onmessage({ data: { type: "interrupt-buffer", buffer: new SharedArrayBuffer(1) } });
  for (let i = 0; i < 200 && posted.length < 2; i++) await new Promise((resolve) => setTimeout(resolve, 10));
  assert.deepEqual(posted.map(([m]) => [m.id, m.ok]), [[1, true], [2, true]]);
  assert.deepEqual(posted[0][1], []);
  assert.deepEqual(posted[1][1], [posted[1][0].result.features.buffer, posted[1][0].result.bits.buffer]);
  assert.ok(e.interrupt instanceof Uint8Array);
});
