"""NPASS, CMAUP and LOTUS as hub datasets (tcmdb.extra.np_activity).

The hub's cards named NPASS and CMAUP with no dataset behind them and LOTUS not at all, so
the hub could find these sources but not fetch, build or query them. Here each is built
from small files with the real headers, and read as the snapshot parsers read them.
"""
from __future__ import annotations

import gzip
import json

import pytest

from bioagent.acquisition.sources import ACQUIRABLE
from bioagent.tcmdb import TCMDataHub, catalog, dataset

GLYCYRRHIZIN = "LPLVUJXQOOQHMX-QWBHMCJMSA-N"
LIQUIRITIN = "DEMKZLAVQYISIA-ZRWXNEIDSA-N"


def _tsv(path, header, rows):
    path.write_text("\n".join(["\t".join(header), *("\t".join(r) for r in rows)]) + "\n",
                    encoding="utf-8")


def _npass(raw):
    _tsv(raw / "NPASSv2.0_download_naturalProducts_generalInfo.txt",
         ["np_id", "pref_name", "iupac_name", "chembl_id", "pubchem_cid"],
         [["NPC1", "glycyrrhizin", "", "CHEMBL441687", "14982"],
          ["NPC2", LIQUIRITIN, "", "", ""],          # NPASS puts the key where a name goes
          ["NPC3", "an unidentified saponin", "", "", ""]])
    _tsv(raw / "NPASSv2.0_download_naturalProducts_structureInfo.txt",
         ["np_id", "InChI", "InChIKey", "SMILES"],
         [["NPC1", "InChI=1S/x", GLYCYRRHIZIN, "C"], ["NPC2", "InChI=1S/y", LIQUIRITIN, "C"]])
    _tsv(raw / "NPASSv2.0_download_naturalProducts_speciesInfo.txt",
         ["org_id", "org_name", "org_tax_level", "org_tax_id", "species_tax_id", "species_name"],
         [["NPO1", "Glycyrrhiza uralensis", "species", "74649", "74649", "Glycyrrhiza uralensis"],
          ["NPO2", "an unplaced sample", "", "", "", ""]])
    pair = ["org_id", "np_id", "org_isolation_part", "org_collect_location", "ref_type", "ref_id",
            "ref_id_type"]
    _tsv(raw / "NPASSv2.0_download_naturalProducts_species_pair.txt", pair,
         [["NPO1", "NPC1", "root", "Gansu", "Literature", "123", "PMID"],
          ["NPO1", "NPC1", "root", "Gansu", "Literature", "123", "PMID"],   # an exact repeat
          ["NPO1", "NPC1", "rhizome", "", "Literature", "10.1/abc", "DOI"],
          ["NPO1", "NPC2", "", "", "Database", "TCM-ID", ""],             # relayed, not read
          ["NPO2", "NPC3", "", "", "", "", ""]])
    _tsv(raw / "NPASSv2.0_download_naturalProducts_targetInfo.txt",
         ["target_id", "target_type", "target_name", "target_organism", "uniprot_id"],
         [["NPT1", "Individual protein", "Cyclooxygenase-2", "Homo sapiens", "P35354"],
          ["NPT2", "Cell line", "HepG2", "Homo sapiens", ""]])
    act = ["np_id", "target_id", "activity_type", "activity_relation", "activity_value",
           "activity_units", "assay_organism", "assay_tissue", "assay_cell_type", "ref_id",
           "ref_id_type"]
    _tsv(raw / "NPASSv2.0_download_naturalProducts_activities.txt", act,
         [["NPC1", "NPT1", "IC50", "=", "12.5", "nM", "Homo sapiens", "", "", "456", "PMID"],
          ["NPC1", "NPT1", "IC50", "=", "12.5", "nM", "Homo sapiens", "", "", "456", "PMID"],
          ["NPC1", "NPT1", "Ki", "<", "10", "uM", "", "", "", "10.2/def", "DOI"],
          ["NPC1", "NPT2", "IC50", "=", "3", "uM", "", "liver", "HepG2", "789", "PMID"],  # a cell
          ["NPC2", "NPT1", "IC50", "=", "40", "uM", "", "", "", "", ""]])              # no ref


