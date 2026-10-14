// The telemetry as the app uses it, under linkedom with a real store (fake IndexedDB) and a scripted model: the About
// page links nowhere (zh and en); the notice asks once and a refusal is a refusal; Settings → Privacy; and whole turns —
// recorded only with the visitor's agreement, never with an upload or identifying text, counted either way, and
// answered the same when the telemetry fails.
import { window } from "./setup.mjs";
import assert from "node:assert/strict";
import test from "node:test";
import { IDBFactory } from "fake-indexeddb";

import { Catalog } from "../../js/core/catalog.js";
import { consentState, setContentConsent } from "../../js/core/consent.js";
import { activeProvider } from "../../js/core/providers.js";
import { envelopeFor, fakeRuntime, memoryApprovals, openaiStep, scriptedLLM, testCatalogDoc } from "../../test/fixtures/fakes.mjs";
import { setLang } from "../../js/core/i18n.js";
import { openStore } from "../../js/core/store.js";
import { ToolRouter } from "../../js/core/router.js";
import { CONSENT_VERSION } from "../../js/core/telemetry-contract.js";
import { Telemetry } from "../../js/core/telemetry.js";
import { App } from "../../js/ui/app.js";
import { mountAbout } from "../../js/ui/pages/about.js";
import { askConsent, privacyTab } from "../../js/ui/privacy.js";
import { registerUiStrings } from "../../js/ui/strings.js";

registerUiStrings();
const proto = window.HTMLElement.prototype;
if (!proto.showModal) proto.showModal = function showModal() { this.setAttribute("open", ""); };
if (!proto.close) proto.close = function close() { this.removeAttribute("open"); };
globalThis.matchMedia = () => ({ matches: true, addEventListener() {}, removeEventListener() {} }); // reduced motion: dialogs close at once

const ORIGIN = "https://science.impf.ai";
const HEALTH = { ok: true, telemetry: { analytics: true, conversations: true, consent_version: CONSENT_VERSION, geo: "country", retention_days: { conversations: 90, events: 90, aggregates: 400, security: 30 } } };
const delay = (ms) => new Promise((r) => setTimeout(r, ms));
// the test DOM's storage cannot be listed: the telemetry's keys by name
const resetChoices = () => { for (const k of ["tcmstudio.consent", "tcmstudio.telemetry.records", "tcmstudio.telemetry.turns"]) localStorage.removeItem(k); };

function telemetry({ fail = false } = {}) {
  const requests = [];
  const tel = new Telemetry({
    origin: ORIGIN,
    fetch: async (url, init) => {
      requests.push({ url, body: init?.body ? JSON.parse(init.body) : null });
      if (fail) throw new TypeError("Failed to fetch");
      return new Response(null, { status: url.endsWith("/conversations") ? 201 : 204 });
    },
    beacon: () => true,
  });
  tel.configure(HEALTH, { relayBase: `${ORIGIN}/v1` });
  return { tel, requests };
}

/** A scripted model in the agent's library shape: one answer per call, from `reply(messages)`; `slowMs` holds the
 * answer back after its first characters (a turn under way). */
function scriptedModel(reply = () => "桂枝汤主治太阳中风表虚证。", { slowMs = 0 } = {}) {
  return {
    openai: {
      async *streamChat({ messages }) {
        const text = reply(messages);
        if (slowMs) {
          yield { type: "text", delta: text.slice(0, 2) };
          await delay(slowMs);
          yield { type: "text", delta: text.slice(2) };
        } else yield { type: "text", delta: text };
        yield { type: "usage", input: 120, output: 30 };
        yield { type: "done", finish_reason: "stop", wire: { role: "assistant", content: text } };
      },
      toolResultMessages: (results) => results.map((r) => ({ role: "tool", tool_call_id: r.id, content: r.text })),
    },
  };
}

