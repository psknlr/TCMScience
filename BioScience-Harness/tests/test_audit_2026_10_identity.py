"""TCM data identity, after the October 2026 audit (AUD-01 to AUD-05).

Each test starts from the audit's own example. The formula-table row quoted by AUD-01
(row 27855, 黄连西瓜霜眼药水) is reproduced from the table's text, not invented.
"""

from __future__ import annotations

import json
from decimal import Decimal

import pytest

from bioagent.analysis.network_pharmacology import (FormulaNotRecorded,
                                                    run_network_pharmacology)
from bioagent.analysis.skill_runner import SkillRunRefused, run_skill
from bioagent.sources import build_snapshot
from bioagent.sources.formulas import parse_composition, parse_dose, records_from_rows
from bioagent.sources.herbs import GEGEN_QINLIAN, HERBS, KEY as HERB_KEY, FormulaVersion
from bioagent.sources.herbs import LICENSE as HERB_LICENSE, herb_rows
from bioagent.sources.ledger import SnapshotLedger
from bioagent.sources.materia import MATERIA, _REVIEWED_ALIASES, crude_drugs, resolve_name
from bioagent.tools.tcm import tcm_compatibility, tcm_herb, tcm_lookup
from test_network_pharmacology import FAST, SKILL_DIR, _build, _world

pytestmark = pytest.mark.unit


def _one(text: str):
    (component,) = parse_composition(text)
    return component


# ============================================================== AUD-01: doses

@pytest.mark.parametrize("text, dose, amount", [
    ("附子1.5g", "1.5g", Decimal("1.5")),
    ("甘草0.5g", "0.5g", Decimal("0.5")),
    ("甘草（炙）1.5两", "1.5两", Decimal("1.5")),
    ("葛根半斤", "半斤", Decimal("0.5")),
    ("胆星钱半", "钱半", Decimal("1.5")),
    ("芎藭1分半", "1分半", Decimal("1.5")),
    ("麝香5厘", "5厘", Decimal("5")),
])
def test_a_dose_keeps_its_decimal_point_and_reads_as_a_number(text, dose, amount):
    """Before: the name was normalised first, which removes punctuation, so 1.5g became
    15g and 0.5g became 05g; 钱半, 1分半 and 5厘 were cut up and read as processing."""
    c = _one(text)
    assert c.dose == dose and c.parsed_dose.amount == amount


def test_the_audits_row_parses_as_written():
    """Row 27855 of the formula table (黄连西瓜霜眼药水)."""
    text = "硫酸黄连素0.5g，西瓜霜（或皮消）5g，月石（即硼砂）0.2g，硝苯汞0.002g，蒸馏水100ml"
    parts = parse_composition(text)
    assert [(c.name, c.dose) for c in parts] == [
        ("硫酸黄连素", "0.5g"), ("西瓜霜", "5g"), ("月石", "0.2g"), ("硝苯汞", "0.002g"),
        ("蒸馏水", "100ml")]
    # 蒸馏水 has a dose of its own, so it is an ingredient, not a note on 硝苯汞
    assert parts[3].processing == ""
    assert parts[0].kind == "compound" and parts[2].drug == "pengsha"


@pytest.mark.parametrize("written, amount, unit", [
    ("1.50g", Decimal("1.50"), "g"), ("0.002g", Decimal("0.002"), "g"),
    ("三两", Decimal(3), "两"), ("两钱", Decimal(2), "钱"), ("十二两", Decimal(12), "两"),
    ("一钱半", Decimal("1.5"), "钱"), ("一两半", Decimal("1.5"), "两"),
    ("各三两", Decimal(3), "两"), ("２．５两", Decimal("2.5"), "两"),
])
def test_parse_dose_reads_amount_and_unit(written, amount, unit):
    dose = parse_dose(written)
    assert (dose.amount, dose.unit) == (amount, unit)
    assert dose.each == written.startswith("各")


@pytest.mark.parametrize("written", ["05g", "0002g", "1钱2分", "各等分", "少许", "减半"])
def test_a_dose_that_does_not_round_trip_keeps_no_number(written):
    """05g is what 0.5g became with its point dropped; it must never read as 5 g."""
    assert parse_dose(written).amount is None


def test_a_formula_version_carries_the_dose_as_written():
    (record,) = records_from_rows([("某方", "附子1.5g，甘草0.5g", "某书", "", "", "", "")])
    assert [c[2] for c in record.version().components] == ["1.5g", "0.5g"]


# ======================================================== AUD-02: near names

