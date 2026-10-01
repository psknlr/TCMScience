"""ChEBI, Rhea, MetaNetX and FoodData Central: snapshot datasets and live connectors.

Every input here is a tiny file written by the test in the source's real format (the
header copied from the release, made-up or real-shaped rows); nothing is downloaded.
"""

from __future__ import annotations

import gzip
import io
import json
import tarfile
import zipfile

import pytest

from bioagent.tcmdb import TCMDataHub
from bioagent.tcmdb.consensus import Crosswalk
from bioagent.tcmdb.datasets import dataset
from bioagent.tcmdb.spec import licence_class

pytestmark = pytest.mark.unit

KAEMPFEROL = "IYRMWMYZSQPJKC-UHFFFAOYSA-N"
KAEMPFEROL_ANION = "IYRMWMYZSQPJKC-UHFFFAOYSA-M"


def _gz(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8", newline="") as fh:
        fh.write(text)


def _tsv(*rows):
    return "".join("\t".join(r) + "\n" for r in rows)


def _rels(hub, kind):
    return hub.relations(kind, limit=10000)


# ================================================================================ ChEBI
_MOLFILE = '"\n  Marvin  05100611142D\n\n  1  0  0  0  0  0            999 V2000\nM  END\n"'


def _chebi(raw):
    _gz(raw / "compounds.tsv.gz", _tsv(
        ("id", "name", "status_id", "source", "parent_id", "merge_type", "chebi_accession",
         "definition", "ascii_name", "stars", "modified_on", "release_date"),
        ("24431", "chemical entity", "1", "ChEBI", "", "", "CHEBI:24431", '""',
         "chemical entity", "3", "2014-07-28", ""),
        ("50906", "role", "1", "ChEBI", "", "", "CHEBI:50906", '""', "role", "3", "", ""),
        ("23824", "flavonol", "1", "ChEBI", "", "", "CHEBI:23824",
         '"A flavone with a ""3-hydroxy"" group;\ttab and\nnewline"', "flavonol", "3", "", ""),
        ("28499", "kaempferol", "1", "KEGG COMPOUND", "", "", "CHEBI:28499", "", "kaempferol",
         "3", "", ""),
        ("58573", "kaempferol oxoanion", "1", "ChEBI", "", "", "CHEBI:58573", "",
         "kaempferol oxoanion", "3", "", ""),
        ("22586", "antioxidant", "1", "ChEBI", "", "", "CHEBI:22586", "", "antioxidant", "3",
         "", "")))
    _gz(raw / "relation_type.tsv.gz", _tsv(
        ("id", "code", "allow_cycles", "description"),
        ("4", "has_role", "false", "has role"), ("5", "is_a", "false", "is a"),
        ("6", "is_conjugate_acid_of", "true", "is conjugate acid of")))
    _gz(raw / "relation.tsv.gz", _tsv(
        ("id", "relation_type_id", "init_id", "final_id", "status_id", "evidence_accession",
         "evidence_source_id"),
        ("1", "5", "23824", "24431", "1", "", ""),
        ("2", "5", "28499", "23824", "1", "", ""),
        ("3", "5", "58573", "24431", "9", "", ""),
        ("4", "5", "22586", "50906", "1", "", ""),          # role hierarchy: no row
        ("5", "4", "28499", "22586", "1", "", ""),
        ("6", "6", "28499", "58573", "1", "", "")))         # structural: no row
    _gz(raw / "status.tsv.gz", _tsv(("id", "name"), ("1", "CHECKED"), ("3", "OK"),
                                    ("9", "SUBMITTED")))
    _gz(raw / "source.tsv.gz", _tsv(
        ("id", "name", "url", "prefix", "description"),
        ("5", "Article", "", "article", ""),
        ("9", "BRENDA Ligand", "https://bioregistry.io/brenda.ligand:*", "brenda.ligand", ""),
        ("43", "International Plant Names Index", "", "IPNI", ""),
        ("55", "MetaboLights", "https://bioregistry.io/metabolights:*", "metabolights", ""),
        ("61", "National Center for Biotechnology Information", "", "ncbitaxon", ""),
        ("69", "PubMed", "http://europepmc.org/abstract/MED/*", "pubmed", "")))
    _gz(raw / "compound_origins.tsv.gz", _tsv(
        ("id", "compound_id", "species_source_id", "species_text", "species_accession",
         "component_source_id", "component_text", "component_accession", "strain_text",
         "source_id", "source_accession", "comments", "status_id"),
        ("1", "28499", "61", "Ficus mucuso", "309328", "", "fruit", "BTO:0000486", "", "69",
         "21619045", '"Methanolic extract of air-dried figs"', "1"),
        ("2", "28499", "43", "Pittocaulon velatum", "238587-1", "", "root", "BTO:0001188", "",
         "69", "21661732", "", "9"),
        ("3", "28499", "61", "Homo sapiens", "9606", "", "", "", "", "55", "MTBLS1636", '""',
         "9"),
        ("4", "28499", "61", "Mus musculus", "10090", "", "", "", "", "69", "19425150",
         "Source: BioModels - MODEL1507180067", "1"),
        ("5", "28499", "", "Deep water sponge", "", "", "", "", "", "69", "18512987", "", "1"),
        ("6", "28499", "9", "blood serum", "x", "", "", "", "", "5",
         '"Phytochemical Dictionary, Chapter 25"', "", "1")))
    _gz(raw / "structures.tsv.gz", _tsv(
        ("id", "compound_id", "status_id", "molfile", "smiles", "standard_inchi",
         "standard_inchi_key", "dimension", "default_structure"),
        ("10", "28499", "1", _MOLFILE, "Oc1ccc(cc1)-c1oc2cc(O)cc(O)c2c(=O)c1O",
         "InChI=1S/C15H10O6/c16-8-3-1-7(2-4-8)15-14(20)13(19)12-10(18)5-9(17)6-11(12)21-15"
         "/h1-6,16-18,20H", KAEMPFEROL, "2D", "true"),
        ("11", "58573", "1", _MOLFILE, "[O-]c1ccc(cc1)-c1oc2cc(O)cc(O)c2c(=O)c1O", '""',
         KAEMPFEROL_ANION, "2D", "true"),
        ("12", "23824", "1", '""', "", "", "", "2D", "true")))
    _gz(raw / "secondary_ids.tsv.gz", _tsv(("compound_id", "secondary_id"),
                                           ("28499", "6100"), ("28499", "24944")))


@pytest.fixture
def chebi(tmp_path):
    hub = TCMDataHub(tmp_path / "hub")
    _chebi(hub.raw_dir("chebi"))
    report = hub.build("chebi", log=lambda m: None)
    return hub, report


def test_chebi_quoted_fields_and_multiline_molfiles_load_as_written(chebi):
    hub, report = chebi
    assert report["tables"]["structures"] == 3 and report["tables"]["compounds"] == 6
    (flav,) = hub.query("chebi", "compounds", where={"id": "23824"})
    assert flav["definition"] == 'A flavone with a "3-hydroxy" group;\ttab and\nnewline'
    (row,) = hub.query("chebi", "structures", where={"id": "11"})
    assert "molfile" not in row                          # left out of the table
    assert row["standard_inchi"] is None                 # "" is an empty value
    assert row["standard_inchi_key"] == KAEMPFEROL_ANION


def test_chebi_origins_say_how_the_compound_was_found(chebi):
    hub, report = chebi
    rows = {r["subject_id"]: r for r in _rels(hub, "organism_compound")}
    assert set(rows) == {"ncbitaxon:309328", "ipni:238587-1", "ncbitaxon:9606",
                         "ncbitaxon:10090"}
    fig = rows["ncbitaxon:309328"]
    assert (fig["object_id"], fig["evidence"], fig["reference"]) == (
        "chebi:28499", "known", "pmid:21619045")
    assert json.loads(fig["context"]) == {"method": "Methanolic extract of air-dried figs",
                                          "qc": "CHECKED", "tissue": "fruit | BTO:0000486"}
    # submitted and not checked by ChEBI's curators
    assert rows["ipni:238587-1"]["evidence"] == "reported"
    human = rows["ncbitaxon:9606"]
    assert human["reference"] == "metabolights:MTBLS1636"
    assert human["note"] == "via MetaboLights" and human["evidence"] == "reported"
    # listed in a metabolic model of the organism, not isolated from it
    mouse = rows["ncbitaxon:10090"]
    assert mouse["evidence"] == "listed" and mouse["note"] == "via BioModels"
    # no taxonomy accession, or a namespace that is not a taxonomy: queued, not joined
    assert report["unresolved"] == 2
    queue = {q["subject_name"]: q for q in hub.unresolved("chebi")}
    assert set(queue) == {"Deep water sponge", "blood serum"}
    assert queue["Deep water sponge"]["reference"] == "pmid:18512987"
    assert queue["blood serum"]["reference"] == "Phytochemical Dictionary, Chapter 25"


def test_chebi_ontology_rows_are_chemical_classes_and_roles_only(chebi):
    hub, _ = chebi
    classes = {(r["subject_id"], r["object_id"]): r for r in _rels(hub, "compound_class")}
    assert set(classes) == {("chebi:23824", "chebi:24431"), ("chebi:28499", "chebi:23824"),
                            ("chebi:58573", "chebi:24431")}       # not role is_a role
    assert json.loads(classes[("chebi:58573", "chebi:24431")]["context"]) == {
        "qc": "SUBMITTED"}
    roles = _rels(hub, "compound_role")
    assert [(r["subject_id"], r["object_id"], r["object_type"], r["evidence"])
            for r in roles] == [("chebi:28499", "chebi:22586", "role", "listed")]
    assert all(r["license"] == "CC BY 4.0" for r in roles)


def test_chebi_ids_primary_and_secondary_map_to_their_own_structure(chebi):
    hub, _ = chebi
    cw = Crosswalk(hub).compound
    assert cw["chebi:28499"] == f"inchikey:{KAEMPFEROL}"
    assert cw["chebi:6100"] == cw["chebi:24944"] == f"inchikey:{KAEMPFEROL}"
    assert cw["chebi:58573"] == f"inchikey:{KAEMPFEROL_ANION}"      # its own charged form
    assert "chebi:23824" not in cw                                  # no structure
    spec = dataset("chebi")
    assert spec.catalog == (80,) and spec.commercial_use == "allowed"
    assert set(spec.relations) == {"organism_compound", "compound_class", "compound_role"}


# ================================================================================= Rhea
_RDF_HEAD = ('<?xml version="1.0" encoding="UTF-8"?>\n<rdf:RDF\n'
             '\txmlns:rh="http://rdf.rhea-db.org/"\n'
             '\txmlns:rdfs="http://www.w3.org/2000/01/rdf-schema#"\n'
             '\txmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">\n')
_RH = "http://rdf.rhea-db.org/"


def _d(about, *props):
    body = "".join(f"\t{p}\n" for p in props)
    return f'<rdf:Description rdf:about="{_RH}{about}">\n{body}</rdf:Description>\n'


def _res(pred, target, base=_RH):
    return f'<{pred} rdf:resource="{base}{target}"/>'


def _reaction(rid, cls, equation, status, *props):
    return _d(rid, _res("rdfs:subClassOf", cls), f"<rh:accession>RHEA:{rid}</rh:accession>",
              f"<rh:equation>{equation}</rh:equation>", _res("rh:status", status), *props)


def _compound(cid, accession, name, cls, *props):
    return _d(f"Compound_{cid}", f"<rh:accession>{accession}</rh:accession>",
              f"<rh:name>{name}</rh:name>", _res("rdfs:subClassOf", cls), *props)


def _rhea_rdf():
    chebi = "http://purl.obolibrary.org/obo/CHEBI_"
    eq = "kaempferol + [protein]-dithiol = teichoic acid + 2 H(+)"
    parts = [
        _d("contains1", _res("rdfs:subPropertyOf", "contains"), "<rh:coefficient>1</rh:coefficient>"),
        _d("contains2", _res("rdfs:subPropertyOf", "contains"), "<rh:coefficient>2</rh:coefficient>"),
        _reaction("15105", "Reaction", eq, "Approved", _res("rh:side", "15105_L"),
                  _res("rh:side", "15105_R"), _res("rh:directionalReaction", "15106")),
        _reaction("15106", "DirectionalReaction", eq.replace(" = ", " =&gt; "), "Approved",
                  _res("rh:substrates", "15105_L"), _res("rh:products", "15105_R")),
        _reaction("15107", "DirectionalReaction", eq.replace(" = ", " &lt;= "), "Approved",
                  _res("rh:substrates", "15105_R"), _res("rh:products", "15105_L")),
        _reaction("15108", "BidirectionalReaction", eq.replace(" = ", " &lt;=&gt; "),
                  "Approved", _res("rh:substratesOrProducts", "15105_L"),
                  _res("rh:substratesOrProducts", "15105_R")),
        # one side stated in two blocks, as the release writes it
        _d("15105_L", _res("rh:contains1", "Participant_15105_compound_1")),
        _d("15105_L", _res("rh:contains1", "Participant_15105_compound_2")),
        _d("15105_R", _res("rh:contains1", "Participant_15105_compound_3"),
           _res("rh:contains2", "Participant_15105_compound_4")),
        _d("Participant_15105_compound_1", _res("rh:compound", "Compound_1")),
        _d("Participant_15105_compound_2", _res("rh:compound", "Compound_2")),
        _d("Participant_15105_compound_3", _res("rh:compound", "Compound_3")),
        _d("Participant_15105_compound_4", _res("rh:compound", "Compound_4")),
        _compound("1", "CHEBI:58573", "kaempferol", "SmallMolecule",
                  _res("rh:chebi", "58573", chebi)),
        _compound("2", "GENERIC:10594", "[protein]-dithiol", "GenericPolypeptide",
                  _res("rh:reactivePart", "Compound_2_rp1")),
        _d("Compound_2_rp1", _res("rdfs:subClassOf", "ReactivePart"),
           "<rh:name>L-cysteine residue</rh:name>", _res("rh:chebi", "29950", chebi)),
        _compound("3", "POLYMER:12833", "teichoic acid", "Polymer",
                  "<rh:polymerizationIndex>n</rh:polymerizationIndex>",
                  _res("rh:underlyingChebi", "133894", chebi)),
        _compound("4", "CHEBI:15378", "H(+)", "SmallMolecule",
                  _res("rh:chebi", "15378", chebi)),
        # a transport reaction: the same compound on both sides, in two places
        _reaction("10192", "Reaction", "choline(out) = choline(in)", "Preliminary",
                  _res("rh:side", "10192_L"), _res("rh:side", "10192_R")),
        _d("10192_L", _res("rh:contains1", "Participant_10192_compound_5_out")),
        _d("10192_R", _res("rh:contains1", "Participant_10192_compound_5_in")),
        _d("Participant_10192_compound_5_out", _res("rh:location", "Out"),
           _res("rh:compound", "Compound_5")),
        _d("Participant_10192_compound_5_in", _res("rh:location", "In"),
           _res("rh:compound", "Compound_5")),
        _compound("5", "CHEBI:15354", "choline", "SmallMolecule",
                  _res("rh:chebi", "15354", chebi)),
        # an obsolete reaction, and a participant the release gives no ChEBI id for
        _reaction("20000", "Reaction", "A = B", "Obsolete", _res("rh:side", "20000_L")),
        _d("20000_L", _res("rh:contains1", "Participant_20000_compound_6")),
        _d("Participant_20000_compound_6", _res("rh:compound", "Compound_6")),
        _reaction("30000", "Reaction", "C = D", "Approved", _res("rh:side", "30000_L")),
        _d("30000_L", _res("rh:contains1", "Participant_30000_compound_7")),
        _d("Participant_30000_compound_7", _res("rh:compound", "Compound_7")),
        _compound("6", "CHEBI:1", "A", "SmallMolecule", _res("rh:chebi", "1", chebi)),
        _compound("7", "UNKNOWN:7", "mystery", "SmallMolecule"),
    ]
    return _RDF_HEAD + "\n".join(parts) + "</rdf:RDF>\n"


@pytest.fixture
def rhea(tmp_path):
    hub = TCMDataHub(tmp_path / "hub")
    raw = hub.raw_dir("rhea")
    _gz(raw / "rhea.rdf.gz", _rhea_rdf())
    (raw / "rhea2uniprot_sprot.tsv").write_text(_tsv(
        ("RHEA_ID", "DIRECTION", "MASTER_ID", "ID"), ("15105", "UN", "15105", "P00001"),
        ("15106", "LR", "15105", "P00001")), encoding="utf-8")
    _gz(raw / "rhea2uniprot_trembl.tsv.gz", _tsv(
        ("RHEA_ID", "DIRECTION", "MASTER_ID", "ID"), ("15108", "BI", "15105", "A0A000")))
    (raw / "chebiId_name.tsv").write_text("CHEBI:58573\t kaempferol\n", encoding="utf-8")
    report = hub.build("rhea", log=lambda m: None)
    return hub, report


def test_rhea_participants_keep_side_role_and_coefficient(rhea):
    hub, report = rhea
    rows = [r for r in _rels(hub, "reaction_participant")]
    by = {(r["subject_id"], r["object_id"], json.loads(r["context"])["action"]): r
          for r in rows}
    assert ("rhea:15105", "chebi:58573", "left side") in by
    assert ("rhea:15106", "chebi:58573", "substrate") in by
    assert ("rhea:15107", "chebi:58573", "product") in by          # right to left
    assert ("rhea:15108", "chebi:58573", "substrate or product") in by
    proton = by[("rhea:15105", "chebi:15378", "right side")]
    assert json.loads(proton["context"])["value"] == "2"
    assert proton["evidence"] == "listed" and proton["effect"] is None
    assert proton["subject_name"].startswith("kaempferol + [protein]-dithiol")


def test_rhea_generic_compounds_polymers_and_transports(rhea):
    hub, report = rhea
    rows = {(r["subject_id"], r["object_id"], json.loads(r["context"])["action"]): r
            for r in _rels(hub, "reaction_participant")}
    generic = rows[("rhea:15105", "rhea.compound:GENERIC:10594", "left side")]
    assert json.loads(generic["context"])["residue"] == "chebi:29950"
    polymer = rows[("rhea:15105", "chebi:133894", "right side")]
    assert polymer["note"] == "POLYMER:12833, polymerization index n"
    out = json.loads(rows[("rhea:10192", "chebi:15354", "left side")]["context"])
    inside = json.loads(rows[("rhea:10192", "chebi:15354", "right side")]["context"])
    assert (out["condition"], inside["condition"]) == ("location Out", "location In")
    assert out["qc"] == "Preliminary"
    # the obsolete reaction has no rows; the unidentified participant is queued
    assert not any(k[0] == "rhea:20000" for k in rows)
    assert report["unresolved"] == 1
    (queued,) = hub.unresolved("rhea")
    assert (queued["subject_id"], queued["object_name"]) == ("rhea:30000", "mystery")


def test_rhea_enzymes_keep_the_direction_uniprot_gives(rhea):
    hub, _ = rhea
    rows = {r["object_id"]: r for r in _rels(hub, "enzyme_reaction")}
    assert set(rows) == {"rhea:15105", "rhea:15106", "rhea:15108"}
    lr = rows["rhea:15106"]
    assert (lr["subject_id"], lr["evidence"], lr["note"]) == (
        "uniprot:P00001", "aggregated", "via UniProtKB")
    assert json.loads(lr["context"]) == {"action": "left to right",
                                         "dataset": "UniProtKB/Swiss-Prot"}
    assert json.loads(rows["rhea:15105"]["context"])["action"] == "undefined direction"
    trembl = rows["rhea:15108"]                     # automatic annotation
    assert trembl["evidence"] == "predicted"
    assert json.loads(trembl["context"])["action"] == "bidirectional"
    assert dataset("rhea").commercial_use == "allowed"


def test_rhea_rebuilds_to_the_same_relations(rhea):
    hub, first = rhea
    assert hub.build("rhea", log=lambda m: None)["relations_digest"] == \
        first["relations_digest"]


# ============================================================================= MetaNetX
_MNX_HEADER = (
    "### MetaNetX/MNXref reconciliation ###\n#Based on the following resources:\n#\n"
    "#RESOURCE:  MetaNetX/MNXref\n#VERSION:   4.5\n#DATE:      2025/08/13\n"
    "#URL:       https://www.metanetx.org\n#LICENSE:\t\n"
    "#\tMetaNetX copyright 2011 SystemsX, SIB Swiss Institute of Bioinformatics\n"
    "#\tlicensed under a Creative Commons Attribution 4.0 International License.\n#\n"
    "#RESOURCE:  enviPath\n#VERSION:   (downloaded on 2021/11/24)\n"
    "#URL:       https://envipath.org\n#LICENSE:\t\n"
    "#\tThe core data sets of enviPath are licensed under the Creative Commons\n"
    "#\tAttribution-NonCommercial-ShareAlike 4.0 International (CC BY-NC-SA 4.0)\n#\n")


def _mnx_tar(path):
    members = {
        "chem_prop.tsv": _MNX_HEADER + _tsv(
            ("#ID", "name", "reference", "formula", "charge", "mass", "InChI", "InChIKey",
             "SMILES"),
            ("MNXM1", "H(+)", "mnx:PROTON", "H", "1", "1.00794", "InChI=1S/p+1",
             "GPRLSGONYQIRFK-UHFFFAOYSA-N", "[H+]"),
            ("MNXM1672", "kaempferol", "chebi:58573", "C15H9O6", "-1", "285.23100",
             "InChI=1S/C15H10O6/p-1", KAEMPFEROL_ANION, "[O-]c1ccccc1"),
            ("BIOMASS", "BIOMASS", "mnx:BIOMASS", "", "", "", "", "", "")),
        "chem_xref.tsv": _MNX_HEADER + _tsv(
            ("#source", "ID", "description"),
            ("hmdb:HMDB0005801", "MNXM1672", "kaempferol||Kaempferol||robigenin"),
            ("hmdb:HMDB05801", "MNXM1672", "secondary/obsolete/fantasy identifier"),
            ("kegg.compound:C05903", "MNXM1672", "Kaempferol"),
            ("keggC:C05903", "MNXM1672", "Kaempferol"),
            ("chebi:28499", "MNXM1672", "kaempferol"),
            ("seed.compound:cpd19040", "MNXM1672", "kaempferol"),
            ("seed.compound:cpd11416", "BIOMASS", "Biomass")),
        "chem_isom.tsv": _MNX_HEADER + _tsv(("#parent", "child", "description"),
                                            ("MNXM100051", "MNXM100344", "a -> b")),
        "chem_depr.tsv": _MNX_HEADER + _tsv(("#deprecated_ID", "ID", "version"),
                                            ("MNXM5000", "MNXM1672", "4.4")),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(path, "w:gz") as tf:
        for name, text in members.items():
            data = text.encode("utf-8")
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))


