#!/usr/bin/env python3
"""Fig. 1 | TCMScience separates what a model may propose from what the system may release.

Every name and count on the figure is read from the code at this commit, not from prose:
data/extracted/inventory.json (the kernel's runtime shape, the compiler's diagnostics, the
providers and native tools) and capabilities.json, public_sources.csv and the other
inventory files (the source cards, the TCM catalogue and data hub, the federated
catalogue, the engines and the reviewed external tools), all written by extract_data.py.
The README's inventory lags the code; manuscript/README.md lists where.
"""

from __future__ import annotations

import collections

import figlib as fl
import nature_style as ns

# Display names for the public sources, keyed by the source's key in bioagent.providers.
# Shortened only by dropping the access route ("REST API", "GraphQL", "site JSON
# endpoints"); release numbers that name a resource are kept. Source Data gives every
# full name. A source missing here stops the build rather than appearing unnamed.
SHORT = {
    "civic": "CIViC", "gdc": "NCI GDC", "cbioportal": "cBioPortal",
    "cellosaurus": "Cellosaurus",
    "chebi": "ChEBI 2.0", "chembl": "ChEMBL", "pubchem": "PubChem",
    "clinicaltrials": "ClinicalTrials.gov", "dailymed": "DailyMed", "orphadata": "Orphadata",
    "rxnav": "RxNorm", "openfda": "openFDA",
    "huggingface": "Hugging Face Hub", "hf_datasets_server": "HF datasets-server",
    "figshare": "figshare",
    "dgidb": "DGIdb", "opentargets": "Open Targets",
    "encode": "ENCODE",
    "biostudies": "BioStudies", "gtex": "GTEx", "proteinatlas": "Human Protein Atlas",
    "fourdn": "4D Nucleome", "chip_atlas": "ChIP-Atlas", "biosamples": "BioSamples",
    "ena": "ENA", "ensembl": "Ensembl", "gwas_catalog": "GWAS Catalog", "hgnc": "HGNC",
    "impc": "IMPC", "mavedb": "MaveDB", "mygene": "MyGene.info", "myvariant": "MyVariant.info",
    "ncbi_datasets": "NCBI Datasets", "rnacentral": "RNAcentral", "remap": "ReMap 2022",
    "ucsc": "UCSC Genome Browser", "gnomad": "gnomAD",
    "idr": "Image Data Resource", "idc": "Imaging Data Commons", "openneuro": "OpenNeuro",
    "tcia_cm": "TCIA Collection Manager", "tcia": "TCIA NBIA",
    "iedb": "IEDB", "vdjserver_adc": "VDJServer", "ireceptor_adc": "iReceptor",
    "crossref": "Crossref", "ebi_search": "EBI Search", "europepmc": "Europe PMC",
    "europepmc_annotations": "Europe PMC Annotations", "ncbi_eutils": "NCBI E-utilities",
    "openalex": "OpenAlex", "pubtator": "PubTator 3", "biorxiv": "bioRxiv/medRxiv",
    "intact": "IntAct/Complex Portal", "jaspar": "JASPAR", "signor": "SIGNOR",
    "mesh": "MeSH", "nlm_clinical_tables": "NLM Clinical Tables",
    "metanetx": "MetaNetX", "rhea": "Rhea", "sabio_rk": "SABIO-RK",
    "metabolights": "MetaboLights", "metabolomics_workbench": "Metabolomics Workbench",
    "bv_brc": "BV-BRC",
    "mgnify": "MGnify", "gutmgene": "gutMGene 2.0",
    "coconut": "COCONUT 2.0", "gbif": "GBIF", "knapsack": "KNApSAcK", "mibig": "MIBiG",
    "phytohub": "PhytoHub", "wikidata_sparql": "Wikidata",
    "bioregistry": "Bioregistry", "disease_ontology": "Disease Ontology", "ols": "OLS4",
    "hpo": "HPO", "identifiers_org": "Identifiers.org", "monarch": "Monarch",
    "quickgo": "QuickGO",
    "kegg": "KEGG", "omnipath": "OmniPath", "panther": "PANTHER", "reactome": "Reactome",
    "wikipathways": "WikiPathways", "gprofiler": "g:Profiler",
    "cpic": "CPIC", "pharmacodb": "PharmacoDB",
    "drugcentral": "DrugCentral", "gpcrdb": "GPCRdb", "pharos": "Pharos", "ttd": "TTD",
    "disprot": "DisProt", "interpro": "InterPro", "pride": "PRIDE",
    "proteomicsdb": "ProteomicsDB", "string": "STRING", "uniprot": "UniProt",
    "cellxgene": "CELLxGENE", "hubmap_portal": "HuBMAP Portal",
    "hubmap_entity": "HuBMAP Entity", "hubmap_search": "HuBMAP Search",
    "hca_azul": "HCA Data Portal",
    "gnps": "GNPS libraries", "gnps_explorer": "GNPS2 explorer", "massbank": "MassBank3",
    "massive": "MassIVE", "gnps_usi": "USI resolver",
    "alphafold": "AlphaFold DB", "klifs": "KLIFS", "pdbe": "PDBe", "rcsb": "RCSB PDB",
    "dcabm_tcm": "DCABM-TCM", "herb_api": "HERB 2.0", "itcm": "ITCM", "symmap": "SymMap v2",
    "tcmbank": "TCMBank",
    "aopwiki": "AOP-Wiki",
}
DOMAIN = {"tcm": "TCM", "cell-biology": "Cell biology", "single-cell": "Single cell"}
CARD = {"lotus": "LOTUS (frozen export)", "npass": "NPASS 2.0", "cmaup": "CMAUP 2.0",
        "bindingdb": "BindingDB", "string": "STRING v12 (human)", "reactome": "Reactome",
        "pubchem_bioassay": "PubChem BioAssay", "opentargets": "Open Targets Platform"}
