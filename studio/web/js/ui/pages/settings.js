// Settings (DESIGN §3.6–3.8, §4.5): 模型 Models · 计算 Compute · 通用 General · 快捷键 Shortcuts.
// Keys are stored only in this browser and never sent to science.impf.ai; Tao-S1 needs none.

import { lang, setLang, t } from "../../core/i18n.js";
import { activeProvider, localFix, newCustomProvider, PRESETS, testConnection } from "../../core/providers.js";
import { clearKeys } from "../../core/settings.js";
import { cssEscape, debounce, fill, formatDuration, formatNumber, h } from "../dom.js";
import { icon } from "../icons.js";
import { confirmDialog, toast } from "../overlay.js";
import { computePanel, detectLocalModels, relayErrorLine } from "../panels.js";
import { button, chip, disclosure, iconButton, keyValue, notice, section, segmented, selectField, spinner, statusDot, switchControl, textArea, textField } from "../primitives.js";
import { SETTINGS_TABS } from "../router.js";
import { shortcutTable } from "../palette.js";
import { exportProject } from "./project.js";
import { jobCard } from "../toolcards.js";

const TAB_ICON = { models: "sparkles", compute: "cpu", general: "sliders", shortcuts: "keyboard" };

export function mountSettings(app, main) {
  const page = h("div.page.page--settings");
  fill(main, page);
  let offs = [];

  // A re-render (set default, add an endpoint, a relay check landing) rebuilds the tab: what was typed is saved first,
  // and focus, the caret and the open provider cards come back to where they were.
  function render({ focusKey } = {}) {
    flushSavers();
    const keep = captureUi(page);
    if (focusKey) keep.focus = { key: `k:${focusKey}`, n: 0 };
    build();
    restoreUi(page, keep);
  }

  function build() {
    const tab = app.state.route.tab || "models";
    const nav = h("nav.settings-nav", { "aria-label": t("ui.nav.settings") }, SETTINGS_TABS.map((id) => h("a", {
      href: `#/settings/${id}`, class: ["settings-nav__item", id === tab && "is-on"], "aria-current": id === tab ? "page" : null,
    }, icon(TAB_ICON[id], { size: 16 }), h("span", t(`ui.settings.tab.${id}`)))));
    const body = { models: modelsTab, compute: computeTab, general: generalTab, shortcuts: shortcutsTab }[tab](app, render);
    fill(page, h("div.settings",
      h("header.settings__head", h("h1.settings__title.serif", t("ui.nav.settings"))),
      h("div.settings__grid", nav, h("div.settings__body", { id: `settings-${tab}` }, body))));
  }
  offs = [
    app.on("relay", () => { if (app.state.route.tab === "models") render(); }),
    app.on("runtime", () => { if (app.state.route.tab === "compute") render(); }),
  ];
  build();
  return { el: page, update: () => { build(); page.scrollTop = 0; }, destroy: () => offs.forEach((off) => off()) };
}

/** A key for a control that survives a re-render: data-key, else its kind, name and words (and which of equals). */
function uiKey(el) {
  if (el.dataset?.key) return `k:${el.dataset.key}`;
  const label = el.id ? el.ownerDocument.querySelector(`label[for="${cssEscape(el.id)}"]`)?.textContent || "" : "";
  return [el.tagName, el.getAttribute("role") || "", el.getAttribute("aria-label") || "", label, el.getAttribute("data-value") || "", el.getAttribute("placeholder") || "", el.matches("input, textarea") ? "" : el.textContent.trim().slice(0, 60)].join("|");
}

