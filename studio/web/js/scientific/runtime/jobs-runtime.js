// The browser's job service as a runtime the router can call (CONTRACTS §6's call(tool, args, ctx)): a job call
// placed in the browser comes here instead of to the Pyodide tool worker, is submitted to the BrowserJobService
// (./job-manager.js), and returns a "job_submitted" envelope at once; job_status for a browser job (an id starting
// "bj-") is answered from the job's record. The model sees the same shapes a runner job gives it.

import { glossary, t } from "../../core/i18n.js";
import { baseReceipt } from "../../core/receipt.js";
import { canonicalJson, isPlainObject, sha256Hex } from "../../core/util.js";
import { envelopeFor } from "./job-manager.js";

/** A browser job's id (the runner's never start so). */
export const BROWSER_JOB_ID = /^bj-[0-9a-f]{8,}$/;

const ENDED = ["succeeded", "failed", "cancelled", "interrupted"];

/** The job kind and parameters a call stands for, or null when it is not a job this service runs. */
export function jobCall(tool, args = {}) {
  const a = isPlainObject(args) ? args : {};
  if (tool === "network_pharmacology_run") {
    const formula = String(a.formula || "").trim();
    const params = isPlainObject(a.parameters) ? a.parameters : {};
    // as tcmstudio.core_tools maps it for the runner: the same question, the same parameters
    const job = { question: `${formula}的实测靶点集中在哪些通路？`, formula };
    if (a.disease) job.disease = a.disease;
    for (const key of ["background", "hits", "permutations", "activity", "optional"]) if (key in params) job[key] = params[key];
    return { kind: "research.run", params: job };
  }
  const id = tool === "call_tool" ? String(a.tool || "") : String(tool || "");
  const params = tool === "call_tool" ? (isPlainObject(a.arguments) ? a.arguments : {}) : a;
  if (id.startsWith("job.")) return { kind: id.slice(4), params };
  return null;
}

/** Is this a job_status call about a browser job? → its id, or null. */
export function browserJobStatus(tool, args = {}) {
  const a = isPlainObject(args) ? args : {};
  const inner = tool === "call_tool" && (a.tool === "system.job_status" || a.tool === "job_status") ? (isPlainObject(a.arguments) ? a.arguments : {}) : a;
  const named = tool === "job_status" || tool === "system.job_status" || (tool === "call_tool" && ["system.job_status", "job_status"].includes(a.tool));
  const id = named ? String(inner.job_id || "") : "";
  return BROWSER_JOB_ID.test(id) ? { id, wait_s: Math.max(0, Math.min(60, Number(inner.wait_s) || 0)) } : null;
}

export class BrowserJobsRuntime {
  /** service: a BrowserJobService (its kinds() are what this runtime runs). */
  constructor({ service }) {
    this.kind = "jobs";
    this.service = service;
  }

  get status() {
    return this.service?.status || "offline";
  }

  onStatus(fn) {
    return this.service?.onStatus ? this.service.onStatus(fn) : () => {};
  }

  /** The kinds this browser runs (catalog BROWSER_JOB_KINDS, as far as this page's handlers go). */
  kinds() {
    return (this.service?.kinds?.() || []).map((k) => k.kind);
  }

  runs(kind) {
    return this.kinds().includes(kind);
  }

  async call(tool, args = {}, ctx = {}) {
    const input_sha256 = await sha256Hex(canonicalJson(isPlainObject(args) ? args : {})).catch(() => null);
    const status = browserJobStatus(tool, args);
    if (status) return this.#status(tool, status, input_sha256);
    const call = jobCall(tool, args);
    if (!call || !this.runs(call.kind)) {
      return failed(tool, input_sha256, "unavailable", `${call ? call.kind : tool} is not a job this browser runs`, "Connect the local runner: it runs every job kind.");
    }
    try {
      const job = await this.service.submit(call.kind, call.params, { project_id: ctx.project_id || null, submission_id: ctx.submission_id || null });
      return envelopeFor(job, { tool, input_sha256 });
    } catch (err) {
      // a refusal before anything was queued (no private storage, no snapshots, bad parameters): said with its need
      const type = err?.type === "bad_arguments" ? "bad_arguments" : "unavailable";
      const env = failed(tool, input_sha256, type, err?.message || String(err), err?.hint || "");
      if (err?.need) env.error.requires = [{ need: err.need, detail: err.message || String(err), remedy: err.hint || "" }];
      if (err?.need === "dataset") env.error.reason = "needs_dataset";
      else if (type === "unavailable") env.error.reason = "needs_job_service";
      return env;
    }
  }

  async #status(tool, { id, wait_s }, input_sha256) {
    let job = await this.service.get(id);
    if (job && wait_s > 0 && !ENDED.includes(job.state)) job = await this.#changed(id, job, wait_s * 1000);
    if (!job) return failed(tool, input_sha256, "not_found", `no job '${id}' in this browser`, "");
    const succeeded = job.state === "succeeded";
    const frac = job.progress?.fraction;
    const tail = Number.isFinite(frac) ? `, ${Math.round(frac * 100)}% done` : "";
    const files = succeeded ? `, ${(job.artefacts || []).length} output file(s)` : "";
    const env = {
      ok: true, tool, via: "system.job_status", status: "succeeded", duration_ms: 0,
      summary: t("ui.tool.job_state", { state: glossary.verdictState(job.state, undefined, "job") }),
      text: `Job ${job.id} (in this browser): ${job.state}${tail}${files}${succeeded ? "" : " (no result yet)"}${job.error?.message ? `: ${job.error.message}` : ""}`,
      result: job, citations: [],
      governance: { kind: "system", released: null, artifact: null, verdict: null, claims: [], evidence: [], refusals: [], labels: [], licences: [], limitations: succeeded ? [] : ["Pending: until the job has succeeded it has no result."], outputs: [] },
      receipt: baseReceipt({ where: "browser", runtime: "studio-jobs/1", input_sha256, kind: "system", outcome: "succeeded" }),
      job: { id: job.id, kind: job.kind, state: job.state, where: "browser" }, approval: null, error: null,
    };
    return env;
  }

  /** The job once it changes (or the time is up). */
  #changed(id, job, ms) {
    return new Promise((resolve) => {
      let done = false;
      const finish = async () => {
        if (done) return;
        done = true;
        off?.();
        clearTimeout(timer);
        resolve((await this.service.get(id)) || job);
      };
      const off = this.service.events?.(id, (ev) => { if (ev?.type === "state" || ev?.type === "done") finish(); });
      const timer = setTimeout(finish, ms);
    });
  }
}

function failed(tool, input_sha256, type, message, hint) {
  return {
    ok: false, tool, via: tool, status: "failed", duration_ms: 0,
    summary: message, text: `Not run: ${message}${hint ? ` ${hint}` : ""} Say what it would need; do not substitute an approximation.`,
    result: null, citations: [],
    governance: { kind: "job", released: null, artifact: null, verdict: null, claims: [], evidence: [], refusals: [], labels: [], licences: [], limitations: [], outputs: [] },
    receipt: baseReceipt({ where: "browser", runtime: "studio-jobs/1", decided_by: "browser-runtime", input_sha256 }),
    job: null, approval: null, error: { type, message, hint },
  };
}