@pytest.fixture
def metanetx(tmp_path):
    hub = TCMDataHub(tmp_path / "hub")
    _mnx_tar(hub.raw_dir("metanetx") / "mnxref_tsv.tar.gz")
    report = hub.build("metanetx", log=lambda m: None)
    return hub, report


def test_metanetx_loads_its_tables_without_the_bulky_columns(metanetx):
    hub, report = metanetx
    assert report["tables"] == {"chem_prop": 3, "chem_xref": 7, "chem_isom": 1,
                                "chem_depr": 1, "licences": 2}
    assert report["relations"] == {}                     # a mapping is not evidence
    (kae,) = hub.query("metanetx", "chem_prop", where={"ID": "MNXM1672"})
    assert kae["InChIKey"] == KAEMPFEROL_ANION and "InChI" not in kae and "SMILES" not in kae
    flags = {r["source"]: r["secondary_flag"] for r in hub.query("metanetx", "chem_xref")}
    assert flags["hmdb:HMDB05801"] == "secondary/obsolete/fantasy identifier"
    assert flags["hmdb:HMDB0005801"] is None
    licences = {r["resource"]: r for r in hub.query("metanetx", "licences")}
    assert licences["enviPath"]["license"].endswith("(CC BY-NC-SA 4.0)")
    assert licences["MetaNetX/MNXref"]["version"] == "4.5"


