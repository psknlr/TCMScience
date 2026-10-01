"""Reference atlases: HuBMAP and 4D Nucleome snapshots, and the HCA, HuBMAP, 4DN and
BioSamples connectors.

Fixtures are tiny files written here in each source's real format (headers copied from
the real files, rows made up); nothing downloaded is used. The connector tests check the
requests that were verified live on 2026-10-01 and the limits the sources' robots rules
set (HCA per entity only, 4DN never ``limit=all`` or ``@@download``).
"""

from __future__ import annotations

import dataclasses
import gzip
import json
import sqlite3
import urllib.parse

import pytest

from bioagent.tcmdb import TCMDataHub

pytestmark = pytest.mark.unit


# ------------------------------------------------------------------------------ HuBMAP
# The real exports: tab-separated, CSV quoting, CRLF, line two a '#' row describing the
# fields, submitter name and e-mail columns. These columns are a subset of the real 258.
_DATASET_COLS = ("uuid", "hubmap_id", "analyte_class", "anatomical_structure_id",
                 "anatomical_structure_label", "assay_type", "created_by_user_displayname",
                 "created_by_user_email", "data_access_level", "dataset_type",
                 "description", "donor.hubmap_id", "operator_email",
                 "origin_samples_unique_mapped_organs", "status")


def _tsv(path, header, *rows):
    def cell(x):
        x = "" if x is None else str(x)
        return '"' + x.replace('"', '""') + '"' if any(c in x for c in '\t\n"') else x
    lines = ["\t".join(header)] + ["\t".join(cell(x) for x in r) for r in rows]
    path.write_bytes(("\r\n".join(lines) + "\r\n").encode("utf-8"))


def _hubmap_files(raw):
    raw.mkdir(parents=True)
    desc = ["#"] + [""] * (len(_DATASET_COLS) - 1)
    desc[_DATASET_COLS.index("description")] = 'Free-text "description",\nover two lines.'
    _tsv(raw / "datasets.tsv", _DATASET_COLS, desc,
         ("a" * 32, "HBM111.AAAA.111", "Protein", None, None, "CODEX", "Jane Roe",
          "jane@example.org", "public", "CODEX", "CODEX on spleen", "HBM900.DDDD.900",
          None, "['Spleen']", "Published"),
         ("b" * 32, "HBM222.BBBB.222", "RNA", None, None, None, "HuBMAP Process",
          "hubmap@example.org", "protected", "RNAseq", "snRNA-seq\twith a tab",
          "HBM901.DDDD.901", "op@example.org", "['Kidney (Right)', 'Knee (Left)']",
          "Published"),
         ("c" * 32, "HBM333.CCCC.333", "RNA", "UBERON:8600039,UBERON:8600041",
          "villous mesenchyme,villous capillary", None, "X", "x@example.org", "public",
          "GeoMx (NGS)", None, "HBM902.DDDD.902", None, "['Placenta']", "Published"),
         ("d" * 32, "HBM444.DDDD.444", "RNA", None, None, None, "X", "x@example.org",
          "public", "RNAseq", None, "HBM903.DDDD.903", None, "['Spleen']", "Retracted"),
         ("e" * 32, "HBM555.EEEE.555", "Lipid", None, None, None, "X", "x@example.org",
          "public", "MALDI", None, "HBM904.DDDD.904", None, "['Spinal Disc']",
          "Published"))
    _tsv(raw / "samples.tsv",
         ("uuid", "hubmap_id", "created_by_user_displayname", "created_by_user_email",
          "data_access_level", "donor.hubmap_id", "origin_samples_unique_mapped_organs",
          "sample_category", "status"),
         ("#", "", "", "", "", "", "", "", ""),
         ("f" * 32, "HBM666.FFFF.666", "Jane Roe", "jane@example.org", "public",
          "HBM900.DDDD.900", "['Spleen']", "block", "Published"))
    _tsv(raw / "donors.tsv",
         ("uuid", "hubmap_id", "age_unit", "age_value", "created_by_user_displayname",
          "created_by_user_email", "race", "sex"),
         ("#", "", "Unit for age measurement.", "The time elapsed since birth.", "", "",
          "", "Biological sex at birth: male or female or other."),
         ("9" * 32, "HBM900.DDDD.900", "years", "17.0", "Jane Roe", "jane@example.org",
          "White", "Male"))
    (raw / "organs.json").write_text(json.dumps([
        {"category": None, "code": "C030071", "laterality": None, "organ_cui": "C0037993",
         "organ_uberon": "UBERON:0002106", "rui_code": "SP", "rui_supported": True,
         "sab": "HUBMAP", "term": "Spleen"},
        {"category": {"organ_uberon": "UBERON:0002113", "term": "Kidney"},
         "code": "C030080", "laterality": "Right", "organ_cui": "C0227614",
         "organ_uberon": "UBERON:0004539", "rui_code": "RK", "rui_supported": True,
         "sab": "HUBMAP", "term": "Kidney (Right)"},
        {"category": {"organ_uberon": "UBERON:0001465", "term": "Knee"},
         "code": "C030072", "laterality": "Left", "organ_cui": "C0230433",
         "organ_uberon": "FMA:24978", "rui_code": "LN", "rui_supported": True,
         "sab": "HUBMAP", "term": "Knee (Left)"},
        {"category": None, "code": "C030089", "laterality": None, "organ_cui": "C0032043",
         "organ_uberon": "UBERON:0001987", "rui_code": "PL", "rui_supported": True,
         "sab": "HUBMAP", "term": "Placenta"},
    ]), encoding="utf-8")


