"""Live connectors for chemical identity, reactions, enzyme kinetics and id mapping.

Each was checked from the harness on 2026-10-01; every operation below answered
without a key or login (``scripts/verify_connectors.py --only chebi rhea sabio_rk
metanetx``).

* **ChEBI 2.0** (``www.ebi.ac.uk/chebi/backend/api/public``): the documented REST API
  (OpenAPI 3.0.3 at ``/chebi/backend/api/schema/``). ``/chebi`` is not disallowed by
  www.ebi.ac.uk's robots.txt, which asks crawlers for a 10 s crawl delay; these are
  per-entity look-ups, and bulk use goes through the ``chebi`` snapshot (``tcmdb``).
* **Rhea** (``www.rhea-db.org/rhea?query=...&format=tsv``): robots.txt disallows every
  URL with a query string (``Disallow: /*?``), but the REST help page documents these
  URLs "for programs". Only targeted searches with a small ``limit`` are wrapped, at one
  request per second; whole-database work uses the ``rhea`` snapshot.
* **SABIO-RK Export API** (``sabiork.h-its.org/export-api/sabio``): documented by an
  OpenAPI 3.1.1 file, read-only, no key, 60 requests a minute per IP. robots.txt is
  ``Disallow: /`` for the whole host; the API's own documentation offers it for
  programs, so only per-entity look-ups and small searches are wrapped, at one request
  per second, and nothing is enumerated. The data are under a non-commercial licence.
  The legacy ``sabioRestWebServices`` paths now redirect to a 404 page.
* **MetaNetX SPARQL** (``rdf.metanetx.org/sparql/``): the documented endpoint over
  MNXref; robots.txt sets a 10 s crawl delay, which the rate limit follows.

Not wrapped: FoodData Central's API needs an api.data.gov key (a keyless call answers
403 API_KEY_MISSING); its CC0 bulk files are the ``fooddata_central`` snapshot.
"""

from __future__ import annotations

from ..public_apis import Operation, PublicSource

__all__ = ["SOURCES", "PENDING"]

_KAEMPFEROL_SMILES = "Oc1ccc(cc1)-c1oc2cc(O)cc(O)c2c(=O)c1O"

_MNX_PREFIXES = ("PREFIX mnx: <https://rdf.metanetx.org/schema/> "
                 "PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#> ")

