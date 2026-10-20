// Browser research jobs, checked in a real browser (science.mjs drives this page with Playwright). The result lands in
// window.__result.
//
// ?mode=selftest  one job worker studies the planted world (tcmstudio.browser_selftest) and compares its five tables
//                 with the answer native CPython committed
// ?mode=gqd       the raw files the harness serves at /raw/ are imported into this origin's private storage, then the
//                 browser job service runs research.snapshot and research.run (葛根芩连汤) exactly as Studio does; the
//                 exports come back with their digests for science.mjs to compare with the native run
// ?mode=service   the job service end to end without real data: the planted world's snapshots are seeded into private
//                 storage (as research.snapshot would leave them), then research.run and the self-test run as jobs of the
//                 service; research.run's five tables must be the self-test's committed answer (same world, same
//                 parameters), its receipt must match its recorded digest with outputs_verified, and a second run whose
//                 workers are lost after its research stage must fail, then resume from that stage's checkpoint
// ?mode=fixture   the snapshot build on raw files mounted from private storage: the parser tests' fixture files
//                 (served at /raw/) are written into OPFS, mounted read-only in a job worker, and built with their own
//                 checksums in place of the reviewed ones (fixture_specs.py); a write to a mounted file must fail and
//                 the OPFS bytes must not change. Then the same files are imported through the dataset workspace (a
//                 registry naming them with their own checksums) and research.snapshot runs as a job of the service,
//                 down to its persisted snapshots and marker; science.mjs compares both builds' snapshot ids with the
//                 same build run natively
// ?mode=omics     the RNA-seq (count matrix) and single-cell jobs: the files the harness serves at /raw/ (omics_native.py
//                 prepare) become a test project's files in the page's own project store, and the job service runs
//                 pipeline.rnaseq and pipeline.scrna on them with omics-plan.mjs's parameters, exactly as Studio does;
//                 each job's receipt must match its recorded digest with outputs_verified and device evidence verified,
//                 its inputs recorded at the project files' digests; every output comes back with its digest and text
//                 for science.mjs to compare with the native command's and to hold to the known answer
// ?mode=admet     the ADMET jobs: the files the harness serves at /raw/ (admet_native.py prepare: a TDC-shaped archive and
//                 a molecule set) become a test project's files, and the job service runs admet.models (the archive given
//                 as a project file) and then pipeline.admet on the molecules with admet-plan.mjs's parameters, exactly
//                 as Studio does: the chemistry in the RDKit.js worker, the models trained in a job worker, the domain
//                 search on this browser's kernels. Every card, output, similarity file and prepared row comes back for
//                 science.mjs to compare with the native runs. The same browser chemistry prepares each endpoint's
//                 training splits once more (which training molecules it refused, held to the cards' counts and the
//                 job's own stage records, so that the native models are trained on the molecules kept here) and the
//                 test splits, whose predictions a probe computes at full precision with the browser's models in a
//                 job worker (admet_native.py exact), as it does for the pipeline's molecules. Then pipeline.admet runs
//                 again with the handlers' harness-only switch that forces the domain search onto the GPU, a software
//                 adapter allowed (SwiftShader: science.mjs launches Chromium with it): its outputs must be the first
//                 run's, its compute facts say webgpu on a software adapter, never accelerated
// ?index=URL      where Pyodide is (default: boot.json's index_url)
// ?rdkit=URL      (admet) where RDKit_minimal.wasm is (default: boot.json's rdkit url); it must be boot.json's pin
// ?real=1&endpoints=a,b   (admet) the archive is a real TDC ADMET group archive (admet-plan.mjs)

import { openStore } from "../../js/core/store.js";
import { DatasetWorkspace } from "../../js/scientific/data/datasets.js";
import { OpfsStore } from "../../js/scientific/data/opfs-store.js";
import { BrowserJobService, jobDir } from "../../js/scientific/runtime/job-manager.js";
import { projectFilesResolver } from "../../js/scientific/runtime/jobs-runtime.js";
import { ScienceWorkerPool, recommendedPoolSize } from "../../js/scientific/runtime/worker-pool.js";
import { preparedFiles } from "../../js/scientific/chem/prepared-set.js";
import { N_BITS_BYTES, N_FEATURES } from "../../js/scientific/chem/features.js";
import { ChemClient, createAdmetHandlers, modelsDir } from "../../js/scientific/pipelines/admet.js";
import { HANDLERS, RESEARCH_EXPORTS, RESEARCH_PATHS, SNAPSHOTS_BUILT_SCHEMA } from "../../js/scientific/pipelines/network-pharmacology.js";
import { OMICS_HANDLERS } from "../../js/scientific/pipelines/omics.js";
import { browserSelfTest } from "../../js/scientific/pipelines/selftest.js";
import { admetPlan } from "./admet-plan.mjs";
import { OMICS_JOBS } from "./omics-plan.mjs";

const q = new URLSearchParams(location.search);
const mode = q.get("mode") || "selftest";
const indexUrl = q.get("index") || undefined;
const out = { mode, ok: true, failures: [], checks: [], isolated: Boolean(globalThis.crossOriginIsolated), user_agent: navigator.userAgent };
const $rows = document.getElementById("rows");
const $status = document.getElementById("status");

function check(label, ok, detail = "", ms = null) {
  out.checks.push({ label, ok: Boolean(ok), detail: String(detail).slice(0, 600), ms });
  if (!ok) { out.ok = false; out.failures.push(`${label}: ${detail}`); }
  const tr = document.createElement("tr");
  for (const [i, text] of [label, ok ? "ok" : "FAILED", String(detail).slice(0, 300), ms === null ? "" : String(Math.round(ms))].entries()) {
    const td = document.createElement("td");
    td.textContent = text;
    if (i === 1) td.className = ok ? "ok" : "bad";
    tr.append(td);
  }
  $rows.append(tr);
  return Boolean(ok);
}

async function boot() {
  const res = await fetch("/runtime/boot.json", { cache: "no-cache" });
  return res.json();
}

function pool(bootDoc) {
  const size = recommendedPoolSize({ cores: navigator.hardwareConcurrency, memoryGb: navigator.deviceMemory, isolated: globalThis.crossOriginIsolated });
  out.pool = size;
  return new ScienceWorkerPool({
    size: size.size, workerUrl: new URL("/js/scientific/engines/science.worker.js", location.href).href,
    boot: bootDoc, siteUrl: new URL("/", location.href).href, indexUrl,
  });
}

