// How large a turn's requests grow (P2.1), from the agent-framework probe scripts/probe-growth.mjs: the real agent
// loop and OpenAI-compatible client, a model that keeps calling tools with new arguments, 16 000-character results. At
// 530031f the requests grew without bound: 287 KiB (1 call a step), 999 KiB (4), 1 948 KiB (8), and 2 026 KiB at step
// 15 with 3 CJK results, past the relay's 2 000 000-byte cap. Every request now fits the context budget.
import "./fixtures/setup.mjs";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { buildWireHistory, runTurn } from "../js/core/agent.js";
import { budgetOf, measure, tokensOf } from "../js/core/budget.js";
import { buildSystemPrompt } from "../js/core/prompt.js";
import { activeProvider } from "../js/core/providers.js";
import { synthEnvelope } from "../js/core/router.js";
import { evidenceStats } from "../js/core/turnrecord.js";
import { ProviderError } from "../js/core/llm/sse.js";
import { fakeFetch, openaiStep, scriptedLLM, sse, streamResponse } from "./fixtures/fakes.mjs";

const RESULT = 16000;
const RELAY_CAP = 2_000_000;
// 27 core tools of about the size the real catalog sends (38 634 characters of schemas, scripts/measure.mjs)
const catalog = {
  core: Array.from({ length: 27 }, (_, i) => ({
    name: i ? `tool_${i}` : "literature_search", description: `Tool ${i}. ${"Looks things up in a governed source and returns an envelope. ".repeat(20)}`,
    parameters: { type: "object", properties: { query: { type: "string", description: "what to look for" }, limit: { type: "integer" } }, required: ["query"] },
  })),
};
const system = buildSystemPrompt({ lang: "zh", provider: { relay: true }, project: { name: "p" }, env: { date: "2026-11-06" } });
const router = (char) => ({
  resolve: () => ({ target: { kind: "connector", id: "literature.search" }, entry: { id: "connector.europepmc.search" } }),
  call: async (name, args) => ({ ...(await synthEnvelope({ tool: name, args, status: "succeeded", text: "" })), ok: true, status: "succeeded", text: char.repeat(RESULT) }),
});

/** A model that calls `par` tools at once whenever it is offered tools, each with new arguments, and answers when not. */
function growing(par) {
  let n = 0;
  return fakeFetch((url, init, k, call) => {
    n++;
    if (!call.body.tools) return streamResponse([sse([{ choices: [{ index: 0, delta: { content: `据已有的 ${call.body.messages.filter((m) => m.role === "tool").length} 个结果作答。` }, finish_reason: "stop" }] }])]);
    const calls = Array.from({ length: par }, (_, j) => ({ index: j, id: `c${n}_${j}`, type: "function", function: { name: "literature_search", arguments: JSON.stringify({ query: `q${n}-${j}` }) } }));
    return streamResponse([sse([{ choices: [{ index: 0, delta: { tool_calls: calls }, finish_reason: "tool_calls" }] }])]);
  });
}

async function turn(provider, par, char) {
  const f = growing(par);
  const res = await runTurn({ provider: { ...provider, fetch: f }, system, history: [{ id: "u", role: "user", content: "研究问题" }], router: router(char), catalog, settings: {}, sleep: async () => {} });
  const sizes = f.calls.map((c) => ({ bytes: Buffer.byteLength(c.rawBody), tokens: tokensOf(measure(c.rawBody).units) }));
  return { res, sizes, calls: f.calls };
}

const relay = { ...activeProvider({ provider: "tao" }, { maxOutput: 8192 }), baseUrl: "https://science.impf.ai/v1" };
const local = { ...activeProvider({ provider: "custom-x", customProviders: [{ id: "custom-x", base_url: "http://127.0.0.1:9/v1", model: "m", context_tokens: 32000 }] }) };

test("the presets the matrix runs on: the relay's budget is 0.9 × its body cap, a 32 000-token model's 0.8 × its window less the answer", () => {
  assert.deepEqual([relay.contextTokens, relay.maxBodyBytes, relay.maxTokens], [128000, RELAY_CAP, 8192]);
  assert.deepEqual(budgetOf(relay), { tokens: 94208, bytes: 1800000 });
  assert.deepEqual([local.contextTokens, local.maxTokens], [32000, 8192]);
  assert.deepEqual(budgetOf(local), { tokens: 17408, bytes: Infinity });
});

