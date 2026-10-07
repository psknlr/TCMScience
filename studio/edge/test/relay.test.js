// The relay under Node: request handling, renaming, filtering, error mapping, limits (node --test).
import assert from "node:assert/strict";
import test from "node:test";

import { config, handle, ipKey, publicText, withoutReasoning } from "../src/relay.js";
import { KEY, NOON, PAGE, ask, cut, events, jsonReply, post, quiet, relay, sse, tidy } from "./helpers.js";

// ------------------------------------------------------------------ the call itself
test("the key is added by the relay and never shown; output capped, n dropped, reasoning_split added, signal passed", async () => {
  const r = relay();
  const res = await r.call("/v1/chat/completions", post(ask));
  assert.equal(res.status, 200);
  assert.equal(res.headers.get("Content-Type"), "text/event-stream");
  assert.equal(res.headers.get("Cache-Control"), "no-store");
  assert.equal(res.headers.get("Access-Control-Allow-Origin"), PAGE);
  assert.match(await res.text(), /好/);
  const [c] = r.calls;
  assert.equal(c.url, "https://api.minimax.cn/v1/chat/completions");
  assert.equal(c.init.method, "POST");
  assert.equal(c.init.headers.Authorization, `Bearer ${KEY}`);
  assert.equal(c.init.headers.Accept, "text/event-stream");
  assert.deepEqual([c.body.model, c.body.max_completion_tokens, c.body.n, c.body.reasoning_split], ["MiniMax-M3", 8192, undefined, true]);
  assert.ok(c.init.signal instanceof AbortSignal);
  for (const [, v] of res.headers) assert.ok(!v.includes(KEY));
  // a key the page sends (it should send none) is never what goes upstream
  const own = relay();
  await (await own.call("/v1/chat/completions", post(tidy, { Authorization: "Bearer visitor-key" }))).text();
  assert.equal(own.calls[0].init.headers.Authorization, `Bearer ${KEY}`);
  // max_tokens is capped the same way; a nonsense value becomes the cap; no model at all means Tao-S1
  const mt = relay();
  await mt.call("/v1/chat/completions", post({ messages: tidy.messages, max_tokens: "lots" }));
  assert.deepEqual([mt.calls[0].body.model, mt.calls[0].body.max_tokens, mt.calls[0].body.max_completion_tokens], ["MiniMax-M3", 8192, undefined]);
  const small = relay({ env: { MAX_OUTPUT_TOKENS: "100" } });
  await small.call("/v1/chat/completions", post({ ...tidy, max_completion_tokens: 50 }));
  await small.call("/v1/chat/completions", post({ ...tidy, max_completion_tokens: 500 }));
  assert.deepEqual(small.calls.map((x) => x.body.max_completion_tokens), [50, 100]);
});

test("a request the relay need not reshape is edited as text: model and formats only, never what a message says", async () => {
  const r = relay();
  const said = '{"model":"Tao-S1","format":"Tao-x"} 与 "format":"Tao-y"';
  const history = [{ role: "user", content: said },
    { role: "assistant", content: "答", reasoning_details: [{ type: "reasoning.text", id: "r1", format: "Tao-response-v1", index: 0, text: "想" }] },
    { role: "user", content: "再问" }];
  const raw = JSON.stringify({ model: "Tao-S1", messages: history, stream: true, max_completion_tokens: 100, tools: [{ type: "function", function: { name: "tcm_herb", parameters: { type: "object", properties: { model: { type: "string" } } } } }] });
  await (await r.call("/v1/chat/completions", post(raw))).text();
  const sent = r.calls[0].init.body;
  assert.ok(sent.startsWith('{"model":"MiniMax-M3","reasoning_split":true,"messages":'), sent.slice(0, 80));
  assert.equal(sent, raw.replace('{"model":"Tao-S1",', '{"model":"MiniMax-M3","reasoning_split":true,').replace('"format":"Tao-response-v1"', '"format":"MiniMax-response-v1"'));
  assert.equal(r.calls[0].body.messages[0].content, said);
  assert.deepEqual(r.calls[0].body.tools[0].function.parameters.properties, { model: { type: "string" } });
  // the slow path (here: n removed) restores the formats on the parsed messages too
  const slow = relay();
  await slow.call("/v1/chat/completions", post({ model: "Tao-S1", messages: history, n: 1, max_completion_tokens: 100 }));
  assert.equal(slow.calls[0].body.messages[1].reasoning_details[0].format, "MiniMax-response-v1");
  assert.equal(slow.calls[0].body.messages[0].content, said);
  // a field the page set itself is not overwritten by UPSTREAM_FIELDS
  const own = relay();
  await own.call("/v1/chat/completions", post({ model: "Tao-S1", messages: history, reasoning_split: false, max_completion_tokens: 100 }));
  assert.equal(own.calls[0].body.reasoning_split, false);
});

