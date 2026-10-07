import "./fixtures/setup.mjs";
import assert from "node:assert/strict";
import { test } from "node:test";
import { anthropicTools, buildRequest, streamChat, thinkingParam, toolResultMessages } from "../js/core/llm/anthropic.js";
import { anthropicSse, bytePieces, fakeFetch, streamResponse } from "./fixtures/fakes.mjs";

function provider(overrides = {}) {
  return {
    id: "anthropic", label: "Anthropic Claude", format: "anthropic", baseUrl: "https://api.anthropic.com/v1", model: "claude-opus-5-5",
    apiKey: "sk-ant-test", maxTokens: 8192, temperature: null, route: "direct", tools: true, key_required: true, ...overrides,
  };
}

const start = (index, content_block) => ({ type: "content_block_start", index, content_block });
const delta = (index, d) => ({ type: "content_block_delta", index, delta: d });
const stop = (index) => ({ type: "content_block_stop", index });

function stream(events, n) {
  const text = anthropicSse(events);
  return n ? bytePieces(text, n) : [text];
}

async function run(events, { p = provider(), n, params = {} } = {}) {
  const f = fakeFetch(() => streamResponse(stream(events, n)));
  const out = [];
  for await (const ev of streamChat({ provider: { ...p, fetch: f }, system: "SYS", messages: [{ role: "user", content: "hi" }], ...params })) out.push(ev);
  return { out, fetch: f, done: out.find((e) => e.type === "done") };
}

const toolTurn = [
  { type: "message_start", message: { id: "msg_1", model: "claude-opus-5-5", usage: { input_tokens: 900, cache_read_input_tokens: 100, output_tokens: 1 } } },
  start(0, { type: "thinking", thinking: "", signature: "" }),
  delta(0, { type: "thinking_delta", thinking: "先查" }),
  delta(0, { type: "thinking_delta", thinking: "配伍。" }),
  delta(0, { type: "signature_delta", signature: "sig-abc" }),
  stop(0),
  start(1, { type: "text", text: "" }),
  delta(1, { type: "text_delta", text: "我先查" }),
  delta(1, { type: "text_delta", text: "一下。" }),
  stop(1),
  start(2, { type: "tool_use", id: "toolu_1", name: "tcm_compatibility", input: {} }),
  delta(2, { type: "input_json_delta", partial_json: "{\"herbs\": [\"甘" }),
  delta(2, { type: "input_json_delta", partial_json: "草\", \"甘遂\"]}" }),
  stop(2),
  { type: "message_delta", delta: { stop_reason: "tool_use" }, usage: { output_tokens: 42 } },
  { type: "message_stop" },
];

test("a streamed turn: thinking (with signature), text, a tool call assembled from input_json_delta, usage", async () => {
  for (const n of [undefined, 7]) {
    const { out, done } = await run(toolTurn, { n });
    assert.equal(out.filter((e) => e.type === "reasoning").map((e) => e.delta).join(""), "先查配伍。");
    assert.equal(out.filter((e) => e.type === "text").map((e) => e.delta).join(""), "我先查一下。");
    const call = out.find((e) => e.type === "tool_call");
    assert.deepEqual([call.id, call.name, call.arguments], ["toolu_1", "tcm_compatibility", { herbs: ["甘草", "甘遂"] }]);
    assert.deepEqual(out.find((e) => e.type === "usage"), { type: "usage", input: 1000, output: 42 });
    assert.equal(done.finish_reason, "tool_use");
    assert.deepEqual(done.wire, {
      role: "assistant",
      content: [
        { type: "thinking", thinking: "先查配伍。", signature: "sig-abc" },
        { type: "text", text: "我先查一下。" },
        { type: "tool_use", id: "toolu_1", name: "tcm_compatibility", input: { herbs: ["甘草", "甘遂"] } },
      ],
    });
  }
});

