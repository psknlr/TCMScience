"""Reviewed execution environments: GPUs and mounts for containers, interpreters per project.

No container runtime is installed here: the container tests read the argv the backend
builds (``ContainerBackend.command``) and, with the runtime probe and ``subprocess.run``
replaced, the argv ``invoke`` would execute. The interpreter tests run real processes
through wrapper scripts standing in for a provider's virtualenv.
"""

from __future__ import annotations

import json
import subprocess as sp
import sys
import time
from pathlib import Path

import pytest

from bioagent.backends.concrete import ContainerBackend, SubprocessBackend
from bioagent.backends.environments import (EnvironmentConfigError, ExecutionEnvironments,
                                            review_digest, reviewed)
from bioagent.runtime.component import ComponentManifest, Provider, RuntimeSpec
from bioagent.status import ExecutionStatus

PAYLOAD = '{"entrypoint": "fold", "arguments": {"seed": 1}}'


def manifest(cid: str = "structure.tool.boltz") -> ComponentManifest:
    return ComponentManifest(id=cid, kind="tool", provider=Provider(project="boltz"),
                             runtime=RuntimeSpec(backend="container", entrypoint="fold",
                                                 image="example/boltz:2.2"))


def envs(body: dict) -> ExecutionEnvironments:
    return ExecutionEnvironments.parse(reviewed(body, by="a reviewer", on="2026-10-07"))


def profile_body(tmp_path: Path, **profile) -> dict:
    (tmp_path / "msa").mkdir(exist_ok=True)
    (tmp_path / "out").mkdir(exist_ok=True)
    base = {"gpus": {"devices": ["0", "1"]}, "data": {"msa": str(tmp_path / "msa")},
            "output": str(tmp_path / "out")}
    base.update(profile)
    return {"containers": {"structure.tool.boltz": base}}


# ------------------------------------------------------------------- the review
def test_a_configuration_is_used_only_as_it_was_reviewed(tmp_path):
    body = profile_body(tmp_path)
    doc = reviewed(body, by="a reviewer", on="2026-10-07")
    accepted = ExecutionEnvironments.parse(doc)
    assert accepted.digest == review_digest(body) == doc["review"]["sha256"]
    assert accepted.describe().startswith(f"reviewed environment {accepted.digest[:12]} by "
                                          "a reviewer on 2026-10-07")
    doc["containers"]["structure.tool.boltz"]["data"]["etc"] = "/etc"   # added afterwards
    with pytest.raises(EnvironmentConfigError, match="changed since it was reviewed"):
        ExecutionEnvironments.parse(doc)
    with pytest.raises(EnvironmentConfigError, match="carries no review"):
        ExecutionEnvironments.parse(body)
    path = tmp_path / "environments.json"
    path.write_text(json.dumps(reviewed(body, by="r", on="d")))
    assert ExecutionEnvironments.load(path).source == str(path)


@pytest.mark.parametrize("profile,words", [
    ({"output": "relative/out"}, "is not an absolute path"),
    ({"data": {"msa": "/srv/a,b"}}, "contains a comma"),
    ({"gpus": {"count": 1, "devices": ["0"]}}, "exactly one of 'count' and 'devices'"),
    ({"gpus": {"count": 0}}, "positive integer"),
    ({"network": "host"}, "network is one of ('none', 'bridge')"),
    ({"privileged": True}, "unknown keys ['privileged']"),
])
def test_a_profile_that_widens_or_blurs_what_a_container_sees_is_refused(tmp_path, profile,
                                                                        words):
    with pytest.raises(EnvironmentConfigError) as refused:
        envs(profile_body(tmp_path, **profile))
    assert words in str(refused.value)


def test_the_writable_directory_may_not_overlap_the_read_only_data(tmp_path):
    body = profile_body(tmp_path, output=str(tmp_path / "msa" / "results"))
    with pytest.raises(EnvironmentConfigError, match="overlaps data root 'msa'"):
        envs(body)


# ------------------------------------------------------------------ containers
def test_a_component_no_profile_names_gets_no_gpu_no_mount_and_no_network(tmp_path):
    backend = ContainerBackend(envs(profile_body(tmp_path)))
    assert backend.command(manifest("other.tool.x"), "docker", {"seed": 1}) == [
        "docker", "run", "--rm", "--network", "none",
        "--env", f"BIOAGENT_INVOCATION={PAYLOAD}", "example/boltz:2.2", "fold",
        "--seed", "1"]
    assert ContainerBackend().command(manifest(), "docker", {"seed": 1}) == \
        backend.command(manifest("other.tool.x"), "docker", {"seed": 1})


