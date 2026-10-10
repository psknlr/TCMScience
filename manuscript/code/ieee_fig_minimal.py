#!/usr/bin/env python3
"""IEEE-style flowchart, one column: TCMScience reduced to the one idea it rests on.

The model proposes and the kernel decides. The trusted kernel compiles, authorizes,
executes and verifies each step; it alone calls the model and the tools; it releases a
claim only of a kind the evidence licenses, and otherwise refuses with a code; and it
appends every decision to a hash-chained audit. Nothing else is drawn.

    python manuscript/code/ieee_fig_minimal.py    # -> manuscript/ieee/IEEE_Fig_Minimal.*
"""

from __future__ import annotations

import figlib as fl
import ieee_style as st

W = st.ONE_COLUMN * 25.4          # 88.9 mm


def main():
    st.apply()
    H = 56.0
    fig = st.figure(st.ONE_COLUMN, H)
    ax = fl.canvas(fig, 0, 0, W, H)
    kx, ky, kw, kh = 22.0, 19.0, 36.0, 18.0          # the kernel, at the centre
    st.block(fig, ax, kx, 1.0, kw, 10.5, "Language model", "proposes; untrusted",
             style=(0, (3.0, 2.0)))
    st.block(fig, ax, kx, ky, kw, kh, "Trusted kernel",
             ["compiles, authorizes,", "executes and verifies"], sep="\n", fill=st.FILL,
             lw=1.25, name_size=st.REGION)
    st.block(fig, ax, kx, 44.5, kw, 10.5, "Tools and data",
             "connectors, tools, snapshots")
    st.block(fig, ax, 0.4, ky + 4.0, 15.6, kh - 8.0, "Question")
    st.block(fig, ax, 64.0, ky - 2.5, W - 64.4, 10.0, "Licensed claim", "its kind licensed",
             lw=1.25)
    st.block(fig, ax, 64.0, ky + 10.5, W - 64.4, 10.0, "Refusal", "code and remedy")
    st.block(fig, ax, 64.0, 44.5, W - 64.4, 10.5, "Audit", "hash-chained")

    st.arrow(ax, [(16.0, ky + kh / 2), (kx, ky + kh / 2)])
    # the kernel alone calls the model and the tools
    for x, up, label, side in ((35.0, True, "context", "right"),
                               (45.0, False, "proposal", "left")):
        pts = [(x, ky), (x, 11.5)] if up else [(x, 11.5), (x, ky)]
        st.arrow(ax, pts)
        st.text(ax, x - 1.2 if side == "right" else x + 1.2, 15.2, label,
                ha=side if side == "right" else "left", style="italic")
    for x, down, label, side in ((35.0, True, "call", "right"),
                                 (45.0, False, "result", "left")):
        pts = [(x, ky + kh), (x, 44.5)] if down else [(x, 44.5), (x, ky + kh)]
        st.arrow(ax, pts)
        st.text(ax, x - 1.2 if side == "right" else x + 1.2, 40.8, label,
                ha=side if side == "right" else "left", style="italic")
    # what leaves, and what is kept
    st.arrow(ax, [(kx + kw, ky + 4.0), (64.0, ky + 2.5)])
    st.arrow(ax, [(kx + kw, ky + kh - 4.0), (64.0, ky + 15.5)])
    st.arrow(ax, [(kx + kw - 3.0, ky + kh), (kx + kw - 3.0, 40.6), (84.0, 40.6),
                  (84.0, 44.5)], dashed=True)
    st.text(ax, 81.6, 41.0, "every decision", ha="right", style="italic", va="top")
    return st.save(fig, "IEEE_Fig_Minimal")


if __name__ == "__main__":
    for p in main():
        print(p)
