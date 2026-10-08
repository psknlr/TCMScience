// Model providers: the presets, the one in use, the relay's health, and a connection test.
//
// The default model is Tao-S1, the science.impf.ai relay on the same origin: no key, rate-limited, and the page never
// names the service behind it. The user may add their own API (key kept in this browser) or a local model server.
//
// `cors` records whether a browser page on https://science.impf.ai can call the provider directly. The cloud
// providers below answered a CORS preflight from that origin (checked 2026-10-07). Local servers need their own CORS
// setting (`help` names it), or the local runner forwards the calls (POST {runner}/api/llm, X-TCM-Target).
//
// `stream` says how a service streams its text (llm/openai.js streamModes): "delta" for the services known to send
// increments, so nothing is guessed; Tao-S1's reasoning comes as the whole text so far; custom endpoints are guessed.

import { lang, t } from "./i18n.js";
import { relayError } from "./llm/openai.js";
import { fetchWithTimeout, isLoopbackUrl, trimSlash } from "./util.js";

export const RELAY_ORIGIN = "https://science.impf.ai";
const RUNNER_ORIGINS = new Set(["http://127.0.0.1:8765", "http://localhost:8765"]);

/** Every preset. `models` are suggestions; any model id the service accepts may be typed. */
export const PRESETS = [
  {
    id: "tao", label: "Tao-S1", format: "openai", base_url: "/v1", models: ["Tao-S1"], default_model: "Tao-S1",
    key_required: false, relay: true, cors: "ok", max_tokens_param: "max_completion_tokens", reasoning: "reasoning_details",
    stream_usage: true, thinking_off: { thinking: { type: "disabled" } }, max_output: 8192, tools: true,
    // the relay's upstream sends reasoning_details as the whole text so far; its content may come either way
    stream: { content: "auto", reasoning: "cumulative" },
    help: {
      zh: "Tao-S1 · 默认模型。无需密钥；请求经 science.impf.ai 中继转发至模型服务，有调用频率限制。",
      en: "Tao-S1 · the default model. No key needed; requests pass through the science.impf.ai relay to the model service, with rate limits.",
    },
  },
  {
    id: "openai", label: "OpenAI", format: "openai", base_url: "https://api.openai.com/v1",
    models: ["gpt-5", "gpt-5-mini", "gpt-4.1"], default_model: "gpt-5", key_required: true, cors: "ok",
    max_tokens_param: "max_completion_tokens", reasoning: null, stream_usage: true, tools: true, stream: "delta",
    docs: "https://platform.openai.com/api-keys",
  },
  {
    id: "anthropic", label: "Anthropic Claude", format: "anthropic", base_url: "https://api.anthropic.com/v1",
    models: ["claude-opus-5-5", "claude-sonnet-5-5", "claude-fable-5-1", "claude-haiku-4-5"], default_model: "claude-opus-5-5",
    key_required: true, cors: "ok", max_tokens_param: "max_tokens", reasoning: "thinking_blocks", stream_usage: true, tools: true,
    docs: "https://console.anthropic.com/settings/keys",
    help: {
      zh: "浏览器直连 Anthropic 时会带上 anthropic-dangerous-direct-browser-access 请求头；密钥只保存在本浏览器。",
      en: "Direct browser calls to Anthropic send the anthropic-dangerous-direct-browser-access header; the key stays in this browser.",
    },
  },
  {
    id: "deepseek", label: "DeepSeek", format: "openai", base_url: "https://api.deepseek.com/v1",
    models: ["deepseek-chat", "deepseek-reasoner"], default_model: "deepseek-chat", key_required: true, cors: "ok",
    max_tokens_param: "max_tokens", reasoning: "reasoning_content", stream_usage: true, tools: true, stream: "delta",
    docs: "https://platform.deepseek.com/api_keys",
  },
  {
    id: "qwen", label: "通义千问 Qwen", format: "openai", base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1",
    models: ["qwen-plus", "qwen-max", "qwen-turbo", "qwen3-max"], default_model: "qwen-plus", key_required: true, cors: "ok",
    max_tokens_param: "max_tokens", reasoning: null, stream_usage: true, tools: true, stream: "delta",
    docs: "https://bailian.console.aliyun.com/",
  },
  {
    id: "moonshot", label: "Kimi (Moonshot)", format: "openai", base_url: "https://api.moonshot.cn/v1",
    models: ["kimi-k2-turbo-preview", "kimi-k2-0905-preview", "moonshot-v1-32k"], default_model: "kimi-k2-turbo-preview",
    key_required: true, cors: "ok", max_tokens_param: "max_tokens", reasoning: "reasoning_content", stream_usage: true, tools: true, stream: "delta",
    docs: "https://platform.moonshot.cn/console/api-keys",
  },
  {
    id: "zhipu", label: "智谱 GLM", format: "openai", base_url: "https://open.bigmodel.cn/api/paas/v4",
    models: ["glm-4.6", "glm-4.5", "glm-4.5-air"], default_model: "glm-4.6", key_required: true, cors: "ok",
    max_tokens_param: "max_tokens", reasoning: "reasoning_content", stream_usage: true, tools: true, stream: "delta",
    docs: "https://open.bigmodel.cn/usercenter/apikeys",
  },
  {
    id: "siliconflow", label: "SiliconFlow 硅基流动", format: "openai", base_url: "https://api.siliconflow.cn/v1",
    models: ["deepseek-ai/DeepSeek-V3.2", "Qwen/Qwen3-235B-A22B-Instruct-2507", "moonshotai/Kimi-K2-Instruct-0905"],
    default_model: "deepseek-ai/DeepSeek-V3.2", key_required: true, cors: "ok", max_tokens_param: "max_tokens",
    reasoning: null, stream_usage: true, tools: true, stream: "delta", docs: "https://cloud.siliconflow.cn/account/ak",
  },
  {
    id: "openrouter", label: "OpenRouter", format: "openai", base_url: "https://openrouter.ai/api/v1",
    models: ["openai/gpt-5", "anthropic/claude-opus-5-5", "deepseek/deepseek-chat-v3.1", "qwen/qwen3-235b-a22b"],
    default_model: "openai/gpt-5", key_required: true, cors: "ok", max_tokens_param: "max_tokens",
    reasoning: "reasoning_details", stream_usage: true, tools: true, stream: "delta", docs: "https://openrouter.ai/keys",
  },
  {
    id: "ollama", label: "Ollama", format: "openai", base_url: "http://127.0.0.1:11434/v1",
    models: ["qwen3:8b", "qwen3:32b", "llama3.1:8b", "deepseek-r1:8b"], default_model: "qwen3:8b",
    key_required: false, local: true, cors: "needs-config", max_tokens_param: "max_tokens", reasoning: null,
    stream_usage: true, tools: true, stream: "delta", fix: "core.local_model.cors.ollama",
    help: {
      zh: "本机 Ollama。浏览器直连需要设置 OLLAMA_ORIGINS=https://science.impf.ai 后重启；也可以经本机 Runner 转发。",
      en: "Ollama on this computer. Direct browser calls need OLLAMA_ORIGINS=https://science.impf.ai and a restart; or route through the local runner.",
    },
  },
  {
    id: "lmstudio", label: "LM Studio", format: "openai", base_url: "http://127.0.0.1:1234/v1",
    models: [], default_model: "", key_required: false, local: true, cors: "needs-config", max_tokens_param: "max_tokens",
    reasoning: null, stream_usage: false, tools: true, stream: "delta", fix: "core.local_model.cors.lmstudio",
    help: {
      zh: "本机 LM Studio。浏览器直连需在 Developer → Server Settings 中打开「Enable CORS」；也可以经本机 Runner 转发。",
      en: "LM Studio on this computer. Direct browser calls need \"Enable CORS\" under Developer → Server Settings; or route through the local runner.",
    },
  },
  {
    id: "vllm", label: "vLLM", format: "openai", base_url: "http://127.0.0.1:8000/v1",
    models: [], default_model: "", key_required: false, local: true, cors: "needs-config", max_tokens_param: "max_tokens",
    reasoning: null, stream_usage: true, tools: true, stream: "delta", fix: "core.local_model.cors.vllm",
    help: {
      zh: "本机 vLLM（OpenAI 兼容服务）。浏览器直连需 --allowed-origins；工具调用需 --enable-auto-tool-choice 与对应的 --tool-call-parser。",
      en: "vLLM's OpenAI-compatible server. Direct browser calls need --allowed-origins; tool calls need --enable-auto-tool-choice and a matching --tool-call-parser.",
    },
  },
  {
    id: "llamacpp", label: "llama.cpp", format: "openai", base_url: "http://127.0.0.1:8080/v1",
    models: [], default_model: "", key_required: false, local: true, cors: "needs-config", max_tokens_param: "max_tokens",
    reasoning: null, stream_usage: false, tools: true, stream: "delta", fix: "core.local_model.cors.llamacpp",
    help: {
      zh: "本机 llama-server。工具调用需以 --jinja 启动。",
      en: "llama-server on this computer. Tool calls need --jinja.",
    },
  },
  {
    id: "custom_openai", label: "自定义 OpenAI 兼容 API", format: "openai", base_url: "", models: [], default_model: "",
    key_required: false, custom: true, cors: "needs-config", max_tokens_param: "max_tokens", reasoning: null,
    stream_usage: false, tools: true,
  },
  {
    id: "custom_anthropic", label: "自定义 Anthropic 兼容 API", format: "anthropic", base_url: "", models: [], default_model: "",
    key_required: false, custom: true, cors: "needs-config", max_tokens_param: "max_tokens", reasoning: "thinking_blocks",
    stream_usage: true, tools: true,
  },
];

