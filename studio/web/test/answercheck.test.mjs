// The answer check (js/core/answercheck.js): measured on a corpus of answers in Chinese and English
// (fixtures/answercheck-corpus.json) with the precision and recall each rule must keep, the known answers it must read
// right (the proof of concept's good and bad answers, the e2e mock model's 十八反 answer), the negation and mention
// allow-list, and that it only reports: the answer and the envelopes are the same after it as before.

import "./fixtures/setup.mjs";
import assert from "node:assert/strict";
import fs from "node:fs";
import { test } from "node:test";
import { KINDS, RULES, checkAnswer, checkTurn, citedIds, excerpt, inquiryConclusionsOf, refusedClaimCodes } from "../js/core/answercheck.js";
import { BANNED_TERMS, VOCABULARY, buildSystemPrompt } from "../js/core/prompt.js";
import { plan } from "../../e2e/mock-llm.mjs";

const FIX = (name) => JSON.parse(fs.readFileSync(new URL(`./fixtures/${name}`, import.meta.url), "utf8"));
const CORPUS = FIX("answercheck-corpus.json");
const envelope = (name) => (name.startsWith("fixture:") ? FIX(`${name.slice(8)}.json`) : CORPUS.envelopes[name]);
const keyOf = (f) => (f.kind === "refusal_unreported" || f.kind === "uncited_with_evidence" ? f.kind : `${f.kind}:${f.id ?? f.term}`);
const keys = (r) => r.findings.map(keyOf);
const run = (c) => checkAnswer(c.answer, { turnEnvelopes: c.turn.map(envelope), conversationCites: c.conversation ?? null });

// what each rule must keep on the corpus (the plan's acceptance)
const FLOOR = {
  unresolved_citation: { precision: 0.95, recall: 0.9 },
  banned_term: { precision: 0.95, recall: 0.9 },
  released_wording: { precision: 0.9, recall: 0.8 },
  refusal_unreported: { precision: 0.9, recall: 0.8 },
  overclaim_inquiry: { precision: 0.9, recall: 0.8 },
  uncited_with_evidence: { precision: 0.9, recall: 0.8 },
};

test("corpus: at least 50 answers in Chinese and English, every kind expected somewhere, every envelope named exists", () => {
  assert.ok(CORPUS.cases.length >= 50, `${CORPUS.cases.length} cases`);
  assert.deepEqual(new Set(CORPUS.cases.map((c) => c.lang)), new Set(["zh", "en"]));
  for (const lang of ["zh", "en"]) assert.ok(CORPUS.cases.filter((c) => c.lang === lang).length >= 20, lang);
  assert.equal(new Set(CORPUS.cases.map((c) => c.id)).size, CORPUS.cases.length, "case ids are unique");
  const expected = new Set(CORPUS.cases.flatMap((c) => c.expect.map((k) => k.split(":")[0])));
  assert.deepEqual([...expected].sort(), [...KINDS].sort());
  for (const c of CORPUS.cases) for (const n of c.turn) assert.ok(envelope(n), `${c.id}: ${n}`);
  // clean answers are part of the measure: a rule that flags everything fails it
  assert.ok(CORPUS.cases.filter((c) => !c.expect.length).length >= 30);
});

test("corpus: each rule keeps its precision and recall", () => {
  const score = Object.fromEntries(KINDS.map((k) => [k, { tp: 0, fp: 0, fn: 0 }]));
  const misses = [];
  for (const c of CORPUS.cases) {
    const got = keys(run(c));
    const left = [...c.expect];
    for (const g of got) {
      const i = left.indexOf(g);
      const kind = g.split(":")[0];
      if (i >= 0) { left.splice(i, 1); score[kind].tp++; } else { score[kind].fp++; misses.push(`${c.id}: extra ${g}`); }
    }
    for (const w of left) { score[w.split(":")[0]].fn++; misses.push(`${c.id}: missing ${w}`); }
  }
  for (const [kind, s] of Object.entries(score)) {
    const precision = s.tp / (s.tp + s.fp);
    const recall = s.tp / (s.tp + s.fn);
    assert.ok(s.tp > 0, `${kind}: the corpus has true cases`);
    assert.ok(precision >= FLOOR[kind].precision, `${kind} precision ${precision.toFixed(3)} (${misses.join("; ")})`);
    assert.ok(recall >= FLOOR[kind].recall, `${kind} recall ${recall.toFixed(3)} (${misses.join("; ")})`);
  }
});

