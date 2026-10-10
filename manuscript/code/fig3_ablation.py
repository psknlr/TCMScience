#!/usr/bin/env python3
"""Fig. 3 | The governance ablation: which layer stops which scientific error.

Reads data/extracted/ablation_*.csv (BioScience-Harness/benchmarks/ablation/results.json)
and data/curated/ablation_rounds.csv.
"""

from __future__ import annotations

import numpy as np
from matplotlib.colors import Normalize

import figlib as fl
import nature_style as ns

GATES = ["provenance", "output", "licensing", "claim_contract", "release_path"]
GATE_NAME = {"provenance": "Ingest", "output": "Output gate", "licensing": "Licensing",
             "claim_contract": "Claim contract", "release_path": "Release path"}
LAYER = {"provenance": "kernel", "output": "kernel", "licensing": "kernel",
         "claim_contract": "domain", "release_path": "domain"}
LAYER_COLOUR = {"kernel": ns.BLUE, "domain": ns.VERMILLION}

#: Error classes, in families, with the label a reader needs.
FAMILIES = [
    ("Scope of the finding", [
        ("population", "Population not enrolled"),
        ("outcome", "Outcome not measured"),
        ("subject", "Intervention substituted")]),
    ("Same errors, in the text only", [
        ("population_text", "Population not enrolled"),
        ("outcome_text", "Outcome not measured"),
        ("subject_text", "Intervention substituted")]),
    ("Direction and strength", [
        ("direction", "Direction inverted"),
        ("certainty", "Stated as proven or curative"),
        ("certainty_text", "Stated as proven (text only)"),
        ("upgrade", "Claim kind upgraded"),
        ("borrowed_direction", "Direction no edge records")]),
    ("Evidence cited for patients", [
        ("animal", "Animal study"),
        ("docking", "Docking prediction"),
        ("plan", "Cure hidden in a plan clause")]),
    ("Record integrity", [
        ("retracted", "Retracted record"),
        ("tampered", "Record edited after signing"),
        ("citation_mismatch", "Another paper's record"),
        ("fabricated_citation", "Invented citation")]),
    ("TCM-specific", [
        ("near_name", "Near-name herb (制白附子 for 制附子)"),
        ("near_name_text", "Near-name herb (text only)"),
        ("dose", "Dose read ten times too high"),
        ("processing_transfer", "制附子 evidence used for 生附子"),
        ("constituent_formula", "Constituent ↔ formula"),
        ("exposure_text", "Bench result at patient exposure"),
        ("duplicate_source", "One trial counted twice"),
        ("unknown_as_negative", "Unmeasured outcome stated absent")]),
]


