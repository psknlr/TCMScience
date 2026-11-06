import "./fixtures/setup.mjs";
import assert from "node:assert/strict";
import { test } from "node:test";
import { streamChat as anthropicStream } from "../js/core/llm/anthropic.js";
import { streamChat as openaiStream } from "../js/core/llm/openai.js";
import { IDLE_MS, LOCAL_IDLE_MS, MAX_IDLE_MS, ProviderError, idleTimeout, isAccountError, readSSE, parseErrorBody, isRetryableStatus, retryAfterSeconds } from "../js/core/llm/sse.js";
import { bytePieces, fakeFetch, streamResponse } from "./fixtures/fakes.mjs";

async function collect(chunks) {
  const out = [];
  for await (const ev of readSSE(streamResponse(chunks).body)) out.push(ev);
  return out;
}

test("events dispatch on blank lines; multi-line data joins with newlines; comments are skipped", async () => {
  const evs = await collect([": keep-alive\n\nevent: state\nid: 7\ndata: {\"a\":1}\n\ndata: line one\ndata: line two\n\n"]);
  assert.deepEqual(evs, [
    { event: "state", data: "{\"a\":1}", id: "7" },
    { event: "message", data: "line one\nline two", id: "7" },
  ]);
});

test("CRLF, CR and LF line ends, split anywhere (including inside a CRLF and a UTF-8 character)", async () => {
  const text = "data: {\"t\":\"桂枝汤\"}\r\n\r\ndata: second\r\rdata: third\n\n";
  for (const n of [1, 2, 3, 5, 7, 64]) {
    const evs = await collect(bytePieces(text, n));
    assert.deepEqual(evs.map((e) => e.data), ["{\"t\":\"桂枝汤\"}", "second", "third"], `piece size ${n}`);
  }
});

test("a final event without the closing blank line is still delivered", async () => {
  const evs = await collect(["data: a\n\ndata: tail"]);
  assert.deepEqual(evs.map((e) => e.data), ["a", "tail"]);
});

test("bare JSON lines (no data: framing) are events", async () => {
  const evs = await collect(["{\"choices\":[]}\n{\"done\":true}\n"]);
  assert.deepEqual(evs.map((e) => e.data), ["{\"choices\":[]}", "{\"done\":true}"]);
});

test("data: with no space and empty data lines", async () => {
  const evs = await collect(["data:x\n\ndata:\ndata: y\n\n"]);
  assert.deepEqual(evs.map((e) => e.data), ["x", "\ny"]);
});

test("an aborted signal stops the read with an AbortError", async () => {
  const ctl = new AbortController();
  const res = streamResponse(["data: 1\n\n", "data: 2\n\n", "data: 3\n\n"]);
  const seen = [];
  await assert.rejects(async () => {
    for await (const ev of readSSE(res.body, { signal: ctl.signal })) {
      seen.push(ev.data);
      ctl.abort();
    }
  }, { name: "AbortError" });
  assert.deepEqual(seen, ["1"]);
});

test("error bodies, retryable statuses and Retry-After", () => {
  assert.deepEqual(parseErrorBody('{"error":{"message":"今日额度已用完","type":"daily_limit"}}'), { message: "今日额度已用完", type: "daily_limit", code: "" });
  assert.deepEqual(parseErrorBody('{"base_resp":{"status_code":1004,"status_msg":"bad key"}}'), { message: "bad key", type: "", code: "" });
  // OpenAI: a generic type and a specific code
  assert.deepEqual(parseErrorBody('{"error":{"message":"too long","type":"invalid_request_error","param":"messages","code":"context_length_exceeded"}}'),
    { message: "too long", type: "invalid_request_error", code: "context_length_exceeded" });
  assert.equal(parseErrorBody("plain text").message, "plain text");
  assert.equal(isRetryableStatus(429, "rate_limited"), true);
  assert.equal(isRetryableStatus(429, "daily_limit"), false, "a daily limit is not transient");
  assert.equal(isRetryableStatus(503, ""), true);
  assert.equal(isRetryableStatus(400, ""), false);
  assert.equal(retryAfterSeconds(new Headers({ "retry-after": "30" })), 30);
  assert.equal(retryAfterSeconds(new Headers()), null);
});

// ------------------------------------------------------------------------------- error classes of users' own providers