test("request: headers for a browser call, tools with input_schema, adaptive thinking, refusal fallbacks on the first-party API", async () => {
  const { fetch } = await run(toolTurn, { params: { tools: [{ name: "tcm_herb", description: "d", parameters: { type: "object", properties: { name: { type: "string" } } } }] } });
  const { url, headers, body } = fetch.calls[0];
  assert.equal(url, "https://api.anthropic.com/v1/messages");
  assert.equal(headers["x-api-key"], "sk-ant-test");
  assert.equal(headers["anthropic-version"], "2023-06-01");
  assert.equal(headers["anthropic-dangerous-direct-browser-access"], "true");
  assert.equal(headers["anthropic-beta"], "server-side-fallback-2026-07-01");
  assert.equal(body.fallbacks, "default");
  assert.equal(body.system, "SYS");
  assert.equal(body.stream, true);
  assert.deepEqual(body.thinking, { type: "adaptive", display: "summarized" });
  assert.deepEqual(body.tools[0], { name: "tcm_herb", description: "d", input_schema: { type: "object", properties: { name: { type: "string" } } }, eager_input_streaming: true });
  assert.deepEqual(body.tool_choice, { type: "auto" });
  assert.equal(body.temperature, undefined);
});

test("thinking: never 'disabled'; budget for older models; nothing for other services' models", () => {
  assert.equal(thinkingParam("claude-opus-5-5", false, 8192, true), undefined);
  assert.deepEqual(thinkingParam("claude-sonnet-5-5", true, 8192, true), { type: "adaptive", display: "summarized" });
  assert.deepEqual(thinkingParam("claude-haiku-4-5", true, 8192, true), { type: "enabled", budget_tokens: 7168 });
  assert.equal(thinkingParam("claude-haiku-4-5", true, 1500, true), undefined);
  assert.equal(thinkingParam("deepseek-chat", true, 8192, false), undefined);
  const viaRunner = buildRequest({ provider: provider({ model: "claude-haiku-4-5", temperature: 0.2, route: "runner", runner: { url: "http://127.0.0.1:8765", token: "t" } }), system: "", messages: [], thinking: false, toolChoice: "none", tools: [{ name: "a", parameters: {} }] });
  assert.equal(viaRunner.url, "http://127.0.0.1:8765/api/llm");
  assert.equal(viaRunner.headers["X-TCM-Target"], "https://api.anthropic.com/v1/messages");
  assert.equal(viaRunner.headers["X-TCM-Token"], "t");
  assert.equal(viaRunner.body.temperature, 0.2, "sampling is allowed on Haiku 4.5 without thinking");
  assert.deepEqual(viaRunner.body.tool_choice, { type: "none" });
  assert.equal(viaRunner.body.fallbacks, undefined, "fallbacks only for the newest models");
  const custom = buildRequest({ provider: provider({ baseUrl: "https://gateway.example/anthropic/v1", model: "claude-opus-5-5" }), system: "", messages: [], tools: [{ name: "a", parameters: {} }] });
  assert.equal(custom.body.fallbacks, undefined, "only on api.anthropic.com");
  assert.equal(custom.body.tools[0].eager_input_streaming, undefined);
  assert.deepEqual(anthropicTools([{ type: "function", function: { name: "x", description: "y", parameters: { type: "object" } } }]), [{ name: "x", description: "y", input_schema: { type: "object" } }]);
});

test("a tool input that is not valid JSON is an error for the model and is replayed as {}", async () => {
  const { out, done } = await run([
    { type: "message_start", message: { model: "claude-opus-5-5", usage: { input_tokens: 10 } } },
    start(0, { type: "tool_use", id: "t1", name: "tcm_herb", input: {} }),
    delta(0, { type: "input_json_delta", partial_json: "{\"name\": 甘草}" }),
    stop(0),
    { type: "message_delta", delta: { stop_reason: "tool_use" }, usage: { output_tokens: 5 } },
  ]);
  const call = out.find((e) => e.type === "tool_call");
  assert.match(call.error, /JSON/);
  assert.deepEqual(done.wire.content[0].input, {});
});

