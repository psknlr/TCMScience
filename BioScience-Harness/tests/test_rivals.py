"""The rival-explanation scaffolds: every rival can win, and a wrong one rarely does.

Each scaffold is checked the way the pathway inquiry is checked on its planted worlds, but
with the worlds drawn from the scaffold's own sealed predictions: a world in which one
rival is true produces that rival's most likely outcome for every test (it must then be
accepted), or outcomes drawn at random from its predictions (a wrong rival must then be
accepted in at most 5% of worlds). The classifiers are checked on results built for each
outcome, and on results that do not meet the conditions a test was sealed with.
"""

from __future__ import annotations

import random

import pytest

from bioagent.research import rivals
from bioagent.research.inquiry import ANALYSES, predictions
from psh.scientist import Executed, Role, run_inquiry

VALUES = {
    "pathway_signal": {"formula": "葛根芩连汤", "pathway": "R-HSA-1234567"},
    "formula_vs_constituent": {"formula": "葛根芩连汤", "constituent": "berberine",
                               "endpoint": "fasting glucose", "margin": 0.5,
                               "better": "lower"},
    "combination": {"agent_a": "黄连", "agent_b": "吴茱萸", "endpoint": "HepG2 growth",
                    "reference": "bliss"},
    "exposure": {"molecule": "baicalin", "target": "PTP1B", "site": "plasma",
                 "metabolite": "baicalein"},
    "efficacy": {"intervention": "葛根芩连汤", "outcome": "HbA1c at 12 weeks",
                 "population": "adults with type 2 diabetes", "comparator": "placebo",
                 "scale": "raw", "better": "lower", "mcid": 0.3},
    "safety_signal": {"herb": "何首乌", "event": "drug-induced liver injury"},
    "herb_drug_interaction": {"herb": "丹参", "drug": "warfarin", "marker": "INR",
                              "threshold": 0.5},
    "target_engagement": {"compound": "quercetin", "target": "PTGS2"},
}
RIVALS = [(sid, r.id) for sid, s in rivals.SCAFFOLDS.items() for r in s.rivals]


def test_every_scaffold_has_values_here():
    assert set(VALUES) == set(rivals.SCAFFOLDS)


@pytest.mark.parametrize("scaffold_id,truth", RIVALS)
def test_each_rival_is_accepted_in_the_world_that_makes_it_true(scaffold_id, truth):
    s = rivals.scaffold(scaffold_id)
    modal = {t.id: max(t.predictions[truth], key=t.predictions[truth].get) for t in s.tests}
    inquiry = rivals.open_scaffold(scaffold_id, VALUES[scaffold_id])
    conclusion = run_inquiry(inquiry, lambda a, step: Executed(modal[a.id]))
    assert conclusion.accepted and conclusion.final, conclusion.reason
    assert conclusion.leader == truth
    assert conclusion.leader_posterior >= 0.95


@pytest.mark.parametrize("scaffold_id", list(rivals.SCAFFOLDS))
def test_every_pair_of_rivals_has_a_severe_test_and_its_replicate(scaffold_id):
    s = rivals.scaffold(scaffold_id)
    inquiry = rivals.open_scaffold(scaffold_id, VALUES[scaffold_id])
    ids = [r.id for r in s.rivals]
    for leader in ids:
        for rival in ids:
            if leader == rival:
                continue
            severe = {t.id for t in s.tests if inquiry.severe(leader, rival, t.id)}
            assert severe, f"nothing tests {leader} severely against {rival}"
            assert any(t.replicates in severe for t in s.tests), (
                f"no replicate of a severe test of {leader} against {rival}")


@pytest.mark.parametrize("scaffold_id,truth", RIVALS)
def test_a_wrong_rival_is_accepted_in_at_most_five_percent_of_noisy_worlds(scaffold_id,
                                                                           truth):
    s = rivals.scaffold(scaffold_id)
    rng = random.Random(f"{scaffold_id}:{truth}")
    rows = {t.id: t.predictions[truth] for t in s.tests}
    wrong = 0
    for _ in range(100):
        inquiry = rivals.open_scaffold(scaffold_id, VALUES[scaffold_id])
        c = run_inquiry(inquiry, lambda a, step: Executed(
            rng.choices(list(rows[a.id]), weights=list(rows[a.id].values()))[0]))
        wrong += c.accepted and c.leader != truth
    assert wrong <= 5


