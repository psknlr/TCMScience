"""The inquiry engine: what to run next, what an outcome does to belief, when to stop.

The model proposes explanations, analyses and what each explanation predicts. The engine
decides everything that follows from those commitments. Each test below pins one of the
decisions the model does not get to make: running the analysis it likes, updating belief
in a claim the evidence cannot reach, stopping as soon as the story is good, or stating
the answer more strongly than the designs behind it license.
"""

from __future__ import annotations

import copy
import math

import pytest

from psh.evidence.support import Certainty
from psh.scientist import (
    CATCH_ALL, INQUIRY_CODES, Analysis, Executed, Explanation, Inquiry, InquiryRefused,
    Purpose, Role, StoppingRule, Verdict, expected_information_gain, run_inquiry,
)
from psh.sir.values import ClaimKind, Licensing, StudyDesign

MECH = ClaimKind.MECHANISM_HYPOTHESIS
SILICO = StudyDesign.IN_SILICO
BINARY = ("enriched", "not_enriched")
SPECIFICITY = ("specific", "not_specific")


def explanations(**priors):
    priors = {"target": 0.4, "coverage": 0.3, "nonspecific": 0.2, **priors}
    return [
        Explanation("target", "the constituents act selectively on pathway A", MECH,
                    prior=priors["target"]),
        Explanation("coverage", "the enrichment comes from which proteins were assayed",
                    role=Role.ARTEFACT, prior=priors["coverage"]),
        Explanation("nonspecific", "any herb combination of this size reaches it",
                    role=Role.ARTEFACT, prior=priors["nonspecific"]),
    ]


def analyses():
    return [
        Analysis("reactome", "enrichment against all of Reactome", SILICO, BINARY),
        Analysis("assayed", "enrichment against the assayed proteins", SILICO, BINARY),
        Analysis("assayed-seed", "the same, another permutation seed", SILICO, BINARY,
                 replicates="assayed"),
        Analysis("random", "random herb combinations of the same size", SILICO,
                 SPECIFICITY),
        Analysis("random-2", "random herb combinations, a second draw", SILICO,
                 SPECIFICITY, replicates="random"),
    ]


def row(p, outcomes=BINARY):
    return {outcomes[0]: p, outcomes[1]: 1.0 - p}


def declare_all(inquiry):
    inquiry.declare("reactome", {h: row(0.9) for h in ("target", "coverage", "nonspecific")})
    for a in ("assayed", "assayed-seed"):
        inquiry.declare(a, {"target": row(0.85), "coverage": row(0.05),
                            "nonspecific": row(0.85)})
    for a in ("random", "random-2"):
        inquiry.declare(a, {"target": row(0.85, SPECIFICITY),
                            "coverage": row(0.5, SPECIFICITY),
                            "nonspecific": row(0.05, SPECIFICITY)})
    return inquiry


def opened(rule=None, **priors):
    return declare_all(Inquiry("Is the concentration of measured targets in pathway A "
                               "real?", explanations(**priors), analyses(), rule))


def world(**outcomes):
    truth = {"reactome": "enriched", "assayed": "enriched", "assayed-seed": "enriched",
             "random": "specific", "random-2": "specific", **outcomes}
    return lambda analysis, step: Executed(truth[analysis.id], f"run:{analysis.id}")


def refused(code, fn, *args, **kwargs):
    with pytest.raises(InquiryRefused) as caught:
        fn(*args, **kwargs)
    assert caught.value.code == code, caught.value
    assert caught.value.remedy
    return caught.value


# ============================================================ opening an inquiry

def test_the_world_is_never_closed():
    error = refused("INQ101", Inquiry, "q", explanations(target=0.5, coverage=0.3,
                                                          nonspecific=0.19))
    assert "below the floor" in str(error)


