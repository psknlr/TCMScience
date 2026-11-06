// Continue in the page (P1.5, P2.3): the notice of a cut-off answer offers "继续回答", a failed or stopped turn with
// settled results offers 继续 next to 重试 on the provider that wrote it, and continuing keeps the message, its tool
// records and its citations, running nothing again. Under linkedom with a real store (fake IndexedDB) and a scripted model.
import { window } from "./setup.mjs";
import assert from "node:assert/strict";
import test from "node:test";
import { IDBFactory } from "fake-indexeddb";

import { Catalog } from "../../js/core/catalog.js";
import { setContentConsent } from "../../js/core/consent.js";
import { setLang } from "../../js/core/i18n.js";
import { ProviderError } from "../../js/core/llm/sse.js";
import { activeProvider } from "../../js/core/providers.js";
import { ToolRouter } from "../../js/core/router.js";
import { openStore } from "../../js/core/store.js";
import { Telemetry } from "../../js/core/telemetry.js";
import { App, applyEvent, resumeLive } from "../../js/ui/app.js";
import { mountConversation } from "../../js/ui/conversation.js";
import { registerUiStrings } from "../../js/ui/strings.js";
import { envelopeFor, fakeRuntime, memoryApprovals, openaiStep, scriptedLLM, testCatalogDoc } from "../../test/fixtures/fakes.mjs";

registerUiStrings();
setLang("zh");
window.HTMLElement.prototype.scrollTo ||= function scrollTo() {}; // not in linkedom
const delay = (ms) => new Promise((r) => setTimeout(r, ms));
const cited = (tool) => envelopeFor(tool, "browser", { citations: [{ id: "E1", kind: "pmid", label: "source", url: "" }], text: `${tool}: succeeded\n[E1] source\nResult: {}` });

async function makeApp(steps, { provider = "deepseek" } = {}) {
  setContentConsent(false);
  const app = new App();
  app.store = await openStore({ indexedDB: new IDBFactory() });
  app.catalog = new Catalog(testCatalogDoc());
  app.runtimes = { browser: fakeRuntime("browser", { answer: (tool) => cited(tool) }), runner: fakeRuntime("runner", { status: "offline" }) };
  app.toolRouter = new ToolRouter({ catalog: app.catalog, runtimes: app.runtimes, settings: { compute: "browser" }, approvals: memoryApprovals() });
  let active = { ...activeProvider({ provider, keys: { [provider]: "k" } }), fetch: undefined };
  app.provider = () => active;
  app.useProvider = (id) => { active = { ...activeProvider({ provider: id, keys: { [id]: "k" } }), fetch: undefined }; };
  app.model = scriptedLLM(steps);
  app.llm = { openai: app.model };
  app.router = { navigate: (route) => { app.state.route = route; } };
  app.telemetry = new Telemetry({ origin: "https://science.impf.ai", fetch: async () => new Response(null, { status: 204 }), beacon: () => true });
  app.telemetry.configure({ ok: false }, { relayBase: "https://science.impf.ai/v1" });
  const project = await app.store.projects.create({ name: "伤寒论", web: false });
  app.state.project = project;
  app.state.projects = [project];
  return app;
}

async function finished(app) {
  for (let i = 0; i < 400 && (app.state.turn || !app.state.messages.some((m) => m.role === "assistant" && !m.draft)); i++) await delay(10);
  await delay(20);
}
const assistantOf = (app) => app.state.messages.find((m) => m.role === "assistant");
const thread = (app) => {
  const main = document.createElement("main");
  document.body.append(main);
  const view = mountConversation(app, main);
  return { main, view, buttons: () => [...main.querySelectorAll(".msg--assistant button")].map((b) => b.textContent.trim()).filter(Boolean) };
};

