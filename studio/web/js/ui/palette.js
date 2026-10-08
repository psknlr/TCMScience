// The command palette (⌘K: projects, conversations, messages, commands, settings) and the shortcut sheet (⌘/),
// DESIGN §8.1. The palette is a combobox over a listbox: arrows move, Enter opens, Esc closes.

import { lang, setLang, t } from "../core/i18n.js";
import { fill, formatRelative, h, isMac, shortcutLabel, uid } from "./dom.js";
import { icon } from "./icons.js";
import { openDialog } from "./overlay.js";
import { kbd } from "./primitives.js";

export const SHORTCUTS = [
  ["palette", "mod+k"], ["new_chat", "mod+shift+o"], ["sidebar", "mod+shift+s"], ["inspector", "alt+backslash"],
  ["inspector_tab", "alt+1…5"], ["focus", "mod+shift+semicolon", "/"], ["send", "enter"], ["newline", "shift+enter"],
  ["stop", "esc"], ["edit_last", "up"], ["copy_last", "mod+shift+c"], ["attach", "mod+u"], ["sheet", "mod+slash"],
];

export function shortcutTable() {
  return h("div.tablewrap.shortcut-table", { role: "region", "aria-label": t("ui.shortcuts.title"), tabindex: "0" },
    h("table",
      h("thead", h("tr", h("th", { scope: "col" }, t("ui.shortcuts.action")), h("th", { scope: "col" }, t("ui.shortcuts.keys")), h("th", { scope: "col" }, t("ui.shortcuts.note")))),
      h("tbody", SHORTCUTS.map(([id, spec, alt]) => h("tr",
        h("td", t(`ui.shortcuts.${id}`)),
        h("td.shortcut-table__keys", keysOf(spec), alt ? [h("span.muted", ` ${t("ui.shortcuts.or")} `), h("kbd.kbd", alt)] : null),
        h("td.muted", t(`ui.shortcuts.${id}_note`) !== `ui.shortcuts.${id}_note` ? t(`ui.shortcuts.${id}_note`) : ""))))));
}

function keysOf(spec) {
  if (spec === "alt+1…5") return [h("kbd.kbd", isMac ? "⌥1" : "Alt+1"), h("span.muted", " … "), h("kbd.kbd", isMac ? "⌥5" : "Alt+5")];
  return kbd(spec);
}

export function openShortcutSheet() {
  openDialog({ title: t("ui.shortcuts.title"), size: "lg", body: h("div.stack", h("p.muted.small", t("ui.shortcuts.lede")), shortcutTable()) });
}

