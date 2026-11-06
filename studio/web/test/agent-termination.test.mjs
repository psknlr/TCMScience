// How a turn ends (P1.5): the answer asked for after the step limit, a cut-off answer said so, and a named termination
// on every exit. Ported from the agent-framework probes scripts/probe-maxsteps.mjs and probe-length.mjs (530031f: five
// calls were four, content '', notice null on a cut-off answer).
import "./fixtures/setup.mjs";
import assert from "node:assert/strict";
import { test } from "node:test";
import { ANSWER_TEXT, TERMINATIONS, runTurn } from "../js/core/agent.js";
import { Catalog } from "../js/core/catalog.js";
import { ProviderError } from "../js/core/llm/sse.js";
import { activeProvider } from "../js/core/providers.js";
import { ToolRouter, synthEnvelope } from "../js/core/router.js";
import { anthropicSse, envelopeFor, fakeFetch, fakeRuntime, openaiStep, scriptedLLM, sse, streamResponse, testCatalogDoc } from "./fixtures/fakes.mjs";

const catalog = new Catalog(testCatalogDoc());
const deepseek = { ...activeProvider({ provider: "deepseek", keys: { deepseek: "sk" } }), fetch: undefined };
const anthropic = { ...activeProvider({ provider: "anthropic", keys: { anthropic: "k" } }), fetch: undefined };
const user = (content, id = "u1") => ({ id, role: "user", content, conversationId: "c1", parentId: null });
const router = () => new ToolRouter({ catalog, runtimes: { browser: fakeRuntime("browser") }, settings: { compute: "auto" } });
// a router whose every call answers with `text` (a large result)
const bulky = (text) => ({
  resolve: () => ({ target: { kind: "native", id: "tcm.herb" }, entry: { id: "native.tcm_herb" } }),
  call: async (name, args) => ({ ...(await synthEnvelope({ tool: name, args, status: "succeeded" })), ok: true, status: "succeeded", text }),
});
const sseOf = (chunks) => streamResponse([sse(chunks)]);

test("probe-maxsteps: after the limit, one more call with no tools and no tool_choice gets an answer from the results", async () => {
  // a provider that calls tools whenever it is offered any, tool_choice "none" or not (the probe's), and answers when
  // it is offered none
  let n = 0;
  const f = fakeFetch((url, init, k, call) => {
    n++;
    if (!call.body.tools) return sseOf([{ choices: [{ index: 0, delta: { content: "据已有结果：葛根记载为解肌退热。缺少第 4 步的结果。" }, finish_reason: "stop" }] }]);
    return sseOf([{ choices: [{ index: 0, delta: { tool_calls: [{ index: 0, id: `c${n}`, type: "function", function: { name: "tcm_herb", arguments: JSON.stringify({ name: `h${n}` }) } }] }, finish_reason: "tool_calls" }] }]);
  });
  const events = [];
  const res = await runTurn({ provider: { ...deepseek, fetch: f }, system: "s", history: [user("q")], router: router(), catalog, settings: { maxSteps: 4 }, onEvent: (e) => events.push(e) });
  assert.equal(f.calls.length, 5, "5 model calls");
  assert.deepEqual(f.calls.map((c) => c.body.tool_choice), ["auto", "auto", "auto", "none", undefined]);
  const fifth = f.calls[4].body;
  assert.equal("tools" in fifth, false, "the 5th request has no tools field");
  assert.equal("tool_choice" in fifth, false, "and no tool_choice");
  assert.equal(fifth.messages.at(-1).role, "user");
  assert.match(fifth.messages.at(-1).content, /Answer now from the results above/);
  assert.equal(fifth.messages.at(-2).role, "tool", "the asked-for tools of the 4th call are answered (not run) before it");
  assert.equal(res.assistant.content, "据已有结果：葛根记载为解肌退热。缺少第 4 步的结果。", "content is non-empty");
  assert.equal(res.status, "max_steps");
  assert.equal(res.assistant.status, "ok");
  assert.equal(res.assistant.termination, "max_steps_synthesized");
  assert.equal(res.assistant.limitReached, true);
  assert.match(res.assistant.notice, /已达到每轮最多 4 次模型调用的上限/, "the step-limit notice is kept");
  assert.deepEqual(res.toolMessages.slice(-1).map((m) => m.envelope.status), ["cancelled"]);
  assert.ok(!res.assistant.wire.some((m) => m.role === "user"), "the request for the answer is not kept in the turn's messages");
  assert.deepEqual(events.at(-1), { type: "done", status: "max_steps", termination: "max_steps_synthesized" });
});

