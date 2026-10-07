"""Properties of the inquiry engine, over generated explanations, predictions and outcomes.

The example tests pin decisions; these pin the arithmetic those decisions rest on, for
every table the generator can produce rather than for the ones an author thought of:

* information gain is never negative and never more than the belief's entropy or the
  outcome's capacity;
* belief is a martingale: averaged over the outcomes the engine itself predicts, the
  posterior is the prior, so no analysis is expected to move belief in a chosen direction;
* an explanation the analysis's design cannot license is held exactly where it was;
* a severe test, once passed, carries at least the likelihood ratio the rule promises;
* a trail replays to the same belief.
"""

from __future__ import annotations

import copy
import math

import pytest

hypothesis = pytest.importorskip("hypothesis")
from hypothesis import HealthCheck, given, settings  # noqa: E402
from hypothesis import strategies as st  # noqa: E402

from psh.scientist import (  # noqa: E402
    CATCH_ALL, Analysis, Explanation, Inquiry, Role, expected_information_gain,
)
from psh.sir.values import ClaimKind, StudyDesign  # noqa: E402

SETTINGS = settings(max_examples=60, deadline=None,
                    suppress_health_check=[HealthCheck.too_slow])


def _normalise(weights):
    total = math.fsum(weights)
    return [w / total for w in weights]


@st.composite
def distributions(draw, size):
    weights = draw(st.lists(st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
                            min_size=size, max_size=size))
    if math.fsum(weights) <= 1e-6:
        weights = [1.0] + [0.0] * (size - 1)
    return _normalise(weights)


@st.composite
def inquiries(draw):
    """A random inquiry: 2-4 named explanations (one an efficacy claim that an in-silico
    analysis cannot reach), 1-4 analyses of 2-4 outcomes, every licensed prediction sealed."""
    named = draw(st.integers(min_value=2, max_value=4))
    weights = draw(st.lists(st.floats(min_value=0.05, max_value=1.0), min_size=named + 1,
                            max_size=named + 1))
    priors = _normalise(weights)
    catch_all = max(priors[-1], 0.06)
    scale = (1.0 - catch_all) / math.fsum(priors[:-1])
    explanations = []
    for i in range(named):
        prior = priors[i] * scale
        if i == 0:
            explanations.append(Explanation("efficacy", "it works in patients",
                                            ClaimKind.EFFICACY, prior=prior))
        elif i % 2:
            explanations.append(Explanation(f"m{i}", f"mechanism {i}",
                                            ClaimKind.MECHANISM_HYPOTHESIS, prior=prior))
        else:
            explanations.append(Explanation(f"a{i}", f"artefact {i}", role=Role.ARTEFACT,
                                            prior=prior))
    count = draw(st.integers(min_value=1, max_value=4))
    analyses = []
    for j in range(count):
        size = draw(st.integers(min_value=2, max_value=4))
        analyses.append(Analysis(f"x{j}", f"analysis {j}", StudyDesign.IN_SILICO,
                                 tuple(f"o{k}" for k in range(size))))
    inquiry = Inquiry("generated", explanations, analyses)
    for analysis in analyses:
        rows = {}
        for e in explanations:
            if inquiry.bears_on(e.id, analysis.id):
                values = draw(distributions(len(analysis.outcomes)))
                rows[e.id] = dict(zip(analysis.outcomes, values))
        inquiry.declare(analysis.id, rows)
    outcomes = [draw(st.sampled_from(a.outcomes)) for a in analyses]
    return inquiry, outcomes


@SETTINGS
@given(st.data())
def test_information_gain_is_bounded(data):
    size = data.draw(st.integers(min_value=2, max_value=5))
    count = data.draw(st.integers(min_value=2, max_value=5))
    belief = dict(zip(range(count), _normalise(data.draw(st.lists(
        st.floats(min_value=0.01, max_value=1.0), min_size=count, max_size=count)))))
    table = {h: dict(zip(range(size), data.draw(distributions(size)))) for h in belief}
    gain = expected_information_gain(belief, table)
    entropy = -math.fsum(p * math.log2(p) for p in belief.values() if p > 0)
    assert -1e-12 <= gain <= min(entropy, math.log2(size)) + 1e-9


@SETTINGS
@given(inquiries())
def test_belief_is_a_martingale(case):
    """Averaged over the outcomes the engine predicts, the posterior is the prior."""
    inquiry, _ = case
    analysis = inquiry.analyses[0]
    prior = inquiry.belief
    expected = dict.fromkeys(prior, 0.0)
    for outcome in analysis.outcomes:
        trial = Inquiry.from_dict(copy.deepcopy(inquiry.as_dict()))
        update = trial.observe(analysis.id, outcome)
        predictive = math.fsum(update.prior[h] * update.likelihood[h] for h in prior)
        for h in prior:
            expected[h] += predictive * update.posterior[h]
    for h in prior:
        assert expected[h] == pytest.approx(prior[h], abs=1e-9)


@SETTINGS
@given(inquiries())
def test_every_update_is_a_probability_and_unlicensed_belief_is_held(case):
    inquiry, outcomes = case
    held = inquiry.belief["efficacy"]
    for analysis, outcome in zip(inquiry.analyses, outcomes):
        update = inquiry.observe(analysis.id, outcome)
        assert math.fsum(update.posterior.values()) == pytest.approx(1.0, abs=1e-9)
        assert all(0.0 <= p <= 1.0 + 1e-12 for p in update.posterior.values())
        assert update.posterior[CATCH_ALL] > 0.0      # the world never closes
        assert "efficacy" in update.held_fixed
        assert update.information >= 0.0
    assert inquiry.belief["efficacy"] == pytest.approx(held, abs=1e-12)


@SETTINGS
@given(inquiries())
def test_a_trail_replays_to_the_same_belief(case):
    inquiry, outcomes = case
    for analysis, outcome in zip(inquiry.analyses, outcomes):
        inquiry.observe(analysis.id, outcome)
    replayed = Inquiry.from_dict(inquiry.as_dict())
    assert replayed.head == inquiry.head
    for h, p in inquiry.belief.items():
        assert replayed.belief[h] == pytest.approx(p, abs=1e-12)


@SETTINGS
@given(inquiries())
def test_passing_a_severe_test_carries_the_promised_likelihood_ratio(case):
    inquiry, _ = case
    bar = inquiry.rule.min_severity
    named = [e.id for e in inquiry.explanations if e.role is not Role.CATCH_ALL]
    for analysis in inquiry.analyses:
        rows = inquiry.predictions(analysis.id)
        for leader in named:
            for rival in named:
                if leader == rival or not inquiry.severe(leader, rival, analysis.id):
                    continue
                passing = [o for o in analysis.outcomes
                           if rows[leader][o] > rows[rival][o]]
                for_leader = math.fsum(rows[leader][o] for o in passing)
                for_rival = math.fsum(rows[rival][o] for o in passing)
                assert for_leader >= bar - 1e-12
                assert for_leader >= (bar / (1.0 - bar)) * for_rival - 1e-9
