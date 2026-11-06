// The agent loop: the model answers or asks for tools; the router runs them where they can run; their results go
// back; until the model answers, the user stops it, or the step limit is reached.
//
// History discipline. Each assistant message keeps the exact provider messages of its turn (`wire`) and is replayed
// verbatim to a provider of the same format: reasoning a provider needs back stays for that provider only
// (reasoning_details for the same provider), other reasoning fields are dropped, an inline <think> is stripped,
// results of earlier turns are cut to 2 500 characters, and a tool call that never got its result (a stopped turn) is
// answered with a placeholder so the history stays valid. A turn made in the other format is replayed as its text.
// Anthropic thinking blocks are replayed only inside the turn that produced them: their signatures bind the exact
// system prompt and earlier messages, which change between turns here (the environment in the system prompt, earlier
// results cut short), and a replayed block after such an edit is a 400 on the current models. Dropping every earlier
// turn's blocks removes a leading run, which the API allows. Within a turn, system, tools and history are fixed until
// the context budget compacts this turn's older results (below): a later block of the turn then no longer matches what
// precedes it. A first-party model that binds blocks drops such a block (anthropic.js, prefix_mismatch_behavior
// drop_block); another Anthropic-format endpoint may refuse the request (error:model), and Continue goes on without
// thinking (resumeOf).
// Two user messages never follow each other (a turn that failed before it said anything leaves nothing to replay):
// they are joined, since several local chat templates reject a conversation whose roles do not alternate.
//
// Every request fits the model's context (budget.js): over budget, earlier results are compacted to their headers;
// when even that is not enough, no more tools are offered and the model is asked for its answer. A turn always says
// how it ended (`termination`): answered, max_steps_synthesized (the step limit, then an answer from the results so
// far), max_steps (no answer could be had after it), context_synthesized (the context was full, then the answer),
// context_full (… and no answer), truncated (the answer hit the output limit), stopped, refused_by_model, or
// error:<kind>. A turn that ended with an error or a stop can be continued on the same provider (`resume`): its
// settled tool results are not run again.

import * as anthropicLib from "./llm/anthropic.js";
import * as openaiLib from "./llm/openai.js";
import { stripThink } from "./llm/openai.js";
import { DEFAULT_CONTEXT_TOKENS, budgetOf, fitRequest, isCompactable, measure } from "./budget.js";
import { toProviderTools } from "./catalog.js";
import { t } from "./i18n.js";
import { isIdentityQuestion } from "./prompt.js";
import { scrubProjectRefs } from "./publiclinks.js";
import { synthEnvelope } from "./router.js";
import { canonicalJson, clip, isAbort, isPlainObject, sleep as defaultSleep, uuid } from "./util.js";

export const MAX_STEPS = 16;
export const OLD_RESULT_CHARS = 2500;
export const RESULT_CHARS = 16000;
export const RETRY_DELAYS = [1500, 4000];
/** A Retry-After longer than this is not waited out inside a turn: the error, which says when to retry, is shown. */
export const MAX_RETRY_AFTER_S = 20;
export const DANGLING_TEXT = "(this call was stopped before it finished; there is no result)";
export const LIMIT_TEXT = "Not run: the limit of model calls for this turn was reached. Answer from the results you have.";
export const CONTEXT_TEXT = "Not run: this turn's tool results fill the model's context, so no more tools run in it. Answer from the results you have.";
/** What the call that asks for the answer adds to its request (never kept in the turn's messages). */
export const ANSWER_TEXT = {
  max_steps: "The limit of model calls for this turn is reached: no more tools can run in it. Answer now from the results above, and say what is still missing.",
  context: "This turn's tool results fill the model's context: no more tools can run in it. Answer now from the results above (a shortened result keeps its header and citations), and say what is still missing.",
};
/** The message a continued answer starts from, when the turn stopped in the middle of its text. */
export const CONTINUE_TEXT = "Continue your answer from exactly where it stopped, without repeating what you already wrote.";
export const UNKNOWN_TEXT = "Not run again: this call was under way when the turn stopped, and whether it took effect is unknown. Do not submit it again before checking: use job_status (or read back what it writes).";
/** finish_reason values that mean the answer was cut off at the output limit. */
const CUT_OFF = new Set(["length", "max_tokens"]);
/** After the model refused a request as too long: the share of that request the one retry may send. */
export const OVERFLOW_SHRINK = 0.7;
export const TERMINATIONS = Object.freeze(["answered", "max_steps_synthesized", "max_steps", "context_synthesized", "context_full", "truncated", "stopped", "refused_by_model", "error:model", "error:network", "error:runtime"]);

export const formatOf = (provider) => (provider?.format === "anthropic" ? "anthropic" : "openai");

// A call the model repeats in a turn, with the same arguments, is answered from the first when its result depends
// only on what it is given: a lookup or computation, a database query, a governed run, a search of the fixed formula
// and materia tables. Never a job, an act that needs confirming, a status, or anything whose answer the turn itself
// can change; and a call that can change state (a job, a confirmed act, the clinic, the data hub, a study) starts the
// count again. A first answer that may not hold on a second try (a runtime error, a stop) is not reused.
const REUSABLE_KINDS = new Set(["native", "connector", "skill"]);
const REUSABLE_ENTRIES = new Set(["tcmdb.formulas", "tcmdb.materia"]);
const STATEFUL_KINDS = new Set(["job", "clinic", "tcmdb", "study"]);
const SETTLED_ERRORS = new Set(["bad_arguments", "not_found", "network_off", "declined", "refused", "dataset_not_built"]);

/** How a call may be reused: {key} when a repeat of it is answered from the first, {stateful} when it starts over. */
export function reusePolicy(router, c) {
  let r = null;
  try { r = router?.resolve?.(c.name, isPlainObject(c.arguments) ? c.arguments : {}) ?? null; } catch { r = null; }
  const target = r && !r.error ? r.target : null;
  if (!target) return { key: null, stateful: false };
  const id = r.entry?.id || target.id || "";
  const acts = Boolean(target.confirm || target.job);
  const pure = !acts && (REUSABLE_KINDS.has(target.kind) || REUSABLE_ENTRIES.has(id));
  return {
    key: pure ? `${c.name}\u0000${canonicalJson(isPlainObject(c.arguments) ? c.arguments : {})}` : null,
    stateful: acts || (STATEFUL_KINDS.has(target.kind) && !REUSABLE_ENTRIES.has(id)),
  };
}

const settled = (env) => env?.status === "succeeded"
  || ((env?.status === "failed" || env?.status === "refused") && SETTLED_ERRORS.has(env?.error?.type));