test("a failed turn with settled results: 重试 and 继续; Continue runs no tool again and goes on in the same message", async () => {
  const app = await makeApp([
    openaiStep({ text: "先查葛根。", calls: [{ id: "a", name: "tcm_herb", args: { name: "葛根" } }] }),
    new ProviderError("模型服务暂时繁忙", { status: 503, type: "overloaded", retryable: false }),
    openaiStep({ text: "葛根：解肌退热 [E1]。" }),
  ]);
  await app.send("葛根有什么记载？");
  await finished(app);
  const failed = assistantOf(app);
  assert.equal(failed.status, "error");
  assert.equal(failed.termination, "error:model");
  const t1 = thread(app);
  assert.ok(t1.buttons().includes("重试"));
  assert.ok(t1.buttons().includes("继续"));
  // on another provider only Retry is offered (a regenerated answer), and the thread follows a model switch
  app.useProvider("qwen");
  app.emit("model", {});
  assert.ok(!t1.buttons().includes("继续"));
  app.useProvider("deepseek");
  app.emit("model", {});
  const button = [...t1.main.querySelectorAll("[data-continue]")][0];
  assert.ok(button);
  const runs = app.runtimes.browser.calls.length;
  button.dispatchEvent(new window.Event("click"));
  await delay(5);
  await finished(app);
  t1.view.destroy();
  const after = app.state.messages.filter((m) => m.role === "assistant");
  assert.equal(after.length, 1, "the same message, not a new answer");
  assert.equal(after[0].id, failed.id);
  assert.equal(after[0].createdAt, failed.createdAt);
  assert.equal(after[0].status, "ok");
  assert.equal(after[0].termination, "answered");
  assert.equal(after[0].content, "先查葛根。\n\n葛根：解肌退热 [E1]。");
  assert.equal(after[0].error, undefined);
  assert.equal(app.runtimes.browser.calls.length, runs, "no tool ran again");
  assert.equal(app.model.calls.length, 3, "one more model call");
  assert.equal(app.state.messages.filter((m) => m.role === "tool").length, 1, "the tool record is kept, not doubled");
  assert.deepEqual(after[0].toolCalls.map((c) => c.id), ["a"]);
  assert.ok(app.model.calls[2].messages.some((m) => m.role === "tool" && m.tool_call_id === "a"), "the settled result was sent again, not run again");
});

test("a permanent error: no 重试, but 继续 when there are settled results; a stopped turn offers 继续 by its note", async () => {
  const app = await makeApp([
    openaiStep({ calls: [{ id: "a", name: "tcm_herb", args: { name: "葛根" } }] }),
    new ProviderError("今天的额度已用完", { status: 429, type: "daily_limit" }),
  ]);
  await app.send("葛根？");
  await finished(app);
  const t = thread(app);
  assert.ok(!t.buttons().includes("重试"));
  assert.ok(t.buttons().includes("继续"));
  t.view.destroy();
  // the same answer, stopped instead
  const m = assistantOf(app);
  await app.store.messages.put({ ...m, status: "stopped", error: undefined, termination: "stopped" });
  await app.loadMessages();
  const s = thread(app);
  const note = s.main.querySelector(".msg__note [data-continue]");
  assert.ok(note, "继续 beside 已停止");
  assert.ok(!s.buttons().includes("重试"));
  s.view.destroy();
  // nothing settled: only a new answer can help
  await app.store.messages.put({ ...m, status: "error" });
  for (const tm of app.state.messages.filter((x) => x.role === "tool")) await app.store.messages.put({ ...tm, envelope: { ...tm.envelope, status: "cancelled" }, status: "cancelled" });
  await app.loadMessages();
  const n = thread(app);
  assert.ok(!n.buttons().includes("继续"));
  n.view.destroy();
});

test("a cut-off answer shows its notice with 继续回答, which goes on in the same message", async () => {
  const app = await makeApp([openaiStep({ text: "桂枝汤由桂枝、芍药、", finish: "length" }), openaiStep({ text: "生姜、大枣、甘草组成。" })]);
  await app.send("桂枝汤的组成？");
  await finished(app);
  const cut = assistantOf(app);
  assert.equal(cut.termination, "truncated");
  const t = thread(app);
  const notice = [...t.main.querySelectorAll(".notice")].find((n) => /被截断/.test(n.textContent));
  assert.ok(notice, "the notice is shown");
  const go = notice.querySelector("[data-continue-answer]");
  assert.equal(go.textContent.trim(), "继续回答");
  go.dispatchEvent(new window.Event("click"));
  await delay(5);
  await finished(app);
  t.view.destroy();
  const after = app.state.messages.filter((m) => m.role === "assistant");
  assert.equal(after.length, 1);
  assert.equal(after[0].content, "桂枝汤由桂枝、芍药、\n\n生姜、大枣、甘草组成。");
  assert.equal(app.state.messages.filter((m) => m.role === "user").length, 1, "no new question");
  assert.equal(after[0].notice, undefined);
});

