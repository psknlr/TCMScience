"""Who may talk to the runner: Host check, Origin allowlist, preflight and the token."""

from __future__ import annotations

import pytest

from tcmstudio.security import (Guard, SecurityError, host_of, is_loopback_host,
                                normalize_origin)
from tcmstudio.server import RunnerServer, ServerConfig

from test_server_support import running_server

TOKEN = "t" * 32


def guard(**kw) -> Guard:
    kw.setdefault("bind_host", "127.0.0.1")
    kw.setdefault("token", TOKEN)
    return Guard(**kw)


# ----------------------------------------------------------------------------- unit level

@pytest.mark.parametrize("host,ok", [
    ("127.0.0.1:8765", True), ("localhost:8765", True), ("[::1]:8765", True),
    ("LOCALHOST", True), ("127.1.2.3:80", True),
    ("evil.example:8765", False), ("science.impf.ai", False), ("192.168.1.5:8765", False),
    ("", False), (None, False), ("localhost.evil.example", False)])
def test_host_check_refuses_rebound_names(host, ok):
    assert guard().host_ok(host) is ok


def test_host_check_accepts_the_bound_address_and_any_on_a_wildcard_bind():
    assert guard(bind_host="192.168.1.5").host_ok("192.168.1.5:8765")
    assert not guard(bind_host="192.168.1.5").host_ok("evil.example")
    # bound to every interface on purpose: the token is what guards it then
    assert guard(bind_host="0.0.0.0").host_ok("my-laptop.lan:8765")


@pytest.mark.parametrize("origin,ok", [
    (None, True),                                      # curl, same-origin GET
    ("https://science.impf.ai", True),
    ("https://SCIENCE.impf.ai/", True),
    ("http://127.0.0.1:8765", True), ("http://localhost:3000", True), ("http://[::1]:9", True),
    ("https://localhost", True),
    ("https://evil.example", False), ("http://science.impf.ai", False),
    ("https://science.impf.ai.evil.example", False), ("null", False), ("", False),
    ("file://", False), ("chrome-extension://abc", False)])
def test_origin_allowlist(origin, ok):
    assert guard().origin_allowed(origin, "127.0.0.1:8765") is ok


def test_allow_origin_and_own_origin():
    g = guard(allow_origins=["https://lab.example.org/"])
    assert g.origin_allowed("https://lab.example.org", "127.0.0.1:8765")
    assert not g.origin_allowed("https://other.example.org", "127.0.0.1:8765")
    # the runner's own pages when reached by a LAN address
    g = guard(bind_host="0.0.0.0")
    assert g.origin_allowed("http://192.168.1.5:8765", "192.168.1.5:8765")
    assert not g.origin_allowed("http://192.168.1.6:8765", "192.168.1.5:8765")


@pytest.mark.parametrize("bad", ["*", "https://*.example.org", "example.org", "ftp://x.org",
                                 "https://x.org/path", "https://user@x.org"])
def test_allow_origin_refuses_wildcards_and_non_origins(bad):
    with pytest.raises(SecurityError):
        normalize_origin(bad)


def test_normalize_origin_drops_default_ports():
    assert normalize_origin("HTTPS://Lab.Example.org:443/") == "https://lab.example.org"
    assert normalize_origin("http://10.0.0.5:8000") == "http://10.0.0.5:8000"


def test_token_is_compared_and_optional_only_on_loopback():
    g = guard()
    assert g.token_ok(TOKEN) and not g.token_ok("x") and not g.token_ok(None)
    assert not g.token_ok(TOKEN + "x")
    assert Guard(bind_host="127.0.0.1", token=None).token_ok(None)
    assert Guard.resolve_token("127.0.0.1", TOKEN, True) is None
    assert Guard.resolve_token("::1", TOKEN, True) is None
    assert Guard.resolve_token("127.0.0.1", TOKEN, False) == TOKEN
    for host in ("0.0.0.0", "192.168.1.5", "::"):
        with pytest.raises(SecurityError):
            Guard.resolve_token(host, TOKEN, True)


def test_preflight_headers_include_private_network_access():
    status, headers = guard().preflight_headers("https://science.impf.ai", "127.0.0.1:8765",
                                                True, "x-tcm-token, content-type, x-custom-key")
    h = dict(headers)
    assert status == 204
    assert h["Access-Control-Allow-Origin"] == "https://science.impf.ai"
    assert h["Access-Control-Allow-Private-Network"] == "true"
    assert "x-tcm-token" in h["Access-Control-Allow-Headers"]
    assert "x-custom-key" in h["Access-Control-Allow-Headers"]
    assert "DELETE" in h["Access-Control-Allow-Methods"]
    status, headers = guard().preflight_headers("https://evil.example", "127.0.0.1:8765", True)
    assert status == 403 and "Access-Control-Allow-Origin" not in dict(headers)


def test_helpers():
    assert host_of("[::1]:8765") == "::1"
    assert host_of("Example.ORG:80") == "example.org"
    assert is_loopback_host("127.0.0.2") and not is_loopback_host("10.0.0.1")


# ------------------------------------------------------------------------- over HTTP

