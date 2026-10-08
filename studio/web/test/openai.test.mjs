import "./fixtures/setup.mjs";
import assert from "node:assert/strict";
import { test } from "node:test";
import { setLang } from "../js/core/i18n.js";
import { Accum, buildRequest, parseArguments, relayError, splitThink, streamChat, streamModes, stripThink } from "../js/core/llm/openai.js";
import { activeProvider } from "../js/core/providers.js";
import { bytePieces, fakeFetch, sse, streamResponse } from "./fixtures/fakes.mjs";

const chunk = (delta, extra = {}) => ({ id: "c1", model: "m", choices: [{ index: 0, delta, finish_reason: null }], ...extra });
const finish = (reason) => ({ id: "c1", choices: [{ index: 0, delta: {}, finish_reason: reason }] });

function provider(overrides = {}) {
  return {
    id: "deepseek", label: "DeepSeek", format: "openai", baseUrl: "https://api.deepseek.com/v1", model: "deepseek-chat",
    apiKey: "sk-test", max_tokens_param: "max_tokens", reasoning: "reasoning_content", stream_usage: true, maxTokens: 4096,
    temperature: null, route: "direct", tools: true, key_required: true, ...overrides,
  };
}

async function run(p, chunks, params = {}) {
  const f = fakeFetch(() => streamResponse(chunks));
  const events = [];
  for await (const ev of streamChat({ provider: { ...p, fetch: f }, system: "SYS", messages: [{ role: "user", content: "hi" }], ...params })) events.push(ev);
  return { events, fetch: f, text: events.filter((e) => e.type === "text").map((e) => e.delta).join(""), reasoning: events.filter((e) => e.type === "reasoning").map((e) => e.delta).join(""), done: events.find((e) => e.type === "done") };
}

test("Accum takes increments and cumulative text alike", () => {
  const a = new Accum();
  assert.equal(a.push("Hel"), "Hel");
  assert.equal(a.push("lo"), "lo");
  assert.equal(a.text, "Hello");
  const c = new Accum();
  assert.equal(c.push("桂枝汤出自"), "桂枝汤出自");
  assert.equal(c.push("桂枝汤出自《伤寒论》"), "《伤寒论》");
  assert.equal(c.push("桂枝汤出自《伤寒论》第十二条。"), "第十二条。");
  assert.equal(c.push("桂枝汤出自《伤寒论》第十二条。"), "", "a repeat adds nothing");
});

test("splitThink holds back what could still become a tag", () => {
  assert.deepEqual(splitThink("<thi"), { thinking: "", answer: "", open: false, pending: true });
  assert.equal(splitThink("<think>plan</th").thinking, "plan");
  assert.deepEqual(splitThink("<think>plan</think>\n\nanswer"), { thinking: "plan", answer: "answer", open: false, pending: false });
  assert.equal(splitThink("plain").answer, "plain");
  assert.equal(stripThink("<think>x</think>\n答案"), "答案");
  assert.equal(stripThink("<think>unclosed"), "");
});

test("incremental text, usage, and the replayable message", async () => {
  const { text, events, done } = await run(provider(), [sse([chunk({ role: "assistant", content: "" }), chunk({ content: "桂枝汤" }), chunk({ content: "出自《伤寒论》。" }), finish("stop"), { id: "c1", choices: [], usage: { prompt_tokens: 120, completion_tokens: 9 } }])]);
  assert.equal(text, "桂枝汤出自《伤寒论》。");
  assert.deepEqual(events.find((e) => e.type === "usage"), { type: "usage", input: 120, output: 9 });
  assert.equal(done.finish_reason, "stop");
  assert.deepEqual(done.wire, { role: "assistant", content: "桂枝汤出自《伤寒论》。" });
});

test("cumulative streams (the relay's upstream): content and reasoning_details", async () => {
  const p = provider({ id: "tao", relay: true, reasoning: "reasoning_details", model: "Tao-S1", apiKey: "", key_required: false, max_tokens_param: "max_completion_tokens", max_output: 8192 });
  const det = (text) => ({ reasoning_details: [{ type: "reasoning.text", id: "r1", format: "Tao-v1", index: 0, text }] });
  const { text, reasoning, done } = await run(p, [sse([
    chunk(det("需要先检索")), chunk(det("需要先检索桂枝汤的原文。")),
    chunk({ content: "我先检索" }), chunk({ content: "我先检索原文。" }), chunk({ content: "我先检索原文。" }),
    finish("stop"),
  ])]);
  assert.equal(reasoning, "需要先检索桂枝汤的原文。");
  assert.equal(text, "我先检索原文。");
  assert.deepEqual(done.wire.reasoning_details, [{ type: "reasoning.text", id: "r1", format: "Tao-v1", index: 0, text: "需要先检索桂枝汤的原文。" }]);
});

