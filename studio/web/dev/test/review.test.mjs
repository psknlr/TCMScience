// Tests for the web-ui review fixes: the draft of a running turn is saved while it streams (tool results included,
// no duplicates at the end), a deleted conversation's turn writes nothing back, attachments are stored before a
// conversation exists, an IME's Esc never stops a turn, skip links never route, refusals and limits of a succeeded
// result are shown, a person's decline is not a kernel refusal, unmet release states read as unmet, the relay's
// state decides the onboarding heading, job logs survive a reconnect, and the strings tell the truth about files.
// Run: node --test web/dev/test/*.test.mjs (from studio/).
import { window } from "./setup.mjs";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { IDBFactory } from "fake-indexeddb";

import { Catalog } from "../../js/core/catalog.js";
import { releaseOf } from "../../js/core/governance.js";
import { setLang } from "../../js/core/i18n.js";
import { activeProvider } from "../../js/core/providers.js";
import { ToolRouter } from "../../js/core/router.js";
import { openStore } from "../../js/core/store.js";
import { App, applyEvent, liveToolMessages, skipTo, toolMessageId } from "../../js/ui/app.js";
import { mergeLogs, viewOfStored } from "../../js/ui/conversation.js";
import { guessLang, kernelText, throttle } from "../../js/ui/dom.js";
import { inlinedInPrompt } from "../../js/ui/files.js";
import { artifactCard, citationHoverCard, limitationsBlock, releaseChecklist, unmetLabel } from "../../js/ui/governance.js";
import { inlineLiterals } from "../../js/ui/pages/catalog.js";
import { parseRoute } from "../../js/ui/router.js";
import { EN, registerUiStrings, ZH } from "../../js/ui/strings.js";
import { thinkingBlock, toolCallCard } from "../../js/ui/toolcards.js";
import { envelopeFor, fakeRuntime, memoryApprovals, openaiStep, scriptedLLM, testCatalogDoc } from "../../test/fixtures/fakes.mjs";

registerUiStrings();
setLang("zh");
const here = path.dirname(fileURLToPath(import.meta.url));
const web = path.resolve(here, "../..");
const fixture = (n) => JSON.parse(fs.readFileSync(path.join(web, "dev/fixtures", `${n}.json`), "utf8"));
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ------------------------------------------------------------------------------------------------- the live turn

/** An App wired to an in-memory store, a scripted model and a fake browser runtime (no boot, no DOM views). */
async function testApp(steps) {
  const app = new App();
  app.store = await openStore({ indexedDB: new IDBFactory() });
  app.catalog = new Catalog(testCatalogDoc());
  const runtimes = { browser: fakeRuntime("browser"), runner: fakeRuntime("runner", { status: "offline" }) };
  app.runtimes = runtimes;
  app.toolRouter = new ToolRouter({ catalog: app.catalog, runtimes, settings: { compute: "browser" }, approvals: memoryApprovals() });
  const provider = { ...activeProvider({ provider: "deepseek", keys: { deepseek: "sk" } }), fetch: undefined };
  app.provider = () => provider;
  app.llm = { openai: scriptedLLM(steps) };
  app.router = { navigate() {} };
  app.state.settings = { ...app.state.settings, thinking: false };
  const project = await app.store.projects.create({ name: "测试" });
  app.state.project = project;
  app.state.projects = [project];
  return app;
}

/** A model step that streams `n` text deltas `ms` apart (a long answer). */
function slowText(n, ms) {
  const evs = [];
  for (let i = 0; i < n; i++) evs.push({ wait: () => sleep(ms) }, { type: "text", delta: `第${i}句。` });
  evs.push({ type: "done", finish_reason: "stop", wire: { role: "assistant", content: "…" } });
  return evs;
}

async function turnEnded(app, timeout = 15000) {
  const t0 = Date.now();
  while (app.state.turn) {
    if (Date.now() - t0 > timeout) throw new Error("the turn did not end");
    await sleep(20);
  }
}

test("throttle runs during a steady stream of calls (a debounce never would) and flushes what is pending", async () => {
  const runs = [];
  const th = throttle((x) => runs.push(x), 60);
  for (let i = 0; i < 20; i++) { th(i); await sleep(10); }
  assert.ok(runs.length >= 3, `ran ${runs.length} times in 200 ms of calls`);
  assert.equal(runs[0], 0, "the first call runs at once");
  th(99);
  th.flush();
  assert.equal(runs.at(-1), 99);
  const before = runs.length;
  th.flush();
  assert.equal(runs.length, before, "nothing pending, nothing run");
});

