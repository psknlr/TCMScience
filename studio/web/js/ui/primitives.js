// Primitives (DESIGN §4.5): buttons, chips, switches, segmented controls, fields, tabs with roving tabindex,
// disclosures, progress, key-value lists, empty states, copy buttons. Each returns a DOM element; none holds app state.

import { t } from "../core/i18n.js";
import { copyText, h, shortcutLabel, uid } from "./dom.js";
import { icon } from "./icons.js";
import { toast } from "./overlay.js";

/** variant: primary | secondary | ghost | danger | quiet; size: sm | md. */
export function button({ label = "", icon: ic, iconAfter, variant = "secondary", size = "md", onClick, title, type = "button", disabled, kbd, attrs = {}, className = "" } = {}) {
  const btn = h("button", {
    type, class: ["btn", `btn--${variant}`, `btn--${size}`, className], disabled, title, "data-tip": title && !label ? title : null, onClick, ...attrs,
  },
  ic ? icon(ic, { size: size === "sm" ? 14 : 16 }) : null,
  label ? h("span.btn__label", label) : null,
  kbd ? h("kbd.kbd.kbd--inline", shortcutLabel(kbd)) : null,
  iconAfter ? icon(iconAfter, { size: 14, className: "btn__after" }) : null);
  return btn;
}

/** An icon-only button with an accessible name (and the same text as its tooltip). */
export function iconButton({ icon: ic, label, onClick, size = "md", variant = "ghost", pressed, expanded, controls, disabled, attrs = {}, className = "", tip = true, kbd } = {}) {
  return h("button", {
    type: "button", class: ["icon-btn", `icon-btn--${size}`, `icon-btn--${variant}`, className], "aria-label": label,
    "data-tip": tip ? label + (kbd ? ` ${shortcutLabel(kbd)}` : "") : null,
    "aria-pressed": pressed === undefined ? null : String(Boolean(pressed)),
    "aria-expanded": expanded === undefined ? null : String(Boolean(expanded)),
    "aria-controls": controls || null, disabled, onClick, ...attrs,
  }, icon(ic, { size: size === "sm" ? 14 : 16 }));
}

/** A small label. tone: neutral | navy | jade | ochre | vermilion | slate; as a button when onClick is given. */
export function chip({ label, icon: ic, tone = "neutral", dashed = false, title, mono = false, onClick, className = "", attrs = {} } = {}) {
  const tag = onClick ? "button" : "span";
  return h(tag, {
    type: onClick ? "button" : null, class: ["chip", `chip--${tone}`, dashed && "chip--dashed", mono && "chip--mono", className],
    "data-tip": title || null, onClick, ...attrs,
  }, ic ? icon(ic, { size: 14, dashed }) : null, h("span.chip__label", label));
}

export function kbd(spec) {
  return h("kbd.kbd", shortcutLabel(spec));
}

/** A dot that carries status by colour and by its accessible label. tone: ok | busy | off | error. */
export function statusDot(tone, label) {
  return h("span", { class: ["status-dot", `status-dot--${tone}`], role: label ? "img" : null, "aria-label": label || null, "aria-hidden": label ? null : "true" });
}

/** role=switch button. */
export function switchControl({ checked = false, label, description, onChange, disabled, id = uid("sw") } = {}) {
  const sw = h("button", {
    type: "button", role: "switch", class: "switch", id, "aria-checked": String(Boolean(checked)), disabled,
    "aria-describedby": description ? `${id}-d` : null,
    onClick: () => {
      const next = sw.getAttribute("aria-checked") !== "true";
      sw.setAttribute("aria-checked", String(next));
      onChange?.(next);
    },
  }, h("span.switch__thumb"));
  if (!label) return sw;
  return h("div.switch-row",
    h("div.switch-row__text",
      h("label.switch-row__label", { for: id }, label),
      description ? h("p.switch-row__desc", { id: `${id}-d` }, description) : null),
    sw);
}

/**
 * A radiogroup of segments with roving tabindex and arrow keys. options: [{value, label, icon?, title?, lang?}].
 * manual: arrows only move focus and Enter/Space applies (for choices that change the whole page: language, theme).
 */