test("Anthropic: the call for the answer keeps the tools (its tool_use history needs them) with tool_choice none", async () => {
  let n = 0;
  const f = fakeFetch(() => {
    n++;
    const start = { type: "message_start", message: { model: "claude-opus-5-5", usage: { input_tokens: 50 } } };
    // a scripted service that ignores tool_choice none on the last allowed call (the real API enforces it)
    const evs = n < 5 ? [start,
      { type: "content_block_start", index: 0, content_block: { type: "tool_use", id: `toolu_${n}`, name: "tcm_herb", input: {} } },
      { type: "content_block_delta", index: 0, delta: { type: "input_json_delta", partial_json: JSON.stringify({ name: `h${n}` }) } },
      { type: "content_block_stop", index: 0 },
      { type: "message_delta", delta: { stop_reason: "tool_use" }, usage: { output_tokens: 7 } }]
      : [start,
        { type: "content_block_start", index: 0, content_block: { type: "text", text: "" } },
        { type: "content_block_delta", index: 0, delta: { type: "text_delta", text: "据已有结果作答。" } },
        { type: "message_delta", delta: { stop_reason: "end_turn" }, usage: { output_tokens: 9 } }];
    return streamResponse([anthropicSse(evs)]);
  });
  const res = await runTurn({ provider: { ...anthropic, fetch: f }, system: "s", history: [user("q")], router: router(), catalog, settings: { maxSteps: 4, thinking: false }, onEvent: () => {} });
  assert.equal(f.calls.length, 5);
  assert.deepEqual(f.calls.map((c) => c.body.tool_choice?.type), ["auto", "auto", "auto", "none", "none"]);
  const fifth = f.calls[4].body;
  assert.deepEqual(fifth.tool_choice, { type: "none" });
  assert.ok(fifth.tools.length > 0);
  const last = fifth.messages.at(-1);
  assert.equal(last.role, "user");
  assert.deepEqual(last.content.map((b) => b.type), ["tool_result", "text"], "the ask joins the tool results' message: roles still alternate");
  assert.equal(last.content.at(-1).text, ANSWER_TEXT.max_steps);
  assert.equal(res.assistant.content, "据已有结果作答。");
  assert.equal(res.assistant.termination, "max_steps_synthesized");
});

test("probe-length: an answer cut off at the output limit says so, and how it ended", async () => {
  const cut = { ...deepseek, relay: true, id: "tao", fetch: async () => sseOf([{ choices: [{ index: 0, delta: { content: "桂枝汤的组成是桂枝、芍药、生姜、大枣、甘" }, finish_reason: "length" }] }]) };
  const res = await runTurn({ provider: cut, system: "s", history: [user("q")], router: null, catalog: { core: [] }, settings: {} });
  assert.equal(res.status, "ok");
  assert.equal(res.assistant.finishReason, "length");
  assert.equal(res.assistant.termination, "truncated");
  assert.ok(res.assistant.notice, "notice is non-null");
  assert.match(res.assistant.notice, /在此处被截断/);
  assert.equal(res.assistant.limitReached, undefined);
  // Anthropic says max_tokens
  const llm = scriptedLLM([openaiStep({ text: "葛根汤由葛根、麻黄", finish: "max_tokens" })]);
  const a = await runTurn({ provider: anthropic, system: "", history: [user("q")], router: null, catalog: { core: [] }, settings: {}, llm: { anthropic: llm } });
  assert.equal(a.assistant.termination, "truncated");
  // a cut-off reasoning with no answer text is still cut off
  const empty = await runTurn({ provider: deepseek, system: "", history: [user("q")], router: null, catalog: { core: [] }, settings: {}, llm: { openai: scriptedLLM([openaiStep({ reasoning: "想了很久", finish: "length" })]) } });
  assert.equal(empty.assistant.termination, "truncated");
});

