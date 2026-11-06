// The check before a question goes to a model outside this computer (ui/phidialog.js), in the real app under linkedom
// with a real store (fake IndexedDB) and the real OpenAI-format client, whose fetch is a spy on the request bodies: on
// the relay preset an ID number opens the dialog and the redacted send carries no 18-digit run; a local or loopback
// model is never asked about; a patient's case with nothing to redact has no redacted choice; inlined project files
// and attachments count; "as written" holds for the conversation and that model service only; a model switch (a new
// question or Retry) is checked again over what the new service has not seen; no research question of the corpus opens
// the dialog; the choice, never the text, is on the question and in turn_end.
import "./fixtures/setup.mjs";
import { window } from "../dev/test/setup.mjs";
import assert from "node:assert/strict";
import test from "node:test";
import { IDBFactory } from "fake-indexeddb";

import { setLang, t } from "../js/core/i18n.js";
import { buildSystemPrompt } from "../js/core/prompt.js";
import { activeProvider } from "../js/core/providers.js";
import { ToolRouter } from "../js/core/router.js";
import { openStore } from "../js/core/store.js";
import { PHI_CHOICES, normalizeEvent } from "../js/core/telemetry-contract.js";
import { App } from "../js/ui/app.js";
import { inlinedKnowledge, isLocalModel, phiCheck, unseenBy } from "../js/ui/phidialog.js";
import { EN, registerUiStrings, ZH } from "../js/ui/strings.js";

registerUiStrings();
const proto = window.HTMLElement.prototype;
if (!proto.showModal) proto.showModal = function showModal() { this.setAttribute("open", ""); };
if (!proto.close) proto.close = function close() { this.removeAttribute("open"); };
globalThis.matchMedia = () => ({ matches: true, addEventListener() {}, removeEventListener() {} }); // reduced motion: dialogs close at once

const ID_CASE = "患者，女，32岁，身份证号110101199003071234";
const CASE = "患者张三，男，45岁，主诉胃痛";
const delay = (ms) => new Promise((r) => setTimeout(r, ms));

// ------------------------------------------------------------------------------------------------- the model's spy

/** Every chat request any model service received: {url, body} (the body as sent, parsed). */
const requests = [];
globalThis.fetch = async (url, init = {}) => {
  const u = String(url);
  if (!/\/chat\/completions$/.test(u)) throw new TypeError(`no network in this test: ${u}`);
  requests.push({ url: u, body: JSON.parse(init.body) });
  return new Response(JSON.stringify({ choices: [{ index: 0, message: { role: "assistant", content: "好的。" }, finish_reason: "stop" }], usage: { prompt_tokens: 10, completion_tokens: 2 } }), { headers: { "content-type": "application/json" } });
};
const lastBody = () => JSON.stringify(requests.at(-1)?.body ?? null);

async function makeApp(settings = {}) {
  const app = new App();
  app.store = await openStore({ indexedDB: new IDBFactory() });
  app.state.settings = { ...app.state.settings, provider: "tao", ...settings };
  app.toolRouter = new ToolRouter({ catalog: null, runtimes: {}, settings: app.state.settings, store: app.store });
  app.router = { navigate: (route) => { app.state.route = route; } };
  const project = await app.store.projects.create({ name: "PHI", web: false });
  app.state.project = project;
  await app.refreshProjects();
  return { app, project };
}

const openDialog = () => [...document.querySelectorAll("dialog.dialog--phi")].at(-1) || null;
const click = (el) => el.dispatchEvent(new window.Event("click"));
/** Press a choice of an open dialog ({dialog, sending} from race) and wait for what it lets go on. */
const choose = (r, c) => { click(r.dialog.querySelector(`[data-phi="${c}"]`)); return r.sending; };
const choices = (d) => [...d.querySelectorAll("[data-phi]")].map((b) => b.dataset.phi);
const section = (d, c) => d.querySelector(`.phi__choice[data-choice="${c}"]`);

/** Start `start()` and resolve {sent} once it settles, or {dialog, sending} as soon as the check opens its dialog. */
async function race(start) {
  let settled = null;
  const sending = start();
  sending.then((sent) => { settled = { sent }; });
  for (let i = 0; i < 1000 && !settled; i++) {
    const d = openDialog();
    if (d) return { dialog: d, sending };
    await delay(2);
  }
  await sending;
  return settled;
}
const ask = (app, text, opts) => race(() => app.send(text, opts));
const retry = (app, assistantId) => race(() => app.regenerate(assistantId));

