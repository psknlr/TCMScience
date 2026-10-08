"""Device detection, selection and the environment and options a device gives a job."""

from __future__ import annotations

import json
import subprocess

import pytest

from tcmstudio import devices as dev


def runner_for(outputs: dict[str, str], *, rc: int = 0):
    """A stand-in for subprocess.run answering by the command's first word."""
    calls = []

    def run(cmd, timeout=5.0):
        calls.append(cmd)
        name = cmd[0].rsplit("/", 1)[-1]
        if name not in outputs:
            raise FileNotFoundError(name)
        return subprocess.CompletedProcess(cmd, rc, stdout=outputs[name], stderr="")
    run.calls = calls
    return run


def which_for(*names):
    return lambda n: f"/usr/bin/{n}" if n in names else None


def test_nvidia_smi_is_parsed():
    out = ("0, NVIDIA GeForce RTX 4090, 24564, 550.54.14\n"
           "1, NVIDIA A100-SXM4-80GB, 81920, 550.54.14\n")
    run = runner_for({"nvidia-smi": out})
    gpus, note = dev.nvidia_gpus(run, which_for("nvidia-smi"))
    assert note is None
    assert gpus == [
        {"id": "cuda:0", "kind": "cuda", "name": "NVIDIA GeForce RTX 4090", "memory_gb": 24.0,
         "driver": "550.54.14", "available": True},
        {"id": "cuda:1", "kind": "cuda", "name": "NVIDIA A100-SXM4-80GB", "memory_gb": 80.0,
         "driver": "550.54.14", "available": True}]
    assert "--query-gpu=index,name,memory.total,driver_version" in run.calls[0]


def test_nvidia_smi_failure_is_a_note_not_a_crash():
    run = runner_for({"nvidia-smi": "NVIDIA-SMI has failed"}, rc=9)
    gpus, note = dev.nvidia_gpus(run, which_for("nvidia-smi"))
    assert gpus == [] and "nvidia-smi failed" in note
    assert dev.nvidia_gpus(run, which_for()) == ([], None)


def test_rocm_smi_is_parsed():
    doc = {"card0": {"Card Series": "Radeon RX 7900 XTX", "VRAM Total Memory (B)": str(24 * 2**30)},
           "card1": {"Card model": "0x74a1", "VRAM Total Memory (B)": "not-a-number"},
           "system": {"Driver version": "6.7.0"}}
    gpus, note = dev.rocm_gpus(runner_for({"rocm-smi": json.dumps(doc)}), which_for("rocm-smi"))
    assert note is None
    assert gpus[0] == {"id": "rocm:0", "kind": "rocm", "name": "Radeon RX 7900 XTX",
                       "memory_gb": 24.0, "driver": "6.7.0", "available": True}
    assert gpus[1]["id"] == "rocm:1" and gpus[1]["memory_gb"] is None


def test_probe_lists_the_cpu_first_and_caches():
    run = runner_for({"nvidia-smi": "0, NVIDIA L4, 23034, 535.1\n"})
    probe = dev.DeviceProbe(run=run, which=which_for("nvidia-smi"), torch=False)
    devices = probe.devices()
    assert devices[0]["id"] == "cpu" and devices[0]["available"] is True
    assert devices[0]["cores"] >= 1
    assert devices[1]["id"] == "cuda:0" and devices[1]["memory_gb"] == 22.5
    calls = len(run.calls)
    probe.devices()
    assert len(run.calls) == calls                      # cached
    probe.devices(refresh=True)
    assert len(run.calls) > calls


def test_torch_facts_are_merged():
    gpus = [{"id": "cuda:0", "kind": "cuda", "name": "X", "available": True}]
    dev._merge_torch(gpus, {"torch": "2.5.0", "cuda": False, "hip": None, "mps": False})
    assert "does not see this GPU" in gpus[0]["note"]
    gpus = []
    dev._merge_torch(gpus, {"torch": "2.5.0", "cuda": True, "hip": None, "mps": False,
                            "devices": [{"index": 0, "name": "T4", "memory_gb": 14.6}]})
    assert gpus[0]["id"] == "cuda:0" and gpus[0]["name"] == "T4"
    gpus = []
    dev._merge_torch(gpus, {"torch": "2.5.0+rocm6", "cuda": True, "hip": "6.0", "mps": False,
                            "devices": [{"index": 0, "name": "MI300X", "memory_gb": 192}]})
    assert gpus[0]["id"] == "rocm:0"
    mps = [{"id": "mps", "kind": "mps", "name": "Apple M3 GPU", "available": True}]
    dev._merge_torch(mps, {"torch": "2.5.0", "cuda": False, "mps": False})
    assert mps[0]["available"] is False
    dev._merge_torch(gpus, None)                        # nothing to merge


