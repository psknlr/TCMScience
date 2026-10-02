"""Run a spatial analysis in its own interpreter, with limits, and keep only the summary.

The analysis environment is a separate Python (it may be a virtual environment with
``h5py`` or a different numpy) that reads the input files and writes the output contract.
The harness receives the summary, the output paths and the provenance — never the matrix.

* ``AnalysisEnvironment.check()`` asks the interpreter what it can do (numpy, h5py, the
  ``bioagent.spatial`` package) before any data are read.
* ``run_isolated`` starts ``python -m bioagent.spatial`` with a wall-clock timeout, an
  address-space limit and a CPU-time limit (POSIX ``setrlimit``), an explicit environment,
  and the input directories checked to exist. A timeout, a limit hit, an input error and
  a crash are reported as different statuses.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping

__all__ = ["AnalysisEnvironment", "run_isolated", "SRC_ROOT"]

SRC_ROOT = Path(__file__).resolve().parents[2]      # …/src, so the child can import bioagent


@dataclass(frozen=True)
class AnalysisEnvironment:
    python: str = sys.executable
    timeout_s: float = 3600.0
    memory_mb: int | None = 8192          # address-space limit for the child
    cpu_s: int | None = None              # CPU-time limit
    env: Mapping[str, str] = field(default_factory=dict)
    pythonpath: tuple[str, ...] = (str(SRC_ROOT),)

    def _env(self) -> dict[str, str]:
        base = {k: v for k, v in os.environ.items()
                if k in ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "SYSTEMROOT")}
        # One BLAS thread: results do not depend on the core count, and per-thread BLAS
        # buffers cannot push a many-core machine past the memory limit.
        base.update({"PYTHONPATH": os.pathsep.join(self.pythonpath), "PYTHONHASHSEED": "0",
                     "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1",
                     "MKL_NUM_THREADS": "1"})
        base.update(self.env)
        return base

    def _limits(self):
        if os.name != "posix":
            return None
        mem, cpu = self.memory_mb, self.cpu_s

        def apply():  # runs in the child before exec
            import resource
            if mem:
                b = int(mem) * 1024 * 1024
                resource.setrlimit(resource.RLIMIT_AS, (b, b))
            if cpu:
                resource.setrlimit(resource.RLIMIT_CPU, (int(cpu), int(cpu)))
        return apply

    def check(self) -> dict[str, Any]:
        code = ("import json, sys\nout = {'python': sys.version.split()[0]}\n"
                "for m in ('numpy', 'h5py', 'bioagent.spatial'):\n"
                "    try:\n        mod = __import__(m, fromlist=['x'])\n"
                "        out[m] = getattr(mod, '__version__', 'present')\n"
                "    except Exception as e:\n        out[m] = None\n"
                "print(json.dumps(out))\n")
        try:
            p = subprocess.run([self.python, "-c", code], capture_output=True, text=True,  # noqa: S603
                               timeout=60, env=self._env(), check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {"usable": False, "reason": f"interpreter {self.python!r} did not answer: "
                                               f"{exc}"}
        try:
            caps = json.loads(p.stdout.strip().splitlines()[-1])
        except (json.JSONDecodeError, IndexError):
            return {"usable": False, "reason": p.stderr.strip()[-500:]}
        usable = bool(caps.get("numpy")) and bool(caps.get("bioagent.spatial"))
        caps["usable"] = usable
        if not usable:
            caps["reason"] = "the interpreter lacks numpy or cannot import bioagent.spatial"
        caps["reads_hdf5"] = bool(caps.get("h5py"))
        return caps


def run_isolated(config: Mapping[str, Any], out_dir: str | Path,
                 environment: AnalysisEnvironment | None = None) -> dict[str, Any]:
    env = environment or AnalysisEnvironment()
    out = Path(out_dir).resolve()
    t0 = time.perf_counter()
    missing = [s["path"] for s in config.get("sections", ()) if not Path(s["path"]).exists()]
    if missing:
        return {"status": "input_error", "error": f"inputs not found: {missing}",
                "out_dir": str(out)}
    caps = env.check()
    if not caps.get("usable"):
        return {"status": "unavailable", "error": caps.get("reason", "environment unusable"),
                "environment": caps, "out_dir": str(out)}
    out.mkdir(parents=True, exist_ok=True)
    cfg_path = out / "run_config.json"
    cfg_path.write_text(json.dumps(config, indent=2, default=str), encoding="utf-8")
    cmd = [env.python, "-m", "bioagent.spatial", "--config", str(cfg_path), "--out", str(out)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,  # noqa: S603
                              timeout=env.timeout_s, env=env._env(), check=False,
                              preexec_fn=env._limits())
    except subprocess.TimeoutExpired:
        return {"status": "timeout", "error": f"timed out after {env.timeout_s}s",
                "out_dir": str(out), "environment": caps}
    record = {"out_dir": str(out), "environment": {**caps, **{
        k: v for k, v in asdict(env).items() if k in ("python", "timeout_s", "memory_mb",
                                                       "cpu_s")}},
              "elapsed_s": round(time.perf_counter() - t0, 2), "returncode": proc.returncode}
    last = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
    try:
        summary = json.loads(last) if last else None
    except json.JSONDecodeError:
        summary = None
    if proc.returncode == 0 and summary:
        return {"status": "succeeded", "summary": summary,
                "provenance": str(out / "provenance.json"), **record}
    if proc.returncode == 2 and summary:
        return {"status": "input_error", "error": summary.get("error"), **record}
    err = proc.stderr.strip()
    limit_hit = "MemoryError" in err or proc.returncode in (-9, -24, 137, 152)
    return {"status": "resource_limit" if limit_hit else "failed",
            "error": err[-1500:] or f"exit code {proc.returncode}", **record}
