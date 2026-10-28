"""Is an optional scientific package really usable here? Imported, versioned, and computing.

Installation metadata says a distribution is present; it does not say the package imports
(a missing system library, an ABI mismatch), that it is the version CI tested, or that it
computes correctly. For every package of every group in
``registry/scientific_environments.json`` this checks the three:

    import       the module imports (BLOCKED when the package is not installed)
    version      the installed version is the locked one (UNVERIFIED when it differs:
                 it may work, but it is not the version that was tested)
    calculation  a small computation with a known answer: aspirin's molecular weight, a
                 hypergeometric tail, a regression's coefficients, a Leiden partition of
                 two triangles, a TM-score of a structure against itself… (UNVERIFIED for
                 a package with no cheap computation here; CI's own jobs run those for real)

and, for the command-line tools a group names, that the binary is on the PATH and answers
``--version``.
"""

from __future__ import annotations

import importlib
import importlib.metadata as metadata
import json
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Mapping

from . import BLOCKED, FAIL, NOT_APPLICABLE, PASS, UNVERIFIED, Record

__all__ = ["MODULES", "load_environments", "readiness", "install_command"]

#: distribution -> import name, where they differ
MODULES = {"scikit-learn": "sklearn", "umap-learn": "umap", "scvi-tools": "scvi",
           "paper-qa": "paperqa"}

#: binary -> the arguments that print its version, where ``--version`` is not one: kallisto
#: reads ``--version`` as an unknown command, prints its usage and exits 1; featureCounts
#: reads it as an unknown option (its version option is ``-v``)
VERSION_ARGS = {"kallisto": ["version"], "featureCounts": ["-v"]}


def load_environments(path: str | Path | None = None) -> dict[str, Any]:
    from ..config import registry_dir

    p = Path(path) if path else registry_dir() / "scientific_environments.json"
    return json.loads(p.read_text(encoding="utf-8"))


def install_command(group: Mapping[str, Any]) -> str:
    """The pip command that installs a group at its locked versions."""
    pins = [f"{name}=={version}" if version else name
            for name, version in (group.get("packages") or {}).items()]
    extra = "".join(f" --extra-index-url {url}" for url in
                    sorted(set((group.get("index_url") or {}).values())))
    return f"python -m pip install{extra} " + " ".join(pins)


# ---------------------------------------------------------------- the calculations
def _rdkit() -> tuple[bool, str]:
    from rdkit import Chem
    from rdkit.Chem import Descriptors

    mw = Descriptors.MolWt(Chem.MolFromSmiles("CC(=O)Oc1ccccc1C(=O)O"))
    return abs(mw - 180.159) < 0.01, f"aspirin MolWt {mw:.3f} (expected 180.159)"


def _meeko() -> tuple[bool, str]:
    from meeko import MoleculePreparation, PDBQTWriterLegacy
    from rdkit import Chem
    from rdkit.Chem import AllChem

    mol = Chem.AddHs(Chem.MolFromSmiles("CCO"))
    AllChem.EmbedMolecule(mol, randomSeed=42)
    setups = MoleculePreparation().prepare(mol)
    text, ok, err = PDBQTWriterLegacy.write_string(setups[0])
    return bool(ok) and "ROOT" in text, f"ethanol prepared as PDBQT ({len(text)} chars){err or ''}"


def _gemmi() -> tuple[bool, str]:
    import gemmi

    st = gemmi.read_pdb_string("ATOM      1  CA  ALA A   1      11.104   6.134  -6.504  1.00  "
                               "0.00           C\nEND\n")
    n = sum(1 for _ in st[0].all())
    return n == 1, f"one ATOM record read as {n} atom"


def _sklearn() -> tuple[bool, str]:
    from sklearn.linear_model import LogisticRegression

    model = LogisticRegression().fit([[0.0], [1.0], [2.0], [3.0]], [0, 0, 1, 1])
    got = [int(x) for x in model.predict([[0.0], [3.0]])]
    return got == [0, 1], f"a separable 1-D problem classified {got} (expected [0, 1])"


def _numpy() -> tuple[bool, str]:
    import numpy as np

    det = float(np.linalg.det(np.array([[1.0, 2.0], [3.0, 4.0]])))
    return abs(det + 2.0) < 1e-9, f"det [[1,2],[3,4]] = {det:.6f} (expected -2)"


