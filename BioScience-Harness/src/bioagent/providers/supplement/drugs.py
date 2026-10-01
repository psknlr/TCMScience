"""Live connectors for drug, drug-target and drug-response databases (supplementary
sources, review of 2026-09-30; each operation run against the live service on 2026-10-01).

* **DrugCentral DRS API**: the REST service the drugcentral.org navigation links ("API").
  It is documented by its OpenAPI page, needs no key, and answers per drug, per target and
  per indication. It runs on an auto-generated AWS App Runner host (version 0.1.0) and
  answered an occasional HTTP 500 during the survey, so the backend's retries matter; if
  the host changes, take the new one from the "API" link on drugcentral.org. Indications,
  contraindications, ATC codes and FAERS signals are here and in the database dump only.
* **PharmacoDB**: the GraphQL endpoint the PharmacoDB site itself calls. It has no
  published API documentation; the schema is self-describing (introspection and GraphiQL
  at the same URL) and may change without notice. It serves dose-response profiles of
  ten cancer screens re-fitted by PharmacoGx (CCLE, CTRPv2, FIMM, GDSC1, GDSC2, GRAY,
  NCI60, PRISM, UHNBreast, gCSI) and gene-drug biomarker statistics. Its GDSC rows are the
  same experiments as the ``gdsc`` dataset, with different curve fits.
* **Pharos**: the IDG Knowledge Management Center's GraphQL API (documented with worked
  examples at pharos.nih.gov/api): target development level, ligands and drug
  activities, disease associations and interactions per target. It serves pharos319;
  the ``pharos`` dataset holds the newer Pharos400 levels.

DrugComb's own portal and API (api.drugcomb.org) reset the TLS handshake from this
environment on 2026-10-01; its data is the ``drugcomb`` dataset (Zenodo). GDSC is a
dataset only: the Cell Model Passports API allows non-commercial use and forbids use in
third-party websites without permission.
"""

from __future__ import annotations

from ..public_apis import Operation, PublicSource

__all__ = ["SOURCES", "PENDING"]

_DRUGCENTRAL_API = "https://uxn2ycvimg.us-east-2.awsapprunner.com"

_PDB_EXPERIMENT_FIELDS = ("id cell_line{id uid name} tissue{name} compound{id uid name} "
                          "dataset{name} profile{IC50 AAC EC50 Einf HS DSS1} "
                          "dose_response{dose response}")

