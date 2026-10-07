"""The TCM data hub: catalogue, download specs, loaders, relation extractors, queries.

Every test builds small source files in the formats the real downloads use (checked
against the files fetched on 2026-10-01) and needs no network.
"""

from __future__ import annotations

import gzip
import json
import sqlite3
import zipfile
from pathlib import Path

import pytest

from bioagent.policy import PROFILES
from bioagent.providers.public_apis import BY_KEY
from bioagent.providers.public_apis_tcm import PENDING_TCM_SOURCES, TCM_SOURCES
from bioagent.tcmdb import (ACCESS_MODES, DATASETS, EVIDENCE, HubError, TCMDataHub, catalog,
                            dataset)
from bioagent.tcmdb.store import read_table

pytestmark = pytest.mark.unit


def _allowed(host: str) -> bool:
    hosts = PROFILES["biomedical-research"].allowed_hosts
    return any(host == h or host.endswith("." + h) for h in hosts)


# ------------------------------------------------------------------------- catalogue
def test_the_catalogue_has_every_source_of_the_architecture_document_once():
    cards = catalog()
    assert [c.no for c in cards] == list(range(1, 137))
    # 1-66 the architecture document's; 67-133 the review of 2026-09-30; 134-136 the
    # integration guide's first phase (HKBU formulas, HKCMMS standards, reference DNA)
    assert {c.origin for c in cards if c.no <= 66} == {"architecture"}
    assert {c.origin for c in cards if 66 < c.no <= 133} == {"review-2026-09-30"}
    assert {c.origin for c in cards if c.no > 133} == {"integration-2026-10-04"}
    assert all(c.commercial_use in ("allowed", "forbidden", "unknown") for c in cards)
    assert all(c.access in ACCESS_MODES for c in cards)
    # 2026-10-07: NPASS's and CMAUP's terms re-read; their sites state no licence.
    for c in cards:
        assert c.assessment and c.checked in ("2026-10-01", "2026-10-02", "2026-10-04",
                                              "2026-10-07"), c.name
        if c.access in ("restricted", "unreachable"):
            assert c.barriers or c.assessment, f"{c.name}: say why it cannot be reached"


def test_a_card_that_claims_a_live_connector_names_a_registered_one():
    for c in catalog():
        if c.access.startswith("live_api"):
            assert c.connector in BY_KEY, f"{c.name}: connector {c.connector!r} not registered"
        if c.dataset and c.dataset.startswith("sources:"):
            # already acquired and parsed by the research snapshot pipeline
            from bioagent.sources.build import VERSIONS
            assert c.dataset.split(":", 1)[1] in VERSIONS
        elif c.dataset:
            assert dataset(c.dataset).key == c.dataset


def test_a_pending_connector_is_not_registered_or_claimed():
    pending = {s.key for s in PENDING_TCM_SOURCES}
    assert pending and not pending & set(BY_KEY)
    assert not any(c.connector in pending for c in catalog())


def test_every_tcm_host_is_in_the_research_profile_allowlist():
    for source in TCM_SOURCES:
        assert _allowed(source.host), source.host
    from urllib.parse import urlsplit
    for spec in DATASETS:
        for f in spec.files:
            if f.url:
                assert _allowed(urlsplit(f.url).hostname or ""), (spec.key, f.url)


def test_manual_datasets_say_how_a_person_obtains_them():
    manual = [d for d in DATASETS if d.access == "manual"]
    assert manual
    for d in manual:
        assert d.instructions and all(not f.url for f in d.files)
    for d in DATASETS:
        if d.access == "download":
            assert all(f.url.startswith(("http://", "https://")) for f in d.files), d.key


# --------------------------------------------------------------------------- loaders
def _spec(fmt, **kw):
    from bioagent.tcmdb.datasets import FileSpec
    return FileSpec("", "f", "t", fmt=fmt, **kw)


def test_a_record_split_by_a_newline_inside_a_field_is_rejoined(tmp_path):
    p = tmp_path / "t.txt"
    p.write_text("id\tnote\tn\r\nA\tline one\r\nline two\t1\r\nB\tplain\t2\r\n", encoding="utf-8")
    rows = list(read_table(p, _spec("tsv")))
    assert rows == [["id", "note", "n"], ["A", "line one\nline two", "1"], ["B", "plain", "2"]]


def test_a_headerless_file_takes_the_declared_columns(tmp_path):
    p = tmp_path / "x.inchi"
    p.write_text("10168\tInChI=1S/C15H8O6\n", encoding="utf-8")
    rows = list(read_table(p, _spec("tsv", columns=("PubChem_CID", "InChI"))))
    assert rows == [["PubChem_CID", "InChI"], ["10168", "InChI=1S/C15H8O6"]]