def test_metanetx_maps_external_ids_to_the_neutral_inchikey(metanetx):
    hub, _ = metanetx
    cw = Crosswalk(hub).compound
    assert cw["hmdb:HMDB0005801"] == f"inchikey:{KAEMPFEROL}"     # -M read as -N
    assert cw["kegg.compound:C05903"] == f"inchikey:{KAEMPFEROL}"
    for left in ("hmdb:HMDB05801", "keggC:C05903", "chebi:28499",
                 "seed.compound:cpd19040", "seed.compound:cpd11416"):
        assert left not in cw                   # secondary, duplicate or not mapped here
    spec = dataset("metanetx")
    assert spec.commercial_use == "unknown"           # CC BY own content, mixed rows
    assert licence_class(spec.license) == "non-commercial"
    assert "ChEBI" in spec.upstream and "SABIO-RK" in spec.upstream


def test_chebi_keeps_its_own_ids_when_both_are_built(tmp_path):
    hub = TCMDataHub(tmp_path / "hub")
    _chebi(hub.raw_dir("chebi"))
    _mnx_tar(hub.raw_dir("metanetx") / "mnxref_tsv.tar.gz")
    hub.build("chebi", log=lambda m: None)
    hub.build("metanetx", log=lambda m: None)
    cw = Crosswalk(hub).compound
    assert cw["chebi:58573"] == f"inchikey:{KAEMPFEROL_ANION}"
    assert cw["hmdb:HMDB0005801"] == cw["chebi:28499"] == f"inchikey:{KAEMPFEROL}"