test("the draft of a streaming turn is saved while it streams, tool results included; the end leaves one copy", async () => {
  const app = await testApp([
    openaiStep({ calls: [{ id: "call-1", name: "tcm_herb", args: { name: "甘草" } }] }),
    slowText(45, 50), // ~2.3 s of text, never a pause long enough for a 1.5 s debounce
  ]);
  assert.equal(await app.send("甘草的性味？"), true);
  const conv = app.state.conversation;
  await sleep(1900);
  assert.ok(app.state.turn, "still streaming");
  const draftId = app.state.turn.live.id;
  const mid = await app.store.messages.list(conv.id);
  const draft = mid.find((m) => m.id === draftId);
  assert.ok(draft?.draft, "a draft assistant record exists mid-stream");
  assert.match(draft.content, /第\d+句/, "with the text said so far");
  const tool = mid.find((m) => m.role === "tool");
  assert.ok(tool, "the tool result is saved before the turn ends");
  assert.equal(tool.id, toolMessageId(draftId, "call-1"));
  assert.equal(tool.envelope.status, "succeeded");
  assert.equal((await app.store.conversations.get(conv.id)).leafId, draftId, "a reload shows the draft");
  await turnEnded(app);
  const after = await app.store.messages.list(conv.id);
  const tools = after.filter((m) => m.role === "tool");
  assert.equal(tools.length, 1, "the final save replaced the draft's tool record");
  assert.equal(tools[0].id, toolMessageId(draftId, "call-1"));
  assert.ok(!tools[0].draft);
  const final = after.find((m) => m.id === draftId);
  assert.ok(!final.draft);
  assert.match(final.content, /第44句/);
});

test("a draft left behind by a reload reads as interrupted, its unanswered calls as interrupted (not cancelled)", () => {
  const m = {
    id: "a1", role: "assistant", draft: true, status: "stopped", content: "部分回答", segments: [{ type: "tools", step: 1, callIds: ["c1", "c2"] }, { type: "text", step: 2, text: "部分回答" }],
    toolCalls: [{ id: "c1", name: "tcm_herb", args: {} }, { id: "c2", name: "run_pipeline", args: {} }],
  };
  const app = { toolMessagesOf: () => [{ role: "tool", toolCallId: "c1", envelope: envelopeFor("tcm_herb") }] };
  const vm = viewOfStored(app, m);
  assert.equal(vm.status, "interrupted");
  assert.equal(vm.tools.get("c1").call.status, "succeeded");
  assert.equal(vm.tools.get("c2").call.status, "interrupted");
  const card = toolCallCard(vm.tools.get("c2").call, null);
  assert.equal(card.querySelector(".tool-card__status").getAttribute("aria-label"), "已中断");
  assert.match(card.textContent, /是否完成，这里无法得知/);
  const stopped = viewOfStored(app, { ...m, draft: false });
  assert.equal(stopped.status, "stopped");
  assert.equal(stopped.tools.get("c2").call.status, "cancelled");
});

test("live tool records carry the envelope, the call and a fixed id", () => {
  const live = { id: "d1", segments: [], tools: new Map(), pending: new Map(), thinkingMs: {}, thinkingStart: {}, status: "streaming" };
  applyEvent(live, { type: "model.start", step: 1 });
  applyEvent(live, { type: "tool.start", step: 1, callId: "x", name: "tcm_herb", args: { name: "甘草" }, where: "browser" });
  assert.deepEqual(liveToolMessages(live, "c"), [], "no record before the result");
  applyEvent(live, { type: "tool.end", step: 1, callId: "x", envelope: envelopeFor("tcm_herb") });
  const [rec] = liveToolMessages(live, "c");
  assert.equal(rec.id, "d1:x");
  assert.equal(rec.parentId, "d1");
  assert.equal(rec.toolCallId, "x");
  assert.deepEqual(rec.args, { name: "甘草" });
  assert.equal(rec.envelope.tool, "tcm_herb");
});

