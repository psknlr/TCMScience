"""The release gates, after the October 2026 audit (AUD-10 to AUD-12).

AUD-10  a plan in the same sentence exempted an efficacy claim from the output gate;
AUD-11  a signed record's retraction status could be edited without breaking the signature;
AUD-12  a record of one paper could be supplied, verified and displayed as another.

Each test starts from the audit's own example and runs the path the audit ran.
"""

from __future__ import annotations

import dataclasses

import pytest

from psh.config import PSHConfig
from psh.contracts import Autonomy, PolicyDenied, RiskTier, VerificationFailed
from psh.evidence import ClaimSupportVerifier, EvidenceRecord
from psh.evidence.claims import LicensedScope, ScientificClaim
from psh.evidence.clauses import asserted, clauses
from psh.evidence.record import (RetractionStatus, SourceType, canonical_identifier,
                                 cited_source, revalidate_trust)
from psh.evidence.signing import UNSIGNED_FIELDS, EvidenceSigner
from psh.kernel import TrustedKernel
from psh.kernel.output_gate import OutputGate
from psh.labels import Destination, Labeled
from psh.policy import PolicySnapshot
from psh.runtime import Runner
from psh.runtime.finalize import ingest_evidence

LOCAL_ONLY = (Destination.LOCAL_COMPUTE, Destination.LOCAL_MODEL, Destination.USER_OUTPUT,
              Destination.PERSISTENT)
ABSTRACT = (
    "Empagliflozin reduced the combined risk of cardiovascular death or hospitalization for "
    "heart failure in patients with heart failure and a preserved ejection fraction. The "
    "primary outcome occurred in 415 of 2997 patients in the empagliflozin group and in 511 "
    "of 2991 patients in the placebo group (hazard ratio, 0.79; 95% confidence interval, "
    "0.69 to 0.90; P<0.001).")
FINDING = ("empagliflozin reduced the combined risk of cardiovascular death or hospitalization "
           "for heart failure in {who} with heart failure and a preserved ejection fraction")


def _kernel(tmp_path, **kw) -> TrustedKernel:
    params = dict(profile_id="audit", allowed_destinations=LOCAL_ONLY, autonomy=Autonomy.ACT,
                  risk_ceiling=RiskTier.R3_CLINICAL, require_claim_support=True)
    params.update(kw)
    return TrustedKernel(PSHConfig(state_dir=tmp_path / "k").ensure_dirs(),
                         policy=PolicySnapshot(**params))


def _record(identifier="34449189", text=ABSTRACT, **kw) -> EvidenceRecord:
    return EvidenceRecord.from_text(identifier=identifier, text=text,
                                    retrieved_by="sable.pubmed_fetch", retrieval_run="r0",
                                    retracted=kw.pop("retracted", False), **kw)


# ============================================ AUD-10: a plan exempts only its clause

@pytest.mark.parametrize("sentence, clinical", [
    ("黄芪能治愈肺癌。", True),
    ("黄芪能治愈肺癌，未来研究将优化剂量。", True),           # the audit's case
    ("黄芪能治愈肺癌未来研究将优化剂量。", True),             # no comma between them
    ("Astragalus cures lung cancer; future studies will optimise the dose.", True),
    ("Astragalus cures lung cancer and future studies will optimise the dose.", True),
    ("黄芪能降低死亡率，但机制尚未明确。", True),             # the open question is the mechanism
    ("未来研究将评估黄芪对生存率的影响。", False),
    ("本研究拟评价黄芪的疗效。", False),
    ("黄芪是否降低死亡率尚未证实。", False),
    ("Future studies will test whether astragalus reduces mortality.", False),
    ("We plan to test whether astragalus improves survival.", False),
    ("Whether astragalus reduces mortality has not been studied.", False),
    ("Further research is needed to determine whether astragalus improves survival.", False),
    ("Future studies will test whether the 1,000 mg dose reduces mortality.", False),
])
def test_a_plan_or_an_open_question_exempts_only_its_own_clause(sentence, clinical):
    assert OutputGate().is_clinical(sentence) is clinical


def test_a_sentence_without_a_plan_is_judged_as_before():
    """Only the exempt clauses are blanked; the rest is read as one sentence."""
    sentence = "HR = 0.71, 95% CI 0.6-0.8, in adults treated with the drug."
    assert asserted(sentence) == sentence
    assert clauses("黄芪能治愈肺癌，未来研究将优化剂量") == ["黄芪能治愈肺癌", "未来研究将优化剂量"]


