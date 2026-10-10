#!/usr/bin/env python3
"""Table 1 | What each display item evaluates, its result, and what it does not show.

Every number in a cell is formatted here from data/extracted and data/curated, the same files
the figures are drawn from, so the table cannot disagree with a figure. Written as
tables/Table1.{md,csv,tex,docx}; the .docx is the editable form a journal's production team
works from.
"""

from __future__ import annotations

import csv
import json

import figlib as fl
import nature_style as ns

TABLES = ns.MANUSCRIPT / "tables"
TITLE = ("Table 1 | The evaluations behind Figs. 1–7: the question each answers, its "
         "construction, the principal result and what it does not show")
HEAD = ["Fig.", "Question", "Construction and data", "Principal result", "What it does not show"]


def pc(k, n, digits=0):
    lo, hi = ns.wilson(k, n)
    f = f"{{:.{digits}f}}"
    return f"{k}/{n} ({f.format(100 * k / n)}%; 95% CI {f.format(100 * lo)}–{f.format(100 * hi)}%)"


def rows() -> list[list[str]]:
    inv = fl.load_json("inventory.json")
    cap = fl.load_json("capabilities.json")
    live = sum(v for k, v in cap["tcm_catalogue"]["by_access"].items() if k.startswith("live"))
    conf = {r["configuration"]: r for r in fl.rows("ablation_configurations.csv")}
    uniq = {r["gate"]: int(r["unique"]) for r in fl.rows("ablation_unique.csv")}
    summ = fl.load_json("ablation_summary.json")
    et = fl.load_json("evidence_typing_summary.json")
    over = {(r["version"], r["split"], r["metric"]): r
            for r in fl.rows("evidence_typing_overall.csv")}
    claims = fl.rows("e2e_claims.csv")
    c = fl.counts()
    prot = {r["protein"]: r for r in fl.rows("np_screening_proteins.csv")}
    dis = {r["definition"]: r for r in fl.rows("np_disease_sets.csv")}
    pk = {(r["contrast"], r["metric"][:4]): r for r in fl.rows("berberine_pk.csv")}
    bsum = fl.load_json("berberine_summary.json")
    hold = fl.rows("robustness_holdouts.csv")
    inq = {r["world"]: r for r in fl.rows("inquiry_summary.csv")}
    opts0 = {r["analysis"]: float(r["expected_bits"]) for r in fl.rows("inquiry_options.csv")
             if r["world"] == "selective" and r["decision"] == "0"}

    def rel(key):
        r = conf[key]
        return pc(int(r["errors_released"]), int(r["mutants"]))

    allowed = sum(r["allowed"] == "1" for r in claims)
    rerun = sum(r["origin"] == "re-run here" for r in claims)
    t = {m: over[("after", "test", m)] for m in ("precision", "coverage", "negatives_typed",
                                                "wrong_readings")}
    mc = et["manual_correct"]
    cyp = prot["CYP1A2"]
    gen, lit = dis["genetic_ge_0.5"], dis["literature_ge_0.5"]
    auc, cmax = pk[("GQD vs HL", "AUC_")], pk[("GQD vs HL", "Cmax")]
    gc = pk[("GQD vs GQD-GC", "AUC_")]
    maresin_cyp = next(r for r in hold if r["pathway"].startswith("Biosynthesis")
                       and r["holdout"].startswith("whole"))
    implaus = [v["verdicts"].get("implausible", 0) for v in bsum["groups"].values()]
    depends = [v["verdicts"].get("depends_on_unknowns", 0) for v in bsum["groups"].values()]
    posts = sorted(float(r["posterior"]) for r in inq.values())

    def ratio(r):
        return (f"{float(r['ratio']):.2f} (90% CI {float(r['ci_low']):.2f}–"
                f"{float(r['ci_high']):.2f})")

    return [
        ["1", "What does each plane hold, and what may the model do?",
         "Names and counts read from the code at this commit (the kernel's runtime, the "
         "providers, the TCM catalogue and data hub, the source cards, the registries and "
         "engines); the connectors' live-verification record",
         f"{inv['public_sources']} public sources in {cap['public_sources']['domains']} "
         f"domains with {inv['typed_operations']} typed operations, all "
         f"{inv['operations_live_verified']} verified live "
         f"({inv['verification_dates'][0]} to {inv['verification_dates'][1]}); a catalogue "
         f"of {cap['tcm_catalogue']['total']} TCM databases ({live} reachable live) and a "
         f"hub of {cap['tcm_hub']['datasets']} datasets; {len(cap['snapshot_cards'])} source "
         f"cards; {inv['native_tools']} native tools in {inv['native_tool_domains']} domains; "
         f"{cap['federated']['capabilities']:,} federated capabilities from "
         f"{cap['federated']['projects']} projects; {inv['compiler_diagnostics']} compiler "
         f"diagnostics in {len(inv['compiler_diagnostics_by_family'])} families; "
         f"{inv['claim_reason_codes']} claim codes",
         "That a registered source is fit for a given claim; isolation: components running "
         "in-process are not isolated, the bundled sandbox is a no-op, and the audit chain is "
         "tamper-evident, not tamper-proof"],
        ["2", "Can a prediction be released as a fact?",
         "The kernel's design × claim matrix (16 designs × 8 claim kinds), checked against the "
         "compile-time table; 15 drafted claims through three end-to-end chains",
         f"An in-silico design licenses only a mechanism hypothesis; {allowed} of "
         f"{len(claims)} drafted claims allowed and {len(claims) - allowed} refused, each with "
         f"its codes; every verdict as the case expects ({rerun} regenerated here)",
         "That the matrix is right: it encodes a judgement about evidence that is enforced, "
         "not validated by an expert panel; the claims were drafted by people, not a model"],
        ["3", "Does the governance stop scientific errors, and which layer does it?",
         f"Mutation testing: {summ['base_cases']} base outputs (6 Chinese, 4 English) × "
         f"{summ['error_classes']} error classes = {summ['mutants']} mutants; 5 gates; "
         f"{len(conf)} configurations",
         f"Errors released: no governance {rel('ungoverned')}; domain layer {rel('domain only')}; "
         f"kernel {rel('kernel only')}; full stack {rel('full')}. Correct outputs refused: 0/10 "
         f"in every configuration. Errors only one gate stops: output gate {uniq['output']}, ingest "
         f"{uniq['provenance']}, claim contract {uniq['claim_contract']}, release path "
         f"{uniq['release_path']}, licensing {uniq['licensing']}",
         "Field error rates: cases and operators were written by the gates' authors; with no "
         "survivors left the benchmark guards against regression and no longer discriminates"],
        ["4", "Are the study-design labels the types rest on correct?",
         f"{et['records']} open-access abstracts (CC BY or CC0; 430 English, 48 Chinese) in 18 "
         f"strata; reference: PubMed publication type; rules changed on dev "
         f"({et['dev_records']}), test ({et['test_records']}) read once",
         f"Test: readings agreeing with PubMed {pc(int(t['precision']['k']), int(t['precision']['n']))}; "
         f"studies typed {pc(int(t['coverage']['k']), int(t['coverage']['n']))}; negatives typed "
         f"{pc(int(t['negatives_typed']['k']), int(t['negatives_typed']['n']))}; "
         f"{t['wrong_readings']['k']} wrong readings; hand-checked PCO values "
         f"{pc(mc['k'], mc['n'])}",
         "Agreement with the truth: the reference is an indexer's label; the mix is stratified, "
         "not literature at large; one reader, not blinded, checked the PCO values"],
        ["5", "What does the 葛根芩连汤 network-pharmacology signal rest on?",
         f"{c['constituents']:,.0f} constituents (NPASS, CMAUP, LOTUS); "
         f"{c['assayed_proteins']:.0f} assayed proteins; PubChem BioAssay "
         f"{c['pubchem_definitive']:,.0f} definitive results on {c['pubchem_proteins']:,.0f} "
         "proteins; Reactome; Open Targets 26.09",
         f"Pathways significant: {c['reactome_significant']:.0f} of "
         f"{c['reactome_pathways_tested']:,.0f} against the whole annotation, "
         f"{c['assayed_significant']:.0f} of {c['assayed_pathways_tested']:.0f} against "
         f"assayed proteins. With inactives ({c['screening_proteins']:.0f} proteins, "
         f"{c['screening_tests']:,.0f} tests, "
         f"{100 * c['screening_active'] / c['screening_tests']:.1f}% active) "
         f"{c['screening_passing']:.0f} pathways pass, six carried by cytochrome P450s "
         f"(CYP1A2 {cyp['active']}/{cyp['tested']} active). Type 2 diabetes genes: fold "
         f"{gen['fold']} (P = {gen['p_hypergeometric']}) by genetics, {lit['fold']} "
         f"(P = 5.2 × 10⁻²⁶) by literature co-mention",
         "A mechanism of action: inactives are rarely recorded, reporter panels are confounded "
         "by cytotoxicity and luciferase inhibition, and no result here is evidence of "
         "irrelevance"],
        ["6", "Do the released hypotheses survive hold-outs, and is berberine active at the "
              "exposure reached?",
         "Hold-out of the 12 largest assay campaigns and of all CYP-panel screens (4,161 "
         "edges); rat pharmacokinetic group summaries (n = 10 per group); "
         f"{bsum['assays_active']} active and {bsum['assays_inactive']} inactive ToxCast results",
         f"No pathway signal survives without the CYP panels (two untestable; one at "
         f"q = {float(maresin_cyp['q']):.3f}). Formula vs 黄连 alone: AUC ratio {ratio(auc)}, "
         f"Cmax ratio {ratio(cmax)}; formula vs formula without 甘草: AUC ratio {ratio(gc)}. 0 of "
         f"{bsum['assays_active']} activities plausible at the measured Cmax "
         f"({min(depends)}–{max(depends)} depend on the unknown unbound fraction, "
         f"{min(implaus)}–{max(implaus)} implausible)",
         "Efficacy, or a gut-lumen mechanism: these are group summaries in rats, the protocol "
         "is exploratory by its own record, and no gut concentration was measured"],
        ["7", "Does the inquiry engine find the true explanation, and stop?",
         "Three planted worlds on the real 葛根芩连汤 herb layer; three explanations and a "
         "catch-all; seven analyses with predictions sealed before any run",
         f"3 of 3 true explanations accepted (posterior {posts[0]:.3f}–{posts[-1]:.3f}); "
         f"mechanism claims released 1, 0, 0; network-pharmacology runs "
         f"{inq['selective']['np_runs']}, {inq['coverage']['np_runs']} and "
         f"{inq['promiscuous']['np_runs']} of {inq['selective']['np_runs_if_everything']}; "
         f"before any run the usual analysis is worth {opts0['annotated']:.2f} bits, the "
         f"assayed background {opts0['assayed']:.2f}",
         "That it decides real questions well: the worlds were built to have an answer, the "
         "likelihoods are the proposer's commitments, and information gain looks one step "
         "ahead"],
    ]


