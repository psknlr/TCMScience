"""Live connectors for cell lines and perturbation resources (review of 2026-09-30).

Of the five sources of this domain only Cellosaurus has a documented, unauthenticated
API that answers one entity at a time. The others are reached as files
(``bioagent.tcmdb.extra.cells_perturb``):

* LINCS L1000 (GEO): the GEO series records are already reachable through the
  ``ncbi_eutils`` connector (``esearch`` / ``esummary`` with ``db=gds``); the CLUE API
  needs a registered user key and its terms are academic-only, so it is not wrapped.
* DepMap: the portal's file API sits behind a Cloudflare Turnstile challenge and its
  robots.txt asks for 500 s between requests; the CC BY 4.0 figshare deposits (24Q4 and
  earlier) are taken as files, and the figshare API is already the ``figshare``
  connector.
* scPerturb and ARCHS4: file deposits (Zenodo, S3), no query API.

Cellosaurus (https://api.cellosaurus.org, OpenAPI 1.0.4, CC BY 4.0) has no robots.txt
on the API host. Its search is Solr: a name query is *ranked*, not exact
(``id:HepG2`` returns HepG2 derivatives and not Hep-G2 itself, whose recommended name
is ``Hep-G2``), so the exact look-ups here go by accession, by any accession (primary or
secondary) and by cross-reference; resolving a name exactly is done offline against the
snapshot's ``cell_line_name`` table.
"""

from __future__ import annotations

from ..public_apis import Operation, PublicSource

_FIELDS = "id,ac,sy,ox,di,ca,sx,ag,hi,oi,cc,dt"
_SEARCH_FIELDS = "id,ac,sy,ox,di,ca"

SOURCES: tuple[PublicSource, ...] = (
    PublicSource(
        "cellosaurus", "Cellosaurus API", "https://api.cellosaurus.org", "api.cellosaurus.org",
        "CC BY 4.0 (Cellosaurus licensing statement and the API's OpenAPI info)",
        "Cell line knowledge resource (SIB): one cell line by its Cellosaurus accession "
        "(CVCL_), with names, species, diseases (NCIt/ORDO), category, parent cell line and "
        "comments, including the 'Problematic cell line' flag for contaminated or "
        "misidentified lines; look-ups by a former accession or by a cross-reference (a "
        "DepMap ACH- id, a COSMIC or ATCC number); and the release stamp. The search "
        "operation is Solr and ranked, not exact.", "cell-biology", (
            Operation("release_info", "Current release: version, date, number of cell lines",
                      "release-info", params={"format": "json"}),
            Operation("cell_line", "One cell line by Cellosaurus accession, limited to the "
                      "listed fields (a full record can be 850 KB, e.g. HeLa)",
                      "cell-line/{ac}", params={"format": "json", "fields": "{fields}"},
                      args=("ac",), example={"ac": "CVCL_1906", "fields": _FIELDS}),
            Operation("cell_line_by_accession", "Cell lines whose primary or secondary "
                      "accession is the one given (follows merged entries)",
                      "search/cell-line",
                      params={"q": "acas:{ac}", "format": "json", "fields": "{fields}",
                              "rows": "{rows}"},
                      args=("ac",), example={"ac": "CVCL_0027", "fields": _SEARCH_FIELDS,
                                             "rows": 5}),
            Operation("cell_line_by_xref", "Cell lines carrying a cross-reference id, e.g. a "
                      "DepMap model id (ACH-000739) or a cell bank catalogue number",
                      "search/cell-line",
                      params={"q": "dr:{xref}", "format": "json", "fields": "{fields}",
                              "rows": "{rows}"},
                      args=("xref",), example={"xref": "ACH-000739", "fields": _SEARCH_FIELDS,
                                               "rows": 5}),
            Operation("search", "Solr search over any field (id, sy, di, ox, cc, ...); results "
                      "are ranked by relevance, not matched exactly: compare ID/SY on the "
                      "client side", "search/cell-line",
                      params={"q": "{q}", "format": "json", "fields": "{fields}",
                              "rows": "{rows}"},
                      args=("q",), example={"q": 'id:"Hep-G2"', "fields": _SEARCH_FIELDS,
                                            "rows": 5}),
        ), smoke="release_info", docs="https://api.cellosaurus.org/",
        rate_note="no stated limit; kept at 1 req/s"),
)

PENDING: tuple[PublicSource, ...] = ()
