"""The four-arm comparison harness: its mechanics, not any result.

Every test here runs ``ScriptedClient`` on the synthetic fixture tasks, which exist to test
the harness; no test calls a model, and nothing here says how any model or the governance
performs. What is checked: each arm sends what its definition says and no more, the gates
withhold in C and D only, D's revision round sees the reasons, A+ spends the same calls as
D, scoring follows gold before reviews, repeats are averaged inside a task, the interval
resamples units rather than claims, blinding hides the arm, and every report built from a
fixture or a script says it is not a result.
"""

from __future__ import annotations

import dataclasses
import json
import types

import pytest

from bioagent.benchmarks.comparison import (
    ARMS, AnthropicClient, Budget, ComparisonTask, GoldClaim, ReleasedClaim, ScriptedClient,
    Transcript, export_for_review, fixture_script, fixture_tasks, load_reviews, load_tasks,
    render_markdown, run_arm, run_comparison, score,
)

MECH, SAFETY = "fixture-formula-mechanism", "fixture-heshouwu-safety"
TRIAL = "doi:10.5555/tcm-ablation.01"
DOCKING = "doi:10.5555/tcm-ablation.09"


def claim(text, citation, kind, subject, population="", outcome="", direction="",
          certainty="moderate"):
    return {"text": text, "citation": citation, "claim_kind": kind, "subject": subject,
            "population": population, "outcome": outcome, "direction": direction,
            "certainty": certainty}


GOOD_TRIAL = claim("葛根芩连汤可降低成人2型糖尿病患者的糖化血红蛋白。", TRIAL, "efficacy", "葛根芩连汤",
                   "成人2型糖尿病患者", "糖化血红蛋白", "decrease")
CHILDREN = claim("葛根芩连汤可降低儿童2型糖尿病患者的糖化血红蛋白。", TRIAL, "efficacy", "葛根芩连汤",
                 "儿童2型糖尿病患者", "糖化血红蛋白", "decrease")
DOCK = claim("Docking suggests that baicalin may bind PTP1B, a hypothesis for experimental "
             "testing.", DOCKING, "mechanism_hypothesis", "baicalin", outcome="PTP1B",
             certainty="tentative")


@pytest.fixture(scope="module")
def tasks() -> list[ComparisonTask]:
    return fixture_tasks()


@pytest.fixture(scope="module")
def runs(tasks) -> tuple[ScriptedClient, list[Transcript]]:
    client = ScriptedClient(fixture_script())
    return client, run_comparison(tasks, list(ARMS), client)


def transcript(runs, task, arm) -> Transcript:
    return next(t for t in runs[1] if t.task == task and t.arm == arm)


# ----------------------------------------------------------------------- the arms

def test_every_arm_sees_the_same_sources_and_question(tasks):
    client = ScriptedClient(fixture_script())
    systems = {}
    original = client.complete

    def spy(system, messages, *, max_tokens):
        systems[client._context[1]] = system
        return original(system, messages, max_tokens=max_tokens)

    client.complete = spy
    for arm in ARMS.values():
        run_arm(tasks[0], arm, client)
    block = systems["A"].split("Sources:\n", 1)[1].split("\n\nAnswer", 1)[0]
    for arm, system in systems.items():
        assert block in system, arm
        assert tasks[0].question in system
    assert "checked" not in systems["A"] and "checked" not in systems["B"]
    assert "checked" in systems["C"] and "rivals" in systems["D"]
    assert "rivals" not in systems["C"]


def test_a_transcript_keeps_what_was_sent_and_reads_back(runs):
    d = transcript(runs, MECH, "D")
    assert "rivals" in d.system and d.turns[0]["content"] == fixture_tasks()[0].question
    again = Transcript.from_dict(json.loads(json.dumps(d.as_dict(), ensure_ascii=False)))
    assert again == d


def test_calls_follow_each_arms_rounds(runs):
    calls = {arm: transcript(runs, MECH, arm).calls for arm in ARMS}
    assert calls == {"A": 1, "A+": 2, "B": 1, "C": 1, "D": 2}


def test_d_spends_no_revision_call_when_nothing_was_withheld(runs):
    assert transcript(runs, SAFETY, "D").calls == 1
    assert transcript(runs, SAFETY, "A+").calls == 2


