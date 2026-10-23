"""Inquiries kept by the runtime across a conversation (``bioagent.research.inquiries``).

The store holds what a model must not: the sealed predictions and the belief. These tests
check that an inquiry survives being written and read back, that one altered on disk is
refused, that the bar for acceptance can be raised and never lowered, that a runtime-
classified test takes data and never a reported outcome, that a replication needs data of
its own, and that a conclusion names the studies that would still decide the question.
"""

from __future__ import annotations

import json

import pytest

from bioagent.research import inquiries as inq
from bioagent.research import rivals
from bioagent.studies.contrast import Group, contrast_formula_monomer
from psh.scientist import InquiryRefused

FVC = {"formula": "葛根芩连汤", "constituent": "berberine", "endpoint": "fasting glucose",
       "margin": 0.5, "better": "lower"}


@pytest.fixture
def store(tmp_path):
    return inq.InquiryStore(tmp_path / "inquiries")


def _contrast(formula, monomer, study="s1", **kw):
    args = {"formula": {"label": "GQD", "values": formula, "study": study},
            "monomer": {"label": "berberine", "values": monomer, "study": study},
            "higher_is_better": False, "margin": 0.5, "dose_matched": True, **kw}

    def group(d):
        return Group(label=d["label"], values=tuple(d["values"]), study=d["study"])
    result = contrast_formula_monomer(group(args["formula"]), group(args["monomer"]),
                                      higher_is_better=False, margin=args["margin"],
                                      dose_matched=args["dose_matched"])
    return args, result


LOWER = [5.1, 5.0, 4.9, 5.2, 5.0, 4.8]          # the formula lowers glucose more
HIGHER = [6.1, 6.0, 6.3, 5.9, 6.2, 6.0]


def test_an_inquiry_opened_from_a_scaffold_is_kept_and_read_back(store):
    view = inq.open_inquiry(store, scaffold="formula_vs_constituent", values=FVC)
    assert view["scaffold"] == "formula_vs_constituent"
    assert view["next"]["action"] == "run" and view["next"]["classifier"] == "contrast"
    assert rivals.GAP_NOTE in view["notes"]
    inquiry, record = store.load(view["inquiry_id"])
    assert inquiry.head == view["head"] and record["values"]["margin"] == 0.5
    assert all(not inquiry.missing(a.id) for a in inquiry.analyses)   # every one sealed
    assert inq.status(store, view["inquiry_id"])["head"] == view["head"]
    assert store.ids() == [view["inquiry_id"]]


def test_runtime_classified_tests_reach_acceptance_and_say_so(store):
    iid = inq.open_inquiry(store, scaffold="formula_vs_constituent", values=FVC)["inquiry_id"]
    first = _contrast(LOWER, HIGHER)
    view = inq.record_classified(store, iid, "contrast", *first,
                                 tool="study.contrast_formula_monomer",
                                 evidence_ref="lab notebook 12")
    assert view["update"]["outcome"] == "formula_exceeds"
    assert view["update"]["by"] == "runtime"
    assert view["update"]["evidence_ref"].startswith("runtime:study.contrast_formula_monomer:")
    assert view["update"]["read"]["difference"]["estimate"] < 0
    second = _contrast([x - 0.1 for x in LOWER], HIGHER, study="s2")
    view = inq.record_classified(store, iid, "contrast-rep", *second,
                                 tool="study.contrast_formula_monomer")
    assert view["next"]["action"] == "stop"
    done = inq.conclude(store, iid)
    c = done["conclusion"]
    assert c["verdict"] == "accepted" and c["leader"] == "formula_adds"
    assert c["certainty"] == "tentative"            # an animal design caps it
    assert done["by_runtime"] == 2 and done["by_agent"] == 0
    assert any("efficacy question" in x for x in c["limitations"])


def test_a_replication_needs_data_of_its_own(store):
    iid = inq.open_inquiry(store, scaffold="formula_vs_constituent", values=FVC)["inquiry_id"]
    first = _contrast(LOWER, HIGHER)
    inq.record_classified(store, iid, "contrast", *first)
    with pytest.raises(rivals.Unclassifiable, match="already recorded for contrast"):
        inq.record_classified(store, iid, "contrast-rep", *first)


