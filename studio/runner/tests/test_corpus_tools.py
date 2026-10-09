"""The seven corpus tools through ``tcmstudio.dispatch.call`` (docs/V2.md §11.5): the
envelope (kind ``corpus``, ``receipt.corpus``, licences and limits per pack read), what each
tool answers, the rules it keeps, and the same answers in the browser (synchronous XHR) as
on the runner (a directory). The last tests read the real 84,294-row table (slow)."""

from __future__ import annotations

import sys
import time
import warnings

import pytest

from tcmstudio.catalog import build_catalog, get_entry
from tcmstudio.core_tools import CORE_BY_NAME, route
from tcmstudio.corpus.reader import reset_default_corpora
from tcmstudio.dispatch import call
from tcmstudio.envelope import KINDS
from test_corpus_support import FakeJS, boot_entry, tiny_corpus

pytest.importorskip("openpyxl")

# The two tests that read the real table are marked ``slow`` (deselect them with -m "not
# slow"); until pyproject.toml registers the mark, keep pytest from warning about it.
warnings.filterwarnings("ignore", message=r"Unknown pytest\.mark\.slow")

CORPUS_TOOLS = ("corpus_search", "corpus_herb", "corpus_formula", "corpus_formulas_with",
                "corpus_compounds", "corpus_safety", "corpus_info")


@pytest.fixture(scope="module")
def tiny(tmp_path_factory):
    return tiny_corpus(tmp_path_factory)


@pytest.fixture
def ctx(tiny, monkeypatch):
    monkeypatch.delenv("TCMSTUDIO_CORPUS", raising=False)
    reset_default_corpora()
    return {"where": "runner", "corpus": {"dir": str(tiny["site"])}}


def _check_envelope(env, tool):
    assert env["tool"] == tool and env["via"] == "corpus." + tool.split("_", 1)[1]
    assert env["governance"]["kind"] == "corpus"
    assert len(env["text"]) <= 16_000
    assert env["text"].startswith(f"{tool} → {env['via']}: {env['status']}")
    rc = env["receipt"].get("corpus")
    assert rc and rc["snapshot_id"].startswith("tcmcorpus-") and len(rc["manifest_sha256"]) == 64
    assert rc["reads_total"] == len(rc["reads"]) <= 50
    assert env["summary"] and env["summary_en"]


# ------------------------------------------------------------------- registration

def test_seven_core_tools_and_their_entries():
    assert "corpus" in KINDS
    doc = build_catalog("browser", probe=False)
    core = {c["name"]: c for c in doc["core"]}
    for name in CORPUS_TOOLS:
        c = core[name]
        assert c["exec"] == ["browser", "runner"] and not c["network"] and not c["confirm"]
        entry_id, _ = route(name, {})
        assert c["maps_to"] == entry_id == "corpus." + name.split("_", 1)[1]
        e = get_entry(entry_id)
        assert e["kind"] == "corpus" and e["exec"] == ["browser", "runner"]
        assert e["title"]["zh"] and e["title"]["en"] and e["example"] is not None
        assert 40 <= len(c["description"]) <= 1000
    text = {n: CORE_BY_NAME[n]["description"] for n in CORPUS_TOOLS}
    assert "not absence" in text["corpus_search"]
    assert "no record ≠ safe" in text["corpus_safety"] and "no record ≠ safe" in text["corpus_herb"]
    assert "not a quotation" in text["corpus_formula"]
    assert "not an active constituent" in text["corpus_compounds"]
    assert doc["counts"]["by_kind"]["corpus"] == 7


# ------------------------------------------------------------------------- tools

