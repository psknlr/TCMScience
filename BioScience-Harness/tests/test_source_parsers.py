"""Source parsers, the herb layer and the gold build, on synthetic files.

The files use the real column layouts of NPASS 2.0, CMAUP 2.0, the LOTUS frozen export
and BindingDB's TSV (read from the providers on 2026-09-23); the rows are invented, so
no upstream data is redistributed with the tests. Identifiers of the gold compounds and
species are the real ones (InChIKeys checked against PubChem, taxa against NCBI).
"""

from __future__ import annotations

import gzip
import zipfile
from pathlib import Path

import pytest

from bioagent.sources import load_snapshot, validate_edge, validate_node
from bioagent.sources.build import build_gold, build_source, require_gold
from bioagent.sources.composition import herb_composition
from bioagent.sources.herbs import GEGEN_QINLIAN, GOLD, HERBS, herb_rows, taxon_filter
from bioagent.sources.parsers import (TaxonFilter, parse_bindingdb, parse_cmaup, parse_lotus,
                                      parse_npass)
from bioagent.sources.parsers.common import parse_measure, publication
from bioagent.sources.snapshot import SnapshotError

PUERARIN = GOLD["tcm:herb.gegen"][1]
BAICALIN = GOLD["tcm:herb.huangqin"][1]
BERBERINE = GOLD["tcm:herb.huanglian"][1]
GLYCYRRHIZIN = GOLD["tcm:herb.gancao"][1]
OTHER = "FJYKMMKDORAAAA-VPRICQMDSA-N"


def tsv(path: Path, header: list[str] | None, rows: list[list[str]]) -> None:
    lines = (["\t".join(header)] if header else []) + ["\t".join(r) for r in rows]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def npass_files(d: Path) -> Path:
    tsv(d / "NPASSv2.0_download_naturalProducts_speciesInfo.txt",
        ["org_id", "org_name", "org_tax_level", "org_tax_id", "subspecies_tax_id",
         "subspecies_name", "species_tax_id", "species_name", "genus_tax_id", "genus_name",
         "family_tax_id", "family_name", "kingdom_tax_id", "kingdom_name",
         "superkingdom_tax_id", "superkingdom_name"],
        [["NPO30303", "Pueraria lobata", "Species", "3893", "n.a.", "n.a.", "3893",
          "Pueraria montana var. lobata", "3892", "Pueraria", "3803", "Fabaceae",
          "33090", "Viridiplantae", "2759", "Eukaryota"],
         ["NPO5649.1", "Coptis chinensis var. brevisepala", "Varieties", "1054456", "n.a.",
          "n.a.", "261450", "Coptis chinensis", "n.a.", "n.a.", "n.a.", "n.a.", "n.a.",
          "n.a.", "n.a.", "n.a."],
         ["NPO5649", "Coptis chinensis", "Species", "261450", "n.a.", "n.a.", "261450",
          "Coptis chinensis", "n.a.", "n.a.", "n.a.", "n.a.", "n.a.", "n.a.", "n.a.", "n.a."]])
    tsv(d / "NPASSv2.0_download_naturalProducts_species_pair.txt",
        ["src_org_pair", "org_id", "np_id", "new_cp_found", "org_isolation_part",
         "org_collect_location", "org_collect_time", "ref_type", "ref_id", "ref_id_type",
         "ref_url"],
        [["NPO30303-NPC1", "NPO30303", "NPC1", "N", "Root", "n.a.", "n.a.", "Publication",
          "12345", "PMID", "n.a."],
         ["NPO5649-NPC2", "NPO5649", "NPC2", "n.a.", "n.a.", "n.a.", "n.a.", "Database",
          "TM-MC", "Database", "n.a."],
         ["NPO5649.1-NPC3", "NPO5649.1", "NPC3", "n.a.", "Leaf", "n.a.", "n.a.",
          "Publication", "54321", "PMID", "n.a."]])
    tsv(d / "NPASSv2.0_download_naturalProducts_structureInfo.txt",
        ["np_id", "InChI", "InChIKey", "SMILES"],
        [["NPC1", "InChI=1S/x", PUERARIN, "C"], ["NPC2", "InChI=1S/y", BERBERINE, "C"],
         ["NPC3", "InChI=1S/z", OTHER, "C"]])
    tsv(d / "NPASSv2.0_download_naturalProducts_generalInfo.txt",
        ["np_id", "pref_name", "iupac_name", "chembl_id", "pubchem_cid", "num_of_organism",
         "num_of_target", "num_of_activity", "if_has_Quantity"],
        [["NPC1", "Puerarin", "n.a.", "CHEMBL1", "5281807", "1", "1", "2", "No"],
         ["NPC2", BERBERINE, "n.a.", "n.a.", "2353", "1", "0", "0", "No"],
         ["NPC3", "Other", "n.a.", "n.a.", "n.a.", "1", "0", "0", "No"]])
    tsv(d / "NPASSv2.0_download_naturalProducts_targetInfo.txt",
        ["target_id", "target_type", "target_name", "target_organism_tax_id",
         "target_organism", "uniprot_id"],
        [["NPT1", "Individual Protein", "Cyclooxygenase-2", "9606", "Homo sapiens", "P35354"],
         ["NPT2", "Cell Line", "HCT-116", "9606", "Homo sapiens", ""]])
    tsv(d / "NPASSv2.0_download_naturalProducts_activities.txt",
        ["np_id", "target_id", "activity_type_grouped", "activity_relation", "activity_type",
         "activity_value", "activity_units", "assay_organism", "assay_tax_id", "assay_strain",
         "assay_tissue", "assay_cell_type", "ref_id", "ref_id_type"],
        [["NPC1", "NPT1", "IC50", "=", "IC50", "49000.0", "nM", "Homo sapiens", "9606",
          "n.a.", "n.a.", "n.a.", "447201", "PMID"],
         ["NPC1", "NPT2", "IC50", "=", "IC50", "10", "uM", "Homo sapiens", "9606",
          "n.a.", "n.a.", "HCT-116", "447201", "PMID"],
         ["NPC1", "NPT1", "Ki", ">", "Ki", "100", "nM", "n.a.", "n.a.", "n.a.", "n.a.",
          "n.a.", "n.a.", "n.a."]])
    return d