# ===================================================================== FoodData Central
_FF = "FoodData_Central_foundation_food_csv_2026-04-30"
_SR = "FoodData_Central_sr_legacy_food_csv_2018-04"


def _csv(*rows):
    return "".join(",".join(f'"{c}"' for c in r) + "\n" for r in rows)


def _zip(path, folder, members):
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{folder}/", "")
        for name, text in members.items():
            zf.writestr(f"{folder}/{name}", text)


_FOOD = ("fdc_id", "data_type", "description", "food_category_id", "publication_date")
_FN = ("id", "fdc_id", "nutrient_id", "amount", "data_points", "derivation_id", "min", "max",
       "median", "footnote", "min_year_acquired")
_NUT = ("id", "name", "unit_name", "nutrient_nbr", "rank")


def _fdc(raw):
    _zip(raw / f"{_FF}.zip", _FF, {
        "food.csv": _csv(_FOOD, ("321358", "foundation_food", "Hummus, commercial", "16",
                                 "2019-04-01"),
                         ("319877", "sub_sample_food", "Hummus", "16", "2019-04-01")),
        "food_nutrient.csv": _csv(
            _FN, ("1", "321358", "1105", "3.0", "1", "1", "", "", "3.0", "", ""),
            ("2", "321358", "1122", "0.0", "1", "1", "", "", "", "", ""),
            ("3", "321358", "1008", "166", "", "49", "", "", "", "", ""),
            ("4", "319877", "1105", "2.5", "1", "1", "", "", "", "", "")),   # a sub-sample
        "nutrient.csv": _csv(_NUT, ("1105", "Retinol", "UG", "319", "7430.0"),
                             ("1122", "Lycopene", "UG", "337", "7530.0"),
                             ("1008", "Energy", "KCAL", "208", "300.0")),
        "food_category.csv": _csv(("id", "code", "description"),
                                  ("16", "1600", "Legumes and Legume Products")),
        "foundation_food.csv": _csv(("fdc_id", "NDB_number", "footnote"),
                                    ("321358", "16158", "")),
    })
    _zip(raw / f"{_SR}.zip", _SR, {
        "food.csv": _csv(_FOOD, ("169231", "sr_legacy_food", "Ginger root, raw", "11",
                                 "2019-04-01")),
        "food_nutrient.csv": _csv(
            _FN, ("10", "169231", "1051", "78.89", "5", "", "70.45", "84.56", "", "", ""),
            ("11", "169231", "2000", "1.7", "0", "67", "", "", "", "", ""),
            ("12", "169231", "1079", "0", "0", "68", "", "", "", "", ""),
            ("13", "169231", "1087", "16", "3", "1", "15", "18", "", "Value; from 3 labs",
             ""),
            ("14", "169231", "1259", "0", "0", "", "", "", "", "", ""),     # codeless 0
            ("15", "169231", "1018", "0", "", "", "", "", "", "", ""),      # no points
            ("16", "169231", "1057", "0", "2", "", "", "", "", "", "")),    # 2 points
        "nutrient.csv": _csv(_NUT, ("1051", "Water", "G", "255", "100.0"),
                             ("1259", "SFA 4:0", "G", "607", "9700.0"),
                             ("1018", "Alcohol, ethyl", "G", "221", "18200.0"),
                             ("1057", "Caffeine", "MG", "262", "18300.0"),
                             ("2000", "Sugars, Total", "G", "269", "1500.0"),
                             ("1079", "Fiber, total dietary", "G", "291", "1200.0"),
                             ("1087", "Calcium, Ca", "MG", "301", "5300.0")),
        "food_category.csv": _csv(("id", "code", "description"),
                                  ("11", "1100", "Vegetables and Vegetable Products")),
        "food_nutrient_derivation.csv": _csv(
            ("id", "code", "description", "source_id"), ("1", "A", "Analytical", "1"),
            ("49", "NC", "Calculated", "2"),
            ("67", "T", "Taken from another source--other tables of food composition", "2"),
            ("68", "Z", "Assumed zero (Insignificant amount or not naturally occurring in "
                        "a food, such as fiber in meat)", "5")),
        "food_nutrient_source.csv": _csv(
            ("id", "code", "description"), ("1", "1", "Analytical or derived from analytical"),
            ("2", "4", "Calculated or imputed"), ("5", "7", "Assumed zero")),
    })


