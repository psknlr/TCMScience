"""Imaging archives: the metadata connectors and the IDC and IDR snapshots, offline.

The connectors are checked for the requests that were verified live on 2026-10-01 and
for their hosts being allowed; none of them fetches an image. The snapshots are built
from tiny files written here in the archives' real formats (the idc-index-data wheel's
parquet members, an IDR searcher export in parquet and in CSV) and checked for what the
rows claim: ids, evidence, outcomes (hits, tested non-hits, unconfirmed calls), context,
the licence of each study, the series licences of each collection, and lineage.
"""

from __future__ import annotations

import csv
import io
import json
import sqlite3
import zipfile
from dataclasses import replace

import pytest

from bioagent.policy import PROFILES, PolicyDecision
from bioagent.providers.public_apis import BY_KEY, render_call
from bioagent.providers.supplement import imaging as conn_mod
from bioagent.tcmdb import TCMDataHub
from bioagent.tcmdb.datasets import dataset
from bioagent.tcmdb.extra import imaging as data_mod

pytestmark = pytest.mark.unit

pa = pytest.importorskip("pyarrow")
pq = pytest.importorskip("pyarrow.parquet")

IMAGING = ("idc", "tcia", "tcia_cm", "idr", "openneuro")


# ------------------------------------------------------------------------- connectors
def test_the_imaging_connectors_are_registered_and_their_hosts_allowed():
    profile = PROFILES["biomedical-research"]
    assert {s.key for s in conn_mod.SOURCES} == set(IMAGING)
    for key in IMAGING + ("biostudies",):
        src = BY_KEY[key]
        ruling = profile.check_network((src.host,))
        assert ruling.decision is PolicyDecision.ALLOW, (key, ruling.reason)
    for spec in (dataset("idc"), dataset("idr")):
        for f in spec.files:
            host = f.url.split("/")[2]
            assert profile.check_network((host,)).decision is PolicyDecision.ALLOW, host


def test_no_imaging_operation_fetches_an_image():
    for key in IMAGING:
        for op in BY_KEY[key].operations:
            text = (op.path + json.dumps(dict(op.params))).lower()
            assert "getimage" not in text and "/objects/" not in text, (key, op.name)
            assert "container_data" not in text, (key, op.name)     # bulk exports


def test_idc_requests_match_the_verified_ones():
    assert BY_KEY["idc"].base_url.endswith("/v3")              # v1 and v2 answer 410
    r = render_call("idc", "collection", collection_id="tcga_luad")
    assert (r["method"], r["path"]) == ("GET", "collections/tcga_luad")
    r = render_call("idc", "licenses", collection_id="nsclc_radiomics")
    assert r["method"] == "POST" and r["path"] == "licenses"
    assert r["json_body"] == {"filters": {"terms": {"collection_id": ["nsclc_radiomics"]}}}
    r = render_call("idc", "sql")
    assert r["json_body"]["max_rows"] == 20 and r["json_body"]["sql"].startswith("SELECT")
    r = render_call("idc", "cohort_counts", collection_id="tcga_luad", modality="CT")
    assert r["json_body"]["filters"]["terms"] == {"collection_id": ["tcga_luad"],
                                                  "Modality": ["CT"]}


def test_tcia_requests_use_nbia_v4_and_the_collection_manager():
    assert BY_KEY["tcia"].base_url.endswith("/nbia-api/services/v4")
    r = render_call("tcia", "series", collection="TCGA-LUAD", patient_id="TCGA-17-Z062")
    assert r["path"] == "getSeries"
    assert r["params"] == {"Collection": "TCGA-LUAD", "PatientID": "TCGA-17-Z062"}
    r = render_call("tcia_cm", "collection", slug="4d-lung")
    assert r["path"] == "v1/collections/" and r["params"]["slug"] == "4d-lung"
    assert "cancer_types" in r["params"]["_fields"]
    r = render_call("tcia_cm", "download", download_id=42107)
    assert r["path"] == "v1/downloads/42107"
    assert "data_license" in r["params"]["_fields"]
    assert "download_access" in r["params"]["_fields"]
    r = render_call("tcia_cm", "collections_search", text="lung")
    assert r["path"] == "v2/collections/" and r["params"]["search"] == "lung"
    assert "Data Usage Policy" in BY_KEY["tcia"].license