def cmaup_files(d: Path) -> Path:
    tsv(d / "CMAUPv2.0_download_Plants.txt",
        ["Plant_ID", "Plant_Name", "Species_Tax_ID", "Species_Name", "Genus_Tax_ID",
         "Genus_Name", "Family_Tax_ID", "Family_Name"],
        [["NPO28877", "Scutellaria Baicalensis", "65409", "Scutellaria baicalensis",
          "n.a.", "Scutellaria", "n.a.", "Lamiaceae"],
         ["NPO9", "Other plant", "12", "Other plant", "NA", "NA", "NA", "NA"]])
    tsv(d / "CMAUPv2.0_download_Plant_Ingredient_Associations_allIngredients.txt", None,
        [["NPO28877", "NPC10"], ["NPO9", "NPC11"]])
    tsv(d / "CMAUPv2.0_download_Ingredients_All.txt",
        ["np_id", "pref_name", "iupac_name", "chembl_id", "pubchem_cid", "MW", "LogS",
         "LogD", "LogP", "nHA", "nHD", "TPSA", "nRot", "nRing", "InChI", "InChIKey", "SMILES"],
        [["NPC10", "Baicalin", "n.a.", "n.a.", "64982"] + ["0"] * 9 + ["InChI=1S/b", BAICALIN, "C"],
         ["NPC11", "x", "n.a.", "n.a.", "n.a."] + ["0"] * 9 + ["InChI=1S/c", OTHER, "C"]])
    tsv(d / "CMAUPv2.0_download_Targets.txt",
        ["Target_ID", "Gene_Symbol", "Protein_Name", "Uniprot_ID", "ChEMBL_ID", "TTD_ID",
         "if_DTP", "if_CYP", "if_therapeutic_target", "Target_Class_Level1",
         "Target_Class_Level2", "Target_Class_Level3", "Target_Class_level_displayed",
         "Target_type"],
        [["NPT598", "RORB", "Nuclear receptor ROR-beta", "Q92753", "CHEMBL1", "NA", "0", "0",
          "1", "a", "b", "c", "c", "Therapeutic Target"]])
    tsv(d / "CMAUPv2.0_download_Ingredient_Target_Associations_ActivityValues_References.txt",
        ["Ingredient_ID", "Target_ID", "Activity_Type", "Activity_Relationship",
         "Activity_Value", "Activity_Unit", "Reference_ID", "Reference_ID_Type"],
        [["NPC10", "NPT598", "Ki", "=", "21000", "nM", "447238", "PMID"],
         ["NPC10", "NPT598", "Ki", "=", "4600", "nM", "NA", "NA"]])
    return d


