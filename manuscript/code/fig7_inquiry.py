#!/usr/bin/env python3
"""Fig. 7 | Deciding what to find out next: the inquiry engine on three planted worlds.

Reads data/extracted/inquiry_*.csv and inquiry_design.json, produced by re-running
BioScience-Harness/scripts/run_inquiry.py --planted all and replaying each hash-chained
trail through psh.scientist.inquiry (code/extract_data.py), so every expected gain shown is
the engine's own number at that decision.
"""

from __future__ import annotations

import matplotlib.patches as mpatches
import numpy as np
from matplotlib.colors import LogNorm

import figlib as fl
import nature_style as ns

EXPL = [("target", "Selective action", ns.BLUE, "-"),
        ("coverage", "Assay coverage", ns.VERMILLION, "-"),
        ("promiscuity", "Promiscuous chemistry", ns.GREEN, "-"),
        ("catch-all", "None of these", ns.MUTED, (0, (2, 1.4)))]
ANALYSIS = {"annotated": "Whole annotation", "assayed": "Assayed background",
            "assayed-seed": "Assayed, reseeded", "random-herbs": "Random herbs",
            "random-herbs-2": "Random herbs, 2nd draw", "rewired": "Rewired network",
            "rewired-2": "Rewired, 2nd draw"}
WORLDS = [("selective", "Selective world"), ("coverage", "Coverage world"),
          ("promiscuous", "Promiscuous world")]


def panel_a(fig):
    ax = fl.canvas(fig, 3, 5, 86, 58)
    fl.box(ax, 0, 0, 30, 57, fc=ns.WASH, ec=ns.RULE, lw=0.4)
    fl.label(ax, 15, 3.2, "The model proposes", ha="center", weight="bold")
    items = ["rival explanations,\nand a catch-all with a\nprior floor (≥ 0.05)",
             "the analyses, their cost\nin network-pharmacology\nruns, and their outcomes",
             "P(outcome | explanation)\nfor every analysis,\nsealed before it runs"]
    y = 8.0
    for text in items:
        fl.box(ax, 2, y, 26, 13.6, fc="white", ec=ns.RULE, lw=0.4)
        fl.label(ax, 15, y + 6.8, text, size=ns.TINY, ha="center", linespacing=1.2)
        y += 16.0
    fl.box(ax, 36, 0, 50, 57, fc=ns.tint(ns.BLUE, 0.07), ec=ns.BLUE, lw=0.5)
    fl.label(ax, 61, 3.2, "The kernel decides", ha="center", weight="bold")
    rules = [("Next analysis", "the most expected bits per run"),
             ("Belief", "Bayes on the sealed predictions only;\na late or edited prediction is "
                        "refused"),
             ("Licensing", "a design that cannot license a claim\nkind cannot move belief in "
                           "it"),
             ("Stopping", "posterior ≥ 0.95 and a severe test\nagainst every live rival, "
                          "replicated"),
             ("Conclusion", "capped by the licensing table,\nnot by the posterior")]
    y = 7.6
    for i, (head, body) in enumerate(rules, start=1):
        ax.add_patch(mpatches.Circle((40.2, y + 1.6), 1.7, fc=ns.BLUE, ec="none", zorder=3))
        fl.label(ax, 40.2, y + 1.65, str(i), size=ns.TINY, ha="center", color="white",
                 weight="bold")
        fl.label(ax, 43.2, y + 1.6, head, size=ns.TINY, weight="bold")
        fl.label(ax, 43.2, y + 3.9, body, size=ns.TINY, va="top", color=ns.INK2,
                 linespacing=1.15)
        y += 9.6
    fl.arrow(ax, 30.4, 28.5, 35.6, 28.5, lw=0.7)


