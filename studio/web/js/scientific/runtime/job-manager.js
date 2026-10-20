// The browser's job service (studio/docs/BROWSER_JOBS.md §3, §10): research jobs that run in this page's job workers
// (./worker-pool.js) instead of on a runner, kept in IndexedDB "tcmstudio-jobs" so a reload finds them again.
//
// A job is submitted (its kind's preflight may refuse it, and then nothing is queued), waits in this tab's queue
// (at most maxConcurrent run at once), runs its kind's handler (pipelines/*.js) with a context that does the
// bookkeeping — stage status and times, progress, logs, the artefacts it wrote, checkpoints — and ends succeeded,
// failed or cancelled. Every change is written to the job's record; a record keeps its last 200 log lines.
//
// When the handler has returned (or thrown), the service reads every artefact back from the origin's private file
// system and hashes it ("page_digests") to compare with the digest the job worker recorded when it wrote the file
// ("worker_digests"). A job succeeds only when the two agree for every artefact and nothing it was expected to write
// is missing; its receipt, out/job_receipt.json (engines/evidence.js), says what ran where and what was checked.
//
// Tabs. While this tab has a job queued or running it holds the Web Lock "tcmstudio.job.<id>". A tab that starts
// finds every job recorded as queued or running whose lock no live tab holds, and marks it "interrupted": the page
// (or the browser) closed while it ran. resume() queues it again; its handler reuses each stage whose checkpoint
// still re-hashes to the digests it recorded. A browser without Web Locks cannot tell another open tab's job from an
// orphan; there every such job is marked interrupted, and its log says why.
//
// The database stays at version 1: a new version would close it under every tab still running the previous release
// (core/store.js says the same of "tcmstudio"). New fields are added to the records, never to the schema.

import { Emitter } from "../../core/events.js";
import { baseReceipt } from "../../core/receipt.js";
import { abortError, canonicalJson, isAbort, isPlainObject, sha256Hex } from "../../core/util.js";
import {
  JOB_RECEIPT_FILE, bareSha256, buildJobReceipt, compareDigests, computeEvidence, digestChecks, pathText, platformOf, runtimeOf,
} from "../engines/evidence.js";

export const JOBS_DB = "tcmstudio-jobs";
export const JOBS_DB_VERSION = 1;
/** The runtime a job envelope names (receipt.runtime): this page's job service, protocol version 1. */
export const JOBS_RUNTIME = "studio-jobs/1";
export const CHECKPOINT_SCHEMA = "tcmstudio.checkpoint/1";
export const JOB_STATES = Object.freeze(["queued", "running", "succeeded", "failed", "cancelled", "interrupted"]);
export const ENDED_STATES = Object.freeze(["succeeded", "failed", "cancelled", "interrupted"]);
/** A record keeps this many log lines, the latest. */
export const LOG_KEEP = 200;
const LOG_LINE = 2000;
const DETAIL_CHARS = 1000;
const SUBMISSION = /^[A-Za-z0-9_.:-]{1,128}$/;
const LEVELS = new Set(["info", "warn", "error"]);
/** As tcmstudio.governance.LIMIT_PENDING says it for a runner job. */
const LIMIT_PENDING = "A job that has not succeeded is pending work, not a result.";
const DEVICE_NOTE = "assigned: the job's Python runs in this browser's job workers, CPython compiled to WebAssembly (Pyodide), on the CPU";
/** A job one of whose stages computed on WebGPU (its kernel calls' compute facts say so): what its record then says. */
const WEBGPU_NOTE = (ids) => `${DEVICE_NOTE}; ${ids.join(", ")} computed on this browser's WebGPU adapter, each result checked on the CPU (the job receipt's compute section names the adapter and the calibration)`;
/** A stage's compute facts are kept at most this many calls. */
const COMPUTE_KEEP = 200;
const MEMORY_MEASURED_BY = "the WebAssembly memory of each job worker when the job gave it back; a wasm32 memory only grows, so this is the peak since that worker started, which can include earlier jobs on it";

/** The Web Lock a tab holds while one of its jobs is queued or running. */
export const lockName = (id) => `tcmstudio.job.${id}`;
/** The job's directory in the origin's private file system. */
export const jobDir = (id) => ["tcmstudio", "jobs", id];

function typedError(type, message, extra = {}) {
  const err = new Error(message);
  err.type = type;
  return Object.assign(err, extra);
}

/** A refusal before anything is queued: type "unavailable" | "bad_arguments", need, hint (spec §10). */
function refusal(type, message, need, hint = "") {
  return typedError(type, message, { need, hint });
}

function cancelledError(message = "the job was cancelled") {
  const err = abortError(message);
  try { err.type = "cancelled"; } catch { /* a frozen DOMException: isAbort still knows it */ }
  return err;
}

function newJobId() {
  const b = new Uint8Array(8);
  globalThis.crypto.getRandomValues(b);
  return `bj-${[...b].map((x) => x.toString(16).padStart(2, "0")).join("")}`;
}

function clip(text, max) {
  const s = String(text ?? "");
  return s.length > max ? `${s.slice(0, max - 1)}…` : s;
}

const round3 = (x) => Math.round(x * 1000) / 1000;

/** A copy that shares nothing with the record (the record is what this service writes). */
function copy(v) {
  return typeof structuredClone === "function" ? structuredClone(v) : JSON.parse(JSON.stringify(v));
}

/** Only what JSON keeps: the parameters and stage records are stored and compared as JSON. */
function jsonOnly(v) {
  return v === undefined ? undefined : JSON.parse(JSON.stringify(v));
}

/** The stages a job of this handler starts with: stagesFor(params) when the kind's stages depend on its parameters
 * (admet.models: two per endpoint), else its fixed list. */
function stagesOf(handler, params) {
  if (typeof handler?.stagesFor === "function") {
    try {
      const list = handler.stagesFor(isPlainObject(params) ? params : {});
      if (Array.isArray(list)) return list;
    } catch { /* the fixed list */ }
  }
  return handler?.stages || [];
}

function blankStage(s) {
  return {
    id: String(s.id), title: s.title && typeof s.title === "object" ? { ...s.title } : { zh: String(s.id), en: String(s.id) },
    status: "pending", started_at: null, finished_at: null, seconds: null, worker: null, detail: null, reused: false,
  };
}

function errorOf(err) {
  const out = { type: typeof err?.type === "string" && err.type ? err.type : "runtime_error", message: clip(err?.message || String(err), 1500) };
  if (err?.hint) out.hint = String(err.hint);
  // what it would need to run (a refusal's: "memory" when the analysis is more than a browser worker holds)
  if (typeof err?.need === "string" && err.need) out.need = err.need;
  return out;
}

// --------------------------------------------------------------------------------------------------- IndexedDB

function req(r) {
  return new Promise((resolve, reject) => {
    r.onsuccess = () => resolve(r.result);
    r.onerror = () => reject(r.error);
  });
}

/** A connection that is gone rather than a request that was wrong: worth one new connection and one retry. */
function connectionLost(err) {
  return err?.name === "InvalidStateError" || err?.name === "UnknownError";
}

class JobsDb {
  constructor(db, idb, name) {
    this.idb = idb;
    this.name = name;
    this.upgraded = false;
    this.#adopt(db);
  }

