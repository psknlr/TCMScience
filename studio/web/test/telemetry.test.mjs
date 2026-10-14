// The page's side of the telemetry: the consent notice's state, the contract both sides read, what is never recorded
// (sensitive.js, turnrecord.js exclusionOf), the record and the usage event of a turn, and the client — that it sends
// nothing a visitor did not allow, keeps what failed for later, survives a reload, and never throws into the app.
import { resetStorage } from "./fixtures/setup.mjs";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import {
  analyticsAllowed, browserOptOut, consentState, contentConsent, deletionKey, forgetConsent, ownerHash, setAnalytics, setContentConsent,
} from "../js/core/consent.js";
import { redactText, sensitiveReason } from "../js/core/sensitive.js";
import {
  CONSENT_VERSION, LIMITS, PROVIDER_MODELS, normalizeBatch, normalizeEvent, normalizeRecord, ownerOf,
} from "../js/core/telemetry-contract.js";
import { PRESETS, newCustomProvider } from "../js/core/providers.js";
import { OUTBOX_PREFIX, RECORDS_KEY, STALE_TURN_MS, TURNS_KEY, Telemetry, VISIT_IDLE_MS } from "../js/core/telemetry.js";
import { SKILL_FILES, buildRecord, citedIds, evidenceStats, exclusionOf, toolRuns, turnEndEvent, turnStatus } from "../js/core/turnrecord.js";

const here = path.dirname(fileURLToPath(import.meta.url));
const FIX = (name) => JSON.parse(fs.readFileSync(path.join(here, "fixtures", name), "utf8"));
const ORIGIN = "https://science.impf.ai";
const HEALTH = { ok: true, telemetry: { analytics: true, conversations: true, consent_version: CONSENT_VERSION, geo: "country", retention_days: { conversations: 90, events: 90, aggregates: 400, security: 30 } } };

/** A Telemetry with a fake network: every request recorded; `answer(url, init)` decides the response. */
function client({ answer, now, nav = {} } = {}) {
  const requests = [];
  const beacons = [];
  let t = now ?? Date.parse("2026-10-11T04:00:00Z");
  const timers = [];
  const tel = new Telemetry({
    origin: ORIGIN, now: () => t, nav,
    fetch: async (url, init) => {
      requests.push({ url, init, body: init?.body ? JSON.parse(init.body) : null });
      if (answer) return answer(url, init);
      return new Response(null, { status: url.endsWith("/conversations") ? 201 : 204 });
    },
    beacon: (url, body) => { beacons.push({ url, body: JSON.parse(body) }); return true; },
    setTimer: (fn) => { timers.push(fn); return timers.length; },
    clearTimer: () => {},
  });
  return { tel, requests, beacons, timers, tick: (ms) => { t += ms; }, at: () => t };
}

// ------------------------------------------------------------------------------------------------- consent

test("consent: off until agreed, versioned, a deletion key kept after withdrawal; analytics off by choice or by the browser", async () => {
  resetStorage();
  assert.equal(contentConsent(), null);
  assert.deepEqual({ ...consentState(), at: null }, { content: null, version: null, at: null, analytics: true, hasKey: false });
  setContentConsent(false);
  assert.equal(contentConsent(), false);
  assert.equal(deletionKey(), null); // declining makes no key
  setContentConsent(true);
  const key = deletionKey();
  assert.match(key, /^[A-Za-z0-9_-]{43}$/);
  assert.equal(await ownerHash(), await ownerOf(key));
  setContentConsent(false);
  assert.equal(deletionKey(), key, "withdrawing keeps the key, so what was recorded can still be deleted");
  // a notice of another version is not an answer to this one
  localStorage.setItem("tcmstudio.consent", JSON.stringify({ v: CONSENT_VERSION + 1, content: true, key }));
  assert.equal(contentConsent(), null);
  setAnalytics(false);
  assert.equal(analyticsAllowed({}), false);
  setAnalytics(true);
  assert.equal(analyticsAllowed({}), true);
  assert.equal(browserOptOut({ globalPrivacyControl: true }), true);
  assert.equal(browserOptOut({ doNotTrack: "1" }), true);
  assert.equal(analyticsAllowed({ doNotTrack: "1" }), false);
  forgetConsent();
  assert.equal(localStorage.getItem("tcmstudio.consent"), null);
});