def test_health_needs_no_token_and_everything_else_does(tmp_path):
    with running_server(tmp_path / "home") as (srv, c):
        status, body = c.get("/api/health", token=False)
        assert status == 200
        assert body == {"name": "tcmstudio", "version": body["version"], "token_required": True,
                        "paired": False}
        assert c.get("/api/health")[1]["paired"] is True
        for path in ("/api/info", "/api/catalog", "/api/devices", "/api/settings", "/api/kinds",
                     "/api/jobs", "/api/uploads", "/api/llm/local"):
            status, body = c.get(path, token=False)
            assert status == 401, path
            assert body["error"]["type"] == "token" and body["token_required"] is True
        status, body = c.get("/api/info", headers={"X-TCM-Token": "wrong"}, token=False)
        assert status == 401
        assert c.post("/api/call", {"tool": "tcm_herb", "arguments": {"name": "黄芪"}},
                      token=False)[0] == 401
        # ?token= is accepted on GET (EventSource, file links), not on a POST
        assert c.get(f"/api/info?token={srv.guard.token}", token=False)[0] == 200
        assert c.post(f"/api/call?token={srv.guard.token}", {"tool": "tcm_herb"},
                      token=False)[0] == 401


def test_cors_allow_deny_and_preflight(tmp_path):
    with running_server(tmp_path / "home") as (srv, c):
        status, h, _ = c.raw("GET", "/api/info", headers={"Origin": "https://science.impf.ai"})
        assert status == 200
        assert h["access-control-allow-origin"] == "https://science.impf.ai"
        assert "origin" in h["vary"].lower()
        # a refused token still tells an allowed page why
        status, h, _ = c.raw("GET", "/api/info", headers={"Origin": "https://science.impf.ai"},
                             token=False)
        assert status == 401 and h["access-control-allow-origin"] == "https://science.impf.ai"
        status, h, body = c.raw("GET", "/api/info", headers={"Origin": "https://evil.example"})
        assert status == 403 and "access-control-allow-origin" not in h
        assert b"forbidden_origin" in body
        status, h, _ = c.raw("GET", "/api/health", headers={"Origin": "https://evil.example"},
                             token=False)
        assert status == 403
        status, h, _ = c.raw("OPTIONS", "/api/call", token=False, headers={
            "Origin": "https://science.impf.ai", "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,x-tcm-token",
            "Access-Control-Request-Private-Network": "true"})
        assert status == 204
        assert h["access-control-allow-private-network"] == "true"
        assert h["access-control-allow-origin"] == "https://science.impf.ai"
        assert "x-tcm-token" in h["access-control-allow-headers"]
        status, h, _ = c.raw("OPTIONS", "/api/call", token=False, headers={
            "Origin": "https://evil.example", "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Private-Network": "true"})
        assert status == 403 and "access-control-allow-private-network" not in h
        status, h, _ = c.raw("GET", "/api/health", token=False,
                             headers={"Origin": "http://localhost:5173"})
        assert status == 200 and h["access-control-allow-origin"] == "http://localhost:5173"


def test_allow_origin_flag(tmp_path):
    with running_server(tmp_path / "home", allow_origins=["https://lab.example.org"]) as (_, c):
        status, h, _ = c.raw("GET", "/api/health", token=False,
                             headers={"Origin": "https://lab.example.org"})
        assert status == 200 and h["access-control-allow-origin"] == "https://lab.example.org"


def test_dns_rebinding_host_is_refused(tmp_path):
    with running_server(tmp_path / "home") as (_, c):
        status, _, body = c.raw("GET", "/api/health", headers={"Host": "evil.example:8765"},
                                token=False)
        assert status == 421 and b"forbidden_host" in body
        status, _, body = c.raw("GET", "/", headers={"Host": "attacker.test"}, token=False)
        assert status == 421
        status, _, _ = c.raw("GET", "/api/health", headers={"Host": "localhost:1"}, token=False)
        assert status == 200


def test_no_token_mode_and_its_limits(tmp_path):
    with running_server(tmp_path / "home", no_token=True) as (srv, c):
        assert srv.guard.token is None
        c.token = None
        status, body = c.get("/api/health")
        assert body["token_required"] is False and body["paired"] is True
        assert c.get("/api/info")[0] == 200
        # an origin check still applies without a token
        assert c.raw("GET", "/api/info", headers={"Origin": "https://evil.example"})[0] == 403
    with pytest.raises(SecurityError):
        RunnerServer(ServerConfig(host="0.0.0.0", port=0, home=tmp_path / "h2", no_token=True,
                                  web=None, torch_probe=False))


def test_token_is_made_once_and_kept_private(tmp_path):
    home = tmp_path / "home"
    with running_server(home) as (srv, _):
        first = srv.guard.token
    assert (home / "runner.json").stat().st_mode & 0o077 == 0
    with running_server(home) as (srv, _):
        assert srv.guard.token == first and len(first) >= 30


def test_request_bodies_and_tokens_are_not_logged(tmp_path, capsys):
    with running_server(tmp_path / "home") as (srv, c):
        c.post("/api/call", {"tool": "tcm_herb", "arguments": {"name": "SECRET-BODY-黄芪"}})
        c.get(f"/api/jobs?token={srv.guard.token}", token=False)
        c.get("/api/nope")
    err = capsys.readouterr().err
    assert "SECRET-BODY" not in err and srv.guard.token not in err
    assert "POST /api/call 200" in err