/** The answer to a repeated call: the first one's outcome, pointing to it rather than repeating it. */
function reusedEnvelope(first, prior) {
  return {
    ...first,
    citations: [], // the first call's citations already carry their numbers
    summary: t("core.agent.reused", { step: prior.step }),
    text: `Not run again: the same call, with the same arguments, was made earlier in this turn (step ${prior.step}, call ${prior.callId}); use its result. Change the arguments to learn something new.`,
    reused: { step: prior.step, callId: prior.callId },
  };
}
// What a result means for the question, in one line the model reads under it. Without it a missing tool, a store not
// built here, a lookup that matched nothing and a tested negative all read as "nothing", and an answer can report as
// a negative finding what is only a database that is not here. An ordinary result gets no line.
const LIST_KEYS = ["rows", "records", "relations", "results", "matched", "hits", "items", "assertions", "edges"];
const OUTCOME_TEXT = {
  tool_unavailable: "the tool cannot run here; calling it again will not help: use another route, or say it is unavailable",
  data_unavailable: "the data this needs are not available here; that says nothing about the question: say which data are missing",
  no_record: "nothing matched; that is not evidence that none exists",
  input_error: "the arguments were refused; change them as the message says",
  not_permitted: "not done: it needs a person's decision",
  tool_failed: "the call failed; the same input will fail again unless the cause is removed",
  negative_finding: "the records report a tested negative result: a finding, not missing data",
  mixed_findings: "some records report positive and some negative results: weigh both",
  inconclusive: "the records are inconclusive: they neither support nor refute",
};

function outcomesIn(result) {
  const seen = new Set();
  if (!isPlainObject(result)) return seen;
  for (const key of LIST_KEYS) {
    const list = result[key];
    if (!Array.isArray(list)) continue;
    for (const row of list.slice(0, 500)) {
      const o = isPlainObject(row) ? row.outcome : null;
      if (o === "positive" || o === "negative" || o === "inconclusive") seen.add(o);
    }
  }
  return seen;
}

function emptyResult(result) {
  if (!isPlainObject(result)) return false;
  const lists = LIST_KEYS.filter((k) => Array.isArray(result[k]));
  if (lists.length) return lists.every((k) => result[k].length === 0);
  return result.total === 0 || result.count === 0;
}

/** [code, advice] for a result that is not an ordinary answer; null for one that is. */
export function outcomeOf(envelope) {
  const err = envelope?.error || {};
  const msg = String(err.message || "");
  if (envelope?.status === "failed" || envelope?.status === "refused") {
    if (err.type === "not_found" && /^unknown tool/i.test(msg)) return ["tool_unavailable", OUTCOME_TEXT.tool_unavailable];
    if (err.type === "unavailable") {
      return /not built|no store|store is|import (it|a store)|not downloaded|no files/i.test(msg)
        ? ["data_unavailable", OUTCOME_TEXT.data_unavailable] : ["tool_unavailable", OUTCOME_TEXT.tool_unavailable];
    }
    if (err.type === "network_off") return ["data_unavailable", "the source cannot be reached with web access off; that says nothing about the question"];
    // Nothing was searched: the stores asked for are not built here (never a zero-hit result).
    if (err.type === "dataset_not_built") {
      const names = (err.coverage?.missing || []).map((m) => m?.dataset).filter(Boolean).join(", ");
      return ["data_unavailable", `the data store${names ? `s (${names})` : "s"} this needs are not built here, so nothing was searched; that says nothing about the question: name the missing store and how to build it`];
    }
    // The source answered with an error report, a page or malformed data: nothing from it is evidence.
    if (err.type === "invalid_response") return ["tool_failed", "the source answered, but not with data (an error report, a page or a malformed answer); nothing from it is evidence and it says nothing about the question"];
    if (err.type === "not_found") return ["no_record", OUTCOME_TEXT.no_record];
    if (err.type === "bad_arguments") return ["input_error", OUTCOME_TEXT.input_error];
    if (err.type === "refused" || err.type === "declined") return ["not_permitted", OUTCOME_TEXT.not_permitted];
    // a continued turn's call that was under way when the turn stopped (a job, a confirmed act): not known, not failed
    if (err.type === "interrupted") return ["state_unknown", "this call was under way when the turn stopped; whether it took effect is unknown: check its state before doing it again"];
    return ["tool_failed", OUTCOME_TEXT.tool_failed];
  }
  if (envelope?.status !== "succeeded") return null;
  const found = outcomesIn(envelope.result);
  if (found.has("negative")) return found.has("positive") ? ["mixed_findings", OUTCOME_TEXT.mixed_findings] : ["negative_finding", OUTCOME_TEXT.negative_finding];
  if (found.has("inconclusive") && !found.has("positive")) return ["inconclusive", OUTCOME_TEXT.inconclusive];
  if (emptyResult(envelope.result)) {
    const missing = (envelope.result?.coverage?.missing || []).map((m) => m?.dataset).filter(Boolean);
    return ["no_record", missing.length
      ? `the stores searched matched nothing, and ${missing.join(", ")} ${missing.length > 1 ? "are not built here and were" : "is not built here and was"} not searched; that is not evidence that none exists`
      : "the query ran and matched nothing; that is not evidence that none exists"];
  }
  return null;
}

// A tool that fails over and over in one turn is not called again in it: three failures, or one that a retry cannot
// change (the tool is not here, cannot run here, or the user declined it). The model is told so, with the failures,
// and is pointed at the ways on: another tool, the missing data named, or the question put to the user.
export const FAILURE_BUDGET = 3;
const FINAL_OUTCOMES = new Set(["tool_unavailable", "not_permitted"]);

function failureKey(router, c) {
  try {
    const r = router?.resolve?.(c.name, isPlainObject(c.arguments) ? c.arguments : {});
    return (r && !r.error && (r.entry?.id || r.target?.id)) || c.name;
  } catch {
    return c.name;
  }
}

/** The tool as the model named it, with the entry it resolved to when that differs (call_tool, a loose name). */
const named = (c, key) => (key && key !== c.name && key !== `native.${c.name}` ? `${c.name} (${key})` : c.name);

async function budgetEnvelope(c, key, past) {
  const list = past.map((p) => `step ${p.step}: ${p.type}${p.message ? ` (${clip(p.message, 120)})` : ""}`).join("; ");
  const env = await synthEnvelope({
    tool: c.name, args: c.arguments, status: "refused", summary: t("core.agent.failure_budget", { n: past.length }),
    text: `Not run: ${named(c, key)} has failed ${past.length === 1 ? "in a way a retry cannot change" : `${past.length} times`} in this turn (${list}). Do not call it again in this turn. Change approach: use another tool (catalog_search), name the data or tool that is missing, or ask the user.`,
    error: { type: "refused", message: `failure budget: ${named(c, key)} failed ${past.length} time(s) in this turn`, hint: "change approach" },
  });
  return { ...env, budget: true };
}

const libFor = (format, llm) => (llm?.[format]) || (format === "anthropic" ? anthropicLib : openaiLib);

