import "./fixtures/setup.mjs";
import assert from "node:assert/strict";
import { test } from "node:test";
import { indexedDB } from "fake-indexeddb";
import { Catalog, requirement } from "../js/core/catalog.js";
import { HUMAN_ONLY, PLACEMENT_REASONS, ToolRouter, hostsDigest, hostsOf, normalizeEnvelope, storeApprovals, synthEnvelope } from "../js/core/router.js";
import { RECEIPT_SCHEMA } from "../js/core/receipt.js";
import { t } from "../js/core/i18n.js";
import { openStore } from "../js/core/store.js";
import { refusalsOf } from "../js/core/governance.js";
import { envelopeFor, fakeRuntime, memoryApprovals, pipelineCatalogDoc, testCatalogDoc } from "./fixtures/fakes.mjs";
import { BrowserJobsRuntime } from "../js/scientific/runtime/jobs-runtime.js";

const catalog = () => new Catalog(testCatalogDoc());

function router({ browser = "idle", runner = "ready", compute = "auto", web = false, approvals, answer } = {}) {
  const runtimes = {
    browser: browser === null ? undefined : fakeRuntime("browser", { status: browser, answer }),
    runner: runner === null ? undefined : fakeRuntime("runner", { status: runner, answer }),
  };
  return { r: new ToolRouter({ catalog: catalog(), runtimes, settings: { compute, web }, approvals: approvals || memoryApprovals() }), runtimes };
}

test("placement: auto uses a connected runner for everything it has, the browser otherwise", () => {
  const { r } = router({ runner: "ready" });
  assert.equal(r.where("tcm_herb"), "runner");
  assert.equal(r.where("native.reverse_complement"), "runner");
  assert.equal(router({ runner: "offline" }).r.where("native.reverse_complement"), "browser");
  assert.equal(r.where("literature_search"), "runner");
  assert.equal(r.where("native.scrna_cluster"), "runner", "heavy deps go to the runner when it is connected");
  assert.equal(r.where("job.pipeline"), "runner");
  assert.equal(r.whereCall("call_tool", { tool: "native.scrna_cluster" }), "runner");
  assert.equal(r.whereCall("call_tool", { tool: "nope" }), null);
});

test("placement: without a runner, runner-only tools are unavailable with the reason; heavy browser-capable ones stay in the browser", () => {
  const { r } = router({ runner: "offline" });
  assert.equal(r.where("tcm_herb"), "browser");
  assert.equal(r.where("native.scrna_cluster"), "browser");
  const p = r.placement("literature_search");
  assert.equal(p.where, null);
  assert.equal(p.reason, "needs_runner");
  assert.match(p.message, /Runner/);
});

test("placement: compute=browser and compute=runner are honoured strictly", () => {
  const b = router({ compute: "browser", runner: "ready" }).r;
  assert.equal(b.where("tcm_herb"), "browser");
  assert.equal(b.placement("literature_search").reason, "compute_browser");
  const rr = router({ compute: "runner", runner: "ready" }).r;
  assert.equal(rr.where("tcm_herb"), "runner");
  const off = router({ compute: "runner", runner: "offline" }).r;
  assert.equal(off.placement("tcm_herb").reason, "runner_offline");
});

test("placement: an entry the runner cannot run (missing deps) says what is missing", () => {
  const { r } = router({ runner: "ready" });
  const p = r.placement("native.dock");
  assert.equal(p.where, null);
  assert.equal(p.reason, "missing_deps");
  assert.match(p.message, /vina, meeko/);
});

test("placement: what the runner lacks is read as its catalog classifies it, never as a package to install", async () => {
  const doc = testCatalogDoc();
  const base = { kind: "job", category: "research", summary: "x", example: null, exec: ["runner"], network: false, confirm: true, job: true, gpu: false, heavy: [], tags: [], parameters: { type: "object", properties: {} } };
  const snapshots = "snapshots (/x/ledger.jsonl)";
  doc.entries.push(
    // the runner's own readiness, merged into the entry: a dataset to build
    { ...base, id: "job.research.run", title: { zh: "研究", en: "Research" }, available: false, missing: [snapshots],
      state: { implementation: "implemented", readiness: { runner: { state: "needs_dataset", requires: [{ need: "dataset", detail: `the research ${snapshots} built on this runner`, remedy: "Build them first: job.research.snapshot (one recorded run downloads, verifies and records every snapshot).", names: ["research snapshots"] }] } } } },
    // an older runner: available/missing only, a sentence among the names
    { ...base, id: "job.old", title: { zh: "旧", en: "Old" }, available: false, missing: ["the job layer cannot run on Windows without WSL"] },
    { ...base, id: "job.pkg", title: { zh: "包", en: "Pkg" }, available: false, missing: ["salmon", snapshots] },
  );
  const runtimes = { browser: fakeRuntime("browser", { status: "idle" }), runner: fakeRuntime("runner", { status: "ready" }) };
  const r = new ToolRouter({ catalog: new Catalog(doc), runtimes, settings: { compute: "auto" }, approvals: memoryApprovals() });
  const research = r.placement("job.research.run");
  assert.equal(research.reason, "needs_dataset");
  assert.deepEqual(research.requires.map((q) => q.need), ["dataset"]);
  assert.match(research.requires[0].remedy, /job\.research\.snapshot/);
  assert.doesNotMatch(research.message, /依赖|install/i);
  const env = await r.call("call_tool", { tool: "job.research.run" }, { projectId: "p" });
  assert.equal(env.error.reason, "needs_dataset");
  assert.equal(env.error.hint, research.requires[0].remedy);
  assert.doesNotMatch(env.text, /install: snapshots/);
  const old = r.placement("job.old");
  assert.equal(old.reason, "needs_job_service");
  assert.deepEqual(old.requires.map((q) => [q.need, q.detail]), [["job_service", "the job layer cannot run on Windows without WSL"]]);
  const pkg = r.placement("job.pkg");
  assert.equal(pkg.reason, "missing_deps");
  assert.deepEqual(pkg.requires.map((q) => [q.need, q.names]), [["dependency", ["salmon"]], ["dataset", ["research snapshots"]]]);
  assert.match(pkg.message, /salmon/);
  assert.doesNotMatch(pkg.message, /snapshots/);
});