function captureUi(page) {
  const out = { focus: null, open: [] };
  const a = document.activeElement;
  if (a && a !== document.body && page.contains(a)) {
    const key = uiKey(a);
    const same = [...page.querySelectorAll(a.tagName)].filter((x) => uiKey(x) === key);
    out.focus = { key, n: Math.max(0, same.indexOf(a)), container: a.closest("[data-key]")?.dataset.key || null };
    if (typeof a.selectionStart === "number") out.focus.sel = [a.selectionStart, a.selectionEnd];
  }
  for (const b of page.querySelectorAll('.disclosure__summary[aria-expanded="true"]')) out.open.push(b.closest("[data-key]")?.dataset.key || b.textContent.trim());
  return out;
}

function restoreUi(page, keep) {
  for (const d of page.querySelectorAll(".disclosure")) {
    const b = d.querySelector(":scope > .disclosure__summary, :scope > * > .disclosure__summary");
    const key = d.closest("[data-key]")?.dataset.key || b?.textContent.trim();
    if (b && keep.open.includes(key) && b.getAttribute("aria-expanded") !== "true") d.setOpen?.(true);
  }
  const f = keep.focus;
  if (!f) return;
  let el = null;
  if (f.key.startsWith("k:")) el = page.querySelector(`[data-key="${cssEscape(f.key.slice(2))}"]`);
  else {
    const tag = f.key.split("|")[0];
    el = [...page.querySelectorAll(tag)].filter((x) => uiKey(x) === f.key)[f.n] || null;
  }
  // the control is gone (设为默认 became 「默认」): the first control of the same card, else the page's heading
  if (!el && f.container) {
    const box = page.querySelector(`[data-key="${cssEscape(f.container)}"]`);
    el = box?.querySelector("button:not([disabled]), input, a[href], [tabindex='0']") || null;
  }
  if (!el) { el = page.querySelector(".settings__title"); el?.setAttribute("tabindex", "-1"); }
  el?.focus({ preventScroll: true });
  if (f.sel && typeof el?.setSelectionRange === "function") { try { el.setSelectionRange(f.sel[0], f.sel[1]); } catch { /* not a text input */ } }
}

// ------------------------------------------------------------------------------------------------- models