@pytest.fixture
def fdc(tmp_path):
    hub = TCMDataHub(tmp_path / "hub")
    _fdc(hub.raw_dir("fooddata_central"))
    report = hub.build("fooddata_central", log=lambda m: None)
    return hub, report


def test_fdc_reads_csv_members_of_the_release_zips(fdc):
    hub, report = fdc
    assert report["tables"]["ff_food_nutrient"] == 4
    assert report["tables"]["sr_food_nutrient_derivation"] == 4
    assert report["missing"] == []


def test_fdc_evidence_follows_the_derivation_and_zero_is_not_always_a_result(fdc):
    hub, _ = fdc
    rows = {(r["subject_id"], r["object_id"]): r for r in _rels(hub, "food_nutrient")}
    assert set(rows) == {
        ("fdc:food.321358", "fdc:nutrient.1105"), ("fdc:food.321358", "fdc:nutrient.1122"),
        ("fdc:food.321358", "fdc:nutrient.1008"), ("fdc:food.169231", "fdc:nutrient.1051"),
        ("fdc:food.169231", "fdc:nutrient.2000"), ("fdc:food.169231", "fdc:nutrient.1087"),
        ("fdc:food.169231", "fdc:nutrient.1057")}
    # a codeless zero with no data points was never measured: no row, not a negative
    caffeine = rows[("fdc:food.169231", "fdc:nutrient.1057")]
    assert (caffeine["evidence"], caffeine["outcome"]) == ("aggregated", "negative")
    # the sub-sample's lab result and the assumed zero (code Z) have no row
    retinol = rows[("fdc:food.321358", "fdc:nutrient.1105")]
    assert (retinol["evidence"], retinol["outcome"], retinol["object_type"]) == (
        "known", "positive", "nutrient")
    assert json.loads(retinol["context"]) == {
        "dataset": "Foundation Foods", "measure": "per 100 g", "method": "A | Analytical",
        "n": "1", "unit": "UG", "value": "3.0"}
    lycopene = rows[("fdc:food.321358", "fdc:nutrient.1122")]
    assert (lycopene["evidence"], lycopene["outcome"]) == ("known", "negative")
    energy = rows[("fdc:food.321358", "fdc:nutrient.1008")]
    assert energy["evidence"] == "predicted"            # calculated
    water = rows[("fdc:food.169231", "fdc:nutrient.1051")]
    assert water["evidence"] == "aggregated"            # no derivation code
    assert water["note"] == "min 70.45; max 84.56"
    sugars = rows[("fdc:food.169231", "fdc:nutrient.2000")]
    assert sugars["evidence"] == "predicted"
    assert "n" not in json.loads(sugars["context"])     # 0 data points: not a count
    calcium = rows[("fdc:food.169231", "fdc:nutrient.1087")]
    assert calcium["note"] == "min 15; max 18; Value; from 3 labs"
    assert all(r["license"] == "CC0 1.0" for r in rows.values())
    assert dataset("fooddata_central").commercial_use == "allowed"


