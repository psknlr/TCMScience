"""Drugs, their targets and drug-response screens (supplementary sources, review of
2026-09-30; every URL checked from the harness on 2026-10-01).

* **DrugCentral** (catalogue 105): the drug-target interaction file. Each row is one
  activity or mechanism-of-action record for a drug on a protein, with the database or
  document it came from. It becomes ``drug_target`` rows. The structures file gives the
  InChIKey of each drug for the consensus crosswalk.
* **DrugComb** (106): drug-combination synergy scores and the drug and cell-line
  identifier tables, from the authors' Zenodo deposits. The synergy table is a screen
  result for the analysis layer and is kept as a file, not turned into relations.
* **GDSC** (108): the fitted dose-response tables of GDSC1 and GDSC2 (analysis layer, kept
  as files), the screened-compound annotation (``compound_target`` from the putative
  targets) and the Cell Model Passports model list.
* **Pharos** (109): the Pharos400 target development levels (Tclin, Tchem, Tbio, Tdark)
  and IDG families of the canonical human proteins, as ``gene_set_member`` rows.

PharmacoDB (107) has no files of its own; it is a live connector only
(``providers.supplement.drugs``).
"""

from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Iterator

from ..rowkit import Row, ctx, has, names, rel, rows, unresolved, v
from ..spec import DatasetSpec, FileSpec
from ..store import open_text

__all__ = ["DATASETS", "EXTRACTORS", "READERS"]


# ------------------------------------------------------------------------------ readers
def _clean(row: list[str]) -> list[str | None]:
    out: list[str | None] = []
    for value in row:
        text = (value or "").strip().strip("\r")
        out.append(text if text else None)
    return out


def _quoted_tsv(path: Path, spec: FileSpec) -> Iterator[list[str | None]]:
    """Tab-separated with double-quoted fields (DrugCentral's TSV export).

    The built-in ``tsv`` reader keeps quotes as part of the value, so every name and
    accession would carry them.
    """
    with open_text(path) as fh:
        for row in csv.reader(fh, delimiter="\t", quotechar='"'):
            if row and any(c.strip() for c in row):
                yield _clean(row)


def _headerless_csv(path: Path, spec: FileSpec) -> Iterator[list[str | None]]:
    """A comma-separated file without a header; the columns come from the spec."""
    if not spec.columns:                                 # pragma: no cover - programming
        raise ValueError(f"{spec.name}: a headerless file needs FileSpec.columns")
    yield list(spec.columns)
    with open_text(path) as fh:
        for row in csv.reader(fh):
            if row and any(c.strip() for c in row):
                yield _clean(row)


READERS = {"tsv_quoted": _quoted_tsv, "csv_headerless": _headerless_csv}


# --------------------------------------------------------------------------- DrugCentral
_DC = "https://unmtid-dbs.net/download/"
_DC_SITE = "https://drugcentral.org/static/"
_DC_LICENSE = "CC BY-SA 4.0"

#: DrugCentral's ACTION_TYPE words -> effect. The word itself stays in context["action"].
#: A positive or negative allosteric modulator states its direction; a modulator without
#: one is "modulation". An antisense oligonucleotide lowers the target's amount, not its
#: activity, hence "decrease". Releasing agents, substrates and chaperones have no single
#: direction on the target: "other".
_DC_EFFECT = {
    "INHIBITOR": "inhibition", "ANTAGONIST": "inhibition", "BLOCKER": "inhibition",
    "GATING INHIBITOR": "inhibition", "ALLOSTERIC ANTAGONIST": "inhibition",
    "COMPLEMENT INHIBITOR": "inhibition", "INVERSE AGONIST": "inhibition",
    "NEGATIVE ALLOSTERIC MODULATOR": "inhibition", "NEGATIVE MODULATOR": "inhibition",
    "AGONIST": "activation", "PARTIAL AGONIST": "activation", "ACTIVATOR": "activation",
    "OPENER": "activation", "POSITIVE ALLOSTERIC MODULATOR": "activation",
    "POSITIVE MODULATOR": "activation",
    "MODULATOR": "modulation", "ALLOSTERIC MODULATOR": "modulation",
    "BINDING AGENT": "binding", "ANTIBODY BINDING": "binding",
    "ANTISENSE INHIBITOR": "decrease",
    "RELEASING AGENT": "other", "SUBSTRATE": "other", "PHARMACOLOGICAL CHAPERONE": "other",
    "OTHER": "other",
}