export function openPalette(app) {
  const listId = uid("pal");
  const input = h("input.palette__input", {
    type: "text", role: "combobox", "aria-expanded": "true", "aria-controls": listId, "aria-autocomplete": "list",
    placeholder: t("ui.palette.placeholder"), "aria-label": t("ui.palette.label"), autocomplete: "off", spellcheck: "false",
  });
  const list = h("div.palette__list", { id: listId, role: "listbox", "aria-label": t("ui.palette.results") });
  let items = [];
  let active = 0;
  let seq = 0;

  const commands = () => [
    { group: "commands", icon: "newChat", label: t("ui.nav.new_chat"), hint: shortcutLabel("mod+shift+o"), run: () => app.newConversation() },
    { group: "commands", icon: "folderPlus", label: t("ui.project.new"), run: () => app.createProject({}) },
    { group: "commands", icon: "languages", label: t("ui.palette.toggle_lang"), hint: lang() === "zh" ? "EN" : "中", run: () => setLang(lang() === "zh" ? "en" : "zh") },
    { group: "commands", icon: "circleHalf", label: t("ui.palette.toggle_theme"), run: () => app.setSetting({ theme: nextTheme(app.state.settings.theme) }) },
    { group: "commands", icon: "sidebar", label: t("ui.sidebar.toggle"), hint: shortcutLabel("mod+shift+s"), run: () => app.toggleSidebar() },
    { group: "commands", icon: "inspector", label: t("ui.inspector.toggle"), hint: shortcutLabel("alt+backslash"), run: () => app.toggleInspector() },
    { group: "commands", icon: "copy", label: t("ui.palette.copy_last"), hint: shortcutLabel("mod+shift+c"), run: () => app.copyLastAnswer() },
    { group: "commands", icon: "plug", label: t("ui.palette.connect_runner"), run: () => app.navigate({ name: "settings", tab: "compute" }) },
    { group: "commands", icon: "keyboard", label: t("ui.shortcuts.title"), hint: shortcutLabel("mod+slash"), run: () => setTimeout(() => openShortcutSheet(), 0) },
    { group: "settings", icon: "sparkles", label: t("ui.settings.tab.models"), run: () => app.navigate({ name: "settings", tab: "models" }) },
    { group: "settings", icon: "cpu", label: t("ui.settings.tab.compute"), run: () => app.navigate({ name: "settings", tab: "compute" }) },
    { group: "settings", icon: "sliders", label: t("ui.settings.tab.general"), run: () => app.navigate({ name: "settings", tab: "general" }) },
    { group: "settings", icon: "library", label: t("ui.nav.catalog"), run: () => app.navigate({ name: "catalog" }) },
    { group: "settings", icon: "help", label: t("ui.nav.about"), run: () => app.navigate({ name: "about" }) },
  ];

  async function search() {
    const q = input.value.trim();
    const my = ++seq;
    const lower = q.toLowerCase();
    const cmd = commands().filter((c) => !q || c.label.toLowerCase().includes(lower));
    const out = [];
    if (!q) {
      out.push(...app.state.recent.slice(0, 6).map((c) => ({ group: "recent", icon: "message", label: c.title || t("ui.conv.untitled"), hint: formatRelative(c.updatedAt), run: () => app.navigate({ name: "conversation", projectId: c.projectId, convId: c.id }) })));
      out.push(...app.state.projects.slice(0, 5).map((p) => ({ group: "projects", icon: "folder", label: p.name, run: () => app.navigate({ name: "project", projectId: p.id }) })));
      out.push(...cmd);
    } else {
      const hits = await app.store.search(q, { limit: 30 });
      if (my !== seq) return;
      const projects = new Map(app.state.projects.map((p) => [p.id, p]));
      for (const hit of hits) {
        if (hit.type === "project") out.push({ group: "projects", icon: "folder", label: projects.get(hit.id)?.name || hit.snippet, sub: hit.snippet, run: () => app.navigate({ name: "project", projectId: hit.id }) });
        else if (hit.type === "conversation") out.push({ group: "conversations", icon: "message", label: hit.snippet, sub: projects.get(hit.projectId)?.name, run: () => app.navigate({ name: "conversation", projectId: hit.projectId, convId: hit.conversationId }) });
        else out.push({ group: "messages", icon: "quote", label: hit.snippet, sub: projects.get(hit.projectId)?.name, run: () => app.navigate({ name: "conversation", projectId: hit.projectId, convId: hit.conversationId }) });
      }
      out.push(...cmd);
    }
    items = out;
    active = 0;
    renderList(q);
  }

  function renderList(q) {
    if (!items.length) {
      fill(list, h("p.palette__empty", t("ui.palette.none", { q })));
      input.removeAttribute("aria-activedescendant");
      return;
    }
    const nodes = [];
    let last = null;
    items.forEach((it, i) => {
      if (it.group !== last) {
        nodes.push(h("p.palette__group", { role: "presentation" }, t(`ui.palette.group.${it.group}`)));
        last = it.group;
      }
      nodes.push(h("div", {
        id: `${listId}-${i}`, role: "option", class: ["palette__item", i === active && "is-active"], "aria-selected": String(i === active),
        onClick: () => choose(i), onPointermove: () => { if (active !== i) { active = i; paint(); } },
      }, icon(it.icon, { size: 16 }), h("span.palette__text", h("span.palette__label", it.label), it.sub ? h("span.palette__sub", it.sub) : null), it.hint ? h("span.palette__hint", it.hint) : null));
    });
    fill(list, ...nodes);
    paint();
  }

  function paint() {
    list.querySelectorAll(".palette__item").forEach((el) => {
      const i = Number(el.id.split("-").pop());
      el.classList.toggle("is-active", i === active);
      el.setAttribute("aria-selected", String(i === active));
    });
    input.setAttribute("aria-activedescendant", `${listId}-${active}`);
    list.querySelector(".is-active")?.scrollIntoView({ block: "nearest" });
  }

  function choose(i) {
    const it = items[i];
    if (!it) return;
    d.close();
    it.run();
  }

  input.addEventListener("input", search);
  input.addEventListener("keydown", (e) => {
    if (e.isComposing || e.keyCode === 229) return;
    if (e.key === "ArrowDown") { e.preventDefault(); active = Math.min(items.length - 1, active + 1); paint(); }
    else if (e.key === "ArrowUp") { e.preventDefault(); active = Math.max(0, active - 1); paint(); }
    else if (e.key === "Enter") { e.preventDefault(); choose(active); }
  });

  const d = openDialog({
    size: "lg", className: "dialog--palette", labelledBy: null,
    body: h("div.palette", { "aria-label": t("ui.palette.label") },
      h("div.palette__search", icon("search", { size: 16 }), input, h("kbd.kbd", "Esc")),
      list,
      h("p.palette__foot", h("span", kbd("up"), kbd("down"), ` ${t("ui.palette.move")}`), h("span", kbd("enter"), ` ${t("ui.palette.open")}`))),
    initialFocus: input,
  });
  d.el.setAttribute("aria-label", t("ui.palette.label"));
  search();
}

function nextTheme(theme) {
  return theme === "system" ? "light" : theme === "light" ? "dark" : "system";
}
