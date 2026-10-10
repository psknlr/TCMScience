#!/usr/bin/env python3
"""Fig. 5 | Re-analysis of the 葛根芩连汤 network-pharmacology signal on public data.

Reads data/curated/np_*.csv: the case study's counts as the repository documents them
(README.md and BioScience-Harness/docs/THIRD_PARTY_DB_CONNECTOR_SPEC.md, cited line by line).
Fold enrichments and hypergeometric P values are recomputed here from those counts and must
agree with the reported ones.
"""

from __future__ import annotations

import matplotlib.patches as mpatches
import numpy as np
from scipy.stats import hypergeom

import figlib as fl
import nature_style as ns

FAMILY_COLOUR = {"CYP450": ns.BLUE, "Nuclear receptor": ns.PURPLE,
                 "Carbonic anhydrase": ns.VERMILLION}
CARRIER_COLOUR = {"cytochrome P450": ns.BLUE, "carbonic anhydrases": ns.VERMILLION,
                  "nuclear receptors": ns.PURPLE, "casein kinase 2 (3 subunits)": ns.MUTED}


def sci(p: float) -> str:
    """P value as Nature prints it: two significant figures, powers of ten below 0.001."""
    if p >= 0.001:
        return f"{p:.2g}" if p < 0.1 else f"{p:.2f}"
    m, e = f"{p:.1e}".split("e")
    return rf"{m}$\times$10$^{{{int(e)}}}$"


def panel_a(fig, c):
    ax = fl.canvas(fig, 3, 5, 86, 68)
    fl.box(ax, 0, 0, 86, 12.6, fc=ns.WASH, ec=ns.RULE, lw=0.4)
    fl.label(ax, 43, 2.9, "葛根芩连汤 Gegen Qinlian decoction (伤寒论 Shanghan Lun)",
             ha="center", weight="bold")
    herbs = [("葛根", "Gegen", "puerarin"), ("黄芩", "Huangqin", "baicalin"),
             ("黄连", "Huanglian", "berberine"), ("甘草", "Gancao", "glycyrrhizic acid")]
    for i, (zh, py, marker) in enumerate(herbs):
        x = 2.0 + i * 20.8
        fl.box(ax, x, 5.4, 19.4, 6.2, fc="white", ec=ns.RULE, lw=0.4)
        fl.label(ax, x + 9.7, 7.3, f"{zh} {py}", size=ns.TINY, ha="center", weight="bold")
        fl.label(ax, x + 9.7, 9.9, marker, size=ns.TINY, ha="center", color=ns.INK2,
                 style="italic")
    fl.arrow(ax, 43, 12.8, 43, 15.6)
    fl.box(ax, 10, 15.8, 66, 7.2, fc="white", ec=ns.INK2, lw=0.5)
    fl.label(ax, 43, 18.0, f"{c['constituents']:,.0f} constituents", ha="center",
             weight="bold")
    fl.label(ax, 43, 21.0, "NPASS 2.0 · CMAUP 2.0 · LOTUS; each herb's marker compound in "
             "all three", size=ns.TINY, ha="center", color=ns.INK2)
    for x in (20.5, 65.5):
        fl.arrow(ax, x, 23.2, x, 26.6)
    col = [(0, "Curated potency (IC50, Ki, Kd, EC50)", ns.INK2),
           (45, "PubChem BioAssay, inactive results kept", ns.INK2)]
    for x, title, _ in col:
        fl.label(ax, x + 20.5, 28.4, title, size=ns.TINY, ha="center", weight="bold")
    left = [(f"{c['assayed_proteins']:.0f} human proteins assayed", "any reported potency"),
            (f"{c['assayed_hits']:.0f} at ≤ 10 µM ({c['assayed_hit_share']:.0f}%)",
             "databases rarely keep inactives")]
    right = [(f"{c['pubchem_cids']:,.0f} compounds · {c['pubchem_results']:,.0f} results",
              "every constituent with a PubChem CID"),
             (f"{c['pubchem_definitive']:,.0f} definitive on {c['pubchem_proteins']:,.0f} "
              "proteins", f"{c['pubchem_active']:,.0f} active; human Swiss-Prot only"),
             (f"{c['screening_proteins']:.0f} proteins × ≥ 20 constituents",
              f"{c['screening_tests']:,.0f} tests, "
              f"{100 * c['screening_active'] / c['screening_tests']:.1f}% active")]
    for x, items in ((0, left), (45, right)):
        y = 30.6
        for i, (head, sub) in enumerate(items):
            fl.box(ax, x, y, 41, 7.4, fc="white", ec=ns.INK2, lw=0.5)
            fl.label(ax, x + 20.5, y + 2.5, head, size=ns.SMALL, ha="center", weight="bold")
            fl.label(ax, x + 20.5, y + 5.3, sub, size=ns.TINY, ha="center", color=ns.INK2)
            if i < len(items) - 1:
                fl.arrow(ax, x + 20.5, y + 7.6, x + 20.5, y + 10.0)
            y += 10.2
    fl.box(ax, 0, 51.0, 41, 12.0, fc=ns.WASH, ec=ns.RULE, lw=0.4)
    fl.label(ax, 20.5, 53.3, "Biology and indication", size=ns.TINY, ha="center",
             weight="bold")
    fl.label(ax, 20.5, 58.6, "Reactome pathways (CC0) · STRING v12\n"
             "Open Targets 26.09: type 2 diabetes", size=ns.TINY, ha="center",
             color=ns.INK2, linespacing=1.3)


