"""Reference-atlas metadata: the Human Cell Atlas, HuBMAP, 4D Nucleome and BioSamples.

These services describe *where* a measurement comes from: the organ, cell suspension,
donor, assay and sample behind a single-cell, spatial or 3D-genome dataset. They are
context for a TCM finding (which atlas data covers the tissue a target acts in, which
sequencing samples exist for a medicinal species), not compound-target relations.

Every connector here answers unauthenticated metadata queries only. Data files that need
a token (HCA managed-access projects, 4DN portal downloads, HuBMAP protected sequence
data) are never wrapped. Checked from the harness on 2026-10-01:

* **HCA Data Portal (Azul)**: the Azul host's robots.txt disallows every path except
  ``/`` and ``/swagger/`` for generic agents, while its OpenAPI document and the HCA
  "Data Browser API" page offer the ``/index`` endpoints for programmatic use. The
  connector therefore makes per-entity look-ups only (one project or one file by UUID)
  and never pages through the index or generates manifests.
* **HuBMAP**: the search API (Elasticsearch query DSL over the public-entities index),
  the entity API and the portal's ``.json`` entity and organ records, all documented
  (docs.hubmapconsortium.org/apis, the portal's llms.txt). Three hosts, so three
  connectors. Queries ask for named ``_source`` fields: an unfiltered hit can carry a
  multi-megabyte file list and submitter contact details.
* **4D Nucleome**: the portal's metadata search and per-item JSON. robots.txt disallows
  ``limit=all`` and every ``@@download`` URL; neither is used. Processed files are on the
  AWS Open Data bucket (``open_data_url`` in the file record) and the loop sets used here
  are in ``tcmdb.extra.atlases``.
* **EMBL-EBI BioSamples**: documented REST/HAL API. www.ebi.ac.uk sets ``Crawl-Delay:
  10`` in robots.txt (``/biosamples`` itself is not disallowed). The connector does not
  pace at that delay: it is paced by the rate the host shares with the other EBI
  connectors (``DEFAULT_RATES``), so it offers per-sample look-ups and one-page searches
  only, never sweeps.

HTAN is not wrapped: every programmatic route to its files needs an account (Synapse,
Google, CGC or dbGaP), and the portal reads a database through credentials embedded in
its front end, which are not a public API.
"""

from __future__ import annotations

from ..public_apis import Operation, PublicSource

#: Fields asked of the HuBMAP search API: identifiers, assay, organ, status and DOI.
#: Never ``files`` (megabytes per processed dataset) or the contact fields.
_HUBMAP_FIELDS = ["uuid", "hubmap_id", "entity_type", "dataset_type", "status",
                  "data_access_level", "origin_samples.organ", "donor.hubmap_id",
                  "group_name", "title", "doi_url", "published_timestamp"]

#: Fields asked of the 4DN search: the search result otherwise inlines lab, award and
#: submitter records (with e-mail addresses) for every hit.
_FOURDN_SET_FIELDS = ["accession", "description", "dataset_label", "condition",
                      "experimentset_type", "status", "public_release"]
_FOURDN_FILE_FIELDS = ["accession", "filename", "description", "file_size", "md5sum",
                       "genome_assembly", "open_data_url", "track_and_facet_info",
                       "status", "public_release"]

