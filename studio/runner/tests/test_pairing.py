"""Pairing without the token in a URL (docs/V2.md §16): one-time codes, requests approved on the
runner's own page, and every way a page that is not the user's could try to get the token."""

from __future__ import annotations

import base64
import json
import re
import sys
from urllib.parse import urlencode, urlsplit

import pytest

from tcmstudio import settings as st
from tcmstudio.security import PairBook, RateLimit
from tcmstudio.server import _installer_hint, fresh_pairing_link

from test_server_support import running_server

SITE = "https://science.impf.ai"


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def decode(link: str) -> dict:
    code = link.split("#pair=")[1]
    return json.loads(base64.urlsafe_b64decode(code + "=" * (-len(code) % 4)))


@pytest.fixture
def server(tmp_path):
    with running_server(tmp_path / "home") as pair:
        yield pair


def claim(c, code, origin=SITE, **kw):
    headers = {} if origin is None else {"Origin": origin}
    return c.post("/api/pair/claim", {"code": code}, headers=headers, token=False, **kw)


def own(srv) -> str:
    return f"127.0.0.1:{srv.port}"


def open_request(c, origin=SITE):
    status, body = c.post("/api/pair/request", {}, headers={"Origin": origin}, token=False)
    assert status == 201, body
    return body


def approve_form(c, srv, req, *, decision="allow", origin="own", nonce=None, site=None):
    status, _, page = c.raw("GET", f"/pair?r={req['request_id']}", token=False,
                            headers={"Host": own(srv)})
    assert status == 200, page
    found = re.search(r'name="nonce" value="([^"]+)"', page.decode()).group(1)
    form = urlencode({"r": req["request_id"], "nonce": found if nonce is None else nonce,
                      "decision": decision}).encode()
    headers = {"Host": own(srv), "Content-Type": "application/x-www-form-urlencoded"}
    if origin == "own":
        headers["Origin"] = f"http://{own(srv)}"
    elif origin is not None:
        headers["Origin"] = origin
    if site:
        headers["Sec-Fetch-Site"] = site
    return c.raw("POST", "/pair", form, headers, token=False)


def poll(c, req, origin=SITE, secret=None):
    headers = {"Origin": origin}
    if secret is not False:
        headers["X-TCM-Pair-Secret"] = req["secret"] if secret is None else secret
    return c.get(f"/api/pair/request/{req['request_id']}", headers=headers, token=False)


# ------------------------------------------------------------------------------ the book

def test_book_codes_are_single_use_and_expire():
    clock = Clock()
    book = PairBook(clock=clock)
    a, b = book.new_code(), book.new_code()
    assert a != b and len(a) == 22                     # 16 random bytes, URL-safe
    assert book.claim(a) is True
    assert book.claim(a) is False                      # single use
    clock.now += 601
    assert book.claim(b) is False                      # expired after ten minutes
    assert book.claim("x" * 22) is False and book.claim(None) is False and book.claim("short") is False


def test_book_keeps_a_bounded_number_of_codes():
    book = PairBook(max_codes=3, clock=Clock())
    codes = [book.new_code() for _ in range(4)]
    assert book.claim(codes[0]) is False               # the oldest went first
    assert all(book.claim(c) for c in codes[1:])


def test_book_requests_states_and_limits():
    clock = Clock()
    book = PairBook(clock=clock, max_pending=2)
    r1, r2 = book.open_request(SITE), book.open_request(SITE)
    assert book.open_request(SITE) is None             # two pending at most
    assert re.fullmatch(r"\d{6}", r1["code"])
    assert book.answer(r1["id"], "wrong-nonce", True) is None
    assert book.answer(r1["id"], r1["nonce"], True) == "approved"
    assert book.answer(r1["id"], r1["nonce"], True) is None         # answered once
    assert book.poll(r1["id"], "https://other.example", r1["secret"]) is None
    assert book.poll(r1["id"], SITE, "guess") is None
    assert book.poll(r1["id"], SITE, r1["secret"]) == {"state": "approved", "expires_in": 600,
                                                        "deliver": True}
    assert book.poll(r1["id"], SITE, r1["secret"])["state"] == "delivered"
    assert book.answer(r2["id"], r2["nonce"], False) == "denied"
    r3 = book.open_request(SITE)
    clock.now += 600
    assert book.poll(r3["id"], SITE, r3["secret"])["state"] == "expired"
    assert book.answer(r3["id"], r3["nonce"], True) is None
    clock.now += 61
    assert book.request(r3["id"]) is None              # forgotten a minute after it expired