@pytest.fixture
def hubmap(tmp_path):
    h = TCMDataHub(tmp_path / "hub")
    _hubmap_files(h.raw_dir("hubmap"))
    report = h.build("hubmap", log=lambda m: None)
    return h, report


def _with_cards(monkeypatch, *numbers):
    """The catalogue cards of added sources are written by the integrator; until then a
    check is run against temporary cards with those numbers."""
    from bioagent.tcmdb import hub as hubmod
    base = hubmod.catalog()
    extra = tuple(dataclasses.replace(base[0], no=n, name=f"card {n}") for n in numbers)
    monkeypatch.setattr(hubmod, "catalog", lambda: base + extra)


def test_hubmap_exports_load_without_the_description_row_or_contact_columns(hubmap):
    h, report = hubmap
    assert report["tables"] == {"datasets": 5, "samples": 1, "donors": 1, "organs": 4}
    cols = h.tables("hubmap")
    for table in ("datasets", "samples", "donors"):
        assert not [c for c in cols[table] if "mail" in c or "displayname" in c], table
    assert "donor_hubmap_id" in cols["datasets"]
    rows = h.query("hubmap", "datasets", limit=10)
    assert [r["hubmap_id"] for r in rows][:1] == ["HBM111.AAAA.111"]
    assert not any((r["uuid"] or "").startswith("#") for r in rows)
    quoted = h.query("hubmap", "datasets", where={"hubmap_id": "HBM222.BBBB.222"})[0]
    assert quoted["description"] == "snRNA-seq\twith a tab"
    assert h.query("hubmap", "donors")[0]["age_value"] == "17.0"


def test_hubmap_datasets_are_linked_to_uberon_organs(hubmap):
    h, report = hubmap
    rows = h.relations("dataset_tissue", limit=100)
    by = {(r["subject_id"], r["object_id"]): r for r in rows}
    spleen = by[("hubmap:HBM111.AAAA.111", "uberon:0002106")]
    assert spleen["object_name"] == "Spleen" and spleen["evidence"] == "listed"
    assert spleen["outcome"] == "positive" and spleen["effect"] is None
    assert json.loads(spleen["context"]) == {"species": "9606", "assay": "CODEX",
                                             "method": "CODEX"}
    kidney = by[("hubmap:HBM222.BBBB.222", "uberon:0004539")]
    assert kidney["note"] == "organ; part of Kidney"
    assert json.loads(kidney["context"])["flags"].startswith("protected")
    # an organ HuBMAP maps to FMA keeps the FMA term
    assert ("hubmap:HBM222.BBBB.222", "fma:24978") in by
    # a spatial region registered to anatomical structures adds one row per structure
    assert by[("hubmap:HBM333.CCCC.333", "uberon:8600041")]["object_name"] == \
        "villous capillary"
    assert ("hubmap:HBM333.CCCC.333", "uberon:0001987") in by
    # a retracted dataset is evidence of nothing
    assert not [r for r in rows if r["subject_id"] == "hubmap:HBM444.DDDD.444"]
    # an organ label outside HuBMAP's organ list keeps HuBMAP's own label as its id
    assert ("hubmap:HBM555.EEEE.555", "hubmap:organ.Spinal Disc") in by
    assert report["relations"] == {"dataset_tissue": 7} and report["unresolved"] == 0
    assert all(r["license"].startswith("CC BY 4.0") for r in rows)
    assert len(h.relations("dataset_tissue", commercial=True, limit=100)) == 7


def test_hubmap_passes_the_acceptance_check(hubmap, monkeypatch):
    h, _ = hubmap
    _with_cards(monkeypatch, 122)
    result = h.check("hubmap")
    assert result["ok"], result["problems"]
    assert result["licences"]["dataset_tissue"]["class"] == "open"


