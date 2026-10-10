#!/usr/bin/env python3
"""Fig. 1 | TCMScience separates what a model may propose from what the system may release.

The counts on the figure are read from the code at this commit (data/extracted/
inventory.json, written by extract_data.py from bioagent.providers, bioagent.tools,
psh.compiler.diagnostics and the connectors' live-verification record), not from prose.
"""

from __future__ import annotations

import matplotlib.patches as mpatches

import figlib as fl
import nature_style as ns

DIAG_NAMES = {"SIR": "SIR  not a program", "STAT": "STAT  statistical design",
              "TYP": "TYP  evidence types", "SCI": "SCI  method incomplete",
              "IFC": "IFC  information flow", "REP": "REP  reproducibility",
              "RES": "RES  resources", "EFF": "EFF  effects, authority"}
DOMAIN_NAMES = {"clinical-calculators": "Clinical calculators", "statistics": "Statistics",
                "sequence-analysis": "Sequence analysis", "tcm-knowledge": "TCM knowledge",
                "pharmacology": "Pharmacology", "file-formats": "File formats",
                "phylogenetics": "Phylogenetics", "variant-analysis": "Variant analysis",
                "protein-analysis": "Protein analysis",
                "population-genetics": "Population genetics",
                "sequence-alignment": "Sequence alignment",
                "survival-analysis": "Survival analysis"}


def _band(ax, x, y, w, h, colour, title, note):
    fl.box(ax, x, y, w, h, fc=ns.tint(colour, 0.07), ec=colour, lw=0.6, r=1.2)
    fl.label(ax, x + 2.2, y + 2.6, title, size=ns.SMALL, weight="bold")
    fl.label(ax, x + w - 2.2, y + 2.6, note, size=ns.TINY, ha="right", color=ns.INK2,
             style="italic")


def _cells(ax, x, y, w, h, colour, items, gap=1.6):
    n = len(items)
    cw = (w - gap * (n - 1)) / n
    out = []
    for i, (title, body) in enumerate(items):
        cx = x + i * (cw + gap)
        fl.box(ax, cx, y, cw, h, fc="white", ec=colour, lw=0.5, r=0.7)
        fl.label(ax, cx + cw / 2, y + 2.7, title, size=ns.SMALL, weight="bold", ha="center")
        fl.label(ax, cx + cw / 2, y + 5.0, body, size=ns.TINY, ha="center", va="top",
                 color=ns.INK2, linespacing=1.18)
        out.append((cx, cw))
    return out


