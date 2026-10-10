#!/usr/bin/env python3
"""Fig. 2 | Study designs are types: which evidence licenses which claim, and where it is checked.

Panel a is drawn from data/extracted/licensing_matrix.csv and max_certainty.csv, read from
psh.sir.values (LICENSING, MAX_CERTAINTY), the matrix the scientific compiler's TYP rules
and the inquiry engine enforce. The script also checks that the workflow compiler's binary
table (psh.workflow.compiler._SUPPORTS, EVIDENCE103) is a subset of the matrix's direct
cells, so the two cannot disagree without the figure failing to build.
Panel c: data/extracted/e2e_claims.csv (cases 1 and 2 re-run here; case 3 as recorded).
"""

from __future__ import annotations

import matplotlib.patches as mpatches

import figlib as fl
import nature_style as ns

CLAIMS = [("attribution", "Attribution", "记载"), ("traditional_use", "Traditional\nuse", "传统应用"),
          ("mechanism_hypothesis", "Mechanism\nhypothesis", "机制假说"),
          ("mechanism", "Mechanism", "机制"), ("safety_signal", "Safety\nsignal", "安全信号"),
          ("association", "Association", "关联"), ("efficacy", "Efficacy", "疗效"),
          ("recommendation", "Recommendation", "推荐")]
GROUPS = [("Tradition", [("classical_text", "Classical text"), ("commentary", "Commentary"),
                         ("expert_consensus", "Expert consensus")]),
          ("Computation", [("in_silico", "In silico")]),
          ("Bench", [("in_vitro", "In vitro"), ("animal", "Animal")]),
          ("Observation", [("case_report", "Case report"), ("case_series", "Case series"),
                           ("cross_sectional", "Cross-sectional"),
                           ("case_control", "Case–control"), ("cohort", "Cohort")]),
          ("Trial", [("non_randomised_trial", "Non-randomised trial"),
                     ("randomised_trial", "Randomised trial")]),
          ("Synthesis", [("systematic_review", "Systematic review"), ("guideline", "Guideline")]),
          ("", [("unknown", "Design unknown")])]
CERTAINTY = ["uncertain", "tentative", "moderate", "strong"]


def check_tables(grade):
    """Every (claim, design) pair the workflow compiler admits must be direct in the matrix."""
    claim_map = {"classical_attribution": "attribution", "traditional_use": "traditional_use",
                 "mechanism": "mechanism", "mechanism_hypothesis": "mechanism_hypothesis",
                 "association": "association", "clinical_efficacy": "efficacy",
                 "safety_signal": "safety_signal"}
    design_map = {"classical_text": ["classical_text"], "expert_consensus": ["expert_consensus"],
                  "in_vitro": ["in_vitro"], "animal": ["animal"],
                  "case_report": ["case_report"],
                  "observational": ["cross_sectional", "case_control", "cohort"],
                  "randomized_trial": ["randomised_trial"],
                  "systematic_review": ["systematic_review"]}
    checked = 0
    for r in fl.rows("compiler_supports.csv"):
        if r["licensed"] != "1":
            continue
        kind = claim_map[r["claim_type"]]
        designs = ["in_silico"] if r["predictive"] == "1" else design_map[r["design"]]
        for d in designs:
            assert grade[(kind, d)] == "direct", (r["claim_type"], r["design"], d)
            checked += 1
    # and the invariant the figure is about: a prediction licenses a hypothesis, nothing more
    assert [k for k, *_ in CLAIMS if grade[(k, "in_silico")] != "unlicensed"] == \
        ["mechanism_hypothesis"]
    return checked


