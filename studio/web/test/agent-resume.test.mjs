// Continue (P2.3): a turn that ended with an error or a stop goes on, on the same provider, in the same message, from
// its settled tool results, which are not run again. At 530031f only Retry existed: it regenerated the answer from the
// question (app.js regenerate), running every tool, approval and model call again.
import "./fixtures/setup.mjs";
import assert from "node:assert/strict";
import { test } from "node:test";
import { CONTINUE_TEXT, UNKNOWN_TEXT, canContinue, resumeOf, runTurn } from "../js/core/agent.js";
import { Catalog } from "../js/core/catalog.js";
import { ProviderError } from "../js/core/llm/sse.js";
import { activeProvider } from "../js/core/providers.js";
import { ToolRouter } from "../js/core/router.js";
import { envelopeFor, fakeRuntime, memoryApprovals, openaiStep, scriptedLLM, testCatalogDoc } from "./fixtures/fakes.mjs";

const catalog = new Catalog(testCatalogDoc());
const deepseek = { ...activeProvider({ provider: "deepseek", keys: { deepseek: "sk" } }), fetch: undefined };
const anthropic = { ...activeProvider({ provider: "anthropic", keys: { anthropic: "k" } }), fetch: undefined };
const question = { id: "u1", role: "user", content: "葛根与桂枝", conversationId: "c1", parentId: null };
const cited = (tool, n) => envelopeFor(tool, "browser", {
  text: `${tool}: succeeded\nCitations (cite as [E1] …; do not invent others):\n${Array.from({ length: n }, (_, i) => `[E${i + 1}] source ${i + 1}`).join("\n")}\nResult: {"ok":true}`,
  citations: Array.from({ length: n }, (_, i) => ({ id: `E${i + 1}`, kind: "pmid", label: `source ${i + 1}`, url: "" })),
});
const answer = (tool) => (tool === "run_pipeline" || tool === "job.pipeline" ? envelopeFor(tool, "runner", { status: "job_submitted", job: { id: "j_1", kind: "pipeline", state: "queued" } }) : cited(tool, 2));

function makeRouter({ runner = "offline", delayMs = 0 } = {}) {
  const runtimes = { browser: fakeRuntime("browser", { answer, delayMs }), runner: fakeRuntime("runner", { status: runner, answer, delayMs }) };
  const approvals = memoryApprovals({ p: { web: true, approvals: { runner: "project", "job.pipeline": "project" } } });
  return { router: new ToolRouter({ catalog, runtimes, settings: { compute: "auto" }, approvals }), runtimes };
}
const calls = (runtimes) => runtimes.browser.calls.length + runtimes.runner.calls.length;
const herb = (id, name) => ({ id, name: "tcm_herb", args: { name } });

/** The stored message as the page keeps it (app.js #finishTurn), and its tool records' envelopes (resume.toolCalls). */
function stored(res) {
  const byCall = new Map(res.toolMessages.map((m) => [m.toolCallId, m.envelope]));
  return { ...res.assistant, toolCalls: res.assistant.toolCalls.map((c) => ({ ...c, envelope: byCall.get(c.id) || null })) };
}
const toolRecords = (res) => res.toolMessages.map((m) => ({ toolCallId: m.toolCallId, envelope: m.envelope }));

