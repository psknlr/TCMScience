#!/usr/bin/env python3
"""Fig. 4 | Typing study designs in real literature: what the evidence types rest on.

Reads data/extracted/evidence_typing_*.csv and evidence_typing_summary.json
(BioScience-Harness/benchmarks/evidence_typing/results.json, which holds both rule versions).
"""

from __future__ import annotations

import matplotlib.patches as mpatches
import numpy as np
from matplotlib.colors import Normalize

import figlib as fl
import nature_style as ns

DESIGNS = ["systematic_review", "randomized_trial", "observational", "case_report",
           "animal", "in_vitro"]
NAME = {"systematic_review": "Systematic review", "randomized_trial": "Randomized trial",
        "observational": "Observational", "case_report": "Case report", "animal": "Animal",
        "in_vitro": "In vitro", "narrative_review": "Narrative review",
        "editorial": "Editorial", "protocol": "Trial protocol"}


def panel_a(fig):
    s = fl.load_json("evidence_typing_summary.json")
    ax = fl.canvas(fig, 4, 5, 82, 56)
    steps = [
        ("Sample frozen before any rule read it",
         f"{s['records']} open-access abstracts (CC BY or CC0) from Europe PMC in 18 strata:\n"
         "430 English (2022), 48 Chinese. Reference: PubMed publication type\n"
         f"(MeSH for animal and in vitro). Sample SHA-256 {s['sample_sha256']}…"),
        ("Split fixed by a seeded hash of each PMID",
         f"dev {s['dev_records']} · test {s['test_records']} "
         f"({s['test_labelled']} studies, {s['test_negatives']} negatives: narrative reviews,\n"
         "editorials, trial protocols). Re-derived on load, so no record can move."),
        ("Rules changed looking at dev only",
         f"evidence.py {s['rules_before']}… → {s['rules_after']}… (SHA-256). An ambiguous\n"
         "text stays unassessed: the rules may abstain, never guess."),
        ("Test read once, by both rule versions",
         "Errors were then read one by one and described; the rules were not\n"
         "changed afterwards. Agreement is with PubMed, not with the truth."),
    ]
    y = 1.0
    for i, (title, body) in enumerate(steps, start=1):
        ax.add_patch(mpatches.Circle((2.4, y + 2.0), 2.0, fc=ns.BLUE, ec="none"))
        fl.label(ax, 2.4, y + 2.05, str(i), size=ns.SMALL, ha="center", color="white",
                 weight="bold")
        fl.label(ax, 6.2, y + 2.0, title, weight="bold")
        fl.label(ax, 6.2, y + 4.4, body, size=ns.TINY, va="top", color=ns.INK2,
                 linespacing=1.3)
        if i < len(steps):
            ax.plot([2.4, 2.4], [y + 4.4, y + 13.0], color=ns.RULE, lw=0.6)
        y += 13.6


def panel_b(fig):
    conf = fl.rows("evidence_typing_confusion_test.csv")
    cols = DESIGNS + ["ambiguous", "no rule"]
    col_names = ["Systematic review", "Randomized trial", "Observational", "Case report",
                 "Animal", "In vitro", "Ambiguous", "No rule"]
    left, top, cw, rh = 124.0, 15.0, 6.6, 4.3
    n_rows = len(conf)
    ax = ns.axes_mm(fig, left, top, cw * len(cols), rh * n_rows)
    ax.set_xlim(0, len(cols))
    ax.set_ylim(n_rows, 0)
    ax.set_axis_off()
    norm = Normalize(0, 1)
    for i, r in enumerate(conf):
        ref = r["reference"]
        total = sum(int(r[c]) for c in cols)
        for j, c in enumerate(cols):
            v = int(r[c])
            frac = v / total
            colour = ns.BLUES(norm(0.08 + 0.92 * frac)) if v else "white"
            ax.add_patch(mpatches.Rectangle((j + 0.03, i + 0.04), 0.94, 0.92, fc=colour,
                                            ec=ns.HAIR if not v else "none", lw=0.3))
            if v:
                ax.text(j + 0.5, i + 0.52, str(v), ha="center", va="center",
                        fontsize=ns.TINY, color=ns.ink_on(colour))
            correct = (c == ref) if ref in DESIGNS else c in ("ambiguous", "no rule")
            if correct:
                ax.add_patch(mpatches.Rectangle((j + 0.03, i + 0.04), 0.94, 0.92, fc="none",
                                                ec=ns.INK, lw=0.6 if v else 0.4, zorder=3))
        ax.text(-0.15, i + 0.52, NAME[ref], ha="right", va="center", fontsize=ns.TINY,
                color=ns.INK)
        ax.text(len(cols) + 0.15, i + 0.52, str(total), ha="left", va="center",
                fontsize=ns.TINY, color=ns.INK2)
    ax.text(len(cols) + 0.15, -0.35, "n", ha="left", va="bottom", fontsize=ns.TINY,
            color=ns.MUTED, fontstyle="italic")
    for j, name in enumerate(col_names):
        ax.text(j + 0.5, -0.25, name, ha="left", va="bottom", rotation=42,
                rotation_mode="anchor", fontsize=ns.TINY, color=ns.INK)
    ax.plot([0, len(cols)], [6, 6], color=ns.INK2, lw=0.5)
    ax.plot([6, 6], [0, n_rows], color=ns.INK2, lw=0.5)
    for a, b, text in ((0.1, 5.9, "Studies"), (6.1, 8.9, "Negatives")):
        ax.plot([-4.05, -4.2, -4.2, -4.05], [a, a, b, b], color=ns.INK2, lw=0.5,
                clip_on=False)
        ax.text(-4.45, (a + b) / 2, text, rotation=90, ha="center", va="center",
                fontsize=ns.TINY, color=ns.INK2, fontstyle="italic")
    ax.text(-5.25, n_rows / 2, "Reference: PubMed publication type", rotation=90,
            ha="center", va="center", fontsize=ns.TINY, color=ns.INK2)
    ax.text(len(cols) / 2, n_rows + 0.9, "Design the rules read (test split, 302 abstracts)",
            ha="center", va="top", fontsize=ns.TINY, color=ns.INK2)


