"""Reference spectra (MassBank, GNPS, NP-MRD) and the spectra/metabolomics connectors.

Every file here is a tiny synthetic file written in the source's real format (MassBank
record text in a release-style zip, GNPS per-library JSON, NP-MRD zips of headerless CSVs,
its JSON cards and SMILES table). No network.
"""

from __future__ import annotations

import gzip
import io
import json
import sqlite3
import zipfile

import pytest

from bioagent.tcmdb import TCMDataHub
from bioagent.tcmdb.extra import spectra as sp

pytestmark = pytest.mark.unit

RUTIN = "IKGXIBQEEMLURG-NVPNHPEKSA-N"
QUERCETIN = "REFJWTPEDVJJIY-UHFFFAOYSA-N"
PROLINE = "ONIBWKKTOPOVIA-BYPYZUCNSA-N"


@pytest.fixture(autouse=True)
def _cards(monkeypatch):
    """Catalogue cards 76-78 (added to the catalogue when the domains are integrated)."""
    from bioagent.tcmdb import hub as hubmod
    real = hubmod.catalog()
    have = {c.no for c in real}
    extra = tuple(hubmod.SourceCard(no=n, name=k, modules=("M14",), url="",
                                    access="snapshot", connector=None, dataset=k, license="",
                                    barriers="", assessment="", checked="2026-10-01",
                                    origin="supplement")
                  for n, k in ((76, "massbank"), (77, "gnps"), (78, "np_mrd"))
                  if n not in have)
    monkeypatch.setattr(hubmod, "catalog", lambda: real + extra)


# ================================================================== MassBank
def _record(acc, name, *, licence="CC BY", confidence=None, inchikey=None, cid=None,
            species=None, taxid=None, sample=None, deprecated=None, publication=None):
    lines = [f"ACCESSION: {acc}"]
    if deprecated:
        lines.append(f"DEPRECATED: {deprecated}")
    lines += [f"RECORD_TITLE: {name}; LC-ESI-QTOF; MS2; CE: 30; [M+H]+",
              "DATE: 2017.12.01", "AUTHORS: A Lab", f"LICENSE: {licence}"]
    if publication:
        lines.append(f"PUBLICATION: {publication}")
    if confidence:
        lines.append(f"COMMENT: CONFIDENCE {confidence}")
    lines += [f"CH$NAME: {name}", "CH$COMPOUND_CLASS: Natural Product",
              "CH$FORMULA: C27H30O16", "CH$EXACT_MASS: 610.1534", "CH$SMILES: C",
              "CH$IUPAC: InChI=1S/C"]
    if cid:
        lines.append(f"CH$LINK: PUBCHEM CID:{cid}")
    if inchikey:
        lines.append(f"CH$LINK: INCHIKEY {inchikey}")
    if species:
        lines.append(f"SP$SCIENTIFIC_NAME: {species}")
    if taxid:
        lines.append(f"SP$LINK: NCBI-TAXONOMY {taxid}")
    if sample:
        lines.append(f"SP$SAMPLE: {sample}")
    lines += ["AC$INSTRUMENT: Bruker impact HD", "AC$INSTRUMENT_TYPE: LC-ESI-QTOF",
              "AC$MASS_SPECTROMETRY: MS_TYPE MS2", "AC$MASS_SPECTROMETRY: ION_MODE POSITIVE",
              "AC$MASS_SPECTROMETRY: COLLISION_ENERGY 30 eV",
              "MS$FOCUSED_ION: PRECURSOR_M/Z 611.16", "MS$FOCUSED_ION: PRECURSOR_TYPE [M+H]+",
              "PK$SPLASH: splash10-0a4i-0000009000-270308e3f77a0404dea8",
              "PK$ANNOTATION: m/z tentative_formula formula_count mass error(ppm)",
              "  303.0499 C15H11O7+ 1 303.0499 0.1",
              "PK$NUM_PEAK: 2", "PK$PEAK: m/z int. rel.int.", "  303.0499 999 999",
              "  465.1027 120 120", "//"]
    return "\n".join(lines) + "\n"