test("Continue after a permanent error at step 3: no tool runs again, the first request holds both results, the answer comes", async () => {
  const { router, runtimes } = makeRouter();
  const failing = scriptedLLM([
    openaiStep({ text: "先查葛根。", calls: [herb("a", "葛根")] }),
    openaiStep({ calls: [herb("b", "桂枝")] }),
    new ProviderError("今天的额度已用完", { status: 429, type: "daily_limit" }),
  ]);
  const first = await runTurn({ provider: deepseek, system: "S", history: [question], router, catalog, settings: {}, llm: { openai: failing }, onEvent: () => {}, projectId: "p" });
  assert.equal(first.status, "error");
  assert.equal(first.assistant.error.permanent, true);
  assert.equal(first.assistant.termination, "error:model");
  assert.equal(calls(runtimes), 2);
  assert.equal(first.assistant.citeTop, 4);
  assert.equal(canContinue(first.assistant, deepseek, toolRecords(first)), "continue");

  const before = calls(runtimes);
  const resumed = scriptedLLM([openaiStep({ calls: [herb("c", "白芍")] }), openaiStep({ text: "葛根 [E1]，桂枝 [E3]，白芍 [E5]。" })]);
  const events = [];
  const res = await runTurn({ provider: deepseek, system: "S", history: [question], router, catalog, settings: {}, llm: { openai: resumed }, onEvent: (e) => events.push(e), projectId: "p", resume: stored(first) });
  assert.equal(calls(runtimes) - before, 1, "only the new call ran: 0 calls again for steps 1-2");
  const firstRequest = resumed.calls[0].messages;
  assert.deepEqual(firstRequest.map((m) => m.role), ["user", "assistant", "tool", "assistant", "tool"]);
  for (const id of ["a", "b"]) assert.equal(firstRequest.find((m) => m.tool_call_id === id).content, first.assistant.wire.find((m) => m.tool_call_id === id).content, `${id} in full`);
  assert.equal(res.status, "ok");
  assert.equal(res.assistant.termination, "answered");
  assert.equal(res.assistant.steps, 5, "the steps already used count (the failed call was one)");
  assert.deepEqual(events.filter((e) => e.type === "model.start").map((e) => e.step), [4, 5]);
  // the same message goes on: what it said, its calls, then what this run adds
  assert.equal(res.assistant.content, "先查葛根。\n\n葛根 [E1]，桂枝 [E3]，白芍 [E5]。");
  assert.deepEqual(res.assistant.toolCalls.map((c) => c.id), ["a", "b", "c"]);
  assert.deepEqual(res.toolMessages.map((m) => m.toolCallId), ["c"], "this run's tool records only");
  // citation numbers go on from the earlier run's: no id names two sources
  const ids = [...first.toolMessages, ...res.toolMessages].flatMap((m) => m.envelope.citations.map((c) => c.id));
  assert.deepEqual(ids, ["E1", "E2", "E3", "E4", "E5", "E6"]);
  assert.equal(new Set(ids).size, ids.length);
  assert.equal(res.assistant.citeTop, 6);
  assert.deepEqual(res.assistant.wire.filter((m) => m.role === "tool").map((m) => m.tool_call_id), ["a", "b", "c"]);
  assert.equal(res.assistant.error, undefined);
});