function modelsTab(app, rerender) {
  const s = app.state.settings;
  const relay = app.state.relay;
  const tao = PRESETS.find((p) => p.relay);
  const isDefault = (id) => s.provider === id;
  const setDefault = (id) => { app.setSetting({ provider: id }); toast(t("ui.models.default_set"), { tone: "ok" }); rerender(); };
  const limits = relay?.limits || null;

  const taoCard = h("section.provider-card.provider-card--tao", { "data-key": "provider:tao" },
    h("header.provider-card__head",
      h("span.provider-card__mark", h("span.wordmark__dot", "·"), "S1"),
      h("div.provider-card__title", h("p.provider-card__name", "Tao-S1"), h("p.provider-card__sub", t("ui.models.tao_sub"))),
      h("div.provider-card__badges",
        isDefault("tao") ? chip({ label: t("ui.models.is_default"), tone: "navy", icon: "check" }) : null,
        relay ? (relay.checking ? spinner({ size: 12 }) : h("span.provider-card__health", statusDot(relay.ok ? "ok" : "busy"), relay.ok ? t("ui.model.relay_ok") : t("ui.model.relay_down"))) : null)),
    h("p.provider-card__disclosure", tao.help[lang()] || tao.help.zh),
    relay && !relay.ok && relay.error ? notice({ tone: "warn", body: relayErrorLine(relay, { tag: "p.notice__body", here: true }) }) : null,
    keyValue([
      [t("ui.models.limits"), limits ? [limits.per_minute ? t("ui.models.per_minute", { n: limits.per_minute }) : null, limits.per_day ? t("ui.models.per_day", { n: limits.per_day }) : null, limits.tokens_per_day ? t("ui.models.tokens_per_day", { n: formatNumber(limits.tokens_per_day) }) : null].filter(Boolean).join(" · ") : h("span.muted", t("ui.models.limits_unknown"))],
      [t("ui.models.max_output"), relay?.max_output_tokens ? t("ui.models.tokens", { n: formatNumber(relay.max_output_tokens) }) : "—"],
      [t("ui.models.key"), t("ui.models.no_key")],
    ]),
    h("div.provider-card__actions",
      isDefault("tao") ? null : button({ label: t("ui.models.set_default"), size: "sm", onClick: () => setDefault("tao") }),
      button({ label: t("ui.models.check_relay"), size: "sm", variant: "ghost", icon: "refresh", attrs: { "data-key": "check-relay" }, onClick: () => app.checkRelay() })));

  const cloud = PRESETS.filter((p) => !p.relay && !p.local && !p.custom);
  const keysBlock = h("div.keys-note",
    notice({ tone: "info", icon: "lock", body: t("ui.models.key_note") }),
    switchControl({ label: t("ui.models.remember"), description: t("ui.models.remember_desc"), checked: s.rememberKeys, onChange: (v) => app.setSetting({ rememberKeys: v }) }),
    h("div.row", button({ label: t("ui.models.forget_keys"), size: "sm", variant: "ghost", icon: "trash", onClick: async () => {
      if (await confirmDialog({ title: t("ui.models.forget_keys"), body: t("ui.models.forget_body"), confirmLabel: t("ui.models.forget"), danger: true })) { clearKeys(); toast(t("ui.models.forgotten")); rerender(); }
    } })));

  const customs = s.customProviders || [];
  const customBlock = h("div.stack",
    customs.length ? customs.map((c, i) => customCard(app, c, i, rerender)) : h("p.muted.small", t("ui.models.no_custom")),
    h("div.row",
      // the new endpoint's card opens with its first field focused (its name)
      ...["openai", "anthropic"].map((fmt) => button({ label: t(`ui.models.add_${fmt}`), size: "sm", icon: "plus", onClick: () => {
        const c = newCustomProvider(fmt, customs.length + 1);
        app.setSetting({ customProviders: [...customs, c] });
        rerender({ focusKey: `field:${c.id}:label` });
      } }))));

  const locals = PRESETS.filter((p) => p.local);
  const detected = app.state.localDetected || [];
  const localBlock = h("div.stack",
    notice({ tone: "info", icon: "shieldCheck", body: t("ui.models.local_note") }),
    h("div.row",
      button({ label: t("ui.model.detect"), size: "sm", icon: "search", disabled: app.state.runner.status !== "ready", onClick: async () => { await detectLocalModels(app); rerender(); } }),
      app.state.runner.status !== "ready" ? h("span.muted.small", t("ui.model.detect_needs_runner")) : null),
    detected.length ? h("ul.detected", { role: "list" }, detected.map((d) => h("li", statusDot(d.ok ? "ok" : "off"), h("span.mono", d.base_url), h("span", d.ok ? t("ui.models.detected_models", { n: d.models.length }) : t("ui.models.not_answering"))))) : null,
    locals.map((p) => providerCard(app, p, { local: true, detected: detected.find((d) => d.id === p.id), rerender })));

  return h("div.stack.stack--xl",
    section({ title: t("ui.models.default_title"), children: taoCard }),
    section({ title: t("ui.models.yours"), children: [keysBlock, h("div.stack", cloud.map((p) => providerCard(app, p, { rerender })))] }),
    section({ title: t("ui.models.custom"), children: customBlock }),
    section({ title: t("ui.models.local"), children: localBlock }),
    section({ title: t("ui.models.advanced"), children: advanced(app) }));
}

