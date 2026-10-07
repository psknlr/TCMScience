// A tiny DOM toolkit for the UI: h() builds elements (never parses HTML), plus the handful of helpers every view
// needs (clear, replace, delegate, copy, download, frame throttling, formatting of sizes, durations and times).

import { lang } from "../core/i18n.js";

/**
 * h("div.card.is-open#id", {attrs, on*: handlers, dataset, style, aria-*}, ...children)
 * - the selector's tag defaults to div; ".a.b" adds classes; "#x" sets the id
 * - attrs: `class` (string or array; falsy entries dropped), `on: {click: fn}` or `onClick: fn`, `dataset`, `style`
 *   (object or string), boolean true → empty attribute, false/null/undefined → omitted; `prop:value` sets a property
 * - children: strings, numbers, Nodes, arrays (flattened), null/false (skipped)
 */
export function h(sel, attrs, ...children) {
  if (attrs && (typeof attrs !== "object" || attrs instanceof Node || Array.isArray(attrs))) {
    children.unshift(attrs);
    attrs = null;
  }
  const m = /^([a-z][\w-]*)?((?:[.#][\w-]+)*)$/i.exec(sel || "div");
  const tag = (m && m[1]) || "div";
  const el = document.createElement(tag);
  if (m && m[2]) {
    for (const part of m[2].match(/[.#][\w-]+/g)) {
      if (part[0] === ".") el.classList.add(part.slice(1));
      else el.id = part.slice(1);
    }
  }
  if (attrs) applyAttrs(el, attrs);
  append(el, children);
  return el;
}

function applyAttrs(el, attrs) {
  for (const [k, v] of Object.entries(attrs)) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class" || k === "className") {
      const list = (Array.isArray(v) ? v : [v]).flatMap((c) => (c ? String(c).split(/\s+/) : []));
      for (const c of list) if (c) el.classList.add(c);
    } else if (k === "on") {
      for (const [type, fn] of Object.entries(v)) if (fn) el.addEventListener(type, fn);
    } else if (/^on[A-Z]/.test(k) && typeof v === "function") {
      el.addEventListener(k.slice(2).toLowerCase(), v);
    } else if (k === "dataset") {
      for (const [dk, dv] of Object.entries(v)) if (dv !== undefined && dv !== null) el.dataset[dk] = String(dv);
    } else if (k === "style") {
      if (typeof v === "string") el.setAttribute("style", v);
      else for (const [sk, sv] of Object.entries(v)) if (sv !== undefined && sv !== null) {
        if (sk.startsWith("--")) el.style.setProperty(sk, String(sv));
        else el.style[sk] = sv;
      }
    } else if (k.startsWith("prop:")) {
      el[k.slice(5)] = v;
    } else if (k === "text") {
      el.textContent = String(v);
    } else {
      el.setAttribute(k, v === true ? "" : String(v));
    }
  }
}

export function append(el, children) {
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false || c === true) continue;
    el.appendChild(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

/** Remove every child. */
export function clear(el) {
  while (el && el.firstChild) el.removeChild(el.firstChild);
  return el;
}

/** Replace an element's children. */
export function fill(el, ...children) {
  clear(el);
  return append(el, children);
}

/** Listen on `root` for events from descendants that match `selector`; returns the remover. */
export function delegate(root, type, selector, fn, opts) {
  const handler = (e) => {
    const target = e.target instanceof Element ? e.target.closest(selector) : null;
    if (target && root.contains(target)) fn(e, target);
  };
  root.addEventListener(type, handler, opts);
  return () => root.removeEventListener(type, handler, opts);
}

/** Run fn at most once per animation frame, with the latest arguments. */
export function rafThrottle(fn) {
  let queued = false;
  let args = [];
  return (...a) => {
    args = a;
    if (queued) return;
    queued = true;
    requestAnimationFrame(() => { queued = false; fn(...args); });
  };
}

export function debounce(fn, ms) {
  let timer = 0;
  const d = (...a) => { clearTimeout(timer); timer = setTimeout(() => fn(...a), ms); };
  d.cancel = () => clearTimeout(timer);
  d.flush = (...a) => { clearTimeout(timer); fn(...a); };
  return d;
}

let idSeq = 0;
/** A page-unique id for aria-controls / aria-labelledby. */
export function uid(prefix = "ui") {
  idSeq += 1;
  return `${prefix}-${idSeq.toString(36)}`;
}

/** Copy text; resolves true when it worked. Falls back to a hidden textarea where the async API is unavailable. */
export async function copyText(text) {
  const s = String(text ?? "");
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(s);
      return true;
    }
  } catch { /* fall through */ }
  try {
    const ta = h("textarea", { style: "position:fixed;left:-9999px;top:0;opacity:0", "aria-hidden": "true" });
    ta.value = s;
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand("copy");
    ta.remove();
    return ok;
  } catch {
    return false;
  }
}

/** Save a Blob (or text) as a file. */
export function download(name, data, type = "application/octet-stream") {
  const blob = data instanceof Blob ? data : new Blob([data], { type });
  const url = URL.createObjectURL(blob);
  const a = h("a", { href: url, download: name, style: "display:none" });
  document.body.appendChild(a);
  a.click();
  setTimeout(() => { URL.revokeObjectURL(url); a.remove(); }, 1000);
}

// ------------------------------------------------------------------------------------------------- formatting

const nf = () => new Intl.NumberFormat(lang() === "zh" ? "zh-CN" : "en-US");

export function formatNumber(n, opts) {
  if (n === null || n === undefined || Number.isNaN(Number(n))) return "—";
  return new Intl.NumberFormat(lang() === "zh" ? "zh-CN" : "en-US", opts).format(Number(n));
}

/** 1,579 字节 / 1.5 KB / 12.4 MB — always with a unit. */
export function formatBytes(bytes) {
  const b = Number(bytes);
  if (!Number.isFinite(b) || b < 0) return "—";
  const zh = lang() === "zh";
  // exact bytes while they are still readable (a hash-verified output's size is a fact, not an estimate)
  if (b < 10000) return zh ? `${nf().format(b)} 字节` : `${nf().format(b)} bytes`;
  const units = ["KB", "MB", "GB", "TB"];
  let v = b / 1024;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
  return `${v >= 100 ? Math.round(v) : v.toFixed(1)} ${units[i]}`;
}

/** 840 毫秒 / 3.2 秒 / 2 分 14 秒 (zh); 840 ms / 3.2 s / 2 min 14 s (en). */
export function formatDuration(ms) {
  const v = Number(ms);
  if (!Number.isFinite(v) || v < 0) return "—";
  const zh = lang() === "zh";
  if (v < 1000) return zh ? `${Math.round(v)} 毫秒` : `${Math.round(v)} ms`;
  const s = v / 1000;
  if (s < 60) return zh ? `${s < 10 ? s.toFixed(1) : Math.round(s)} 秒` : `${s < 10 ? s.toFixed(1) : Math.round(s)} s`;
  const m = Math.floor(s / 60);
  const rest = Math.round(s % 60);
  if (m < 60) return zh ? `${m} 分 ${rest} 秒` : `${m} min ${rest} s`;
  const hh = Math.floor(m / 60);
  return zh ? `${hh} 小时 ${m % 60} 分` : `${hh} h ${m % 60} min`;
}

/** The absolute date-time with its time zone, for tooltips (DESIGN §7.1 rule 5). */
export function formatDateTime(t) {
  const d = toDate(t);
  if (!d) return "";
  return new Intl.DateTimeFormat(lang() === "zh" ? "zh-CN" : "en-GB", {
    year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", timeZoneName: "short",
  }).format(d);
}

/** "14:02": the local time of day, 24-hour, for a message's meta line (the full date-time goes in its tooltip). */
export function formatClock(t) {
  const d = toDate(t);
  if (!d) return "";
  return new Intl.DateTimeFormat(lang() === "zh" ? "zh-CN" : "en-GB", { hour: "2-digit", minute: "2-digit", hour12: false }).format(d);
}

/** "刚刚 / 5 分钟前 / 昨天 14:02 / 10月3日" — relative, short. */
export function formatRelative(t, now = Date.now()) {
  const d = toDate(t);
  if (!d) return "";
  const zh = lang() === "zh";
  const diff = (now - d.getTime()) / 1000;
  if (diff < 45) return zh ? "刚刚" : "just now";
  if (diff < 3600) {
    const m = Math.max(1, Math.round(diff / 60));
    return zh ? `${m} 分钟前` : `${m} min ago`;
  }
  const loc = zh ? "zh-CN" : "en-GB";
  const time = new Intl.DateTimeFormat(loc, { hour: "2-digit", minute: "2-digit" }).format(d);
  const group = dateGroup(d.getTime(), now);
  if (group === "today") return time;
  if (group === "yesterday") return zh ? `昨天 ${time}` : `Yesterday ${time}`;
  const sameYear = d.getFullYear() === new Date(now).getFullYear();
  return new Intl.DateTimeFormat(loc, sameYear ? { month: "short", day: "numeric" } : { year: "numeric", month: "short", day: "numeric" }).format(d);
}

/** today | yesterday | week | older — the sidebar's date groups (今天 / 昨天 / 过去 7 天 / 更早). */
export function dateGroup(t, now = Date.now()) {
  const d = toDate(t);
  if (!d) return "older";
  const start = new Date(now);
  start.setHours(0, 0, 0, 0);
  const day = 86400000;
  const ts = d.getTime();
  if (ts >= start.getTime()) return "today";
  if (ts >= start.getTime() - day) return "yesterday";
  if (ts >= start.getTime() - 6 * day) return "week";
  return "older";
}

function toDate(t) {
  if (t === null || t === undefined || t === "") return null;
  const d = t instanceof Date ? t : new Date(typeof t === "number" ? t : String(t));
  return Number.isNaN(d.getTime()) ? null : d;
}

/** Is the platform a Mac (for ⌘ vs Ctrl in labels)? */
export const isMac = (() => {
  try { return /Mac|iPhone|iPad|iPod/i.test(navigator.userAgentData?.platform || navigator.platform || navigator.userAgent || ""); } catch { return false; }
})();

/** "⌘K" on a Mac, "Ctrl+K" elsewhere, from a neutral spec "mod+k", "mod+shift+o", "alt+\\". */
export function shortcutLabel(spec) {
  const parts = spec.split("+");
  const map = isMac
    ? { mod: "⌘", shift: "⇧", alt: "⌥", ctrl: "⌃", enter: "↩", esc: "Esc", up: "↑", down: "↓", slash: "/", backslash: "\\", semicolon: ";" }
    : { mod: "Ctrl", shift: "Shift", alt: "Alt", ctrl: "Ctrl", enter: "Enter", esc: "Esc", up: "↑", down: "↓", slash: "/", backslash: "\\", semicolon: ";" };
  const keys = parts.map((p) => map[p] || (p.length === 1 ? p.toUpperCase() : p));
  return isMac ? keys.join("") : keys.join("+");
}

/** Clamp a number. */
export const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));

/** Is the viewport narrower than a breakpoint (px)? */
export const below = (px) => window.matchMedia(`(max-width: ${px - 0.02}px)`).matches;