test("a visitor sees none of the upstream: model, reasoning formats and own fields renamed or dropped, however the stream is cut", async () => {
  const stream = [
    '{"id":"a1","choices":[{"index":0,"delta":{"role":"assistant"}}],"created":1,"model":"MiniMax-M3","object":"chat.completion.chunk","usage":null}',
    '{"id":"a1","choices":[{"index":0,"delta":{"reasoning_details":[{"type":"reasoning.text","id":"r1","format":"MiniMax-response-v1","index":0,"text":"想"}]}}],"model":"MiniMax-M3"}',
    '{"id":"a1","choices":[{"index":0,"delta":{"content":"答：\\"model\\":\\"MiniMax-M3\\" 是原文"}}],"model":"MiniMax-M3","input_sensitive":false,"output_sensitive":false}',
    '{"id":"a1","choices":[{"finish_reason":"stop","index":0,"delta":{},"output_sensitive_int":0}],"model":"MiniMax-M3","base_resp":{"status_code":0,"status_msg":""},"usage":{"total_characters":0,"prompt_tokens":3,"completion_tokens":2}}',
    "[DONE]",
  ];
  const whole = stream.map((e) => `data: ${e}\n\n`).join("");
  for (const size of [1, 5, 64, 100000]) {
    const r = relay({ upstream: () => cut(whole, size) });
    const text = await (await r.call("/v1/chat/completions", post(tidy))).text();
    const visible = text.replace(/\\"model\\":\\"MiniMax-M3\\"/g, ""); // the answer's own words are not rewritten
    assert.doesNotMatch(visible, /MiniMax|minimax|base_resp|_sensitive|total_characters/, `size ${size}: ${text}`);
    assert.match(text, /答：\\"model\\":\\"MiniMax-M3\\" 是原文/);
    const all = events(text);
    assert.equal(all.length, 4);
    assert.ok(all.every((e) => e.model === "Tao-S1"));
    assert.equal(all[1].choices[0].delta.reasoning_details[0].format, "Tao-response-v1");
    assert.deepEqual(all[3].usage, { prompt_tokens: 3, completion_tokens: 2 });
    assert.match(text, /data: \[DONE\]/);
  }
  // CRLF lines and a comment line pass, the upstream's fields in the last event still go
  const crlf = relay({ upstream: () => new Response(`: ping\r\n\r\n${stream.slice(0, 2).map((e) => `data: ${e}\r\n\r\n`).join("")}`
    + 'data: {"id":"a1","choices":[],"model":"MiniMax-M3","usage":{"total_characters":0,"prompt_tokens":3},"base_resp":{"status_code":0}}\r\n\r\ndata: [DONE]\r\n\r\n',
  { status: 200, headers: { "Content-Type": "text/event-stream" } }) });
  const c2 = await (await crlf.call("/v1/chat/completions", post(tidy))).text();
  assert.doesNotMatch(c2, /MiniMax|base_resp|total_characters/, c2);
  assert.equal(events(c2.replace(/\r/g, "")).length, 3);
  assert.match(c2, /^: ping/);
  // a plain JSON reply too
  const plain = relay({ upstream: async () => jsonReply({ id: "c", model: "MiniMax-M3", choices: [{ message: { content: "好", reasoning_details: [{ format: "MiniMax-response-v1", text: "想" }] } }],
    base_resp: { status_code: 0, status_msg: "" }, input_sensitive: false, usage: { prompt_tokens: 1, completion_tokens: 1, total_characters: 0 } }) });
  const pj = await (await plain.call("/v1/chat/completions", post({ ...tidy, stream: false }))).json();
  assert.deepEqual(pj, { id: "c", model: "Tao-S1", choices: [{ message: { content: "好", reasoning_details: [{ format: "Tao-response-v1", text: "想" }] } }], usage: { prompt_tokens: 1, completion_tokens: 1 } });
  // only the configured upstream's formats are renamed; another passes as it is
  assert.match(publicText('{"format":"Other-v1","model":"x"}', config({})), /"format":"Other-v1","model":"Tao-S1"/);
  assert.match(publicText('{"format":"MiniMax-response-v1"}', config({ FORMAT_PREFIX: "Other-" })), /"format":"MiniMax-response-v1"/);
});