  #adopt(db) {
    this.db = db;
    // a newer release upgraded the database in another tab: let it, and stop writing here (this tab must reload)
    db.onversionchange = () => { this.upgraded = true; try { db.close(); } catch { /* closed */ } };
  }

  static open(idb, name) {
    return new Promise((resolve, reject) => {
      let r;
      try { r = idb.open(name, JOBS_DB_VERSION); } catch (err) { reject(err); return; }
      r.onupgradeneeded = () => {
        const jobs = r.result.createObjectStore("jobs", { keyPath: "id" });
        jobs.createIndex("project_id", "project_id");
        jobs.createIndex("created_at", "created_at");
      };
      r.onsuccess = () => resolve(new JobsDb(r.result, idb, name));
      r.onerror = () => reject(r.error);
      r.onblocked = () => reject(new Error("the job database is blocked by another open tab"));
    });
  }

  /** body(store) in a new transaction; when the connection is gone (Safari drops it in background tabs), once more. */
  async #run(mode, body) {
    for (let attempt = 0; ; attempt++) {
      try {
        const tx = this.db.transaction("jobs", mode);
        const done = mode === "readwrite" ? new Promise((resolve, reject) => {
          tx.oncomplete = () => resolve();
          tx.onerror = () => reject(tx.error);
          tx.onabort = () => reject(tx.error || new Error("transaction aborted"));
        }) : null;
        // awaited below once the body succeeds; a body that throws reports its own error, not this one again
        done?.catch(() => {});
        const result = await body(tx.objectStore("jobs"));
        if (done) await done;
        return result;
      } catch (err) {
        if (attempt > 0 || !connectionLost(err) || this.upgraded) throw err;
        this.#adopt((await JobsDb.open(this.idb, this.name)).db);
      }
    }
  }

  get(id) { return this.#run("readonly", async (s) => (await req(s.get(id))) ?? null); }
  all() { return this.#run("readonly", (s) => req(s.getAll())); }
  byProject(projectId) { return this.#run("readonly", (s) => req(s.index("project_id").getAll(projectId))); }
  put(record) { return this.#run("readwrite", (s) => req(s.put(record))); }
  delete(id) { return this.#run("readwrite", (s) => req(s.delete(id))); }
}

// --------------------------------------------------------------------------------------------------- the service

export class BrowserJobService {
  #idb;
  #locks;
  #pool;
  #store;
  #datasets;
  #groups = new Map();
  #projectFiles;
  #handlers = new Map();
  #now;
  #max;
  #hashFile;
  #dbName;
  #cancelGraceMs;
  #nav;
  #db = null;
  #status = "idle";
  #reason = null;
  #startP = null;
  #bus = new Emitter();
  /** the records of the jobs this tab has queued or is running (the newest state; the database follows) */
  #jobs = new Map();
  /** per job this tab runs: {lock, controller, leases, probes, memory, outcome, force, stage, t0} */
  #runs = new Map();
  #queue = [];
  #running = new Set();
  #pumpTimer = null;
  #writes = new Map();
  #submissions = new Map();
  #resuming = new Set();

  /**
   * indexedDB, locks (navigator.locks), pool (a ScienceWorkerPool), store (an OpfsStore), datasets (the dataset
   * workspace of the research files), datasetGroups ({group: DatasetWorkspace} of other registry groups: "admet", the
   * TDC archive the ADMET models are built from), projectFiles (the page's project files: {async find(projectId, ref)
   * → {id, name, bytes, sha256, blob} | null}, ref a file id or a file name — the newest file of that name in the
   * project), handlers ([{kind, title, stages, stagesFor?(params), preflight?, run, expected?}]), now (→ Date),
   * maxConcurrent (1), hashFile(path, algorithm) → hex (default: streamed from the store through data/digest.js),
   * cancelGraceMs: how long a cancelled handler has to stop before its job is ended as cancelled without it (10 s).
   */
  constructor({
    indexedDB = globalThis.indexedDB, locks = globalThis.navigator?.locks, pool = null, store = null, datasets = null,
    datasetGroups = null, projectFiles = null, handlers = [], now = () => new Date(), maxConcurrent = 1, hashFile = null,
    dbName = JOBS_DB, cancelGraceMs = 10_000, navigator: nav = globalThis.navigator,
  } = {}) {
    this.#idb = indexedDB || null;
    this.#locks = locks && typeof locks.request === "function" ? locks : null;
    this.#pool = pool;
    this.#store = store;
    this.#datasets = datasets;
    for (const [group, ws] of Object.entries(isPlainObject(datasetGroups) ? datasetGroups : {})) if (ws) this.#groups.set(group, ws);
    this.#projectFiles = projectFiles && typeof projectFiles.find === "function" ? projectFiles : null;
    for (const h of handlers || []) if (h && typeof h.kind === "string" && typeof h.run === "function") this.#handlers.set(h.kind, h);
    this.#now = now;
    this.#max = Math.max(1, Math.floor(Number(maxConcurrent)) || 1);
    this.#hashFile = typeof hashFile === "function" ? hashFile : (path, algorithm) => this.#digestFromStore(path, algorithm);
    this.#dbName = dbName;
    this.#cancelGraceMs = cancelGraceMs;
    this.#nav = nav || null;
  }

  /** What a handler's preflight reads (and, with the job's project_id, its third argument, the project's files). */
  get store() { return this.#store; }
  get datasets() { return this.#datasets; }
  /** The dataset workspace of a registry group ("research", "admet"), or null when this page has none for it. */
  datasetsFor(group) {
    const g = String(group || "");
    if (this.#groups.has(g)) return this.#groups.get(g);
    return this.#datasets && (this.#datasets.group ?? "research") === g ? this.#datasets : null;
  }
  get pool() { return this.#pool; }
  get projectFiles() { return this.#projectFiles; }

  /** "idle" before start(), then "ready" or "unsupported" (with `reason`). */
  get status() { return this.#status; }
  get reason() { return this.#reason; }

  onStatus(fn) { return this.#bus.on("status", fn); }

  /** [{kind, title, stages: [{id, title}]}] of the kinds this page runs. */
  kinds() {
    return [...this.#handlers.values()].map((h) => ({ kind: h.kind, title: h.title ? { ...h.title } : null, stages: (h.stages || []).map((s) => ({ id: s.id, title: s.title ? { ...s.title } : null })) }));
  }

  /** Open the database and recover what earlier pages left running. Resolves with the status; never rejects. */
  start() {
    if (!this.#startP) this.#startP = this.#start();
    return this.#startP;
  }

  async #start() {
    if (!this.#idb) return this.#unsupported("this browser has no IndexedDB to keep jobs in");
    if (!this.#pool || this.#pool.available === false) return this.#unsupported("this browser cannot start job workers (no Web Worker)");
    try {
      this.#db = await JobsDb.open(this.#idb, this.#dbName);
    } catch (err) {
      return this.#unsupported(`the job database could not be opened (${err?.name || "Error"}: ${err?.message || err}); private windows may refuse it`);
    }
    try {
      await this.#recover();
    } catch (err) {
      (globalThis.reportError || console.error)(err);
    }
    this.#setStatus("ready");
    return this.#status;
  }

  #unsupported(reason) {
    this.#reason = reason;
    this.#setStatus("unsupported");
    return this.#status;
  }

  #setStatus(status) {
    this.#status = status;
    this.#bus.emit("status", { status, reason: this.#reason });
  }

  /** Every job recorded as queued or running that no live tab holds the lock of → interrupted. */
  async #recover() {
    const open = (await this.#db.all()).filter((j) => (j.state === "queued" || j.state === "running") && !this.#runs.has(j.id));
    for (const rec of open) await this.#reclaim(rec, { atStart: true });
  }

  /**
   * A record that says queued or running for a job this tab does not run → the record as it now is: interrupted when
   * no live tab runs it. The job's lock is taken first and the record read again under it, so a tab that is just
   * ending the job (it writes its last record before it lets the lock go) is never overwritten, and only a record that
   * still says queued or running is changed. Without Web Locks a job another open tab runs cannot be told apart: then
   * only start() does this (as it always has, and says so in the job's log), never a later read.
   */
  async #reclaim(rec, { atStart = false } = {}) {
    if (!this.#db || this.#runs.has(rec.id) || (rec.state !== "queued" && rec.state !== "running")) return rec;
    if (!this.#locks && !atStart) return rec;
    const lock = await this.#hold(rec.id);
    if (!lock) return rec; // a live tab has it queued or running
    if (!lock.held && !atStart) return rec; // the locks refused here (an opaque origin): only start() decides then
    try {
      const now = await this.#db.get(rec.id);
      if (!now || (now.state !== "queued" && now.state !== "running") || this.#runs.has(rec.id)) return now || rec;
      const at = this.#iso();
      for (const s of now.stages || []) {
        if (s.status !== "running") continue;
        s.status = "cancelled";
        s.finished_at = at;
        s.detail = "interrupted: the page was closed while this stage ran";
      }
      const was = now.state;
      now.state = "interrupted";
      now.progress = null;
      now.error = {
        type: "interrupted",
        message: `the page (or the browser) was closed while the job was ${was}; resume it to continue`,
        hint: "Resume the job: stages whose outputs still match their checkpoints are reused.",
      };
      this.#appendLog(now, lock.held ? "info" : "warn", lock.held
        ? "interrupted: no open tab was running it"
        : "interrupted: no tab was running it (Web Locks are missing here or could not be queried, so a job another open tab runs cannot be told apart)");
      await this.#db.put(now);
      return now;
    } finally {
      lock.release();
    }
  }

  /** The names of the locks held or wanted by any live tab of this origin; null without Web Locks. */
  async #heldLocks() {
    if (!this.#locks || typeof this.#locks.query !== "function") return null;
    try {
      const q = await this.#locks.query();
      return new Set([...(q?.held || []), ...(q?.pending || [])].map((l) => l.name));
    } catch {
      return null;
    }
  }

  /**
   * Hold the job's Web Lock until release() → {held, release}, or null when another tab holds it. Without Web Locks
   * nothing is held (held: false).
   */
  async #hold(id) {
    if (!this.#locks) return { held: false, release() {} };
    let release;
    const until = new Promise((resolve) => { release = resolve; });
    try {
      const got = await new Promise((resolve, reject) => {
        this.#locks.request(lockName(id), { mode: "exclusive", ifAvailable: true }, (lock) => {
          resolve(Boolean(lock));
          // held until the job ends: the request settles only when this promise does
          return lock ? until : undefined;
        }).catch(reject);
      });
      return got ? { held: true, release } : null;
    } catch {
      // a context that refuses locks (an opaque origin) runs the job unguarded, as a browser without them does
      return { held: false, release() {} };
    }
  }

  // ------------------------------------------------------------------------------------------------------ submit

  /**
   * Queue a job of `kind` → its view. The kind's preflight(params, service, {project_id}) runs first; a refusal throws
   * an Error with type ("unavailable" | "bad_arguments"), need and hint, and nothing is queued. The same submission_id
   * returns the job it already made.
   */
  async submit(kind, params = {}, { project_id = null, submission_id = null } = {}) {
    await this.start();
    if (this.#status !== "ready") {
      throw refusal("unavailable", `this browser's job service is not available: ${this.#reason}`, "job_service",
        "Use a current Chromium-based browser such as Chrome or Edge, outside a private window (Firefox and Safari have not been tested with these jobs), or connect the local runner.");
    }
    const handler = this.#handlers.get(String(kind || ""));
    if (!handler) {
      throw refusal("unavailable", `this browser does not run job kind '${kind}'`, "job_kind", "Connect the local runner: it runs every job kind.");
    }
    if (params === null || params === undefined) params = {};
    if (!isPlainObject(params)) throw refusal("bad_arguments", "the job's parameters must be an object", "arguments");
    if (project_id !== null && project_id !== undefined && (typeof project_id !== "string" || project_id.length > 200)) {
      throw refusal("bad_arguments", "project_id must be a string of at most 200 characters", "arguments");
    }
    let stored;
    try {
      stored = jsonOnly(params);
    } catch (err) {
      throw refusal("bad_arguments", `the job's parameters cannot be stored as JSON: ${err?.message || err}`, "arguments");
    }
    if (submission_id !== null && submission_id !== undefined) {
      if (typeof submission_id !== "string" || !SUBMISSION.test(submission_id)) {
        throw refusal("bad_arguments", "submission_id: letters, digits and _.:- (at most 128)", "arguments");
      }
      const pending = this.#submissions.get(submission_id);
      if (pending) return pending;
      const p = (async () => {
        const same = [...this.#jobs.values()].find((j) => j.submission_id === submission_id)
          || (await this.#db.all()).find((j) => j.submission_id === submission_id);
        if (same) return this.#view(this.#jobs.get(same.id) || same);
        return this.#create(handler, stored, project_id ?? null, submission_id);
      })();
      this.#submissions.set(submission_id, p);
      try { return await p; } finally { this.#submissions.delete(submission_id); }
    }
    return this.#create(handler, stored, project_id ?? null, null);
  }

  async #create(handler, params, projectId, submissionId) {
    try {
      if (typeof handler.preflight === "function") await handler.preflight(params, this, { project_id: projectId });
    } catch (err) {
      const e = err instanceof Error ? err : new Error(String(err));
      if (!e.type) e.type = "unavailable";
      throw e;
    }
    const id = newJobId();
    const job = {
      id, kind: handler.kind, where: "browser", state: "queued", params, project_id: projectId, submission_id: submissionId,
      title: handler.title ? { ...handler.title } : { zh: handler.kind, en: handler.kind },
      created_at: this.#iso(), started_at: null, finished_at: null, progress: null,
      stages: stagesOf(handler, params).map(blankStage),
      artefacts: [], inputs: {}, device: "cpu", device_note: DEVICE_NOTE, outcome: null, result: null, receipt: null,
      error: null, logs: [], attempts: 1,
    };
    const lock = await this.#hold(id);
    if (!lock) throw refusal("unavailable", `the lock of new job ${id} is taken`, "job_service");
    this.#appendLog(job, "info", "queued");
    this.#jobs.set(id, job);
    try {
      await this.#save(job);
    } catch (err) {
      this.#jobs.delete(id);
      lock.release();
      throw refusal("unavailable", `the job could not be recorded: ${err?.message || err}`, "storage");
    }
    this.#enqueue(job, lock);
    const view = this.#view(job);
    this.#emit(job, "state");
    return view;
  }

  #enqueue(job, lock) {
    let finished;
    const ended = new Promise((resolve) => { finished = resolve; });
    this.#runs.set(job.id, {
      lock, controller: null, leases: new Set(), wrapped: new Map(), usage: new Map(), probes: [], memory: [], outcome: {},
      force: null, forceTimer: null, stage: null, t0: null, closed: false, checkpoint: null, ended, finished,
    });
    this.#queue.push(job.id);
    this.#schedule();
  }

  /** Start queued jobs on the next turn, so a caller that just submitted one can subscribe to its events first. */
  #schedule() {
    if (this.#pumpTimer) return;
    this.#pumpTimer = setTimeout(() => {
      this.#pumpTimer = null;
      this.#pump();
    }, 0);
  }

  #pump() {
    while (this.#running.size < this.#max && this.#queue.length) {
      const id = this.#queue.shift();
      this.#running.add(id);
      this.#execute(id).catch((err) => (globalThis.reportError || console.error)(err));
    }
  }

  // ------------------------------------------------------------------------------------------------------- run

  async #execute(id) {
    const job = this.#jobs.get(id);
    const run = this.#runs.get(id);
    if (!job || !run) {
      // queued twice (two resumes where nothing could tell them apart): the first one ran it; this one has nothing to do
      this.#running.delete(id);
      this.#schedule();
      return;
    }
    const handler = this.#handlers.get(job.kind);
    run.controller = new AbortController();
    const forced = new Promise((_, reject) => { run.force = reject; });
    forced.catch(() => {});
    job.state = "running";
    job.started_at = this.#iso();
    job.error = null;
    job.progress = { fraction: 0, stage: null, message: "" };
    run.t0 = this.#now();
    this.#appendLog(job, "info", job.attempts > 1 ? `running (attempt ${job.attempts})` : "running");
    await this.#save(job).catch(() => {});
    this.#emit(job, "state");
    let returned = null;
    let failure = null;
    try {
      const ctx = this.#context(job, run);
      returned = await Promise.race([Promise.resolve().then(() => handler.run(ctx)), forced]);
    } catch (err) {
      failure = err;
    }
    await this.#finish(job, run, handler, returned, failure);
  }

  async #finish(job, run, handler, returned, failure) {
    clearTimeout(run.forceTimer);
    // from here on the handler's context changes nothing (a handler cut off by the cancel grace may still be running)
    run.closed = true;
    try {
      await this.#record(job, run, handler, returned, failure);
    } catch (err) {
      // the end could not be assembled (a bug, or storage that failed under it): the job still ends, and says so,
      // so that its lock is released and the queue behind it moves on
      (globalThis.reportError || console.error)(err);
      if (!ENDED_STATES.includes(job.state)) {
        Object.assign(job, {
          state: "failed", finished_at: this.#iso(), progress: null, result: null,
          error: { type: "runtime_error", message: clip(`the job's end could not be recorded: ${err?.message || err}`, 1500) },
        });
        await this.#save(job).catch(() => {});
        this.#emit(job, "state");
        this.#emit(job, "error", job.error);
        this.#emit(job, "done");
      }
    } finally {
      run.lock?.release();
      this.#runs.delete(job.id);
      this.#jobs.delete(job.id);
      this.#writes.delete(job.id);
      this.#running.delete(job.id);
      run.finished();
      this.#schedule();
    }
  }

  /** Read the outputs back, write the receipt, and put the job's end on its record (one write, then the events). */
  async #record(job, run, handler, returned, failure) {
    const stopped = run.controller.signal.aborted;
    await this.#settleLeases(run);
    let state = stopped ? "cancelled" : failure ? "failed" : "succeeded";
    let error = state === "failed" ? errorOf(failure) : null;
    const at = this.#iso();
    for (const s of job.stages) {
      if (s.status !== "running") continue;
      s.status = state === "cancelled" ? "cancelled" : "failed";
      s.finished_at = at;
    }
    if (state === "succeeded" && returned && typeof returned === "object") {
      try {
        this.#mergeOutcome(run, returned.outcome);
        job.result = returned.result === undefined ? null : jsonOnly(returned.result);
      } catch (err) {
        // a result the record cannot hold is no result: it could not be shown or followed up later
        state = "failed";
        error = { type: "runtime_error", message: clip(`the job's result cannot be kept as JSON: ${err?.message || err}`, 1500) };
        job.result = null;
      }
    }
    const o = run.outcome;
    const expected = this.#expected(handler, job.params);
    // a cancelled job is not read back: the person asked it to stop
    const collected = state === "cancelled" ? this.#uncollected(job) : await this.#collect(job, expected);
    const problems = [...(o.problems || []), ...collected.problems];
    const missing = [...new Set([...(o.missing || []), ...collected.missing])];
    if (state === "succeeded" && (collected.problems.length || missing.length)) {
      state = "failed";
      error = { type: "integrity", message: clip([...collected.problems, ...missing.map((n) => `${n} is missing`)].join("; "), 1500) };
    }
    const checks = state === "succeeded" ? [...(o.checks || []), ...digestChecks(collected.cmp)] : [];
    // the job's end, assembled apart and put on the record in one step: no reader sees it ended without its receipt
    const end = {
      state, error, finished_at: at, progress: null, result: state === "succeeded" ? job.result : null, receipt: null,
      outcome: { checks, problems: problems.map(String), missing, expected },
    };
    if (typeof o.complete === "boolean") end.outcome.complete = o.complete;
    if (o.limitations?.length) end.limitations = [...o.limitations];
    if (o.provenance) end.provenance = o.provenance;
    // a stage that computed on WebGPU: the record says the job used it (the receipt says with what, and how checked)
    const gpuStages = computeEvidence(job.stages)?.gpu_stages || [];
    if (gpuStages.length) Object.assign(end, { device: "cpu+webgpu", device_note: WEBGPU_NOTE(gpuStages) });
    try {
      end.receipt = await this.#writeReceipt({ ...job, ...end }, run, collected, expected);
    } catch (err) {
      // a job whose record of the run cannot be kept has no verified result
      const why = `the job receipt could not be written: ${err?.message || err}`;
      if (end.state === "succeeded") {
        end.state = "failed";
        end.error = { type: "storage", message: why };
        end.result = null;
        end.outcome.checks = [];
      }
      end.outcome.problems.push(why);
    }
    Object.assign(job, end);
    this.#appendLog(job, job.state === "succeeded" ? "info" : job.state === "failed" ? "error" : "warn",
      job.state === "failed" ? `failed: ${job.error?.message || "no reason given"}` : job.state);
    await this.#save(job).catch(() => {});
    this.#emit(job, "state");
    if (job.state === "failed") this.#emit(job, "error", job.error);
    this.#emit(job, "done");
  }

  /**
   * A job of this tab whose end is being recorded (its handler has stopped, or it was cancelled while queued; its lock
   * is not yet released): wait for that, so whoever saw it end can resume or remove it at once. → true when the job is
   * still queued or running here.
   */
  async #busyHere(id) {
    const run = this.#runs.get(id);
    if (!run) return false;
    if (!run.closed && !ENDED_STATES.includes(this.#jobs.get(id)?.state)) return true;
    await run.ended;
    return this.#runs.has(id);
  }

  /** Give back the workers the handler still holds; stop the ones still computing. Waits for the memory readings. */
  async #settleLeases(run) {
    for (const lease of run.leases) {
      if (run.usage.has(lease)) continue; // given back already (its memory reading may still be under way)
      if (lease.busy) lease.terminate();
      else if (!lease.released) run.wrapped.get(lease)?.release();
    }
    await Promise.allSettled(run.probes);
  }

  #mergeOutcome(run, o) {
    if (!o || typeof o !== "object") return;
    const out = run.outcome;
    for (const key of ["checks", "problems", "missing", "limitations"]) {
      if (Array.isArray(o[key])) out[key] = [...(out[key] || []), ...jsonOnly(o[key])];
    }
    if (typeof o.complete === "boolean") out.complete = o.complete;
    // what the results rest on, as a runner job's receipt keeps it (receipt.provenance): the models' cards
    if (isPlainObject(o.provenance)) {
      const p = jsonOnly(o.provenance);
      out.provenance = { ...(out.provenance || {}) };
      for (const [k, v] of Object.entries(p)) out.provenance[k] = Array.isArray(v) ? [...(Array.isArray(out.provenance[k]) ? out.provenance[k] : []), ...v] : v;
    }
  }

  #expected(handler, params) {
    try {
      const e = typeof handler.expected === "function" ? handler.expected(params) : handler.expected;
      return Array.isArray(e) ? e.map(String) : [];
    } catch {
      return [];
    }
  }

  /** Read every artefact back from storage and hash it, to compare with the worker's digest. */
  async #collect(job, expected) {
    const workerDigests = {};
    const pageDigests = {};
    const problems = [];
    const missing = [];
    for (const a of job.artefacts) {
      workerDigests[a.name] = bareSha256(a.sha256);
      pageDigests[a.name] = null;
      if (!this.#store) {
        problems.push(`${a.name}: there is no storage here to read it back from`);
        continue;
      }
      const path = this.#resolve(job.id, a.path);
      try {
        const st = await this.#store.stat(path);
        if (!st) {
          missing.push(a.name);
          continue;
        }
        if (Number.isFinite(a.bytes) && Number.isFinite(st.bytes) && st.bytes !== a.bytes) {
          problems.push(`${a.name}: ${st.bytes} bytes in storage, the worker wrote ${a.bytes}`);
        }
        pageDigests[a.name] = bareSha256(await this.#hashFile(path, "sha256"));
        // gone between the stat and the read, or a hash that is not a SHA-256: the page has no digest to agree with
        if (!pageDigests[a.name]) problems.push(`${a.name}: reading it back from storage gave no SHA-256`);
      } catch (err) {
        problems.push(`${a.name} could not be read back: ${err?.message || err}`);
      }
    }
    const names = new Set(job.artefacts.map((a) => a.name));
    for (const n of expected) if (!names.has(n)) missing.push(n);
    const cmp = compareDigests(job.artefacts, workerDigests, pageDigests);
    for (const n of cmp.unrecorded) problems.push(`${n}: the job worker recorded no SHA-256 for it`);
    for (const n of cmp.mismatched) {
      problems.push(`${n}: read back from storage its SHA-256 is ${pageDigests[n].slice(0, 16)}…, the job worker recorded ${workerDigests[n].slice(0, 16)}…`);
    }
    return { workerDigests, pageDigests, problems, missing, cmp };
  }

  #uncollected(job) {
    const workerDigests = Object.fromEntries(job.artefacts.map((a) => [a.name, bareSha256(a.sha256)]));
    const pageDigests = Object.fromEntries(job.artefacts.map((a) => [a.name, null]));
    return { workerDigests, pageDigests, problems: [], missing: [], cmp: compareDigests(job.artefacts, workerDigests, pageDigests) };
  }

  async #writeReceipt(job, run, collected, expected) {
    if (!this.#store) throw new Error("this browser has no private file system for it");
    const leases = [...run.leases];
    const byWorker = new Map();
    // each lease's use as the handler left it (the service's own memory reading is not the job's work)
    const usage = leases.map((l) => run.usage.get(l) || l.stats());
    for (const u of usage) {
      const w = byWorker.get(u.id) || { id: u.id, stages: [], busy_ms: 0, calls: 0, boot_ms: null };
      w.busy_ms += u.busy_ms || 0;
      w.calls += u.calls || 0;
      if (Number.isFinite(u.boot_ms)) w.boot_ms = (w.boot_ms || 0) + u.boot_ms;
      byWorker.set(u.id, w);
    }
    for (const s of job.stages) {
      if (!s.worker) continue;
      const w = byWorker.get(s.worker) || { id: s.worker, stages: [], busy_ms: 0, calls: 0, boot_ms: null };
      if (!w.stages.includes(s.id)) w.stages.push(s.id);
      byWorker.set(s.worker, w);
    }
    const infos = leases.map((l) => l.info).filter(Boolean);
    const busyMs = usage.reduce((a, u) => a + (u.busy_ms || 0), 0);
    const peak = run.memory.filter(Number.isFinite);
    const nav = this.#nav;
    const doc = buildJobReceipt({
      job,
      artefacts: job.artefacts,
      workerDigests: collected.workerDigests,
      pageDigests: collected.pageDigests,
      workers: [...byWorker.values()],
      runtime: runtimeOf(infos),
      platform: {
        python: platformOf(infos),
        user_agent: nav?.userAgent ?? null,
        cross_origin_isolated: Boolean(globalThis.crossOriginIsolated),
        cores: nav?.hardwareConcurrency ?? null,
        device_memory_gb: nav?.deviceMemory ?? null,
      },
      resources: {
        wall_s: job.started_at && job.finished_at ? round3(Math.max(0, Date.parse(job.finished_at) - Date.parse(job.started_at)) / 1000) : null,
        busy_s: round3(busyMs / 1000),
        memory: peak.length
          ? { wasm_peak_bytes: Math.max(...peak), measured_by: MEMORY_MEASURED_BY }
          : { wasm_peak_bytes: null, measured_by: null, reason: leases.length ? "no job worker reported its memory (stopped, or released mid-call)" : "no job worker ran" },
        peak_rss_mb: null,
      },
      checks: job.outcome.checks,
      problems: job.outcome.problems,
      limitations: job.limitations || [],
      stages: job.stages,
      paramsSha256: await sha256Hex(canonicalJson(job.params || {})),
      expected,
      missing: job.outcome.missing,
    });
    // indent 1, as the runner writes it; the digest is of exactly these bytes (UTF-8)
    const text = JSON.stringify(doc, null, 1);
    await this.#store.writeText([...jobDir(job.id), "out", JOB_RECEIPT_FILE], text);
    return { path: JOB_RECEIPT_FILE, sha256: await sha256Hex(text) };
  }

  // ------------------------------------------------------------------------------------------------- context

  #context(job, run) {
    const signal = run.controller.signal;
    const dir = jobDir(job.id);
    const throwIfCancelled = () => {
      if (signal.aborted || run.closed) throw cancelledError();
    };
    const live = (fn) => (...args) => (run.closed ? undefined : fn(...args));
    return {
      job: this.#view(job),
      params: copy(job.params),
      signal,
      pool: this.#leaser(job, run),
      store: this.#store,
      datasets: this.#datasets,
      /** the dataset workspace of a registry group ("admet"), or null */
      datasetsFor: (group) => this.datasetsFor(group),
      /** the page's project files ({find(projectId, ref)}); the job's project is job.project_id */
      projectFiles: this.#projectFiles,
      dir: [...dir],
      /** scratch space shared by the handler's stages; never stored */
      state: {},
      stage: (id, fn, opts) => this.#stage(job, run, id, fn, opts),
      checkpoint: this.#checkpoints(job, run),
      progress: live((fraction, message) => this.#progress(job, run, fraction, message)),
      log: live((level, text) => this.#log(job, level, text)),
      artefact: live((a) => this.#artefact(job, a)),
      /** forget the artefacts recorded under `under` (segments under the job's directory): a stage that writes that
       * directory afresh lists what it wrote, not what an earlier attempt wrote there */
      forget: live((under) => this.#forget(job, under)),
      /** an input file the job read: {name, sha256, bytes, source, group?} → job.inputs (and the receipt's inputs) */
      input: live((i) => this.#input(job, i)),
      /** checks / problems / missing / limitations / complete the handler found, added to the job's outcome */
      outcome: live((o) => this.#mergeOutcome(run, o)),
      /** a finished stage's kernel results that the job did not use after all (a later stage's check rejected them and
       * computed its own): each of that stage's compute facts is marked discarded with the reason, so the evidence counts
       * neither its GPU nor an acceleration */
      discardCompute: live((id, reason) => this.#discardCompute(job, id, reason)),
      throwIfCancelled,
    };
  }

  /**
   * ctx.checkpoint: records at <job>/checkpoints/<stage>.json ({schema: "tcmstudio.checkpoint/1", stage,
   * inputs_digest, outputs: [{path, bytes, sha256}]}); verify() re-hashes every recorded output from storage.
   */
  #checkpoints(job, run) {
    if (run.checkpoint) return run.checkpoint;
    const dir = jobDir(job.id);
    const checkpoint = {
      read: async (id) => {
        if (!this.#store) return null;
        try {
          const rec = await this.#store.readJson([...dir, "checkpoints", `${id}.json`]);
          return rec && rec.schema === CHECKPOINT_SCHEMA && rec.stage === id ? rec : null;
        } catch {
          return null; // a damaged checkpoint is no checkpoint: the stage runs again
        }
      },
      write: async (id, rec = {}) => {
        if (!this.#store) throw typedError("storage", "there is no storage here for checkpoints");
        const doc = {
          schema: CHECKPOINT_SCHEMA, stage: String(id), inputs_digest: rec.inputs_digest ?? null,
          outputs: (rec.outputs || []).map((o) => ({
            path: Array.isArray(o.path) ? [...o.path] : String(o.path).split("/"), bytes: Number.isFinite(o.bytes) ? o.bytes : null,
            sha256: bareSha256(o.sha256),
          })),
          written_at: this.#iso(), attempt: job.attempts,
        };
        await this.#store.writeJson([...dir, "checkpoints", `${id}.json`], doc);
        return doc;
      },
      /** Does every output the checkpoint recorded still re-hash to its digest? → {ok, problems, checked}. */
      verify: async (rec, { inputs_digest } = {}) => {
        if (!rec || rec.schema !== CHECKPOINT_SCHEMA) return { ok: false, problems: ["no checkpoint"], checked: 0 };
        const problems = [];
        if (inputs_digest !== undefined && rec.inputs_digest !== inputs_digest) problems.push("the stage's inputs changed since its checkpoint");
        const outputs = Array.isArray(rec.outputs) ? rec.outputs : [];
        if (!outputs.length) problems.push("the checkpoint records no outputs to check");
        let checked = 0;
        for (const o of outputs) {
          const path = this.#resolve(job.id, o.path);
          const name = pathText(o.path);
          try {
            const st = this.#store ? await this.#store.stat(path) : null;
            if (!st) { problems.push(`${name} is missing`); continue; }
            if (Number.isFinite(o.bytes) && st.bytes !== o.bytes) { problems.push(`${name} has ${st.bytes} bytes, its checkpoint recorded ${o.bytes}`); continue; }
            const sha = bareSha256(await this.#hashFile(path, "sha256"));
            checked += 1;
            if (!sha || sha !== bareSha256(o.sha256)) problems.push(`${name} changed since its checkpoint`);
          } catch (err) {
            problems.push(`${name} could not be read: ${err?.message || err}`);
          }
        }
        return { ok: !problems.length, problems, checked };
      },
    };
    /** read() and verify() in one: → verify's {ok, problems, checked}. */
    checkpoint.reusable = async (id, opts) => checkpoint.verify(await checkpoint.read(id), opts);
    run.checkpoint = checkpoint;
    return checkpoint;
  }

  /**
   * ctx.pool: leases go through it so the service knows which workers a job holds (a cancel interrupts them; the
   * receipt names them and their busy time). A lease's calls take the job's signal when they name none, and its
   * release() first asks the worker for its memory (the receipt's wasm_peak_bytes). A cancelled job's worker is
   * terminated instead of given back: Python may have stopped in place, but what the job left in the worker's memory
   * (its working files, OPFS files it mounted and holds open) is in no known state, and the next job must not inherit it.
   */
  #leaser(job, run) {
    const signal = run.controller.signal;
    return {
      size: this.#pool?.size,
      stats: () => this.#pool?.stats?.(),
      lease: async (opts = {}) => {
        if (signal.aborted) throw cancelledError();
        if (run.closed) throw cancelledError();
        const lease = await this.#pool.lease({ ...opts, signal: opts.signal || signal });
        if (run.closed) {
          // the job ended while it waited (a handler cut off by the cancel grace): nobody would give this one back
          lease.release();
          throw cancelledError();
        }
        run.leases.add(lease);
        let releasing = false;
        const release = () => {
          if (releasing || lease.released) return;
          releasing = true;
          run.usage.set(lease, lease.stats());
          if (signal.aborted) {
            lease.terminate();
            return;
          }
          run.probes.push((async () => {
            if (lease.alive && !lease.busy && !signal.aborted) {
              try {
                const m = await lease.call("memory", {}, { timeoutMs: 5000 });
                if (Number.isFinite(m?.wasm_bytes)) run.memory.push(m.wasm_bytes);
              } catch { /* a worker that cannot say: no reading */ }
            }
            lease.release();
          })());
        };
        const wrapped = {
          get id() { return lease.id; },
          get info() { return lease.info; },
          get packages() { return lease.packages; },
          get alive() { return lease.alive; },
          get busy() { return lease.busy; },
          get released() { return releasing || lease.released; },
          call: (op, payload, o = {}) => lease.call(op, payload, { ...o, signal: o.signal || signal }),
          interrupt: () => lease.interrupt(),
          release,
          terminate: () => lease.terminate(),
          stats: () => lease.stats(),
        };
        run.wrapped.set(lease, wrapped);
        return wrapped;
      },
    };
  }

  /**
   * Run one stage: status, times, seconds and the worker (fn's result {worker} or stageCtx.worker = id), written
   * after each change. With {reuse}, reuse(stageCtx) is asked first: true (or {ok: true}) marks the stage succeeded
   * and reused without running fn. fn's result may carry detail, worker, reused: true (it found its work already
   * done), verified: true (it is a verify stage that passed), compute (its kernel calls' facts, one or a list:
   * compute/science-scheduler.js; stageCtx.compute(facts) adds one as it runs) and checkpoint: {inputs_digest, outputs}
   * (written for the next attempt). The stage record keeps compute (stage.compute), which the receipt's evidence reads.
   */
  async #stage(job, run, id, fn, { reuse } = {}) {
    const signal = run.controller.signal;
    if (signal.aborted || run.closed) throw cancelledError();
    const sid = String(id);
    let s = job.stages.find((x) => x.id === sid);
    if (!s) {
      s = blankStage({ id: sid });
      job.stages.push(s);
    }
    Object.assign(s, { status: "running", started_at: this.#iso(), finished_at: null, seconds: null, worker: null, detail: null, reused: false });
    delete s.verified;
    delete s.record;
    delete s.compute;
    const t0 = this.#now();
    run.stage = sid;
    job.progress = { ...(job.progress || { fraction: 0 }), stage: sid, message: s.title?.en || sid };
    this.#touch(job);
    this.#emit(job, "stages");
    const end = (status, detail) => {
      if (run.closed) return; // the job already ended without this stage
      s.status = status;
      s.finished_at = this.#iso();
      s.seconds = round3(Math.max(0, this.#now() - t0) / 1000);
      if (detail !== undefined && detail !== null) s.detail = clip(detail, DETAIL_CHARS);
      this.#touch(job);
      this.#emit(job, "stages");
    };
    const sctx = {
      id: sid,
      signal,
      get worker() { return s.worker; },
      set worker(v) { s.worker = v ? String(v) : null; },
      record: (facts) => {
        if (run.closed || !facts || typeof facts !== "object") return;
        s.record = { ...(s.record || {}), ...jsonOnly(facts) };
        this.#touch(job);
      },
      /** one kernel call's compute facts (compute/science-scheduler.js), kept on the stage as it ran: a stage that fails
       * later still says where its calls computed */
      compute: (facts) => {
        if (run.closed) return;
        this.#addCompute(job, s, facts);
      },
      detail: (text) => {
        if (run.closed) return;
        s.detail = clip(text, DETAIL_CHARS);
        this.#touch(job);
      },
      progress: (fraction, message) => { if (!run.closed) this.#progress(job, run, fraction, message); },
      log: (level, text) => { if (!run.closed) this.#log(job, level, text); },
    };
    try {
      if (typeof reuse === "function") {
        const r = await reuse(sctx);
        if (r === true || r?.ok === true) {
          s.reused = true;
          end("succeeded", r?.detail || "reused: its outputs re-hash to the digests its checkpoint recorded");
          return { reused: true };
        }
        // a stage that never ran has no checkpoint: nothing to say about it
        const why = Array.isArray(r?.problems) ? r.problems.filter((p) => p !== "no checkpoint") : [];
        if (why.length) this.#log(job, "info", `${sid}: its checkpoint does not verify, so it runs again (${why.slice(0, 3).join("; ")})`);
      }
      const out = await fn(sctx);
      if (out && typeof out === "object") {
        if (out.worker) s.worker = String(out.worker);
        // the stage's kernel calls, when it returns them rather than adding each as it ran
        if (out.compute !== undefined && !run.closed) this.#addCompute(job, s, out.compute);
        if (out.reused === true) s.reused = true;
        if (out.verified === true) s.verified = true;
        // a stage that finished after its job ended (cut off by the cancel grace) leaves nothing for a later attempt
        if (out.checkpoint && !run.closed) await this.#checkpoints(job, run).write(sid, out.checkpoint);
      }
      end("succeeded", out?.detail);
      return out;
    } catch (err) {
      end(signal.aborted || isAbort(err) ? "cancelled" : "failed", err?.message || String(err));
      throw err;
    }
  }

  /** Compute facts (one call's, or a list) added to the stage's: only facts with a backend, as JSON. */
  #addCompute(job, s, facts) {
    const list = (Array.isArray(facts) ? facts : [facts]).filter((f) => isPlainObject(f) && typeof f.backend === "string");
    if (!list.length) return;
    s.compute = [...(Array.isArray(s.compute) ? s.compute : []), ...jsonOnly(list)].slice(-COMPUTE_KEEP);
    this.#touch(job);
  }

  /** Stage `id`'s compute facts marked discarded (ctx.discardCompute): kept, as what ran, but no longer used. */
  #discardCompute(job, id, reason) {
    const s = (job.stages || []).find((x) => x.id === String(id));
    if (!s || !Array.isArray(s.compute) || !s.compute.length) return;
    const why = clip(String(reason || "not used"), 300);
    s.compute = s.compute.map((f) => ({ ...f, discarded: why, accelerated: f.accelerated === true ? false : f.accelerated }));
    this.#touch(job);
  }

  #progress(job, run, fraction, message) {
    const f = Number(fraction);
    job.progress = { fraction: Number.isFinite(f) ? Math.max(0, Math.min(1, f)) : (job.progress?.fraction ?? 0), stage: run.stage, message: clip(message ?? "", 500) };
    this.#touch(job);
    this.#emit(job, "progress", job.progress);
  }

  #log(job, level, text) {
    const entry = this.#appendLog(job, level, text);
    this.#touch(job);
    this.#emit(job, "log", entry);
  }

  #appendLog(job, level, text) {
    const entry = { at: this.#iso(), level: LEVELS.has(level) ? level : "info", text: clip(text, LOG_LINE) };
    job.logs.push(entry);
    if (job.logs.length > LOG_KEEP) job.logs.splice(0, job.logs.length - LOG_KEEP);
    return entry;
  }

  /** Record an output: {name, path (segments under the job's directory, or a whole path in the store), bytes, sha256}. */
  #artefact(job, a) {
    if (!a || typeof a.name !== "string" || !a.name) throw typedError("bad_arguments", "an artefact needs a name");
    const path = Array.isArray(a.path) ? a.path.map(String) : String(a.path || a.name).split("/").filter(Boolean);
    const rec = { name: a.name, path, bytes: Number.isFinite(a.bytes) ? a.bytes : null, sha256: bareSha256(a.sha256) };
    if (!this.#inJob(job.id, path)) rec.shared = true;
    const i = job.artefacts.findIndex((x) => x.name === a.name);
    if (i >= 0) job.artefacts[i] = rec;
    else job.artefacts.push(rec);
    this.#touch(job);
    this.#emit(job, "artefact", { ...rec, path: [...rec.path] });
    return rec;
  }

  #forget(job, under) {
    const prefix = Array.isArray(under) ? under.map(String) : String(under || "").split("/").filter(Boolean);
    if (!prefix.length) return 0;
    const before = job.artefacts.length;
    job.artefacts = job.artefacts.filter((a) => !(a.path.length > prefix.length && prefix.every((seg, i) => a.path[i] === seg)));
    if (job.artefacts.length !== before) this.#touch(job);
    return before - job.artefacts.length;
  }

  /**
   * Record an input: job.inputs[name] = {sha256, bytes, source} (or job.inputs[group][name], as the runner's receipt
   * keeps a sample sheet's files under "<param>.files", each as "<column>:<line>"). sha256 is the digest of the bytes
   * the job read; source says where they came from (the project file's id and name).
   */
  #input(job, i) {
    if (!i || typeof i.name !== "string" || !i.name) throw typedError("bad_arguments", "an input needs a name");
    const rec = { sha256: bareSha256(i.sha256), bytes: Number.isFinite(i.bytes) ? i.bytes : null, source: i.source === undefined ? null : jsonOnly(i.source) };
    if (!isPlainObject(job.inputs)) job.inputs = {};
    if (typeof i.group === "string" && i.group) {
      if (!isPlainObject(job.inputs[i.group])) job.inputs[i.group] = {};
      job.inputs[i.group][i.name] = rec;
    } else {
      job.inputs[i.name] = rec;
    }
    this.#touch(job);
    return rec;
  }

  // --------------------------------------------------------------------------------------------------- reading

  async get(id) {
    await this.start();
    const live = this.#jobs.get(id);
    if (live) return this.#view(live);
    const rec = this.#db ? await this.#db.get(String(id)) : null;
    // a job left running by a tab that has closed since this one started is interrupted, not running for ever
    return rec ? this.#view(await this.#reclaim(rec)) : null;
  }

  /** The jobs of a project (or all), newest first, at most `limit`. */
  async list({ project_id, limit = 20 } = {}) {
    await this.start();
    if (!this.#db) return [];
    const recs = project_id === undefined || project_id === null ? await this.#db.all() : await this.#db.byProject(project_id);
    const byId = new Map();
    for (const r of recs) byId.set(r.id, await this.#reclaim(r));
    for (const j of this.#jobs.values()) if (project_id === undefined || project_id === null || j.project_id === project_id) byId.set(j.id, j);
    return [...byId.values()]
      .sort((a, b) => (a.created_at < b.created_at ? 1 : a.created_at > b.created_at ? -1 : 0))
      .slice(0, Math.max(0, Number(limit) || 0))
      .map((j) => this.#view(j));
  }

  /** fn({type: "state"|"log"|"progress"|"stages"|"artefact"|"done"|"error", job, data}) for the job → unsubscribe. */
  events(id, fn) {
    return this.#bus.on(`job:${id}`, fn);
  }

  /**
   * The job's artefacts that are in storage at the size the worker wrote: [{name, path, bytes, sha256, file}]; file is
   * what a person calls it, its path under the job's out/ ("run.json" for the artefact named "run"), else its name.
   */
  async files(id) {
    const job = await this.get(id);
    if (!job) throw typedError("not_found", `no job '${id}' in this browser`);
    if (!this.#store) return [];
    const out = [];
    for (const a of job.artefacts || []) {
      const st = await this.#store.stat(this.#resolve(id, a.path)).catch(() => null);
      if (!st || (Number.isFinite(a.bytes) && st.bytes !== a.bytes)) continue;
      out.push({ name: a.name, path: [...a.path], bytes: st.bytes, sha256: a.sha256, file: fileOf(a) });
    }
    return out;
  }

  /**
   * One of the job's files as a File: path in segments under the job's directory (["out", "x.tsv"]), an artefact's
   * name or file (files()), or a recorded artefact's own path. Nothing outside the job's directory is read but its
   * recorded artefacts.
   */
  async readFile(id, path) {
    const job = await this.get(id);
    if (!job) throw typedError("not_found", `no job '${id}' in this browser`);
    if (!this.#store) throw typedError("unavailable", "this browser has no private file system for job files");
    let segs = null;
    if (typeof path === "string") {
      const art = (job.artefacts || []).find((a) => a.name === path) || (job.artefacts || []).find((a) => fileOf(a) === path);
      segs = art ? art.path : path === JOB_RECEIPT_FILE ? ["out", JOB_RECEIPT_FILE] : path.split("/").filter(Boolean);
    } else if (Array.isArray(path)) {
      segs = path.map(String);
    }
    if (!segs || !segs.length) throw typedError("bad_arguments", "a file path is a list of names");
    const full = this.#resolve(id, segs);
    const allowed = this.#inJob(id, segs) || (job.artefacts || []).some((a) => pathText(this.#resolve(id, a.path)) === pathText(full));
    if (!allowed) throw typedError("bad_arguments", `${pathText(segs)} is not a file of job ${id}`);
    const file = await this.#store.readFile(full);
    if (!file) throw typedError("not_found", `${pathText(segs)} is not in this browser's storage`);
    return file;
  }

  /** The job receipt → {doc, sha256, matches} (matches: its bytes have the digest the job recorded), or null. */
  async receipt(id) {
    const job = await this.get(id);
    if (!job?.receipt?.sha256 || !this.#store) return null;
    const file = await this.#store.readFile([...jobDir(id), "out", job.receipt.path || JOB_RECEIPT_FILE]).catch(() => null);
    if (!file) return { doc: null, sha256: null, matches: false, missing: true };
    const bytes = new Uint8Array(await file.arrayBuffer());
    const sha256 = await sha256Hex(bytes);
    let doc = null;
    try {
      const d = JSON.parse(new TextDecoder().decode(bytes));
      doc = d && typeof d === "object" && d.schema === "tcmstudio.job-receipt/1" ? d : null;
    } catch { doc = null; }
    return { doc, sha256, matches: sha256 === job.receipt.sha256 };
  }

  // -------------------------------------------------------------------------------------------------- control

  /**
   * Cancel a job → its view. Queued here: cancelled at once. Running here: its signal aborts, the workers it leased
   * are interrupted (then terminated after their grace period, and terminated anyway when the job gives them back),
   * and it ends "cancelled" when the handler has stopped (or after cancelGraceMs without it). Interrupted: cancelled
   * (it will not be resumed). A job another live tab runs is that tab's to cancel; a finished one, or one whose
   * handler had already stopped when the cancel came, has nothing to cancel.
   */
  async cancel(id) {
    await this.start();
    // a job whose handler has stopped is ending as it ran: wait for its record, then answer from it below
    if (this.#runs.get(id)?.closed) await this.#runs.get(id).ended;
    const job = this.#jobs.get(id);
    const run = this.#runs.get(id);
    if (job && run) {
      if (job.state === "queued") {
        const i = this.#queue.indexOf(id);
        if (i >= 0) this.#queue.splice(i, 1);
        job.state = "cancelled";
        job.finished_at = this.#iso();
        this.#appendLog(job, "warn", "cancelled before it started");
        await this.#save(job).catch(() => {});
        const view = this.#view(job);
        this.#emit(job, "state");
        this.#emit(job, "done");
        run.lock?.release();
        this.#runs.delete(id);
        this.#jobs.delete(id);
        this.#writes.delete(id);
        run.finished();
        return view;
      }
      if (!run.controller?.signal.aborted) {
        this.#log(job, "warn", "cancel requested: stopping its workers");
        run.controller?.abort();
        for (const lease of run.leases) lease.interrupt();
        run.forceTimer = setTimeout(() => {
          if (run.closed) return;
          this.#log(job, "warn", `the job did not stop within ${Math.round(this.#cancelGraceMs / 1000)} s of the cancel; its workers were terminated`);
          run.force?.(cancelledError());
        }, this.#cancelGraceMs);
      }
      return this.#view(job);
    }
    const rec = this.#db ? await this.#db.get(String(id)) : null;
    if (!rec) throw typedError("not_found", `no job '${id}' in this browser`);
    if (rec.state === "queued" || rec.state === "running") {
      const held = await this.#heldLocks();
      if (held === null || held.has(lockName(id))) throw typedError("conflict", `job ${id} runs in another tab of this browser; cancel it there`);
      // an orphan this tab's start() did not see yet
    } else if (rec.state !== "interrupted") {
      throw typedError("conflict", `job ${id} already finished (${rec.state}); there is nothing to cancel`);
    }
    rec.state = "cancelled";
    rec.progress = null;
    rec.finished_at = rec.finished_at || this.#iso();
    this.#appendLog(rec, "warn", "cancelled: it will not be resumed");
    await this.#db.put(rec);
    this.#emit(rec, "state");
    this.#emit(rec, "done");
    return this.#view(rec);
  }

  /** Queue an interrupted or failed job again → its view (attempts + 1). Its handler may reuse finished stages. */
  async resume(id) {
    // one resume of a job at a time in this tab, whether or not Web Locks can tell two apart
    if (this.#resuming.has(id)) throw typedError("conflict", `job ${id} is already being resumed`);
    this.#resuming.add(id);
    try {
      return await this.#resume(id);
    } finally {
      this.#resuming.delete(id);
    }
  }

  async #resume(id) {
    await this.start();
    if (await this.#busyHere(id)) throw typedError("conflict", `job ${id} is already queued or running`);
    const found = this.#db ? await this.#db.get(String(id)) : null;
    if (!found) throw typedError("not_found", `no job '${id}' in this browser`);
    // left running by a tab that has closed: interrupted first, then resumed like any interrupted job
    const rec = await this.#reclaim(found);
    if (rec.state !== "interrupted" && rec.state !== "failed") {
      throw typedError("conflict", `only an interrupted or failed job can be resumed; ${id} is ${rec.state}`);
    }
    const handler = this.#handlers.get(rec.kind);
    if (!handler) throw refusal("unavailable", `this browser does not run job kind '${rec.kind}'`, "job_kind", "Connect the local runner.");
    const lock = await this.#hold(id);
    if (!lock) throw typedError("conflict", `job ${id} is being resumed in another tab`);
    try {
      // what it needs may have gone since (its snapshots removed, storage full): refused now, it stays as it was
      if (typeof handler.preflight === "function") await handler.preflight(rec.params || {}, this, { project_id: rec.project_id ?? null });
    } catch (err) {
      lock.release();
      const e = err instanceof Error ? err : new Error(String(err));
      if (!e.type) e.type = "unavailable";
      throw e;
    }
    const job = {
      ...rec, state: "queued", attempts: (Number(rec.attempts) || 1) + 1, error: null, started_at: null, finished_at: null,
      progress: null, outcome: null, result: null, receipt: null,
      // the stages run again in order; each may be reused when its checkpoint verifies
      stages: ((handler.stages || handler.stagesFor) ? stagesOf(handler, rec.params) : rec.stages || []).map(blankStage),
      // kept: a reused stage does not write its artefacts again, and every one is read back at the end
      artefacts: [...(rec.artefacts || [])],
      // the next attempt stages its inputs again and records them as it finds them
      inputs: {},
      device: "cpu", device_note: DEVICE_NOTE,
    };
    delete job.provenance; // the next attempt's own
    delete job.limitations; // the next attempt's own, if it finds any
    this.#appendLog(job, "info", `resumed (attempt ${job.attempts})`);
    this.#jobs.set(id, job);
    try {
      await this.#save(job);
    } catch (err) {
      // not recorded as queued: it stays as it was, and its lock is not kept for a job that will not run
      this.#jobs.delete(id);
      this.#writes.delete(id);
      lock.release();
      throw refusal("unavailable", `the job could not be recorded: ${err?.message || err}`, "storage");
    }
    this.#enqueue(job, lock);
    const view = this.#view(job);
    this.#emit(job, "state");
    return view;
  }

  /** Delete a job's record and its files (not one that is queued or running) → whether it existed. */
  async remove(id) {
    await this.start();
    if (await this.#busyHere(id)) throw typedError("conflict", `job ${id} is queued or running; cancel it first`);
    const rec = this.#db ? await this.#db.get(String(id)) : null;
    if (!rec) return false;
    if (rec.state === "queued" || rec.state === "running") {
      const held = await this.#heldLocks();
      if (held === null || held.has(lockName(id))) throw typedError("conflict", `job ${id} runs in another tab of this browser`);
    }
    // the files first: a record without its files is a lie, files without a record are only space
    if (this.#store) await this.#store.remove(jobDir(id), { recursive: true });
    await this.#db.delete(id);
    return true;
  }

  // -------------------------------------------------------------------------------------------------- internal

  #iso() {
    const d = this.#now();
    return (d instanceof Date ? d : new Date(d)).toISOString();
  }

  /** Is `path` (segments) inside the job's directory? A relative path always is. */
  #inJob(id, path) {
    const full = this.#resolve(id, path);
    const dir = jobDir(id);
    return full.length > dir.length && dir.every((seg, i) => full[i] === seg);
  }

  /** Segments under the job's directory, or a whole path when it starts at the store's base ("tcmstudio"). */
  #resolve(id, path) {
    const segs = Array.isArray(path) ? path.map(String) : String(path || "").split("/").filter(Boolean);
    const base = this.#store?.base?.[0] ?? "tcmstudio";
    return segs[0] === base ? segs : [...jobDir(id), ...segs];
  }

  /** The default hashFile: the file streamed from the store through data/digest.js (no whole copy in memory). */
  async #digestFromStore(path, algorithm) {
    const file = await this.#store?.readFile(path);
    if (!file) return null;
    const { digestFile } = await import("../data/digest.js");
    return digestFile(file, algorithm);
  }

  #view(job) {
    return copy(job);
  }

  #emit(job, type, data) {
    this.#bus.emit(`job:${job.id}`, { type, job: this.#view(job), data: data === undefined ? null : data });
  }

  /** Write the record, coalesced: a write not started yet takes every change made before it starts. */
  #save(job) {
    if (!this.#db) return Promise.resolve();
    let w = this.#writes.get(job.id);
    if (!w) this.#writes.set(job.id, (w = { tail: Promise.resolve(), next: null }));
    if (w.next) return w.next;
    const p = w.tail.then(() => {
      w.next = null; // from here on, a change needs a write of its own
      return this.#db.put(copy(job));
    });
    w.next = p;
    w.tail = p.catch(() => {});
    return p;
  }

  #touch(job) {
    this.#save(job).catch((err) => { this.persistError = err; });
  }
}

/** An artefact's file as a person calls it: its path under out/ when it is there, else its name. */
function fileOf(a) {
  const path = Array.isArray(a?.path) ? a.path : [];
  return path.length > 1 && path[0] === "out" ? path.slice(1).join("/") : String(a?.name || "");
}

/**
 * The envelope a job call returns when the service took it (spec §11): status "job_submitted" while the job is queued
 * or running — failed or cancelled when it ended at once — with job {id, kind, state, where: "browser"}. Its receipt
 * is the page's (core/receipt.js) with where "browser" and runtime "studio-jobs/1": nothing has been computed yet, so
 * its outcome is the job's own state (queued | running), as the runner's job_submitted envelopes say (its result
 * kind and assurance come only with a result); the job's own receipt (out/job_receipt.json) says what ran.
 */
export function envelopeFor(job, { tool = "job", input_sha256 = null } = {}) {
  const j = job || {};
  const state = j.state || "queued";
  const status = state === "failed" || state === "interrupted" ? "failed" : state === "cancelled" ? "cancelled" : "job_submitted";
  const via = j.kind ? `job.${j.kind}` : String(tool);
  const titleZh = j.title?.zh || j.kind || "";
  const titleEn = j.title?.en || j.kind || "";
  const view = { id: j.id ?? null, kind: j.kind ?? null, state, where: "browser" };
  const outcome = status === "job_submitted" ? (state === "running" ? "running" : "queued") : status === "cancelled" ? "cancelled" : "failed";
  const STATE_ZH = { queued: "排队中", running: "运行中", succeeded: "已完成", failed: "失败", cancelled: "已取消", interrupted: "已中断" };
  const head = `${tool} → ${via}: ${status}`;
  const body = status === "job_submitted"
    ? `Job ${j.id} submitted (${titleEn}) to this browser's job service: ${state}; no result until it has finished and its outputs have been verified. Follow it with job_status {"job_id": "${j.id}"}.`
    : `Job ${j.id} (${titleEn}) ${state} at once: ${j.error?.message || "no reason given"}. There is no result.`;
  const errorType = ["bad_arguments", "unavailable", "not_found", "refused", "runtime_error", "timeout", "network_off"].includes(j.error?.type) ? j.error.type : "runtime_error";
  return {
    ok: status === "job_submitted",
    tool,
    via,
    status,
    duration_ms: 0,
    summary: status === "job_submitted"
      ? `已提交任务 ${j.id}（${titleZh}，在本浏览器中运行）：${STATE_ZH[state] || state}；完成并核验前没有结果`
      : `任务 ${j.id}（${titleZh}）${STATE_ZH[state] || state}`,
    text: `${head}\n${body}`,
    result: {
      id: view.id, kind: view.kind, state, where: "browser", title: j.title ?? null, project_id: j.project_id ?? null,
      submission_id: j.submission_id ?? null, created_at: j.created_at ?? null, params: j.params ?? null,
      stages: (j.stages || []).map((s) => ({ id: s.id, title: s.title, status: s.status })),
    },
    citations: [],
    governance: { kind: "job", released: null, artifact: null, verdict: null, claims: [], evidence: [], refusals: [], labels: [], licences: [], limitations: [LIMIT_PENDING], outputs: [] },
    receipt: baseReceipt({ where: "browser", runtime: JOBS_RUNTIME, device: "cpu", versions: {}, input_sha256, started_at: j.created_at, outcome }),
    job: view,
    approval: null,
    error: status === "job_submitted" ? null : { type: errorType, message: j.error?.message || `the job ${state}`, hint: j.error?.hint || "" },
  };
}
