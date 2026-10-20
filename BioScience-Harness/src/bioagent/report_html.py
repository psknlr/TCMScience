"""A Markdown report as one self-contained HTML page: the renderer the reports share.

The pipelines (RNA-seq, single-cell, docking, structure prediction, ADMET), the clinic's
session record and the network-pharmacology exports write ``report.html`` with
:func:`render`. It reads the Markdown those reports are written in and nothing more:
headings (``#``, ``##``), lists, tables, fenced code, figures named by ``![name]`` and given
as SVG markup, and inline code and bold. Text from the Markdown is HTML-escaped; a figure is
inserted as the markup handed in.

It needs only the standard library. It used to live in :mod:`bioagent.omics.rnaseq`, which
imports numpy and scipy, so a report from a run that uses neither (the research job's, in
the browser's Python among others) loaded both for nothing. ``rnaseq._html`` and
``rnaseq._inline`` are still these two functions under their old names.
"""

from __future__ import annotations

import html
from typing import Mapping

__all__ = ["inline", "render"]


def render(md: str, plots: Mapping[str, str]) -> str:
    """The Markdown report as one HTML page, figures inline."""
    body: list[str] = []
    table: list[str] = []

    def flush() -> None:
        if not table:
            return
        rows = [r for r in table if not set(r.replace("|", "").strip()) <= {"-"}]
        body.append("<table>")
        for k, r in enumerate(rows):
            cells = [c.strip() for c in r.strip().strip("|").split("|")]
            tag = "th" if k == 0 else "td"
            body.append("<tr>" + "".join(f"<{tag}>{inline(c)}</{tag}>" for c in cells)
                        + "</tr>")
        body.append("</table>")
        table.clear()

    in_list = False
    in_code = False
    for line in md.splitlines():
        if line.startswith("```"):
            flush()
            body.append("</pre>" if in_code else "<pre>")
            in_code = not in_code
            continue
        if in_code:
            body.append(html.escape(line))
            continue
        if line.startswith("|"):
            table.append(line)
            continue
        flush()
        if line.startswith("- "):
            if not in_list:
                body.append("<ul>")
                in_list = True
            body.append(f"<li>{inline(line[2:])}</li>")
            continue
        if in_list:
            body.append("</ul>")
            in_list = False
        if line.startswith("# "):
            body.append(f"<h1>{inline(line[2:])}</h1>")
        elif line.startswith("## "):
            body.append(f"<h2>{inline(line[3:])}</h2>")
        elif line.startswith("!["):
            name = line[2:line.index("]")]
            body.append(f'<figure>{plots.get(name, "")}</figure>')
        elif line.strip():
            body.append(f"<p>{inline(line)}</p>")
    flush()
    if in_list:
        body.append("</ul>")
    if in_code:
        body.append("</pre>")
    style = ("body{font-family:system-ui,-apple-system,Segoe UI,sans-serif;max-width:980px;"
             "margin:2em auto;padding:0 16px;color:#222;background:#fff}"
             "table{border-collapse:collapse;margin:1em 0;font-size:13px}"
             "td,th{border:1px solid #ccc;padding:4px 8px;text-align:left}"
             "th{background:#f3f3f3}figure{margin:1em 0}svg{max-width:100%;height:auto}"
             "code{background:#f3f3f3;padding:0 3px}"
             "pre{background:#f6f6f6;padding:8px;overflow-x:auto;font-size:12px}")
    title = md.splitlines()[0].lstrip("# ")
    return ("<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
            f"<title>{html.escape(title)}</title><style>{style}</style></head><body>\n"
            + "\n".join(body) + "\n</body></html>\n")


def inline(text: str) -> str:
    out = html.escape(text)
    parts = out.split("`")
    out = "".join(f"<code>{p}</code>" if i % 2 else p for i, p in enumerate(parts))
    parts = out.split("**")
    return "".join(f"<strong>{p}</strong>" if i % 2 else p for i, p in enumerate(parts))
