"""Cell lines and perturbation resources: Cellosaurus, LINCS L1000, DepMap, scPerturb, ARCHS4.

Every fixture is a tiny synthetic file in the source's real format (the header and line
codes copied from the real files, rows made up or real-shaped). No network.
"""

from __future__ import annotations

import csv
import gzip
import json
import urllib.parse

import pytest

from bioagent.tcmdb import TCMDataHub
from bioagent.tcmdb.datasets import dataset
from bioagent.tcmdb.spec import allows_commercial, licence_class

pytestmark = pytest.mark.unit

KEYS = ("cellosaurus", "lincs_l1000", "depmap", "scperturb", "archs4")


def _quiet(_msg: str) -> None:
    return None


@pytest.fixture
def stub_catalogue(monkeypatch):
    """Catalogue cards 90-94 (the shared catalogue gets them at integration)."""
    from bioagent.tcmdb import hub as hubmod
    real = hubmod.catalog()
    stubs = tuple(hubmod.SourceCard(no=n, name=k, modules=(), url="", access="snapshot",
                                    connector=None, dataset=k, license="", barriers="",
                                    assessment="", checked="")
                  for n, k in zip(range(90, 95), KEYS))
    monkeypatch.setattr(hubmod, "catalog", lambda: real + stubs)


# --------------------------------------------------------------------------- Cellosaurus
_CELLO = """\
----------------------------------------------------------------------------
        CALIPHO group at the SIB - Swiss Institute of Bioinformatics
----------------------------------------------------------------------------

 Description: Cellosaurus: a knowledge resource on cell lines
 Version: 56.0

 Format of data file:

 ---------  ------------------------------  -----------------------
 Line code  Content                         Occurrence in an entry
 ---------  ------------------------------  -----------------------
 ID         Identifier (cell line name)     Once; starts an entry
 AC         Accession (CVCL_xxxx)           Once
 DI         Diseases                        Optional; once or more
 //         Terminator                      Once; ends an entry

____________________________________________________________________________
ID   HEp-2
AC   CVCL_1906
SY   Hep-2; HEP-2; Hep2 (HeLa derivative); HEp-2/HeLa
DR   ATCC; CCL-23
DR   Cell_Model_Passport; SIDM01208
DR   PubChem_Cell_line; CVCL_1906
CC   Problematic cell line: Contaminated. Shown to be a HeLa derivative (PubMed=4864103; PubMed=5641128). Originally thought to originate from a 56 year old male patient with a laryngeal carcinoma.
CC   Derived from site: In situ; Uterus, cervix; UBERON=UBERON_0000002.
DI   NCIt; C27677; Human papillomavirus-related endocervical adenocarcinoma
OX   NCBI_TaxID=9606; ! Homo sapiens (Human)
HI   CVCL_0030 ! HeLa
SX   Female
AG   30Y6M
CA   Cancer cell line
DT   Created: 04-04-12; Last updated: 10-04-25; Version: 43
//
ID   Hep-G2
AC   CVCL_0027
SY   HEP-G2; Hep G2; HEP G2; HepG2; HEPG2
DR   DepMap; ACH-000739
DR   LINCS_LDP; LCL-1925
CC   Problematic cell line: Misclassified. Originally thought to be a hepatocellular carcinoma cell line but shown to be from an hepatoblastoma (PubMed=19751877).
DI   NCIt; C3728; Hepatoblastoma
DI   ORDO; Orphanet_449; Hepatoblastoma
OX   NCBI_TaxID=9606; ! Homo sapiens (Human)
SX   Male
AG   15Y
CA   Cancer cell line
DT   Created: 04-04-12; Last updated: 25-06-26; Version: 51
//
ID   #16-15
AC   CVCL_KA96
DR   RCB; RCB1234
OX   NCBI_TaxID=10090; ! Mus musculus (Mouse)
OX   NCBI_TaxID=10116; ! Rattus norvegicus (Rat)
HI   CVCL_4032 ! P3X63Ag8.653
CA   Hybridoma
DT   Created: 22-08-17; Last updated: 21-03-23; Version: 3
//
"""


@pytest.fixture
def cello(tmp_path):
    h = TCMDataHub(tmp_path / "hub")
    raw = h.raw_dir("cellosaurus")
    raw.mkdir(parents=True)
    (raw / "cellosaurus.txt").write_text(_CELLO, encoding="utf-8")
    return h, h.build("cellosaurus", log=_quiet)