#: ACT_SOURCE / MOA_SOURCE values that name another database (the row's upstream). The
#: others (DRUG LABEL, SCIENTIFIC LITERATURE, EXPERT CURATOR, UNKNOWN) are DrugCentral's
#: own curation.
_DC_UPSTREAM = {"CHEMBL": "ChEMBL", "IUPHAR": "GtoPdb", "DRUGBANK": "DrugBank",
                "KEGG DRUG": "KEGG", "WOMBAT-PK": "WOMBAT-PK", "DRUG MATRIX": "DrugMatrix",
                "PDSP": "PDSP"}

#: RELATION values that qualify ACT_VALUE (a bound or an estimate).
_DC_COMPARISONS = frozenset({">", "<", ">=", "<=", "~"})

_PUBMED = re.compile(r"pubmed\.ncbi\.nlm\.nih\.gov/(\d+)|ncbi\.nlm\.nih\.gov/pubmed/(\d+)")
_DOI = re.compile(r"^https?://(?:dx\.)?doi\.org/(10\..+)$", re.I)


def _reference(url: object) -> str | None:
    """A PubMed or DOI link as ``pmid:`` / ``doi:``; any other link as it is."""
    text = v(url)
    if not text:
        return None
    m = _PUBMED.search(text)
    if m:
        return f"pmid:{m.group(1) or m.group(2)}"
    m = _DOI.match(text)
    return f"doi:{m.group(1)}" if m else text


def _drugcentral(conn) -> Iterator[Row | None]:
    if not has(conn, "dti"):
        return
    for r in rows(conn, "SELECT * FROM dti"):
        drug = v(r["STRUCT_ID"])
        subject = f"drugcentral:{drug}" if drug else None
        accessions = [a.strip() for a in (v(r["ACCESSION"]) or "").split("|") if a.strip()]
        if not accessions:
            yield unresolved("drug_target", "drugcentral", subject, r["DRUG_NAME"],
                             r["TARGET_NAME"], "DrugCentral gives no UniProt accession")
            continue
        genes = [g.strip() for g in (v(r["GENE"]) or "").split("|")]
        if len(genes) != len(accessions):
            genes = [None] * len(accessions)    # the columns do not line up: no gene names
        moa = v(r["MOA"]) == "1"
        action = v(r["ACTION_TYPE"])
        effect = _DC_EFFECT.get(action.upper(), "other") if action else None
        act_source, moa_source = v(r["ACT_SOURCE"]), v(r["MOA_SOURCE"]) if moa else None
        reference = (_reference(r["MOA_SOURCE_URL"]) if moa else None) \
            or _reference(r["ACT_SOURCE_URL"])
        value = v(r["ACT_VALUE"])
        relation = v(r["RELATION"])
        upstream: list[str] = []
        for src in (act_source, moa_source):
            name = _DC_UPSTREAM.get((src or "").upper())
            if name and name not in upstream:
                upstream.append(name)
        context = ctx(
            action=action, mechanism="mechanism of action" if moa else None,
            measure=v(r["ACT_TYPE"]),
            # DrugCentral standardises every activity to -log10 of the molar value
            unit="-log10(M)" if value else None,
            # only a comparison qualifies the value; '=' is exact and '-' says nothing
            flags=f"relation {relation}" if value and relation in _DC_COMPARISONS else None,
            # a mechanism-of-action record says "Mechanism of Action" where an assay
            # record describes its assay
            assay=None if (v(r["ACT_COMMENT"]) or "").lower() == "mechanism of action"
            else v(r["ACT_COMMENT"]), species=v(r["ORGANISM"]),
            source_db=names(act_source, moa_source))
        complex_note = (f"component of {v(r['TARGET_NAME'])} ({len(accessions)} components)"
                        if len(accessions) > 1 else None)
        via = f"via {'; '.join(upstream)}" if upstream else None
        note = "; ".join(x for x in (complex_note, via) if x) or None
        for acc, gene in zip(accessions, genes):
            yield rel("drug_target", "drugcentral", subject, r["DRUG_NAME"],
                      f"uniprot:{acc}",
                      names(gene, r["TARGET_NAME"] if len(accessions) == 1 else None),
                      "known", score=value, reference=reference, note=note,
                      effect=effect, context=context)