/**
 * Run one turn. `history` is the branch so far as stored messages (CONTRACTS §7), ending with the new user message.
 * onEvent receives: model.start {step} · context {step, bytes, tokens, budget, compacted} · text {delta, step, joins
 * (it goes on in the text a continued answer was cut off in)} · reasoning {delta, step} · tool.pending {index, name,
 * step} · tool.start {callId, name, args, where, step} · tool.approval {callId, request} (request.respond(decision)
 * when no onApproval is given) · tool.end {callId, envelope, step} · usage {input, output, step, round} · model.retry
 * {step, attempt, delay_ms, message} (drop that step's partial text) · model.end {step, finish_reason} · error
 * {message, kind} · done {status, termination}.
 * `resume` continues a stored turn that ended with an error or a stop, or whose answer was cut off (resumeOf): its
 * messages are sent again as they were, its settled results are not run again, the steps it used count against the
 * limit (at the limit, only the call for the answer is left), and what this run adds goes on in the same message, a
 * cut-off text in that text (`history` then ends with that turn's question).
 * Returns {assistant, toolMessages (this run's), usage (this run's), status, steps}.
 */
export async function runTurn({
  provider, system, history = [], router, catalog, settings = {}, signal, onEvent, projectId = null, conversationId = null,
  onApproval, tools, llm, retryDelays = RETRY_DELAYS, sleep = defaultSleep, resume = null,
}) {
  const format = formatOf(provider);
  const lib = libFor(format, llm);
  const coreTools = tools ?? catalog?.core ?? [];
  const providerTools = provider.tools === false ? [] : toProviderTools(coreTools, format);
  const lastUser = [...history].reverse().find((m) => m.role === "user") || null;
  const identity = Boolean(provider.relay && isIdentityQuestion(lastUser?.content));
  let thinking = settings.thinking !== false && !identity;
  const maxSteps = Math.max(1, Math.min(64, Math.floor(Number(settings.maxSteps) || MAX_STEPS)));
  const base = buildWireHistory(history, provider);
  const turnWire = [];
  const usage = { input: 0, output: 0 };
  const texts = [];
  const reasonings = [];
  const toolMessages = [];
  const seen = new Map(); // a call's key (reusePolicy) → {step, callId, envelope: Promise}
  const failures = new Map(); // a tool's key (failureKey) → [{step, callId, type, message, final}]
  const notes = new Map(); // callId → a line added under its result (the failure budget)
  const emit = (ev) => {
    try { onEvent?.(ev); } catch (err) { (globalThis.reportError || console.error)(err); }
  };
  const assistant = {
    id: uuid(), conversationId, parentId: lastUser?.id ?? null, role: "assistant", createdAt: Date.now(),
    content: "", reasoning: "", toolCalls: [], segments: [], provider: provider.id, model: provider.model,
    wireFormat: format, wire: turnWire, usage: { ...usage }, status: "ok", steps: 0,
  };
  if (identity) assistant.hideReasoning = true;

  let status = "ok";
  let step = 0;
  // citations are numbered across the conversation (renumberCitations): this turn continues after the earlier turns'
  // ids, so an [E1] the model read in an earlier result never names a different source in this one
  let citeNext = citationsUsed(history) + 1;
  let lastFinish = null;
  // the current step's stream, kept here so a stop or an error keeps what was already said
  let stepText = "";
  let stepReasoning = "";
  let stepRecorded = true;
  let calls = [];
  let done = null;
  let roundUsage = null;
  // the context budget (budget.js): every request is fitted to it; after the model refused one as too long, the one
  // retry and the rest of the turn keep under a tighter ceiling
  const budget = budgetOf(provider);
  let ceiling = null;
  let overflowRetried = false;
  const compacted = new Set(); // the calls whose results a request of this turn carried shortened
  // "max_steps" | "context": this call asks for the answer, with no tools to run (see ANSWER_TEXT)
  let answering = null;
  let ended = false; // the turn ended on a model call that asked for no tools
  let answered = false; // … and wrote an answer
  const prior = { input: 0, output: 0 }; // what a continued turn's earlier run used
  // a continued answer cut off in the middle of its text: the text segment its first call goes on in (one text, not
  // two: a cut inside a sentence, a list or a code fence reads as it was meant), and the message that asked it to
  let joinTo = null;
  let goOn = null;

  const recordText = (s, text, reasoning) => {
    const cut = text ? joinTo : null;
    joinTo = null;
    if (reasoning && !identity) {
      reasonings.push(reasoning);
      // the reasoning of the text it goes on with comes before that text
      const seg = { type: "reasoning", step: s, text: reasoning };
      if (cut) assistant.segments.splice(assistant.segments.indexOf(cut), 0, seg);
      else assistant.segments.push(seg);
    }
    if (cut) {
      cut.text += text;
      texts[texts.length - 1] += text;
    } else if (text) {
      texts.push(text);
      assistant.segments.push({ type: "text", step: s, text });
    }
  };
  const partialWire = (text) => {
    if (!text) return;
    turnWire.push(format === "anthropic" ? { role: "assistant", content: [{ type: "text", text }] } : { role: "assistant", content: text });
  };

  /** The request for this call: its messages fitted to the budget, the tools it offers, and tool_choice. */
  const request = (finalCall, mode) => {
    // asking for the answer: the OpenAI-compatible path sends no tools and no tool_choice (a "none" that some services
    // ignore); Anthropic keeps the tools, which its history of tool_use needs, with tool_choice none, which it enforces
    const offered = mode && format === "openai" ? [] : providerTools;
    const toolChoice = offered.length && (mode || (finalCall && step > 1)) ? "none" : undefined;
    // an answer asked to go on where it was cut off is not asked for the answer again
    const ask = mode && (!goOn || turnWire[turnWire.length - 1] !== goOn) ? ANSWER_TEXT[mode] : null;
    const fixed = fixedCost({ provider, system, tools: offered, thinking, toolChoice, format, ask });
    const b = ceiling ? { tokens: Math.min(budget.tokens, ceiling.tokens), bytes: Math.min(budget.bytes, ceiling.bytes) } : budget;
    const fit = fitRequest({ base, turn: turnWire, fixed, budget: b, lastResort: Boolean(mode) });
    return { messages: ask ? withAsk(fit.messages, ask, format) : fit.messages, tools: offered, toolChoice, fit, budget: b };
  };
  /**
   * This call's request; when nothing more fits with tools offered, the request that asks for the answer from the
   * results gathered instead. Before this turn has any, the request is sent as it is: an answer without tools or
   * results is no answer, and an estimate is not the model's count (a model that finds it too long says so: callModel).
   */
  const fitted = (finalCall) => {
    let req = request(finalCall, answering);
    if (!req.fit.fits && !answering && turnWire.some(isResults)) {
      answering = "context";
      req = request(finalCall, answering);
    }
    return req;
  };

  try {
    if (resume) {
      const r = resumeOf(resume, format);
      turnWire.push(...r.wire);
      if (r.thinkingDropped) thinking = false; // their signatures bound a system prompt rebuilt since (top of file)
      for (const sg of r.segments) {
        assistant.segments.push(sg);
        if (sg.type === "text" && sg.text) texts.push(sg.text);
        if (sg.type === "reasoning" && sg.text && !identity) reasonings.push(sg.text);
      }
      assistant.toolCalls.push(...r.toolCalls.map(({ envelope, ...c }) => c));
      for (const k of ["servedBy", "fallback"]) if (resume[k]) assistant[k] = resume[k];
      prior.input = Number(resume.usage?.input) || 0;
      prior.output = Number(resume.usage?.output) || 0;
      step = r.steps;
      assistant.steps = step;
      citeNext = Math.max(citeNext - 1, r.citeTop) + 1;
      // what the earlier run settled: a repeat of a pure call is answered from it, a failed tool keeps its count
      for (const c of r.toolCalls) {
        const env = c.envelope;
        if (!env || r.unsettled.some((u) => u.id === c.id)) continue;
        const call = { id: c.id, name: c.name, arguments: c.args };
        const reuse = reusePolicy(router, call);
        if (reuse.stateful) seen.clear();
        else if (reuse.key && settled(env)) seen.set(reuse.key, { step: c.step, callId: c.id, envelope: Promise.resolve(env) });
        if (!env.reused && !env.budget) noteFailure(failureKey(router, call), call, c.step, env, { quiet: true });
      }
      // the calls under way when it stopped: a pure one runs again, one that may have changed something is reported as
      // unknown, never submitted again
      if (r.unsettled.length) {
        const outcomes = await Promise.all(r.unsettled.map((u) => {
          const c = { id: u.id, name: u.name, arguments: u.args };
          return reusePolicy(router, c).stateful ? unknownOutcome(c, u.step) : executeTool(c, u.step);
        }));
        for (const o of outcomes) {
          const before = citeNext;
          citeNext = renumberCitations(o.envelope, citeNext);
          if (before > 1 && citeNext > before) emit({ type: "tool.update", callId: o.c.id, envelope: o.envelope, step: o.s });
          toolMessages.push(toolMessage(o.c, o.envelope, assistant, o.s));
        }
        replaceResults(turnWire, format, lib.toolResultMessages(outcomes.map(resultOf)));
      }
      // an answer cut off (or stopped) in the middle of its text: the model is asked to go on from there, and what it
      // writes goes on in that text
      const last = turnWire[turnWire.length - 1];
      if (last?.role === "assistant" && !callsIn(last)) {
        goOn = format === "anthropic" ? { role: "user", content: [{ type: "text", text: CONTINUE_TEXT }] } : { role: "user", content: CONTINUE_TEXT };
        turnWire.push(goOn);
        const tail = assistant.segments[assistant.segments.length - 1];
        if (tail?.type === "text" && tail.text) joinTo = tail;
      }
      // the steps it used reached the limit: what is left is the call for the answer, with no tools to run
      if (step >= maxSteps) {
        status = "max_steps";
        answering = "max_steps";
      }
    }

    for (;;) {
      if (signal?.aborted) { status = "stopped"; break; }
      step++;
      assistant.steps = step;
      const finalCall = step >= maxSteps;
      const req = fitted(finalCall);
      for (const id of req.fit.compacted) compacted.add(id);
      emit({ type: "model.start", step });
      emit({ type: "context", step, bytes: req.fit.bytes, tokens: req.fit.tokens, budget: req.budget, compacted: compacted.size, answering });

      stepRecorded = false;
      try {
        await callModel(req, finalCall);
      } catch (err) {
        // the call for the answer after the step limit failed: the turn ends as it did without it, with its notice;
        // what that call had begun to say is no answer, and is not kept as one
        if (answering !== "max_steps" || isAbort(err) || signal?.aborted) throw err;
        stepText = ""; stepReasoning = ""; stepRecorded = true;
        break;
      }

      if (roundUsage) {
        usage.input += roundUsage.input;
        usage.output += roundUsage.output;
        emit({ type: "usage", input: prior.input + usage.input, output: prior.output + usage.output, step, round: roundUsage });
      }
      recordText(step, stepText, stepReasoning);
      stepRecorded = true;
      lastFinish = done?.finish_reason || null;
      emit({ type: "model.end", step, finish_reason: lastFinish });
      if (done?.wire) turnWire.push(cleanWire(done.wire, format, identity));
      else if (stepText && !done?.refusal) partialWire(stepText);
      if (done?.refusal) { assistant.refusal = done.refusal; break; }
      if (done?.fallback) assistant.fallback = done.fallback;
      if (done?.model && done.model !== provider.model) assistant.servedBy = done.model;
      if (!calls.length) {
        // the model answered on its last allowed call (tool_choice none honoured): the limit was still reached
        if (finalCall && step > 1 && !answering) status = "max_steps";
        ended = true;
        answered = Boolean(stepText.trim());
        break;
      }

      for (const c of calls) assistant.toolCalls.push({ id: c.id, name: c.name, args: c.arguments, ...(c.error ? { error: c.error } : {}) });
      assistant.segments.push({ type: "tools", step, callIds: calls.map((c) => c.id) });

      if (finalCall || answering) {
        // the model asked for tools on the last call it was allowed, or on one that offered none: record them as not
        // run, keep the history valid
        const results = [];
        const why = answering === "context" ? CONTEXT_TEXT : LIMIT_TEXT;
        const summary = answering === "context" ? t("core.agent.context_full") : t("core.agent.max_steps", { n: maxSteps });
        for (const c of calls) {
          // never handed to the router: not executed (a stop of a call that had started would be "cancelled"); `limit`
          // names the limit that left it unrun, so a continued turn never reads it as under way and runs it after all
          const envelope = {
            ...(await synthEnvelope({ tool: c.name, args: c.arguments, status: "cancelled", summary, text: why, outcome: "not_executed" })),
            limit: answering === "context" ? "context" : "max_steps",
          };
          emit({ type: "tool.start", callId: c.id, name: c.name, args: c.arguments, where: null, step });
          emit({ type: "tool.end", callId: c.id, envelope, step });
          toolMessages.push(toolMessage(c, envelope, assistant, step));
          results.push({ id: c.id, text: envelope.text, isError: true });
        }
        turnWire.push(...lib.toolResultMessages(results));
        // even the call for the answer asked for tools: nothing more is asked
        if (answering) break;
        // one more call, for the answer from the results gathered so far
        status = "max_steps";
        answering = "max_steps";
        continue;
      }

      // run in parallel, record in call order
      const outcomes = await Promise.all(calls.map((c) => executeTool(c, step)));
      // in call order, so the numbering does not depend on which call finished first
      for (const o of outcomes) {
        const before = citeNext;
        citeNext = renumberCitations(o.envelope, citeNext);
        if (before > 1 && citeNext > before) emit({ type: "tool.update", callId: o.c.id, envelope: o.envelope, step: o.s });
      }
      for (const o of outcomes) toolMessages.push(toolMessage(o.c, o.envelope, assistant, o.s));
      turnWire.push(...lib.toolResultMessages(outcomes.map(resultOf)));
      if (signal?.aborted) { status = "stopped"; break; }
    }
  } catch (err) {
    if (isAbort(err) || signal?.aborted) {
      status = "stopped";
      emit({ type: "error", message: t("core.agent.stopped"), kind: "aborted" });
    } else {
      status = "error";
      const kind = err?.kind === "network" ? "network" : err?.name === "ProviderError" || err?.status ? "model" : "runtime";
      assistant.error = {
        message: err?.message || String(err), kind, ...(err?.status ? { status: err.status } : {}), ...(err?.type ? { type: err.type } : {}),
        // an error that retrying cannot fix (a daily quota, a conversation too large even after it was shortened): no Retry
        ...(err?.permanent ? { permanent: true } : {}), ...(err?.lang ? { lang: err.lang } : {}),
      };
      emit({ type: "error", message: assistant.error.message, kind });
    }
  }

  // a stopped stream keeps what was said; a turn always ends with every tool call answered
  if (!stepRecorded) {
    recordText(step, stepText, stepReasoning);
    partialWire(stepText);
  }
  assistant.wire = answerDangling(turnWire, format);
  assistant.content = texts.join("\n\n");
  assistant.reasoning = identity ? "" : reasonings.join("\n\n");
  assistant.status = status === "max_steps" ? "ok" : status;
  assistant.usage = { input: prior.input + usage.input, output: prior.output + usage.output };
  assistant.termination = status === "stopped" ? "stopped"
    : status === "error" ? `error:${assistant.error.kind}`
      : assistant.refusal ? "refused_by_model"
        : ended && CUT_OFF.has(lastFinish) ? "truncated"
          : answering === "context" ? (answered ? "context_synthesized" : "context_full")
            : status === "max_steps" ? (answered ? "max_steps_synthesized" : "max_steps")
              : "answered";
  const notices = [];
  if (status === "max_steps") {
    assistant.limitReached = true;
    notices.push(t("core.agent.max_steps", { n: maxSteps }));
  }
  if (answering === "context") {
    notices.push(t(answered ? "core.agent.context_full" : "core.agent.context_failed"));
    // a local server or a custom endpoint: its window is what Settings say (32 000 until set), maybe less than it takes
    if (provider.local || provider.custom) notices.push(t("core.agent.context_window", { n: Math.floor(Number(provider.contextTokens)) || DEFAULT_CONTEXT_TOKENS }));
  }
  if (assistant.termination === "truncated") notices.push(t("core.agent.truncated"));
  if (notices.length) assistant.notice = notices.join(" ");
  if (compacted.size) assistant.compacted = compacted.size;
  assistant.finishReason = lastFinish;
  assistant.citeTop = citeNext - 1;
  emit({ type: "done", status, termination: assistant.termination });
  return { assistant, toolMessages, usage: { ...usage }, status, steps: step };

  /** One model call, with its retries: transient errors twice, and a request too long for the model once, shortened. */
  async function callModel(initial, finalCall) {
    let req = initial;
    for (let attempt = 0; ; attempt++) {
      stepText = ""; stepReasoning = ""; calls = []; done = null; roundUsage = null;
      try {
        const stream = lib.streamChat({ provider, system, messages: req.messages, tools: req.tools, signal, thinking, toolChoice: req.toolChoice });
        for await (const ev of stream) {
          if (ev.type === "text") { stepText += ev.delta; emit({ type: "text", delta: ev.delta, step, ...(joinTo ? { joins: true } : {}) }); }
          else if (ev.type === "reasoning") { if (!identity) { stepReasoning += ev.delta; emit({ type: "reasoning", delta: ev.delta, step }); } }
          else if (ev.type === "tool_call_delta") emit({ type: "tool.pending", index: ev.index, name: ev.name, step });
          else if (ev.type === "tool_call") calls.push(ev);
          else if (ev.type === "usage") roundUsage = { input: ev.input || 0, output: ev.output || 0 };
          else if (ev.type === "done") done = ev;
        }
        return;
      } catch (err) {
        if (isAbort(err) || signal?.aborted) throw err;
        if (isCompactable(err)) {
          // the model or the relay found the request too long: shortened, once in the turn; a second time it is final
          const next = overflowRetried ? null : shorter(req, finalCall);
          if (!next) { err.permanent = true; throw err; }
          overflowRetried = true;
          req = next;
          for (const id of req.fit.compacted) compacted.add(id);
          emit({ type: "model.retry", step, attempt: attempt + 1, delay_ms: 0, message: err.message || String(err), notice: t("core.agent.compact_retry") });
          emit({ type: "context", step, bytes: req.fit.bytes, tokens: req.fit.tokens, budget: req.budget, compacted: compacted.size, answering });
          attempt--; // not one of the transient retries
          continue;
        }
        if (!err?.retryable || attempt >= retryDelays.length) throw err;
        // a service that says how long to wait is believed: a retry inside its window only fails again
        const after = retryAfterMs(err);
        if (after !== null && after > MAX_RETRY_AFTER_S * 1000) throw err;
        const delay = Math.max(retryDelays[attempt], after ?? 0);
        emit({ type: "model.retry", step, attempt: attempt + 1, delay_ms: delay, message: err.message || String(err), notice: t("core.agent.retry", { n: attempt + 1 }) });
        stepText = ""; stepReasoning = ""; // the UI drops the failed attempt's words; so does the record
        await sleep(delay, signal);
      }
    }
  }

  /**
   * The request again, shorter than the one refused: the rest of the turn keeps under a ceiling below it. Compacting
   * earlier results comes first, with the tools still offered, when that brings it under the ceiling; when it does not,
   * the request asks for the answer instead (the tools go, the newest results may be shortened): the one retry is never
   * spent on a request barely shorter than the one refused. null when it cannot be made shorter.
   */
  function shorter(req, finalCall) {
    ceiling = { tokens: Math.floor(req.fit.tokens * OVERFLOW_SHRINK), bytes: Math.floor(req.fit.bytes * OVERFLOW_SHRINK) };
    const kept = request(finalCall, answering);
    if (kept.fit.fits && kept.fit.bytes < req.fit.bytes) return kept;
    const next = fitted(finalCall);
    return next.fit.bytes < req.fit.bytes ? next : null;
  }

  /** A stateful call that was under way when a continued turn stopped: reported, never run again. */
  async function unknownOutcome(c, s) {
    emit({ type: "tool.start", callId: c.id, name: c.name, args: c.arguments, where: null, step: s });
    const envelope = await synthEnvelope({
      tool: c.name, args: c.arguments, status: "failed", summary: t("core.agent.unknown_state"), text: UNKNOWN_TEXT,
      error: { type: "interrupted", message: "under way when the turn stopped; its effect is unknown", hint: "check its state (job_status) before submitting it again" },
    });
    emit({ type: "tool.end", callId: c.id, envelope, step: s });
    return { c, s, envelope };
  }

  async function executeTool(c, s) {
    let envelope;
    if (c.error) {
      emit({ type: "tool.start", callId: c.id, name: c.name, args: {}, raw: c.raw, where: null, step: s });
      envelope = await synthEnvelope({
        tool: c.name, args: {}, status: "failed", summary: c.error,
        text: `Not run: ${c.error}. The arguments received were: ${clip(c.raw || "", 300)}. Send one JSON object that matches the tool's parameters.`,
        error: { type: "bad_arguments", message: c.error, hint: "send a JSON object matching the parameters" },
      });
    } else {
      let where = null;
      try { where = router?.whereCall ? router.whereCall(c.name, c.arguments) : router?.where?.(c.name) ?? null; } catch { where = null; }
      emit({ type: "tool.start", callId: c.id, name: c.name, args: c.arguments, where, step: s });
      const fkey = failureKey(router, c);
      const past = failures.get(fkey) || [];
      if (past.length >= FAILURE_BUDGET || past.some((p) => p.final)) {
        envelope = await budgetEnvelope(c, fkey, past);
        emit({ type: "tool.end", callId: c.id, envelope, step: s });
        return { c, s, envelope };
      }
      const reuse = reusePolicy(router, c);
      if (reuse.stateful) seen.clear();
      const prior = reuse.key ? seen.get(reuse.key) : null;
      if (prior) {
        const first = await prior.envelope;
        if (settled(first)) envelope = reusedEnvelope(first, prior);
      }
      if (!envelope) {
        const run = (async () => {
          try {
            return router
              ? await router.call(c.name, c.arguments, { projectId, conversationId, signal, callId: c.id, onApproval: approvalHandler(c.id) })
              : await synthEnvelope({ tool: c.name, args: c.arguments, status: "failed", summary: "no router", text: "No tool runtime is available.", error: { type: "unavailable", message: "no router", hint: "" } });
          } catch (err) {
            return synthEnvelope({
              tool: c.name, args: c.arguments, status: "failed", summary: err?.message || String(err), text: `The tool failed to run: ${err?.message || err}. There is no result.`,
              error: { type: "runtime_error", message: err?.message || String(err), hint: "" },
            });
          }
        })();
        if (reuse.key) seen.set(reuse.key, { step: s, callId: c.id, envelope: run });
        envelope = await run;
      }
      if (!envelope.reused) noteFailure(fkey, c, s, envelope);
    }
    emit({ type: "tool.end", callId: c.id, envelope, step: s });
    return { c, s, envelope };
  }

  function noteFailure(key, c, s, envelope, { quiet = false } = {}) {
    if (envelope.status === "succeeded" || envelope.status === "job_submitted") { failures.delete(key); return; }
    if (envelope.status !== "failed" && envelope.status !== "refused") return; // a stop is not the tool's failure
    const [code] = outcomeOf(envelope) || ["tool_failed"];
    const list = [...(failures.get(key) || []), {
      step: s, callId: c.id, type: envelope.error?.type || envelope.status, message: envelope.error?.message || "",
      final: FINAL_OUTCOMES.has(code),
    }];
    failures.set(key, list);
    if (quiet) return; // a continued turn's earlier failures: counted, already told
    const last = list[list.length - 1];
    const who = named(c, key);
    if (last.final) notes.set(c.id, `This failure will not change on a retry: ${who} is not called again in this turn.`);
    else if (list.length === FAILURE_BUDGET - 1) notes.set(c.id, `${who} has now failed ${list.length} times in this turn; one more failure and it is not called again in this turn. Change what you send (see the error), or take another route.`);
    else if (list.length >= FAILURE_BUDGET) notes.set(c.id, `${who} has failed ${list.length} times in this turn and is not called again in it. Change approach.`);
  }

  function resultOf({ c, envelope }) {
    const outcome = envelope.reused || envelope.budget ? null : outcomeOf(envelope);
    const lines = [outcome ? `Outcome: ${outcome[0]} — ${outcome[1]}.` : "", notes.get(c.id) || ""].filter(Boolean);
    // the project's own repository and pages are not shown in Studio (publiclinks.js): the model is not given them to repeat
    const body = scrubProjectRefs(envelope.text || (envelope.ok ? envelope.summary || "{}" : `Failed: ${envelope.error?.message || envelope.summary || "no result"}`));
    const tail = lines.length ? `\n${lines.join("\n")}` : "";
    const text = clip(body, RESULT_CHARS - tail.length) + tail;
    return { id: c.id, text, isError: envelope.status === "failed" || envelope.status === "cancelled" };
  }

  function approvalHandler(callId) {
    if (!onApproval && !onEvent) return undefined; // nobody to ask: the router answers needs_approval
    return (request) => {
      const req = { ...request, callId };
      if (onApproval) {
        emit({ type: "tool.approval", callId, request: req });
        return onApproval(req);
      }
      return new Promise((resolve) => {
        let settled = false;
        req.respond = (decision) => { if (!settled) { settled = true; resolve(decision); } };
        emit({ type: "tool.approval", callId, request: req });
      });
    };
  }
}

