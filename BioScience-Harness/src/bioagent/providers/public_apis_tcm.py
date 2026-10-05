"""Live query connectors for the TCM databases that answer machine-readable requests.

The architecture document (``docs/tcm-data-sources.md``) lists 66 databases. On
2026-10-01 each one was checked from this environment: its home page, API, download
page, access barriers, licence and robots rules. Most TCM databases publish files and
no API. Those are wrapped as snapshot datasets in ``bioagent.tcmdb`` and queried
locally. This module holds the ones a program can query live:

* **Documented APIs**: DCABM-TCM (``Search/restful``), IEDB (PostgREST), the Hugging
  Face Hub and datasets-server, and figshare. BATMAN-TCM 2.0 (``queryTarget`` /
  ``queryTcm``) is defined but pending (see ``PENDING_TCM_SOURCES``). openFDA's herbal adverse-event queries are operations on the existing
  ``openfda`` source.
* **Undocumented read endpoints**: TCMBank, ITCM and TTD. These are the unauthenticated
  GET endpoints the sites' own pages call. They can change without notice, so each
  operation says so, the request rate is kept low, and the downloadable files in
  ``bioagent.tcmdb`` are the reference copy.

* **Site query endpoints (POST)**: SymMap ``/related_components/`` and HERB
  ``/chedi/api/``. These are the read queries the sites' own detail and search pages
  send, with no token and no login. They are wrapped at the repository owner's request
  (2026-10-02) for per-entity look-ups at one request per second. They are not used to
  copy a database wholesale.

Some sources were deliberately not wrapped:
* endpoints a site gates behind a login or a page token (the new TCMSP API, TCMSP
  pages, ImmPort data APIs, yiankb case search);
* commercial services whose terms forbid automated use (Medscape, Natural Medicines).

``docs/tcm-data-sources.md`` gives the reason for each. Every operation here is executed
by ``scripts/verify_connectors.py --only <key>``, and the result is written to
``data/connector_live_verification.csv``.
"""

from __future__ import annotations

from .public_apis import Operation, PublicSource

