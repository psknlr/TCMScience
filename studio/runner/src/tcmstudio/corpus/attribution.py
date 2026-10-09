"""The attribution page and file of the published corpus (docs/V2.md §11.2).

``attribution_md(manifest)`` writes ``corpus/ATTRIBUTION.md`` and ``attribution_html(manifest)``
the standalone ``/attribution.html`` (linked from About and from every corpus envelope).
Both list every pack with its sources, licences and citations, the owner's decision for the
formula table word for word, and the sources this site never publishes, with why and where
users get them instead. Chinese first, then English. Generated from the manifest alone, so
the pages change exactly when the snapshot does.
"""

from __future__ import annotations

import html
from typing import Any, Mapping

from . import packs as P

__all__ = ["attribution_md", "attribution_html"]

_DECISION_KEYS = (("decided_by", "决定人", "Decided by"), ("date", "日期", "Date"),
                  ("basis", "依据", "Basis"), ("licence", "许可标识", "Licence id"),
                  ("commercial", "商业用途", "Commercial use"))
_SOURCE_COLS = (("name", "来源", "Source"), ("version", "版本", "Version"),
                ("licence", "许可", "Licence"), ("retrieved", "获取日期", "Retrieved"),
                ("citation", "引用", "Cite"))
_COUNT_LABELS = {
    "herbs": ("药材", "herbs"), "syndromes": ("证候", "syndromes"),
    "formulas": ("方剂（整理）", "curated formulas"), "safety": ("安全性记录", "safety records"),
    "passages": ("经典条文", "classical passages"), "names": ("名称", "names"),
    "rows": ("行", "rows"), "resolved_rows": ("组成全部可对应药材的行", "fully resolved rows"),
    "distinct_names": ("不同方名", "distinct names"), "components": ("组成药味", "components"),
    "compounds": ("化合物", "compounds"), "occurrences": ("来源记录", "occurrence records"),
    "herbs_with_compounds": ("有化合物记录的药材", "herbs with compounds"),
    "pathways": ("通路", "pathways"), "proteins": ("蛋白", "proteins"),
    "symbols": ("HGNC 符号", "HGNC symbols"),
}


def _counts(counts: Mapping[str, Any], lang: str) -> str:
    i = 0 if lang == "zh" else 1
    parts = [f"{label[i]} {counts[k]:,}" for k, label in _COUNT_LABELS.items()
             if isinstance(counts.get(k), int)]
    return ("，" if lang == "zh" else ", ").join(parts)


def _basis(meta: Mapping[str, Any], lang: str) -> str:
    pub = meta.get("publication") or {}
    if pub.get("basis") == "open-licence":
        return "开放许可" if lang == "zh" else "open licence"
    return "站点所有者决定" if lang == "zh" else "the site owner's decision"


# ----------------------------------------------------------------------------- Markdown

