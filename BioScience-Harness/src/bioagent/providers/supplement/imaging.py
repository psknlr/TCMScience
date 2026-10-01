"""Imaging archives: metadata look-ups, never image downloads.

Four archives answer unauthenticated metadata queries; each was checked from the harness
on 2026-10-01 (review of 2026-09-30):

* **NCI Imaging Data Commons** (``idc``): the v3 REST API (documented, OpenAPI 3.1, labelled
  beta 3.0.0b4; v1 and v2 answer 410 Gone). Collections, analysis results, licence
  breakdowns and read-only SQL over the idc-index tables. The versioned index is also a
  snapshot dataset (``tcmdb`` dataset ``idc``).
* **The Cancer Imaging Archive**: two hosts, so two connectors. ``tcia`` is the NBIA
  REST API v4 (patients, studies, series and their per-series licence). TCIA calls NBIA
  its legacy system, retired in favour of IDC for public DICOM, and ends help-desk
  support for the pre-v4 APIs in October 2026, so only v4 is wrapped. ``tcia_cm`` is the
  Collection Manager API (collections with cancer types and DOIs, downloads with their
  licence and access level). Collections that need a data use agreement answer empty
  without a login, and no login is attempted.
* **Image Data Resource** (``idr``): the OMERO JSON API, the study annotations of the
  web client and the IDR searcher (which screens imaged a gene or a compound). The
  per-study exports are a snapshot dataset (``idr``).
* **OpenNeuro** (``openneuro``): the GraphQL API (dataset, snapshot description and
  licence, file listing).

The BioImage Archive is reached through the existing ``biostudies`` connector (same host
and API: ``bioimages_search``, ``study_info``), not as a source of its own.

Licences are per collection, series, study or dataset in all of them, and several are
non-commercial (CC BY-NC, NC-SA, NC-ND); each operation that returns records returns
their licence field, which is the one to filter on. IDC redistributes most public TCIA
collections (the same SeriesInstanceUID in both), so a series is counted once.
"""

from __future__ import annotations

from ..public_apis import Operation, PublicSource

_TCIA_POLICY = ("TCIA Data Usage Policy "
                "(https://www.cancerimagingarchive.net/data-usage-policies-and-restrictions/): "
                "cite each dataset's DOI, no re-identification")

#: Fields of a TCIA collection record worth returning (the full record is ~6 KB of HTML).
_TCIA_COLLECTION_FIELDS = ("id,slug,collection_doi,collection_short_title,collection_title,"
                           "cancer_types,cancer_locations,species,data_types,subjects,"
                           "collection_downloads,version_number,date_updated")
_TCIA_DOWNLOAD_FIELDS = ("id,slug,data_license,download_access,download_type,"
                         "download_requirements,file_type,data_type,subjects,image_count,"
                         "download_size,download_size_unit,collection_status,date_updated")

_OPENNEURO_DATASET = (
    "query($id: ID!){ dataset(id: $id) { id name created public latestSnapshot { tag "
    "created description { Name License Authors DatasetDOI BIDSVersion ReferencesAndLinks "
    "Funding } summary { modalities subjects sessions tasks size totalFiles } } } }")
_OPENNEURO_DATASETS = (
    "query($first: Int, $after: String){ datasets(first: $first, after: $after, "
    "orderBy: {created: descending}) { edges { node { id created latestSnapshot { tag "
    "description { Name License DatasetDOI } summary { modalities subjects tasks } } } } "
    "pageInfo { hasNextPage endCursor count } } }")
_OPENNEURO_FILES = (
    "query($id: ID!, $tag: String!){ snapshot(datasetId: $id, tag: $tag) { id tag "
    "files { filename size directory urls } } }")