TCM_SOURCES: tuple[PublicSource, ...] = (
    PublicSource(
        "dcabm_tcm", "DCABM-TCM RESTful search", "http://bionet.ncpsb.org.cn/seed_tcm/Search",
        "bionet.ncpsb.org.cn", "Not stated (sister of BATMAN-TCM: free for academic use)",
        "Constituents detected in blood after oral TCM (prototypes and metabolites), "
        "per prescription and per herb, with the source study and detection conditions.",
        "tcm", (
            Operation("prescription_blood", "Blood constituents of prescriptions (pinyin "
                      "names)", "restful", method="POST",
                      json_body={"type": "prescription_blood",
                                 "filters": {"pinyinName": "{names}"}},
                      args=("names",), example={"names": ["LIU WEI DI HUANG WAN"]}),
            Operation("herb_blood", "Blood constituents of herbs (pinyin names)", "restful",
                      method="POST",
                      json_body={"type": "herb_blood", "filters": {"pinyinName": "{names}"}},
                      args=("names",), example={"names": ["SANG YE"]}),
            Operation("ingredient", "A blood constituent by PubChem CID: structure, "
                      "cross-references, the prescriptions and herbs it was detected for",
                      "restful", method="POST",
                      json_body={"type": "ingredients",
                                 "filters": {"PubChem CID": "{cids}"}},
                      args=("cids",), example={"cids": ["5280343"]}),
        ), smoke="herb_blood", docs="http://bionet.ncpsb.org.cn/dcabm-tcm/#/Download",
        rate_note="no stated limit; kept at 1 req/s"),

    PublicSource(
        "tcmbank", "TCMBank (site JSON endpoints)", "http://tcmbank.cn/api", "tcmbank.cn",
        "Not stated (help page: for academic queries)",
        "Herbs, ingredients, targets and diseases with TCMBank ids. Undocumented endpoints "
        "used by the site's own pages; plain http because the site's TLS certificate "
        "expired on 2026-06-02.", "tcm", (
            Operation("herb_search", "Herbs by name (Chinese, English or Latin); undocumented",
                      "herb/search",
                      params={"herb_name": "{name}", "cur_page": "{page}",
                              "per_page": "{per_page}"},
                      args=("name",), example={"name": "Huang Qi", "page": 1, "per_page": 5}),
            Operation("ingredient_search", "Ingredients by name; undocumented",
                      "ingredient/search",
                      params={"ingredient_name": "{name}", "formula": "",
                              "cur_page": "{page}", "per_page": "{per_page}"},
                      args=("name",), example={"name": "quercetin", "page": 1, "per_page": 5}),
            Operation("detail", "One record by TCMBank id; kind is herb, ingredient, target "
                      "or disease; undocumented", "{kind}/detail",
                      params={"tcmbank_id": "{tcmbank_id}"}, args=("kind", "tcmbank_id"),
                      example={"kind": "herb", "tcmbank_id": "TCMBANKHE000053"}),
            Operation("herb_ingredients", "Ingredients of a herb; undocumented",
                      "herb/toingredients", params={"tcmbank_id": "{tcmbank_id}"},
                      args=("tcmbank_id",), example={"tcmbank_id": "TCMBANKHE000053"}),
        ), smoke="herb_ingredients", docs="http://tcmbank.cn/Download",
        rate_note="undocumented; no stated limit; kept at 1 req/s"),

    PublicSource(
        "itcm", "ITCM (site JSON endpoints)", "http://itcm.biotcm.net", "itcm.biotcm.net",
        "Not stated",
        "Integrated TCM database: herbs with TCMID/ETCM/SymMap/TCMSP cross-references, "
        "their ingredients, formulas and enriched diseases, and ingredient targets. "
        "Undocumented endpoints used by the site's detail pages; ITCM numeric ids.", "tcm", (
            Operation("herb", "Herb record with cross-references to TCMID, ETCM, SymMap and "
                      "TCMSP; undocumented", "getHerbInfo/{herb_id}", args=("herb_id",),
                      example={"herb_id": 21}),
            Operation("herb_ingredients", "Ingredients of a herb as PubChem CID -> score; "
                      "undocumented", "getHerbCoIng/{herb_id}", args=("herb_id",),
                      example={"herb_id": 21}),
            Operation("herb_formulas", "Formulas containing a herb; undocumented",
                      "getHerbCoFor/{herb_id}", args=("herb_id",), example={"herb_id": 21}),
            Operation("herb_diseases", "Diseases enriched for a herb's targets (p, FDR); "
                      "undocumented", "getHerbCoDis/{herb_id}", args=("herb_id",),
                      example={"herb_id": 21}),
            Operation("ingredient_targets", "Targets of an ingredient with the source "
                      "database of each pair; undocumented", "getIngCoTarget/{ingredient_id}",
                      args=("ingredient_id",), example={"ingredient_id": 1}),
        ), smoke="herb", docs="http://itcm.biotcm.net/download.html",
        rate_note="undocumented; no stated limit; kept at 1 req/s"),

    PublicSource(
        "ttd", "TTD (site JSON endpoints)", "https://ttd.idrblab.cn/api/ttd",
        "ttd.idrblab.cn", "Not stated (citation requested)",
        "Therapeutic targets with their drugs, mechanisms, diseases and pathways. "
        "Undocumented endpoints used by the TTD site; the full downloads are the "
        "reference copy.", "pharmacology", (
            Operation("target", "Target general information; undocumented",
                      "getTargetGeneralInfo/{target_id}", args=("target_id",),
                      example={"target_id": "T47101"}),
            Operation("target_drugs", "Drugs of a target by development status, with "
                      "mechanism and disease; undocumented", "getTargetDrugAndMoA/{target_id}",
                      args=("target_id",), example={"target_id": "T47101"}),
            Operation("target_pathways", "KEGG/WikiPathways of a target; undocumented",
                      "getTargetPathway/{target_id}", args=("target_id",),
                      example={"target_id": "T47101"}),
        ), smoke="target", docs="https://ttd.idrblab.cn/full-data-download",
        rate_note="undocumented; no stated limit; kept at 1 req/s"),

    PublicSource(
        "symmap", "SymMap v2 (site query endpoint)", "http://www.symmap.org",
        "www.symmap.org", "Not stated (files 'free to download'; BUCM copyright)",
        "Entities related to a SymMap entity: a herb's ingredients, targets, TCM and "
        "modern-medicine symptoms, diseases and syndromes, or the herbs of an ingredient. "
        "The form post SymMap's own detail pages send (undocumented); wrapped on the "
        "repository owner's decision of 2026-10-02, per entity, at most 1 req/s.", "tcm", (
            Operation("related", "Entities of one type related to an entity. entity_id is a "
                      "SymMap id (SMHB herb, SMIT ingredient, SMTT target, SMTS TCM "
                      "symptom, SMMS modern symptom, SMDE disease, SMSY syndrome); related "
                      "is Herb, Mol, Gene, TCM_symptom, MM_symptom, Disease or Syndrome; "
                      "filter 0 all, 1 P<0.05, 2 FDR(BH)<0.05, 3 FDR(Bonferroni)<0.05. "
                      "Rows inferred through the network carry Relationship, IES Value, "
                      "P_value and FDR; direct rows carry the supporting PubMed abstract.",
                      "related_components/", method="POST",
                      form={"rrid": "{entity_id}", "table_name": "{related}",
                            "filter": "{filter}"},
                      args=("entity_id", "related"),
                      example={"entity_id": "SMHB00187", "related": "Syndrome",
                               "filter": 0}),
        ), smoke="related", docs="http://www.symmap.org/help/",
        rate_note="undocumented form endpoint; kept at 1 req/s"),

    PublicSource(
        "herb_api", "HERB 2.0 (site query endpoint)", "http://47.92.70.12/chedi",
        "47.92.70.12", "Not stated",
        "A HERB 2.0 entity's record with its relations: a herb's ingredients, formulas, "
        "targets and diseases (inferred, with P and FDR), literature-reported targets and "
        "diseases (PubMed, grade, supporting sentence), meta-analyses and trials; an "
        "ingredient's targets with the upstream source of each pair. The JSON post HERB's "
        "own pages send (undocumented); wrapped on the repository owner's decision of "
        "2026-10-02, per entity, at most 1 req/s.", "tcm", (
            Operation("detail", "Record of one entity; label is Herb, Ingredient, Formula, "
                      "Target or Disease; entity_id a HERB id (HERB…, HBIN…, HBFO…, "
                      "HBTAR…, HBDIS…). About 0.5 MB for a well-studied herb.",
                      "api/", method="POST",
                      json_body={"v": "{entity_id}", "label": "{label}",
                                 "key_id": "{entity_id}", "func_name": "detail_api"},
                      args=("entity_id", "label"),
                      example={"entity_id": "HBTAR000113", "label": "Target"}),
            Operation("search", "Search one entity type by name or id",
                      "api/", method="POST",
                      json_body={"keyword": "{keyword}", "label": "{label}",
                                 "func_name": "search_api"},
                      args=("keyword", "label"), example={"keyword": "黄芪", "label": "Herb"}),
        ), smoke="search", docs="http://47.92.70.12/#/Help",
        rate_note="undocumented JSON endpoint; kept at 1 req/s"),

    PublicSource(
        "iedb", "IEDB Query API", "https://query-api.iedb.org", "query-api.iedb.org",
        "CC-BY-4.0", "Immune epitopes, antigens and T-cell/B-cell/MHC assays (PostgREST; "
        "filters use PostgREST syntax, e.g. eq.SIINFEKL or ilike.*IL-6*).", "immunology", (
            Operation("epitopes_by_sequence", "Epitopes with a given linear sequence",
                      "epitope_search",
                      params={"linear_sequence": "eq.{sequence}",
                              "select": "structure_id,linear_sequence,structure_iri",
                              "limit": "{limit}"},
                      args=("sequence",), example={"sequence": "SIINFEKL", "limit": 5}),
            Operation("antigens", "Source antigens by name pattern", "antigen_search",
                      params={"parent_source_antigen_name": "ilike.*{name}*",
                              "select": "parent_source_antigen_iri,parent_source_antigen_name",
                              "limit": "{limit}"},
                      args=("name",), example={"name": "Interleukin-6", "limit": 5}),
            Operation("tcell_assays", "T-cell assays for antigens matching a name pattern",
                      "tcell_search",
                      params={"parent_source_antigen_name": "ilike.*{name}*",
                              "select": "tcell_id,linear_sequence,parent_source_antigen_name",
                              "limit": "{limit}"},
                      args=("name",), example={"name": "Interleukin-6", "limit": 5}),
            # added with the supplementary sources (review of 2026-09-30), each answered
            # on 2026-10-01
            Operation("epitope", "One epitope by IEDB structure id: antigens, source "
                      "organisms, MHC alleles, outcomes, PubMed ids, ChEBI ids",
                      "epitope_search",
                      params={"structure_id": "eq.{structure_id}",
                              "select": "structure_id,structure_iri,linear_sequence,"
                                        "structure_type,non_peptidic_molecule_names,chebi_ids,"
                                        "parent_source_antigen_iris,parent_source_antigen_names,"
                                        "source_organism_iris,source_organism_names,"
                                        "mhc_allele_names,qualitative_measures,pubmed_ids",
                              "limit": 1},
                      args=("structure_id",), example={"structure_id": 58560}),
            Operation("epitope_summary", "IEDB's one-paragraph summary of an epitope (assay "
                      "and publication counts)", "epitope_summary",
                      params={"structure_id": "eq.{structure_id}"},
                      args=("structure_id",), example={"structure_id": 58560}),
            Operation("epitopes_by_organism", "Epitopes from a source organism by NCBI taxon "
                      "id (e.g. 4220 Artemisia vulgaris, mugwort)", "epitope_search",
                      params={"source_organism_iri_search": "cs.{NCBITaxon:{taxon_id}}",
                              "select": "structure_id,linear_sequence,structure_type,"
                                        "parent_source_antigen_iris,parent_source_antigen_names,"
                                        "source_organism_names,qualitative_measures",
                              "limit": "{limit}"},
                      args=("taxon_id",), example={"taxon_id": 4220, "limit": 5}),
            Operation("tcell_by_antigen", "T-cell assays on epitopes of one antigen (UniProt "
                      "accession), with host, MHC restriction and outcome", "tcell_search",
                      params={"parent_source_antigen_iri": "eq.UNIPROT:{accession}",
                              "select": "tcell_id,structure_id,linear_sequence,"
                                        "host_organism_name,mhc_allele_name,assay_names,"
                                        "qualitative_measure,pubmed_id",
                              "limit": "{limit}"},
                      args=("accession",), example={"accession": "P01012", "limit": 5}),
            Operation("tcell_by_molecule", "T-cell assays on non-peptidic epitopes (drugs, "
                      "haptens, natural products) whose name matches a pattern",
                      "tcell_search",
                      params={"non_peptidic_molecule_name": "ilike.*{name}*",
                              "select": "tcell_id,structure_id,non_peptidic_molecule_iri,"
                                        "non_peptidic_molecule_name,chebi_ids,"
                                        "host_organism_name,assay_names,qualitative_measure,"
                                        "pubmed_id",
                              "limit": "{limit}"},
                      args=("name",), example={"name": "penicillin", "limit": 5}),
            Operation("bcell_by_sequence", "B-cell / antibody assays on a linear epitope",
                      "bcell_search",
                      params={"linear_sequence": "eq.{sequence}",
                              "select": "bcell_id,structure_id,linear_sequence,"
                                        "parent_source_antigen_iri,parent_source_antigen_name,"
                                        "host_organism_name,assay_names,qualitative_measure,"
                                        "antibody_isotype,pubmed_id",
                              "limit": "{limit}"},
                      args=("sequence",), example={"sequence": "NLVPMVATV", "limit": 5}),
            Operation("mhc_by_sequence", "MHC binding and elution assays of a peptide, with "
                      "qualitative and quantitative outcome (quantitative values carry HTML "
                      "entities such as &nbsp;)", "mhc_search",
                      params={"linear_sequence": "eq.{sequence}",
                              "select": "elution_id,structure_id,linear_sequence,"
                                        "mhc_allele_name,assay_names,qualitative_measure,"
                                        "quantitative_measure,pubmed_id",
                              "limit": "{limit}"},
                      args=("sequence",), example={"sequence": "SIINFEKL", "limit": 5}),
            Operation("tcr_by_epitope", "T-cell receptor groups (CDR3 alpha/beta) that "
                      "recognise a peptide", "tcr_search",
                      params={"linear_sequences": "cs.{{sequence}}",
                              "select": "receptor_group_id,receptor_type,"
                                        "receptor_species_names,chain1_cdr3_seq,"
                                        "chain2_cdr3_seq,linear_sequences,mhc_allele_names",
                              "limit": "{limit}"},
                      args=("sequence",), example={"sequence": "NLVPMVATV", "limit": 5}),
            Operation("bcr_by_cdr3", "B-cell receptor groups with a given heavy-chain CDR3 "
                      "and the antigens they bind", "bcr_search",
                      params={"chain1_cdr3_seq": "eq.{cdr3}",
                              "select": "receptor_group_id,receptor_type,"
                                        "receptor_species_names,chain1_cdr3_seq,"
                                        "chain2_cdr3_seq,linear_sequences,"
                                        "parent_source_antigen_names",
                              "limit": "{limit}"},
                      args=("cdr3",), example={"cdr3": "TRLGDYGYAYTMDY", "limit": 5}),
            Operation("reference_by_pmid", "An IEDB reference by PubMed id, with the "
                      "epitopes curated from it", "reference_search",
                      params={"pubmed_id": "eq.{pmid}",
                              "select": "reference_id,reference_iri,pubmed_id,reference_title,"
                                        "journal_name,reference_date,structure_ids,"
                                        "linear_sequences",
                              "limit": 1},
                      args=("pmid",), example={"pmid": "9469429"}),
        ), smoke="epitopes_by_sequence",
        docs="https://discuss.iedb.org/t/immune-epitope-database-query-api-iq-api/154",
        rate_note="no key; no stated limit; at most 10,000 rows per page, and paging needs an "
                  "'order' parameter (otherwise pages are inconsistent)"),

    PublicSource(
        "huggingface", "Hugging Face Hub API", "https://huggingface.co/api",
        "huggingface.co", "Per dataset (read the card's licence)",
        "Dataset metadata and file listings, e.g. ShenNong-TCM-Dataset and TCM-Ladder.",
        "datasets", (
            Operation("dataset", "Dataset metadata, card data and licence",
                      "datasets/{repo_id}", args=("repo_id",),
                      example={"repo_id": "michaelwzhu/ShenNong_TCM_Dataset"}),
            Operation("dataset_tree", "Files at the root of a dataset revision",
                      "datasets/{repo_id}/tree/{revision}", args=("repo_id",),
                      example={"repo_id": "timzzyus/TCM-Ladder", "revision": "main"}),
        ), smoke="dataset", docs="https://huggingface.co/docs/hub/api"),

    PublicSource(
        "hf_datasets_server", "Hugging Face datasets-server",
        "https://datasets-server.huggingface.co", "datasets-server.huggingface.co",
        "Per dataset (read the card's licence)",
        "Splits and paged rows of a dataset without downloading it.", "datasets", (
            Operation("splits", "Configurations and splits", "splits",
                      params={"dataset": "{repo_id}"}, args=("repo_id",),
                      example={"repo_id": "michaelwzhu/ShenNong_TCM_Dataset"}),
            Operation("rows", "A page of rows (length <= 100)", "rows",
                      params={"dataset": "{repo_id}", "config": "{config}",
                              "split": "{split}", "offset": "{offset}", "length": "{length}"},
                      args=("repo_id",),
                      example={"repo_id": "michaelwzhu/ShenNong_TCM_Dataset",
                               "config": "default", "split": "train", "offset": 0,
                               "length": 2}),
        ), smoke="splits", docs="https://huggingface.co/docs/dataset-viewer"),

    PublicSource(
        "figshare", "figshare API v2", "https://api.figshare.com/v2", "api.figshare.com",
        "Per article (CC BY 4.0 for TCMP-300)",
        "Article metadata and file listings with sizes and MD5s, e.g. the TCMP-300 "
        "medicinal-plant image set.", "datasets", (
            Operation("article", "Article metadata, licence and files", "articles/{article_id}",
                      args=("article_id",), example={"article_id": 29432726}),
            Operation("article_files", "Files of an article with sizes and MD5s",
                      "articles/{article_id}/files", args=("article_id",),
                      example={"article_id": 29432726}),
        ), smoke="article", docs="https://docs.figshare.com/"),
)

