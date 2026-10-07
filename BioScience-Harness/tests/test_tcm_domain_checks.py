"""What a TCM finding is about, beyond the subject's name: level, processing, exposure, count.

A claim can name the drug its evidence studied and still not be about what was studied. A
trial of 葛根芩连汤 says nothing of 黄连 alone; a study of 制附子 says nothing of the raw root
生附子, which the resolver rightly folds into the same drug; a cell assay at 100 µM says nothing
of what patients reach; and the same trial reached through two databases is one trial.
CLM015 to CLM018 refuse each, and the governance ablation carries an operator for each.
"""

from __future__ import annotations

import pytest

from bioagent.contracts import CandidateClaim, EvidenceItem, check_claim
from bioagent.contracts.claim_language import overreaching_language
from bioagent.sources.materia import names_in, processing_of

TRIAL = ("方法：一项纳入360名成人2型糖尿病患者的随机双盲安慰剂对照试验。结论：葛根芩连汤可降低成人2型"
         "糖尿病患者的糖化血红蛋白。")
FUZI = "结论：制附子1.5g/日可增加成人慢性心力衰竭患者的6分钟步行距离。"
ASSAY = "In vitro, compound A inhibited target T1 in a reporter assay; compound A may inhibit T1."


def item(eid="e1", *, design="randomized_trial", subject="葛根芩连汤", text=TRIAL,
         identifier="10.5555/domain.1", population="成人2型糖尿病患者",
         outcome="糖化血红蛋白") -> EvidenceItem:
    return EvidenceItem(id=eid, design=design, quote=text[:40], source_card_id="fixture",
                        identifier=identifier, identifier_type="doi", subject=subject,
                        population=population, outcome=outcome,
                        retracted="not_retracted").located_in(text)


def claim(text, *, subject="葛根芩连汤", kind="efficacy", supports=("e1",),
          population="成人2型糖尿病患者", outcome="糖化血红蛋白", basis="one trial"):
    return CandidateClaim(id="c1", text=text, claim_kind=kind, subject=subject,
                          supports=supports, asserted_population=population,
                          supported_population=population, asserted_outcome=outcome,
                          supported_outcome=outcome, confidence=0.5, confidence_basis=basis,
                          falsified_by="a larger null trial")


def codes(c, items) -> tuple[str, ...]:
    return check_claim(c, {i.id: i for i in items}).codes


def test_the_faithful_claim_raises_none_of_the_domain_codes():
    found = codes(claim("葛根芩连汤可降低成人2型糖尿病患者的糖化血红蛋白。"), [item()])
    assert not {"CLM015", "CLM016", "CLM017", "CLM018"} & set(found)


# --------------------------------------------------------------------- CLM015

def test_a_constituent_is_not_credited_with_the_formulas_trial():
    found = codes(claim("黄连可降低成人2型糖尿病患者的糖化血红蛋白。", subject="黄连"), [item()])
    assert "CLM015" in found


def test_a_formula_is_not_claimed_from_one_compounds_assay():
    c = claim("In vitro, Gegen Qinlian decoction may inhibit target T1.", kind="mechanism",
              subject="Gegen Qinlian decoction", population="", outcome="")
    evidence = item(design="in_vitro", subject="compound A", text=ASSAY, population="",
                    outcome="")
    assert "CLM015" in codes(c, [evidence])


def test_a_mechanism_hypothesis_may_be_drawn_from_a_constituent():
    """Network pharmacology hypothesises a formula's mechanism from its constituents, and
    the kind says it is a hypothesis; CLM015 is for kinds that state what acts."""
    c = claim("Gegen Qinlian decoction may act on target T1.", kind="mechanism_hypothesis",
              subject="Gegen Qinlian decoction", population="", outcome="")
    evidence = item(design="in_vitro", subject="compound A", text=ASSAY, population="",
                    outcome="")
    assert "CLM015" not in codes(c, [evidence])


# --------------------------------------------------------------------- CLM016

