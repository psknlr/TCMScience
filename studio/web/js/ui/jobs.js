// Following jobs (CONTRACTS §4): the thread's job cards, Settings → Compute's job list and Settings → Data's
// acquisitions all use one JobTracker per view. A job is the runner's, or this browser's own (an id starting "bj-":
// studio/web/js/scientific, BROWSER_JOBS.md), followed the same way through the page's job service. A job is pending until the runner has collected and verified it:
// while it runs, its card shows the stage (in words), the progress, the files it has written so far by name and size
// only (the runner's 'artefact' events: not results, never opened), and its live log; once it ends, the file list
// (succeeded only), and the runner's own record of the run (out/job_receipt.json: device, duration, peak memory,
// engines), read once and used only at the digest the job recorded for it (job.receipt.sha256).
//
// A list (Settings → Compute) hands over each job's view as the runner gave it: one already ended is shown from that
// view as it is, never fetched again, and its files and run record are not read (its card links the record); only a
// queued or running one is followed. A job a thread or an acquisition shows is followed, and read once it ends.

import { t } from "../core/i18n.js";
import { sha256Hex } from "../core/util.js";
import { fill, h } from "./dom.js";
import { icon } from "./icons.js";
import { toast } from "./overlay.js";
import { jobCard } from "./toolcards.js";

/** The most log lines the runner replays when a job's event stream (re)connects. */
const LOG_BACKLOG = 80;
const ENDED = ["succeeded", "failed", "cancelled", "interrupted"];
/** A browser job's id (the runner's never start so). */
const BROWSER_JOB = /^bj-/;
/** The most written-so-far files a card keeps (the runner announces at most 200 per poll). */
const PARTIAL_MAX = 500;

/**
 * Lines shown + lines replayed after a reconnect: the replay is the tail of the same log, so it overlaps the end of
 * what is shown; only what follows the overlap is new.
 */
export function mergeLogs(shown, replay) {
  const max = Math.min(shown.length, replay.length);
  for (let k = max; k > 0; k--) {
    let same = true;
    for (let i = 0; i < k; i++) if (shown[shown.length - k + i] !== replay[i]) { same = false; break; }
    if (same) return [...shown, ...replay.slice(k)];
  }
  // no overlap: nothing was shown yet, or more than the backlog was missed while disconnected
  return [...shown, ...replay];
}

function endReplay(rec) {
  if (!rec.replay) return;
  clearTimeout(rec.replayTimer);
  rec.logs = mergeLogs(rec.logs, rec.replay);
  rec.replay = null;
}

/**
 * Follows runner jobs that a view shows (events when connected); a job is pending until collected.
 * opts: {onOpenFile(job, path)} (the thread opens a collected file in the inspector; elsewhere files are links).
 */
export class JobTracker {
  constructor(app, { onOpenFile } = {}) {
    this.app = app;
    this.onOpenFile = onOpenFile || null;
    this.jobs = new Map(); // id → {job, files, logs, partial, receipt, close, fetched}
    this.slots = new Map(); // id → Set of slot elements
  }

  /**
   * A slot that shows the job's card and is re-filled whenever the job changes. fresh: the stub is a Job the runner
   * just listed (Settings), newer than what may be held: it is taken over, and followed again while it is not ended;
   * one that has ended is the runner's own view already, shown as it is (no request). The thread's stubs ({id, kind,
   * state} from an envelope) are older than what the runner says, and never are.
   */
  node(stub, { fresh = false } = {}) {
    let rec = this.jobs.get(stub.id);
    if (!rec) {
      const listed = fresh && ENDED.includes(stub.state);
      rec = { job: { ...stub }, files: null, logs: [], partial: [], receipt: null, close: null, fetched: listed };
      this.jobs.set(stub.id, rec);
      if (!listed) this.#follow(stub.id);
    } else if (fresh && stub.state) {
      rec.job = { ...rec.job, ...stub };
      if (!ENDED.includes(rec.job.state) && !rec.close) this.#follow(stub.id);
    }
    const slot = h("div.job-slot", { "data-job": stub.id });
    if (!this.slots.has(stub.id)) this.slots.set(stub.id, new Set());
    this.slots.get(stub.id).add(slot);
    this.#fill(slot, rec);
    return slot;
  }