DRUGCENTRAL = DatasetSpec(
    "drugcentral", "DrugCentral", (105,), "https://drugcentral.org/download", _DC_LICENSE,
    (FileSpec(_DC + "drug.target.interaction.tsv.gz", "drug.target.interaction.tsv.gz",
              "dti", fmt="tsv_quoted",
              note="drug-target activities and mechanisms of action; about 1 MB, updated "
                   "with each release (2026-09-27 when checked)"),
     FileSpec(_DC + "DrugCentral/2021_09_01/structures.smiles.tsv", "structures.smiles.tsv",
              "structures", fmt="tsv", expected_bytes=1089436,
              note="SMILES, InChI and InChIKey per drug id; the newest structures file with "
                   "InChIKeys (2021-11-12), so drugs added later have no InChIKey here"),
     FileSpec(_DC_SITE + "FDA_Approved.csv", "FDA_Approved.csv", "approved_fda",
              fmt="csv_headerless", columns=("struct_id", "name")),
     FileSpec(_DC_SITE + "EMA_Approved.csv", "EMA_Approved.csv", "approved_ema",
              fmt="csv_headerless", columns=("struct_id", "name")),
     FileSpec(_DC_SITE + "PMDA_Approved.csv", "PMDA_Approved.csv", "approved_pmda",
              fmt="csv_headerless", columns=("struct_id", "name")),
     FileSpec(_DC + "DrugCentral/2023/structures.molV2.sdf.gz", "structures.molV2.2023.sdf.gz",
              "", fmt="raw", optional=True, expected_bytes=1384296,
              note="the last complete V2000 structure set (2023-06-12), for cheminformatics"),
     FileSpec(_DC + "structures.molV2.sdf.gz", "structures.molV2.new.sdf.gz", "", fmt="raw",
              optional=True,
              note="the file the download page links: only the about 60 drugs approved "
                   "since 2024, not the full set"),
     FileSpec(_DC + "Drugcentral_2026-09-25.pgdump", "Drugcentral_2026-09-25.pgdump", "",
              fmt="raw", optional=True, expected_bytes=1378106682,
              note="the full database (1.4 GB); restoring it needs PostgreSQL 16 with the "
                   "RDKit cartridge. Indications, contraindications, FAERS signals and ATC "
                   "codes live only here and in the live connector 'drugcentral'")),
    version="2026-09-25 dump; drug-target file 2026-09-27",
    notes="Approved and unapproved drugs (human and veterinary) with their targets. Each "
          "drug-target row is one record of the interaction file: an activity (Ki, IC50, "
          "Kd, EC50 ..., standardised to -log10 M: the score) or a mechanism of action "
          "(MOA = 1), with the database or document it came from. Evidence is 'known' "
          "(measured or curated); rows DrugCentral took from ChEMBL, GtoPdb (IUPHAR), "
          "DrugBank, KEGG, WOMBAT-PK, DrugMatrix or PDSP say so as 'via ...'. The effect "
          "comes from the action type (inhibitor, agonist, blocker ...), kept in "
          "context.action; rows without an action type have no effect. A target made of "
          "several proteins (a channel complex) gives one row per component. Indications "
          "are not in any public file except the database dump; use the live connector "
          "('indications').",
    relations=("drug_target",),
    commercial_use="allowed",
    upstream=("ChEMBL", "GtoPdb", "DrugBank", "KEGG", "WOMBAT-PK", "DrugMatrix", "PDSP"),
    crosswalk={
        "compound": "SELECT 'drugcentral:' || ID, 'inchikey:' || InChIKey FROM structures "
                    "WHERE length(InChIKey) = 27",
        "gene": "SELECT DISTINCT 'uniprot:' || ACCESSION, 'symbol:' || GENE FROM dti "
                "WHERE ORGANISM = 'Homo sapiens' AND ACCESSION NOT LIKE '%|%' "
                "AND GENE IS NOT NULL AND GENE NOT LIKE '%|%'"})


# ------------------------------------------------------------------------------ DrugComb
_ZENODO = "https://zenodo.org/api/records/"
_DRUGCOMB_LICENSE = ("CC BY 4.0 (the DrugComb authors' Zenodo deposits); the screens it "
                     "collects keep their own terms (e.g. GDSC is non-commercial)")