def test_every_tool_answers_with_a_corpus_envelope(ctx):
    args = {"corpus_search": {"query": "桂枝"}, "corpus_herb": {"name": "黄芪"},
            "corpus_formula": {"name": "桂枝汤"}, "corpus_formulas_with": {"herbs": ["黄芪"]},
            "corpus_compounds": {"herb": "甘草"}, "corpus_safety": {"herbs": ["甘草", "海藻"]},
            "corpus_info": {}}
    for tool in CORPUS_TOOLS:
        env = call(tool, args[tool], ctx)
        assert env["status"] == "succeeded", (tool, env["error"])
        _check_envelope(env, tool)
        lic = {x["asset"]: x for x in env["governance"]["licences"]}
        assert "corpus:core" in lic, tool
        assert env["governance"]["corpus"]["attribution_url"] == "https://science.impf.ai/attribution.html"


def test_licences_and_limits_follow_the_packs_read(ctx):
    env = call("corpus_formula", {"name": "桂枝汤", "source": "伤寒"}, ctx)
    assert env["status"] == "succeeded"
    lic = {x["asset"]: x for x in env["governance"]["licences"]}
    assert lic["corpus:formulas"]["licence"] == "LicenseRef-owner-published-unstated"
    assert lic["corpus:formulas"]["commercial"] is False
    assert "owner's decision" in lic["corpus:formulas"]["note"]
    assert "所有者决定在 science.impf.ai 公开发布" in lic["corpus:formulas"]["note"]
    limits = env["governance"]["limitations"]
    assert any("出处 is a pointer" in x for x in limits)
    assert limits.index(next(x for x in limits if "出处" in x)) < \
        limits.index(next(x for x in limits if "materia layer" in x)), "the table's limits first"
    assert "出处 is a pointer" in env["text"] and "commercial" in env["text"]
    zh = {p["id"]: p for p in env["governance"]["corpus"]["packs"]}
    assert zh["formulas"]["limitations"][0]["zh"]
    compounds = call("corpus_compounds", {"herb": "甘草"}, ctx)
    lic = {x["asset"]: x for x in compounds["governance"]["licences"]}
    assert lic["corpus:lotus"]["licence"] == "CC-BY-4.0" and lic["corpus:lotus"]["commercial"] is True
    assert any("presence is not an active constituent" in x
               for x in compounds["governance"]["limitations"])
    assert any(c["kind"] == "doi" and "10.7554/eLife.70780" in c["url"] for c in compounds["citations"])


def test_search_reports_kinds_aliases_and_table_rows(ctx):
    env = call("corpus_search", {"query": "人参"}, ctx)
    r = env["result"]
    assert r["counts"]["herb"] >= 1 and r["counts"]["formula_table"] == 4
    assert r["matches"][0]["id"] == "herb.renshen" and r["matches"][0]["match"] == "exact"
    assert [row[3] for row in r["table"]["rows"]][:4] == ["prefix"] * 4
    alias = call("corpus_search", {"query": "炙甘草", "kinds": ["herb"]}, ctx)["result"]["matches"]
    assert alias[0]["id"] == "herb.gancao" and alias[0]["alias_kind"] == "processed"
    assert "not the crude drug itself" in alias[0]["note"]
    src = call("corpus_search", {"query": "圣惠", "kinds": ["formula"]}, ctx)["result"]["table"]
    assert src["source_matches"] == 3 and src["name_total"] == 0
    none = call("corpus_search", {"query": "绝无此名"}, ctx)
    assert none["status"] == "failed" and none["error"]["type"] == "not_found"
    assert "不等于不存在" in none["summary"] and "not absence" in none["error"]["hint"]
    _check_envelope(none, "corpus_search")