LICENCE = {"CC-BY-4.0": "CC BY 4.0", "CC-BY-3.0": "CC BY 3.0", "CC0-1.0": "CC0 1.0",
           "Not stated": "not stated", "NCBI-data-policy": "NCBI data policy"}
TOOL_DOMAIN = {"clinical-calculators": "Clinical calculators", "statistics": "Statistics",
               "sequence-analysis": "Sequence analysis", "tcm-knowledge": "TCM knowledge",
               "pharmacology": "Pharmacology", "file-formats": "File formats",
               "phylogenetics": "Phylogenetics", "variant-analysis": "Variant analysis",
               "protein-analysis": "Protein analysis",
               "population-genetics": "Population genetics",
               "sequence-alignment": "Sequence alignment",
               "survival-analysis": "Survival analysis"}
ACCESS = [("live_api+snapshot", "Live API and snapshot"), ("live_api", "Live API"),
          ("snapshot", "Snapshot"), ("manual_import", "Manual import"),
          ("restricted", "Restricted"), ("unreachable", "Unreachable")]
KIND = {"tool": "Tools", "skill": "Skills", "database": "Databases",
        "benchmark": "Benchmarks", "software": "Software", "dataset": "Datasets",
        "agent_role": "Agent roles"}

BODY = 5.0 * 1.18 * 25.4 / 72      # one line of 5 pt text at linespacing 1.18, in mm


def domain_name(d: str) -> str:
    return DOMAIN.get(d, d.replace("-", " ").capitalize())


# --------------------------------------------------------------------------- panel a
def _band(ax, x, y, w, h, colour, title, note):
    fl.box(ax, x, y, w, h, fc=ns.tint(colour, 0.07), ec=colour, lw=0.6, r=1.2)
    fl.label(ax, x + 2.2, y + 2.6, title, size=ns.SMALL, weight="bold")
    fl.label(ax, x + w - 2.2, y + 2.6, note, size=ns.TINY, ha="right", color=ns.INK2,
             style="italic")


def _cells(fig, ax, x, y, w, h, colour, items, gap=1.6, weights=None):
    """A row of cells, each a bold title over a body wrapped to the cell's width."""
    n = len(items)
    weights = weights or [1.0] * n
    unit = (w - gap * (n - 1)) / sum(weights)
    out = []
    cx = x
    for i, (title, body) in enumerate(items):
        cw = unit * weights[i]
        fl.box(ax, cx, y, cw, h, fc="white", ec=colour, lw=0.5, r=0.7)
        fl.label(ax, cx + cw / 2, y + 2.5, title, size=ns.SMALL, weight="bold", ha="center")
        lines = fl.wrap(fig, body, cw - 1.8, ns.TINY)
        if 4.4 + len(lines) * BODY > h + 0.2:
            raise SystemExit(f"Fig. 1a: {title!r} needs {len(lines)} lines in a {h} mm cell")
        fl.label(ax, cx + cw / 2, y + 4.3, "\n".join(lines), size=ns.TINY, ha="center",
                 va="top", color=ns.INK2, linespacing=1.18)
        out.append((cx, cw))
        cx += cw + gap
    return out