def panel_a(fig):
    grade = {(r["claim_kind"], r["design"]): r["grade"] for r in fl.rows("licensing_matrix.csv")}
    cert = {r["design"]: r["max_certainty"] for r in fl.rows("max_certainty.csv")}
    check_tables(grade)
    left, top, cw, rh = 30.0, 26.0, 8.6, 2.95
    rows, labels, heads = [], [], []
    for g, members in GROUPS:
        heads.append((len(rows), g))
        for key, name in members:
            rows.append(key)
            labels.append(name)
    gap_after = {i for i, _ in heads[1:]}
    ys, y = [], 0.0
    for i in range(len(rows)):
        if i in gap_after:
            y += 0.45
        ys.append(y)
        y += 1
    height = (ys[-1] + 1) * rh
    ax = ns.axes_mm(fig, left, top, cw * (len(CLAIMS) + 1.6), height)
    ax.set_xlim(-0.5, len(CLAIMS) + 1.1)
    ax.set_ylim(ys[-1] + 0.6, -0.6)
    ax.set_axis_off()
    j_ins = rows.index("in_silico")
    ax.add_patch(mpatches.FancyBboxPatch((-0.45, ys[j_ins] - 0.46), len(CLAIMS) - 0.1, 0.92,
                                         boxstyle="round,pad=0,rounding_size=0.25",
                                         fc=ns.tint(ns.VERMILLION, 0.12), ec="none", zorder=0))
    for i, d in enumerate(rows):
        y = ys[i]
        for j, (k, _, _) in enumerate(CLAIMS):
            g = grade[(k, d)]
            if g == "direct":
                ax.plot(j, y, "o", ms=4.4, mfc=ns.BLUE, mec=ns.BLUE, mew=0.6, zorder=3)
            elif g == "extrapolated":
                ax.plot(j, y, "o", ms=4.0, mfc="white", mec=ns.BLUE, mew=0.8, zorder=3)
            elif not (d == "in_silico" and j >= 3):     # under the annotation
                ax.plot(j, y, "o", ms=1.4, mfc=ns.RULE, mec="none", zorder=3)
        ax.text(-0.75, y, labels[i], ha="right", va="center", fontsize=ns.TINY, color=ns.INK)
        level = CERTAINTY.index(cert[d])
        x0 = len(CLAIMS) + 0.05
        for s in range(1, 4):
            ax.add_patch(mpatches.Rectangle((x0 + (s - 1) * 0.3, y - 0.3), 0.24, 0.6,
                                            fc=ns.INK2 if s <= level else ns.HAIR, ec="none"))
        ax.text(x0 + 0.98, y, cert[d], ha="left", va="center", fontsize=ns.TINY,
                color=ns.INK2)
    margin = __import__("matplotlib").transforms.blended_transform_factory(fig.transFigure,
                                                                           ax.transData)
    for i, g in heads:
        if g:
            ax.text(1.2 / 183, ys[i], g, ha="left", va="center", fontsize=ns.TINY,
                    fontweight="bold", color=ns.INK2, transform=margin)
    for j, (_, en, zh) in enumerate(CLAIMS):
        ax.text(j - 0.1, -0.85, f"{en.replace(chr(10), ' ').replace('- ', '')} {zh}",
                ha="left", va="bottom", rotation=38, rotation_mode="anchor",
                fontsize=ns.TINY, color=ns.INK, fontweight="bold" if j == 2 else "normal")
    ax.text(len(CLAIMS) + 0.05, -0.85, "Strongest\nwording", ha="left", va="bottom",
            fontsize=ns.TINY, color=ns.INK, linespacing=1.0)
    ax.text(3.45, ys[j_ins], "a prediction licenses only a hypothesis",
            fontsize=ns.TINY, color=ns.INK, va="center", ha="left")
    kax = fl.canvas(fig, left - 26, top + height + 3.0, 110, 3)
    items = [("o", 4.4, ns.BLUE, ns.BLUE, "licenses directly"),
             ("o", 4.0, "white", ns.BLUE, "only by extrapolation, which must be declared"),
             ("o", 1.4, ns.RULE, "none", "cannot license")]
    x = 0
    for marker, ms, fc, ec, text in items:
        kax.plot([x + 1], [1.5], marker, ms=ms, mfc=fc, mec=ec, mew=0.7)
        fl.label(kax, x + 2.6, 1.5, text, size=ns.TINY)
        x += {"licenses directly": 22, "cannot license": 0}.get(text, 52)