@pytest.mark.parametrize("text, expected", [
    ("制附子", "制"), ("生附子1.5g/日", "生"), ("炙甘草", "炙"), ("姜半夏", "姜制"),
    ("熟大黄", "熟"), ("附子", ""),
    ("服用后未发生附子相关不良反应", ""),      # 生 belongs to 发生
])
def test_a_processing_state_is_read_where_it_is_written(text, expected):
    (mention,) = names_in(text)
    assert processing_of(mention, text) == expected


def fuzi_claim(text, subject):
    return claim(text, subject=subject, population="成人慢性心力衰竭患者",
                 outcome="6分钟步行距离")


def fuzi_item():
    return item(subject="制附子", text=FUZI, population="成人慢性心力衰竭患者",
                outcome="6分钟步行距离")


def test_the_raw_root_is_not_claimed_from_evidence_on_the_processed_one():
    c = fuzi_claim("生附子1.5g/日可增加成人慢性心力衰竭患者的6分钟步行距离。", "生附子")
    verdict = check_claim(c, {"e1": fuzi_item()})
    assert "CLM016" in verdict.codes
    assert any("'生'" in r and "制" in r for r in verdict.reason_text)


def test_an_unstated_processing_state_is_not_a_different_one():
    c = fuzi_claim("附子1.5g/日可增加成人慢性心力衰竭患者的6分钟步行距离。", "附子")
    assert "CLM016" not in codes(c, [fuzi_item()])


# --------------------------------------------------------------------- CLM017

def mechanism(text):
    return claim(text, kind="mechanism", subject="compound A", population="", outcome="")


def assay():
    return item(design="in_vitro", subject="compound A", text=ASSAY, population="",
                outcome="")


@pytest.mark.parametrize("text", [
    "In vitro, compound A may inhibit target T1 at concentrations reached in patients.",
    "Compound A may inhibit target T1 at clinically relevant concentrations.",
    "化合物A在临床相关浓度下可能抑制靶点T1。",
])
def test_human_exposure_is_not_asserted_from_a_bench_study(text):
    assert "CLM017" in codes(mechanism(text), [assay()])


def test_a_bench_finding_stated_at_its_own_concentration_is_fine():
    assert "CLM017" not in codes(mechanism("In vitro, compound A may inhibit target T1."),
                                 [assay()])


# --------------------------------------------------------------------- CLM018

def test_one_trial_reached_twice_is_not_two_independent_trials():
    twice = [item("e1"), item("e2")]                    # the same DOI, two databases
    c = claim("两项研究显示葛根芩连汤可降低成人2型糖尿病患者的糖化血红蛋白。", supports=("e1", "e2"))
    verdict = check_claim(c, {i.id: i for i in twice})
    assert "CLM018" in verdict.codes
    assert any("1 source(s)" in r for r in verdict.reason_text)


def test_two_different_trials_may_be_counted_as_two():
    both = [item("e1"), item("e2", identifier="10.5555/domain.2")]
    c = claim("Two independent trials found 葛根芩连汤 lowered HbA1c in adults.",
              supports=("e1", "e2"))
    assert "CLM018" not in codes(c, both)


def test_the_count_is_read_from_the_confidence_basis_too():
    twice = [item("e1"), item("e2")]
    c = claim("葛根芩连汤可降低成人2型糖尿病患者的糖化血红蛋白。", supports=("e1", "e2"),
              basis="replicated in 2 studies")
    assert "CLM018" in codes(c, twice)


# ------------------------------------------------------------ absolute wording

@pytest.mark.parametrize("text", ["Compound A always inhibits T1.", "化合物A必然抑制T1。",
                                  "化合物A在任何情况下都抑制T1。"])
def test_absolute_wording_is_licensed_by_no_kind(text):
    for kind in ("mechanism", "efficacy", "mechanism_hypothesis"):
        assert any(f.family == "absolute" for f in overreaching_language(text, kind))


def test_a_denied_absolute_is_a_calibrated_statement():
    assert not overreaching_language("Compound A does not always inhibit T1.", "mechanism")
