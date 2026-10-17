#!/usr/bin/env python3
"""Draw the README and project-page figures from the code they describe.

    python docs/assets/make_figures.py

* ``overview.svg``  — Fig. 1 of the README: the three planes and the boundary
  between them, drawn as a figure for an IEEE Transactions paper. Its counts
  (connectors, operations, native tools, domains, ablation mutants and error
  classes) are read from BioScience-Harness, so they cannot go stale either;
* ``licensing.svg`` — which study designs license which claim kinds, read from
  PSH's own ``psh.workflow.compiler._SUPPORTS``: the figure cannot disagree with
  the kernel, because it is drawn from the table the kernel enforces.

``licensing.svg`` keeps its colours in CSS custom properties and a
``prefers-color-scheme: dark`` block, so it reads well on GitHub's light and
dark themes and on the project page. ``overview.svg`` is a print figure: black
on its own white paper, grey fills and line styles instead of colour, serif
type, so it survives a greyscale printer and either theme. System fonts only:
an SVG shown through ``<img>`` cannot load web fonts.
"""

from __future__ import annotations

import sys
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "PSH-Harness" / "src"))
sys.path.insert(0, str(ROOT / "BioScience-Harness" / "src"))

from bioagent.benchmarks.ablation import base_cases, mutants  # noqa: E402
from bioagent.providers.public_apis import SOURCES  # noqa: E402
from bioagent.tools import TOOLS  # noqa: E402
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


def svg(w: int, h: int, body: str, title: str, desc: str, style: str = STYLE) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" '
            f'height="{h}" role="img" aria-labelledby="t d">\n<title id="t">{escape(title)}</title>\n'
            f'<desc id="d">{escape(desc)}</desc>{style}\n{body}\n</svg>\n')


# ------------------------------------------------------------------ overview
# Fig. 1 follows the house style of an IEEE Transactions figure: black line art on white,
# grey fills and line styles where the other figures use colour, Times-metric type, square
# blocks, panels tagged (a)-(c) for the caption to cite, and a legend for the line styles.
SERIF = ("'Times New Roman', Times, 'Nimbus Roman', 'Nimbus Roman No9 L', 'TeX Gyre Termes', "
         "'Liberation Serif', serif")
COURIER = "'Courier New', Courier, 'Nimbus Mono PS', 'Liberation Mono', monospace"
IEEE_STYLE = f"""
<style>
  text {{ font-family:{SERIF}; fill:#000; }}
  .it {{ font-style:italic; }}
  .mono {{ font-family:{COURIER}; font-style:normal; font-size:.92em; }}
</style>"""
GREY_GOV, GREY_KERNEL = "#e3e3e3", "#f3f3f3"     # 11 % and 5 % grey; the capability plane is white


def counts() -> dict[str, int]:
    """What Fig. 1 says about BioScience-Harness, read from the code it describes."""
    made = mutants(base_cases())
    return {"connectors": len(SOURCES), "operations": sum(len(s.operations) for s in SOURCES),
            "tools": len(TOOLS), "domains": len({t.domain for t in TOOLS}),
            "mutants": len(made), "classes": len({m.error_class for m in made})}


def _set(text: str, code: str = "") -> str:
    """Escape ``text`` and set ``code``, if it occurs, in Courier."""
    out = escape(text)
    return out.replace(code, f'<tspan class="mono">{code}</tspan>') if code else out


def line(x1, y1, x2, y2, *, dashed=False, width=1.4):
    dash = ' stroke-dasharray="6 4"' if dashed else ''
    return (f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="#000" '
            f'stroke-width="{width}"{dash} marker-end="url(#head)"/>')


def block(x, y, w, h, fill, title, sub, *, code=""):
    """A square block: its name, and under it in italics what it does."""
    cx, cy = x + w / 2, y + h / 2
    return (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="{fill}" stroke="#000" '
            f'stroke-width="1.2"/>\n'
            f'<text x="{cx}" y="{cy - 5}" text-anchor="middle" font-size="19">{_set(title, code)}</text>\n'
            f'<text x="{cx}" y="{cy + 17}" text-anchor="middle" font-size="16" '
            f'class="it">{_set(sub, code)}</text>')


