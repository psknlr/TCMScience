// The harness's parts: policies (references to earlier results, citations as the loop numbered them, the fallback when a
// reference cannot be resolved), the fault server's faults as the real client receives them, the relay's deployed
// settings, the egress recorder, and the live driver's guard.
import "../../web/test/fixtures/setup.mjs";
import assert from "node:assert/strict";
import { test } from "node:test";
import * as openaiLib from "../../web/js/core/llm/openai.js";
import { startEgressRecorder } from "../lib/egress.mjs";
import { Policy } from "../lib/policy.mjs";
import { faultDriver, startFaultServer } from "../models/fault.mjs";
import { liveRefusal } from "../models/live.mjs";
import { deployedVars } from "../models/relay.mjs";

test("a policy refers to an earlier call's result and citations; an unresolvable reference ends it with its fallback", () => {
  const p = new Policy({ steps: [
    { calls: [{ id: "c1", name: "call_tool", args: { tool: "native.translate" } }] },
    { calls: [{ id: "c2", name: "call_tool", args: { tool: "native.peptide_mass", arguments: { sequence: { $from: "c1", path: "result.protein" } } } }] },
    { text: "见 {{cite:c1}} 与 {{cite:c1:2}}，还有 {{cite:c9}}。" },
  ] });
  assert.equal(p.next().calls[0].id, "c1");
  p.observe({ type: "tool.end", callId: "c1", envelope: { result: { protein: "DRVYIHPF" }, citations: [{ id: "E3" }, { id: "E4" }] } });
  assert.equal(p.next().calls[0].args.arguments.sequence, "DRVYIHPF");
  assert.equal(p.next().text, "见 [E3] 与 [E4]，还有 [E0]。", "a citation no tool returned becomes [E0], which resolves to nothing");
  const q = new Policy({ steps: [{ calls: [{ id: "c2", name: "x", args: { id: { $from: "c1", path: "result.inquiry_id" } } }] }, { text: "没有可用的结果。" }] });
  const s = q.next();
  assert.equal(s.calls, undefined);
  assert.equal(s.text, "没有可用的结果。");
  assert.equal(q.next().text, "没有可用的结果。", "it stays ended");
});

test("a repeating step makes new calls on every request, and answers instead when no tools are offered", () => {
  const p = new Policy({ steps: [{ repeat: { name: "literature_search", parallel: 3, args: { query: "q{n}-{k}" } }, honour_tool_choice: true, on_no_tools: { text: "done" } }] });
  assert.deepEqual(p.next().calls.map((c) => c.args.query), ["q1-0", "q1-1", "q1-2"]);
  assert.deepEqual(p.next().calls.map((c) => c.id), ["r2_0", "r2_1", "r2_2"]);
  assert.equal(p.next({ toolChoice: "none" }).text, "done");
  const ignores = new Policy({ steps: [{ repeat: { name: "tcm_herb", parallel: 1, args: { name: "h{n}" } }, on_no_tools: { text: "answer" } }] });
  assert.equal(ignores.next({ toolChoice: "none" }).calls.length, 1, "without honour_tool_choice it ignores tool_choice none");
  assert.equal(ignores.next({ tools: false }).text, "answer");
});