  /** The job as last seen (or null). */
  get(id) {
    return this.jobs.get(id)?.job || null;
  }

  /** Where a job lives: the runner, or this browser's job service (whose jobs need no connection). */
  #source(job) {
    if (BROWSER_JOB.test(String(job?.id || "")) || job?.where === "browser") {
      const jobs = this.app.runtimes.jobs;
      return { browser: true, api: jobs?.service ? browserJobsApi(jobs.service) : null, connected: jobs?.status === "ready" };
    }
    return { browser: false, api: this.app.runtimes.runner?.jobs ? this.app.runtimes.runner.jobs : null, runner: this.app.runtimes.runner, connected: this.app.state.runner.status === "ready" };
  }

  #fill(slot, rec) {
    const src = this.#source(rec.job);
    if (src.browser) return this.#fillBrowser(slot, rec, src);
    const runner = this.app.runtimes.runner;
    const connected = this.app.state.runner.status === "ready";
    const card = jobCard(rec.job, {
      files: rec.files, logs: rec.logs, partial: rec.partial, jobReceipt: rec.receipt,
      onCancel: connected ? async (job) => {
        try { rec.job = { ...rec.job, ...(await runner.jobs.cancel(job.id)) }; this.#changed(job.id); } catch (err) { toast(err?.message || String(err), { tone: "warn" }); }
      } : null,
      onOpenFile: this.onOpenFile ? (path) => this.onOpenFile(rec.job, path) : null,
      // the runner's own records of the run (job_receipt.json, run_manifest.json): opened from the runner itself
      fileHref: connected && typeof runner?.fileUrl === "function" ? (path) => runner.fileUrl(rec.job.id, path) : null,
    });
    if (!connected && !ENDED.includes(rec.job.state)) {
      card.append(h("p.job__pending", icon("plug", { size: 12 }), t("ui.job.connect_to_follow")));
    }
    fill(slot, card);
  }

  /** A browser job's card: cancel while it runs, resume once interrupted, its files opened from this browser. */
  #fillBrowser(slot, rec, src) {
    const service = this.app.runtimes.jobs?.service;
    const view = browserJobView(rec.job);
    const card = jobCard(view, {
      files: rec.files, logs: rec.logs, partial: rec.partial, jobReceipt: rec.receipt,
      onCancel: src.connected ? async (job) => {
        try { rec.job = { ...rec.job, ...(await service.cancel(job.id)) }; this.#changed(job.id); } catch (err) { toast(err?.message || String(err), { tone: "warn" }); }
      } : null,
      onResume: src.connected && rec.job.state === "interrupted" ? async (job) => {
        try {
          rec.job = { ...rec.job, ...(await service.resume(job.id)) };
          this.#changed(job.id);
          if (!rec.close) this.#follow(job.id);
        } catch (err) { toast(err?.message || String(err), { tone: "warn" }); }
      } : null,
      onOpenFile: this.onOpenFile ? (path) => this.onOpenFile(rec.job, path) : null,
      fileHref: null,
    });
    if (!src.connected && !ENDED.includes(rec.job.state)) card.append(h("p.job__pending", icon("plug", { size: 12 }), t("ui.job.browser_unavailable")));
    fill(slot, card);
  }

  /** Re-fill the job's cards on the page (a slot no longer on the page is dropped). */
  #refill(id) {
    const rec = this.jobs.get(id);
    for (const slot of [...(this.slots.get(id) || [])]) {
      if (!slot.isConnected) { this.slots.get(id).delete(slot); continue; }
      this.#fill(slot, rec);
    }
  }

  /** The job changed: its cards, and the app's "job" event (a run index entry and telemetry once it has ended). */
  #changed(id) {
    this.#refill(id);
    this.app.emit("job", this.jobs.get(id).job);
  }

  /** The files of a succeeded job, and the job receipt of any job that started (once each). */
  async #ended(id, rec) {
    const src = this.#source(rec.job);
    if (src.browser) {
      const service = this.app.runtimes.jobs?.service;
      if (!service) return;
      if (rec.job.state === "succeeded" && !rec.files) {
        try { rec.files = (await service.files(id)).map((f) => ({ path: f.name, bytes: f.bytes, sha256: f.sha256 })); } catch { /* listed later */ }
      }
      if (!rec.receipt && rec.job.receipt?.sha256) {
        try {
          const r = await service.receipt(id);
          rec.receipt = r?.matches ? r.doc : r ? { changed: true } : null;
        } catch { rec.receipt = null; }
      }
      return;
    }
    const runner = this.app.runtimes.runner;
    if (rec.job.state === "succeeded" && !rec.files) {
      try { rec.files = (await runner.jobs.files(id))?.files || null; } catch { /* listed later */ }
    }
    if (!rec.receipt && rec.job.receipt?.path) rec.receipt = await readJobReceipt(runner, id, rec.job.receipt.path, rec.job.receipt.sha256);
  }

  async #follow(id) {
    const rec = this.jobs.get(id);
    const src = this.#source(rec.job);
    if (!src.api || !src.connected) return;
    const runner = { jobs: src.api };
    try {
      rec.job = { ...rec.job, ...(await runner.jobs.get(id)) };
      rec.fetched = true;
      if (ENDED.includes(rec.job.state)) await this.#ended(id, rec);
      this.#changed(id);
    } catch { return; }
    if (ENDED.includes(rec.job.state) || rec.close) return;
    rec.close = runner.jobs.events(id, async (ev) => {
      const data = ev?.data || {};
      if (ev.type === "error") {
        // the stream gave up (runner gone, token changed): forget it, so refresh() or the next "ready" follows again
        rec.close = null;
        rec.fetched = false;
        return;
      }
      // every (re)connection of the stream (EventSource reconnects by itself) starts with the job's state and then
      // replays up to 80 recent log lines: those are merged with the lines already shown, not appended twice
      if (ev.type === "log") {
        const line = typeof data === "string" ? data : data.line || JSON.stringify(data);
        if (rec.replay) {
          rec.replay.push(line);
          clearTimeout(rec.replayTimer);
          if (rec.replay.length >= LOG_BACKLOG) endReplay(rec);
          else rec.replayTimer = setTimeout(() => { endReplay(rec); this.#changed(id); }, 250);
        } else rec.logs.push(line);
      } else endReplay(rec);
      if (ev.type === "state") { rec.replay = []; rec.replayTimer = setTimeout(() => endReplay(rec), 250); }
      if (ev.type === "state" || ev.type === "done") rec.job = { ...rec.job, ...(data.job || data) };
      else if (ev.type === "progress") rec.job = { ...rec.job, progress: data };
      else if (ev.type === "stages" && Array.isArray(data.stages)) rec.job = { ...rec.job, stages: data.stages };
      else if (ev.type === "artefact" && data && typeof data.path === "string") {
        // written so far: a name and a size, not a result (the card lists them only until the job is collected)
        if (!rec.partial.some((f) => f.path === data.path)) rec.partial = [...rec.partial, { path: data.path, bytes: data.bytes }].slice(-PARTIAL_MAX);
      }
      if (ev.type === "done" || ENDED.includes(rec.job.state)) {
        rec.close?.();
        rec.close = null;
        if (ENDED.includes(rec.job.state)) await this.#ended(id, rec);
      }
      this.#changed(id);
    });
  }

  /**
   * After the runner (re)connected: follow again what is not followed, and re-draw the rest (whether the runner is
   * connected changes a card). Re-drawing is not a change of the job: no "job" event, so a job a list showed already
   * ended is never taken for one this page saw end.
   */
  refresh() {
    for (const [id, rec] of this.jobs) {
      if (!rec.fetched || (!rec.close && !ENDED.includes(rec.job.state))) this.#follow(id);
      else this.#refill(id);
    }
  }

  close() {
    for (const rec of this.jobs.values()) rec.close?.();
    this.jobs.clear();
    this.slots.clear();
  }
}