def test_an_inquiry_opens_with_a_catch_all_holding_the_remainder():
    inquiry = opened()
    assert inquiry.belief[CATCH_ALL] == pytest.approx(0.1)
    assert inquiry.explanation(CATCH_ALL).role is Role.CATCH_ALL
    assert math.fsum(inquiry.belief.values()) == pytest.approx(1.0)


def test_one_explanation_is_a_test_not_a_comparison():
    refused("INQ102", Inquiry, "q", explanations()[:1])


@pytest.mark.parametrize("make", [
    lambda: Explanation("x", "about the world, with no claim kind", prior=0.2),
    lambda: Explanation("x", "an artefact", MECH, role=Role.ARTEFACT, prior=0.2),
    lambda: Explanation(CATCH_ALL, "taking the engine's id", MECH, prior=0.2),
    lambda: Explanation("x", "", MECH, prior=0.2),
    lambda: Explanation("x", "a certainty", MECH, prior=1.0),
    lambda: Explanation(" x", "padded id", MECH, prior=0.2),
])
def test_a_malformed_explanation_is_refused(make):
    refused("INQ103", make)


def test_duplicate_ids_and_a_proposed_catch_all_are_refused():
    twice = explanations() + [Explanation("target", "again", MECH, prior=0.05)]
    refused("INQ103", Inquiry, "q", twice)


@pytest.mark.parametrize("make", [
    lambda: Analysis("a", "one outcome", SILICO, ("only",)),
    lambda: Analysis("a", "repeated outcomes", SILICO, ("x", "x")),
    lambda: Analysis("a", "free", SILICO, BINARY, cost=0.0),
    lambda: Analysis("a", "itself", SILICO, BINARY, replicates="a"),
    lambda: Analysis("a", "a list", SILICO, ["x", "y"]),
])
def test_a_malformed_analysis_is_refused(make):
    refused("INQ104", make)


def test_a_replicate_must_name_a_recorded_analysis_with_the_same_outcomes():
    inquiry = opened()
    refused("INQ104", inquiry.add_analysis,
            Analysis("orphan", "replicates nothing", SILICO, BINARY, replicates="missing"))
    refused("INQ104", inquiry.add_analysis,
            Analysis("other", "different outcomes", SILICO, SPECIFICITY,
                     replicates="assayed"))


@pytest.mark.parametrize("kw", [{"accept_at": 0.5}, {"min_severity": 1.0},
                                {"catch_all_floor": 0.6, "catch_all_alarm": 0.5},
                                {"budget": 0.0}, {"max_steps": 0}, {"min_gain": -1.0}])
def test_a_malformed_stopping_rule_is_refused(kw):
    refused("INQ105", StoppingRule, **kw)


# ============================================================ sealed predictions

@pytest.mark.parametrize("bad", [
    {"enriched": 0.7, "not_enriched": 0.2},
    {"enriched": 1.0},
    {"enriched": 1.2, "not_enriched": -0.2},
    {"enriched": float("nan"), "not_enriched": 0.5},
    {"enriched": 0.5, "not_enriched": 0.5, "other": 0.0},
])
def test_a_prediction_must_be_a_distribution_over_the_declared_outcomes(bad):
    inquiry = Inquiry("q", explanations(), analyses())
    refused("INQ112", inquiry.declare, "assayed", {"target": bad})


def test_the_catch_all_is_predicted_by_the_engine_not_the_proposer():
    inquiry = Inquiry("q", explanations(), analyses())
    refused("INQ114", inquiry.declare, "assayed", {CATCH_ALL: row(0.5)})


def test_a_prediction_after_the_outcome_is_an_accommodation():
    inquiry = opened()
    inquiry.observe("assayed", "enriched")
    refused("INQ115", inquiry.declare, "assayed", {"target": row(0.9)})


def test_a_sealed_prediction_cannot_be_changed():
    inquiry = opened()
    refused("INQ116", inquiry.declare, "assayed", {"target": row(0.99)})