def _strip(fig, ax, x, y, w, h, colour, head, body, lw=0.4):
    fl.box(ax, x, y, w, h, fc="white", ec=colour, lw=lw, r=0.7)
    fl.label(ax, x + 2.0, y + h / 2, head, size=ns.TINY, weight="bold")
    hw = fl.text_width(fig, head, ns.TINY, "bold")
    lines = fl.wrap(fig, body, w - hw - 6.0, ns.TINY, sep=" · ")
    fl.label(ax, x + 4.0 + hw, y + h / 2, "\n".join(lines), size=ns.TINY, color=ns.INK2,
             linespacing=1.18)


def panel_a(fig, inv, cap):
    rt = inv["runtime"]
    ax = fl.canvas(fig, 0, 5, 183, 82.0)
    X0, W = 26.0, 131.5
    skills = cap["skills"]
    # governance layer
    _band(ax, X0, 0, W, 17.6, ns.ORANGE, "Governance layer",
          "constrains the kernel; never executes")
    _cells(fig, ax, X0 + 2, 4.7, W - 4, 11.6, ns.ORANGE, [
        ("Skill contracts", f"skill.yaml compiled to a ScientificProgram; "
                            f"{len(skills['stable_locked'])} skills locked, "
                            f"{len(skills['candidates'])} candidates"),
        ("Data contracts", "source card → evidence item → candidate claim → artifact; "
                           f"CLM001–CLM{inv['claim_reason_codes']:03d}"),
        ("Registry", "content-hashed lockfile; a scout proposes, a person promotes"),
        ("Policy", "one frozen PolicySnapshot per run; work-mode profiles; ExecPolicy"),
        ("Benchmarks", "governance ablation (Fig. 3); evidence typing (Fig. 4)")])
    # kernel
    KY, KH = 21.4, 34.8
    _band(ax, X0, KY, W, KH, ns.BLUE, "Trusted kernel (PSH)",
          "no manifest can replace it; the model cannot rewrite it")
    k = _cells(fig, ax, X0 + 2, KY + 4.7, W - 4, 13.6, ns.BLUE, [
        ("Ingress", "IngressGateway: a Classifier labels every value at entry"),
        ("Compiler", f"{len(rt['compiler_passes'])} passes, "
                     f"{inv['compiler_diagnostics']} codes; lowering to a Plan"),
        ("PlanValidator", f"{len(rt['plan_validator_families'])} check families; "
                          "an AuthorityLattice that can only narrow"),
        ("Agent loop", f"AgentLoopController: bounded; {len(rt['task_kinds'])} task kinds, "
                       f"{len(rt['loop_terminations'])} stop reasons"),
        ("ExecutionBroker", "model, tool and delegation gateways; isolated tool processes"),
        ("Release", "Quarantine, OutputGate and release gate, through one Finalizer")])
    for (ax0, aw), (bx0, _) in zip(k[:-1], k[1:]):
        fl.arrow(ax, ax0 + aw + 0.05, KY + 11.5, bx0 - 0.05, KY + 11.5, head=1.6, lw=0.5)
    _strip(fig, ax, X0 + 2, KY + 19.8, W - 4, 6.2, ns.BLUE, "Kernel services",
           ["EventStore: every gate's decision on a hash-chained audit",
            "BudgetGovernor: nested budgets", "ExecPolicy and approvals",
            "PersistenceGateway to the WorkGraph"])
    _strip(fig, ax, X0 + 2, KY + 27.2, W - 4, 6.2, ns.BLUE, "Scientist plane",
           ["ScientificLedger: hypotheses, preregistered protocols and observations "
            "as content-hashed records", "ScientificWorldModel", "inquiry engine (Fig. 7)"])
    # capability plane
    CY, CH = KY + KH + 4.6, 20.8
    pub = cap["public_sources"]
    eng = cap["engines"]
    _band(ax, X0, CY, W, CH, ns.GREEN,
          "Capability plane (BioScience-Harness), admitted through BioScienceBridge",
          "a call crosses both kernels")
    _cells(fig, ax, X0 + 2, CY + 4.7, W - 4, CH - 5.9, ns.GREEN, [
        ("Public sources", f"{pub['total']} sources, {pub['operations']} typed operations "
                           f"in {pub['domains']} domains, all verified live (b)"),
        ("TCM data", f"a hub of {cap['tcm_hub']['datasets']} datasets; a catalogue of "
                     f"{cap['tcm_catalogue']['total']} TCM databases; "
                     f"{cap['formula_table']['formulas']:,} formulas (d)"),
        ("Snapshots", f"{len(cap['snapshot_cards'])} source cards with parsers; "
                      "content-hashed, on a ledger (c)"),
        ("Tools and engines", f"{inv['native_tools']} native tools in "
                              f"{inv['native_tool_domains']} domains (d); Boltz, Chai-1, "
                              "ProteinMPNN, OpenMM, ESMFold, ColabFold, AutoDock Vina, "
                              f"PyDESeq2, Scanpy, PaperQA2; {eng['admet_endpoints']} "
                              "ADMET models"),
        ("Reviewed external tools", f"ToolUniverse {cap['tooluniverse']['version']} "
                                    f"({len(cap['tooluniverse']['tools'])} tools); BioMCP "
                                    f"{cap['biomcp']['version']} "
                                    f"({len(cap['biomcp']['tools'])} tools, in draft review); "
                                    f"Biomni ({len(cap['bindings'])} bindings)")],
        weights=[1.0, 1.0, 1.0, 1.75, 1.3])
    # the model, and what leaves
    fl.box(ax, 0.3, KY, 16.9, KH, fc=ns.WASH, ec=ns.MUTED, lw=0.6, r=1.2,
           style=(0, (2.2, 1.4)))
    fl.label(ax, 8.75, KY + 4.4, "Language\nmodel", size=ns.SMALL, weight="bold",
             ha="center", linespacing=1.05)
    fl.label(ax, 8.75, KY + 16.6, "proposes\nplans,\nexplanations,\npredictions\n"
             "and claims", size=ns.TINY, ha="center", color=ns.INK2, linespacing=1.18)
    fl.label(ax, 8.75, KY + 29.6, "holds no\nauthority", size=ns.TINY, ha="center",
             color=ns.INK, style="italic", linespacing=1.1)
    fl.arrow(ax, 17.4, KY + 13.0, X0 - 0.1, KY + 13.0, lw=0.7, head=1.8)
    fl.arrow(ax, X0 - 0.1, KY + 17.4, 17.4, KY + 17.4, lw=0.7, head=1.8)
    fl.label(ax, (17.3 + X0) / 2, KY + 11.4, "proposals", size=ns.TINY, ha="center",
             color=ns.INK2)
    fl.label(ax, (17.3 + X0) / 2, KY + 18.8, "labelled\ncontext", size=ns.TINY,
             ha="center", va="top", color=ns.INK2, linespacing=1.05)
    out_x = X0 + W + 3.4
    out_w = 182.7 - out_x
    fl.box(ax, out_x, KY, out_w, 15.4, fc="white", ec=ns.INK, lw=0.6, r=1.0)
    fl.label(ax, out_x + out_w / 2, KY + 2.6, "Released\nclaim", size=ns.SMALL,
             weight="bold", ha="center", va="top", linespacing=1.0)
    fl.label(ax, out_x + out_w / 2, KY + 7.9, "its kind licensed;\nsnapshots and\n"
             "chain head named", size=ns.TINY, ha="center", va="top", color=ns.INK2,
             linespacing=1.15)
    fl.box(ax, out_x, KY + 18.6, out_w, KH - 18.6, fc="white", ec=ns.MUTED, lw=0.6, r=1.0)
    fl.label(ax, out_x + out_w / 2, KY + 21.3, "Refusal", size=ns.SMALL, weight="bold",
             ha="center")
    fl.label(ax, out_x + out_w / 2, KY + 23.8, "its code and\nremedy; the text\nwithheld",
             size=ns.TINY, ha="center", va="top", color=ns.INK2, linespacing=1.15)
    rx, rw = k[5]
    fl.arrow(ax, rx + rw - 1.2, KY + 6.0, out_x - 0.3, KY + 7.4, lw=0.7, head=1.8,
             connection="arc3,rad=-0.2")
    fl.arrow(ax, rx + rw - 1.2, KY + 16.8, out_x - 0.3, KY + 25.6, lw=0.7, head=1.8,
             connection="arc3,rad=0.2")
    # the broker reaches the capability plane; governance constrains the kernel
    bx, bw = k[4]
    mid = bx + bw / 2
    for dx, y1, y2 in ((-1.8, KY + KH + 0.2, CY + 4.5), (1.8, CY + 4.5, KY + KH + 0.2)):
        fl.arrow(ax, mid + dx, y1, mid + dx, y2, lw=0.7, head=1.8)
    fl.label(ax, mid - 2.8, KY + KH + 2.3, "call_tool", size=ns.TINY, ha="right",
             color=ns.INK2)
    fl.label(ax, mid + 2.8, KY + KH + 2.3, "labelled results", size=ns.TINY, ha="left",
             color=ns.INK2)
    for cx in (X0 + 22, X0 + 48, X0 + 74, X0 + 100, X0 + 126):
        fl.arrow(ax, cx, 16.5, cx, KY - 0.1, lw=0.6, dashed=True, color=ns.ORANGE, head=1.8)
    return 5 + CY + CH


