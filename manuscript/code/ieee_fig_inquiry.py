#!/usr/bin/env python3
"""IEEE-style plot, two columns: the inquiry engine on three planted worlds.

Data: BioScience-Harness/scripts/run_inquiry.py --planted all, re-run and replayed step by
step through psh.scientist.inquiry (data/extracted/inquiry_*.csv), so every posterior and
expected gain is the engine's own number at that decision. Each world is built on the real
herb layer of the Gegen Qinlian decoction and makes one explanation true.

    python manuscript/code/ieee_fig_inquiry.py       # -> manuscript/ieee/IEEE_Fig_Inquiry.*
"""

from __future__ import annotations

from matplotlib.lines import Line2D

import figlib as fl
import ieee_style as st

W = st.TWO_COLUMN * 25.4
#: explanation -> (label, colour, line style, marker)
EXPL = {"target": ("selective action", st.BLUE, "-", "o"),
        "coverage": ("assay coverage", st.VERMILLION, (0, (4.0, 1.6)), "s"),
        "promiscuity": ("promiscuous chemistry", st.GREEN, (0, (5.0, 1.5, 1.2, 1.5)), "^"),
        "catch-all": ("none of these (catch-all)", st.GREY, (0, (1.2, 1.4)), None)}
ANALYSIS = {"annotated": "whole annotation", "assayed": "assayed background",
            "assayed-seed": "assayed, reseeded", "random-herbs": "random herbs",
            "random-herbs-2": "random herbs, again", "rewired": "rewired network",
            "rewired-2": "rewired, again"}
WORLDS = [("selective", "Selective world"), ("coverage", "Coverage world"),
          ("promiscuous", "Promiscuous world")]


def trajectories(fig, x0, top, width, height, world, steps, summary, first):
    rows = [r for r in steps if r["world"] == world]
    xs = [int(r["step"]) for r in rows]
    ys = {k: [float(r[f"p_{k}"]) for r in rows] for k in EXPL}
    ax = st.axes(fig, x0, top, width, height)
    keys = list(EXPL)
    # where two posteriors coincide exactly, case the first so both stay visible
    for i in range(len(xs) - 1):
        for a_i, a in enumerate(keys):
            for b in keys[a_i + 1:]:
                if (abs(ys[a][i] - ys[b][i]) < 1e-9
                        and abs(ys[a][i + 1] - ys[b][i + 1]) < 1e-9):
                    ax.plot(xs[i:i + 2], ys[a][i:i + 2], color=st.tint(EXPL[a][1], 0.35),
                            lw=3.2, solid_capstyle="round", zorder=2)
    for k, (label, colour, ls, marker) in EXPL.items():
        ax.plot(xs, ys[k], color=colour, lw=1.0, ls=ls, zorder=3)
        if marker:
            ax.plot(xs, ys[k], marker, ms=3.4, mfc=colour, mec="white", mew=0.5, zorder=4)
    ax.axhline(0.95, color=st.BLACK, lw=0.6, ls=(0, (3.0, 2.0)), zorder=1)
    ax.set_ylim(-0.03, 1.03)
    ax.set_xlim(-0.3, len(xs) - 0.7)
    ax.set_yticks([0, 0.25, 0.5, 0.75, 1.0], ["0", "", "0.5", "", "1"] if first else [])
    if first:
        ax.set_ylabel("posterior probability")
    labels = ["prior"] + [ANALYSIS[r["analysis"]] for r in rows[1:]]
    ax.set_xticks(xs, labels, rotation=40, ha="right", rotation_mode="anchor")
    st.grid(ax, "y")
    return ax


def expected_information(fig, x0, top, width, height, opts):
    first = [r for r in opts if r["world"] == "selective" and r["decision"] == "0"]
    first.sort(key=lambda r: -float(r["bits_per_run"]))
    names = [ANALYSIS[r["analysis"]] for r in first]
    for k, (col, label, xmax, ticks) in enumerate(
            (("expected_bits", "expected bits", 0.8, [0, 0.4, 0.8]),
             ("bits_per_run", "bits per run", 0.5, [0, 0.25, 0.5]))):
        ax = st.axes(fig, x0 + k * (width + 4.0), top, width, height)
        for i, r in enumerate(first):
            v = float(r[col])
            colour = st.BLUE if r["chosen"] == "1" else st.tint(st.BLUE, 0.45)
            ax.barh(i, v, height=0.62, color=colour, lw=0)
            ax.text(v + xmax * 0.02, i, f"{v:.2f}" if col == "expected_bits" else f"{v:.3f}",
                    va="center", fontsize=st.TEXT)
        ax.set_yticks(range(len(first)), names if k == 0 else [])
        ax.set_ylim(len(first) - 0.5, -0.5)
        ax.set_xlim(0, xmax * 1.3)
        ax.set_xticks(ticks)
        ax.tick_params(axis="y", which="both", length=0)
        ax.set_xlabel(label)
        st.grid(ax, "x")
        if k == 1:
            for i, r in enumerate(first):
                ax.text(xmax * 1.32, i, f"cost {float(r['cost']):g}", va="center",
                        fontsize=st.TEXT, fontstyle="italic", clip_on=False)


def main():
    st.apply()
    steps = fl.rows("inquiry_steps.csv")
    opts = fl.rows("inquiry_options.csv")
    summary = {r["world"]: r for r in fl.rows("inquiry_summary.csv")}
    H = 122.0
    fig = st.figure(st.TWO_COLUMN, H)
    width, gap, x0, top, height = 47.0, 10.5, 14.0, 3.0, 36.0
    for k, (world, title) in enumerate(WORLDS):
        x = x0 + k * (width + gap)
        trajectories(fig, x, top, width, height, world, steps, summary, k == 0)
        s = summary[world]
        st.subcaption(fig, x + width / 2, top + height + 22.5,
                      f"({'abc'[k]}) {title}: {EXPL[s['leader']][0]}\naccepted at "
                      f"{float(s['posterior']):.3f} after {s['np_runs']} of "
                      f"{s['np_runs_if_everything']} runs")
    expected_information(fig, 36.0, 80.0, 30.0, 25.0, opts)
    st.subcaption(fig, 53.0, 80.0 + 25.0 + 11.0,
                  "(d) What each analysis was worth before any run")
    # one key for (a) to (c)
    kax = fl.canvas(fig, 116.0, 80.0, 64.0, 34.0)
    handles = [Line2D([], [], color=c, ls=ls, lw=1.0, marker=m or "None", ms=3.4,
                      mfc=c, mec="white", mew=0.5) for _, c, ls, m in EXPL.values()]
    handles.append(Line2D([], [], color=st.BLACK, ls=(0, (3.0, 2.0)), lw=0.6))
    labels = [v[0] for v in EXPL.values()] + ["acceptance threshold (0.95)"]
    kax.legend(handles, labels, loc="upper left", title="Explanation", title_fontsize=st.TEXT,
               handlelength=2.6)
    return st.save(fig, "IEEE_Fig_Inquiry")


if __name__ == "__main__":
    for p in main():
        print(p)