test("the call for the answer failing ends the turn as before: at the step limit, its notice kept, no error", async () => {
  let k = 0;
  const llm = scriptedLLM([
    () => openaiStep({ calls: [{ id: `t${++k}`, name: "tcm_herb", args: { name: `h${k}` } }] }),
    () => openaiStep({ calls: [{ id: `t${++k}`, name: "tcm_herb", args: { name: `h${k}` } }] }),
    new ProviderError("upstream broke", { status: 400, type: "bad_request" }),
  ]);
  const res = await runTurn({ provider: deepseek, system: "", history: [user("q")], router: router(), catalog, settings: { maxSteps: 2 }, llm: { openai: llm }, onEvent: () => {} });
  assert.equal(llm.calls.length, 3);
  assert.equal(res.status, "max_steps");
  assert.equal(res.assistant.status, "ok");
  assert.equal(res.assistant.error, undefined);
  assert.equal(res.assistant.termination, "max_steps");
  assert.match(res.assistant.notice, /已达到每轮最多 2 次模型调用的上限/);

  // broken off mid-stream: what it had begun to say is no answer, and is not kept as one (nor sent again later)
  k = 0;
  const cut = scriptedLLM([
    () => openaiStep({ calls: [{ id: `t${++k}`, name: "tcm_herb", args: { name: `h${k}` } }] }),
    () => openaiStep({ calls: [{ id: `t${++k}`, name: "tcm_herb", args: { name: `h${k}` } }] }),
    [{ type: "reasoning", delta: "先看结果" }, { type: "text", delta: "据已有结果，葛根的主要" }, new ProviderError("stream broke", { status: 400, type: "bad_request" })],
  ]);
  const broken = await runTurn({ provider: deepseek, system: "", history: [user("q")], router: router(), catalog, settings: { maxSteps: 2 }, llm: { openai: cut }, onEvent: () => {}, retryDelays: [] });
  assert.equal(cut.calls.length, 3);
  assert.equal(broken.assistant.status, "ok");
  assert.equal(broken.assistant.termination, "max_steps");
  assert.equal(broken.assistant.content, "");
  assert.equal(broken.assistant.reasoning, "");
  assert.deepEqual(broken.assistant.segments.map((s) => s.type), ["tools", "tools"]);
  assert.equal(broken.assistant.wire.at(-1).role, "tool", "the turn's messages end with the results, not a half-sentence");
  assert.equal(broken.assistant.error, undefined);
  assert.match(broken.assistant.notice, /已达到每轮最多 2 次模型调用的上限/);
});