// ------------------------------------------------------------------------------------------------- sensitive content

test("sensitive content: identifiers and patient cases keep a turn out; scientific questions do not; secrets are redacted", () => {
  const out = {
    identifier: ["身份证号 110101199003077777", "姓名：张三，男", "住院号: A12345", "MRN 778899", "出生日期：1980-02-03", "patient name: John Doe"],
    clinical_case: ["患者，女，32岁，月经不调三月", "女，45岁，咳嗽一周", "6岁患儿发热两天", "A 45-year-old male patient with cough", "患儿 3个月 腹泻"],
    clinical_record: ["主诉：咳嗽3天。现病史：……既往史：无", "Chief complaint: chest pain. History of present illness: ..."],
    contact_with_clinical: ["主诉头痛，请回电 13800138000"],
  };
  for (const [why, texts] of Object.entries(out)) for (const text of texts) assert.equal(sensitiveReason(text), why, text);
  for (const text of ["桂枝汤主治什么？", "舌红苔黄，脉数是什么证？", "甘草与甘遂同用，有哪些记载？", "TP53 的 GC 含量", "What does the 2020 Chinese Pharmacopoeia say about 黄芪?",
    "女性更年期常用方有哪些", "Which formula treats a patient's spleen deficiency in general?", "RNA-seq 流程", "数值 120 340 560 的均值", ""]) {
    assert.equal(sensitiveReason(text), null, text);
  }
  const r = redactText("key sk-proj-abcdefghijklmnopqrstu, Bearer abcdefgh1234ijklmnop, mail x@y.org, tel 13800138000, 010-12345678, https://u:p@h.org/x?token=abc, 192.168.0.1, 密码：hunter2pass");
  assert.equal(r.text, "key [redacted:api_key], Bearer [redacted:bearer], mail [redacted:email], tel [redacted:mobile], [redacted:phone], https://[redacted:url_credentials]@h.org/x?token=[redacted:url_secret], [redacted:ip_address], 密码：[redacted:secret]");
  assert.equal(r.hits, 9);
  assert.deepEqual(redactText("桂枝汤 3 剂，每剂 9 g"), { text: "桂枝汤 3 剂，每剂 9 g", hits: 0 });
  // typed with a Chinese input method's full-width forms, or with the groups a phone number is written in: still
  // found; what is kept has those digits and letters in ASCII, and the Chinese punctuation as written
  const wide = {
    "电话１３８００１３８０００": "电话[redacted:mobile]", "ａｂｃ＠ｅｘａｍｐｌｅ．ｃｏｍ": "[redacted:email]", "０１０-１２３４５６７８": "[redacted:phone]",
    "１９２．１６８．１．１": "[redacted:ip_address]", "138-0013-8000": "[redacted:mobile]", "+86 138 0013 8000": "[redacted:mobile]",
    "服务器是 10.0.0.1.": "服务器是 [redacted:ip_address].", "Authorization: Bearer abcdefghijklmnop": "Authorization: Bearer [redacted:bearer]",
    "Authorization: Basic dXNlcjpwYXNzd29yZA==": "Authorization: Basic [redacted:bearer]",
    "桂枝汤，主治：太阳中风（表虚证）？～１０％": "桂枝汤，主治：太阳中风（表虚证）？～10％", "ＴＰ５３ 的 ＧＣ 含量": "TP53 的 GC 含量",
    "电话：１３８　００１３　８０００": "电话：[redacted:mobile]", "　　首行缩进": "　　首行缩进",
  };
  for (const [text, kept] of Object.entries(wide)) assert.equal(redactText(text).text, kept, text);
  // prose is not a credential, nor a version number an address
  for (const text of ["a basic understanding of 本草", "Basic Understanding of herbs", "the bearer of the formula", "version 1.10.0.0.1 build"]) {
    assert.deepEqual(redactText(text), { text, hits: 0 }, text);
  }
  // "patient notes" is not a patient number: the label is a whole word
  for (const text of ["patient notes on 桂枝汤", "Patient identification in TCM practice", "hospital notes"]) assert.equal(sensitiveReason(text), null, text);
  for (const text of ["patient id: A12345", "patient no. 778899", "hospital number 55667"]) assert.equal(sensitiveReason(text), "identifier", text);
  assert.equal(sensitiveReason("身份证号 １１０１０１１９９００３０７７７７７"), "identifier"); // full-width digits
});

