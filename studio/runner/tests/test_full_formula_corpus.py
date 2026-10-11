"""Formula questions are answered from the complete workbook, not the six-formula seed.

Users found the system answering from the seed corpus: a herb's formulas were the seed's one
or two, a formula outside the seed could not be checked for incompatibilities, and when the
workbook was missing the seed's answer came back as if it were the whole corpus. Here the
herb, compatibility and fallback paths are checked against counts made independently of the
code under test, on the real 84,294-row workbook.
"""
from __future__ import annotations

import sqlite3

import pytest

from tcmstudio import repository_data
from tcmstudio.dispatch import call
from tcmstudio.governance import LIMIT_SEED


def _rows(where: str, params: tuple = ()) -> set[int]:
    conn = sqlite3.connect(repository_data.formula_store())
    try:
        return {r[0] for r in conn.execute(f"SELECT row FROM formulas WHERE {where}", params)}
    finally:
        conn.close()


# ------------------------------------------------------------------------- a herb's formulas

def test_a_herb_lists_the_complete_workbooks_formulas_beside_the_seeds(browser_ctx):
    e = call("tcm_herb", {"name": "黄芪"}, browser_ctx)
    assert e["status"] == "succeeded", e["error"]
    found = e["result"]["repository_formulas"]
    expected = _rows("instr(composition, '黄芪') > 0 OR instr(composition, '黄耆') > 0")
    assert found["total"] == len(expected) > 5000, "黄耆 is how most of the workbook writes it"
    assert found["herb"]["searched"] == ["黄芪", "黄耆"] and found["corpus_rows"] == 84294
    assert {r["row"] for r in found["records"]} <= expected and found["count"] == 20
    assert found["more"] == {"tool": "tcmdb.formulas", "arguments": {"herb": "黄芪", "offset": 20}}
    assert e["result"]["formulas"], "the seed's curated formulas, with their roles, stay"
    assert "5,8" in e["summary"] and "黄耆" in e["summary"]
    limits = e["governance"]["limitations"]
    assert LIMIT_SEED not in limits, "the formulas are no longer the seed's alone"
    assert any("source records whose composition names the herb" in x for x in limits)
    assert any(lic["asset"] == "repository_formulas" for lic in e["governance"]["licences"])


def test_another_drug_whose_name_contains_the_herb_is_not_counted(browser_ctx):
    found = call("tcm_herb", {"name": "茯苓"}, browser_ctx)["result"]["repository_formulas"]
    assert found["herb"]["not_within"] == {"茯苓": ["土茯苓"]}
    needles = found["herb"]["searched"]
    blanked = "replace(composition, '土茯苓', '|')"
    expected = _rows(" OR ".join(f"instr({blanked if n == '茯苓' else 'composition'}, ?) > 0" for n in needles),
                     tuple(needles))
    assert found["total"] == len(expected)
    only_tu = _rows("instr(composition, '土茯苓') > 0 AND instr(replace(composition, '土茯苓', '|'), '茯苓') = 0")
    assert len(only_tu) > 100, "the workbook has formulas with 土茯苓 and no 茯苓"
    # of those, only the ones that also name 茯苓 another way (妙济丹 has 云苓) are its formulas
    other_names = _rows(" OR ".join("instr(composition, ?) > 0" for n in needles if n != "茯苓"),
                        tuple(n for n in needles if n != "茯苓"))
    assert only_tu & expected == only_tu & other_names and len(only_tu & expected) < 5


def test_a_herb_outside_the_seed_gets_its_workbook_formulas_too(browser_ctx):
    e = call("tcm_herb", {"name": "葛根"}, browser_ctx)
    assert e["result"]["annotation_status"] == "identity_only"
    assert e["result"]["repository_formulas"]["total"] > 1000


def test_tcmdb_formulas_pages_through_a_herbs_formulas_in_one_stable_order(browser_ctx):
    def page(offset, limit):
        e = call("call_tool", {"tool": "tcmdb.formulas",
                               "arguments": {"herb": "黄芪", "offset": offset, "limit": limit}}, browser_ctx)
        assert e["status"] == "succeeded", e["error"]
        return e["result"]
    whole = page(0, 60)
    assert [r["row"] for r in page(0, 30)["records"] + page(30, 30)["records"]] == [r["row"] for r in whole["records"]]
    herb_total = call("tcm_herb", {"name": "黄芪"}, browser_ctx)["result"]["repository_formulas"]["total"]
    assert whole["total"] == herb_total and whole["herb"]["searched"] == ["黄芪", "黄耆"]
    named = [("黄芪" in r["name"] or "黄耆" in r["name"]) for r in whole["records"]]
    assert named == sorted(named, reverse=True), "the formulas named after the herb come first"