def panel_b(fig):
    ax = fl.canvas(fig, 124, 6, 58.6, 80)
    stages = [
        ("Plan", "compile time", "EVIDENCE103, TYP",
         "a skill or programme whose evidence steps\ncannot license its declared claim kind\n"
         "is refused before it spends anything"),
        ("Belief", "inquiry", "INQ113",
         "a design that cannot license an explanation's\nclaim kind cannot carry a "
         "prediction about it,\nso it cannot move belief in it"),
        ("Claim", "claim contract", "CLM005",
         "each cited item must license the claim\nkind, the weakest link first; scope, "
         "population\nand wording are checked as well"),
        ("Release", "release gate", "snapshot path",
         "every edge on the claim's path must exist in\na verified snapshot; the strongest "
         "kind the\npath supports is recorded, with the reason"),
    ]
    y = 0.5
    for i, (name, where, code, body) in enumerate(stages):
        fl.box(ax, 0, y, 58.2, 16.2, fc="white", ec=ns.BLUE, lw=0.5, r=0.8)
        ax.add_patch(mpatches.FancyBboxPatch((0, y), 13.0, 16.2,
                                             boxstyle="round,pad=0,rounding_size=0.8",
                                             fc=ns.tint(ns.BLUE, 0.12), ec="none", zorder=2))
        fl.label(ax, 6.5, y + 6.2, name, size=ns.SMALL, weight="bold", ha="center")
        fl.label(ax, 6.5, y + 9.0, where, size=ns.TINY, ha="center", color=ns.INK2)
        fl.label(ax, 15.0, y + 3.0, code, size=ns.TINY, weight="bold", color=ns.INK)
        fl.label(ax, 15.0, y + 5.0, body, size=ns.TINY, va="top", color=ns.INK2,
                 linespacing=1.18)
        if i < len(stages) - 1:
            fl.arrow(ax, 6.5, y + 16.3, 6.5, y + 19.1, lw=0.6)
        y += 19.2


SHORT = {
    ("rnaseq", 1): "Treatment T raised a planted gene's expression in these cells",
    ("rnaseq", 2): "T may act through the PLANTED_UP pathway (a hypothesis to test)",
    ("rnaseq", 3): "T activates the PLANTED_UP pathway",
    ("rnaseq", 4): "T may act through it at concentrations reached in patients",
    ("rnaseq", 5): "T improves outcomes in patients",
    ("literature", 1): "葛根芩连汤 lowers HbA1c in adults with type 2 diabetes (bias unassessed)",
    ("literature", 2): "The same claim, after a reviewer assessed the trial's risk of bias",
    ("literature", 3): "The same, in children with type 2 diabetes",
    ("literature", 4): "The same, “with no apparent adverse reactions” (未见明显不良反应)",
    ("literature", 5): "Empagliflozin reduced heart-failure hospitalization (passage untyped)",
    ("literature", 6): "Higher serum C-telopeptide was associated with mortality (≥ 65 years)",
    ("compound", 1): "Docking suggests 4-aminobenzamidine may bind trypsin (a hypothesis)",
    ("compound", 2): "4-Aminobenzamidine inhibits human trypsin-1",
    ("compound", 3): "4-Aminobenzamidine reduces attacks of hereditary pancreatitis",
    ("compound", 4): "It may bind trypsin at concentrations reached in patients",
}
CITES = {("rnaseq", 1): "fold change (in vitro)", ("rnaseq", 2): "enrichment (prediction)",
         ("rnaseq", 3): "enrichment (prediction)", ("rnaseq", 4): "enrichment (prediction)",
         ("rnaseq", 5): "enrichment (prediction)",
         ("literature", 1): "randomized trial", ("literature", 2): "randomized trial",
         ("literature", 3): "randomized trial", ("literature", 4): "randomized trial",
         ("literature", 5): "untyped passage", ("literature", 6): "observational",
         ("compound", 1): "docking", ("compound", 2): "docking", ("compound", 3): "docking",
         ("compound", 4): "docking"}
KIND = {"mechanism": "mechanism", "mechanism_hypothesis": "mechanism hypothesis",
        "efficacy": "efficacy", "association": "association"}
CASES = [("rnaseq", "Case 1 · RNA-seq counts → differential expression → preranked GSEA"),
         ("literature", "Case 2 · literature → PaperQA2 passages → typed evidence items"),
         ("compound", "Case 3 · compound → Open Targets → docking and ADMET")]


