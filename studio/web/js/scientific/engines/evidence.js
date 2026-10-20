// The evidence a browser job leaves (studio/docs/BROWSER_JOBS.md §9): out/job_receipt.json, schema
// tcmstudio.job-receipt/1, with the runner's fields where they mean the same thing (studio/runner/src/tcmstudio/jobs.py
// JobService._write_receipt) and what only a browser job has: placement "browser", the Python runtime the job
// workers reported (Pyodide: CPython compiled to WebAssembly), the platform, the workers that ran which stages, and
// both readings of every output's SHA-256 — the worker's, taken as Python's files were copied out to the origin's
// private file system, and the page's, taken by reading the same files back from there.
//
// The rung a job reaches on the evidence ladder (ui/runlog.js) is decided from what happened, never from what was
// asked:
//   verified    a job worker computed, the job succeeded, a verify stage ran (not reused) and said it passed, and
//               for every output the page's digest equals the worker's
//   validated   a job worker computed, the job succeeded and the two digests agree for every output, but no verify
//               stage ran
//   dispatched  a job worker computed, but the job's checks did not pass (or it was stopped)
//   selected    the CPU was chosen and no job worker computed anything (whatever the page itself wrote)
//   accelerated verified, and a stage's GPU work was measured faster: a stage carries the compute facts of its kernel
//               calls (compute/science-scheduler.js; stage.compute, or stage.record.compute as the stage context's
//               record() keeps them), and every call of it that tried the GPU used a result from a physical adapter
//               that passed its known-answer test, was checked on the CPU, and that this device's calibration (its
//               device check) measured faster than the CPU kernel at that size. Each call's rung is decided again here
//               from its facts (capabilities.js evidenceLevel), not taken from them.
// Most stages run on the CPU only (Pyodide in the job workers, the page's kernels); a stage whose similarity search
// ran on WebGPU says so (device "webgpu"), and the receipt's compute section says with what adapter and calibration.
// Its assurance is "outputs_verified" only when it succeeded, both digests agree for every output and no output it was
// expected to write is missing.

import { EVIDENCE, evidenceLevel } from "../../compute/capabilities.js";

export const JOB_RECEIPT_SCHEMA = "tcmstudio.job-receipt/1";
export const JOB_RECEIPT_FILE = "job_receipt.json";
/** The rungs a browser job can reach, lowest first. */
export const JOB_EVIDENCE = Object.freeze(["selected", "dispatched", "validated", "verified", "accelerated"]);

const HEX64 = /^[0-9a-f]{64}$/;

/** A SHA-256 as 64 lower-case hex digits (a "sha256:" prefix dropped), or null when it is not one. */
export function bareSha256(value) {
  if (typeof value !== "string") return null;
  const hex = value.trim().toLowerCase().replace(/^sha256:/, "");
  return HEX64.test(hex) ? hex : null;
}

/** A path as one string: segments joined by "/". */
export function pathText(path) {
  return Array.isArray(path) ? path.join("/") : String(path ?? "");
}

/**
 * The two readings of every output compared → {names, worker_digests, page_digests, unrecorded, unread, mismatched}.
 * worker_digests: every output has the worker's SHA-256; page_digests: besides, the page read every one back and
 * found the same digest. Both are false when there are no outputs at all: nothing was hashed.
 */
export function compareDigests(artefacts = [], workerDigests = {}, pageDigests = {}) {
  const names = (Array.isArray(artefacts) ? artefacts : []).map((a) => String(a?.name ?? ""));
  const w = (n) => bareSha256(workerDigests?.[n]);
  const p = (n) => bareSha256(pageDigests?.[n]);
  const unrecorded = names.filter((n) => !w(n));
  const unread = names.filter((n) => !p(n));
  const mismatched = names.filter((n) => w(n) && p(n) && w(n) !== p(n));
  const worker = names.length > 0 && !unrecorded.length;
  return { names, worker_digests: worker, page_digests: worker && !unread.length && !mismatched.length, unrecorded, unread, mismatched };
}

/** The checks the digests passed, by name, as a receipt and job.outcome.checks list them. */
export function digestChecks(cmp) {
  return [cmp?.worker_digests ? "worker_digests" : null, cmp?.page_digests ? "page_digests" : null].filter(Boolean);
}

