import "./fixtures/setup.mjs";
import assert from "node:assert/strict";
import { test } from "node:test";
import { DANGLING_TEXT, answerDangling, buildWireHistory, prepareWire, renumberCitations, runTurn } from "../js/core/agent.js";
import { Catalog } from "../js/core/catalog.js";
import { ProviderError } from "../js/core/llm/sse.js";
import { activeProvider } from "../js/core/providers.js";
import { ToolRouter } from "../js/core/router.js";
import { anthropicSse, envelopeFor, fakeFetch, fakeRuntime, memoryApprovals, openaiStep, scriptedLLM, sse, streamResponse, testCatalogDoc } from "./fixtures/fakes.mjs";

const catalog = new Catalog(testCatalogDoc());
const deepseek = { ...activeProvider({ provider: "deepseek", keys: { deepseek: "sk" } }), fetch: undefined };
const user = (content, id = "u1") => ({ id, role: "user", content, conversationId: "c1", parentId: null });

function makeRouter({ runner = "offline", approvals = memoryApprovals(), answer } = {}) {
  const runtimes = { browser: fakeRuntime("browser", { answer }), runner: fakeRuntime("runner", { status: runner, answer }) };
  return { router: new ToolRouter({ catalog, runtimes, settings: { compute: "auto" }, approvals }), runtimes };
}

function collector() {
  const events = [];
  const onEvent = (e) => events.push(e);
  return { events, onEvent, types: () => events.map((e) => e.type) };
}

test("multi-step tool use: the model calls tools, gets envelopes back, then answers; usage is summed", async () => {
  const llm = scriptedLLM([
    openaiStep({ text: "我先查一下。", reasoning: "需要查配伍", calls: [{ id: "a", name: "tcm_compatibility", args: { herbs: ["甘草", "甘遂"] } }, { id: "b", name: "tcm_herb", args: { name: "甘草" } }], usage: { input: 1000, output: 50 } }),
    openaiStep({ text: "甘草与甘遂：记载为十八反 [E1]。", usage: { input: 1500, output: 80 } }),
  ]);
  const { router, runtimes } = makeRouter();
  const c = collector();
  const res = await runTurn({ provider: deepseek, system: "SYS", history: [user("甘草和甘遂能同用吗？")], router, catalog, settings: {}, onEvent: c.onEvent, llm: { openai: llm }, projectId: "p", conversationId: "c1" });
  assert.equal(res.status, "ok");
  assert.equal(res.steps, 2);
  assert.deepEqual(res.usage, { input: 2500, output: 130 });
  assert.equal(res.assistant.content, "我先查一下。\n\n甘草与甘遂：记载为十八反 [E1]。");
  assert.equal(res.assistant.reasoning, "需要查配伍");
  assert.equal(res.assistant.parentId, "u1");
  assert.deepEqual(res.assistant.toolCalls.map((t) => t.name), ["tcm_compatibility", "tcm_herb"]);
  assert.deepEqual(res.assistant.segments.map((s) => s.type), ["reasoning", "text", "tools", "text"]);
  assert.equal(res.toolMessages.length, 2);
  assert.equal(res.toolMessages[0].parentId, res.assistant.id);
  assert.equal(res.toolMessages[0].envelope.receipt.where, "browser");
  assert.equal(runtimes.browser.calls.length, 2);
  // the second call saw: system prompt separately, the user message, the assistant tool calls, two tool results
  const second = llm.calls[1];
  assert.equal(second.system, "SYS");
  assert.deepEqual(second.messages.map((m) => m.role), ["user", "assistant", "tool", "tool"]);
  assert.equal(second.messages[2].tool_call_id, "a");
  assert.ok(second.tools.length > 0 && second.tools[0].type === "function");
  assert.equal(second.toolChoice, undefined);
  const types = c.types();
  for (const t of ["model.start", "reasoning", "text", "tool.start", "tool.end", "usage", "model.end", "done"]) assert.ok(types.includes(t), t);
  assert.deepEqual(c.events.filter((e) => e.type === "usage").map((e) => [e.input, e.output]), [[1000, 50], [2500, 130]]);
  assert.equal(c.events.find((e) => e.type === "tool.start").where, "browser");
  assert.equal(c.events.at(-1).type, "done");
  assert.equal(c.events.at(-1).status, "ok");
  // the stored wire is the exact provider exchange
  assert.deepEqual(res.assistant.wire.map((m) => m.role), ["assistant", "tool", "tool", "assistant"]);
  assert.equal(res.assistant.wireFormat, "openai");
});

