import "./fixtures/setup.mjs";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import {
  BODY_SHARE, DEFAULT_CONTEXT_TOKENS, KEEP_CHARS, MARGIN, SHORTENED, budgetOf, compactResult, compactText, estimate, fitRequest, isCompactable, measure, tokensOf,
} from "../js/core/budget.js";
import { ProviderError } from "../js/core/llm/sse.js";

// the text the model reads for two envelopes, as the runner's envelope.compose_text writes it (make_envelope_text_fixtures.py)
const TEXTS = JSON.parse(readFileSync(new URL("./fixtures/envelope_texts.json", import.meta.url), "utf8"));
const digest = (x) => createHash("sha256").update(JSON.stringify(x)).digest("hex");
const tool = (id, content) => ({ role: "tool", tool_call_id: id, content });
const asks = (...ids) => ({ role: "assistant", content: "", tool_calls: ids.map((id) => ({ id, type: "function", function: { name: "tcm_herb", arguments: "{}" } })) });

test("the estimate: UTF-8 bytes exactly, one token per CJK code point and per four other bytes, 20% over", () => {
  for (const s of ["", "abc", "x".repeat(4000), "甘草与甘遂", "十八反：藻戟遂芫俱战草", "é ü ß", "𠀀𪚥 (CJK Ext B)", "emoji 🧪 mixed 中文 and ascii", "全角ＡＢＣ　、。", "한국어", "\u{d800}lone"]) {
    assert.equal(measure(s).bytes, Buffer.byteLength(s, "utf8"), JSON.stringify(s));
  }
  assert.deepEqual(measure("x".repeat(4000)), { bytes: 4000, units: 1000 });
  assert.deepEqual(measure("中".repeat(1000)), { bytes: 3000, units: 1000 }, "a CJK character is one token, not three bytes' worth");
  assert.deepEqual(measure("𠀀"), { bytes: 4, units: 1 }, "a CJK character outside the BMP is one token too");
  assert.deepEqual(measure("é"), { bytes: 2, units: 0.5 });
  assert.equal(MARGIN, 1.2);
  assert.equal(tokensOf(1000), 1200);
  assert.deepEqual(estimate("中".repeat(1000) + "x".repeat(4000)), { bytes: 7000, tokens: 2400 });
});

test("the budget: 0.8 × the window − the answer; 0.9 × the body cap on the relay only; 32 000 tokens when unknown", () => {
  assert.deepEqual(budgetOf({ contextTokens: 128000, maxTokens: 8192, relay: true, maxBodyBytes: 2000000 }), { tokens: 94208, bytes: 1800000 });
  assert.equal(BODY_SHARE * 2000000, 1800000);
  assert.deepEqual(budgetOf({ contextTokens: 32000, maxTokens: 8192 }), { tokens: 17408, bytes: Infinity }, "no byte cap off the relay");
  assert.deepEqual(budgetOf({ maxTokens: 0 }), { tokens: 0.8 * DEFAULT_CONTEXT_TOKENS, bytes: Infinity });
  assert.deepEqual(budgetOf({ contextTokens: 32000, maxTokens: 8192, maxBodyBytes: 1000 }), { tokens: 17408, bytes: Infinity }, "a body cap is the relay's");
  assert.equal(budgetOf({ contextTokens: 8000, maxTokens: 8192 }).tokens, 2000, "an answer setting larger than the window still leaves a quarter of it");
});

test("a request too long for the model is told apart: context_length_exceeded, too_large, a 413, the words of a 400", () => {
  assert.equal(isCompactable(new ProviderError("x", { status: 400, type: "context_length_exceeded" })), true);
  assert.equal(isCompactable(new ProviderError("对话太长", { status: 413, type: "too_large" })), true);
  assert.equal(isCompactable({ status: 413 }), true);
  assert.equal(isCompactable({ compactable: true }), true, "as core/llm/sse.js marks it");
  assert.equal(isCompactable(new ProviderError("The model service returned 400: prompt is too long: 210000 tokens > 200000 maximum", { status: 400, type: "invalid_request_error" })), true);
  assert.equal(isCompactable(new ProviderError("This model's maximum context length is 32768 tokens", { status: 400 })), true);
  for (const e of [new ProviderError("busy", { status: 429, type: "rate_limit" }), new ProviderError("x", { status: 500 }), new ProviderError("x", { status: 429, type: "daily_limit" }), new ProviderError("bad temperature", { status: 400, type: "invalid_request_error" }), null, "too_large"]) {
    assert.equal(isCompactable(e), false, String(e?.message ?? e));
  }
});