def test_the_budget_is_passed_to_every_call(tasks):
    seen = []

    class Recorder(ScriptedClient):
        def complete(self, system, messages, *, max_tokens):
            seen.append(max_tokens)
            return super().complete(system, messages, max_tokens=max_tokens)

    run_comparison(tasks, list(ARMS), Recorder(fixture_script()), Budget(max_tokens=1234))
    assert seen and set(seen) == {1234}


def test_ungated_arms_release_everything(runs):
    for arm in ("A", "A+", "B"):
        assert all(c.released for c in transcript(runs, MECH, arm).claims), arm


def test_the_gates_withhold_the_extrapolation_in_c(runs):
    c = transcript(runs, MECH, "C")
    withheld = [x for x in c.claims if not x.released]
    assert [x.text for x in withheld] == [CHILDREN["text"]]
    reasons = " ".join(withheld[0].reasons)
    assert "licensing" in reasons and "claim_contract" in reasons
    assert {x.text for x in c.released} == {GOOD_TRIAL["text"], DOCK["text"]}


def test_d_revises_on_the_reasons_and_its_revision_is_gated_again(runs):
    d = transcript(runs, MECH, "D")
    revision = d.turns[2]["content"]
    assert revision.startswith("These claims were withheld")
    assert CHILDREN["text"] in revision and "licensing" in revision
    assert [x.text for x in d.claims] == [GOOD_TRIAL["text"], DOCK["text"]]
    assert all(x.released for x in d.claims)
    assert d.rivals == ["regression to the mean"]


def test_a_plus_reviews_its_own_answer_with_an_ordinary_prompt(runs):
    a_plus = transcript(runs, MECH, "A+")
    assert a_plus.turns[2]["content"].startswith("Review your answer")
    assert [x.text for x in a_plus.claims][1].startswith("Docking suggests baicalin")


def test_a_claim_citing_no_given_source_is_withheld_before_the_gates(tasks):
    fabricated = claim("葛根芩连汤可降低成人2型糖尿病患者的糖化血红蛋白。", "doi:10.5555/tcm-ablation.99",
                       "efficacy", "葛根芩连汤", "成人2型糖尿病患者", "糖化血红蛋白", "decrease")
    uncited = dict(GOOD_TRIAL, citation="")
    client = ScriptedClient({(MECH, "C", 0): json.dumps({"claims": [fabricated, uncited]})})
    out = run_arm(tasks[0], ARMS["C"], client)
    assert [x.released for x in out.claims] == [False, False]
    assert "names none of the sources" in out.claims[0].reasons[0]
    assert out.claims[1].reasons == ("the claim cites no source",)


def test_an_unreadable_structured_reply_is_recorded_not_guessed(tasks):
    client = ScriptedClient({(MECH, "B", 0): "I cannot answer in JSON."})
    out = run_arm(tasks[0], ARMS["B"], client)
    assert out.claims == [] and out.parse_errors == ["no JSON object in the reply"]


def test_prose_next_steps_are_plans_not_claims(runs):
    a = transcript(runs, MECH, "A")
    assert a.next_steps == ["test baicalin against PTP1B"]
    assert all("test baicalin" not in x.text for x in a.claims)
    assert a.claims[0].citation == TRIAL


# --------------------------------------------------------------------- scoring

def test_gold_decides_and_an_error_phrase_outranks_a_licensed_one(runs, tasks):
    report = score(runs[1], tasks, resamples=200)
    a = report.per_task["A"][MECH]
    assert (a["correct"], a["errors"]) == (1, 1)            # 成人 trial claim; 儿童 extrapolation
    c = report.per_task["C"][MECH]
    assert (c["correct"], c["errors"], c["withheld"], c["withheld_licensed"]) == (2, 0, 1, 0)
    assert c["coverage"] == 1.0 and a["coverage"] == 0.5


def test_a_claim_gold_does_not_decide_waits_for_review(runs, tasks):
    report = score(runs[1], tasks, resamples=200)
    a_plus = report.per_task["A+"][MECH]
    # "Docking suggests baicalin may bind PTP1B" matches the docking gold claim.
    assert a_plus["correct"] == 2 and a_plus["unadjudicated"] == 0
    odd = ReleasedClaim(text="The evidence base is small.", citation="")
    tr = Transcript(task=MECH, arm="A", client="scripted (harness test; not a model)",
                    claims=[odd])
    pending = score([tr], tasks, resamples=0).per_task["A"][MECH]
    assert pending["unadjudicated"] == 1 and pending["uncited"] == 1
    reviewed = score([tr], tasks, resamples=0,
                     reviews={(MECH, "theevidencebaseissmall"): "not_a_claim"})
    assert reviewed.per_task["A"][MECH]["released"] == 0


