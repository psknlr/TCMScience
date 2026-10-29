"""The formula table (中医方剂数据表.xlsx): parsing, drug resolution, question parsing, and a
research run on a formula read from a table. Uses synthetic tables, never the real file."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bioagent.research import QuestionRefused, default_protocol, parse_question, run_research
from bioagent.sources import build_snapshot
from bioagent.sources.formulas import (COLUMNS, FormulaTable, TABLE_LICENSE, load_formula_table,
                                       parse_composition, records_from_rows)
from bioagent.sources.herbs import GEGEN_QINLIAN, HERBS, herb_rows
from bioagent.sources.herbs import KEY as HERB_KEY, LICENSE as HERB_LICENSE
from bioagent.sources.ledger import SnapshotLedger
from bioagent.sources.materia import MATERIA, crude_drugs, resolve_name

from test_network_pharmacology import FAST, PROTEINS, _build


def names(text):
    return [(c.name, c.dose, c.drug) for c in parse_composition(text)]


# ------------------------------------------------------------------ parsing

def test_commas_inside_a_processing_note_do_not_split_the_item():
    comps = parse_composition("附子1枚（去皮，破八片，炮），甘草2两（炙）")
    assert [(c.name, c.dose, c.drug) for c in comps] == [("附子", "1枚", "fuzi"),
                                                        ("甘草", "2两", "gancao")]
    assert "去皮，破八片，炮" in comps[0].processing and comps[1].processing == "炙"


def test_a_dose_is_read_off_the_end_without_eating_the_name():
    assert names("半夏半两，人参各等分，麝香少许，葛根半斤") == [
        ("半夏", "半两", "banxia"), ("人参", "各等分", "renshen"), ("麝香", "少许", "shexiang"),
        ("葛根", "半斤", "gegen")]


def test_a_unit_character_inside_a_name_is_not_read_as_a_dose():
    """合 is a volume unit and 百 a numeral, so 百合1两 also reads as the dose 百合1两 with no
    name. The split that leaves a known drug wins."""
    assert names("百合1两，半夏半两") == [("百合", "1两", "baihe"), ("半夏", "半两", "banxia")]


def test_a_drug_written_without_a_dose_is_not_read_as_one():
    """百合 (百 a numeral, 合 a unit) and 三七 (two numerals) read whole as doses. Written
    without a dose, as 81 百合 and 16 三七 items of the workbook are, they were parsed as the
    dose 百合 with no name and as 三 with the dose 七, and their formulas could not be
    studied. A whole item that is a known drug is that drug."""
    assert names("百合、三七、参三七，麦冬") == [("百合", "", "baihe"), ("三七", "", "sanqi"),
                                          ("参三七", "", "sanqi"), ("麦冬", "", "maidong")]
    assert names("百合（蜜炒），三七3g") == [("百合", "", "baihe"), ("三七", "3g", "sanqi")]


def test_processing_names_and_aliases_resolve_to_one_drug():
    for written, drug in [("炙甘草", "gancao"), ("粉草", "gancao"), ("桂心", "rougui"),
                          ("白茯苓", "fuling"), ("川大黄", "dahuang"), ("麸炒枳壳", "zhiqiao"),
                          ("熟地黄", "shudi"), ("生地黄", "shengdi"), ("芎", "chuanxiong")]:
        assert resolve_name(written) == drug, written


def test_an_ambiguous_or_unknown_name_stays_unresolved():
    assert resolve_name("贝母") is None            # 川贝母 or 浙贝母: not guessed
    comps = parse_composition("贝母1两，不存在的药3钱")
    assert [c.drug for c in comps] == [None, None]
    assert [c.name for c in comps] == ["贝母", "不存在的药"]


def test_a_processing_fragment_is_attached_to_the_previous_ingredient():
    comps = parse_composition("杏仁30枚，去皮尖双仁，麻黄3两")
    assert [c.name for c in comps] == ["杏仁", "麻黄"]
    assert "去皮尖双仁" in comps[0].processing


def test_a_formula_is_resolved_only_when_every_ingredient_is():
    records = records_from_rows([
        ("葛芩连汤（《某书》卷一。）", "麻黄3两，桂枝2两", "《某书》卷一。", "", "", "", ""),
        ("麻桂汤", "麻黄3两，贝母2两", "《某书》", "", "", "", ""),
    ])
    assert records[0].name == "葛芩连汤" and records[0].resolved
    assert not records[1].resolved and records[1].unresolved == ("贝母",)
    with pytest.raises(ValueError, match="unresolved"):
        records[1].version()
    version = records[0].version()
    assert version.license == TABLE_LICENSE
    assert [c[0] for c in version.components] == ["tcm:herb.mahuang", "tcm:herb.guizhi"]


def test_a_formula_id_is_a_digest_of_its_content_not_its_row():
    a = records_from_rows([("葛芩连汤", "麻黄3两", "《某书》", "", "", "", "")])[0]
    b = records_from_rows([("x", "y", "z", "", "", "", ""),
                           ("葛芩连汤", "麻黄3两", "《某书》", "", "", "", "")])[1]
    assert a.id == b.id and a.row != b.row


def test_the_workbooks_encoding_artefacts_are_read_as_the_text_they_stand_for(tmp_path):
    """The workbook writes 䓖 as U+E840, the private-use code point the GBK code page gives
    GB 18030's 0xFE7B (2,409 times, in 芎䓖), and keeps Excel's escaped carriage return
    ``_x000D_`` literally before line breaks. Both are read as the text they stand for, by
    either reader; the workbook is not edited."""
    row = ("芎\ue840散", "芎\ue8401两，川芎\ue840半两，甘草1两。", "《某书》", "", "",
           "每服2钱。_x000D_\n日3次。_x000D_", "")
    rec, = records_from_rows([row])
    assert (rec.name, rec.composition) == ("芎䓖散", "芎䓖1两，川芎䓖半两，甘草1两。")
    assert rec.usage == "每服2钱。\n日3次。"
    assert [(c.name, c.drug) for c in rec.components] == [
        ("芎䓖", "chuanxiong"), ("川芎䓖", "chuanxiong"), ("甘草", "gancao")]
    assert rec.id == records_from_rows([("芎䓖散", *row[1:])])[0].id, "the id is of the text read"
    table = load_formula_table(_xlsx(tmp_path / "t.xlsx", [row]))
    assert (table.records[0].composition, table.records[0].usage) == (rec.composition, rec.usage)


def _gb18030_of_gbk_private_use(code_point: int) -> str:
    """The character GB 18030 assigns the code that the GBK code page writes as ``code_point``:
    the code page numbers the codes of GB 18030's 0xFE50-0xFEA0 block from U+E815, in code
    order (0xFE7F is no code)."""
    trail = 0x50 + code_point - 0xE815
    return bytes([0xFE, trail + (trail >= 0x7F)]).decode("gb18030")


def test_the_other_private_use_code_points_of_the_block_are_read_as_their_characters(tmp_path):
    """U+E836 (3 cells), U+E81F (401) and U+E848 (4) are written through the same code page
    as U+E840, and were left as code points no font draws: (U+E836堇)竹沥. By the sequence
    that gives 0xFE7B, 䓖, for U+E840, they are 0xFE71 ⺮ (bamboo), 0xFE5A 㖞 and 0xFE84 ⻊
    (foot), as their contexts say: 口眼㖞斜, and the two radicals open the workbook's
    spellings of characters it cannot write, like its (犭屯): (⺮堇)竹沥, 登高（⻊俞）垣."""
    from bioagent.sources.formulas import _PRIVATE_USE
    assert _gb18030_of_gbk_private_use(0xE840) == "䓖"                     # the anchor
    assert {chr(k): v for k, v in _PRIVATE_USE.items()} == {
        chr(c): _gb18030_of_gbk_private_use(c) for c in (0xE81F, 0xE836, 0xE840, 0xE848)}
    rows = [("秫米散", "秫米、竹（\ue836条）各等分。", "《圣惠》卷九十。", "上烧灰，为细散。",
             "小儿烂疮及恶疮。", "以田中禾下水调涂之。", ""),
            ("石膏饮子", "石膏4两（捣碎），茯神1两。", "《圣惠》卷二十六。", "", "心劳实。",
             "去滓，加(\ue836堇)竹沥半合，更煎1沸。", ""),
            ("虎睛汤", "虎睛1对，茯苓1两。", "《某书》", "", "登高（\ue848俞）垣，中风口眼\ue81f斜。",
             "", "")]
    shumi, shigao, hujing = records_from_rows(rows)
    assert shumi.composition == "秫米、竹（⺮条）各等分。"
    assert shigao.usage == "去滓，加(⺮堇)竹沥半合，更煎1沸。"
    assert hujing.actions == "登高（⻊俞）垣，中风口眼㖞斜。"
    assert shumi.id == records_from_rows([(rows[0][0], "秫米、竹（⺮条）各等分。", *rows[0][2:])]
                                         )[0].id, "the id is of the text read"
    table = load_formula_table(_xlsx(tmp_path / "t.xlsx", rows))
    assert [(r.composition, r.actions, r.usage) for r in table.records] == [
        (r.composition, r.actions, r.usage) for r in (shumi, shigao, hujing)]
    from bioagent.sources.formulas import CELL_READING
    assert all(f"U+{c:04X}" in CELL_READING for c in (0xE81F, 0xE836, 0xE840, 0xE848))


def _xlsx(path: Path, rows) -> Path:
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(list(COLUMNS))
    for r in rows:
        ws.append(list(r))
    wb.save(path)
    return path


def test_the_table_is_read_and_its_columns_are_checked(tmp_path):
    good = _xlsx(tmp_path / "t.xlsx", [("葛芩连汤", "麻黄3两", "《某书》", "", "", "", "")])
    table = load_formula_table(good)
    assert len(table) == 1 and table.stats()["resolved_formulas"] == 1
    assert table.digest.startswith("sha256:")
    import openpyxl
    wb = openpyxl.Workbook()
    wb.active.append(["name", "recipe"])
    wb.save(tmp_path / "bad.xlsx")
    with pytest.raises(ValueError, match="unexpected columns"):
        load_formula_table(tmp_path / "bad.xlsx")


# ------------------------------------------------------------------ materia

def test_every_name_belongs_to_one_drug_and_organism_drugs_have_species():
    seen = {}
    for entry in MATERIA.values():
        for n in (entry.chinese, *entry.aliases):
            assert seen.setdefault(n, entry.id) == entry.id, n
        if entry.has_organism:
            assert entry.species_names


def test_an_unreviewed_synonym_match_contributes_no_taxon(tmp_path):
    record = {"names": {
        "Ephedra sinica": {"taxid": "3389", "scientific_name": "Ephedra sinica",
                           "matched_on": "Scientific Name"},
        "Ephedra intermedia": {"taxid": "111", "scientific_name": "Something else",
                               "matched_on": "All Names", "reviewed": False},
    }}
    path = tmp_path / "taxa.json"
    path.write_text(json.dumps(record), encoding="utf-8")
    drug = crude_drugs(path)["tcm:herb.mahuang"]
    assert [s.taxid for s in drug.species] == ["3389"]


def test_the_shipped_taxa_pin_cassia_to_chinese_cassia_not_its_homonym():
    drug = crude_drugs()["tcm:herb.rougui"]
    assert [s.taxid for s in drug.species] == ["119260"]      # Cinnamomum aromaticum
    assert "Cinnamomum cassia" in {drug.species[0].name, *drug.species[0].synonyms}


def test_the_gold_herbs_keep_their_hand_checked_species():
    drugs = crude_drugs()
    for herb_id, herb in HERBS.items():
        assert drugs[herb_id] is herb


# ------------------------------------------------------------------ questions

TABLE_ROWS = [
    ("葛芩连汤（《某书》卷一。）", "葛根半斤，黄芩3两，黄连3两，甘草2两", "《某书》卷一。", "", "", "", ""),
    ("双书汤", "葛根1两，甘草1两", "《局方》卷九。", "", "", "", ""),
    ("双书汤", "葛根1两，黄连1两", "《千金》卷三。", "", "", "", ""),
    ("贝葛汤", "葛根1两，贝母1两", "《某书》", "", "", "", ""),
    ("普济方", "甘草1两", "《某书》", "", "", "", ""),
]


@pytest.fixture
def table():
    return FormulaTable(records_from_rows(TABLE_ROWS))


def test_a_table_formula_is_found_by_name(table):
    q = parse_question("葛芩连汤的实测靶点是否集中在某条通路？", table=table)
    assert q.formula_id == table.named("葛芩连汤")[0].id
    assert [c[0] for c in q.formula.components][:2] == ["tcm:herb.gegen", "tcm:herb.huangqin"]


def test_a_name_with_several_compositions_needs_its_book(table):
    with pytest.raises(QuestionRefused, match="2 different compositions") as exc:
        parse_question("双书汤作用于哪些通路？", table=table)
    assert "《局方》" in str(exc.value) and "《千金》" in str(exc.value)
    q = parse_question("双书汤《太平惠民和剂局方》作用于哪些通路？", table=table)
    assert "局方" in q.formula.source
    q = parse_question("双书汤《千金》", table=table)
    assert "千金" in q.formula.source


def test_a_book_title_is_not_read_as_a_formula_name(table):
    q = parse_question("葛芩连汤《某书》，参见《鸡峰普济方》", table=table)
    assert q.formula.chinese == "葛芩连汤"


def test_a_book_no_version_cites_is_refused_with_the_books_they_cite(table):
    # 葛芩连汤 has one version, from 《某书》: asked for the 《千金》 one, the loop does not
    # study the 《某书》 one under that name
    with pytest.raises(QuestionRefused, match="no version of 葛芩连汤 .*cites 《千金》") as exc:
        parse_question("葛芩连汤《千金》", table=table)
    assert "《某书》卷一" in str(exc.value)


def test_a_formula_with_an_unresolved_ingredient_is_refused(table):
    with pytest.raises(QuestionRefused, match="贝母"):
        parse_question("贝葛汤作用于哪些通路？", table=table)


def test_a_formula_id_in_the_question_is_used_directly(table):
    rid = table.named("葛芩连汤")[0].id
    assert parse_question(f"{rid} 的靶点", table=table).formula_id == rid
    with pytest.raises(QuestionRefused, match="no formula has id"):
        parse_question("tcm:formula.fxdoesnotexist", table=table)


def test_the_hand_checked_formula_still_takes_precedence(table):
    assert parse_question("葛根芩连汤", table=table).formula_id == GEGEN_QINLIAN.id


# ------------------------------------------------------------------ herb layer

def test_the_herb_layer_holds_many_formulas_and_minerals_have_no_species(table):
    rec = records_from_rows([("朱葛汤", "葛根1两，朱砂3分", "《某书》", "", "", "", "")])[0]
    nodes, edges = herb_rows([GEGEN_QINLIAN, rec.version()], drugs=crude_drugs())
    by_id = {n["id"]: n for n in nodes}
    assert by_id["tcm:herb.zhusha"]["raw"]["no_organism"] is True
    assert not [e for e in edges if e["subject"] == "tcm:herb.zhusha"]
    licences = {e["subject"]: e["license"] for e in edges if e["predicate"] == "contains"}
    assert licences[GEGEN_QINLIAN.id] == HERB_LICENSE and licences[rec.id] == TABLE_LICENSE
    assert len([e for e in edges if e["subject"] == "tcm:herb.gegen"]) == 1   # not duplicated


# ------------------------------------------------------------------ end to end

def _world_with(tmp_path: Path, formulas) -> SnapshotLedger:
    ledger = SnapshotLedger(tmp_path / "audit" / "snapshots.jsonl")
    _build(tmp_path / "snap", ledger=ledger, tested_only=PROTEINS[8:40])
    nodes, edges = herb_rows([GEGEN_QINLIAN, *formulas])
    raw = tmp_path / "snap" / "raw.txt"
    build_snapshot(key=HERB_KEY, version="table", nodes=nodes, edges=edges,
                   license=HERB_LICENSE, citation="fixture", raw_files={"raw.txt": raw},
                   parser="fixture", root=tmp_path / "snap", ledger=ledger)
    return ledger


def test_a_formula_read_from_a_table_runs_through_the_loop(tmp_path, table):
    rec = table.named("葛芩连汤")[0]
    ledger = _world_with(tmp_path, [rec.version()])
    q = parse_question("葛芩连汤的实测靶点是否集中在某条通路？", table=table)
    run = run_research(q, protocol=default_protocol(q, parameters=FAST),
                       snapshot_root=tmp_path / "snap", ledger_path=ledger.path,
                       state_dir=tmp_path / "state", output_dir=tmp_path / "out")
    assert run.released and run.hypotheses == ("reactome:R-HSA-A",)
    assert run.artifact.claims[0].subject == rec.id


def test_a_formula_missing_from_the_herb_layer_is_refused(tmp_path, table):
    from bioagent.research import ResearchRefused
    ledger = _world_with(tmp_path, [])
    q = parse_question("葛芩连汤", table=table)
    with pytest.raises(ResearchRefused, match="does not contain"):
        run_research(q, protocol=default_protocol(q, parameters=FAST),
                     snapshot_root=tmp_path / "snap", ledger_path=ledger.path,
                     state_dir=tmp_path / "state", output_dir=tmp_path / "out")


def test_a_mineral_component_is_named_as_a_limitation(tmp_path):
    rows = [("朱砂葛汤", "葛根半斤，黄芩3两，黄连3两，甘草2两，朱砂3分", "《某书》", "", "", "", "")]
    t = FormulaTable(records_from_rows(rows))
    ledger = _world_with(tmp_path, [t.records[0].version()])
    q = parse_question("朱砂葛汤", table=t)
    run = run_research(q, protocol=default_protocol(q, parameters=FAST),
                       snapshot_root=tmp_path / "snap", ledger_path=ledger.path,
                       state_dir=tmp_path / "state", output_dir=tmp_path / "out")
    assert any("朱砂 (mineral)" in line for line in run.artifact.limitations)


# ------------------------------------------------------------------ errata

ERRATUM_ROW = ("桂枝甘草汤（《伤寒论》。）", "桂枝4两（去皮），草2两（炙）。", "《伤寒论》。", "",
               "发汗过多，其人叉手自冒心，心下悸，欲得按者。", "", "")


def _errata(tmp_path, monkeypatch, **change):
    from bioagent.sources import formulas
    doc = json.loads(formulas.ERRATA_FILE.read_text(encoding="utf-8"))
    doc["errata"][0].update(change)
    path = tmp_path / "errata.json"
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("BIOAGENT_FORMULA_ERRATA", str(path))
    return path


def test_a_misread_composition_is_read_through_its_erratum_and_keeps_its_text():
    rec, = records_from_rows([ERRATUM_ROW])
    assert rec.erratum is not None and rec.erratum.id == "erratum.0001"
    assert rec.composition == "桂枝4两（去皮），草2两（炙）。", "the workbook is not edited"
    assert rec.id == "tcm:formula.fx3fcb0aa28cc9", "the id is the digest of the text as written"
    assert rec.resolved and [c.drug for c in rec.components] == ["guizhi", "gancao"]
    assert [c.name for c in rec.written_components] == ["桂枝", "草"]
    version = rec.version()
    assert version.written == ("桂枝", "草"), "each component keeps the name the workbook wrote"
    nodes, edges = herb_rows(version)
    assert {e["raw"]["written"] for e in edges if e["predicate"] == "contains"} == {"桂枝", "草"}
    assert "第64条" in rec.erratum.authority and rec.erratum.status == "proposed"


LIZHONG_ROW = ("理中丸", "人参3钱，干姜3钱，甘草（炙）3钱，白术3两。", "《伤寒论》。",
               "上为末，炼蜜为丸，如鸡子黄许大。", "", "以沸汤数合，和1丸，研碎，温服之，日3次，夜2次。", "")


def test_a_misread_dose_is_read_through_its_erratum_and_keeps_its_text():
    """理中丸《伤寒论》 (row 37773) writes 人参、干姜、甘草 at 3钱 against 白术 3两, a ratio
    of 1:1:1:10; §386 gives all four at 三两, as the workbook's own 理中汤《伤寒论》 does."""
    rec, = records_from_rows([LIZHONG_ROW])
    assert rec.erratum is not None and rec.erratum.id == "erratum.0002"
    assert rec.erratum.status == "proposed" and "第386条" in rec.erratum.authority
    assert rec.composition == LIZHONG_ROW[1], "the workbook is not edited"
    assert rec.id == rec.erratum.formula_id == "tcm:formula.fx7e936afe6d38"
    assert [(c.name, c.dose) for c in rec.components] == [
        ("人参", "3两"), ("干姜", "3两"), ("甘草", "3两"), ("白术", "3两")]
    assert [c.dose for c in rec.written_components] == ["3钱", "3钱", "3钱", "3两"]
    assert rec.components[2].processing == "炙"