SOURCES: tuple[PublicSource, ...] = (
    PublicSource(
        "hca_azul", "Human Cell Atlas Data Portal (Azul index)",
        "https://service.azul.data.humancellatlas.org/index",
        "service.azul.data.humancellatlas.org",
        "CC BY 4.0 for open-access projects (HCA data use agreement, release dcp60); "
        "managed-access projects (DUO GRU / GRU-NCU) need an HCA Data Access Committee "
        "approval and are not served to anonymous callers",
        "One HCA project or file by UUID: organs and organ parts, selected cell types and "
        "cell counts, donor species, development stage and disease, library construction, "
        "the project's GEO/INSDC/ArrayExpress accessions, its data-use restriction and "
        "whether it is accessible anonymously, and file records (format, size, sha256, "
        "DRS URI). catalog selects a release (e.g. dcp60); omitted, the service's default "
        "release answers. Documented (OpenAPI); robots.txt disallows the API paths for "
        "crawlers, so only per-entity look-ups are offered.", "single-cell", (
            Operation("project", "One project by its UUID, with its aggregated samples, "
                      "specimens, donors, cell suspensions, protocols, accessions and "
                      "contributed matrices", "projects/{project_id}",
                      params={"catalog": "{catalog}"}, args=("project_id",),
                      example={"project_id": "74b6d569-3b11-42ef-b6b1-a0454522b4a0",
                               "catalog": None}),
            Operation("file", "One file by its UUID: format, size, sha256, DRS URI and the "
                      "project, specimens and donors it derives from", "files/{file_id}",
                      params={"catalog": "{catalog}"}, args=("file_id",),
                      example={"file_id": "6e63e10e-7a5f-52b8-9242-df9d169b802a",
                               "catalog": None}),
        ), smoke="project",
        docs="https://service.azul.data.humancellatlas.org/swagger/index.html",
        rate_note="robots.txt disallows the API for crawlers; per-entity look-ups only, "
                  "kept at 1 req/s"),

    PublicSource(
        "hubmap_search", "HuBMAP Search API",
        "https://search.api.hubmapconsortium.org/v3",
        "search.api.hubmapconsortium.org",
        "HuBMAP External Data Sharing Policy: open data released under a permissive "
        "licence such as CC BY 4.0 (DOI-registered datasets carry CC BY 4.0); users "
        "agree not to identify or contact participants",
        "Published HuBMAP datasets of healthy human tissue (public-entities index): by "
        "organ code (the HuBMAP organ code, e.g. SP spleen, LK/RK left/right kidney, LI "
        "large intestine, HT heart) or by dataset type (e.g. CODEX, RNAseq, ATACseq, "
        "LC-MS). Each hit gives the HuBMAP id and UUID, dataset type, organ, donor, "
        "group, status, access level and DOI. Elasticsearch query DSL (documented).",
        "single-cell", (
            Operation("datasets_by_organ", "Datasets from one organ (HuBMAP organ code)",
                      "search", method="POST",
                      json_body={"size": "{size}", "_source": {"includes": _HUBMAP_FIELDS},
                                 "query": {"bool": {"filter": [
                                     {"term": {"entity_type.keyword": "Dataset"}},
                                     {"term": {"origin_samples.organ.keyword":
                                               "{organ_code}"}}]}}},
                      args=("organ_code",), example={"organ_code": "SP", "size": 5}),
            Operation("datasets_by_type", "Datasets of one dataset type (e.g. CODEX, "
                      "RNAseq, ATACseq, LC-MS, MALDI)", "search", method="POST",
                      json_body={"size": "{size}", "_source": {"includes": _HUBMAP_FIELDS},
                                 "query": {"bool": {"filter": [
                                     {"term": {"entity_type.keyword": "Dataset"}},
                                     {"term": {"dataset_type.keyword": "{dataset_type}"}}]}}},
                      args=("dataset_type",), example={"dataset_type": "CODEX", "size": 5}),
        ), smoke="datasets_by_organ",
        docs="https://smart-api.info/ui/7aaf02b838022d564da776b03f357158",
        rate_note="no stated limit; kept at 1 req/s"),

    PublicSource(
        "hubmap_entity", "HuBMAP Entity API", "https://entity.api.hubmapconsortium.org",
        "entity.api.hubmapconsortium.org",
        "HuBMAP External Data Sharing Policy (open data: permissive, e.g. CC BY 4.0)",
        "The provenance record of one HuBMAP dataset, sample or donor by HuBMAP id "
        "(HBM...) or UUID: entity type, dataset type, status, access level, antibodies of "
        "a spatial-proteomics panel, direct ancestors, metadata and DOI. Documented.",
        "single-cell", (
            Operation("entity", "One entity by HuBMAP id or UUID", "entities/{entity_id}",
                      args=("entity_id",), example={"entity_id": "HBM543.RSRV.265"}),
        ), smoke="entity", docs="https://smart-api.info/ui/0065e419668f3336a40d1f5ab89c6ba3",
        rate_note="no stated limit; kept at 1 req/s"),

    PublicSource(
        "hubmap_portal", "HuBMAP Data Portal (entity and organ JSON)",
        "https://portal.hubmapconsortium.org", "portal.hubmapconsortium.org",
        "HuBMAP External Data Sharing Policy (open data: permissive, e.g. CC BY 4.0)",
        "The portal's indexed record of one entity (mapped anatomy, assay modality, donor "
        "demographics, mapped metadata) and one organ's record with its UBERON term. The "
        "'.json' entity and organ URLs are documented in the portal's llms.txt; "
        "robots.txt disallows only the /search pages.", "single-cell", (
            Operation("entity", "Indexed metadata of one entity; entity_type is dataset, "
                      "sample, donor, collection or publication, uuid its 32-character "
                      "HuBMAP UUID", "browse/{entity_type}/{uuid}.json",
                      args=("entity_type", "uuid"),
                      example={"entity_type": "dataset",
                               "uuid": "3d14dcc3d7c3e0cd339c9366e34b37c7"}),
            Operation("organ", "One organ's record (name, description, UBERON term, the "
                      "labels it is searched by); organ is the portal's slug, e.g. spleen, "
                      "kidney, large-intestine", "organs/{organ}.json", args=("organ",),
                      example={"organ": "spleen"}),
        ), smoke="organ", docs="https://portal.hubmapconsortium.org/llms.txt",
        rate_note="no stated limit; kept at 1 req/s"),

    PublicSource(
        "fourdn", "4D Nucleome Data Portal (metadata)", "https://data.4dnucleome.org",
        "data.4dnucleome.org",
        "4DN data: 'External data users may freely download, analyze, and publish results "
        "based on any 4DN data provided here without restrictions' (AWS Open Data "
        "registry); cite the 4DN papers and acknowledge the generating lab",
        "3D-genome experiment sets (Hi-C, Micro-C, ChIA-PET, PLAC-seq, imaging) and "
        "processed files (loops, compartments, contact matrices) with their biosource, "
        "genome assembly and the anonymous AWS Open Data URL of each file. Documented "
        "(programmatic-access guide). Portal file downloads need an account and robots.txt "
        "disallows them and limit=all; neither is used.", "genomics", (
            Operation("experiment_set", "One replicate experiment set by accession "
                      "(4DNES...), with its experiments, biosamples and files",
                      "experiment-set-replicates/{accession}/", params={"format": "json"},
                      args=("accession",), example={"accession": "4DNESU36SE4V"}),
            Operation("processed_file", "One processed file by accession (4DNFI...): "
                      "format, size, md5, genome assembly and open_data_url",
                      "files-processed/{accession}/", params={"format": "json"},
                      args=("accession",), example={"accession": "4DNFI32J1C6W"}),
            Operation("experiment_sets_by_sample", "Released replicate experiment sets "
                      "of one biosource (e.g. HFF-hTERT, H1-hESC (Tier 1), GM12878)",
                      "search/",
                      params={"type": "ExperimentSetReplicate",
                              "experiments_in_set.biosample.biosource_summary": "{sample}",
                              "status": "released", "format": "json", "limit": "{limit}",
                              "field": _FOURDN_SET_FIELDS},
                      args=("sample",), example={"sample": "HFF-hTERT", "limit": 5}),
            Operation("processed_files_by_format", "Released processed files of one "
                      "format (bedpe loops, hic, mcool, bed, bw)", "search/",
                      params={"type": "FileProcessed", "file_format.file_format": "{file_format}",
                              "status": "released", "format": "json", "limit": "{limit}",
                              "field": _FOURDN_FILE_FIELDS},
                      args=("file_format",), example={"file_format": "bedpe", "limit": 5}),
        ), smoke="processed_file",
        docs="https://data.4dnucleome.org/help/user-guide/programmatic-access",
        rate_note="no stated limit; kept at 1 req/s; never limit=all or @@download"),

    PublicSource(
        "biosamples", "EMBL-EBI BioSamples", "https://www.ebi.ac.uk/biosamples",
        "www.ebi.ac.uk",
        "EMBL-EBI terms of use: no restriction beyond the data owners'; attribution "
        "expected (no BioSamples-specific data licence)",
        "Sample records (SAMEA/SAMN/SAMD) brokered from ENA/SRA, ArrayExpress, EGA and "
        "other archives: organism and NCBI taxon, organism part, developmental stage, "
        "free-text attributes with ontology terms, relationships between samples and "
        "links to the archive records. Search by text or by an exact attribute value "
        "(attributes are case-sensitive). Documented REST/HAL API.", "genomics", (
            Operation("sample", "One sample by accession", "samples/{accession}",
                      args=("accession",), example={"accession": "SAMEA1094826"}),
            Operation("search", "Samples matching free text (one page; size <= 200)",
                      "samples", params={"text": "{text}", "size": "{size}"},
                      args=("text",), example={"text": "Panax ginseng", "size": 2}),
            Operation("by_organism", "Samples whose organism attribute is exactly the given "
                      "name (e.g. a medicinal species)", "samples",
                      params={"filter": "attr:organism:{organism}", "size": "{size}"},
                      args=("organism",), example={"organism": "Panax ginseng", "size": 2}),
        ), smoke="sample",
        docs="https://www.ebi.ac.uk/biosamples/docs/references/api/overview",
        rate_note="look-ups only, paced by the shared www.ebi.ac.uk rate; robots.txt "
                  "Crawl-Delay 10 is for crawlers and no sweeps are made"),
)

#: Nothing defined and held back: every connector above answered.
PENDING: tuple[PublicSource, ...] = ()