@pytest.mark.parametrize("text, released", [
    ("黄芪能治愈肺癌。", False),
    ("黄芪能治愈肺癌，未来研究将优化剂量。", False),
    ("Astragalus cures lung cancer; future studies will optimise the dose.", False),
    ("未来研究将优化黄芪的剂量。", True),
])
def test_the_runner_refuses_a_claim_written_with_a_plan(tmp_path, local_model, text, released):
    """The audit's path: a Runner whose policy requires claim support. The plain claim was
    refused and the same claim followed by a plan was released."""
    kernel = _kernel(tmp_path)
    result = Runner(kernel, model=local_model, model_invoke=lambda prompt: text).run("q")
    assert (result.status == "ok") is released, result.error
    if not released:
        assert result.released_output is None
    kernel.close()


def test_a_reporting_construction_does_not_skip_the_scope_check():
    """"This study reports that …" introduces a finding. The claim parser exempted it as a
    non-claim, so a trial in adults supported the same finding in children at 0.95."""
    record = EvidenceSigner(key=b"k" * 32).sign(_record())
    verifier = ClaimSupportVerifier()
    for lead in ("", "This study reports that ", "The results were that ",
                 "Table 2 shows that "):
        children = verifier.verify_record(statement=lead + FINDING.format(who="children"),
                                          record=record)
        adults = verifier.verify_record(statement=lead + FINDING.format(who="patients"),
                                        record=record)
        assert not children.supports, lead
        assert adults.supports, lead


def test_a_plan_contributes_nothing_to_the_claims_structure():
    """A claim about adults followed by a plan for children is a claim about adults."""
    statement = FINDING.format(who="patients") + "; future studies will test children."
    parsed = ScientificClaim.parse(statement)
    assert parsed.is_clinical_claim and "child" not in parsed.population.describe().lower()
    record = EvidenceSigner(key=b"k" * 32).sign(_record())
    assert ClaimSupportVerifier().verify_record(statement=statement, record=record).supports
    assert not ScientificClaim.parse("Future studies will test whether drug A reduces "
                                     "mortality in children.").is_clinical_claim


def test_a_sources_open_question_gives_it_no_direction():
    scope = LicensedScope.from_text("Further research is needed to determine whether drug "
                                    "A reduces mortality in adults with sepsis.")
    assert scope.direction.value == "unknown" and not scope.subjects


# ================================================ AUD-11: every field is signed

def test_an_edited_retraction_status_breaks_the_signature():
    """The audit's case: a signed record marked retracted, edited to not retracted
    without re-signing. The signature still verified and the source was accepted."""
    signer = EvidenceSigner(key=b"k" * 32)
    signed = signer.sign(_record(retracted=True))
    assert not signed.usable[0]
    edited = dataclasses.replace(signed, retraction=RetractionStatus.NOT_RETRACTED)
    assert not signer.verify(edited)
    rechecked = revalidate_trust(edited, signer)
    assert rechecked.tampered and not rechecked.trusted
    usable, why = rechecked.usable
    assert not usable and "changed after it was signed" in why


@pytest.mark.parametrize("change", [
    {"retraction": RetractionStatus.NOT_RETRACTED},
    {"quality": "high"},
    {"publication_status": "peer_reviewed"},
    {"licensed_scope": LicensedScope.from_text("A trial in children with heart failure.")},
    {"identifier": "38291045"},
    {"source_type": SourceType.DOI},
    {"source_uri": "https://example.org/other"},
    {"title": "another paper"},
    {"retrieved_at": 0.0},
    {"evidence_id": "evr_other"},
])
def test_no_field_a_decision_reads_can_change_under_a_signature(change):
    signer = EvidenceSigner(key=b"k" * 32)
    signed = signer.sign(_record(retracted=None))
    assert signer.verify(signed)
    assert not signer.verify(dataclasses.replace(signed, **change))


def test_a_new_field_is_signed_without_being_listed():
    """Only the signature, the flags derived from it and the hashed content are left out."""
    assert UNSIGNED_FIELDS == {"signature", "trusted", "tampered", "content"}
    message = EvidenceSigner._message(_record()).decode()
    for f in dataclasses.fields(EvidenceRecord):
        assert (f"\n{f.name}=" in message) is (f.name not in UNSIGNED_FIELDS), f.name


