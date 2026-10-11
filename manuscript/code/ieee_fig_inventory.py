#!/usr/bin/env python3
"""IEEE-style plot, two columns: what the capability plane holds, as the code registers it.

(a) the 117 public sources and their typed operations by domain (bioagent.providers);
(b) the TCM database catalogue by how each database can be reached
(bioagent/data/tcm_source_catalog.json); (c) the federated capability catalogue by kind and
by whether its licence lets the code be vendored (unified_capability_catalogue.csv);
(d) the native tools by domain (bioagent.tools). All from data/extracted/.

    python manuscript/code/ieee_fig_inventory.py     # -> manuscript/ieee/IEEE_Fig_Inventory.*
"""

from __future__ import annotations

import matplotlib.patches as mpatches

import figlib as fl
import ieee_style as st

W = st.TWO_COLUMN * 25.4
ACCESS = [("live_api+snapshot", "Live API and snapshot"), ("live_api", "Live API"),
          ("snapshot", "Snapshot"), ("manual_import", "Manual import"),
          ("restricted", "Restricted"), ("unreachable", "Unreachable")]
KIND = {"tool": "Tools", "skill": "Skills", "database": "Databases",
        "benchmark": "Benchmarks", "software": "Software", "dataset": "Datasets",
        "agent_role": "Agent roles"}
TOOL_DOMAIN = {"clinical-calculators": "Clinical calculators", "statistics": "Statistics",
               "sequence-analysis": "Sequence analysis", "tcm-knowledge": "TCM knowledge",
               "pharmacology": "Pharmacology", "file-formats": "File formats",
               "phylogenetics": "Phylogenetics", "variant-analysis": "Variant analysis",
               "protein-analysis": "Protein analysis",
               "population-genetics": "Population genetics",
               "sequence-alignment": "Sequence alignment",
               "survival-analysis": "Survival analysis"}
DARK = st.shade(st.GREEN, 0.25)          # the TCM rows, and what can be reached live
LIGHT = st.tint(st.GREEN, 0.55)


def domain_name(d: str) -> str:
    return {"tcm": "TCM", "single-cell": "Single cell"}.get(d, d.replace("-", " ").capitalize())


def hbars(ax, labels, values, colours, xmax, ticks, *, hatch=None, label_values=True):
    for i, (v, c) in enumerate(zip(values, colours)):
        ax.barh(i, v, height=0.66, color=c, lw=0, hatch=hatch)
        if label_values:
            ax.text(v + xmax * 0.015, i, f"{v:,}", va="center", fontsize=st.TEXT)
    ax.set_yticks(range(len(labels)), labels)
    ax.set_ylim(len(labels) - 0.4, -0.6)
    ax.set_xlim(0, xmax)
    ax.set_xticks(ticks)
    ax.tick_params(axis="y", which="both", length=0)
    st.grid(ax, "x")


