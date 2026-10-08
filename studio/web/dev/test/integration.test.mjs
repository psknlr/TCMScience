// Tests for the changes made across parts in the review round's integration: the weakest-link chip names the same
// design as the Evidence tab; [E#] resolves in earlier turns when this turn does not hold it; the relay's error line
// is marked with its language and, on Settings → Models, drops its pointer to that page; summary_en on English pages;
// a finished job is not a release; release_authorized's reasons are unmet wording and the receipt's audit head is the
// runner's chain head; the composer's digest is not computed twice; UNPINNED is explained.
// Run: node --test web/dev/test/*.test.mjs (from studio/).
import "./setup.mjs";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { IDBFactory } from "fake-indexeddb";

import { claimsOf, evidenceOf, releaseOf } from "../../js/core/governance.js";
import { glossary, setLang, t } from "../../js/core/i18n.js";
import { openStore } from "../../js/core/store.js";
import { messageContext } from "../../js/ui/conversation.js";
import { auditHeadView, claimCard, familyChip } from "../../js/ui/governance.js";
import { relayErrorLine, withoutModelsPointer } from "../../js/ui/panels.js";
import { registerUiStrings } from "../../js/ui/strings.js";
import { envelopeSummary, jobCard } from "../../js/ui/toolcards.js";

registerUiStrings();
setLang("zh");
const here = path.dirname(fileURLToPath(import.meta.url));
const web = path.resolve(here, "../..");
const fixture = (n) => JSON.parse(fs.readFileSync(path.join(web, "dev/fixtures", `${n}.json`), "utf8"));

test("the Claims tab's weakest-link chip names the cited item's design, as the Evidence tab does", () => {
  const env = fixture("envelope_safety");
  const ev = evidenceOf(env);
  const [claim] = claimsOf(env);
  assert.equal(claim.weakest_tier, "expert_experience");
  const card = claimCard(claim, { evidence: ev });
  const chip = card.querySelector(".claim__weakest .fam__label").textContent;
  const cited = ev.find((e) => e.id === claim.supports[0]);
  const evidenceTab = familyChip(cited.family, { design: cited.design, tier: cited.tier }).querySelector(".fam__label").textContent;
  assert.equal(chip, evidenceTab);
  assert.equal(chip, `传统 · ${glossary.design("expert_consensus")}`);
  assert.doesNotMatch(chip, /名医经验/);
  // the compact card's note says the same
  const compact = claimCard(claim, { evidence: ev, compact: true });
  assert.match(compact.querySelector(".claim__notes").textContent, new RegExp(glossary.design("expert_consensus")));
});

test("a CLM005 refusal never shows ✓ on its licensing ladder", () => {
  const env = fixture("envelope_claims");
  const ev = evidenceOf(env);
  for (const claim of claimsOf(env).filter((c) => c.codes.includes("CLM005"))) {
    const ladder = claimCard(claim, { evidence: ev }).querySelector(".ladder");
    assert.ok(ladder.classList.contains("is-unlicensed"), claim.id);
    assert.equal(ladder.querySelector(".ladder__ok"), null, claim.id);
  }
});

test("[E#] resolves in this turn first, then in the earlier turns on the path; an id found nowhere stays unresolved", () => {
  const cite = (id, label) => ({ status: "succeeded", citations: [{ id, kind: "source", label }], governance: {} });
  const vm = (calls) => ({ segments: [{ type: "tools", step: 1, callIds: calls.map((c) => c[0]) }], tools: new Map(calls.map(([id, env]) => [id, { envelope: env }])) });
  const turn1 = vm([["a", cite("E1", "第一轮的 E1")], ["b", cite("E2", "第一轮的 E2")]]);
  const turn2 = vm([["c", cite("E3", "第二轮的 E3")]]);
  let read = 0;
  const ctx = messageContext(turn2, { earlier: () => { read += 1; return [turn1]; } });
  assert.equal(ctx.cite("E3").label, "第二轮的 E3");
  assert.equal(read, 0, "earlier turns are read only when needed");
  assert.equal(ctx.cite("E2").label, "第一轮的 E2");
  assert.equal(ctx.cite("E9"), null);
  ctx.cite("E1");
  assert.equal(read, 1, "and read once");
  // without the earlier turns nothing is guessed
  assert.equal(messageContext(turn2).cite("E2"), null);
  // an old record whose turns both number an E1: this turn's own wins
  const old = vm([["d", cite("E1", "本轮的 E1")]]);
  assert.equal(messageContext(old, { earlier: [turn1] }).cite("E1").label, "本轮的 E1");
});

