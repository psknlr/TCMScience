"""TCM safety sources built from tiny files in their real formats: TCMToxDB, MIBiG,
gutMGene and the EMA herbal index.

The headers are copied from the files as downloaded on 2026-10-01; the rows are made up
in their shape. Nothing here touches the network.
"""

from __future__ import annotations

import io
import json
import tarfile

import pytest

from bioagent.tcmdb import TCMDataHub
from bioagent.tcmdb import hub as hubmod
from bioagent.tcmdb.spec import licence_class
from bioagent.tcmdb.store import StoreError

pytestmark = pytest.mark.unit


def _by(rows, *keys):
    if len(keys) == 1:
        return {r[keys[0]]: r for r in rows}
    return {tuple(r[k] for k in keys): r for r in rows}


def _ctx(row):
    return json.loads(row["context"]) if row["context"] else {}


@pytest.fixture
def catalogued(monkeypatch):
    """The catalogue with entries 71-75, which the integrator adds to the JSON."""
    real = hubmod.catalog()
    have = {c.no for c in real}
    extra = tuple(hubmod.SourceCard(no=n, name=f"entry {n}", modules=(), url="",
                                    access="snapshot", connector=None, dataset=None,
                                    license="", barriers="", assessment="", checked="")
                  for n in (71, 73, 74, 75) if n not in have)
    monkeypatch.setattr(hubmod, "catalog", lambda: real + extra)


# ============================================================================ TCMToxDB
_FORMULA = "Form_name,Form_name_en,Prescription,Type,Dosage,Diseases,Syndromes,Indications,Efficacy"
_HERB = ("Herb_pinyin_name,Herb_cn_name,Herb_en_name,Herb_latin_name,Properties,Meridians,"
         "UsePart,Function,Indication,Toxicity,Clinical_manifestations,Therapeutic_en_class,"
         "HERB_id,SymMap_id,TCMID_id,TCMSP_id,TCM_ID_id")
_INGREDIENT = ("Name,Alias,Formula,Simles,Weight,HERB_ID,CAS_ID,SymMap_ID,TCMID_ID,TCMSP_ID,"
               "TCM-ID_ID,PubChem_ID,DrugBank_ID")
_TARGET = ("Gene_name,Gene_alias,Chromosome,Map_location,Description,Type_of_gene,"
           "TTD_target_id,TTD_target_type,Source_ID,UniProt_ID,Sequence,Toxicity")


def _bom_csv(path, header, *lines):
    path.write_text("﻿" + "\n".join((header,) + lines) + "\n", encoding="utf-8")


@pytest.fixture
def tcmtox(tmp_path, catalogued):
    h = TCMDataHub(tmp_path / "hub")
    raw = h.raw_dir("tcmtoxdb")
    raw.mkdir(parents=True)
    _bom_csv(raw / "TCMTox_formula.csv", _FORMULA,
             '一捻金,Yi Nian Jin,"Dai Huang 100g,Chao Qian Niu Zi 200g,Zhu Sha 30g,Bing Pian",'
             'Digestant medicinal,Pulveres,Retention Of Food,,,',
             '北豆根片,Bei Dou Gen Pian,"Bei Dou Gen Ti Qu Wu 120g（ Huo Xiang 30g, Bo He 10g）,'
             'Gan Cao 5.5g",Heat-clearing medicinal,Tablet,,,,')
    _bom_csv(raw / "TCMTox_herb.csv", _HERB,
             "Zhu Sha,朱砂,Cinnabar,Cinnabaris,Cold; Sweet,Heart,mineral,,,Toxic,,"
             ",HERB005000,SMHB00500,,,",
             "Ba Dou,巴豆,Croton Fruit,Fructus Crotonis,Hot; Pungent,Stomach,seed,,,"
             "Extremely Toxic,,,HERB000129,,,,",
             "Chan Su,蟾酥,Toad Venom,Venenum Bufonis,,,,,,slightly toxic,,,HERB000700,,,,",
             "Ai Ye,艾叶,leaf of Argy Wormwood,Folium Artemisiae Argyi,Warm,Spleen,leaf,,,,,"
             ",HERB000066,SMHB00002,6218,2,3")
    _bom_csv(raw / "TCMTox_ingredient.csv", _INGREDIENT,
             "solanone,solanone,C13H22O,CC(C)C(CCC(=O)C)C=CC(=C)C,194.31 g/mol,"
             "HBIN044308;HBIN012136,1937-54-8,SMIT10486,33466,MOL009341,,6451337,")
    _bom_csv(raw / "TCMTox_target.csv", _TARGET,
             "CDKN2A,ARF; INK4A,9,9p21.3,cyclin dependent kinase inhibitor 2A,protein-coding,"
             "T16452,Research target,HBTAR000644,P42771,MEPAAG,Neurotoxicity;Reproductive toxicity",
             "RB1,OSRC,13,13q14.2,RB transcriptional corepressor 1,protein-coding,,/,,P06400,MPP,",
             "Symbol,Synonyms,chromosome,map_location,description,type_of_gene,,/,,,,")
    report = h.build("tcmtoxdb", log=lambda m: None)
    return h, report