/**
 * out/job_receipt.json of a job, read from the runner (GET /api/jobs/{id}/files/{path}), or null. It is the runner's
 * record of the run (schema tcmstudio.job-receipt/1): the device it assigned, the duration, the peak memory of the
 * largest process, the engines. As the runner's own reader (jobs.py) does, it is used only at the digest the job
 * recorded for it (sha256, the Job's receipt.sha256): bytes with another digest → {changed: true} (the card says so and
 * shows nothing from them); no recorded digest, or no Web Crypto here to compute one → null (nothing shown). Only an
 * object of that schema is kept.
 */
export async function readJobReceipt(runner, id, path = "job_receipt.json", sha256 = null) {
  try {
    if (typeof runner?.fileUrl !== "function") return null;
    const want = typeof sha256 === "string" ? sha256.replace(/^sha256:/, "").toLowerCase() : "";
    if (!/^[0-9a-f]{64}$/.test(want)) return null;
    const fetchImpl = typeof runner._fetch === "function" ? runner._fetch : globalThis.fetch;
    const res = await fetchImpl(runner.fileUrl(id, path), { headers: { Accept: "application/json" } });
    if (!res?.ok) return null;
    const bytes = new Uint8Array(await res.arrayBuffer());
    if ((await sha256Hex(bytes)) !== want) return { changed: true };
    const doc = JSON.parse(new TextDecoder().decode(bytes));
    return doc && typeof doc === "object" && doc.schema === "tcmstudio.job-receipt/1" ? doc : null;
  } catch { return null; }
}