def panel(x, y, w, h, tag, label, note, *, fill="none", width=1.2, dashed=False):
    """One plane: a frame tagged (a), (b) or (c), its name, and what it may do."""
    dash = ' stroke-dasharray="8 5"' if dashed else ''
    return (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="{fill}" stroke="#000" '
            f'stroke-width="{width}"{dash}/>\n'
            f'<text x="{x + 14}" y="{y + 26}" font-size="18" font-weight="700">'
            f'({tag}) {escape(label)}</text>\n'
            f'<text x="{x + w - 14}" y="{y + 26}" font-size="16.5" text-anchor="end" '
            f'class="it">{escape(note)}</text>')


def cylinder(x, y, w, h, title, sub, *, ry=9):
    """A store, drawn the way block diagrams draw one."""
    rx, cx, cy = w / 2, x + w / 2, (y + 2 * ry + y + h) / 2
    return (f'<path d="M{x},{y + ry} V{y + h - ry} A{rx},{ry} 0 0 0 {x + w},{y + h - ry} '
            f'V{y + ry} Z" fill="#fff" stroke="#000" stroke-width="1.2"/>\n'
            f'<ellipse cx="{cx}" cy="{y + ry}" rx="{rx}" ry="{ry}" fill="#fff" stroke="#000" '
            f'stroke-width="1.2"/>\n'
            f'<text x="{cx}" y="{cy - 5}" text-anchor="middle" font-size="19">{escape(title)}</text>\n'
            f'<text x="{cx}" y="{cy + 17}" text-anchor="middle" font-size="16" '
            f'class="it">{escape(sub)}</text>')