test("a job submitted in the failed turn is never submitted again; one under way when it stopped is reported, not resubmitted", async () => {
  const { router, runtimes } = makeRouter({ runner: "ready" });
  const llm = scriptedLLM([openaiStep({ calls: [{ id: "j", name: "run_pipeline", args: { pipeline: "rnaseq" } }] }), new ProviderError("upstream", { status: 400, type: "bad_request" })]);
  const first = await runTurn({ provider: deepseek, system: "", history: [question], router, catalog, settings: {}, llm: { openai: llm }, onEvent: () => {}, projectId: "p" });
  assert.equal(first.toolMessages[0].envelope.status, "job_submitted");
  const jobs = () => runtimes.runner.calls.filter((c) => /pipeline/.test(c.tool)).length;
  assert.equal(jobs(), 1);
  const resumed = scriptedLLM([openaiStep({ text: "任务已提交，用 job_status 跟进。" })]);
  const res = await runTurn({ provider: deepseek, system: "", history: [question], router, catalog, settings: {}, llm: { openai: resumed }, onEvent: () => {}, projectId: "p", resume: stored(first) });
  assert.equal(jobs(), 1, "router calls for job.* during Continue: 0");
  assert.equal(res.assistant.termination, "answered");

  // stopped while the job call and a lookup were under way
  const slow = makeRouter({ runner: "ready", delayMs: 200 });
  const ctl = new AbortController();
  setTimeout(() => ctl.abort(), 30);
  const stopped = await runTurn({ provider: deepseek, system: "", history: [question], router: slow.router, catalog, settings: {}, signal: ctl.signal, onEvent: () => {}, projectId: "p",
    llm: { openai: scriptedLLM([openaiStep({ calls: [{ id: "j2", name: "run_pipeline", args: { pipeline: "rnaseq" } }, herb("h2", "葛根")] })]) } });
  assert.equal(stopped.status, "stopped");
  assert.deepEqual(stopped.toolMessages.map((m) => m.envelope.status), ["cancelled", "cancelled"]);
  const r = resumeOf(stored(stopped), "openai");
  assert.deepEqual(r.unsettled.map((c) => c.id), ["j2", "h2"]);
  const fresh = makeRouter({ runner: "ready" });
  const again = scriptedLLM([openaiStep({ text: "葛根已查到；任务是否已提交未知，先用 job_status 查看。" })]);
  const cont = await runTurn({ provider: deepseek, system: "", history: [question], router: fresh.router, catalog, settings: {}, llm: { openai: again }, onEvent: () => {}, projectId: "p", resume: stored(stopped) });
  assert.deepEqual(fresh.runtimes.runner.calls.map((c) => c.tool).concat(fresh.runtimes.browser.calls.map((c) => c.tool)), ["tcm_herb"], "the lookup ran again; the job did not");
  const byId = Object.fromEntries(cont.toolMessages.map((m) => [m.toolCallId, m.envelope]));
  assert.equal(byId.h2.status, "succeeded");
  assert.equal(byId.j2.status, "failed");
  assert.equal(byId.j2.error.type, "interrupted");
  assert.ok(byId.j2.text.endsWith(UNKNOWN_TEXT));
  const sent = again.calls[0].messages.filter((m) => m.role === "tool");
  assert.deepEqual(sent.map((m) => m.tool_call_id), ["j2", "h2"], "each call answered once, in place");
  assert.match(sent[0].content, /Outcome: state_unknown/);
  assert.doesNotMatch(sent[1].content, /cancelled/);
});

test("Continue is offered on the provider that wrote the answer, in its format, when a settled result is there", async () => {
  const { router } = makeRouter();
  const first = await runTurn({ provider: deepseek, system: "", history: [question], router, catalog, settings: {}, onEvent: () => {},
    llm: { openai: scriptedLLM([openaiStep({ calls: [herb("a", "葛根")] }), new ProviderError("busy", { status: 503, retryable: false })]) } });
  const tools = toolRecords(first);
  assert.equal(canContinue(first.assistant, deepseek, tools), "continue");
  assert.equal(canContinue(first.assistant, anthropic, tools), null, "a stored OpenAI wire and an Anthropic provider: only Retry");
  assert.equal(canContinue(first.assistant, { ...activeProvider({ provider: "qwen", keys: { qwen: "k" } }) }, tools), null, "another provider of the same format: only Retry");
  assert.equal(canContinue({ ...first.assistant, draft: true }, deepseek, tools), null, "an interrupted draft has no wire to go on from");
  assert.equal(canContinue(first.assistant, deepseek, tools.map((t) => ({ ...t, envelope: { ...t.envelope, status: "cancelled" } }))), null, "nothing settled: nothing to keep");
  assert.equal(canContinue(first.assistant, { ...deepseek, needsKey: true }, tools), null);
  assert.equal(canContinue({ ...first.assistant, status: "ok", termination: "answered" }, deepseek, tools), null);
  assert.equal(canContinue({ ...first.assistant, status: "ok", termination: "truncated" }, deepseek, tools), "answer");
  // messages stored before this change (no termination, no wire format named) are read as they were
  assert.equal(canContinue({ ...first.assistant, wireFormat: undefined, termination: undefined }, deepseek, tools), "continue");
  assert.equal(canContinue({ role: "assistant", status: "ok", provider: "deepseek", content: "旧回答" }, deepseek, []), null);
});