def _massbank_zip(path):
    top = "MassBank-MassBank-data-705afb7/"
    records = {
        "BS/MSBNK-BS-BS003074.txt": _record(
            "MSBNK-BS-BS003074", "Rutin", licence="CC BY-NC-SA", inchikey=RUTIN,
            cid="5280805", confidence="Reference Standard (Level 1)",
            publication="Fine D, et al. J Exp Bot (2014). [PMID: 24700000]"),
        "SMB_Measured/MSBNK-SMB_Measured-HSA001P0116000.txt": _record(
            "MSBNK-SMB_Measured-HSA001P0116000", "L-Proline", licence="CC BY",
            inchikey=PROLINE, species="Homo sapiens", taxid="9606 ", sample="Serum",
            confidence="Tentative candidate (Level 3); SMB Level 3c"),
        "ISAS_Dortmund/MSBNK-ISAS_Dortmund-IA000175.txt": _record(
            "MSBNK-ISAS_Dortmund-IA000175", "5-OxoETE-[d7]", licence="CC BY-SA",
            inchikey="MEASLHGILYBXFO-XTDASVJISA-N", confidence="standard compound",
            deprecated="2019-11-25 Wrong MS measurement assigned"),
        "Eawag/MSBNK-EAWAG-EC001501.txt": _record(
            "MSBNK-EAWAG-EC001501", "Quercetin-like", licence="CC0", cid="5280343",
            confidence="2a"),
        "Eawag/MSBNK-EAWAG-EC001502.txt": _record(
            "MSBNK-EAWAG-EC001502", "Unknown mixture", licence="CC BY"),
        "RIKEN_ReSpect/MSBNK-RIKEN_ReSpect-PM000301.txt": _record(
            "MSBNK-RIKEN_ReSpect-PM000301", "Quercetin", licence="CC BY-SA",
            inchikey=QUERCETIN),
        "IPB_Halle/MSBNK-IPB_Halle-PB010101.txt": _record(
            "MSBNK-IPB_Halle-PB010101", "Marchantin G", licence="CC BY",
            inchikey="CUIZSIJMLPQKRE-UHFFFAOYSA-N", confidence="Predicted"),
        "BAFG/MSBNK-BAFG-CSL23111000001.txt": _record(
            "MSBNK-BAFG-CSL23111000001", "Atrazine", licence="dl-de/by-2-0",
            inchikey="MXWJVTOOROXGIU-UHFFFAOYSA-N", confidence="Reference Standard (Level 1)"),
    }
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(top + "README.md", "# MassBank-data\n")
        z.writestr(top + ".github/workflows/release.yml", "on: push\n")
        for name, text in records.items():
            z.writestr(top + name, text)


@pytest.fixture
def massbank(tmp_path):
    h = TCMDataHub(tmp_path / "hub")
    raw = h.raw_dir("massbank")
    raw.mkdir(parents=True)
    _massbank_zip(raw / "MassBank-data-2026.03.zip")
    return h, h.build("massbank", log=lambda m: None)


def test_massbank_loads_one_row_per_record_without_peaks(massbank):
    h, report = massbank
    assert report["tables"] == {"record": 8}
    with sqlite3.connect(h.db_path("massbank")) as conn:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(record)")]
        row = conn.execute("SELECT * FROM record WHERE accession='MSBNK-BS-BS003074'") \
            .fetchone()
    rec = dict(zip(cols, row))
    assert not any("peak" in c and c != "num_peak" for c in cols)
    assert rec["num_peak"] == "2" and rec["license"] == "CC BY-NC-SA"
    assert rec["inchikey"] == RUTIN and rec["pubchem"] == "CID:5280805"
    assert rec["confidence"] == "Reference Standard (Level 1)"
    assert rec["precursor_type"] == "[M+H]+" and rec["ion_mode"] == "POSITIVE"