def lotus_file(d: Path) -> Path:
    path = d / "260413_frozen.csv.gz"
    rows = [
        "structure_inchikey,organism_name,reference_doi,manual_validation,organism_wikidata,"
        "structure_wikidata,reference_wikidata",
        f"{GLYCYRRHIZIN},Glycyrrhiza uralensis,10.1021/NP0001,,"
        "http://www.wikidata.org/entity/Q1,http://www.wikidata.org/entity/Q2,"
        "http://www.wikidata.org/entity/Q3",
        f"{GLYCYRRHIZIN},Glycyrrhiza uralensis,10.1021/NP0002,Y,"
        "http://www.wikidata.org/entity/Q1,http://www.wikidata.org/entity/Q2,"
        "http://www.wikidata.org/entity/Q4",
        f"{PUERARIN},Pueraria lobata,10.1021/NP0003,,"
        "http://www.wikidata.org/entity/Q5,http://www.wikidata.org/entity/Q6,"
        "http://www.wikidata.org/entity/Q7",
        f"{OTHER},Pueraria montana var. thomsonii,10.1021/NP0004,,"
        "http://www.wikidata.org/entity/Q8,http://www.wikidata.org/entity/Q9,"
        "http://www.wikidata.org/entity/Q10",
    ]
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        fh.write("\n".join(rows) + "\n")
    return path


@pytest.fixture
def raw(tmp_path):
    d = tmp_path / "raw"
    d.mkdir()
    npass_files(d)
    cmaup_files(d)
    lotus_file(d)
    return d


def _sound(result):
    for n in result.nodes:
        assert validate_node(n) == [], n
    for e in result.edges:
        assert validate_edge(e) == [], e


# ================================================================ helpers

def test_references_and_measures_are_normalised():
    assert publication("12345", "PMID") == "pmid:12345"
    assert publication("10.1021/ABC", "DOI") == "doi:10.1021/abc"
    assert publication("TM-MC", "Database") is None
    assert publication("n.a.", "PMID") is None
    assert parse_measure("Ki", ">10000", "nM") == {"type": "Ki", "relation": ">",
                                                  "value": 10000.0, "unit": "nM"}
    assert parse_measure("IC50", "n.a.", "nM") is None


# ================================================================ NPASS

def test_npass_keeps_the_records_own_taxon_and_labels_edges(raw):
    result = parse_npass(raw, taxa=taxon_filter())
    _sound(result)
    ids = {n["id"] for n in result.nodes}
    assert {"ncbitaxon:3893", "ncbitaxon:261450", f"inchikey:{PUERARIN}",
            f"inchikey:{BERBERINE}", "uniprot:P35354"} <= ids
    # the variety matched only through its parent species is not a source of 黄连
    assert "ncbitaxon:1054456" not in ids and f"inchikey:{OTHER}" not in ids
    contains = {e["source_record_id"]: e for e in result.edges if e["predicate"] == "contains"}
    root = contains["NPO30303-NPC1"]
    assert root["publications"] == ["pmid:12345"] and root["raw"]["parts"] == ["Root"]
    via_db = contains["NPO5649-NPC2"]
    assert via_db["primary_knowledge_source"] == "tm-mc"
    assert via_db["aggregator_knowledge_source"] == "npass"
    activity = [e for e in result.edges if e["predicate"] == "targets"]
    assert len(activity) == 1 and activity[0]["measure"]["value"] == 49000.0
    dropped = result.report.dropped
    assert dropped["activity against a non-protein or unmapped target"] == 1
    assert dropped["activity without a PMID or DOI"] == 1


def test_npass_without_a_filter_reads_everything(raw):
    result = parse_npass(raw)
    assert "ncbitaxon:1054456" in {n["id"] for n in result.nodes}


# ================================================================ CMAUP