/** How long a failed call says to wait (Retry-After), in ms; null when it does not say. */
function retryAfterMs(err) {
  const v = err?.retryAfter;
  if (v === null || v === undefined || v === "") return null;
  const s = Number(v);
  return Number.isFinite(s) && s >= 0 ? Math.round(s * 1000) : null;
}

/**
 * The highest citation number the earlier turns of `history` used: their recorded `citeTop`, or the [E#] marks of the
 * tool results they replay (records made before citeTop was kept).
 */
export function citationsUsed(history) {
  let top = 0;
  const scan = (text) => {
    if (typeof text !== "string") return;
    for (const m of text.matchAll(/\[E(\d+)\]/g)) top = Math.max(top, Number(m[1]));
  };
  for (const m of history || []) {
    if (m?.role !== "assistant") continue;
    if (Number.isFinite(m.citeTop)) top = Math.max(top, m.citeTop);
    for (const msg of Array.isArray(m.wire) ? m.wire : []) {
      if (msg?.role === "tool") scan(msg.content);
      else if (msg?.role === "user" && Array.isArray(msg.content)) {
        for (const b of msg.content) if (b?.type === "tool_result") scan(typeof b.content === "string" ? b.content : "");
      }
    }
  }
  return top;
}

/**
 * Number one envelope's citations after those already used in this conversation. Every envelope numbers its own from
 * E1, so a second tool's E1 would be ambiguous to the model and to the reader; the ids are shifted (E1 → E5 when E1–E4
 * are taken) in `citations` and in the `[E#]` marks of the text the model reads. Mutates the envelope; returns the
 * next free number. The first envelope of a conversation is left as it is.
 */
