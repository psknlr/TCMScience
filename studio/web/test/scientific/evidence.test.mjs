// The evidence of a browser job (js/scientific/engines/evidence.js): the rung it reaches from what happened — verified
// only with a verify stage that ran and the page's digests equal to the worker's, validated with the digests alone,
// dispatched when a worker computed but the checks did not pass, selected when nothing computed — the assurance, and
// the receipt document. Also the memory budget helpers (js/scientific/runtime/memory.js).
import "../fixtures/setup.mjs";
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  JOB_RECEIPT_SCHEMA, bareSha256, buildJobReceipt, compareDigests, digestChecks, jobEvidence, platformOf, runtimeOf, verifyStages,
} from "../../js/scientific/engines/evidence.js";
import { ASSUMED_DEVICE_GB, WASM32_MAX_BYTES, measureMemory, memoryBudget } from "../../js/scientific/runtime/memory.js";

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
  const verified = jobEvidence({ ...base, stages: [stage("research"), stage("verify")] });
  assert.equal(verified.evidence, "verified");
  assert.equal(verified.assurance, "outputs_verified");
  assert.deepEqual(verified.verify_stages, ["verify"]);
  // a stage that says it verified counts as well, whatever its id
  assert.equal(jobEvidence({ ...base, stages: [stage("recompute", { verified: true })] }).evidence, "verified");
  // a verify stage reused from a checkpoint did not run this time
  assert.equal(jobEvidence({ ...base, stages: [stage("research"), stage("verify", { reused: true })] }).evidence, "validated");
  assert.deepEqual(verifyStages([stage("verify", { status: "failed" })]), []);
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
  const missing = jobEvidence({ ...base, stages: [stage("research"), stage("verify")], missing: ["report.html"] });
  assert.equal(missing.evidence, "verified");
  assert.equal(missing.assurance, "none");
  // nothing computed on the CPU: it was only selected
  const nothing = jobEvidence({ state: "failed", stages: [{ id: "plan", status: "failed", worker: null }], artefacts: [], workers: [] });
  assert.equal(nothing.evidence, "selected");
  assert.equal(nothing.assurance, "none");
  // a stage that names its worker is work that ran, even when no lease was recorded
  assert.equal(jobEvidence({ state: "cancelled", stages: [{ id: "build", status: "cancelled", worker: "science-2" }] }).evidence, "dispatched");
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
    stages: [stage("prepare", { worker: "science-1" }), stage("research"), stage("verify"), { id: "extra", status: "succeeded", worker: null, reused: true, seconds: 0 }],
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
  assert.deepEqual(doc.stages.map((s) => [s.name, s.device, s.reused]), [["prepare", "cpu", false], ["research", "cpu", false], ["verify", "cpu", false], ["extra", null, true]]);
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

test("memoryBudget: the reported device memory (or an assumption it names), half for the job, wasm32's ceiling", () => {
  const b = memoryBudget({ deviceMemory: 8, workers: 2 });
  assert.equal(b.device_gb, 8);
  assert.equal(b.assumed_gb, null);
  assert.equal(b.total_bytes, 4 * 2 ** 30);
  assert.equal(b.per_worker_bytes, 2 * 2 ** 30);
  assert.equal(b.source, "navigator.deviceMemory");
  const assumed = memoryBudget({ deviceMemory: undefined });
  assert.equal(assumed.device_gb, null);
  assert.equal(assumed.assumed_gb, ASSUMED_DEVICE_GB);
  assert.match(assumed.note, /not reported here; 2 GB is assumed/);
  const measured = memoryBudget({ deviceMemory: 4, measured: { bytes: 2 ** 29 } });
  assert.equal(measured.measured_bytes, 2 ** 29);
  assert.equal(measured.total_bytes, 2 * 2 ** 30 - 2 ** 29);
  assert.match(measured.note, /this page holds now \(measured\)/);
  const big = memoryBudget({ deviceMemory: 64, workers: 1 });
  assert.equal(big.per_worker_bytes, WASM32_MAX_BYTES, "no worker holds more than 4 GiB");
});

test("measureMemory: only on an isolated page that has the call; otherwise null with the reason", async () => {
  const off = await measureMemory({ isolated: false, performance: { measureUserAgentSpecificMemory: async () => ({ bytes: 1 }) } });
  assert.equal(off.bytes, null);
  assert.match(off.reason, /not cross-origin isolated/);
  const missing = await measureMemory({ isolated: true, performance: {} });
  assert.equal(missing.bytes, null);
  assert.match(missing.reason, /no performance\.measureUserAgentSpecificMemory/);
  const ok = await measureMemory({ isolated: true, performance: { measureUserAgentSpecificMemory: async () => ({ bytes: 123456, breakdown: [] }) } });
  assert.deepEqual(ok, { bytes: 123456, measured_by: "performance.measureUserAgentSpecificMemory", reason: null });
  const slow = await measureMemory({ isolated: true, timeoutMs: 10, performance: { measureUserAgentSpecificMemory: () => new Promise(() => {}) } });
  assert.equal(slow.bytes, null);
  assert.match(slow.reason, /did not answer/);
  const refused = await measureMemory({ isolated: true, performance: { measureUserAgentSpecificMemory: async () => { throw Object.assign(new Error("no"), { name: "SecurityError" }); } } });
  assert.match(refused.reason, /SecurityError: no/);
});
