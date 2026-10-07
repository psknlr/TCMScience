"""BioMCP through the reviewed MCP transport: the entry, the checks, and the call path.

The offline tests build the transport's entry from the provider's review and drive the
call path with a stand-in connection that answers with BioMCP's recorded replies
(``fixtures/biomcp``). The tests marked ``need_module("biomcp")`` start the real server,
which lists its tools without the network; the one live call is marked ``integration``.
"""

from __future__ import annotations

import dataclasses
import json
import sys
from pathlib import Path

import pytest

from bioagent.mcp import MCPCallError, MCPConfigError, PROXY_VARIABLES
from bioagent.providers.biomcp import load_server_config, schema_digest
from bioagent.providers.biomcp_client import BioMCPClient
from bioagent.status import ExecutionStatus

FIX = Path(__file__).parent / "fixtures" / "biomcp"
CONFIG = load_server_config()


def _fixture(name: str) -> dict:
    return json.loads((FIX / f"{name}.json").read_text(encoding="utf-8"))


class _Recorded:
    """A connection that answers every call with one recorded reply, and remembers it."""

    def __init__(self, reply: dict) -> None:
        self.reply, self.sent, self.tools = reply, [], ("article_searcher",)

    def provenance(self, tool: str) -> dict:
        return {"server": "biomcp", "tool": tool, "server_version": "0.7.3"}

    def call_tool(self, tool: str, arguments: dict) -> dict:
        self.sent.append((tool, dict(arguments)))
        return self.reply

    def close(self) -> None:
        pass


# ----------------------------------------------------------------------- the entry

def test_the_transport_entry_is_the_providers_review_filled_in(tmp_path):
    entry = BioMCPClient(tmp_path, allow_draft=True).server_config()
    assert entry.command == sys.executable
    assert entry.args == ("-I", "-m", "biomcp", "run")
    assert entry.cwd == str(tmp_path.resolve())
    assert entry.settings["XDG_CACHE_HOME"] == f"{tmp_path.resolve()}/cache"
    assert entry.destination == "public_remote"
    assert set(entry.tools) == set(CONFIG.tools)
    for name, snapshot in entry.tools.items():
        assert snapshot.input_schema == CONFIG.tools[name].input_schema_sha256
        assert snapshot.description and snapshot.output_schema


def test_every_variable_the_review_passes_is_one_the_transport_hands_on():
    assert set(CONFIG.env_pass) <= set(PROXY_VARIABLES)


def test_a_variable_the_transport_would_drop_is_refused_not_ignored(tmp_path):
    config = dataclasses.replace(CONFIG, env_pass=(*CONFIG.env_pass, "BIOMCP_API_KEY"))
    with pytest.raises(MCPConfigError, match="does not hand on"):
        BioMCPClient(tmp_path, config=config).server_config()


def test_two_runs_never_share_a_cache(tmp_path):
    one = BioMCPClient(tmp_path / "a").server_config()
    two = BioMCPClient(tmp_path / "b").server_config()
    assert one.settings["XDG_CACHE_HOME"] != two.settings["XDG_CACHE_HOME"]
    assert one.digest != two.digest


def test_the_recorded_listing_matches_the_transport_pins_too():
    listed = {t["name"]: t for t in _fixture("tools_list")["tools"]}
    for name, policy in CONFIG.tools.items():
        if name in listed:
            assert schema_digest(listed[name]["inputSchema"]) == policy.input_schema_sha256


# ------------------------------------------------------------------- the refusals

def test_a_draft_configuration_serves_only_a_run_that_asks_for_it(tmp_path):
    assert CONFIG.status == "draft"
    with pytest.raises(MCPCallError) as refused:
        BioMCPClient(tmp_path).open()
    assert refused.value.status is ExecutionStatus.DENIED
    assert "allow_draft" in refused.value.reason


def test_a_release_other_than_the_reviewed_one_is_not_started(tmp_path, monkeypatch):
    import importlib.metadata

    real = importlib.metadata.version
    monkeypatch.setattr(importlib.metadata, "version",
                        lambda name: "0.7.3" if name == "biomcp-python" else real(name))
    config = dataclasses.replace(CONFIG, version="0.0.1")
    with pytest.raises(MCPCallError) as refused:
        BioMCPClient(tmp_path, config=config, allow_draft=True).open()
    assert refused.value.status is ExecutionStatus.UNAVAILABLE
    assert "0.0.1" in refused.value.reason


