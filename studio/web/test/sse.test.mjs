import "./fixtures/setup.mjs";
import assert from "node:assert/strict";
import { test } from "node:test";
import { readSSE, parseErrorBody, isRetryableStatus, retryAfterSeconds } from "../js/core/llm/sse.js";
import { bytePieces, streamResponse } from "./fixtures/fakes.mjs";

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
  assert.deepEqual(parseErrorBody('{"error":{"message":"今日额度已用完","type":"daily_limit"}}'), { message: "今日额度已用完", type: "daily_limit" });
  assert.deepEqual(parseErrorBody('{"base_resp":{"status_code":1004,"status_msg":"bad key"}}'), { message: "bad key", type: "" });
  assert.equal(parseErrorBody("plain text").message, "plain text");
  assert.equal(isRetryableStatus(429, "rate_limited"), true);
  assert.equal(isRetryableStatus(429, "daily_limit"), false, "a daily limit is not transient");
  assert.equal(isRetryableStatus(503, ""), true);
  assert.equal(isRetryableStatus(400, ""), false);
  assert.equal(retryAfterSeconds(new Headers({ "retry-after": "30" })), 30);
  assert.equal(retryAfterSeconds(new Headers()), null);
});
