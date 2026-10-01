"""Live connectors for RNA, regulation and protein-modification sources.

Checked from the harness on 2026-10-01 (review of 2026-09-30). Each answers small,
per-entity requests without a key:

* **DisProt** (``disprot``, CC BY 4.0): one curated entry of intrinsically disordered
  regions, or the entries of a UniProt accession. Documented (OpenAPI at
  ``/assets/disprot8-ws-openapi.json``); robots.txt allows ``/api``.
* **RNAcentral** (``rnacentral``, CC0): an expert-database id mapped to an RNAcentral id
  (URS), and per-URS targets, interactions, GO annotations and cross-references.
  Documented (OpenAPI at ``/api/schema/``). robots.txt disallows ``/api/v1/`` for
  crawlers with a 5 s crawl delay while the API documentation invites programmatic use,
  so the connector makes per-entity lookups only, at one request per 5 s.
* **ChIP-Atlas** (``chip_atlas``, CC BY 4.0 on the site, CC BY-SA 4.0 on the NBDC
  archive): genome list, one experiment's curated metadata, antigen counts and the index
  of precomputed target-gene tables. Documented (``/openapi.yaml`` and the ``/agents``
  page, which invites agents to call these ``/data`` endpoints although robots.txt
  disallows ``/data`` for crawlers with a 30 s crawl delay); one request per 30 s. Bulk
  and per-TF files come from chip-atlas.dbcls.jp (``tcmdb.extra.rna_reg``).
* **ReMap 2022** (``remap``, CC BY-NC 4.0): the ChIP-seq datasets of one transcriptional
  regulator or one cell type (biotype). The REST page embeds its Swagger UI from port 98,
  which this environment cannot reach; the dataset endpoints answer on 443.

iPTMnet's documented API (``PENDING``) answered HTTP 503 on every call on 2026-10-01; its
bulk files are a snapshot dataset instead. miRTarBase has no API.
"""

from __future__ import annotations

from ..public_apis import Operation, PublicSource

__all__ = ["SOURCES", "PENDING"]

_J = {"format": "json"}

