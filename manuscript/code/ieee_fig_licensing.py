#!/usr/bin/env python3
"""IEEE-style plot, one column: which study design licenses which kind of claim.

The matrix is the kernel's (psh.sir.values.LICENSING and MAX_CERTAINTY, via
data/extracted/). As for Fig. 2 of the Nature set, the build first checks that every pair
the compile-time table admits (psh.workflow.compiler._SUPPORTS, EVIDENCE103) is a direct
cell here, and that an in-silico design licenses a mechanism hypothesis and nothing else.

    python manuscript/code/ieee_fig_licensing.py     # -> manuscript/ieee/IEEE_Fig_Licensing.*
"""

from __future__ import annotations

import matplotlib.patches as mpatches

import fig2_licensing as f2
import figlib as fl
import ieee_style as st

W = st.ONE_COLUMN * 25.4


def main():
    st.apply()
    grade = {(r["claim_kind"], r["design"]): r["grade"] for r in fl.rows("licensing_matrix.csv")}
    cert = {r["design"]: r["max_certainty"] for r in fl.rows("max_certainty.csv")}
    f2.check_tables(grade)
    rows, names, gaps = [], [], []
    for _, members in f2.GROUPS:
        if rows:
            gaps.append(len(rows))
        for key, name in members:
            rows.append(key)
            names.append(name)
    ys, y = [], 0.0
    for i in range(len(rows)):
        if i in gaps:
            y += 0.4
        ys.append(y)
        y += 1.0
    H = 101.0
    fig = st.figure(st.ONE_COLUMN, H)
    left, top, cw, rh = 29.0, 27.0, 4.55, 3.25
    ncol = len(f2.CLAIMS)
    ax = st.axes(fig, left, top, cw * ncol, rh * (ys[-1] + 1))
    ax.set_xlim(-0.5, ncol - 0.5)
    ax.set_ylim(ys[-1] + 0.5, -0.5)
    ax.tick_params(which="both", length=0, top=False, right=False, bottom=False, left=False)
    ax.set_xticks([])
    ax.set_yticks(ys, names)
    blue = st.BLUE
    j_ins = rows.index("in_silico")
    ax.add_patch(mpatches.Rectangle((-0.5, ys[j_ins] - 0.5), ncol, 1.0,
                                    fc=st.tint(st.VERMILLION, 0.18), ec="none", zorder=0))
    for x in range(ncol - 1):
        ax.axvline(x + 0.5, color=st.GRID, lw=0.5, linestyle=(0, (1.0, 1.5)), zorder=1)
    for g in gaps:
        ax.axhline(ys[g] - 0.7, color=st.GREY, lw=0.5, zorder=1)
    for i, d in enumerate(rows):
        for j, (k, _, _) in enumerate(f2.CLAIMS):
            g = grade[(k, d)]
            if g == "direct":
                ax.plot(j, ys[i], "o", ms=4.6, mfc=blue, mec=blue, mew=0.6, zorder=3)
            elif g == "extrapolated":
                ax.plot(j, ys[i], "o", ms=4.2, mfc="white", mec=blue, mew=0.9, zorder=3)
            else:
                ax.plot(j, ys[i], "o", ms=1.6, mfc=st.GREY, mec="none", zorder=3)
    for j, (_, en, _) in enumerate(f2.CLAIMS):
        ax.text(j - 0.1, -0.75, en.replace("\n", " "), rotation=60, rotation_mode="anchor",
                ha="left", va="bottom", fontsize=st.TEXT,
                fontweight="bold" if j == 2 else "normal")
    # the strongest wording a single study of each design licenses
    cax = fl.canvas(fig, left + cw * ncol + 1.6, top, W - left - cw * ncol - 2.2,
                    rh * (ys[-1] + 1))
    for i, d in enumerate(rows):
        level = f2.CERTAINTY.index(cert[d])
        yy = (ys[i] + 0.5) * rh
        for s in range(1, 4):
            cax.add_patch(mpatches.Rectangle((0.3 + (s - 1) * 1.7, yy - 1.0), 1.3, 2.0,
                                             fc=st.DARK if s <= level else st.LIGHT,
                                             ec="none"))
        st.text(cax, 5.8, yy + 1.05, cert[d], va="baseline")
    # key
    kax = fl.canvas(fig, 1.0, top + rh * (ys[-1] + 1) + 2.4, W - 2.0, 13.0)
    items = [("o", 4.6, blue, blue, 0.6, "licenses directly"),
             ("o", 4.2, "white", blue, 0.9, "only by declared extrapolation"),
             ("o", 1.6, st.GREY, "none", 0.0, "cannot license")]
    x = 1.0
    for marker, ms, fc, ec, mew, text in items:
        kax.plot([x], [2.0], marker, ms=ms, mfc=fc, mec=ec, mew=mew)
        st.text(kax, x + 1.8, 3.05, text, va="baseline")
        x += 4.2 + fl.text_width(fig, text, st.TEXT)
    kax.add_patch(mpatches.Rectangle((0.0, 4.6), 3.0, 2.2, fc=st.tint(st.VERMILLION, 0.18),
                                     ec="none"))
    st.text(kax, 3.8, 5.7, "a prediction licenses a mechanism hypothesis, nothing more")
    for s_ in range(1, 4):
        kax.add_patch(mpatches.Rectangle((0.0 + (s_ - 1) * 1.7, 8.7), 1.3, 2.0,
                                         fc=st.DARK if s_ <= 2 else st.LIGHT, ec="none"))
    st.text(kax, 5.6, 9.7, "the strongest wording one study of the design licenses")
    return st.save(fig, "IEEE_Fig_Licensing")


if __name__ == "__main__":
    for p in main():
        print(p)
