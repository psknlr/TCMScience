"""The runner's HTTP API (CONTRACTS §4) against a real server on an ephemeral port."""

from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path
from urllib.parse import quote

import pytest

from tcmstudio.server import pairing_link

from test_server_support import running_server


@pytest.fixture
def server(tmp_path):
    with running_server(tmp_path / "home") as pair:
        yield pair


# ---------------------------------------------------------------------------- read-only

def test_info_describes_this_runner(server):
    srv, c = server
    status, info = c.get("/api/info")
    assert status == 200
    assert info["name"] == "tcmstudio"
    assert set(info["versions"]) >= {"tcmstudio", "bioagent", "psh", "python"}
    assert info["home"] == str(srv.home) and Path(info["home"]).is_absolute()
    assert info["cpu"]["cores"] >= 1
    assert info["devices"][0]["id"] == "cpu" and info["devices"][0]["available"] is True
    assert info["settings"] == srv.state.get()
    assert info["network"] == {"enabled": False, "profile": "biomedical-research"}
    assert info["purpose"] == "academic"
    assert info["counts"]["core"] == 32 and info["counts"]["entries"] >= 600
    assert info["token_required"] is True
    assert any(e["id"] == "omics-builtin" and e["installed"] for e in info["engines"])
    missing = [e for e in info["engines"] if not e["installed"]]
    assert all("missing" in e for e in missing)
    assert info["paths"]["data_lake"].startswith(str(srv.home))
    assert info["jobs"]["available"] is True


def test_devices_and_selection(server):
    _, c = server
    status, doc = c.get("/api/devices")
    assert status == 200
    assert doc["selected"] == "auto"
    assert doc["resolved"] in {d["id"] for d in doc["devices"]}
    assert {"boltz", "chai", "openmm"} <= set(doc["engine_options"])
    assert "memory_available_gb" in doc["usage"]
    for d in doc["devices"]:
        assert {"id", "kind", "name", "available"} <= set(d)


def test_catalog_reflects_this_runner(server):
    srv, c = server
    status, h, raw = c.raw("GET", "/api/catalog", headers={"Accept-Encoding": "gzip"})
    assert status == 200 and h["content-encoding"] == "gzip"
    doc = json.loads(gzip.decompress(raw))
    assert doc["schema"] == "tcmstudio.catalog/1" and doc["where"] == "runner"
    assert len(doc["core"]) == 32
    jobs = {e["id"]: e for e in doc["entries"] if e["kind"] == "job"}
    # the research loop needs snapshots this fresh home does not have
    research = jobs["job.research.run"]
    assert research["available"] is False
    assert any("snapshots" in m for m in research["missing"])
    plain = c.get("/api/catalog")[1]
    assert plain["counts"] == doc["counts"]


def test_kinds_carry_schemas_and_availability(server):
    _, c = server
    status, kinds = c.get("/api/kinds")
    assert status == 200
    by = {k["kind"]: k for k in kinds}
    assert set(by) == {"pipeline.rnaseq", "pipeline.scrna", "pipeline.fold", "pipeline.dock",
                       "pipeline.admet", "research.run", "skill.run", "tcmdb.fetch",
                       "tcmdb.build"}
    for k in kinds:
        assert k["parameters"]["type"] == "object"
        assert k["duration"] in ("seconds", "minutes", "hours")
        assert isinstance(k["missing"], list) and k["available"] is (not k["missing"])
        assert k["title"]["zh"] and k["title"]["en"]
    assert by["pipeline.rnaseq"]["parameters"]["required"] == ["samples"]
    assert by["tcmdb.fetch"]["network"] is True


# -------------------------------------------------------------------------- settings