SOURCES: tuple[PublicSource, ...] = (
    PublicSource(
        "disprot", "DisProt REST API", "https://disprot.org/api", "disprot.org",
        "CC BY 4.0",
        "Manually curated intrinsically disordered regions: per region the residues, the "
        "IDPO disorder state or GO function term, the ECO evidence code, the paper and the "
        "interaction partners.", "proteomics", (
            Operation("entry", "One DisProt entry with all its curated regions",
                      "{disprot_id}", args=("disprot_id",),
                      example={"disprot_id": "DP00086"}),
            Operation("search_by_accession", "DisProt entries of a UniProt accession",
                      "search", params={"acc": "{accession}", "page_size": "{page_size}"},
                      args=("accession",), example={"accession": "P04637", "page_size": 5}),
        ), smoke="entry", docs="https://disprot.org/api",
        rate_note="no stated limit; kept at 1 req/s"),

    PublicSource(
        "rnacentral", "RNAcentral REST API", "https://rnacentral.org/api/v1", "rnacentral.org",
        "CC0 1.0 (data from release 20 on; earlier data under EMBL-EBI terms of use)",
        "Non-coding RNA sequences (URS ids) from 59 expert databases: id mapping, and per "
        "URS and species the TarBase/LncBase targets, IntAct interactions, GO annotations "
        "and cross-references.", "genomics", (
            Operation("rna_by_external_id", "RNAcentral entries for an expert-database id "
                      "(e.g. a miRBase MIMAT accession)", "rna/",
                      params={**_J, "external_id": "{external_id}"}, args=("external_id",),
                      example={"external_id": "MIMAT0000062"}),
            Operation("rna", "One RNAcentral sequence by URS id", "rna/{urs}/",
                      params=_J, args=("urs",), example={"urs": "URS0000416056"}),
            Operation("protein_targets", "Target genes of an ncRNA in one species "
                      "(TarBase/LncBase records, with the experimental methods)",
                      "rna/{urs}/protein-targets/{taxid}/",
                      params={**_J, "page": "{page}", "page_size": "{page_size}"},
                      args=("urs", "taxid"),
                      example={"urs": "URS0000416056", "taxid": 9606, "page": 1,
                               "page_size": 25}),
            Operation("interactions", "Molecular interactions of an ncRNA in one species "
                      "(from IntAct)", "rna/{urs}/interactions/{taxid}/",
                      params={**_J, "page": "{page}"}, args=("urs", "taxid"),
                      example={"urs": "URS0000416056", "taxid": 9606, "page": 1}),
            Operation("go_annotations", "GO annotations of an ncRNA in one species, with "
                      "ECO evidence codes and the assigning group",
                      "rna/{urs}/go-annotations/{taxid}/", params=_J, args=("urs", "taxid"),
                      example={"urs": "URS0000416056", "taxid": 9606}),
            Operation("xrefs", "Cross-references of an ncRNA in one species",
                      "rna/{urs}/xrefs/{taxid}/",
                      params={**_J, "page_size": "{page_size}"}, args=("urs", "taxid"),
                      example={"urs": "URS0000416056", "taxid": 9606, "page_size": 10}),
        ), smoke="rna_by_external_id", docs="https://rnacentral.org/api",
        rate_note="robots.txt Crawl-delay 5 s (documented throttle 20 req/s); kept at 1 "
                  "request per 5 s"),

    PublicSource(
        "chip_atlas", "ChIP-Atlas HTTP API", "https://chip-atlas.org/data", "chip-atlas.org",
        "CC BY 4.0 (site and OpenAPI; the NBDC archive page states CC BY-SA 4.0)",
        "Uniformly reprocessed public ChIP-seq, ATAC-seq, DNase-seq and Bisulfite-seq "
        "experiments: genomes, one experiment's curated antigen and cell type, antigen "
        "counts, and which antigens have precomputed target-gene tables.", "genomics", (
            Operation("genomes", "Genome assemblies in ChIP-Atlas", "list_of_genome.json"),
            Operation("experiment", "Curated metadata of one experiment (SRX/ERX/DRX or "
                      "GSM), one record per genome assembly", "exp_metadata.json",
                      params={"expid": "{expid}"}, args=("expid",),
                      example={"expid": "SRX018625"}),
            Operation("antigens", "Antigens of an experiment class with experiment counts, "
                      "for a genome and cell type class", "chip_antigen",
                      params={"genome": "{genome}", "agClass": "{ag_class}",
                              "clClass": "{cl_class}"}, args=("genome", "ag_class"),
                      example={"genome": "hg38", "ag_class": "TFs and others",
                               "cl_class": "All cell types"}),
            Operation("target_genes_index", "Antigens with precomputed Target Genes tables, "
                      "per genome", "target_genes_analysis.json"),
        ), smoke="genomes", docs="https://chip-atlas.org/agents",
        rate_note="robots.txt Crawl-delay 30 s; kept at 1 request per 30 s"),

    PublicSource(
        "remap", "ReMap 2022 REST API", "https://remap.univ-amu.fr/api/v1",
        "remap.univ-amu.fr", "CC BY-NC 4.0 (ReMap catalogues)",
        "ReMap 2022 ChIP-seq/ChIP-exo/DAP-seq datasets of a transcriptional regulator or "
        "of a cell type (biotype): dataset name (GEO/ENCODE series), biotype, "
        "modifications and the BED URL. The REST page's Swagger UI (port 98) was not "
        "reachable from this environment; the dataset endpoints answer on 443.",
        "genomics", (
            Operation("datasets_by_target", "ChIP-seq datasets of one regulator in one "
                      "species", "datasets/findByTarget/target={target}&taxid={taxid}",
                      args=("target", "taxid"), example={"target": "FOXA1", "taxid": 9606}),
            Operation("datasets_by_biotype", "ChIP-seq datasets of one cell type in one "
                      "species", "datasets/findByBiotype/biotype={biotype}&taxid={taxid}",
                      args=("biotype", "taxid"),
                      example={"biotype": "MCF-7", "taxid": 9606}),
        ), smoke="datasets_by_target", docs="https://remap.univ-amu.fr/rest_page",
        rate_note="no stated limit; kept at 1 req/s"),
)

#: Defined but not shipped.
PENDING: tuple[PublicSource, ...] = (
    # iPTMnet's documented Swagger 2.0 API (v2.1.1) returned HTTP 503 Service Unavailable
    # for every call on 2026-10-01 (21:00 and 21:45 UTC: /v1/P04637/info, /v1/{id}/
    # substrate, /stats), while its spec and the site's pages answered. Ship it once
    # ``scripts/verify_connectors.py --only iptmnet`` succeeds.
    PublicSource(
        "iptmnet", "iPTMnet REST API", "https://research.bioinformatics.udel.edu/iptmnet/api",
        "research.bioinformatics.udel.edu",
        "CC BY-NC-SA 4.0 (licence page; the API spec says CC BY-NC-ND 4.0)",
        "Post-translational modifications from curated databases and text mining: a "
        "protein's PTM sites with their enzymes, sources and papers.", "proteomics", (
            Operation("info", "Top-level information on a protein", "v1/{id}/info",
                      args=("id",), example={"id": "P04637"}),
            Operation("substrate", "PTM sites of a substrate with enzymes and sources",
                      "v1/{id}/substrate", args=("id",), example={"id": "P04637"}),
            Operation("ptm_ppi", "PTM-dependent protein interactions of a protein",
                      "v1/{id}/ptmppi", args=("id",), example={"id": "P04637"}),
        ), smoke="info", docs="https://research.bioinformatics.udel.edu/iptmnet/api/doc/",
        rate_note="no stated limit; kept at 1 req/s"),
)
