"""The corpus build (docs/V2.md §11.1–§11.3): object format, manifest, determinism, the
publication gate, the owner decision, and nothing invented in the merged core pack."""

from __future__ import annotations

import gzip
import hashlib
import json
import re
from pathlib import Path

import pytest

from tcmstudio.corpus import packs as P
from tcmstudio.corpus.build import (MAX_OBJECT_BYTES, BuildError, build_corpus, build_site_corpus,
                                    core_payload, encode, runtime_boot_entry, type_aliases)
from test_corpus_support import TABLE_ROWS, tiny_corpus, write_data, write_table

pytest.importorskip("openpyxl")


@pytest.fixture(scope="module")
def tiny(tmp_path_factory):
    return tiny_corpus(tmp_path_factory)


@pytest.fixture(scope="module")
def manifest(tiny):
    corpus = tiny["site"] / "corpus"
    return json.loads((corpus / tiny["summary"]["manifest"]).read_text(encoding="utf-8"))


def _obj(tiny, manifest, path):
    meta = manifest["objects"][path]
    return json.loads(gzip.decompress((tiny["site"] / "corpus" / meta["url"]).read_bytes()))


# ------------------------------------------------------------------------ format

def test_objects_are_content_addressed_gzip_of_compact_json(tiny, manifest):
    corpus = tiny["site"] / "corpus"
    assert manifest["objects"], "no objects"
    for path, meta in manifest["objects"].items():
        data = (corpus / meta["url"]).read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        assert meta["sha256"] == digest, path
        assert meta["url"] == f"o/{digest[:32]}.gz"
        assert meta["bytes"] == len(data) <= MAX_OBJECT_BYTES
        # gzip header: level-9 deflate, no file name, mtime 0
        assert data[:3] == b"\x1f\x8b\x08" and data[3] & 0x08 == 0 and data[4:8] == b"\0\0\0\0"
        raw = gzip.decompress(data)
        assert meta["raw_bytes"] == len(raw)
        assert encode(json.loads(raw)) == raw, f"{path} is not compact, key-sorted UTF-8 JSON"
        assert meta["pack"] in P.PACKS
    # no stray object file
    assert {p.name for p in (corpus / "o").iterdir()} == \
        {m["url"][2:] for m in manifest["objects"].values()}


def test_manifest_layout_and_snapshot_id(tiny, manifest):
    corpus = tiny["site"] / "corpus"
    name = tiny["summary"]["manifest"]
    data = (corpus / name).read_bytes()
    assert name == f"manifest.{hashlib.sha256(data).hexdigest()[:12]}.json"
    latest = json.loads((corpus / "latest.json").read_text(encoding="utf-8"))
    assert latest == {"schema": "tcmstudio.corpus/1", "snapshot_id": manifest["snapshot_id"],
                      "manifest": name, "sha256": hashlib.sha256(data).hexdigest()}
    lines = "".join(f"{p} {m['sha256']}\n" for p, m in sorted(manifest["objects"].items()))
    sha12 = hashlib.sha256(lines.encode("utf-8")).hexdigest()[:12]
    assert manifest["snapshot_id"] == f"tcmcorpus-{P.DATA_DATE.replace('-', '.')}-{sha12}"
    assert re.fullmatch(r"tcmcorpus-\d{4}\.\d{2}\.\d{2}-[0-9a-f]{12}", manifest["snapshot_id"])
    assert manifest["schema"] == P.SCHEMA and manifest["data_date"] == P.DATA_DATE
    assert set(manifest["packs"]) == {"core", "formulas", "lotus", "pathways"}
    assert manifest["skipped"] == {}
    t = manifest["totals"]
    assert t["objects"] == len(manifest["objects"])
    assert t["bytes"] == sum({m["url"]: m["bytes"] for m in manifest["objects"].values()}.values())
    assert t["largest"]["bytes"] == max(m["bytes"] for m in manifest["objects"].values())
    assert (corpus / "ATTRIBUTION.md").is_file()
    assert (tiny["site"] / "attribution.html").is_file()


