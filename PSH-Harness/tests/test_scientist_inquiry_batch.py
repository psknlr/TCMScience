"""Batches and sensitivity: running analyses together, and how sure the predictions were.

An agent that can run several tools at once should not spend them on analyses that would
tell the same story: ``batch`` values a set by the information it carries jointly, under
the engine's own updates, and ``plan`` seals the set into the trail so that the record
says the engine chose it. ``sensitivity`` replays the outcomes with every prediction made
less confident, to show whether the answer rests on which outcome each explanation
favoured or on how sure the predictions were.
"""

from __future__ import annotations

import copy
import itertools
import math

import pytest

from psh.scientist import (
    CATCH_ALL, Analysis, Explanation, Inquiry, InquiryRefused, Purpose, Role, StoppingRule,
)
from psh.scientist.inquiry import MAX_BATCH
from psh.sir.values import ClaimKind, StudyDesign

from test_scientist_inquiry import BINARY, SPECIFICITY, analyses, opened, row

SILICO = StudyDesign.IN_SILICO


def enumerated(inquiry, ids):
    """E[KL(belief after || belief now)] over every joint outcome, each applied by
    ``observe`` on a copy: what ``joint_gain`` must equal."""
    prior = inquiry.belief
    total = 0.0
    for outcomes in itertools.product(*(inquiry.analysis(a).outcomes for a in ids)):
        trial = Inquiry.from_dict(copy.deepcopy(inquiry.as_dict()))
        weight = 1.0
        for a, o in zip(ids, outcomes):
            update = trial.observe(a, o)
            weight *= math.fsum(update.prior[h] * update.likelihood[h] for h in prior)
        post = trial.belief
        total += weight * math.fsum(post[h] * math.log2(post[h] / prior[h])
                                    for h in post if post[h] > 0.0 and prior[h] > 0.0)
    return total


def with_efficacy():
    """An inquiry where one explanation (efficacy) no in-silico analysis can move."""
    inquiry = Inquiry("q", [
        Explanation("efficacy", "it works in patients", ClaimKind.EFFICACY, prior=0.3),
        Explanation("target", "it acts on the target", ClaimKind.MECHANISM_HYPOTHESIS,
                    prior=0.3),
        Explanation("noise", "assay noise", role=Role.ARTEFACT, prior=0.3)], [
        Analysis("a", "first screen", SILICO, ("hit", "miss", "unclear")),
        Analysis("b", "second screen", SILICO, ("hit", "miss")),
        Analysis("c", "a third", SILICO, ("hit", "miss"))])
    inquiry.declare("a", {"target": {"hit": .7, "miss": .1, "unclear": .2},
                          "noise": {"hit": .2, "miss": .5, "unclear": .3}})
    inquiry.declare("b", {"target": row(.8, ("hit", "miss")),
                          "noise": row(.3, ("hit", "miss"))})
    inquiry.declare("c", {"target": row(.6, ("hit", "miss")),
                          "noise": row(.0, ("hit", "miss"))})
    return inquiry


# ================================================================== joint gain
@pytest.mark.parametrize("ids", [["assayed"], ["assayed", "assayed-seed"],
                                 ["random", "assayed"], ["reactome", "assayed", "random"],
                                 ["assayed", "random", "random-2", "assayed-seed"]])
def test_joint_gain_is_what_the_engine_s_own_updates_yield_on_average(ids):
    inquiry = opened()
    assert inquiry.joint_gain(ids) == pytest.approx(enumerated(inquiry, ids), abs=1e-9)


def test_joint_gain_holds_for_an_explanation_no_design_here_can_move():
    inquiry = with_efficacy()
    for ids in (["a", "b"], ["b", "a", "c"]):
        assert inquiry.joint_gain(ids) == pytest.approx(enumerated(inquiry, ids), abs=1e-9)


def test_joint_gain_of_one_analysis_is_its_expected_gain_and_of_none_is_zero():
    inquiry = opened()
    assert inquiry.joint_gain(["assayed"]) == pytest.approx(inquiry.expected_gain("assayed"))
    assert inquiry.joint_gain([]) == 0.0
    inquiry.observe("assayed", "enriched")
    assert inquiry.joint_gain(["assayed"]) == 0.0     # observed: not ready, adds nothing