test("deleting a conversation mid-turn stops the turn and nothing of it is written back", async () => {
  const app = await testApp([
    openaiStep({ calls: [{ id: "call-1", name: "tcm_herb", args: { name: "甘草" } }] }),
    slowText(80, 40),
  ]);
  await app.send("甘草的性味？");
  const conv = app.state.conversation;
  await sleep(600);
  assert.ok(app.state.turn);
  const toasts = document.getElementById("toasts");
  const before = toasts.textContent;
  await app.deleteConversation(conv.id);
  await turnEnded(app);
  await sleep(50);
  assert.deepEqual(await app.store.messages.list(conv.id), [], "no orphan assistant or tool records (tool args included)");
  assert.ok(!(await app.store.conversations.get(conv.id)));
  assert.ok(!/未能保存/.test(toasts.textContent.slice(before.length)), "no 'could not save' toast");
});

test("deleting the project of a running turn discards the turn too", async () => {
  const app = await testApp([slowText(80, 40)]);
  await app.send("长回答");
  const conv = app.state.conversation;
  const pid = app.state.project.id;
  await sleep(300);
  await app.deleteProject(pid);
  await turnEnded(app);
  await sleep(50);
  assert.deepEqual(await app.store.messages.list(conv.id), []);
});

test("attachments are stored before a conversation is made: a failure sends nothing and leaves no empty conversation", async () => {
  const app = await testApp([openaiStep({ text: "ok" })]);
  const add = app.store.files.add;
  app.store.files.add = async () => { throw new Error("QuotaExceededError"); };
  const ok = await app.send("看附件", { files: [{ file: new Blob(["x"], { type: "text/plain" }), sha256: "ab" }] });
  assert.equal(ok, false);
  assert.equal(app.state.conversation, null);
  assert.deepEqual(await app.store.conversations.list(app.state.project.id), []);
  assert.match(document.getElementById("toasts").textContent, /附件没有存下，消息未发送/);
  // a composer attachment ({file, sha256}) is stored as a file, and the composer's hash goes along
  let opts = null;
  app.store.files.add = async (pid, blob, o) => { opts = o; return add.call(app.store.files, pid, blob, o); };
  const file = Object.assign(new Blob(["患者笔记"], { type: "text/plain" }), { name: "note.txt" });
  assert.equal(await app.send("看附件", { files: [{ file, sha256: "f".repeat(64) }] }), true);
  assert.equal(opts.sha256, "f".repeat(64));
  await turnEnded(app);
});

test("Esc that cancels an IME candidate does not stop the running answer", () => {
  const app = new App();
  let stopped = 0;
  app.state.turn = { abort: { abort: () => { stopped++; } } };
  app.stop = () => { stopped++; return true; };
  app.handleKey(new KeyboardEvent("keydown", { key: "Escape", isComposing: true }));
  app.handleKey(new KeyboardEvent("keydown", { key: "Escape", keyCode: 229 }));
  assert.equal(stopped, 0);
  app.handleKey(new KeyboardEvent("keydown", { key: "Escape" }));
  assert.equal(stopped, 1, "a plain Esc still stops");
});

test("skip links are anchors, not routes", () => {
  assert.equal(parseRoute("#composer-input").name, "home");
  assert.equal(parseRoute("#composer-input").anchor, "composer-input");
  assert.equal(parseRoute("#thread").anchor, "thread");
  assert.equal(parseRoute("#/settings/general").anchor, undefined);
  assert.equal(parseRoute("#/x/y").name, "notfound");
  const html = fs.readFileSync(path.join(web, "index.html"), "utf8");
  for (const m of html.matchAll(/class="skip-link" href="#([^"]+)"/g)) assert.ok(!m[1].startsWith("/"), "a skip link targets an element");
  // without its target (a settings page), the skip link falls back to the main region
  const main = document.createElement("main");
  main.id = "main";
  document.body.append(main);
  assert.equal(skipTo("#composer-input"), main);
  main.remove();
});

test("thinking under a second has no number; the clock starts at the step, not at the first reasoning token", () => {
  assert.equal(thinkingBlock({ text: "x", durationMs: 0 }).querySelector(".think__label").textContent, "思考");
  assert.equal(thinkingBlock({ text: "x", durationMs: 999 }).querySelector(".think__label").textContent, "思考");
  assert.match(thinkingBlock({ text: "x", durationMs: 8000 }).querySelector(".think__label").textContent, /思考 · 8\.0 秒/);
  const live = { segments: [], tools: new Map(), pending: new Map(), thinkingMs: {}, thinkingStart: {}, status: "streaming" };
  applyEvent(live, { type: "model.start", step: 1 });
  const started = live.stepStart[1];
  applyEvent(live, { type: "reasoning", step: 1, delta: "一次到达的思考" });
  assert.equal(live.thinkingStart[1], started);
});

