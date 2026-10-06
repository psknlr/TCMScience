"""Chinese retrieval and planner arguments, after the October 2026 audit (AUD-15, AUD-17).

AUD-15  the generic registry tokenised only Latin letters and digits, so every Chinese
        query had no terms and returned the first components of the pool;
AUD-17  the LLM planner kept a step's component id and dropped its arguments.

AUD-16 (Chinese claim support) is PSH's and is tested there.
"""

from __future__ import annotations

import pandas as pd
import pytest

from bioagent.planners.llm import LLMPlanner, check_arguments
from bioagent.psh import default_runtime
from bioagent.registry import CapabilityRegistry
from bioagent.runtime.agentspec import AgentSpec
from bioagent.runtime.registry import query_terms

pytestmark = pytest.mark.unit


@pytest.fixture(scope="module")
def runtime():
    return default_runtime(catalogue=False, public_apis=False, skills=False)


def _found(runtime, query: str, limit: int = 3) -> list[str]:
    return [m.id.removeprefix("native.tool.")
            for m in runtime.registry.search(query, limit=limit)]


# ============================================================ AUD-15: Chinese retrieval

def test_a_chinese_query_no_longer_returns_the_first_dna_tools(runtime):
    """The audit's case: 附子安全 and an unrelated Chinese phrase chose the same DNA tool."""
    dna = {"reverse_complement", "transcribe", "translate"}
    for query in ("附子安全", "中文无关词", "量子力学", "黄芪"):
        assert not set(_found(runtime, query)) & dna, query


@pytest.mark.parametrize("query, expected", [
    ("附子安全", "tcm_compatibility"),
    ("附子 毒性", "tcm_herb"),
    ("黄芪的功效", "tcm_herb"),
    ("十八反配伍禁忌", "tcm_compatibility"),
    ("桂枝汤的组成", "tcm_formula"),
    ("脾胃气虚证的舌象脉象", "tcm_syndrome"),
])
def test_a_chinese_query_finds_the_tool_it_names(runtime, query, expected):
    assert expected in _found(runtime, query)


@pytest.mark.parametrize("query", ["中文无关词", "量子力学", "x", "?"])
def test_a_query_nothing_matches_finds_nothing(runtime, query):
    assert _found(runtime, query) == []


def test_english_queries_are_unchanged(runtime):
    assert "six_frame_translation" in _found(runtime, "DNA sequence translation")
    assert _found(runtime, "herb safety")[0] == "tcm_herb"
    assert len(runtime.registry.search(None, limit=5)) == 5       # no query: the pool


def test_terms_cover_latin_and_chinese():
    terms = query_terms("附子 toxicity QC")
    assert {"附子", "toxicity", "qc"} <= terms
    assert "aconite" in terms                     # the lexicon's English for 附子


def test_the_capability_catalogue_ranks_chinese_and_matches_nothing_else():
    frame = pd.DataFrame([
        {"name": n, "kind": "tool", "domain": d, "omics_type": "general",
         "contributing_projects": "p", "licenses": "MIT", "integration_mode": "native",
         "availability": "available", "description": desc}
        for n, d, desc in (("dna_translate", "genomics", "translate a DNA sequence"),
                           ("herb_safety", "tcm", "中药 安全性 配伍禁忌 十八反"))])
    catalogue = CapabilityRegistry(frame)
    assert [c.name for c in catalogue.find("十八反")] == ["herb_safety"]
    assert catalogue.find("量子力学") == []          # was the whole catalogue, unranked


# ============================================================ AUD-17: planner arguments

def _plan(runtime, answer: str):
    planner = LLMPlanner(client=lambda prompt: answer)
    return planner.plan("translate the DNA sequence ATGC", runtime.registry)


def test_the_planner_keeps_the_arguments_the_model_chose(runtime):
    """The audit's case: the model answered arguments={"sequence": "ATGC"} and the step
    was built with none, so the real tool failed for want of sequence."""
    plan = _plan(runtime, '[{"id": "native.tool.translate", "arguments": '
                          '{"sequence": "ATGC"}, "rationale": "translate"}]')
    assert plan.planner == "llm"
    (step,) = plan.steps
    assert step.arguments == {"sequence": "ATGC"}


def test_planned_arguments_reach_the_tool(runtime):
    planner = LLMPlanner(client=lambda prompt: (
        '[{"id": "native.tool.translate", "arguments": {"sequence": "ATGGCC"}}]'))
    report = runtime.run("translate ATGGCC", AgentSpec(name="t", planner="llm",
                                                       max_attempts=1), planner=planner)
    (result,) = report.results
    assert result.status.successful and result.value["protein"] == "MA"


@pytest.mark.parametrize("arguments, problem", [
    ('{"seq": "ATGC"}', "unknown argument 'seq'"),
    ('{}', "missing required argument 'sequence'"),
    ('{"sequence": "ATGC", "frame": "one"}', "argument 'frame' has the wrong type"),
    ('{"sequence": "ATGC", "to_stop": 1}', "argument 'to_stop' has the wrong type"),
    ('"ATGC"', "arguments must be an object"),
])
def test_arguments_that_do_not_fit_the_tool_are_refused(runtime, arguments, problem):
    plan = _plan(runtime, f'[{{"id": "native.tool.translate", "arguments": {arguments}}}]')
    assert plan.planner != "llm" and problem in plan.notes
    assert all(s.arguments == {} for s in plan.steps)     # nothing invented in their place


def test_a_component_that_declares_no_parameters_takes_arguments_as_given(runtime):
    class Bare:
        inputs: dict = {}
    assert check_arguments(Bare(), {"anything": 1}) == []
    assert check_arguments(Bare(), ["not", "an", "object"])


# ================================================= end to end: one Chinese research task

def test_a_chinese_task_is_retrieved_planned_with_arguments_and_executed(runtime):
    """附子与半夏合用是否存在十八反配伍禁忌? — the registry must offer the compatibility tool
    for the Chinese question, the planner must keep the herbs the model chose, and the tool
    must find the 十八反 record."""
    task = "附子与半夏合用是否存在十八反配伍禁忌？"
    offered: list[str] = []

    def model(prompt: str) -> str:
        offered.extend(i for i in ("native.tool.tcm_compatibility", "native.tool.translate")
                       if f'"{i}"' in prompt)
        return ('[{"id": "native.tool.tcm_compatibility", '
                '"arguments": {"herbs": ["附子", "半夏"]}, "rationale": "十八反"}]')

    report = runtime.run(task, AgentSpec(name="t", planner="llm", max_attempts=1),
                         planner=LLMPlanner(client=model))
    assert offered == ["native.tool.tcm_compatibility"]
    (step,) = report.plan.steps
    assert step.arguments == {"herbs": ["附子", "半夏"]}
    (result,) = report.results
    assert result.status.successful and result.value["compatible"] is False
    (conflict,) = result.value["conflicts"]
    assert {conflict["first_id"], conflict["second_id"]} == {"herb.fuzi", "herb.banxia"}
    assert "十八反" in conflict["description"]