def _interval(ax, y, rate, lo, hi, colour, filled=True, z=3):
    ax.plot([100 * lo, 100 * hi], [y, y], color=colour, lw=0.7, solid_capstyle="butt",
            zorder=z - 1)
    ax.plot([100 * rate], [y], "o", ms=3.0, mfc=colour if filled else "white",
            mec=colour if not filled else "white", mew=0.6 if not filled else 0.5, zorder=z)


def panel_c(fig):
    d = fl.rows("evidence_typing_designs.csv")
    get = {(r["version"], r["split"], r["design"], r["metric"]): r for r in d}
    for col, (metric, title) in enumerate((("precision", "Precision"),
                                           ("recall", "Recall"))):
        ax = ns.axes_mm(fig, 27 + col * 43, 77, 33, 38)
        for i, design in enumerate(DESIGNS):
            b = get[("before", "test", design, metric)]
            a = get[("after", "test", design, metric)]
            yb, ya = i - 0.17, i + 0.17
            _interval(ax, yb, float(b["rate"]), float(b["ci_low"]), float(b["ci_high"]),
                      ns.MUTED, filled=False, z=2)
            _interval(ax, ya, float(a["rate"]), float(a["ci_low"]), float(a["ci_high"]),
                      ns.BLUE)
            ax.text(103, ya, f"{a['k']}/{a['n']}", fontsize=ns.TINY, va="center",
                    ha="left", color=ns.INK, clip_on=False)
        ax.set_ylim(len(DESIGNS) - 0.5, -0.6)
        ax.set_xlim(0, 101)
        ax.set_xticks([0, 50, 100])
        ax.xaxis.set_minor_locator(__import__("matplotlib").ticker.MultipleLocator(25))
        ax.set_yticks(range(len(DESIGNS)), [NAME[x] for x in DESIGNS] if col == 0 else [])
        ax.tick_params(axis="y", length=0, pad=2)
        ax.spines["left"].set_visible(False)
        ns.hairline_grid(ax, "x")
        ax.set_xlabel(f"{title} (%)")
        ax.set_title(title, fontsize=ns.SIZE, fontweight="bold", loc="left", pad=3)
    # key
    kax = fl.canvas(fig, 27, 70.5, 70, 3)
    for x, colour, filled, text in ((0, ns.MUTED, False, "Rules before revision"),
                                    (34, ns.BLUE, True, "Rules after revision on dev")):
        kax.plot([x, x + 4], [1.5, 1.5], color=colour, lw=0.7)
        kax.plot([x + 2], [1.5], "o", ms=3.0, mfc=colour if filled else "white",
                 mec="white" if filled else colour, mew=0.5 if filled else 0.6)
        fl.label(kax, x + 5.4, 1.5, text, size=ns.TINY, color=ns.INK)


