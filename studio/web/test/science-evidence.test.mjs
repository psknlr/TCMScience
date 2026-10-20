// The compute facts in a browser job's evidence (js/scientific/engines/evidence.js): a stage carries its kernel calls'
// facts (compute/science-scheduler.js), the receipt says where each stage computed and with what adapter and
// calibration, and the job reaches "accelerated" only when a stage's GPU work was physical, verified, checked and
// calibrated faster — decided again from the facts, never taken from them.
import "./fixtures/setup.mjs";
import assert from "node:assert/strict";
import { test } from "node:test";
import { wordsFromBits } from "../js/compute/science-cpu.js";
import { ScienceGPU } from "../js/compute/science-gpu.js";
import { runTanimoto } from "../js/compute/science-scheduler.js";
import { JOB_EVIDENCE, buildJobReceipt, callGpuRung, computeEvidence, jobEvidence, stageCompute } from "../js/scientific/engines/evidence.js";
import { calibrationFor, fakeScienceGPU } from "./science-fakes.mjs";

const A = "a".repeat(64);
const arts = [{ name: "admet.tsv", path: ["out", "admet.tsv"], bytes: 10, sha256: A }];
const digests = { "admet.tsv": A };
const workers = [{ id: "science-1", stages: ["analysis", "verify"], busy_ms: 10, calls: 2, boot_ms: 5 }];
const stage = (id, extra = {}) => ({ id, status: "succeeded", seconds: 1, worker: null, reused: false, ...extra });
const job = (stages) => ({ id: "bj-1", kind: "pipeline.admet", state: "succeeded", stages, artefacts: arts, outcome: { complete: true } });
const base = { state: "succeeded", artefacts: arts, workerDigests: digests, pageDigests: { ...digests }, workers };

const r = (() => { let s = 5; return () => (s = (Math.imul(s, 1664525) + 1013904223) >>> 0) / 2 ** 32; })();
const fps = (n) => wordsFromBits(Array.from({ length: n }, () => { const s = []; for (let b = 0; b < 2048; b++) if (r() < 0.025) s.push(b); return s; }));
const Q = fps(40), R = fps(600);
const SIZES = [{ queries: 32, refs: 600, accelerated: true, gpu_ms: 2, cpu_ms: 9 }];

/** Real facts: runTanimoto on a device double with a calibration that measured it faster (or as `options` say). */
async function facts({ info, corrupt, sizes = SIZES, preference = "auto" } = {}) {
  const gpu = fakeScienceGPU({ info, corrupt });
  const calibration = await calibrationFor({ navigator: gpu.navigator, info, sizes });
  return (await runTanimoto({ queries: Q, refs: R, gpu: new ScienceGPU({ ...gpu, allowSoftware: true }), calibration, navigator: gpu.navigator, preference })).facts;
}

const stagesWith = (compute, where = "record") => [
  stage("chemistry"), stage("domain", where === "record" ? { record: { compute } } : { compute }),
  stage("analysis", { worker: "science-1" }), stage("verify", { worker: "science-1", verified: true }),
];

test("the ladder ends at accelerated; a stage's facts come from stage.compute or the stage context's record()", async () => {
  assert.deepEqual(JOB_EVIDENCE, ["selected", "dispatched", "validated", "verified", "accelerated"]);
  const f = await facts();
  assert.equal(stageCompute({ compute: f }).length, 1);
  assert.equal(stageCompute({ record: { compute: [f, f] } }).length, 2);
  assert.deepEqual(stageCompute({ record: { other: 1 } }), []);
  assert.deepEqual(stageCompute({ compute: [{ nope: true }] }), [], "a fact without a backend is not one");
  assert.equal(computeEvidence([stage("x")]), null, "no facts, no compute section");
});

test("a stage whose GPU work was physical, verified, checked and calibrated faster: the job is accelerated", async () => {
  const f = await facts();
  assert.equal(f.backend, "webgpu");
  for (const where of ["record", "compute"]) {
    const stages = stagesWith([f, { ...f }], where);
    const ev = jobEvidence({ ...base, stages });
    assert.equal(ev.evidence, "accelerated", where);
    assert.equal(ev.compute.accelerated, true);
    assert.deepEqual(ev.compute.gpu_stages, ["domain"]);
  }
  const doc = buildJobReceipt({ job: job(stagesWith([f])), workerDigests: digests, pageDigests: { ...digests }, workers });
  assert.equal(doc.device.evidence, "accelerated");
  assert.equal(doc.device.assigned, "cpu");
  assert.deepEqual(doc.device.stages, { domain: "webgpu", analysis: "cpu", verify: "cpu" });
  assert.match(doc.device.note, /one thread each; domain computed on the WebGPU adapter nvidia ampere \(physical\), each result checked on the CPU$/);
  assert.equal(doc.stages.find((s) => s.name === "domain").device, "webgpu");
  const d = doc.compute.stages.domain;
  assert.deepEqual([d.calls, d.backend, d.accelerated, d.evidence], [1, "webgpu", true, "accelerated"]);
  assert.deepEqual(d.calibration_ids, ["c".repeat(64)]);
  assert.deepEqual([d.gpu.tried, d.gpu.used, d.gpu.dispatched, d.gpu.validated, d.gpu.verified], [1, 1, 1, 1, true]);
  assert.equal(d.gpu.adapter.kind, "physical");
  assert.match(d.basis, /physical adapter that passed its known-answer test/);
  assert.equal(JSON.parse(JSON.stringify(doc)).compute.stages.domain.accelerated, true, "plain JSON");
  // without a verify stage the job is validated, whatever its stages ran on
  assert.equal(jobEvidence({ ...base, stages: stagesWith([f]).filter((s) => s.id !== "verify") }).evidence, "validated");
});

