// File previews for the inspector's Files tab (DESIGN §3.5): JSON as a collapsible tree, text, CSV/TSV as a table
// (first rows), images, and HTML reports from the runner in a sandboxed iframe (scripts never run). Bytes are read
// from this browser (IndexedDB) or fetched from the local runner; nothing goes to the server.

import { t } from "../core/i18n.js";
import { fill, formatBytes, h } from "./dom.js";
import { icon } from "./icons.js";
import { notice, spinner } from "./primitives.js";

const MAX_TEXT = 512 * 1024;
const MAX_ROWS = 200;

/** source: {blob} | {url (runner file, with token)} | {json: value}; meta: {name, media_type}. */
export function filePreview(source, meta) {
  const box = h("div.preview", h("p.preview__loading.muted", spinner({ size: 12 }), " ", t("ui.files.loading")));
  load(source, meta).then((node) => fill(box, node)).catch((err) => fill(box, notice({ tone: "warn", body: t("ui.files.preview_failed", { message: err?.message || String(err) }) })));
  return box;
}

async function load(source, meta) {
  const name = meta?.name || "";
  const type = (meta?.media_type || meta?.type || "").toLowerCase();
  if (source.json !== undefined) return jsonTree(source.json);
  let blob = source.blob;
  if (!blob && source.url) {
    const r = await fetch(source.url);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    blob = await r.blob();
  }
  if (!blob) throw new Error(t("ui.files.no_bytes"));
  const kind = kindOf(type || blob.type, name);
  if (kind === "image") {
    const url = URL.createObjectURL(blob);
    return h("div.preview__image", h("img", { src: url, alt: name, onLoad: () => setTimeout(() => URL.revokeObjectURL(url), 60000) }));
  }
  if (kind === "html") {
    // a report from the runner: shown inert (sandbox without scripts, a blob: URL so COEP allows it)
    const html = await blob.text();
    const url = URL.createObjectURL(new Blob([html], { type: "text/html" }));
    return h("div.preview__html",
      h("p.preview__note", icon("shield", { size: 12 }), t("ui.files.sandboxed")),
      h("iframe", { src: url, sandbox: "", title: name, referrerpolicy: "no-referrer", loading: "lazy" }));
  }
  if (kind === "pdf") return notice({ tone: "info", body: t("ui.files.pdf") });
  if (blob.size > MAX_TEXT && kind !== "json") {
    const head = await blob.slice(0, MAX_TEXT).text();
    return h("div", h("p.preview__note", t("ui.files.truncated", { size: formatBytes(blob.size) })), textView(head, kind));
  }
  const text = await blob.text();
  if (kind === "json") {
    try { return jsonTree(JSON.parse(text)); } catch { return textView(text, "text"); }
  }
  return textView(text, kind);
}

function kindOf(type, name) {
  if (/^image\/(png|jpe?g|gif|webp|svg\+xml|avif)/.test(type) || /\.(png|jpe?g|gif|webp|svg|avif)$/i.test(name)) return "image";
  if (/html/.test(type) || /\.html?$/i.test(name)) return "html";
  if (/json/.test(type) || /\.jsonl?$/i.test(name)) return "json";
  if (/csv/.test(type) || /\.csv$/i.test(name)) return "csv";
  if (/tab-separated|tsv/.test(type) || /\.tsv$/i.test(name)) return "tsv";
  if (/pdf/.test(type) || /\.pdf$/i.test(name)) return "pdf";
  return "text";
}

function textView(text, kind) {
  if (kind === "csv" || kind === "tsv") return tableView(text, kind === "tsv" ? "\t" : ",");
  return h("pre.code-block.code-block--wrap.preview__text", text);
}

/** CSV/TSV → a table of the first rows (quoted fields honoured). */
export function parseDelimited(text, sep = ",") {
  const rows = [];
  let row = [];
  let field = "";
  let quoted = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (quoted) {
      if (c === '"' && text[i + 1] === '"') { field += '"'; i++; }
      else if (c === '"') quoted = false;
      else field += c;
    } else if (c === '"' && field === "") quoted = true;
    else if (c === sep) { row.push(field); field = ""; }
    else if (c === "\n" || c === "\r") {
      if (c === "\r" && text[i + 1] === "\n") i++;
      row.push(field); field = "";
      rows.push(row); row = [];
      if (rows.length > MAX_ROWS) break;
    } else field += c;
  }
  if (field || row.length) { row.push(field); rows.push(row); }
  return rows.filter((r) => r.length > 1 || r[0] !== "");
}

function tableView(text, sep) {
  const rows = parseDelimited(text, sep);
  if (!rows.length) return h("p.muted", t("ui.files.empty"));
  const [head, ...body] = rows;
  const total = text.split(/\r?\n/).filter(Boolean).length - 1;
  return h("div",
    h("div.tablewrap.preview__table", { tabindex: "0", role: "region", "aria-label": t("ui.files.table") },
      h("table", h("thead", h("tr", head.map((c) => h("th", { scope: "col" }, c)))),
        h("tbody", body.slice(0, MAX_ROWS).map((r) => h("tr", r.map((c) => h("td", { class: /^-?\d+(\.\d+)?(e-?\d+)?$/i.test(c) ? "num" : null }, c))))))),
    h("p.preview__note", t("ui.files.rows", { shown: Math.min(body.length, MAX_ROWS), total: Math.max(total, body.length) })));
}

/** A collapsible JSON tree (objects and arrays fold; the first two levels open). */
export function jsonTree(value, depth = 0) {
  if (value === null || typeof value !== "object") return scalar(value);
  const entries = Array.isArray(value) ? value.map((v, i) => [i, v]) : Object.entries(value);
  const open = depth < 2 && entries.length <= 60;
  const summary = Array.isArray(value) ? `[${entries.length}]` : `{${entries.length}}`;
  const details = h("details.json", { open: open || null },
    h("summary.json__sum", h("span.json__brace", summary)),
    h("ul.json__list", { role: "list" }, entries.slice(0, 500).map(([k, v]) => h("li.json__item",
      h("span.json__key", Array.isArray(value) ? `${k}` : `${k}`), h("span.json__colon", ": "),
      v !== null && typeof v === "object" ? jsonTree(v, depth + 1) : scalar(v))),
    entries.length > 500 ? h("li.muted", t("ui.files.more_items", { n: entries.length - 500 })) : null));
  return depth === 0 ? h("div.json-root", details) : details;
}

function scalar(v) {
  const type = v === null ? "null" : typeof v;
  const text = type === "string" ? JSON.stringify(v) : String(v);
  return h("span", { class: ["json__val", `json__val--${type}`] }, text.length > 400 ? `${text.slice(0, 400)}…` : text);
}
