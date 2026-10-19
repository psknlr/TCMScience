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
//                 parameters), and both jobs must write receipts that match their recorded digests
// ?mode=fixture   the snapshot build on raw files mounted from private storage: the parser tests' fixture files
//                 (served at /raw/) are written into OPFS, mounted read-only in a job worker, and built with their own
//                 checksums in place of the reviewed ones (fixture_specs.py); science.mjs compares the snapshot ids with
//                 the same build run natively
// ?index=URL      where Pyodide is (default: boot.json's index_url)

import { DatasetWorkspace } from "../../js/scientific/data/datasets.js";
import { OpfsStore } from "../../js/scientific/data/opfs-store.js";
import { BrowserJobService } from "../../js/scientific/runtime/job-manager.js";
import { ScienceWorkerPool, recommendedPoolSize } from "../../js/scientific/runtime/worker-pool.js";
import { HANDLERS, RESEARCH_EXPORTS, RESEARCH_PATHS, SNAPSHOTS_BUILT_SCHEMA } from "../../js/scientific/pipelines/network-pharmacology.js";
import { browserSelfTest } from "../../js/scientific/pipelines/selftest.js";

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
    const text = /\.(json|md)$/.test(name) || name === "enrichment.tsv" ? new TextDecoder().decode(bytes) : undefined;
    out.files[name] = { sha256: sha, bytes: bytes.byteLength, ...(text !== undefined && bytes.byteLength < 4e6 ? { text } : {}) };
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
  for (const name of ["compounds.tsv", "targets.tsv", "disease_targets.tsv", "enrichment.tsv", "network.tsv"]) {
    const a = (job.artefacts || []).find((x) => x.name === name);
    check(`${name} is the committed answer`, a && `sha256:${a.sha256}` === out.expected?.[name], `${a?.sha256 || "missing"} vs ${out.expected?.[name]}`);
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
  for (const f of listed) await store.writeBytes([...RESEARCH_PATHS.raw, f.name], new Uint8Array(await (await fetch(`/raw/${encodeURIComponent(f.name)}`)).arrayBuffer()));
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
    check("raw files stay read-only", true);
  } finally {
    w.terminate();
  }
}

(async () => {
  try {
    if (mode === "gqd") await gqd();
    else if (mode === "fixture") await fixture();
    else if (mode === "service") await service();
    else await selftest();
  } catch (err) {
    check("unexpected error", false, err?.stack || String(err));
  }
  $status.textContent = out.ok ? "All checks passed" : `${out.failures.length} check(s) failed`;
  globalThis.__result = out;
})();