def test_settings_round_trip_and_validation(server):
    srv, c = server
    status, s = c.get("/api/settings")
    assert status == 200 and set(s) == {"device", "threads", "max_jobs", "network", "purpose",
                                        "allow_remote"}
    status, s2 = c.json("PUT", "/api/settings", {**s, "threads": 2, "max_jobs": 3,
                                                 "network": {"enabled": True,
                                                             "profile": "biomedical-research"},
                                                 "unknown_key": 1})
    assert status == 200 and s2["threads"] == 2 and s2["max_jobs"] == 3
    assert s2["network"]["enabled"] is True and "unknown_key" not in s2
    saved = json.loads((srv.home / "runner.json").read_text())["settings"]
    assert saved == s2
    for bad in ({"threads": 0}, {"max_jobs": "two"}, {"device": "cuda:x"},
                {"network": {"profile": "everything"}}, {"purpose": "military"},
                {"allow_remote": "yes"}):
        status, body = c.json("PUT", "/api/settings", bad)
        assert status == 400 and body["error"]["type"] == "bad_arguments", bad
    assert c.get("/api/settings")[1] == s2
    status, body = c.json("PUT", "/api/settings", ["not", "an", "object"])
    assert status == 400


# ------------------------------------------------------------------------------ calls

def test_call_native_tool(server):
    _, c = server
    env = c.call("tcm_compatibility", {"herbs": ["甘草", "甘遂"]}, project_id="p-native")
    assert env["status"] == "succeeded" and env["ok"] is True
    assert env["receipt"]["where"] == "runner"
    assert env["receipt"]["device"] == "cpu"
    assert env["receipt"]["project_id"] == "p-native"
    assert "十八反" in env["summary"]


def test_call_governed_skill_is_released_on_a_durable_chain(server):
    srv, c = server
    env = c.call("tcm_safety_report", {"subject": "甘草", "co_administered": ["甘遂"]},
                 project_id="p-gov", approvals=["first_runner_call"])
    assert env["status"] == "succeeded"
    assert env["governance"]["kind"] == "skill" and env["governance"]["released"] is True
    assert env["receipt"]["durable"] is True and env["receipt"]["content_hash"]
    assert env["receipt"]["approvals"] == ["first_runner_call"]
    assert (srv.home / "projects" / "p-gov" / "psh" / "events.db").is_file()
    verify = c.call("call_tool", {"tool": "system.audit_verify", "arguments": {}},
                    project_id="p-gov")
    assert verify["status"] == "succeeded" and verify["result"]["intact"] is True
    assert verify["result"]["records"] > 0


def test_network_off_is_a_refusal_that_names_the_runner_switch(server):
    _, c = server
    env = c.call("connector_call", {"connector": "uniprot", "operation": "entry",
                                    "arguments": {"accession": "P69905"}})
    assert env["status"] == "failed"
    assert env["error"]["type"] == "network_off"
    assert "Runner network" in env["error"]["hint"] and "--network" in env["error"]["hint"]


def test_call_errors_are_envelopes_or_clear_http_errors(server):
    _, c = server
    env = c.call("no_such_tool", {})
    assert env["status"] == "failed" and env["error"]["type"] == "not_found"
    env = c.call("tcm_herb", {"nmae": "黄芪"})
    assert env["error"]["type"] == "bad_arguments"
    env = c.call("clinic.sign", {})
    assert env["status"] == "refused"
    status, body = c.post("/api/call", {"arguments": {}})
    assert status == 400 and body["error"]["type"] == "bad_arguments"
    status, _, raw = c.raw("POST", "/api/call", b"{not json", {"Content-Type": "application/json"})
    assert status == 400 and b"not JSON" in raw


def test_capabilities_include_host_facts(server):
    _, c = server
    env = c.call("capabilities_status", {})
    host = env["result"]["host"]
    assert host["device_for_jobs"] == "cpu" and host["jobs"]["queued"] == 0
    assert {k["kind"] for k in host["kinds"]} >= {"pipeline.rnaseq", "skill.run"}
    assert env["result"]["jobs"] is True


# ---------------------------------------------------------------------------- uploads

