// The project overview (DESIGN §3.1, §4.1): a composer that starts a conversation here, the example prompts of an
// empty project (§4.6, each shows a refusal), the conversations, the instructions, the knowledge files (they stay in
// this browser unless sent to the runner), the defaults, and the recent claims with their verdicts.

import { claimsOf, verdictBadge } from "../../core/governance.js";
import { t } from "../../core/i18n.js";
import { debounce, download, fill, formatBytes, formatDateTime, formatRelative, h } from "../dom.js";
import { verdictPill, hashBadge } from "../governance.js";
import { icon } from "../icons.js";
import { confirmDialog, openDialog, openMenu, promptDialog, toast } from "../overlay.js";
import { availableProviders } from "../panels.js";
import { button, chip, emptyState, iconButton, notice, progressBar, section, selectField, switchControl, textArea } from "../primitives.js";
import { mountComposer } from "../composer.js";
import { filePreview } from "../files.js";

export const EXAMPLES = [
  { id: "normalize", icon: "languages", skill: "normalize-tcm-entities" },
  { id: "network", icon: "network", skill: "analyze-tcm-network-pharmacology" },
  { id: "safety", icon: "shieldAlert", skill: "assess-tcm-safety" },
  { id: "evidence", icon: "bookText", skill: "retrieve-tcm-evidence" },
  { id: "enrichment", icon: "workflow", skill: "research.run", runner: true },
];

export function mountProject(app, main) {
  const project = app.state.project;
  const page = h("div.page.page--project");
  fill(main, page);
  const composer = mountComposer(app, { variant: "hero", autofocus: true, initialText: app.state.pendingPrompt || "" });
  app.state.pendingPrompt = null;
  let offs = [];

  function render() {
    const p = app.state.project;
    const convs = p ? app.state.conversations : [];
    const head = h("header.project-head",
      h("p.kicker", p ? t("ui.project.kicker") : "TCMScience Studio"),
      h("div.project-head__row",
        h("h1.project-head__title.serif", p ? p.name : t("ui.project.empty_title")),
        p ? h("div.project-head__actions",
          iconButton({ icon: "rename", label: t("ui.project.rename"), onClick: async () => {
            const name = await promptDialog({ title: t("ui.project.rename"), value: p.name });
            if (name) await app.updateProject(p.id, { name });
          } }),
          iconButton({ icon: "more", label: t("ui.project.menu", { name: p.name }), onClick: (e) => projectMenu(app, p, e.currentTarget) })) : null),
      p?.description ? h("p.project-head__desc", p.description) : null,
      !p || !convs.length ? h("p.project-head__lede", t("ui.project.empty_body")) : null);

    const parts = [head, h("div.project-composer", composer.el)];
    if (app.state.storageWarning) parts.push(notice({ tone: "warn", body: app.state.storageWarning }));
    if (!convs.length) parts.push(examples(app, composer));
    if (p) {
      if (convs.length) parts.push(conversationsSection(app, convs));
      parts.push(instructionsSection(app, p));
      parts.push(knowledgeSection(app, p));
      parts.push(defaultsSection(app, p));
      parts.push(recentClaimsSection(app, p, convs));
    }
    fill(page, h("div.page__inner", parts));
  }

  offs = [
    app.on("conversations", render),
    app.on("project", (p) => { if (p?.id === app.state.project?.id) renderHeadOnly(); }),
    app.on("lang", render),
  ];
  function renderHeadOnly() {
    const title = page.querySelector(".project-head__title");
    if (title && app.state.project) title.textContent = app.state.project.name;
  }
  render();
  if (!project && !app.state.projects.length) composer.focus();
  return { el: page, destroy() { offs.forEach((off) => off()); composer.destroy(); } };
}