def panel_b(fig, c):
    # pathways significant under each background
    ax = ns.axes_mm(fig, 107, 12, 26, 24)
    bgs = [("Whole\nannotation", c["reactome_background"], c["reactome_pathways_tested"],
            c["reactome_significant"]),
           ("Assayed\nproteins", c["assayed_background"], c["assayed_pathways_tested"],
            c["assayed_significant"])]
    for i, (name, prot, tested, sig) in enumerate(bgs):
        ax.bar(i, sig, width=0.56, color=ns.BLUE if sig else ns.MUTED, lw=0)
        ax.text(i, sig + 2.0, f"{sig:.0f}/{tested:,.0f}", ha="center", va="bottom",
                fontsize=ns.TINY, color=ns.INK)
        ax.text(i, -0.30, f"{prot:,.0f}\nproteins", ha="center", va="top",
                fontsize=ns.TINY, color=ns.INK2, linespacing=1.05,
                transform=ax.get_xaxis_transform())
    ax.set_xticks([0, 1], [b[0] for b in bgs], linespacing=1.0)
    ax.tick_params(axis="x", length=0, pad=2)
    ax.set_xlim(-0.6, 1.6)
    ax.set_ylim(0, 80)
    ax.set_yticks([0, 20, 40, 60, 80])
    ax.set_ylabel("Pathways significant\n(of those tested)", linespacing=1.1)
    ns.hairline_grid(ax, "y")
    # the carbonic-anhydrase pathway under each background
    ax2 = ns.axes_mm(fig, 150, 12, 26, 24)
    members, hits = c["ca_pathway_members"], c["ca_pathway_hits"]
    folds = []
    for i, (name, prot, _, _) in enumerate(bgs):
        expected = members * c["assayed_hits"] / prot
        fold = hits / expected
        folds.append(fold)
        ax2.bar(i, fold, width=0.56, color=ns.VERMILLION, lw=0)
        if fold > 3:
            ax2.text(i, fold * 1.18, f"{fold:.1f}×", ha="center", va="bottom",
                     fontsize=ns.TINY, color=ns.INK)
        else:
            ax2.text(i, np.sqrt(fold * 0.45), f"{fold:.1f}×", ha="center", va="center",
                     fontsize=ns.TINY, color="white")
        ax2.text(i, -0.30, f"{hits:.0f}/{members:.0f} hits\nvs {expected:.2g}\nexpected",
                 ha="center", va="top", fontsize=ns.TINY, color=ns.INK2,
                 linespacing=1.05, transform=ax2.get_xaxis_transform())
    cap = c["assayed_background"] / c["assayed_hits"]
    assert abs(cap - c["max_fold_assayed"]) < 0.01, cap
    ax2.plot([0.62, 1.38], [cap, cap], color=ns.INK, lw=0.6, ls=(0, (2, 1.4)))
    ax2.text(1.42, cap, f"max.\n{cap:.2f}×", fontsize=ns.TINY, va="center",
             ha="left", color=ns.INK2, linespacing=1.0)
    ax2.set_yscale("log")
    ax2.set_ylim(0.45, 200)
    ax2.set_yticks([1, 10, 100], ["1", "10", "100"])
    ax2.yaxis.set_minor_formatter(__import__("matplotlib").ticker.NullFormatter())
    ax2.set_xticks([0, 1], [b[0] for b in bgs], linespacing=1.0)
    ax2.tick_params(axis="x", length=0, pad=2)
    ax2.set_xlim(-0.6, 1.9)
    ax2.set_ylabel("Carbonic-anhydrase pathway\nfold enrichment", linespacing=1.1)
    ns.hairline_grid(ax2, "y")
    return folds


