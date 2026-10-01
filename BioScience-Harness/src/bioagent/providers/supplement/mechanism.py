"""Live connectors for mechanism sources: IntAct and Complex Portal, SIGNOR, JASPAR.

Checked from the harness on 2026-10-01 (review of 2026-09-30); every operation below was
sent live with its example arguments and answered.

* **IntAct / Complex Portal** (EMBL-EBI): PSICQUIC (MIQL queries, PSI-MITAB rows or a
  count), the IntAct portal's interaction search (OpenAPI-documented, JSON) and the
  Complex Portal web service (search, one complex). www.ebi.ac.uk's robots.txt sets
  ``Crawl-Delay: 10`` and does not disallow these paths; they are per-entity lookups.
* **SIGNOR**: the documented API (``APIs.php``): an entity's causal relations, a
  pathway's relations and description. ``API/getHumanData.php`` (the whole human set)
  fails intermittently with a PHP memory error answered as HTTP 200, and is a bulk dump
  besides, so it is not wrapped; the quarterly release file is the snapshot
  (``tcmdb`` dataset ``signor``).
* **JASPAR** 2026: the REST API (matrix by id, matrices of a factor, search, versions,
  releases). The API allows 25 requests per second; the harness keeps to 1.

Not wrapped: the BioGRID REST API and the ORCS API (both need a registered access key;
the downloads are the ``tcmdb`` datasets ``biogrid`` and ``biogrid_orcs``) and GtoPdb's
web services (an API key since September 2026, and paid access for commercial users).
"""

from __future__ import annotations

from ..public_apis import Operation, PublicSource

__all__ = ["SOURCES", "PENDING"]

_PSICQUIC = "Tools/webservices/psicquic/intact/webservices/current/search/query/{query}"
_MITAB25 = ("PSI-MITAB 2.5, 15 tab-separated columns and no header row (so the generic "
            "parser reads the first interaction as the column names): ID(s) A, ID(s) B, "
            "alt. IDs A, alt. IDs B, aliases A, aliases B, detection method, first author, "
            "publication ids, taxid A, taxid B, interaction type, source database, "
            "interaction ids, confidence (intact-miscore)")
_SIGNOR_COLUMNS = ("ENTITYA TYPEA IDA DATABASEA ENTITYB TYPEB IDB DATABASEB EFFECT MECHANISM "
                   "RESIDUE SEQUENCE TAX_ID CELL_DATA TISSUE_DATA MODULATOR_COMPLEX "
                   "TARGET_COMPLEX MODIFICATIONA MODASEQ MODIFICATIONB MODBSEQ PMID DIRECT "
                   "NOTES ANNOTATOR SENTENCE SIGNOR_ID SCORE")

