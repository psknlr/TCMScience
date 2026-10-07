"""A citation must name a usable record, whatever the sentence around it claims.

The output gate verified support only for clinical sentences, and skipped every other
sentence whole. A mechanism, a hypothesis or a classical attribution that cited a retracted
record, the record of another paper, or no record at all therefore went out unexamined
(found by the governance ablation, 2026-10). Support is still checked only where a claim
about patients is made; the citation itself is now checked everywhere.
"""

from __future__ import annotations

import dataclasses

import pytest

from psh.contracts import RunEnvelope, VerificationFailed
from psh.evidence import EvidenceRecord
from psh.evidence.record import RetractionStatus, SourceType, revalidate_trust
from psh.evidence.signing import EvidenceSigner
from psh.kernel.output_gate import OutputGate

SIGNER = EvidenceSigner(key=b"c" * 32)
TEXT = ("Molecular docking predicted that baicalin may bind the catalytic site of PTP1B, a "
        "hypothesis for experimental testing.")
SENTENCE = "Docking suggests that baicalin may bind PTP1B (doi:10.5555/gate.1)."


def record(identifier="10.5555/gate.1", retracted=False) -> EvidenceRecord:
    return SIGNER.sign(EvidenceRecord.from_text(
        identifier=identifier, text=TEXT, retrieved_by="fixture", retrieval_run="r",
        retracted=retracted, source_type=SourceType.DOI))


def check(text, sources, **kw):
    return OutputGate(**kw).check(text, RunEnvelope(), sources=sources)


def test_a_non_clinical_sentence_is_not_a_claim_about_patients():
    assert not OutputGate.is_clinical(SENTENCE)


def test_a_valid_citation_in_a_non_clinical_sentence_is_released():
    assert check(SENTENCE, {"doi:10.5555/gate.1": record()}).allowed


@pytest.mark.parametrize("sources, why", [
    ({}, "no record was supplied"),
    ({"doi:10.5555/gate.1": record(retracted=True)}, "retracted"),
    ({"doi:10.5555/gate.1": record("10.5555/gate.2")}, "is the record of"),
])
def test_a_citation_that_cannot_stand_is_refused(sources, why):
    with pytest.raises(VerificationFailed, match=why):
        check(SENTENCE, sources)


def test_a_tampered_record_is_refused_once_its_signature_is_rechecked():
    edited = dataclasses.replace(record(retracted=True),
                                 retraction=RetractionStatus.NOT_RETRACTED)
    # As supplied, the edit is invisible to the gate: it reads the fields.
    assert check(SENTENCE, {"doi:10.5555/gate.1": edited}).allowed
    rechecked = revalidate_trust(edited, SIGNER)
    with pytest.raises(VerificationFailed, match="changed after it was signed"):
        check(SENTENCE, {"doi:10.5555/gate.1": rechecked})


def test_drafting_mode_records_the_problem_and_releases():
    verdict = check(SENTENCE, {}, require_support=False)
    assert verdict.allowed


def test_a_sentence_without_a_citation_is_unaffected():
    assert check("Docking suggests that baicalin may bind PTP1B.", {}).allowed


@pytest.mark.parametrize("sentence", [
    "服用含何首乌的制剂可能与成人药物性肝损伤有关。",
    "该制剂的毒性尚需评估，可能与剂量有关。",
])
def test_a_safety_or_association_sentence_in_chinese_is_a_claim_about_patients(sentence):
    """有关 is the counterpart of 相关, and adverse-event words of 不良反应: before, a
    Chinese safety signal was read as making no claim, and was not checked at all."""
    assert OutputGate.is_clinical(sentence)


# ------------------------------------------- mechanism sentences (ablation, round 2)

def test_a_mechanism_sentence_about_another_agent_is_refused():
    """A docking sentence about berberine passed on its overlap with a baicalin run."""
    with pytest.raises(VerificationFailed, match="about 'berberine'"):
        check("Docking suggests that berberine may bind PTP1B (doi:10.5555/gate.1).",
              {"doi:10.5555/gate.1": record()})


def test_a_mechanism_sentence_stronger_than_its_record_is_refused():
    with pytest.raises(VerificationFailed, match="does not support it as written"):
        check("Baicalin always binds PTP1B (doi:10.5555/gate.1).",
              {"doi:10.5555/gate.1": record()})


def test_a_methods_sentence_names_no_mechanism_and_is_not_held_to_support():
    assert check("Docking was run with AutoDock Vina (doi:10.5555/gate.1).",
                 {"doi:10.5555/gate.1": record()}).allowed


# ------------------------------------------------- passage citations and quotations

PASSAGE = "《伤寒论》第34条：太阳病，桂枝证，医反下之，利遂不止；喘而汗出者，葛根黄芩黄连汤主之。"
PATTERN = r"\b(passage\.[a-z0-9_]+)\b"


def passage() -> EvidenceRecord:
    return SIGNER.sign(EvidenceRecord.from_text(
        identifier="passage.shl_34", text=PASSAGE, retrieved_by="fixture",
        retrieval_run="r", retracted=False, source_type=SourceType.LOCAL_ARTIFACT))


def test_a_passage_citation_is_a_citation_the_deployment_names():
    sentence = "《伤寒论》第34条记载：喘而汗出者，葛根黄芩黄连汤主之（passage.shl_99）。"
    assert check(sentence, {}).allowed                          # unrecognised, unchecked
    with pytest.raises(VerificationFailed, match="no record was supplied"):
        check(sentence, {}, citation_patterns=(PATTERN,))


def test_quoted_words_must_be_in_the_passage_cited():
    sources = {"passage.shl_34": passage()}
    good = "《伤寒论》第34条记载：喘而汗出者，葛根黄芩黄连汤主之（passage.shl_34）。"
    assert check(good, sources, citation_patterns=(PATTERN,)).allowed
    with pytest.raises(VerificationFailed, match="words it quotes are not in the cited record"):
        check(good.replace("葛根黄芩黄连汤", "桂枝汤"), sources, citation_patterns=(PATTERN,))


def test_a_citation_pattern_must_capture_one_identifier():
    with pytest.raises(ValueError, match="exactly one group"):
        OutputGate(citation_patterns=(r"passage\.[a-z]+",))


def test_the_kernel_takes_its_citation_shapes_from_its_config(tmp_path):
    from psh import PSHConfig, TrustedKernel
    kernel = TrustedKernel(PSHConfig(state_dir=tmp_path, citation_patterns=(PATTERN,)))
    try:
        assert kernel.output_gate._identifiers("见（passage.shl_34）") == ["passage.shl_34"]
    finally:
        kernel.close()