def test_cellosaurus_flat_file_becomes_four_tables_without_the_preamble(cello):
    h, report = cello
    assert report["tables"] == {"cell_line": 3, "cell_line_disease": 3, "cell_line_xref": 6,
                                "cell_line_name": 12}
    line = {r["accession"]: r for r in h.query("cellosaurus", "cell_line")}
    assert set(line) == {"CVCL_1906", "CVCL_0027", "CVCL_KA96"}
    assert line["CVCL_1906"]["parents"] == "CVCL_0030 ! HeLa"
    assert line["CVCL_1906"]["problematic"].startswith("Contaminated. Shown to be")
    assert line["CVCL_1906"]["derived_from_site"] == "In situ; Uterus, cervix; " \
                                                     "UBERON=UBERON_0000002."
    assert line["CVCL_KA96"]["species_taxids"] == "10090; 10116"
    assert line["CVCL_KA96"]["diseases"] is None


def test_cellosaurus_names_resolve_exactly_where_the_api_search_ranks(cello):
    h, _ = cello
    # the API's id:HepG2 search ranks HepG2 derivatives and misses Hep-G2 itself
    hits = h.query("cellosaurus", "cell_line_name", where={"name": "HepG2"})
    assert [(r["accession"], r["name_type"]) for r in hits] == [("CVCL_0027", "synonym")]
    assert h.query("cellosaurus", "cell_line_xref", where={"xref_id": "ACH-000739"})[0][
        "accession"] == "CVCL_0027"


def test_cellosaurus_diseases_are_listed_with_ncit_and_orpha_ids(cello):
    h, _ = cello
    rows = h.relations("cell_line_disease", limit=100)
    by = {(r["subject_id"], r["object_id"]): r for r in rows}
    assert set(by) == {("cellosaurus:CVCL_1906", "ncit:C27677"),
                       ("cellosaurus:CVCL_0027", "ncit:C3728"),
                       ("cellosaurus:CVCL_0027", "orpha:449")}
    hep2 = by[("cellosaurus:CVCL_1906", "ncit:C27677")]
    assert hep2["evidence"] == "listed" and hep2["outcome"] == "positive"
    assert hep2["effect"] is None
    assert json.loads(hep2["context"]) == {"flags": "problematic cell line: Contaminated",
                                           "species": "9606"}
    assert "HEp-2" in hep2["subject_name"] and "HEp-2/HeLa" in hep2["subject_name"]
    assert hep2["license"] == "CC BY 4.0"
    # a line with no DI line has no disease row: not tested is not negative
    assert not any(r["subject_id"] == "cellosaurus:CVCL_KA96" for r in rows)


def test_cellosaurus_passes_the_acceptance_check(cello, stub_catalogue):
    h, _ = cello
    result = h.check("cellosaurus")
    assert result["ok"], result["problems"]
    assert result["relations"] == {"cell_line_disease": {"listed/positive": 3}}
    assert result["licences"]["cell_line_disease"]["class"] == "open"


# ------------------------------------------------------------------------------- DepMap
_MODEL_HEADER = (
    "ModelID,PatientID,CellLineName,StrippedCellLineName,DepmapModelType,OncotreeLineage,"
    "OncotreePrimaryDisease,OncotreeSubtype,OncotreeCode,PatientSubtypeFeatures,RRID,Age,"
    "AgeCategory,Sex,PatientRace,PrimaryOrMetastasis,SampleCollectionSite,SourceType,"
    "SourceDetail,CatalogNumber,ModelType,TissueOrigin,ModelDerivationMaterial,ModelTreatment,"
    "PatientTreatmentStatus,PatientTreatmentType,PatientTreatmentDetails,Stage,StagingSystem,"
    "PatientTumorGrade,PatientTreatmentResponse,GrowthPattern,OnboardedMedia,FormulationID,"
    "SerumFreeMedia,PlateCoating,EngineeredModel,EngineeredModelDetails,CulturedResistanceDrug,"
    "PublicComments,CCLEName,HCMIID,ModelAvailableInDbgap,ModelSubtypeFeatures,"
    "WTSIMasterCellID,SangerModelID,COSMICID").split(",")


def _model(model_id, name, lineage, disease, subtype, code, rrid):
    row = dict.fromkeys(_MODEL_HEADER, "")
    row.update(ModelID=model_id, CellLineName=name,
               StrippedCellLineName=name.replace("-", "").replace(":", ""),
               OncotreeLineage=lineage, OncotreePrimaryDisease=disease,
               OncotreeSubtype=subtype, OncotreeCode=code, RRID=rrid)
    return [row[c] for c in _MODEL_HEADER]