test("max steps: the last allowed call is made with tool_choice none; the turn ends as max_steps", async () => {
  let n = 0;
  const llm = scriptedLLM([() => openaiStep({ text: `第${++n}步`, calls: [{ id: `t${n}`, name: "tcm_herb", args: { name: "葛根" } }] })]);
  const { router, runtimes } = makeRouter();
  const res = await runTurn({ provider: deepseek, system: "", history: [user("循环")], router, catalog, settings: { maxSteps: 3 }, llm: { openai: llm }, onEvent: () => {} });
  assert.equal(llm.calls.length, 3, "never more model calls than maxSteps");
  assert.equal(llm.calls[2].toolChoice, "none");
  assert.equal(res.status, "max_steps");
  assert.equal(res.assistant.limitReached, true);
  assert.equal(res.assistant.status, "ok");
  assert.equal(runtimes.browser.calls.length, 2, "tools requested on the final call are not run");
  const last = res.toolMessages.at(-1);
  assert.equal(last.envelope.status, "cancelled");
  assert.deepEqual(res.assistant.wire.at(-1), { role: "tool", tool_call_id: "t3", content: last.content }, "and still answered, so the history stays valid");
});

test("default limit is 16 model calls", async () => {
  const llm = scriptedLLM([() => openaiStep({ calls: [{ id: Math.random().toString(36), name: "tcm_herb", args: { name: "x" } }] })]);
  const res = await runTurn({ provider: deepseek, system: "", history: [user("x")], router: makeRouter().router, catalog, settings: {}, llm: { openai: llm }, onEvent: () => {} });
  assert.equal(llm.calls.length, 16);
  assert.equal(res.status, "max_steps");
});

test("abort mid-stream: what was said is kept, the turn is stopped, the history stays valid", async () => {
  const ctl = new AbortController();
  const llm = scriptedLLM([
    openaiStep({ calls: [{ id: "a", name: "tcm_herb", args: { name: "甘草" } }] }),
    [{ type: "text", delta: "甘草" }, { wait: async () => { ctl.abort(); } }, { type: "text", delta: "不会出现" }],
  ]);
  const c = collector();
  const res = await runTurn({ provider: deepseek, system: "", history: [user("甘草")], router: makeRouter().router, catalog, settings: {}, signal: ctl.signal, onEvent: c.onEvent, llm: { openai: llm } });
  assert.equal(res.status, "stopped");
  assert.equal(res.assistant.status, "stopped");
  assert.equal(res.assistant.content, "甘草");
  assert.deepEqual(res.assistant.wire.map((m) => m.role), ["assistant", "tool", "assistant"]);
  assert.equal(res.assistant.wire[2].content, "甘草");
  assert.ok(c.events.some((e) => e.type === "error" && e.kind === "aborted"));
  assert.deepEqual(c.events.at(-1), { type: "done", status: "stopped" });
});

test("abort while tools run: the calls are cancelled and answered", async () => {
  const ctl = new AbortController();
  const runtimes = { browser: fakeRuntime("browser", { delayMs: 200 }) };
  const router = new ToolRouter({ catalog, runtimes, settings: {} });
  const llm = scriptedLLM([openaiStep({ calls: [{ id: "a", name: "tcm_herb", args: { name: "甘草" } }] }), openaiStep({ text: "never" })]);
  setTimeout(() => ctl.abort(), 20);
  const res = await runTurn({ provider: deepseek, system: "", history: [user("x")], router, catalog, settings: {}, signal: ctl.signal, onEvent: () => {}, llm: { openai: llm } });
  assert.equal(res.status, "stopped");
  assert.equal(llm.calls.length, 1);
  assert.equal(res.toolMessages[0].envelope.status, "cancelled");
  assert.equal(res.assistant.wire.at(-1).role, "tool");
});

test("transient errors are retried twice (1.5 s, 4 s); the UI is told to drop the partial step", async () => {
  const delays = [];
  const flaky = new ProviderError("连接中断", { retryable: true, kind: "network" });
  const llm = scriptedLLM([[{ type: "text", delta: "半句" }, flaky], flaky, openaiStep({ text: "完整回答" })]);
  const c = collector();
  const res = await runTurn({ provider: deepseek, system: "", history: [user("x")], router: makeRouter().router, catalog, settings: {}, onEvent: c.onEvent, llm: { openai: llm }, sleep: async (ms) => { delays.push(ms); } });
  assert.equal(res.status, "ok");
  assert.deepEqual(delays, [1500, 4000]);
  assert.equal(res.assistant.content, "完整回答", "the partial text of a failed attempt is not part of the answer");
  assert.deepEqual(c.events.filter((e) => e.type === "model.retry").map((e) => e.attempt), [1, 2]);
});

