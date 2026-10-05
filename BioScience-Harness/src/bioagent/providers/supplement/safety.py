"""Safety: adverse outcome pathways (AOP-Wiki) and rare diseases (Orphadata), live.

Both answer per-entity look-ups without a key; each operation was executed from the
harness on 2026-10-01 (``scripts/verify_connectors.py --only aopwiki orphadata``). Bulk
copies of both are snapshot datasets in ``bioagent.tcmdb`` (``aopwiki``, ``orphadata``),
which are the reference for anything larger than a few look-ups.

Not wrapped:
* EPA's CTX APIs (ToxCast bioactivity, chemicals, hazard) need a personal API key, issued
  on request by e-mail, and their host did not answer from the harness. ToxCast is the
  ``comptox`` snapshot (invitrodb v4.3).
* Orphadata's look-up by gene symbol (``/rd-associated-genes/genes/symbols/{symbol}``)
  answers 404 'Query not found' for genes Orphanet lists (KIF7, AGA): it is in ``PENDING``.
* AOP-Wiki's ``/stressors/{id}.json`` answers 500; its paginated ``/aops.json`` index is
  about 1.1 MB a page (the quarterly XML is the bulk route).
"""

from __future__ import annotations

from ..public_apis import Operation, PublicSource

_ORPHA_LICENCE = ("CC-BY-4.0 (Orphadata Science; every response carries __licence). Cite "
                  "Orphadata Science with the data version; a tool that offers results "
                  "built on these data to third parties must say Orphanet data were used.")

SOURCES: tuple[PublicSource, ...] = (
    PublicSource(
        "aopwiki", "AOP-Wiki (OECD AOP Knowledge Base)", "https://aopwiki.org", "aopwiki.org",
        "CC BY-SA 2.0 (AOP-Wiki default); an AOP page may be 'All rights reserved' "
        "(see <wiki-license> in its XML)",
        "Adverse outcome pathways: one AOP as AOP-XML (key events, key-event relationships "
        "with weight of evidence, stressors and their chemicals, licence and OECD status), "
        "and JSON views of an AOP, a key event and a key-event relationship. The XML feed is "
        "documented ('Dynamic Downloads'); the .json views are undocumented Rails views "
        "the site serves and may change.", "toxicology", (
            Operation("aop_xml", "One AOP as AOP-XML, up to the minute (documented)",
                      "aops/{aop_id}.xml", accept="application/xml", args=("aop_id",),
                      example={"aop_id": 1}),
            Operation("aop", "One AOP: title, stressors, MIEs, KEs, AOs and relationships "
                      "(undocumented JSON view; no licence field)", "aops/{aop_id}.json",
                      args=("aop_id",), example={"aop_id": 1}),
            Operation("event", "One key event: level of organisation, organ, event "
                      "components (undocumented JSON view)", "events/{event_id}.json",
                      args=("event_id",), example={"event_id": 142}),
            Operation("relationship", "One key-event relationship: upstream and downstream "
                      "events (undocumented JSON view; weight of evidence is in the XML)",
                      "relationships/{relationship_id}.json", args=("relationship_id",),
                      example={"relationship_id": 324}),
        ), smoke="event", docs="https://aopwiki.org/info_pages/5",
        rate_note="no stated limit; kept at 1 req/s"),

    PublicSource(
        "orphadata", "Orphadata API (Orphanet)", "https://api.orphadata.com",
        "api.orphadata.com", _ORPHA_LICENCE,
        "Rare diseases by ORPHAcode: associated genes with association type and PMIDs, HPO "
        "phenotypes with frequency, cross-references (OMIM, ICD-10/11, MONDO, UMLS, MeSH, "
        "MedDRA, GARD), epidemiology, natural history, classifications; look-ups by OMIM "
        "number, disease name and HPO id. Documented OpenAPI service, updated each July and "
        "December.", "clinical", (
            Operation("genes", "Genes associated with a rare disease (type, status, PMIDs, "
                      "gene cross-references)", "rd-associated-genes/orphacodes/{orphacode}",
                      args=("orphacode",), example={"orphacode": 93}),
            Operation("phenotypes", "HPO phenotypes of a rare disease with frequency and "
                      "diagnostic-criterion marks", "rd-phenotypes/orphacodes/{orphacode}",
                      params={"lang": "{lang}"}, args=("orphacode",),
                      example={"orphacode": 58, "lang": "en"}),
            Operation("cross_references", "Names, synonyms and cross-references of a rare "
                      "disease", "rd-cross-referencing/orphacodes/{orphacode}",
                      params={"lang": "{lang}"}, args=("orphacode",),
                      example={"orphacode": 58, "lang": "en"}),
            Operation("epidemiology", "Prevalence records of a rare disease",
                      "rd-epidemiology/orphacodes/{orphacode}", params={"lang": "{lang}"},
                      args=("orphacode",), example={"orphacode": 58, "lang": "en"}),
            Operation("natural_history", "Age of onset, age of death and inheritance of a "
                      "rare disease", "rd-natural_history/orphacodes/{orphacode}",
                      params={"lang": "{lang}"}, args=("orphacode",),
                      example={"orphacode": 58, "lang": "en"}),
            Operation("classifications", "The classifications a rare disease belongs to, "
                      "with its parents and children", "rd-classification/orphacodes/"
                      "{orphacode}/hchids", args=("orphacode",), example={"orphacode": 58}),
            Operation("by_omim", "Rare diseases cross-referenced to an OMIM number",
                      "rd-cross-referencing/omims/{omim}", params={"lang": "{lang}"},
                      args=("omim",), example={"omim": 203450, "lang": "en"}),
            Operation("by_name", "A rare disease by its preferred name",
                      "rd-cross-referencing/orphacodes/names/{name}",
                      params={"lang": "{lang}"}, args=("name",),
                      example={"name": "Achondroplasia", "lang": "en"}),
            Operation("by_hpo", "Rare diseases annotated with HPO terms (full records: a "
                      "specific term returns about 1 MB, a common one over 10 MB)",
                      "rd-phenotypes/hpoids/{hpoids}", params={"lang": "{lang}"},
                      args=("hpoids",), example={"hpoids": "HP:0001166", "lang": "en"}),
        ), smoke="genes", docs="https://api.orphadata.com/openapi.json",
        rate_note="no stated limit; kept at 1 req/s"),
)

#: Defined but not shipped.
PENDING: tuple[PublicSource, ...] = (
    # /rd-associated-genes/genes/symbols/{symbol} answered 404 'Query not found' on
    # 2026-10-01 for KIF7 and AGA, both of which Orphanet associates with diseases.
    PublicSource(
        "orphadata_gene_lookup", "Orphadata API: diseases by gene symbol",
        "https://api.orphadata.com", "api.orphadata.com", _ORPHA_LICENCE,
        "Rare diseases associated with a gene, by HGNC symbol (answers 404 for listed "
        "genes; not shipped).", "clinical", (
            Operation("by_gene", "Rare diseases associated with a gene symbol",
                      "rd-associated-genes/genes/symbols/{symbol}", args=("symbol",),
                      example={"symbol": "KIF7"}),
        ), smoke="by_gene", docs="https://api.orphadata.com/openapi.json"),
)
