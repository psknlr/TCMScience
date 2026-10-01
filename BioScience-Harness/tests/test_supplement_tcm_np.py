"""Natural products and plant names: COCONUT, the WFO Plant List, IMPPAT, TM-MC's licences,
and the COCONUT and KNApSAcK connectors.

Every file here is a tiny synthetic one written in the source's real format (the header
copied from the real file, made-up rows); nothing touches the network.
"""

from __future__ import annotations

import csv
import gzip
import io
import json
import zipfile

import pytest

from bioagent.tcmdb import TCMDataHub
from bioagent.tcmdb.spec import allows_commercial, licence_class

pytestmark = pytest.mark.unit

IK_A = "WQZGKKKJIJFFOK-GASJEMHNSA-N"
IK_B = "NWKFECICNXDNOQ-UHFFFAOYSA-N"
IK_C = "HNKJADCVZUBCPG-UHFFFAOYSA-N"
IK_D = "RYYVLZVUVIJVGH-UHFFFAOYSA-N"
IK_E = "BSYNRYMUTXBXSQ-UHFFFAOYSA-N"


@pytest.fixture
def catalogued(monkeypatch):
    """The source cards 67-69 the integrator adds, so ``check`` can be run here."""
    from bioagent.tcmdb import hub as hubmod
    from bioagent.tcmdb.hub import SourceCard
    cards = hubmod.catalog() + tuple(
        SourceCard(no=n, name=name, modules=("M1",), url="", access="snapshot",
                   connector=None, dataset=key, license="", barriers="", assessment="",
                   checked="2026-10-01", origin="review 2026-09-30")
        for n, name, key in ((67, "COCONUT", "coconut"), (68, "WFO", "wfo"),
                             (69, "IMPPAT", "imppat3")))
    monkeypatch.setattr(hubmod, "catalog", lambda: cards)


# ----------------------------------------------------------------------------- COCONUT
COCONUT_HEADER = (
    "identifier", "canonical_smiles", "standard_inchi", "standard_inchi_key", "name",
    "iupac_name", "annotation_level", "total_atom_count", "heavy_atom_count",
    "molecular_weight", "exact_molecular_weight", "molecular_formula", "alogp",
    "topological_polar_surface_area", "rotatable_bond_count", "hydrogen_bond_acceptors",
    "hydrogen_bond_donors", "hydrogen_bond_acceptors_lipinski",
    "hydrogen_bond_donors_lipinski", "lipinski_rule_of_five_violations",
    "aromatic_rings_count", "qed_drug_likeliness", "formal_charge", "fractioncsp3",
    "number_of_minimal_rings", "van_der_walls_volume", "contains_sugar",
    "contains_ring_sugars", "contains_linear_sugars", "murcko_framework", "np_likeness",
    "chemical_class", "chemical_sub_class", "chemical_super_class",
    "direct_parent_classification", "np_classifier_pathway", "np_classifier_superclass",
    "np_classifier_class", "np_classifier_is_glycoside", "organisms", "collections", "dois",
    "synonyms", "cas")


def _coconut_row(cnp, ik, name, organisms, collections, dois=""):
    row = dict.fromkeys(COCONUT_HEADER, "")
    row.update(identifier=cnp, standard_inchi_key=ik, name=name, organisms=organisms,
               collections=collections, dois=dois, annotation_level="5")
    return [row[c] for c in COCONUT_HEADER]