def attribution_md(manifest: Mapping[str, Any]) -> str:
    """``corpus/ATTRIBUTION.md``."""
    out = [f"# 语料来源与许可 · Corpus sources and licences", "",
           f"快照 Snapshot: `{manifest['snapshot_id']}` · 数据日期 Data date: {manifest['data_date']}",
           "", "本站发布的每个语料包、其来源、许可与引用如下；凡读取语料的工具结果都会注明所读的包与许可。",
           "Every pack this site publishes, with its sources, licences and citations; every tool "
           "result that reads the corpus names the packs it read and their licences.", ""]
    for name in P.PACK_ORDER:
        meta = (manifest.get("packs") or {}).get(name)
        if not meta:
            continue
        out += [f"## {meta['title']['zh']} · {meta['title']['en']}", "",
                f"- 许可 Licence: `{meta['licence']}` · 发布依据 Basis: {_basis(meta, 'zh')} · "
                f"{_basis(meta, 'en')}",
                f"- 规模 Size: {_counts(meta.get('counts') or {}, 'zh')} · "
                f"{_counts(meta.get('counts') or {}, 'en')}", "",
                meta["attribution"]["zh"], "", meta["attribution"]["en"], ""]
        pub = meta.get("publication") or {}
        if pub.get("basis") != "open-licence":
            out += ["**所有者决定（原文）Owner decision (verbatim):**", "", "```json",
                    _json(pub), "```", ""]
        out += ["| 来源 Source | 版本 Version | 许可 Licence | 获取 Retrieved | 引用 Cite | SHA-256 |",
                "|---|---|---|---|---|---|"]
        for s in meta.get("sources") or ():
            url = s.get("url") or ""
            label = f"[{_md(s.get('name', ''))}]({url})" if url else _md(s.get("name", ""))
            out.append(f"| {label} | {_md(s.get('version', ''))} | {_md(s.get('licence', ''))} | "
                       f"{_md(s.get('retrieved', ''))} | {_md(s.get('citation', ''))} | "
                       f"`{(s.get('sha256') or '')[:16]}` |")
        for s in meta.get("sources") or ():
            if s.get("changes"):
                out += ["", f"改动 Changes ({_md(s.get('name'))}): {_md(s['changes'])}"]
        out += ["", "局限 Limitations:", ""]
        for lim in meta.get("limitations") or ():
            out += [f"- {lim['zh']}", f"  {lim['en']}"]
        if meta.get("unresolved"):
            out += ["", "未能与药材表对应的临床知识包名称（按原名保留） Clinic-pack names that match no "
                        "materia entry (kept by name): "
                    + "、".join(u["name"] for u in meta["unresolved"])]
        out.append("")
    skipped = manifest.get("skipped") or {}
    if skipped:
        out += ["## 本快照未包含 · Not in this snapshot", ""]
        out += [f"- `{k}`: {_md(v)}" for k, v in skipped.items()]
        out.append("")
    out += ["## 不发布的来源 · Sources never published", "", P.NEVER_PUBLISHED_REASON["zh"], "",
            P.NEVER_PUBLISHED_REASON["en"], "", "| 来源 Source | 记录的许可 Licence recorded |",
            "|---|---|"]
    out += [f"| {_md(c['name'])} | {_md(c['recorded'])} |" for c in P.NEVER_PUBLISHED.values()]
    out.append("")
    return "\n".join(out)


def _md(value: Any) -> str:
    return str(value or "").replace("|", "\\|").replace("\n", " ")


def _json(value: Any) -> str:
    import json
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=False)


# --------------------------------------------------------------------------------- HTML

_CSS = """
:root{color-scheme:light;--bg:#fbfaf7;--surface:#fff;--sunken:#f3f1ec;--ink:#1a2230;--ink-2:#3d4757;
--ink-3:#596273;--rule:#e4e1da;--accent:#2d4a7a;--ochre-ink:#8a5822;--ochre-soft:#f8f0e4;
--vermilion:#b3402d;--vermilion-soft:#fbece8;--jade-ink:#25664f;--jade-soft:#e7f3ee;
--font-sans:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","Noto Sans SC",
"Noto Sans CJK SC","Source Han Sans SC","Microsoft YaHei",sans-serif;
--font-mono:ui-monospace,SFMono-Regular,Menlo,Consolas,"Liberation Mono",monospace}
@media (prefers-color-scheme:dark){:root{color-scheme:dark;--bg:#0f1318;--surface:#151a21;--sunken:#1a2029;
--ink:#e7ecf2;--ink-2:#c4ccd6;--ink-3:#98a3b3;--rule:#262e39;--accent:#8fb2ec;--ochre-ink:#e0ab6a;
--ochre-soft:#2b2114;--vermilion:#f08a76;--vermilion-soft:#2e1a16;--jade-ink:#79c9ad;--jade-soft:#132a23}}
*{box-sizing:border-box}html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.7 var(--font-sans)}
main{max-width:920px;margin:0 auto;padding:32px 16px 64px}
header p{color:var(--ink-2);margin:.25em 0}
h1{font-size:26px;font-weight:600;line-height:1.3;margin:0 0 8px}
h2{font-size:19px;font-weight:600;margin:20px 0 4px;line-height:1.35}
h2 small{display:block;font-size:14px;font-weight:500;color:var(--ink-3)}
h3{font-size:15px;font-weight:600;margin:18px 0 6px}
a{color:var(--accent)}code,.mono{font-family:var(--font-mono);font-size:12.5px}
.meta{color:var(--ink-3);font-size:13px}.en{color:var(--ink-2)}
section{background:var(--surface);border:1px solid var(--rule);border-radius:12px;
padding:4px 20px 18px;margin-top:20px}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin:10px 0}
.chip{border:1px solid var(--rule);border-radius:999px;padding:1px 10px;font-size:13px;
background:var(--sunken)}
.chip.owner{border-color:var(--ochre-ink);color:var(--ochre-ink);background:var(--ochre-soft)}
.chip.open{border-color:var(--jade-ink);color:var(--jade-ink);background:var(--jade-soft)}
.table{overflow-x:auto;margin:8px 0}
table{border-collapse:collapse;width:100%;font-size:13px;min-width:560px}
th,td{text-align:left;vertical-align:top;border-bottom:1px solid var(--rule);padding:6px 8px}
th{background:var(--sunken);font-weight:600}
.decision{border-left:3px solid var(--ochre-ink);background:var(--ochre-soft);padding:10px 14px;
border-radius:6px}
.decision dl{display:grid;grid-template-columns:max-content 1fr;gap:4px 14px;margin:6px 0 0}
.decision dt{color:var(--ink-3)}.decision dd{margin:0}
.deny{border-left:3px solid var(--vermilion);background:var(--vermilion-soft);padding:10px 14px;
border-radius:6px}
ul.lim{padding-left:20px;margin:6px 0}ul.lim li{margin:4px 0}
footer{margin-top:40px;color:var(--ink-3);font-size:13px}
"""