test("asked for no thinking, no reasoning reaches the browser, however the stream is cut", async () => {
  const stream = [
    '{"choices":[{"index":0,"delta":{"reasoning_details":[{"type":"reasoning.text","text":"底层是某模型"}],"reasoning_content":"想"}}]}',
    '{"choices":[{"index":0,"delta":{"content":"<think>内心独白</think>我是 Tao-S1"}}]}',
    '{"choices":[{"index":0,"delta":{"content":"，TCMScience Studio 的研究助手"}}]}',
    "[DONE]",
  ];
  for (const sep of ["\n\n", "\r\n\r\n"]) {
    const whole = stream.map((e) => `data: ${e}${sep}`).join("");
    for (const size of [1, 3, 7, 1000]) {
      const r = relay({ upstream: () => cut(whole, size) });
      const text = await (await r.call("/v1/chat/completions", post({ ...tidy, thinking: { type: "disabled" } }))).text();
      assert.ok(!/reasoning|底层是某模型|内心独白|think>/.test(text), `size ${size}: ${text}`);
      assert.match(text, /我是 Tao-S1/);
      assert.match(text, /研究助手/);
      assert.match(text, /data: \[DONE\]/);
    }
  }
  const plain = relay({ upstream: () => cut(stream.map((e) => `data: ${e}\n\n`).join("")) });
  assert.match(await (await plain.call("/v1/chat/completions", post(tidy))).text(), /reasoning_details/); // otherwise it passes
  const json = relay({ upstream: async () => jsonReply({ choices: [{ message: { content: "<think>想</think>答", reasoning_content: "想" } }] }) });
  const j = await (await json.call("/v1/chat/completions", post({ ...tidy, stream: false, thinking: { type: "disabled" } }))).json();
  assert.deepEqual(j, { choices: [{ message: { content: "答" } }] });
  // a <think> block streamed in pieces, or cumulatively: none of it passes
  const read = (chunks) => {
    const open = new Map();
    return chunks.map((c) => withoutReasoning({ choices: [{ delta: { content: c } }] }, open).choices[0].delta.content).join("|");
  };
  assert.equal(read(["<think>一", "二", "三</think>答", "案"]), "||答|案");
  assert.equal(read(["<think>一", "<think>一二", "<think>一二</think>答", "<think>一二</think>答案"]), "||答|答案");
});

// ------------------------------------------------------------------ who may call, and what
test("only the allowed origins; preflight; health and models answer anyone", async () => {
  const r = relay();
  const evil = await r.call("/v1/chat/completions", post(ask, { Origin: "https://evil.example" }));
  assert.equal(evil.status, 403);
  assert.equal((await evil.json()).error.type, "forbidden_origin");
  assert.equal(evil.headers.get("Access-Control-Allow-Origin"), null);
  const none = post(ask);
  delete none.headers.Origin; // a script, or a page with Referrer-Policy: no-referrer, sends none
  assert.equal((await r.call("/v1/chat/completions", none)).status, 403);
  assert.equal((await r.call("/v1/chat/completions", post(ask, { Origin: "null" }))).status, 403);
  assert.equal((await r.call("/v1/chat/completions", post(ask, { Origin: "https://science.impf.ai.evil.example" }))).status, 403);
  assert.equal(r.calls.length, 0);
  assert.equal(r.gates.length, 0); // refused before anything is counted
  // the app as the local runner serves it may call, and may read Retry-After
  for (const origin of ["http://127.0.0.1:8765", "http://localhost:8765"]) {
    const res = await r.call("/v1/chat/completions", post(tidy, { Origin: origin }));
    assert.equal(res.status, 200);
    assert.equal(res.headers.get("Access-Control-Allow-Origin"), origin);
    assert.match(res.headers.get("Access-Control-Expose-Headers"), /Retry-After/);
    assert.match(res.headers.get("Vary"), /Origin/);
    await res.text();
  }
  const pre = await r.call("/v1/chat/completions", { method: "OPTIONS", headers: { Origin: "http://127.0.0.1:8765", "Access-Control-Request-Method": "POST" } });
  assert.equal(pre.status, 204);
  assert.equal(pre.headers.get("Access-Control-Allow-Origin"), "http://127.0.0.1:8765");
  assert.match(pre.headers.get("Access-Control-Allow-Methods"), /POST/);
  assert.match(pre.headers.get("Access-Control-Allow-Headers"), /Content-Type/);
  const badPre = await r.call("/v1/chat/completions", { method: "OPTIONS", headers: { Origin: "http://localhost:5173" } });
  assert.equal(badPre.status, 403);
  const dev = relay({ env: { ALLOWED_ORIGINS: `${PAGE}, http://localhost:5173` } });
  assert.equal((await dev.call("/v1/chat/completions", { method: "OPTIONS", headers: { Origin: "http://localhost:5173" } })).status, 204);
  const h = await r.call("/v1/health");
  assert.equal(h.status, 200);
  assert.equal(h.headers.get("Access-Control-Allow-Origin"), null);
});