SOURCES: tuple[PublicSource, ...] = (
    PublicSource(
        "idc", "NCI Imaging Data Commons API v3", "https://api.imaging.datacommons.cancer.gov/v3",
        "api.imaging.datacommons.cancer.gov",
        "Metadata: MIT (idc-index); each DICOM series has its own licence "
        "(license_short_name: CC BY 4.0, CC BY 3.0, CC BY-NC 4.0, CC BY-NC 3.0, National "
        "Library of Medicine Terms and Conditions)",
        "Cancer imaging collections (radiology and slide microscopy, mostly from TCIA, "
        "GDC/TCGA, CPTAC and HTAN), their cancer types, sites, modalities, patients, "
        "series counts and per-series licences; analysis results (segmentations, "
        "annotations); read-only DuckDB SQL over the idc-index tables. API labelled beta "
        "(3.0.0b4); v1 and v2 are retired (410).", "imaging", (
            Operation("version", "IDC data release and API version", "version"),
            Operation("stats", "Headline totals: collections, patients, series, size",
                      "stats"),
            Operation("collections", "Every collection with cancer types, tumour "
                      "locations, species, subject count and description", "collections"),
            Operation("collection", "One collection with modalities, counts and its "
                      "licence breakdown (series per licence)",
                      "collections/{collection_id}", args=("collection_id",),
                      example={"collection_id": "tcga_luad"}),
            Operation("analysis_results", "Analysis results (segmentations, annotations, "
                      "AI outputs) with their source collections, DOI and licence",
                      "analysis_results"),
            Operation("attribute_values", "Distinct values of a filter attribute "
                      "(Modality, BodyPartExamined, collection_id, ...) with counts",
                      "attributes/{attribute}/values", params={"limit": "{limit}"},
                      args=("attribute",), example={"attribute": "Modality", "limit": 50}),
            Operation("tables", "Queryable index tables", "tables"),
            Operation("table", "Columns of one index table", "tables/{table}",
                      args=("table",), example={"table": "index"}),
            Operation("clinical_tables", "Clinical tables of a collection (joinable to "
                      "the index on PatientID)", "clinical/tables",
                      params={"collection_id": "{collection_id}"}, args=("collection_id",),
                      example={"collection_id": "tcga_luad"}),
            Operation("licenses", "Licence breakdown (series and size per licence) of a "
                      "collection; check it before reuse", "licenses", method="POST",
                      json_body={"filters": {"terms": {"collection_id": ["{collection_id}"]}}},
                      args=("collection_id",), example={"collection_id": "nsclc_radiomics"}),
            Operation("cohort_counts", "Patients, studies, series and size of a "
                      "collection and modality", "cohort/counts", method="POST",
                      json_body={"filters": {"terms": {"collection_id": ["{collection_id}"],
                                                       "Modality": ["{modality}"]}}},
                      args=("collection_id", "modality"),
                      example={"collection_id": "tcga_luad", "modality": "CT"}),
            Operation("sql", "One read-only SELECT/WITH over the idc-index tables "
                      "(index, collections_index, seg_index, sm_index, clinical.*); "
                      "max_rows caps the answer", "sql", method="POST",
                      json_body={"sql": "{sql}", "max_rows": "{max_rows}"}, args=("sql",),
                      example={"sql": "SELECT collection_id, license_short_name, count(*) "
                                      "AS n_series FROM index WHERE collection_id = "
                                      "'nsclc_radiomics' GROUP BY 1, 2 ORDER BY 2",
                               "max_rows": 20}),
        ), smoke="collection", docs="https://learn.canceridc.dev/rest-api/endpoint-details",
        rate_note="no stated limit; kept at 1 req/s"),

    PublicSource(
        "tcia", "TCIA NBIA REST API v4",
        "https://services.cancerimagingarchive.net/nbia-api/services/v4",
        "services.cancerimagingarchive.net",
        "Per series (LicenseName/LicenseURI): mostly CC BY 3.0 / CC BY 4.0, some CC BY-NC; "
        + _TCIA_POLICY,
        "Public TCIA collections, their patients, studies and series (modality, body part, "
        "manufacturer, image count, licence). NBIA is TCIA's legacy DICOM system, being "
        "retired in favour of IDC; only v4 is wrapped (pre-v4 support ends October 2026). "
        "Collections that need a data use agreement answer empty without a login.",
        "imaging", (
            Operation("collections", "Names of the public collections",
                      "getCollectionValues"),
            Operation("patients", "Patients of a collection (sex, species, phantom)",
                      "getPatient", params={"Collection": "{collection}"},
                      args=("collection",), example={"collection": "TCGA-LUAD"}),
            Operation("patient_studies", "Studies of a patient (date, age, series count)",
                      "getPatientStudy",
                      params={"Collection": "{collection}", "PatientID": "{patient_id}"},
                      args=("collection", "patient_id"),
                      example={"collection": "TCGA-LUAD", "patient_id": "TCGA-17-Z062"}),
            Operation("series", "Series of a patient with modality, body part, image "
                      "count and licence", "getSeries",
                      params={"Collection": "{collection}", "PatientID": "{patient_id}"},
                      args=("collection", "patient_id"),
                      example={"collection": "TCGA-LUAD", "patient_id": "TCGA-17-Z062"}),
            Operation("modalities", "Modalities of a collection", "getModalityValues",
                      params={"Collection": "{collection}"}, args=("collection",),
                      example={"collection": "TCGA-LUAD"}),
            Operation("body_parts", "Body parts examined in a collection",
                      "getBodyPartValues", params={"Collection": "{collection}"},
                      args=("collection",), example={"collection": "TCGA-LUAD"}),
            Operation("series_size", "Uncompressed size and object count of a series",
                      "getSeriesSize", params={"SeriesInstanceUID": "{series_uid}"},
                      args=("series_uid",),
                      example={"series_uid": "1.3.6.1.4.1.14519.5.2.1.7777.9002."
                                             "200196598119445662658463835458"}),
        ), smoke="modalities", docs="https://www.cancerimagingarchive.net/tcia-api-guides/",
        rate_note="slow service (several seconds per call); kept at 1 req/s"),

    PublicSource(
        "tcia_cm", "TCIA Collection Manager API", "https://www.cancerimagingarchive.net/api",
        "www.cancerimagingarchive.net",
        "Per download item (data_license): CC BY 4.0, CC BY 3.0, CC BY-NC 4.0, CC BY-NC "
        "3.0, TCIA Restricted/Limited, NCTN/NCORP, NIH Controlled Data Access; "
        + _TCIA_POLICY,
        "TCIA collections with cancer types and locations, species, data types, subject "
        "counts and DOIs, and their download items with licence and access level (Public "
        "or Limited). v2 for search and lists; the record look-ups use v1, whose filters "
        "v2 does not apply.", "imaging", (
            Operation("collections_search", "Collections matching a text (title, short "
                      "title, summary, program; case-insensitive)", "v2/collections/",
                      params={"search": "{text}", "per_page": "{per_page}",
                              "page": "{page}", "fields": _TCIA_COLLECTION_FIELDS},
                      args=("text",), example={"text": "lung", "per_page": 5, "page": 1}),
            Operation("collection", "One collection by slug, with the ids of its download "
                      "items", "v1/collections/",
                      params={"slug": "{slug}", "_fields": _TCIA_COLLECTION_FIELDS},
                      args=("slug",), example={"slug": "4d-lung"}),
            Operation("download", "One download item: licence, access (Public/Limited), "
                      "requirements, file and data types, size",
                      "v1/downloads/{download_id}", params={"_fields": _TCIA_DOWNLOAD_FIELDS},
                      args=("download_id",), example={"download_id": 42107}),
            Operation("licenses", "The licences TCIA download items carry",
                      "v2/licenses/"),
        ), smoke="collection",
        docs="https://www.cancerimagingarchive.net/collection-manager-rest-api/",
        rate_note="no stated limit; kept at 1 req/s"),

    PublicSource(
        "idr", "Image Data Resource (OMERO JSON API and IDR searcher)",
        "https://idr.openmicroscopy.org", "idr.openmicroscopy.org",
        "Per study ('License' in the study annotation): mostly CC BY 4.0 or CC0; some CC "
        "BY-NC-SA 3.0, CC BY-NC 4.0, CC BY-NC-ND 4.0, CC BY-SA 3.0",
        "Cell and tissue imaging studies (high-content RNAi, CRISPR and compound screens, "
        "localisation atlases) with curated key-values: genes, siRNAs, compounds (PubChem, "
        "InChIKey, SMILES, MoA), CMPO phenotypes, cell lines. Which studies imaged a gene "
        "or a compound, how many images per phenotype, and each study's licence.",
        "imaging", (
            Operation("screens", "Screens (OMERO JSON API)", "api/v0/m/screens/",
                      params={"limit": "{limit}", "offset": "{offset}"},
                      example={"limit": 5, "offset": 0}),
            Operation("projects", "Projects (OMERO JSON API)", "api/v0/m/projects/",
                      params={"limit": "{limit}", "offset": "{offset}"},
                      example={"limit": 5, "offset": 0}),
            Operation("screen_study", "Study annotation of a screen: organism, study and "
                      "screen type, PubMed id, licence", "webclient/api/annotations/",
                      params={"type": "map", "ns": "idr.openmicroscopy.org/study/info",
                              "screen": "{screen_id}"},
                      args=("screen_id",), example={"screen_id": 1101}),
            Operation("project_study", "Study annotation of a project",
                      "webclient/api/annotations/",
                      params={"type": "map", "ns": "idr.openmicroscopy.org/study/info",
                              "project": "{project_id}"},
                      args=("project_id",), example={"project_id": 51}),
            Operation("containers", "Every screen and project with its image count "
                      "(IDR searcher)", "searchengine/api/v1/resources/container_images/",
                      params={"data_source": "idr"}),
            Operation("gene_studies", "Screens and projects with images annotated with a "
                      "gene symbol (IDR searcher)",
                      "searchengine/api/v1/resources/image/search/",
                      params={"key": "Gene Symbol", "value": "{symbol}",
                              "return_containers": "true", "case_sensitive": "false",
                              "data_source": "idr"},
                      args=("symbol",), example={"symbol": "CDC20"}),
            Operation("compound_studies", "Screens and projects with images of a compound "
                      "(IDR searcher)", "searchengine/api/v1/resources/image/search/",
                      params={"key": "Compound Name", "value": "{compound}",
                              "return_containers": "true", "case_sensitive": "false",
                              "data_source": "idr"},
                      args=("compound",), example={"compound": "quercetin"}),
            Operation("search_studies", "Screens and projects whose images carry a key-value "
                      "(e.g. 'Phenotype Term Accession' = 'CMPO_0000077', 'InChIKey', "
                      "'Cell Line')", "searchengine/api/v1/resources/image/search/",
                      params={"key": "{key}", "value": "{value}",
                              "return_containers": "true", "case_sensitive": "false",
                              "data_source": "idr"},
                      args=("key", "value"),
                      example={"key": "Cell Line", "value": "HeLa"}),
            Operation("container_values", "Values of one key in a study, with image counts "
                      "(e.g. its phenotypes)",
                      "searchengine/api/v1/resources/image/container_keyvalues/",
                      params={"container_name": "{container}", "key": "{key}",
                              "data_source": "idr"},
                      args=("container", "key"),
                      example={"container": "idr0013-neumann-mitocheck/screenA",
                               "key": "Phenotype Term Name"}),
        ), smoke="screens", docs="https://idr.openmicroscopy.org/about/api.html",
        rate_note="no stated limit; kept at 1 req/s"),

    PublicSource(
        "openneuro", "OpenNeuro GraphQL API", "https://openneuro.org/crn/graphql",
        "openneuro.org",
        "Per dataset (description.License): CC0 for about 93%; PDDL for legacy OpenfMRI "
        "imports; a few CC BY, CC BY-SA, CC BY-NC; some unstated",
        "Neuroimaging datasets (MRI, EEG, MEG, iEEG, PET) in BIDS: name, authors, DOI, "
        "licence, modalities, tasks, subjects, size and the snapshot's file listing. "
        "Metadata only; files are not fetched.", "imaging", (
            Operation("dataset", "A dataset and its latest snapshot: description, licence, "
                      "DOI, modalities, tasks, subjects, size", "", method="POST",
                      graphql=_OPENNEURO_DATASET, variables={"id": "{dataset_id}"},
                      args=("dataset_id",), example={"dataset_id": "ds000001"}),
            Operation("datasets", "Datasets, newest first, a page at a time (pass "
                      "pageInfo.endCursor as after)", "", method="POST",
                      graphql=_OPENNEURO_DATASETS,
                      variables={"first": "{first}", "after": "{after}"},
                      example={"first": 5, "after": None}),
            Operation("snapshot_files", "Top-level files of a dataset snapshot with sizes",
                      "", method="POST", graphql=_OPENNEURO_FILES,
                      variables={"id": "{dataset_id}", "tag": "{tag}"},
                      args=("dataset_id", "tag"),
                      example={"dataset_id": "ds000001", "tag": "1.0.0"}),
        ), smoke="dataset", docs="https://docs.openneuro.org/api.html",
        rate_note="no stated limit; kept at 1 req/s"),
)
