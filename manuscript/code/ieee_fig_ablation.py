#!/usr/bin/env python3
"""IEEE-style plot, two columns: which layer of governance stops which scientific error.

Data: the governance ablation (BioScience-Harness/benchmarks/ablation/results.json, via
data/extracted/ablation_*.csv) and the closure rounds documented in
docs/governance-ablation.md (data/curated/ablation_rounds.csv). 121 mutants of ten outputs
a reviewer would release, each carrying one known scientific error; five gates; 14
configurations. Proportions carry 95% Wilson score intervals.

    python manuscript/code/ieee_fig_ablation.py      # -> manuscript/ieee/IEEE_Fig_Ablation.*
"""

from __future__ import annotations

import numpy as np
from matplotlib.colors import Normalize

import figlib as fl
import ieee_style as st
import nature_style as ns

W = st.TWO_COLUMN * 25.4
GATES = ["provenance", "output", "licensing", "claim_contract", "release_path"]
GATE = {"provenance": "Ingest", "output": "Output gate", "licensing": "Licensing",
        "claim_contract": "Claim contract", "release_path": "Release path"}
LAYER = {"provenance": "kernel", "output": "kernel", "licensing": "kernel",
         "claim_contract": "domain", "release_path": "domain"}
COLOUR = {"kernel": st.BLUE, "domain": st.VERMILLION}
#: The error classes in families, in English only (the figure's face has no CJK glyphs).
FAMILIES = [
    ("Scope", [("population", "Population not enrolled"),
               ("outcome", "Outcome not measured"),
               ("subject", "Intervention substituted")]),
    ("Scope, text", [("population_text", "Population not enrolled"),
                     ("outcome_text", "Outcome not measured"),
                     ("subject_text", "Intervention substituted")]),
    ("Direction, strength", [("direction", "Direction inverted"),
                             ("certainty", "Stated as proven"),
                             ("certainty_text", "Proven (text only)"),
                             ("upgrade", "Claim kind upgraded"),
                             ("borrowed_direction", "Direction no edge records")]),
    ("For patients", [("animal", "Animal study"), ("docking", "Docking prediction"),
                      ("plan", "Cure in a plan clause")]),
    ("Record integrity", [("retracted", "Retracted record"),
                          ("tampered", "Edited after signing"),
                          ("citation_mismatch", "Another paper's record"),
                          ("fabricated_citation", "Invented citation")]),
    ("TCM-specific", [("near_name", "Near-name herb"),
                      ("near_name_text", "Near-name herb (text)"),
                      ("dose", "Dose read ×10"),
                      ("processing_transfer", "Processed → raw herb"),
                      ("constituent_formula", "Constituent ↔ formula"),
                      ("exposure_text", "Bench at patient exposure"),
                      ("duplicate_source", "One trial counted twice"),
                      ("unknown_as_negative", "Unmeasured stated absent")]),
]


def panel_configurations(fig, conf):
    groups = [("none", [("ungoverned", "No gate", st.GREY, "X")]),
              ("one gate", [(f"{g} only", GATE[g], COLOUR[LAYER[g]], "o") for g in GATES]),
              ("one layer", [("kernel only", "Kernel (3 gates)", st.BLUE, "s"),
                             ("domain only", "Domain layer (2)", st.VERMILLION, "s")]),
              ("all but one", [(f"full without {g}", f"Without {GATE[g].lower()}",
                                st.BLACK, "^") for g in GATES]),
              ("all five", [("full", "Full stack", st.BLACK, "D")])]
    ax = st.axes(fig, 37.0, 2.0, 40.0, 60.0)
    y, ticks, labels, heads = 0.0, [], [], []
    for name, members in groups:
        heads.append((y, name))
        y += 0.9
        for key, text, colour, marker in members:
            c = conf[key]
            rate, lo, hi = (100 * float(c[k]) for k in ("rate", "ci_low", "ci_high"))
            ax.plot([lo, hi], [y, y], color=colour, lw=0.9, solid_capstyle="butt", zorder=2)
            ax.plot([rate], [y], marker, ms=3.8, mfc=colour, mec=colour, mew=0.5, zorder=3)
            ax.text(103.5, y, f"{c['errors_released']}/{c['mutants']}", va="center",
                    fontsize=st.TEXT, clip_on=False)
            ticks.append(y)
            labels.append(text)
            y += 1.0
        y += 0.2
    ax.set_ylim(y - 0.5, -0.75)
    ax.set_yticks(ticks, labels)
    ax.tick_params(axis="y", which="both", length=0)
    for yy, name in heads:
        ax.text(-1.0, yy + 0.05, name, ha="left", va="center", fontsize=st.TEXT,
                fontstyle="italic", color=st.DARK)
    ax.set_xlim(-3, 103)
    ax.set_xticks([0, 25, 50, 75, 100])
    ax.set_xlabel("erroneous outputs released (%)")
    st.grid(ax, "x")
    assert {c["false_refusals"] for c in conf.values()} == {"0"}
    st.subcaption(fig, 50.0, 71.5, "(a) Errors released by configuration")