def test_a_reviewed_profile_adds_exactly_its_gpus_and_mounts(tmp_path):
    backend = ContainerBackend(envs(profile_body(tmp_path)))
    assert backend.command(manifest(), "/usr/bin/docker", {"seed": 1}) == [
        "/usr/bin/docker", "run", "--rm", "--network", "none",
        "--gpus", '"device=0,1"',
        "--mount", f"type=bind,source={tmp_path / 'msa'},target=/data/msa,readonly=true",
        "--mount", f"type=bind,source={tmp_path / 'out'},target=/out",
        "--env", "BIOAGENT_OUTPUT_DIR=/out",
        "--env", f"BIOAGENT_INVOCATION={PAYLOAD}", "example/boltz:2.2", "fold", "--seed", "1"]


def test_gpus_are_requested_in_each_runtimes_own_terms(tmp_path):
    counted = ContainerBackend(envs(profile_body(tmp_path, gpus={"count": 2})))
    assert counted.command(manifest(), "nerdctl", {})[5:7] == ["--gpus", "count=2"]
    devices = ContainerBackend(envs(profile_body(tmp_path)))
    assert devices.command(manifest(), "podman", {})[5:9] == [
        "--device", "nvidia.com/gpu=0", "--device", "nvidia.com/gpu=1"]
    with pytest.raises(EnvironmentConfigError, match="podman selects GPUs by CDI device"):
        counted.command(manifest(), "podman", {})
    bridged = ContainerBackend(envs(profile_body(tmp_path, network="bridge")))
    assert bridged.command(manifest(), "docker", {})[3:5] == ["--network", "bridge"]


@pytest.fixture
def usable_runtime(monkeypatch):
    from bioagent.runtime import registry as registry_module

    monkeypatch.setattr(registry_module, "_CONTAINER_PROBE",
                        registry_module.RuntimeProbe("docker", True, ""))
    monkeypatch.setattr(registry_module, "_CONTAINER_PROBE_AT", time.monotonic())
    calls: list[list[str]] = []

    def answer(stderr: str = "", code: int = 0, stdout: str = '{"ok": true}'):
        def run(cmd, **kwargs):
            calls.append(cmd)
            return sp.CompletedProcess(cmd, code, stdout, stderr)
        monkeypatch.setattr("bioagent.backends.concrete.subprocess.run", run)

    answer()
    return calls, answer


def test_invoke_runs_the_command_it_shows_and_records_the_grants(tmp_path, usable_runtime):
    calls, _ = usable_runtime
    backend = ContainerBackend(envs(profile_body(tmp_path)))
    result = backend.invoke(manifest(), seed=1, timeout_s=5)
    assert result.status is ExecutionStatus.SUCCEEDED and result.value == {"ok": True}
    assert calls == [backend.command(manifest(), "docker", {"seed": 1})]
    assert result.metadata["gpus"] == {"devices": ["0", "1"]}
    assert result.metadata["mounts"][0] == {"source": str(tmp_path / "msa"),
                                            "target": "/data/msa", "read_only": True}
    assert result.metadata["environment"].startswith("reviewed environment")


def test_a_profile_this_machine_cannot_honour_runs_nothing(tmp_path, usable_runtime):
    calls, answer = usable_runtime
    body = profile_body(tmp_path)
    body["containers"]["structure.tool.boltz"]["data"]["pdb"] = str(tmp_path / "absent")
    result = ContainerBackend(envs(body)).invoke(manifest(), timeout_s=5)
    assert result.status is ExecutionStatus.UNAVAILABLE and calls == []
    assert "data root 'pdb'" in result.error and "is not a directory" in result.error
    answer(code=125, stdout="", stderr='docker: Error response from daemon: could not select '
                                       'device driver "" with capabilities: [[gpu]].')
    gpu = ContainerBackend(envs(profile_body(tmp_path))).invoke(manifest(), timeout_s=5)
    assert gpu.status is ExecutionStatus.UNAVAILABLE and not gpu.status.executed
    assert "could not start the container" in gpu.error


