// The browser's job service as a runtime the router can call (CONTRACTS §6's call(tool, args, ctx)): a job call
// placed in the browser comes here instead of to the Pyodide tool worker, is submitted to the BrowserJobService
// (./job-manager.js), and returns a "job_submitted" envelope at once; job_status for a browser job (an id starting
// "bj-") is answered from the job's record. The model sees the same shapes a runner job gives it.

import { glossary, t } from "../../core/i18n.js";
import { baseReceipt } from "../../core/receipt.js";
import { canonicalJson, isPlainObject, sha256Hex } from "../../core/util.js";
import { DatasetWorkspace } from "../data/datasets.js";
import { OpfsStore } from "../data/opfs-store.js";
import { HANDLERS } from "../pipelines/network-pharmacology.js";
import { SELFTEST_KIND, browserSelfTest } from "../pipelines/selftest.js";
import { BrowserJobService, envelopeFor } from "./job-manager.js";
import { ScienceWorkerPool, recommendedPoolSize } from "./worker-pool.js";

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
    /** Whether research snapshots are built in this browser (true/false), null until the page has looked (ui/app.js). */
    this.snapshots = null;
  }

  get status() {
    return this.service?.status || "offline";
  }

  onStatus(fn) {
    return this.service?.onStatus ? this.service.onStatus(fn) : () => {};
  }

  /** The kinds this browser runs for the model (catalog BROWSER_JOB_KINDS; not the internal self-test). */
  kinds() {
    return (this.service?.kinds?.() || []).map((k) => k.kind).filter((kind) => kind !== SELFTEST_KIND);
  }

  /** Why the service cannot run jobs here (when its status is "unsupported"). */
  get reason() {
    return this.service?.reason || null;
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
      text: `Job ${job.id} (in this browser): ${job.state}${tail}${files}${succeeded ? "" : " (no result yet)"}${job.error?.message ? `: ${job.error.message}` : ""}${succeeded ? limitsText(job.limitations) : ""}`,
      result: job, citations: [],
      governance: { kind: "system", released: null, artifact: null, verdict: null, claims: [], evidence: [], refusals: [], labels: [], licences: [], limitations: succeeded ? [...(job.limitations || [])] : ["Pending: until the job has succeeded it has no result."], outputs: [] },
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

/** A finished job's limitations as the model reads them, worded as the runner's envelope words them (envelope.py). */
function limitsText(limitations) {
  const list = (Array.isArray(limitations) ? limitations : []).map(String).filter(Boolean);
  if (!list.length) return "";
  const clip = (x) => {
    const one = x.split(/\s+/).filter(Boolean).join(" ");
    return one.length <= 320 ? one : `${one.slice(0, 319)}…`;
  };
  const lines = list.slice(0, 8).map((x) => `- ${clip(x)}`);
  if (list.length > 8) lines.push(`- … ${list.length - 8} more in the UI`);
  return `\nLimits (state these when you use this result):\n${lines.join("\n")}`;
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

/**
 * The page's job service, ready to take jobs, or one whose status says why it cannot: no Web Workers, no IndexedDB for
 * the job records, no private file storage (OPFS) for the files, or no boot.json. Nothing heavy starts here: Pyodide
 * boots in a job worker when the first job runs. opts: {bootUrl, siteUrl, Worker, indexedDB, storage, fetch}.
 */
export async function createJobsRuntime(opts = {}) {
  const site = opts.siteUrl || new URL("../../../", import.meta.url).href;
  const unsupported = (reason) => new BrowserJobsRuntime({ service: { status: "unsupported", reason, kinds: () => [], onStatus: () => () => {} } });
  const Worker = opts.Worker ?? globalThis.Worker;
  if (typeof Worker !== "function") return unsupported("this browser has no Web Workers");
  if (!(opts.indexedDB ?? globalThis.indexedDB)) return unsupported("this browser keeps no IndexedDB (a private window?)");
  const { store, reason } = await OpfsStore.open({ storage: opts.storage });
  if (!store) return unsupported(reason || "this browser has no Origin Private File System");
  let boot;
  try {
    const res = await (opts.fetch || globalThis.fetch)(opts.bootUrl || new URL("runtime/boot.json", site).href, { cache: "no-cache" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    boot = await res.json();
  } catch (err) {
    return unsupported(`runtime/boot.json could not be read (${err?.message || err})`);
  }
  const nav = globalThis.navigator || {};
  const size = recommendedPoolSize({ cores: nav.hardwareConcurrency, memoryGb: nav.deviceMemory, isolated: Boolean(globalThis.crossOriginIsolated) });
  const pool = new ScienceWorkerPool({
    size: size.size, Worker, boot, siteUrl: site, indexUrl: opts.indexUrl,
    workerUrl: new URL("js/scientific/engines/science.worker.js", site).href,
  });
  const datasets = new DatasetWorkspace({
    store, siteUrl: site, Worker, registryUrl: new URL(boot.datasets?.path || "runtime/datasets.json", site).href,
    workerUrl: new URL("js/scientific/data/data.worker.js", site).href,
  });
  const service = new BrowserJobService({ indexedDB: opts.indexedDB, pool, store, datasets, handlers: [...HANDLERS, browserSelfTest] });
  try {
    await service.start();
  } catch (err) {
    return unsupported(`the job records could not be opened (${err?.message || err})`);
  }
  const rt = new BrowserJobsRuntime({ service });
  rt.pool = { size: size.size, reason: size.reason };
  return rt;
}
