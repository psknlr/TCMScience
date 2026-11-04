"""Test only (science.mjs --mode omics): the omics jobs' inputs, their native runs, and the known answer's checks.

    python omics_native.py prepare WORK JOBS_JSON
    python omics_native.py values WORK KEY DIR

``prepare`` writes WORK/raw, the files the page is given as a project's files:

* the RNA-seq count-matrix reference ``omics_counts_world`` (BioScience-Harness/tests/omics_world.py
  ``make_counts_world(seed=1)``): ``counts.tsv`` and ``samples.tsv``;
* the single-cell reference ``sc_world`` (tests/sc_world.py ``make_sc_world(seed=0)``, four Cell Ranger
  directories) as count tables: one gzipped TSV per sample, genes in rows, and ``sc_samples.csv``, a sample sheet with
  sc_world's own columns (sample, path, condition, batch). Each 10x matrix must have the reference manifest's
  digest, and each table, read back by the pipeline's own reader, the same cells, genes and counts as its matrix.

Then it runs the native command on each, with the parameters JOBS_JSON gives the browser job (``bioagent rnaseq
--counts … --samples …``, ``bioagent scrna <sheet> …``), into WORK/native/<key>, with OpenBLAS and OpenMP at one
thread: natively the UMAP layout depends on the BLAS thread count, and a browser worker has one thread. Python,
numpy, scipy, the machine, its CPU features and the thread count go into WORK/native/environment.json, the planted
answer into WORK/truth.json. Where this is the environment a reference's output digests were recorded in (the same
Python, numpy, scipy, machine and CPU features), the native outputs are compared with those digests too, for the
record: the manifest recorded sc_world's at OpenBLAS's default thread count, from its 10x files. It prints
``{environment, runs: {key: {command, seconds, exit, recorded_digests}}}``.

``values`` holds a run's files in DIR (the browser's or the native run's) to the known answer, the bounds being the
reference manifest's (bioagent/data/pipeline_references.json), never loosened: for RNA-seq the planted genes found in
their direction, false discoveries, genes tested; for single-cell every cell one of sc_world's, the clusters against
the true cell types (adjusted Rand index, computed as tests/test_pipeline_references.py does), the four cell types, the
planted genes in the B-cell pseudobulk and nothing elsewhere, batch mixing. A run's ``reference_dataset`` label is
trusted only with its parameters equal to the manifest's (``match_reference`` compares the inputs only). It prints
``{key, checks: [{label, ok, detail}]}``.
"""
from __future__ import annotations

import csv
import gzip
import hashlib
import json
import math
import os
import platform
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
TESTS = HERE.parents[3] / "BioScience-Harness" / "tests"
sys.path.insert(0, str(TESTS))

#: The job kinds' commands: the subcommand of ``bioagent`` and the parameter given as its positional argument.
COMMANDS = {"pipeline.rnaseq": ("rnaseq", None), "pipeline.scrna": ("scrna", "source")}
#: The parameters that name a file (the page's project file of that name; here the file of WORK/raw).
FILES = ("counts", "samples", "source", "markers")
#: The reference each job runs on, by the key science.mjs gives it.
REFERENCES = {"rnaseq": "omics_counts_world", "scrna": "sc_world"}
ONE_THREAD = {"OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1", "MKL_NUM_THREADS": "1"}


def _reference(ref_id: str) -> dict:
    from bioagent.runrecord import pipeline_references

    return next(r for r in pipeline_references()["references"] if r["id"] == ref_id)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _environment() -> dict:
    import numpy as np
    import scipy
    from test_pipeline_references import _cpu_features, _same_environment

    env = {"python": platform.python_version(), "numpy": np.__version__, "scipy": scipy.__version__,
           "machine": platform.machine(), "cpu_features": _cpu_features(), "threads": dict(ONE_THREAD)}
    env["recording_environment"] = {
        ref: _same_environment(_reference(ref)["expected"]["outputs_recorded_with"]) for ref in REFERENCES.values()}
    return env