// ------------------------------------------------------------------------------------------------- the contract

test("contract: events keep only known fields within bounds; a route is a name, never an id", () => {
  assert.deepEqual(normalizeEvent({ type: "pageview", route: "conversation", tab: "privacy", ts: 5, convId: "secret", text: "我的问题" }),
    { type: "pageview", ts: 5, route: "conversation", tab: "privacy" });
  assert.equal(normalizeEvent({ type: "pageview", route: "/p/abc/c/def" }), null);
  assert.equal(normalizeEvent({ type: "turn_end", turn: "t1" }), null); // no status
  assert.equal(normalizeEvent({ type: "tool_end", call: "a:b", tool: "患者张三" }), null); // not a tool name
  assert.deepEqual(normalizeEvent({ type: "turn_end", turn: "t1", status: "completed", ms: -5, tokens_in: "12", steps: 900 }),
    { type: "turn_end", turn: "t1", status: "completed", ms: 0, tokens_in: 12, steps: 64 });
  assert.equal(normalizeEvent({ type: "error", category: "js_error", name: "TypeError", where: "js/ui/app.js:1", message: "secret" }).message, undefined);
  assert.equal(normalizeBatch({ v: 1, batch: "b", events: Array.from({ length: LIMITS.events + 1 }, () => ({ type: "pageview", route: "home" })) }), null);
});

test("contract: a provider by its preset's id or \"custom\", a model the preset lists or \"other\" — never an id or a path of the visitor's", async () => {
  // the page's presets and the contract's list are the same (a preset added later must be listed here too)
  assert.deepEqual(PROVIDER_MODELS, Object.fromEntries(PRESETS.map((p) => [p.id, p.models])));
  const mine = newCustomProvider("openai", 1); // custom-<the time it was added>: the same for this browser every day
  assert.match(mine.id, /^custom-/);
  const ev = (provider, model) => normalizeEvent({ type: "turn_start", provider, model });
  assert.deepEqual(ev(mine.id, "ft:gpt-4o-mini:acme-hospital:triage:abc123"), { type: "turn_start", provider: "custom", model: "other" });
  assert.deepEqual(ev("vllm", "/data/zhangsan/Qwen2.5-72B-Instruct"), { type: "turn_start", provider: "vllm", model: "other" });
  assert.deepEqual(ev("ollama", "/Users/alice.wang/models/qwen.gguf"), { type: "turn_start", provider: "ollama", model: "other" });
  assert.deepEqual(ev("ollama", "qwen3:8b"), { type: "turn_start", provider: "ollama", model: "qwen3:8b" });
  assert.deepEqual(ev("siliconflow", "Qwen/Qwen3-235B-A22B-Instruct-2507").model, "Qwen/Qwen3-235B-A22B-Instruct-2507");
  assert.deepEqual(ev("tao", "Tao-S1"), { type: "turn_start", provider: "tao", model: "Tao-S1" });
  assert.equal(ev("constructor", "x").provider, "custom");
  const end = normalizeEvent({ type: "turn_end", turn: "t1", status: "completed", provider: mine.id, model: "my-model" });
  assert.deepEqual([end.provider, end.model], ["custom", "other"]);
  const owner = await ownerOf("p".repeat(43));
  const rec = normalizeRecord({ v: 1, id: "m1", owner, consent: CONSENT_VERSION, question: "q", status: "completed", provider: mine.id, model: "/home/li/m.gguf" });
  assert.deepEqual([rec.provider, rec.model], ["custom", "other"]);
});

test("contract: a record needs the current notice, an owner digest and a question; text is cut to its limit", async () => {
  const owner = await ownerOf("k".repeat(43));
  const base = { v: 1, id: "m1", owner, consent: CONSENT_VERSION, question: "q", status: "completed" };
  assert.ok(normalizeRecord(base));
  assert.equal(normalizeRecord({ ...base, consent: CONSENT_VERSION + 1 }), null);
  assert.equal(normalizeRecord({ ...base, owner: "x" }), null);
  assert.equal(normalizeRecord({ ...base, question: "   " }), null);
  assert.equal(normalizeRecord({ ...base, status: "weird" }), null);
  const long = normalizeRecord({ ...base, question: "问".repeat(5000), answer: "答".repeat(20000) });
  assert.equal(long.question.length, LIMITS.question);
  assert.equal(long.answer.length, LIMITS.answer);
  // a tool run keeps neither its arguments nor their names
  assert.deepEqual(normalizeRecord({ ...base, tools: [{ name: "tcm_herb", args: { name: "甘草" }, arg_keys: ["name"] }] }).tools[0],
    { name: "tcm_herb", kind: "unknown", where: "unknown", status: "unknown" });
});