test("an answer cut off at the output limit goes on from where it stopped, in the same message", async () => {
  const cut = await runTurn({ provider: deepseek, system: "", history: [question], router: null, catalog, settings: {}, onEvent: () => {},
    llm: { openai: scriptedLLM([openaiStep({ text: "桂枝汤由桂枝、芍药、生姜、", finish: "length" })]) } });
  assert.equal(cut.assistant.termination, "truncated");
  const llm = scriptedLLM([openaiStep({ text: "大枣、甘草组成。" })]);
  const res = await runTurn({ provider: deepseek, system: "", history: [question], router: null, catalog, settings: {}, llm: { openai: llm }, onEvent: () => {}, resume: cut.assistant });
  assert.deepEqual(llm.calls[0].messages.slice(-2), [{ role: "assistant", content: "桂枝汤由桂枝、芍药、生姜、" }, { role: "user", content: CONTINUE_TEXT }]);
  // one text, as it was meant: the continuation goes on in the text it was cut off in (no paragraph break mid-sentence)
  assert.equal(res.assistant.content, "桂枝汤由桂枝、芍药、生姜、大枣、甘草组成。");
  assert.deepEqual(res.assistant.segments, [{ type: "text", step: 1, text: "桂枝汤由桂枝、芍药、生姜、大枣、甘草组成。" }]);
  assert.equal(res.assistant.termination, "answered");
  assert.equal(res.assistant.notice, undefined, "the cut-off notice goes with the cut");
  assert.deepEqual(res.assistant.wire.map((m) => m.role), ["assistant", "user", "assistant"], "the request to go on is kept: the replayed roles alternate");
  assert.deepEqual(cut.assistant.segments, [{ type: "text", step: 1, text: "桂枝汤由桂枝、芍药、生姜、" }], "the stored message is not changed");

  // cut inside a code fence, and the continuation thinks first: its reasoning comes before the one text; the live
  // events say the text goes on in the cut one
  const fence = await runTurn({ provider: deepseek, system: "", history: [question], router: null, catalog, settings: {}, onEvent: () => {},
    llm: { openai: scriptedLLM([openaiStep({ reasoning: "列出组成", text: "组成：\n```\n桂枝 9g\n芍药", finish: "length" })]) } });
  const events = [];
  const more = await runTurn({ provider: deepseek, system: "", history: [question], router: null, catalog, settings: {}, onEvent: (e) => events.push(e), resume: fence.assistant,
    llm: { openai: scriptedLLM([openaiStep({ reasoning: "接着写", text: " 9g\n```\n共五味。" })]) } });
  assert.equal(more.assistant.content, "组成：\n```\n桂枝 9g\n芍药 9g\n```\n共五味。");
  assert.deepEqual(more.assistant.segments.map((s) => [s.type, s.step]), [["reasoning", 1], ["reasoning", 2], ["text", 1]]);
  assert.deepEqual(events.filter((e) => e.type === "text").map((e) => e.joins), [true]);
  // a stop in the middle of the stream goes on in the same text too
  const ctl = new AbortController();
  const stopped = await runTurn({ provider: deepseek, system: "", history: [question], router: null, catalog, settings: {}, signal: ctl.signal, onEvent: (e) => { if (e.type === "text") ctl.abort(); },
    llm: { openai: scriptedLLM([[{ type: "text", delta: "葛根解肌" }, { type: "text", delta: "退热" }]]) } });
  assert.equal(stopped.assistant.termination, "stopped");
  const on = await runTurn({ provider: deepseek, system: "", history: [question], router: null, catalog, settings: {}, onEvent: () => {}, resume: stored(stopped), llm: { openai: scriptedLLM([openaiStep({ text: "退热，生津止渴。" })]) } });
  assert.equal(on.assistant.content, "葛根解肌退热，生津止渴。");
});