def test_an_unsigned_record_is_untrusted_text_not_a_tampered_one():
    signer = EvidenceSigner(key=b"k" * 32)
    plain = revalidate_trust(_record(), signer)
    assert not plain.trusted and not plain.tampered and plain.usable[0]
    tampered = revalidate_trust(dataclasses.replace(signer.sign(_record()), title="x"),
                                signer)
    assert tampered.tampered
    resigned = signer.sign(tampered)                   # a fresh signature clears the flag
    assert resigned.trusted and not resigned.tampered and signer.verify(resigned)


def test_the_runner_refuses_an_edited_record(tmp_path, local_model):
    kernel = _kernel(tmp_path)
    signed = kernel.evidence_signer.sign(_record(retracted=True))
    edited = dataclasses.replace(signed, retraction=RetractionStatus.NOT_RETRACTED)
    text = "Empagliflozin may reduce heart-failure hospitalization (PMID: 34449189)."
    runner = Runner(kernel, model=local_model, model_invoke=lambda prompt: text)
    assert runner.run("q", sources={"34449189": kernel.evidence_signer.sign(
        _record())}).status == "ok"
    refused = runner.run("q", sources={"34449189": edited})
    assert refused.status == "refused" and refused.released_output is None
    kernel.close()


# ================================= AUD-12: a citation names the record it is checked on

def test_identifiers_compare_as_identifiers():
    assert canonical_identifier("PMID: 34449189") == canonical_identifier("34449189") \
        == canonical_identifier("pmid:34449189") == ("pmid", "34449189")
    assert canonical_identifier("https://doi.org/10.1056/NEJMoa2107038") == \
        canonical_identifier("doi:10.1056/nejmoa2107038")
    assert canonical_identifier("nct04157751")[0] == "nct"
    assert canonical_identifier("99999999") != canonical_identifier("PMID:34449189")


def test_a_record_supplied_under_another_identifier_is_refused(tmp_path, local_model):
    """The audit's case: a record of PMID 34449189 under the key 99999999 was verified as
    itself and the output displayed 99999999."""
    kernel = _kernel(tmp_path)
    signed = kernel.evidence_signer.sign(_record(identifier="PMID:34449189"))

    class Envelope:
        run_id = "r1"

    with pytest.raises(PolicyDenied, match="is supplied as the record of"):
        ingest_evidence(kernel, {"99999999": signed}, Envelope)
    text = "Empagliflozin may reduce heart-failure hospitalization (PMID: 99999999)."
    result = Runner(kernel, model=local_model, model_invoke=lambda prompt: text).run(
        "q", sources={"99999999": signed})
    assert result.status == "refused" and result.released_output is None
    assert result.refused_at == "ingest_evidence"
    kernel.close()


def test_the_gate_checks_the_record_behind_a_citation(tmp_path):
    """The output gate does the same for a caller that hands it sources directly."""
    kernel = _kernel(tmp_path)
    signed = kernel.evidence_signer.sign(_record(identifier="PMID:34449189"))
    envelope = kernel.policy.envelope()
    text = "Empagliflozin may reduce heart-failure hospitalization (PMID: 99999999)."
    with pytest.raises(VerificationFailed, match="is the record of"):
        kernel.output_gate.check(Labeled(text, kernel.classifier.classify(text)), envelope,
                                 sources={"99999999": signed})
    found, why = cited_source({"PMID:34449189": signed}, "34449189")
    assert found is signed and why == ""
    kernel.close()


def test_a_source_supplied_in_another_spelling_of_its_identifier_is_found(tmp_path,
                                                                          local_model):
    kernel = _kernel(tmp_path)
    text = "Empagliflozin may reduce heart-failure hospitalization (PMID: 34449189)."
    result = Runner(kernel, model=local_model, model_invoke=lambda prompt: text).run(
        "q", sources={"PMID:34449189": kernel.evidence_signer.sign(_record())})
    assert result.status == "ok", result.error
    kernel.close()


def test_a_bibliographic_record_must_be_of_its_identifiers_type(tmp_path):
    kernel = _kernel(tmp_path)

    class Envelope:
        run_id = "r1"

    wrong = kernel.evidence_signer.sign(_record(identifier="PMID:34449189",
                                                source_type=SourceType.DOI))
    with pytest.raises(PolicyDenied, match="its record says doi"):
        ingest_evidence(kernel, {"PMID:34449189": wrong}, Envelope)
    typed = ingest_evidence(kernel, {"NCT04157751": "a registration", "my notes": "text"},
                            Envelope)
    assert typed["NCT04157751"].source_type is SourceType.NCT
    assert typed["my notes"].source_type is SourceType.USER_SUPPLIED
    kernel.close()
