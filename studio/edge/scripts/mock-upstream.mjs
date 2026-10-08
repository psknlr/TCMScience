#!/usr/bin/env node
// A stand-in for the upstream model service, for running the Worker locally without a real key (`npm run mock`, then
// UPSTREAM_BASE=http://127.0.0.1:8790/v1 in .dev.vars). It answers POST /v1/chat/completions the way the real service
// does — its model name, its reasoning formats, its own fields, usage in the last event — so that what the relay
// changes can be seen. The last user message steers it:
//   "slow"        40 events, a quarter second apart (stop the answer and watch the log: the call is cancelled)
//   "fail:<code>" a 200 reply carrying base_resp.status_code <code> (e.g. 1008: no balance)
//   "http:<code>" that HTTP status, with the service's own error text
// Each request is logged as one JSON line: whether the key arrived, the model and the fields the relay set.
import http from "node:http";

const port = Number(process.argv[2] || 8790);
const key = process.env.MOCK_KEY || "local-test-key";
const MODEL = "MiniMax-M3";

const server = http.createServer(async (req, res) => {
  if (req.method !== "POST" || !req.url.endsWith("/chat/completions")) {
    res.writeHead(404, { "Content-Type": "application/json" }).end('{"error":{"message":"not found"}}');
    return;
  }
  const chunks = [];
  for await (const c of req) chunks.push(c);
  const body = JSON.parse(Buffer.concat(chunks).toString("utf8"));
  const last = [...(body.messages || [])].reverse().find((m) => m.role === "user")?.content || "";
  const said = typeof last === "string" ? last : JSON.stringify(last);
  console.log(JSON.stringify({
    key: req.headers.authorization === `Bearer ${key}` ? "ok" : "wrong or missing", model: body.model, stream: Boolean(body.stream),
    max_tokens: body.max_tokens ?? null, max_completion_tokens: body.max_completion_tokens ?? null, n: body.n ?? null,
    reasoning_split: body.reasoning_split ?? null, formats: (body.messages || []).flatMap((m) => (m.reasoning_details || []).map((d) => d.format)),
  }));
  if (req.headers.authorization !== `Bearer ${key}`) {
    res.writeHead(401, { "Content-Type": "application/json" }).end('{"error":{"message":"invalid api key (MiniMax)"}}');
    return;
  }
  const http_ = /^http:(\d{3})$/.exec(said);
  if (http_) {
    res.writeHead(Number(http_[1]), { "Content-Type": "application/json" }).end(`{"error":{"message":"MiniMax says ${http_[1]}"}}`);
    return;
  }
  const fail = /^fail:(\d+)$/.exec(said);
  if (fail) {
    res.writeHead(200, { "Content-Type": "application/json" })
      .end(JSON.stringify({ id: "m0", base_resp: { status_code: Number(fail[1]), status_msg: "MiniMax refused" } }));
    return;
  }
  const usage = { prompt_tokens: 25, completion_tokens: 6, total_tokens: 31, total_characters: 0 };
  if (!body.stream) {
    res.writeHead(200, { "Content-Type": "application/json; charset=utf-8" }).end(JSON.stringify({
      id: "m1", object: "chat.completion", created: 1, model: MODEL,
      choices: [{ index: 0, finish_reason: "stop", message: { role: "assistant", content: "好", reasoning_details: [{ type: "reasoning.text", id: "r1", format: "MiniMax-response-v1", index: 0, text: "想一想" }] } }],
      usage, input_sensitive: false, output_sensitive: false, base_resp: { status_code: 0, status_msg: "" },
    }));
    return;
  }
  res.writeHead(200, { "Content-Type": "text/event-stream; charset=utf-8", "Cache-Control": "no-cache" });
  const send = (event) => res.write(`data: ${JSON.stringify(event)}\n\n`);
  const chunk = (delta, extra = {}) => ({ id: "m2", object: "chat.completion.chunk", created: 1, model: MODEL, choices: [{ index: 0, delta }], usage: null, ...extra });
  let closed = false;
  res.on("close", () => { closed = true; });
  send(chunk({ role: "assistant", content: "", reasoning_details: [{ type: "reasoning.text", id: "r1", format: "MiniMax-response-v1", index: 0, text: "想一想" }] }, { input_sensitive: false }));
  const n = said === "slow" ? 40 : 3;
  for (let i = 0; i < n; i++) {
    if (closed) {
      console.log(`client went away (cancelled) at event ${i}`);
      return;
    }
    send(chunk({ content: `第${i}段 ` }));
    if (n > 3) await new Promise((r) => setTimeout(r, 250));
  }
  send({ id: "m2", object: "chat.completion.chunk", created: 1, model: MODEL, choices: [{ index: 0, finish_reason: "stop", delta: {} }], usage, base_resp: { status_code: 0, status_msg: "" } });
  res.end("data: [DONE]\n\n");
  console.log("stream finished");
});

server.listen(port, "127.0.0.1", () => console.log(`mock model service on http://127.0.0.1:${port}/v1 (key: ${key === "local-test-key" ? key : "from MOCK_KEY"})`));
