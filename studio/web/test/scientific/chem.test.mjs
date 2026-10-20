// The browser chemistry (js/scientific/chem) against native RDKit 2026.09.1 (fixtures/chem_parity.json, written by
// studio/scripts/make_chem_fixtures.py): the vendored RDKit.js loader is npm's file byte for byte, the wasm is used
// only at its pinned SHA-256, and on every molecule of the parity corpus the browser either gives native's answers
// (canonical SMILES, the 2073-column float32 feature row, the packed bits, the descriptors, AddHs's atom count, the
// FilterCatalog alerts) or refuses, exactly where the native intermediates say it must, naming the step.
import "../fixtures/setup.mjs";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import { ChemError, RDKIT, checkWasm, fetchWasm, loadRDKit } from "../../js/scientific/chem/rdkit.js";
import { Ctx } from "../../js/scientific/chem/molecule.js";
import { FEATURE_KEYS, FP_BITS, FeatureTables, N_FEATURES, features } from "../../js/scientific/chem/features.js";
import { MAX_ATOMS, REFUSALS, StandardizeData, pyRepr, smilesAtoms, standardize } from "../../js/scientific/chem/standardize.js";
import { AlertCatalog } from "../../js/scientific/chem/alerts.js";
import { Preparer } from "../../js/scientific/chem/prepare.js";
import { ENGINE, PREPARED_SCHEMA, preparedFiles, smilesSha256 } from "../../js/scientific/chem/prepared-set.js";

const CHEM = fileURLToPath(new URL("../../js/scientific/chem/", import.meta.url));
const STUDIO = fileURLToPath(new URL("../../../", import.meta.url));
const require = createRequire(path.join(STUDIO, "package.json"));
const NPM_LOADER = require.resolve("@rdkit/rdkit");
const NPM_WASM = require.resolve("@rdkit/rdkit/RDKit_minimal.wasm");
const factory = require("@rdkit/rdkit");
const json = (p) => JSON.parse(readFileSync(p, "utf8"));
const TABLES = json(path.join(CHEM, "rdkit_tables.json"));
const CATALOGS = json(path.join(CHEM, "filter_catalogs.json"));
const FIXTURE = json(fileURLToPath(new URL("../fixtures/chem_parity.json", import.meta.url)));
const WASM = readFileSync(NPM_WASM);
const sha256 = (b) => createHash("sha256").update(b).digest("hex");

let rdkit = null;
async function RDKitModule() {
  rdkit ??= loadRDKit(WASM, { factory });
  return rdkit;
}

/** The fixture's float32 row: the clipped counts, then the 25 descriptor columns (big-endian float32 hex). */
function nativeRow(m) {
  const row = new Float32Array(N_FEATURES);
  for (const [i, c] of Object.entries(m.counts)) row[Number(i)] = Math.min(c, 255);
  const tail = Buffer.from(m.row_tail_f32, "hex");
  for (let j = 0; j < FEATURE_KEYS.length; j++) row[FP_BITS + j] = tail.readFloatBE(j * 4);
  return row;
}

test("the vendored loader is npm's dist/RDKit_minimal.js byte for byte, then one export line; the pins are npm's", async () => {
  const npm = readFileSync(NPM_LOADER);
  const vendored = readFileSync(path.join(CHEM, RDKIT.loader.path));
  assert.equal(npm.length, RDKIT.loader.npm_bytes);
  assert.equal(sha256(npm), RDKIT.loader.npm_sha256, "the installed @rdkit/rdkit loader is the pinned one");
  assert.ok(vendored.subarray(0, npm.length).equals(npm), "everything before the appended line is npm's file");
  assert.equal(vendored.subarray(npm.length).toString("utf8"), RDKIT.loader.suffix);
  const mod = await import(new URL(RDKIT.loader.path, new URL("../../js/scientific/chem/", import.meta.url)).href);
  assert.deepEqual(Object.keys(mod), ["default"]);
  assert.equal(mod.default.toString(), factory.toString(), "an ES module whose default export is npm's initRDKitModule");
  const pkg = json(path.join(path.dirname(NPM_LOADER), "..", "package.json"));
  assert.equal(pkg.version, RDKIT.version);
  assert.equal(pkg.license, RDKIT.licence.spdx);
  assert.equal(json(path.join(STUDIO, "package.json")).devDependencies["@rdkit/rdkit"], RDKIT.version, "an exact devDependency");
  assert.equal(WASM.length, RDKIT.wasm.bytes);
  assert.equal(sha256(WASM), RDKIT.wasm.sha256, "npm's RDKit_minimal.wasm is the pinned build");
  assert.equal(RDKIT.wasm.url, `https://cdn.jsdelivr.net/npm/@rdkit/rdkit@${RDKIT.version}/dist/RDKit_minimal.wasm`);
  assert.match(readFileSync(path.join(CHEM, RDKIT.licence.path), "utf8"), /^BSD 3-Clause License\n/);
  for (const doc of [TABLES, CATALOGS]) assert.equal(doc.rdkit, RDKIT.release);
  assert.equal(FIXTURE.native.rdkit, RDKIT.release);
  assert.equal(FIXTURE.native.legacy_stereo_perception, true, "native's default, which loadRDKit sets in RDKit.js");
});