@pytest.fixture
def coconut(tmp_path, catalogued):
    h = TCMDataHub(tmp_path / "hub")
    raw = h.raw_dir("coconut")
    raw.mkdir(parents=True)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(COCONUT_HEADER)
    w.writerow(_coconut_row("CNP0000001.0", IK_A, "Baicalin", "Scutellaria baicalensis|"
                            "Homo  sapiens", "KNApSaCK|NPASS", "10.1/x|10.2/y"))
    # a second record of the same structure: its organisms join the first's
    w.writerow(_coconut_row("CNP0000001.1", IK_A, "baicalin (variant)",
                            "Scutellaria baicalensis",
                            "UNPD (Universal Natural Products Database)"))
    w.writerow(_coconut_row("CNP0000002.0", IK_B, "Flavylium", "", "ChEBI NPs"))
    w.writerow(_coconut_row("CNP0000003.0", IK_C, "Thioanisole", "Ascophyllum nodosum",
                            "ChEBI NPs|Super Natural II"))
    # FooDB's terms ask permission for commercial use; a vendor catalogue's are its own
    w.writerow(_coconut_row("CNP0000004.0", IK_D, "Caffeine", "Coffea arabica", "FooDB"))
    w.writerow(_coconut_row("CNP0000005.0", IK_E, "Aspirin", "Salix alba",
                            "Specs Natural Products|ChEBI NPs"))
    with zipfile.ZipFile(raw / "coconut_csv-10-2026.zip", "w") as z:
        z.writestr("coconut_csv-10-2026.csv", buf.getvalue())
    report = h.build("coconut", log=lambda m: None)
    return h, report


def test_coconut_pairs_each_organism_with_the_compound_inchikey(coconut):
    h, report = coconut
    assert report["tables"] == {"molecule": 6}
    rows = h.relations("organism_compound", limit=100)
    pairs = {(r["subject_id"], r["object_id"]) for r in rows}
    assert pairs == {("coconut:organism.Scutellaria baicalensis", f"inchikey:{IK_A}"),
                     ("coconut:organism.Homo sapiens", f"inchikey:{IK_A}"),
                     ("coconut:organism.Ascophyllum nodosum", f"inchikey:{IK_C}"),
                     ("coconut:organism.Coffea arabica", f"inchikey:{IK_D}"),
                     ("coconut:organism.Salix alba", f"inchikey:{IK_E}")}
    assert all(r["evidence"] == "aggregated" and r["outcome"] == "positive"
               and r["reference"] is None for r in rows)       # DOIs are not per organism
    assert not any(r["object_id"] == f"inchikey:{IK_B}" for r in rows)   # no organism


def test_coconut_merges_the_records_of_one_structure_and_names_their_collections(coconut):
    h, _ = coconut
    rows = h.relations("organism_compound", subject="Scutellaria baicalensis")
    assert len(rows) == 1
    row = rows[0]
    assert json.loads(row["context"]) == {"source_id": "CNP0000001.0 | CNP0000001.1"}
    # the pair rests on one of these collections, not on each: one lineage unit
    assert row["note"] == "via any: KNApSAcK; NPASS; UNPD"
    assert row["object_name"] == "Baicalin | baicalin (variant)"
    human = h.relations("organism_compound", subject="Homo sapiens")[0]
    assert human["note"] == "via any: KNApSAcK; NPASS"     # only the record that gave it


def test_coconut_collections_are_one_lineage_with_one_token_names(coconut):
    from bioagent.tcmdb.consensus import independent_count, lineage_of
    h, _ = coconut
    row = h.relations("organism_compound", subject="Scutellaria baicalensis")[0]
    assert lineage_of(row) == {"any:KNAPSACK|NPASS|UNPD"}
    assert independent_count(lineage_of(row)) == 1
    # multi-word titles become one token each (not SUPER for 'Super Natural II')
    other = h.relations("organism_compound", subject="Ascophyllum nodosum")[0]
    assert other["note"] == "via any: ChEBI; SuperNatural"
    assert lineage_of(other) == {"any:CHEBI|SUPERNATURAL"}
    single = h.relations("organism_compound", subject="Coffea arabica")[0]
    assert single["note"] == "via FooDB" and lineage_of(single) == {"FOODB"}
    # another source's row naming one of the collections may share the upstream
    assert independent_count(lineage_of(row) | {"KNAPSACK"}) == 1


def test_coconut_rows_resting_on_a_non_commercial_collection_carry_its_terms(coconut):
    from bioagent.tcmdb.datasets import dataset
    h, _ = coconut
    knapsack = h.relations("organism_compound", subject="Scutellaria baicalensis")[0]
    assert licence_class(knapsack["license"]) == "non-commercial"
    open_row = h.relations("organism_compound", subject="Ascophyllum nodosum")[0]
    assert open_row["license"] == dataset("coconut").license
    foodb = h.relations("organism_compound", subject="Coffea arabica")[0]
    assert licence_class(foodb["license"]) == "non-commercial"
    vendor = h.relations("organism_compound", subject="Salix alba")[0]
    assert licence_class(vendor["license"]) == "unknown"
    assert "Specs Natural Products" in vendor["license"]
    assert {r["subject_name"] for r in h.relations("organism_compound", commercial=True)} \
        == {"Ascophyllum nodosum"}