def panel_b(fig):
    pred = fl.rows("inquiry_predictions.csv")
    opts = [r for r in fl.rows("inquiry_options.csv")
            if r["world"] == "selective" and r["decision"] == "0"]
    gain = {r["analysis"]: (float(r["expected_bits"]), float(r["cost"])) for r in opts}
    blocks = ["annotated", "assayed", "random-herbs", "rewired"]
    titles = {"annotated": "Enrichment, whole annotation (the usual analysis)",
              "assayed": "Enrichment, assayed background",
              "random-herbs": "Random herb combinations",
              "rewired": "Degree-preserving rewiring"}
    support = {"enriched": 0, "specific": 0, "beyond_wiring": 0, "not_specific": 1,
               "explained_by_degree": 1, "not_enriched": 2}
    shades = [ns.BLUES(0.85), ns.BLUES(0.42), "#d9d9d9"]
    ax = ns.axes_mm(fig, 120, 11, 34, 47)
    y = 0
    for b in blocks:
        ax.text(0, y - 0.15, titles[b], fontsize=ns.TINY, fontweight="bold", ha="left",
                va="bottom", color=ns.INK, transform=ax.get_yaxis_transform(),
                clip_on=False)
        y += 0.35
        for key, name, colour, _ in EXPL[:3]:
            row = {r["outcome"]: float(r["p"]) for r in pred
                   if r["analysis"] == b and r["explanation"] == key}
            x = 0.0
            for outcome in sorted(row, key=lambda o: support[o]):
                p = row[outcome]
                ax.barh(y, p, left=x, height=0.72, color=shades[support[outcome]], lw=0)
                if p >= 0.2:
                    ax.text(x + p / 2, y, f"{p:.2f}".lstrip("0"), ha="center", va="center",
                            fontsize=ns.TINY,
                            color=ns.ink_on(shades[support[outcome]]))
                x += p
            ax.plot([-0.035], [y], "s", ms=2.4, mfc=colour, mec="none",
                    transform=ax.get_yaxis_transform(), clip_on=False)
            y += 0.82
        bits, cost = gain[b]
        ax.text(1.05, y - 1.64, f"{bits:.2f} bits", transform=ax.get_yaxis_transform(),
                fontsize=ns.TINY, ha="left", va="center", color=ns.INK, fontweight="bold",
                clip_on=False)
        ax.text(1.05, y - 0.82, f"{cost:.0f} run{'s' if cost > 1 else ''} · "
                f"{bits / cost:.3f}/run".replace("0.", "."),
                transform=ax.get_yaxis_transform(), fontsize=ns.TINY, ha="left",
                va="center", color=ns.INK2, clip_on=False)
        y += 0.75
    ax.set_xlim(0, 1)
    ax.set_ylim(y - 0.5, -0.9)
    ax.set_yticks([])
    ax.set_xticks([0, 0.5, 1], ["0", "0.5", "1"])
    ax.spines["left"].set_visible(False)
    ax.set_xlabel("Sealed P(outcome | explanation)")
    kax = fl.canvas(fig, 96, 67.5, 87, 6)
    x = 0
    for key, name, colour, _ in EXPL[:3]:
        kax.plot([x + 1], [1.3], "s", ms=2.4, mfc=colour, mec="none")
        fl.label(kax, x + 2.4, 1.3, name, size=ns.TINY)
        x += 22
    x = 0
    for shade, text in zip(shades, ("enriched, specific or beyond wiring",
                                    "not specific / explained by degree", "not enriched")):
        kax.add_patch(mpatches.Rectangle((x, 3.6), 2.2, 1.6, fc=shade, ec="none"))
        fl.label(kax, x + 3.0, 4.4, text, size=ns.TINY)
        x += [40, 33, 0][[0, 1, 2][shades.index(shade)]]


def _cased(ax, xs, series):
    """Draw each explanation's posterior; where two coincide exactly, case the lower one
    so that both stay visible without moving either off its value."""
    keys = list(series)
    for i in range(len(xs) - 1):
        for a_i, a in enumerate(keys):
            same = [b for b in keys[a_i + 1:]
                    if abs(series[a][1][i] - series[b][1][i]) < 1e-9
                    and abs(series[a][1][i + 1] - series[b][1][i + 1]) < 1e-9]
            if same:
                ax.plot(xs[i:i + 2], series[a][1][i:i + 2], color=series[a][0], lw=2.6,
                        solid_capstyle="round", zorder=2)
    for key, (colour, ys, ls) in series.items():
        ax.plot(xs, ys, color=colour, lw=0.9, ls=ls, zorder=3)
        ax.plot(xs, ys, "o", ms=2.2, mfc=colour, mec="white", mew=0.4, zorder=4)


