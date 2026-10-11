#!/usr/bin/env python3
"""IEEE-style plot, two columns: the Gegen Qinlian decoction case study on public data.

The counts are those the repository documents (data/curated/np_*.csv, each value checked
against the line it cites when the data are extracted). Fold enrichments and one-sided
hypergeometric P values are recomputed here from the counts and must equal the reported
ones; proportions carry 95% Wilson intervals.

    python manuscript/code/ieee_fig_case_study.py    # -> manuscript/ieee/IEEE_Fig_CaseStudy.*
"""

from __future__ import annotations

import matplotlib.ticker as mticker
from scipy.stats import hypergeom

import figlib as fl
import ieee_style as st
import nature_style as ns

W = st.TWO_COLUMN * 25.4
FAMILY = [("CYP450", "Cytochrome P450: uniform Tox21 and qHTS panels", st.VERMILLION, "o"),
          ("Nuclear receptor", "Nuclear receptors: uniform panels", st.BLUE, "s"),
          ("Carbonic anhydrase", "Carbonic anhydrases: literature sets chosen for actives",
           st.GREEN, "^")]


def sci(p: float) -> str:
    if p >= 0.01:
        return f"{p:.2f}"
    if p >= 0.001:
        return f"{p:.3f}"
    m, e = f"{p:.1e}".split("e")
    return f"{m}×10$^{{{int(e)}}}$"


def panel_backgrounds(fig, c, top):
    bgs = [("whole", c["reactome_background"], c["reactome_pathways_tested"],
            c["reactome_significant"]),
           ("assayed", c["assayed_background"], c["assayed_pathways_tested"],
            c["assayed_significant"])]
    ax = st.axes(fig, 15.0, top, 24.0, 34.0)
    for i, (name, prot, tested, sig) in enumerate(bgs):
        ax.bar(i, sig, width=0.55, color=st.BLUE if sig else st.LIGHT, lw=0)
        ax.text(i, sig + 2.0, f"{sig:.0f}\nof {tested:,.0f}", ha="center", va="bottom",
                fontsize=st.TEXT, linespacing=1.0)
    ax.set_xticks([0, 1], [b[0] for b in bgs])
    ax.tick_params(axis="x", which="both", length=0)
    ax.set_xlim(-0.6, 1.6)
    ax.set_ylim(0, 90)
    ax.set_yticks([0, 20, 40, 60, 80])
    ax.set_ylabel("pathways significant")
    ax.set_xlabel("background")
    st.grid(ax, "y")
    return bgs


def panel_ca(fig, c, bgs, top):
    members, hits = c["ca_pathway_members"], c["ca_pathway_hits"]
    ax = st.axes(fig, 62.0, top, 30.0, 34.0)
    for i, (name, prot, _, _) in enumerate(bgs):
        expected = members * c["assayed_hits"] / prot
        fold = hits / expected
        ax.bar(i, fold, width=0.55, color=st.VERMILLION, lw=0)
        if fold > 3:
            ax.text(i, fold * 1.15, f"{fold:.1f}×", ha="center", va="bottom",
                    fontsize=st.TEXT)
        else:
            ax.text(i, (fold * 0.5) ** 0.5, f"{fold:.1f}×", ha="center", va="center",
                    fontsize=st.TEXT, color="white")
    cap = c["assayed_background"] / c["assayed_hits"]
    assert abs(cap - c["max_fold_assayed"]) < 0.01, cap
    ax.plot([0.6, 1.4], [cap, cap], color=st.BLACK, lw=0.75, ls=(0, (3.0, 2.0)))
    ax.text(1.45, cap, f"ceiling\n{cap:.2f}×", va="center", fontsize=st.TEXT,
            fontstyle="italic", linespacing=1.0)
    ax.set_yscale("log")
    ax.set_ylim(0.5, 300)
    ax.set_yticks([1, 10, 100], ["1", "10", "100"])
    ax.yaxis.set_minor_formatter(mticker.NullFormatter())
    ax.set_xticks([0, 1], [b[0] for b in bgs])
    ax.tick_params(axis="x", which="both", length=0)
    ax.set_xlim(-0.6, 2.45)
    ax.set_ylabel("fold enrichment")
    ax.set_xlabel("background")
    st.grid(ax, "y")


