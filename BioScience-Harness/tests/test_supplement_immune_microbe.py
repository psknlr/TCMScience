"""Immune repertoires, epitopes, pathogen genomes and metagenomes, offline.

The connectors are checked for the requests that were verified live on 2026-10-01 (the
added IEDB IQ-API operations, two AIRR Data Commons repositories, BV-BRC and MGnify) and
for their hosts being allowed. The IEDB snapshot is built from tiny files written here in
the exports' real layout (a zipped TSV with a category header row above a field header
row, and the ``|``-separated compound map) and checked for what the rows claim: ids,
outcomes (negative assays kept), the compound mapping, deduplication, the unresolved
queue and the licence.
"""

from __future__ import annotations

import csv
import io
import json
import zipfile

import pytest

from bioagent.policy import PROFILES, PolicyDecision
from bioagent.providers.public_apis import BY_KEY, render_call
from bioagent.providers.supplement import immune_microbe as conn_mod
from bioagent.tcmdb import TCMDataHub
from bioagent.tcmdb.datasets import dataset
from bioagent.tcmdb.extra import immune_microbe as data_mod

pytestmark = pytest.mark.unit

CONNECTORS = ("ireceptor_adc", "vdjserver_adc", "bv_brc", "mgnify")


# ------------------------------------------------------------------------- connectors
def test_the_connectors_are_registered_and_their_hosts_allowed():
    profile = PROFILES["biomedical-research"]
    assert {s.key for s in conn_mod.SOURCES} == set(CONNECTORS)
    assert conn_mod.PENDING == ()
    for key in CONNECTORS + ("iedb",):
        src = BY_KEY[key]
        ruling = profile.check_network((src.host,))
        assert ruling.decision is PolicyDecision.ALLOW, (key, ruling.reason)
        assert src.smoke in {op.name for op in src.operations}
        for op in src.operations:
            assert op.example is not None or not op.args, (key, op.name)
    for f in dataset("iedb").files:
        host = f.url.split("/")[2]
        assert profile.check_network((host,)).decision is PolicyDecision.ALLOW, host


def test_the_adc_requests_are_the_verified_post_queries():
    assert BY_KEY["ireceptor_adc"].base_url == "https://covid19-1.ireceptor.org/airr/v1"
    assert BY_KEY["vdjserver_adc"].base_url == "https://vdjserver.org/airr/v1"
    r = render_call("ireceptor_adc", "info")
    assert (r["method"], r["path"]) == ("GET", "info")
    r = render_call("ireceptor_adc", "repertoires_by_study")
    assert (r["method"], r["path"]) == ("POST", "repertoire")
    assert r["json_body"]["filters"] == {"op": "=", "content": {"field": "study.study_id",
                                                                "value": "PRJNA628125"}}
    assert r["json_body"]["size"] == 2 and "sample.tissue" in r["json_body"]["fields"]
    r = render_call("vdjserver_adc", "rearrangements_by_junction")
    assert r["path"] == "rearrangement"
    both = r["json_body"]["filters"]["content"]
    assert both[1] == {"op": "=", "content": {"field": "junction_aa", "value": "CASSLGQTETEAFF"}}
    assert "sequence_alignment" not in r["json_body"]["fields"]     # no alignments
    r = render_call("vdjserver_adc", "studies_by_diagnosis")
    assert r["json_body"] == {"filters": {"op": "contains", "content": {
        "field": "subject.diagnosis.disease_diagnosis.label", "value": "carcinoma"}},
        "facets": "study.study_id"}
    r = render_call("ireceptor_adc", "rearrangements", repertoire_id="x", offset=10, size=3)
    assert (r["json_body"]["from"], r["json_body"]["size"]) == (10, 3)


def test_bv_brc_queries_are_rql_in_the_path():
    r = render_call("bv_brc", "genome")
    assert r["method"] == "GET" and r["params"] == {}
    assert r["path"].startswith("genome/?eq(genome_id,83332.12)&select(genome_id,")
    assert r["path"].endswith("&limit(1)")
    r = render_call("bv_brc", "amr_lab_by_antibiotic")
    assert "eq(antibiotic,amikacin)&eq(evidence,Laboratory%20Method)" in r["path"]
    r = render_call("bv_brc", "epitopes_by_taxon")
    assert "IEDB" in BY_KEY["bv_brc"].op("epitopes_by_taxon").description


