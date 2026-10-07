"""What this machine can compute with: CPU, memory, GPUs, and the engines the job kinds use.

Nothing in bioagent detects devices, so the runner does it here:

* the CPU model and core count, and the memory (``/proc``, ``sysctl`` or Windows' API);
* NVIDIA GPUs from ``nvidia-smi --query-gpu``, AMD GPUs from ``rocm-smi``, and the Apple GPU
  (Metal, ``mps``) on Apple Silicon;
* what PyTorch sees (CUDA, ROCm, MPS), probed in a child interpreter so torch is never
  imported into the runner, and only once something asks.

Results are cached; ``refresh`` probes again. The device the user selects (``auto``, ``cpu``,
``cuda:N``, ``rocm:N``, ``mps``) becomes environment variables for a job
(``CUDA_VISIBLE_DEVICES=""`` forces the CPU) plus the thread variables, and the engine
options Boltz, Chai-1 and OpenMM take.
"""

from __future__ import annotations

import contextlib
import csv
import importlib.metadata
import importlib.util
import io
import json
import os
import platform
import shutil
import subprocess
import sys
import threading
import time
from typing import Any, Callable

__all__ = ["DeviceProbe", "cpu_info", "memory_gb", "memory_available_gb", "nvidia_gpus",
           "rocm_gpus", "apple_gpu", "torch_probe", "resolve", "job_env", "engine_options",
           "engines", "THREAD_VARS"]

THREAD_VARS = ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS",
               "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "OPENMM_CPU_THREADS")
Runner = Callable[..., "subprocess.CompletedProcess[str]"]


def _run(cmd: list[str], timeout: float = 5.0) -> "subprocess.CompletedProcess[str]":
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,  # noqa: S603
                          stdin=subprocess.DEVNULL, check=False)


# ----------------------------------------------------------------------------- CPU / memory

def cpu_info(run: Runner = _run) -> dict[str, Any]:
    model = None
    system = platform.system()
    try:
        if system == "Linux":
            with open("/proc/cpuinfo", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    key, _, value = line.partition(":")
                    if key.strip() in ("model name", "Hardware", "Processor", "cpu model"):
                        model = value.strip() or None
                        if model:
                            break
        elif system == "Darwin":
            out = run(["sysctl", "-n", "machdep.cpu.brand_string"], timeout=3)
            model = out.stdout.strip() or None
        elif system == "Windows":
            model = platform.processor() or None
    except (OSError, subprocess.SubprocessError):
        model = None
    return {"cores": os.cpu_count() or 1, "model": model or platform.processor() or None,
            "arch": platform.machine() or None}


def _meminfo() -> dict[str, int]:
    out: dict[str, int] = {}
    with open("/proc/meminfo", encoding="utf-8") as fh:
        for line in fh:
            key, _, rest = line.partition(":")
            parts = rest.split()
            if parts and parts[0].isdigit():
                out[key.strip()] = int(parts[0]) * 1024
    return out


def memory_gb(run: Runner = _run) -> float | None:
    try:
        system = platform.system()
        if system == "Linux":
            total = _meminfo().get("MemTotal")
            return round(total / 2**30, 1) if total else None
        if system == "Darwin":
            out = run(["sysctl", "-n", "hw.memsize"], timeout=3)
            return round(int(out.stdout.strip()) / 2**30, 1)
        if system == "Windows":                                 # pragma: no cover - not CI
            import ctypes

            class _Status(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong),
                            ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong),
                            ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong),
                            ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("sullAvailExtendedVirtual", ctypes.c_ulonglong)]
            status = _Status()
            status.dwLength = ctypes.sizeof(_Status)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
            return round(status.ullTotalPhys / 2**30, 1)
    except (OSError, ValueError, subprocess.SubprocessError, AttributeError):
        return None
    return None


def memory_available_gb() -> float | None:
    try:
        if platform.system() == "Linux":
            avail = _meminfo().get("MemAvailable")
            return round(avail / 2**30, 1) if avail else None
    except (OSError, ValueError):
        return None
    return None


# ---------------------------------------------------------------------------------- GPUs

def _gb_from_mib(value: str) -> float | None:
    try:
        return round(float(value) / 1024, 1)
    except (TypeError, ValueError):
        return None