test("only Tao-S1: the upstream's own model names are refused too, and never named", async () => {
  const r = relay();
  for (const model of ["gpt-4o", "MiniMax-M3", "minimax-m3", "tao-s1", 42]) {
    const res = await r.call("/v1/chat/completions", post({ ...tidy, model }));
    assert.equal(res.status, 400, String(model));
    const e = (await res.json()).error;
    assert.equal(e.type, "model_not_allowed");
    assert.doesNotMatch(e.message, /MiniMax|M3/i);
    assert.match(e.message, /Tao-S1/);
  }
  assert.equal(r.calls.length, 0);
  for (const model of [undefined, null, "", "Tao-S1"]) {
    const ok = relay();
    const res = await ok.call("/v1/chat/completions", post({ ...tidy, model }));
    assert.equal(res.status, 200, String(model));
    assert.equal(ok.calls[0].body.model, "MiniMax-M3");
  }
});

test("bodies within the limit, valid JSON with messages; nothing refused here reaches the upstream or is counted", async () => {
  const big = relay({ env: { MAX_BODY_BYTES: "1000" } });
  const over = await big.call("/v1/chat/completions", post({ ...tidy, messages: [{ role: "user", content: "x".repeat(2000) }] }));
  assert.equal(over.status, 413);
  const tooLarge = (await over.json()).error;
  assert.equal(tooLarge.type, "too_large");
  assert.match(tooLarge.message, /上限（1 KB）.*新开一个对话/);
  assert.match((await (await relay().call("/v1/chat/completions", post(tidy, { "Content-Length": "2000001" }))).json()).error.message, /上限（2 MB）/);
  const declared = await big.call("/v1/chat/completions", post(tidy, { "Content-Length": "5000" })); // refused before reading
  assert.equal(declared.status, 413);
  const cjk = await relay({ env: { MAX_BODY_BYTES: "3000" } }).call("/v1/chat/completions",
    post({ ...tidy, messages: [{ role: "user", content: "字".repeat(1500) }] })); // 1 500 characters, 4 500 bytes
  assert.equal(cjk.status, 413);
  const chunked = relay({ env: { MAX_BODY_BYTES: "1000" } });
  let pulled = 0;
  const body = new ReadableStream({
    pull(c) {
      pulled++;
      if (pulled > 100) c.close();
      else c.enqueue(new TextEncoder().encode("x".repeat(100)));
    },
  });
  const stream = await chunked.call("/v1/chat/completions", { ...post(""), body, duplex: "half" });
  assert.equal(stream.status, 413);
  assert.ok(pulled < 20, `read ${pulled} pieces of a body over the limit`);
  const r = relay();
  for (const [raw, type] of [["{not json", "bad_request"], ["[]", "bad_request"], ['{"model":"Tao-S1"}', "bad_request"], ['{"messages":[]}', "bad_request"], ["null", "bad_request"]]) {
    const res = await r.call("/v1/chat/completions", post(raw));
    assert.equal(res.status, 400, raw);
    assert.equal((await res.json()).error.type, type);
  }
  assert.equal(big.calls.length + chunked.calls.length + r.calls.length, 0);
  assert.equal(big.gates.length + chunked.gates.length + r.gates.length, 0);
});

test("health and models say only what the page needs (CONTRACTS.md §1), nothing secret, no vendor", async () => {
  const r = relay();
  const res = await r.call("/v1/health", { headers: { Origin: PAGE } });
  assert.equal(res.headers.get("Cache-Control"), "no-store");
  const h = await res.json();
  assert.deepEqual(h, {
    ok: true, service: "tcmscience-studio", version: "1", model: "Tao-S1", models: ["Tao-S1"], max_output_tokens: 8192,
    limits: { per_minute: 40, per_day: 1200, tokens_per_day: 10000000 },
  });
  assert.equal(res.headers.get("Access-Control-Allow-Origin"), PAGE);
  assert.doesNotMatch(JSON.stringify(h), /MiniMax|minimax/);
  assert.ok(!JSON.stringify(h).includes(KEY));
  const m = await (await r.call("/v1/models")).json();
  assert.deepEqual(m, { object: "list", data: [{ id: "Tao-S1", object: "model", owned_by: "impf" }] });
  const head = await r.call("/v1/health", { method: "HEAD" });
  assert.equal(head.status, 200);
  // the kill switches: no key, or RELAY = "off"
  assert.equal((await (await relay({ env: { MINIMAX_API_KEY: "" } }).call("/v1/health")).json()).ok, false);
  const off = relay({ env: { RELAY: "off" } });
  assert.equal((await (await off.call("/v1/health")).json()).ok, false);
  const refused = await off.call("/v1/chat/completions", post(tidy));
  assert.deepEqual([refused.status, (await refused.json()).error.type], [503, "not_configured"]);
  const unset = relay({ env: { MINIMAX_API_KEY: undefined } });
  assert.equal((await unset.call("/v1/chat/completions", post(tidy))).status, 503);
  assert.equal(off.calls.length + unset.calls.length + off.gates.length, 0);
  // unknown paths and methods
  const missing = await r.call("/v1/nothing");
  assert.deepEqual([missing.status, (await missing.json()).error.type], [404, "not_found"]);
  const get = await r.call("/v1/chat/completions");
  assert.deepEqual([get.status, get.headers.get("Allow"), (await get.json()).error.type], [405, "POST", "method_not_allowed"]);
  const postHealth = await r.call("/v1/health", { method: "POST", headers: { Origin: PAGE }, body: "{}" });
  assert.deepEqual([postHealth.status, postHealth.headers.get("Allow")], [405, "GET, HEAD"]);
});