/** A user-defined endpoint (settings.customProviders) as a preset. */
export function customPreset(c) {
  const format = c.format === "anthropic" ? "anthropic" : "openai";
  return {
    id: String(c.id), label: c.label || c.id, format, base_url: c.base_url || "", models: c.model ? [c.model] : [],
    default_model: c.model || "", key_required: false, custom: true,
    cors: isLoopbackUrl(c.base_url) ? "needs-config" : "unknown",
    max_tokens_param: c.max_tokens_param || (format === "anthropic" ? "max_tokens" : "max_tokens"),
    reasoning: c.reasoning ?? (format === "anthropic" ? "thinking_blocks" : null),
    stream_usage: Boolean(c.stream_usage), tools: c.tools !== false, headers: c.headers || {}, extra_body: c.extra_body || {},
    full_url: Boolean(c.full_url),
  };
}

export function newCustomProvider(format = "openai", n = 1) {
  return {
    id: `custom-${Date.now().toString(36)}`, label: format === "anthropic" ? `Anthropic API ${n}` : `API ${n}`, format,
    base_url: "", model: "", headers: {}, extra_body: {}, max_tokens_param: "max_tokens",
  };
}

export function allProviders(settings = {}) {
  return [...PRESETS, ...(settings.customProviders || []).map(customPreset)];
}