def legend(x, y, w, h=34):
    cy = y + h / 2
    b = [f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="#fff" stroke="#000" stroke-width="0.8"/>']
    keys = [("Execution", lambda kx: line(kx, cy, kx + 40, cy)),
            ("Governance constraint", lambda kx: line(kx, cy, kx + 40, cy, dashed=True, width=1.2)),
            ("Trust boundary", lambda kx: f'<rect x="{kx}" y="{cy - 8}" width="40" height="16" '
                                          f'fill="{GREY_KERNEL}" stroke="#000" stroke-width="2.4"/>'),
            ("Untrusted plane", lambda kx: f'<rect x="{kx}" y="{cy - 8}" width="40" height="16" '
                                           f'fill="none" stroke="#000" stroke-width="1.2" '
                                           f'stroke-dasharray="8 5"/>')]
    # a key is a 40-unit sample, a 12-unit gap and its label (Times: about 0.44 em a
    # character); the gaps between keys and at the ends are equal
    widths = [52 + 0.44 * 16 * len(label) for label, _ in keys]
    gap, kx = (w - sum(widths)) / (len(keys) + 1), x
    for (label, key), kw in zip(keys, widths):
        kx += gap
        b.append(key(kx))
        b.append(f'<text x="{kx + 52}" y="{cy + 5.5}" font-size="16">{escape(label)}</text>')
        kx += kw
    return "\n".join(b)


def overview() -> str:
    n = counts()
    W, H = 1200, 580
    X0, X1 = 156, 1036                     # the three panels share these edges

    def row(k, gap=18):
        w = (X1 - X0 - 36 - gap * (k - 1)) / k
        return [(X0 + 18 + i * (w + gap), w) for i in range(k)]

    b = [('<defs><marker id="head" viewBox="0 0 10 8" refX="8" refY="4" markerWidth="11" '
          'markerHeight="8.8" markerUnits="userSpaceOnUse" orient="auto">'
          '<path d="M0,0 L10,4 L0,8 z" fill="#000"/></marker></defs>'),
         f'<rect width="{W}" height="{H}" fill="#fff"/>']
    # (a) governance
    b.append(panel(X0, 14, X1 - X0, 124, "a", "Governance layer",
                   "Constrains the kernel; never executes", fill="#fff"))
    gov = [("Skill manifests", "skill.yaml → program", "skill.yaml"),
           ("Data contracts", "evidence, claim, artifact", ""),
           ("Registry + lockfile", "human promotion only", ""),
           ("Governance regression", f"{n['mutants']} mutants, {n['classes']} classes", "")]
    for (x, w), (t, s, code) in zip(row(4), gov):
        b.append(block(x, 52, w, 70, GREY_GOV, t, s, code=code))
        b.append(line(x + w / 2, 140, x + w / 2, 194, dashed=True, width=1.2))
    # (b) kernel
    b.append(panel(X0, 196, X1 - X0, 142, "b", "Trusted kernel (PSH)",
                   "The model cannot rewrite these rules", fill=GREY_KERNEL, width=2.4))
    ker = [("Ingress", "labels at entry"), ("Authority", "policy lattice"),
           ("Compiled plan", "typed, bounded"), ("Gateway", "models, tools"),
           ("Release gate", "quarantine, verify")]
    cells = row(5, gap=22)
    for i, ((x, w), (t, s)) in enumerate(zip(cells, ker)):
        b.append(block(x, 236, w, 76, "#fff", t, s))
        if i:
            px, pw = cells[i - 1]
            b.append(line(px + pw, 274, x - 1, 274))
    # question in, audit out
    b.append('<rect x="12" y="242" width="124" height="64" rx="32" fill="#fff" stroke="#000" '
             'stroke-width="1.4"/>'
             '<text x="74" y="269" text-anchor="middle" font-size="19">Research</text>'
             '<text x="74" y="291" text-anchor="middle" font-size="19">question</text>')
    b.append(line(136, 274, cells[0][0] - 1, 274))
    ax = X1 + 24
    b.append(cylinder(ax, 228, W - 14 - ax, 94, "Audit log", "hash-chained"))
    lx, lw = cells[-1]
    b.append(line(lx + lw, 274, ax - 1, 274))
    # (c) capability plane, called through the gateway only
    b.append(panel(X0, 392, X1 - X0, 124, "c", "Capability plane (BioScience-Harness)",
                   "Called, never trusted", dashed=True))
    cap = [(f"{n['connectors']} public connectors", f"{n['operations']} typed operations", ""),
           (f"{n['tools']} native tools", f"{n['domains']} domains", ""),
           ("bioagent.tcm", "herbs, formulas, classics", "bioagent.tcm"),
           ("Source snapshots", "content-hashed, ledger", "")]
    for (x, w), (t, s, code) in zip(row(4), cap):
        b.append(block(x, 430, w, 70, "#fff", t, s, code=code))
    gx, gw = cells[3]
    b.append(line(gx + gw / 2, 312, gx + gw / 2, 391))
    b.append(f'<text x="{gx + gw / 2 + 10}" y="369" font-size="16.5" class="it">calls</text>')
    b.append(legend(X0, 534, X1 - X0))
    return svg(W, H, "\n".join(b), "Fig. 1. TCMScience architecture",
               "(a) The governance layer (skill manifests, data contracts, a registry and lockfile "
               "with human promotion only, and a governance regression of "
               f"{n['mutants']} mutants in {n['classes']} error classes) constrains the kernel and "
               "never executes. (b) A research question enters the trusted kernel, PSH, which "
               "labels data at entry, narrows authority through a policy lattice, compiles a typed "
               "and bounded plan, calls models and tools only through its gateway, and releases "
               "results only through a quarantine-and-verify gate into a hash-chained audit log. "
               f"(c) The capability plane ({n['connectors']} public connectors with "
               f"{n['operations']} typed operations, {n['tools']} native tools in {n['domains']} "
               "domains, bioagent.tcm, and content-hashed source snapshots) is reached only "
               "through the gateway and never trusted.", style=IEEE_STYLE)


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
