"""Live connectors for reference spectra and metabolomics: MassBank, GNPS, MassIVE and the
Metabolomics Workbench.

Checked from this environment on 2026-10-01; every operation below answered live
(``scripts/verify_connectors.py --only massbank gnps gnps_usi gnps_explorer massive
metabolomics_workbench``).

* **MassBank** (``massbank.eu``): the documented MassBank3 REST API
  (``/MassBank-api``, OpenAPI at ``/MassBank-api/ui/openapi.yaml``) and its export
  service (``/MassBank-export``, ``/v3/api-docs``). The live instance served data version
  2025.10 on 2026-10-01 while the newest data release is 2026.03 (the snapshot dataset
  ``massbank`` reads that release). Every record carries its own licence (CC0 to
  CC BY-NC-SA, and dl-de/by-2-0); a caller keeps the ``license`` of each record it uses.
  ``massbank.eu/robots.txt`` is absent and ``/MassBank/robots.txt`` allows everything.
* **GNPS** (``external.gnps2.org``, ``metabolomics-usi.gnps2.org``,
  ``explorer.gnps2.org``): the endpoints listed in the GNPS and GNPS2 API documentation:
  the library list, one library spectrum with its annotation history, a spectrum's peaks
  by USI, and a public dataset's file list. Several answer JSON labelled ``text/html``;
  the transport parses the body. No robots.txt on these hosts.
* **MassIVE** (``massive.ucsd.edu``): the PROXI v0.1 dataset search and dataset record
  (a HUPO-PSI standard API). robots.txt disallows only ``/ProteoSAFe/DownloadResultFile``.
* **Metabolomics Workbench** (``www.metabolomicsworkbench.org/rest``): the documented REST
  API (v1.2), which its documentation offers for "3rd party applications or scripts to
  enable programmatic access". The site's robots.txt is ``Disallow: /`` for every agent,
  so only per-entity look-ups are wrapped (one RefMet name, one compound, one study, one
  MetStat query) at one request per second. The whole-table dumps
  (``/refmet/all``, ``/refmet/all_ids``, ``/study/study_id/ST/...``) are not wrapped and
  must not be called. Errors come back as HTTP 200 with an HTML page, which the transport
  reports as a failure.
"""

from __future__ import annotations

from ..public_apis import Operation, PublicSource

_MB = "MassBank-api/"
_MB_LICENSE = ("Per record: CC0, CC BY, CC BY-SA, CC BY-NC, CC BY-NC-SA, CC BY-NC-ND or "
               "dl-de/by-2-0 (each record's 'license' field is binding; NC records are "
               "not for commercial use)")
_GNPS_LICENSE = ("CC0 for spectra contributed directly to GNPS (documentation, 'License'); "
                 "imported third-party libraries (MASSBANK, MASSBANKEU, MONA, HMDB, "
                 "RESPECT, ...) keep their upstream licences")
_MW_LICENSE = ("Site terms of use: personal, non-commercial use of the content; public "
               "study data are in the public domain after their embargo (data-sharing "
               "FAQ), and each study states its own licence (e.g. CC BY 4.0); RefMet has "
               "no licence of its own")