def test_herb_record_keeps_layers_and_unstated_values(ctx):
    env = call("corpus_herb", {"name": "葛根"}, ctx)
    r = env["result"]
    assert r["identity_only"] is True
    h = r["herb"]
    assert h["toxicity"] == "" and h["actions"] == [] and h["nature"] == ""
    assert "性味、归经、功效未录（不作推定）" in env["summary"]
    assert r["formula_table"] is not None and r["lotus"]["compound_count"] == 115
    seed = call("corpus_herb", {"name": "huang qi"}, ctx)          # pinyin resolves
    assert seed["result"]["herb"]["id"] == "herb.huangqi" and seed["result"]["identity_only"] is False
    assert seed["result"]["formula_table"]["count"] == 4
    assert [row[0] for row in seed["result"]["formula_table"]["first"]] == [3, 6, 7, 10]
    assert any("《神农本草经》" in c["label"] for c in seed["citations"])
    fuzi = call("corpus_herb", {"name": "附子"}, ctx)
    assert len({p["statement"] for p in fuzi["result"]["pregnancy"]}) == 2
    assert "种子语料" in fuzi["summary"] and "临床知识包" in fuzi["summary"]


def test_a_processed_form_is_never_resolved_to_the_crude_drug(ctx):
    env = call("corpus_herb", {"name": "炙甘草"}, ctx)
    assert env["status"] == "failed" and env["error"]["type"] == "not_found"
    assert env["result"]["alias_of"]["id"] == "herb.gancao"
    assert env["result"]["alias_of"]["kind"] == "processed"
    assert "甘草" in env["error"]["hint"]
    missing = call("corpus_herb", {"name": "绝无此药"}, ctx)
    assert missing["error"]["type"] == "not_found" and "不等于不存在" in missing["summary"]
    syndrome = call("corpus_herb", {"name": "气虚证"}, ctx)
    assert "syndrome" in syndrome["error"]["hint"]


def test_formula_curated_first_then_the_table(ctx):
    env = call("corpus_formula", {"name": "桂枝汤"}, ctx)
    r = env["result"]
    assert r["curated"][0]["id"] == "formula.guizhitang" and r["curated"][0]["roles_recorded"]
    assert r["curated"][1]["layer"] == "clinic" and not r["curated"][1]["roles_recorded"]
    assert r["table_variants"]["count"] == 2
    assert "种子语料" in env["summary"] and "方剂表另有同名记录 2 行" in env["summary"]
    many = call("corpus_formula", {"name": "人参散"}, ctx)["result"]
    assert many["status"] == "candidates" and many["count"] == 4
    assert [c[0] for c in many["candidates"]] == [2, 3, 4, 5]
    one = call("corpus_formula", {"name": "人参散", "source": "千金"}, ctx)
    row = one["result"]["rows"][0]
    assert row["rowno"] == 5 and row["sheet_row"] == 7 and row["resolved"]
    assert [c["herb_id"] for c in row["components"]] == ["herb.renshen", "herb.danggui"]
    assert one["citations"][0]["label"] == "中医方剂数据表 第 7 行 · 出处《千金》卷二"
    assert one["citations"][0]["kind"] == "source"
    by_row = call("corpus_formula", {"rowno": 9}, ctx)["result"]["rows"][0]
    assert by_row["name"] == "怪方" and not by_row["resolved"]
    assert by_row["unresolved"] == ["贝母", "防葵"]
    alone = call("corpus_formula", {"id": by_row["id"]}, ctx)["result"]["rows"]
    assert [x["rowno"] for x in alone] == [9]
    six = call("corpus_formula", {"rowno": 6}, ctx)["result"]["rows"][0]
    dup = call("corpus_formula", {"id": six["id"]}, ctx)              # an id two rows share
    assert [x["rowno"] for x in dup["result"]["rows"]] == [6, 10] and len(dup["citations"]) == 2
    empty = call("corpus_formula", {"rowno": 8}, ctx)
    assert empty["error"]["type"] == "not_found" and "empty" in empty["error"]["message"]
    out = call("corpus_formula", {"rowno": 999}, ctx)
    assert out["error"]["type"] == "bad_arguments"
    none = call("corpus_formula", {}, ctx)
    assert none["error"]["type"] == "bad_arguments"
    absent = call("corpus_formula", {"name": "绝无此方"}, ctx)
    assert absent["error"]["type"] == "not_found" and "不等于古籍未载" in absent["summary"]