def test_coconut_passes_the_acceptance_check(coconut):
    h, _ = coconut
    result = h.check("coconut")
    assert result["ok"], result["problems"]
    assert result["relations"] == {"organism_compound": {"aggregated/positive": 5}}


# --------------------------------------------------------------------------------- WFO
WFO_HEADER = ("taxonID", "scientificNameID", "localID", "scientificName", "taxonRank",
              "parentNameUsageID", "scientificNameAuthorship", "family", "subfamily", "tribe",
              "subtribe", "genus", "subgenus", "specificEpithet", "infraspecificEpithet",
              "verbatimTaxonRank", "nomenclaturalStatus", "namePublishedIn",
              "taxonomicStatus", "acceptedNameUsageID", "originalNameUsageID",
              "nameAccordingToID", "taxonRemarks", "created", "modified", "references",
              "source", "majorGroup", "tplID")


def _wfo_line(**values):
    out = []
    for c in WFO_HEADER:
        text = values.get(c, "")
        # the release quotes fields with spaces or punctuation, doubling inner quotes
        out.append('"' + text.replace('"', '""') + '"' if any(ch in text for ch in ' ".,')
                   else text)
    return "\t".join(out)


@pytest.fixture
def wfo(tmp_path, catalogued):
    h = TCMDataHub(tmp_path / "hub")
    raw = h.raw_dir("wfo")
    raw.mkdir(parents=True)
    lines = ["\t".join(WFO_HEADER),
             _wfo_line(taxonID="wfo-0000307642",
                       scientificNameID="urn:lsid:ipni.org:names:458155-1",
                       scientificName="Scutellaria baicalensis",
                       scientificNameAuthorship="Georgi", taxonRank="species",
                       taxonomicStatus="Accepted", parentNameUsageID="wfo-4000034856",
                       family="Lamiaceae", genus="Scutellaria", specificEpithet="baicalensis",
                       taxonRemarks='<a href=http://x >The "Plant" List</a>', majorGroup="A"),
             _wfo_line(taxonID="wfo-0000185834", scientificName="Astragalus membranaceus",
                       scientificNameAuthorship="Fisch. ex Bunge", taxonRank="species",
                       taxonomicStatus="Synonym", acceptedNameUsageID="wfo-0000185128",
                       namePublishedIn='Mém. Acad. "Imp." Sci. 11: 25 (1868)',
                       family="Fabaceae", genus="Astragalus", majorGroup="A")]
    with zipfile.ZipFile(raw / "_DwC_backbone_R.zip", "w") as z:
        z.writestr("classification.csv", "\n".join(lines) + "\n")
    with gzip.open(raw / "deduplicated_ids_lookup.csv.gz", "wt", encoding="utf-8") as fh:
        fh.write("wfo_id,name_canonical,authors_string,rank,nomenclatural_status\n"
                 "wfo-4000048768,wfo-4000048766,?,,genus,deprecated\n")
    # saved by a client that decoded Zenodo's Content-Encoding: plain text under .gz
    (raw / "deprecated_names_lookup.csv.gz").write_text(
        "wfo_id,name_canonical,authors_string,rank,nomenclatural_status\n"
        "wfo-0001420350,? acuminata,,species,deprecated\n", encoding="utf-8")
    report = h.build("wfo", log=lambda m: None)
    return h, report