test("https only (loopback excepted, for local runs)", async () => {
  const r = relay();
  const clear = await r.call("/v1/chat/completions", post(tidy), "http://science.impf.ai");
  assert.equal(clear.status, 403);
  assert.equal((await clear.json()).error.type, "https_required");
  assert.equal((await r.call("/v1/health", {}, "http://science.impf.ai")).status, 403);
  assert.equal(r.calls.length, 0);
  assert.equal((await r.call("/v1/health", {}, "http://127.0.0.1:8787")).status, 200);
  assert.equal((await r.call("/v1/health", {}, "http://localhost:8787")).status, 200);
});

// ------------------------------------------------------------------ the upstream's failures
test("the upstream's failures become this service's own words, with the right status, never its text", () => quiet(async () => {
  const said = { error: { message: "invalid params, temperature of MiniMax-M3 must be in (0, 1] (api.minimax.cn) key sk-cp-12345" } };
  const outcome = async (upstream, body = tidy) => {
    const res = await relay({ upstream }).call("/v1/chat/completions", post(body));
    const text = await res.text();
    assert.doesNotMatch(text, /MiniMax|minimax|sk-|temperature of|status_msg|base_resp/, text);
    return [res.status, JSON.parse(text).error.type, res.headers.get("Retry-After")];
  };
  assert.deepEqual(await outcome(async () => jsonReply(said, 401)), [503, "upstream_auth", null]);
  assert.deepEqual(await outcome(async () => jsonReply(said, 403)), [503, "upstream_auth", null]);
  assert.deepEqual(await outcome(async () => jsonReply(said, 429)), [429, "upstream_rate", "30"]);
  assert.deepEqual(await outcome(async () => new Response("{}", { status: 429, headers: { "Retry-After": "12" } })), [429, "upstream_rate", "12"]);
  assert.deepEqual(await outcome(async () => new Response("MiniMax internal error", { status: 500 })), [502, "upstream_error", null]);
  assert.deepEqual(await outcome(async () => new Response("MiniMax gateway", { status: 504 })), [502, "upstream_error", null]);
  assert.deepEqual(await outcome(async () => jsonReply(said, 404)), [502, "upstream_error", null]);
  assert.deepEqual(await outcome(async () => jsonReply(said, 400)), [400, "upstream_rejected", null]);
  assert.deepEqual(await outcome(async () => jsonReply(said, 422)), [400, "upstream_rejected", null]);
  assert.deepEqual(await outcome(async () => { throw new TypeError("fetch failed"); }), [502, "upstream_unreachable", null]);
  // MiniMax's base_resp inside a 200 reply (it answers a stream request that way too)
  const base = (code) => async () => jsonReply({ id: "x", base_resp: { status_code: code, status_msg: "MiniMax: details" } });
  assert.deepEqual(await outcome(base(1004)), [503, "upstream_auth", null]);
  assert.deepEqual(await outcome(base(2049)), [503, "upstream_auth", null]);
  assert.deepEqual(await outcome(base(1008)), [503, "upstream_quota", null]);
  assert.deepEqual(await outcome(base(1002)), [429, "upstream_rate", "30"]);
  assert.deepEqual(await outcome(base(1039)), [429, "upstream_rate", "30"]);
  assert.deepEqual(await outcome(base(1026)), [400, "upstream_rejected", null]);
  assert.deepEqual(await outcome(base(2013)), [400, "upstream_rejected", null]);
  assert.deepEqual(await outcome(base(1013)), [502, "upstream_error", null]);
  assert.deepEqual(await outcome(async () => jsonReply(said)), [502, "upstream_error", null]); // an error object in a 200
  assert.deepEqual(await outcome(async () => new Response("<html>MiniMax gateway</html>", { status: 200, headers: { "Content-Type": "text/html" } })), [502, "upstream_error", null]);
  assert.deepEqual(await outcome(async () => new Response("not json", { status: 200, headers: { "Content-Type": "application/json" } })), [502, "upstream_error", null]);
  // the moderation refusal says what to do
  const mod = await relay({ upstream: base(1027) }).call("/v1/chat/completions", post(tidy));
  assert.match((await mod.json()).error.message, /审核/);
  // an error the upstream reports inside a stream becomes an error event in this service's words
  const failing = relay({ upstream: () => sse(['{"id":"b","choices":[{"index":0,"delta":{"content":"半"}}],"model":"MiniMax-M3"}',
    '{"id":"b","choices":[],"model":"MiniMax-M3","base_resp":{"status_code":1027,"status_msg":"output new_sensitive MiniMax"}}', "[DONE]"]) });
  const f = await (await failing.call("/v1/chat/completions", post(tidy))).text();
  assert.doesNotMatch(f, /MiniMax|sensitive|1027|status_msg/);
  const [first, err] = events(f);
  assert.equal(first.choices[0].delta.content, "半");
  assert.equal(err.error.type, "upstream_rejected");
  assert.match(err.error.message, /Tao-S1/);
  const generic = relay({ upstream: () => sse(['{"error":{"message":"MiniMax overloaded","type":"server_error"}}', "[DONE]"]) });
  assert.equal(events(await (await generic.call("/v1/chat/completions", post(tidy))).text())[0].error.type, "upstream_error");
  // every failure is answered in Chinese, telling the visitor what to do
  const auth = await relay({ upstream: async () => jsonReply(said, 401) }).call("/v1/chat/completions", post(tidy));
  assert.match((await auth.json()).error.message, /设置 → 模型/);
}));

