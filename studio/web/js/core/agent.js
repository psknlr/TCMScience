// The agent loop: the model answers or asks for tools; the router runs them where they can run; their results go
// back; until the model answers, the user stops it, or the step limit is reached.
//
// History discipline. Each assistant message keeps the exact provider messages of its turn (`wire`) and is replayed
// verbatim to a provider of the same format: reasoning a provider needs back stays for that provider only
// (reasoning_details for the same provider; Anthropic thinking blocks for the same provider), other reasoning fields
// are dropped, an inline <think> is stripped, results of earlier turns are cut to 2 500 characters, and a tool call
// that never got its result (a stopped turn) is answered with a placeholder so the history stays valid. A turn made in
// the other format is replayed as its text.

import * as anthropicLib from "./llm/anthropic.js";
import * as openaiLib from "./llm/openai.js";
import { stripThink } from "./llm/openai.js";
import { toProviderTools } from "./catalog.js";
import { t } from "./i18n.js";
import { isIdentityQuestion } from "./prompt.js";
import { synthEnvelope } from "./router.js";
import { clip, isAbort, sleep as defaultSleep, uuid } from "./util.js";

export const MAX_STEPS = 16;
export const OLD_RESULT_CHARS = 2500;
export const RESULT_CHARS = 16000;
export const RETRY_DELAYS = [1500, 4000];
export const DANGLING_TEXT = "(this call was stopped before it finished; there is no result)";
export const LIMIT_TEXT = "Not run: the limit of model calls for this turn was reached. Answer from the results you have.";

export const formatOf = (provider) => (provider?.format === "anthropic" ? "anthropic" : "openai");
const libFor = (format, llm) => (llm?.[format]) || (format === "anthropic" ? anthropicLib : openaiLib);

/**
 * Run one turn. `history` is the branch so far as stored messages (CONTRACTS §7), ending with the new user message.
 * onEvent receives: model.start {step} · text {delta, step} · reasoning {delta, step} · tool.pending {index, name,
 * step} · tool.start {callId, name, args, where, step} · tool.approval {callId, request} (request.respond(decision)
 * when no onApproval is given) · tool.end {callId, envelope, step} · usage {input, output, step, round} ·
 * model.retry {step, attempt, delay_ms, message} (drop that step's partial text) · model.end {step, finish_reason} ·
 * error {message, kind} · done {status}.
 * Returns {assistant, toolMessages, usage, status, steps}.
 */
