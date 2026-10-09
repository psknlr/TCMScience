// The design-QA gallery: every component, rendered with fixture envelopes made from real TCMScience runs
// (dev/fixtures/make_fixtures.py). Theme and language switch live, so both are checked on the same page.
// Open /dev/gallery.html (append ?lang=en or #section to jump).

import { citationsOf, claimsOf, evidenceOf, releaseOf, verdictBadge } from "../js/core/governance.js";
import { lang, setLang, t } from "../js/core/i18n.js";
import { renderMarkdown } from "../js/core/markdown.js";
import { loadSettings, updateSettings } from "../js/core/settings.js";
import { h, fill } from "../js/ui/dom.js";
import {
  artifactCard, citationChip, claimCard, codeChip, evidenceGroups, evidenceItem, evidenceTable, familyChip, hashBadge,
  licensingLadderView, qualityPills, releaseChecklist, sourceCard, verdictPill,
} from "../js/ui/governance.js";
import { ICON_NAMES, icon } from "../js/ui/icons.js";
import { installTooltips, openDialog, openMenu, openPopover, toast } from "../js/ui/overlay.js";
import {
  button, chip, copyButton, disclosure, emptyState, iconButton, kbd, keyValue, notice, progressBar, section, segmented, selectField,
  slider, spinner, statusDot, switchControl, tabList, textArea, textField,
} from "../js/ui/primitives.js";
import { registerUiStrings } from "../js/ui/strings.js";
import { jobCard, permissionCard, thinkingBlock, toolCallCard } from "../js/ui/toolcards.js";
import { loadFixtures } from "./demo.js";

registerUiStrings();
installTooltips();

let FIX = {};

/** Sections registered by other modules (thread, composer, inspector, pages) once they exist. */
export const extraSections = [];

async function load() {
  FIX = await loadFixtures();
  try {
    const mod = await import("./gallery-app.js");
    extraSections.push(...(mod.sections || []));
  } catch (err) {
    if (!/Failed to fetch|Cannot find|404|Importing a module/i.test(String(err?.message))) console.error(err);
  }
  render();
}

function render() {
  const root = document.getElementById("gallery");
  const sections = [
    ["tokens", "Tokens", tokensSection],
    ["primitives", t("ui.gallery.primitives"), primitivesSection],
    ["governance", t("ui.gallery.governance"), governanceSection],
    ["claims", t("ui.gallery.claims"), claimsSection],
    ["artifacts", t("ui.gallery.artifacts"), artifactsSection],
    ["evidence", t("ui.gallery.evidence"), evidenceSection],
    ["run", t("ui.gallery.run"), runSection],
    ["jobs", t("ui.gallery.jobs"), jobsSection],
    ...extraSections.map((s) => [s.id, s.title(), () => s.render(FIX)]),
  ];
  fill(root,
    topBar(sections),
    sections.map(([id, title, fn]) => h("section.g-section", { id }, h("h2", title), fn())));
  root.removeAttribute("aria-busy");
  if (location.hash) document.getElementById(location.hash.slice(1))?.scrollIntoView();
}

function topBar(sections) {
  const s = loadSettings();
  return h("header.g-top",
    h("h1", h("span.wordmark", h("span.wordmark__tcm", "TCM", h("span.wordmark__dot", "·"), "Science"), " ", h("span.wordmark__studio", "Studio")), h("small", t("ui.gallery.title"))),
    h("nav.g-nav", sections.map(([id, title]) => h("a", { href: `#${id}` }, title))),
    segmented({
      label: "Theme", size: "sm", value: s.theme,
      options: [{ value: "system", label: t("ui.theme.system") }, { value: "light", label: t("ui.theme.light") }, { value: "dark", label: t("ui.theme.dark") }],
      onChange: (v) => {
        updateSettings({ theme: v });
        if (v === "system") document.documentElement.removeAttribute("data-theme");
        else document.documentElement.setAttribute("data-theme", v);
      },
    }),
    segmented({
      label: "Language", size: "sm", value: lang(),
      options: [{ value: "zh", label: "中" }, { value: "en", label: "EN" }],
      onChange: (v) => { setLang(v); render(); },
    }));
}

