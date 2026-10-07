// OpenAI-compatible chat completions, streamed: the Tao-S1 relay, OpenAI, DeepSeek, Qwen, Kimi, GLM, SiliconFlow,
// OpenRouter, and local servers (Ollama, LM Studio, vLLM, llama.cpp).
//
// Services stream differently. Most send increments; some (the relay's upstream among them) send cumulative text, in
// the content or in reasoning_details. Every field goes through `Accum`, which accepts either. Reasoning arrives as
// reasoning_details[], reasoning_content, reasoning, or inline <think>…</think> at the start of the content; all of
// it is reported as "reasoning" and none of it as answer text. Tool calls are assembled per index and their
// arguments parsed strictly: arguments that are not a JSON object come back to the agent as an error for the model.
// The assistant message is returned whole (`wire`) because some services need their reasoning back verbatim.

import { t } from "../i18n.js";
import { isLoopbackUrl } from "../util.js";
import { ProviderError, isRetryableStatus, parseErrorBody, readSSE, retryAfterSeconds } from "./sse.js";

/** Accumulates a field that may stream as increments or as the whole text so far. push() returns what is new. */
export class Accum {
  constructor() { this.text = ""; this.mode = null; this.extended = false; }
  push(piece) {
    if (piece === undefined || piece === null || piece === "") return "";
    const s = String(piece);
    if (this.mode !== "delta" && this.text && s.length > this.text.length && s.startsWith(this.text)) {
      // the whole text so far, and more: cumulative (decided once it is long enough not to be a coincidence)
      const add = s.slice(this.text.length);
      this.text = s;
      this.extended = true;
      if (this.text.length >= 12) this.mode = "cumulative";
      return add;
    }
    // a repeat of what we have, in a stream that has already shown itself cumulative
    if ((this.mode === "cumulative" || (this.extended && !this.mode)) && this.text.startsWith(s)) return "";
    if (this.text && !this.mode) this.mode = "delta";
    this.text += s;
    return s;
  }
}

const OPEN = "<think>";
const CLOSE = "</think>";

/**
 * Split content that may start with <think>…</think>. While the block is open, a trailing partial "</thi" is held
 * back, and content that could still become "<think>" yields nothing yet, so what is shown never has to be taken back.
 */
export function splitThink(raw) {
  const s = String(raw || "").replace(/^\s+/, "");
  if (!s) return { thinking: "", answer: "", open: false, pending: true };
  if (s.length < OPEN.length && OPEN.startsWith(s)) return { thinking: "", answer: "", open: false, pending: true };
  if (!s.startsWith(OPEN)) return { thinking: "", answer: s, open: false, pending: false };
  const end = s.indexOf(CLOSE);
  if (end < 0) {
    let thinking = s.slice(OPEN.length);
    for (let k = Math.min(CLOSE.length - 1, thinking.length); k > 0; k--) {
      if (CLOSE.startsWith(thinking.slice(-k))) { thinking = thinking.slice(0, -k); break; }
    }
    return { thinking, answer: "", open: true, pending: false };
  }
  return { thinking: s.slice(OPEN.length, end), answer: s.slice(end + CLOSE.length).replace(/^\s+/, ""), open: false, pending: false };
}

/** Remove a leading <think>…</think> (closed or not) from text. */
export function stripThink(text) {
  if (typeof text !== "string") return text;
  return text.replace(/^\s*<think>[\s\S]*?(?:<\/think>\s*|$)/, "");
}

/** Neutral tool definitions ({name, description, parameters}) or OpenAI ones → OpenAI function tools. */
export function openaiTools(tools) {
  return (tools || []).map((tool) => (tool.type === "function" && tool.function ? tool : {
    type: "function",
    function: { name: tool.name, description: tool.description || "", parameters: tool.parameters || tool.input_schema || { type: "object", properties: {} } },
  }));
}

/**
 * The HTTP request for one call: {url, headers, body (object; `model` is its first key), target}. Exported for tests
 * and for the runner proxy's own probe.
 */