test("reasoning_content (DeepSeek) is reported as reasoning and kept for this turn's replay", async () => {
  const { text, reasoning, done } = await run(provider(), [sse([chunk({ reasoning_content: "思考" }), chunk({ reasoning_content: "一下" }), chunk({ content: "答" }), finish("stop")])]);
  assert.equal(reasoning, "思考一下");
  assert.equal(text, "答");
  assert.equal(done.wire.reasoning_content, "思考一下");
});

test("one reasoning source per reply: a duplicate `reasoning` field is not shown twice", async () => {
  const { reasoning } = await run(provider({ reasoning: null }), [sse([chunk({ reasoning_content: "A", reasoning: "A" }), chunk({ reasoning: "A" }), chunk({ content: "x" }), finish("stop")])]);
  assert.equal(reasoning, "A");
});

test("inline <think> split across chunks is reasoning, never answer text", async () => {
  const body = sse([chunk({ content: "<thi" }), chunk({ content: "nk>先查" }), chunk({ content: "原文</thi" }), chunk({ content: "nk>\n\n答案" }), chunk({ content: "在此" }), finish("stop")]);
  for (const n of [3, 1000]) {
    const { text, reasoning, done } = await run(provider({ reasoning: null }), bytePieces(body, n));
    assert.equal(reasoning, "先查原文");
    assert.equal(text, "答案在此");
    assert.equal(done.wire.content, "<think>先查原文</think>\n\n答案在此", "the wire keeps the content verbatim");
  }
});

test("a short reply that looked like the start of <think> is still shown", async () => {
  const { text } = await run(provider({ reasoning: null }), [sse([chunk({ content: "<t" }), finish("stop")])]);
  assert.equal(text, "<t");
});

test("tool calls are assembled by index; arguments split anywhere; names repeated or split", async () => {
  const tc = (index, fn, id) => chunk({ tool_calls: [{ index, ...(id ? { id, type: "function" } : {}), function: fn }] });
  const { events, done } = await run(provider(), [sse([
    chunk({ content: "我先查一下。" }),
    tc(0, { name: "tcm_", arguments: "" }, "call_a"), tc(0, { name: "herb", arguments: "{\"na" }), tc(0, { arguments: "me\": \"甘草\"}" }),
    tc(1, { name: "tcm_compatibility", arguments: "{\"herbs\": [\"甘草\"," }, "call_b"), tc(1, { name: "tcm_compatibility", arguments: " \"甘遂\"]}" }),
    finish("tool_calls"),
  ])]);
  const calls = events.filter((e) => e.type === "tool_call");
  assert.deepEqual(calls.map((c) => [c.id, c.name, c.arguments]), [
    ["call_a", "tcm_herb", { name: "甘草" }],
    ["call_b", "tcm_compatibility", { herbs: ["甘草", "甘遂"] }],
  ]);
  assert.ok(events.some((e) => e.type === "tool_call_delta" && e.name === "tcm_herb"));
  assert.equal(done.finish_reason, "tool_calls");
  assert.equal(done.wire.tool_calls.length, 2);
  assert.equal(done.wire.tool_calls[1].function.arguments, "{\"herbs\": [\"甘草\", \"甘遂\"]}");
});

test("arguments that are not a JSON object come back as an error for the model, and are not replayed", async () => {
  const { events, done } = await run(provider(), [sse([
    chunk({ tool_calls: [{ index: 0, id: "c1", function: { name: "tcm_herb", arguments: "{name: 甘草}" } }] }),
    chunk({ tool_calls: [{ index: 1, id: "c2", function: { name: "tcm_herb", arguments: "[1,2]" } }] }),
    chunk({ tool_calls: [{ index: 2, id: "c3", function: { name: "capabilities_status", arguments: "" } }] }),
    finish("tool_calls"),
  ])]);
  const calls = events.filter((e) => e.type === "tool_call");
  assert.match(calls[0].error, /JSON/);
  assert.equal(calls[0].raw, "{name: 甘草}");
  assert.ok(calls[1].error);
  assert.equal(calls[2].error, undefined);
  assert.deepEqual(calls[2].arguments, {});
  assert.equal(done.wire.tool_calls[0].function.arguments, "{}");
  assert.equal(parseArguments(" ").error, null);
});