def test_massbank_rows_carry_their_own_record_licence_and_confidence(massbank):
    h, report = massbank
    rows = {r["object_id"]: r for r in h.relations("compound_spectrum", limit=100)}
    rutin = rows["massbank:MSBNK-BS-BS003074"]
    assert rutin["subject_id"] == f"inchikey:{RUTIN}"
    assert (rutin["evidence"], rutin["outcome"]) == ("known", "positive")
    assert rutin["license"] == "CC BY-NC-SA" and rutin["reference"] == "pmid:24700000"
    assert rutin["note"].startswith("SPLASH splash10-")
    ctx = json.loads(rutin["context"])
    assert ctx["instrument"] == "Bruker impact HD" and ctx["method"] == "LC-ESI-QTOF"
    assert ctx["assay"] == "MS2" and ctx["condition"] == "CE 30 eV; [M+H]+"
    # a tentative (level 3) identification is reported but not counted as support
    proline = rows["massbank:MSBNK-SMB_Measured-HSA001P0116000"]
    assert (proline["evidence"], proline["outcome"]) == ("reported", "inconclusive")
    # no InChIKey: the PubChem CID is the compound; level 2a is reported
    eawag = rows["massbank:MSBNK-EAWAG-EC001501"]
    assert eawag["subject_id"] == "pubchem:5280343"
    assert (eawag["evidence"], eawag["outcome"]) == ("reported", "positive")
    assert rows["massbank:MSBNK-IPB_Halle-PB010101"]["evidence"] == "predicted"
    # a record of another database names it as its lineage
    assert rows["massbank:MSBNK-RIKEN_ReSpect-PM000301"]["note"].endswith("via ReSpect")
    # NC records are left out of a commercial query, CC0/CC BY ones stay
    commercial = {r["object_id"] for r in h.relations("compound_spectrum", commercial=True,
                                                      limit=100)}
    assert "massbank:MSBNK-BS-BS003074" not in commercial
    assert "massbank:MSBNK-EAWAG-EC001501" in commercial


def test_a_deprecated_massbank_record_is_inconclusive_and_flagged(massbank):
    h, _ = massbank
    row = next(r for r in h.relations("compound_spectrum", limit=100)
               if r["object_id"] == "massbank:MSBNK-ISAS_Dortmund-IA000175")
    assert row["outcome"] == "inconclusive"
    assert json.loads(row["context"])["flags"].startswith("deprecated: 2019-11-25")


def test_a_massbank_compound_without_structure_ids_waits_in_the_queue(massbank):
    h, report = massbank
    assert report["unresolved"] == 1
    queue = h.unresolved("massbank")
    assert queue[0]["subject_name"] == "Unknown mixture"
    assert queue[0]["object_name"] == "massbank:MSBNK-EAWAG-EC001502"
    objects = {r["object_id"] for r in h.relations("compound_spectrum", limit=100)}
    assert "massbank:MSBNK-EAWAG-EC001502" not in objects


def test_massbank_sample_organisms_become_organism_compound_rows(massbank):
    h, _ = massbank
    rows = h.relations("organism_compound", limit=100)
    assert len(rows) == 1
    row = rows[0]
    assert row["subject_id"] == "ncbitaxon:9606" and row["object_id"] == f"inchikey:{PROLINE}"
    assert (row["evidence"], row["outcome"]) == ("reported", "inconclusive")
    assert json.loads(row["context"])["tissue"] == "Serum"


def test_massbank_passes_the_acceptance_check_and_maps_cids(massbank):
    from bioagent.tcmdb.consensus import Crosswalk
    h, _ = massbank
    result = h.check("massbank")
    assert result["ok"], result["problems"]
    assert result["unresolved"] == 1
    assert Crosswalk(h).canon("compound", "pubchem:5280805") == f"inchikey:{RUTIN}"