export function buildRequest({ provider, system, messages, tools, thinking = true, toolChoice }) {
  const target = provider.full_url ? provider.baseUrl : `${provider.baseUrl}/chat/completions`;
  if (!/^https?:\/\//i.test(target || "")) throw new ProviderError(t("core.provider.bad_url", { url: target || "—" }), { kind: "model" });
  // the relay edits the request as text and expects `model` first: build the object in that order
  const body = { model: provider.model };
  body.messages = system ? [{ role: "system", content: system }, ...messages] : [...messages];
  body.stream = true;
  if (tools?.length && provider.tools !== false) {
    body.tools = openaiTools(tools);
    body.tool_choice = toolChoice === "none" ? "none" : "auto";
  }
  const maxTokens = Number(provider.maxTokens) || 0;
  if (maxTokens > 0) body[provider.max_tokens_param || "max_tokens"] = provider.max_output ? Math.min(maxTokens, provider.max_output) : maxTokens;
  if (provider.temperature !== null && provider.temperature !== undefined && provider.temperature !== "") body.temperature = Number(provider.temperature);
  for (const [k, v] of Object.entries(provider.extra_body || {})) if (!["model", "messages", "stream", "tools"].includes(k)) body[k] = v;
  if (thinking === false && provider.thinking_off) Object.assign(body, provider.thinking_off);
  if (provider.stream_usage) body.stream_options = { include_usage: true };

  const headers = { "Content-Type": "application/json", Accept: "text/event-stream" };
  if (provider.apiKey) headers.Authorization = `Bearer ${provider.apiKey}`;
  for (const [k, v] of Object.entries(provider.headers || {})) headers[k] = v;
  if (provider.id === "openrouter") {
    try { headers["HTTP-Referer"] = globalThis.location?.origin || "https://science.impf.ai"; } catch { /* no location */ }
    headers["X-Title"] = "TCMScience Studio";
  }
  let url = target;
  if (provider.route === "runner" && provider.runner?.url) {
    url = `${provider.runner.url.replace(/\/+$/, "")}/api/llm`;
    headers["X-TCM-Target"] = target;
    if (provider.runner.token) headers["X-TCM-Token"] = provider.runner.token;
  }
  return { url, headers, body, target };
}

/**
 * Stream one completion. Yields {type:"text", delta} · {type:"reasoning", delta} · {type:"tool_call_delta", index,
 * name} · {type:"tool_call", id, name, arguments, raw, error?} · {type:"usage", input, output} ·
 * {type:"done", finish_reason, wire, model}. Throws ProviderError (or an AbortError when `signal` aborts).
 */
export async function* streamChat({ provider, system, messages, tools, signal, thinking = true, toolChoice, fetch: fetchImpl }) {
  const doFetch = fetchImpl || provider.fetch || globalThis.fetch;
  if (!provider.apiKey && provider.key_required) throw new ProviderError(t("core.provider.no_key", { label: provider.label }));
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
    const shown = provider.relay && message ? message : t("core.provider.http", { status, hint: httpHint(status), message: message || resp.statusText || "" });
    throw new ProviderError(shown, { status, type, retryable: isRetryableStatus(status, type), retryAfter: retryAfterSeconds(resp.headers), kind: "model", body: provider.relay ? "" : text.slice(0, 2000) });
  }

  const st = newState();
  const ctype = resp.headers?.get?.("content-type") || "";
  if (!ctype.includes("event-stream")) {
    // a service that ignored stream:true, or an error delivered with status 200
    const text = await resp.text();
    let j;
    try { j = JSON.parse(text); } catch { throw new ProviderError(t("core.provider.unparseable", { text: text.slice(0, 300) })); }
    yield* handleChunk(j, st, true, provider.relay);
  } else {
    try {
      for await (const ev of readSSE(resp.body, { signal })) {
        const payload = ev.data.trim();
        if (payload === "[DONE]") break;
        let j;
        try { j = JSON.parse(payload); } catch { continue; } // a keep-alive or a vendor's non-JSON note
        yield* handleChunk(j, st, false, provider.relay);
      }
    } catch (err) {
      if (err?.name === "AbortError" || signal?.aborted) throw err;
      if (err instanceof ProviderError) throw err;
      throw new ProviderError(t("core.provider.stream_error", { message: err?.message || String(err) }), { retryable: true, kind: "network" });
    }
  }
  yield* finish(st, provider);
}

function newState() {
  return {
    content: new Accum(), reasoning: new Accum(), details: new Map(), calls: new Map(), reasoningSource: null,
    shownThinking: 0, shownAnswer: 0, finishReason: null, usage: null, final: null, model: null,
  };
}

