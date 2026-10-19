// The RNA-seq (count matrix) and single-cell browser jobs (js/scientific/pipelines/omics.js) through the job service,
// over fake-indexeddb, the in-memory OPFS of fixtures/fake-opfs.mjs, the page's project files as the jobs runtime reads
// them (projectFilesResolver), and scripted job workers whose Python answers what each test says: what is refused before
// anything is queued (and why), the inputs copied and held to the project's SHA-256, a sample sheet rewritten to the
// copies, the outputs named as the runner names them, the receipt's inputs, the verify outcome and the limitations, a
// resumed job reusing its analysis, and run_pipeline as a job call.
import "../fixtures/setup.mjs";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { test } from "node:test";
import { IDBFactory } from "fake-indexeddb";
import { fakeOpfs } from "../fixtures/fake-opfs.mjs";
import { OpfsStore } from "../../js/scientific/data/opfs-store.js";
import { OMICS, OMICS_HANDLERS, parseDelimited, rnaseqCounts, rnaseqNeedsRunner, scrna, scrnaNeedsRunner } from "../../js/scientific/pipelines/omics.js";
import { BrowserJobService, jobDir } from "../../js/scientific/runtime/job-manager.js";
import { BrowserJobsRuntime, jobCall, projectFilesResolver } from "../../js/scientific/runtime/jobs-runtime.js";
import { ScienceWorkerPool } from "../../js/scientific/runtime/worker-pool.js";

const enc = new TextEncoder();
const dec = new TextDecoder();
const hex = (bytes) => createHash("sha256").update(bytes).digest("hex");
const BOOT = { schema: "tcmstudio.boot/1", pyodide: { version: "314.0.7", index_url: "https://cdn.example/pyodide/" }, bundle: { path: "b.tar.gz", sha256: "a".repeat(64) } };
const VERSIONS = { numpy: "2.4.6", scipy: "1.18.0" };

// ------------------------------------------------------------------------------------------------------- fakes

/** The page's project files (core/store.js `files`): records with a Blob and the SHA-256 recorded when it was added. */
function projectStore(records = []) {
  let t = 1000;
  const recs = [];
  const add = (projectId, name, text, { sha256 = null, id = null } = {}) => {
    const bytes = typeof text === "string" ? enc.encode(text) : text;
    const rec = { id: id || `f-${recs.length + 1}`, projectId, name, type: "text/plain", bytes: bytes.length, sha256: sha256 || hex(bytes), blob: new Blob([bytes]), createdAt: (t += 10) };
    recs.push(rec);
    return rec;
  };
  for (const r of records) add(...r);
  return {
    recs,
    add,
    async list(projectId) { return recs.filter((r) => r.projectId === projectId).sort((a, b) => a.createdAt - b.createdAt); },
    async get(id) { return recs.find((r) => r.id === id); },
  };
}

/**
 * Scripted job workers with a memory file system: mount reads the files from the OPFS the job wrote, persist writes
 * the memory tree back (with each file's SHA-256), restore and free as science.worker.js does them; run answers what
 * python(fn, args, fs) returns ({ok, result} is added).
 */
function workerClass(store, python) {
  const instances = [];
  class FakeWorker {
    constructor() { this.terminated = false; this.fs = new Map(); this.calls = []; this.mounted = []; instances.push(this); }
    postMessage(data) {
      if (this.terminated || typeof data !== "string") return;
      const msg = JSON.parse(data);
      setTimeout(() => this.#answer(msg).catch((err) => this.send({ id: msg.id, ok: false, ms: 1, error: { type: "runtime_error", message: String(err?.message || err) } })), 0);
    }
    async #answer({ id, op, payload }) {
      const reply = (result) => this.send({ id, ok: true, ms: 1, result });
      this.calls.push({ op, payload });
      if (op === "init") return reply({ runtime: "pyodide-314.0.7 / CPython 3.14.2", platform: "emscripten", machine: "wasm32", packages: ["pyyaml"], pyodide: { version: "314.0.7" } });
      if (op === "packages") return reply({ packages: ["pyyaml", ...payload.names], versions: { ...VERSIONS, pyyaml: "6.0.3" } });
      if (op === "memory") return reply({ wasm_bytes: 200 << 20 });
      if (op === "mount") {
        const files = [];
        for (const name of payload.files) {
          const b = await store.readBytes([...payload.dir, name]);
          this.fs.set(`${payload.root}/${name}`, b);
          files.push({ name, bytes: b.length });
        }
        this.mounted.push({ root: payload.root, files: Object.fromEntries(files.map((f) => [f.name, dec.decode(this.fs.get(`${payload.root}/${f.name}`))])) });
        return reply({ root: payload.root, mode: "sync-access", files, bytes: files.reduce((a, f) => a + f.bytes, 0) });
      }
      if (op === "persist") {
        if (payload.clean) await store.remove(payload.dir, { recursive: true }).catch(() => false);
        const files = [];
        for (const [p, b] of [...this.fs].sort()) {
          if (!p.startsWith(`${payload.from}/`)) continue;
          const rel = p.slice(payload.from.length + 1).split("/");
          await store.writeBytes([...payload.dir, ...rel], b);
          files.push({ path: rel, bytes: b.length, sha256: hex(b) });
        }
        return reply({ dir: payload.dir, files });
      }
      if (op === "restore") {
        const walk = async (dir, rel) => {
          for (const e of await store.list(dir)) {
            if (e.kind === "directory") await walk([...dir, e.name], [...rel, e.name]);
            else this.fs.set([payload.to, ...rel, e.name].join("/"), await store.readBytes([...dir, e.name]));
          }
        };
        await walk(payload.dir, []);
        return reply({ to: payload.to, files: 0, bytes: 0 });
      }
      if (op === "free") {
        for (const p of payload.paths) for (const k of [...this.fs.keys()]) if (k === p || k.startsWith(`${p}/`)) this.fs.delete(k);
        return reply({ freed: payload.paths.length });
      }
      if (op === "run") {
        this.send({ type: "event", id, event: { kind: "log", level: "info", text: `python: ${payload.fn}` } });
        const out = await python(payload.fn, payload.args, this.fs, this);
        // {fatal: message}: the interpreter itself died (as a wasm allocation past the limit ends it)
        if (out && typeof out === "object" && typeof out.fatal === "string") return this.send({ id, ok: false, fatal: true, ms: 1, error: { type: "fatal", message: out.fatal } });
        return reply(out && typeof out === "object" && "ok" in out ? out : { ok: true, result: out });
      }
      return this.send({ id, ok: false, ms: 1, error: { type: "bad_request", message: op } });
    }
    send(obj) { setTimeout(() => { if (!this.terminated) this.onmessage?.({ data: JSON.stringify(obj) }); }, 0); }
    terminate() { this.terminated = true; }
  }
  FakeWorker.instances = instances;
  return FakeWorker;
}