test("after two retries the error ends the turn; a non-retryable error is not retried", async () => {
  const flaky = new ProviderError("服务暂时不可用", { retryable: true, status: 503 });
  const llm = scriptedLLM([flaky, flaky, flaky, openaiStep({ text: "too late" })]);
  const c = collector();
  const res = await runTurn({ provider: deepseek, system: "", history: [user("x")], router: makeRouter().router, catalog, settings: {}, onEvent: c.onEvent, llm: { openai: llm }, sleep: async () => {} });
  assert.equal(res.status, "error");
  assert.equal(llm.calls.length, 3);
  assert.equal(res.assistant.error.kind, "model");
  assert.ok(c.events.some((e) => e.type === "error" && /服务暂时不可用/.test(e.message)));

  const auth = scriptedLLM([new ProviderError("API Key 无效", { status: 401 })]);
  const r2 = await runTurn({ provider: deepseek, system: "", history: [user("x")], router: makeRouter().router, catalog, settings: {}, onEvent: () => {}, llm: { openai: auth }, sleep: async () => {} });
  assert.equal(r2.status, "error");
  assert.equal(auth.calls.length, 1);

  const net = scriptedLLM([new ProviderError("无法连接", { retryable: false, kind: "network" })]);
  const r3 = await runTurn({ provider: deepseek, system: "", history: [user("x")], router: makeRouter().router, catalog, settings: {}, onEvent: () => {}, llm: { openai: net } });
  assert.equal(r3.assistant.error.kind, "network");
});

test("approval denied: the tool does not run and the model reads the refusal", async () => {
  const llm = scriptedLLM([openaiStep({ calls: [{ id: "j", name: "run_pipeline", args: { pipeline: "fold", arguments: {} } }] }), openaiStep({ text: "未获批准，未运行折叠流程。" })]);
  const { router, runtimes } = makeRouter({ runner: "ready" });
  const c = collector();
  const asked = [];
  const res = await runTurn({ provider: deepseek, system: "", history: [user("折叠这个蛋白")], router, catalog, settings: {}, onEvent: c.onEvent, llm: { openai: llm }, projectId: "p", onApproval: async (req) => { asked.push(req); return "deny"; } });
  assert.equal(res.status, "ok");
  assert.equal(runtimes.runner.calls.length, 0);
  assert.equal(asked[0].callId, "j");
  assert.equal(asked[0].reason, "job");
  const approvalEvent = c.events.find((e) => e.type === "tool.approval");
  assert.equal(approvalEvent.callId, "j");
  assert.equal(res.toolMessages[0].envelope.status, "refused");
  const toolMsg = llm.calls[1].messages.find((m) => m.role === "tool");
  assert.match(toolMsg.content, /declined/);
});

test("approval through the event: request.respond(decision) when no onApproval is given", async () => {
  const llm = scriptedLLM([openaiStep({ calls: [{ id: "j", name: "run_pipeline", args: { pipeline: "fold" } }] }), openaiStep({ text: "已提交。" })]);
  const { router, runtimes } = makeRouter({ runner: "ready" });
  const res = await runTurn({
    provider: deepseek, system: "", history: [user("x")], router, catalog, settings: {}, llm: { openai: llm }, projectId: "p",
    onEvent: (e) => { if (e.type === "tool.approval") setTimeout(() => e.request.respond("once"), 1); },
  });
  assert.equal(res.status, "ok");
  assert.equal(runtimes.runner.calls.length, 1);
  assert.deepEqual(runtimes.runner.calls[0].ctx.approvals, ["job", "first_runner_call"]);
});

