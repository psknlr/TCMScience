"""The governed inquiry on planted worlds: does it find the explanation that is true?

Each planted world makes one explanation of "葛根芩连汤's measured targets concentrate in
pathway A" true (``bioagent.research.planted``). The inquiry is run on each, unchanged,
with the same sealed predictions, and must accept the true explanation, release a
mechanism claim only where the mechanism is the true explanation, and say what it could
not run.
"""

from __future__ import annotations

import pytest

from bioagent.analysis.network_pharmacology import Parameters
from bioagent.research.inquiry import (ANALYSES, pathway_inquiry, predictions,
                                       render_markdown, run_pathway_inquiry)
from bioagent.research.planted import PATHWAY, SCENARIOS, TRUTH, build_world
from bioagent.sources.herbs import GEGEN_QINLIAN

FAST = Parameters(permutations=200)


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    out = {}
    for scenario in SCENARIOS:
        root = tmp_path_factory.mktemp(scenario)
        snaps = build_world(scenario, root / "snap")
        replica = build_world(scenario, root / "independent", experiment=1)
        out[scenario] = run_pathway_inquiry(snaps, pathway=PATHWAY, root=root / "work",
                                            params=FAST, state_dir=root / "state",
                                            replication_snapshots=replica)
    return out


@pytest.mark.parametrize("scenario", ["selective", "promiscuous"])
def test_the_inquiry_accepts_the_explanation_the_world_makes_true(runs, scenario):
    conclusion = runs[scenario].conclusion
    assert conclusion.accepted and conclusion.final
    assert conclusion.leader == TRUTH[scenario]
    assert conclusion.leader_posterior >= 0.95


def test_a_mechanism_claim_is_released_only_where_the_mechanism_is_true(runs):
    selective = runs["selective"]
    assert [r["object"] for r in selective.released] == [PATHWAY]
    assert selective.conclusion.claim_kind.value == "mechanism_hypothesis"
    assert selective.conclusion.certainty.value == "tentative"
    for scenario in ("coverage", "promiscuous"):
        assert runs[scenario].released == ()
        assert runs[scenario].conclusion.claim_kind is None   # a finding about the analysis
        assert "no mechanism claim" in runs[scenario].refused[0]


def test_an_all_hit_background_cannot_explain_the_signal(runs):
    r = runs["coverage"]
    assert r.conclusion.final and not r.conclusion.accepted
    assert "no attainable enrichment" in r.inquiry.withdrawn["assayed"]
    assert not r.conclusion.replications and not r.released
    assert r.np_runs == 3


def test_absent_pathway_never_updates_belief(tmp_path):
    s = build_world("selective", tmp_path/"snap")
    r = run_pathway_inquiry(s, pathway="reactome:absent", root=tmp_path/"work", params=FAST)
    assert not r.inquiry.updates and not r.released and not r.conclusion.accepted


@pytest.mark.parametrize("supply_same", [False, True])
def test_reseeding_or_reciting_the_same_dataset_is_not_replication(tmp_path, supply_same):
    s = build_world("selective", tmp_path/"snap")
    r = run_pathway_inquiry(s, pathway=PATHWAY, root=tmp_path/"work", params=FAST,
                            replication_snapshots=s if supply_same else ())
    assert not r.conclusion.accepted and not r.conclusion.replications and not r.released


def test_the_usual_analysis_is_worth_almost_nothing_before_anything_runs():
    inquiry = pathway_inquiry(PATHWAY, GEGEN_QINLIAN)
    gains = {a.id: inquiry.expected_gain(a.id) for a in inquiry.analyses}
    assert gains["annotated"] < 0.1 < gains["assayed"]
    assert inquiry.next_step().analysis_id == "assayed"


def test_every_prediction_is_sealed_before_the_first_analysis(runs):
    trail = runs["selective"].inquiry.trail
    events = [e["event"] for e in trail]
    first = events.index("observed")
    assert events[:first].count("declared") == len(ANALYSES)
    assert "declared" not in events[first:]


def test_the_trail_replays_and_the_audit_chain_carries_it(runs, tmp_path):
    from psh.scientist import Inquiry
    result = runs["selective"]
    replayed = Inquiry.from_dict(result.inquiry.as_dict())
    assert replayed.conclude().as_dict()["digest"] == result.conclusion.as_dict()["digest"]
    assert result.audit_head


def test_without_control_herbs_the_specificity_test_is_withdrawn_and_nothing_released(
        tmp_path):
    snaps = build_world("selective", tmp_path / "snap", controls=False)
    result = run_pathway_inquiry(snaps, pathway=PATHWAY, root=tmp_path / "work",
                                 params=FAST)
    assert {"random-herbs", "random-herbs-2"} <= set(result.inquiry.withdrawn)
    assert not result.conclusion.accepted
    assert result.released == ()
    assert any("could not be run" in x for x in result.conclusion.limitations)


def test_predictions_cover_every_analysis_and_explanation():
    for analysis_id, (_, outcomes, _, _) in ANALYSES.items():
        rows = predictions(analysis_id)
        assert set(rows) == {"target", "coverage", "promiscuity"}
        for row in rows.values():
            assert set(row) == set(outcomes)
            assert sum(row.values()) == pytest.approx(1.0)


def test_the_report_leads_with_the_verdict_and_names_what_did_not_run(runs):
    text = render_markdown(runs["coverage"])
    assert text.splitlines()[2].startswith("**Verdict: undetermined.**")
    assert "no attainable enrichment" in text and "governed_execution: false" in text


def test_small_null_ensemble_cannot_establish_five_percent():
    from psh.scientist import AnalysisUnavailable
    from bioagent.research.inquiry import _null_frequency
    with pytest.raises(AnalysisUnavailable, match="straddle"):
        _null_frequency(0, 20)
    assert _null_frequency(0, 60)[0]
    assert not _null_frequency(60, 60)[0]