def test_mgnify_uses_api_v2_and_iedb_gained_iq_api_tables():
    assert BY_KEY["mgnify"].base_url.endswith("/metagenomics/api/v2")
    assert "Crawl-Delay 10" in BY_KEY["mgnify"].rate_note
    r = render_call("mgnify", "studies_by_biome")
    assert r["path"] == "studies/"
    assert r["params"] == {"biome_lineage": "root:Host-associated:Human:Digestive system",
                           "page_size": 2}
    assert render_call("mgnify", "genome")["path"] == "genomes/MGYG000450016"
    r = render_call("iedb", "epitopes_by_organism")
    assert r["path"] == "epitope_search"
    assert r["params"]["source_organism_iri_search"] == "cs.{NCBITaxon:4220}"
    r = render_call("iedb", "tcr_by_epitope")
    assert r["path"] == "tcr_search" and r["params"]["linear_sequences"] == "cs.{NLVPMVATV}"
    r = render_call("iedb", "tcell_by_antigen")
    assert r["params"]["parent_source_antigen_iri"] == "eq.UNIPROT:P01012"
    for name in ("epitope", "epitope_summary", "bcell_by_sequence", "mhc_by_sequence",
                 "bcr_by_cdr3", "reference_by_pmid", "tcell_by_molecule"):
        assert render_call("iedb", name)["method"] == "GET"


# ----------------------------------------------------------------------- IEDB snapshot
def _export(path, member, columns, records):
    """A zipped IEDB export: category row, field row, then the records."""
    buf = io.StringIO()
    w = csv.writer(buf, delimiter="\t", lineterminator="\n")
    cols = list(columns) + ["Extra | Ignored"]
    w.writerow([c.split(" | ")[0] for c in cols])
    w.writerow([c.split(" | ")[1] for c in cols])
    for rec in records:
        w.writerow([rec.get(c, "") for c in cols])
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(member, buf.getvalue())


_EPI = "http://www.iedb.org/epitope/"


def _assay(num, epitope, call, **extra):
    rec = {"Assay ID | IEDB IRI": f"http://www.iedb.org/assay/{num}",
           "Reference | PMID": "15448372", "Epitope | IEDB IRI": _EPI + str(epitope),
           "Epitope | Object Type": "Linear peptide", "Epitope | Name": "KLEDLERDL",
           "Epitope | Reference Name": "HDV 26-34",
           "Epitope | Molecule Parent": "Large delta antigen",
           "Epitope | Molecule Parent IRI": "http://www.uniprot.org/uniprot/P29996",
           "Epitope | Species": "Hepatitis delta virus",
           "Epitope | Species IRI": "http://purl.obolibrary.org/obo/NCBITaxon_12475",
           "Host | Name": "Homo sapiens (human)",
           "Host | IRI": "http://purl.obolibrary.org/obo/NCBITaxon_9606",
           "1st in vivo Process | Process Type": "Occurrence of infectious disease",
           "1st in vivo Process | Disease": "hepatitis D",
           "Assay | Method": "ELISPOT", "Assay | Response measured": "IFNg release",
           "Assay | IRI": "http://purl.obolibrary.org/obo/OBI_1110179",
           "Assay | Qualitative Measurement": call,
           "Effector Cell | Name": "PBMC", "Effector Cell | Source Tissue": "blood",
           "MHC Restriction | Name": "HLA-A*02:01", "MHC Restriction | Class": "I"}
    rec.update(extra)
    return rec


def _receptor(group, epitope, reference):
    return {"Receptor | Group IRI": f"https://www.iedb.org/receptor/{group}",
            "Receptor | IEDB Receptor ID": "57", "Receptor | Reference Name": "KK50.4",
            "Receptor | Type": "alphabeta",
            "Reference | IEDB IRI": f"https://www.iedb.org/reference/{reference}",
            "Epitope | IEDB IRI": f"https://www.iedb.org/epitope/{epitope}",
            "Epitope | Name": "VMAPRTLIL", "Assay | Type": "T cell",
            "Assay | MHC Allele Names": "HLA-E*01:01|HLA-E*01:03",
            "Chain 1 | Type": "alpha",
            "Chain 1 | Organism IRI": "http://purl.obolibrary.org/obo/NCBITaxon_9606",
            "Chain 1 | CDR3 Curated": "IVVRSSNTGKLI", "Chain 2 | Type": "beta",
            "Chain 2 | CDR3 Calculated": "ASSQDRDTQY"}