/**
 * The ids of the verify stages that ran and passed in this attempt: a stage that said it verified (verified: true,
 * which a handler returns only when its check passed) and succeeded without being reused from a checkpoint. A
 * stage's name proves nothing: one called "verify" that did not say so is not counted.
 */
export function verifyStages(stages = []) {
  return (Array.isArray(stages) ? stages : [])
    .filter((s) => s && s.status === "succeeded" && !s.reused && s.verified === true)
    .map((s) => String(s.id));
}

/** The workers that did something for the job: an operation sent (calls), or a stage that names it. A worker that
 * was only started for the job (its boot is not the job's work) is not one. */
function workersThatRan(workers = []) {
  return (Array.isArray(workers) ? workers : []).filter((w) => Number(w?.calls) > 0 || (Array.isArray(w?.stages) && w.stages.length > 0));
}

/** Whether a job worker computed anything for the job: a worker that ran an operation for it, or a stage that names one. */
export function workRan({ workers = [], stages = [] } = {}) {
  return workersThatRan(workers).length > 0 || (Array.isArray(stages) && stages.some((s) => s?.worker));
}

// ------------------------------------------------------------------------------------------------- compute facts

/** The compute facts a stage carries (compute/science-scheduler.js runTanimoto's, one per call) → a list. */
export function stageCompute(stage) {
  const c = stage?.compute ?? stage?.record?.compute ?? null;
  const list = Array.isArray(c) ? c : c && typeof c === "object" ? [c] : [];
  return list.filter((f) => f && typeof f === "object" && typeof f.backend === "string");
}

/** One call's GPU rung, decided from its facts: null when the GPU was not tried. */
export function callGpuRung(f) {
  const g = f?.gpu;
  if (!g || typeof g !== "object") return null;
  const used = f.backend === "webgpu";
  return evidenceLevel({
    selected: true, reported: Boolean(g.adapter), dispatched: g.dispatched === true, validated: used && g.validated === true, verified: g.verified === true,
    accelerated: used && f.accelerated === true && typeof f.calibration_id === "string" && Boolean(f.calibration_id), adapter_kind: g.adapter?.kind ?? null,
  });
}

/**
 * What the stages' kernel calls ran on → {stages: {id: {calls, backend, backends, gpu, calibration_ids, accelerated,
 * evidence, basis}}, accelerated, gpu_stages, tried_stages}, or null when no stage carries compute facts. A stage is
 * accelerated when at least one of its calls used a GPU result and every call that tried the GPU reached
 * "accelerated"; its evidence is the lowest GPU rung among those calls. A reused stage computed nothing this time.
 */
export function computeEvidence(stages = []) {
  const out = {};
  for (const s of Array.isArray(stages) ? stages : []) {
    const calls = stageCompute(s);
    if (!calls.length || s.reused) continue;
    const backends = {};
    for (const f of calls) backends[f.backend] = (backends[f.backend] || 0) + 1;
    const tried = calls.filter((f) => f.gpu && typeof f.gpu === "object");
    const used = calls.filter((f) => f.backend === "webgpu");
    const rungs = tried.map(callGpuRung);
    const lowest = rungs.length ? rungs.reduce((a, b) => (EVIDENCE.indexOf(b) < EVIDENCE.indexOf(a) ? b : a)) : null;
    const accelerated = used.length ? rungs.every((r) => r === "accelerated") : tried.length ? false : null;
    const first = tried.find((f) => f.gpu.adapter)?.gpu.adapter ?? null;
    const sum = (k) => { const xs = tried.map((f) => f.gpu[k]).filter(Number.isFinite); return xs.length === tried.length && xs.length ? Math.round(xs.reduce((a, b) => a + b, 0) * 1000) / 1000 : null; };
    const rejected = tried.filter((f) => f.backend !== "webgpu");
    out[String(s.id)] = {
      calls: calls.length,
      backend: Object.keys(backends).length === 1 ? Object.keys(backends)[0] : "mixed",
      backends,
      gpu: tried.length ? {
        adapter: first ? { kind: first.kind ?? null, vendor: first.vendor ?? "", architecture: first.architecture ?? "", description: first.description ?? "" } : null,
        tried: tried.length, used: used.length, dispatched: tried.filter((f) => f.gpu.dispatched === true).length,
        validated: used.filter((f) => f.gpu.validated === true).length, verified: tried.every((f) => f.gpu.verified === true),
        not_used: rejected.map((f) => String(f.gpu.rejected || f.gpu.error || "not used")).slice(0, 5),
        kernel_ms: sum("kernel_ms"), total_ms: sum("total_ms"),
      } : null,
      calibration_ids: [...new Set(calls.map((f) => f.calibration_id).filter((x) => typeof x === "string" && x))],
      accelerated,
      evidence: lowest,
      basis: accelerated === true ? used[0].basis : used.length ? `not every GPU call reached "accelerated" (lowest: ${lowest})`
        : tried.length ? "the GPU was tried and its results not used" : "the GPU computed nothing for this stage",
    };
  }
  const ids = Object.keys(out);
  if (!ids.length) return null;
  return {
    stages: out,
    accelerated: ids.some((id) => out[id].accelerated === true) && !ids.some((id) => out[id].accelerated === false),
    gpu_stages: ids.filter((id) => out[id].gpu?.used > 0),
    tried_stages: ids.filter((id) => out[id].gpu?.tried > 0),
  };
}