test("a closing message that carries the whole reply (or tool calls) is used", async () => {
  const { text, events } = await run(provider(), [sse([
    chunk({ content: "部分" }),
    { id: "c1", choices: [{ index: 0, delta: {}, message: { role: "assistant", content: "部分以及全部", tool_calls: [{ id: "x", type: "function", function: { name: "tcm_herb", arguments: { name: "葛根" } } }] }, finish_reason: "tool_calls" }] },
  ])]);
  assert.equal(text, "部分以及全部");
  assert.deepEqual(events.find((e) => e.type === "tool_call").arguments, { name: "葛根" });
});

test("a JSON (non-stream) reply is read whole", async () => {
  const f = fakeFetch(() => new Response(JSON.stringify({ choices: [{ message: { role: "assistant", content: "整段回复", reasoning_content: "想" }, finish_reason: "stop" }], usage: { prompt_tokens: 5, completion_tokens: 3 } }), { headers: { "content-type": "application/json" } }));
  const evs = [];
  for await (const ev of streamChat({ provider: { ...provider(), fetch: f }, system: "", messages: [] })) evs.push(ev);
  assert.equal(evs.filter((e) => e.type === "text").map((e) => e.delta).join(""), "整段回复");
  assert.equal(evs.filter((e) => e.type === "reasoning").map((e) => e.delta).join(""), "想");
  assert.deepEqual(evs.find((e) => e.type === "usage"), { type: "usage", input: 5, output: 3 });
});

test("an error inside the stream is a model error, not text", async () => {
  await assert.rejects(run(provider(), [sse([chunk({ content: "a" }), { error: { message: "默认服务出错了", type: "upstream_error" } }])]), (err) => {
    assert.equal(err.name, "ProviderError");
    assert.match(err.message, /默认服务出错了/);
    assert.equal(err.retryable, true);
    return true;
  });
  await assert.rejects(run(provider(), [sse([{ base_resp: { status_code: 1004, status_msg: "auth" } }])]), /auth/);
});

test("HTTP errors: status, hint, retryable, Retry-After; the relay's own words are kept", async () => {
  const f429 = fakeFetch(() => new Response(JSON.stringify({ error: { message: "请求太频繁，请稍后再试。", type: "rate_limited" } }), { status: 429, headers: { "retry-after": "12" } }));
  const relay = { ...provider(), relay: true, apiKey: "", key_required: false, fetch: f429 };
  await assert.rejects(async () => { for await (const _ of streamChat({ provider: relay, messages: [] })); }, (err) => {
    assert.equal(err.message, "请求太频繁，请稍后再试。");
    assert.equal(err.retryable, true);
    assert.equal(err.retryAfter, 12);
    return true;
  });
  const fDaily = fakeFetch(() => new Response(JSON.stringify({ error: { message: "今日额度已用完", type: "daily_limit" } }), { status: 429 }));
  await assert.rejects(async () => { for await (const _ of streamChat({ provider: { ...relay, fetch: fDaily }, messages: [] })); }, (err) => err.retryable === false);
  const f401 = fakeFetch(() => new Response(JSON.stringify({ error: { message: "Invalid key" } }), { status: 401 }));
  await assert.rejects(async () => { for await (const _ of streamChat({ provider: { ...provider(), fetch: f401 }, messages: [] })); }, (err) => {
    assert.match(err.message, /401/);
    assert.match(err.message, /Invalid key/);
    assert.equal(err.retryable, false);
    return true;
  });
});