def test_every_pack_carries_its_publication_fields(manifest):
    for name, meta in manifest["packs"].items():
        assert set(meta["title"]) == {"zh", "en"} and all(meta["title"].values()), name
        assert meta["licence"] and meta["attribution"]["zh"] and meta["attribution"]["en"]
        assert "evidence_ceiling" in meta
        assert meta["limitations"] and all(x["zh"] and x["en"] for x in meta["limitations"])
        assert meta["sources"] and all(s.get("name") and s.get("licence") for s in meta["sources"])
        assert meta["publication"]
        assert meta["objects"] == sum(1 for m in manifest["objects"].values() if m["pack"] == name)
    assert manifest["packs"]["core"]["publication"] == {"basis": "open-licence"}
    assert manifest["packs"]["lotus"]["licence"] == "CC-BY-4.0"
    assert manifest["packs"]["pathways"]["licence"] == "CC0-1.0"
    assert manifest["packs"]["formulas"]["evidence_ceiling"] == "EXPERT_EXPERIENCE"


def test_the_owner_decision_is_recorded_verbatim(tiny, manifest):
    decision = {"decided_by": "IMPF-AI（science.impf.ai 站点所有者）", "date": "2026-10-08",
                "basis": "所有者决定在 science.impf.ai 公开发布；原表未声明来源与许可",
                "licence": "LicenseRef-owner-published-unstated", "commercial": "unknown"}
    assert manifest["packs"]["formulas"]["publication"] == decision
    assert manifest["packs"]["formulas"]["licence"] == decision["licence"]
    page = (tiny["site"] / "attribution.html").read_text(encoding="utf-8")
    md = (tiny["site"] / "corpus" / "ATTRIBUTION.md").read_text(encoding="utf-8")
    for value in decision.values():
        assert value in page and value in md


def test_attribution_page_lists_packs_sources_and_the_denylist(tiny, manifest):
    page = (tiny["site"] / "attribution.html").read_text(encoding="utf-8")
    assert page.startswith("<!doctype html>") and '<html lang="zh-CN">' in page
    assert "<script" not in page
    assert "prefers-color-scheme:dark" in page
    assert page.index("语料来源与许可") < page.index("Corpus sources and licences")
    for meta in manifest["packs"].values():
        assert meta["title"]["zh"] in page and meta["title"]["en"] in page
        for s in meta["sources"]:
            assert json.dumps(s["name"], ensure_ascii=False)[1:-1].replace("&", "&amp;") in page
        for lim in meta["limitations"]:
            assert lim["zh"] in page
    for card in P.NEVER_PUBLISHED.values():
        assert card["name"] in page
    assert P.NEVER_PUBLISHED_REASON["zh"] in page and "tcmdb fetch" in page
    assert "doi:10.7554/eLife.70780" in page


def test_sizes_are_recorded_and_bounded(tiny):
    s = tiny["summary"]
    assert s["totals"]["objects"] > 0 and s["totals"]["bytes"] > 0
    assert s["totals"]["largest"]["bytes"] <= MAX_OBJECT_BYTES
    for pack in s["packs"].values():
        assert pack["largest"]["bytes"] <= MAX_OBJECT_BYTES


# -------------------------------------------------------------------- determinism

def test_building_twice_gives_identical_bytes(tiny, tmp_path):
    again = build_corpus(tmp_path / "again", xlsx=tiny["xlsx"], data_dir=tiny["data"], cache=False)
    assert again["manifest"] == tiny["summary"]["manifest"]
    assert again["snapshot_id"] == tiny["summary"]["snapshot_id"]
    first = {p.relative_to(tiny["site"]): p.read_bytes() for p in tiny["site"].rglob("*") if p.is_file()}
    second = {p.relative_to(tmp_path / "again"): p.read_bytes()
              for p in (tmp_path / "again").rglob("*") if p.is_file()}
    assert first.keys() == second.keys()
    assert all(first[k] == second[k] for k in first), "a rebuild changed some bytes"


