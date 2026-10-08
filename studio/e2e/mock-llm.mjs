#!/usr/bin/env node
// A scripted OpenAI-compatible model for the end-to-end tests: POST /v1/chat/completions streams SSE the way real
// services do (role first, reasoning_content, tool calls in fragments, usage in the last event, [DONE]). What it says is
// decided by the last user message (the scenario) and by whether the request already ends with a tool result (the
// second round of the agent loop). It never decides anything about the science: whatever the tool returned is echoed
// back, so a test can check that the model saw the real envelope.
//
//   node studio/e2e/mock-llm.mjs [--port N] [--upstream]   (prints "mock-llm listening on http://127.0.0.1:N")
//
// Scenarios (matched against the last user message):
//   甘草 + 甘遂          round 1 calls tcm_safety_report {subject:"甘草", co_administered:["甘遂"]};
//                        round 2 answers with [E1] and the limits, plus "工具状态：<status>"
//   RNA-seq / 流程       round 1 calls run_pipeline {pipeline:"rnaseq", …}; round 2 echoes the envelope's status and error
//   文献 / literature    calls literature_search; round 2 echoes the status (and "（未联网）" for network_off)
//   后台任务             round 1 submits job.skill.run through call_tool; then job_status until the job is done;
//                        then "任务状态：<state>"
//   慢速 / slow          a long answer, 400 pieces 30 ms apart (for the stop button)
//   你是谁 / who are you an identity answer, no reasoning
//   (a ping tool offered) the connection test of Settings → Models: one call of ping {echo:"ok"}
//   anything else        a short answer with a little reasoning
//
// Test hooks: GET /__requests → every chat request received (bodies, newest last); POST /__reset clears them. A request
// with an Origin came from a page directly ("direct"); the runner's model proxy never forwards one ("proxy").
// CORS is open (the browser runtime calls it directly), including Private Network Access preflights.

import http from "node:http";

const MODEL = "mock-sci-1";
// the name the Tao-S1 relay must never let a page see (its upstream's model and reasoning formats)
export const UPSTREAM_VENDOR = "MiniMax";
export const UPSTREAM_MODEL = "MiniMax-M3";

// first match wins
export const SCENARIOS = {
  job: { match: (s) => /后台任务|background job/i.test(s) },
  safety: { match: (s) => /甘草/.test(s) && /甘遂/.test(s) },
  pipeline: { match: (s) => /RNA-?seq|流程/i.test(s) },
  literature: { match: (s) => /文献|literature/i.test(s) },
  slow: { match: (s) => /慢速|slow/i.test(s) },
  identity: { match: (s) => /你是谁|who are you/i.test(s) },
};

export function scenarioOf(text) {
  for (const [name, sc] of Object.entries(SCENARIOS)) if (sc.match(text)) return name;
  return "plain";
}

function textOf(content) {
  if (typeof content === "string") return content;
  if (Array.isArray(content)) return content.map((p) => (typeof p === "string" ? p : p?.text || "")).join("");
  return "";
}