def test_wfo_loads_name_tables_and_yields_no_relations(wfo):
    from bioagent.tcmdb.extra.tcm_np import WFO_NAME_COLUMNS
    h, report = wfo
    assert report["tables"] == {"name": 2, "deprecated_name": 1, "deduplicated_id": 1}
    assert report["relations"] == {}
    assert h.tables("wfo")["name"] == list(WFO_NAME_COLUMNS)
    syn = h.query("wfo", "name", where={"scientificName": "Astragalus membranaceus"})[0]
    assert syn["taxonomicStatus"] == "Synonym"
    assert syn["acceptedNameUsageID"] == "wfo-0000185128"
    assert syn["scientificNameAuthorship"] == "Fisch. ex Bunge"      # quotes removed
    assert syn["namePublishedIn"] == 'Mém. Acad. "Imp." Sci. 11: 25 (1868)'
    acc = h.query("wfo", "name", where={"taxonID": "wfo-0000307642"})[0]
    assert acc["acceptedNameUsageID"] is None and acc["taxonomicStatus"] == "Accepted"


def test_wfo_gives_the_unnamed_replacement_id_column_a_name(wfo):
    h, _ = wfo
    row = h.query("wfo", "deduplicated_id")[0]
    assert row["wfo_id"] == "wfo-4000048768"
    assert row["replacement_wfo_id"] == "wfo-4000048766"
    assert row["nomenclatural_status"] == "deprecated"
    assert h.query("wfo", "deprecated_name")[0]["name_canonical"] == "? acuminata"


def test_wfo_passes_the_acceptance_check_and_is_not_a_materia_medica(wfo):
    from bioagent.tcmdb.datasets import dataset
    h, _ = wfo
    result = h.check("wfo")
    assert result["ok"], result["problems"]
    spec = dataset("wfo")
    assert spec.relations == () and "not a materia medica" in spec.notes
    assert spec.commercial_use == "allowed" and licence_class(spec.license) == "open"


# ------------------------------------------------------------------------------ IMPPAT
def _tsv(path, header, *rows):
    path.write_text("\n".join("\t".join(r) for r in (header, *rows)) + "\n", encoding="utf-8")