async function makeApp({ fail = false, reply, slowMs = 0 } = {}) {
  const app = new App();
  app.store = await openStore({ indexedDB: new IDBFactory() });
  app.toolRouter = new ToolRouter({ catalog: null, runtimes: {}, settings: app.state.settings, store: app.store });
  app.router = { navigate: (route) => { app.state.route = route; } };
  app.llm = scriptedModel(reply, { slowMs });
  const t = telemetry({ fail });
  app.telemetry = t.tel;
  const p = await app.store.projects.create({ name: "伤寒论", web: false });
  app.state.project = p;
  await app.refreshProjects();
  return { app, requests: t.requests, project: p };
}

async function finished(app) {
  for (let i = 0; i < 400 && (app.state.turn || !app.state.messages.some((m) => m.role === "assistant" && !m.draft)); i++) await delay(10);
  await delay(30); // the record and the usage event follow the stored answer
  await app.telemetry.flush();
}

const records = (requests) => requests.filter((r) => r.url === `${ORIGIN}/api/telemetry/conversations`).map((r) => r.body);
const events = (requests, type) => requests.filter((r) => r.url === `${ORIGIN}/api/telemetry/events`).flatMap((r) => r.body.events).filter((e) => !type || e.type === type);

// ------------------------------------------------------------------------------------------------- About

test("About links nowhere: no project homepage, Arena or repository, in Chinese and in English", () => {
  for (const l of ["zh", "en"]) {
    setLang(l);
    const main = document.createElement("main");
    mountAbout({ catalog: { versions: { tcmstudio: "0.1.0" }, core: [], entries: [] } }, main);
    const hrefs = [...main.querySelectorAll("a")].map((a) => a.getAttribute("href"));
    assert.deepEqual(hrefs, ["#/settings/privacy"], l);
    assert.doesNotMatch(main.innerHTML, /github|psknlr|Arena|项目主页|Project homepage|Source code/i, l);
    assert.equal(main.querySelector(".about__links"), null);
    assert.match(main.textContent, l === "zh" ? /设置 → 隐私/ : /Settings → Privacy/);
  }
  setLang("zh");
});

// ------------------------------------------------------------------------------------------------- the notice

test("the notice: what is and is not recorded, two answers, the refusal focused; closing it is a refusal", async () => {
  resetChoices();
  const app = { telemetry: telemetry().tel, emit() {} };
  const pending = askConsent(app);
  const dlg = [...document.querySelectorAll("dialog")].at(-1);
  assert.equal(dlg.querySelector(".dialog__title").textContent, "对话记录说明");
  assert.deepEqual([...dlg.querySelectorAll(".consent__list dt")].map((d) => d.textContent), ["记录什么", "从不记录", "用途与保存"]);
  assert.match(dlg.textContent, /90 天后自动删除/);
  assert.match(dlg.textContent, /不含工具参数/);
  assert.match(dlg.textContent, /匿名/);
  const [decline, agree] = dlg.querySelectorAll("[data-consent]");
  assert.deepEqual([decline.dataset.consent, agree.dataset.consent], ["decline", "agree"]);
  dlg.dispatchEvent(new window.Event("cancel")); // Esc
  assert.equal(await pending, false);
  assert.equal(consentState().content, false);
  const again = askConsent(app);
  [...document.querySelectorAll("dialog")].at(-1).querySelector('[data-consent="agree"]').dispatchEvent(new window.Event("click"));
  assert.equal(await again, true);
  assert.equal(consentState().content, true);
  assert.equal(consentState().hasKey, true);
});