def test_uploads_stream_hash_and_dedupe(server):
    srv, c = server
    data = b">seq1\nMKTAYIAKQRQISFVKSHFSRQ\n" * 1000
    status, _, raw = c.raw("POST", "/api/uploads", data, {
        "X-Filename": quote("黄芪 序列.fasta"), "Content-Type": "text/plain"})
    assert status == 201
    meta = json.loads(raw)
    assert meta["name"] == "黄芪 序列.fasta" and meta["bytes"] == len(data)
    assert meta["sha256"] == hashlib.sha256(data).hexdigest()
    assert meta["id"].startswith("u_") and meta["media_type"] == "text/plain"
    stored = srv.home / "uploads" / meta["id"] / "黄芪 序列.fasta"
    assert stored.read_bytes() == data
    # the same bytes under the same name are kept once
    status, _, raw = c.raw("POST", "/api/uploads", data, {"X-Filename": quote("黄芪 序列.fasta")})
    assert status == 200 and json.loads(raw)["id"] == meta["id"]
    status, listing = c.get("/api/uploads")
    assert [u["id"] for u in listing["uploads"]] == [meta["id"]]
    assert c.get(f"/api/uploads/{meta['id']}")[1]["sha256"] == meta["sha256"]
    # a path in the name never escapes the upload folder
    status, _, raw = c.raw("POST", "/api/uploads", b"x", {"X-Filename": "../../evil.txt"})
    assert status == 201 and json.loads(raw)["name"] == "evil.txt"
    assert not (srv.home / "evil.txt").exists()
    assert c.json("DELETE", f"/api/uploads/{meta['id']}")[0] == 200
    assert c.get(f"/api/uploads/{meta['id']}")[0] == 404


def test_upload_needs_a_length(server):
    _, c = server
    import http.client
    conn = http.client.HTTPConnection("127.0.0.1", c.port, timeout=10)
    conn.putrequest("POST", "/api/uploads")
    conn.putheader("X-TCM-Token", c.token)
    conn.putheader("Transfer-Encoding", "chunked")
    conn.endheaders()
    conn.send(b"3\r\nabc\r\n0\r\n\r\n")
    res = conn.getresponse()
    assert res.status == 411
    conn.close()


def test_json_bodies_are_capped(server):
    _, c = server
    big = b'{"tool":"tcm_herb","arguments":{"name":"' + b"x" * (9 * 2**20) + b'"}}'
    status, _, raw = c.raw("POST", "/api/call", big, {"Content-Type": "application/json"})
    assert status == 413 and b"too_large" in raw


def test_unknown_endpoints_and_methods(server):
    _, c = server
    assert c.get("/api/nope")[0] == 404
    status, h, _ = c.raw("DELETE", "/api/info")
    assert status == 405 and "GET" in json.loads(_)["error"]["allow"]
    assert c.raw("POST", "/index.html", b"")[0] == 405


# ---------------------------------------------------------------------- static serving

def test_static_files_with_isolation_headers(tmp_path):
    web = tmp_path / "web"
    (web / "js").mkdir(parents=True)
    (web / "index.html").write_text("<!doctype html><title>Studio</title>", encoding="utf-8")
    (web / "js" / "main.js").write_text("export const x = 1;\n", encoding="utf-8")
    (web / "app.webmanifest").write_text("{}", encoding="utf-8")
    (web / "runtime").mkdir()
    (web / "runtime" / "boot.json").write_text('{"pyodide":{}}', encoding="utf-8")
    (web / "runtime" / "tcms-py.0123456789ab.tar.gz").write_bytes(gzip.compress(b"bundle"))
    with running_server(tmp_path / "home", web=web) as (_, c):
        status, h, body = c.raw("GET", "/", token=False)
        assert status == 200 and b"<title>Studio</title>" in body
        assert h["content-type"] == "text/html; charset=utf-8"
        assert h["cross-origin-opener-policy"] == "same-origin"
        assert h["cross-origin-embedder-policy"] == "require-corp"
        assert h["cross-origin-resource-policy"] == "cross-origin"
        assert h["cache-control"] == "no-cache"
        status, h, _ = c.raw("GET", "/js/main.js", token=False)
        assert status == 200 and h["content-type"] == "text/javascript; charset=utf-8"
        assert c.raw("GET", "/app.webmanifest", token=False)[1]["content-type"] == \
            "application/manifest+json"
        status, h, _ = c.raw("HEAD", "/js/main.js", token=False)
        assert status == 200 and h["content-length"] == str(len("export const x = 1;\n"))
        # a History-API route is the app; a missing script is a real 404
        status, _, body = c.raw("GET", "/p/abc/c/def", token=False,
                                headers={"Sec-Fetch-Mode": "navigate"})
        assert status == 200 and b"Studio" in body
        assert c.raw("GET", "/js/missing.js", token=False,
                     headers={"Sec-Fetch-Mode": "cors"})[0] == 404
        assert c.raw("GET", "/../../etc/passwd", token=False,
                     headers={"Sec-Fetch-Mode": "cors"})[0] == 404
        status, h, _ = c.raw("GET", "/runtime/boot.json", token=False)
        assert status == 200 and h["cache-control"] == "no-cache"
        status, h, _ = c.raw("GET", "/runtime/tcms-py.0123456789ab.tar.gz", token=False)
        assert status == 200 and "immutable" in h["cache-control"]
        assert h["content-type"] == "application/gzip"