function providerCard(app, p, { local = false, detected = null, rerender }) {
  const s = app.state.settings;
  const key = s.keys?.[p.id] || "";
  const configured = local ? Boolean(s.models?.[p.id] || s.baseUrls?.[p.id]) : Boolean(key);
  const isDefault = s.provider === p.id;
  const result = h("div.test-result", { "aria-live": "polite" });
  const save = patchSaver((patch) => app.setSetting(patch));
  const models = [...new Set([...(detected?.models || []), ...(p.models || [])])];
  const listId = `models-${p.id}`;
  const content = () => h("div.provider-card__body",
    p.help ? h("p.provider-card__help", p.help[lang()] || p.help.zh) : null,
    !local ? keyField(app, p.id) : null,
    textField({ label: t("ui.models.base_url"), value: s.baseUrls?.[p.id] || "", placeholder: p.base_url, mono: true, help: t("ui.models.base_url_help"), onInput: (v) => save({ baseUrls: { [p.id]: v.trim() } }) }),
    h("div.field",
      h("label.field__label", { for: `${listId}-in` }, t("ui.models.model")),
      Object.assign(h("input.input.input--mono", { id: `${listId}-in`, list: listId, placeholder: p.default_model || t("ui.models.model_ph"), autocomplete: "off", spellcheck: "false", onInput: (e) => save({ models: { [p.id]: e.target.value.trim() } }) }), { value: s.models?.[p.id] || "" }),
      h("datalist", { id: listId }, models.map((m) => h("option", { value: m })))),
    local ? h("p.provider-card__fix", icon("info", { size: 12 }), localFix(p)) : null,
    h("div.provider-card__actions",
      button({ label: t("ui.models.test"), size: "sm", icon: "zap", onClick: () => runTest(app, p.id, result) }),
      isDefault ? chip({ label: t("ui.models.is_default"), tone: "navy", icon: "check" }) : button({ label: t("ui.models.set_default"), size: "sm", variant: "ghost", onClick: () => { flushSavers(); app.setSetting({ provider: p.id }); toast(t("ui.models.default_set"), { tone: "ok" }); rerender(); } }),
      p.docs ? h("a.panel__link", { href: p.docs, target: "_blank", rel: "noopener noreferrer" }, t("ui.models.get_key"), icon("external", { size: 12 })) : null),
    result);
  return h("section", { class: ["provider-card", configured && "is-configured"], "data-key": `provider:${p.id}` },
    disclosure({
      summary: h("span.provider-card__summary",
        h("span.provider-card__name", p.label),
        h("span.provider-card__state", configured ? [statusDot("ok"), t(local ? "ui.models.configured_local" : "ui.models.configured")] : t(local ? "ui.models.not_set_local" : "ui.models.no_key_set")),
        isDefault ? chip({ label: t("ui.models.is_default"), tone: "navy" }) : null),
      content, className: "provider-card__disc",
    }));
}

function keyField(app, id) {
  let shown = false;
  const saver = patchSaver((patch) => app.setSetting(patch));
  const save = (v) => saver({ keys: { [id]: v.trim() } });
  const reveal = iconButton({ icon: "eye", label: t("ui.models.reveal"), size: "sm", onClick: () => {
    shown = !shown;
    field.input.type = shown ? "text" : "password";
    reveal.setAttribute("aria-pressed", String(shown));
  } });
  const field = textField({ label: "API Key", value: app.state.settings.keys?.[id] || "", type: "password", mono: true, placeholder: "sk-…", help: t("ui.models.key_note_short"), onInput: save, trailing: reveal, attrs: { autocomplete: "off" } });
  return field;
}

async function runTest(app, providerId, out) {
  flushSavers();
  fill(out, h("p.muted.small", spinner({ size: 12 }), " ", t("ui.models.testing")));
  const provider = activeProvider(app.state.settings, { provider: providerId, runner: { ...app.state.settings.runner, connected: app.state.runner.status === "ready" } });
  if (provider.needsKey) { fill(out, notice({ tone: "warn", body: t("core.provider.no_key", { label: provider.label }) })); return; }
  const r = await testConnection(provider);
  if (r.ok) {
    fill(out, notice({
      tone: r.tools ? "ok" : "warn",
      title: r.tools ? t("ui.models.test_ok") : t("ui.models.test_no_tools"),
      body: [t("ui.models.latency", { d: formatDuration(r.latency_ms) }), r.first_token_ms ? t("ui.models.first_token", { d: formatDuration(r.first_token_ms) }) : null, provider.route === "runner" ? t("ui.models.via_runner") : null].filter(Boolean).join(" · "),
    }));
  } else {
    fill(out, notice({ tone: "warn", title: t("ui.models.test_failed"), body: r.error || "" }));
  }
}

