// UI layer tests: strings (zh/en complete, microcopy rules), routes, the DOM helper and formatters, the governance
// components over real-run fixtures, tool cards, the live-turn model, the composer's IME-safe Enter, file previews.
// Run: node --test web/dev/test/*.test.mjs (from studio/).
import { window } from "./setup.mjs";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { claimsOf, evidenceOf, releaseOf } from "../../js/core/governance.js";
import { setLang, t } from "../../js/core/i18n.js";
import { renderMarkdown } from "../../js/core/markdown.js";
import { applyEvent, autoTitle, layoutMode } from "../../js/ui/app.js";
import { answerMarkdown, messageContext } from "../../js/ui/conversation.js";
import { dateGroup, formatBytes, formatDuration, h } from "../../js/ui/dom.js";
import { parseDelimited } from "../../js/ui/files.js";
import {
  artifactCard, citationChip, claimCard, evidenceTable, familyChip, hashBadge, licensingLadderView, qualityPills, releaseChecklist,
} from "../../js/ui/governance.js";
import { ICON_NAMES, icon } from "../../js/ui/icons.js";
import { reproduceCommand } from "../../js/ui/inspector.js";
import { SHORTCUTS } from "../../js/ui/palette.js";
import { EXAMPLES } from "../../js/ui/pages/project.js";
import { parseRoute, routeHref } from "../../js/ui/router.js";
import { EN, registerUiStrings, ZH } from "../../js/ui/strings.js";
import { argsSummary, jobCard, permissionCard, toolCallCard } from "../../js/ui/toolcards.js";

registerUiStrings();
const here = path.dirname(fileURLToPath(import.meta.url));
const web = path.resolve(here, "../..");
const FIX = Object.fromEntries(fs.readdirSync(path.join(web, "dev/fixtures")).filter((f) => f.endsWith(".json"))
  .map((f) => [f.replace(/\.json$/, ""), JSON.parse(fs.readFileSync(path.join(web, "dev/fixtures", f), "utf8"))]));

// ------------------------------------------------------------------------------------------------- strings

function sourceFiles(dir) {
  const out = [];
  for (const f of fs.readdirSync(dir)) {
    const p = path.join(dir, f);
    if (fs.statSync(p).isDirectory()) out.push(...sourceFiles(p));
    else if (p.endsWith(".js")) out.push(p);
  }
  return out;
}