def test_a_replicate_tells_less_than_its_gain_alone_suggests():
    inquiry = opened()
    alone = inquiry.expected_gain("assayed") + inquiry.expected_gain("assayed-seed")
    together = inquiry.joint_gain(["assayed", "assayed-seed"])
    assert inquiry.expected_gain("assayed") < together < alone
    entropy = -math.fsum(p * math.log2(p) for p in inquiry.belief.values() if p > 0)
    assert inquiry.joint_gain([a.id for a in analyses()]) <= entropy + 1e-9


# ===================================================================== batches
def test_a_batch_starts_with_the_engine_s_next_step_and_adds_what_it_does_not_already_know():
    inquiry = opened()
    head = inquiry.head
    options = inquiry.batch(2)
    assert options[0].analysis_id == inquiry.next_step().analysis_id == "assayed"
    # the replicate would repeat the story; the specificity test tells a different one
    assert options[1].analysis_id == "random"
    joint = inquiry.joint_gain([o.analysis_id for o in options])
    assert math.fsum(o.expected_gain for o in options) == pytest.approx(joint)
    assert inquiry.head == head, "a batch is a reading, not a step"


def test_a_batch_stops_when_the_next_analysis_adds_too_little():
    inquiry = opened(rule=StoppingRule(min_gain=0.25))
    options = inquiry.batch(MAX_BATCH)
    assert 1 <= len(options) < MAX_BATCH
    assert all(o.gain_per_cost >= 0.25 for o in options[1:])


def test_a_batch_keeps_to_the_budget_and_the_step_limit():
    inquiry = opened(rule=StoppingRule(budget=2.0))
    assert len(inquiry.batch(MAX_BATCH)) == 2
    inquiry = opened(rule=StoppingRule(max_steps=1))
    assert len(inquiry.batch(MAX_BATCH)) == 1


@pytest.mark.parametrize("size", [0, MAX_BATCH + 1, 2.0, True, "2"])
def test_a_batch_has_one_to_max_batch_analyses(size):
    with pytest.raises(ValueError):
        opened().batch(size)


def test_there_is_no_batch_when_the_next_step_is_not_to_run_something():
    inquiry = Inquiry("q", [Explanation("x", "x", ClaimKind.MECHANISM_HYPOTHESIS,
                                        prior=0.4),
                            Explanation("y", "y", role=Role.ARTEFACT, prior=0.4)],
                      [Analysis("a", "a", SILICO, BINARY)])
    assert inquiry.next_step().action == "declare"
    assert inquiry.batch(3) == ()
    assert inquiry.plan(3) == () and len(inquiry.trail) == 1, "nothing to record"


# ======================================================================= plans
def test_a_plan_is_sealed_and_its_analyses_are_recorded_as_the_engine_s_batch():
    inquiry = opened()
    planned = [o.analysis_id for o in inquiry.plan(2)]
    assert inquiry.trail[-1]["event"] == "planned"
    assert inquiry.trail[-1]["body"]["analyses"] == planned == ["assayed", "random"]
    assert inquiry.planned == ("assayed", "random")
    first = inquiry.observe("random", "specific")       # out of order: still the plan's
    assert first.purpose is Purpose.BATCH
    assert inquiry.planned == ("assayed",)
    second = inquiry.observe("assayed", "enriched")
    assert second.purpose is not Purpose.UNRECOMMENDED
    other = inquiry.observe("reactome", "enriched")
    assert other.purpose is not Purpose.BATCH, "not in the plan"


def test_a_plan_replays_and_one_written_afterwards_does_not():
    inquiry = opened()
    inquiry.plan(3)
    inquiry.observe("assayed", "enriched")
    replayed = Inquiry.from_dict(copy.deepcopy(inquiry.as_dict()))
    assert replayed.head == inquiry.head
    doc = copy.deepcopy(inquiry.as_dict())
    plan = next(e for e in doc["trail"] if e["event"] == "planned")
    plan["body"]["analyses"] = ["reactome", "random"]        # not what the engine chose
    with pytest.raises(InquiryRefused) as caught:
        Inquiry.from_dict(doc)
    assert caught.value.code == "INQ150"


