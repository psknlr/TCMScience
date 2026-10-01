"""RNA, regulation and protein-modification sources: iPTMnet, DisProt, RNAcentral,
miRTarBase, ChIP-Atlas and ReMap snapshots, and their connectors.

Every file here is a few synthetic rows written in the source's real format (headers and
column orders copied from the files served on 2026-10-01). No network.
"""

from __future__ import annotations

import json

import pytest

from bioagent.backends.http import DEFAULT_RATES
from bioagent.policy import PROFILES
from bioagent.providers.public_apis import BY_KEY, render_call
from bioagent.providers.supplement import rna_reg as connectors
from bioagent.tcmdb import TCMDataHub
from bioagent.tcmdb.datasets import dataset
from bioagent.tcmdb.extra import rna_reg
from bioagent.tcmdb.spec import allows_commercial

pytestmark = pytest.mark.unit

_QUIET = {"log": lambda m: None}


def _write(path, lines, *, bom=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(lines) + "\n"
    path.write_text(("﻿" if bom else "") + text, encoding="utf-8")


def _tsv(*cells):
    return "\t".join(cells)


def _ctx(row):
    return json.loads(row["context"]) if row["context"] else {}


@pytest.fixture
def hub(tmp_path):
    return TCMDataHub(tmp_path / "hub")


# --------------------------------------------------------------------------- iPTMnet
def _iptmnet(raw):
    _write(raw / "readme.txt", ["iPTMnet release 6.2"])
    _write(raw / "ptm.txt", [
        # IEDB: substrate only, the note is the IEDB record id
        _tsv("ACETYLATION", "iedb", "A8TX70", "COL6A5", "Homo sapiens (Human)", "K652", "",
             "", "iedb:1797193", "34357683"),
        # HPRD: an enzyme is named, the note is a condition
        _tsv("PHOSPHORYLATION", "hprd", "P04637", "TP53", "Homo sapiens (Human)", "S15",
             "Q13315", "ATM", "in vivo", "9733514,9765201"),
        # RLIMS-P text mining
        _tsv("O-GLYCOSYLATION", "rlim+", "P34707", "skn-1", "Caenorhabditis elegans",
             "S470", "O18158", "ogt-1", "", "28272406"),
        # a row with no source cannot be attributed: dropped
        _tsv("PHOSPHORYLATION", "", "P04637", "TP53", "Homo sapiens (Human)", "S20",
             "O96017", "CHEK2", "", "1"),
    ])
    _write(raw / "score.txt", [_tsv("P04637", "S15", "Q13315", "Phosphorylation", "7"),
                               _tsv("A8TX70", "K652", "", "Acetylation", "1")])
    _write(raw / "protein.txt", [
        _tsv("P04637", "P53_HUMAN", "Cellular tumor antigen p53;",
             "Name: TP53; Synonyms: P53; ", "Homo sapiens (Human)", "PR:P04637", "SP"),
        _tsv("Q13315", "ATM_HUMAN", "Serine-protein kinase ATM;", "Name: ATM; ",
             "Homo sapiens (Human)", "PR:Q13315", "SP")])


def test_iptmnet_labels_curated_and_text_mined_rows_per_source(hub):
    _iptmnet(hub.raw_dir("iptmnet"))
    report = hub.build("iptmnet", **_QUIET)
    assert report["relations"] == {"ptm_site": 2, "protein_site": 1}
    sites = {_ctx(r)["source_db"]: r
             for r in hub.relations("ptm_site")}
    hprd = sites["hprd"]
    assert (hprd["subject_id"], hprd["object_id"]) == ("uniprot:Q13315", "uniprot:P04637")
    assert hprd["evidence"] == "aggregated" and hprd["note"] == "via HPRD"
    assert hprd["score"] in ("7", 7, 7.0)
    assert hprd["reference"] == "pmid:9733514 | pmid:9765201"
    c = _ctx(hprd)
    assert c["residue"] == "S15" and c["mechanism"] == "phosphorylation"
    assert c["condition"] == "in vivo"
    mined = sites["rlim+"]
    assert mined["evidence"] == "mentioned" and mined["note"] == "via RLIMS-P"
    assert mined["score"] is None                    # not in score.txt: no score, not 0
    (site,) = hub.relations("protein_site")
    assert site["subject_id"] == "uniprot:A8TX70"
    assert site["object_id"] == "uniprot:A8TX70/K652"
    assert site["note"] == "via IEDB" and _ctx(site)["source_id"] == "iedb:1797193"


def test_iptmnet_is_non_commercial_with_the_licence_contradiction_recorded():
    spec = dataset("iptmnet")
    assert spec.commercial_use == "forbidden"
    assert "CC BY-NC-SA 4.0" in spec.license and "CC BY 4.0" in spec.license
    assert not allows_commercial(spec.license)
    assert spec.catalog == (115,)
    assert "iptmnet" in {s.key for s in connectors.PENDING}
    assert "iptmnet" not in {s.key for s in connectors.SOURCES}


def test_iptmnet_crosswalk_maps_reviewed_human_proteins_to_symbols(hub):
    _iptmnet(hub.raw_dir("iptmnet"))
    hub.build("iptmnet", **_QUIET)
    import sqlite3
    conn = sqlite3.connect(hub.db_path("iptmnet"))
    pairs = set(conn.execute(dataset("iptmnet").crosswalk["gene"]))
    assert pairs == {("uniprot:P04637", "symbol:TP53"), ("uniprot:Q13315", "symbol:ATM")}


# --------------------------------------------------------------------------- DisProt
_REGION_COLS = ("acc", "name", "disorder_content", "organism", "ncbi_taxon_id", "disprot_id",
                "region_id", "start", "end", "term_namespace", "term", "term_name", "ec",
                "ec_name", "reference", "region_sequence", "confidence", "obsolete")


def _disprot(raw):
    xray = ("ECO:0006220", "X-ray crystallography-based structural model with missing "
            "residue coordinates used in manual assertion")
    _write(raw / "disprot_2026_06_regions.tsv", [
        _tsv(*_REGION_COLS),
        _tsv("P04637", "Cellular tumor antigen p53", "0.4", "Homo sapiens", "9606",
             "DP00086", "DP00086r001", "1", "93", "Structural state", "IDPO:0000002",
             "disorder", *xray, "pmid:8632448", "MEEPQ", "", ""),
        _tsv("P04637", "Cellular tumor antigen p53", "0.4", "Homo sapiens", "9606",
             "DP00086", "DP00086r002", "17", "29", "Molecular function", "GO:0005515",
             "protein binding", *xray, "pmid:8875929", "ETFSD", "AMBEXP", ""),
        _tsv("P04637", "Cellular tumor antigen p53", "0.4", "Homo sapiens", "9606",
             "DP00086", "DP00086r003", "300", "320", "Structural state", "IDPO:0000002",
             "disorder", *xray, "pmid:1", "PKKKP", "", "True"),
    ])
    entry = {"disprot_id": "DP00086", "acc": "P04637", "name": "Cellular tumor antigen p53",
             "genes": [{"name": {"value": "TP53", "evidences": []}, "synonyms": []}],
             "organism": "Homo sapiens", "ncbi_taxon_id": 9606, "length": 393,
             "disorder_content": 0.4, "regions_counter": 2, "released": "2016_10",
             "regions": [
                 {"region_id": "DP00086r002", "start": 17, "end": 29,
                  "term_id": "GO:0005515", "term_name": "protein binding",
                  "term_namespace": "Molecular function", "term_is_binding": True,
                  "ec_id": "ECO:0006077", "ec_name": "bait-prey hybrid interaction "
                  "evidence used in manual assertion", "reference_source": "pmid",
                  "reference_id": "8875929", "confidence": None,
                  "interaction_partner": [
                      {"db": "UniProt", "id": "Q00987", "partner_start": 25,
                       "partner_end": 109},
                      {"db": "ChEBI", "id": "CHEBI:29105", "partner_start": None,
                       "partner_end": None}]}]}
    _write(raw / "disprot_2026_06.json", [json.dumps({"data": [entry], "size": 1})])


def test_disprot_regions_and_partners(hub):
    _disprot(hub.raw_dir("disprot"))
    report = hub.build("disprot", **_QUIET)
    assert report["tables"] == {"regions": 3, "entries": 1, "partners": 2}
    assert report["relations"] == {"protein_region": 2, "protein_interaction": 1}
    regions = {r["object_id"]: r for r in hub.relations("protein_region")}
    assert set(regions) == {"idpo:0000002", "go:0005515"}      # the obsolete one is gone
    disorder = regions["idpo:0000002"]
    assert disorder["subject_id"] == "uniprot:P04637" and disorder["evidence"] == "known"
    assert disorder["outcome"] == "positive" and disorder["reference"] == "pmid:8632448"
    assert _ctx(disorder)["residue"] == "1-93" and _ctx(disorder)["assay"] == "ECO:0006220"
    assert "TP53" in disorder["subject_name"]
    assert regions["go:0005515"]["outcome"] == "inconclusive"   # ambiguous evidence
    (pair,) = hub.relations("protein_interaction")
    assert (pair["subject_id"], pair["object_id"]) == ("uniprot:P04637", "uniprot:Q00987")
    assert pair["effect"] == "binding" and pair["reference"] == "pmid:8875929"
    assert _ctx(pair)["sample"] == "partner residues 25-109"
    spec = dataset("disprot")
    assert spec.commercial_use == "allowed" and allows_commercial(spec.license)


# ------------------------------------------------------------------------ miRTarBase
_MTB_HEAD = ("miRTarBase ID,miRNA,Species (miRNA),Target Gene,Target Gene (Entrez ID),"
             "Species (Target Gene),Experiments,Support Type,References (PMID)")


def test_mirtarbase_keeps_support_classes_and_deduplicates(hub):
    raw = hub.raw_dir("mirtarbase")
    _write(raw / "LICENSE", ["MIRTARBASE IS PROVIDED AT NO COST IN THE PUBLIC DOMAIN"])
    row = ("MIRT003135,mmu-miR-122-5p,mmu,Cd320,54219.0,mmu,"
           "Luciferase reporter assay//qRT-PCR//Western blot,Functional MTI,18158304.0")
    _write(raw / "miRTarBase_SE_WR.csv", [
        _MTB_HEAD, row, row,                 # the file repeats a record verbatim
        "MIRT000002,hsa-miR-21-5p,hsa,PTEN,5728.0,hsa,Western blot,"
        "Functional MTI (Weak),19000000.0",
        "MIRT000003,hsa-miR-34a-5p,hsa,BCL2,596.0,hsa,Luciferase reporter assay,"
        "Non-Functional MTI,20000000.0",
        "MIRT000004,hsa-miR-1-3p,hsa,FOO1,0.0,hsa,Western blot,Functional MTI,21000000.0",
    ], bom=True)
    report = hub.build("mirtarbase", **_QUIET)
    assert report["relations"] == {"mirna_target": 3} and report["unresolved"] == 1
    got = {r["object_id"]: r for r in hub.relations("mirna_target")}
    cd320 = got["ncbigene:54219"]
    assert cd320["subject_id"] == "mirbase:mmu-miR-122-5p" and cd320["evidence"] == "known"
    assert cd320["reference"] == "pmid:18158304" and cd320["outcome"] == "positive"
    assert _ctx(cd320)["method"] == "Luciferase reporter assay | qRT-PCR | Western blot"
    weak = got["ncbigene:5728"]
    assert weak["evidence"] == "known" and _ctx(weak)["flags"] == "Functional MTI (Weak)"
    assert got["ncbigene:596"]["outcome"] == "negative"
    spec = dataset("mirtarbase")
    assert spec.commercial_use == "unknown"
    assert [f.name for f in spec.files if not f.optional] == ["LICENSE",
                                                              "miRTarBase_SE_WR.csv"]


# ------------------------------------------------------------------------ ChIP-Atlas
def _chip(raw):
    _write(raw / "analysisList.tab", [_tsv("STAT3", "Blood,Liver", "+", "hg38"),
                                      _tsv("AGO1", "Seedling", "+", "TAIR12"),
                                      _tsv("FOXZ9", "Blood", "-", "hg38")])
    _write(raw / "antigenList.tab", [
        _tsv("Genome", "Antigen_class", "Antigen", "Num_data", "ID"),
        _tsv("hg38", "TFs and others", "STAT3", "2", "SRX1,SRX2")])
    _write(raw / "celltypeList.tab", [
        _tsv("Genome", "Cell_type_class", "Cell_type", "Num_data", "ID"),
        _tsv("hg38", "Liver", "HepG2", "1", "SRX1")])
    _write(raw / rna_reg.TARGET_DIR / "hg38.STAT3.5.tsv", [
        _tsv("Target_genes", "STAT3|Average", "SRX1|HepG2", "SRX2|K-562", "STRING"),
        _tsv("SOCS3", "1500.5", "1000", "2001", "999"),
        _tsv("JUNB", "300", "0", "600", "0"),
        _tsv("ZZZ3", "0", "0", "0", "0"),          # no peak anywhere: not a target
    ])


def test_chip_atlas_target_genes_are_predicted_binding(hub):
    _chip(hub.raw_dir("chip_atlas"))
    report = hub.build("chip_atlas", **_QUIET)
    assert report["tables"]["target_genes"] == 3
    assert report["relations"] == {"tf_target": 2}
    got = {r["object_id"]: r for r in hub.relations("tf_target")}
    socs3 = got["symbol:SOCS3"]
    assert socs3["subject_id"] == "symbol:STAT3" and socs3["evidence"] == "predicted"
    assert socs3["effect"] == "binding" and float(socs3["score"]) == 1500.5
    assert _ctx(socs3)["n"] == 2 and _ctx(socs3)["genome_build"] == "hg38"
    assert got["symbol:JUNB"]["note"] == "1 of 2 experiments with a peak"


def test_chip_atlas_fetch_refuses_tfs_without_a_target_genes_table(hub):
    _chip(hub.raw_dir("chip_atlas"))
    out = rna_reg.fetch_target_genes(hub, ["FOXZ9", "AGO1"], build=False, **_QUIET)
    assert [r["ok"] for r in out] == [False, False]      # '-' in hg38; AGO1 is TAIR12
    with pytest.raises(Exception):
        rna_reg.fetch_target_genes(hub, ["STAT3"], distance=3, build=False, **_QUIET)


# ---------------------------------------------------------------------------- ReMap
def test_remap_dataset_index_has_absolute_bed_links_and_no_relations(hub):
    raw = hub.raw_dir("remap")
    row = ["<a href=target_page/AATF:9606>AATF</a>", "Homo sapiens",
           "<a href=http://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE93626>GSE93626</a>",
           "<a href=biotype_page/NALM-6:9606>NALM-6</a>",
           "<a href=storage/remap2022/hg38/MACS2/TF/AATF/"
           "remap2022_AATF_all_macs2_hg38_v1_0.bed.gz>hg38</a>",
           "<a href=storage/remap2022/hg38/MACS2/TF/AATF/"
           "remap2022_AATF_nr_macs2_hg38_v1_0.bed.gz>hg38</a>"]
    _write(raw / "download_by_target.json", [json.dumps({"data": [row, ["short"]]})])
    report = hub.build("remap", **_QUIET)
    assert report["tables"]["datasets"] == 1 and not report["relations"]
    import sqlite3
    conn = sqlite3.connect(hub.db_path("remap"))
    target, taxid, series, biotype, url = conn.execute(
        "SELECT target, taxid, series, biotype, all_peaks_url FROM datasets").fetchone()
    assert (target, taxid, series, biotype) == ("AATF", "9606", "GSE93626", "NALM-6")
    assert url.startswith("https://remap.univ-amu.fr/storage/remap2022/hg38/")
    spec = dataset("remap")
    assert spec.commercial_use == "forbidden" and "CC BY-NC" in spec.license
    big = [f for f in spec.files if f.name.endswith(".bed.gz")]
    assert big and all(f.optional for f in big)


# ------------------------------------------------------------------------ RNAcentral
def test_rnacentral_loads_id_mappings_only(hub):
    raw = hub.raw_dir("rnacentral")
    _write(raw / "release_notes.txt", ["RNAcentral Release 27"])
    _write(raw / "mirbase.tsv", [
        _tsv("URS000000076D", "MIRBASE", "MI0000019", "6239", "pre_miRNA", "cel-mir-48"),
        _tsv("URS00000007CB", "MIRBASE", "MIMAT0023447", "6289", "miRNA", "hco-miR-5945-3p")])
    _write(raw / "hgnc.tsv", [_tsv("URS0000416056", "HGNC", "HGNC:31476", "9606", "miRNA",
                                   "MIR21")])
    report = hub.build("rnacentral", **_QUIET)
    assert report["tables"]["mirbase"] == 2 and report["tables"]["hgnc"] == 1
    assert not report["relations"]
    spec = dataset("rnacentral")
    assert spec.commercial_use == "allowed"
    assert all(f.optional for f in spec.files if f.name == "id_mapping.tsv.gz")


# ------------------------------------------------------------------------ connectors
def _allowed(host):
    hosts = PROFILES["biomedical-research"].allowed_hosts
    return any(host == a or host.endswith("." + a) for a in hosts)


def test_connectors_are_registered_on_allowed_hosts_with_rates():
    assert {s.key for s in connectors.SOURCES} == {"disprot", "rnacentral", "chip_atlas",
                                                   "remap"}
    for source in connectors.SOURCES + connectors.PENDING:
        assert _allowed(source.host) and source.host in DEFAULT_RATES
    for source in connectors.SOURCES:
        assert BY_KEY[source.key] is source
    assert DEFAULT_RATES["rnacentral.org"] == pytest.approx(0.2)        # Crawl-delay 5 s
    assert DEFAULT_RATES["chip-atlas.org"] == pytest.approx(1 / 30)     # Crawl-delay 30 s
    for spec in rna_reg.DATASETS:
        for f in spec.files:
            host = f.url.split("/")[2]
            assert _allowed(host) or host == "ftp.ebi.ac.uk", host


def test_requests_are_the_ones_verified_live():
    call = render_call("disprot", "entry")
    assert (call["method"], call["path"]) == ("GET", "DP00086")
    call = render_call("disprot", "search_by_accession")
    assert call["path"] == "search" and call["params"] == {"acc": "P04637", "page_size": 5}
    call = render_call("rnacentral", "rna_by_external_id")
    assert call["path"] == "rna/" and call["params"] == {"format": "json",
                                                         "external_id": "MIMAT0000062"}
    call = render_call("rnacentral", "protein_targets")
    assert call["path"] == "rna/URS0000416056/protein-targets/9606/"
    assert call["params"] == {"format": "json", "page": 1, "page_size": 25}
    assert render_call("rnacentral", "go_annotations")["path"] == \
        "rna/URS0000416056/go-annotations/9606/"
    assert render_call("chip_atlas", "genomes")["path"] == "list_of_genome.json"
    call = render_call("chip_atlas", "experiment")
    assert call["path"] == "exp_metadata.json" and call["params"] == {"expid": "SRX018625"}
    call = render_call("chip_atlas", "antigens")
    assert call["params"] == {"genome": "hg38", "agClass": "TFs and others",
                              "clClass": "All cell types"}
    call = render_call("remap", "datasets_by_target")
    assert call["path"] == "datasets/findByTarget/target=FOXA1&taxid=9606"
    assert render_call("remap", "datasets_by_biotype")["path"] == \
        "datasets/findByBiotype/biotype=MCF-7&taxid=9606"