def test_tcmtoxdb_formulas_list_their_herbs_with_the_dose(tcmtox):
    h, report = tcmtox
    assert report["tables"]["formula"] == 2 and report["unresolved"] == 0
    rows = _by(h.relations("formula_herb", sources=["tcmtoxdb"]), "subject_id", "object_id")
    first = "tcmtoxdb:formula.一捻金"
    dai = rows[(first, "tcmtoxdb:herb.Dai Huang")]
    assert dai["evidence"] == "listed" and dai["subject_name"] == "一捻金 | Yi Nian Jin"
    assert _ctx(dai) == {"dose": "100", "dose_unit": "g"}
    # a herb of the herb table is the herb table's herb, names and all
    zhu = rows[(first, "tcmtoxdb:herb.Zhu Sha")]
    assert "朱砂" in zhu["object_name"] and _ctx(zhu)["dose"] == "30"
    # an item without a dose has no dose, not a zero
    assert rows[(first, "tcmtoxdb:herb.Bing Pian")]["context"] is None
    # a bracketed sub-ingredient with its own dose is a remark, not the herb's dose
    extract = rows[("tcmtoxdb:formula.北豆根片", "tcmtoxdb:herb.Bei Dou Gen Ti Qu Wu")]
    assert _ctx(extract) == {"dose": "120", "dose_unit": "g"}
    assert extract["note"] == "Huo Xiang 30g, Bo He 10g"
    assert _ctx(rows[("tcmtoxdb:formula.北豆根片", "tcmtoxdb:herb.Gan Cao")])["dose"] == "5.5"
    assert len(rows) == 6


def test_tcmtoxdb_toxicity_grades_and_organ_toxicities(tcmtox):
    h, _ = tcmtox
    rows = h.relations("subject_toxicity", sources=["tcmtoxdb"])
    grades = {r["subject_id"]: r for r in rows if r["subject_type"] == "herb"}
    assert {k: (r["object_id"], r["object_name"]) for k, r in grades.items()} == {
        "tcmtoxdb:herb.Zhu Sha": ("tcmtoxdb:grade.toxic", "Toxic"),
        "tcmtoxdb:herb.Ba Dou": ("tcmtoxdb:grade.extremely_toxic", "Extremely toxic"),
        "tcmtoxdb:herb.Chan Su": ("tcmtoxdb:grade.slightly_toxic", "Slightly toxic")}
    # a herb without a grade has no row: ungraded is not non-toxic
    assert "tcmtoxdb:herb.Ai Ye" not in grades
    assert all(r["evidence"] == "listed" for r in grades.values())
    genes = [r for r in rows if r["subject_type"] == "target"]
    assert {(r["subject_id"], r["object_id"]) for r in genes} == {
        ("symbol:CDKN2A", "tcmtoxdb:toxicity.neurotoxicity"),
        ("symbol:CDKN2A", "tcmtoxdb:toxicity.reproductive_toxicity")}
    assert all(r["evidence"] == "aggregated" and r["note"] == "via GeneCards" for r in genes)


def test_tcmtoxdb_terms_are_unknown_so_a_commercial_query_leaves_it_out(tcmtox):
    h, _ = tcmtox
    rows = h.relations(sources=["tcmtoxdb"], limit=100)
    assert rows and all(licence_class(r["license"]) == "unknown" for r in rows)
    assert h.relations(sources=["tcmtoxdb"], commercial=True) == []
    check = h.check("tcmtoxdb")
    assert check["ok"], check["problems"]