const own = (fetch, over = {}) => ({
  id: "openai", label: "OpenAI", format: "openai", baseUrl: "https://api.openai.com/v1", model: "gpt-x", apiKey: "sk-test",
  key_required: true, route: "direct", tools: true, stream: "delta", fetch, ...over,
});
const claudeOwn = (fetch) => own(fetch, { id: "anthropic", format: "anthropic", baseUrl: "https://api.anthropic.com/v1", model: "claude-opus-5-5" });
const failWith = (status, body) => fakeFetch(() => new Response(typeof body === "string" ? body : JSON.stringify(body), { status }));
async function errorOf(stream, provider) {
  try { for await (const _ of stream({ provider, system: "", messages: [] })); } catch (err) { return err; }
  return null;
}

test("a user's own provider: quota, billing, key and 402 errors are permanent and never retried", async () => {
  const cases = [
    [429, { error: { message: "You exceeded your current quota", type: "insufficient_quota", param: null, code: "insufficient_quota" } }],
    [429, { error: { message: "Billing hard limit has been reached", type: "invalid_request_error", code: "billing_hard_limit_reached" } }],
    [429, { error: { message: "Your account is suspended due to insufficient balance", type: "exceeded_current_quota_error" } }],
    [401, { error: { message: "Incorrect API key provided", type: "invalid_request_error", param: null, code: "invalid_api_key" } }],
    [402, { error: { message: "Insufficient Balance", type: "unknown_error" } }],
    [402, "Payment Required"],
  ];
  for (const [status, body] of cases) {
    const err = await errorOf(openaiStream, own(failWith(status, body)));
    assert.equal(err?.name, "ProviderError", JSON.stringify(body));
    assert.equal(err.retryable, false, `${status} ${JSON.stringify(body)}: not retried`);
    assert.equal(err.permanent, true, `${status} ${JSON.stringify(body)}: no Retry`);
    assert.equal(err.compactable, false);
  }
  const billing = await errorOf(anthropicStream, claudeOwn(failWith(402, { type: "error", error: { type: "billing_error", message: "Your credit balance is too low" } })));
  assert.deepEqual([billing.permanent, billing.retryable], [true, false]);
});

test("an unknown key or an account out of credit points to the model settings, whatever the service calls it", async () => {
  // Anthropic's 401 says "x-api-key", which no plain "API key" search finds; the turn record keeps status and type
  const anthropic = await errorOf(anthropicStream, claudeOwn(failWith(401, { type: "error", error: { type: "authentication_error", message: "invalid x-api-key" } })));
  assert.equal(anthropic.permanent, true);
  assert.equal(isAccountError(anthropic), true);
  assert.equal(isAccountError({ message: anthropic.message, kind: "model", status: 401, type: anthropic.type, permanent: true }), true);
  const quota = await errorOf(openaiStream, own(failWith(429, { error: { message: "You exceeded your current quota", type: "insufficient_quota", code: "insufficient_quota" } })));
  assert.equal(isAccountError(quota), true);
  const key = await errorOf(openaiStream, own(failWith(401, { error: { message: "Incorrect API key provided", type: "invalid_request_error", code: "invalid_api_key" } })));
  assert.equal(isAccountError(key), true, "by its code");
  assert.equal(isAccountError({ status: 402 }), true);
  // not the user's account: a rate limit, the relay's own limits, a stall, a request too long
  const rate = await errorOf(openaiStream, own(failWith(429, { error: { message: "Rate limit reached", type: "requests", code: "rate_limit_exceeded" } })));
  for (const e of [rate, { status: 429, type: "daily_limit" }, { status: 502, type: "upstream_auth" }, { type: "stream_stalled" }, { status: 400, type: "context_length_exceeded" }, {}]) {
    assert.equal(isAccountError(e), false, JSON.stringify({ status: e.status, type: e.type }));
  }
});