def test_cmaup_pairs_carry_no_reference_and_uncited_activities_are_dropped(raw):
    result = parse_cmaup(raw, taxa=taxon_filter())
    _sound(result)
    pair = [e for e in result.edges if e["predicate"] == "contains"]
    assert len(pair) == 1 and pair[0]["publications"] == []
    assert pair[0]["object"] == f"inchikey:{BAICALIN}"
    assert [e["measure"]["value"] for e in result.edges if e["predicate"] == "targets"] == [21000.0]
    assert result.report.dropped["activity without a PMID or DOI"] == 1


# ================================================================ LOTUS

def test_lotus_matches_synonyms_merges_references_and_keeps_validation(raw):
    result = parse_lotus(raw / "260413_frozen.csv.gz", taxa=taxon_filter())
    _sound(result)
    edges = {(e["subject"], e["object"]): e for e in result.edges}
    licorice = edges[("ncbitaxon:74613", f"inchikey:{GLYCYRRHIZIN}")]
    assert licorice["publications"] == ["doi:10.1021/np0001", "doi:10.1021/np0002"]
    assert licorice["agent_type"] == "manual_validation_of_automated_agent"
    assert ("ncbitaxon:3893", f"inchikey:{PUERARIN}") in edges        # "Pueraria lobata"
    assert all("thomsonii" not in n["name"] for n in result.nodes)    # 粉葛 is not 葛根


# ================================================================ BindingDB

_BDB_HEADER = [
    "BindingDB Reactant_set_id", "Ligand SMILES", "Ligand InChI", "Ligand InChI Key",
    "BindingDB MonomerID", "BindingDB Ligand Name", "Target Name",
    "Target Source Organism According to Curator or DataSource", "Ki (nM)", "IC50 (nM)",
    "Kd (nM)", "EC50 (nM)", "kon (M-1-s-1)", "koff (s-1)", "pH", "Temp (C)",
    "Curation/DataSource", "Article DOI", "BindingDB Entry DOI", "PMID", "PubChem AID",
    "Patent Number", "Authors", "Institution", "Link to Ligand in BindingDB",
    "Link to Target in BindingDB", "Link to Ligand-Target Pair in BindingDB",
    "Ligand HET ID in PDB", "PDB ID(s) for Ligand-Target Complex", "PubChem CID",
    "PubChem SID", "ChEBI ID of Ligand", "ChEMBL ID of Ligand", "DrugBank ID of Ligand",
    "IUPHAR_GRAC ID of Ligand", "KEGG ID of Ligand", "ZINC ID of Ligand",
    "Number of Protein Chains in Target (>1 implies a multichain complex)",
    "BindingDB Target Chain Sequence", "PDB ID(s) of Target Chain",
    "UniProt (SwissProt) Recommended Name of Target Chain",
    "UniProt (SwissProt) Entry Name of Target Chain",
    "UniProt (SwissProt) Primary ID of Target Chain",
]


def _bdb_row(reactant, inchikey, *, ki="", ic50="", curation="BindingDB", pmid="", doi="",
             chains=(("", ""),)):
    row = [reactant, "C", "InChI=1S/x", inchikey, "50001", "ligand", "target", "Homo sapiens",
           ki, ic50, "", "", "", "", "", "", curation, doi, "", pmid, "", "", "", "", "", "",
           "", "", "", "5281807", "", "", "", "", "", "", "", str(len(chains))]
    for seq, acc in chains:
        row += [seq or "MSEQ", "", "name", "entry", acc]
    return row


def test_bindingdb_filters_by_inchikey_licenses_per_record_and_reads_the_chain(tmp_path):
    rows = [
        _bdb_row("1", PUERARIN, ki=">10000", pmid="111", chains=(("", "P35354"),)),
        _bdb_row("2", PUERARIN, ic50="30", curation="ChEMBL", doi="10.1/X",
                 chains=(("", ""), ("", "P23219"))),          # accession on the second chain
        _bdb_row("3", PUERARIN, ki="5", chains=(("", "P35354"),)),   # no reference
        _bdb_row("4", OTHER, ki="1", pmid="1", chains=(("", "P35354"),)),
        _bdb_row("5", PUERARIN, ki="1", pmid="2", chains=(("", ""),)),
    ]
    path = tmp_path / "BindingDB_All_202609_tsv.zip"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("BindingDB_All.tsv", "\n".join("\t".join(r) for r in [_BDB_HEADER] + rows))
    result = parse_bindingdb(path, inchikeys=[PUERARIN])
    _sound(result)
    by_id = {e["source_record_id"]: e for e in result.edges}
    assert by_id["1|Ki"]["measure"] == {"type": "Ki", "relation": ">", "value": 10000.0,
                                        "unit": "nM"}
    assert by_id["1|Ki"]["license"] == "CC-BY-4.0"
    chembl = by_id["2|IC50"]
    assert chembl["license"] == "CC-BY-SA-3.0" and chembl["object"] == "uniprot:P23219"
    assert chembl["primary_knowledge_source"] == "chembl"
    assert set(by_id) == {"1|Ki", "2|IC50"}
    assert result.report.dropped["measurement without a PMID, DOI or patent"] == 1
    assert result.report.dropped["target chain without a UniProt accession"] == 1


