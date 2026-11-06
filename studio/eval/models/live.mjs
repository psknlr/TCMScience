// The live driver: a real model behind any OpenAI-compatible base URL, through the real client. It runs only when
// asked for by name (run.mjs --live) with EVAL_LIVE_BASE_URL set, and in CI only from a manually started workflow
// (workflow_dispatch): no push or pull request ever calls a model, paid or not, and no CI gate depends on one.
// EVAL_LIVE_API_KEY is read from the environment and never written into a report or a cassette.

import { newMeter } from "./scripted.mjs";

/** Why a live run may not start here, or null when it may. */
export function liveRefusal(env = process.env, { asked = false } = {}) {
  if (!asked) return "the live driver runs only when asked for (--live)";
  if (!env.EVAL_LIVE_BASE_URL) return "EVAL_LIVE_BASE_URL is not set (an OpenAI-compatible base URL, e.g. http://127.0.0.1:8080/v1)";
  const ci = env.CI && !/^(0|false)$/i.test(String(env.CI).trim());
  if (ci && env.GITHUB_EVENT_NAME !== "workflow_dispatch") return "in CI the live driver runs only from a manually started workflow (workflow_dispatch)";
  return null;
}

/** {provider, meter, model: "live"}: the real openai.js client against the configured service. */
export function liveDriver(env = process.env) {
  const baseUrl = String(env.EVAL_LIVE_BASE_URL).replace(/\/+$/, "");
  const meter = newMeter();
  const fetchImpl = async (url, init = {}) => {
    let parsed = {};
    try { parsed = JSON.parse(init.body); } catch { /* counted as sent */ }
    meter.request(Buffer.byteLength(String(init.body || "")), Array.isArray(parsed.tools) && parsed.tools.length > 0, parsed.tool_choice);
    return globalThis.fetch(url, init);
  };
  const provider = {
    id: "eval-live", label: env.EVAL_LIVE_MODEL || "live", format: "openai", model: env.EVAL_LIVE_MODEL || "default",
    baseUrl, apiKey: env.EVAL_LIVE_API_KEY || "", tools: true, maxTokens: Number(env.EVAL_LIVE_MAX_TOKENS) || 4096,
    max_tokens_param: "max_tokens", stream_usage: true, stream: "delta", route: "direct", fetch: fetchImpl,
  };
  return { kind: "live", model: "live", provider, llm: undefined, meter, close: async () => {} };
}
