// A small, safe Markdown renderer. It builds DOM nodes with createElement and text nodes and never parses HTML, so a
// model's answer cannot inject markup: raw HTML shows as the text it is (except <br>, which becomes a line break),
// links are kept only for http, https and mailto, and images are shown as links, never loaded.
//
// Blocks: ATX headings, paragraphs (a single newline is a line break, which suits Chinese text), nested lists (with
// task boxes), block quotes, fenced code, GFM tables, rules, $$ display math. Inline: code, **bold**, *italic*,
// ~~strike~~, links, autolinks and bare URLs, $math$ (kept as TeX in data-tex for the UI to typeset), and evidence
// citations [E1] · [E1, E2] · 【E1】 turned into chips by opts.onCitation(id) (a default chip otherwise).

import { t } from "./i18n.js";

const FENCE_OPEN = /^ {0,3}(`{3,}|~{3,})[ \t]*([^\s`]*)[^`]*$/;
const HEADING = /^ {0,3}(#{1,6})[ \t]+(.*?)(?:[ \t]+#+)?[ \t]*$/;
const RULE = /^ {0,3}([-*_])(?:[ \t]*\1){2,}[ \t]*$/;
const QUOTE = /^ {0,3}>[ \t]?(.*)$/;
const ITEM = /^([ \t]*)([-*+•](?=[ \t])|\d{1,9}[.)](?=[ \t])|\d{1,9}、)[ \t]*(\S.*)$/; // "1、甲" needs no space
const TABLE_SEP = /^[ \t]*\|?[ \t]*:?-{1,}:?[ \t]*(?:\|[ \t]*:?-{1,}:?[ \t]*)*\|?[ \t]*$/;
const CITE = /^[[【](E\d{1,4}(?:[ \t]*[,，、;；][ \t]*E\d{1,4})*)[\]】]/;
const MAX_NEST = 12; // quotes and lists nested deeper than this are shown as text, not recursed into
const URL_BARE = /^(https?:\/\/[^\s<>"'`，。、；：！？（）【】「」《》]+)/i;
const TRAILING_PUNCT = /[.,;:!?)\]}'"*_~]+$/;

/**
 * Render Markdown to a DocumentFragment.
 * opts: {onCitation(id) → Node|null, streaming (append a caret), document (a Document to build with; default the
 * page's), headingOffset (1: "#" becomes <h2>)}.
 */
export function renderMarkdown(text, opts = {}) {
  const doc = opts.document || globalThis.document;
  if (!doc) throw new Error("renderMarkdown needs a document");
  const ctx = { doc, opts, offset: opts.headingOffset ?? 1, nest: 0 };
  const frag = doc.createDocumentFragment();
  const lines = String(text ?? "").replace(/\r\n?/g, "\n").split("\n");
  blocks(lines, frag, ctx);
  if (opts.streaming) appendCaret(frag, ctx);
  return frag;
}

function el(ctx, tag, attrs, ...children) {
  const node = ctx.doc.createElement(tag);
  if (attrs) for (const [k, v] of Object.entries(attrs)) if (v !== undefined && v !== null && v !== false) node.setAttribute(k, v === true ? "" : String(v));
  for (const c of children.flat()) {
    if (c === null || c === undefined || c === false) continue;
    node.appendChild(typeof c === "string" ? ctx.doc.createTextNode(c) : c);
  }
  return node;
}

// ------------------------------------------------------------------------------------------------- blocks