// ------------------------------------------------------------------ limits
test("limits: per visitor per minute, per visitor per day, all visitors per day, a new day", async () => {
  let t = NOON + 5000;
  const r = relay({ env: { PER_MINUTE: "2", PER_DAY: "3", TOTAL_PER_DAY: "4" }, now: () => t });
  assert.equal((await r.call("/v1/chat/completions", post(tidy))).status, 200);
  assert.equal((await r.call("/v1/chat/completions", post(tidy))).status, 200);
  const minute = await r.call("/v1/chat/completions", post(tidy));
  assert.equal(minute.status, 429);
  const m = (await minute.json()).error;
  assert.equal(m.type, "rate_limited");
  assert.match(m.message, /每分钟最多 2 次/);
  assert.equal(minute.headers.get("Retry-After"), "55");
  t += 60000; // the next minute
  assert.equal((await r.call("/v1/chat/completions", post(tidy))).status, 200);
  const dayRes = await r.call("/v1/chat/completions", post(tidy));
  assert.equal(dayRes.status, 429);
  const d = (await dayRes.json()).error;
  assert.equal(d.type, "daily_limit");
  assert.match(d.message, /每人每天 3 次/);
  assert.match(d.message, /北京时间每天 8:00/);
  assert.equal(Number(dayRes.headers.get("Retry-After")), 12 * 3600 - 65);
  assert.equal((await r.call("/v1/chat/completions", post(tidy, { "CF-Connecting-IP": "198.51.100.1" }))).status, 200);
  const total = await r.call("/v1/chat/completions", post(tidy, { "CF-Connecting-IP": "198.51.100.2" }));
  assert.deepEqual([total.status, (await total.json()).error.type], [429, "total_limit"]);
  assert.equal(r.calls.length, 4);
  t = Date.UTC(2026, 9, 8, 0, 0, 1); // a new UTC day
  assert.equal((await r.call("/v1/chat/completions", post(tidy))).status, 200);
  // "0" is no limit; a mistyped number keeps the default rather than lifting the limit
  assert.equal(config({ PER_DAY: "0" }).chat.perDay, 0);
  assert.equal(config({ PER_DAY: "lots" }).chat.perDay, 1200);
  assert.equal(config({ PER_DAY: "-5" }).chat.perDay, 1200);
  assert.equal(config({ MAX_OUTPUT_TOKENS: "0" }).maxTokens, 8192);
});

test("the ten-second gate comes before the body is read; an IPv6 visitor is one /64", async () => {
  const gated = relay({ burst: (key) => key !== "203.0.113.7" });
  const request = new Request("https://science.impf.ai/v1/chat/completions", post(tidy));
  const refused = await handle(request, gated.env, gated.deps);
  assert.equal(refused.status, 429);
  assert.equal(refused.headers.get("Retry-After"), "10");
  assert.equal((await refused.json()).error.type, "rate_limited");
  assert.equal(request.bodyUsed, false);
  assert.equal(gated.calls.length + gated.gates.length, 0);
  assert.equal((await gated.call("/v1/chat/completions", post(tidy, { "CF-Connecting-IP": "2001:db8:1:2:aaaa::1" }))).status, 200);
  assert.deepEqual(gated.bursts.slice(-1), ["2001:db8:1:2::/64"]);
  assert.equal(ipKey("2001:DB8:0001:0002:0:0:0:9"), "2001:db8:1:2::/64");
  assert.equal(ipKey("::1"), "0:0:0:0::/64");
  assert.equal(ipKey("198.51.100.4"), "198.51.100.4");
  assert.equal(ipKey(""), "unknown");
});