test("network failures say what to do, per route, and only remote ones are retried", async () => {
  const down = fakeFetch(() => { throw new TypeError("Failed to fetch"); });
  const tryIt = async (p) => { try { for await (const _ of streamChat({ provider: { ...p, fetch: down }, messages: [] })); } catch (e) { return e; } return null; };
  const relayErr = await tryIt({ ...provider(), relay: true, apiKey: "", key_required: false });
  assert.match(relayErr.message, /Tao-S1/);
  assert.equal(relayErr.retryable, true);
  assert.equal(relayErr.kind, "network");
  const ollama = await tryIt({ ...provider(), id: "ollama", label: "Ollama", local: true, baseUrl: "http://127.0.0.1:11434/v1", apiKey: "", key_required: false, fix: "core.local_model.cors.ollama" });
  assert.match(ollama.message, /OLLAMA_ORIGINS/);
  assert.equal(ollama.retryable, false);
  const viaRunner = await tryIt({ ...provider(), route: "runner", runner: { url: "http://127.0.0.1:8765", token: "t" } });
  assert.match(viaRunner.message, /127\.0\.0\.1:8765/);
  const remote = await tryIt(provider());
  assert.match(remote.message, /api\.deepseek\.com/);
  assert.equal(remote.retryable, true);
});

test("a missing key is reported before any request", async () => {
  const f = fakeFetch(() => streamResponse([]));
  await assert.rejects(async () => { for await (const _ of streamChat({ provider: { ...provider(), apiKey: "", fetch: f }, messages: [] })); }, /API Key/);
  assert.equal(f.calls.length, 0);
});

test("request body: model first, max tokens param, usage, tools, thinking off for the relay", () => {
  const relay = activeProvider({ provider: "tao", maxTokens: 16000 }, {});
  const req = buildRequest({ provider: relay, system: "SYS", messages: [{ role: "user", content: "你是谁" }], tools: [{ name: "tcm_herb", description: "d", parameters: { type: "object", properties: {} } }], thinking: false });
  const json = JSON.stringify(req.body);
  assert.ok(json.startsWith('{"model":"Tao-S1",'), json.slice(0, 40));
  assert.equal(req.body.max_completion_tokens, 8192, "capped at the relay's output limit");
  assert.deepEqual(req.body.stream_options, { include_usage: true });
  assert.deepEqual(req.body.thinking, { type: "disabled" });
  assert.equal(req.body.tool_choice, "auto");
  assert.equal(req.body.tools[0].type, "function");
  assert.equal(req.body.messages[0].role, "system");
  assert.equal(req.headers.Authorization, undefined, "no key is ever sent to the relay");
  const none = buildRequest({ provider: relay, system: "", messages: [], tools: [{ name: "a", parameters: {} }], toolChoice: "none" });
  assert.equal(none.body.tool_choice, "none");
  const keep = buildRequest({ provider: relay, system: "", messages: [], thinking: true });
  assert.equal(keep.body.thinking, undefined);
});

test("through the runner proxy: POST {runner}/api/llm with the target and token headers; the key stays in Authorization", () => {
  const p = { ...provider(), route: "runner", runner: { url: "http://127.0.0.1:8765/", token: "tok" } };
  const req = buildRequest({ provider: p, system: "", messages: [] });
  assert.equal(req.url, "http://127.0.0.1:8765/api/llm");
  assert.equal(req.headers["X-TCM-Target"], "https://api.deepseek.com/v1/chat/completions");
  assert.equal(req.headers["X-TCM-Token"], "tok");
  assert.equal(req.headers.Authorization, "Bearer sk-test");
});

test("abort mid-stream ends with an AbortError and cancels the body", async () => {
  const ctl = new AbortController();
  let cancelled = false;
  const f = fakeFetch(() => streamResponse([sse([chunk({ content: "一" })], { done: false }), sse([chunk({ content: "二" })], { done: false }), sse([chunk({ content: "三" })])], { onCancel: () => { cancelled = true; } }));
  const seen = [];
  await assert.rejects(async () => {
    for await (const ev of streamChat({ provider: { ...provider(), fetch: f }, messages: [], signal: ctl.signal })) {
      if (ev.type === "text") { seen.push(ev.delta); ctl.abort(); }
    }
  }, { name: "AbortError" });
  assert.deepEqual(seen, ["一"]);
  assert.equal(cancelled, true);
});

test("an invalid endpoint is refused before fetch", () => {
  assert.throws(() => buildRequest({ provider: { ...provider(), baseUrl: "" }, messages: [] }), /无效|valid/);
});

// ------------------------------------------------------------------------------------------------- review fixes

