"""Drug, drug-target and drug-response sources (``tcmdb.extra.drugs`` and
``providers.supplement.drugs``), checked offline on tiny files in each source's format.

DrugCentral writes double-quoted TSV; a target made of several proteins lists them all in
one row; its action type gives the effect, and the database a row came from is its
lineage. GDSC's putative targets are free text and stay GDSC's own names. Pharos's
development levels are gene sets. DrugComb's and GDSC's screen tables are kept as files,
never exploded into relations. The connectors send the requests that were verified live.
"""

from __future__ import annotations

import gzip
import json

import pytest

from bioagent.tcmdb import TCMDataHub

pytestmark = pytest.mark.unit

_DTI_HEADER = ("DRUG_NAME", "STRUCT_ID", "TARGET_NAME", "TARGET_CLASS", "ACCESSION", "GENE",
               "SWISSPROT", "ACT_VALUE", "ACT_UNIT", "ACT_TYPE", "ACT_COMMENT", "ACT_SOURCE",
               "RELATION", "MOA", "MOA_SOURCE", "ACT_SOURCE_URL", "MOA_SOURCE_URL",
               "ACTION_TYPE", "TDL", "ORGANISM")


def _dti_line(**kw) -> str:
    """One line as DrugCentral writes it: text quoted, numbers and blanks bare."""
    out = []
    for col in _DTI_HEADER:
        value = kw.get(col, "")
        if value == "" or isinstance(value, (int, float)):
            out.append(str(value))
        else:
            out.append(f'"{value}"')
    return "\t".join(out)