/**
 * The rung and the assurance → {evidence, assurance, verify_stages, digests, compute}. state: the job's final state;
 * missing: the outputs it was expected to write and did not.
 */
export function jobEvidence({ state, stages = [], artefacts = [], workerDigests = {}, pageDigests = {}, missing = [], workers = [] } = {}) {
  const digests = compareDigests(artefacts, workerDigests, pageDigests);
  const verify = verifyStages(stages);
  const compute = computeEvidence(stages);
  const ok = state === "succeeded";
  // every rung above "selected" is about the device: none is reached when no job worker computed anything
  let evidence = "selected";
  if (workRan({ workers, stages })) {
    if (ok && digests.page_digests && verify.length) evidence = compute?.accelerated ? "accelerated" : "verified";
    else if (ok && digests.page_digests) evidence = "validated";
    else evidence = "dispatched";
  }
  const assurance = ok && digests.page_digests && !(missing || []).length ? "outputs_verified" : "none";
  return { evidence, assurance, verify_stages: verify, digests, compute };
}

/** The Python runtime the job workers reported ("pyodide-314.0.7 / CPython 3.14.2"), or null when none started. */
export function runtimeOf(infos = []) {
  const seen = [...new Set((infos || []).map((i) => i?.runtime).filter((r) => typeof r === "string" && r))];
  return seen.length ? seen.join("; ") : null;
}

/** The platform Python reported in the workers ("emscripten/wasm32"), or null when none said. */
export function platformOf(infos = []) {
  const seen = [...new Set((infos || []).filter((i) => i?.platform).map((i) => (i.machine ? `${i.platform}/${i.machine}` : String(i.platform))))];
  return seen.length ? seen.join("; ") : null;
}

const round3 = (x) => Math.round(x * 1000) / 1000;

function seconds(from, to) {
  const a = Date.parse(from || "");
  const b = Date.parse(to || "");
  return Number.isFinite(a) && Number.isFinite(b) ? round3(Math.max(0, b - a) / 1000) : null;
}

/**
 * The job receipt document (schema tcmstudio.job-receipt/1) → an object to write as out/job_receipt.json.
 *
 * job: the job record in its final state; artefacts: [{name, path, bytes, sha256}] (default job.artefacts);
 * workerDigests / pageDigests: {name: sha256 | null}; workers: [{id, stages, busy_ms, calls, boot_ms}] the workers the
 * job leased; runtime: runtimeOf(); platform: {python, user_agent, cross_origin_isolated, …}; resources: {wall_s,
 * busy_s, memory: {wasm_peak_bytes, measured_by}}; checks: the checks that passed (the handler's and the service's);
 * problems, limitations: lists of strings; stages: job.stages; paramsSha256: SHA-256 of the canonical JSON of the
 * parameters (the bytes receipt.input_sha256 is taken over; the runner's params_sha256 hashes Python's default
 * json.dumps spacing, so the two are not compared); expected / missing: output names.
 */
