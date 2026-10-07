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
        out[scenario] = run_pathway_inquiry(snaps, pathway=PATHWAY, root=root / "work",
                                            params=FAST, state_dir=root / "state")
    return out


@pytest.mark.parametrize("scenario", SCENARIOS)
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


def test_the_engine_spends_fewer_runs_than_the_full_checklist(runs):
    for result in runs.values():
        assert 0 < result.np_runs < result.np_runs_if_everything
    # Where the first analysis already refutes the mechanism, the engine stops early.
    assert runs["coverage"].np_runs < runs["selective"].np_runs


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
    assert text.splitlines()[2].startswith("**Verdict: accepted.**")
    assert "Not run" in text and "governed_execution: false" in text