def test_an_erratum_applies_only_to_the_text_it_names(tmp_path, monkeypatch):
    changed = (ERRATUM_ROW[0], "桂枝4两，草2两。", *ERRATUM_ROW[2:])
    rec, = records_from_rows([changed])
    assert rec.erratum is None and not rec.resolved and rec.unresolved == ("草",)
    _errata(tmp_path, monkeypatch, status="rejected", reviewed_by="a maintainer")
    rec, = records_from_rows([ERRATUM_ROW])
    assert rec.erratum is None and not rec.resolved, "a rejected erratum is not applied"
    monkeypatch.setenv("BIOAGENT_FORMULA_ERRATA", "none")
    assert records_from_rows([ERRATUM_ROW])[0].erratum is None


@pytest.mark.parametrize("change, message", [
    ({"corrected": "桂枝4两（去皮），甘草2两（炙），大枣12枚。"}, "does not add"),
    ({"formula_id": "tcm:formula.fx000000000000"}, "give tcm:formula.fx3fcb0aa28cc9"),
    ({"authority": ""}, "quotes no authority"),
    ({"status": "approved"}, "status 'approved'"),
])
def test_a_malformed_erratum_is_refused(tmp_path, monkeypatch, change, message):
    _errata(tmp_path, monkeypatch, **change)
    with pytest.raises(ValueError, match=message):
        records_from_rows([ERRATUM_ROW])


def test_a_question_reaches_the_corrected_formula_by_its_book():
    table = FormulaTable(records_from_rows([ERRATUM_ROW]))
    q = parse_question("桂枝甘草汤《伤寒论》与心下悸", table=table)
    assert q.formula_id == "tcm:formula.fx3fcb0aa28cc9"
    assert [c[0] for c in q.formula.components] == ["tcm:herb.guizhi", "tcm:herb.gancao"]