def _e(value: Any) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def _source_rows(sources: list[Mapping[str, Any]]) -> str:
    head = "".join(f"<th>{zh}<br><span class=en>{en}</span></th>" for _, zh, en in _SOURCE_COLS)
    rows = []
    for s in sources:
        cells = []
        for key, _, _ in _SOURCE_COLS:
            value = _e(s.get(key, ""))
            if key == "name" and s.get("url"):
                value = f'<a href="{_e(s["url"])}" rel="noopener">{value}</a>'
            cells.append(f"<td>{value}</td>")
        sha = s.get("sha256") or ""
        cells.append(f"<td class=mono title=\"{_e(sha)}\">{_e(sha[:16])}</td>")
        rows.append("<tr>" + "".join(cells) + "</tr>")
    return (f"<div class=table><table><thead><tr>{head}<th>SHA-256</th></tr></thead><tbody>"
            + "".join(rows) + "</tbody></table></div>")


def attribution_html(manifest: Mapping[str, Any]) -> str:
    """``/attribution.html``: standalone, no script, light and dark."""
    parts: list[str] = []
    for name in P.PACK_ORDER:
        meta = (manifest.get("packs") or {}).get(name)
        if not meta:
            continue
        pub = meta.get("publication") or {}
        owner = pub.get("basis") != "open-licence"
        chips = [f'<span class="chip {"owner" if owner else "open"}">{_e(_basis(meta, "zh"))} · '
                 f'{_e(_basis(meta, "en"))}</span>',
                 f"<span class=chip>许可 Licence: <code>{_e(meta['licence'])}</code></span>"]
        ceiling = meta.get("evidence_ceiling")
        if ceiling:
            chips.append(f"<span class=chip>证据上限 Evidence ceiling: <code>{_e(ceiling)}</code></span>")
        block = [f"<section id=\"{_e(name)}\"><h2>{_e(meta['title']['zh'])}"
                 f"<small lang=en>{_e(meta['title']['en'])}</small></h2>",
                 "<div class=chips>" + "".join(chips) + "</div>",
                 f"<p class=meta>{_e(_counts(meta.get('counts') or {}, 'zh'))}<br>"
                 f"<span lang=en>{_e(_counts(meta.get('counts') or {}, 'en'))}</span></p>",
                 f"<p>{_e(meta['attribution']['zh'])}</p>",
                 f"<p class=en lang=en>{_e(meta['attribution']['en'])}</p>"]
        if owner:
            rows = "".join(f"<dt>{zh} <span lang=en>{en}</span></dt><dd>{_e(pub.get(k, ''))}</dd>"
                           for k, zh, en in _DECISION_KEYS)
            block.append("<div class=decision><strong>所有者决定（原文）</strong> "
                         "<span lang=en class=en>Owner decision (verbatim)</span>"
                         f"<dl>{rows}</dl></div>")
        block.append("<h3>来源 <span lang=en class=en>Sources</span></h3>")
        block.append(_source_rows(list(meta.get("sources") or [])))
        for s in meta.get("sources") or ():
            if s.get("changes"):
                block.append(f"<p class=meta>改动 <span lang=en>Changes</span>（{_e(s.get('name'))}）："
                             f"<span lang=en>{_e(s['changes'])}</span></p>")
        note = meta.get("evidence_note") or {}
        if note:
            block.append(f"<h3>证据 <span lang=en class=en>Evidence</span></h3><p>{_e(note.get('zh'))}"
                         f"<br><span class=en lang=en>{_e(note.get('en'))}</span></p>")
        block.append("<h3>局限 <span lang=en class=en>Limitations</span></h3><ul class=lim>"
                     + "".join(f"<li>{_e(x['zh'])}<br><span class=en lang=en>{_e(x['en'])}</span></li>"
                               for x in meta.get("limitations") or ()) + "</ul>")
        if meta.get("unresolved"):
            block.append("<p class=meta>未能与药材表对应的临床知识包名称（按原名保留）"
                         "<span lang=en> · clinic-pack names that match no materia entry (kept by "
                         "name)</span>：" + _e("、".join(u["name"] for u in meta["unresolved"]))
                         + "</p>")
        block.append("</section>")
        parts.append("".join(block))
    skipped = manifest.get("skipped") or {}
    if skipped:
        parts.append("<section id=skipped><h2>本快照未包含<small lang=en>Not in this snapshot</small>"
                     "</h2><ul>" + "".join(f"<li><code>{_e(k)}</code>：{_e(v)}</li>"
                                           for k, v in skipped.items()) + "</ul></section>")
    deny_rows = "".join(f"<tr><td>{_e(c['name'])}</td><td>{_e(c['recorded'])}</td></tr>"
                        for c in P.NEVER_PUBLISHED.values())
    parts.append("<section id=never><h2>不发布的来源<small lang=en>Sources never published</small></h2>"
                 f"<div class=deny><p>{_e(P.NEVER_PUBLISHED_REASON['zh'])}</p>"
                 f"<p class=en lang=en>{_e(P.NEVER_PUBLISHED_REASON['en'])}</p></div>"
                 "<div class=table><table><thead><tr><th>来源<br><span class=en>Source</span></th>"
                 "<th>记录的许可<br><span class=en>Licence recorded</span></th></tr></thead><tbody>"
                 + deny_rows + "</tbody></table></div></section>")
    t = manifest.get("totals") or {}
    return (
        "<!doctype html>\n<html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        "<meta name=\"color-scheme\" content=\"light dark\">"
        "<meta name=\"theme-color\" content=\"#fbfaf7\" media=\"(prefers-color-scheme: light)\">"
        "<meta name=\"theme-color\" content=\"#0f1318\" media=\"(prefers-color-scheme: dark)\">"
        "<title>语料来源与许可 · TCMScience Studio</title>"
        f"<style>{_CSS}</style></head><body><main>"
        "<header><h1>语料来源与许可</h1><p lang=en>Corpus sources and licences</p>"
        f"<p class=meta>快照 <code>{_e(manifest.get('snapshot_id'))}</code> · 数据日期 "
        f"{_e(manifest.get('data_date'))} · {t.get('objects', 0):,} 个对象 objects · "
        f"{t.get('bytes', 0):,} 字节 bytes</p>"
        "<p>TCMScience Studio 在本站发布下列语料包。凡读取语料的工具结果都会注明所读的包、许可与局限；"
        "表格记录与经典条文是两类不同的东西，引用时请分开。</p>"
        "<p class=en lang=en>TCMScience Studio publishes the packs below. Every tool result that "
        "reads the corpus names the packs it read, their licences and limits; a table row and a "
        "classical passage are different things, cite them as such.</p></header>"
        + "".join(parts)
        + "<footer><p><a href=\"/\">返回 TCMScience Studio</a> · <a href=\"corpus/ATTRIBUTION.md\">"
          "ATTRIBUTION.md</a> · <a href=\"corpus/latest.json\">latest.json</a></p></footer>"
          "</main></body></html>\n")