# --------------------------------------------------------------- interpreters
def wrapper(tmp_path: Path, name: str = "venv-python", body: str = "") -> Path:
    """A stand-in for a provider's virtualenv interpreter: a script that runs this one."""
    path = tmp_path / name
    path.write_text(body or f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
    path.chmod(0o755)
    return path


CODE = ("import json, sys; print(json.dumps({'isolated': sys.flags.isolated, "
        "'prefix': sys.prefix}))")


def sub_manifest(project: str = "ProteinMPNN") -> ComponentManifest:
    return ComponentManifest(id="p.tool.design", kind="tool",
                             runtime=RuntimeSpec(backend="subprocess"),
                             provider=Provider(project=project))


def test_a_reviewed_project_runs_in_its_own_interpreter_isolated(tmp_path):
    python = wrapper(tmp_path)
    backend = SubprocessBackend(environments=envs(
        {"interpreters": {"ProteinMPNN": {"python": str(python), "root": str(tmp_path)}}}))
    result = backend.invoke(sub_manifest(), code=CODE)
    assert result.status is ExecutionStatus.SUCCEEDED, result.error
    assert result.value["isolated"] == 1, "-I is kept"
    assert result.metadata["interpreter"] == str(python)
    assert result.metadata["python"] == sys.version.split()[0]
    assert result.metadata["interpreter_source"].startswith("reviewed environment")


def test_a_prefix_names_the_environments_own_python(tmp_path):
    prefix = tmp_path / "conda-env"
    (prefix / "bin").mkdir(parents=True)
    wrapper(prefix / "bin", "python")
    chosen = envs({"interpreters": {"ProteinMPNN": {"prefix": str(prefix)}}})
    assert chosen.interpreter("ProteinMPNN").python == prefix / "bin" / "python"
    result = SubprocessBackend({"ProteinMPNN": tmp_path}, environments=chosen).invoke(
        sub_manifest(), code=CODE)
    assert result.status is ExecutionStatus.SUCCEEDED


def test_a_missing_or_broken_interpreter_is_refused_never_replaced(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("nothing may run when the reviewed interpreter is unusable")

    missing = envs({"interpreters": {"ProteinMPNN": {"python": str(tmp_path / "gone"),
                                                     "root": str(tmp_path)}}})
    monkeypatch.setattr("bioagent.backends.concrete.subprocess.run", forbidden)
    result = SubprocessBackend(environments=missing).invoke(sub_manifest(), code=CODE)
    assert result.status is ExecutionStatus.UNAVAILABLE
    assert "does not exist on this machine" in result.error
    monkeypatch.undo()
    broken = wrapper(tmp_path, "broken",
                     "#!/bin/sh\necho 'libpython: not found' >&2\nexit 1\n")
    chosen = envs({"interpreters": {"ProteinMPNN": {"python": str(broken),
                                                    "root": str(tmp_path)}}})
    result = SubprocessBackend(environments=chosen).invoke(sub_manifest(), code=CODE)
    assert result.status is ExecutionStatus.UNAVAILABLE and not result.status.executed
    assert "did not start: libpython: not found" in result.error


def test_a_project_no_review_names_runs_in_the_harness_interpreter_and_says_so(tmp_path):
    chosen = envs({"interpreters": {"ProteinMPNN": {"python": str(wrapper(tmp_path))}}})
    result = SubprocessBackend({"Biomni": tmp_path}, environments=chosen).invoke(
        sub_manifest("Biomni"), code=CODE)
    assert result.status is ExecutionStatus.SUCCEEDED
    assert result.metadata["interpreter"] == sys.executable
    assert "no reviewed environment names this project" in result.metadata[
        "interpreter_source"]


def test_the_default_runtime_runs_under_the_reviewed_configuration_it_is_given(
        tmp_path, monkeypatch):
    from bioagent.psh.assembly import default_runtime

    def runtime():
        return default_runtime(catalogue=False, public_apis=False, native_tools=False,
                               skills=False, data_lake=tmp_path)

    monkeypatch.delenv("BIOAGENT_ENVIRONMENTS", raising=False)
    assert runtime().backends.get("container").environments is None
    doc = reviewed(profile_body(tmp_path), by="a reviewer", on="2026-10-07")
    path = tmp_path / "environments.json"
    path.write_text(json.dumps(doc))
    monkeypatch.setenv("BIOAGENT_ENVIRONMENTS", str(path))
    backends = runtime().backends
    assert backends.get("container").environments.digest == doc["review"]["sha256"]
    assert backends.get("subprocess").environments is backends.get("container").environments
    doc["containers"]["structure.tool.boltz"]["network"] = "bridge"
    path.write_text(json.dumps(doc))
    with pytest.raises(EnvironmentConfigError, match="changed since it was reviewed"):
        runtime()
