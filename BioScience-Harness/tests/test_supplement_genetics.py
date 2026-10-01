"""Genetics sources: the eQTL Catalogue and IMPC snapshots, and the MaveDB and IMPC
connectors.

Every file here is a few synthetic rows written in the source's real format (the header
copied from the file served on 2026-10-01). No network.
"""

from __future__ import annotations

import gzip
import json

import pytest

from bioagent.backends.http import DEFAULT_RATES
from bioagent.policy import PROFILES
from bioagent.providers.public_apis import BY_KEY, render_call
from bioagent.providers.supplement import genetics as connectors
from bioagent.tcmdb import TCMDataHub
from bioagent.tcmdb.datasets import dataset
from bioagent.tcmdb.spec import allows_commercial

pytestmark = pytest.mark.unit


def _write(path, lines, *, gz=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(lines) + "\n"
    if gz:
        with gzip.open(path, "wt", encoding="utf-8") as fh:
            fh.write(text)
    else:
        path.write_text(text, encoding="utf-8")


def _tsv(*cells):
    return "\t".join(cells)


# ---------------------------------------------------------------------- eQTL Catalogue
_META = ("study_id", "dataset_id", "study_label", "sample_group", "tissue_id", "tissue_label",
         "condition_label", "sample_size", "quant_method", "pmid", "study_type")
_PATHS = ("study_id", "dataset_id", "study_label", "sample_group", "tissue_id",
          "tissue_label", "condition_label", "sample_size", "quant_method", "ftp_path",
          "ftp_cs_path", "ftp_lbf_path")
_CS = ("molecular_trait_id", "gene_id", "cs_id", "variant", "rsid", "cs_size", "pip",
       "pvalue", "beta", "se", "z", "cs_min_r2", "region")


def _eqtl(raw, *, credible_sets=True):
    liver = ("QTS000015", "QTD000266", "GTEx", "liver", "UBERON_0001114", "liver", "naive",
             "208", "ge", "32913098", "bulk")
    _write(raw / "dataset_metadata_r7.tsv", [
        _tsv(*_META), _tsv(*liver),
        _tsv("QTS000001", "QTD000001", "Alasoo_2018", "macrophage_naive", "CL_0000235",
             "macrophage", "naive", "84", "ge", "29379200", "bulk")])
    # release 8 reuses the dataset id for different data (GTEx v10, 261 samples)
    _write(raw / "dataset_metadata_r8_beta.tsv", [
        _tsv(*_META), _tsv("QTS000015", "QTD000266", "GTEx_v10", "liver", "UBERON_0001114",
                           "liver", "naive", "261", "ge", "32913098", "bulk")])
    ftp = "ftp://ftp.ebi.ac.uk/pub/databases/spot/eQTL"
    _write(raw / "tabix_ftp_paths.tsv", [
        _tsv(*_PATHS),
        _tsv(*liver[:9], f"{ftp}/sumstats/QTS000015/QTD000266/QTD000266.all.tsv.gz",
             f"{ftp}/susie/QTS000015/QTD000266/QTD000266.credible_sets.tsv.gz",
             f"{ftp}/susie/QTS000015/QTD000266/QTD000266.lbf_variable.txt.gz")])
    if credible_sets:
        cs = "ENSG00000230489_L1"
        _write(raw / "QTD000266.credible_sets.tsv.gz", [
            _tsv(*_CS),
            # one variant, three rsids: one variant-gene row
            _tsv("ENSG00000230489", "ENSG00000230489", cs, "chr1_108006349_TAAG_T",
                 "rs149029272", "53", "0.0197", "7.46e-09", "0.767", "0.116", "7.19", "0.94",
                 "chr1:106964443-108964443"),
            _tsv("ENSG00000230489", "ENSG00000230489", cs, "chr1_108006349_TAAG_T",
                 "rs752693742", "53", "0.0197", "7.46e-09", "0.767", "0.116", "7.19", "0.94",
                 "chr1:106964443-108964443"),
            _tsv("ENSG00000230489", "ENSG00000230489", cs, "chr1_108006349_TAAG_T",
                 "rs564865200", "53", "0.0197", "7.46e-09", "0.767", "0.116", "7.19", "0.94",
                 "chr1:106964443-108964443"),
            # a negative beta lowers expression of the gene for the ALT allele
            _tsv("ENSG00000000460", "ENSG00000000460", "ENSG00000000460_L1",
                 "chrX_169691524_A_G", "NA", "4", "0.61", "1.5e-09", "-0.705", "0.111",
                 "-6.35", "0.44", "chrX:168691524-170691524"),
            # a beta of exactly 0 states no direction
            _tsv("ENSG00000000460", "ENSG00000000460", "ENSG00000000460_L1",
                 "chrX_169692414_A_T", "rs4987375", "4", "0.2", "0.5", "0", "0.111",
                 "0", "0.44", "chrX:168691524-170691524"),
            # a variant that is not chr_pos_ref_alt is queued, not given a made-up id
            _tsv("ENSG00000000460", "ENSG00000000460", "ENSG00000000460_L1",
                 "chrX:169692999", "rs1", "4", "0.1", "0.5", "0.2", "0.111", "1.8", "0.44",
                 "chrX:168691524-170691524"),
        ], gz=True)


@pytest.fixture
def eqtl(tmp_path):
    h = TCMDataHub(tmp_path / "hub")
    _eqtl(h.raw_dir("eqtl_catalogue"))
    return h, h.build("eqtl_catalogue", log=lambda m: None)


def test_eqtl_catalogue_loads_its_catalogue_and_credible_sets(eqtl):
    _, report = eqtl
    assert report["tables"] == {"datasets_r7": 2, "datasets_r8_beta": 1, "ftp_paths_r7": 1,
                                "credible_sets_gtex_liver": 6}
    assert report["relations"] == {"variant_gene": 3}
    assert report["unresolved"] == 1


def test_an_eqtl_is_a_variant_by_coordinates_with_its_rsids_in_the_context(eqtl):
    h, _ = eqtl
    rows = {r["subject_id"]: r for r in h.relations("variant_gene")}
    assert set(rows) == {"grch38:1-108006349-TAAG-T", "grch38:X-169691524-A-G",
                         "grch38:X-169692414-A-T"}
    up = rows["grch38:1-108006349-TAAG-T"]
    assert up["object_id"] == "ensembl:ENSG00000230489"
    assert up["evidence"] == "associated" and up["outcome"] == "positive"
    assert up["effect"] == "increase" and up["score"] == "0.0197"
    assert up["reference"] == "pmid:32913098"
    assert up["note"].endswith("via GTEx") and "eQTL Catalogue r7" in up["note"]
    context = json.loads(up["context"])
    assert context["variant"] == "rs149029272 | rs564865200 | rs752693742"
    assert context["effect_allele"] == "T" and context["genome_build"] == "GRCh38"
    # the URL says release 7, so the release-7 dataset (208 samples) describes the row,
    # not the release-8 dataset that reuses the id
    assert context["dataset"] == "QTD000266 (r7)" and context["n"] == "208"
    assert context["tissue"] == "liver" and context["study"] == "GTEx"
    assert context["method"] == "ge" and "measure" not in context
    assert up["license"] == dataset("eqtl_catalogue").license
    down = rows["grch38:X-169691524-A-G"]
    assert down["effect"] == "decrease"
    assert "variant" not in json.loads(down["context"])         # rsid NA is not an rsid
    assert rows["grch38:X-169692414-A-T"]["effect"] is None       # beta 0: no direction


def test_an_unparseable_variant_waits_in_the_queue(eqtl):
    h, _ = eqtl
    (queued,) = h.unresolved("eqtl_catalogue")
    assert queued["kind"] == "variant_gene" and "chrX:169692999" in queued["reason"]
    assert queued["object_name"] == "ENSG00000000460"


def test_the_eqtl_catalogue_without_credible_sets_is_a_catalogue_only(tmp_path):
    h = TCMDataHub(tmp_path / "hub")
    _eqtl(h.raw_dir("eqtl_catalogue"), credible_sets=False)
    report = h.build("eqtl_catalogue", log=lambda m: None)
    assert report["relations"] == {}
    assert "QTD000266.credible_sets.tsv.gz" in report["optional_absent"]
    assert h.query("eqtl_catalogue", "ftp_paths_r7")[0]["ftp_cs_path"].endswith(
        "QTD000266.credible_sets.tsv.gz")


def test_the_eqtl_catalogue_spec_keeps_large_files_optional():
    spec = dataset("eqtl_catalogue")
    assert spec.catalog == (95,) and spec.commercial_use == "allowed"
    assert allows_commercial(spec.license)
    defaults = [f for f in spec.files if not f.optional]
    assert {f.table for f in defaults} == {"datasets_r7", "datasets_r8_beta", "ftp_paths_r7"}
    big = [f for f in spec.files if (f.expected_bytes or 0) > 300_000_000]
    assert big and all(f.optional and f.fmt == "raw" for f in big)


# --------------------------------------------------------------------------------- IMPC
_GP = ("marker_accession_id", "marker_symbol", "phenotyping_center", "colony_id", "sex",
       "zygosity", "allele_accession_id", "allele_symbol", "allele_name",
       "strain_accession_id", "strain_name", "project_name", "pipeline_name",
       "pipeline_stable_id", "procedure_stable_id", "procedure_name", "parameter_stable_id",
       "parameter_name", "top_level_mp_term_id", "top_level_mp_term_name", "mp_term_id",
       "mp_term_name", "p_value", "percentage_change", "effect_size", "statistical_method",
       "resource_name")
_VIA = ('"Gene Symbol","Gene Accession Id","Allele Symbol","Allele Accession Id",'
        '"Background Strain Name","Background Strain Accession Id","Chromosome",'
        '"Phenotyping Center","Colony Id","Zygosity","Breeding Strategy","Total # Pups",'
        '"Total # Male Pups","Total # Female Pups","Total # Hom Males","Total # Hom Females",'
        '"Total # Hem Males","Total # WT Males","Total # WT Females","Total # Het Males",'
        '"Total # Het Females","Percentage HOMs / HEMs","Supporting Data",'
        '"Viability Phenotype HOMs/HEMIs","Viability Call Method","Comment","Procedure Link"')


def _gp(gene, symbol, term, term_name, *, p="0.0", change="", effect="1.0",
        method="Supplied as data", resource="IMPC", sex="male", procedure="Gross Pathology",
        parameter="Skin", centre="TCP", colony="C1"):
    cells = (gene, symbol, centre, colony, sex, "homozygote", "MGI:5766143",
             f"{symbol}<tm1>", "targeted mutation 1", "MGI:2683688", "C57BL/6NCrl",
             "PROJ", "Pipeline", "P_001", "IMPC_X_001", procedure, "IMPC_X_001_001",
             parameter, "MP:0010771", "integument phenotype", term, term_name, p, change,
             effect, method, resource)
    return ",".join(f'"{c}"' if "," in c else c for c in cells)


def _impc(raw):
    _write(raw / "genotype-phenotype-assertions-ALL.csv.gz", [
        ",".join(_GP),
        _gp("MGI:1914346", "Mmachc", "MP:0001297", "microphthalmia"),
        _gp("MGI:1915864", "Letmd1", "MP:0011110",
            "preweaning lethality, incomplete penetrance", p="8.27058E-5",
            procedure="Viability Primary Screen", parameter="Viability Outcome",
            sex="not_considered"),
        _gp("MGI:1913452", "Fam217b", "MP:0002137", "decreased urine magnesium level",
            p="1.16965443883532E-10", change="-71.06", effect="-0.2599",
            method="Linear Mixed Model framework, LME, including Weight",
            procedure="Urinalysis", parameter="Magnesium"),
        _gp("MGI:97525", "Pde6b", "MP:0005534", "decreased body temperature",
            p="3.6E-5", effect="-0.40", method="Linear Model Using Generalized Least "
            "Squares framework, GLS, including Weight", resource="EuroPhenome",
            centre="HMGU"),
        _gp("MGI:1916000", "Trpa1", "MP:0001970", "abnormal pain threshold", p="1.0E-4",
            effect="2.1", method="Linear Mixed Model framework, LME, not including Weight",
            resource="pwg", centre="JAX"),
        _gp("MGI:2387356", "Ggnbp2", "MPATH:796", "aspermia", procedure="Histopathology",
            parameter="Epididymis - MPATH pathological process term", sex="no data"),
        _gp("MGI:104659", "Dll1", "", "", p="8.4E-6", effect="-0.88",
            method="Linear Mixed Model framework, LME, including Weight",
            resource="EuroPhenome", parameter="Food consumption"),
        # the same call twice in the file is one row
        _gp("MGI:1914346", "Mmachc", "MP:0001297", "microphthalmia"),
    ], gz=True)
    _write(raw / "viability.csv.gz", [
        _VIA, '"1110059G10Rik","MGI:1913452","1110059G10Rik<tm1a(KOMP)Wtsi>","MGI:4419470",'
              '"C57BL/6N","MGI:2159965","9","WTSI","MEYZ","homozygote","HetXHet","91","36",'
              '"55","10","14","No information available","5","14","21","27",'
              '"0.2637","Homozygous - Viable","viable","curated","",'
              '"https://www.mousephenotype.org/impress/ProcedureInfo?action=list&procID=703"'],
        gz=True)
    _write(raw / "README.md", ["# IMPC Reports"])


@pytest.fixture
def impc(tmp_path):
    h = TCMDataHub(tmp_path / "hub")
    _impc(h.raw_dir("impc"))
    return h, h.build("impc", log=lambda m: None)


def test_impc_loads_calls_and_viability_and_keeps_the_readme_as_a_file(impc):
    _, report = impc
    assert report["tables"] == {"genotype_phenotype": 8, "viability": 1}
    assert report["skipped"] == ["README.md"]
    assert report["relations"] == {"gene_phenotype": 6}            # duplicate merged
    assert report["unresolved"] == 1


def test_a_knockout_call_is_a_known_gene_phenotype_in_mouse(impc):
    h, _ = impc
    rows = {r["subject_name"]: r for r in h.relations("gene_phenotype")}
    eye = rows["Mmachc"]
    assert (eye["subject_id"], eye["object_id"]) == ("mgi:1914346", "mp:0001297")
    assert eye["evidence"] == "known" and eye["outcome"] == "positive"
    assert eye["effect"] is None and eye["license"] == "CC BY 4.0"
    context = json.loads(eye["context"])
    assert context["species"] == "ncbitaxon:10090"
    assert context["zygosity"] == "homozygote" and context["sex"] == "male"
    assert context["assay"] == "Gross Pathology | IMPC_X_001"
    assert context["model"] == "Mmachc<tm1> | C57BL/6NCrl"
    # "Supplied as data": the 0.0 P value and the 1.0 effect size are placeholders
    assert "pvalue" not in context and "value" not in context
    viability = json.loads(rows["Letmd1"]["context"])
    assert viability["pvalue"] == "8.27058E-5" and "value" not in viability
    assert viability["sex"] == "not_considered"
    measured = rows["Fam217b"]
    assert json.loads(measured["context"])["value"] == "-0.2599"
    assert measured["note"] == "percentage change -71.06"


def test_impc_names_the_legacy_resources_it_rehosts(impc):
    h, _ = impc
    rows = {r["subject_name"]: r for r in h.relations("gene_phenotype")}
    assert rows["Pde6b"]["note"] == "via EuroPhenome"
    # the Pain Working Group ran at IMPC centres: IMPC's own call, no upstream
    assert rows["Trpa1"]["note"] is None
    assert json.loads(rows["Trpa1"]["context"])["source_db"] == "pwg"


def test_a_histopathology_call_keeps_its_mpath_term_and_organ(impc):
    h, _ = impc
    (row,) = h.relations("gene_phenotype", subject="mgi:2387356")
    assert row["object_id"] == "mpath:796" and row["object_name"] == "aspermia"
    context = json.loads(row["context"])
    assert context["tissue"] == "Epididymis"
    assert "sex" not in context                                    # "no data" is unknown


def test_a_call_without_a_phenotype_term_is_queued(impc):
    h, _ = impc
    (queued,) = h.unresolved("impc")
    assert queued["subject_id"] == "mgi:104659"
    assert queued["object_name"] == "Food consumption"


def test_the_impc_spec_is_pinned_to_a_release():
    spec = dataset("impc")
    assert spec.catalog == (97,) and spec.commercial_use == "allowed"
    assert all("/release-24.0/" in f.url for f in spec.files)
    stats = spec.file("statistical-results-ALL.csv.gz")
    assert stats.optional and stats.fmt == "raw"


# --------------------------------------------------------------------------- connectors
def _allowed(host):
    hosts = PROFILES["biomedical-research"].allowed_hosts
    return any(host == a or host.endswith("." + a) for a in hosts)


def test_genetics_connectors_are_registered_on_allowed_hosts_with_rates():
    assert {s.key for s in connectors.SOURCES} == {"mavedb", "impc"}
    assert connectors.PENDING == ()
    for source in connectors.SOURCES:
        assert BY_KEY[source.key] is source
        assert _allowed(source.host) and source.host in DEFAULT_RATES
    for spec in (dataset("eqtl_catalogue"), dataset("impc")):
        for f in spec.files:
            host = f.url.split("/")[2]
            assert _allowed(host), host


def test_mavedb_requests_are_the_ones_verified_live():
    call = render_call("mavedb", "score_set")
    assert (call["method"], call["path"]) == ("GET", "score-sets/urn:mavedb:00000001-a-1")
    call = render_call("mavedb", "scores")
    assert call["path"] == "score-sets/urn:mavedb:00000001-a-1/scores"
    assert call["params"] == {"start": 0, "limit": 50} and call["accept"] == "text/csv"
    call = render_call("mavedb", "search_score_sets")
    assert call["method"] == "POST" and call["path"] == "score-sets/search"
    assert call["json_body"] == {"text": "UBE2I", "published": True, "limit": 5}
    call = render_call("mavedb", "score_sets_by_target", gene="TP53")
    assert call["json_body"] == {"targets": ["TP53"], "published": True, "limit": 5}
    call = render_call("mavedb", "gene_score_sets")
    assert call["path"] == "genes/BRCA1" and call["params"] == {"limit": 2, "offset": 0}
    assert render_call("mavedb", "licenses")["path"] == "licenses/"


def test_impc_requests_are_the_ones_verified_live():
    call = render_call("impc", "gene")
    assert (call["method"], call["path"]) == ("GET", "gene/select")
    assert call["params"]["q"] == "marker_symbol:Car4" and call["params"]["rows"] == 1
    assert "phenotyping_data_available" in call["params"]["fl"]
    call = render_call("impc", "phenotype_calls")
    assert call["path"] == "genotype-phenotype/select"
    assert call["params"]["q"] == "marker_symbol:Letmd1" and call["params"]["rows"] == 5
    call = render_call("impc", "genes_with_phenotype")
    assert call["params"]["q"] == 'mp_term_id:"MP:0001297"'
    call = render_call("impc", "statistical_results")
    assert call["path"] == "statistical-result/select" and "significant" in call["params"]["fl"]
    call = render_call("impc", "disease_models", symbol="Pax6")
    assert call["path"] == "phenodigm/select"
    assert call["params"]["q"] == "type:disease_model_summary AND marker_symbol:Pax6"
    assert call["params"]["rows"] == 3