const g = (label, ...children) => h("div.g-cell", h("div.g-label", label), ...children);

// ------------------------------------------------------------------------------------------------- tokens

function tokensSection() {
  const names = ["bg", "bg-sidebar", "surface", "surface-2", "sunken", "ink", "ink-2", "ink-3", "ink-4", "rule", "control-border",
    "accent", "accent-soft", "jade", "jade-soft", "ochre", "ochre-ink", "ochre-soft", "vermilion", "vermilion-soft", "slate", "slate-soft"];
  return h("div",
    h("p.g-lede", t("ui.gallery.tokens_lede")),
    h("div.g-swatches", names.map((n) => h("div.g-swatch", h("div.g-swatch__chip", { style: { background: `var(--${n})` } }), h("div.g-swatch__name", `--${n}`)))),
    h("div.g-panel.g-type", { style: "margin-top:20px" },
      h("p.kicker", t("ui.gallery.kicker")),
      h("p.serif", { style: "font-size:36px;font-weight:600;line-height:1.2" }, "从经典知识到可验证证据"),
      h("p.serif", { style: "font-size:28px;font-weight:600" }, "葛根芩连汤 · 网络药理学复核"),
      h("p", { style: "font-size:16px" }, "Inter 16 · The model may reason. It does not define the laws of the laboratory."),
      h("div.prose", { lang: "zh-Hans" }, h("p", "让语言模型推理，但不让它定义实验室的法则。甘草与甘遂同用，记载为十八反配伍禁忌；这是一条记载，不是临床安全性结论。无记录（不等于安全）。")),
      h("p.mono", "sha256:0da551c0…626d · CLM005 · native.reverse_complement"),
      h("p", ICON_NAMES.map((n) => h("span", { "data-tip": n, style: "display:inline-block;padding:4px;color:var(--ink-2)" }, icon(n))))));
}

// ------------------------------------------------------------------------------------------------- primitives