def test_the_build_cache_gives_the_same_objects(tiny, tmp_path, monkeypatch):
    monkeypatch.setenv("TCMSTUDIO_CACHE", str(tmp_path / "cache"))
    logs1: list[str] = []
    logs2: list[str] = []
    one = build_corpus(tmp_path / "one", xlsx=tiny["xlsx"], data_dir=tiny["data"], log=logs1.append)
    two = build_corpus(tmp_path / "two", xlsx=tiny["xlsx"], data_dir=tiny["data"], log=logs2.append)
    assert not any("build cache" in m for m in logs1)
    assert any("from the build cache" in m for m in logs2)
    assert one["manifest"] == two["manifest"] == tiny["summary"]["manifest"]
    # a damaged cache is noticed and rebuilt, never used
    cached = next((tmp_path / "cache" / "corpus-build").glob("formulas-*/o/*.gz"))
    cached.write_bytes(b"garbage")
    logs3: list[str] = []
    three = build_corpus(tmp_path / "three", xlsx=tiny["xlsx"], data_dir=tiny["data"],
                         log=logs3.append)
    assert any("unusable" in m for m in logs3)
    assert three["manifest"] == tiny["summary"]["manifest"]


# --------------------------------------------------------------------------- gate

def test_allowlist_and_denylist_never_overlap():
    allowed = {P.source_key(s) for spec in P.PACKS.values() for s in spec["sources"]}
    for key in allowed:
        assert P.denied(key) is None, key
    for key, card in P.NEVER_PUBLISHED.items():
        assert P.denied(key) == key and P.denied(card["name"]) == key
        for alias in card.get("aliases", ()):
            assert P.denied(alias) == key
    assert P.denied({"name": "HERB 2.0"}) == "herb" and P.denied("NPASS 2.0") == "npass"
    assert P.denied("LOTUS") is None and P.denied("Reactome") is None


@pytest.mark.parametrize("source, words", [
    ({"key": "npass", "name": "NPASS 2.0", "licence": "Not stated"}, "never published"),
    ({"name": "HERB 2.0", "licence": "CC-BY-4.0"}, "never published"),
    ({"key": "lotus", "name": "LOTUS", "licence": "not stated"}, "only an open licence"),
    ({"key": "lotus", "name": "LOTUS", "licence": "CC-BY-NC-4.0"}, "only an open licence"),
    ({"key": "coconut", "name": "COCONUT", "licence": "CC0-1.0"}, "not allowlisted"),
])
def test_a_source_outside_the_allowlist_stops_the_build(tiny, tmp_path, source, words):
    data = write_data(tmp_path / "data", lotus_source=source)
    with pytest.raises(P.GateError, match=words):
        build_corpus(tmp_path / "site", xlsx=tiny["xlsx"], data_dir=data, cache=False)
    assert not (tmp_path / "site" / "corpus").exists(), "nothing is written when the gate refuses"


def test_the_formula_table_needs_its_owner_decision(monkeypatch):
    P.check_sources("formulas", [{"key": "formula-table", "name": "中医方剂数据表",
                                  "licence": P.OWNER_DECISION_FORMULAS["licence"]}])
    spec = dict(P.PACKS["formulas"])
    monkeypatch.setitem(P.PACKS, "formulas", {**spec, "publication": {"basis": "open-licence"}})
    with pytest.raises(P.GateError, match="only an open licence or a recorded owner decision"):
        P.check_sources("formulas", [{"key": "formula-table", "name": "中医方剂数据表",
                                      "licence": P.OWNER_DECISION_FORMULAS["licence"]}])
    monkeypatch.setitem(P.PACKS, "formulas", {**spec, "publication": {
        "basis": "x", "decided_by": "", "date": "2026-10-08", "licence": "y"}})
    with pytest.raises(P.GateError, match="lacks decided_by"):
        P.check_sources("formulas", [{"key": "formula-table", "name": "t", "licence": "y"}])