def panel_unique(fig):
    uniq = {r["gate"]: int(r["unique"]) for r in fl.rows("ablation_unique.csv")}
    order = sorted(GATES, key=lambda g: -uniq[g])
    ax = st.axes(fig, 120.0, 2.0, 24.0, 24.0)
    for i, g in enumerate(order):
        ax.barh(i, uniq[g], height=0.6, color=COLOUR[LAYER[g]], lw=0)
        ax.text(uniq[g] + 1.2, i, str(uniq[g]), va="center", fontsize=st.TEXT)
    ax.set_yticks(range(len(order)), [GATE[g] for g in order])
    ax.set_ylim(len(order) - 0.5, -0.5)
    ax.set_xlim(0, 52)
    ax.set_xticks([0, 20, 40])
    ax.tick_params(axis="y", which="both", length=0)
    ax.set_xlabel("errors only it stops")
    st.grid(ax, "x")
    st.subcaption(fig, 118.0, 35.0, "(b) Errors one gate alone stops")


def panel_rounds(fig):
    rounds = fl.rows("ablation_rounds.csv")
    ax = st.axes(fig, 158.0, 2.0, 21.5, 24.0)
    xs = np.arange(len(rounds))
    ys = []
    for x, r in zip(xs, rounds):
        k, n = int(r["errors_released"]), int(r["mutants"])
        lo, hi = ns.wilson(k, n)
        ax.plot([x, x], [100 * lo, 100 * hi], color=st.BLACK, lw=0.9, zorder=2)
        ax.plot([x], [100 * k / n], "D", ms=3.6, mfc=st.BLACK, mec=st.BLACK, zorder=3)
        ax.text(x + 0.12, 100 * k / n + 2.0, f"{k}/{n}", fontsize=st.TEXT, va="bottom")
        ys.append(100 * k / n)
    ax.plot(xs, ys, color=st.BLACK, lw=0.5, zorder=1)
    ax.set_xticks(xs, ["1", "2", "3"])
    ax.set_xlim(-0.4, 3.3)
    ax.set_ylim(-2, 30)
    ax.set_yticks([0, 10, 20, 30])
    ax.set_xlabel("benchmark round")
    ax.set_ylabel("released (%)")
    st.grid(ax, "y")
    st.subcaption(fig, 166.0, 35.0, "(c) Full stack, by round")