def test_formulas_with_counts_and_pages(ctx):
    env = call("corpus_formulas_with", {"herbs": ["黄芪", "人参"]}, ctx)
    r = env["result"]
    assert r["total"] == 4 and [row[0] for row in r["rows"]] == [3, 6, 7, 10]
    anyof = call("corpus_formulas_with", {"herbs": ["当归", "茯苓"], "match": "any"}, ctx)["result"]
    assert anyof["total"] == 4
    page = call("corpus_formulas_with", {"herbs": ["人参"], "limit": 2, "offset": 2}, ctx)["result"]
    assert page["total"] == 7 and [row[0] for row in page["rows"]] == [4, 5]
    processed = call("corpus_formulas_with", {"herbs": ["炙甘草"]}, ctx)
    assert "counts as 甘草" in processed["result"]["herbs"][0]["note"]
    bad = call("corpus_formulas_with", {"herbs": ["绝无此药"]}, ctx)
    assert bad["error"]["type"] == "not_found"


def test_compounds_and_their_absence(ctx):
    env = call("corpus_compounds", {"herb": "甘草", "limit": 5}, ctx)
    r = env["result"]
    assert r["total"] == 734 and len(r["compounds"]) == 5
    refs = [c["refs"] for c in r["compounds"]]
    assert refs == sorted(refs, reverse=True)
    assert {o["matched_by"] for o in r["organisms"]} <= {"taxid", "name"}
    assert "不等于有效成分" in env["summary"]
    mineral = call("corpus_compounds", {"herb": "石膏"}, ctx)
    assert mineral["status"] == "succeeded" and mineral["result"]["total"] == 0
    assert mineral["result"]["unmatched"]["reason"] == "not_an_organism"
    assert "不等于不含成分" in mineral["summary"]


def test_safety_records_pairs_and_unrecognised_names(ctx):
    env = call("corpus_safety", {"herbs": ["甘草", "海藻"]}, ctx)
    r = env["result"]
    assert len(r["pairs"]) == 1 and r["pairs"][0]["rule"].startswith("十八反")
    assert r["pairs"][0]["severity"] == "unstated" and r["pairs"][0]["layer"] == "clinic"
    assert "记载 1 处配伍禁忌（十八反）" in env["summary"]
    assert "无记录不等于安全" in env["summary"]
    assert "no record is not safety" in env["text"]
    both = call("corpus_safety", {"herbs": ["甘草", "甘遂"]}, ctx)["result"]
    assert {p["layer"] for p in both["pairs"]} == {"seed", "clinic"}
    named = call("corpus_safety", {"herbs": ["藜芦", "西洋参", "无名草"]}, ctx)
    assert named["result"]["unresolved"] == ["无名草"]
    assert any(p["between"] == ["藜芦", "西洋参"] for p in named["result"]["pairs"])
    drug = call("corpus_safety", {"herbs": ["甘草", "地高辛"]}, ctx)["result"]
    assert drug["unresolved"] == [] and drug["pairs"][0]["kind"] == "interaction"
    processed = call("corpus_safety", {"herbs": ["法半夏", "附子"]}, ctx)
    assert processed["result"]["pairs"], "法半夏 is matched to 半夏's records"
    assert "processing can change toxicity" in processed["text"]
    nothing = call("corpus_safety", {"herbs": ["无名草"]}, ctx)
    assert nothing["error"]["type"] == "not_found" and "未识别不等于安全" in nothing["summary"]


def test_info_describes_the_snapshot(ctx, tiny):
    env = call("corpus_info", {}, ctx)
    r = env["result"]
    assert r["snapshot_id"] == tiny["summary"]["snapshot_id"]
    assert set(r["packs"]) == {"core", "formulas", "lotus", "pathways"}
    assert r["packs"]["formulas"]["publication"]["decided_by"].startswith("IMPF-AI")
    assert "NPASS" in r["never_published"]
    assert env["receipt"]["corpus"]["reads"] == []