def test_an_argument_the_server_drops_is_refused_before_anything_is_sent(tmp_path):
    client = BioMCPClient(tmp_path, allow_draft=True)
    client.connection = recorded = _Recorded(_fixture("article_searcher")["reply"])
    call = client.call("article_searcher", {"chemicals": ["berberine"], "page_size": 3})
    assert call.status is ExecutionStatus.DENIED and "ignores" in call.reply.reason
    assert recorded.sent == []


def test_an_unopened_client_reports_unavailable(tmp_path):
    call = BioMCPClient(tmp_path).call("article_searcher", {"chemicals": ["berberine"]})
    assert call.status is ExecutionStatus.UNAVAILABLE


# --------------------------------------------------------------------- the call path

def test_a_call_sends_the_reviewed_arguments_and_returns_records(tmp_path):
    doc = _fixture("article_searcher")
    client = BioMCPClient(tmp_path, allow_draft=True)
    client.connection = recorded = _Recorded(doc["reply"])
    asked = {k: v for k, v in doc["arguments"].items()
             if k not in ("page_size", "include_cbioportal")}
    call = client.call("article_searcher", asked)
    (tool, sent), = recorded.sent
    assert sent["include_cbioportal"] is False                    # fixed by the review
    assert call.status is ExecutionStatus.SUCCEEDED and call.reply.records
    assert call.provenance["server_version"] == "0.7.3"
    assert call.provenance["configuration"] == "draft"


def test_an_error_sent_as_a_success_is_a_failure(tmp_path):
    doc = _fixture("article_getter_pmcid")
    client = BioMCPClient(tmp_path, allow_draft=True)
    client.connection = _Recorded(doc["reply"])
    call = client.call("article_getter", doc["arguments"])
    assert doc["reply"]["isError"] is False
    assert call.status is ExecutionStatus.FAILED and call.reply.records == ()


def test_a_transport_failure_keeps_its_status(tmp_path):
    class TimesOut(_Recorded):
        def call_tool(self, tool, arguments):
            raise MCPCallError("no answer in 60s", status=ExecutionStatus.TIMEOUT,
                               metadata={"tool": tool})

    client = BioMCPClient(tmp_path, allow_draft=True)
    client.connection = TimesOut({})
    call = client.call("trial_searcher", {"conditions": ["type 2 diabetes"]})
    assert call.status is ExecutionStatus.TIMEOUT and "60s" in call.reply.reason


# ---------------------------------------------------------------- the real server

def test_the_installed_server_passes_both_checks_and_closes(tmp_path):
    from omics_world import need_mcp_sdk, need_module
    need_module("biomcp")
    need_mcp_sdk()
    with BioMCPClient(tmp_path / "run", allow_draft=True) as client:
        assert set(client.admitted) == set(CONFIG.tools)
        connection = client.connection
        refused = client.call("trial_searcher", {"conditions": ["diabetes"], "sex": "female"})
        assert refused.status is ExecutionStatus.DENIED
    assert client.connection is None and not connection.alive


def test_a_description_rewritten_after_review_is_refused(tmp_path):
    from omics_world import need_mcp_sdk, need_module
    need_module("biomcp")
    need_mcp_sdk()
    tools = dict(CONFIG.tools)
    tools["article_getter"] = dataclasses.replace(tools["article_getter"],
                                                  description_sha256="0" * 64)
    config = dataclasses.replace(CONFIG, tools=tools)
    with pytest.raises(MCPCallError) as refused:
        BioMCPClient(tmp_path / "run", config=config, allow_draft=True).open()
    assert refused.value.status is ExecutionStatus.DENIED
    assert "article_getter" in refused.value.reason


@pytest.mark.integration
def test_a_live_article_lookup_is_read_into_canonical_records(tmp_path):
    from omics_world import need_mcp_sdk, need_module
    need_module("biomcp")
    need_mcp_sdk()
    with BioMCPClient(tmp_path / "run", allow_draft=True) as client:
        call = client.call("article_getter", {"pmid": "34956436"})
    assert call.status is ExecutionStatus.SUCCEEDED, call.reply.reason
    assert str(call.reply.records[0].key) == "pmid:34956436"