SOURCES: tuple[PublicSource, ...] = (
    PublicSource(
        "intact", "IntAct and Complex Portal (EMBL-EBI)", "https://www.ebi.ac.uk",
        "www.ebi.ac.uk",
        "CC BY 4.0 (IntAct data); CC0 1.0 (Complex Portal data)",
        "Molecular interaction evidence curated by IntAct and the IMEx partners (MINT, "
        "UniProt, ...): binary interactions with detection method, interaction type, "
        "publication, host organism and MI-score; and Complex Portal's curated "
        "macromolecular complexes with their members, stoichiometry and evidence.",
        "mechanism", (
            Operation("psicquic_query", "Interactions matching a MIQL query (a gene name, "
                      "'identifier:P07550', 'species:human AND type:\"direct interaction\"'), "
                      "a page of rows. " + _MITAB25, _PSICQUIC,
                      params={"format": "tab25", "firstResult": "{first}",
                              "maxResults": "{max}"},
                      accept="text/plain", args=("query",),
                      example={"query": "ADRB2", "first": 0, "max": 10}),
            Operation("psicquic_count", "Number of interaction evidences matching a MIQL "
                      "query (plain integer)", _PSICQUIC, params={"format": "count"},
                      accept="text/plain", args=("query",), example={"query": "ADRB2"}),
            Operation("interactions", "IntAct portal search, one page (JSON): each "
                      "interaction's ids, molecules, taxa, detection method, type, MI-score, "
                      "PubMed id and a 'negative' flag. Records are large (about 150 KB each, "
                      "they embed XML and JSON serialisations): keep page_size small",
                      "intact/ws/interaction/findInteractions/{query}",
                      params={"page": "{page}", "pageSize": "{page_size}"}, args=("query",),
                      example={"query": "ADRB2", "page": 0, "page_size": 2}),
            Operation("complex_search", "Complex Portal search by gene, protein, complex "
                      "name or id: complex accession, name, organism, members, whether "
                      "predicted", "intact/complex-ws/search/{query}",
                      params={"first": "{first}", "number": "{number}", "format": "json"},
                      args=("query",), example={"query": "SMAD4", "first": 0, "number": 5}),
            Operation("complex", "One Complex Portal complex: participants with "
                      "stoichiometry, function, assembly, ligands, agonists, antagonists, "
                      "diseases, cross-references and evidence",
                      "intact/complex-ws/complex/{complex_ac}", args=("complex_ac",),
                      example={"complex_ac": "CPX-1"}),
        ), smoke="psicquic_count",
        docs="https://www.ebi.ac.uk/intact/ws/interaction/v3/api-docs ; "
             "https://psicquic.github.io/PsicquicSpec_1_4_Rest.html ; "
             "https://www.ebi.ac.uk/intact/complex-ws/",
        rate_note="robots.txt Crawl-Delay 10 for www.ebi.ac.uk; per-entity lookups only"),

    PublicSource(
        "signor", "SIGNOR API", "https://signor.uniroma2.it", "signor.uniroma2.it",
        "CC BY 4.0",
        "Signed causal relations curated from the literature: regulator, target, effect "
        "(up/down-regulates activity or quantity), mechanism (phosphorylation, binding, "
        "transcriptional regulation, ...), residue, organism, cell and tissue (BTO), "
        "direct or not, PubMed id, SIGNOR id and score; and SIGNOR's pathways.",
        "mechanism", (
            Operation("entity_relations", "Every causal relation involving one entity "
                      "(UniProt accession, PubChem CID, or a SIGNOR id such as "
                      "SIGNOR-C1) in one organism (9606, 10090, 10116). Tab-separated with "
                      "no header row (the generic parser reads the first relation as the "
                      "column names); the columns are " + _SIGNOR_COLUMNS,
                      "getData.php", params={"organism": "{organism}", "id": "{id}"},
                      accept="text/plain", args=("id",),
                      example={"id": "P62258", "organism": "9606"}),
            Operation("relations_among", "SIGNOR's 'connect' search for a list of proteins "
                      "(comma-separated UniProt accessions): the relations that connect "
                      "them, including those of the entities linking them (287 rows for the "
                      "4-protein example); headerless like entity_relations", "getData.php",
                      params={"type": "connect", "proteins": "{proteins}"},
                      accept="text/plain", args=("proteins",),
                      example={"proteins": "P29317,Q06124,P04049,P15056"}),
            Operation("pathway_relations", "The causal relations of one SIGNOR pathway, "
                      "with a header: pathway, regulator and target with their cellular "
                      "location, effect, mechanism, residue, PubMed id, SIGNOR id, score",
                      "getPathwayData.php", params={"pathway": "{pathway}",
                                                   "relations": "only"},
                      accept="text/plain", args=("pathway",),
                      example={"pathway": "SIGNOR-MM"}),
            Operation("pathway_description", "One SIGNOR pathway's name and description",
                      "getPathwayData.php", params={"pathway": "{pathway}",
                                                   "description": ""},
                      accept="text/plain", args=("pathway",),
                      example={"pathway": "SIGNOR-MM"}),
        ), smoke="entity_relations", docs="https://signor.uniroma2.it/APIs.php",
        rate_note="academic server, no stated limit; kept at 1 req/s"),

    PublicSource(
        "jaspar", "JASPAR REST API", "https://jaspar.elixir.no/api/v1", "jaspar.elixir.no",
        "CC BY 4.0",
        "Transcription factor binding profiles (JASPAR 2026): a matrix's counts, the "
        "factor's UniProt ids, species, class and family, the experiment type and PubMed "
        "ids; matrices of a factor; free-text search; a matrix's versions; releases.",
        "mechanism", (
            Operation("matrix", "One matrix by id (MA0139.2): position frequency counts "
                      "and annotations", "matrix/{matrix_id}/", params={"format": "json"},
                      args=("matrix_id",), example={"matrix_id": "MA0139.2"}),
            Operation("factor_matrices", "The latest CORE matrices of a factor (exact "
                      "name) in one species", "matrix/",
                      params={"name": "{name}", "tax_id": "{tax_id}", "collection": "CORE",
                              "version": "latest", "format": "json"},
                      args=("name",), example={"name": "SP1", "tax_id": 9606}),
            Operation("search", "Free-text matrix search (fuzzy: SP1 also finds AR, KLF13)",
                      "matrix/",
                      params={"search": "{query}", "tax_id": "{tax_id}", "collection": "CORE",
                              "version": "latest", "page_size": "{page_size}",
                              "format": "json"},
                      args=("query",), example={"query": "SP1", "tax_id": 9606,
                                                "page_size": 3}),
            Operation("versions", "Every version of a matrix (base id MA0139)",
                      "matrix/{base_id}/versions/", params={"format": "json"},
                      args=("base_id",), example={"base_id": "MA0139"}),
            Operation("releases", "JASPAR releases (for pinning a version)", "releases/",
                      params={"format": "json"}),
        ), smoke="matrix", docs="https://jaspar.elixir.no/api/v1/docs/",
        rate_note="API allows 25 req/s; kept at 1 req/s"),
)

#: Nothing pending: every operation defined above verified live on 2026-10-01.
PENDING: tuple[PublicSource, ...] = ()