# ------------------------------------------------------------------------- a syndrome's formulas

def test_a_syndrome_lists_the_workbook_formulas_whose_actions_mention_it(browser_ctx):
    e = call("tcm_syndrome", {"name": "脾胃气虚证"}, browser_ctx)
    assert e["status"] == "succeeded", e["error"]
    found = e["result"]["repository_formulas"]
    expected = _rows("instr(actions, '脾胃气虚') > 0")
    assert found["total"] == len(expected) > 100 and found["indication"]["terms"] == ["脾胃气虚"]
    assert {r["row"] for r in found["records"]} <= expected and found["count"] == 20
    assert found["more"] == {"tool": "tcmdb.formulas", "arguments": {"indication": "脾胃气虚", "offset": 20}}
    assert [f["chinese"] for f in e["result"]["formulas"]] == ["补中益气汤", "四君子汤"], "the seed's stay"
    limits = e["governance"]["limitations"]
    assert LIMIT_SEED not in limits and any("never their 注意 cautions" in x for x in limits)
    assert f"{len(expected)} 条方剂的功效提及「脾胃气虚」" in e["summary"]


def test_a_syndromes_other_names_are_searched_too(browser_ctx):
    found = call("tcm_syndrome", {"name": "太阳中风证"}, browser_ctx)["result"]["repository_formulas"]
    assert found["indication"]["terms"] == ["太阳中风", "表虚"], "表虚证 is the seed's other name for it"
    assert found["total"] == len(_rows("instr(actions, '太阳中风') > 0 OR instr(actions, '表虚') > 0"))


def test_a_contraindication_is_never_counted_as_an_indication():
    # 注意 says whom a formula is not for: 气虚人减量服, 气虚挟寒者慎用
    only_cautions = _rows("instr(cautions, '气虚') > 0 AND instr(actions, '气虚') = 0")
    assert len(only_cautions) > 10
    found = repository_data.formula_search("", indication="气虚", limit=5000)
    assert found["total"] == len(_rows("instr(actions, '气虚') > 0")) == found["count"]
    assert not {r["row"] for r in found["records"]} & only_cautions


def test_a_syndrome_outside_the_seed_says_how_many_workbook_formulas_mention_it(browser_ctx):
    e = call("tcm_syndrome", {"name": "湿热证"}, browser_ctx)
    assert e["error"]["type"] == "not_found", "a mention is not a syndrome record"
    n = len(_rows("instr(actions, '湿热') > 0"))
    assert n > 500 and f"{n:,} 条方剂的功效提及「湿热」" in e["summary"]
    assert e["error"]["hint"].startswith('tcmdb.formulas {"indication": "湿热"}')
    listed = call("call_tool", {"tool": "tcmdb.formulas", "arguments": {"indication": "湿热"}}, browser_ctx)
    assert listed["status"] == "succeeded" and listed["result"]["total"] == n
    none = call("tcm_syndrome", {"name": "肝郁脾虚证"}, browser_ctx)
    assert none["error"]["type"] == "not_found" and "字面匹配，不等于没有相应方剂" in none["summary"]
    english = call("tcm_syndrome", {"name": "damp heat"}, browser_ctx)
    assert english["error"]["type"] == "not_found" and "完整方剂库" not in english["summary"], \
        "the 功效 text is Chinese: an English name is not searched in it"


def test_a_ranked_search_within_an_indication(browser_ctx):
    e = call("call_tool", {"tool": "tcmdb.formulas", "arguments": {
        "query": "黄芪", "rank": True, "indication": "脾胃气虚", "limit": 100}}, browser_ctx)
    within = _rows("instr(actions, '脾胃气虚') > 0")
    assert e["result"]["rank"]["rows_searched"] == len(within)
    assert {r["row"] for r in e["result"]["records"]} <= within and e["result"]["total"] > 0


# ------------------------------------------------------------------------- compatibility

