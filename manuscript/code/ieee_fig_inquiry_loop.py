#!/usr/bin/env python3
"""IEEE-style flowchart, one column: how the inquiry engine decides what to do next.

The decision order is that of psh.scientist.inquiry.Inquiry.next_step, and every threshold
on the figure is the default StoppingRule, read from the code
(data/extracted/inventory.json): the step limit, the catch-all's alarm and floor, the
acceptance threshold, the severity a test must reach, the replication requirement and the
least expected information an analysis must offer per unit of cost.

    python manuscript/code/ieee_fig_inquiry_loop.py  # -> manuscript/ieee/IEEE_Fig_InquiryLoop.*
"""

from __future__ import annotations

import figlib as fl
import ieee_style as st

W = st.ONE_COLUMN * 25.4


def main():
    st.apply()
    rule = fl.load_json("inventory.json")["inquiry_stopping_rule"]
    assert rule["require_replication"] is True, rule
    kern, _, kblock = st.ROLE["kernel"]
    mod, mfill, _ = st.ROLE["model"]
    ref, _, rblock = st.ROLE["refusal"]
    H = 117.0
    fig = st.figure(st.ONE_COLUMN, H)
    ax = fl.canvas(fig, 0, 0, W, H)
    cx, mw = 27.0, 46.0                 # the main column: centre and width
    left = cx - mw / 2
    rx = 57.5                           # the column of exits
    rw = W - rx - 0.6
    rc = rx + rw / 2

    st.block(fig, ax, left, 1.0, mw, 19.0, "The model proposes",
             f"explanations and a catch-all (prior ≥ {rule['catch_all_floor']:.2f}); "
             "analyses with costs; predictions, sealed before any run",
             edge=mod, fill=mfill, style=(0, (3.0, 2.0)))

    d1, d2, d3 = 31.0, 46.5, 62.0
    dw, dh = 34.0, 12.0
    st.diamond(fig, ax, cx, d1, dw, dh, f"{rule['max_steps']} steps\nreached?", edge=kern)
    st.diamond(fig, ax, cx, d2, dw, dh, f"catch-all\n≥ {rule['catch_all_alarm']:.2f}?",
               edge=kern)
    st.diamond(fig, ax, cx, d3, dw, dh, f"leader\n≥ {rule['accept_at']:.2f}?", edge=kern)
    st.arrow(ax, [(cx, 20.0), (cx, d1 - dh / 2)])
    st.arrow(ax, [(cx, d1 + dh / 2), (cx, d2 - dh / 2)])
    st.arrow(ax, [(cx, d2 + dh / 2), (cx, d3 - dh / 2)])
    for y in (d1, d2, d3):
        st.text(ax, cx + 1.2, y + dh / 2 + 1.7, "no", style="italic")
        st.text(ax, cx + dw / 2 + 1.0, y - 1.7, "yes", style="italic")

    # yes: stop, or ask for new explanations, or test the leader
    sw = rw - 7.0                       # leaves a lane on the right for the return path
    st.block(fig, ax, rx, d1 - 5.6, sw, 11.2, "Stop", "provisional or undetermined",
             edge=ref, fill=rblock, name_colour=ref)
    st.arrow(ax, [(cx + dw / 2, d1), (rx, d1)])
    st.block(fig, ax, rx, d2 - 5.5, rw, 11.0, "New explanations",
             "carved from the catch-all", edge=mod, fill=mfill, style=(0, (3.0, 2.0)))
    st.arrow(ax, [(cx + dw / 2, d2), (rx, d2)])
    lane = rx + sw + 3.5
    st.arrow(ax, [(lane, d2 - 5.5), (lane, 10.5), (left + mw, 10.5)])
    d4h = 15.0
    d4 = d3 + 3.0 + d4h / 2
    st.diamond(fig, ax, rc, d4, rw + 0.4, d4h, "tested and\nreplicated?", edge=kern)
    st.arrow(ax, [(cx + dw / 2, d3), (rc, d3), (rc, d4 - d4h / 2)])

    # the next analysis: a test the leader owes, or the most informative
    ry, rh = d3 + dh / 2 + 6.0, 19.5
    st.block(fig, ax, left, ry, mw, rh, "Run the next analysis",
             f"a severe test (≥ {rule['min_severity']:.1f}) or replicate the leader owes; "
             f"else the most information per unit cost (≥ {rule['min_gain']} bits), or "
             "stop", edge=kern)
    st.arrow(ax, [(cx, d3 + dh / 2), (cx, ry)])
    st.arrow(ax, [(rc - (rw + 0.4) / 2, d4), (left + mw + 3.0, d4), (left + mw + 3.0, ry + 5.0),
                  (left + mw, ry + 5.0)])
    st.text(ax, rc - (rw + 0.4) / 2 - 0.8, d4 - 1.7, "no", style="italic", ha="right")
    oy, oh = ry + rh + 5.0, 17.0
    st.block(fig, ax, left, oy, mw, oh, "Observe and update",
             "Bayes on the sealed predictions only; a design that cannot license a claim "
             "kind leaves it fixed", edge=kern)
    st.arrow(ax, [(cx, ry + rh), (cx, oy)])
    st.arrow(ax, [(left, oy + oh / 2), (1.0, oy + oh / 2), (1.0, d1), (cx - dw / 2, d1)])

    # yes: accept, its wording capped by the licensing table
    ay = d4 + d4h / 2 + 5.0
    st.block(fig, ax, rx, ay, rw, 17.0, "Accept the leader",
             "wording capped by the licensing table, not the posterior",
             edge=kern, fill=kblock, lw=1.25, name_colour=kern)
    st.arrow(ax, [(rc, d4 + d4h / 2), (rc, ay)])
    st.text(ax, rc + 1.2, d4 + d4h / 2 + 2.0, "yes", style="italic")
    assert oy + oh < H - 0.5, oy + oh
    return st.save(fig, "IEEE_Fig_InquiryLoop")


if __name__ == "__main__":
    for p in main():
        print(p)