// Field edits are saved 300 ms after the last keystroke. Every patch typed meanwhile is kept (a plain debounce would
// keep only the last call: a URL typed just before a model name would be lost), and a test or a "make default" first
// writes what is pending, so it never runs on the settings of a moment ago.
const savers = new Set();

function patchSaver(apply, { ms = 300, deep = true } = {}) {
  let pending = null;
  let save = null;
  const flush = () => {
    savers.delete(save);
    if (!pending) return;
    const patch = pending;
    pending = null;
    apply(patch);
  };
  const later = debounce(flush, ms);
  save = (patch) => {
    savers.add(save);
    pending = { ...(pending || {}) };
    for (const [k, v] of Object.entries(patch)) {
      const both = deep && v && typeof v === "object" && !Array.isArray(v) && pending[k] && typeof pending[k] === "object";
      pending[k] = both ? { ...pending[k], ...v } : v;
    }
    later();
  };
  save.flush = () => { later.cancel(); flush(); };
  return save;
}

function flushSavers() {
  for (const save of [...savers]) save.flush();
}

function customCard(app, c, i, rerender) {
  const s = app.state.settings;
  const result = h("div.test-result", { "aria-live": "polite" });
  const update = patchSaver((patch) => {
    const list = [...(app.state.settings.customProviders || [])];
    const at = list.findIndex((x) => x.id === c.id);
    list[at < 0 ? i : at] = { ...list[at < 0 ? i : at], ...patch };
    app.setSetting({ customProviders: list });
  }, { deep: false });
  const jsonField = (label, key) => textArea({
    label, value: Object.keys(c[key] || {}).length ? JSON.stringify(c[key], null, 2) : "", rows: 2, mono: true, placeholder: "{}",
    onInput: (v) => { try { update({ [key]: v.trim() ? JSON.parse(v) : {} }); } catch { /* keep typing */ } },
  });
  return h("section.provider-card.is-configured", { "data-key": `provider:${c.id}` },
    disclosure({
      open: !c.base_url,
      summary: h("span.provider-card__summary", h("span.provider-card__name", c.label || c.id), h("span.provider-card__state", c.format === "anthropic" ? t("ui.models.format_anthropic") : t("ui.models.format_openai")), s.provider === c.id ? chip({ label: t("ui.models.is_default"), tone: "navy" }) : null),
      className: "provider-card__disc",
      content: () => h("div.provider-card__body",
        textField({ label: t("ui.models.label"), value: c.label || "", attrs: { "data-key": `field:${c.id}:label` }, onInput: (v) => update({ label: v }) }),
        textField({ label: t("ui.models.base_url"), value: c.base_url || "", mono: true, placeholder: "https://…/v1", onInput: (v) => update({ base_url: v.trim() }) }),
        textField({ label: t("ui.models.model"), value: c.model || "", mono: true, onInput: (v) => update({ model: v.trim() }) }),
        keyField(app, c.id),
        selectField({ label: t("ui.models.max_tokens_param"), value: c.max_tokens_param || "max_tokens", options: [{ value: "max_tokens", label: "max_tokens" }, { value: "max_completion_tokens", label: "max_completion_tokens" }], onChange: (v) => update({ max_tokens_param: v }) }),
        jsonField(t("ui.models.headers"), "headers"),
        jsonField(t("ui.models.extra_body"), "extra_body"),
        h("div.provider-card__actions",
          button({ label: t("ui.models.test"), size: "sm", icon: "zap", onClick: () => runTest(app, c.id, result) }),
          s.provider === c.id ? null : button({ label: t("ui.models.set_default"), size: "sm", variant: "ghost", onClick: () => { flushSavers(); app.setSetting({ provider: c.id }); rerender(); } }),
          button({ label: t("ui.action.delete"), size: "sm", variant: "ghost", icon: "trash", onClick: async () => {
            if (!(await confirmDialog({ title: t("ui.models.delete_custom"), body: c.label || c.id, confirmLabel: t("ui.action.delete"), danger: true }))) return;
            const list = (app.state.settings.customProviders || []).filter((x) => x.id !== c.id);
            const keys = { ...app.state.settings.keys };
            delete keys[c.id];
            app.setSetting({ customProviders: list, keys, provider: app.state.settings.provider === c.id ? "tao" : app.state.settings.provider });
            rerender();
          } })),
        result),
    }));
}