# =============================================================================== MIBiG
def _bgc(acc, status, compounds, *, taxid=263358, organism="Verrucosispora maris AB-18-032",
         methods=("Knock-out studies",), refs=("pubmed:21656887",)):
    return {"accession": acc, "version": 5, "changelog": {"releases": []},
            "quality": "questionable", "status": status, "completeness": "complete",
            "loci": [{"accession": "JF752342.1", "location": {"from": 0, "to": 0},
                      "evidence": [{"method": m} for m in methods]}],
            "biosynthesis": {"classes": [{"class": "PKS", "subclass": "Type I"}]},
            "compounds": compounds, "taxonomy": {"name": organism, "ncbiTaxId": taxid},
            "legacy_references": list(refs)}


def _mibig_archive(path):
    records = [
        _bgc("BGC0000001", "active", [
            {"name": "abyssomicin C", "evidence": [],
             "databaseIds": ["npatlas:NPA01961", "pubchem:71455791", "chembl:CHEMBL1651097"],
             "structure": "CC1CC", "mass": 346.14, "formula": "C19H22O6",
             "bioactivities": [
                 {"name": "antibacterial", "observed": True, "references": []},
                 {"name": {"activity": "cytotoxic"}, "observed": False,
                  "references": ["doi:10.1000/x1"],
                  "assays": [{"concentration": "10.0 µg/mL", "target": "cytotoxic"}]}]},
            {"name": "atrop-abyssomicin C", "evidence": [],
             "databaseIds": ["npatlas:NPA018239", "chemspider:19955692"]},
            {"name": "abyssomicin X", "evidence": []}]),
        _bgc("BGC0000002", "retired", [{"name": "old", "evidence": [],
                                        "databaseIds": ["pubchem:1"]}]),
        _bgc("BGC0000003", "active", [{"name": "lycopene", "evidence": [],
                                       "databaseIds": ["chebi:28196"]}],
             taxid=5599, organism="Alternaria alternata",
             methods=("Homology-based prediction",), refs=("doi:10.1000/x3",)),
    ]
    with tarfile.open(path, "w:gz") as tf:
        def add(name, data):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
        for rec in records:
            add(f"mibig_json_4.0/{rec['accession']}.json", json.dumps(rec).encode())
        # a macOS resource fork, as in a repackaged archive: not a record
        add("mibig_json_4.0/._BGC0000001.json", b"\x00\x05\x16\x07Mac OS X")


@pytest.fixture
def mibig(tmp_path, catalogued):
    h = TCMDataHub(tmp_path / "hub")
    h.raw_dir("mibig").mkdir(parents=True)
    _mibig_archive(h.raw_dir("mibig") / "mibig_json_4.0.tar.gz")
    return h, h.build("mibig", log=lambda m: None)


def test_mibig_names_each_product_by_its_best_database_id(mibig):
    h, report = mibig
    assert report["tables"] == {"entry": 3, "compound": 5, "bioactivity": 2}
    rows = _by(h.relations("organism_compound", sources=["mibig"]), "object_id")
    assert set(rows) == {"pubchem:71455791", "npatlas:NPA018239",
                         "mibig:BGC0000001:abyssomicin X", "chebi:28196"}
    r = rows["pubchem:71455791"]
    assert (r["subject_id"], r["evidence"], r["reference"], r["note"]) == (
        "ncbitaxon:263358", "known", "pmid:21656887", "BGC0000001.5")
    assert _ctx(r) == {"method": "Knock-out studies", "qc": "questionable"}
    # a cluster known only by homology does not show the organism makes the compound
    assert rows["chebi:28196"]["evidence"] == "predicted"
    # a retired record gives nothing
    assert not any(x["note"].startswith("BGC0000002") for x in rows.values())


def test_mibig_bioactivities_keep_tested_and_not_observed_apart(mibig):
    h, _ = mibig
    rows = _by(h.relations("compound_assay", sources=["mibig"]), "object_id")
    pos, neg = rows["mibig:activity.antibacterial"], rows["mibig:activity.cytotoxic"]
    assert pos["subject_id"] == neg["subject_id"] == "pubchem:71455791"
    assert (pos["outcome"], pos["evidence"], pos["reference"]) == ("positive", "known", None)
    assert (neg["outcome"], neg["reference"]) == ("negative", "doi:10.1000/x1")
    assert _ctx(neg) == {"concentration": "10.0 µg/mL"}
    assert all(licence_class(r["license"]) == "open" for r in rows.values())
    check = h.check("mibig")
    assert check["ok"], check["problems"]
    assert check["relations"]["compound_assay"] == {"known/positive": 1, "known/negative": 1}


