// The telemetry contract between the page and the Worker (studio/docs/TELEMETRY.md): the consent notice's version, the
// usage events and the conversation record, each reduced to known fields within known bounds. The page builds what it
// sends through these functions, and the Worker (studio/edge/src/telemetry/) reads what it receives through the same
// ones, never trusting the page: whatever a field does not allow is dropped, never passed on. Dependency-free on
// purpose, so both run it unchanged (the Worker imports this file).
//
// Three kinds of data, kept apart all the way to storage:
//   usage events          counts and timings, no text a visitor wrote, no identifier that outlives a day (always
//                         optional: the visitor may switch them off, and Do Not Track / Global Privacy Control do)
//   conversation records  one finished turn with its question and answer, only after the visitor agreed to the
//                         notice of CONSENT_VERSION, and never for a conversation that touches uploaded files, project
//                         files, a clinical tool or identifying text (see web/js/core/turnrecord.js, sensitive.js)
//   security events       written by the Worker alone, from what it sees of requests (studio/edge/src/telemetry/security.js)

/** The notice the visitor agreed to. A change of what is recorded, or how, is a new version: the page asks again, and
 * the Worker refuses records made under an older one. */
export const CONSENT_VERSION = 1;
export const RECORD_VERSION = 1;
export const EVENTS_VERSION = 1;

export const LIMITS = Object.freeze({
  question: 4000, // characters of the question kept in a record
  answer: 12000, // characters of the answer kept in a record
  recordBytes: 60000, // a record as sent (the page shortens the answer to fit; keepalive requests allow 64 KiB)
  tools: 40, // tool runs listed in a record
  argKeys: 12, // argument names listed per tool run (never their values)
  events: 50, // events in one batch
  batchBytes: 30000,
});

export const ROUTES = ["home", "project", "conversation", "settings", "about", "catalog", "notfound", "admin"];
export const LAYOUTS = ["wide", "medium", "narrow", "mobile"];
export const LANGS = ["zh", "en"];
export const BROWSERS = ["chromium", "firefox", "safari", "other"];
export const TURN_STATUSES = ["completed", "cancelled", "failed", "interrupted"];
export const TOOL_STATUSES = ["succeeded", "failed", "refused", "cancelled", "needs_approval", "job_submitted", "running", "unknown"];
export const TOOL_KINDS = ["native", "skill", "connector", "clinic", "tcmdb", "study", "job", "system", "unknown"];
export const PLACES = ["browser", "runner", "gateway", "page", "unknown"];
export const WORKFLOW_KINDS = ["skill", "study", "job", "tcmdb"];
export const WORKFLOW_PHASES = ["started", "submitted", "completed", "failed", "refused", "cancelled"];
export const SERVICES = ["relay", "runner", "browser_runtime", "storage"];
export const ERROR_CATEGORIES = ["js_error", "unhandled_rejection", "model", "network", "runtime", "storage"];
export const ERROR_KINDS = ["model", "network", "runtime"];
export const CONSENT_DECISIONS = ["granted", "declined", "withdrawn", "deleted"];
/** Why a turn's content was not recorded (the turn is still counted, without its words). */
export const EXCLUSIONS = ["no_consent", "uploaded_file", "project_files", "clinical_tool", "user_data_tool", "sensitive_text", "discarded", "unavailable"];

export const EVENT_TYPES = ["app_init", "visit", "pageview", "turn_start", "turn_end", "tool_end", "workflow", "error", "availability", "consent"];

// ------------------------------------------------------------------------------------------------- field readers

const TOKEN = /^[A-Za-z0-9_.:@/+-]+$/;
const ID = /^[A-Za-z0-9_.:-]{1,100}$/;

/** A name from a fixed vocabulary of the app (a tool, a model, a provider id): letters, digits and _.:@/+- only. */
export function token(v, max = 80) {
  if (typeof v !== "string") return undefined;
  const s = v.trim();
  return s && s.length <= max && TOKEN.test(s) ? s : undefined;
}

export function oneOf(v, list) {
  return typeof v === "string" && list.includes(v) ? v : undefined;
}

/** A whole number within [min, max] (a larger one is cut to max), or undefined. */
export function int(v, min = 0, max = Number.MAX_SAFE_INTEGER) {
  const n = typeof v === "number" ? v : typeof v === "string" && v.trim() !== "" ? Number(v) : NaN;
  if (!Number.isFinite(n)) return undefined;
  return Math.min(max, Math.max(min, Math.round(n)));
}

export const bool = (v) => (typeof v === "boolean" ? v : undefined);
export const id = (v) => (typeof v === "string" && ID.test(v) ? v : undefined);

function text(v, max) {
  if (typeof v !== "string") return undefined;
  // control characters other than newline and tab are never kept
  const s = v.replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/g, "");
  return s.length > max ? `${s.slice(0, max - 1)}…` : s;
}