test("a repeat of a call the earlier run settled is answered from it, not run again", async () => {
  const { router, runtimes } = makeRouter();
  const first = await runTurn({ provider: deepseek, system: "", history: [question], router, catalog, settings: {}, onEvent: () => {},
    llm: { openai: scriptedLLM([openaiStep({ calls: [herb("a", "葛根")] }), new ProviderError("busy", { status: 503, retryable: false })]) } });
  assert.equal(calls(runtimes), 1);
  const llm = scriptedLLM([openaiStep({ calls: [herb("a2", "葛根")] }), openaiStep({ text: "葛根 [E1]。" })]);
  const res = await runTurn({ provider: deepseek, system: "", history: [question], router, catalog, settings: {}, llm: { openai: llm }, onEvent: () => {}, resume: stored(first) });
  assert.equal(calls(runtimes), 1, "the same call with the same arguments: answered from the first");
  const env = res.toolMessages[0].envelope;
  assert.deepEqual(env.reused, { step: 1, callId: "a" });
  assert.match(llm.calls[1].messages.find((m) => m.tool_call_id === "a2").content, /Not run again: the same call/);
  assert.equal(res.assistant.termination, "answered");
});

test("Continue after a stop during the call for the answer: the calls the step limit left unrun stay unrun", async () => {
  const { router, runtimes } = makeRouter();
  let k = 0;
  const step = () => openaiStep({ calls: [herb(`t${++k}`, `h${k}`)] });
  const ctl = new AbortController();
  const first = await runTurn({ provider: deepseek, system: "", history: [question], router, catalog, settings: { maxSteps: 2 }, signal: ctl.signal,
    onEvent: (e) => { if (e.type === "text") ctl.abort(); },
    llm: { openai: scriptedLLM([step, step, [{ type: "text", delta: "据已有结果，" }, { type: "text", delta: "葛根" }]]) } });
  assert.equal(first.status, "stopped");
  assert.equal(calls(runtimes), 1, "t2 was asked for on the last allowed call: not run");
  const t2 = first.toolMessages.find((m) => m.toolCallId === "t2").envelope;
  assert.deepEqual([t2.status, t2.receipt.result.outcome, t2.limit], ["cancelled", "not_executed", "max_steps"]);
  assert.deepEqual(resumeOf(stored(first), "openai").unsettled, [], "not under way: it was never handed to the router");
  assert.equal(canContinue(first.assistant, deepseek, toolRecords(first)), "continue", "t1 is settled");
  const llm = scriptedLLM([openaiStep({ text: "葛根的主要成分是葛根素。" })]);
  const res = await runTurn({ provider: deepseek, system: "", history: [question], router, catalog, settings: { maxSteps: 2 }, llm: { openai: llm }, onEvent: () => {}, resume: stored(first) });
  assert.equal(calls(runtimes), 1, "router.call for t2 during Continue: 0");
  assert.equal(llm.calls.length, 1);
  assert.deepEqual(llm.calls[0].tools, [], "at the limit already: the call for the answer, with no tools");
  assert.equal(llm.calls[0].toolChoice, undefined);
  assert.equal(llm.calls[0].messages.at(-1).content, CONTINUE_TEXT, "asked to go on where it stopped, not to start the answer again");
  assert.equal(res.assistant.content, "据已有结果，葛根的主要成分是葛根素。");
  assert.equal(res.assistant.termination, "max_steps_synthesized");
  // only placeholders for unrun calls: nothing settled to go on from
  const unrun = toolRecords(first).map((m) => ({ ...m, envelope: m.toolCallId === "t1" ? { ...m.envelope, status: "cancelled", limit: "max_steps" } : m.envelope }));
  assert.equal(canContinue(first.assistant, deepseek, unrun), null);
});