test("known answers: the PoC bad answer gives E7 and 证明, the PoC good answer and the mock 十八反 answer give nothing", () => {
  const byId = Object.fromEntries(CORPUS.cases.map((c) => [c.id, c]));
  const bad = keys(run(byId["poc-bad"]));
  assert.ok(bad.includes("unresolved_citation:E7"), bad.join(", "));
  assert.ok(bad.includes("banned_term:证明"), bad.join(", "));
  assert.deepEqual(keys(run(byId["poc-good"])), []);
  // the mock model's answer as the e2e tests stream it, read from the mock itself (not a copy)
  const events = plan({ messages: [{ role: "user", content: "甘草与甘遂同用，有哪些记载？" }, { role: "tool", content: "tcm_safety_report → skill.assess-tcm-safety: succeeded" }] }).events;
  const answer = events.map((e) => e.delta?.content || "").join("");
  assert.match(answer, /没有记载也不等于安全/);
  assert.equal(answer, byId["mock-shibafan"].answer, "the corpus holds the mock's answer as it is");
  assert.deepEqual(keys(checkAnswer(answer, { turnEnvelopes: [FIX("envelope_safety_released.json")] })), []);
});

test("negation and mention allow-list: the negated, asked, conditional, quoted and coded forms pass; the bare word does not", () => {
  const cites = [{ citations: [{ id: "E1" }] }];
  const banned = (text) => checkAnswer(`${text} [E1]`, { turnEnvelopes: cites }).findings.filter((f) => f.kind === "banned_term").map((f) => f.term);
  for (const ok of [
    "无记录不等于安全。", "无记录（不等于安全）。", "没有记载也不等于安全。", "这不能证明其有效。", "尚未证明。", "并不能证实疗效。", "不足以证明安全。",
    "是否安全尚无定论。", "需要随机对照试验才能证明。", "按规范用「记载」，不用「证明」。", "不一定安全。", "两药同用可能不安全。",
    "Not proven.", "This has not been proven.", "No record (not safe).", "The absence of a record does not mean it is safe.",
    "Whether it is safe is unknown.", "Is it safe?", "A trial would be needed to prove it.", "We cannot prove efficacy.", "Nothing here is proven.",
    "Fail-safe storage.", "`safe` is a word the rule forbids.", "安全信号 2 条。", "安全性评价未做。", "精准医学。",
  ]) assert.deepEqual(banned(ok), [], ok);
  for (const [text, term] of [
    ["临床证明有效。", "证明"], ["长期服用安全。", "安全"], ["研究证实了这一点。", "证实"], ["精准定位靶点。", "精准"], ["发现了机制。", "发现了机制"],
    ["副作用确认。", "副作用确认"], ["不仅有效，而且安全。", "安全"], ["非常安全。", "安全"], ["安全性良好。", "安全"],
    ["It is proven.", "proven"], ["Trials prove it works.", "proven"], ["It is safe.", "safe"], ["It can be safely combined.", "safe"], ["It is not toxic and is safe.", "safe"],
    ["Docking discovered the target.", "discovered"], ["A confirmed side effect.", "confirmed side effect"],
  ]) assert.deepEqual(banned(text), [term], text);
  // 发现、揭示 and irrelevant are the rule's words for a prediction and a result that is not significant only: elsewhere
  // they are ordinary (一项试验发现…), so they are not reported alone
  assert.deepEqual(banned("一项随机对照试验发现血压下降。The p-value is irrelevant here."), []);
});