def test_rate_limit_is_a_sliding_window():
    clock = Clock()
    limit = RateLimit(2, 10, clock)
    assert limit.allow() and limit.allow() and not limit.allow()
    assert limit.exhausted()
    clock.now += 10
    assert limit.allow() and not limit.exhausted()


# -------------------------------------------------------------------- one-time codes, HTTP

def test_claim_trades_a_one_time_code_for_the_token(server):
    srv, c = server
    doc = decode(srv.pairing_link())
    assert set(doc) == {"url", "code"} and doc["url"] == srv.url
    status, h, raw = c.raw("POST", "/api/pair/claim", json.dumps({"code": doc["code"]}).encode(),
                           {"Origin": SITE, "Content-Type": "application/json"}, token=False)
    assert status == 200 and json.loads(raw) == {"url": srv.url, "token": srv.guard.token}
    assert h["access-control-allow-origin"] == SITE
    # reused: refused, and says what to do
    status, body = claim(c, doc["code"])
    assert status == 410 and body["error"]["type"] == "code_invalid"
    assert "Connect" in body["error"]["message"]
    # the runner's own page claims with its own origin
    local = decode(srv.pairing_link(srv.url))
    assert claim(c, local["code"], origin=f"http://{own(srv)}")[0] == 200


def test_claim_refuses_other_origins_no_origin_and_expired_codes(server):
    srv, c = server
    code = decode(srv.pairing_link())["code"]
    assert claim(c, code, origin=None)[0] == 403              # curl or a script: no Origin
    assert claim(c, code, origin="null")[0] == 403            # a sandboxed frame or file://
    status, h, _ = c.raw("POST", "/api/pair/claim", json.dumps({"code": code}).encode(),
                         {"Origin": "https://evil.example", "Content-Type": "application/json"},
                         token=False)
    assert status == 403 and "access-control-allow-origin" not in h
    assert claim(c, code)[0] == 200                           # none of those spent it
    clock = Clock()
    srv.pairs = PairBook(clock=clock)
    late = decode(srv.pairing_link())["code"]
    clock.now += 601
    assert claim(c, late)[0] == 410


def test_wrong_codes_are_rate_limited(server):
    srv, c = server
    good = decode(srv.pairing_link())["code"]
    for _ in range(10):
        assert claim(c, "A" * 22)[0] == 410
    status, body = claim(c, good)
    assert status == 429 and body["error"]["type"] == "too_many_attempts"


def test_a_tokenless_runner_pairs_by_address(tmp_path):
    with running_server(tmp_path / "home", no_token=True) as (srv, c):
        assert decode(srv.pairing_link()) == {"url": srv.url, "token": ""}
        c.token = None
        status, body = c.post("/api/pair/link", {})
        assert status == 200 and decode(body["link"])["token"] == ""


def test_fresh_link_needs_the_token_and_is_what_the_installer_opens(server, tmp_path):
    srv, c = server
    assert c.post("/api/pair/link", {}, token=False)[0] == 401
    status, body = c.post("/api/pair/link", {})
    assert status == 200 and body["expires_in"] == 600
    assert decode(body["link"])["url"] == srv.url and body["link"].startswith(SITE + "/#pair=")
    assert body["local_link"].startswith(srv.url + "/#pair=")
    link = fresh_pairing_link(srv.url, srv.home)
    assert claim(c, decode(link)["code"])[0] == 200
    local = fresh_pairing_link(srv.url, srv.home, local=True)
    assert local.startswith(srv.url) and claim(c, decode(local)["code"])[0] == 200
    with pytest.raises(OSError):
        fresh_pairing_link(srv.url, tmp_path / "nowhere")


