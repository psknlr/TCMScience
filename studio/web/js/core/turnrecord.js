// One finished turn, as the telemetry sees it (studio/docs/TELEMETRY.md): whether its content may be recorded at all,
// the conversation record when it may (telemetry-contract.js normalizeRecord), and the usage event every turn gives,
// recorded or not (counts and timings only). Built from what the app already stores: the branch of messages, the
// assistant's record, the tool messages with their envelopes.
//
// Never recorded, whatever the visitor agreed to (exclusionOf): a conversation with an uploaded file, in a project that
// holds files (small text files go to the model with every message), one that used a clinical tool or a tool given the
// visitor's own data (a file, a path, an upload, an imported store), and one whose questions read as a person's medical
// record or carry an identifier (sensitive.js). A conversation is judged whole: once it touched any of these, none of its
// later turns is recorded either. A tool's arguments are never recorded: their names only.

import { citationsOf, claimsOf, evidenceOf, releaseOf } from "./governance.js";
import { redactText, sensitiveAny } from "./sensitive.js";
import { CONSENT_VERSION, LIMITS, PLACES, RECORD_VERSION, TOOL_KINDS, normalizeRecord, token } from "./telemetry-contract.js";

// tools that take the visitor's own data: an imported store, a pipeline over their files, the clinic's case records
const USER_DATA_TOOL = /^(?:tcmdb\.import|tcmdb_import|job\.pipeline\.|job\.tcmdb\.|run_pipeline\b|job\.skill\.run|uploads?\b|files?\.)/;
const CLINICAL_TOOL = /^(?:clinic[._]|skill\.draft-tcm-prescription)/;
// argument names that hand a tool a file of the visitor's (a public dataset's accession is not one, nor numbers typed
// into the question, which the record holds anyway)
const DATA_ARG = /(?:^|_)(?:file|files|path|paths|filename|upload|uploads|upload_id|upload_ids|data_file|input_file|input_files|sample_sheet|fastq|fastqs|bam|store_path|local_path)$/i;
const LOCAL_VALUE = /^(?:\/|\.{1,2}\/|~\/|[A-Za-z]:\\|file:|upload:|uploads\/|blob:)/;

/** The tool a stored tool message ran: its catalog entry (envelope.via) when it resolved, else the name the model gave. */
export function toolName(t) {
  return String(t?.envelope?.via || t?.name || "");
}

function argsOf(t) {
  const a = t?.args && typeof t.args === "object" ? t.args : {};
  // call_tool / catalog calls carry the real tool and its arguments one level down
  return a.tool && a.arguments && typeof a.arguments === "object" ? { name: String(a.tool), args: a.arguments } : { name: "", args: a };
}

/** Does a call hand a tool the visitor's own files or data (by an argument's name or a local path for its value)? */
export function touchesUserData(args, depth = 0) {
  if (!args || typeof args !== "object" || depth > 4) return false;
  for (const [k, v] of Object.entries(args)) {
    if (DATA_ARG.test(k) && v !== null && v !== "" && !(Array.isArray(v) && !v.length)) return true;
    if (typeof v === "string" && LOCAL_VALUE.test(v.trim())) return true;
    if (v && typeof v === "object" && touchesUserData(v, depth + 1)) return true;
  }
  return false;
}

/**
 * Why this conversation's turns are not recorded, or null. path: the branch's user and assistant messages up to and
 * including this turn's question; tools: every tool message of the conversation (earlier turns' too); projectFiles:
 * how many files the project holds; discarded: the conversation was deleted while the turn ran.
 */
export function exclusionOf({ path = [], tools = [], projectFiles = 0, discarded = false } = {}) {
  if (discarded) return "discarded";
  if (path.some((m) => m?.role === "user" && Array.isArray(m.attachments) && m.attachments.length)) return "uploaded_file";
  if (projectFiles > 0) return "project_files";
  for (const t of tools) {
    const kind = t?.envelope?.governance?.kind || "";
    const inner = argsOf(t);
    const names = [toolName(t), String(t?.name || ""), inner.name];
    if (kind === "clinic" || names.some((n) => CLINICAL_TOOL.test(n))) return "clinical_tool";
    if (names.some((n) => USER_DATA_TOOL.test(n)) || touchesUserData(inner.args)) return "user_data_tool";
  }
  if (sensitiveAny(path.filter((m) => m?.role === "user").map((m) => m.content))) return "sensitive_text";
  return null;
}

