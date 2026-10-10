#!/usr/bin/env python3
"""Fig. 6 | From network pharmacology to questions the data can answer no.

Reads data/curated/robustness_holdouts.csv and routes.csv (docs/falsifiable-studies.md), and
data/extracted/berberine_*.csv (docs/studies/gqd_berberine.json, written by
BioScience-Harness/scripts/study_gqd_berberine.py under a hash-locked, exploratory protocol).
"""

from __future__ import annotations

import matplotlib.patches as mpatches
import matplotlib.ticker as mticker
import numpy as np

import figlib as fl
import nature_style as ns

VERDICT_COLOUR = {"increased": ns.BLUE, "decreased": ns.VERMILLION, "inconclusive": ns.MUTED}


def panel_a(fig):
    rows = fl.rows("robustness_holdouts.csv")
    pathways = ["Synthesis of EET and DHET", "Synthesis of 16-20-HETE",
                "Biosynthesis of maresin-like SPMs"]
    cols = [("none (baseline)", "Nothing\nheld out"),
            ("campaign", "Each of the 12 largest\nassay campaigns"),
            ("whole CYP-panel family (4161 edges)", "All CYP-panel\nscreens (4,161 edges)")]
    cell = {}
    for p in pathways:
        mine = [r for r in rows if r["pathway"] == p]
        base = next(r for r in mine if r["holdout"].startswith("none"))
        cyp = next(r for r in mine if r["holdout"].startswith("whole"))
        camp = [r for r in mine if r not in (base, cyp)]
        cell[(p, 0)] = (f"q = {float(base['q']):.3f}", "kept")
        if len(camp) == 1 and camp[0]["status"].startswith("survives"):
            cell[(p, 1)] = ("kept in 12 of 12", "kept")
        else:
            lost = ", ".join(f"{r['holdout']} (q {float(r['q']):.3f})" for r in camp)
            cell[(p, 1)] = (f"lost without\n{lost.replace(', ', ' or ')}", "lost")
        cell[(p, 2)] = (("not testable", "untestable") if cyp["status"] == "not testable"
                        else (f"q = {float(cyp['q']):.3f}", "lost"))
    ax = fl.canvas(fig, 4, 9, 90, 34)
    x0, cw, rh, top = 26.0, 21.2, 8.0, 7.0
    fills = {"kept": ns.tint(ns.BLUE, 0.16), "lost": ns.tint(ns.VERMILLION, 0.18),
             "untestable": ns.WASH}
    for j, (_, head) in enumerate(cols):
        fl.label(ax, x0 + j * cw + cw / 2, top - 0.8, head, size=ns.TINY, ha="center",
                 va="bottom", color=ns.INK, linespacing=1.1, weight="bold")
    for i, p in enumerate(pathways):
        y = top + i * rh
        fl.label(ax, x0 - 1.2, y + rh / 2, p.replace("Biosynthesis of ", "Biosynthesis of\n")
                 .replace("Synthesis of ", "Synthesis of\n"), size=ns.TINY, ha="right",
                 linespacing=1.1)
        for j in range(3):
            text, state = cell[(p, j)]
            fl.box(ax, x0 + j * cw + 0.4, y + 0.4, cw - 0.8, rh - 0.8, fc=fills[state],
                   ec="none", r=0.6)
            fl.label(ax, x0 + j * cw + cw / 2, y + rh / 2,
                     text.replace("lost without\nAID2284394 (q 0.093) or AID2283485 (q 0.086)",
                                  "lost without AID2284394\n(q 0.093) or AID2283485\n(q 0.086)"),
                     size=ns.TINY, ha="center", color=ns.INK, linespacing=1.1)
    y = top + 3 * rh + 1.6
    for k, (state, text) in enumerate((("kept", "still released"),
                                       ("lost", "no longer released"),
                                       ("untestable", "its proteins leave the background"))):
        x = x0 + k * 21.2
        ax.add_patch(mpatches.FancyBboxPatch((x, y), 2.4, 2.0, boxstyle="round,pad=0,"
                                             "rounding_size=0.4", fc=fills[state], ec="none"))
        fl.label(ax, x + 3.2, y + 1.0, text, size=ns.TINY, color=ns.INK2)


