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
    // what it would need (Python says "memory" when a worker ran out of it, "runner" for what only the runner runs)
    if (typeof doc.error?.need === "string" && doc.error.need) e.need = doc.error.need;
    if (typeof doc.error?.hint === "string" && doc.error.hint) e.hint = doc.error.hint;
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

// ------------------------------------------------------------------------------------------- project files, workers
// (what pipelines/admet.js needs as omics.js does it; omics.js keeps its own copies, with the scRNA directory note)

/** A name a job's directory can hold: the file's own name where it can (Python reads a format from its suffix), else
 * with the characters a path segment cannot have replaced. */
export function safeName(name) {
  const s = String(name || "").replace(/[/\\\0]/g, "_").trim();
  return !s || s === "." || s === ".." ? "input" : s;
}

/**
 * The project file a parameter names (its id, or its name: the newest file of that name in the project) → {id, name,
 * bytes, sha256, blob}, or null when no file has that name. Refused (type "unavailable", need "storage") when the page
 * gives no project files or they cannot be read, or the file has no bytes or no recorded SHA-256.
 */
export async function projectFile(projectFiles, projectId, param, ref) {
  if (!projectFiles) {
    throw refusal("unavailable", "This page gives the job service no project files to read the inputs from.", "storage",
      "Open Studio itself (the page that holds the project), or connect the local runner.");
  }
  let rec = null;
  try {
    rec = await projectFiles.find(projectId ?? null, String(ref).trim());
  } catch (err) {
    throw refusal("unavailable", `${param}: the project's files could not be read (${err?.message || err})`, "storage");
  }
  if (!rec) return null;
  if (!rec.blob || typeof rec.blob.slice !== "function" || !/^(sha256:)?[0-9a-f]{64}$/i.test(String(rec.sha256 || "").trim())) {
    throw refusal("unavailable", `${param}: ${rec.name} has no bytes or no recorded SHA-256 in this browser`, "storage", "Attach the file to the project again.");
  }
  return rec;
}

/** The integrity error of a file whose bytes here are not the ones its record names. */
export function digestMismatch(name, got, want, hint = "Attach the file to the project again.") {
  const e = new Error(`${name}: its bytes in this browser have SHA-256 ${String(got).slice(0, 16)}…, not the ${String(want).replace(/^sha256:/, "").slice(0, 16)}… recorded for it; it was not used`);
  e.type = "integrity";
  e.hint = hint;
  return e;
}

// wasm32 holds a job worker to about 4 GB. Running out is said as what it is, a refusal the runner answers: Python's
// MemoryError (numpy's _ArrayMemoryError among them), or the worker's own allocation failing (Emscripten's OOM abort,
// a Memory.grow past the limit, which Pyodide reports as a fatal error)
const OUT_OF_MEMORY = /\bMemoryError\b|_ArrayMemoryError|\bOOM\b|out of memory|Cannot enlarge memory|Maximum memory size exceeded|Memory\.grow/i;
export const MEMORY_HINT = "Connect the local runner: it runs the same analysis natively, without the browser's memory limit.";

/** The typed refusal (type "refused", need "memory") for a worker call that ran out of memory, or null. */
export function memoryRefusal(err, what) {
  const message = String(err?.message || "");
  if (err?.type === "refused" && err.need === "memory") {
    if (!err.hint) err.hint = MEMORY_HINT;
    return err;
  }
  if (!err || err.type === "refused" || !OUT_OF_MEMORY.test(message)) return null;
  const e = new Error(`${what} ran out of memory: a browser job worker holds at most about 4 GB (wasm32); run it on the runner (${message.slice(0, 300)})`);
  e.type = "refused";
  e.need = "memory";
  e.hint = MEMORY_HINT;
  return e;
}

/** A Python function in the stage's worker → its result; running out of memory is a refusal, not a runtime error. */
export async function pythonCall(ctx, lease, fn, args, [base, span], what) {
  try {
    return unwrap(await lease.call("run", { fn, args }, { signal: ctx.signal, onEvent: (ev) => onPythonEvent(ctx, ev, base, span) }), fn);
  } catch (err) {
    throw memoryRefusal(err, what) || err;
  }
}
