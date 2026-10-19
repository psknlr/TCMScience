// What ran here: the catalog's 「已成功执行 Executed successfully」 field and the compute panel's evidence column.
//
// The run index is per browser: a small record in localStorage (key "tcmstudio.runlog", versioned), never in
// IndexedDB (no schema change), and never sent anywhere. It counts an envelope only when a runtime ran the call and it
// succeeded: status "succeeded", no receipt.decided_by (a page-made envelope never ran), and a result outcome of
// succeeded or validated (core/governance.js resultOf, so a legacy envelope is read conservatively). A returned job id
// is not a run: job_submitted, and a job_status poll of a queued or running job, never count; a job counts once, by its
// id, when the runner reports it succeeded (collected, its output hashes checked). Refused, declined, failed,
// cancelled and interrupted calls never count. Each entry keeps how many runs, and where and with which compute
// backend the last one ran. Every read and write is wrapped: storage may be missing or full, and nothing here may
// break a turn.
//
// The label says what it is: runs seen from this browser (calls this page made, and runner jobs it followed to their
// end), not every run the runner did.
//
// The session's compute evidence (app.state.computeEvidence) is what this page's own calls showed, on the ladder of
// compute/capabilities.js: how many browser calls each backend (pyodide, wasm-simd, js, webgpu) computed, the highest
// rung the WebGPU device reached in a receipt (dispatched, validated, verified, accelerated — the last only from a
// measured comparison on a physical adapter, never from an estimate), and the devices the runner assigned (selected;
// a job never reports more).

import { resultOf } from "../core/governance.js";

export const RUNLOG_KEY = "tcmstudio.runlog";
/** The stored record's version: a record of any other version is set aside and counting starts again. */
export const RUNLOG_VERSION = 1;
const MAX_JOB_IDS = 50;
const EVIDENCE = ["selected", "reported", "dispatched", "validated", "verified", "accelerated"];
const BACKENDS = ["pyodide", "wasm-simd", "js", "webgpu"];

const area = (storage) => storage ?? globalThis.localStorage;

/** The stored index {v, entries: {id: {n, last: {at, where, kernel}, jobs?}}}; an empty one when absent or of another version. */
export function readRunlog(storage) {
  try {
    const raw = area(storage)?.getItem(RUNLOG_KEY);
    const doc = raw ? JSON.parse(raw) : null;
    if (doc && doc.v === RUNLOG_VERSION && doc.entries && typeof doc.entries === "object") return doc;
  } catch { /* unreadable: start again */ }
  return { v: RUNLOG_VERSION, entries: {} };
}

function writeRunlog(doc, storage) {
  try { area(storage)?.setItem(RUNLOG_KEY, JSON.stringify(doc)); return true; } catch { return false; }
}

/** Forget every run (Settings → General → clear local data does the same). */
export function clearRunlog(storage) {
  try { area(storage)?.removeItem(RUNLOG_KEY); } catch { /* unavailable */ }
}

/** Did a runtime run this call, and did it succeed? (A page-made envelope, a job id, a pending job: no.) */
export function countsAsRun(envelope) {
  if (!envelope || typeof envelope !== "object" || envelope.status !== "succeeded") return false;
  const receipt = envelope.receipt && typeof envelope.receipt === "object" ? envelope.receipt : {};
  if (receipt.decided_by) return false;
  // the page's own answers (catalog_search, capabilities_status) are not runs of a catalog entry
  if (receipt.local === true) return false;
  const outcome = resultOf(envelope).outcome;
  return outcome === "succeeded" || outcome === "validated";
}

/** The catalog ids a counted envelope stands for: the entry it ran (via), and a job's own kind once it succeeded. */
export function runIdsOf(envelope) {
  const out = [];
  const via = typeof envelope?.via === "string" && envelope.via ? envelope.via : typeof envelope?.tool === "string" ? envelope.tool : "";
  if (via) out.push({ id: via });
  const job = envelope?.job && typeof envelope.job === "object" ? envelope.job : null;
  if (job?.state === "succeeded" && job.kind && job.id) out.push({ id: `job.${job.kind}`, job: String(job.id) });
  return out;
}

/** Record one envelope (when countsAsRun). → the ids counted ([] when none). */
export function recordRun(envelope, { at = Date.now(), storage } = {}) {
  try {
    if (!countsAsRun(envelope)) return [];
    const r = envelope.receipt || {};
    const last = {
      at, where: r.where || null,
      kernel: r.execution?.kernel || r.compute?.backend || (r.execution?.runtime === "pyodide" ? "pyodide" : null),
    };
    return bump(runIdsOf(envelope), last, storage);
  } catch { return []; }
}

/** Record a runner job the page saw succeed (collected, output hashes checked): job.<kind>, once per job id. */
export function recordJob(job, { at = Date.now(), storage } = {}) {
  try {
    if (!job || job.state !== "succeeded" || !job.kind || !job.id) return [];
    return bump([{ id: `job.${job.kind}`, job: String(job.id) }], { at, where: "runner", kernel: null }, storage);
  } catch { return []; }
}