def test_idr_and_openneuro_requests_match_the_verified_ones():
    r = render_call("idr", "gene_studies", symbol="CDC20")
    assert r["path"] == "searchengine/api/v1/resources/image/search/"
    assert r["params"]["key"] == "Gene Symbol" and r["params"]["value"] == "CDC20"
    assert r["params"]["return_containers"] == "true"
    r = render_call("idr", "screen_study", screen_id=1101)
    assert r["params"] == {"type": "map", "ns": "idr.openmicroscopy.org/study/info",
                           "screen": 1101}
    r = render_call("openneuro", "dataset", dataset_id="ds000001")
    assert r["method"] == "POST" and "dataset(id: $id)" in r["graphql"]
    assert r["variables"] == {"id": "ds000001"}
    r = render_call("openneuro", "datasets")
    assert r["variables"] == {"first": 5, "after": None}           # null: the first page


def test_the_bioimage_archive_is_reached_through_biostudies():
    assert "bioimage_archive" not in BY_KEY
    r = render_call("biostudies", "bioimages_search", query="hepatocyte")
    assert r["path"] == "BioImages/search"
    assert r["params"] == {"query": "hepatocyte", "pageSize": 5, "page": 1}
    assert "undocumented" in BY_KEY["biostudies"].op("bioimages_search").description
    assert render_call("biostudies", "study_info", accession="S-BIAD623")["path"] == \
        "studies/S-BIAD623/info"



@pytest.mark.parametrize("first", ["bioagent.providers.supplement.imaging",
                                   "bioagent.providers.supplement",
                                   "bioagent.tcmdb.extra.imaging"])
def test_a_domain_module_can_be_the_first_import_of_a_process(first):
    # In a fresh interpreter, importing the domain module first used to fail: it imports
    # public_apis, whose load_sources() then met the module half-initialised.
    import os
    import subprocess
    import sys
    code = (f"import {first}\n"
            "from bioagent.providers.public_apis import BY_KEY\n"
            "assert 'idc' in BY_KEY and 'openneuro' in BY_KEY\n")
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                          env={**os.environ}, timeout=120)
    assert done.returncode == 0, done.stderr


# ------------------------------------------------------------------------ IDC snapshot
def _parquet_bytes(records, schema=None):
    buf = io.BytesIO()
    pq.write_table(pa.Table.from_pylist(records, schema=schema), buf)
    return buf.getvalue()


def _source(doi, licence, kind="original_data", modalities="CT"):
    return {"Access": "Public", "ImageTypes": modalities, "citation": "Someone (2020).",
            "license": {"license_long_name": licence, "license_short_name": licence,
                        "license_url": "https://example.org/licence"},
            "modalities": modalities, "source_doi": doi, "source_id": "x",
            "source_title": "t", "source_type": kind, "source_url": f"https://doi.org/{doi}"}


def _collection(cid, name, cancer, species, sources, subjects=10, location="Lung"):
    return {"collection_name": name, "collection_id": cid, "cancer_types": cancer,
            "tumor_locations": location, "subjects": subjects, "species": species,
            "sources": sources, "supporting_data": "Clinical", "program_id": "community",
            "status": "Complete", "updated": "2024-01-01", "description": "d"}