def test_whitespace_rows_keep_a_name_with_spaces_whole(tmp_path):
    p = tmp_path / "w.txt.gz"
    with gzip.open(p, "wt") as fh:
        fh.write("PubChem_CID IUPAC_name predicted_target_proteins\r\n"
                 "5280343 acetic acid 2720(0.89)|3039(0.62)\r\n")
    rows = list(read_table(p, _spec("ws")))
    assert rows[1] == ["5280343", "acetic acid", "2720(0.89)|3039(0.62)"]


_TTD_PREAMBLE = ("\r\nTTD - Therapeutic Targets Database\r\n-----------------------------\r\n"
                 "Abbreviations:\r\nTARGETID\tTTD Target ID\r\n"
                 "DRUGINFO\tTTD Drug ID\tDrug Name\tHighest Clinical Status\r\n"
                 "-----------------------------\r\n\r\n")


def test_ttd_tagged_files_skip_the_preamble_and_read_both_layouts(tmp_path):
    rows_file = tmp_path / "target.txt"
    rows_file.write_text(_TTD_PREAMBLE + "T47101\tTARGETID\tT47101\r\nT47101\tGENENAME\tFGFR1\r\n"
                         "T47101\tDRUGINFO\tD09HNV\tNintedanib\tApproved\r\n", encoding="utf-8")
    rows = list(read_table(rows_file, _spec("ttd")))
    assert rows[1:] == [["T47101", "TARGETID", "T47101", None, None],
                        ["T47101", "GENENAME", "FGFR1", None, None],
                        ["T47101", "DRUGINFO", "D09HNV", "Nintedanib", "Approved"]]
    blocks = tmp_path / "drug_disease.txt"
    blocks.write_text(_TTD_PREAMBLE + "TTDDRUID\tDZB84T\t\t\r\nDRUGNAME\tMaralixibat\t\t\r\n"
                      "INDICATI\tPruritus\tICD-11: EC90\tApproved\r\n", encoding="utf-8")
    rows = list(read_table(blocks, _spec("ttd")))
    assert rows[1:] == [["DZB84T", "DRUGNAME", "Maralixibat", None, None],
                        ["DZB84T", "INDICATI", "Pruritus", "ICD-11: EC90", "Approved"]]


def test_gmt_parquet_xlsx_and_json_lines_load(tmp_path):
    gmt = tmp_path / "g.gmt"
    gmt.write_text("Innate response\thttps://x/GO:0002218\tAIM2\tBTK\n", encoding="utf-8")
    assert list(read_table(gmt, _spec("gmt")))[1:] == [
        ["Innate response", "https://x/GO:0002218", "AIM2"],
        ["Innate response", "https://x/GO:0002218", "BTK"]]
    import pyarrow as pa
    import pyarrow.parquet as pq
    pq.write_table(pa.table({"question": ["何谓君药?"], "answer": ["A"]}), tmp_path / "q.parquet")
    assert list(read_table(tmp_path / "q.parquet", _spec("parquet"))) == [
        ["question", "answer"], ["何谓君药?", "A"]]
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    wb.active.append(["LATIN", "CHINESE"])
    wb.active.append(["Astragali Radix", "黄芪"])
    wb.save(tmp_path / "m.xlsx")
    assert list(read_table(tmp_path / "m.xlsx", _spec("xlsx")))[1] == ["Astragali Radix", "黄芪"]
    jl = tmp_path / "d.json"
    jl.write_text('{"query": "q1", "response": "r1"}\n{"query": "q2"}\n', encoding="utf-8")
    assert list(read_table(jl, _spec("jsonl"))) == [["query", "response"], ["q1", "r1"],
                                                    ["q2", None]]


# ------------------------------------------------------------------- fixture datasets
def _itcm(raw: Path) -> None:
    raw.mkdir(parents=True)
    files = {
        "itcm_herb_detail.txt": "NID\tCHN\tPINYIN\tLATIN\tEnglish_Name\n"
                                "1519\t黄芪\tHUANG QI\tAstragali Radix\tMilkvetch Root\n"
                                "20\t炙黄芪\tZHI HUANG QI\tAstragali Radix Praeparata\t\n",
        "itcm_ingredient_detail.txt": "NID\tname\tPUBCHEM_CID\n1\tquercetin\t5280343\n"
                                      "2\tastragaloside\t\n",
        "itcm_formula_detail.txt": "NID\tCHN\tPY\n7\t补中益气汤\tBU ZHONG YI QI TANG\n",
        "itcm_target_detail.txt": "NID\tgene_symbol\tGene_name\n10\tTP53\ttumor protein p53\n",
        "itcm_disease_detail.txt": "NID\tdiseaseId\tdiseaseName\n3\tC0011849\tDiabetes Mellitus\n",
        "itcm_herb2ingredient.txt": "ingNID\therbNID\n1\t1519\n2\t1519\n1\t20\n",
        "itcm_ingredient2target.txt": "tarNID\tingNID\n10\t1\n",
        "itcm_formula2herb.txt": "herbNID\tforNID\n1519\t7\n",
        "itcm_target2disease.txt": "disNID\ttarNID\n3\t10\n",
    }
    for name, text in files.items():
        (raw / name).write_text(text, encoding="utf-8")