test("Settings → Privacy: the record switch, deletion with a reference, the usage switch, unavailable when the site takes none", async () => {
  resetChoices();
  const { tel } = telemetry();
  const app = { telemetry: tel, emit() {} };
  let renders = 0;
  const tab = privacyTab(app, () => { renders++; });
  const switches = [...tab.querySelectorAll('[role="switch"]')];
  assert.equal(switches.length, 2);
  assert.equal(switches[0].getAttribute("aria-checked"), "false"); // off until agreed
  assert.equal(switches[1].getAttribute("aria-checked"), "true"); // usage statistics on unless switched off
  assert.equal(tab.querySelector('[data-key="privacy-delete"]').disabled, true); // nothing recorded, nothing to delete
  switches[0].dispatchEvent(new window.Event("click"));
  assert.equal(consentState().content, true);
  assert.equal(renders, 1);
  const after = privacyTab(app, () => {});
  assert.equal(after.querySelector('[data-key="privacy-delete"]').disabled, false);
  await delay(10);
  assert.match(after.querySelector(".privacy__ref").textContent, /^记录编号：[0-9a-f]{12}/);
  // a page whose site takes nothing says so, and offers nothing to switch on
  const none = new Telemetry({ origin: ORIGIN, fetch: async () => new Response(null) });
  none.configure({ ok: false }, { relayBase: `${ORIGIN}/v1` });
  const off = privacyTab({ telemetry: none, emit() {} }, () => {});
  assert.match(off.textContent, /本站当前不收集对话记录/);
  assert.match(off.textContent, /本站当前不收集使用统计/);
});

// ------------------------------------------------------------------------------------------------- whole turns

test("declined: the answer comes, the turn is counted, and no conversation content leaves the page", async () => {
  resetChoices();
  setContentConsent(false);
  const { app, requests } = await makeApp();
  assert.equal(await app.send("桂枝汤主治什么？"), true);
  await finished(app);
  assert.match(app.state.messages.find((m) => m.role === "assistant").content, /太阳中风/);
  assert.deepEqual(records(requests), []);
  const [end] = events(requests, "turn_end");
  assert.deepEqual([end.status, end.provider, end.model, end.recorded, end.excluded, end.first, end.calls], ["completed", "tao", "Tao-S1", false, "no_consent", true, 1]);
  assert.equal(events(requests, "turn_start").length, 1);
  assert.doesNotMatch(JSON.stringify(requests), /桂枝汤|太阳中风/);
});

test("agreed: one record per turn with question, answer, model, timings and tokens; the second turn is numbered 2", async () => {
  resetChoices();
  setContentConsent(true);
  const { app, requests } = await makeApp();
  await app.send("桂枝汤主治什么？");
  await finished(app);
  await app.send("麻黄汤呢？联系我 a.b@example.org");
  await finished(app);
  const recs = records(requests);
  assert.equal(recs.length, 2);
  const [r1, r2] = recs;
  assert.deepEqual([r1.v, r1.consent, r1.turn, r1.status, r1.provider, r1.model, r1.question], [1, CONSENT_VERSION, 1, "completed", "tao", "Tao-S1", "桂枝汤主治什么？"]);
  assert.match(r1.answer, /太阳中风/);
  assert.deepEqual(r1.usage, { input: 120, output: 30 });
  assert.ok(r1.ms >= 0 && r1.started && r1.finished);
  assert.match(r1.owner, /^[0-9a-f]{64}$/);
  assert.equal(r2.turn, 2);
  assert.equal(r2.question, "麻黄汤呢？联系我 [redacted:email]");
  assert.equal(r2.conversation, r1.conversation);
  assert.deepEqual(events(requests, "turn_end").map((e) => [e.recorded, e.first]), [[true, true], [true, false]]);
  // nothing that identifies the browser or the visitor rides along
  assert.doesNotMatch(JSON.stringify(recs), /localStorage|tcmstudio\.consent|"key"/);
});

test("agreed, but never recorded: an uploaded file, a patient's case — and the conversation stays out after it", async () => {
  resetChoices();
  setContentConsent(true);
  const { app, requests } = await makeApp();
  const file = new window.Blob(["患者，女，32岁"], { type: "text/plain" });
  file.name = "case.txt";
  await app.send("请读一下附件", { files: [file] });
  await finished(app);
  await app.send("桂枝汤主治什么？"); // the same conversation: still out
  await finished(app);
  assert.deepEqual(records(requests), []);
  assert.deepEqual(events(requests, "turn_end").map((e) => e.excluded), ["uploaded_file", "uploaded_file"]);

  const second = await makeApp();
  await second.app.send("患者，男，60岁，咳嗽三天，舌红苔黄");
  await finished(second.app);
  assert.deepEqual(records(second.requests), []);
  assert.equal(events(second.requests, "turn_end")[0].excluded, "sensitive_text");
});