test("bad JSON arguments go back to the model as a bad_arguments result; nothing runs", async () => {
  const llm = scriptedLLM([
    [{ type: "tool_call", id: "a", name: "tcm_herb", arguments: {}, raw: "{name: 甘草}", error: "参数不是合法的 JSON：x" }, { type: "done", finish_reason: "tool_calls", wire: { role: "assistant", content: "", tool_calls: [{ id: "a", type: "function", function: { name: "tcm_herb", arguments: "{}" } }] } }],
    openaiStep({ text: "已更正。" }),
  ]);
  const { router, runtimes } = makeRouter();
  const res = await runTurn({ provider: deepseek, system: "", history: [user("x")], router, catalog, settings: {}, onEvent: () => {}, llm: { openai: llm } });
  assert.equal(runtimes.browser.calls.length, 0);
  assert.equal(res.toolMessages[0].envelope.error.type, "bad_arguments");
  assert.match(llm.calls[1].messages.at(-1).content, /\{name: 甘草\}/);
});

test("identity questions to the relay: thinking off, no reasoning shown or stored", async () => {
  const relay = activeProvider({ provider: "tao" });
  const llm = scriptedLLM([[
    { type: "reasoning", delta: "内部推理" }, { type: "text", delta: "我是 Tao-S1。" },
    { type: "done", finish_reason: "stop", wire: { role: "assistant", content: "<think>x</think>我是 Tao-S1。", reasoning_details: [{ type: "reasoning.text", text: "内部推理" }] } },
  ]]);
  const c = collector();
  const res = await runTurn({ provider: relay, system: "", history: [user("你是谁？用的什么大模型？")], router: makeRouter().router, catalog, settings: { thinking: true }, onEvent: c.onEvent, llm: { openai: llm } });
  assert.equal(llm.calls[0].thinking, false);
  assert.equal(c.events.filter((e) => e.type === "reasoning").length, 0);
  assert.equal(res.assistant.reasoning, "");
  assert.equal(res.assistant.hideReasoning, true);
  assert.equal(res.assistant.wire[0].reasoning_details, undefined);
  assert.equal(res.assistant.wire[0].content, "我是 Tao-S1。");
  const normal = scriptedLLM([openaiStep({ text: "ok" })]);
  await runTurn({ provider: relay, system: "", history: [user("桂枝汤的组成是什么？")], router: makeRouter().router, catalog, settings: {}, onEvent: () => {}, llm: { openai: normal } });
  assert.equal(normal.calls[0].thinking, true);
});

test("a model refusal ends the turn as a result", async () => {
  const llm = scriptedLLM([[{ type: "text", delta: "部分" }, { type: "done", finish_reason: "refusal", wire: null, refusal: { category: "bio", explanation: null } }]]);
  const anth = { ...activeProvider({ provider: "anthropic", keys: { anthropic: "k" } }) };
  const res = await runTurn({ provider: anth, system: "", history: [user("x")], router: makeRouter().router, catalog, settings: {}, onEvent: () => {}, llm: { anthropic: llm } });
  assert.equal(res.status, "ok");
  assert.deepEqual(res.assistant.refusal, { category: "bio", explanation: null });
  assert.equal(res.assistant.wire.length, 0);
});

test("end to end with the real OpenAI-compatible client over SSE", async () => {
  let n = 0;
  const f = fakeFetch(() => {
    n++;
    if (n === 1) {
      return streamResponse([sse([
        { choices: [{ index: 0, delta: { reasoning_content: "查一下" } }] },
        { choices: [{ index: 0, delta: { tool_calls: [{ index: 0, id: "call_1", function: { name: "tcm_herb", arguments: "{\"name\":" } }] } }] },
        { choices: [{ index: 0, delta: { tool_calls: [{ index: 0, function: { arguments: "\"甘草\"}" } }] } }] },
        { choices: [{ index: 0, delta: {}, finish_reason: "tool_calls" }] },
        { choices: [], usage: { prompt_tokens: 10, completion_tokens: 2 } },
      ])]);
    }
    return streamResponse([sse([{ choices: [{ index: 0, delta: { content: "甘草：记载为调和诸药 [E1]。" } }] }, { choices: [{ index: 0, delta: {}, finish_reason: "stop" }] }])]);
  });
  const p = { ...deepseek, fetch: f };
  const res = await runTurn({ provider: p, system: "SYS", history: [user("甘草")], router: makeRouter().router, catalog, settings: {}, onEvent: () => {} });
  assert.equal(res.status, "ok");
  assert.equal(res.assistant.content, "甘草：记载为调和诸药 [E1]。");
  assert.equal(res.assistant.reasoning, "查一下");
  const second = f.calls[1].body;
  assert.ok(JSON.stringify(second).startsWith('{"model":"deepseek-chat"'));
  assert.equal(second.messages[0].role, "system");
  assert.equal(second.messages[2].reasoning_content, "查一下", "DeepSeek gets its reasoning back within the turn");
  assert.equal(second.messages[3].role, "tool");
});