const tracked = (app, type) => app.telemetry.pending.filter((e) => e.type === type);
const turnEnds = (app) => tracked(app, "turn_end");

/** The turn that started has ended, and its usage event was tracked (after the answer is stored, never awaited). */
async function settle(app) {
  for (let i = 0; i < 1000 && (app.state.turn || turnEnds(app).length < tracked(app, "turn_start").length); i++) await delay(5);
}
const questions = (app) => app.state.messages.filter((m) => m.role === "user");

// ------------------------------------------------------------------------------------------------- the acceptance

test("an ID number on the relay preset opens the dialog; the redacted send's request body holds the marker and no 18-digit run", async () => {
  const { app } = await makeApp();
  const sent0 = requests.length;
  const r = await ask(app, ID_CASE);
  const { dialog } = r;
  assert.ok(dialog, "the dialog opens before anything is sent");
  assert.equal(requests.length, sent0, "nothing went to the model while the dialog is open");
  assert.equal(app.state.conversation, null, "nor was anything stored");
  // the choices (no local model is set up here), sending nothing focused first
  assert.deepEqual(choices(dialog), ["edit", "as_written", "redacted"]);
  assert.match(dialog.textContent, /Tao-S1 不在你的电脑上运行/);
  assert.match(dialog.querySelector(".phi__where").textContent, /这条消息/);
  // the exact text each choice sends, and what redaction does not do
  assert.equal(section(dialog, "redacted").querySelector("pre").textContent, "患者，女，32岁，身份证号[redacted:id_number]");
  assert.match(section(dialog, "redacted").textContent, /正文里的人名不会被隐去/);
  assert.match(section(dialog, "redacted").textContent, /11 位的数字（如 PubChem CID）可能被当作手机号隐去/);
  assert.equal(section(dialog, "as_written").querySelector("pre").textContent, ID_CASE);
  assert.equal(await choose(r, "redacted"), true);
  await settle(app);
  assert.equal(requests.length, sent0 + 1);
  assert.match(requests.at(-1).url, /science\.impf\.ai\/v1\/chat\/completions$/);
  assert.match(lastBody(), /\[redacted:id_number\]/);
  assert.doesNotMatch(lastBody(), /\d{18}/);
  // the question is stored as it was sent, with the choice (never the text it replaced)
  const [q] = questions(app);
  assert.equal(q.content, "患者，女，32岁，身份证号[redacted:id_number]");
  assert.deepEqual(q.phi, { reason: "identifier", choice: "redacted" });
  const stored = JSON.stringify(await app.store.messages.list(app.state.conversation.id));
  assert.doesNotMatch(stored, /110101199003071234/);
  assert.deepEqual(turnEnds(app).map((e) => e.phi), ["redacted"]);
  // the redacted question still reads as a case, but Tao-S1 has it already: the next question is not held for it
  assert.deepEqual(await ask(app, "那用什么方？"), { sent: true });
  await settle(app);
  assert.deepEqual(turnEnds(app).map((e) => e.phi), ["redacted", undefined]);
});

test("a loopback model is never asked about: Ollama, a custom server on localhost; the text goes to it as written", async () => {
  for (const settings of [
    { provider: "ollama", models: { ollama: "qwen3:8b" } },
    { provider: "custom-loop", customProviders: [{ id: "custom-loop", label: "本机", format: "openai", base_url: "http://localhost:8080/v1", model: "m" }] },
  ]) {
    const { app } = await makeApp(settings);
    const r = await ask(app, ID_CASE);
    assert.deepEqual(r, { sent: true }, settings.provider);
    await settle(app);
    assert.match(requests.at(-1).url, /^http:\/\/(127\.0\.0\.1|localhost):\d+\/v1\/chat\/completions$/);
    assert.match(lastBody(), /110101199003071234/);
    assert.equal(questions(app)[0].phi, undefined);
    assert.equal(turnEnds(app)[0].phi, undefined);
  }
  // a custom endpoint elsewhere is a model outside this computer
  const { app } = await makeApp({ provider: "custom-far", customProviders: [{ id: "custom-far", label: "远程", format: "openai", base_url: "https://llm.example.org/v1", model: "m" }] });
  const r = await ask(app, ID_CASE);
  assert.ok(r.dialog);
  assert.match(r.dialog.textContent, /远程 · m 不在你的电脑上运行/);
  assert.equal(await choose(r, "edit"), false);
});