@pytest.fixture
def imppat(tmp_path, catalogued):
    h = TCMDataHub(tmp_path / "hub")
    raw = h.raw_dir("imppat3")
    raw.mkdir(parents=True)
    _tsv(raw / "Plant_Information_IMPPAT.tsv",
         ("Plant_identifier", "Indian_Medicinal_plant", "Synonymous names", "Kingdom", "Family",
          "Group", "Common_name", "IUCN_Red_List_Category", "System_of_Medicine"),
         ("IMPPAT3_PLTID000001", "Abelmoschus esculentus", "Hibiscus esculentus", "Plantae",
          "Malvaceae", "Angiosperms", "Ladies Finger", "", "Ayurveda,Siddha"),
         ("IMPPAT3_PLTID002318", "Phyllanthus emblica", "Emblica officinalis", "Plantae",
          "Phyllanthaceae", "Angiosperms", "Indian gooseberry", "", "Ayurveda"))
    chem_header = ("IMPPAT_Phytochemical_identifier", "Reference_identifier",
                   "Phytochemical name_standardised", "Synonymous chemical names",
                   "Pubchem_CID", "ChEMBL", "ChEBI", "ZINC", "FDASRS", "SureChEMBL",
                   "MolPort", "Canonical_SMILES", "DeepSMILES", "InChI", "InChIKey",
                   "Scaffold Graph", "Scaffold Graph/Node", "Scaffold Graph/Node/Bond",
                   "Functional_Groups")

    def chem(pid, name, cid, ik):
        row = dict.fromkeys(chem_header, "")
        row.update({"IMPPAT_Phytochemical_identifier": pid,
                    "Phytochemical name_standardised": name, "Pubchem_CID": cid,
                    "InChIKey": ik})
        return tuple(row[c] for c in chem_header)

    _tsv(raw / "Chemical_Information_IMPPAT_Phytochemicals.tsv", chem_header,
         chem("IMPPAT3_PHYID000001", "Flavylium", "CID_145858", IK_B),
         chem("IMPPAT3_PHYID000002", "D-Glucose", "CID_5793", ""),       # no InChIKey
         chem("IMPPAT3_PHYID000003", "Unknown X", "", ""))               # neither
    _tsv(raw / "IMPPAT_Phytochemical_Plant_Association.tsv",
         ("Plant_identifier", "Indian_Medicinal_plant", "Plant_part",
          "IMPPAT_Phytochemical_identifier", "Reference_identifier"),
         ("IMPPAT3_PLTID000001", "Abelmoschus esculentus", "flower", "IMPPAT3_PHYID000001",
          "CID_145858"),
         ("IMPPAT3_PLTID000001", "Abelmoschus esculentus", "seed", "IMPPAT3_PHYID000001",
          "CID_145858"),
         ("IMPPAT3_PLTID000001", "Abelmoschus esculentus", "seed", "IMPPAT3_PHYID000001",
          "CID_145858"),                                                   # repeated
         ("IMPPAT3_PLTID000001", "Abelmoschus esculentus", "", "IMPPAT3_PHYID000003", ""))
    _tsv(raw / "IMPPAT_TherapeuticUse_Plant_Association.tsv",
         ("IMPPAT_Plant_identifier", "Indian_Medicinal_plant", "Plant_part",
          "IMPPAT_Therapeutic_use_identifier", "Therapeutic_use"),
         ("IMPPAT3_PLTID000001", "Abelmoschus esculentus", "", "IMPPAT3_TPUID000522",
          "emollients"))
    _tsv(raw / "Target_IMPPAT_Phytochemicals.tsv",
         ("IMPPAT_Phytochemical_identifier", "Gene_identifier", "HGNC_Symbol",
          "Entrez gene identifier", "Source"),
         ("IMPPAT3_PHYID000002", "ENSG00000085662", "AKR1B1", "231", "ChEMBL | NPASS"),
         ("IMPPAT3_PHYID000002", "ENSG00000291368", "", "49", "ChEMBL"))
    _tsv(raw / "Bioactivity_IMPPAT_Phytochemicals.tsv",
         ("IMPPAT_Phytochemical_identifier", "Target_name", "Target_organism",
          "UniProt_identifier", "EC50", "IC50", "Kd", "Ki", "Source"),
         ("IMPPAT3_PHYID000002", "Brain glycogen phosphorylase", "Homo sapiens", "P11216",
          "", "= 900000.0 nM", "", "= 7400000.0 nM|= 1700000.0 nM", "BindingDB|NPASS"),
         ("IMPPAT3_PHYID000002", "SARS-CoV-2", "Severe acute respiratory syndrome "
          "coronavirus 2", "", "", "> 20000.0 nM", "", "", "NPASS"),
         # every value right-censored at >= 10 uM: tested and inactive
         ("IMPPAT3_PHYID000002", "Aldose reductase", "Homo sapiens", "P15121",
          "", "> 100000.0 nM|>= 50000.0 nM", "", "", "NPASS"),
         # censored below 10 uM: says only that it is not potent
         ("IMPPAT3_PHYID000002", "Carbonic anhydrase 2", "Homo sapiens", "P00918",
          "> 100.0 nM", "", "", "", "BindingDB"),
         # one measured value among censored ones: activity was found
         ("IMPPAT3_PHYID000002", "Alpha-glucosidase", "Homo sapiens", "P10253",
          "", "", "= 48800.0 nM|> 50000.0 nM", "", "NPASS"))
    form_cols = ("Formulation_identifier", "Formulation_name_in_API_original",
                 "Ingredient name in API_original", "Plant part in API_original",
                 "Therapeutic_uses (according to The Ayurvedic Pharmacopoeia of India)",
                 "Ingredient_name_standardised", "Plant_name_standardised",
                 "Plant_part_standardised", "Therapeutic_uses_standardised",
                 "IMPPAT_identifiers", "References")
    _tsv(raw / "IMPPAT_SingleHerbalFormulations.tsv", form_cols,
         ("API000003", "Āmalakī_1", "Emblica officinalis Gaertn.", "fresh fruit pulp",
          "raktapitta", "Phyllanthus emblica", "Phyllanthus emblica", "fruit",
          "bleeds profusely", "IMPPAT3_TPUID000199", "ISBN:9788190115131"))
    poly_cols = tuple(c.replace("API", "AFI").replace(
        "Therapeutic_uses (according to The Ayurvedic Pharmacopoeia of India)",
        "Therapeutic uses (according to The Ayurvedic Formulary of India)")
        .replace("Formulation_name_in_AFI_original", "Formulation name_in_AFI_original")
        for c in form_cols)
    _tsv(raw / "IMPPAT_PolyHerbalFormulations.tsv", poly_cols,
         ("AFI000001", "Jyotiṣmatī taila", "Mayūraka (Apāmārga)", "(Pl.)", "",
          "Achyranthes aspera", "Achyranthes aspera", "whole plant", "", "",
          "ISBN:8190115146"),
         ("AFI000002", "Lauha parpaṭi", "Gandhaka - śuddha", "", "", "Purified sulphur",
          "", "", "", "", "ISBN:8190115146"))
    report = h.build("imppat3", log=lambda m: None)
    return h, report