DRUGCOMB = DatasetSpec(
    "drugcomb", "DrugComb", (106,), "https://drugcomb.org/", _DRUGCOMB_LICENSE,
    (FileSpec(_ZENODO + "11102665/files/summary_table_v1.4.csv/content",
              "summary_table_v1.4.csv", "", fmt="raw", expected_bytes=193184734,
              note="one row per tested combination block (drug_row, drug_col, cell line): "
                   "CSS, ZIP, Bliss, Loewe and HSA synergy, IC50 and RI of each drug; md5 "
                   "c11efbdcae4a860c2374c1505a66599b. Analysis layer: not exploded"),
     FileSpec(_ZENODO + "18449193/files/DrugComb_drug_identifiers.xlsx/content",
              "DrugComb_drug_identifiers.xlsx", "drugs", fmt="xlsx", expected_bytes=5856350,
              note="drug names with ChEMBL, InChIKey, SMILES, PubChem CID, DrugBank, KEGG, "
                   "clinical phase and target names"),
     FileSpec(_ZENODO + "18449193/files/DrugComb_cell_line_identifiers.xlsx/content",
              "DrugComb_cell_line_identifiers.xlsx", "cell_lines", fmt="xlsx",
              expected_bytes=161434,
              note="cell lines with Cellosaurus, DepMap, Cell Model Passports, COSMIC and "
                   "CCLE ids"),
     FileSpec(_ZENODO + "15235991/files/summary_v_1_5.csv/content", "summary_v_1_5.csv", "",
              fmt="raw", optional=True, expected_bytes=1421694989,
              note="v1.5 summary (1.4 GB) with study_name and the monotherapy screens "
                   "(GDSC1, CTRPv2, ...): those rows duplicate GDSC and PharmacoDB"),
     FileSpec(_ZENODO + "18449193/files/drugcomb_data_v1.4.csv/content",
              "drugcomb_data_v1.4.csv", "", fmt="raw", optional=True,
              expected_bytes=2008117325,
              note="dose-level % inhibition (2.0 GB) with PubChem CIDs and Cellosaurus "
                   "accessions")),
    version="1.4 (summary 2019, identifiers re-deposited 2026-02-01); 1.5 optional",
    notes="Drug-combination screens collected from published studies, scored for synergy "
          "from the measured dose-response matrices. The synergy table is a screen result "
          "kept as a file for analysis (one block per drug pair and cell line); the drug "
          "and cell-line tables map DrugComb's names to ChEMBL, InChIKey, PubChem, "
          "Cellosaurus and DepMap ids. Target names in the drug table are protein names "
          "integrated from other databases without provenance and are not turned into "
          "relations. The portal and its API (api.drugcomb.org) did not answer on "
          "2026-10-01; the Zenodo deposits are the reference copy (Zenodo asks for 10 s "
          "between requests).",
    commercial_use="unknown",
    upstream=("ONEIL", "NCI-ALMANAC", "ASTRAZENECA", "FRIEDMAN", "BEATAML", "GDSC", "CTRP",
              "CCLE"))


# ---------------------------------------------------------------------------------- GDSC
_GDSC_CMP = "https://cmp.cog.sanger.ac.uk/download/"
_GDSC_LEGACY = "https://cog.sanger.ac.uk/cancerrxgene/GDSC_release8.5/"
_GDSC_LICENSE = ("DepMap at Sanger Data Usage Policy: internal research and educational use; "
                 "no resale, redistribution or commercial services; commercial use not "
                 "permitted without prior consent")

#: TARGET values that say "no target", not a target.
_GDSC_NO_TARGET = frozenset({"others", "other", "not defined", "none", "unknown"})
_CHEMBL = re.compile(r"^CHEMBL\d+$")