def panel_b(fig):
    pk = fl.rows("berberine_pk.csv")
    names = {"GQD vs HL": "Formula vs 黄连 alone",
             "GQD vs GQD-GC": "Formula vs formula without 甘草",
             "GQD-GC vs HL": "Without 甘草 vs 黄连 alone"}
    ax = ns.axes_mm(fig, 130, 11, 25, 33)
    y = 0
    ticks, labels, heads = [], [], []
    for contrast in ("GQD vs HL", "GQD vs GQD-GC", "GQD-GC vs HL"):
        heads.append((y, names[contrast]))
        y += 0.9
        for r in [p for p in pk if p["contrast"] == contrast]:
            ratio, lo, hi = (float(r[k]) for k in ("ratio", "ci_low", "ci_high"))
            colour = VERDICT_COLOUR[r["verdict"]]
            ax.plot([lo, hi], [y, y], color=colour, lw=0.8, zorder=2)
            ax.plot([ratio], [y], "o" if r["metric"].startswith("AUC") else "D",
                    ms=3.0 if r["metric"].startswith("AUC") else 2.6, mfc=colour,
                    mec="white", mew=0.5, zorder=3)
            ax.text(1.04, y, f"{ratio:.2f} ({lo:.2f}–{hi:.2f})", fontsize=ns.TINY,
                    va="center", ha="left", color=ns.INK, transform=ax.get_yaxis_transform(),
                    clip_on=False)
            ticks.append(y)
            labels.append("AUC$_{last}$" if r["metric"].startswith("AUC") else "C$_{max}$")
            y += 1
        y += 0.45
    ax.axvspan(0.8, 1.25, color=ns.WASH, zorder=0, lw=0)
    ax.axvline(1, color=ns.INK2, lw=0.5, zorder=1)
    ax.set_xscale("log")
    ax.set_xlim(0.2, 5)
    ax.xaxis.set_major_locator(mticker.FixedLocator([0.25, 0.5, 1, 2, 4]))
    ax.xaxis.set_major_formatter(mticker.FixedFormatter(["0.25", "0.5", "1", "2", "4"]))
    ax.xaxis.set_minor_formatter(mticker.NullFormatter())
    ax.set_yticks(ticks, labels)
    ax.tick_params(axis="y", length=0, pad=2)
    ax.set_ylim(y - 0.3, -0.6)
    ax.spines["left"].set_visible(False)
    for yy, name in heads:
        ax.text(-0.04, yy, name, transform=ax.get_yaxis_transform(), fontsize=ns.TINY,
                fontweight="bold", ha="right", va="center", color=ns.INK, clip_on=False)
    ax.set_xlabel("Ratio of berberine exposure (90% CI)")
    ax.text(1.04, -0.9, "ratio (90% CI)", fontsize=ns.TINY, color=ns.MUTED,
            transform=ax.get_yaxis_transform(), ha="left", va="center", fontstyle="italic",
            clip_on=False)
    kax = fl.canvas(fig, 103, 52.5, 80, 3)
    x = 0
    for verdict, colour in VERDICT_COLOUR.items():
        kax.plot([x, x + 3.2], [1.5, 1.5], color=colour, lw=0.8)
        kax.plot([x + 1.6], [1.5], "o", ms=2.6, mfc=colour, mec="white", mew=0.5)
        fl.label(kax, x + 4.2, 1.5, verdict, size=ns.TINY)
        x += 17
    kax.add_patch(mpatches.Rectangle((x, 0.6), 3.2, 1.8, fc=ns.WASH, ec="none"))
    fl.label(kax, x + 4.2, 1.5, "0.80–1.25", size=ns.TINY)


