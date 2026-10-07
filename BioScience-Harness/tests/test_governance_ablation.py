"""The governance ablation: what each part of the stack catches, measured on the real gates.

These tests pin the benchmark's construction (every base case is an output the full stack
releases, every mutant is an error) and the findings the change that introduced it rests
on: which gaps were closed, and which remain and are listed rather than hidden.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bioagent.benchmarks.ablation import (CONFIGURATIONS, GATES, MUTATIONS, Case,
                                          base_cases, load_drafts, mutants, render_markdown,
                                          run_ablation)

COMMITTED = Path(__file__).resolve().parents[1] / "benchmarks" / "ablation" / "results.json"


@pytest.fixture(scope="module")
def report():
    return run_ablation()


def _row(report, name):
    return next(r for r in report.rows() if r["configuration"] == name)


def test_every_base_case_is_released_by_the_full_stack(report):
    assert report.base and not report.false_refusals()


def test_every_mutant_is_an_error_of_a_named_class(report):
    names = {m.name for m in MUTATIONS}
    for case in report.mutated:
        assert case.gold == "refuse" and case.error_class in names and case.base
        base = next(b for b in report.base if b.id == case.base)
        assert case.statement != base.statement or case.source != base.source \
            or case.path != base.path or case.cited_as != base.cited_as


def test_an_ungoverned_agent_releases_every_error(report):
    row = _row(report, "ungoverned")
    assert row["errors_released"] == row["mutants"] and row["false_refusals"] == 0


def test_the_full_stack_releases_fewer_errors_than_either_layer_alone(report):
    full = _row(report, "full")["errors_released"]
    assert full < _row(report, "kernel only")["errors_released"]
    assert full < _row(report, "domain only")["errors_released"]
    assert full <= 0.15 * _row(report, "full")["mutants"]


def test_tampering_is_caught_only_when_ingest_rechecks_the_signature(report):
    """The one interaction: the output gate sees a tampered record as intact unless ingest
    has re-checked its signature first."""
    tampered = [c for c in report.mutated if c.error_class == "tampered"
                and c.base != "C1"]
    assert tampered
    for case in tampered:
        assert report.released(case, ("output",))
        assert not report.released(case, ("provenance", "output"))
    assert set(report.adds()["provenance"]) >= {c.id for c in tampered}


def test_a_citation_in_a_non_clinical_sentence_must_name_a_usable_record(report):
    """Closed here: a mechanism or hypothesis sentence skipped the output gate whole, so a
    fabricated, mismatched or tampered record behind its citation went out."""
    for cid in ("M1/fabricated_citation", "H1/fabricated_citation",
                "S1/fabricated_citation", "M1/retracted", "H1/retracted"):
        case = next(c for c in report.mutated if c.id == cid)
        assert not report.released(case, ("output",)), cid


def test_a_near_name_in_the_text_is_caught_by_the_claim_contract(report):
    for cid in ("E4/near_name_text", "S1/near_name_text"):
        case = next(c for c in report.mutated if c.id == cid)
        assert not report.released(case, ("claim_contract",)), cid
        assert "CLM014" in report.results[cid]["claim_contract"].codes


def test_a_direction_no_edge_records_is_caught_only_by_the_release_check(report):
    assert report.adds()["release_path"] == ["M1/borrowed_direction"]


def test_the_remaining_gaps_are_listed_not_hidden(report):
    survivors = {c.id for c in report.survivors()}
    # Passage citations are not machine-checked, so a tampered or invented passage id
    # and a misattribution in the text pass the stack: listed in the results and docs.
    assert {"C1/fabricated_citation", "C1/tampered", "C1/subject_text"} <= survivors
    assert all(report.mutated[[c.id for c in report.mutated].index(s)].error_class
               for s in survivors)


def test_the_committed_results_are_the_ones_the_code_produces(report):
    committed = json.loads(COMMITTED.read_text(encoding="utf-8"))
    assert committed["mutants"] == len(report.mutated)
    assert {s["id"] for s in committed["survivors"]} == {c.id for c in report.survivors()}
    assert committed["configurations"] == report.rows()


def test_two_runs_agree(report):
    again = run_ablation()
    assert again.rows() == report.rows()
    assert {c.id for c in again.survivors()} == {c.id for c in report.survivors()}


def test_every_configuration_is_read_off_the_same_gate_results(report):
    assert set(CONFIGURATIONS["full"]) == set(GATES)
    assert CONFIGURATIONS["ungoverned"] == ()
    for case in report.cases:
        assert set(report.results[case.id]) >= set(GATES) | {"output|ingested"}


def test_drafts_from_elsewhere_run_through_the_same_gates(tmp_path, report):
    base = base_cases()[0]
    wrong = next(m for m in mutants([base]) if m.error_class == "population")
    lines = [json.dumps(c.as_dict(), ensure_ascii=False) for c in (base, wrong)]
    path = tmp_path / "drafts.jsonl"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    drafts = load_drafts(path)
    assert [d.gold for d in drafts] == ["release", "refuse"]
    small = run_ablation(drafts)
    assert _row(small, "full")["errors_released"] == 0
    assert _row(small, "full")["false_refusals"] == 0


def test_a_draft_without_a_label_is_refused(tmp_path):
    case = base_cases()[0].as_dict()
    case["gold"] = "maybe"
    path = tmp_path / "drafts.jsonl"
    path.write_text(json.dumps(case, ensure_ascii=False) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="gold"):
        load_drafts(path)


def test_the_report_states_its_construction(report):
    text = render_markdown(report)
    assert "not an estimate of field error rates" in text
    assert "## Errors the full stack releases" in text


def test_fixture_sources_cannot_be_mistaken_for_publications():
    for case in base_cases():
        identifier = case.source.identifier
        assert (identifier.startswith("doi:10.5555/") or identifier == "PMID: 34449189"
                or identifier.startswith("shanghanlun:")), identifier


def test_a_case_round_trips():
    for case in base_cases():
        again = Case.from_dict({**case.as_dict(), "material": {}})
        assert again.as_dict() == case.as_dict()