def panel_a(fig, inv):
    ax = fl.canvas(fig, 0, 6, 183, 72)
    X0, W = 26.0, 131.0
    # governance layer
    _band(ax, X0, 0, W, 17.5, ns.ORANGE, "Governance layer",
          "constrains the kernel; never executes")
    _cells(ax, X0 + 2, 4.8, W - 4, 11.2, ns.ORANGE, [
        ("Skill manifests", "skill.yaml compiled into\na kernel program"),
        ("Data contracts", "evidence, claim, artifact;\nclaim codes CLM001–CLM019"),
        ("Registry", "content-hashed lockfile;\npromotion only by a person"),
        ("Benchmarks", "governance ablation (Fig. 3),\nevidence typing (Fig. 4)")])
    # kernel
    _band(ax, X0, 21.5, W, 29.0, ns.BLUE, "Trusted kernel (PSH)",
          "the model cannot rewrite these rules")
    k = _cells(ax, X0 + 2, 26.3, W - 4, 13.6, ns.BLUE, [
        ("Ingress", "labels every datum\nat entry"),
        ("Authority", "policy lattice that\ncan only narrow"),
        ("Compiler", f"typed, bounded\nprograms; "
                     f"{inv['compiler_diagnostics']} codes"),
        ("Gateway", "every model, tool\nand delegation call"),
        ("Release gate", "quarantine; a claim\nonly as strong as\nits evidence"),
        ("Audit", "hash-chained,\ntamper-evident")])
    for (ax0, aw), (bx0, _) in zip(k[:-1], k[1:]):
        fl.arrow(ax, ax0 + aw + 0.05, 33.1, bx0 - 0.05, 33.1, head=1.6, lw=0.5)
    fl.box(ax, X0 + 2, 41.6, W - 4, 7.0, fc="white", ec=ns.BLUE, lw=0.4, r=0.7, style="-")
    fl.label(ax, X0 + 4, 45.1, "Scientist plane", size=ns.TINY, weight="bold")
    fl.label(ax, X0 + 22.5, 45.1, "hypotheses, preregistered protocols and observations "
             "as content-hashed records · world model · inquiry engine (Fig. 7)",
             size=ns.TINY, color=ns.INK2)
    # capability plane
    _band(ax, X0, 54.5, W, 17.5, ns.GREEN, "Capability plane (BioScience-Harness)",
          "reached only through the gateway; never trusted")
    _cells(ax, X0 + 2, 59.3, W - 4, 11.6, ns.GREEN, [
        (f"{inv['public_sources']} public sources",
         f"{inv['typed_operations']} typed operations,\nall verified live"),
        (f"{inv['native_tools']} native tools", f"in {inv['native_tool_domains']} domains"),
        ("Typed TCM layer", "herbs, processing states,\n君臣佐使 formulas, 十八反"),
        ("Source snapshots", "content-hashed, on a\nhash-chained ledger"),
        ("Bound implementations", "PyDESeq2, Scanpy,\nPaperQA2, Vina, Boltz-2")])
    # the model and what leaves
    fl.box(ax, 0.3, 21.5, 17.2, 29.0, fc=ns.WASH, ec=ns.MUTED, lw=0.6, r=1.2,
           style=(0, (2.2, 1.4)))
    fl.label(ax, 8.9, 25.2, "Language\nmodel", size=ns.SMALL, weight="bold", ha="center",
             linespacing=1.05)
    fl.label(ax, 8.9, 36.0, "plans, reasons,\nproposes\nexplanations\nand predictions,\n"
             "drafts claims", size=ns.TINY, ha="center", color=ns.INK2, linespacing=1.18)
    fl.label(ax, 8.9, 47.2, "no authority", size=ns.TINY, ha="center", color=ns.INK,
             style="italic")
    fl.arrow(ax, 17.7, 32.4, 25.8, 32.4, lw=0.7)
    fl.arrow(ax, 25.8, 36.6, 17.7, 36.6, lw=0.7)
    fl.label(ax, 21.75, 30.6, "requests", size=ns.TINY, ha="center", color=ns.INK2)
    fl.label(ax, 21.75, 38.5, "labelled\nresults", size=ns.TINY, ha="center", va="top",
             color=ns.INK2, linespacing=1.05)
    out_x = X0 + W + 4.0
    out_w = 182.4 - out_x
    fl.box(ax, out_x, 21.5, out_w, 13.4, fc="white", ec=ns.INK, lw=0.6, r=1.0)
    fl.label(ax, out_x + (out_w) / 2, 24.3, "Released claim", size=ns.SMALL,
             weight="bold", ha="center")
    fl.label(ax, out_x + (out_w) / 2, 26.6, "its kind licensed;\nsnapshots and\n"
             "audit head named", size=ns.TINY, ha="center", va="top", color=ns.INK2,
             linespacing=1.15)
    fl.box(ax, out_x, 37.1, out_w, 13.4, fc="white", ec=ns.MUTED, lw=0.6, r=1.0)
    fl.label(ax, out_x + (out_w) / 2, 39.9, "Refusal", size=ns.SMALL, weight="bold",
             ha="center")
    fl.label(ax, out_x + (out_w) / 2, 42.2, "with its code\nand remedy", size=ns.TINY,
             ha="center", va="top", color=ns.INK2, linespacing=1.15)
    gx, gw = k[3]
    rx, rw = k[4]
    fl.arrow(ax, rx + rw - 1.5, 26.1, out_x - 0.3, 26.6, lw=0.7,
             connection="arc3,rad=-0.25")
    fl.arrow(ax, rx + rw - 1.5, 40.0, out_x - 0.3, 43.6, lw=0.7, connection="arc3,rad=0.2")
    # gateway <-> capability; governance constrains the kernel
    fl.arrow(ax, gx + gw / 2 - 2.0, 50.2, gx + gw / 2 - 2.0, 59.0, lw=0.7)
    fl.arrow(ax, gx + gw / 2 + 2.0, 59.0, gx + gw / 2 + 2.0, 50.2, lw=0.7)
    fl.label(ax, gx + gw / 2 - 3.0, 52.6, "calls", size=ns.TINY, ha="right",
             color=ns.INK2)
    fl.label(ax, gx + gw / 2 + 3.0, 52.6, "results", size=ns.TINY, ha="left",
             color=ns.INK2)
    for cx in (X0 + 20, X0 + 52, X0 + 84, X0 + 116):
        fl.arrow(ax, cx, 16.2, cx, 21.4, lw=0.6, dashed=True, color=ns.ORANGE)