def _sc_tables(raw: Path, work: Path) -> dict:
    """sc_world written as count tables in ``raw``: one gzipped TSV per sample and the sample sheet; each 10x matrix
    at the manifest's digest, each table read back to its matrix's cells, genes and counts → the truth."""
    import numpy as np
    from bioagent.omics.sc import io as sc_io
    from bioagent.omics.scrna import _matrix_digest
    from sc_world import make_sc_world

    ref = _reference("sc_world")
    world = make_sc_world(work / "sc_world", **ref["source"]["arguments"])
    rows = list(csv.DictReader(world.sheet.open(encoding="utf-8")))
    sheet = ["sample,path,condition,batch"]
    for r in rows:
        m = sc_io.read_counts(world.root / r["path"])
        if _matrix_digest(m) != ref["inputs"][r["sample"]]:
            raise SystemExit(f"sc_world {r['sample']}: its matrix is not at the manifest's digest")
        name = f"{r['sample']}.tsv.gz"
        counts = m.counts.T.toarray().astype(np.int64)            # genes x cells
        with gzip.GzipFile(raw / name, "wb", mtime=0) as fh:
            fh.write(("gene\t" + "\t".join(map(str, m.cells)) + "\n").encode())
            for gene, row in zip(m.gene_names, counts):
                fh.write((str(gene) + "\t" + "\t".join(map(str, row.tolist())) + "\n").encode())
        back = sc_io.read_counts(raw / name)
        if (list(back.cells) != list(m.cells) or list(back.gene_names) != list(m.gene_names)
                or (back.counts != m.counts).nnz):
            raise SystemExit(f"{name} does not read back as sc_world's {r['sample']}")
        sheet.append(f"{r['sample']},{name},{r['condition']},{r['batch']}")
    (raw / "sc_samples.csv").write_text("\n".join(sheet) + "\n", encoding="utf-8")
    return {"types": {cell: t["type"] for cell, t in world.truth.items()},
            "condition_genes": list(world.condition_genes)}


def _command(kind: str, params: dict, raw: Path, out: Path) -> list[str]:
    """The native command a job of ``kind`` with ``params`` stands for (the runner's, tcmstudio.kinds)."""
    sub, positional = COMMANDS[kind]
    cmd = [sys.executable, "-I", "-m", "bioagent.cli", sub]
    for key, value in params.items():
        if value is None:
            continue
        text = str(raw / value) if key in FILES else str(value)
        if key == positional:
            cmd.append(text)
        else:
            cmd.append(f"--{key.replace('_', '-')}={text}")
    # one thread, as in a browser worker (rnaseq's own default is two; it is recorded in run.json)
    if sub == "rnaseq":
        cmd.append("--threads=1")
    return [*cmd, f"--out={out}", "--json"]


def prepare(work: Path, jobs: list[dict]) -> dict:
    from omics_world import make_counts_world

    raw = work / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    ref = _reference("omics_counts_world")
    world = make_counts_world(work / "counts_world", **ref["source"]["arguments"])
    for name in ("counts", "samples"):
        (raw / f"{name}.tsv").write_bytes(Path(world[name]).read_bytes())
    truth = {"rnaseq": {"up": world["up"], "down": world["down"]}, "scrna": _sc_tables(raw, work)}
    (work / "truth.json").write_text(json.dumps(truth), encoding="utf-8")
    native = work / "native"
    native.mkdir(exist_ok=True)
    env = _environment()
    (native / "environment.json").write_text(json.dumps(env, indent=1), encoding="utf-8")
    runs = {}
    for job in jobs:
        out = native / job["key"]
        cmd = _command(job["kind"], job["params"], raw, out)
        t0 = time.perf_counter()
        r = subprocess.run(cmd, capture_output=True, text=True, env={**os.environ, **ONE_THREAD}, cwd=work)
        seconds = time.perf_counter() - t0
        runs[job["key"]] = {"command": [Path(cmd[0]).name, *cmd[1:]], "exit": r.returncode,
                            "seconds": round(seconds, 2), "stderr": r.stderr[-2000:]}
        if r.returncode != 0:
            raise SystemExit(f"the native {job['kind']} run failed (exit {r.returncode}): {r.stderr[-2000:]}")
        ref = REFERENCES[job["key"]]
        if env["recording_environment"][ref]:
            recorded = _reference(ref)["expected"]["outputs"]
            runs[job["key"]]["recorded_digests"] = {
                name: "the recorded digest" if (out / name).is_file() and _sha(out / name) == digest
                else "not the recorded digest" for name, digest in recorded.items()}
    return {"environment": env, "runs": runs, "raw": sorted(p.name for p in raw.iterdir())}


