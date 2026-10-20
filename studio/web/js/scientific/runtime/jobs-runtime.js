// The browser's job service as a runtime the router can call (CONTRACTS §6's call(tool, args, ctx)): a job call
// placed in the browser comes here instead of to the Pyodide tool worker, is submitted to the BrowserJobService
// (./job-manager.js), and returns a "job_submitted" envelope at once; job_status for a browser job (an id starting
// "bj-") is answered from the job's record. The model sees the same shapes a runner job gives it.
//
// A job reads the project's own files (the omics jobs' count tables, the ADMET jobs' molecules and archive): the page's
// file store (core/store.js) is given to the service as a resolver, {find(projectId, ref)}, which names a file by its id
// or, as the runner resolves a bare name among its uploads, by its name: the newest file of that name in the project.
//
// The ADMET jobs (pipelines/admet.js) also get the RDKit.js build boot.json pins (its rdkit entry: the wasm's URL and
// SHA-256), the dataset workspace of the registry's "admet" group (the TDC archive, through the site's relay) and the
// person's acceleration setting (settings.browserAcceleration: where the applicability-domain search may run). The
// docking job (pipelines/dock.js) gets the AutoDock Vina build boot.json pins (its vina entry: the wasm's URL, SHA-256
// and size), which its own workers run (js/scientific/dock/dock.worker.js).

import { pipelineArguments } from "../../core/catalog.js";
import { glossary, t } from "../../core/i18n.js";
import { baseReceipt } from "../../core/receipt.js";
import { canonicalJson, isPlainObject, sha256Hex } from "../../core/util.js";
import { DatasetWorkspace } from "../data/datasets.js";
import { OpfsStore } from "../data/opfs-store.js";
import { HANDLERS } from "../pipelines/network-pharmacology.js";
import { ADMET_HANDLERS, createAdmetHandlers } from "../pipelines/admet.js";
import { DOCK_HANDLERS, createDockHandlers } from "../pipelines/dock.js";
import { OMICS_HANDLERS } from "../pipelines/omics.js";
import { SELFTEST_KIND, browserSelfTest } from "../pipelines/selftest.js";
import { BrowserJobService, envelopeFor } from "./job-manager.js";
import { ScienceWorkerPool, recommendedPoolSize } from "./worker-pool.js";

/** A browser job's id (the runner's never start so). */
export const BROWSER_JOB_ID = /^bj-[0-9a-f]{8,}$/;

const ENDED = ["succeeded", "failed", "cancelled", "interrupted"];
/** The handlers this page has, by kind: what a call needs the runner for is asked of them before it is placed. */
const BY_KIND = new Map([...HANDLERS, ...OMICS_HANDLERS, ...ADMET_HANDLERS, ...DOCK_HANDLERS].map((h) => [h.kind, h]));

/**
 * The job kind and parameters a call stands for ({kind, params}), or null when it is not a job this service runs; a
 * run_pipeline call the runner's schema refuses (core/catalog.js pipelineArguments) → {kind, problem}.
 */
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
  // as tcmstudio.core_tools.route maps it for the runner: job.pipeline.<pipeline> with the job kind's arguments (an
  // object, or the JSON text of one, as the dispatcher reads them)
  if (tool === "run_pipeline") {
    const pipeline = typeof a.pipeline === "string" ? a.pipeline.trim() : "";
    if (!pipeline) return null;
    const { params, problem } = pipelineArguments(a);
    return problem ? { kind: `pipeline.${pipeline}`, problem } : { kind: `pipeline.${pipeline}`, params };
  }
  const id = tool === "call_tool" ? String(a.tool || "") : String(tool || "");
  const params = tool === "call_tool" ? (isPlainObject(a.arguments) ? a.arguments : {}) : a;
  if (id.startsWith("job.")) return { kind: id.slice(4), params };
  if (id === "run_pipeline") return jobCall("run_pipeline", params);
  return null;
}

/**
 * The page's project files as the job service reads them: files is core/store.js's `files` ({list(projectId),
 * get(id)}). find(projectId, ref) → {id, name, bytes, sha256, blob} | null: the file with that id (in this project,
 * when the job has one), else the newest file of the project with that name. A job of no project finds files by id
 * only: a name means nothing outside a project.
 */