@pytest.mark.parametrize("text,expected", [
    (None, ("known", "positive")),
    ("standard compound", ("known", "positive")),
    ("Reference Standard (Level 1)", ("known", "positive")),
    ("1", ("known", "positive")),
    ("Pure standard", ("known", "positive")),
    ("2b", ("reported", "positive")),
    ("confident structure", ("reported", "positive")),
    (": Probable structure confirmed via library spectrum match (Level 2a)",
     ("reported", "positive")),
    ("3", ("reported", "inconclusive")),
    ("2b (tentative)", ("reported", "inconclusive")),
    ("Tentative identification: molecular formula only (Level 4)",
     ("reported", "inconclusive")),
    ("structure hypothesis", ("reported", "inconclusive")),
    ("Predicted", ("predicted", "positive")),
    ("Claviceps purpurea sclerotia", ("reported", "positive")),
])
def test_massbank_confidence_statements_map_to_evidence(text, expected):
    assert sp.massbank_confidence(text) == expected


# ======================================================================= GNPS
def _gnps_rec(sid, name, cls, *, key=None, library="LEAFBOT", splash="splash10-abc",
              pubmed="N/A", source="Commercial"):
    return {"spectrum_id": sid, "source_file": "f.mgf", "task": "t", "scan": "1",
            "ms_level": "2", "library_membership": library, "spectrum_status": "1",
            "peaks_json": "[[108.08,0.33],[109.09,0.51]]", "splash": splash,
            "submit_user": "u", "Compound_Name": name, "Ion_Source": "LC-ESI",
            "Compound_Source": source, "Instrument": "qTof", "PI": "P",
            "Data_Collector": "D", "Adduct": "M+H", "Scan": "-1", "Precursor_MZ": "281.2",
            "ExactMass": "280.19", "Charge": "1", "CAS_Number": "N/A", "Pubmed_ID": pubmed,
            "Smiles": "C" if key else "N/A", "INCHI": "N/A", "INCHI_AUX": "N/A",
            "Library_Class": cls, "SpectrumID": sid, "Ion_Mode": "Positive",
            "create_time": "2024-05-13 09:21:37.0", "task_id": "t", "user_id": "null",
            "InChIKey_smiles": key or "", "InChIKey_inchi": "None",
            "Formula_smiles": "", "Formula_inchi": "",
            "url": f"https://gnps.ucsd.edu/ProteoSAFe/gnpslibraryspectrum.jsp?SpectrumID={sid}",
            "annotation_history": [{"Compound_Name": name}]}


@pytest.fixture
def gnps(tmp_path):
    h = TCMDataHub(tmp_path / "hub")
    raw = h.raw_dir("gnps")
    raw.mkdir(parents=True)
    gold = _gnps_rec("CCMSLIB00000000001", '"RUTIN"', "1", key=RUTIN)
    (raw / "LEAFBOT.json").write_text(json.dumps([
        gold,
        _gnps_rec("CCMSLIB00000000002", "Quercetin", "2", key=QUERCETIN, pubmed="31000000",
                  source="Isolated"),
        _gnps_rec("CCMSLIB00000000003", "putative flavone", "3", key=QUERCETIN,
                  splash="null-null-null-null"),
        _gnps_rec("CCMSLIB00000000004", "Unknown 281", "10"),
        _gnps_rec("CCMSLIB00000000005", "no structure", "3"),
    ]))
    # the same library spectrum exported in a second file counts once
    (raw / "PHENOLICSDB.json").write_text(json.dumps([gold]))
    (raw / "MASSBANKEU.json").write_text(json.dumps([
        _gnps_rec("CCMSLIB00000400001", "Quercetin", "1", key=QUERCETIN,
                  library="MASSBANKEU")]))
    return h, h.build("gnps", log=lambda m: None)


