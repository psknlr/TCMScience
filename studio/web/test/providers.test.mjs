import "./fixtures/setup.mjs";
import assert from "node:assert/strict";
import { test } from "node:test";
import { setLang } from "../js/core/i18n.js";
import { PRESETS, activeProvider, allProviders, customPreset, localFix, relayBaseUrl, relayHealth, routeFor, testConnection } from "../js/core/providers.js";
import { anthropicSse, fakeFetch, sse, streamResponse } from "./fixtures/fakes.mjs";

test("the presets: Tao-S1 first, the cloud services, local servers, custom endpoints; the relay's vendor is never listed", () => {
  const ids = PRESETS.map((p) => p.id);
  assert.deepEqual(ids, ["tao", "openai", "anthropic", "deepseek", "qwen", "moonshot", "zhipu", "siliconflow", "openrouter", "ollama", "lmstudio", "vllm", "llamacpp", "custom_openai", "custom_anthropic"]);
  assert.doesNotMatch(JSON.stringify(PRESETS), /minimax|abab|hailuo|海螺/i);
  for (const p of PRESETS) {
    for (const k of ["id", "label", "format", "base_url", "models", "default_model", "key_required", "cors", "max_tokens_param"]) assert.ok(k in p, `${p.id}.${k}`);
    assert.ok(["openai", "anthropic"].includes(p.format));
    assert.ok(["ok", "blocked", "needs-config"].includes(p.cors), p.id);
    if (p.local) assert.ok(p.help?.zh && p.help?.en, `${p.id} explains its CORS fix`);
  }
  const tao = PRESETS[0];
  assert.equal(tao.relay, true);
  assert.equal(tao.key_required, false);
  assert.match(tao.help.zh, /无需密钥/);
  assert.equal(PRESETS.find((p) => p.id === "ollama").base_url, "http://127.0.0.1:11434/v1");
  assert.equal(PRESETS.find((p) => p.id === "qwen").base_url, "https://dashscope.aliyuncs.com/compatible-mode/v1");
});

test("the relay's address: same origin, except for a page served by the local runner", () => {
  assert.equal(relayBaseUrl("https://science.impf.ai"), "https://science.impf.ai/v1");
  assert.equal(relayBaseUrl("http://127.0.0.1:8765"), "https://science.impf.ai/v1");
  assert.equal(relayBaseUrl("http://localhost:8765"), "https://science.impf.ai/v1");
  assert.equal(relayBaseUrl("http://127.0.0.1:4173"), "http://127.0.0.1:4173/v1", "a dev or test server keeps its own /v1");
  assert.equal(relayBaseUrl(""), "https://science.impf.ai/v1");
});

test("activeProvider: model, key, base URL, output cap; the relay never gets a key", () => {
  const s = { provider: "tao", keys: { tao: "should-not-be-sent", deepseek: " sk-1 " }, models: { tao: "MiniMax-M3", deepseek: "deepseek-reasoner" }, maxTokens: 16000, temperature: 0.3 };
  const tao = activeProvider(s);
  assert.equal(tao.model, "Tao-S1", "the relay only takes its own model name");
  assert.equal(tao.apiKey, "");
  assert.equal(tao.maxTokens, 8192);
  assert.equal(tao.route, "direct");
  assert.equal(activeProvider(s, { maxOutput: 4096 }).maxTokens, 4096, "the relay's health sets the ceiling");
  const ds = activeProvider(s, { provider: "deepseek" });
  assert.equal(ds.apiKey, "sk-1");
  assert.equal(ds.model, "deepseek-reasoner");
  assert.equal(ds.maxTokens, 16000);
  assert.equal(ds.temperature, 0.3);
  assert.equal(activeProvider({ provider: "anthropic" }).needsKey, true);
  assert.equal(activeProvider({ provider: "ollama" }).needsKey, false);
  assert.equal(activeProvider({ provider: "openai" }, { model: "gpt-4.1" }).model, "gpt-4.1", "a conversation's model chip overrides");
  assert.equal(activeProvider({ provider: "vllm", baseUrls: { vllm: "http://127.0.0.1:9000/v1/" } }).baseUrl, "http://127.0.0.1:9000/v1");
  assert.equal(activeProvider({ provider: "gone" }).id, "tao", "an unknown provider falls back to Tao-S1");
});