test("tokens: a call is charged its size as it goes out, settled by what the answer cost, once", async () => {
  const final = '{"id":"a","choices":[{"finish_reason":"stop","index":0,"delta":{}}],"model":"MiniMax-M3","usage":{"prompt_tokens":70,"completion_tokens":40,"total_characters":9},"base_resp":{"status_code":0}}';
  const stream = ['{"id":"a","choices":[{"index":0,"delta":{"content":"答"}}],"model":"MiniMax-M3","usage":null}', final, "[DONE]"];
  const r = relay({ env: { TOKENS_PER_DAY: "250", TOTAL_TOKENS_PER_DAY: "300" }, upstream: () => sse(stream) });
  const first = await r.call("/v1/chat/completions", post(tidy));
  const reserved = r.tokens();
  assert.ok(reserved > 30 && reserved < 250, String(reserved)); // about a third of the body's bytes, charged at once
  await first.text(); // read to its end, as a browser does
  await r.settle();
  assert.equal(r.tokens(), 110);
  await (await r.call("/v1/chat/completions", post(tidy))).text();
  await r.settle();
  await (await r.call("/v1/chat/completions", post(tidy))).text();
  await r.settle(); // 330 of 250
  const over = await r.call("/v1/chat/completions", post(tidy));
  assert.equal(over.status, 429);
  const e = (await over.json()).error;
  assert.deepEqual([e.type, /按用量计/.test(e.message)], ["total_limit", true]); // 330 is over the day's 300 as well
  const mine = relay({ env: { TOKENS_PER_DAY: "200" }, upstream: () => sse(stream) });
  for (let i = 0; i < 2; i++) { await (await mine.call("/v1/chat/completions", post(tidy))).text(); await mine.settle(); }
  const own = await mine.call("/v1/chat/completions", post(tidy));
  assert.deepEqual([own.status, (await own.json()).error.type], [429, "daily_limit"]);
  // a stream that reports its usage in every event (cumulative) is counted once
  const repeated = relay({ env: { TOKENS_PER_DAY: "100000" }, upstream: () => sse([
    '{"choices":[{"index":0,"delta":{"content":"一"}}],"usage":{"prompt_tokens":70,"completion_tokens":1}}',
    '{"choices":[{"index":0,"delta":{"content":"二"}}],"usage":{"prompt_tokens":70,"completion_tokens":2}}',
    '{"choices":[{"index":0,"delta":{}, "finish_reason":"stop"}],"usage":{"prompt_tokens":70,"completion_tokens":3}}', "[DONE]"]) });
  await (await repeated.call("/v1/chat/completions", post(tidy))).text();
  await repeated.settle();
  assert.equal(repeated.tokens(), 73);
  // a plain JSON reply counts too
  const plain = relay({ env: { TOKENS_PER_DAY: "100" }, upstream: async () => jsonReply({ id: "c", model: "MiniMax-M3", choices: [{ message: { content: "好" } }], usage: { prompt_tokens: 90, completion_tokens: 20 } }) });
  assert.equal((await plain.call("/v1/chat/completions", post({ ...tidy, stream: false }))).status, 200);
  await plain.settle();
  assert.equal((await plain.call("/v1/chat/completions", post({ ...tidy, stream: false }))).status, 429);
  // without token limits nothing is reserved or spent: no needless calls to the Durable Object
  const free = relay({ env: { TOKENS_PER_DAY: "0", TOTAL_TOKENS_PER_DAY: "0" }, upstream: () => sse(stream) });
  for (let i = 0; i < 3; i++) { await (await free.call("/v1/chat/completions", post(tidy))).text(); await free.settle(); }
  assert.deepEqual([free.calls.length, free.spends.length, free.tokens()], [3, 0, 0]);
  // a call that brought no answer is refunded its reserve, but still counts as a call
  const failing = relay({ env: { TOKENS_PER_DAY: "100000", PER_DAY: "2" }, upstream: async () => jsonReply({ base_resp: { status_code: 1013 } }) });
  await quiet(async () => {
    for (let i = 0; i < 2; i++) assert.equal((await failing.call("/v1/chat/completions", post(tidy))).status, 502);
  });
  await failing.settle();
  assert.equal(failing.tokens(), 0);
  assert.equal((await (await failing.call("/v1/chat/completions", post(tidy))).json()).error.type, "daily_limit");
  for (const upstream of [async () => new Response("x", { status: 500 }), async () => { throw new TypeError("net"); },
    async () => new Response("<html>", { headers: { "Content-Type": "text/html" } })]) {
    const f = relay({ env: { TOKENS_PER_DAY: "100000" }, upstream });
    await quiet(async () => f.call("/v1/chat/completions", post(tidy)));
    await f.settle();
    assert.equal(f.tokens(), 0);
  }
  // an answer the visitor stopped keeps its reserve (its usage never arrived)
  const stopped = relay({ env: { TOKENS_PER_DAY: "100000" }, upstream: () => sse(stream) });
  const res = await stopped.call("/v1/chat/completions", post(tidy));
  const held = stopped.tokens();
  await res.body.cancel();
  await stopped.settle();
  assert.equal(stopped.tokens(), held);
});