# ============================================================================ gutMGene
_MM = ("Index,PMID,Gut Microbiota,Gut Microbiota NCBI ID,Rank,Strain,Substrate,"
       "Substrate PubChem CID,Metabolite,Metabolite PubChem CID,Metabolite ChEBI,"
       "Metabolite HMDB,Metabolite KEGG,Metabolite fooDB,Metabolite Metabolights,Extra Source,"
       "Associative mode,human/mouse,Sample,Experimental method,Measurement technique,"
       "Description,Condition,DOID")
_MG = ("Index,PMID,Substrate,Substrate PubChem CID,Metabolite,Metabolite PubChem CID,"
       "Metabolite ChEBI,Metabolite HMDB,Metabolite KEGG,Metabolite fooDB,"
       "Metabolite Metabolights,Extra Source,Gene,Gene ID,Alteration,high/low-throughput,"
       "Associative mode,human/mouse,Sample,Experimental method,Measurement technique,"
       "Description,Condition,DOID")
# the real header has a corrupted byte where the others have a space
_BG = ("Index,PMID,Gut Microbiota,Gut Microbiota NCBI ID,Rank,Strain,Gene,Gene ID,Alteration,"
       "high/low-throughput,Associative mode,human/mouse,Sample,Experimental method,"
       "Measurement?technique,Description,Condition,DOID")


def _gut_files(raw):
    raw.mkdir(parents=True)
    (raw / "gutmgene_microbe_metabolite.csv").write_text("\n".join([
        _MM,
        "1,21357455,Christensenella minuta,626937,species,YIT 12065T,D-Glucose,5793,Acetate,"
        "175,CHEBI:30089,,,FDB008299,MTBLC30089,homo sapiens,causally,human,fecal,"
        "cell culture,HPLC analysis,Acid is produced from glucose.,healthy,",
        "1,21357455,Christensenella minuta,626937,species,YIT 12065T,Salicin,439503,Acetate,"
        "175,CHEBI:30089,,,FDB008299,MTBLC30089,homo sapiens,causally,human,fecal,"
        "cell culture,HPLC analysis,Acid is produced from salicin.,healthy,",
        "2,23349065,Bififidobacterium thetaiotaomicron,,species,,,,Indole-3-acetic acid,802,,,,"
        ",,,causally,human,fecal,cell culture,,,healthy,",
        "3,25947926,Eggerthella lenta,84112,species,,,,(2R)-1-(3-hydroxyphenyl)propan-2-ol,,,,"
        ",,,,causally,human,fecal,cell culture,,,healthy,",
        "4,33213950,Desulfovibrio,872,genus,,,,Leucine,6106,CHEBI:25017,,,,,,correlatively,"
        "mouse,fecal,control experiment,NMR,,PM2.5 exposure, DOID:0060180",
    ]) + "\n", encoding="utf-8")
    (raw / "gutmgene_metabolite_gene.csv").write_text("\n".join([
        _MG,
        "1,28322790,,,Acetate,175,CHEBI:30089,,,FDB008299,MTBLC30089,homo sapiens,FFAR3,2865,"
        "activation,low-throughput,causally,human,colon,cell culture,qPCR,,healthy,",
        "2,35876011,Tyrosine,6057,P-Cresol,2879,,,,,,,Fos,14281,inhibition,low-throughput,"
        "causally,mouse,brain,Supplementation of metabolite,RT-PCR,,autism spectrum disorder,"
        "DOID:0060041",
        "2,35876011,Toluene,1140,P-Cresol,2879,,,,,,,Fos,14281,inhibition,low-throughput,"
        "causally,mouse,brain,Supplementation of metabolite,RT-PCR,,autism spectrum disorder,"
        "DOID:0060041",
        "3,34242820,,,Lipopolysaccharide,,,,,,,,Nfil3,18030,activation,low-throughput,"
        "causally,mouse,ileum,cell culture,qPCR,,healthy,",
    ]) + "\n", encoding="utf-8")
    (raw / "gutmgene_microbe_gene.csv").write_bytes(("\n".join([
        _BG,
        '1,31142855,Eikenella,538,genus,,CXCL6,6372,inhibition,low-throughput,correlatively,'
        'human,rectum,cell culture,"qPCR,LC/MS analysis",β-defensin 2 was downregulated,'
        ' ulcerative colitis, DOID:8577',
        "2,30000001,Akkermansia muciniphila,239935,species,Muc(T),Tnf,21926,activation,"
        "high-throughput,causally,mouse,colon,control experiment,ELISA,,colitis,",
        "3,30000002,Akkermansia muciniphila,239935,species,,REG3B,,activation,low-throughput,"
        "causally,human,colon,control experiment,ELISA,,healthy,",
    ]) + "\n").encode("gb18030"))