def test_an_analysis_cannot_run_before_every_live_explanation_predicts_it():
    inquiry = Inquiry("q", explanations(), analyses())
    inquiry.declare("assayed", {"target": row(0.85), "coverage": row(0.05)})
    assert inquiry.missing("assayed") == ("nonspecific",)
    assert not inquiry.ready("assayed")
    refused("INQ110", inquiry.observe, "assayed", "enriched")


def test_an_outcome_nobody_declared_cannot_update_belief():
    inquiry = opened()
    refused("INQ130", inquiry.observe, "assayed", "weakly_enriched")


def test_an_analysis_is_observed_once_and_a_replicate_is_its_own_analysis():
    inquiry = opened()
    inquiry.observe("assayed", "enriched")
    refused("INQ131", inquiry.observe, "assayed", "enriched")


# ======================================== licensing constrains the likelihood itself

def clinical_question():
    named = [
        Explanation("effective", "the formula lowers HbA1c in adults with T2DM",
                    ClaimKind.EFFICACY, prior=0.3),
        Explanation("ineffective", "the formula does not lower HbA1c in adults with T2DM",
                    ClaimKind.EFFICACY, prior=0.3),
        Explanation("mechanism", "the constituents inhibit PTP1B",
                    MECH, prior=0.3),
    ]
    docking = [Analysis(f"dock-{i}", f"docking run {i}", StudyDesign.IN_SILICO, BINARY)
               for i in range(5)]
    trial = Analysis("trial", "a randomised trial", StudyDesign.RANDOMISED_TRIAL,
                     ("lower", "not_lower"))
    return Inquiry("Does the formula work in T2DM?", named, docking + [trial])


def test_a_docking_analysis_may_not_carry_a_prediction_about_efficacy():
    inquiry = clinical_question()
    error = refused("INQ113", inquiry.declare, "dock-0",
                    {"effective": row(0.9), "ineffective": row(0.2)})
    assert "in_silico analysis" in str(error) and "clinical_efficacy" not in str(error)
    assert "efficacy claim" in str(error)
    assert inquiry.grade("effective", "dock-0") is Licensing.UNLICENSED
    assert inquiry.grade("effective", "trial") is Licensing.DIRECT


def test_belief_in_efficacy_does_not_move_however_many_docking_runs_come_back():
    inquiry = clinical_question()
    for i in range(5):
        inquiry.declare(f"dock-{i}", {"mechanism": row(0.9)})
    before = inquiry.belief
    for i in range(5):
        update = inquiry.observe(f"dock-{i}", "enriched")
        assert set(update.held_fixed) == {"effective", "ineffective"}
    after = inquiry.belief
    assert after["effective"] == pytest.approx(before["effective"], abs=1e-12)
    assert after["ineffective"] == pytest.approx(before["ineffective"], abs=1e-12)
    # The runs redistribute mass only among the explanations they bear on: the mechanism
    # gains exactly what the catch-all loses, and nothing comes from efficacy.
    assert after["mechanism"] > before["mechanism"]
    assert after[CATCH_ALL] < before[CATCH_ALL]
    assert after["mechanism"] + after[CATCH_ALL] == pytest.approx(
        before["mechanism"] + before[CATCH_ALL], abs=1e-12)


def test_a_design_that_licenses_nothing_named_is_worth_nothing_about_them():
    inquiry = clinical_question()
    inquiry.add_analysis(Analysis("dock-x", "docking, efficacy only", SILICO, BINARY))
    # Nothing named can be predicted, so only the catch-all bears on it: zero bits.
    inquiry.declare("dock-x", {"mechanism": row(0.5)})
    assert inquiry.expected_gain("dock-x") == pytest.approx(0.0, abs=1e-12)