#: GDSC target names that are approved HGNC symbols for the protein GDSC means. Checked
#: against the human canonical-protein symbols of Pharos400 (release 8.5's 382 names).
#: PDK1, FAS and EIF2A are HGNC symbols too but GDSC means another protein by them
#: (PDPK1, FASN and EIF2S1 for KIN001-244, C-75 and Salubrinal), so they are left out.
_GDSC_SYMBOLS = frozenset("""
ACACA ACVR1B ACVR1C ADRA1A ADRB1 AKT1 AKT2 AKT3 ALK AR ARAF ARFGAP1 ATM ATR AURKA AURKB
AURKC AXL BAZ2A BAZ2B BCL2 BIRC5 BMX BRAF BRD2 BRD3 BRD4 BRD9 BRDT BRPF1 BRPF3 BTK CAPN1
CDC7 CDK1 CDK2 CDK4 CDK5 CDK6 CDK7 CDK9 CECR2 CHEK1 CHEK2 CLK4 CRBN CSF1R DAPK3 DDR1 DDR2
DOT1L DYRK1A DYRK1B EGFR EGLN1 EHMT1 EHMT2 EP300 EPHB4 ERBB2 ERBB3 ERBB4 ESR1 ESR2 EZH2
FEN1 FGFR1 FGFR2 FGFR3 FGFR4 FLT1 FLT3 FLT4 FYN GLS GSK3A GSK3B HDAC1 HDAC10 HDAC11 HDAC2
HDAC3 HDAC6 HDAC8 HIPK2 HSF1 IGF1R IRAK1 IRAK4 ITK JAK1 JAK2 JAK3 KDM3A KDM4A KDM4C KDM4E
KDM6B KDR KIF11 KIT L3MBTL3 LCK LIMK1 LRRK2 LTK MAP4K2 MCL1 MDM2 MDM4 MET MKNK1 MKNK2
MRE11 MTOR NAMPT NTRK1 NTRK2 NTRK3 NUAK1 NUAK2 PAK1 PAK2 PARP1 PARP2 PARP6 PDGFRA PDGFRB
PIK3CB PIKFYVE PIM1 PIM2 PIM3 PLK1 PLK2 PLK3 PORCN PPM1D PPP1R15B RAC1 RAC2 RAC3 RET ROCK1
ROCK2 ROS1 SGK2 SGK3 SIRT1 SMARCA2 SMARCA4 SMO SRC SYK TAF1 TBK1 TEC TERT TGFB1 TGFBR1
TNKS2 TOP1 TP53 TTK TYMS ULK1 USP1 USP47 USP7 WEE1 XIAP
""".split())

#: GDSC names for one protein that are not its HGNC symbol: a common alias, a UniProt
#: entry name, or a symbol GDSC gives in parentheses. Families (ERK, PI3K, VEGFR) and
#: complexes (MTORC1) are not here: they stay GDSC's names.
_GDSC_ALIASES = {
    "ALK5": "TGFBR1", "CRAF": "RAF1", "c-FGR": "FGR", "eEF2K": "EEF2K", "EG5": "KIF11",
    "ERK1": "MAPK3", "ERK2": "MAPK1", "ERK5": "MAPK7", "MEK1": "MAP2K1", "MEK2": "MAP2K2",
    "MEK5": "MAP2K5", "JNK1": "MAPK8", "JNK2": "MAPK9", "JNK3": "MAPK10",
    "p38alpha": "MAPK14", "p38beta": "MAPK11", "TAK1": "MAP3K7", "NIK": "MAP3K14",
    "IKK1": "CHUK", "IKK-1": "CHUK", "IKK2": "IKBKB", "IKK-2": "IKBKB", "IKKb": "IKBKB",
    "S6K1": "RPS6KB1", "KS6B1 (p70S6K)": "RPS6KB1", "RSK1": "RPS6KA1", "RSK2": "RPS6KA3",
    "RSK3": "RPS6KA2", "MNK1": "MKNK1", "MNK2": "MKNK2", "M4K2": "MAP4K2",
    "PK3CG": "PIK3CG", "VSP34": "PIK3C3", "PDK1 (PDPK1)": "PDPK1", "FAK1": "PTK2",
    "VEGFR1": "FLT1", "VEGFR2": "KDR", "VEGFR3": "FLT4", "VEGFR3/FLT4": "FLT4",
    "TIE2": "TEK", "RON": "MST1R", "MPS1": "TTK", "WIP1": "PPM1D", "LSD1": "KDM1A",
    "G9A": "EHMT2", "GLP": "EHMT1", "TNKS1": "TNKS", "DNAPK": "PRKDC", "SETD8": "KMT5A",
    "CBP": "CREBBP", "PERK": "EIF2AK3", "GADD34": "PPP1R15A", "UAF1": "WDR48",
    "PMRT5": "PRMT5", "MRCKB": "CDC42BPB", "MRCKB_HUMAN": "CDC42BPB", "MCT1": "SLC16A1",
    "MCT4": "SLC16A3", "PARP7": "TIPARP", "MCL-1": "MCL1", "BCL-XL": "BCL2L1",
    "BCL-W": "BCL2L2", "BCL-B": "BCL2L10", "BFL1": "BCL2A1", "cIAP1": "BIRC2",
    "cIAP2": "BIRC3", "FXR": "NR1H4", "FGRF1": "FGFR1", "IGFR1": "IGF1R",
    "SHP-1 (PTPN6)": "PTPN6", "SHP-2 (PTPN11)": "PTPN11", "KRAS (G12C)": "KRAS",
    "IDH1 (R132H)": "IDH1", "IDH2 R140Q mutant": "IDH2", "IDH2(R140Q)": "IDH2",
    "Endothelin-1 receptor (EDNRA)": "EDNRA", "Dihydrofolate reductase (DHFR)": "DHFR",
    "Farnesyl-transferase (FNTA)": "FNTA",
}