test("the relay's error line: its own Chinese marked as such; on Settings → Models without the pointer to that page", () => {
  setLang("en");
  try {
    const line = relayErrorLine({ error: "中继出错了", error_lang: "zh" }, { tag: "p.notice__body" });
    assert.equal(line.querySelector("span[lang]").getAttribute("lang"), "zh-Hans");
    assert.match(line.textContent, /^The relay reports: 中继出错了$/);
    assert.equal(relayErrorLine({ error: "plain" }).querySelector("span").getAttribute("lang"), null);
  } finally {
    setLang("zh");
  }
  for (const l of ["zh", "en"]) {
    setLang(l);
    for (const type of ["not_found", "forbidden_origin", "not_configured", "bad_request", "too_large", "model_not_allowed", "daily_limit", "total_limit", "unavailable", "upstream_unreachable", "upstream_auth", "upstream_rate", "upstream_quota", "upstream_error", "relay_error", "not_relay"]) {
      const full = t(`core.relay.error.${type}`);
      const here = withoutModelsPointer(full);
      assert.ok(here.length > 8, `${l} ${type}: ${here}`);
      assert.doesNotMatch(here, /设置 → 模型|Settings → Model/, `${l} ${type}: ${here}`);
      assert.match(here, /[。.]$/, `${l} ${type} ends as a sentence: ${here}`);
    }
  }
  setLang("zh");
  // the runner's own messages too
  assert.doesNotMatch(withoutModelsPointer("本机 Runner 不提供 Tao-S1。请在默认端口 8765 启动 Runner，或在「设置 → 模型」中使用自己的模型 API 或本地模型。"), /设置/);
  assert.doesNotMatch(withoutModelsPointer("start the runner on its default port 8765, or use your own model API or a local model (Settings → Models)."), /Settings/);
  // a text with no pointer is left as it is
  assert.equal(withoutModelsPointer("HTTP 503"), "HTTP 503");
});

test("summary_en is shown on an English page, with the lang of the string shown", () => {
  const env = { summary: "甘草 + 甘遂：记载 1 处配伍禁忌", summary_en: "甘草 + 甘遂: 1 recorded incompatibility (the eighteen antagonisms and others)" };
  assert.deepEqual(envelopeSummary(env), { text: env.summary, lang: "zh-Hans" });
  setLang("en");
  try {
    assert.deepEqual(envelopeSummary(env), { text: env.summary_en, lang: "en" });
    // a page-made envelope without summary_en falls back to summary
    assert.equal(envelopeSummary({ summary: "已拒绝" }).text, "已拒绝");
    assert.deepEqual(envelopeSummary(null), { text: "", lang: null });
  } finally {
    setLang("zh");
  }
});

test("a finished job is not a release: its pill is neutral and says the hashes were checked", () => {
  const card = jobCard({ id: "j_1", kind: "skill.run", state: "succeeded", created_at: "2026-10-08T00:00:00Z", artefacts: [] });
  const pill = card.querySelector(".job__head .verdict");
  assert.ok(pill.classList.contains("verdict--neutral"));
  assert.ok(!pill.classList.contains("verdict--jade"));
  assert.equal(pill.textContent, "已完成（输出哈希已核验）");
});

test("release: unmet wording for release_authorized; the receipt's audit head is the runner's chain head, not the artifact's", () => {
  const env = fixture("envelope_unpinned");
  const rel = releaseOf(env);
  const auth = rel.states.find((s) => s.id === "release_authorized");
  assert.equal(auth.ok, false);
  for (const r of auth.reasons) assert.doesNotMatch(r, /已核验|已证明|结构有效/, r);
  assert.ok(Array.isArray(auth.unmet) && auth.unmet.length === auth.reasons.length);
  const art = env.governance?.artifact || {};
  assert.equal(rel.audit_head, art.audit_head || "");
  assert.equal(rel.chain_head, env.receipt?.audit_head || "");
  if (!art.audit_head && env.receipt?.audit_head) {
    const view = auditHeadView(env, rel);
    assert.match(view.textContent, new RegExp(t("ui.artifact.chain_head")));
  }
});

test("files.add takes the composer's digest and does not read the file again", async () => {
  const store = await openStore({ indexedDB: new IDBFactory() });
  const project = await store.projects.create({ name: "摘要" });
  // a Blob that fails if it is read: the digest must come from the composer
  class Unread extends Blob {
    arrayBuffer() { throw new Error("read twice"); }
    stream() { throw new Error("read twice"); }
    text() { throw new Error("read twice"); }
  }
  const unread = new Unread(["abc"], { type: "application/octet-stream" });
  const digest = "ab".repeat(32);
  const rec = await store.files.add(project.id, unread, { name: "big.bin", sha256: digest.toUpperCase() });
  assert.equal(rec.sha256, digest);
  // without one (or with a malformed one) it is computed
  const real = new Blob(["甘草"], { type: "text/plain" });
  const computed = await store.files.add(project.id, real, { name: "a.txt", sha256: "not-a-digest" });
  assert.match(computed.sha256, /^[0-9a-f]{64}$/);
  assert.notEqual(computed.sha256, "not-a-digest");
});

test("UNPINNED has its own explanation in the glossary", () => {
  assert.match(glossary.code("UNPINNED"), /候选 Skill/);
  setLang("en");
  try {
    assert.match(glossary.code("UNPINNED"), /Candidate Skill/);
  } finally {
    setLang("zh");
  }
});