# ------------------------------------------------------------------------- 4D Nucleome
def _bedpe(path, *rows):
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        for r in rows:
            fh.write("\t".join(r) + "\n")


@pytest.fixture
def fourdn(tmp_path):
    h = TCMDataHub(tmp_path / "hub")
    raw = h.raw_dir("fourdn")
    raw.mkdir(parents=True)
    _bedpe(raw / "HFFc6.union-loops.bedpe.gz",
           ("chr1", "904330", "905329", "chr1", "937758", "938758",
            "Micro-C,CTCF-ChIAPET,Hi-C,RNAPII-ChIAPET"),
           ("chr1", "775000", "780000", "chr1", "820000", "825000", "H3K4me3-PLACSeq"),
           ("chr1", "775000", "780000", "chr1", "820000", "825000", "H3K4me3-PLACSeq"),
           ("chr1", "NA", "780000", "chr1", "820000", "825000", "Hi-C"))
    _bedpe(raw / "H1ESC.union-loops.bedpe.gz",
           ("chr1", "775000", "780000", "chr1", "820000", "825000", "Micro-C"))
    return h, h.build("fourdn", log=lambda m: None)


def test_fourdn_loops_join_grch38_anchors_with_their_supporting_platforms(fourdn):
    h, report = fourdn
    assert report["tables"] == {"loops_hffc6": 4, "loops_h1esc": 1}
    # the duplicated line is one row; the line without a start coordinate is none
    assert report["relations"] == {"chromatin_loop": 3}
    rows = h.relations("chromatin_loop", subject="grch38:chr1:904330-905329")
    assert len(rows) == 1
    row = rows[0]
    assert row["object_id"] == "grch38:chr1:937758-938758"
    assert row["evidence"] == "known" and row["reference"] == "4dn:4DNFI32J1C6W"
    ctx = json.loads(row["context"])
    assert ctx == {"species": "9606", "cell": "HFFc6", "genome_build": "GRCh38",
                   "assay": "CTCF-ChIAPET,Hi-C,Micro-C,RNAPII-ChIAPET",
                   "method": "4DN union loop calls"}
    # the same loop in two cell lines is two rows, each with its own cell line
    same = h.relations("chromatin_loop", subject="grch38:chr1:775000-780000")
    assert sorted(json.loads(r["context"])["cell"] for r in same) == ["H1-hESC", "HFFc6"]


def test_fourdn_passes_the_check_and_says_its_licence_is_not_a_named_one(fourdn,
                                                                          monkeypatch):
    h, _ = fourdn
    _with_cards(monkeypatch, 124)
    result = h.check("fourdn")
    assert result["ok"], result["problems"]
    assert result["licences"]["chromatin_loop"]["class"] == "unknown"
    assert any("chromatin_loop" in w for w in result["warnings"])


def test_atlas_datasets_are_registered_with_their_catalogue_numbers():
    from bioagent.policy import PROFILES
    from bioagent.tcmdb.datasets import dataset
    from bioagent.tcmdb.rowkit import RELATION_KINDS
    allowed = PROFILES["biomedical-research"].allowed_hosts
    assert dataset("hubmap").catalog == (122,) and dataset("fourdn").catalog == (124,)
    assert RELATION_KINDS["dataset_tissue"] == ("dataset", "tissue")
    assert RELATION_KINDS["chromatin_loop"] == ("region", "region")
    for key in ("hubmap", "fourdn"):
        spec = dataset(key)
        assert spec.commercial_use == "allowed"
        for f in spec.files:
            host = urllib.parse.urlsplit(f.url).hostname
            assert any(host == a or host.endswith("." + a) for a in allowed), host
            assert not f.optional
    # no portal @@download URL: the loop sets come from the AWS Open Data bucket
    assert all("@@download" not in f.url for f in dataset("fourdn").files)


# -------------------------------------------------------------------------- connectors
_KEYS = ("hca_azul", "hubmap_search", "hubmap_entity", "hubmap_portal", "fourdn",
         "biosamples")


def test_every_atlas_connector_is_registered_paced_and_allowed():
    from bioagent.backends.http import DEFAULT_RATES
    from bioagent.policy import PROFILES
    from bioagent.providers.public_apis import BY_KEY
    allowed = PROFILES["biomedical-research"].allowed_hosts
    for key in _KEYS:
        src = BY_KEY[key]
        assert src.host == urllib.parse.urlsplit(src.base_url).hostname
        assert any(src.host == a or src.host.endswith("." + a) for a in allowed), key
        assert src.host in DEFAULT_RATES
        if src.host != "www.ebi.ac.uk":                 # shared with the other EBI APIs
            assert DEFAULT_RATES[src.host] <= 1.0
        assert src.docs.startswith("https://")


