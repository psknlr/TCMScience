// AutoDock Vina 1.2.7 compiled to WebAssembly (js/scientific/dock/vina.js and dock.worker.js) under Node, on the
// vendored build: the pin is vina_build.json's; nothing starts from bytes that are not the pinned wasm (a sha other
// than the pin, a wrong size, one flipped byte); the worker's ops through its own handlers and message loop (init,
// dock's tcmstudio.vina-result/1 object and its result_sha256, score, optimize, Vina's own refusal as a result, calls
// refused before Vina sees them, a module that traps is not used again). On the committed native reference
// (fixtures/vina_parity.json, written by studio/scripts/make_dock_fixtures.py with the native vina 1.2.7 wheel):
// score and optimize bit for bit on every ligand (Tier E), and seeded docking held to the Tier S rule on 2 ligands ×
// 3 seeds (VINA_TIER_S=full: every ligand × seeds 1..8, as the harness does), with the WebAssembly engine's own
// results pinned exactly (they are the same on every machine; native Vina's seeded search is not: its last bits
// depend on the CPU's and the C library's maths, which the Monte Carlo search amplifies). VINA_PRINT=1 prints the
// medians and the results not pinned.
import "../fixtures/setup.mjs";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { existsSync, readFileSync } from "node:fs";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import { handle, serve, workerEnv } from "../../js/scientific/dock/dock.worker.js";
import {
  ENGINE, RESULT_SCHEMA, SEARCH, VINA, VinaError, checkWasm, fetchWasm, loadVina, runVina, sha256Hex, vinaParams, vinaResultText,
} from "../../js/scientific/dock/vina.js";

const DOCK = new URL("../../js/scientific/dock/", import.meta.url);
const WEB = new URL("../../", import.meta.url);
const WASM = readFileSync(new URL("vendor/vina-1.2.7.wasm", DOCK));
const BUILD = JSON.parse(readFileSync(new URL("vina_build.json", DOCK), "utf8"));
const wasmBuffer = () => WASM.buffer.slice(WASM.byteOffset, WASM.byteOffset + WASM.byteLength);
const sha = (bytes) => createHash("sha256").update(bytes).digest("hex");

// A made-up pocket around the origin (an aspartate's carboxylate at the bottom, a ring of carbons, a hydroxyl), small
// enough that Vina's grid takes milliseconds, and benzamidine as Meeko 0.8.0 writes it (bioagent.docking.prep).
function pdbqtAtom(i, name, res, seq, [x, y, z], q, type) {
  const f = (v) => v.toFixed(3).padStart(8);
  const atom = (name.length < 4 ? ` ${name}` : name).padEnd(4);
  const charge = (q < 0 ? "" : "+") + q.toFixed(3);
  return `ATOM  ${String(i).padStart(5)} ${atom} ${res} A${String(seq).padStart(4)}    ${f(x)}${f(y)}${f(z)}  1.00  0.00    ${charge} ${type.padEnd(2)}`;
}
const RECEPTOR = [
  pdbqtAtom(1, "CG", "ASP", 189, [0, -4.6, 0], 0.172, "C"),
  pdbqtAtom(2, "OD1", "ASP", 189, [1.1, -4.0, 0], -0.648, "OA"),
  pdbqtAtom(3, "OD2", "ASP", 189, [-1.1, -4.0, 0], -0.648, "OA"),
  ...Array.from({ length: 8 }, (_, k) => {
    const a = (k * Math.PI) / 4;
    return pdbqtAtom(4 + k, "CB", "ALA", 190 + k, [5 * Math.cos(a), 0.5 * (k % 2), 5 * Math.sin(a)], 0.0, "C");
  }),
  pdbqtAtom(12, "OG", "SER", 195, [0, 4.5, 1.0], -0.398, "OA"),
  pdbqtAtom(13, "HG", "SER", 195, [0, 5.4, 1.3], 0.209, "HD"),
].join("\n") + "\n";
const LIGAND = `REMARK SMILES N=C(N)c1ccccc1
REMARK SMILES IDX 3 1 2 2 1 3 4 7 5 8 9 9 6 10 8 11 7 12
REMARK H PARENT 3 4 3 5 1 6
ROOT
ATOM      1  N   UNL     1      -2.431  -0.930   0.819  1.00  0.00    -0.384 N
ATOM      2  C   UNL     1      -1.775  -0.315  -0.205  1.00  0.00    +0.122 C
ATOM      3  N   UNL     1      -2.483   0.035  -1.231  1.00  0.00    -0.284 NA
ATOM      4  H   UNL     1      -3.436  -0.831   0.710  1.00  0.00    +0.158 HD
ATOM      5  H   UNL     1      -2.117  -0.687   1.751  1.00  0.00    +0.158 HD
ATOM      6  H   UNL     1      -1.893   0.442  -1.963  1.00  0.00    +0.189 HD
ENDROOT
BRANCH   2   7
ATOM      7  C   UNL     1      -0.312  -0.063  -0.112  1.00  0.00    +0.016 A
ATOM      8  C   UNL     1       0.506  -0.988   0.546  1.00  0.00    +0.012 A
ATOM      9  C   UNL     1       0.243   1.098  -0.660  1.00  0.00    +0.012 A
ATOM     10  C   UNL     1       1.879  -0.756   0.646  1.00  0.00    +0.001 A
ATOM     11  C   UNL     1       1.617   1.326  -0.556  1.00  0.00    +0.001 A
ATOM     12  C   UNL     1       2.433   0.400   0.095  1.00  0.00    +0.000 A
ENDBRANCH   2   7
TORSDOF 1
`;
const BOX = { center: [0, 0, 0], size: [12, 12, 12] };
const PARAMS = { scoring: "vina", exhaustiveness: 1, n_poses: 3, seed: 7, energy_range: 3.0, ...BOX };

