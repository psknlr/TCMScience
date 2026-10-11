"""The agent regression suite (tcmstudio.scenarios): twelve research scenarios as tool chains.

Run offline against an empty hub: the scenarios that compute (formula provenance, enrichment,
sequence analysis, statistics, evidence licensing) pass against references established
outside the code; the ones that need a built dataset, an engine or the network end with the
class and reason an agent must give, and never pass.
"""
from __future__ import annotations

import json

import pytest

from bioagent.validation import BLOCKED, PASS, SKIPPED, UNVERIFIED
from tcmstudio import scenarios


@pytest.fixture(scope="module")
def offline(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("scenarios")
    ctx = {"where": "runner", "state_root": str(tmp / "state"), "tcmdb_root": str(tmp / "tcmdb"),
           "project_id": "scenarios"}
    return {r.component.split(".", 2)[-1]: r for r in scenarios.run_scenarios(ctx, tier="offline")}


def test_the_suite_is_the_plans_twelve_scenarios_each_fully_specified():
    assert [s.n for s in scenarios.SCENARIOS] == list(range(1, 13))
    for s in scenarios.SCENARIOS:
        d = s.as_dict()
        assert d["task"] and d["expected"] and d["criteria"], s.id
        assert "acceptable_limitations" in d and "datasets" in d


def test_what_computes_here_passes_against_independent_references(offline):
    for sid in ("formula_provenance", "pathway_enrichment", "protein_sequence", "evidence_synthesis"):
        assert offline[sid].status == PASS, offline[sid].as_dict()
    assert offline["formula_provenance"].check("composition").status == PASS
    assert "SciPy" in offline["pathway_enrichment"].check("p_values").detail
    assert offline["protein_sequence"].check("monoisotopic_mass").status == PASS


def test_the_eighteen_antagonisms_of_gancao_are_all_named(offline):
    # the seed corpus records 甘遂 only; the rule table adds 大戟, 海藻 and 芫花
    assert offline["evidence_synthesis"].check("eighteen_incompatibilities").status == PASS


def test_what_cannot_run_here_ends_with_its_class_and_never_passes(offline):
    for sid in ("np_compound_identification", "compound_target_retrieval", "cross_database_integration"):
        rec = offline[sid]
        assert rec.status == BLOCKED and "built" in rec.failure_reason and "--tcmdb-root" in rec.failure_reason
    for sid in ("molecular_docking", "admet"):
        rec = offline[sid]
        assert rec.status == BLOCKED and "tool_unavailable" in rec.failure_reason
    assert offline["clinical_literature"].status == SKIPPED
    assert offline["mechanism_hypothesis"].status == UNVERIFIED
    assert offline["mechanism_hypothesis"].check("only_hypotheses").status == PASS


def test_the_outcome_classes_are_the_agents():
    assert scenarios.outcome_of({"status": "failed", "error": {"type": "unavailable",
                                                               "message": "needs rdkit"}}) == "tool_unavailable"
    assert scenarios.outcome_of({"status": "failed", "error": {"type": "unavailable",
                                                               "message": "the store is not built"}}) == "data_unavailable"
    assert scenarios.outcome_of({"status": "failed", "error": {"type": "network_off"}}) == "data_unavailable"
    assert scenarios.outcome_of({"status": "failed", "error": {"type": "not_found",
                                                               "message": "unknown tool 'x'"}}) == "tool_unavailable"
    assert scenarios.outcome_of({"status": "refused", "error": {"type": "refused"}}) == "not_permitted"
    assert scenarios.outcome_of({"status": "succeeded"}) is None


def test_the_rule_table_names_what_the_seed_corpus_cannot_see(browser_ctx):
    from tcmstudio.dispatch import call
    s = call("tcm_safety_report", {"subject": "甘草", "co_administered": ["海藻"]}, browser_ctx)
    table = s["result"]["rule_table"]
    assert {r["herb"] for r in table["incompatible_with"]} >= {"甘遂", "海藻", "芫花", "京大戟"}
    assert [r["in_seed_corpus"] for r in table["incompatible_with"] if r["herb"] == "甘遂"] == [True]
    assert table["co_administered_conflicts"][0]["herb"] == "海藻"
    assert "not reviewed by a licensed TCM practitioner" in s["summary_en"]
    assert "no incompatibility recorded (which is not safety to combine)" not in s["summary_en"]
    c = call("tcm_compatibility", {"herbs": ["甘草", "芫花"]}, browser_ctx)
    assert c["result"]["compatible"] is False
    assert c["result"]["rule_table_conflicts"] == [{"pair": ["甘草", "芫花"], "rule": "十八反",
                                                    "source": c["result"]["rule_table_conflicts"][0]["source"],
                                                    "in_seed_corpus": False}]
    ok = call("tcm_compatibility", {"herbs": ["桂枝", "白芍"]}, browser_ctx)
    assert "rule_table_conflicts" not in ok["result"]