export function renumberCitations(envelope, next = 1) {
  const cits = Array.isArray(envelope?.citations) ? envelope.citations : [];
  let top = 0;
  for (const c of cits) {
    const m = /^E(\d+)$/.exec(String(c?.id || ""));
    if (m) top = Math.max(top, Number(m[1]));
  }
  if (!top) return next;
  const offset = next - 1;
  if (offset > 0) {
    for (const c of cits) {
      const m = /^E(\d+)$/.exec(String(c?.id || ""));
      if (m) c.id = `E${Number(m[1]) + offset}`;
    }
    if (typeof envelope.text === "string") {
      envelope.text = envelope.text.replace(/\[E(\d+)\]/g, (all, n) => (Number(n) <= top ? `[E${Number(n) + offset}]` : all));
    }
  }
  return next + top;
}

// ------------------------------------------------------------------------------------------------- budget, resume

const callsIn = (m) => m?.role === "assistant" && ((Array.isArray(m.tool_calls) && m.tool_calls.length > 0)
  || (Array.isArray(m.content) && m.content.some((b) => b?.type === "tool_use")));
const isResults = (m) => m?.role === "tool" || (m?.role === "user" && Array.isArray(m.content) && m.content.some((b) => b?.type === "tool_result"));

/**
 * {bytes, units} (budget.js measure) of a request without its messages: the body the provider's client builds for it
 * (system prompt, tools, parameters), plus the message that asks for the answer when there is one.
 */