test("end to end with the real Anthropic client: tool_use, tool_result, final text", async () => {
  let n = 0;
  const f = fakeFetch(() => {
    n++;
    const evs = n === 1 ? [
      { type: "message_start", message: { model: "claude-opus-5-5", usage: { input_tokens: 50 } } },
      { type: "content_block_start", index: 0, content_block: { type: "tool_use", id: "toolu_1", name: "tcm_herb", input: {} } },
      { type: "content_block_delta", index: 0, delta: { type: "input_json_delta", partial_json: "{\"name\":\"葛根\"}" } },
      { type: "content_block_stop", index: 0 },
      { type: "message_delta", delta: { stop_reason: "tool_use" }, usage: { output_tokens: 7 } },
    ] : [
      { type: "message_start", message: { model: "claude-opus-5-5", usage: { input_tokens: 80 } } },
      { type: "content_block_start", index: 0, content_block: { type: "text", text: "" } },
      { type: "content_block_delta", index: 0, delta: { type: "text_delta", text: "葛根：记载于《神农本草经》。" } },
      { type: "message_delta", delta: { stop_reason: "end_turn" }, usage: { output_tokens: 12 } },
    ];
    return streamResponse([anthropicSse(evs)]);
  });
  const p = { ...activeProvider({ provider: "anthropic", keys: { anthropic: "k" } }), fetch: f };
  const res = await runTurn({ provider: p, system: "SYS", history: [user("葛根")], router: makeRouter().router, catalog, settings: {}, onEvent: () => {} });
  assert.equal(res.status, "ok");
  assert.deepEqual(res.usage, { input: 130, output: 19 });
  const second = f.calls[1].body;
  assert.deepEqual(second.messages.map((m) => m.role), ["user", "assistant", "user"]);
  assert.equal(second.messages[2].content[0].type, "tool_result");
  assert.equal(second.messages[2].content[0].tool_use_id, "toolu_1");
  assert.equal(second.tools[0].input_schema.type, "object");
});

// ------------------------------------------------------------------------------------------------- history replay

test("replay: <think> stripped, earlier tool results cut to 2 500 characters, foreign reasoning dropped", () => {
  const long = "x".repeat(6000);
  const history = [
    user("q1", "u1"),
    { id: "a1", role: "assistant", provider: "deepseek", model: "deepseek-chat", wireFormat: "openai", content: "答", wire: [
      { role: "assistant", content: "<think>私下推理</think>\n\n我查一下", reasoning_content: "r", tool_calls: [{ id: "t1", type: "function", function: { name: "tcm_herb", arguments: "{}" } }] },
      { role: "tool", tool_call_id: "t1", content: long },
      { role: "assistant", content: "答", reasoning_details: [{ text: "d" }] },
    ] },
    user("q2", "u2"),
  ];
  const out = buildWireHistory(history, deepseek);
  assert.deepEqual(out.map((m) => m.role), ["user", "assistant", "tool", "assistant", "user"]);
  assert.equal(out[1].content, "我查一下");
  assert.equal(out[1].reasoning_content, undefined);
  assert.ok(out[2].content.length < 2700);
  assert.match(out[2].content, /6000/);
  assert.equal(out[3].reasoning_details, undefined, "reasoning_details only go back to a reasoning_details provider");
});

test("replay: reasoning_details go back to the same relay; a turn in the other format is replayed as text", () => {
  const relay = activeProvider({ provider: "tao" });
  const turn = { id: "a1", role: "assistant", provider: "tao", wireFormat: "openai", content: "答", wire: [{ role: "assistant", content: "答", reasoning_details: [{ type: "reasoning.text", format: "Tao-v1", text: "d" }] }] };
  assert.deepEqual(buildWireHistory([user("q"), turn], relay)[1].reasoning_details, [{ type: "reasoning.text", format: "Tao-v1", text: "d" }]);
  const anth = activeProvider({ provider: "anthropic", keys: { anthropic: "k" } });
  const asAnthropic = buildWireHistory([user("q"), turn], anth);
  assert.deepEqual(asAnthropic[1], { role: "assistant", content: [{ type: "text", text: "答" }] });
  const claudeTurn = { id: "a2", role: "assistant", provider: "anthropic", wireFormat: "anthropic", content: "嗯", wire: [{ role: "assistant", content: [{ type: "thinking", thinking: "t", signature: "s" }, { type: "text", text: "嗯" }] }] };
  assert.equal(buildWireHistory([claudeTurn], anth)[0].content[0].type, "thinking", "same provider keeps its thinking blocks");
  const other = { ...anth, id: "custom_anthropic" };
  assert.deepEqual(buildWireHistory([claudeTurn], other)[0].content, [{ type: "text", text: "嗯" }]);
  assert.deepEqual(buildWireHistory([claudeTurn], deepseek)[0], { role: "assistant", content: "嗯" });
});

