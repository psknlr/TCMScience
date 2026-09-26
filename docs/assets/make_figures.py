#!/usr/bin/env python3
"""Draw the README and project-page figures from the code they describe.

    python docs/assets/make_figures.py

* ``overview.svg``  — the three planes and the boundary between them;
* ``licensing.svg`` — which study designs license which claim kinds, read from
  PSH's own ``psh.workflow.compiler._SUPPORTS``: the figure cannot disagree with
  the kernel, because it is drawn from the table the kernel enforces.

Both are plain SVG with their colours in CSS custom properties and a
``prefers-color-scheme: dark`` block, so one file reads well on GitHub's light
and dark themes and on the project page. System fonts only: an SVG shown
through ``<img>`` cannot load web fonts.
"""

from __future__ import annotations

import sys
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "PSH-Harness" / "src"))

from psh.workflow.compiler import _SUPPORTS  # noqa: E402
from psh.workflow.ir import EVIDENCE_DESIGNS, PREDICTIVE_DESIGNS, ClaimType  # noqa: E402

OUT = Path(__file__).resolve().parent
FONT = ("-apple-system, BlinkMacSystemFont, 'Segoe UI', 'Helvetica Neue', Arial, "
        "'PingFang SC', 'Hiragino Sans GB', 'Noto Sans CJK SC', 'Microsoft YaHei', sans-serif")
MONO = "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"

STYLE = f"""
<style>
  svg {{ --ink:#17202c; --muted:#5a6573; --rule:#d5dbe3; --paper:#ffffff;
         --k:#2d4a7a; --k-bg:#eaf0f8; --g:#9a6428; --g-bg:#f8f0e4;
         --c:#2c7560; --c-bg:#e7f3ee; --x:#b3402d; --x-bg:#fbe9e5; --dot:#2d4a7a; }}
  @media (prefers-color-scheme: dark) {{
    svg {{ --ink:#e6edf3; --muted:#9aa6b4; --rule:#34404e; --paper:#0d1117;
           --k:#8fb2ec; --k-bg:#16233a; --g:#e0ab6a; --g-bg:#2b2114;
           --c:#79c9ad; --c-bg:#132a23; --x:#f08a76; --x-bg:#35191385; --dot:#8fb2ec; }}
  }}
  text {{ font-family:{FONT}; fill:var(--ink); }}
  .mono {{ font-family:{MONO}; }}
  .muted {{ fill:var(--muted); }}
  .rule {{ stroke:var(--rule); }}
</style>"""


def svg(w: int, h: int, body: str, title: str, desc: str) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" '
            f'height="{h}" role="img" aria-labelledby="t d">\n<title id="t">{escape(title)}</title>\n'
            f'<desc id="d">{escape(desc)}</desc>{STYLE}\n{body}\n</svg>\n')


def box(x, y, w, h, tone, title, sub="", *, r=8, size=17):
    lines = [f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}" '
             f'style="fill:var(--{tone}-bg);stroke:var(--{tone});stroke-width:1.2"/>']
    ty = y + h / 2 - (9 if sub else -6)
    lines.append(f'<text x="{x + w / 2}" y="{ty}" text-anchor="middle" font-size="{size}" '
                 f'font-weight="600">{escape(title)}</text>')
    if sub:
        lines.append(f'<text x="{x + w / 2}" y="{ty + 23}" text-anchor="middle" font-size="14" '
                     f'class="muted">{escape(sub)}</text>')
    return "\n".join(lines)


def band(x, y, w, h, tone, label, note):
    return (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="12" '
            f'style="fill:none;stroke:var(--{tone});stroke-width:1.4;stroke-dasharray:{"0" if tone == "k" else "0"}"/>'
            f'<text x="{x + 18}" y="{y + 26}" font-size="14.5" font-weight="700" '
            f'style="fill:var(--{tone});letter-spacing:.06em">{escape(label.upper())}</text>'
            f'<text x="{x + w - 18}" y="{y + 26}" font-size="14" text-anchor="end" class="muted">'
            f'{escape(note)}</text>')


def arrow(x1, y1, x2, y2, tone="ink", dashed=False, width=1.6):
    dash = ';stroke-dasharray:5 4' if dashed else ''
    color = 'var(--ink)' if tone == 'ink' else f'var(--{tone})'
    return (f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" '
            f'style="stroke:{color};stroke-width:{width}{dash}" marker-end="url(#a-{tone})"/>')


def markers(*tones):
    out = ['<defs>']
    for t in tones:
        color = 'var(--ink)' if t == 'ink' else f'var(--{t})'
        out.append(f'<marker id="a-{t}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" '
                   f'markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" '
                   f'style="fill:{color}"/></marker>')
    out.append('</defs>')
    return "".join(out)