function env(extra = {}) {
  return workerEnv({ fetch: async () => { throw new Error("no network in this test"); }, ...extra });
}

async function started(extra) {
  const e = env(extra);
  const reply = await handle({ id: "i", op: "init", payload: { wasmBytes: wasmBuffer(), sha256: VINA.wasm.sha256 } }, e);
  assert.equal(reply.ok, true, JSON.stringify(reply.error));
  return e;
}

const rejectsWith = (promise, code, re) => assert.rejects(promise, (err) => {
  assert.ok(err instanceof VinaError, String(err));
  assert.equal(err.code, code);
  if (re) assert.match(err.message, re);
  return true;
});

test("the pin is vina_build.json's: version, the wasm's SHA-256 and size, the vendored loader and licences; the engine names it", () => {
  const out = BUILD.outputs;
  assert.equal(VINA.version, BUILD.engine.version);
  assert.equal(VINA.version, "1.2.7");
  assert.deepEqual([VINA.wasm.sha256, VINA.wasm.bytes], [out["vendor/vina-1.2.7.wasm"].sha256, out["vendor/vina-1.2.7.wasm"].bytes]);
  assert.deepEqual([sha(WASM), WASM.byteLength], [VINA.wasm.sha256, VINA.wasm.bytes], "the vendored wasm is the pinned build");
  for (const [file, rel] of [[VINA.loader, "vendor/vina-1.2.7.mjs"], [VINA.wasm.url, "vendor/vina-1.2.7.wasm"]]) {
    assert.equal(new URL(file, WEB).href, new URL(rel, DOCK).href, `${file} is the site's path of dock/${rel}`);
    const bytes = readFileSync(new URL(file, WEB));
    assert.deepEqual([sha(bytes), bytes.byteLength], [out[rel].sha256, out[rel].bytes], rel);
  }
  const licence = readFileSync(new URL(VINA.licence, DOCK));
  assert.deepEqual([sha(licence), licence.byteLength], [out["vendor/LICENSES-vina.txt"].sha256, out["vendor/LICENSES-vina.txt"].bytes]);
  assert.deepEqual({ ...ENGINE }, { name: "AutoDock Vina", version: "1.2.7", build: "wasm", wasm_sha256: VINA.wasm.sha256 });
  assert.ok(Object.isFrozen(VINA) && Object.isFrozen(VINA.wasm) && Object.isFrozen(ENGINE));
  assert.equal(RESULT_SCHEMA, "tcmstudio.vina-result/1");
});

