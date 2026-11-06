// The scripted driver: the case's policy played in-process as the model library the loop calls (agent.js takes it as
// `llm`), with fixtures/fakes.mjs's scriptedLLM and openaiStep. Nothing goes over HTTP; the bytes of each request are
// measured on the body openai.js would send (its own buildRequest), so cost numbers compare with the other drivers.

import { buildRequest } from "../../web/js/core/llm/openai.js";
import { activeProvider } from "../../web/js/core/providers.js";
import { openaiStep, scriptedLLM } from "../../web/test/fixtures/fakes.mjs";
import { isFault } from "../lib/policy.mjs";

/** {provider, llm, meter, model} for one run of `policy`. */
export function scriptedDriver(policy, { preset = "tao" } = {}) {
  const provider = providerFor(preset, "http://127.0.0.1:9/v1");
  const meter = newMeter();
  const lib = scriptedLLM([(params) => {
    const offered = Boolean(params.tools?.length);
    meter.request(requestBytes(provider, params), offered, params.toolChoice);
    const step = policy.next({ tools: offered, toolChoice: params.toolChoice });
    if (isFault(step)) throw new Error("an HTTP-level fault step needs the fault or relay driver");
    return openaiStep({ text: step.text || "", reasoning: step.reasoning || "", calls: step.calls || [], usage: step.usage || undefined, finish: step.finish });
  }]);
  return { kind: "scripted", model: "scripted", provider, llm: { openai: lib }, meter, close: async () => {} };
}

/** The provider a preset gives (activeProvider) with its base URL pointed at `baseUrl` and an eval key when it needs one. */
export function providerFor(preset, baseUrl, extra = {}) {
  const p = activeProvider({ provider: preset, baseUrls: { [preset]: baseUrl }, keys: { [preset]: "eval-key-not-a-real-one" } });
  return { ...p, baseUrl, ...extra };
}

/** The exact request body openai.js sends for these parameters, in bytes. */
export function requestBytes(provider, params) {
  const req = buildRequest({ provider, system: params.system, messages: params.messages, tools: params.tools, thinking: params.thinking, toolChoice: params.toolChoice });
  return Buffer.byteLength(JSON.stringify(req.body));
}

/** What a run of the model cost: requests (bytes, tools offered, tool_choice), upstream calls and gate hits (relay). */
export function newMeter() {
  const m = {
    requests: [],
    upstreamCalls: null,
    gateHits: null,
    request(bytes, tools, toolChoice) { m.requests.push({ bytes, tools: Boolean(tools), tool_choice: toolChoice ?? null }); },
  };
  return m;
}
