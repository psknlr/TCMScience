"""Evidence binding, after the October 2026 audit (AUD-07 to AUD-09).

The scope, the tier and the direction of a claim are what its cited evidence says, not
what the claim or the relation says about itself. Each test starts from the audit's own
example.
"""

from __future__ import annotations

import pytest

from bioagent.contracts.artifact import validate_artifact
from bioagent.contracts.candidate_claim import CandidateClaim, check_claim
from bioagent.sources import build_snapshot
from bioagent.sources.release import CandidateClaim as EdgeClaim, check_release
from bioagent.tcm.knowledge import seed
from bioagent.tcm import knowledge
from bioagent.tcm.model import ActionRelation, EvidenceTier, SafetyRecord, StudyEvidence
from test_contracts import artifact, item

pytestmark = pytest.mark.unit


# ======================================================= AUD-07: scope from evidence

HF = dict(population="adults with heart failure", outcome="NT-proBNP")


def _claim(**kw) -> CandidateClaim:
    base = dict(id="c1", text="Drug A lowers NT-proBNP in adults with heart failure.",
                claim_kind="efficacy", subject="drug A", predicate="reduces",
                object="NT-proBNP", supports=("e1",),
                asserted_population=HF["population"], asserted_outcome=HF["outcome"],
                supported_population=HF["population"], supported_outcome=HF["outcome"],
                direction="decrease", falsified_by="a larger null trial")
    base.update(kw)
    return CandidateClaim(**base)


def _why(verdict) -> str:
    return " ".join(r.detail for r in verdict.reasons)


def _trial(**kw):
    base = dict(id="e1", identifier="34449189", identifier_type="pmid", **HF)
    base.update(kw)
    return item(**base)


def test_a_claim_cannot_state_a_scope_its_evidence_does_not_cover():
    """The audit's case: evidence in P with endpoint X, a claim about Q and Y that fills
    its own supported_* with Q and Y. It was allowed with no codes."""
    claim = _claim(text="Drug A lowers mortality in children.", object="mortality",
                   asserted_population="children", asserted_outcome="mortality",
                   supported_population="children", supported_outcome="mortality")
    verdict = check_claim(claim, {"e1": _trial()})
    assert not verdict.allowed
    assert verdict.codes.count("CLM013") == 2          # population and outcome
    assert "CLM009" in verdict.codes                   # and both are extrapolations
    assert not verdict.needs_declaration               # a misstatement is not declarable
    assert "adults with heart failure" in _why(verdict)


def test_declaring_the_gap_does_not_excuse_a_misstated_scope():
    claim = _claim(asserted_population="children", supported_population="children",
                   declared_extrapolations={"population:children": "assumed similar"})
    verdict = check_claim(claim, {"e1": _trial()})
    assert not verdict.allowed and "CLM013" in verdict.codes


def test_a_claim_within_its_evidence_is_allowed():
    verdict = check_claim(_claim(), {"e1": _trial()})
    assert verdict.allowed, _why(verdict)


def test_a_wider_assertion_over_correctly_stated_evidence_must_be_declared():
    claim = _claim(asserted_population="adults")
    verdict = check_claim(claim, {"e1": _trial()})
    assert verdict.codes == ("CLM009",) and verdict.needs_declaration
    declared = _claim(asserted_population="adults",
                      declared_extrapolations={"population:adults": "heart failure only"})
    assert check_claim(declared, {"e1": _trial()}).allowed


def test_evidence_that_states_no_population_leaves_the_claims_own_word_unsupported():
    """Nothing in the evidence says who was studied, so "supported: adults with heart
    failure" is the claim's word: the population it asserts is an extrapolation."""
    verdict = check_claim(_claim(), {"e1": _trial(population="")})
    assert not verdict.allowed
    assert "undeclared extrapolation 'population:adults with heart failure'" in _why(verdict)
    declared = _claim(declared_extrapolations={
        "population:adults with heart failure": "the trial report names no population"})
    allowed = check_claim(declared, {"e1": _trial(population="")})
    assert allowed.allowed and allowed.unvalidated_extrapolations


def test_evidence_items_together_cover_an_enumerated_population():
    both = _claim(supports=("e1", "e2"), asserted_population="adults; children",
                  supported_population="adults; children")
    evidence = {"e1": _trial(population="adults"),
                "e2": _trial(id="e2", identifier="38291045", population="children")}
    assert check_claim(both, evidence).allowed


def test_claims_with_descriptive_scope_labels_keep_their_own_comparison():
    """A mechanism hypothesis names its scope "human proteins (in silico)" over edges
    whose evidence records a relation, not a study population; it is not a claim about
    people, and its labels are not held to the evidence's fields."""
    claim = _claim(claim_kind="mechanism_hypothesis", text="A may act on T1.",
                   asserted_population="human proteins (in silico)",
                   supported_population="human proteins (in silico)",
                   asserted_outcome="target relation", supported_outcome="target relation")
    evidence = {"e1": item(id="e1", design="in_vitro", outcome="targets",
                           identifier="38291045", identifier_type="pmid")}
    verdict = check_claim(claim, evidence)
    assert "CLM013" not in verdict.codes and "CLM009" not in verdict.codes


