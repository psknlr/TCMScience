"""Live connectors for immune repertoires, pathogen genomes and metagenomes.

Checked from this environment on 2026-10-01 (supplementary sources, review of
2026-09-30):

* **AIRR Data Commons (ADC API v1.2)**: the iReceptor COVID-19 repository and VDJServer.
  The ADC API is a documented community standard and "currently does not define an
  authentication method"; both repositories answered unauthenticated JSON queries.
  The iReceptor Gateway (the web front end) needs a login and, since 2026-08-31, a paid
  subscription for commercial users; it is not used. Repertoire metadata and
  rearrangements are re-annotations of public AIRR-seq submissions (NCBI SRA /
  BioProject), and one study can sit in several repositories: de-duplicate by
  ``study.study_id`` and ``repertoire_id``.
* **BV-BRC Data API** (PATRIC + IRD + ViPR): documented RQL queries, no key for public
  data ("though this may change in the future"). The RQL expression is the query string
  itself, so it is written into the operation's path. The bulk mirror is FTPS only and is
  not wrapped. The web site put a Cloudflare challenge in front of its pages on
  2026-10-01; ``/api`` still answered. A challenge on ``/api`` would make every
  operation UNAVAILABLE (the HTTP backend refuses an HTML page), and it is not solved.
* **MGnify API v2** (EMBL-EBI metagenomics). v1 was deprecated in June 2026 and may be
  switched off after 2026-09-01, so only v2 is wrapped. Result files listed by the API
  live on ftp.ebi.ac.uk, whose robots.txt disallows automated agents; they are not
  fetched. www.ebi.ac.uk's robots.txt sets ``Crawl-Delay: 10``; the HTTP backend paces
  ``www.ebi.ac.uk/metagenomics/`` at one request per 10 s (``DEFAULT_RATES``), apart from
  the host's own rate for the other EBI APIs.

ImmPort's study data needs a registered account and an API key, so it is not wrapped
(its gene lists are the ``immport`` dataset). IEDB's IQ-API operations are on the
``iedb`` source in ``providers.public_apis_tcm``.
"""

from __future__ import annotations

from ..public_apis import Operation, PublicSource

#: The repertoire fields returned by the repertoire operations: enough to know what a
#: repertoire is (study, subject, diagnosis, tissue, cell subset) without its ~13 kB of
#: protocol metadata.
_REPERTOIRE_FIELDS = ["repertoire_id", "study.study_id", "study.study_title", "study.pub_ids",
                      "subject.subject_id", "subject.species", "subject.diagnosis",
                      "sample.sample_id", "sample.tissue", "sample.cell_subset",
                      "sample.pcr_target"]
#: The rearrangement fields returned: the receptor (locus, V/D/J/C calls, junction) and
#: its abundance, not the alignment (~7.5 kB per record otherwise).
_REARRANGEMENT_FIELDS = ["sequence_id", "repertoire_id", "locus", "v_call", "d_call",
                         "j_call", "c_call", "junction_aa", "productive", "duplicate_count",
                         "clone_id", "cell_id"]


