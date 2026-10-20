// The ADMET browser jobs (js/scientific/pipelines/admet.js) through the job service, over fake-indexeddb, the in-memory
// OPFS of fixtures/fake-opfs.mjs, the page's project files, the real chemistry worker (chem.worker.js with npm's
// RDKit.js, the pinned build) and scripted job workers whose Python keeps tcmstudio.browser_jobs' ADMET contract
// (fixtures/admet-fakes.mjs): what is refused before anything is queued and why; admet.models' stages, the models in
// admet/models with their cards, an endpoint kept when its model still stands, the prepared sets the page wrote held to
// their molecules; pipeline.admet's stages, the similarities the page computed (CPU, a GPU double, the harness's forced
// software GPU) held to an exact search, the job's limitations, provenance and compute evidence; a resumed run reusing
// its chemistry; and the jobs runtime and catalog rules around them.
import "../fixtures/setup.mjs";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { test } from "node:test";
import { IDBFactory } from "fake-indexeddb";
import { fakeOpfs } from "../fixtures/fake-opfs.mjs";
import { RUNNER_ONLY } from "../fixtures/fakes.mjs";
import { admetPython, archiveBytes, chemWorkerClass, corpus, hex, npy, projectStore, scienceWorkerClass } from "../fixtures/admet-fakes.mjs";
import { calibrationFor, fakeScienceGPU } from "../science-fakes.mjs";
import { runnerOnly, entryState } from "../../js/core/catalog.js";
import { ScienceGPU } from "../../js/compute/science-gpu.js";
import { RDKIT } from "../../js/scientific/chem/rdkit.js";
import { OpfsStore } from "../../js/scientific/data/opfs-store.js";
import {
  ADMET, ADMET_HANDLERS, ENDPOINTS, LIMIT_PREDICTED, MAX_MOLECULES, TDC_ADMET_SHA256, admetModelsBuilt, admetNeedsRunner, computedLine,
  createAdmetHandlers, modelsDir, npyRows, readCards,
} from "../../js/scientific/pipelines/admet.js";
import { BrowserJobService, jobDir } from "../../js/scientific/runtime/job-manager.js";
import { BrowserJobsRuntime, jobCall, projectFilesResolver } from "../../js/scientific/runtime/jobs-runtime.js";
import { ScienceWorkerPool } from "../../js/scientific/runtime/worker-pool.js";

const BOOT = { schema: "tcmstudio.boot/1", pyodide: { version: "314.0.7", index_url: "https://cdn.example/pyodide/" }, bundle: { path: "b.tar.gz", sha256: "a".repeat(64) } };
const BOOT_RDKIT = { version: "2026.9.1", release: "2026.09.1", url: "https://mirror.example/RDKit_minimal.wasm", sha256: RDKIT.wasm.sha256, bytes: RDKIT.wasm.bytes, loader: "js/scientific/chem/vendor/RDKit_minimal-2026.9.1.mjs" };
const C = corpus();
const dec = new TextDecoder();

/** A job service over the fakes; handlers: createAdmetHandlers options beyond the fakes'. */
async function setup({ python = admetPython(), files = projectStore(), handlers = {}, fake = fakeOpfs(), datasetGroups = null, idb = new IDBFactory() } = {}) {
  const store = new OpfsStore({ root: fake.root, storage: fake.storage });
  const Worker = scienceWorkerClass(store, python);
  const ChemWorker = chemWorkerClass();
  const pool = new ScienceWorkerPool({ size: 1, Worker, workerUrl: "w.js", boot: BOOT, interruptGraceMs: 20 });
  const admet = createAdmetHandlers({ rdkit: BOOT_RDKIT, ChemWorker, chemWorkerUrl: "chem.worker.js", locks: null, calibration: async () => null, ...handlers });
  const svc = new BrowserJobService({
    indexedDB: idb, locks: null, pool, store, projectFiles: projectFilesResolver(files), handlers: admet, datasetGroups,
    navigator: { userAgent: "test", hardwareConcurrency: 4, deviceMemory: 8 },
  });
  assert.equal(await svc.start(), "ready");
  return { svc, store, files, python, Worker, ChemWorker, fake, idb, admet };
}