// ------------------------------------------------------------------------------------------------- what a turn may record

test("exclusion: uploads, project files, clinical and user-data tools, identifying text; judged over the whole conversation", () => {
  const q = (content, extra = {}) => ({ id: content, role: "user", content, ...extra });
  const tool = (name, args = {}, kind = "native") => ({ role: "tool", name, args, envelope: { via: name, governance: { kind }, status: "succeeded" } });
  assert.equal(exclusionOf({ path: [q("桂枝汤主治什么？")] }), null);
  assert.equal(exclusionOf({ path: [q("看看这个", { attachments: ["f1"] }), q("然后呢")] }), "uploaded_file");
  assert.equal(exclusionOf({ path: [q("桂枝汤")], projectFiles: 1 }), "project_files");
  assert.equal(exclusionOf({ path: [q("辨证")], tools: [tool("clinic_assess", {}, "clinic")] }), "clinical_tool");
  assert.equal(exclusionOf({ path: [q("x")], tools: [tool("call_tool", { tool: "clinic.assess", arguments: {} }, "system")] }), "clinical_tool");
  assert.equal(exclusionOf({ path: [q("x")], tools: [tool("run_pipeline", { pipeline: "rnaseq" }, "job")] }), "user_data_tool");
  assert.equal(exclusionOf({ path: [q("x")], tools: [tool("tcmdb_relations", { store_path: "/home/me/store" }, "tcmdb")] }), "user_data_tool");
  assert.equal(exclusionOf({ path: [q("x")], tools: [tool("literature_search", { query: "TP53" })] }), null);
  assert.equal(exclusionOf({ path: [q("患者，男，60岁"), q("桂枝汤呢？")] }), "sensitive_text"); // an earlier question
  assert.equal(exclusionOf({ path: [q("x")], discarded: true }), "discarded");
  // an earlier turn's reason, kept on the conversation, holds for every later turn (a project file removed since)
  assert.equal(exclusionOf({ path: [q("桂枝汤")], projectFiles: 0, before: "project_files" }), "project_files");
  assert.equal(exclusionOf({ path: [q("桂枝汤")], before: null }), null);
});