def panel_matrix(fig, top):
    km = {r["error_class"]: r for r in fl.rows("ablation_kill_matrix.csv")}
    assert sum(len(m) for _, m in FAMILIES) == len(km) == 26
    cols = GATES + ["kernel", "domain", "full"]
    col_names = ["Ingest", "Output gate", "Licensing", "Claim contract", "Release path",
                 "Kernel", "Domain layer", "Full stack"]
    classes, spans = [], []
    for fam, members in FAMILIES:
        spans.append((len(classes), len(classes) + len(members) - 1, fam))
        classes += members
    left, cw, rh = 30.0, 5.2, 3.3
    ax = st.axes(fig, left, top, cw * len(classes), rh * len(cols))
    ax.set_xlim(-0.5, len(classes) - 0.5)
    ax.set_ylim(len(cols) - 0.5, -0.5)
    norm = Normalize(0, 1)
    cmap = ns.BLUES
    grid = np.array([[float(km[k][c]) for k, _ in classes] for c in cols])
    for i in range(len(cols)):
        for j in range(len(classes)):
            v = grid[i, j]
            colour = cmap(norm(0.06 + 0.94 * v)) if v > 0 else "white"
            ax.add_patch(__import__("matplotlib").patches.Rectangle(
                (j - 0.46, i - 0.44), 0.92, 0.88, fc=colour, ec="none"))
            if 0 < v < 1:
                ax.text(j, i, f"{100 * v:.0f}", ha="center", va="center", fontsize=st.TEXT,
                        color=ns.ink_on(colour))
    ax.set_yticks(range(len(cols)), col_names)
    for lab, c in zip(ax.get_yticklabels(), cols):
        if c in LAYER:
            lab.set_color(st.shade(COLOUR[LAYER[c]], 0.1))
    ax.axhline(4.5, color=st.BLACK, lw=0.5)
    ax.axhline(6.5, color=st.BLACK, lw=0.5)
    ax.set_xticks(range(len(classes)), [f"{t} ({km[k]['n']})" for k, t in classes],
                  rotation=90)
    ax.tick_params(which="both", length=0, top=False, right=False)
    for a, b, fam in spans:
        if a:
            ax.axvline(a - 0.5, color=st.BLACK, lw=0.5)
        ax.text((a + b) / 2, -0.75, fam, ha="center", va="bottom", fontsize=st.TEXT,
                fontweight="bold")
    # colour key
    cax = st.axes(fig, left + cw * len(classes) + 4.0, top + 1.0, 3.0, rh * len(cols) - 1.0)
    cax.imshow(np.linspace(1, 0, 256)[:, None], aspect="auto", cmap=cmap,
               norm=Normalize(-0.06 / 0.94, 1), extent=(0, 1, 0, 100))
    cax.set_xticks([])
    cax.yaxis.tick_right()
    cax.set_yticks([0, 50, 100])
    cax.tick_params(which="both", left=False, top=False, bottom=False)
    cax.set_ylabel("refused (%)", labelpad=1.5)
    cax.yaxis.set_label_position("right")
    return left + cw * len(classes) / 2


def main():
    st.apply()
    conf = {c["configuration"]: c for c in fl.rows("ablation_configurations.csv")}
    H = 158.0
    fig = st.figure(st.TWO_COLUMN, H)
    panel_configurations(fig, conf)
    panel_unique(fig)
    panel_rounds(fig)
    # a key to (a): marker, the configuration; colour, the layer of its gates
    kax = fl.canvas(fig, 98.0, 46.0, 82.0, 26.0)
    st.text(kax, 2.0, -0.6, "Key to (a)", weight="bold", va="top")
    rows = [("o", "one gate"), ("s", "one layer"), ("^", "all but one gate"),
            ("D", "all five gates")]
    for i, (m, text) in enumerate(rows):
        x, y = 4.0 + (i % 2) * 40.0, 5.6 + (i // 2) * 5.6
        kax.plot([x], [y], m, ms=3.8, mfc=st.BLACK, mec=st.BLACK)
        st.text(kax, x + 2.6, y, text)
    for i, (colour, text) in enumerate(((st.BLUE, "kernel gates (PSH)"),
                                        (st.VERMILLION, "domain-layer gates"))):
        x, y = 4.0 + i * 40.0, 18.2
        kax.plot([x - 1.8, x + 1.8], [y, y], color=colour, lw=0.9)
        kax.plot([x], [y], "o", ms=3.8, mfc=colour, mec=colour)
        st.text(kax, x + 2.6, y, text)
    st.text(kax, 2.0, 24.0, "bars, 95% Wilson intervals; n = 121 mutants per configuration",
            style="italic", color=st.DARK)
    top = 83.0
    cx = panel_matrix(fig, top)
    st.subcaption(fig, cx, H - 4.5, "(d) Share of each error class's mutants refused, "
                  "by gate, by layer and by the full stack")
    return st.save(fig, "IEEE_Fig_Ablation")


if __name__ == "__main__":
    for p in main():
        print(p)
