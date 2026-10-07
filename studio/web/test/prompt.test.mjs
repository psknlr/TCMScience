import "./fixtures/setup.mjs";
import assert from "node:assert/strict";
import { test } from "node:test";
import { INLINE_LIMIT, buildSystemPrompt, isIdentityQuestion } from "../js/core/prompt.js";
import { activeProvider } from "../js/core/providers.js";

const relay = activeProvider({ provider: "tao" });
const deepseek = activeProvider({ provider: "deepseek", models: { deepseek: "deepseek-reasoner" } });

test("identity: Tao-S1 through the relay, the model's own name otherwise; no vendor is named", () => {
  const p = buildSystemPrompt({ lang: "zh", provider: relay, env: { date: "2026-10-07" } });
  assert.ok(p.startsWith("# Identity\nYou are Tao-S1, the research assistant of TCMScience Studio (IMPF-AI)."));
  assert.doesNotMatch(p, /minimax|MiniMax|abab|海螺/i);
  const q = buildSystemPrompt({ lang: "en", provider: deepseek });
  assert.match(q, /You are the research assistant of TCMScience Studio, running on deepseek-reasoner \(DeepSeek\)\./);
  assert.doesNotMatch(q, /Tao-S1/);
});

test("the sections come in the contract's order and carry the governance rules", () => {
  const p = buildSystemPrompt({ lang: "zh", provider: relay, project: { name: "葛根芩连汤复核", instructions: "只讨论经典记载。", defaults: { web: true } }, knowledge: [], env: { date: "2026-10-07", browser: { status: "ready" }, runner: { status: "offline" } } });
  const order = ["# Identity", "# How TCMScience reasons", "# Tools", "# Environment", "# Project"].map((h) => p.indexOf(h));
  assert.ok(order.every((i) => i >= 0));
  assert.deepEqual([...order].sort((a, b) => a - b), order);
  for (const rule of [/types, not grades/, /never stated as a fact/, /no record ≠ safe/, /not irrelevant/, /attribution or a traditional use/, /licensed practitioner/, /\[E1\]/, /CLM005/, /catalog_search/, /call_tool/, /job_status/, /Never fabricate/]) {
    assert.match(p, rule);
  }
  assert.match(p, /efficacy: randomized_trial, systematic_review/);
  assert.match(p, /mechanism_hypothesis: computational_prediction, preclinical/);
  assert.match(p, /记载 \(not 证明\/证实\)/, "the Chinese vocabulary when the UI is Chinese");
  assert.match(p, /Today: 2026-10-07\./);
  assert.match(p, /local runner not connected/);
  assert.match(p, /Web access for this project: on/);
  assert.match(p, /只讨论经典记载。/);
  const en = buildSystemPrompt({ lang: "en", provider: relay, env: { runner: { status: "ready", url: "http://127.0.0.1:8765", version: "0.1.0", devices: [{ id: "cuda:0", name: "RTX 4090", available: true }, { id: "cpu", available: true }] }, web: false } });
  assert.match(en, /recorded \(not proven\)/);
  assert.match(en, /local runner connected at http:\/\/127\.0\.0\.1:8765 \(tcmstudio 0\.1\.0\)/);
  assert.match(en, /cuda:0 \(RTX 4090\)/);
  assert.match(en, /Web access for this project: off/);
});

test("knowledge: listed with size and hash; texts under 8 kB are inlined, longer ones are not", () => {
  const small = "葛根芩连汤：葛根、黄芩、黄连、炙甘草。";
  const big = "x".repeat(INLINE_LIMIT + 10);
  const p = buildSystemPrompt({
    lang: "zh", provider: relay, project: { name: "P" },
    knowledge: [
      { name: "方剂.md", text: small, sha256: "ab".repeat(32) },
      { name: "long.txt", text: big, sha256: "cd".repeat(32) },
      { name: "data.csv", bytes: 123456, sha256: "ef".repeat(32) },
    ],
  });
  assert.match(p, /- 方剂\.md · \d+ B · sha256:abababababab \(inlined below\)/);
  assert.match(p, /### 方剂\.md\n```\n葛根芩连汤/);
  assert.match(p, /long\.txt · 8\.0 kB · sha256:cdcdcdcdcdcd \(too long to inline/);
  assert.doesNotMatch(p, /x{100}/);
  assert.match(p, /data\.csv · 120\.6 kB/);
});

test("identity questions in Chinese and English, including traditional characters and passing mentions", () => {
  for (const q of ["你是谁？", "你是谁开发的", "你用的是什么大模型", "你是不是 ChatGPT", "你是DeepSeek吗", "介绍一下你自己", "你的底层模型是什么", "你叫什么名字", "你們是誰", "請問你是什麼模型", "Who are you?", "what model are you", "Are you Claude?", "who made you", "what is your underlying model", "桂枝汤出自哪里？顺便问下你是谁"]) {
    assert.equal(isIdentityQuestion(q), true, q);
  }
  for (const q of ["桂枝汤出自哪里？", "这个网络药理学分析用什么模型预测靶点？", "What model should I use for survival analysis?", "甘草的性味归经", "who are the authors of this trial", "你能帮我查一下葛根吗", ""]) {
    assert.equal(isIdentityQuestion(q), false, q);
  }
});