export function projectFilesResolver(files) {
  if (!files || typeof files.get !== "function" || typeof files.list !== "function") return null;
  const view = (r) => ({ id: r.id, name: r.name, bytes: r.bytes ?? r.blob?.size ?? null, sha256: r.sha256 ?? null, blob: r.blob ?? null, type: r.type ?? null });
  return {
    async find(projectId, ref) {
      const key = typeof ref === "string" ? ref.trim() : "";
      if (!key) return null;
      const byId = await files.get(key).catch(() => null);
      if (byId && (!projectId || byId.projectId === projectId)) return view(byId);
      if (!projectId) return null;
      const named = (await files.list(projectId)).filter((r) => r.name === key);
      // oldest first (as list() gives them; the sort is stable): the newest of the name, as the runner takes the newest
      // upload of a bare name
      named.sort((x, y) => (Number(x.createdAt) || 0) - (Number(y.createdAt) || 0));
      return named.length ? view(named[named.length - 1]) : null;
    },
  };
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
    /** The ADMET endpoints whose models are built in this browser ([] when none), null until the page has looked. */
    this.admetModels = null;
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

  /**
   * Why this call of `kind` needs the runner although the browser runs the kind (a FASTQ RNA-seq run, Scanpy, a 10x
   * HDF5 file …), from its parameters alone → {message, hint}, or null. Known from the handlers themselves, so it is
   * said even where the job service cannot start.
   */
  needsRunner(kind, params = {}) {
    const h = BY_KIND.get(String(kind || ""));
    return typeof h?.needsRunner === "function" ? h.needsRunner(isPlainObject(params) ? params : {}) : null;
  }

  async call(tool, args = {}, ctx = {}) {
    const input_sha256 = await sha256Hex(canonicalJson(isPlainObject(args) ? args : {})).catch(() => null);
    const status = browserJobStatus(tool, args);
    if (status) return this.#status(tool, status, input_sha256);
    const call = jobCall(tool, args);
    if (call?.problem) {
      return failed(tool, input_sha256, "bad_arguments", call.problem, "Give the job kind's parameters as an object in arguments: run_pipeline {pipeline, arguments: {…}}.");
    }
    if (!call || !this.runs(call.kind)) {
      if (call && BY_KIND.has(call.kind)) {
        return failed(tool, input_sha256, "unavailable", `${call.kind} is not a job this browser runs`, "Connect the local runner: it runs every job kind.");
      }
      // a kind no handler of this page knows (run_pipeline naming no pipeline): not one the runner is known to run either
      const env = failed(tool, input_sha256, "not_found", `${call ? call.kind : tool} is not a job this browser runs`,
        `This browser runs ${this.kinds().join(", ") || "no job kind now"}; catalog_search kind='job' lists every job kind and where it runs.`);
      env.error.reason = "not_found";
      return env;
    }
    try {
      const job = await this.service.submit(call.kind, call.params, { project_id: ctx.project_id || null, submission_id: ctx.submission_id || null });
      return envelopeFor(job, { tool, input_sha256 });
    } catch (err) {
      // a refusal before anything was queued (no private storage, no snapshots, bad parameters): said with its need
      const type = err?.type === "bad_arguments" ? "bad_arguments" : "unavailable";
      const env = failed(tool, input_sha256, type, err?.message || String(err), err?.hint || "");
      if (err?.need) env.error.requires = [{ need: err.need, detail: err.message || String(err), remedy: err.hint || "" }];
      // a call whose files only the runner reads (an .h5ad named by its id, a sheet's .h5 path) needs the runner, not the
      // job service: the reason is the need's, as the router's own needs_runner (tcmstudio.capability.REASON_NEED)
      if (err?.need === "dataset") env.error.reason = "needs_dataset";
      else if (err?.need === "runner") env.error.reason = "needs_runner";
      // more than a browser worker holds (pipeline.admet's molecules counted before it is queued): the runner runs it
      else if (err?.need === "memory") env.error.reason = "too_large_for_browser";
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
    // what a succeeded job's results rest on (an ADMET run's model cards), as the runner's receipt.provenance names it
    if (succeeded && isPlainObject(job.provenance)) {
      env.receipt.provenance = { datasets: [], seeds: {}, models: Array.isArray(job.provenance.models) ? [...job.provenance.models] : [] };
    }
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
 * boots in a job worker when the first job runs. opts: {bootUrl, siteUrl, Worker, indexedDB, storage, fetch, files:
 * the page's project file store (core/store.js `files`), which the jobs that read project files find them in;
 * settings: () → the page's settings (their browserAcceleration: "auto" | "cpu" | "reference")}.
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
  const admetData = new DatasetWorkspace({
    store, siteUrl: site, Worker, group: "admet", registryUrl: new URL(boot.datasets?.path || "runtime/datasets.json", site).href,
    workerUrl: new URL("js/scientific/data/data.worker.js", site).href,
  });
  const admet = createAdmetHandlers({
    rdkit: isPlainObject(boot.rdkit) ? boot.rdkit : null, ChemWorker: Worker,
    chemWorkerUrl: new URL("js/scientific/chem/chem.worker.js", site).href,
    preference: () => (typeof opts.settings === "function" ? opts.settings()?.browserAcceleration : null) || "auto",
  });
  // a site whose boot.json names no vina entry refuses the docking (null), rather than fetching a build it did not pin
  const dock = createDockHandlers({
    vina: isPlainObject(boot.vina) ? boot.vina : null, VinaWorker: Worker, siteUrl: site,
    vinaWorkerUrl: new URL("js/scientific/dock/dock.worker.js", site).href,
  });
  const service = new BrowserJobService({
    indexedDB: opts.indexedDB, pool, store, datasets, datasetGroups: { admet: admetData }, projectFiles: projectFilesResolver(opts.files),
    handlers: [...HANDLERS, ...OMICS_HANDLERS, ...admet, ...dock, browserSelfTest],
  });
  try {
    await service.start();
  } catch (err) {
    return unsupported(`the job records could not be opened (${err?.message || err})`);
  }
  const rt = new BrowserJobsRuntime({ service });
  rt.pool = { size: size.size, reason: size.reason };
  return rt;
}