def test_efficacy_is_left_undecided_and_the_designs_that_could_decide_it_are_named():
    inquiry = clinical_question()
    for i in range(5):
        inquiry.declare(f"dock-{i}", {"mechanism": row(0.9)})
    conclusion = run_inquiry(inquiry, lambda a, s: Executed("enriched"))
    assert conclusion.verdict is not Verdict.ACCEPTED
    assert conclusion.leader_posterior < 0.95
    # The trial is the only analysis that could move the efficacy explanations, and
    # nobody has sealed a prediction for it: the engine asks rather than guessing.
    assert "trial" in inquiry.next_step().pending


# ======================================================== choosing what to run next

def test_the_standard_analysis_is_ranked_last_because_everyone_predicts_it():
    ranked = opened().ranked()
    assert ranked[0].analysis_id in ("assayed", "assayed-seed")
    assert ranked[-1].analysis_id == "reactome"
    assert ranked[-1].expected_gain < 0.1 < ranked[0].expected_gain


def test_the_engine_runs_the_most_informative_analysis_per_unit_cost():
    inquiry = Inquiry("q", explanations(), [
        Analysis("assayed", "assayed background", SILICO, BINARY, cost=10.0),
        Analysis("random", "random herbs", SILICO, SPECIFICITY, cost=1.0)])
    inquiry.declare("assayed", {"target": row(0.85), "coverage": row(0.05),
                                "nonspecific": row(0.85)})
    inquiry.declare("random", {"target": row(0.85, SPECIFICITY),
                               "coverage": row(0.5, SPECIFICITY),
                               "nonspecific": row(0.05, SPECIFICITY)})
    assert inquiry.expected_gain("assayed") > inquiry.expected_gain("random")
    step = inquiry.next_step()
    assert (step.action, step.analysis_id, step.purpose) == ("run", "random",
                                                              Purpose.DISCRIMINATE)


def test_nothing_worth_running_stops_the_inquiry_undetermined():
    rule = StoppingRule(min_gain=0.5)
    inquiry = Inquiry("q", explanations(), [analyses()[0]], rule)
    inquiry.declare("reactome", {h: row(0.9) for h in ("target", "coverage",
                                                       "nonspecific")})
    assert inquiry.next_step().action == "stop"
    conclusion = inquiry.conclude()
    assert conclusion.verdict is Verdict.UNDETERMINED and conclusion.final
    assert "bits per unit of cost" in conclusion.reason
    assert conclusion.certainty is Certainty.UNCERTAIN


def test_running_something_else_is_recorded_as_unrecommended():
    inquiry = opened()
    update = inquiry.observe("reactome", "enriched")
    assert update.purpose is Purpose.UNRECOMMENDED
    assert update.recommended in ("assayed", "assayed-seed")


# ======================================================================= severity

def test_identical_predictions_are_not_a_severe_test():
    """Regression: with no outcome favouring the leader the test 'fails it' with certainty
    under the rival, because it cannot pass it at all. Power is what catches that."""
    inquiry = opened()
    assert inquiry.severity("target", "nonspecific", "assayed") == pytest.approx(1.0)
    assert inquiry.power("target", "nonspecific", "assayed") == 0.0
    assert not inquiry.severe("target", "nonspecific", "assayed")
    assert inquiry.severe("target", "coverage", "assayed")
    assert inquiry.severe("target", "nonspecific", "random")


def test_a_leader_on_priors_alone_is_contradicted_before_anything_else():
    inquiry = opened(StoppingRule(accept_at=0.9), target=0.93, coverage=0.01,
                     nonspecific=0.01)
    step = inquiry.next_step()
    assert step.action == "run" and step.purpose is Purpose.CONTRADICT
    assert (step.against, step.analysis_id) == ("coverage", "assayed")
    assert step.severity >= 0.8
    # Accepting it here would accept a prior; the conclusion says so.
    assert inquiry.conclude().verdict is Verdict.PROVISIONAL