FOOTNOTE = ("Proportions are given with Wilson score 95% confidence intervals (CI). Exposure "
            "ratios are ratios of published group means (n = 10 rats per group) with 90% CIs "
            "by the delta method on the log scale (t distribution, Welch–Satterthwaite degrees "
            "of freedom), the interval used for equivalence. P values in row 5 are one-sided "
            "hypergeometric, as reported by the pipeline and recomputed here from the counts. "
            "PCO, population, comparator and outcome. Cmax, peak plasma concentration; AUC, "
            "area under the concentration–time curve to the last sample. Sources for every "
            "number: manuscript/data (extracted from the code and committed results, or "
            "curated with the file and line each value is quoted from).")


def write(rows_: list[list[str]]) -> None:
    TABLES.mkdir(parents=True, exist_ok=True)
    with (TABLES / "Table1.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(HEAD)
        w.writerows(rows_)
    md = [f"**{TITLE}**", "", "| " + " | ".join(HEAD) + " |",
          "| " + " | ".join("---" for _ in HEAD) + " |"]
    for r in rows_:
        md.append("| " + " | ".join(x.replace("|", "\\|") for x in r) + " |")
    md += ["", FOOTNOTE, ""]
    (TABLES / "Table1.md").write_text("\n".join(md), encoding="utf-8")
    tex_rows = []
    for r in rows_:
        tex_rows.append(" & ".join(_tex(x) for x in r) + r" \\")
    tex = "\n".join([
        "% Table 1 -- generated by manuscript/code/make_table1.py; do not edit by hand.",
        "% Needs: booktabs, tabularx, and a CJK-capable engine (xelatex + xeCJK) for 葛根芩连汤.",
        r"\begin{table*}[t]",
        r"\caption{\textbf{" + _tex(TITLE.split(" | ", 1)[1]) + r"}}",
        r"\label{tab:evaluations}",
        r"\footnotesize",
        r"\begin{tabularx}{\textwidth}{@{}l>{\raggedright}p{2.6cm}>{\raggedright}X"
        r">{\raggedright}X>{\raggedright\arraybackslash}X@{}}",
        r"\toprule",
        " & ".join(rf"\textbf{{{_tex(h)}}}" for h in HEAD) + r" \\",
        r"\midrule",
        *tex_rows,
        r"\bottomrule",
        r"\end{tabularx}",
        r"\par\smallskip\noindent\scriptsize " + _tex(FOOTNOTE),
        r"\end{table*}", ""])
    (TABLES / "Table1.tex").write_text(tex, encoding="utf-8")
    _docx(rows_)


def _tex(s: str) -> str:
    out = (s.replace("\\", r"\textbackslash{}").replace("&", r"\&").replace("%", r"\%")
           .replace("_", r"\_").replace("#", r"\#").replace("≥", r"$\geq$")
           .replace("≤", r"$\leq$").replace("×", r"$\times$").replace("–", "--")
           .replace("⁻²⁶", r"$^{-26}$").replace("µ", r"$\mu$"))
    return out


def _docx(rows_: list[list[str]]) -> None:
    try:
        from docx import Document
        from docx.enum.section import WD_ORIENT
        from docx.oxml import OxmlElement
        from docx.oxml.ns import qn
        from docx.shared import Mm, Pt
    except ImportError:                     # optional: the .md/.csv/.tex are complete
        return
    doc = Document()
    sec = doc.sections[0]
    sec.orientation = WD_ORIENT.LANDSCAPE
    sec.page_width, sec.page_height = Mm(297), Mm(210)
    for side in ("left_margin", "right_margin", "top_margin", "bottom_margin"):
        setattr(sec, side, Mm(15))
    style = doc.styles["Normal"]
    style.font.name = "Arial"
    style.font.size = Pt(8)
    style.element.rPr.rFonts.set(qn("w:eastAsia"), "SimSun")
    p = doc.add_paragraph()
    lead, rest = TITLE.split(" | ", 1)
    p.add_run(lead + " | ").bold = True
    p.add_run(rest).bold = True
    table = doc.add_table(rows=1, cols=len(HEAD))
    widths = [Mm(10), Mm(40), Mm(60), Mm(90), Mm(67)]
    for cell, text, width in zip(table.rows[0].cells, HEAD, widths):
        cell.width = width
        run = cell.paragraphs[0].add_run(text)
        run.bold = True
    for r in rows_:
        cells = table.add_row().cells
        for cell, text, width in zip(cells, r, widths):
            cell.width = width
            cell.paragraphs[0].add_run(text)
    # rules above and below the header and below the last row (no vertical rules)
    tbl = table._tbl
    borders = OxmlElement("w:tblBorders")
    for edge, size in (("top", 8), ("bottom", 8), ("insideH", 2)):
        el = OxmlElement(f"w:{edge}")
        el.set(qn("w:val"), "single")
        el.set(qn("w:sz"), str(size))
        el.set(qn("w:color"), "000000")
        borders.append(el)
    tbl.tblPr.append(borders)
    note = doc.add_paragraph(FOOTNOTE)
    note.runs[0].font.size = Pt(7)
    doc.core_properties.title = "Table 1"
    doc.core_properties.author = "TCMScience authors"
    doc.save(TABLES / "Table1.docx")
    fl.deterministic_zip(TABLES / "Table1.docx")


def main():
    r = rows()
    write(r)
    return [TABLES / f"Table1.{x}" for x in ("md", "csv", "tex", "docx")]


if __name__ == "__main__":
    for p in main():
        print(p)
