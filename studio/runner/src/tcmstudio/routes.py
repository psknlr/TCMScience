"""Research routes: for each kind of question TCM research asks, every source in this runtime
that answers a part of it, the kind of evidence each gives, and what covers a source that
cannot run here (CONTRACTS §2: catalog entry system.routes, core tool research_routes).

A route is a map, not a pipeline: it names catalog entries and runs nothing. Each step answers
one part of the question with every source that can answer it, the most direct evidence
first. ``view`` judges each source against this runtime (the readiness catalog_search uses,
plus the data hub's datasets for a relation kind) and, for a source that cannot run here,
names the one in the same step that covers it and what that changes, so a switch is never
silent. The agent makes the calls itself, each through the dispatcher with its own approvals,
network permission and governance: nothing here widens what a call may do.

Coverage is a test (tests/test_routes.py): every connector operation of the catalog is in a
route; so is every relation kind the data hub has a dataset for, and every dataset, by one of
its relation kinds, by an entry that reads it or by name in tcmdb.query. The datasets left
out are in ``NOT_ROUTED`` with the reason. Every call a route names is checked against the
schema the dispatcher validates it against. studio/docs/ROUTES.md is generated from this
module (``markdown``) and checked against it.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping, Sequence

__all__ = ["EVIDENCE", "RULES", "NOT_ROUTED", "Use", "Step", "Route", "ROUTES", "ROUTE_BY_ID",
           "Judge", "call_for", "fixed_args", "label", "hub_datasets", "match", "view", "listing",
           "markdown"]

#: What a source gives, in general: (a short phrase, what it means for a claim). The data
#: hub's relation rows carry their own label each (per_row).
EVIDENCE: dict[str, tuple[str, str]] = {
    "recorded": ("what a TCM text or standard records",
                 "what a TCM corpus or standard records (classical passages, materia and formula "
                 "records, monographs): tradition and specification, not efficacy"),
    "reference": ("identity and cross-references",
                  "identity, nomenclature and cross-references: what a name or id refers to, "
                  "not a finding"),
    "measured": ("measured values",
                 "values an experiment measured (activities, expression, kinetics, assays, "
                 "constituents detected in blood)"),
    "curated": ("relations curated from publications",
                "relations curators took from publications or other databases: documented, "
                "each traceable to its reference"),
    "associated": ("statistical associations",
                   "a statistical association in a population or a screen (GWAS, eQTL, "
                   "knockout and CRISPR screens): not causation"),
    "aggregated": ("aggregated scores",
                   "a score combining several sources (Open Targets, STRING, DGIdb): it ranks "
                   "what to read, it is not a finding"),
    "reported": ("spontaneous reports",
                 "reports to a surveillance system: that reports exist, not how often harm "
                 "occurs or that the product caused it"),
    "registered": ("registered studies", "a registered study: what was planned, not what it found"),
    "predicted": ("predictions to test",
                  "a computation's prediction (target prediction, docking, structure, ADMET, "
                  "variant effect): a hypothesis to test"),
    "mentioned": ("text-mined co-mentions",
                  "text mining: that a text mentions both, not that it found a link"),
    "literature": ("pointers to read",
                   "a bibliographic hit: a pointer to read; nothing is licensed until the study "
                   "itself is assessed"),
    "data": ("data to analyse",
             "a dataset to analyse (runs, spectra, images, repertoires): a finding comes only "
             "from an analysis of it"),
    "analysis": ("a computation on the data given",
                 "a computation run here on the data given: as sound as its inputs and design"),
    "per_row": ("the data hub's labelled rows",
                "the data hub's rows, each with its source and its own evidence label (known, "
                "predicted, aggregated, listed, reported, mentioned, signal, associated): report "
                "each as labelled"),
}

#: How the agent uses a route; every route's view repeats them.
RULES: tuple[str, ...] = (
    "Run every source of a step that can run here, not only the first; calls that do not "
    "depend on each other can go out together.",
    "Keep evidence kinds apart in the answer: never merge predicted, aggregated or text-mined "
    "rows into measured or curated ones, and say which source gave each.",
    "When a source cannot run here, say so and use what covers it, saying what that changes; "
    "never switch silently. When nothing in a step can run, say that part is unanswered here "
    "and what would answer it.",
    "Fill each <…> from the question or an earlier result; never invent an id (search for it).",
    "A missing row or an empty result is not evidence of absence.",
    "To decide between explanations, open the route's scaffold with research_inquiry; a route "
    "by itself decides nothing.",
    "Every call keeps its own approvals, network permission and governance; a route adds no "
    "permission.",
)

#: Datasets of the data hub no route uses, with the reason.
NOT_ROUTED: dict[str, str] = {
    "shennong": "generated question-and-answer text for instruction tuning: training data, "
                "never evidence",
    "tcm_ladder": "an evaluation benchmark for TCM language models, not a source of facts "
                  "about herbs or patients",
    "tcmp300": "medicinal-plant image classes for training image recognition; no tool here "
               "identifies a herb from an image",
}


@dataclass(frozen=True)
class Use:
    """One source for a step: a catalog entry, the arguments to give it ('<…>' marks what to
    fill in from the question or an earlier result), the kind of evidence it gives, and what
    it adds when its title does not say."""
    entry: str
    evidence: str
    args: Mapping[str, Any] = field(default_factory=dict)
    note: str = ""


@dataclass(frozen=True)
class Step:
    """One part of the question, with every source that answers it, the most direct first."""
    id: str
    answers: str
    uses: tuple[Use, ...]


@dataclass(frozen=True)
class Route:
    """A kind of research question and the sources that answer it, step by step."""
    id: str
    title: tuple[str, str]                      # (zh, en)
    asks: str
    keywords: tuple[str, ...]
    steps: tuple[Step, ...]
    reconcile: str
    scaffold: str | None = None                 # a research_inquiry scaffold (rivals.SCAFFOLDS)
    then: tuple[str, ...] = ()
    cautions: tuple[str, ...] = ()
    ids: tuple[str, ...] = ()                   # patterns of identifiers that point here


def _c(source: str, op: str, evidence: str, note: str = "", /, **args: Any) -> Use:
    """A connector operation (its arguments may be named source or op)."""
    return Use(f"connector.{source}.{op}", evidence, args, note)


def _hub(kind: str, note: str = "", /, **args: Any) -> Use:
    """One relation kind across every built store of the data hub."""
    return Use("tcmdb.relations", "per_row", {"kind": kind, **args}, note)


def _table(dataset: str, evidence: str, note: str) -> Use:
    """The tables of a dataset that has no relation kinds (tcmdb.tables lists them)."""
    return Use("tcmdb.query", evidence,
               {"dataset": dataset, "table": "<a table tcmdb.tables lists>"}, note)


def _e(entry: str, evidence: str, note: str = "", /, **args: Any) -> Use:
    """Any other catalog entry."""
    return Use(entry, evidence, args, note)


_PINYIN = "<pinyin, upper case, e.g. HUANG QI>"
_GENES = "<gene symbols, space-separated>"

ROUTES: tuple[Route, ...] = (
    Route(
        "herb_identity", ("药材身份与基原", "Herb identity and botanical source"),
        "What a herb name refers to: the recorded materia, its aliases and processed forms, the "
        "species and part used, its standards and reference DNA, and its records in the TCM "
        "databases.",
        ("药材", "基原", "物种", "拉丁名", "学名", "别名", "鉴别", "正品", "伪品", "混伪品", "炮制",
         "herb identity", "species", "botanical", "latin name", "authentication", "dna barcode",
         "adulterant", "processed form"),
        (Step("materia", "the recorded materia record, its aliases and processed forms", (
            _e("native.tcm_lookup", "recorded", name="<herb name>"),
            _e("native.tcm_herb", "recorded", name="<herb name>"),
            _e("tcmdb.materia", "recorded", query="<herb name>"),
            _e("skill.normalize-tcm-entities", "reference", "the governed normalisation, with "
               "its record", names=["<herb name>"]))),
         Step("species", "the accepted botanical name, its taxonomy and where it grows", (
             _c("gbif", "match", "reference", name="<Latin binomial>"),
             _c("ncbi_datasets", "taxonomy", "reference",
                taxon="<Latin binomial or NCBI taxon id>"),
             _c("wikidata_sparql", "taxon_by_name", "reference", name="<Latin binomial>"),
             _table("wfo", "reference", "World Flora Online: accepted names and synonyms"),
             _c("gbif", "species", "reference", key="<GBIF key from match>"),
             _c("gbif", "occurrences", "data", "where the species has been recorded",
                taxon_key="<GBIF key>"))),
         Step("standards", "pharmacopoeial tests, reference DNA sequences and monographs", (
             _e("native.hkcmms_standard_lookup", "recorded", herb="<herb name>"),
             _e("native.hk_cmm_dna_lookup", "reference", name="<Latin binomial>"),
             _hub("subject_monograph", "EMA herbal monographs", subject="<herb or Latin name>"))),
         Step("databases", "the herb in the TCM databases, with the ids the next routes need", (
             _c("tcmbank", "herb_search", "curated", name="<herb name>"),
             _c("herb_api", "search", "curated", keyword="<herb name>", label="Herb"),
             _c("itcm", "herb", "reference", "cross-references to TCMID, ETCM, SymMap and TCMSP",
                herb_id="<ITCM herb id>"),
             _table("herb1", "curated", "HERB 1.0 entity tables"),
             _table("symmap2", "curated", "SymMap v2 entity tables with their UMLS, MeSH, OMIM, "
                    "ICD-10-CM and HPO mappings"),
             _table("tcmbank", "curated", "TCMBank entity tables")))),
        "Name the species and the part the answer is about: one herb name can cover several "
        "species (正品 and 混伪品), and a processed form (炙, 炒) is recorded apart. Say which "
        "records agree.",
        then=("herb_composition", "traditional_use", "safety"),
        cautions=("A name not found is not evidence that it does not exist.",
                  "An identity match infers no clinical property."),
    ),
    Route(
        "herb_composition", ("药材的化学成分", "What a herb contains"),
        "Which compounds a herb contains, how each is known (reported from the plant, detected "
        "in blood after dosing, or predicted), and how many independent sources agree.",
        ("成分", "化学成分", "化合物", "活性成分", "有效成分", "单体", "含有", "constituent",
         "ingredient", "compound", "phytochemical", "natural product", "contains", "chemistry"),
        (Step("reported", "compounds reported from the plant", (
            _hub("organism_compound", subject="<Latin binomial>"),
            _hub("herb_ingredient", subject="<herb>"),
            _c("coconut", "organism_molecules", "curated", organism="<Latin binomial>"),
            _c("knapsack", "organism_metabolites", "curated", organism="<Latin binomial>"),
            _c("knapsack", "pair_reference", "curated", "the reference of one species–metabolite "
               "pair (row: the organism's row on the metabolite's page)", c_id="<KNApSAcK C_ID>",
               row="<row>"),
            _c("coconut", "collection_molecules", "curated", "every molecule of one source "
               "collection COCONUT merged (a TCM database among them)",
               collection="<COCONUT collection title>"),
            _c("wikidata_sparql", "compounds_in_taxon", "curated", "LOTUS occurrences, each with "
               "its reference", taxon_qid="<Wikidata QID from taxon_by_name>"),
            _c("tcmbank", "herb_ingredients", "curated", tcmbank_id="<TCMBank herb id>"),
            _c("itcm", "herb_ingredients", "curated", herb_id="<ITCM herb id>"),
            _c("herb_api", "detail", "curated", entity_id="<HERB herb id>", label="Herb"),
            _c("symmap", "related", "curated", entity_id="<SymMap herb id SMHB…>", related="Mol"))),
         Step("absorbed", "constituents detected in blood after the herb was given", (
             _c("dcabm_tcm", "herb_blood", "measured", names=[_PINYIN]),
             _table("dcabm", "measured", "structures of every constituent DCABM-TCM detected in "
                    "blood (which herb each came from is the connector's)"))),
         Step("foods", "for a herb that is also a food (药食同源): nutrients and dietary "
                       "phytochemicals", (
             _hub("food_nutrient", subject="<food name>"),
             _c("phytohub", "search", "curated", name="<compound or food>"),
             _c("phytohub", "food_source", "reference", food_id="<PhytoHub food id>"),
             _c("phytohub", "structure_sdf", "reference", phub_id="<PHUB…>"))),
         Step("agreement", "how many independent sources report each compound", (
             _e("tcmdb.consensus", "per_row", kind="herb_ingredient", subject="<herb>"),
             _e("tcmdb.consensus", "per_row", kind="organism_compound", subject="<Latin binomial>"),
             _e("tcmdb.compare", "per_row", kind="herb_ingredient", subject="<herb>")))),
        "tcmdb_consensus counts independent sources once (copies of one upstream are one "
        "source). Keep compounds detected in blood apart from those only reported in the plant, "
        "and predicted rows apart from both.",
        then=("compound_identity", "exposure_metabolism", "compound_targets"),
        cautions=("Reported from the plant is not present in the medicinal part, the processed "
                  "slice or the decoction: processing and dose change what is there.",
                  "A compound missing from every source is not shown to be absent."),
    ),
    Route(
        "formula_composition", ("方剂组成与出处", "Formula composition and sources"),
        "What a formula contains in each recorded source version, with doses as written; how "
        "the databases list its herbs; which recorded incompatibilities its herbs raise; and "
        "whether the whole formula does more than its main constituent.",
        ("方剂", "组成", "出处", "方源", "剂量", "君臣佐使", "加减", "方子", "整方", "全方", "单用",
         "比单用", "formula", "prescription", "decoction", "composition", "recipe",
         "whole formula", "than its constituent"),
        (Step("versions", "the formula's recorded source versions, doses as written", (
            _e("native.tcm_formula", "recorded", name="<formula>"),
            _e("tcmdb.formulas", "recorded", query="<formula>", rank=True),
            _e("native.hkbu_formula_lookup", "recorded", formula="<formula>"),
            _e("native.tcm_classical_search", "recorded", query="<formula>"))),
         Step("herbs", "the herbs each database lists for it", (
             _hub("formula_herb", subject="<formula>"),
             _e("tcmdb.consensus", "per_row", kind="formula_herb", subject="<formula>"),
             _c("herb_api", "search", "curated", keyword="<formula>", label="Formula"),
             _c("itcm", "herb_formulas", "curated", "the formulas that contain a herb (the reverse "
                "direction)", herb_id="<ITCM herb id>"))),
         Step("compatibility", "recorded incompatibilities among its herbs (十八反, 十九畏, "
                               "妊娠禁忌)", (
             _e("native.tcm_compatibility", "recorded", herbs=["<herb>", "<herb>"]),)),
         Step("absorbed", "constituents detected in blood after the formula was given", (
             _c("dcabm_tcm", "prescription_blood", "measured", names=[_PINYIN]),)),
         Step("versus_constituent", "whether the whole formula does more than its main "
                                    "constituent on one endpoint", (
             _e("study.contrast_formula_monomer", "analysis", "measured effects of both at "
                "matched dose, against an equivalence margin fixed in advance",
                formula={"label": "formula", "values": ["<replicate values>"],
                         "study": "<study>"},
                monomer={"label": "monomer:<constituent>", "values": ["<replicate values>"],
                         "study": "<the same study>"},
                margin="<margin, fixed in advance>"),))),
        "Keep source versions apart (one name can be different formulas); a database's herb "
        "list is one version, so say which.",
        scaffold="formula_vs_constituent",
        then=("herb_composition", "formula_mechanism", "safety"),
        cautions=("Recorded composition and dose are the text's, not a prescription.",
                  "A compatibility record is a traditional rule for a practitioner to weigh, not "
                  "a measured interaction."),
    ),
    Route(
        "traditional_use", ("传统功效与主治", "Traditional use, syndromes and indications"),
        "What a herb or formula is recorded to treat in TCM terms (actions, syndromes, "
        "symptoms), how those map to modern names, and what the record licenses.",
        ("功效", "主治", "证候", "证型", "辨证", "症状", "归经", "性味", "古籍", "传统", "治什么",
         "traditional use", "indication", "syndrome", "symptom", "classical", "used for"),
        (Step("recorded", "actions, indications and passages as recorded", (
            _e("native.tcm_herb", "recorded", name="<herb>"),
            _e("native.tcm_syndrome", "recorded", name="<syndrome>"),
            _e("native.tcm_classical_search", "recorded", query="<herb, formula or phrase>"),
            _e("tcmdb.formulas", "recorded", "formulas whose recorded 功效 mentions a term",
               indication="<功效 term, e.g. 脾胃气虚>"))),
         Step("databases", "the syndromes, symptoms and diseases the TCM databases link to it", (
             _hub("herb_syndrome", subject="<herb>"),
             _hub("herb_symptom", subject="<herb>"),
             _hub("herb_disease", subject="<herb>"),
             _c("symmap", "related", "curated", entity_id="<SymMap herb id SMHB…>",
                related="Syndrome"),
             _c("symmap", "related", "curated", "modern-medicine symptoms",
                entity_id="<SymMap herb id SMHB…>", related="MM_symptom"),
             _c("itcm", "herb_diseases", "predicted", "diseases enriched among the herb's "
                "targets (p, FDR): a computation, not a record of use", herb_id="<ITCM herb id>"))),
         Step("modern_names", "the modern terms a symptom or a disease maps to", (
             _c("mesh", "descriptor", "reference", label="<term>"),
             _c("hpo", "search", "reference", q="<symptom>"),
             _c("nlm_clinical_tables", "conditions", "reference", terms="<condition>"),
             _c("nlm_clinical_tables", "icd10cm", "reference", terms="<condition>"))),
         Step("licensing", "what the recorded evidence licenses", (
             _e("native.tcm_applicability", "analysis", subject="<herb or formula>",
                object="<condition or syndrome>", claim_kind="traditional_use"),
             _e("native.tcm_evidence_tiers", "reference")))),
        "Give the recorded use with its source; a syndrome is not a disease, so give a mapping "
        "as a mapping, never as the same thing.",
        then=("efficacy_evidence", "formula_mechanism"),
        cautions=("A recorded use is tradition: it licenses a traditional_use claim, never "
                  "efficacy.",),
    ),
    Route(
        "compound_identity", ("化合物身份、结构与来源", "Compound identity, structure and origin"),
        "What a compound is (identifiers, structure, class), how its names map across "
        "databases, and in which organisms it occurs.",
        ("化合物", "结构", "分子式", "单体", "标识符", "化学式", "inchikey", "smiles", "cas",
         "molecule", "structure", "identifier", "pubchem", "chebi"),
        (Step("identifiers", "identifiers, structure and synonyms", (
            _c("pubchem", "cid_by_name", "reference", name="<compound>"),
            _c("pubchem", "properties_by_cid", "reference", cid="<PubChem CID>"),
            _c("pubchem", "synonyms", "reference", cid="<PubChem CID>"),
            _c("chebi", "search", "reference", term="<compound>"),
            _c("chebi", "compound", "reference", chebi_id="<ChEBI id>"),
            _c("chebi", "compounds", "reference", "several at once; secondary ids resolve to "
               "their primary entry", chebi_ids="<ChEBI ids, comma-separated>"),
            _c("chembl", "molecule", "reference", chembl_id="<ChEMBL molecule id>"),
            _c("metabolomics_workbench", "refmet_match", "reference", name="<compound>"),
            _c("metabolomics_workbench", "compound_by_inchikey", "reference",
               inchikey="<InChIKey>"),
            _c("metanetx", "chem_by_xref", "reference", xref="<e.g. chebi:28499>"),
            _c("tcmbank", "ingredient_search", "curated", name="<compound>"),
            _c("drugcentral", "drug_by_name", "reference", "for a compound that is also a drug",
               name="<drug>"),
            _table("metanetx", "reference", "MNXref: the same metabolite across ChEBI, KEGG, "
                   "HMDB, MetaCyc and others"),
            _table("drugbank_vocabulary", "reference", "DrugBank open vocabulary: names, "
                   "synonyms, CAS and UNII"))),
         Step("class", "its chemical class, its role and similar structures", (
             _hub("compound_class", subject="<compound or ChEBI id>"),
             _hub("compound_role", subject="<compound or ChEBI id>"),
             _c("chebi", "parents", "reference", chebi_id="<ChEBI id>"),
            _c("chebi", "children", "reference", "the entities that point to it (subclasses, "
               "derivatives, conjugates)", chebi_id="<ChEBI id>"),
             _c("chebi", "structure_search", "reference", "structurally similar compounds",
                smiles="<SMILES>"))),
         Step("occurrence", "the organisms it is reported from", (
             _hub("organism_compound", object="<compound>"),
             _c("wikidata_sparql", "taxa_with_compound", "curated", inchikey="<InChIKey>"),
             _c("knapsack", "metabolite_search", "curated", name="<compound>"),
             _c("knapsack", "metabolite", "curated", c_id="<KNApSAcK C_ID>"),
             _c("coconut", "search", "curated", query="<name, SMILES or InChIKey>"),
             _c("mibig", "entry", "curated", "a microbial gene cluster that makes it",
                accession_version="<BGC accession.version>")))),
        "Join records on the InChIKey or the PubChem CID, never on names: name matches across "
        "databases are often other stereoisomers or salts.",
        then=("compound_targets", "exposure_metabolism", "spectra_identification", "safety"),
        cautions=("A name-only match is ambiguous: give the identifier the answer rests on.",),
        ids=(r"\b[A-Z]{14}-[A-Z]{10}-[A-Z]\b", r"\bCID:?\s*\d+", r"\bCHEMBL\d+"),
    ),
    Route(
        "compound_targets", ("化合物的作用靶点", "What a compound acts on"),
        "Which proteins a compound acts on, with measured activities kept apart from curated "
        "links and from predictions, and how many independent sources agree.",
        ("靶点", "作用靶点", "结合", "抑制", "激动", "活性", "亲和力", "ic50", "ki", "target",
         "binding", "inhibit", "inhibitor", "agonist", "affinity", "mechanism of action"),
        (Step("measured", "activities measured in assays", (
            _hub("compound_target", subject="<compound>"),
            _hub("compound_assay", subject="<compound>"),
            _c("drugcentral", "activities", "measured", "for a compound that is a drug",
               struct_id="<DrugCentral struct id>"),
            _c("pharos", "ligand", "measured", ligand_id="<CID:… or name>"),
            _c("klifs", "ligand_bioactivities", "measured", "for a kinase ligand",
               ligand_id="<KLIFS ligand id>"),
            _c("chembl", "activities", "measured", "the activities measured on one candidate "
               "target", target="<ChEMBL target id>"))),
         Step("curated", "interactions curated from publications", (
             _hub("drug_target", subject="<drug>"),
             _c("signor", "entity_relations", "curated", id="<PubChem CID>"),
             _c("intact", "psicquic_query", "curated", query="<compound name or CHEBI id>"),
             _c("dgidb", "drug_interactions", "aggregated", drug="<drug>"))),
         Step("tcm_databases", "the targets the TCM databases list, many of them predicted", (
             _hub("ingredient_target", subject="<compound>"),
             _c("itcm", "ingredient_targets", "curated", "each pair with its source database",
                ingredient_id="<ITCM ingredient id>"),
             _c("symmap", "related", "curated", entity_id="<SymMap ingredient id SMIT…>",
                related="Gene"),
             _c("herb_api", "detail", "curated", entity_id="<HERB ingredient id HBIN…>",
                label="Ingredient"),
             _c("tcmbank", "detail", "curated", kind="ingredient",
                tcmbank_id="<TCMBank ingredient id>"))),
         Step("agreement", "how many independent sources support each target", (
             _e("tcmdb.consensus", "per_row", kind="ingredient_target", subject="<compound>"),
             _e("tcmdb.consensus", "per_row", kind="compound_target", subject="<compound>"))),
         Step("binding", "a structure-based prediction for a shortlisted target (a job)", (
             _e("job.pipeline.dock", "predicted", receptor="<PDB:id, UniProt:accession or file>",
                ligands="<SMILES or file>"),))),
        "List measured targets first with their values and assays, then curated links, then "
        "predictions; tcmdb_consensus counts independent lineages once.",
        scaffold="target_engagement",
        then=("target_profile", "pathways_networks", "exposure_metabolism"),
        cautions=("A target list is not a mechanism: activity has to be shown at concentrations "
                  "the compound reaches (exposure_metabolism).",
                  "Predicted and docking targets support hypotheses only."),
    ),
    Route(
        "target_profile", ("靶点的身份、功能、表达与成药性",
                           "A target: identity, function, expression and tractability"),
        "What a gene or protein is, what it does, where it is expressed, how constrained it is "
        "in people, and how tractable it is as a drug target.",
        ("基因", "蛋白", "靶标", "功能", "表达", "组织分布", "成药性", "敲除", "gene", "protein",
         "function", "expression", "tissue", "druggability", "tractability", "knockout",
         "essential"),
        (Step("identity", "the gene and protein records", (
            _c("hgnc", "symbol", "reference", symbol="<gene symbol>"),
            _c("uniprot", "search", "reference", query="<gene symbol> AND organism_id:9606"),
            _c("uniprot", "entry", "curated", accession="<UniProt accession>"),
            _c("ensembl", "gene_lookup", "reference", symbol="<gene symbol>"),
            _c("ncbi_datasets", "gene_by_symbol", "reference", symbol="<gene symbol>"),
            _c("mygene", "query", "reference", q="<gene symbol>"),
            _c("ensembl", "xrefs", "reference", id="<Ensembl gene id>"),
            _c("ensembl", "sequence", "reference", id="<Ensembl id>"),
            _c("uniprot", "fasta", "reference", accession="<UniProt accession>"))),
         Step("function", "functions, domains, modification sites and disordered regions", (
             _c("quickgo", "annotations", "curated", gene_product="<UniProt accession>"),
             _c("interpro", "protein_entries", "predicted", "domain signatures matched to the "
                "sequence", accession="<UniProt accession>"),
             _c("interpro", "protein", "reference", accession="<UniProt accession>"),
             _c("interpro", "entry", "curated", ipr="<IPR…>"),
             _c("disprot", "search_by_accession", "curated", accession="<UniProt accession>"),
             _c("disprot", "entry", "curated", disprot_id="<DisProt id>"),
             _hub("protein_region", subject="<UniProt accession>"),
             _hub("ptm_site", "the sites enzymes modify on it", object="<UniProt accession>"),
             _hub("protein_site", subject="<UniProt accession>"),
             _c("omnipath", "annotations", "aggregated", proteins="<UniProt accession>"),
             _e("native.protein_properties", "analysis", sequence="<protein sequence>"))),
         Step("expression", "where it is expressed, as RNA and as protein", (
             _c("gtex", "median_expression", "measured", gencode_id="<versioned GENCODE id>"),
             _c("gtex", "gene", "reference", gene_id="<gene symbol or GENCODE id>"),
             _c("proteinatlas", "gene", "measured", ensembl_id="<Ensembl gene id>"),
             _c("proteinatlas", "search", "measured", query="<gene symbol>"),
             _c("proteomicsdb", "expression", "measured", uniprot="<UniProt accession>"))),
         Step("tractability", "its development level, known ligands and drugs", (
             _c("pharos", "target", "curated", symbol="<gene symbol>"),
             _c("pharos", "target_ligands", "measured", symbol="<gene symbol>"),
             _c("pharos", "tdl_counts", "reference", "how many targets are at each development "
                "level, the background for one target's level"),
             _c("pharos", "version", "reference", "the release to cite"),
             _c("chembl", "target_search", "reference", symbol="<gene symbol>"),
             _c("chembl", "target", "reference", chembl_id="<ChEMBL target id>"),
             _c("drugcentral", "gene_activities", "measured", gene="<gene symbol>"),
             _c("dgidb", "gene_interactions", "aggregated", gene="<gene symbol>"),
             _c("ttd", "target", "curated", target_id="<TTD target id>"),
             _c("ttd", "target_drugs", "curated", target_id="<TTD target id>"),
             _c("gpcrdb", "protein_by_accession", "curated", "for a GPCR",
                accession="<UniProt accession>"),
             _c("gpcrdb", "drugs", "curated", entry_name="<UniProt entry name, e.g. adrb2_human>"),
             _c("gpcrdb", "ligands", "measured", entry_name="<UniProt entry name>"),
             _c("klifs", "kinase_id", "curated", "for a kinase", name="<kinase name>"),
             _hub("gene_set_member", "the gene sets it belongs to (Pharos development levels, "
                  "ImmPort lists)", object="<gene symbol>"))),
         Step("genetics", "constraint, variant effects and what losing it does", (
             _c("gnomad", "gene_constraint", "measured", symbol="<gene symbol>"),
             _c("mavedb", "score_sets_by_target", "measured", "deep mutational scans",
                gene="<gene symbol>"),
             _c("impc", "phenotype_calls", "associated", symbol="<mouse gene symbol>"),
             _c("impc", "statistical_results", "associated", "every test of its knockout lines, "
                "significant or not (a tested negative counts)", symbol="<mouse gene symbol>"),
             _hub("gene_phenotype", subject="<gene symbol>"),
             _hub("screen_gene", "CRISPR screens that hit it", object="<gene symbol>")))),
        "Give each fact with its source; RNA (GTEx) and protein (ProteomicsDB, HPA) expression "
        "can disagree, and both are tissue averages.",
        then=("structure_binding", "pathways_networks", "disease_links", "gene_regulation"),
        cautions=("Expression in a tissue is not activity there, and a mouse knockout's "
                  "phenotype is not the human one.",),
    ),
    Route(
        "pathways_networks", ("通路、富集与相互作用网络", "Pathways, enrichment and interaction networks"),
        "Which pathways a set of genes or targets falls in, whether that is more than chance "
        "against a stated background, and how the proteins connect.",
        ("通路", "富集", "网络", "相互作用", "信号通路", "互作", "kegg", "go", "pathway",
         "enrichment", "network", "interaction", "signalling", "signaling", "ppi"),
        (Step("membership", "the pathways each gene or protein belongs to", (
            _c("reactome", "pathways_for_entity", "curated",
               id="<UniProt accession or Reactome id>"),
            _c("reactome", "query", "curated", id="<Reactome stable id>"),
            _c("reactome", "version", "reference", "the release to cite"),
            _c("kegg", "link", "curated", target_db="pathway", entry="<hsa:gene id>"),
            _c("kegg", "find", "reference", db="pathway", query="<term>"),
            _c("kegg", "get", "curated", entry="<KEGG id>"),
            _c("kegg", "info", "reference", "the release to cite"),
            _c("wikipathways", "find", "curated", query="<term or gene>"),
            _c("wikipathways", "info", "curated", pathway_id="<WP…>"),
            _c("wikipathways", "list", "reference", "every pathway, for a gene-set library of "
               "your own"),
            _c("panther", "geneinfo", "curated", genes="<gene ids, comma-separated>"),
            _c("signor", "pathway_relations", "curated", pathway="<SIGNOR pathway id>"),
            _c("signor", "pathway_description", "curated", pathway="<SIGNOR pathway id>"),
            _c("ttd", "target_pathways", "curated", target_id="<TTD target id>"))),
         Step("enrichment", "over-representation against a stated background", (
             _c("gprofiler", "gost", "analysis", query=_GENES),
             _c("panther", "enrich", "analysis", genes="<gene ids, comma-separated>"),
             _e("native.enrichment_analysis", "analysis", "gene sets and background you state",
                genes=["<gene>"], gene_sets={"<set>": ["<gene>"]}, background="<background size>"),
             _e("native.hypergeometric_test", "analysis", overlap="<k>", query_size="<n>",
                set_size="<K>", background="<N>"),
             _e("native.benjamini_hochberg", "analysis", p_values=["<p>"]))),
         Step("interactions", "how the proteins connect", (
             _hub("protein_interaction", subject="<UniProt accession or gene>"),
             _hub("complex_member", object="<UniProt accession>"),
             _hub("regulation", subject="<regulator>"),
             _hub("genetic_interaction", subject="<gene>"),
             _c("intact", "interactions", "curated", query="<gene or UniProt accession>"),
             _c("intact", "psicquic_count", "reference", "how many interaction evidences match, "
                "before fetching them", query="<MIQL query or gene>"),
             _c("intact", "complex_search", "curated", query="<gene or complex>"),
             _c("intact", "complex", "curated", complex_ac="<CPX-…>"),
             _c("signor", "relations_among", "curated", proteins="<UniProt accessions, "
                "comma-separated>"),
             _c("omnipath", "interactions", "curated", partners="<UniProt accessions or symbols>"),
             _c("string", "network", "aggregated", identifiers="<genes>"),
             _c("pharos", "target_interactions", "aggregated", symbol="<gene symbol>")))),
        "State the background, the gene-set library and the multiple-testing correction of "
        "every enrichment. STRING scores and Pharos partners are aggregated: keep them apart "
        "from curated interactions.",
        scaffold="pathway_signal",
        then=("disease_links", "gene_regulation"),
        cautions=("Enrichment of a predicted target list says which pathways the predictions "
                  "fall in, not what the herb does.",
                  "A pathway named without its background is not a result."),
    ),
    Route(
        "gene_regulation", ("基因调控：转录因子、miRNA 与染色质",
                            "Gene regulation: transcription factors, miRNAs and chromatin"),
        "What regulates a gene: the transcription factors that bind it and their motifs, the "
        "miRNAs that target it, chromatin contacts and expression QTLs.",
        ("调控", "转录因子", "启动子", "增强子", "表观", "染色质", "mirna", "eqtl", "motif",
         "regulation", "transcription factor", "promoter", "enhancer", "chip-seq", "chromatin",
         "epigenetic", "epigenomic"),
        (Step("transcription_factors", "the factors bound at the gene, and their motifs", (
            _hub("tf_target", object="<gene symbol>"),
            _hub("tf_motif", subject="<factor>"),
            _c("chip_atlas", "target_genes_index", "reference"),
            _c("chip_atlas", "antigens", "data", ag_class="TFs and others", genome="hg38"),
            _c("chip_atlas", "experiment", "data", expid="<SRX… or GSM…>"),
            _c("chip_atlas", "genomes", "reference"),
            _c("remap", "datasets_by_target", "data", target="<factor>", taxid=9606),
            _c("remap", "datasets_by_biotype", "data", biotype="<cell type>", taxid=9606),
            _table("remap", "data", "ReMap: which regulator was profiled in which cell type, "
                   "with each BED file"),
            _c("jaspar", "factor_matrices", "curated", name="<factor>"),
            _c("jaspar", "search", "curated", query="<factor>"),
            _c("jaspar", "matrix", "curated", matrix_id="<MA…>"),
            _c("jaspar", "versions", "curated", "every version of a matrix", base_id="<MA…>"),
            _c("jaspar", "releases", "reference", "the releases, to pin a version"),
            _c("encode", "search", "data", term="<factor or gene>", type="Experiment"),
            _e("native.motif_search", "analysis", sequence="<DNA>", motif="<IUPAC motif>"))),
         Step("mirnas", "the miRNAs and other ncRNAs that target it", (
             _hub("mirna_target", object="<gene symbol>"),
             _c("rnacentral", "rna_by_external_id", "reference", external_id="<miRBase MIMAT…>"),
             _c("rnacentral", "rna", "reference", urs="<URS id>"),
             _c("rnacentral", "xrefs", "reference", urs="<URS id>", taxid=9606),
             _c("rnacentral", "protein_targets", "curated", urs="<URS id>", taxid=9606),
             _c("rnacentral", "interactions", "curated", urs="<URS id>", taxid=9606),
             _c("rnacentral", "go_annotations", "curated", urs="<URS id>", taxid=9606),
             _table("rnacentral", "reference", "RNAcentral id mappings (miRBase, HGNC, TarBase, "
                    "LncBase)"))),
         Step("chromatin", "chromatin contacts, region tracks and the sequence", (
             _hub("chromatin_loop", subject="<chr:start-end>"),
             _c("fourdn", "experiment_sets_by_sample", "data", sample="<biosource, e.g. H1-hESC>"),
             _c("fourdn", "processed_files_by_format", "data", file_format="bedpe"),
             _c("fourdn", "experiment_set", "data", accession="<4DNES…>"),
             _c("fourdn", "processed_file", "data", accession="<4DNFI…>"),
             _c("ucsc", "track", "curated", genome="hg38", chrom="<chr>", start="<start>",
                end="<end>", track="<track>"),
             _c("ucsc", "sequence", "reference", genome="hg38", chrom="<chr>", start="<start>",
                end="<end>"),
             _e("native.cpg_islands", "analysis", sequence="<DNA>"))),
         Step("eqtl", "the variants that change its expression", (
             _hub("variant_gene", object="<gene>"),))),
        "Say the cell type of every binding claim; ReMap and ChIP-Atlas reprocess the same "
        "public experiments, so compare them by series accession before counting binding "
        "twice.",
        then=("pathways_networks", "target_profile"),
        cautions=("Binding, motif presence and an eQTL each suggest regulation; none shows it "
                  "alone.",),
    ),
    Route(
        "disease_links", ("靶点、基因与疾病的关联", "Genes, targets and diseases"),
        "Which diseases a gene or target is linked to and how (genetic association, curated "
        "gene–disease links, animal models, phenotypes), and which genes a disease implicates.",
        ("疾病", "相关基因", "致病", "遗传", "表型", "罕见病", "易感", "disease", "association",
         "genetic", "phenotype", "rare disease", "gwas", "mouse model", "susceptibility"),
        (Step("associations", "gene–disease links and their sources", (
            _hub("target_disease", subject="<gene symbol>"),
            _c("opentargets", "associated_diseases", "aggregated", ensembl_id="<Ensembl gene id>"),
            _c("opentargets", "target", "curated", ensembl_id="<Ensembl gene id>"),
            _c("pharos", "target_diseases", "aggregated", symbol="<gene symbol>"),
            _c("monarch", "associations", "curated", subject="<gene or disease CURIE>"),
            _c("monarch", "search", "reference", q="<gene or disease>"))),
         Step("genetics", "genome-wide associations", (
             _c("gwas_catalog", "snps_by_gene", "associated", gene="<gene symbol>"),
             _c("gwas_catalog", "associations_by_rsid", "associated", rs_id="<rs…>"),
             _c("gwas_catalog", "snp", "reference", rs_id="<rs…>"))),
         Step("rare_disease", "rare diseases, their genes, phenotypes and natural history", (
             _c("orphadata", "by_name", "reference", name="<disease>"),
             _c("orphadata", "genes", "curated", orphacode="<ORPHA code>"),
             _c("orphadata", "phenotypes", "curated", orphacode="<ORPHA code>"),
             _c("orphadata", "epidemiology", "curated", orphacode="<ORPHA code>"),
             _c("orphadata", "natural_history", "curated", orphacode="<ORPHA code>"),
             _c("orphadata", "classifications", "reference", orphacode="<ORPHA code>"),
             _c("orphadata", "cross_references", "reference", orphacode="<ORPHA code>"),
             _c("orphadata", "by_omim", "reference", omim="<OMIM number>"),
             _c("orphadata", "by_hpo", "reference", hpoids="<HP:…>"),
             _hub("disease_phenotype", subject="<disease>"))),
         Step("phenotypes", "the phenotypes annotated to genes, and animal models", (
             _c("hpo", "term_annotations", "curated", hpo_id="<HP:…>"),
             _c("hpo", "term", "reference", hpo_id="<HP:…>"),
             _c("impc", "disease_models", "associated", symbol="<mouse gene symbol>"),
             _c("impc", "genes_with_phenotype", "associated", mp_id="<MP:…>"),
             _c("impc", "gene", "reference", symbol="<mouse gene symbol>"),
             _c("disease_ontology", "metadata", "reference", doid="<DOID:…>"))),
         Step("tcm_side", "the diseases the TCM databases link to a herb or an ingredient", (
             _hub("herb_disease", subject="<herb>"),
             _hub("ingredient_disease", subject="<compound>"),
             _c("herb_api", "detail", "curated", entity_id="<HERB disease id>", label="Disease"),
             _c("tcmbank", "detail", "curated", kind="disease",
                tcmbank_id="<TCMBank disease id>")))),
        "Give each link's kind: an Open Targets or Pharos score is aggregated, a GWAS hit is an "
        "association, Orphanet and Monarch links are curated; never add scores across sources.",
        then=("efficacy_evidence", "pathways_networks"),
        cautions=("A gene linked to a disease is not a treatment target for it, and a herb's "
                  "target that is also a disease gene shows overlap, not an effect.",),
    ),
    Route(
        "efficacy_evidence", ("疗效证据：试验、荟萃分析与文献",
                              "Efficacy evidence: trials, meta-analyses and studies"),
        "Whether a herb or formula works for a condition in people: registered and published "
        "trials, meta-analyses, their designs, and what the evidence licenses.",
        ("疗效", "有效", "临床试验", "随机", "对照", "荟萃", "系统评价", "临床证据", "有没有用",
         "管用", "efficacy", "effective", "trial", "randomized", "randomised", "rct",
         "meta-analysis", "systematic review", "clinical evidence", "evidence", "works for",
         "does it work", "benefit"),
        (Step("recorded", "the trials, meta-analyses and papers the data hub records for it", (
            _e("tcmdb.evidence_for", "per_row", subject="<herb or formula>"),
            _hub("subject_clinical_trial", subject="<herb or formula>"),
            _hub("subject_meta_analysis", subject="<herb or formula>"),
            _hub("subject_reference", subject="<herb or formula>"))),
         Step("registered", "registered trials, with their status and any posted results", (
             _c("clinicaltrials", "search", "registered", term="<intervention AND condition>"),
             _c("clinicaltrials", "study", "registered", nct_id="<NCT…>"))),
         Step("published", "published trials and reviews", (
             _c("europepmc", "search", "literature", query="<intervention> AND <condition> AND "
                "(PUB_TYPE:\"Randomized Controlled Trial\" OR PUB_TYPE:\"Meta-Analysis\")"),
             _c("ncbi_eutils", "esearch", "literature", db="pubmed",
                term="<intervention>[tiab] AND <condition>[tiab] AND randomized controlled "
                     "trial[pt]"),
             _e("skill.retrieve-tcm-evidence", "literature", "a governed search whose results "
                "carry their study design", subject="<herb or formula>", claim_kind="efficacy"))),
         Step("synthesis", "pooled estimates, subgroups and what the evidence licenses", (
             _e("native.meta_analysis", "analysis", estimates=["<log effect>"],
                standard_errors=["<standard error>"]),
             _e("study.effect_modification", "analysis", "whether the effect differs by a "
                "baseline characteristic", outcome=["<outcome>"], treated=["<0 or 1>"],
                modifiers=[{"name": "<modifier>", "values": ["<value>"], "baseline": True}]),
             _e("native.tcm_applicability", "analysis", subject="<herb or formula>",
                object="<condition>", claim_kind="efficacy")))),
        "Weigh by design and risk of bias, not by count: one adequately powered randomised "
        "trial outweighs many small or uncontrolled ones. Say which trials are only "
        "registered.",
        scaffold="efficacy",
        then=("safety",),
        ids=(r"\bNCT\d{8}\b", r"\bChiCTR[-\w]+"),
        cautions=("A registered trial is a plan and a search hit is a pointer to read; an "
                  "efficacy claim is licensed only by tcm_applicability and the release gate.",),
    ),
    Route(
        "safety", ("安全性：毒性、不良事件与禁忌", "Safety: toxicity, adverse events and contraindications"),
        "What is known about harm from a herb, formula or compound: recorded cautions and "
        "incompatibilities, toxicity data and adverse outcome pathways, and adverse-event "
        "reports.",
        ("安全", "毒性", "不良反应", "副作用", "肝毒性", "肾毒性", "禁忌", "孕妇", "十八反", "十九畏",
         "配伍禁忌", "反药", "一起用", "同用", "safety", "safe", "toxicity", "toxic", "adverse",
         "side effect", "hepatotoxicity", "nephrotoxicity", "contraindication", "pregnancy"),
        (Step("recorded", "recorded cautions, incompatibilities and the governed safety report", (
            _e("skill.assess-tcm-safety", "recorded", subject="<herb or formula>"),
            _e("native.tcm_compatibility", "recorded", herbs=["<herb>", "<herb>"]),
            _e("native.tcm_herb", "recorded", "its recorded safety records", name="<herb>"),
            _hub("subject_monograph", "EMA herbal monographs", subject="<herb or Latin name>"))),
         Step("toxicity", "toxicity records, assays and adverse outcome pathways", (
             _hub("subject_toxicity", subject="<herb, formula or compound>"),
             _hub("compound_assay", subject="<compound>"),
             _hub("assay_target", subject="<ToxCast assay>"),
             _hub("assay_event", subject="<ToxCast assay>"),
             _hub("stressor_event", subject="<compound>"),
             _hub("stressor_aop", subject="<stressor>"),
             _hub("aop_event", subject="<AOP id>"),
             _hub("key_event_relationship", subject="<key event>"),
             _c("aopwiki", "aop", "curated", aop_id="<AOP id>"),
             _c("aopwiki", "aop_xml", "curated", aop_id="<AOP id>"),
             _c("aopwiki", "event", "curated", event_id="<key event id>"),
             _c("aopwiki", "relationship", "curated", relationship_id="<relationship id>"),
             _e("job.pipeline.admet", "predicted", "ADMET models (a job)",
                molecules="<SMILES or file>"),
             _e("skill.predict-admet", "predicted", "the governed ADMET skill (a job)",
                molecules="<SMILES or file>"))),
         Step("reports", "adverse-event reports and signals", (
             _c("openfda", "herbal_event_reactions", "reported",
                product="<product name, e.g. GINKGO>"),
             _c("openfda", "drug_event", "reported", search="<openFDA query>"),
             _hub("drug_adverse_event", subject="<drug>"),
             _c("drugcentral", "faers", "reported", struct_id="<DrugCentral struct id>"),
             _table("faers", "reported", "FAERS quarterly files, for analyses openFDA does not "
                    "give"))),
         Step("labels", "a drug's label: warnings, contraindications, populations", (
             _c("openfda", "drug_label", "curated", search="<openFDA label query>"),
             _c("dailymed", "spls", "curated", drug_name="<drug>"),
             _c("drugcentral", "indications", "curated", struct_id="<DrugCentral struct id>")))),
        "Report every signal found, with its kind: a report count is not an incidence. Name "
        "the population each caution concerns (pregnancy, children, liver or kidney disease).",
        scaffold="safety_signal",
        then=("herb_drug_interaction", "exposure_metabolism"),
        cautions=("A safety signal does not wait for acceptance: report it now, with what "
                  "would confirm or refute it.",
                  "A missing safety record or an absence of reports is not evidence of safety."),
    ),
    Route(
        "herb_drug_interaction", ("中药与西药相互作用", "Herb–drug interactions"),
        "Whether a herb (or its constituents) interacts with a drug: recorded interactions, "
        "shared metabolising enzymes and transporters, pharmacogenes, and co-reported adverse "
        "events.",
        ("相互作用", "合用", "联用", "同服", "西药", "代谢酶", "华法林", "herb-drug",
         "drug interaction", "co-administration", "cyp", "warfarin", "taken with"),
        (Step("recorded", "recorded herb–drug interactions", (
            _hub("herb_drug_interaction", subject="<herb>"),
            _e("skill.assess-tcm-safety", "recorded", "with the drugs taken",
               subject="<herb or formula>", co_administered=["<drug>"]))),
         Step("the_drug", "the drug: its identity, label and pharmacogenes", (
             _c("rxnav", "rxcui_by_name", "reference", name="<drug>"),
             _c("rxnav", "approximate", "reference", term="<drug name as written>"),
             _c("rxnav", "properties", "reference", rxcui="<RxCUI>"),
             _c("rxnav", "related", "reference", rxcui="<RxCUI>"),
             _c("rxnav", "drug_classes", "reference", rxcui="<RxCUI>"),
             _c("nlm_clinical_tables", "rxterms", "reference", terms="<drug>"),
             _c("dailymed", "drug_names", "reference", drug_name="<drug>"),
             _c("openfda", "drug_label", "curated", "the label's drug-interaction section",
                search="openfda.generic_name:\"<drug>\""),
             _c("drugcentral", "structure", "reference", struct_id="<DrugCentral struct id>"),
             _c("drugcentral", "structure_by_inchikey", "reference", inchikey="<InChIKey>"),
             _c("drugcentral", "identifiers", "reference", struct_id="<DrugCentral struct id>"),
             _c("drugcentral", "atc", "reference", struct_id="<DrugCentral struct id>"),
             _hub("drug_pharmacogene", subject="<drug>"),
             _c("cpic", "pairs_for_drug", "curated", drug="<drug, lower case>"))),
         Step("shared_enzymes", "the enzymes and transporters both act on", (
             _hub("ingredient_target", subject="<compound>"),
             _hub("drug_target", subject="<drug>"),
             _c("drugcentral", "target_activities", "measured",
                accession="<UniProt accession of the enzyme, e.g. P08684 for CYP3A4>"),
             _hub("enzyme_reaction", subject="<UniProt accession of the enzyme>"))),
         Step("co_reports", "adverse events reported with both", (
             _c("openfda", "herbal_event_coreported_drugs", "reported", product="<product name>"),
             _c("openfda", "herbal_event_reactions", "reported", product="<product name>")))),
        "Keep recorded interactions (with their evidence level) apart from mechanisms inferred "
        "from shared enzymes, and both apart from co-reports.",
        scaffold="herb_drug_interaction",
        then=("variants_pharmacogenomics", "exposure_metabolism"),
        cautions=("Shared enzymes suggest an interaction; they do not show one at clinical "
                  "doses.", "Being reported together is not interacting."),
    ),
    Route(
        "exposure_metabolism",
        ("暴露与代谢：入血成分、代谢与药代",
         "Exposure and metabolism: absorbed constituents, metabolism, kinetics"),
        "Which constituents reach the blood and at what concentrations, how they are "
        "metabolised (enzymes, reactions, kinetics), and whether the concentrations reached "
        "are those at which they act.",
        ("入血", "血中", "吸收", "代谢", "药代", "药动", "生物利用度", "血药浓度", "代谢酶",
         "exposure", "absorption", "metabolism", "pharmacokinetics", "bioavailability",
         "plasma", "concentration", "metabolite"),
        (Step("absorbed", "constituents detected in blood after dosing", (
            _c("dcabm_tcm", "herb_blood", "measured", names=[_PINYIN]),
            _c("dcabm_tcm", "prescription_blood", "measured", names=[_PINYIN]),
            _c("dcabm_tcm", "ingredient", "measured", "a blood constituent: the prescriptions "
               "and herbs it was detected for", cids=["<PubChem CID>"]),
            _table("dcabm", "measured", "structures of every constituent DCABM-TCM detected in "
                   "blood"))),
         Step("metabolism", "the enzymes, reactions and kinetics", (
             _hub("reaction_participant", object="<compound>"),
             _hub("enzyme_reaction", subject="<enzyme UniProt accession>"),
             _c("rhea", "search", "curated", query="<compound or enzyme>"),
             _c("sabio_rk", "kinlaw_search", "measured", q="<enzyme or substrate query>"),
             _c("sabio_rk", "kinlaw_entry", "measured", entry_id="<SABIO entry id>"),
             _c("sabio_rk", "enzyme_by_ec", "reference", ec="<EC number>"),
             _c("sabio_rk", "protein", "reference", uniprot_id="<UniProt accession>"),
             _c("sabio_rk", "compound", "reference", compound_id="<SABIO compound id>"),
             _c("metanetx", "chem", "reference", mnx_id="<MNXM…>"),
             _c("phytohub", "entry", "curated", "dietary precursors and their metabolites",
                phub_id="<PHUB…>"))),
         Step("in_people", "studies that measured it in people", (
             _c("metabolomics_workbench", "studies_by_refmet_name", "data",
                refmet_name="<RefMet name>"),
             _table("nhanes", "data", "NHANES participant-level survey data: an association "
                    "must be estimated with the survey weights and design"))),
         Step("models", "kinetics, and whether the concentrations reached are active ones", (
             _e("study.exposure_screen", "analysis", "concentrations reached against those at "
                "which each activity was measured",
                exposures=[{"molecule": "<compound>", "species": "<species>", "site": "plasma",
                            "concentration": {"qualifier": "measured", "value": "<value>",
                                              "unit": "<unit>"}}],
                assays=[{"molecule": "<compound>", "target": "<target>", "endpoint": "<IC50>",
                         "result": {"qualifier": "measured", "value": "<value>",
                                    "unit": "<unit>"}}],
                site="plasma"),
             _e("native.pk_one_compartment", "analysis", dose_mg="<dose>", volume_l="<V>",
                half_life_h="<t½>"),
             _e("native.half_life_from_levels", "analysis", c1="<C1>", t1_h="<t1>", c2="<C2>",
                t2_h="<t2>"),
             _e("job.pipeline.admet", "predicted", "ADMET models (a job)",
                molecules="<SMILES or file>")))),
        "Compare the concentration reached (with its unit, species and the dose that gave it) "
        "with the concentration at which each activity was measured; say when either is "
        "missing.",
        scaffold="exposure",
        then=("compound_targets", "herb_drug_interaction"),
        cautions=("A constituent never detected in blood may still act (in the gut, or "
                  "through its metabolites), but that claim then needs its own evidence.",),
    ),
    Route(
        "structure_binding", ("结构与结合：结构、口袋与对接", "Structures, binding sites and docking"),
        "Which structures exist for a target (experimental or predicted), where ligands bind, "
        "and whether a compound could fit, as a hypothesis.",
        ("结构", "晶体结构", "对接", "分子对接", "结合位点", "口袋", "alphafold", "pdb",
         "structure", "docking", "binding site", "pocket", "crystal", "cryo-em"),
        (Step("experimental", "experimental structures and their ligands", (
            _c("pdbe", "best_structures", "measured", accession="<UniProt accession>"),
            _c("pdbe", "entry_summary", "measured", pdb_id="<PDB id>"),
            _c("pdbe", "molecules", "measured", pdb_id="<PDB id>"),
            _c("pdbe", "uniprot_mapping", "reference", pdb_id="<PDB id>"),
            _c("rcsb", "entry", "measured", pdb_id="<PDB id>"),
            _c("rcsb", "polymer_entity", "measured", pdb_id="<PDB id>", entity_id="<entity id>"),
            _c("klifs", "structures", "measured", "kinase structures with their pocket "
               "conformations", kinase_ids="<KLIFS kinase ids>"),
            _c("klifs", "ligands", "measured", kinase_ids="<KLIFS kinase ids>"),
            _c("klifs", "interaction_fingerprint", "measured", structure_id="<KLIFS structure id>"),
            _c("klifs", "kinase_names", "reference", group="<kinase group, e.g. TK>"),
            _c("klifs", "drugs", "curated"),
            _c("gpcrdb", "structure", "measured", pdb_code="<PDB code>"),
            _c("gpcrdb", "protein", "curated", entry_name="<UniProt entry name>"),
            _c("gpcrdb", "mutants", "measured", "mutagenesis data on ligand binding",
               entry_name="<UniProt entry name>"),
            _e("native.parse_pdb", "analysis", text="<PDB text>"))),
         Step("predicted", "predicted structures", (
             _c("alphafold", "prediction", "predicted", accession="<UniProt accession>"),
             _c("alphafold", "uniprot_summary", "predicted", accession="<UniProt accession>"),
             _e("job.pipeline.fold", "predicted", "a fold on the runner (a job)",
                source="<sequence or FASTA>"),
             _e("skill.predict-protein-structure", "predicted", "the governed structure "
                "prediction skill (a job)", sequences="<sequences>"))),
         Step("docking", "whether a compound could fit (a job)", (
             _e("job.pipeline.dock", "predicted", receptor="<PDB:id, UniProt:accession or file>",
                ligands="<SMILES or file>"),
             _e("skill.dock-ligands", "predicted", "the governed docking skill (a job)",
                receptor="<receptor>", ligands="<SMILES or file>")))),
        "Prefer an experimental structure with a ligand bound at the site docked into; a "
        "docking score ranks poses against one receptor, it is not an affinity.",
        scaffold="target_engagement",
        then=("compound_targets",),
        cautions=("Docking poses and predicted structures are hypotheses: binding is shown "
                  "only by an assay.",),
    ),
    Route(
        "variants_pharmacogenomics", ("变异与药物基因组", "Variants and pharmacogenomics"),
        "What a genetic variant does (frequency, predicted and measured effect, clinical "
        "significance) and how genes change a drug's response.",
        ("变异", "突变", "多态性", "药物基因组", "基因型", "等位基因", "snp", "variant", "mutation",
         "polymorphism", "pharmacogenomics", "pharmacogenetics", "genotype", "allele", "hgvs",
         "dosing"),
        (Step("the_variant", "its identity, frequency and annotations", (
            _c("myvariant", "variant", "curated", hgvs="<chr:g.posRef>Alt>"),
            _c("myvariant", "query", "curated", q="<rsid or gene>"),
            _c("gnomad", "variant", "measured", variant_id="<chrom-pos-ref-alt>"),
            _c("ensembl", "vep_hgvs", "predicted", hgvs="<HGVS>"),
            _e("native.parse_hgvs", "analysis", hgvs="<HGVS>"),
            _e("native.normalise_variant", "analysis", chrom="<chrom>", pos="<position>",
               ref="<ref>", alt="<alt>"),
            _e("native.annotate_coding_variant", "analysis", cds="<coding sequence>",
               position="<c. position>", ref="<ref>", alt="<alt>"))),
         Step("measured_effect", "measured effects of variants in the gene", (
             _c("mavedb", "search_score_sets", "measured", text="<gene>"),
             _c("mavedb", "gene_score_sets", "measured", symbol="<gene symbol>"),
             _c("mavedb", "score_set", "measured", urn="<urn:mavedb:…>"),
             _c("mavedb", "scores", "measured", urn="<urn:mavedb:…>"),
             _c("mavedb", "licenses", "reference"))),
         Step("drug_response", "gene–drug pairs, alleles and prescribing guidance", (
             _hub("drug_pharmacogene", object="<gene symbol>"),
             _hub("variant_drug", subject="<rsid or variant>"),
             _hub("allele_variant", subject="<allele, e.g. CYP2C9*3>"),
             _c("cpic", "pairs_for_gene", "curated", gene="<gene symbol>"),
             _c("cpic", "alleles", "curated", gene="<gene symbol>"),
             _c("cpic", "recommendations", "curated", drug="<drug, lower case>"),
             _c("cpic", "gene", "reference", gene="<gene symbol>"),
             _c("cpic", "drug", "reference", drug="<drug, lower case>"),
             _c("cpic", "pairs_by_level", "curated", level="A"),
             _c("cpic", "report_files", "reference", type="PAIR"))),
         Step("somatic", "clinical interpretations of somatic variants", (
             _c("civic", "gene", "curated", gene="<gene symbol>"),
             _c("civic", "variant", "curated", variant_id="<CIViC variant id>"))),
         Step("your_data", "genotypes you have", (
             _e("native.parse_vcf", "analysis", text="<VCF text>"),
             _e("native.allele_frequencies", "analysis", genotypes=["<genotype>"])))),
        "Keep population frequency, predicted consequence, measured effect and clinical "
        "guidance apart; a CPIC level says how actionable a gene–drug pair is.",
        then=("herb_drug_interaction", "disease_links"),
        cautions=("A predicted consequence (VEP) is not a measured effect, and guidance applies "
                  "to the drug it names.",),
        ids=(r"\brs\d{3,}\b", r"\b[cgnp]\.\d+", r"\b[A-Z0-9]{3,}\*\d+"),
    ),
    Route(
        "omics_datasets", ("组学数据：表达、单细胞、蛋白质组与代谢组",
                           "Omics data: expression, single-cell, proteomics and metabolomics"),
        "Which public datasets bear on the question (bulk and single-cell expression, "
        "proteomics, metabolomics, samples of a medicinal species) and how to analyse one here.",
        ("组学", "转录组", "测序", "单细胞", "蛋白质组", "代谢组", "数据集", "表达谱", "omics",
         "transcriptome", "rna-seq", "single-cell", "scrna-seq", "proteomics", "metabolomics",
         "dataset", "sequencing", "geo"),
        (Step("sequencing", "sequencing runs, samples and studies", (
            _c("ena", "search", "data", result="read_run", query="<ENA query>"),
            _c("ena", "filereport", "data", accession="<PRJ… or SRR…>"),
            _c("ncbi_eutils", "esearch", "data", db="gds", term="<GEO query>"),
            _c("ncbi_eutils", "esummary", "data", db="gds", id="<GEO ids>"),
            _c("ncbi_eutils", "efetch", "data", db="<database>", id="<ids>"),
            _c("ncbi_eutils", "einfo", "reference"),
            _c("biosamples", "by_organism", "data", "samples of a medicinal species",
               organism="<Latin binomial>"),
            _c("biosamples", "search", "data", text="<text>"),
            _c("biosamples", "sample", "data", accession="<SAM…>"),
            _c("biostudies", "search", "data", query="<text>"),
            _c("biostudies", "study", "data", accession="<E-MTAB-… or S-…>"),
            _c("encode", "experiment", "data", accession="<ENCSR…>"),
            _c("ncbi_datasets", "genome_report", "data", taxon="<taxon>"),
            _table("archs4", "data", "ARCHS4: about a million uniformly processed RNA-seq "
                   "samples"),
            _table("lincs_l1000", "data", "LINCS L1000: which perturbagen, dose, time and cell "
                   "line each signature is"))),
         Step("single_cell", "single-cell and spatial atlases", (
             _c("cellxgene", "collection", "data", collection_id="<collection id>"),
             _c("hca_azul", "project", "data", project_id="<HCA project UUID>"),
             _c("hca_azul", "file", "data", file_id="<file UUID>"),
             _c("hubmap_search", "datasets_by_organ", "data", organ_code="<organ code>"),
             _c("hubmap_search", "datasets_by_type", "data", dataset_type="<e.g. RNAseq>"),
             _c("hubmap_entity", "entity", "data", entity_id="<HuBMAP id>"),
             _c("hubmap_portal", "entity", "data", entity_type="dataset", uuid="<uuid>"),
             _c("hubmap_portal", "organ", "reference", organ="<organ slug>"),
             _hub("dataset_tissue", object="<tissue>"),
             _table("scperturb", "data", "scPerturb: harmonised single-cell perturbation "
                    "datasets"))),
         Step("proteomics_metabolomics", "proteomics and metabolomics studies", (
             _c("pride", "search", "data", keyword="<text>"),
             _c("pride", "project", "data", accession="<PXD…>"),
             _c("proteomicsdb", "protein", "reference", uniprot="<UniProt accession>"),
             _c("proteomicsdb", "dose_response_datasets", "data"),
             _c("metabolights", "studies", "data"),
             _c("metabolights", "study", "data", study_id="<MTBLS…>"),
             _c("metabolomics_workbench", "study_summary", "data", study_id="<ST…>"),
             _c("metabolomics_workbench", "study_analyses", "data", study_id="<ST…>"),
             _c("metabolomics_workbench", "study_metabolites", "data", study_id="<ST…>"),
            _c("metabolomics_workbench", "metstat", "data", "studies by species, sample source, "
               "disease or metabolite (an empty string for a slot not used)",
               analysis_type="<e.g. LCMS, or empty>", polarity="<positive, negative or empty>",
               chromatography="<e.g. HILIC, or empty>", species="<e.g. Human, or empty>",
               sample_source="<e.g. Blood, or empty>", disease="<disease, or empty>",
               kegg_id="<KEGG compound id, or empty>", refmet_name="<RefMet name, or empty>"))),
         Step("deposits", "datasets deposited in general repositories", (
             _c("figshare", "article", "data", article_id="<figshare id>"),
             _c("figshare", "article_files", "data", article_id="<figshare id>"),
             _c("huggingface", "dataset", "data", repo_id="<owner/name>"),
             _c("huggingface", "dataset_tree", "data", repo_id="<owner/name>"),
             _c("hf_datasets_server", "splits", "data", repo_id="<owner/name>"),
             _c("hf_datasets_server", "rows", "data", repo_id="<owner/name>"),
             _c("ebi_search", "search", "data", domain="<EBI Search domain>", query="<text>"),
             _c("ebi_search", "domains", "reference"))),
         Step("analysis", "analyse a dataset here (jobs, with approval)", (
             _e("job.pipeline.rnaseq", "analysis", samples="<sample sheet>"),
             _e("job.pipeline.scrna", "analysis", source="<h5ad or 10x matrix>"),
             _e("skill.rnaseq-differential-expression", "analysis", "the governed FASTQ-to-DESeq2 "
                "skill (a job)", sample_sheet="<sample sheet>"),
             _e("skill.scrna-cell-atlas", "analysis", "the governed single-cell atlas skill (a "
                "job)", source="<count matrices>"),
             _e("native.cpm", "analysis", counts=["<count>"]),
             _e("native.tpm", "analysis", counts=["<count>"], lengths_bp=["<length>"]),
             _e("native.log2_fold_change", "analysis", treated=["<value>"],
                control=["<value>"]),
             _e("native.welch_t_test", "analysis", x=["<value>"], y=["<value>"]),
             _e("native.correlation", "analysis", x=["<value>"], y=["<value>"])))),
        "Cite each dataset by accession with its organism, tissue, design and licence; a "
        "reanalysis inherits the dataset's design.",
        then=("pathways_networks",),
        cautions=("A dataset is data, not a finding: report results only from an analysis that "
                  "was run, with its design.",),
        ids=(r"\b(?:GSE|GSM|SRR|SRP|SRX|ERR|PRJNA|PRJEB|PXD|MTBLS)\d+", r"\bE-MTAB-\d+"),
    ),
    Route(
        "spectra_identification", ("谱图与成分鉴定", "Spectra and compound identification"),
        "Which compound a mass or NMR spectrum matches, which reference spectra exist for a "
        "compound, and which public datasets hold spectra of a herb.",
        ("质谱", "谱图", "鉴定", "核磁", "指纹图谱", "成分鉴定", "spectrum", "spectra",
         "mass spectrometry", "ms/ms", "identification", "nmr", "lc-ms", "fingerprint"),
        (Step("reference_spectra", "reference spectra of a compound", (
            _hub("compound_spectrum", subject="<compound or InChIKey>"),
            _c("massbank", "records_search", "measured", inchikey="<InChIKey>"),
            _c("massbank", "records", "measured", inchikey="<InChIKey>"),
            _c("massbank", "records_search_name", "measured", name="<compound>"),
            _c("massbank", "record", "measured", accession="<MSBNK-…>"),
            _c("massbank", "record_text", "measured", "with its LICENSE line",
               accession="<MSBNK-…>"),
            _c("massbank", "record_simple", "measured", "accession, title, peaks and SMILES only",
               accession="<MSBNK-…>"),
            _c("massbank", "record_jsonld", "reference", "the record's licence URL and citation",
               accession="<MSBNK-…>"),
            _c("massbank", "convert", "data", "records as MSP or JSON, for a local library search",
               accessions=["<MSBNK-…>"]),
            _c("gnps", "spectrum", "measured", spectrum_id="<CCMSLIB…>"),
            _c("gnps", "libraries", "reference"),
            _c("massbank", "browse_options", "reference", "contributors, instruments, MS types and "
               "ion modes with their record counts"),
            _c("massbank", "metadata", "reference", "the data version and counts, to cite"),
            _c("massbank", "count", "reference"),
            _c("massbank", "status", "reference"),
            _c("gnps_usi", "spectrum", "measured", usi="<mzspec:…>"))),
         Step("match", "library matches for a measured spectrum", (
             _c("massbank", "peak_search", "analysis", peak_list="<mz;intensity,mz;intensity>"),
             _e("native.peptide_mass", "analysis", "for a peptide", sequence="<peptide>"))),
         Step("datasets", "public raw spectra datasets", (
             _c("massive", "dataset_search", "data", text="<herb or compound>"),
             _c("massive", "dataset", "data", accession="<MSV…>"),
             _c("gnps_explorer", "dataset_files", "data", accession="<MSV…>"),
             _c("metabolights", "study", "data", study_id="<MTBLS…>"))),
         Step("names", "standard names and formulas for what was found", (
             _c("metabolomics_workbench", "refmet_match", "reference", name="<metabolite>"),
             _c("metabolomics_workbench", "refmet", "reference", refmet_name="<RefMet name>"),
             _c("metabolomics_workbench", "refmet_by_formula", "reference",
                formula="<molecular formula>"),
             _c("metabolomics_workbench", "compound_by_pubchem_cid", "reference",
                cid="<PubChem CID>")))),
        "Give each identification with its confidence level and the library entry it rests on.",
        then=("compound_identity",),
        cautions=("A library match is a putative identification until it is confirmed against "
                  "a reference standard.",),
    ),
    Route(
        "cell_models_cancer", ("细胞模型、药敏与肿瘤基因组",
                               "Cell models, drug sensitivity and cancer genomics"),
        "Which cell lines model a disease, how they respond to a compound across screens, which "
        "genes they depend on, and what tumour cohorts say about a gene.",
        ("细胞系", "细胞模型", "药敏", "肿瘤", "癌", "抗肿瘤", "抗癌", "cell line", "cancer",
         "tumour", "tumor", "drug sensitivity", "dose-response", "crispr", "dependency",
         "anticancer", "oncology"),
        (Step("cell_lines", "the cell line: identity, disease and known problems", (
            _c("cellosaurus", "search", "reference", q="<cell line>"),
            _c("cellosaurus", "cell_line", "curated", ac="<CVCL_…>"),
            _c("cellosaurus", "cell_line_by_accession", "reference", ac="<CVCL_…>"),
            _c("cellosaurus", "cell_line_by_xref", "reference", xref="<e.g. ACH-000739>"),
            _c("cellosaurus", "release_info", "reference"),
            _hub("cell_line_disease", subject="<cell line>"))),
         Step("sensitivity", "dose–response of a compound across screens", (
             _c("pharmacodb", "compound", "measured", compound="<compound>"),
             _c("pharmacodb", "experiments", "measured", compound="<compound>"),
             _c("pharmacodb", "experiments_in_cell_line", "measured", cell_line="<cell line>",
                compound="<compound>"),
             _c("pharmacodb", "biomarkers", "associated", compound="<compound>", gene="<gene>"),
             _c("pharmacodb", "search", "reference", query="<name>"),
             _c("pharmacodb", "cell_line", "reference", cell_line="<cell line>"),
             _c("pharmacodb", "dataset_stats", "reference"),
             _hub("compound_target", "GDSC's screened compounds and their targets",
                  subject="<compound>", sources=["gdsc"]),
             _c("proteomicsdb", "curves", "measured", "Kinobeads dose–response curves",
                protein_id="<ProteomicsDB protein id>"),
             _table("drugcomb", "measured", "DrugComb: combination screens with synergy scores"))),
         Step("dependencies", "the genes the cells depend on (CRISPR screens)", (
             _hub("screen_gene", object="<gene symbol>"),
             _hub("drug_target", "DepMap's PRISM drugs and their targets", subject="<drug>",
                  sources=["depmap"]))),
         Step("tumour_genomics", "a gene in tumour cohorts", (
             _c("cbioportal", "gene", "reference", gene="<gene symbol>"),
             _c("cbioportal", "studies", "data"),
             _c("cbioportal", "study", "data", study_id="<study id>"),
             _c("cbioportal", "molecular_profiles", "data", study_id="<study id>"),
             _c("gdc", "projects", "data"),
             _c("gdc", "project", "data", project_id="<e.g. TCGA-LUAD>"),
             _c("gdc", "cases", "data", filters="<GDC filter JSON>"),
             _c("gdc", "status", "reference"),
             _c("civic", "gene", "curated", gene="<gene symbol>")))),
        "Report each screen's own measure (IC50, AUC, AAC) with the screen; screens disagree on "
        "the same cell line and compound, so say which.",
        then=("combination_effects", "compound_targets", "exposure_metabolism"),
        cautions=("Sensitivity of cells at concentrations no tissue reaches is not an anticancer "
                  "effect.", "Check a cell line for misidentification (Cellosaurus) before "
                  "citing it."),
    ),
    Route(
        "immunology", ("免疫：表位、过敏原与免疫组库", "Immunology: epitopes, allergens and repertoires"),
        "Which epitopes of an antigen (plant allergens included) are known and how they were "
        "assayed, which receptors recognise them, and which immune repertoire studies bear on "
        "a condition.",
        ("免疫", "表位", "过敏", "过敏原", "抗原", "抗体", "免疫组库", "t细胞", "b细胞", "immune",
         "epitope", "allergen", "allergy", "antigen", "t cell", "b cell", "antibody",
         "repertoire", "tcr", "bcr"),
        (Step("epitopes", "epitopes and their assays", (
            _hub("epitope_antigen", object="<UniProt accession>"),
            _hub("epitope_assay", subject="<epitope>"),
            _hub("receptor_epitope", object="<epitope>"),
            _c("iedb", "antigens", "curated", name="<antigen name pattern>"),
            _c("iedb", "epitopes_by_organism", "curated", taxon_id="<NCBI taxon id>"),
            _c("iedb", "epitopes_by_sequence", "curated", sequence="<peptide>"),
            _c("iedb", "epitope", "curated", structure_id="<IEDB structure id>"),
            _c("iedb", "epitope_summary", "curated", structure_id="<IEDB structure id>"),
            _c("iedb", "tcell_assays", "measured", name="<antigen name pattern>"),
            _c("iedb", "tcell_by_antigen", "measured", accession="<UniProt accession>"),
            _c("iedb", "tcell_by_molecule", "measured", "non-peptidic epitopes: drugs, haptens, "
               "natural products", name="<compound>"),
            _c("iedb", "bcell_by_sequence", "measured", sequence="<peptide>"),
            _c("iedb", "mhc_by_sequence", "measured", sequence="<peptide>"),
            _c("iedb", "tcr_by_epitope", "curated", sequence="<peptide>"),
            _c("iedb", "bcr_by_cdr3", "curated", cdr3="<heavy-chain CDR3>"),
            _c("iedb", "reference_by_pmid", "curated", pmid="<PMID>"),
            _c("bv_brc", "epitopes_by_taxon", "curated", "redistributed from IEDB: count each "
               "epitope once", taxon_id="<NCBI taxon id>"))),
         Step("repertoires", "immune repertoire studies", (
             _c("ireceptor_adc", "studies_by_diagnosis", "data", diagnosis="<condition>"),
             _c("ireceptor_adc", "repertoires_by_study", "data", study_id="<BioProject accession>"),
             _c("ireceptor_adc", "repertoires", "data"),
             _c("ireceptor_adc", "rearrangements", "data", repertoire_id="<repertoire id>"),
             _c("ireceptor_adc", "rearrangements_by_junction", "data", junction_aa="<CDR3>",
                repertoire_id="<repertoire id>"),
             _c("ireceptor_adc", "rearrangement_count", "data", repertoire_id="<repertoire id>"),
             _c("ireceptor_adc", "study_counts", "data"),
             _c("ireceptor_adc", "info", "reference"),
             _c("vdjserver_adc", "studies_by_diagnosis", "data", diagnosis="<condition>"),
             _c("vdjserver_adc", "repertoires_by_study", "data", study_id="<BioProject accession>"),
             _c("vdjserver_adc", "repertoires", "data"),
             _c("vdjserver_adc", "rearrangements", "data", repertoire_id="<repertoire id>"),
             _c("vdjserver_adc", "rearrangement_count", "data", repertoire_id="<repertoire id>"),
             _c("vdjserver_adc", "rearrangements_by_junction", "data", junction_aa="<CDR3>",
                repertoire_id="<repertoire id>"),
             _c("vdjserver_adc", "study_counts", "data"),
             _c("vdjserver_adc", "info", "reference"))),
         Step("immune_genes", "immune gene lists", (
             _hub("gene_set_member", "ImmPort's immune gene lists", object="<gene symbol>",
                  sources=["immport"]),))),
        "Count an epitope once across IEDB and BV-BRC; keep positive and negative assays apart "
        "and give the host species.",
        then=("disease_links", "safety"),
        cautions=("An epitope assayed positive in one host and one assay is not an allergy in "
                  "people.",),
    ),
    Route(
        "microbiome", ("肠道菌群与微生物", "Gut microbes and other microorganisms"),
        "How gut microbes relate to a compound or herb (which microbes make or transform which "
        "metabolites, which host genes they affect), what metagenomics studies show, and what "
        "is known about a microbe's genomes, resistance and virulence.",
        ("菌群", "肠道菌", "微生物", "益生菌", "宏基因组", "耐药", "细菌", "肠道", "microbiome",
         "microbiota", "gut", "microbe", "bacteria", "metagenomics", "16s", "antimicrobial",
         "resistance"),
        (Step("microbe_metabolite", "microbes, metabolites and the host genes they affect", (
            _hub("organism_compound", "gutMGene's microbe–metabolite rows",
                 subject="<microbe>", sources=["gutmgene"]),
            _hub("regulation", "gutMGene's microbe and metabolite effects on host genes",
                 subject="<microbe or metabolite>", sources=["gutmgene"]),
            _c("gutmgene", "microbe_metabolite", "curated", "the evidence rows of one "
               "association the hub returned", index_id="<index id>", microbe="<microbe>",
               metabolite="<metabolite>", species="<host species>"),
            _c("gutmgene", "microbe_gene", "curated", index_id="<index id>", microbe="<microbe>",
               gene="<gene>", alteration="<alteration>", species="<host species>"),
            _c("gutmgene", "metabolite_gene", "curated", index_id="<index id>",
               metabolite="<metabolite>", gene="<gene>", alteration="<alteration>",
               species="<host species>"))),
         Step("metagenomics", "metagenomics studies and their analyses", (
             _c("mgnify", "studies", "data", search="<text>"),
             _c("mgnify", "studies_by_biome", "data",
                biome_lineage="root:Host-associated:Human:Digestive system"),
             _c("mgnify", "study", "data", accession="<MGYS…>"),
             _c("mgnify", "study_analyses", "data", accession="<MGYS…>"),
             _c("mgnify", "study_samples", "data", accession="<MGYS…>"),
             _c("mgnify", "analysis", "data", accession="<MGYA…>"),
             _c("mgnify", "analysis_annotations", "data", accession="<MGYA…>"),
             _c("mgnify", "biomes", "reference", biome_lineage="root:Host-associated:Human"),
             _c("mgnify", "genome_catalogues", "reference"),
             _c("mgnify", "genomes", "data", search="<g__Genus>"),
             _c("mgnify", "genome", "data", accession="<MGYG…>"),
             _e("native.diversity", "analysis", counts=["<count>"]))),
         Step("microbe", "a microbe's genomes, resistance, virulence and interactions", (
             _c("bv_brc", "taxonomy", "reference", taxon_id="<NCBI taxon id>"),
             _c("bv_brc", "genomes_by_taxon", "data", taxon_id="<NCBI taxon id>"),
             _c("bv_brc", "genome", "data", genome_id="<BV-BRC genome id>"),
             _c("bv_brc", "genome_amr", "measured", "laboratory-measured and predicted, said per "
                "row", genome_id="<BV-BRC genome id>"),
             _c("bv_brc", "amr_lab_by_antibiotic", "measured", antibiotic="<antibiotic>"),
             _c("bv_brc", "specialty_genes", "curated", genome_id="<BV-BRC genome id>"),
             _c("bv_brc", "ppi_by_genome", "curated", genome_id="<BV-BRC genome id>"),
             _c("bv_brc", "antibiotic", "reference", name="<antibiotic>"),
             _c("mibig", "stats", "reference")))),
        "Name the host species and the sample (faeces, mucosa) of every microbiome finding; a "
        "correlation between a microbe and a metabolite is not production.",
        then=("exposure_metabolism", "pathways_networks"),
        cautions=("Microbe–metabolite links are curated from studies of their own design: check "
                  "whether each was measured in people or in animals.",),
    ),
    Route(
        "imaging_data", ("影像数据", "Imaging data"),
        "Which public image collections bear on a disease, tissue, gene or compound "
        "(radiology, pathology, microscopy screens, neuroimaging), with their licences.",
        ("影像", "图像", "病理", "显微", "成像", "核磁共振", "imaging", "image", "radiology",
         "pathology", "microscopy", "mri", "neuroimaging", "high-content"),
        (Step("cancer_imaging", "cancer radiology and pathology collections", (
            _c("idc", "collections", "data"),
            _c("idc", "collection", "data", collection_id="<collection id>"),
            _c("idc", "cohort_counts", "data", collection_id="<collection id>",
               modality="<e.g. CT, MR, SM>"),
            _c("idc", "clinical_tables", "data", collection_id="<collection id>"),
            _c("idc", "licenses", "reference", collection_id="<collection id>"),
            _c("idc", "analysis_results", "data"),
            _c("idc", "attribute_values", "reference", attribute="Modality"),
            _c("idc", "tables", "reference"),
            _c("idc", "table", "reference", table="<index table>"),
            _c("idc", "sql", "data", sql="<one read-only SELECT>"),
            _c("idc", "stats", "reference"),
            _c("idc", "version", "reference"),
            _hub("dataset_disease", object="<disease>"),
            _c("tcia", "collections", "data"),
            _c("tcia", "patients", "data", collection="<collection>"),
            _c("tcia", "modalities", "data", collection="<collection>"),
            _c("tcia", "body_parts", "data", collection="<collection>"),
            _c("tcia", "patient_studies", "data", collection="<collection>",
               patient_id="<patient id>"),
            _c("tcia", "series", "data", collection="<collection>", patient_id="<patient id>"),
            _c("tcia", "series_size", "data", series_uid="<series UID>"),
            _c("tcia_cm", "collections_search", "data", text="<text>"),
            _c("tcia_cm", "collection", "data", slug="<collection slug>"),
            _c("tcia_cm", "download", "reference", download_id="<download id>"),
            _c("tcia_cm", "licenses", "reference"))),
         Step("cell_imaging", "microscopy screens by gene, compound or phenotype", (
             _c("idr", "gene_studies", "data", symbol="<gene symbol>"),
             _c("idr", "compound_studies", "data", compound="<compound>"),
             _c("idr", "search_studies", "data", key="<key>", value="<value>"),
             _c("idr", "screen_study", "data", screen_id="<screen id>"),
             _c("idr", "project_study", "data", project_id="<project id>"),
             _c("idr", "container_values", "data", container="<screen or project>", key="<key>"),
             _c("idr", "containers", "data"),
             _c("idr", "screens", "data"),
             _c("idr", "projects", "data"),
             _hub("gene_phenotype", "IDR's image-derived phenotypes", subject="<gene>",
                  sources=["idr"]),
             _c("biostudies", "bioimages_search", "data", query="<text>"),
             _c("biostudies", "study_info", "data", accession="<S-BIAD…>"))),
         Step("neuroimaging", "neuroimaging datasets", (
             _c("openneuro", "datasets", "data"),
             _c("openneuro", "dataset", "data", dataset_id="<ds…>"),
             _c("openneuro", "snapshot_files", "data", dataset_id="<ds…>",
                tag="<snapshot tag>")))),
        "Check each collection's licence before reuse (IDC and TCIA give it per series or per "
        "download).",
        then=("omics_datasets",),
        cautions=("A collection is data: findings come only from an analysis of it.",),
    ),
    Route(
        "literature", ("文献检索与证据线索", "Literature search and text mining"),
        "What has been published on a question: articles, preprints, citations, the entities "
        "they mention, and how to read them as evidence.",
        ("文献", "论文", "综述", "发表", "检索", "引用", "预印本", "literature", "paper",
         "article", "publication", "pubmed", "review", "preprint", "citation", "text mining",
         "papers on", "find papers", "published"),
        (Step("search", "articles and preprints", (
            _c("europepmc", "search", "literature", query="<query>"),
            _c("ncbi_eutils", "esearch", "literature", db="pubmed", term="<query>"),
            _c("ncbi_eutils", "esummary", "literature", db="pubmed", id="<PMIDs>"),
            _c("crossref", "search", "literature", query="<query>"),
            _c("crossref", "work", "literature", doi="<DOI>"),
            _c("openalex", "work", "literature", work_id="<OpenAlex id or DOI>"),
            _c("biorxiv", "details", "literature", "a preprint: not peer reviewed", doi="<DOI>"),
            _c("europepmc", "citations", "literature", id="<PMID>", source="MED"))),
         Step("text_mining", "the entities articles mention", (
             _c("pubtator", "search", "mentioned", text="<entities or query>"),
             _c("pubtator", "export", "mentioned", pmids="<PMIDs>"),
             _c("pubtator", "autocomplete", "reference", query="<entity>"),
             _c("europepmc_annotations", "by_entity", "mentioned", entity="<entity>"),
             _c("europepmc_annotations", "by_article", "mentioned", article_ids="<MED:PMID>"))),
         Step("governed_reading", "passages to cite from an indexed corpus, and reviewed "
                                  "third-party tools (on the runner)", (
             _e("skill.retrieve-literature-evidence", "literature", question="<question>",
                corpus="<indexed corpus>"),
             _e("system.provider_catalog", "reference", "the reviewed ToolUniverse and BioMCP "
                "wrappers", query="<literature, trials, variants …>"),
             _e("system.provider_call", "literature", "one reviewed wrapper",
                provider="<biomcp or tooluniverse>", tool="<tool from system.provider_catalog>")))),
        "Cite by PMID or DOI, and group hits by study design before drawing anything from them.",
        then=("efficacy_evidence",),
        cautions=("A hit is a pointer to read, not evidence: nothing is licensed until the study "
                  "itself has been assessed.", "A co-mention in text mining is not a finding."),
        ids=(r"\bPMID:?\s*\d+", r"\b10\.\d{4,9}/\S+", r"\bPMC\d+"),
    ),
    Route(
        "ontology_mapping", ("术语与标识符映射", "Terms and identifiers across vocabularies"),
        "How a term or an identifier maps across vocabularies and databases (MeSH, HPO, Disease "
        "Ontology, GO, ICD-10, LOINC), and what a prefix or a CURIE resolves to.",
        ("术语", "本体", "标识符", "映射", "编码", "icd", "mesh", "ontology", "identifier",
         "mapping", "curie", "vocabulary", "loinc", "id conversion"),
        (Step("terms", "the term in each vocabulary", (
            _c("ols", "search", "reference", q="<term>"),
            _c("ols", "term", "reference", ontology="<ontology>", obo_id="<ID>"),
            _c("ols", "ontologies", "reference"),
            _c("mesh", "descriptor", "reference", label="<term>"),
            _c("mesh", "term", "reference", label="<term>"),
            _c("mesh", "details", "reference", descriptor="<D…>"),
            _table("mesh", "reference", "MeSH descriptors, for offline use"),
            _c("hpo", "search", "reference", q="<term>"),
            _c("disease_ontology", "metadata", "reference", doid="<DOID:…>"),
            _c("quickgo", "go_search", "reference", query="<term>"),
            _c("quickgo", "go_term", "reference", go_id="<GO:…>"),
            _c("nlm_clinical_tables", "icd10cm", "reference", terms="<term>"),
            _c("nlm_clinical_tables", "loinc", "reference", terms="<test>"),
            _c("nlm_clinical_tables", "hcpcs", "reference", terms="<term>"),
            _c("monarch", "entity", "reference", id="<CURIE>"),
            _e("native.parse_obo", "analysis", text="<OBO text>"))),
         Step("identifiers", "what a prefix or a CURIE resolves to, and identifier conversion", (
             _c("bioregistry", "reference", "reference", prefix="<prefix>", identifier="<id>"),
             _c("bioregistry", "registry", "reference", prefix="<prefix>"),
             _c("identifiers_org", "resolve", "reference", curie="<prefix:id>"),
             _c("wikidata_sparql", "sparql", "reference", "one read-only SPARQL query: the "
                "identifiers Wikidata links to an entity, or LOTUS by compound class",
                query="<SPARQL SELECT>"),
             _c("gprofiler", "convert", "reference", query="<ids, space-separated>",
                target="ENSG"),
             _c("string", "map_ids", "reference", identifiers="<ids>"),
             _c("hgnc", "search", "reference", query="<alias>"),
             _c("hgnc", "hgnc_id", "reference", hgnc_id="<HGNC:…>"),
             _c("ensembl", "id_lookup", "reference", id="<Ensembl id>"),
             _c("ensembl", "ping", "reference"),
             _c("mygene", "gene", "reference", id="<Entrez or Ensembl id>"),
             _c("ncbi_datasets", "gene_by_id", "reference", gene_id="<NCBI Gene id>"),
             _c("ucsc", "genomes", "reference"),
             _c("ucsc", "chromosomes", "reference", genome="hg38"))),
         Step("tcm_terms", "TCM names to standard names", (
             _e("skill.normalize-tcm-entities", "reference", names=["<name>"]),
             _e("native.tcm_lookup", "recorded", name="<name>")))),
        "Give a mapping with its vocabulary and version; a cross-reference can be broader or "
        "narrower than the term.",
        cautions=("A mapping is a correspondence between vocabularies, not a finding.",),
        ids=(r"\b(?:HP|GO|DOID|MONDO|UBERON|EFO|MP|CHEBI|NCIT|ORPHA):\s?\d+",),
    ),
    Route(
        "combination_effects", ("配伍与联合作用", "Combinations and synergy"),
        "Whether two herbs, two compounds, or a herb and a drug act together beyond what each "
        "does alone, judged against a stated reference model.",
        ("配伍", "协同", "联合", "拮抗", "增效", "减毒", "药对", "synergy", "combination",
         "antagonism", "additive", "herb pair", "drug combination"),
        (Step("screens", "measured combination screens", (
            _table("drugcomb", "measured", "DrugComb: synergy scores from measured dose–response "
                   "matrices of drug pairs"),
            _c("pharmacodb", "experiments_in_cell_line", "measured", "each drug alone, the "
               "baseline a combination is judged against", cell_line="<cell line>",
               compound="<compound>"))),
         Step("analysis", "a combination analysed against a stated reference model", (
             _e("study.combination", "analysis",
                cells=[{"dose_a": "<dose>", "dose_b": "<dose>", "effect": ["<fraction>"]}],
                primary="<bliss, hsa or loewe>"),)),
         Step("recorded_pairs", "recorded herb pairs and incompatibilities", (
             _e("native.tcm_compatibility", "recorded", herbs=["<herb>", "<herb>"]),
             _e("tcmdb.formulas", "recorded", "the formulas that contain both herbs",
                query="<herb> <herb>", rank=True)))),
        "Name the reference model (Bliss, Loewe, HSA) and the dose range: synergy at one dose "
        "ratio does not generalise.",
        scaffold="combination",
        then=("formula_mechanism", "safety"),
        cautions=("Synergy in cells is not a benefit in patients, and a recorded herb pair is "
                  "tradition.",),
    ),
    Route(
        "formula_mechanism", ("方剂作用机制（网络药理）", "How a formula might work (network pharmacology)"),
        "A mechanism hypothesis for a formula or a herb in a disease: its herbs, their "
        "constituents (those that reach the blood first), their measured and predicted targets, "
        "the pathways those fall in against a stated background, and the overlap with the "
        "disease's genes.",
        ("机制", "作用机制", "网络药理", "多靶点", "治疗机制", "怎么起效", "mechanism",
         "network pharmacology", "multi-target", "systems pharmacology", "how does"),
        (Step("composition", "the formula's herbs, in its stated version", (
            _e("native.tcm_formula", "recorded", name="<formula>"),
            _e("tcmdb.consensus", "per_row", kind="formula_herb", subject="<formula>"))),
         Step("constituents", "each herb's constituents, those detected in blood first", (
             _c("dcabm_tcm", "prescription_blood", "measured", names=[_PINYIN]),
             _e("tcmdb.consensus", "per_row", kind="herb_ingredient", subject="<herb>"))),
         Step("targets", "the constituents' targets, measured apart from predicted", (
             _e("tcmdb.consensus", "per_row", kind="compound_target", subject="<compound>"),
             _e("tcmdb.consensus", "per_row", kind="ingredient_target", subject="<compound>"),
             _hub("herb_target", subject="<herb>"))),
         Step("pathways", "pathway enrichment against a stated background", (
             _c("gprofiler", "gost", "analysis", query=_GENES),
             _e("native.enrichment_analysis", "analysis", genes=["<gene>"],
                gene_sets={"<set>": ["<gene>"]}, background="<background size>"))),
         Step("disease", "the disease's genes, and the overlap", (
             _hub("target_disease", object="<disease>"),
             _c("opentargets", "associated_diseases", "aggregated",
                ensembl_id="<Ensembl gene id>"))),
         Step("recorded_run", "the whole loop as one recorded run (a job), or the governed "
                              "seed-corpus analysis", (
             _e("job.research.run", "analysis", question="<question>", formula="<formula>",
                disease="<disease>"),
             _e("skill.analyze-tcm-network-pharmacology", "predicted", formula_name="<formula>")))),
        "Carry each target's evidence kind into the pathway step: an enrichment of predicted "
        "targets is a hypothesis about the predictions.",
        scaffold="pathway_signal",
        then=("compound_targets", "exposure_metabolism", "efficacy_evidence"),
        cautions=("A network generates hypotheses: it predicts, it does not show a mechanism "
                  "(predicted ≠ measured).",
                  "A large target set overlaps a disease's genes by chance: give the background "
                  "and the test."),
    ),
)

ROUTE_BY_ID: dict[str, Route] = {r.id: r for r in ROUTES}

#: What an entry lacks here for a call with these fixed arguments: capability requirements
#: ({need, detail, remedy}), empty when it can run.
Judge = Callable[[str, Mapping[str, Any]], list[dict[str, Any]]]


def _placeholder(value: Any) -> bool:
    if isinstance(value, str):
        return "<" in value
    if isinstance(value, Mapping):
        return any(_placeholder(v) for v in value.values()) or any(_placeholder(k) for k in value)
    if isinstance(value, (list, tuple)):
        return any(_placeholder(v) for v in value)
    return False


def fixed_args(use: Use) -> dict[str, Any]:
    """The arguments a route gives outright (no '<…>' to fill in)."""
    return {k: v for k, v in use.args.items() if not _placeholder(v)}


def call_for(entry: str, args: Mapping[str, Any]) -> dict[str, Any]:
    """The call that runs ``entry`` with ``args``: its core tool where one takes the entry's
    own arguments, connector_call for an API operation, run_pipeline for a pipeline job, and
    call_tool for any other entry."""
    from .core_tools import CORE_BY_NAME, core_for
    args = dict(args)
    if entry.startswith("connector."):
        _, source, op = entry.split(".", 2)
        return {"tool": "connector_call",
                "arguments": {"connector": source, "operation": op, "arguments": args}}
    if entry.startswith("job.pipeline."):
        return {"tool": "run_pipeline",
                "arguments": {"pipeline": entry.rsplit(".", 1)[1], "arguments": args}}
    core = core_for(entry)
    if core and set(args) <= set(CORE_BY_NAME[core]["parameters"].get("properties") or {}):
        return {"tool": core, "arguments": args}
    return {"tool": "call_tool", "arguments": {"tool": entry, "arguments": args}}


def label(use: Use) -> str:
    """A use as the agent and the docs name it: the entry, with the relation kind or dataset
    it is given."""
    for key in ("kind", "dataset"):
        if key in use.args and not _placeholder(use.args[key]):
            return f"{use.entry} {use.args[key]}"
    return use.entry


_DATASETS: list[Any] | None = None


def _datasets() -> list[Any]:
    global _DATASETS
    if _DATASETS is None:
        from bioagent.tcmdb import DATASETS
        _DATASETS = list(DATASETS)
    return _DATASETS


def hub_datasets(kind: str) -> list[str]:
    """The datasets whose stores hold rows of a relation kind (downloadable ones first)."""
    order = {"download": 0, "live": 1, "manual": 2}
    specs = [d for d in _datasets() if kind in (d.relations or ())]
    return [d.key for d in sorted(specs, key=lambda d: order.get(d.access, 3))]


def _kind_requirement(kind: str, built: Sequence[str], where: str) -> dict[str, Any] | None:
    """What a relation kind needs here: one of its datasets built (None when one is)."""
    from .capability import requirement
    keys = hub_datasets(kind)
    if not keys or set(keys) & set(built):
        return None
    shown = ", ".join(keys[:6]) + (f" and {len(keys) - 6} more" if len(keys) > 6 else "")
    if where == "runner":
        remedy = (f"Fetch and build one of them on this runner (job.tcmdb.fetch {{dataset: "
                  f"{keys[0]}}}, then job.tcmdb.build; tcmdb.datasets says how each is "
                  "obtained), or import a built store with tcmdb.import_store.")
    else:
        remedy = (f"Import a built store of one of them here with tcmdb.import_store, or build "
                  f"one on the local runner (job.tcmdb.build {{dataset: {keys[0]}}}).")
    return requirement("dataset", f"one of the datasets with {kind} rows ({shown}) built or "
                                  "imported in this runtime", remedy, keys)


def _changes(was: str, now: str) -> str:
    """What answering from a source of kind ``now`` instead of ``was`` changes in the answer."""
    if was == now:
        return (f"the same kind of evidence ({EVIDENCE[now][0]}) from another source: name the "
                "source that answered")
    how = "report each row with its own label" if now == "per_row" else f"report it as {now}"
    return f"{EVIDENCE[now][0]} instead of {EVIDENCE[was][0]}: {how}"


def _unique(items: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(items))


def view(route: Route, judge: Judge | None = None, *, built: Sequence[str] | None = None,
         where: str = "runner") -> dict[str, Any]:
    """One route for the agent: every source of every step with its call and evidence kind.
    With ``judge`` (this runtime's readiness), each source says whether it can run here and,
    when it cannot, what it needs and which source in the same step covers it with what that
    changes; a step none of whose sources can run says what would answer it. ``built``: the
    datasets built in this runtime's data hub (None: not judged)."""
    steps = []
    total = runnable_total = 0
    for step in route.steps:
        rows: list[tuple[Use, dict[str, Any], list[dict[str, Any]]]] = []
        for use in step.uses:
            item: dict[str, Any] = {"source": label(use), "call": call_for(use.entry, use.args),
                                    "evidence": use.evidence}
            if use.note:
                item["note"] = use.note
            kind = use.args.get("kind") if use.entry == "tcmdb.relations" else None
            if kind:
                item["datasets"] = hub_datasets(kind)
            needs: list[dict[str, Any]] = []
            if judge is not None:
                needs = list(judge(use.entry, fixed_args(use)))
                if kind and built is not None:
                    extra = _kind_requirement(kind, built, where)
                    if extra is not None:
                        # the kind's own datasets name what is missing better than "a store"
                        needs = [r for r in needs if r.get("need") != "dataset"] + [extra]
                item["runnable_now"] = not needs
                if needs:
                    item["needs"] = [r["detail"] for r in needs]
            rows.append((use, item, needs))
        out: dict[str, Any] = {"step": step.id, "answers": step.answers,
                               "sources": [item for _, item, _ in rows]}
        if judge is not None:
            ready = [(u, it) for u, it, n in rows if not n]
            total += len(rows)
            runnable_total += len(ready)
            for use, item, needs in rows:
                if not needs or not ready:
                    continue
                cover = next(((u, it) for u, it in ready if u.evidence == use.evidence), ready[0])
                item["covered_by"] = cover[1]["source"]
                item["changes"] = _changes(use.evidence, cover[0].evidence)
            if not ready:
                out["unanswered_here"] = {
                    "needs": _unique(r["detail"] for _, _, n in rows for r in n),
                    "to_answer_it": _unique(r["remedy"] for _, _, n in rows for r in n)}
        steps.append(out)
    used = _unique(u.evidence for s in route.steps for u in s.uses)
    doc: dict[str, Any] = {
        "route": route.id, "title": {"zh": route.title[0], "en": route.title[1]},
        "asks": route.asks, "steps": steps,
        "evidence": {k: EVIDENCE[k][1] for k in EVIDENCE if k in used},
        "reconcile": route.reconcile, "cautions": list(route.cautions), "rules": list(RULES)}
    if route.scaffold:
        doc["scaffold"] = {
            "id": route.scaffold,
            "how": f"research_inquiry {{action: 'scaffolds', scaffold: '{route.scaffold}'}} gives "
                   f"its parameters; then {{action: 'open', scaffold: '{route.scaffold}', "
                   "values: {…}}} seals its predictions before any result is recorded"}
    if route.then:
        doc["then"] = [{"route": r, "title": ROUTE_BY_ID[r].title[1]} for r in route.then]
    if judge is not None:
        doc["counts"] = {"sources": total, "runnable_now": runnable_total}
    return doc


def listing() -> dict[str, Any]:
    """Every route in a line: its id, title, what it asks and its inquiry scaffold."""
    return {"routes": [{"route": r.id, "title": {"zh": r.title[0], "en": r.title[1]},
                        "asks": r.asks, **({"scaffold": r.scaffold} if r.scaffold else {})}
                       for r in ROUTES],
            "how": "research_routes {route: '<id>'} gives one route with every source, whether "
                   "each can run here and what covers one that cannot; {question: '<text>'} "
                   "finds the routes that answer a question."}


_ASCII = re.compile(r"^[\x00-\x7f]+$")


def match(question: str, limit: int = 3) -> list[tuple[Route, float]]:
    """The routes whose keywords a question uses, best first (a Chinese keyword counts as a
    substring, twice its length; an English one as a word, its plural included; an
    identifier a route's ``ids`` recognise, an rsID or an NCT number, as a keyword of eight)."""
    raw = str(question or "")
    text = raw.lower()
    scored = []
    for order, route in enumerate(ROUTES):
        score = 8.0 * sum(1 for p in route.ids if re.search(p, raw, re.IGNORECASE))
        for kw in route.keywords:
            k = kw.lower()
            if _ASCII.match(k):
                if re.search(rf"(?<![a-z0-9]){re.escape(k)}s?(?![a-z0-9])", text):
                    score += len(k)
            elif k in text:
                score += 2 * len(k)
        if score:
            scored.append((score, order, route))
    scored.sort(key=lambda t: (-t[0], t[1]))
    return [(route, score) for score, _, route in scored[:max(1, limit)]]


def _cell(text: str) -> str:
    """Prose for the document: a '<' shown as itself, never read as HTML."""
    return str(text).replace("<", "&lt;").replace("|", "\\|").replace("\n", " ")


def _args_text(use: Use) -> str:
    return "; ".join(f"{k}: {v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)}"
                     for k, v in use.args.items())


def markdown() -> str:
    """studio/docs/ROUTES.md: every route with its steps and sources (the coverage test checks
    the committed file against it)."""
    out = ["# Research routes", "",
           "<!-- Generated from studio/runner/src/tcmstudio/routes.py by "
           "`python studio/scripts/routes_doc.py`; do not edit by hand. -->", "",
           "For each kind of question TCM research asks, every source in the runtime that answers "
           "a part of it, step by step, with the kind of evidence each gives. The agent reads a "
           "route with the core tool `research_routes`, which also says, in the runtime it is "
           "asked in, which sources can run, what each one that cannot run needs, and which "
           "source in the same step covers it with what that changes. A route runs nothing: "
           "every call keeps its own approvals, network permission and governance.", "",
           "## Evidence kinds", "", "| Kind | Meaning |", "| --- | --- |"]
    out += [f"| `{k}` | {_cell(v[1])} |" for k, v in EVIDENCE.items()]
    out += ["", "## How the agent uses a route", ""]
    out += [f"- {_cell(rule)}" for rule in RULES]
    out += ["", "## Routes", ""]
    out += [f"- [{r.title[1]}](#{r.id}) (`{r.id}`, {r.title[0]})" for r in ROUTES]
    for r in ROUTES:
        out += ["", f'<a id="{r.id}"></a>', "", f"### {r.title[1]} · {r.title[0]}", "",
                f"`{r.id}`: {_cell(r.asks)}", ""]
        for step in r.steps:
            out += [f"**{step.answers[0].upper()}{step.answers[1:]}** (`{step.id}`)", "",
                    "| Source | Evidence | Arguments | Note |", "| --- | --- | --- | --- |"]
            out += [f"| `{label(u)}` | {u.evidence} | "
                    + (f"`{_args_text(u).replace('|', chr(92) + '|')}`" if u.args else "")
                    + f" | {_cell(u.note)} |" for u in step.uses]
            out.append("")
        out += [f"Reconcile: {_cell(r.reconcile)}", ""]
        if r.scaffold:
            out += [f"Deciding between explanations: research_inquiry scaffold `{r.scaffold}`.", ""]
        if r.cautions:
            out += [f"Cautions: {_cell(' '.join(r.cautions))}", ""]
        if r.then:
            out += ["Next: " + ", ".join(f"[{ROUTE_BY_ID[t].title[1]}](#{t})" for t in r.then), ""]
    out += ["## Datasets no route uses", "", "| Dataset | Why |", "| --- | --- |"]
    out += [f"| `{k}` | {_cell(v)} |" for k, v in sorted(NOT_ROUTED.items())]
    return "\n".join(out).rstrip() + "\n"