def test_gnps_loads_spectra_without_their_peaks(gnps):
    h, report = gnps
    assert report["tables"]["lib_leafbot"] == 5
    with sqlite3.connect(h.db_path("gnps")) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(lib_leafbot)")}
    assert "peaks_json" not in cols and "annotation_history" not in cols
    assert {"spectrum_id", "Library_Class", "InChIKey_smiles", "splash"} <= cols


def test_gnps_quality_classes_map_to_evidence_and_outcome(gnps):
    h, _ = gnps
    rows = {r["object_id"]: r for r in h.relations("compound_spectrum", limit=100)}
    gold = rows["gnps:CCMSLIB00000000001"]
    assert gold["subject_id"] == f"inchikey:{RUTIN}" and gold["subject_name"] == "RUTIN"
    assert (gold["evidence"], gold["outcome"]) == ("known", "positive")
    ctx = json.loads(gold["context"])
    assert ctx["confidence"] == "gold" and ctx["library"] == "LEAFBOT"
    assert ctx["condition"] == "M+H" and ctx["sample"] == "Commercial"
    assert gold["license"].startswith("CC0")
    silver = rows["gnps:CCMSLIB00000000002"]
    assert (silver["evidence"], silver["outcome"]) == ("reported", "positive")
    assert silver["reference"] == "pmid:31000000"
    bronze = rows["gnps:CCMSLIB00000000003"]
    assert (bronze["evidence"], bronze["outcome"]) == ("reported", "inconclusive")
    assert bronze["note"] is None                     # null-null-null-null is no SPLASH
    assert bronze["reference"] is None                # N/A is no paper


def test_gnps_challenge_and_structureless_spectra_wait_in_the_queue(gnps):
    h, report = gnps
    assert report["unresolved"] == 2
    reasons = {q["object_name"]: q["reason"] for q in h.unresolved("gnps")}
    assert "challenge" in reasons["gnps:CCMSLIB00000000004"]
    assert "no structure" in reasons["gnps:CCMSLIB00000000005"]


def test_gnps_deduplicates_a_spectrum_and_marks_imported_lineage(gnps):
    h, _ = gnps
    rows = h.relations("compound_spectrum", limit=100)
    assert [r["object_id"] for r in rows].count("gnps:CCMSLIB00000000001") == 1
    imported = next(r for r in rows if r["object_id"] == "gnps:CCMSLIB00000400001")
    assert imported["note"].endswith("via MassBank")
    assert "NC" in imported["license"]               # MassBank's licences, not CC0
    from bioagent.tcmdb.consensus import lineage_of
    assert lineage_of(imported) == frozenset({"MASSBANK"})
    commercial = {r["object_id"] for r in h.relations("compound_spectrum", commercial=True,
                                                      limit=100)}
    assert "gnps:CCMSLIB00000400001" not in commercial
    assert "gnps:CCMSLIB00000000001" in commercial
    # the fixture holds two of the twelve default libraries: that is all the check finds
    problems = h.check("gnps")["problems"]
    assert problems and all(p.startswith("missing file ") for p in problems)


def test_propagated_gnps_libraries_are_recognised():
    assert sp._gnps_propagated("GNPS-IIMN-PROPOGATED")
    assert sp._gnps_propagated("MULTIPLEX-SYNTHESIS-LIBRARY-ALL-PARTITION-1")
    assert not sp._gnps_propagated("LEAFBOT")


# ===================================================================== NP-MRD
def _zip(path, members):
    with zipfile.ZipFile(path, "w") as z:
        for name, text in members.items():
            z.writestr(name, text)