#: TARGET values that describe a mechanism or a drug class and name no protein: they go
#: to the unresolved queue, not into compound_target.
_GDSC_MECHANISMS = frozenset(s.lower() for s in (
    "Alkylating agent", "DNA alkylating agent", "DNA crosslinker", "DNA damage",
    "dsDNA break induction", "Anthracycline", "Anti-metabolite", "Antimetabolite",
    "Antimetabolite (DNA & RNA)", "Pyrimidine antimetabolite",
    "Pyrimidine synthesis inhibitor", "Autophagy inducer", "Biguanide agent",
    "Broad spectrum kinase inhibitor", "G-quadruplex stabiliser", "Glycolysis",
    "Induces reactive oxygen species", "Inflammatory related", "Metabo", "Metabolism",
    "Microtubule destabiliser", "Microtubule stabiliser", "Mitochondria",
    "anti-oxidant proteins", "Retinoic acid"))

#: Whole TARGET values that the comma split would break apart.
_GDSC_WHOLE = {"HDAC inhibitor Class I, IIa, IIb, IV": "HDAC class I, IIa, IIb, IV",
               "Retinioic X receptor (RXR) agonist": "RXR"}


def _split_targets(text: str | None) -> list[str]:
    """GDSC's comma-separated putative targets, keeping commas inside parentheses."""
    text = (text or "").strip()
    if text in _GDSC_WHOLE:
        return [_GDSC_WHOLE[text]]
    out, depth, cur = [], 0, []
    for ch in text:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        if ch == "," and depth == 0:
            out.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
    out.append("".join(cur).strip())
    return [t for t in out if t and v(t) and t.lower() not in _GDSC_NO_TARGET]


def _gdsc_target(name: str) -> str:
    """The object id for one GDSC target name: an HGNC symbol where GDSC names one
    protein, else GDSC's own name (a family, complex or unmapped name)."""
    symbol = name if name in _GDSC_SYMBOLS else _GDSC_ALIASES.get(name)
    return f"symbol:{symbol}" if symbol else f"gdsc:target.{name}"


def _gdsc(conn) -> Iterator[Row | None]:
    if not has(conn, "compounds"):
        return
    for r in rows(conn, "SELECT * FROM compounds"):
        key, chembl = v(r["INCHI_KEY"]), v(r["CHEMBL_ID"])
        subject = (f"inchikey:{key}" if key and len(key) == 27
                   else f"chembl:{chembl}" if chembl and _CHEMBL.match(chembl)
                   else f"gdsc:drug.{v(r['DRUG_ID'])}")
        synonyms = [s.strip() for s in (v(r["SYNONYMS"]) or "").split(",")]
        drug = names(r["DRUG_NAME"], *synonyms)
        pathway = v(r["TARGET_PATHWAY"])
        note = f"putative target; pathway: {pathway}" if pathway else "putative target"
        for target in _split_targets(v(r["TARGET"])):
            if target.lower() in _GDSC_MECHANISMS:
                yield unresolved("compound_target", "gdsc", subject, drug, target,
                                 "GDSC gives a mechanism or drug class, not a target",
                                 note=note)
                continue
            # Gene symbols (EGFR) and aliases of one protein (ERK1) become HGNC symbols;
            # families (PDGFR, PI3K) and complexes (MTORC1) stay GDSC's own names.
            obj = _gdsc_target(target)
            yield rel("compound_target", "gdsc", subject, drug, obj,
                      names(obj[7:], target) if obj.startswith("symbol:") else target,
                      "listed", note=note)