@pytest.mark.parametrize("query, near", [
    ("白附子", "附子"), ("土茯苓", "茯苓"), ("水半夏", "半夏"), ("黄芪甲苷", "黄芪"),
    ("甘草酸", "甘草"),
])
def test_a_near_name_is_a_candidate_not_the_entity(query, near):
    """Before: a single substring candidate was returned as the entity found."""
    answer = tcm_lookup(query)
    assert answer["found"] is False and answer["entity"] is None
    assert answer["status"] == "unresolved" and answer["match"] == "partial"
    assert [c["chinese"] for c in answer["candidates"]] == [near]


def test_tcm_herb_refuses_a_near_name_and_names_the_candidates():
    with pytest.raises(ValueError, match="附子 .*partial match is not an identification"):
        tcm_herb("白附子")


def test_compatibility_is_not_borrowed_from_a_near_name():
    """白附子 with 半夏 used to report 十八反 for 附子 with 半夏."""
    answer = tcm_compatibility(["白附子", "半夏"])
    assert answer["conflicts"] == []
    assert [u["name"] for u in answer["unresolved"]] == ["白附子"]
    assert answer["compatible"] is False                  # an unresolved name is not safe
    real = tcm_compatibility(["附子", "半夏"])
    assert real["conflicts"] and "十八反" in real["conflicts"][0]["description"]


def test_exact_names_and_aliases_still_resolve():
    for query, chinese in (("附子", "附子"), ("黄耆", "黄芪"), ("国老", "甘草")):
        answer = tcm_lookup(query)
        assert answer["status"] == "resolved" and answer["entity"]["chinese"] == chinese
    assert tcm_lookup("参")["status"] == "ambiguous"       # 人参 or 丹参, still listed


@pytest.mark.parametrize("name, drug", [
    ("川牛膝", "chuanniuxi"), ("牛膝", "niuxi"), ("川木通", "chuanmutong"),
    ("川木香", "chuanmuxiang"), ("白丁香", "baidingxiang"), ("丁香", "dingxiang"),
    ("白附子", "baifuzi"), ("土茯苓", "tufuling"),
    ("大麦", None), ("大麻子", None), ("胡麻子", None), ("水半夏", None),
    ("麻黄根", "mahuanggen"), ("车前子根", None), ("桃花石", None), ("珍珠母", None),
])
def test_the_materia_table_does_not_fold_a_near_name_into_another_drug(name, drug):
    """Stripping 川, 大, 小 or 白, or reading what follows a name as processing, made
    川牛膝 牛膝 (another species), 白丁香 丁香 (it is sparrow droppings), 大麦 小麦 and
    桃花石 桃枝."""
    c = _one(name + "1两")
    assert c.drug == drug, (name, c.drug, c.processing)


def test_the_reviewed_aliases_resolve_to_existing_entries():
    for name, drug in _REVIEWED_ALIASES.items():
        assert drug in MATERIA and resolve_name(name) == drug, name
    for name, drug in (("川当归", "danggui"), ("大半夏", "banxia"), ("白云苓", "fuling"),
                       ("莲子草", "mohanlian"), ("金头蜈蚣", "wugong"), ("香附米", "xiangfu")):
        assert resolve_name(name) == drug


def test_processing_instructions_after_a_name_still_resolve():
    for text, drug, note in (("甘草去皮1两", "gancao", "去皮"), ("当归酒浸2两", "danggui", "酒浸"),
                             ("半夏汤洗七次", "banxia", "汤洗七次"),
                             ("大枣如鸡子大", "dazao", "如鸡子大")):
        c = _one(text)
        assert (c.drug, c.processing) == (drug, note), text


# ============================================================ AUD-03: 化橘红

def test_huajuhong_is_its_own_drug_and_species():
    """Before: 化橘红 was an exact alias of 陈皮 (Citrus reticulata)."""
    assert resolve_name("化橘红") == "huajuhong" and resolve_name("化州橘红") == "huajuhong"
    assert resolve_name("橘红") == "juhong" and resolve_name("陈皮") == "chenpi"
    assert not {"化橘红", "橘红"} & set(MATERIA["chenpi"].aliases)
    drugs = crude_drugs()
    hua = {s.taxid for s in drugs["tcm:herb.huajuhong"].species}
    chen = {s.taxid for s in drugs["tcm:herb.chenpi"].species}
    assert hua == {"37334"} and chen == {"85571"}         # Citrus maxima, Citrus reticulata
    assert _one("化橘红9g").drug == "huajuhong"            # row 15950, 定喘汤1号


def test_bamboo_leaves_are_not_the_grass_danzhuye():
    assert resolve_name("竹叶") == "zhuye" and resolve_name("淡竹叶") == "danzhuye"
    assert resolve_name("苦竹叶") is None
    assert {s.name for s in crude_drugs()["tcm:herb.zhuye"].species} == {
        "Phyllostachys nigra var. henonis"}