test("a compacted result keeps its header, every claim and refusal, every citation and its Outcome line; the body goes", () => {
  for (const [name, full] of Object.entries(TEXTS)) {
    const text = `${full}\nOutcome: no_record — the query ran and matched nothing; that is not evidence that none exists.`;
    const out = compactResult(text);
    assert.ok(out.length < text.length / 2, `${name}: ${out.length} of ${text.length}`);
    const head = text.slice(0, text.indexOf("\nResult: "));
    assert.ok(out.startsWith(`${head}\nResult: ${SHORTENED}`), name);
    for (const id of new Set(text.match(/\[E\d+\]/g))) assert.ok(out.includes(id), `${name}: ${id}`);
    for (const line of text.split("\n").filter((l) => /^- \[|^- [A-Z]{3}\d{3}: |^Claims |^Refused |^Release: |^Limits /.test(l))) assert.ok(out.includes(line), `${name}: ${line}`);
    assert.ok(out.endsWith("\nOutcome: no_record — the query ran and matched nothing; that is not evidence that none exists."), name);
    assert.equal(compactResult(out), out, "compacting twice changes nothing");
  }
  assert.match(TEXTS.claims_refused, /REFUSED/);
  assert.match(TEXTS.claims_refused, /\nRefused \(a refusal is a result/);
});

test("a result without the envelope header keeps its start, its Outcome line and the citation ids of what was left out", () => {
  const text = `literature_search: ${"x".repeat(5000)} see [E7] and [E9] ${"y".repeat(3000)} [E7]\nOutcome: tool_failed — the call failed.`;
  const out = compactResult(text);
  assert.ok(out.startsWith(text.slice(0, KEEP_CHARS)));
  assert.match(out, /Citations in the part left out: \[E7\] \[E9\]/);
  assert.match(out, /\nOutcome: tool_failed — the call failed\.$/);
  assert.ok(out.length < 1000);
  assert.equal(compactResult("short"), "short", "never longer than it was");
  // what follows the one-line result is the agent's few lines; were it more, only its Outcome line is kept
  const long = compactResult(`head\nResult: {"a":1}\n${"z".repeat(5000)}\nOutcome: no_record — nothing matched.`);
  assert.equal(long, `head\nResult: ${SHORTENED}\nOutcome: no_record — nothing matched.`);
  assert.equal(compactText("短"), "短");
  assert.ok(compactText("长".repeat(5000)).length < 1000);
});

test("under budget the request is the input itself: the same messages, the same digest", () => {
  const base = [{ role: "user", content: "q0" }, asks("a"), tool("a", TEXTS.safety_released), { role: "assistant", content: "答 [E1]" }, { role: "user", content: "q1" }];
  const turn = [asks("b"), tool("b", TEXTS.claims_refused)];
  const fit = fitRequest({ base, turn, fixed: measure("{}"), budget: { tokens: 1e9, bytes: 1e9 } });
  assert.equal(fit.fits, true);
  assert.equal(fit.changed, false);
  assert.deepEqual(fit.compacted, []);
  assert.equal(digest(fit.messages), digest([...base, ...turn]));
  fit.messages.forEach((m, i) => assert.equal(m, [...base, ...turn][i], "not even copied"));
  // and it measures what it sends: the messages array as JSON, comma for comma
  const fixed = measure('{"model":"m","messages":[]}');
  const f2 = fitRequest({ base, turn, fixed: { bytes: fixed.bytes - 1, units: fixed.units - 0.25 }, budget: { tokens: 1e9, bytes: 1e9 } });
  assert.equal(f2.bytes, Buffer.byteLength(JSON.stringify({ model: "m", messages: [...base, ...turn] })));
});

test("over budget: earlier turns' results go first, then this turn's oldest; the newest step's results never, until the answer's request", () => {
  const base = [{ role: "user", content: "q0" }, asks("old"), tool("old", TEXTS.safety_released), { role: "assistant", content: "长的旧回答".repeat(400) }, { role: "user", content: "q1" }];
  const turn = [asks("s1"), tool("s1", TEXTS.claims_refused), asks("s2"), tool("s2", TEXTS.safety_released), asks("s3a", "s3b"), tool("s3a", TEXTS.claims_refused), tool("s3b", TEXTS.safety_released)];
  const all = [...base, ...turn];
  const size = fitRequest({ base, turn, budget: { tokens: 1e9, bytes: 1e9 } }).bytes;
  const at = (bytes, lastResort = false) => fitRequest({ base, turn, budget: { tokens: 1e9, bytes }, lastResort });

  let f = at(size - 100);
  assert.deepEqual(f.compacted, ["old"], "1. an earlier turn's result first");
  assert.equal(f.fits, true);
  f = at(f.bytes - 1);
  assert.deepEqual(f.compacted, ["old", "s1"], "2. then this turn's oldest");
  f = at(f.bytes - 1);
  assert.deepEqual(f.compacted, ["old", "s1", "s2"]);
  assert.ok(f.messages[3].content === all[3].content, "earlier assistant text is kept while results can go");
  // only the newest step's results and earlier assistant text are left
  f = at(f.bytes - 1);
  assert.deepEqual(f.compacted, ["old", "s1", "s2"], "3. earlier assistant text, never the newest results");
  assert.ok(f.messages[3].content.length < all[3].content.length, "the earlier answer was shortened");
  assert.equal(f.fits, true);
  f = at(10000);
  assert.equal(f.fits, false, "nothing more fits without the newest results");
  assert.equal(f.messages.at(-1).content, TEXTS.safety_released, "the newest step's results are never compacted for a call that may run tools");
  assert.equal(f.messages.at(-2).content, TEXTS.claims_refused);
  f = at(10000, true);
  assert.deepEqual(f.compacted, ["old", "s1", "s2", "s3a", "s3b"], "the answer's request may shorten them too");
  // a compacted result keeps every citation and verdict line
  for (const m of f.messages.filter((x) => x.role === "tool")) {
    const original = all.find((x) => x.role === "tool" && x.tool_call_id === m.tool_call_id).content;
    for (const id of new Set(original.match(/\[E\d+\]/g))) assert.ok(m.content.includes(id));
    for (const line of original.split("\n").filter((l) => /— (REFUSED|allowed)|^- [A-Z]{3}\d{3}: /.test(l))) assert.ok(m.content.includes(line));
  }
  assert.equal(all[2].content, TEXTS.safety_released, "the input is never changed");
});

test("Anthropic tool_result blocks are compacted the same way; thinking and tool_use are left alone", () => {
  const base = [{ role: "user", content: "q" }];
  const turn = [
    { role: "assistant", content: [{ type: "thinking", thinking: "t", signature: "sig" }, { type: "tool_use", id: "a", name: "tcm_herb", input: {} }] },
    { role: "user", content: [{ type: "tool_result", tool_use_id: "a", content: TEXTS.claims_refused }] },
    { role: "assistant", content: [{ type: "tool_use", id: "b", name: "tcm_herb", input: {} }] },
    { role: "user", content: [{ type: "tool_result", tool_use_id: "b", content: TEXTS.safety_released }] },
  ];
  const size = fitRequest({ base, turn }).bytes;
  const f = fitRequest({ base, turn, budget: { tokens: 1e9, bytes: size - 1000 } });
  assert.deepEqual(f.compacted, ["a"]);
  assert.deepEqual(f.messages[1], turn[0]);
  assert.match(f.messages[2].content[0].content, /\nResult: \[shortened/);
  assert.equal(f.messages[4], turn[3]);
});

test("budgeting a 2 MB request takes at most 20 ms", () => {
  const big = [{ role: "user", content: "q" }, ...Array.from({ length: 70 }, (_, i) => [asks(`c${i}`), tool(`c${i}`, (i % 2 ? "中" : "x").repeat(16000))]).flat()];
  assert.ok(Buffer.byteLength(JSON.stringify(big)) > 2_000_000);
  const times = [];
  for (let k = 0; k < 5; k++) {
    const t0 = performance.now();
    const f = fitRequest({ base: big, turn: [], budget: { tokens: 1e9, bytes: 1e9 } });
    times.push(performance.now() - t0);
    assert.ok(f.bytes > 2_000_000);
  }
  assert.ok(Math.min(...times) <= 20, `best of 5: ${Math.min(...times).toFixed(1)} ms`);
});