const CITE = /[[【]\s*(E\d+(?:\s*[,，、;；]\s*E\d+)*)\s*[\]】]/g;

/** The citation ids an answer cites ([E1], [E2, E3], 【E4】). */
export function citedIds(text) {
  const out = new Set();
  for (const m of String(text || "").matchAll(CITE)) for (const id of m[1].split(/\s*[,，、;；]\s*/)) out.add(id.trim());
  return out;
}

/**
 * What the turn's tools gave and the answer used, counted: citations (total, quote verified, cited in the answer, cited
 * but not found among the conversation's), claims (allowed / refused / unchecked by the kernel), evidence (items,
 * predicted, and results released or withheld). `known`: every tool message of the conversation, for the ids.
 */
export function evidenceStats(tools = [], answer = "", known = tools) {
  const out = {
    citations: { total: 0, verified: 0, used: 0, unresolved: 0 }, claims: { total: 0, allowed: 0, refused: 0, unchecked: 0 },
    evidence: { items: 0, predicted: 0, released: 0, withheld: 0 },
  };
  const ids = new Set();
  for (const t of known) for (const c of safe(() => citationsOf(t?.envelope), [])) ids.add(c.id);
  for (const t of tools) {
    const env = t?.envelope;
    if (!env) continue;
    const cites = safe(() => citationsOf(env), []);
    out.citations.total += cites.length;
    out.citations.verified += cites.filter((c) => c.quote_verified === true).length;
    const ev = safe(() => evidenceOf(env), []);
    out.evidence.items += ev.length;
    out.evidence.predicted += ev.filter((e) => e.predicted).length;
    for (const c of safe(() => claimsOf(env), [])) {
      out.claims.total++;
      if (c.allowed === true) out.claims.allowed++;
      else if (c.allowed === false) out.claims.refused++;
      else out.claims.unchecked++;
    }
    const rel = safe(() => releaseOf(env), null);
    if (rel) rel.authorized ? out.evidence.released++ : out.evidence.withheld++;
  }
  const used = citedIds(answer);
  out.citations.used = used.size;
  out.citations.unresolved = [...used].filter((id) => !ids.has(id)).length;
  return out;
}

function safe(fn, fallback) {
  try { return fn() ?? fallback; } catch { return fallback; }
}

/** The tool runs of a turn as a record lists them: name, kind, where, status, time, error type, argument names. */
export function toolRuns(tools = []) {
  return tools.slice(0, LIMITS.tools).map((t) => {
    const env = t?.envelope || {};
    const where = env.receipt?.decided_by === "router" ? "page" : t?.where || env.receipt?.where;
    return {
      name: token(toolName(t), 80) || "unknown",
      kind: TOOL_KINDS.includes(env.governance?.kind) ? env.governance.kind : "unknown",
      where: PLACES.includes(where) ? where : "unknown",
      status: env.status || t?.status || "unknown",
      ms: Number.isFinite(Number(env.duration_ms)) ? Number(env.duration_ms) : undefined,
      error_type: env.error?.type || undefined,
      arg_keys: Object.keys(argsOf(t).args).slice(0, LIMITS.argKeys),
    };
  });
}

/** completed | cancelled | failed | interrupted, from the assistant record's status (ok, stopped, error). */
export function turnStatus(assistant, { interrupted = false } = {}) {
  if (interrupted) return "interrupted";
  if (assistant?.status === "error") return "failed";
  if (assistant?.status === "stopped") return "cancelled";
  return "completed";
}