test("exclusion: the governed skills that run as jobs on the visitor's files, however the file is named", () => {
  const q = (content) => ({ id: content, role: "user", content });
  const via = (tool, args, envelope = {}) => ({ role: "tool", name: "call_tool", args: { tool, arguments: args }, envelope: { via: tool, governance: { kind: "skill" }, status: "succeeded", ...envelope } });
  const out = (t) => exclusionOf({ path: [q("x")], tools: [t] });
  // every file argument of every such skill, given an upload's id, its bare name, a Windows or relative path, a literal
  for (const [tool, args] of [
    ["skill.dock-ligands", { receptor: "PDB:3PTB", ligands: "D:/lab/x.sdf" }],
    ["skill.dock-ligands", { receptor: "R1=/home/me/ref.pdb", ligands: "CCO" }],
    ["skill.predict-admet", { molecules: "u_0123456789abcdef" }],
    ["skill.retrieve-literature-evidence", { question: "黄芩苷", corpus: "lab/notes.txt" }],
    ["skill.scrna-cell-atlas", { source: "C:/Users/li/pbmc.h5ad" }],
    ["skill.scrna-cell-atlas", { source: "pbmc_counts.h5ad" }],
    ["skill.predict-protein-structure", { sequences: "MKTAYIAKQR" }],
    ["skill.rnaseq-differential-expression", { sample_sheet: "u_00000000000000aa" }],
  ]) assert.equal(out(via(tool, args)), "user_data_tool", `${tool} ${JSON.stringify(args)}`);
  // called by name, or with its arguments as JSON text (the runner's dispatcher parses it)
  assert.equal(out({ role: "tool", name: "skill.predict-admet", args: { molecules: "my.smi" }, envelope: { via: "skill.predict-admet" } }), "user_data_tool");
  assert.equal(out({ role: "tool", name: "call_tool", args: { tool: "native.x", arguments: JSON.stringify({ data: "/home/me/a.csv" }) }, envelope: { via: "native.x" } }), "user_data_tool");
  assert.equal(out({ role: "tool", name: "call_tool", args: { tool: "skill.predict-admet", arguments: '{"molecules":"CCO"}' }, envelope: { via: "skill.predict-admet" } }), "user_data_tool");
  // an upload's id in any argument of any tool is that file; so is NAME=<a path> in a list
  assert.equal(out(via("native.blast", { query: "u_0123456789abcdef" })), "user_data_tool");
  assert.equal(out(via("native.align", { inputs: "R1=/data/a.fq, R2=/data/b.fq" })), "user_data_tool");
  // a job's status brings in what the job ran on: a pipeline, a governed skill run, a store's build or import
  const status = (kind) => ({ role: "tool", name: "job_status", args: { job_id: "j_1" }, envelope: { via: "system.job_status", governance: { kind: "system" }, job: { id: "j_1", kind, state: "succeeded" } } });
  for (const kind of ["skill.run", "pipeline.rnaseq", "tcmdb.import"]) assert.equal(out(status(kind)), "user_data_tool", kind);
  assert.equal(out(status("provision")), null);
  // not the visitor's data: a flag left off, a number, an empty value, a genotype call, an accession
  assert.equal(out(via("native.x", { input_file: false, upload: 0, files: [], path: "" })), null);
  assert.equal(out(via("native.allele_frequencies", { genotypes: ["0/1", "./.", "1|1"] })), null);
  assert.equal(out(via("native.allele_frequencies", { genotypes: "./." })), null);
  assert.equal(out(via("connector.uniprot.entry", { accession: "P04637" })), null);
  assert.equal(out(via("native.x", { note: "a=b, c=d" })), null);
});

test("the page's table of skills that take files is the runner's (kinds.py _SKILL_FILES)", () => {
  const source = fs.readFileSync(new URL("../../runner/src/tcmstudio/kinds.py", import.meta.url), "utf8");
  const block = /_SKILL_FILES:[^=]*=\s*\{([\s\S]*?)\n\}/.exec(source);
  assert.ok(block, "kinds.py has _SKILL_FILES");
  const runner = {};
  for (const m of block[1].matchAll(/"([a-z0-9-]+)":\s*\{([^}]*)\}/g)) runner[m[1]] = [...m[2].matchAll(/"([a-z_]+)":/g)].map((x) => x[1]);
  assert.ok(Object.keys(runner).length >= 6);
  assert.deepEqual(SKILL_FILES, runner);
});