test("a request too large for the service is compactable (and, until the agent can shorten it, permanent)", async () => {
  const overflow = await errorOf(openaiStream, own(failWith(400, {
    error: { message: "This model's maximum context length is 128000 tokens. However, your messages resulted in 131072 tokens.", type: "invalid_request_error", param: "messages", code: "context_length_exceeded" },
  })));
  assert.equal(overflow.code, "context_length_exceeded");
  assert.equal(overflow.compactable, true);
  assert.equal(overflow.permanent, true);
  assert.equal(overflow.retryable, false);
  // the relay's body limit, Anthropic's request size and its context window (a 400 that says so only in its words)
  const relay = await errorOf(openaiStream, own(failWith(413, { error: { message: "请求过大", type: "too_large" } }), { id: "tao", relay: true, apiKey: "", key_required: false }));
  assert.deepEqual([relay.compactable, relay.permanent, relay.retryable], [true, true, false]);
  const tooBig = await errorOf(anthropicStream, claudeOwn(failWith(413, { type: "error", error: { type: "request_too_large", message: "Request exceeds the maximum allowed number of bytes." } })));
  assert.deepEqual([tooBig.compactable, tooBig.permanent], [true, true]);
  const tooLong = await errorOf(anthropicStream, claudeOwn(failWith(400, { type: "error", error: { type: "invalid_request_error", message: "prompt is too long: 215034 tokens > 200000 maximum" } })));
  assert.deepEqual([tooLong.compactable, tooLong.permanent, tooLong.retryable], [true, true, false]);
  // another 400 is neither
  const other = await errorOf(anthropicStream, claudeOwn(failWith(400, { type: "error", error: { type: "invalid_request_error", message: "max_tokens: Field required" } })));
  assert.deepEqual([other.compactable, other.permanent], [false, false]);
});

test("upstream_unknown is not retried by the agent and not permanent: the page keeps its Retry", async () => {
  const relay = own(failWith(502, { error: { message: "上游返回了无法识别的错误", type: "upstream_unknown" } }), { id: "tao", relay: true, apiKey: "", key_required: false });
  const err = await errorOf(openaiStream, relay);
  assert.equal(err.type, "upstream_unknown");
  assert.equal(err.retryable, false);
  assert.equal(err.permanent, false);
  assert.equal(isRetryableStatus(502, "upstream_unknown"), false);
  assert.equal(new ProviderError("x", { status: 502, type: "upstream_unknown", retryable: true }).retryable, false);
});

test("a 429 without a quota type keeps the retry path; a permanent error is never marked retryable", async () => {
  const rate = await errorOf(openaiStream, own(failWith(429, { error: { message: "Rate limit reached for requests", type: "requests", code: "rate_limit_exceeded" } })));
  assert.deepEqual([rate.retryable, rate.permanent], [true, false]);
  const bare = await errorOf(openaiStream, own(failWith(429, "Too Many Requests")));
  assert.deepEqual([bare.retryable, bare.permanent], [true, false]);
  assert.equal(new ProviderError("x", { status: 429, type: "insufficient_quota", retryable: true }).retryable, false);
  assert.equal(isRetryableStatus(402, ""), false);
  assert.equal(isRetryableStatus(429, "", "insufficient_quota"), false);
});

// ------------------------------------------------------------------------------------------------- the idle timer

/** A body that sends `chunks` ({text, after?: ms on the clock}) and then nothing, ever (unless `close`). */
function stallingBody(chunks, { close = false } = {}) {
  const enc = new TextEncoder();
  const seen = { cancelled: 0 };
  let i = 0;
  const body = new ReadableStream({
    async pull(controller) {
      if (i >= chunks.length) {
        if (close) { controller.close(); return undefined; }
        return new Promise(() => {}); // a connection that stays open and silent
      }
      const c = chunks[i++];
      if (c.after) await new Promise((r) => setTimeout(r, c.after));
      controller.enqueue(enc.encode(c.text));
      return undefined;
    },
    cancel() { seen.cancelled++; },
  });
  return { body, seen };
}

const flush = async (n = 10) => { for (let k = 0; k < n; k++) await new Promise((r) => setImmediate(r)); };

/** Read the events' data into `out`; resolves to the error that ended the read, or "ended". */
async function consume(body, opts, out) {
  try { for await (const ev of readSSE(body, opts)) out.push(ev.data); } catch (err) { return err; }
  return "ended";
}

test("idle timer: nothing for idleMs ends the read with a retryable network error and cancels the body (fake clock)", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const { body, seen } = stallingBody([{ text: "data: 1\n\n" }]);
  const out = [];
  let settled = null;
  consume(body, { idleMs: 90_000 }, out).then((r) => { settled = r; });
  await flush();
  assert.deepEqual(out, ["1"]);
  t.mock.timers.tick(89_999);
  await flush();
  assert.equal(settled, null, "not before 90 s");
  t.mock.timers.tick(1);
  await flush();
  assert.ok(settled instanceof ProviderError, String(settled));
  assert.equal(settled.type, "stream_stalled");
  assert.equal(settled.kind, "network");
  assert.equal(settled.retryable, true);
  assert.equal(settled.maxRetries, 1, "retried once");
  assert.equal(settled.permanent, false);
  assert.match(settled.message, /90/);
  assert.equal(seen.cancelled, 1, "the body is cancelled, which aborts its fetch");
});