def _batman2(raw: Path) -> None:
    raw.mkdir(parents=True)
    def gz(name, text):
        with gzip.open(raw / name, "wt", encoding="utf-8") as fh:
            fh.write(text)
    gz("known_browse_by_ingredients.txt.gz",
       "PubChem_CID\tIUPAC_name\tknown_target_proteins\n5280343\tquercetin\tCYP2C9|FOS\n")
    gz("predicted_browse_by_ingredients.txt.gz",
       "PubChem_CID IUPAC_name predicted_target_proteins\r\n5280343 quercetin 7157(0.89)|1(0.62)\r\n")
    gz("known_browse_by_targets.txt.gz", "entrez_gene_id\tentrez_gene_symbol\tPubChem_CIDs\n")
    gz("predicted__browse_by_targets.txt.gz",
       "entrez_gene_id\tentrez_gene_symbol\tPubChem_CIDs\n")
    (raw / "herb_browse.txt").write_text(
        "Pinyin.Name\tChinese.Name\tEnglish.Name\tLatin.Name\tIngredients\r\n"
        "HUANG QI\t黄芪\tMilkvetch\tAstragalus membranaceus\t"
        "quercetin(5280343)|7-hydroxy-(2S)-flavanone (isomer)(162933)\r\n", encoding="utf-8")
    (raw / "formula_browse.txt").write_text(
        "Pinyin.Name\tChinese.Name\tPinyin.composition\n"
        "BU ZHONG YI QI TANG\t补中益气汤\tHUANG QI,REN SHEN\n", encoding="utf-8")


def _build(hub: TCMDataHub, key: str, writer) -> dict:
    writer(hub.raw_dir(key))
    return hub.build(key, log=lambda m: None)


@pytest.fixture
def hub(tmp_path) -> TCMDataHub:
    h = TCMDataHub(tmp_path / "hub")
    _build(h, "itcm", _itcm)
    _build(h, "batman2", _batman2)
    return h


def test_a_build_records_where_every_table_came_from(hub):
    with sqlite3.connect(hub.db_path("itcm")) as conn:
        files = {r[0]: r for r in conn.execute("SELECT tbl, file, sha256, rows FROM _tcmdb_files")}
    assert files["herb2ingredient"][3] == 3
    assert files["herb"][2].startswith("sha256:")
    assert not list(hub.db_path("itcm").parent.glob("*.building"))


def test_relations_use_global_ids_and_keep_every_name(hub):
    rows = hub.herb_ingredients("黄芪", sources=["itcm"])
    assert {r["object_id"] for r in rows} == {"pubchem:5280343", "itcm:ingredient.2"}
    assert rows[0]["subject_name"].split(" | ")[:2] == ["黄芪", "HUANG QI"]
    assert all(r["evidence"] == "aggregated" for r in rows)
    assert hub.relations("ingredient_target", subject="pubchem:5280343",
                         sources=["itcm"])[0]["object_id"] == "symbol:TP53"
    assert hub.formula_herbs("补中益气汤", sources=["itcm"])[0]["object_id"] == "itcm:herb.1519"
    assert hub.target_diseases("TP53")[0]["object_id"] == "umls:C0011849"


def test_an_exact_name_does_not_match_a_longer_one_unless_asked(hub):
    exact = {r["subject_id"] for r in hub.herb_ingredients("黄芪", sources=["itcm"])}
    assert exact == {"itcm:herb.1519"}
    wide = {r["subject_id"] for r in hub.herb_ingredients("黄芪", sources=["itcm"],
                                                          contains=True)}
    assert wide == {"itcm:herb.1519", "itcm:herb.20"}
    assert hub.herb_ingredients("huang qi", sources=["itcm"])        # case-insensitive


def test_known_and_predicted_targets_stay_apart_with_the_model_score(hub):
    rows = hub.ingredient_targets("pubchem:5280343", sources=["batman2"])
    known = {r["object_id"] for r in rows if r["evidence"] == "known"}
    predicted = {r["object_id"]: r["score"] for r in rows if r["evidence"] == "predicted"}
    assert known == {"symbol:CYP2C9", "symbol:FOS"}
    assert predicted == {"ncbigene:7157": "0.89", "ncbigene:1": "0.62"}
    assert {r["evidence"] for r in hub.relations()} <= EVIDENCE