def _bare_web(tmp_path):
    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_text("<!doctype html>", encoding="utf-8")
    return web


def test_runtime_made_on_demand_when_the_site_is_not_built(tmp_path):
    pytest.importorskip("tcmstudio.webbuild")
    with running_server(tmp_path / "home", web=_bare_web(tmp_path)) as (_, c):
        status, h, body = c.raw("GET", "/runtime/boot.json", token=False)
        assert status == 200 and h["cache-control"] == "no-cache"
        assert h["cross-origin-embedder-policy"] == "require-corp"
        boot = json.loads(body)
        bundle = boot["bundle"]
        assert bundle["path"].startswith("runtime/tcms-py.")
        status, h, data = c.raw("GET", "/" + bundle["path"], token=False)
        assert status == 200 and "immutable" in h["cache-control"]
        assert hashlib.sha256(data).hexdigest() == bundle["sha256"]
        status, _, body = c.raw("GET", "/runtime/catalog.json", token=False)
        assert status == 200 and json.loads(body)["where"] == "browser"
        assert c.raw("GET", "/runtime/tcms-py.000000000000.tar.gz", token=False)[0] == 404


def test_runtime_without_webbuild_still_offers_the_catalog(tmp_path, monkeypatch):
    import importlib
    real = importlib.import_module

    def without_webbuild(name, *args):
        if name == "tcmstudio.webbuild":
            raise ImportError("not installed")
        return real(name, *args)
    monkeypatch.setattr(importlib, "import_module", without_webbuild)
    with running_server(tmp_path / "home", web=_bare_web(tmp_path)) as (_, c):
        status, _, body = c.raw("GET", "/runtime/catalog.json", token=False)
        assert status == 200 and json.loads(body)["where"] == "browser"
        status, _, body = c.raw("GET", "/runtime/boot.json", token=False)
        assert status == 503 and b"build_web.py" in body


def test_v1_says_the_runner_is_not_the_relay(server):
    # the app served by a runner on a port other than 8765 asks its own origin for Tao-S1's health
    _, c = server
    status, h, body = c.raw("GET", "/v1/health", token=False)
    assert status == 200
    doc = json.loads(body)
    assert doc["ok"] is False and doc["relay"] is False
    assert doc["error"]["type"] == "not_relay" and "8765" in doc["error"]["message"]
    # a code and the port, so the page can say it in its own language and place
    assert doc["error"]["code"] == "runner_no_relay" and doc["error"]["port"] == 8765
    assert doc["error"]["message_en"].startswith("This local runner does not provide Tao-S1")
    status, _, body = c.raw("POST", "/v1/chat/completions", b"{}", token=False,
                            headers={"Content-Type": "application/json"})
    err = json.loads(body)["error"]
    assert status == 404 and err["type"] == "not_relay" and err["code"] == "runner_no_relay"


def test_api_only_runner_says_where_the_app_is(server):
    _, c = server
    status, _, body = c.raw("GET", "/", token=False)
    assert status == 404 and b"science.impf.ai" in body


def test_pairing_link_round_trips():
    import base64
    link = pairing_link("http://127.0.0.1:8765", "tok")
    assert link.startswith("https://science.impf.ai/#pair=")
    code = link.split("#pair=")[1]
    assert "=" not in code
    doc = json.loads(base64.urlsafe_b64decode(code + "=" * (-len(code) % 4)))
    assert doc == {"url": "http://127.0.0.1:8765", "token": "tok"}
