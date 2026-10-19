// Browser research jobs, checked in a real browser (science.mjs drives this page with Playwright). The result lands in
// window.__result.
//
// ?mode=selftest  one job worker studies the planted world (tcmstudio.browser_selftest) and compares its five tables
//                 with the answer native CPython committed
// ?mode=gqd       the raw files the harness serves at /raw/ are imported into this origin's private storage, then the
//                 browser job service runs research.snapshot and research.run (葛根芩连汤) exactly as Studio does; the
//                 exports come back with their digests for science.mjs to compare with the native run
// ?index=URL      where Pyodide is (default: boot.json's index_url)

import { DatasetWorkspace } from "../../js/scientific/data/datasets.js";
import { OpfsStore } from "../../js/scientific/data/opfs-store.js";
import { BrowserJobService } from "../../js/scientific/runtime/job-manager.js";
import { ScienceWorkerPool, recommendedPoolSize } from "../../js/scientific/runtime/worker-pool.js";
import { HANDLERS, RESEARCH_EXPORTS } from "../../js/scientific/pipelines/network-pharmacology.js";

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

(async () => {
  try {
    if (mode === "gqd") await gqd();
    else await selftest();
  } catch (err) {
    check("unexpected error", false, err?.stack || String(err));
  }
  $status.textContent = out.ok ? "All checks passed" : `${out.failures.length} check(s) failed`;
  globalThis.__result = out;
})();