test("the banned words are the prompt's: each is in the rendered rule of its language, and the rule names the never-list", () => {
  assert.ok(BANNED_TERMS.length >= 10);
  for (const lang of ["zh", "en"]) {
    const p = buildSystemPrompt({ lang, provider: { relay: true }, env: { date: "2026-11-06" } });
    for (const b of BANNED_TERMS.filter((x) => x.lang === lang)) assert.ok(p.includes(b.term), `${lang} ${b.term}`);
  }
  assert.deepEqual(BANNED_TERMS.filter((b) => b.never).map((b) => b.term), [...VOCABULARY.zh.never]);
  // a word the rule allows when the evidence licenses it (unless …) is never banned
  for (const w of [...VOCABULARY.zh.words, ...VOCABULARY.en.words].filter((x) => x.unless)) {
    for (const term of w.not) assert.equal(BANNED_TERMS.some((b) => b.term === term), false, term);
  }
  assert.throws(() => { BANNED_TERMS[0].term = "x"; }, TypeError, "the list is frozen");
});

test("report-only: the answer and the envelopes are unchanged, and the same input gives the same findings", () => {
  const c = CORPUS.cases.find((x) => x.id === "s11-long-mutant-en");
  const envs = c.turn.map(envelope);
  const before = JSON.stringify(envs);
  const answer = c.answer;
  const copy = `${answer}`;
  const a = checkAnswer(answer, { turnEnvelopes: envs });
  const b = checkAnswer(answer, { turnEnvelopes: envs });
  assert.equal(answer, copy);
  assert.equal(Buffer.compare(Buffer.from(answer), Buffer.from(copy)), 0, "byte-identical");
  assert.equal(JSON.stringify(envs), before, "the envelopes are not touched");
  assert.deepEqual(a, b);
  assert.ok(a.findings.length > 0);
  // an empty answer says nothing: nothing to report
  assert.deepEqual(checkAnswer("", { turnEnvelopes: envs }).findings, []);
  assert.deepEqual(checkAnswer("   \n", { turnEnvelopes: envs }).counts, Object.fromEntries(KINDS.map((k) => [k, 0])));
});

test("counts, order, rules and excerpts", () => {
  const r = checkAnswer("桂枝汤临床证明安全有效 [E7]。", { turnEnvelopes: [{ citations: [{ id: "E1" }] }] });
  assert.deepEqual(r.counts, { unresolved_citation: 1, banned_term: 2, released_wording: 0, refusal_unreported: 0, uncited_with_evidence: 0, overclaim_inquiry: 0 });
  assert.deepEqual(r.findings.map((f) => f.kind), ["banned_term", "banned_term", "unresolved_citation"], "in the order of the text");
  assert.equal(excerpt("桂枝汤临床证明安全有效 [E7]。", r.findings[0], 3), "…汤临床证明安全有…");
  assert.deepEqual(RULES, KINDS);
  // the least certain rule can be left out
  const weak = { result: { conclusion: { verdict: "provisional", certainty: "tentative" } }, citations: [{ id: "E1" }] };
  assert.equal(checkAnswer("H1 is accepted [E1].", { turnEnvelopes: [weak] }).counts.overclaim_inquiry, 1);
  assert.equal(checkAnswer("H1 is accepted [E1].", { turnEnvelopes: [weak], rules: KINDS.filter((k) => k !== "overclaim_inquiry") }).counts.overclaim_inquiry, 0);
  assert.deepEqual(inquiryConclusionsOf([weak, { result: {} }, null]), [{ verdict: "provisional", certainty: "tentative", leader: null }]);
  assert.deepEqual(refusedClaimCodes([FIX("envelope_claims_refused.json")]).codes.filter((c) => c.startsWith("CLM")), ["CLM004", "CLM005", "CLM006", "CLM008", "CLM009"]);
  assert.deepEqual([...citedIds("见 [E1, E2] 与 【E9】")], ["E1", "E2", "E9"]);
});