@pytest.mark.parametrize("scaffold_id", list(rivals.SCAFFOLDS))
def test_every_test_predicts_for_every_rival_over_exactly_its_outcomes(scaffold_id):
    s = rivals.scaffold(scaffold_id)
    ids = {r.id for r in s.rivals}
    tests = {t.id for t in s.tests}
    assert abs(sum(r.prior for r in s.rivals) - 0.9) < 1e-9     # the catch-all keeps 0.1
    for t in s.tests:
        assert set(t.predictions) == ids, t.id
        for row in t.predictions.values():
            assert set(row) == set(t.outcomes) and abs(sum(row.values()) - 1) < 1e-9
        assert not t.replicates or t.replicates in tests
        if t.replicates:
            original = next(o for o in s.tests if o.id == t.replicates)
            assert original.outcomes == t.outcomes
        assert not t.classifier or t.classifier in rivals.CLASSIFIERS
    inquiry = rivals.open_scaffold(scaffold_id, VALUES[scaffold_id])
    for e in inquiry.explanations:
        if e.role is Role.SUBSTANTIVE:
            # licensing lets every test move every substantive rival: none is held fixed
            assert all(inquiry.bears_on(e.id, t) for t in tests), e.id


def test_the_pathway_scaffold_seals_what_the_pathway_inquiry_seals():
    s = rivals.scaffold("pathway_signal")
    assert [t.id for t in s.tests] == list(ANALYSES)
    for t in s.tests:
        assert {h: dict(r) for h, r in t.predictions.items()} == predictions(t.id)


def test_criteria_are_sealed_into_the_trail():
    inquiry = rivals.open_scaffold("formula_vs_constituent",
                                   VALUES["formula_vs_constituent"])
    titles = {a["id"]: a["title"] for a in inquiry.trail[0]["body"]["analyses"]}
    assert "±0.5" in titles["contrast"] and "lower is better" in titles["contrast"]
    assert rivals.matches(inquiry, "formula_vs_constituent",
                          VALUES["formula_vs_constituent"])
    assert not rivals.matches(inquiry, "formula_vs_constituent",
                              {**VALUES["formula_vs_constituent"], "margin": 1.0})


def test_parameters_are_checked_and_completed():
    with pytest.raises(ValueError) as exc:
        rivals.parameters("formula_vs_constituent", {"formula": "x", "margin": -1,
                                                     "model": "human", "dose": 3})
    message = str(exc.value)
    for problem in ("unknown parameter 'dose'", "constituent is required",
                    "margin must be a positive number", "model must be one of"):
        assert problem in message
    done = rivals.parameters("formula_vs_constituent", {
        "formula": "x", "constituent": "y", "endpoint": "z", "margin": 2})
    assert done["better"] == "higher" and done["model"] == "animal" and done["margin"] == 2.0
    with pytest.raises(ValueError):
        rivals.scaffold("no_such_scaffold")


def test_describe_gives_every_scaffold_in_brief_and_one_in_full():
    brief = rivals.describe()
    assert [b["id"] for b in brief] == list(rivals.SCAFFOLDS)
    full = rivals.describe("efficacy")
    tests = {t["id"]: t for t in full["tests"]}
    assert tests["all_trials"]["classified_by"] == "runtime"
    assert "native.meta_analysis" in tests["all_trials"]["data"]
    assert rivals.GAP_NOTE in full["notes"]
    assert {r["role"] for r in full["rivals"]} == {"substantive", "artefact"}


# ------------------------------------------------------------------ classifiers

FVC = VALUES["formula_vs_constituent"]


def _contrast_args(**kw):
    return {"formula": {}, "monomer": {}, "higher_is_better": False, "margin": 0.5,
            "dose_matched": True, **kw}


@pytest.mark.parametrize("verdict", ["formula_exceeds", "equivalent", "monomer_exceeds",
                                     "inconclusive"])
def test_the_contrast_classifier_reads_the_verdict(verdict):
    outcome, _ = rivals.classify("formula_vs_constituent", "contrast", FVC,
                                 _contrast_args(), {"verdict": verdict})
    assert outcome == verdict