# --------------------------------------------------------------------------------------------- the known answer

def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh, delimiter="\t"))


def _number(text: str) -> float:
    try:
        return float(text)
    except (TypeError, ValueError):
        return math.nan


def _significant(path: Path, alpha: float, gene: str = "gene") -> dict[str, float]:
    """The genes of a results table with p_adjusted below alpha → their log2 fold change (NA is not significant)."""
    out = {}
    for r in _rows(path):
        q = _number(r["p_adjusted"])
        if math.isfinite(q) and q < alpha:
            out[r[gene]] = _number(r["log2_fold_change"])
    return out


def _checker(checks: list[dict]):
    """check(label, ok, detail) appending to ``checks``."""
    def check(label: str, ok: object, detail: object) -> None:
        checks.append({"label": label, "ok": bool(ok), "detail": str(detail)})
    return check


def _label(record: dict, ref: dict, params: dict) -> tuple[bool, str]:
    """Whether the run's reference_dataset label can be trusted: the manifest's id and version, with the manifest's
    parameters (match_reference compares the inputs only)."""
    got = record.get("reference_dataset") or {}
    if got.get("id") != ref["id"] or str(got.get("version")) != str(ref["version"]):
        return False, f"labelled {got.get('id')!r} v{got.get('version')}, not {ref['id']} v{ref['version']}"
    differ = {k: (params.get(k), v) for k, v in ref["parameters"].items() if params.get(k) != v}
    if differ:
        return False, f"labelled {ref['id']}, but its parameters are not the manifest's: {differ}"
    return True, f"{ref['id']} v{ref['version']}, with the manifest's parameters ({ref['parameters']})"


def values_rnaseq(run: Path, truth: dict) -> list[dict]:
    ref = _reference(REFERENCES["rnaseq"])
    want = ref["expected"]["values"]
    record = json.loads((run / "run.json").read_text(encoding="utf-8"))
    config = record.get("config") or {}
    params = {"de_backend": config.get("de_backend"), "design": config.get("design"),
              "contrast": list(config.get("contrast") or []) or None, "alpha": config.get("alpha")}
    checks: list[dict] = []
    check = _checker(checks)
    ok, why = _label(record, ref, params)
    digests = {k: v.get("content_sha256") for k, v in (record.get("validation") or {}).items()}
    check("its inputs are the reference's (content digests) and its label trusted", ok and digests == ref["inputs"],
          why if digests == ref["inputs"] else f"input digests {digests} are not the manifest's {ref['inputs']}")
    alpha = ref["parameters"]["alpha"]
    called = _significant(run / "deseq2_results.tsv", alpha, gene="gene_id")
    up, down = truth["up"], truth["down"]
    found_up = sum(called.get(g, 0) > 1 for g in up)
    found_down = sum(called.get(g, 0) < -1 for g in down)
    check(f"planted up found (FDR < {alpha}, log2 fold change > 1): at least {want['planted_up_found_at_least']}",
          found_up >= want["planted_up_found_at_least"], f"{found_up} of {len(up)}")
    check(f"planted down found (log2 fold change < -1): at least {want['planted_down_found_at_least']}",
          found_down >= want["planted_down_found_at_least"], f"{found_down} of {len(down)}")
    wrong = [g for g in up if called.get(g, 0) < 0] + [g for g in down if called.get(g, 0) > 0]
    check(f"planted genes called in the wrong direction: {want['planted_in_the_wrong_direction']}",
          len(wrong) == want["planted_in_the_wrong_direction"], ", ".join(wrong) or "none")
    false = sorted(set(called) - set(up) - set(down))
    check(f"false discoveries: at most {want['false_discoveries_at_most']}",
          len(false) <= want["false_discoveries_at_most"], f"{len(false)} among {len(called)} calls")
    tested = sum(math.isfinite(_number(r["p_value"])) for r in _rows(run / "deseq2_results.tsv"))
    check(f"genes tested: {want['tested']}", tested == want["tested"], tested)
    check("complete", record.get("complete") is want["complete"], record.get("complete"))
    return checks