function primitivesSection() {
  const tabs = tabList({ label: "demo", value: "run", tabs: [
    { id: "run", label: "过程", icon: "activity", count: 4 }, { id: "evidence", label: "证据", icon: "bookText", count: 6 },
    { id: "claims", label: "主张", icon: "scale", count: 3 }, { id: "files", label: "文件", icon: "files" }, { id: "prov", label: "溯源", icon: "fingerprint" }] });
  const menuBtn = button({ label: t("ui.gallery.open_menu"), iconAfter: "chevronDown", onClick: (e) => openMenu(e.currentTarget, [
    { label: "重命名", icon: "rename", onSelect: () => toast("重命名") }, { label: "归档", icon: "archive", onSelect: () => {} }, "-",
    { label: "删除", icon: "trash", danger: true, onSelect: () => {} }]) });
  const popBtn = button({ label: t("ui.gallery.open_popover"), onClick: (e) => openPopover(e.currentTarget, h("div", { style: "padding:8px 10px;max-width:280px" }, h("p", { style: "font-weight:600" }, "Popover"), h("p.muted.small", "Esc 关闭，点击外部关闭，焦点回到触发按钮。")), { width: 300 }) });
  const dlgBtn = button({ label: t("ui.gallery.open_dialog"), onClick: () => {
    const d = openDialog({ title: "对话框", body: h("p.dialog__text", "焦点被限制在对话框内；Esc 关闭；关闭后焦点回到触发按钮。"),
      actions: [button({ label: t("ui.action.cancel"), onClick: () => d.close() }), button({ label: t("ui.action.confirm"), variant: "primary", onClick: () => d.close() })] });
  } });
  return h("div.g-grid",
    g("buttons", h("div.g-row", button({ label: "运行", variant: "primary", icon: "play" }), button({ label: "复制", icon: "copy" }), button({ label: "重试", variant: "ghost", icon: "regenerate" }), button({ label: "删除", variant: "danger" })),
      h("div.g-row", button({ label: "允许一次", variant: "primary", size: "sm" }), button({ label: "本项目允许", size: "sm" }), button({ label: "拒绝", variant: "ghost", size: "sm" }), button({ label: "新建会话", size: "sm", kbd: "mod+shift+o" }))),
    g("icon buttons", h("div.g-row", iconButton({ icon: "sidebar", label: "侧栏", kbd: "mod+shift+s" }), iconButton({ icon: "inspector", label: "检查器", pressed: true }), iconButton({ icon: "copy", label: "复制", size: "sm" }), iconButton({ icon: "more", label: "更多", variant: "secondary" }), copyButton("hello"))),
    g("chips · kbd · dots", h("div.g-row", chip({ label: "中性" }), chip({ label: "已核验", tone: "jade", icon: "check" }), chip({ label: "navy", tone: "navy" }), chip({ label: "ochre", tone: "ochre" }), chip({ label: "已撤稿", tone: "vermilion" }), chip({ label: "未核查撤稿", tone: "slate", dashed: true }), chip({ label: "pmid:32112345", mono: true })),
      h("div.g-row", kbd("mod+k"), kbd("alt+backslash"), kbd("mod+shift+o"), kbd("enter"), statusDot("ok", "connected"), statusDot("busy", "busy"), statusDot("off", "off"), statusDot("error", "error"), statusDot("loading", "loading"), spinner({ size: 14 }))),
    g("switch · segmented", switchControl({ label: "联网", description: "本项目可以访问网络上的数据源（每个新主机仍需批准）。", checked: false }),
      switchControl({ label: "记住密钥", description: "保存在本浏览器的 localStorage 中。", checked: true }),
      h("div.g-row", segmented({ label: "compute", value: "auto", options: [{ value: "auto", label: "自动" }, { value: "browser", label: "浏览器", icon: "globe" }, { value: "runner", label: "本机 Runner", icon: "laptop" }] }))),
    g("fields", textField({ label: "Base URL", value: "https://api.deepseek.com/v1", mono: true, help: "OpenAI 兼容接口地址。" }),
      textField({ label: "API Key", type: "password", value: "sk-xxxxxxxxxxxx", help: "密钥只保存在本浏览器，不会发送到 science.impf.ai。清除浏览器数据会删除它。" }),
      selectField({ label: "模型", value: "deepseek-chat", options: [{ value: "deepseek-chat", label: "deepseek-chat" }, { value: "deepseek-reasoner", label: "deepseek-reasoner" }] }),
      slider({ label: "CPU 线程", min: 1, max: 16, value: 4, format: (v) => `${v} 线程` }),
      textArea({ label: "项目说明", value: "回答时区分记载、预测与实测。", rows: 3 })),
    g("tabs (roving tabindex)", tabs),
    g("disclosure · progress", disclosure({ summary: "完整参数（JSON）", content: h("pre.code-block", '{\n  "subject": "甘草",\n  "co_administered": ["甘遂"]\n}') }),
      progressBar({ value: 0.42, label: "progress" }), progressBar({ value: null, label: "loading" })),
    g("overlays", h("div.g-row", menuBtn, popBtn, dlgBtn, button({ label: "Toast", onClick: () => toast("已复制完整哈希", { tone: "ok" }) }))),
    g("notices", notice({ tone: "info", title: "未连接本机 Runner", body: "浏览器内计算仍可用；大文件和 GPU 任务需要 Runner。", actions: [button({ label: "连接 Runner", size: "sm" })] }),
      notice({ tone: "warn", body: "浏览器接下来会询问是否允许本站访问「本机上的应用」。请选择允许。数据不会离开你的电脑。" }),
      notice({ tone: "refusal", title: "已拒绝（CLM005）", body: "疗效主张需要随机对照试验或系统评价，本主张最弱的证据是临床前研究。" })),
    g("kv · empty", keyValue([["policy_id", h("code.kv__code", "tcmstudio.default/1")], ["audit_head", hashBadge("7333f2464569c38d8638fc419cd518da787e851ef5323c9c4438f6b1b25c1066", { prefix: "" })], ["设备", "CPU · 8 核"]]),
      emptyState({ icon: "bookText", title: "还没有证据", body: "运行工具后，这里会列出每条证据、它的种类和来源快照。" })));
}