#: Herbal adverse-event operations, added to the existing ``openfda`` source. Product
#: names in FAERS are free text, so a herb is queried by the names reporters use.
OPENFDA_HERBAL_OPERATIONS: tuple[Operation, ...] = (
    Operation("herbal_event_reactions", "Reactions reported with a product name, counted "
              "(MedDRA preferred terms); e.g. GINKGO, GINSENG, ST. JOHN'S WORT",
              "drug/event.json",
              params={"search": 'patient.drug.medicinalproduct:"{product}"',
                      "count": "patient.reaction.reactionmeddrapt.exact", "limit": "{limit}"},
              args=("product",), example={"product": "GINKGO", "limit": 10}),
    Operation("herbal_event_coreported_drugs", "Products most often reported in the same "
              "case as a product name: the starting point for herb-drug signal mining",
              "drug/event.json",
              params={"search": 'patient.drug.medicinalproduct:"{product}"',
                      "count": "patient.drug.medicinalproduct.exact", "limit": "{limit}"},
              args=("product",), example={"product": "GINKGO", "limit": 10}),
)

#: Connectors whose service did not answer the verification run. They are defined so
#: they can be verified again (``verify_connectors.py --only batman_tcm2``) and are
#: registered only once a run records SUCCEEDED for every operation. BATMAN-TCM 2.0's
#: API answered queryTarget for the survey on 2026-10-01, then returned 502 and timed out
#: during verification the same day. Its complete download files are in the
#: ``batman2`` dataset, so its data is available either way.
PENDING_TCM_SOURCES: tuple[PublicSource, ...] = (
    PublicSource(
        "batman_tcm2", "BATMAN-TCM 2.0 API", "http://batman2api.cloudna.cn",
        "batman2api.cloudna.cn", "Free for academic use (BATMAN-TCM; commercial use by "
        "arrangement with the authors)",
        "Known (literature, HIT/DrugBank/KEGG/TTD) and predicted target proteins of TCM "
        "formulas, herbs and ingredients; and the ingredients that target given genes.",
        "tcm", (
            Operation("query_targets", "Targets of formulas, herbs or ingredients. kind is "
                      "'fufang' (formula pinyin), 'herb' (pinyin) or 'ingredient' (PubChem "
                      "CIDs); items is a list. score >= 500 is the authors' high-confidence "
                      "cut-off for predictions.",
                      "queryTarget", method="POST",
                      json_body={"content": [{"clusterName": "query", "type": "{kind}",
                                              "list": "{items}"}],
                                 "pvalue": "{pvalue}", "userInScore": "{score}"},
                      args=("kind", "items"),
                      example={"kind": "herb", "items": ["HUANG QI"], "pvalue": 0.05,
                               "score": 500}),
            Operation("query_tcm", "TCM ingredients whose known or predicted targets "
                      "include the given genes (NCBI Gene ids).",
                      "queryTcm", method="POST",
                      json_body={"userInGenid": "{gene_ids}", "userInScore": "{score}"},
                      args=("gene_ids",), example={"gene_ids": ["7157"], "score": 500}),
        ), smoke="query_targets", docs="http://bionet.ncpsb.org.cn/batman-tcm/#/Api",
        rate_note="the documentation asks for one second between calls; 2026-10-01 the API "
                  "answered intermittently (502)"),
)
