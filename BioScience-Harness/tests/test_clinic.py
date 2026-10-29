"""TCM syndrome differentiation and prescription support (bioagent.clinic)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from bioagent.clinic import pack as P
from bioagent.clinic.differentiate import differentiate
from bioagent.clinic.findings import (TermMatcher, match_pulse, match_tongue, split_entries,
                                      tongue_features)
from bioagent.clinic.followup import assess_followup, parse_visit
from bioagent.clinic.intake import IntakeError, parse_intake, template
from bioagent.clinic.prescribe import Line, check
from bioagent.clinic.session import SignOffError, assess, sign, verify
from bioagent.clinic.validate import agreement, cohen_kappa, wilson

pytestmark = pytest.mark.unit


@pytest.fixture(scope="module")
def pack():
    return P.load_pack()


def case(present, tongue, pulse, *, age=40, sex="female", pregnant=False, **extra):
    doc = {"patient": {"id": "t", "age": age, "sex": sex, "pregnant": pregnant},
           "inquiry": {"present": list(present)}, "inspection": {"tongue": list(tongue)},
           "palpation": {"pulse": list(pulse)}, "medications": extra.pop("meds", []),
           "allergies": extra.pop("allergies", []), "conditions": extra.pop("conditions", [])}
    doc.update(extra)
    return doc


SPLEEN = (["神疲乏力", "胃口差", "大便稀", "腹胀", "面色萎黄", "少气懒言"], ["舌淡，苔白"],
          ["脉缓弱"])
STASIS = (["刺痛", "痛处固定", "夜间痛甚", "唇甲青紫"], ["舌紫暗"], ["脉涩"])
PHLEGM = (["痰多", "胸脘痞闷", "形体肥胖", "纳呆"], ["苔白腻"], ["脉滑"])


# ------------------------------------------------------------------- the pack
def test_the_pack_is_consistent_and_says_it_is_unreviewed(pack):
    assert P.check(pack) == []
    assert not pack.reviewed and "not reviewed" in pack.review_status
    on_form = {t for terms in pack.inquiry_groups.values() for t in terms}
    assert pack.finding_terms() <= on_form                 # every scored finding is asked
    rule, source = pack.incompatibility("附子", "法半夏")
    assert rule == "十八反" and "儒门事亲" in source
    assert pack.incompatibility("人参", "五灵脂")[0] == "十九畏"
    assert pack.incompatibility("人参", "甘草") == ("", "")


def test_an_inconsistent_pack_is_not_loaded(pack, tmp_path):
    data = json.loads(Path(pack.path).read_text(encoding="utf-8"))
    data["formulas"]["二陈汤"]["herbs"].append(["附子", 3])          # 十八反 inside a formula
    data["formulas"]["四君子汤"]["herbs"].append(["不存在的药", 3])
    bad = tmp_path / "pack.json"
    bad.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(P.PackError, match="十八反 pair 法半夏\\+附子|no dose limits"):
        P.load_pack(bad)
    data = json.loads(Path(pack.path).read_text(encoding="utf-8"))
    data["incompatible"].append({"rule": "十八反", "a": ["甘草"], "b": ["甘草", "甘遂"]})
    worse = tmp_path / "self.json"
    worse.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(P.PackError, match="on both sides"):
        P.load_pack(worse)


# --------------------------------------------------------------- the findings
def test_findings_match_by_synonym_containment_and_negation():
    m = TermMatcher(["神疲乏力", "畏寒", "自汗", "无汗", "喘", "头晕耳鸣"], {"怕冷": "畏寒"})
    present, absent = m.classify(split_entries("神疲乏力明显，怕冷、无自汗；无汗，耳鸣，咳喘"))
    assert absent == ["自汗"] and "无汗" in present        # 无汗 is itself a finding
    got = {t: (r.strength, r.via) for t in ("神疲乏力", "畏寒", "自汗", "无汗", "喘", "头晕耳鸣")
           for r in [m.match(t, present, absent)]}
    assert got["神疲乏力"] == (1.0, "contains") and got["畏寒"] == (1.0, "synonym")
    assert got["自汗"] == (0.0, "absent") and got["无汗"] == (1.0, "exact")
    assert got["喘"] == (1.0, "contains") and got["头晕耳鸣"] == (0.5, "partial")


def test_tongues_and_pulses_are_compared_as_features():
    assert tongue_features("舌红苔黄腻") == {"body:红", "coat:黄", "coat:腻"}
    assert tongue_features("舌淡红，苔薄白") == {"body:淡红", "coat:薄", "coat:白"}
    assert match_tongue("舌红苔黄腻", ["舌红", "苔黄腻"]).strength == 1.0    # pooled
    assert match_tongue("舌淡嫩", ["舌淡"]).strength == 0.5
    assert match_tongue("舌淡", ["淡红舌"]).strength == 0                   # a normal tongue
    assert match_tongue("舌红少苔", ["舌淡胖", "苔白滑"]).contradicted
    assert match_pulse("脉弱", ["脉沉细无力"]).strength == 1.0              # 沉细无力 is 弱
    assert match_pulse("脉弦细数", ["弦", "细数"]).strength == 1.0
    assert match_pulse("脉弦有力", ["脉弱"]).contradicted
    assert match_pulse("脉浮缓", ["脉浮紧"]).contradicted                   # 中风 vs 伤寒


# ------------------------------------------------------------ the intake
def test_the_intake_form_lists_what_is_scored(pack):
    t = template(pack)
    form = t["_form"]
    assert "十问歌" in form and form["舌象参考"] and form["脉象参考"]
    assert t["medications"] is None                       # not asked, as opposed to none
    listed = {x for terms in form["问诊要点（按十问分组，均为辨证计分所用）"].values() for x in terms}
    assert pack.finding_terms() <= listed


def test_an_intake_needs_an_age_and_says_what_is_missing(pack):
    with pytest.raises(IntakeError, match="patient.age"):
        parse_intake({"patient": {"sex": "female"}}, pack)
    with pytest.raises(IntakeError, match="male patient"):
        parse_intake({"patient": {"age": 30, "sex": "male", "pregnant": True}}, pack)
    it = parse_intake({"patient": {"age": 30, "sex": "female"},
                       "inquiry": {"present": ["神疲乏力"]}}, pack)
    joined = " ".join(it.warnings)
    for what in ("舌象未记录", "脉象未记录", "妊娠状态未记录", "目前用药未询问"):
        assert what in joined


# ---------------------------------------------------------- differentiation
def test_spleen_qi_deficiency_meets_the_criteria_and_covers_qi_deficiency(pack):
    present = SPLEEN[0] + ["自汗"]
    d = differentiate(parse_intake(case(present, SPLEEN[1], SPLEEN[2]), pack), pack)
    first = d.candidates[0]
    assert d.status == "meets" and first.syndrome == "脾胃气虚证"
    via = {h.term: h.via for h in first.hits}
    assert via["食少"] == "synonym" and via["便溏"] == "synonym"     # 胃口差, 大便稀
    general = next(c for c in d.candidates if c.syndrome == "气虚证")
    assert general.status == "meets" and general.covered_by == "脾胃气虚证"
    assert d.candidates.index(general) == 1               # right after the specific one


def test_two_syndromes_that_both_fit_are_both_reported(pack, tmp_path):
    # 气虚 and 血虚 together: the report must not pick one and hide the other
    present = ["神疲乏力", "少气懒言", "自汗", "活动后加重", "气短",
               "面色淡白", "面色萎黄", "唇甲色淡", "头晕眼花", "心悸", "失眠多梦"]
    s = assess(case(present, ["舌淡"], ["脉虚", "脉细"]), tmp_path, pack=pack)
    first, second = s.differentiation.candidates[0], s.differentiation.candidates[1]
    assert {first.syndrome, second.syndrome} == {"气虚证", "血虚证"}
    assert first.status == second.status == "meets"
    assert abs(first.score - second.score) <= 0.1
    (note,) = s.differentiation.notes
    assert "亦符合诊断标准" in note and "或为兼证" in note
    assert note in (tmp_path / "report.md").read_text(encoding="utf-8")


def test_contradicting_tongue_and_pulse_keep_a_syndrome_from_the_criteria(pack):
    doc = case(["五心烦热", "潮热", "盗汗", "口燥咽干"], ["舌淡胖", "苔白滑"], ["脉沉迟"])
    d = differentiate(parse_intake(doc, pack), pack)
    yin = next(c for c in d.candidates if c.syndrome == "阴虚证")
    assert yin.main == 4 and yin.status != "meets"
    assert len(yin.contradictions) == 2 and d.status != "meets"


def test_too_little_information_asks_instead_of_drafting(pack, tmp_path):
    s = assess(case(["神疲乏力"], [], []), tmp_path, pack=pack)
    assert s.status == "needs_information" and s.prescription is None
    asked = [q.finding for q in s.differentiation.questions]
    assert asked[:2] == ["舌象（舌质、舌苔）", "脉象"]
    assert "建议补充询问" in (tmp_path / "report.md").read_text(encoding="utf-8")


# --------------------------------------------------------------- red flags
def test_red_flags_stop_the_consultation_until_cleared(pack, tmp_path):
    doc = case(["胸部刺痛", "痛处固定", "夜间痛甚"], STASIS[1], STASIS[2])
    s = assess(doc, tmp_path / "a", pack=pack)
    assert s.status == "refer" and s.differentiation is None and s.prescription is None
    assert [f.id for f in s.red_flags] == ["chest_pain"]
    doc["red_flags_cleared"] = [{"flag": "chest_pain", "by": "心内科医师",
                                 "how": "心电图、肌钙蛋白正常"}]
    s = assess(doc, tmp_path / "b", pack=pack)
    assert s.status == "draft" and s.red_flags[0].cleared
    # 胸胁胀痛 is the hypochondrium, not chest pain
    s = assess(case(["胸胁胀痛", "情志抑郁", "善太息"], ["苔薄白"], ["脉弦"]), tmp_path / "c",
               pack=pack)
    assert not s.red_flags and s.prescription.formula == "柴胡疏肝散"


def test_vital_signs_at_news2_red_scores_refer(pack, tmp_path):
    for vitals in ({"spo2": 88}, {"systolic": 85}, {"heart_rate": 135}, {"resp_rate": 26}):
        s = assess(case(*SPLEEN, vitals=vitals), tmp_path / str(len(str(vitals))), pack=pack)
        assert s.status == "refer", vitals
    s = assess(case(*SPLEEN, vitals={"spo2": 97, "heart_rate": 80, "systolic": 118}),
               tmp_path / "ok", pack=pack)
    assert s.status == "draft"
    md = (tmp_path / "ok" / "report.md").read_text(encoding="utf-8")
    assert "血氧 97 %" in md and "血压 118/— mmHg" in md and "nan" not in md


def test_an_emergency_syndrome_is_referred_not_prescribed(pack, tmp_path):
    doc = case(["四肢厥逆", "恶寒蜷卧", "神衰欲寐", "下利清谷"], ["舌淡苔白滑"], ["脉微细"])
    s = assess(doc, tmp_path, pack=pack)
    assert s.status == "refer" and not s.prescription.lines
    assert s.prescription.issues[0].code == "emergency"


# ------------------------------------------------------------ prescriptions
def test_textbook_doses_above_the_pharmacopoeia_are_drafted_at_its_limit(pack, tmp_path):
    s = assess(case(["腰膝酸软", "头晕耳鸣", "五心烦热", "盗汗", "失眠多梦"], ["舌红少苔"],
                    ["脉细数"]), tmp_path / "yin", pack=pack)
    dose = {ln.herb: ln for ln in s.prescription.lines}
    assert s.prescription.formula == "六味地黄丸"
    assert dose["熟地黄"].textbook_g == 24 and dose["熟地黄"].dose_g == 15
    assert "dose_capped:熟地黄" in {i.id for i in s.prescription.issues}
    s = assess(case(*PHLEGM), tmp_path / "tan", pack=pack)
    dose = {ln.herb: ln.dose_g for ln in s.prescription.lines}
    assert s.prescription.formula == "二陈汤" and dose["法半夏"] == 9 and dose["陈皮"] == 10
    assert "toxic:法半夏" in {i.id for i in s.prescription.issues}


def test_childrens_doses_and_ranges_are_scaled(pack, tmp_path):
    s = assess(case(["恶风", "汗出", "发热", "头痛"], ["苔薄白"], ["脉浮缓"], age=5, sex="male",
                    pregnant=None), tmp_path, pack=pack)
    rx = s.prescription
    assert rx.formula == "桂枝汤" and rx.fraction == 0.5
    gui = next(ln for ln in rx.lines if ln.herb == "桂枝")
    assert gui.dose_g == 4.5 and gui.range_g == (1.5, 5.0)


def test_who_the_pregnancy_question_is_asked_of(pack, tmp_path):
    def ids(age, sex, pregnant):
        s = assess(case(*STASIS, age=age, sex=sex, pregnant=pregnant), tmp_path / f"{age}{sex}",
                   pack=pack)
        return {i.id for i in s.prescription.issues}

    assert not any(i.startswith("pregnancy") for i in ids(60, "female", None))   # past 55
    assert not any(i.startswith("pregnancy") for i in ids(40, "male", None))
    assert not any(i.startswith("pregnancy") for i in ids(8, "female", None))    # under 12
    assert "pregnancy_unknown:桃仁" in ids(12, "female", None)
    assert "infant" in ids(0.5, "female", False)
    assert "elderly" in ids(70, "female", False)


def test_pregnancy_blocks_cautioned_herbs_and_stops_contraindicated_ones(pack, tmp_path):
    s = assess(case(*STASIS, pregnant=True), tmp_path / "a", pack=pack)
    blocks = {i.id for i in s.prescription.issues if i.severity == "block"}
    assert {"pregnancy:桃仁", "pregnancy:红花", "pregnancy:牛膝", "pregnancy:枳壳"} <= blocks
    assert s.status == "draft"
    # 附子: the Pharmacopoeia says 慎用, the seed knowledge base 禁用; the stricter applies
    s = assess(case(["腰膝酸冷", "畏寒", "肢冷", "夜尿多", "小便清长"], ["舌淡胖", "苔白"],
                    ["脉沉细无力"], pregnant=True), tmp_path / "b", pack=pack)
    stop = next(i for i in s.prescription.issues if i.id == "pregnancy:附子")
    assert stop.severity == "stop" and "来源不一" in stop.message and s.status == "blocked"
    # unknown status in a woman of child-bearing age: ask first
    s = assess(case(*STASIS, pregnant=None), tmp_path / "c", pack=pack)
    assert "pregnancy_unknown:桃仁" in {i.id for i in s.prescription.issues}


def test_incompatible_additions_allergies_and_interactions_are_caught(pack, tmp_path):
    s = assess(case(*PHLEGM), tmp_path / "a", pack=pack,
               modifications=[{"add": "附子", "dose": 6}])
    pair = [i for i in s.prescription.issues if i.code == "incompatible"]
    assert len(pair) == 1 and pair[0].severity == "block" and "十八反" in pair[0].message
    s = assess(case(*SPLEEN, allergies=["人参"]), tmp_path / "b", pack=pack)
    assert s.status == "blocked" and "allergy:人参" in {i.id for i in s.prescription.issues}
    s = assess(case(*SPLEEN, meds=["华法林 3mg qd"], conditions=["高血压"]), tmp_path / "c",
               pack=pack)
    ids = {i.id for i in s.prescription.issues}
    assert "interaction:人参:华法林" in ids and "condition:人参:高血压" in ids


def test_textbook_modifications_are_suggested_and_applied_only_when_asked(pack, tmp_path):
    present = SPLEEN[0] + ["恶心"]
    s = assess(case(present, *SPLEEN[1:]), tmp_path / "a", pack=pack)
    assert any(m == {"add": "法半夏"} for sug in s.prescription.suggested
               for m in sug["modifications"])
    assert "法半夏" not in {ln.herb for ln in s.prescription.lines}
    s = assess(case(present, *SPLEEN[1:]), tmp_path / "b", pack=pack, apply_textbook=True)
    added = next(ln for ln in s.prescription.lines if ln.herb == "法半夏")
    assert added.dose_g == 3 and added.basis.startswith("added at the Pharmacopoeia lower")


def test_two_textbook_modifications_that_would_clash_are_not_both_applied(pack, tmp_path):
    # 四君子汤: 附子 for 畏寒肢冷 and 法半夏 for 恶心 are 十八反 together, though neither
    # clashes with the base formula on its own.
    present = SPLEEN[0] + ["恶心", "畏寒", "肢冷"]
    s = assess(case(present, *SPLEEN[1:]), tmp_path, pack=pack, apply_textbook=True)
    herbs = {ln.herb for ln in s.prescription.lines}
    assert ("附子" in herbs) != ("法半夏" in herbs)            # one of them, never both
    skipped = [i for i in s.prescription.issues if i.code == "modification_skipped"]
    assert len(skipped) == 1 and "十八反" in skipped[0].message
    assert "加干姜、附子" in skipped[0].message          # the whole modification, not one herb
    assert "干姜" not in herbs
    assert not [i for i in s.prescription.issues if i.code == "incompatible"]
    assert "**未应用：" in (tmp_path / "report.md").read_text(encoding="utf-8")


def test_check_reviews_a_practitioners_own_prescription(pack):
    it = parse_intake(case(*PHLEGM, pregnant=True), pack)
    lines = [Line("甘草", 6, "practitioner"), Line("甘遂", 3, "practitioner"),
             Line("不在知识包的药", 9, "practitioner")]
    found = {i.id: i.severity for i in check(lines, it, pack)}
    assert found["incompatible:甘草+甘遂"] == "block"
    assert found["pregnancy:甘遂"] == "stop"
    assert found["dose_above_range:甘遂"] == "block"          # toxic and above 0.5-1.5 g
    assert found["unknown_herb:不在知识包的药"] == "block"


# ------------------------------------------------------------------ allergies
_ALLERGY_CODES = ("allergy", "allergy_source", "allergy_near_name", "allergy_formula",
                  "allergy_homonym", "allergy_denied", "allergy_taken")


def _allergy_issues(pack, allergy, herb):
    it = parse_intake(case(*SPLEEN, allergies=[allergy]), pack)
    return [i for i in check([Line(herb, 9, "practitioner")], it, pack)
            if i.code in _ALLERGY_CODES]


def _found(pack, allergy, herb):
    return [(i.severity, i.code) for i in _allergy_issues(pack, allergy, herb)]


def _all_herbs_issues(pack, record):
    it = parse_intake(case(*SPLEEN, allergies=[record]), pack)
    lines = [Line(h, 9, "practitioner") for h in pack.herbs]
    return [i for i in check(lines, it, pack) if i.code in _ALLERGY_CODES]


@pytest.mark.parametrize("allergy,herb", [("黄耆", "黄芪"), ("北芪", "黄芪"), ("绵芪", "黄芪"),
                                          ("炙黄芪", "黄芪"), ("芍药", "白芍"), ("国老", "甘草"),
                                          ("国老", "炙甘草"), ("黄芪", "黄芪"),
                                          ("黄耆过敏", "黄芪"), ("对北芪过敏", "黄芪"),
                                          # a processing word before a knowledge-base name
                                          ("黄耆", "炙黄芪"), ("炙黄耆", "黄芪"),
                                          ("芍药", "炒白芍")])
def test_an_allergy_written_under_another_name_of_the_herb_stops_it(pack, allergy, herb):
    # 黄耆 is the classics' name of 黄芪 (Taiwan writes it 黃耆, which is folded to 黄耆), 北芪
    # a trade name: the same herb
    it = parse_intake(case(*SPLEEN, allergies=[allergy]), pack)
    found = {i.id: i for i in check([Line(herb, 9, "practitioner")], it, pack)}
    assert found[f"allergy:{herb}"].severity == "stop", sorted(found)
    assert allergy in found[f"allergy:{herb}"].message
    assert "过敏与" not in found[f"allergy:{herb}"].message    # 黄耆与黄芪, not 黄耆过敏与黄芪


def test_an_allergy_to_another_herb_is_not_read_as_this_one(pack):
    # 党参 is not 人参, and a name that only shares a character is nothing
    for allergy, herb in (("党参", "人参"), ("青霉素", "黄芪"), ("黄芩", "黄芪"), ("西洋参", "人参")):
        assert not _allergy_issues(pack, allergy, herb), (allergy, herb)


@pytest.mark.parametrize("allergy", ["沙姜", "洋姜", "姜汤", "姜某", "面部皮疹", "方便面", "枣红色", "已归档",
                                     "广西桂林", "姜黄"])
def test_a_one_character_name_inside_another_word_is_no_name(pack, allergy):
    # 姜, 枣, 归, 桂 and 面 are names of the materia table, and words far more often than
    # names: read inside 沙姜 (Kaempferia), 姜某 (a surname), 面部 (the face), 归档 (filing) or
    # 桂林 (a city) they blocked common records. A name of one character counts only standing
    # alone or between the words of an allergy record (C3)
    for herb in ("生姜", "干姜", "大枣", "当归", "桂枝", "小麦"):
        assert not _allergy_issues(pack, allergy, herb), (allergy, herb)


@pytest.mark.parametrize("record", ["对姜过敏", "姜过敏史", "、姜、", "姜", "过敏史：姜", "服用姜后出现皮疹",
                                    "患者自诉姜过敏", "3年前姜过敏", "青霉素及姜均过敏", "对姜有过敏史", "姜\n半夏"])
def test_a_one_character_name_standing_alone_is_read(pack, record):
    assert _found(pack, record, "生姜") == [("stop", "allergy")], record
    # the table files 枣 with 大枣, a source-level link: a block that says so
    found = _allergy_issues(pack, record.replace("姜", "枣"), "大枣")
    assert [(i.severity, i.code) for i in found] == [("block", "allergy_source")], record
    assert "同列于" in found[0].message, found[0].message


@pytest.mark.parametrize("allergy,herb", [("鸡蛋", "鸡内金"), ("鸡子", "鸡内金"), ("乌鸡", "鸡内金"),
                                          ("海带", "海藻"), ("昆布", "海藻"), ("山芋", "山药"),
                                          ("萝卜", "莱菔子"), ("杏子", "苦杏仁"), ("薯蓣", "山药"),
                                          ("麦门冬", "麦冬"), ("双花", "金银花"), ("茯苓", "茯神"),
                                          # a processing word does not make 鸡蛋 the herb
                                          ("生鸡蛋", "鸡内金")])
def test_a_name_the_materia_table_files_with_the_herb_needs_a_reason_and_claims_no_species(
        pack, allergy, herb):
    # the materia table groups names by source, for network pharmacology: its 鸡内金 lists
    # 鸡蛋 and 乌鸡 (Gallus gallus), its 海藻 海带 and 昆布, its 山药 山芋 (in many places
    # today a sweet potato) and 薯蓣, its 莱菔子 萝卜. Read as the herb, an egg allergy was
    # an unsignable stop for 鸡内金 that said the two were one drug; ignored, an allergy
    # recorded as 薯蓣 passed 山药 in silence. It says where the table files the two and asks
    # which was meant, and the answer is recorded: a block, signed only with the reason. It
    # does not say they are one species: the table files 海带 (Saccharina) under 海藻
    # (Sargassum), and holds no species of its own for a name it files.
    found = _allergy_issues(pack, allergy, herb)
    assert [(i.severity, i.code) for i in found] == [("block", "allergy_source")], found
    msg = found[0].message
    assert "同列于" in msg and "未必同一物种" in msg and "请核实" in msg and "药材名表" in msg, msg
    assert "为同一药材" not in msg and "同一物种/来源" not in msg and "同源" not in msg, msg


def test_a_name_filed_with_another_species_says_only_where_the_table_files_it(pack):
    # S4: 海带 is Saccharina japonica, 海藻 Sargassum; the old message called them 同源（同一
    # 物种/来源）
    found = _allergy_issues(pack, "海带过敏", "海藻")
    assert [(i.severity, i.code) for i in found] == [("block", "allergy_source")], found
    assert "药材名表将海带与海藻同列于“海藻”条下（未必同一物种），请核实" in found[0].message


def test_an_allergy_to_a_drug_of_the_same_species_needs_a_reason(pack):
    # 芍药 is 白芍 by the knowledge base, a stop; 赤芍 is the root of the same Paeonia
    # lactiflora, which passed with no issue at all
    stop = _allergy_issues(pack, "芍药", "白芍")
    assert [(i.severity, i.code) for i in stop] == [("stop", "allergy")]
    for allergy, herb, species in (("芍药", "赤芍", "Paeonia lactiflora"),
                                   ("赤芍", "白芍", "Paeonia lactiflora"),
                                   ("生姜", "干姜", "Zingiber officinale"),
                                   ("干生姜", "生姜", "Zingiber officinale"),
                                   # the table files 炮姜 under 干姜, so it is not 姜 processed
                                   ("炮姜", "生姜", "Zingiber officinale"),
                                   ("麻黄根", "麻黄", "Ephedra sinica"),
                                   ("紫苏", "紫苏子", "Perilla frutescens"),
                                   ("天花粉", "瓜蒌", "Trichosanthes kirilowii")):
        found = _allergy_issues(pack, allergy, herb)
        assert [(i.severity, i.code) for i in found] == [("block", "allergy_source")], found
        assert "同源（同一物种：" in found[0].message and species in found[0].message
        assert "为同一药材" not in found[0].message


@pytest.mark.parametrize("allergy,herb", [("生地黄", "熟地黄"), ("熟地黄", "生地黄"),
                                          ("鲜地黄", "熟地黄"), ("干地黄", "熟地黄"), ("熟地", "生地黄"),
                                          ("地黄过敏", "熟地黄"), ("对熟地过敏", "生地黄")])
def test_the_processed_forms_of_one_drug_stop_each_other(pack, allergy, herb):
    # 地黄 stopped 熟地黄 (熟 + 地黄), but 生地黄, which the materia table files with 地黄,
    # only warned that the two share a species: two processing words before one base were
    # not seen. 熟地 is 熟地黄's short name by the knowledge base, so it reaches 生地黄 too.
    found = _allergy_issues(pack, allergy, herb)
    assert [(i.severity, i.code) for i in found] == [("stop", "allergy")], found
    assert "地黄" in found[0].message and "未必" not in found[0].message


@pytest.mark.parametrize("allergy,herb", [("生地", "熟地黄"), ("鲜生地", "熟地黄"), ("对生地过敏", "熟地黄"),
                                          ("生地", "生地黄")])
def test_a_short_name_only_the_materia_table_gives_asks(pack, allergy, herb):
    # 生地 is in the materia table only, which groups a source's names: no drug-level table
    # says it is 生地黄, so it asks (a block that says where the table files it), never stops
    found = _allergy_issues(pack, allergy, herb)
    assert [(i.severity, i.code) for i in found] == [("block", "allergy_source")], found
    assert "请核实" in found[0].message


def test_every_name_the_materia_table_files_with_a_pack_herb_is_stopped_or_needs_a_reason(
        pack):
    # DB-01: an allergy recorded under any name the repo's materia table gives a pack
    # herb's source is caught, as a stop (the herb by name or by the knowledge base) or a
    # same-source block, for all the pack's herbs, not only the knowledge base's 23
    from bioagent.sources import materia
    names: dict[str, list[str]] = {}
    for name, drug in materia._NAME_INDEX.items():
        names.setdefault(drug, []).append(name)
    checked = 0
    for herb in pack.herbs:
        drug = materia.resolve_name(herb)
        if drug is None:                       # 首乌藤: not in the materia table
            continue
        for name in names[drug]:
            found = {i.code: i.severity for i in _allergy_issues(pack, name, herb)}
            assert found.get("allergy") == "stop" or "block" in (
                found.get("allergy_source"), found.get("allergy_homonym")), (name, herb, found)
            assert "warn" not in found.values(), (name, herb, found)
            checked += 1
    assert checked > 400, checked


@pytest.mark.parametrize("allergy,herb", [("白附子", "附子"), ("香附子", "附子"), ("核桃仁", "桃仁"),
                                          ("胡桃仁", "桃仁"), ("川牛膝", "牛膝"), ("牛膝", "川牛膝"),
                                          ("土茯苓", "茯苓"), ("胡黄连", "黄连"), ("茱萸", "山茱萸")])
def test_a_near_name_of_another_drug_needs_a_reason_instead_of_stopping(pack, allergy, herb):
    # one name inside the other is not the herb when the tables give both names to
    # different drugs: 白附子 (Typhonium) is not 附子 (Aconitum), 核桃仁 (walnut) is not 桃仁.
    # But a name such as 茱萸 may mean either drug, and these were stops: the practitioner
    # records which was meant (a block), as the message asks.
    found = _allergy_issues(pack, allergy, herb)
    assert [(i.severity, i.code) for i in found] == [("block", "allergy_near_name")], found
    assert "名称相近" in found[0].message and "不同药材" in found[0].message


@pytest.mark.parametrize("allergy", ["花粉过敏", "对花粉过敏", "花粉"])
def test_a_pollen_allergy_asks_about_tianhuafen_only(pack, allergy):
    # 花粉 is 天花粉's short name in prescriptions, and the materia table files it there, but
    # in an allergy history it is pollen, the most common entry. It warned for 瓜蒌 as well,
    # as a drug of the same species, and a bare 花粉 was an unsignable stop for 天花粉.
    assert not _allergy_issues(pack, allergy, "瓜蒌")
    found = _allergy_issues(pack, allergy, "天花粉")
    assert [(i.severity, i.code) for i in found] == [("block", "allergy_homonym")], found
    assert "植物花粉" in found[0].message and "请核实" in found[0].message
    # an allergy to 天花粉 itself, or under its other names, is not pollen
    assert [(i.severity, i.code) for i in _allergy_issues(pack, "天花粉过敏", "天花粉")] == [
        ("stop", "allergy")]
    assert [(i.severity, i.code) for i in _allergy_issues(pack, "瓜蒌根", "瓜蒌")] == [
        ("block", "allergy_source")]


def test_an_allergy_that_may_be_the_herb_is_signed_only_with_a_reason(pack, tmp_path):
    # allergy_source and allergy_near_name ask the practitioner to check (请核实); as warnings
    # they were signed with no trace that anyone had, among them pairs that were stops before
    for allergy, issue in (("红参", "allergy_source:人参"), ("土茯苓", "allergy_near_name:茯苓"),
                           ("复方甘草片过敏", "allergy_near_name:炙甘草"),
                           ("否认人参过敏", "allergy_denied:人参")):
        out = tmp_path / allergy
        s = assess(case(*SPLEEN, allergies=[allergy]), out, pack=pack)
        assert s.status != "blocked"                       # not a stop
        assert {i.id: i.severity for i in s.prescription.issues}[issue] == "block"
        with pytest.raises(SignOffError, match=issue):
            sign(out, practitioner="张医师", licence="L-1", decision="accept", pack=pack)
        rec = sign(out, practitioner="张医师", licence="L-1", decision="accept", pack=pack,
                   reasons={issue: "已核实：所记过敏非本药"})
        assert issue in rec["acknowledged"]


@pytest.mark.parametrize("allergy,herb", [("炙黄芪", "黄芪"), ("黄芪", "炙黄芪"), ("地黄", "熟地黄"),
                                          ("熟地黄", "地黄"), ("制附子", "附子"), ("半夏", "法半夏"),
                                          ("人参过敏史", "人参"), ("川当归", "当归")])
def test_a_processed_form_or_a_name_a_drug_level_table_gives_stops(pack, allergy, herb):
    # 熟地黄 is 地黄 processed, though the materia table files the two apart; 法半夏 is 半夏
    # processed by the knowledge base; 川当归 a reviewed alias of the materia table; 人参过敏史
    # is free text naming 人参
    found = _allergy_issues(pack, allergy, herb)
    assert ("stop", "allergy") in [(i.severity, i.code) for i in found], found


@pytest.mark.parametrize("allergy,herb,code", [("丹皮", "牡丹皮", "allergy_source"),
                                               ("银花", "金银花", "allergy_source"),
                                               ("莱菔", "莱菔子", "allergy_source"),
                                               ("车前", "车前子", "allergy_source"),
                                               ("菖蒲", "石菖蒲", "allergy_source"),
                                               ("寄生", "桑寄生", "allergy_source"),
                                               ("牡丹花", "牡丹皮", "allergy_near_name"),
                                               ("美国大杏仁", "苦杏仁", "allergy_near_name")])
def test_one_name_inside_another_never_stops_by_itself(pack, allergy, herb, code):
    # 莱菔 is the radish plant and 莱菔子 its seed, 车前 the plantain and 车前子 its seed, 菖蒲
    # most often Acorus calamus: the materia table files each pair under one entry, which
    # groups a source's names, and one name inside the other says no more. A stop claimed
    # “一名含另一名” as if it made the two one drug; now the record asks
    found = _allergy_issues(pack, allergy + "过敏", herb)
    assert [(i.severity, i.code) for i in found] == [("block", code)], found
    assert "一名含另一名，名表未将二者列为不同药材" not in found[0].message


@pytest.mark.parametrize("record,herb,expected", [
    ("对黄耆、人参过敏", "黄芪", ("stop", "allergy")), ("对黄耆，人参过敏", "黄芪", ("stop", "allergy")),
    ("对北芪、国老过敏", "黄芪", ("stop", "allergy")), ("对北芪；青霉素过敏", "黄芪", ("stop", "allergy")),
    ("对芍药、人参过敏", "白芍", ("stop", "allergy")), ("对丹皮、人参过敏", "牡丹皮", ("block", "allergy_source")),
    ("对云苓、人参过敏", "茯苓", ("stop", "allergy")), ("对半夏、人参过敏", "法半夏", ("stop", "allergy")),
    ("对乌头、人参过敏", "附子", ("block", "allergy_source")),
    ("对土茯苓、人参过敏", "茯苓", ("block", "allergy_near_name")),
    ("对川牛膝、人参过敏", "牛膝", ("block", "allergy_near_name")),
    ("对花粉、青霉素过敏", "天花粉", ("block", "allergy_homonym")),
    ("曾对黄耆、人参过敏", "黄芪", ("stop", "allergy")), ("对黄耆、人参过敏", "人参", ("stop", "allergy")),
    # names joined by 及 or written with no mark between them are read too
    ("对人参及黄耆过敏", "黄芪", ("stop", "allergy")), ("北芪人参过敏", "黄芪", ("stop", "allergy"))])
def test_every_name_of_an_allergy_list_is_read(pack, record, herb, expected):
    # the intake splits a record at 、，,；; and a reading by wordings lost the names that
    # were not first or last: 对黄耆 kept its 对, 对人参及黄耆过敏 was the one name 人参及黄耆
    found = _allergy_issues(pack, record, herb)
    assert [(i.severity, i.code) for i in found] == [expected], (record, found)
    assert "对" not in found[0].message.split("：")[-1], found[0].message


#: How an allergy history writes a name: 过敏 / 过敏史 / 过敏反应 after it, or a label before
#: it with either colon, and spaces anywhere; 对 or 有 before it, with 过敏 after it or not.
_RECORD_FORMS = ("{}过敏史", "过敏：{}", "过敏:{}", "过敏史：{}", "过敏史:{}", " 过敏史 ： {} ",
                 "{}过敏", "对{}过敏", "有{}过敏史", "药物过敏史：{}", "过敏原：{}",
                 "对{}", "有{}", "过敏源：{}", "过敏药物：{}", "过敏物：{}", "过敏反应：{}",
                 "既往过敏史：{}", "过敏史：对{}过敏", "{}过敏（皮疹）", "{}过敏反应", "曾对{}过敏",
                 "既往{}过敏", "对{}有过敏史", "对{}有过敏")


@pytest.mark.parametrize("form", _RECORD_FORMS)
@pytest.mark.parametrize("allergy,herb", [("黄耆", "黄芪"), ("北芪", "黄芪"), ("国老", "甘草"),
                                          ("芍药", "白芍"), ("熟地", "生地黄"), ("人参", "人参"),
                                          ("姜", "生姜"), ("夜交藤", "首乌藤")])
def test_an_allergy_in_a_historys_own_words_stops_the_herb_under_any_of_its_names(
        pack, form, allergy, herb):
    record = form.format(allergy)
    found = _allergy_issues(pack, record, herb)
    assert [(i.severity, i.code) for i in found] == [("stop", "allergy")], (record, found)
    assert "过敏与" not in found[0].message and "过敏史与" not in found[0].message


@pytest.mark.parametrize("form", _RECORD_FORMS)
def test_a_name_that_may_be_the_herb_asks_the_same_in_a_historys_own_words(pack, form):
    for allergy, herb, code in (("薯蓣", "山药", "allergy_source"),
                                ("白附子", "附子", "allergy_near_name"),
                                ("花粉", "天花粉", "allergy_homonym")):
        found = _allergy_issues(pack, form.format(allergy), herb)
        assert [(i.severity, i.code) for i in found] == [("block", code)], (form, found)
    assert not _allergy_issues(pack, form.format("花粉"), "瓜蒌")      # pollen, as before


def test_every_name_the_tables_know_is_found_standing_in_each_record_form(pack):
    # the scan finds the name wherever the form puts it, without touching it: a drug whose
    # name starts with a word of negation (没药, 无食子, 没食子) is that drug, not a denial
    from bioagent.clinic.allergy import _EVERYDAY, _EVERYDAY_NEXT, _vocab, fold, mentions
    from bioagent.clinic.prescribe import _kb
    from bioagent.sources import materia
    kb = _kb()
    names = {fold(n) for n in materia._NAME_INDEX} | set(_vocab(kb, pack).names)
    assert {"没药", "无食子", "没食子", "黄耆", "北芪", "夜交藤", "川黄柏"} <= names
    for name in sorted(names):
        for form in ("{}", "{}过敏史", "过敏：{}", "对{}过敏", "有{}", "既往{}过敏", "对{}有过敏史",
                     "患者自诉{}过敏", "过敏药物：{}（皮疹）"):
            got = [(m.name, m.word, m.denied) for m in mentions(form.format(name), kb=kb, pack=pack)]
            before = form[:form.index("{}")][-1:]
            after = form[form.index("{}") + 2:]
            if before + name[:1] in _EVERYDAY and name[-1:] + after[:1] in _EVERYDAY_NEXT:
                # 对于术后: the 于 of 对于 (regarding) does not start 于术 before 术后, so the
                # name is read inside the longer word, and asks; 对于术过敏 and 对于术有过敏史 are
                # 对 + 于术 (below)
                assert any(n == name and word for n, word, _ in got), (form, name, got)
                continue
            assert (name, "", False) in got, (form, name, got)


#: Records of no allergy, the review's 44: each names nothing, so each adds nothing.
_NO_ALLERGY = ["无", "否认过敏史", "过敏史：无", "无药物过敏史", "暂无", "NKDA", "none", "None", "NKA",
               "nil", "no", "No known drug allergies", "No known allergies", "N/A", "n/a",
               "无已知药物过敏", "无明确过敏史", "未发现过敏", "否认药物、食物过敏史", "否认药物及食物过敏史",
               "无特殊", "过敏史：（-）", "过敏史：阴性", "过敏史：否", "否", "没有过敏", "未诉过敏史",
               "未述过敏史", "不详", "过敏史不详", "无药物食物过敏史", "无过敏", "denies", "denied",
               "NKFA", "无食物药物过敏", "否认其他过敏史", "过敏史：-", "无 过敏史", "过敏史： 无 ",
               "无。", "（无）", "无过敏史及家族史", "否认食物、药物过敏史，否认输血史",
               # and the earlier list: labels and words with no allergen after them
               "否认", "没有", "过敏史:否认", " 过敏史 ： 无 ", "否认食物及药物过敏史", "过敏史", "过敏",
               "对", "有", "有过敏史", "过敏源", "过敏药物", "过敏体质", "过敏反应", "过敏史：不明",
               "过敏性鼻炎", "Allergies: none", "allergy: nil"]


@pytest.mark.parametrize("record", _NO_ALLERGY)
def test_a_record_of_no_allergy_names_nothing_and_matches_no_herb(pack, record):
    from bioagent.clinic.prescribe import _allergen
    assert _allergen(record) == "", record
    assert not [i.id for i in _all_herbs_issues(pack, record)], record


#: Allergies to what is no herb: silent, against every herb of the pack.
_NOT_A_HERB = ["青霉素过敏", "海鲜过敏", "头孢过敏", "芒果过敏", "牛奶过敏", "花生过敏", "尘螨过敏",
               "碘过敏", "虾蟹过敏", "小麦过敏", "乳胶过敏", "磺胺过敏", "鱼虾过敏", "酒精过敏",
               "阿司匹林过敏", "布洛芬过敏", "链霉素过敏", "猫毛过敏", "粉尘过敏", "紫外线过敏",
               "造影剂过敏", "鸡肉过敏", "牛肉过敏", "芝麻过敏", "大豆过敏", "坚果过敏", "核桃过敏",
               "金属过敏", "镍过敏", "化妆品过敏", "染发剂过敏", "苹果过敏", "西红柿过敏", "土豆过敏",
               "玉米过敏", "燕麦过敏", "龙虾过敏", "蜂毒过敏", "感冒药过敏", "安乃近过敏",
               "青霉素皮试阳性", "皮肤过敏", "季节性过敏", "过敏性哮喘", "蚊虫叮咬过敏", "动物皮毛过敏",
               "对青霉素、头孢过敏", "penicillin allergy", "allergic to sulfa", "shellfish"]


@pytest.mark.parametrize("record", _NOT_A_HERB)
def test_an_allergy_to_what_is_no_herb_is_silent(pack, record):
    assert not [(i.id, i.message) for i in _all_herbs_issues(pack, record)], record


# B1: a denial clause before a real allergy dropped the allergy (960ab72)
@pytest.mark.parametrize("record,herb", [
    ("食物过敏史：无 药物过敏史：黄芪", "黄芪"), ("无药物过敏史但对黄芪过敏", "黄芪"),
    ("否认药物过敏史 对黄芪过敏", "黄芪"), ("无过敏史（黄芪过敏除外）", "黄芪"),
    ("未发现过敏 人参过敏", "人参"), ("药物过敏史：无 食物过敏史：无 中药过敏：黄芪", "黄芪"),
    ("无食物过敏 药物过敏：黄芪", "黄芪"), ("无药物过敏史，但对黄芪过敏", "黄芪"),
    ("过敏史：无 （对人参过敏）", "人参"), ("NKDA except 黄芪", "黄芪"), ("无\n对黄芪过敏", "黄芪"),
    ("过敏史：否认 人参过敏史", "人参"), ("过敏史不详 人参过敏", "人参"), ("过敏体质 对人参过敏", "人参"),
    ("过敏性鼻炎 人参过敏", "人参"), ("过敏原：无 过敏药物：黄芪", "黄芪"),
    ("食物过敏：无 药物过敏：黄芪", "黄芪"), ("食物过敏史：无\t药物过敏史：黄芪", "黄芪"),
    ("否认食物过敏史 药物过敏史：人参", "人参"), ("无药物过敏史 但服人参后出现皮疹", "人参"),
    ("不详 人参过敏", "人参"), ("无药物过敏史，对北芪过敏", "黄芪"),
    # a denial that an event closes (无明显诱因 … 出现) denies nothing after it
    ("无明显诱因出现人参过敏", "人参")])
def test_a_denial_before_an_allergy_does_not_drop_it(pack, record, herb):
    found = _allergy_issues(pack, record, herb)
    assert [(i.severity, i.code) for i in found] == [("stop", "allergy")], (record, found)


@pytest.mark.parametrize("record", ["否认黄芪过敏", "无黄芪过敏史", "否认对黄芪过敏", "患者否认黄芪过敏",
                                    "黄芪：无", "黄芪过敏史：否", "黄芪（-）", "对黄芪不过敏",
                                    "否认青霉素、黄芪过敏", "否认北芪过敏", "否認黃耆過敏",
                                    "no huangqi allergy", "denies 黄芪 allergy", "NKDA 北芪",
                                    "黄芪皮试阴性", "过敏史：黄芪（-）"])
def test_a_herb_the_history_denies_asks_for_confirmation_and_is_never_silent(pack, record):
    # a denial that names a herb may be read as one, but only as a block that asks the
    # practitioner to confirm it: never a silent pass
    found = _allergy_issues(pack, record, "黄芪")
    assert [(i.severity, i.code) for i in found] == [("block", "allergy_denied")], (record, found)
    assert "否认的字样" in found[0].message and "可能是否认" in found[0].message, found
    assert "确认该否认无误" in found[0].message


@pytest.mark.parametrize("record,herb,word", [("对于术后感染", "白术", "对于术"),
                                              ("由于术后出现皮疹", "白术", "由于术"),
                                              ("对鸡蛋白及过敏", "白及", "鸡蛋白及")])
def test_a_name_whose_first_character_ends_an_everyday_word_asks(pack, record, herb, word):
    # 于术 is 白术's other name, but in 对于术后 the 于 belongs to 对于 (regarding), and in
    # 鸡蛋白及 the 白 to 蛋白 (protein): read standing, these were stops that named the herb
    found = _allergy_issues(pack, record, herb)
    assert [(i.severity, i.code) for i in found] == [("block", "allergy_near_name")], found
    assert f"记录中的“{word}”" in found[0].message, found[0].message


def test_a_denial_hides_no_name_in_another_clause(pack):
    it = parse_intake(case(*SPLEEN, allergies=["否认黄芪过敏，对人参过敏", "无过敏史 甘草过敏"]),
                      pack)
    lines = [Line(h, 9, "practitioner") for h in ("黄芪", "人参", "甘草")]
    found = {i.id: i.severity for i in check(lines, it, pack) if i.code in _ALLERGY_CODES}
    assert found == {"allergy_denied:黄芪": "block", "allergy:人参": "stop", "allergy:甘草": "stop"}


# B2: an alias was read only in the wordings a list held
@pytest.mark.parametrize("record,herb", [
    # narrative
    ("患者对国老过敏", "甘草"), ("自诉国老过敏", "甘草"), ("3年前北芪过敏", "黄芪"),
    ("以前对芍药过敏", "白芍"), ("可疑熟地过敏", "熟地黄"), ("服用北芪后出现皮疹", "黄芪"),
    ("对芍药严重过敏", "白芍"), ("对北芪也过敏", "黄芪"), ("2019年对北芪过敏", "黄芪"),
    ("幼时对北芪过敏", "黄芪"), ("北芪过敏3年", "黄芪"), ("过敏史：有 对黄耆过敏", "黄芪"),
    # lists
    ("北芪/人参过敏", "黄芪"), ("黄耆 人参过敏", "黄芪"), ("对黄耆及人参过敏", "黄芪"),
    ("对北芪、国老等过敏", "黄芪"), ("对北芪、国老等过敏", "甘草"), ("对青霉素、北芪均过敏", "黄芪"),
    ("北芪、国老等", "甘草"), ("对国老等过敏", "炙甘草"),
    # labels
    ("过敏药品：北芪", "黄芪"), ("变应原：北芪", "黄芪"), ("已知过敏：北芪", "黄芪"),
    ("过敏史：黄耆（皮疹）", "黄芪"), ("过敏史：北芪(+)", "黄芪"), ("致敏原：北芪", "黄芪"),
    ("致敏药物：北芪", "黄芪"), ("过敏史(有)：北芪", "黄芪"), ("药物过敏史（有）：北芪", "黄芪"),
    ("过敏药物名称：北芪", "黄芪"), ("过敏史：有 北芪", "黄芪"), ("过敏史：+ 北芪", "黄芪"),
    ("过敏情况：北芪", "黄芪"), ("药物过敏情况：北芪", "黄芪"), ("过敏：是，北芪", "黄芪"),
    ("明确过敏：北芪", "黄芪"), ("北芪（过敏）", "黄芪"), ("北芪（皮疹，已停药）", "黄芪"),
    ("北芪；过敏", "黄芪"), ("北芪过敏，表现为皮疹", "黄芪"), ("北芪:皮疹", "黄芪"),
    ("过敏药物：北芪（皮疹）", "黄芪"),
    # English, pinyin and Latin
    ("allergic to 北芪", "黄芪"), ("Allergy: 黄耆", "黄芪"), ("Allergies: 北芪", "黄芪"),
    ("ALLERGY: 北芪", "黄芪"), ("Allergic to: 黄耆", "黄芪"), ("allergy - 国老", "甘草"),
    ("allergic to huangqi", "黄芪"), ("Allergy: huangqi", "黄芪"), ("huang qi", "黄芪"),
    ("Astragali Radix allergy", "黄芪"), ("allergic to Astragali Radix", "黄芪"),
    ("astragaliradix", "黄芪"), ("Glycyrrhizae Radix et Rhizoma", "甘草"),
    ("Ginseng Radix et Rhizoma", "人参"),
    # characters that show nothing, or other marks
    ("北芪​过敏", "黄芪"), ("﻿北芪过敏", "黄芪"), ("北芪⁠过敏", "黄芪"),
    ("北芪\x00过敏", "黄芪"), ("过敏史∶北芪", "黄芪"), ("过敏史︰北芪", "黄芪"),
    ("对「北芪」过敏", "黄芪"), ("对《北芪》过敏", "黄芪"), ("对·北芪·过敏", "黄芪"),
    ("过敏史：北芪　", "黄芪"), ("北芪\xa0过敏", "黄芪"), ("北芪 过 敏", "黄芪"),
    ("过 敏 史 ： 北 芪", "黄芪")])
def test_an_alias_is_read_wherever_the_history_writes_it(pack, record, herb):
    found = _allergy_issues(pack, record, herb)
    assert [(i.severity, i.code) for i in found] == [("stop", "allergy")], (record, found)


# S1: traditional characters
@pytest.mark.parametrize("record,herb", [("黃耆過敏", "黄芪"), ("對北芪過敏", "黄芪"), ("過敏史：北芪", "黄芪"),
                                         ("人參过敏史", "人参"), ("黃耆过敏史", "黄芪"), ("當歸過敏", "当归"),
                                         ("對黃耆過敏", "黄芪"), ("否認藥物過敏史，對人參過敏", "人参"),
                                         ("對國老過敏", "炙甘草"), ("熟地黃過敏", "熟地黄")])
def test_an_allergy_in_traditional_characters_stops_the_herb(pack, record, herb):
    # the materia table's own fold (materia.to_simplified) reads the names; the words around
    # them (過敏, 對, 否認) are folded with them
    assert _found(pack, record, herb) == [("stop", "allergy")], record


# S2 and S3: a herb's name inside a longer word
@pytest.mark.parametrize("record,herb,inner,word", [
    ("郁金香过敏", "郁金", "郁金", "郁金香"), ("龙胆紫过敏", "龙胆", "龙胆", "龙胆紫"),
    ("对牛奶蛋白及鸡蛋过敏", "白及", "白及", "牛奶蛋白及"), ("蛋白及乳制品过敏", "白及", "白及", None),
    ("白蛋白及血浆过敏", "白及", "白及", None), ("对鸡蛋白及花生过敏", "白及", "白及", None),
    ("海藻酸钠过敏", "海藻", "海藻", "海藻酸钠"), ("预防风疹疫苗过敏", "防风", "防风", "预防风疹疫苗"),
    ("红花油过敏", "红花", "红花", "红花油"), ("藏红花过敏", "红花", "红花", "藏红花"),
    ("西红花过敏", "红花", "红花", "西红花"), ("双黄连过敏", "黄连", "黄连", "双黄连"),
    ("双黄连注射液过敏", "黄连", "黄连", "双黄连注射液"), ("黄连素过敏", "黄连", "黄连", "黄连素"),
    ("麦芽糖过敏", "麦芽", "麦芽", "麦芽糖"), ("伪麻黄碱过敏", "麻黄", "麻黄", "伪麻黄碱"),
    ("野菊花过敏", "菊花", "菊花", "野菊花"), ("复方丹参滴丸过敏", "丹参", "丹参", "复方丹参滴丸"),
    ("黄芪注射液过敏", "黄芪", "黄芪", "黄芪注射液"),
    ("知柏地黄丸过敏", "熟地黄", "地黄", "知柏地黄丸"), ("知柏地黄丸过敏", "生地黄", "地黄", "知柏地黄丸"),
    ("复方甘草片过敏", "甘草", "甘草", "复方甘草片"), ("复方甘草片过敏", "炙甘草", "甘草", "复方甘草片")])
def test_a_herb_name_inside_a_longer_word_asks_and_never_claims_the_herb(pack, record, herb,
                                                                       inner, word):
    # 郁金香 is the tulip, 龙胆紫 gentian violet, 蛋白 protein, 海藻酸钠 sodium alginate, 风疹
    # rubella, 西红花 saffron, 双黄连 and 知柏地黄丸 patent medicines: each was a stop that said
    # the record named the herb. The record holds the characters; the practitioner says
    # whether it meant the herb. (六味地黄丸, a formula of the pack, is read as its herbs.)
    found = _allergy_issues(pack, record, herb)
    assert [(i.severity, i.code) for i in found] == [("block", "allergy_near_name")], (record, found)
    msg = found[0].message
    assert f"含有“{inner}”的字样" in msg and "未必是指" in msg and "请核实" in msg, msg
    if word:
        assert f"记录中的“{word}”" in msg, msg
    assert f"{record.removesuffix('过敏')}与" not in msg, msg        # never the whole text


def test_the_reason_names_the_herb_found_not_the_text_around_it(pack):
    # S3: 患者对地黄过敏 gave “患者对地黄与熟地黄同为地黄或其炮制品”
    found = _allergy_issues(pack, "患者对地黄过敏", "熟地黄")
    assert [(i.severity, i.code) for i in found] == [("stop", "allergy")], found
    assert "地黄与熟地黄" in found[0].message and "对地黄与" not in found[0].message
    # a product word the materia table resolves to the herb is the herb: 甘草片, 山药粉
    assert _found(pack, "甘草片过敏", "甘草") == [("stop", "allergy")]
    assert _found(pack, "山药粉过敏", "山药") == [("stop", "allergy")]


# S7: the pack's cited aliases
def test_the_packs_cited_aliases_are_read(pack):
    found = _allergy_issues(pack, "对夜交藤过敏", "首乌藤")
    assert [(i.severity, i.code) for i in found] == [("stop", "allergy")], found
    assert "夜交藤为首乌藤的别名" in found[0].message and "《中药学》" in found[0].message
    for record, herb in (("川黄柏过敏", "黄柏"), ("味连过敏", "黄连"), ("对雅连过敏", "黄连"),
                         ("黑顺片过敏", "附子"), ("白附片过敏", "附子"), ("对松贝过敏", "川贝母"),
                         ("大贝过敏", "浙贝母"), ("连丹皮过敏", "牡丹皮"), ("毛山药过敏", "山药")):
        found = _allergy_issues(pack, record, herb)
        assert [(i.severity, i.code) for i in found] == [("stop", "allergy")], (record, found)
        assert "药典" in found[0].source, found[0]
    # 白附子 is still another drug (Typhonium), though the Pharmacopoeia's 白附片 is 附子
    assert _found(pack, "白附子过敏", "附子") == [("block", "allergy_near_name")]
    # and a prescribed alias is its herb: 附子 against 黑顺片 written in the prescription
    assert ("stop", "allergy") in _found(pack, "附子过敏", "黑顺片")


@pytest.mark.parametrize("record", ["对何首乌过敏", "对首乌过敏", "制首乌过敏"])
def test_he_shou_wu_and_shou_wu_teng_are_one_plant_not_one_drug(pack, record):
    # 何首乌 is the root of Polygonum multiflorum and 首乌藤 its stem (药典 2020): 首乌 stopped
    # 首乌藤 (the name inside the other) while 何首乌 passed it in silence. Both now ask, on
    # the species the pack records for 首乌藤
    found = _allergy_issues(pack, record, "首乌藤")
    assert [(i.severity, i.code) for i in found] == [("block", "allergy_source")], found
    assert "Polygonum multiflorum" in found[0].message and "未必是同一种药" in found[0].message


def test_every_pack_alias_cites_its_source_and_no_table_gives_it_another_drug(pack):
    from bioagent.clinic.allergy import _identity
    from bioagent.clinic.prescribe import _kb
    from bioagent.sources import materia
    kb = _kb()
    assert pack.herb_aliases and pack.herb_sources
    for alias, (herb, source) in pack.herb_aliases.items():
        assert herb in pack.herbs and ("药典" in source or "《中药学》" in source), alias
        assert herb in source or alias in source, alias              # the entry it is cited from
        drug = materia.resolve_name(alias)
        assert drug is None or drug == materia.resolve_name(herb), (alias, drug)
        ids = _identity(kb, alias)
        assert not ids or ids == _identity(kb, herb), alias
    for herb, (species, source) in pack.herb_sources.items():
        assert materia.resolve_name(herb) is None and species and "药典" in source, herb


def test_a_pack_alias_with_no_source_or_given_twice_is_refused(pack, tmp_path):
    base = json.loads(Path(pack.path).read_text(encoding="utf-8"))
    for extra, refusal in (({"herb": "黄芪", "aliases": ["夜交藤"], "source": "x"}, "夜交藤"),
                           ({"herb": "黄芪", "aliases": ["芪"], "source": ""}, "cite its source"),
                           ({"herb": "不在包中", "aliases": ["某药"], "source": "x"},
                            "not a herb of the pack")):
        data = json.loads(json.dumps(base))
        data["herb_aliases"].append(extra)
        bad = tmp_path / "pack.json"
        bad.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        with pytest.raises(P.PackError, match=refusal):
            P.load_pack(bad)


# S5 and S6: what the field holds
@pytest.mark.parametrize("blank", ["", "   ", [""], ["。。。"], ["  ", "、"], "///"])
def test_a_blank_allergy_field_reads_as_not_asked(pack, blank):
    it = parse_intake(case(*SPLEEN, allergies=blank), pack)
    assert it.allergies is None and "过敏史未询问" in it.warnings
    lines =[Line(h, 9, "practitioner") for h in pack.herbs]
    assert not [i for i in check(lines, it, pack) if i.code in _ALLERGY_CODES]
    # [] is "asked, none", as the template says
    it = parse_intake(case(*SPLEEN, allergies=[]), pack)
    assert it.allergies == () and "过敏史未询问" not in it.warnings


@pytest.mark.parametrize("field", ["allergies", "medications", "conditions"])
@pytest.mark.parametrize("value", [5, 5.5, True, {"北芪": "皮疹"}, [{"substance": "北芪"}], [None],
                                   [["北芪"]], ["北芪", 3]])
def test_a_list_field_that_is_not_text_is_refused(pack, field, value):
    # the studio refuses these with this message; the command line and the library turned an
    # object into text (and read an allergy out of its keys) or raised a TypeError
    doc = case(*SPLEEN)
    doc[field] = value
    with pytest.raises(IntakeError, match=rf"^{field} must be a list of strings or null$"):
        parse_intake(doc, pack)


# the property: no name any table gives a pack herb passes it in silence
def _traditional(name):
    from bioagent.sources import materia
    back: dict[str, str] = {}
    for t, s in zip(materia._TRADITIONAL, materia._SIMPLIFIED):
        back.setdefault(s, t)
    return "".join(back.get(c, c) for c in name)


#: C1: the common forms a record writes a name in: its own name; 对X过敏; X过敏(史);
#: 过敏(史/原/源/药物)：X; narrative; lists separated by 、，,；;/ 及 和 与 或, line breaks, tabs or
#: spaces; English; a denial clause before the allergy.
_C1_TEMPLATES = ("{}", "对{}过敏", "{}过敏", "{}过敏史", "过敏史：{}", "过敏(史)：{}", "过敏原：{}", "过敏源：{}",
                 "过敏药物：{}", "患者对{}过敏", "自诉{}过敏", "既往{}过敏", "曾对{}过敏", "3年前对{}过敏",
                 "服用{}后出现皮疹", "{}、青霉素过敏", "青霉素、{}过敏", "青霉素，{}", "{},头孢", "头孢；{}", "头孢;{}",
                 "青霉素/{}过敏", "青霉素及{}均过敏", "青霉素和{}过敏", "{}与头孢过敏", "青霉素或{}",
                 "过敏史：\n青霉素\n{}\n头孢", "青霉素\t{}", "青霉素 {}", "{} 黄芪", "姜 {}", "allergic to {}",
                 "Allergy: {}", "{} allergy", "无药物过敏史，对{}过敏", "否认药物过敏史 {}过敏",
                 "食物过敏史：无 药物过敏史：{}", "一吃{}就过敏", "因{}过敏住院", "过敏药物：{}（皮疹）")
_TRADITIONAL_TEMPLATES = ("對{}過敏", "過敏史：{}")


def test_every_name_any_table_gives_a_pack_herb_is_never_silent(pack):
    # C1: every name the tables give a pack herb (the knowledge base's names and aliases, the
    # materia table's names, Latin names and species, the pack's cited aliases, pinyin and
    # English names), in every common form and in traditional characters, gives that herb at
    # least a block
    from bioagent.clinic.allergy import allergy_issues, names_of
    from bioagent.clinic.prescribe import _kb
    kb = _kb()
    checked, missed = 0, []
    for herb in pack.herbs:
        for kind, names in names_of(herb, kb=kb, pack=pack).items():
            for name in names:
                records = [t.format(name) for t in _C1_TEMPLATES]
                if kind == "chinese":
                    records += [t.format(_traditional(name)) for t in _TRADITIONAL_TEMPLATES]
                for record in records:
                    checked += 1
                    if not allergy_issues([herb], [record], kb, pack):
                        missed.append((herb, name, record))
    assert not missed, missed[:20]
    assert checked > 40000, checked


@pytest.mark.parametrize("record", ["😀", "\ud800北芪过敏", "北芪abc😀过敏ΑΒΓ한국어", "，。、；：！？（）",
                                    "\\u5317\\u82aa", "黄" * 5000, "过敏" * 5000])
def test_odd_text_in_a_history_does_not_break_the_check(pack, record):
    from bioagent.clinic.allergy import allergy_issues
    from bioagent.clinic.prescribe import _kb
    out = allergy_issues(list(pack.herbs), [record], _kb(), pack)
    if "北芪" in record:
        assert ("allergy:黄芪", "stop") in [(i[0], i[1]) for i in out], out


def test_every_formula_of_the_pack_in_its_product_forms_asks_about_every_herb(pack):
    # C1: a formula of the pack by name or in its 丸/汤/散/颗粒 form
    from bioagent.clinic.allergy import allergy_issues
    from bioagent.clinic.prescribe import _kb
    kb = _kb()
    for name, f in pack.formulas.items():
        herbs = [h for h, _ in f.herbs]
        stem = name[:-1] if name[-1] in "汤丸散饮" else name
        for record in (f"{name}过敏", f"对{name}过敏", f"服用{name}后出现皮疹", f"青霉素、{name}过敏",
                       *(f"{stem}{form}过敏" for form in ("丸", "汤", "散", "颗粒"))):
            out = allergy_issues(herbs, [record], kb, pack)
            assert {i[4][0] for i in out} == set(herbs), (record, sorted(i[0] for i in out))
            assert all(i[1] == "block" for i in out), (record, out)


#: The reasons a stop gives: what a drug-level table says (the same name, the knowledge base,
#: a cited or reviewed alias, a processed form, the pinyin or Latin name). Anything weaker is
#: a block: one source in the materia table, a species, a near name, a name inside a word.
_STOP_REASONS = re.compile(r"为同一药材|的别名|或其炮制品|即.+（.*(?:拼音|拉丁名)")
_WEAK_CLAIMS = ("同列于", "同源", "一名含另一名", "含有", "未必", "可能", "字样")


def test_a_stop_claims_only_what_a_drug_level_table_says(pack):
    # C2: over the whole acceptance corpus and the C1 forms, every stop's reason is one a
    # drug-level table gives, and no stop carries a weaker claim
    from bioagent.clinic.allergy import allergy_issues
    from bioagent.clinic.prescribe import _kb
    kb = _kb()
    records = [c["record"] for c in _corpus()["cases"]]
    records += [t.format(n) for t in _C1_TEMPLATES[:12] for n in ("北芪", "国老", "夜交藤", "地黄", "姜", "丹皮",
                                                                 "huangqi", "Radix Astragali", "莱菔", "车前")]
    for record in records:
        for i in allergy_issues(list(pack.herbs), [record], kb, pack):
            if i[1] != "stop":
                continue
            reason = i[3].split("：处方含", 1)[1]
            assert not any(w in reason for w in _WEAK_CLAIMS), (record, i[3])
            herb = i[4][0]
            assert reason == herb or _STOP_REASONS.search(reason), (record, i[3])


@pytest.mark.parametrize("make,expected", [
    (lambda n: "嗯" + "黄芪" * (n // 2) + "丸", {"allergy_near_name:黄芪": "block"}),  # names in one word
    (lambda n: "嗯" + "钩" * n + "嗯", {"allergy_near_name:钩藤": "block"}),  # 钩钩 overlapping itself
    (lambda n: "嗯" + "红花椒" * (n // 3) + "嗯", {"allergy_near_name:红花": "block"}),  # 红花/花椒/椒红
    (lambda n: "郁" + "黄芪" * (n // 2) + "香", {"allergy_near_name:黄芪": "block"}),
    (lambda n: ("北 芪 " * n)[:n], {"allergy:黄芪": "stop"}),       # names across whitespace
    (lambda n: ("姜 黄 " * n)[:n], {"allergy:生姜": "stop"}),
    (lambda n: ("人参、" * n)[:n], {"allergy:人参": "stop"}),       # one long list
    (lambda n: ("目前在吃人参" * n)[:n], {"allergy_taken:人参": "block"}),
    (lambda n: ("四君子汤" * n)[:n], {"allergy_formula:白术": "block"}),
    (lambda n: ("huang qi " * n)[:n], {"allergy:黄芪": "stop"}),
    (lambda n: "否认" * (n // 2), {}), (lambda n: "无" * n + "x", {}), (lambda n: "对" * n, {}),
    (lambda n: "过敏" * (n // 2), {}), (lambda n: "白" * n, {}),
    (lambda n: "对北芪过敏" + "很" * n, {"allergy:黄芪": "stop"}),
    (lambda n: "黄芪" + " " * n + "过敏", {"allergy:黄芪": "stop"}),
    # r7 review round 1: a scan past the marks after a clause, once per name in it, and the
    # separators after a list, once per name of it
    (lambda n: ("吃人参" * n)[: n // 2] + "，" * (n - n // 2), {"allergy_taken:人参": "block"}),
    (lambda n: ("人参" * n)[: n // 2] + "：" * (n - n // 2), {"allergy:人参": "stop"}),
    (lambda n: ("人参" * n)[: n // 2] + "？" * (n - n // 2), {"allergy:人参": "stop"}),
    (lambda n: ("人参" * n)[: n // 2] + "-" * (n - n // 2), {"allergy:人参": "stop"}),
    (lambda n: ("huangqi " * n)[: n // 2] + "." * (n - n // 2), {"allergy:黄芪": "stop"}),
    (lambda n: ("taking huangqi " * n)[: n // 2] + "." * (n - n // 2), {"allergy_taken:黄芪": "block"}),
    (lambda n: ("服用黄芪后，" * n)[:n], {"allergy_taken:黄芪": "block"}),
    (lambda n: ("NKDA\n黄芪过敏\n" * n)[:n], {"allergy:黄芪": "stop"}),
    (lambda n: ("人参吃了没事" * n)[:n], {"allergy_denied:人参": "block"}),
    (lambda n: "黄芪" + "\n" * n + "过敏", {"allergy:黄芪": "stop"}),
], ids=["run_in_word", "gou_chain", "honghuajiao", "yu_huangqi_xiang", "bei_qi_spaced", "jiang_huang_spaced",
        "list", "taken", "formula", "pinyin", "fouren", "wu", "dui", "guomin", "bai", "hen", "spaces",
        "taken_commas", "chain_colons", "chain_qmarks", "chain_dashes", "pinyin_dots", "taken_pinyin_dots",
        "temporal", "nkda_lines", "tolerated", "line_breaks"])
def test_a_long_history_is_read_in_linear_time_with_bounded_messages(pack, make, expected):
    # C4: 20k and 200k characters of any content (runs of names with no separators, chains
    # of overlapping names, names across whitespace, long lists): the time grows with the
    # length, not its square, and each message quotes a bounded excerpt
    import time
    from bioagent.clinic.allergy import allergy_issues
    from bioagent.clinic.prescribe import _kb
    kb, herbs = _kb(), list(pack.herbs)
    allergy_issues(herbs, ["黄芪"], kb, pack)

    def timed(record):
        t0 = time.perf_counter()
        out = allergy_issues(herbs, [record], kb, pack)
        return time.perf_counter() - t0, out

    small, _ = timed(make(20000))
    large, out = timed(make(200000))
    assert large < 30 and large < 20 * small + 1, (small, large)
    assert all(len(i[3]) < 1200 for i in out), max(len(i[3]) for i in out)
    got = {i[0]: i[1] for i in out}
    assert {k: got.get(k) for k in expected} == expected, sorted(got.items())[:10]
    assert expected or not got, sorted(got.items())[:10]


def test_a_long_history_of_random_names_is_read_in_linear_time(pack):
    # every name the scan knows, at random, with no mark between them or with one: each
    # distinct reading is matched to each herb once, so the time stops growing with it
    import random
    import time
    from bioagent.clinic.allergy import _vocab, allergy_issues
    from bioagent.clinic.prescribe import _kb
    kb, herbs = _kb(), list(pack.herbs)
    names = sorted(_vocab(kb, pack).names)
    for sep in ("", "、", " "):
        rng = random.Random(7)

        def record(n):
            out, size = [], 0
            while size < n:
                out.append(rng.choice(names))
                size += len(out[-1]) + len(sep)
            return sep.join(out)[:n]

        t0 = time.perf_counter()
        allergy_issues(herbs, [record(20000)], kb, pack)
        small = time.perf_counter() - t0
        t0 = time.perf_counter()
        out = allergy_issues(herbs, [record(200000)], kb, pack)
        large = time.perf_counter() - t0
        assert large < 30 and large < 20 * small + 1, (sep, small, large)
        assert all(len(i[3]) < 1200 for i in out), (sep, max(len(i[3]) for i in out))


# ------------------------------------------------------------------ the acceptance corpus
_CORPUS = Path(__file__).resolve().parent / "data" / "clinic_allergy_corpus.json"


def _corpus():
    return json.loads(_CORPUS.read_text(encoding="utf-8"))


def _outcome(issues, herb):
    severities = {i[1] for i in issues if i[4][0] == herb}
    return "stop" if "stop" in severities else "block" if severities else "none"


@pytest.mark.parametrize("case", _corpus()["cases"], ids=lambda c: c["id"])
def test_the_acceptance_corpus(pack, case):
    # every repro of the two review rounds and the review6 scripts, and the common forms of
    # C1 and C3, each with its outcome per prescribed herb: stop, block or none. A case marked
    # nit is a form outside the criteria (a name no table holds), kept to pin the limit
    from bioagent.clinic.allergy import allergy_issues
    from bioagent.clinic.prescribe import _kb
    herbs = list(pack.herbs) if case["herbs"] == "pack" else case["herbs"]
    out = allergy_issues(herbs, [case["record"]], _kb(), pack)
    for herb in herbs:
        mine = [i for i in out if i[4][0] == herb]
        assert _outcome(out, herb) == case["expect"].get(herb, "none"), (herb, mine)
        if herb in case.get("codes", {}):
            assert case["codes"][herb] in {i[2] for i in mine}, (herb, mine)
        for text in case.get("has", {}).get(herb, ()):
            assert any(text in i[3] for i in mine), (herb, text, mine)
        for text in case.get("lacks", {}).get(herb, ()):
            assert not any(text in i[3] for i in mine), (herb, text, mine)


@pytest.mark.parametrize("group", _corpus()["silent"], ids=lambda g: g["id"])
def test_the_acceptance_corpus_records_that_name_no_herb(pack, group):
    # C3: records of no allergy, allergies to what is no herb, and everyday words that hold a
    # name's characters give nothing for any herb of the pack
    from bioagent.clinic.allergy import allergy_issues
    from bioagent.clinic.prescribe import _kb
    noisy = {r: [(i[4][0], i[1], i[2]) for i in allergy_issues(list(pack.herbs), [r], _kb(), pack)]
             for r in group["records"]}
    assert not {r: v for r, v in noisy.items() if v}


@pytest.mark.parametrize("entry", _corpus()["intake"], ids=lambda e: e["id"])
def test_the_acceptance_corpus_allergy_fields(pack, entry):
    # C3: a blank field, one holding only a label, or one saying not asked or not known reads
    # as not asked (the 过敏史未询问 warning); an answer, a none included, reads as asked
    it = parse_intake(case(*SPLEEN, allergies=entry["allergies"]), pack)
    asked = it.allergies is not None and "过敏史未询问" not in it.warnings
    assert asked == entry["asked"], (entry["allergies"], it.allergies, it.warnings)


def test_the_corpus_holds_every_repro_and_each_criterion():
    doc = _corpus()
    ids = [c["id"] for c in doc["cases"]]
    assert len(ids) == len(set(ids))
    sources = {c["source"].split(":")[0] for c in doc["cases"]}
    for source in ("r6 review round 1 (d087468)", "r6 review round 2 (f8cf0eb)", "review6 scripts",
                   "C1 common form", "C3 common corpus", "r7 review round 1 (03a822b)",
                   "r7 review round 2 (b989f7d)"):
        assert any(s.startswith(source) for s in sources), source
    assert {c["expect"].get(h) for c in doc["cases"] for h in c["expect"]} >= {"stop", "block", "none"}
    assert sum(len(g["records"]) for g in doc["silent"]) > 200 and len(doc["intake"]) > 80


def test_a_list_allergy_written_among_the_conditions_is_read_whole(pack):
    # the intake splits conditions at 、 and ，: 对黄芪、人参过敏 lost 黄芪
    it = parse_intake(case(*SPLEEN, allergies=[], conditions=["对黄芪、人参过敏", "高血压"]), pack)
    found = {i.id: i.severity for i in check([Line("黄芪", 9, "practitioner"), Line("人参", 9, "practitioner")],
                                             it, pack) if i.code in _ALLERGY_CODES}
    assert found == {"allergy:黄芪": "stop", "allergy:人参": "stop"}, found


# ------------------------------------------- r7 review round 1 (03a822b), beyond the corpus
#: A reaction told after the herb (C1: 服用X后出现皮疹 and its variants). They were read as the
#: herb taken, a block whose message said no reaction was written.
_REACTION_TEMPLATES = ("服用{}后，皮肤瘙痒", "服{}后，皮肤过敏", "服用{}，3小时后出现皮疹", "服{}后,皮肤瘙痒",
                       "服{}后 皮肤瘙痒", "服{}后，面部红肿", "吃{}后，嘴唇肿了", "服用{}后出现呼吸困难",
                       "服用{}后起疙瘩", "皮疹（服{}后）", "荨麻疹（服用{}后）", "出现皮疹，考虑与服用{}有关",
                       "全身瘙痒，服用{}所致", "服用{}后，自觉胸闷")
#: A denial on one line and the allergy on the next, or after a denial that takes no object
#: (C1: a denial clause before the allergy, line breaks as separators).
_LINE_TEMPLATES = ("NKDA\n{}过敏", "NKDA\n\n对{}过敏", "NKDA\r\n{}过敏", "No known drug allergies\n{}过敏",
                   "None\n{}过敏", "denies allergies\n{}过敏", "nil\n{}过敏", "No\n{}过敏", "NKDA {}过敏",
                   "nil {}过敏", "NKDA\t{}过敏", "No known drug allergies {}过敏")
#: A herb taken or tolerated (C3: at most a block that says so, never a stop).
_TOLERATED_TEMPLATES = ("服用{}后无不适", "{}服用后未见过敏", "服用{}期间无不良反应", "{}吃了没事", "{}用过，无过敏",
                        "{}：服用中", "{}（服用中）", "{}可以吃", "{}没问题", "目前在吃{}，过敏史：无", "目前在吃{}",
                        "目前在吃{}，青霉素过敏")


def test_a_reaction_after_a_herb_or_an_allergy_after_a_denial_line_stops_every_pack_herb(pack):
    from bioagent.clinic.allergy import allergy_issues
    from bioagent.clinic.prescribe import _kb
    kb = _kb()
    missed = []
    for herb in pack.herbs:
        for t in _REACTION_TEMPLATES + _LINE_TEMPLATES:
            out = allergy_issues([herb], [t.format(herb)], kb, pack)
            if ("stop", "allergy") not in {(i[1], i[2]) for i in out}:
                missed.append((t.format(herb), [(i[1], i[2]) for i in out]))
        for t in _TOLERATED_TEMPLATES:
            out = allergy_issues([herb], [t.format(herb)], kb, pack)
            if not out or any(i[1] == "stop" for i in out):
                missed.append((t.format(herb), [(i[1], i[2]) for i in out]))
    assert not missed, missed[:20]


def test_a_reaction_narrative_blocks_the_draft_and_a_tolerated_herb_never_stops_it(pack, tmp_path):
    s = assess(case(*SPLEEN, allergies=["服用人参后，皮肤瘙痒"]), tmp_path / "a", pack=pack)
    assert s.status == "blocked" and "allergy:人参" in {i.id for i in s.prescription.issues}
    s = assess(case(*SPLEEN, allergies=["服用人参后无不适"]), tmp_path / "b", pack=pack)
    found = {(i.id, i.severity) for i in s.prescription.issues if i.code in _ALLERGY_CODES}
    assert ("allergy_denied:人参", "block") in found and not any(v == "stop" for _, v in found), found
    it = parse_intake(case(*SPLEEN, allergies=[], conditions=["过敏性鼻炎，服用黄芪后无不适"]), pack)
    found = {(i.id, i.severity) for i in check([Line("黄芪", 9, "practitioner")], it, pack)
             if i.code in _ALLERGY_CODES}
    assert found == {("allergy_denied:黄芪", "block")}, found


@pytest.mark.parametrize("record", ["头孢过敏，归类为药疹", "中药归经", "过年归来，否认过敏史", "患者来自枣庄，否认过敏史",
                                    "桂林人，青霉素过敏", "芒果过敏，皮肤呈枣红色斑疹", "姜某，否认过敏史", "无，已归档",
                                    "皮疹归属于药疹", "过敏史归纳：无", "回归正常饮食后无过敏", "2019年归国后出现皮疹"])
def test_no_one_character_reading_is_an_everyday_word(pack, record):
    # the words around a name of one character (allergy._BEFORE_ONE, _AFTER_ONE) were
    # checked pairwise with 姜, 归, 枣, 桂 and 芎 for everyday words: 归类 (classify) was one,
    # and is a word of its own; these are the others a record writes
    assert not _all_herbs_issues(pack, record), record


@pytest.mark.parametrize("make", [lambda n: "黄芪" + " " * n + "过敏", lambda n: "过敏史：" + " " * n + "黄芪",
                                  lambda n: "过敏史：" + "\t" * n + "黄芪", lambda n: "(" + " " * n + "黄芪"],
                         ids=["spaces", "colon_spaces", "colon_tabs", "bracket_spaces"])
def test_a_long_allergy_field_is_read_through_the_intake_in_linear_time(pack, make):
    # C4 through the intake, not only the reading: its label and negative patterns
    # backtracked over a run of whitespace, so 200,000 spaces took minutes before the
    # allergy reading began
    import time
    lines = [Line("黄芪", 9, "practitioner"), Line("人参", 9, "practitioner")]

    def timed(n):
        t0 = time.perf_counter()
        out = check(lines, parse_intake(case(*SPLEEN, allergies=[make(n)]), pack), pack)
        return time.perf_counter() - t0, out

    timed(1000)
    small, _ = timed(20000)
    large, out = timed(200000)
    assert large < 30 and large < 20 * small + 1, (small, large)
    assert ("allergy:黄芪", "stop") in {(i.id, i.severity) for i in out}
    assert all(len(i.message) < 1200 for i in out)


# ------------------------------------------- r7 review round 2 (b989f7d), beyond the corpus
#: A reaction told in the clause after the herb's, past the words a story opens with, after a
#: sentence mark, or after a tolerance (C1). They were read as the herb taken or tolerated.
_STORY_TEMPLATES = ("曾服{}，有过敏反应", "既往服用{}，有过敏", "服用{}，结果出现皮疹", "服用{}，患者出现皮疹",
                    "服用{}，并出现皮疹", "服用{}，第三天出现皮疹", "服用{}，晚上出现皮疹", "服{}，次晨皮疹",
                    "服{}，1h后出现皮疹", "服{}，十余分钟后出现皮疹", "服用{}，服药后出现皮疹", "服用{}，自诉皮疹",
                    "服{}，荨麻疹", "服{}，红疹", "服{}，身痒", "服{}，有皮疹", "{}煲汤，喝后过敏",
                    "用{}泡水，喝了以后起疹子", "Took {}; rash next day", "Took {}. Rash next day",
                    "服用{}后。出现皮疹", "服用{}后；出现皮疹", "吃{}。起疹子", "服用{}后无不适，次日出现皮疹",
                    "服用{}后，自觉无不适，次日出现皮疹", "{}耐受差", "对{}耐受性差", "{}耐受不良")
#: A herb taken with no reaction, with words of the story before the denial, a symptom that got
#: better or that the herb was taken for, and another allergen after the herb's time (C3: at
#: most a block, never a stop).
_TAKEN_TEMPLATES = ("服用{}后，自觉无不适", "曾服{}，次日无不适", "长期服用{}，自觉无不适", "服用{}，3天后无任何不适",
                    "服用{}后，面部无红肿", "服用{}后，皮肤未见皮疹", "{}\t服用中\t无不适", "服用{}后咳嗽缓解",
                    "因咳嗽服用{}", "服用{}后，头晕好转", "过敏性鼻炎，服用{}后喷嚏减少", "过敏性鼻炎（服用{}后好转）",
                    "服用{}后精神好转，青霉素过敏", "服用{}期间，青霉素过敏", "服用{}后，有青霉素过敏史",
                    "服用{}，次日青霉素皮试阳性", "服用{}治疗咳嗽，有效", "{}治疗过敏性鼻炎有效",
                    "曾用{}治疗皮疹，效佳")


def test_a_story_that_goes_on_to_a_reaction_stops_every_pack_herb_and_one_that_does_not_never_does(pack):
    from bioagent.clinic.allergy import allergy_issues
    from bioagent.clinic.prescribe import _kb
    kb = _kb()
    missed = []
    for herb in pack.herbs:
        for t in _STORY_TEMPLATES:
            out = allergy_issues([herb], [t.format(herb)], kb, pack)
            if ("stop", "allergy") not in {(i[1], i[2]) for i in out}:
                missed.append((t.format(herb), [(i[1], i[2]) for i in out]))
        for t in _TAKEN_TEMPLATES:
            out = allergy_issues([herb], [t.format(herb)], kb, pack)
            if not out or any(i[1] == "stop" for i in out):
                missed.append((t.format(herb), [(i[1], i[2]) for i in out]))
    assert not missed, missed[:20]


def test_a_pinyin_or_latin_name_in_an_english_allergen_list_reads_as_in_an_allergy_line(pack):
    # C1: penicillin/huang qi, penicillin and huang qi, codeine ren shen were unconfirmed (a
    # block that said the sentence says nothing of an allergy); a list of common allergens is
    # an allergy line, whatever separates its entries, so each reads as after "Allergy:"
    from bioagent.clinic.allergy import allergy_issues, names_of
    from bioagent.clinic.prescribe import _kb
    kb = _kb()
    differ, checked = [], 0
    for herb in pack.herbs:
        names = names_of(herb, kb=kb, pack=pack)
        for name in names["pinyin"] + names["latin"]:
            want = {(i[1], i[2]) for i in allergy_issues([herb], [f"Allergy: {name}"], kb, pack)}
            for t in ("penicillin/{}", "penicillin and {}", "sulfa or {}", "codeine {}", "{} and penicillin",
                      "PCN/{}", "{}/aspirin", "shellfish {}"):
                checked += 1
                got = {(i[1], i[2]) for i in allergy_issues([herb], [t.format(name)], kb, pack)}
                if got != want:
                    differ.append((herb, t.format(name), sorted(got), sorted(want)))
    assert not differ, differ[:20]
    assert checked > 1000, checked


def test_the_story_and_the_tolerance_reach_the_studio_as_the_reading_gives_them(pack, tmp_path):
    s = assess(case(*SPLEEN, allergies=["曾服人参，有过敏反应"]), tmp_path / "a", pack=pack)
    assert s.status == "blocked" and "allergy:人参" in {i.id for i in s.prescription.issues}
    s = assess(case(*SPLEEN, allergies=["服用人参后，自觉无不适"]), tmp_path / "b", pack=pack)
    found = {(i.id, i.severity) for i in s.prescription.issues if i.code in _ALLERGY_CODES}
    assert ("allergy_denied:人参", "block") in found and not any(v == "stop" for _, v in found), found
    s = assess(case(*SPLEEN, allergies=["无"], conditions=["过敏性鼻炎，服用人参后喷嚏减少"]), tmp_path / "c",
               pack=pack)
    found = {(i.id, i.severity) for i in s.prescription.issues if i.code in _ALLERGY_CODES}
    assert found == {("allergy_taken:人参", "block")}, found


@pytest.mark.parametrize("make,expected", [
    (lambda n: ("服用黄芪，结果出现皮疹，" * n)[:n], {"allergy:黄芪": "stop"}),
    (lambda n: ("曾服黄芪，有" * n)[:n], {"allergy_taken:黄芪": "block"}),
    (lambda n: "服黄芪，" + "次日" * (n // 2) + "皮疹", {"allergy_taken:黄芪": "block"}),
    (lambda n: ("服用黄芪后，自觉无不适。" * n)[:n], {"allergy_denied:黄芪": "block"}),
    (lambda n: ("服用黄芪后，自觉" * n)[:n], {"allergy_taken:黄芪": "block"}),
    (lambda n: ("服用人参后咳嗽缓解，" * n)[:n], {"allergy_taken:人参": "block"}),
    (lambda n: ("人参\t服用中\t无不适\n" * n)[:n], {"allergy_denied:人参": "block"}),
    (lambda n: ("对于术及青霉素过敏，" * n)[:n], {"allergy:白术": "stop"}),
    (lambda n: ("penicillin/huang qi " * n)[:n], {"allergy:黄芪": "stop"}),
    (lambda n: ("Took huang qi, she got " * n)[:n], {"allergy_taken:黄芪": "block"}),
    (lambda n: "吃黄芪" + "。" * n + "皮疹", {"allergy_taken:黄芪": "block"}),
], ids=["story", "story_cut", "time_run", "tolerated_story", "feeling_run", "improved", "row_cells",
        "yushu", "pinyin_list", "english_story", "marks_run"])
def test_a_long_story_is_read_in_linear_time(pack, make, expected):
    # C4: the clause after a herb is read once per place (``_tells``, ``_goes_on``), and each
    # look past its opening words is bounded
    import time
    from bioagent.clinic.allergy import allergy_issues
    from bioagent.clinic.prescribe import _kb
    kb, herbs = _kb(), list(pack.herbs)
    allergy_issues(herbs, ["黄芪"], kb, pack)

    def timed(record):
        t0 = time.perf_counter()
        out = allergy_issues(herbs, [record], kb, pack)
        return time.perf_counter() - t0, out

    small, _ = timed(make(20000))
    large, out = timed(make(200000))
    assert large < 30 and large < 20 * small + 1, (small, large)
    assert all(len(i[3]) < 1200 for i in out), max(len(i[3]) for i in out)
    got = {i[0]: i[1] for i in out}
    assert {k: got.get(k) for k in expected} == expected, sorted(got.items())[:10]


@pytest.mark.parametrize("make", [lambda n: "无，" + "see chart " * (n // 10), lambda n: "无" + "未查" * (n // 2),
                                  lambda n: "否认过敏史，" + "详见病历" * (n // 4)],
                         ids=["see_chart", "weicha", "see_record"])
def test_a_not_asked_remark_after_an_answer_is_read_in_linear_time(pack, make):
    import time

    def timed(n):
        t0 = time.perf_counter()
        it = parse_intake(case(*SPLEEN, allergies=[make(n)]), pack)
        return time.perf_counter() - t0, it

    timed(1000)
    small, _ = timed(20000)
    large, it = timed(200000)
    assert large < 30 and large < 20 * small + 1, (small, large)
    assert it.allergies is not None and "过敏史未询问" not in it.warnings


def test_the_doc_describes_the_scan_and_its_limits():
    doc = Path(__file__).resolve().parents[2] / "docs" / "tcm-clinic.md"
    if not doc.is_file():                       # not checked out next to the harness
        pytest.skip("docs/tcm-clinic.md is not in this tree")
    en, zh = doc.read_text(encoding="utf-8").split("## 中文摘要")
    for phrase in ("黃耆", "traditional characters", "to_simplified", "scanned", "allergy_denied",
                   "淮山", "夜交藤", "allergy_formula", "herb_pinyin", "herb_common_names", "OpenCC",
                   "Radix Astragali", "姜 / 黄芪", "allergy_taken", "clinic_allergy_corpus.json",
                   "Read twice", "One name inside another never stops", "linear", "面部",
                   "never across a line break or a tab", "A not-asked remark", "The story goes on",
                   "penicillin/huang qi"):
        assert phrase in en, phrase
    for phrase in ("黃耆", "繁体字", "to_simplified", "扫描", "allergy_denied", "淮山", "夜交藤",
                   "allergy_formula", "herb_pinyin", "herb_common_names", "OpenCC",
                   "Radix Astragali", "姜 / 黄芪", "allergy_taken", "clinic_allergy_corpus.json",
                   "读两遍", "一名包含另一名本身从不停止", "成正比", "面部", "从不跨换行或制表符",
                   "未询问说明不改变答复", "叙述接续", "penicillin/huang qi"):
        assert phrase in zh, phrase
    assert "no traditional-to-simplified table" not in en and "没有繁简转换表" not in zh
    # the parts scan is gone, and the doc no longer says a part or abbreviation asks
    for gone in ("parts of two characters", "一部分或简写", "abbreviations that drop one character"):
        assert gone not in en and gone not in zh, gone


# ------------------------------------------------------------------ allergies: round 6 fix-up
def _codes(pack, record, herbs):
    from bioagent.clinic.allergy import allergy_issues
    from bioagent.clinic.prescribe import _kb
    out = allergy_issues(list(herbs), [record], _kb(), pack)
    return {(i[4][0], i[1], i[2]) for i in out}, {i[4][0]: i[3] for i in out}


def _draft_issues(pack, allergies, syndrome):
    from bioagent.clinic.prescribe import draft
    rx = draft(parse_intake(case(*SPLEEN, allergies=allergies), pack), syndrome, pack)
    return rx, {i.herbs[0]: (i.severity, i.code, i.message) for i in rx.issues
                if i.code in _ALLERGY_CODES}


@pytest.mark.parametrize("record,syndrome", [
    ("四君子汤过敏", "脾胃气虚证"), ("服用四君子汤后出现皮疹", "脾胃气虚证"),
    ("补中益气丸过敏", "脾虚气陷证"), ("金匮肾气丸过敏", "肾阳虚证"), ("二陈汤过敏", "痰湿证"),
    ("四物汤过敏", "血虚证"), ("六味地黄丸过敏", "肾阴虚证"), ("龙胆泻肝丸过敏", "肝火炽盛证"),
    ("清胃散过敏", "胃热炽盛证"), ("天麻钩藤颗粒过敏", "肝阳上亢证")])
def test_an_allergy_to_a_formula_asks_about_every_herb_of_it(pack, record, syndrome):
    # a formula the pack holds was read through the herb names in its name only: 四君子汤过敏
    # drafted 四君子汤 with no allergy issue, and 六味地黄丸 asked about 熟地黄 alone
    rx, found = _draft_issues(pack, [record], syndrome)
    herbs = [ln.herb for ln in rx.lines]
    assert herbs and set(herbs) <= set(found), (record, rx.formula, herbs, found)
    for herb in herbs:
        severity, code, message = found[herb]
        assert (severity, code) == ("block", "allergy_formula"), (record, herb, found[herb])
        assert "所载方剂" in message and "组成为" in message and f"未必是{herb}" in message


def test_a_formula_allergy_through_the_studio_needs_reasons_before_signing(pack, tmp_path):
    s = assess(case(*SPLEEN, allergies=["四君子汤过敏"]), tmp_path, pack=pack)
    blocks = {i.id for i in s.prescription.issues if i.code == "allergy_formula"}
    assert blocks == {f"allergy_formula:{h}" for h in ("人参", "白术", "茯苓", "炙甘草")}
    with pytest.raises(SignOffError):
        sign(tmp_path, practitioner="张医师", licence="L-1", decision="accept", pack=pack)


def test_a_formula_in_another_product_form_says_its_composition_may_differ(pack):
    found, messages = _codes(pack, "补中益气丸过敏", ["黄芪", "党参"])
    assert found == {("黄芪", "block", "allergy_formula")}, found
    assert "同名而剂型不同，组成未必完全相同" in messages["黄芪"]
    # 四逆散 is not 四逆汤: 散 is never read as another product form of a formula
    from bioagent.clinic.allergy import _vocab
    from bioagent.clinic.prescribe import _kb
    assert "四逆散" not in _vocab(_kb(), pack).formulas


@pytest.mark.parametrize("record", ["姜\n半夏", "姜\n黄芪", "过敏史：\n青霉素\n姜\n黄芪", "姜 黄芩",
                                    "姜\r\n黄连", "姜\t厚朴", "姜　黄芪"])
def test_a_name_is_not_read_across_whitespace_where_names_stand_on_both_sides(pack, record):
    # whitespace was dropped and the scan ran across it: 姜 / 半夏 was 姜半夏, 姜 / 黄芪 was 姜黄 +
    # 黄芪, and the 姜 of an allergy written one per line was lost
    found, messages = _codes(pack, record, ["生姜", "干姜", "法半夏"])
    assert ("生姜", "stop", "allergy") in found, (record, found)
    assert any(h == "干姜" for h, *_ in found), (record, found)
    assert all("姜半夏" not in m for m in messages.values()), messages
    # a name written with spaces between its characters is still read
    assert ("黄芪", "stop", "allergy") in _codes(pack, "北 芪 过 敏", ["黄芪"])[0]


def test_a_ginger_allergy_written_on_its_own_line_blocks_the_draft(pack):
    rx, found = _draft_issues(pack, ["姜\n半夏"], "风寒表虚证")
    assert rx.status == "blocked" and found["生姜"][:2] == ("stop", "allergy"), found


@pytest.mark.parametrize("record,herb,expected", [
    ("黑順片過敏", "附子", "stop"), ("黑順片过敏", "附子", "stop"), ("人銜過敏", "人参", "stop"),
    ("對四君子湯過敏", "人参", "block"), ("天麻鉤藤飲過敏", "天麻", "block"),
    ("当帰過敏", "当归", "stop"), ("芍薬過敏", "白芍", "stop"), ("竜胆過敏", "龙胆", "stop"),
    ("甘艸過敏", "甘草", "stop"), ("蔴黃過敏", "麻黄", "stop")])
def test_traditional_and_japanese_forms_the_materia_fold_leaves_are_folded(pack, record, herb,
                                                                          expected):
    found, _ = _codes(pack, record, [herb])
    assert expected in {sev for h, sev, _ in found if h == herb}, (record, found)


def test_a_prescription_written_in_traditional_characters_or_radicals_is_read(pack):
    assert ("黑順片", "stop", "allergy") in _codes(pack, "附子过敏", ["黑順片"])[0]
    assert ("⻩芪", "stop", "allergy") in _codes(pack, "黄芪过敏", ["⻩芪"])[0]
    rx, found = _draft_issues(pack, ["黑順片過敏"], "肾阳虚证")
    assert rx.status == "blocked" and found["附子"][:2] == ("stop", "allergy"), found


def test_every_traditional_form_opencc_gives_a_scanned_name_is_folded(pack):
    # where OpenCC is installed: every character of every name the scan knows, in its
    # traditional, Hong Kong and Taiwan forms, folds back (順 and 銜 did not). The table in
    # bioagent.clinic.allergy was built with this check; OpenCC is not a dependency.
    opencc = pytest.importorskip("opencc")
    from bioagent.clinic.allergy import _cjk, _vocab, fold
    from bioagent.clinic.prescribe import _kb
    v = _vocab(_kb(), pack)
    chars = {c for n in (*v.names, *v.formulas) for c in n if _cjk(c)}
    missed = []
    for conv in ("s2t", "s2hk", "s2tw"):
        cc = opencc.OpenCC(conv)
        for c in sorted(chars):
            t = cc.convert(c)
            if len(t) == 1 and t != c and t not in chars and fold(t) != c:
                missed.append((conv, c, t))
    assert not missed, missed[:20]


@pytest.mark.parametrize("record,herb", [
    ("allergic to Panax ginseng", "人参"), ("Astragalus membranaceus allergy", "黄芪"),
    ("Astragalus mongholicus", "黄芪"), ("Glycyrrhiza uralensis allergy", "甘草"),
    ("Aconitum carmichaelii", "附子"), ("Zingiber officinale", "生姜"),
    ("Allergy: Cinnamomum cassia", "桂枝"), ("Polygonum multiflorum", "首乌藤"),
    ("allergic to ginger", "生姜"), ("licorice", "甘草"), ("ginseng allergy", "人参"),
    ("cinnamon", "桂枝"), ("dong quai allergy", "当归"), ("astragalus", "黄芪"),
    ("Astragalus", "黄芪")])
def test_a_species_genus_or_english_name_of_the_source_asks(pack, record, herb):
    # the species materia files (MateriaEntry.species_names) were never scanned, nor any
    # English name: allergic to Panax ginseng passed 人参 in silence. A species names a plant,
    # not a drug, so it asks (a block) and names the species it shares
    found, messages = _codes(pack, record, [herb])
    assert found == {(herb, "block", "allergy_source")}, (record, found)
    assert "来源物种含" in messages[herb] and "未必是对" in messages[herb], messages[herb]


def test_a_species_allergy_blocks_the_draft(pack):
    rx, found = _draft_issues(pack, ["Glycyrrhiza uralensis allergy"], "风寒表虚证")
    assert found["炙甘草"][:2] == ("block", "allergy_source"), found


@pytest.mark.parametrize("record,herb", [
    ("Allergy: Huáng Qí", "黄芪"), ("Rén Shēn", "人参"), ("Gān Cǎo allergy", "甘草"),
    ("Fù Zǐ allergy", "附子"), ("dāng guī allergy", "当归"), ("huang2 qi2", "黄芪"),
    ("Huang'qi", "黄芪"), ("Huang–Qi", "黄芪"), ("HUANGQI", "黄芪"),
    # every pack herb's pinyin, not only the knowledge base's 23
    ("huang qin", "黄芩"), ("huang lian", "黄连"), ("dang shen", "党参"), ("jin yin hua", "金银花"),
    ("shan yao", "山药"), ("gou qi zi", "枸杞子"), ("wu wei zi", "五味子"), ("bo he", "薄荷"),
    ("xi xin", "细辛"), ("hong hua", "红花"), ("tao ren", "桃仁"), ("chuan xiong", "川芎"),
    # the older Latin word order, and the Latin of 首乌藤, which only the pack holds
    ("Radix Astragali", "黄芪"), ("Radix Ginseng", "人参"), ("Radix Glycyrrhizae", "甘草"),
    ("Rhizoma Zingiberis Recens", "生姜"), ("Polygoni Multiflori Caulis", "首乌藤"),
    ("Caulis Polygoni Multiflori", "首乌藤")])
def test_pinyin_with_tones_and_latin_in_either_order_stop(pack, record, herb):
    found, _ = _codes(pack, record, [herb])
    assert found == {(herb, "stop", "allergy")}, (record, found)


def test_every_pack_herb_has_its_pinyin_read(pack):
    from bioagent.clinic.allergy import allergy_issues
    from bioagent.clinic.prescribe import _kb
    assert set(pack.herb_pinyin) == set(pack.herbs) and pack.herb_pinyin_source
    for herb, names in pack.herb_pinyin.items():
        for name in names:
            out = allergy_issues([herb], [f"allergic to {name}"], _kb(), pack)
            assert [(i[1], i[2]) for i in out] == [("stop", "allergy")], (herb, name, out)


@pytest.mark.parametrize("record,herb", [
    ("炒夜交藤过敏", "首乌藤"), ("生夜交藤过敏", "首乌藤"), ("制黑顺片过敏", "附子"),
    ("炮黑顺片过敏", "附子"), ("炙松贝过敏", "川贝母")])
def test_a_processing_word_before_a_pack_alias_stops_the_herb(pack, record, herb):
    found, messages = _codes(pack, record, [herb])
    assert found == {(herb, "stop", "allergy")}, (record, found)
    assert "的炮制品" in messages[herb] and "的别名" in messages[herb], messages[herb]


@pytest.mark.parametrize("record,herb", [("熟川军过敏", "大黄"), ("熟薏米过敏", "薏苡仁")])
def test_a_processing_word_before_a_materia_name_asks_like_the_name(pack, record, herb):
    found, _ = _codes(pack, record, [herb])
    assert found == {(herb, "block", "allergy_source")}, (record, found)


@pytest.mark.parametrize("record,herb", [("⻩芪过敏", "黄芪"), ("⻩连过敏", "黄连"), ("⻰胆过敏", "龙胆"),
                                         ("⼈参过敏", "人参")])
def test_look_alike_radicals_are_read_as_the_characters_they_show(pack, record, herb):
    assert _codes(pack, record, [herb])[0] == {(herb, "stop", "allergy")}, record


def test_a_radical_look_alike_blocks_the_draft(pack):
    rx, found = _draft_issues(pack, ["⻩芪过敏"], "脾虚气陷证")
    assert rx.status == "blocked" and found["黄芪"][:2] == ("stop", "allergy"), found


@pytest.mark.parametrize("record,herb", [
    ("法夏过敏", "法半夏"), ("姜夏过敏", "法半夏"), ("清夏过敏", "法半夏"), ("淮山过敏", "山药"),
    ("怀山过敏", "山药"), ("川柏过敏", "黄柏"), ("双钩过敏", "钩藤"), ("交藤过敏", "首乌藤"), ("台乌过敏", "乌药"),
    ("对金银过敏", "金银花"), ("决明过敏", "石决明"), ("天花过敏", "天花粉"), ("太子过敏", "太子参"),
    ("对母草过敏", "益母草"), ("贝母过敏", "浙贝母"), ("贝母过敏", "川贝母")])
def test_an_abbreviation_no_table_lists_is_not_read(pack, record, herb):
    # every part and drop-one abbreviation of every name was scanned for, and read ordinary
    # words (明天 of 明天麻, 淡黄 of 淡黄芩, 金子 of 金铃子); a part was a stop that claimed one
    # drug (决明 of 石决明, 金银 of 金银花). An abbreviation counts only where a table lists it;
    # these are in no table, a documented limit
    assert not _found(pack, record, herb), record


@pytest.mark.parametrize("record", ["无，明天复查过敏原", "青霉素过敏，皮疹伴淡黄色渗液", "云南白药过敏", "大地过敏",
                                    "生子后出现过敏", "香米过敏", "对金银首饰过敏", "瓜子过敏", "黄花鱼过敏",
                                    "白花油过敏", "北风过敏", "荷叶过敏", "粉皮过敏", "每到大冬天皮肤过敏",
                                    "大夏天日晒后皮肤过敏", "黄皮果过敏", "大麦过敏", "皮肤大红", "炒白菜过敏",
                                    "高丽菜过敏"])
def test_an_everyday_word_that_holds_part_of_a_name_is_silent(pack, record):
    assert not [(i.id, i.message) for i in _all_herbs_issues(pack, record)], record


#: The narrative wordings the review counted: 1302 of 1395 records naming a pack herb's own
#: name became blocks that said the record may not mean it.
_NARRATIVE = ("一吃{}就过敏", "因{}过敏住院", "对{}的成分过敏", "{}的过敏反应", "小时候{}过敏", "似乎{}过敏",
              "喝{}水过敏", "{}吃了过敏", "{}禁用", "服用含有{}的中药后出现皮疹", "{}服用后皮疹",
              "听说{}过敏", "我们发现{}过敏", "以前喝过{}，出现皮疹", "{}接触性皮炎", "曾因{}过敏就诊",
              "黄连素、{}过敏")


def test_a_herb_written_in_a_historys_narrative_stops_it(pack):
    from bioagent.clinic.allergy import allergy_issues
    from bioagent.clinic.prescribe import _kb
    kb = _kb()
    for herb in pack.herbs:
        for t in _NARRATIVE:
            record = t.format(herb)
            out = allergy_issues([herb], [record], kb, pack)
            assert [(i[1], i[2]) for i in out] == [("stop", "allergy")], (record, out)


@pytest.mark.parametrize("record", ["非常严重黄芪过敏", "非常怕黄芪", "未成年时对黄芪过敏", "未成年时黄芪过敏",
                                    "无意中服用黄芪后过敏", "没想到吃黄芪也会过敏", "未经医嘱服用黄芪后皮疹",
                                    "无数次服黄芪后皮疹", "非处方购买黄芪服用后皮疹", "无论生熟黄芪均过敏",
                                    "no doubt huangqi allergy",
                                    "no other allergy than 黄芪", "无其他过敏仅黄芪过敏", "黄芪过敏无其他过敏"])
def test_a_word_that_only_starts_with_a_denial_denies_nothing(pack, record):
    assert _found(pack, record, "黄芪") == [("stop", "allergy")], record


@pytest.mark.parametrize("record,herbs", [
    ("天麻钩藤颗粒过敏", ("天麻", "钩藤")), ("桂枝茯苓丸过敏", ("桂枝", "茯苓")),
    ("麻黄附子细辛汤过敏", ("麻黄", "附子", "细辛")), ("人参归脾丸过敏", ("人参",)),
    ("半夏厚朴汤过敏", ("法半夏", "厚朴")), ("当归芍药散过敏", ("当归", "白芍")),
    ("丹参川芎嗪注射液过敏", ("丹参", "川芎")), ("黄连阿胶汤过敏", ("黄连",)),
    ("芍药甘草汤过敏", ("白芍", "甘草")), ("柴胡桂枝汤过敏", ("柴胡", "桂枝")),
    ("黄芪桂枝五物汤过敏", ("黄芪", "桂枝")), ("茯苓桂枝白术甘草汤过敏", ("茯苓", "桂枝", "白术", "甘草"))])
def test_a_patent_medicine_named_by_its_herbs_asks_about_each_and_stops_none(pack, record,
                                                                            herbs):
    # every herb name but the last stood as a word, because the next one started there:
    # 桂枝茯苓丸 stopped 桂枝 and asked about 茯苓
    found, _ = _codes(pack, record, herbs)
    assert {h for h, *_ in found} == set(herbs), (record, found)
    assert all(sev == "block" for _, sev, _ in found), (record, found)


def test_a_product_word_after_a_name_asks_and_never_stops(pack):
    # 口服 is a word a history writes after a name (北芪口服), but 口服液 is a product: 人参口服液
    # stopped 人参
    from bioagent.clinic.allergy import allergy_issues
    from bioagent.clinic.prescribe import _kb
    kb = _kb()
    for herb in pack.herbs:
        for word in ("口服液", "注射液", "颗粒", "胶囊", "糖浆", "软胶囊", "滴丸"):
            record = f"{herb}{word}过敏"
            out = allergy_issues([herb], [record], kb, pack)
            assert out and all(i[1] == "block" for i in out), (record, out)


def test_names_written_together_still_stand_together(pack):
    assert _codes(pack, "北芪人参过敏", ["黄芪", "人参"])[0] == {("黄芪", "stop", "allergy"),
                                                           ("人参", "stop", "allergy")}
    found, messages = _codes(pack, "对牛奶蛋白及鸡蛋过敏", ["白及", "鸡内金"])
    assert ("鸡内金", "block", "allergy_source") in found          # 及 joins 鸡蛋 to the list
    assert "记录中的“牛奶蛋白及”" in messages["白及"], messages


@pytest.mark.parametrize("record,herbs", [
    ("黄芪无过敏史", ("黄芪",)), ("对黄芪无过敏反应", ("黄芪",)), ("曾服用黄芪未见过敏", ("黄芪",)),
    ("黄芪：未见过敏", ("黄芪",)), ("长期服用黄芪无不良反应", ("黄芪",)), ("服黄芪无不适", ("黄芪",)),
    ("黄芪过敏-否", ("黄芪",)), ("黄芪过敏？否", ("黄芪",)), ("已排除黄芪过敏", ("黄芪",)),
    ("怀疑黄芪过敏，已排除", ("黄芪",)), ("曾怀疑人参过敏，后证实非过敏", ("人参",)),
    ("否认中药（黄芪、人参）过敏", ("黄芪", "人参")), ("既往服用黄芪、党参等未见过敏", ("黄芪", "党参")),
    ("黄芪耐受良好", ("黄芪",)), ("tolerates 黄芪", ("黄芪",)), ("黄芪可用", ("黄芪",)),
    ("黄芪已用过无事", ("黄芪",)), ("曾服补中益气汤（含黄芪、人参）无不适", ("黄芪", "人参"))])
def test_a_herb_the_history_says_is_tolerated_asks_and_never_stops(pack, record, herbs):
    found, _ = _codes(pack, record, herbs)
    assert {h for h, *_ in found} == set(herbs), (record, found)
    assert all(sev == "block" for _, sev, _ in found), (record, found)
    assert {code for *_, code in found} == {"allergy_denied"}, found


@pytest.mark.parametrize("record,herbs", [
    ("无过敏史，目前在吃人参", ("人参",)), ("否认药物过敏史，平素服黄芪泡水", ("黄芪",)),
    ("无过敏史；正在服用黄芪", ("黄芪",)), ("无过敏史，喜食生姜", ("生姜",)), ("无，爱吃大枣", ("大枣",)),
    ("否认过敏史，常食山药", ("山药",)), ("无过敏史，喜欢喝菊花茶", ("菊花",)),
    ("无过敏史，每周炖汤加党参、黄芪", ("党参", "黄芪")), ("否认过敏史。现用药：人参、黄芪泡水", ("人参", "黄芪")),
    ("NKDA, currently on huangqi", ("黄芪",)), ("无过敏史（曾服四君子汤，效佳）", ("人参", "白术", "茯苓", "炙甘草")),
    ("无，在服金匮肾气丸", ("附子", "桂枝")), ("No allergies (uses licorice root tea)", ("甘草",)),
    ("NKDA. Takes ginseng daily.", ("人参",)), ("无。患者自行服用黄芪颗粒半年", ("黄芪",))])
def test_a_herb_the_history_says_is_taken_asks_and_never_stops(pack, record, herbs):
    # a record that the herb is taken or eaten (目前在吃人参, currently on huangqi) is no
    # allergy: it gave a stop that read as one, or a formula block that said the allergy may
    # be to any herb of it. It asks, and says what the record says
    found, messages = _codes(pack, record, herbs)
    assert found == {(h, "block", "allergy_taken") for h in herbs}, (record, found)
    for h in herbs:
        assert "服用" in messages[h] and "不是过敏记录" in messages[h], messages[h]
        assert "所记过敏可能由" not in messages[h], messages[h]
    # nothing in such a record stops any herb of the pack
    assert not [i.id for i in _all_herbs_issues(pack, record) if i.severity == "stop"], record


def test_a_reaction_while_using_a_herb_is_no_tolerance(pack):
    assert _found(pack, "长期服用人参后出现皮疹", "人参") == [("stop", "allergy")]
    assert _found(pack, "正在服用黄芪，出现皮疹", "黄芪") == [("stop", "allergy")]
    assert _found(pack, "以前喝过黄芪，出现皮疹", "黄芪") == [("stop", "allergy")]
    # the 过敏 of the next clause is 青霉素's: 人参 is taken
    assert _found(pack, "目前在吃人参，青霉素过敏", "人参") == [("block", "allergy_taken")]


@pytest.mark.parametrize("record", ["黄芪——皮疹", "Hx: huangqi -> hives", "黄芪 -", "黄芪-皮疹",
                                    "黄芪—皮疹", "黄芪→皮疹"])
def test_a_dash_or_an_arrow_after_a_name_is_no_denial(pack, record):
    assert _found(pack, record, "黄芪") == [("stop", "allergy")], record
    for denied in ("黄芪（-）", "黄芪：-", "黄芪(-)"):
        assert _found(pack, denied, "黄芪") == [("block", "allergy_denied")], denied


@pytest.mark.parametrize("record", ["否认药物、食物及花粉过敏史", "否认花粉过敏", "无花粉过敏", "花粉：无",
                                    "无药物、食物、花粉过敏"])
def test_a_denied_pollen_allergy_asks_nothing(pack, record):
    assert not _allergy_issues(pack, record, "天花粉"), record


@pytest.mark.parametrize("record", ["桂花过敏", "桂鱼过敏", "鳜鱼过敏", "桂圆过敏", "胡萝卜过敏"])
def test_an_everyday_food_that_holds_a_names_characters_is_silent(pack, record):
    # 桂花 is osmanthus, not 桂 (肉桂) and so not 桂枝; 胡萝卜 the carrot, not the 萝卜 the
    # materia table files under 莱菔子
    assert not [(i.id, i.message) for i in _all_herbs_issues(pack, record)], record


def test_a_name_no_table_holds_is_not_read_from_a_longer_one(pack):
    # 桂圆 (longan) is in no table; the materia table holds 桂圆肉 under 龙眼肉. No part of a
    # name is scanned for, so 桂圆过敏 does not reach 龙眼肉 (a documented limit), and 桂圆 is
    # an everyday word that never reaches 桂枝
    assert not _codes(pack, "桂圆过敏", ["龙眼肉", "桂枝"])[0]
    assert ("龙眼肉", "block", "allergy_source") in _codes(pack, "桂圆肉过敏", ["龙眼肉"])[0]


@pytest.mark.parametrize("record,herb", [("没药物过敏史", "没药"), ("患者没药物过敏", "没药"),
                                         ("无食物过敏", "无食子")])
def test_a_denial_that_starts_like_a_name_is_the_denial(pack, record, herb):
    assert not _codes(pack, record, [herb])[0], record
    assert _codes(pack, f"{herb}过敏", [herb])[0] == {(herb, "stop", "allergy")}
    assert _codes(pack, "没药人参过敏", ["没药", "人参"])[0] == {("没药", "stop", "allergy"),
                                                           ("人参", "stop", "allergy")}


@pytest.mark.parametrize("record", ["北️芪过敏", "北\U000e0100芪过敏", "北͏芪过敏", "北ㅤ芪过敏",
                                    "北ᅟ芪过敏", "北⠀芪过敏"])
def test_marks_that_show_nothing_do_not_split_a_name(pack, record):
    assert _found(pack, record, "黄芪") == [("stop", "allergy")], repr(record)


def test_an_allergy_recorded_among_the_conditions_is_read(pack):
    it = parse_intake(case(*SPLEEN, allergies=[], conditions=["黄芪过敏", "高血压"]), pack)
    found = {i.id: i.severity for i in check([Line("黄芪", 9, "practitioner")], it, pack)
             if i.code in _ALLERGY_CODES}
    assert found == {"allergy:黄芪": "stop"}
    it = parse_intake(case(*SPLEEN, allergies=[], conditions=["过敏性鼻炎"]), pack)
    assert not [i for i in check([Line("黄芪", 9, "practitioner")], it, pack)
                if i.code in _ALLERGY_CODES]


@pytest.mark.parametrize("field", ["过敏史：", "Allergies:", "药物过敏史：  食物过敏史：", "过敏史：____",
                                   "过敏史：（ ）", "过敏原：", "过敏史：待询问", "未问", "待查", "TBD",
                                   "unknown", "null", "患者昏迷，未能询问", "不详"])
def test_an_allergy_field_with_only_a_label_or_unknown_reads_as_not_asked(pack, field):
    it = parse_intake(case(*SPLEEN, allergies=[field]), pack)
    assert it.allergies is None and "过敏史未询问" in it.warnings, field
    # what it holds is still read: a name written next to "not asked" is not lost
    it = parse_intake(case(*SPLEEN, allergies=["过敏史不详 人参过敏"]), pack)
    assert it.allergies and "过敏史未询问" not in it.warnings
    assert ("stop", "allergy") in [(i.severity, i.code)
                                   for i in check([Line("人参", 9, "practitioner")], it, pack)]


@pytest.mark.parametrize("field", ["过敏史：（-）", "过敏史：-", "无", "NKDA", "N/A", "否认"])
def test_a_labelled_none_is_an_answer(pack, field):
    it = parse_intake(case(*SPLEEN, allergies=[field]), pack)
    assert it.allergies is not None and "过敏史未询问" not in it.warnings, field


def test_a_long_record_is_quoted_as_written(pack):
    record = ("Patient reports an allergic reaction to Astragali Radix in 2019, presenting as "
              "rash and itching over both arms")
    _, messages = _codes(pack, record, ["黄芪"])
    assert "reaction to Astragali Radix in 2019" in messages["黄芪"], messages


@pytest.mark.parametrize("record,herb,expected", [
    ("炙草过敏", "炙甘草", ("block", "allergy_source")),
    # 於 folds to 于: 对 + 于术 + 过敏, and the knowledge base gives 于术 to 白术
    ("对於朮過敏", "白术", ("stop", "allergy"))])
def test_short_and_traditional_names_are_never_silent(pack, record, herb, expected):
    assert _found(pack, record, herb) == [expected], record


def test_a_long_history_of_distinct_words_is_read_in_linear_time(pack):
    # every mention had its own message, and messages were de-duplicated by a list scan:
    # 200k characters took 27 times as long as 20k
    import itertools
    import time
    from bioagent.clinic.allergy import allergy_issues
    from bioagent.clinic.prescribe import _kb
    kb = _kb()
    pool = "珂琪瑶璐琳瑾瑜璇璟瑗琰琨珺玥玮珏"

    def record(n):
        words = ("".join(w[:3]) + "地黄" + w[3] for w in itertools.product(pool, repeat=4))
        return "，".join(itertools.islice(words, n))

    allergy_issues(["熟地黄"], ["地黄"], kb, pack)

    def timed(n):
        t0 = time.perf_counter()
        out = allergy_issues(["熟地黄", "生地黄"], [record(n)], kb, pack)
        return time.perf_counter() - t0, out

    small, _ = timed(2500)
    large, out = timed(25000)
    assert {i[2] for i in out} == {"allergy_near_name"} and "另有" in out[0][3]
    assert large < 20 and large < 20 * small + 1, (small, large)


@pytest.mark.parametrize("medication,herb,klass,atc", [
    ("呋塞米 20mg qd", "炙甘草", "利尿剂", "C03CA01"),
    ("氢氯噻嗪片 25mg", "甘草", "利尿剂", "C03AA03"),
    ("泼尼松 10mg", "甘草", "糖皮质激素", "H02AB07"),
    ("Coumadin 3 mg", "当归", "华法林", "B01AA03"),
    ("华法林钠片 3mg", "当归", "华法林", None),          # the class word itself, as before
    ("苯乙肼 15mg tid", "麻黄", "单胺氧化酶抑制剂", "N06AF03"),
    ("氨氯地平 5mg", "麻黄", "降压药", "C08CA01"),
    ("Norvasc 5 mg", "麻黄", "降压药", "C08CA01"),
    # a thiazide is a diuretic and an antihypertensive (DB-04): 麻黄 with it warned of nothing
    ("氢氯噻嗪片 25mg", "麻黄", "降压药", "C03AA03"),
    ("吲达帕胺 2.5mg", "麻黄", "降压药", "C03BA11"),
    ("吲达帕胺 2.5mg", "甘草", "利尿剂", "C03BA11"),
])
def test_a_medicine_named_specifically_is_read_as_its_atc_class(pack, medication, herb, klass,
                                                               atc):
    # 甘草 with a loop diuretic (hypokalaemia) is the case the rule was written for, but the
    # rule's words are classes (利尿剂, diuretic): a record of 呋塞米 matched none of them
    it = parse_intake(case(*SPLEEN, meds=[medication]), pack)
    hits = [i for i in check([Line(herb, 6, "practitioner")], it, pack) if i.code == "interaction"]
    assert len(hits) == 1, (medication, herb)
    assert hits[0].severity == "warn" and klass in hits[0].message
    if atc:
        assert f"ATC {atc}" in hits[0].message and "WHO" in hits[0].source


def test_a_medicine_outside_the_mapping_or_its_rule_raises_no_interaction(pack):
    for medication, herb in (("螺内酯 20mg", "甘草"),      # potassium-sparing: not the rule's risk
                             ("阿莫西林", "甘草"), ("呋塞米", "麻黄"), ("美托洛尔", "麻黄")):
        it = parse_intake(case(*SPLEEN, meds=[medication]), pack)
        assert not [i for i in check([Line(herb, 6, "practitioner")], it, pack)
                    if i.code == "interaction"], (medication, herb)


def test_a_thiazide_is_read_as_both_its_classes(pack):
    it = parse_intake(case(*SPLEEN, meds=["氢氯噻嗪 25mg qd"]), pack)
    hits = {i.id: i.message for i in check([Line("甘草", 6, "practitioner"),
                                            Line("麻黄", 6, "practitioner")], it, pack)
            if i.code == "interaction"}
    assert "（利尿剂，ATC C03AA03）" in hits["interaction:甘草:氢氯噻嗪"]
    assert "（降压药，ATC C03AA03）" in hits["interaction:麻黄:氢氯噻嗪"]


def test_every_mapped_medicine_names_its_rule_classes_and_their_atc_groups(pack, tmp_path):
    assert pack.drug_classes, "the pack maps specific medicines to its rules' classes"
    rule_words = {d for r in pack.drug_interactions for d in r["drugs"]}
    for name, (classes, atc) in pack.drug_classes.items():
        assert classes and set(classes) <= rule_words, (name, classes)
        assert re.fullmatch(r"[A-Z]\d\d[A-Z][A-Z]\d\d", atc), (name, atc)
        groups = {k for k, gs in pack.atc_groups.items() if any(atc.startswith(g) for g in gs)}
        assert groups == set(classes), (name, atc, classes, groups)
    assert pack.drug_classes["氢氯噻嗪"][0] == ("利尿剂", "降压药")
    assert pack.drug_classes["呋塞米"][0] == ("利尿剂",)
    original = json.loads(Path(pack.path).read_text(encoding="utf-8"))

    def refused(change, match):
        data = json.loads(json.dumps(original))
        change(data["drug_classes"]["drugs"])
        bad = tmp_path / "pack.json"
        bad.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        with pytest.raises(P.PackError, match=match):
            P.load_pack(bad)

    # a beta blocker (C07): no class lists C07, so it cannot be mapped to one
    refused(lambda drugs: drugs.append({"atc": "C07AB02", "class": "降压药",
                                        "names": ["美托洛尔"]}), "C07AB02")
    # a medicine is read as every class whose groups hold its code, and only those
    def one(atc, classes):
        return lambda drugs: next(d for d in drugs if d["atc"] == atc).update(
            {"class": classes})
    refused(one("C03AA03", "利尿剂"), "C03AA03")                  # 氢氯噻嗪 as a diuretic alone
    refused(one("C03CA01", ["利尿剂", "降压药"]), "C03CA01")      # 呋塞米 as an antihypertensive
    refused(one("C03CA01", []), "no class")


def test_a_recorded_medication_the_rules_do_not_recognise_is_reported(pack):
    # "no interaction" is told from "not recognised": 美托洛尔 and 螺内酯 matched nothing and
    # the result looked like a clean check
    meds = ["美托洛尔 25mg bid", "螺内酯 20mg", "氨氯地平 5mg", "阿司匹林肠溶片 100mg", "华法林"]
    it = parse_intake(case(*SPLEEN, meds=meds), pack)
    found = [i for i in check([Line("麻黄", 6, "practitioner")], it, pack)
             if i.code == "medications_unrecognised"]
    assert [i.severity for i in found] == ["info"]
    msg = found[0].message
    assert "美托洛尔25mgbid" in msg and "螺内酯20mg" in msg, msg     # as recorded (normalised)
    assert "氨氯地平" not in msg and "阿司匹林" not in msg and "华法林" not in msg, msg
    assert "未核查" in msg
    # recognised and checked, with nothing found: no notice
    for recorded in (["呋塞米 20mg"], [], None):
        it = parse_intake(case(*SPLEEN, meds=recorded), pack)
        assert not [i for i in check([Line("麻黄", 6, "practitioner")], it, pack)
                    if i.code in ("medications_unrecognised", "interaction")], recorded


@pytest.mark.parametrize("recorded", [["无"], ["否认"], ["none"], ["None"], ["无特殊用药"],
                                      ["未服药"], ["无，否认"], ["否认长期用药史"], ["暂无"],
                                      ["no medications"], ["无", "呋塞米 20mg"]])
def test_a_record_of_no_medication_is_not_an_unrecognised_medicine(pack, recorded):
    # the template asks for [] when there are none, but 无 is how it is usually written:
    # it was listed as "未识别的用药：无"
    it = parse_intake(case(*SPLEEN, meds=recorded), pack)
    assert not [i for i in check([Line("麻黄", 6, "practitioner")], it, pack)
                if i.code == "medications_unrecognised"], recorded


def test_a_record_that_is_not_a_none_is_still_reported(pack):
    it = parse_intake(case(*SPLEEN, meds=["无", "美托洛尔", "不详"]), pack)
    found = [i for i in check([Line("麻黄", 6, "practitioner")], it, pack)
             if i.code == "medications_unrecognised"]
    assert len(found) == 1 and "美托洛尔" in found[0].message and "不详" in found[0].message
    assert "：无" not in found[0].message and "、无" not in found[0].message


def test_assess_blocks_the_draft_for_an_allergy_under_another_name(pack, tmp_path):
    sinking = (["脘腹重坠", "久泻", "脱肛", "内脏下垂", "神疲乏力", "少气懒言"], ["舌淡，苔白"],
               ["脉缓弱"])
    for allergy in ("黄芪", "黄耆", "北芪"):
        s = assess(case(*sinking, allergies=[allergy]), tmp_path / allergy, pack=pack)
        assert "黄芪" in {ln.herb for ln in s.prescription.lines}
        assert s.status == "blocked" and "allergy:黄芪" in {i.id for i in s.prescription.issues}


# ----------------------------------------------------------------- sign-off
def test_a_draft_is_signed_only_with_reasons_and_verifies(pack, tmp_path):
    out = tmp_path / "s"
    s = assess(case(*STASIS, pregnant=None), out, pack=pack)
    needed = {i.id for i in s.prescription.issues if i.severity == "block"}
    with pytest.raises(SignOffError, match="needs the practitioner's reason"):
        sign(out, practitioner="张医师", licence="L-1", decision="accept", pack=pack)
    with pytest.raises(SignOffError, match="licence"):
        sign(out, practitioner="张医师", licence=" ", decision="accept", pack=pack)
    rec = sign(out, practitioner="张医师", licence="L-1", decision="accept", pack=pack,
               reasons={k: "已确认未孕（尿hCG阴性）" for k in needed})
    assert rec["final_prescription"]["formula"] == "血府逐瘀汤"
    assert set(rec["acknowledged"]) == needed
    ok, problems = verify(out)
    assert ok, problems
    assert "张医师" in (out / "prescription_signed.md").read_text(encoding="utf-8")
    with pytest.raises(SignOffError, match="already signed"):
        sign(out, practitioner="张医师", licence="L-1", decision="reject", note="x", pack=pack)
    doc = json.loads((out / "session.json").read_text(encoding="utf-8"))
    doc["status"] = "edited"
    (out / "session.json").write_text(json.dumps(doc), encoding="utf-8")
    ok, problems = verify(out)
    assert not ok and any("since it was signed" in p for p in problems)


def test_a_blocked_draft_is_modified_or_rejected_never_accepted(pack, tmp_path):
    out = tmp_path / "s"
    s = assess(case(*SPLEEN, allergies=["人参"]), out, pack=pack)
    assert s.status == "blocked"
    with pytest.raises(SignOffError, match="modified or rejected"):
        sign(out, practitioner="王医师", licence="L-2", decision="accept", pack=pack)
    with pytest.raises(SignOffError, match="records why"):
        sign(out, practitioner="王医师", licence="L-2", decision="reject", pack=pack)
    rec = sign(out, practitioner="王医师", licence="L-2", decision="modify", pack=pack,
               modifications=[{"replace": "人参", "with": "黄芪", "dose": 15}])
    herbs = {x["herb"]: x["dose_g"] for x in rec["final_prescription"]["lines"]}
    assert "人参" not in herbs and herbs["黄芪"] == 15
    # 人参's 另煎兑服 note must not survive 人参 being replaced
    text = "\n".join(rec["final_prescription"]["instructions"])
    assert "人参" not in text and "另煎" not in text
    assert "人参" not in (out / "prescription_signed.md").read_text(encoding="utf-8")


def test_a_syndrome_with_no_base_formula_says_so_and_offers_the_check(pack, tmp_path):
    s = assess(case(["发热重", "咽喉肿痛", "口微渴", "恶寒轻"], ["舌边尖红", "苔薄黄"],
                    ["脉浮数"], sex="male", pregnant=None), tmp_path, pack=pack)
    assert s.status == "no_formula"
    assert s.differentiation.top.syndrome == "风热表证"
    (issue,) = s.prescription.issues
    assert issue.code == "no_formula" and "辛凉解表" in issue.message
    md = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "## 下一步" in md and "clinic check" in md
    assert "## 签署" not in md                       # nothing to sign
    for decision in ("accept", "modify"):
        with pytest.raises(SignOffError, match="nothing to sign or modify"):
            sign(tmp_path, practitioner="赵医师", licence="L-4", decision=decision, pack=pack,
                 modifications=[{"add": "金银花", "dose": 15}] if decision == "modify" else ())
    rec = sign(tmp_path, practitioner="赵医师", licence="L-4", decision="reject", pack=pack,
               note="本方由医师另拟")
    assert rec["decision"] == "reject" and rec["final_prescription"] is None


def test_a_referral_cannot_be_signed_into_a_prescription(pack, tmp_path):
    s = assess(case(*SPLEEN, vitals={"spo2": 85}), tmp_path, pack=pack)
    assert s.status == "refer"
    with pytest.raises(SignOffError, match="referral"):
        sign(tmp_path, practitioner="李医师", licence="L-3", decision="modify", pack=pack,
             modifications=[{"add": "黄芪", "dose": 15}])


# ---------------------------------------------------------------- follow-up
def test_follow_up_scores_the_change_and_stops_on_harm(pack):
    base = parse_visit({"date": "2026-10-01", "scores": {"神疲乏力": 3, "食少": "中", "便溏": 2}})
    now = parse_visit({"date": "2026-10-15", "scores": {"神疲乏力": 1, "食少": "轻", "便溏": 0}})
    r = assess_followup(base, now, pack, syndrome="脾胃气虚证")
    assert r.baseline_total == 14 and r.current_total == 4      # main findings count double
    assert r.reduction == pytest.approx(0.714, abs=0.001) and r.category == "显效"
    harm = parse_visit({"date": "2026-10-08", "scores": {"神疲乏力": 3, "食少": 2, "便溏": 2},
                        "adverse_events": [{"event": "心悸", "severity": "moderate",
                                            "relation": "possible"}],
                        "new_findings": ["胸痛"]})
    r = assess_followup(base, harm, pack, syndrome="脾胃气虚证")
    assert r.stop and r.refer and r.category == "无效"
    with pytest.raises(ValueError, match="0-3"):
        parse_visit({"date": "2026-10-01", "scores": {"x": 5}})


# --------------------------------------------------------------- agreement
def test_agreement_needs_labels_and_says_when_cases_are_synthetic(pack):
    assert cohen_kappa(["a", "b", "a", "b"], ["a", "b", "a", "b"]) == 1.0
    assert cohen_kappa(["a", "a", "b", "b"], ["a", "b", "a", "b"]) == 0.0
    lo, hi = wilson(8, 10)
    assert lo < 0.8 < hi
    cases = [{"id": 1, "synthetic": True, "intake": case(*SPLEEN),
              "expert": {"syndrome": "脾胃气虚证", "labelled_by": "test"}},
             {"id": 2, "synthetic": True, "intake": case(*PHLEGM),
              "expert": {"syndrome": "痰湿证", "labelled_by": "test"},
              "second_expert": {"syndrome": "痰湿证", "labelled_by": "test2"}},
             {"id": 3, "synthetic": True, "intake": case(["神疲乏力"], [], []),
              "expert": {"syndrome": "气虚证", "labelled_by": "test"}}]
    with pytest.raises(ValueError, match="synthetic"):
        agreement(cases, pack)
    with pytest.raises(ValueError, match="no expert label"):
        agreement([{"id": 9, "intake": case(*SPLEEN)}], pack)
    rep = agreement(cases, pack, allow_synthetic=True)
    assert rep.n == 3 and rep.top1 == 2 and rep.abstained == 1
    assert rep.inter_rater_n == 1
    assert rep.markdown().startswith("> **SYNTHETIC CASES")


# --------------------------------------------------------------- CLI
def test_the_command_line_runs_a_consultation(pack, tmp_path, capsys):
    from bioagent.cli import main
    intake = tmp_path / "intake.json"
    intake.write_text(json.dumps(case(*SPLEEN), ensure_ascii=False), encoding="utf-8")
    assert main(["clinic", "template", "--out", str(tmp_path / "blank.json")]) == 0
    assert json.loads((tmp_path / "blank.json").read_text(encoding="utf-8"))["_form"]
    assert main(["clinic", "assess", str(intake), "--out", str(tmp_path / "s")]) == 0
    out = capsys.readouterr().out
    assert "status: draft" in out and "四君子汤" in out
    assert main(["clinic", "verify", str(tmp_path / "s")]) == 0
    assert main(["clinic", "check", str(intake), "--herbs", "附子:6,法半夏:9"]) == 0
    assert "incompatible:法半夏+附子" in capsys.readouterr().out
    assert main(["clinic", "sign", str(tmp_path / "s"), "--practitioner", "张医师",
                 "--licence", "L-1", "--decision", "accept"]) == 0
    page = (tmp_path / "s" / "report.html").read_text(encoding="utf-8")
    assert '<html lang="zh-CN">' in page and "未经医师签署不是处方" in page
    visits = tmp_path / "v.json"
    visits.write_text(json.dumps({
        "syndrome": "脾胃气虚证",
        "baseline": {"date": "2026-10-01", "scores": {"神疲乏力": 3, "食少": 2}},
        "current": {"date": "2026-10-15", "scores": {"神疲乏力": 1, "食少": 1}}}), encoding="utf-8")
    assert main(["clinic", "followup", str(visits)]) == 0
    # both are main findings of 脾胃气虚证, so each counts double: 10 → 4, a 60% reduction
    assert "score 10 → 4, reduction 60% (有效)" in capsys.readouterr().out
    cases = tmp_path / "c.json"
    cases.write_text(json.dumps({"cases": [
        {"id": 1, "synthetic": True, "intake": case(*SPLEEN),
         "expert": {"syndrome": "脾胃气虚证", "labelled_by": "合成"}}]},
        ensure_ascii=False), encoding="utf-8")
    assert main(["clinic", "agreement", str(cases)]) == 1          # synthetic, not allowed
    assert main(["clinic", "agreement", str(cases), "--allow-synthetic",
                 "--out", str(tmp_path / "eval")]) == 0
    assert "SYNTHETIC CASES" in (tmp_path / "eval" / "agreement.md").read_text(encoding="utf-8")


# --------------------------------------------------------------- the skill
CANDIDATES = Path(__file__).resolve().parents[1] / "skills" / "candidates" / "clinic"


def test_the_skill_drafts_under_governance_and_claims_only_the_textbook(tmp_path):
    from bioagent.contracts import check_claim
    from bioagent.governed import GovernedRunRefused, run_governed

    intake = tmp_path / "intake.json"
    intake.write_text(json.dumps(case(*SPLEEN), ensure_ascii=False), encoding="utf-8")
    args = {"intake": str(intake), "out_dir": str(tmp_path / "run")}
    with pytest.raises(GovernedRunRefused, match="no lockfile pins skill"):
        run_governed("draft-tcm-prescription", args, skill_dir=CANDIDATES,
                     state_dir=tmp_path / "psh0")
    run = run_governed("draft-tcm-prescription", args, skill_dir=CANDIDATES,
                       state_dir=tmp_path / "psh", output_dir=tmp_path / "out",
                       allow_unpinned=True)
    art = run.artifact
    (claim,) = art.claims
    assert claim.claim_kind == "traditional_use" and claim.object == "四君子汤"
    assert check_claim(claim, {e.id: e for e in art.evidence}).allowed
    assert all(e.has_quote_receipt and e.design == "expert_consensus" for e in art.evidence)
    assert run.verdict.publishable, run.verdict.codes
    assert not run.released
    assert "not a prescription" in " ".join(art.limitations)


def test_the_doc_states_the_packs_real_counts(pack):
    doc = (Path(__file__).resolve().parents[2] / "docs" / "tcm-clinic.md")
    if not doc.is_file():                       # not checked out next to the harness
        pytest.skip("docs/tcm-clinic.md is not in this tree")
    text = doc.read_text(encoding="utf-8")
    for what, n in (("Syndromes", len(pack.syndromes)), ("Formulas", len(pack.formulas)),
                    ("Herbs", len(pack.herbs))):
        row = next(ln for ln in text.splitlines() if ln.startswith(f"| {what}"))
        assert f"| {n} |" in row, row
    assert pack.version in text and "not reviewed" in text
    medicines = len({atc for _, atc in pack.drug_classes.values()})
    assert f"{medicines} named medicines" in text and f"{medicines} 种药" in text