test("a patient's case with nothing to redact: the dialog without a redacted choice; going back sends and stores nothing", async () => {
  const { app } = await makeApp();
  const sent0 = requests.length;
  const r = await ask(app, CASE);
  assert.ok(r.dialog);
  assert.deepEqual(choices(r.dialog), ["edit", "as_written"]);
  assert.equal(section(r.dialog, "redacted"), null);
  assert.match(r.dialog.textContent, /读起来像一个具体病例/);
  assert.equal(r.dialog.querySelector("[data-phi=edit]"), r.dialog.querySelector(".dialog__foot button"), "going back is the first choice");
  assert.equal(await choose(r, "edit"), false, "the composer keeps the text");
  assert.equal(requests.length, sent0);
  assert.equal(app.state.conversation, null);
  assert.deepEqual(await app.store.conversations.list(), []);
});

test("a local model the person set up is offered: the text goes there as written, and the conversation stays on it", async () => {
  const { app } = await makeApp({ models: { ollama: "qwen3:8b" } });
  const r = await ask(app, CASE);
  assert.deepEqual(choices(r.dialog), ["edit", "local", "as_written"]);
  assert.match(section(r.dialog, "local").textContent, /只发给这台电脑上的 Ollama · qwen3:8b/);
  assert.equal(await choose(r, "local"), true);
  await settle(app);
  assert.match(requests.at(-1).url, /^http:\/\/127\.0\.0\.1:11434\//);
  assert.match(lastBody(), /患者张三/);
  assert.equal(app.state.conversation.provider, "ollama");
  assert.deepEqual(questions(app)[0].phi, { reason: "clinical_case", choice: "local" });
  assert.deepEqual(turnEnds(app).map((e) => [e.provider, e.phi]), [["ollama", "local"]]);
  // the next question goes to the local model: nothing to ask
  assert.deepEqual(await ask(app, "患者，男，60岁，咳嗽三天"), { sent: true });
  await settle(app);
});

test("as written holds for the conversation and that service: a switch to another service asks again, a new question or Retry", async () => {
  const { app } = await makeApp({ keys: { deepseek: "sk-test-0000000000000000" } });
  let r = await ask(app, ID_CASE);
  assert.equal(await choose(r, "as_written"), true);
  await settle(app);
  assert.match(lastBody(), /110101199003071234/);
  assert.deepEqual(app.state.conversation.phiAsWritten, ["tao"]);
  // the same conversation, the same service: not asked again, and the choice is still recorded
  assert.deepEqual(await ask(app, "患者，男，60岁，咳嗽三天"), { sent: true });
  await settle(app);
  assert.deepEqual(questions(app).map((q) => q.phi), [{ reason: "identifier", choice: "as_written" }, { reason: "clinical_case", choice: "as_written", remembered: true }]);
  assert.deepEqual(turnEnds(app).map((e) => e.phi), ["as_written", "as_written"]);

  // switched to the person's own DeepSeek: a harmless question, but the branch it sends was never sent there
  await app.chooseModel({ provider: "deepseek", model: "deepseek-chat" });
  const sent0 = requests.length;
  r = await ask(app, "黄芪的靶点");
  assert.ok(r.dialog, "the earlier questions are checked for the new service");
  assert.match(r.dialog.querySelector(".phi__where").textContent, /本对话中较早的 2 条消息（DeepSeek · deepseek-chat 还没有收到过）/);
  assert.deepEqual(choices(r.dialog), ["edit", "as_written"], "redacting the new question would not keep the earlier ones back");
  assert.equal(section(r.dialog, "as_written").querySelector("pre").textContent, "黄芪的靶点");
  assert.equal(await choose(r, "edit"), false);
  assert.equal(requests.length, sent0);

  // Retry of the last answer on DeepSeek (the relay's switch banner, then Retry): the same check, nothing to edit
  const last = app.state.messages.filter((m) => m.role === "assistant").at(-1);
  r = await retry(app, last.id);
  assert.ok(r.dialog, "Retry on another service asks too");
  assert.deepEqual(choices(r.dialog), ["edit", "as_written"]);
  assert.equal(r.dialog.querySelector('[data-phi="edit"]').textContent, "取消");
  assert.match(section(r.dialog, "as_written").textContent, /没有新消息/);
  await choose(r, "as_written");
  await settle(app);
  assert.equal(requests.length, sent0 + 1);
  assert.match(requests.at(-1).url, /api\.deepseek\.com/);
  assert.match(lastBody(), /110101199003071234/);
  assert.deepEqual(app.state.conversation.phiAsWritten, ["tao", "deepseek"]);
  assert.equal(turnEnds(app).at(-1).phi, "as_written");
  // remembered for DeepSeek now: neither a new question nor another Retry asks
  assert.deepEqual(await ask(app, "黄芪的靶点"), { sent: true });
  await settle(app);
  assert.deepEqual(await retry(app, app.state.messages.filter((m) => m.role === "assistant").at(-1).id), { sent: undefined });
  await settle(app);
});

test("Retry where the same service answered the question asks nothing, even of a question that still reads as a case", async () => {
  const { app } = await makeApp();
  const r = await ask(app, ID_CASE);
  await choose(r, "redacted");
  await settle(app);
  const sent0 = requests.length;
  assert.deepEqual(await retry(app, app.state.messages.find((m) => m.role === "assistant").id), { sent: undefined });
  await settle(app);
  assert.equal(requests.length, sent0 + 1);
  assert.doesNotMatch(lastBody(), /\d{18}/);
});

test("project files the prompt inlines count, and so do attachments about to join them; going back stores no attachment", async () => {
  const { app, project } = await makeApp();
  await app.store.files.add(project.id, new window.Blob(["门诊记录：身份证号 110101199003071234，舌红苔黄"], { type: "text/plain" }), { name: "intake.txt", source: "upload" });
  let r = await ask(app, "这份笔记里提到了哪些药？");
  assert.ok(r.dialog, "an ID number in an inlined project file opens the dialog");
  assert.match(r.dialog.querySelector(".phi__where").textContent, /项目文件 intake\.txt（随每条消息发给模型）/);
  assert.deepEqual(choices(r.dialog), ["edit", "as_written"], "redacting the question would not keep the file back");
  assert.match(section(r.dialog, "as_written").textContent, /项目文件 intake\.txt 也原样发送/);
  assert.equal(await choose(r, "edit"), false);

  const other = await makeApp();
  const file = new window.Blob(["患者，女，32岁"], { type: "text/plain" });
  file.name = "case.txt";
  r = await ask(other.app, "请读一下附件", { files: [file] });
  assert.ok(r.dialog, "an attachment the prompt will inline counts before it is stored");
  assert.match(r.dialog.querySelector(".phi__where").textContent, /case\.txt/);
  assert.equal(await choose(r, "edit"), false);
  assert.deepEqual(await other.app.store.files.list(other.project.id), [], "nothing was stored");
});

// ------------------------------------------------------------------------------------------------- no false positives

// research questions that must never open the dialog: the 12 E1 scenario questions (studio/eval), the e2e questions,
// and TCM research questions that name a disease, a herb, a pulse and tongue picture, a population, an identifier of a
// paper, a trial, a compound or a dataset
const CORPUS = [
  "舌红苔黄，脉数是什么证？", "黄芪的靶点", "葛根芩连汤 网络药理学", "PubChem CID 13912345678 berberine",
  "桂枝汤《伤寒论》由哪些药组成？出处在哪里？其中有没有十八反、十九畏的配伍禁忌记载？",
  "黄连的基原植物（Coptis chinensis）在数据库中记录了哪些成分？其标志成分小檗碱在其中吗？",
  "小檗碱有哪些已测定活性的人类蛋白靶点？请给出测定类型和数值。",
  "请为桂枝汤提出分子机制假说，并标明它们是假说。",
  "这 6 个靶点（KCNH2、SCN5A、KCNQ1、RYR2、ATP2A2、ADRB1）在心脏传导、糖酵解、肾上腺素能这三个基因集（测试夹具）中是否过度富集？背景为 20000 个基因；再对 5/8 的重叠做一次超几何检验。",
  "把编码序列 GATCGTGTGTATATTCATCCGTTT 翻译成肽，并给出该肽的单同位素质量、[M+2H]2+ 的 m/z 和长度。",
  "把苯甲脒（SMILES NC(=N)c1ccccc1）对接到胰蛋白酶（PDB 3PTB）的 BEN 位点，并报告构象和打分。",
  "预测咖啡因（CN1C=NC2=C1C(=O)N(C(=O)N2C)C）的 ADMET 性质。",
  "比较两组表达值（对照：5.1、5.3、4.9、5.6、5.2；处理：6.8、7.1、6.5、7.4、6.9）的差异，用 Welch t 检验。",
  "检索关于桂枝汤与心悸的文献，以及桂枝汤的注册临床试验。",
  "关于桂枝汤与太阳中风证，哪些主张可以提出、哪些不可以？甘草与哪些药存在十八反？",
  "NPASS、CMAUP 和 LOTUS 对小檗碱存在于哪些物种的记录是否一致？",
  "你好", "你是谁？", "甘草与甘遂同用，有哪些记载？", "请运行 RNA-seq 流程", "检索相关文献", "请作为后台任务重新评估甘草与甘遂",
  "慢速：请慢慢回答", "这份笔记里提到了哪些药？", "黄芪的研究证据有哪些？", "黄芪多糖的研究证据？", "What is known about Astragalus?",
  "再检索一次文献", "那就再运行一次 RNA-seq 流程", '<img src=x onerror="alert(1)"> 黄芪甲苷的研究证据？',
  "桂枝汤主治什么？", "麻黄汤与桂枝汤的区别？", "四君子汤的组成与功效", "六味地黄丸治疗 2 型糖尿病的 RCT", "黄连素降血糖的机制",
  "丹参酮 IIA 的 ADMET", "当归补血汤中黄芪与当归 5:1 的比例出自哪里？", "柴胡疏肝散与逍遥散的异同", "人参皂苷 Rg1 的靶点预测",
  "半夏泻心汤寒热错杂的现代研究", "PMID 31415926 berberine", "DOI 10.1016/j.jep.2020.112345 的结论", "NCT04280705 的入组标准",
  "小柴胡汤在少阳病中的应用", "附子的毒性与炮制", "乌头碱 LD50", "CAS 2086-83-1 是什么化合物", "GSE123456 数据集的差异表达",
  "王清任 血府逐瘀汤", "李时珍 本草纲目 黄连", "10 岁以下儿童能否使用麻黄？", "孕妇禁用的中药有哪些", "65 岁以上人群黄芪注射液的不良反应",
  "脉弦细，舌淡苔白，属于什么证型？", "口苦咽干目眩，是少阳病吗？", "糖尿病患者能吃黄芪吗？", "高血压患者服用天麻钩藤饮的证据",
  "Compare berberine and metformin in type 2 diabetes trials", "What are the targets of baicalin?", "network pharmacology of Gegen Qinlian decoction",
];

test(`no dialog on ${CORPUS.length} research questions: the E1 scenarios, the e2e questions and TCM research questions`, async () => {
  assert.ok(CORPUS.length >= 30 + 12);
  for (const q of CORPUS) assert.equal(phiCheck({ text: q }), null, q);
  // and through the app on the relay preset: each is sent at once (one conversation each)
  const { app } = await makeApp();
  for (const q of CORPUS) {
    app.state.conversation = null;
    app.state.messages = [];
    app.telemetry.pending = [];
    const r = await ask(app, q);
    assert.deepEqual(r, { sent: true }, q);
    await settle(app);
    assert.equal(questions(app)[0].content, q);
  }
});

// ------------------------------------------------------------------------------------------------- the parts

test("local means this computer: Tao-S1 never is (even served from localhost); a local preset or a loopback address is", () => {
  const tao = activeProvider({ provider: "tao", baseUrls: { tao: "http://127.0.0.1:8787/v1" } });
  assert.equal(isLocalModel(tao), false);
  assert.equal(isLocalModel(activeProvider({ provider: "tao" })), false);
  assert.equal(isLocalModel(activeProvider({ provider: "openai", keys: { openai: "k" } })), false);
  assert.equal(isLocalModel(activeProvider({ provider: "ollama" })), true);
  assert.equal(isLocalModel(activeProvider({ provider: "lmstudio" })), true);
  assert.equal(isLocalModel(activeProvider({ provider: "c", customProviders: [{ id: "c", base_url: "http://[::1]:9000/v1", model: "m" }] })), true);
  assert.equal(isLocalModel(activeProvider({ provider: "c", customProviders: [{ id: "c", base_url: "https://api.example.org/v1", model: "m" }] })), false);
});

test("the files checked are exactly the files the system prompt inlines (size, then the 64 kB budget)", () => {
  const big = "黄".repeat(3000); // 9 kB in UTF-8: listed, not inlined
  const knowledge = [
    { name: "a.txt", text: "笔记 A 身份证号110101199003071234" },
    { name: "big.txt", text: `${big}身份证号110101199003071234` },
    { name: "bin.dat" },
    ...Array.from({ length: 12 }, (_, i) => ({ name: `f${i}.txt`, text: `F${i}:${"x".repeat(7000)}` })), // 84 kB: the budget runs out
  ];
  const prompt = buildSystemPrompt({ lang: "zh", knowledge });
  const inlined = inlinedKnowledge(knowledge).map((f) => f.name);
  for (const f of knowledge) {
    const inPrompt = typeof f.text === "string" && prompt.includes(`### ${f.name}\n`);
    assert.equal(inlined.includes(f.name), inPrompt, f.name);
  }
  assert.ok(inlined.includes("a.txt") && !inlined.includes("big.txt") && !inlined.includes("f11.txt"));
  assert.deepEqual(phiCheck({ text: "黄芪", knowledge }).files, ["a.txt"]);
});

test("what a service has not seen: the questions after the last one it answered, in any branch", () => {
  const msgs = [
    { id: "u1", role: "user", content: "q1", parentId: null },
    { id: "a1", role: "assistant", provider: "ollama", parentId: "u1" },
    { id: "a1b", role: "assistant", provider: "tao", parentId: "u1" }, // a Retry on Tao-S1: it saw u1
    { id: "u2", role: "user", content: "q2", parentId: "a1" },
    { id: "a2", role: "assistant", provider: "ollama", parentId: "u2" },
  ];
  const path = msgs.filter((m) => ["u1", "a1", "u2", "a2"].includes(m.id));
  assert.deepEqual(unseenBy(msgs, path, "tao").map((m) => m.id), ["u2"]);
  assert.deepEqual(unseenBy(msgs, path, "ollama").map((m) => m.id), []);
  assert.deepEqual(unseenBy(msgs, path, "deepseek").map((m) => m.id), ["u1", "u2"]);
});

test("turn_end keeps the choice and nothing else: a choice outside the three, or any text, is dropped", () => {
  const base = { type: "turn_end", turn: "t1", status: "completed", provider: "tao", model: "Tao-S1" };
  for (const c of PHI_CHOICES) assert.equal(normalizeEvent({ ...base, phi: c }).phi, c);
  for (const bad of ["edit", ID_CASE, "", 1, { choice: "redacted" }]) assert.equal(normalizeEvent({ ...base, phi: bad }).phi, undefined, String(bad));
  assert.deepEqual(PHI_CHOICES, ["redacted", "as_written", "local"]);
});

test("every reason the check can give has its sentence in Chinese and in English", () => {
  for (const reason of ["identifier", "clinical_record", "clinical_case", "contact_with_clinical"]) {
    const k = `ui.phi.reason.${reason}`;
    assert.ok(ZH[k] && EN[k], k);
  }
  setLang("en");
  try {
    assert.equal(t("ui.phi.in_history", { n: 1, name: "DeepSeek" }), "1 earlier message of this conversation (DeepSeek has not received it)");
    assert.equal(t("ui.phi.in_history", { n: 2, name: "DeepSeek" }), "2 earlier messages of this conversation (DeepSeek has not received them)");
    assert.match(t("ui.phi.redacted_names"), /A name in running text stays/);
  } finally {
    setLang("zh");
  }
});
