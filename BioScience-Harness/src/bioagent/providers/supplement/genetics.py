"""Live connectors for genetics sources: variant-effect maps and mouse knockout phenotypes.

Checked from this environment on 2026-10-01 (review of 2026-09-30):

* **MaveDB** (``mavedb``): the documented REST API at ``api.mavedb.org/api/v1`` answers
  public reads without a key (a key is needed only to write or to read private records).
  Score sets from deep mutational scans, MPRAs and saturation genome editing: search,
  one score set's metadata (target gene with Ensembl/RefSeq/UniProt ids, method, licence,
  calibrations) and a page of its scores as CSV. The licence is chosen per score set
  (CC0 by default; CC BY 4.0, CC BY-SA 4.0 and, on a few older sets, CC BY-NC-SA 4.0), so
  a caller reads ``license.shortName`` from the score set before reusing its scores. The
  CC0 bulk archive on Zenodo (1.9 GB) is not wrapped: it exceeds the size this harness
  fetches, and the API serves the same records.
* **IMPC** (``impc``): the Solr cores behind mousephenotype.org, documented on its
  programmatic-access pages, answer unauthenticated at ``www.ebi.ac.uk/mi/impc/solr``.
  Per-gene look-ups only; www.ebi.ac.uk's robots.txt asks for 10 s between crawler
  requests, and the full phenotype calls are the ``impc`` snapshot (``tcmdb.extra.genetics``).

Not wrapped: the eQTL Catalogue REST API (retired, HTTP 410; the data are files, see the
``eqtl_catalogue`` dataset), the FinnGen PheWeb browser (robots.txt disallows everything;
summary statistics follow a registration form) and IEU OpenGWAS (every data endpoint
needs a personal JWT, and its EULA forbids commercial use without a licence and bulk
download through the API).
"""

from __future__ import annotations

from ..public_apis import Operation, PublicSource

_MAVEDB_LICENCE = ("Per score set: CC0 1.0 (default), CC BY 4.0, CC BY-SA 4.0, and CC "
                   "BY-NC-SA 4.0 on a few older sets (read license.shortName of each score "
                   "set); API software AGPL-3.0")

_IMPC_GENE_FIELDS = ("marker_symbol,marker_name,mgi_accession_id,phenotyping_data_available,"
                     "assignment_status,null_allele_production_status,"
                     "es_cell_production_status,significant_top_level_mp_terms")
_IMPC_CALL_FIELDS = ("marker_symbol,marker_accession_id,allele_symbol,allele_accession_id,"
                     "mp_term_id,mp_term_name,top_level_mp_term_name,zygosity,sex,"
                     "life_stage_name,procedure_name,parameter_stable_id,parameter_name,"
                     "phenotyping_center,statistical_method,p_value,effect_size,"
                     "percentage_change,resource_name,assertion_type_id")
_IMPC_RESULT_FIELDS = ("marker_symbol,marker_accession_id,allele_symbol,procedure_name,"
                       "parameter_stable_id,parameter_name,zygosity,life_stage_name,"
                       "phenotyping_center,status,significant,p_value,effect_size,"
                       "statistical_method,mp_term_id,mp_term_name,metadata_group")

