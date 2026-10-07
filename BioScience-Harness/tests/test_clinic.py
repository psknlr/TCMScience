"""TCM syndrome differentiation and prescription support (bioagent.clinic)."""

from __future__ import annotations

import json
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