export async function runTurn({
  provider, system, history = [], router, catalog, settings = {}, signal, onEvent, projectId = null, conversationId = null,
  onApproval, tools, llm, retryDelays = RETRY_DELAYS, sleep = defaultSleep,
}) {
  const format = formatOf(provider);
  const lib = libFor(format, llm);
  const coreTools = tools ?? catalog?.core ?? [];
  const providerTools = provider.tools === false ? [] : toProviderTools(coreTools, format);
  const lastUser = [...history].reverse().find((m) => m.role === "user") || null;
  const identity = Boolean(provider.relay && isIdentityQuestion(lastUser?.content));
  const thinking = settings.thinking !== false && !identity;
  const maxSteps = Math.max(1, Math.min(64, Math.floor(Number(settings.maxSteps) || MAX_STEPS)));
  const base = buildWireHistory(history, provider);
  const turnWire = [];
  const usage = { input: 0, output: 0 };
  const texts = [];
  const reasonings = [];
  const toolMessages = [];
  const emit = (ev) => {
    try { onEvent?.(ev); } catch (err) { (globalThis.reportError || console.error)(err); }
  };
  const assistant = {
    id: uuid(), conversationId, parentId: lastUser?.id ?? null, role: "assistant", createdAt: Date.now(),
    content: "", reasoning: "", toolCalls: [], segments: [], provider: provider.id, model: provider.model,
    wireFormat: format, wire: turnWire, usage, status: "ok", steps: 0,
  };
  if (identity) assistant.hideReasoning = true;

  let status = "ok";
  let step = 0;
  let citeNext = 1; // citations are numbered across the turn (renumberCitations)
  let lastFinish = null;
  // the current step's stream, kept here so a stop or an error keeps what was already said
  let stepText = "";
  let stepReasoning = "";
  let stepRecorded = true;

  const recordText = (s, text, reasoning) => {
    if (reasoning && !identity) {
      reasonings.push(reasoning);
      assistant.segments.push({ type: "reasoning", step: s, text: reasoning });
    }
    if (text) {
      texts.push(text);
      assistant.segments.push({ type: "text", step: s, text });
    }
  };
  const partialWire = (text) => {
    if (!text) return;
    turnWire.push(format === "anthropic" ? { role: "assistant", content: [{ type: "text", text }] } : { role: "assistant", content: text });
  };

  try {
    for (;;) {
      if (signal?.aborted) { status = "stopped"; break; }
      step++;
      assistant.steps = step;
      const finalCall = step >= maxSteps;
      emit({ type: "model.start", step });

      stepRecorded = false;
      let calls = [];
      let done = null;
      let roundUsage = null;
      for (let attempt = 0; ; attempt++) {
        stepText = ""; stepReasoning = ""; calls = []; done = null; roundUsage = null;
        try {
          const stream = lib.streamChat({
            provider, system, messages: [...base, ...turnWire], tools: providerTools, signal, thinking,
            toolChoice: finalCall && step > 1 && providerTools.length ? "none" : undefined,
          });
          for await (const ev of stream) {
            if (ev.type === "text") { stepText += ev.delta; emit({ type: "text", delta: ev.delta, step }); }
            else if (ev.type === "reasoning") { if (!identity) { stepReasoning += ev.delta; emit({ type: "reasoning", delta: ev.delta, step }); } }
            else if (ev.type === "tool_call_delta") emit({ type: "tool.pending", index: ev.index, name: ev.name, step });
            else if (ev.type === "tool_call") calls.push(ev);
            else if (ev.type === "usage") roundUsage = { input: ev.input || 0, output: ev.output || 0 };
            else if (ev.type === "done") done = ev;
          }
          break;
        } catch (err) {
          if (isAbort(err) || signal?.aborted) throw err;
          if (!err?.retryable || attempt >= retryDelays.length) throw err;
          const delay = retryDelays[attempt];
          emit({ type: "model.retry", step, attempt: attempt + 1, delay_ms: delay, message: err.message || String(err), notice: t("core.agent.retry", { n: attempt + 1 }) });
          stepText = ""; stepReasoning = ""; // the UI drops the failed attempt's words; so does the record
          await sleep(delay, signal);
        }
      }

      if (roundUsage) {
        usage.input += roundUsage.input;
        usage.output += roundUsage.output;
        emit({ type: "usage", input: usage.input, output: usage.output, step, round: roundUsage });
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
        if (finalCall && step > 1) status = "max_steps";
        break;
      }

      for (const c of calls) assistant.toolCalls.push({ id: c.id, name: c.name, args: c.arguments, ...(c.error ? { error: c.error } : {}) });
      assistant.segments.push({ type: "tools", step, callIds: calls.map((c) => c.id) });

      if (finalCall) {
        // the model asked for tools on the last call it was allowed: record them as not run, keep the history valid
        const results = [];
        for (const c of calls) {
          const envelope = await synthEnvelope({
            tool: c.name, args: c.arguments, status: "cancelled", summary: t("core.agent.max_steps", { n: maxSteps }), text: LIMIT_TEXT,
          });
          emit({ type: "tool.start", callId: c.id, name: c.name, args: c.arguments, where: null, step });
          emit({ type: "tool.end", callId: c.id, envelope, step });
          toolMessages.push(toolMessage(c, envelope, assistant, step));
          results.push({ id: c.id, text: envelope.text, isError: true });
        }
        turnWire.push(...lib.toolResultMessages(results));
        status = "max_steps";
        break;
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
      assistant.error = { message: err?.message || String(err), kind, ...(err?.status ? { status: err.status } : {}), ...(err?.type ? { type: err.type } : {}) };
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
  if (status === "max_steps") {
    assistant.limitReached = true;
    assistant.notice = t("core.agent.max_steps", { n: maxSteps });
  }
  assistant.finishReason = lastFinish;
  emit({ type: "done", status });
  return { assistant, toolMessages, usage: { ...usage }, status, steps: step };

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
      try {
        envelope = router
          ? await router.call(c.name, c.arguments, { projectId, conversationId, signal, callId: c.id, onApproval: approvalHandler(c.id) })
          : await synthEnvelope({ tool: c.name, args: c.arguments, status: "failed", summary: "no router", text: "No tool runtime is available.", error: { type: "unavailable", message: "no router", hint: "" } });
      } catch (err) {
        envelope = await synthEnvelope({
          tool: c.name, args: c.arguments, status: "failed", summary: err?.message || String(err), text: `The tool failed to run: ${err?.message || err}. There is no result.`,
          error: { type: "runtime_error", message: err?.message || String(err), hint: "" },
        });
      }
    }
    emit({ type: "tool.end", callId: c.id, envelope, step: s });
    return { c, s, envelope };
  }

  function resultOf({ c, envelope }) {
    const text = clip(envelope.text || (envelope.ok ? envelope.summary || "{}" : `Failed: ${envelope.error?.message || envelope.summary || "no result"}`), RESULT_CHARS);
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

/**
 * Number one envelope's citations after those already used in this turn. Every envelope numbers its own from E1, so a
 * second tool's E1 would be ambiguous to the model and to the reader; the ids are shifted (E1 → E5 when E1–E4 are
 * taken) in `citations` and in the `[E#]` marks of the text the model reads. Mutates the envelope; returns the next
 * free number. The first envelope of a turn is left as it is.
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
      out.push({ role: "user", content: userText(m) });
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
          if (b.type === "thinking" || b.type === "redacted_thinking") return sameProvider && !hideReasoning;
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