test("every UI string key used in the code exists in zh and en", () => {
  const files = [...sourceFiles(path.join(web, "js/ui")), ...sourceFiles(path.join(web, "dev")).filter((f) => !f.includes(`${path.sep}test${path.sep}`)), path.join(web, "js/main.js")];
  const used = new Set();
  for (const f of files) for (const m of fs.readFileSync(f, "utf8").matchAll(/\bt\(\s*"(ui\.[A-Za-z0-9_.]+)"/g)) used.add(m[1]);
  for (const m of fs.readFileSync(path.join(web, "index.html"), "utf8").matchAll(/data-i18n="(ui\.[^"]+)"/g)) used.add(m[1]);
  const missingZh = [...used].filter((k) => !(k in ZH));
  const missingEn = [...used].filter((k) => !(k in EN));
  assert.deepEqual(missingZh, [], `missing zh: ${missingZh.join(", ")}`);
  assert.deepEqual(missingEn, [], `missing en: ${missingEn.join(", ")}`);
  assert.ok(used.size > 500);
});

test("zh and en have the same keys and the same placeholders", () => {
  assert.deepEqual(Object.keys(ZH).sort(), Object.keys(EN).sort());
  for (const k of Object.keys(ZH)) {
    const ph = (s) => [...String(s).matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort();
    assert.deepEqual(ph(ZH[k]), ph(EN[k]), `placeholders differ for ${k}`);
  }
});

test("dynamic key families are complete", () => {
  const need = [
    ...["run", "evidence", "claims", "files", "provenance"].map((x) => `ui.inspector.tab.${x}`),
    ...["idle", "offline", "connecting", "ready", "error"].map((x) => `ui.runner.status.${x}`),
    ...["idle", "loading", "connecting", "ready", "error", "offline"].map((x) => `ui.browser.status.${x}`),
    ...["pinned", "today", "yesterday", "week", "older"].map((x) => `ui.sidebar.group.${x}`),
    ...EXAMPLES.flatMap((e) => [`ui.examples.${e.id}.prompt`, `ui.examples.${e.id}.shows`]),
    ...SHORTCUTS.map(([id]) => `ui.shortcuts.${id}`),
    ...["recent", "projects", "conversations", "messages", "commands", "settings"].map((x) => `ui.palette.group.${x}`),
    ...["all", "allowed", "refused", "declaration", "extrapolation"].map((x) => `ui.claims.filter.${x}`),
    ...["native", "skill", "connector", "clinic", "tcmdb", "study", "job", "system"].flatMap((x) => [`ui.run.gov.${x}`, `ui.catalog.kind.${x}`]),
    ...["language", "model", "compute"].map((x) => `ui.onb.step.${x}`),
    ...["models", "compute", "general", "shortcuts"].map((x) => `ui.settings.tab.${x}`),
    ...["auto", "browser", "runner"].map((x) => `ui.compute.target_help.${x}`),
    ...["system", "light", "dark"].map((x) => `ui.theme.${x}`),
    ...["model", "network", "runtime"].map((x) => `ui.thread.error.${x}`),
    ...["once", "project", "deny"].map((x) => `ui.perm.decided.${x}`),
    ...["schema_valid", "evidence_verified", "outputs_verified", "execution_declared", "execution_attested", "release_authorized"].map((x) => `ui.release.unmet.${x}`),
    ...["checking", "ready", "down"].map((x) => `ui.onb.model_title.${x}`),
    ...["run", "evidence", "claims", "provenance"].map((x) => `ui.inspector.empty_turn.${x}`),
    "ui.code.UNPINNED",
    ...["submitted", "running", "collected"].map((x) => `ui.job.step.${x}`),
    ...["runtime", "skill", "source", "benchmark"].map((x) => `ui.cv.${x}`),
    ...[1, 2, 3, 4].map((i) => `ui.about.what.${i}`), ...[1, 2, 3, 4].map((i) => `ui.about.where.${i}`), ...[1, 2, 3, 4, 5].map((i) => `ui.about.not.${i}`),
  ];
  const missing = need.filter((k) => !(k in ZH) || !(k in EN));
  assert.deepEqual(missing, []);
});

test("microcopy: no exclamation marks, no apologies, refusals are not errors (DESIGN §7)", () => {
  for (const [k, v] of [...Object.entries(ZH), ...Object.entries(EN)]) {
    assert.ok(!/[!！]/.test(v), `exclamation mark in ${k}: ${v}`);
    assert.ok(!/抱歉|对不起|出错了|\bsorry\b|\boops\b/i.test(v), `apology or "出错了" in ${k}: ${v}`);
    assert.ok(!/革命性|全球首个|精准|确认有效|发现了机制/.test(v), `overclaiming word in ${k}: ${v}`);
  }
  assert.equal(ZH["ui.models.key_note"], "密钥只保存在本浏览器，不会发送到 science.impf.ai。清除浏览器数据会删除它。");
  assert.equal(ZH["ui.runner.not_connected"], "未连接本机 Runner。浏览器内计算仍可用；大文件和 GPU 任务需要 Runner。");
});

test("the Tao-S1 card never names the upstream vendor", () => {
  const all = JSON.stringify([ZH, EN]);
  assert.ok(!/minimax/i.test(all));
  for (const f of sourceFiles(path.join(web, "js/ui"))) assert.ok(!/minimax/i.test(fs.readFileSync(f, "utf8")), f);
});

// ------------------------------------------------------------------------------------------------- routes, DOM, format

test("routes parse and print both ways", () => {
  for (const r of [
    { name: "home" }, { name: "project", projectId: "p 1" }, { name: "conversation", projectId: "p1", convId: "c/2" },
    { name: "settings", tab: "compute" }, { name: "about" }, { name: "catalog" }, { name: "catalog", entryId: "native.reverse_complement" },
  ]) {
    const back = parseRoute(routeHref(r));
    for (const [k, v] of Object.entries(r)) assert.equal(back[k], v, `${k} of ${routeHref(r)}`);
  }
  assert.equal(parseRoute("#/settings/nope").tab, "models");
  assert.equal(parseRoute("#/x/y").name, "notfound");
  assert.equal(parseRoute("").name, "home");
});

test("h() builds elements and drops null children", () => {
  const el = h("button.a.b#x", { class: ["c", false, "d e"], type: "button", "data-tip": null, disabled: false, dataset: { k: 1 } }, null, "text", [h("span", "s"), undefined], 0);
  assert.equal(el.tagName, "BUTTON");
  assert.equal(el.id, "x");
  assert.deepEqual([...el.classList].sort(), ["a", "b", "c", "d", "e"]);
  assert.equal(el.getAttribute("data-tip"), null);
  assert.equal(el.hasAttribute("disabled"), false);
  assert.equal(el.dataset.k, "1");
  assert.equal(el.textContent, "texts0");
});

test("formatters carry units in both languages", () => {
  setLang("zh");
  assert.equal(formatBytes(1579), "1,579 字节");
  assert.equal(formatBytes(37 * 1024), "37.0 KB");
  assert.equal(formatBytes(12.4 * 1024 * 1024), "12.4 MB");
  assert.equal(formatDuration(840), "840 毫秒");
  assert.equal(formatDuration(3200), "3.2 秒");
  assert.equal(formatDuration(134000), "2 分 14 秒");
  setLang("en");
  assert.equal(formatBytes(1579), "1,579 bytes");
  assert.equal(formatDuration(3200), "3.2 s");
  setLang("zh");
  const now = Date.parse("2026-10-07T12:00:00");
  assert.equal(dateGroup(now - 3600e3, now), "today");
  assert.equal(dateGroup(now - 86400e3, now), "yesterday");
  assert.equal(dateGroup(now - 4 * 86400e3, now), "week");
  assert.equal(dateGroup(now - 30 * 86400e3, now), "older");
});

test("icons render at 1.5 px for any size and are decorative unless labelled", () => {
  assert.ok(ICON_NAMES.length > 100);
  const a = icon("check", { size: 16 });
  assert.equal(a.getAttribute("aria-hidden"), "true");
  assert.equal(Number(a.getAttribute("stroke-width")), 2.25);
  const b = icon("hexagon", { size: 14, dashed: true, label: "预测" });
  assert.equal(b.getAttribute("role"), "img");
  assert.ok(b.getAttribute("stroke-dasharray"));
});

// ------------------------------------------------------------------------------------------------- governance

test("hash badge shows first 8 and last 4 and names the full value", () => {
  const full = "0da551c093b58f51724d7ab206890c783280d79b008fd7d1ac9c5b45fe1f626d";
  const b = hashBadge(full);
  assert.match(b.textContent, /sha256:0da551c0…626d/);
  assert.match(b.getAttribute("aria-label"), new RegExp(full));
});

test("evidence families carry icon and words; predicted is marked", () => {
  const p = familyChip("predicted", { design: "docking" });
  assert.ok(p.classList.contains("fam--predicted"));
  assert.match(p.textContent, /预测 · 分子对接/);
  assert.ok(p.querySelector("svg[stroke-dasharray]"));
  assert.match(familyChip("tradition", { design: "classical_text" }).textContent, /经典文献$/);
  assert.match(familyChip("clinical", { design: "randomized_trial" }).textContent, /临床 · 随机对照试验/);
});

test("refused claims are rendered as results with their codes and the ladder (real kernel verdicts)", () => {
  const env = FIX.envelope_claims;
  const claims = claimsOf(env);
  const ev = evidenceOf(env);
  const refused = claims.filter((c) => c.allowed === false);
  assert.equal(refused.length, 2);
  const card = claimCard(refused[0], { evidence: ev });
  assert.ok(card.classList.contains("claim--refused"));
  assert.match(card.textContent, /CLM005/);
  assert.match(card.textContent, /已拒绝/);
  assert.ok(card.querySelector(".ladder"));
  assert.match(card.querySelector(".ladder__caption").textContent, /不支撑/);
  const allowed = claims.find((c) => c.allowed);
  const ok = claimCard(allowed, { evidence: ev });
  assert.ok(!ok.classList.contains("claim--refused"));
  assert.match(ok.textContent, /允许/);
  // a compact card lists each code once
  const compact = claimCard(refused[0], { evidence: ev, compact: true });
  const codes = [...compact.querySelectorAll(".reasons .code-chip")].map((x) => x.textContent);
  assert.equal(new Set(codes).size, codes.length);
});

test("licensing ladder: eight cells, licensed set, weakest marked, result in words", () => {
  const l = licensingLadderView("efficacy", "preclinical", { codes: ["CLM005"] });
  assert.equal(l.querySelectorAll(".ladder__cell").length, 8);
  assert.equal(l.querySelectorAll(".ladder__cell.is-licensed").length, 2);
  assert.equal(l.querySelectorAll(".ladder__cell.is-weakest").length, 1);
  assert.match(l.textContent, /不支撑（CLM005）/);
});

test("release checklist: six states, a green summary only when authorized", () => {
  const ok = releaseChecklist(releaseOf(FIX.envelope_safety));
  assert.equal(ok.querySelectorAll(".release__row").length, 6);
  assert.equal(ok.querySelectorAll(".release__row.is-ok").length, 6);
  assert.match(ok.textContent, /发布状态 6\/6 · 已准予发布/);
  const partial = releaseChecklist(releaseOf(FIX.envelope_network_unattested));
  assert.match(partial.textContent, /发布状态 2\/6 · 未准予发布/);
  assert.ok(!partial.querySelector(".verdict--released"));
  assert.match(partial.textContent, /not checked/);
});

test("artifact card always shows limitations and the composite version", () => {
  const card = artifactCard(FIX.envelope_network);
  assert.match(card.textContent, /局限 \d+ 条/);
  assert.match(card.textContent, /复合版本/);
  assert.match(card.textContent, /未版本化/);
  assert.match(card.textContent, /network\.json/);
});

test("quality pills never total; unassessed stays visible", () => {
  const ev = evidenceOf(FIX.envelope_claims)[0];
  const q = qualityPills(ev.quality);
  assert.equal(q.querySelectorAll(".quality__pill").length, 4);
  assert.ok(q.querySelectorAll(".is-unassessed").length >= 1);
  assert.ok(!/总分|total|score/i.test(q.textContent));
  const table = evidenceTable(evidenceOf(FIX.envelope_safety));
  assert.equal(table.querySelectorAll("tbody tr").length, evidenceOf(FIX.envelope_safety).length);
});

test("citation chips are coloured by family and markdown citations resolve to them", () => {
  const ctx = messageContext({ segments: [{ type: "tools", step: 1, callIds: ["a"] }], tools: new Map([["a", { envelope: FIX.envelope_safety }]]) });
  const c = ctx.cites.get("E1");
  assert.ok(c);
  const chip = citationChip(c, { evidence: ctx.evidenceFor("E1") });
  assert.ok(chip.classList.contains("fam--tradition"));
  const frag = renderMarkdown("记载为十八反 [E1]，另见 [E9]。", { onCitation: (id) => citationChip(ctx.cites.get(id) || null, { evidence: ctx.evidenceFor(id) }) });
  const div = h("div", frag);
  assert.equal(div.querySelectorAll(".cite").length, 2);
  assert.equal(div.querySelectorAll(".cite.is-unknown").length, 1);
});

test("copying an answer lists the citations it uses", () => {
  const vm = { segments: [{ type: "tools", step: 1, callIds: ["a"] }, { type: "text", step: 2, text: "记载为十八反 [E1]。" }], tools: new Map([["a", { envelope: FIX.envelope_safety }]]) };
  const md = answerMarkdown(null, vm);
  assert.match(md, /记载为十八反 \[E1\]/);
  assert.match(md, /- \[E1\] /);
});

// ------------------------------------------------------------------------------------------------- run transparency

test("tool card: where it ran, status, refusals as results, page-decided calls marked as not run", () => {
  const ok = toolCallCard({ id: "1", name: "tcm_safety_report", args: { subject: "甘草" } }, FIX.envelope_safety);
  assert.match(ok.textContent, /本机 Runner/);
  assert.match(ok.textContent, /已准予发布/);
  // the person declined: their decision, in neutral words — not the kernel's vermilion 已拒绝 (DESIGN §7.1 #4)
  const denied = toolCallCard({ id: "2", name: "network_pharmacology_run", args: {} }, FIX.envelope_denied, { open: true });
  assert.ok(denied.classList.contains("tool-card--declined"));
  assert.ok(!denied.classList.contains("tool-card--refused"));
  assert.ok(!denied.querySelector(".tool-card__refused"));
  assert.match(denied.textContent, /未批准 · 未运行/);
  assert.ok(!/the user declined/.test(denied.textContent), "model-facing hints are not shown");
  const running = toolCallCard({ id: "3", name: "tcm_evidence", args: {}, where: "runner", status: "running" }, null);
  assert.match(running.textContent, /正在本机运行 tcm_evidence/);
  running.update(null, FIX.envelope_compatibility);
  assert.match(running.textContent, /配伍冲突|配伍禁忌/);
  assert.equal(argsSummary({ herbs: ["甘草", "甘遂"] }), "herbs: 甘草、甘遂");
});

test("permission card offers once / project / deny and records the answer", () => {
  let got = null;
  const card = permissionCard(FIX.approvals[0], { onDecide: (d) => { got = d; } });
  assert.match(card.textContent, /www\.ebi\.ac\.uk/);
  assert.match(card.textContent, /第一次在本机 Runner/);
  const buttons = [...card.querySelectorAll("button")];
  assert.deepEqual(buttons.map((b) => b.textContent), ["允许一次", "本项目允许", "拒绝"]);
  buttons[2].dispatchEvent(new window.Event("click"));
  assert.equal(got, "deny");
  assert.match(card.textContent, /未批准 · 未运行/);
  assert.ok(!/已拒绝/.test(card.textContent), "a person's decline is not worded as a kernel refusal");
  assert.equal(card.querySelector(".permission__decided").getAttribute("tabindex"), "-1", "the decision line can take the focus");
});

test("job card never shows outputs before the job is collected", () => {
  const running = jobCard(FIX.jobs.running);
  assert.ok(!running.querySelector(".job__files"));
  assert.match(running.textContent, /不会作为结果/);
  const done = jobCard(FIX.jobs.succeeded);
  assert.ok(done.querySelector(".job__files"));
  assert.match(done.textContent, /已收集并核验/);
});

test("the live turn folds agent events into segments, tools and thinking time", () => {
  const live = { segments: [], tools: new Map(), pending: new Map(), thinkingMs: {}, thinkingStart: {}, status: "streaming" };
  applyEvent(live, { type: "model.start", step: 1 });
  applyEvent(live, { type: "reasoning", step: 1, delta: "先查" });
  applyEvent(live, { type: "reasoning", step: 1, delta: "配伍" });
  applyEvent(live, { type: "tool.pending", step: 1, index: 0, name: "tcm_compatibility" });
  applyEvent(live, { type: "tool.start", step: 1, callId: "c1", name: "tcm_compatibility", args: {}, where: "browser" });
  applyEvent(live, { type: "tool.end", step: 1, callId: "c1", envelope: FIX.envelope_compatibility });
  applyEvent(live, { type: "model.start", step: 2 });
  applyEvent(live, { type: "text", step: 2, delta: "记载" });
  applyEvent(live, { type: "text", step: 2, delta: "为十八反" });
  applyEvent(live, { type: "done", status: "ok" });
  assert.deepEqual(live.segments.map((s) => s.type), ["reasoning", "tools", "text"]);
  assert.equal(live.segments[0].text, "先查配伍");
  assert.equal(live.segments[2].text, "记载为十八反");
  assert.equal(live.pending.size, 0);
  assert.equal(live.tools.get("c1").call.status, "succeeded");
  assert.equal(typeof live.thinkingMs[1], "number");
  // a retried step drops its partial text
  applyEvent(live, { type: "model.retry", step: 2, notice: "retry" });
  assert.deepEqual(live.segments.map((s) => s.type), ["reasoning", "tools"]);
});

test("titles, layout modes, the reproduce command and delimited previews", () => {
  assert.equal(autoTitle("  \n甘草与甘遂同用，有哪些记载？\n第二行"), "甘草与甘遂同用，有哪些记载？");
  assert.equal([...autoTitle("一".repeat(80))].length, 32);
  assert.equal(layoutMode(1440), "wide");
  assert.equal(layoutMode(1100), "medium");
  assert.equal(layoutMode(900), "narrow");
  assert.equal(layoutMode(390), "mobile");
  const cmd = reproduceCommand({ tool: "tcm_safety_report" }, { args: { subject: "it's 甘草" } });
  assert.match(cmd, /^python3 -m tcmstudio call tcm_safety_report --where runner --args /);
  assert.match(cmd, /'\{"subject":"it'\\''s 甘草"\}'$/);
  assert.deepEqual(parseDelimited('a,b\n1,"x, y"\n2,"q""uote"\n'), [["a", "b"], ["1", "x, y"], ["2", 'q"uote']]);
  assert.deepEqual(parseDelimited("g\tv\nTNF\t-1.4\n", "\t"), [["g", "v"], ["TNF", "-1.4"]]);
});

// ------------------------------------------------------------------------------------------------- composer

test("composer: Enter sends, but never while an IME is composing; Shift+Enter is a newline", async () => {
  const { mountComposer } = await import("../../js/ui/composer.js");
  const sent = [];
  const listeners = new Map();
  const app = {
    state: { layout: "wide", turn: null, conversation: null, route: { name: "project" }, settings: { compute: "auto", runner: { url: "" } }, runner: { status: "idle" }, project: null },
    on: (type, fn) => { listeners.set(type, fn); return () => {}; }, emit: () => {},
    send: async (text) => { sent.push(text); return true; },
    webOn: () => false, compute: () => "auto", provider: () => ({ relay: true, label: "Tao-S1", model: "Tao-S1" }),
    stop: () => true,
  };
  const c = mountComposer(app, { variant: "dock" });
  document.body.append(c.el);
  const ta = c.el.querySelector("textarea");
  const key = (init) => { const e = new KeyboardEvent("keydown", { bubbles: true, cancelable: true, ...init }); ta.dispatchEvent(e); return e; };
  ta.value = "生姜还是干姜";
  key({ key: "Enter", isComposing: true });
  key({ key: "Enter", keyCode: 229 });
  key({ key: "Enter", shiftKey: true });
  await new Promise((r) => setTimeout(r, 5));
  assert.deepEqual(sent, []);
  key({ key: "Enter" });
  await new Promise((r) => setTimeout(r, 5));
  assert.deepEqual(sent, ["生姜还是干姜"]);
  assert.equal(ta.value, "");
  c.destroy();
});

test("t() falls back to the key, so a missing string is visible in review", () => {
  assert.equal(t("ui.no.such.key"), "ui.no.such.key");
});