def _adc_operations(study_id: str, repertoire_id: str, junction_aa: str,
                    diagnosis: str) -> tuple[Operation, ...]:
    """The ADC API operations, with examples that answered on one repository."""
    by_repertoire = {"op": "=", "content": {"field": "repertoire_id", "value": "{repertoire_id}"}}
    return (
        Operation("info", "Service, ADC API and AIRR Schema versions, and query limits",
                  "info"),
        Operation("repertoires", "A page of repertoires (study, subject, diagnosis, tissue, "
                  "cell subset)", "repertoire", method="POST",
                  json_body={"from": "{offset}", "size": "{size}",
                             "fields": _REPERTOIRE_FIELDS},
                  example={"offset": 0, "size": 2}),
        Operation("repertoires_by_study", "Repertoires of one study (study.study_id, usually "
                  "an NCBI BioProject accession)", "repertoire", method="POST",
                  json_body={"filters": {"op": "=", "content": {"field": "study.study_id",
                                                                "value": "{study_id}"}},
                             "from": 0, "size": "{size}", "fields": _REPERTOIRE_FIELDS},
                  args=("study_id",), example={"study_id": study_id, "size": 2}),
        Operation("study_counts", "Number of repertoires per study in the repository",
                  "repertoire", method="POST", json_body={"facets": "study.study_id"}),
        Operation("studies_by_diagnosis", "Repertoire counts per study for subjects whose "
                  "diagnosis label contains a text (e.g. COVID, carcinoma)", "repertoire",
                  method="POST",
                  json_body={"filters": {"op": "contains", "content": {
                      "field": "subject.diagnosis.disease_diagnosis.label",
                      "value": "{diagnosis}"}}, "facets": "study.study_id"},
                  args=("diagnosis",), example={"diagnosis": diagnosis}),
        Operation("rearrangements", "Rearrangements (V/D/J/C calls, junction, counts) of one "
                  "repertoire; filter only on ADC query fields", "rearrangement",
                  method="POST",
                  json_body={"filters": by_repertoire, "from": "{offset}", "size": "{size}",
                             "fields": _REARRANGEMENT_FIELDS},
                  args=("repertoire_id",),
                  example={"repertoire_id": repertoire_id, "offset": 0, "size": 2}),
        Operation("rearrangements_by_junction", "Rearrangements of one repertoire with a "
                  "given junction amino-acid sequence (a clonotype)", "rearrangement",
                  method="POST",
                  json_body={"filters": {"op": "and", "content": [
                      by_repertoire,
                      {"op": "=", "content": {"field": "junction_aa",
                                              "value": "{junction_aa}"}}]},
                      "from": 0, "size": "{size}", "fields": _REARRANGEMENT_FIELDS},
                  args=("repertoire_id", "junction_aa"),
                  example={"repertoire_id": repertoire_id, "junction_aa": junction_aa,
                           "size": 2}),
        Operation("rearrangement_count", "Number of rearrangements in one repertoire",
                  "rearrangement", method="POST",
                  json_body={"filters": by_repertoire, "facets": "repertoire_id"},
                  args=("repertoire_id",), example={"repertoire_id": repertoire_id}),
    )


_ADC_DOCS = "https://docs.airr-community.org/en/stable/api/adc_api_overview.html"

#: BV-BRC RQL: the expression is the query string. Field values are URL-encoded one by
#: one (a space is %20); a value holding a comma or a parenthesis cannot be sent this way.
_BVBRC_GENOME = "select(genome_id,genome_name,taxon_id,genome_status,host_name," \
                "isolation_source,isolation_country,collection_year)"

