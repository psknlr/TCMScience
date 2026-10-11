"""Complete source coverage, formula provenance and isolated browser SQLite imports."""
from __future__ import annotations

import base64
import hashlib
import io
import sqlite3
import tarfile
from types import SimpleNamespace

import pytest

from tcmstudio import webbuild
from tcmstudio.dispatch import call
from tcmstudio.repository_data import formula_search, materia_search, provider_catalog


def test_full_workbook_asset_is_lazy_verified_complete_and_deployable():
    files, manifest = webbuild.data_assets()
    asset, = manifest
    assert asset["rows"] == 84294 and asset["lazy"] is True
    assert asset["license"] == "LicenseRef-user-supplied-unstated"
    assert asset["clinical_validation"] == "not assessed"
    packed = files[asset["path"].removeprefix("runtime/")][0]
    assert hashlib.sha256(packed).hexdigest() == asset["sha256"]
    assert all(len(content) < 25 * 1024 * 1024 for content, _ in files.values())
    with tarfile.open(fileobj=io.BytesIO(packed), mode="r:gz") as archive:
        assert archive.getnames() == ["repository-formulas.sqlite"]
    result = formula_search("桂枝汤", exact=True, limit=1)
    assert result["corpus_rows"] == 84294 and result["total"] > 1
    assert result["count"] == 1 and result["next_offset"] == 1
    assert result["records"][0]["components"]
    assert result["provenance"]["source_sha256"] == asset["source_sha256"]


def test_broad_native_lookups_preserve_versions_and_identity_scope(browser_ctx):
    formula = call("tcm_formula", {"name": "葛根芩连汤"}, browser_ctx)
    assert formula["status"] == "succeeded", formula["error"]
    assert formula["result"]["repository_records"]["total"] >= 1
    assert "clinically validated" in " ".join(formula["governance"]["limitations"])
    lookup = call("tcm_lookup", {"name": "桂枝汤", "kind": "formula"}, browser_ctx)
    assert lookup["status"] == "succeeded"
    assert lookup["result"]["ambiguous"] is True and lookup["result"]["candidate_count"] > 1
    identity = call("tcm_herb", {"name": "龙胆"}, browser_ctx)
    assert identity["status"] == "succeeded"
    assert identity["result"]["annotation_status"] == "identity_only"
    assert "toxicity" not in identity["result"]["herb"]
    assert materia_search()["corpus_rows"] == 401


def test_gateway_manifest_covers_every_registered_operation():
    from bioagent.providers.public_apis import SOURCES
    manifest = webbuild.source_gateway_document()
    assert len(manifest["sources"]) == len(SOURCES) == 117
    pairs = {(s["key"], op["name"]) for s in manifest["sources"] for op in s["operations"]}
    assert pairs == {(s.key, op.name) for s in SOURCES for op in s.operations}
    assert len(pairs) == 444
    assert next(s for s in manifest["sources"] if s["key"] == "herb_api")["transport"] == "http"
    assert all(s["rate_rps"] > 0 for s in manifest["sources"])


def test_full_third_party_catalog_reports_reviewed_wrappers_explicitly():
    result = provider_catalog(kind="database", limit=1)
    assert result["corpus_rows"] == 2567 and result["total"] == 286
    assert result["count"] == 1 and result["next_offset"] == 1
    assert result["reviewed_wrappers"]["tooluniverse"]
    assert result["reviewed_wrappers"]["biomcp"]
    assert all(t["studio_entry"] == "system.provider_call"
               for t in result["reviewed_wrappers"]["tooluniverse"])


def _store_bytes(path):
    from bioagent.tcmdb.rowkit import COLUMNS
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE _tcmdb_files (tbl TEXT, rows INTEGER, license TEXT, sha256 TEXT)")
        conn.execute("INSERT INTO _tcmdb_files VALUES ('compounds', 2, 'CC BY 4.0', 'test')")
        conn.execute("CREATE TABLE compounds (name TEXT, target TEXT)")
        conn.executemany("INSERT INTO compounds VALUES (?,?)", [("alpha", "A"), ("beta", "B")])
        conn.execute("CREATE TABLE relations (" + ",".join(f'"{name}" TEXT' for name in COLUMNS) + ")")
    return base64.b64encode(path.read_bytes()).decode()


def test_browser_import_query_and_project_isolation(tmp_path, browser_ctx):
    encoded = _store_bytes(tmp_path / "incoming.sqlite")
    imported = call("tcmdb.import_store", {"dataset": "ddid", "content_base64": encoded}, browser_ctx)
    assert imported["status"] == "succeeded", imported["error"]
    query = call("tcmdb.query", {"dataset": "ddid", "table": "compounds", "limit": 1,
                                 "offset": 1}, browser_ctx)
    assert query["status"] == "succeeded" and query["result"]["rows"] == [{"name": "beta", "target": "B"}]
    separate = call("tcmdb.tables", {"dataset": "ddid"}, {**browser_ctx, "project_id": "another"})
    assert separate["status"] == "failed" and separate["error"]["type"] == "unavailable"
    broken = call("tcmdb.import_store", {"dataset": "ddid", "content_base64": "bad"}, browser_ctx)
    assert broken["status"] == "failed" and broken["error"]["type"] == "bad_arguments"
    assert call("tcmdb.query", {"dataset": "ddid", "table": "compounds"}, browser_ctx)["result"]["count"] == 2