def panel_a(fig):
    ax = fl.canvas(fig, 4, 5, 84, 60)
    base = fl.rows("ablation_base_cases.csv")
    design = {"randomized_trial": "RCT", "observational": "Cohort", "case_report": "Case",
              "in_vitro": "Bench", "docking": "Dock", "classical_text": "伤寒论"}
    fl.label(ax, 0, 1.2, "10 base outputs a reviewer would release", weight="bold")
    w, gap = 7.4, 0.95
    for i, c in enumerate(base):
        x = i * (w + gap)
        fl.box(ax, x, 3.3, w, 5.2, c["case"], fc=ns.WASH, ec=ns.RULE, lw=0.4,
               size=ns.SMALL, weight="bold")
        fl.label(ax, x + w / 2, 10.0, design[c["source_design"]], size=ns.TINY, ha="center",
                 color=ns.INK2)
        fl.label(ax, x + w / 2, 12.3, c["language"], size=ns.TINY, ha="center",
                 color=ns.MUTED)
    fl.arrow(ax, 40, 14.0, 40, 17.4)
    fl.box(ax, 0, 17.8, 83.5, 15.4, fc="white", ec=ns.INK2, lw=0.5)
    fl.label(ax, 1.6, 20.2, "26 mutation operators, one known scientific error each",
             weight="bold")
    examples = ("population or outcome the study lacked · direction inverted · stated as "
                "proven\nanimal study or docking cited for patients · retracted, tampered, "
                "mismatched\nor invented record · 制白附子 for 制附子 · dose ×10 · one trial "
                "counted twice\n“no adverse reactions” or 无毒 over an outcome nobody measured")
    fl.label(ax, 1.6, 26.6, examples, size=ns.TINY, color=ns.INK2, va="center",
             linespacing=1.3)
    fl.label(ax, 82, 20.2, "121 mutants", weight="bold", ha="right")
    fl.label(ax, 82, 23.0, "73 zh · 48 en", size=ns.TINY, ha="right", color=ns.INK2)
    fl.arrow(ax, 40, 33.4, 40, 36.8)
    gates = [("provenance", "Ingest", "signature,\nidentifier"),
             ("output", "Output gate", "per-sentence\nsupport"),
             ("licensing", "Licensing", "design × claim\n(TYP)"),
             ("claim_contract", "Claim contract", "CLM001–\nCLM019"),
             ("release_path", "Release path", "snapshot\nedges")]
    gw, gg = 15.2, 1.3
    xs = [i * (gw + gg) for i in range(5)]
    xs[3] += 1.0
    xs[4] += 1.0
    for (g, name, what), x in zip(gates, xs):
        colour = LAYER_COLOUR[LAYER[g]]
        fl.box(ax, x, 37.2, gw, 10.2, fc=ns.tint(colour, 0.10), ec=colour, lw=0.6)
        fl.label(ax, x + gw / 2, 39.7, name, size=ns.SMALL, weight="bold", ha="center")
        fl.label(ax, x + gw / 2, 44.2, what, size=ns.TINY, ha="center", color=ns.INK2,
                 linespacing=1.1)
    for i in range(2):
        fl.arrow(ax, xs[i] + gw + 0.1, 42.3, xs[i + 1] - 0.1, 42.3, head=1.6, lw=0.5)
    fl.arrow(ax, xs[3] + gw + 0.1, 42.3, xs[4] - 0.1, 42.3, head=1.6, lw=0.5)
    k0, k1 = xs[0], xs[2] + gw
    d0, d1 = xs[3], xs[4] + gw
    for (a, b, text, colour) in ((k0, k1, "Kernel (PSH)", ns.BLUE),
                                 (d0, d1, "Domain layer", ns.VERMILLION)):
        ax.plot([a, a, b, b], [49.0, 49.8, 49.8, 49.0], color=colour, lw=0.6)
        fl.label(ax, (a + b) / 2, 51.6, text, size=ns.SMALL, ha="center", color=ns.INK,
                 weight="bold")
    fl.label(ax, 0, 56.6, "An output is released if no enabled gate refuses it; 14 "
             "configurations,\nread off one run of every gate on every case.",
             size=ns.TINY, color=ns.INK2, linespacing=1.25)


def panel_b(fig):
    conf = {c["configuration"]: c for c in fl.rows("ablation_configurations.csv")}
    groups = [
        ("No governance", [("ungoverned", "No gate", ns.MUTED)]),
        ("One gate", [(f"{g} only", GATE_NAME[g], LAYER_COLOUR[LAYER[g]]) for g in GATES]),
        ("One layer", [("kernel only", "Kernel (3 gates)", ns.BLUE),
                       ("domain only", "Domain layer (2 gates)", ns.VERMILLION)]),
        ("All but one", [(f"full without {g}", f"Without {GATE_NAME[g].lower()}", ns.INK)
                         for g in GATES]),
        ("Full stack", [("full", "All five gates", ns.INK)]),
    ]
    ax = ns.axes_mm(fig, 122, 8, 44, 53)
    y = 0
    ticks, labels, heads = [], [], []
    for name, members in groups:
        heads.append((y - 0.05, name))
        y += 0.95
        for key, text, colour in members:
            c = conf[key]
            rate, lo, hi = (100 * float(c[k]) for k in ("rate", "ci_low", "ci_high"))
            ax.plot([lo, hi], [y, y], color=colour, lw=0.7, solid_capstyle="butt", zorder=2)
            ax.plot([rate], [y], "o", ms=3.0, mfc=colour, mec="white", mew=0.5, zorder=3)
            ax.text(106, y, f"{c['errors_released']}/{c['mutants']}", fontsize=ns.TINY,
                    va="center", ha="left", color=ns.INK, clip_on=False)
            ticks.append(y)
            labels.append(text)
            y += 1
        y += 0.35
    ax.set_ylim(y - 0.2, -0.6)
    ax.set_yticks(ticks, labels)
    ax.tick_params(axis="y", length=0, pad=2, labelsize=ns.SMALL)
    for yy, name in heads:
        ax.text(-0.035, yy, name, transform=ax.get_yaxis_transform(), ha="right",
                va="center", fontsize=ns.TINY, color=ns.INK2, fontstyle="italic",
                clip_on=False)
    ax.set_xlim(-2, 103)
    ax.set_xticks([0, 25, 50, 75, 100])
    ax.set_xlabel("Erroneous outputs released (%)")
    ns.hairline_grid(ax, "x")
    ax.spines["left"].set_visible(False)
    ax.text(106, -0.05, "k/n", fontsize=ns.TINY, color=ns.MUTED, ha="left", va="center",
            clip_on=False)
    fp = {c["false_refusals"] for c in conf.values()}
    assert fp == {"0"}, fp
    ax.text(-0.035, -0.118, "Correct outputs refused:\n0/10 in every configuration",
            transform=ax.transAxes, fontsize=ns.TINY, color=ns.INK2, ha="right", va="top",
            linespacing=1.2)