test("a turn's record and usage event from a real kernel envelope: citations, claims, tools without arguments", async () => {
  const env = FIX("envelope_safety_released.json");
  const tools = [{ id: "t1", role: "tool", name: "tcm_safety_report", args: { subject: "甘草", co_administered: ["甘遂"] }, envelope: env, where: "browser", status: env.status }];
  const stats = evidenceStats(tools, "十八反 [E1]，见 [E1, E2] 与 【E9】");
  assert.ok(stats.citations.total >= 1);
  assert.deepEqual([...citedIds("十八反 [E1]，见 [E1, E2] 与 【E9】")], ["E1", "E2", "E9"]);
  assert.ok(stats.citations.unresolved >= 1, "E9 names no citation the tools gave");
  assert.ok(stats.evidence.released + stats.evidence.withheld >= 1);
  const runs = toolRuns(tools);
  assert.equal(runs[0].arg_keys, undefined);
  assert.doesNotMatch(JSON.stringify(runs), /甘草|甘遂|subject|co_administered/);
  const assistant = { id: "a1", content: "甘草反甘遂 [E1]。联系 a@b.org", status: "ok", steps: 2, segments: [{ type: "tools", step: 1 }], usage: { input: 900, output: 80 }, provider: "tao", model: "Tao-S1" };
  const owner = await ownerOf("z".repeat(43));
  const record = buildRecord({ id: "a1", conversation: "c1", index: 1, owner, question: "甘草与甘遂同用，有哪些记载？", assistant, tools, status: turnStatus(assistant),
    provider: "tao", model: "Tao-S1", startedAt: 1_000, endedAt: 9_000, firstAt: 1_500, calls: 3, app: { tcmstudio: "0.1.0" }, runtime: { compute: "auto", layout: "wide", lang: "zh" } });
  assert.equal(record.status, "completed");
  assert.equal(record.answer, "甘草反甘遂 [E1]。联系 [redacted:email]");
  assert.deepEqual([record.ms, record.first_ms, record.calls, record.tool_rounds, record.redacted], [8000, 500, 3, 1, 1]);
  assert.deepEqual(record.usage, { input: 900, output: 80 });
  assert.doesNotMatch(JSON.stringify(record), /co_administered|subject/); // no argument, nor its name
  const ev = normalizeEvent(turnEndEvent({ id: "a1", provider: "tao", model: "Tao-S1", status: "completed", first: true, startedAt: 1_000, endedAt: 9_000, calls: 3, assistant, tools, recorded: true }));
  assert.deepEqual([ev.type, ev.ms, ev.calls, ev.tools, ev.tokens_in, ev.recorded, ev.first], ["turn_end", 8000, 3, 1, 900, true, true]);
  assert.equal(JSON.stringify(ev).includes("甘草"), false, "a usage event holds no text");
  // statuses
  assert.equal(turnStatus({ status: "stopped" }), "cancelled");
  assert.equal(turnStatus({ status: "error" }), "failed");
  assert.equal(turnStatus({ status: "ok" }, { interrupted: true }), "interrupted");
  // an answer too long for one request is shortened to fit; the question stays whole
  const big = buildRecord({ id: "a2", conversation: "c1", index: 2, owner, question: "q", assistant: { ...assistant, content: "答".repeat(11000) }, tools, status: "completed" });
  assert.ok(new TextEncoder().encode(JSON.stringify(big)).length <= LIMITS.recordBytes);
});

// ------------------------------------------------------------------------------------------------- the client

test("client: nothing is sent before the site says it takes telemetry, nor when it does not, nor from another origin", async () => {
  resetStorage();
  const a = client();
  a.tel.route({ name: "home" });
  assert.equal(a.requests.length, 0);
  a.tel.configure({ ok: false }, { relayBase: `${ORIGIN}/v1` }); // a health without telemetry (the runner, a file server)
  await a.tel.flush();
  assert.equal(a.requests.length, 0);
  assert.equal(a.tel.needsConsent(), false);
  resetStorage();
  const b = client();
  b.tel.route({ name: "home" });
  b.tel.configure(HEALTH, { relayBase: "https://elsewhere.example.org/v1" }); // the relay of another site
  await b.tel.flush();
  assert.equal(b.requests.length, 0);
  assert.equal(b.tel.here, false);
});

test("client: page views and a visit, batched; a visit again only after half an hour idle; routes by name", async () => {
  resetStorage();
  const c = client();
  c.tel.route({ name: "home" }); // before the health: kept, sent once it says yes
  c.tel.configure(HEALTH, { relayBase: `${ORIGIN}/v1` });
  c.tel.route({ name: "conversation", projectId: "p-secret", convId: "c-secret" });
  c.tel.route({ name: "settings", tab: "privacy" });
  await c.tel.flush();
  const sent = c.requests.filter((r) => r.url === `${ORIGIN}/api/telemetry/events`).flatMap((r) => r.body.events);
  assert.deepEqual(sent.map((e) => [e.type, e.route, e.tab]), [["visit", "home", undefined], ["pageview", "home", undefined], ["pageview", "conversation", undefined], ["pageview", "settings", "privacy"]]);
  assert.doesNotMatch(JSON.stringify(c.requests), /secret/);
  assert.equal(c.requests[0].init.method, "POST");
  assert.equal(c.requests[0].init.keepalive, true);
  c.tick(VISIT_IDLE_MS + 1000);
  c.tel.route({ name: "about" });
  await c.tel.flush();
  assert.deepEqual(c.requests.at(-1).body.events.map((e) => e.type), ["visit", "pageview"]);
  assert.ok(c.requests.every((r) => r.body.v === 1 && typeof r.body.batch === "string"));
});