def test_an_artifact_carrying_a_misstated_scope_is_not_publishable():
    claim = _claim(asserted_population="children", supported_population="children")
    verdict = validate_artifact(artifact(claims=(claim,), evidence=(_trial(),)))
    assert not verdict.publishable and "ART105" in verdict.codes
    assert "covers population 'children'" in verdict.explain()


def test_a_safety_signal_takes_its_scope_from_the_record_it_cites(monkeypatch):
    """Once the corpus holds a case report, assess-tcm-safety makes a safety_signal
    claim, a claim about people. Its population and outcome are the cited record's, so
    the skill's own claim passes the scope check that now reads them from there."""
    from bioagent.skills.p0.assess_safety import assess_tcm_safety
    kb = seed()
    study = StudyEvidence(id="study.case_huangqi", tier=EvidenceTier.CASE_REPORT,
                          subject_id="herb.huangqi", design="case_report", population="成人",
                          outcome="过敏反应", pmid="12345678")
    kb.extend(studies=(study,), safety=(SafetyRecord(
        id="safety.huangqi_case", subject_id="herb.huangqi", kind="adverse_event",
        description="黄芪致过敏反应", severity="high", tier=EvidenceTier.CASE_REPORT,
        evidence_ids=(study.id,), population="成人"),))
    monkeypatch.setattr(knowledge, "default_knowledge", lambda: kb)
    produced = assess_tcm_safety("黄芪", run_id="r1")
    (claim,) = produced.claims
    assert claim.claim_kind == "safety_signal" and claim.asserted_population == "成人"
    assert check_claim(claim, {e.id: e for e in produced.evidence}).allowed
    assert validate_artifact(produced).publishable


# ================================================ AUD-08: direction from the path

A, B = "inchikey:AAAAAAAAAAAAAA-AAAAAAAAAA-N", "inchikey:BBBBBBBBBBBBBB-BBBBBBBBBB-N"
T1, T2, X = "uniprot:P10001", "uniprot:P10002", "uniprot:P10003"
HERB, SPECIES = "tcm:herb.fixture", "ncbitaxon:1"


def _edge(record, subject, predicate, obj, **kw):
    return {"subject": subject, "predicate": predicate, "object": obj,
            "primary_knowledge_source": "fixture", "knowledge_level": "knowledge_assertion",
            "agent_type": "manual_agent", "study_design": "in_vitro", "license": "CC0-1.0",
            "source_record_id": record, "publications": ["pmid:1"], **kw}


EDGES = [
    _edge("bind", A, "targets", T1,
          measure={"type": "Kd", "relation": "=", "value": 50.0, "unit": "nM"}),
    _edge("antag_B_T2", B, "antagonises", T2),
    _edge("antag_A_T1", A, "antagonises", T1),
    _edge("pot_A_T1", A, "potentiates", T1),
    _edge("antag_A_X", A, "antagonises", X),
    _edge("ppi_X_T2", X, "interacts_with", T2),
    _edge("antag_X_T2", X, "antagonises", T2),
    _edge("base", HERB, "has_base_species", SPECIES, study_design="expert_consensus"),
    _edge("measured", SPECIES, "contains", A, study_design="chemical_analysis",
          composition_level="C1"),
]


@pytest.fixture
def snap(tmp_path):
    raw = tmp_path / "raw.txt"
    raw.write_text("fixture", encoding="utf-8")
    nodes = [{"id": i, "category": c, "name": i, "source": "fixture"}
             for i, c in ((A, "ingredient"), (B, "ingredient"), (T1, "target"),
                          (T2, "target"), (X, "target"), (HERB, "herb"),
                          (SPECIES, "organism"))]
    return build_snapshot(key="fixture", version="1", nodes=nodes, edges=EDGES,
                          raw_files={"raw.txt": raw}, parser="def p(): pass",
                          root=tmp_path / "snap", license="CC0-1.0", citation="fixture")


def _released(snap, subject, obj, statement, *records,
              kind: str = "mechanism") -> tuple[bool, str]:
    claim = EdgeClaim(kind, subject, obj,
                      tuple((snap.snapshot_id, r) for r in records), statement=statement)
    verdict = check_release([claim], [snap])
    return verdict.ok, "; ".join(reason for _, reason in verdict.refused)


def test_an_unrelated_edge_cannot_lend_its_direction(snap):
    """The audit's case: A's own record is a binding constant; B antagonises T2. "A
    inhibits T1" was refused on the first and released with the second added."""
    alone = _released(snap, A, T1, "A inhibits T1", "bind")
    borrowed = _released(snap, A, T1, "A inhibits T1", "bind", "antag_B_T2")
    assert not alone[0] and not borrowed[0]
    assert "no path of supporting edges records" in borrowed[1]