export function presetById(id, settings) {
  return allProviders(settings).find((p) => p.id === id) || null;
}

/**
 * Where the Tao-S1 relay lives for a page at `origin`: the same origin, except when the page is served by the local
 * runner on its default port (http://127.0.0.1:8765), which has no relay, so the public one is used (it admits that
 * origin). A runner on another port answers /v1/health itself with ok:false and the reason.
 */
export function relayBaseUrl(origin = pageOrigin()) {
  if (!origin || origin === "null" || RUNNER_ORIGINS.has(origin)) return `${RELAY_ORIGIN}/v1`;
  return `${origin}/v1`;
}

function pageOrigin() {
  try { return globalThis.location?.origin || ""; } catch { return ""; }
}

/**
 * The provider in use: the preset plus {model, baseUrl, apiKey, route, runner?, maxTokens, temperature, needsKey}.
 * `override` (a conversation's model chip) may name {provider, model}; `override.runner` = {url, token, connected}
 * says whether model calls can go through the local runner; `override.maxOutput` is the relay's
 * health.max_output_tokens.
 */
export function activeProvider(settings = {}, override = {}) {
  const id = override.provider || settings.provider || "tao";
  const preset = presetById(id, settings) || PRESETS[0];
  const models = settings.models || {};
  let model = override.model || models[preset.id] || preset.default_model || "";
  if (preset.relay && !preset.models.includes(model)) model = preset.default_model;
  let baseUrl = trimSlash((settings.baseUrls || {})[preset.id] || preset.base_url);
  if (preset.relay && !(settings.baseUrls || {})[preset.id]) baseUrl = relayBaseUrl();
  else if (baseUrl.startsWith("/")) baseUrl = `${pageOrigin()}${baseUrl}`;
  const apiKey = preset.relay ? "" : String((settings.keys || {})[preset.id] || "").trim();
  const runner = override.runner || (settings.runner ? { ...settings.runner, connected: false } : null);
  const route = routeFor(preset, baseUrl, settings.route || "auto", runner);
  // the relay's ceiling comes from its health check when the UI has one (override.maxOutput)
  const ceiling = preset.relay && Number(override.maxOutput) > 0 ? Number(override.maxOutput) : preset.max_output;
  const maxTokens = ceiling ? Math.min(Number(settings.maxTokens) || 8192, ceiling) : Number(settings.maxTokens) || 8192;
  return {
    ...preset, model, baseUrl, apiKey, route, maxTokens,
    temperature: settings.temperature ?? null,
    runner: route === "runner" && runner ? { url: trimSlash(runner.url), token: runner.token || "" } : null,
    needsKey: Boolean(preset.key_required && !apiKey),
  };
}

/**
 * direct | runner. The relay is always direct (the runner proxy does not forward to it). "auto": a provider that
 * accepts browser calls goes direct; a local server or a blocked one goes through the runner when it is connected.
 */