def test_imppat_plant_compounds_are_listed_with_the_plant_part(imppat):
    h, report = imppat
    rows = h.relations("organism_compound", limit=100)
    got = {(r["object_id"], r["context"]) for r in rows}
    assert got == {(f"inchikey:{IK_B}", '{"part": "flower"}'),
                   (f"inchikey:{IK_B}", '{"part": "seed"}'),
                   ("imppat:IMPPAT3_PHYID000003", None)}      # no structure id at all
    assert all(r["evidence"] == "listed" and r["subject_id"] == "imppat:IMPPAT3_PLTID000001"
               for r in rows)
    assert report["relations"]["organism_compound"] == 3        # the repeated row is one


def test_imppat_targets_keep_the_upstream_and_measured_values(imppat):
    h, report = imppat
    rows = h.relations("compound_target", limit=100)
    # D-Glucose has no InChIKey: its PubChem CID is its id
    assert {r["subject_id"] for r in rows} == {"pubchem:5793"}
    listed = {r["object_id"]: r for r in rows if r["evidence"] == "aggregated"}
    assert set(listed) == {"symbol:AKR1B1", "ensembl:ENSG00000291368"}
    assert listed["symbol:AKR1B1"]["note"] == "via ChEMBL; NPASS"
    measured = {json.loads(r["context"])["measure"]: r for r in rows
                if r["evidence"] == "known" and r["object_id"] == "uniprot:P11216"}
    assert set(measured) == {"IC50", "Ki"}
    ki = measured["Ki"]
    assert ki["object_id"] == "uniprot:P11216" and ki["note"] == "via BindingDB; NPASS"
    assert json.loads(ki["context"]) == {"measure": "Ki", "species": "Homo sapiens",
                                         "value": "= 7400000.0 nM|= 1700000.0 nM"}
    assert ki["effect"] is None and ki["outcome"] == "positive"
    # an organism as the "target": no protein to join, so it waits in the queue
    queue = h.unresolved("imppat3")
    assert [q["object_name"] for q in queue] == ["SARS-CoV-2"]
    assert queue[0]["note"] == "IC50 > 20000.0 nM; via NPASS"
    assert report["unresolved"] == 1


def test_imppat_right_censored_bioactivities_are_not_positive(imppat):
    h, _ = imppat
    known = {r["object_id"]: r for r in h.relations("compound_target", limit=100)
             if r["evidence"] == "known" and r["object_id"] != "uniprot:P11216"}
    assert known["uniprot:P15121"]["outcome"] == "negative"
    assert json.loads(known["uniprot:P15121"]["context"])["value"] == \
        "> 100000.0 nM|>= 50000.0 nM"
    assert known["uniprot:P00918"]["outcome"] == "inconclusive"
    assert known["uniprot:P10253"]["outcome"] == "positive"


def test_imppat_measure_outcome_rules():
    from bioagent.tcmdb.extra.tcm_np import _measure_outcome
    assert _measure_outcome("= 1420.0 nM|= 1630.0 nM") == "positive"
    assert _measure_outcome("42700 nM") == "positive"                # unqualified
    assert _measure_outcome("< 10.0 nM") == "positive"
    assert _measure_outcome("> 100000.0 nM") == "negative"
    assert _measure_outcome(">> 20000.0 nM|> 19952.62 nM") == "negative"
    assert _measure_outcome("> 500.0 nM|> 20000.0 nM") == "inconclusive"
    assert _measure_outcome("> 100.0 ug.mL-1") == "inconclusive"      # unit not judged