@pytest.fixture
def gut(tmp_path, catalogued):
    h = TCMDataHub(tmp_path / "hub")
    _gut_files(h.raw_dir("gutmgene"))
    return h, h.build("gutmgene", log=lambda m: None)


def test_gutmgene_causal_rows_are_known_and_correlations_associated(gut):
    h, _ = gut
    rows = _by(h.relations("organism_compound", sources=["gutmgene"]), "subject_id",
               "object_id")
    assert set(rows) == {("ncbitaxon:626937", "pubchem:175"),
                         ("ncbitaxon:872", "pubchem:6106")}
    acetate = rows[("ncbitaxon:626937", "pubchem:175")]
    assert (acetate["evidence"], acetate["reference"]) == ("known", "pmid:21357455")
    # one association, repeated by the file per substrate: one row naming both
    assert acetate["note"] == ("strain: YIT 12065T; substrate: D-Glucose (pubchem:5793), "
                               "Salicin (pubchem:439503)")
    assert _ctx(acetate) == {"species": "9606", "sample": "fecal", "method": "cell culture",
                             "assay": "HPLC analysis", "condition": "healthy"}
    leucine = rows[("ncbitaxon:872", "pubchem:6106")]
    assert leucine["evidence"] == "associated"
    assert _ctx(leucine)["species"] == "10090"
    assert _ctx(leucine)["condition"] == "PM2.5 exposure | DOID:0060180"


def test_gutmgene_gene_changes_keep_the_source_word(gut):
    h, report = gut
    rows = _by(h.relations("regulation", sources=["gutmgene"]), "subject_id", "object_id")
    ffar3 = rows[("pubchem:175", "ncbigene:2865")]
    assert (ffar3["subject_type"], ffar3["object_type"], ffar3["effect"]) == (
        "compound", "gene", "activation")
    assert _ctx(ffar3)["action"] == "activation"
    fos = rows[("pubchem:2879", "ncbigene:14281")]
    assert fos["effect"] == "inhibition"
    assert fos["note"] == "substrate: Tyrosine (pubchem:6057), Toluene (pubchem:1140)"
    cxcl6 = rows[("ncbitaxon:538", "ncbigene:6372")]
    assert (cxcl6["subject_type"], cxcl6["evidence"], cxcl6["effect"]) == (
        "organism", "associated", "inhibition")
    assert _ctx(cxcl6)["condition"] == "ulcerative colitis | DOID:8577"
    assert rows[("ncbitaxon:239935", "ncbigene:21926")]["note"] == "strain: Muc(T)"
    # the GB18030 file is read as what it says, not as replacement characters
    table = h.query("gutmgene", "microbe_gene", where={"Gene": "CXCL6"})
    assert table[0]["Description"].startswith("β-defensin")
    assert "Measurement_technique" in h.tables("gutmgene")["microbe_gene"]


def test_gutmgene_rows_without_an_id_wait_in_the_queue(gut):
    h, report = gut
    reasons = sorted((q["kind"], q["reason"]) for q in h.unresolved("gutmgene"))
    assert reasons == [
        ("organism_compound", "no NCBI Taxonomy id for the microbe"),
        ("organism_compound", "no PubChem, ChEBI, HMDB or KEGG id for the metabolite"),
        ("regulation", "no NCBI Gene id for the host gene"),
        ("regulation", "no PubChem, ChEBI, HMDB or KEGG id for the metabolite")]
    assert report["unresolved"] == 4
    check = h.check("gutmgene")
    assert check["ok"], check["problems"]