# ================================================================ herbs, composition, gold

def test_the_formula_is_bound_to_its_text_and_the_herb_layer_is_sound():
    nodes, edges = herb_rows()
    for n in nodes:
        assert validate_node(n) == [], n
    for e in edges:
        assert validate_edge(e) == [], e
    components = [e for e in edges if e["subject"] == GEGEN_QINLIAN.id]
    assert [e["raw"]["dose"] for e in components] == ["半斤", "三两", "三两", "二两"]
    assert GEGEN_QINLIAN.fingerprint.startswith("sha256:")
    assert HERBS["tcm:herb.huanglian"].matches_part(["Rhizome"])
    assert not HERBS["tcm:herb.huanglian"].matches_part(["Leaf"])


def test_the_gold_build_reproduces_the_answer_key_and_labels_composition(raw, tmp_path):
    build = require_gold(build_gold(raw, tmp_path / "snap"))
    assert set(build.snapshots) == {"tcm_herbs", "npass", "cmaup", "lotus"}
    hits = {(h["herb"], h["compound"]): h for h in build.composition}
    puerarin = hits[("tcm:herb.gegen", f"inchikey:{PUERARIN}")]
    assert puerarin["level"] == "C2"                     # NPASS recorded the root
    assert puerarin["sources"] == ["lotus", "npass"]
    assert hits[("tcm:herb.huanglian", f"inchikey:{BERBERINE}")]["level"] == "C1"
    for snap in build.snapshots.values():
        again = load_snapshot(tmp_path / "snap", snap.key, snap.version,
                              expected_id=snap.snapshot_id, accept_review=True)
        assert again.snapshot_id == snap.snapshot_id
    npass_manifest = build.snapshots["npass"].manifest["content"]
    assert npass_manifest["version"].startswith("2.0+subset-")
    assert npass_manifest["extra"]["parse_report"]["source"] == "npass"


def test_a_missing_marker_fails_the_gold_standard(raw, tmp_path):
    (raw / "260413_frozen.csv.gz").unlink()
    lotus = raw / "260413_frozen.csv.gz"
    with gzip.open(lotus, "wt", encoding="utf-8") as fh:
        fh.write("structure_inchikey,organism_name,reference_doi\n")
    build = build_gold(raw, tmp_path / "snap")
    assert not build.passed and "tcm:herb.gancao" in build.missing
    with pytest.raises(SnapshotError, match="glycyrrhizic acid"):
        require_gold(build)


def test_the_same_raw_files_give_the_same_snapshot(raw, tmp_path):
    a = build_source("npass", raw, tmp_path / "a", taxa=taxon_filter())
    b = build_source("npass", raw, tmp_path / "b", taxa=taxon_filter())
    full = build_source("npass", raw, tmp_path / "c")
    assert a.snapshot_id == b.snapshot_id != full.snapshot_id
    assert full.version == "2.0"


def test_composition_merges_sources_on_the_inchikey(raw, tmp_path):
    build = build_gold(raw, tmp_path / "snap")
    snaps = list(build.snapshots.values())
    assert herb_composition(snaps) and all(
        h.compound.startswith("inchikey:") for h in herb_composition(snaps))


def test_taxon_filter_matches_ids_and_names():
    f = TaxonFilter({"3893": ("Pueraria montana var. lobata", "Pueraria lobata")})
    assert f.by_id(None, "3893") == "3893" and f.by_id("174648") is None
    assert f.by_name("pueraria LOBATA") == "3893" and f.by_name("Pueraria montana") is None


# ================================================================ STRING and Reactome