test("the steps a turn used count against the limit; a continued request goes through the budget", async () => {
  const { router } = makeRouter();
  let k = 0;
  const step = () => openaiStep({ calls: [herb(`s${++k}`, `h${k}`)] });
  const first = await runTurn({ provider: deepseek, system: "", history: [question], router, catalog, settings: { maxSteps: 4 }, onEvent: () => {},
    llm: { openai: scriptedLLM([step, step, step, new ProviderError("x", { status: 400, type: "bad_request" })]) } });
  assert.equal(first.assistant.steps, 4);
  const llm = scriptedLLM([openaiStep({ text: "答。" })]);
  const atLimit = await runTurn({ provider: deepseek, system: "", history: [question], router, catalog, settings: { maxSteps: 4 }, llm: { openai: llm }, onEvent: () => {}, resume: stored(first) });
  // at the limit already: what is left is the call for the answer (no tools, no tool_choice on this path)
  assert.deepEqual(llm.calls[0].tools, []);
  assert.equal(llm.calls[0].toolChoice, undefined);
  assert.match(llm.calls[0].messages.at(-1).content, /limit of model calls for this turn is reached/);
  assert.equal(atLimit.assistant.steps, 5);
  assert.equal(atLimit.assistant.termination, "max_steps_synthesized");
  // a small window: the earlier results are compacted in the continued request, the newest kept whole
  const big = { ...deepseek, contextTokens: 3000, maxTokens: 100 };
  const wide = await runTurn({ provider: deepseek, system: "", history: [question], router, catalog, settings: {}, onEvent: () => {},
    llm: { openai: scriptedLLM([openaiStep({ calls: [herb("x1", "a")] }), openaiStep({ calls: [herb("x2", "b")] }), new ProviderError("x", { status: 400, type: "bad_request" })]) } });
  const padded = stored(wide);
  padded.wire = padded.wire.map((m) => (m.role === "tool" ? { ...m, content: m.content.replace('Result: {"ok":true}', `Result: {"ok":true,"rows":"${"x".repeat(8000)}"}`) } : m));
  const small = scriptedLLM([openaiStep({ text: "答。" })]);
  const res = await runTurn({ provider: big, system: "", history: [question], router, catalog, settings: {}, llm: { openai: small }, onEvent: () => {}, resume: padded });
  const sent = small.calls[0].messages.filter((m) => m.role === "tool");
  assert.match(sent[0].content, /\[shortened to fit the model's context/);
  assert.equal(res.assistant.compacted >= 1, true);
});

test("Anthropic: a continued turn drops its thinking blocks (they bound the system prompt as it was) and thinks no more", async () => {
  const wire = [
    { role: "assistant", content: [{ type: "thinking", thinking: "查", signature: "sig" }, { type: "tool_use", id: "t1", name: "tcm_herb", input: { name: "葛根" } }] },
    { role: "user", content: [{ type: "tool_result", tool_use_id: "t1", content: "葛根: succeeded" }] },
  ];
  const message = { role: "assistant", provider: "anthropic", wireFormat: "anthropic", status: "error", wire, steps: 1, segments: [{ type: "tools", step: 1, callIds: ["t1"] }], toolCalls: [{ id: "t1", name: "tcm_herb", args: { name: "葛根" }, envelope: envelopeFor("tcm_herb") }] };
  const llm = scriptedLLM([openaiStep({ text: "答。" })]);
  await runTurn({ provider: anthropic, system: "", history: [question], router: makeRouter().router, catalog, settings: {}, llm: { anthropic: llm }, onEvent: () => {}, resume: message });
  assert.equal(llm.calls[0].thinking, false);
  assert.deepEqual(llm.calls[0].messages[1].content.map((b) => b.type), ["tool_use"]);
  assert.equal(message.wire[0].content.length, 2, "the stored message is not changed");
});