// ------------------------------------------------------------------------------------------------- governance shown

test("a succeeded candidate run shows UNPINNED, a candidate chip, and no attested-looking audit head", () => {
  const env = fixture("envelope_unpinned");
  assert.equal(env.status, "succeeded");
  const compact = artifactCard(env, { compact: true });
  assert.match(compact.textContent, /UNPINNED/);
  assert.match(compact.textContent, /候选 · 未锁定，不会发布/);
  assert.match(compact.textContent, /未满足：/);
  assert.ok(!/○?\s*执行已证明/.test(compact.querySelector(".artifact__why").textContent), "unmet states are not named by their met label");
  assert.match(compact.querySelector(".artifact__why").textContent, /执行未证明/);
  const full = artifactCard(env);
  assert.match(full.textContent, /未经审计链证明/);
  assert.match(full.textContent, /Runner 审计链头（不是产物的证明）/);
  const card = toolCallCard({ id: "1", name: "call_tool", args: { tool: "skill.draft-tcm-prescription" } }, env, { open: true });
  assert.match(card.textContent, /内核的拒绝 1 条/);
  assert.match(card.textContent, /UNPINNED/);
});

test("the release checklist names unmet states as unmet, release_authorized's reasons included", () => {
  const rel = releaseOf(fixture("envelope_unpinned"));
  const list = releaseChecklist(rel);
  const auth = [...list.querySelectorAll(".release__row")].find((r) => r.textContent.includes("release_authorized"));
  assert.match(auth.textContent, /未准予发布/);
  assert.match(auth.textContent, /执行未证明/);
  assert.ok(!auth.textContent.includes("执行已证明"));
  assert.equal(unmetLabel("outputs_verified"), "输出未核验");
});

test("a released artifact says its verdict once: no 草稿 chip and no verdict tail beside the pill", () => {
  const card = artifactCard(fixture("envelope_safety"), { compact: true });
  assert.ok(!/草稿/.test(card.querySelector(".artifact__badges").textContent));
  assert.equal(card.querySelector(".artifact__sub").textContent, "发布状态 6/6");
  assert.equal(card.querySelectorAll(".verdict").length, 1);
  const full = artifactCard(fixture("envelope_safety"));
  assert.match(full.textContent, /产物状态草稿（工作流状态，未经人工验证）/);
  // the thread's tool card leaves the verdict to the artifact card that follows it
  const tc = toolCallCard({ id: "1", name: "tcm_safety_report", args: {} }, fixture("envelope_safety"), { artifactFollows: true });
  assert.ok(!tc.querySelector(".verdict"));
});

test("limits of results without an artifact are shown; a clinic result is a draft for a practitioner, not a ✓", () => {
  const clinic = fixture("envelope_clinic");
  const block = limitationsBlock(clinic.governance.limitations);
  assert.match(block.textContent, /licensed TCM practitioner/);
  assert.equal(block.querySelectorAll("li").length, 2);
  assert.match(block.textContent, /另有 \d+ 条/);
  assert.equal(block.querySelector("li").getAttribute("lang"), "en", "kernel English is marked as English");
  assert.match(block.textContent, /英文原文/);
  const card = toolCallCard({ id: "1", name: "clinic_assess", args: {} }, clinic);
  assert.match(card.textContent, /草案 · 待执业医师审核签署/);
  assert.ok(!card.querySelector(".tool-card__status svg")?.outerHTML.includes("M20 6 9 17"), "not the plain check");
  assert.equal(card.querySelector(".tool-card__status").getAttribute("aria-label"), "草案 · 待执业医师审核签署");
});

test("kernel text carries its language; guessLang weighs Chinese against Latin words", () => {
  assert.equal(guessLang("Can 甘草 be used with 甘遂?"), "en");
  assert.equal(guessLang("TNF 和 IL6 在葛根芩连汤中的作用"), "zh-Hans");
  assert.equal(guessLang("甘草的性味？"), "zh-Hans");
  assert.equal(guessLang(""), "");
  assert.equal(kernelText("p", "ABSENCE OF A RECORD IS NOT EVIDENCE OF SAFETY").getAttribute("lang"), "en");
  const hc = citationHoverCard({ id: "E1", label: "x", url: "https://example.org" }, { family: "tradition", content_hash: "a".repeat(64) }, { interactive: false });
  assert.equal(hc.querySelectorAll("a, button").length, 0, "a hover card opened by keyboard focus holds no controls");
});