test("client: opted out (by switch, Do Not Track or GPC), nothing is sent and what waited is dropped", async () => {
  resetStorage();
  const dnt = client({ nav: { doNotTrack: "1" } });
  dnt.tel.configure(HEALTH, { relayBase: `${ORIGIN}/v1` });
  dnt.tel.route({ name: "home" });
  await dnt.tel.flush({ beacon: true });
  assert.deepEqual([dnt.requests.length, dnt.beacons.length], [0, 0]);
  resetStorage();
  const c = client();
  c.tel.configure(HEALTH, { relayBase: `${ORIGIN}/v1` });
  c.tel.route({ name: "home" });
  setAnalytics(false);
  c.tel.discardEvents();
  c.tel.route({ name: "about" });
  await c.tel.flush();
  assert.equal(c.requests.length, 0);
});

test("client: a failed send is kept and sent after a reload of the same tab; a refused one is dropped; beacons on leaving", async () => {
  resetStorage();
  let down = true;
  const c = client({ answer: () => (down ? Promise.reject(new TypeError("Failed to fetch")) : new Response(null, { status: 204 })) });
  c.tel.configure(HEALTH, { relayBase: `${ORIGIN}/v1` });
  c.tel.route({ name: "home" });
  assert.deepEqual(await c.tel.flush(), { sent: 0 }); // never throws
  const tab = sessionStorage.getItem("tcmstudio.tab");
  assert.ok(JSON.parse(localStorage.getItem(`${OUTBOX_PREFIX}${tab}`)).batches.length === 1);
  // the page reloads (same tab): a new client finds the batch and sends it, under the same batch id
  down = false;
  const firstId = c.requests[0].body.batch;
  const again = client();
  again.tel.configure(HEALTH, { relayBase: `${ORIGIN}/v1` });
  await again.tel.flush();
  assert.equal(again.requests[0].body.batch, firstId, "the Worker counts a batch once, whatever arrives twice");
  assert.equal(JSON.parse(localStorage.getItem(`${OUTBOX_PREFIX}${tab}`)).batches.length, 0);
  // a malformed batch (400) is not tried again; 429 and 5xx are
  resetStorage();
  let status = 400;
  const r = client({ answer: () => new Response(null, { status }) });
  r.tel.configure(HEALTH, { relayBase: `${ORIGIN}/v1` });
  r.tel.route({ name: "home" });
  await r.tel.flush();
  assert.equal(r.tel.box.batches.length, 0);
  status = 503;
  r.tel.route({ name: "about" });
  await r.tel.flush();
  assert.equal(r.tel.box.batches.length, 1);
  // leaving the page: sendBeacon, nothing awaited
  status = 204;
  await r.tel.flush({ beacon: true });
  assert.equal(r.beacons.length, 1);
  assert.equal(r.tel.box.batches.length, 0);
});

test("client: records only while agreed; dropped on withdrawal or the conversation's deletion; deletion by key", async () => {
  resetStorage();
  const c = client();
  c.tel.configure(HEALTH, { relayBase: `${ORIGIN}/v1` });
  assert.equal(c.tel.needsConsent(), true);
  const owner = await ownerOf("q".repeat(43));
  const record = { v: 1, id: "m1", conversation: "c1", owner, consent: CONSENT_VERSION, question: "q", status: "completed" };
  assert.deepEqual(await c.tel.sendRecord(record), { sent: 0 }); // not agreed: nothing
  assert.equal(c.requests.length, 0);
  setContentConsent(true);
  assert.deepEqual(await c.tel.sendRecord(record), { sent: 1 });
  assert.equal(c.requests[0].url, `${ORIGIN}/api/telemetry/conversations`);
  // a record that could not be sent waits (for this page or the next); withdrawing drops it
  const down = client({ answer: () => new Response(null, { status: 503 }) });
  down.tel.configure(HEALTH, { relayBase: `${ORIGIN}/v1` });
  await down.tel.sendRecord({ ...record, id: "m2" });
  await down.tel.sendRecord({ ...record, id: "m3", conversation: "c2" });
  assert.equal(JSON.parse(localStorage.getItem(RECORDS_KEY)).length, 2);
  down.tel.purgeRecords("c2");
  assert.deepEqual(JSON.parse(localStorage.getItem(RECORDS_KEY)).map((r) => r.id), ["m2"]);
  setContentConsent(false);
  down.tel.purgeRecords();
  assert.equal(localStorage.getItem(RECORDS_KEY), null);
  // deletion presents the key, never the records
  const del = client({ answer: () => new Response(JSON.stringify({ ok: true, deleted: 3 }), { status: 200 }) });
  del.tel.configure(HEALTH, { relayBase: `${ORIGIN}/v1` });
  assert.deepEqual(await del.tel.deleteMine(), { ok: true, deleted: 3 });
  assert.deepEqual(del.requests[0].body, { key: deletionKey() });
  assert.equal(del.requests[0].url, `${ORIGIN}/api/telemetry/conversations/delete`);
  const failing = client({ answer: () => Promise.reject(new TypeError("offline")) });
  failing.tel.configure(HEALTH, { relayBase: `${ORIGIN}/v1` });
  assert.deepEqual(await failing.tel.deleteMine(), { ok: false, reason: "network" });
});

