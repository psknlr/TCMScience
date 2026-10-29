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
    assert facts["pinned_skills"] == 4 and facts["core"] == 27
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


def test_a_stale_version_badge_is_caught():
    known = doc_facts.versions()
    assert known["psh"] and known["bioagent"]
    badge = "![Kernel](https://img.shields.io/badge/kernel-PSH%200.5.3-9a6428.svg)\n"
    problems = doc_facts.check_versions("README.md", badge, known)
    assert len(problems) == 1 and "says psh 0.5.3" in problems[0] and known["psh"] in problems[0]
    ok = badge.replace("0.5.3", known["psh"])
    assert doc_facts.check_versions("README.md", ok, known) == []
    assert "no longer there" in doc_facts.check_versions("README.zh-CN.md", "no badge\n", known)[0]


def test_the_readme_figure_is_drawn_from_the_code():
    figures = SCRIPT.parents[2] / "docs" / "assets" / "make_figures.py"
    res = subprocess.run([sys.executable, str(figures), "--check"], capture_output=True, text=True, timeout=120)
    assert res.returncode == 0, res.stdout + res.stderr


def test_every_level_the_catalog_uses_has_its_own_row_in_the_docs(facts):
    from tcmstudio.catalog import build_catalog
    used = {n for e in build_catalog("browser", probe=False)["entries"] for n in e["verification"]["levels"]}
    assert used <= set(doc_facts.VERIFICATION_LEVELS), used - set(doc_facts.VERIFICATION_LEVELS)
    assert sum(facts[f"level.{n}"] for n in doc_facts.VERIFICATION_LEVELS) > 0
    # the table in docs/validation.md has a row for each level, and a deleted row is reported (not only a wrong count)
    text = (SCRIPT.parents[2] / "docs" / "validation.md").read_text(encoding="utf-8")
    assert doc_facts.check_rows("docs/validation.md", text) == []
    lines = text.splitlines(keepends=True)
    without = "".join(x for x in lines if not x.startswith("| `gateway_mock` |"))
    assert without != text
    problems = doc_facts.check_rows("docs/validation.md", without)
    assert len(problems) == 1 and "gateway_mock" in problems[0], problems
    assert doc_facts.check_text("docs/validation.md", without, facts) == []      # the counts alone would not notice


def test_the_kernel_case_count_is_the_fixtures(facts):
    import json
    cases = json.loads((SCRIPT.parents[2] / doc_facts.KERNEL_CASES).read_text(encoding="utf-8"))["cases"]
    assert facts["kernel_cases"] == len(cases) > 0
    wrong = facts["kernel_cases"] + 1
    text = f"{wrong} parity cases against the Python reference\n输出相同（{wrong} 个一致性用例）\n"
    problems = doc_facts.check_text("studio/docs/NEW.md", text, facts)
    assert len(problems) == 2 and all("kernel_cases" in p for p in problems), problems


def test_the_files_with_no_reviewed_size_are_the_registrys(facts):
    from tcmstudio.datasets import registry_document
    unsized = [f["name"] for f in registry_document()["groups"]["research"]["files"] if f["bytes"] is None]
    assert facts["unsized_files"] == len(unsized) == 2, unsized   # Reactome's UniProt mapping, the UniProt accessions
    # the docs write the count as a word, wrapped across lines too, in either language
    text = ("a storage check: the three files with no reviewed\nsize are not counted; Three files without a reviewed "
            "size are not guessed at.\n构建前检查剩余存储空间；三个没有审核大小的文件不做估计。\n")
    problems = doc_facts.check_text("studio/docs/NEW.md", text, facts)
    assert len(problems) == 3 and all("says 3;" in p and "unsized_files = 2" in p for p in problems), problems
    right = text.replace("three", "two").replace("Three", "Two").replace("三", "两")
    assert doc_facts.check_text("studio/docs/NEW.md", right, facts) == []
    # a numeral the check does not read is left alone, never read by its last character (十二 is not 二)
    assert doc_facts.check_text("studio/docs/NEW.md", "十二个没有审核大小的文件", facts) == []


def test_the_files_without_a_reviewed_checksum_are_the_registrys(facts):
    """docs/validation.md named STRING's aliases file among the files without a reviewed
    checksum after it was pinned: the count is the registry's, and the sentence that names
    them is anchored, so a pin (or a new unpinned file) is noticed in the doc."""
    from tcmstudio.datasets import registry_document
    files = registry_document()["groups"]["research"]["files"]
    unchecked = sorted(f["name"] for f in files if not f["checksum"])
    assert unchecked == ["UniProt2Reactome.txt", "uniprot_human_reviewed_accessions.txt"]
    assert facts["unchecked_files"] == 2
    assert next(f for f in files if f["name"].startswith("9606.protein.aliases"))["checksum"]
    stale = "a stage that reads either of the three files without a reviewed checksum (Reactome's\n"
    problems = doc_facts.check_text("docs/NEW.md", stale + "UniProt mapping and the UniProt accession list,", facts)
    assert len(problems) == 1 and "says 3;" in problems[0] and "unchecked_files = 2" in problems[0], problems
    # the doc says two, and still names them where the anchor looks
    text = (doc_facts.REPO / "docs" / "validation.md").read_text(encoding="utf-8")
    assert doc_facts.check_text("docs/validation.md", text, facts) == []
    reworded = text.replace("either of the two files without a reviewed checksum", "one without a checksum")
    assert any("no longer there" in p for p in doc_facts.check_text("docs/validation.md", reworded, facts))
    assert "STRING's\nalias file, Reactome's UniProt mapping" not in text


def test_the_usage_example_names_the_released_versions():
    known = doc_facts.versions()
    # USAGE.md and INSTALL.md both print the example artifact's version line
    for doc in ("USAGE.md", "INSTALL.md"):
        line = "  versions:   runtime=psh-0.5.3+bioagent-0.2.6|skill=...\n"
        problems = doc_facts.check_versions(doc, line, known)
        assert len(problems) == 2, (doc, problems)
        assert any("psh 0.5.3" in p for p in problems) and any("bioagent 0.2.6" in p for p in problems)
        ok = f"  versions:   runtime=psh-{known['psh']}+bioagent-{known['bioagent']}|skill=...\n"
        assert doc_facts.check_versions(doc, ok, known) == []
    assert "INSTALL.md" in {p.relative_to(doc_facts.REPO).as_posix() for p in doc_facts.docs()}
