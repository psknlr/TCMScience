"""Live connectors for natural-product and plant-name sources (review of 2026-09-30).

Each was checked from the harness on 2026-10-01:

* **COCONUT 2.0** (``coconut``): the one unauthenticated resource of COCONUT's documented
  REST API, ``POST /api/search``. Every other resource in its OpenAPI document
  (``/api/molecules``, ``/api/organisms``, ...) needs a Sanctum bearer token, i.e. an
  account; those are not wrapped. Bulk work uses the CC0 CSV (``tcmdb`` dataset
  ``coconut``).
* **KNApSAcK Core** (``knapsack``): the URL scheme KNApSAcK documents on its top page
  "for incorporation to program" (``info.php?sname=<item>&word=<keyword>``). ``info.php``
  answers with a JavaScript stub that sends the browser to ``information.php`` (one
  metabolite) or ``result.php`` (a list), so the operations call those two directly. The
  answers are HTML pages; nothing else is offered. The terms forbid redistribution and
  commercial use without contacting the KNApSAcK group, so these are per-entity look-ups
  for research, never a harvest.

Defined but not shipped (``PENDING``):

* **World Flora Online** (``wfo``): the documented name-matching REST API and GraphQL
  API at ``list.worldfloraonline.org``. The host sends only its leaf certificate (the
  "GeoTrust TLS RSA CA G1" intermediate is missing), so TLS verification fails with the
  standard CA bundle ("unable to get local issuer certificate", checked 2026-10-01).
  Verification is never turned off; the connector waits until the host serves its full
  chain or the transport learns to add a published intermediate. Its robots.txt also
  sets ``Crawl-delay: 10`` (``DEFAULT_RATES`` holds 0.1 req/s). The CC0 name backbone is
  the ``tcmdb`` dataset ``wfo``.
"""

from __future__ import annotations

from ..public_apis import Operation, PublicSource

__all__ = ["SOURCES", "PENDING"]

_COCONUT_LICENSE = ("CC0 (download page: 'COCONUT data is released under the Creative "
                    "Commons CC0 license'); the terms of service keep the restrictions of "
                    "the original data owners of each collection")
_KNAPSACK_LICENSE = ("CC BY-NC-ND 4.0 (KNApSAcK Core terms of service, Art. 2); 'cannot be "
                     "re-distributed or used for commercial purposes ... without contacting "
                     "the KNApSAcK DB group' (top page); cite Afendi et al. 2012, "
                     "doi:10.1093/pcp/pcr165")