for (const [label, char] of [["ASCII", "x"], ["CJK", "中"]]) {
  for (const par of [1, 4, 8]) {
    test(`growth, ${par} call(s) a step, ${label} results: every relay request within budget and under 2 000 000 bytes; an answer at the end`, async () => {
      const { res, sizes } = await turn(relay, par, char);
      const budget = budgetOf(relay);
      for (const [i, s] of sizes.entries()) {
        assert.ok(s.bytes <= budget.bytes && s.bytes < RELAY_CAP, `request ${i + 1}: ${s.bytes} bytes`);
        assert.ok(s.tokens <= budget.tokens, `request ${i + 1}: ${s.tokens} tokens`);
      }
      assert.ok(res.assistant.content.trim(), "the turn ends with an answer");
      assert.ok(["max_steps_synthesized", "context_synthesized"].includes(res.assistant.termination), res.assistant.termination);
      assert.ok(sizes.length >= 2);
    });

    test(`growth, ${par} call(s) a step, ${label} results, a 32 000-token window: every estimate at most 25 600 tokens; an answer at the end`, async () => {
      const { res, sizes } = await turn(local, par, char);
      for (const [i, s] of sizes.entries()) assert.ok(s.tokens <= 25600 && s.tokens <= budgetOf(local).tokens, `request ${i + 1}: ${s.tokens} tokens`);
      assert.ok(res.assistant.content.trim(), "the turn ends with an answer");
    });
  }
}