export function segmented({ options, value, onChange, label, size = "md", className = "", manual = false } = {}) {
  const group = h("div", { class: ["segmented", `segmented--${size}`, className], role: "radiogroup", "aria-label": label || null });
  const buttons = options.map((o) => h("button", {
    type: "button", role: "radio", class: "segmented__item", "aria-checked": String(o.value === value), tabindex: o.value === value ? "0" : "-1",
    "data-value": o.value, "data-tip": o.title || null, "aria-label": o.ariaLabel || null, lang: o.lang || null,
    onClick: () => select(o.value, true),
  }, o.icon ? icon(o.icon, { size: 14 }) : null, o.label ? h("span", o.label) : null));
  if (!options.some((o) => o.value === value) && buttons[0]) buttons[0].tabIndex = 0;
  group.append(...buttons);
  function select(v, fire) {
    for (const b of buttons) {
      const on = b.dataset.value === String(v);
      b.setAttribute("aria-checked", String(on));
      b.tabIndex = on ? 0 : -1;
    }
    if (fire) onChange?.(v);
  }
  group.addEventListener("keydown", (e) => {
    const i = buttons.indexOf(document.activeElement);
    if (i < 0) return;
    let j = -1;
    if (e.key === "ArrowRight" || e.key === "ArrowDown") j = (i + 1) % buttons.length;
    else if (e.key === "ArrowLeft" || e.key === "ArrowUp") j = (i - 1 + buttons.length) % buttons.length;
    else if (e.key === "Home") j = 0;
    else if (e.key === "End") j = buttons.length - 1;
    if (j < 0) return;
    e.preventDefault();
    buttons[j].focus();
    if (manual) { for (const b of buttons) b.tabIndex = b === buttons[j] ? 0 : -1; return; }
    select(buttons[j].dataset.value, true);
  });
  group.setValue = (v) => select(v, false);
  return group;
}

/** A labelled text field. */
export function textField({ label, value = "", type = "text", placeholder = "", onInput, onChange, help, mono = false, id = uid("tf"), autocomplete = "off", attrs = {}, trailing, spellcheck = false, inputmode } = {}) {
  const input = h("input", {
    id, type, class: ["input", mono && "input--mono"], placeholder, autocomplete, spellcheck: String(spellcheck), inputmode: inputmode || null,
    "aria-describedby": help ? `${id}-h` : null, ...attrs,
  });
  input.value = value ?? "";
  if (onInput) input.addEventListener("input", () => onInput(input.value));
  if (onChange) input.addEventListener("change", () => onChange(input.value));
  const control = trailing ? h("div.input-group", input, trailing) : input;
  const field = h("div.field", label ? h("label.field__label", { for: id }, label) : null, control, help ? h("p.field__help", { id: `${id}-h` }, help) : null);
  field.input = input;
  return field;
}

export function textArea({ label, value = "", placeholder = "", onInput, help, rows = 4, id = uid("ta"), mono = false, attrs = {} } = {}) {
  const ta = h("textarea", { id, class: ["input", "textarea", mono && "input--mono"], rows: String(rows), placeholder, "aria-describedby": help ? `${id}-h` : null, ...attrs });
  ta.value = value ?? "";
  if (onInput) ta.addEventListener("input", () => onInput(ta.value));
  const field = h("div.field", label ? h("label.field__label", { for: id }, label) : null, ta, help ? h("p.field__help", { id: `${id}-h` }, help) : null);
  field.input = ta;
  return field;
}

export function selectField({ label, options, value, onChange, help, id = uid("sel") } = {}) {
  const sel = h("select", { id, class: "input select", "aria-describedby": help ? `${id}-h` : null },
    options.map((o) => h("option", { value: o.value, selected: o.value === value }, o.label)));
  sel.addEventListener("change", () => onChange?.(sel.value));
  const field = h("div.field", label ? h("label.field__label", { for: id }, label) : null, h("div.select-wrap", sel, icon("chevronDown", { size: 14, className: "select-wrap__icon" })), help ? h("p.field__help", { id: `${id}-h` }, help) : null);
  field.input = sel;
  return field;
}