test("only the pinned wasm is compiled: other bytes, another size, another pin, or no bytes are refused first", async () => {
  const tampered = Buffer.from(WASM);
  tampered[1000] ^= 1;
  await assert.rejects(checkWasm(tampered), (e) => e instanceof ChemError && e.code === "bad_wasm" && /sha256/.test(e.message) && e.message.includes(RDKIT.wasm.sha256));
  await assert.rejects(checkWasm(WASM.subarray(0, 1000)), (e) => e.code === "bad_wasm" && /1000 bytes/.test(e.message));
  await assert.rejects(checkWasm(WASM, "ab".repeat(32)), (e) => e.code === "bad_wasm" && /pinned to RDKit\.js 2026\.9\.1/.test(e.message));
  await assert.rejects(checkWasm("RDKit_minimal.wasm"), (e) => e.code === "bad_wasm" && /as bytes/.test(e.message));
  assert.equal((await checkWasm(WASM.buffer.slice(WASM.byteOffset, WASM.byteOffset + WASM.byteLength))).byteLength, RDKIT.wasm.bytes);
  let called = 0;
  await assert.rejects(loadRDKit(tampered, { factory: () => { called++; } }), (e) => e.code === "bad_wasm");
  assert.equal(called, 0, "the loader is not even started for bytes that are not the pin");
  const asked = [];
  const fakeFetch = (bytes, status = 200) => async (url, init) => {
    asked.push([url, init.credentials]);
    return { ok: status === 200, status, arrayBuffer: async () => bytes };
  };
  await assert.rejects(fetchWasm("https://mirror.test/RDKit_minimal.wasm", { fetch: fakeFetch(null, 404) }), (e) => e.code === "bad_wasm" && /HTTP 404/.test(e.message));
  await assert.rejects(fetchWasm(undefined, { fetch: fakeFetch(tampered) }), (e) => e.code === "bad_wasm");
  assert.equal((await fetchWasm("https://mirror.test/x.wasm", { fetch: fakeFetch(WASM) })).byteLength, RDKIT.wasm.bytes);
  assert.deepEqual(asked.map((a) => a[0]), ["https://mirror.test/RDKit_minimal.wasm", RDKIT.wasm.url, "https://mirror.test/x.wasm"]);
  assert.ok(asked.every((a) => a[1] === "omit"), "no credentials go to the wasm host");
});

test("loadRDKit starts RDKit.js 2026.09.1 from the checked bytes, handed over through instantiateWasm", async () => {
  const seen = [];
  const spy = (opts) => { seen.push(Object.keys(opts)); return factory(opts); };
  const R = await loadRDKit(WASM, { factory: spy });
  assert.equal(R.version(), "2026.09.1");
  assert.ok(seen[0].includes("instantiateWasm"), "the compiled module is passed in, not fetched by the loader");
  assert.ok(!seen[0].includes("locateFile") && !seen[0].includes("wasmBinary"));
  await assert.rejects(loadRDKit(WASM, { factory: async () => ({ version: () => "2025.03.1" }) }), (e) => e.code === "rdkit" && /2025\.03\.1/.test(e.message));
  // the loader waits for instantiateWasm's callback for ever: a module that does not instantiate must fail, not hang
  const waitsForCallback = (opts) => new Promise((resolve) => { opts.instantiateWasm({}, () => resolve({ version: () => RDKIT.release })); });
  let timer;
  const hung = new Promise((_, reject) => { timer = setTimeout(() => reject(new Error("loadRDKit did not settle")), 10_000); });
  await assert.rejects(Promise.race([loadRDKit(WASM, { factory: waitsForCallback }), hung]),
    (e) => e instanceof ChemError && e.code === "rdkit" && /did not start/.test(e.message));
  clearTimeout(timer);
});