/**
 * The browser job service (scientific/runtime/job-manager.js) with the runner client's job calls: get, events (the
 * runner's event shapes: state and done carry the job, log a line, progress the progress, stages the stages, artefact a
 * file written), cancel and files.
 */
export function browserJobsApi(service) {
  return {
    get: async (id) => {
      const job = await service.get(id);
      if (!job) throw new Error(`no job ${id} in this browser`);
      return job;
    },
    events: (id, fn) => service.events(id, (ev) => {
      const job = ev?.job || null;
      const data = ev?.data;
      if (ev?.type === "log") fn({ type: "log", data: { line: typeof data === "string" ? data : data?.text ?? JSON.stringify(data) } });
      else if (ev?.type === "progress") fn({ type: "progress", data: data || job?.progress || null });
      else if (ev?.type === "stages") fn({ type: "stages", data: { stages: browserJobView(job || {}).stages || [] } });
      else if (ev?.type === "artefact") fn({ type: "artefact", data: { path: data?.name || (data?.path || []).join?.("/"), bytes: data?.bytes } });
      else if (ev?.type === "error") fn({ type: "state", data: job || {} });
      else fn({ type: ev?.type || "state", data: job || data || {} });
    }),
    cancel: (id) => service.cancel(id),
    files: async (id) => ({ files: (await service.files(id)).map((f) => ({ path: f.name, bytes: f.bytes, sha256: f.sha256 })) }),
  };
}

/** A browser job as the job card reads a job: its stages named and titled, its error as the outcome's. */
export function browserJobView(job) {
  const stages = Array.isArray(job?.stages) ? job.stages.map((x) => ({
    name: x.id, title: x.title, status: x.status === "succeeded" ? "succeeded" : x.status,
    seconds: x.seconds, device: x.worker ? "cpu" : undefined, reused: x.reused,
  })) : job?.stages;
  const outcome = job?.state === "failed" || job?.state === "interrupted"
    ? { ...(job.outcome || {}), error: job.error?.message || job.outcome?.error || null }
    : job?.outcome || null;
  return { ...job, stages, outcome, device: job?.device || "cpu" };
}