async function selftest() {
  const p = pool(await boot());
  const t0 = performance.now();
  const lease = await p.lease({ packages: ["pyarrow"] });
  check("job worker booted", true, `${lease.id}`, performance.now() - t0);
  try {
    const t1 = performance.now();
    const text = await lease.call("run", { fn: "self_test", args: { work_dir: "/work/selftest" } }, { timeoutMs: 600_000 });
    const doc = typeof text === "string" ? JSON.parse(text) : text;
    const report = doc?.result ?? doc;
    out.selftest = report;
    check("planted world studied in the browser", doc?.ok !== false && report?.exit !== undefined, doc?.error?.message || `exit ${report?.exit}`, performance.now() - t1);
    check("its exports recompute (np_exports verify)", report?.verify?.status === "PASS", JSON.stringify(report?.verify?.problems || []).slice(0, 300));
    for (const [name, t] of Object.entries(report?.tables || {})) {
      check(`${name} equals the committed answer`, ["identical", "within_tolerance"].includes(t.status), t.status === "identical" ? "identical bytes" : JSON.stringify(t).slice(0, 300));
    }
    check("self-test verdict", report?.status === "PASS", report?.status);
  } finally {
    lease.release();
    p.terminateAll();
  }
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function sha256Hex(bytes) {
  const d = new Uint8Array(await crypto.subtle.digest("SHA-256", bytes));
  return [...d].map((b) => b.toString(16).padStart(2, "0")).join("");
}

async function waitFor(svc, id, timeoutMs) {
  const ended = ["succeeded", "failed", "cancelled", "interrupted"];
  const t0 = performance.now();
  for (;;) {
    const job = await svc.get(id);
    if (job && ended.includes(job.state)) return job;
    if (performance.now() - t0 > timeoutMs) return job;
    $status.textContent = `${job?.kind}: ${job?.state} ${job?.progress ? `${Math.round((job.progress.fraction || 0) * 100)}% ${job.progress.message || ""}` : ""}`;
    await new Promise((r) => setTimeout(r, 1000));
  }
}

async function gqd() {
  const bootDoc = await boot();
  const { store, reason } = await OpfsStore.open();
  if (!check("private storage (OPFS)", Boolean(store), store ? "" : reason)) return;
  const datasets = new DatasetWorkspace({
    store, registryUrl: "/runtime/datasets.json", workerUrl: new URL("/js/scientific/data/data.worker.js", location.href).href,
  });
  // the raw files the live-data workflow downloaded, imported as a person would import theirs
  const listed = await (await fetch("/raw/index.json")).json();
  const plan = await datasets.plan();
  const wanted = new Set(plan.files.map((f) => f.name));
  const files = new Map();
  for (const f of listed) {
    if (!wanted.has(f.name)) continue;
    const blob = await (await fetch(`/raw/${encodeURIComponent(f.name)}`)).blob();
    files.set(f.name, new File([blob], f.name));
  }
  const t0 = performance.now();
  const rows = await datasets.acquire({ names: [...files.keys()], files });
  out.imported = rows.map((r) => ({ file: r.file, status: r.status, bytes: r.bytes, route: r.route }));
  check("raw files imported into private storage", rows.every((r) => ["PASS", "UNVERIFIED"].includes(r.status)), rows.filter((r) => !["PASS", "UNVERIFIED"].includes(r.status)).map((r) => `${r.file}: ${r.reason}`).join("; ") || `${rows.length} files`, performance.now() - t0);

  const svc = new BrowserJobService({ pool: pool(bootDoc), store, datasets, handlers: HANDLERS });
  await svc.start();
  const t1 = performance.now();
  let job = await svc.submit("research.snapshot", {}, { project_id: "science-check" });
  job = await waitFor(svc, job.id, 60 * 60_000);
  out.snapshot_job = job;
  if (!check("research.snapshot in the browser", job?.state === "succeeded", job?.error?.message || job?.state, performance.now() - t1)) return;

  const t2 = performance.now();
  job = await svc.submit("research.run", {
    question: "葛根芩连汤的实测靶点集中在哪些通路？", formula: "葛根芩连汤",
    activity: ["npass", "cmaup", "lotus"], optional: ["string"],
  }, { project_id: "science-check" });
  job = await waitFor(svc, job.id, 60 * 60_000);
  out.run_job = job;
  if (!check("research.run in the browser", job?.state === "succeeded", job?.error?.message || job?.state, performance.now() - t2)) return;
  check("job receipt written", Boolean(job.receipt?.sha256), JSON.stringify(job.receipt || {}));
  const receipt = await svc.receipt(job.id);
  out.receipt = receipt?.doc || null;
  check("job receipt matches its recorded digest", receipt?.matches === true, receipt?.sha256 || "");
  out.files = {};
  for (const name of RESEARCH_EXPORTS) {
    const file = await svc.readFile(job.id, ["out", name]).catch(() => null);
    if (!file) continue;
    const bytes = new Uint8Array(await file.arrayBuffer());
    const d = new Uint8Array(await crypto.subtle.digest("SHA-256", bytes));
    const sha = `sha256:${[...d].map((b) => b.toString(16).padStart(2, "0")).join("")}`;
    // the text too, for science.mjs to compare cell by cell should the bytes differ (every export is text)
    out.files[name] = { sha256: sha, bytes: bytes.byteLength, ...(bytes.byteLength < 16e6 ? { text: new TextDecoder().decode(bytes) } : {}) };
  }
  check("nine exports read back", Object.keys(out.files).length === RESEARCH_EXPORTS.length, Object.keys(out.files).join(", "));
}

async function service() {
  const bootDoc = await boot();
  const { store, reason } = await OpfsStore.open();
  if (!check("private storage (OPFS)", Boolean(store), store ? "" : reason)) return;
  await store.remove(["tcmstudio"], { recursive: true }).catch(() => false);
  const p = pool(bootDoc);
  // seed: the planted world's snapshots, built by a job worker and kept where research.snapshot keeps them
  const t0 = performance.now();
  const lease = await p.lease({ packages: ["pyarrow"] });
  try {
    const seeded = await lease.call("run", { fn: "self_test", args: { work_dir: "/work/seed" } });
    if (!check("planted world built in a job worker", seeded?.ok !== false && seeded?.result?.status === "PASS", seeded?.error?.message || seeded?.result?.status)) return;
    const kept = await lease.call("persist", { from: "/work/seed/snapshots", dir: RESEARCH_PATHS.snapshots, clean: true });
    const ledger = kept.files.find((f) => f.path.join("/") === "ledger.jsonl");
    await store.writeJson(RESEARCH_PATHS.built, { schema: SNAPSHOTS_BUILT_SCHEMA, job_id: "seed", at: new Date().toISOString(), ledger: { sha256: `sha256:${ledger.sha256}`, bytes: ledger.bytes }, files: kept.files.length });
    check("snapshots seeded into private storage", Boolean(ledger), `${kept.files.length} files`, performance.now() - t0);
    out.expected = seeded.result.expected_digests;
  } finally {
    lease.release();
  }
  const svc = new BrowserJobService({ pool: p, store, datasets: null, handlers: [...HANDLERS, browserSelfTest] });
  await svc.start();
  check("job service started", svc.status === "ready", svc.reason || svc.status);

  const t1 = performance.now();
  let job = await svc.submit("research.run", { question: "葛根芩连汤的实测靶点集中在哪些通路？", background: "assayed", permutations: 200 }, { project_id: "science-check" });
  job = await waitFor(svc, job.id, 10 * 60_000);
  out.run_job = job;
  if (!check("research.run as a browser job", job?.state === "succeeded", job?.error?.message || job?.state, performance.now() - t1)) return;
  check("its stages", job.stages.every((s) => s.status === "succeeded"), job.stages.map((s) => `${s.id}:${s.status}${s.worker ? `@${s.worker}` : ""}`).join(" "));
  const receipt = await svc.receipt(job.id);
  out.receipt = receipt?.doc || null;
  check("receipt at its recorded digest", receipt?.matches === true, JSON.stringify({ placement: receipt?.doc?.placement, device: receipt?.doc?.device, assurance: receipt?.doc?.assurance }));
  check("receipt says the browser verified the outputs", receipt?.doc?.device?.evidence === "verified", JSON.stringify(receipt?.doc?.device));
  check("receipt: both digest readings agree (outputs_verified)", receipt?.doc?.assurance === "outputs_verified", String(receipt?.doc?.assurance));
  for (const name of ["compounds.tsv", "targets.tsv", "disease_targets.tsv", "enrichment.tsv", "network.tsv"]) {
    const a = (job.artefacts || []).find((x) => x.name === name);
    check(`${name} is the committed answer`, a && `sha256:${a.sha256}` === out.expected?.[name], `${a?.sha256 || "missing"} vs ${out.expected?.[name]}`);
  }
  const cp = await svc.readFile(job.id, ["checkpoints", "research.json"]).then((f) => f.text()).then(JSON.parse).catch((e) => ({ error: String(e) }));
  check("the research stage's checkpoint", cp?.schema === "tcmstudio.checkpoint/1" && (cp.outputs || []).length > 0 && Boolean(cp.inputs_digest), JSON.stringify({ schema: cp?.schema, outputs: cp?.outputs?.length, error: cp?.error }));

  // a run whose job workers are lost after its research stage fails; resumed, it reuses that stage from its checkpoint
  const t3 = performance.now();
  // the workers are stopped from the job's own event, as the research stage ends: no race with the verify stage
  let stopped = null;
  let lost = await svc.submit("research.run", { question: "葛根芩连汤的实测靶点集中在哪些通路？", background: "assayed", permutations: 200 }, { project_id: "science-check" });
  const unsubscribe = svc.events(lost.id, (ev) => {
    const research = ev?.job?.stages?.find((x) => x.id === "research");
    if (!stopped && ev?.type === "stages" && research?.status === "succeeded") {
      stopped = ev.job.stages.map((x) => `${x.id}:${x.status}`);
      p.terminateAll();
    }
  });
  lost = await waitFor(svc, lost.id, 10 * 60_000);
  unsubscribe?.();
  out.lost_job = { stopped_at: stopped, state: lost?.state, stages: lost?.stages?.map((x) => `${x.id}:${x.status}:${x.detail || ""}`) };
  if (check("workers lost after the research stage: the job fails", lost?.state === "failed", `${lost?.state}: ${lost?.error?.message || ""}`)) {
    await svc.resume(lost.id);
    lost = await waitFor(svc, lost.id, 10 * 60_000);
    const research = lost?.stages?.find((x) => x.id === "research");
    check("resumed: the research stage reused from its checkpoint, the rest run again", lost?.state === "succeeded" && research?.reused === true
      && lost.stages.filter((x) => x.id !== "research" && x.id !== "prepare").every((x) => !x.reused),
    lost?.stages?.map((x) => `${x.id}:${x.status}${x.reused ? "(reused)" : ""}`).join(" ") || lost?.state, performance.now() - t3);
    for (const name of ["enrichment.tsv", "targets.tsv"]) {
      const a = (lost?.artefacts || []).find((x) => x.name === name);
      check(`resumed: ${name} is still the committed answer`, a && `sha256:${a.sha256}` === out.expected?.[name], `${a?.sha256 || "missing"}`);
    }
    // the reused stage's artifact still says how the analysis ran, and the resumed job says so too
    check("resumed: the job still states the in-process isolation", (lost?.limitations || []).some((l) => /isolation: in_process/.test(l)), JSON.stringify(lost?.limitations || []));
  }

  const t2 = performance.now();
  let st = await svc.submit("browser.selftest", {}, { project_id: null });
  st = await waitFor(svc, st.id, 10 * 60_000);
  out.selftest_job = st;
  check("self-test as a browser job", st?.state === "succeeded", st?.error?.message || st?.state, performance.now() - t2);
  p.terminateAll();
}

async function fixture() {
  const bootDoc = await boot();
  const { store, reason } = await OpfsStore.open();
  if (!check("private storage (OPFS)", Boolean(store), store ? "" : reason)) return;
  await store.remove(["tcmstudio"], { recursive: true }).catch(() => false);
  const listed = await (await fetch("/raw/index.json")).json();
  const raw = new Map();
  for (const f of listed) raw.set(f.name, new Uint8Array(await (await fetch(`/raw/${encodeURIComponent(f.name)}`)).arrayBuffer()));
  for (const [name, bytes] of raw) await store.writeBytes([...RESEARCH_PATHS.raw, name], bytes);
  const before = new Map();
  for (const [name, bytes] of raw) before.set(name, await sha256Hex(bytes));
  check("fixture files written to private storage", listed.length > 0, listed.map((f) => f.name).join(", "));
  // the worker driven directly: the python op exists only for a worker booted with debug (test pages)
  const w = new Worker("/js/scientific/engines/science.worker.js", { type: "module" });
  let seq = 0;
  const pending = new Map();
  w.onmessage = (e) => {
    const m = typeof e.data === "string" ? JSON.parse(e.data) : e.data;
    if (m.type === "event") return;
    pending.get(m.id)?.(m);
    pending.delete(m.id);
  };
  const call = async (op, payload) => {
    const m = await new Promise((r) => { const id = ++seq; pending.set(id, r); w.postMessage(JSON.stringify({ id, op, payload })); });
    if (!m.ok) throw new Error(`${op}: ${m.error?.type}: ${m.error?.message}`);
    return m.result;
  };
  try {
    await call("init", { boot: bootDoc, siteUrl: `${location.origin}/`, indexUrl, debug: true });
    await call("packages", { names: ["pyarrow"] });
    const t0 = performance.now();
    const m = await call("mount", { root: "/work/fx/raw", dir: RESEARCH_PATHS.raw, files: listed.map((f) => f.name) });
    check("raw files mounted read-only from OPFS", m.files.length === listed.length, `${m.mode}, ${m.bytes} bytes`);
    const helper = await (await fetch("/test/runtime/fixture_specs.py")).text();
    const args = { raw_dir: "/work/fx/raw", snapshot_root: "/work/fx/snap", ledger: "/work/fx/snap/ledger.jsonl", report_path: "/work/fx/report.json" };
    const text = await call("python", { code: `${helper}
patch("/work/fx/raw")
from tcmstudio.browser_jobs import call as _call
result = _call("build_snapshots", ${JSON.stringify(JSON.stringify(args))})` });
    const doc = JSON.parse(text);
    out.report = doc.result || null;
    check("snapshots built in the job worker", doc.ok && ["PASS", "UNVERIFIED"].includes(doc.result?.status), doc.error?.message || doc.result?.status, performance.now() - t0);
    const first = listed[0].name;
    const tried = await call("python", { code: `
try:
    with open(${JSON.stringify(`/work/fx/raw/${first}`)}, "r+b") as fh:
        fh.write(b"x")
    result = "written"
except OSError as e:
    result = f"refused: {type(e).__name__} errno {e.errno}"` });
    check("a write to a mounted raw file is refused", String(tried).startsWith("refused"), String(tried));
    await call("unmount", { root: "/work/fx/raw" });
  } finally {
    w.terminate();
  }
  const changed = [];
  for (const name of raw.keys()) {
    if ((await sha256Hex(await store.readBytes([...RESEARCH_PATHS.raw, name]))) !== before.get(name)) changed.push(name);
  }
  check("the raw files in OPFS are the bytes written before the build", !changed.length, changed.join(", ") || `${raw.size} files re-hashed`);
  await snapshotJob(bootDoc, store, raw);
}

/** research.snapshot as a job of the service on the fixture files, imported through the dataset workspace with a
 * registry that names them with their own checksums; the worker's reviewed specs are patched the same way. */
async function snapshotJob(bootDoc, store, raw) {
  await store.remove(["tcmstudio"], { recursive: true }).catch(() => false);
  const real = await (await fetch("/runtime/datasets.json")).json();
  const files = [];
  for (const f of real.groups.research.files) {
    const bytes = raw.get(f.name);
    if (bytes) files.push({ ...f, bytes: bytes.byteLength, checksum: `sha256:${await sha256Hex(bytes)}` });
  }
  const doc = { ...real, groups: { research: { ...real.groups.research, files } } };
  const registryUrl = URL.createObjectURL(new Blob([JSON.stringify(doc)], { type: "application/json" }));
  const datasets = new DatasetWorkspace({ store, registryUrl, workerUrl: new URL("/js/scientific/data/data.worker.js", location.href).href });
  const rows = await datasets.acquire({ names: files.map((f) => f.name), files: new Map(files.map((f) => [f.name, new File([raw.get(f.name)], f.name)])) });
  if (!check("fixture files imported through the dataset workspace", rows.length === files.length && rows.every((r) => r.status === "PASS"), rows.filter((r) => r.status !== "PASS").map((r) => `${r.file}: ${r.status} ${r.reason}`).join("; ") || `${rows.length} files`)) return;
  // one worker, booted with its debug op, its reviewed specs patched to the fixtures' before the job leases it
  const p = new ScienceWorkerPool({
    size: 1, workerUrl: new URL("/js/scientific/engines/science.worker.js", location.href).href,
    boot: bootDoc, siteUrl: new URL("/", location.href).href, indexUrl, debug: true,
  });
  try {
    const lease = await p.lease({ packages: ["pyarrow"] });
    try {
      await lease.call("mount", { root: "/work/patch/raw", dir: datasets.rawDir(), files: files.map((f) => f.name) });
      const helper = await (await fetch("/test/runtime/fixture_specs.py")).text();
      await lease.call("python", { code: `${helper}
result = ",".join(patch("/work/patch/raw"))` });
      await lease.call("unmount", { root: "/work/patch/raw" });
    } finally {
      lease.release();
    }
    const svc = new BrowserJobService({ pool: p, store, datasets, handlers: HANDLERS });
    await svc.start();
    const t0 = performance.now();
    let job = await svc.submit("research.snapshot", {}, { project_id: "science-check" });
    job = await waitFor(svc, job.id, 10 * 60_000);
    out.snapshot_job = job;
    if (!check("research.snapshot as a browser job", job?.state === "succeeded", job?.error?.message || job?.state, performance.now() - t0)) return;
    out.service_report = job.result || null;
    check("its stages", job.stages.every((x) => x.status === "succeeded"), job.stages.map((x) => `${x.id}:${x.status}`).join(" "));
    const marker = await store.readJson(RESEARCH_PATHS.built).catch(() => null);
    const ledger = await store.readBytes([...RESEARCH_PATHS.snapshots, "ledger.jsonl"]).catch(() => null);
    check("snapshots kept with their marker, the ledger at the marker's digest", marker?.schema === SNAPSHOTS_BUILT_SCHEMA && marker?.job_id === job.id && ledger
      && marker.ledger?.sha256 === `sha256:${await sha256Hex(ledger)}`, JSON.stringify({ schema: marker?.schema, ledger: marker?.ledger }));
    const receipt = await svc.receipt(job.id);
    check("its receipt at its recorded digest, outputs_verified", receipt?.matches === true && receipt?.doc?.assurance === "outputs_verified", JSON.stringify({ assurance: receipt?.doc?.assurance, evidence: receipt?.doc?.device?.evidence }));
  } finally {
    p.terminateAll();
    URL.revokeObjectURL(registryUrl);
  }
}

async function omics() {
  const bootDoc = await boot();
  const { store, reason } = await OpfsStore.open();
  if (!check("private storage (OPFS)", Boolean(store), store ? "" : reason)) return;
  await store.remove(["tcmstudio"], { recursive: true }).catch(() => false);
  // the served files become a test project's files in the page's project store (core/store.js, IndexedDB), as a
  // person's attachments do, and the job service finds them as Studio's does (projectFilesResolver over that store)
  const projects = await openStore({ name: "tcmstudio-science-check" });
  const project = await projects.projects.create({ name: "science check: omics" });
  const listed = await (await fetch("/raw/index.json")).json();
  const t0 = performance.now();
  const recorded = new Map();
  for (const f of listed) {
    const blob = await (await fetch(`/raw/${encodeURIComponent(f.name)}`)).blob();
    const rec = await projects.files.add(project.id, new File([blob], f.name), { name: f.name });
    recorded.set(rec.id, rec);
  }
  out.project_files = [...recorded.values()].map((r) => ({ id: r.id, name: r.name, bytes: r.bytes, sha256: r.sha256 }));
  check("the inputs are a project's files", recorded.size === listed.length && listed.length > 0,
    `${out.project_files.map((r) => `${r.name} (${r.bytes} bytes)`).join(", ")}${projects.persistent ? "" : "; in memory (IndexedDB unavailable)"}`, performance.now() - t0);
  const p = pool(bootDoc);
  const svc = new BrowserJobService({ pool: p, store, projectFiles: projectFilesResolver(projects.files), handlers: OMICS_HANDLERS });
  await svc.start();
  check("job service started", svc.status === "ready", svc.reason || svc.status);
  out.jobs = {};
  try {
    for (const plan of OMICS_JOBS) await omicsJob(svc, plan, project.id, recorded);
  } finally {
    p.terminateAll();
  }
}

/** One omics job of the service, its receipt checked and every output read back (digest and text) into out.jobs. */
async function omicsJob(svc, plan, projectId, recorded) {
  const handler = OMICS_HANDLERS.find((h) => h.kind === plan.kind);
  const t0 = performance.now();
  let job = await svc.submit(plan.kind, plan.params, { project_id: projectId });
  job = await waitFor(svc, job.id, 15 * 60_000);
  const ms = performance.now() - t0;
  const rec = {
    kind: plan.kind, params: plan.params, state: job?.state, ms: Math.round(ms), error: job?.error || null,
    stages: (job?.stages || []).map((x) => ({ id: x.id, status: x.status, seconds: x.seconds ?? null, worker: x.worker ?? null, reused: Boolean(x.reused), detail: x.detail || "" })),
    limitations: job?.limitations || [], outcome: job?.outcome || null, logs: (job?.logs || []).map((l) => l.text).slice(-20),
  };
  out.jobs[plan.key] = rec;
  if (!check(`${plan.kind} as a browser job`, job?.state === "succeeded", job?.error?.message || job?.state, ms)) return;
  check(`${plan.kind}: its stages`, job.stages.every((x) => x.status === "succeeded"), rec.stages.map((x) => `${x.id}:${x.status} ${x.seconds ?? "?"} s${x.worker ? `@${x.worker}` : ""}`).join(", "));
  const receipt = await svc.receipt(job.id);
  const doc = receipt?.doc || null;
  rec.receipt = doc;
  check(`${plan.kind}: receipt at its recorded digest`, receipt?.matches === true, receipt?.sha256 || "no receipt");
  check(`${plan.kind}: receipt says both digest readings agree (outputs_verified)`, doc?.assurance === "outputs_verified", String(doc?.assurance));
  check(`${plan.kind}: receipt says the browser verified the outputs (device evidence verified)`, doc?.device?.evidence === "verified", JSON.stringify(doc?.device?.verify_stages ?? doc?.device?.evidence));
  const runner = ["verify_run", "required_artefacts", "validator:run"];
  check(`${plan.kind}: the runner's checks passed (${runner.join(", ")})`, runner.every((c) => (doc?.checks || []).includes(c)), (doc?.checks || []).join(", "));
  check(`${plan.kind}: every output the runner requires is there`, JSON.stringify(doc?.expected) === JSON.stringify(handler.expected) && !(doc?.missing || []).length,
    `expected ${(doc?.expected || []).join(", ")}; missing ${(doc?.missing || []).join(", ") || "none"}`);
  // each input the receipt lists is a file of the project, at the digest the project recorded for it
  const inputs = Object.entries(doc?.inputs || {}).flatMap(([k, v]) => (v && typeof v.sha256 === "string" ? [[k, v]] : Object.entries(v || {}).map(([kk, vv]) => [`${k} ${kk}`, vv])));
  const wrong = inputs.filter(([, v]) => recorded.get(v?.source?.project_file)?.sha256 !== v?.sha256);
  check(`${plan.kind}: inputs recorded at the project files' digests`, inputs.length > 0 && !wrong.length,
    wrong.length ? `not the project's: ${wrong.map(([k]) => k).join(", ")}` : inputs.map(([k, v]) => `${k}: ${v.source?.name} ${String(v.sha256).slice(0, 12)}…`).join("; "));
  const versions = (rec.limitations.find((l) => /^Computed in this browser with Pyodide's numpy /.test(l)) || "").match(/numpy (\S+) and scipy (\S+);/);
  rec.packages = versions ? { numpy: versions[1], scipy: versions[2] } : null;
  check(`${plan.kind}: the limitations say what computed it`, Boolean(versions) && !/not reported/.test(versions[0]), versions ? versions[0] : "no line naming numpy and scipy");
  rec.memory = doc?.resources?.memory ?? null;
  rec.files = {};
  for (const a of job.artefacts || []) {
    const file = await svc.readFile(job.id, a.path).catch(() => null);
    if (!file) continue;
    const bytes = new Uint8Array(await file.arrayBuffer());
    const sha = await sha256Hex(bytes);
    rec.files[a.path.slice(1).join("/")] = { sha256: `sha256:${sha}`, recorded: a.sha256, bytes: bytes.byteLength, text: new TextDecoder().decode(bytes) };
  }
  const unread = (job.artefacts || []).filter((a) => !rec.files[a.path.slice(1).join("/")] || rec.files[a.path.slice(1).join("/")].sha256 !== `sha256:${String(a.sha256).replace(/^sha256:/, "")}`);
  check(`${plan.kind}: every output read back at its recorded digest`, (job.artefacts || []).length > 0 && !unread.length,
    unread.length ? `not read back as recorded: ${unread.map((a) => a.path.join("/")).join(", ")}` : `${Object.keys(rec.files).length} files`);
}

// ------------------------------------------------------------------------------------------------- ?mode=admet

/** The Pyodide packages the ADMET jobs and the probe import. */
const ADMET_PACKAGES = ["numpy", "scipy", "scikit-learn"];
const TEXT_FILE = /\.(tsv|md|html|json|svg|txt|csv)$/i;

/** A job's record as the page keeps it for science.mjs: its stages with their records and compute facts. */
function jobRecord(plan, job, ms) {
  return {
    kind: plan.kind, params: plan.params, id: job?.id ?? null, state: job?.state, ms: Math.round(ms), error: job?.error || null,
    stages: (job?.stages || []).map((x) => ({
      id: x.id, status: x.status, seconds: x.seconds ?? null, worker: x.worker ?? null, reused: Boolean(x.reused), detail: x.detail || "",
      record: x.record ?? null, compute: x.compute ?? null,
    })),
    limitations: job?.limitations || [], outcome: job?.outcome || null, result: job?.result ?? null, logs: (job?.logs || []).map((l) => l.text).slice(-30),
  };
}

/**
 * One ADMET job of the service: submitted, waited for, its receipt checked as omicsJob checks one, every artefact read
 * back (its digest; the text of a text file) → the record, or null when it did not succeed.
 */
async function admetJob(svc, label, plan, projectId, recorded, { timeoutMin = 60 } = {}) {
  const t0 = performance.now();
  let job;
  try {
    job = await svc.submit(plan.kind, plan.params, { project_id: projectId });
  } catch (err) {
    check(`${label}: ${plan.kind} submitted`, false, `${err?.type || "error"}: ${err?.message || err}${err?.need ? ` (need ${err.need})` : ""}`);
    return null;
  }
  job = await waitFor(svc, job.id, timeoutMin * 60_000);
  const ms = performance.now() - t0;
  const rec = jobRecord(plan, job, ms);
  if (!check(`${label}: ${plan.kind} as a browser job`, job?.state === "succeeded", job?.error?.message || job?.state, ms)) return rec;
  check(`${label}: its stages`, job.stages.every((x) => x.status === "succeeded"), rec.stages.map((x) => `${x.id}:${x.status}${x.reused ? "(reused)" : ""} ${x.seconds ?? "?"} s`).join(", ").slice(0, 600));
  const receipt = await svc.receipt(job.id);
  const doc = receipt?.doc || null;
  rec.receipt = doc;
  check(`${label}: receipt at its recorded digest`, receipt?.matches === true, receipt?.sha256 || "no receipt");
  check(`${label}: receipt says both digest readings agree (outputs_verified)`, doc?.assurance === "outputs_verified", String(doc?.assurance));
  check(`${label}: receipt says the browser verified the outputs (device evidence verified)`, doc?.device?.evidence === "verified", JSON.stringify(doc?.device?.verify_stages ?? doc?.device?.evidence));
  const inputs = Object.entries(doc?.inputs || {}).filter(([, v]) => v?.source?.project_file);
  const wrong = inputs.filter(([, v]) => recorded.get(v.source.project_file)?.sha256 !== v.sha256);
  check(`${label}: inputs recorded at the project files' digests`, inputs.length > 0 && !wrong.length,
    wrong.length ? `not the project's: ${wrong.map(([k]) => k).join(", ")}` : inputs.map(([k, v]) => `${k}: ${v.source?.name} ${String(v.sha256).slice(0, 12)}…`).join("; "));
  rec.memory = doc?.resources?.memory ?? null;
  rec.files = {};
  for (const a of job.artefacts || []) {
    const file = await svc.readFile(job.id, a.path).catch(() => null);
    if (!file) continue;
    const bytes = new Uint8Array(await file.arrayBuffer());
    const key = a.path[0] === "out" ? a.path.slice(1).join("/") : a.path.at(-1);
    rec.files[key] = { sha256: `sha256:${await sha256Hex(bytes)}`, recorded: a.sha256, bytes: bytes.byteLength, ...(TEXT_FILE.test(key) ? { text: new TextDecoder().decode(bytes) } : {}) };
  }
  const unread = (job.artefacts || []).filter((a) => {
    const f = rec.files[a.path[0] === "out" ? a.path.slice(1).join("/") : a.path.at(-1)];
    return !f || f.sha256 !== `sha256:${String(a.sha256).replace(/^sha256:/, "")}`;
  });
  check(`${label}: every output read back at its recorded digest`, (job.artefacts || []).length > 0 && !unread.length,
    unread.length ? `not read back as recorded: ${unread.map((a) => a.path.join("/")).join(", ")}` : `${Object.keys(rec.files).length} files`);
  rec.ok = true;
  return rec;
}

/** A file of a job's directory → its bytes, or null. */
async function jobBytes(svc, id, path) {
  const f = await svc.readFile(id, path).catch(() => null);
  return f ? new Uint8Array(await f.arrayBuffer()) : null;
}

/** Little-endian uint32 triples (arg, inter, union per query) → {arg, inter, union}. */
function triples(bytes) {
  const v = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const n = Math.floor(bytes.byteLength / 12);
  const out = { arg: [], inter: [], union: [] };
  for (let i = 0; i < n; i++) {
    out.arg.push(v.getUint32(12 * i, true));
    out.inter.push(v.getUint32(12 * i + 4, true));
    out.union.push(v.getUint32(12 * i + 8, true));
  }
  return out;
}

/**
 * What a pipeline.admet job left in its directory besides its outputs: the molecules admet_read read, the prepared set
 * (each row of prepared.json, and the SHA-256 of each prepared molecule's feature row and bits) and the similarity
 * files (similarity.json and each endpoint's triples).
 */
async function pipelineFiles(svc, rec) {
  const id = rec.id;
  const text = async (path) => { const b = await jobBytes(svc, id, path); return b ? new TextDecoder().decode(b) : null; };
  const molecules = JSON.parse((await text(["read", "molecules.json"])) || "null");
  const prepared = JSON.parse((await text(["prepared", "prepared.json"])) || "null");
  const features = await jobBytes(svc, id, ["prepared", "features.f32"]);
  const bits = await jobBytes(svc, id, ["prepared", "bits.u8"]);
  const rows = {};
  const notPrepared = {};
  let at = 0;
  for (const [i, r] of (prepared?.rows || []).entries()) {
    const name = molecules?.names?.[i] ?? String(i);
    if (!r.ok) { notPrepared[name] = { error: r.error, need: r.need ?? null, missing: r.missing ?? null }; continue; }
    rows[name] = {
      features_sha256: features ? await sha256Hex(features.subarray(at * N_FEATURES * 4, (at + 1) * N_FEATURES * 4)) : null,
      bits_sha256: bits ? await sha256Hex(bits.subarray(at * N_BITS_BYTES, (at + 1) * N_BITS_BYTES)) : null,
      canonical: r.canonical, descriptors: r.descriptors, atoms_with_h: r.atoms_with_h, alerts: r.alerts,
    };
    at += 1;
  }
  const simDoc = JSON.parse((await text(["similarity", "similarity.json"])) || "null");
  const similarity = { doc: simDoc, endpoints: {} };
  for (const e of Object.keys(simDoc?.endpoints || {})) {
    const b = await jobBytes(svc, id, ["similarity", `${e}.u32`]);
    if (b) similarity.endpoints[e] = { sha256: await sha256Hex(b), ...triples(b) };
  }
  return {
    molecules, engine: prepared?.engine ?? null, chemistry: { rows, not_prepared: notPrepared },
    names: (molecules?.names || []).filter((_, i) => prepared?.rows?.[i]?.ok), similarity,
  };
}

/** The counts a prepared set's rows give (pipelines/admet.js preparedCounts, which the job's chemistry stages record). */
function counted(rows) {
  const missing = {};
  let ok = 0, refused = 0;
  for (const r of rows) {
    if (r.ok) ok += 1;
    else if (r.refused) { refused += 1; const k = String(r.refused.missing || "a step"); missing[k] = (missing[k] || 0) + 1; }
  }
  return { n: rows.length, ok, refused, invalid: rows.length - ok - refused, missing };
}

const joined = (pieces, Type) => {
  const out = new Type(pieces.reduce((a, p) => a + p.length, 0));
  let at = 0;
  for (const p of pieces) { out.set(p, at); at += p.length; }
  return out;
};

/**
 * Each endpoint's training splits as admet_split wrote them in the admet.models job, prepared once more by the same
 * browser chemistry: the molecules it refused and could not read (for the native models to leave out the same), its
 * counts held to the job's chemistry stage records, and each test split kept as a prepared set for the probe
 * (tcmstudio/harness/tests/<endpoint>) → {endpoint: {train_val, test}}.
 */
async function rechem(svc, store, models, chemOpts, endpoints) {
  const chem = new ChemClient(chemOpts);
  const out = {};
  try {
    for (const e of endpoints) {
      const stage = models.stages.find((x) => x.id === `chemistry.${e}`);
      out[e] = {};
      for (const split of ["train_val", "test"]) {
        const doc = JSON.parse(new TextDecoder().decode((await jobBytes(svc, models.id, ["split", e, `${split}.json`])) || new Uint8Array()) || "null");
        const smiles = doc?.smiles || [];
        const t0 = performance.now();
        const prep = await chem.prepareAll(smiles, { alerts: false });
        const counts = counted(prep.rows);
        out[e][split] = {
          n: smiles.length, counts, ms: Math.round(performance.now() - t0),
          refused: prep.rows.flatMap((r, i) => (!r.ok && r.refused ? [[smiles[i], String(r.error || ""), String(r.refused.missing || "")]] : [])),
          unreadable: prep.rows.flatMap((r, i) => (!r.ok && !r.refused ? [[smiles[i], String(r.error || "")]] : [])),
          job_counts: stage?.record?.prepared?.[split] ?? null,
        };
        if (split === "test") {
          const files = preparedFiles({ smiles, rows: prep.rows, features: joined(prep.features, Float32Array), bits: joined(prep.bits, Uint8Array) });
          const dir = ["tcmstudio", "harness", "tests", e];
          await store.remove(dir, { recursive: true }).catch(() => false);
          await store.writeText([...dir, "prepared.json"], files.json);
          await store.writeBytes([...dir, "features.f32"], new Uint8Array(files.features));
          await store.writeBytes([...dir, "bits.u8"], new Uint8Array(files.bits));
          out[e][split].smiles = smiles;
        }
      }
      const r = out[e];
      const same = ["train_val", "test"].every((k) => JSON.stringify(r[k].counts) === JSON.stringify(r[k].job_counts));
      check(`${e}: the browser chemistry, run again on its splits, gives the job's own counts`, same,
        ["train_val", "test"].map((k) => `${k}: ${r[k].counts.ok} of ${r[k].n} prepared, ${r[k].counts.refused} refused, ${r[k].counts.invalid} not read${same ? "" : ` (the job recorded ${JSON.stringify(r[k].job_counts)})`}`).join("; "));
    }
  } finally {
    chem.terminate();
  }
  return out;
}

/**
 * The probe: a job worker booted with its debug op runs admet_native.py's probe on the browser's models (admet/models)
 * and its prepared sets (the pipeline job's, each endpoint's test split), so that its full-precision predictions and
 * its CPU domain search are computed by the same code as the native side's → probe()'s result.
 */
async function admetProbe(bootDoc, store, { cards, pipelineId, tests }) {
  const p = new ScienceWorkerPool({
    size: 1, workerUrl: new URL("/js/scientific/engines/science.worker.js", location.href).href,
    boot: bootDoc, siteUrl: new URL("/", location.href).href, indexUrl, debug: true,
  });
  const t0 = performance.now();
  try {
    const lease = await p.lease({ packages: ADMET_PACKAGES });
    try {
      const files = ["model_cards.json", ...Object.values(cards).flatMap((c) => [c.model_file, c.domain_file])];
      await lease.call("mount", { root: "/work/probe/models", dir: modelsDir(store), files: [...new Set(files)] });
      const sets = [];
      if (pipelineId) {
        await lease.call("mount", { root: "/work/probe/prepared", dir: [...jobDir(pipelineId), "prepared"], files: ["prepared.json", "features.f32", "bits.u8"] });
        await lease.call("mount", { root: "/work/probe/read", dir: [...jobDir(pipelineId), "read"], files: ["molecules.json"] });
        sets.push({ key: "molecules", prepared_dir: "/work/probe/prepared", molecules: "/work/probe/read/molecules.json", endpoints: Object.keys(cards) });
      }
      for (const [e, t] of Object.entries(tests || {})) {
        await lease.call("mount", { root: `/work/probe/tests/${e}`, dir: ["tcmstudio", "harness", "tests", e], files: ["prepared.json", "features.f32", "bits.u8"] });
        sets.push({ key: `test:${e}`, prepared_dir: `/work/probe/tests/${e}`, smiles: t.smiles, endpoints: [e] });
      }
      const source = await (await fetch("/test/runtime/admet_native.py", { cache: "no-cache" })).text();
      const text = await lease.call("python", { code: `${source}
import json as _json
result = _json.dumps(probe(_json.loads(${JSON.stringify(JSON.stringify({ models_dir: "/work/probe/models", sets }))})))` }, { timeoutMs: 60 * 60_000 });
      const doc = JSON.parse(text);
      const memory = await lease.call("memory", {}).catch(() => null);
      await lease.call("free", { paths: ["/work/probe"] }).catch(() => {});
      check("probe: the browser's models predicted its prepared sets in a job worker (admet_native.py exact)", Boolean(doc), `${Object.keys(doc || {}).length} sets`, performance.now() - t0);
      return { sets: doc, memory };
    } finally {
      lease.release();
    }
  } finally {
    p.terminateAll();
  }
}

async function admet() {
  const bootDoc = await boot();
  const plan = admetPlan({ real: q.get("real") === "1", endpoints: (q.get("endpoints") || "").split(",").filter(Boolean) });
  out.plan = plan;
  const pin = bootDoc.rdkit || null;
  if (!check("boot.json pins RDKit.js (version, url, sha256, bytes)", Boolean(pin?.sha256 && pin?.url && pin?.version), JSON.stringify(pin))) return;
  const rdkit = q.get("rdkit") ? { ...pin, url: new URL(q.get("rdkit"), location.href).href } : pin;
  out.rdkit = { version: rdkit.version, url: rdkit.url, sha256: rdkit.sha256, mirror: Boolean(q.get("rdkit")) };
  const { store, reason } = await OpfsStore.open();
  if (!check("private storage (OPFS)", Boolean(store), store ? "" : reason)) return;
  await store.remove(["tcmstudio"], { recursive: true }).catch(() => false);
  const projects = await openStore({ name: "tcmstudio-science-check" });
  const project = await projects.projects.create({ name: "science check: admet" });
  const listed = await (await fetch("/raw/index.json")).json();
  const t0 = performance.now();
  const recorded = new Map();
  for (const f of listed) {
    const blob = await (await fetch(`/raw/${encodeURIComponent(f.name)}`)).blob();
    const rec = await projects.files.add(project.id, new File([blob], f.name), { name: f.name });
    recorded.set(rec.id, rec);
  }
  out.project_files = [...recorded.values()].map((r) => ({ id: r.id, name: r.name, bytes: r.bytes, sha256: r.sha256 }));
  check("the archive and the molecules are a project's files", recorded.size === listed.length && listed.length >= 2,
    out.project_files.map((r) => `${r.name} (${r.bytes} bytes)`).join(", "), performance.now() - t0);

  const chemOpts = { Worker, url: new URL("/js/scientific/chem/chem.worker.js", location.href).href, wasm: { url: rdkit.url, sha256: rdkit.sha256 } };
  const common = { rdkit, ChemWorker: Worker, chemWorkerUrl: chemOpts.url, calibration: async () => null };
  const p = pool(bootDoc);
  const projectFiles = projectFilesResolver(projects.files);
  const svc = new BrowserJobService({ pool: p, store, projectFiles, handlers: createAdmetHandlers(common) });
  await svc.start();
  check("job service started", svc.status === "ready", svc.reason || svc.status);
  const res = {};
  out.admet = res;
  const minutes = plan.real ? 150 : 20;
  try {
    // the models, from the archive given as a project file
    const models = await admetJob(svc, "models", plan.models, project.id, recorded, { timeoutMin: minutes });
    res.models = models;
    if (!models?.ok) return;
    const cardsText = await store.readBytes([...modelsDir(store), "model_cards.json"]).then((b) => new TextDecoder().decode(b)).catch(() => null);
    const cards = JSON.parse(cardsText || "{}");
    models.cards = cards;
    models.domains = {};
    for (const [e, c] of Object.entries(cards)) {
      const b = await store.readBytes([...modelsDir(store), c.domain_file]).catch(() => null);
      models.domains[e] = b ? { sha256: await sha256Hex(b), bytes: b.byteLength } : null;
    }
    const endpoints = plan.models.params.endpoints || Object.keys(cards);
    check("the models' cards are in this browser's models", endpoints.every((e) => cards[e]), `${Object.keys(cards).join(", ")}`);
    res.rechem = await rechem(svc, store, models, chemOpts, endpoints);

    // the molecules
    const run = await admetJob(svc, "pipeline", plan.pipeline, project.id, recorded, { timeoutMin: minutes });
    res.pipeline = run;
    if (run?.ok) Object.assign(run, await pipelineFiles(svc, run));

    // the probe: full-precision predictions with the browser's models
    const tests = Object.fromEntries(Object.entries(res.rechem).map(([e, r]) => [e, { smiles: r.test.smiles }]));
    for (const r of Object.values(res.rechem)) delete r.test.smiles;
    res.probe = await admetProbe(bootDoc, store, { cards, pipelineId: run?.ok ? run.id : null, tests }).catch((err) => {
      check("probe: the browser's models predicted its prepared sets in a job worker (admet_native.py exact)", false, err?.stack || String(err));
      return null;
    });

    // once more with the domain search forced onto the GPU (a software adapter allowed): the harness-only switch
    const forced = new BrowserJobService({ pool: p, store, projectFiles, handlers: createAdmetHandlers({ ...common, debug: { forceSoftwareGpu: true } }) });
    await forced.start();
    const gpu = await admetJob(forced, "forced GPU", plan.pipeline, project.id, recorded, { timeoutMin: minutes });
    res.forced = gpu;
    if (gpu?.ok) Object.assign(gpu, await pipelineFiles(forced, gpu));
  } finally {
    p.terminateAll();
  }
}

(async () => {
  try {
    if (mode === "admet") await admet();
    else if (mode === "omics") await omics();
    else if (mode === "gqd") await gqd();
    else if (mode === "fixture") await fixture();
    else if (mode === "service") await service();
    else await selftest();
  } catch (err) {
    check("unexpected error", false, err?.stack || String(err));
  }
  $status.textContent = out.ok ? "All checks passed" : `${out.failures.length} check(s) failed`;
  globalThis.__result = out;
})();