def test_the_accepted_answer_was_severely_tested_and_replicated():
    inquiry = opened()
    conclusion = run_inquiry(inquiry, world())
    assert conclusion.verdict is Verdict.ACCEPTED and conclusion.final
    assert conclusion.leader == "target"
    assert set(conclusion.severe_tests) == {"coverage", "nonspecific"}
    assert conclusion.replications["coverage"] == ("assayed-seed",)
    assert conclusion.replications["nonspecific"] == ("random-2",)
    # The analysis everyone runs was never worth running here.
    assert not inquiry.observed("reactome")


def test_no_replicate_leaves_the_leader_provisional():
    inquiry = Inquiry("q", explanations(), [a for a in analyses()
                                            if a.id not in ("assayed-seed",)])
    inquiry.declare("reactome", {h: row(0.9) for h in ("target", "coverage",
                                                       "nonspecific")})
    inquiry.declare("assayed", {"target": row(0.85), "coverage": row(0.05),
                                "nonspecific": row(0.85)})
    for a in ("random", "random-2"):
        inquiry.declare(a, {"target": row(0.85, SPECIFICITY),
                            "coverage": row(0.5, SPECIFICITY),
                            "nonspecific": row(0.05, SPECIFICITY)})
    conclusion = run_inquiry(inquiry, world())
    assert conclusion.leader_posterior >= 0.95
    assert conclusion.verdict is Verdict.PROVISIONAL and conclusion.final
    assert conclusion.unreplicated == ("coverage",) and not conclusion.untested
    assert "not replicated" in conclusion.reason
    assert conclusion.certainty is Certainty.TENTATIVE


def test_a_replication_that_went_the_other_way_is_named_in_the_verdict():
    inquiry = Inquiry("q", explanations(), analyses(), StoppingRule(accept_at=0.8))
    inquiry.declare("reactome", {h: row(0.9) for h in ("target", "coverage",
                                                       "nonspecific")})
    inquiry.declare("assayed", {"target": row(0.85), "coverage": row(0.05),
                                "nonspecific": row(0.85)})
    inquiry.declare("assayed-seed", {"target": row(0.7), "coverage": row(0.05),
                                     "nonspecific": row(0.7)})
    for a in ("random", "random-2"):
        inquiry.declare(a, {"target": row(0.85, SPECIFICITY),
                            "coverage": row(0.5, SPECIFICITY),
                            "nonspecific": row(0.05, SPECIFICITY)})
    conclusion = run_inquiry(inquiry, world(**{"assayed-seed": "not_enriched"}))
    assert [u.purpose for u in inquiry.updates][-2:] == [Purpose.REPLICATE,
                                                          Purpose.REPLICATE]
    assert conclusion.leader == "target" and conclusion.leader_posterior >= 0.8
    assert conclusion.verdict is Verdict.PROVISIONAL
    assert conclusion.unreplicated == ("coverage",)
    assert "replications that did not favour it: assayed-seed" in conclusion.reason


def test_confirmatory_analyses_raise_a_posterior_and_cannot_close_an_inquiry():
    """Twenty analyses that all favour the leader a little. The posterior climbs past the
    threshold; none of them would have failed the leader had a rival been true.

    ``min_gain=0`` lets the engine keep running them: by default it stops earlier, when a
    weak analysis is no longer worth its cost, which is the same verdict reached sooner.
    """
    inquiry = Inquiry("q", explanations(), [
        Analysis(f"weak-{i}", f"a weakly informative analysis {i}", SILICO, BINARY)
        for i in range(20)], StoppingRule(min_gain=0.0))
    for i in range(20):
        inquiry.declare(f"weak-{i}", {"target": row(0.6), "coverage": row(0.4),
                                      "nonspecific": row(0.4)})
    conclusion = run_inquiry(inquiry, lambda a, s: Executed("enriched"))
    assert conclusion.leader == "target" and conclusion.leader_posterior >= 0.95
    assert conclusion.verdict is Verdict.PROVISIONAL
    assert set(conclusion.untested) == {"coverage", "nonspecific"}
    assert conclusion.certainty.rank >= Certainty.TENTATIVE.rank


