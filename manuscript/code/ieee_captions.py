#!/usr/bin/env python3
"""The IEEE-style figures' captions, written once and rendered twice.

``write()`` produces ieee/captions.md (for reading) and ieee/captions.tex (IEEEtran
``figure*`` and ``figure`` environments, the two-column figures at ``\\textwidth`` and the
one-column ones at ``\\columnwidth``, both of which equal the widths the figures were drawn
at). Counts in the captions are read from the same extracted data as the figures.
"""

from __future__ import annotations

import figlib as fl
import ieee_style as st

COLOURS = ("Colour has one meaning throughout: blue, the trusted kernel and what it "
           "releases; green, the capability plane; orange, governance; reddish purple, the "
           "scientist plane; vermillion, refusals; grey and dashed, the untrusted model.")


def figures() -> list[tuple[str, str, str, str]]:
    """(file stem, width, label, caption) in the order the figures take in a paper."""
    inv = fl.load_json("inventory.json")
    cap = fl.load_json("capabilities.json")
    rule = inv["inquiry_stopping_rule"]
    pub, fed = cap["public_sources"], cap["federated"]
    d0, d1 = (date(d) for d in inv["verification_dates"])
    two, one = "two", "one"
    return [
        ("IEEE_Fig_Architecture", two, "fig:architecture",
         "The TCMScience agent architecture, with each component named as in the code. "
         "(1) A request enters through the command line, the Python services or Studio and "
         "is labelled at ingress. (2) It passes to the planner, which (3) obtains a proposal "
         "from the language model only through call_model and the ModelGateway. (4) The "
         "scientific compiler checks the proposed program in eight passes "
         f"({inv['compiler_diagnostics']} diagnostic codes; Fig. 4) and lowers it to a typed "
         "plan; a refused program returns to the planner with each diagnostic's code and "
         "remedy, for a bounded number of repairs. (5) The PlanValidator checks the whole "
         "plan before any step runs. (6) The AgentLoopController acts only through the "
         "ExecutionBroker's three calls (model, tool, delegate) and stops for one of "
         f"{len(inv['runtime']['loop_terminations'])} named reasons. (7) Tool calls cross the "
         "BioScienceBridge into the capability plane and return as labelled results. (8) "
         "The loop's candidate output passes evidence ingestion and claim checks and is held "
         "in quarantine and checked by the OutputGate; (9) it leaves only through one release "
         "gate, as a result whose claim kind its evidence licenses or as a refusal with its "
         "code and remedy. (10) Validated state is committed through the PersistenceGateway "
         "to four stores, and every gate's decision is appended to the hash-chained event "
         "store. Governance constrains the kernel and never executes. " + COLOURS),
        ("IEEE_Fig_Minimal", one, "fig:minimal",
         "TCMScience in brief. The language model only proposes. The trusted kernel "
         "compiles, authorizes, executes and verifies every step, calls the model and the "
         "tools itself, and releases a claim only of a kind its evidence licenses; otherwise "
         "it refuses, with a code and a remedy. Every decision is appended to a hash-chained "
         "audit. Dashed box, untrusted; dashed arrow, a record."),
        ("IEEE_Fig_ExecutionPath", two, "fig:execution-path",
         "The trusted path one request takes through Runner.run, in the frozen order of its "
         f"{len(inv['runtime']['runner_stages'])} stages. Two orderings are kernel "
         "invariants: the request is classified before anything is written (stages 2 to 4), "
         "and output is released before anything is committed to project memory (stages 14 "
         "and 15). From stage 11 to stage 14 the output is held in quarantine and cannot be "
         "read; stage 14 is the only release. Every stage runs inside one refusal scope: "
         "if the kernel raises any of the seven refusals named, the run closes at that "
         "stage, the quarantined output is refused and the refusal is audited, so a run that "
         "stopped releases nothing. A single pass records validate_plan; typed plans are "
         "validated by the PlanValidator in the agent loop (Fig. 4)."),
        ("IEEE_Fig_Compiler", two, "fig:compiler",
         "The scientific compiler. A program in the scientific intermediate representation "
         "(SIR), written by the model as JSON, by a builder or compiled from a skill "
         "contract, passes eight passes in order, each emitting one family of diagnostics "
         f"(bars: the {inv['compiler_diagnostics']} codes by family and severity; an error "
         "refuses the program, a warning is reported). The structure pass stops the pipeline "
         "on error, because the later passes assume an acyclic graph. A program that "
         "compiles is lowered to the typed Plan that the PlanValidator checks as a whole "
         "(graph, authority, dataflow, budget, scientific) before the AgentLoopController "
         "runs any step. Every refusal returns to the ModelPlanner with its code, remedy and "
         "location, for a bounded number of repairs. The compiler can only refuse: it grants "
         "nothing the runtime would not."),
        ("IEEE_Fig_Snapshots", two, "fig:snapshots",
         "How third-party data enter, as audited snapshots. A source card states the "
         "licence and the access paths in order of preference (bulk, API, manual import, "
         "web; web only after a person has approved it). A release is fetched, each raw file "
         "hashed; parsed into node and edge tables, dropped rows counted; normalized to "
         "InChIKey, Swiss-Prot and MONDO identifiers; and passed through a quality gate: a "
         "structural or licence problem fails the snapshot, and coverage below the card's "
         "minimum or drift above "
         f"{cap['snapshot_qc_max_drift']:.0%} (the default) sends it to a person for "
         "review. The snapshot id is the key, the version and the first 12 hexadecimal "
         "digits of the SHA-256 of the manifest's content (raw files, parser code, tables, "
         "quality result, licence; nothing time-dependent); it is recorded on an append-only, "
         "hash-chained ledger, and every load re-hashes the tables and checks the id and the "
         f"ledger head. Below, the {len(cap['snapshot_cards'])} source cards, their access "
         "paths, the licence each card states and their role in the Gegen Qinlian decoction "
         "study. A run may use only the sources its skill declares, the cards enable and its "
         "policy allows, and its lock names every snapshot and the ledger head it read."),
        ("IEEE_Fig_InquiryLoop", one, "fig:inquiry-loop",
         "How the inquiry engine decides its next step (Inquiry.next_step), with the default "
         "stopping rule. The model proposes rival explanations with a catch-all whose prior "
         f"is at least {rule['catch_all_floor']:.2f}, analyses with their costs, and what "
         "each explanation predicts each analysis will show, sealed before it runs. The "
         f"engine stops at {rule['max_steps']} steps; asks for new explanations, carved from "
         f"the catch-all, once the catch-all holds {rule['catch_all_alarm']:.2f} or more; "
         f"when a leader reaches {rule['accept_at']:.2f}, runs the severe test (severity at "
         f"least {rule['min_severity']:.1f}) or the replication it still owes against a live "
         "rival, and accepts the leader when none is owed; and otherwise runs the analysis "
         "with the most expected information per unit cost, if one offers at least "
         f"{rule['min_gain']} bits (analyses without sealed predictions must have them "
         "declared first). Belief moves only by the sealed predictions, and a design that "
         "cannot license an explanation's claim kind leaves that explanation fixed (INQ113). "
         "A stopped inquiry is provisional or undetermined; an accepted one is stated no more "
         "strongly than the licensing table allows."),
        ("IEEE_Fig_Inventory", two, "fig:inventory",
         "What the capability plane holds, as the code registers it. (a) The "
         f"{pub['total']} public sources the connectors reach and their {pub['operations']} "
         f"typed operations, by domain ({pub['domains']} domains; TCM in the darker shade); "
         f"every operation was verified against its live service between {d0} and {d1}. (b) "
         f"The catalogue of {cap['tcm_catalogue']['total']} TCM databases by how each can be "
         f"reached. (c) The federated catalogue of {fed['capabilities']:,} capabilities from "
         f"{fed['projects']} projects by kind; hatched, capabilities of projects whose licence "
         "does not permit copying, which an adapter invokes in place. (d) The "
         f"{inv['native_tools']} native tools by domain."),
        ("IEEE_Fig_Licensing", one, "fig:licensing",
         "Which study design licenses which kind of claim (the kernel's matrix, "
         "psh.sir.values.LICENSING): directly (filled), only by an extrapolation the claim "
         "must declare (open), or not at all (dot); right, the strongest wording one study of "
         "the design licenses. An in-silico design (docking, network pharmacology, "
         "simulation) licenses a mechanism hypothesis and nothing stronger (shaded). Building "
         "the figure checks that every pair the compile-time table admits (EVIDENCE103) is a "
         "direct cell of this matrix."),
        ("IEEE_Fig_Ablation", two, "fig:ablation",
         "Which layer of governance stops which scientific error. Ten outputs a reviewer "
         "would release were each altered by one known error at a time, giving 121 mutants in "
         "26 error classes; five gates run as the runtime calls them, three in the kernel and "
         "two in the domain layer, and an output is released when no enabled gate refuses "
         "it. (a) Erroneous outputs released by each of 14 configurations (bars, 95% Wilson "
         "intervals; n = 121 each); no configuration refused any of the ten correct outputs. "
         "(b) Errors that one gate alone stops. (c) Errors the full stack released in the "
         "benchmark's three rounds, each gap closed by a change to a runtime check. (d) Share "
         "of each error class's mutants refused by each gate, each layer and the full stack "
         "(n per class in parentheses). The cases and operators were written by the authors "
         "of the gates: these are regression measurements, not field error rates."),
        ("IEEE_Fig_EvidenceTyping", two, "fig:evidence-typing",
         "Reading study designs from real abstracts. 478 open-access abstracts were frozen "
         "before any rule read them and split by a seeded hash of the PMID; the rules were "
         "revised on the dev split (176) only, and the test split (302) was read once by each "
         "version. (a) Test split: PubMed publication type against the design the rules "
         "read; outlined, a correct reading, which for the negatives (narrative reviews, "
         "editorials, trial protocols) means abstaining. (b) Precision and recall for each "
         "design on the test split, before and after the revision (bars, 95% Wilson "
         "intervals). (c) Readings correct, studies typed and negatives typed, before and "
         "after, on test and dev. The reference is an indexer's label, not the truth."),
        ("IEEE_Fig_CaseStudy", two, "fig:case-study",
         "The Gegen Qinlian decoction on public data. (a) Pathways significant after "
         "Benjamini-Hochberg correction and a degree-matched permutation null, against the "
         "whole human Reactome annotation and against the proteins actually assayed. (b) The "
         "carbonic-anhydrase pathway, all 12 of whose members were assayed and 11 hit: fold "
         "enrichment under each background; dashed, the largest fold any pathway can reach "
         "on the assayed background (391/237). (c) Overlap of the 237 measured targets with "
         "type 2 diabetes genes (Open Targets 26.09) under five definitions of the gene set, "
         "and on the assayed background (square); P, one-sided hypergeometric, recomputed "
         "from the counts. Literature co-mention is circular: target data and text mining "
         "study the same proteins. (d) Share of tested constituents active against each "
         "protein in the screening data (bars, 95% Wilson intervals); dashed, all 472 "
         "proteins tested against at least 20 constituents."),
        ("IEEE_Fig_Inquiry", two, "fig:inquiry",
         "The inquiry engine on three planted worlds, each built on the real herb layer of "
         "the Gegen Qinlian decoction and making one explanation true. (a) to (c) The "
         "posterior of each explanation after each analysis; where two posteriors coincide "
         "exactly, the first is drawn with a pale casing so both stay visible; dashed, the "
         "acceptance threshold. The true explanation was accepted in every world, and a "
         "mechanism hypothesis, stated tentatively, was released only in the selective world. "
         "(d) The expected information of each analysis before any run, in total and per "
         "network-pharmacology run: enrichment against the whole annotation is worth 0.06 "
         "bits, because every explanation predicts that it comes out enriched; the assayed "
         "background is worth 0.43. The worlds are fixtures with a known answer, and every "
         "posterior is conditional on the sealed predictions."),
    ]