SOURCES: tuple[PublicSource, ...] = (
    PublicSource(
        "chebi", "ChEBI 2.0 REST API", "https://www.ebi.ac.uk/chebi/backend/api/public",
        "www.ebi.ac.uk", "CC BY 4.0",
        "Chemical Entities of Biological Interest: a compound's names, formula, mass, "
        "default structure (SMILES, InChI, InChIKey), natural-product origins, ontology "
        "parents and children (is_a, has_role, conjugate acid/base) and cross-references; "
        "text and structure search. ChEBI ids are CHEBI:<n> (or the bare number).",
        "chemistry", (
            Operation("compound", "One compound's full record by ChEBI id",
                      "compound/{chebi_id}/", args=("chebi_id",),
                      example={"chebi_id": "CHEBI:28499"}),
            Operation("compounds", "Several compounds by ChEBI id (comma-separated); "
                      "secondary ids resolve to their primary entry", "compounds/",
                      params={"chebi_ids": "{chebi_ids}"}, args=("chebi_ids",),
                      example={"chebi_ids": "CHEBI:28499,CHEBI:2979"}),
            Operation("search", "Text search over names and synonyms, best match first",
                      "es_search/", params={"term": "{term}", "page": "{page}",
                                            "size": "{size}"},
                      args=("term",), example={"term": "baicalein", "page": 1, "size": 5}),
            Operation("parents", "Outgoing ontology relations (is_a, has_role, "
                      "has_functional_parent, is_conjugate_base_of, ...)",
                      "ontology/parents/{chebi_id}/", args=("chebi_id",),
                      example={"chebi_id": "CHEBI:2979"}),
            Operation("children", "Incoming ontology relations (the entities that point "
                      "to this one)", "ontology/children/{chebi_id}/", args=("chebi_id",),
                      example={"chebi_id": "CHEBI:28499"}),
            Operation("structure_search", "Structure search by SMILES; search_type is "
                      "similarity, substructure or connectivity; similarity is 0.4-1.0; "
                      "three_star_only=false also returns 2-star entries",
                      "structure_search/",
                      params={"smiles": "{smiles}", "search_type": "{search_type}",
                              "similarity": "{similarity}",
                              "three_star_only": "{three_star_only}", "page": "{page}",
                              "size": "{size}"},
                      args=("smiles",),
                      example={"smiles": _KAEMPFEROL_SMILES, "search_type": "similarity",
                               "similarity": 0.8, "three_star_only": "true", "page": 1,
                               "size": 5}),
        ), smoke="compound", docs="https://www.ebi.ac.uk/chebi/backend/api/docs/",
        rate_note="robots.txt asks crawlers for a 10 s delay; per-entity look-ups only, "
                  "bulk via the chebi snapshot"),

    PublicSource(
        "rhea", "Rhea REST (reaction search)", "https://www.rhea-db.org", "www.rhea-db.org",
        "CC BY 4.0",
        "Expert-curated biochemical reactions as TSV. query takes Rhea's search syntax: "
        "chebi:58573 (participants are pH 7.3 microspecies, so neutral ids find nothing: "
        "map them with the rhea snapshot's chebi_ph7_3_mapping), uniprot:P00439, "
        "ec:2.1.1.155, RHEA:15105 or free text. columns is a comma-separated list of "
        "rhea-id, equation, chebi, chebi-id, ec, uniprot (a count), go, pubmed and "
        "reaction-xref(EcoCyc|MetaCyc|KEGG|Reactome|M-CSA).", "metabolism", (
            Operation("search", "Reactions matching a query, as TSV with the chosen "
                      "columns (keep limit small)", "rhea",
                      params={"query": "{query}", "columns": "{columns}", "format": "tsv",
                              "limit": "{limit}"},
                      accept="text/tab-separated-values", args=("query",),
                      example={"query": "chebi:58573",
                               "columns": "rhea-id,equation,chebi-id,ec,uniprot,pubmed",
                               "limit": 5}),
        ), smoke="search", docs="https://www.rhea-db.org/help/rest-api",
        rate_note="robots.txt disallows query URLs to crawlers; the REST help documents "
                  "them for programs: targeted searches only, 1 req/s"),

    PublicSource(
        "sabio_rk", "SABIO-RK Export API", "https://sabiork.h-its.org/export-api/sabio",
        "sabiork.h-its.org",
        "HITS SABIO-RK Non-Commercial Purpose License (internal non-commercial research "
        "and academic use only; cite SABIO-RK)",
        "Curated enzyme kinetics from the literature: kinetic-law entries with Km, kcat, "
        "Vmax, Ki and IC50 values and units, the reaction and its substrates, products, "
        "inhibitors and activators, the enzyme (EC, UniProt, wild type or mutant), the "
        "organism, tissue, pH, temperature and buffer, and the PubMed reference. q uses "
        "SABIO's Solr fields (Organism, ECNumber, UniProtKB_AC, Substrate, Product, "
        "Inhibitor, Activator, ParameterType, PubMedID, ...).", "metabolism", (
            Operation("kinlaw_search", "Kinetic-law entries matching a query, one page "
                      "(keep pageSize small)", "kinlaw-entry/json",
                      params={"q": "{q}", "page": "{page}", "pageSize": "{page_size}"},
                      args=("q",),
                      example={"q": "Inhibitor:quercetin", "page": 1, "page_size": 5}),
            Operation("kinlaw_entry", "One kinetic-law entry by its SABIO entry id",
                      "kinlaw-entry/json/{entry_id}", args=("entry_id",),
                      example={"entry_id": 5182}),
            Operation("compound", "A SABIO compound with its structures and ChEBI, KEGG, "
                      "PubChem and MetaNetX cross-references", "compound/{compound_id}",
                      args=("compound_id",), example={"compound_id": 5066}),
            Operation("enzyme_by_ec", "An enzyme class by EC number", "enzyme/by-ec/{ec}",
                      args=("ec",), example={"ec": "1.14.18.1"}),
            Operation("protein", "A protein by UniProt accession, with its EC number and "
                      "cross-references", "uniprot/by-uniprot-id/{uniprot_id}",
                      args=("uniprot_id",), example={"uniprot_id": "P00439"}),
        ), smoke="compound", docs="https://sabiork.h-its.org/openapi/export-api.json",
        rate_note="60 requests/min/IP (429 beyond); robots.txt Disallow: / - documented "
                  "API, per-entity look-ups only, 1 req/s"),

    PublicSource(
        "metanetx", "MetaNetX SPARQL", "https://rdf.metanetx.org", "rdf.metanetx.org",
        "CC BY 4.0 for MNXref's own content; rows also carry their upstream's terms, "
        "several non-commercial (KEGG, BiGG, HMDB, MetaCyc, enviPath, SABIO-RK)",
        "MNXref identifier reconciliation: the MNX chemical behind an external id "
        "(identifiers.org form: CHEBI:28499, hmdb:HMDB0005801, kegg.compound:C05903, "
        "lipidmaps:..., metacyc.compound:...), or one MNX chemical's name, formula, "
        "charge, InChIKey and cross-references. Chemicals are protonation-normalised to "
        "the major microspecies at pH 7.3, so the InChIKey can be a charged form.",
        "metabolism", (
            Operation("chem_by_xref", "MNX chemicals with an external cross-reference",
                      "sparql/", method="POST",
                      form={"query": _MNX_PREFIXES + (
                          "SELECT ?chem ?name ?formula ?charge ?inchikey WHERE { "
                          "?chem mnx:chemXref <https://identifiers.org/{xref}> . "
                          "OPTIONAL { ?chem rdfs:comment ?name } "
                          "OPTIONAL { ?chem mnx:formula ?formula } "
                          "OPTIONAL { ?chem mnx:charge ?charge } "
                          "OPTIONAL { ?chem mnx:inchikey ?inchikey } } LIMIT 20")},
                      accept="application/sparql-results+json", args=("xref",),
                      example={"xref": "hmdb:HMDB0005801"}),
            Operation("chem", "One MNX chemical: name, formula, charge, InChIKey and its "
                      "cross-references", "sparql/", method="POST",
                      form={"query": _MNX_PREFIXES + (
                          "SELECT ?name ?formula ?charge ?inchikey ?xref WHERE { "
                          "<https://rdf.metanetx.org/chem/{mnx_id}> rdfs:label ?label . "
                          "OPTIONAL { <https://rdf.metanetx.org/chem/{mnx_id}> "
                          "rdfs:comment ?name } "
                          "OPTIONAL { <https://rdf.metanetx.org/chem/{mnx_id}> "
                          "mnx:formula ?formula } "
                          "OPTIONAL { <https://rdf.metanetx.org/chem/{mnx_id}> "
                          "mnx:charge ?charge } "
                          "OPTIONAL { <https://rdf.metanetx.org/chem/{mnx_id}> "
                          "mnx:inchikey ?inchikey } "
                          "OPTIONAL { <https://rdf.metanetx.org/chem/{mnx_id}> "
                          "mnx:chemXref ?xref } } LIMIT 200")},
                      accept="application/sparql-results+json", args=("mnx_id",),
                      example={"mnx_id": "MNXM1672"}),
        ), smoke="chem_by_xref", docs="https://rdf.metanetx.org/",
        rate_note="robots.txt Crawl-delay: 10 - one request per 10 s"),
)

#: Defined but not shipped: none.
PENDING: tuple[PublicSource, ...] = ()