test("catalog summaries render ``literals`` as code, not raw markup", () => {
  const box = document.createElement("p");
  box.append(...inlineLiterals("Translate; ``X`` for an ambiguous codon, see ``min_length_aa``."));
  assert.equal(box.textContent, "Translate; X for an ambiguous codon, see min_length_aa.");
  assert.equal(box.querySelectorAll("code").length, 2);
});

// ------------------------------------------------------------------------------------------------- strings

test("the privacy strings say that small text files go to the model", () => {
  for (const k of ["ui.composer.drop_note", "ui.knowledge.privacy", "ui.files.inputs_note", "ui.general.privacy", "ui.about.privacy_text"]) {
    assert.match(ZH[k], /8 KB/, `zh ${k}`);
    assert.match(ZH[k], /模型/, `zh ${k}`);
    assert.match(EN[k], /8 KB/, `en ${k}`);
    assert.match(EN[k], /model/, `en ${k}`);
    assert.ok(!/否则不会离开这台设备。$/.test(ZH[k]), `zh ${k} still promises the file never leaves`);
  }
  assert.ok(inlinedInPrompt({ name: "patient-notes.txt", type: "text/plain", size: 600 }));
  assert.ok(!inlinedInPrompt({ name: "reads.fastq", type: "", size: 9 * 1024 }));
  assert.ok(!inlinedInPrompt({ name: "photo.png", type: "image/png", size: 100 }));
});

test("example copy matches what the seed run returns; the WebGPU line does not contradict itself", () => {
  assert.ok(!/主张停在机制假说/.test(ZH["ui.examples.network.shows"]));
  assert.match(ZH["ui.examples.network.shows"], /不构建网络、不提出主张/);
  assert.match(EN["ui.examples.network.shows"], /no network is built and no claim is made/);
  assert.ok(!/可用，但没有可用/.test(ZH["ui.browser.webgpu_no_adapter"]));
});

test("onboarding's model heading follows the relay", async () => {
  const { openOnboarding } = await import("../../js/ui/onboarding.js");
  // jsdom-free: render the step through a fake dialog host
  const shown = [];
  for (const relay of [{ ok: true }, { ok: false, error: "503" }, { checking: true }]) {
    const app = { state: { relay, settings: {} }, setSetting() {}, on: () => () => {}, navigate() {} };
    const dlg = await renderStep(openOnboarding, app, 1);
    shown.push(dlg.querySelector(".onb__title").textContent);
  }
  assert.deepEqual(shown, ["模型：Tao-S1 已就绪", "模型：先接入一个模型", "模型：正在检查 Tao-S1"]);
});

/** Open the onboarding dialog in linkedom and press 下一步 `n` times; returns the dialog element. */
async function renderStep(openOnboarding, app, n) {
  const proto = window.HTMLElement.prototype;
  if (!proto.showModal) proto.showModal = function showModal() { this.setAttribute("open", ""); };
  if (!proto.close) proto.close = function close() { this.removeAttribute("open"); };
  openOnboarding(app);
  const dlg = [...document.querySelectorAll("dialog")].at(-1);
  for (let i = 0; i < n; i++) [...dlg.querySelectorAll("button")].find((b) => b.textContent === "下一步").dispatchEvent(new window.Event("click"));
  dlg.remove();
  return dlg;
}

// ------------------------------------------------------------------------------------------------- jobs

test("a reconnecting job stream does not duplicate its log lines", () => {
  const shown = ["a", "b", "c", "d"];
  assert.deepEqual(mergeLogs(shown, ["b", "c", "d"]), shown, "the backlog only repeats what is shown");
  assert.deepEqual(mergeLogs(shown, ["c", "d", "e", "f"]), [...shown, "e", "f"], "new lines after the overlap are kept");
  assert.deepEqual(mergeLogs([], ["x", "y"]), ["x", "y"], "the first connection's backlog is shown");
  assert.deepEqual(mergeLogs(["a"], ["q"]), ["a", "q"]);
});

// ------------------------------------------------------------------------------------------------- overlays