function blocks(lines, parent, ctx) {
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) { i++; continue; }
    let m;

    if ((m = FENCE_OPEN.exec(line))) {
      const fence = m[1];
      const lang = m[2] || "";
      const body = [];
      i++;
      while (i < lines.length) {
        const close = /^ {0,3}(`{3,}|~{3,})[ \t]*$/.exec(lines[i]);
        if (close && close[1][0] === fence[0] && close[1].length >= fence.length) { i++; break; }
        body.push(lines[i]);
        i++;
      }
      parent.appendChild(codeBlock(body.join("\n"), lang, ctx));
      continue;
    }

    if (/^ {0,3}(\$\$|\\\[)[ \t]*$/.test(line) || /^ {0,3}\$\$.+\$\$[ \t]*$/.test(line)) {
      const single = /^ {0,3}\$\$(.+)\$\$[ \t]*$/.exec(line);
      if (single) { parent.appendChild(mathNode(single[1].trim(), true, ctx)); i++; continue; }
      const closer = line.trim() === "$$" ? "$$" : "\\]";
      const body = [];
      i++;
      while (i < lines.length && lines[i].trim() !== closer) body.push(lines[i++]);
      i++;
      parent.appendChild(mathNode(body.join("\n").trim(), true, ctx));
      continue;
    }

    if ((m = HEADING.exec(line))) {
      const level = Math.min(m[1].length + ctx.offset, 6);
      parent.appendChild(el(ctx, `h${level}`, null, inline(m[2], ctx)));
      i++;
      continue;
    }

    if (RULE.test(line)) { parent.appendChild(el(ctx, "hr")); i++; continue; }

    if (QUOTE.test(line) && ctx.nest < MAX_NEST) {
      const inner = [];
      while (i < lines.length && lines[i].trim() && QUOTE.test(lines[i])) inner.push(QUOTE.exec(lines[i++])[1]);
      const bq = el(ctx, "blockquote");
      blocks(inner, bq, { ...ctx, nest: ctx.nest + 1 });
      parent.appendChild(bq);
      continue;
    }

    if (line.includes("|") && i + 1 < lines.length && TABLE_SEP.test(lines[i + 1]) && lines[i + 1].includes("-")) {
      const header = line;
      const sep = lines[i + 1];
      const rows = [];
      i += 2;
      while (i < lines.length && lines[i].trim() && lines[i].includes("|")) rows.push(lines[i++]);
      parent.appendChild(table(header, sep, rows, ctx));
      continue;
    }

    if (ITEM.test(line) && ctx.nest < MAX_NEST) {
      const block = [];
      const kind = listKind(line);
      const base = indentOf(line);
      // another list kind at the same depth starts a new list
      const sameList = (l) => !ITEM.test(l) || indentOf(l) > base || listKind(l) === kind;
      while (i < lines.length) {
        const l = lines[i];
        if (!l.trim()) {
          // a blank line ends the list unless the list (or an item's continuation) goes on after it
          const next = lines[i + 1];
          if (next !== undefined && ((ITEM.test(next) && sameList(next)) || /^[ \t]{2,}\S/.test(next))) { block.push(l); i++; continue; }
          break;
        }
        if (block.length && !sameList(l)) break;
        if (block.length && !ITEM.test(l) && !/^[ \t]+\S/.test(l) && startsBlock(l)) break;
        block.push(l);
        i++;
      }
      parent.appendChild(list(block, ctx));
      continue;
    }

    const para = [];
    while (i < lines.length && lines[i].trim() && !(para.length && startsBlock(lines[i], lines[i + 1]))) para.push(lines[i++]);
    const p = el(ctx, "p");
    para.forEach((l, k) => {
      if (k) p.appendChild(el(ctx, "br"));
      p.appendChild(inline(l.replace(/^[ \t]+/, ""), ctx));
    });
    parent.appendChild(p);
  }
}

function indentOf(l) {
  return /^[ \t]*/.exec(l)[0].replace(/\t/g, "    ").length;
}

function listKind(l) {
  const m = ITEM.exec(l);
  return m && /\d/.test(m[2]) ? "ol" : "ul";
}

function startsBlock(line, next) {
  return FENCE_OPEN.test(line) || HEADING.test(line) || RULE.test(line) || QUOTE.test(line) || ITEM.test(line)
    || /^ {0,3}(\$\$|\\\[)/.test(line) || (line.includes("|") && next !== undefined && TABLE_SEP.test(next) && next.includes("-"));
}

function codeBlock(code, lang, ctx) {
  const codeEl = el(ctx, "code", lang ? { "data-lang": lang, class: `language-${lang.replace(/[^\w+#.-]/g, "")}` } : null, code);
  return el(ctx, "div", { class: "md-code" },
    el(ctx, "div", { class: "md-code-head" },
      el(ctx, "span", { class: "md-code-lang" }, lang || "text"),
      el(ctx, "button", { type: "button", class: "md-code-copy", "data-copy-code": "" }, t("core.md.copy"))),
    el(ctx, "pre", null, codeEl));
}

function mathNode(tex, display, ctx) {
  return el(ctx, display ? "div" : "span", { class: display ? "md-math md-math-block" : "md-math", "data-tex": tex, translate: "no" }, tex);
}

function table(header, sep, rows, ctx) {
  const aligns = splitRow(sep).map((c) => {
    const s = c.trim();
    if (s.startsWith(":") && s.endsWith(":")) return "center";
    if (s.endsWith(":")) return "right";
    if (s.startsWith(":")) return "left";
    return null;
  });
  const head = splitRow(header);
  const tableEl = el(ctx, "table");
  const thead = el(ctx, "thead");
  const tr = el(ctx, "tr");
  head.forEach((cell, k) => tr.appendChild(el(ctx, "th", alignAttr(aligns[k]), inline(cell.trim(), ctx))));
  thead.appendChild(tr);
  tableEl.appendChild(thead);
  const tbody = el(ctx, "tbody");
  for (const row of rows) {
    const cells = splitRow(row);
    const rowEl = el(ctx, "tr");
    for (let k = 0; k < head.length; k++) rowEl.appendChild(el(ctx, "td", alignAttr(aligns[k]), inline((cells[k] ?? "").trim(), ctx)));
    tbody.appendChild(rowEl);
  }
  tableEl.appendChild(tbody);
  return el(ctx, "div", { class: "md-table", tabindex: "0", role: "region" }, tableEl);
}

function alignAttr(a) {
  return a ? { style: `text-align:${a}` } : null;
}

/** Split a table row at pipes that are not escaped and not inside a code span. */
function splitRow(row) {
  let s = row.trim();
  if (s.startsWith("|")) s = s.slice(1);
  if (s.endsWith("|") && !s.endsWith("\\|")) s = s.slice(0, -1);
  const cells = [];
  let cur = "";
  let tick = 0;
  for (let i = 0; i < s.length; i++) {
    const c = s[i];
    if (c === "\\" && s[i + 1] === "|") { cur += "|"; i++; continue; }
    if (c === "`") {
      let n = 1;
      while (s[i + n] === "`") n++;
      tick = tick === n ? 0 : tick || n;
      cur += s.slice(i, i + n);
      i += n - 1;
      continue;
    }
    if (c === "|" && !tick) { cells.push(cur); cur = ""; continue; }
    cur += c;
  }
  cells.push(cur);
  return cells;
}

function list(lines, ctx) {
  const first = ITEM.exec(lines[0]);
  const base = indentOf(lines[0]);
  const ordered = /\d/.test(first[2]);
  const start = ordered ? parseInt(first[2], 10) : null;
  const listEl = el(ctx, ordered ? "ol" : "ul", ordered && start !== 1 ? { start } : null);
  let item = null;
  let body = [];
  const flush = () => {
    if (!item) return;
    const [head, ...rest] = body;
    const task = /^\[([ xX])\][ \t]+/.exec(head);
    const li = el(ctx, "li", task ? { class: "md-task", "data-checked": task[1] !== " " ? "true" : "false" } : null);
    if (task) li.appendChild(el(ctx, "span", { class: "md-task-box", "aria-hidden": "true" }, task[1] !== " " ? "☑ " : "☐ "));
    li.appendChild(inline(task ? head.slice(task[0].length) : head, ctx));
    const nested = rest.filter((l, k) => l.trim() || k < rest.length - 1);
    if (nested.some((l) => l.trim())) {
      const minIndent = Math.min(...nested.filter((l) => l.trim()).map(indentOf));
      const dedented = nested.map((l) => l.replace(/\t/g, "    ").slice(Math.min(minIndent, indentOf(l))));
      if (ITEM.test(dedented.find((l) => l.trim()) || "")) {
        const sub = ctx.doc.createDocumentFragment();
        blocks(dedented, sub, { ...ctx, nest: ctx.nest + 1 });
        li.appendChild(sub);
      } else {
        for (const l of dedented) {
          if (!l.trim()) continue;
          li.appendChild(el(ctx, "br"));
          li.appendChild(inline(l.trim(), ctx));
        }
      }
    }
    listEl.appendChild(li);
  };
  for (const l of lines) {
    const m = ITEM.exec(l);
    if (m && indentOf(l) <= base) {
      flush();
      item = m;
      body = [m[3]];
    } else {
      body.push(l);
    }
  }
  flush();
  return listEl;
}

function appendCaret(frag, ctx) {
  let target = frag.lastChild;
  while (target && target.nodeType === 1 && ["UL", "OL", "LI", "BLOCKQUOTE"].includes(target.nodeName.toUpperCase())) {
    const child = target.lastChild;
    if (child && child.nodeType === 1 && ["UL", "OL", "LI", "BLOCKQUOTE", "P"].includes(child.nodeName.toUpperCase())) target = child;
    else break;
  }
  const caret = el(ctx, "span", { class: "md-caret", "aria-hidden": "true" });
  if (target && target.nodeType === 1 && !["PRE", "TABLE", "HR", "DIV"].includes(target.nodeName.toUpperCase())) target.appendChild(caret);
  else frag.appendChild(el(ctx, "p", null, caret));
}

// ------------------------------------------------------------------------------------------------- inline

function inline(text, ctx) {
  const frag = ctx.doc.createDocumentFragment();
  parseInline(String(text ?? ""), frag, ctx, 0);
  return frag;
}

/** Allowed link targets: absolute http(s) and mailto. Everything else (javascript:, data:, relative…) is refused. */
export function safeHref(raw) {
  const s = String(raw ?? "").trim().replace(/^<|>$/g, "");
  if (!s || /[\s<>"]/.test(s)) return null;
  if (/^mailto:/i.test(s)) return /^mailto:[^@\s/:]+@[^@\s/:]+\.[^@\s/:]+$/i.test(s) ? s : null;
  if (!/^https?:\/\//i.test(s)) return null;
  try {
    const u = new URL(s);
    return u.protocol === "http:" || u.protocol === "https:" ? u.href : null;
  } catch {
    return null;
  }
}

function link(href, children, ctx, cls) {
  return el(ctx, "a", { href, target: "_blank", rel: "noopener noreferrer nofollow", class: cls }, children);
}

const isWordChar = (c) => Boolean(c) && /[A-Za-z0-9]/.test(c);

function parseInline(s, parent, ctx, depth) {
  // what is known to have no closer from a position on: keeps text full of unmatched ` * [ linear, not quadratic
  const noClose = new Map();
  let buf = "";
  const flushText = () => {
    if (buf) { parent.appendChild(ctx.doc.createTextNode(buf)); buf = ""; }
  };
  const push = (node) => { flushText(); parent.appendChild(node); };
  let i = 0;
  while (i < s.length) {
    const c = s[i];
    const rest = s.slice(i);
    let m;

    if (c === "\\" && i + 1 < s.length && /[!-/:-@[-`{-~]/.test(s[i + 1])) {
      if (s[i + 1] === "(" && depth < 4) {
        const end = s.indexOf("\\)", i + 2);
        if (end > i + 2) { push(mathNode(s.slice(i + 2, end).trim(), false, ctx)); i = end + 2; continue; }
      }
      buf += s[i + 1];
      i += 2;
      continue;
    }

    if (c === "`") {
      let n = 1;
      while (s[i + n] === "`") n++;
      const fence = "`".repeat(n);
      const key = `\`${n}`;
      const end = noClose.has(key) ? -1 : s.indexOf(fence, i + n);
      if (end < 0) noClose.set(key, true);
      if (end >= 0 && s[end + n] !== "`") {
        let code = s.slice(i + n, end);
        if (/^ .* $/.test(code) && code.trim()) code = code.slice(1, -1);
        push(el(ctx, "code", null, code));
        i = end + n;
        continue;
      }
      buf += fence;
      i += n;
      continue;
    }

    if (c === "<") {
      if ((m = /^<br\s*\/?>/i.exec(rest))) { push(el(ctx, "br")); i += m[0].length; continue; }
      if ((m = /^<((?:https?:\/\/|mailto:)[^\s<>]+)>/i.exec(rest))) {
        const href = safeHref(m[1]);
        if (href) { push(link(href, m[1], ctx)); i += m[0].length; continue; }
      }
      buf += c;
      i++;
      continue;
    }

    if ((c === "[" || c === "【") && (m = CITE.exec(rest)) && s[i + m[0].length] !== "(") {
      const ids = m[1].split(/[ \t]*[,，、;；][ \t]*/);
      const group = el(ctx, "span", { class: "md-cites" });
      for (const id of ids) {
        const custom = ctx.opts.onCitation ? ctx.opts.onCitation(id) : null;
        group.appendChild(custom && typeof custom === "object" && "nodeType" in custom
          ? custom
          : el(ctx, "span", { class: "md-cite", "data-cite": id, role: "doc-noteref" }, id));
      }
      push(group);
      i += m[0].length;
      continue;
    }

    if (c === "!" && s[i + 1] === "[" && (m = linkAt(s, i + 1, noClose))) {
      // an image is shown as a link to it, never loaded: no requests from model output
      const href = safeHref(m.url);
      const label = m.text || m.url;
      push(href ? link(href, label, ctx, "md-image-link") : ctx.doc.createTextNode(label));
      i = m.end;
      continue;
    }

    if (c === "[" && (m = linkAt(s, i, noClose))) {
      const href = safeHref(m.url);
      const inner = ctx.doc.createDocumentFragment();
      parseInline(m.text, inner, { ...ctx, inLink: true }, depth + 1);
      if (href && !ctx.inLink) push(link(href, inner, ctx));
      else push(inner);
      i = m.end;
      continue;
    }

    if ((c === "h" || c === "H") && !ctx.inLink && !isWordChar(s[i - 1]) && (m = URL_BARE.exec(rest))) {
      let url = m[1];
      const trail = TRAILING_PUNCT.exec(url);
      if (trail) {
        // keep a closing parenthesis that closes one inside the URL (wikipedia-style links)
        let cut = trail[0];
        if (cut.startsWith(")") && (url.match(/\(/g) || []).length > (url.slice(0, -cut.length).match(/\)/g) || []).length) cut = cut.slice(1);
        if (cut) url = url.slice(0, -cut.length);
      }
      const href = safeHref(url);
      if (href) { push(link(href, url, ctx)); i += url.length; continue; }
    }

    if (c === "$" && depth < 4 && (m = /^\$([^\s$](?:[^$\n]*[^\s$])?)\$(?![0-9A-Za-z])/.exec(rest)) && !/^\d+([.,]\d+)?$/.test(m[1])) {
      push(mathNode(m[1], false, ctx));
      i += m[0].length;
      continue;
    }

    if ((c === "*" || c === "_" || c === "~") && depth < 6) {
      const span = emphasisAt(s, i, noClose);
      if (span) {
        const node = el(ctx, span.tag);
        parseInline(span.inner, node, ctx, depth + 1);
        push(node);
        i = span.end;
        continue;
      }
    }

    buf += c;
    i++;
  }
  flushText();
}

const LINK_TEXT_MAX = 1000;
const CLOSER_TRIES = 64;

/** [text](url "title") starting at s[i] === "[" → {text, url, end} or null. Link text is bounded in length. */
function linkAt(s, i, noClose = new Map()) {
  if (noClose.has("]")) return null;
  let depth = 0;
  let j = i;
  const stop = Math.min(s.length, i + LINK_TEXT_MAX);
  for (; j < stop; j++) {
    if (s[j] === "\\") { j++; continue; }
    if (s[j] === "[") depth++;
    else if (s[j] === "]") { depth--; if (!depth) break; }
  }
  if (depth) {
    if (stop === s.length && s.indexOf("]", i) < 0) noClose.set("]", true);
    return null;
  }
  if (s[j + 1] !== "(") return null;
  let k = j + 2;
  let paren = 1;
  const stopUrl = Math.min(s.length, j + 2 + LINK_TEXT_MAX);
  for (; k < stopUrl; k++) {
    if (s[k] === "\\") { k++; continue; }
    if (s[k] === "(") paren++;
    else if (s[k] === ")") { paren--; if (!paren) break; }
  }
  if (paren) return null;
  const target = s.slice(j + 2, k).trim();
  const url = (/^<([^>]*)>/.exec(target)?.[1] ?? target.split(/\s+/)[0]) || "";
  return { text: s.slice(i + 1, j), url, end: k + 1 };
}

/** **strong** __strong__ *em* _em_ ~~del~~ at s[i]; intra-word underscores (snake_case) stay literal. */
function emphasisAt(s, i, noClose = new Map()) {
  const c = s[i];
  const double = s[i + 1] === c;
  if (c === "~" && !double) return null;
  const delim = double ? c + c : c;
  const after = s[i + delim.length];
  if (!after || /\s/.test(after) || after === c) return null;
  if (c === "_" && isWordChar(s[i - 1])) return null;
  let j = i + delim.length;
  if ((noClose.get(delim) ?? Infinity) <= j) return null;
  for (let tries = 0; j < s.length && tries < CLOSER_TRIES; tries++) {
    const k = s.indexOf(delim, j);
    if (k < 0) { noClose.set(delim, Math.min(noClose.get(delim) ?? Infinity, j)); return null; }
    const before = s[k - 1];
    const next = s[k + delim.length];
    const closes = !/\s/.test(before) && (delim.length === 2 || next !== c) && !(c === "_" && isWordChar(next));
    if (closes && k > i + delim.length) {
      return { tag: c === "~" ? "del" : double ? "strong" : "em", inner: s.slice(i + delim.length, k), end: k + delim.length };
    }
    j = k + 1;
  }
  return null;
}