export function slider({ label, min = 0, max = 100, step = 1, value = 0, onChange, format = (v) => String(v), id = uid("sl") } = {}) {
  const out = h("output.slider__value.num", { for: id }, format(value));
  const input = h("input", { id, type: "range", class: "slider", min: String(min), max: String(max), step: String(step) });
  input.value = String(value);
  input.addEventListener("input", () => { out.textContent = format(Number(input.value)); });
  input.addEventListener("change", () => onChange?.(Number(input.value)));
  const field = h("div.field.field--slider", h("div.field__row", h("label.field__label", { for: id }, label), out), input);
  field.input = input;
  return field;
}

export function spinner({ size = 14, label } = {}) {
  return h("span", { class: "spinner", style: { width: `${size}px`, height: `${size}px` }, role: label ? "status" : null, "aria-label": label || null, "aria-hidden": label ? null : "true" });
}

/** value 0..1, or null for indeterminate. */
export function progressBar({ value = null, label, className = "" } = {}) {
  const bar = h("div", {
    class: ["progress", value === null && "progress--indeterminate", className], role: "progressbar", "aria-label": label || null,
    "aria-valuemin": "0", "aria-valuemax": "100", "aria-valuenow": value === null ? null : String(Math.round(value * 100)),
  }, h("div.progress__bar", { style: value === null ? null : { width: `${Math.max(0, Math.min(1, value)) * 100}%` } }));
  bar.set = (v) => {
    bar.classList.toggle("progress--indeterminate", v === null);
    if (v === null) bar.removeAttribute("aria-valuenow");
    else bar.setAttribute("aria-valuenow", String(Math.round(v * 100)));
    bar.firstChild.style.width = v === null ? "" : `${Math.max(0, Math.min(1, v)) * 100}%`;
  };
  return bar;
}

/**
 * A disclosure: a button with aria-expanded controlling a region. `content` may be a Node or a function that builds it
 * on first open (tool results can be large).
 */
export function disclosure({ summary, content, open = false, onToggle, className = "", summaryClass = "", level } = {}) {
  const id = uid("dc");
  const region = h("div", { id, class: "disclosure__body", hidden: !open });
  let built = false;
  const build = () => {
    if (built) return;
    built = true;
    const node = typeof content === "function" ? content() : content;
    if (node) region.append(node);
  };
  if (open) build();
  const btn = h("button", { type: "button", class: ["disclosure__summary", summaryClass], "aria-expanded": String(open), "aria-controls": id },
    icon("chevronRight", { size: 14, className: "disclosure__chev" }), summary);
  btn.addEventListener("click", () => setOpen(btn.getAttribute("aria-expanded") !== "true"));
  const root = h("div", { class: ["disclosure", className, open && "is-open"] }, level ? h(`h${level}.disclosure__heading`, btn) : btn, region);
  function setOpen(v) {
    if (v) build();
    btn.setAttribute("aria-expanded", String(v));
    region.hidden = !v;
    root.classList.toggle("is-open", v);
    onToggle?.(v);
  }
  root.setOpen = setOpen;
  return root;
}

/**
 * Tabs (WAI-ARIA tabs pattern): roving tabindex, arrows/Home/End move and activate. tabs: [{id, label, icon?, count?}].
 * Returns the tablist element with .select(id) and .setCount(id, n).
 */