test("idle timer: every chunk resets it, keep-alive comments included; pauses under the limit are not cut", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const { body } = stallingBody([
    { text: "data: a\n\n" }, { after: 60_000, text: ": keep-alive\n\n" }, { after: 60_000, text: ": keep-alive\n\n" }, { after: 60_000, text: "data: b\n\n" },
  ], { close: true });
  const out = [];
  let settled = null;
  consume(body, { idleMs: 90_000 }, out).then((r) => { settled = r; });
  for (let k = 0; k < 3; k++) { await flush(); t.mock.timers.tick(60_000); }
  await flush();
  assert.equal(settled, "ended");
  assert.deepEqual(out, ["a", "b"]);
});

test("idle timer: an abort while the stream is silent ends the read at once, without the clock moving", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const ctl = new AbortController();
  const { body } = stallingBody([{ text: "data: 1\n\n" }]);
  let settled = null;
  consume(body, { signal: ctl.signal, idleMs: 90_000 }, []).then((r) => { settled = r; });
  await flush();
  ctl.abort();
  await flush(2);
  assert.equal(settled?.name, "AbortError");
});

test("idleTimeout: 90 s by default, the provider's own idleMs (0: none), longer for a model on the user's computer", () => {
  assert.equal(IDLE_MS, 90_000);
  assert.equal(idleTimeout({ id: "tao", relay: true, baseUrl: "/v1" }), 90_000);
  assert.equal(idleTimeout({ baseUrl: "https://api.deepseek.com/v1" }), 90_000);
  assert.equal(idleTimeout({ baseUrl: "https://api.deepseek.com/v1", idleMs: 300_000 }), 300_000);
  assert.equal(idleTimeout({ baseUrl: "https://api.deepseek.com/v1", idleMs: 0 }), 0);
  assert.equal(idleTimeout({ local: true, baseUrl: "http://127.0.0.1:11434/v1" }), LOCAL_IDLE_MS);
  assert.equal(idleTimeout({ baseUrl: "http://localhost:8080/v1" }), LOCAL_IDLE_MS);
  assert.equal(idleTimeout({ local: true, idleMs: 120_000 }), 120_000);
});

test("idleTimeout: a model server elsewhere on the user's network gets the local default too", () => {
  for (const url of ["http://192.168.1.20:8080/v1", "http://10.0.0.5:11434/v1", "http://172.20.1.1:8000/v1", "http://100.101.2.3:8000/v1", "http://[fd12:3456::1]:8080/v1", "http://gpubox:8080/v1", "http://studio.local:1234/v1"]) {
    assert.equal(idleTimeout({ baseUrl: url }), LOCAL_IDLE_MS, url);
  }
  for (const url of ["https://generativelanguage.googleapis.com/v1beta/openai", "http://172.32.0.1/v1", "http://8.8.8.8/v1", "http://[2001:db8::1]/v1"]) {
    assert.equal(idleTimeout({ baseUrl: url }), IDLE_MS, url);
  }
});

test("idleTimeout: what a user sets is read as ms, at most setTimeout's ceiling; anything else sets no limit", () => {
  assert.equal(MAX_IDLE_MS, 2 ** 31 - 1);
  assert.equal(idleTimeout({ idleMs: 3e9 }), MAX_IDLE_MS, "a longer delay would fire at once");
  assert.equal(idleTimeout({ idleMs: "120000" }), 120_000);
  assert.equal(idleTimeout({ idleMs: 1500.4 }), 1500);
  for (const bad of [-1, "abc", " ", null, true, [], {}, Number.NaN, Number.POSITIVE_INFINITY]) {
    assert.equal(idleTimeout({ baseUrl: "https://api.deepseek.com/v1", idleMs: bad }), IDLE_MS, String(bad));
  }
});

test("idle timer: a limit past setTimeout's ceiling does not cut the stream at once (real clock)", async () => {
  const ctl = new AbortController();
  const { body } = stallingBody([{ text: "data: 1\n\n" }]);
  let settled = null;
  consume(body, { signal: ctl.signal, idleMs: 3e9 }, []).then((r) => { settled = r; });
  await new Promise((r) => setTimeout(r, 50));
  assert.equal(settled, null, String(settled?.type || settled));
  ctl.abort();
  await flush(4);
  assert.equal(settled?.name, "AbortError");
});
