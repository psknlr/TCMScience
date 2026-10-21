// The evidence of a browser job (js/scientific/engines/evidence.js): the rung it reaches from what happened — verified
// only with a verify stage that ran and said it passed and the page's digests equal to the worker's, validated with the
// digests alone, dispatched when a worker computed but the checks did not pass, selected when no job worker computed
// anything — the assurance, and the receipt document.
import "../fixtures/setup.mjs";
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  JOB_RECEIPT_SCHEMA, bareSha256, buildJobReceipt, compareDigests, dedicatedEngines, dedicatedNote, digestChecks, engineList, jobEvidence,
  platformOf, runtimeOf, verifyStages,
} from "../../js/scientific/engines/evidence.js";

const A = "a".repeat(64);
const B = "b".repeat(64);
const C = "c".repeat(64);
const arts = [{ name: "compounds.tsv", path: ["out", "compounds.tsv"], bytes: 10, sha256: A }, { name: "targets.tsv", path: ["out", "targets.tsv"], bytes: 20, sha256: B }];
const worker = { "compounds.tsv": A, "targets.tsv": B };
const stage = (id, extra = {}) => ({ id, status: "succeeded", seconds: 1.5, worker: "science-1", reused: false, ...extra });
const workers = [{ id: "science-1", stages: ["research", "verify"], busy_ms: 1234.4, calls: 3, boot_ms: 900 }];

test("digests: a SHA-256 in any spelling; both readings compared for every output", () => {
  assert.equal(bareSha256(`sha256:${A.toUpperCase()}`), A);
  assert.equal(bareSha256("nope"), null);
  assert.equal(bareSha256(null), null);
  const same = compareDigests(arts, worker, { ...worker });
  assert.equal(same.worker_digests, true);
  assert.equal(same.page_digests, true);
  assert.deepEqual(digestChecks(same), ["worker_digests", "page_digests"]);
  const changed = compareDigests(arts, worker, { "compounds.tsv": A, "targets.tsv": C });
  assert.deepEqual(changed.mismatched, ["targets.tsv"]);
  assert.equal(changed.page_digests, false);
  assert.deepEqual(digestChecks(changed), ["worker_digests"]);
  const unread = compareDigests(arts, worker, { "compounds.tsv": A });
  assert.deepEqual(unread.unread, ["targets.tsv"]);
  assert.equal(unread.page_digests, false);
  const unrecorded = compareDigests(arts, { "compounds.tsv": A }, worker);
  assert.equal(unrecorded.worker_digests, false);
  assert.equal(unrecorded.page_digests, false, "the page cannot agree with a digest nobody recorded");
  const none = compareDigests([], {}, {});
  assert.equal(none.page_digests, false, "no outputs: nothing was hashed, nothing agrees");
});

test("the ladder: verified needs a verify stage that ran and agreeing digests; validated the digests alone", () => {
  const base = { state: "succeeded", artefacts: arts, workerDigests: worker, pageDigests: { ...worker }, workers };
  const verified = jobEvidence({ ...base, stages: [stage("research"), stage("verify", { verified: true })] });
  assert.equal(verified.evidence, "verified");
  assert.equal(verified.assurance, "outputs_verified");
  assert.deepEqual(verified.verify_stages, ["verify"]);
  // a stage that says it verified counts, whatever its id; a name alone proves nothing
  assert.equal(jobEvidence({ ...base, stages: [stage("recompute", { verified: true })] }).evidence, "verified");
  assert.equal(jobEvidence({ ...base, stages: [stage("research"), stage("verify")] }).evidence, "validated");
  // a verify stage reused from a checkpoint did not run this time
  assert.equal(jobEvidence({ ...base, stages: [stage("research"), stage("verify", { reused: true, verified: true })] }).evidence, "validated");
  assert.deepEqual(verifyStages([stage("verify", { status: "failed", verified: true })]), []);
  const validated = jobEvidence({ ...base, stages: [stage("research")] });
  assert.equal(validated.evidence, "validated");
  assert.equal(validated.assurance, "outputs_verified");
  // a digest that differs: the work ran, the checks did not pass
  const mismatch = jobEvidence({ ...base, pageDigests: { ...worker, "targets.tsv": C }, stages: [stage("research"), stage("verify")] });
  assert.equal(mismatch.evidence, "dispatched");
  assert.equal(mismatch.assurance, "none");
  // a failed job is never verified, whatever its digests
  assert.equal(jobEvidence({ ...base, state: "failed", stages: [stage("research"), stage("verify")] }).evidence, "dispatched");
  // an expected output that is missing keeps the rung but not the assurance
  const missing = jobEvidence({ ...base, stages: [stage("research"), stage("verify", { verified: true })], missing: ["report.html"] });
  assert.equal(missing.evidence, "verified");
  assert.equal(missing.assurance, "none");
  // nothing computed on the CPU: it was only selected
  const nothing = jobEvidence({ state: "failed", stages: [{ id: "plan", status: "failed", worker: null }], artefacts: [], workers: [] });
  assert.equal(nothing.evidence, "selected");
  assert.equal(nothing.assurance, "none");
  // a stage that names its worker is work that ran, even when no lease was recorded
  assert.equal(jobEvidence({ state: "cancelled", stages: [{ id: "build", status: "cancelled", worker: "science-2" }] }).evidence, "dispatched");
  // outputs the page wrote itself agree with their digests, but no job worker computed them: no rung above selected
  const pageOnly = jobEvidence({ ...base, workers: [], stages: [{ id: "write", status: "succeeded", worker: null, reused: false, verified: true }] });
  assert.equal(pageOnly.evidence, "selected");
  assert.equal(pageOnly.assurance, "outputs_verified", "the hashes still agree");
  // a worker only started for the job (no operation sent, no stage) computed nothing for it
  assert.equal(jobEvidence({ state: "failed", stages: [], workers: [{ id: "science-1", stages: [], calls: 0, busy_ms: 0, boot_ms: 900 }] }).evidence, "selected");
});