def test_a_formula_outside_the_seed_is_checked_as_its_workbook_herbs(browser_ctx):
    e = call("tcm_compatibility", {"herbs": ["甘遂半夏汤"]}, browser_ctx)
    assert e["status"] == "succeeded", e["error"]
    expanded, = e["result"]["expanded_formulas"]
    assert expanded["formula"] == "甘遂半夏汤" and expanded["herbs"] == ["甘遂", "半夏", "芍药", "甘草"]
    assert any("甘遂反甘草" in c["description"] for c in e["result"]["conflicts"]), "the 十八反 pair in it"
    assert e["result"]["compatible"] is False
    assert "按完整方剂库组成展开" in e["summary"]


def test_herbs_without_safety_records_stay_unresolved_never_safe(browser_ctx):
    e = call("tcm_compatibility", {"herbs": ["葛根芩连汤", "甘草"]}, browser_ctx)
    assert e["result"]["expanded_formulas"][0]["herbs"] == ["葛根", "甘草", "黄芩", "黄连"]
    assert sorted(u["name"] for u in e["result"]["unresolved"]) == ["葛根", "黄芩", "黄连"]
    assert e["result"]["compatible"] is False
    assert any("not safe" in x for x in e["governance"]["limitations"])


def test_a_formula_with_several_versions_is_not_guessed(browser_ctx):
    e = call("tcm_compatibility", {"herbs": ["四逆散"]}, browser_ctx)
    ambiguous, = e["result"]["ambiguous_formulas"]
    assert ambiguous["formula"] == "四逆散" and ambiguous["versions"] == 3
    assert "expanded_formulas" not in e["result"]
    assert "3 个版本" in e["summary"]


def test_a_herb_is_never_taken_for_a_formula_of_the_same_name(browser_ctx):
    # the workbook's one formula called 雷丸 is 草乌头, 川乌头, 两头尖 … with no 雷丸 in it
    assert repository_data.formula_search("雷丸", exact=True)["total"] == 1
    e = call("tcm_compatibility", {"herbs": ["雷丸", "甘草"]}, browser_ctx)
    assert e["status"] == "succeeded" and "expanded_formulas" not in e["result"]
    assert [u["name"] for u in e["result"]["unresolved"]] == ["雷丸"]
    assert e["result"]["compatible"] is False


def test_a_seed_formula_keeps_its_curated_composition(browser_ctx):
    e = call("tcm_compatibility", {"herbs": ["桂枝汤"]}, browser_ctx)
    assert e["status"] == "succeeded" and "expanded_formulas" not in e["result"]


# ------------------------------------------------------------------------- never silently seed-only

@pytest.fixture
def no_workbook(monkeypatch, tmp_path):
    monkeypatch.setenv("BIOAGENT_FORMULA_SQLITE", str(tmp_path / "missing.sqlite"))
    repository_data.take_formula_store_wanted()
    yield


def test_without_the_workbook_the_seed_answer_says_it_is_only_the_seed(browser_ctx, no_workbook):
    e = call("tcm_formula", {"name": "桂枝汤"}, browser_ctx)
    assert e["status"] == "succeeded"
    assert e["result"]["repository_records"]["status"] == "unavailable"
    assert e["governance"]["limitations"][0].startswith("The complete formula workbook (84,294 rows) is not loaded")
    assert "完整方剂库未加载" in e["summary"] and "seed corpus only" in e["summary_en"]
    herb = call("tcm_herb", {"name": "黄芪"}, browser_ctx)
    assert herb["result"]["repository_formulas"]["status"] == "unavailable"
    assert repository_data.take_formula_store_wanted() is True, "the browser's Worker loads it and runs again"
    assert repository_data.take_formula_store_wanted() is False
    check = call("tcm_compatibility", {"herbs": ["甘遂半夏汤"]}, browser_ctx)
    assert check["result"]["repository_formulas"]["status"] == "unavailable"
    assert check["governance"]["limitations"][0] == herb["governance"]["limitations"][0]
    assert repository_data.take_formula_store_wanted() is True
    # herbs alone never need the workbook, so checking them downloads nothing
    call("tcm_compatibility", {"herbs": ["葛根", "甘草"]}, browser_ctx)
    assert repository_data.take_formula_store_wanted() is False
    syndrome = call("tcm_syndrome", {"name": "脾胃气虚证"}, browser_ctx)
    assert syndrome["result"]["repository_formulas"]["status"] == "unavailable"
    outside = call("tcm_syndrome", {"name": "湿热证"}, browser_ctx)
    assert outside["error"]["type"] == "not_found" and "完整方剂库未加载" in outside["summary"]
    assert outside["governance"]["limitations"][0] == herb["governance"]["limitations"][0]
    assert repository_data.take_formula_store_wanted() is True