def _gz(path: Path, text: str) -> None:
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        fh.write(text)


def string_files(d: Path) -> Path:
    _gz(d / "9606.protein.aliases.v12.0.txt.gz",
        "#string_protein_id\talias\tsource\n"
        "9606.ENSP1\tP35354\tUniProt_AC\n9606.ENSP1\t5743\tEnsembl_HGNC_entrez_id\n"
        "9606.ENSP2\tP23219\tUniProt_AC\n9606.ENSP3\tQ92753\tUniProt_AC\n"
        "9606.ENSP4\tNOTANACC\tUniProt_AC\n")
    _gz(d / "9606.protein.info.v12.0.txt.gz",
        "#string_protein_id\tpreferred_name\tprotein_size\tannotation\n"
        "9606.ENSP1\tPTGS2\t604\tx\n9606.ENSP2\tPTGS1\t599\tx\n9606.ENSP3\tRORB\t459\tx\n")
    _gz(d / "9606.protein.links.v12.0.txt.gz",
        "protein1 protein2 combined_score\n"
        "9606.ENSP1 9606.ENSP2 950\n9606.ENSP2 9606.ENSP1 950\n"
        "9606.ENSP1 9606.ENSP3 150\n9606.ENSP3 9606.ENSP4 999\n")
    return d


def test_string_keeps_the_induced_subnetwork_as_predictions_with_raw_scores(tmp_path):
    from bioagent.sources.parsers import parse_string

    d = string_files(tmp_path)
    result = parse_string(d, proteins=["P35354", "P23219"])
    _sound(result)
    assert len(result.edges) == 1                       # listed twice, kept once
    e = result.edges[0]
    assert (e["subject"], e["object"]) == ("uniprot:P23219", "uniprot:P35354")
    assert e["knowledge_level"] == "prediction" and e["study_design"] == "in_silico"
    assert e["score"] == 0.95                           # no cut-off applied
    wider = parse_string(d, proteins=["P35354"], mode="neighbours")
    assert {x["object"] for x in wider.edges} | {x["subject"] for x in wider.edges} >= {
        "uniprot:Q92753"}                               # a low score is kept too
    with pytest.raises(ValueError):
        parse_string(d, proteins=["P35354"], mode="everything")


def test_reactome_keeps_human_rows_and_labels_electronic_inference(tmp_path):
    from bioagent.sources.parsers import parse_reactome

    path = tmp_path / "UniProt2Reactome.txt"
    tsv(path, None, [
        ["P35354", "R-HSA-2162123", "https://reactome.org/x", "Synthesis of Prostaglandins",
         "TAS", "Homo sapiens"],
        ["P35354", "R-HSA-9999", "https://reactome.org/y", "Inferred pathway", "IEA",
         "Homo sapiens"],
        ["P35354", "R-RNO-2162123", "https://reactome.org/z", "rat", "IEA",
         "Rattus norvegicus"],
        ["Q92753", "R-HSA-383280", "https://reactome.org/w", "Nuclear Receptor", "TAS",
         "Homo sapiens"]])
    result = parse_reactome(path, proteins=["P35354"])
    _sound(result)
    by = {e["object"]: e for e in result.edges}
    assert set(by) == {"reactome:R-HSA-2162123", "reactome:R-HSA-9999"}
    assert by["reactome:R-HSA-2162123"]["study_design"] == "expert_consensus"
    assert by["reactome:R-HSA-9999"]["knowledge_level"] == "prediction"


def test_the_network_build_scopes_string_and_reactome_to_the_targets_found(raw, tmp_path):
    string_files(raw)
    tsv(raw / "UniProt2Reactome.txt", None, [
        ["P35354", "R-HSA-2162123", "u", "Synthesis of Prostaglandins", "TAS", "Homo sapiens"],
        ["P12345", "R-HSA-1", "u", "unrelated", "TAS", "Homo sapiens"]])
    build = build_gold(raw, tmp_path / "snap", network=True)
    assert build.passed
    # Reactome is the whole human annotation: it is the enrichment background.
    reactome_edges = build.snapshots["reactome"].edges
    assert sorted(e["subject"] for e in reactome_edges) == ["uniprot:P12345", "uniprot:P35354"]
    assert build.snapshots["reactome"].version == "current"
    assert build.snapshots["string"].version.startswith("12.0+subset-")