def _idc_wheel(path):
    collections = [
        _collection("nsclc_radiomics", "NSCLC-Radiomics", "Lung Cancer", "Human",
                    [_source("10.7937/k9/tcia.2015.pf0m9rei", "CC BY 4.0"),
                     _source("10.7937/tcia.2019.x", "CC BY-NC 3.0", "analysis_result",
                             "SEG")]),
        _collection("cptac_dlbcl", "CPTAC-DLBCL",
                    "Diffuse Large B-Cell Lymphoma, Not Otherwise Specified, Burkitt Lymphoma",
                    "Human", [_source("10.5281/zenodo.123", "CC BY 4.0", modalities="SM")]),
        _collection("covid_19_ar", "COVID-19-AR", "COVID-19 (non-cancer)", "Human",
                    [_source("10.7937/tcia.2020.py71-5978", "CC BY 4.0")]),
        _collection("lung_phantom", "Lung-Phantom", "Phantom", "Phantom",
                    [_source("10.7937/k9/tcia.2015.08a1ixoo", "CC BY 3.0")]),
        _collection("ccdi_mci", "CCDI-MCI", "Various", "Human",
                    [_source("10.5281/zenodo.11099086", "CC BY 4.0", modalities="SM")]),
        _collection("nlm_visible_human_project", "NLM-Visible-Human-Project",
                    "Melanoma", "Human, Mouse",
                    [_source("10.1000/vhp", "National Library of Medicine Terms and "
                                            "Conditions; May 21, 2019")]),
    ]
    analysis = [{"analysis_result_id": "bamf_aimi_annotations",
                 "analysis_result_title": "BAMF", "source_DOI": "10.5281/zenodo.8345959",
                 "source_url": "u", "subjects": 4226, "collections": "nsclc_radiomics",
                 "modalities": "SEG", "updated": "2023-11-07", "license_url": "u",
                 "license_long_name": "CC BY 4.0", "license_short_name": "CC BY 4.0",
                 "description": "d", "citation": "c"}]
    versions = [{"idc_version": 24, "version_timestamp": "2026-07-01"}]
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("idc_index_data/collections_index.parquet", _parquet_bytes(collections))
        z.writestr("idc_index_data/analysis_results_index.parquet", _parquet_bytes(analysis))
        z.writestr("idc_index_data/version_metadata_index.parquet", _parquet_bytes(versions))
        z.writestr("idc_index_data/idc_index.parquet", b"not read")
        z.writestr("idc_index_data/__init__.py", "")


@pytest.fixture
def idc(tmp_path):
    h = TCMDataHub(tmp_path / "hub")
    raw = h.raw_dir("idc")
    raw.mkdir(parents=True)
    _idc_wheel(raw / dataset("idc").files[0].name)
    report = h.build("idc", log=lambda m: None)
    return h, report


def test_the_wheel_members_become_tables_and_the_sources_one_row_each(idc):
    h, report = idc
    assert report["tables"] == {"idc_collections": 6, "idc_collection_sources": 7,
                                "idc_analysis_results": 1, "idc_versions": 1}
    src = h.query("idc", "idc_collection_sources", where={"collection_id": "nsclc_radiomics"})
    assert sorted(r["license_short_name"] for r in src) == ["CC BY 4.0", "CC BY-NC 3.0"]
    coll = h.query("idc", "idc_collections", where={"collection_id": "nsclc_radiomics"})[0]
    assert json.loads(coll["sources"])[0]["source_doi"] == "10.7937/k9/tcia.2015.pf0m9rei"


def test_a_collection_lists_its_cancer_types_with_licences_and_lineage(idc):
    h, _ = idc
    rows = {r["subject_id"]: r for r in h.relations("dataset_disease", limit=100)}
    r = rows["idc:nsclc_radiomics"]
    assert (r["object_id"], r["object_name"], r["evidence"]) == (
        "idc:disease.lung_cancer", "Lung Cancer", "listed")
    assert r["reference"] == "doi:10.7937/k9/tcia.2015.pf0m9rei"   # original data only
    assert r["note"] == "series licences: CC BY 4.0, CC BY-NC 3.0; via TCIA"
    ctx = json.loads(r["context"])
    assert ctx["flags"] == "non-commercial series" and ctx["species"] == "9606"
    assert ctx["n"] == "10" and ctx["tissue"] == "Lung" and ctx["method"] == "CT"
    # the rows are IDC's MIT metadata; the images' licences are in the note
    assert r["license"] == dataset("idc").license
    from bioagent.tcmdb.consensus import lineage_of
    assert lineage_of(r) == frozenset({"TCIA"})


def test_cancer_type_lists_are_split_but_a_nos_qualifier_is_kept(idc):
    h, _ = idc
    objs = {r["object_name"] for r in h.relations("dataset_disease", limit=100)
            if r["subject_id"] == "idc:cptac_dlbcl"}
    assert objs == {"Diffuse Large B-Cell Lymphoma, Not Otherwise Specified",
                    "Burkitt Lymphoma"}
    row = next(r for r in h.relations("dataset_disease", limit=100)
               if r["subject_id"] == "idc:cptac_dlbcl")
    assert "via" not in (row["note"] or "")                       # a Zenodo DOI, not TCIA