# --------------------------------------------------------------------------- panel b
def _partition(heights: list[float], k: int) -> list[int]:
    """Split a sequence into ``k`` consecutive runs minimising the tallest (column breaks)."""
    n = len(heights)
    prefix = [0.0]
    for h in heights:
        prefix.append(prefix[-1] + h)
    best = [[float("inf")] * (n + 1) for _ in range(k + 1)]
    cut = [[0] * (n + 1) for _ in range(k + 1)]
    best[0][0] = 0.0
    for j in range(1, k + 1):
        for i in range(1, n + 1):
            for m in range(j - 1, i):
                cost = max(best[j - 1][m], prefix[i] - prefix[m])
                if cost < best[j][i]:
                    best[j][i], cut[j][i] = cost, m
    bounds, i = [], n
    for j in range(k, 0, -1):
        bounds.append(i)
        i = cut[j][i]
    return sorted(bounds)


def panel_b_layout(fig, sources, ncol=6, gap=1.6, width=183.0):
    by_domain = collections.defaultdict(list)
    for s in sources:
        if s["key"] not in SHORT:
            raise SystemExit(f"Fig. 1b: no display name for public source {s['key']!r}")
        by_domain[s["domain"]].append(s)
    order = sorted(by_domain, key=lambda d: (-len(by_domain[d]), d))
    cw = (width - gap * (ncol - 1)) / ncol
    boxes = []
    for d in order:
        names = sorted((SHORT[s["key"]] for s in by_domain[d]), key=str.casefold)
        lines = fl.wrap(fig, names, cw - 2.0, ns.TINY)
        ops = sum(int(s["operations"]) for s in by_domain[d])
        boxes.append({"domain": d, "lines": lines, "n": len(names), "ops": ops,
                      "h": 4.9 + len(lines) * BODY})
    ends = _partition([b["h"] + gap for b in boxes], ncol)
    cols, start = [], 0
    for end in ends:
        cols.append(boxes[start:end])
        start = end
    height = max(sum(b["h"] + gap for b in col) - gap for col in cols)
    return cols, cw, height