def _drugcentral_files(raw):
    raw.mkdir(parents=True)
    chembl = dict(DRUG_NAME="levobupivacaine", STRUCT_ID=4,
                  TARGET_NAME="Potassium voltage-gated channel subfamily H member 2",
                  TARGET_CLASS="Ion channel", ACCESSION="Q12809", GENE="KCNH2",
                  SWISSPROT="KCNH2_HUMAN", ACT_VALUE=4.89, ACT_TYPE="IC50",
                  ACT_COMMENT="Inhibition of human ERG channel by patch clamp",
                  ACT_SOURCE="CHEMBL", RELATION="=", TDL="Tclin", ORGANISM="Homo sapiens")
    lines = [
        "\t".join(f'"{c}"' for c in _DTI_HEADER),
        _dti_line(**chembl),
        _dti_line(**chembl),                         # the same record twice: one row
        # a mechanism of action from the label, cited by a PubMed link
        _dti_line(DRUG_NAME="levobupivacaine", STRUCT_ID=4,
                  TARGET_NAME="Sodium channel protein type 4 subunit alpha",
                  TARGET_CLASS="Ion channel", ACCESSION="P35499", GENE="SCN4A",
                  SWISSPROT="SCN4A_HUMAN", ACT_COMMENT="Mechanism of Action",
                  ACT_SOURCE="WOMBAT-PK", MOA=1, MOA_SOURCE="DRUG LABEL",
                  MOA_SOURCE_URL="https://pubmed.ncbi.nlm.nih.gov/12345678",
                  ACTION_TYPE="BLOCKER", TDL="Tclin", ORGANISM="Homo sapiens"),
        # a channel complex: one row per component, each with its own gene
        _dti_line(DRUG_NAME="diazepam", STRUCT_ID=864,
                  TARGET_NAME="GABA-A receptor alpha-1/beta-3/gamma-2",
                  TARGET_CLASS="Ion channel", ACCESSION="P14867|P28472|P18507",
                  GENE="GABRA1|GABRB3|GABRG2", SWISSPROT="GBRA1_HUMAN|GBRB3_HUMAN|GBRG2_HUMAN",
                  ACT_SOURCE="IUPHAR", MOA=1, MOA_SOURCE="CHEMBL",
                  MOA_SOURCE_URL="https://www.ebi.ac.uk/chembl/compound/inspect/CHEMBL12",
                  ACTION_TYPE="POSITIVE ALLOSTERIC MODULATOR", TDL="Tclin|Tclin|Tclin",
                  ORGANISM="Homo sapiens"),
        _dti_line(DRUG_NAME="pimavanserin", STRUCT_ID=5096, TARGET_NAME="5-HT2A receptor",
                  TARGET_CLASS="GPCR", ACCESSION="P28223", GENE="HTR2A",
                  ACT_VALUE=9.3, ACT_TYPE="Ki", ACT_SOURCE="PDSP", RELATION=">",
                  ACTION_TYPE="INVERSE AGONIST", ORGANISM="Homo sapiens"),
        _dti_line(DRUG_NAME="mipomersen", STRUCT_ID=4964, TARGET_NAME="Apolipoprotein B-100",
                  TARGET_CLASS="Secreted", ACCESSION="P04114", GENE="APOB",
                  ACT_SOURCE="DRUG LABEL", MOA=1, MOA_SOURCE="DRUG LABEL",
                  MOA_SOURCE_URL="http://dx.doi.org/10.1000/xyz.123",
                  ACTION_TYPE="ANTISENSE INHIBITOR", ORGANISM="Homo sapiens"),
        _dti_line(DRUG_NAME="ratdrug", STRUCT_ID=7, TARGET_NAME="Some rat enzyme",
                  TARGET_CLASS="Enzyme", ACCESSION="P99999", GENE="Abc1",
                  ACT_VALUE=6, ACT_TYPE="Ki", ACT_SOURCE="DRUG MATRIX", RELATION="=",
                  ORGANISM="Rattus norvegicus"),
        _dti_line(DRUG_NAME="orphan", STRUCT_ID=8, TARGET_NAME="Unmapped protein",
                  TARGET_CLASS="Unclassified", ACT_SOURCE="SCIENTIFIC LITERATURE",
                  ORGANISM="Homo sapiens"),
    ]
    with gzip.open(raw / "drug.target.interaction.tsv.gz", "wt", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    (raw / "structures.smiles.tsv").write_text(
        "SMILES\tInChI\tInChIKey\tID\tINN\tCAS_RN\n"
        "CCCCN1CCCCC1C(=O)NC1=C(C)C=CC=C1C\tInChI=1S/C18H28N2O\t"
        "LEBVLXFERQHONN-INIZCTEOSA-N\t4\tlevobupivacaine\t27262-47-1\n"
        "C\tInChI=1S/CH4\t\t9\tbiologic\t\n", encoding="utf-8")
    (raw / "FDA_Approved.csv").write_text("4,levobupivacaine\n864,diazepam\n",
                                          encoding="utf-8")
    (raw / "EMA_Approved.csv").write_text("864,diazepam\n", encoding="utf-8")
    (raw / "PMDA_Approved.csv").write_text("5096,pimavanserin\n", encoding="utf-8")


@pytest.fixture
def drugcentral(tmp_path):
    h = TCMDataHub(tmp_path / "hub")
    _drugcentral_files(h.raw_dir("drugcentral"))
    report = h.build("drugcentral", log=lambda m: None)
    return h, report


def _ctx(row):
    return json.loads(row["context"]) if row["context"] else {}


def test_drugcentral_quoted_tsv_loads_without_quotes(drugcentral):
    h, report = drugcentral
    assert report["tables"]["dti"] == 8
    rows = h.query("drugcentral", "dti", where={"STRUCT_ID": "4"})
    assert {r["DRUG_NAME"] for r in rows} == {"levobupivacaine"}
    assert {r["ACCESSION"] for r in rows} == {"Q12809", "P35499"}
    fda = h.query("drugcentral", "approved_fda")
    assert fda == [{"struct_id": "4", "name": "levobupivacaine"},
                   {"struct_id": "864", "name": "diazepam"}]


def test_drugcentral_activity_rows_keep_value_measure_and_upstream(drugcentral):
    h, report = drugcentral
    rows = h.relations("drug_target", subject="drugcentral:4", object="uniprot:Q12809")
    assert len(rows) == 1                                   # duplicate line deduplicated
    r = rows[0]
    assert r["evidence"] == "known" and r["outcome"] == "positive"
    assert r["score"] == "4.89" and r["effect"] is None     # no action type: no effect
    assert r["note"] == "via ChEMBL"
    assert r["object_name"] == "KCNH2 | Potassium voltage-gated channel subfamily H member 2"
    assert _ctx(r) == {"assay": "Inhibition of human ERG channel by patch clamp",
                       "measure": "IC50", "source_db": "CHEMBL", "species": "Homo sapiens",
                       "unit": "-log10(M)"}
    assert r["license"] == "CC BY-SA 4.0"


def test_drugcentral_mechanism_rows_take_the_effect_and_the_cited_paper(drugcentral):
    h, _ = drugcentral
    r = h.relations("drug_target", subject="drugcentral:4", object="uniprot:P35499")[0]
    assert r["effect"] == "inhibition" and r["reference"] == "pmid:12345678"
    assert r["score"] is None
    ctx = _ctx(r)
    assert ctx["action"] == "BLOCKER" and ctx["mechanism"] == "mechanism of action"
    assert "assay" not in ctx                    # "Mechanism of Action" is not an assay
    assert r["note"] == "via WOMBAT-PK"           # the label itself is DrugCentral's own
    r = h.relations("drug_target", subject="drugcentral:4964")[0]
    assert r["effect"] == "decrease"              # antisense lowers the amount
    assert r["reference"] == "doi:10.1000/xyz.123" and r["note"] is None
    r = h.relations("drug_target", subject="drugcentral:5096")[0]
    assert r["effect"] == "inhibition"            # an inverse agonist is not an agonist
    assert _ctx(r)["flags"] == "relation >" and r["note"] == "via PDSP"


def test_drugcentral_complex_targets_give_one_row_per_component(drugcentral):
    h, _ = drugcentral
    rows = h.relations("drug_target", subject="drugcentral:864")
    assert {(r["object_id"], r["object_name"]) for r in rows} == {
        ("uniprot:P14867", "GABRA1"), ("uniprot:P28472", "GABRB3"),
        ("uniprot:P18507", "GABRG2")}
    for r in rows:
        assert r["effect"] == "activation"        # a positive allosteric modulator
        assert _ctx(r)["action"] == "POSITIVE ALLOSTERIC MODULATOR"
        assert r["note"] == ("component of GABA-A receptor alpha-1/beta-3/gamma-2 "
                             "(3 components); via GtoPdb; ChEMBL")
        assert r["reference"].endswith("CHEMBL12")


def test_drugcentral_without_accession_goes_to_the_queue(drugcentral):
    h, report = drugcentral
    assert report["unresolved"] == 1
    q = h.unresolved("drugcentral")[0]
    assert q["subject_id"] == "drugcentral:8" and q["object_name"] == "Unmapped protein"
    assert not h.relations("drug_target", subject="drugcentral:8")
    rat = h.relations("drug_target", subject="drugcentral:7")[0]
    assert _ctx(rat)["species"] == "Rattus norvegicus" and rat["note"] == "via DrugMatrix"


def test_drugcentral_ids_meet_other_sources_through_the_crosswalk(drugcentral):
    from bioagent.tcmdb.consensus import Crosswalk
    h, _ = drugcentral
    cw = Crosswalk(h)
    assert cw.canon("drug", "drugcentral:4") == "inchikey:LEBVLXFERQHONN-INIZCTEOSA-N"
    assert cw.canon("drug", "drugcentral:9") == "drugcentral:9"         # no InChIKey
    assert cw.canon("target", "uniprot:Q12809") == "symbol:KCNH2"
    assert cw.canon("target", "uniprot:P99999") == "uniprot:P99999"     # rat: not HGNC
    assert cw.canon("target", "uniprot:P14867") == "uniprot:P14867"     # complex row


def test_drugcentral_rows_are_commercially_usable_share_alike(drugcentral):
    h, _ = drugcentral
    assert h.relations("drug_target", commercial=True, limit=100)
    result = h.check("drugcentral")
    assert result["licences"]["drug_target"]["class"] == "share-alike"
    assert not [p for p in result["problems"] if "catalogue" not in p]


# ---------------------------------------------------------------------------------- GDSC
_GDSC_HEADER = "DRUG_ID,SCREENING_SITE,DRUG_NAME,SYNONYMS,TARGET,TARGET_PATHWAY,CHEMBL_ID,INCHI_KEY"


@pytest.fixture
def gdsc(tmp_path):
    h = TCMDataHub(tmp_path / "hub")
    raw = h.raw_dir("gdsc")
    raw.mkdir(parents=True)
    (raw / "screened_compounds_rel_8.5_inchi.csv").write_text("\n".join([
        _GDSC_HEADER,
        '5,MGH,Sunitinib,"Sutent, SU-11248","PDGFR, KIT",RTK signaling,CHEMBL535,'
        'WINHZLLDWRZWRT-ATVHPVEESA-N',
        # the same compound at the other site: same rows, not new ones
        '1085,SANGER,Sunitinib,"Sutent, SU-11248","PDGFR, KIT",Other,CHEMBL535,'
        'WINHZLLDWRZWRT-ATVHPVEESA-N',
        '17,MGH,Cyclopamine,,"Tankyrase 1/2 (PARP5a, PARP5b)",Other,several,',
        '29,MGH,AZ628,AZ-628,BRAF,ERK MAPK signaling,CHEMBL2144069,',
        '30,SANGER,Mystery,,"not defined, others",Unclassified,none,',
    ]) + "\n", encoding="utf-8")
    (raw / "model_list_20260921.csv").write_text(
        "model_id,model_name,RRID\nSIDM00848,A673,CVCL_0080\n", encoding="utf-8")
    (raw / "GDSC2_fitted_dose_response_27Oct23.xlsx").write_bytes(b"PK\x03\x04 not read")
    report = h.build("gdsc", log=lambda m: None)
    return h, report


def test_gdsc_putative_targets_stay_gdsc_names(gdsc):
    h, report = gdsc
    assert report["relations"] == {"compound_target": 4}
    rows = h.relations("compound_target", subject="inchikey:WINHZLLDWRZWRT-ATVHPVEESA-N")
    assert {r["object_id"] for r in rows} == {"gdsc:target.PDGFR", "gdsc:target.KIT"}
    assert all(r["evidence"] == "listed" and r["effect"] is None for r in rows)
    assert "Sutent" in rows[0]["subject_name"].split(" | ")
    # a comma inside parentheses does not split the target
    cyc = h.relations("compound_target", subject="gdsc:drug.17")
    assert [r["object_id"] for r in cyc] == ["gdsc:target.Tankyrase 1/2 (PARP5a, PARP5b)"]
    # a ChEMBL id is used only when it is one ("several" is not)
    assert h.relations("compound_target", subject="chembl:CHEMBL2144069")
    # "not defined" and "others" are no target at all
    assert not h.relations("compound_target", subject="gdsc:drug.30")


def test_gdsc_screen_tables_are_kept_as_files_and_rows_are_non_commercial(gdsc):
    h, report = gdsc
    assert "GDSC2_fitted_dose_response_27Oct23.xlsx" in report["skipped"]
    assert set(report["tables"]) == {"compounds", "models"}
    assert h.relations("compound_target", commercial=True) == []
    from bioagent.tcmdb.datasets import dataset
    assert dataset("gdsc").commercial_use == "forbidden"
    assert h.relations("compound_target")[0]["license"].startswith("DepMap at Sanger")


# -------------------------------------------------------------------------------- Pharos
_PHAROS_HEADER = ("id,uniprot_id,tdl,uniprot_reviewed,uniprot_annotationScore,name,xref,"
                  "idg_family,uniprot_function,symbol,ncbi_id,ensembl_id,"
                  "canonical_isoform_status,uniprot_isoform,tdl_ligand_count,tdl_drug_count,"
                  "tdl_go_term_count,tdl_generif_count,tdl_pm_score,tdl_antibody_count")


@pytest.fixture
def pharos(tmp_path):
    h = TCMDataHub(tmp_path / "hub")
    raw = h.raw_dir("pharos")
    raw.mkdir(parents=True)
    (raw / "pharos400_tdls_canonicals.csv").write_text("\n".join([
        _PHAROS_HEADER,
        'IFXProtein:A,P00533,Tclin,True,5,Epidermal growth factor receptor,'
        '"[""Symbol:EGFR""]",Kinase,Receptor tyrosine kinase,EGFR,1956,ENSP00000275493.2,'
        'canonical,P00533-1,4421,15,120,900,5000.5,1200',
        'IFXProtein:B,V9GXZ4,Tdark,False,1,Uncharacterised protein,"[]",,,,,,canonical,,0,'
        '0,0,0,0.1,3',
    ]) + "\n", encoding="utf-8")
    report = h.build("pharos", log=lambda m: None)
    return h, report


def test_pharos_levels_and_families_are_gene_sets(pharos):
    h, report = pharos
    assert report["relations"] == {"gene_set_member": 3}
    egfr = {r["subject_id"]: r for r in h.relations("gene_set_member",
                                                     object="uniprot:P00533")}
    assert set(egfr) == {"pharos:tdl.Tclin", "pharos:family.Kinase"}
    tclin = egfr["pharos:tdl.Tclin"]
    assert tclin["evidence"] == "listed" and tclin["context"] is None
    assert tclin["note"] == "ligands=4421; drugs=15"
    assert tclin["object_name"] == "EGFR | Epidermal growth factor receptor"
    dark = h.relations("gene_set_member", object="uniprot:V9GXZ4")
    assert [r["subject_id"] for r in dark] == ["pharos:tdl.Tdark"]
    assert _ctx(dark[0]) == {"flags": "unreviewed"}
    # no licence of its own: not offered for commercial reuse
    assert h.relations("gene_set_member", commercial=True) == []


def test_pharos_maps_uniprot_to_symbols(pharos):
    from bioagent.tcmdb.consensus import Crosswalk
    h, _ = pharos
    cw = Crosswalk(h)
    assert cw.canon("protein", "uniprot:P00533") == "symbol:EGFR"
    assert cw.canon("protein", "uniprot:V9GXZ4") == "uniprot:V9GXZ4"


# ------------------------------------------------------------------------------ DrugComb
def test_drugcomb_keeps_the_synergy_table_as_a_file(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    h = TCMDataHub(tmp_path / "hub")
    raw = h.raw_dir("drugcomb")
    raw.mkdir(parents=True)
    (raw / "summary_table_v1.4.csv").write_text(
        '"block_id","drug_row","drug_col","cell_line_name","conc_r_unit","conc_c_unit",'
        '"css","synergy_zip","synergy_bliss","synergy_loewe","synergy_hsa","ic50_row",'
        '"ic50_col","ri_row","ri_col","css_row","css_col","S"\n'
        '"1","5-FU","ABT-888","A2058","uM","uM","30.9","3.9","6.3","-2.9","5.5","5.1",'
        '"3.3","11.5","-0.4","22.5","39.2","-4.1"\n', encoding="utf-8")
    for name, sheet, header, row in (
            ("DrugComb_drug_identifiers.xlsx", "drug",
             ("dname", "id", "chembl_id", "inchikey", "cid", "target_name"),
             ("5-Fluorouracil", "1", "CHEMBL185", "GHASVSINZRGABV-UHFFFAOYSA-N", "3385",
              "Thymidylate synthase; Prelamin-A/C")),
            ("DrugComb_cell_line_identifiers.xlsx", "cell_line",
             ("name", "cellosaurus_accession", "depmap_id"),
             ("A2058", "CVCL_1059", "ACH-000788"))):
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = sheet
        ws.append(list(header))
        ws.append(list(row))
        wb.save(raw / name)
    report = h.build("drugcomb", log=lambda m: None)
    assert report["tables"] == {"drugs": 1, "cell_lines": 1}
    assert report["skipped"] == ["summary_table_v1.4.csv"]
    assert report["relations"] == {}
    assert h.query("drugcomb", "cell_lines")[0]["cellosaurus_accession"] == "CVCL_1059"
    from bioagent.tcmdb.datasets import dataset
    spec = dataset("drugcomb")
    assert spec.commercial_use == "unknown" and not spec.relations
    assert all(f.optional for f in spec.files if (f.expected_bytes or 0) > 300_000_000)


# ---------------------------------------------------------------------------- the specs
def test_specs_declare_catalogue_numbers_lineage_and_large_files_optional():
    from bioagent.tcmdb.datasets import dataset
    expected = {"drugcentral": 105, "drugcomb": 106, "gdsc": 108, "pharos": 109}
    for key, no in expected.items():
        spec = dataset(key)
        assert spec.catalog == (no,)
        for f in spec.files:
            if (f.expected_bytes or 0) > 300_000_000:
                assert f.optional, (key, f.name)
    assert "ChEMBL" in dataset("drugcentral").upstream
    assert dataset("drugcentral").commercial_use == "allowed"
    assert dataset("pharos").commercial_use == "unknown"


# --------------------------------------------------------------------------- connectors
def _allowed(host: str) -> bool:
    from bioagent.policy import PROFILES, PolicyDecision
    ruling = PROFILES["biomedical-research"].check_network([host])
    return ruling.decision is PolicyDecision.ALLOW


def test_every_drug_host_is_in_the_allowlist():
    import urllib.parse

    from bioagent.providers.public_apis import BY_KEY
    from bioagent.tcmdb.datasets import dataset
    for key in ("drugcentral", "pharmacodb", "pharos"):
        assert _allowed(BY_KEY[key].host), key
    for key in ("drugcentral", "drugcomb", "gdsc", "pharos"):
        for f in dataset(key).files:
            assert _allowed(urllib.parse.urlsplit(f.url).hostname), f.url
    # the App Runner host is listed exactly, never the shared suffix
    assert not _allowed("someone-else.us-east-2.awsapprunner.com")


def test_drugcentral_requests_are_the_ones_verified():
    from bioagent.providers.public_apis import BY_KEY, render_call
    assert BY_KEY["drugcentral"].base_url == "https://uxn2ycvimg.us-east-2.awsapprunner.com"
    expected = {
        "drug_by_name": "synonyms/name/imatinib",
        "structure": "structures/id/1423",
        "structure_by_inchikey": "structures/inchikey/KTUFNOKKBVMGRW-UHFFFAOYSA-N",
        "identifiers": "identifier/struct_id/1423",
        "activities": "act_table_full/struct_id/1423",
        "target_activities": "act_table_full/accession/P00519",
        "gene_activities": "act_table_full/gene/EGFR",
        "indications": "omop_relationship/struct_id/1423",
        "atc": "struct2atc/struct_id/1423",
        "faers": "faers/struct_id/1423",
    }
    assert {op.name for op in BY_KEY["drugcentral"].operations} == set(expected)
    for op, path in expected.items():
        call = render_call("drugcentral", op)
        assert (call["method"], call["path"], call["params"]) == ("GET", path, {}), op


def test_pharmacodb_and_pharos_send_typed_graphql_variables():
    from bioagent.providers.public_apis import BY_KEY, render_call
    call = render_call("pharmacodb", "experiments")
    assert call["method"] == "POST" and call["path"] == "graphql"
    assert call["variables"] == {"name": "Paclitaxel", "page": 1, "per_page": 5}
    assert "compoundName:$name" in call["graphql"]
    call = render_call("pharmacodb", "experiments_in_cell_line", compound="Lapatinib",
                       cell_line="BT-474")
    assert call["variables"] == {"name": "Lapatinib", "cell": "BT-474"}
    assert render_call("pharmacodb", "biomarkers")["variables"] == {
        "gene": "ERBB2", "compound": "Lapatinib", "per_page": 10}
    assert render_call("pharmacodb", "dataset_stats")["variables"] == {}
    call = render_call("pharos", "target_ligands")
    assert call["variables"] == {"q": {"sym": "EGFR"}, "top": 3, "isdrug": True}
    assert render_call("pharos", "target", symbol="TP53")["variables"] == {
        "q": {"sym": "TP53"}}
    assert render_call("pharos", "ligand")["variables"] == {"id": "DC:1423"}
    assert BY_KEY["pharos"].smoke == "version"