function fixedCost({ provider, system, tools, thinking, toolChoice, format, ask }) {
  let body;
  try {
    body = (format === "anthropic" ? anthropicLib : openaiLib).buildRequest({ provider, system, messages: [], tools, thinking, toolChoice }).body;
  } catch {
    body = { model: provider.model, system, messages: [], tools };
  }
  let { bytes, units } = measure(JSON.stringify(body));
  // each message adds itself and a comma (budget.js costOf); into an empty array, one comma fewer
  if (!(Array.isArray(body.messages) && body.messages.length)) { bytes -= 1; units -= 0.25; }
  if (ask) {
    const a = measure(JSON.stringify({ role: "user", content: ask }));
    bytes += a.bytes + 1;
    units += a.units + 0.25;
  }
  return { bytes, units };
}

/** The request's messages with the text that asks for the answer as the last user message (a copy). */
function withAsk(messages, text, format) {
  const last = messages[messages.length - 1];
  if (last?.role === "user") {
    // two user messages never follow each other (several chat templates reject it): it joins the last one
    const content = Array.isArray(last.content) ? [...last.content, { type: "text", text }]
      : format === "anthropic" ? [{ type: "text", text: String(last.content ?? "") }, { type: "text", text }]
        : `${last.content ?? ""}\n\n${text}`;
    return [...messages.slice(0, -1), { ...last, content }];
  }
  return [...messages, format === "anthropic" ? { role: "user", content: [{ type: "text", text }] } : { role: "user", content: text }];
}