def test_gutmgene_refuses_the_success_body_of_a_missing_file(tmp_path):
    h = TCMDataHub(tmp_path / "hub")
    _gut_files(h.raw_dir("gutmgene"))
    (h.raw_dir("gutmgene") / "gutmgene_microbe_gene.csv").write_text("success")
    with pytest.raises(StoreError, match="not a gutMGene table"):
        h.build("gutmgene", log=lambda m: None)


# =========================================================================== EMA herbal
def _herbal(latin, botanical, common, *, combination="No"):
    return {"latin_name": latin, "combination": combination, "english_common_name": common,
            "botanical_name": botanical, "therapeutic_area": "Sleep disorders",
            "status": "F: Assessment finalised",
            "outcome_of_european_assessment": "European Union herbal monograph",
            "additional_information": "", "date_added_to_the_inventory": "30/10/2007",
            "date_added_to_the_priority_list": "", "first_published_date": "31/12/2009",
            "last_updated_date": "03/09/2026",
            "herbal_medicine_url": "https://www.ema.europa.eu/en/medicines/herbal/"
                                   + latin.lower().replace(" ", "-")}


def _doc(i, name, kind, status="Adopted"):
    return {"id": str(i), "name": name, "type": kind, "medicine_name": "",
            "ema_product_number": "", "status": status, "consultation_date": "",
            "first_published_date": "2008-11-06T01:00:00Z",
            "last_updated_date": "2008-11-06T01:00:00Z",
            "reference_number": f"EMA/HMPC/{i}/2010",
            "document_url": f"https://www.ema.europa.eu/en/documents/{kind}/doc-{i}_en.pdf"}


@pytest.fixture
def ema(tmp_path, catalogued):
    h = TCMDataHub(tmp_path / "hub")
    raw = h.raw_dir("ema_herbal")
    raw.mkdir(parents=True)
    valerian = _herbal("Valerianae radix", "Valeriana officinalis L.", "Valerian Root")
    herbs = [valerian, valerian,               # the file repeats records
             _herbal("Valerianae aetheroleum", "Valeriana officinalisL.", "Valerian oil"),
             _herbal("Foeniculi amari fructus",
                     "Foeniculum vulgare Miller subsp. vulgare var. vulgare", "Bitter Fennel"),
             _herbal("Foeniculi dulcis fructus",
                     "Foeniculum vulgare Miller subsp. vulgare var. dulce", "Sweet Fennel"),
             _herbal("Myrtilli fructus recens", "Vaccinium myrtillus L.", "Fresh Bilberry"),
             _herbal("Myrtilli fructus siccus", "Vaccinium myrtillus L.", "Dried Bilberry"),
             _herbal("Rhodiolae roseae rhizoma et radix", "Rhodiola rosea L.", "Roseroot")]
    (raw / "ema_herbal_medicines.json").write_text(json.dumps(
        {"meta": {"total_records": 7, "timestamp": "2026-10-01T18:10:45Z"}, "data": herbs}),
        encoding="utf-8")
    docs = [
        _doc(1, "Final European Union herbal monograph on Valeriana officinalis L., radix",
             "herbal-monograph"),
        _doc(2, "Final assessment report on Valeriana officinalis L., radix and Valeriana "
                "officinalis L., aetheroleum", "herbal-report"),
        _doc(3, "Final European Union herbal monograph on Foeniculum vulgare Miller subsp. "
                "vulgare var. vulgare, fructus", "herbal-monograph"),
        _doc(4, "Draft European Union herbal monograph on Vaccinium myrtillus L., fructus "
                "recens", "herbal-monograph", "Draft: consultation closed"),
        _doc(5, "Draft assessment report on Rhodiola rosea - First version", "herbal-report",
             "Draft"),
        _doc(6, "Public statement on Xigris: Withdrawal of the marketing authorisation in "
                "the European Union", "public-statement", "unknown"),
        _doc(7, "Final list of references supporting the assessment of Valeriana officinalis "
                "L., radix", "herbal-references"),
        _doc(8, "UCB Pharma withdraws its marketing authorisation application",
             "press-release", "unknown"),
        _doc(9, "Valerian root - Summary for the public", "herbal-summary", "unknown"),
        _doc(10, "Superseded community herbal monograph on Valeriana officinalis L., radix",
             "herbal-monograph"),
    ]
    (raw / "ema_documents.json").write_text(json.dumps(
        {"meta": {"total_records": 10, "timestamp": "2026-10-01T17:45:12Z"}, "data": docs}),
        encoding="utf-8")
    return h, h.build("ema_herbal", log=lambda m: None)