async function settle(svc, id, ms = 60000) {
  const t0 = Date.now();
  for (;;) {
    const job = await svc.get(id);
    if (["succeeded", "failed", "cancelled", "interrupted"].includes(job?.state)) return job;
    if (Date.now() - t0 > ms) throw new Error(`job ${id} did not end: ${job?.state}`);
    await new Promise((r) => setTimeout(r, 10));
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

/** An archive in the project, the models built from it (both endpoints) → the setup and the job. */
async function built(opts = {}) {
  const s = await setup(opts);
  const archive = s.files.add("p1", "admet_group.zip", archiveBytes(C.ok, { refusedSmiles: C.refused, invalid: C.invalid }));
  const job = await s.svc.submit("admet.models", { archive: "admet_group.zip", endpoints: TWO, max_iter: 100, ...(opts.params || {}) }, { project_id: "p1" });
  return { ...s, archive, job: await settle(s.svc, job.id) };
}

const TWO = ["caco2_wang", "hia_hou"];
const MOLECULES = () => `name,smiles\n${C.ok.slice(30, 36).map((s, i) => `m${i + 1},${s}`).join("\n")}\nbad,${C.invalid}\nrefused,${C.refused}\n`;

// ------------------------------------------------------------------------------------------------- refusals

test("needsRunner: an SDF or MOL file is the runner's, by its name; the catalog's runner_only rule says the same", () => {
  for (const m of ["mols.sdf", "MOLS.SDF.GZ", " ligand.mol "]) assert.match(admetNeedsRunner({ molecules: m }).message, /SDMolSupplier/);
  for (const m of ["mols.csv", "mols.smi", "CCO", "", undefined]) assert.equal(admetNeedsRunner({ molecules: m }), null);
  const entry = { execution: { browser_job: { runner_only: RUNNER_ONLY["pipeline.admet"] } } };
  for (const m of ["a.sdf", "a.sdf.gz", "a.mol", "a.csv", "a.smi", "CCO", "a.SDF"]) {
    assert.equal(Boolean(admetNeedsRunner({ molecules: m })), runnerOnly(entry, { molecules: m }).length > 0, m);
  }
  const [models, pipeline] = ADMET_HANDLERS;
  assert.equal(models.needsRunner({ archive: "x.zip" }), null, "a model build needs nothing of the runner");
  assert.equal(pipeline.needsRunner, admetNeedsRunner);
  const rt = new BrowserJobsRuntime({ service: { status: "ready", kinds: () => [{ kind: "pipeline.admet" }] } });
  assert.match(rt.needsRunner("pipeline.admet", { molecules: "x.sdf" }).hint, /native RDKit/);
});

test("pipeline.admet preflight: bad arguments, no models here, models missing for an endpoint, an SDF file, too many molecules", async () => {
  const { svc, files, store } = await setup();
  const [, pipeline] = (await setup()).admet;
  files.add("p1", "mols.csv", MOLECULES());
  files.add("p1", "named.sdf", "x");
  assert.equal((await refusedBy(pipeline, { molecules: "mols.csv", extra: 1 }, svc)).type, "bad_arguments");
  assert.match((await refusedBy(pipeline, { molecules: "mols.csv", endpoints: ["nope"] }, svc)).message, /no endpoint "nope"/);
  assert.match((await refusedBy(pipeline, {}, svc)).message, /molecules: a file of this project/);
  const none = await refusedBy(pipeline, { molecules: "mols.csv" }, svc);
  assert.deepEqual([none.type, none.need], ["unavailable", "dataset"]);
  assert.match(none.hint, /job\.admet\.models/);
  // one endpoint built, another asked for
  await store.writeJson([...modelsDir(store), "model_cards.json"], { caco2_wang: { model_file: "caco2_wang.pkl", domain_file: "caco2_wang_domain.npy" } });
  for (const f of ["caco2_wang.pkl", "caco2_wang_domain.npy"]) await store.writeBytes([...modelsDir(store), f], new Uint8Array([1]));
  assert.deepEqual((await admetModelsBuilt(store)).endpoints, ["caco2_wang"]);
  const missing = await refusedBy(pipeline, { molecules: "mols.csv", endpoints: ["caco2_wang", "herg"] }, svc);
  assert.equal(missing.need, "dataset");
  assert.match(missing.message, /herg is not built in this browser \(built here: caco2_wang\)/);
  assert.match(missing.hint, /endpoints: \["herg"\]/);
  assert.equal(await refusedBy(pipeline, { molecules: "mols.csv", endpoints: ["caco2_wang"] }, svc), null);
  assert.equal(await refusedBy(pipeline, { molecules: "CCO" }, svc), null, "one SMILES");
  const sdf = await refusedBy(pipeline, { molecules: "named.sdf" }, svc);
  assert.deepEqual([sdf.type, sdf.need], ["unavailable", "runner"]);
  assert.equal((await refusedBy(pipeline, { molecules: "gone.csv" }, svc)).type, "bad_arguments", "no such file, and a file's name is not a SMILES");
  assert.equal((await refusedBy(pipeline, { molecules: "C C" }, svc)).type, "bad_arguments", "no such file, and not a SMILES");
  // more molecules than a run here takes, counted as Python counts them (a header line is not a molecule)
  files.add("p1", "big.csv", `name,smiles\n${Array.from({ length: MAX_MOLECULES + 1 }, (_, i) => `m${i},CCO`).join("\n")}\n`);
  files.add("p1", "fits.csv", `name,smiles\n${Array.from({ length: MAX_MOLECULES }, (_, i) => `m${i},CCO`).join("\n")}\n`);
  files.add("p1", "fits.smi", `${Array.from({ length: MAX_MOLECULES }, (_, i) => `CCO m${i}`).join("\n")}\n`);
  const big = await refusedBy(pipeline, { molecules: "big.csv" }, svc);
  assert.deepEqual([big.type, big.need], ["unavailable", "memory"]);
  assert.ok(big.message.includes(`${(MAX_MOLECULES + 1).toLocaleString("en-US")} molecules, more than the ${MAX_MOLECULES.toLocaleString("en-US")}`), big.message);
  assert.equal(await refusedBy(pipeline, { molecules: "fits.csv" }, svc), null);
  assert.equal(await refusedBy(pipeline, { molecules: "fits.smi" }, svc), null, "a .smi file has no header");
});

test("pipeline.admet preflight: what the models cannot change is said first — an SDF named by its file id needs the runner, a missing file is a bad argument — before the models this browser lacks", async () => {
  const { svc, files, admet } = await setup();
  const [, pipeline] = admet;
  const sdf = files.add("p1", "ligands.sdf", "x");
  const byId = await refusedBy(pipeline, { molecules: sdf.id }, svc);
  assert.deepEqual([byId.type, byId.need], ["unavailable", "runner"], byId.message);
  assert.equal((await refusedBy(pipeline, { molecules: "gone.csv" }, svc)).type, "bad_arguments");
  files.add("p1", "big.csv", `name,smiles\n${Array.from({ length: MAX_MOLECULES + 1 }, (_, i) => `m${i},CCO`).join("\n")}\n`);
  assert.equal((await refusedBy(pipeline, { molecules: "big.csv" }, svc)).need, "memory");
  assert.equal((await refusedBy(pipeline, { molecules: "CCO" }, svc)).need, "dataset", "a run that can go ahead once the models are built");
});

test("admet.models preflight: no archive anywhere is need dataset; bad settings; a site whose RDKit.js pin is not this code's", async () => {
  const { svc, admet } = await setup();
  const [models] = admet;
  const none = await refusedBy(models, {}, svc);
  assert.deepEqual([none.type, none.need], ["unavailable", "dataset"]);
  assert.match(none.hint, /project file/);
  assert.match((await refusedBy(models, { archive: "missing.zip" }, svc)).message, /no file named "missing.zip"/);
  assert.match((await refusedBy(models, { max_iter: 5 }, svc)).message, /max_iter: between 10 and 5000/);
  assert.match((await refusedBy(models, { seed: 1.5 }, svc)).message, /seed: a whole number/);
  assert.match((await refusedBy(models, { rebuild: "yes" }, svc)).message, /rebuild: true or false/);
  assert.match((await refusedBy(models, { endpoints: ["caco2"] }, svc)).message, /no endpoint "caco2"/);
  const [skewed] = createAdmetHandlers({ rdkit: { ...BOOT_RDKIT, sha256: "0".repeat(64) }, ChemWorker: class {} });
  const skew = await refusedBy(skewed, { archive: "x.zip" }, svc);
  assert.equal(skew.type, "unavailable");
  assert.match(skew.message, /boot\.json pins RDKit\.js .* this page's chemistry is written for RDKit\.js 2026\.9\.1/);
  const [noWorker] = createAdmetHandlers({ ChemWorker: null });
  assert.match((await refusedBy(noWorker, { archive: "x.zip" }, svc)).message, /cannot start the chemistry worker/);
});

// ------------------------------------------------------------------------------------------------- admet.models

test("admet.models: the archive, its split, each endpoint's chemistry and model, verified; the models shared in admet/models", async () => {
  const { svc, store, python, archive, job, ChemWorker } = await built();
  assert.equal(job.state, "succeeded", JSON.stringify(job.error));
  assert.deepEqual(job.stages.map((s) => [s.id, s.status]), [
    ["archive", "succeeded"], ["split", "succeeded"],
    ["chemistry.caco2_wang", "succeeded"], ["train.caco2_wang", "succeeded"], ["chemistry.hia_hou", "succeeded"], ["train.hia_hou", "succeeded"],
    ["verify", "succeeded"],
  ], "two stages per endpoint asked for, in order, between the split and the check");
  // the wasm came from the URL boot.json names, checked against the pin by the worker
  assert.deepEqual([...new Set(ChemWorker.fetched)], [BOOT_RDKIT.url]);
  // the archive is kept by its digest, and the job records it as its input
  const sha = archive.sha256;
  assert.ok(await store.stat(["tcmstudio", "admet", "archive", `${sha.slice(0, 12)}.zip`]));
  assert.equal(job.inputs.archive.sha256, sha);
  assert.deepEqual(job.inputs.archive.source, { project_file: archive.id, name: "admet_group.zip" });
  // the chemistry stage's record: what was prepared, refused and not read
  const chem = job.stages.find((s) => s.id === "chemistry.caco2_wang");
  assert.deepEqual(chem.record.prepared.train_val, { n: 12, ok: 10, refused: 1, invalid: 1, missing: { [C.refusal]: 1 } });
  assert.match(chem.detail, /train_val: 10 of 12 prepared; 1 refused .*; 1 not read; test: 4 of 4 prepared/);
  // Python trained each endpoint from the prepared sets the page wrote (held to their molecules by the fake, as
  // PrecomputedEngine holds them), into a models directory of its own, told to train (this page decided)
  const trains = python.calls.filter((c) => c.name === "admet_train");
  assert.deepEqual(trains.map((c) => [c.args.endpoint, c.args.seed, c.args.max_iter, c.args.rebuild]), [["caco2_wang", 0, 100, true], ["hia_hou", 0, 100, true]]);
  assert.match(trains[0].args.models_dir, /\/train\/caco2_wang\/models$/);
  const cards = await readCards(store);
  assert.deepEqual(Object.keys(cards), ["caco2_wang", "hia_hou"], "the second endpoint's card merged after the first's");
  assert.deepEqual(cards.caco2_wang.chemistry, ADMET.chemistry);
  assert.equal(cards.caco2_wang.dropped_unstandardisable, 1);
  assert.equal(cards.caco2_wang.dropped_unreadable, 1);
  for (const e of ["caco2_wang", "hia_hou"]) {
    assert.equal(hex(await store.readBytes([...modelsDir(store), cards[e].model_file])), cards[e].model_sha256);
    assert.equal(hex(await store.readBytes([...modelsDir(store), cards[e].domain_file])), cards[e].domain_sha256);
  }
  // the artefacts are the shared files, read back at the digests the worker took
  assert.deepEqual(job.artefacts.map((a) => a.name).sort(), ["caco2_wang.pkl", "caco2_wang_domain.npy", "hia_hou.pkl", "hia_hou_domain.npy", "model_cards.json"]);
  assert.ok(job.artefacts.every((a) => a.shared && a.path[0] === "tcmstudio" && a.path[1] === "admet"));
  assert.ok(job.outcome.checks.includes("page_digests") && job.outcome.checks.includes("verify_models"));
  const receipt = (await svc.receipt(job.id)).doc;
  assert.equal(receipt.device.evidence, "verified");
  assert.equal(receipt.device.used, "cpu");
  // the prepared sets and the job's copies are gone once the models are in admet/models
  assert.equal(await store.exists([...jobDir(job.id), "prepared"]), false);
  assert.equal(await store.exists([...jobDir(job.id), "models"]), false);
  // verified with the archive's digest, and the result as the runner's job gives it
  const verify = python.calls.find((c) => c.name === "verify_pipeline");
  assert.deepEqual([verify.args.kind, verify.args.archive_sha256, verify.args.endpoints], ["admet.models", sha, TWO]);
  assert.equal(job.result.endpoints.caco2_wang.kept, false);
  assert.equal(job.result.endpoints.caco2_wang.metric, "mae");
  assert.match(job.result.endpoints.caco2_wang.card_sha256, /^[0-9a-f]{64}$/);
  assert.match(job.limitations[0], /Models built in this browser: chemistry RDKit\.js 2026\.09\.1 \+ ports, models scikit-learn 1\.8\.0 in Pyodide\. .*\(1 in all\)/);
  assert.match(job.limitations[1], /not the pinned TDC ADMET group's/);
});

test("admet.models again: an endpoint whose model still stands is kept (nothing prepared or trained); rebuild trains it again", async () => {
  const first = await built();
  assert.equal(first.job.state, "succeeded");
  const { svc, python, ChemWorker } = first;
  const before = python.calls.length;
  const again = await settle(svc, (await svc.submit("admet.models", { archive: "admet_group.zip", endpoints: TWO, max_iter: 100 }, { project_id: "p1" })).id);
  assert.equal(again.state, "succeeded", JSON.stringify(again.error));
  const ids = (j) => j.stages.filter((s) => s.reused).map((s) => s.id);
  assert.deepEqual(ids(again), ["chemistry.caco2_wang", "train.caco2_wang", "chemistry.hia_hou", "train.hia_hou"]);
  assert.match(again.stages.find((s) => s.id === "train.hia_hou").detail, /^kept: its model in this browser was built from the same archive with the same settings, scikit-learn 1\.8\.0 and chemistry/);
  assert.deepEqual(python.calls.slice(before).map((c) => c.name), ["admet_split", "verify_pipeline"], "no endpoint trained");
  assert.equal(again.result.endpoints.caco2_wang.kept, true);
  // the kept models are the job's artefacts all the same, read back at their cards' digests
  assert.deepEqual(again.artefacts.map((a) => a.name).sort(), ["caco2_wang.pkl", "caco2_wang_domain.npy", "hia_hou.pkl", "hia_hou_domain.npy"]);
  assert.equal(again.receipt?.sha256?.length, 64);
  // other settings are another model: trained again; so is every endpoint with rebuild
  const other = await settle(svc, (await svc.submit("admet.models", { archive: "admet_group.zip", endpoints: ["hia_hou"], max_iter: 200 }, { project_id: "p1" })).id);
  assert.deepEqual(ids(other), []);
  assert.equal((await readCards(first.store)).hia_hou.max_iter, 200);
  assert.equal((await readCards(first.store)).caco2_wang.max_iter, 100, "the other endpoint's card is left as it was");
  const n = python.calls.length;
  const rebuilt = await settle(svc, (await svc.submit("admet.models", { archive: "admet_group.zip", endpoints: ["caco2_wang"], max_iter: 100, rebuild: true }, { project_id: "p1" })).id);
  assert.equal(rebuilt.state, "succeeded");
  assert.deepEqual(python.calls.slice(n).map((c) => c.name), ["admet_split", "admet_train", "verify_pipeline"]);
  assert.ok(ChemWorker.instances.every((w) => w.terminated), "every job's chemistry worker was stopped when it ended");
});

test("admet.models: a model file changed in storage is not kept; the archive comes through the dataset workspace when none is given", async () => {
  const bytes = archiveBytes(C.ok);
  const sha = hex(bytes);
  const acquired = [];
  /** The dataset workspace of group "admet" over `fake`'s storage: acquire writes the file and answers `row`. */
  const workspace = (fake, row = {}) => {
    const store = new OpfsStore({ root: fake.root, storage: fake.storage });
    return {
      group: "admet",
      rawDir: () => ["tcmstudio", "datasets", "admet", "raw"],
      registry: async () => ({ files: [{ name: "tdc_admet_group.zip", url: "https://dataverse.harvard.edu/api/access/datafile/4426004", bytes: bytes.length, checksum: `sha256:${sha}`, relay: true }] }),
      acquire: async ({ names, transport }) => {
        acquired.push({ names, transport });
        await store.writeBytes(["tcmstudio", "datasets", "admet", "raw", "tdc_admet_group.zip"], bytes);
        return [{ file: names[0], status: "PASS", route: "relay", transport: "relay", bytes: bytes.length, sha256: sha, verified: "sha256", reason: "downloaded through the site's relay", ...row }];
      },
    };
  };
  const fake = fakeOpfs();
  const store = new OpfsStore({ root: fake.root, storage: fake.storage });
  const s = await setup({ fake, datasetGroups: { admet: workspace(fake) } });
  const job = await settle(s.svc, (await s.svc.submit("admet.models", { endpoints: ["caco2_wang"], max_iter: 100 })).id);
  assert.equal(job.state, "succeeded", JSON.stringify(job.error));
  assert.deepEqual(acquired, [{ names: ["tdc_admet_group.zip"], transport: "auto" }]);
  assert.deepEqual(job.inputs.archive.source, { dataset: "admet", file: "tdc_admet_group.zip", url: "https://dataverse.harvard.edu/api/access/datafile/4426004", route: "relay", transport: "relay", verified: "sha256" });
  assert.match(job.stages[0].detail, /tdc_admet_group\.zip through the site's relay, verified by sha256; SHA-256 [0-9a-f]{12}…, NOT the pinned TDC ADMET group's/);
  // a model whose file no longer has its card's digest does not stand
  const card = (await readCards(store)).caco2_wang;
  await store.writeBytes([...modelsDir(store), card.model_file], new TextEncoder().encode("changed"));
  const again = await settle(s.svc, (await s.svc.submit("admet.models", { endpoints: ["caco2_wang"], max_iter: 100 })).id);
  assert.equal(again.state, "succeeded");
  assert.deepEqual(again.stages.filter((x) => x.reused).map((x) => x.id), []);
  assert.equal(hex(await store.readBytes([...modelsDir(store), card.model_file])), (await readCards(store)).caco2_wang.model_sha256);
  // the copy of an archive the workspace fetched is held to the digest it was fetched with
  const other = fakeOpfs();
  const t = await setup({ fake: other, datasetGroups: { admet: workspace(other, { sha256: "f".repeat(64) }) } });
  const failed = await settle(t.svc, (await t.svc.submit("admet.models", { endpoints: ["caco2_wang"] })).id);
  assert.equal(failed.state, "failed");
  assert.equal(failed.error.type, "integrity");
  // a blocked download is the network's, said so
  const third = fakeOpfs();
  const u = await setup({ fake: third, datasetGroups: { admet: { ...workspace(third), acquire: async ({ names }) => [{ file: names[0], status: "BLOCKED", reason: "the relay answered HTTP 502" }] } } });
  const off = await settle(u.svc, (await u.svc.submit("admet.models", { endpoints: ["caco2_wang"] })).id);
  assert.deepEqual([off.state, off.error.type, off.error.need], ["failed", "unavailable", "network"]);
  assert.match(off.error.message, /HTTP 502/);
});

test("admet.models cancelled while it installs an endpoint's model: the shared models are not left without their cards", async () => {
  const s = await built();
  assert.equal(s.job.state, "succeeded", JSON.stringify(s.job.error));
  const before = await readCards(s.store);
  const copy = s.store.copy.bind(s.store);
  let job = null;
  // the cancel arrives as the new model_cards.json is being copied into admet/models
  s.store.copy = async (from, to, opts) => {
    if (job && to.at(-1) === "model_cards.json" && to.includes("models") && to.includes("admet")) {
      await s.svc.cancel(job.id);
      await new Promise((r) => setTimeout(r, 20));
    }
    return copy(from, to, opts);
  };
  job = await s.svc.submit("admet.models", { archive: "admet_group.zip", endpoints: ["caco2_wang"], max_iter: 100, rebuild: true }, { project_id: "p1" });
  const done = await settle(s.svc, job.id);
  assert.ok(["cancelled", "succeeded"].includes(done.state), done.state);
  const after = await readCards(s.store);
  assert.deepEqual(Object.keys(after).sort(), Object.keys(before).sort(), "every endpoint's card is still there");
  assert.deepEqual((await admetModelsBuilt(s.store))?.endpoints, TWO);
  for (const e of TWO) {
    assert.equal(hex(await s.store.readBytes([...modelsDir(s.store), after[e].model_file])), after[e].model_sha256, e);
    assert.equal(hex(await s.store.readBytes([...modelsDir(s.store), after[e].domain_file])), after[e].domain_sha256, e);
  }
});

test("admet.models: a project archive whose bytes are not the ones the project recorded is not used; one that is not a zip is refused by its own name", async () => {
  const s = await setup();
  s.files.add("p1", "notzip.zip", "this is not a zip");
  const notZip = await settle(s.svc, (await s.svc.submit("admet.models", { archive: "notzip.zip", endpoints: TWO }, { project_id: "p1" })).id);
  assert.deepEqual([notZip.state, notZip.error.type, notZip.error.need ?? null], ["failed", "refused", null]);
  assert.match(notZip.error.message, /notzip\.zip is not a zip archive/);
  assert.doesNotMatch(notZip.error.message, /[0-9a-f]{12}\.zip/, "not the name of this browser's copy");
  s.files.add("p1", "admet_group.zip", archiveBytes(C.ok), { sha256: "0".repeat(64) });
  const job = await settle(s.svc, (await s.svc.submit("admet.models", { archive: "admet_group.zip", endpoints: TWO }, { project_id: "p1" })).id);
  assert.deepEqual([job.state, job.error.type], ["failed", "integrity"]);
  assert.equal((await s.store.list(["tcmstudio", "admet", "archive"])).filter((x) => !x.name.startsWith(hex(new TextEncoder().encode("this is not a zip")).slice(0, 12))).length, 0, "nothing of it is kept");
});

// ------------------------------------------------------------------------------------------------- pipeline.admet

/** The models built, then a pipeline.admet run on the project's molecules with `handlers` (createAdmetHandlers). */
async function predicted({ handlers = {}, params = {}, molecules = MOLECULES(), python } = {}) {
  const s = await built({ handlers, ...(python ? { python } : {}) });
  assert.equal(s.job.state, "succeeded", JSON.stringify(s.job.error));
  s.files.add("p1", "mols.csv", molecules);
  const job = await settle(s.svc, (await s.svc.submit("pipeline.admet", { molecules: "mols.csv", ...params }, { project_id: "p1" })).id);
  return { ...s, run: job };
}

test("pipeline.admet on the CPU: read, chemistry, the domain searched with WebAssembly SIMD and checked by Python, predictions, verified", async () => {
  const { svc, store, python, run } = await predicted();
  assert.equal(run.state, "succeeded", JSON.stringify(run.error));
  assert.deepEqual(run.stages.map((s) => [s.id, s.status]), ["inputs", "read", "chemistry", "domain", "analysis", "verify"].map((id) => [id, "succeeded"]));
  assert.equal(run.inputs.molecules.sha256, hex(new TextEncoder().encode(MOLECULES())));
  assert.deepEqual(run.stages.find((s) => s.id === "chemistry").record.prepared, { n: 8, ok: 6, refused: 1, invalid: 1, missing: { [C.refusal]: 1 } });
  // the domain: the queries are the prepared rows' bits, the references each model's domain, the answers exact
  const domain = run.stages.find((s) => s.id === "domain");
  assert.equal(domain.compute.length, 2, "one kernel call per endpoint");
  assert.ok(domain.compute.every((f) => f.backend === "wasm-simd" && f.size.queries === 6 && f.accelerated === null));
  assert.match(domain.compute[0].reason, /no device check has been run on this device/);
  assert.match(domain.detail, /2 endpoints × 6 molecules: wasm-simd for 2/);
  const sim = await store.readJson([...jobDir(run.id), "similarity", "similarity.json"]);
  assert.equal(sim.schema, "tcmstudio.admet-similarity/1");
  assert.equal(sim.backend, "wasm-simd");
  assert.deepEqual(Object.keys(sim.endpoints), TWO);
  const call = python.calls.find((c) => c.name === "run_admet");
  assert.deepEqual(Object.keys(call.args).sort(), ["models_dir", "molecules_file", "out_dir", "prepared_dir", "similarity_dir"]);
  assert.match(call.args.models_dir, /\/models$/, "the models directory build_models writes (<cache>/models)");
  const record = await store.readJson([...jobDir(run.id), "out", "run.json"]);
  assert.deepEqual(record.similarity, { backend: "wasm-simd", checked: { caco2_wang: 6, hia_hou: 6 }, rejected: null }, "Python checked every query against its exact search and used them");
  // outputs named as the runner names them, the limitations as the runner's job gives them, the models' provenance
  assert.deepEqual(run.artefacts.filter((a) => ["table", "run", "limitations"].includes(a.name)).map((a) => a.name).sort(), ["limitations", "run", "table"]);
  assert.equal(run.limitations.at(-1), LIMIT_PREDICTED);
  const line = run.limitations.at(-2);
  assert.match(line, /^Computed in this browser: chemistry RDKit\.js 2026\.09\.1 \+ ports, models scikit-learn 1\.8\.0 built in this browser from the TDC archive \([0-9a-f]{12}\); similarity wasm-simd, checked by Python against the exact search\.$/);
  assert.ok(line.length <= 320);
  assert.ok(run.limitations[0].startsWith("Each predicted endpoint is a model estimate"), "the run's own limitations first");
  const cards = await readCards(store);
  assert.deepEqual(run.provenance.models, TWO.map((e) => ({ id: e, sha256: cards[e].model_sha256, archive_sha256: cards[e].archive_sha256, metric: cards[e].metric, test_score: cards[e].test_score })));
  const receipt = (await svc.receipt(run.id)).doc;
  assert.equal(receipt.device.evidence, "verified");
  assert.equal(receipt.device.used, "cpu");
  assert.deepEqual(receipt.provenance.models.map((m) => m.id), TWO);
  assert.equal(receipt.compute.stages.domain.backend, "wasm-simd");
  assert.equal(receipt.compute.stages.chemistry.backend, "rdkit.js");
  assert.match(receipt.device.note, /one thread each; in this page: chemistry rdkit\.js, domain wasm-simd; nothing ran on a GPU$/);
  assert.equal(run.device, "cpu");
  // the job_status the model reads: the predicted-result line among the limits, the models in the receipt's provenance
  const rt = new BrowserJobsRuntime({ service: svc });
  const env = await rt.call("job_status", { job_id: run.id });
  assert.ok(env.governance.limitations.includes(LIMIT_PREDICTED));
  assert.deepEqual(env.receipt.provenance.models.map((m) => m.id), TWO);
  assert.match(env.text, /Limits \(state these when you use this result\):/);
});

test("pipeline.admet on a GPU that this device measured faster: the GPU's checked result, the job accelerated; Python's answer the same", async () => {
  const gpu = fakeScienceGPU();
  // this device's CPU kernel took a minute at a size below the run's in its device check: the GPU's call is faster
  const calibration = await calibrationFor({ navigator: gpu.navigator, sizes: [{ queries: 1, refs: 5, accelerated: true, gpu_ms: 1, cpu_ms: 60000 }] });
  const cpu = await predicted();
  const { svc, store, run } = await predicted({ handlers: { gpu: new ScienceGPU(gpu), calibration: async () => calibration, navigator: gpu.navigator } });
  assert.equal(run.state, "succeeded", JSON.stringify(run.error));
  const domain = run.stages.find((s) => s.id === "domain");
  assert.ok(domain.compute.every((f) => f.backend === "webgpu" && f.accelerated === true && f.gpu.validated), JSON.stringify(domain.compute.map((f) => f.basis)));
  const sim = await store.readJson([...jobDir(run.id), "similarity", "similarity.json"]);
  assert.equal(sim.backend, "webgpu");
  // byte for byte what the CPU kernel wrote for the same molecules and models
  for (const e of TWO) {
    const a = await store.readBytes([...jobDir(run.id), "similarity", `${e}.u32`]);
    const b = await cpu.store.readBytes([...jobDir(cpu.run.id), "similarity", `${e}.u32`]);
    assert.deepEqual(a, b, e);
  }
  assert.equal((await store.readJson([...jobDir(run.id), "out", "run.json"])).similarity.rejected, null);
  assert.equal(run.device, "cpu+webgpu");
  assert.match(run.device_note, /domain computed on this browser's WebGPU adapter, each result checked on the CPU/);
  const receipt = (await svc.receipt(run.id)).doc;
  assert.equal(receipt.device.used, "cpu+webgpu");
  assert.equal(receipt.device.evidence, "accelerated");
  assert.equal(receipt.device.stages.domain, "webgpu");
  assert.match(run.limitations.at(-2), /similarity webgpu, checked by Python against the exact search \(the GPU's results also checked on the CPU here\)\.$/);
});

test("the harness's debug switch: the domain search forced onto a software GPU, never accelerated, its answers the CPU's; Studio's handlers never force it", async () => {
  const gpu = fakeScienceGPU({ info: { vendor: "google", architecture: "swiftshader", isFallbackAdapter: true } });
  const cpu = await predicted();
  const { run, store } = await predicted({ handlers: { debug: { forceSoftwareGpu: true }, navigator: gpu.navigator } });
  assert.equal(run.state, "succeeded", JSON.stringify(run.error));
  const facts = run.stages.find((s) => s.id === "domain").compute;
  assert.ok(facts.every((f) => f.backend === "webgpu" && f.forced === "webgpu" && f.accelerated === false && f.gpu.adapter.kind === "software"), JSON.stringify(facts[0]));
  assert.match(facts[0].basis, /forced by a test harness's debug switch: never acceleration/);
  for (const e of TWO) {
    assert.deepEqual(await store.readBytes([...jobDir(run.id), "similarity", `${e}.u32`]), await cpu.store.readBytes([...jobDir(cpu.run.id), "similarity", `${e}.u32`]), e);
  }
  assert.deepEqual(await store.readBytes([...jobDir(run.id), "out", "admet.tsv"]), await cpu.store.readBytes([...jobDir(cpu.run.id), "out", "admet.tsv"]));
  // without the switch a software adapter is refused, and the CPU computes
  const plain = await predicted({ handlers: { navigator: gpu.navigator, calibration: async () => calibrationFor({ navigator: gpu.navigator, info: { vendor: "google", architecture: "swiftshader", isFallbackAdapter: true }, sizes: [{ queries: 1, refs: 5, accelerated: true, gpu_ms: 1, cpu_ms: 60000 }] }) } });
  assert.ok(plain.run.stages.find((s) => s.id === "domain").compute.every((f) => f.backend === "wasm-simd"));
  // the jobs runtime builds its handlers without it (jobs-runtime.js createJobsRuntime); no parameter reaches it
  assert.equal((await refusedBy(ADMET_HANDLERS[1], { molecules: "CCO", debug: { forceSoftwareGpu: true } }, { store: null })).type, "bad_arguments");
  assert.doesNotMatch(readFileSync(new URL("../../js/scientific/runtime/jobs-runtime.js", import.meta.url), "utf8"), /forceSoftwareGpu/);
});

test("preference reference: no browser kernel searches the domain; Python's own search does, and the run says so", async () => {
  const { run, store, python } = await predicted({ handlers: { preference: () => "reference" } });
  assert.equal(run.state, "succeeded", JSON.stringify(run.error));
  assert.match(run.stages.find((s) => s.id === "domain").detail, /the reference implementation was asked for/);
  assert.equal(await store.exists([...jobDir(run.id), "similarity"]), false);
  assert.equal(python.calls.find((c) => c.name === "run_admet").args.similarity_dir, undefined);
  assert.match(run.limitations.at(-2), /similarity Python's own search on the CPU\.$/);
  // cpu: WebAssembly SIMD, whatever a calibration says
  const c = await predicted({ handlers: { preference: () => "cpu" } });
  assert.ok(c.run.stages.find((s) => s.id === "domain").compute.every((f) => f.backend === "wasm-simd" && f.reason === "CPU only was asked for"));
});

test("a GPU result that fails its check on the CPU is not used: the CPU computes it, the device is set aside, the job says so", async () => {
  // the known-answer test's four reduce passes answer right; every one after it is wrong in a way only a CPU check sees
  const gpu = fakeScienceGPU({ corrupt: { from: 4, kind: "arg" } });
  const calibration = await calibrationFor({ navigator: gpu.navigator, sizes: [{ queries: 1, refs: 5, accelerated: true, gpu_ms: 1, cpu_ms: 60000 }] });
  const { run } = await predicted({ handlers: { gpu: new ScienceGPU(gpu), calibration: async () => calibration, navigator: gpu.navigator } });
  assert.equal(run.state, "succeeded", JSON.stringify(run.error));
  const facts = run.stages.find((s) => s.id === "domain").compute;
  assert.ok(facts.every((f) => f.backend === "wasm-simd"), JSON.stringify(facts.map((f) => f.backend)));
  assert.match(facts[0].gpu.rejected, /^its result failed the check on the CPU/);
  assert.match(facts[1].gpu.error, /set aside/, "the next call does not use the device");
  assert.equal(run.device, "cpu");
});

test("similarities Python rejects (its own check of them failed): the outputs rest on its CPU search, so the GPU's result counts for nothing — no WebGPU, no acceleration", async () => {
  const gpu = fakeScienceGPU();
  const calibration = await calibrationFor({ navigator: gpu.navigator, sizes: [{ queries: 1, refs: 5, accelerated: true, gpu_ms: 1, cpu_ms: 60000 }] });
  const why = `query 3: its intersection and union with its nearest reference are not those of the fingerprints ${"(a long reason) ".repeat(6)}`;
  const { svc, run } = await predicted({ python: admetPython({ rejectSimilarity: why }), handlers: { gpu: new ScienceGPU(gpu), calibration: async () => calibration, navigator: gpu.navigator } });
  assert.equal(run.state, "succeeded", JSON.stringify(run.error));
  const facts = run.stages.find((s) => s.id === "domain").compute;
  assert.ok(facts.every((f) => f.backend === "webgpu" && f.gpu.validated), "the page's own check passed: it offered them");
  assert.ok(facts.every((f) => /^not used: Python rejected these similarities/.test(f.discarded) && f.accelerated === false), JSON.stringify(facts.map((f) => [f.discarded, f.accelerated])));
  assert.equal(run.device, "cpu", "the job's results rest on no GPU work");
  const receipt = (await svc.receipt(run.id)).doc;
  assert.equal(receipt.device.used, "cpu");
  assert.equal(receipt.device.evidence, "verified");
  assert.equal(receipt.device.stages.domain, "cpu");
  assert.equal(receipt.compute.accelerated, false);
  assert.deepEqual([receipt.compute.stages.domain.gpu.used, receipt.compute.stages.domain.discarded.calls], [0, 2]);
  assert.match(receipt.device.note, /the GPU was tried for domain and its results not used/);
  // the job's line says so, its reason cut short with an ellipsis, within the 320 characters
  const line = run.limitations.at(-2);
  assert.match(line, /similarity recomputed on the CPU by Python: the browser's was rejected \(webgpu: caco2_wang: query 3: .*…\)\.$/);
  assert.ok(line.length <= 320, line.length);
});

test("pipeline.admet resumed after it was interrupted: its chemistry and its analysis are reused while they re-hash", async () => {
  const { svc, store, python, run, idb, fake, files } = await predicted();
  assert.equal(run.state, "succeeded");
  // the page reloaded with the job recorded as running: interrupted, then resumed
  const rec = await svc.get(run.id);
  const req = idb.open("tcmstudio-jobs", 1);
  await new Promise((r) => { req.onsuccess = r; });
  const tx = req.result.transaction("jobs", "readwrite");
  tx.objectStore("jobs").put({ ...rec, state: "running" });
  await new Promise((r) => { tx.oncomplete = r; });
  req.result.close();
  const again = await setup({ idb, fake, files, python });
  assert.equal((await again.svc.get(run.id)).state, "interrupted");
  await again.svc.resume(run.id);
  const resumed = await settle(again.svc, run.id);
  assert.equal(resumed.state, "succeeded", JSON.stringify(resumed.error));
  assert.deepEqual(resumed.stages.filter((s) => s.reused).map((s) => s.id), ["chemistry", "analysis"]);
  assert.equal(python.calls.filter((c) => c.name === "run_admet").length, 1, "the analysis was not run again");
  void store;
});

test("pipeline.admet: an SDF the project names only by a file name the runner reads is refused; a SMILES runs; cancelled mid-chemistry", async () => {
  const s = await built();
  s.files.add("p1", "ligands.sdf", "x");
  await assert.rejects(s.svc.submit("pipeline.admet", { molecules: "ligands.sdf" }, { project_id: "p1" }), (err) => err.need === "runner");
  const one = await settle(s.svc, (await s.svc.submit("pipeline.admet", { molecules: "CC(=O)Oc1ccccc1C(=O)O", endpoints: ["hia_hou"] }, { project_id: "p1" })).id);
  assert.equal(one.state, "succeeded", JSON.stringify(one.error));
  assert.deepEqual(one.inputs, {}, "one SMILES is no file");
  assert.equal(s.python.calls.filter((c) => c.name === "admet_read").at(-1).args.molecules, "CC(=O)Oc1ccccc1C(=O)O");
  assert.deepEqual(s.python.calls.filter((c) => c.name === "run_admet").at(-1).args.endpoints, ["hia_hou"]);
  // a cancel while the chemistry runs stops the job and its chemistry worker
  s.files.add("p1", "many.csv", `name,smiles\n${Array.from({ length: 3000 }, (_, i) => `m${i},${C.ok[i % C.ok.length]}`).join("\n")}\n`);
  const job = await s.svc.submit("pipeline.admet", { molecules: "many.csv" }, { project_id: "p1" });
  for (let i = 0; i < 500 && (await s.svc.get(job.id)).stages.find((x) => x.id === "chemistry").status !== "running"; i++) await new Promise((r) => setTimeout(r, 10));
  await s.svc.cancel(job.id);
  const done = await settle(s.svc, job.id);
  assert.equal(done.state, "cancelled");
  assert.ok(s.ChemWorker.instances.at(-1).terminated);
});

// ------------------------------------------------------------------------------------------------- the pieces

test("npyRows reads np.save's uint8 rows (numpy's own file where Python has numpy) and refuses anything else", () => {
  const rows = new Uint8Array(3 * 256).map((_, i) => i % 251);
  const mine = npyRows(npy(rows, 3));
  assert.deepEqual([mine.n, mine.width], [3, 256]);
  assert.deepEqual(mine.data, rows);
  assert.equal(npyRows(npy(new Uint8Array(0), 0)).n, 0);
  assert.throws(() => npyRows(new Uint8Array(20)), /not a \.npy file/);
  const wrong = npy(rows, 3);
  wrong.set(new TextEncoder().encode("'<f4'"), 19);
  assert.throws(() => npyRows(wrong), /not uint8/);
  assert.throws(() => npyRows(npy(rows.subarray(0, 512), 3)), /holds 512 bytes of data, not 3 × 256/);
  const dir = mkdtempSync(path.join(tmpdir(), "tcms-npy-"));
  try {
    const out = path.join(dir, "d.npy");
    const code = "import sys, numpy as np; np.save(sys.argv[1], (np.arange(5 * 256) % 251).astype(np.uint8).reshape(5, 256))";
    let made = false;
    for (const py of [process.env.PYTHON, "python3"].filter(Boolean)) {
      try { execFileSync(py, ["-I", "-c", code, out], { stdio: "ignore", timeout: 60000 }); made = true; break; } catch { /* the next one */ }
    }
    if (made) {
      const theirs = npyRows(readFileSync(out));
      assert.deepEqual([theirs.n, theirs.width], [5, 256]);
      assert.deepEqual(new Uint8Array(theirs.data), new Uint8Array(5 * 256).map((_, i) => i % 251));
    }
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("the job's 'computed in this browser' line stays within the 320 characters the model reads, whatever it names", () => {
  const long = computedLine({ sklearn: "1.8.0", archives: [TDC_ADMET_SHA256, "f".repeat(64)], similarity: `recomputed on the CPU by Python: the browser's was rejected (${"x".repeat(90)})` });
  assert.ok(long.length <= 320, long.length);
  assert.match(computedLine({ sklearn: null, similarity: "x" }), /scikit-learn \(version not reported\)/);
});

/** What tcmstudio and bioagent say, when $PYTHON (or python3) imports them: null otherwise. */
function pythonFacts() {
  const code = [
    "import json",
    "from bioagent.admet.models import ENDPOINTS, TDC_ADMET_SHA256",
    "from tcmstudio import browser_jobs, kinds",
    "print(json.dumps({'endpoints': list(ENDPOINTS), 'sha': TDC_ADMET_SHA256, 'max': browser_jobs.ADMET_MOLECULES_MAX, 'max_iter': list(kinds.ADMET_MAX_ITER)}))",
  ].join("\n");
  for (const py of [process.env.PYTHON, "python3"].filter(Boolean)) {
    try { return JSON.parse(execFileSync(py, ["-I", "-c", code], { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"], timeout: 60000 })); } catch { /* the next one */ }
  }
  return null;
}
const PY = pythonFacts();

test("the page's ADMET constants are bioagent's and tcmstudio's: the endpoints, the pinned archive, the molecules a run takes, max_iter's bounds", { skip: PY ? false : "bioagent and tcmstudio are not importable by $PYTHON or python3" }, () => {
  assert.deepEqual([...ENDPOINTS], PY.endpoints);
  assert.equal(TDC_ADMET_SHA256, PY.sha);
  assert.equal(MAX_MOLECULES, PY.max);
  assert.deepEqual(PY.max_iter, [10, 5000]);
});

test("jobs runtime: run_pipeline admet and call_tool job.admet.models / job.pipeline.admet are job calls of the two kinds", () => {
  assert.deepEqual(jobCall("run_pipeline", { pipeline: "admet", arguments: { molecules: "mols.csv" } }), { kind: "pipeline.admet", params: { molecules: "mols.csv" } });
  assert.deepEqual(jobCall("call_tool", { tool: "job.pipeline.admet", arguments: { molecules: "CCO" } }), { kind: "pipeline.admet", params: { molecules: "CCO" } });
  assert.deepEqual(jobCall("call_tool", { tool: "job.admet.models", arguments: { endpoints: ["herg"] } }), { kind: "admet.models", params: { endpoints: ["herg"] } });
  assert.deepEqual(ADMET_HANDLERS.map((h) => h.kind), ["admet.models", "pipeline.admet"]);
  assert.deepEqual(ADMET_HANDLERS[0].stagesFor({ endpoints: ["herg"] }).map((s) => s.id), ["archive", "split", "chemistry.herg", "train.herg", "verify"]);
  assert.equal(ADMET_HANDLERS[0].stagesFor({}).length, 2 + 2 * 22 + 1);
});

test("jobs runtime: a refusal before queueing says its need and the reason the router's needs use (models: needs_dataset, SDF: needs_runner, too many molecules: too_large_for_browser)", async () => {
  const { svc, files } = await setup();
  await svc.store.writeJson([...modelsDir(svc.store), "model_cards.json"], { caco2_wang: { model_file: "caco2_wang.pkl", domain_file: "caco2_wang_domain.npy" } });
  files.add("p1", "big.csv", `name,smiles\n${Array.from({ length: MAX_MOLECULES + 1 }, (_, i) => `m${i},CCO`).join("\n")}\n`);
  files.add("p1", "named.sdf", "x");
  const rt = new BrowserJobsRuntime({ service: svc });
  const call = (molecules) => rt.call("run_pipeline", { pipeline: "admet", arguments: { molecules } }, { project_id: "p1" });
  const none = await call("CCO");
  assert.deepEqual([none.status, none.error.reason, none.error.requires[0].need], ["failed", "needs_dataset", "dataset"]);
  for (const f of ["caco2_wang.pkl", "caco2_wang_domain.npy"]) await svc.store.writeBytes([...modelsDir(svc.store), f], new Uint8Array([1]));
  const big = await call("big.csv");
  assert.deepEqual([big.status, big.error.type, big.error.reason, big.error.requires[0].need], ["failed", "unavailable", "too_large_for_browser", "memory"]);
  assert.match(big.error.hint, /local runner/);
  const sdf = await call("named.sdf");
  assert.deepEqual([sdf.error.reason, sdf.error.requires[0].need], ["needs_runner", "runner"]);
});

test("catalog readiness: pipeline.admet in this browser needs the models built here (the endpoints a call names, or any)", () => {
  const entry = { id: "job.pipeline.admet", exec: ["browser", "runner"], job: true, execution: { browser_job: { runner_only: RUNNER_ONLY["pipeline.admet"] } } };
  const ready = (admet, args) => entryState(entry, { browser: "ready", runner: "offline", jobs: { status: "ready", kinds: ["pipeline.admet"], admet_models: admet }, args }).readiness.browser;
  assert.equal(ready(null).state, "ready", "not read yet: nothing held against it");
  assert.deepEqual(ready([]).requires.map((r) => [r.need, r.names]), [["dataset", ["ADMET models"]]]);
  assert.match(ready([]).requires[0].remedy, /job\.admet\.models/);
  assert.equal(ready(["caco2_wang"]).state, "ready");
  assert.match(ready(["caco2_wang"], { endpoints: ["herg"] }).requires[0].detail, /for herg/);
});