export function tabList({ tabs, value, onChange, label, idPrefix = uid("tab"), className = "", variant = "line" } = {}) {
  const list = h("div", { class: ["tabs", `tabs--${variant}`, className], role: "tablist", "aria-label": label || null });
  const btns = tabs.map((tab) => {
    const count = h("span.tabs__count.num", { hidden: !tab.count }, tab.count ? String(tab.count) : "");
    const b = h("button", {
      type: "button", role: "tab", class: "tabs__tab", id: `${idPrefix}-${tab.id}`, "aria-controls": `${idPrefix}-${tab.id}-panel`,
      "aria-selected": String(tab.id === value), tabindex: tab.id === value ? "0" : "-1", "data-tab": tab.id,
      "data-tip": tab.tip || null,
      onClick: () => select(tab.id, true),
    }, tab.icon ? icon(tab.icon, { size: 14 }) : null, h("span.tabs__label", tab.label), count);
    b._count = count;
    return b;
  });
  list.append(...btns);
  list.addEventListener("keydown", (e) => {
    const i = btns.indexOf(document.activeElement);
    if (i < 0) return;
    let j = -1;
    if (e.key === "ArrowRight") j = (i + 1) % btns.length;
    else if (e.key === "ArrowLeft") j = (i - 1 + btns.length) % btns.length;
    else if (e.key === "Home") j = 0;
    else if (e.key === "End") j = btns.length - 1;
    if (j < 0) return;
    e.preventDefault();
    btns[j].focus();
    select(btns[j].dataset.tab, true);
  });
  function select(id, fire) {
    for (const b of btns) {
      const on = b.dataset.tab === id;
      b.setAttribute("aria-selected", String(on));
      b.tabIndex = on ? 0 : -1;
    }
    if (fire) onChange?.(id);
  }
  list.select = (id) => select(id, false);
  list.setCount = (id, n) => {
    const b = btns.find((x) => x.dataset.tab === id);
    if (!b) return;
    b._count.hidden = !n;
    b._count.textContent = n ? String(n) : "";
  };
  list.panelId = (id) => `${idPrefix}-${id}-panel`;
  list.tabId = (id) => `${idPrefix}-${id}`;
  return list;
}

/** A copy button; `text` may be a function evaluated on click. */
export function copyButton(text, { label = t("ui.action.copy"), done = t("ui.toast.copied"), size = "sm", variant = "ghost", withLabel = false, className = "" } = {}) {
  const onClick = async (e) => {
    e.stopPropagation();
    const ok = await copyText(typeof text === "function" ? text() : text);
    toast(ok ? done : t("ui.toast.copy_failed"), { tone: ok ? "ok" : "warn" });
    if (ok) {
      const btn = e.currentTarget;
      btn.classList.add("is-done");
      setTimeout(() => btn.classList.remove("is-done"), 1200);
    }
  };
  if (withLabel) return button({ label, icon: "copy", size, variant, onClick, className });
  return iconButton({ icon: "copy", label, onClick, size, variant, className: ["copy-btn", className].join(" ") });
}

/** dl.kv: rows [[label, value(Node|string)], …]; empty values become "—". */
export function keyValue(rows, { className = "" } = {}) {
  return h("dl", { class: ["kv", className] }, rows.filter(Boolean).map(([k, v]) => [
    h("dt", k),
    h("dd", v === null || v === undefined || v === "" ? h("span.muted", "—") : v),
  ]));
}

export function emptyState({ icon: ic, title, body, actions = [], className = "" } = {}) {
  return h("div", { class: ["empty", className] },
    ic ? h("div.empty__icon", icon(ic, { size: 20 })) : null,
    title ? h("p.empty__title", title) : null,
    body ? h("p.empty__body", body) : null,
    actions.length ? h("div.empty__actions", actions) : null);
}

/** A section with a small heading row: title, optional count and trailing actions. */
export function section({ title, count, actions = [], children = [], className = "", id, level = 2 } = {}) {
  return h("section", { class: ["section", className], id: id || null },
    h("div.section__head",
      h(`h${level}.section__title`, title, count === undefined || count === null ? null : h("span.section__count.num", String(count))),
      actions.length ? h("div.section__actions", actions) : null),
    children);
}

/** A tiny inline notice. tone: info | ok | warn | danger | neutral. Refusals use "neutral" or "refusal", never "danger". */
export function notice({ tone = "info", icon: ic, title, body, actions = [], className = "", role } = {}) {
  const icons = { info: "info", ok: "circleCheck", warn: "alert", danger: "alert", neutral: "info", refusal: "ban" };
  return h("div", { class: ["notice", `notice--${tone}`, className], role: role || null },
    icon(ic || icons[tone] || "info", { size: 16, className: "notice__icon" }),
    h("div.notice__text", title ? h("p.notice__title", title) : null, body ? (body instanceof Node ? body : h("p.notice__body", body)) : null,
      actions.length ? h("div.notice__actions", actions) : null));
}