@pytest.fixture
def np_mrd(tmp_path):
    h = TCMDataHub(tmp_path / "hub")
    raw = h.raw_dir("np_mrd")
    raw.mkdir(parents=True)
    _zip(raw / "peak_lists.zip", {
        "NP0000003_1474_peak_list.csv": "H,7.97\nH,6.39\nH,2.75\n",
        "NP0333371_1938_peak_list.csv": "H,7.49\nC,128.1\n"})
    _zip(raw / "assignment_tables.zip", {
        "690_NP0000003_176_assignmenttable.txt": 'O,1,,,\nC,2,166.3,s,\n'
                                                  'H,14,8.3,dd,"6.4,1.4"\n',
        "552_NP0000003_176_chsqc_assignmenttable.txt": "H,16,C,5,3.14,40.4\n",
        "633_NP0002679_126_user_assignmenttable.txt": "O,1,,,\nN,2,,,\n"})
    with gzip.open(raw / "smiles_NP0000001_NP0050000.csv.gz", "wt") as fh:
        fh.write("Natural_Products_Name,NP_MRD_ID,SMILES\n"
                 "Psoralen,NP0000003,O=C1OC2=CC3=C(C=CO3)C=C2C=C1\n"
                 "Cotinine,NP0002679,CN1C(CCC1=O)C1=CN=CC=C1\n")
    with gzip.open(raw / "smiles_NP0300001_NP0350000.csv.gz", "wt") as fh:
        fh.write("Natural_Products_Name,NP_MRD_ID,SMILES\nCompound X,NP0333371,CCO\n")
    cards = {"np_mrd": {"natural_product": [
        {"version": "1.0", "accession": "NP0000003", "name": "Psoralen",
         "synonyms": {"synonym": "Ficusin"}, "inchikey": "ZCCUUQDIBDJBTK-UHFFFAOYSA-N",
         "taxonomy": {"kingdom": "Organic compounds", "class": "Coumarins"},
         "chebi_id": "27616", "pubchem_compound_id": None, "description": "x, y",
         "general_references": {"reference": [{"reference_text": "r",
                                                "pubmed_id": "123"}]}},
        {"version": "1.0", "accession": "NP0002679", "name": "Cotinine",
         "synonyms": {"synonym": ["a", "b"]}, "inchikey": None}]}}
    with zipfile.ZipFile(raw / "npmrd_natural_products_NP0000001_NP0050000_json.zip",
                         "w") as z:
        z.writestr("npmrd_natural_products_NP0000001_NP0050000.json",
                   json.dumps(cards, indent=2))
    return h, h.build("np_mrd", log=lambda m: None)


def test_np_mrd_relates_compounds_to_their_experimental_nmr_tables(np_mrd):
    h, report = np_mrd
    assert report["tables"]["peak_list"] == 5 and report["tables"]["card_1"] == 2
    assert "card_7" not in report["tables"]               # the JSON chunk was not given
    rows = {r["object_id"]: r for r in h.relations("compound_spectrum", limit=100)}
    # a table that lists atoms but no shift is no measurement: no row
    assert set(rows) == {"np_mrd:NP0000003_1474_peak_list", "np_mrd:NP0333371_1938_peak_list",
                         "np_mrd:690_NP0000003_176_assignmenttable",
                         "np_mrd:552_NP0000003_176_chsqc_assignmenttable"}
    peaks = rows["np_mrd:NP0000003_1474_peak_list"]
    assert peaks["subject_id"] == "inchikey:ZCCUUQDIBDJBTK-UHFFFAOYSA-N"
    assert peaks["subject_name"] == "Psoralen"
    assert (peaks["evidence"], peaks["outcome"]) == ("known", "positive")
    ctx = json.loads(peaks["context"])
    assert ctx == {"assay": "deposited peak list (unassigned)", "condition": "nuclei 1H",
                   "measure": "chemical shift", "method": "NMR", "source_id": "NP0000003",
                   "unit": "ppm"}
    # without its JSON card the compound keeps its NP-MRD id
    other = rows["np_mrd:NP0333371_1938_peak_list"]
    assert other["subject_id"] == "np_mrd:NP0333371" and other["subject_name"] == "Compound X"
    assert "13C, 1H" in other["object_name"]
    hsqc = json.loads(rows["np_mrd:552_NP0000003_176_chsqc_assignmenttable"]["context"])
    assert hsqc["assay"] == "C-HSQC chemical-shift assignment"
    assert hsqc["condition"] == "nuclei 13C, 1H"
    assert h.relations("compound_spectrum", commercial=True, limit=100) == []
    result = h.check("np_mrd")
    assert result["licences"]["compound_spectrum"]["class"] == "non-commercial"
    # six SMILES chunks and one JSON chunk are not in the fixture: nothing else fails
    assert all(p.startswith("missing file ") for p in result["problems"])


