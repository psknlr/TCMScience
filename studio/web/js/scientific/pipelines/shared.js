// What the browser job handlers (./network-pharmacology.js, ./omics.js) do the same way: a refusal before anything is
// queued, a worker call's result unwrapped, the storage check, a run's limitations.md as the job carries it, and
// Python's progress and log events put on the job.

/** A refusal before anything is queued (the job service's spec §10): type "unavailable" | "bad_arguments", need, hint. */
export function refusal(type, message, need, hint = "") {
  const e = new Error(message);
  e.type = type;
  e.need = need;
  e.hint = hint;
  return e;
}

/** The job's working directory in the worker's memory. */
export function work(job) {
  return `/work/${job.id}`;
}

/** What a stage worker call returned, or a thrown error carrying Python's own type and message. */
export function unwrap(text, what) {
  const doc = typeof text === "string" ? JSON.parse(text) : text;
  if (doc && doc.ok === false) {
    const e = new Error(`${what}: ${doc.error?.message || "failed"}`);
    e.type = doc.error?.type || "runtime_error";
    throw e;
  }
  return doc && "result" in doc ? doc.result : doc;
}

/** A refusal when the browser's free storage is below needBytes (the reviewed sizes known); unsized: the files whose
 * size nobody reviewed, which this check cannot count and says so. */
export async function storageRefusal(store, needBytes, unsized = []) {
  if (!store) return refusal("unavailable", "This browser has no Origin Private File System for the job's files.", "storage", "Use a current Chromium-based browser such as Chrome or Edge, outside a private window (Firefox and Safari have not been tested with these jobs).");
  if (!needBytes) return null;
  const est = await store.estimate?.().catch(() => null);
  if (est && Number.isFinite(est.quota) && Number.isFinite(est.usage) && est.quota - est.usage < needBytes) {
    const more = unsized.length ? `, and more for ${unsized.length} file${unsized.length > 1 ? "s" : ""} with no reviewed size (${unsized.join(", ")})` : "";
    return refusal("unavailable", `The job needs at least ${(needBytes / 1e6).toFixed(0)} MB of browser storage${more}; ${((est.quota - est.usage) / 1e6).toFixed(0)} MB is free.`, "storage", "Free some space, or allow the site to store more.");
  }
  return null;
}

// a run's limitations.md as the job carries it, as the runner's job service does (tcmstudio.jobs): at most this many
// lines, each at most this long
const LIMITATION_LINES = 100;
const LIMITATION_CHARS = 1000;

/** The non-empty lines of a limitations.md, held to the runner's bounds. */
export function limitationLines(text) {
  const lines = String(text || "").split(/\r?\n/).map((l) => l.trim()).filter(Boolean);
  const out = lines.slice(0, LIMITATION_LINES).map((l) => (l.length <= LIMITATION_CHARS ? l : `${l.slice(0, LIMITATION_CHARS - 1)}…`));
  if (lines.length > LIMITATION_LINES) out.push(`… and ${lines.length - LIMITATION_LINES} more in limitations.md`);
  return out;
}

/** Python's events on the job: log lines as they are, progress mapped into [base, base + span] of the job's. */
export function onPythonEvent(ctx, ev, base, span) {
  if (!ev) return;
  if (ev.kind === "log" && ev.text) ctx.log(ev.level || "info", String(ev.text));
  else if (ev.kind === "progress") ctx.progress(base + span * Math.max(0, Math.min(1, Number(ev.fraction) || 0)), ev.message || "");
}