const RNASEQ_OUT = {
  "run.json": JSON.stringify({ pipeline: "bioagent.omics.rnaseq_counts", input_kind: "counts", complete: true }),
  "stages.json": JSON.stringify({ schema: "bioagent.stages/1", complete: true }),
  "report.html": "<html>report</html>",
  "report.md": "# report\n",
  "limitations.md": "Counts were given, not quantified here.\nThe design has one factor.\n",
  "counts.tsv": "gene_id\tgene_name\tc1\tt1\nG1\tA\t10\t40\n",
  "deseq2_results.tsv": "gene_id\tgene_name\tbase_mean\tlog2_fold_change\tlfc_se\tstat\tp_value\tp_adjusted\nG1\tA\t25\t2\t0.3\t6.7\t1e-11\t1e-10\n",
  "de_diagnostics.tsv": "gene_id\tdispersion_gene_wise\tdispersion_trend\tdispersion\tcooks_outlier\tconverged\nG1\t0.05\t0.05\t0.05\tFalse\tTrue\n",
  "vst.tsv": "gene_id\tc1\tt1\nG1\t3.3\t5.3\n",
  "plots/pca.svg": "<svg/>",
};

const SCRNA_OUT = {
  "run.json": JSON.stringify({ pipeline: "bioagent.omics.scrna", complete: true }),
  "stages.json": "{}",
  "report.html": "<html>sc</html>",
  "limitations.md": "The input is the reference dataset.\n",
  "cells.tsv": "cell\tsample\tcluster\tumap_1\tumap_2\n",
  "clusters.tsv": "cluster\tcell_type\tcells\n",
  "markers.tsv": "cluster\tgene\n",
  "plots/umap_clusters.svg": "<svg/>",
};

const PASS = { status: "PASS", checks: [{ name: "verify_run", status: "PASS" }, { name: "required_artefacts", status: "PASS" }, { name: "validator:run", status: "PASS" }], problems: [], report: {} };

/** A Python that writes `outputs` into out_dir for the pipeline call and passes verify_pipeline; `calls` records both. */
function cannedPython(outputs, { exit = 0, verify = () => PASS, refused = null } = {}) {
  const calls = [];
  const fn = async (name, args, fs) => {
    calls.push({ name, args });
    if (name === "verify_pipeline") return verify(args);
    if (exit !== 2) for (const [p, text] of Object.entries(outputs)) fs.set(`${args.out_dir}/${p}`, enc.encode(text));
    return { exit, summary: exit === 2 ? null : { out_dir: args.out_dir, genes: 1 }, failed: exit === 1 ? ["differential_expression: the design is not full rank"] : [], refused };
  };
  fn.calls = calls;
  return fn;
}

const COUNTS = "gene_id\tgene_name\tc1\tt1\nG1\tA\t10\t40\n";
const SAMPLES = "sample\tcondition\nc1\tcontrol\nt1\ttreated\n";

async function setup({ python, files = projectStore(), quota } = {}) {
  const fake = fakeOpfs(quota ? { quota } : {});
  const store = new OpfsStore({ root: fake.root, storage: fake.storage });
  const Worker = workerClass(store, python || cannedPython(RNASEQ_OUT));
  const pool = new ScienceWorkerPool({ size: 1, Worker, workerUrl: "w.js", boot: BOOT, interruptGraceMs: 20 });
  const svc = new BrowserJobService({
    indexedDB: new IDBFactory(), locks: null, pool, store, projectFiles: projectFilesResolver(files), handlers: OMICS_HANDLERS,
    navigator: { userAgent: "test", hardwareConcurrency: 4, deviceMemory: 8 },
  });
  assert.equal(await svc.start(), "ready");
  return { svc, store, files, Worker, fake };
}

async function settle(svc, id, ms = 5000) {
  const t0 = Date.now();
  for (;;) {
    const job = await svc.get(id);
    if (["succeeded", "failed", "cancelled", "interrupted"].includes(job?.state)) return job;
    if (Date.now() - t0 > ms) throw new Error(`job ${id} did not end: ${job?.state}`);
    await new Promise((r) => setTimeout(r, 5));
  }
}

/** What preflight refuses → {type, need, message, hint}. */
async function refused(handler, params, svc, projectId = "p1") {
  try {
    await handler.preflight(params, svc, { project_id: projectId });
  } catch (err) {
    return { type: err.type, need: err.need, message: err.message, hint: err.hint };
  }
  return null;
}

async function preflightSvc({ files, quota } = {}) {
  const fake = fakeOpfs(quota ? { quota } : {});
  return { store: new OpfsStore({ root: fake.root, storage: fake.storage }), pool: {}, projectFiles: projectFilesResolver(files || projectStore()) };
}

// ------------------------------------------------------------------------------------------------------- refusals