/** The events of one answer: [{delta?, finish?, usage?, delayMs?}] in order. */
export function plan(body) {
  const messages = body.messages || [];
  const lastUser = [...messages].reverse().find((m) => m.role === "user");
  const said = textOf(lastUser?.content);
  const scenario = scenarioOf(said);
  const tail = messages[messages.length - 1];
  const toolResult = tail?.role === "tool" ? textOf(tail.content) : null;
  const tools = new Set((body.tools || []).map((t) => t.function?.name || t.name));
  const out = [];
  const reason = (s) => out.push({ delta: { reasoning_content: s } });
  const say = (s, size = 6) => { for (let i = 0; i < s.length; i += size) out.push({ delta: { content: s.slice(i, i + size) } }); };
  const call = (id, name, args) => {
    const json = JSON.stringify(args);
    out.push({ delta: { tool_calls: [{ index: 0, id, type: "function", function: { name, arguments: "" } }] } });
    // arguments arrive in fragments, cut through multi-byte characters' neighbours, as real services do
    for (let i = 0; i < json.length; i += 7) out.push({ delta: { tool_calls: [{ index: 0, function: { arguments: json.slice(i, i + 7) } }] } });
    out.push({ finish: "tool_calls" });
  };

  if (tools.has("ping") && toolResult === null) {
    // Settings → Models → Test: the connection check asks for one call of its ping tool
    call("call_ping_1", "ping", { echo: "ok" });
  } else if (scenario === "safety") {
    if (toolResult === null) {
      reason("用户问的是甘草与甘遂同用的记载。应当调用安全性工具，而不是凭记忆回答。");
      if (tools.has("tcm_safety_report")) call("call_safety_1", "tcm_safety_report", { subject: "甘草", co_administered: ["甘遂"] });
      else { say("（测试模型：没有提供 tcm_safety_report 工具。）"); out.push({ finish: "stop" }); }
    } else {
      const status = /: (succeeded|failed|refused|cancelled|needs_approval|job_submitted)\b/.exec(toolResult)?.[1] || "unknown";
      reason("工具已返回受治理的结果，按引用作答，并说明限度。");
      say(`根据工具结果，甘草与甘遂同用在资料中记载为十八反配伍禁忌 [E1]。这是记载，不是临床安全性结论；没有记载也不等于安全。\n\n工具状态：${status}`);
      out.push({ finish: "stop" });
    }
  } else if (scenario === "pipeline") {
    if (toolResult === null) {
      reason("这需要运行 RNA-seq 流程，属于需要确认的任务。");
      call("call_pipe_1", "run_pipeline", { pipeline: "rnaseq", arguments: { samplesheet: "samples.csv", organism: "human" } });
    } else {
      const status = /: (succeeded|failed|refused|cancelled|needs_approval|job_submitted)\b/.exec(toolResult)?.[1] || "unknown";
      say(`流程调用的结果：${status}。`);
      out.push({ finish: "stop" });
    }
  } else if (scenario === "job") {
    const head = /^(\S+) → (\S+): (\w+)/.exec(toolResult || "");
    const job = /Job: (j_[0-9a-f]+) \S+ state=(\w+)/.exec(toolResult || "");
    if (toolResult === null) {
      reason("这是一个后台任务：受治理 Skill 在本机 Runner 上运行，提交后跟进状态。");
      call("call_job_1", "call_tool", { tool: "job.skill.run", arguments: { skill_id: "assess-tcm-safety", arguments: { subject: "甘草", co_administered: ["甘遂"] } } });
    } else if (job && !["succeeded", "failed", "cancelled"].includes(job[2])) {
      call("call_job_2", "job_status", { job_id: job[1], wait_s: 30 });
    } else {
      say(`任务状态：${job ? job[2] : head ? head[3] : "unknown"}。`);
      out.push({ finish: "stop" });
    }
  } else if (scenario === "literature") {
    if (toolResult === null) {
      call("call_lit_1", "literature_search", { query: "葛根芩连汤 2型糖尿病", limit: 3 });
    } else {
      const status = /^\S+ → \S+: (\w+)/.exec(toolResult)?.[1] || "unknown";
      const why = /network_off|未联网/.test(toolResult) ? "（未联网）" : "";
      say(`文献检索的结果：${status}${why}。`);
      out.push({ finish: "stop" });
    }
  } else if (scenario === "slow") {
    reason("写一段较长的说明。");
    const piece = "四气五味是中药药性理论的核心内容之一。";
    for (let i = 0; i < 400; i++) out.push({ delta: { content: piece.slice((i * 3) % piece.length, ((i * 3) % piece.length) + 3) || "。" }, delayMs: 30 });
    out.push({ finish: "stop" });
  } else if (scenario === "identity") {
    say("我是 TCMScience Studio 的研究助手（测试模型）。");
    out.push({ finish: "stop" });
  } else {
    reason("简单回答。");
    say("你好。这是测试模型的回答：工具结果会以卡片显示，并附上它的证据与发布状态。");
    out.push({ finish: "stop" });
  }
  out.push({ usage: { prompt_tokens: 120 + messages.length * 10, completion_tokens: 40, total_tokens: 160 + messages.length * 10 } });
  return { scenario, events: out };
}

function cors(req) {
  return {
    "Access-Control-Allow-Origin": req.headers.origin || "*",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers": req.headers["access-control-request-headers"] || "*",
    "Access-Control-Allow-Private-Network": "true",
    "Access-Control-Max-Age": "600",
    Vary: "Origin",
  };
}

/**
 * flavor "openai" (default): a plain OpenAI-compatible service. flavor "upstream": the service behind the Tao-S1 relay
 * as the relay sees it — it needs `Authorization: Bearer <key>`, names itself with its own model id, streams reasoning
 * as reasoning_details in its own format, and adds fields of its own; the relay must rename and strip all of that.
 */
