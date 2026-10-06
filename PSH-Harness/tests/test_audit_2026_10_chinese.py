"""Chinese claim support, after the October 2026 audit (AUD-16).

The default support verifier tokenised English words only, so a Chinese claim identical to
its source was ``unknown`` with confidence 0 while the same sentence in English was
supported at 0.95. Supporting Chinese lexically is not enough on its own: the structured
scope check (subject, population, outcome) also read English only, so once Chinese claims
could be supported, a trial in adults would have supported the same claim about children.
Both now read Chinese.
"""

from __future__ import annotations

import pytest

from psh.config import PSHConfig
from psh.contracts import Autonomy, RiskTier
from psh.evidence import ClaimSupportVerifier, EvidenceRecord
from psh.evidence.claims import LicensedScope, ScientificClaim
from psh.evidence.signing import EvidenceSigner
from psh.kernel import TrustedKernel
from psh.labels import Destination
from psh.policy import PolicySnapshot
from psh.runtime import Runner

CLAIM = "黄芪注射液可降低慢性心力衰竭患者的再住院率。"
ENGLISH = "Astragalus injection reduced readmission in patients with chronic heart failure."
TRIAL = ("一项纳入120例成人慢性心力衰竭患者的随机双盲安慰剂对照试验显示，"
         "黄芪注射液降低了再住院率。")


def _verify(claim: str, source: str):
    return ClaimSupportVerifier().verify(statement=claim, identifier="x", source_text=source)


def test_a_chinese_claim_is_supported_by_the_same_sentence_as_an_english_one_is():
    """The audit's case: identical Chinese claim and source were unknown, confidence 0."""
    chinese, english = _verify(CLAIM, CLAIM), _verify(ENGLISH, ENGLISH)
    assert chinese.relationship.value == english.relationship.value == "support"
    assert chinese.confidence == english.confidence


@pytest.mark.parametrize("source", [
    "黄芪注射液未能降低慢性心力衰竭患者的再住院率。",
    "黄芪注射液对慢性心力衰竭患者的再住院率无显著影响。",
    "两组慢性心力衰竭患者的再住院率差异无统计学意义。",
])
def test_a_chinese_negated_finding_contradicts_the_claim(source):
    assert _verify(CLAIM, source).relationship.value == "contradict"


@pytest.mark.parametrize("source", [
    "黄芪注射液降低了慢性心力衰竭患者的再住院率，未见明显不良反应。",
    "无论是否合并糖尿病，黄芪注射液均可降低慢性心力衰竭患者的再住院率。",
])
def test_a_safety_note_or_a_concessive_clause_is_not_a_negated_finding(source):
    assert _verify(CLAIM, source).relationship.value == "support"


def test_a_chinese_figure_must_be_the_sources_figure():
    """``\\w`` matches Chinese, so 降低了30% read as no number at all."""
    source = "黄芪注射液使再住院率降低了30%。"
    assert _verify("黄芪注射液使再住院率降低了30%。", source).relationship.value == "support"
    wrong = _verify("黄芪注射液使再住院率降低了45%。", source)
    assert wrong.relationship.value == "unknown" and "45%" in wrong.rationale


def test_a_chinese_claim_stronger_than_its_source_is_only_partly_supported():
    claim = "黄芪注射液可治愈慢性心力衰竭。"
    assert _verify(claim, CLAIM).relationship.value == "partial"
    # 尚未证实 ("not yet shown") is not 证实 ("shown").
    assert _verify("黄芪注射液的疗效尚未证实。", "黄芪注射液的疗效尚未证实。") \
        .claim_certainty.value == "uncertain"


def test_an_unrelated_chinese_source_supports_nothing():
    assert _verify("丹参滴丸可改善冠心病患者的心绞痛症状。", CLAIM).relationship.value \
        == "unknown"


# ------------------------------------------------------- the scope check reads Chinese

def test_a_chinese_claim_has_a_subject_a_direction_an_outcome_and_a_population():
    claim = ScientificClaim.parse("与对照组相比，黄芪注射液显著降低了儿童患者的死亡率。")
    assert claim.is_clinical_claim
    assert (claim.subject, claim.direction.value, claim.outcome) == ("黄芪注射液", "decrease",
                                                                    "mortality")
    assert claim.population.descriptors == ("paediatric",)
    assert ScientificClaim.parse("黄芪注射液未能降低再住院率。").direction.value == "no_effect"
    assert ScientificClaim.parse("研究显示黄芪与心衰预后相关。").subject == "黄芪"
    assert not ScientificClaim.parse("患者的再住院率降低。").subject   # an outcome, not an agent


def test_a_chinese_source_states_its_scope():
    scope = LicensedScope.from_text(TRIAL)
    assert scope.design == "randomised-trial" and scope.sample_size == 120
    assert scope.subjects == ("黄芪注射液",) and scope.outcomes == ("hospitalisation",)
    assert "heart-failure" in scope.population.descriptors


@pytest.mark.parametrize("claim, finding", [
    ("黄芪注射液可降低儿童慢性心力衰竭患者的再住院率。", "population_extrapolation"),
    ("黄芪注射液可降低孕妇慢性心力衰竭患者的再住院率。", "population_extrapolation"),
    ("丹参注射液可降低慢性心力衰竭患者的再住院率。", "subject_mismatch"),
    ("黄芪注射液可降低慢性心力衰竭患者的死亡率。", "outcome_mismatch"),
])
def test_a_trial_in_adults_does_not_support_a_chinese_claim_beyond_it(claim, finding):
    record = EvidenceSigner(key=b"k" * 32).sign(EvidenceRecord.from_text(
        identifier="12345678", text=TRIAL, retrieved_by="pubmed", retrieval_run="r",
        retracted=False))
    verifier = ClaimSupportVerifier()
    assert verifier.verify_record(statement=CLAIM, record=record).supports
    refused = verifier.verify_record(statement=claim, record=record)
    assert not refused.supports and finding in refused.rationale


# ------------------------------------------------- end to end: a Chinese conclusion released

@pytest.mark.parametrize("conclusion, released", [
    ("黄芪注射液可降低慢性心力衰竭患者的再住院率（PMID: 12345678）。", True),
    ("黄芪注射液可降低儿童慢性心力衰竭患者的再住院率（PMID: 12345678）。", False),
    ("黄芪注射液可治愈慢性心力衰竭（PMID: 12345678）。", False),
    ("黄芪注射液可降低慢性心力衰竭患者的再住院率。", False),               # no citation
])
def test_a_runner_releases_a_supported_chinese_conclusion_and_nothing_beyond_it(
        tmp_path, local_model, conclusion, released):
    policy = PolicySnapshot(profile_id="zh", autonomy=Autonomy.ACT,
                            risk_ceiling=RiskTier.R3_CLINICAL, require_claim_support=True,
                            allowed_destinations=(Destination.LOCAL_COMPUTE,
                                                  Destination.LOCAL_MODEL,
                                                  Destination.USER_OUTPUT,
                                                  Destination.PERSISTENT))
    kernel = TrustedKernel(PSHConfig(state_dir=tmp_path / "k").ensure_dirs(), policy=policy)
    record = kernel.evidence_signer.sign(EvidenceRecord.from_text(
        identifier="12345678", text=TRIAL, retrieved_by="sable.pubmed_fetch",
        retrieval_run="r0", retracted=False))
    result = Runner(kernel, model=local_model, model_invoke=lambda prompt: conclusion).run(
        "黄芪注射液能否降低慢性心衰患者的再住院率？", sources={"12345678": record})
    assert (result.status == "ok") is released, result.error
    if released:
        assert result.released_output == conclusion
    kernel.close()