def panel_c(fig):
    expo = fl.rows("berberine_exposure.csv")
    summary = fl.load_json("berberine_summary.json")
    hl = [r for r in expo if r["group"] == "HL"]
    groups = [("HL", "黄连 alone"), ("GQD-GC", "Formula without 甘草"), ("GQD", "Whole formula")]
    ax = ns.axes_mm(fig, 33, 76, 76, 46)
    fu = (0.01, 1.0)
    for i, (g, name) in enumerate(groups):
        cmax = summary["groups"][g]["cmax_nM_total"] / 1000.0          # µM, total
        lo, hi = cmax * fu[0], cmax / 0.1 * fu[1]
        ax.add_patch(mpatches.Rectangle((lo, i - 0.26), hi - lo, 0.52, fc=ns.WASH, ec="none"))
        ax.plot([cmax, cmax], [i - 0.26, i + 0.26], color=ns.INK, lw=0.9)
        ax.text(cmax, i - 0.33, f"C$_{{max}}$ {1000 * cmax:.0f} nM", fontsize=ns.TINY,
                ha="center", va="bottom", color=ns.INK)
        v = summary["groups"][g]["verdicts"]
        assert v.get("plausible", 0) == 0
        for x_mm, value in ((5, v.get("plausible", 0)), (13, v.get("depends_on_unknowns", 0)),
                            (21, v.get("implausible", 0))):
            ax.text(1 + x_mm / 76, i, str(value), transform=ax.get_yaxis_transform(),
                    fontsize=ns.TINY, va="center", ha="center", color=ns.INK, clip_on=False)
    for x_mm, head in ((5, "plaus-\nible"), (13, "depends\non f$_u$"), (21, "implaus-\nible")):
        ax.text(1 + x_mm / 76, -0.8, head, transform=ax.get_yaxis_transform(),
                fontsize=ns.TINY, va="center", ha="center", color=ns.MUTED,
                clip_on=False, fontstyle="italic", linespacing=1.0)
    # the assays not ruled out at the highest exposure, by family
    fam = {"MEA": ("Rat cortical network MEA", ns.PURPLE),
           "CYP2D6": ("CYP2D6 inhibition", ns.BLUE),
           "other": ("Other Tox21 assays", ns.MUTED)}
    def family(a):
        if a.startswith("CCTE_Shafer_MEA"):
            return "MEA"
        if "CYP2D6" in a:
            return "CYP2D6"
        return "other"
    rng = np.random.default_rng(20261010)
    y0 = len(groups) + 0.35
    for r in hl:
        f = family(r["assay"])
        ax.plot([float(r["ac50_uM"])], [y0 + rng.uniform(-0.22, 0.22)], "o", ms=2.4,
                mfc=fam[f][1], mec="white", mew=0.4, zorder=3)
    n_inactive = summary["assays_inactive"]
    ax.text(1 + 2 / 76, y0, f"of {summary['assays_active']} active AC$_{{50}}$s;\n"
            f"{summary['assays_inactive']} assays inactive", transform=ax.get_yaxis_transform(),
            fontsize=ns.TINY, va="center", ha="left", color=ns.INK2, clip_on=False,
            linespacing=1.1)
    ax.set_xscale("log")
    ax.set_xlim(5e-4, 30)
    ax.set_ylim(y0 + 0.6, -0.95)
    ax.set_yticks(list(range(len(groups))) + [y0],
                  [n for _, n in groups] + ["AC$_{50}$ of the 28\nactives not ruled out"])
    ax.tick_params(axis="y", length=0, pad=2)
    ax.spines["left"].set_visible(False)
    ax.xaxis.set_major_locator(mticker.LogLocator(numticks=10))
    ax.xaxis.set_major_formatter(mticker.FuncFormatter(
        lambda v, _: {1e-3: "0.001", 1e-2: "0.01", 1e-1: "0.1", 1: "1", 10: "10"}.get(v, "")))
    ax.xaxis.set_minor_formatter(mticker.NullFormatter())
    ns.hairline_grid(ax, "x")
    ax.set_xlabel("Concentration (µM)")
    kax = fl.canvas(fig, 33, 129.5, 100, 3)
    x = 0
    for f in ("MEA", "CYP2D6", "other"):
        kax.plot([x + 1], [1.5], "o", ms=2.4, mfc=fam[f][1], mec="white", mew=0.4)
        fl.label(kax, x + 2.6, 1.5, fam[f][0], size=ns.TINY)
        x += 24
    kax.add_patch(mpatches.Rectangle((x - 4, 0.6), 3.2, 1.8, fc=ns.WASH, ec="none"))
    fl.label(kax, x + 0.2 - 4, 1.5, "  AC$_{50}$ at which the verdict depends on f$_u$ "
             "(0.01 × to 10 × C$_{max}$)", size=ns.TINY)


def panel_d(fig):
    routes = fl.rows("routes.csv")
    style = {"run": ("Run on real data", ns.BLUE, "o", True),
             "partly": ("Partly", ns.BLUE, "o", False),
             "implemented": ("Analysis implemented and tested", ns.INK2, "s", False),
             "records": ("Records in place", ns.INK2, "s", False),
             "not started": ("Not started", ns.RULE, "x", False)}
    ax = fl.canvas(fig, 140, 74, 43, 52)
    for i, r in enumerate(routes):
        y = 2.0 + i * 5.6
        name, colour, marker, filled = style[r["status"]]
        if marker == "x":
            ax.plot([1.6], [y], marker="x", ms=3.0, mec=colour, mew=0.8)
        else:
            ax.plot([1.6], [y], marker=marker, ms=3.4, mfc=colour if filled else "white",
                    mec=colour, mew=0.7)
        fl.label(ax, 4.0, y, f"{r['route']}  {r['name']}", size=ns.TINY, weight="bold")
        shown = f" (panel {r['shown']})" if r["shown"] else ""
        fl.label(ax, 6.2, y + 2.3, name.lower() + shown, size=ns.TINY, color=ns.INK2)


def main():
    ns.apply()
    fig = ns.figure(ns.DOUBLE, 140)
    panel_a(fig)
    panel_b(fig)
    panel_c(fig)
    panel_d(fig)
    for letter, x, y in (("a", 0, 1.5), ("b", 97, 1.5), ("c", 0, 64.5), ("d", 136, 64.5)):
        ns.panel_label(fig, letter, x, y)
    return ns.save(fig, "Fig6")


if __name__ == "__main__":
    for p in main():
        print(p)