export function startMockLLM({ port = 0, host = "127.0.0.1", log = false, flavor = "openai", key = "e2e-upstream-key", model } = {}) {
  const requests = [];
  const upstream = flavor === "upstream";
  const modelName = model || (upstream ? UPSTREAM_MODEL : MODEL);
  const server = http.createServer(async (req, res) => {
    const h = cors(req);
    const url = new URL(req.url, "http://x");
    if (req.method === "OPTIONS") { res.writeHead(204, h).end(); return; }
    if (url.pathname === "/__requests") { res.writeHead(200, { ...h, "Content-Type": "application/json" }).end(JSON.stringify(requests)); return; }
    if (url.pathname === "/__reset" && req.method === "POST") { requests.length = 0; res.writeHead(204, h).end(); return; }
    if (req.method === "GET" && /\/v1\/models$/.test(url.pathname)) {
      res.writeHead(200, { ...h, "Content-Type": "application/json" }).end(JSON.stringify({ object: "list", data: [{ id: modelName, object: "model", owned_by: "e2e" }] }));
      return;
    }
    if (req.method !== "POST" || !/\/chat\/completions$/.test(url.pathname)) {
      res.writeHead(404, { ...h, "Content-Type": "application/json" }).end('{"error":{"message":"not found","type":"not_found"}}');
      return;
    }
    const chunks = [];
    for await (const c of req) chunks.push(c);
    let body;
    try { body = JSON.parse(Buffer.concat(chunks).toString("utf8")); } catch {
      res.writeHead(400, { ...h, "Content-Type": "application/json" }).end('{"error":{"message":"bad json","type":"invalid_request_error"}}');
      return;
    }
    if (upstream && req.headers.authorization !== `Bearer ${key}`) {
      res.writeHead(401, { ...h, "Content-Type": "application/json" }).end(JSON.stringify({ error: { message: `invalid api key (${UPSTREAM_VENDOR})`, type: "authentication_error" } }));
      return;
    }
    const { scenario, events } = plan(body);
    const entry = { at: Date.now(), scenario, body, via: req.headers.origin ? "direct" : "proxy", completed: false, aborted: false };
    requests.push(entry);
    if (log) console.log(JSON.stringify({ scenario, messages: body.messages?.length, tools: body.tools?.length || 0 }));

    if (!body.stream) {
      const text = events.map((e) => e.delta?.content || "").join("");
      const calls = [];
      for (const e of events) for (const tc of e.delta?.tool_calls || []) {
        if (tc.id) calls.push({ id: tc.id, type: "function", function: { name: tc.function.name, arguments: "" } });
        else calls[calls.length - 1].function.arguments += tc.function.arguments;
      }
      const finish = events.find((e) => e.finish)?.finish || "stop";
      res.writeHead(200, { ...h, "Content-Type": "application/json" }).end(JSON.stringify({
        id: "mock-1", object: "chat.completion", created: Math.floor(Date.now() / 1000), model: modelName,
        choices: [{ index: 0, finish_reason: finish, message: { role: "assistant", content: text || null, ...(calls.length ? { tool_calls: calls } : {}) } }],
        usage: events.find((e) => e.usage)?.usage,
      }));
      entry.completed = true;
      return;
    }

    res.writeHead(200, { ...h, "Content-Type": "text/event-stream; charset=utf-8", "Cache-Control": "no-cache", Connection: "keep-alive" });
    let closed = false;
    res.on("close", () => { closed = true; });
    const created = Math.floor(Date.now() / 1000);
    const vendor = upstream ? { input_sensitive: false, output_sensitive: false, base_resp: { status_code: 0, status_msg: "" } } : {};
    let reasoning = "";
    const shape = (delta) => {
      if (!upstream || delta.reasoning_content === undefined) return delta;
      // the upstream sends its reasoning as reasoning_details, the whole text so far, in its own format
      reasoning += delta.reasoning_content;
      return { reasoning_details: [{ type: "reasoning.text", id: "rd-1", format: `${UPSTREAM_VENDOR}-response-v1`, index: 0, text: reasoning }] };
    };
    const chunk = (delta, finish = null, extra = {}) => ({ id: "mock-chunk", object: "chat.completion.chunk", created, model: modelName, choices: [{ index: 0, delta: shape(delta), finish_reason: finish }], ...vendor, ...extra });
    const send = (obj) => { if (!closed) res.write(`data: ${JSON.stringify(obj)}\n\n`); };
    send(chunk({ role: "assistant", content: "" }));
    for (const e of events) {
      if (closed) break;
      if (e.delayMs) await new Promise((r) => setTimeout(r, e.delayMs));
      if (e.delta) send(chunk(e.delta));
      else if (e.finish) send(chunk({}, e.finish));
      else if (e.usage && (upstream || body.stream_options?.include_usage)) send({ id: "mock-chunk", object: "chat.completion.chunk", created, model: modelName, choices: [], usage: e.usage, ...vendor });
    }
    if (closed) { entry.aborted = true; return; }
    res.write("data: [DONE]\n\n");
    entry.completed = true;
    res.end();
  });
  return new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(port, host, () => {
      const url = `http://${host}:${server.address().port}`;
      resolve({
        url, port: server.address().port, requests,
        close: () => new Promise((r) => { server.closeAllConnections?.(); server.close(() => r()); }),
      });
    });
  });
}

if (import.meta.url === `file://${process.argv[1]}`) {
  const i = process.argv.indexOf("--port");
  const port = i > 0 ? Number(process.argv[i + 1]) : 0;
  const flavor = process.argv.includes("--upstream") ? "upstream" : "openai";
  const m = await startMockLLM({ port, log: true, flavor });
  console.log(`mock-llm listening on ${m.url} (OpenAI-compatible base URL: ${m.url}/v1, ${flavor === "upstream" ? `as the relay's upstream, key e2e-upstream-key` : `model ${MODEL}`})`);
}

export { MODEL };