test("checkWasm refuses a sha256 other than the pin before looking at the bytes, then a wrong size, then other bytes", async () => {
  await rejectsWith(checkWasm(wasmBuffer(), "00".repeat(32)), "bad_wasm", /pinned to AutoDock Vina 1\.2\.7 .*sha256 0{64}/);
  await rejectsWith(checkWasm(new ArrayBuffer(0), "11".repeat(32)), "bad_wasm", /pinned to AutoDock Vina/);
  await rejectsWith(checkWasm(wasmBuffer(), ""), "bad_wasm", /\(none given\)/);
  await rejectsWith(checkWasm("not bytes"), "bad_wasm", /must be given as bytes/);
  await rejectsWith(checkWasm(new Uint8Array(WASM.byteLength - 1)), "bad_wasm", /is 492556 bytes; the pinned build's is 492557/);
  const tampered = new Uint8Array(wasmBuffer());
  tampered[100000] ^= 0x01;
  await rejectsWith(checkWasm(tampered), "bad_wasm", /has sha256 [0-9a-f]{64}, not the pinned build's 94807d4a.*: it is not used/);

  const view = await checkWasm(wasmBuffer(), VINA.wasm.sha256.toUpperCase());
  assert.ok(view instanceof Uint8Array && view.byteLength === VINA.wasm.bytes);
  const padded = new Uint8Array(WASM.byteLength + 8);
  padded.set(WASM, 8);
  const sub = await checkWasm(new DataView(padded.buffer, 8, WASM.byteLength));
  assert.equal(sub.byteOffset, 8, "a view is checked where it lies, not copied");
  assert.equal(await sha256Hex(sub), VINA.wasm.sha256);
  assert.equal(await sha256Hex("abc"), "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad", "a string is hashed as UTF-8");
});

test("fetchWasm asks the pinned URL on the site (same-origin credentials) or the one given, checks what comes back, tries nothing else", async () => {
  const asked = [];
  const ok = async (url, init) => { asked.push([url, init.credentials]); return { ok: true, status: 200, arrayBuffer: async () => wasmBuffer() }; };
  const got = await fetchWasm(undefined, { fetch: ok });
  assert.equal(got.byteLength, VINA.wasm.bytes);
  assert.deepEqual(asked, [[new URL("vendor/vina-1.2.7.wasm", DOCK).href, "same-origin"]], "a site path resolves against the site, not the caller");
  await fetchWasm("http://127.0.0.1:9/mirror/vina-1.2.7.wasm", { fetch: ok });
  assert.equal(asked[1][0], "http://127.0.0.1:9/mirror/vina-1.2.7.wasm");
  assert.equal(asked.length, 2);

  await rejectsWith(fetchWasm("http://x.test/v.wasm", { fetch: async () => ({ ok: false, status: 404, arrayBuffer: async () => new ArrayBuffer(0) }) }), "bad_wasm", /answered HTTP 404/);
  await rejectsWith(fetchWasm("http://x.test/v.wasm", { fetch: async () => { throw new TypeError("network down"); } }), "bad_wasm", /could not be downloaded .*network down/);
  await rejectsWith(fetchWasm("http://x.test/v.wasm", { fetch: async () => ({ ok: true, status: 200, arrayBuffer: async () => new ArrayBuffer(10) }) }), "bad_wasm", /is 10 bytes/);
  await rejectsWith(fetchWasm("http://x.test/v.wasm", { fetch: null }), "bad_wasm", /no fetch here/);
  await assert.rejects(fetchWasm("http://x.test/v.wasm", { fetch: async () => { throw new DOMException("stop", "AbortError"); } }), { name: "AbortError" });
  await rejectsWith(fetchWasm("http://x.test/v.wasm", { fetch: ok, sha256: "ab".repeat(32) }), "bad_wasm", /pinned to/);
});

test("loadVina starts the vendored loader only on checked bytes, through instantiateWasm, silently; a loader that fails is \"vina\"", async () => {
  const printed = [];
  const log = console.log;
  const error = console.error;
  console.log = (...a) => printed.push(a);
  console.error = (...a) => printed.push(a);
  let V;
  try {
    V = await loadVina(wasmBuffer());
    const r = V.run("score", RECEPTOR, LIGAND, { sf: "vina", seed: 1, center: BOX.center, size: BOX.size, spacing: 0.375, max_steps: 0 });
    assert.equal(r.ok, true, r.error);
    assert.equal(r.energies.length, 8);
  } finally {
    console.log = log;
    console.error = error;
  }
  assert.deepEqual(printed, [], "nothing printed: no fetch of its own wasm, no Vina output");
  assert.equal(typeof V.run, "function");

  const tampered = new Uint8Array(wasmBuffer());
  tampered[1234] ^= 0xff;
  let called = false;
  const spy = async () => { called = true; return {}; };
  await rejectsWith(loadVina(tampered, { factory: spy }), "bad_wasm", /not the pinned build's/);
  await rejectsWith(loadVina(wasmBuffer(), { sha256: "cd".repeat(32), factory: spy }), "bad_wasm", /pinned to/);
  assert.equal(called, false, "the loader is never called for bytes that are not the pin");

  const given = [];
  const real = (await import("../../js/scientific/dock/vendor/vina-1.2.7.mjs")).default;
  const watched = await loadVina(wasmBuffer(), { factory: (opts) => { given.push(Object.keys(opts).sort()); return real(opts); } });
  assert.deepEqual(given, [["instantiateWasm", "print", "printErr"]], "the loader is handed the compiled module, never a URL or other bytes");
  assert.equal(typeof watched.run, "function");
  // the loader's instantiation fails (imports it does not have): reported, not awaited forever
  await rejectsWith(loadVina(wasmBuffer(), { factory: (opts) => new Promise((resolve) => opts.instantiateWasm({}, resolve)) }), "vina", /did not start/);
  await rejectsWith(loadVina(wasmBuffer(), { factory: async () => { throw new Error("loader broke"); } }), "vina", /did not start: loader broke/);
  await rejectsWith(loadVina(wasmBuffer(), { factory: async () => ({}) }), "vina", /without the binding's run/);
});

test("vinaParams: dock's parameters in the result's order with native's spacing, min_rmsd and max_evals; bounds as the runner's", () => {
  const p = vinaParams("dock", { ...PARAMS, extra: 1 });
  assert.deepEqual(Object.keys(p), ["scoring", "exhaustiveness", "n_poses", "seed", "energy_range", "center", "size", "spacing", "min_rmsd", "max_evals"]);
  assert.deepEqual([p.spacing, p.min_rmsd, p.max_evals], [0.375, 1.0, 0]);
  assert.deepEqual([SEARCH.spacing, SEARCH.min_rmsd, SEARCH.max_evals], [0.375, 1.0, 0]);
  const exact = vinaParams("dock", { ...PARAMS, center: [-1.8555000000000001, 14.366, 16.747999999999998] });
  assert.ok(Object.is(exact.center[0], -1.8555000000000001), "floats pass through unrounded");
  assert.deepEqual(Object.keys(vinaParams("score", PARAMS)), ["scoring", "seed", "center", "size", "spacing"]);
  assert.deepEqual(vinaParams("optimize", { scoring: "vinardo", ...BOX }), { scoring: "vinardo", seed: 42, ...BOX, spacing: 0.375, max_steps: 0 });
  const refused = [
    [{ scoring: "ad4" }, /scoring: vina or vinardo/],
    [{ exhaustiveness: 0 }, /exhaustiveness: a whole number from 1 to 64/],
    [{ exhaustiveness: 65 }, /exhaustiveness/],
    [{ exhaustiveness: 8.5 }, /exhaustiveness/],
    [{ n_poses: 21 }, /n_poses: a whole number from 1 to 20/],
    [{ seed: 0 }, /seed: a whole number from 1 to 2147483647/],
    [{ seed: 2 ** 31 }, /seed/],
    [{ seed: "42" }, /seed/],
    [{ energy_range: 0 }, /energy_range: a finite number > 0/],
    [{ energy_range: undefined }, /energy_range/],
    [{ center: [0, 0] }, /center: three finite numbers/],
    [{ center: [0, 0, NaN] }, /center/],
    [{ size: [10, 10, 0] }, /size: three finite numbers .*each > 0/],
    [{ spacing: 0 }, /spacing/],
    [{ min_rmsd: -1 }, /min_rmsd: a finite number ≥ 0/],
    [{ max_evals: 1.5 }, /max_evals/],
  ];
  for (const [change, re] of refused) {
    assert.throws(() => vinaParams("dock", { ...PARAMS, ...change }), (err) => err instanceof VinaError && err.code === "bad_arguments" && re.test(err.message), JSON.stringify(change));
  }
  assert.throws(() => vinaParams("dock", null), /params: an object/);
  assert.throws(() => vinaParams("fly", PARAMS), /mode "fly": score, optimize or dock/);
  assert.throws(() => vinaParams("optimize", { ...PARAMS, max_steps: -1 }), /max_steps/);
});

test("init starts Vina only from the pinned wasm, given as bytes or fetched; anything else is a clear error", async () => {
  const e = env();
  let r = await handle({ id: 1, op: "dock", payload: { key: "ligand-0", receptor: RECEPTOR, ligand: LIGAND, params: PARAMS } }, e);
  assert.deepEqual([r.ok, r.error.type], [false, "not_initialised"]);
  r = await handle({ id: 2, op: "init", payload: { wasmBytes: wasmBuffer(), sha256: "00".repeat(32) } }, e);
  assert.deepEqual([r.ok, r.error.type], [false, "bad_wasm"]);
  assert.match(r.error.message, /pinned to AutoDock Vina 1\.2\.7/);
  const tampered = new Uint8Array(wasmBuffer());
  tampered[5000] ^= 0xff;
  r = await handle({ id: 3, op: "init", payload: { wasmBytes: tampered.buffer } }, e);
  assert.deepEqual([r.ok, r.error.type], [false, "bad_wasm"]);
  r = await handle({ id: 4, op: "init", payload: {} }, e);
  assert.deepEqual([r.ok, r.error.type], [false, "bad_arguments"]);
  r = await handle({ id: 5, op: "init", payload: { wasmBytes: wasmBuffer(), wasmUrl: "https://x.test/v.wasm" } }, e);
  assert.deepEqual([r.ok, r.error.type], [false, "bad_arguments"]);
  r = await handle({ id: 6, op: "init", payload: { wasmUrl: "https://x.test/v.wasm" } }, e);
  assert.deepEqual([r.ok, r.error.type], [false, "bad_wasm"], "a fetch that fails");
  assert.equal((await handle({ id: 7, op: "info" }, e)).result, null, "nothing started by the refused inits");

  r = await handle({ id: 8, op: "init", payload: { wasmBytes: wasmBuffer(), sha256: VINA.wasm.sha256 } }, e);
  assert.equal(r.ok, true, JSON.stringify(r.error));
  assert.deepEqual(r.result.engine, { name: "AutoDock Vina", version: "1.2.7", build: "wasm", wasm_sha256: VINA.wasm.sha256 });
  assert.deepEqual(r.result.wasm, { sha256: VINA.wasm.sha256, bytes: VINA.wasm.bytes, source: "bytes" });
  assert.ok(Number.isInteger(r.result.ms) && Number.isInteger(r.ms));
  const { ms: _ms, ...info } = r.result;
  assert.deepEqual((await handle({ id: 9, op: "info" }, e)).result, info);

  const asked = [];
  const fetched = env({ fetch: async (url, init) => { asked.push([url, init.credentials]); return { ok: true, status: 200, arrayBuffer: async () => wasmBuffer() }; } });
  r = await handle({ id: 10, op: "init", payload: { wasmUrl: VINA.wasm.url, sha256: VINA.wasm.sha256 } }, fetched);
  assert.equal(r.ok, true, JSON.stringify(r.error));
  assert.equal(r.result.wasm.source, VINA.wasm.url);
  assert.deepEqual(asked, [[new URL(VINA.wasm.url, WEB).href, "same-origin"]], "boot.json's site path, resolved against the site");
});

test("dock → the tcmstudio.vina-result/1 object, its result_sha256 the SHA-256 of JSON.stringify of the rest; the same seed, the same poses", async () => {
  const e = await started();
  const r = await handle({ id: "d", op: "dock", payload: { key: "ligand-3", receptor: RECEPTOR, ligand: LIGAND, params: PARAMS } }, e);
  assert.equal(r.ok, true, JSON.stringify(r.error));
  const res = r.result;
  assert.deepEqual(Object.keys(res), ["schema", "key", "receptor_sha256", "ligand_sha256", "params", "engine", "energies", "poses", "seconds", "result_sha256"]);
  assert.equal(res.schema, "tcmstudio.vina-result/1");
  assert.equal(res.key, "ligand-3");
  assert.equal(res.receptor_sha256, sha(Buffer.from(RECEPTOR, "utf8")));
  assert.equal(res.ligand_sha256, sha(Buffer.from(LIGAND, "utf8")));
  assert.deepEqual(res.params, { scoring: "vina", exhaustiveness: 1, n_poses: 3, seed: 7, energy_range: 3.0, center: [0, 0, 0], size: [12, 12, 12], spacing: 0.375, min_rmsd: 1.0, max_evals: 0 });
  assert.deepEqual(res.engine, { ...ENGINE });
  assert.ok(res.energies.length >= 1 && res.energies.length <= 3);
  for (const row of res.energies) assert.ok(row.length === 5 && row.every(Number.isFinite), JSON.stringify(row));
  assert.ok(res.energies[0][0] < 0, "benzamidine binds the made-up pocket");
  for (let k = 1; k < res.energies.length; k++) assert.ok(res.energies[k][0] >= res.energies[k - 1][0], "ranked best first");
  assert.equal((res.poses.match(/^MODEL /gm) || []).length, res.energies.length);
  assert.equal((res.poses.match(/^ENDMDL/gm) || []).length, res.energies.length);
  assert.match(res.poses, /^REMARK VINA RESULT: /m);
  assert.ok(typeof res.seconds === "number" && res.seconds >= 0);

  const { result_sha256, ...doc } = res;
  const text = JSON.stringify(doc);
  assert.equal(result_sha256, sha(Buffer.from(text, "utf8")), "the digest is of the exact bytes the page writes");
  assert.equal(vinaResultText(res), text);
  assert.equal(JSON.stringify(structuredClone(doc)), text, "the same text on the page's side of postMessage");

  const again = (await handle({ id: "d2", op: "dock", payload: { key: "ligand-3", receptor: RECEPTOR, ligand: LIGAND, params: PARAMS } }, e)).result;
  assert.deepEqual([again.energies, again.poses], [res.energies, res.poses], "a seeded run repeats bit for bit");
  const other = (await handle({ id: "d3", op: "dock", payload: { key: "reference", receptor: RECEPTOR, ligand: LIGAND, params: { ...PARAMS, seed: 8, scoring: "vinardo" } } }, e)).result;
  assert.equal(other.key, "reference");
  assert.deepEqual([other.params.seed, other.params.scoring], [8, "vinardo"]);
  assert.notEqual(other.poses, res.poses);
});

test("score and optimize: Vina.score() and Vina.optimize() on the pose given; optimize's pose scores as it reports", async () => {
  const e = await started();
  const p = { scoring: "vina", ...BOX };
  const s = await handle({ id: 1, op: "score", payload: { receptor: RECEPTOR, ligand: LIGAND, params: p } }, e);
  assert.equal(s.ok, true, JSON.stringify(s.error));
  assert.deepEqual(Object.keys(s.result), ["mode", "receptor_sha256", "ligand_sha256", "params", "engine", "energies", "seconds"]);
  assert.equal(s.result.mode, "score");
  assert.equal(s.result.energies.length, 8);
  assert.deepEqual(s.result.params, { scoring: "vina", seed: 42, ...BOX, spacing: 0.375 });
  const o = await handle({ id: 2, op: "optimize", payload: { receptor: RECEPTOR, ligand: LIGAND, params: p } }, e);
  assert.equal(o.ok, true, JSON.stringify(o.error));
  assert.deepEqual(Object.keys(o.result), ["mode", "receptor_sha256", "ligand_sha256", "params", "engine", "energies", "pose", "seconds"]);
  assert.equal(o.result.params.max_steps, 0);
  assert.ok(o.result.energies[0] <= s.result.energies[0], "a local optimisation does not make the pose worse");
  assert.match(o.result.pose, /^ROOT$/m);
  const rescored = await handle({ id: 3, op: "score", payload: { receptor: RECEPTOR, ligand: o.result.pose, params: p } }, e);
  assert.ok(Math.abs(rescored.result.energies[0] - o.result.energies[0]) < 0.01, "the written pose (3 decimals) scores as optimize reported");
  const again = await handle({ id: 4, op: "score", payload: { receptor: RECEPTOR, ligand: LIGAND, params: { ...p, seed: 99 } } }, e);
  assert.deepEqual(again.result.energies, s.result.energies, "score draws nothing from the seed");
});

test("Vina's own refusal of an input is a result with error (the op succeeds), written and hashed like any result", async () => {
  const e = await started();
  for (const [ligand, re] of [["ROOT\nENDROOT\nTORSDOF 0\n", /No atoms in this ligand/], ["garbage\n", /Unknown or inappropriate tag/]]) {
    const r = await handle({ id: 1, op: "dock", payload: { key: "ligand-1", receptor: RECEPTOR, ligand, params: PARAMS } }, e);
    assert.equal(r.ok, true, JSON.stringify(r.error));
    assert.deepEqual(Object.keys(r.result), ["schema", "key", "receptor_sha256", "ligand_sha256", "params", "engine", "error", "seconds", "result_sha256"]);
    assert.match(r.result.error, re);
    assert.match(r.result.error, /^PDBQT parsing error/, "Vina's message, its surrounding blank lines trimmed");
    assert.equal(r.result.result_sha256, sha(Buffer.from(vinaResultText(r.result), "utf8")));
  }
  const flexible = RECEPTOR + "ROOT\n";
  const r = await handle({ id: 2, op: "score", payload: { receptor: flexible, ligand: LIGAND, params: { scoring: "vina", ...BOX } } }, e);
  assert.equal(r.ok, true);
  assert.match(r.result.error, /PDBQT parsing error/);
  const after = await handle({ id: 3, op: "dock", payload: { key: "ligand-0", receptor: RECEPTOR, ligand: LIGAND, params: PARAMS } }, e);
  assert.ok(after.ok && after.result.energies, "a refusal leaves the module usable");
});

test("calls refused before Vina sees them: a bad key, missing texts, bad parameters, an unknown op", async () => {
  const e = await started();
  const call = (payload, op = "dock") => handle({ id: 1, op, payload }, e);
  const base = { key: "ligand-0", receptor: RECEPTOR, ligand: LIGAND, params: PARAMS };
  for (const key of ["../x", "ligand-01", "ligand-", "ligand-1a", "", null, 3]) {
    const r = await call({ ...base, key });
    assert.deepEqual([r.ok, r.error.type], [false, "bad_arguments"], JSON.stringify(key));
  }
  for (const change of [{ receptor: "" }, { receptor: undefined }, { ligand: "  \n" }, { ligand: 5 }, { params: { ...PARAMS, seed: 0 } }, { params: undefined }]) {
    const r = await call({ ...base, ...change });
    assert.deepEqual([r.ok, r.error.type], [false, "bad_arguments"], JSON.stringify(change));
  }
  let r = await call({ receptor: RECEPTOR, ligand: LIGAND, params: { scoring: "vina", center: [0, 0, 0] } }, "score");
  assert.deepEqual([r.ok, r.error.type], [false, "bad_arguments"]);
  assert.match(r.error.message, /^size: /);
  r = await handle({ id: 2, op: "train" }, e);
  assert.deepEqual([r.ok, r.error.type], [false, "bad_arguments"]);
  r = await handle(undefined, e);
  assert.deepEqual([r.ok, r.error.type], [false, "bad_arguments"]);
  assert.throws(() => runVina({ run: () => ({ ok: true }) }, "dock", RECEPTOR, "", PARAMS), /ligand: the PDBQT text/);
});

test("a run that throws breaks the module: \"vina\" now and for every later run, until init starts a fresh one", async () => {
  const real = (await import("../../js/scientific/dock/vendor/vina-1.2.7.mjs")).default;
  let trap = false;
  const factory = async (opts) => {
    const V = await real(opts);
    return { run: (...args) => { if (trap) throw new WebAssembly.RuntimeError("memory access out of bounds"); return V.run(...args); } };
  };
  const e = await started({ factory });
  trap = true;
  let r = await handle({ id: 1, op: "dock", payload: { key: "ligand-0", receptor: RECEPTOR, ligand: LIGAND, params: PARAMS } }, e);
  assert.deepEqual([r.ok, r.error.type], [false, "vina"]);
  assert.match(r.error.message, /failed during dock \(memory access out of bounds\); this module is not used again/);
  trap = false;
  r = await handle({ id: 2, op: "score", payload: { receptor: RECEPTOR, ligand: LIGAND, params: { scoring: "vina", ...BOX } } }, e);
  assert.deepEqual([r.ok, r.error.type], [false, "vina"]);
  assert.match(r.error.message, /stopped working earlier in this worker .*init starts a fresh module/);
  assert.equal((await handle({ id: 3, op: "init", payload: { wasmBytes: wasmBuffer() } }, e)).ok, true);
  r = await handle({ id: 4, op: "dock", payload: { key: "ligand-0", receptor: RECEPTOR, ligand: LIGAND, params: PARAMS } }, e);
  assert.equal(r.ok, true, JSON.stringify(r.error));

  const lying = await started({ factory: async (opts) => { const V = await real(opts); return { run: (m, a, b, p) => ({ ...V.run(m, a, b, p), seed: p.seed + 1 }) }; } });
  r = await handle({ id: 5, op: "dock", payload: { key: "ligand-0", receptor: RECEPTOR, ligand: LIGAND, params: PARAMS } }, lying);
  assert.deepEqual([r.ok, r.error.type], [false, "vina"]);
  assert.match(r.error.message, /ran with seed 8, not the 7 it was given/);

  // std::bad_alloc reaches the binding's catch with Vina's own errors: out of memory is not Vina refusing the ligand
  const starved = await started({ factory: async () => ({ run: () => ({ ok: false, error: "std::bad_alloc" }) }) });
  r = await handle({ id: 6, op: "dock", payload: { key: "ligand-0", receptor: RECEPTOR, ligand: LIGAND, params: PARAMS } }, starved);
  assert.deepEqual([r.ok, r.error.type], [false, "vina"]);
  assert.match(r.error.message, /ran out of WebAssembly memory during dock \(std::bad_alloc\)/);
  assert.throws(() => runVina({ run: () => ({ ok: false, error: "std::bad_alloc" }) }, "score", RECEPTOR, LIGAND, { scoring: "vina", ...BOX }),
    (err) => err instanceof VinaError && err.code === "vina" && err.memory === true && err.broken === true);
});

test("the message loop: requests answered in order, one at a time; messages without an id are ignored", async () => {
  const posted = [];
  const scope = { onmessage: null, postMessage: (m) => posted.push(m) };
  serve(scope, env());
  scope.onmessage({ data: { id: 1, op: "init", payload: { wasmBytes: wasmBuffer() } } });
  scope.onmessage({ data: { id: 2, op: "dock", payload: { key: "ligand-0", receptor: RECEPTOR, ligand: LIGAND, params: PARAMS } } });
  scope.onmessage({ data: { id: 3, op: "score", payload: { receptor: RECEPTOR, ligand: LIGAND, params: { scoring: "vina", ...BOX } } } });
  scope.onmessage({ data: "not ours" });
  scope.onmessage({ data: { op: "info" } });
  scope.onmessage({ data: { id: 4, op: "nope" } });
  for (let i = 0; i < 500 && posted.length < 4; i++) await new Promise((resolve) => setTimeout(resolve, 10));
  assert.deepEqual(posted.map((m) => [m.id, m.ok]), [[1, true], [2, true], [3, true], [4, false]]);
  assert.equal(posted[1].result.schema, RESULT_SCHEMA);
});

// ------------------------------------------------------------------ the native reference (fixtures/vina_parity.json)

const FIXTURE_URL = new URL("../fixtures/vina_parity.json", import.meta.url);
const FIXTURE = existsSync(FIXTURE_URL) ? JSON.parse(readFileSync(FIXTURE_URL, "utf8")) : null;
const NO_FIXTURE = FIXTURE ? false : "fixtures/vina_parity.json is not there (studio/scripts/make_dock_fixtures.py writes it)";
const REPO = new URL("../../../../", import.meta.url); // the fixture's paths are the repository's
const repoText = (rel) => readFileSync(new URL(rel, REPO), "utf8");
// Tier S in this test: 2 ligands × 3 seeds (under a minute); VINA_TIER_S=full runs every ligand × every seed (1..8)
const FULL = process.env.VINA_TIER_S === "full";
const TIER_S = FULL ? null : { ligands: ["benzamidine", "p_methylbenzamidine"], seeds: [1, 2, 3] };
const TIER_S_MEDIAN = 0.10; // kcal/mol: |median(best browser) − median(best native)| per ligand
const REDOCK_RMSD = 2.0; // Å, bioagent.docking.prepared.REDOCK_RMSD

/** The ligands of a PDBQT file as bioagent.docking.prepared.read_ligands gives them to Vina: one per MODEL block
 * (MODEL/ENDMDL lines dropped), or the whole file; "\n" line ends, ending with one. */
function splitModels(text) {
  const lines = text.replace(/\r\n?/g, "\n").replace(/\n+$/, "").split("\n");
  if (!lines.some((l) => l.startsWith("MODEL"))) return [lines.join("\n") + "\n"];
  const out = [];
  let block = null;
  for (const l of lines) {
    if (l.startsWith("MODEL")) block = [];
    else if (l.startsWith("ENDMDL")) { out.push(block.join("\n") + "\n"); block = null; }
    else if (block) block.push(l);
  }
  return out;
}

/** Heavy atoms' coordinates (AutoDock types other than H, HD, HS), in file order, of a pose text's first model. */
function heavyXyz(text) {
  const xyz = [];
  for (const l of text.split("\n")) {
    if (l.startsWith("ENDMDL")) break;
    if (!/^(ATOM|HETATM)/.test(l) || ["H", "HD", "HS"].includes(l.slice(77, 79).trim())) continue;
    xyz.push([30, 38, 46].map((c) => Number(l.slice(c, c + 8))));
  }
  return xyz;
}

/** RMSD in atom order (no symmetry: an upper bound of the symmetry-aware one), as the pipeline's redocking check. */
function rmsdInOrder(a, b) {
  assert.equal(a.length, b.length, "the same heavy atoms");
  let s = 0;
  for (let i = 0; i < a.length; i++) for (let k = 0; k < 3; k++) s += (a[i][k] - b[i][k]) ** 2;
  return Math.sqrt(s / a.length);
}

const median = (xs) => {
  const s = [...xs].sort((x, y) => x - y);
  return s.length % 2 ? s[(s.length - 1) / 2] : (s[s.length / 2 - 1] + s[s.length / 2]) / 2;
};

/** The texts Vina gets: the receptor, each ligand (a MODEL block of the ligands file), the reference ligand. */
function parityInputs() {
  const receptor = repoText(FIXTURE.files.receptor);
  const ligands = splitModels(repoText(FIXTURE.files.ligands));
  const reference = splitModels(repoText(FIXTURE.files.reference))[0];
  return { receptor, ligands, reference };
}

const box = () => ({ center: FIXTURE.box.center, size: FIXTURE.box.size });
const searchParams = (seed) => {
  const { scoring, exhaustiveness, n_poses, energy_range, spacing, min_rmsd, max_evals } = FIXTURE.params;
  return { scoring, exhaustiveness, n_poses, seed, energy_range, ...box(), spacing, min_rmsd, max_evals };
};

test("the native reference's inputs are the committed fixtures: receptor, every ligand and the reference by SHA-256, in order", { skip: NO_FIXTURE }, () => {
  assert.equal(FIXTURE.schema, "tcmstudio.vina-parity/1");
  assert.equal(FIXTURE.native.vina, VINA.version);
  assert.deepEqual([FIXTURE.params.spacing, FIXTURE.params.min_rmsd, FIXTURE.params.max_evals], [SEARCH.spacing, SEARCH.min_rmsd, SEARCH.max_evals]);
  const { receptor, ligands, reference } = parityInputs();
  assert.equal(sha(Buffer.from(receptor, "utf8")), FIXTURE.receptor_sha256);
  assert.equal(ligands.length, FIXTURE.ligands.length);
  ligands.forEach((text, i) => {
    const lig = FIXTURE.ligands[i];
    assert.equal(sha(Buffer.from(text, "utf8")), lig.sha256, `${lig.name}: the MODEL block as Vina gets it`);
    assert.equal(repoText(lig.pdbqt), text, `${lig.name}: its own file`);
    assert.equal(sha(Buffer.from(repoText(lig.top_pose), "utf8")), lig.top_pose_sha256, `${lig.name}: its native top pose`);
    assert.ok(text.includes(`REMARK  Name = ${lig.name}\n`));
  });
  assert.equal(sha(Buffer.from(reference, "utf8")), FIXTURE.reference.sha256);
  assert.equal(heavyXyz(reference).length, FIXTURE.reference.heavy_atoms);
});

test("Tier E: score and optimize are native Vina 1.2.7's raw values bit for bit on every ligand, and the optimised pose byte for byte", { skip: NO_FIXTURE }, async () => {
  const e = await started();
  const { receptor } = parityInputs();
  const params = { scoring: FIXTURE.params.scoring, ...box(), spacing: FIXTURE.params.spacing };
  for (const lig of FIXTURE.ligands) {
    const pose = repoText(lig.top_pose);
    const s = await handle({ id: 1, op: "score", payload: { receptor, ligand: pose, params } }, e);
    assert.equal(s.ok, true, JSON.stringify(s.error));
    assert.deepEqual(s.result.energies, lig.score_raw, `${lig.name}: score`); // deepStrictEqual: Object.is on each float
    const o = await handle({ id: 2, op: "optimize", payload: { receptor, ligand: pose, params } }, e);
    assert.equal(o.ok, true, JSON.stringify(o.error));
    assert.deepEqual(o.result.energies, lig.optimize_raw, `${lig.name}: optimize`);
    assert.equal(o.result.pose, lig.optimize_pose, `${lig.name}: the optimised pose`);
  }
});

// The WebAssembly engine's own results on the fixture's inputs and search parameters, for the runs this test makes by
// default: WebAssembly's arithmetic is the same on every machine (Vina's libm is compiled in), so they are pinned
// exactly; a change means the engine or its inputs changed. name → seed → [best total (raw), SHA-256 of the poses].
const WASM_DOCK = {
  benzamidine: {
    1: [-6.054151427044774, "707afd6a8bfdee173706a75cada11aa1dd13f1796127d4c0aa90eee91441bf3e"],
    2: [-5.978843747542764, "f28e1d328da21d0267fa40721b881caad146b7cf0edc7ae12b593b6332c8bd1b"],
    3: [-6.055752889102715, "abf937892e18c6c4475af2b4937dce8559c1b34067ed5111615f83a8c0fea41a"],
  },
  p_methylbenzamidine: {
    1: [-6.150620256654999, "a6e5cb596e39f7c2a2cf0f3f6a054f5740d357167d297ddf3476278a6ad4e04f"],
    2: [-6.179353229019538, "b37e8c957ac357031b97a16ea59c7c862bc97d225c5f4eafc5209e0da673cc3f"],
    3: [-6.161525321420359, "9fc80124c6cc387eb1e15ce891eec460226c018195c3c25459e95e68dd78f809"],
  },
  reference: {
    1: [-6.1840371863980685, "375f192c40ee9e7b2f693ddda754c9bea1006fe5585db7f08923c18636f7b0d4"],
    2: [-6.17921006808369, "dac8c628266d6e4ff82cff0a62b3e5cec658bdf533c9586233b07428d01b15d4"],
    3: [-6.193712036740894, "e2b17a3c7933eba4459c6cb93e99f39ba2e577bae8051430d457e97288f4094f"],
  },
};

test("Tier S: seeded docking within 0.10 kcal/mol of native's median best per ligand, and the crystal redocked as often", { skip: NO_FIXTURE, timeout: FULL ? 3_600_000 : 180_000 }, async () => {
  const e = await started();
  const { receptor, ligands, reference } = parityInputs();
  const seeds = TIER_S ? TIER_S.seeds : FIXTURE.params.seeds;
  const chosen = FIXTURE.ligands.map((lig, i) => ({ ...lig, index: i, text: ligands[i] })).filter((lig) => !TIER_S || TIER_S.ligands.includes(lig.name));
  assert.equal(chosen.length, TIER_S ? TIER_S.ligands.length : FIXTURE.ligands.length);
  let pinned = 0;
  const dock = async (name, key, ligand, seed) => {
    const r = await handle({ id: `${key}:${seed}`, op: "dock", payload: { key, receptor, ligand, params: searchParams(seed) } }, e);
    assert.equal(r.ok, true, JSON.stringify(r.error));
    assert.equal(r.result.error, undefined, r.result.error);
    const got = [r.result.energies[0][0], sha(Buffer.from(r.result.poses, "utf8"))];
    const want = WASM_DOCK[name]?.[seed];
    if (want) {
      assert.deepEqual(got, want, `${name} seed ${seed}: the engine's own result`);
      pinned++;
    } else if (process.env.VINA_PRINT) console.log(JSON.stringify([name, seed, got]));
    return r.result;
  };
  const report = [];
  for (const lig of chosen) {
    const browser = [];
    for (const seed of seeds) browser.push((await dock(lig.name, `ligand-${lig.index}`, lig.text, seed)).energies[0][0]);
    const native = seeds.map((seed) => lig.dock[String(seed)].best_raw);
    const delta = Math.abs(median(browser) - median(native));
    report.push(`${lig.name}: median best ${median(browser).toFixed(3)} here, ${median(native).toFixed(3)} native (Δ ${delta.toFixed(3)})`);
    assert.ok(delta <= TIER_S_MEDIAN, `${lig.name}: |Δ median best| ${delta} > ${TIER_S_MEDIAN} kcal/mol over seeds ${seeds}`);
  }
  const crystal = heavyXyz(reference);
  let here = 0;
  for (const seed of seeds) if (rmsdInOrder(heavyXyz((await dock("reference", "reference", reference, seed)).poses), crystal) <= REDOCK_RMSD) here++;
  const native = seeds.filter((seed) => FIXTURE.crystal_redock[String(seed)] <= REDOCK_RMSD).length;
  report.push(`crystal redocked within ${REDOCK_RMSD} Å (atom order) on ${here}/${seeds.length} seeds here, ${native}/${seeds.length} native`);
  if (process.env.VINA_PRINT) console.log(report.join("\n"));
  assert.ok(here >= native, report.join("; "));
  if (!FULL) assert.equal(pinned, 9, "every default run is pinned");
});