test("placement: a job kind the browser's job service runs goes to it, never to the Pyodide tool worker", async () => {
  const doc = testCatalogDoc();
  const obj = { type: "object", properties: {} };
  const job = { kind: "job", category: "research", summary: "x", example: null, network: false, confirm: true, job: true, gpu: false, heavy: [], tags: [], parameters: obj };
  doc.entries.push(
    { ...job, id: "job.research.run", title: { zh: "研究", en: "Research" }, exec: ["browser", "runner"], execution: { browser_job: { service: "studio-jobs/1" } } },
    { ...job, id: "job.research.other", title: { zh: "其他", en: "Other" }, exec: ["browser", "runner"] },
    { id: "system.job_status", kind: "system", title: { zh: "任务状态", en: "Job status" }, summary: "x", category: "system", parameters: obj, example: null, exec: ["browser", "runner"], network: false, confirm: false, job: false, gpu: false, heavy: [], tags: [] },
  );
  doc.core.push({ name: "job_status", title: { zh: "任务状态", en: "Job status" }, description: "Status of a job.", parameters: obj, category: "system", exec: ["browser", "runner"], network: false, confirm: false, job: false, maps_to: "system.job_status" });
  const submitted = (tool) => ({ ...envelopeFor(tool, "browser"), status: "job_submitted", job: { id: "bj-0123456789abcdef", kind: "research.run", state: "queued", where: "browser" } });
  const make = ({ jobs = "ready", runner = "offline", compute = "auto" } = {}) => {
    const runtimes = {
      browser: fakeRuntime("browser", { status: "idle" }),
      runner: fakeRuntime("runner", { status: runner }),
      jobs: jobs === null ? undefined : { ...fakeRuntime("jobs", { status: jobs, answer: submitted }), runs: (kind) => kind === "research.run" },
    };
    return { r: new ToolRouter({ catalog: new Catalog(doc), runtimes, settings: { compute }, approvals: memoryApprovals() }), runtimes };
  };
  const { r, runtimes } = make();
  assert.equal(r.where("job.research.run"), "browser");
  const env = await r.call("call_tool", { tool: "job.research.run", arguments: { question: "q" } }, { projectId: "p", onApproval: async () => "once" });
  assert.equal(env.status, "job_submitted");
  assert.equal(runtimes.jobs.calls.length, 1);
  assert.equal(runtimes.browser.calls.length, 0, "a job never goes to the tool worker");
  assert.equal(env.receipt.execution.placement.chosen, "browser");
  // a kind the browser's service does not implement has no browser place, whatever its exec says
  const other = make().r.placement("job.research.other");
  assert.equal(other.where, null);
  assert.equal(other.reason, "needs_job_service");
  // a connected runner takes jobs under auto, as it takes every call it can run
  assert.equal(make({ runner: "ready" }).r.where("job.research.run"), "runner");
  // no job service in this browser: said so, under browser and under auto without a runner
  for (const compute of ["browser", "auto"]) {
    const p = make({ jobs: "unsupported", compute }).r.placement("job.research.run");
    assert.equal(p.reason, "needs_job_service", compute);
    assert.deepEqual(p.requires.map((q) => q.need), ["job_service"]);
    assert.match(p.requires[0].detail, /this browser's job service/);
  }
  // a browser job's status is the page's to answer, even with a runner connected; a runner job's is the runner's
  const both = make({ runner: "ready" });
  const st = await both.r.call("job_status", { job_id: "bj-0123456789abcdef" }, { projectId: "p" });
  assert.equal(both.runtimes.jobs.calls.at(-1).tool, "job_status");
  assert.equal(st.receipt.execution.placement.chosen, "browser");
  await both.r.call("job_status", { job_id: "j-1234" }, { projectId: "p", onApproval: async () => "once" });
  assert.equal(both.runtimes.runner.calls.at(-1).tool, "job_status");
  const gone = await make({ jobs: null, runner: "ready" }).r.call("job_status", { job_id: "bj-0123456789abcdef" }, { projectId: "p" });
  assert.equal(gone.error.reason, "needs_job_service");
});

test("run_pipeline: placed by the kind its pipeline names; a call whose arguments need the runner says why, and a runner takes it", async () => {
  const doc = testCatalogDoc();
  const obj = (props = {}) => ({ type: "object", properties: props });
  const job = { kind: "job", category: "omics", summary: "x", example: null, network: false, confirm: true, job: true, gpu: false, heavy: [], tags: [] };
  const browserJob = { service: "studio-jobs/1", runtime: "pyodide", pyodide_packages: ["numpy", "scipy"] };
  // as tcmstudio.catalog / core_tools write them once the two kinds run in the browser
  const run = doc.core.find((c) => c.name === "run_pipeline");
  Object.assign(run, { exec: ["browser", "runner"], maps_to: "job.pipeline.*", parameters: obj({ pipeline: { type: "string", enum: ["rnaseq", "scrna", "fold"] }, arguments: { type: "object" } }) });
  doc.entries.push(
    { ...job, id: "job.pipeline.rnaseq", title: { zh: "RNA-seq", en: "RNA-seq" }, parameters: obj({ counts: { type: "string" }, samples: { type: "string" } }), exec: ["browser", "runner"], execution: { browser_job: browserJob } },
    { ...job, id: "job.pipeline.scrna", title: { zh: "单细胞", en: "Single-cell" }, parameters: obj({ source: { type: "string" } }), exec: ["browser", "runner"], execution: { browser_job: browserJob } },
    { ...job, id: "job.pipeline.fold", title: { zh: "结构", en: "Fold" }, parameters: obj({ method: { type: "string" } }), exec: ["runner"] },
  );
  const submitted = [];
  const service = (status = "ready") => ({
    status, reason: status === "ready" ? null : "no OPFS", events: () => () => {}, onStatus: () => () => {},
    kinds: () => (status === "ready" ? [{ kind: "pipeline.rnaseq" }, { kind: "pipeline.scrna" }] : []),
    submit: async (kind, params, opts) => {
      submitted.push({ kind, params, opts });
      return { id: "bj-0123456789abcdef", kind, state: "queued", title: { zh: "x", en: "x" }, stages: [] };
    },
  });
  const make = ({ runner = "offline", compute = "auto", jobs = "ready" } = {}) => {
    const runtimes = { browser: fakeRuntime("browser", { status: "idle" }), runner: fakeRuntime("runner", { status: runner }), jobs: new BrowserJobsRuntime({ service: service(jobs) }) };
    return { r: new ToolRouter({ catalog: new Catalog(doc), runtimes, settings: { compute }, approvals: memoryApprovals() }), runtimes };
  };
  const counts = { pipeline: "rnaseq", arguments: { counts: "counts.tsv", samples: "samples.tsv" } };
  const fastq = { pipeline: "rnaseq", arguments: { samples: "sheet.csv", transcripts: "tx.fa" } };

  // a count matrix: the browser's job service takes it, never the tool worker
  const { r, runtimes } = make();
  assert.equal(r.where("run_pipeline", counts), "browser");
  const env = await r.call("run_pipeline", counts, { projectId: "p1", onApproval: async () => "once" });
  assert.equal(env.status, "job_submitted");
  assert.deepEqual(submitted.at(-1), { kind: "pipeline.rnaseq", params: counts.arguments, opts: { project_id: "p1", submission_id: null } });
  assert.equal(runtimes.browser.calls.length, 0);
  assert.equal(env.receipt.execution.placement.chosen, "browser");
  assert.equal(r.whereCall("call_tool", { tool: "run_pipeline", arguments: counts }), "browser");
  // not named yet: a place in the browser while its job service runs a pipeline
  assert.equal(r.where("run_pipeline"), "browser");
  assert.equal(make({ jobs: "unsupported" }).r.where("run_pipeline"), null);

  // FASTQ, Scanpy, an .h5ad file: needs_runner with the job service's reason, under auto and under browser
  for (const [args, why] of [[fastq, /from FASTQ/], [{ pipeline: "scrna", arguments: { source: "x.tsv", analysis_backend: "scanpy" } }, /Scanpy/]]) {
    for (const compute of ["auto", "browser"]) {
      const p = make({ compute }).r.placement("run_pipeline", args);
      assert.equal(p.where, null, compute);
      assert.equal(p.reason, "needs_runner", compute);
      assert.equal(p.requires[0].need, "runner");
      assert.match(p.requires[0].detail, why);
      assert.match(p.requires[0].remedy, /Connect the local runner/);
      assert.deepEqual(p.requires.map((q) => q.need), compute === "browser" ? ["runner", "compute_setting"] : ["runner"]);
      assert.match(p.message, /只能在本机 Runner 上运行/);
    }
  }
  const viaEntry = make().r.placement(make().r.resolve("call_tool", { tool: "job.pipeline.scrna" }).target, { source: "pbmc.h5ad" });
  assert.equal(viaEntry.reason, "needs_runner");
  assert.match(viaEntry.requires[0].detail, /AnnData/);
  const refusedCall = await make().r.call("run_pipeline", fastq, { projectId: "p1", onApproval: async () => "once" });
  assert.equal(refusedCall.status, "failed");
  assert.equal(refusedCall.error.reason, "needs_runner");
  assert.match(refusedCall.text, /Needs: an RNA-seq run from FASTQ/);
  // said even where the job service cannot start: the reason is the call's, not the service's
  assert.equal(make({ jobs: "unsupported" }).r.placement("run_pipeline", fastq).reason, "needs_runner");
  assert.equal(make({ jobs: "unsupported" }).r.placement("run_pipeline", counts).reason, "needs_job_service");

  // a connected runner takes them all under auto, FASTQ included; a pipeline the browser does not run is the runner's
  const withRunner = make({ runner: "ready" });
  assert.equal(withRunner.r.where("run_pipeline", fastq), "runner");
  assert.equal(withRunner.r.where("run_pipeline", counts), "runner");
  const fold = make().r.placement("run_pipeline", { pipeline: "fold", arguments: {} });
  assert.deepEqual([fold.where, fold.reason], [null, "needs_runner"]);
  assert.equal(make({ compute: "browser" }).r.placement("run_pipeline", { pipeline: "fold", arguments: {} }).reason, "compute_browser");
  assert.equal(withRunner.r.where("run_pipeline", { pipeline: "fold", arguments: {} }), "runner");

  // a runner that is connected but lacks what the kind needs there says that, not "connect the runner"
  const scrnaEntry = doc.entries.find((e) => e.id === "job.pipeline.scrna");
  Object.assign(scrnaEntry, { available: false, missing: ["scanpy"] });
  const lacking = make({ runner: "ready" }).r.placement("job.pipeline.scrna", { source: "x.tsv", analysis_backend: "scanpy" });
  assert.deepEqual([lacking.where, lacking.reason], [null, "missing_deps"]);
  assert.match(lacking.message, /scanpy/);
  assert.equal(make({ runner: "offline" }).r.placement("job.pipeline.scrna", { source: "x.tsv", analysis_backend: "scanpy" }).reason, "needs_runner");
});

/** A router over pipelineCatalogDoc, with a job service that runs pipeline.rnaseq and pipeline.scrna (jobs null: none). */
function pipelineRouter({ runner = "offline", compute = "auto", jobs = "ready", browser = "idle" } = {}) {
  const submitted = [];
  const service = {
    status: jobs, reason: jobs === "ready" ? null : "no OPFS", events: () => () => {}, onStatus: () => () => {},
    kinds: () => (jobs === "ready" ? [{ kind: "pipeline.rnaseq" }, { kind: "pipeline.scrna" }] : []),
    submit: async (kind, params, opts) => {
      submitted.push({ kind, params, opts });
      return { id: "bj-0123456789abcdef", kind, state: "queued", title: { zh: "x", en: "x" }, stages: [] };
    },
  };
  const runtimes = { browser: fakeRuntime("browser", { status: browser }), runner: fakeRuntime("runner", { status: runner }) };
  if (jobs !== null) runtimes.jobs = new BrowserJobsRuntime({ service });
  const cat = new Catalog(pipelineCatalogDoc());
  return { r: new ToolRouter({ catalog: cat, runtimes, settings: { compute }, approvals: memoryApprovals() }), runtimes, submitted, catalog: cat };
}

test("without a call's arguments a browser job kind is ready where the job service runs it; the page's catalog answers say so too", async () => {
  const { r, catalog: cat } = pipelineRouter();
  // nothing is known that would need the runner (tcmstudio.capability.runner_only(entry, None) is empty)
  for (const x of ["job.pipeline.rnaseq", cat.byId["job.pipeline.rnaseq"], "job.pipeline.scrna", "run_pipeline"]) {
    const p = r.placement(x);
    assert.deepEqual([p.where, p.reason], ["browser", "ok"], x.id || x);
  }
  // a call's arguments are known, even empty ones: no counts is the FASTQ run
  assert.equal(r.placement("job.pipeline.rnaseq", {}).reason, "needs_runner");
  assert.equal(r.whereCall("call_tool", { tool: "job.pipeline.rnaseq" }), null);
  assert.equal(r.whereCall("call_tool", { tool: "job.pipeline.rnaseq", arguments: { counts: "c.tsv", samples: "s.tsv" } }), "browser");
  // catalog_search and capabilities_status, answered in the page while Pyodide is not ready, agree
  const search = await r.call("catalog_search", { query: "RNA-seq" }, { projectId: "p1" });
  assert.equal(search.receipt.execution.placement.chosen, "page");
  const hit = search.result.matched.find((m) => m.id === "job.pipeline.rnaseq");
  assert.deepEqual([hit.runnable_now, hit.where, hit.needs], [true, "browser", []]);
  assert.equal(JSON.parse(search.text).matched.find((m) => m.id === "job.pipeline.rnaseq").runnable_now, true);
  const caps = await r.call("capabilities_status", {}, { projectId: "p1" });
  const base = new ToolRouter({ catalog: new Catalog(testCatalogDoc()), runtimes: r.runtimes, settings: { compute: "auto" } });
  const baseCaps = await base.call("capabilities_status", {}, { projectId: "p1" });
  assert.equal(caps.result.by_place.browser, baseCaps.result.by_place.browser + 2, "both kinds, by the browser");
});

test("run_pipeline's arguments as the runner's schema reads them: JSON text is the object it spells; flat parameters, or arguments that are not an object, are bad arguments before any approval", async () => {
  const counts = { counts: "counts.tsv", samples: "samples.tsv" };
  const { r, submitted } = pipelineRouter();
  const text = { pipeline: "rnaseq", arguments: JSON.stringify(counts) };
  assert.equal(r.where("run_pipeline", text), "browser");
  const asked = [];
  const env = await r.call("run_pipeline", text, { projectId: "p1", onApproval: async (q) => { asked.push(q); return "once"; } });
  assert.equal(env.status, "job_submitted");
  assert.deepEqual(submitted.at(-1).params, counts);
  assert.deepEqual(asked[0].args, { pipeline: "rnaseq", arguments: counts }, "approved as the object it spells");
  const viaTool = await r.call("call_tool", { tool: "run_pipeline", arguments: { pipeline: "rnaseq", arguments: JSON.stringify(counts) } }, { projectId: "p1", onApproval: async () => "once" });
  assert.equal(viaTool.status, "job_submitted");
  assert.deepEqual(submitted.at(-1).params, counts);

  for (const [args, why] of [
    [{ pipeline: "rnaseq", ...counts }, /run_pipeline has no argument 'counts', 'samples'/],
    [{ pipeline: "rnaseq", arguments: "counts.tsv" }, /arguments must be an object \(the job kind's parameters\), not a string/],
    [{ pipeline: "rnaseq", arguments: [counts] }, /arguments must be an object \(the job kind's parameters\), not a list/],
    [{ pipeline: "rnaseq" }, /run_pipeline is missing the required argument 'arguments'/],
  ]) {
    const n = submitted.length;
    let approvals = 0;
    const bad = await r.call("run_pipeline", args, { projectId: "p1", onApproval: async () => { approvals += 1; return "once"; } });
    assert.equal(bad.status, "failed", JSON.stringify(args));
    assert.equal(bad.error.type, "bad_arguments");
    assert.match(bad.error.message, why);
    assert.doesNotMatch(bad.text, /FASTQ/, "not refused for a reason the call does not have");
    assert.equal(approvals, 0, "refused before any approval");
    assert.equal(submitted.length, n);
    assert.equal(bad.receipt.execution.placement.chosen, null);
    assert.equal(r.whereCall("run_pipeline", args), null);
  }
});

test("run_pipeline naming no pipeline of the catalog is bad arguments with the pipelines listed, before any placement or approval", async () => {
  const { r, submitted } = pipelineRouter();
  for (const [args, why, mean] of [
    [{ pipeline: "Rnaseq", arguments: { counts: "c.tsv", samples: "s.tsv" } }, /pipeline must be one of rnaseq, scrna, fold; got "Rnaseq"/, /Did you mean 'rnaseq' for pipeline\?/],
    [{ arguments: { counts: "c.tsv", samples: "s.tsv" } }, /run_pipeline is missing the required argument 'pipeline' \(one of rnaseq, scrna, fold\)/, null],
    [{ pipeline: "deseq2", arguments: {} }, /pipeline must be one of rnaseq, scrna, fold; got "deseq2"/, null],
  ]) {
    let approvals = 0;
    const env = await r.call("run_pipeline", args, { projectId: "p1", onApproval: async () => { approvals += 1; return "once"; } });
    assert.equal(env.status, "failed", JSON.stringify(args));
    assert.equal(env.error.type, "bad_arguments");
    assert.match(env.error.message, why);
    assert.match(env.error.hint, /pipeline \(rnaseq, scrna, fold\)/);
    if (mean) assert.match(env.error.hint, mean);
    assert.doesNotMatch(env.text, /runs every job kind|Connect the local runner/);
    assert.equal(approvals, 0);
    assert.equal(r.whereCall("run_pipeline", args), null);
  }
  assert.equal(submitted.length, 0, "no job was asked for");
  // not named yet (no call in view): still a place in the browser while its job service runs a pipeline
  assert.equal(r.where("run_pipeline"), "browser");
});

test("compute Browser with the runner connected: a call whose arguments need the runner is refused for the setting, with the arguments' reason", async () => {
  const fastq = { pipeline: "rnaseq", arguments: { samples: "sheet.csv" } };
  const { r } = pipelineRouter({ compute: "browser", runner: "ready" });
  const p = r.placement("run_pipeline", fastq);
  assert.deepEqual([p.where, p.reason], [null, "compute_browser"]);
  assert.deepEqual(p.requires.map((q) => q.need), ["compute_setting"]);
  assert.match(p.message, /from FASTQ .*计算目标已设为「浏览器」/);
  assert.doesNotMatch(p.message, /连接 Runner 后可运行/);
  const env = await r.call("run_pipeline", fastq, { projectId: "p1", onApproval: async () => "once" });
  assert.equal(env.error.reason, "compute_browser");
  assert.equal(env.error.hint, requirement("compute_setting").remedy);
  assert.match(env.text, /with these arguments runs only on the local runner: an RNA-seq run from FASTQ .*\. Compute is set to Browser/);
  assert.doesNotMatch(env.text, /Connect the runner/);
  assert.equal(r.runtimes.runner.calls.length, 0);
  // a connected runner that lacks the kind: the setting first, then what it lacks there
  const lacking = pipelineRouter({ compute: "browser", runner: "ready" });
  Object.assign(lacking.catalog.byId["job.pipeline.scrna"], { available: false, missing: ["scanpy"] });
  const q = lacking.r.placement("job.pipeline.scrna", { source: "x.tsv", analysis_backend: "scanpy" });
  assert.deepEqual([q.reason, q.requires.map((x) => x.need)], ["compute_browser", ["compute_setting", "dependency"]]);
  // the runner not connected: it needs the runner (and the setting), as before
  const off = pipelineRouter({ compute: "browser" }).r.placement("run_pipeline", fastq);
  assert.deepEqual([off.reason, off.requires.map((x) => x.need)], ["needs_runner", ["runner", "compute_setting"]]);
  assert.match(off.message, /连接 Runner 后可运行/);
});

test("without the job service's module the catalog's runner_only rules still say what needs the runner, as tcmstudio.capability.runner_only reads them", async () => {
  const { r } = pipelineRouter({ jobs: null });
  for (const [x, args, why] of [
    ["run_pipeline", { pipeline: "rnaseq", arguments: { samples: "sheet.csv", transcripts: "tx.fa" } }, /^without counts this is the FASTQ run/],
    ["job.pipeline.rnaseq", { counts: "c.tsv", samples: "s.tsv", de_backend: "pydeseq2" }, /^PyDESeq2 is not in this browser's Python/],
    ["job.pipeline.scrna", { source: "pbmc.H5AD" }, /^a 10x HDF5 or \.h5ad source/],
    ["call_tool", { tool: "job.pipeline.scrna", arguments: { source: "x.tsv", analysis_backend: "scanpy" } }, /^Scanpy, anndata/],
  ]) {
    const p = x === "call_tool" ? r.placement(r.resolve(x, args).target, args.arguments) : r.placement(x, args);
    assert.deepEqual([p.where, p.reason], [null, "needs_runner"], JSON.stringify(args));
    assert.match(p.requires[0].detail, why);
    assert.equal(p.requires[0].remedy, requirement("runner").remedy);
  }
  const env = await r.call("job.pipeline.scrna", { source: "pbmc.h5ad" }, { projectId: "p1" });
  assert.equal(env.error.reason, "needs_runner");
  // what the browser would run is still the job service's to run; and no arguments, nothing judged
  assert.equal(r.placement("run_pipeline", { pipeline: "rnaseq", arguments: { counts: "c.tsv", samples: "s.tsv" } }).reason, "needs_job_service");
  assert.equal(r.placement("job.pipeline.rnaseq").reason, "needs_job_service");
});

test("a browser runtime that failed to load counts as absent", () => {
  const { r } = router({ browser: "offline", runner: "offline" });
  assert.equal(r.where("tcm_herb"), null);
});

test("a call runs where placed, with the project context, and the envelope records where", async () => {
  const { r, runtimes } = router({ runner: "offline" });
  const env = await r.call("tcm_herb", { name: "甘草" }, { projectId: "p1", conversationId: "c1" });
  assert.equal(env.status, "succeeded");
  assert.equal(env.receipt.where, "browser");
  assert.deepEqual(runtimes.browser.calls[0].ctx.project_id, "p1");
  assert.deepEqual(runtimes.browser.calls[0].ctx.conversation_id, "c1");
});

test("unknown tools fail as not_found with suggestions; unavailable tools fail with the reason, nothing substituted", async () => {
  const { r } = router({ runner: "offline" });
  const env = await r.call("tcm_herbs", {});
  assert.equal(env.status, "failed");
  assert.equal(env.error.type, "not_found");
  assert.match(env.text, /tcm_herb/);
  const un = await r.call("literature_search", { query: "葛根" });
  assert.equal(un.status, "failed");
  assert.equal(un.error.type, "unavailable");
  assert.match(un.text, /local runner/);
  assert.ok(un.receipt.input_sha256 && /^[0-9a-f]{64}$/.test(un.receipt.input_sha256));
});

test("a name that differs from an id only in case and separators runs as that id, and the model is told its name", async () => {
  const { r, runtimes } = router({ runner: "offline" });
  // function-calling APIs allow no dots in a tool's name: models write native_reverse_complement
  const viaCallTool = await r.call("call_tool", { tool: "native_reverse_complement", arguments: { sequence: "ATGC" } });
  assert.equal(viaCallTool.status, "succeeded");
  assert.equal(runtimes.browser.calls.at(-1).args.tool, "native.reverse_complement", "the runtime gets the real id");
  assert.match(viaCallTool.text, /'native_reverse_complement' is written 'native\.reverse_complement'; call it so/);
  const direct = await r.call("Native.Reverse-Complement", { sequence: "ATGC" });
  assert.equal(direct.status, "succeeded");
  assert.equal(runtimes.browser.calls.at(-1).tool, "native.reverse_complement");
  const core = await r.call("tcm.herb", { name: "甘草" });
  assert.equal(core.status, "succeeded");
  assert.equal(runtimes.browser.calls.at(-1).tool, "tcm_herb");
  assert.equal(r.whereCall("call_tool", { tool: "native_reverse_complement" }), "browser");
  // a misspelling is not a separator: still unknown, with the did-you-mean hint
  assert.equal((await r.call("tcm_herbs", {})).error.type, "not_found");
});

test("the first runner call of a project needs approval; 'once' covers this page's life, 'project' is remembered", async () => {
  const approvals = memoryApprovals({ p1: { web: true, approvals: {} }, p2: { web: true, approvals: {} } });
  const { r } = router({ runner: "ready", web: true, approvals });
  const asked = [];
  const once = async (req) => { asked.push(req); return "once"; };
  const a = await r.call("call_tool", { tool: "native.scrna_cluster", arguments: {} }, { projectId: "p1", onApproval: once, callId: "x1" });
  assert.equal(a.status, "succeeded");
  assert.equal(asked.length, 1);
  assert.equal(asked[0].reason, "first_runner_call");
  assert.equal(asked[0].callId, "x1");
  await r.call("call_tool", { tool: "native.scrna_cluster", arguments: {} }, { projectId: "p1", onApproval: once });
  assert.equal(asked.length, 1, "not asked again in this page");
  assert.deepEqual(approvals.projects.p1.approvals, {}, "'once' is not stored");
  const proj = async () => "project";
  await r.call("call_tool", { tool: "native.scrna_cluster", arguments: {} }, { projectId: "p2", onApproval: proj });
  assert.equal(approvals.projects.p2.approvals.runner, "project");
  const fresh = new ToolRouter({ catalog: catalog(), runtimes: router({ runner: "ready" }).runtimes, settings: {}, approvals });
  let askedAgain = false;
  await fresh.call("call_tool", { tool: "native.scrna_cluster", arguments: {} }, { projectId: "p2", onApproval: async () => { askedAgain = true; return "deny"; } });
  assert.equal(askedAgain, false, "a project approval stands for a new page too");
});

test("deny: the call does not run and the model is told plainly", async () => {
  const { r, runtimes } = router({ runner: "ready" });
  const env = await r.call("run_pipeline", { pipeline: "fold", arguments: {} }, { projectId: "p1", onApproval: async () => "deny" });
  assert.equal(env.status, "refused");
  assert.equal(env.error.type, "declined");
  assert.match(env.text, /declined/);
  assert.equal(env.approval.decision, "deny");
  assert.equal(runtimes.runner.calls.length, 0);
});

test("jobs ask with reason 'job' (plus the first runner call); without a handler the call is needs_approval", async () => {
  const { r } = router({ runner: "ready" });
  let req;
  await r.call("run_pipeline", { pipeline: "fold" }, { projectId: "p1", onApproval: async (q) => { req = q; return "once"; } });
  assert.deepEqual(req.reasons, ["job", "first_runner_call"]);
  assert.equal(req.reason, "job");
  const { r: r2 } = router({ runner: "ready" });
  const env = await r2.call("run_pipeline", { pipeline: "fold" }, { projectId: "p1" });
  assert.equal(env.status, "needs_approval");
  assert.equal(env.approval.reason, "job");
  assert.match(env.approval.what, /job\.pipeline|流程/);
});

test("network tools need the project's web access, then an approval naming the hosts", async () => {
  const approvals = memoryApprovals({ off: { web: false, approvals: {} }, on: { web: true, approvals: { runner: "project" } } });
  const { r } = router({ runner: "ready", approvals });
  const off = await r.call("literature_search", { query: "葛根素" }, { projectId: "off", onApproval: async () => "once" });
  assert.equal(off.status, "failed");
  assert.equal(off.error.type, "network_off");
  // the same shape as the dispatcher's network_off: its reason, the hosts it would reach, the first remedy as hint
  assert.equal(off.error.reason, "network_off");
  assert.deepEqual(off.error.requires.map((q) => [q.need, q.names]), [["network", ["www.ebi.ac.uk"]]]);
  assert.equal(off.error.hint, off.error.requires[0].remedy);
  let req;
  const on = await r.call("literature_search", { query: "葛根素" }, { projectId: "on", onApproval: async (q) => { req = q; return "project"; } });
  assert.equal(on.status, "succeeded");
  assert.deepEqual(req.reasons, ["network"]);
  assert.deepEqual(req.hosts, ["www.ebi.ac.uk"]);
  // the grant is for the hosts the call reaches
  const key = `connector.europepmc.search:network@${await hostsDigest(["www.ebi.ac.uk"])}`;
  assert.match(key, /:network@[0-9a-f]{12}$/);
  assert.equal(approvals.projects.on.approvals[key], "project");
  assert.equal(approvals.projects.on.approvals["connector.europepmc.search:network"], undefined);
});

test("sending data to a third party (allow_remote) is its own approval", async () => {
  const approvals = memoryApprovals({ p: { web: true, approvals: { runner: "project" } } });
  const { r } = router({ runner: "ready", approvals });
  let req;
  await r.call("call_tool", { tool: "native.scrna_cluster", arguments: { allow_remote: true } }, { projectId: "p", onApproval: async (q) => { req = q; return "once"; } });
  assert.deepEqual(req.reasons, ["remote_upload"]);
});

test("allow_remote as the dispatcher reads it: in arguments given as JSON text, or as the text \"true\", it still asks", async () => {
  const approvals = memoryApprovals({ p: { web: true, approvals: { runner: "project" } } });
  const { r, runtimes } = router({ runner: "ready", approvals });
  const asked = [];
  const onApproval = async (q) => { asked.push(q.reasons); return "deny"; };
  const text = JSON.stringify({ allow_remote: true, data: "x" });
  const declined = await r.call("call_tool", { tool: "native.scrna_cluster", arguments: text }, { projectId: "p", onApproval });
  assert.equal(declined.status, "refused");
  await r.call("call_tool", { tool: "native.scrna_cluster", arguments: { allow_remote: "TRUE" } }, { projectId: "p", onApproval });
  await r.call("call_tool", { tool: "native.scrna_cluster", arguments: ' {"allow_remote": "true"}' }, { projectId: "p", onApproval });
  assert.deepEqual(asked, [["remote_upload"], ["remote_upload"], ["remote_upload"]]);
  assert.equal(runtimes.runner.calls.length, 0); // declined: nothing ran
  // approved, the runtime gets the object the text spells, as the dispatcher would have read it
  await r.call("call_tool", { tool: "native.scrna_cluster", arguments: text }, { projectId: "p", onApproval: async () => "once" });
  assert.deepEqual(runtimes.runner.calls[0].args, { tool: "native.scrna_cluster", arguments: { allow_remote: true, data: "x" } });
  // anything else stays as it was given (the dispatcher refuses what is not an object)
  await r.call("call_tool", { tool: "native.scrna_cluster", arguments: "[1, 2]" }, { projectId: "p", onApproval: async () => "once" });
  assert.equal(runtimes.runner.calls[1].args.arguments, "[1, 2]");
});

test("parallel calls that need the same approval ask once", async () => {
  const { r } = router({ runner: "ready" });
  let asked = 0;
  const onApproval = async () => { asked++; await new Promise((res) => setTimeout(res, 5)); return "once"; };
  const envs = await Promise.all([1, 2, 3].map(() => r.call("call_tool", { tool: "native.scrna_cluster", arguments: {} }, { projectId: "p", onApproval })));
  assert.deepEqual(envs.map((e) => e.status), ["succeeded", "succeeded", "succeeded"]);
  assert.equal(asked, 1);
});

test("approvals persist in the Store's project record", async () => {
  const store = await openStore({ indexedDB, name: `router-${Math.random()}` });
  const p = await store.projects.create({ name: "审批", web: true });
  const { runtimes } = router({ runner: "ready" });
  const r = new ToolRouter({ catalog: catalog(), runtimes, settings: {}, store });
  await r.call("literature_search", { query: "x" }, { projectId: p.id, onApproval: async () => "project" });
  const saved = await store.projects.get(p.id);
  assert.equal(saved.approvals.runner, "project");
  assert.equal(saved.approvals[`connector.europepmc.search:network@${await hostsDigest(["www.ebi.ac.uk"])}`], "project");
  const adapter = storeApprovals(store);
  assert.equal((await adapter.get(p.id)).web, true);
});

test("catalog_search and capabilities_status are answered in the page when no runtime is ready", async () => {
  const { r, runtimes } = router({ browser: "idle", runner: "offline" });
  const env = await r.call("catalog_search", { query: "配伍禁忌", limit: 5 });
  assert.equal(env.status, "succeeded");
  assert.equal(env.receipt.where, "browser");
  assert.equal(env.receipt.local, true);
  assert.equal(runtimes.browser.calls.length, 0);
  const parsed = JSON.parse(env.text);
  assert.equal(parsed.matched[0].id, "native.tcm_compatibility");
  const caps = await r.call("capabilities_status", {});
  assert.equal(caps.result.runner.connected, false);
  assert.equal(caps.result.web_access, false);
  assert.deepEqual(caps.result.remote_compute, { configured: false, reason: "no remote compute backend is registered; calls run in this browser or on the paired runner (runner.loopback says whether that runner is this computer)" });
  assert.equal(caps.receipt.schema, RECEIPT_SCHEMA);
  assert.deepEqual(caps.receipt.result, { kind: "system", outcome: "succeeded", assurance: "none" });
  assert.equal(caps.receipt.execution.placement.chosen, "page", "answered by the page itself");
  const ready = router({ browser: "ready", runner: "offline" });
  await ready.r.call("catalog_search", { query: "x" });
  assert.equal(ready.runtimes.browser.calls.length, 1, "a ready runtime answers itself");
});

test("a runtime that throws gives a runtime_error envelope; an abort gives cancelled", async () => {
  const { r } = router({ runner: "offline", answer: () => { throw new Error("worker crashed"); } });
  const env = await r.call("tcm_herb", { name: "甘草" });
  assert.equal(env.status, "failed");
  assert.equal(env.error.type, "runtime_error");
  assert.match(env.text, /worker crashed/);

  const slow = new ToolRouter({ catalog: catalog(), runtimes: { browser: fakeRuntime("browser", { delayMs: 200 }) }, settings: {} });
  const ctl = new AbortController();
  setTimeout(() => ctl.abort(), 10);
  const cancelled = await slow.call("tcm_herb", { name: "甘草" }, { signal: ctl.signal });
  assert.equal(cancelled.status, "cancelled");

  const asking = router({ runner: "ready" }).r;
  const ctl2 = new AbortController();
  setTimeout(() => ctl2.abort(), 10);
  const pending = await asking.call("run_pipeline", { pipeline: "fold" }, { signal: ctl2.signal, onApproval: () => new Promise(() => {}) });
  assert.equal(pending.status, "cancelled", "a stop while the approval card is open cancels the call");
  assert.equal(pending.receipt.result.outcome, "not_executed", "it never reached a runtime");
  assert.equal(cancelled.receipt.result.outcome, "cancelled", "the runtime had it");
});

test("normalizeEnvelope fills the shape and caps text", async () => {
  const env = await normalizeEnvelope({ ok: true, text: "x".repeat(20000) }, { tool: "t", where: "runner" });
  assert.equal(env.status, "succeeded");
  assert.equal(env.receipt.where, "runner");
  assert.ok(env.text.length < 16200);
  assert.match(env.text, /truncated/);
  const bad = await normalizeEnvelope(null, { tool: "t", where: "browser" });
  assert.equal(bad.status, "failed");
  const kept = await normalizeEnvelope(envelopeFor("tcm_herb", "runner", { extra_field: 1 }), { tool: "tcm_herb", where: "runner" });
  assert.equal(kept.extra_field, 1);
});

test("acts reserved for a person are refused with HUMAN_ONLY, by name or through call_tool — never 'unknown tool'", async () => {
  const { r, runtimes } = router({ runner: "ready", browser: "ready" });
  for (const [name, args] of [["clinic.sign", {}], ["call_tool", { tool: "clinic.sign", arguments: { draft_id: "d1" } }], ["call_tool", { tool: "clinic_sign" }], ["shell", { cmd: "ls" }], ["call_tool", { tool: "registry.release" }]]) {
    const env = await r.call(name, args, { projectId: "p" });
    assert.equal(env.status, "refused", name);
    assert.equal(env.ok, false);
    assert.deepEqual(env.governance.refusals.map((x) => x.code), ["HUMAN_ONLY"]);
    assert.ok(env.governance.refusals[0].message && env.governance.refusals[0].remedy);
    assert.match(env.text, /HUMAN_ONLY/);
    assert.doesNotMatch(env.text, /catalog_search|There is no tool/, "the model is not sent looking for a way to do it");
    assert.notEqual(env.error?.type, "not_found");
  }
  const sign = await r.call("call_tool", { tool: "clinic.sign" }, {});
  assert.equal(sign.governance.kind, "clinic");
  assert.equal(sign.via, "clinic.sign");
  assert.match(sign.summary, /只能由人完成/);
  assert.equal(refusalsOf(sign)[0].explanation, "只能由人完成的操作，不是工具调用");
  assert.equal(runtimes.runner.calls.length + runtimes.browser.calls.length, 0, "nothing runs, and no approval is asked");
  assert.equal(r.whereCall("call_tool", { tool: "clinic.sign" }), null);
  assert.ok(Object.keys(HUMAN_ONLY).includes("clinic.sign"));
  // a catalog that ships never_offered adds to the page's own list
  const withList = new ToolRouter({ catalog: new Catalog({ ...testCatalogDoc(), never_offered: { "lab.order": "Ordering a lab test is a clinician's act." } }), runtimes, settings: {}, approvals: memoryApprovals() });
  const order = await withList.call("call_tool", { tool: "lab.order" }, {});
  assert.equal(order.status, "refused");
  assert.match(order.governance.refusals[0].message, /clinician's act/);
});

// ------------------------------------------------------------------------------------------------- requirements and reasons

const TORCH = { need: "dependency", names: ["torch", "transformers"], detail: "install: torch, transformers", remedy: "Install it on the runner machine (pip install …, or the tool's own installer)." };

/** The test catalog with extra entries (a size limit, engines with runner readiness, hosts by argument). */
function richDoc() {
  const doc = testCatalogDoc();
  const obj = (props = {}) => ({ type: "object", properties: props });
  doc.core.push({ name: "run_fold", title: { zh: "流程", en: "Pipeline" }, description: "", parameters: obj(), category: "omics", exec: ["runner"], network: false, confirm: true, job: true, maps_to: "job.pipeline.*" });
  doc.core.find((c) => c.name === "run_pipeline").maps_to = "job.pipeline.*";
  doc.entries.push(
    { id: "native.big_align", kind: "native", title: { zh: "比对", en: "Align" }, summary: "Align.", category: "seq_genomics", parameters: obj({ sequence: { type: "string" } }), example: null, exec: ["browser", "runner"], network: false, confirm: false, job: false, gpu: false, heavy: [], tags: [], limits: { max_request_bytes: { runner: 8388608, browser: 200 } } },
    { id: "native.big_align_browser", kind: "native", title: { zh: "比对", en: "Align" }, summary: "Align.", category: "seq_genomics", parameters: obj({ sequence: { type: "string" } }), example: null, exec: ["browser"], network: false, confirm: false, job: false, gpu: false, heavy: [], tags: [], limits: { max_request_bytes: { browser: 200 } } },
    {
      id: "system.provider_call", kind: "system", title: { zh: "调用工具提供方", en: "Provider call" }, summary: "Call a provider tool.", category: "system",
      parameters: obj({ provider: { type: "string" }, tool: { type: "string" }, arguments: { type: "object" } }), example: null, exec: ["runner"], network: true, confirm: false, job: false, gpu: false, heavy: [], tags: [],
      hosts: ["static.example.org"],
      hosts_by: { tool: { ChEMBL_search_activities: ["www.ebi.ac.uk"], PubChem_get_CID_by_compound_name: ["pubchem.ncbi.nlm.nih.gov"] } },
    },
    {
      id: "job.pipeline.fold", kind: "job", title: { zh: "结构预测", en: "Fold" }, summary: "Fold.", category: "omics",
      parameters: obj({ method: { type: "string", enum: ["esmatlas", "esmfold", "colabfold"], default: "esmatlas" } }), example: null,
      exec: ["runner"], network: false, confirm: true, job: true, gpu: true, heavy: [], tags: [], hosts: ["api.esmatlas.com"],
      execution: { engines: [
        // as tcmstudio.catalog writes them: static requires, and on a probing runner readiness.runner per engine
        { id: "esmatlas", selected_by: { arg: "method", value: "esmatlas" }, requires: [], device: "remote", readiness: { runner: { state: "ready", requires: [] } } },
        { id: "esmfold", selected_by: { arg: "method", value: "esmfold" }, requires: [TORCH], device: "gpu", readiness: { runner: { state: "missing_dependency", requires: [TORCH] } } },
        // an engine that says only available/missing (another runner's way of saying it)
        { id: "colabfold", selected_by: { arg: "method", value: "colabfold" }, requires: [], device: "gpu", available: false, missing: ["colabfold_batch"] },
        // chosen by two arguments at once (selected_by.with)
        { id: "esmfold-relax", selected_by: { arg: "method", value: "esmfold", with: { relax: "amber" } }, requires: [], device: "gpu", readiness: { runner: { state: "missing_dependency", requires: [{ need: "dependency", names: ["openmm"], detail: "install: openmm", remedy: "Install it." }] } } },
      ] },
    },
    {
      id: "job.tcmdb.fetch", kind: "job", title: { zh: "下载数据集", en: "Fetch a dataset" }, summary: "Download.", category: "tcm_data",
      parameters: obj({ dataset: { type: "string", enum: ["herb2", "npass"] } }), example: null, exec: ["runner"], network: true, confirm: true, job: true, gpu: false, heavy: [], tags: [],
      hosts_by: { dataset: { herb2: ["herb.ac.cn"], npass: ["bidd.group", "BIDD.group"] } },
    },
  );
  return doc;
}

function richRouter({ browser = "idle", runner = "ready", compute = "auto", approvals, runnerUrl, answer } = {}) {
  const runtimes = {
    browser: browser === null ? undefined : fakeRuntime("browser", { status: browser, answer }),
    runner: runner === null ? undefined : fakeRuntime("runner", { status: runner, answer }),
  };
  if (runnerUrl && runtimes.runner) runtimes.runner.url = runnerUrl;
  return { r: new ToolRouter({ catalog: new Catalog(richDoc()), runtimes, settings: { compute, web: true }, approvals: approvals || memoryApprovals() }), runtimes };
}

test("every placement that finds no place says what it would need; the hint is the first remedy, error.reason the code", async () => {
  const cases = [
    [router({ runner: "offline" }).r, "literature_search", { query: "x" }, "needs_runner", "runner"],
    [router({ runner: "ready" }).r, "call_tool", { tool: "native.dock" }, "missing_deps", "dependency"],
    [router({ compute: "browser", runner: "ready" }).r, "literature_search", { query: "x" }, "compute_browser", "compute_setting"],
    [router({ compute: "runner", runner: "offline" }).r, "tcm_herb", { name: "甘草" }, "runner_offline", "runner"],
    [router({ compute: "browser", browser: "offline", runner: "ready" }).r, "tcm_herb", { name: "甘草" }, "no_runtime", "browser_runtime"],
  ];
  for (const [r, name, args, reason, need] of cases) {
    const env = await r.call(name, args, { projectId: "p" });
    assert.equal(env.status, "failed", reason);
    assert.equal(env.error.type, "unavailable");
    assert.equal(env.error.reason, reason);
    assert.equal(env.error.requires[0].need, need);
    for (const q of env.error.requires) assert.ok(q.detail && q.remedy, `${reason}: detail and remedy`);
    assert.equal(env.error.hint, env.error.requires[0].remedy, "the hint is a remedy, not a reason code");
    assert.ok(!PLACEMENT_REASONS.includes(env.error.hint));
    assert.match(env.text, new RegExp(`Hint: ${env.error.hint.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}`));
    assert.match(env.text, /Needs: /);
    assert.equal(env.receipt.execution.placement.reason, reason);
    assert.equal(env.receipt.execution.placement.chosen, null);
  }
  const dock = await router({ runner: "ready" }).r.call("call_tool", { tool: "native.dock" });
  assert.deepEqual(dock.error.requires[0].names, ["vina", "meeko"]);
  assert.equal(dock.error.requires[0].detail, "install: vina, meeko");
  const p = router({ runner: "offline" }).r.placement("literature_search");
  assert.deepEqual(Object.keys(p).sort(), ["message", "reason", "requires", "where"]);
  assert.equal(router({ runner: "ready" }).r.placement("tcm_herb").requires.length, 0);
});

test("an unknown compute preference is reported (unknown_compute), never run as auto; the page still answers its two tools", async () => {
  for (const compute of ["gpu", "local", "Auto"]) {
    const { r, runtimes } = router({ compute, runner: "ready", browser: "ready" });
    const p = r.placement("tcm_herb");
    assert.equal(p.where, null, compute);
    assert.equal(p.reason, "unknown_compute");
    assert.equal(p.requires[0].need, "compute_setting");
    assert.match(p.requires[0].detail, new RegExp(`"${compute}"`));
    const env = await r.call("tcm_herb", { name: "甘草" }, { projectId: "p", onApproval: async () => "once" });
    assert.equal(env.error.reason, "unknown_compute");
    assert.match(env.summary, new RegExp(compute));
    assert.equal(runtimes.runner.calls.length + runtimes.browser.calls.length, 0);
    assert.equal(env.receipt.execution.placement.requested, compute);
  }
  // a project default this page does not know is the same
  const { r } = router({ compute: "auto", runner: "ready" });
  r.setSettings({ compute: "cluster" });
  assert.equal(r.placement("native.reverse_complement").reason, "unknown_compute");
  const local = await r.call("catalog_search", { query: "配伍" });
  assert.equal(local.status, "succeeded");
  assert.equal(local.receipt.local, true);
  const needs = JSON.parse(local.text).matched.flatMap((m) => m.needs || []);
  assert.ok(needs.some((n) => /does not know/.test(n)), "catalog_search says why nothing runs");
});

test("compute 'remote': every call is unavailable (remote_not_configured) and nothing falls back to the runner or the browser", async () => {
  const { r, runtimes } = router({ compute: "remote", runner: "ready", browser: "ready" });
  // even with something that looks like a remote runtime: no remote backend is implemented
  runtimes.remote = fakeRuntime("remote", { status: "ready" });
  r.setRuntimes(runtimes);
  let asked = 0;
  for (const [name, args] of [["tcm_herb", { name: "甘草" }], ["literature_search", { query: "x" }], ["call_tool", { tool: "native.reverse_complement", arguments: { sequence: "ATGC" } }], ["run_pipeline", { pipeline: "fold", arguments: {} }]]) {
    const p = r.placement(r.resolve(name, args).target);
    assert.equal(p.where, null, name);
    assert.equal(p.reason, "remote_not_configured");
    assert.deepEqual(p.requires.map((q) => q.need), ["remote_backend"]);
    const env = await r.call(name, args, { projectId: "p", onApproval: async () => { asked++; return "once"; } });
    assert.equal(env.status, "failed");
    assert.equal(env.error.reason, "remote_not_configured");
    assert.equal(env.receipt.where, "page");
    assert.equal(env.receipt.result.outcome, "not_executed");
  }
  assert.equal(asked, 0, "no approval is asked for a call that cannot run");
  assert.equal(runtimes.runner.calls.length + runtimes.browser.calls.length + runtimes.remote.calls.length, 0);
  // the page's own tools: answered by the page itself, not the ready browser runtime (whose answer does not know the
  // preference and would list entries as runnable that the router refuses)
  assert.equal(r.where("catalog_search"), "page");
  const local = await r.call("catalog_search", { query: "配伍" });
  assert.equal(local.status, "succeeded");
  assert.equal(local.receipt.local, true);
  assert.equal(local.receipt.execution.placement.chosen, "page");
  assert.equal(runtimes.runner.calls.length + runtimes.browser.calls.length + runtimes.remote.calls.length, 0);
  const matched = JSON.parse(local.text).matched;
  assert.ok(matched.length && matched.every((m) => m.runnable_now === false), "nothing is listed as runnable");
  assert.ok(matched.every((m) => m.needs.includes("a remote compute backend (none is configured)")), "each says what it would need");
  const caps = await r.call("capabilities_status", {});
  assert.equal(caps.receipt.local, true);
  assert.equal(caps.result.counts.runnable_now, 0);
  assert.equal(runtimes.browser.calls.length, 0);
  // the same for a value this page does not know, with the browser runtime ready
  const unknown = router({ compute: "gpu", runner: "ready", browser: "ready" });
  const u = await unknown.r.call("catalog_search", { query: "配伍" });
  assert.equal(u.receipt.local, true);
  assert.equal(unknown.runtimes.browser.calls.length + unknown.runtimes.runner.calls.length, 0);
  assert.equal(t("core.router.remote_not_configured", { name: "x" }, "zh").includes("本机 Runner 上运行"), true, "it says it is not moved there");
});

test("bug fix: under compute 'browser' the page's own tools are never sent to a connected runner, and ask nothing", async () => {
  const { r, runtimes } = router({ compute: "browser", runner: "ready", browser: "idle" });
  let asked = 0;
  const onApproval = async () => { asked++; return "once"; };
  for (const name of ["catalog_search", "capabilities_status"]) {
    const env = await r.call(name, {}, { projectId: "p", onApproval });
    assert.equal(env.status, "succeeded");
    assert.equal(env.receipt.local, true);
    assert.equal(env.receipt.where, "browser");
  }
  assert.equal(runtimes.runner.calls.length, 0);
  assert.equal(asked, 0);
  assert.equal(r.where("catalog_search"), "browser");
  // compute "runner" or "auto" may still send them to the runner
  assert.equal(router({ compute: "runner", runner: "ready" }).r.where("catalog_search"), "runner");
  assert.equal(router({ compute: "auto", runner: "ready", browser: "idle" }).r.where("catalog_search"), "runner");
});

test("too_large_for_browser: a request above the entry's browser limit is not sent to the browser; the runner takes it under auto", async () => {
  const big = { sequence: "A".repeat(500) };
  const off = richRouter({ runner: "offline" });
  const p = off.r.placement("native.big_align", big);
  assert.equal(p.reason, "too_large_for_browser");
  assert.deepEqual(p.requires.map((q) => q.need), ["memory", "runner"]);
  assert.match(p.requires[0].detail, /at most 200 B in the browser runtime \(this one is 515 B/);
  const env = await off.r.call("call_tool", { tool: "native.big_align", arguments: big });
  assert.equal(env.error.reason, "too_large_for_browser");
  assert.equal(off.runtimes.browser.calls.length, 0);
  assert.equal(off.r.placement("native.big_align", { sequence: "ACGT" }).where, "browser", "a small request runs");
  assert.equal(richRouter({ runner: "ready" }).r.whereCall("call_tool", { tool: "native.big_align", arguments: big }), "runner");
  const strict = richRouter({ compute: "browser", runner: "ready" }).r.placement("native.big_align", big);
  assert.equal(strict.reason, "too_large_for_browser", "compute browser refuses it too, and says what would run it");
  assert.deepEqual(strict.requires.map((q) => q.need), ["memory", "compute_setting"]);
  // the catalog document's limit holds for an entry without its own; without either nothing is checked here
  const doc = { ...testCatalogDoc(), limits: { max_request_bytes: { runner: 8388608, browser: 64 } } };
  const docLimit = new ToolRouter({ catalog: new Catalog(doc), runtimes: router({ runner: "offline" }).runtimes, settings: {} });
  assert.equal(docLimit.placement("native.reverse_complement", { sequence: "A".repeat(100) }).reason, "too_large_for_browser");
  assert.equal(router({ runner: "offline" }).r.placement("native.reverse_complement", { sequence: "A".repeat(100000) }).where, "browser");
  // an entry only the browser runs: a smaller input is the one remedy, and the message does not send it to the runner
  const only = richRouter({ runner: "ready" }).r.placement("native.big_align_browser", big);
  assert.equal(only.reason, "too_large_for_browser");
  assert.deepEqual(only.requires.map((q) => q.need), ["memory"]);
  assert.match(only.requires[0].remedy, /smaller input/);
  assert.doesNotMatch(only.requires[0].remedy, /runner/);
  assert.doesNotMatch(t("core.router.too_large_for_browser_only", { name: "x", size: "1 MB", limit: "1 KB" }, "zh"), /Runner/);
  assert.doesNotMatch(only.message, /runner|Runner/);
});

test("missing_engine: the engine the arguments select lacks dependencies on the runner — refused before any approval", async () => {
  const { r, runtimes } = richRouter({ runner: "ready" });
  let asked = 0;
  const onApproval = async () => { asked++; return "once"; };
  const env = await r.call("run_pipeline", { pipeline: "fold", arguments: { method: "esmfold" } }, { projectId: "p", onApproval });
  assert.equal(env.status, "failed");
  assert.equal(env.error.reason, "missing_engine");
  assert.deepEqual(env.error.requires, [TORCH]);
  assert.match(env.summary, /esmfold/);
  const relax = r.placement(r.resolve("call_tool", { tool: "job.pipeline.fold" }).target, { method: "esmfold", relax: "amber" });
  assert.deepEqual(relax.requires.flatMap((q) => q.names), ["torch", "transformers", "openmm"], "every engine the arguments choose");
  assert.equal(asked, 0);
  assert.equal(runtimes.runner.calls.length, 0);
  const viaCallTool = await r.call("call_tool", { tool: "job.pipeline.fold", arguments: { method: "colabfold" } }, { projectId: "p", onApproval });
  assert.equal(viaCallTool.error.reason, "missing_engine", "an older catalog's available:false on the engine counts too");
  assert.deepEqual(viaCallTool.error.requires[0].names, ["colabfold_batch"]);
  // the default engine (method absent: its schema default) is ready: the job is asked about as usual
  const ok = await r.call("call_tool", { tool: "job.pipeline.fold", arguments: {} }, { projectId: "p", onApproval });
  assert.equal(ok.status, "succeeded");
  assert.equal(asked, 1);
  // a runner catalog without engine readiness: nothing is decided here (the runner refuses with the packages named)
  const doc = richDoc();
  for (const g of doc.entries.find((e) => e.id === "job.pipeline.fold").execution.engines) { delete g.readiness; delete g.available; }
  const plain = new ToolRouter({ catalog: new Catalog(doc), runtimes: richRouter().runtimes, settings: {} });
  assert.equal(plain.whereCall("call_tool", { tool: "job.pipeline.fold", arguments: { method: "esmfold" } }), "runner");
});

// ------------------------------------------------------------------------------------------------- hosts and approvals

test("network approvals are for the hosts a call reaches: hosts_by by argument, keyed by their digest", async () => {
  const approvals = memoryApprovals({ p: { web: true, approvals: { runner: "project" } } });
  const { r } = richRouter({ runner: "ready", approvals });
  const asked = [];
  const onApproval = async (q) => { asked.push(q); return "project"; };
  await r.call("call_tool", { tool: "job.tcmdb.fetch", arguments: { dataset: "herb2" } }, { projectId: "p", onApproval });
  assert.deepEqual(asked[0].reasons, ["job", "network"]);
  assert.deepEqual(asked[0].hosts, ["herb.ac.cn"], "the real hosts, not every dataset's");
  assert.deepEqual(asked[0].needs.find((n) => n.reason === "network").hosts, ["herb.ac.cn"]);
  assert.match(asked[0].text, /herb\.ac\.cn/);
  const herbKey = `job.tcmdb.fetch:network@${await hostsDigest(["herb.ac.cn"])}`;
  assert.equal(approvals.projects.p.approvals[herbKey], "project");
  // another dataset reaches other hosts: the grant does not carry over
  await r.call("call_tool", { tool: "job.tcmdb.fetch", arguments: { dataset: "npass" } }, { projectId: "p", onApproval });
  assert.deepEqual(asked[1].reasons, ["network"], "the job approval (keyed by the entry) stands; the network one is new");
  assert.deepEqual(asked[1].hosts, ["bidd.group"]);
  // the same dataset again: nothing asked
  await r.call("call_tool", { tool: "job.tcmdb.fetch", arguments: { dataset: "herb2" } }, { projectId: "p", onApproval });
  assert.equal(asked.length, 2);
  assert.deepEqual(hostsOf({ hosts: ["B.org", "a.org", "b.org"] }), ["a.org", "b.org"]);
  assert.equal(await hostsDigest(["b.org", "A.org"]), await hostsDigest(["a.org", "b.org"]), "order and case do not matter");
});

test("an approval stored before host-scoped keys (<entry>:network) stands only for the entry's own static hosts", async () => {
  const approvals = memoryApprovals({ p: { web: true, approvals: { runner: "project", "connector.europepmc.search:network": "project", "job.tcmdb.fetch:network": "project", "job.tcmdb.fetch": "project" } } });
  const { r } = richRouter({ runner: "ready", approvals });
  const asked = [];
  const onApproval = async (q) => { asked.push(q); return "once"; };
  const env = await r.call("call_tool", { tool: "connector.europepmc.search", arguments: { query: "x" } }, { projectId: "p", onApproval });
  assert.equal(asked.length, 0, "www.ebi.ac.uk is the entry's own host: the old grant stands");
  const net = env.receipt.execution.approvals.find((a) => a.reason === "network");
  assert.deepEqual(net, { reason: "network", scope: "standing", hosts: ["www.ebi.ac.uk"], legacy: true });
  // hosts the old grant never named (job.tcmdb.fetch had no static hosts): asked, with the hosts
  await r.call("call_tool", { tool: "job.tcmdb.fetch", arguments: { dataset: "herb2" } }, { projectId: "p", onApproval });
  assert.equal(asked.length, 1);
  assert.deepEqual(asked[0].reasons, ["network"]);
  assert.deepEqual(asked[0].hosts, ["herb.ac.cn"]);
});

test("a call whose hosts are not known in advance (hosts_by does not map its argument) is asked about every time; no grant stands for it", async () => {
  const approvals = memoryApprovals({ p: { web: true, approvals: { runner: "project", "system.provider_call:network": "project" } } });
  const { r, runtimes } = richRouter({ runner: "ready", approvals });
  const asked = [];
  const onApproval = async (q) => { asked.push(q); return "project"; };
  const unmapped = { tool: "call_tool", arguments: { tool: "system.provider_call", arguments: { provider: "tooluniverse", tool: "Some_unlisted_tool", arguments: {} } } };
  const env = await r.call(unmapped.tool, unmapped.arguments, { projectId: "p", onApproval });
  assert.equal(env.status, "succeeded");
  assert.equal(asked.length, 1, "the entry-wide grant from before does not stand for hosts nobody named");
  assert.deepEqual(asked[0].reasons, ["network"]);
  assert.deepEqual(asked[0].hosts, [], "the static hosts are not this call's");
  assert.deepEqual(asked[0].needs, [{ reason: "network", hosts: [], per_call: true }]);
  assert.match(asked[0].text, /无法事先确定/);
  assert.doesNotMatch(asked[0].text, /—/);
  assert.deepEqual(Object.keys(approvals.projects.p.approvals).sort(), ["runner", "system.provider_call:network"], "'project' stored nothing");
  assert.deepEqual(env.receipt.execution.approvals, [{ reason: "network", scope: "once", hosts: [] }, { reason: "first_runner_call", scope: "standing", hosts: [] }]);
  await r.call(unmapped.tool, unmapped.arguments, { projectId: "p", onApproval });
  assert.equal(asked.length, 2, "and again for the next call");
  // a tool hosts_by maps: scoped to its hosts as usual, and stored
  await r.call("call_tool", { tool: "system.provider_call", arguments: { provider: "tooluniverse", tool: "ChEMBL_search_activities", arguments: {} } }, { projectId: "p", onApproval });
  assert.deepEqual(asked[2].hosts, ["www.ebi.ac.uk"]);
  assert.equal(approvals.projects.p.approvals[`system.provider_call:network@${await hostsDigest(["www.ebi.ac.uk"])}`], "project");
  assert.equal(runtimes.runner.calls.length, 3);
  assert.equal(t("core.approval.network_unknown", { what: "x" }, "en").includes("not known in advance"), true);
});

test("without Web Crypto (an http page on another address) the router still never throws", async () => {
  const subtle = Object.getOwnPropertyDescriptor(globalThis.crypto, "subtle");
  Object.defineProperty(globalThis.crypto, "subtle", { value: undefined, configurable: true });
  try {
    const approvals = memoryApprovals({ p: { web: true, approvals: { runner: "project" } } });
    const { r } = router({ runner: "ready", approvals });
    const env = await r.call("call_tool", { tool: "connector.europepmc.search", arguments: { query: "x" } }, { projectId: "p", onApproval: async () => "project" });
    assert.equal(env.status, "succeeded");
    assert.equal(env.receipt.execution.approvals[0].reason, "network");
    // the grant is still for these hosts only: keyed by the hosts themselves
    assert.equal(approvals.projects.p.approvals["connector.europepmc.search:network@www.ebi.ac.uk"], "project");
    const un = await router({ runner: "offline" }).r.call("literature_search", { query: "x" });
    assert.equal(un.error.reason, "needs_runner");
    assert.equal(un.receipt.input_sha256, null);
    const local = await router({ runner: "offline" }).r.call("catalog_search", { query: "配伍" });
    assert.equal(local.status, "succeeded");
    assert.equal(local.receipt.input_sha256, null);
  } finally {
    if (subtle) Object.defineProperty(globalThis.crypto, "subtle", subtle);
    else delete globalThis.crypto.subtle;
  }
  assert.match(await hostsDigest(["a.org"]), /^[0-9a-f]{12}$/, "restored");
});

test("a fault in the page itself still gives an envelope, never a rejection", async () => {
  const { r } = router({ runner: "ready" });
  r.setCatalog({ coreTool() { throw new Error("broken catalog"); } });
  const env = await r.call("tcm_herb", { name: "甘草" });
  assert.equal(env.status, "failed");
  assert.equal(env.error.type, "runtime_error");
  assert.match(env.text, /broken catalog/);
  assert.equal(env.receipt.decided_by, "router");
  assert.equal(env.receipt.execution.placement.chosen, null);
});

test("a runner on another machine is a remote runner: its own approval (runner@host), never the local one's, never called local", async () => {
  const approvals = memoryApprovals({ p: { web: true, approvals: { runner: "project" } } });
  const { r } = router({ runner: "ready", approvals });
  r.runtimes.runner.url = "https://lab.example.org:8765";
  const asked = [];
  const env = await r.call("tcm_herb", { name: "甘草" }, { projectId: "p", onApproval: async (q) => { asked.push(q); return "project"; } });
  assert.equal(env.status, "succeeded");
  assert.equal(asked.length, 1, "the loopback runner's standing approval does not cover it");
  assert.deepEqual(asked[0].reasons, ["remote_runner"]);
  assert.deepEqual(asked[0].hosts, ["lab.example.org:8765"]);
  assert.match(asked[0].text, /远程 Runner/);
  assert.doesNotMatch(asked[0].text, /本机/);
  assert.equal(approvals.projects.p.approvals["runner@lab.example.org:8765"], "project");
  assert.deepEqual(env.receipt.execution.placement.runner, { host: "lab.example.org:8765", loopback: false });
  assert.match(t("core.approval.remote_runner", { hosts: "h" }, "en"), /leaves this computer/);
  // a job on it is started there: the prompt names that runner, in both languages, and never says local
  let job;
  await r.call("run_pipeline", { pipeline: "fold" }, { projectId: "p", onApproval: async (q) => { job = q; return "once"; } });
  assert.deepEqual(job.reasons, ["job"]);
  assert.match(job.text, /远程 Runner（lab\.example\.org:8765）上启动任务/);
  assert.doesNotMatch(job.text, /本机/);
  const en = t("core.approval.job_remote", { runner: "lab.example.org:8765", what: "x" }, "en");
  assert.match(en, /remote runner \(lab\.example\.org:8765\)/);
  assert.doesNotMatch(en, /local/);
  // a missing dependency there is the remote runner's
  const dock = r.placement("native.dock");
  assert.equal(dock.reason, "missing_deps");
  assert.doesNotMatch(dock.message, /本机|local/);
  assert.match(t("core.router.missing_deps_remote", { name: "x", missing: "y" }, "en"), /remote runner/);
  // the loopback runner: as before
  const local = router({ runner: "ready" });
  local.r.runtimes.runner.url = "http://127.0.0.1:8765";
  let req;
  const env2 = await local.r.call("tcm_herb", { name: "甘草" }, { projectId: "p", onApproval: async (q) => { req = q; return "once"; } });
  assert.deepEqual(req.reasons, ["first_runner_call"]);
  assert.deepEqual(env2.receipt.execution.placement.runner, { host: "127.0.0.1:8765", loopback: true });
});

// ------------------------------------------------------------------------------------------------- receipts

test("every envelope records its placement and the approvals it ran under, with their scope", async () => {
  const approvals = memoryApprovals({ p: { web: true, approvals: {} } });
  const { r } = router({ runner: "ready", approvals });
  const first = await r.call("tcm_herb", { name: "甘草" }, { projectId: "p", onApproval: async () => "once" });
  assert.deepEqual(first.receipt.execution.placement, { requested: "auto", chosen: "runner", reason: "ok", runner: null });
  assert.deepEqual(first.receipt.execution.approvals, [{ reason: "first_runner_call", scope: "once", hosts: [] }]);
  const second = await r.call("tcm_herb", { name: "甘草" }, { projectId: "p" });
  assert.deepEqual(second.receipt.execution.approvals, [{ reason: "first_runner_call", scope: "page", hosts: [] }]);
  await r.call("literature_search", { query: "x" }, { projectId: "p", onApproval: async () => "project" });
  // a new page: the network grant stands in the project record; the first runner call was approved only once
  const fresh = new ToolRouter({ catalog: catalog(), runtimes: r.runtimes, settings: {}, approvals });
  const third = await fresh.call("literature_search", { query: "y" }, { projectId: "p" });
  assert.equal(third.status, "needs_approval");
  assert.deepEqual(third.receipt.execution.approvals.map((a) => [a.reason, a.scope]), [["network", "standing"]], "only what stands is listed");
  approvals.projects.p.approvals.runner = "project";
  const fourth = await fresh.call("literature_search", { query: "y" }, { projectId: "p" });
  assert.equal(fourth.status, "succeeded");
  assert.deepEqual(fourth.receipt.execution.approvals.map((a) => [a.reason, a.scope]), [["network", "standing"], ["first_runner_call", "standing"]]);
  assert.deepEqual(r.runtimes.runner.calls.at(-1).ctx.approvals, ["network", "first_runner_call"], "the runtime gets the reasons, in order");
  const browser = await router({ runner: "offline" }).r.call("tcm_herb", { name: "甘草" });
  assert.equal(browser.receipt.execution.placement.chosen, "browser");
  assert.deepEqual(browser.receipt.execution.approvals, []);
  const none = await router({ runner: "offline" }).r.call("literature_search", { query: "x" });
  assert.deepEqual(none.receipt.execution.placement, { requested: "auto", chosen: null, reason: "needs_runner", runner: null });
  // "project" with no project to store it in: held for this page's life, and said so (never "project")
  const noProject = router({ runner: "ready" });
  const np = await noProject.r.call("tcm_herb", { name: "甘草" }, { onApproval: async () => "project" });
  assert.deepEqual(np.receipt.execution.approvals, [{ reason: "first_runner_call", scope: "page", hosts: [] }]);
  const noStore = new ToolRouter({ catalog: catalog(), runtimes: router({ runner: "ready" }).runtimes, settings: {} });
  const ns = await noStore.call("tcm_herb", { name: "甘草" }, { projectId: "p", onApproval: async () => "project" });
  assert.deepEqual(ns.receipt.execution.approvals.map((a) => a.scope), ["page"]);
});

test("a runtime's receipt is kept as it wrote it: no schema or result is made up, only the placement is added", async () => {
  const { r } = router({ runner: "offline" });
  const legacy = await r.call("tcm_herb", { name: "甘草" });
  assert.equal(legacy.receipt.runtime, "test");
  assert.equal(legacy.receipt.schema, undefined);
  assert.equal(legacy.receipt.result, undefined);
  assert.deepEqual(Object.keys(legacy.receipt.execution).sort(), ["approvals", "placement"]);
  const modern = router({
    runner: "offline",
    answer: (tool) => envelopeFor(tool, "browser", { receipt: { where: "browser", schema: RECEIPT_SCHEMA, execution: { runtime: "pyodide", kernel: null }, result: { kind: "record", outcome: "succeeded", assurance: "none" } } }),
  });
  const env = await modern.r.call("tcm_herb", { name: "甘草" });
  assert.equal(env.receipt.execution.runtime, "pyodide", "the runtime's execution fields stay");
  assert.equal(env.receipt.execution.placement.chosen, "browser");
  assert.deepEqual(env.receipt.result, { kind: "record", outcome: "succeeded", assurance: "none" });
  const bare = await normalizeEnvelope(envelopeFor("tcm_herb", "runner"), { tool: "tcm_herb", where: "runner" });
  assert.equal(bare.receipt.schema, undefined);
  assert.equal(bare.receipt.execution, undefined, "normalizeEnvelope itself adds nothing to the receipt but where");
});

test("envelopes decided in the page: where 'page', decided_by router, the receipt schema, not executed (failed when a runtime threw)", async () => {
  const outcomes = [];
  const check = (env, outcome) => {
    assert.equal(env.receipt.schema, RECEIPT_SCHEMA);
    assert.equal(env.receipt.where, "page");
    assert.equal(env.receipt.decided_by, "router");
    assert.equal(env.receipt.output_sha256, null);
    assert.match(env.receipt.input_sha256, /^[0-9a-f]{64}$/);
    assert.ok(env.receipt.finished_at >= env.receipt.started_at);
    assert.deepEqual(env.receipt.result, { kind: null, outcome, assurance: "none" });
    assert.equal(env.receipt.execution.runtime, "page");
    outcomes.push(outcome);
  };
  const { r } = router({ runner: "ready" });
  check(await r.call("tcm_herbs", {}), "not_executed");                                               // not_found
  check(await r.call("clinic.sign", {}), "not_executed");                                             // HUMAN_ONLY
  check(await router({ runner: "offline" }).r.call("literature_search", { query: "x" }), "not_executed"); // unavailable
  check(await r.call("literature_search", { query: "x" }, { projectId: "p" }), "not_executed");        // network_off (web off)
  check(await r.call("run_pipeline", { pipeline: "fold" }, { projectId: "p" }), "not_executed");        // needs_approval
  check(await r.call("run_pipeline", { pipeline: "fold" }, { projectId: "p", onApproval: async () => "deny" }), "not_executed"); // declined
  const threw = router({ runner: "offline", answer: () => { throw new Error("worker crashed"); } }).r;
  check(await threw.call("tcm_herb", { name: "甘草" }), "failed");
  const notFound = await r.call("tcm_herbs", {});
  assert.equal(notFound.error.reason, "not_found");
  const off = await r.call("literature_search", { query: "x" }, { projectId: "p" });
  assert.deepEqual(off.error.requires.map((q) => q.need), ["network"]);
  const stopped = await synthEnvelope({ tool: "x", status: "cancelled" });
  assert.equal(stopped.receipt.result.outcome, "cancelled");
  assert.equal(outcomes.length, 7);
});

test("patient data goes to a remote runner only with a yes to that very call; nothing stands for it", async () => {
  const doc = testCatalogDoc();
  doc.entries.push({ id: "clinic.assess", kind: "clinic", phi: true, title: { zh: "辨证", en: "Assess" }, summary: "Assess.", category: "tcm_knowledge", parameters: { type: "object", properties: {} }, example: null, exec: ["browser", "runner"], network: false, confirm: false, job: false, gpu: false, heavy: [], tags: [] });
  const approvals = memoryApprovals({ p: { web: true, approvals: { "runner@lab.example.org:8765": "project" } } });
  const runtimes = { browser: fakeRuntime("browser", { status: "idle" }), runner: fakeRuntime("runner", { status: "ready" }) };
  runtimes.runner.url = "https://lab.example.org:8765";
  const r = new ToolRouter({ catalog: new Catalog(doc), runtimes, settings: {}, approvals });
  // an ordinary call: the standing grant for that runner covers it
  let asked = [];
  await r.call("tcm_herb", { name: "甘草" }, { projectId: "p", onApproval: async (q) => { asked.push(q); return "project"; } });
  assert.equal(asked.length, 0);
  const ask = async (q) => { asked.push(q); return "project"; };
  const first = await r.call("call_tool", { tool: "clinic.assess", arguments: { intake: { age: 40 } } }, { projectId: "p", onApproval: ask });
  assert.equal(first.status, "succeeded");
  assert.equal(asked.length, 1, "asked although the runner stands approved");
  assert.equal(asked[0].needs[0].per_call, true);
  assert.match(asked[0].text, /患者资料/);
  assert.deepEqual(first.receipt.execution.approvals, [{ reason: "remote_runner", scope: "once", hosts: ["lab.example.org:8765"] }]);
  assert.doesNotMatch(JSON.stringify(first.receipt), /"age"/, "no argument value goes into the receipt");
  await r.call("call_tool", { tool: "clinic.assess", arguments: { intake: { age: 41 } } }, { projectId: "p", onApproval: ask });
  assert.equal(asked.length, 2, "and again for the next call, even after 'project'");
  const denied = await r.call("call_tool", { tool: "clinic.assess", arguments: {} }, { projectId: "p", onApproval: async () => "deny" });
  assert.equal(denied.status, "refused");
  assert.equal(runtimes.runner.calls.filter((c) => c.args?.tool === "clinic.assess").length, 2);
});

test("an entry that takes a clinic intake is patient data (as the dispatcher decides it), and so is job.skill.run of that skill", async () => {
  // as the built catalog has them: a skill (no phi flag) whose parameters take the intake, and the job that runs a skill
  const doc = testCatalogDoc();
  const base = { summary: "x", category: "tcm_knowledge", example: null, network: false, gpu: false, heavy: [], tags: [] };
  doc.entries.push(
    { ...base, id: "skill.draft-tcm-prescription", kind: "skill", title: { zh: "处方草案", en: "Draft" }, parameters: { type: "object", properties: { intake: { type: "object" }, days: { type: "integer" } } }, exec: ["browser", "runner"], confirm: true, job: false },
    { ...base, id: "job.skill.run", kind: "job", title: { zh: "技能任务", en: "Skill job" }, parameters: { type: "object", properties: { skill_id: { type: "string" }, arguments: { type: "object" } } }, exec: ["runner"], confirm: true, job: true },
  );
  const host = "lab.example.org:8765";
  for (const [tool, args, phi] of [
    ["skill.draft-tcm-prescription", { intake: { age: 40 } }, true],
    ["job.skill.run", { skill_id: "draft-tcm-prescription", arguments: { intake: { age: 40 } } }, true],
    ["job.skill.run", { skill_id: "normalize-tcm-entities", arguments: { names: ["甘草"] } }, false],
  ]) {
    // the remote runner and the entry's own confirmation both stand approved for the project
    const approvals = memoryApprovals({ p: { web: true, approvals: { [`runner@${host}`]: "project", [tool]: "project" } } });
    const runtimes = { browser: fakeRuntime("browser", { status: "idle" }), runner: fakeRuntime("runner", { status: "ready" }) };
    runtimes.runner.url = `https://${host}`;
    const r = new ToolRouter({ catalog: new Catalog(doc), runtimes, settings: { compute: "runner" }, approvals });
    const asked = [];
    const env = await r.call("call_tool", { tool, arguments: args }, { projectId: "p", onApproval: async (q) => { asked.push(q); return "deny"; } });
    if (phi) {
      assert.equal(env.status, "refused", `${tool}: asked, and the answer was no`);
      assert.deepEqual(asked.map((q) => q.needs.map((n) => [n.reason, n.per_call === true])), [[["remote_runner", true]]], tool);
      assert.match(asked[0].text, /患者资料/, `${tool}: the prompt says it is patient data`);
      assert.equal(runtimes.runner.calls.length, 0, `${tool}: nothing went to the remote runner`);
    } else {
      assert.equal(asked.length, 0, "a skill without an intake rides the standing grants");
      assert.equal(env.status, "succeeded");
    }
  }
});

test("the new router and approval messages exist in zh and en, with the same placeholders", () => {
  const keys = ["unknown_compute", "remote_not_configured", "too_large_for_browser", "too_large_for_browser_only", "missing_engine", "missing_deps_remote", "runner_lacks", "runner_lacks_remote", "browser_only_setting_args", "bad_arguments"].map((k) => `core.router.${k}`)
    .concat(["core.approval.remote_runner", "core.approval.remote_runner_phi", "core.approval.job_remote", "core.approval.network_unknown", "core.runner.label_remote"])
    .concat(["core.runner.not_running_remote", "core.runner.timeout_remote", "core.runner.http_remote", "core.local.capabilities_remote"]);
  const ph = (s) => [...String(s).matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort();
  for (const k of keys) {
    const zh = t(k, undefined, "zh");
    const en = t(k, undefined, "en");
    assert.notEqual(zh, k, `${k} exists`);
    assert.notEqual(zh, en, `${k} has its own zh and en (a missing one falls back to the other)`);
    assert.match(zh, /[一-鿿]/, `${k} zh is Chinese`);
    assert.deepEqual(ph(zh), ph(en), `${k} placeholders`);
    assert.doesNotMatch(`${zh} ${en}`, /[!！]/);
  }
  for (const k of ["core.approval.remote_runner", "core.approval.remote_runner_phi", "core.runner.label_remote", "core.router.remote_not_configured"]) {
    assert.doesNotMatch(t(k, { hosts: "h", name: "n" }, "zh").split("，")[0], /本机/, `${k}: a remote target is never 本机`);
  }
  // the strings that stand for a remote runner say so, whole, in both languages
  for (const k of ["core.approval.job_remote", "core.router.missing_deps_remote", "core.runner.not_running_remote", "core.runner.timeout_remote", "core.runner.http_remote", "core.local.capabilities_remote"]) {
    assert.doesNotMatch(t(k, {}, "zh"), /本机/, k);
    assert.doesNotMatch(t(k, {}, "en"), /\blocal\b/i, k);
    assert.match(`${t(k, {}, "zh")} ${t(k, {}, "en")}`, /远程 Runner.*remote runner/, k);
  }
});