// ------------------------------------------------------------------------------------------------- governance

function governanceSection() {
  const ev = evidenceOf(FIX.envelope_claims);
  const evSafety = evidenceOf(FIX.envelope_safety);
  const cites = citationsOf(FIX.envelope_safety);
  const evById = new Map(evSafety.map((e) => [e.id, e]));
  const citesClaims = citationsOf(FIX.envelope_claims);
  const evClaims = new Map(ev.map((e) => [e.id, e]));
  const prose = renderMarkdown(
    "甘草与甘遂同用，在种子语料中**记载**为十八反配伍禁忌 [E1]。另有 4 条与甘草相关的安全性记录，其中 3 条为高或严重级别 [E2]；这些是名医经验层级的记载，不是临床安全性数据，**无记录不等于安全**。\n\n葛根素在细胞实验中降低 TNF-α 分泌 [E1]，对接预测其可与 TNF 结合 [E2]——后者是**预测**，最多支撑机制假说。引用 [E9] 在本轮中不存在。",
    { onCitation: () => null });
  // swap the default chips for family-coloured ones
  let k = 0;
  prose.querySelectorAll("[data-cite]").forEach((node) => {
    const id = node.getAttribute("data-cite");
    const useClaims = k++ >= 2;
    const c = (useClaims ? citesClaims : cites).find((x) => x.id === id);
    const e = c ? (useClaims ? evClaims : evById).get(c.evidence_ref) : null;
    node.replaceWith(citationChip(c, { evidence: e, onActivate: (x) => toast(`${x?.id} → 证据`) }));
  });
  return h("div",
    h("div.g-grid",
      g("hash badges", h("div.g-row", hashBadge("0da551c093b58f51724d7ab206890c783280d79b008fd7d1ac9c5b45fe1f626d"), hashBadge("5a12df2abe348b70547efb3529853cc6cb40c0495bce4eb5db9c732cf306a87e", { label: "Skill", compact: true }), hashBadge(""))),
      g("evidence families (§6.1)", h("div.g-row", familyChip("tradition", { design: "classical_text" }), familyChip("tradition", { tier: "expert_experience" }), familyChip("bench", { design: "in_vitro" }), familyChip("clinical", { design: "randomized_trial" }), familyChip("predicted", { design: "docking" }), familyChip("predicted", { design: "network_prediction" }))),
      g("verdict badges (§6.2)", h("div.g-row",
        verdictPill(verdictBadge({ states: { release_authorized: true }, authorized: true, publishable: true })),
        verdictPill(verdictBadge({ states: { schema_valid: true }, publishable: true, authorized: false })),
        verdictPill({ tone: "caveat", icon: "!", label: "允许 · 附说明" }), verdictPill({ tone: "refused", icon: "✕", label: "已拒绝" }),
        verdictPill({ tone: "declaration", icon: "✎", label: "需声明外推" }), verdictPill({ tone: "prediction", icon: "✕", label: "预测被当作事实" }),
        verdictPill(verdictBadge("draft", "artifact")), verdictPill(verdictBadge("experimental", "artifact")),
        verdictPill(verdictBadge("accepted", "inquiry")), verdictPill(verdictBadge("provisional", "inquiry")), verdictPill(verdictBadge("undetermined", "inquiry")), verdictPill(verdictBadge("needs_hypotheses", "inquiry")),
        verdictPill(verdictBadge("verified", "goal")), verdictPill(verdictBadge("pending_manual", "goal")), verdictPill(verdictBadge("unverified", "goal")))),
      g("reason codes", h("div.g-row", ["CLM005", "CLM004", "CLM009", "ART105", "ART116", "EVIDENCE103", "INQ113", "GATE004"].map((c) => codeChip(c))))),
    h("div.g-grid", { style: "margin-top:16px" },
      g("licensing ladder · efficacy ← preclinical (CLM005)", licensingLadderView("efficacy", "preclinical", { codes: ["CLM005"] })),
      g("licensing ladder · mechanism hypothesis ← prediction", licensingLadderView("mechanism_hypothesis", "computational_prediction")),
      g("licensing ladder · traditional use ← expert experience", licensingLadderView("traditional_use", "expert_experience")),
      g("licensing ladder · attribution (no weakest tier)", licensingLadderView("attribution", null))),
    h("div.g-cell", { style: "margin-top:16px;max-width:760px" }, h("div.g-label", "citation chips in prose (hover / focus for the card)"), h("div.prose", { lang: lang() === "zh" ? "zh-Hans" : "en" }, prose)));
}