# ------------------------------------------------------------------- requests and approval

def test_request_approved_on_the_runners_page_delivers_the_token_once(server):
    srv, c = server
    req = open_request(c)
    assert set(req) == {"request_id", "secret", "code6", "approve_url", "expires_in"}
    assert re.fullmatch(r"\d{6}", req["code6"]) and req["expires_in"] == 600
    assert req["approve_url"] == f"{srv.url}/pair?r={req['request_id']}"
    pending = poll(c, req)[1]
    assert pending["state"] == "pending" and 590 <= pending["expires_in"] <= 600
    # the approval page: the origin that asked, the same code, a form with a nonce
    status, h, page = c.raw("GET", urlsplit(req["approve_url"]).path + "?r=" + req["request_id"],
                            token=False, headers={"Host": own(srv)})
    text = page.decode()
    assert status == 200 and h["content-type"].startswith("text/html")
    assert SITE in text and f"{req['code6'][:3]} {req['code6'][3:]}" in text
    assert 'name="nonce"' in text and 'action="/pair"' in text
    status, h, page = approve_form(c, srv, req)
    assert status == 200 and 'data-state="approved"' in page.decode()
    status, body = poll(c, req)
    assert status == 200 and body["state"] == "approved"
    assert body["token"] == srv.guard.token and body["url"] == srv.url
    status, body = poll(c, req)
    assert body == {"state": "delivered", "expires_in": body["expires_in"]}   # never twice


def test_denied_request_gives_nothing(server):
    srv, c = server
    req = open_request(c)
    status, _, page = approve_form(c, srv, req, decision="deny")
    assert status == 200 and 'data-state="denied"' in page.decode()
    status, body = poll(c, req)
    assert body["state"] == "denied" and "token" not in body


def test_approval_page_cannot_be_framed_cached_or_read_by_another_origin(server):
    srv, c = server
    req = open_request(c)
    status, h, _ = c.raw("GET", f"/pair?r={req['request_id']}", token=False,
                         headers={"Host": own(srv)})
    assert status == 200
    assert "frame-ancestors 'none'" in h["content-security-policy"]
    assert "default-src 'none'" in h["content-security-policy"]
    assert h["x-frame-options"] == "DENY"
    # no-referrer would make Chromium send Origin: null with the form (and the answer refused)
    assert h["referrer-policy"] == "same-origin"
    assert h["cache-control"] == "no-store" and "access-control-allow-origin" not in h
    # a script on an allowed origin fetching the page (to read its nonce) gets nothing
    status, h, page = c.raw("GET", f"/pair?r={req['request_id']}", token=False,
                            headers={"Host": own(srv), "Origin": SITE})
    assert status == 403 and "access-control-allow-origin" not in h
    assert req["secret"] not in page.decode()


def test_approval_refuses_wrong_or_missing_origin_and_bad_nonce(server):
    srv, c = server
    req = open_request(c)
    _, _, page = c.raw("GET", f"/pair?r={req['request_id']}", token=False,
                       headers={"Host": own(srv)})
    nonce = re.search(r'name="nonce" value="([^"]+)"', page.decode()).group(1)

    def answer(origin="own", *, n=nonce, site=None, decision="allow"):
        headers = {"Host": own(srv), "Content-Type": "application/x-www-form-urlencoded"}
        if origin is not None:
            headers["Origin"] = f"http://{own(srv)}" if origin == "own" else origin
        if site:
            headers["Sec-Fetch-Site"] = site
        form = urlencode({"r": req["request_id"], "nonce": n, "decision": decision}).encode()
        status, _, body = c.raw("POST", "/pair", form, headers, token=False)
        return status, body.decode()

    # an allowed origin for the API (another local page, the public site) is not this page
    for origin in ("http://localhost:18800", SITE, None, "null", "https://evil.example"):
        assert answer(origin)[0] == 403, origin
    assert answer(site="cross-site")[0] == 403
    status, body = answer(n="not-the-nonce")
    assert status == 410 and 'data-state="gone"' in body
    assert answer(decision="maybe")[0] == 410
    assert poll(c, req)[1]["state"] == "pending"              # none of those answered it
    status, body = answer()
    assert status == 200 and 'data-state="approved"' in body
    assert answer(decision="deny")[0] == 410                  # answered once
    status, _, _ = c.raw("GET", f"/pair?r={req['request_id']}", token=False,
                         headers={"Host": own(srv)})
    assert status == 410