def date(iso: str) -> str:
    """2026-09-17 -> 17 September 2026."""
    import datetime
    d = datetime.date.fromisoformat(iso)
    return f"{d.day} {d.strftime('%B')} {d.year}"


_TEX = {"≥": r"$\geq$", "≤": r"$\leq$", "×": r"$\times$", "→": r"$\rightarrow$",
        "↔": r"$\leftrightarrow$", "_": r"\_", "%": r"\%", "&": r"\&", "#": r"\#"}


def tex(text: str) -> str:
    return "".join(_TEX.get(ch, ch) for ch in text)


def refs(text: str, figs) -> str:
    """"Fig. N" in a caption becomes a LaTeX reference to that figure's label."""
    import re
    return re.sub(r"Fig\. (\d+)", lambda m: "Fig.~\\ref{" + figs[int(m.group(1)) - 1][2] + "}",
                  text)


def write() -> list:
    figs = figures()
    md = ["# IEEE-style figures: captions", "",
          "Twelve figures in the style of the IEEE Transactions, in the order they take in a "
          "paper: four architecture flowcharts, two process flowcharts and six plots of "
          "results. Two-column figures are 7.16 in wide, one-column figures 3.5 in. Each is a "
          "PDF and an EPS (vector, fonts embedded) and a 600 dpi PNG; `captions.tex` holds "
          "the same captions in IEEEtran environments. Every name and number is read from "
          "the code and the committed results, as for the Nature figures.", "", "---", ""]
    lines = ["% IEEE-style figures for an IEEEtran journal paper, in order.",
             "% \\textwidth is 43 pc (7.16 in) and \\columnwidth 21 pc (3.5 in) in IEEEtran's",
             "% journal mode, the widths the figures were drawn at.", ""]
    for n, (stem, width, label, caption) in enumerate(figs, start=1):
        md += [f"**Fig. {n}.** {caption}", "",
               f"*File:* `{stem}.pdf` ({'two columns' if width == 'two' else 'one column'})",
               ""]
        env = "figure*" if width == "two" else "figure"
        size = r"\textwidth" if width == "two" else r"\columnwidth"
        lines += [f"\\begin{{{env}}}[!t]", "\\centering",
                  f"\\includegraphics[width={size}]{{{stem}.pdf}}",
                  f"\\caption{{{refs(tex(caption), figs)}}}", f"\\label{{{label}}}",
                  f"\\end{{{env}}}", ""]
    st.OUT.mkdir(parents=True, exist_ok=True)
    (st.OUT / "captions.md").write_text("\n".join(md).rstrip() + "\n", encoding="utf-8")
    (st.OUT / "captions.tex").write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return figs


if __name__ == "__main__":
    st.apply()
    for f in write():
        print(f[0])