# ------------------------------------------------------------------ overview
def overview() -> str:
    W, H = 1200, 560
    X0, X1 = 150, 1030                     # the three bands share these edges
    b = [markers("ink", "g", "k", "c")]

    def row(n, gap=18):
        w = (X1 - X0 - 36 - gap * (n - 1)) / n
        return [(X0 + 18 + i * (w + gap), w) for i in range(n)]

    # governance
    b.append(band(X0, 16, X1 - X0, 124, "g", "Governance layer", "constrains the kernel · never executes"))
    gov = [("Skill manifests", "skill.yaml → program"),
           ("Data contracts", "evidence · claim · artifact"),
           ("Registry + lockfile", "human promotion only"),
           ("Benchmarks", "6 tracks · 8 dimensions")]
    for (x, w), (t, s) in zip(row(4), gov):
        b.append(box(x, 50, w, 74, "g", t, s))
        b.append(arrow(x + w / 2, 142, x + w / 2, 196, "g", dashed=True, width=1.3))
    # kernel
    b.append(band(X0, 198, X1 - X0, 140, "k", "Trusted kernel · PSH", "the model cannot rewrite these rules"))
    ker = [("Ingress", "labels at entry"), ("Authority", "policy lattice"),
           ("Compiled plan", "typed · bounded"), ("Gateway", "models · tools"),
           ("Release gate", "quarantine · verify")]
    cells = row(5, gap=22)
    for i, ((x, w), (t, s)) in enumerate(zip(cells, ker)):
        b.append(box(x, 238, w, 76, "k", t, s))
        if i:
            px, pw = cells[i - 1]
            b.append(arrow(px + pw + 2, 276, x - 2, 276, "k", width=2))
    # question in, audit out
    b.append('<rect x="14" y="242" width="118" height="68" rx="34" style="fill:var(--k);stroke:none"/>'
             '<text x="73" y="272" text-anchor="middle" font-size="15.5" font-weight="700" '
             'style="fill:var(--paper)">Research</text><text x="73" y="292" text-anchor="middle" '
             'font-size="15.5" font-weight="700" style="fill:var(--paper)">question</text>')
    b.append(arrow(128, 276, cells[0][0] - 2, 276, "k", width=2.2))
    ax = X1 + 24
    b.append(f'<rect x="{ax}" y="240" width="{W - ax - 14}" height="72" rx="8" '
             f'style="fill:var(--paper);stroke:var(--ink);stroke-width:1.2"/>'
             f'<text x="{(ax + W - 14) / 2}" y="272" text-anchor="middle" font-size="16" '
             f'font-weight="600">Audit</text><text x="{(ax + W - 14) / 2}" y="293" '
             f'text-anchor="middle" font-size="13.5" class="muted">hash-chained</text>')
    lx, lw = cells[-1]
    b.append(arrow(lx + lw + 2, 276, ax - 2, 276, "ink", width=2))
    # capability plane, called through the gateway
    b.append(band(X0, 386, X1 - X0, 124, "c", "Capability plane · BioScience-Harness", "called, never trusted"))
    cap = [("58 public sources", "153 typed operations"), ("147 native tools", "12 domains"),
           ("bioagent.tcm", "herbs · formulas · classics"), ("Source snapshots", "content-hashed · ledger")]
    for (x, w), (t, s) in zip(row(4), cap):
        b.append(box(x, 420, w, 74, "c", t, s))
    gx, gw = cells[3]
    b.append(arrow(gx + gw / 2, 316, gx + gw / 2, 384, "c", width=2))
    b.append(f'<text x="{gx + gw / 2 + 8}" y="358" font-size="14" class="muted">calls</text>')
    b.append(f'<text x="{X0}" y="542" font-size="14" class="muted">Solid arrows: execution. '
             'Dashed: where governance constrains the kernel. The capability plane is reached '
             'only through the gateway.</text>')
    return svg(W, H, "\n".join(b), "TCMScience architecture",
               "A research question enters the trusted kernel, which labels data, checks authority, "
               "runs a compiled plan through a gateway into the capability plane, and releases "
               "results only through a verification gate into a hash-chained audit. The governance "
               "layer constrains the kernel from above.")


# ------------------------------------------------------------------ licensing
CLAIMS = [(ClaimType.CLASSICAL, "attribution", "记载"),
          (ClaimType.TRADITIONAL, "traditional|use", "传统应用"),
          (ClaimType.MECHANISM_HYPOTHESIS, "mechanism|hypothesis", "机制假说"),
          (ClaimType.MECHANISTIC, "mechanism", "机制"),
          (ClaimType.ASSOCIATION, "association", "关联"),
          (ClaimType.CLINICAL, "efficacy", "疗效"),
          (ClaimType.SAFETY, "safety|signal", "安全信号")]
