#!/usr/bin/env python3
"""IEEE-style plot, two columns: how well the rules read study designs from real abstracts.

Data: the evidence-typing benchmark (BioScience-Harness/benchmarks/evidence_typing/
results.json, which holds both versions of the rules), via
data/extracted/evidence_typing_*.csv. 478 open-access abstracts split by a seeded hash of the
PMID; the rules were revised on the dev split (176) and the test split (302) was read once
by both versions. The reference is PubMed's publication type. Proportions carry 95% Wilson
intervals.

    python manuscript/code/ieee_fig_evidence_typing.py   # -> manuscript/ieee/IEEE_Fig_EvidenceTyping.*
"""

from __future__ import annotations

import matplotlib.patches as mpatches
from matplotlib.lines import Line2D

import figlib as fl
import ieee_style as st
import nature_style as ns

W = st.TWO_COLUMN * 25.4
DESIGNS = ["systematic_review", "randomized_trial", "observational", "case_report",
           "animal", "in_vitro"]
NAME = {"systematic_review": "Systematic review", "randomized_trial": "Randomized trial",
        "observational": "Observational", "case_report": "Case report", "animal": "Animal",
        "in_vitro": "In vitro", "narrative_review": "Narrative review",
        "editorial": "Editorial", "protocol": "Trial protocol"}
BEFORE, AFTER = st.GREY, st.BLUE


def panel_confusion(fig, top):
    conf = fl.rows("evidence_typing_confusion_test.csv")
    cols = DESIGNS + ["ambiguous", "no rule"]
    col_names = [NAME[c] for c in DESIGNS] + ["Ambiguous", "No rule"]
    left, cw, rh = 30.0, 6.3, 4.6
    ax = st.axes(fig, left, top, cw * len(cols), rh * len(conf))
    ax.set_xlim(-0.5, len(cols) - 0.5)
    ax.set_ylim(len(conf) - 0.5, -0.5)
    ax.tick_params(which="both", length=0, top=False, right=False, bottom=False, left=False)
    for i, r in enumerate(conf):
        ref = r["reference"]
        total = sum(int(r[c]) for c in cols)
        for j, c in enumerate(cols):
            v = int(r[c])
            colour = ns.BLUES(0.08 + 0.92 * v / total) if v else "white"
            ax.add_patch(mpatches.Rectangle((j - 0.47, i - 0.45), 0.94, 0.9, fc=colour,
                                            ec="none"))
            correct = (c == ref) if ref in DESIGNS else c in ("ambiguous", "no rule")
            if correct:
                ax.add_patch(mpatches.Rectangle((j - 0.47, i - 0.45), 0.94, 0.9, fc="none",
                                                ec=st.BLACK, lw=0.9, zorder=3))
            if v:
                ax.text(j, i, str(v), ha="center", va="center", fontsize=st.TEXT,
                        color=ns.ink_on(colour))
        ax.text(len(cols) - 0.3, i, str(total), ha="left", va="center", fontsize=st.TEXT)
    ax.text(len(cols) - 0.3, -0.75, "n", ha="left", va="bottom", fontsize=st.TEXT,
            fontstyle="italic")
    ax.set_yticks(range(len(conf)), [NAME[r["reference"]] for r in conf])
    ax.set_xticks(range(len(cols)), col_names, rotation=60, ha="left",
                  rotation_mode="anchor")
    ax.xaxis.tick_top()
    ax.axhline(5.5, color=st.BLACK, lw=0.5)
    ax.axvline(5.5, color=st.BLACK, lw=0.5)
    ax.set_ylabel("PubMed publication type")
    return left + cw * len(cols) / 2


def _interval(ax, y, r, colour, filled):
    rate, lo, hi = (100 * float(r[k]) for k in ("rate", "ci_low", "ci_high"))
    ax.plot([lo, hi], [y, y], color=colour, lw=0.9, solid_capstyle="butt", zorder=2)
    ax.plot([rate], [y], "o", ms=3.8, mfc=colour if filled else "white", mec=colour,
            mew=0.9, zorder=3)


