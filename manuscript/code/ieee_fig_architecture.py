#!/usr/bin/env python3
"""IEEE-style flowchart, two columns: the TCMScience agent architecture in full.

Every component is named as the code names it, and every count is read from the code
(data/extracted/inventory.json and capabilities.json, written by extract_data.py): the
compiler's passes and diagnostics, the plan validator's check families, the loop's task
kinds and stop reasons, the four stores, and what the capability plane holds. The
numbered steps are those the caption (ieee/captions.md) walks through.

    python manuscript/code/ieee_fig_architecture.py    # -> manuscript/ieee/IEEE_Fig_Architecture.*
"""

from __future__ import annotations

import figlib as fl
import ieee_style as st

W = st.TWO_COLUMN * 25.4          # 181.9 mm


def main():
    st.apply()
    inv = fl.load_json("inventory.json")
    cap = fl.load_json("capabilities.json")
    rt = inv["runtime"]
    assert rt["task_kinds"] == ["model", "tool", "delegate"], rt["task_kinds"]
    assert len(rt["stores"]) == 4, rt["stores"]
    H = 179.0
    fig = st.figure(st.TWO_COLUMN, H)
    ax = fl.canvas(fig, 0, 0, W, H)

    # ---------------------------------------------------------------- entry and the model
    st.region(ax, 0, 0, 117, 21, "Entry: a question, a program or a skill",
              title_size=st.NAME)
    ew = (117 - 3.2 - 3.2) / 3
    for i, (name, body) in enumerate([
            ("Command line", "psh; python -m bioagent.cli"),
            ("Python services", "ResearchRunService, ScientificRunService, run_governed"),
            ("Studio", "browser runtime (Pyodide) or local runner (tcmstudio)")]):
        st.block(fig, ax, 1.6 + i * (ew + 1.6), 5.2, ew, 14.2, name, body)
    st.block(fig, ax, 121, 3.0, W - 121, 14.0, "Language model (untrusted)",
             "proposes plans, programs, explanations, predictions and claims; "
             "holds no authority", style=(0, (3.0, 2.0)))

    # ------------------------------------------------------------------- trusted kernel
    KT, KB, KR = 24.0, 131.0, 146.0
    st.region(ax, 0, KT, KR, KB - KT, "Trusted kernel (PSH)", fill=st.FILL, lw=1.25,
              title_at="right")
    # K1: classify, plan, compile, validate
    y1, h1 = 31.0, 18.0
    st.block(fig, ax, 3, y1, 22, h1, "Ingress", "a Classifier labels every value")
    st.block(fig, ax, 30, y1, 27, h1, "Planner",
             "ModelPlanner proposes through call_model; bounded repair")
    passes = ", ".join(rt["compiler_passes"])
    st.block(fig, ax, 62, y1, 50, h1, "Scientific compiler",
             f"{passes}; {inv['compiler_diagnostics']} diagnostic codes; lowered to a Plan")
    st.block(fig, ax, 117, y1, 26, h1, "PlanValidator",
             ", ".join(rt["plan_validator_families"]))
    for x1, x2 in ((25, 30), (57, 62), (112, 117)):
        st.arrow(ax, [(x1, y1 + h1 / 2), (x2, y1 + h1 / 2)])
    # rejection returns to the planner with its codes
    yf = y1 + h1 + 3.2
    st.arrow(ax, [(121.5, y1 + h1), (121.5, yf), (46.5, yf), (46.5, y1 + h1)])
    ax.plot([87, 87], [y1 + h1, yf], color=st.BLACK, lw=0.75, zorder=3)
    st.text(ax, 84.0, yf + 2.2, "refused: diagnostics with code and remedy", ha="center",
            style="italic")
    # K2: the loop and the broker
    y2, h2 = 58.0, 26.0
    st.region(ax, 3, y2, 80, h2, "ExecutionBroker: the only way to act", title_size=st.NAME)
    gw = (80 - 3.2 - 3.2) / 3
    gates = [("ModelGateway", "to the model; ceilings per destination"),
             ("ToolGateway", "ExecPolicy and approvals; isolated execution"),
             ("DelegationGateway", "child loops under a narrowed envelope")]
    gx = []
    for i, (name, body) in enumerate(gates):
        x = 4.6 + i * (gw + 1.6)
        st.block(fig, ax, x, y2 + 5.2, gw, h2 - 6.8, name, body)
        gx.append(x)
    st.block(fig, ax, 93, y2, 50, h2, "AgentLoopController",
             f"plan, act, observe, evaluate ({', '.join(rt['evaluator_layers'])}); "
             f"{len(rt['loop_terminations'])} stop reasons; checkpoint and resume")
    st.arrow(ax, [(136.0, y1 + h1), (136.0, y2)])
    st.text(ax, 134.8, y2 - 2.6, "validated plan", ha="right", style="italic")
    st.arrow(ax, [(93, y2 + 9.0), (83, y2 + 9.0)])
    st.arrow(ax, [(83, y2 + 17.0), (93, y2 + 17.0)])
    st.text(ax, 88.0, y2 + 6.6, "calls", ha="center", style="italic")
    st.text(ax, 88.0, y2 + 19.6, "results", ha="center", style="italic")
    # lower left: the services every gate shares
    st.block(fig, ax, 3, 90, 25.5, 39, "Kernel services",
             ["AuthorityLattice", "BudgetGovernor", "ExecPolicy", "Hooks", "ContextCompiler",
              "EventStore", "EvidenceSigner"], sep="\n", align="left")
    # K3: one release path
    y3, h3 = 91.5, 15.0
    fw = (105 - 3 * 4.0) / 4
    fin = [("Evidence\ningestion", "signed records; labels joined"),
           ("Claim checks", "licensing by evidence scope; support verified"),
           ("Quarantine,\nOutputGate", "held, then checked"),
           ("Release gate", "one Finalizer for every entry")]
    fx = []
    for i, (name, body) in enumerate(fin):
        x = 38 + i * (fw + 4.0)
        st.block(fig, ax, x, y3, fw, h3, name, body)
        fx.append(x)
        if i:
            st.arrow(ax, [(x - 4.0, y3 + h3 / 2), (x, y3 + h3 / 2)])
    st.arrow(ax, [(104.0, y2 + h2), (104.0, 87.5), (fx[0] + fw / 2, 87.5),
                  (fx[0] + fw / 2, y3)])
    st.text(ax, fx[0] + fw / 2 + 2.0, 88.0, "candidate output", style="italic", va="top")
    # state: the four stores behind the PersistenceGateway, and the scientist plane
    y4, h4 = 110.0, 12.0
    names = {"git_workspace": ("Git\nworkspace", "authored"),
             "event_store": ("Event\nstore", "hash-chained"),
             "artifact_store": ("Artifact\nstore", "by content"),
             "index_store": ("Index\nstore", "WorkGraph")}
    sw = (74 - 3 * 1.6) / 4
    for i, key in enumerate(rt["stores"]):
        st.block(fig, ax, 38 + i * (sw + 1.6), y4, sw, h4, *names[key])
    pg = fx[3]
    st.block(fig, ax, 116, y4, 27, h4, "PersistenceGateway")
    st.arrow(ax, [(pg + fw / 2, y3 + h3), (pg + fw / 2, y4)])
    st.arrow(ax, [(116, y4 + h4 / 2), (112, y4 + h4 / 2)])
    st.strip(fig, ax, 38, 124.5, 105, 5.0, "Scientist plane",
             ["ScientificLedger", "ScientificWorldModel", "inquiry engine"])

    # ---------------------------------------------------- governance and the two outcomes
    gov = [("Skill contracts", f"skill.yaml compiled; {len(cap['skills']['stable_locked'])} "
                               f"locked, {len(cap['skills']['candidates'])} candidates"),
           ("Registry", "content-hashed lockfile; a person promotes"),
           ("Data contracts", "source card, evidence item, claim, artifact; "
                              f"CLM001–CLM{inv['claim_reason_codes']:03d}"),
           ("Policy", "PolicySnapshot; work-mode profiles"),
           ("Benchmarks", "governance ablation; evidence typing")]
    GH = 65.5
    st.region(ax, 151, KT, W - 151, GH, "Governance", style=(0, (4.0, 1.5, 1.0, 1.5)),
              title_size=st.NAME)
    st.text(ax, 152.6, KT + 5.0, "never executes", style="italic", va="top")
    y = KT + 8.8
    for name, body in gov:
        lines = fl.wrap(fig, body, W - 151 - 3.0, st.TEXT)
        st.text(ax, 152.4, y, name, weight="bold", va="top")
        st.text(ax, 152.4, y + st.LINE, "\n".join(lines), va="top")
        y += (1 + len(lines)) * st.LINE + 0.8
    assert y < KT + GH, y
    st.arrow(ax, [(151, 52.0), (KR, 52.0)], dashed=True)
    st.block(fig, ax, 151, y3 - 0.5, W - 151, 16.5, "Released result",
             "claim kind licensed; snapshots and chain head named", lw=1.25)
    st.block(fig, ax, 151, 110, W - 151, 19, "Refusal",
             "code and remedy; the text withheld")
    st.arrow(ax, [(fx[3] + fw, y3 + 5.0), (151, y3 + 5.0)])
    st.arrow(ax, [(fx[3] + fw, y3 + 11.5), (147.8, y3 + 11.5), (147.8, 119.5), (151, 119.5)])

    # --------------------------------------------------------------- capability plane
    CT = 136.0
    st.region(ax, 0, CT, W, H - 1.0 - CT, "Capability plane (BioScience-Harness)",
              note="reached only through the ToolGateway; nothing in it is trusted")
    st.strip(fig, ax, 3, CT + 5.6, W - 6, 7.4, "BioScienceBridge",
             ["each component becomes a PSH manifest", "BioScience's licence and lineage "
              "policy rules as well", "an isolated entrypoint"])
    pub = cap["public_sources"]
    tu, bm = cap["tooluniverse"], cap["biomcp"]
    caps = [("Public connectors", f"{pub['total']} sources, {pub['operations']} typed "
                                  f"operations, {pub['domains']} domains"),
            ("TCM data", f"hub of {cap['tcm_hub']['datasets']} datasets; catalogue of "
                         f"{cap['tcm_catalogue']['total']} databases; "
                         f"{cap['formula_table']['formulas']:,} formulas"),
            ("Snapshots", f"{len(cap['snapshot_cards'])} source cards; parse, normalize, "
                          "quality gate, hash, ledger"),
            ("Native tools", f"{inv['native_tools']} tools in {inv['native_tool_domains']} "
                             f"domains; {cap['engines']['admet_endpoints']} ADMET models; "
                             "governed skills"),
            ("Engines", "Boltz, Chai-1, ProteinMPNN, OpenMM, ESMFold, ColabFold, Vina, "
                        "PyDESeq2, Scanpy, PaperQA2"),
            ("Reviewed external", f"ToolUniverse {tu['version']}, BioMCP {bm['version']} "
                                  f"(draft), Biomni bindings; "
                                  f"{cap['federated']['capabilities']:,} catalogued")]
    cw = (W - 6 - 5 * 1.6) / 6
    for i, (name, body) in enumerate(caps):
        st.block(fig, ax, 3 + i * (cw + 1.6), CT + 15.6, cw, H - 1.0 - CT - 18.2, name, body)
    tx = gx[1]
    st.arrow(ax, [(tx + 2.5, y2 + h2), (tx + 2.5, CT + 5.6)])
    st.arrow(ax, [(tx + 5.5, CT + 5.6), (tx + 5.5, y2 + h2)])
    st.text(ax, tx + 1.4, KB + 2.6, "call_tool", ha="right", style="italic")
    st.text(ax, tx + 6.6, KB + 2.6, "labelled result", ha="left", style="italic")

    # ------------------------------------------------------------------ entry and model
    st.arrow(ax, [(14.0, 21.0), (14.0, y1)])
    st.arrow(ax, [(43.5, y1), (43.5, 22.6), (130.0, 22.6), (130.0, 17.0)], both=True)
    st.text(ax, 131.4, 19.8, "call_model, via the ModelGateway", style="italic")
    st.text(ax, 45.0, 27.6, "proposal", style="italic")

    # ----------------------------------------------------------------- numbered steps
    for n, (x, y) in enumerate([(10.4, 26.6), (27.5, y1 + h1 / 2 - 4.0),
                                (40.0, 26.6), (59.5, y1 + h1 / 2 - 4.0),
                                (139.6, y2 - 4.6), (88.0, y2 + 12.9),
                                (tx - 14.2, KB + 2.6), (98.6, 87.5),
                                (148.6, y3 + 1.0), (pg + fw / 2 - 3.6, y4 - 2.0)], start=1):
        st.step(ax, x, y, n)
    return st.save(fig, "IEEE_Fig_Architecture")


if __name__ == "__main__":
    for p in main():
        print(p)