def nvidia_gpus(run: Runner = _run, which: Callable[[str], str | None] = shutil.which
                ) -> tuple[list[dict[str, Any]], str | None]:
    """NVIDIA GPUs as devices ``cuda:<index>``, and a note when nvidia-smi failed."""
    exe = which("nvidia-smi")
    if not exe:
        return [], None
    try:
        out = run([exe, "--query-gpu=index,name,memory.total,driver_version",
                   "--format=csv,noheader,nounits"], timeout=8)
    except (OSError, subprocess.SubprocessError) as exc:
        return [], f"nvidia-smi could not be run: {exc}"
    if out.returncode != 0:
        why = (out.stderr or out.stdout or "").strip().splitlines()
        return [], f"nvidia-smi failed: {why[-1][:200] if why else f'exit {out.returncode}'}"
    devices = []
    for row in csv.reader(io.StringIO(out.stdout)):
        row = [c.strip() for c in row]
        if len(row) < 4 or not row[0].isdigit():
            continue
        devices.append({"id": f"cuda:{row[0]}", "kind": "cuda", "name": row[1],
                        "memory_gb": _gb_from_mib(row[2]), "driver": row[3] or None,
                        "available": True})
    return devices, None


def rocm_gpus(run: Runner = _run, which: Callable[[str], str | None] = shutil.which
              ) -> tuple[list[dict[str, Any]], str | None]:
    """AMD GPUs (ROCm) as devices ``rocm:<index>``."""
    exe = which("rocm-smi")
    if not exe:
        return [], None
    try:
        out = run([exe, "--showproductname", "--showmeminfo", "vram", "--showdriverversion",
                   "--json"], timeout=8)
        doc = json.loads(out.stdout or "{}")
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        return [], f"rocm-smi could not be read: {exc}"
    driver = None
    system = doc.get("system") if isinstance(doc, dict) else None
    if isinstance(system, dict):
        driver = next((str(v) for k, v in system.items() if "driver" in k.lower()), None)
    devices = []
    for key, card in sorted(doc.items()) if isinstance(doc, dict) else ():
        if not key.lower().startswith("card") or not isinstance(card, dict):
            continue
        index = key[4:]
        if not index.isdigit():
            continue
        name = next((str(card[k]) for k in ("Card Series", "Card series", "Card model",
                                            "Marketing Name", "Device Name") if card.get(k)),
                    "AMD GPU")
        total = next((v for k, v in card.items() if "vram total memory" in k.lower()), None)
        try:
            mem = round(int(total) / 2**30, 1) if total is not None else None
        except (TypeError, ValueError):
            mem = None
        devices.append({"id": f"rocm:{index}", "kind": "rocm", "name": name, "memory_gb": mem,
                        "driver": driver, "available": True})
    return devices, None


def apple_gpu(run: Runner = _run) -> dict[str, Any] | None:
    if platform.system() != "Darwin" or platform.machine() not in ("arm64", "aarch64"):
        return None
    chip = None
    with contextlib.suppress(OSError, subprocess.SubprocessError):
        chip = run(["sysctl", "-n", "machdep.cpu.brand_string"], timeout=3).stdout.strip()
    return {"id": "mps", "kind": "mps", "name": f"{chip or 'Apple Silicon'} GPU (Metal)",
            "memory_gb": memory_gb(run), "available": True,
            "note": "unified memory, shared with the CPU"}


_TORCH_PROBE = r"""
import json, sys
info = {"torch": None}
try:
    import torch
except Exception as exc:
    info["error"] = "%s: %s" % (type(exc).__name__, exc)
else:
    info["torch"] = torch.__version__
    info["hip"] = getattr(torch.version, "hip", None)
    info["cuda_build"] = getattr(torch.version, "cuda", None)
    try:
        info["cuda"] = bool(torch.cuda.is_available())
        info["devices"] = [{"index": i, "name": torch.cuda.get_device_name(i),
                            "memory_gb": round(torch.cuda.get_device_properties(i).total_memory / 2**30, 1)}
                           for i in range(torch.cuda.device_count())] if info["cuda"] else []
    except Exception as exc:
        info["cuda"], info["devices"] = False, []
        info["cuda_error"] = str(exc)[:200]
    try:
        mps = getattr(torch.backends, "mps", None)
        info["mps"] = bool(mps and mps.is_available())
    except Exception:
        info["mps"] = False
print(json.dumps(info))
"""


def torch_probe(timeout: float = 90.0, run: Runner = _run) -> dict[str, Any] | None:
    """What PyTorch in this Python sees, from a child interpreter. None when torch is not
    installed (nothing is started then)."""
    try:
        if importlib.util.find_spec("torch") is None:
            return None
    except (ImportError, ValueError):
        return None
    try:
        out = run([sys.executable, "-I", "-c", _TORCH_PROBE], timeout=timeout)
        return json.loads(out.stdout.strip().splitlines()[-1])
    except (OSError, subprocess.SubprocessError, ValueError, IndexError) as exc:
        return {"torch": None, "error": f"the PyTorch probe failed: {exc}"}