def test_absent_extracts_are_skipped_and_said_so(tiny, tmp_path):
    logs: list[str] = []
    summary = build_corpus(tmp_path / "site", xlsx=tiny["xlsx"], data_dir=tmp_path / "nothing",
                           cache=False, log=logs.append)
    m = json.loads((tmp_path / "site" / "corpus" / summary["manifest"]).read_text(encoding="utf-8"))
    assert set(m["packs"]) == {"core", "formulas"}
    assert set(m["skipped"]) == {"lotus", "pathways"}
    assert any("lotus pack skipped" in x for x in logs)
    assert "本快照未包含" in (tmp_path / "site" / "attribution.html").read_text(encoding="utf-8")
    core = json.loads(gzip.decompress(
        (tmp_path / "site" / "corpus" / m["objects"]["core/core.json"]["url"]).read_bytes()))
    assert all(h["compound_count"] is None for h in core["herbs"]), "no LOTUS pack, no count"


def test_build_site_corpus_returns_the_boot_entry(tiny, tmp_path, monkeypatch):
    import tcmstudio.corpus.build as B
    monkeypatch.setattr(B, "default_xlsx", lambda: tiny["xlsx"])
    monkeypatch.setenv("TCMSTUDIO_CORPUS_DATA", str(tiny["data"]))
    monkeypatch.setenv("TCMSTUDIO_CACHE", str(tmp_path / "cache"))      # not the user's cache
    entry = build_site_corpus(tmp_path / "staging")
    assert entry == {"schema": "tcmstudio.corpus/1", "snapshot_id": tiny["summary"]["snapshot_id"],
                     "manifest": f"corpus/{tiny['summary']['manifest']}",
                     "sha256": tiny["summary"]["sha256"], "base": "corpus/"}
    assert (tmp_path / "staging" / entry["manifest"]).is_file()
    assert (tmp_path / "staging" / "attribution.html").is_file()
    assert runtime_boot_entry() == {"schema": "tcmstudio.corpus/1",
                                    "latest_url": "https://science.impf.ai/corpus/latest.json"}


# ----------------------------------------------------------------- the core pack

@pytest.fixture(scope="module")
def core(tiny, manifest):
    return _obj(tiny, manifest, "core/core.json")


def test_core_merges_seed_materia_and_clinic(core):
    herbs = {h["id"]: h for h in core["herbs"]}
    assert len(herbs) == 401
    assert "herb.shudihuang" in herbs and "herb.shudi" not in herbs
    assert herbs["herb.shudihuang"]["materia_id"] == "shudi"
    assert sum(1 for h in core["herbs"] if h["layers"].get("nature") == "seed") == 23
    assert len(core["syndromes"]) == 27
    assert sum(1 for s in core["syndromes"] if s["layer"] == "seed") == 8
    assert len(core["formulas"]) == 19
    assert sum(1 for f in core["formulas"] if f["roles_recorded"]) == 6
    assert len(core["passages"]) == 9
    seed_safety = [r for r in core["safety"] if r["layer"] == "seed"]
    assert len(seed_safety) == 14


def test_nothing_is_invented(core):
    herbs = {h["id"]: h for h in core["herbs"]}
    gegen = herbs["herb.gegen"]                      # outside the seed, no clinic toxicity
    assert gegen["toxicity"] == "" and gegen["actions"] == [] and gegen["nature"] == ""
    assert gegen["flavours"] == [] and gegen["meridians"] == [] and gegen["first_recorded"] == ""
    assert "toxicity" not in gegen["layers"]
    xixin = herbs["herb.xixin"]                      # the clinic pack states it
    assert xixin["toxicity"] == "有小毒" and xixin["layers"]["toxicity"] == "clinic"
    stated = {h["id"] for h in core["herbs"] if h["toxicity"]}
    assert all(herbs[i]["layers"]["toxicity"] in ("seed", "clinic") for i in stated)
    for r in core["safety"]:
        if r["layer"] == "clinic":
            assert r["severity"] == "unstated", r["id"]
    for f in core["formulas"]:
        if f["layer"] == "clinic":
            assert f["dosage_form"] == "" and not f["roles_recorded"]
            assert all(i["role"] == "" for i in f["ingredients"])