def panel_b(fig, top, layout, pub, inv):
    cols, cw, height = layout
    ax = fl.canvas(fig, 0, top, 183, height + 0.4)
    gap = 1.6
    for i, col in enumerate(cols):
        x, y = i * (cw + gap), 0.2
        for b in col:
            tcm = b["domain"] == "tcm"
            fl.box(ax, x, y, cw, b["h"], fc=ns.tint(ns.GREEN, 0.16 if tcm else 0.05),
                   ec=ns.GREEN if tcm else ns.tint(ns.GREEN, 0.55), lw=0.6 if tcm else 0.4,
                   r=0.6)
            fl.label(ax, x + 1.0, y + 2.2, domain_name(b["domain"]), size=ns.TINY,
                     weight="bold")
            fl.label(ax, x + cw - 1.0, y + 2.2, f"{b['n']} · {b['ops']}", size=ns.TINY,
                     ha="right", color=ns.INK2)
            fl.label(ax, x + 1.0, y + 3.9, "\n".join(b["lines"]), size=ns.TINY, va="top",
                     color=ns.INK, linespacing=1.18)
            y += b["h"] + gap
    return ax


# --------------------------------------------------------------------------- panel c
def panel_c(fig, top, cap, roles):
    ax = fl.canvas(fig, 2.5, top, 96, 58)
    steps = [("Provider\nrelease", "source card:\nlicence and access"),
             ("Parse", "per-source parser;\ndrops counted"),
             ("Normalize", "to Swiss-Prot,\nInChIKey, MONDO"),
             ("Quality gate", "fails closed"),
             ("Content hash", "key@version#\nsha256[:12]"),
             ("Ledger", "hash-chained;\nverified on load")]
    w, gap = 13.8, 2.6
    for _, body in steps:
        widest = max(fl.text_width(fig, line, ns.TINY) for line in body.split("\n"))
        assert widest < w + gap - 1.0, body
    for i, (title, body) in enumerate(steps):
        x = i * (w + gap)
        fl.box(ax, x, 0.5, w, 8.0, fc=ns.tint(ns.GREEN, 0.10), ec=ns.GREEN, lw=0.5, r=0.7)
        fl.label(ax, x + w / 2, 4.5, title, size=ns.TINY, weight="bold", ha="center",
                 linespacing=1.0)
        fl.label(ax, x + w / 2, 9.8, body, size=ns.TINY, ha="center", va="top",
                 color=ns.INK2, linespacing=1.15)
        if i < len(steps) - 1:
            fl.arrow(ax, x + w + 0.1, 4.5, x + w + gap - 0.1, 4.5, head=1.6, lw=0.5)
    # the source cards a snapshot can be built from
    y0 = 16.6
    cols = [(0.0, "Source card"), (33.0, "Access"), (49.0, "Licence on the card"),
            (72.0, "Role in the 葛根芩连汤 study")]
    for x, head in cols:
        fl.label(ax, x + 0.8, y0, head, size=ns.TINY, weight="bold")
    ax.plot([0, 95.5], [y0 + 1.5, y0 + 1.5], color=ns.RULE, lw=0.4)
    row = 2.25
    for j, c in enumerate(cap["snapshot_cards"]):
        y = y0 + 3.3 + j * row
        role = roles.get(c["key"], "")
        fl.label(ax, 0.8, y, CARD[c["key"]], size=ns.TINY)
        fl.label(ax, 33.8, y, ", ".join(c["access"]), size=ns.TINY, color=ns.INK2)
        fl.label(ax, 49.8, y, LICENCE.get(c["licence"], c["licence"]), size=ns.TINY,
                 color=ns.INK if c["licence"] != "Not stated" else ns.VERMILLION)
        fl.label(ax, 72.8, y, role or "not used", size=ns.TINY,
                 color=ns.INK2 if role else ns.MUTED, style="normal" if role else "italic")
    yb = y0 + 3.3 + len(cap["snapshot_cards"]) * row - 0.6
    ax.plot([0, 95.5], [yb, yb], color=ns.RULE, lw=0.4)
    for i, (head, body) in enumerate([
            ("A run's sources", "declaration ∩ registry ∩ policy: a skill can narrow its "
                                "sources, never widen them"),
            ("A run's lock", "names each snapshot by id; a rerun on other content under "
                             "the same label is refused")]):
        y = yb + 1.5 + i * 5.0
        fl.label(ax, 0.8, y, head, size=ns.TINY, weight="bold", va="top")
        lines = fl.wrap(fig, body, 95.5 - 19.0, ns.TINY)
        fl.label(ax, 18.0, y, "\n".join(lines), size=ns.TINY, color=ns.INK2, va="top",
                 linespacing=1.18)
    return yb + 1.5 + 5.0 + 2.0 * 2.08