def test_phantoms_give_no_row_various_is_queued_and_other_terms_are_flagged(idc):
    h, report = idc
    subjects = {r["subject_id"] for r in h.relations("dataset_disease", limit=100)}
    assert "idc:lung_phantom" not in subjects
    assert report["unresolved"] == 1
    assert h.unresolved("idc")[0]["subject_id"] == "idc:ccdi_mci"
    covid = next(r for r in h.relations("dataset_disease", limit=100)
                 if r["subject_id"] == "idc:covid_19_ar")
    assert covid["object_id"] == "idc:disease.covid_19"
    vhp = next(r for r in h.relations("dataset_disease", limit=100)
               if r["subject_id"] == "idc:nlm_visible_human_project")
    ctx = json.loads(vhp["context"])
    assert ctx["flags"] == "series under terms that grant no recognisable licence"
    assert ctx["species"] == "9606 | 10090"


# ------------------------------------------------------------------------ IDR snapshot
#: idr0013 export columns, as the real file has them
_MITO_COLUMNS = (
    "index", "image_webclient_url", "File Path", "Thumbnail", "id", "data_source", "name",
    "description", "screen_id", "screen_name", "plate_id", "plate_name",
    "Analysis Gene Annotation Build", "Cell Line", "Control Type", "Gene Identifier",
    "Mitocheck siRNA Identifier", "Organism", "siRNA Identifier", "Gene Identifier URL",
    "Quality Control", "Sense Sequence", "siRNA Pool Identifier", "Antisense Sequence",
    "Channels", "Gene Symbol", "Original Gene Target", "Potential Mitotic Hit At Gene Level",
    "Reagent Design Gene Annotation Build", "Validated Mitotic Hit At Gene Level",
    "Control Comments", "Gene Annotation Comments", "Phenotype", "Phenotype Term Name",
    "Has Phenotype", "Phenotype Term Accession", "Phenotype Annotation Level",
    "Phenotype Term Accession URL", "Comments", "Unnamed: 38")


def _well(i, gene=None, symbol=None, sirna=None, qc="True", control=None, potential=None,
          validated=None, phenotype=None, names=None, accessions=None, has=None):
    row = dict.fromkeys(_MITO_COLUMNS)
    row.update({"index": i, "id": str(1483351 + i), "data_source": "idr",
                "screen_id": "1101", "screen_name": "idr0013-neumann-mitocheck/screenA",
                "Cell Line": "HeLa", "Organism": "Homo sapiens", "Gene Identifier": gene,
                "Gene Symbol": symbol, "Mitocheck siRNA Identifier": sirna,
                "Quality Control": qc, "Control Type": control,
                "Potential Mitotic Hit At Gene Level": potential,
                "Validated Mitotic Hit At Gene Level": validated, "Phenotype": phenotype,
                "Phenotype Term Name": names, "Phenotype Term Accession": accessions,
                "Has Phenotype": has,
                "Phenotype Annotation Level": "multiple replicates of reagent" if has
                else None})
    return row


_PPP2R1A = dict(gene="ENSG00000105568", symbol="PPP2R1A", sirna="MCO_0007529",
                potential="yes", validated="yes",
                phenotype="metaphase delay/arrest (manual)",
                names="metaphase delayed phenotype,metaphase arrested phenotype",
                accessions="CMPO_0000307,CMPO_0000305", has="yes")


def _mitocheck_rows():
    return [
        _well(0, **_PPP2R1A),
        _well(1, **_PPP2R1A),                                   # replicate well
        _well(2, gene="ENSG00000068383", symbol="INPP5A", sirna="MCO_0007348",
              phenotype="cell death (automatic)", names="cell death phenotype",
              accessions="CMPO_0000030"),                        # no Has Phenotype call
        _well(3, gene="ENSG00000141510", symbol="TP53", sirna="MCO_0000001"),   # non-hit
        _well(4, gene="ENSG00000141510", symbol="TP53", sirna="MCO_0000002", qc="False"),
        _well(5, gene="ENSG00000186007", symbol="LEMD1", sirna="MCO_0051496",
              qc="FALSE,FALSE,TRUE,TRUE"),                       # merged row, failed QC
        _well(6, gene="ENSG00000138160", symbol="KIF11", sirna="MCO_0016402",
              control="positive control", potential="yes", has="yes",
              names="binuclear cell phenotype", accessions="CMPO_0000213"),
        _well(7, sirna="MCO_0006965", phenotype="cell death (automatic)",
              names="cell death phenotype", accessions="CMPO_0000030", has="yes"),
        _well(8, sirna="MCO_0006965", phenotype="cell death (automatic)",
              names="cell death phenotype", accessions="CMPO_0000030", has="yes"),
        _well(9, gene="ENSG00000141510,ENSG00000141510", symbol="TP53,TP53",
              sirna="MCO_0000001"),                              # merged, one gene
        _well(10, gene="ENSG00000213585", symbol="NP_112203.1", sirna="MCO_0000003"),
    ]