def test_dns_rebinding_and_remote_hosts_cannot_reach_the_approval(server, tmp_path):
    srv, c = server
    req = open_request(c)
    status, _, body = c.raw("GET", f"/pair?r={req['request_id']}", token=False,
                            headers={"Host": "evil.example:8765"})
    assert status == 421 and b"forbidden_host" in body
    # a runner listening on every address still approves only from this computer
    with running_server(tmp_path / "lan", host="0.0.0.0") as (lan, lc):
        r = open_request(lc)
        status, _, page = lc.raw("GET", f"/pair?r={r['request_id']}", token=False,
                                 headers={"Host": f"192.168.1.20:{lan.port}"})
        assert status == 403 and 'data-state="forbidden"' in page.decode()


def test_polling_needs_the_secret_and_the_same_origin(server):
    srv, c = server
    req = open_request(c)
    assert poll(c, req, secret="wrong")[0] == 404
    assert poll(c, req, secret=False)[0] == 404
    assert poll(c, req, origin="http://localhost:3000")[0] == 404
    status, _, _ = c.raw("GET", f"/api/pair/request/{req['request_id']}", token=False,
                         headers={"X-TCM-Pair-Secret": req["secret"]})
    assert status == 403                                     # no Origin at all
    assert poll(c, {"request_id": "nope", "secret": req["secret"]})[0] == 404
    # the browser may send the secret header (preflight)
    status, h, _ = c.raw("OPTIONS", f"/api/pair/request/{req['request_id']}", token=False, headers={
        "Origin": SITE, "Access-Control-Request-Method": "GET",
        "Access-Control-Request-Headers": "x-tcm-pair-secret"})
    assert status == 204 and "x-tcm-pair-secret" in h["access-control-allow-headers"]


def test_request_needs_an_allowed_origin_and_flooding_is_refused(server):
    srv, c = server
    assert c.post("/api/pair/request", {}, token=False)[0] == 403
    assert c.post("/api/pair/request", {}, headers={"Origin": "https://evil.example"},
                  token=False)[0] == 403
    assert c.post("/api/pair/request", {}, headers={"Origin": "null"}, token=False)[0] == 403
    reqs = [open_request(c) for _ in range(5)]
    status, body = c.post("/api/pair/request", {}, headers={"Origin": SITE}, token=False)
    assert status == 429 and body["error"]["type"] == "too_many_pending"
    for r in reqs:                                            # answered requests free their place
        approve_form(c, srv, r, decision="deny")
    for _ in range(15):
        r = open_request(c)
        approve_form(c, srv, r, decision="deny")
    status, body = c.post("/api/pair/request", {}, headers={"Origin": SITE}, token=False)
    assert status == 429 and body["error"]["type"] == "too_many_requests"


def test_expired_request(server):
    srv, c = server
    clock = Clock()
    srv.pairs = PairBook(clock=clock)
    req = open_request(c)
    clock.now += 601
    assert poll(c, req)[1]["state"] == "expired"
    status, _, page = c.raw("GET", f"/pair?r={req['request_id']}", token=False,
                            headers={"Host": own(srv)})
    assert status == 410


def test_pairing_secrets_never_reach_the_log(tmp_path, capsys):
    with running_server(tmp_path / "home") as (srv, c):
        req = open_request(c)
        approve_form(c, srv, req)
        poll(c, req)
        claim(c, decode(srv.pairing_link())["code"])
        poll(c, req, secret="wrong")
    err = capsys.readouterr().err
    assert "配对请求 Pair request" in err and req["code6"][:3] in err and "已配对 Paired" in err
    for secret in (srv.guard.token, req["secret"]):
        assert secret not in err
    assert "GET /api/pair/request/… 404" in err