test("the fault server's faults reach the real client as a failing service sends them", async () => {
  const server = await startFaultServer();
  const run = async (step, preset = "openai") => {
    const d = faultDriver(server, new Policy({ steps: [step] }), { preset });
    const events = [];
    let error = null;
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 1500);
    try {
      for await (const ev of openaiLib.streamChat({ provider: d.provider, system: "s", messages: [{ role: "user", content: "q" }], tools: [], signal: ctrl.signal })) events.push(ev);
    } catch (err) { error = err; } finally { clearTimeout(timer); }
    await d.close();
    return { events, error, meter: d.meter };
  };
  try {
    const r429 = await run({ http: { status: 429, headers: { "Retry-After": "5" }, json: { error: { message: "slow down", type: "requests" } } } });
    assert.equal(r429.error.status, 429);
    assert.equal(r429.error.retryable, true);
    assert.equal(r429.error.retryAfter, 5);
    const quota = await run({ http: { status: 429, json: { error: { message: "quota", type: "insufficient_quota" } } } });
    assert.equal(quota.error.type, "insufficient_quota");
    const inStream = await run({ stream_error: { error: { message: "Overloaded", type: "overloaded_error" } }, after_text: "桂枝" });
    assert.equal(inStream.events.find((e) => e.type === "text")?.delta, "桂枝");
    assert.equal(inStream.error.retryable, true);
    const code = await run({ base_resp: { status_code: 2056, status_msg: "usage limit exceeded" } });
    assert.match(code.error.message, /usage limit exceeded/);
    const cut = await run({ text: "桂枝汤的组成是", finish: "length" });
    assert.equal(cut.events.at(-1).finish_reason, "length");
    assert.equal(cut.events.filter((e) => e.type === "text").map((e) => e.delta).join(""), "桂枝汤的组成是");
    const calls = await run({ calls: [{ id: "a", name: "tcm_herb", args: { name: "甘草" } }, { id: "b", name: "tcm_herb", args: { name: "黄芪" } }] });
    assert.deepEqual(calls.events.filter((e) => e.type === "tool_call").map((e) => [e.id, e.arguments.name]), [["a", "甘草"], ["b", "黄芪"]]);
    const t0 = Date.now();
    const stall = await run({ stall: { after_text: "桂枝汤由" } });
    assert.ok(Date.now() - t0 >= 1400, "a stalled stream waits until the client gives up");
    assert.equal(stall.error?.name, "AbortError");
    assert.equal(stall.meter.requests.length, 1);
    assert.ok(stall.meter.requests[0].bytes > 0);
  } finally {
    await server.stop();
  }
});

test("the relay reads wrangler.toml's deployed [vars]: the limits, the body cap, the origins", () => {
  const vars = deployedVars();
  assert.equal(vars.MAX_BODY_BYTES, "2000000");
  assert.equal(vars.PUBLIC_MODEL, "Tao-S1");
  assert.match(vars.ALLOWED_ORIGINS, /https:\/\/science\.impf\.ai/);
  assert.equal(vars.UPSTREAM_FIELDS, '{"reasoning_split":true}');
});

test("the egress recorder refuses what the runner sends off the machine and keeps the host only", async () => {
  const egress = await startEgressRecorder();
  try {
    const u = new URL(egress.url);
    const net = await import("node:net");
    const reply = await new Promise((resolve, reject) => {
      const s = net.connect(Number(u.port), u.hostname, () => s.write("CONNECT www.ebi.ac.uk:443 HTTP/1.1\r\nHost: www.ebi.ac.uk:443\r\n\r\n"));
      let got = "";
      s.on("data", (d) => { got += d; });
      s.on("end", () => resolve(got));
      s.on("error", reject);
    });
    assert.match(reply, /^HTTP\/1\.1 403/);
    const plain = await fetch(egress.url, { headers: { Host: "example.org" } }).catch(() => null);
    assert.equal(plain?.status, 403);
    assert.deepEqual(egress.attempts.map((a) => a.method), ["CONNECT", "GET"]);
    assert.equal(egress.attempts[0].host, "www.ebi.ac.uk:443");
  } finally {
    await egress.stop();
  }
});

test("the live driver runs only when asked for, with a base URL, and in CI only from workflow_dispatch", () => {
  assert.match(liveRefusal({ EVAL_LIVE_BASE_URL: "http://x/v1" }, { asked: false }), /only when asked/);
  assert.match(liveRefusal({}, { asked: true }), /EVAL_LIVE_BASE_URL/);
  assert.match(liveRefusal({ EVAL_LIVE_BASE_URL: "http://x/v1", CI: "true", GITHUB_EVENT_NAME: "pull_request" }, { asked: true }), /workflow_dispatch/);
  assert.equal(liveRefusal({ EVAL_LIVE_BASE_URL: "http://x/v1", CI: "true", GITHUB_EVENT_NAME: "workflow_dispatch" }, { asked: true }), null);
  assert.equal(liveRefusal({ EVAL_LIVE_BASE_URL: "http://127.0.0.1:8080/v1" }, { asked: true }), null);
});
