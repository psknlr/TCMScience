// Anthropic Messages API, streamed over fetch + SSE (no SDK: the app has no runtime dependencies).
//
// Browser calls carry `anthropic-dangerous-direct-browser-access: true`; the key is the user's own and is sent only to
// the provider (or through the local runner's proxy). Current models take adaptive thinking with a summarized display
// (shown as reasoning, never as evidence); older ones a token budget. The assistant content is returned verbatim
// (`wire`), so thinking blocks and their signatures round-trip within the turn that produced them (the agent does not
// replay them in later turns: see agent.js). Tool inputs are parsed strictly; a
// turn that stops on max_tokens or a refusal never runs its tools. On the first-party API the newest models are asked
// for server-side refusal fallbacks (`fallbacks: "default"`): a declined request is re-run on the model Anthropic
// recommends for that category, inside the same call, and the reply says which model answered.

import { t } from "../i18n.js";
import { ProviderError, isRetryableStatus, parseErrorBody, readSSE, retryAfterSeconds } from "./sse.js";
import { parseArguments, unreachable } from "./openai.js";

export const ANTHROPIC_VERSION = "2023-06-01";
const FALLBACK_BETA = "server-side-fallback-2026-07-01";
const FALLBACK_MODELS = new Set(["claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"]);
// Preserved thinking: on these models a thinking block is valid only in the conversation that produced it. The agent
// replays blocks only within their own turn (agent.js), so none should fail the check; should one still fail (a block
// after a mid-output fallback, whose earlier blocks are not replayed), the first-party API is asked to drop it rather
// than refuse the whole request. The field needs this beta header, and is a 400 without it.
export const BINDING_BETA = "thinking-binding-controls-2026-08-01";
const BINDING_MODELS = new Set(["claude-fable-5-1", "claude-opus-5-5", "claude-sonnet-5-5"]);
const RETRYABLE_TYPES = new Set(["overloaded_error", "api_error", "rate_limit_error", "timeout_error"]);

const isFirstParty = (url) => { try { return new URL(url).hostname === "api.anthropic.com"; } catch { return false; } };

/** Models that still take {type:"enabled", budget_tokens}; everything newer takes adaptive thinking. */
const BUDGET_MODELS = /claude-(?:3|haiku-4-5|sonnet-4-5|opus-4-5|opus-4-1|opus-4-0|sonnet-4-0|opus-4-20|sonnet-4-20)/;
/** Models on which temperature/top_p were removed (a 400 if sent). */
const NO_SAMPLING = /claude-(?:fable|mythos|opus-5|sonnet-5|opus-4-[78])/;

export function thinkingParam(model, on, maxTokens, firstParty) {
  if (!on) return undefined; // never "disabled": several current models answer that with a 400
  const m = String(model || "");
  if (!firstParty && !/^claude-/.test(m)) return undefined; // an Anthropic-compatible endpoint of another service
  if (BUDGET_MODELS.test(m)) {
    const budget = Math.min(16000, Math.floor((Number(maxTokens) || 8192) - 1024));
    return budget >= 1024 ? { type: "enabled", budget_tokens: budget } : undefined;
  }
  return { type: "adaptive", display: "summarized" };
}

export function anthropicTools(tools, { eager = false } = {}) {
  return (tools || []).map((tool) => {
    if (tool.input_schema && !tool.function) return eager ? { ...tool, eager_input_streaming: true } : tool;
    const fn = tool.function || tool;
    const out = { name: fn.name, description: fn.description || "", input_schema: fn.parameters || fn.input_schema || { type: "object", properties: {} } };
    if (eager) out.eager_input_streaming = true;
    return out;
  });
}