# --------------------------------------------------------------------------- panel d
def _hbars(ax, labels, values, colours, xmax, ticks, *, fmt="{:,}", stacked=None):
    n = len(labels)
    for i, (v, c) in enumerate(zip(values, colours)):
        ax.barh(i, v, height=0.68, color=c, lw=0)
        if stacked is not None:
            ax.barh(i, stacked[i], left=v, height=0.68, color="white",
                    edgecolor=c, lw=0.5)
        total = v + (stacked[i] if stacked is not None else 0)
        ax.text(total + xmax * 0.02, i, fmt.format(total), va="center", fontsize=ns.TINY,
                color=ns.INK)
    ax.set_yticks(range(n), labels)
    ax.tick_params(axis="y", length=0, pad=1.5, labelsize=ns.TINY)
    ax.tick_params(axis="x", labelsize=ns.TINY)
    ax.set_ylim(n - 0.45, -0.6)
    ax.set_xlim(0, xmax)
    ax.set_xticks(ticks)
    ax.spines["left"].set_visible(False)
    ns.hairline_grid(ax, "x")


def panel_d(fig, top, inv, cap):
    # the catalogue of TCM databases, by how each is reached
    acc = cap["tcm_catalogue"]["by_access"]
    ax = ns.axes_mm(fig, 125, top + 4.2, 17, 14.0)
    reach = [ns.GREEN, ns.GREEN, ns.tint(ns.GREEN, 0.6), ns.tint(ns.GREEN, 0.6),
             ns.HAIR, ns.HAIR]
    _hbars(ax, [lab for _, lab in ACCESS], [acc[k] for k, _ in ACCESS], reach, 46,
           [0, 20, 40])
    ax.set_title(f"TCM databases ({cap['tcm_catalogue']['total']})", fontsize=ns.TINY,
                 fontweight="bold", loc="right", pad=2.5)
    # the federated catalogue, by kind and by how it may be used
    fed = fl.rows("federated_catalogue_kinds.csv")
    bx = ns.axes_mm(fig, 125, top + 26.6, 17, 16.4)
    _hbars(bx, [KIND[r["kind"]] for r in fed], [int(r["vendor"]) for r in fed],
           [ns.tint(ns.GREEN, 0.6)] * len(fed), 1800, [0, 800, 1600],
           stacked=[int(r["adapter_only"]) for r in fed])
    f = cap["federated"]
    leg = fl.canvas(fig, 104, top + 47.4, 40, 3)
    for x, fill, text in ((2.0, ns.tint(ns.GREEN, 0.6), "may be vendored"),
                          (22.0, "white", "adapter only")):
        fl.box(leg, x, 0.6, 2.4, 1.8, fc=fill, ec=ns.tint(ns.GREEN, 0.6), lw=0.5, r=0)
        fl.label(leg, x + 3.2, 1.5, text, size=ns.TINY, color=ns.INK2)
    bx.set_title(f"Federated catalogue ({f['capabilities']:,}; {f['projects']} projects)",
                 fontsize=ns.TINY, fontweight="bold", loc="right", pad=2.5)
    # native tools by domain
    doms = sorted(inv["native_tools_by_domain"].items(), key=lambda kv: -kv[1])
    cx = ns.axes_mm(fig, 166, top + 4.2, 14.5, 39.8)
    _hbars(cx, [TOOL_DOMAIN[d] for d, _ in doms], [v for _, v in doms],
           [ns.GREEN if d == "tcm-knowledge" else ns.tint(ns.GREEN, 0.6) for d, _ in doms],
           66, [0, 30, 60])
    cx.set_title(f"Native tools ({inv['native_tools']})", fontsize=ns.TINY,
                 fontweight="bold", loc="right", pad=2.5)
    return ax, bx, cx