test("runtime and platform come only from what the workers reported", () => {
  const infos = [{ runtime: "pyodide-314.0.7 / CPython 3.14.2", platform: "emscripten", machine: "wasm32" }, { runtime: "pyodide-314.0.7 / CPython 3.14.2", platform: "emscripten", machine: "wasm32" }];
  assert.equal(runtimeOf(infos), "pyodide-314.0.7 / CPython 3.14.2");
  assert.equal(platformOf(infos), "emscripten/wasm32");
  assert.equal(runtimeOf([]), null);
  assert.equal(platformOf([{}]), null);
});

test("the receipt: the runner's fields where they mean the same, placement browser, both digests, device evidence", () => {
  const job = {
    id: "bj-0123456789abcdef", kind: "research.run", state: "succeeded", project_id: "p1", submission_id: null,
    started_at: "2026-10-19T10:00:00.000Z", finished_at: "2026-10-19T10:02:03.500Z", error: null, outcome: { complete: true },
    stages: [stage("prepare", { worker: "science-1" }), stage("research"), stage("verify", { verified: true }), { id: "extra", status: "succeeded", worker: null, reused: true, seconds: 0 }],
    artefacts: arts,
  };
  const doc = buildJobReceipt({
    job, workerDigests: worker, pageDigests: { ...worker }, workers, runtime: "pyodide-314.0.7 / CPython 3.14.2",
    platform: { python: "emscripten/wasm32" }, resources: { wall_s: 123.5, busy_s: 1.234, memory: { wasm_peak_bytes: 1 << 30, measured_by: "x" } },
    checks: ["np_exports.verify"], problems: [], limitations: ["Predicted ≠ measured"], paramsSha256: C, expected: ["compounds.tsv"], missing: [],
  });
  assert.equal(doc.schema, JOB_RECEIPT_SCHEMA);
  assert.equal(doc.job_id, job.id);
  assert.equal(doc.placement, "browser");
  assert.equal(doc.runtime, "pyodide-314.0.7 / CPython 3.14.2");
  assert.deepEqual(doc.platform, { python: "emscripten/wasm32" });
  assert.equal(doc.duration_s, 123.5);
  assert.equal(doc.params_sha256, C);
  assert.deepEqual(doc.device.assigned, "cpu");
  assert.deepEqual(doc.device.used, "cpu");
  assert.equal(doc.device.evidence, "verified");
  assert.match(doc.device.note, /1 dedicated Web Worker, one thread each; nothing ran on a GPU/);
  assert.deepEqual(doc.device.stages, { prepare: "cpu", research: "cpu", verify: "cpu" }, "a reused stage computed nothing");
  assert.deepEqual(doc.workers, [{ id: "science-1", stages: ["research", "verify"], busy_ms: 1234, calls: 3, boot_ms: 900 }]);
  assert.deepEqual(doc.artefacts[0], { name: "compounds.tsv", path: "out/compounds.tsv", bytes: 10, sha256: A });
  assert.deepEqual(doc.digests.worker, worker);
  assert.deepEqual(doc.digests.page, worker);
  assert.deepEqual(doc.checks, ["np_exports.verify", "worker_digests", "page_digests"]);
  assert.equal(doc.assurance, "outputs_verified");
  assert.equal(doc.complete, true);
  assert.deepEqual(doc.stages.map((s) => [s.name, s.device, s.reused, s.verified]), [["prepare", "cpu", false, false], ["research", "cpu", false, false], ["verify", "cpu", false, true], ["extra", null, true, false]]);
  assert.deepEqual(doc.device.verify_stages, ["verify"]);
  assert.equal(doc.engine, null);
  assert.deepEqual(doc.limitations, ["Predicted ≠ measured"]);
  assert.equal(JSON.parse(JSON.stringify(doc)).resources.memory.wasm_peak_bytes, 1 << 30, "it is plain JSON");

  // a failed job: no checks claimed (as the runner writes it), its error and problems kept
  const failed = buildJobReceipt({ job: { ...job, state: "failed", error: { type: "failed", message: "verify said FAIL" } }, workerDigests: worker, pageDigests: worker, workers, checks: ["np_exports.verify"], problems: ["p"] });
  assert.deepEqual(failed.checks, []);
  assert.equal(failed.error, "verify said FAIL");
  assert.equal(failed.device.evidence, "dispatched");
  assert.equal(failed.assurance, "none");
  // nothing ran: the CPU is assigned, not used
  const idle = buildJobReceipt({ job: { id: "bj-1", kind: "research.run", state: "cancelled", stages: [], artefacts: [] } });
  assert.equal(idle.device.used, null);
  assert.equal(idle.device.evidence, "selected");
  assert.equal(idle.duration_s, null);
});