# ------------------------------------------------------------------------------ the probe

def _merge_torch(devices: list[dict[str, Any]], info: dict[str, Any] | None) -> None:
    if not info or not info.get("torch"):
        return
    kind = "rocm" if info.get("hip") else "cuda"
    have = {d["id"] for d in devices}
    if info.get("cuda"):
        for d in info.get("devices") or ():
            dev_id = f"{kind}:{d.get('index')}"
            if dev_id not in have:
                devices.append({"id": dev_id, "kind": kind, "name": d.get("name"),
                                "memory_gb": d.get("memory_gb"), "available": True,
                                "note": f"seen by PyTorch {info['torch']}"})
    else:
        for d in devices:
            if d["kind"] in ("cuda", "rocm") and "note" not in d:
                d["note"] = (f"PyTorch {info['torch']} in this Python does not see this GPU "
                             "(a CPU build?); jobs that use torch will run on the CPU")
    for d in devices:
        if d["kind"] == "mps":
            if info.get("mps"):
                d["note"] = f"{d.get('note', '')}; PyTorch {info['torch']} can use it".lstrip("; ")
            else:
                d["available"] = False
                d["note"] = f"PyTorch {info['torch']} here cannot use Metal (MPS)"


class DeviceProbe:
    """Cached device facts. The quick part (nvidia-smi, rocm-smi, platform) runs on first
    use; the PyTorch part runs in the background, once, and is merged when it is ready."""

    def __init__(self, *, run: Runner = _run, which: Callable[[str], str | None] = shutil.which,
                 torch: bool = True) -> None:
        self._run = run
        self._which = which
        self._torch_enabled = torch
        self._lock = threading.Lock()
        self._quick: dict[str, Any] | None = None
        self._torch: dict[str, Any] | None = None
        self._torch_state = "idle"                # idle | running | done
        self._usage_at = 0.0
        self._usage: dict[str, Any] = {}

    def _quick_probe(self) -> dict[str, Any]:
        cpu = cpu_info(self._run)
        ram = memory_gb(self._run)
        notes: list[str] = []
        nvidia, note = nvidia_gpus(self._run, self._which)
        if note:
            notes.append(note)
        rocm, note = rocm_gpus(self._run, self._which)
        if note:
            notes.append(note)
        gpus = nvidia + rocm
        apple = apple_gpu(self._run)
        if apple:
            gpus.append(apple)
        return {"cpu": cpu, "memory_gb": ram, "gpus": gpus, "notes": notes,
                "probed_at": time.time()}

    def start_torch_probe(self) -> None:
        """Probe PyTorch in a background thread (a child interpreter imports it)."""
        with self._lock:
            if not self._torch_enabled or self._torch_state != "idle":
                return
            self._torch_state = "running"

        def work() -> None:
            info = torch_probe(run=self._run)
            with self._lock:
                self._torch = info
                self._torch_state = "done"

        threading.Thread(target=work, name="tcmstudio-torch-probe", daemon=True).start()

    def facts(self, refresh: bool = False) -> dict[str, Any]:
        with self._lock:
            if self._quick is None or refresh:
                self._quick = self._quick_probe()
                if refresh and self._torch_state == "done":
                    self._torch_state = "idle"
            quick = json.loads(json.dumps(self._quick))
            torch_info = self._torch
            torch_state = self._torch_state
        if torch_state == "idle":
            self.start_torch_probe()
        cpu = quick["cpu"]
        devices = [{"id": "cpu", "kind": "cpu", "name": cpu.get("model") or "CPU",
                    "memory_gb": quick["memory_gb"], "cores": cpu.get("cores"),
                    "available": True}]
        gpus = quick["gpus"]
        _merge_torch(gpus, torch_info)
        devices += gpus
        return {"cpu": cpu, "memory_gb": quick["memory_gb"], "devices": devices,
                "torch": (torch_info or {}).get("torch") if torch_info else None,
                "torch_probe": torch_state if torch_info is None else "done",
                "notes": quick["notes"] + ([torch_info["error"]] if torch_info and
                                           torch_info.get("error") else [])}

    def devices(self, refresh: bool = False) -> list[dict[str, Any]]:
        return self.facts(refresh)["devices"]

    def usage(self) -> dict[str, Any]:
        """Load and free memory now (cheap; GPU memory at most every 5 s)."""
        out: dict[str, Any] = {"memory_available_gb": memory_available_gb()}
        try:
            out["load_1m"] = round(os.getloadavg()[0], 2)
        except (OSError, AttributeError):
            out["load_1m"] = None
        now = time.monotonic()
        with self._lock:
            stale = now - self._usage_at > 5
        if stale:
            gpu: dict[str, Any] = {}
            exe = self._which("nvidia-smi")
            if exe:
                try:
                    res = self._run([exe, "--query-gpu=index,memory.used,utilization.gpu",
                                     "--format=csv,noheader,nounits"], timeout=5)
                    for row in csv.reader(io.StringIO(res.stdout)):
                        row = [c.strip() for c in row]
                        if len(row) >= 3 and row[0].isdigit():
                            gpu[f"cuda:{row[0]}"] = {"memory_used_gb": _gb_from_mib(row[1]),
                                                     "utilization": _num(row[2])}
                except (OSError, subprocess.SubprocessError):
                    pass
            with self._lock:
                self._usage, self._usage_at = gpu, now
        with self._lock:
            out["gpus"] = dict(self._usage)
        return out