def panel_c(fig):
    claims = fl.rows("e2e_claims.csv")
    reasons = fl.load_json("inventory.json")["claim_reasons"]
    ax = fl.canvas(fig, 2, 94, 181, 58)
    cols = {"verdict": 0.0, "text": 4.0, "kind": 92.0, "cites": 115.0, "codes": 143.0}
    for name, x in (("Drafted claim", cols["text"]), ("Claim kind", cols["kind"]),
                    ("Cites", cols["cites"]), ("Refusal codes", cols["codes"])):
        fl.label(ax, x, 1.0, name, size=ns.TINY, weight="bold", color=ns.INK)
    ax.plot([0, 181], [2.6, 2.6], color=ns.INK2, lw=0.5)
    y = 4.4
    used = []
    for case, title in CASES:
        fl.label(ax, cols["text"], y, title, size=ns.TINY, style="italic", color=ns.INK2)
        y += 2.7
        for r in [c for c in claims if c["case"] == case]:
            key = (case, int(r["order"]))
            allowed = r["allowed"] == "1"
            if allowed:
                ax.plot([1.6], [y], "o", ms=3.2, mfc=ns.BLUE, mec=ns.BLUE, mew=0.5)
            else:
                ax.plot([1.6], [y], "o", ms=3.2, mfc="white", mec=ns.VERMILLION, mew=0.8)
                ax.plot([1.6], [y], "x", ms=1.8, mec=ns.VERMILLION, mew=0.7)
            fl.label(ax, cols["text"], y, SHORT[key], size=ns.TINY)
            fl.label(ax, cols["kind"], y, KIND[r["kind"]], size=ns.TINY, color=ns.INK2)
            fl.label(ax, cols["cites"], y, CITES[key], size=ns.TINY, color=ns.INK2)
            x = cols["codes"]
            for code in [c for c in r["codes"].split(";") if c]:
                fl.box(ax, x, y - 1.05, 8.6, 2.1, fc=ns.tint(ns.VERMILLION, 0.14), ec="none",
                       r=0.5)
                fl.label(ax, x + 4.3, y, code, size=ns.TINY, ha="center", color=ns.INK)
                x += 9.4
                used.append(code)
            y += 2.55
        y += 0.6
    ax.plot([0, 181], [y - 1.0, y - 1.0], color=ns.INK2, lw=0.5)
    used = sorted(set(used))
    short = {"CLM002": "cited item not present", "CLM004": "clinical claim on prediction only",
             "CLM005": "evidence cannot license the claim kind", "CLM006": "evidence quality "
             "blocks it", "CLM009": "undeclared extrapolation", "CLM013": "scope is not what "
             "the evidence covers", "CLM017": "human exposure never measured",
             "CLM019": "absence never tested"}
    for code in used:
        assert code in reasons, code
    text = " · ".join(f"{c} {short[c]}" for c in used)
    half = len(used) // 2
    fl.label(ax, 4.0, y + 1.0, " · ".join(f"{c} {short[c]}" for c in used[:half]),
             size=ns.TINY, color=ns.INK2)
    fl.label(ax, 4.0, y + 3.4, " · ".join(f"{c} {short[c]}" for c in used[half:]),
             size=ns.TINY, color=ns.INK2)
    kx = 143.0
    ax.plot([kx + 1.0], [y + 2.2], "o", ms=3.2, mfc=ns.BLUE, mec=ns.BLUE)
    fl.label(ax, kx + 2.6, y + 2.2, "allowed", size=ns.TINY)
    ax.plot([kx + 15.0], [y + 2.2], "o", ms=3.2, mfc="white", mec=ns.VERMILLION, mew=0.8)
    ax.plot([kx + 15.0], [y + 2.2], "x", ms=1.8, mec=ns.VERMILLION, mew=0.7)
    fl.label(ax, kx + 16.6, y + 2.2, "refused", size=ns.TINY)
    return text


def main():
    ns.apply()
    fig = ns.figure(ns.DOUBLE, 156)
    panel_a(fig)
    panel_b(fig)
    panel_c(fig)
    for letter, x, y in (("a", 0, 1.5), ("b", 119, 1.5), ("c", 0, 88.5)):
        ns.panel_label(fig, letter, x, y)
    return ns.save(fig, "Fig2")


if __name__ == "__main__":
    for p in main():
        print(p)