const iso = (v) => (typeof v === "string" && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,3})?Z$/.test(v) ? v : undefined);
const HOUR = 3600000;

/** An object without its undefined fields (what JSON would drop anyway, dropped now so equality is plain). */
function compact(o) {
  const out = {};
  for (const [k, v] of Object.entries(o)) {
    if (v === undefined) continue;
    out[k] = v && typeof v === "object" && !Array.isArray(v) ? compact(v) : v;
  }
  return out;
}

const plain = (v) => v !== null && typeof v === "object" && !Array.isArray(v);

// ------------------------------------------------------------------------------------------------- usage events

const EVENT_FIELDS = {
  app_init: (e) => ({
    layout: oneOf(e.layout, LAYOUTS), lang: oneOf(e.lang, LANGS), app: token(e.app, 24), browser: oneOf(e.browser, BROWSERS),
    mobile: bool(e.mobile), standalone: bool(e.standalone), persistent: bool(e.persistent),
  }),
  visit: (e) => ({ route: oneOf(e.route, ROUTES) }),
  pageview: (e) => ({ route: oneOf(e.route, ROUTES), tab: token(e.tab, 24) }),
  turn_start: (e) => ({ provider: token(e.provider, 40), model: token(e.model, 80), first: bool(e.first) }),
  turn_end: (e) => ({
    turn: id(e.turn), provider: token(e.provider, 40), model: token(e.model, 80), status: oneOf(e.status, TURN_STATUSES),
    first: bool(e.first), steps: int(e.steps, 0, 64), tool_rounds: int(e.tool_rounds, 0, 64), calls: int(e.calls, 0, 256),
    tools: int(e.tools, 0, 1000), tool_failures: int(e.tool_failures, 0, 1000), ms: int(e.ms, 0, 24 * HOUR),
    first_ms: int(e.first_ms, 0, 24 * HOUR), tokens_in: int(e.tokens_in, 0, 1e10), tokens_out: int(e.tokens_out, 0, 1e10),
    citations: int(e.citations, 0, 100000), citations_verified: int(e.citations_verified, 0, 100000),
    citations_used: int(e.citations_used, 0, 100000), claims: int(e.claims, 0, 100000), claims_refused: int(e.claims_refused, 0, 100000),
    limit_reached: bool(e.limit_reached), recorded: bool(e.recorded), excluded: oneOf(e.excluded, EXCLUSIONS),
    error_kind: oneOf(e.error_kind, ERROR_KINDS), error_type: token(e.error_type, 40),
  }),
  tool_end: (e) => ({
    call: id(e.call), tool: token(e.tool, 80), kind: oneOf(e.kind, TOOL_KINDS) || "unknown", place: oneOf(e.place, PLACES) || "unknown",
    status: oneOf(e.status, TOOL_STATUSES) || "unknown", ms: int(e.ms, 0, 7 * 24 * HOUR), error_type: token(e.error_type, 40),
  }),
  workflow: (e) => ({ key: id(e.key), kind: oneOf(e.kind, WORKFLOW_KINDS), name: token(e.name, 80), phase: oneOf(e.phase, WORKFLOW_PHASES) }),
  error: (e) => ({ category: oneOf(e.category, ERROR_CATEGORIES), name: token(e.name, 40), where: token(e.where, 80) }),
  availability: (e) => ({ service: oneOf(e.service, SERVICES), ok: bool(e.ok), detail: token(e.detail, 40) }),
  consent: (e) => ({ decision: oneOf(e.decision, CONSENT_DECISIONS), version: int(e.version, 0, 1000) }),
};

// the fields without which an event says nothing
const REQUIRED = {
  visit: ["route"], pageview: ["route"], turn_end: ["turn", "status"], tool_end: ["call", "tool"], workflow: ["key", "kind", "phase"],
  error: ["category"], availability: ["service", "ok"], consent: ["decision"],
};

/** One usage event as it may be sent and stored: {type, ts, …its known fields}, or null when it is not one. */
export function normalizeEvent(e) {
  if (!plain(e)) return null;
  const read = EVENT_FIELDS[e.type];
  if (!read) return null;
  const out = compact({ type: e.type, ts: int(e.ts, 0), ...read(e) });
  for (const k of REQUIRED[e.type] || []) if (out[k] === undefined) return null;
  return out;
}

/**
 * A batch as the page sends it: {v, batch, events}. → {batch, events, dropped} with the events that passed, or null
 * when the batch itself is not one (the Worker then refuses it as a bad request).
 */
export function normalizeBatch(b) {
  if (!plain(b) || b.v !== EVENTS_VERSION || !id(b.batch) || !Array.isArray(b.events) || !b.events.length || b.events.length > LIMITS.events) return null;
  const events = [];
  for (const e of b.events) {
    const n = normalizeEvent(e);
    if (n) events.push(n);
  }
  return { batch: b.batch, events, dropped: b.events.length - events.length };
}

