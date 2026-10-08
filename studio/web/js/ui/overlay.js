// Overlays: toasts, live-region announcements, tooltips, dialogs (native <dialog>, focus kept inside, focus returned to
// the trigger), popovers and menus (positioned against an anchor, light-dismiss, Esc), hover cards, and a focus trap
// for drawers and sheets. Esc closes the topmost overlay first (DESIGN §8.1).

import { t } from "../core/i18n.js";
import { clamp, clear, h, uid } from "./dom.js";
import { icon } from "./icons.js";

// ------------------------------------------------------------------------------------------------- live regions

/** Say something to screen readers. Assertive is for refusals and errors, once (DESIGN §8.2). */
export function announce(text, { assertive = false } = {}) {
  const el = document.getElementById(assertive ? "announcer-assertive" : "announcer");
  if (!el) return;
  el.textContent = "";
  // a new text node after a frame, so the same sentence twice is still announced
  requestAnimationFrame(() => { el.textContent = String(text || ""); });
}

// ------------------------------------------------------------------------------------------------- toasts

/** Toasts with an action (Undo, Stop) stay at least this long, and never close while pointed at or focused. */
export const ACTION_TOAST_MS = 10000;

/**
 * A short notice. tone: neutral | ok | warn. action: {label, onClick}. A toast with an action stays at least
 * ACTION_TOAST_MS, pauses while the pointer or focus is on it (WCAG 2.2.1), and has its own dismiss button.
 */
export function toast(message, { tone = "neutral", action, timeout = 3200 } = {}) {
  let region = document.getElementById("toasts");
  if (!region) {
    region = h("div#toasts.toast-region", { role: "region", "aria-live": "polite" });
    document.body.appendChild(region);
  }
  const icons = { ok: "circleCheck", warn: "alert", neutral: "info" };
  if (action && timeout) timeout = Math.max(timeout, ACTION_TOAST_MS);
  let timer = 0;
  let closed = false;
  const close = () => {
    if (closed) return;
    closed = true;
    clearTimeout(timer);
    el.classList.add("is-leaving");
    setTimeout(() => el.remove(), 200);
  };
  const el = h("div", { class: ["toast", `toast--${tone}`, action && "toast--action"], role: "status" },
    icon(icons[tone] || "info", { size: 16, className: "toast__icon" }),
    h("span.toast__text", message),
    action ? h("button.toast__action", { type: "button", onClick: () => { action.onClick?.(); close(); } }, action.label) : null,
    action ? h("button.toast__close", { type: "button", "aria-label": t("ui.action.close"), onClick: close }, icon("x", { size: 14 })) : null);
  region.appendChild(el);
  while (region.children.length > 3) region.firstChild.remove();
  if (timeout) {
    let held = 0;
    const arm = () => { clearTimeout(timer); if (!held && !closed) timer = setTimeout(close, timeout); };
    const hold = () => { held++; clearTimeout(timer); };
    const release = () => { held = Math.max(0, held - 1); arm(); };
    el.addEventListener("pointerenter", hold);
    el.addEventListener("pointerleave", release);
    el.addEventListener("focusin", hold);
    el.addEventListener("focusout", (e) => { if (!el.contains(e.relatedTarget)) release(); });
    arm();
  }
  return close;
}

// ------------------------------------------------------------------------------------------------- Esc stack

const stack = []; // open overlays, topmost last: {el, close, modal}

function pushOverlay(entry) {
  stack.push(entry);
  return () => {
    const i = stack.indexOf(entry);
    if (i >= 0) stack.splice(i, 1);
  };
}

/** Close the topmost overlay; true when one was closed (the app's Esc handler asks first). */
export function closeTopOverlay() {
  const top = stack[stack.length - 1];
  if (!top) return false;
  top.close();
  return true;
}

export const hasOverlay = () => stack.length > 0;

// ------------------------------------------------------------------------------------------------- focus

const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"]), [contenteditable="true"]';

/**
 * The elements Tab stops on, in order. A roving-tabindex item (an inactive tab, tabindex=-1) is not one of them: it
 * would make the trap's first/last wrong and let Shift+Tab out of a modal drawer.
 */