def test_a_commercial_project_does_not_read_the_formula_table(tiny, monkeypatch):
    monkeypatch.delenv("TCMSTUDIO_CORPUS", raising=False)
    ctx = {"where": "runner", "purpose": "commercial", "corpus": {"dir": str(tiny["site"])}}
    env = call("corpus_formula", {"name": "人参散"}, ctx)
    assert env["status"] == "refused" and env["error"]["type"] == "refused"
    assert env["governance"]["refusals"][0]["code"] == "usage.corpus.formulas"
    assert env["governance"]["refusals"][0]["remedy"]
    assert not any(r["path"].startswith("formulas/") for r in env["receipt"]["corpus"]["reads"])
    curated = call("corpus_formula", {"name": "桂枝汤"}, ctx)
    assert curated["status"] == "succeeded" and curated["result"]["table_variants"] is None
    assert curated["governance"]["refusals"][0]["code"] == "usage.corpus.formulas"
    search = call("corpus_search", {"query": "人参"}, ctx)
    assert search["result"]["table"] is None and search["governance"]["refusals"]
    herb = call("corpus_herb", {"name": "黄芪"}, ctx)
    assert herb["result"]["formula_table"] is None


def test_a_missing_corpus_fails_with_the_remedy(tmp_path, monkeypatch):
    monkeypatch.delenv("TCMSTUDIO_CORPUS", raising=False)
    reset_default_corpora()
    env = call("corpus_herb", {"name": "黄芪"},
               {"where": "runner", "corpus": {"dir": str(tmp_path / "nowhere")}})
    assert env["status"] == "failed" and env["error"]["type"] == "unavailable"
    assert env["error"]["hint"]
    import tcmstudio.corpus.reader as R
    monkeypatch.setattr(R, "_site_candidates", lambda: [])
    offline = call("corpus_info", {}, {"where": "runner", "network": False,
                                       "state_root": str(tmp_path / "home" / "projects")})
    assert offline["error"]["type"] == "network_off"
    assert "tcmstudio corpus fetch" in offline["error"]["hint"]
    assert "未联网" in offline["summary"]


def test_bad_arguments_are_refused_before_the_corpus_is_read(ctx):
    env = call("corpus_herb", {"nmae": "黄芪"}, ctx)
    assert env["error"]["type"] == "bad_arguments" and "name" in env["error"]["hint"]
    env = call("corpus_search", {"query": "x", "kinds": ["drug"]}, ctx)
    assert env["error"]["type"] == "bad_arguments"


@pytest.mark.parametrize("tool, args", [
    ("corpus_formula", {"name": "人参散", "source": "千金"}),
    ("corpus_formula", {"name": "桂枝汤"}),
    ("corpus_herb", {"name": "甘草"}),
    ("corpus_safety", {"herbs": ["甘草", "海藻"]}),
    ("corpus_compounds", {"herb": "黄芪", "limit": 3}),
    ("corpus_formulas_with", {"herbs": ["黄芪", "人参"]}),
    ("corpus_search", {"query": "黄芪"}),
    ("corpus_info", {}),
])
def test_the_browser_reads_the_same_answers_as_the_runner(tiny, monkeypatch, tool, args):
    """The same tool through a directory (runner) and through the worker's synchronous XHR
    (browser, a fake ``js`` module) returns the same result, summary, citations and reads."""
    monkeypatch.delenv("TCMSTUDIO_CORPUS", raising=False)
    reset_default_corpora()
    runner = call(tool, args, {"where": "runner", "corpus": {"dir": str(tiny["site"])}})
    base = "https://science.test/corpus/"
    fake = FakeJS(tiny["site"] / "corpus", base)
    monkeypatch.setitem(sys.modules, "js", fake)
    reset_default_corpora()
    browser = call(tool, args, {"where": "browser",
                                "corpus": {"base_url": base, **boot_entry(tiny["summary"])}})
    assert browser["status"] == runner["status"] == "succeeded"
    for key in ("result", "summary", "summary_en", "citations"):
        assert browser[key] == runner[key], key
    assert browser["receipt"]["where"] == "browser" and runner["receipt"]["where"] == "runner"
    for key in ("snapshot_id", "manifest_sha256", "reads", "reads_total"):
        assert browser["receipt"]["corpus"][key] == runner["receipt"]["corpus"][key], key
    assert browser["governance"]["licences"] == runner["governance"]["licences"]
    assert browser["governance"]["limitations"] == runner["governance"]["limitations"]
    assert fake.requests and all(u.startswith(base) for u in fake.requests)