def _cmaup(raw):
    _tsv(raw / "CMAUPv2.0_download_Plants.txt",
         ["Plant_ID", "Plant_Name", "Species_Tax_ID", "Species_Name", "Genus_Name", "Family_Name"],
         [["NPO1001", "Gan Cao", "74649", "Glycyrrhiza uralensis", "Glycyrrhiza", "Fabaceae"]])
    _tsv(raw / "CMAUPv2.0_download_Ingredients_All.txt",
         ["np_id", "pref_name", "InChIKey", "pubchem_cid", "chembl_id"],
         [["NPC1", "glycyrrhizin", GLYCYRRHIZIN, "14982", ""]])
    (raw / "CMAUPv2.0_download_Plant_Ingredient_Associations_allIngredients.txt").write_text(
        "NPO1001\tNPC1\nNPO1001\tNPC1\n", encoding="utf-8")         # headerless, repeated
    _tsv(raw / "CMAUPv2.0_download_Targets.txt",
         ["Target_ID", "Gene_Symbol", "Protein_Name", "Uniprot_ID"],
         [["NPT1", "PTGS2", "Cyclooxygenase-2", "P35354"]])
    _tsv(raw / "CMAUPv2.0_download_Ingredient_Target_Associations_ActivityValues_References.txt",
         ["Ingredient_ID", "Target_ID", "Activity_Type", "Activity_Relationship",
          "Activity_Value", "Activity_Unit", "Reference_ID", "Reference_ID_Type"],
         [["NPC1", "NPT1", "IC50", "=", "15", "nM", "10.3/ghi", "DOI"]])


def _lotus(raw):
    header = ["structure_inchikey", "structure_nameTraditional", "organism_name",
              "organism_taxonomy_ncbiid", "organism_wikidata", "reference_doi",
              "manual_validation"]
    rows = [[GLYCYRRHIZIN, "glycyrrhizin", "Glycyrrhiza uralensis", "74649",
             "http://www.wikidata.org/entity/Q1505406", "10.4/one", "Y"],
            [GLYCYRRHIZIN, "glycyrrhizin", "Glycyrrhiza uralensis", "74649",
             "http://www.wikidata.org/entity/Q1505406", "10.4/two", "N"],
            [LIQUIRITIN, "liquiritin", "Glycyrrhiza glabra", "",
             "http://www.wikidata.org/entity/Q257106", "10.4/three", ""],
            ["not-a-key", "x", "Glycyrrhiza glabra", "49827", "", "", ""]]
    text = "\n".join(",".join(r) for r in [header, *rows]) + "\n"
    (raw / "260413_frozen.csv.gz").write_bytes(gzip.compress(text.encode("utf-8")))


@pytest.fixture
def hub(tmp_path) -> TCMDataHub:
    h = TCMDataHub(tmp_path / "hub")
    for key, writer in (("npass", _npass), ("cmaup", _cmaup), ("lotus", _lotus)):
        h.raw_dir(key).mkdir(parents=True, exist_ok=True)
        writer(h.raw_dir(key))
        h.build(key, log=lambda m: None)
    return h


def _by(rows, *keys):
    return {tuple(r[k] for k in keys): r for r in rows}


def test_the_specs_take_their_files_from_the_reviewed_acquisition_table():
    reviewed = {a.filename: a for a in ACQUIRABLE}
    for key, project in (("npass", "NPASS"), ("cmaup", "CMAUP"), ("lotus", "LOTUS")):
        spec = dataset(key)
        assert {f.name for f in spec.files} == {a.filename for a in ACQUIRABLE
                                                if a.source_project == project}
        for f in spec.files:
            assert (f.url, f.expected_bytes) == (reviewed[f.name].url, reviewed[f.name].expected_bytes)
    assert dataset("lotus").file("metadata").optional, "90 MB, fetched only on request"
    assert dataset("cmaup").file("pairs").columns == ("plant_id", "np_id"), "headerless"
    assert dataset("lotus").license == "CC-BY-4.0" and dataset("npass").commercial_use == "unknown"


