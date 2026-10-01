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

Some sources were deliberately not wrapped:
* undocumented POST endpoints (HERB ``/chedi/api/``, SymMap ``/related_components/``);
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
        ), smoke="epitopes_by_sequence", docs="https://query-api.iedb.org/docs/",
        rate_note="no key; no stated limit"),

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