def panel_disease(fig, top):
    rows = fl.rows("np_disease_sets.csv")
    labels = ["genetic ≥ 0.5 (default)", "genetic, any score", "genetic ≥ 0.8",
              "overall score ≥ 0.5", "literature ≥ 0.5", "genetic ≥ 0.5, assayed"]
    ax = st.axes(fig, 132.0, top, 26.0, 34.0)
    for i, r in enumerate(rows):
        whole = "whole" in r["background"]
        M = 12155 if whole else 391
        n, N, k = 237, int(r["disease_genes"]), int(r["overlap"])
        fold = k / (n * N / M)
        p = hypergeom.sf(k - 1, M, n, N)
        assert abs(fold - float(r["fold"])) < 0.006, (r["definition"], fold)
        assert abs(p - float(r["p_hypergeometric"])) / float(r["p_hypergeometric"]) < 0.05
        colour = (st.VERMILLION if r["definition"].startswith("literature")
                  else st.GREY if r["definition"].startswith("overall") else st.BLUE)
        ax.plot([fold], [i], "o" if whole else "s", ms=3.8, mfc=colour, mec=colour, zorder=3)
        ax.text(9.5, i, sci(float(r["p_hypergeometric"])), va="center", fontsize=st.TEXT,
                clip_on=False)
    ax.axvline(1, color=st.BLACK, lw=0.5)
    ax.set_xscale("log")
    ax.set_xlim(0.5, 8)
    ax.set_xticks([0.5, 1, 2, 4, 8], ["0.5", "1", "2", "4", "8"])
    ax.xaxis.set_minor_formatter(mticker.NullFormatter())
    ax.set_yticks(range(len(rows)), labels)
    ax.tick_params(axis="y", which="both", length=0)
    ax.set_ylim(len(rows) - 0.5, -0.5)
    ax.text(9.5, -0.95, "P", fontsize=st.TEXT, fontstyle="italic", va="center",
            clip_on=False)
    ax.set_xlabel("fold enrichment")
    st.grid(ax, "x")


def panel_screening(fig, c, top):
    prots = fl.rows("np_screening_proteins.csv")
    ax = st.axes(fig, 30.0, top, 110.0, 50.0)
    y, ticks, labels = 0.0, [], []
    for fam, title, colour, marker in FAMILY:
        ax.text(1.0, y + 0.05, title, fontsize=st.TEXT, fontweight="bold", va="center")
        y += 1.0
        for r in [p for p in prots if p["family"] == fam]:
            k, n = int(r["active"]), int(r["tested"])
            lo, hi = ns.wilson(k, n)
            ax.plot([100 * lo, 100 * hi], [y, y], color=colour, lw=0.9, zorder=2)
            ax.plot([100 * k / n], [y], marker, ms=3.8, mfc=colour, mec=colour, zorder=3)
            ax.text(105.0, y, f"{k}/{n}", va="center", fontsize=st.TEXT, clip_on=False)
            ticks.append(y)
            labels.append(r["protein"])
            y += 1.0
        y += 0.3
    overall = 100 * c["screening_active"] / c["screening_tests"]
    ax.axvline(overall, color=st.BLACK, lw=0.75, ls=(0, (3.0, 2.0)), zorder=1)
    nr_header = 1.0 + sum(1 for p in prots if p["family"] == "CYP450") + 0.3
    ax.text(50.0, nr_header + 0.05, f"dashed: all {c['screening_proteins']:.0f} proteins "
            f"tested, {overall:.1f}% active", fontsize=st.TEXT, va="center",
            fontstyle="italic")
    ax.set_yticks(ticks, labels)
    ax.tick_params(axis="y", which="both", length=0)
    ax.set_ylim(y - 0.2, -0.6)
    ax.set_xlim(0, 103)
    ax.set_xticks([0, 25, 50, 75, 100])
    ax.set_xlabel("constituents active among those tested (%)")
    st.grid(ax, "x")


def main():
    st.apply()
    c = fl.counts()
    H = 133.0
    fig = st.figure(st.TWO_COLUMN, H)
    top = 6.0
    bgs = panel_backgrounds(fig, c, top)
    panel_ca(fig, c, bgs, top)
    panel_disease(fig, top)
    for x, text in ((27.0, "(a) Pathways significant"),
                    (79.0, "(b) Carbonic-anhydrase pathway"),
                    (128.0, "(c) Type 2 diabetes genes")):
        st.subcaption(fig, x, top + 34.0 + 12.5, text)
    panel_screening(fig, c, 66.0)
    st.subcaption(fig, 85.0, 66.0 + 50.0 + 12.0,
                  "(d) Activity in the screening data, by protein")
    return st.save(fig, "IEEE_Fig_CaseStudy")


if __name__ == "__main__":
    for p in main():
        print(p)