export function buildRequest({ provider, system, messages, tools, thinking = true, toolChoice }) {
  const base = String(provider.baseUrl || "").replace(/\/+$/, "");
  const target = provider.full_url ? base : `${base}/messages`;
  if (!/^https?:\/\//i.test(target)) throw new ProviderError(t("core.provider.bad_url", { url: target || "—" }));
  const firstParty = isFirstParty(target);
  const maxTokens = Number(provider.maxTokens) || 8192;
  const body = { model: provider.model, max_tokens: maxTokens };
  if (system) body.system = system;
  body.messages = messages;
  body.stream = true;
  if (tools?.length && provider.tools !== false) {
    body.tools = anthropicTools(tools, { eager: firstParty });
    body.tool_choice = { type: toolChoice === "none" ? "none" : "auto" };
  }
  const th = thinkingParam(provider.model, thinking, maxTokens, firstParty);
  const bind = Boolean(firstParty && th?.type === "adaptive" && BINDING_MODELS.has(provider.model));
  if (th) body.thinking = bind ? { ...th, block_binding: { prefix_mismatch_behavior: "drop_block" } } : th;
  if (!th && provider.temperature !== null && provider.temperature !== undefined && provider.temperature !== "" && !NO_SAMPLING.test(provider.model)) {
    body.temperature = Number(provider.temperature);
  }
  for (const [k, v] of Object.entries(provider.extra_body || {})) if (!["model", "messages", "stream", "tools", "system"].includes(k)) body[k] = v;
  const headers = {
    "Content-Type": "application/json",
    Accept: "text/event-stream",
    "anthropic-version": ANTHROPIC_VERSION,
    "anthropic-dangerous-direct-browser-access": "true",
  };
  if (provider.apiKey) headers["x-api-key"] = provider.apiKey;
  const betas = [];
  if (firstParty && FALLBACK_MODELS.has(provider.model) && body.fallbacks === undefined) {
    body.fallbacks = "default";
    betas.push(FALLBACK_BETA);
  }
  if (body.thinking?.block_binding) betas.push(BINDING_BETA);
  if (betas.length) headers["anthropic-beta"] = betas.join(",");
  for (const [k, v] of Object.entries(provider.headers || {})) {
    // a user's own anthropic-beta adds to ours: block_binding without its header is a 400
    if (k.toLowerCase() === "anthropic-beta" && betas.length) {
      delete headers["anthropic-beta"];
      headers[k] = [...new Set([...String(v).split(",").map((s) => s.trim()).filter(Boolean), ...betas])].join(",");
    } else headers[k] = v;
  }
  let url = target;
  if (provider.route === "runner" && provider.runner?.url) {
    url = `${provider.runner.url.replace(/\/+$/, "")}/api/llm`;
    headers["X-TCM-Target"] = target;
    if (provider.runner.token) headers["X-TCM-Token"] = provider.runner.token;
  }
  return { url, headers, body, target };
}

/** Same events as openai.streamChat; `done` also carries `refusal: {category, explanation}` when the model declined. */
export async function* streamChat({ provider, system, messages, tools, signal, thinking = true, toolChoice, fetch: fetchImpl }) {
  const doFetch = fetchImpl || provider.fetch || globalThis.fetch;
  if (!provider.apiKey && provider.key_required) {
    throw new ProviderError(t("core.provider.no_key", { label: provider.label }));
  }
  const req = buildRequest({ provider, system, messages, tools, thinking, toolChoice });
  let resp;
  try {
    resp = await doFetch(req.url, { method: "POST", headers: req.headers, body: JSON.stringify(req.body), signal });
  } catch (err) {
    if (err?.name === "AbortError" || signal?.aborted) throw err;
    throw unreachable(provider, req.target, err);
  }
  if (!resp.ok) {
    const text = await resp.text().catch(() => "");
    const { message, type } = parseErrorBody(text);
    const status = resp.status;
    const hint = status === 401 || status === 403 ? t("core.provider.hint.auth") : status === 404 ? t("core.provider.hint.not_found")
      : status === 429 ? t("core.provider.hint.rate") : status >= 500 ? t("core.provider.hint.server") : "";
    throw new ProviderError(t("core.provider.http", { status, hint, message: message || resp.statusText || "" }), {
      status, type, retryable: isRetryableStatus(status, type) || RETRYABLE_TYPES.has(type), retryAfter: retryAfterSeconds(resp.headers), body: text.slice(0, 2000),
    });
  }

  const st = { blocks: new Map(), stopReason: null, stopDetails: null, usage: { input: 0, output: 0 }, model: provider.model };
  const ctype = resp.headers?.get?.("content-type") || "";
  if (!ctype.includes("event-stream")) {
    const text = await resp.text();
    let j;
    try { j = JSON.parse(text); } catch { throw new ProviderError(t("core.provider.unparseable", { text: text.slice(0, 300) })); }
    if (j.type === "error" || j.error) throw streamError(j);
    yield* wholeMessage(j, st);
  } else {
    try {
      for await (const ev of readSSE(resp.body, { signal })) {
        let j;
        try { j = JSON.parse(ev.data); } catch { continue; }
        yield* handleEvent(j, st);
      }
    } catch (err) {
      if (err?.name === "AbortError" || signal?.aborted) throw err;
      if (err instanceof ProviderError) throw err;
      throw new ProviderError(t("core.provider.stream_error", { message: err?.message || String(err) }), { retryable: true, kind: "network" });
    }
  }
  yield* finish(st);
}

function streamError(j) {
  const e = j.error || {};
  const type = e.type || "";
  return new ProviderError(t("core.provider.stream_error", { message: e.message || type || "unknown error" }), { type, retryable: RETRYABLE_TYPES.has(type) });
}

function* handleEvent(j, st) {
  switch (j.type) {
    case "message_start": {
      const m = j.message || {};
      if (m.model) st.model = m.model;
      addUsage(st, m.usage);
      break;
    }
    case "content_block_start": {
      const b = { ...(j.content_block || {}) };
      if (b.type === "text") b.text = b.text || "";
      if (b.type === "thinking") { b.thinking = b.thinking || ""; b.signature = b.signature || ""; }
      if (b.type === "tool_use") {
        b.partial = "";
        b.initial = b.input && typeof b.input === "object" && Object.keys(b.input).length ? b.input : null;
        yield { type: "tool_call_delta", index: j.index, name: b.name };
      }
      st.blocks.set(j.index, b);
      if (b.type === "text" && b.text) yield { type: "text", delta: b.text };
      if (b.type === "thinking" && b.thinking) yield { type: "reasoning", delta: b.thinking };
      break;
    }
    case "content_block_delta": {
      const b = st.blocks.get(j.index);
      const d = j.delta || {};
      if (!b) break;
      if (d.type === "text_delta" && typeof d.text === "string") {
        b.text += d.text;
        if (d.text) yield { type: "text", delta: d.text };
      } else if (d.type === "thinking_delta" && typeof d.thinking === "string") {
        b.thinking += d.thinking;
        if (d.thinking) yield { type: "reasoning", delta: d.thinking };
      } else if (d.type === "signature_delta" && typeof d.signature === "string") {
        b.signature = (b.signature || "") + d.signature;
      } else if (d.type === "input_json_delta" && typeof d.partial_json === "string") {
        b.partial += d.partial_json;
      }
      break;
    }
    case "message_delta": {
      if (j.delta?.stop_reason) st.stopReason = j.delta.stop_reason;
      if (j.delta?.stop_details) st.stopDetails = j.delta.stop_details;
      if (j.usage) {
        // message_delta usage is cumulative for output; input appears here only on some paths
        if (typeof j.usage.output_tokens === "number") st.usage.output = j.usage.output_tokens;
        if (typeof j.usage.input_tokens === "number" && j.usage.input_tokens > 0) {
          st.usage.input = j.usage.input_tokens + (j.usage.cache_read_input_tokens || 0) + (j.usage.cache_creation_input_tokens || 0);
        }
      }
      break;
    }
    case "error":
      throw streamError(j);
    default:
      break; // ping, message_stop, content_block_stop
  }
}

function addUsage(st, u) {
  if (!u) return;
  st.usage.input = (u.input_tokens || 0) + (u.cache_read_input_tokens || 0) + (u.cache_creation_input_tokens || 0);
  if (typeof u.output_tokens === "number") st.usage.output = u.output_tokens;
}

function* wholeMessage(m, st) {
  if (m.model) st.model = m.model;
  addUsage(st, m.usage);
  st.stopReason = m.stop_reason || null;
  st.stopDetails = m.stop_details || null;
  (m.content || []).forEach((b, i) => {
    const copy = { ...b };
    if (copy.type === "tool_use") { copy.partial = ""; copy.initial = copy.input || {}; }
    st.blocks.set(i, copy);
  });
  for (const b of st.blocks.values()) {
    if (b.type === "thinking" && b.thinking) yield { type: "reasoning", delta: b.thinking };
    if (b.type === "text" && b.text) yield { type: "text", delta: b.text };
  }
}

function* finish(st) {
  let blocks = [...st.blocks.entries()].sort((a, b) => a[0] - b[0]).map(([, b]) => b);
  // after a mid-output fallback, what the declined attempt produced before the switch is not replayed, except its text
  const lastFallback = blocks.map((b) => b.type).lastIndexOf("fallback");
  const fellBack = lastFallback >= 0 ? { from: blocks[lastFallback].from?.model || null, to: blocks[lastFallback].to?.model || null } : null;
  if (lastFallback >= 0) blocks = blocks.filter((b, i) => i > lastFallback || b.type === "text");

  const refused = st.stopReason === "refusal";
  const truncated = st.stopReason === "max_tokens";
  const toolCalls = [];
  const content = [];
  for (const b of blocks) {
    if (b.type === "text") { if (b.text) content.push({ type: "text", text: b.text }); continue; }
    if (b.type === "thinking") { content.push({ type: "thinking", thinking: b.thinking || "", signature: b.signature || "" }); continue; }
    if (b.type === "redacted_thinking") { content.push({ type: "redacted_thinking", data: b.data }); continue; }
    if (b.type === "tool_use") {
      if (refused) continue; // a refusal can cut a tool input off: never run that turn's tools
      // an input cut off by max_tokens often still parses as a smaller object: never run it
      const parsed = truncated ? { value: {}, error: t("core.provider.truncated_tool") }
        : b.partial ? parseArguments(b.partial) : { value: b.initial || {}, error: null };
      toolCalls.push({ id: b.id, name: b.name, arguments: parsed.value, raw: b.partial || JSON.stringify(b.initial || {}), error: parsed.error });
      content.push({ type: "tool_use", id: b.id, name: b.name, input: parsed.error ? {} : parsed.value });
    }
    // other block types (fallback markers, server-tool blocks this client never requests) are not replayed
  }
  for (const c of toolCalls) {
    yield { type: "tool_call", id: c.id, name: c.name, arguments: c.arguments, raw: c.raw, ...(c.error ? { error: c.error } : {}) };
  }
  if (st.usage.input || st.usage.output) yield { type: "usage", input: st.usage.input, output: st.usage.output };
  // a refused reply is not replayed (its partial text is not an answer); an empty one cannot be
  const wire = refused || !content.length ? null : { role: "assistant", content };
  const done = { type: "done", finish_reason: st.stopReason || (toolCalls.length ? "tool_use" : "end_turn"), wire, model: st.model };
  if (refused) done.refusal = { category: st.stopDetails?.category ?? null, explanation: st.stopDetails?.explanation ?? null };
  if (fellBack) done.fallback = fellBack;
  yield done;
}

/** Tool results go back as one user message of tool_result blocks. A refusal is a result: only failures are errors. */
export function toolResultMessages(results) {
  return [{
    role: "user",
    content: results.map((r) => ({ type: "tool_result", tool_use_id: r.id, content: r.text || "(no output)", ...(r.isError ? { is_error: true } : {}) })),
  }];
}

export function userMessage(text) {
  return { role: "user", content: String(text ?? "") };
}