test("a toast with an action (Undo) stays at least 10 s; a plain one still closes after 3.2 s", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const { toast, ACTION_TOAST_MS } = await import("../../js/ui/overlay.js");
  assert.ok(ACTION_TOAST_MS >= 10000);
  const region = document.getElementById("toasts");
  toast("已归档", { action: { label: "撤销", onClick() {} } });
  const withAction = region.lastElementChild;
  assert.ok(withAction.querySelector(".toast__close"), "a dismiss button");
  toast("已复制");
  const plain = region.lastElementChild;
  t.mock.timers.tick(3300);
  assert.ok(plain.classList.contains("is-leaving"));
  assert.ok(!withAction.classList.contains("is-leaving"), "the Undo toast is still there after 3.3 s");
  t.mock.timers.tick(7000);
  assert.ok(withAction.classList.contains("is-leaving"));
  t.mock.timers.tick(300);
});

test("a popover asked to open at a missing or detached anchor opens nothing (no orphan panel, no throw)", async () => {
  const { openPopover } = await import("../../js/ui/overlay.js");
  const before = document.querySelectorAll(".popover").length;
  const a = openPopover(null, document.createElement("div"));
  const b = openPopover(document.createElement("button"), () => document.createElement("div"));
  assert.equal(a.el, null);
  assert.equal(b.el, null);
  a.close();
  b.place();
  assert.equal(document.querySelectorAll(".popover").length, before);
});

// ------------------------------------------------------------------------------------------------- css

/** [selector, declarations] of every plain rule in a stylesheet (comments dropped; at-rule bodies included). */
function cssRules(file) {
  const src = fs.readFileSync(path.join(web, "css", file), "utf8").replace(/\/\*[\s\S]*?\*\//g, "");
  return [...src.matchAll(/([^{}]+)\{([^{}]*)\}/g)].map((m) => [m[1].trim(), m[2]]);
}

test("--ink-4 never colours readable text, and metadata text is at least 12 px (WCAG 1.4.3, DESIGN §5)", () => {
  // decorative uses only: separators, list markers, icons, status dots, a disabled control, the hash copy glyph
  const decorative = /(sep|::marker|::before|__pin|> svg|__icon|status-dot|:disabled|is-empty|hash__copy|typing span|job__dot|json__sum|side-archived summary)/;
  const bad = [];
  for (const file of ["app.css", "components.css", "base.css"]) {
    for (const [sel, body] of cssRules(file)) if (/color:\s*var\(--ink-4\)/.test(body) && !/background(-color)?:\s*var\(--ink-4\)/.test(body) && !decorative.test(sel)) bad.push(`${file}: ${sel}`);
  }
  assert.deepEqual(bad, []);
  const rules = [...cssRules("app.css"), ...cssRules("components.css")];
  for (const sel of [".composer__foot", ".side-item__time", ".msg__time", ".side-group__title", ".example__meta code", ".catalog__cat-n", ".run__n", ".run__turn-n", ".ladder__ticks span"]) {
    const r = rules.find(([s]) => s === sel);
    assert.ok(r, `rule ${sel}`);
    const size = /font-size:\s*([^;]+)/.exec(r[1])?.[1].trim();
    assert.ok(size === "var(--text-xs)" || parseFloat(size) >= 12, `${sel} font-size ${size}`);
    assert.match(r[1], /color:\s*var\(--ink-3\)/, `${sel} colour`);
  }
});

test("the text tokens meet their contrast roles in both themes", () => {
  const tokens = fs.readFileSync(path.join(web, "css", "tokens.css"), "utf8");
  const lum = (hex) => {
    const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255).map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
    return 0.2126 * r + 0.7152 * g + 0.0722 * b;
  };
  const ratio = (a, b) => { const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p); return (x + 0.05) / (y + 0.05); };
  const light = (name) => new RegExp(`--${name}:\\s*(#[0-9a-f]{6})`, "i").exec(tokens)[1];
  const dark = (name) => new RegExp(`--${name}:\\s*(#[0-9a-f]{6})`, "i").exec(tokens.slice(tokens.indexOf("prefers-color-scheme: dark")))[1];
  for (const pick of [light, dark]) {
    for (const bg of ["bg", "surface", "surface-2", "bg-sidebar"]) assert.ok(ratio(pick("ink-3"), pick(bg)) >= 4.5, `ink-3 on ${bg}`);
    assert.ok(ratio(pick("control-border"), pick("surface")) >= 3, "control borders (the switch's off state) reach 3:1");
  }
});