def test_an_ingredient_name_with_parentheses_keeps_its_cid(hub):
    rows = hub.herb_ingredients("HUANG QI", sources=["batman2"])
    names = {r["object_id"]: r["object_name"] for r in rows}
    assert names["pubchem:162933"] == "7-hydroxy-(2S)-flavanone (isomer)"


def test_queries_check_names_and_bind_values(hub):
    assert hub.query("itcm", "herb", where={"CHN": "黄芪"})[0]["NID"] == "1519"
    assert hub.query("itcm", "herb", where={"CHN": "x' OR '1'='1"}) == []
    assert len(hub.query("itcm", "herb", contains={"PINYIN": "huang"})) == 2
    assert hub.query("itcm", "herb", contains={"PINYIN": "%"}) == []    # wildcard escaped
    with pytest.raises(HubError, match="no column"):
        hub.query("itcm", "herb", where={"CHN; DROP TABLE herb": "x"})
    with pytest.raises(HubError, match="no table"):
        hub.query("itcm", "sqlite_master")


def test_limit_applies_per_source_so_one_source_cannot_crowd_out_another(hub):
    rows = hub.herb_ingredients("黄芪", limit=1)
    assert {r["source"] for r in rows} == {"itcm", "batman2"}


def test_status_fetch_and_build_refuse_with_the_reason(hub, tmp_path):
    status = {s["dataset"]: s for s in hub.status()}
    assert status["itcm"]["built"] and status["itcm"]["relations"]["herb_ingredient"] == 3
    assert not status["herb2"]["built"]
    with pytest.raises(HubError, match="cannot be downloaded by a program"):
        hub.fetch("tcmsp_export")
    with pytest.raises(HubError, match="no files"):
        hub.build("herb2")
    with pytest.raises(HubError, match="not built"):
        hub.query("herb2", "herb")
    with pytest.raises(HubError, match="unknown relation kind"):
        hub.relations("herb_compound")


# -------------------------------------------------------------- more source formats
def _tcmio_raw(raw: Path, *, target_id: int) -> None:
    openpyxl = pytest.importorskip("openpyxl")
    raw.mkdir(parents=True)

    def xlsx(name, *rows):
        wb = openpyxl.Workbook()
        for r in rows:
            wb.active.append(list(r))
        wb.save(raw / name)
    # target.xlsx has no id column: a target's id is its row number under the header
    xlsx("target.xlsx", ("Target_name", "Gene_name", "Organism", "Uniprot_id"),
         ("5'-nucleotidase", "NT5E", "Homo sapiens (Human)", "P21589"),
         ("Oxysterols receptor LXR-beta", "NR1H2", "Homo sapiens (Human)", "P55055"))
    xlsx("tcm.xlsx", ("id", "chinese_name", "pinyin_name", "english_name"),
         (1, "黄芪", "Huang Qi", "ASTRAGALI RADIX"))
    xlsx("ingredient.xlsx", ("id", "name", "inchikey"),
         (5, "quercetin", "REFJWTPEDVJJIY-UHFFFAOYSA-N"))
    xlsx("tcm_ingredient_relation.xlsx", ("id", "tcm_id", "ingredient_id"), (1, 1, 5))
    xlsx("ingredient_target_relation.xlsx", ("id", "target_id", "ingredient_id", "type"),
         (1, target_id, 5, "Network-based prediction"))
    xlsx("prescription_tcm_relation.xlsx", ("id", "pres_name", "tcm_name", "quantity"),
         (1, "一捻金", "黄芪", "100g"))


def test_tcmio_target_ids_are_the_target_table_row_numbers(tmp_path):
    h = TCMDataHub(tmp_path)
    _tcmio_raw(h.raw_dir("tcmio"), target_id=2)
    h.build("tcmio", log=lambda m: None)
    target = h.relations("ingredient_target")[0]
    assert target["object_id"] == "uniprot:P55055"
    assert target["object_name"].startswith("NR1H2")
    assert target["evidence"] == "predicted" and "tcmio:target.2" in target["note"]
    assert h.herb_ingredients("黄芪")[0]["object_id"] == "inchikey:REFJWTPEDVJJIY-UHFFFAOYSA-N"
    assert h.herb_formulas("黄芪")[0]["note"] == "100g"