test("dangling tool calls get a placeholder result, in both formats", () => {
  const oa = answerDangling([{ role: "assistant", content: "", tool_calls: [{ id: "a" }, { id: "b" }] }, { role: "tool", tool_call_id: "a", content: "ok" }], "openai");
  assert.deepEqual(oa.map((m) => [m.role, m.tool_call_id, m.content]), [["assistant", undefined, ""], ["tool", "a", "ok"], ["tool", "b", DANGLING_TEXT]]);
  const an = answerDangling([{ role: "assistant", content: [{ type: "tool_use", id: "x", name: "n", input: {} }] }], "anthropic");
  assert.deepEqual(an[1], { role: "user", content: [{ type: "tool_result", tool_use_id: "x", content: DANGLING_TEXT, is_error: true }] });
  const prepared = prepareWire([{ role: "assistant", content: "" }], "openai");
  assert.deepEqual(prepared, [], "an empty assistant message is not sent");
});

test("citations are numbered across a turn: a second result's E1 becomes the next free id, in the text too", async () => {
  const cited = (tool, n) => envelopeFor(tool, "browser", {
    citations: Array.from({ length: n }, (_, i) => ({ id: `E${i + 1}`, kind: "source", label: `${tool} ${i + 1}`, url: "", evidence_ref: `${tool}.${i + 1}` })),
    text: `${tool} → native.${tool}: succeeded\nCitations (cite as [E1] …; do not invent others):\n${Array.from({ length: n }, (_, i) => `[E${i + 1}] ${tool} ${i + 1}`).join("\n")}\nResult: {"note":"[E9] stays"}`,
  });
  const llm = scriptedLLM([
    openaiStep({ calls: [{ id: "a", name: "tcm_compatibility", args: { herbs: ["甘草", "甘遂"] } }, { id: "b", name: "tcm_herb", args: { name: "甘草" } }] }),
    openaiStep({ calls: [{ id: "c", name: "tcm_herb", args: { name: "甘遂" } }] }),
    openaiStep({ text: "见 [E1]、[E3] 与 [E5]。" }),
  ]);
  // the second call of the first step finishes first: the numbering still follows the call order
  const answer = (tool, args) => new Promise((r) => setTimeout(() => r(cited(tool, tool === "tcm_compatibility" ? 2 : 2)), tool === "tcm_compatibility" ? 20 : 0));
  const { router } = makeRouter({ answer });
  const c = collector();
  const res = await runTurn({ provider: deepseek, system: "", history: [user("x")], router, catalog, settings: {}, onEvent: c.onEvent, llm: { openai: llm } });
  const ids = res.toolMessages.map((m) => m.envelope.citations.map((x) => x.id));
  assert.deepEqual(ids, [["E1", "E2"], ["E3", "E4"], ["E5", "E6"]]);
  assert.match(res.toolMessages[1].content, /^\[E3\] tcm_herb 1$/m);
  assert.match(res.toolMessages[1].content, /cite as \[E3\]/);
  assert.match(res.toolMessages[1].content, /\[E9\] stays/, "only the envelope's own ids move");
  assert.match(llm.calls[1].messages.at(-1).content, /^\[E4\] tcm_herb 2$/m, "the model reads the new ids");
  assert.match(llm.calls[2].messages.at(-1).content, /^\[E5\] tcm_herb 1$/m);
  assert.deepEqual(c.events.filter((e) => e.type === "tool.update").map((e) => e.callId), ["b", "c"]);
  // a lone result keeps its numbers
  const one = cited("tcm_herb", 3);
  assert.equal(renumberCitations(one, 1), 4);
  assert.deepEqual(one.citations.map((x) => x.id), ["E1", "E2", "E3"]);
  assert.equal(renumberCitations(envelopeFor("tcm_herb"), 7), 7, "no citations: nothing taken");
});