# ============================================ the posterior is not a licence

def test_a_high_posterior_from_in_silico_work_licenses_a_tentative_hypothesis():
    conclusion = run_inquiry(opened(), world())
    assert conclusion.leader_posterior > 0.95
    assert conclusion.claim_kind is MECH
    assert conclusion.licensing is Licensing.DIRECT
    assert conclusion.designs == (SILICO,)
    assert conclusion.certainty is Certainty.TENTATIVE
    assert any("conditional on the predictions" in x for x in conclusion.limitations)


def test_an_artefact_conclusion_is_a_finding_about_the_analysis():
    conclusion = run_inquiry(opened(), world(**{"assayed": "not_enriched",
                                                "assayed-seed": "not_enriched"}))
    assert conclusion.leader == "coverage"
    assert conclusion.role is Role.ARTEFACT
    assert conclusion.claim_kind is None and conclusion.licensing is None


def test_evidence_that_reaches_a_claim_only_by_extrapolation_says_so():
    named = [Explanation("signal", "the herb raises ALT in people", ClaimKind.SAFETY_SIGNAL,
                         prior=0.45),
             Explanation("noise", "the ALT rise is a batch effect", role=Role.ARTEFACT,
                         prior=0.45)]
    # Three studies, because a uniform catch-all caps each binary outcome's likelihood
    # ratio against it at 2: two concordant studies leave "something else" at 0.06.
    rats = [Analysis("rat", "a rat hepatotoxicity study", StudyDesign.ANIMAL,
                     ("raised", "normal")),
            Analysis("rat-2", "a second rat study", StudyDesign.ANIMAL,
                     ("raised", "normal"), replicates="rat"),
            Analysis("dog", "a dog hepatotoxicity study", StudyDesign.ANIMAL,
                     ("raised", "normal"))]
    inquiry = Inquiry("Does the herb raise ALT?", named, rats)
    for a in ("rat", "rat-2", "dog"):
        inquiry.declare(a, {"signal": {"raised": 0.9, "normal": 0.1},
                            "noise": {"raised": 0.05, "normal": 0.95}})
    assert inquiry.grade("signal", "rat") is Licensing.EXTRAPOLATED
    conclusion = run_inquiry(inquiry, lambda a, s: Executed("raised"))
    assert conclusion.verdict is Verdict.ACCEPTED
    assert conclusion.licensing is Licensing.EXTRAPOLATED
    assert any("extrapolation" in x for x in conclusion.limitations)
    assert conclusion.certainty is Certainty.TENTATIVE


def test_stopping_early_is_visible_in_the_conclusion():
    inquiry = opened()
    inquiry.observe("assayed", "enriched")
    conclusion = inquiry.conclude()
    assert not conclusion.final
    assert "stopped before the stopping rule was met" in conclusion.reason
    assert conclusion.verdict is not Verdict.ACCEPTED


# ================================================ the catch-all and new explanations

def surprising():
    inquiry = opened()
    inquiry.add_analysis(Analysis("dose", "dose dependence", SILICO, ("monotone", "flat")))
    inquiry.declare("dose", {"target": {"monotone": 0.9, "flat": 0.1},
                             "coverage": {"monotone": 0.9, "flat": 0.1},
                             "nonspecific": {"monotone": 0.9, "flat": 0.1}})
    return inquiry


def test_outcomes_nobody_predicted_move_belief_to_the_catch_all():
    inquiry = surprising()
    inquiry.observe("assayed", "not_enriched")
    inquiry.observe("random", "specific")       # coverage leads, then is surprised
    inquiry.observe("dose", "flat")
    inquiry.observe("reactome", "not_enriched")
    assert inquiry.belief[CATCH_ALL] >= 0.5
    step = inquiry.next_step()
    assert step.action == "propose"
    assert inquiry.conclude().verdict is Verdict.NEEDS_HYPOTHESES