def _scipy() -> tuple[bool, str]:
    from scipy.stats import hypergeom

    p = float(hypergeom.sf(0, 10, 5, 2))
    return abs(p - 7 / 9) < 1e-9, f"P(X > 0), X ~ Hypergeom(10, 5, 2) = {p:.6f} (expected 7/9)"


def _pandas() -> tuple[bool, str]:
    import pandas as pd

    got = pd.DataFrame({"g": ["a", "a", "b"], "v": [1, 2, 3]}).groupby("g")["v"].sum().to_dict()
    return got == {"a": 3, "b": 3}, f"group sums {got}"


def _statsmodels() -> tuple[bool, str]:
    import numpy as np
    import statsmodels.api as sm

    x = np.arange(10.0)
    params = sm.OLS(2 * x + 1, sm.add_constant(x)).fit().params
    got = [round(float(x), 6) for x in params]
    return bool(np.allclose(params, [1.0, 2.0])), f"OLS of y = 2x + 1: coefficients {got}"


def _anndata_scanpy() -> tuple[bool, str]:
    import anndata
    import numpy as np
    import scanpy as sc

    adata = anndata.AnnData(np.array([[1.0, 3.0], [2.0, 2.0], [0.0, 8.0]]))
    sc.pp.normalize_total(adata, target_sum=4.0)
    sums = [float(x) for x in np.asarray(adata.X).sum(axis=1)]
    return bool(np.allclose(sums, 4.0)), f"normalize_total to 4: row sums {sums}"


def _leiden() -> tuple[bool, str]:
    import igraph as ig
    import leidenalg

    g = ig.Graph(edges=[(0, 1), (1, 2), (2, 0), (3, 4), (4, 5), (5, 3)])
    part = leidenalg.find_partition(g, leidenalg.ModularityVertexPartition, seed=0)
    groups = sorted(sorted(c) for c in part)
    return groups == [[0, 1, 2], [3, 4, 5]], f"two triangles partitioned as {groups}"


def _h5py() -> tuple[bool, str]:
    import h5py
    import numpy as np

    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "x.h5"
        with h5py.File(path, "w") as fh:
            fh["x"] = np.arange(5)
        with h5py.File(path, "r") as fh:
            back = [int(x) for x in fh["x"][:]]
    return back == [0, 1, 2, 3, 4], f"a dataset written and read back: {back}"


def _tmtools() -> tuple[bool, str]:
    import numpy as np
    from tmtools import tm_align

    coords = np.array([[0.0, 0.0, 0.0], [3.8, 0.0, 0.0], [5.0, 3.6, 0.0], [8.0, 5.0, 1.0],
                       [11.0, 7.0, 3.0], [12.0, 10.0, 6.0]])
    res = tm_align(coords, coords, "AAAAAA", "AAAAAA")
    return abs(res.tm_norm_chain1 - 1.0) < 1e-6, f"TM-score of a chain on itself {res.tm_norm_chain1:.4f}"


def _torch() -> tuple[bool, str]:
    import torch

    got = (torch.tensor([[1.0, 2.0], [3.0, 4.0]]) @ torch.tensor([[1.0], [1.0]])).flatten().tolist()
    return got == [3.0, 7.0], f"[[1,2],[3,4]] @ [1,1] = {got}"


def _openpyxl() -> tuple[bool, str]:
    import io

    import openpyxl

    wb = openpyxl.Workbook()
    wb.active.append(["桂枝", 3])
    buf = io.BytesIO()
    wb.save(buf)
    back = [c.value for c in openpyxl.load_workbook(io.BytesIO(buf.getvalue())).active[1]]
    return back == ["桂枝", 3], f"a row written and read back: {back}"


def _pyarrow() -> tuple[bool, str]:
    import io

    import pyarrow as pa
    import pyarrow.parquet as pq

    buf = io.BytesIO()
    pq.write_table(pa.table({"x": [1, 2, 3]}), buf)
    back = pq.read_table(io.BytesIO(buf.getvalue())).column("x").to_pylist()
    return back == [1, 2, 3], f"a Parquet column round-tripped: {back}"


CALCULATIONS: dict[str, Callable[[], tuple[bool, str]]] = {
    "rdkit": _rdkit, "meeko": _meeko, "gemmi": _gemmi, "scikit-learn": _sklearn,
    "numpy": _numpy, "scipy": _scipy, "pandas": _pandas, "statsmodels": _statsmodels,
    "scanpy": _anndata_scanpy, "anndata": _anndata_scanpy, "leidenalg": _leiden,
    "igraph": _leiden, "h5py": _h5py, "tmtools": _tmtools, "torch": _torch,
    "openpyxl": _openpyxl, "pyarrow": _pyarrow}