function bump(ids, last, storage) {
  const doc = readRunlog(storage);
  const counted = [];
  for (const { id, job } of ids) {
    const e = doc.entries[id] && typeof doc.entries[id] === "object" ? doc.entries[id] : { n: 0 };
    if (job) {
      const seen = Array.isArray(e.jobs) ? e.jobs : [];
      if (seen.includes(job)) continue;
      e.jobs = [...seen, job].slice(-MAX_JOB_IDS);
    }
    e.n = (Number(e.n) || 0) + 1;
    e.last = last;
    doc.entries[id] = e;
    counted.push(id);
  }
  if (counted.length) writeRunlog(doc, storage);
  return counted;
}

/** One entry's record: {n, last} or null. */
export function runOf(id, log = readRunlog()) {
  const e = log?.entries?.[id];
  return e && Number(e.n) > 0 ? { n: Number(e.n), last: e.last || null } : null;
}

// ------------------------------------------------------------------------------------------------- session evidence

/**
 * Did this receipt's GPU call compare its time with a CPU kernel's (compute.gpu, compute/accelerator.js settle)? Only
 * then is "faster" or "not faster" a measurement. accelerator.js keeps cpu_baseline_ms whenever a CPU baseline exists,
 * also when it made no comparison (a software or unidentified adapter, a known-answer test not passed, a step not
 * timed: its basis says which), so the baseline alone is not a measurement. A comparison needs a physical adapter
 * whose known-answer test passed, counts Python used (validated), both times, and the basis of the measured branch
 * (or accelerated, which only that branch sets).
 */
export function gpuMeasured(g) {
  if (!g || typeof g !== "object") return false;
  if (g.adapter?.kind !== "physical" || g.validated !== true || g.verified !== true) return false;
  if (!Number.isFinite(g.gpu_ms) || !Number.isFinite(g.cpu_baseline_ms)) return false;
  // receipts written before `measured` existed say so only in the basis sentence
  return g.measured === true || g.accelerated === true || /^measured on this device\b/.test(String(g.basis || ""));
}

/** An empty session record. */
export function emptyEvidence() {
  return { calls: Object.fromEntries(BACKENDS.map((b) => [b, 0])), webgpu: null, runner: { calls: 0, devices: {} } };
}

const higher = (a, b) => (EVIDENCE.indexOf(b) > EVIDENCE.indexOf(a) ? b : a);

/**
 * Fold one envelope into the session's evidence (in place; returns it). Only envelopes a runtime answered count. A
 * browser call is credited to its compute backend (receipt.compute.backend; pyodide when no kernel took part). The
 * WebGPU rung is the receipt's own (execution.device.evidence when the GPU's counts were used, else what
 * compute.gpu shows: a kernel dispatched and then rejected stays "dispatched"); accelerated (true or false) is kept
 * only when the receipt compared it with a CPU kernel (gpuMeasured), and the accelerated rung only from that.
 */
export function noteEvidence(evidence, envelope) {
  const ev = evidence && typeof evidence === "object" ? evidence : emptyEvidence();
  try {
    const r = envelope?.receipt;
    if (!r || typeof r !== "object" || r.decided_by || r.local === true) return ev;
    if (r.where === "browser" && envelope.status === "succeeded") {
      const c = r.compute && typeof r.compute === "object" ? r.compute : null;
      const backend = BACKENDS.includes(c?.backend) ? c.backend : "pyodide";
      ev.calls[backend] = (ev.calls[backend] || 0) + 1;
    }
    const g = r.compute?.gpu;
    if (r.where === "browser" && g && typeof g === "object") {
      const w = ev.webgpu || { evidence: null, adapter_kind: null, accelerated: null, dispatched: 0, validated: 0 };
      let rung = null;
      if (r.compute.backend === "webgpu") rung = EVIDENCE.includes(r.execution?.device?.evidence) ? r.execution.device.evidence : EVIDENCE.includes(g.evidence) ? g.evidence : "validated";
      else if (g.dispatched) rung = "dispatched";
      if (rung === "accelerated" && !(gpuMeasured(g) && g.accelerated === true)) rung = "verified";
      if (rung) w.evidence = w.evidence ? higher(w.evidence, rung) : rung;
      if (g.adapter?.kind) w.adapter_kind = g.adapter.kind;
      if (g.dispatched) w.dispatched += 1;
      if (r.compute.backend === "webgpu") w.validated += 1;
      // compared on this device: true when faster, false when compared and not faster; never set without a comparison
      if (r.compute.backend === "webgpu" && gpuMeasured(g)) w.accelerated = w.accelerated === true || g.accelerated === true;
      ev.webgpu = w;
    }
    if (r.where === "runner" && envelope.status === "succeeded") {
      ev.runner.calls += 1;
      const d = r.execution?.device;
      const assigned = d?.assigned || (typeof r.device === "string" ? r.device : null);
      if (assigned) {
        const rung = EVIDENCE.includes(d?.evidence) ? d.evidence : "selected";
        ev.runner.devices[assigned] = ev.runner.devices[assigned] ? higher(ev.runner.devices[assigned], rung) : rung;
      }
    }
  } catch { /* evidence is never the turn's concern */ }
  return ev;
}