test("routing: auto goes direct for CORS-ok services and through a connected runner for local servers", () => {
  const runner = { url: "http://127.0.0.1:8765", token: "t", connected: true };
  const off = { ...runner, connected: false };
  assert.equal(activeProvider({ provider: "ollama" }, { runner }).route, "runner");
  assert.deepEqual(activeProvider({ provider: "ollama" }, { runner }).runner, { url: "http://127.0.0.1:8765", token: "t" });
  assert.equal(activeProvider({ provider: "ollama" }, { runner: off }).route, "direct");
  assert.equal(activeProvider({ provider: "deepseek" }, { runner }).route, "direct");
  assert.equal(activeProvider({ provider: "tao", route: "runner" }, { runner }).route, "direct", "the relay is always direct");
  assert.equal(activeProvider({ provider: "deepseek", route: "runner" }, { runner }).route, "runner");
  assert.equal(activeProvider({ provider: "ollama", route: "direct" }, { runner }).route, "direct");
  assert.equal(routeFor({ cors: "blocked" }, "https://x", "auto", runner), "runner");
  assert.equal(routeFor({ cors: "blocked" }, "https://x", "auto", off), "direct");
});

test("custom providers join the list", () => {
  const s = { customProviders: [{ id: "custom-1", label: "公司网关", format: "openai", base_url: "https://llm.example/v1", model: "qwen-internal", headers: { "api-key": "k" } }] };
  const all = allProviders(s);
  assert.equal(all.at(-1).id, "custom-1");
  const p = activeProvider({ ...s, provider: "custom-1" });
  assert.equal(p.model, "qwen-internal");
  assert.equal(p.baseUrl, "https://llm.example/v1");
  assert.deepEqual(p.headers, { "api-key": "k" });
  assert.equal(customPreset({ id: "l", base_url: "http://localhost:5000/v1" }).cors, "needs-config");
});

test("the local CORS fix names this site's origin", () => {
  assert.match(localFix(PRESETS.find((p) => p.id === "ollama"), "https://science.impf.ai"), /OLLAMA_ORIGINS=https:\/\/science\.impf\.ai/);
  assert.match(localFix(PRESETS.find((p) => p.id === "lmstudio"), "https://science.impf.ai"), /Enable CORS/);
});

test("relayHealth reads the relay's health and never throws", async () => {
  const ok = await relayHealth({ baseUrl: "https://science.impf.ai/v1", fetch: fakeFetch(() => new Response(JSON.stringify({ ok: true, model: "Tao-S1", models: ["Tao-S1"], max_output_tokens: 8192, limits: { per_minute: 30, per_day: 600 } }))) });
  assert.deepEqual([ok.ok, ok.model, ok.max_output_tokens, ok.limits.per_day], [true, "Tao-S1", 8192, 600]);
  const unconf = await relayHealth({ fetch: fakeFetch(() => new Response(JSON.stringify({ ok: false }))) });
  assert.equal(unconf.ok, false);
  const down = await relayHealth({ fetch: fakeFetch(() => { throw new TypeError("Failed to fetch"); }) });
  assert.equal(down.ok, false);
  assert.match(down.error, /Failed to fetch/);
});