def test_criteria_not_sealed_are_refused_and_nothing_is_recorded(store):
    iid = inq.open_inquiry(store, scaffold="formula_vs_constituent", values=FVC)["inquiry_id"]
    args, result = _contrast(LOWER, HIGHER)
    with pytest.raises(rivals.Unclassifiable, match="margin"):
        inq.record_classified(store, iid, "contrast", {**args, "margin": 2.0}, result)
    with pytest.raises(rivals.Unclassifiable, match="dose_matched"):
        inq.record_classified(store, iid, "contrast", {**args, "dose_matched": False}, result)
    assert inq.status(store, iid)["observed"] == []


def test_a_runtime_classified_test_takes_data_never_a_reported_outcome(store):
    iid = inq.open_inquiry(store, scaffold="formula_vs_constituent", values=FVC)["inquiry_id"]
    with pytest.raises(ValueError, match="classified by the runtime"):
        inq.record_reported(store, iid, "contrast", "formula_exceeds", "PMID:1")


def test_a_reported_outcome_cites_its_evidence(store):
    iid = inq.open_inquiry(store, scaffold="safety_signal",
                           values={"herb": "何首乌", "event": "liver injury"})["inquiry_id"]
    with pytest.raises(ValueError, match="evidence_ref"):
        inq.record_reported(store, iid, "dechallenge", "positive", "  ")
    view = inq.record_reported(store, iid, "dechallenge", "positive", "PMID:12345678")
    assert view["update"]["by"] == "agent"
    assert view["update"]["evidence_ref"] == "agent:PMID:12345678"
    with pytest.raises(ValueError, match="evidence of its own"):
        inq.record_reported(store, iid, "dechallenge-rep", "positive", "PMID:12345678")
    with pytest.raises(InquiryRefused) as exc:
        inq.record_reported(store, iid, "authenticated", "made_up_outcome", "PMID:2")
    assert exc.value.code == "INQ130"
    done = inq.conclude(store, iid)
    assert done["by_agent"] == 1
    # the safety warning leads, and says an open verdict is not reassurance
    assert "not reassurance" in done["conclusion"]["limitations"][0]


def test_stored_trails_and_values_are_verified(store):
    iid = inq.open_inquiry(store, scaffold="formula_vs_constituent", values=FVC)["inquiry_id"]
    path = store.path(iid)
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["record"]["values"]["margin"] = 5.0             # a laxer margin, written in afterwards
    path.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(rivals.Unclassifiable, match="not the ones sealed"):
        inq.record_classified(store, iid, "contrast", *_contrast(LOWER, HIGHER))
    doc["inquiry"]["trail"][1]["body"]["predictions"]["formula_adds"]["equivalent"] = 0.5
    path.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(InquiryRefused) as exc:
        store.load(iid)
    assert exc.value.code == "INQ150"
    listed = inq.listing(store)
    assert listed[0]["inquiry_id"] == iid and "INQ150" in listed[0]["error"]


def test_ids_are_checked_before_any_path_is_built(store):
    for bad in ("../x", "inq_123", "INQ_0123456789abcdef", ""):
        with pytest.raises(ValueError):
            store.path(bad)
    with pytest.raises(LookupError):
        inq.status(store, "inq_0123456789abcdef")


@pytest.mark.parametrize("rule", [
    {"accept_at": 0.9}, {"min_severity": 0.6}, {"require_replication": False},
    {"catch_all_floor": 0.01}, {"catch_all_alarm": 0.9}, {"max_steps": 100},
    {"accept_at": "high"}, {"no_such_field": 1}])
def test_the_bar_cannot_be_lowered(store, rule):
    with pytest.raises(ValueError):
        inq.open_inquiry(store, scaffold="formula_vs_constituent", values=FVC, rule=rule)


def test_the_bar_can_be_raised(store):
    view = inq.open_inquiry(store, scaffold="formula_vs_constituent", values=FVC,
                            rule={"accept_at": 0.99, "min_severity": 0.9,
                                  "require_replication": True, "max_steps": 10})
    inquiry, _ = store.load(view["inquiry_id"])
    assert inquiry.rule.accept_at == 0.99 and inquiry.rule.min_severity == 0.9


CUSTOM = {
    "question": "Does 黄芪 raise white-cell counts after chemotherapy?",
    "explanations": [
        {"id": "raises", "proposition": "黄芪 raises white-cell counts after chemotherapy",
         "claim_kind": "efficacy", "prior": 0.4},
        {"id": "no_change", "proposition": "黄芪 does not change them",
         "claim_kind": "efficacy", "prior": 0.4}],
    "analyses": [
        {"id": "rct", "title": "pooled randomised trials", "design": "systematic_review",
         "outcomes": ["raised", "unchanged"], "run_with": "literature_search",
         "classify": "raised: the pooled CI excludes no effect"},
        {"id": "dock", "title": "docking to G-CSF receptor", "design": "in_silico",
         "outcomes": ["binds", "no"]}],
    "predictions": {"rct": {"raises": {"raised": 0.85, "unchanged": 0.15},
                            "no_change": {"raised": 0.1, "unchanged": 0.9}}}}


