"""ToolUniverse as a reviewed provider: what is admitted, how it maps, how calls end.

The offline tests use a fake installation (a ``data/`` directory holding one
configuration file) and need no ToolUniverse. The tests that build an engine need the
package and skip without it; the live UniProt call is marked ``integration``.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
from omics_world import need_module

from bioagent.backends.concrete import PythonBackend
from bioagent.policy import PROFILES
from bioagent.providers import tooluniverse as tu
from bioagent.providers.tooluniverse import (Allowlist, Installed, ReviewedTool,
                                             ToolUniverseCallError, ToolUniverseProvider,
                                             classify, entry_digest, load_allowlist,
                                             smoke_arguments)
from bioagent.runtime.component import ComponentManifest, RuntimeSpec
from bioagent.runtime.registry import ComponentRegistry
from bioagent.status import ExecutionStatus, LifecycleState

NAME = "UniProt_get_entry_by_accession"
ENTRY = {
    "name": NAME, "type": "UniProtRESTTool",
    "description": "Get the complete JSON entry for a specified UniProtKB accession.",
    "parameter": {"type": "object", "required": ["accession"],
                  "properties": {"accession": {"type": "string"},
                                 "compact": {"type": "boolean", "default": True}}},
    "fields": {"endpoint": "https://rest.uniprot.org/uniprotkb/{accession}.json"},
    "test_examples": [{"accession": "P04637"}],
    "return_schema": {"type": "object", "properties": {"primaryAccession":
                                                       {"type": "string"}}},
}
UNLISTED = {**ENTRY, "name": "UniProt_search", "test_examples": [{"query": "TP53"}]}


def _install(tmp_path: Path, *entries: dict, version: str = "1.5.6") -> Installed:
    data = tmp_path / "data"
    data.mkdir(exist_ok=True)
    (data / "uniprot_tools.json").write_text(json.dumps(list(entries or (ENTRY, UNLISTED))),
                                             encoding="utf-8")
    return Installed(version, data)


def _allowlist(entry: dict = ENTRY, **changes) -> Allowlist:
    tool = ReviewedTool(name=NAME, config_file="uniprot_tools.json",
                        entry_sha256=entry_digest(entry),
                        implementation="tooluniverse.uniprot_tool.UniProtRESTTool",
                        hosts=("rest.uniprot.org",), domain="proteomics",
                        purpose="the UniProtKB entry for an accession",
                        data_terms="https://www.uniprot.org/help/license",
                        live_check={"on": "2026-10-07", "status": "SUCCEEDED"})
    return Allowlist(reviewed_version="1.5.6", reviewed_on="2026-10-07",
                     package_licence="Apache-2.0",
                     repo="https://github.com/mims-harvard/ToolUniverse",
                     tools=(replace(tool, **changes),), path="test")


def _one(provider: ToolUniverseProvider) -> ComponentManifest:
    (manifest,) = provider.discover()
    return manifest


# ------------------------------------------------------------------ the shipped review
def test_the_allowlist_names_a_reviewed_handful_under_fixed_ids():
    allow = load_allowlist()
    assert 5 <= len(allow.tools) <= 10 and allow.reviewed_version == "1.5.6"
    assert allow.get(NAME) is not None
    profile = PROFILES["biomedical-research"]
    for tool in allow.tools:
        assert tool.component_id == f"tooluniverse.{tool.name}"
        assert tool.implementation.startswith("tooluniverse.")
        assert tool.config_file in allow.config_files
        assert profile.check_network(tool.hosts).allowed, tool.name
        assert tool.live_check.get("status") == "SUCCEEDED", tool.name


# ------------------------------------------------------------------ mapping
def test_a_reviewed_tool_maps_onto_a_python_component(tmp_path):
    m = _one(ToolUniverseProvider(_allowlist(), install=_install(tmp_path)))
    assert m.id == f"tooluniverse.{NAME}" and m.kind == "tool" and m.version == "1.5.6"
    assert m.state is LifecycleState.DISCOVERED and m.validate() == []
    # parameter -> inputs, test_examples -> the smoke test, return_schema -> outputs
    assert m.inputs["properties"] == ENTRY["parameter"]["properties"]
    assert m.inputs["required"] == ["accession"]
    assert m.inputs["examples"] == [{"accession": "P04637"}]
    assert smoke_arguments(m) == {"accession": "P04637"}
    assert m.outputs == ENTRY["return_schema"]
    # hosts -> network permissions; the data licence is what the configuration states
    assert m.permissions.network == ("rest.uniprot.org",)
    assert (m.license.spdx, m.license.integration_mode) == ("Apache-2.0", "federated")
    assert m.license.data == "unknown" and "uniprot.org/help/license" in m.license.note
    # bound to the upstream name and to the reviewed bytes
    assert m.runtime.backend == "python"
    assert m.runtime.entrypoint == f"bioagent.providers.tooluniverse:run_{NAME}"
    assert m.provider.commit == f"sha256:{entry_digest(ENTRY)}"
    assert m.provider.source_path == "tooluniverse/data/uniprot_tools.json"
    assert m.requires.python == ("tooluniverse",)
    assert m.validation.last_validated == "2026-10-07"
    registry = ComponentRegistry([m])
    assert registry.get(m.id).state is LifecycleState.REGISTERED


def test_a_licence_the_configuration_states_is_recorded(tmp_path):
    stated = {**ENTRY, "license": "CC-BY-4.0"}
    m = _one(ToolUniverseProvider(_allowlist(stated), install=_install(tmp_path, stated)))
    assert m.license.data == "CC-BY-4.0"


def test_without_the_package_the_reviewed_tools_are_listed_with_nothing_read(tmp_path):
    m = _one(ToolUniverseProvider(_allowlist(), install=None))
    assert m.state is LifecycleState.DISCOVERED and m.inputs == {}
    assert m.requires.python == ("tooluniverse",)       # the resolver reports it missing


# ------------------------------------------------------------------ what is not reviewed
def test_a_tool_outside_the_allowlist_is_never_exposed(tmp_path):
    install = _install(tmp_path)                  # the file also holds UniProt_search
    names = [m.name for m in ToolUniverseProvider(_allowlist(), install=install).discover()]
    assert names == [NAME]
    with pytest.raises(AttributeError):
        getattr(tu, "run_UniProt_search")
    with pytest.raises(AttributeError):
        getattr(tu, "UniProt_get_entry_by_accession")      # only run_<name> is an entry
    with pytest.raises(ToolUniverseCallError) as refused:
        tu.call("UniProt_search", {"query": "TP53"}, allowlist=_allowlist())
    assert refused.value.execution_status is ExecutionStatus.DENIED


def test_a_manifest_forged_for_an_unlisted_tool_cannot_load():
    from bioagent.psh.assembly import default_runtime
    from bioagent.runtime.agentspec import AgentSpec

    forged = ComponentManifest(
        id="tooluniverse.UniProt_search", kind="tool", runtime=RuntimeSpec(
            backend="python", entrypoint="bioagent.providers.tooluniverse:run_UniProt_search"))
    runtime = default_runtime(catalogue=False, public_apis=False, native_tools=False,
                              skills=False, extra_manifests=(forged,))
    result = runtime.invoke(forged.id, spec=AgentSpec(name="t"), query="TP53")
    assert result.status is ExecutionStatus.UNAVAILABLE and not result.executed


_RETYPED = {**ENTRY, "type": "OtherTool"}
_ELSEWHERE = {**ENTRY, "fields": {"endpoint": "https://elsewhere.example.org/x"}}
_KEYED = {**ENTRY, "required_api_keys": ["UNIPROT_KEY"]}


@pytest.mark.parametrize("installed,pinned,version,needle", [
    (ENTRY, ENTRY, "1.6.0",
     "1.6.0 is installed; the allowlist was reviewed against 1.5.6"),
    ({**ENTRY, "description": "edited upstream"}, ENTRY, "1.5.6", "not the reviewed"),
    (UNLISTED, ENTRY, "1.5.6", "holds 0 entries named"),
    # the next three pin the changed entry, so the check that fires is not the digest
    (_RETYPED, _RETYPED, "1.5.6", "implemented by 'OtherTool'"),
    (_ELSEWHERE, _ELSEWHERE, "1.5.6", "contacts ['elsewhere.example.org']"),
    (_KEYED, _KEYED, "1.5.6", "requires API keys"),
])
def test_what_is_installed_but_not_what_was_reviewed_is_quarantined(
        tmp_path, installed, pinned, version, needle):
    install = _install(tmp_path, installed, version=version)
    m = _one(ToolUniverseProvider(_allowlist(pinned), install=install))
    assert m.state is LifecycleState.QUARANTINED and needle in m.blocking_reason
    assert m.inputs == {} and m.validate() == []
    registry = ComponentRegistry([m])
    assert registry.get(m.id).state is LifecycleState.QUARANTINED


# ------------------------------------------------------------------ how calls end
@pytest.mark.parametrize("result,status", [
    ({"status": "success", "data": {"primaryAccession": "P04637"}}, ExecutionStatus.SUCCEEDED),
    ([{"term": "aspirin", "count": 3}], ExecutionStatus.SUCCEEDED),
    ({"status": "error", "error": "UniProt API returned status code: 400",
      "detail": "The 'accession' value has invalid format."}, ExecutionStatus.FAILED),
    ({"status": "error", "error": "Request to UniProt API timed out"},
     ExecutionStatus.TIMEOUT),
    ({"status": "error", "error": "Request to UniProt API failed: HTTPSConnectionPool(host="
      "'rest.uniprot.org', port=443): Max retries exceeded (Caused by ProxyError(...))"},
     ExecutionStatus.UNAVAILABLE),
    ({"status": "error", "error": "Parameter validation failed",
      "error_details": {"type": "ToolValidationError"}}, ExecutionStatus.FAILED),
    ({"status": "error", "error": "Tool 'X' not found even after loading tools",
      "error_details": {"type": "ToolUnavailableError"}}, ExecutionStatus.UNAVAILABLE),
    ({"status": "error", "error": "missing key", "error_details": {"type": "ToolAuthError"}},
     ExecutionStatus.UNAVAILABLE),
    ({"error": "no status, still an error"}, ExecutionStatus.FAILED),
    ([{"error": "openFDA said no"}], ExecutionStatus.FAILED),
    (None, ExecutionStatus.FAILED),
])
def test_a_failure_reported_as_a_value_is_never_a_success(result, status):
    got, reason = classify(result)
    assert got is status
    assert bool(reason) is (status is not ExecutionStatus.SUCCEEDED)
    assert len(reason) <= 330


class _Loader:
    def __init__(self, fn):
        self.fn = fn

    def load(self, cid):
        return self.fn


@pytest.mark.parametrize("raised,status", [
    (ToolUniverseCallError(ExecutionStatus.TIMEOUT, "timed out"), ExecutionStatus.TIMEOUT),
    (ToolUniverseCallError(ExecutionStatus.UNAVAILABLE, "no route"),
     ExecutionStatus.UNAVAILABLE),
    (ToolUniverseCallError(ExecutionStatus.DENIED, "not reviewed"), ExecutionStatus.DENIED),
    (ToolUniverseCallError(ExecutionStatus.SUCCEEDED, "a raise is not a success"),
     ExecutionStatus.FAILED),
    (RuntimeError("plain"), ExecutionStatus.FAILED),
])
def test_the_python_backend_records_the_failure_an_entrypoint_declares(raised, status):
    def entrypoint(**_):
        raise raised

    m = ComponentManifest(id="t.x", kind="tool",
                          runtime=RuntimeSpec(backend="python", entrypoint="m:f"))
    result = PythonBackend(_Loader(entrypoint)).invoke(m)
    assert result.status is status and type(raised).__name__ in result.error


def test_the_components_cross_the_psh_bridge_with_their_hosts_and_schema(tmp_path):
    from bioagent.psh.manifest import bridge_manifest

    m = _one(ToolUniverseProvider(_allowlist(), install=_install(tmp_path)))
    psh = bridge_manifest(m, entrypoint=m.runtime.entrypoint)
    assert psh.id == m.id and psh.allowed_hosts == ("rest.uniprot.org",)
    assert psh.input_schema["required"] == ["accession"]
    assert psh.provenance["data_license"] == "unknown"
    assert psh.provenance["code_license"] == "Apache-2.0"


# ------------------------------------------------------------------ with the package
def test_the_installed_package_is_the_reviewed_one():
    need_module("tooluniverse")
    manifests = list(ToolUniverseProvider().discover())
    assert [m.state for m in manifests] == [LifecycleState.DISCOVERED] * len(manifests), [
        m.blocking_reason for m in manifests]
    assert all(r["drift"] == "" for r in tu.review())


def _plant(tmp_path: Path) -> Path:
    """A ./.tooluniverse holding a Python file that leaves a mark when imported, and a
    JSON file that redefines the reviewed tool."""
    marker = tmp_path / "ran"
    workspace = tmp_path / ".tooluniverse"
    workspace.mkdir()
    (workspace / "planted.py").write_text(
        f"open({str(marker)!r}, 'w').write('imported')\n", encoding="utf-8")
    (workspace / "planted.json").write_text(json.dumps([{**ENTRY, "description": "x"}]),
                                            encoding="utf-8")
    return marker


def test_tooluniverse_1_5_6_imports_the_working_directory_even_when_told_not_to(
        tmp_path, monkeypatch):
    """The upstream behaviour the engine works around. If this starts failing, a release
    has closed the gap and the override in ``_build_engine`` can be reviewed away."""
    need_module("tooluniverse")
    from tooluniverse import ToolUniverse

    marker = _plant(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TOOLUNIVERSE_CACHE_ENABLED", "false")
    monkeypatch.setenv("TOOLUNIVERSE_CACHE_PERSIST", "false")
    engine = ToolUniverse(load_workspace=False, log_level="WARNING")
    engine.load_tools(include_tools=[NAME])
    assert marker.exists()
    assert engine.all_tool_dict[NAME]["description"] == "x"


def test_an_engine_holds_one_tool_and_runs_nothing_from_the_working_directory(
        tmp_path, monkeypatch, capsys):
    """The engine built here imports nothing from ./.tooluniverse, keeps no result cache
    and prints nothing to standard output (the PSH isolated executor's result channel)."""
    need_module("tooluniverse")
    marker = _plant(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(tu, "_ENGINES", {})
    allow = load_allowlist()
    engine = tu._engine(allow.get(NAME), allow)
    assert not marker.exists()
    assert list(engine.all_tool_dict) == [NAME]
    loaded = {k: v for k, v in engine.all_tool_dict[NAME].items()
              if k not in ("source_file", "category", "mcp_annotations")}
    assert entry_digest(loaded) == allow.get(NAME).entry_sha256      # not the planted one
    assert engine.cache_manager.enabled is False and engine.cache_manager.persistent is None
    assert capsys.readouterr().out == ""


def test_a_call_with_bad_arguments_fails_without_reaching_the_network():
    need_module("tooluniverse")
    with pytest.raises(ToolUniverseCallError) as failed:
        tu.call(NAME, {})                                   # no accession
    assert failed.value.execution_status is ExecutionStatus.FAILED
    assert "ToolValidationError" in failed.value.reason


@pytest.mark.integration
def test_uniprot_p04637_live_through_the_runtime():
    """Registry -> resolver -> policy -> python backend -> ToolUniverse -> UniProt."""
    need_module("tooluniverse")
    from bioagent.psh.assembly import default_runtime
    from bioagent.runtime.agentspec import AgentSpec

    manifests = list(ToolUniverseProvider().discover())
    runtime = default_runtime(catalogue=False, public_apis=False, native_tools=False,
                              skills=False, extra_manifests=manifests)
    spec = AgentSpec(name="live", permission_profile="biomedical-research")
    result = runtime.invoke(f"tooluniverse.{NAME}", spec=spec, accession="P04637")
    assert result.status is ExecutionStatus.SUCCEEDED, result.error
    assert result.value["status"] == "success"
    assert result.value["data"]["primaryAccession"] == "P04637"
    assert result.authorization is not None and result.authorization.allowed
