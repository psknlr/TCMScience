#!/usr/bin/env python3
"""IEEE-style figure, two columns: the scientific compiler, pass by pass, and what it refuses.

The passes are psh.compiler.pipeline.PASS_ORDER and each emits one diagnostic family (the
family letters in each pass module's codes; checked when the data are extracted). The bars
are the diagnostic registry (psh.compiler.diagnostics.REGISTRY) by family and severity:
an error refuses the program, a warning is reported and does not. The plan validator's
check families are read from psh.runtime.plan_validator. Everything is read from
data/extracted/inventory.json.

    python manuscript/code/ieee_fig_compiler.py      # -> manuscript/ieee/IEEE_Fig_Compiler.*
"""

from __future__ import annotations

import figlib as fl
import ieee_style as st

W = st.TWO_COLUMN * 25.4
FAMILY = {"structure": "SIR", "method": "SCI", "typecheck": "TYP", "effects": "EFF",
          "infoflow": "IFC", "statistics": "STAT", "reproducibility": "REP",
          "resources": "RES"}
#: Each pass's question, as psh/compiler/pipeline.py states it.
ASKS = {"structure": "is this a program? (stops the pipeline on error)",
        "method": "is the science complete?",
        "typecheck": "does the evidence license the claims?",
        "effects": "may this run do that?",
        "infoflow": "where may these values go?",
        "statistics": "will the design support the inference?",
        "reproducibility": "could anyone repeat it?",
        "resources": "can this run afford it?"}


def main():
    st.apply()
    inv = fl.load_json("inventory.json")
    rt = inv["runtime"]
    sev = inv["compiler_diagnostics_by_severity"]
    passes = rt["compiler_passes"]
    assert passes == list(FAMILY), passes
    assert sum(sum(v.values()) for v in sev.values()) == inv["compiler_diagnostics"]
    kern, _, kblock = st.ROLE["kernel"]
    ref, _, rblock = st.ROLE["refusal"]
    H = 111.0
    fig = st.figure(st.TWO_COLUMN, H)
    ax = fl.canvas(fig, 0, 0, W, H)

    # where a program comes from, and who repairs it
    st.block(fig, ax, 34, 1.5, 68, 10.5, "SIR program",
             "from the model's JSON, a builder or a skill.yaml", edge=kern)
    st.block(fig, ax, 1.5, 1.5, 25.5, 16.5, "ModelPlanner",
             "repairs from the codes, a bounded number of times", edge=kern)
    st.arrow(ax, [(27.0, 6.8), (34.0, 6.8)])

    # the eight passes
    y0, rh, bh = 17.0, 7.6, 6.0
    rows = []
    for i, name in enumerate(passes):
        y = y0 + i * rh
        st.block(fig, ax, 34, y, 68, bh, None, None, edge=kern)
        base = y + bh / 2 + 1.05            # one baseline for the name and its question
        st.text(ax, 35.4, base, f"{name}:", weight="bold", va="baseline")
        nw = fl.text_width(fig, f"{name}: ", st.TEXT, "bold")
        st.text(ax, 35.4 + nw, base, ASKS[name], va="baseline")
        st.step(ax, 34.0, y, i + 1, r=1.7, colour=kern)
        if i:
            st.arrow(ax, [(68.0, y - (rh - bh)), (68.0, y)])
        rows.append(y + bh / 2)
    st.arrow(ax, [(68.0, 12.0), (68.0, y0)])
    last = y0 + (len(passes) - 1) * rh + bh

    # diagnostics per family, aligned with the pass that emits them
    left, right = 122.0, W - 14.0
    a_top, a_bottom = y0 - 1.0, last + 1.0
    bax = st.axes(fig, left, a_top, right - left, a_bottom - a_top)
    c0 = y0 + bh / 2                       # row i is centred at c0 + i * rh mm
    bax.set_ylim((a_bottom - c0) / rh, (a_top - c0) / rh)
    bax.set_xlim(0, 18)
    for i, name in enumerate(passes):
        fam = sev[FAMILY[name]]
        e, w, n = fam.get("error", 0), fam.get("warning", 0), fam.get("info", 0)
        bax.barh(i, e, height=0.62, color=st.BLUE, lw=0)
        bax.barh(i, w, left=e, height=0.62, color=st.SKY, hatch="////", edgecolor="white",
                 lw=0)
        bax.barh(i, n, left=e + w, height=0.62, color="white", edgecolor=st.BLUE, lw=0.5)
        bax.text(e + w + n + 0.35, i, str(e + w + n), va="center", fontsize=st.TEXT)
    bax.set_yticks(range(len(passes)), [FAMILY[p] for p in passes])
    bax.set_xticks([0, 5, 10, 15])
    bax.set_xlabel(f"diagnostic codes ({inv['compiler_diagnostics']} in all)")
    st.grid(bax, "x")
    bax.tick_params(axis="y", which="both", length=0, right=False)
    bax.legend(handles=[__import__("matplotlib").patches.Patch(fc=st.BLUE, label="error: refuses"),
                        __import__("matplotlib").patches.Patch(fc=st.SKY, hatch="////",
                                                               ec="white", label="warning"),
                        __import__("matplotlib").patches.Patch(fc="white", ec=st.BLUE,
                                                               label="information")],
               loc="upper center", bbox_to_anchor=(0.5, -0.17), ncol=3, frameon=False,
               handlelength=1.4, columnspacing=1.0)
    for y in rows:
        ax.plot([102.0, left - 9.0], [y, y], color=st.GRID, lw=0.5, zorder=0,
                linestyle=(0, (1.0, 1.5)))

    # lowering, then the existing validator and loop
    yl = last + 6.0
    st.block(fig, ax, 34, yl, 68, 9.0, "Lowering to a Plan",
             "the compiler refuses, it never permits", edge=kern, fill=kblock)
    st.arrow(ax, [(68.0, last), (68.0, yl)])
    yv = yl + 13.0
    st.block(fig, ax, 34, yv, 68, 12.0, "PlanValidator",
             ", ".join(rt["plan_validator_families"]) + ": the whole plan, before any step",
             edge=kern)
    st.arrow(ax, [(68.0, yl + 9.0), (68.0, yv)])
    st.block(fig, ax, 112, yv, W - 112 - 3.0, 12.0, "AgentLoopController",
             f"{len(rt['task_kinds'])} task kinds through the ExecutionBroker", edge=kern)
    st.arrow(ax, [(102.0, yv + 6.0), (112.0, yv + 6.0)])

    # every refusal goes back to the planner with its codes
    bus = 30.5
    for y in rows + [yv + 6.0]:
        st.arrow(ax, [(34.0, y), (bus, y)], head=False, color=ref)
    ax.plot([bus, bus], [rows[0], yv + 6.0], color=ref, lw=0.75, zorder=3)
    st.arrow(ax, [(bus, rows[0]), (bus, 15.0), (27.0, 15.0)], color=ref)
    st.text(ax, bus - 2.2, (rows[3] + rows[4]) / 2, "refused: code, remedy and location",
            ha="center", va="center", style="italic", color=ref, rotation=90)
    return st.save(fig, "IEEE_Fig_Compiler")


if __name__ == "__main__":
    for p in main():
        print(p)