def test_an_agent_can_open_its_own_inquiry_under_the_engines_rules(store):
    view = inq.open_inquiry(store, **CUSTOM)
    iid = view["inquiry_id"]
    assert view["scaffold"] is None and view["next"]["analysis_id"] == "rct"
    assert view["next"]["run_with"] == "literature_search"
    # a docking analysis cannot carry a prediction about efficacy (INQ113)
    with pytest.raises(InquiryRefused) as exc:
        inq.declare(store, iid, "dock", {"raises": {"binds": 0.9, "no": 0.1}})
    assert exc.value.code == "INQ113"
    # with data it is not classified by the runtime
    with pytest.raises(ValueError, match="not classified by the runtime"):
        inq.record_classified(store, iid, "rct", {"x": 1}, None)
    view = inq.record_reported(store, iid, "rct", "raised", "PMID:1, PMID:2")
    assert view["leader"] == "raises"
    with pytest.raises(InquiryRefused) as exc:              # sealed after the outcome: no
        inq.declare(store, iid, "rct", {"no_change": {"raised": 0.5, "unchanged": 0.5}})
    assert exc.value.code in ("INQ115", "INQ116")
    view = inq.declare(store, iid, "rct2", {"raises": {"raised": 0.85, "unchanged": 0.15},
                                           "no_change": {"raised": 0.1, "unchanged": 0.9}},
                       analysis={"id": "rct2", "title": "a new registered trial",
                                 "design": "randomised_trial",
                                 "outcomes": ["raised", "unchanged"], "replicates": "rct"})
    assert view["next"]["analysis_id"] == "rct2"
    view = inq.withdraw(store, iid, "rct2", "not yet done: no registered trial reports it")
    done = inq.conclude(store, iid)
    gaps = {g["analysis_id"]: g for g in done["to_decide"]}
    assert "rct2" in gaps and gaps["rct2"]["withdrawn"].startswith("not yet done")
    assert done["conclusion"]["verdict"] != "accepted"


def test_a_closed_world_is_refused(store):
    closed = {**CUSTOM, "explanations": [{**e, "prior": 0.5} for e in CUSTOM["explanations"]]}
    with pytest.raises(InquiryRefused) as exc:
        inq.open_inquiry(store, **closed)
    assert exc.value.code == "INQ101"
    assert store.ids() == []                                # nothing half-written


def test_new_explanations_come_out_of_the_catch_all(store):
    view = inq.open_inquiry(store, **CUSTOM)
    iid = view["inquiry_id"]
    before = view["belief"]["catch-all"]
    view = inq.admit(store, iid, [{"id": "mobilises", "proposition": "黄芪 mobilises "
                                   "white cells from the marrow without raising production",
                                   "claim_kind": "mechanism_hypothesis"}],
                     {"mobilises": 0.5})
    assert view["belief"]["mobilises"] == pytest.approx(before * 0.5, abs=1e-4)
    assert view["next"]["action"] in ("declare", "run")


def test_a_scaffold_and_a_custom_question_are_not_mixed(store):
    with pytest.raises(ValueError, match="not both"):
        inq.open_inquiry(store, scaffold="efficacy", values={}, question="q")


def test_plain_turns_results_into_json_data():
    import math
    np = pytest.importorskip("numpy")
    assert inq.plain({"a": np.float64(1.5), "b": (1, np.int64(2)), "c": math.nan}) == {
        "a": 1.5, "b": [1, 2], "c": None}
    assert inq.digest({"a": np.float64(1.5)}) == inq.digest({"a": 1.5})


# ============================================================ plans and sensitivity