def _num(value: str) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------------ selection and mapping

_PREFERENCE = {"cuda": 0, "rocm": 1, "mps": 2}


def resolve(selected: str, devices: list[dict[str, Any]], *, gpu_capable: bool = True
            ) -> tuple[str, str | None]:
    """The device a job uses, and a note when the selection could not be honoured.

    A kind that cannot use a GPU always runs on the CPU. ``auto`` takes the first available
    GPU (CUDA, then ROCm, then Apple's), else the CPU."""
    if not gpu_capable:
        return "cpu", None
    available = {d["id"]: d for d in devices if d.get("available")}
    if selected in ("", None, "auto"):
        gpus = sorted((d for d in available.values() if d["kind"] in _PREFERENCE),
                      key=lambda d: (_PREFERENCE[d["kind"]], d["id"]))
        return (gpus[0]["id"], None) if gpus else ("cpu", None)
    if selected in available:
        return selected, None
    if selected == "cpu":
        return "cpu", None
    return "cpu", f"the selected device {selected} is not available here; the job runs on the CPU"


def job_env(device: str, threads: int) -> dict[str, str]:
    """Environment for a job on ``device`` with ``threads`` CPU threads.

    The thread variables keep torch, BLAS and OpenMM from starting one thread per core on
    top of whatever else runs. ``CUDA_VISIBLE_DEVICES=""`` hides every NVIDIA GPU, so a CPU
    job never touches one; ``cuda:N`` shows only GPU N (it is ``cuda:0`` inside the job)."""
    n = str(max(1, int(threads)))
    env = {name: n for name in THREAD_VARS}
    kind, _, index = device.partition(":")
    if kind == "cuda":
        env["CUDA_VISIBLE_DEVICES"] = index or "0"
    elif kind == "rocm":
        # HIP honours HIP_VISIBLE_DEVICES; CUDA_VISIBLE_DEVICES is left to the user's own value
        env["HIP_VISIBLE_DEVICES"] = index or "0"
    elif kind == "mps":
        env["CUDA_VISIBLE_DEVICES"] = ""
        env["PYTORCH_ENABLE_MPS_FALLBACK"] = "1"
    else:
        env["CUDA_VISIBLE_DEVICES"] = ""
        env["HIP_VISIBLE_DEVICES"] = ""
    return env


def engine_options(device: str) -> dict[str, dict[str, str]]:
    """The structure engines' own device options for ``device``: Boltz takes an
    ``accelerator``, Chai-1 a torch ``device`` (the visible GPU is index 0 inside a job),
    OpenMM a ``platform``; ProteinMPNN, ESMFold and ColabFold follow
    ``CUDA_VISIBLE_DEVICES``."""
    kind = device.partition(":")[0]
    if kind == "cuda":
        return {"boltz": {"accelerator": "gpu"}, "chai": {"device": "cuda:0"},
                "openmm": {"platform": "CUDA"}}
    if kind == "rocm":
        return {"boltz": {"accelerator": "gpu"}, "chai": {"device": "cuda:0"},
                "openmm": {"platform": "HIP"}}
    if kind == "mps":
        return {"boltz": {"accelerator": "gpu"}, "chai": {"device": "mps"},
                "openmm": {"platform": "OpenCL"}}
    return {"boltz": {"accelerator": "cpu"}, "chai": {"device": "cpu"},
            "openmm": {"platform": "CPU"}}


# -------------------------------------------------------------------------------- engines