def test_a_record_of_the_same_pair_with_a_direction_supports_it(snap):
    assert _released(snap, A, T1, "A inhibits T1", "bind", "antag_A_T1")[0]
    assert _released(snap, A, T1, "A binds T1", "bind")[0]          # no direction stated


def test_records_of_both_directions_support_neither(snap):
    ok, why = _released(snap, A, T1, "A inhibits T1", "antag_A_T1", "pot_A_T1")
    assert not ok and "both directions" in why


def test_an_edge_without_a_direction_leaves_the_path_without_one(snap):
    """A antagonises X and X interacts with T2: nothing says what A does to T2."""
    ok, why = _released(snap, A, T2, "A inhibits T2", "antag_A_X", "ppi_X_T2")
    assert not ok and "no direction" in why


def test_directions_compose_along_a_path(snap):
    """An antagonist of an antagonist of T2 does not inhibit T2."""
    assert not _released(snap, A, T2, "A inhibits T2", "antag_A_X", "antag_X_T2")[0]
    assert _released(snap, A, T2, "A activates T2", "antag_A_X", "antag_X_T2")[0]


def test_composition_edges_pass_the_direction_through(snap):
    """A herb whose species contains an antagonist of T1: the measured composition says
    what the herb holds, not what it does, and leaves the antagonist's direction."""
    path = ("base", "measured", "antag_A_T1")
    ok, why = _released(snap, HERB, T1, "the herb may inhibit T1", *path,
                        kind="mechanism_hypothesis")
    assert ok, why
    assert not _released(snap, HERB, T1, "the herb may inhibit T1", "base", "measured",
                         "bind", kind="mechanism_hypothesis")[0]


# ===================================== AUD-09: a relation's tier is its evidence's

def _fake_rct(**kw) -> ActionRelation:
    base = dict(id="relation.fake_rct", subject_id="formula.guizhitang",
                predicate="indicated_for", object_id="syndrome.taiyang_zhongfeng",
                tier=EvidenceTier.RANDOMIZED_TRIAL, evidence_ids=("passage.shl_12",))
    base.update(kw)
    return ActionRelation(**base)


def test_a_relation_cannot_claim_a_tier_above_its_evidence():
    """The audit's case: declared a randomised trial, citing a 伤寒论 passage."""
    kb = seed()
    with pytest.raises(ValueError, match="RANDOMIZED_TRIAL, but its evidence supports "
                                         "CLASSICAL_TEXT at most"):
        kb.extend(relations=(_fake_rct(),))
    assert "relation.fake_rct" not in kb.relations


def test_applicability_reads_the_tier_off_the_evidence():
    """A relation placed in the base without ``extend`` is bounded all the same."""
    kb = seed()
    kb.relations["relation.fake_rct"] = _fake_rct()
    verdict = kb.applicability("relation.fake_rct", claim_kind="efficacy", population="儿童")
    assert not verdict.licensed and verdict.verdict == "unsupported"
    assert verdict.tier is EvidenceTier.CLASSICAL_TEXT
    assert kb.applicability("relation.fake_rct", claim_kind="attribution").licensed


def _with_trial(study_population: str = "", relation_population: str = ""):
    kb = seed()
    study = StudyEvidence(id="study.fixture_rct", tier=EvidenceTier.RANDOMIZED_TRIAL,
                          subject_id="herb.huangqi", design="randomized_trial",
                          population=study_population, outcome="乏力评分", pmid="12345678")
    kb.extend(studies=(study,), relations=(ActionRelation(
        id="relation.fixture", subject_id="herb.huangqi", predicate="treats",
        object_id="syndrome.qixu", tier=EvidenceTier.RANDOMIZED_TRIAL,
        evidence_ids=(study.id,), population=relation_population),))
    return kb


def _efficacy(kb, population: str = ""):
    return kb.applicability("relation.fixture", claim_kind="efficacy", population=population)


def test_a_clinical_claim_about_a_population_the_evidence_does_not_state_is_not_licensed():
    """Asking about 儿童 over evidence with no population used to record a reason and
    license the claim anyway."""
    kb = _with_trial()
    verdict = _efficacy(kb, "儿童")
    assert verdict.verdict == "extrapolated" and not verdict.licensed
    assert _efficacy(kb).licensed                      # no population asked: in scope


def test_the_relations_own_population_is_not_evidence_for_a_clinical_claim():
    verdict = _efficacy(_with_trial(relation_population="儿童"), "儿童")
    assert not verdict.licensed
    assert any("is not evidence" in r for r in verdict.reasons)


def test_the_studys_population_decides_and_the_relation_can_only_narrow_it():
    kb = _with_trial(study_population="成人气虚证患者")
    assert _efficacy(kb, "成人").licensed
    assert not _efficacy(kb, "儿童").licensed
    narrowed = _with_trial(study_population="成人气虚证患者", relation_population="老年")
    assert not _efficacy(narrowed, "成人").licensed