def test_typed_aliases_keep_processed_forms_apart(core):
    herbs = {h["id"]: h for h in core["herbs"]}
    kinds = {a["name"]: a["kind"] for a in herbs["herb.gancao"]["aliases"]}
    assert kinds["炙甘草"] == "processed" and kinds["生甘草"] == "processed"
    assert kinds["甘草梢"] == "part" and kinds["甘草末"] == "processed"
    assert kinds["国老"] == "synonym" and kinds["炙草"] == "processed"
    banxia = {a["name"]: a for a in herbs["herb.banxia"]["aliases"]}
    assert banxia["法半夏"]["kind"] == "processed"
    assert banxia["法半夏"]["processed_id"] == "processed.fa_banxia"
    assert banxia["半夏曲"]["kind"] == "product"
    assert {a["name"]: a["kind"] for a in herbs["herb.fuling"]["aliases"]}["茯神"] == "part"
    names = core["names"]
    for processed in ("炙甘草", "法半夏", "半夏曲", "甘草梢", "生地黄", "炙黄芪"):
        assert processed not in names, f"{processed} must not resolve to a crude drug"
    assert names["黄耆"] == [["herb", "herb.huangqi"]]
    assert names["国老"] == [["herb", "herb.gancao"]]
    assert names["熟地"] == [["herb", "herb.shudihuang"]]
    # the clinic pack's data for a processed form stays on that form
    assert banxia["法半夏"]["clinic"]["toxicity"] == "有毒"
    assert herbs["herb.banxia"]["dose"] is None


def test_type_aliases_rules():
    out = {a["name"]: a for a in type_aliases("甘草", "gancao",
                                              ["炙甘草", "粉草", "甘草梢", "甘草末", "国老"])}
    assert [out[n]["kind"] for n in ("炙甘草", "粉草", "甘草梢", "甘草末", "国老")] == \
        ["processed", "synonym", "part", "processed", "synonym"]
    jiu = {a["name"]: a["kind"] for a in type_aliases("韭菜子", "jiuzi", ["韭子", "韭根", "韭白"])}
    assert jiu == {"韭子": "synonym", "韭根": "part", "韭白": "part"}


def test_the_formula_table_never_enters_the_core_name_index(core, tiny):
    names = core["names"]
    assert names["桂枝汤"] == sorted([["formula", "formula.guizhitang"],
                                     ["formula", next(f["id"] for f in core["formulas"]
                                                      if f["chinese"] == "桂枝汤" and f["layer"] == "clinic")]])
    assert "人参散" not in names and "黄芪汤" not in names and "怪方" not in names
    table_names = {r[0] for r in TABLE_ROWS if r[0]}
    curated = {f["chinese"] for f in core["formulas"]}
    assert all(n in curated for n in table_names if n in names)


def test_unresolved_clinic_names_are_reported_not_dropped(core, manifest, tiny):
    expected = {"红大戟", "平贝母", "伊贝母", "湖北贝母", "人参叶", "西洋参", "南沙参", "狼毒", "首乌藤"}
    assert {u["name"] for u in core["unresolved"]} == expected
    assert {u["name"] for u in manifest["packs"]["core"]["unresolved"]} == expected
    assert any("西洋参" in line for line in tiny["logs"])
    # a record naming one is kept, by name
    kept = [r for r in core["safety"] if (r.get("counterpart") or {}).get("name") == "西洋参"]
    assert kept and kept[0]["counterpart"]["id"] is None and kept[0]["subject"]["id"] == "herb.lilu"