def _csv(path, header, rows):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(rows)


@pytest.fixture
def depmap(tmp_path):
    h = TCMDataHub(tmp_path / "hub")
    raw = h.raw_dir("depmap")
    raw.mkdir(parents=True)
    _csv(raw / "Model.csv", _MODEL_HEADER, [
        _model("ACH-000001", "NIH:OVCAR-3", "Ovary/Fallopian Tube", "Ovarian Epithelial Tumor",
               "High-Grade Serous Ovarian Cancer", "HGSOC", "CVCL_0465"),
        # a second model of the same line and disease: one row, not two
        _model("ACH-002000", "NIH:OVCAR-3", "Ovary/Fallopian Tube", "Ovarian Epithelial Tumor",
               "High-Grade Serous Ovarian Cancer", "HGSOC", "CVCL_0465"),
        _model("ACH-000172", "TM-87", "Soft Tissue", "Rhabdoid Cancer",
               "Extrarenal Rhabdoid Cancer", "", "CVCL_8001"),
        _model("ACH-003000", "HTERT-RPE1", "Eye", "Non-Cancerous", "Non-Cancerous", "",
               "CVCL_4388"),
        _model("ACH-004000", "PDO-17", "Bowel", "Colorectal Adenocarcinoma",
               "Colon Adenocarcinoma", "COAD", ""),
    ])
    _csv(raw / "CRISPRInferredCommonEssentials.csv", ["Essentials"],
         [["AAMP (14)"], ["AARS1 (16)"]])
    _csv(raw / "PortalCompounds.csv",
         ["CompoundID", "CompoundName", "GeneSymbolOfTargets", "TargetOrMechanism", "Synonyms",
          "SampleIDs", "ChEMBLID", "SMILES", "InChIKey", "DoseUnit", "PubChemCID"],
         [["DPC-000001", "(+)-CAMPTOTHECIN", "TOP1", "TOPOISOMERASE INHIBITOR",
           "CAMPTOTHECIN;CAMPTOTHECINE", "BRD:BRD-K37890730-001-15-1;GDSC2:1003",
           "CHEMBL65", "CCC1(O)C(=O)OCC2=C1C=C1N(C2=O)CC2=CC3=CC=CC=C3N=C12",
           "VSJKWCGYPAHWDS-FQEVSTJZSA-N", "uM", "24360"],
          ["DPC-000004", "(R)-(-)-APOMORPHINE", "DRD1;DRD2", "DOPAMINE RECEPTOR AGONIST",
           "R(-)-APOMORPHINE", "BRD:BRD-K76022557-003-02-7", "", "", "", "uM", ""],
          ["DPC-000002", "(+)-JQ-1", "", "", "JQ-1;JQ1", "CTRP:616354;GDSC1:1218", "", "", "",
           "uM", ""]])
    _csv(raw / "AchillesCommonEssentialControls.csv", ["Gene"], [["AAMP (14)"]])
    _csv(raw / "AchillesNonessentialControls.csv", ["Gene"], [["ABCG8 (64241)"]])
    (raw / "README.txt").write_text("# DepMap Public 24Q4\n", encoding="utf-8")
    return h, h.build("depmap", log=_quiet)


def test_depmap_models_meet_cellosaurus_by_rrid_and_uncoded_diseases_wait(depmap):
    h, report = depmap
    rows = h.relations("cell_line_disease", limit=100)
    assert {(r["subject_id"], r["object_id"]) for r in rows} == {
        ("cellosaurus:CVCL_0465", "oncotree:HGSOC"), ("depmap:ACH-004000", "oncotree:COAD")}
    ovcar = next(r for r in rows if r["subject_id"] == "cellosaurus:CVCL_0465")
    assert ovcar["evidence"] == "listed"
    assert json.loads(ovcar["context"]) == {"tissue": "Ovary/Fallopian Tube"}
    assert ovcar["note"] == "depmap:ACH-000001"
    # Rhabdoid cancer has no OncoTree code: queued; 'Non-Cancerous' is not a disease
    queue = h.unresolved("depmap")
    assert report["unresolved"] == 1
    assert queue[0]["subject_id"] == "cellosaurus:CVCL_8001"
    assert queue[0]["object_name"] == "Extrarenal Rhabdoid Cancer | Rhabdoid Cancer"