GDSC = DatasetSpec(
    "gdsc", "Genomics of Drug Sensitivity in Cancer (GDSC1/GDSC2)", (108,),
    "https://cellmodelpassports.sanger.ac.uk/downloads", _GDSC_LICENSE,
    (FileSpec(_GDSC_CMP + "GDSC2_fitted_dose_response_27Oct23.xlsx",
              "GDSC2_fitted_dose_response_27Oct23.xlsx", "", fmt="raw",
              note="release 8.5: one fitted curve per drug and cell line (LN_IC50, AUC, "
                   "RMSE, Z_SCORE); the file Cell Model Passports publishes. Analysis "
                   "layer: not exploded"),
     FileSpec(_GDSC_CMP + "GDSC1_fitted_dose_response_27Oct23.xlsx",
              "GDSC1_fitted_dose_response_27Oct23.xlsx", "", fmt="raw",
              note="the legacy GDSC1 screen, same layout"),
     FileSpec("https://cog.sanger.ac.uk/cmp/download/screened_compounds_rel_8.5_inchi.csv",
              "screened_compounds_rel_8.5_inchi.csv", "compounds", fmt="csv",
              note="621 compound entries (one per screening site) with synonyms, putative "
                   "targets, pathway, ChEMBL id and InChIKey"),
     FileSpec("https://cog.sanger.ac.uk/cmp/download/model_list_20260921.csv",
              "model_list_20260921.csv", "models", fmt="csv", expected_bytes=957555,
              note="Cell Model Passports annotation: SANGER_MODEL_ID to COSMIC, Broad, "
                   "CCLE and Cellosaurus (RRID) ids, tissue and cancer type. "
                   "model_list_latest.csv.gz is a stale 2021 file despite its name"),
     FileSpec(_GDSC_LEGACY + "GDSC2_fitted_dose_response_27Oct23.csv",
              "GDSC2_fitted_dose_response_27Oct23.csv", "", fmt="raw", optional=True,
              expected_bytes=39830840,
              note="the same table as CSV, from the retired cancerrxgene.org bucket"),
     FileSpec(_GDSC_LEGACY + "GDSC1_fitted_dose_response_27Oct23.csv",
              "GDSC1_fitted_dose_response_27Oct23.csv", "", fmt="raw", optional=True,
              expected_bytes=54084519),
     FileSpec(_GDSC_LEGACY + "Cell_Lines_Details.xlsx", "Cell_Lines_Details.xlsx",
              "cell_lines", fmt="xlsx", optional=True, expected_bytes=117316),
     FileSpec(_GDSC_LEGACY + "ANOVA_results_GDSC2_27Oct23.xlsx",
              "ANOVA_results_GDSC2_27Oct23.xlsx", "", fmt="raw", optional=True,
              expected_bytes=52381059,
              note="genomic feature to drug response associations (effect size, p, FDR)"),
     FileSpec(_GDSC_CMP + "GDSC2_public_raw_data_27Oct23.zip",
              "GDSC2_public_raw_data_27Oct23.zip", "", fmt="raw", optional=True,
              expected_bytes=122866072, note="plate-level raw intensities"),
     FileSpec(_GDSC_CMP + "GDSC1_public_raw_data_27Oct23.zip",
              "GDSC1_public_raw_data_27Oct23.zip", "", fmt="raw", optional=True,
              expected_bytes=66418176)),
    version="release 8.5 (27Oct23); model list 2026-09-21",
    notes="Cancer cell-line drug sensitivity measured at the Wellcome Sanger Institute and "
          "MGH. The fitted dose-response tables are screen results kept as files for "
          "analysis. The compound table's putative targets become compound_target rows "
          "('listed': GDSC's annotation, not a measurement). They are free text: a name "
          "that is an HGNC symbol (EGFR) or a fixed alias of one protein (ERK1 -> MAPK3) "
          "becomes symbol:<HGNC>, checked against the human protein symbols; a family "
          "(PDGFR, PI3K) or complex (MTORC1) stays GDSC's name (gdsc:target.<name>); a "
          "mechanism or drug class that names no protein ('Microtubule stabiliser') goes "
          "to the unresolved queue. cancerrxgene.org "
          "answers 410 since GDSC moved to Cell Model Passports. Non-commercial: the data "
          "may not be redistributed.",
    relations=("compound_target",),
    commercial_use="forbidden")


