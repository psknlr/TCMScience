"""Statements in the docs and docstrings that the code can check.

``ROADMAP_AGENT_RUNTIME.md`` calls itself the honest map, and several of its sentences had
gone stale: a §6 sentence still read the supervisor row as ❌ after v0.7 made it ✅,
``LoopState`` said checkpointing was not implemented beside a loop that checkpoints, and
the scientific docs called hypothesis competition future work after
``psh.scientist.inquiry`` implemented it. Each test here pins one statement to the thing
it describes, so the next change that makes it false fails here instead of being found
by a reader.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

PSH = Path(__file__).resolve().parents[1]
REPO = PSH.parent
DOCS = PSH / "docs"
ROADMAP = DOCS / "ROADMAP_AGENT_RUNTIME.md"


def flat(path: Path) -> str:
    """A file's text with every run of whitespace as one space, so a sentence that a
    reflow splits across lines is still found."""
    return re.sub(r"\s+", " ", path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("path, stale", [
    (PSH / "src/psh/runtime/loop.py", "Checkpointing is not implemented"),
    (PSH / "src/psh/runtime/plan.py", "This runtime is single-threaded"),
    (ROADMAP, 'reads "Supervisor / worker pool ❌"'),
    (ROADMAP, '"Supervisor / worker pool ❌"正是原因'),
    (DOCS / "V6_SCIENTIFIC_COMPILER.md", "The two coarsenings"),
    (DOCS / "V6_SCIENTIFIC_COMPILER.md", "No hypothesis competition"),
    (DOCS / "SCIENTIFIC_WORKFLOW.md", "Competition remains future work"),
    (DOCS / "SCIENTIFIC_WORKFLOW.md", "竞争仍是未来工作"),
])
def test_a_corrected_statement_stays_corrected(path, stale):
    assert stale not in flat(path), f"{path.name} says again: {stale!r}"


def test_the_supervisor_row_is_done_and_says_how_the_ledger_reserves():
    text = flat(ROADMAP)
    assert "| Supervisor / worker pool | ❌ | ✅ |" in text
    assert "reserved for a whole batch or not at all" in text


def test_the_retry_row_names_the_sleep_the_services_use():
    from psh.runtime import ResearchRunService, ScientificRunService, jittered_sleep
    import inspect

    text = flat(ROADMAP)
    assert "`jittered_sleep`" in text
    for service in (ResearchRunService, ScientificRunService):
        default = inspect.signature(service.__init__).parameters["sleep"].default
        assert default is jittered_sleep, service.__name__


def test_every_coarsening_the_bridge_makes_is_named_in_v6():
    from psh.compiler.bridge import DESIGN_NAMES

    text = flat(DOCS / "V6_SCIENTIFIC_COMPILER.md")
    coarsened = [d for d, name in DESIGN_NAMES.items()
                 if d.value != name and d.value.replace("randomised", "randomized") != name]
    assert len(coarsened) == 5
    assert "The five coarsenings" in text
    for design in coarsened:
        assert f"`{design.name}`" in text, design.name


def test_the_inquiry_engine_the_docs_point_to_exists():
    from psh.scientist import inquiry

    for name in ("V6_SCIENTIFIC_COMPILER.md", "SCIENTIFIC_WORKFLOW.md",
                 "SCIENTIFIC_MODEL_PLANNER.md"):
        assert "`psh.scientist.inquiry`" in flat(DOCS / name), name
    assert hasattr(inquiry, "Inquiry")


# --------------------------------------------------- §8: which parts a product calls

#: The parts §8 lists as a library with no product caller.
NO_PRODUCT_CALLER = (
    "AgentLoopController", "StaticPlanner", "ModelPlanner", "Evaluator",
    "ResearchRunService", "ScientificRunService", "DynamicResearchRunService",
    "Supervisor", "WorkerPool", "Reducer", "LocalSubagentBackend",
    "CheckpointStore", "JournalCheckpointStore", "OperationLedger",
    "ContextCompiler", "Compactor", "MemoryRetriever", "A2AAgentAdapter",
)

#: What §8 names as the product callers of the parts that have one.
PRODUCT_CALLERS = (
    ("studio/runner/src/tcmstudio/dispatch.py", "run_governed"),
    ("BioScience-Harness/src/bioagent/operations.py", "broker.call_tool"),
    ("BioScience-Harness/src/bioagent/research/loop.py", "broker.call_tool"),
    ("BioScience-Harness/src/bioagent/research/inquiry.py", "run_inquiry"),
    ("BioScience-Harness/src/bioagent/research/loop.py", "ScientificCompiler"),
    ("studio/runner/src/tcmstudio/dispatch.py", "EventStore"),
    ("studio/runner/src/tcmstudio/governance.py", "Classifier"),
)

#: Where product code lives. Only Python can reach this package (the browser runs it
#: through Pyodide from Python files too), so only Python files are read.
PRODUCT_TREES = ("BioScience-Harness/src", "studio/runner/src", "studio/web")


@pytest.fixture(scope="module")
def product_text():
    if not all((REPO / tree).is_dir() for tree in PRODUCT_TREES):
        pytest.skip("the product trees are not beside this package")
    return {p: p.read_text(encoding="utf-8", errors="replace")
            for tree in PRODUCT_TREES for p in sorted((REPO / tree).rglob("*.py"))
            if "node_modules" not in p.parts}


@pytest.fixture(scope="module")
def psh_names_used(product_text):
    """Every name product code takes from ``psh``: imported from it, or reached as
    ``psh.<module>.<name>``. A class of the same name defined elsewhere is not counted."""
    import ast

    used: dict[str, set[str]] = {}
    for path, text in product_text.items():
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        where = str(path.relative_to(REPO))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == "psh":
                for alias in node.names:
                    used.setdefault(alias.name, set()).add(where)
            elif isinstance(node, ast.Attribute):
                root = node.value
                while isinstance(root, ast.Attribute):
                    root = root.value
                if isinstance(root, ast.Name) and root.id == "psh":
                    used.setdefault(node.attr, set()).add(where)
    return used


def test_the_scan_sees_the_parts_that_do_have_callers(psh_names_used):
    """A scan that finds nothing would pass every 'no caller' test below."""
    for name in ("TrustedKernel", "run_inquiry", "ScientificCompiler", "EventStore",
                 "Classifier", "OperationState"):
        assert name in psh_names_used, name


def test_section_8_lists_each_part_without_a_caller():
    text = flat(ROADMAP)
    assert "## 8. Which parts a product calls" in text
    for name in NO_PRODUCT_CALLER:
        assert f"`{name}`" in text, name


@pytest.mark.parametrize("name", NO_PRODUCT_CALLER)
def test_a_part_listed_without_a_product_caller_has_none(psh_names_used, name):
    """When a product starts calling one of these, §8 is out of date: move the row."""
    callers = sorted(psh_names_used.get(name, ()))
    assert not callers, (
        f"{name} now has a product caller ({callers}); update §8 of "
        "ROADMAP_AGENT_RUNTIME.md and this list together")


@pytest.mark.parametrize("path, needle", PRODUCT_CALLERS)
def test_a_product_caller_named_in_section_8_still_calls(product_text, path, needle):
    text = product_text.get(REPO / path)
    assert text is not None, f"{path} is gone; update §8"
    assert needle in text, f"{path} no longer uses {needle}; update §8"