export function focusables(root) {
  const tabIndex = (el) => (typeof el.tabIndex === "number" ? el.tabIndex : Number(el.getAttribute("tabindex") ?? 0));
  return [...root.querySelectorAll(FOCUSABLE)].filter((el) => tabIndex(el) >= 0 && ((!el.hidden && el.offsetParent !== null) || el === document.activeElement));
}

/**
 * Keep Tab inside `root` until the returned function is called; focus returns to what had it, or — when that is gone
 * (re-rendered) or was <body> — to `returnTo` (an element or a function that finds one).
 */
export function trapFocus(root, { initial, returnTo } = {}) {
  const previous = document.activeElement;
  const onKey = (e) => {
    if (e.key !== "Tab") return;
    const list = focusables(root);
    if (!list.length) { e.preventDefault(); return; }
    const first = list[0];
    const last = list[list.length - 1];
    if (e.shiftKey && (document.activeElement === first || !root.contains(document.activeElement))) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && (document.activeElement === last || !root.contains(document.activeElement))) { e.preventDefault(); first.focus(); }
  };
  root.addEventListener("keydown", onKey);
  requestAnimationFrame(() => {
    const target = (typeof initial === "string" ? root.querySelector(initial) : initial) || focusables(root)[0] || root;
    if (target === root && !root.hasAttribute("tabindex")) root.setAttribute("tabindex", "-1");
    target.focus({ preventScroll: true });
  });
  return () => {
    root.removeEventListener("keydown", onKey);
    const back = previous && previous !== document.body && previous.isConnected && typeof previous.focus === "function"
      ? previous : (typeof returnTo === "function" ? returnTo() : returnTo);
    back?.focus?.({ preventScroll: true });
  };
}

// ------------------------------------------------------------------------------------------------- dialogs

/**
 * A modal dialog. body: Node or (close) => Node. actions: Nodes for the footer. size: sm | md | lg.
 * Returns {el, close, setBody}. Uses <dialog>.showModal(), so the rest of the page is inert.
 */
export function openDialog({ title, description, body, actions = [], size = "md", onClose, className = "", initialFocus, dismissible = true, labelledBy } = {}) {
  const titleId = uid("dlg-t");
  const descId = description ? uid("dlg-d") : null;
  const dlg = h("dialog", { class: ["dialog", `dialog--${size}`, className], "aria-labelledby": title ? labelledBy || titleId : labelledBy || null, "aria-describedby": descId });
  let closed = false;
  const close = (value) => {
    if (closed) return;
    closed = true;
    releaseStack();
    releaseTrap();
    dlg.classList.add("is-leaving");
    const finish = () => { try { dlg.close(); } catch { /* closed */ } dlg.remove(); onClose?.(value); };
    if (matchMedia("(prefers-reduced-motion: reduce)").matches) finish();
    else setTimeout(finish, 140);
  };
  const head = title ? h("header.dialog__head",
    h("h2.dialog__title", { id: titleId }, title),
    dismissible ? h("button.icon-btn.icon-btn--ghost.icon-btn--md.dialog__close", { type: "button", "aria-label": t("ui.action.close"), onClick: () => close(null) }, icon("x")) : null) : null;
  const content = h("div.dialog__body");
  const setBody = (b) => {
    clear(content);
    const node = typeof b === "function" ? b(close) : b;
    if (node) content.append(node);
  };
  setBody(body);
  dlg.append(h("div.dialog__panel",
    head,
    description ? h("p.dialog__desc", { id: descId }, description) : null,
    content,
    actions.length ? h("footer.dialog__foot", actions) : null));
  dlg.addEventListener("cancel", (e) => { e.preventDefault(); if (dismissible) close(null); });
  dlg.addEventListener("mousedown", (e) => { if (e.target === dlg && dismissible) close(null); });
  document.body.appendChild(dlg);
  dlg.showModal();
  const releaseStack = pushOverlay({ el: dlg, close: () => dismissible && close(null), modal: true });
  const releaseTrap = trapFocus(dlg, { initial: initialFocus });
  return { el: dlg, close, setBody };
}