test("a long turn keeps going while earlier results are compacted, and says how many were", async () => {
  const { res, sizes, calls } = await turn(relay, 4, "x");
  assert.equal(calls.filter((c) => c.body.tools).length, 16, "all 16 tool steps ran: compaction made the room");
  assert.equal(res.assistant.termination, "max_steps_synthesized");
  assert.ok(res.assistant.compacted > 0, `compacted ${res.assistant.compacted}`);
  // the 16th call (the last that could run tools) read the older results shortened and the newest step's whole
  const tools = calls.filter((c) => c.body.tools).at(-1).body.messages.filter((m) => m.role === "tool");
  assert.ok(tools.filter((m) => /\[shortened to fit the model's context/.test(m.content)).length >= res.assistant.compacted - 4);
  assert.deepEqual(tools.slice(-4).map((m) => m.content.length >= RESULT - 100), [true, true, true, true], "the newest step's results are whole");
  assert.ok(Math.max(...sizes.map((s) => s.bytes)) < 400_000, "999 KiB at 530031f");
});

test("under budget the requests are what they were: system, earlier turns, this turn's messages, digest for digest", async () => {
  // the text the model reads for two governed envelopes (fixtures/make_envelope_text_fixtures.py)
  const texts = Object.values(JSON.parse(readFileSync(new URL("./fixtures/envelope_texts.json", import.meta.url), "utf8")));
  const digest = (x) => createHash("sha256").update(JSON.stringify(x)).digest("hex");
  const earlier = [
    { id: "u0", role: "user", content: "甘草与甘遂能同用吗？" },
    { id: "a0", role: "assistant", provider: relay.id, wireFormat: "openai", content: "记载为十八反 [E1]。", citeTop: 4, wire: [
      { role: "assistant", content: "", tool_calls: [{ id: "p1", type: "function", function: { name: "literature_search", arguments: "{\"query\":\"甘草 甘遂\"}" } }] },
      { role: "tool", tool_call_id: "p1", content: texts[1] },
      { role: "assistant", content: "记载为十八反 [E1]。" },
    ] },
  ];
  for (const steps of [0, 1, 3]) {
    let n = 0;
    const f = fakeFetch(() => {
      n++;
      if (n > steps) return streamResponse([sse([{ choices: [{ index: 0, delta: { content: "答 [E5]。" }, finish_reason: "stop" }] }])]);
      return streamResponse([sse([{ choices: [{ index: 0, delta: { tool_calls: [{ index: 0, id: `c${n}`, type: "function", function: { name: "literature_search", arguments: JSON.stringify({ query: `q${n}` }) } }] }, finish_reason: "tool_calls" }] }])]);
    });
    const history = [...earlier, { id: "u1", role: "user", content: "那桂枝汤呢？" }];
    const r = { resolve: router("x").resolve, call: async (name, args) => ({ ...(await synthEnvelope({ tool: name, args, status: "succeeded" })), ok: true, status: "succeeded", text: texts[n % 2] }) };
    const res = await runTurn({ provider: { ...relay, fetch: f }, system, history, router: r, catalog, settings: {} });
    const base = buildWireHistory(history, relay);
    const wire = res.assistant.wire;
    // the request of step k: the turn's messages before its k-th assistant message, as the loop built it before budgets
    const before = (k) => wire.findIndex((m, i) => m.role === "assistant" && wire.slice(0, i + 1).filter((x) => x.role === "assistant").length === k + 1);
    f.calls.forEach((call, k) => {
      const expected = [{ role: "system", content: system }, ...base, ...wire.slice(0, before(k))];
      assert.equal(digest(call.body.messages), digest(expected), `${steps} step(s), request ${k + 1}`);
    });
    assert.equal(f.calls.length, steps + 1);
    assert.equal(res.assistant.compacted, undefined);
  }
});

// ------------------------------------------------------------------------------------------------- overflow

const overflow = () => new ProviderError("The model service returned 400: This model's maximum context length is 131072 tokens", { status: 400, type: "context_length_exceeded" });
const tooLarge = () => new ProviderError("对话太长，超过了 Tao-S1 的上限。", { status: 413, type: "too_large" });
const step = (k, args = { query: `q${k}` }) => openaiStep({ calls: [{ id: `c${k}`, name: "literature_search", args }] });

test("a request the model finds too long: exactly one compacted retry, and the turn goes on", async () => {
  for (const err of [overflow, tooLarge]) {
    const llm = scriptedLLM([step(1), step(2), err(), openaiStep({ text: "据已有结果作答 [E1]。" })]);
    const events = [];
    const sleeps = [];
    const res = await runTurn({ provider: relay, system, history: [{ id: "u", role: "user", content: "q" }], router: router("x"), catalog, settings: {}, llm: { openai: llm }, onEvent: (e) => events.push(e), sleep: async (ms) => sleeps.push(ms) });
    assert.equal(llm.calls.length, 4, "the refused request, then one retry");
    const [refused, retry] = [llm.calls[2], llm.calls[3]];
    assert.ok(JSON.stringify(retry.messages).length < JSON.stringify(refused.messages).length * 0.75, "the retry is shorter");
    assert.match(retry.messages.find((m) => m.tool_call_id === "c1").content, /\[shortened to fit the model's context/, "the older result went first");
    assert.equal(retry.messages.find((m) => m.tool_call_id === "c2").content, refused.messages.find((m) => m.tool_call_id === "c2").content, "the newest result is whole");
    assert.equal(retry.tools.length, catalog.core.length, "tools still offered: the shorter request fits");
    assert.deepEqual(sleeps, [], "no waiting: it is not a transient error");
    assert.deepEqual(events.filter((e) => e.type === "model.retry").map((e) => [e.step, e.delay_ms, e.notice]), [[3, 0, "请求超出模型的上下文，已缩短较早的工具结果，正在重试…"]]);
    assert.equal(res.status, "ok");
    assert.equal(res.assistant.termination, "answered");
    assert.equal(res.assistant.compacted, 1);
  }
});

test("too long again after the one retry: the error is final (no Retry), and nothing more is sent", async () => {
  for (const err of [overflow, tooLarge]) {
    const llm = scriptedLLM([step(1), step(2), err(), err(), openaiStep({ text: "never" })]);
    const res = await runTurn({ provider: relay, system, history: [{ id: "u", role: "user", content: "q" }], router: router("x"), catalog, settings: {}, llm: { openai: llm }, onEvent: () => {} });
    assert.equal(llm.calls.length, 4, "exactly one compacted retry");
    assert.equal(res.status, "error");
    assert.equal(res.assistant.error.permanent, true, "the page offers no Retry");
    assert.equal(res.assistant.termination, "error:model");
  }
  // nothing that could be shortened (no earlier results, nothing of this turn's): no retry at all
  const llm = scriptedLLM([overflow(), openaiStep({ text: "never" })]);
  const res = await runTurn({ provider: relay, system, history: [{ id: "u", role: "user", content: "q" }], router: router("x"), catalog, settings: {}, llm: { openai: llm }, onEvent: () => {} });
  assert.equal(llm.calls.length, 1, "the same request would only fail again");
  assert.equal(res.assistant.error.permanent, true);
});

test("the one retry gives up the tools when nothing earlier can be compacted: the newest result is answered from", async () => {
  for (const [par, shortened] of [[1, false], [2, true]]) {
    // the newest results: as they are when the tools' room is enough; shortened when even that is not
    const calls = Array.from({ length: par }, (_, j) => ({ id: j ? `c1_${j}` : "c1", name: "literature_search", args: { query: `q${j}` } }));
    const llm = scriptedLLM([openaiStep({ calls }), overflow(), openaiStep({ text: "据已有结果作答。" })]);
    const res = await runTurn({ provider: relay, system, history: [{ id: "u", role: "user", content: "q" }], router: router("中"), catalog, settings: {}, llm: { openai: llm }, onEvent: () => {} });
    assert.equal(llm.calls.length, 3);
    assert.deepEqual(llm.calls[2].tools, [], "no tools: the request asks for the answer");
    assert.match(llm.calls[2].messages.at(-1).content, /fill the model's context/);
    assert.equal(/\[shortened/.test(llm.calls[2].messages.find((m) => m.tool_call_id === "c1").content), shortened);
    assert.equal(res.assistant.termination, "context_synthesized");
    assert.equal(res.assistant.content, "据已有结果作答。");
  }
});

test("citations survive compaction: every [E#], claim verdict and refusal of a shortened result is still sent; an answer citing it resolves", async () => {
  const env = JSON.parse(readFileSync(new URL("./fixtures/envelope_claims_refused.json", import.meta.url), "utf8"));
  const texts = JSON.parse(readFileSync(new URL("./fixtures/envelope_texts.json", import.meta.url), "utf8"));
  let n = 0;
  // each call returns the refused-claims envelope with its model text (citations E1, E2), so each is numbered on
  const r = { resolve: router("x").resolve, call: async () => ({ ...structuredClone(env), text: texts.claims_refused, callNo: ++n }) };
  const small = { ...local, contextTokens: 30000, maxTokens: 2000 };
  const llm = scriptedLLM([step(1), step(2), step(3), step(4), openaiStep({ text: "据记载 [E1]，模型预测 [E2]；[E7] 为第 4 次检索。" })]);
  const res = await runTurn({ provider: small, system, history: [{ id: "u", role: "user", content: "葛根" }], router: r, catalog, settings: {}, llm: { openai: llm }, onEvent: () => {} });
  assert.ok(res.assistant.compacted >= 1, `compacted ${res.assistant.compacted}`);
  const sent = llm.calls.at(-1).messages.filter((m) => m.role === "tool");
  const shortened = sent.filter((m) => /\[shortened to fit the model's context/.test(m.content));
  assert.ok(shortened.length >= 1);
  for (const m of shortened) {
    const whole = res.assistant.wire.find((w) => w.role === "tool" && w.tool_call_id === m.tool_call_id).content;
    for (const id of new Set(whole.match(/\[E\d+\]/g))) assert.ok(m.content.includes(id), `${m.tool_call_id}: ${id}`);
    for (const line of whole.split("\n").filter((l) => /— (REFUSED|allowed)|^- [A-Z]{3}\d{3}: /.test(l))) assert.ok(m.content.includes(line), line);
  }
  // the stored turn keeps every result whole; the answer's citations resolve against them (as the page and the
  // telemetry resolve them: turnrecord.evidenceStats)
  assert.equal(res.toolMessages.length, 4);
  assert.deepEqual(res.toolMessages.map((m) => m.envelope.citations.map((c) => c.id)), [["E1", "E2"], ["E3", "E4"], ["E5", "E6"], ["E7", "E8"]]);
  const stats = evidenceStats(res.toolMessages, res.assistant.content);
  assert.equal(stats.citations.used, 3);
  assert.equal(stats.citations.unresolved, 0);
});