test("testConnection: a tool-calling round trip, with latency", async () => {
  const f = fakeFetch(() => streamResponse([sse([
    { choices: [{ index: 0, delta: { tool_calls: [{ index: 0, id: "c", function: { name: "ping", arguments: "{\"echo\":\"ok\"}" } }] } }] },
    { choices: [{ index: 0, delta: {}, finish_reason: "tool_calls" }] },
  ])]));
  const p = { ...activeProvider({ provider: "deepseek", keys: { deepseek: "k" } }), fetch: f };
  const r = await testConnection(p);
  assert.equal(r.ok, true);
  assert.equal(r.tools, true);
  assert.ok(r.latency_ms >= 0);
  assert.equal(f.calls[0].body.tools[0].function.name, "ping");
  assert.equal(f.calls[0].body.max_tokens, 1024);

  const textOnly = fakeFetch(() => streamResponse([sse([{ choices: [{ index: 0, delta: { content: "ok" } }] }])]));
  const r2 = await testConnection({ ...p, fetch: textOnly });
  assert.deepEqual([r2.ok, r2.tools, r2.reply], [true, false, "ok"]);

  const bad = fakeFetch(() => new Response(JSON.stringify({ error: { message: "invalid key" } }), { status: 401 }));
  const r3 = await testConnection({ ...p, fetch: bad });
  assert.equal(r3.ok, false);
  assert.equal(r3.status, 401);
  assert.match(r3.error, /invalid key/);

  const anth = fakeFetch(() => streamResponse([anthropicSse([
    { type: "content_block_start", index: 0, content_block: { type: "tool_use", id: "t", name: "ping", input: {} } },
    { type: "content_block_delta", index: 0, delta: { type: "input_json_delta", partial_json: "{\"echo\":\"ok\"}" } },
    { type: "message_delta", delta: { stop_reason: "tool_use" } },
  ])]));
  const r4 = await testConnection({ ...activeProvider({ provider: "anthropic", keys: { anthropic: "k" } }), fetch: anth });
  assert.equal(r4.tools, true);
  assert.equal(anth.calls[0].body.thinking, undefined, "the check runs without thinking");
});

test("relayHealth tells a relay or runner error in the page's language (the runner on another port has no relay)", async () => {
  const notRelay = { ok: false, service: "tcmstudio", relay: false, model: "Tao-S1", models: [], error: { type: "not_relay", message: "本机 Runner 不提供 Tao-S1。请在默认端口 8765 启动 Runner。" } };
  const fetchIt = () => fakeFetch(() => new Response(JSON.stringify(notRelay)));
  const zh = await relayHealth({ fetch: fetchIt() });
  assert.equal(zh.error, notRelay.error.message, "a Chinese page shows the runner's own sentence");
  assert.equal(zh.error_type, "not_relay");
  setLang("en");
  try {
    const en = await relayHealth({ fetch: fetchIt() });
    assert.match(en.error, /^This local runner does not provide Tao-S1\./);
    assert.doesNotMatch(en.error, /[\u3400-\u9fff]/);
    const off = await relayHealth({ fetch: fakeFetch(() => new Response(JSON.stringify({ ok: false }))) });
    assert.match(off.error, /^Tao-S1 is not available right now/);
    const blocked = await relayHealth({ fetch: fakeFetch(() => new Response(JSON.stringify({ error: { type: "forbidden_origin", message: "Tao-S1 只供 TCMScience Studio 网页使用" } }), { status: 403 })) });
    assert.equal(blocked.status, 403);
    assert.match(blocked.error, /^Tao-S1 serves only the TCMScience Studio page/);
    const odd = await relayHealth({ fetch: fakeFetch(() => new Response(JSON.stringify({ error: { type: "brand_new", message: "新情况" } }), { status: 500 })) });
    assert.equal(odd.error, "新情况");
    assert.equal(odd.error_lang, "zh");
  } finally {
    setLang("zh");
  }
});

test("presets say how they stream: increments for the known services; Tao-S1's reasoning arrives whole each time", () => {
  const by = Object.fromEntries(PRESETS.map((p) => [p.id, p]));
  assert.deepEqual(by.tao.stream, { content: "auto", reasoning: "cumulative" });
  for (const id of ["openai", "deepseek", "qwen", "moonshot", "zhipu", "siliconflow", "openrouter", "ollama", "lmstudio", "vllm", "llamacpp"]) assert.equal(by[id].stream, "delta", id);
  assert.equal(by.custom_openai.stream, undefined, "a custom endpoint is guessed");
  assert.equal(activeProvider({ provider: "tao" }).stream.reasoning, "cumulative");
});