def test_hca_is_asked_about_one_entity_at_a_time():
    from bioagent.providers.public_apis import BY_KEY, render_call
    r = render_call("hca_azul", "project")
    assert r["method"] == "GET"
    assert r["path"] == "projects/74b6d569-3b11-42ef-b6b1-a0454522b4a0"
    assert r["params"] == {"catalog": None}           # the service's default release
    assert render_call("hca_azul", "project", project_id="x", catalog="dcp60")["params"] \
        == {"catalog": "dcp60"}
    assert render_call("hca_azul", "file")["path"] == \
        "files/6e63e10e-7a5f-52b8-9242-df9d169b802a"
    # robots.txt disallows the index for crawlers: no paging, filtering or manifests
    for op in BY_KEY["hca_azul"].operations:
        assert "{" in op.path and not {"size", "filters", "search_after"} & set(op.params)
        assert "manifest" not in op.path


def test_hubmap_requests_name_their_fields_and_never_the_file_lists():
    from bioagent.providers.public_apis import render_call
    r = render_call("hubmap_search", "datasets_by_organ")
    assert (r["method"], r["path"]) == ("POST", "search")
    body = r["json_body"]
    assert body["size"] == 5
    assert body["query"]["bool"]["filter"] == [
        {"term": {"entity_type.keyword": "Dataset"}},
        {"term": {"origin_samples.organ.keyword": "SP"}}]
    fields = body["_source"]["includes"]
    assert "files" not in fields and not [f for f in fields if "contact" in f]
    r = render_call("hubmap_search", "datasets_by_type", dataset_type="RNAseq", size=3)
    assert r["json_body"]["size"] == 3
    assert {"term": {"dataset_type.keyword": "RNAseq"}} in \
        r["json_body"]["query"]["bool"]["filter"]
    assert render_call("hubmap_entity", "entity")["path"] == "entities/HBM543.RSRV.265"
    assert render_call("hubmap_portal", "entity")["path"] == \
        "browse/dataset/3d14dcc3d7c3e0cd339c9366e34b37c7.json"
    assert render_call("hubmap_portal", "organ")["path"] == "organs/spleen.json"


def test_fourdn_never_asks_for_everything_or_a_portal_download():
    from bioagent.backends.http import HTTPRequest
    from bioagent.providers.public_apis import BY_KEY, render_call
    assert render_call("fourdn", "processed_file")["path"] == "files-processed/4DNFI32J1C6W/"
    assert render_call("fourdn", "experiment_set")["params"] == {"format": "json"}
    r = render_call("fourdn", "experiment_sets_by_sample")
    url = HTTPRequest(url="https://data.4dnucleome.org/" + r["path"],
                      params=r["params"]).full_url
    assert url.startswith("https://data.4dnucleome.org/search/?type=ExperimentSetReplicate")
    assert "experiments_in_set.biosample.biosource_summary=HFF-hTERT" in url
    assert "&field=accession&field=description" in url and "limit=5" in url
    for op in BY_KEY["fourdn"].operations:
        assert "@@download" not in op.path
        assert op.params.get("limit") in (None, "{limit}")
        assert op.example.get("limit", 0) <= 100


def test_biosamples_requests_match_the_verified_ones():
    from bioagent.providers.public_apis import render_call
    assert render_call("biosamples", "sample")["path"] == "samples/SAMEA1094826"
    r = render_call("biosamples", "by_organism", organism="Glycyrrhiza uralensis")
    assert r["path"] == "samples"
    assert r["params"] == {"filter": "attr:organism:Glycyrrhiza uralensis", "size": 2}
    assert render_call("biosamples", "search")["params"] == {"text": "Panax ginseng",
                                                            "size": 2}


def test_no_atlas_connector_is_held_back_and_htan_is_not_wrapped():
    from bioagent.providers.public_apis import BY_KEY
    from bioagent.providers.supplement import atlases
    assert atlases.PENDING == ()
    assert {s.key for s in atlases.SOURCES} == set(_KEYS)
    assert not [k for k in BY_KEY if "htan" in k or "synapse" in k]


def test_a_built_store_records_where_each_table_came_from(hubmap):
    h, _ = hubmap
    with sqlite3.connect(h.db_path("hubmap")) as conn:
        urls = dict(conn.execute("SELECT tbl, url FROM _tcmdb_files"))
    assert urls["datasets"] == "https://portal.hubmapconsortium.org/metadata/v0/datasets.tsv"
    assert urls["organs"].startswith("https://ontology.api.hubmapconsortium.org/organs")