def panel_c(fig, c):
    prots = fl.rows("np_screening_proteins.csv")
    groups = [("CYP450", "Cytochrome P450 (uniform Tox21 and qHTS panels)"),
              ("Nuclear receptor", "Nuclear receptors (uniform panels)"),
              ("Carbonic anhydrase", "Carbonic anhydrases (literature sets chosen for actives)")]
    ax = ns.axes_mm(fig, 21, 86, 52, 76)
    y = 0
    ticks, labels = [], []
    for fam, title in groups:
        ax.text(-0.3, y, title, transform=ax.get_yaxis_transform(), fontsize=ns.TINY,
                fontweight="bold", color=ns.INK, ha="left", va="center", clip_on=False,
                zorder=5, bbox=dict(fc="white", ec="none", pad=0.6))
        y += 1.0
        for r in [p for p in prots if p["family"] == fam]:
            k, n = int(r["active"]), int(r["tested"])
            lo, hi = ns.wilson(k, n)
            colour = FAMILY_COLOUR[fam]
            ax.plot([100 * lo, 100 * hi], [y, y], color=colour, lw=0.7, zorder=2)
            ax.plot([100 * k / n], [y], "o", ms=3.0, mfc=colour, mec="white", mew=0.5,
                    zorder=3)
            ax.text(104, y, f"{k}/{n}", fontsize=ns.TINY, va="center", ha="left",
                    color=ns.INK, clip_on=False)
            ticks.append(y)
            labels.append(r["protein"])
            y += 1
        y += 0.5
    overall = 100 * c["screening_active"] / c["screening_tests"]
    ax.axvline(overall, color=ns.INK, lw=0.6, ls=(0, (2, 1.4)), zorder=1)
    ax.text(overall + 1.5, y - 0.55, f"all {c['screening_proteins']:.0f} proteins: "
            f"{overall:.1f}% active", fontsize=ns.TINY, color=ns.INK, va="center")
    ax.set_yticks(ticks, labels)
    ax.set_ylim(y - 0.1, -0.6)
    ax.set_xlim(0, 101)
    ax.set_xticks([0, 25, 50, 75, 100])
    ax.tick_params(axis="y", length=0, pad=2)
    ax.spines["left"].set_visible(False)
    ns.hairline_grid(ax, "x")
    ax.set_xlabel("Constituents active among those tested (%)")
    ax.text(104, -0.95, "k/n", fontsize=ns.TINY, color=ns.MUTED, va="center", ha="left",
            clip_on=False, fontstyle="italic")


def panel_d(fig):
    rows = fl.rows("np_threshold_pathways.csv")
    th = {int(r["min_constituents"]): r for r in fl.rows("np_thresholds.csv")}
    order = ["Xenobiotics", "Synthesis of EET and DHET", "Synthesis of 16-20-HETE",
             "Biosynthesis of maresin-like SPMs", "Aspirin ADME", "CYP2E1 reactions",
             "Aflatoxin activation and detoxification",
             "Nuclear receptor transcription pathway",
             "Reversible hydration of carbon dioxide",
             "Erythrocytes take up oxygen and release CO2",
             "WNT mediated activation of DVL", "Condensation of prometaphase chromosomes"]
    carrier = {r["pathway"]: r["carrier"] for r in rows}
    passed = {(r["pathway"], int(r["min_constituents"])): r["q"] for r in rows}
    assert set(order) == set(carrier)
    for t in (10, 20, 50):
        assert int(th[t]["pathways_passing"]) == sum(k[1] == t for k in passed), t
    ax = ns.axes_mm(fig, 141, 110, 27, 56)
    cols = [10, 20, 50]
    for i, pw in enumerate(order):
        colour = CARRIER_COLOUR[carrier[pw]]
        for j, t in enumerate(cols):
            if (pw, t) in passed:
                q = passed[(pw, t)]
                ax.plot([j], [i], "o", ms=4.2, mfc=colour, mec="white", mew=0.5, zorder=3)
                if q:
                    ax.text(j + 0.2, i, q.lstrip("0") if q.startswith("0.") else q,
                            fontsize=ns.TINY, va="center", ha="left", color=ns.INK2)
            else:
                ax.plot([j], [i], "o", ms=2.0, mfc="white", mec=ns.RULE, mew=0.5, zorder=3)
    ax.set_yticks(range(len(order)), order)
    ax.tick_params(axis="y", length=0, pad=2, labelsize=ns.TINY)
    for lab in ax.get_yticklabels():
        lab.set_color(ns.INK)
    ax.set_xticks(range(3), [f"≥ {t}" for t in cols])
    ax.xaxis.tick_top()
    ax.tick_params(axis="x", length=0, pad=1.5, labelsize=ns.TINY)
    for j, t in enumerate(cols):
        ax.text(j, len(order) - 0.15, f"{th[t]['proteins']}\n{th[t]['active_percent']}%",
                ha="center", va="top", fontsize=ns.TINY, color=ns.INK2, linespacing=1.05)
    ax.text(-0.75, len(order) - 0.15, "proteins\nactive", ha="right", va="top",
            fontsize=ns.TINY, color=ns.MUTED, linespacing=1.05, fontstyle="italic")
    ax.set_xlim(-0.5, 2.75)
    ax.set_ylim(len(order) + 1.6, -0.7)
    for s in ax.spines.values():
        s.set_visible(False)
    ax.text(1.0, -2.2, "Minimum constituents tested per protein", ha="center", va="bottom",
            fontsize=ns.TINY, color=ns.INK)
    for y0 in (6.5, 7.5, 9.5):
        ax.axhline(y0, color=ns.HAIR, lw=0.4, xmin=0.0, xmax=1.0)