def panel_d(fig):
    o = {(r["version"], r["split"], r["metric"]): r for r in fl.rows("evidence_typing_overall.csv")}
    metrics = [("precision", "Readings\ncorrect"), ("coverage", "Studies\ntyped"),
               ("negatives_typed", "Negatives\ntyped")]
    for j, (metric, title) in enumerate(metrics):
        ax = ns.axes_mm(fig, 131.5 + j * 18.5, 77, 9, 30)
        for split, colour, filled in (("dev", ns.MUTED, False), ("test", ns.BLUE, True)):
            ys = [100 * float(o[(v, split, metric)]["rate"]) for v in ("before", "after")]
            ax.plot([0, 1], ys, color=colour, lw=0.8, zorder=2)
            for x, v in enumerate(("before", "after")):
                r = o[(v, split, metric)]
                if split == "test":
                    ax.plot([x, x], [100 * float(r["ci_low"]), 100 * float(r["ci_high"])],
                            color=colour, lw=0.6, zorder=2)
                ax.plot([x], [100 * float(r["rate"])], "o", ms=2.8,
                        mfc=colour if filled else "white", mec="white" if filled else colour,
                        mew=0.5 if filled else 0.6, zorder=3)
            if split == "test":
                for x, v in enumerate(("before", "after")):
                    r = o[(v, split, metric)]
                    ax.text(x + (0.16 if x else -0.16), 100 * float(r["rate"]),
                            f"{r['k']}/{r['n']}", fontsize=ns.TINY,
                            ha="left" if x else "right", va="center", color=ns.INK,
                            clip_on=False)
        ax.set_xlim(-0.35, 1.35)
        ax.set_ylim(0, 100)
        ax.set_xticks([0, 1], ["Before", "After"], rotation=0)
        ax.tick_params(axis="x", labelsize=ns.TINY, pad=1)
        ax.set_yticks([0, 50, 100], ["0", "50", "100"] if j == 0 else [])
        ax.spines["left"].set_visible(False)
        ax.tick_params(axis="y", length=0)
        ns.hairline_grid(ax, "y")
        ax.set_title(title, fontsize=ns.TINY, fontweight="bold", pad=2, linespacing=1.0)
        if j == 0:
            ax.set_ylabel("Share (%)")
    kax = fl.canvas(fig, 133, 112, 48, 4)
    for x, colour, filled, text in ((0, ns.BLUE, True, "test (302)"),
                                    (20, ns.MUTED, False, "dev (176)")):
        kax.plot([x, x + 4], [2, 2], color=colour, lw=0.8)
        kax.plot([x + 2], [2], "o", ms=2.8, mfc=colour if filled else "white",
                 mec="white" if filled else colour, mew=0.5 if filled else 0.6)
        fl.label(kax, x + 5.2, 2, text, size=ns.TINY)


def panel_e(fig):
    filled = {r["field"]: r for r in fl.rows("evidence_typing_fields_test.csv")}
    check = {r["field"]: r for r in fl.rows("evidence_typing_manual_check.csv")}
    s = fl.load_json("evidence_typing_summary.json")["manual_correct"]
    ax = ns.axes_mm(fig, 138, 131, 26, 13)
    fields = ["population", "comparator", "outcome"]
    parts = [("correct", ns.BLUE), ("partly", ns.SKY), ("wrong", ns.VERMILLION)]
    for i, f in enumerate(fields):
        x = 0
        for key, colour in parts:
            v = int(check[f][key])
            if v:
                ax.barh(i, v, left=x, height=0.62, color=colour, lw=0)
                if v >= 2:
                    ax.text(x + v / 2, i, str(v), ha="center", va="center",
                            fontsize=ns.TINY, color=ns.ink_on(colour))
            x += v
        r = filled[f]
        ax.text(15.8, i, f"{r['filled']}/{r['n']} ({100 * float(r['rate']):.0f}%)",
                fontsize=ns.TINY, va="center", ha="left", color=ns.INK2,
                clip_on=False)
    ax.text(15.8, -0.95, "Filled", fontsize=ns.TINY, va="center", ha="left",
            color=ns.MUTED, fontstyle="italic", clip_on=False)
    ax.set_yticks(range(3), ["Population", "Comparator", "Outcome"])
    ax.set_ylim(2.6, -0.6)
    ax.set_xlim(0, 15)
    ax.set_xticks([0, 5, 10, 15])
    ax.tick_params(axis="y", length=0, pad=2)
    ax.spines["left"].set_visible(False)
    ax.set_xlabel("Values checked by hand")
    kax = fl.canvas(fig, 124, 124.5, 58, 3)
    x = 0
    for (key, colour), text in zip(parts, ("correct", "partly", "wrong")):
        kax.add_patch(mpatches.Rectangle((x, 0.6), 2.2, 1.8, fc=colour, ec="none"))
        fl.label(kax, x + 3.0, 1.5, text, size=ns.TINY)
        x += 12
    ax.text(0.5, -0.62, f"{s['k']}/{s['n']} correct ({100 * s['rate']:.0f}%; 95% CI "
            f"{100 * s['ci95'][0]:.0f}–{100 * s['ci95'][1]:.0f}%)", transform=ax.transAxes,
            ha="center", va="top", fontsize=ns.TINY, color=ns.INK2)


def main():
    ns.apply()
    fig = ns.figure(ns.DOUBLE, 156)
    panel_a(fig)
    panel_b(fig)
    panel_c(fig)
    panel_d(fig)
    panel_e(fig)
    for letter, x, y in (("a", 0, 1.5), ("b", 90, 1.5), ("c", 0, 66.5), ("d", 118, 66.5),
                         ("e", 118, 121.5)):
        ns.panel_label(fig, letter, x, y)
    return ns.save(fig, "Fig4")


if __name__ == "__main__":
    for p in main():
        print(p)
