// The browser's job service as a runtime (js/scientific/runtime/jobs-runtime.js): which calls are jobs it runs, which
// job_status calls it answers, and the envelopes it gives back — a finished job's limitations included.
import "../fixtures/setup.mjs";
import assert from "node:assert/strict";
import { test } from "node:test";
import { limitationLines } from "../../js/scientific/pipelines/network-pharmacology.js";
import { BrowserJobsRuntime, browserJobStatus, jobCall } from "../../js/scientific/runtime/jobs-runtime.js";

const ID = "bj-0123456789abcdef";

function fakeService(jobs = {}) {
  return {
    status: "ready",
    reason: null,
    kinds: () => [{ kind: "research.snapshot" }, { kind: "research.run" }, { kind: "browser.selftest" }],
    get: async (id) => jobs[id] ?? null,
    events: () => () => {},
    submit: async () => { throw Object.assign(new Error("No research snapshots in this browser yet"), { type: "unavailable", need: "dataset", hint: "Build them first" }); },
  };
}

test("calls: network_pharmacology_run and job.* are jobs; job_status names a browser job only by its bj- id", () => {
  assert.deepEqual(jobCall("network_pharmacology_run", { formula: "葛根芩连汤", parameters: { permutations: 200 } }), {
    kind: "research.run", params: { question: "葛根芩连汤的实测靶点集中在哪些通路？", formula: "葛根芩连汤", permutations: 200 },
  });
  assert.deepEqual(jobCall("call_tool", { tool: "job.research.snapshot", arguments: { network: true } }), { kind: "research.snapshot", params: { network: true } });
  assert.equal(jobCall("tcm_formula", {}), null);
  assert.deepEqual(browserJobStatus("job_status", { job_id: ID, wait_s: 600 }), { id: ID, wait_s: 60 });
  assert.equal(browserJobStatus("job_status", { job_id: "j-1234" }), null, "a runner job's id is the runner's");
  const rt = new BrowserJobsRuntime({ service: fakeService() });
  assert.deepEqual(rt.kinds(), ["research.snapshot", "research.run"], "the self-test is the page's own, not the model's");
  assert.equal(rt.snapshots, null, "unknown until the page has looked");
});

test("job_status: a succeeded job carries its own limitations (in-process isolation); one still running says it has no result", async () => {
  const limitation = "The governed analysis ran in this browser's job worker process (isolation: in_process).";
  const rt = new BrowserJobsRuntime({ service: fakeService({
    [ID]: { id: ID, kind: "research.run", state: "succeeded", artefacts: [{ name: "enrichment.tsv" }], limitations: [limitation] },
    "bj-00000000aaaaaaaa": { id: "bj-00000000aaaaaaaa", kind: "research.run", state: "running", progress: { fraction: 0.5 } },
  }) });
  const done = await rt.call("job_status", { job_id: ID });
  assert.equal(done.ok, true);
  assert.deepEqual(done.governance.limitations, [limitation]);
  assert.match(done.text, /succeeded, 1 output file/);
  // the model reads the text: the limits are in it, worded as the runner's envelope words them
  assert.ok(done.text.endsWith(`\nLimits (state these when you use this result):\n- ${limitation}`), done.text);
  assert.equal(done.job.where, "browser");
  const running = await rt.call("job_status", { job_id: "bj-00000000aaaaaaaa" });
  assert.deepEqual(running.governance.limitations, ["Pending: until the job has succeeded it has no result."]);
  assert.match(running.text, /running, 50% done \(no result yet\)$/);
  const missing = await rt.call("job_status", { job_id: "bj-ffffffffffffffff" });
  assert.equal(missing.ok, false);
  assert.equal(missing.error.type, "not_found");
});

test("a refusal before anything is queued keeps its need: no snapshots here is needs_dataset, not a missing service", async () => {
  const rt = new BrowserJobsRuntime({ service: fakeService() });
  const env = await rt.call("network_pharmacology_run", { formula: "葛根芩连汤" });
  assert.equal(env.ok, false);
  assert.equal(env.error.reason, "needs_dataset");
  assert.deepEqual(env.error.requires.map((r) => r.need), ["dataset"]);
  const other = await rt.call("call_tool", { tool: "job.pipeline.rnaseq", arguments: {} });
  assert.equal(other.error.type, "unavailable");
  assert.match(other.error.message, /not a job this browser runs/);
});

test("a run's limitations.md becomes the job's limitations as the runner's job service bounds them", () => {
  assert.deepEqual(limitationLines("one\n\n  two  \n"), ["one", "two"]);
  const long = limitationLines(`${"x".repeat(1200)}\n${Array.from({ length: 120 }, (_, i) => `line ${i}`).join("\n")}`);
  assert.equal(long.length, 101);
  assert.equal(long[0].length, 1000);
  assert.ok(long[0].endsWith("…"));
  assert.equal(long.at(-1), "… and 21 more in limitations.md");
});

test("createJobsRuntime: the ADMET kinds, with the RDKit.js build boot.json pins, the admet dataset group and the person's acceleration setting", async () => {
  const { IDBFactory } = await import("fake-indexeddb");
  const { fakeOpfs } = await import("../fixtures/fake-opfs.mjs");
  const { createJobsRuntime } = await import("../../js/scientific/runtime/jobs-runtime.js");
  const { RDKIT } = await import("../../js/scientific/chem/rdkit.js");
  const boot = (rdkit) => ({
    schema: "tcmstudio.boot/1", pyodide: { version: "314.0.7", index_url: "https://cdn.example/pyodide/" }, bundle: { path: "b.tar.gz", sha256: "a".repeat(64) },
    datasets: { path: "runtime/datasets.json" }, ...(rdkit ? { rdkit } : {}),
  });
  const make = async (doc) => createJobsRuntime({
    siteUrl: "https://science.example/", Worker: class { postMessage() {} terminate() {} }, indexedDB: new IDBFactory(), storage: fakeOpfs().storage,
    fetch: async () => ({ ok: true, json: async () => doc }), settings: () => ({ browserAcceleration: "cpu" }),
  });
  const rt = await make(boot({ version: RDKIT.version, release: RDKIT.release, url: "https://mirror.example/RDKit_minimal.wasm", sha256: RDKIT.wasm.sha256, bytes: RDKIT.wasm.bytes }));
  assert.equal(rt.status, "ready");
  assert.ok(rt.kinds().includes("admet.models") && rt.kinds().includes("pipeline.admet"));
  assert.equal(rt.admetModels, null, "unknown until the page has looked");
  assert.equal(rt.service.datasetsFor("admet").group, "admet");
  assert.equal(rt.service.datasetsFor("research").group, "research");
  // the kinds' refusals say what they lack here: no models built in this browser
  const env = await rt.call("run_pipeline", { pipeline: "admet", arguments: { molecules: "CCO" } });
  assert.equal(env.error.reason, "needs_dataset");
  assert.match(env.error.hint, /job\.admet\.models/);
  const sdf = await rt.call("run_pipeline", { pipeline: "admet", arguments: { molecules: "x.sdf" } });
  assert.equal(sdf.error.reason, "needs_runner");
  // a site whose boot.json pins another RDKit.js build than this page's code: refused, with the remedy
  const skew = await make(boot({ version: "2027.3.1", url: "https://x/RDKit_minimal.wasm", sha256: "b".repeat(64), bytes: 1 }));
  const refused = await skew.call("call_tool", { tool: "job.admet.models", arguments: { archive: "x.zip" } });
  assert.match(refused.error.message, /boot\.json pins RDKit\.js 2027\.3\.1/);
  assert.match(refused.error.hint, /Reload the page/);
});