def test_an_unresolved_citation_is_counted_as_evidence_quality(tasks):
    tr = Transcript(task=MECH, arm="A", client="scripted (harness test; not a model)",
                    claims=[ReleasedClaim(text="葛根芩连汤可降低成人2型糖尿病患者的糖化血红蛋白。",
                                          citation="doi:10.5555/tcm-ablation.99")])
    row = score([tr], tasks, resamples=0).per_task["A"][MECH]
    assert row["unresolved"] == 1 and row["correct"] == 1


def test_repeats_are_averaged_inside_a_task(tasks):
    good = ReleasedClaim(text=GOOD_TRIAL["text"], citation=TRIAL)
    bad = ReleasedClaim(text=CHILDREN["text"], citation=TRIAL)
    trs = [Transcript(task=MECH, arm="B", client="x", repeat=0, claims=[good, bad]),
           Transcript(task=MECH, arm="B", client="x", repeat=1, claims=[good])]
    report = score(trs, tasks, resamples=0)
    assert report.per_task["B"][MECH]["errors"] == 0.5
    assert report.stability["B"] == pytest.approx(0.5)      # {trial, children} vs {trial}
    single = score(trs[:1], tasks, resamples=0)
    assert single.stability["B"] is None


def test_the_interval_resamples_units_not_tasks(tasks):
    """Two tasks from one unit are one unit: no interval can be drawn from a single unit."""
    one_unit = [ComparisonTask(id=t.id, scenario=t.scenario, question=t.question,
                               sources=t.sources, unit="the-same-paper", gold=t.gold,
                               expected=t.expected, synthetic=True) for t in tasks]
    client = ScriptedClient(fixture_script())
    trs = run_comparison(one_unit, ["C", "D"], client)
    report = score(trs, one_unit, resamples=500)
    d_c = [d for d in report.differences if d["comparison"] == "D - C"]
    assert d_c and all(d["units"] == 1 and d["tasks"] == 2 for d in d_c)
    assert all(d["ci95"][0] != d["ci95"][0] for d in d_c)    # NaN: not estimable


def test_the_named_comparisons_are_reported_with_intervals(runs, tasks):
    report = score(runs[1], tasks, resamples=300, seed=1)
    names = {d["comparison"] for d in report.differences}
    assert names == {"B - A", "C - B", "D - C", "D - A+", "D - A"}
    d_a = next(d for d in report.differences
               if d["comparison"] == "D - A" and d["metric"] == "errors")
    assert d_a["estimate"] == pytest.approx(-0.5) and d_a["units"] == 2
    lo, hi = d_a["ci95"]
    assert lo <= d_a["estimate"] <= hi
    again = score(runs[1], tasks, resamples=300, seed=1)
    assert again.differences == report.differences             # seeded


def test_every_report_from_a_script_or_a_fixture_says_it_is_not_a_result(runs, tasks):
    report = score(runs[1], tasks, resamples=100)
    assert report.synthetic
    text = render_markdown(report)
    assert "**Not a result.**" in text and "scripted (harness test; not a model)" in text
    real = [ComparisonTask(id=t.id, scenario=t.scenario, question=t.question,
                           sources=t.sources, unit=t.unit, gold=t.gold, expected=t.expected)
            for t in tasks]
    scripted = score(run_comparison(real, ["A"], ScriptedClient(fixture_script())), real,
                     resamples=0)
    assert not scripted.synthetic and "**Not a result.**" in render_markdown(scripted)


# -------------------------------------------------------------------- blinding

def test_the_review_file_hides_the_arm_and_the_key_restores_it(runs, tasks, tmp_path):
    extra = Transcript(task=MECH, arm="B", client="x", claims=[
        ReleasedClaim(text="The evidence base is small.", citation=""),
        ReleasedClaim(text="Berberine also lowers glucose.", citation=TRIAL)])
    same_text_other_arm = Transcript(task=MECH, arm="D", client="x", claims=[
        ReleasedClaim(text="The evidence base is small.", citation="")])
    review, key = tmp_path / "review.jsonl", tmp_path / "key.jsonl"
    n = export_for_review([*runs[1], extra, same_text_other_arm], tasks, review, key, seed=3)
    rows = [json.loads(line) for line in review.read_text(encoding="utf-8").splitlines()]
    keys = [json.loads(line) for line in key.read_text(encoding="utf-8").splitlines()]
    assert n == len(rows) == len(keys) == 2                     # gold decided the rest
    assert all("arm" not in r for r in rows)
    assert all(r["verdict"] == "" for r in rows)
    assert {k["review_id"] for k in keys} == {r["review_id"] for r in rows}
    berberine = next(r for r in rows if r["claim"].startswith("Berberine"))
    assert "葛根芩连汤" in berberine["source"]                  # the cited source, for the reviewer

    for r in rows:
        r["verdict"] = "error" if r["claim"].startswith("Berberine") else "not a claim"
    review.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                      encoding="utf-8")
    verdicts = load_reviews(review)
    assert set(verdicts.values()) == {"error", "not_a_claim"}
    b = score([extra], tasks, reviews=verdicts, resamples=0).per_task["B"][MECH]
    assert (b["released"], b["errors"], b["unadjudicated"]) == (1, 1, 0)