test("on another provider, 继续回答 asks for the rest in a new question", async () => {
  const app = await makeApp([openaiStep({ text: "桂枝汤由桂枝、", finish: "length" }), openaiStep({ text: "芍药、生姜、大枣、甘草。" })]);
  await app.send("桂枝汤的组成？");
  await finished(app);
  app.useProvider("qwen");
  app.llm = { openai: app.model };
  const t = thread(app);
  t.main.querySelector("[data-continue-answer]").dispatchEvent(new window.Event("click"));
  await delay(5);
  for (let i = 0; i < 400 && (app.state.turn || app.state.messages.filter((m) => m.role === "assistant").length < 2); i++) await delay(10);
  t.view.destroy();
  const users = app.state.messages.filter((m) => m.role === "user").map((m) => m.content);
  assert.deepEqual(users, ["桂枝汤的组成？", "请从上次停下的地方接着写，不要重复已经写过的内容。"]);
});

test("the live view of a continued answer starts from what it shows; a call run again stays in its place", () => {
  const m = {
    id: "m1", createdAt: 5, steps: 2, usage: { input: 10, output: 2 }, compacted: 3,
    segments: [{ type: "text", step: 1, text: "先查" }, { type: "tools", step: 1, callIds: ["a", "b"] }],
    toolCalls: [{ id: "a", name: "tcm_herb", args: {} }, { id: "b", name: "tcm_herb", args: {} }],
  };
  const live = resumeLive({ tools: new Map(), segments: [], createdAt: 9 }, m, [{ toolCallId: "a", envelope: envelopeFor("tcm_herb") }]);
  assert.equal(live.createdAt, 5);
  assert.equal(live.tools.get("a").call.status, "succeeded");
  assert.equal(live.tools.get("b").call.status, "cancelled");
  applyEvent(Object.assign(live, { pending: new Map(), thinkingStart: {}, thinkingMs: {} }), { type: "tool.start", callId: "b", name: "tcm_herb", args: {}, step: 1 });
  assert.deepEqual(live.segments[1].callIds, ["a", "b"], "not added twice");
  assert.equal(live.tools.get("b").call.status, "running");
  applyEvent(live, { type: "context", step: 3, compacted: 4 });
  assert.equal(live.compacted, 4);
  assert.notEqual(m.segments[1].callIds, live.segments[1].callIds, "the stored message is not changed");
});

test("results shortened to fit the context are said once, quietly", async () => {
  const app = await makeApp([openaiStep({ text: "答" })]);
  await app.send("q");
  await finished(app);
  const m = assistantOf(app);
  await app.store.messages.put({ ...m, compacted: 3 });
  await app.loadMessages();
  const t = thread(app);
  assert.match(t.main.querySelector(".msg--assistant").textContent, /3 个较早的工具结果发给模型时被缩短/);
  t.view.destroy();
});

test("only the branch's last answer goes on: a later question was asked after the earlier one as it was", async () => {
  const app = await makeApp([
    openaiStep({ calls: [{ id: "a", name: "tcm_herb", args: { name: "葛根" } }] }),
    new ProviderError("模型服务暂时繁忙", { status: 503, type: "overloaded", retryable: false }),
    openaiStep({ text: "桂枝：发汗解肌。" }),
  ]);
  await app.send("葛根？");
  await finished(app);
  const failed = assistantOf(app);
  await app.send("那桂枝呢？");
  for (let i = 0; i < 400 && (app.state.turn || app.state.messages.filter((m) => m.role === "assistant").length < 2); i++) await delay(10);
  const t = thread(app);
  const first = t.main.querySelector(`.msg--assistant[data-msg="${failed.id}"]`);
  assert.ok([...first.querySelectorAll("button")].some((b) => b.textContent.trim() === "重试"));
  assert.equal(first.querySelector("[data-continue]"), null);
  t.view.destroy();
  assert.equal(await app.continueTurn(failed.id), false);
  assert.equal(app.model.calls.length, 3);
});