// ------------------------------------------------------------------------------------------------- conversation records

const OWNER = /^[0-9a-f]{64}$/;

function toolRun(t) {
  if (!plain(t)) return null;
  const name = token(t.name, 80);
  if (!name) return null;
  const keys = Array.isArray(t.arg_keys) ? t.arg_keys.map((k) => token(k, 40)).filter(Boolean).slice(0, LIMITS.argKeys) : undefined;
  return compact({
    name, kind: oneOf(t.kind, TOOL_KINDS) || "unknown", where: oneOf(t.where, PLACES) || "unknown",
    status: oneOf(t.status, TOOL_STATUSES) || "unknown", ms: int(t.ms, 0, 7 * 24 * HOUR), error_type: token(t.error_type, 40),
    arg_keys: keys && keys.length ? keys : undefined,
  });
}

function counts(o, keys) {
  if (!plain(o)) return undefined;
  const out = {};
  for (const k of keys) {
    const n = int(o[k], 0, 1e10);
    if (n !== undefined) out[k] = n;
  }
  return Object.keys(out).length ? out : undefined;
}

/**
 * One finished turn as it may be stored (studio/docs/TELEMETRY.md lists the fields), or null when it is not one: a
 * record of another version or consent notice, without an owner, a question, or a status. Text fields are cut to their
 * limits here; what is in them is the sensitive-content check's to judge (sensitive.js), on both sides.
 */
export function normalizeRecord(r, { consentVersion = CONSENT_VERSION } = {}) {
  if (!plain(r) || r.v !== RECORD_VERSION) return null;
  if (r.consent !== consentVersion) return null;
  const question = text(r.question, LIMITS.question);
  const status = oneOf(r.status, TURN_STATUSES);
  if (!id(r.id) || !OWNER.test(String(r.owner || "")) || !question || !question.trim() || !status) return null;
  const error = plain(r.error) ? compact({ kind: oneOf(r.error.kind, ERROR_KINDS), type: token(r.error.type, 40), status: int(r.error.status, 0, 999) }) : undefined;
  const app = plain(r.app) ? compact({ tcmstudio: token(r.app.tcmstudio, 24), bioagent: token(r.app.bioagent, 24), psh: token(r.app.psh, 24) }) : undefined;
  const runtime = plain(r.runtime) ? compact({
    compute: oneOf(r.runtime.compute, ["auto", "browser", "runner"]), browser: token(r.runtime.browser, 24), runner: token(r.runtime.runner, 24),
    layout: oneOf(r.runtime.layout, LAYOUTS), lang: oneOf(r.runtime.lang, LANGS),
  }) : undefined;
  return compact({
    v: RECORD_VERSION, id: r.id, conversation: id(r.conversation), turn: int(r.turn, 1, 10000), owner: r.owner, consent: consentVersion,
    started: iso(r.started), finished: iso(r.finished), ms: int(r.ms, 0, 24 * HOUR), first_ms: int(r.first_ms, 0, 24 * HOUR),
    status, limit_reached: bool(r.limit_reached),
    question, answer: text(r.answer, LIMITS.answer) ?? "", redacted: int(r.redacted, 0, 100000),
    provider: token(r.provider, 40), model: token(r.model, 80),
    steps: int(r.steps, 0, 64), tool_rounds: int(r.tool_rounds, 0, 64), calls: int(r.calls, 0, 256),
    tools: Array.isArray(r.tools) ? r.tools.slice(0, LIMITS.tools).map(toolRun).filter(Boolean) : [],
    citations: counts(r.citations, ["total", "verified", "used", "unresolved"]),
    claims: counts(r.claims, ["total", "allowed", "refused", "unchecked"]),
    evidence: counts(r.evidence, ["items", "predicted", "released", "withheld"]),
    usage: counts(r.usage, ["input", "output"]),
    error: error && Object.keys(error).length ? error : undefined,
    app: app && Object.keys(app).length ? app : undefined,
    runtime: runtime && Object.keys(runtime).length ? runtime : undefined,
  });
}

/** The owner of records, from the visitor's deletion key: sha256("tcms-owner|v1|" + key) in hex. The key never
 * leaves the browser until the visitor asks for deletion; the Worker keeps only this digest. */
export const OWNER_PREFIX = "tcms-owner|v1|";
export const OWNER_KEY = /^[A-Za-z0-9_-]{43}$/; // 32 random bytes, base64url without padding

export function ownerOk(v) {
  return typeof v === "string" && OWNER.test(v);
}

export async function ownerOf(key, subtle = globalThis.crypto?.subtle) {
  if (!OWNER_KEY.test(String(key || "")) || !subtle) return null;
  const bytes = new Uint8Array(await subtle.digest("SHA-256", new TextEncoder().encode(OWNER_PREFIX + key)));
  return [...bytes].map((b) => b.toString(16).padStart(2, "0")).join("");
}