test("citations: an earlier turn's id resolves (the conversation's), a turn without tools cites nothing it may", () => {
  const earlier = { role: "tool", envelope: { citations: [{ id: "E1" }, { id: "E2" }] } };
  const now = { role: "tool", envelope: { citations: [{ id: "E3" }] } };
  assert.deepEqual(keys(checkTurn({ content: "见 [E1] 与 [E3]。", tools: [now], known: [earlier, now] })), []);
  assert.deepEqual(keys(checkTurn({ content: "见 [E1] 与 [E3]。", tools: [now], known: [now] })), ["unresolved_citation:E1"]);
  assert.deepEqual(keys(checkTurn({ content: "见 [E4]。", tools: [], known: [earlier] })), ["unresolved_citation:E4"]);
  // a function stands for the conversation's ids (the page resolves them lazily)
  assert.deepEqual(keys(checkAnswer("[E5]", { conversationCites: (id) => id === "E5" })), []);
  assert.deepEqual(keys(checkAnswer("[E5]", { conversationCites: () => false })), ["unresolved_citation:E5"]);
});

test("the agent eval's evidence grader reads this module, not a copy", { skip: !fs.existsSync(new URL("../../eval/graders/evidence.mjs", import.meta.url)) && "studio/eval (plan item E1) is not in this tree" }, () => {
  const source = fs.readFileSync(new URL("../../eval/graders/evidence.mjs", import.meta.url), "utf8");
  assert.match(source, /from\s+["'](?:\.\.\/)+web\/js\/core\/answercheck\.js["']/);
  assert.doesNotMatch(source, /证明\|确认有效|BANNED\s*=\s*\//, "no vocabulary of its own");
});

// ------------------------------------------------------------------------------------------------- in the page

test("in the page: a badge only when something is marked, the details for the inspector, and the stored answer untouched", async () => {
  await import("../dev/test/setup.mjs");
  const { registerUiStrings } = await import("../js/ui/strings.js");
  const { setLang } = await import("../js/core/i18n.js");
  const { answerCheckBadge, answerCheckList } = await import("../js/ui/governance.js");
  const { answerCheckOf } = await import("../js/ui/conversation.js");
  registerUiStrings();
  const content = "桂枝汤由桂枝、芍药、甘草、生姜、大枣组成 [E1]，临床证明安全有效 [E7]。";
  const record = { id: "a1", role: "assistant", content, status: "ok" };
  const env = { status: "succeeded", citations: [{ id: "E1", label: "伤寒论" }, { id: "E2", label: "x" }] };
  const vm = { id: "a1", record, segments: [{ type: "tools", step: 1, callIds: ["c1"] }, { type: "text", step: 2, text: content }], tools: new Map([["c1", { call: { id: "c1" }, envelope: env }]]) };
  const app = { path: () => [{ id: "u1", role: "user", content: "q" }, record] };
  const before = JSON.stringify({ record, env });
  const check = answerCheckOf(app, vm);
  assert.equal(JSON.stringify({ record, env }), before, "the stored answer and its envelopes are not changed");
  assert.equal(record.content, content);
  assert.equal(check.content, content);
  assert.deepEqual(keys(check), ["banned_term:证明", "banned_term:安全", "unresolved_citation:E7"]);
  try {
    let opened = 0;
    const badge = answerCheckBadge(check, { onOpen: () => { opened++; } });
    assert.equal(badge.textContent.trim(), "回答检查：3 处");
    assert.equal(badge.getAttribute("data-answer-check"), "3");
    badge.click();
    assert.equal(opened, 1);
    const list = answerCheckList(check, check.content);
    assert.deepEqual([...list.querySelectorAll("[data-check-kind]")].map((li) => li.getAttribute("data-check-kind")), ["banned_term", "banned_term", "unresolved_citation"]);
    assert.match(list.textContent, /「证明」是治理规则不用的词，规则用「记载」/);
    assert.match(list.textContent, /\[E7\] 不是本对话中任何工具给出的引文编号/);
    setLang("en");
    assert.equal(answerCheckBadge(check).textContent.trim(), "Answer check: 3 notes");
    assert.match(answerCheckList(check, check.content).textContent, /“证明” is a word the governance rule does not use; it says “记载”/);
    // a clean answer: no badge at all, and the inspector says only that nothing was marked
    const clean = answerCheckOf(app, { ...vm, record: { ...record, content: "桂枝汤由五味药组成 [E1]。" } });
    assert.equal(answerCheckBadge(clean), null);
    assert.equal(answerCheckList(clean, clean.content).textContent, "Nothing marked.");
  } finally {
    setLang("zh");
  }
});