/** Resolves true on confirm. danger: the confirm button is the danger variant (delete). */
export function confirmDialog({ title, body, confirmLabel = t("ui.action.confirm"), cancelLabel = t("ui.action.cancel"), danger = false } = {}) {
  return new Promise((resolve) => {
    let result = false;
    const ok = h("button", { type: "button", class: ["btn", "btn--md", danger ? "btn--danger" : "btn--primary"], onClick: () => { result = true; d.close(true); } }, confirmLabel);
    const cancel = h("button.btn.btn--md.btn--secondary", { type: "button", onClick: () => d.close(false) }, cancelLabel);
    const d = openDialog({
      title, size: "sm", body: typeof body === "string" ? h("p.dialog__text", body) : body, actions: [cancel, ok],
      initialFocus: danger ? cancel : ok, onClose: () => resolve(result),
    });
  });
}

/** Resolves the entered text, or null when cancelled. */
export function promptDialog({ title, label, value = "", placeholder = "", confirmLabel = t("ui.action.save"), maxLength = 200 } = {}) {
  return new Promise((resolve) => {
    let result = null;
    const id = uid("pr");
    const input = h("input.input", { id, type: "text", placeholder, maxlength: String(maxLength), autocomplete: "off" });
    input.value = value;
    const submit = () => { const v = input.value.trim(); if (!v) return; result = v; d.close(v); };
    input.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.isComposing && e.keyCode !== 229) { e.preventDefault(); submit(); } });
    const d = openDialog({
      title, size: "sm",
      body: h("div.field", label ? h("label.field__label", { for: id }, label) : null, input),
      actions: [
        h("button.btn.btn--md.btn--secondary", { type: "button", onClick: () => d.close(null) }, t("ui.action.cancel")),
        h("button.btn.btn--md.btn--primary", { type: "button", onClick: submit }, confirmLabel),
      ],
      initialFocus: input, onClose: () => resolve(result),
    });
    requestAnimationFrame(() => input.select());
  });
}

// ------------------------------------------------------------------------------------------------- popovers

let layer = null;
function overlayLayer() {
  if (!layer || !layer.isConnected) {
    layer = h("div.overlay-layer");
    document.body.appendChild(layer);
  }
  return layer;
}

/**
 * A popover next to `anchor`. content: Node or (close) => Node. placement: "bottom-start" | "bottom-end" | "top-start"
 * | "top-end". Light dismiss (outside click, Esc, focus leaving). focus: move focus in on open; restoreFocus: put it
 * back on the anchor when the popover closes by keyboard (Esc, an item chosen, Tab out) — a menu does not take focus
 * itself (it focuses its first item) but still returns it. An anchor that is missing or detached opens nothing.
 */
export function openPopover(anchor, content, { placement = "bottom-start", className = "", onClose, width, label, focus = true, restoreFocus = focus, role = "dialog", returnTo } = {}) {
  if (!anchor || !anchor.isConnected) return { el: null, close() {}, place() {} };
  const pop = h("div", { class: ["popover", className], role, "aria-label": label || null, tabindex: "-1", style: width ? { width: typeof width === "number" ? `${width}px` : width } : null });
  let closed = false;
  let releaseStack = () => {};
  const close = (opts = {}) => {
    if (closed) return;
    closed = true;
    releaseStack();
    document.removeEventListener("pointerdown", onDown, true);
    window.removeEventListener("resize", place);
    window.removeEventListener("scroll", place, true);
    anchor.setAttribute?.("aria-expanded", "false");
    pop.classList.add("is-leaving");
    setTimeout(() => pop.remove(), 120);
    const inside = pop.contains(document.activeElement) || !document.activeElement || document.activeElement === document.body;
    // the anchor may have been re-rendered while the popover was open (a status change redraws the top bar)
    const back = anchor.isConnected ? anchor : returnTo?.();
    if (opts.restoreFocus !== false && restoreFocus && inside && back?.isConnected) back.focus({ preventScroll: true });
    onClose?.();
  };
  const node = typeof content === "function" ? content(close) : content;
  if (node) pop.append(node);
  function place() {
    // an anchor re-rendered underneath an open popover: keep the popover where it is
    if (!anchor.isConnected) return;
    const a = anchor.getBoundingClientRect();
    const p = pop.getBoundingClientRect();
    const vw = document.documentElement.clientWidth;
    const vh = document.documentElement.clientHeight;
    const gap = 6;
    let [side, align] = placement.split("-");
    if (side === "bottom" && a.bottom + gap + p.height > vh - 8 && a.top - gap - p.height > 8) side = "top";
    else if (side === "top" && a.top - gap - p.height < 8 && a.bottom + gap + p.height < vh - 8) side = "bottom";
    let top = side === "bottom" ? a.bottom + gap : a.top - gap - p.height;
    let left = align === "end" ? a.right - p.width : a.left;
    left = clamp(left, 8, Math.max(8, vw - p.width - 8));
    top = clamp(top, 8, Math.max(8, vh - p.height - 8));
    pop.style.left = `${Math.round(left)}px`;
    pop.style.top = `${Math.round(top)}px`;
    pop.dataset.side = side;
  }
  const onDown = (e) => {
    if (pop.contains(e.target) || anchor.contains(e.target)) return;
    // a click inside a modal dialog opened from this popover must not close it
    if (e.target.closest?.("dialog[open]") && !pop.closest("dialog")) return;
    close({ restoreFocus: false });
  };
  // listeners and the Esc stack first, then into the page: nothing can leave a popover on screen without them
  document.addEventListener("pointerdown", onDown, true);
  window.addEventListener("resize", place);
  window.addEventListener("scroll", place, true);
  pop.addEventListener("keydown", (e) => {
    if (e.key === "Escape") { e.stopPropagation(); close(); }
  });
  pop.addEventListener("focusout", (e) => {
    const to = e.relatedTarget;
    if (to && (pop.contains(to) || anchor.contains(to) || to.closest?.(".popover, dialog"))) return;
    if (to) { close({ restoreFocus: false }); return; }
    // Tab past the last item lands on nothing (the popover is at the end of the page): close and go back to the anchor
    requestAnimationFrame(() => {
      const a = document.activeElement;
      if (!closed && (!a || a === document.body) && role !== "tooltip") close();
    });
  });
  releaseStack = pushOverlay({ el: pop, close });
  overlayLayer().appendChild(pop);
  anchor.setAttribute?.("aria-expanded", "true");
  place();
  requestAnimationFrame(place);
  if (focus) requestAnimationFrame(() => (focusables(pop)[0] || pop).focus({ preventScroll: true }));
  return { el: pop, close, place };
}

