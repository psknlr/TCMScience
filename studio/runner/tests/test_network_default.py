"""Network access is on by default in every mode, and off wherever the user or caller says.

The page's project web access and the runner's switch are tested with the page and the
runner; here, the two ways in that do not pass through them: the dispatcher's own context
and ``tcmstudio call``.
"""
from __future__ import annotations

import json

from tcmstudio.cli import main
from tcmstudio.dispatch import Context, call


def test_a_context_reaches_the_network_unless_it_says_otherwise():
    assert Context().network is True and Context().profile == "biomedical-research"
    assert Context.coerce(None)[0].network is True
    assert Context.coerce({"where": "browser"})[0].network is True
    assert Context.coerce({"network": False})[0].network is False
    assert Context.coerce({"network": {"enabled": False}})[0].network is False
    assert Context.coerce({"network": {"enabled": True, "profile": "offline-analysis"}})[0].network is False


def test_a_context_that_cannot_be_read_keeps_the_network_off(tmp_path):
    class Unreadable(dict):
        def get(self, *args, **kwargs):
            raise RuntimeError("unreadable")

    e = call("capabilities_status", {}, Unreadable(state_root=str(tmp_path)))
    assert e["status"] == "succeeded", e["error"]
    assert e["result"]["network"]["enabled"] is False, "not knowing is not permission"


def test_tcmstudio_call_reaches_the_network_unless_told_not_to(capsys):
    assert main(["call", "capabilities_status", "--compact"]) == 0
    assert json.loads(capsys.readouterr().out)["result"]["network"]["enabled"] is True
    assert main(["call", "capabilities_status", "--compact", "--no-network"]) == 0
    assert json.loads(capsys.readouterr().out)["result"]["network"]["enabled"] is False