def panel_b(fig):
    ax = fl.canvas(fig, 2.5, 86, 102, 40)
    steps = [("Provider\nrelease", "source card: licence\nand access path"),
             ("Parse", "per-source parser;\ndropped rows counted"),
             ("Normalize", "Swiss-Prot, InChIKey,\nMONDO identifiers"),
             ("Quality gate", "fails closed"),
             ("Content hash", "key@version#\nsha256[:12]"),
             ("Ledger", "hash-chained;\nverified on load")]
    n = len(steps)
    w, gap = 14.6, 2.7
    for i, (title, body) in enumerate(steps):
        x = i * (w + gap)
        fl.box(ax, x, 2.0, w, 8.6, fc=ns.tint(ns.GREEN, 0.10), ec=ns.GREEN, lw=0.5, r=0.7)
        fl.label(ax, x + w / 2, 6.3, title, size=ns.TINY, weight="bold", ha="center",
                 linespacing=1.0)
        fl.label(ax, x + w / 2, 12.2, body, size=ns.TINY, ha="center", va="top",
                 color=ns.INK2, linespacing=1.15)
        if i < n - 1:
            fl.arrow(ax, x + w + 0.1, 6.3, x + w + gap - 0.1, 6.3, head=1.6, lw=0.5)
    y = 20.5
    fl.box(ax, 0, y, 101, 8.4, fc="white", ec=ns.RULE, lw=0.4, r=0.7)
    fl.label(ax, 2.0, y + 2.6, "A run's sources", size=ns.TINY, weight="bold")
    fl.label(ax, 2.0, y + 5.7, "skill declaration ∩ source registry ∩ the run's policy: a skill "
             "can narrow its sources, never widen them", size=ns.TINY, color=ns.INK2)
    y = 30.8
    fl.box(ax, 0, y, 101, 8.4, fc="white", ec=ns.RULE, lw=0.4, r=0.7)
    fl.label(ax, 2.0, y + 2.6, "A run's lock", size=ns.TINY, weight="bold")
    fl.label(ax, 2.0, y + 5.7, "names each snapshot by id; a rerun on other content under the "
             "same label is refused", size=ns.TINY, color=ns.INK2)


def panel_c(fig, inv):
    doms = sorted(inv["native_tools_by_domain"].items(), key=lambda kv: -kv[1])
    ax = ns.axes_mm(fig, 133, 92, 16, 32)
    for i, (d, v) in enumerate(doms):
        colour = ns.GREEN if d == "tcm-knowledge" else ns.tint(ns.GREEN, 0.55)
        ax.barh(i, v, height=0.66, color=colour, lw=0)
        ax.text(v + 1.2, i, str(v), va="center", fontsize=ns.TINY, color=ns.INK)
    ax.set_yticks(range(len(doms)), [DOMAIN_NAMES[d] for d, _ in doms])
    ax.tick_params(axis="y", length=0, pad=1.5, labelsize=ns.TINY)
    ax.set_ylim(len(doms) - 0.4, -0.6)
    ax.set_xlim(0, 64)
    ax.set_xticks([0, 30, 60])
    ax.spines["left"].set_visible(False)
    ax.set_title(f"Native tools by domain ({inv['native_tools']})", fontsize=ns.TINY,
                 fontweight="bold", loc="right", pad=3)
    ns.hairline_grid(ax, "x")
    fams = sorted(inv["compiler_diagnostics_by_family"].items(), key=lambda kv: -kv[1])
    bx = ns.axes_mm(fig, 171, 92, 10, 32)
    for i, (f, v) in enumerate(fams):
        colour = ns.BLUE if f == "TYP" else ns.tint(ns.BLUE, 0.5)
        bx.barh(i, v, height=0.66, color=colour, lw=0)
        bx.text(v + 0.8, i, str(v), va="center", fontsize=ns.TINY, color=ns.INK)
    bx.set_yticks(range(len(fams)), [DIAG_NAMES[f].split("  ")[1] for f, _ in fams])
    bx.tick_params(axis="y", length=0, pad=1.5, labelsize=ns.TINY)
    bx.set_ylim(len(fams) - 0.4, -0.6)
    bx.set_xlim(0, 20)
    bx.set_xticks([0, 10, 20])
    bx.spines["left"].set_visible(False)
    bx.set_title(f"Compiler diagnostics ({inv['compiler_diagnostics']})", fontsize=ns.TINY,
                 fontweight="bold", loc="right", pad=3)
    ns.hairline_grid(bx, "x")


def main():
    ns.apply()
    inv = fl.load_json("inventory.json")
    assert inv["operations_live_verified"] == inv["typed_operations"], "unverified operations"
    fig = ns.figure(ns.DOUBLE, 128)
    panel_a(fig, inv)
    panel_b(fig)
    panel_c(fig, inv)
    for letter, x, y in (("a", 0, 1.0), ("b", 0, 81.5), ("c", 107, 81.5)):
        ns.panel_label(fig, letter, x, y)
    return ns.save(fig, "Fig1")


if __name__ == "__main__":
    for p in main():
        print(p)