def main():
    ns.apply()
    inv = fl.load_json("inventory.json")
    cap = fl.load_json("capabilities.json")
    assert inv["operations_live_verified"] == inv["typed_operations"], "unverified operations"
    assert cap["public_sources"]["total"] == inv["public_sources"]
    sources = fl.rows("public_sources.csv")
    roles = {r["key"]: r["role"] for r in fl.rows("case_study_sources.csv")}
    probe = ns.figure(ns.DOUBLE, 50)
    layout = panel_b_layout(probe, sources)
    import matplotlib.pyplot as plt
    plt.close(probe)
    top_b = 5 + 82.0 + 7.6
    top_c = top_b + layout[2] + 9.0
    height = top_c + 51.5
    fig = ns.figure(ns.DOUBLE, height)
    bottom_a = panel_a(fig, inv, cap)
    assert bottom_a < top_b - 5.5, bottom_a
    panel_b(fig, top_b, layout, cap["public_sources"], inv)
    key = fl.canvas(fig, 100, top_b - 4.6, 83, 3)
    fl.label(key, 83, 1.5, "per domain: sources · typed operations", size=ns.TINY,
             ha="right", color=ns.INK2)
    panel_c(fig, top_c, cap, roles)
    panel_d(fig, top_c, inv, cap)
    for letter, x, y in (("a", 0, 1.0), ("b", 0, top_b - 4.6), ("c", 0, top_c - 4.6),
                         ("d", 101, top_c - 4.6)):
        ns.panel_label(fig, letter, x, y)
    return ns.save(fig, "Fig1")


if __name__ == "__main__":
    for p in main():
        print(p)