SOURCES: tuple[PublicSource, ...] = (
    PublicSource(
        "coconut", "COCONUT 2.0 search API", "https://coconut.naturalproducts.net/api",
        "coconut.naturalproducts.net", _COCONUT_LICENSE,
        "Natural products (COCONUT CNP ids) found by name, SMILES, InChI or InChIKey, or by "
        "the organism or source collection they were reported from: identifier, name, "
        "structure, annotation level and organism/collection/citation counts, paginated "
        "(``data.total``, ``data.last_page``). A tag search that matches nothing answers "
        "``{\"data\": []}``; a collection must be named exactly as COCONUT titles it "
        "(e.g. 'KNApSaCK', 'DrugBankNP').", "natural-products", (
            Operation("search", "Molecules matching a name, SMILES, InChI or InChIKey",
                      "search", method="POST", json_body={"query": "{query}"},
                      args=("query",),
                      example={"query": "HNKJADCVZUBCPG-UHFFFAOYSA-N"}),
            Operation("organism_molecules", "Molecules reported from an organism (Latin "
                      "name; several comma-separated), one page of `limit`",
                      "search", method="POST",
                      json_body={"type": "tags", "tagType": "organisms",
                                 "query": "{organism}", "limit": "{limit}",
                                 "page": "{page}"},
                      args=("organism",),
                      example={"organism": "Scutellaria baicalensis", "limit": 5,
                               "page": 1}),
            Operation("collection_molecules", "Molecules of one source collection, by its "
                      "exact COCONUT title, one page of `limit`",
                      "search", method="POST",
                      json_body={"type": "tags", "tagType": "dataSource",
                                 "query": "{collection}", "limit": "{limit}",
                                 "page": "{page}"},
                      args=("collection",),
                      example={"collection": "DrugBankNP", "limit": 5, "page": 1}),
        ), smoke="search", docs="https://coconut.naturalproducts.net/api-documentation",
        rate_note="no stated limit; kept at 1 req/s"),

    PublicSource(
        "knapsack", "KNApSAcK Core (program URL scheme)",
        "https://www.knapsackfamily.com/knapsack_core", "www.knapsackfamily.com",
        _KNAPSACK_LICENSE,
        "Species-metabolite occurrences curated from the literature (NAIST). HTML pages, "
        "the targets of the documented 'incorporation to program' URL scheme: a "
        "metabolite's record (names, formula, mass, CAS, C_ID, InChIKey, SMILES) with the "
        "kingdom, family and species it was reported in; the metabolites reported for an "
        "organism or matching a name (forward match, at least three characters); the "
        "literature reference of one species-metabolite pair. Returned as page text "
        "(``format``/``text``); research look-ups only, no redistribution.",
        "natural-products", (
            Operation("metabolite", "One metabolite by KNApSAcK C_ID, with the organisms "
                      "it was reported in (HTML)", "information.php",
                      params={"sname": "C_ID", "word": "{c_id}"}, accept="text/html",
                      args=("c_id",), example={"c_id": "C00000001"}),
            Operation("organism_metabolites", "Metabolites reported for an organism "
                      "(forward match on the name, >= 3 characters; HTML table: C_ID, CAS, "
                      "name, formula, mass, organism)", "result.php",
                      params={"sname": "organism", "word": "{organism}"},
                      accept="text/html", args=("organism",),
                      example={"organism": "Scutellaria baicalensis"}),
            Operation("metabolite_search", "Metabolites whose name forward-matches a word "
                      "(>= 3 characters; HTML table)", "result.php",
                      params={"sname": "metabolite", "word": "{name}"}, accept="text/html",
                      args=("name",), example={"name": "baicalin"}),
            Operation("pair_reference", "The literature reference of one species-"
                      "metabolite pair: `row` is the 0-based row of the organism table on "
                      "the metabolite's page (HTML)", "information.php",
                      params={"mode": "r", "word": "{c_id}", "key": "{row}"},
                      accept="text/html", args=("c_id", "row"),
                      example={"c_id": "C00000001", "row": 0}),
        ), smoke="metabolite",
        docs="https://www.knapsackfamily.com/knapsack_core/top.php",
        rate_note="no stated limit or robots.txt; kept at 1 req/s"),
)

#: Defined, not shipped: TLS verification of list.worldfloraonline.org fails (the host
#: omits its intermediate certificate), checked 2026-10-01. See the module docstring.
PENDING: tuple[PublicSource, ...] = (
    PublicSource(
        "wfo", "World Flora Online name matching", "https://list.worldfloraonline.org",
        "list.worldfloraonline.org", "CC0 1.0 (WFO Plant List releases on Zenodo)",
        "Plant names matched to WFO ids in the current classification, and a name's "
        "accepted usage (a synonym is a name whose currentPreferredUsage.hasName differs "
        "from itself). The site asks that the API not be used to scrape: bulk work uses "
        "the Zenodo release (tcmdb dataset 'wfo').", "taxonomy", (
            Operation("match_name", "Match a plant name (with or without authors) to a WFO "
                      "id", "matching_rest.php", params={"input_string": "{name}"},
                      args=("name",), example={"name": "Scutellaria baicalensis Georgi"}),
            Operation("name_usage", "A WFO name and its current accepted usage", "gql.php",
                      method="POST",
                      graphql="query($id:String!){ taxonNameById(nameId:$id){ id "
                              "fullNameStringPlain currentPreferredUsage{ id hasName{ id "
                              "fullNameStringPlain } } } }",
                      variables={"id": "{wfo_id}"}, args=("wfo_id",),
                      example={"wfo_id": "wfo-0001048766"}),
        ), smoke="match_name", docs="https://list.worldfloraonline.org/",
        rate_note="robots.txt Crawl-delay: 10 (0.1 req/s)"),
)