test("max_tokens with a tool call: the call is not run", async () => {
  const { out } = await run([
    start(0, { type: "tool_use", id: "t1", name: "tcm_herb", input: {} }),
    delta(0, { type: "input_json_delta", partial_json: "{\"name\": \"甘" }),
    { type: "message_delta", delta: { stop_reason: "max_tokens" }, usage: { output_tokens: 8192 } },
  ]);
  assert.match(out.find((e) => e.type === "tool_call").error, /上限|limit/);
});

test("a refusal: no tool runs, nothing is replayed, the category is reported", async () => {
  const { out, done } = await run([
    start(0, { type: "text", text: "" }),
    delta(0, { type: "text_delta", text: "部分" }),
    start(1, { type: "tool_use", id: "t1", name: "tcm_herb", input: {} }),
    { type: "message_delta", delta: { stop_reason: "refusal", stop_details: { type: "refusal", category: "bio", explanation: null } } },
  ]);
  assert.equal(out.filter((e) => e.type === "tool_call").length, 0);
  assert.equal(done.finish_reason, "refusal");
  assert.equal(done.wire, null);
  assert.deepEqual(done.refusal, { category: "bio", explanation: null });
});

test("after a server-side fallback, only the text of the declined attempt is replayed", async () => {
  const { out, done } = await run([
    { type: "message_start", message: { model: "claude-opus-4-8" } },
    start(0, { type: "thinking", thinking: "", signature: "" }),
    delta(0, { type: "thinking_delta", thinking: "x" }),
    start(1, { type: "text", text: "前文" }),
    start(2, { type: "tool_use", id: "t0", name: "tcm_herb", input: {} }),
    start(3, { type: "fallback", from: { model: "claude-opus-5-5" }, to: { model: "claude-opus-4-8" } }),
    start(4, { type: "text", text: "续写" }),
    start(5, { type: "tool_use", id: "t1", name: "tcm_herb", input: { name: "葛根" } }),
    { type: "message_delta", delta: { stop_reason: "tool_use" } },
  ]);
  assert.deepEqual(done.wire.content.map((b) => b.type), ["text", "text", "tool_use"]);
  assert.deepEqual(out.filter((e) => e.type === "tool_call").map((c) => c.id), ["t1"]);
  assert.deepEqual(done.fallback, { from: "claude-opus-5-5", to: "claude-opus-4-8" });
  assert.equal(done.model, "claude-opus-4-8");
});

test("errors: an error event in the stream, HTTP 529 overloaded (retryable), 401", async () => {
  await assert.rejects(run([{ type: "error", error: { type: "overloaded_error", message: "Overloaded" } }]), (e) => e.retryable === true && /Overloaded/.test(e.message));
  const f529 = fakeFetch(() => new Response(JSON.stringify({ type: "error", error: { type: "overloaded_error", message: "Overloaded" } }), { status: 529 }));
  await assert.rejects(async () => { for await (const _ of streamChat({ provider: { ...provider(), fetch: f529 }, messages: [] })); }, (e) => e.retryable && e.status === 529);
  const f401 = fakeFetch(() => new Response(JSON.stringify({ type: "error", error: { type: "authentication_error", message: "invalid x-api-key" } }), { status: 401 }));
  await assert.rejects(async () => { for await (const _ of streamChat({ provider: { ...provider(), fetch: f401 }, messages: [] })); }, (e) => !e.retryable && /invalid x-api-key/.test(e.message));
});

test("tool results go back in one user message; only failures are errors", () => {
  assert.deepEqual(toolResultMessages([{ id: "a", text: "ok" }, { id: "b", text: "Not run: declined", isError: false }, { id: "c", text: "Failed", isError: true }]), [{
    role: "user",
    content: [
      { type: "tool_result", tool_use_id: "a", content: "ok" },
      { type: "tool_result", tool_use_id: "b", content: "Not run: declined" },
      { type: "tool_result", tool_use_id: "c", content: "Failed", is_error: true },
    ],
  }]);
});