export function routeFor(preset, baseUrl, setting, runner) {
  if (preset.relay) return "direct";
  if (setting === "direct") return "direct";
  if (setting === "runner") return runner?.url ? "runner" : "direct";
  const connected = Boolean(runner?.connected && runner?.url);
  if (!connected) return "direct";
  if (preset.cors === "blocked") return "runner";
  if (preset.cors === "needs-config" && (preset.local || isLoopbackUrl(baseUrl))) return "runner";
  return "direct";
}

/** The sentence that tells the user how to fix a local server's CORS. */
export function localFix(provider, origin = pageOrigin() || RELAY_ORIGIN) {
  return t(provider.fix || "core.local_model.cors.generic", { origin });
}

/**
 * GET {relay}/health → {ok, model, limits, max_output_tokens, error?, error_type?, error_lang?}; never throws. An error
 * of a known type is told in the page's language (relayError); `error_lang` is "zh" when the relay's own Chinese
 * message is shown on a page in another language.
 */
export async function relayHealth({ fetch: fetchImpl = globalThis.fetch, baseUrl = relayBaseUrl(), timeoutMs = 10000 } = {}) {
  try {
    const r = await fetchWithTimeout(fetchImpl, `${trimSlash(baseUrl)}/health`, { headers: { Accept: "application/json" } }, timeoutMs);
    const j = await r.json().catch(() => ({}));
    const type = typeof j.error?.type === "string" ? j.error.type : "";
    // the runner's not_relay error carries message_en beside its Chinese message: the page's language picks
    const said = (lang() !== "zh" && typeof j.error?.message_en === "string" && j.error.message_en.trim())
      || (typeof j.error?.message === "string" ? j.error.message : "");
    const told = (fallback) => {
      if (!type && !said) return { error: fallback };
      const shown = relayError(type, said || "");
      return { error: shown.text, ...(type ? { error_type: type } : {}), ...(shown.lang ? { error_lang: shown.lang } : {}) };
    };
    return {
      ok: Boolean(r.ok && j.ok), model: j.model || "Tao-S1", models: j.models || ["Tao-S1"], limits: j.limits || null,
      max_output_tokens: Number(j.max_output_tokens) || null, version: j.version || null,
      ...(r.ok ? {} : { status: r.status, ...told(`HTTP ${r.status}`) }),
      ...(r.ok && !j.ok ? told(t("core.relay.off")) : {}),
    };
  } catch (err) {
    return { ok: false, model: "Tao-S1", limits: null, max_output_tokens: null, error: err?.message || String(err) };
  }
}

/**
 * A tiny tool-calling round trip: the model is asked to call `ping`. Returns {ok, latency_ms, tools, model, reply?,
 * error?}. `tools:false` with ok:true means the model answered but did not call the tool.
 */
export async function testConnection(provider, { timeoutMs = 45000, signal } = {}) {
  const started = Date.now();
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), timeoutMs);
  const onAbort = () => ctl.abort();
  signal?.addEventListener?.("abort", onAbort, { once: true });
  const tools = [{
    name: "ping", description: "Connectivity check. Call it with echo set to the word the user gives.",
    parameters: { type: "object", properties: { echo: { type: "string" } }, required: ["echo"] },
  }];
  const lib = provider.format === "anthropic" ? await import("./llm/anthropic.js") : await import("./llm/openai.js");
  let reply = "", called = false, firstMs = null;
  try {
    const stream = lib.streamChat({
      provider: { ...provider, maxTokens: Math.min(provider.maxTokens || 1024, 1024) },
      system: "You are a connectivity check. Call the ping tool exactly once with the given word, and write nothing else.",
      messages: [{ role: "user", content: "Call ping with echo = \"ok\"." }],
      tools, signal: ctl.signal, thinking: false,
    });
    for await (const ev of stream) {
      if (firstMs === null && (ev.type === "text" || ev.type === "tool_call" || ev.type === "reasoning")) firstMs = Date.now() - started;
      if (ev.type === "text") reply += ev.delta;
      if (ev.type === "tool_call" && ev.name === "ping" && !ev.error) called = true;
    }
    return { ok: true, latency_ms: Date.now() - started, first_token_ms: firstMs, tools: called, model: provider.model, reply: reply.trim().slice(0, 200) };
  } catch (err) {
    const aborted = ctl.signal.aborted && !signal?.aborted;
    return { ok: false, latency_ms: Date.now() - started, tools: false, model: provider.model, error: aborted ? `timeout after ${timeoutMs} ms` : err?.message || String(err), status: err?.status || 0 };
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener?.("abort", onAbort);
  }
}