def test_no_console_at_all_does_not_break_answers(tmp_path, monkeypatch):
    """pythonw has no console: sys.stderr is None. Logging must not break the response."""
    with running_server(tmp_path / "home") as (srv, c):
        monkeypatch.setattr(sys, "stderr", None)
        assert c.post("/api/call", {"tool": "tcm_herb", "arguments": {"name": "黄芪"}})[0] == 200
        assert open_request(c)["code6"]
        monkeypatch.undo()


# ---------------------------------------------------------------- install-aware hints

def write_record(home, **options):
    home.mkdir(parents=True, exist_ok=True)
    doc = {"schema": st.INSTALL_SCHEMA, "method": "uv-tool", "version": "0.1.0+abc",
           "site": "https://science.impf.ai", "os": options.pop("os", "linux"),
           "options": {"cn": False, "gpu": False, "autostart": False, "extras": [], **options}}
    (home / "install.json").write_text(json.dumps(doc), encoding="utf-8")


def test_install_record_applies_only_to_a_uv_tool_environment(tmp_path):
    home, prefix = tmp_path / "home", tmp_path / "env"
    prefix.mkdir()
    write_record(home, extras=["analysis", "../evil"])
    assert st.install_record(home, prefix=prefix) is None        # not a uv tool environment
    (prefix / "uv-receipt.toml").write_text("[tool]\n")
    rec = st.install_record(home, prefix=prefix)
    assert rec["method"] == "uv-tool" and rec["options"]["extras"] == ["analysis"]
    (home / "install.json").write_text("{broken")
    assert st.install_record(home, prefix=prefix) is None


def test_reinstall_command_keeps_earlier_choices():
    rec = {"site": "https://science.impf.ai", "os": "linux",
           "options": {"cn": True, "gpu": False, "autostart": True, "extras": ["admet"]}}
    assert st.reinstall_command(rec, ["analysis"]) == (
        "curl -LsSf https://science.impf.ai/install.sh | sh -s -- --cn --autostart "
        "--extras admet,analysis")
    assert st.reinstall_command({**rec, "options": {}}) == "curl -LsSf https://science.impf.ai/install.sh | sh"
    win = {**rec, "os": "windows", "options": {"gpu": True, "extras": ["fold"]}}
    assert st.reinstall_command(win, ["scvi"]) == (
        'powershell -ExecutionPolicy ByPass -c "& ([scriptblock]::Create((irm '
        'https://science.impf.ai/install.ps1))) --gpu --extras scvi"')
    assert st.extras_for(["rdkit", "scanpy", "leidenalg", "salmon", "torch"]) == ["admet", "analysis", "fold"]


def test_engines_and_refusals_point_to_the_installer(server, monkeypatch):
    srv, c = server
    srv.install = {"method": "uv-tool", "version": "0.1.0", "site": "https://science.impf.ai",
                   "os": "linux", "options": {"cn": False, "gpu": False, "autostart": False,
                                              "extras": []}}
    from tcmstudio import devices
    monkeypatch.setattr(devices, "engines", lambda *a, **k: [
        {"id": "scanpy", "name": "Scanpy", "installed": False, "missing": ["scanpy"],
         "install": "pip install scanpy leidenalg harmonypy"},
        {"id": "salmon", "name": "Salmon", "installed": False, "missing": ["salmon"],
         "install": "conda install -c bioconda salmon"}])
    srv._facts = None
    engines = {e["id"]: e for e in c.get("/api/info")[1]["engines"]}
    assert engines["scanpy"]["install"].endswith("| sh -s -- --extras analysis")
    assert engines["salmon"]["install"].startswith("conda install")      # a system tool stays
    env = {"text": "x Remedy: Install it (pip install …) on the runner machine",
           "error": {"type": "unavailable", "message": "Docking needs rdkit, vina, which is not "
                     "installed on this runner.", "hint": "Install it (pip install …) on the runner machine"}}
    _installer_hint(env, env["error"], srv.install)
    assert env["error"]["hint"].endswith("install.sh | sh -s -- --extras admet,docking")
    assert "pip install" not in env["text"]