def panels_c_d(fig):
    steps = fl.rows("inquiry_steps.csv")
    opts = fl.rows("inquiry_options.csv")
    summary = {r["world"]: r for r in fl.rows("inquiry_summary.csv")}
    order = list(ANALYSIS)
    norm = LogNorm(1e-3, 0.5)
    width, gap, left = 44.0, 9.5, 31.0
    for k, (world, title) in enumerate(WORLDS):
        x0 = left + k * (width + gap)
        rows = [r for r in steps if r["world"] == world]
        n = len(rows) - 1
        ax = ns.axes_mm(fig, x0, 89, width, 27)
        xs = [int(r["step"]) for r in rows]
        series = {key: (colour, [float(r[f"p_{key}"]) for r in rows], ls)
                  for key, _, colour, ls in EXPL}
        _cased(ax, xs, series)
        ax.axhline(0.95, color=ns.INK, lw=0.5, ls=(0, (2, 1.4)), zorder=1)
        ax.set_xlim(-0.4, 5.5)
        ax.set_ylim(0, 1.0)
        outcomes = {int(r["step"]): r["outcome"] for r in rows if r["step"] != "0"}
        short = {"enriched": "enr.", "not_enriched": "not\nenr.", "specific": "spec.",
                 "not_specific": "not\nspec.", "beyond_wiring": "beyond", 
                 "explained_by_degree": "degree"}
        ax.set_xticks(range(0, 6), [""] + [short.get(outcomes.get(i, ""), "")
                                            for i in range(1, 6)])
        ax.tick_params(axis="x", labelsize=ns.TINY, pad=1.0, length=1.5)
        ax.set_yticks([0, 0.5, 1], ["0", "0.5", "1"] if k == 0 else [])
        if k == 0:
            ax.set_ylabel("Posterior probability")
            ax.text(-0.75, -0.115, "outcome", fontsize=ns.TINY, ha="right",
                    va="center", color=ns.MUTED, fontstyle="italic",
                    transform=ax.get_xaxis_transform())
        ns.hairline_grid(ax, "y")
        s = summary[world]
        leader = dict((e[0], e[1]) for e in EXPL)[s["leader"]]
        claims = int(s["mechanism_claims_released"])
        what = ("1 mechanism hypothesis,\nstated tentatively" if claims
                else "no mechanism claim:\na finding about the analysis")
        ax.set_title(title, fontsize=ns.SIZE, fontweight="bold", pad=9.5)
        ax.text(0.5, 1.035, f"accepted: {leader.lower()} ({float(s['posterior']):.3f})",
                transform=ax.transAxes, ha="center", va="bottom", fontsize=ns.TINY,
                color=ns.INK)
        tx, ty = (0.36, 0.52) if world == "coverage" else (0.03, 0.80)
        ax.text(tx, ty, f"{what}\n{s['np_runs']} of {s['np_runs_if_everything']} runs",
                transform=ax.transAxes, fontsize=ns.TINY, ha="left", va="center",
                color=ns.INK2, linespacing=1.15)
        # d: the engine's options at each decision
        hx = ns.axes_mm(fig, x0, 124.5, width, 20)
        for step in range(1, n + 1):
            decision = [o for o in opts if o["world"] == world
                        and int(o["decision"]) == step - 1]
            for i, aid in enumerate(order):
                o = next(r for r in decision if r["analysis"] == aid)
                if o["ready"] == "1":
                    v = max(float(o["bits_per_run"]), 1.0001e-3)
                    colour = ns.BLUES(norm(v) * 0.92 + 0.04)
                    hx.add_patch(mpatches.Rectangle((step - 0.45, i - 0.45), 0.9, 0.9,
                                                    fc=colour, ec="none"))
                    if o["chosen"] == "1":
                        hx.add_patch(mpatches.Rectangle((step - 0.45, i - 0.45), 0.9, 0.9,
                                                        fc="none", ec=ns.INK, lw=0.8))
                else:
                    hx.plot([step], [i], ".", ms=1.2, color=ns.RULE)
        hx.set_xlim(-0.4, 5.5)
        hx.set_ylim(len(order) - 0.5, -0.5)
        hx.set_yticks(range(len(order)), [ANALYSIS[a] for a in order] if k == 0 else [])
        hx.tick_params(axis="y", length=0, pad=2, labelsize=ns.TINY)
        hx.set_xticks(range(1, 6), [str(i) for i in range(1, 6)])
        hx.tick_params(axis="x", labelsize=ns.TINY, pad=1)
        hx.set_xlabel("Step")
        for side in ("left", "top", "right"):
            hx.spines[side].set_visible(False)
    # keys
    kax = fl.canvas(fig, 31, 154.5, 100, 4)
    x = 0
    for key, name, colour, ls in EXPL:
        kax.plot([x, x + 4], [2, 2], color=colour, lw=0.9, ls=ls)
        kax.plot([x + 2], [2], "o", ms=2.2, mfc=colour, mec="white", mew=0.4)
        fl.label(kax, x + 5.2, 2, name, size=ns.TINY)
        x += 25
    cax = ns.axes_mm(fig, 150, 156.0, 26, 1.6)
    grad = np.logspace(-3, np.log10(0.5), 256)[None, :]
    cax.imshow(norm(grad) * 0.92 + 0.04, aspect="auto", cmap=ns.BLUES, vmin=0, vmax=1,
               extent=(np.log10(1e-3), np.log10(0.5), 0, 1))
    cax.set_yticks([])
    cax.set_xticks([-3, -2, -1], ["0.001", "0.01", "0.1"])
    cax.tick_params(axis="x", labelsize=ns.TINY, length=1.2, pad=0.8)
    for sp in cax.spines.values():
        sp.set_visible(False)
    cax.set_title("Expected bits per run (d)", fontsize=ns.TINY, pad=1.5,
                  fontweight="normal")


def main():
    ns.apply()
    fig = ns.figure(ns.DOUBLE, 163)
    panel_a(fig)
    panel_b(fig)
    panels_c_d(fig)
    for letter, x, y in (("a", 0, 1.5), ("b", 93, 1.5), ("c", 0, 77.5), ("d", 0, 121.5)):
        ns.panel_label(fig, letter, x, y)
    return ns.save(fig, "Fig7")


if __name__ == "__main__":
    for p in main():
        print(p)