function projectMenu(app, p, anchor) {
  openMenu(anchor, [
    { label: t("ui.project.edit_desc"), icon: "edit", onSelect: async () => {
      const d = await promptDialog({ title: t("ui.project.edit_desc"), value: p.description || "", maxLength: 400 });
      if (d !== null) app.updateProject(p.id, { description: d });
    } },
    { label: t("ui.project.export"), icon: "download", onSelect: () => exportProject(app, p) },
    { label: t("ui.project.archive"), icon: "archive", onSelect: async () => { await app.updateProject(p.id, { archived: true }); app.navigate({ name: "home" }); } },
    "-",
    { label: t("ui.action.delete"), icon: "trash", danger: true, onSelect: async () => {
      const ok = await confirmDialog({ title: t("ui.project.delete_title", { name: p.name }), body: t("ui.project.delete_body"), confirmLabel: t("ui.action.delete"), danger: true });
      if (ok) app.deleteProject(p.id);
    } },
  ]);
}

export async function exportProject(app, p) {
  try {
    const blob = await app.store.exportProject(p.id);
    const safe = p.name.replace(/[\\/:*?"<>|]+/g, "_").slice(0, 60) || "project";
    download(`${safe}.tcmstudio.json`, blob);
    toast(t("ui.project.exported"), { tone: "ok" });
  } catch (err) {
    toast(err?.message || String(err), { tone: "warn" });
  }
}

// ------------------------------------------------------------------------------------------------- examples

function examples(app, composer) {
  const runnerReady = app.state.runner.status === "ready";
  return h("section.examples", { "aria-label": t("ui.examples.label") },
    h("div.examples__grid", EXAMPLES.map((ex) => {
      const prompt = t(`ui.examples.${ex.id}.prompt`);
      const disabled = ex.runner && !runnerReady;
      return h("button", {
        type: "button", class: ["example", ex.runner && "example--runner"], "aria-disabled": disabled ? "true" : null,
        onClick: () => {
          if (disabled) { toast(t("ui.examples.needs_runner"), { action: { label: t("ui.runner.connect"), onClick: () => app.navigate({ name: "settings", tab: "compute" }) } }); return; }
          composer.setText(prompt, { send: true });
        },
      },
      h("span.example__icon", icon(ex.icon, { size: 16 })),
      h("span.example__text",
        h("span.example__prompt", prompt),
        h("span.example__shows", t(`ui.examples.${ex.id}.shows`))),
      h("span.example__meta", h("code", ex.skill), ex.runner ? chip({ label: t("ui.examples.runner_chip"), tone: runnerReady ? "jade" : "slate", icon: "laptop" }) : null));
    })));
}

// ------------------------------------------------------------------------------------------------- conversations

function conversationsSection(app, convs) {
  return section({
    title: t("ui.project.conversations"), count: convs.length,
    children: h("ul.conv-list", { role: "list" }, convs.slice(0, 30).map((c) => h("li",
      h("a.conv-list__item", { href: app.href({ name: "conversation", projectId: c.projectId, convId: c.id }) },
        icon("message", { size: 16 }),
        h("span.conv-list__title", c.title || t("ui.conv.untitled")),
        h("time.conv-list__time.num", { datetime: new Date(c.updatedAt).toISOString(), title: formatDateTime(c.updatedAt) }, formatRelative(c.updatedAt)))))),
  });
}

// ------------------------------------------------------------------------------------------------- instructions

function instructionsSection(app, p) {
  const status = h("span.save-state", { "aria-live": "polite" });
  const save = debounce(async (v) => {
    await app.updateProject(p.id, { instructions: v });
    status.textContent = t("ui.project.saved");
    setTimeout(() => { status.textContent = ""; }, 1600);
  }, 600);
  const field = textArea({
    attrs: { "aria-label": t("ui.project.instructions") }, value: p.instructions || "", rows: 4,
    placeholder: t("ui.project.instructions_ph"), help: t("ui.project.instructions_help"),
    onInput: (v) => { status.textContent = t("ui.project.saving"); save(v); },
  });
  return section({ title: t("ui.project.instructions"), actions: [status], children: field });
}

// ------------------------------------------------------------------------------------------------- knowledge

function knowledgeSection(app, p) {
  const list = h("div.knowledge__list");
  const input = h("input", { type: "file", multiple: true, hidden: true });
  const zone = h("button.dropzone", { type: "button", onClick: () => input.click() },
    icon("upload", { size: 18 }),
    h("span.dropzone__title", t("ui.knowledge.drop")),
    h("span.dropzone__note", t("ui.knowledge.privacy")));
  const add = async (files) => {
    for (const f of files) {
      try {
        const rec = await app.store.files.add(p.id, f, { name: f.name, source: "upload" });
        toast(t("ui.knowledge.added", { name: rec.name }), { tone: "ok" });
      } catch (err) { toast(err?.message || String(err), { tone: "warn" }); }
    }
    refresh();
  };
  input.addEventListener("change", () => { add([...input.files]); input.value = ""; });
  zone.addEventListener("dragover", (e) => { e.preventDefault(); zone.classList.add("is-over"); });
  zone.addEventListener("dragleave", () => zone.classList.remove("is-over"));
  zone.addEventListener("drop", (e) => { e.preventDefault(); e.stopPropagation(); zone.classList.remove("is-over"); add([...e.dataTransfer.files]); });

  async function refresh() {
    const files = await app.store.files.list(p.id);
    fill(list, files.length ? h("ul.file-list", { role: "list" }, files.map((f) => knowledgeRow(app, f, refresh))) : h("p.muted.small", t("ui.knowledge.none")));
  }
  refresh();
  return section({ title: t("ui.knowledge.title"), children: [zone, input, list] });
}

function knowledgeRow(app, f, refresh) {
  const runnerReady = app.state.runner.status === "ready";
  const big = f.bytes > 200 * 1024 * 1024;
  const progress = h("div.file-row__progress");
  const send = runnerReady && !f.runnerUploadId ? iconButton({
    icon: "upload", label: t("ui.knowledge.send_runner"), size: "sm", onClick: async () => {
      const ok = await confirmDialog({ title: t("ui.knowledge.send_title"), body: t("ui.knowledge.send_body", { name: f.name }), confirmLabel: t("ui.knowledge.send") });
      if (!ok) return;
      const bar = progressBar({ value: 0, label: t("ui.knowledge.uploading") });
      fill(progress, bar);
      try {
        const res = await app.runtimes.runner.upload(f.blob, { name: f.name, onProgress: (ev) => bar.set(ev.fraction ?? null) });
        if (res.sha256 && res.sha256 !== f.sha256) toast(t("ui.knowledge.hash_mismatch"), { tone: "warn" });
        await app.store.files.update(f.id, { runnerUploadId: res.id });
        toast(t("ui.knowledge.sent", { name: f.name }), { tone: "ok" });
      } catch (err) { toast(err?.message || String(err), { tone: "warn" }); }
      refresh();
    },
  }) : null;
  return h("li.file-row",
    icon(/^image\//.test(f.type) ? "image" : /json/.test(f.type) ? "fileJson" : /csv|tsv|sheet/.test(f.type + f.name) ? "fileTable" : "fileText", { size: 16, className: "file-row__icon" }),
    h("div.file-row__main",
      h("p.file-row__name", f.name),
      h("p.file-row__meta", [f.type || "—", formatBytes(f.bytes), f.source === "tool" ? t("ui.knowledge.from_tool") : t("ui.knowledge.uploaded"), formatRelative(f.createdAt)].join(" · ")),
      h("div.file-row__badges",
        hashBadge(f.sha256, { compact: true }),
        chip({ label: f.runnerUploadId ? t("ui.files.loc_both") : t("ui.files.loc_browser"), tone: f.runnerUploadId ? "navy" : "neutral", icon: f.runnerUploadId ? "laptop" : "lock" }),
        big ? chip({ label: t("ui.knowledge.big"), tone: "ochre", icon: "alert" }) : null),
      progress),
    h("div.file-row__actions",
      iconButton({ icon: "eye", label: t("ui.files.preview"), size: "sm", onClick: () => openDialog({ title: f.name, size: "lg", body: filePreview({ blob: f.blob }, { name: f.name, media_type: f.type }) }) }),
      send,
      iconButton({ icon: "trash", label: t("ui.action.delete"), size: "sm", onClick: async () => {
        const ok = await confirmDialog({ title: t("ui.knowledge.delete_title"), body: t("ui.knowledge.delete_body", { name: f.name }), confirmLabel: t("ui.action.delete"), danger: true });
        if (ok) { await app.store.files.remove(f.id); refresh(); }
      } })));
}

// ------------------------------------------------------------------------------------------------- defaults

function defaultsSection(app, p) {
  const d = p.defaults || {};
  const providers = availableProviders(app);
  const modelOptions = [{ value: "", label: t("ui.project.follow_global") }, ...providers.map((x) => ({ value: x.id, label: x.relay ? "Tao-S1" : `${x.label}${app.state.settings.models?.[x.id] ? ` · ${app.state.settings.models[x.id]}` : ""}` }))];
  return section({
    title: t("ui.project.defaults"),
    children: h("div.defaults",
      selectField({ label: t("ui.project.default_model"), value: d.provider || "", options: modelOptions, help: t("ui.project.default_model_help"),
        onChange: (v) => app.updateProject(p.id, { defaults: { ...(p.defaults || {}), provider: v || undefined, model: v ? app.state.settings.models?.[v] || "" : undefined } }) }),
      selectField({ label: t("ui.project.default_compute"), value: d.compute || "", help: t("ui.project.default_compute_help"),
        options: [{ value: "", label: t("ui.project.follow_global") }, { value: "auto", label: t("ui.compute.auto") }, { value: "browser", label: t("ui.compute.browser") }, { value: "runner", label: t("ui.compute.runner") }],
        onChange: (v) => app.updateProject(p.id, { defaults: { ...(p.defaults || {}), compute: v || undefined } }) }),
      switchControl({ label: t("ui.web.label"), description: t("ui.web.desc_long"), checked: Boolean(d.web), onChange: (v) => app.setWeb(v) }),
      Object.keys(p.approvals || {}).length ? h("div.approvals",
        h("p.field__label", t("ui.project.approvals")),
        h("ul.approvals__list", { role: "list" }, Object.keys(p.approvals).map((k) => h("li", approvalLabel(k), button({ label: t("ui.project.revoke"), size: "sm", variant: "ghost", onClick: async () => {
          const next = { ...p.approvals };
          delete next[k];
          await app.updateProject(p.id, { approvals: next });
          app.emit("conversations");
        } })))),
        h("p.field__help", t("ui.project.approvals_help"))) : null),
  });
}

/** A stored approval key (core/router.js) in words: runner · <entry> · <entry>:network · <entry>:remote_upload. */
function approvalLabel(key) {
  if (key === "runner") return h("span", t("ui.project.approval.runner"));
  const [id, what] = key.split(":");
  const label = what === "network" ? t("ui.project.approval.network") : what === "remote_upload" ? t("ui.project.approval.remote") : t("ui.project.approval.run");
  return h("span", h("code", id), h("span.muted", ` · ${label}`));
}

// ------------------------------------------------------------------------------------------------- recent claims

function recentClaimsSection(app, p, convs) {
  const box = h("div.recent-claims", h("p.muted.small", t("ui.files.loading")));
  (async () => {
    const rows = [];
    for (const c of convs.slice(0, 12)) {
      const msgs = await app.store.messages.list(c.id);
      for (const m of msgs) {
        if (m.role !== "tool" || !m.envelope) continue;
        for (const cl of claimsOf(m.envelope)) rows.push({ cl, conv: c, at: m.createdAt });
      }
    }
    rows.sort((a, b) => b.at - a.at);
    fill(box, rows.length ? h("ul.claim-rows", { role: "list" }, rows.slice(0, 8).map(({ cl, conv }) => h("li",
      h("a.claim-row", { href: app.href({ name: "conversation", projectId: p.id, convId: conv.id }) },
        h("span.claim__kind", cl.kind_label),
        verdictPill(verdictBadge(cl), { size: "sm" }),
        h("span.claim-row__text", cl.text),
        cl.codes.length ? h("code.claim-row__codes", cl.codes.slice(0, 3).join(" ")) : null)))) : h("p.muted.small", t("ui.project.no_claims")));
  })().catch(() => fill(box, h("p.muted.small", t("ui.project.no_claims"))));
  return section({ title: t("ui.project.recent_claims"), children: box });
}