test("facts that claim more than they show are decided again: a software, unidentified, unchecked or uncalibrated GPU is not accelerated", async () => {
  const f = await facts();
  const forged = [
    { ...f, gpu: { ...f.gpu, adapter: { ...f.gpu.adapter, kind: "software" } } },
    { ...f, gpu: { ...f.gpu, adapter: { ...f.gpu.adapter, kind: "unidentified" } } },
    { ...f, gpu: { ...f.gpu, validated: false } },
    { ...f, gpu: { ...f.gpu, verified: false } },
    { ...f, gpu: { ...f.gpu, dispatched: false } },
    { ...f, calibration_id: null },
    { ...f, accelerated: false },
  ];
  for (const x of forged) {
    assert.notEqual(callGpuRung(x), "accelerated", JSON.stringify(x.gpu.adapter) + x.calibration_id);
    assert.equal(jobEvidence({ ...base, stages: stagesWith([x]) }).evidence, "verified");
  }
  // one GPU call short of accelerated holds the stage back, even beside one that is
  assert.equal(jobEvidence({ ...base, stages: stagesWith([f, forged[2]]) }).compute.stages.domain.accelerated, false);
  assert.equal(jobEvidence({ ...base, stages: stagesWith([f, forged[2]]) }).evidence, "verified");
  // a software adapter's real facts (the device check's software mode computes on it): exact, never accelerated
  const sw = await facts({ info: { vendor: "google", architecture: "swiftshader" }, sizes: SIZES });
  assert.equal(sw.backend, "wasm-simd", "its calibration never selects it");
});

test("CPU-only stages: the job is verified, and nothing ran on a GPU", async () => {
  const cpu = await facts({ preference: "cpu" });
  assert.equal(cpu.backend, "wasm-simd");
  const doc = buildJobReceipt({ job: job(stagesWith([cpu, cpu])), workerDigests: digests, pageDigests: { ...digests }, workers });
  assert.equal(doc.device.evidence, "verified");
  assert.match(doc.device.note, /one thread each; nothing ran on a GPU$/);
  assert.deepEqual(doc.device.stages, { domain: "cpu", analysis: "cpu", verify: "cpu" }, "the page's kernel computed the domain stage");
  assert.deepEqual([doc.compute.stages.domain.backend, doc.compute.stages.domain.calls, doc.compute.stages.domain.accelerated, doc.compute.stages.domain.gpu], ["wasm-simd", 2, null, null]);
  assert.equal(doc.compute.accelerated, false);
  // Python's reference, and a reused stage, computed nothing in the browser this time
  const py = await facts({ preference: "reference" });
  assert.equal(buildJobReceipt({ job: job(stagesWith([py])), workerDigests: digests, pageDigests: digests, workers }).compute.stages.domain.backend, "python");
  const reused = stagesWith([await facts()]).map((s) => (s.id === "domain" ? { ...s, reused: true } : s));
  const r2 = buildJobReceipt({ job: job(reused), workerDigests: digests, pageDigests: digests, workers });
  assert.equal(r2.compute, null);
  assert.equal(r2.device.evidence, "verified");
  assert.equal(r2.stages.find((s) => s.name === "domain").device, null);
});

test("a GPU result that failed its check: verified at most, and the receipt says it was tried and not used", async () => {
  const rejected = await facts({ corrupt: { from: 4, kind: "arg" } });
  assert.equal(rejected.backend, "wasm-simd");
  const doc = buildJobReceipt({ job: job(stagesWith([rejected])), workerDigests: digests, pageDigests: { ...digests }, workers });
  assert.equal(doc.device.evidence, "verified");
  assert.match(doc.device.note, /the GPU was tried for domain and its results not used$/);
  assert.equal(doc.device.stages.domain, "cpu");
  const d = doc.compute.stages.domain;
  assert.equal(d.accelerated, false);
  assert.deepEqual([d.gpu.tried, d.gpu.used], [1, 0]);
  assert.match(d.gpu.not_used[0], /its result failed the check on the CPU/);
  assert.equal(d.evidence, "dispatched");
  assert.equal(doc.compute.accelerated, false);
});