def test_reviewed_provider_call_routes_through_policy_runtime(monkeypatch, runner_ctx):
    from bioagent.providers.tooluniverse import load_allowlist
    import bioagent.psh.assembly as assembly
    tool = load_allowlist().tools[0]
    seen = []
    def invoke(component, spec, **arguments):
        seen.append((component, spec.permission_profile, arguments))
        return SimpleNamespace(status=SimpleNamespace(value="SUCCEEDED"), value={"record": "ok"},
                               metadata={}, adapter="python", reason="", error="")
    monkeypatch.setattr(assembly, "default_runtime", lambda **kwargs: SimpleNamespace(invoke=invoke))
    result = call("system.provider_call", {"provider": "tooluniverse", "tool": tool.name,
                                           "arguments": {"query": "test"}}, {**runner_ctx, "network": True})
    assert result["status"] == "succeeded", result["error"]
    assert seen == [(tool.component_id, "biomedical-research", {"query": "test"})]
    assert result["receipt"]["component_id"] == tool.component_id
    unknown = call("system.provider_call", {"provider": "tooluniverse", "tool": "arbitrary.shell"},
                   {**runner_ctx, "network": True})
    assert unknown["status"] == "failed" and len(seen) == 1
    offline = call("system.provider_call", {"provider": "tooluniverse", "tool": tool.name}, runner_ctx)
    assert offline["status"] == "failed" and offline["error"]["type"] == "network_off"


def test_provider_wrappers_are_explicitly_unavailable_without_configuration(runner_ctx, browser_ctx):
    from bioagent.providers.biomcp import load_server_config
    tool = next(iter(load_server_config().tools))
    args = {"provider": "biomcp", "tool": tool}
    runner = call("system.provider_call", args, {**runner_ctx, "network": True})
    assert runner["status"] == "failed" and "draft" in runner["error"]["message"]
    browser = call("system.provider_call", args, {**browser_ctx, "network": True})
    assert browser["status"] == "failed" and browser["error"]["type"] == "unavailable"


# ------------------------------------------------------------------- ranked search

def _rows_containing(*needles: str) -> set[int]:
    """Rows with any needle in any of the seven fields, counted independently of the search."""
    from tcmstudio.repository_data import FORMULA_COLUMNS, formula_store
    conn = sqlite3.connect(formula_store())
    try:
        found = set()
        for row in conn.execute(f"SELECT row, {', '.join(FORMULA_COLUMNS)} FROM formulas"):
            if any(n in (cell or "") for cell in row[1:] for n in needles):
                found.add(row[0])
        return found
    finally:
        conn.close()


def test_a_herb_is_found_under_the_name_classical_sources_write():
    from tcmstudio.repository_data import ranked_formula_search, search_variants
    assert search_variants("黄芪")["search"] == ["黄芪", "黄耆"]
    literal = formula_search("黄芪")["total"]
    ranked = ranked_formula_search("黄芪", limit=5)
    assert ranked["total"] == len(_rows_containing("黄芪", "黄耆"))
    assert ranked["total"] > 100 * literal, "黄耆 is how most of the workbook writes it"
    term, = ranked["rank"]["terms"]
    assert term["materia"] == "huangqi" and term["rows"] == ranked["total"]
    plain = ranked_formula_search("黄芪", aliases=False)
    assert plain["total"] == literal and plain["rank"]["terms"][0]["search"] == ["黄芪"]
    assert formula_search("黄芪", limit=1)["also_written"]["names"] == ["黄耆"]
    assert "also_written" not in formula_search("桂枝汤", limit=1)


def test_several_terms_rank_rows_with_more_of_them_first():
    from tcmstudio.repository_data import ranked_formula_search, search_variants
    result = ranked_formula_search("黄芪、当归", limit=50)
    variants = [*search_variants("黄芪")["search"], *search_variants("当归")["search"]]
    assert result["total"] == len(_rows_containing(*variants))
    scores = [r["match"]["score"] for r in result["records"]]
    assert scores == sorted(scores, reverse=True)
    top = result["records"][0]
    assert set(top["match"]["fields"]) == {"黄芪", "当归"}
    assert all(set(r["match"]["fields"]) <= {"黄芪", "当归"} and r["match"]["fields"] for r in result["records"])


def test_an_exact_name_comes_first_and_ties_keep_workbook_order():
    from tcmstudio.repository_data import ranked_formula_search
    result = ranked_formula_search("桂枝汤", limit=100)
    fields = [r["match"]["fields"]["桂枝汤"] for r in result["records"]]
    exact = fields.count("exact_name")
    assert exact >= 2 and fields[:exact] == ["exact_name"] * exact
    assert all(r["name"] == "桂枝汤" or r["written_name"] == "桂枝汤" or "桂枝汤" in r["name"]
               for r in result["records"][:exact])
    rows = [r["row"] for r in result["records"][:exact]]
    assert rows == sorted(rows), "equal scores keep the workbook's order"