GROUPS = [("Tradition", ["classical_text", "expert_consensus"]),
          ("Bench", ["in_vitro", "animal"]),
          ("Clinical", ["case_report", "observational", "randomized_trial"]),
          ("Computation", ["predictive"])]


def licensing() -> str:
    predictive = sorted(PREDICTIVE_DESIGNS)
    # every predictive design must license exactly the same kinds, or one row is a lie
    rows = {tuple(sorted(c for c, _, _ in CLAIMS if d in _SUPPORTS[c])) for d in predictive}
    assert len(rows) == 1, f"predictive designs differ in what they license: {rows}"
    for _, designs in GROUPS[:-1]:
        for d in designs:
            assert d in EVIDENCE_DESIGNS, d

    def licensed(design: str, claim) -> bool:
        return (predictive[0] if design == "predictive" else design) in _SUPPORTS[claim]

    left, top, cw, rh = 300, 134, 116, 42
    W = left + cw * len(CLAIMS) + 30
    nrows = sum(len(d) for _, d in GROUPS)
    H = top + rh * nrows + 22 * len(GROUPS) + 86
    b = []
    for j, (_, en, zh) in enumerate(CLAIMS):
        cx = left + cw * j + cw / 2
        hi = j == 2
        if hi:
            b.append(f'<rect x="{left + cw * j + 4}" y="28" width="{cw - 8}" '
                     f'height="{H - 28 - 70}" rx="10" style="fill:var(--x-bg);stroke:none"/>')
        accent = 'style="fill:var(--x)"' if hi else ""
        for k, part in enumerate(en.split("|")):
            b.append(f'<text x="{cx}" y="{50 + 19 * k}" text-anchor="middle" font-size="15.5" '
                     f'font-weight="600" {accent}>{escape(part)}</text>')
        b.append(f'<text x="{cx}" y="98" text-anchor="middle" font-size="14" class="muted">{zh}</text>')
    b.append(f'<line x1="20" y1="{top - 18}" x2="{W - 20}" y2="{top - 18}" class="rule" '
             f'style="stroke-width:1"/>')
    y = top
    for gname, designs in GROUPS:
        b.append(f'<text x="24" y="{y + 4}" font-size="12.5" font-weight="700" class="muted" '
                 f'style="letter-spacing:.08em">{gname.upper()}</text>')
        y += 22
        for d in designs:
            comp = d == "predictive"
            label = "predictive designs (6)" if comp else d
            accent = 'style="fill:var(--x)"' if comp else ""
            b.append(f'<text x="36" y="{y + 5}" font-size="15.5" class="mono" '
                     f'{accent}>{escape(label)}</text>')
            if comp:
                for k in range(0, len(predictive), 3):
                    b.append(f'<text x="36" y="{y + 24 + 16 * (k // 3)}" font-size="12.5" '
                             f'class="muted mono">{escape(", ".join(predictive[k:k + 3]))}</text>')
            for j, (claim, _, _) in enumerate(CLAIMS):
                cx = left + cw * j + cw / 2
                if licensed(d, claim):
                    tone = "x" if comp else "dot"
                    b.append(f'<circle cx="{cx}" cy="{y}" r="9" style="fill:var(--{tone})"/>')
                else:
                    b.append(f'<circle cx="{cx}" cy="{y}" r="3" style="fill:var(--rule)"/>')
            y += rh if not comp else rh + 34
        y += 0
    b.append(f'<text x="24" y="{H - 40}" font-size="14" class="muted">'
             f'Drawn from psh.workflow.compiler._SUPPORTS. A computational prediction licenses a '
             f'mechanism hypothesis and nothing stronger;</text>')
    b.append(f'<text x="24" y="{H - 18}" font-size="14" class="muted">'
             f'a claim that asks for more is refused at compile time (EVIDENCE103) and again at '
             f'release (CLM005).</text>')
    return svg(W, H, "\n".join(b), "Which evidence licenses which claim",
               "A matrix of study designs against claim kinds. Classical text licenses attribution "
               "and traditional use; bench studies license mechanism hypothesis and mechanism; "
               "clinical designs license association, efficacy and safety as marked; the six "
               "predictive designs license only a mechanism hypothesis.")


if __name__ == "__main__":
    for name, draw in (("overview.svg", overview), ("licensing.svg", licensing)):
        (OUT / name).write_text(draw(), encoding="utf-8")
        print(OUT / name)