def test_torch_probe_is_skipped_without_torch(monkeypatch):
    monkeypatch.setattr(dev.importlib.util, "find_spec", lambda name: None)
    called = []
    assert dev.torch_probe(run=lambda *a, **k: called.append(a)) is None
    assert called == []


@pytest.mark.parametrize("selected,gpu_capable,expected", [
    ("auto", True, "cuda:0"), ("auto", False, "cpu"), ("cpu", True, "cpu"),
    ("cuda:1", True, "cuda:1"), ("mps", True, "mps")])
def test_resolve(selected, gpu_capable, expected):
    devices = [{"id": "cpu", "kind": "cpu", "available": True},
               {"id": "mps", "kind": "mps", "available": True},
               {"id": "cuda:1", "kind": "cuda", "available": True},
               {"id": "cuda:0", "kind": "cuda", "available": True}]
    assert dev.resolve(selected, devices, gpu_capable=gpu_capable) == (expected, None)


def test_resolve_falls_back_to_the_cpu_with_a_note():
    devices = [{"id": "cpu", "kind": "cpu", "available": True},
               {"id": "cuda:0", "kind": "cuda", "available": False}]
    assert dev.resolve("auto", devices) == ("cpu", None)
    device, note = dev.resolve("cuda:0", devices)
    assert device == "cpu" and "not available" in note


def test_job_environment_per_device():
    cpu = dev.job_env("cpu", 3)
    assert cpu["CUDA_VISIBLE_DEVICES"] == "" and cpu["HIP_VISIBLE_DEVICES"] == ""
    assert all(cpu[v] == "3" for v in dev.THREAD_VARS)
    assert dev.job_env("cuda:2", 1)["CUDA_VISIBLE_DEVICES"] == "2"
    rocm = dev.job_env("rocm:1", 1)
    assert rocm["HIP_VISIBLE_DEVICES"] == "1" and "CUDA_VISIBLE_DEVICES" not in rocm
    mps = dev.job_env("mps", 8)
    assert mps["CUDA_VISIBLE_DEVICES"] == "" and mps["PYTORCH_ENABLE_MPS_FALLBACK"] == "1"
    assert dev.job_env("cpu", 0)["OMP_NUM_THREADS"] == "1"


def test_engine_options_per_device():
    assert dev.engine_options("cpu") == {"boltz": {"accelerator": "cpu"},
                                         "chai": {"device": "cpu"},
                                         "openmm": {"platform": "CPU"}}
    cuda = dev.engine_options("cuda:3")
    # the visible GPU is index 0 inside the job (CUDA_VISIBLE_DEVICES=3)
    assert cuda["chai"]["device"] == "cuda:0" and cuda["openmm"]["platform"] == "CUDA"
    assert cuda["boltz"]["accelerator"] == "gpu"
    assert dev.engine_options("rocm:0")["openmm"]["platform"] == "HIP"
    assert dev.engine_options("mps")["chai"]["device"] == "mps"


def test_engines_report_without_importing_anything():
    import sys
    before = set(sys.modules)
    engines = dev.engines(refresh=True, which=which_for())
    assert {e["id"] for e in engines} >= {"omics-builtin", "pydeseq2", "salmon", "vina",
                                          "esmfold", "admet", "pyarrow"}
    salmon = next(e for e in engines if e["id"] == "salmon")
    assert salmon["installed"] is False and salmon["missing"] == ["salmon"]
    assert salmon["install"].startswith("conda install")
    assert next(e for e in engines if e["id"] == "omics-builtin")["installed"] is True
    heavy = {"torch", "rdkit", "transformers", "scanpy", "vina"}
    assert not (heavy & (set(sys.modules) - before))


def test_cpu_and_memory_facts():
    cpu = dev.cpu_info()
    assert cpu["cores"] >= 1
    mem = dev.memory_gb()
    assert mem is None or mem > 0
    usage = dev.DeviceProbe(torch=False, which=which_for()).usage()
    assert "memory_available_gb" in usage and "gpus" in usage
