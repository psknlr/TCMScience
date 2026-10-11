#!/usr/bin/env python3
"""IEEE-style figure, two columns: how third-party data enter, as audited snapshots.

The pipeline is bioagent.sources (cards, parsers, snapshot, ledger): access paths in the
cards' order of preference (ACCESS_MODES), a quality gate whose three outcomes and drift
limit are QCThresholds' and QCReport's, an id hashed from the manifest's content, a
hash-chained ledger, and re-verification on load. The eight source cards, their access
paths and licences are read from the code (data/extracted/capabilities.json); their role
in the Gegen Qinlian decoction study from README.md line 130 (data/curated).

    python manuscript/code/ieee_fig_snapshots.py      # -> manuscript/ieee/IEEE_Fig_Snapshots.*
"""

from __future__ import annotations

from matplotlib.patches import FancyBboxPatch

import figlib as fl
import ieee_style as st

W = st.TWO_COLUMN * 25.4
CARD = {"lotus": "LOTUS (frozen export)", "npass": "NPASS 2.0", "cmaup": "CMAUP 2.0",
        "bindingdb": "BindingDB", "string": "STRING v12 (human)", "reactome": "Reactome",
        "pubchem_bioassay": "PubChem BioAssay", "opentargets": "Open Targets Platform"}
LICENCE = {"CC-BY-4.0": "CC BY 4.0", "CC-BY-3.0": "CC BY 3.0", "CC0-1.0": "CC0 1.0",
           "Not stated": "not stated", "NCBI-data-policy": "NCBI data policy"}


def chip_colour(licence: str):
    if licence.startswith("CC"):
        return st.tint(st.GREEN, 0.30)                  # an open licence
    if licence == "Not stated":
        return st.tint(st.VERMILLION, 0.30)             # nothing stated
    return st.tint(st.SKY, 0.30)                        # a provider's own data policy


def main():
    st.apply()
    cap = fl.load_json("capabilities.json")
    roles = {r["key"]: r["role"] for r in fl.rows("case_study_sources.csv")}
    modes = cap["snapshot_access_modes"]
    drift = cap["snapshot_qc_max_drift"]
    assert modes == ["bulk", "api", "manual", "web"], modes
    capc, _, cblock = st.ROLE["capability"]
    kern, _, kblock = st.ROLE["kernel"]
    ref, _, rblock = st.ROLE["refusal"]
    govc, _, gblock = st.ROLE["governance"]
    H = 88.5
    fig = st.figure(st.TWO_COLUMN, H)
    ax = fl.canvas(fig, 0, 0, W, H)

    steps = [("Source card", "licence; access " + " > ".join(modes)),
             ("Fetch", "raw files, each hashed"),
             ("Parse", "node and edge tables; dropped rows counted"),
             ("Normalize", "InChIKey, Swiss-Prot, MONDO"),
             ("Quality gate", "pass, review or fail"),
             ("Content hash", "key@version# + 12 hex of SHA-256"),
             ("Ledger", "append-only, hash-chained"),
             ("Load", "re-hashed; id and head must match")]
    x0, gap, y0, bh = 1.5, 3.4, 2.0, 18.0
    bw = (W - 2 * x0 - 7 * gap) / 8
    xs = []
    for i, (name, body) in enumerate(steps):
        x = x0 + i * (bw + gap)
        xs.append(x)
        edge, fill = (kern, kblock) if name in ("Ledger", "Load") else (capc, "white")
        if name == "Quality gate":
            edge, fill = capc, cblock
        st.block(fig, ax, x, y0, bw, bh, name, body, edge=edge, fill=fill)
        if i:
            st.arrow(ax, [(x - gap, y0 + bh / 2), (x, y0 + bh / 2)])
    # the quality gate's other two outcomes
    q = xs[4] + bw / 2
    yb = y0 + bh + 5.0
    st.block(fig, ax, xs[2], yb, 2 * bw + gap, 12.0, "review",
             f"coverage short, or drift > {drift:.0%}: a person accepts", edge=govc,
             fill=gblock, name_colour=govc)
    st.block(fig, ax, xs[5], yb, 2 * bw + gap, 12.0, "fail",
             "structure or licence: the snapshot is rejected", edge=ref, fill=rblock,
             name_colour=ref)
    st.arrow(ax, [(q - 2.0, y0 + bh), (q - 2.0, yb - 2.2), (xs[3] + bw / 2 + gap / 2, yb - 2.2),
                  (xs[3] + bw / 2 + gap / 2, yb)], color=govc)
    st.arrow(ax, [(q + 2.0, y0 + bh), (q + 2.0, yb - 2.2), (xs[5] + bw + gap / 2, yb - 2.2),
                  (xs[5] + bw + gap / 2, yb)], color=ref)

    # the eight source cards
    ty = 46.0
    cols = [(1.5, "Source card"), (40.0, "Access"), (60.0, "Licence on the card"),
            (90.0, "Role in the case study")]
    for x, head in cols:
        st.text(ax, x, ty, head, weight="bold", va="baseline")
    ax.plot([1.5, 117.0], [ty + 1.4, ty + 1.4], color=st.BLACK, lw=0.5)
    rh = 4.15
    for j, c in enumerate(cap["snapshot_cards"]):
        y = ty + 1.4 + (j + 1) * rh - 1.1
        st.text(ax, 1.5, y, CARD[c["key"]], va="baseline")
        st.text(ax, 40.0, y, ", ".join(c["access"]), va="baseline")
        lic = LICENCE.get(c["licence"], c["licence"])
        lw = fl.text_width(fig, lic, st.TEXT)
        ax.add_patch(FancyBboxPatch((59.2, y - 2.55), lw + 1.6, 3.5,
                                    boxstyle="round,pad=0,rounding_size=0.8",
                                    fc=chip_colour(c["licence"]), ec="none", zorder=2))
        st.text(ax, 60.0, y, lic, va="baseline")
        role = roles.get(c["key"])
        st.text(ax, 90.0, y, role or "not used", va="baseline",
                style="normal" if role else "italic", color=st.BLACK if role else st.GREY)
    yend = ty + 1.4 + len(cap["snapshot_cards"]) * rh + 0.6
    ax.plot([1.5, 117.0], [yend, yend], color=st.BLACK, lw=0.5)

    # which snapshots a run may use, and how it pins them
    rx = 121.0
    st.block(fig, ax, rx, 41.0, W - rx - 1.5, 21.0, "A run's sources",
             "the skill's declared sources ∩ the enabled cards ∩ the run's policy: a skill "
             "can narrow its sources, never widen them", edge=kern)
    st.block(fig, ax, rx, 65.5, W - rx - 1.5, 19.0, "A run's lock",
             "names every snapshot id and the ledger head it read; other content under the "
             "same label is refused", edge=kern)
    # chips key
    ky = yend + 4.2
    x = 1.5
    for colour, text in ((st.tint(st.GREEN, 0.30), "open licence"),
                         (st.tint(st.SKY, 0.30), "provider's data policy"),
                         (st.tint(st.VERMILLION, 0.30), "no licence stated")):
        ax.add_patch(FancyBboxPatch((x, ky - 2.55), 6.0, 3.5,
                                    boxstyle="round,pad=0,rounding_size=0.8", fc=colour,
                                    ec="none"))
        st.text(ax, x + 7.2, ky, text, va="baseline")
        x += 9.0 + fl.text_width(fig, text, st.TEXT) + 4.0
    assert ky < H - 0.5, ky
    return st.save(fig, "IEEE_Fig_Snapshots")


if __name__ == "__main__":
    for p in main():
        print(p)