function advanced(app) {
  const s = app.state.settings;
  return h("div.stack",
    h("div.field", h("p.field__label", t("ui.models.route")),
      segmented({ label: t("ui.models.route"), value: s.route, options: [{ value: "auto", label: t("ui.compute.auto") }, { value: "direct", label: t("ui.models.route_direct") }, { value: "runner", label: t("ui.models.route_runner") }], onChange: (v) => app.setSetting({ route: v }) }),
      h("p.field__help", t("ui.models.route_help"))),
    switchControl({ label: t("ui.models.thinking"), description: t("ui.models.thinking_desc"), checked: s.thinking, onChange: (v) => app.setSetting({ thinking: v }) }),
    h("div.grid-2",
      textField({ label: t("ui.models.max_tokens"), value: String(s.maxTokens), type: "number", inputmode: "numeric", help: t("ui.models.max_tokens_help"), onChange: (v) => app.setSetting({ maxTokens: Number(v) }) }),
      textField({ label: t("ui.models.max_steps"), value: String(s.maxSteps), type: "number", inputmode: "numeric", help: t("ui.models.max_steps_help"), onChange: (v) => app.setSetting({ maxSteps: Number(v) }) }),
      textField({ label: t("ui.models.temperature"), value: s.temperature === null ? "" : String(s.temperature), type: "number", inputmode: "decimal", placeholder: t("ui.models.temperature_default"), help: t("ui.models.temperature_help"), onChange: (v) => app.setSetting({ temperature: v === "" ? null : Number(v) }) })));
}

// ------------------------------------------------------------------------------------------------- compute

function computeTab(app) {
  const jobsBox = h("div.jobs-list");
  const runner = app.runtimes.runner;
  if (app.state.runner.status === "ready" && runner?.jobs) {
    jobsBox.append(h("p.muted.small", spinner({ size: 12 }), " ", t("ui.files.loading")));
    runner.jobs.list({}).then((res) => {
      const jobs = (res?.jobs || []).slice(0, 20);
      fill(jobsBox, jobs.length ? h("div.stack", jobs.map((j) => jobCard(j, { onCancel: async (job) => { try { await runner.jobs.cancel(job.id); app.emit("runtime", { kind: "runner" }); } catch (err) { toast(err?.message || String(err), { tone: "warn" }); } } }))) : h("p.muted.small", t("ui.jobs.none")));
    }).catch((err) => fill(jobsBox, h("p.muted.small", err?.message || String(err))));
  } else {
    jobsBox.append(h("p.muted.small", t("ui.jobs.needs_runner")));
  }
  return h("div.stack.stack--xl",
    section({ title: t("ui.compute.title"), children: computePanel(app, { compact: false }) }),
    section({ title: t("ui.web.label"), children: switchControl({ label: t("ui.web.default_label"), description: t("ui.web.default_desc"), checked: app.state.settings.web, onChange: (v) => app.setSetting({ web: v }) }) }),
    section({ title: t("ui.jobs.title"), children: jobsBox }));
}

// ------------------------------------------------------------------------------------------------- general