# -------------------------------------------------------------------------------- Pharos
_PHAROS_LICENSE = ("not stated: Pharos defers to its primary sources ('Please respect their "
                   "individual licenses regarding proper use and redistribution')")


def _pharos(conn) -> Iterator[Row | None]:
    if not has(conn, "tdl"):
        return
    for r in rows(conn, "SELECT * FROM tdl"):
        acc = v(r["uniprot_id"])
        if not acc:
            continue
        protein = f"uniprot:{acc}"
        pname = names(r["symbol"], r["name"])
        status = v(r["canonical_isoform_status"])
        flags = names("unreviewed" if (v(r["uniprot_reviewed"]) or "").lower() == "false"
                      else None, status if status and status != "canonical" else None)
        tdl = v(r["tdl"])
        if tdl:
            yield rel("gene_set_member", "pharos", f"pharos:tdl.{tdl}",
                      f"Pharos target development level {tdl}", protein, pname, "listed",
                      note=f"ligands={v(r['tdl_ligand_count']) or 0}; "
                           f"drugs={v(r['tdl_drug_count']) or 0}",
                      context=ctx(flags=flags))
        family = v(r["idg_family"])
        if family:
            yield rel("gene_set_member", "pharos", f"pharos:family.{family}",
                      f"Pharos protein family {family}", protein, pname, "listed",
                      context=ctx(flags=flags))


PHAROS = DatasetSpec(
    "pharos", "Pharos / TCRD (Pharos400 target development levels)", (109,),
    "https://pharos.nih.gov/", _PHAROS_LICENSE,
    (FileSpec("https://opendata.ncats.nih.gov/public/pharos/pharos400_tdls_canonicals.csv",
              "pharos400_tdls_canonicals.csv", "tdl", fmt="csv", expected_bytes=44614999,
              note="20,654 canonical human proteins: UniProt, symbol, NCBI Gene, Ensembl, "
                   "TDL, IDG family and the counts behind the TDL (ligands, drugs, GO terms, "
                   "GeneRIFs, PubMed score, antibodies); release 2026-05-28"),
     FileSpec("https://opendata.ncats.nih.gov/public/pharos/pharos400_tdls_full.csv",
              "pharos400_tdls_full.csv", "", fmt="raw", optional=True,
              expected_bytes=87201549,
              note="all 166,833 human protein rows, isoforms and alternate products included"),
     FileSpec("https://opendata.ncats.nih.gov/public/pharos/archive/pharos319.sql.gz",
              "pharos319.sql.gz", "", fmt="raw", optional=True, expected_bytes=2257847063,
              note="the MySQL dump behind the live API (pharos319, 2.3 GB)")),
    version="Pharos400 (2026-05-28); the live API serves pharos319",
    notes="IDG target development levels: Tclin (approved-drug target with a known "
          "mechanism), Tchem (potent small-molecule activities), Tbio (biological evidence) "
          "and Tdark (little known), plus the IDG family (GPCR, kinase, ion channel, "
          "nuclear receptor). Each becomes a gene_set_member row (pharos:tdl.Tclin -> "
          "uniprot:...), 'listed': a classification Pharos computes by published rules "
          "from DrugCentral, ChEMBL, GO, GeneRIF, PubMed and antibody counts. The file is "
          "Pharos400, newer than the pharos319 database the live connector 'pharos' "
          "queries; 2,137 proteins moved up a level between them. Ligand, disease and "
          "interaction data come through the live connector.",
    relations=("gene_set_member",),
    commercial_use="unknown",
    upstream=("DrugCentral", "ChEMBL", "UniProt", "GO", "GeneRIF", "JensenLab",
              "Antibodypedia"),
    crosswalk={
        "gene": "SELECT DISTINCT 'uniprot:' || uniprot_id, 'symbol:' || symbol FROM tdl "
                "WHERE symbol IS NOT NULL AND symbol NOT LIKE '%|%' "
                "AND canonical_isoform_status = 'canonical'"})


DATASETS: tuple[DatasetSpec, ...] = (DRUGCENTRAL, DRUGCOMB, GDSC, PHAROS)
EXTRACTORS = {"drugcentral": _drugcentral, "gdsc": _gdsc, "pharos": _pharos}