test("a project file the model was given keeps the conversation out, even once the file is removed", async () => {
  resetChoices();
  setContentConsent(true);
  // the model repeats what it was given: the project's note in the first turn, its own earlier answer in the second
  const { app, requests, project } = await makeApp({ reply: (messages) => (/150\/95/.test(JSON.stringify(messages.slice(1))) ? "再说一遍：血压 150/95。" : "笔记：血压 150/95。") });
  const note = new window.Blob(["我的私人笔记：血压 150/95，夜寐不安。"], { type: "text/plain" });
  const f = await app.store.files.add(project.id, note, { name: "notes.txt", source: "upload" });
  await app.send("总结一下项目里的笔记");
  await finished(app);
  await app.store.files.remove(f.id); // the visitor removes it from the project
  await app.send("把你上面说的再说一遍");
  await finished(app);
  assert.deepEqual(records(requests), []);
  assert.deepEqual(events(requests, "turn_end").map((e) => [e.recorded, e.excluded]), [[false, "project_files"], [false, "project_files"]]);
  const conv = await app.store.conversations.get(app.state.conversation.id);
  assert.equal(conv.recordExcluded, "project_files");
  // a new conversation in the same project, now without files, may be recorded
  app.state.conversation = null;
  await app.send("桂枝汤主治什么？");
  await finished(app);
  assert.equal(records(requests).length, 1);
  assert.equal(records(requests)[0].question, "桂枝汤主治什么？");
});

test("agreeing while an answer streams does not reach back to the question asked before", async () => {
  resetChoices();
  setContentConsent(false); // declined
  const { app, requests } = await makeApp({ slowMs: 150 });
  const sent = app.send("桂枝汤主治什么？");
  await delay(60); // the answer is under way...
  setContentConsent(true); // ...when the visitor turns records on (Settings → Privacy, another tab)
  await sent;
  await finished(app);
  assert.deepEqual(records(requests), []);
  assert.deepEqual(events(requests, "turn_end").map((e) => e.excluded), ["no_consent"]);
  await app.send("麻黄汤呢？"); // asked after agreeing: recorded
  await finished(app);
  assert.deepEqual(records(requests).map((r) => r.question), ["麻黄汤呢？"]);
});