/**
 * A menu (role=menu) of items [{label, icon?, onSelect, danger?, disabled?, kbd?} | "-"]. Arrow keys move, Enter
 * selects, typing a letter jumps.
 */
export function openMenu(anchor, items, { placement = "bottom-end", label } = {}) {
  let ctl;
  const buttons = [];
  const list = h("div.menu", { role: "none" });
  for (const it of items) {
    if (it === "-") { list.append(h("div.menu__sep", { role: "separator" })); continue; }
    const b = h("button", {
      type: "button", role: "menuitem", class: ["menu__item", it.danger && "is-danger"], disabled: it.disabled, tabindex: "-1",
      onClick: () => { ctl.close(); it.onSelect?.(); },
    }, it.icon ? icon(it.icon, { size: 16 }) : h("span.menu__spacer"), h("span.menu__label", it.label), it.hint ? h("span.menu__hint", it.hint) : null);
    buttons.push(b);
    list.append(b);
  }
  ctl = openPopover(anchor, list, { placement, className: "popover--menu", label, role: "menu", focus: false, restoreFocus: true });
  if (!ctl.el) return ctl;
  const focusAt = (i) => { const b = buttons[(i + buttons.length) % buttons.length]; b?.focus(); };
  requestAnimationFrame(() => focusAt(0));
  ctl.el.addEventListener("keydown", (e) => {
    const i = buttons.indexOf(document.activeElement);
    if (e.key === "ArrowDown") { e.preventDefault(); focusAt(i + 1); }
    else if (e.key === "ArrowUp") { e.preventDefault(); focusAt(i - 1); }
    else if (e.key === "Home") { e.preventDefault(); focusAt(0); }
    else if (e.key === "End") { e.preventDefault(); focusAt(buttons.length - 1); }
    else if (e.key === "Tab") { e.preventDefault(); ctl.close(); }
    else if (e.key.length === 1 && /\S/.test(e.key)) {
      const k = e.key.toLowerCase();
      const j = buttons.findIndex((b, n) => n > i && b.textContent.trim().toLowerCase().startsWith(k));
      const jj = j >= 0 ? j : buttons.findIndex((b) => b.textContent.trim().toLowerCase().startsWith(k));
      if (jj >= 0) focusAt(jj);
    }
  });
  return ctl;
}

// ------------------------------------------------------------------------------------------------- tooltips