export function buildJobReceipt({
  job, artefacts, workerDigests = {}, pageDigests = {}, workers = [], runtime = null, platform = null, resources = null,
  checks = [], problems = [], limitations = [], stages, paramsSha256 = null, expected = [], missing = [],
} = {}) {
  const j = job || {};
  const arts = Array.isArray(artefacts) ? artefacts : Array.isArray(j.artefacts) ? j.artefacts : [];
  const stageList = Array.isArray(stages) ? stages : Array.isArray(j.stages) ? j.stages : [];
  const state = j.state || null;
  const ev = jobEvidence({ state, stages: stageList, artefacts: arts, workerDigests, pageDigests, missing, workers });
  const ran = workRan({ workers, stages: stageList });
  const n = workersThatRan(workers).length || new Set(stageList.map((s) => s?.worker).filter(Boolean)).size;
  const passed = state === "succeeded" ? [...new Set([...(checks || []).map((c) => (typeof c === "string" ? c : JSON.stringify(c))), ...digestChecks(ev.digests)])] : [];
  const compute = ev.compute;
  // where a stage computed: the GPU for a stage whose kernel calls used a GPU result, else the CPU
  const stageDevice = (s) => (s?.reused ? null : compute?.stages[s?.id]?.gpu?.used > 0 ? "webgpu" : s?.worker || compute?.stages[s?.id] ? "cpu" : null);
  const deviceStages = {};
  for (const s of stageList) if (s?.status === "succeeded" && stageDevice(s)) deviceStages[s.id] = stageDevice(s);
  let gpuNote = "nothing ran on a GPU";
  if (compute?.tried_stages.length) {
    const used = compute.gpu_stages, unused = compute.tried_stages.filter((id) => !used.includes(id));
    const a = compute.stages[used[0] ?? unused[0]].gpu.adapter;
    const name = a ? `${[a.vendor, a.architecture, a.description].filter(Boolean).join(" ") || "an adapter with no name"} (${a.kind})` : "an adapter";
    gpuNote = [
      used.length ? `${used.join(", ")} computed on the WebGPU adapter ${name}, each result checked on the CPU` : null,
      unused.length ? `the GPU was tried for ${unused.join(", ")} and its results not used` : null,
    ].filter(Boolean).join("; ");
  }
  return {
    schema: JOB_RECEIPT_SCHEMA,
    job_id: j.id ?? null,
    kind: j.kind ?? null,
    state,
    project_id: j.project_id ?? null,
    submission_id: j.submission_id ?? null,
    params_sha256: paramsSha256,
    inputs: j.inputs && typeof j.inputs === "object" ? j.inputs : {},
    engine: null,
    placement: "browser",
    runtime,
    platform,
    workers: (workers || []).map((w) => ({
      id: w.id, stages: [...(w.stages || [])], busy_ms: Math.round(w.busy_ms || 0), calls: w.calls ?? null, boot_ms: w.boot_ms ?? null,
    })),
    device: {
      assigned: "cpu",
      used: ran ? "cpu" : null,
      evidence: ev.evidence,
      note: ran
        ? `computed on this device's CPU: CPython compiled to WebAssembly (Pyodide) in ${n} dedicated Web Worker${n === 1 ? "" : "s"}, one thread each; ${gpuNote}`
        : "assigned the CPU; no job worker computed anything for this job",
      stages: Object.keys(deviceStages).length ? deviceStages : null,
      verify_stages: ev.verify_stages,
    },
    started_at: j.started_at ?? null,
    finished_at: j.finished_at ?? null,
    duration_s: seconds(j.started_at, j.finished_at),
    artefacts: arts.map((a) => ({ name: a.name, path: pathText(a.path), bytes: a.bytes ?? null, sha256: bareSha256(a.sha256) })),
    digests: {
      worker: Object.fromEntries(arts.map((a) => [a.name, bareSha256(workerDigests?.[a.name])])),
      page: Object.fromEntries(arts.map((a) => [a.name, bareSha256(pageDigests?.[a.name])])),
      unrecorded: ev.digests.unrecorded,
      unread: ev.digests.unread,
      mismatched: ev.digests.mismatched,
    },
    expected: [...(expected || [])],
    missing: [...(missing || [])],
    checks: passed,
    problems: [...(problems || [])].map(String),
    error: j.error?.message ?? null,
    stages: stageList.map((s) => ({
      name: s.id, status: s.status, seconds: s.seconds ?? null, device: stageDevice(s), worker: s.worker ?? null,
      reused: Boolean(s.reused), verified: s.verified === true,
    })),
    // per stage that carries compute facts: its kernel calls' backends, the GPU's facts and the calibration they cite
    compute,
    complete: typeof j.outcome?.complete === "boolean" ? j.outcome.complete : null,
    limitations: [...(limitations || [])].map(String),
    resources,
    assurance: ev.assurance,
  };
}