@pytest.mark.parametrize("change,why", [
    ({"margin": None}, "margin"), ({"margin": 1.0}, "margin"),
    ({"higher_is_better": True}, "lower is better"), ({"alpha": 0.2}, "alpha"),
    ({"dose_matched": None}, "dose_matched"), ({"dose_matched": False}, "dose_matched")])
def test_the_contrast_classifier_refuses_what_was_not_sealed(change, why):
    with pytest.raises(rivals.Unclassifiable, match=why):
        rivals.classify("formula_vs_constituent", "contrast", FVC, _contrast_args(**change),
                        {"verdict": "formula_exceeds"})


def _cells(cyto=True):
    c = (lambda v: {"cytotoxicity": [v]}) if cyto else (lambda v: {})
    return [{"dose_a": 1, "dose_b": 0, "effect": [0.2]}, {"dose_a": 0, "dose_b": 1,
                                                          "effect": [0.2]},
            {"dose_a": 1, "dose_b": 1, "effect": [0.6], **c(0.1)}]


@pytest.mark.parametrize("exceeds,outcome", [
    ({"cells": 1, "of": 1, "selective": 1}, "selective_excess"),
    ({"cells": 1, "of": 1, "selective": 0}, "cytotoxic_excess"),
    ({"cells": 0, "of": 1, "selective": 0}, "no_excess")])
def test_the_combination_classifier(exceeds, outcome):
    got, _ = rivals.classify("combination", "matrix", VALUES["combination"],
                             {"cells": _cells(), "primary": "bliss"},
                             {"exceeds_reference": exceeds})
    assert got == outcome


def test_the_combination_classifier_needs_cytotoxicity_and_the_sealed_reference():
    result = {"exceeds_reference": {"cells": 1, "of": 1, "selective": 1}}
    with pytest.raises(rivals.Unclassifiable, match="cytotoxicity"):
        rivals.classify("combination", "matrix", VALUES["combination"],
                        {"cells": _cells(cyto=False), "primary": "bliss"}, result)
    with pytest.raises(rivals.Unclassifiable, match="bliss"):
        rivals.classify("combination", "matrix", VALUES["combination"],
                        {"cells": _cells(), "primary": "hsa"}, result)
    with pytest.raises(rivals.Unclassifiable, match="cytotoxic_above"):
        rivals.classify("combination", "matrix", VALUES["combination"],
                        {"cells": _cells(), "primary": "bliss", "cytotoxic_above": 0.99},
                        result)


def _pair(molecule, verdict, target="PTP1B", reason=""):
    return {"molecule": molecule, "target": target, "verdict": verdict, "reason": reason}


@pytest.mark.parametrize("verdicts,outcome", [
    (["plausible", "plausible"], "plausible"), (["implausible"], "implausible"),
    (["plausible", "implausible"], "depends_on_unknowns"),
    (["depends_on_unknowns"], "depends_on_unknowns"),
    (["implausible", "undeterminable"], "implausible")])
def test_the_exposure_classifier_reads_only_this_molecule_on_this_target(verdicts, outcome):
    pairs = [_pair("baicalin", v) for v in verdicts]
    pairs += [_pair("baicalein", "plausible"), _pair("baicalin", "plausible", "AMPK")]
    got, read = rivals.classify("exposure", "parent", VALUES["exposure"], {"site": "plasma"},
                                {"pairs": pairs})
    assert got == outcome and read["pairs"] == len(verdicts)
    got, _ = rivals.classify("exposure", "metabolite", VALUES["exposure"],
                             {"site": "plasma"}, {"pairs": pairs})
    assert got == "plausible"


def test_the_exposure_classifier_refuses_what_it_cannot_judge():
    values = VALUES["exposure"]
    with pytest.raises(rivals.Unclassifiable, match="no pair could be judged"):
        rivals.classify("exposure", "parent", values, {"site": "plasma"},
                        {"pairs": [_pair("baicalin", "undeterminable", reason="no exposure"),
                                   _pair("baicalin", "inactive_in_assay")]})
    with pytest.raises(rivals.Unclassifiable, match="site"):
        rivals.classify("exposure", "parent", values, {"site": "tissue:UBERON_0002107"},
                        {"pairs": [_pair("baicalin", "plausible")]})
    with pytest.raises(rivals.Unclassifiable, match="no screened pair is of baicalin"):
        rivals.classify("exposure", "parent", values, {"site": "plasma"},
                        {"pairs": [_pair("wogonin", "plausible")]})
    unnamed = {k: v for k, v in values.items() if k != "metabolite"}
    with pytest.raises(rivals.Unclassifiable, match="without naming the metabolite"):
        rivals.classify("exposure", "metabolite", unnamed, {"site": "plasma"},
                        {"pairs": [_pair("baicalein", "plausible")]})