function claimsSection() {
  const env = FIX.envelope_claims;
  const claims = claimsOf(env);
  const ev = evidenceOf(env);
  const safety = claimsOf(FIX.envelope_safety);
  const safetyEv = evidenceOf(FIX.envelope_safety);
  const norm = claimsOf(FIX.envelope_normalize);
  return h("div",
    h("p.g-lede", t("ui.gallery.claims_lede")),
    h("div.g-grid.g-grid--wide",
      claims.map((c) => g(`claim · ${c.id}`, claimCard(c, { evidence: ev, onEvidence: (id) => toast(id) }))),
      safety.map((c) => g(`claim · ${c.id} (assess-tcm-safety)`, claimCard(c, { evidence: safetyEv }))),
      norm.map((c) => g(`claim · ${c.id} (normalize)`, claimCard(c, { evidence: evidenceOf(FIX.envelope_normalize) })))),
    h("div.g-stack", { style: "margin-top:16px" }, h("div.g-label", "compact (thread)"),
      claims.map((c) => claimCard(c, { evidence: ev, compact: true, onOpen: () => toast("→ 主张") }))));
}

function artifactsSection() {
  return h("div",
    h("div.g-grid.g-grid--wide",
      g("released 6/6 · assess-tcm-safety (runner, attested)", artifactCard(FIX.envelope_safety)),
      g("consistent, not verified · network (no output root)", artifactCard(FIX.envelope_network_unattested)),
      g("refused · claims review", artifactCard(FIX.envelope_claims))),
    h("div.g-stack", { style: "margin-top:16px" }, h("div.g-label", "compact (thread)"),
      artifactCard(FIX.envelope_network, { compact: true, onOpen: () => {} }),
      artifactCard(FIX.envelope_network_unattested, { compact: true, onOpen: () => {} }),
      artifactCard(FIX.envelope_claims, { compact: true, onOpen: () => {} })),
    h("div.g-grid", { style: "margin-top:16px" },
      g("release checklist · 6/6", releaseChecklist(releaseOf(FIX.envelope_safety))),
      g("release checklist · 2/6", releaseChecklist(releaseOf(FIX.envelope_network_unattested))),
      g("release checklist · refused", releaseChecklist(releaseOf(FIX.envelope_claims)))));
}

function evidenceSection() {
  const ev = [...evidenceOf(FIX.envelope_safety), ...evidenceOf(FIX.envelope_claims)];
  const src = evidenceOf(FIX.envelope_claims)[0]?.source;
  const src2 = evidenceOf(FIX.envelope_safety)[0]?.source;
  return h("div",
    h("div.g-grid",
      g("quality pills (no total)", qualityPills(evidenceOf(FIX.envelope_claims)[0].quality), qualityPills(null)),
      g("source card", sourceCard(src)),
      g("source card · seed corpus", sourceCard(src2))),
    h("div.g-grid.g-grid--wide", { style: "margin-top:16px" }, ev.slice(0, 2).map((e, i) => g(`evidence item E${i + 1}`, evidenceItem(e, { n: i + 1 }))), ev.slice(-2).map((e, i) => g(`evidence item (claims) E${i + 1}`, evidenceItem(e, { n: i + 1 })))),
    h("div.g-cell", { style: "margin-top:16px" }, h("div.g-label", "grouped by family"), h("div", { style: "max-width:480px" }, evidenceGroups(ev))),
    h("div.g-cell", { style: "margin-top:16px" }, h("div.g-label", "evidence table (Elicit-style)"), evidenceTable(ev)));
}

