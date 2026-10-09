"""Complete source coverage, formula provenance and isolated browser SQLite imports."""
from __future__ import annotations

import base64
import hashlib
import io
import sqlite3
import tarfile
from types import SimpleNamespace

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