test("Accum: a declared mode is followed, nothing guessed; 'auto' no longer doubles a whole reply that is repeated", () => {
  // cumulative: the whole text, then the same again (the closing chunk), then more
  const c = new Accum("cumulative");
  assert.equal(c.push("好的，已完成。"), "好的，已完成。");
  assert.equal(c.push("好的，已完成。"), "");
  assert.equal(c.push("好的"), "", "less of the text so far adds nothing");
  assert.equal(c.push("好的，已完成。还有"), "还有");
  assert.equal(c.text, "好的，已完成。还有");
  // delta: an increment that happens to start with the text so far is still an increment
  const d = new Accum("delta");
  for (const piece of ["1", "1.", " 桂枝"]) d.push(piece);
  assert.equal(d.text, "11. 桂枝");
  const bold = new Accum("delta");
  for (const piece of ["**", "**注意**"]) bold.push(piece);
  assert.equal(bold.text, "****注意**");
  // auto: a whole short reply repeated before it ever grew, and a cumulative stream that stalls once
  const a = new Accum();
  a.push("好的，已完成。");
  assert.equal(a.push("好的，已完成。"), "");
  assert.equal(a.text, "好的，已完成。");
  const stall = new Accum();
  for (const piece of ["我先检索", "我先检索", "我先检索原文。"]) stall.push(piece);
  assert.equal(stall.text, "我先检索原文。");
  assert.deepEqual(streamModes({ stream: "delta" }), { content: "delta", reasoning: "delta" });
  assert.deepEqual(streamModes({ stream: { content: "auto", reasoning: "cumulative" } }), { content: "auto", reasoning: "cumulative" });
  assert.deepEqual(streamModes({ stream: "nonsense" }), { content: "auto", reasoning: "auto" });
  assert.deepEqual(streamModes({}), { content: "auto", reasoning: "auto" });
});

test("the relay: reasoning_details sent whole and then repeated are shown, and replayed, once", async () => {
  const p = { ...activeProvider({ provider: "tao" }), baseUrl: "https://science.impf.ai/v1" };
  const det = (text) => [{ type: "reasoning.text", id: "r1", format: "Tao-v1", index: 0, text }];
  const r = await run(p, [sse([chunk({ reasoning_details: det("想一想"), content: "" }), chunk({ reasoning_details: det("想一想"), content: "好" }), chunk({ content: "的" }), finish("stop")])]);
  assert.equal(r.reasoning, "想一想");
  assert.equal(r.text, "好的");
  assert.equal(r.done.wire.reasoning_details[0].text, "想一想");
  assert.equal(r.done.wire.reasoning_details[0].format, "Tao-v1");
});

test("a provider known to stream increments: '1' then '1.' is '11.', not a cumulative '1.'", async () => {
  const p = { ...activeProvider({ provider: "deepseek", keys: { deepseek: "sk" } }) };
  assert.equal(p.stream, "delta");
  const r = await run(p, [sse([chunk({ content: "1" }), chunk({ content: "1." }), chunk({ content: " 桂枝" }), finish("stop")])]);
  assert.equal(r.text, "11. 桂枝");
  const z = await run(p, [sse([chunk({ reasoning_content: "先" }), chunk({ reasoning_content: "先查" }), chunk({ content: "答" }), finish("stop")])]);
  assert.equal(z.reasoning, "先先查");
});