def test_a_review_verdict_outside_the_vocabulary_is_an_error(tmp_path):
    path = tmp_path / "r.jsonl"
    path.write_text(json.dumps({"task": MECH, "claim": "x", "verdict": "mostly fine"}) + "\n")
    with pytest.raises(ValueError, match="not one of"):
        load_reviews(path)


# ----------------------------------------------------------------------- tasks

def test_tasks_round_trip_through_json_lines(tasks, tmp_path):
    path = tmp_path / "tasks.jsonl"
    rows = []
    for t in tasks:
        rows.append({"id": t.id, "scenario": t.scenario, "question": t.question,
                     "unit": t.unit, "synthetic": t.synthetic, "expected": list(t.expected),
                     "sources": [dataclasses.asdict(s) for s in t.sources],
                     "gold": [dataclasses.asdict(g) for g in t.gold]})
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), "utf-8")
    loaded = load_tasks(path)
    assert [t.id for t in loaded] == [t.id for t in tasks]
    assert loaded[0].gold == tasks[0].gold and loaded[0].sources == tasks[0].sources
    path.write_text('{"id": "broken"}\n', encoding="utf-8")
    with pytest.raises(ValueError, match=":1:"):
        load_tasks(path)


def test_a_gold_claim_reads_alternatives_and_exclusions():
    g = GoldClaim("mechanism-as-fact", ("PTP1B", "葛根芩连汤|formula"), licensed=False,
                  none_of=("may", "可能"))
    assert g.matches("The formula lowers glucose through PTP1B.")
    assert not g.matches("The formula may act through PTP1B.")
    assert g.matches("葛根芩连汤通过PTP1B降糖")


# ------------------------------------------------------------- the API adapter

class _Messages:
    def __init__(self, response=None, error=None):
        self.response, self.error, self.calls = response, error, []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.response


def _response(text, stop="end_turn"):
    return types.SimpleNamespace(
        content=[types.SimpleNamespace(type="thinking", thinking="..."),
                 types.SimpleNamespace(type="text", text=text)],
        usage=types.SimpleNamespace(input_tokens=11, output_tokens=7), stop_reason=stop)


def test_the_api_adapter_needs_a_model_and_sends_the_same_request_shape():
    with pytest.raises(ValueError, match="no default"):
        AnthropicClient("", client=object())
    messages = _Messages(_response("an answer"))
    client = AnthropicClient("model-under-test", params={"thinking": {"type": "adaptive"}},
                             client=types.SimpleNamespace(messages=messages))
    reply = client.complete("system", [{"role": "user", "content": "q"}], max_tokens=99)
    assert (reply.text, reply.input_tokens, reply.output_tokens) == ("an answer", 11, 7)
    sent = messages.calls[0]
    assert sent["model"] == "model-under-test" and sent["max_tokens"] == 99
    assert sent["system"] == "system" and sent["thinking"] == {"type": "adaptive"}
    assert client.label == "anthropic:model-under-test"


def test_the_api_adapter_records_refusals_and_failures_as_replies():
    refused = AnthropicClient("m", client=types.SimpleNamespace(
        messages=_Messages(_response("partial", stop="refusal"))))
    reply = refused.complete("s", [{"role": "user", "content": "q"}], max_tokens=10)
    assert reply.stop_reason == "refusal" and reply.text == ""
    failing = AnthropicClient("m", client=types.SimpleNamespace(
        messages=_Messages(error=RuntimeError("overloaded"))))
    reply = failing.complete("s", [{"role": "user", "content": "q"}], max_tokens=10)
    assert reply.stop_reason == "error" and "overloaded" in reply.detail