# ============================================== AUD-04: compounds and extracts

@pytest.mark.parametrize("text, kind", [
    ("黄连素3g", "compound"), ("甘草酸1g", "compound"), ("黄芪甲苷10mg", "compound"),
    ("人参皂苷10mg", "constituent_group"), ("银杏叶总黄酮0.2g", "constituent_group"),
    ("甘草浸膏5g", "extract"),
])
def test_a_constituent_is_not_its_herb(text, kind):
    """Before: 黄连素3g was 黄连 with processing 素, 人参皂苷10mg 人参 with 皂苷."""
    c = _one(text)
    assert c.drug is None and c.kind == kind and c.processing == ""


def test_an_unresolved_formula_says_what_its_ingredients_read_as():
    (record,) = records_from_rows([("某方", "黄连素3g，甘草二两", "某书", "", "", "", "")])
    assert not record.resolved
    assert record.unresolved_kinds == (("黄连素", "compound"),)


# ===================================================== AUD-05: formula binding

def _only_gegen() -> FormulaVersion:
    return FormulaVersion(GEGEN_QINLIAN.id, GEGEN_QINLIAN.chinese, GEGEN_QINLIAN.source,
                          (GEGEN_QINLIAN.components[0],))


def test_a_changed_composition_under_the_same_id_is_refused_everywhere(tmp_path):
    """The audit: the snapshot holds 葛根芩连汤; the request keeps its id with 葛根 only.
    The run went ahead (governed), its compound table still had 黄连's berberine, and its
    provenance named the 葛根-only fingerprint."""
    from dataclasses import asdict

    from bioagent.research.tools import network_pharmacology_tool

    ledger = SnapshotLedger(tmp_path / "audit" / "snapshots.jsonl")
    snaps = _build(tmp_path / "snap", ledger=ledger)
    edited = _only_gegen()
    with pytest.raises(SkillRunRefused, match="changed after the snapshot was built"):
        run_skill(skill_dir=SKILL_DIR, snapshot_root=tmp_path / "snap",
                  ledger_path=ledger.path, out_dir=tmp_path / "run", params=FAST,
                  allowed={"npass", "string", "reactome"}, formula=edited)
    assert not (tmp_path / "run" / "provenance.json").exists()
    with pytest.raises(FormulaNotRecorded):
        run_network_pharmacology(snaps, formula=edited, params=FAST)
    with pytest.raises(FormulaNotRecorded):                # the research loop's governed tool
        network_pharmacology_tool(
            str(tmp_path / "snap"), str(ledger.path),
            [[s.key, s.version, s.snapshot_id] for s in snaps],
            {"id": edited.id, "chinese": edited.chinese, "source": edited.source,
             "components": [list(c) for c in edited.components]}, asdict(FAST))


def test_a_formula_without_huanglian_does_not_analyse_huanglian(tmp_path):
    """拆方 done right: the reduced formula is its own version, built into the layer."""
    reduced = FormulaVersion("tcm:formula.gegen_qinlian_minus_huanglian", "葛根芩连汤去黄连",
                             GEGEN_QINLIAN.source,
                             tuple(c for c in GEGEN_QINLIAN.components
                                   if c[0] != "tcm:herb.huanglian"))
    raw = tmp_path / "raw.txt"
    raw.write_text("fixture", encoding="utf-8")
    (np_nodes, np_edges), (r_nodes, r_edges), _ = _world()
    nodes, edges = herb_rows([GEGEN_QINLIAN, reduced])
    common = dict(raw_files={"raw.txt": raw}, parser="fixture", root=tmp_path,
                  citation="fixture")
    snaps = [build_snapshot(key=HERB_KEY, version="gold", nodes=nodes, edges=edges,
                            license=HERB_LICENSE, **common),
             build_snapshot(key="npass", version="2.0+fx", nodes=np_nodes, edges=np_edges,
                            license="fixture", **common),
             build_snapshot(key="reactome", version="current", nodes=r_nodes,
                            edges=r_edges, license="CC0-1.0", **common)]
    full = run_network_pharmacology(snaps, formula=GEGEN_QINLIAN, params=FAST)
    less = run_network_pharmacology(snaps, formula=reduced, params=FAST)
    huanglian = {f"ncbitaxon:{s.taxid}" for s in HERBS["tcm:herb.huanglian"].species}
    berberine_like = {e["object"] for e in np_edges
                      if e["predicate"] == "contains" and e["subject"] in huanglian}
    assert berberine_like <= {c["compound"] for c in full.compounds}
    assert not berberine_like & {c["compound"] for c in less.compounds}
    assert json.loads(json.dumps(less.as_dict(), default=str))["formula"] == reduced.id
