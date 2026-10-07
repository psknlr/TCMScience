"""A near name is not the same drug, in running text as in a lookup.

The October 2026 audit stopped the resolver folding 白附子 into 附子 (AUD-02). A claim could
still be written about one and rest on a study of the other: nothing compared the drug a
claim's text names with the drug its evidence studied. ``materia.names_in`` reads names
out of text, ``shared_name`` says when two are near, and ``check_claim`` refuses the
substitution as CLM014.
"""

from __future__ import annotations

import pytest

from bioagent.contracts import CandidateClaim, EvidenceItem, check_claim
from bioagent.sources.materia import IDENTITY_PREFIXES, names_in, shared_name

STUDY = ("目的：评价制附子对成人慢性心力衰竭患者运动耐量的影响。方法：随机对照试验，加用制附子"
         "1.5g/日。结论：制附子1.5g/日可增加成人慢性心力衰竭患者的6分钟步行距离。")


@pytest.mark.parametrize("text, expected", [
    ("制附子1.5g/日", [("制附子", "fuzi")]),
    ("制白附子1.5g/日", [("制白附子", "baifuzi")]),
    ("土茯苓和茯苓", [("土茯苓", "tufuling"), ("茯苓", "fuling")]),
    ("川牛膝与牛膝", [("川牛膝", "chuanniuxi"), ("牛膝", "niuxi")]),
    ("含何首乌的制剂", [("何首乌", "heshouwu")]),
])
def test_names_are_read_out_of_running_text(text, expected):
    assert [(m.name, m.drug_id) for m in names_in(text)] == expected


@pytest.mark.parametrize("text, written, contains", [
    ("服用含白首乌的制剂", "白首乌", "heshouwu"),     # 首乌 is an alias of 何首乌
    ("水半夏3g", "水半夏", "banxia"),
])
def test_an_identity_word_before_a_known_name_is_not_that_drug(text, written, contains):
    (mention,) = names_in(text)
    assert (mention.name, mention.drug_id, mention.contains) == (written, None, contains)
    assert not mention.identified
    assert written[0] in IDENTITY_PREFIXES


def test_one_character_words_are_not_read_as_names():
    assert names_in("加姜三片，酒煎") == []


@pytest.mark.parametrize("text, expected", [
    ("减小附子剂量", [("附子", "fuzi")]),           # 减小 + 附子, not 小附子
    ("加大黄芪用量", [("黄芪", "huangqi")]),         # 加大 + 黄芪, not 大黄 + 芪
    ("方中加大黄、芒硝", [("大黄", "dahuang"), ("芒硝", "mangxiao")]),   # 加 + 大黄
    ("加大枣三枚", [("大枣", "dazao")]),
])
def test_an_identity_word_that_ends_an_everyday_word_belongs_to_it(text, expected):
    """加大 and 减小 are words. Reading their last character into the name after them
    turned 减小附子剂量 into an unidentified 小附子 and 加大黄芪 into 大黄; a correct claim
    about 附子 was refused as a near name. Where no name starts after the 大, as in
    加大黄, the 大 is still read as part of the name."""
    assert [(m.name, m.drug_id) for m in names_in(text)] == expected


@pytest.mark.parametrize("text", ["小柴胡口服液", "小柴胡滴丸", "小柴胡合剂"])
def test_a_preparation_named_after_a_herb_is_not_a_near_name(text):
    assert names_in(text) == []


@pytest.mark.parametrize("a, b, shared", [
    ("制白附子", "制附子", "附子"), ("川牛膝", "牛膝", "牛膝"), ("土茯苓", "茯苓", "茯苓"),
    ("黄芩", "黄芪", None), ("白芍", "赤芍", None), ("人参", "党参", None),
])
def test_near_names_share_a_drug_name_and_not_merely_a_character(a, b, shared):
    assert shared_name(a, b) == shared


