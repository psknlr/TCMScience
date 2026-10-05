"""Pharmacogenomics and protein-family connectors: CPIC, GPCRdb, KLIFS and ProteomicsDB.

Each source was checked from this environment on 2026-10-01 (catalogue entries 110-114)
and every operation below answered without a key, a login or a form:

* **CPIC** (``api.cpicpgx.org``): the documented PostgREST API of the Clinical
  Pharmacogenetics Implementation Consortium. Filters use PostgREST syntax
  (``eq.CYP2C19``). The column once named ``pgkbcalevel`` is now ``clinpgxlevel``;
  selecting the old name answers 400. CPIC content is CC0.
* **GPCRdb** (``gpcrdb.org/services``): receptors, their drugs with mechanism and
  indication, ligand bioactivities (redistributed from ChEMBL, the Guide to
  Pharmacology and DrugCentral), mutagenesis data and structures. CC BY 4.0.
* **KLIFS** (``klifs.net``): kinases, co-crystallised ligands, structures with
  conformation annotations, interaction fingerprints, ChEMBL bioactivities per ligand
  and the kinase drug list. ``/api`` is the stable v1 API, ``/api_v2`` the beta.
* **ProteomicsDB** (``www.proteomicsdb.org``): protein abundance per tissue, fluid or
  cell line, and (API v2, beta) Kinobeads dose-response curves and the drug-sensitivity
  datasets it hosts. The data are CC BY 2.5; the site's terms license use of the
  service itself for research purposes only.

Not wrapped here:

* **PharmVar**'s REST API now answers every call with 401 unless an account-bound
  ``Api-Key`` header is sent. No connector is built; the public per-gene and all-gene
  zip downloads are wrapped as the ``pharmvar`` dataset (``tcmdb.extra.pgx_proteins``).
* **ClinPGx** (formerly PharmGKB) is used through its bulk files (the ``clinpgx``
  dataset); the CPIC API above already carries ClinPGx ids and levels for the
  guideline pairs.

Every operation is executed by ``scripts/verify_connectors.py --only <key>``.
"""

from __future__ import annotations

from ..public_apis import Operation, PublicSource

_CPIC_PAIR = ("pairid,genesymbol,drugname,cpiclevel,clinpgxlevel,pgxtesting,guidelinename,"
              "guidelineurl,usedforrecommendation,provisional")
_PDB_EXPRESSION = (
    "api/proteinexpression.xsodata/InputParams(PROTEINFILTER='{uniprot}',MS_LEVEL=1,"
    "TISSUE_ID_SELECTION='',TISSUE_CATEGORY_SELECTION='{category}',SCOPE_SELECTION=1,"
    "GROUP_BY_TISSUE=1,CALCULATION_METHOD={method},EXP_ID=-1)/Results")