function* handleChunk(j, st, whole, relay = false) {
  if (j.error || (j.base_resp && j.base_resp.status_code)) {
    const e = j.error || {};
    const message = (typeof e === "string" ? e : e.message || e.msg) || j.base_resp?.status_msg || "unknown error";
    const type = (typeof e === "object" && (e.type || e.code)) || "";
    // the relay's errors are already in its own words, written for the visitor
    throw new ProviderError(relay ? message : t("core.provider.stream_error", { message }), {
      type: String(type), status: Number(e.status || j.base_resp?.status_code || 0) || 0,
      retryable: ["upstream_error", "upstream_unreachable", "unavailable", "rate_limited", "upstream_rate", "overloaded_error", "server_error", "api_error"].includes(String(type)),
    });
  }
  if (j.model) st.model = j.model;
  if (j.usage) st.usage = j.usage;
  const choice = Array.isArray(j.choices) ? j.choices[0] : null;
  if (!choice) return;
  if (choice.finish_reason) st.finishReason = choice.finish_reason;
  if (choice.message) st.final = choice.message;
  const d = choice.delta || (whole ? choice.message : null) || {};

  // one source of reasoning per reply: a service that sends the same thoughts in two fields is shown them once
  let reasoningDelta = "";
  if (Array.isArray(d.reasoning_details)) {
    st.reasoningSource ||= "details";
    for (const [k, det] of d.reasoning_details.entries()) {
      if (!det || typeof det !== "object") continue;
      const idx = det.index ?? k;
      let slot = st.details.get(idx);
      if (!slot) st.details.set(idx, (slot = { meta: {}, acc: new Accum() }));
      for (const [key, v] of Object.entries(det)) if (key !== "text" && v !== undefined && v !== null) slot.meta[key] = v;
      const piece = slot.acc.push(typeof det.text === "string" ? det.text : typeof det.summary === "string" ? det.summary : "");
      if (st.reasoningSource === "details") reasoningDelta += piece;
    }
  }
  if (typeof d.reasoning_content === "string" && d.reasoning_content) {
    st.reasoningSource ||= "content";
    if (st.reasoningSource === "content") reasoningDelta += st.reasoning.push(d.reasoning_content);
  } else if (typeof d.reasoning === "string" && d.reasoning) {
    st.reasoningSource ||= "reasoning";
    if (st.reasoningSource === "reasoning") reasoningDelta += st.reasoning.push(d.reasoning);
  }
  if (reasoningDelta) yield { type: "reasoning", delta: reasoningDelta };

  const content = textOf(d.content);
  if (content) {
    if (st.content.push(content)) {
      const view = splitThink(st.content.text);
      if (view.thinking.length > st.shownThinking) {
        yield { type: "reasoning", delta: view.thinking.slice(st.shownThinking) };
        st.shownThinking = view.thinking.length;
      }
      if (view.answer.length > st.shownAnswer) {
        yield { type: "text", delta: view.answer.slice(st.shownAnswer) };
        st.shownAnswer = view.answer.length;
      }
    }
  }

  if (Array.isArray(d.tool_calls)) {
    for (const [k, tc] of d.tool_calls.entries()) {
      if (!tc || typeof tc !== "object") continue;
      const idx = tc.index ?? k;
      let slot = st.calls.get(idx);
      const fresh = !slot;
      if (!slot) st.calls.set(idx, (slot = { id: "", name: "", args: new Accum() }));
      if (tc.id) slot.id = tc.id;
      const fn = tc.function || {};
      const before = slot.name;
      if (fn.name) {
        // some services repeat the whole name in every delta, some split it: merge defensively
        slot.name = !slot.name ? fn.name : fn.name.startsWith(slot.name) ? fn.name : slot.name.endsWith(fn.name) ? slot.name : slot.name + fn.name;
      }
      if (fn.arguments !== undefined && fn.arguments !== null) {
        slot.args.push(typeof fn.arguments === "string" ? fn.arguments : JSON.stringify(fn.arguments));
      }
      if ((fresh || slot.name !== before) && slot.name) yield { type: "tool_call_delta", index: idx, name: slot.name };
    }
  }
}

function textOf(content) {
  if (typeof content === "string") return content;
  if (Array.isArray(content)) return content.map((p) => (typeof p === "string" ? p : p?.text || "")).join("");
  return "";
}