@pytest.mark.parametrize("better,low,high,mcid,outcome", [
    ("lower", -0.9, -0.4, 0.3, "benefit"),
    ("lower", -0.25, -0.05, 0.3, "no_important_benefit"),     # significant, unimportant
    ("lower", -0.2, 0.1, 0.3, "no_important_benefit"),
    ("lower", -0.5, 0.1, 0.3, "inconclusive"),
    ("lower", -0.2, 0.1, None, "inconclusive"),               # no margin: absence unshown
    ("higher", 0.4, 0.9, 0.3, "benefit"),
    ("higher", -0.1, 0.2, 0.3, "no_important_benefit"),
    ("higher", -0.1, 0.5, 0.3, "inconclusive")])
def test_the_meta_analysis_classifier(better, low, high, mcid, outcome):
    values = {**VALUES["efficacy"], "better": better, "mcid": mcid}
    got, _ = rivals.classify("efficacy", "all_trials", values, {"scale": "raw"},
                             {"k": 3, "random_effects": {"ci_low": low, "ci_high": high}})
    assert got == outcome


def test_the_meta_analysis_classifier_keeps_the_sealed_scale():
    with pytest.raises(rivals.Unclassifiable, match="raw scale"):
        rivals.classify("efficacy", "all_trials", VALUES["efficacy"], {"scale": "log"},
                        {"random_effects": {"ci_low": -1, "ci_high": -0.5}})


@pytest.mark.parametrize("ratio,low,high,outcome", [
    (1.0, 0.9, 1.1, "unchanged"), (2.0, 1.6, 2.5, "changed"), (0.5, 0.4, 0.62, "changed"),
    (1.15, 1.05, 1.3, "inconclusive"), (1.3, 0.9, 1.9, "inconclusive")])
def test_the_ratio_classifier_uses_the_no_effect_limits(ratio, low, high, outcome):
    got, _ = rivals.classify("herb_drug_interaction", "clinical_pk",
                             VALUES["herb_drug_interaction"],
                             {"ratio": ratio, "ci_low": low, "ci_high": high})
    assert got == outcome


@pytest.mark.parametrize("estimate,low,high,outcome", [
    (0.1, -0.2, 0.3, "unchanged"), (0.8, 0.6, 1.0, "changed"), (-0.9, -1.2, -0.6, "changed"),
    (0.3, 0.1, 0.4, "unchanged"),          # significant, but within the relevant change
    (0.3, 0.1, 0.7, "inconclusive"), (0.4, -0.3, 1.1, "inconclusive")])
def test_the_difference_classifier_uses_the_sealed_threshold(estimate, low, high, outcome):
    got, _ = rivals.classify("herb_drug_interaction", "clinical_pd",
                             VALUES["herb_drug_interaction"],
                             {"estimate": estimate, "ci_low": low, "ci_high": high})
    assert got == outcome


def test_published_numbers_must_be_an_interval_around_the_estimate():
    with pytest.raises(rivals.Unclassifiable):
        rivals.classify("herb_drug_interaction", "clinical_pk",
                        VALUES["herb_drug_interaction"],
                        {"ratio": 2.0, "ci_low": 0.9, "ci_high": 1.1})
    with pytest.raises(rivals.Unclassifiable):
        rivals.classify("herb_drug_interaction", "clinical_pd",
                        VALUES["herb_drug_interaction"], {"estimate": "big"})


def test_a_test_the_agent_classifies_has_no_runtime_rule():
    with pytest.raises(rivals.Unclassifiable, match="not a runtime-classified test"):
        rivals.classify("safety_signal", "dechallenge", VALUES["safety_signal"], {}, {})