def test_np_mrd_assignment_rows_keep_both_layouts(np_mrd):
    h, _ = np_mrd
    with sqlite3.connect(h.db_path("np_mrd")) as conn:
        hsqc = conn.execute("SELECT atom, atom_id, shift_ppm, atom2, atom2_id, shift2_ppm "
                            "FROM assignment WHERE table_kind='chsqc'").fetchone()
        one_d = conn.execute("SELECT multiplicity, j_hz FROM assignment WHERE atom_id='14'"
                             ).fetchone()
        card = conn.execute("SELECT synonyms, class, pubmed_ids FROM card_1 "
                            "WHERE accession='NP0000003'").fetchone()
    assert hsqc == ("H", "16", "3.14", "C", "5", "40.4")
    assert one_d == ("dd", "6.4,1.4")
    assert card == ("Ficusin", "Coumarins", "123")


def test_the_card_stream_survives_items_split_across_reads():
    doc = {"np_mrd": {"natural_product": [{"accession": f"NP{i:07d}", "name": "a, [b] {c}"}
                                          for i in range(5)]}}
    items = list(sp._array_items(io.StringIO(json.dumps(doc, indent=1)), "natural_product",
                                 chunk=7))
    assert [x["accession"] for x in items] == [f"NP{i:07d}" for i in range(5)]
    assert list(sp._array_items(io.StringIO('{"np_mrd": {"natural_product": []}}'),
                                "natural_product", chunk=3)) == []


def test_np_mrd_keeps_predicted_spectra_apart():
    from bioagent.tcmdb.datasets import dataset
    spec = dataset("np_mrd")
    predicted = spec.file("predicted_nmrml_spectra.zip")
    assert predicted.optional and predicted.fmt == "raw" and not predicted.table
    assert "predict" in predicted.note.lower()
    # two of the seven JSON chunks by default; the rest on request
    cards = [f for f in spec.files if f.fmt == "npmrd_cards"]
    assert len(cards) == 7 and sum(not f.optional for f in cards) == 2
    assert spec.commercial_use == "forbidden"


# =================================================================== the specs
def test_spectra_specs_are_registered_with_catalogue_numbers_and_licences():
    from bioagent.tcmdb.datasets import dataset
    from bioagent.tcmdb.relations import EXTRACTORS
    for key, no in (("massbank", 76), ("gnps", 77), ("np_mrd", 78)):
        spec = dataset(key)
        assert spec.catalog == (no,) and key in EXTRACTORS
        assert set(spec.relations) <= {"compound_spectrum", "organism_compound"}
        for f in spec.files:
            if (f.expected_bytes or 0) > 300_000_000:
                assert f.optional, f.name
    assert dataset("gnps").file("MASSBANKEU.json").optional
    assert dataset("massbank").file("MassBank-data-2026.03.zip").fmt == "massbank_records"