def test_tcmio_ids_beyond_the_table_are_not_mapped(tmp_path):
    # a relation file naming target 157 against a two-row table is not the numbering
    # that was verified, so nothing is mapped
    h = TCMDataHub(tmp_path)
    _tcmio_raw(h.raw_dir("tcmio"), target_id=157)
    h.build("tcmio", log=lambda m: None)
    target = h.relations("ingredient_target")[0]
    assert target["object_id"] == "tcmio:target.157" and target["object_name"] is None


def test_ddid_keeps_herb_rows_and_one_row_per_paper(tmp_path):
    h = TCMDataHub(tmp_path)
    raw = h.raw_dir("ddid")
    raw.mkdir(parents=True)
    header = ("Drug_ID,Drug_Name,Food_Herb_ID,Food_Herb_Name,Type,Result,Effect,"
              "Potential_Target,PMID,DOI\n")
    (raw / "DDID_Interaction_Information.csv").write_text(
        header
        + 'D1,Warfarin,H9,Ginkgo,Herb,"INR raised, bleeding",Negative,CYP2C9,111,\n'
        + "D1,Warfarin,H9,Ginkgo,Herb,No change,No Effect,NA,222,\n"
        + "D2,Simvastatin,F1,Pomegranate,Food,No change,No Effect,NA,333,\n",
        encoding="utf-8-sig")
    h.build("ddid", log=lambda m: None)
    rows = h.relations("herb_drug_interaction", subject="Ginkgo")
    assert {r["reference"] for r in rows} == {"pmid:111", "pmid:222"}
    assert "Negative" in next(r["note"] for r in rows if r["reference"] == "pmid:111")
    assert not h.relations("herb_drug_interaction", subject="Pomegranate")