def test_depmap_common_essentials_and_compound_targets(depmap):
    h, _ = depmap
    ess = h.relations("screen_gene", limit=100)
    assert {r["object_id"] for r in ess} == {"symbol:AAMP", "symbol:AARS1"}
    aamp = next(r for r in ess if r["object_id"] == "symbol:AAMP")
    assert aamp["evidence"] == "known" and aamp["note"] == "ncbigene:14"
    assert json.loads(aamp["context"])["phenotype"] == "common essential"
    targets = h.relations("drug_target", limit=100)
    assert {(r["subject_id"], r["object_id"]) for r in targets} == {
        ("depmap:DPC-000001", "symbol:TOP1"), ("depmap:DPC-000004", "symbol:DRD1"),
        ("depmap:DPC-000004", "symbol:DRD2")}
    for r in targets:
        assert r["evidence"] == "aggregated"
        assert r["effect"] is None                   # the mechanism is the compound's
    camp = next(r for r in targets if r["subject_id"] == "depmap:DPC-000001")
    assert json.loads(camp["context"]) == {"mechanism": "TOPOISOMERASE INHIBITOR"}
    assert camp["subject_name"] == "(+)-CAMPTOTHECIN | CAMPTOTHECIN | CAMPTOTHECINE"


def test_depmap_compounds_map_to_inchikeys_for_consensus(depmap):
    from bioagent.tcmdb.consensus import Crosswalk
    h, _ = depmap
    cw = Crosswalk(h)
    assert cw.canon("drug", "depmap:DPC-000001") == "inchikey:VSJKWCGYPAHWDS-FQEVSTJZSA-N"
    assert cw.canon("drug", "depmap:DPC-000004") == "depmap:DPC-000004"   # no structure


def test_depmap_passes_the_acceptance_check_with_open_licences(depmap, stub_catalogue):
    h, _ = depmap
    result = h.check("depmap")
    assert result["ok"], result["problems"]
    assert {k: v["class"] for k, v in result["licences"].items()} == {
        "cell_line_disease": "open", "screen_gene": "open", "drug_target": "open"}
    assert len(h.relations(commercial=True, limit=100)) == 2 + 2 + 3


# ------------------------------------------------------------------- LINCS / scPerturb
def _gz_tsv(path, header, rows):
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        fh.write("\t".join(header) + "\n")
        for r in rows:
            fh.write("\t".join(r) + "\n")