def main():
    st.apply()
    inv = fl.load_json("inventory.json")
    cap = fl.load_json("capabilities.json")
    doms = fl.rows("public_sources_by_domain.csv")
    assert sum(int(d["sources"]) for d in doms) == cap["public_sources"]["total"]
    H = 99.0
    fig = st.figure(st.TWO_COLUMN, H)
    top = 2.0

    # (a) sources and operations by domain, as two panels on one domain axis
    h = 84.0
    names = [domain_name(d["domain"]) for d in doms]
    colours = [DARK if d["domain"] == "tcm" else st.GREEN for d in doms]
    ax = st.axes(fig, 28.5, top, 14.0, h)
    hbars(ax, names, [int(d["sources"]) for d in doms], colours, 18, [0, 8, 16],
          label_values=False)
    ax.set_xlabel("sources")
    for lab, d in zip(ax.get_yticklabels(), doms):
        if d["domain"] == "tcm":
            lab.set_fontweight("bold")
    bx = st.axes(fig, 44.5, top, 16.0, h)
    hbars(bx, names, [int(d["operations"]) for d in doms],
          colours, 64, [0, 30, 60],
          label_values=False)
    bx.set_yticklabels([])
    bx.set_xlabel("operations")
    st.subcaption(fig, 31.0, top + h + 7.6,
                  f"(a) {cap['public_sources']['total']} sources, "
                  f"{cap['public_sources']['operations']} operations")

    # (b) the TCM catalogue by access
    acc = cap["tcm_catalogue"]["by_access"]
    reach = [DARK, DARK, LIGHT, LIGHT, st.LIGHT, st.LIGHT]
    cx = st.axes(fig, 96.0, top, 21.0, 20.0)
    hbars(cx, [lab for _, lab in ACCESS], [acc[k] for k, _ in ACCESS], reach, 48,
          [0, 20, 40])
    cx.set_xlabel("databases")
    st.subcaption(fig, 94.0, top + 20.0 + 7.6,
                  f"(b) TCM catalogue ({cap['tcm_catalogue']['total']})")

    # (c) the federated catalogue by kind; filled, may be vendored; hatched, adapter only
    fed = fl.rows("federated_catalogue_kinds.csv")
    cy = top + 37.0
    dx = st.axes(fig, 96.0, cy, 21.0, 24.0)
    vend = [int(r["vendor"]) for r in fed]
    adapt = [int(r["adapter_only"]) for r in fed]
    hbars(dx, [KIND[r["kind"]] for r in fed], vend, [st.GREEN] * len(fed), 2400,
          [0, 1000, 2000], label_values=False)
    for i, (v, a) in enumerate(zip(vend, adapt)):
        dx.barh(i, a, left=v, height=0.66, color="white", edgecolor=st.GREEN, lw=0.5,
                hatch="////")
        dx.text(v + a + 30, i, f"{v + a:,}", va="center", fontsize=st.TEXT)
    dx.set_xlabel("capabilities")
    f = cap["federated"]
    st.subcaption(fig, 94.0, cy + 24.0 + 7.6,
                  f"(c) Federated catalogue ({f['capabilities']:,})")

    # (d) native tools by domain
    tools = sorted(inv["native_tools_by_domain"].items(), key=lambda kv: -kv[1])
    ex = st.axes(fig, 155.0, top, 21.0, 61.0)
    hbars(ex, [TOOL_DOMAIN[d] for d, _ in tools], [v for _, v in tools],
          [DARK if d == "tcm-knowledge" else st.GREEN for d, _ in tools], 70, [0, 30, 60])
    ex.set_xlabel("tools")
    for lab, (d, _) in zip(ex.get_yticklabels(), tools):
        if d == "tcm-knowledge":
            lab.set_fontweight("bold")
    st.subcaption(fig, 152.0, top + 61.0 + 7.6, f"(d) Native tools ({inv['native_tools']})")

    # one key for the shades
    kax = fl.canvas(fig, 66.0, 79.0, 116.0, 18.0)
    adapter = sum(1 for r in fl.rows("federated_projects.csv")
                  if r["integration_mode"] == "adapter-only")
    items = [(DARK, None, "TCM (a, d); reachable live (b)"), (LIGHT, None, "as files (b)"),
             (st.LIGHT, None, "not reachable (b)"),
             ("white", "////", f"adapter only (c): invoked in place, never copied "
                               f"({adapter} of {f['projects']} projects)")]
    x = 2.0
    for colour, hatch, text in items[:3]:
        kax.add_patch(mpatches.Rectangle((x, 2.2), 4.0, 2.6, fc=colour, ec="none"))
        st.text(kax, x + 5.2, 3.5, text)
        x += 9.0 + fl.text_width(fig, text, st.TEXT) + 3.0
    colour, hatch, text = items[3]
    kax.add_patch(mpatches.Rectangle((2.0, 8.2), 4.0, 2.6, fc=colour, ec=st.GREEN, lw=0.5,
                                     hatch=hatch))
    st.text(kax, 7.2, 9.5, text)
    return st.save(fig, "IEEE_Fig_Inventory")


if __name__ == "__main__":
    for p in main():
        print(p)