test("fail closed: when the counters cannot be reached, no call goes out uncounted", () => quiet(async () => {
  const down = relay({ gateFails: true });
  const res = await down.call("/v1/chat/completions", post(tidy));
  assert.equal(res.status, 503);
  assert.equal(res.headers.get("Retry-After"), "60");
  const e = (await res.json()).error;
  assert.equal(e.type, "unavailable");
  assert.match(e.message, /Tao-S1 暂时不可用/);
  assert.equal(down.calls.length, 0);
  const odd = relay({ gateAnswer: undefined, gateFails: false });
  odd.deps.gate = async () => undefined; // a malformed answer counts as no answer
  assert.equal((await odd.call("/v1/chat/completions", post(tidy))).status, 503);
  assert.equal(odd.calls.length, 0);
  // a failure inside the handler itself is a relay_error, never a stack trace or the request
  const broken = relay();
  broken.deps.now = () => { throw new Error("visitor text: 桂枝汤"); };
  const b = await broken.call("/v1/chat/completions", post(tidy));
  assert.equal(b.status, 500);
  const text = await b.text();
  assert.equal(JSON.parse(text).error.type, "relay_error");
  assert.doesNotMatch(text, /桂枝汤|visitor text/);
}));

// ------------------------------------------------------------------ cancellation
test("cancellation: the visitor stopping, or leaving, ends the upstream call", async () => {
  // before the answer starts: the upstream fetch carries the request's own signal
  const controller = new AbortController();
  let seen;
  const pending = relay({
    upstream: (url, init) => new Promise((resolve, reject) => {
      seen = init.signal;
      init.signal.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
    }),
  });
  const answer = pending.call("/v1/chat/completions", { ...post(tidy), signal: controller.signal });
  await new Promise((r) => setTimeout(r, 10));
  assert.equal(seen.aborted, false);
  controller.abort();
  assert.equal(seen.aborted, true);
  assert.equal((await answer).status, 502); // nobody reads it: the visitor has gone
  // during the answer: cancelling what the visitor reads cancels the upstream's body
  let cancelled = false;
  const encoder = new TextEncoder();
  const streaming = relay({
    upstream: () => new Response(new ReadableStream({
      start(c) { c.enqueue(encoder.encode('data: {"choices":[{"index":0,"delta":{"content":"一"}}],"model":"MiniMax-M3"}\n\n')); },
      cancel() { cancelled = true; },
    }), { status: 200, headers: { "Content-Type": "text/event-stream" } }),
  });
  for (const thinking of [undefined, { type: "disabled" }]) {
    cancelled = false;
    const res = await streaming.call("/v1/chat/completions", post({ ...tidy, thinking }));
    const reader = res.body.getReader();
    const { value } = await reader.read();
    assert.match(new TextDecoder().decode(value), /Tao-S1|一/);
    await reader.cancel();
    for (let i = 0; i < 20 && !cancelled; i++) await new Promise((r) => setTimeout(r, 5));
    assert.equal(cancelled, true, `thinking ${JSON.stringify(thinking)}`);
  }
});

// ------------------------------------------------------------------ configuration
test("config: defaults, the Worker's variables over them, empty values fall back", () => {
  const c = config({ MODELS: "MiniMax-M3, MiniMax-M2.7", UPSTREAM_BASE: "https://api.minimax.io/v1/", UPSTREAM_FIELDS: "not json", ALLOWED_ORIGINS: " a , b ,," });
  assert.equal(c.model, "MiniMax-M3");
  assert.equal(c.upstream, "https://api.minimax.io/v1");
  assert.deepEqual(c.upstreamFields, {});
  assert.deepEqual(c.origins, ["a", "b"]);
  const d = config({ PUBLIC_MODEL: "", RELAY: "" });
  assert.equal(d.publicModel, "Tao-S1");
  assert.equal(d.enabled, true);
  for (const off of ["off", "OFF", "0", "false", "no"]) assert.equal(config({ RELAY: off }).enabled, false);
  assert.deepEqual(config().origins, ["https://science.impf.ai", "http://127.0.0.1:8765", "http://localhost:8765"]);
  assert.deepEqual(config({ UPSTREAM_FIELDS: "[1]" }).upstreamFields, {});
  assert.equal(config({ UPSTREAM_BASE: "http://127.0.0.1:8790/v1" }).upstream, "http://127.0.0.1:8790/v1");
});

test("the handler is pure over its dependencies (the Worker entry binds them)", async () => {
  const r = relay();
  const res = await handle(new Request("https://science.impf.ai/v1/health"), { MINIMAX_API_KEY: "k" }, r.deps);
  assert.equal((await res.json()).ok, true);
});