def values_scrna(run: Path, truth: dict) -> list[dict]:
    import numpy as np
    from sc_world import EXPECTED
    from test_pipeline_references import _ari

    ref = _reference(REFERENCES["scrna"])
    want = ref["expected"]["values"]
    record = json.loads((run / "run.json").read_text(encoding="utf-8"))
    config = record.get("config") or {}
    checks: list[dict] = []
    check = _checker(checks)
    differ = {k: (config.get(k), v) for k, v in ref["parameters"].items() if config.get(k) != v}
    check(f"its parameters are {ref['id']}'s", not differ, differ or ref["parameters"])
    label = record.get("reference_dataset")
    if label:
        ok, why = _label(record, ref, config)
        check("its reference label trusted", ok, why)
    else:
        # the count tables carry gene names where the 10x files carry ids, so the matrix digest is another; that the
        # tables hold sc_world's counts was checked when they were written (prepare)
        check("its inputs: sc_world v1's counts as count tables (checked when written; no reference label, as the "
              "tables' gene ids are the names)", True, "not labelled")
    cells = _rows(run / "cells.tsv")
    # every cell is one of sc_world's (the reference test indexes world.truth[c]); only the cells whose known type is
    # none of the four (the path, the doublets) are left out of the ARI
    unknown = [r["cell"] for r in cells if r["cell"] not in truth["types"]]
    check("every cell of cells.tsv is one of sc_world's", not unknown,
          f"{len(unknown)} of {len(cells)} are not: {', '.join(unknown[:5])}{', …' if len(unknown) > 5 else ''}"
          if unknown else f"all {len(cells)}")
    known = [r for r in cells if r["cell"] in truth["types"]]
    kind = np.array([truth["types"][r["cell"]] for r in known], dtype=object)
    cluster = np.array([r["cluster"] for r in known], dtype=object)
    keep = np.isin(kind, list(EXPECTED))
    ari = _ari(kind[keep], cluster[keep]) if keep.sum() > 1 else math.nan
    check(f"cluster ARI against the true cell types above {want['cluster_ari_above']}",
          ari > want["cluster_ari_above"], f"{ari:.4f} over {int(keep.sum())} cells of the four types")
    types = sorted({r["cell_type"] for r in _rows(run / "clusters.tsv")})
    check(f"the cell types are {', '.join(want['cell_types'])}", types == want["cell_types"], ", ".join(types))
    pseudobulk = record.get("pseudobulk") or {}
    tests = pseudobulk.get("tests") or {}
    # each test's table as the run names it (two cell types can share a stem: Mono+ and Mono- are Mono_ and Mono__2)
    files = pseudobulk.get("files") or {}
    unnamed = sorted(set(tests) - set(files))
    check("the run record names each pseudobulk test's table", not unnamed,
          f"no table named for {', '.join(unnamed)}" if unnamed else f"{len(files)} tables")
    planted = set(truth["condition_genes"])
    b = tests.get("B cell") if "B cell" in files else None
    found = set(_significant(run / files["B cell"], b["alpha"])) if b else set()
    check(f"planted genes significant in the B-cell pseudobulk: at least "
          f"{want['pseudobulk_b_cell_planted_genes_at_least']}",
          len(found & planted) >= want["pseudobulk_b_cell_planted_genes_at_least"],
          f"{len(found & planted)} of {len(planted)} ({len(found)} significant)" if b else "no B-cell test")
    others = {ct: len(_significant(run / files[ct], t["alpha"]))
              for ct, t in tests.items() if ct != "B cell" and ct in files}
    check(f"significant genes in each other cell type: at most "
          f"{want['pseudobulk_other_cell_types_significant_at_most']}",
          others and max(others.values()) <= want["pseudobulk_other_cell_types_significant_at_most"], others)
    mixing = (record.get("integration") or {}).get("mixing_after")
    check(f"batch mixing after integration above {want['batch_mixing_after_above']}",
          isinstance(mixing, (int, float)) and mixing > want["batch_mixing_after_above"], mixing)
    check("complete", record.get("complete") is want["complete"], record.get("complete"))
    return checks


def main(argv: list[str]) -> int:
    if len(argv) == 3 and argv[0] == "prepare":
        print(json.dumps(prepare(Path(argv[1]), json.loads(argv[2]))))
        return 0
    if len(argv) == 4 and argv[0] == "values":
        truth = json.loads((Path(argv[1]) / "truth.json").read_text(encoding="utf-8"))
        key, run = argv[2], Path(argv[3])
        checks = (values_rnaseq if key == "rnaseq" else values_scrna)(run, truth[key])
        print(json.dumps({"key": key, "checks": checks}))
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