def test_the_capabilities_say_which_formula_corpus_is_there(browser_ctx):
    e = call("capabilities_status", {}, browser_ctx)
    assert e["result"]["formulas"]["workbook"]["status"] in ("loaded", "available")
    assert e["result"]["formulas"]["seed"]["formulas"] == 6


def test_a_missing_workbook_is_reported_with_its_fix(browser_ctx, no_workbook):
    status = repository_data.formula_corpus_status()["workbook"]
    assert status["status"] == "unavailable" and "BIOAGENT_FORMULA_WORKBOOK" in status["fix"]
    e = call("capabilities_status", {}, browser_ctx)
    assert e["result"]["formulas"]["workbook"]["status"] == "on_demand", "the page loads it when needed"


# ------------------------------------------------------------------------- governed runs point onward

def test_governed_seed_runs_point_to_the_complete_corpus(browser_ctx):
    net = call("tcm_network_hypothesis", {"formula_name": "葛根芩连汤"}, browser_ctx)
    assert net["status"] == "succeeded" and "tcm_formula" in net["summary"]
    norm = call("tcm_normalize", {"names": ["葛根芩连汤", "黄芪"]}, browser_ctx)
    assert "tcm_lookup" in norm["summary"]


# ------------------------------------------------------------------------- a formula and its book

def test_a_formula_named_with_its_book_is_that_books_version(browser_ctx):
    # the research tools ask for 桂枝汤《伤寒论》; tcm_formula refused it as a partial match
    e = call("tcm_formula", {"name": "桂枝汤《伤寒论》"}, browser_ctx)
    assert e["status"] == "succeeded", e["error"]
    records = e["result"]["repository_records"]
    assert records["total"] == 1 and "伤寒论" in records["records"][0]["source"]
    assert records["records"][0]["id"] == "tcm:formula.fx559d1e07f05c"
    assert e["result"]["formula"]["id"] == "formula.guizhitang", "the curated 伤寒论 formula"


def test_the_curated_annotations_stay_with_their_own_book(browser_ctx):
    e = call("tcm_formula", {"name": "桂枝汤《圣济总录》"}, browser_ctx)
    assert e["status"] == "succeeded", e["error"]
    assert "formula" not in e["result"], "the 伤寒论 roles and passages are not this version's"
    assert all("圣济总录" in r["source"] for r in e["result"]["repository_records"]["records"])
    assert e["result"]["repository_records"]["total"] == len(
        _rows("clean_name = '桂枝汤' AND instr(source, '圣济总录') > 0"))


def test_a_book_no_version_cites_is_not_found_with_the_books_they_cite(browser_ctx):
    e = call("tcm_formula", {"name": "桂枝甘草汤《金匮》"}, browser_ctx)
    assert e["status"] == "failed" and e["error"]["type"] == "not_found"
    assert "《伤寒论》" in e["error"]["message"] and "cites 《金匮》" in e["error"]["message"]


def test_a_misread_composition_is_read_through_its_erratum_and_says_so(browser_ctx):
    # the workbook writes 草2两（炙） for 甘草; 《伤寒论》第64条 gives 甘草二两（炙）
    e = call("tcm_formula", {"name": "桂枝甘草汤《伤寒论》"}, browser_ctx)
    assert e["status"] == "succeeded", e["error"]
    row, = e["result"]["repository_records"]["records"]
    assert row["composition"] == "桂枝4两（去皮），草2两（炙）。", "the workbook text is kept"
    assert [c["drug"] for c in row["components"]] == ["guizhi", "gancao"] and row["resolved"]
    assert row["erratum"]["id"] == "erratum.0001" and row["erratum"]["status"] == "proposed"
    assert "第64条" in row["erratum"]["authority"]
    assert any("erratum.0001" in x and "awaiting a maintainer's review" in x
               for x in e["governance"]["limitations"])


def test_a_formula_written_with_its_book_is_expanded_into_that_versions_herbs(browser_ctx):
    e = call("tcm_compatibility", {"herbs": ["桂枝甘草汤《伤寒论》"]}, browser_ctx)
    assert e["status"] == "succeeded", e["error"]
    expanded, = e["result"]["expanded_formulas"]
    assert expanded["herbs"] == ["桂枝", "甘草"]