#: What the job kinds can use, with the install command when there is a standard one.
#: ``needs`` are import names or command names; an engine is installed when all are present.
_ENGINES: tuple[dict[str, Any], ...] = (
    {"id": "omics-builtin", "name": "Built-in omics (NumPy, SciPy)", "modules": ("numpy", "scipy"),
     "dist": "numpy", "used_by": ["pipeline.rnaseq", "pipeline.scrna"],
     "install": "pip install numpy scipy"},
    {"id": "pydeseq2", "name": "PyDESeq2", "modules": ("pydeseq2",), "dist": "pydeseq2",
     "used_by": ["pipeline.rnaseq", "pipeline.scrna"], "install": "pip install pydeseq2"},
    {"id": "salmon", "name": "Salmon", "commands": ("salmon",), "used_by": ["pipeline.rnaseq"],
     "install": "conda install -c bioconda salmon"},
    {"id": "kallisto", "name": "kallisto", "commands": ("kallisto",),
     "used_by": ["pipeline.rnaseq"], "install": "conda install -c bioconda kallisto"},
    {"id": "hisat2", "name": "HISAT2 + featureCounts", "commands": ("hisat2", "samtools",
                                                                   "featureCounts"),
     "used_by": ["pipeline.rnaseq"],
     "install": "conda install -c bioconda hisat2 samtools subread"},
    {"id": "fastp", "name": "fastp", "commands": ("fastp",), "used_by": ["pipeline.rnaseq"],
     "install": "conda install -c bioconda fastp"},
    {"id": "scanpy", "name": "Scanpy", "modules": ("scanpy", "leidenalg", "harmonypy"),
     "dist": "scanpy", "used_by": ["pipeline.scrna"],
     "install": "pip install scanpy leidenalg harmonypy"},
    {"id": "scvi", "name": "scVI (scvi-tools)", "modules": ("scvi",), "dist": "scvi-tools",
     "used_by": ["pipeline.scrna"], "install": "pip install scvi-tools",
     "note": "runs on the CPU by design (reproducible models)"},
    {"id": "esmfold", "name": "ESMFold (PyTorch + Transformers)",
     "modules": ("torch", "transformers"), "dist": "transformers", "gpu": True,
     "used_by": ["pipeline.fold"], "install": "pip install torch transformers"},
    {"id": "colabfold", "name": "ColabFold", "commands": ("colabfold_batch",), "gpu": True,
     "used_by": ["pipeline.fold"]},
    {"id": "vina", "name": "AutoDock Vina (RDKit, Meeko, Gemmi)",
     "modules": ("rdkit", "meeko", "vina", "gemmi"), "dist": "vina",
     "used_by": ["pipeline.dock"], "install": "pip install rdkit meeko vina gemmi"},
    {"id": "admet", "name": "ADMET models (RDKit, scikit-learn)",
     "modules": ("rdkit", "sklearn"), "dist": "scikit-learn", "used_by": ["pipeline.admet"],
     "install": "pip install rdkit scikit-learn"},
    {"id": "pyarrow", "name": "Snapshot tables (PyArrow)", "modules": ("pyarrow",),
     "dist": "pyarrow", "used_by": ["research.run"], "install": "pip install pyarrow"},
)

_ENGINE_CACHE: dict[str, Any] = {}
_ENGINE_LOCK = threading.Lock()


def _module_present(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _dist_version(name: str | None) -> str | None:
    if not name:
        return None
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def engines(refresh: bool = False, which: Callable[[str], str | None] = shutil.which
            ) -> list[dict[str, Any]]:
    """Installed or missing, per engine. Asks the import system and PATH only; nothing is
    imported or run. Cached for a minute."""
    with _ENGINE_LOCK:
        cached = _ENGINE_CACHE.get("value")
        if cached is not None and not refresh and time.monotonic() - _ENGINE_CACHE["at"] < 60:
            return json.loads(json.dumps(cached))
    out = []
    for spec in _ENGINES:
        missing = [m for m in spec.get("modules", ()) if not _module_present(m)]
        missing += [c for c in spec.get("commands", ()) if not which(c)]
        item: dict[str, Any] = {"id": spec["id"], "name": spec["name"], "installed": not missing,
                                "gpu": bool(spec.get("gpu")), "used_by": list(spec["used_by"])}
        version = _dist_version(spec.get("dist")) if not missing else None
        if version:
            item["version"] = version
        if missing:
            item["missing"] = missing
            if spec.get("install"):
                item["install"] = spec["install"]
        if spec.get("note"):
            item["note"] = spec["note"]
        out.append(item)
    with _ENGINE_LOCK:
        _ENGINE_CACHE.update(value=out, at=time.monotonic())
    return json.loads(json.dumps(out))