def test_ranked_pages_are_one_stable_order():
    from tcmstudio.repository_data import ranked_formula_search
    whole = [r["row"] for r in ranked_formula_search("补气 黄芪", limit=60)["records"]]
    paged = [r["row"] for offset in (0, 20, 40)
             for r in ranked_formula_search("补气 黄芪", limit=20, offset=offset)["records"]]
    assert paged == whole
    assert [r["row"] for r in ranked_formula_search("补气 黄芪", limit=60)["records"]] == whole


def test_a_source_filter_narrows_the_ranked_rows():
    from tcmstudio.repository_data import ranked_formula_search
    result = ranked_formula_search("桂枝", source="伤寒论", limit=100)
    assert result["total"] > 0 and result["rank"]["rows_searched"] < result["corpus_rows"]
    assert all("伤寒论" in r["source"] for r in result["records"])


@pytest.mark.parametrize("term, absent", [
    ("当归", "归"), ("生姜", "姜"), ("生姜", "姜汁"), ("生姜", "煨姜")])
def test_names_that_would_find_other_drugs_or_preparations_are_not_searched(term, absent):
    from tcmstudio.repository_data import search_variants
    assert absent not in search_variants(term)["search"]


def test_a_processed_form_is_searched_as_written():
    from tcmstudio.repository_data import search_variants
    assert search_variants("炙甘草") == {"term": "炙甘草", "search": ["炙甘草"], "materia": "gancao",
                                        "processed_form_of": "甘草"}
    assert search_variants("黃耆")["search"][:1] == ["黃耆"] and "黄耆" in search_variants("黃耆")["search"]
    assert search_variants("补气") == {"term": "补气", "search": ["补气"]}


def test_ranked_search_through_the_dispatcher(browser_ctx):
    ok = call("call_tool", {"tool": "tcmdb.formulas", "arguments": {"query": "黄芪 当归", "rank": True,
                                                                     "limit": 2}}, browser_ctx)
    assert ok["status"] == "succeeded", ok["error"]
    assert ok["result"]["count"] == 2 and "rank" in ok["result"]
    assert any("lexical relevance" in x for x in ok["governance"]["limitations"])
    assert ok["governance"]["licences"], "the workbook's unstated licence travels with ranked results too"
    for args, why in (({"query": "桂枝汤", "rank": True, "exact": True}, "rank and exact"),
                      ({"query": "  ", "rank": True}, "at least one term"),
                      ({"query": " ".join(f"t{i}" for i in range(13)), "rank": True}, "at most 12")):
        bad = call("call_tool", {"tool": "tcmdb.formulas", "arguments": args}, browser_ctx)
        assert bad["status"] == "failed" and bad["error"]["type"] == "bad_arguments", args
        assert why in bad["error"]["message"]


def test_the_name_helpers_read_the_materia_table_as_it_is():
    from tcmstudio.repository_data import _drug_named, _names_by_drug, _processed_base
    assert _drug_named("黄芪") == _drug_named("黄耆") == _drug_named("黃耆") == "huangqi"
    assert _drug_named("炙甘草") == "gancao" and _drug_named("生姜") == "shengjiang"
    assert _drug_named("酒炒黄芪") is None and _drug_named("桂枝汤") is None and _drug_named("") is None
    names = _names_by_drug()["danggui"]
    assert names[0] == "当归" and "川当归" in names and len(names) == len(set(names))
    for name, drug, base in (("炙甘草", "gancao", "甘草"), ("甘草末", "gancao", "甘草"), ("粉草", "gancao", None),
                             ("甘草", "gancao", None), ("生姜", "shengjiang", None), ("姜汁", "shengjiang", "姜")):
        assert _processed_base(name, drug) == base, name


def test_the_pinned_skills_still_run_the_code_that_was_reviewed():
    """Code a pinned skill imports cannot change without a review and a new pin: its governed
    runs are refused otherwise. Do not edit the pin to make this pass."""
    import yaml
    from pathlib import Path
    from bioagent.skills.loader import skill_content_hash
    harness = Path(__file__).resolve().parents[3] / "BioScience-Harness"
    pins = {e["skill_id"]: e["content_hash"]
            for e in yaml.safe_load((harness / "registry" / "skills.lock.yaml").read_text(encoding="utf-8"))["skills"]}
    checked = 0
    for manifest in harness.glob("skills/**/skill.yaml"):
        spec = yaml.safe_load(manifest.read_text(encoding="utf-8"))
        sid = spec.get("id") or manifest.parent.name
        if sid in pins:
            entry = (spec.get("runtime") or {}).get("entrypoint", "")
            assert skill_content_hash(manifest.parent, entrypoint=entry) == pins[sid], sid
            checked += 1
    assert checked == len(pins) >= 4