#: why a package has no calculation here
NO_CALCULATION = {
    "vina": "a docking needs a prepared receptor; CI's molecular-tools job docks a real one",
    "pydeseq2": "a differential-expression fit takes seconds to minutes; CI's analysis job runs it",
    "harmonypy": "integration needs a batch design; CI's analysis job runs it",
    "umap-learn": "the first call compiles with numba (slow); CI's analysis job runs it",
    "gseapy": "a preranked GSEA needs gene sets; CI's analysis job runs it",
    "scvi-tools": "training takes minutes; CI's scvi job trains on the CPU",
    "transformers": "a model needs gigabytes of weights; run by hand"}


def _installed(dist: str) -> str | None:
    try:
        return metadata.version(dist)
    except metadata.PackageNotFoundError:
        return None


def readiness(groups: Mapping[str, Any] | None = None, *, calculate: bool = True
              ) -> list[Record]:
    """One record per group: each package imported, versioned and computing."""
    doc = groups if groups is not None else load_environments()
    out = []
    done: dict[str, tuple[str, str]] = {}
    for name, group in (doc.get("groups") or {}).items():
        rec = Record(name, "environment", "", provenance={"purpose": group.get("purpose", "")},
                     reproduce=install_command(group))
        for dist, pin in (group.get("packages") or {}).items():
            module = MODULES.get(dist, dist)
            t0 = time.perf_counter()
            try:
                importlib.import_module(module)
            except ImportError as exc:
                rec.add(f"{dist}:import", BLOCKED, f"not installed or not importable here "
                        f"({exc.__class__.__name__}: {exc}); install with the group's command")
                rec.add(f"{dist}:calculation", NOT_APPLICABLE, "not importable")
                continue
            except Exception as exc:                            # noqa: BLE001
                rec.add(f"{dist}:import", FAIL, f"installed but fails to import: "
                        f"{type(exc).__name__}: {exc}")
                continue
            have = _installed(dist)
            rec.add(f"{dist}:import", PASS, f"imports ({module}); version {have}",
                    duration_s=time.perf_counter() - t0)
            if pin is None:
                rec.add(f"{dist}:version", UNVERIFIED, f"{have} installed; no tested pin")
            elif have is None or have.split("+", 1)[0] != pin:
                rec.add(f"{dist}:version", UNVERIFIED,
                        f"{have} installed, {pin} locked: not the version that was tested",
                        installed=have, locked=pin)
            else:
                rec.add(f"{dist}:version", PASS, f"the locked {pin}")
            if not calculate:
                continue
            fn = CALCULATIONS.get(dist)
            if fn is None:
                rec.add(f"{dist}:calculation", UNVERIFIED, NO_CALCULATION.get(
                    dist, "no calculation with a known answer is run here"))
                continue
            if dist in done:
                status, detail = done[dist]
            else:
                t1 = time.perf_counter()
                try:
                    ok, detail = fn()
                    status = PASS if ok else FAIL
                except Exception as exc:                        # noqa: BLE001
                    status, detail = FAIL, f"{type(exc).__name__}: {exc}"
                detail = f"{detail} [{time.perf_counter() - t1:.2f} s]"
                done[dist] = (status, detail)
            rec.add(f"{dist}:calculation", status, detail)
        for binary in group.get("binaries") or ():
            exe = shutil.which(binary)
            if not exe:
                rec.add(f"{binary}:binary", BLOCKED, "not on the PATH (install it with the "
                        "system's package manager)")
                continue
            args = VERSION_ARGS.get(binary, ["--version"])
            try:
                res = subprocess.run([exe, *args], capture_output=True, text=True,
                                     timeout=30, check=False)
                text = (res.stdout or res.stderr).strip().splitlines()[:1]
                said = text[0] if text else "no output"
                rec.add(f"{binary}:binary", PASS if res.returncode == 0 else FAIL,
                        said if res.returncode == 0 else
                        f"`{binary} {' '.join(args)}` exited {res.returncode}: {said}", path=exe)
            except (OSError, subprocess.SubprocessError) as exc:
                rec.add(f"{binary}:binary", FAIL, f"{type(exc).__name__}: {exc}")
        out.append(rec)
    return out