def test_imppat_formulations_map_plants_and_keep_minerals_named(imppat):
    h, _ = imppat
    rows = {r["subject_id"]: r for r in h.relations("formula_herb", limit=100)}
    assert rows["imppat:API000003"]["object_id"] == "imppat:IMPPAT3_PLTID002318"
    assert rows["imppat:API000003"]["reference"] == "isbn:9788190115131"
    assert rows["imppat:API000003"]["context"] == '{"part": "fruit"}'
    assert rows["imppat:AFI000001"]["object_id"] == "imppat:plant.Achyranthes aspera"
    assert rows["imppat:AFI000002"]["object_id"] == "imppat:ingredient.Purified sulphur"
    use = h.relations("herb_disease")[0]
    assert (use["object_id"], use["object_name"], use["evidence"]) == \
        ("imppat:IMPPAT3_TPUID000522", "emollients", "listed")


def test_imppat_is_non_commercial_and_passes_the_check(imppat):
    h, _ = imppat
    assert h.relations(commercial=True, sources=["imppat3"]) == []
    result = h.check("imppat3")
    assert result["ok"], result["problems"]
    assert {v["class"] for v in result["licences"].values()} == {"non-commercial"}


# ------------------------------------------------------------------------------- TM-MC
def test_tmmc_licences_follow_the_file_each_kind_comes_from():
    from bioagent.tcmdb.datasets import dataset
    spec = dataset("tmmc2")
    files = {f.name: f.license for f in spec.files}
    assert files["medicinal_compound.xlsx"] == files["chemical_property.xlsx"] \
        == "CC BY-NC (version unstated)"
    assert {files[n] for n in ("medicinal_material.xlsx", "chemical_protein.xlsx",
                               "protein_disease.xlsx", "prescription.xlsx")} \
        == {"CC BY (version unstated)"}
    classes = {k: licence_class(spec.licence_of(k)) for k in spec.relations}
    # herb->compound is the NC file; targets use compound ids from the NC file; the
    # disease rows copy DisGeNET, whose own terms are non-commercial
    assert classes == {"herb_ingredient": "non-commercial",
                       "ingredient_target": "non-commercial",
                       "target_disease": "non-commercial", "formula_herb": "open"}
    assert spec.commercial_use == "forbidden"
    assert spec.upstream == ("STITCH", "PubChem", "DisGeNET")
    assert not allows_commercial(spec.license)


# -------------------------------------------------------------------------- connectors
def test_the_coconut_requests_are_the_ones_verified_live():
    from bioagent.providers.public_apis import BY_KEY, render_call
    src = BY_KEY["coconut"]
    assert src.base_url == "https://coconut.naturalproducts.net/api"
    search = render_call("coconut", "search")
    assert (search["method"], search["path"]) == ("POST", "search")
    assert search["json_body"] == {"query": "HNKJADCVZUBCPG-UHFFFAOYSA-N"}
    org = render_call("coconut", "organism_molecules", organism="Scutellaria baicalensis")
    assert org["json_body"] == {"type": "tags", "tagType": "organisms",
                                "query": "Scutellaria baicalensis", "limit": 5, "page": 1}
    coll = render_call("coconut", "collection_molecules")
    assert coll["json_body"]["tagType"] == "dataSource"
    assert coll["json_body"]["query"] == "DrugBankNP"


def test_the_knapsack_requests_ask_for_the_documented_pages():
    from bioagent.providers.public_apis import render_call
    met = render_call("knapsack", "metabolite")
    assert (met["method"], met["path"], met["accept"]) == ("GET", "information.php",
                                                           "text/html")
    assert met["params"] == {"sname": "C_ID", "word": "C00000001"}
    org = render_call("knapsack", "organism_metabolites")
    assert org["path"] == "result.php"
    assert org["params"] == {"sname": "organism", "word": "Scutellaria baicalensis"}
    assert render_call("knapsack", "metabolite_search")["params"] == {
        "sname": "metabolite", "word": "baicalin"}
    assert render_call("knapsack", "pair_reference")["params"] == {
        "mode": "r", "word": "C00000001", "key": 0}