def test_a_new_explanation_earns_no_credit_for_the_data_it_was_invented_to_fit():
    inquiry = surprising()
    for a, o in (("assayed", "not_enriched"), ("random", "specific"), ("dose", "flat"),
                 ("reactome", "not_enriched")):
        inquiry.observe(a, o)
    mass = inquiry.belief[CATCH_ALL]
    late = Explanation("binding-site", "the activity is assay interference",
                       role=Role.ARTEFACT)
    inquiry.admit([late], {"binding-site": 0.6})
    assert inquiry.belief["binding-site"] == pytest.approx(0.6 * mass)
    assert inquiry.belief[CATCH_ALL] == pytest.approx(0.4 * mass)
    assert math.fsum(inquiry.belief.values()) == pytest.approx(1.0)
    # It must predict the analyses still to run before any of them can.
    assert "binding-site" in inquiry.missing("assayed-seed")
    refused("INQ110", inquiry.observe, "assayed-seed", "not_enriched")


def test_a_new_explanation_cannot_close_the_world_either():
    inquiry = opened()
    late = Explanation("late", "a late idea", role=Role.ARTEFACT)
    refused("INQ140", inquiry.admit, [late], {"late": 0.99})
    refused("INQ140", inquiry.admit, [Explanation("late", "a prior of its own",
                                                  role=Role.ARTEFACT, prior=0.1)],
            {"late": 0.5})
    refused("INQ140", inquiry.admit, [late], {"other": 0.5})


def test_the_model_proposes_and_the_inquiry_continues():
    inquiry = surprising()
    for a, o in (("assayed", "not_enriched"), ("random", "specific"), ("dose", "flat"),
                 ("reactome", "not_enriched")):
        inquiry.observe(a, o)

    def propose(inq, step):
        inq.admit([Explanation("interference", "constituents interfere with the assay",
                               role=Role.ARTEFACT)], {"interference": 0.9})
        inq.declare("assayed-seed", {"interference": row(0.02)})
        inq.declare("random-2", {"interference": row(0.9, SPECIFICITY)})

    conclusion = run_inquiry(inquiry, world(**{"assayed-seed": "not_enriched"}),
                             propose=propose)
    assert "interference" in conclusion.posterior
    assert conclusion.posterior["interference"] > 0.0


# ============================================================ the trail is the state

def test_the_trail_round_trips_and_recomputes():
    inquiry = opened()
    run_inquiry(inquiry, world())
    copy_ = Inquiry.from_dict(inquiry.as_dict())
    assert copy_.head == inquiry.head
    assert copy_.belief == pytest.approx(inquiry.belief)
    assert copy_.conclude().as_dict()["digest"] == inquiry.conclude().as_dict()["digest"]


@pytest.mark.parametrize("tamper", [
    lambda d: d["trail"][3]["body"]["predictions"]["target"].update(enriched=0.95,
                                                                   not_enriched=0.05),
    lambda d: next(e for e in d["trail"] if e["event"] == "observed")["body"].update(
        outcome="not_enriched"),
    lambda d: d["belief"].update(target=0.999),
    lambda d: d["trail"].pop(2),
    lambda d: d.update(head="0" * 64),
])
def test_a_trail_that_does_not_recompute_is_refused(tamper):
    inquiry = opened()
    run_inquiry(inquiry, world())
    data = copy.deepcopy(inquiry.as_dict())
    tamper(data)
    refused("INQ150", Inquiry.from_dict, data)