function* finish(st, provider) {
  const finalText = textOf(st.final?.content);
  const rawContent = finalText && finalText.length >= st.content.text.length ? finalText : st.content.text;
  // a closing message that is longer than what streamed: show the rest
  if (rawContent !== st.content.text) {
    const view = splitThink(rawContent);
    if (view.thinking.length > st.shownThinking) yield { type: "reasoning", delta: view.thinking.slice(st.shownThinking) };
    if (view.answer.length > st.shownAnswer) yield { type: "text", delta: view.answer.slice(st.shownAnswer) };
  } else if (!st.shownAnswer && !st.shownThinking && st.content.text.trim() && !st.content.text.trim().startsWith(OPEN)) {
    // content that never left the "could be <think>" state (a reply of "<thi"): it was the answer after all
    yield { type: "text", delta: st.content.text.replace(/^\s+/, "") };
  }
  if (!st.reasoningSource && typeof st.final?.reasoning_content === "string" && st.final.reasoning_content && !st.reasoning.text) {
    st.reasoning.push(st.final.reasoning_content);
    yield { type: "reasoning", delta: st.final.reasoning_content };
  }

  let calls = [...st.calls.entries()].sort((a, b) => a[0] - b[0]).map(([, c]) => ({ id: c.id, name: c.name, raw: c.args.text }));
  if (!calls.length && Array.isArray(st.final?.tool_calls)) {
    calls = st.final.tool_calls.map((tc) => ({
      id: tc.id, name: tc.function?.name || "",
      raw: typeof tc.function?.arguments === "string" ? tc.function.arguments : JSON.stringify(tc.function?.arguments ?? {}),
    }));
  }
  const toolCalls = calls.filter((c) => c.name).map((c, k) => {
    const id = c.id || `call_${Math.random().toString(36).slice(2, 10)}${k}`;
    const parsed = parseArguments(c.raw);
    return { id, name: c.name, arguments: parsed.value, raw: c.raw || "", error: parsed.error };
  });

  const wire = { role: "assistant", content: rawContent || "" };
  if (toolCalls.length) {
    wire.tool_calls = toolCalls.map((c) => ({
      id: c.id, type: "function",
      // invalid arguments are not replayed as is: some services reject them in history
      function: { name: c.name, arguments: c.error ? "{}" : (c.raw.trim() || "{}") },
    }));
  }
  const details = [...st.details.entries()].sort((a, b) => a[0] - b[0]).map(([, s]) => ({ ...s.meta, text: s.acc.text }));
  if (provider.reasoning === "reasoning_details") {
    const fin = Array.isArray(st.final?.reasoning_details) && st.final.reasoning_details.length ? st.final.reasoning_details : null;
    if (fin || details.length) wire.reasoning_details = fin || details;
  }
  if (provider.reasoning === "reasoning_content") {
    const rc = (typeof st.final?.reasoning_content === "string" && st.final.reasoning_content) || st.reasoning.text;
    if (rc) wire.reasoning_content = rc;
  }

  for (const c of toolCalls) {
    yield { type: "tool_call", id: c.id, name: c.name, arguments: c.arguments, raw: c.raw, ...(c.error ? { error: c.error } : {}) };
  }
  const usage = normalizeUsage(st.usage);
  if (usage) yield { type: "usage", ...usage };
  yield { type: "done", finish_reason: st.finishReason || (toolCalls.length ? "tool_calls" : "stop"), wire, model: st.model || provider.model };
}

/** Strict: the arguments must be a JSON object (empty means {}). */
export function parseArguments(raw) {
  const s = String(raw ?? "").trim();
  if (!s) return { value: {}, error: null };
  try {
    const v = JSON.parse(s);
    if (v === null || typeof v !== "object" || Array.isArray(v)) return { value: {}, error: t("core.tool.not_object") };
    return { value: v, error: null };
  } catch (err) {
    return { value: {}, error: t("core.tool.bad_json", { error: err.message }) };
  }
}

export function normalizeUsage(u) {
  if (!u || typeof u !== "object") return null;
  const input = Number(u.prompt_tokens ?? u.input_tokens ?? 0) || 0;
  const output = Number(u.completion_tokens ?? u.output_tokens ?? 0) || 0;
  if (!input && !output) return null;
  return { input, output };
}

function httpHint(status) {
  if (status === 401 || status === 403) return t("core.provider.hint.auth");
  if (status === 404) return t("core.provider.hint.not_found");
  if (status === 429) return t("core.provider.hint.rate");
  if (status >= 500) return t("core.provider.hint.server");
  return "";
}

/** The fetch never reached the service: say what to do about it, by route. */
export function unreachable(provider, target, err) {
  let host = target;
  try { host = new URL(target).host; } catch { /* keep the raw target */ }
  if (provider.relay) return new ProviderError(t("core.relay.unreachable"), { kind: "network", retryable: true });
  if (provider.route === "runner") {
    return new ProviderError(t("core.runner.proxy_unreachable", { url: provider.runner?.url || "" }), { kind: "network", retryable: false });
  }
  if (provider.local || isLoopbackUrl(target)) {
    // a refused connection or a CORS block on loopback does not go away in four seconds: no retry
    const origin = (() => { try { return globalThis.location?.origin || "https://science.impf.ai"; } catch { return "https://science.impf.ai"; } })();
    const fix = t(provider.fix || "core.local_model.cors.generic", { origin });
    return new ProviderError(t("core.provider.unreachable_local", { label: provider.label, host, fix }), { kind: "network", retryable: false });
  }
  const e = new ProviderError(t("core.provider.unreachable", { host }), { kind: "network", retryable: true });
  e.cause = err;
  return e;
}

/** Tool results go back as role "tool" messages. */
export function toolResultMessages(results) {
  return results.map((r) => ({ role: "tool", tool_call_id: r.id, content: r.text }));
}

export function userMessage(text) {
  return { role: "user", content: String(text ?? "") };
}