def test_every_host_is_allowed_and_paced_and_wfo_waits_for_its_certificate_chain():
    from bioagent.backends.http import DEFAULT_RATES
    from bioagent.policy import PROFILES
    from bioagent.providers.public_apis import BY_KEY
    from bioagent.providers.supplement.tcm_np import PENDING, SOURCES
    from bioagent.tcmdb.datasets import dataset
    allowed = PROFILES["biomedical-research"].allowed_hosts

    def ok(host):
        return any(host == a or host.endswith("." + a) for a in allowed)

    for s in SOURCES:
        assert ok(s.host) and DEFAULT_RATES[s.host] <= 1.0, s.key
        assert s.key in BY_KEY
    for key in ("coconut", "wfo", "imppat3", "tmmc2"):
        for f in dataset(key).files:
            assert ok(f.url.split("/")[2]), f.url
    assert [p.key for p in PENDING] == ["wfo"] and "wfo" not in BY_KEY
    assert DEFAULT_RATES["list.worldfloraonline.org"] <= 0.1      # robots Crawl-delay: 10
    # the datasets' download hosts are paced too (acquisition.Downloader reads these)
    for key in ("coconut", "wfo", "imppat3", "tmmc2"):
        for f in dataset(key).files:
            assert DEFAULT_RATES[f.url.split("/")[2]] <= 1.0, f.url
    assert DEFAULT_RATES["zenodo.org"] <= 0.1                     # robots Crawl-delay: 10


def test_the_hub_fetch_waits_out_zenodos_crawl_delay(tmp_path, monkeypatch):
    """``tcmdb fetch wfo``: a HEAD and a GET per file, each request starting at least
    10 s after the previous request to zenodo.org ended."""
    from bioagent.acquisition import downloader
    from bioagent.tcmdb.datasets import dataset

    clock = [1000.0]
    calls: list[tuple[str, str, float]] = []

    class Resp:
        status = 200

        def __init__(self, body):
            self._body, self.headers = io.BytesIO(body), {}

        def read(self, n=-1):
            return self._body.read(n)

        def close(self):
            clock[0] += 3.0                       # the transfer took 3 s

    def fake_urlopen(req, timeout=None):
        calls.append((req.get_method(), req.full_url, clock[0]))
        name = req.full_url.rsplit("/", 2)[-2]
        size = dataset("wfo").file(name).expected_bytes
        resp = Resp(b"" if req.get_method() == "HEAD" else b"x" * size)
        resp.headers = {"Content-Length": str(size)}
        return resp

    monkeypatch.setattr(downloader, "PACER", downloader._HostPacer())
    monkeypatch.setattr(downloader.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(downloader.time, "sleep", lambda s: clock.__setitem__(0, clock[0] + s))
    monkeypatch.setattr(downloader.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(downloader.Downloader, "_hash", staticmethod(lambda p, a="sha256": "x"))
    h = TCMDataHub(tmp_path / "hub")
    results = h.fetch("wfo", log=lambda m: None)
    assert [r["ok"] for r in results] == [True, True, True]
    assert [c[0] for c in calls] == ["HEAD", "GET"] * 3
    for (_, _, start), (_, _, nxt) in zip(calls, calls[1:]):
        assert nxt - (start + 3.0) >= 10.0                          # end -> next start


def test_the_downloader_paces_unlisted_hosts_at_one_request_a_second():
    from bioagent.acquisition import downloader
    pacer = downloader._HostPacer(default_rps=1.0)
    assert pacer.interval("files.example.org", {}) == 1.0
    assert pacer.interval("localhost", {}) == 0.0
    assert pacer.interval("zenodo.org", {"zenodo.org": 0.1}) == pytest.approx(10.0)


def test_an_html_page_a_connector_asked_for_is_returned_whole():
    """Indented markup has tabs and newlines; it used to be read as a one-column table
    that kept each line only up to its first tab."""
    from bioagent.backends.http import HTTPBackend
    page = b"<html>\n<table>\n\t<tr><td>C00001022</td>\t<td>Baicalein</td></tr>\n</table>"
    value = HTTPBackend._parse(page, "text/html; charset=UTF-8")
    assert value["format"] == "html" and "Baicalein" in value["text"]
    assert value["truncated"] is False