def panel_c(fig):
    km = {r["error_class"]: r for r in fl.rows("ablation_kill_matrix.csv")}
    assert sum(len(m) for _, m in FAMILIES) == len(km) == 26
    cols = GATES + ["kernel", "domain", "full"]
    col_names = ["Ingest", "Output\ngate", "Licens-\ning", "Claim\ncontract", "Release\npath",
                 "Kernel", "Domain", "Full\nstack"]
    rows, ylabels, heads = [], [], []
    for fam, members in FAMILIES:
        heads.append((len(rows), fam))
        rows.append(None)
        for key, text in members:
            rows.append(key)
            ylabels.append((len(rows) - 1, f"{text} ({km[key]['n']})"))
    left, top, cw, rh = 41.5, 84.0, 6.1, 2.42
    xs = [0, 1, 2, 3, 4, 5.3, 6.3, 7.6]
    width = (xs[-1] + 1) * cw
    ax = ns.axes_mm(fig, left, top, width, rh * len(rows))
    ax.set_xlim(0, xs[-1] + 1)
    ax.set_ylim(len(rows), 0)
    ax.set_axis_off()
    norm = Normalize(0, 1)
    for i, key in enumerate(rows):
        if key is None:
            continue
        for x, col in zip(xs, cols):
            v = float(km[key][col])
            colour = ns.BLUES(norm(0.06 + 0.94 * v)) if v > 0 else "white"
            ax.add_patch(__import__("matplotlib").patches.Rectangle(
                (x + 0.04, i + 0.06), 0.92, 0.88, fc=colour, ec=ns.HAIR if v == 0 else "none",
                lw=0.3))
            if 0 < v < 1:
                ax.text(x + 0.5, i + 0.52, f"{100 * v:.0f}", ha="center", va="center",
                        fontsize=ns.TINY, color=ns.ink_on(colour))
    for i, text in ylabels:
        ax.text(-0.25, i + 0.52, text, ha="right", va="center", fontsize=ns.TINY,
                color=ns.INK)
    for i, fam in heads:
        ax.text(-0.25, i + 0.62, fam, ha="right", va="center", fontsize=ns.TINY,
                fontweight="bold", color=ns.INK)
        if i:
            ax.plot([-6.6, xs[-1] + 1], [i, i], color=ns.HAIR, lw=0.4, clip_on=False)
    for x, name in zip(xs, col_names):
        ax.text(x + 0.5, -0.5, name, ha="center", va="bottom", fontsize=ns.TINY,
                color=ns.INK, linespacing=1.0)
    for x, col in zip(xs[:5], GATES):
        ax.add_patch(__import__("matplotlib").patches.Rectangle(
            (x + 0.08, -0.32), 0.84, 0.2, fc=LAYER_COLOUR[LAYER[col]], ec="none",
            clip_on=False))
    for x, colour in ((xs[5], ns.BLUE), (xs[6], ns.VERMILLION)):
        ax.add_patch(__import__("matplotlib").patches.Rectangle(
            (x + 0.08, -0.32), 0.84, 0.2, fc=colour, ec="none", clip_on=False))
    ax.text(2.5, -3.0, "Single gates", ha="center", va="bottom", fontsize=ns.TINY,
            color=ns.INK2, fontstyle="italic")
    ax.text(6.3, -3.0, "Layers", ha="center", va="bottom", fontsize=ns.TINY,
            color=ns.INK2, fontstyle="italic")
    # colour key
    cax = ns.axes_mm(fig, left + width - 26, top + rh * len(rows) + 3.0, 26, 1.8)
    grad = np.linspace(0, 1, 256)[None, :]
    cax.imshow(grad, aspect="auto", cmap=ns.BLUES, extent=(0, 100, 0, 1),
               norm=Normalize(-0.06 / 0.94, 1))
    cax.set_yticks([])
    cax.set_xticks([0, 50, 100])
    cax.tick_params(axis="x", labelsize=ns.TINY, length=1.5, pad=1)
    for s in cax.spines.values():
        s.set_visible(False)
    fig.text((left + width - 27.5) * ns.MM / fig.get_size_inches()[0],
             1 - (top + rh * len(rows) + 3.9) * ns.MM / fig.get_size_inches()[1],
             "Mutants refused (%)", ha="right", va="center", fontsize=ns.TINY, color=ns.INK)