_PCM_HEADER = ("image_webclient_url,File Path,Thumbnail,id,data_source,name,description,"
               "project_name,project_id,Cell Cycle Phase,Cell Line,Gene Annotation Comments,"
               "Gene Identifier,Organism,Phenotype,Antibody Target,Channels,"
               "Gene Identifier URL,Phenotype Term Name,Gene Symbol,Has Phenotype,"
               "Phenotype Term Accession,Targeted Protein,Phenotype Annotation Level,"
               "Phenotype Term Accession URL,Targeted Protein URL,,Gene Symbol Synonyms")


def _pcm_row(i, gene, symbol, antibody, accession="CMPO_0000425", has="yes"):
    values = {"id": str(1885141 + i), "data_source": "idr",
              "name": f"{antibody}_{i:03d}_SIR_PRJ.dv",
              "project_name": "idr0021-lawo-pericentriolarmaterial/experimentA",
              "project_id": "51.0", "Cell Cycle Phase": "interphase", "Cell Line": "HeLa",
              "Gene Identifier": gene, "Organism": "Homo sapiens",
              "Phenotype": "protein localized to centrosome", "Antibody Target": antibody,
              "Phenotype Term Name": "protein localized in centrosome phenotype",
              "Gene Symbol": symbol, "Has Phenotype": has,
              "Phenotype Term Accession": accession, "Targeted Protein": symbol,
              "Phenotype Annotation Level": "protein", "": "None",
              "Gene Symbol Synonyms": "None"}
    return [values.get(c, "") for c in _PCM_HEADER.split(",")]


