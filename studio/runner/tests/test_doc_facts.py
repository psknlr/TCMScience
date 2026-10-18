"""The catalog numbers the READMEs and docs restate agree with the catalog (studio/scripts/doc_facts.py)."""
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "doc_facts.py"
sys.path.insert(0, str(SCRIPT.parent))
import doc_facts  # noqa: E402


@pytest.fixture(scope="module")
def facts():
    return doc_facts.facts()


def test_every_restated_number_agrees():
    res = subprocess.run([sys.executable, str(SCRIPT), "--check"], capture_output=True, text=True, timeout=300)
    assert res.returncode == 0, res.stdout + res.stderr


def test_the_facts_are_the_catalogs(facts):
    from bioagent.providers.public_apis import SOURCES
    from bioagent.tools import TOOLS
    from tcmstudio.catalog import build_catalog
    doc = build_catalog("browser", probe=False)
    assert facts["entries"] == doc["counts"]["entries"] == sum(doc["counts"]["by_kind"].values())
    assert facts["sources"] == len({s.key for s in SOURCES})
    assert facts["operations"] == sum(len(s.operations) for s in SOURCES)
    assert facts["native"] == len(TOOLS) and facts["native_domains"] == len({t.domain for t in TOOLS})
    assert facts["browser"] + facts["runner_only"] == facts["entries"]
    assert facts["class.2"] == 4 and facts["class.3"] == 3 and facts["kernel_tools"] == 7
    assert facts["pinned_skills"] == 4 and facts["core"] == 25
    assert facts["live_verified"] <= facts["operations"]


def test_a_stale_number_is_caught_in_either_language(facts):
    text = ("The catalog has 12 public sources with 9 typed operations and 3 native tools.\n"
            "共 12 个公共数据源、9 个带类型的操作、3 个原生工具，640 个条目。\n")
    problems = doc_facts.check_text("studio/docs/NEW.md", text, facts)
    assert len(problems) == 7, problems
    assert any("sources" in p and "says 12" in p for p in problems)
    right = (f"{facts['sources']} public sources, {facts['operations']} typed operations, "
             f"{facts['native']} 个原生工具, {facts['entries']} entries\n")
    assert doc_facts.check_text("studio/docs/NEW.md", right, facts) == []


def test_an_anchor_that_no_longer_matches_is_reported(facts):
    problems = doc_facts.check_text("studio/docs/BROWSER_COMPUTE.md", "nothing here\n", facts)
    assert problems and all("no longer there" in p for p in problems)


def test_dated_reports_and_release_notes_keep_their_own_numbers(facts):
    assert doc_facts.check_text("docs/release-qualification-2026-10.md", "649 catalog entries", facts) == []
    notes = ("# x\n\n## v2.4 — then\n\n16 → 58 verified public sources, 45 → 153 typed operations.\n\n"
             "## Layout\n\n    providers/ and 58 public sources\n")
    problems = doc_facts.check_text("BioScience-Harness/README.md", notes, facts, anchors=False)
    assert len(problems) == 1 and ":9:" in problems[0], problems
    for reason in [*doc_facts.HISTORICAL_FILES.values(), *(r for _, r in doc_facts.HISTORICAL_SECTIONS.values())]:
        assert len(reason) > 20, "every exemption says why"