SOURCES: tuple[PublicSource, ...] = (
    PublicSource(
        "massbank", "MassBank3 REST API and export service", "https://massbank.eu",
        "massbank.eu", _MB_LICENSE,
        "Reference MS/MS (and EI) spectra of compounds: record accessions by InChIKey, "
        "name, formula, mass or peak list; full records with peaks, instrument, ion mode "
        "and the record's licence; native record text and NIST/RIKEN MSP conversion. "
        "Documented OpenAPI. The live database lags the newest data release (data "
        "version 2025.10 served on 2026-10-01; release 2026.03 exists).", "spectra", (
            Operation("records_search", "Accessions of the records for one InChIKey "
                      "(with atom counts)", _MB + "records/search",
                      params={"inchi_key": "{inchikey}"}, args=("inchikey",),
                      example={"inchikey": "IKGXIBQEEMLURG-NVPNHPEKSA-N"}),
            Operation("records_search_name", "Accessions by compound name. The name match "
                      "is broad (substring-like: 'Rutin' also finds 'Rutine'); prefer the "
                      "InChIKey search", _MB + "records/search",
                      params={"compound_name": "{name}"}, args=("name",),
                      example={"name": "Mellein"}),
            Operation("records", "Full records (peaks, instrument, ion mode, licence, "
                      "links) for one InChIKey", _MB + "records",
                      params={"inchi_key": "{inchikey}"}, args=("inchikey",),
                      example={"inchikey": "KWILGNNWGSNMPA-UHFFFAOYSA-N"}),
            Operation("record", "One full record by accession", _MB + "records/{accession}",
                      args=("accession",), example={"accession": "MSBNK-BS-BS003074"}),
            Operation("record_simple", "One record reduced to accession, title, peaks and "
                      "SMILES", _MB + "records/{accession}/simple", args=("accession",),
                      example={"accession": "MSBNK-BS-BS003074"}),
            Operation("peak_search", "Records whose spectrum resembles a peak list "
                      "('mz;intensity,mz;intensity'; cosine score, threshold 0-1)",
                      _MB + "records/search",
                      params={"peak_list": "{peak_list}",
                              "peak_list_threshold": "{threshold}"},
                      args=("peak_list",),
                      example={"peak_list": "133.0648;225,151.0754;94,155.9743;112,"
                                            "161.0597;999,179.0703;750",
                               "threshold": "0.8"}),
            Operation("count", "Number of records in the live database",
                      _MB + "records/count"),
            Operation("metadata", "Data version, timestamp, spectrum and compound counts "
                      "and compound classes of the live database (about 210 KB)",
                      _MB + "metadata"),
            Operation("browse_options", "Contributors, instrument types, MS types and ion "
                      "modes with record counts", _MB + "filter/browse"),
            Operation("status", "Health and versions of the API, database, export and "
                      "similarity services", _MB + "status"),
            Operation("record_text", "The record in MassBank's native text format "
                      "(including its LICENSE line)", "MassBank-export/rawtext/{accession}",
                      accept="text/plain", args=("accession",),
                      example={"accession": "MSBNK-BS-BS003074"}),
            Operation("convert", "Convert listed records to nist_msp, riken_msp, massbank "
                      "or json", "MassBank-export/convert", method="POST",
                      json_body={"record_list": "{accessions}", "format": "{format}"},
                      accept="text/plain", args=("accessions",),
                      example={"accessions": ["MSBNK-BS-BS003074", "MSBNK-BS-BS003075"],
                               "format": "nist_msp"}),
            Operation("record_jsonld", "Bioschemas JSON-LD metadata of one record (licence "
                      "URL, citation, measurement technique)",
                      "MassBank-export/metadata/{accession}", accept="application/ld+json",
                      args=("accession",),
                      example={"accession": "MSBNK-BS-BS003074"}),
        ), smoke="records_search",
        docs="https://massbank.eu/MassBank-api/ui/ ; "
             "https://massbank.eu/MassBank-export/v3/api-docs",
        rate_note="no stated limit; kept at 1 req/s"),

    PublicSource(
        "gnps", "GNPS public spectral libraries (GNPS2 external)", "https://external.gnps2.org",
        "external.gnps2.org", _GNPS_LICENSE,
        "The list of public GNPS spectral libraries and one library spectrum by its "
        "CCMSLIB id, with its annotation history (compound, SMILES/InChI, adduct, "
        "instrument, library quality class 1 gold / 2 silver / 3 bronze) and peaks. "
        "Documented in the GNPS and GNPS2 API pages; the JSON is served as text/html.",
        "spectra", (
            Operation("libraries", "Names and types (GNPS, GNPS-PROPOGATED, IMPORT) of the "
                      "public libraries", "gnpslibrary.json"),
            Operation("spectrum", "One library spectrum with its annotations and peaks",
                      "gnpsspectrum", params={"SpectrumID": "{spectrum_id}"},
                      args=("spectrum_id",), example={"spectrum_id": "CCMSLIB00000001547"}),
        ), smoke="spectrum",
        docs="https://ccms-ucsd.github.io/GNPSDocumentation/api/ ; "
             "https://wang-bioinformatics-lab.github.io/GNPS2_Documentation/api/",
        rate_note="no stated limit; kept at 1 req/s"),

    PublicSource(
        "gnps_usi", "Metabolomics USI resolver (GNPS2)", "https://metabolomics-usi.gnps2.org",
        "metabolomics-usi.gnps2.org", _GNPS_LICENSE,
        "Peaks, precursor m/z, charge and SPLASH of any spectrum named by a Universal "
        "Spectrum Identifier (GNPS library, MassBank, MassIVE/MetaboLights files). "
        "Documented in the GNPS API page.", "spectra", (
            Operation("spectrum", "A spectrum's peaks by USI", "json/",
                      params={"usi1": "{usi}"}, args=("usi",),
                      example={"usi": "mzspec:GNPS:GNPS-LIBRARY:accession:CCMSLIB00000001547"}),
        ), smoke="spectrum",
        docs="https://ccms-ucsd.github.io/GNPSDocumentation/api/",
        rate_note="no stated limit; kept at 1 req/s"),

    PublicSource(
        "gnps_explorer", "GNPS2 dataset explorer", "https://explorer.gnps2.org",
        "explorer.gnps2.org", "Per dataset (MassIVE states each dataset's licence, e.g. "
        "CC0 1.0)",
        "The file list of one public MassIVE/GNPS dataset (file name, collection, size, "
        "MS2 count, vendor and model). Documented in the GNPS2 API page.", "spectra", (
            Operation("dataset_files", "Files of one public dataset",
                      "api/datasets/{accession}/files", args=("accession",),
                      example={"accession": "MSV000084794"}),
        ), smoke="dataset_files",
        docs="https://wang-bioinformatics-lab.github.io/GNPS2_Documentation/api/",
        rate_note="no stated limit; kept at 1 req/s"),

    PublicSource(
        "massive", "MassIVE PROXI dataset API", "https://massive.ucsd.edu/ProteoSAFe/proxi/v0.1",
        "massive.ucsd.edu", "Per dataset (each dataset page states its licence, e.g. "
        "CC0 1.0)",
        "Public mass-spectrometry datasets (GNPS/MassIVE MSV accessions): full-text search "
        "and one dataset's title, species (NCBI taxon), instruments, keywords, contacts "
        "and publications. PROXI v0.1 (HUPO-PSI). Responses are ISO-8859-1.", "spectra", (
            Operation("dataset_search", "Datasets matching a text (title, keywords, "
                      "species), one page", "datasets",
                      params={"resultType": "compact", "pageSize": "{page_size}",
                              "pageNumber": "{page}", "search": "{text}"},
                      args=("text",), example={"text": "Ginkgo", "page_size": 5, "page": 1}),
            Operation("dataset", "One dataset by MSV accession", "datasets/{accession}",
                      args=("accession",), example={"accession": "MSV000084794"}),
        ), smoke="dataset",
        docs="https://github.com/HUPO-PSI/proxi-schemas",
        rate_note="no stated limit; kept at 1 req/s"),

    PublicSource(
        "metabolomics_workbench", "Metabolomics Workbench REST API (NMDR, RefMet)",
        "https://www.metabolomicsworkbench.org/rest", "www.metabolomicsworkbench.org",
        _MW_LICENSE,
        "RefMet metabolite nomenclature (standardise a name, a RefMet record by name or "
        "formula), the compound database's cross-references (PubChem, HMDB, KEGG, ChEBI, "
        "LIPID MAPS) by InChIKey or PubChem CID, one study's summary (with its licence), "
        "analyses and named metabolites, studies reporting a RefMet metabolite and MetStat "
        "context searches. Documented REST API v1.2. "
        "robots.txt disallows the whole site; the documentation offers this API for "
        "programmatic use, so only per-entity look-ups are wrapped, never the whole-table "
        "dumps. A study id must be a full id (ST000001): 'ST' alone returns every study.",
        "metabolomics", (
            Operation("refmet_match", "Standardise a free-text metabolite name to RefMet",
                      "refmet/match/{name}/name/", args=("name",),
                      example={"name": "citrate"}),
            Operation("refmet", "One RefMet record (InChIKey, PubChem CID, formula, mass, "
                      "classes) by exact RefMet name", "refmet/name/{refmet_name}/all",
                      args=("refmet_name",), example={"refmet_name": "Quercetin"}),
            Operation("refmet_by_formula", "RefMet records with one molecular formula",
                      "refmet/formula/{formula}/all", args=("formula",),
                      example={"formula": "C15H10O7"}),
            Operation("compound_by_inchikey", "Compound record and cross-references by "
                      "InChIKey", "compound/inchi_key/{inchikey}/all", args=("inchikey",),
                      example={"inchikey": "REFJWTPEDVJJIY-UHFFFAOYSA-N"}),
            Operation("compound_by_pubchem_cid", "Compound record and cross-references by "
                      "PubChem CID", "compound/pubchem_cid/{cid}/all", args=("cid",),
                      example={"cid": "5280343"}),
            Operation("study_summary", "One study's summary: title, species, institute, "
                      "analysis type, sample count, dates and licence",
                      "study/study_id/{study_id}/summary", args=("study_id",),
                      example={"study_id": "ST000001"}),
            Operation("study_analyses", "One study's analyses (platform, polarity, "
                      "chromatography)", "study/study_id/{study_id}/analysis",
                      args=("study_id",), example={"study_id": "ST000001"}),
            Operation("study_metabolites", "Named metabolites one study reports, with their "
                      "RefMet names", "study/study_id/{study_id}/metabolites",
                      args=("study_id",), example={"study_id": "ST000009"}),
            Operation("studies_by_refmet_name", "Studies that report a RefMet metabolite",
                      "study/refmet_name/{refmet_name}/data", args=("refmet_name",),
                      example={"refmet_name": "Quercetin"}),
            Operation("metstat", "Studies by context: analysis type; polarity; "
                      "chromatography; species; sample source; disease; KEGG id; RefMet "
                      "name (pass an empty string for a slot that is not used)",
                      "metstat/{analysis_type};{polarity};{chromatography};{species};"
                      "{sample_source};{disease};{kegg_id};{refmet_name}",
                      args=("analysis_type", "polarity", "chromatography", "species",
                            "sample_source", "disease", "kegg_id", "refmet_name"),
                      example={"analysis_type": "", "polarity": "", "chromatography": "",
                               "species": "Human", "sample_source": "Blood",
                               "disease": "Diabetes", "kegg_id": "", "refmet_name": ""}),
            # The documented m/z search (/moverz/REFMET/...) redirects to a page under
            # /data/ that answers HTML, not data (checked 2026-10-01): not wrapped.
        ), smoke="refmet_match", docs="https://www.metabolomicsworkbench.org/tools/mw_rest.php",
        rate_note="robots.txt disallows all crawling; per-entity look-ups of the documented "
                  "API only, at 1 req/s"),
)