# ================================================================= connectors
@pytest.mark.parametrize("key,op,kwargs,method,path,params", [
    ("massbank", "records_search", {"inchikey": RUTIN}, "GET",
     "MassBank-api/records/search", {"inchi_key": RUTIN}),
    ("massbank", "records", {"inchikey": RUTIN}, "GET", "MassBank-api/records",
     {"inchi_key": RUTIN}),
    ("massbank", "record", {"accession": "MSBNK-BS-BS003074"}, "GET",
     "MassBank-api/records/MSBNK-BS-BS003074", {}),
    ("massbank", "record_text", {"accession": "MSBNK-BS-BS003074"}, "GET",
     "MassBank-export/rawtext/MSBNK-BS-BS003074", {}),
    ("massbank", "peak_search", {"peak_list": "133.06;225,151.07;94", "threshold": "0.8"},
     "GET", "MassBank-api/records/search",
     {"peak_list": "133.06;225,151.07;94", "peak_list_threshold": "0.8"}),
    ("gnps", "spectrum", {"spectrum_id": "CCMSLIB00000001547"}, "GET", "gnpsspectrum",
     {"SpectrumID": "CCMSLIB00000001547"}),
    ("gnps", "libraries", {}, "GET", "gnpslibrary.json", {}),
    ("gnps_usi", "spectrum", {"usi": "mzspec:GNPS:GNPS-LIBRARY:accession:CCMSLIB1"}, "GET",
     "json/", {"usi1": "mzspec:GNPS:GNPS-LIBRARY:accession:CCMSLIB1"}),
    ("gnps_explorer", "dataset_files", {"accession": "MSV000084794"}, "GET",
     "api/datasets/MSV000084794/files", {}),
    ("massive", "dataset", {"accession": "MSV000084794"}, "GET", "datasets/MSV000084794", {}),
    ("massive", "dataset_search", {"text": "Ginkgo", "page_size": 5, "page": 1}, "GET",
     "datasets", {"resultType": "compact", "pageSize": 5, "pageNumber": 1,
                  "search": "Ginkgo"}),
    ("metabolomics_workbench", "refmet_match", {"name": "citrate"}, "GET",
     "refmet/match/citrate/name/", {}),
    ("metabolomics_workbench", "compound_by_inchikey", {"inchikey": QUERCETIN}, "GET",
     f"compound/inchi_key/{QUERCETIN}/all", {}),
    ("metabolomics_workbench", "study_summary", {"study_id": "ST000001"}, "GET",
     "study/study_id/ST000001/summary", {}),
])
def test_connectors_render_the_verified_requests(key, op, kwargs, method, path, params):
    from bioagent.providers.public_apis import render_call
    rendered = render_call(key, op, **kwargs)
    assert rendered["method"] == method and rendered["path"] == path
    assert rendered["params"] == params


def test_massbank_convert_posts_the_accession_list():
    from bioagent.providers.public_apis import render_call
    rendered = render_call("massbank", "convert", accessions=["MSBNK-A-1", "MSBNK-A-2"],
                           format="nist_msp")
    assert rendered["method"] == "POST" and rendered["path"] == "MassBank-export/convert"
    assert rendered["json_body"] == {"record_list": ["MSBNK-A-1", "MSBNK-A-2"],
                                     "format": "nist_msp"}
    assert rendered["accept"] == "text/plain"


def test_metstat_needs_every_slot_so_no_example_value_leaks_into_a_query():
    from bioagent.providers.public_apis import render_call
    with pytest.raises(ValueError, match="requires"):
        render_call("metabolomics_workbench", "metstat", species="Mouse")
    slots = dict.fromkeys(("analysis_type", "polarity", "chromatography", "species",
                           "sample_source", "disease", "kegg_id", "refmet_name"), "")
    rendered = render_call("metabolomics_workbench", "metstat",
                           **{**slots, "species": "Mouse", "refmet_name": "Tyrosine"})
    assert rendered["path"] == "metstat/;;;Mouse;;;;Tyrosine"


def test_no_spectra_connector_calls_a_bulk_dump_and_every_host_is_allowed():
    from bioagent.policy import PROFILES
    from bioagent.providers.supplement import spectra as conn
    allowed = PROFILES["biomedical-research"].allowed_hosts
    for source in conn.SOURCES:
        assert any(source.host == a or source.host.endswith("." + a) for a in allowed), \
            source.host
        for op in source.operations:
            assert "refmet/all" not in op.path and "study_id/ST/" not in op.path
            assert "ALL_GNPS" not in op.path and "gnpslibraryjson" not in op.path