@pytest.fixture
def idr(tmp_path):
    h = TCMDataHub(tmp_path / "hub")
    raw = h.raw_dir("idr")
    raw.mkdir(parents=True)
    spec = dataset("idr")
    schema = pa.schema([("index", pa.int64())] + [(c, pa.string())
                                                 for c in _MITO_COLUMNS[1:]])
    pq.write_table(pa.Table.from_pylist(_mitocheck_rows(), schema=schema),
                   raw / spec.file("idr0013_mitocheck").name)
    with open(raw / spec.file("idr0021_pcm").name, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(_PCM_HEADER.split(","))
        w.writerow(_pcm_row(0, "ENSG00000131462", "TUBG1", "TUBG1-N"))
        w.writerow(_pcm_row(1, "ENSG00000131462", "TUBG1", "TUBG1-N"))
        w.writerow(_pcm_row(2, "ENSG00000160299", "PCNT", "PCNT-N1", accession="None"))
    report = h.build("idr", log=lambda m: None)
    return h, report


def _gp(h):
    return h.relations("gene_phenotype", limit=1000)


def test_a_knockdown_phenotype_is_one_row_per_reagent_and_term(idr):
    h, _ = idr
    rows = [r for r in _gp(h) if r["subject_name"] == "PPP2R1A"]
    assert {r["object_id"] for r in rows} == {"cmpo:CMPO_0000307", "cmpo:CMPO_0000305"}
    assert len(rows) == 2                               # two wells, one reagent: deduplicated
    r = rows[0]
    assert (r["subject_id"], r["evidence"], r["outcome"]) == (
        "ensembl:ENSG00000105568", "known", "positive")
    assert r["reference"] == "pmid:20360735" and r["license"] == "CC0 1.0"
    ctx = json.loads(r["context"])
    assert ctx["source_id"] == "MCO_0007529" and ctx["cell"] == "HeLa"
    assert ctx["species"] == "9606" and ctx["screen"] == "idr0013-neumann-mitocheck/screenA"


def test_a_phenotype_without_the_replicate_call_is_inconclusive(idr):
    h, _ = idr
    r = next(r for r in _gp(h) if r["subject_name"] == "INPP5A")
    assert r["outcome"] == "inconclusive"
    assert "Has Phenotype not set" in json.loads(r["context"])["flags"]


def test_controls_and_failed_wells_give_no_phenotype_and_unmapped_reagents_wait(idr):
    h, report = idr
    assert "KIF11" not in {r["subject_name"] for r in _gp(h)}       # positive control
    assert report["unresolved"] == 1                                 # two wells, one queue row
    q = h.unresolved("idr")[0]
    assert q["subject_id"] == "idr:sirna.MCO_0006965"
    assert q["object_name"] == "cell death phenotype"


def test_screen_calls_keep_hits_and_tested_non_hits_apart_from_untested(idr):
    h, _ = idr
    calls = {(r["object_name"], json.loads(r["context"])["measure"]): r
             for r in h.relations("screen_gene", limit=1000)}
    potential = "potential mitotic hit (primary screen, gene level)"
    validated = "validated mitotic hit (gene level)"
    assert calls[("PPP2R1A", potential)]["outcome"] == "positive"
    assert calls[("PPP2R1A", validated)]["outcome"] == "positive"
    tp53 = calls[("TP53", potential)]
    assert tp53["outcome"] == "negative"                 # tested (QC passed), no hit
    assert json.loads(tp53["context"])["n"] == 1         # one siRNA passed QC
    assert ("TP53", validated) not in calls              # validation: no negatives
    assert not any(name == "LEMD1" for name, _ in calls)  # only failed wells: not tested
    assert not any(name == "KIF11" for name, _ in calls)  # control wells only
    assert all(r["license"] == "CC0 1.0" for r in calls.values())


def test_a_localisation_study_keeps_its_own_licence_and_reads_none_as_missing(idr):
    h, _ = idr
    rows = [r for r in _gp(h) if r["reference"] == "pmid:23086237"]
    assert [(r["subject_name"], r["object_id"]) for r in rows] == [
        ("TUBG1", "cmpo:CMPO_0000425")]                  # PCNT's term is the string None
    r = rows[0]
    assert r["license"] == "CC BY 4.0"
    ctx = json.loads(r["context"])
    assert ctx["assay"] == "antibody TUBG1-N" and ctx["stage"] == "interphase"


def test_the_crosswalk_maps_ensembl_ids_to_symbols_but_not_other_names(idr):
    h, _ = idr
    sql = dataset("idr").crosswalk["gene"]
    with sqlite3.connect(h.db_path("idr")) as conn:
        pairs = dict(conn.execute(sql).fetchall())
    assert pairs["ensembl:ENSG00000105568"] == "symbol:PPP2R1A"
    assert pairs["ensembl:ENSG00000141510"] == "symbol:TP53"
    assert "ensembl:ENSG00000213585" not in pairs         # NP_112203.1 is no symbol


def test_both_snapshots_pass_the_acceptance_check(idc, idr, monkeypatch):
    from bioagent.tcmdb import hub as hubmod
    cards = hubmod.catalog()
    have = {c.no for c in cards}
    # catalogue cards 129 (IDC) and 132 (IDR) are added with the catalogue update
    extra = tuple(replace(cards[0], no=n) for n in (129, 132) if n not in have)
    monkeypatch.setattr(hubmod, "catalog", lambda: cards + extra)
    for h, key in ((idc[0], "idc"), (idr[0], "idr")):
        result = h.check(key)
        assert result["ok"], (key, result["problems"])
        assert all(v["class"] == "open" for v in result["licences"].values())


def test_the_wheel_reader_refuses_a_table_it_does_not_know(tmp_path):
    from bioagent.tcmdb.spec import FileSpec
    from bioagent.tcmdb.store import StoreError
    path = tmp_path / "w.whl"
    _idc_wheel(path)
    with pytest.raises(StoreError):
        list(data_mod._idc_wheel(path, FileSpec("u", "w.whl", "idc_series",
                                                fmt="idc_wheel")))