def test_the_cards_point_at_the_datasets_and_lotus_has_one():
    cards = {c.no: c for c in catalog()}
    assert (cards[5].dataset, cards[6].dataset) == ("npass", "cmaup")
    assert cards[137].name.startswith("LOTUS") and cards[137].dataset == "lotus"
    assert cards[137].connector == "wikidata_sparql" and cards[137].commercial_use == "allowed"


def test_npass_gives_occurrences_and_measured_activities_as_its_parser_reads_them(hub):
    occ = _by(hub.relations("organism_compound", sources=["npass"]), "subject_id", "object_id")
    found = occ[("ncbitaxon:74649", f"inchikey:{GLYCYRRHIZIN}")]
    assert found["evidence"] == "known" and found["reference"] == "doi:10.1/abc | pmid:123"
    assert json.loads(found["context"]) == {"part": "rhizome | root"}
    relayed = occ[("ncbitaxon:74649", f"inchikey:{LIQUIRITIN}")]
    assert relayed["evidence"] == "aggregated" and relayed["note"] == "via TCM-ID"
    assert relayed["object_name"] == LIQUIRITIN, "the key stands in for a missing name"
    unplaced = occ[("npass:NPO2", "npass:NPC3")]
    assert unplaced["evidence"] == "known" and unplaced["reference"] is None
    assert len(occ) == 3

    act = hub.relations("compound_target", sources=["npass"])
    assert {(r["object_id"], r["reference"]) for r in act} == {
        ("uniprot:P35354", "pmid:456"), ("uniprot:P35354", "doi:10.2/def")}, \
        "one row for the repeat; none for a cell line or an activity without a PMID or DOI"
    ic50 = next(r for r in act if r["reference"] == "pmid:456")
    assert ic50["evidence"] == "known" and ic50["subject_id"] == f"inchikey:{GLYCYRRHIZIN}"
    assert json.loads(ic50["context"]) == {"measure": "IC50", "value": "12.5", "unit": "nM",
                                           "species": "Homo sapiens"}
    ki = next(r for r in act if r["reference"] == "doi:10.2/def")
    assert json.loads(ki["context"])["value"] == "<10", "the qualifier is kept"


def test_cmaup_pairs_carry_no_reference_and_its_activities_meet_npass_on_the_inchikey(hub):
    occ = hub.relations("organism_compound", sources=["cmaup"])
    assert len(occ) == 1 and occ[0]["evidence"] == "aggregated"
    assert (occ[0]["subject_id"], occ[0]["object_id"]) == ("ncbitaxon:74649", f"inchikey:{GLYCYRRHIZIN}")
    both = hub.relations("compound_target", subject=f"inchikey:{GLYCYRRHIZIN}",
                         sources=["npass", "cmaup"])
    assert {r["source"] for r in both} == {"npass", "cmaup"}
    assert {r["object_id"] for r in both} == {"uniprot:P35354"}


def test_lotus_pairs_are_known_when_validated_and_reported_otherwise(hub):
    occ = _by(hub.relations("organism_compound", sources=["lotus"]), "subject_id", "object_id")
    validated = occ[("ncbitaxon:74649", f"inchikey:{GLYCYRRHIZIN}")]
    assert validated["evidence"] == "known"
    assert validated["reference"] == "doi:10.4/one | doi:10.4/two", "one pair, both references"
    unplaced = occ[("lotus:Q257106", f"inchikey:{LIQUIRITIN}")]
    assert unplaced["evidence"] == "reported" and unplaced["object_name"] == "liquiritin"
    assert len(occ) == 2, "a row without an InChIKey yields nothing"


def test_a_raw_file_that_differs_from_the_reviewed_one_is_a_problem(hub):
    report = hub.check("npass")
    assert any("sha256 differs from the reviewed file's" in p for p in report["problems"])