// ------------------------------------------------------------------------------------------------- run

function runSection() {
  const cat = FIX.catalog;
  const mk = (env, extra = {}) => toolCallCard({ id: `c-${env.tool}-${Math.random()}`, name: env.tool, args: env.tool === "call_tool" ? JSON.parse(JSON.stringify({ tool: env.via, arguments: {} })) : argsOf(env), ...extra }, env, { catalog: cat, runnerUrl: "http://127.0.0.1:8765", onOpen: () => toast("→ 检查器") });
  return h("div",
    h("div.g-stack",
      g("thinking · done", thinkingBlock({ text: "用户问的是十八反配伍。先规范化两味药名，再查配伍关系和安全性记录。注意：记载不等于临床安全性结论。\n\n需要说明无记录不等于安全。", durationMs: 8200 })),
      g("thinking · streaming", thinkingBlock({ text: "正在比较两个候选…", streaming: true, startedAt: Date.now() - 3000 })),
      g("tool · running (runner, GPU)", toolCallCard({ id: "r1", name: "tcm_evidence", args: { subject: "附子", claim_kind: "safety_signal" }, where: "runner", status: "running" }, null, { catalog: cat, runnerUrl: "http://127.0.0.1:8765" })),
      g("tool · succeeded · governed, released (runner)", mk(FIX.envelope_safety)),
      g("tool · succeeded · browser native", mk(FIX.envelope_compatibility, { args: { herbs: ["甘草", "甘遂"] } })),
      g("tool · succeeded · expanded", toolCallCard({ id: "x", name: "tcm_normalize", args: { names: ["姜", "白芍"] } }, FIX.envelope_normalize, { catalog: cat, open: true, onOpen: () => {} })),
      g("tool · claims refused by the kernel", mk(FIX.envelope_claims)),
      g("tool · refused by the user", toolCallCard({ id: "d", name: "network_pharmacology_run", args: { formula: "葛根芩连汤", disease: "2 型糖尿病" } }, FIX.envelope_denied, { catalog: cat, open: true })),
      g("tool · failed · needs runner", mk(FIX.envelope_needs_runner, { args: { query: "Puerariae Lobatae Radix inflammation" } })),
      g("tool · failed · network off", mk(FIX.envelope_network_off, { args: { connector: "chembl", operation: "molecule" } })),
      g("tool · job submitted (CUDA:0)", mk(FIX.envelope_job, { args: { pipeline: "dock", arguments: { ligand: "puerarin" } } })),
      g("tool · waiting for approval", toolCallCard({ id: "w", name: "literature_search", args: { query: "葛根素 炎症" }, status: "needs_approval" }, null, { catalog: cat })),
      g("permission · network + first runner call", permissionCard(FIX.approvals[0], { catalog: cat, onDecide: (d) => toast(d) })),
      g("permission · job", permissionCard(FIX.approvals[1], { onDecide: () => {} })),
      g("permission · remote upload", permissionCard(FIX.approvals[2], { onDecide: () => {} })),
      g("permission · decided", permissionCard(FIX.approvals[1], { decided: "project" }), permissionCard(FIX.approvals[0], { decided: "deny" }))));
}

function argsOf(env) {
  const a = { tcm_safety_report: { subject: "甘草", co_administered: ["甘遂"] }, tcm_normalize: { names: ["姜", "白芍"] }, tcm_network_hypothesis: { formula_name: "桂枝汤" } };
  return a[env.tool] || {};
}

function jobsSection() {
  const j = FIX.jobs;
  return h("div.g-grid.g-grid--wide",
    g("job · queued", jobCard(j.queued, { onCancel: () => toast("cancel") })),
    g("job · running (CUDA:0)", jobCard(j.running, { onCancel: () => toast("cancel"), logs: ["vina: exhaustiveness 8", "pose 1/10", "pose 2/10", "pose 3/10", "pose 4/10"] })),
    g("job · collected and verified", jobCard(j.succeeded, { onOpenFile: (p) => toast(p) })),
    g("job · failed (engine missing)", jobCard(j.failed)));
}

load();