def test_clinic_incompatibilities_and_seed_conflicts_are_both_kept(core):
    gancao_haizao = [r for r in core["safety"] if r["kind"] == "incompatibility"
                     and r["subject"]["id"] == "herb.gancao"
                     and r["counterpart"]["id"] == "herb.haizao"]
    assert len(gancao_haizao) == 1 and gancao_haizao[0]["rule"].startswith("十八反")
    assert set(gancao_haizao[0]["subject"]["written"]) == {"甘草", "炙甘草"}
    fuzi = [r for r in core["safety"] if r["subject"]["id"] == "herb.fuzi" and r["population"] == "孕妇"]
    assert {(r["layer"], r["rule"]) for r in fuzi} == {("seed", "孕妇禁用"), ("clinic", "孕妇慎用")}


def test_core_payload_is_deterministic():
    assert encode(core_payload()[0]) == encode(core_payload()[0])


# ------------------------------------------------------------------ the formula pack

def test_formula_rows_resolve_as_the_research_loop_resolves(tiny, manifest):
    from bioagent.sources.formulas import load_formula_table
    table = load_formula_table(tiny["xlsx"])
    index = _obj(tiny, manifest, "formulas/index.json")
    chunk = _obj(tiny, manifest, "formulas/rows/0000.json")
    assert chunk["start"] == 0 and len(chunk["rows"]) == len(TABLE_ROWS)
    assert len(index["names"]) == len(index["source_ids"]) == len(TABLE_ROWS)
    by_row = {rec.row - 2: rec for rec in table.records}
    for row in chunk["rows"]:
        rec = by_row.get(row["rowno"])
        if rec is None:
            assert row.get("empty") is True and index["names"][row["rowno"]] == ""
            continue
        assert row["id"] == rec.id and row["name"] == rec.name == index["names"][row["rowno"]]
        assert index["sources"][index["source_ids"][row["rowno"]]] == rec.source == row["source"]
        assert row["components"] == [[c.name or c.written, c.dose, c.processing, c.drug]
                                     for c in rec.components]
        assert row["indications"] == rec.actions
    assert chunk["rows"][7]["written_name"] == "补中益气汤（《脾胃论》卷中。）"
    stats = _obj(tiny, manifest, "formulas/stats.json")
    expected = table.stats()
    for key in ("resolved_components", "components", "drugs_used"):
        assert stats[key] == expected[key]
    assert stats["rows"] == expected["formulas"] and stats["resolved_rows"] == expected["resolved_formulas"]
    assert stats["sheet_rows"] == len(TABLE_ROWS) and stats["empty_rows"] == 1


def test_by_herb_postings_and_id_shards(tiny, manifest):
    from bioagent.sources.formulas import load_formula_table
    table = load_formula_table(tiny["xlsx"])
    for drug in ("huangqi", "renshen", "gancao"):
        post = _obj(tiny, manifest, f"formulas/by-herb/{drug}.json")
        rows, total = [], 0
        for d in post["rows_delta"]:
            total += d
            rows.append(total)
        expected = sorted(rec.row - 2 for rec in table.records
                          if any(c.drug == drug for c in rec.components))
        assert rows == expected and post["count"] == len(expected)
    dup = table.records[6].id
    shard = _obj(tiny, manifest, f"formulas/ids/{dup.rsplit('.fx', 1)[-1][0]}.json")
    assert shard[dup] == [6, 10]


def test_an_object_over_the_size_limit_stops_the_build(tiny, tmp_path, monkeypatch):
    import tcmstudio.corpus.build as B
    monkeypatch.setattr(B, "MAX_OBJECT_BYTES", 1000)
    with pytest.raises(BuildError, match="over the"):
        build_corpus(tmp_path / "site", xlsx=tiny["xlsx"], data_dir=tiny["data"], cache=False)