SOURCES: tuple[PublicSource, ...] = (
    PublicSource(
        "ireceptor_adc", "iReceptor AIRR COVID-19 repository (ADC API)",
        "https://covid19-1.ireceptor.org/airr/v1", "covid19-1.ireceptor.org",
        "Not stated for the data (service software GNU LGPL v3; ADC API specification CC BY "
        "4.0); iReceptor Gateway terms: commercial access is paid from 2026-08-31",
        "Adaptive immune receptor repertoires (BCR/TCR AIRR-seq) of the iReceptor AIRR "
        "COVID-19 repository: repertoire metadata (study, subject, diagnosis, tissue, cell "
        "subset) and rearrangements (V/D/J/C calls, junctions, counts) through the AIRR "
        "Data Commons API. One of several iReceptor repositories; the Gateway that "
        "federates them needs a login and is not used.", "immunology",
        _adc_operations("PRJNA628125", "5ed6859e99011334ac05e847", "CARAHQVPYDFWSGYPYYFDYW",
                        "COVID"),
        smoke="info", docs=_ADC_DOCS,
        rate_note="no stated limit; kept at 1 req/s. Commercial use: ask support@ireceptor.org"),

    PublicSource(
        "vdjserver_adc", "VDJServer Community Data Portal (ADC API)",
        "https://vdjserver.org/airr/v1", "vdjserver.org",
        "Not stated for the data (service software GNU AGPL v3; ADC API specification CC BY "
        "4.0)",
        "Adaptive immune receptor repertoires curated by VDJServer (UT Southwestern) through "
        "the AIRR Data Commons API: repertoire metadata and rearrangements. Studies can "
        "also sit in iReceptor repositories; de-duplicate by study_id.", "immunology",
        _adc_operations("PRJNA606979", "3781383765129162260-242ac117-0001-012",
                        "CASSLGQTETEAFF", "carcinoma"),
        smoke="info", docs=_ADC_DOCS,
        rate_note="max_size 1000 records per query; kept at 1 req/s"),

    PublicSource(
        "bv_brc", "BV-BRC Data API", "https://www.bv-brc.org/api", "www.bv-brc.org",
        "Not stated (cite Olson et al., Nucleic Acids Res 2023, gkac1003)",
        "Bacterial and viral genomes (PATRIC, IRD, ViPR): genome metadata, taxonomy, "
        "antimicrobial-resistance phenotypes (laboratory-measured or model-predicted: read "
        "'evidence'), specialty genes (AMR, virulence, drug target, transporter; homology "
        "calls against CARD, VFDB, DrugBank, TTD and others), protein-protein "
        "interactions (about 97% computational, mostly STRING) and epitopes (from IEDB). "
        "RQL queries; JSON.", "microbiology", (
            Operation("genome", "Genome metadata by BV-BRC genome id",
                      "genome/?eq(genome_id,{genome_id})&" + _BVBRC_GENOME + "&limit(1)",
                      args=("genome_id",), example={"genome_id": "83332.12"}),
            Operation("genomes_by_taxon", "Complete genomes under an NCBI taxon (any rank)",
                      "genome/?eq(taxon_lineage_ids,{taxon_id})&eq(genome_status,Complete)&"
                      + _BVBRC_GENOME + "&limit({limit})",
                      args=("taxon_id",), example={"taxon_id": 239934, "limit": 3}),
            Operation("taxonomy", "Taxon name, rank, lineage and genome count",
                      "taxonomy/?eq(taxon_id,{taxon_id})&select(taxon_id,taxon_name,"
                      "taxon_rank,lineage_names,genomes)&limit(1)",
                      args=("taxon_id",), example={"taxon_id": 239934}),
            Operation("genome_amr", "Antimicrobial-resistance phenotypes of a genome, "
                      "laboratory-measured and computationally predicted ('evidence')",
                      "genome_amr/?eq(genome_id,{genome_id})&select(genome_id,genome_name,"
                      "taxon_id,antibiotic,resistant_phenotype,measurement,measurement_sign,"
                      "measurement_value,measurement_unit,evidence,computational_method,"
                      "laboratory_typing_method,testing_standard,pmid)&limit({limit})",
                      args=("genome_id",), example={"genome_id": "83332.12", "limit": 5}),
            Operation("amr_lab_by_antibiotic", "Laboratory-measured resistance phenotypes "
                      "for one antibiotic, across genomes",
                      "genome_amr/?eq(antibiotic,{antibiotic})&eq(evidence,Laboratory%20Method)"
                      "&select(genome_id,genome_name,taxon_id,antibiotic,resistant_phenotype,"
                      "measurement,measurement_unit,laboratory_typing_method,testing_standard,"
                      "pmid)&limit({limit})",
                      args=("antibiotic",), example={"antibiotic": "amikacin", "limit": 5}),
            Operation("specialty_genes", "Specialty genes of a genome (property: Antibiotic "
                      "Resistance, Virulence Factor, Drug Target, Transporter, ...); homology "
                      "calls unless evidence is Literature",
                      "sp_gene/?eq(genome_id,{genome_id})&select(genome_id,patric_id,gene,"
                      "product,property,source,source_id,evidence,identity,query_coverage,"
                      "antibiotics,pmid)&limit({limit})",
                      args=("genome_id",), example={"genome_id": "83332.12", "limit": 5}),
            Operation("ppi_by_genome", "Protein-protein interactions with interactor A in a "
                      "genome; 'evidence' says experimental or computational, 'source_db' "
                      "the upstream database",
                      "ppi/?eq(genome_id_a,{genome_id})&select(interactor_a,interactor_b,"
                      "gene_a,gene_b,genome_id_a,genome_id_b,taxon_id_a,taxon_id_b,category,"
                      "evidence,source_db,detection_method,interaction_type,pmid)"
                      "&limit({limit})",
                      args=("genome_id",), example={"genome_id": "83332.12", "limit": 3}),
            Operation("epitopes_by_taxon", "Epitopes of an organism (redistributed from IEDB; "
                      "do not count twice with the iedb source)",
                      "epitope/?eq(taxon_id,{taxon_id})&select(epitope_id,epitope_sequence,"
                      "epitope_type,protein_accession,protein_name,organism,taxon_id,"
                      "host_name,assay_results)&limit({limit})",
                      args=("taxon_id",), example={"taxon_id": 11676, "limit": 3}),
            Operation("antibiotic", "Antibiotic by name with its PubChem CID, CAS number and "
                      "ATC classes",
                      "antibiotics/?eq(antibiotic_name,{name})&select(antibiotic_name,"
                      "pubchem_cid,cas_id,atc_classification)&limit(1)",
                      args=("name",), example={"name": "amikacin"}),
        ), smoke="genome", docs="https://www.bv-brc.org/api/doc/",
        rate_note="no stated limit; kept at 1 req/s; no token for public data 'though this "
                  "may change in the future'"),

    PublicSource(
        "mgnify", "MGnify API v2", "https://www.ebi.ac.uk/metagenomics/api/v2",
        "www.ebi.ac.uk", "EMBL-EBI Terms of Use (no additional restrictions beyond the "
        "original data owners')",
        "Metagenomics studies, samples and analyses analysed by EMBL-EBI's MGnify pipeline "
        "(biome, taxonomic SSU/LSU profiles, result file lists) and the MAG/isolate genome "
        "catalogues (GTDB taxonomy, completeness, contamination, cross-references to NCBI, "
        "ENA and BV-BRC genomes). Raw reads and assemblies come from ENA/INSDC.",
        "microbiome", (
            Operation("studies", "Search studies by text", "studies/",
                      params={"search": "{search}", "page_size": "{page_size}"},
                      args=("search",), example={"search": "gut", "page_size": 2}),
            Operation("studies_by_biome", "Studies under a biome lineage (e.g. "
                      "root:Host-associated:Human:Digestive system)", "studies/",
                      params={"biome_lineage": "{biome_lineage}", "page_size": "{page_size}"},
                      args=("biome_lineage",),
                      example={"biome_lineage": "root:Host-associated:Human:Digestive system",
                               "page_size": 2}),
            Operation("study", "One study (MGYS) with its biome and download list",
                      "studies/{accession}", args=("accession",),
                      example={"accession": "MGYS00006862"}),
            Operation("study_analyses", "Analyses (MGYA) of a study with each sample's biome",
                      "studies/{accession}/analyses/", params={"page_size": "{page_size}"},
                      args=("accession",), example={"accession": "MGYS00006862",
                                                    "page_size": 2}),
            Operation("study_samples", "Samples of a study", "studies/{accession}/samples/",
                      params={"page_size": "{page_size}"}, args=("accession",),
                      example={"accession": "MGYS00006862", "page_size": 2}),
            Operation("analysis", "One analysis: pipeline, sample, run/assembly and result "
                      "files", "analyses/{accession}", args=("accession",),
                      example={"accession": "MGYA00795235"}),
            Operation("analysis_annotations", "One analysis with its annotations: SSU/LSU "
                      "taxon counts (and InterPro, Pfam, GO for read analyses)",
                      "analyses/{accession}/annotations", args=("accession",),
                      example={"accession": "MGYA00795235"}),
            Operation("biomes", "Biome lineages under a biome", "biomes/",
                      params={"biome_lineage": "{biome_lineage}", "max_depth": "{max_depth}",
                              "page_size": "{page_size}"},
                      args=("biome_lineage",),
                      example={"biome_lineage": "root:Host-associated:Human", "max_depth": 4,
                               "page_size": 5}),
            Operation("genome_catalogues", "MAG/isolate genome catalogues (human gut, oral, "
                      "cow rumen, marine, ...)", "genomes/catalogues/",
                      params={"page_size": "{page_size}"}, example={"page_size": 3}),
            Operation("genomes", "Species-representative genomes whose GTDB lineage contains "
                      "a text (a substring match: filter g__<genus> downstream)", "genomes/",
                      params={"search": "{search}", "page_size": "{page_size}"},
                      args=("search",), example={"search": "Akkermansia", "page_size": 2}),
            Operation("genome", "One MGnify genome (MGYG): lineage, quality, origin, "
                      "cross-references", "genomes/{accession}", args=("accession",),
                      example={"accession": "MGYG000450016"}),
        ), smoke="study", docs="https://docs.mgnify.org/src/docs/api.html",
        rate_note="www.ebi.ac.uk robots.txt: Crawl-Delay 10; the backend paces "
                  "www.ebi.ac.uk/metagenomics/ at one request per 10 s. "
                  "API v1 is deprecated and not used."),
)

#: Nothing is pending: every source above answered every operation on 2026-10-01.
PENDING: tuple[PublicSource, ...] = ()