// under way when the turn stopped: no result, or a stop's (or a pending approval's). Never a call a limit left unrun
// (`limit`): it was never handed to the router, and a continued turn does not run it after all. The router's own
// not_executed stop (stopped before the runtime had it) is under way: running it again is what the call was for
const underWay = (env) => !env || (!env.limit && (env.status === "cancelled" || env.status === "needs_approval"));
/** A result a continued turn keeps: settled, not a placeholder for a call a limit left unrun. */
const keptResult = (env) => !underWay(env) && !env.limit;

/**
 * A stored turn as runTurn continues it (`resume` = the stored assistant message's wire, wireFormat, status,
 * segments, steps, citeTop, usage, and its toolCalls each with the `envelope` of its tool record, null when it has
 * none): {wire (a copy, every call answered; on Anthropic without its thinking blocks, which bound a system prompt
 * rebuilt since), segments, toolCalls (with their step), unsettled (the calls of its last tool step that were under
 * way when it stopped, or never answered), steps, citeTop, thinkingDropped}.
 */
export function resumeOf(resume, format) {
  let wire = structuredClone(Array.isArray(resume?.wire) ? resume.wire : []);
  let thinkingDropped = false;
  if (format === "anthropic") {
    wire = wire.map((m) => {
      if (m?.role !== "assistant" || !Array.isArray(m.content)) return m;
      const content = m.content.filter((b) => b?.type !== "thinking" && b?.type !== "redacted_thinking");
      if (content.length !== m.content.length) thinkingDropped = true;
      return { ...m, content };
    }).filter((m) => !(m?.role === "assistant" && Array.isArray(m.content) && !m.content.length));
  }
  wire = answerDangling(wire, format);
  const segments = structuredClone(Array.isArray(resume?.segments) ? resume.segments : []);
  const stepOf = new Map();
  for (const sg of segments) if (sg?.type === "tools") for (const id of sg.callIds || []) stepOf.set(id, sg.step);
  const toolCalls = (resume?.toolCalls || []).map((c) => ({ ...c, envelope: c.envelope || null, step: stepOf.get(c.id) ?? null }));
  const lastTools = [...segments].reverse().find((sg) => sg?.type === "tools");
  const open = resume?.status === "error" || resume?.status === "stopped";
  const unsettled = open && lastTools ? toolCalls.filter((c) => (lastTools.callIds || []).includes(c.id) && !c.error && underWay(c.envelope)) : [];
  const steps = Math.max(Number(resume?.steps) || 0, 0, ...segments.map((sg) => Number(sg?.step) || 0));
  const citeTop = Math.max(Number(resume?.citeTop) || 0, citationsUsed([{ role: "assistant", wire }]));
  return { wire, segments, toolCalls, unsettled, steps, citeTop, thinkingDropped };
}

/** Put new results into a turn's messages in place of the ones they answer (next to the step that asked for them). */
function replaceResults(wire, format, msgs) {
  if (format === "anthropic") {
    const blocks = msgs.flatMap((m) => (Array.isArray(m.content) ? m.content : []));
    const ids = new Set(blocks.map((b) => b.tool_use_id));
    const at = wire.findLastIndex((m) => m.role === "assistant" && Array.isArray(m.content) && m.content.some((b) => b?.type === "tool_use" && ids.has(b.id)));
    const next = at >= 0 ? wire[at + 1] : null;
    if (at < 0) wire.push(...msgs);
    else if (next?.role === "user" && Array.isArray(next.content)) next.content = [...next.content.filter((b) => !(b?.type === "tool_result" && ids.has(b.tool_use_id))), ...blocks];
    else wire.splice(at + 1, 0, { role: "user", content: blocks });
    return;
  }
  const ids = new Set(msgs.map((m) => m.tool_call_id));
  const at = wire.findLastIndex((m) => m.role === "assistant" && (m.tool_calls || []).some((tc) => ids.has(tc.id)));
  if (at < 0) { wire.push(...msgs); return; }
  let end = at + 1;
  while (end < wire.length && wire[end].role === "tool") end++;
  const kept = wire.slice(at + 1, end).filter((m) => !ids.has(m.tool_call_id));
  wire.splice(at + 1, end - at - 1, ...kept, ...msgs);
}

/**
 * Can this stored answer be continued with `provider`: "continue" (it ended with an error or a stop, and holds a
 * settled tool result), "answer" (its answer was cut off at the output limit), or null. Only on the provider that
 * made it, whose format its stored messages are in; otherwise only Retry (a new answer) is offered. `tools`: its tool
 * records (with their envelopes).
 */
