"""bioagent.credentials: a person's credential for a service that answers only registered
users, sent as a header to the service's own host over https, never stored or shown.

IEU OpenGWAS is the first such service: its connector is defined from the API's own client
and stays pending until a run with a token has verified every operation.
"""
from __future__ import annotations

import csv
import json
import runpy
import sys
from pathlib import Path

import pytest

from bioagent.credentials import (Credential, CredentialMissing, configured, describe,
                                  headers_for)

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[1]
TOKEN = "eyJ.secret-token.value"


def test_a_credential_is_sent_only_as_a_header_over_https_to_its_own_host():
    cred = Credential("BIOAGENT_CREDENTIAL_X", obtain="log in at https://x.example",
                      terms="non-commercial")
    env = {"BIOAGENT_CREDENTIAL_X": f"  {TOKEN} "}
    assert headers_for(cred, "https://api.x.example/v1", "api.x.example", env) == {
        "Authorization": f"Bearer {TOKEN}"}
    with pytest.raises(CredentialMissing, match="only over https"):
        headers_for(cred, "http://api.x.example/v1", "api.x.example", env)
    with pytest.raises(CredentialMissing, match="own host"):
        headers_for(cred, "https://elsewhere.example/v1", "api.x.example", env)
    with pytest.raises(CredentialMissing, match="line break"):
        headers_for(cred, "https://api.x.example/v1", "api.x.example",
                    {"BIOAGENT_CREDENTIAL_X": "a\r\nX-Injected: 1"})


def test_without_one_the_call_is_refused_with_how_to_get_it_and_its_terms():
    cred = Credential("BIOAGENT_CREDENTIAL_X", obtain="log in at https://x.example",
                      terms="non-commercial use only")
    with pytest.raises(CredentialMissing) as exc:
        headers_for(cred, "https://api.x.example", "api.x.example", {})
    assert "BIOAGENT_CREDENTIAL_X" in str(exc.value) and "x.example" in str(exc.value)
    assert "non-commercial use only" in str(exc.value)
    assert not configured(cred, {}) and configured(cred, {"BIOAGENT_CREDENTIAL_X": "t"})


def test_what_is_shown_of_a_credential_is_whether_it_is_set_never_its_value():
    cred = Credential("BIOAGENT_CREDENTIAL_X")
    shown = describe(cred, {"BIOAGENT_CREDENTIAL_X": TOKEN})
    assert shown["configured"] is True and TOKEN not in json.dumps(shown)


def test_a_credential_variable_is_named_so_the_runner_keeps_it_out_of_jobs():
    with pytest.raises(ValueError, match="BIOAGENT_CREDENTIAL_"):
        Credential("OPENGWAS_JWT")
    with pytest.raises(ValueError, match="value's place"):
        Credential("BIOAGENT_CREDENTIAL_X", template="Bearer")


def _opengwas():
    from bioagent.providers.supplement import load_pending
    return next(s for s in load_pending() if s.key == "opengwas")


def test_opengwas_is_pending_until_verified_with_a_token():
    from bioagent.providers.public_apis import BY_KEY
    src = _opengwas()
    assert "opengwas" not in BY_KEY, "not shipped: no run with a token has verified it"
    assert src.credential.env == "BIOAGENT_CREDENTIAL_OPENGWAS"
    assert src.base_url.startswith("https://") and src.host == "api.opengwas.io"
    # the requests the API's own client (ieugwasr) sends: POST with a JSON body
    tophits = src.op("tophits").render(id="ieu-a-2", pval=5e-8)
    assert tophits["method"] == "POST" and tophits["path"] == "tophits"
    assert tophits["json_body"] == {"id": ["ieu-a-2"], "pval": 5e-8, "preclumped": 1,
                                    "clump": 1, "r2": 0.001, "kb": 10000, "pop": "EUR"}
    assoc = src.op("associations").render(variant="rs1205", id="ieu-a-2", proxies=0)
    assert assoc["json_body"]["variant"] == ["rs1205"] and assoc["json_body"]["proxies"] == 0
    assert {o.name for o in src.operations} == {"gwasinfo", "tophits", "associations",
                                                "phewas"}, "no operation returns the account"


