"""The hub's reviewed checksums (``bioagent/data/hub_reviewed_checksums.json``).

A hub file's checksum is pinned only when its address names a fixed release, or the file
belongs to an archive whose files do not change, and two or more live runs read it
identically. These tests hold the table to that rule and to the specs it applies to.
"""
from __future__ import annotations

import json
import re
from importlib import resources

import pytest

from bioagent.tcmdb.datasets import dataset

pytestmark = pytest.mark.unit

DOC = json.loads(resources.files("bioagent.data").joinpath(
    "hub_reviewed_checksums.json").read_text(encoding="utf-8"))
PINS = [(key, name, pin) for key, files in DOC["files"].items()
        for name, pin in files.items()]

#: addresses that change in place: never pinned
ROLLING = re.compile(r"/current/|/latest/|/resolve/main/|/master/|version=current|/api/v\d+/"
                     r"download/|downloadApiFile|searchengine/api|/export/json")


def test_the_table_states_its_rule_and_pins_something():
    assert "two or more live runs" in DOC["rule"] and "fixed release" in DOC["rule"]
    assert len(PINS) >= 80


@pytest.mark.parametrize("key,name,pin", PINS, ids=[f"{k}/{n}" for k, n, _ in PINS])
def test_each_pin_is_a_fixed_release_read_twice_and_applied_to_its_spec(key, name, pin):
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", pin["checksum"])
    assert len(set(pin["read_by"])) >= 2, "read identically by two or more live runs"
    assert all(r.startswith("https://github.com/psknlr/TCMScience/actions/runs/")
               for r in pin["read_by"])
    assert pin["fixed"] and not ROLLING.search(pin["url"]), pin["url"]
    files = [f for f in dataset(key).files if f.name == name]
    assert files, f"{key} has no file {name}: the pin is stale and must be reviewed again"
    assert all(f.url == pin["url"] and f.checksum == pin["checksum"] for f in files)


def test_a_file_at_a_rolling_address_keeps_no_checksum():
    for key in ("np_mrd", "iptmnet", "intact", "cpic", "shennong", "tcm_ladder", "clinpgx"):
        assert not any(f.checksum for f in dataset(key).files), key