test("parallel tool calls streamed whole without an index stay separate calls", async () => {
  const tc = (fields) => chunk({ tool_calls: [fields] });
  const r = await run(provider(), [sse([
    tc({ id: "a", type: "function", function: { name: "tcm_herb", arguments: "{\"name\":\"甘草\"}" } }),
    tc({ id: "b", type: "function", function: { name: "tcm_formula", arguments: "{\"name\":\"桂枝汤\"}" } }),
    finish("tool_calls")])]);
  const calls = r.events.filter((e) => e.type === "tool_call");
  assert.deepEqual(calls.map((c) => [c.id, c.name, c.arguments, c.error]), [["a", "tcm_herb", { name: "甘草" }, undefined], ["b", "tcm_formula", { name: "桂枝汤" }, undefined]]);
  assert.deepEqual(r.done.wire.tool_calls.map((c) => c.id), ["a", "b"]);
  // the same id on every delta is one call; a delta with neither index nor id continues the call being streamed
  const r2 = await run(provider(), [sse([
    tc({ id: "x", function: { name: "tcm_herb", arguments: "{\"name\":" } }),
    tc({ id: "x", function: { arguments: "\"葛根\"" } }),
    tc({ function: { arguments: "}" } }),
    finish("tool_calls")])]);
  const c2 = r2.events.filter((e) => e.type === "tool_call");
  assert.deepEqual(c2.map((c) => [c.id, c.name, c.arguments]), [["x", "tcm_herb", { name: "葛根" }]]);
  // services that send an index keep working as before
  const r3 = await run(provider(), [sse([
    chunk({ tool_calls: [{ index: 0, id: "p", function: { name: "tcm_herb", arguments: "" } }, { index: 1, id: "q", function: { name: "tcm_formula", arguments: "" } }] }),
    chunk({ tool_calls: [{ index: 1, function: { arguments: "{}" } }, { index: 0, function: { arguments: "{}" } }] }),
    finish("tool_calls")])]);
  assert.deepEqual(r3.events.filter((e) => e.type === "tool_call").map((c) => [c.id, c.name]), [["p", "tcm_herb"], ["q", "tcm_formula"]]);
});

test("relay errors are told in the page's language, by type; the Chinese page keeps the relay's own words", async () => {
  const daily = { error: { message: "今天的 Tao-S1 免费额度已用完（每人每天 1200 次模型调用）", type: "daily_limit" } };
  const relay = { ...provider(), id: "tao", relay: true, apiKey: "", key_required: false };
  const fail = (body, status, headers = {}) => ({ ...relay, fetch: fakeFetch(() => new Response(JSON.stringify(body), { status, headers })) });
  const errOf = async (p) => { try { for await (const _ of streamChat({ provider: p, messages: [] })); } catch (err) { return err; } return null; };
  setLang("en");
  try {
    const e1 = await errOf(fail(daily, 429, { "retry-after": "3600" }));
    assert.match(e1.message, /^Your free Tao-S1 quota for today is used up\. It resets every day at 00:00 UTC/);
    assert.doesNotMatch(e1.message, /[\u3400-\u9fff]/);
    assert.equal(e1.permanent, true, "no Retry for a daily quota");
    assert.equal(e1.retryable, false);
    const e2 = await errOf(fail({ error: { message: "请求太频繁，请 10 秒后再试", type: "rate_limited" } }, 429, { "retry-after": "10" }));
    assert.equal(e2.message, "Too many requests to Tao-S1. Try again in 10 s.");
    assert.equal(e2.retryAfter, 10);
    assert.equal(e2.permanent, false);
    const e3 = await errOf(fail({ error: { message: "新的中继错误", type: "something_new" } }, 500));
    assert.equal(e3.message, "新的中继错误", "an unknown type keeps the relay's words");
    assert.equal(e3.lang, "zh", "marked as Chinese on an English page");
    const e4 = await errOf({ ...relay, fetch: fakeFetch(() => streamResponse([sse([{ error: { message: "Tao-S1 出错了", type: "upstream_error" } }])])) });
    assert.match(e4.message, /^Tao-S1's model service reported a problem/);
    assert.equal(e4.retryable, true);
    for (const type of ["https_required", "forbidden_origin", "not_configured", "bad_request", "too_large", "model_not_allowed", "total_limit", "unavailable", "upstream_unreachable", "upstream_auth", "upstream_rate", "upstream_quota", "upstream_rejected", "relay_error", "not_relay", "not_found", "method_not_allowed"]) {
      const shown = relayError(type, "中文", {}, "en");
      assert.doesNotMatch(shown.text, /[\u3400-\u9fff]|core\.relay/, type);
      assert.doesNotMatch(shown.text, /minimax|abab|hailuo/i, type);
    }
    assert.equal(relayError("rate_limited", "", {}, "en").text, "Too many requests to Tao-S1. Try again in a moment.");
  } finally {
    setLang("zh");
  }
  const z = await errOf(fail(daily, 429));
  assert.equal(z.message, daily.error.message, "the relay's Chinese, with its numbers, on a Chinese page");
  assert.equal(z.lang, undefined);
  assert.equal(relayError("daily_limit", "", {}, "zh").text.startsWith("今天的 Tao-S1 免费额度已用完"), true, "without a message: the page's own sentence");
});