export function canContinue(message, provider, tools = []) {
  if (!message || message.role !== "assistant" || message.draft || !provider || provider.needsKey) return null;
  if (!Array.isArray(message.wire) || !message.wire.length) return null;
  if (message.provider !== provider.id || (message.wireFormat || "openai") !== formatOf(provider)) return null;
  if (message.status === "ok" && message.termination === "truncated") return "answer";
  if (message.status !== "error" && message.status !== "stopped") return null;
  const byCall = new Map(tools.map((m) => [m.toolCallId, m.envelope]));
  return (message.toolCalls || []).some((c) => keptResult(byCall.get(c.id))) ? "continue" : null;
}

function toolMessage(c, envelope, assistant, step) {
  return {
    id: uuid(), conversationId: assistant.conversationId, parentId: assistant.id, role: "tool", createdAt: Date.now(),
    toolCallId: c.id, name: c.name, args: c.arguments, content: envelope.text || "", envelope, step,
    where: envelope.receipt?.where || null, status: envelope.status,
  };
}

/** A provider message as it is kept for replay: an inline <think> stripped; on an identity turn, all reasoning. */
function cleanWire(wire, format, identity) {
  const w = structuredClone(wire);
  if (format === "openai" && typeof w.content === "string") w.content = stripThink(w.content);
  if (identity) {
    delete w.reasoning_details;
    delete w.reasoning_content;
    delete w.reasoning;
    if (Array.isArray(w.content)) w.content = w.content.filter((b) => b.type !== "thinking" && b.type !== "redacted_thinking");
  }
  return w;
}

// ------------------------------------------------------------------------------------------------- history

/** The provider messages for the stored history (without the system prompt), in `provider`'s wire format. */
export function buildWireHistory(history, provider) {
  const format = formatOf(provider);
  const out = [];
  for (const m of history || []) {
    if (m.role === "user") {
      const text = userText(m);
      const last = out[out.length - 1];
      // the turn in between left nothing to replay (it failed or was stopped before it said anything): one user
      // message with both questions, so the roles still alternate (results of tools, in user messages, are left alone)
      if (last?.role === "user" && typeof last.content === "string") last.content = `${last.content}\n\n${text}`;
      else out.push({ role: "user", content: text });
      continue;
    }
    if (m.role !== "assistant") continue;
    const wireFormat = m.wireFormat || inferFormat(m.wire);
    if (Array.isArray(m.wire) && m.wire.length && wireFormat === format) {
      out.push(...prepareWire(m.wire, format, { sameProvider: m.provider === provider.id, provider, hideReasoning: Boolean(m.hideReasoning) }));
    } else {
      const text = stripThink(typeof m.content === "string" ? m.content : "").trim();
      if (text) out.push({ role: "assistant", content: format === "anthropic" ? [{ type: "text", text }] : text });
    }
  }
  return out;
}

function userText(m) {
  const text = typeof m.content === "string" ? m.content : "";
  const names = Array.isArray(m.attachmentNames) ? m.attachmentNames : [];
  return names.length ? `${text}\n\n[attached: ${names.join(", ")}]` : text;
}

function inferFormat(wire) {
  if (!Array.isArray(wire) || !wire.length) return null;
  for (const msg of wire) {
    if (msg.role === "tool" || msg.tool_calls) return "openai";
    if (Array.isArray(msg.content) && msg.content.some((b) => b && ["tool_use", "tool_result", "thinking", "text"].includes(b.type))) return "anthropic";
  }
  return "openai";
}

/** An earlier turn's wire, made safe to send again. */
export function prepareWire(wire, format, { sameProvider = false, provider = {}, hideReasoning = false } = {}) {
  const copy = structuredClone(wire);
  const out = [];
  for (const msg of copy) {
    if (format === "openai") {
      if (msg.role === "assistant") {
        if (!(sameProvider && provider.reasoning === "reasoning_details" && !hideReasoning)) delete msg.reasoning_details;
        delete msg.reasoning_content; // earlier turns' reasoning is not sent back (DeepSeek rejects it)
        delete msg.reasoning;
        if (typeof msg.content === "string") msg.content = stripThink(msg.content);
        if (!msg.content && !msg.tool_calls?.length) continue;
      }
      if (msg.role === "tool") msg.content = shorten(msg.content);
    } else {
      if (msg.role === "assistant" && Array.isArray(msg.content)) {
        msg.content = msg.content.filter((b) => {
          // an earlier turn's thinking is never replayed, for any provider: its signature binds the system prompt and
          // the messages before it exactly as they were, and this history has changed since (see the top of this file)
          if (b.type === "thinking" || b.type === "redacted_thinking") return false;
          if (b.type === "text") return Boolean(b.text);
          return true;
        });
        if (!msg.content.length) continue;
      }
      if (msg.role === "user" && Array.isArray(msg.content)) {
        for (const block of msg.content) if (block.type === "tool_result" && typeof block.content === "string") block.content = shorten(block.content);
      }
    }
    out.push(msg);
  }
  return answerDangling(out, format);
}

function shorten(text) {
  if (typeof text !== "string" || text.length <= OLD_RESULT_CHARS) return text;
  return `${text.slice(0, OLD_RESULT_CHARS)}… [an earlier tool result, cut from ${text.length} characters; call the tool again if you need the rest]`;
}

/** Every tool call gets its result before the conversation goes on: a placeholder for any that has none. */
export function answerDangling(msgs, format) {
  const out = [];
  for (let i = 0; i < msgs.length; i++) {
    const msg = msgs[i];
    out.push(msg);
    if (format === "openai" && msg.role === "assistant" && msg.tool_calls?.length) {
      const answered = new Set();
      let j = i + 1;
      while (j < msgs.length && msgs[j].role === "tool") { answered.add(msgs[j].tool_call_id); out.push(msgs[j]); j++; }
      for (const tc of msg.tool_calls) if (!answered.has(tc.id)) out.push({ role: "tool", tool_call_id: tc.id, content: DANGLING_TEXT });
      i = j - 1;
    }
    if (format === "anthropic" && msg.role === "assistant" && Array.isArray(msg.content)) {
      const ids = msg.content.filter((b) => b.type === "tool_use").map((b) => b.id);
      if (!ids.length) continue;
      const next = msgs[i + 1];
      const got = new Set(next?.role === "user" && Array.isArray(next.content) ? next.content.filter((b) => b.type === "tool_result").map((b) => b.tool_use_id) : []);
      const missing = ids.filter((id) => !got.has(id));
      if (!missing.length) continue;
      const blocks = missing.map((id) => ({ type: "tool_result", tool_use_id: id, content: DANGLING_TEXT, is_error: true }));
      if (next?.role === "user" && Array.isArray(next.content)) next.content = [...next.content, ...blocks];
      else out.push({ role: "user", content: blocks });
    }
  }
  return out;
}

/** A turn's messages without the model's reasoning (identity turns keep none). */
export function withoutReasoning(wire) {
  return (wire || []).map((msg) => (msg.role === "assistant" ? cleanWire(msg, Array.isArray(msg.content) ? "anthropic" : "openai", true) : structuredClone(msg)));
}