def _verify(monkeypatch, tmp_path, env):
    from bioagent.backends import http as httpmod
    sent = []

    class _Result:
        def __init__(self):
            from bioagent.status import ExecutionStatus
            self.status, self.value, self.error = ExecutionStatus.SUCCEEDED, {"ok": 1}, None
            self.metadata = {"http_status": 200, "attempts": 1}

    def invoke(self, manifest, **kw):
        sent.append(kw)
        return _Result()
    monkeypatch.setattr(httpmod.HTTPBackend, "invoke", invoke)
    for k in list(env):
        monkeypatch.setenv(k, env[k])
    if not env:
        monkeypatch.delenv("BIOAGENT_CREDENTIAL_OPENGWAS", raising=False)
    out = tmp_path / "rows.csv"
    monkeypatch.setattr(sys, "argv", ["verify_connectors.py", "--only", "opengwas", "--out",
                                      str(out), "--report", str(tmp_path / "report")])
    with pytest.raises(SystemExit) as code:
        runpy.run_path(str(ROOT / "scripts" / "verify_connectors.py"), run_name="__main__")
    return sent, code.value.code, list(csv.DictReader(out.open())), tmp_path / "report"


def test_verifying_opengwas_without_a_token_sends_nothing_and_says_why(monkeypatch, tmp_path):
    sent, code, rows, _ = _verify(monkeypatch, tmp_path, {})
    assert sent == [] and code == 1
    assert {r["status"] for r in rows} == {"DENIED"}
    assert all("BIOAGENT_CREDENTIAL_OPENGWAS" in r["error"] for r in rows)


def test_with_a_token_it_goes_in_the_header_and_nowhere_else(monkeypatch, tmp_path, capsys):
    sent, code, rows, report = _verify(monkeypatch, tmp_path,
                                       {"BIOAGENT_CREDENTIAL_OPENGWAS": TOKEN})
    assert code == 0 and len(sent) == 4
    assert all(kw["headers"]["Authorization"] == f"Bearer {TOKEN}" for kw in sent)
    written = (tmp_path / "rows.csv").read_text() + (report / "connector_live_report.json"
                                                     ).read_text()
    assert TOKEN not in written and TOKEN not in capsys.readouterr().out


def _run_script(monkeypatch, tmp_path, status, *extra):
    from bioagent.backends import http as httpmod
    from bioagent.status import ExecutionStatus

    class _Result:
        def __init__(self):
            self.status, self.value = ExecutionStatus(status), None
            self.error = "URLError: timed out" if status == "TIMEOUT" else "HTTP 400"
            self.metadata = {"http_status": None if status == "TIMEOUT" else 400, "attempts": 3}
    monkeypatch.setattr(httpmod.HTTPBackend, "invoke", lambda self, manifest, **kw: _Result())
    monkeypatch.setattr(sys, "argv", ["verify_connectors.py", "--only", "knapsack", "--out",
                                      str(tmp_path / "rows.csv"), "--report",
                                      str(tmp_path / "report"), *extra])
    with pytest.raises(SystemExit) as code:
        runpy.run_path(str(ROOT / "scripts" / "verify_connectors.py"), run_name="__main__")
    report = json.loads((tmp_path / "report" / "connector_live_report.json").read_text())
    return code.value.code, {r["status"] for r in report["records"]}


def test_a_service_that_did_not_answer_is_blocked_and_fails_the_run_only_when_asked(
        monkeypatch, tmp_path):
    """The live workflow fails on a wrong answer, not on a server that timed out; the
    timeout is reported BLOCKED either way, never as a pass."""
    assert _run_script(monkeypatch, tmp_path, "TIMEOUT") == (1, {"BLOCKED"})
    assert _run_script(monkeypatch, tmp_path, "TIMEOUT", "--fail-on", "fail") == (0, {"BLOCKED"})
    assert _run_script(monkeypatch, tmp_path, "FAILED", "--fail-on", "fail") == (1, {"FAIL"})