def panel_d(fig):
    uniq = {r["gate"]: int(r["unique"]) for r in fl.rows("ablation_unique.csv")}
    order = sorted(GATES, key=lambda g: -uniq[g])
    ax = ns.axes_mm(fig, 146, 84, 30, 27)
    for i, g in enumerate(order):
        ax.barh(i, uniq[g], height=0.56, color=LAYER_COLOUR[LAYER[g]], lw=0)
        ax.text(uniq[g] + 1.0, i, str(uniq[g]), va="center", ha="left", fontsize=ns.TINY,
                color=ns.INK)
    ax.set_yticks(range(len(order)), [GATE_NAME[g] for g in order])
    ax.set_ylim(len(order) - 0.45, -0.55)
    ax.set_xlim(0, 48)
    ax.set_xticks([0, 20, 40])
    ax.tick_params(axis="y", length=0, pad=2)
    ax.spines["left"].set_visible(False)
    ax.set_xlabel("Errors released only when\nthis gate is switched off", linespacing=1.1)
    ns.hairline_grid(ax, "x")


def panel_e(fig):
    rounds = fl.rows("ablation_rounds.csv")
    ax = ns.axes_mm(fig, 146, 133, 30, 21)
    xs = np.arange(len(rounds))
    for x, r in zip(xs, rounds):
        k, n = int(r["errors_released"]), int(r["mutants"])
        lo, hi = ns.wilson(k, n)
        ax.plot([x, x], [100 * lo, 100 * hi], color=ns.INK, lw=0.7, zorder=2)
        ax.plot([x], [100 * k / n], "o", ms=3.0, mfc=ns.INK, mec="white", mew=0.5, zorder=3)
        ax.text(x + 0.12, 100 * k / n + 2.2, f"{k}/{n}", fontsize=ns.TINY, va="bottom",
                ha="left", color=ns.INK)
    ax.plot(xs, [100 * int(r["errors_released"]) / int(r["mutants"]) for r in rounds],
            color=ns.INK, lw=0.5, zorder=1)
    ax.set_xticks(xs, ["Round 1", "After\nround 1", "After\nround 2"], linespacing=1.0)
    ax.set_xlim(-0.4, len(rounds) - 0.25)
    ax.set_ylim(-1, 30)
    ax.set_yticks([0, 10, 20, 30])
    ax.set_ylabel("Released by\nfull stack (%)", linespacing=1.1)
    ns.hairline_grid(ax, "y")


def main():
    ns.apply()
    fig = ns.figure(ns.DOUBLE, 170)
    panel_a(fig)
    panel_b(fig)
    panel_c(fig)
    panel_d(fig)
    panel_e(fig)
    for letter, x, y in (("a", 0, 1.5), ("b", 92, 1.5), ("c", 0, 74.5), ("d", 122, 74.5),
                         ("e", 122, 124.5)):
        ns.panel_label(fig, letter, x, y)
    return ns.save(fig, "Fig3")


if __name__ == "__main__":
    for p in main():
        print(p)