def _claim(text):
    return CandidateClaim(id="c1", text=text, claim_kind="efficacy", subject="制附子",
                          supports=("e1",), asserted_population="成人慢性心力衰竭患者",
                          supported_population="成人慢性心力衰竭患者",
                          asserted_outcome="6分钟步行距离", supported_outcome="6分钟步行距离",
                          confidence=0.5, confidence_basis="one trial",
                          falsified_by="a larger null trial")


def _evidence():
    return {"e1": EvidenceItem(id="e1", design="randomized_trial", quote=STUDY[:60],
                               source_card_id="fixture", identifier="10.5555/near.1",
                               identifier_type="doi", subject="制附子",
                               population="成人慢性心力衰竭患者", outcome="6分钟步行距离",
                               retracted="not_retracted").located_in(STUDY)}


def test_the_claim_about_the_drug_studied_is_allowed():
    verdict = check_claim(_claim("制附子1.5g/日可增加成人慢性心力衰竭患者的6分钟步行距离。"),
                          _evidence())
    assert "CLM014" not in verdict.codes and verdict.allowed


def test_a_dose_change_written_with_an_everyday_word_is_not_a_near_name():
    verdict = check_claim(_claim("减小制附子剂量后，成人慢性心力衰竭患者的6分钟步行距离增加。"),
                          _evidence())
    assert "CLM014" not in verdict.codes


def test_a_near_name_in_the_claim_text_is_refused():
    verdict = check_claim(_claim("制白附子1.5g/日可增加成人慢性心力衰竭患者的6分钟步行距离。"),
                          _evidence())
    assert not verdict.allowed and "CLM014" in verdict.codes
    assert any("白附子" in r and "附子" in r for r in verdict.reason_text)


def test_an_unidentified_near_name_is_refused_too():
    claim = _claim("白首乌可增加成人慢性心力衰竭患者的6分钟步行距离。")
    evidence = {"e1": EvidenceItem(
        id="e1", design="randomized_trial", quote="何首乌组6分钟步行距离增加",
        source_card_id="fixture", identifier="10.5555/near.2", identifier_type="doi",
        subject="何首乌", population="成人慢性心力衰竭患者", outcome="6分钟步行距离",
        retracted="not_retracted").located_in("…何首乌组6分钟步行距离增加…")}
    verdict = check_claim(claim, evidence)
    assert "CLM014" in verdict.codes
    assert any("identifies no recorded drug" in r for r in verdict.reason_text)


def test_another_drug_that_is_not_a_near_name_is_not_this_check():
    """A claim that also names some other drug is a matter for scope, not for CLM014."""
    verdict = check_claim(
        _claim("制附子1.5g/日与黄芪合用可增加成人慢性心力衰竭患者的6分钟步行距离。"), _evidence())
    assert "CLM014" not in verdict.codes


def test_a_formula_named_after_a_herb_is_not_a_near_name_of_it():
    """小柴胡汤 is a formula. Reading it as an unidentified near name of 柴胡 would refuse a
    correct claim that mentions the formula and the herb in one sentence."""
    assert [(m.name, m.drug_id) for m in names_in("小柴胡汤中的柴胡")] == [("柴胡", "chaihu")]
    claim = CandidateClaim(id="c1", text="小柴胡汤中的柴胡可降低成人发热患者的体温。",
                           claim_kind="efficacy", subject="柴胡", supports=("e1",),
                           asserted_population="成人发热患者",
                           supported_population="成人发热患者", asserted_outcome="体温",
                           supported_outcome="体温", confidence=0.5,
                           confidence_basis="one trial", falsified_by="a null trial")
    evidence = {"e1": EvidenceItem(
        id="e1", design="randomized_trial", quote="柴胡组体温下降", source_card_id="fixture",
        identifier="10.5555/near.3", identifier_type="doi", subject="柴胡",
        population="成人发热患者", outcome="体温",
        retracted="not_retracted").located_in("…柴胡组体温下降…")}
    assert "CLM014" not in check_claim(claim, evidence).codes