def panel_e(fig):
    rows = fl.rows("np_disease_sets.csv")
    ax = ns.axes_mm(fig, 131, 57, 26, 25)
    for i, r in enumerate(rows):
        whole = "whole" in r["background"]
        M, n = (12155, 237) if whole else (391, 237)
        N, k = int(r["disease_genes"]), int(r["overlap"])
        fold = k / (n * N / M)
        p = hypergeom.sf(k - 1, M, n, N)
        assert abs(fold - float(r["fold"])) < 0.006, (r["definition"], fold)
        assert abs(p - float(r["p_hypergeometric"])) / float(r["p_hypergeometric"]) < 0.05
        colour = (ns.VERMILLION if r["definition"].startswith("literature")
                  else ns.MUTED if r["definition"].startswith("overall") else ns.BLUE)
        ax.plot([fold], [i], "o" if whole else "s", ms=3.0, mfc=colour, mec="white",
                mew=0.5, zorder=3)
        ax.text(1.05, i, f"{k}/{N}", fontsize=ns.TINY, va="center", ha="left",
                color=ns.INK, clip_on=False, transform=ax.get_yaxis_transform())
        ax.text(1.42, i, sci(float(r["p_hypergeometric"])), fontsize=ns.TINY,
                va="center", ha="left", color=ns.INK, clip_on=False,
                transform=ax.get_yaxis_transform())
    ax.axvline(1, color=ns.INK, lw=0.5, zorder=1)
    ax.set_xscale("log")
    ax.set_xlim(0.5, 8)
    ax.set_xticks([0.5, 1, 2, 4, 8], ["0.5", "1", "2", "4", "8"])
    ax.xaxis.set_minor_formatter(__import__("matplotlib").ticker.NullFormatter())
    labels = ["Genetic ≥ 0.5 (default)", "Genetic, any score", "Genetic ≥ 0.8",
              "Overall score ≥ 0.5", "Literature ≥ 0.5", "Genetic ≥ 0.5, assayed"]
    ax.set_yticks(range(len(rows)), labels)
    ax.tick_params(axis="y", length=0, pad=2, labelsize=ns.TINY)
    ax.set_ylim(len(rows) - 0.4, -0.8)
    ax.spines["left"].set_visible(False)
    ns.hairline_grid(ax, "x")
    ax.set_xlabel("Fold enrichment of disease genes")
    ax.text(1.05, -1.3, "overlap", fontsize=ns.TINY, color=ns.MUTED, ha="left",
            va="center", clip_on=False, fontstyle="italic", transform=ax.get_yaxis_transform())
    ax.text(1.42, -1.3, "P", fontsize=ns.TINY, color=ns.MUTED,
            ha="left", va="center", clip_on=False, fontstyle="italic",
            transform=ax.get_yaxis_transform())


def main():
    ns.apply()
    c = fl.counts()
    fig = ns.figure(ns.DOUBLE, 174)
    panel_a(fig, c)
    panel_b(fig, c)
    panel_e(fig)          # c: disease genes
    panel_c(fig, c)       # d: screening activity per protein
    panel_d(fig)          # e: thresholds
    for letter, x, y in (("a", 0, 1.5), ("b", 94, 1.5), ("c", 94, 49.5), ("d", 0, 74.5),
                         ("e", 94, 96.5)):
        ns.panel_label(fig, letter, x, y)
    return ns.save(fig, "Fig5")


if __name__ == "__main__":
    for p in main():
        print(p)