@pytest.fixture
def iedb(tmp_path):
    h = TCMDataHub(tmp_path / "hub")
    raw = h.raw_dir("iedb")
    raw.mkdir(parents=True, exist_ok=True)
    keep = data_mod._KEEP
    hapten = dict(_assay(30, 110163, "Positive-Low"), **{
        "Epitope | Object Type": "Non-peptidic", "Epitope | Name": "benzylpenicilloyl",
        "Epitope | Reference Name": "", "Epitope | Molecule Parent": "",
        "Epitope | Molecule Parent IRI": "",
        "Epitope | IRI": "http://purl.obolibrary.org/obo/CHEBI_34718"})
    unmapped = dict(hapten, **{"Assay ID | IEDB IRI": "http://www.iedb.org/assay/31",
                               "Epitope | IEDB IRI": _EPI + "999",
                               "Epitope | IRI": "http://purl.obolibrary.org/obo/CHEBI_12345"})
    _export(raw / "tcell_full_v3_tsv.zip", "tcell_full_v3.tsv", keep["tcell"], [
        _assay(29, 31803, "Positive", **{"Assay | Number of Subjects Tested": "4",
                                         "Assay | Number of Subjects Positive": "4"}),
        _assay(28, 31803, "Negative", **{
            "Reference | PMID": "", "Host | Name": "Mus musculus HLA-A2 Tg",
            "Host | IRI": "https://ontology.iedb.org/ontology/ONTIE_0000490"}),
        hapten, unmapped,
        _assay(32, 31803, ""),                                  # no outcome: queued
    ])
    _export(raw / "tcr_full_v3_tsv.zip", "tcr_full_v3.tsv", keep["tcr"],
            [_receptor(47, 69921, 1004539)])
    _export(raw / "bcr_full_v3_tsv.zip", "bcr_full_v3.tsv", keep["bcr"], [])
    _export(raw / "reference_full_v3_tsv.zip", "reference_full_v3.tsv", keep["reference"],
            [{"Reference ID | IEDB IRI": "http://www.iedb.org/reference/1004539",
              "Reference | Type": "Literature", "Reference | PMID": "16474394"}])
    (raw / "epitope_id_chebi_id_pubchem_id_maps.txt").write_text(
        "https://www.iedb.org/epitope/110163|https://www.ebi.ac.uk/chebi/CHEBI:34718|"
        "https://pubchem.ncbi.nlm.nih.gov/summary/summary.cgi?cid=6\n")
    report = h.build("iedb", log=lambda m: None)
    return h, report


def test_assays_keep_their_own_outcome_negatives_included(iedb):
    h, _ = iedb
    rows = {json.loads(r["context"])["source_id"]: r
            for r in h.relations("epitope_assay", limit=100)}
    assert set(rows) == {"iedb:assay.29", "iedb:assay.28"}
    pos, neg = rows["iedb:assay.29"], rows["iedb:assay.28"]
    assert pos["subject_id"] == "iedb:epitope.31803" and pos["object_id"] == "obi:1110179"
    assert (pos["evidence"], pos["outcome"], pos["reference"]) == ("known", "positive",
                                                                   "pmid:15448372")
    assert neg["outcome"] == "negative" and neg["reference"] is None
    ctx = json.loads(pos["context"])
    assert ctx["species"] == "ncbitaxon:9606" and ctx["cell"] == "PBMC" and ctx["tissue"] == "blood"
    assert ctx["n"] == "4" and ctx["assay"] == "T cell"
    assert "MHC HLA-A*02:01 (class I)" in pos["note"] and "4/4 subjects" in pos["note"]
    assert pos["license"] == "CC BY 4.0"
    # a strain host keeps IEDB's prefixed term as the species and its name in the note
    assert json.loads(neg["context"])["species"] == "iedb:ONTIE_0000490"
    assert "host Mus musculus HLA-A2 Tg" in neg["note"]
    assert "host" not in pos["note"]


