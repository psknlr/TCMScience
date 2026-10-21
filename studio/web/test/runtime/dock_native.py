"""Test only (science.mjs --mode dock): the docking job's inputs, its native runs, and native Vina's scores of the poses
the browser found.

    python dock_native.py prepare WORK PLAN_JSON
    python dock_native.py native WORK PLAN_JSON

``prepare`` copies the prepared fixtures (BioScience-Harness/tests/fixtures/docking/prepared, written by
studio/scripts/make_dock_fixtures.py: 3PTB's pocket as a rigid receptor, six ligands as MODEL blocks, the crystal
ligand BEN in its crystal pose) into WORK/raw, the files the page is given as a project's files, after checking that
native AutoDock Vina 1.2.7 is here (the browser's engine is that release; anything else is refused before the page
runs). It prints ``{environment, files: {name: {sha256, bytes}}}``.

``native`` runs, after the page, what the job stands for, natively, on the same files with the same parameters
(PLAN_JSON: dock-plan.mjs's ``{job: {kind, params}}``):

* ``read``: ``tcmstudio.browser_jobs.dock_read``, the plan of the Vina runs, into WORK/native/read (the browser's
  plan must be its bytes);
* ``analysis``: ``tcmstudio.browser_jobs.run_dock`` on the browser's own Vina results (WORK/browser/results, as the
  page wrote them), into WORK/native/analysis: the Python stages in CPython, ``PrecomputedVina`` reading what the
  WebAssembly engine computed (every output must be the browser's, byte for byte);
* ``runner``: the runner's command for the job (``tcmstudio.kinds`` pipeline.dock: ``bioagent dock RECEPTOR.pdbqt
  LIGANDS.pdbqt --reference-ligand …``, native Vina 1.2.7 on this machine's cores), into WORK/native/runner, with the
  kind's own checks of what it wrote;
* ``score``: each pose the page re-scored with the WebAssembly engine (WORK/browser/scored.json: {key, pose, params},
  the first pose of the browser's result for that run) scored by native Vina, ``Vina.score()`` raw (the C++ values;
  the Python wrapper rounds to 3 decimals), each value also as its float64 bits; and whether that pose is the first
  MODEL of the browser's result as ``bioagent.docking.prepared.pose_models`` reads it.

It writes WORK/native.json ``{environment, plan_dir, runs: {analysis, runner}, scores}`` and prints ``{written}``.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Mapping

FIXTURES = Path(__file__).resolve().parents[4] / "BioScience-Harness" / "tests" / "fixtures" / "docking" / "prepared"
VINA_VERSION = "1.2.7"
#: The parameters of the job that are files (the page's project files of those names; here the files of WORK/raw).
FILES = ("receptor", "ligands", "reference_ligand")
#: The job's settings dock_read and run_dock take beside the files.
SETTINGS = ("center", "size", "scoring", "exhaustiveness", "poses", "seed")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def bits(x: float) -> str:
    """A float64's bits as 16 hex digits, big-endian (dock-plan.mjs float64Bits)."""
    return struct.pack(">d", x).hex()


def environment() -> dict[str, Any]:
    import numpy as np
    import vina
    out: dict[str, Any] = {"python": platform.python_version(), "numpy": np.__version__, "vina": vina.__version__,
                           "machine": platform.machine(), "platform": platform.platform(), "cores": os.cpu_count()}
    libc, version = platform.libc_ver()
    if libc == "glibc":
        out["glibc"] = version
    try:
        cpu = next((ln.split(":", 1)[1].strip() for ln in Path("/proc/cpuinfo").read_text().splitlines()
                    if ln.startswith("model name")), None)
    except OSError:
        cpu = None
    if cpu:
        out["cpu"] = cpu
    return out


def _need_vina() -> None:
    try:
        import vina
    except ImportError:
        raise SystemExit(f"dock_native.py needs native AutoDock Vina {VINA_VERSION} (pip install vina=={VINA_VERSION} "
                         "meeko==0.8.0 gemmi==0.7.5)") from None
    if vina.__version__ != VINA_VERSION:
        raise SystemExit(f"dock_native.py needs native AutoDock Vina {VINA_VERSION}, the browser engine's release; "
                         f"found {vina.__version__}")


# --------------------------------------------------------------------------------------------------- prepare

def prepare(work: Path, plan: Mapping[str, Any]) -> dict[str, Any]:
    _need_vina()
    raw = work / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    files = {}
    for key in FILES:
        name = plan["job"]["params"].get(key)
        if not name:
            continue
        data = (FIXTURES / name).read_bytes()
        (raw / name).write_bytes(data)
        files[name] = {"sha256": _sha(data), "bytes": len(data)}
    return {"environment": environment(), "files": files}


# ---------------------------------------------------------------------------------------------------- native

def _args(work: Path, plan: Mapping[str, Any]) -> dict[str, Any]:
    """The job's parameters as dock_read and run_dock take them: each file a path in WORK/raw."""
    p = plan["job"]["params"]
    out = {k: str(work / "raw" / p[k]) for k in FILES if p.get(k)}
    return {**out, **{k: p[k] for k in SETTINGS if k in p}}