test("an engine of the job's own in dedicated workers (pipeline.dock's AutoDock Vina): the receipt's engine, the device note with its workers, not the page's", () => {
  const W = "9".repeat(64);
  const vina = { name: "AutoDock Vina", version: "1.2.7", build: "wasm", wasm_sha256: `sha256:${W}`, stages: ["dock"] };
  assert.deepEqual(engineList(vina), [{ id: "AutoDock Vina", version: "1.2.7", stages: ["dock"], name: "AutoDock Vina", build: "wasm", wasm_sha256: W }]);
  assert.deepEqual(engineList([{ id: "x" }, null, { version: "1" }]), [{ id: "x", version: null, stages: [] }], "an engine with no name is none");
  assert.deepEqual(engineList(null), []);
  const fact = (worker, extra = {}) => ({ op: "vina.dock", backend: "vina-wasm", placement: "browser", where: "worker", worker, gpu: null, accelerated: null, ms: 10, ...extra });
  const dock = { id: "dock", status: "succeeded", seconds: 3, worker: null, reused: false, compute: [fact("vina-1"), fact("vina-2"), fact("vina-1"), fact("vina-3")] };
  const stages = [stage("read"), dock, stage("analysis"), stage("verify", { verified: true })];
  assert.deepEqual(dedicatedEngines(stages, vina), [{ engine: "AutoDock Vina 1.2.7 (WebAssembly)", workers: 3, stages: ["dock"], backends: ["vina-wasm"] }]);
  assert.equal(dedicatedNote(dedicatedEngines(stages, vina)[0]), "AutoDock Vina 1.2.7 (WebAssembly) in 3 dedicated Web Workers, one thread each");
  assert.equal(dedicatedNote({ engine: "E (WebAssembly)", workers: 1 }), "E (WebAssembly) in 1 dedicated Web Worker, one thread each");
  assert.equal(dedicatedNote({ engine: "E (WebAssembly)", workers: null }), "E (WebAssembly) in dedicated Web Workers, one thread each");
  // not this engine's stage, a reused stage, a native build, discarded facts: none counts
  assert.deepEqual(dedicatedEngines(stages, { ...vina, stages: ["other"] }), []);
  assert.deepEqual(dedicatedEngines([{ ...dock, reused: true }], vina), []);
  assert.deepEqual(dedicatedEngines(stages, { ...vina, build: "native" }), []);
  assert.deepEqual(dedicatedEngines([{ ...dock, compute: dock.compute.map((f) => ({ ...f, discarded: "not used" })) }], vina), []);
  const job = {
    id: "bj-0123456789abcdef", kind: "pipeline.dock", state: "succeeded", started_at: "2026-10-19T10:00:00.000Z", finished_at: "2026-10-19T10:01:00.000Z",
    stages, artefacts: arts, engine: [vina], outcome: { complete: true },
  };
  const doc = buildJobReceipt({ job, workerDigests: worker, pageDigests: { ...worker }, workers, runtime: "pyodide-314.0.7 / CPython 3.14.2" });
  assert.deepEqual(doc.engine, [{ id: "AutoDock Vina", version: "1.2.7", stages: ["dock"], name: "AutoDock Vina", build: "wasm", wasm_sha256: W }]);
  assert.equal(doc.device.note, "computed on this device's CPU: CPython compiled to WebAssembly (Pyodide) in 1 dedicated Web Worker, one thread each; AutoDock Vina 1.2.7 (WebAssembly) in 3 dedicated Web Workers, one thread each; nothing ran on a GPU");
  assert.doesNotMatch(doc.device.note, /in this page/, "Vina is named as the engine, not as the page's own computing");
  assert.equal(doc.device.evidence, "verified");
  assert.equal(doc.device.stages.dock, "cpu");
  assert.equal(doc.compute.stages.dock.backend, "vina-wasm");
  // the engine can be given apart from the job, as the receipt's argument
  assert.equal(buildJobReceipt({ job: { ...job, engine: undefined }, engine: vina, workerDigests: worker, pageDigests: worker, workers }).engine[0].id, "AutoDock Vina");
  // without its own engine named, a stage's backend is what the page computed, as before
  const plain = buildJobReceipt({ job: { ...job, engine: undefined }, workerDigests: worker, pageDigests: worker, workers });
  assert.equal(plain.engine, null);
  assert.match(plain.device.note, /one thread each; in this page: dock vina-wasm; nothing ran on a GPU$/);
});