def test_ema_keeps_only_herbal_documents_and_the_file_generation(ema):
    h, report = ema
    assert report["tables"] == {"herbal": 8, "document": 9}       # no press release
    rows = h.query("ema_herbal", "document", columns=["id", "meta_timestamp"], limit=100)
    assert {r["meta_timestamp"] for r in rows} == {"2026-10-01T17:45:12Z"}
    assert "8" not in {r["id"] for r in rows}


def test_ema_links_a_document_to_the_substance_its_title_names(ema):
    h, _ = ema
    rows = h.relations("subject_monograph", sources=["ema_herbal"])
    pairs = {(r["subject_id"], r["object_id"]) for r in rows}
    radix, oil = "ema:herbal.Valerianae radix", "ema:herbal.Valerianae aetheroleum"
    assert pairs == {(radix, "ema:document.1"), (radix, "ema:document.2"),
                     (oil, "ema:document.2"), (radix, "ema:document.9"),
                     (radix, "ema:document.10"),
                     ("ema:herbal.Myrtilli fructus recens", "ema:document.4"),
                     ("ema:herbal.Rhodiolae roseae rhizoma et radix", "ema:document.5")}
    one = _by(rows, "object_id")["ema:document.1"]
    assert (one["subject_type"], one["evidence"], one["reference"]) == (
        "herb", "reported",
        "https://www.ema.europa.eu/en/documents/herbal-monograph/doc-1_en.pdf")
    assert one["subject_name"] == "Valerianae radix | Valerian Root | Valeriana officinalis"
    assert one["note"] == "herbal-monograph | EMA/HMPC/1/2010"
    assert _ctx(one) == {"stage": "Adopted"}
    assert _ctx(_by(rows, "object_id")["ema:document.10"]) == {"flags": "superseded",
                                                               "stage": "Adopted"}
    assert all(licence_class(r["license"]) == "open" for r in rows)


def test_ema_does_not_guess_between_two_varieties(ema):
    h, _ = ema
    queue = h.unresolved("ema_herbal")
    assert [(q["object_name"][:40], q["reason"]) for q in queue] == [
        ("Final European Union herbal monograph on",
         "several indexed substances match the title")]
    assert queue[0]["subject_name"] == "Foeniculi amari fructus | Foeniculi dulcis fructus"
    check = h.check("ema_herbal")
    assert check["ok"], check["problems"]


# ======================================================================== registration
def test_the_domain_registers_its_datasets_kinds_and_readers():
    from bioagent.tcmdb.datasets import dataset
    from bioagent.tcmdb.relations import EXTRACTORS
    from bioagent.tcmdb.rowkit import RELATION_KINDS
    from bioagent.tcmdb.store import READERS
    assert RELATION_KINDS["subject_toxicity"] == ("subject", "toxicity")
    assert {"mibig_entry", "mibig_compound", "mibig_bioactivity", "gutmgene_csv",
            "ema_json"} <= set(READERS)
    for key, no, commercial in (("tcmtoxdb", 71, "unknown"), ("mibig", 73, "allowed"),
                                ("gutmgene", 74, "unknown"), ("ema_herbal", 75, "allowed")):
        spec = dataset(key)
        assert spec.catalog == (no,) and key in EXTRACTORS
        assert spec.commercial_use == commercial
        assert (licence_class(spec.license) == "open") == (commercial == "allowed")
        assert all(f.optional for f in spec.files if f.fmt == "raw")


def test_a_generated_file_answering_head_with_length_zero_is_sized_by_a_ranged_get(
        tmp_path, monkeypatch):
    """EMA's report files answer HEAD with Content-Length 0; that is not their size."""
    from bioagent.acquisition import downloader

    class Answer:
        def __init__(self, headers):
            self.headers = headers

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_urlopen(req, timeout=None):
        if req.get_method() == "HEAD":
            return Answer({"Content-Length": "0"})
        assert req.get_header("Range") == "bytes=0-0"
        return Answer({"Content-Range": "bytes 0-0/37223213", "Content-Length": "1"})

    monkeypatch.setattr(downloader.urllib.request, "urlopen", fake_urlopen)
    dl = downloader.Downloader(tmp_path)
    assert dl._remote_size("https://www.ema.europa.eu/en/documents/report/x.json") == 37223213