function generalTab(app, rerender) {
  const s = app.state.settings;
  const store = app.store;
  const counts = h("p.muted.small");
  Promise.all([store.projects.list({ archived: null }), store.conversations.list()]).then(([ps, cs]) => {
    counts.textContent = t("ui.general.counts", { projects: ps.length, conversations: cs.length });
  });
  const projectSel = selectField({ label: t("ui.general.export_label"), value: app.state.project?.id || app.state.projects[0]?.id || "", options: app.state.projects.map((p) => ({ value: p.id, label: p.name })) });
  const importInput = h("input", { type: "file", accept: ".json,application/json", hidden: true });
  importInput.addEventListener("change", async () => {
    const f = importInput.files[0];
    importInput.value = "";
    if (!f) return;
    try {
      const id = await store.importProject(f);
      await app.refreshProjects();
      toast(t("ui.general.imported"), { tone: "ok" });
      app.navigate({ name: "project", projectId: id });
    } catch (err) {
      toast(err?.message || String(err), { tone: "warn", timeout: 6000 });
    }
  });
  return h("div.stack.stack--xl",
    // manual activation: arrows move between the choices, Enter or Space applies one (an arrow must not switch the
    // whole interface's language or theme on its own: WCAG 3.2.2)
    section({ title: t("ui.general.language"), children: segmented({ label: t("ui.general.language"), value: lang(), manual: true, options: [{ value: "zh", label: "中文", lang: "zh-Hans" }, { value: "en", label: "English", lang: "en" }], onChange: (v) => setLang(v) }) }),
    section({ title: t("ui.theme.title"), children: segmented({ label: t("ui.theme.title"), value: s.theme, manual: true, options: [{ value: "system", label: t("ui.theme.system"), icon: "circleHalf" }, { value: "light", label: t("ui.theme.light"), icon: "sun" }, { value: "dark", label: t("ui.theme.dark"), icon: "moon" }], onChange: (v) => app.setSetting({ theme: v }) }) }),
    section({
      title: t("ui.general.data"), children: h("div.stack",
        notice({ tone: store.persistent ? "info" : "warn", icon: "database", title: store.persistent ? t("ui.general.persistent") : t("ui.general.memory"), body: t("ui.general.privacy") }),
        counts,
        h("div.data-row", projectSel, button({ label: t("ui.general.export"), icon: "download", disabled: !app.state.projects.length, onClick: () => {
          const p = app.state.projects.find((x) => x.id === projectSel.input.value);
          if (p) exportProject(app, p);
        } })),
        h("div.data-row", h("div", h("p.field__label", t("ui.general.import")), h("p.field__help", t("ui.general.import_help"))), button({ label: t("ui.general.import_btn"), icon: "upload", onClick: () => importInput.click() }), importInput),
        h("div.data-row.data-row--danger",
          h("div", h("p.field__label", t("ui.general.clear")), h("p.field__help", t("ui.general.clear_help"))),
          button({ label: t("ui.general.clear_btn"), variant: "danger", icon: "trash", onClick: async () => {
            const ok = await confirmDialog({ title: t("ui.general.clear_title"), body: t("ui.general.clear_body"), confirmLabel: t("ui.general.clear_btn"), danger: true });
            if (!ok) return;
            await store.clearAll();
            try { localStorage.removeItem("tcmstudio.settings"); sessionStorage.removeItem("tcmstudio.keys"); } catch { /* storage unavailable */ }
            try { for (const k of await caches.keys()) await caches.delete(k); } catch { /* no Cache Storage */ }
            location.hash = "#/";
            location.reload();
          } })))
    }),
    section({ title: t("ui.general.onboarding"), children: h("div.row", button({ label: t("ui.general.show_onboarding"), size: "sm", onClick: () => import("../onboarding.js").then((m) => m.openOnboarding(app)) })) }));
}

function shortcutsTab() {
  return h("div.stack", h("p.muted.small", t("ui.shortcuts.lede")), shortcutTable());
}