def test_a_mapped_non_peptidic_epitope_is_a_compound(iedb):
    h, _ = iedb
    rows = {r["subject_id"]: r for r in h.relations("compound_assay", limit=100)}
    assert set(rows) == {"pubchem:6", "chebi:12345"}           # map first, else ChEBI IRI
    assert rows["pubchem:6"]["outcome"] == "positive"          # Positive-Low
    assert "iedb:epitope.110163" in rows["pubchem:6"]["note"]


def test_the_antigen_is_listed_once_per_epitope_and_unknown_outcomes_queue(iedb):
    h, report = iedb
    antigen = h.relations("epitope_antigen", limit=100)
    assert [(r["subject_id"], r["object_id"], r["evidence"]) for r in antigen] == [
        ("iedb:epitope.31803", "uniprot:P29996", "listed")]
    assert json.loads(antigen[0]["context"])["species"] == "ncbitaxon:12475"
    assert report["unresolved"] == 1
    assert "assay 32" in h.unresolved("iedb")[0]["reason"]


def test_a_receptor_meets_its_epitope_with_the_pubmed_reference(iedb):
    h, _ = iedb
    (row,) = h.relations("receptor_epitope", limit=10)
    assert row["subject_id"] == "iedb:receptor.47"
    assert row["object_id"] == "iedb:epitope.69921"
    assert row["reference"] == "pmid:16474394" and row["evidence"] == "known"
    assert "alpha IVVRSSNTGKLI / beta ASSQDRDTQY" in row["subject_name"]
    assert "MHC HLA-E*01:01, HLA-E*01:03" in row["note"]


def test_the_iedb_dataset_passes_the_check_and_a_changed_layout_is_refused(iedb, tmp_path):
    h, _ = iedb
    result = h.check("iedb")
    assert result["ok"], result
    spec = dataset("iedb")
    assert spec.catalog == (49,) and spec.commercial_use == "allowed"
    assert {f.name for f in spec.files if not f.optional} == {
        "tcell_full_v3_tsv.zip", "tcr_full_v3_tsv.zip", "bcr_full_v3_tsv.zip",
        "reference_full_v3_tsv.zip", "epitope_id_chebi_id_pubchem_id_maps.txt"}
    bad = tmp_path / "bad.zip"
    _export(bad, "tcell_full_v3.tsv", ("Assay ID | IEDB IRI",), [])
    tcell = next(f for f in spec.files if f.table == "tcell")
    with pytest.raises(Exception, match="IEDB changed its layout"):
        list(data_mod.READERS["iedb_tsv"](bad, tcell))


def test_mgnify_is_paced_at_the_ebi_crawl_delay_without_slowing_the_host(monkeypatch):
    import time

    from bioagent.backends.http import _RateLimiter, _default_rates

    rates = _default_rates()
    assert rates["www.ebi.ac.uk/metagenomics/"] <= 0.1
    assert rates["www.ebi.ac.uk"] > 0.1                   # other EBI APIs keep their rate
    clock = {"t": 1000.0}
    monkeypatch.setattr(time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(time, "sleep", lambda s: clock.__setitem__("t", clock["t"] + s))
    lim = _RateLimiter({"www.ebi.ac.uk": 10.0, "www.ebi.ac.uk/metagenomics/": 0.1})
    assert lim.wait("www.ebi.ac.uk", "/metagenomics/api/v2/studies") == 0
    assert lim.wait("www.ebi.ac.uk", "/metagenomics/api/v2/biomes") == pytest.approx(10)
    assert lim.wait("www.ebi.ac.uk", "/chembl/api/data/molecule") == pytest.approx(0.1)


def test_iedb_downloads_are_paced_at_the_crawl_delay(monkeypatch, tmp_path):
    from bioagent.acquisition import downloader as dl_mod
    from bioagent.tcmdb.datasets import dataset

    assert dataset("iedb").min_interval_s >= 10
    clock = {"t": 50.0}
    monkeypatch.setattr(dl_mod.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(dl_mod.time, "sleep", lambda s: clock.__setitem__("t", clock["t"] + s))
    d = dl_mod.Downloader(tmp_path, min_interval_s=10.5)
    d._pace()
    start = clock["t"]
    clock["t"] += 2.0
    d._pace()
    assert clock["t"] == pytest.approx(start + 10.5)