def test_a_plan_is_sealed_and_says_how_to_run_each_analysis(store):
    iid = inq.open_inquiry(store, scaffold="formula_vs_constituent", values=FVC)["inquiry_id"]
    out = inq.plan(store, iid, 2)
    batch = [b["analysis_id"] for b in out["batch"]]
    assert batch == out["planned"] == ["contrast", "contrast-rep"]
    assert all(b["classifier"] == "contrast" and b["data"] for b in out["batch"])
    assert out["batch_bits"] == pytest.approx(sum(b["adds_bits"] for b in out["batch"]),
                                              abs=1e-3)
    inquiry, _ = store.load(iid)
    assert inquiry.trail[-1]["event"] == "planned" and inquiry.head == out["head"]
    # the replicate first: still the engine's batch, not an unrecommended step
    view = inq.record_classified(store, iid, "contrast-rep",
                                 *_contrast([x - 0.1 for x in LOWER], HIGHER, study="s2"))
    assert view["update"]["purpose"] == "batch" and view["planned"] == ["contrast"]
    view = inq.record_classified(store, iid, "contrast", *_contrast(LOWER, HIGHER))
    assert view["update"]["purpose"] != "unrecommended" and "planned" not in view
    assert inq.conclude(store, iid)["conclusion"]["verdict"] == "accepted"


def test_an_empty_plan_records_nothing_and_says_why(store):
    iid = inq.open_inquiry(store, scaffold="formula_vs_constituent", values=FVC)["inquiry_id"]
    inq.record_classified(store, iid, "contrast", *_contrast(LOWER, HIGHER))
    inq.record_classified(store, iid, "contrast-rep",
                          *_contrast([x - 0.1 for x in LOWER], HIGHER, study="s2"))
    head = inq.status(store, iid)["head"]
    out = inq.plan(store, iid, 3)
    assert out["batch"] == [] and out["batch_note"].startswith("nothing to run now")
    assert out["head"] == head
    for size in (0, 5, "2"):
        with pytest.raises(ValueError):
            inq.plan(store, iid, size)


def test_a_conclusion_that_holds_says_so_last(store):
    iid = inq.open_inquiry(store, scaffold="formula_vs_constituent", values=FVC)["inquiry_id"]
    inq.record_classified(store, iid, "contrast", *_contrast(LOWER, HIGHER))
    inq.record_classified(store, iid, "contrast-rep",
                          *_contrast([x - 0.1 for x in LOWER], HIGHER, study="s2"))
    done = inq.conclude(store, iid)
    rows = done["sensitivity"]["tempers"]
    assert [r["temper"] for r in rows] == list(inq.TEMPERS)
    assert {r["leader"] for r in rows} == {"formula_adds"}
    assert done["sensitivity"]["leader_holds_to"] == inq.TEMPERS[-1]
    posteriors = [r["belief"]["formula_adds"] for r in rows]
    assert posteriors == sorted(posteriors, reverse=True)
    assert posteriors[0] <= done["conclusion"]["posterior"]["formula_adds"]
    assert "still leads" in done["conclusion"]["limitations"][-1]


ZEROED = {
    "question": "Is the screen's signal real?",
    "explanations": [
        {"id": "real", "proposition": "the compound acts on the pathway",
         "claim_kind": "mechanism_hypothesis", "prior": 0.45},
        {"id": "artefact", "proposition": "the signal is an assay artefact", "prior": 0.45}],
    "analyses": [{"id": a, "title": f"screen {a}", "design": "in_silico",
                  "outcomes": ["hit", "miss"]} for a in ("a", "b", "c")],
    # one confident zero carries the answer: "real" calls a miss in a impossible
    "predictions": {"a": {"real": {"hit": 1.0, "miss": 0.0},
                          "artefact": {"hit": 0.6, "miss": 0.4}},
                    "b": {"real": {"hit": 0.1, "miss": 0.9},
                          "artefact": {"hit": 0.9, "miss": 0.1}},
                    "c": {"real": {"hit": 0.1, "miss": 0.9},
                          "artefact": {"hit": 0.9, "miss": 0.1}}}}


def test_a_conclusion_carried_by_how_sure_a_prediction_was_says_so_first(store):
    iid = inq.open_inquiry(store, **ZEROED)["inquiry_id"]
    for analysis, source in (("a", "PMID:1"), ("b", "PMID:2"), ("c", "PMID:3")):
        inq.record_reported(store, iid, analysis, "miss", source)
    done = inq.conclude(store, iid)
    assert done["conclusion"]["leader"] == "artefact"
    assert done["sensitivity"]["leader_holds_to"] == 0.0
    assert done["sensitivity"]["tempers"][0]["leader"] == "real"
    limitations = done["conclusion"]["limitations"]
    assert limitations[0].startswith("3 of the 3 outcomes were classified by the agent")
    assert "real leads instead of artefact" in limitations[1]