test("every molecule of the parity corpus: native's answers, or the refusal native's intermediates predict", async (t) => {
  const R = await RDKitModule();
  const P = new Preparer(R, { tables: TABLES, catalogs: CATALOGS });
  const problems = [];
  const counts = { ok: 0, refused: 0, error: 0 };
  const lastBit = {};
  const times = [];
  try {
    for (const m of FIXTURE.molecules) {
      const t0 = performance.now();
      const r = P.prepare(m.smiles);
      times.push(performance.now() - t0);
      const where = `${m.id} ${m.name} ${m.smiles}`;
      if (m.expect === "error") {
        if (r.ok || r.refused || r.error !== m.error) problems.push(`${where}: expected the error ${m.error}, got ${r.ok ? "ok" : r.error}`);
        else counts.error++;
        continue;
      }
      if (m.expect === "refused") {
        if (r.ok || r.refused?.missing !== m.refusal || r.refused?.need !== "runner") {
          problems.push(`${where}: expected a refusal (${m.refusal}), got ${r.ok ? "ok" : r.refused?.missing ?? r.error}`);
        } else counts.refused++;
        continue;
      }
      if (!r.ok) {
        problems.push(`${where}: not prepared (${r.error}) but native standardises it`);
        continue;
      }
      if (r.canonical !== m.canonical) problems.push(`${where}: canonical ${r.canonical}, native ${m.canonical}`);
      const ref = nativeRow(m);
      const differ = [];
      for (let k = 0; k < N_FEATURES; k++) if (!Object.is(r.features[k], ref[k])) differ.push(k);
      if (differ.length) problems.push(`${where}: ${differ.length} of the 2073 float32 features differ (first: column ${differ[0]})`);
      if (Buffer.from(r.bits).toString("hex") !== m.bits) problems.push(`${where}: the packed bits differ`);
      if (r.atoms_with_h !== m.atoms_with_h) problems.push(`${where}: atoms with H ${r.atoms_with_h}, native ${m.atoms_with_h}`);
      if (JSON.stringify(r.alerts) !== JSON.stringify(m.alerts)) problems.push(`${where}: alerts ${JSON.stringify(r.alerts)}, native ${JSON.stringify(m.alerts)}`);
      for (const k of FEATURE_KEYS) {
        const a = r.descriptors[k];
        const b = m.descriptors[k];
        if (b === null ? Number.isNaN(a) : a === b) continue;
        // V8's Math.exp/Math.log are not glibc's: BertzCT and qed may differ in the last bit of float64 (never in float32)
        if ((k === "BertzCT" || k === "qed") && b !== null && Math.abs(a - b) <= 4 * Number.EPSILON * Math.abs(b)) {
          lastBit[k] = (lastBit[k] || 0) + 1;
          continue;
        }
        problems.push(`${where}: ${k} ${a}, native ${b}`);
      }
      counts.ok++;
    }
  } finally {
    P.free();
  }
  const s = [...times].sort((a, b) => a - b);
  t.diagnostic(`identical ${counts.ok}, refused as expected ${counts.refused}, native's error ${counts.error}; float64 last-bit differences ${JSON.stringify(lastBit)}`);
  t.diagnostic(`ms per molecule: mean ${(s.reduce((a, b) => a + b, 0) / s.length).toFixed(2)}, p50 ${s[s.length >> 1].toFixed(2)}, p95 ${s[Math.floor(s.length * 0.95)].toFixed(2)}`);
  assert.deepEqual(problems, []);
  assert.deepEqual(counts, { ok: FIXTURE.counts.ok, refused: FIXTURE.counts.refused, error: FIXTURE.counts.error });
  assert.ok(FIXTURE.molecules.length >= 444, "the probe's 444 molecules and the ports' stress cases");
  assert.deepEqual(Object.keys(FIXTURE.refusals).sort(), [REFUSALS.metal, REFUSALS.reionizer, REFUSALS.ranking, REFUSALS.unsanitisable,
    "feature ports: bond type DATIVE", "feature ports: bond type UNSPECIFIED"].sort(), "every kind of refusal is exercised");
});