# ================================================================= sensitivity
def test_untempered_sensitivity_is_the_belief_itself():
    inquiry = opened()
    for analysis, outcome in (("assayed", "enriched"), ("random", "specific")):
        inquiry.observe(analysis, outcome)
    (row0,) = inquiry.sensitivity((0.0,))
    assert row0["leader"] == inquiry.leader
    for h, p in inquiry.belief.items():
        assert row0["belief"][h] == pytest.approx(p, abs=1e-12)


def test_tempering_shrinks_the_evidence_but_keeps_its_direction_here():
    inquiry = opened()
    for analysis, outcome in (("assayed", "enriched"), ("assayed-seed", "enriched"),
                              ("random", "specific"), ("random-2", "specific")):
        inquiry.observe(analysis, outcome)
    head = inquiry.head
    rows = inquiry.sensitivity((0.1, 0.3, 0.6))
    assert inquiry.head == head, "sensitivity is a reading"
    assert [r["leader"] for r in rows] == ["target"] * 3
    posteriors = [r["leader_posterior"] for r in rows]
    assert inquiry.belief["target"] > posteriors[0] > posteriors[1] > posteriors[2]
    assert all(r["belief"]["target"] == r["leader_posterior"] for r in rows)
    for r in rows:
        assert math.fsum(r["belief"].values()) == pytest.approx(1.0)


def test_an_answer_carried_by_confident_zeros_changes_when_they_are_tempered():
    # y leads only because x declared one outcome impossible, and that outcome came
    inquiry = Inquiry("q", [
        Explanation("x", "x", ClaimKind.MECHANISM_HYPOTHESIS, prior=0.45),
        Explanation("y", "y", role=Role.ARTEFACT, prior=0.45)], [
        Analysis("a", "a", SILICO, BINARY), Analysis("b", "b", SILICO, BINARY),
        Analysis("c", "c", SILICO, BINARY)])
    inquiry.declare("a", {"x": row(1.0), "y": row(0.6)})
    inquiry.declare("b", {"x": row(0.1), "y": row(0.9)})
    inquiry.declare("c", {"x": row(0.1), "y": row(0.9)})
    inquiry.observe("a", "not_enriched")       # x said impossible: refuted
    inquiry.observe("b", "not_enriched")       # though x predicted these far better
    inquiry.observe("c", "not_enriched")
    assert inquiry.leader == "y" and inquiry.belief["x"] == 0.0
    rows = inquiry.sensitivity((0.1, 0.3))
    assert rows[0]["belief"]["x"] > 0.0, "a tempered zero no longer refutes"
    assert rows[1]["leader"] == "x", "the answer rested on how sure one prediction was"


@pytest.mark.parametrize("temper", [-0.1, 1.0, float("nan"), "0.1"])
def test_a_temper_is_a_weight_below_one(temper):
    with pytest.raises(ValueError):
        opened().sensitivity((temper,))


def test_sensitivity_replays_late_explanations_from_the_catch_all():
    inquiry = opened()
    for analysis in ("assayed", "random"):
        inquiry.observe(analysis, inquiry.analysis(analysis).outcomes[-1])
    inquiry.admit([Explanation("late", "something else", role=Role.ARTEFACT)],
                  {"late": 0.5})
    (row0,) = inquiry.sensitivity((0.0,))
    assert row0["belief"]["late"] == pytest.approx(inquiry.belief["late"], abs=1e-12)
    assert row0["belief"][CATCH_ALL] == pytest.approx(inquiry.belief[CATCH_ALL], abs=1e-12)


def test_specificity_outcomes_are_used_by_the_fixture():
    # guards the imports above against the fixture changing shape
    assert SPECIFICITY == ("specific", "not_specific")