# ------------------------------------------------------------- the real table (slow)

@pytest.fixture(scope="module")
def real(tmp_path_factory):
    """The real corpus, built from the repository's table (about 30 s; no build cache)."""
    from tcmstudio.corpus.build import build_corpus, default_data_dir, default_xlsx
    if not default_xlsx().is_file():
        pytest.skip("the formula table is not in this checkout")
    site = tmp_path_factory.mktemp("corpus-real") / "site"
    data = default_data_dir()
    t0 = time.perf_counter()
    summary = build_corpus(site, data_dir=data if data.is_dir() else None, cache=False)
    return {"site": site, "summary": summary, "seconds": time.perf_counter() - t0}


@pytest.mark.slow
def test_real_table_answers(real, monkeypatch):
    monkeypatch.delenv("TCMSTUDIO_CORPUS", raising=False)
    reset_default_corpora()
    ctx = {"where": "runner", "corpus": {"dir": str(real["site"])}}
    assert real["summary"]["packs"]["formulas"]["counts"]["rows"] == 84_294
    assert real["summary"]["totals"]["largest"]["bytes"] <= 2 * 1024 * 1024
    gz = call("corpus_formula", {"name": "桂枝汤"}, ctx)
    assert gz["result"]["curated"][0]["id"] == "formula.guizhitang"
    assert gz["result"]["table_variants"]["count"] == 30
    rs = call("corpus_formula", {"name": "人参散"}, ctx)["result"]
    assert rs["status"] == "candidates" and rs["count"] == 259
    hq = call("corpus_formulas_with", {"herbs": ["黄芪"]}, ctx)["result"]
    # the research loop's figure: rows of the table with a component resolving to 黄芪
    assert hq["total"] == 5_796
    herb = call("corpus_herb", {"name": "黄芪"}, ctx)["result"]
    assert herb["formula_table"]["count"] == herb["herb"]["formula_count"] == 5_796
    safety = call("corpus_safety", {"herbs": ["甘草", "海藻"]}, ctx)["result"]
    assert any(p["rule"].startswith("十八反") for p in safety["pairs"])
    if "lotus" in real["summary"]["packs"]:
        compounds = call("corpus_compounds", {"herb": "甘草", "limit": 3}, ctx)["result"]
        assert compounds["total"] > 0 and compounds["organisms"]


@pytest.mark.slow
def test_real_table_resolves_like_the_research_loop(real):
    """Rows of the real table, read back from the corpus, carry exactly the components the
    research loop parses from the xlsx."""
    import gzip
    import json
    from bioagent.sources.formulas import load_formula_table
    from tcmstudio.corpus.build import default_xlsx
    table = load_formula_table(default_xlsx())        # the build's parse, cached by bioagent
    corpus = real["site"] / "corpus"
    manifest = json.loads((corpus / real["summary"]["manifest"]).read_text(encoding="utf-8"))
    for chunk_no in (0, 46, 168):
        meta = manifest["objects"][f"formulas/rows/{chunk_no:04d}.json"]
        rows = json.loads(gzip.decompress((corpus / meta["url"]).read_bytes()))["rows"]
        for row in rows[::37]:
            rec = table.records[row["rowno"]]
            assert row["id"] == rec.id and row["name"] == rec.name
            assert row["components"] == [[c.name or c.written, c.dose, c.processing, c.drug]
                                         for c in rec.components]
