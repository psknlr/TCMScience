"""A connector that needs a person's credential (bioagent.credentials) through the Studio
dispatcher: the runner's environment holds it, it goes to the source as a header at call
time, and nothing the model reads, no receipt and no job's environment carries it."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from bioagent.credentials import Credential
from bioagent.providers.public_apis import Operation, PublicSource
from tcmstudio import dispatch

TOKEN = "eyJ.secret-token.value"
_SOURCE = PublicSource(
    "keyed", "A keyed service", "https://api.keyed.example/v1", "api.keyed.example",
    "registered users only", "A service that answers registered users.", "genetics",
    (Operation("info", "One record", "info/{id}", args=("id",), example={"id": "x"}),),
    smoke="info", credential=Credential("BIOAGENT_CREDENTIAL_KEYED",
                                        obtain="log in at https://keyed.example"))
_ENTRY = {"id": "connector.keyed.info", "parameters": {"type": "object"},
          "connector": {"key": "keyed", "operation": "info", "license": "registered users only",
                        "name": "A keyed service", "host": "api.keyed.example"}}


@pytest.fixture
def runtime(monkeypatch):
    from bioagent.providers import public_apis
    monkeypatch.setitem(public_apis.BY_KEY, "keyed", _SOURCE)
    sent = []

    class _Runtime:
        def invoke(self, component, spec=None, **kwargs):
            sent.append((component, kwargs))
            from bioagent.status import ExecutionStatus
            return SimpleNamespace(status=ExecutionStatus.SUCCEEDED, value={"id": "x"},
                                   error=None, adapter="http",
                                   metadata={"url": "https://api.keyed.example/v1/info/x",
                                             "method": "GET", "host": "api.keyed.example",
                                             "http_status": 200})
    monkeypatch.setattr(dispatch, "_runtime", lambda: _Runtime())
    return sent


def test_the_runner_sends_the_credential_as_a_header_and_shows_it_nowhere(runtime, monkeypatch):
    monkeypatch.setenv("BIOAGENT_CREDENTIAL_KEYED", TOKEN)
    o = dispatch._exec_connector(_ENTRY, {"id": "x"}, dispatch.Context(where="runner"))
    assert o.status == "succeeded"
    (component, kwargs), = runtime
    assert kwargs["headers"]["Authorization"] == f"Bearer {TOKEN}"
    shown = json.dumps({"result": o.result, "receipt": o.receipt, "summary": o.summary_en,
                        "governance": o.governance}, default=str)
    assert TOKEN not in shown


def test_without_it_nothing_is_sent_and_the_remedy_is_given(runtime, monkeypatch):
    monkeypatch.delenv("BIOAGENT_CREDENTIAL_KEYED", raising=False)
    o = dispatch._exec_connector(_ENTRY, {"id": "x"}, dispatch.Context(where="runner"))
    assert runtime == [] and o.status == "failed"
    assert o.error["type"] == "unavailable" and "BIOAGENT_CREDENTIAL_KEYED" in o.error["message"]
    assert "keyed.example" in o.error["hint"]


def test_the_browser_never_holds_it(runtime, monkeypatch):
    monkeypatch.setenv("BIOAGENT_CREDENTIAL_KEYED", TOKEN)
    o = dispatch._exec_connector(_ENTRY, {"id": "x"}, dispatch.Context(where="browser"))
    assert runtime == [] and o.error["type"] == "unavailable" and "runner" in o.error["message"]


def test_a_job_started_by_the_runner_does_not_inherit_a_credential(tmp_path):
    from tcmstudio import devices as dev
    from tcmstudio.jobs import JobService
    from tcmstudio.kinds import KINDS
    from tcmstudio.settings import RunnerState
    service = JobService(tmp_path, RunnerState(tmp_path), dev.DeviceProbe(torch=False),
                         environ={"BIOAGENT_CREDENTIAL_KEYED": TOKEN,
                                  "BIOAGENT_TCMDB": str(tmp_path / "hub")})
    job = SimpleNamespace(prepared={}, device="cpu")
    env = service._job_env(KINDS["pipeline.admet"], job, {"threads": 1})
    assert "BIOAGENT_CREDENTIAL_KEYED" not in env and TOKEN not in json.dumps(env)
    assert env["BIOAGENT_TCMDB"] == str(tmp_path / "hub")