test("the workflow funnel joins up: a governed skill started, submitted and completed under one name; a repeated call is no new run", async () => {
  resetChoices();
  setContentConsent(false);
  const doc = testCatalogDoc();
  doc.entries.push({ id: "skill.scrna-cell-atlas", kind: "skill", title: { zh: "单细胞图谱", en: "Cell atlas" }, summary: "x", category: "omics",
    parameters: { type: "object", properties: { source: { type: "string" } }, required: ["source"] }, example: null, exec: ["browser", "runner"],
    network: false, confirm: false, job: false, gpu: false, heavy: [], tags: [] });
  const answer = (tool, args) => (tool === "call_tool" && args?.tool === "skill.scrna-cell-atlas"
    ? { ...envelopeFor("skill.scrna-cell-atlas"), via: "skill.scrna-cell-atlas", status: "job_submitted", governance: { ...envelopeFor("x").governance, kind: "job" }, job: { id: "job-1", kind: "skill.run", state: "queued" } }
    : envelopeFor(tool));
  const app = new App();
  app.store = await openStore({ indexedDB: new IDBFactory() });
  app.catalog = new Catalog(doc);
  app.runtimes = { browser: fakeRuntime("browser", { answer }), runner: fakeRuntime("runner", { status: "offline" }) };
  app.toolRouter = new ToolRouter({ catalog: app.catalog, runtimes: app.runtimes, settings: { compute: "browser" }, approvals: memoryApprovals() });
  const provider = { ...activeProvider({ provider: "tao" }), fetch: undefined };
  app.provider = () => provider;
  app.llm = { openai: scriptedLLM([
    openaiStep({ calls: [
      { id: "c1", name: "call_tool", args: { tool: "skill.scrna-cell-atlas", arguments: { source: "pbmc.h5ad" } } },
      { id: "c2", name: "tcm_herb", args: { name: "甘草" } },
    ] }),
    openaiStep({ calls: [{ id: "c3", name: "tcm_herb", args: { name: "甘草" } }] }), // the same lookup again: answered from the first
    openaiStep({ text: "已提交，完成后可查看结果。" }),
  ]) };
  app.router = { navigate: (route) => { app.state.route = route; } };
  const t = telemetry();
  app.telemetry = t.tel;
  const project = await app.store.projects.create({ name: "组学" });
  app.state.project = project;
  app.state.projects = [project];
  await app.send("给我的单细胞数据做细胞注释");
  await finished(app);
  app.emit("job", { id: "job-1", kind: "skill.run", state: "succeeded", params: { skill_id: "scrna-cell-atlas" } });
  await delay(30);
  await app.telemetry.flush();
  const flows = events(t.requests, "workflow").map((e) => `${e.kind}:${e.name}:${e.phase}`);
  assert.deepEqual(flows, ["skill:skill.scrna-cell-atlas:started", "skill:skill.scrna-cell-atlas:submitted", "skill:skill.scrna-cell-atlas:completed"]);
  const runs = events(t.requests, "tool_end").map((e) => `${e.tool}:${e.kind}:${e.status}`);
  assert.deepEqual(runs.sort(), ["native.tcm_herb:native:succeeded", "skill.scrna-cell-atlas:skill:job_submitted"].sort(), "the repeated lookup is no second run");
  assert.equal(events(t.requests, "turn_end")[0].tool_failures, 0);
});

test("the telemetry failing changes nothing in the conversation: the answer is stored and shown, the record waits", async () => {
  resetChoices();
  setContentConsent(true);
  const { app, requests } = await makeApp({ fail: true });
  assert.equal(await app.send("桂枝汤主治什么？"), true);
  await finished(app);
  const stored = (await app.store.messages.list(app.state.conversation.id)).filter((m) => m.role === "assistant");
  assert.equal(stored.length, 1);
  assert.match(stored[0].content, /太阳中风/);
  assert.ok(records(requests).length >= 1, "it was tried");
  assert.equal(JSON.parse(localStorage.getItem("tcmstudio.telemetry.records")).length, 1, "and kept for the next try");
  // deleting the conversation drops the record that was never sent
  await app.deleteConversation(app.state.conversation.id);
  assert.equal(localStorage.getItem("tcmstudio.telemetry.records"), null);
});

test("the notice asks before the first question when the site takes records, and not again once answered", async () => {
  resetChoices();
  const { app, requests } = await makeApp();
  const sent = app.send("桂枝汤主治什么？");
  await delay(20);
  const dlg = [...document.querySelectorAll("dialog")].at(-1);
  assert.equal(dlg?.querySelector(".dialog__title")?.textContent, "对话记录说明");
  dlg.querySelector('[data-consent="decline"]').dispatchEvent(new window.Event("click"));
  assert.equal(await sent, true);
  await finished(app);
  const before = document.querySelectorAll("dialog.dialog--consent").length;
  await app.send("麻黄汤呢？");
  await finished(app);
  assert.equal(document.querySelectorAll("dialog.dialog--consent").length, before, "asked once");
  assert.deepEqual(records(requests), []);
  assert.deepEqual(events(requests, "consent").map((e) => e.decision), ["declined"]);
});