test("client: a page under an older or newer notice neither asks nor records", () => {
  resetStorage();
  const c = client();
  c.tel.configure({ ...HEALTH, telemetry: { ...HEALTH.telemetry, consent_version: CONSENT_VERSION + 1 } }, { relayBase: `${ORIGIN}/v1` });
  assert.equal(c.tel.conversationsAvailable, false);
  assert.equal(c.tel.needsConsent(), false);
  assert.equal(c.tel.analyticsOn, true);
});

test("client: turns under way are told apart from turns cut off by a reload", async () => {
  resetStorage();
  const c = client();
  c.tel.turnBegan({ id: "t1", conversationId: "c1", provider: "tao", model: "Tao-S1", first: true, startedAt: c.at() });
  c.tel.turnBegan({ id: "t2", conversationId: "c2", provider: "tao", model: "Tao-S1", first: false, startedAt: c.at() });
  assert.deepEqual(await c.tel.staleTurns(), []);
  c.tick(STALE_TURN_MS - 1);
  c.tel.turnBeat("t2");
  c.tick(2);
  assert.deepEqual((await c.tel.staleTurns()).map((t) => t.id), ["t1"]);
  c.tel.turnEnded("t1");
  assert.deepEqual(Object.keys(JSON.parse(localStorage.getItem(TURNS_KEY))), ["t2"]);
  // with the Web Locks API, a turn whose page still holds its lock is never stale, however long it has been silent
  const held = new Set();
  const locks = { request: async (name, fn) => { held.add(name); await fn(); held.delete(name); }, query: async () => ({ held: [...held].map((name) => ({ name })) }) };
  const l = client({ nav: { locks } });
  l.tel.turnBegan({ id: "t3", conversationId: "c3", startedAt: l.at() });
  l.tick(10 * STALE_TURN_MS);
  assert.ok(!(await l.tel.staleTurns()).some((t) => t.id === "t3"));
  l.tel.turnEnded("t3");
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(held.size, 0, "the lock goes with the turn");
});

test("client: errors as category, name and place in the app's own code, never a message; at most ten a page", async () => {
  resetStorage();
  const c = client();
  c.tel.configure(HEALTH, { relayBase: `${ORIGIN}/v1` });
  c.tel.error("js_error", new TypeError("患者张三的数据"), `${ORIGIN}/js/ui/app.js?v=1`, 120);
  c.tel.error("js_error", new TypeError("again"), `${ORIGIN}/js/ui/app.js`, 120); // the same: once
  const err = new RangeError("x");
  err.stack = `RangeError: x\n    at f (${ORIGIN}/js/core/agent.js:77:9)`;
  c.tel.error("unhandled_rejection", err);
  c.tel.error("js_error", new Error("ext"), "chrome-extension://abc/content.js", 3);
  for (let i = 0; i < 20; i++) c.tel.error("js_error", { name: `E${i}` }, `${ORIGIN}/js/x.js`, i + 1);
  await c.tel.flush();
  const errors = c.requests.flatMap((r) => r.body.events).filter((e) => e.type === "error");
  assert.deepEqual(errors.slice(0, 3).map((e) => [e.category, e.name, e.where]), [
    ["js_error", "TypeError", "js/ui/app.js:120"], ["unhandled_rejection", "RangeError", "js/core/agent.js:77"], ["js_error", "Error", "external"]]);
  assert.equal(errors.length, 10);
  assert.doesNotMatch(JSON.stringify(c.requests), /患者|张三|again/);
});