def test_lincs_metadata_is_loaded_as_tables_and_yields_no_relations(tmp_path,
                                                                    stub_catalogue):
    h = TCMDataHub(tmp_path / "hub")
    raw = h.raw_dir("lincs_l1000")
    raw.mkdir(parents=True)
    _gz_tsv(raw / "GSE92742_Broad_LINCS_sig_info.txt.gz",
            ["sig_id", "pert_id", "pert_iname", "pert_type", "cell_id", "pert_dose",
             "pert_dose_unit", "pert_idose", "pert_time", "pert_time_unit", "pert_itime",
             "distil_id"],
            [["CPC006_A549_6H:BRD-K70792160-001-01-1:10", "BRD-K70792160", "10-DEBC",
              "trt_cp", "A549", "10.0", "µM", "10 µM", "6", "h", "6 h",
              "CPC006_A549_6H_X1_B3:K05|CPC006_A549_6H_X2_B3:K05"]])
    _gz_tsv(raw / "GSE92742_Broad_LINCS_pert_info.txt.gz",
            ["pert_id", "pert_iname", "pert_type", "is_touchstone", "inchi_key_prefix",
             "inchi_key", "canonical_smiles", "pubchem_cid"],
            [["BRD-K70792160", "10-DEBC", "trt_cp", "0", "GYBXAGDWMCJZJK",
              "GYBXAGDWMCJZJK-UHFFFAOYSA-N", "CCN(CC)CCCCN1c2ccccc2Oc2ccc(Cl)cc12", "11957660"],
             ["56582", "AKT2", "trt_oe", "0", "-666", "-666", "-666", "-666"]])
    _gz_tsv(raw / "GSE92742_Broad_LINCS_cell_info.txt.gz",
            ["cell_id", "cell_type", "base_cell_id", "precursor_cell_id", "modification",
             "sample_type", "primary_site", "subtype", "original_growth_pattern",
             "provider_catalog_id", "original_source_vendor", "donor_age", "donor_sex",
             "donor_ethnicity"],
            [["A549", "cell line", "A549", "-666", "-666", "tumor", "lung",
              "non small cell lung cancer| carcinoma", "adherent", "CCL-185", "ATCC", "58", "M",
              "Caucasian"]])
    _gz_tsv(raw / "GSE92742_Broad_LINCS_gene_info.txt.gz",
            ["pr_gene_id", "pr_gene_symbol", "pr_gene_title", "pr_is_lm", "pr_is_bing"],
            [["780", "DDR1", "discoidin domain receptor tyrosine kinase 1", "1", "1"]])
    _gz_tsv(raw / "GSE70138_Broad_LINCS_sig_info_2017-03-06.txt.gz",
            ["sig_id", "pert_id", "pert_iname", "pert_type", "cell_id", "pert_idose",
             "pert_itime", "distil_id"],
            [["LJP005_A375_24H:A03", "DMSO", "DMSO", "ctl_vehicle", "A375", "-666", "24 h",
              "LJP005_A375_24H_X1_B19:A03"]])
    _gz_tsv(raw / "GSE70138_Broad_LINCS_pert_info_2017-03-06.txt.gz",
            ["pert_id", "canonical_smiles", "inchi_key", "pert_iname", "pert_type"],
            [["BRD-K70792160", "CCN(CC)CCCCN1c2ccccc2Oc2ccc(Cl)cc12",
              "GYBXAGDWMCJZJK-UHFFFAOYSA-N", "10-DEBC", "trt_cp"]])
    _gz_tsv(raw / "GSE70138_Broad_LINCS_cell_info_2017-04-28.txt.gz",
            ["cell_id", "cell_type"], [["A375", "cell line"]])
    report = h.build("lincs_l1000", log=_quiet)
    assert report["relations"] == {} and report["missing"] == []
    assert "GSE106127_sig_info.txt.gz" in report["optional_absent"]
    # -666 is the Broad's missing-value mark: kept as written, never read as a number
    akt2 = h.query("lincs_l1000", "phase1_pert_info", where={"pert_id": "56582"})[0]
    assert akt2["inchi_key"] == "-666"
    sig = h.query("lincs_l1000", "phase1_sig_info")[0]
    assert (sig["cell_id"], sig["pert_idose"], sig["pert_itime"]) == ("A549", "10 µM", "6 h")
    result = h.check("lincs_l1000")
    assert result["ok"], result["problems"]


def test_scperturb_and_archs4_catalogues_are_tables(tmp_path, stub_catalogue):
    h = TCMDataHub(tmp_path / "hub")
    raw = h.raw_dir("scperturb")
    raw.mkdir(parents=True)

    def record(rid, version, keys):
        return {"id": rid, "pids": {"doi": {"identifier": f"10.5281/zenodo.{rid}"}},
                "metadata": {"version": version, "publication_date": "2022-08-22",
                             "rights": [{"id": "cc-by-4.0"}]},
                "files": {"count": len(keys), "entries": {
                    k: {"key": k, "size": s, "checksum": f"md5:{i:032x}",
                        "links": {"content": f"https://zenodo.org/api/records/{rid}/files/"
                                             f"{k}/content"}}
                    for i, (k, s) in enumerate(keys)}}}
    (raw / "zenodo_13350497.json").write_text(json.dumps(record(13350497, "1.4", [
        ("SrivatsanTrapnell2020_sciplex2.h5ad", 145178504),
        ("AdamsonWeissman2016_GSM2406675_10X001.h5ad", 34557246)])), encoding="utf-8")
    (raw / "zenodo_7058382.json").write_text(json.dumps(record(7058382, "1.0", [
        ("PierceGreenleaf2021_K562.zip", 2778105709)])), encoding="utf-8")
    report = h.build("scperturb", log=_quiet)
    assert report["tables"] == {"files": 2, "atac_files": 1}
    files = h.query("scperturb", "files")
    assert [f["key"] for f in files] == ["AdamsonWeissman2016_GSM2406675_10X001.h5ad",
                                         "SrivatsanTrapnell2020_sciplex2.h5ad"]
    assert files[1]["size"] == "145178504" and files[1]["license"] == "cc-by-4.0"
    assert files[1]["url"].endswith("/files/SrivatsanTrapnell2020_sciplex2.h5ad/content")
    assert h.check("scperturb")["ok"]

    raw = h.raw_dir("archs4")
    raw.mkdir(parents=True)
    entries = [{"checksum": "ae96de0519b9f008b0dc3a9f944ee9007daf2f6a", "data_level": "gene",
                "ensembl_annotation": 107, "file_size": 47865298452, "id": 12,
                "samples": 888821, "species": "human",
                "timestamp": "Mon, 13 Jan 2025 23:30:45 GMT", "version_major": "2",
                "version_minor": "5"},
               {"checksum": "20f4063e264437c78c7875d74a31685e2ca2a18d", "data_level": "gene",
                "ensembl_annotation": 107, "file_size": 37513952183, "id": 2,
                "samples": 722425, "species": "human",
                "timestamp": "Mon, 13 Jan 2025 19:38:56 GMT", "version_major": "2",
                "version_minor": "2"}]
    (raw / "archs4_versionfile.json").write_text(json.dumps({"versionfiles": entries}),
                                                 encoding="utf-8")
    h.build("archs4", log=_quiet)
    versions = h.query("archs4", "versions")
    assert [v["id"] for v in versions] == ["2", "12"]
    assert versions[1]["checksum"] == "ae96de0519b9f008b0dc3a9f944ee9007daf2f6a"
    assert h.check("archs4")["ok"]


