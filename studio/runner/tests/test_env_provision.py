"""env.provision: one group of optional packages installed at the versions CI tested.

Nothing installs itself: the job runs only when a person approves it (every job entry asks),
installs wheels only (an sdist's build script is not covered by a reviewed pin), and is
verified afterwards by importing each package and running a calculation with a known answer.
"""
from __future__ import annotations

import pytest

from bioagent.validation.dependencies import install_command, load_environments
from tcmstudio.catalog import get_entry
from tcmstudio.kinds import KINDS, BadParams, StartContext
from test_research_snapshot_job import _ctx
from test_server_support import running_server


def test_it_installs_the_tested_pins_as_wheels_only(tmp_path):
    kind = KINDS["env.provision"]
    prepared = kind.prepare({"group": "docking"}, _ctx(tmp_path))
    pins = load_environments()["groups"]["docking"]["packages"]
    assert prepared["packages"] == pins
    start = StartContext(job_id="j", job_dir=tmp_path, project_id="p", paths={}, data_env={},
                         settings={}, device="cpu", threads=1, python="/usr/bin/python3")
    argv = kind.command(prepared, start)
    assert argv[:5] == ["/usr/bin/python3", "-I", "-m", "pip", "install"]
    assert "--only-binary=:all:" in argv
    assert {a for a in argv if "==" in a} == {f"{n}=={v}" for n, v in pins.items()}
    assert kind.needs_network(prepared)
    entry = get_entry("job.env.provision")
    assert entry["confirm"] and entry["job"], "a person approves every install"
    assert "docking" in entry["parameters"]["properties"]["group"]["enum"]


def test_a_group_without_tested_pins_is_refused(tmp_path):
    kind = KINDS["env.provision"]
    with pytest.raises(BadParams, match="installed by hand"):
        kind.prepare({"group": "structure_prediction"}, _ctx(tmp_path))
    with pytest.raises(BadParams):
        kind.prepare({"group": "everything"}, _ctx(tmp_path))
    torch = kind.prepare({"group": "machine_learning"}, _ctx(tmp_path))
    assert torch["index_urls"] == ["https://download.pytorch.org/whl/cpu"]
    assert "--extra-index-url https://download.pytorch.org/whl/cpu" in install_command(
        load_environments()["groups"]["machine_learning"])


def test_the_check_after_the_install_is_the_readiness_check(tmp_path):
    kind = KINDS["env.provision"]
    problems = kind.verify(kind.prepare({"group": "data_files"}, _ctx(tmp_path)), {})
    assert problems == [], "openpyxl and pyarrow import and compute here"
    result = kind.result(tmp_path, {"group": "data_files"})
    assert result["status"] == "PASS" and result["checks"]["openpyxl:calculation"] == "PASS"


def test_with_the_runner_offline_it_is_refused(tmp_path):
    with running_server(tmp_path / "home", network=False) as (_, c):
        status, body = c.post("/api/jobs", {"kind": "env.provision", "params": {"group": "admet"}})
        assert status == 422 and body["error"]["type"] == "network_off"