def test_a_rewritten_trail_with_fresh_digests_still_does_not_recompute():
    """Recomputing the hash chain is easy; making the recorded belief follow from the
    recorded predictions and outcomes is not."""
    import hashlib
    import json

    def digest(entry):
        body = {k: entry[k] for k in ("seq", "event", "body", "prev")}
        return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"),
                                         ensure_ascii=False).encode()).hexdigest()

    inquiry = opened()
    run_inquiry(inquiry, world())
    data = copy.deepcopy(inquiry.as_dict())
    observed = next(e for e in data["trail"] if e["event"] == "observed")
    observed["body"]["posterior"]["target"] = 0.99
    prev = ""
    for entry in data["trail"]:
        entry["prev"] = prev
        entry["digest"] = digest(entry)
        prev = entry["digest"]
    data["head"] = prev
    refused("INQ150", Inquiry.from_dict, data)


def test_a_refusing_recorder_leaves_the_inquiry_where_it_was():
    class Gateway:
        refuse = False
        events: list = []

        def record(self, inquiry, event, entry):
            if self.refuse:
                raise PermissionError("the persistence gateway refused")
            self.events.append(event)

    gateway = Gateway()
    inquiry = Inquiry("q", explanations(), analyses(), recorder=gateway)
    declare_all(inquiry)
    before = (inquiry.belief, inquiry.head, len(inquiry.trail))
    gateway.refuse = True
    with pytest.raises(PermissionError):
        inquiry.observe("assayed", "enriched")
    assert (inquiry.belief, inquiry.head, len(inquiry.trail)) == before
    assert not inquiry.observed("assayed")
    assert gateway.events[0] == "opened" and gateway.events.count("declared") == 5


# ========================================================================= budget

def test_the_budget_is_a_hard_stop():
    inquiry = opened(StoppingRule(budget=2.0))
    inquiry.observe("assayed", "enriched")
    inquiry.observe("random", "specific")
    refused("INQ132", inquiry.observe, "assayed-seed", "enriched")
    assert inquiry.next_step().action == "stop"
    conclusion = inquiry.conclude()
    assert conclusion.verdict in (Verdict.PROVISIONAL, Verdict.UNDETERMINED)
    assert conclusion.spent == 2.0


def test_every_refusal_code_is_registered_with_a_remedy():
    import inspect
    import re

    from psh.scientist import inquiry as module

    raised = set(re.findall(r'InquiryRefused\(\s*"(INQ\d+)"', inspect.getsource(module)))
    assert raised == set(INQUIRY_CODES)
    assert all(title and remedy for title, remedy in INQUIRY_CODES.values())


def test_expected_information_gain_matches_the_textbook_case():
    # A fair coin between two explanations, a perfectly discriminating test: one bit.
    belief = {"a": 0.5, "b": 0.5}
    perfect = {"a": {"x": 1.0, "y": 0.0}, "b": {"x": 0.0, "y": 1.0}}
    assert expected_information_gain(belief, perfect) == pytest.approx(1.0)
    useless = {"a": {"x": 0.5, "y": 0.5}, "b": {"x": 0.5, "y": 0.5}}
    assert expected_information_gain(belief, useless) == pytest.approx(0.0)


# ================================================================ withdrawal

def test_an_analysis_that_cannot_run_is_withdrawn_not_given_an_outcome():
    from psh.scientist import AnalysisUnavailable

    def execute(analysis, step):
        if analysis.id.startswith("random"):
            raise AnalysisUnavailable("too few herbs outside the formula have recorded "
                                      "compounds")
        return world()(analysis, step)

    inquiry = opened()
    before = inquiry.belief
    conclusion = run_inquiry(inquiry, execute)
    assert set(inquiry.withdrawn) >= {"random"}
    assert not inquiry.observed("random")
    # Without the specificity test, nonspecific cannot be ruled out: the leader is not
    # accepted, and the conclusion says what could not be run.
    assert conclusion.verdict is not Verdict.ACCEPTED
    assert any("random could not be run" in x for x in conclusion.limitations)
    refused("INQ133", inquiry.observe, "random", "specific")
    refused("INQ133", inquiry.withdraw, "assayed", "after the fact")
    assert Inquiry.from_dict(inquiry.as_dict()).withdrawn == inquiry.withdrawn
    assert before != inquiry.belief