test("the FilterCatalog port: all 765 entries of the five catalogs, read back exactly, in rules.alerts' order", async () => {
  const R = await RDKitModule();
  const ctx = new Ctx(R);
  try {
    const cat = new AlertCatalog(ctx, CATALOGS);
    assert.deepEqual(cat.unported, []);
    assert.equal(cat.entries.length, 765);
    assert.deepEqual(CATALOGS.catalogs, ["PAINS_A", "PAINS_B", "PAINS_C", "BRENK", "NIH"]);
    assert.deepEqual([...new Set(cat.entries.map((e) => e.filterSet))], ["PAINS_A", "PAINS_B", "PAINS_C", "Brenk", "NIH"]);
    assert.ok(Object.keys(CATALOGS.source.files).every((f) => /^Code\/GraphMol\/FilterCatalog\//.test(f)) && CATALOGS.source.tag === "Release_2026_09_1");
    const mol = R.get_mol("Oc1ccccc1O");
    assert.deepEqual(cat.alerts(mol), [["PAINS_B", "catechol_A(92)"], ["Brenk", "catechol"], ["NIH", "ortho_hydroquinone"]]);
    mol.delete();
  } finally {
    ctx.free();
  }
});

test("alerts keep each (catalog, description) once, first first; an entry not read back exactly makes alerts refuse", async () => {
  const R = await RDKitModule();
  const ctx = new Ctx(R);
  const entry = (description, smarts, extra = {}) => ({ catalog: "BRENK", filter_set: "Brenk", description, smarts,
    written: R.get_qmol(smarts).get_smarts(), min_count: 1, how: "verbatim", ...extra });
  const doc = (entries, unported = []) => ({ schema: "tcmstudio.filter-catalogs/1", rdkit: "2026.09.1", catalogs: ["BRENK"], entries, unported });
  const mol = R.get_mol("OCCO");
  try {
    const dup = new AlertCatalog(ctx, doc([entry("hydroxy", "[OX2H]"), entry("carbon", "[#6]"), entry("hydroxy", "[OH]"), entry("nitrogen", "[#7]")]));
    assert.deepEqual(dup.alerts(mol), [["Brenk", "hydroxy"], ["Brenk", "carbon"]]);
    const counted = new AlertCatalog(ctx, doc([entry("two_or_more_O", "[#8]", { min_count: 2 }), entry("three_or_more_O", "[#8]", { min_count: 3 })]));
    assert.deepEqual(counted.alerts(mol), [["Brenk", "two_or_more_O"]], "SmartsMatcher's minimum count of unique matches");
    const misread = new AlertCatalog(ctx, doc([entry("hydroxy", "[OX2H]", { written: "[#8]" })]));
    assert.equal(misread.unported.length, 1);
    assert.match(misread.unported[0].reason, /RDKit\.js writes the parsed pattern as/);
    assert.throws(() => misread.alerts(mol), (e) => e.code === "refused" && e.missing === "FilterCatalog entries" && /incomplete/.test(e.message));
    const listed = new AlertCatalog(ctx, doc([entry("carbon", "[#6]")], [{ catalog: "BRENK", description: "x", reason: "the source SMARTS does not rebuild the native entry" }]));
    assert.throws(() => listed.alerts(mol), (e) => e.code === "refused");
    const unparsed = new AlertCatalog(ctx, doc([{ catalog: "BRENK", filter_set: "Brenk", description: "bad", smarts: "[C", written: "[C", min_count: 1 }]));
    assert.match(unparsed.unported[0].reason, /did not parse the SMARTS \[C/);
    const trapping = Object.assign(Object.create(ctx), { qmol: () => { throw new WebAssembly.RuntimeError("unreachable"); } });
    assert.throws(() => new AlertCatalog(trapping, doc([entry("carbon", "[#6]")])), WebAssembly.RuntimeError, "RDKit.js failing is not an unported entry");
  } finally {
    mol.delete();
    ctx.free();
  }
});

test("chem.standardize's errors and refusals: native's message for an invalid SMILES, the step named for a refusal", async () => {
  const R = await RDKitModule();
  const ctx = new Ctx(R);
  const data = new StandardizeData(TABLES);
  try {
    assert.throws(() => standardize(ctx, "C1CC", data), (e) => e.code === "invalid_smiles" && e.message === "'C1CC' is not a valid SMILES");
    assert.throws(() => standardize(ctx, "C\u200bC", data), (e) => e.code === "invalid_smiles" && e.message === "'C\\u200bC' is not a valid SMILES");
    assert.throws(() => standardize(ctx, "rdkitjson", data), (e) => e.code === "refused" && e.need === "runner" && e.missing === "SMILES input");
    assert.throws(() => standardize(ctx, "CC(=O)O[Na]", data), (e) => e.code === "refused" && e.missing === REFUSALS.metal && /MetalDisconnector/.test(e.message));
    assert.throws(() => standardize(ctx, "OC(=O)CCc1ccc([O-])cc1", data), (e) => e.missing === REFUSALS.reionizer);
    assert.throws(() => standardize(ctx, "C[N+](C)(C)CC(CC(=O)[O-])CC(=O)[O-]", data), (e) => e.missing === REFUSALS.ranking);
    const ok = standardize(ctx, "CS(=O)c1ccc(cc1)C(=O)[O-].[Na+]", data);
    assert.equal(ok.mol.get_smiles(), "C[S+]([O-])c1ccc(C(=O)O)cc1");
    assert.deepEqual(ok.steps, ["normalize:1", "fragment", "uncharge"], "the second Cleanup finds nothing more to normalise");
    ok.mol.delete();
  } finally {
    ctx.free();
  }
  // Python's repr(), which chem.standardize's message uses
  assert.equal(pyRepr("C1CC"), "'C1CC'");
  assert.equal(pyRepr("C'C"), "\"C'C\"");
  assert.equal(pyRepr("a'b\"c"), "'a\\'b\"c'");
  assert.equal(pyRepr("C\\C\n"), "'C\\\\C\\n'");
  assert.equal(pyRepr("C\u0007"), "'C\\x07'");
  // beyond Latin-1 too: what str.isprintable() rejects is escaped, the rest kept
  assert.equal(pyRepr("C\u200bC"), "'C\\u200bC'");
  assert.equal(pyRepr("C\u00a0C\u3000\u2028"), "'C\\xa0C\\u3000\\u2028'");
  assert.equal(pyRepr("C\u{e0001}"), "'C\\U000e0001'");
  assert.equal(pyRepr("C\u00e9\u4e2d\u{1f600} C"), "'C\u00e9\u4e2d\u{1f600} C'");
});

test("a molecule too large for RDKit.js is refused before it is read; what RDKit.js throws stops the module for good", async () => {
  assert.deepEqual(["CCO", "C[C@H](Cl)Br", "[nH]1cccc1", " CCO ethanol", "c1ccccc1 |c:1|", "[Na+].[Cl-]", "*CC", "[2H]OC", ""].map(smilesAtoms),
    [3, 4, 5, 3, 6, 2, 3, 3, 0]);
  const R = await RDKitModule();
  const P = new Preparer(R, { tables: TABLES, catalogs: CATALOGS });
  const real = P.ctx.RDKit;
  try {
    const big = P.prepare("C".repeat(MAX_ATOMS + 1));
    assert.deepEqual([big.ok, big.refused], [false, { need: "runner", missing: REFUSALS.size }]);
    assert.match(big.error, new RegExp(`${MAX_ATOMS + 1} atoms; RDKit\\.js takes at most ${MAX_ATOMS}`));
    assert.equal(P.prepare("C".repeat(MAX_ATOMS)).ok, true);
    assert.equal(P.prepare("C".repeat(1000)).refused.missing, REFUSALS.size, "where RDKit.js's get_mol itself traps");
    assert.equal(P.prepare("CC(=O)Oc1ccccc1C(=O)O").ok, true);
    // a bug in the ports is thrown as it is; a trap (or an exception RDKit.js let out) retires the module
    P.ctx.RDKit = Object.assign(Object.create(real), { get_mol: () => { throw new TypeError("a bug"); } });
    assert.throws(() => P.prepare("CCO"), TypeError);
    assert.equal(P.broken, null);
    P.ctx.RDKit = Object.assign(Object.create(real), { get_mol: () => { throw new WebAssembly.RuntimeError("memory access out of bounds"); } });
    assert.throws(() => P.prepare("CCO"), (e) => e instanceof ChemError && e.code === "rdkit" && /'CCO' \(memory access out of bounds\)/.test(e.message) &&
      /start the chemistry again/.test(e.message));
    P.ctx.RDKit = real;
    assert.throws(() => P.prepare("CCO"), (e) => e.code === "rdkit" && /stopped working earlier \(memory access out of bounds\)/.test(e.message),
      "nothing more is computed with a module whose memory a trap may have corrupted");
  } finally {
    P.ctx.RDKit = real;
    P.free();
  }
  // why the limit: on a module of its own, RDKit.js 2026.09.1 traps reading a 1,000-atom chain
  const own = await loadRDKit(WASM, { factory });
  assert.throws(() => own.get_mol("C".repeat(1000)), WebAssembly.RuntimeError);
});

test("the Morgan count port is checked against RDKit.js's bits on every molecule: a wrong invariant is refused", async () => {
  const R = await RDKitModule();
  const ctx = new Ctx(R);
  const broken = structuredClone(TABLES);
  broken.periodic[6].isotopes["13"] = 14.5; // the 13C atom's delta-mass invariant changes, and with it its environments' bits
  const mol = R.get_mol("[13CH3]CO");
  try {
    const good = features(ctx, mol, new FeatureTables(TABLES));
    assert.equal(good.row.length, 2073);
    assert.throws(() => features(ctx, mol, new FeatureTables(broken)), (e) => e.code === "refused" && e.missing === "Morgan count fingerprint port");
    const dative = R.get_mol("CN(C)(C)->O");
    assert.throws(() => features(ctx, dative, new FeatureTables(TABLES)), (e) => e.missing === "feature ports: bond type DATIVE");
    dative.delete();
  } finally {
    mol.delete();
    ctx.free();
  }
});

test("the prepared set on disk: prepared.json, little-endian features.f32 and bits.u8 for the ok rows, in order", async () => {
  const R = await RDKitModule();
  const P = new Preparer(R, { tables: TABLES, catalogs: CATALOGS });
  const smiles = ["CCO", "C1CC", "CC(=O)O[Na]", "", "c1ccccc1O"];
  const rows = smiles.map((s) => P.prepare(s));
  P.free();
  const ok = rows.filter((r) => r.ok);
  const features = new Float32Array(ok.length * N_FEATURES);
  const bits = new Uint8Array(ok.length * 256);
  ok.forEach((r, i) => { features.set(r.features, i * N_FEATURES); bits.set(r.bits, i * 256); });
  const files = preparedFiles({ smiles, rows, features, bits });
  const doc = JSON.parse(files.json);
  assert.equal(doc.schema, PREPARED_SCHEMA);
  assert.deepEqual(doc.engine, { name: "rdkit.js", version: "2026.09.1", detail: "RDKit.js 2026.09.1 MinimalLib + bioagent ports 1" });
  assert.deepEqual(doc.engine, { ...ENGINE });
  assert.equal(doc.n, 5);
  assert.equal(doc.smiles_sha256, sha256(smiles.join("\n")));
  assert.equal(smilesSha256(["a", "é"]), sha256("a\né"));
  const keys = ["ok", "error", "canonical", "descriptors", "atoms_with_h", "alerts"];
  assert.deepEqual(doc.rows.map((r) => Object.keys(r)), [keys, keys, [...keys, "need", "missing"], keys, keys]);
  assert.deepEqual(doc.rows.map((r) => r.ok), [true, false, false, true, true]);
  assert.deepEqual(doc.rows[1], { ok: false, error: "'C1CC' is not a valid SMILES", canonical: null, descriptors: null, atoms_with_h: null, alerts: null });
  assert.deepEqual([doc.rows[2].need, doc.rows[2].missing], ["runner", "MetalDisconnector"], "refused, not unreadable: the runner can");
  assert.match(doc.rows[2].error, /MetalDisconnector/);
  assert.equal(doc.rows[3].descriptors.BalabanJ, null, "NaN (an empty molecule's BalabanJ) is written as null");
  assert.deepEqual(Object.keys(doc.rows[0].descriptors), [...FEATURE_KEYS]);
  assert.equal(files.n_ok, 3);
  assert.equal(files.features.byteLength, 3 * 2073 * 4);
  assert.equal(files.bits.byteLength, 3 * 256);
  const view = new DataView(files.features);
  for (let i = 0; i < ok.length; i++) for (const k of [0, 1500, 2047, 2048, 2072]) assert.ok(Object.is(view.getFloat32((i * 2073 + k) * 4, true), ok[i].features[k]));
  assert.deepEqual(new Uint8Array(files.bits), bits);
  assert.throws(() => preparedFiles({ smiles, rows, features: features.subarray(1), bits }), /Float32Array of 3 × 2073/);
  assert.throws(() => preparedFiles({ smiles: smiles.slice(1), rows, features, bits }), /one row per input/);
  assert.throws(() => preparedFiles({ smiles, rows, features, bits: bits.subarray(5) }), /Uint8Array of 3 × 256/);
});