SOURCES: tuple[PublicSource, ...] = (
    PublicSource(
        "mavedb", "MaveDB API", "https://api.mavedb.org/api/v1", "api.mavedb.org",
        _MAVEDB_LICENCE,
        "Multiplexed assays of variant effect (deep mutational scanning, MPRA, saturation "
        "genome editing): score-set search, a score set's target, method, licence and "
        "calibrations, and its per-variant scores (MAVE-HGVS) as CSV. What a score means "
        "differs between score sets: read the score set's methodText.",
        "genomics", (
            Operation("score_set", "One score set by URN: target gene(s) with Ensembl, "
                      "RefSeq and UniProt ids, score columns, method, licence, calibrations",
                      "score-sets/{urn}", args=("urn",),
                      example={"urn": "urn:mavedb:00000001-a-1"}),
            Operation("scores", "A page of a score set's variant scores as CSV (accession, "
                      "hgvs_nt, hgvs_splice, hgvs_pro, then the set's own score columns)",
                      "score-sets/{urn}/scores", params={"start": "{start}", "limit": "{limit}"},
                      accept="text/csv", args=("urn",),
                      example={"urn": "urn:mavedb:00000001-a-1", "start": 0, "limit": 50}),
            Operation("search_score_sets", "Published score sets matching free text (fuzzy: "
                      "'BRCA1' also finds BAP1 sets); limit at most 100",
                      "score-sets/search", method="POST",
                      json_body={"text": "{text}", "published": True, "limit": "{limit}"},
                      args=("text",), example={"text": "UBE2I", "limit": 5}),
            Operation("score_sets_by_target", "Published score sets whose target gene has "
                      "this exact name; limit at most 100",
                      "score-sets/search", method="POST",
                      json_body={"targets": ["{gene}"], "published": True,
                                 "limit": "{limit}"},
                      args=("gene",), example={"gene": "BRCA1", "limit": 5}),
            Operation("gene_score_sets", "An HGNC gene (symbol, name, HGNC id) with its "
                      "published score sets, paged", "genes/{symbol}",
                      params={"limit": "{limit}", "offset": "{offset}"}, args=("symbol",),
                      example={"symbol": "BRCA1", "limit": 2, "offset": 0}),
            Operation("licenses", "The licences a score set can carry, with their status",
                      "licenses/"),
        ), smoke="score_set", docs="https://api.mavedb.org/docs",
        rate_note="no enforced limit ('please be considerate'); kept at 1 req/s"),

    PublicSource(
        "impc", "IMPC Solr API", "https://www.ebi.ac.uk/mi/impc/solr", "www.ebi.ac.uk",
        "CC-BY-4.0",
        "International Mouse Phenotyping Consortium: knockout lines' significant phenotype "
        "calls (MP terms) with zygosity, sex, procedure, P value and effect size; all "
        "statistical results, significant or not; gene production status; and Phenodigm "
        "mouse-model to human-disease phenotype similarity. Mouse genes (MGI ids). Solr "
        "returns 10 rows unless rows is set.", "genomics", (
            Operation("gene", "A mouse gene's MGI id and phenotyping and production status",
                      "gene/select",
                      params={"q": "marker_symbol:{symbol}", "wt": "json", "rows": 1,
                              "fl": _IMPC_GENE_FIELDS},
                      args=("symbol",), example={"symbol": "Car4"}),
            Operation("phenotype_calls", "Significant genotype-phenotype calls of a gene "
                      "(knockout lines from IMPC and the legacy EuroPhenome, MGP and 3i "
                      "resources)", "genotype-phenotype/select",
                      params={"q": "marker_symbol:{symbol}", "wt": "json", "rows": "{rows}",
                              "fl": _IMPC_CALL_FIELDS},
                      args=("symbol",), example={"symbol": "Letmd1", "rows": 5}),
            Operation("genes_with_phenotype", "Genotype-phenotype calls annotated with exactly "
                      "this MP term (not its descendants)", "genotype-phenotype/select",
                      params={"q": 'mp_term_id:"{mp_id}"', "wt": "json", "rows": "{rows}",
                              "fl": _IMPC_CALL_FIELDS},
                      args=("mp_id",), example={"mp_id": "MP:0001297", "rows": 5}),
            Operation("statistical_results", "Every statistical test of a gene's lines, "
                      "significant or not (significant=false is a tested negative)",
                      "statistical-result/select",
                      params={"q": "marker_symbol:{symbol}", "wt": "json", "rows": "{rows}",
                              "fl": _IMPC_RESULT_FIELDS},
                      args=("symbol",), example={"symbol": "Car4", "rows": 5}),
            Operation("disease_models", "Phenodigm phenotype similarity between a gene's "
                      "mouse models (IMPC and MGI) and human diseases (OMIM, Orphanet); "
                      "association_curated marks a known gene-disease link, the rest is a "
                      "similarity score", "phenodigm/select",
                      params={"q": "type:disease_model_summary AND marker_symbol:{symbol}",
                              "wt": "json", "rows": "{rows}"},
                      args=("symbol",), example={"symbol": "Car4", "rows": 3}),
        ), smoke="gene", docs="https://www.mousephenotype.org/help/programmatic-data-access/",
        rate_note="www.ebi.ac.uk robots.txt sets Crawl-delay 10 s for crawling; these "
                  "per-gene and per-term look-ups on the Solr cores IMPC documents for "
                  "programmatic access are documented API use, not crawling, and are paced "
                  "by the shared www.ebi.ac.uk host limit (10 req/s; the transport limits "
                  "per host, not per path). Never enumerate genes through it: the full "
                  "calls are the impc snapshot"),
)

#: Nothing is pending: every operation above answered the verification run.
PENDING: tuple[PublicSource, ...] = ()