SOURCES: tuple[PublicSource, ...] = (
    PublicSource(
        "drugcentral", "DrugCentral DRS API", _DRUGCENTRAL_API,
        "uxn2ycvimg.us-east-2.awsapprunner.com", "CC BY-SA 4.0",
        "DrugCentral per-entity lookups: a drug's structure, synonyms, identifiers, ATC "
        "codes, activities and mechanisms of action on its targets, indications and "
        "contraindications (SNOMED / UMLS / OMOP concepts) and FAERS disproportionality "
        "(log-likelihood ratio); a target's drugs by UniProt accession or gene symbol. "
        "Drug ids are DrugCentral struct ids ('drug_by_name' finds them).",
        "pharmacology", (
            Operation("drug_by_name", "Synonym records matching a drug name, each with the "
                      "DrugCentral struct id ('id') it belongs to", "synonyms/name/{name}",
                      args=("name",), example={"name": "imatinib"}),
            Operation("structure", "Structure record of a drug: name, SMILES, InChI, "
                      "InChIKey, CAS, formula, status, definition", "structures/id/{struct_id}",
                      args=("struct_id",), example={"struct_id": 1423}),
            Operation("structure_by_inchikey", "Structure record by InChIKey",
                      "structures/inchikey/{inchikey}", args=("inchikey",),
                      example={"inchikey": "KTUFNOKKBVMGRW-UHFFFAOYSA-N"}),
            Operation("identifiers", "Cross-references of a drug (ChEMBL, PubChem, DrugBank, "
                      "KEGG, UNII, RxNorm, MeSH, ...)", "identifier/struct_id/{struct_id}",
                      args=("struct_id",), example={"struct_id": 1423}),
            Operation("activities", "Target activities and mechanisms of action of a drug "
                      "(act_value in -log10 M, action_type, moa flag, sources)",
                      "act_table_full/struct_id/{struct_id}", args=("struct_id",),
                      example={"struct_id": 1423}),
            Operation("target_activities", "Drug activities on a target by UniProt "
                      "accession", "act_table_full/accession/{accession}",
                      args=("accession",), example={"accession": "P00519"}),
            Operation("gene_activities", "Drug activities on a target by gene symbol",
                      "act_table_full/gene/{gene}", args=("gene",), example={"gene": "EGFR"}),
            Operation("indications", "Indications and contraindications of a drug "
                      "(relationship_name, concept_name, SNOMED, UMLS CUI)",
                      "omop_relationship/struct_id/{struct_id}", args=("struct_id",),
                      example={"struct_id": 1423}),
            Operation("atc", "ATC codes of a drug", "struct2atc/struct_id/{struct_id}",
                      args=("struct_id",), example={"struct_id": 1423}),
            Operation("faers", "FAERS adverse-event signals of a drug: MedDRA term, "
                      "log-likelihood ratio and its threshold, report counts (a "
                      "disproportionality signal, not causation; about 0.2 MB for a "
                      "common drug)", "faers/struct_id/{struct_id}", args=("struct_id",),
                      example={"struct_id": 1423}),
        ), smoke="structure", docs=_DRUGCENTRAL_API + "/docs",
        rate_note="no stated limit; auto-generated App Runner host that answered an "
                  "occasional 500; kept at 1 req/s"),

    PublicSource(
        "pharmacodb", "PharmacoDB (site GraphQL endpoint)", "https://pharmacodb.ca",
        "pharmacodb.ca",
        "Not stated (no licence on the site or in its code; the screens it re-processes "
        "keep their own terms, e.g. GDSC is non-commercial)",
        "Cancer cell-line drug sensitivity across ten screens (CCLE, CTRPv2, FIMM, GDSC1, "
        "GDSC2, GRAY, NCI60, PRISM, UHNBreast, gCSI), re-fitted uniformly by PharmacoGx: "
        "compounds with PubChem/ChEMBL/InChIKey, per-experiment IC50, AAC, EC50 and dose "
        "response, and gene-compound biomarker statistics. Undocumented: the GraphQL "
        "endpoint the PharmacoDB site calls, described only by its own introspection. "
        "Compound targets carry no source and include metabolising enzymes.",
        "pharmacogenomics", (
            Operation("dataset_stats", "Screens with their cell-line, compound, tissue and "
                      "experiment counts", "graphql", method="POST",
                      graphql="query{dataset_stats{dataset{id name} cell_line_count "
                              "experiment_count compound_count tissue_count}}"),
            Operation("search", "Compounds, cell lines, tissues, genes and datasets whose "
                      "name matches", "graphql", method="POST",
                      graphql="query($q:String){search(input:$q){id value type}}",
                      variables={"q": "{query}"}, args=("query",),
                      example={"query": "lapatinib"}),
            Operation("compound", "A compound by name: PharmacoDB id, annotation (SMILES, "
                      "InChIKey, PubChem, ChEMBL, FDA status), screens and targets (Ensembl "
                      "genes, no provenance)", "graphql", method="POST",
                      graphql="query($name:String){compound(compoundName:$name){compound{"
                              "id name uid annotation{smiles inchikey pubchem chembl "
                              "fda_status} datasets{id name}} targets{target_id target_name "
                              "genes{id name}}}}",
                      variables={"name": "{compound}"}, args=("compound",),
                      example={"compound": "Paclitaxel"}),
            Operation("cell_line", "A cell line by its PharmacoDB name (MCF-7, not MCF7; "
                      "'search' finds it): Cellosaurus accession, tissue, diseases and the "
                      "name each screen uses", "graphql", method="POST",
                      graphql="query($name:String){cell_line(cellName:$name){id uid name "
                              "accession_id diseases tissue{id name} synonyms{name "
                              "dataset{name}}}}",
                      variables={"name": "{cell_line}"}, args=("cell_line",),
                      example={"cell_line": "MCF-7"}),
            Operation("experiments", "Dose-response experiments of a compound, one page "
                      "(per_page <= 100): cell line, screen, IC50/AAC/EC50 profile and the "
                      "dose-response points", "graphql", method="POST",
                      graphql="query($name:String,$page:Int,$per_page:Int){experiments("
                              "compoundName:$name,page:$page,per_page:$per_page){"
                              + _PDB_EXPERIMENT_FIELDS + "}}",
                      variables={"name": "{compound}", "page": "{page}",
                                 "per_page": "{per_page}"},
                      args=("compound",),
                      example={"compound": "Paclitaxel", "page": 1, "per_page": 5}),
            Operation("experiments_in_cell_line", "Dose-response experiments of a compound "
                      "in one cell line (PharmacoDB names), every screen that tested the pair; "
                      "an unknown name gives an empty list",
                      "graphql", method="POST",
                      graphql="query($name:String,$cell:String){experiments(compoundName:"
                              "$name,cellLineName:$cell){" + _PDB_EXPERIMENT_FIELDS + "}}",
                      variables={"name": "{compound}", "cell": "{cell_line}"},
                      args=("compound", "cell_line"),
                      example={"compound": "Paclitaxel", "cell_line": "MCF-7"}),
            Operation("biomarkers", "Gene-compound associations per screen: estimate, "
                      "p-value, FDR, n, sensitivity statistic and molecular data type (rna, "
                      "cnv, mutation)", "graphql", method="POST",
                      graphql="query($gene:String,$compound:String,$per_page:Int){"
                              "gene_compound_dataset(geneName:$gene,compoundName:$compound,"
                              "per_page:$per_page){id gene{id name annotation{symbol}} "
                              "compound{id name} dataset{name} estimate lower_analytic "
                              "upper_analytic pvalue_analytic fdr_analytic n sens_stat "
                              "mDataType}}",
                      variables={"gene": "{gene}", "compound": "{compound}",
                                 "per_page": "{per_page}"},
                      args=("gene", "compound"),
                      example={"gene": "ERBB2", "compound": "Lapatinib", "per_page": 10}),
        ), smoke="dataset_stats", docs="https://pharmacodb.ca/graphql",
        rate_note="undocumented; no stated limit; kept at 1 req/s"),

    PublicSource(
        "pharos", "Pharos GraphQL API (IDG / TCRD)", "https://pharos-api.ncats.io",
        "pharos-api.ncats.io",
        "Not stated (Pharos defers to the licences of its primary sources)",
        "Target knowledge from the NIH IDG programme: target development level (Tclin, "
        "Tchem, Tbio, Tdark) and family, ligands and approved drugs with their activities "
        "(from ChEMBL, DrugCentral, Guide to Pharmacology), disease associations with their "
        "upstream source, and protein interactions (STRING, BioPlex, Reactome). Serves "
        "pharos319 (TCRD 6.13.5).",
        "pharmacology", (
            Operation("target", "A target by gene symbol: UniProt, TDL, family, novelty, "
                      "description", "graphql", method="POST",
                      graphql="query($q:ITarget){target(q:$q){name sym uniprot tdl fam "
                              "novelty description}}",
                      variables={"q": {"sym": "{symbol}"}}, args=("symbol",),
                      example={"symbol": "EGFR"}),
            Operation("target_ligands", "A target's ligand and drug counts and its top "
                      "ligands (isdrug=true for approved drugs) with cross-references and "
                      "activities (pActivity, mechanism, reference)", "graphql", method="POST",
                      graphql="query($q:ITarget,$top:Int,$isdrug:Boolean){target(q:$q){sym "
                              "uniprot tdl ligandCounts{name value} ligands(top:$top,"
                              "isdrug:$isdrug){ligid name smiles isdrug synonyms{name value} "
                              "activities{type value moa reference}}}}",
                      variables={"q": {"sym": "{symbol}"}, "top": "{top}",
                                 "isdrug": "{isdrug}"},
                      args=("symbol",), example={"symbol": "EGFR", "top": 3, "isdrug": True}),
            Operation("target_diseases", "A target's associated diseases (MONDO) with each "
                      "association's upstream source, evidence and scores", "graphql",
                      method="POST",
                      graphql="query($q:ITarget,$top:Int){target(q:$q){sym diseases(top:$top){"
                              "name mondoID associations(top:$top){type name did zscore conf "
                              "evidence reference drug source score pvalue}}}}",
                      variables={"q": {"sym": "{symbol}"}, "top": "{top}"},
                      args=("symbol",), example={"symbol": "EGFR", "top": 3}),
            Operation("target_interactions", "A target's protein interaction partners with "
                      "the data source and scores of each", "graphql", method="POST",
                      graphql="query($q:ITarget,$top:Int){target(q:$q){sym ppiCounts{name "
                              "value} ppis(top:$top){target{sym uniprot tdl} props{name "
                              "value}}}}",
                      variables={"q": {"sym": "{symbol}"}, "top": "{top}"},
                      args=("symbol",), example={"symbol": "EGFR", "top": 3}),
            Operation("ligand", "A ligand or drug by id (prefix CID:, DC:, G2P:, UNII:, "
                      "LYCHI:) or name: structure, cross-references, number of targets and "
                      "of activities", "graphql", method="POST",
                      graphql="query($id:String){ligand(ligid:$id){ligid name isdrug smiles "
                              "description actcnt targetCount synonyms{name value}}}",
                      variables={"id": "{ligand_id}"}, args=("ligand_id",),
                      example={"ligand_id": "DC:1423"}),
            Operation("tdl_counts", "Number of targets per development level", "graphql",
                      method="POST",
                      graphql="query{targets(facets:[\"Target Development Level\"]){count "
                              "facets{facet values{name value}}}}"),
            Operation("version", "Database version served (e.g. pharos319)", "graphql",
                      method="POST", graphql="query{dbVersion}"),
        ), smoke="version", docs="https://pharos.nih.gov/api",
        rate_note="no stated limit; kept at 1 req/s"),
)

#: Connectors defined but not shipped. None: every operation above answered.
PENDING: tuple[PublicSource, ...] = ()