test("every way a turn ends is named (termination), and the status values are the ones they were", async () => {
  const rows = [];
  const run = async (name, opts) => {
    const events = [];
    const res = await runTurn({ provider: deepseek, system: "", history: [user("q")], router: router(), catalog, settings: {}, onEvent: (e) => events.push(e), retryDelays: [], ...opts });
    rows.push([name, res.assistant.termination, res.status, res.assistant.status]);
    assert.equal(events.at(-1).type, "done");
    assert.equal(events.at(-1).termination, res.assistant.termination, name);
    return res;
  };
  const step = (k) => openaiStep({ calls: [{ id: `t${k}`, name: "tcm_herb", args: { name: `h${k}` } }] });
  await run("plain answer", { llm: { openai: scriptedLLM([openaiStep({ text: "答" })]) } });
  await run("limit, then the answer", { settings: { maxSteps: 2 }, llm: { openai: scriptedLLM([step(1), openaiStep({ text: "据已有结果作答" })]) } });
  await run("limit, no answer", { settings: { maxSteps: 2 }, llm: { openai: scriptedLLM([step(1), step(2), step(3)]) } });
  // a small window: the second request, with a large result, does not fit with tools offered
  const small = { ...deepseek, contextTokens: 3000, maxTokens: 100 };
  const ctxLlm = scriptedLLM([step(1), openaiStep({ text: "据已有结果作答" })]);
  const ctx = await run("context full, then the answer", { provider: small, router: bulky("x".repeat(20000)), llm: { openai: ctxLlm } });
  assert.deepEqual(ctxLlm.calls[1].tools, [], "no tools offered once nothing more fits");
  assert.match(ctxLlm.calls[1].messages.at(-1).content, /fill the model's context/);
  assert.match(ctx.assistant.notice, /已占满模型的上下文/);
  assert.doesNotMatch(ctx.assistant.notice, /上下文窗口/, "a preset states its window: nothing to set");
  // a local server or a custom endpoint: its window is what Settings say, and the notice says where
  const mine = { ...activeProvider({ provider: "c1", customProviders: [{ id: "c1", base_url: "http://127.0.0.1:9/v1", model: "m" }] }), contextTokens: 3000, maxTokens: 100, fetch: undefined };
  const own = await runTurn({ provider: mine, system: "", history: [user("q")], router: bulky("x".repeat(20000)), catalog, settings: {}, onEvent: () => {}, llm: { openai: scriptedLLM([step(1), openaiStep({ text: "据已有结果作答" })]) } });
  assert.equal(own.assistant.termination, "context_synthesized");
  assert.match(own.assistant.notice, /这个模型在设置中的上下文窗口是 3000 个 token；如果它能读入更多，请在设置的「模型」中调高「上下文窗口」。/);
  await run("context full, no answer", { provider: small, router: bulky("x".repeat(20000)), llm: { openai: scriptedLLM([step(1), step(2)]) } });
  await run("cut off", { llm: { openai: scriptedLLM([openaiStep({ text: "半句", finish: "length" })]) } });
  const ctl = new AbortController();
  await run("stopped", { signal: ctl.signal, llm: { openai: scriptedLLM([[{ type: "text", delta: "甘" }, { wait: async () => ctl.abort() }, { type: "text", delta: "不会出现" }]]) } });
  await run("refused", { llm: { openai: scriptedLLM([[{ type: "done", finish_reason: "refusal", wire: null, refusal: { category: "bio", explanation: null } }]]) } });
  await run("model error", { llm: { openai: scriptedLLM([new ProviderError("bad", { status: 400, type: "invalid_request_error" })]) } });
  await run("network error", { llm: { openai: scriptedLLM([new ProviderError("down", { kind: "network", retryable: true })]) } });
  await run("runtime error", { llm: { openai: scriptedLLM([new TypeError("broken client")]) } });
  assert.deepEqual(rows, [
    ["plain answer", "answered", "ok", "ok"],
    ["limit, then the answer", "max_steps_synthesized", "max_steps", "ok"],
    ["limit, no answer", "max_steps", "max_steps", "ok"],
    ["context full, then the answer", "context_synthesized", "ok", "ok"],
    ["context full, no answer", "context_full", "ok", "ok"],
    ["cut off", "truncated", "ok", "ok"],
    ["stopped", "stopped", "stopped", "stopped"],
    ["refused", "refused_by_model", "ok", "ok"],
    ["model error", "error:model", "error", "error"],
    ["network error", "error:network", "error", "error"],
    ["runtime error", "error:runtime", "error", "error"],
  ]);
  for (const [, termination] of rows) assert.ok(TERMINATIONS.includes(termination), termination);
  assert.deepEqual([...new Set(rows.map((r) => r[1]))].sort(), [...TERMINATIONS].sort(), "every named termination is reached by some exit");
  // the stored status values are the ones the page and the store already know
  assert.ok(rows.every((r) => ["ok", "stopped", "error"].includes(r[3])));
  assert.ok(envelopeFor("x"));
});