# --------------------------------------------------------------------------------- specs
def test_large_files_are_optional_and_matrices_stay_raw():
    for key in KEYS:
        for f in dataset(key).files:
            if (f.expected_bytes or 0) > 300_000_000:
                assert f.optional, (key, f.name)
            if f.name.endswith((".gctx.gz", ".h5", ".h5ad")) or "GeneEffect" in f.name:
                assert f.fmt == "raw" and not f.table, (key, f.name)


def test_licences_and_commercial_use_follow_the_terms():
    assert licence_class(dataset("cellosaurus").license) == "open"
    assert allows_commercial(dataset("depmap").license)
    assert dataset("depmap").commercial_use == "allowed"
    # ARCHS4 states no data licence: unknown is not permission
    assert licence_class(dataset("archs4").license) == "unknown"
    assert dataset("archs4").commercial_use == "unknown"
    # DepMap's only snapshot is the CC BY figshare deposits; no portal (25Q2+) URL
    assert all("depmap.org" not in f.url for f in dataset("depmap").files)
    # nothing from the CLUE S3 bucket or API (named-user, academic-only terms)
    assert all("clue.io" not in f.url for f in dataset("lincs_l1000").files)
    assert not dataset("lincs_l1000").relations and not dataset("scperturb").relations


def test_every_download_host_is_in_the_allowlist():
    from bioagent.policy import PROFILES
    profile = PROFILES["biomedical-research"]
    hosts = {urllib.parse.urlsplit(f.url).hostname for k in KEYS for f in dataset(k).files}
    hosts.add("s3.k8s.maayanlab.cloud")             # ARCHS4 redirects there
    assert profile.check_network(sorted(hosts)).decision.value == "ALLOW", hosts


# ---------------------------------------------------------------------------- connector
def test_cellosaurus_connector_sends_the_verified_requests():
    from bioagent.backends.http import DEFAULT_RATES
    from bioagent.policy import PROFILES
    from bioagent.providers.public_apis import BY_KEY, render_call
    src = BY_KEY["cellosaurus"]
    assert src.host == "api.cellosaurus.org" and DEFAULT_RATES[src.host] == 1.0
    assert PROFILES["biomedical-research"].check_network([src.host]).decision.value == "ALLOW"
    assert render_call("cellosaurus", "release_info") == {
        "path": "release-info", "method": "GET", "params": {"format": "json"},
        "accept": "application/json"}
    r = render_call("cellosaurus", "cell_line")
    assert r["path"] == "cell-line/CVCL_1906"
    assert r["params"] == {"format": "json", "fields": "id,ac,sy,ox,di,ca,sx,ag,hi,oi,cc,dt"}
    assert render_call("cellosaurus", "cell_line_by_xref")["params"]["q"] == "dr:ACH-000739"
    assert render_call("cellosaurus", "cell_line_by_accession")["params"]["q"] == \
        "acas:CVCL_0027"
    s = render_call("cellosaurus", "search", q="di:Hepatoblastoma")
    assert s["path"] == "search/cell-line" and s["params"]["q"] == "di:Hepatoblastoma"
    assert s["params"]["rows"] == 5
    assert render_call("cellosaurus", "cell_line", ac="CVCL_0030")["path"] == \
        "cell-line/CVCL_0030"