def _runner(work: Path, plan: Mapping[str, Any]) -> dict[str, Any]:
    """The runner's command for the job (tcmstudio.kinds pipeline.dock, prepare then command), the files found by their
    names in WORK/raw as the runner finds a project's uploads, run here; then the kind's own checks of its outputs."""
    from tcmstudio.kinds import KINDS, OUTPUT_TOKEN, PrepareContext, StartContext

    kind = KINDS[plan["job"]["kind"]]
    raw = work / "raw"
    job = work / "native" / "runner-job"
    out = work / "native" / "runner"
    for d in (job, out):
        if d.exists():
            shutil.rmtree(d)
    job.mkdir(parents=True)
    found = {p.name: (p, {"name": p.name}) for p in raw.iterdir() if p.is_file()}
    ctx = PrepareContext(job_id="science-dock", job_dir=job, project_id=None, paths={}, data_env={},
                         find=found.get, settings={})
    prepared = kind.prepare(dict(plan["job"]["params"]), ctx)
    start = StartContext(job_id="science-dock", job_dir=job, project_id=None, paths={}, data_env={}, settings={},
                         device="cpu", threads=os.cpu_count() or 1, python=sys.executable)
    argv = [a.replace(OUTPUT_TOKEN, str(out)) for a in kind.command(prepared, start)]
    # -I (the runner's) also ignores PYTHONPATH: a checkout run from its sources keeps it; an installed one (CI) does
    # not need it and runs the command exactly as the runner does
    if os.environ.get("PYTHONPATH"):
        argv = [a for a in argv if a != "-I"]
    t0 = time.perf_counter()
    r = subprocess.run(argv, capture_output=True, text=True, cwd=work)
    seconds = round(time.perf_counter() - t0, 2)
    checks = {}
    for name, rel, required in kind.artefacts:
        path = out / rel
        if not path.is_file():
            checks[name] = "missing" if required else "absent (not required)"
            continue
        problem = kind.validators[name](path) if name in kind.validators else None
        checks[name] = problem or "ok"
    return {"command": [Path(argv[0]).name, *(a.replace(str(work), "WORK") for a in argv[1:])], "exit": r.returncode,
            "seconds": seconds, "stdout": r.stdout[-3000:], "stderr": r.stderr[-3000:], "out": str(out),
            "checks": checks}


def _score(receptor_text: str, pose: str, params: Mapping[str, Any]) -> list[float]:
    """Native Vina.score() of a pose as given, raw (make_dock_fixtures.py score_and_optimize does the same)."""
    import vina
    v = vina.Vina(sf_name=params["scoring"], seed=int(params.get("seed", 42)), verbosity=0)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "receptor.pdbqt"
        path.write_bytes(receptor_text.encode("utf-8"))   # Vina reads a receptor from a file only
        v.set_receptor(rigid_pdbqt_filename=str(path))
    v.set_ligand_from_string(pose)
    v.compute_vina_maps(center=list(params["center"]), box_size=list(params["size"]),
                        spacing=float(params.get("spacing", 0.375)))
    return [float(x) for x in v._vina.score()]


def native(work: Path, plan: Mapping[str, Any]) -> dict[str, Any]:
    from bioagent.docking.prepared import pose_models
    from tcmstudio.browser_jobs import dock_read, run_dock

    _need_vina()
    root = work / "native"
    for d in ("read", "analysis"):
        if (root / d).exists():
            shutil.rmtree(root / d)
    args = _args(work, plan)
    dock_read({**args, "out_dir": str(root / "read")})
    plan_dir = root / "read" / "plan"
    runs: dict[str, Any] = {}

    # the browser's own Vina results, read by the same Python in CPython
    results = work / "browser" / "results"
    t0 = time.perf_counter()
    ended = run_dock({**args, "plan_dir": str(plan_dir), "results_dir": str(results),
                      "out_dir": str(root / "analysis")})
    runs["analysis"] = {"command": ["tcmstudio.browser_jobs.run_dock", "results_dir=WORK/browser/results"],
                        "exit": ended["exit"], "seconds": round(time.perf_counter() - t0, 2),
                        "ended": ended, "out": str(root / "analysis")}
    runs["runner"] = _runner(work, plan)

    # the browser's top poses, scored by native Vina
    receptor = (plan_dir / "receptor.pdbqt").read_text(encoding="utf-8")
    scores = []
    scored = json.loads((work / "browser" / "scored.json").read_text(encoding="utf-8"))
    for s in scored:
        row: dict[str, Any] = {"key": s["key"], "name": s.get("name"),
                               "receptor_sha256": _sha(receptor.encode("utf-8")),
                               "ligand_sha256": _sha(s["pose"].encode("utf-8"))}
        try:
            doc = json.loads((results / f"{s['key']}.json").read_text(encoding="utf-8"))
            first = "\n".join(pose_models(doc["poses"])[0]) + "\n"
            row["first_pose"] = first == s["pose"]
        except (OSError, ValueError, KeyError, IndexError, TypeError) as exc:
            row["first_pose"] = False
            row["first_pose_error"] = f"{type(exc).__name__}: {exc}"
        t1 = time.perf_counter()
        try:
            energies = _score(receptor, s["pose"], s["params"])
            row.update({"energies": energies, "bits": [bits(x) for x in energies]})
        except (RuntimeError, TypeError, ValueError) as exc:     # Vina's errors, through SWIG
            row["error"] = " ".join(str(exc).split())
        row["ms"] = round((time.perf_counter() - t1) * 1000, 1)
        scores.append(row)
    return {"environment": environment(), "plan_dir": str(plan_dir), "runs": runs, "scores": scores}


def main(argv: list[str]) -> int:
    if len(argv) > 1:
        argv = [argv[0], str(Path(argv[1]).resolve()), *argv[2:]]
    if argv[:1] == ["prepare"] and len(argv) == 3:
        print(json.dumps(prepare(Path(argv[1]), json.loads(argv[2]))))
        return 0
    if argv[:1] == ["native"] and len(argv) == 3:
        doc = native(Path(argv[1]), json.loads(argv[2]))
        (Path(argv[1]) / "native.json").write_text(json.dumps(doc), encoding="utf-8")
        print(json.dumps({"written": str(Path(argv[1]) / "native.json")}))
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
