"""Every pair the clinic pack's 十八反 / 十九畏 table names is reported by the tools that read it.

The table names 57 herbs, and eight of them are not in the materia table (西洋参, 南沙参, 人参叶,
红大戟, 平贝母, 伊贝母, 湖北贝母 and 狼毒, whose identity is disputed). The table was keyed by
their names as written, but the subject, the co-administered herbs and the herbs to check were
looked up only by materia id, so their 21 pairs never fired: 藜芦 + 西洋参 was reported as
"未见配伍禁忌记录" while the same reply listed 西洋参 among the herbs 藜芦 is not to be combined
with. These tests run every pair of the pack through both tools.
"""
from __future__ import annotations

import pytest

from bioagent.clinic.pack import load_pack
from tcmstudio.dispatch import call

PACK = load_pack()
NO_RECORD = "未见配伍禁忌记录（不等于可以合用）"


def test_every_pair_of_the_table_is_a_conflict_in_tcm_compatibility(browser_ctx):
    missed = []
    for a, b, rule in PACK.incompatible:
        e = call("tcm_compatibility", {"herbs": [a, b]}, browser_ctx)
        assert e["status"] == "succeeded", e["error"]
        found = e["result"].get("rule_table_conflicts") or []
        if not any(set(p["pair"]) == {a, b} and p["rule"] == rule for p in found):
            missed.append(f"{a}+{b}")
        elif e["result"]["compatible"] is not False:
            missed.append(f"{a}+{b} (compatible)")
    assert missed == [], f"{len(missed)} of {len(PACK.incompatible)} pairs not reported"


@pytest.mark.parametrize("subject", sorted({n for a, b, _ in PACK.incompatible for n in (a, b)}))
def test_every_pair_of_the_table_is_a_conflict_in_tcm_safety_report(subject, browser_ctx):
    partners = PACK.incompatible_with(subject)
    e = call("tcm_safety_report", {"subject": subject, "co_administered": partners}, browser_ctx)
    assert e["status"] == "succeeded", e["error"]
    table = e["result"].get("rule_table")
    assert table, f"no rule table for {subject}"
    assert {r["herb"] for r in table["incompatible_with"]} <= set(partners)
    conflicts = table.get("co_administered_conflicts") or []
    # one conflict per co-administered herb: each of them matched its row of the table
    assert len(conflicts) == len(partners), (subject, partners, conflicts)
    assert NO_RECORD not in e["summary"]


def test_a_name_outside_the_materia_table_is_matched_as_written(browser_ctx):
    s = call("tcm_safety_report", {"subject": "藜芦", "co_administered": ["西洋参"]}, browser_ctx)
    assert [r["herb"] for r in s["result"]["rule_table"]["co_administered_conflicts"]] == ["西洋参"]
    assert s["summary"].startswith("藜芦 + 西洋参：种子语料未见此配伍禁忌，规则表记载藜芦与西洋参不宜同用")
    s = call("tcm_safety_report", {"subject": "西洋参", "co_administered": ["藜芦"]}, browser_ctx)
    assert [r["herb"] for r in s["result"]["rule_table"]["incompatible_with"]] == ["藜芦"]
    c = call("tcm_compatibility", {"herbs": ["甘草", "红大戟"]}, browser_ctx)
    assert c["result"]["rule_table_conflicts"][0]["pair"] == ["甘草", "红大戟"]
    assert "规则表：甘草与红大戟（十八反）不宜同用" in c["summary"]
    # a name neither table knows still names nothing
    c = call("tcm_compatibility", {"herbs": ["藜芦", "某药"]}, browser_ctx)
    assert "rule_table_conflicts" not in c["result"]