def panel_designs(fig, top, height):
    get = {(r["version"], r["split"], r["design"], r["metric"]): r
           for r in fl.rows("evidence_typing_designs.csv")}
    for k, (metric, title) in enumerate((("precision", "precision (%)"),
                                         ("recall", "recall (%)"))):
        ax = st.axes(fig, 122.0 + k * 28.0, top, 24.0, height)
        for i, design in enumerate(DESIGNS):
            _interval(ax, i - 0.17, get[("before", "test", design, metric)], BEFORE, False)
            _interval(ax, i + 0.17, get[("after", "test", design, metric)], AFTER, True)
        ax.set_ylim(len(DESIGNS) - 0.5, -0.5)
        ax.set_yticks(range(len(DESIGNS)), [NAME[d] for d in DESIGNS] if k == 0 else [])
        ax.tick_params(axis="y", which="both", length=0)
        ax.set_xlim(-3, 105)
        ax.set_xticks([0, 50, 100])
        ax.set_xlabel(title)
        st.grid(ax, "x")
    return 122.0 + 26.0


def panel_overall(fig, top):
    o = {(r["version"], r["split"], r["metric"]): r
         for r in fl.rows("evidence_typing_overall.csv")}
    metrics = [("precision", "Readings correct"), ("coverage", "Studies typed"),
               ("negatives_typed", "Negatives typed")]
    ax = st.axes(fig, 122.0, top, 52.0, 22.0)
    for i, (metric, _) in enumerate(metrics):
        for split, dy, marker in (("test", -0.16, "o"), ("dev", 0.16, "s")):
            b, a = o[("before", split, metric)], o[("after", split, metric)]
            xb, xa = 100 * float(b["rate"]), 100 * float(a["rate"])
            ax.annotate("", xy=(xa, i + dy), xytext=(xb, i + dy),
                        arrowprops=dict(arrowstyle="-|>,head_length=0.35,head_width=0.18",
                                        color=st.DARK, lw=0.6, shrinkA=2.5, shrinkB=2.5))
            ax.plot([xb], [i + dy], marker, ms=3.6, mfc="white", mec=BEFORE, mew=0.9,
                    zorder=3)
            ax.plot([xa], [i + dy], marker, ms=3.6, mfc=AFTER, mec=AFTER, zorder=3)
    ax.set_ylim(len(metrics) - 0.5, -0.5)
    ax.set_yticks(range(len(metrics)), [m for _, m in metrics])
    ax.tick_params(axis="y", which="both", length=0)
    ax.set_xlim(-3, 103)
    ax.set_xticks([0, 25, 50, 75, 100])
    ax.set_xlabel("share (%)")
    st.grid(ax, "x")


def main():
    st.apply()
    H = 126.0
    fig = st.figure(st.TWO_COLUMN, H)
    top = 24.0
    ca = panel_confusion(fig, top)
    st.subcaption(fig, ca - 10.0, top + 9 * 4.6 + 6.0,
                  "(a) Test split: reference against the rules' reading")
    cb = panel_designs(fig, top, 41.4)
    st.subcaption(fig, cb, top + 41.4 + 11.0, "(b) Each design, test split")
    panel_overall(fig, 86.0)
    st.subcaption(fig, cb, 86.0 + 22.0 + 11.0, "(c) Overall, test and dev")
    # one key for (b) and (c)
    kax = fl.canvas(fig, 6.0, 84.0, 84.0, 30.0)
    items = [(Line2D([], [], color=BEFORE, marker="o", mfc="white", mec=BEFORE, ms=3.8,
                     lw=0.9), "rules before revision"),
             (Line2D([], [], color=AFTER, marker="o", mfc=AFTER, mec=AFTER, ms=3.8, lw=0.9),
              "rules after revision on dev"),
             (Line2D([], [], color="none", marker="o", mfc=st.GREY, mec=st.GREY, ms=3.8),
              "test split, 302 abstracts (c)"),
             (Line2D([], [], color="none", marker="s", mfc=st.GREY, mec=st.GREY, ms=3.6),
              "dev split, 176 abstracts (c)")]
    kax.legend(handles=[h for h, _ in items], labels=[t for _, t in items],
               loc="upper left", title="Key to (b) and (c)", title_fontsize=st.TEXT,
               frameon=True)
    st.text(kax, 1.0, 27.5, "(a): outlined, a correct reading; for the negatives, abstaining",
            style="italic", color=st.DARK)
    return st.save(fig, "IEEE_Fig_EvidenceTyping")


if __name__ == "__main__":
    for p in main():
        print(p)
