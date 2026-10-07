"""Scope that the claim parser could not see, closed (governance ablation, round 2).

Each test starts from a sentence the full stack released although its source did not reach
it: an all-cause mortality claim over a trial of cardiovascular death, a claim about children
over a cohort of "adults aged 65 or older", and Chinese association sentences whose subject
the parser never found, so their population and outcome were never compared.
"""

from __future__ import annotations

import pytest

from psh.evidence.claims import (
    EffectDirection, LicensedScope, ScientificClaim, extract_population,
)
from psh.evidence.scope import check_scope

EMPEROR = (
    "Empagliflozin reduced the combined risk of cardiovascular death or hospitalization for "
    "heart failure in patients with heart failure and a preserved ejection fraction. The "
    "primary outcome occurred in 415 of 2997 patients in the empagliflozin group and in 511 "
    "of 2991 patients in the placebo group (hazard ratio, 0.79; 95% confidence interval, "
    "0.69 to 0.90; P<0.001).")
COHORT = ("In a prospective cohort of 4,812 adults aged 65 or older followed for 8 years, "
          "higher serum C-telopeptide was associated with all-cause mortality.")
ARISTOLOCHIC = ("一项纳入2,000名成人慢性肾脏病患者的前瞻性队列研究随访5年，长期服用含马兜铃酸的中药与"
                "成人慢性肾脏病患者终末期肾病风险升高相关（风险比1.8）。")
HESHOUWU = ("病例报告：一名52岁成人女性在服用含何首乌的制剂4周后出现药物性肝损伤，停药后恢复。"
            "结论：服用含何首乌的制剂可能与成人药物性肝损伤有关。")


def verdict(claim: str, source: str):
    parsed = ScientificClaim.parse(claim)
    assert parsed.is_clinical_claim, f"never checked: {claim}"
    return check_scope(parsed, LicensedScope.from_text(source))


@pytest.mark.parametrize("claim, source", [
    ("Empagliflozin reduced cardiovascular death or hospitalization for heart failure in "
     "patients with heart failure and a preserved ejection fraction.", EMPEROR),
    ("Higher serum C-telopeptide was associated with all-cause mortality in adults aged 65 "
     "or older.", COHORT),
    ("长期服用含马兜铃酸的中药与成人慢性肾脏病患者终末期肾病风险升高相关。", ARISTOLOCHIC),
    ("服用含何首乌的制剂可能与成人药物性肝损伤有关。", HESHOUWU),
])
def test_the_faithful_sentence_is_licensed(claim, source):
    assert not verdict(claim, source).blocked


@pytest.mark.parametrize("claim, source, why", [
    ("Empagliflozin reduced all-cause mortality in patients with heart failure and a "
     "preserved ejection fraction.", EMPEROR, "outcome_mismatch"),
    ("Higher serum C-telopeptide was associated with all-cause mortality in children.",
     COHORT, "population_extrapolation"),
    ("长期服用含马兜铃酸的中药与儿童慢性肾脏病患者终末期肾病风险升高相关。", ARISTOLOCHIC,
     "population_extrapolation"),
    ("长期服用含马兜铃酸的中药与成人慢性肾脏病患者全因死亡率升高相关。", ARISTOLOCHIC,
     "outcome_mismatch"),
    ("服用含何首乌的制剂可能与儿童药物性肝损伤有关。", HESHOUWU, "population_extrapolation"),
])
def test_the_mutated_sentence_is_blocked(claim, source, why):
    found = verdict(claim, source)
    assert found.blocked and why in found.describe()


def test_cardiovascular_death_is_a_cardiovascular_event_not_mortality():
    scope = LicensedScope.from_text(EMPEROR)
    assert "mortality" not in scope.outcomes
    assert {"cardiovascular-event", "hospitalisation"} <= set(scope.outcomes)
    assert "mortality" in LicensedScope.from_text("All-cause mortality fell.").outcomes


@pytest.mark.parametrize("text", ["adults aged 65 or older", "patients 70 years and older",
                                  "participants ≥65 years", "65岁及以上的患者"])
def test_older_age_phrases_are_a_population(text):
    assert "elderly" in extract_population(text).axes.get("age", ())


def test_an_adult_age_floor_is_not_read_as_older_adults():
    assert "age" not in extract_population("adults aged 18 or older").axes


@pytest.mark.parametrize("sentence, subject", [
    ("长期服用含马兜铃酸的中药与终末期肾病风险升高相关", "含马兜铃酸的中药"),
    ("结论：服用含何首乌的制剂可能与药物性肝损伤有关", "含何首乌的制剂"),
])
def test_the_exposure_is_the_subject_of_a_chinese_association(sentence, subject):
    parsed = ScientificClaim.parse(sentence)
    assert parsed.subject == subject
    assert parsed.direction in (EffectDirection.ASSOCIATION, EffectDirection.INCREASE)
