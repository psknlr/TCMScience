// A model stream that stalls, end to end on a fake clock: the real agent loop (runTurn) and the real provider clients,
// over a fetch whose body sends some chunks and then nothing.
import "./fixtures/setup.mjs";
import assert from "node:assert/strict";
import { test } from "node:test";
import { runTurn } from "../js/core/agent.js";
import { streamChat as anthropicStream } from "../js/core/llm/anthropic.js";
import { anthropicSse, fakeFetch, sse } from "./fixtures/fakes.mjs";

const enc = new TextEncoder();

/** A streamed Response that sends `chunks` ({text, after?: ms on the clock}) and then, unless `close`, nothing ever. */
function stallingResponse(chunks, { close = false } = {}) {
  let i = 0;
  const body = new ReadableStream({
    async pull(controller) {
      if (i >= chunks.length) {
        if (close) { controller.close(); return undefined; }
        return new Promise(() => {}); // the connection stays open and silent
      }
      const c = chunks[i++];
      if (c.after) await new Promise((r) => setTimeout(r, c.after));
      controller.enqueue(enc.encode(c.text));
      return undefined;
    },
  });
  return new Response(body, { status: 200, headers: { "content-type": "text/event-stream" } });
}

const chunk = (delta, extra = {}) => ({ id: "c1", model: "deepseek-chat", choices: [{ index: 0, delta, finish_reason: null }], ...extra });
const provider = (fetch, over = {}) => ({
  id: "deepseek", label: "DeepSeek", format: "openai", baseUrl: "https://api.deepseek.com/v1", model: "deepseek-chat", apiKey: "sk-test",
  key_required: true, route: "direct", tools: true, reasoning: "reasoning_content", stream: "delta", stream_usage: true, fetch, ...over,
});
const user = (content) => ({ id: "u1", role: "user", content, createdAt: 1 });
const flush = async (n = 20) => { for (let k = 0; k < n; k++) await new Promise((r) => setImmediate(r)); };

/** Start a turn (not awaited); `result` fills in when it ends. */
function startTurn(prov, { signal } = {}) {
  const events = [];
  const state = { events, result: null };
  state.done = runTurn({ provider: prov, system: "SYS", history: [user("葛根芩连汤的组成？")], tools: [], settings: {}, signal, onEvent: (e) => events.push(e) })
    .then((r) => { state.result = r; return r; });
  return state;
}

test("a stream that sends one chunk and then hangs is retried at 90 s; when the retry stalls too, the turn ends in a network error", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const f = fakeFetch(() => stallingResponse([{ text: sse([chunk({ content: "葛根" })], { done: false }) }]));
  const turn = startTurn(provider(f));
  await flush();
  assert.equal(f.calls.length, 1);
  t.mock.timers.tick(89_999);
  await flush();
  assert.equal(turn.events.filter((e) => e.type === "model.retry").length, 0, "nothing before 90 s");
  t.mock.timers.tick(1);
  await flush();
  const retries = turn.events.filter((e) => e.type === "model.retry");
  assert.equal(retries.length, 1, "model.retry at 90 s");
  assert.equal(retries[0].attempt, 1);
  assert.equal(retries[0].delay_ms, 1500);
  // the retry waits its delay, then streams one chunk and stalls again
  t.mock.timers.tick(1500);
  await flush();
  assert.equal(f.calls.length, 2);
  t.mock.timers.tick(90_000);
  await flush();
  assert.ok(turn.result, "the turn has ended");
  assert.equal(turn.result.status, "error");
  assert.equal(turn.result.assistant.error.kind, "network");
  assert.equal(turn.result.assistant.error.type, "stream_stalled");
  assert.equal(turn.result.assistant.error.permanent, undefined, "the page keeps its Retry");
  assert.equal(f.calls.length, 2, "a stall is retried once");
  assert.equal(turn.events.filter((e) => e.type === "model.retry").length, 1);
  assert.ok(turn.events.some((e) => e.type === "error" && e.kind === "network"));
});

test("Stop during a stall ends the turn 'stopped' at once, without the clock moving", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const ctl = new AbortController();
  const f = fakeFetch(() => stallingResponse([{ text: sse([chunk({ content: "葛根" })], { done: false }) }]));
  const turn = startTurn(provider(f), { signal: ctl.signal });
  await flush();
  ctl.abort();
  await flush(3);
  assert.ok(turn.result, "ended without a clock tick");
  assert.equal(turn.result.status, "stopped");
  assert.equal(turn.result.assistant.content, "葛根", "what was said is kept");
  assert.equal(turn.events.filter((e) => e.type === "model.retry").length, 0);
});

test("a stream that pauses 60 s between reasoning chunks is not cut", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const f = fakeFetch(() => stallingResponse([
    { text: sse([chunk({ reasoning_content: "先看组成，" })], { done: false }) },
    { after: 60_000, text: sse([chunk({ reasoning_content: "再看出处，" })], { done: false }) },
    { after: 60_000, text: sse([chunk({ reasoning_content: "然后作答。" })], { done: false }) },
    { after: 60_000, text: sse([chunk({ content: "葛根、黄芩、黄连、炙甘草。" }), { id: "c1", choices: [{ index: 0, delta: {}, finish_reason: "stop" }] }]) },
  ], { close: true }));
  const turn = startTurn(provider(f));
  for (let k = 0; k < 3; k++) { await flush(); t.mock.timers.tick(60_000); }
  await flush();
  assert.ok(turn.result, "the turn has ended");
  assert.equal(turn.result.status, "ok");
  assert.equal(turn.result.assistant.reasoning, "先看组成，再看出处，然后作答。");
  assert.equal(turn.result.assistant.content, "葛根、黄芩、黄连、炙甘草。");
  assert.equal(turn.events.filter((e) => e.type === "model.retry").length, 0);
  assert.equal(f.calls.length, 1);
});

test("the provider's own idleMs overrides the default; the Anthropic client stalls the same way", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const f = fakeFetch(() => stallingResponse([{ text: anthropicSse([{ type: "message_start", message: { model: "claude-opus-5-5", usage: { input_tokens: 10 } } }]) }]));
  const prov = { id: "anthropic", label: "Anthropic Claude", format: "anthropic", baseUrl: "https://api.anthropic.com/v1", model: "claude-opus-5-5", apiKey: "sk-ant-test", key_required: true, route: "direct", idleMs: 200_000, fetch: f };
  let err = null;
  (async () => { try { for await (const _ of anthropicStream({ provider: prov, system: "SYS", messages: [{ role: "user", content: "hi" }] })); } catch (e) { err = e; } })();
  await flush();
  t.mock.timers.tick(90_000);
  await flush();
  assert.equal(err, null, "its own 200 s, not 90 s");
  t.mock.timers.tick(110_000);
  await flush();
  assert.equal(err?.type, "stream_stalled");
  assert.equal(err.kind, "network");
  assert.equal(err.retryable, true);
});