let tipEl = null;
let tipTimer = 0;
let tipFor = null;

/** One delegated tooltip for every [data-tip] element: on hover after a short delay, on keyboard focus at once. */
export function installTooltips(root = document) {
  const show = (target, delay) => {
    clearTimeout(tipTimer);
    tipTimer = setTimeout(() => {
      const text = target.getAttribute("data-tip");
      if (!text || !target.isConnected) return;
      if (!tipEl) {
        tipEl = h("div.tooltip", { role: "tooltip", id: "ui-tooltip" });
        document.body.appendChild(tipEl);
      }
      tipEl.textContent = text;
      tipEl.classList.add("is-visible");
      tipFor = target;
      const a = target.getBoundingClientRect();
      const p = tipEl.getBoundingClientRect();
      const vw = document.documentElement.clientWidth;
      let top = a.bottom + 6;
      if (top + p.height > document.documentElement.clientHeight - 6) top = a.top - p.height - 6;
      const left = clamp(a.left + a.width / 2 - p.width / 2, 6, vw - p.width - 6);
      tipEl.style.left = `${Math.round(left)}px`;
      tipEl.style.top = `${Math.round(top)}px`;
    }, delay);
  };
  const hide = () => {
    clearTimeout(tipTimer);
    tipFor = null;
    tipEl?.classList.remove("is-visible");
  };
  root.addEventListener("pointerover", (e) => {
    if (e.pointerType === "touch") return;
    const target = e.target.closest?.("[data-tip]");
    if (target && target !== tipFor) show(target, 450);
    if (!target) hide();
  });
  root.addEventListener("pointerout", (e) => { if (!e.relatedTarget || !e.relatedTarget.closest?.("[data-tip]")) hide(); });
  root.addEventListener("focusin", (e) => {
    const target = e.target.closest?.("[data-tip]");
    if (target && target.matches(":focus-visible")) show(target, 80);
  });
  root.addEventListener("focusout", hide);
  root.addEventListener("pointerdown", hide, true);
  root.addEventListener("keydown", (e) => { if (e.key === "Escape") hide(); }, true);
}

// ------------------------------------------------------------------------------------------------- hover cards

/**
 * A hover card for `target` (citation chips): shows `build()` after a delay on hover or at once on focus, stays while
 * the pointer is over it, and hides on leave or Esc. Returns a remover.
 */
export function attachHoverCard(target, build, { placement = "top-start", width = 360 } = {}) {
  let ctl = null;
  let timer = 0;
  let via = "pointer";
  const open = (delay) => {
    clearTimeout(timer);
    timer = setTimeout(() => {
      if (ctl || !target.isConnected) return;
      // from the pointer the card may hold links (role=dialog, non-modal); from keyboard focus it is a tooltip: text
      // only, since focus cannot move into it (its facts are in the inspector, one key away)
      const interactive = via === "pointer";
      ctl = openPopover(target, build({ interactive }), { placement, className: "popover--hovercard", width, focus: false, restoreFocus: false, role: interactive ? "dialog" : "tooltip", label: interactive ? target.getAttribute("aria-label") : null, onClose: () => { ctl = null; } });
      if (!ctl.el) { ctl = null; return; }
      target.setAttribute("aria-describedby", ctl.el.id || (ctl.el.id = uid("hc")));
      ctl.el.addEventListener("pointerenter", () => clearTimeout(timer));
      ctl.el.addEventListener("pointerleave", () => leave());
    }, delay);
  };
  const leave = () => {
    clearTimeout(timer);
    timer = setTimeout(() => { ctl?.close({ restoreFocus: false }); target.removeAttribute("aria-describedby"); }, 160);
  };
  const onEnter = (e) => { if (e.pointerType !== "touch") { via = "pointer"; open(260); } };
  const onFocus = () => { if (target.matches(":focus-visible")) { via = "focus"; open(0); } };
  target.addEventListener("pointerenter", onEnter);
  target.addEventListener("pointerleave", leave);
  target.addEventListener("focus", onFocus);
  target.addEventListener("blur", leave);
  return () => {
    target.removeEventListener("pointerenter", onEnter);
    target.removeEventListener("pointerleave", leave);
    target.removeEventListener("focus", onFocus);
    target.removeEventListener("blur", leave);
    ctl?.close({ restoreFocus: false });
  };
}