SOURCES: tuple[PublicSource, ...] = (
    PublicSource(
        "cpic", "CPIC database API", "https://api.cpicpgx.org/v1", "api.cpicpgx.org",
        "CC0-1.0 (CPIC curated content; attribution requested)",
        "Clinical Pharmacogenetics Implementation Consortium: gene-drug pairs with CPIC "
        "and ClinPGx levels, drugs with RxNorm/DrugBank/ATC/ClinPGx ids, allele clinical "
        "function, and phenotype-specific prescribing recommendations. PostgREST: "
        "filters are written eq.<value>.", "pharmacogenomics", (
            Operation("pairs_for_gene", "Gene-drug pairs of a gene with CPIC level, ClinPGx "
                      "level of evidence, PGx testing level and guideline",
                      "pair_view", params={"select": _CPIC_PAIR, "genesymbol": "eq.{gene}",
                                           "limit": "{limit}"},
                      args=("gene",), example={"gene": "CYP2C19", "limit": 20}),
            Operation("pairs_for_drug", "Gene-drug pairs of a drug (name as CPIC writes "
                      "it, lower case)", "pair_view",
                      params={"select": _CPIC_PAIR, "drugname": "eq.{drug}",
                              "limit": "{limit}"},
                      args=("drug",), example={"drug": "clopidogrel", "limit": 20}),
            Operation("pairs_by_level", "Gene-drug pairs with one CPIC level (A, B, C, D)",
                      "pair_view", params={"select": _CPIC_PAIR, "cpiclevel": "eq.{level}",
                                           "limit": "{limit}"},
                      args=("level",), example={"level": "A", "limit": 5}),
            Operation("drug", "A drug by name with its RxNorm, DrugBank, ATC and ClinPGx "
                      "ids", "drug", params={"name": "eq.{drug}"}, args=("drug",),
                      example={"drug": "clopidogrel"}),
            Operation("gene", "A gene by HGNC symbol with its cross-references and "
                      "lookup method", "gene", params={"symbol": "eq.{gene}"},
                      args=("gene",), example={"gene": "CYP2D6"}),
            Operation("alleles", "Alleles of a gene with CPIC clinical function, activity "
                      "value and evidence strength", "allele",
                      params={"select": "genesymbol,name,clinicalfunctionalstatus,"
                                        "activityvalue,strength,citations",
                              "genesymbol": "eq.{gene}", "limit": "{limit}"},
                      args=("gene",), example={"gene": "CYP2C19", "limit": 10}),
            Operation("recommendations", "Phenotype-specific prescribing recommendations "
                      "of a drug (lookup key, implications, classification)",
                      "recommendation_view",
                      params={"drugname": "eq.{drug}", "limit": "{limit}"},
                      args=("drug",), example={"drug": "clopidogrel", "limit": 5}),
            Operation("report_files", "CPIC report files of one type (PAIR, ALLELE_SUMMARY, "
                      "ALLELE_DEFINITION, DIPLOTYPE_PHENOTYPE, RECOMMENDATION, ...)",
                      "file_artifact", params={"select": "type,filename,url",
                                               "type": "eq.{type}"},
                      args=("type",), example={"type": "PAIR"}),
        ), smoke="drug", docs="https://api.cpicpgx.org/v1/",
        rate_note="no stated limit; kept at 1 req/s"),

    PublicSource(
        "gpcrdb", "GPCRdb web services", "https://gpcrdb.org/services", "gpcrdb.org",
        "CC-BY-4.0 (ligand bioactivities keep their upstream licences: ChEMBL CC BY-SA 3.0, "
        "Guide to Pharmacology and DrugCentral CC BY-SA 4.0)",
        "G protein-coupled receptors: receptor records by UniProt entry name or accession, "
        "approved and trial drugs per receptor with mechanism and indication, ligand "
        "bioactivities per receptor (from ChEMBL, the Guide to Pharmacology and "
        "DrugCentral; ligands by name and SMILES only), literature mutagenesis data and "
        "structures. Names are HTML-escaped.", "pharmacology", (
            Operation("protein", "Receptor by UniProt entry name", "protein/{entry_name}/",
                      args=("entry_name",), example={"entry_name": "adrb2_human"}),
            Operation("protein_by_accession", "Receptor by UniProt accession",
                      "protein/accession/{accession}/", args=("accession",),
                      example={"accession": "P07550"}),
            Operation("drugs", "Approved and trial drugs on a receptor: phase, indication, "
                      "status, drug type, mechanism (agonist, antagonist, ...)",
                      "drugs/{entry_name}/", args=("entry_name",),
                      example={"entry_name": "adrb2_human"}),
            Operation("ligands", "Ligand bioactivities on a receptor (value type, standard "
                      "value, assay, upstream source, DOI); can exceed 2 MB for "
                      "well-studied receptors", "ligands/{entry_name}/",
                      args=("entry_name",), example={"entry_name": "gpr35_human"}),
            Operation("mutants", "Literature mutagenesis data of a receptor (PMID, "
                      "position, ligand, effect and fold change)", "mutants/{entry_name}/",
                      args=("entry_name",), example={"entry_name": "ednrb_human"}),
            Operation("structure", "A structure by PDB code (receptor, state, ligands)",
                      "structure/{pdb_code}/", args=("pdb_code",),
                      example={"pdb_code": "2RH1"}),
        ), smoke="protein", docs="https://docs.gpcrdb.org/web_services.html",
        rate_note="no stated limit; kept at 1 req/s"),

    PublicSource(
        "klifs", "KLIFS API", "https://klifs.net", "klifs.net",
        "Free for academia and industry (KLIFS FAQ; no formal licence); bioactivities are "
        "ChEMBL data (CC BY-SA 3.0), structures from the PDB",
        "Kinase-ligand interaction fingerprints and structures: kinase ids with UniProt "
        "and IUPHAR ids, co-crystallised ligands (InChIKey, SMILES), structures with DFG "
        "and alpha-C helix conformations, pocket interaction fingerprints, ChEMBL "
        "bioactivities per ligand (any target, any organism) and the kinase drug list. "
        "/api is v1; /api_v2 is labelled beta.", "structural-biology", (
            Operation("kinase_id", "A kinase by name and species: KLIFS id, HGNC symbol, "
                      "family, group, UniProt, IUPHAR, 85-residue pocket", "api/kinase_ID",
                      params={"kinase_name": "{name}", "species": "{species}"},
                      args=("name",), example={"name": "EGFR", "species": "HUMAN"}),
            Operation("kinase_names", "Kinases of a group (TK, TKL, STE, CK1, AGC, CAMK, "
                      "CMGC, Other, Atypical) and species", "api/kinase_names",
                      params={"kinase_group": "{group}", "species": "{species}"},
                      args=("group",), example={"group": "TK", "species": "HUMAN"}),
            Operation("ligands", "Co-crystallised ligands of kinases (comma-separated KLIFS "
                      "kinase ids): ligand id, PDB code, name, SMILES, InChIKey",
                      "api/ligands_list", params={"kinase_ID": "{kinase_ids}"},
                      args=("kinase_ids",), example={"kinase_ids": "406"}),
            Operation("structures", "Structures of kinases (comma-separated KLIFS kinase "
                      "ids) with ligand, resolution, quality and conformation",
                      "api/structures_list", params={"kinase_ID": "{kinase_ids}"},
                      args=("kinase_ids",), example={"kinase_ids": "406"}),
            Operation("ligand_bioactivities", "ChEMBL bioactivities of a KLIFS ligand "
                      "(standard type, relation, value, units, pChEMBL); not limited to "
                      "kinases or to human", "api/bioactivity_list_id",
                      params={"ligand_ID": "{ligand_id}"}, args=("ligand_id",),
                      example={"ligand_id": "26"}),
            Operation("interaction_fingerprint", "Ligand-pocket interaction fingerprint of a "
                      "structure (85 residues x 7 interaction types; API v2 beta)",
                      "api_v2/interactions_get_IFP", params={"structure_ID": "{structure_id}"},
                      args=("structure_id",), example={"structure_id": "782"}),
            Operation("drugs", "Kinase drugs and clinical candidates: INN, brand, phase, "
                      "approval year, SMILES, ChEMBL id, PDB ligand (API v2 beta; about "
                      "650 rows, no target field)", "api_v2/drug_list"),
        ), smoke="kinase_id", docs="https://klifs.net/swagger/",
        rate_note="no stated limit; kept at 1 req/s"),

    PublicSource(
        "proteomicsdb", "ProteomicsDB API", "https://www.proteomicsdb.org/proteomicsdb/logic",
        "www.proteomicsdb.org",
        "CC BY 2.5 (data); the Terms of Use license the service for research purposes only "
        "and forbid making it available to third parties",
        "Mass-spectrometry protein abundance (iBAQ or TOP3, log10) per tissue, body fluid or "
        "cell line, by UniProt accession; API v2 (beta, OData): proteins, Kinobeads "
        "dose-response curves per protein and the drug-sensitivity datasets hosted "
        "(several re-host CCLE, GDSC and CTRP).", "proteomics", (
            Operation("expression", "Protein abundance per tissue/fluid (category "
                      "'tissue;fluid') or cell line ('cell line'); method 0 = iBAQ, 1 = TOP3",
                      _PDB_EXPRESSION,
                      params={"$select": "UNIQUE_IDENTIFIER,TISSUE_ID,TISSUE_NAME,"
                                         "TISSUE_SAP_SYNONYM,SAMPLE_ID,SAMPLE_NAME,"
                                         "AFFINITY_PURIFICATION,EXPERIMENT_ID,EXPERIMENT_NAME,"
                                         "EXPERIMENT_SCOPE,EXPERIMENT_SCOPE_NAME,PROJECT_ID,"
                                         "PROJECT_NAME,PROJECT_STATUS,UNNORMALIZED_INTENSITY,"
                                         "NORMALIZED_INTENSITY,MIN_NORMALIZED_INTENSITY,"
                                         "MAX_NORMALIZED_INTENSITY,SAMPLES",
                              "$format": "json"},
                      args=("uniprot",),
                      example={"uniprot": "P00533", "category": "tissue;fluid", "method": 0}),
            Operation("protein", "API v2 (beta): proteins of a UniProt accession with their "
                      "internal PROTEIN_ID (an accession can map to more than one)",
                      "api_v2/api.xsodata/Protein",
                      params={"$filter": "UNIQUE_IDENTIFIER eq '{uniprot}'",
                              "$format": "json", "$top": "{top}"},
                      args=("uniprot",), example={"uniprot": "P00533", "top": 5}),
            Operation("curves", "API v2 (beta): dose-response curves of a protein (Kinobeads "
                      "competition: COD, BIC, P value, scope)",
                      "api_v2/api.xsodata/Protein({protein_id})/Curve",
                      params={"$format": "json", "$top": "{top}"},
                      args=("protein_id",), example={"protein_id": 51261, "top": 5}),
            Operation("dose_response_datasets", "API v2 (beta): the drug-sensitivity "
                      "datasets hosted, with description, URI and DOI",
                      "api_v2/api.xsodata/DoseResponseDataSet",
                      params={"$format": "json", "$top": "{top}"}, example={"top": 25}),
        ), smoke="protein", docs="https://www.proteomicsdb.org/api",
        rate_note="no stated limit; kept at 1 req/s"),
)

#: Defined but not shipped: none. PharmVar's API needs an account key, so no connector
#: was written for it (see the module docstring).
PENDING: tuple[PublicSource, ...] = ()