test("rnaseq preflight: FASTQ and PyDESeq2 need the runner (with what it would do); a count-matrix run's own errors are bad arguments", async () => {
  const files = projectStore([["p1", "counts.tsv", COUNTS], ["p1", "samples.tsv", SAMPLES], ["p1", "fastq_sheet.csv", "sample,fastq_1,fastq_2,condition\nc1,a_1.fq.gz,a_2.fq.gz,control\n"]]);
  const svc = await preflightSvc({ files });
  const fastq = await refused(rnaseqCounts, { samples: "fastq_sheet.csv", transcripts: "tx.fa" }, svc);
  assert.deepEqual([fastq.type, fastq.need], ["unavailable", "runner"]);
  assert.match(fastq.message, /from FASTQ .* runs only on the runner/);
  assert.match(fastq.hint, /full pipeline from FASTQ/);
  const deseq = await refused(rnaseqCounts, { counts: "counts.tsv", samples: "samples.tsv", de_backend: "pydeseq2" }, svc);
  assert.deepEqual([deseq.type, deseq.need], ["unavailable", "runner"]);
  assert.match(deseq.message, /PyDESeq2 is not in this browser's Python; the built-in DESeq2 implementation is/);
  // the sample table given with counts is a FASTQ sheet: the runner runs that, without counts
  const sheet = await refused(rnaseqCounts, { counts: "counts.tsv", samples: "fastq_sheet.csv" }, svc);
  assert.deepEqual([sheet.type, sheet.need], ["unavailable", "runner"]);
  assert.match(sheet.message, /fastq_sheet\.csv is a FASTQ sample sheet/);

  const cases = [
    [{ counts: "counts.tsv", samples: "samples.tsv", transcripts: "tx.fa", engine: "salmon" }, /a count-matrix run takes no FASTQ reference or engine: transcripts, engine/],
    [{ counts: "counts.tsv" }, /samples: the sample table .* is required with counts/],
    [{ counts: "counts.tsv", samples: "samples.tsv", alpha: 0 }, /alpha: between 0\.0001 and 0\.5, not 0/],
    [{ counts: "counts.tsv", samples: "samples.tsv", alpha: 0.6 }, /alpha: between 0\.0001 and 0\.5/],
    [{ counts: "counts.tsv", samples: "samples.tsv", alpha: "0.05" }, /alpha: a number is needed/],
    // an empty text is not left out: the runner's schema refuses it for a number or a choice, so is it here
    [{ counts: "counts.tsv", samples: "samples.tsv", alpha: "" }, /alpha: a number is needed, not ""/],
    [{ counts: "counts.tsv", samples: "samples.tsv", de_backend: "" }, /de_backend: one of builtin, pydeseq2, not ""/],
    [{ counts: "counts.tsv", samples: "samples.tsv", contrast: "condition treated control" }, /contrast: .* is not in the expected form/],
    [{ counts: "counts.tsv", samples: "samples.tsv", design: "-x" }, /design: a value cannot start with '-'/],
    [{ counts: "counts.tsv", samples: "samples.tsv", de_backend: "edger" }, /de_backend: one of builtin, pydeseq2/],
    [{ counts: "counts.tsv", samples: "samples.tsv", threads: 4 }, /unknown parameter threads; pipeline\.rnaseq takes counts, samples/],
    [{ counts: "missing.tsv", samples: "samples.tsv" }, /counts: no file named "missing\.tsv" in this project/],
  ];
  for (const [params, why] of cases) {
    const r = await refused(rnaseqCounts, params, svc);
    assert.equal(r?.type, "bad_arguments", JSON.stringify(params));
    assert.match(r.message, why);
  }
  assert.equal(await refused(rnaseqCounts, { counts: "counts.tsv", samples: "samples.tsv", alpha: 0.0001, contrast: "condition,treated,control" }, svc), null);
  // null is left out (the runner's schema check drops it): the default
  assert.equal(await refused(rnaseqCounts, { counts: "counts.tsv", samples: "samples.tsv", alpha: null, de_backend: null, design: "" }, svc), null);
  // a job of no project names its files by id only
  const noProject = await refused(rnaseqCounts, { counts: "counts.tsv", samples: "samples.tsv" }, svc, null);
  assert.match(noProject.message, /no file named "counts\.tsv" in this project \(the job belongs to no project/);
  assert.equal(await refused(rnaseqCounts, { counts: files.recs[0].id, samples: files.recs[1].id }, svc, null), null);
});

test("scrna preflight: Scanpy, scVI, .h5 and .h5ad need the runner; the bounds are the runner's; a sheet's files must all be here", async () => {
  const files = projectStore([
    ["p1", "pbmc.tsv.gz", "gz"], ["p1", "pbmc.h5ad", "x"], ["p1", "pbmc.h5", "x"], ["p1", "matrix.mtx.gz", "x"],
    ["p1", "sheet.csv", "sample,path,batch\nctrl1,pbmc.tsv.gz,b1\n"], ["p1", "sheet.txt", "sample\tpath\nctrl1\tpbmc.tsv.gz\n"],
    ["p1", "sheet_h5.csv", "sample,path\nctrl1,pbmc.h5\n"], ["p1", "sheet_missing.csv", "sample,path\nctrl1,pbmc.tsv.gz\nctrl2,gone.tsv\n"],
    ["p1", "sheet_latin1.csv", Uint8Array.from([...enc.encode("sample,path,note\nctrl1,pbmc.tsv.gz,"), 0xe9, 0x0a])],
  ]);
  const svc = await preflightSvc({ files });
  for (const [params, why] of [
    [{ source: "pbmc.tsv.gz", analysis_backend: "scanpy" }, /Scanpy .* is not in this browser's Python/],
    [{ source: "pbmc.tsv.gz", integration: "scvi" }, /scVI .* is not in this browser's Python/],
    [{ source: "pbmc.h5ad" }, /AnnData \(\.h5ad\) needs anndata/],
    [{ source: "pbmc.h5" }, /10x HDF5 \(\.h5\) is not yet supported in this browser/],
    [{ source: files.recs[1].id }, /AnnData/], // by id: the format is the file's own name
    [{ source: "sheet_h5.csv" }, /line 2, path: 10x HDF5/],
  ]) {
    const r = await refused(scrna, params, svc);
    assert.deepEqual([r?.type, r?.need], ["unavailable", "runner"], JSON.stringify(params));
    assert.match(r.message, why);
    assert.match(r.hint, /Connect the local runner/);
  }
  for (const [params, why] of [
    [{ source: "pbmc.tsv.gz", resolution: 0.01 }, /resolution: between 0\.05 and 5/],
    [{ source: "pbmc.tsv.gz", resolution: 6 }, /resolution: between 0\.05 and 5/],
    [{ source: "pbmc.tsv.gz", seed: -1 }, /seed: between 0 and 2147483647/],
    [{ source: "pbmc.tsv.gz", seed: 2 ** 31 }, /seed: between 0 and 2147483647/],
    [{ source: "pbmc.tsv.gz", seed: 1.5 }, /seed: a whole number is needed/],
    [{ source: "pbmc.tsv.gz", n_top_genes: 49 }, /n_top_genes: between 50 and 10000/],
    [{ source: "pbmc.tsv.gz", n_top_genes: 10001 }, /n_top_genes: between 50 and 10000/],
    [{ source: "pbmc.tsv.gz", doublets: "drop" }, /doublets: one of remove, flag, off/],
    [{ source: "pbmc.tsv.gz", seed: "" }, /seed: a whole number is needed, not ""/],
    [{ source: "pbmc.tsv.gz", integration: "" }, /integration: one of none, harmony, scvi, not ""/],
    [{ source: "pbmc.tsv.gz", de_backend: "pydeseq2" }, /unknown parameter de_backend; pipeline\.scrna takes source/],
    [{}, /source: a count table .* is required/],
    [{ source: "matrix.mtx.gz" }, /matrix\.mtx\.gz is not a count table this job reads/],
    [{ source: "sheet.txt" }, /a sample sheet is read as \.csv or \.tsv only/],
    [{ source: "sheet_missing.csv" }, /source: line 3, path: no file named "gone\.tsv" in this project/],
    [{ source: "sheet_latin1.csv" }, /source: the sheet sheet_latin1\.csv could not be read as UTF-8 text/],
    [{ source: "pbmc.tsv.gz", markers: "markers.tsv" }, /markers: no file named "markers\.tsv"/],
  ]) {
    const r = await refused(scrna, params, svc);
    assert.equal(r?.type, "bad_arguments", JSON.stringify(params));
    assert.match(r.message, why);
  }
  assert.equal(await refused(scrna, { source: "sheet.csv", n_top_genes: 600, resolution: 0.05, seed: 2 ** 31 - 1, integration: "none" }, svc), null);
  assert.equal(await refused(scrna, { source: "pbmc.tsv.gz", n_top_genes: 50 }, svc), null);
});

test("preflight: storage for three times the inputs, a page with no project files, and the checks the router asks without files", async () => {
  const files = projectStore([["p1", "counts.tsv", "x".repeat(4000)], ["p1", "samples.tsv", SAMPLES]]);
  const full = await preflightSvc({ files, quota: 10_000 });
  const r = await refused(rnaseqCounts, { counts: "counts.tsv", samples: "samples.tsv" }, full);
  assert.deepEqual([r.type, r.need], ["unavailable", "storage"]);
  assert.match(r.message, /needs at least 0 MB of browser storage; 0 MB is free/);
  const none = await refused(rnaseqCounts, { counts: "counts.tsv", samples: "samples.tsv" }, { ...(await preflightSvc()), projectFiles: null });
  assert.deepEqual([none.type, none.need], ["unavailable", "storage"]);
  assert.match(none.message, /no project files/);
  // from the parameters alone, as the router asks before it places a call
  assert.equal(rnaseqNeedsRunner({ counts: "c.tsv", samples: "s.tsv" }), null);
  assert.match(rnaseqNeedsRunner({ samples: "s.csv" }).message, /FASTQ/);
  assert.equal(scrnaNeedsRunner({ source: "x.tsv.gz" }), null);
  assert.match(scrnaNeedsRunner({ source: "x.H5AD" }).message, /AnnData/);
  assert.deepEqual(rnaseqCounts.expected, ["run", "report", "results", "limitations"]);
  assert.deepEqual(scrna.expected, ["run", "report", "clusters", "cells", "limitations"]);
});

// ------------------------------------------------------------------------------------------------------- runs

test("rnaseq: the inputs copied and checked, Python called on the mounted copies, outputs named as the runner names them, verified", async () => {
  const files = projectStore([["p1", "counts.tsv", COUNTS], ["p1", "samples.tsv", SAMPLES]]);
  const python = cannedPython(RNASEQ_OUT);
  const { svc, store, Worker } = await setup({ python, files });
  const job = await svc.submit("pipeline.rnaseq", { counts: "counts.tsv", samples: "samples.tsv", contrast: "condition,treated,control" }, { project_id: "p1" });
  assert.deepEqual(job.stages.map((s) => [s.id, s.title.en]), [["inputs", "Copy and check the input files"], ["analysis", "Differential expression, VST, PCA and report"], ["verify", "Check the outputs as the runner does"]]);
  const done = await settle(svc, job.id);
  assert.equal(done.state, "succeeded", JSON.stringify(done.error));
  const root = `/work/${job.id}`;

  // the copies in OPFS are the project's bytes; the worker read them where the Python arguments say
  assert.equal(dec.decode(await store.readBytes([...jobDir(job.id), "inputs", "counts.tsv"])), COUNTS);
  const run = python.calls.find((c) => c.name === "run_rnaseq_counts");
  assert.deepEqual(run.args, { counts: `${root}/inputs/counts.tsv`, samples: `${root}/inputs/samples.tsv`, contrast: "condition,treated,control", out_dir: `${root}/out` });
  assert.deepEqual(Worker.instances[0].mounted, [{ root: `${root}/inputs`, files: { "counts.tsv": COUNTS, "samples.tsv": SAMPLES } }]);
  const verify = python.calls.find((c) => c.name === "verify_pipeline");
  assert.deepEqual(verify.args, { kind: "pipeline.rnaseq", out_dir: `${root}/out` });
  // numpy and scipy leased; the work directory freed at the end
  assert.ok(Worker.instances[0].calls.some((c) => c.op === "packages" && c.payload.names.join() === "numpy,scipy"));
  assert.deepEqual(Worker.instances[0].calls.filter((c) => c.op === "free").at(-1).payload, { paths: [root] });

  // outputs: the kind's required ones under the runner's names, the rest by path
  const names = done.artefacts.map((a) => a.name).sort();
  assert.deepEqual(names, ["counts.tsv", "de_diagnostics.tsv", "limitations", "plots/pca.svg", "report", "report.md", "results", "run", "stages.json", "vst.tsv"]);
  assert.deepEqual(done.artefacts.find((a) => a.name === "results").path, ["out", "deseq2_results.tsv"]);
  assert.deepEqual(done.outcome.expected, ["run", "report", "results", "limitations"]);
  assert.deepEqual(done.outcome.missing, []);
  assert.deepEqual(done.outcome.checks, ["verify_run", "required_artefacts", "validator:run", "worker_digests", "page_digests"]);
  assert.equal(done.outcome.complete, true);
  assert.deepEqual(done.result, { out_dir: `${root}/out`, genes: 1 });

  // the run's limitations, then where it was computed (the worker's versions), as the job carries them
  assert.deepEqual(done.limitations, [
    "Counts were given, not quantified here.", "The design has one factor.",
    "Computed in this browser with Pyodide's numpy 2.4.6 and scipy 1.18.0; the native run is the reference, and its numbers can differ in the last digits (held to a stated tolerance)",
  ]);
  assert.equal(done.limitations.at(-1), OMICS.computedLine(VERSIONS));

  // the inputs, on the record and on the receipt: the digests of the bytes copied, and which project file they were
  const counts = files.recs[0];
  assert.deepEqual(done.inputs, {
    counts: { sha256: counts.sha256, bytes: counts.bytes, source: { project_file: counts.id, name: "counts.tsv" } },
    samples: { sha256: files.recs[1].sha256, bytes: files.recs[1].bytes, source: { project_file: files.recs[1].id, name: "samples.tsv" } },
  });
  const receipt = await svc.receipt(job.id);
  assert.equal(receipt.matches, true);
  assert.deepEqual(receipt.doc.inputs, done.inputs);
  assert.equal(receipt.doc.assurance, "outputs_verified");
  assert.equal(receipt.doc.device.evidence, "verified");
  assert.deepEqual(receipt.doc.expected, ["run", "report", "results", "limitations"]);
  assert.equal(receipt.doc.complete, true);

  // the files as a person sees them: by their path under out/, and readable by it
  const listed = await svc.files(job.id);
  assert.equal(listed.find((f) => f.name === "run").file, "run.json");
  assert.equal(await (await svc.readFile(job.id, "deseq2_results.tsv")).text(), RNASEQ_OUT["deseq2_results.tsv"]);
  assert.equal(await (await svc.readFile(job.id, "results")).text(), RNASEQ_OUT["deseq2_results.tsv"]);
});

test("a project file whose bytes do not have the SHA-256 the project recorded is refused, and its copy removed", async () => {
  const files = projectStore();
  files.add("p1", "counts.tsv", COUNTS, { sha256: "0".repeat(64) });
  files.add("p1", "samples.tsv", SAMPLES);
  const python = cannedPython(RNASEQ_OUT);
  const { svc, store } = await setup({ python, files });
  const job = await svc.submit("pipeline.rnaseq", { counts: "counts.tsv", samples: "samples.tsv" }, { project_id: "p1" });
  const done = await settle(svc, job.id);
  assert.equal(done.state, "failed");
  assert.equal(done.error.type, "integrity");
  assert.match(done.error.message, new RegExp(`counts\\.tsv: its bytes in this browser have SHA-256 ${hex(enc.encode(COUNTS)).slice(0, 16)}…, not the 0000000000000000… the project recorded; it was not used`));
  assert.equal(await store.exists([...jobDir(job.id), "inputs", "counts.tsv"]), false);
  assert.equal(python.calls.length, 0, "nothing ran on it");
  assert.deepEqual(done.stages.map((s) => s.status), ["failed", "pending", "pending"]);
});

test("scrna: a sample sheet's files found in the project (by name or id), copied, and the sheet rewritten to the copies", async () => {
  const files = projectStore();
  const a = files.add("p1", "ctrl1.tsv.gz", "gene\tc1\nG1\t3\n");
  const b1 = files.add("p1", "matrix.tsv", "gene\tc2\nG1\t4\n");
  const b2 = files.add("p1", "matrix.tsv", "gene\tc3\nG1\t5\n"); // the same name, another file: both are inputs
  // a quoted cell with the delimiter in it, and the sheet's own columns kept
  files.add("p1", "sheet.csv", `sample,path,batch,note\nctrl1,ctrl1.tsv.gz,b1,"first, of two"\ntrt1,${b1.id},b2,x\ntrt2,${b2.id},b2,y\n`);
  files.add("p1", "markers.tsv", "cell_type\tgene\nB cell\tMS4A1\n");
  const python = cannedPython(SCRNA_OUT);
  const { svc, store, Worker } = await setup({ python, files });
  const job = await svc.submit("pipeline.scrna", { source: "sheet.csv", markers: "markers.tsv", n_top_genes: 600, integration: "harmony" }, { project_id: "p1" });
  const done = await settle(svc, job.id);
  assert.equal(done.state, "succeeded", JSON.stringify(done.error));
  const inputs = `/work/${job.id}/inputs`;
  const staged = Object.keys(Worker.instances[0].mounted[0].files).sort();
  assert.deepEqual(staged, ["2-matrix.tsv", "ctrl1.tsv.gz", "markers.tsv", "matrix.tsv", "sheet.csv"]);
  const sheet = dec.decode(await store.readBytes([...jobDir(job.id), "inputs", "sheet.csv"]));
  assert.equal(sheet, `sample,path,batch,note\nctrl1,${inputs}/ctrl1.tsv.gz,b1,"first, of two"\ntrt1,${inputs}/matrix.tsv,b2,x\ntrt2,${inputs}/2-matrix.tsv,b2,y\n`);
  const run = python.calls.find((c) => c.name === "run_scrna");
  assert.deepEqual(run.args, { source: `${inputs}/sheet.csv`, markers: `${inputs}/markers.tsv`, integration: "harmony", n_top_genes: 600, out_dir: `/work/${job.id}/out` });
  // the sheet as the project holds it, and each of its files as the runner's receipt keys them ("<column>:<line>")
  assert.equal(done.inputs.source.sha256, files.recs[3].sha256);
  assert.deepEqual(done.inputs["source.files"], {
    "path:2": { sha256: a.sha256, bytes: a.bytes, source: { project_file: a.id, name: "ctrl1.tsv.gz" } },
    "path:3": { sha256: b1.sha256, bytes: b1.bytes, source: { project_file: b1.id, name: "matrix.tsv" } },
    "path:4": { sha256: b2.sha256, bytes: b2.bytes, source: { project_file: b2.id, name: "matrix.tsv" } },
  });
  assert.equal(done.inputs.markers.sha256, files.recs[4].sha256);
  assert.ok(done.logs.some((l) => l.text === "matrix.tsv is staged as 2-matrix.tsv: another input has its name"));
  assert.deepEqual(done.outcome.expected, ["run", "report", "clusters", "cells", "limitations"]);
  assert.deepEqual(done.artefacts.map((a) => a.name).sort(), ["cells", "clusters", "limitations", "markers.tsv", "plots/umap_clusters.svg", "report", "run", "stages.json"]);
  assert.equal(done.limitations.at(-1), "The UMAP layout is a picture of the graph: it amplifies last-digit differences and is not compared with the native run's");
  assert.equal((await svc.receipt(job.id)).doc.inputs["source.files"]["path:3"].sha256, b1.sha256);
});

test("the pipeline's own ends: refused (exit 2), incomplete (exit 1, outputs kept), and outputs that do not verify", async () => {
  const files = projectStore([["p1", "counts.tsv", COUNTS], ["p1", "samples.tsv", SAMPLES]]);
  const params = { counts: "counts.tsv", samples: "samples.tsv" };
  {
    const { svc } = await setup({ python: cannedPython(RNASEQ_OUT, { exit: 2, refused: "counts must be integers; round estimated counts first" }), files });
    const done = await settle(svc, (await svc.submit("pipeline.rnaseq", params, { project_id: "p1" })).id);
    assert.deepEqual([done.state, done.error.type], ["failed", "refused"]);
    assert.match(done.error.message, /refused: counts must be integers/);
  }
  {
    const { svc } = await setup({ python: cannedPython(RNASEQ_OUT, { exit: 1 }), files });
    const done = await settle(svc, (await svc.submit("pipeline.rnaseq", params, { project_id: "p1" })).id);
    assert.deepEqual([done.state, done.error.type], ["failed", "failed"]);
    assert.match(done.error.message, /the pipeline did not complete: differential_expression: the design is not full rank/);
    assert.ok(done.artefacts.some((a) => a.name === "run"), "what it wrote is listed");
    assert.ok(done.limitations.length >= 3);
  }
  {
    const verify = () => ({ status: "FAIL", checks: [{ name: "verify_run", status: "FAIL" }, { name: "required_artefacts", status: "PASS" }], problems: ["verify_run: output vst.tsv changed"], report: {} });
    const { svc } = await setup({ python: cannedPython(RNASEQ_OUT, { verify }), files });
    const done = await settle(svc, (await svc.submit("pipeline.rnaseq", params, { project_id: "p1" })).id);
    assert.deepEqual([done.state, done.error.type], ["failed", "failed"]);
    assert.match(done.error.message, /the outputs did not verify: verify_run: output vst\.tsv changed/);
    assert.ok(done.outcome.problems.includes("verify_run: output vst.tsv changed"));
    assert.deepEqual(done.outcome.checks, [], "a failed job passes no checks");
  }
});

test("running out of memory is a refusal that needs memory (the runner), never a runtime error: Python's MemoryError, the worker's own OOM, Python's cell limit", async () => {
  const files = projectStore([["p1", "pbmc.tsv.gz", "gz"]]);
  const params = { source: "pbmc.tsv.gz", n_top_genes: 600 };
  const ends = [
    // what browser_jobs.call makes of a MemoryError that reaches it (numpy's is _ArrayMemoryError)
    ["run_scrna", { ok: false, error: { type: "runtime_error", message: "_ArrayMemoryError: Unable to allocate 3.2 GiB for an array with shape (13568, 31000) and data type float64" } }, /^the analysis ran out of memory: a browser job worker holds at most about 4 GB \(wasm32\); run it on the runner \(run_scrna: _ArrayMemoryError: Unable to allocate/],
    // wasm memory grown past wasm32's limit: Pyodide dies, the pool reports a fatal error
    ["run_scrna", { fatal: "Pyodide has suffered a fatal error. RangeError: WebAssembly.Memory.grow(): Maximum memory size exceeded" }, /^the analysis ran out of memory: .*Maximum memory size exceeded/],
    ["verify_pipeline", { ok: false, error: { type: "runtime_error", message: "MemoryError" } }, /^the check of the outputs ran out of memory/],
    // what browser_jobs.call itself now says of a MemoryError: refused, need memory — kept, with the hint added
    ["run_scrna", { ok: false, error: { type: "refused", need: "memory", message: "MemoryError: the analysis ran out of memory in this browser worker (wasm32, 4 GB)" } }, /^run_scrna: MemoryError: the analysis ran out of memory/],
  ];
  for (const [fn, end, why] of ends) {
    const base = cannedPython(SCRNA_OUT);
    const python = async (name, args, fs, w) => (name === fn ? end : base(name, args, fs, w));
    const { svc, Worker } = await setup({ python, files });
    const done = await settle(svc, (await svc.submit("pipeline.scrna", params, { project_id: "p1" })).id);
    assert.equal(done.state, "failed");
    assert.deepEqual([done.error.type, done.error.need], ["refused", "memory"], JSON.stringify(done.error));
    assert.match(done.error.message, why);
    assert.match(done.error.hint, /Connect the local runner/);
    // its worker is retired, not handed to the next job with its grown memory
    assert.ok(Worker.instances.length > 0 && Worker.instances.every((w) => w.terminated), "the worker refused for memory is terminated");
  }
  // Python refuses before the analysis, with what it would need (more cells than a browser worker holds)
  const limit = cannedPython(SCRNA_OUT, { exit: 2, refused: "13,568 cells exceed the 6,000 a browser worker can hold (wasm32, 4 GB): run it on the runner" });
  const { svc } = await setup({ python: async (n, a, fs, w) => (n === "run_scrna" ? { ...(await limit(n, a, fs, w)), need: "memory" } : limit(n, a, fs, w)), files });
  const done = await settle(svc, (await svc.submit("pipeline.scrna", params, { project_id: "p1" })).id);
  assert.deepEqual([done.error.type, done.error.need], ["refused", "memory"]);
  assert.match(done.error.message, /^refused: 13,568 cells exceed the 6,000 a browser worker can hold/);
  // any other failure keeps its own type
  const other = cannedPython(SCRNA_OUT);
  const { svc: svc2 } = await setup({ python: async (n, a, fs, w) => (n === "run_scrna" ? { ok: false, error: { type: "runtime_error", message: "ValueError: bad table" } } : other(n, a, fs, w)), files });
  const failed = await settle(svc2, (await svc2.submit("pipeline.scrna", params, { project_id: "p1" })).id);
  assert.deepEqual([failed.error.type, failed.error.need], ["runtime_error", undefined]);
});

test("a call whose files only the runner reads reaches the model as needs_runner, not as a missing job service", async () => {
  const files = projectStore([
    ["p1", "pbmc.h5ad", "x"], ["p1", "counts.tsv", COUNTS],
    ["p1", "fastq.csv", "sample,fastq_1,fastq_2,condition\nc1,a_1.fq.gz,a_2.fq.gz,control\n"],
  ]);
  const { svc } = await setup({ files });
  const rt = new BrowserJobsRuntime({ service: svc });
  // named by its id, the source is an .h5ad only by the file's own name; the sample table is a FASTQ sheet only inside
  for (const [tool, args, why] of [
    ["call_tool", { tool: "job.pipeline.scrna", arguments: { source: files.recs[0].id } }, /AnnData \(\.h5ad\) needs anndata/],
    ["run_pipeline", { pipeline: "rnaseq", arguments: { counts: "counts.tsv", samples: "fastq.csv" } }, /fastq\.csv is a FASTQ sample sheet/],
  ]) {
    assert.equal(rt.needsRunner(jobCall(tool, args).kind, jobCall(tool, args).params), null, "not known from the parameters");
    const env = await rt.call(tool, args, { project_id: "p1" });
    assert.equal(env.status, "failed");
    assert.equal(env.error.reason, "needs_runner");
    assert.deepEqual(env.error.requires.map((q) => q.need), ["runner"]);
    assert.match(env.error.message, why);
  }
  // a refusal about the browser's own storage is still the job service's
  const none = await rt.call("run_pipeline", { pipeline: "rnaseq", arguments: { counts: "counts.tsv", samples: "fastq.csv" } }, { project_id: null });
  assert.equal(none.error.reason, undefined, "a missing file is bad arguments");
  assert.equal(none.error.type, "bad_arguments");
});

test("a resumed job copies its inputs again and reuses its analysis when the outputs still re-hash; changed inputs run it again", async () => {
  const files = projectStore([["p1", "counts.tsv", COUNTS], ["p1", "samples.tsv", SAMPLES]]);
  let failVerify = true;
  const python = cannedPython(RNASEQ_OUT, { verify: () => (failVerify ? { status: "FAIL", checks: [], problems: ["not yet"], report: {} } : PASS) });
  const { svc, Worker } = await setup({ python, files });
  const job = await svc.submit("pipeline.rnaseq", { counts: "counts.tsv", samples: "samples.tsv" }, { project_id: "p1" });
  assert.equal((await settle(svc, job.id)).state, "failed");
  failVerify = false;
  await svc.resume(job.id);
  const done = await settle(svc, job.id);
  assert.equal(done.state, "succeeded", JSON.stringify(done.error));
  assert.deepEqual(done.stages.map((s) => [s.id, s.reused]), [["inputs", false], ["analysis", true], ["verify", false]]);
  assert.equal(python.calls.filter((c) => c.name === "run_rnaseq_counts").length, 1, "the analysis ran once");
  // the outputs put back in the worker for verify_pipeline, without the earlier attempt's job receipt
  const w = Worker.instances.at(-1);
  assert.ok(w.calls.some((c) => c.op === "restore"));
  assert.ok(w.calls.some((c) => c.op === "free" && c.payload.paths[0].endsWith("/out/job_receipt.json")));
  assert.equal(done.attempts, 2);
  assert.ok(done.inputs.counts.sha256, "the second attempt recorded its inputs");
  assert.ok(done.limitations.at(-1).startsWith("Computed in this browser"));
  assert.deepEqual(done.outcome.checks.slice(0, 3), ["verify_run", "required_artefacts", "validator:run"]);

  // a newer file of the same name before the resume: the analysis's inputs changed, so it runs again on the new one
  // (and lists what it wrote this time: a file only the first attempt wrote is not looked for)
  failVerify = true;
  RNASEQ_OUT["first_only.tsv"] = "x\n";
  const again = await svc.submit("pipeline.rnaseq", { counts: "counts.tsv", samples: "samples.tsv" }, { project_id: "p1" });
  const first = await settle(svc, again.id);
  delete RNASEQ_OUT["first_only.tsv"];
  assert.equal(first.state, "failed");
  assert.ok(first.artefacts.some((a) => a.name === "first_only.tsv"));
  const newer = files.add("p1", "counts.tsv", `${COUNTS}G2\tB\t1\t2\n`);
  failVerify = false;
  await svc.resume(again.id);
  const second = await settle(svc, again.id);
  assert.equal(second.state, "succeeded");
  assert.deepEqual(second.stages.map((s) => [s.id, s.reused]), [["inputs", false], ["analysis", false], ["verify", false]]);
  assert.equal(python.calls.filter((c) => c.name === "run_rnaseq_counts").length, 3, "the second job's analysis ran in both attempts");
  assert.equal(second.inputs.counts.sha256, newer.sha256, "the newest file of the name");
  assert.equal(second.artefacts.some((a) => a.name === "first_only.tsv"), false);
  assert.deepEqual(second.outcome.missing, []);
  assert.ok(second.logs.some((l) => /analysis: its checkpoint does not verify, so it runs again \(the stage's inputs changed since its checkpoint\)/.test(l.text)));
});

// ------------------------------------------------------------------------------------------------------- the page

test("run_pipeline is a job call: job.pipeline.<pipeline> with the kind's arguments; the runtime says what needs the runner", async () => {
  assert.deepEqual(jobCall("run_pipeline", { pipeline: "rnaseq", arguments: { counts: "c.tsv", samples: "s.tsv" } }), { kind: "pipeline.rnaseq", params: { counts: "c.tsv", samples: "s.tsv" } });
  assert.deepEqual(jobCall("call_tool", { tool: "run_pipeline", arguments: { pipeline: "scrna", arguments: { source: "x.tsv" } } }), { kind: "pipeline.scrna", params: { source: "x.tsv" } });
  assert.deepEqual(jobCall("call_tool", { tool: "job.pipeline.scrna", arguments: { source: "x.tsv" } }), { kind: "pipeline.scrna", params: { source: "x.tsv" } });
  assert.equal(jobCall("run_pipeline", { arguments: {} }), null);
  const submitted = [];
  const service = {
    status: "ready", reason: null, kinds: () => OMICS_HANDLERS.map((h) => ({ kind: h.kind })), events: () => () => {},
    submit: async (kind, params, opts) => {
      submitted.push({ kind, params, opts });
      return { id: "bj-0123456789abcdef", kind, state: "queued", title: { zh: "x", en: "x" }, stages: [] };
    },
  };
  const rt = new BrowserJobsRuntime({ service });
  const env = await rt.call("run_pipeline", { pipeline: "rnaseq", arguments: { counts: "c.tsv", samples: "s.tsv" } }, { project_id: "p1" });
  assert.equal(env.status, "job_submitted");
  assert.deepEqual(submitted, [{ kind: "pipeline.rnaseq", params: { counts: "c.tsv", samples: "s.tsv" }, opts: { project_id: "p1", submission_id: null } }]);
  assert.equal(env.via, "job.pipeline.rnaseq");
  assert.equal(rt.needsRunner("pipeline.rnaseq", { counts: "c.tsv", samples: "s.tsv" }), null);
  assert.match(rt.needsRunner("pipeline.rnaseq", { samples: "s.csv" }).message, /FASTQ/);
  assert.match(rt.needsRunner("pipeline.scrna", { source: "a.tsv", integration: "scvi" }).hint, /Connect the local runner/);
  assert.equal(rt.needsRunner("research.run", { question: "q" }), null);
  // said even where the job service cannot start: it is the handlers' own knowledge
  const off = new BrowserJobsRuntime({ service: { status: "unsupported", reason: "no OPFS", kinds: () => [], onStatus: () => () => {} } });
  assert.match(off.needsRunner("pipeline.scrna", { source: "a.h5ad" }).message, /AnnData/);
});

test("project files: by id within the job's project, by name the newest of that name; nothing across projects", async () => {
  const files = projectStore();
  const old = files.add("p1", "counts.tsv", "old");
  const other = files.add("p2", "counts.tsv", "other project");
  const newer = files.add("p1", "counts.tsv", "new");
  const find = projectFilesResolver(files).find;
  assert.equal((await find("p1", "counts.tsv")).id, newer.id);
  assert.equal((await find("p1", old.id)).id, old.id);
  assert.equal(await find("p1", other.id), null, "another project's file is not this job's");
  assert.equal((await find(null, other.id)).id, other.id, "a job of no project names a file by its id");
  assert.equal(await find(null, "counts.tsv"), null);
  assert.equal(await find("p1", "  "), null);
  const got = await find("p2", " counts.tsv ");
  assert.deepEqual({ name: got.name, bytes: got.bytes, sha256: got.sha256 }, { name: "counts.tsv", bytes: other.bytes, sha256: other.sha256 });
  assert.equal(await got.blob.text(), "other project");
  assert.equal(projectFilesResolver(null), null);
});

test("sample sheets are read as Python's csv module reads them (quotes, CRLF, blank lines, a byte-order mark)", () => {
  assert.deepEqual(parseDelimited('\uFEFFsample,path\r\n"a, b",x.tsv\r\n\r\n"say ""hi""",y\n', ","), [["sample", "path"], ["a, b", "x.tsv"], ['say "hi"', "y"]]);
  assert.deepEqual(parseDelimited("sample\tpath\nc1\t\n", "\t"), [["sample", "path"], ["c1", ""]]);
  assert.deepEqual(parseDelimited('a,"multi\nline"\nb,c', ","), [["a", "multi\nline"], ["b", "c"]]);
});