/** Rounds of the turn that ran tools (each one model call that asked for tools). */
export function toolRounds(assistant) {
  return new Set((assistant?.segments || []).filter((s) => s?.type === "tools").map((s) => s.step)).size;
}

const iso = (t) => (Number.isFinite(t) && t > 0 ? new Date(t).toISOString() : undefined);
const safeText = (s) => (typeof s === "string" ? s : "");

/**
 * The usage event of a finished turn ("turn_end"): counts and timings, never text. turn: {id, provider, model, status,
 * first, startedAt, endedAt, firstAt, calls, assistant, tools, known, excluded, recorded}.
 */
export function turnEndEvent(turn) {
  const a = turn.assistant || {};
  const tools = turn.tools || [];
  const stats = evidenceStats(tools, safeText(a.content), turn.known || tools);
  return {
    type: "turn_end", turn: turn.id, provider: turn.provider, model: turn.model, status: turn.status, first: Boolean(turn.first),
    steps: a.steps, tool_rounds: toolRounds(a), calls: turn.calls || a.steps, tools: tools.length,
    tool_failures: tools.filter((t) => ["failed", "refused"].includes(t?.envelope?.status || t?.status)).length,
    ms: turn.endedAt && turn.startedAt ? turn.endedAt - turn.startedAt : undefined,
    first_ms: turn.firstAt && turn.startedAt ? turn.firstAt - turn.startedAt : undefined,
    tokens_in: a.usage?.input, tokens_out: a.usage?.output,
    citations: stats.citations.total, citations_verified: stats.citations.verified, citations_used: stats.citations.used,
    claims: stats.claims.total, claims_refused: stats.claims.refused, limit_reached: Boolean(a.limitReached),
    recorded: Boolean(turn.recorded), excluded: turn.excluded || undefined,
    error_kind: a.error?.kind, error_type: a.error?.type || (a.error?.status ? `http_${a.error.status}` : undefined),
  };
}

/**
 * The conversation record of a turn (telemetry-contract.js normalizeRecord), within LIMITS.recordBytes as sent (the
 * answer is shortened when it must be). Question and answer are redacted here (sensitive.js) and again by the Worker.
 * turn: as for turnEndEvent, plus {conversation, index, owner, question, app, runtime}.
 */
export function buildRecord(turn) {
  const a = turn.assistant || {};
  const tools = turn.tools || [];
  const q = redactText(safeText(turn.question));
  const ans = redactText(safeText(a.content));
  const stats = evidenceStats(tools, ans.text, turn.known || tools);
  const draft = {
    v: RECORD_VERSION, id: turn.id, conversation: turn.conversation, turn: turn.index, owner: turn.owner, consent: CONSENT_VERSION,
    started: iso(turn.startedAt), finished: iso(turn.endedAt), ms: turn.endedAt && turn.startedAt ? turn.endedAt - turn.startedAt : undefined,
    first_ms: turn.firstAt && turn.startedAt ? turn.firstAt - turn.startedAt : undefined,
    status: turn.status, limit_reached: Boolean(a.limitReached),
    question: q.text, answer: ans.text, redacted: q.hits + ans.hits || undefined,
    provider: turn.provider, model: turn.model, steps: a.steps, tool_rounds: toolRounds(a), calls: turn.calls || a.steps,
    tools: toolRuns(tools), citations: stats.citations, claims: stats.claims, evidence: stats.evidence,
    usage: a.usage ? { input: a.usage.input, output: a.usage.output } : undefined,
    error: a.error ? { kind: a.error.kind, type: a.error.type, status: a.error.status } : undefined,
    app: turn.app, runtime: turn.runtime,
  };
  let record = normalizeRecord(draft);
  if (!record) return null;
  const size = (r) => new TextEncoder().encode(JSON.stringify(r)).length;
  while (size(record) > LIMITS.recordBytes && record.answer.length > 200) {
    record = { ...record, answer: `${record.answer.slice(0, Math.floor(record.answer.length * 0.7))}…` };
  }
  return size(record) > LIMITS.recordBytes ? null : record;
}