def test_ttd_drug_targets_carry_the_gene_and_the_status(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    h = TCMDataHub(tmp_path)
    raw = h.raw_dir("ttd")
    raw.mkdir(parents=True)
    (raw / "P1-01-TTD_target_download.txt").write_text(
        _TTD_PREAMBLE + "T47101\tGENENAME\tFGFR1\r\nT47101\tTARGNAME\tFibroblast growth "
                        "factor receptor 1\r\n", encoding="utf-8")
    (raw / "P1-02-TTD_drug_download.txt").write_text(
        _TTD_PREAMBLE + "D09HNV\tTRADNAME\tOfev\r\n", encoding="utf-8")
    wb = openpyxl.Workbook()
    wb.active.append(["TargetID", "DrugID", "Highest_status", "MOA"])
    wb.active.append(["T47101", "D09HNV", "Approved", "Inhibitor"])
    wb.save(raw / "P1-07-Drug-TargetMapping.xlsx")
    h.build("ttd", log=lambda m: None)
    row = h.relations("drug_target", object="FGFR1")[0]
    assert row["subject_id"] == "ttd:D09HNV" and row["subject_name"] == "Ofev"
    assert row["object_id"] == "symbol:FGFR1" and row["note"] == "Approved | Inhibitor"


def test_dbpth_reads_its_table_from_inside_the_zip(tmp_path):
    h = TCMDataHub(tmp_path)
    raw = h.raw_dir("dbpth")
    raw.mkdir(parents=True)
    with zipfile.ZipFile(raw / "Homo_sapiens.zip", "w") as z:
        z.writestr("Reported_ITIs_Homo_sapiens.txt",
                   "dbPTH ID\tProtein Accession\tProtein Name\tGene\tIngredient\tPubChem\r\n"
                   "PTHT0000006\tP25774\tCathepsin S\tCTSS\tAcetaldehyde\t177\r\n")
    h.build("dbpth", log=lambda m: None)
    row = h.ingredient_targets("pubchem:177")[0]
    assert row["object_id"] == "uniprot:P25774" and row["evidence"] == "known"


# ------------------------------------------------------------------------------ live
def test_a_live_call_is_governed_and_checked_before_it_is_sent(tmp_path, monkeypatch):
    h = TCMDataHub(tmp_path)
    with pytest.raises(HubError, match="no live connector"):
        h.live("batman_tcm2", "query_targets", kind="herb", items=["HUANG QI"])
    with pytest.raises(HubError, match="requires"):
        h.live("dcabm_tcm", "herb_blood")                 # the herb names are required
    calls = []

    class Runtime:
        def invoke(self, component_id, *, spec, **kwargs):
            calls.append((component_id, spec.permission_profile, kwargs))
            return "result"
    h._runtime = Runtime()
    assert h.live("dcabm_tcm", "herb_blood", names=["SANG YE"]) == "result"
    component, profile, kwargs = calls[0]
    assert component == "public.connector.dcabm_tcm" and profile == "biomedical-research"
    assert kwargs["json_body"] == {"type": "herb_blood", "filters": {"pinyinName": ["SANG YE"]}}


def test_the_cli_lists_the_catalogue(capsys):
    from bioagent.cli import main
    assert main(["tcmdb", "sources", "--json"]) == 0
    cards = json.loads(capsys.readouterr().out)
    assert len(cards) == 136 and sum(c["origin"] == "architecture" for c in cards) == 66
    assert main(["tcmdb", "sources", "--module", "M6"]) == 0
    assert "DCABM-TCM" in capsys.readouterr().out


# ------------------------------------------------- SymMap / HERB site query endpoints
class _Result:
    def __init__(self, value, status="SUCCEEDED"):
        from bioagent.status import ExecutionStatus
        self.status, self.value, self.error = ExecutionStatus(status), value, None


_SYMMAP = {
    "Mol": {"data": [
        {"MOL_id": "SMIT00013", "Molecule_name": "Quercetin", "PubChem_CID": "5280343",
         "TCMSP_id": "MOL000098", "evidence": '<div>...(<a href="x">&nbspPMID:32726039</a>'},
        {"MOL_id": "SMIT00009", "Molecule_name": "Uridine", "PubChem_CID": "6029|1177",
         "TCMSP_id": "MOL000059", "evidence": ""}]},
    "Gene": {"data": [{"Gene_symbol": "A2M", "Gene_name": "Alpha-2-macroglobulin",
                       "Value": "0.1884", "P_value": "0.028", "FDR(BH)": "0.07",
                       "Relationship": "By_ingredient"}]},
    "Syndrome": {"data": [{"Syndrome_id": "SMSY00003", "Syndrome_name": "中气下陷",
                           "Syndrome_English": "sinking of the middle qi",
                           "Type": "Summarized terms"}]},
}

_HERB = {
    "herb_ingredient": [["Ingredient id", "Ingredient name"],
                        [{"link": "/Detail/?v=HBIN041495", "title": "HBIN041495"}, "Quercetin"]],
    "herb_target": [["Target id", "Gene symbol", "Protein name", "P value", "FDR BH"],
                    [{"title": "HBTAR000113"}, "IL6", "interleukin 6", 1e-5, 0.01]],
    "drug_paper_target": [["Target id", "Gene symbol", "Protein name", "Reference"],
                          [{"title": "HBTAR001257"}, "EPO", "erythropoietin",
                           [["Reference ID", "PubMed ID", "Reference title", "Relationship",
                             "Grade", "Supporting sentences"],
                            [{"title": "HBREF1"}, {"title": "32009956"}, "t", "NA",
                             "Grade C", "s"]]]],
}


def test_site_queries_are_cached_per_entity_and_labelled_by_what_they_are(tmp_path):
    h = TCMDataHub(tmp_path)
    calls = []

    def live(connector, operation, **kw):
        calls.append((connector, operation, kw))
        if connector == "symmap":
            return _Result(_SYMMAP.get(kw["related"], {"data": []}))
        return _Result(_HERB)
    h.live = live
    h.enrich_symmap(["SMHB00187"], related=("Mol", "Gene", "Syndrome"), log=lambda m: None)
    h.enrich_herb(["HERB002560"], log=lambda m: None)
    assert len(calls) == 4
    h.enrich_symmap(["SMHB00187"], related=("Mol",), log=lambda m: None)   # cached
    assert len(calls) == 4

    rows = h.relations(sources=["symmap_api"], limit=100)
    mol = {(r["object_id"], r["evidence"], r["reference"]) for r in rows
           if r["kind"] == "herb_ingredient"}
    assert mol == {("pubchem:5280343", "mentioned", "pmid:32726039"),
                   ("pubchem:6029", "aggregated", None)}
    target = next(r for r in rows if r["kind"] == "herb_target")
    assert target["evidence"] == "predicted" and "By_ingredient" in target["note"]
    assert next(r for r in rows if r["kind"] == "herb_syndrome")["evidence"] == "listed"

    herb = h.relations(sources=["herb_api"], limit=100)
    inferred = next(r for r in herb if r["object_id"] == "symbol:IL6")
    paper = next(r for r in herb if r["object_id"] == "symbol:EPO")
    assert inferred["evidence"] == "predicted"
    assert paper["evidence"] == "reported" and paper["reference"] == "pmid:32009956"
    assert "Grade C" in paper["note"]


def test_a_failed_site_query_is_not_cached(tmp_path):
    h = TCMDataHub(tmp_path)
    h.live = lambda *a, **k: _Result(None, status="FAILED")
    out = h.enrich_herb(["HERB002560"], log=lambda m: None)
    assert out == [{"file": "HERB002560__Herb.json", "cached": False, "ok": False,
                    "error": "FAILED"}]
    assert not list(h.raw_dir("herb_api").glob("*.json"))


def test_form_posts_are_encoded_as_a_browser_sends_them():
    from bioagent.providers.public_apis import render_call
    call = render_call("symmap", "related", entity_id="SMHB00187", related="Mol", filter=0)
    assert call["method"] == "POST"
    assert call["form"] == {"rrid": "SMHB00187", "table_name": "Mol", "filter": 0}


# ----------------------------------------------------------- reconciling many sources
_COLS = ("kind", "source", "subject_type", "subject_id", "subject_name", "object_type",
         "object_id", "object_name", "evidence", "score", "reference", "note")


def _store(hub: TCMDataHub, key: str, rows, tables: dict | None = None) -> None:
    path = hub.db_path(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute(f"CREATE TABLE relations ({', '.join(c + ' TEXT' for c in _COLS)})")
    conn.executemany(f"INSERT INTO relations VALUES ({', '.join('?' * len(_COLS))})",
                     [tuple(r.get(c) for c in _COLS) for r in rows])
    for name, (cols, data) in (tables or {}).items():
        conn.execute(f"CREATE TABLE {name} ({', '.join(c + ' TEXT' for c in cols)})")
        conn.executemany(f"INSERT INTO {name} VALUES ({', '.join('?' * len(cols))})", data)
    conn.commit()
    conn.close()


def _it(source, subject, obj, evidence, *, reference=None, note=None, name="quercetin",
        obj_name=None):
    return {"kind": "ingredient_target", "source": source, "subject_type": "ingredient",
            "subject_id": subject, "subject_name": name, "object_type": "target",
            "object_id": obj, "object_name": obj_name, "evidence": evidence,
            "reference": reference, "note": note}


@pytest.fixture
def many(tmp_path) -> TCMDataHub:
    h = TCMDataHub(tmp_path)
    ik = "REFJWTPEDVJJIY-UHFFFAOYSA-N"
    # HERB maps its ingredient to the structure, and NCBI Gene ids to symbols
    _store(h, "herb2", [], {
        "ingredient": (("Ingredient_id", "PubChem_id", "InChIKey"),
                       [("HBIN041495", "5280343", ik)]),
        "target": (("Target_id", "Entrez_id", "Gene_symbol"),
                   [("HBTAR1", "7124", "TNF"), ("HBTAR2", "3569", "IL6")])})
    _store(h, "itcm", [_it("itcm", "pubchem:5280343", f"symbol:{g}", "aggregated")
                       for g in ("TNF", "IL6", "AKT1", "CAT")])
    # a copy of ITCM under another name: same pairs, more than the overlap threshold
    _store(h, "tmmc2", [_it("tmmc2", f"inchikey:{ik}", f"ensembl:ENSP{i}", "aggregated",
                            note="via STITCH", obj_name=g)
                        for i, g in enumerate(("TNF", "IL6", "AKT1", "CAT"))])
    _store(h, "batman2", [_it("batman2", "pubchem:5280343", "ncbigene:7124", "known"),
                          _it("batman2", "pubchem:5280343", "ncbigene:3569", "predicted")])
    _store(h, "dbpth", [_it("dbpth", "pubchem:5280343", "uniprot:P01375", "known",
                            obj_name="TNF")])
    _store(h, "herb_api", [_it("herb_api", "herb2:HBIN041495", "symbol:TNF", "reported",
                               reference="pmid:111"),
                           _it("herb_api", "herb2:HBIN041495", "symbol:TNF", "reported",
                               reference="pmid:222"),
                           _it("herb_api", "herb2:HBIN041495", "symbol:IL6", "aggregated",
                               note="via SymMap:SMTT1; HIT:T2")])
    return h


def _by_object(result):
    return {x["object"]: x for x in result["items"]}


def test_every_name_of_one_compound_and_one_gene_meets_in_one_assertion(many):
    out = many.consensus("ingredient_target", subject="pubchem:5280343")
    assert out["subjects"] == ["inchikey:REFJWTPEDVJJIY-UHFFFAOYSA-N"]
    tnf = _by_object(out)["symbol:TNF"]
    # ITCM pubchem+symbol, TM-MC inchikey+ensembl, BATMAN ncbigene, HERB its own id
    assert set(tnf["sources"]) == {"itcm", "tmmc2", "batman2", "herb_api"}


def test_a_name_also_reaches_the_sources_that_store_only_ids(many):
    _store(many, "batman1", [_it("batman1", "pubchem:5280343", "symbol:CAT", "known",
                                 name=None)])
    out = many.consensus("ingredient_target", subject="quercetin")
    assert "batman1" in out["sources"]
    assert "batman1" in _by_object(out)["symbol:CAT"]["sources"]


def test_evidence_kinds_are_kept_apart_and_never_summed(many):
    items = _by_object(many.consensus("ingredient_target", subject="pubchem:5280343"))
    il6 = items["symbol:IL6"]
    assert il6["evidence"] == {"aggregated": ["herb_api", "itcm", "tmmc2"],
                               "predicted": ["batman2"]}
    assert il6["support"] == "integrated"          # three integrations are not an observation
    assert items["symbol:AKT1"]["support"] == "integrated"


def test_replication_counts_independent_lineages_not_databases(many):
    tnf = _by_object(many.consensus("ingredient_target", subject="pubchem:5280343"))["symbol:TNF"]
    # two papers (HERB) and BATMAN's curated set: independent observations
    assert tnf["support"] == "independently_replicated"
    assert tnf["references"] == ["pmid:111", "pmid:222"]
    assert tnf["independent"]["observed"] == 3


def test_a_declared_any_of_lineage_does_not_count_as_several():
    from bioagent.tcmdb.consensus import independent_count, lineage_of
    batman = lineage_of({"source": "batman2", "evidence": "known"})
    assert len(batman) == 1                         # one of HIT/DrugBank/KEGG/TTD, not four
    herb_via_hit = lineage_of({"source": "herb_api", "evidence": "aggregated",
                               "note": "via HIT:T0128"})
    assert independent_count(batman | herb_via_hit) == 1   # may be the same upstream
    assert independent_count(batman | {"pmid:1"}) == 2


def test_copies_found_by_overlap_count_once(many):
    out = many.consensus("ingredient_target", subject="pubchem:5280343")
    # four objects each is below the minimum for judging overlap: no cluster yet
    assert out["redundancy"]["clusters"] == []
    from bioagent.tcmdb.consensus import redundancy
    rows = [{"source": s, "evidence": "aggregated", "_s": "c", "_o": f"g{i}"}
            for s in ("itcm", "tmmc2") for i in range(30)]
    found = redundancy(rows)
    assert found["clusters"] == [["itcm", "tmmc2"]]
    assert found["lineage_map"]["db:itcm"] == found["lineage_map"]["db:tmmc2"]


def test_a_saved_survey_supplies_the_copy_clusters(many):
    path = many.root / "consensus" / "ingredient_target.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"clusters": [["itcm", "tmmc2"]], "subjects_sampled": 300}))
    out = many.consensus("ingredient_target", subject="pubchem:5280343")
    assert out["redundancy"]["survey_clusters"] == [["itcm", "tmmc2"]]
    akt1 = _by_object(out)["symbol:AKT1"]
    assert akt1["independent"]["all"] == 1          # ITCM and its copy: one lineage


def test_silence_is_reported_separately_from_not_covering(many):
    cat = _by_object(many.consensus("ingredient_target", subject="pubchem:5280343"))["symbol:CAT"]
    assert set(cat["sources"]) == {"itcm", "tmmc2"}
    # BATMAN, dbPTH and HERB cover quercetin and do not list CAT
    assert set(cat["silent_sources"]) == {"batman2", "dbpth", "herb_api"}


def test_herbs_meet_by_drug_and_processed_forms_stay_apart():
    from bioagent.tcmdb.consensus import herb_key
    assert herb_key("黄芪 | HUANG QI", "a") == herb_key("HUANG QI", "b") == "materia:huangqi"
    assert herb_key("炙黄芪", "c") == "materia:huangqi#炙黄芪"
    assert herb_key("炙黄芪", "c", merge_processed=True) == "materia:huangqi"


def test_formula_versions_are_compared_not_pooled(tmp_path):
    h = TCMDataHub(tmp_path)

    def fh(source, fid, herbs):
        return [{"kind": "formula_herb", "source": source, "subject_type": "formula",
                 "subject_id": fid, "subject_name": "补中益气汤", "object_type": "herb",
                 "object_id": f"{source}:{x}", "object_name": x, "evidence": "listed"}
                for x in herbs]
    _store(h, "batman2", fh("batman2", "batman2:formula.1", ["黄芪", "人参", "白术"])
           + fh("batman2", "batman2:formula.2", ["黄芪", "当归"]))
    _store(h, "herb2", fh("herb2", "herb2:HBFO403", ["黄芪", "人参", "白术"]))
    out = h.compare("formula_herb", "补中益气汤")
    assert out["compared"] == "versions"
    assert out["sources"] == {"batman2:formula.1": 3, "batman2:formula.2": 2,
                              "herb2:HBFO403": 3}
    assert out["jaccard"]["batman2:formula.1|herb2:HBFO403"] == 1.0