# =========================================================================== connectors
def test_connectors_send_the_requests_that_were_verified_live():
    from bioagent.providers.public_apis import render_call

    r = render_call("chebi", "compound")
    assert (r["method"], r["path"]) == ("GET", "compound/CHEBI:28499/")
    assert render_call("chebi", "compounds")["params"] == {
        "chebi_ids": "CHEBI:28499,CHEBI:2979"}
    assert render_call("chebi", "search", term="quercetin")["params"] == {
        "term": "quercetin", "page": 1, "size": 5}
    assert render_call("chebi", "parents")["path"] == "ontology/parents/CHEBI:2979/"
    assert render_call("chebi", "children")["path"] == "ontology/children/CHEBI:28499/"
    s = render_call("chebi", "structure_search")["params"]
    assert (s["search_type"], s["similarity"], s["three_star_only"]) == (
        "similarity", 0.8, "true")
    rhea = render_call("rhea", "search")
    assert (rhea["method"], rhea["path"], rhea["accept"]) == (
        "GET", "rhea", "text/tab-separated-values")
    assert rhea["params"] == {"query": "chebi:58573",
                              "columns": "rhea-id,equation,chebi-id,ec,uniprot,pubmed",
                              "format": "tsv", "limit": 5}
    assert render_call("sabio_rk", "kinlaw_search")["params"] == {
        "q": "Inhibitor:quercetin", "page": 1, "pageSize": 5}
    assert render_call("sabio_rk", "kinlaw_entry")["path"] == "kinlaw-entry/json/5182"
    assert render_call("sabio_rk", "compound")["path"] == "compound/5066"
    assert render_call("sabio_rk", "enzyme_by_ec")["path"] == "enzyme/by-ec/1.14.18.1"
    assert render_call("sabio_rk", "protein")["path"] == "uniprot/by-uniprot-id/P00439"
    mnx = render_call("metanetx", "chem_by_xref")
    assert (mnx["method"], mnx["path"], mnx["accept"]) == (
        "POST", "sparql/", "application/sparql-results+json")
    assert "mnx:chemXref <https://identifiers.org/hmdb:HMDB0005801>" in mnx["form"]["query"]
    chem = render_call("metanetx", "chem", mnx_id="MNXM1")["form"]["query"]
    assert "<https://rdf.metanetx.org/chem/MNXM1>" in chem and "{mnx_id}" not in chem


def test_every_connector_host_is_allowed_and_rate_limited():
    from bioagent.backends.http import DEFAULT_RATES
    from bioagent.policy import PROFILES, PolicyDecision
    from bioagent.providers.public_apis import BY_KEY
    from bioagent.providers.supplement import chem_onto

    profile = PROFILES["biomedical-research"]
    for source in chem_onto.SOURCES:
        assert BY_KEY[source.key] is source
        assert profile.check_network([source.host]).decision == PolicyDecision.ALLOW, source.host
        assert source.host in DEFAULT_RATES
    assert DEFAULT_RATES["rdf.metanetx.org"] <= 0.1          # robots.txt Crawl-delay: 10
    assert DEFAULT_RATES["sabiork.h-its.org"] <= 1.0         # 60 requests a minute
    assert licence_class(BY_KEY["sabio_rk"].license) == "non-commercial"
    for key in ("chebi", "rhea", "metanetx", "fooddata_central"):
        for f in dataset(key).files:
            host = f.url.split("/")[2]
            assert profile.check_network([host]).decision == PolicyDecision.ALLOW, host
