#!/usr/bin/env python3
"""The browser compatibility matrix of every catalog entry, generated from the catalog itself.

    python3 studio/scripts/compat_matrix.py            # rewrites studio/docs/compat/matrix.csv and summary.md
    python3 studio/scripts/compat_matrix.py --check    # fails when the committed files are out of date

Every row is one entry of the tool catalog (``tcmstudio.catalog``): what runs it today, where its
computation happens, which local backends it has, what it needs, and what stands in the way of the
rest. The classes are the ones the modernisation plan asks about; an entry is put in the most
specific class its code and dependencies support, never in one it has not been shown to reach:

  1  runs in the browser now, unchanged (Pyodide: CPython compiled to WebAssembly, CPU)
  1g runs in the browser now; its data comes from the source's public API through the site's
     bounded gateway (the request, parsing and policy run locally; the data is remote by nature)
  1j runs in the browser now as a job: the same Python in the page's job workers (Pyodide, CPU),
     its files in the site's private storage (OPFS), followed and cancelled like a runner job
  2  converted: a JavaScript CPU kernel computes the hot loop, byte-identical to Python (checked)
  3  WebGPU and WebAssembly SIMD: exact integer reductions on the GPU or with SIMD on the CPU,
     with recounts in Python (and the JavaScript kernel, then Python, as the fallbacks)
  4  WebNN candidate: neural inference once a model is packaged (none ships yet)
  5  WASM SIMD / threads candidate: a CPU kernel would pay off; not written yet
  6  needs substantial work to run in a browser (data, memory, a job model), though its
     Python dependencies exist in Pyodide
  7  needs a native engine or a GPU framework the browser cannot load (the local runner keeps it)
  8  stays on the local runner: processes, files, long jobs, MCP servers
"""
from __future__ import annotations

import argparse
import csv
import io
import sys
from collections import Counter
from pathlib import Path

STUDIO = Path(__file__).resolve().parents[1]
REPO = STUDIO.parent
OUT = STUDIO / "docs" / "compat"

for src in (STUDIO / "runner" / "src", REPO / "BioScience-Harness" / "src", REPO / "PSH-Harness" / "src"):
    if src.is_dir() and str(src) not in sys.path:
        sys.path.insert(0, str(src))

CLASS_LABELS = {
    "1": "browser now (Pyodide, CPU)",
    "1g": "browser now; data via the gateway",
    "1j": "browser now, as a job (job workers + private storage)",
    "2": "browser: JavaScript CPU kernel",
    "3": "browser: WebGPU / WASM SIMD (exact) + CPU kernel",
    "4": "WebNN candidate",
    "5": "WASM SIMD/threads candidate",
    "6": "substantial work for the browser",
    "7": "native engine or GPU framework (runner)",
    "8": "local runner only",
}

# Which tools have a browser kernel, and which backends it has, is read from the catalog
# (entry.execution.browser_compute, which tcmstudio.catalog derives from tcmstudio.browser_compute):
# class 3 when WebGPU is among the backends, class 2 otherwise. These two maps only hold the notes
# a person wrote for them; a tool listed here without a kernel in the catalog stays in its class.
# the notes for the tools whose hot loop has a JavaScript kernel (studio/web/js/compute/kernels.js)
KERNEL = {
    "native.global_alignment": "Needleman–Wunsch",
    "native.local_alignment": "Smith–Waterman",
    "native.protein_alignment": "Needleman–Wunsch / Smith–Waterman (BLOSUM62)",
    "native.edit_distance": "Levenshtein",
}
# … and for those whose integer counts also have the WebGPU kernel (studio/web/js/runtime/webgpu.js) and
# the WebAssembly SIMD kernel (studio/web/js/compute/wasm/seqcount.wat)
GPU = {
    "native.distance_matrix": "pairwise site/difference/transition counts",
    "native.hamming_distance": "mismatch count",
    "native.gc_content": "GC counts (windows slide in JavaScript)",
}
# CPU work in Pyodide that a WASM SIMD or threaded kernel would speed up (measured or O(·) evident), not written
WASM_CANDIDATES = {
    "native.neighbor_joining": "O(n³) in Python; exact port needs the same tie-breaking",
    "native.upgma": "O(n³) in Python",
    "native.kmer_counts": "linear, but dict-heavy in Python for long sequences",
    "native.find_orfs": "string scanning, six frames",
    "native.crispr_guides": "string scanning",
    "native.mann_whitney_u": "rank sums over large samples",
    "native.kruskal_wallis": "ranks over large samples",
    "native.enrichment_analysis": "many hypergeometric tests",
}
# Notes for entries that run in Pyodide as they are (class 1) and need one
PYODIDE_NOTES = {
    # The limited seed-corpus skill draws no permutation and computes no topology (skills/p0/netpharm.py:
    # randomisation not performed), so no kernel would pay off: class 1, not a WASM candidate. The seeded
    # degree-matched permutation null and Brandes betweenness are the research loop's
    # (analysis/network_pharmacology.py, job.research.run, class 6), outside the pin.
    "skill.analyze-tcm-network-pharmacology": "limited seed corpus: no permutation null, no topology; trivial "
                                              "CPU. The seeded permutation null and betweenness run in "
                                              "job.research.run",
}
# job and skill details: what blocks the browser, with the Pyodide 314 lockfile in mind
RUNNER_REASONS = {
    "job.pipeline.fold": ("7", "ESMFold needs torch + transformers (not in Pyodide) and a GPU; ESM Atlas / ColabFold are "
                               "remote services (allow_remote)"),
    "job.pipeline.dock": ("7", "AutoDock Vina, meeko, gemmi and rdkit are native and not in Pyodide"),
    "job.pipeline.admet": ("7", "rdkit is not in Pyodide (scikit-learn is)"),
    "job.skill.run": ("8", "runs a governed skill as a subprocess job"),
    "job.tcmdb.fetch": ("8", "downloads full third-party databases to disk"),
    "job.tcmdb.build": ("8", "builds SQLite stores from those downloads"),
    "skill.retrieve-literature-evidence": ("7", "PaperQA2 and its model calls are not a browser package"),
    "skill.dock-ligands": ("7", "the docking engines above"),
    "skill.predict-admet": ("7", "rdkit"),
    "skill.predict-protein-structure": ("7", "torch / transformers and a GPU, or a remote structure service"),
    "skill.rnaseq-differential-expression": ("7", "the RNA-seq pipeline from FASTQ (salmon / kallisto / hisat2 / "
                                                  "fastp are native binaries; a transcriptome index needs GBs of "
                                                  "memory); the count-matrix mode runs as job.pipeline.rnaseq in the "
                                                  "browser"),
    "skill.scrna-cell-atlas": ("6", "the single-cell pipeline as a governed skill, a skill.run job on the runner; its "
                                    "built-in analysis of count tables runs as job.pipeline.scrna in the browser"),
    "system.provider_call": ("8", "ToolUniverse / BioMCP run as local MCP processes"),
    "system.doctor": ("8", "inspects the runner's Python environment"),
}
# What a browser job reads and writes (classified by entry.execution.browser_job, from tcmstudio.catalog)
BROWSER_JOB_NOTES = {
    "job.research.snapshot": "17 raw files (418 MB) downloaded, relayed or imported into OPFS, read in place by Python "
                             "through synchronous access handles; integrity, parse, gold check and ledger as on the "
                             "runner",
    "job.research.run": "the sixteen-stage workflow and nine exports over the snapshots in OPFS; the governed analysis "
                        "runs in-process through the PSH broker (a worker cannot start processes) and the artifact "
                        "says so",
    "job.pipeline.rnaseq": "the count-matrix mode (a count table and a sample table from the project's files, copied "
                           "into OPFS): the built-in DESeq2 implementation, VST, PCA and the report, as on the runner; "
                           "FASTQ (QC, trimming, quantification) and PyDESeq2 need the runner, and so do more than "
                           "100,000 genes or 200 samples (wasm32, 4 GB); the numbers are held to the native run's "
                           "within a stated tolerance",
    "job.pipeline.scrna": "count tables (CSV/TSV, gzipped or not) or a sheet of them from the project's files: QC, "
                          "doublets, built-in Harmony, Leiden, UMAP, markers, annotation, pseudobulk, as on the runner; "
                          "Scanpy, scVI, 10x HDF5 and .h5ad need the runner, and so do more than 6,000 cells (a "
                          "browser worker holds no more: wasm32, 4 GB); the UMAP layout amplifies last-digit "
                          "differences and is not compared with the native run's",
}
PYODIDE_NOTES_JOBS = {
    "system.job_status": "the page answers its own jobs (ids bj-…); a runner job's status needs the runner",
}
WEBNN_NOTE = ("No model ships with the site; a packaged ONNX model (embeddings for TCM terms, a text classifier) "
              "could run here through ONNX Runtime Web's WebNN, WebGPU or WASM providers")


def validation(e: dict) -> str:
    """The entry's committed verification (entry.verification, from tcmstudio.catalog): each level
    with its status and, for a dated record, its date. No record is UNVERIFIED, never a pass."""
    levels = (e.get("verification") or {}).get("levels") or {}
    if not levels:
        return "UNVERIFIED: no committed verification record"
    return "; ".join(f"{name} {lv.get('status', 'UNVERIFIED')}" + (f" ({str(lv['at'])[:10]})" if lv.get("at") else "")
                     for name, lv in levels.items())


def classify(e: dict) -> tuple[str, str, str, str, str]:
    """→ (class, local backends in order, fallback, validation, notes)."""
    eid, exec_ = e["id"], e.get("exec") or []
    checked = validation(e)
    kernel = (e.get("execution") or {}).get("browser_compute")
    if kernel and "browser" in exec_:
        fast = [b for b in kernel.get("backends") or () if b in ("webgpu", "wasm-simd")]
        if "webgpu" in fast:
            return ("3", " | ".join(fast) + " → js kernel → pyodide", "the CPU kernel, then the original Python",
                    checked, GPU.get(eid) or kernel.get("workload", ""))
        return ("2", "js kernel → pyodide", "the original Python", checked,
                KERNEL.get(eid) or kernel.get("workload", ""))
    job = (e.get("execution") or {}).get("browser_job")
    if job and "browser" in exec_:
        packages = ", ".join(job.get("pyodide_packages") or [])
        return ("1j", f"pyodide job workers ({packages}) + OPFS → the runner's job service",
                "the runner, when the browser has no private storage or the user prefers it", checked,
                BROWSER_JOB_NOTES.get(eid, ""))
    if eid in RUNNER_REASONS:
        cls, why = RUNNER_REASONS[eid]
        return (cls, "runner (native CPython" + (", GPU" if e.get("gpu") else "") + ")",
                "none in the browser: the call says what it needs", checked, why)
    if "browser" not in exec_:
        return ("8", "runner (native CPython)", "none in the browser", checked, "runner-only entry")
    if e.get("network"):
        hosts = ", ".join(e.get("hosts") or [])
        return ("1g", "pyodide + same-origin gateway", "unavailable offline or when the source is down; the runner",
                checked, hosts)
    if eid in WASM_CANDIDATES:
        return ("5", "pyodide", "—", checked, WASM_CANDIDATES[eid])
    pk = e.get("pyodide_packages") or []
    note = f"loads {', '.join(pk)} on demand" if pk else ""
    return ("1", "pyodide", "—", checked, PYODIDE_NOTES.get(eid) or PYODIDE_NOTES_JOBS.get(eid) or note)


def mobile(cls: str, e: dict) -> str:
    if cls == "1j":
        return "not yet: a phone's browser storage and memory are not measured for these jobs"
    if cls in ("1", "1g", "2", "3", "5"):
        extra = " + numpy/scipy on demand" if e.get("pyodide_packages") else ""
        return f"yes: phone CPU (Pyodide ~11 MB once{extra})"
    if cls == "4":
        return "where the model fits"
    return "no: needs the local runner on a computer"


def catalog() -> dict:
    from tcmstudio.catalog import build_catalog
    return build_catalog("browser", probe=False)


def connector_counts(doc: dict | None = None) -> tuple[int, int]:
    """(sources, operations) of the connector entries."""
    items = [e for e in (doc or catalog())["entries"] if e["kind"] == "connector"]
    return len({e["connector"]["key"] for e in items}), len(items)


def rows() -> list[dict]:
    doc = catalog()
    out = []
    for e in sorted(doc["entries"], key=lambda x: x["id"]):
        cls, backends, fallback, validation, notes = classify(e)
        deps = ", ".join([*(e.get("heavy") or []), *(e.get("pyodide_packages") or [])])
        out.append({
            "id": e["id"], "kind": e["kind"], "category": e.get("category", ""), "domain": e.get("domain", "") or "",
            "exec_now": "+".join(e.get("exec") or []), "class": cls, "class_label": CLASS_LABELS[cls],
            "local_backends": backends, "network": "yes" if e.get("network") else "no",
            "offline": "no" if e.get("network") or cls in ("7", "8") else "yes (after first load)",
            "gpu": "yes" if e.get("gpu") else "no", "job": "yes" if e.get("job") else "no",
            "deps": deps, "mobile": mobile(cls, e), "fallback": fallback, "validation": validation, "notes": notes,
        })
    return out


FIELDS = ["id", "kind", "category", "domain", "exec_now", "class", "class_label", "local_backends", "network",
          "offline", "gpu", "job", "deps", "mobile", "fallback", "validation", "notes"]


def to_csv(table: list[dict]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=FIELDS, lineterminator="\n")
    w.writeheader()
    w.writerows(table)
    return buf.getvalue()


# per-family requirements and plans, for the summary (memory, compute, mobile, complexity, priority)
FAMILIES = [
    ("Native tools: clinical calculators, statistics, pharmacology, file formats, variants, survival",
     "native: clinical_calc, study_design, …", "1", "KB; microseconds to milliseconds", "yes",
     "runs in Pyodide, the same Python code as the runner; compared with native CPython in Pyodide only for "
     "benjamini_hochberg, fisher_exact and the TCM lookups (the manual runtime check, [BROWSER_COMPUTE.md §10](../BROWSER_COMPUTE.md#10-validation))",
     "the runtime check's comparisons for every browser entry, in CI"),
    ("Sequence alignment and edit distance", "native.global/local/protein_alignment, native.edit_distance", "2",
     "O(n·m) cells, ≤ 4 M (alignment); 1 byte of traceback per cell", "yes",
     "done, exact; speed-ups measured in docs/benchmarks", "WASM SIMD (anti-diagonal) for a further gain"),
    ("Sequence counts: distance matrices, Hamming, GC windows", "native.distance_matrix, hamming_distance, gc_content",
     "3", "O(n²·w) comparisons; counts handed back are O(n²)",
     "yes (WebGPU only on an adapter not identified as a software renderer)",
     "WebAssembly SIMD and JavaScript kernels exact; WebGPU exact on a software adapter in tests, never run on a "
     "hardware GPU; handing the counts to Python now dominates large matrices",
     "a compact count format across the Pyodide boundary"),
    ("Phylogeny, k-mers, ORFs, rank statistics", "neighbor_joining, upgma, kmer_counts, …", "5",
     "O(n³) (trees), linear (scans)", "yes", "runs in Pyodide; candidates for kernels", "P2"),
    ("Network pharmacology: the limited seed-corpus skill", "skill.analyze-tcm-network-pharmacology", "1",
     "seed corpus only: no permutation null, no topology; trivial CPU", "yes",
     "runs in Pyodide on the 6-formula seed corpus; no test has run it there yet",
     "none needed here: the permutation null is the research loop's (below)"),
    ("Governed TCM skills, clinic, TCM data hub queries, studies", "skill.*(tcm), clinic.*, tcmdb.*, study.*", "1",
     "MB (formula SQLite 12 MB, loaded on demand)", "yes",
     "runs in Pyodide; the pinned safety, normalisation and evidence skills and ranked formula search compared "
     "with native CPython (manual runtime check); hub queries over packaged or imported stores, while building "
     "NPASS, CMAUP or LOTUS needs the runner",
     "semantic retrieval with a packaged embedding model (P2)"),
    ("Public database connectors ({sources} sources, {operations} operations)", "connector.*", "1g",
     "bounded responses", "yes (online)",
     "local parsing and policy, data via the gateway; levels per operation in [connectors.md](connectors.md) (none verified live "
     "through the deployed gateway)", "—"),
    ("Text embeddings, classification, reranking (none exist yet)", "—", "4", "20–120 MB models",
     "small models", "not started: no model is packaged", "P2: one ONNX embedding model, WebNN → WebGPU → WASM"),
    ("Research snapshots and the research loop", "job.research.snapshot, job.research.run", "1j",
     "418 MB of raw files in OPFS (read in place); snapshots of a few MB; minutes of CPU per build",
     "not yet measured",
     "browser jobs running the same Python as the runner (the permutation null is CPython's own MT19937 in "
     "WebAssembly, so it draws exactly what the runner draws); on the planted world the five tables are "
     "compared byte for byte with the native answer (studio/e2e/science.spec.mjs); on the real 葛根芩连汤 files "
     "every export was the native run's bytes in live-data #18 and #19 (2026-10-19), a comparison repeated on every "
     "live-data run",
     "parallel per-source snapshot builds across job workers"),
    ("RNA-seq from a count matrix; single-cell from count tables", "job.pipeline.rnaseq (counts), job.pipeline.scrna",
     "1j", "a count matrix of KB to MB (2,000 genes x 6 samples: under a second natively); count tables of MB (about "
     "1,000 cells: seconds natively); tens of thousands of cells need GBs, so a browser job refuses more than 6,000 "
     "cells, or 100,000 genes or 200 samples, for the runner (wasm32 caps a worker near 4 GB)", "not yet measured",
     "browser jobs running the same Python as the runner with Pyodide's numpy and scipy (the built-in DESeq2 "
     "implementation; the built-in single-cell analysis and Harmony); compared output by output with the native "
     "command on omics_counts_world v1 and sc_world v1 (studio/web/test/runtime/science.mjs --mode omics): identical, "
     "within a stated tolerance (at most 0.133 of it) or, for the UMAP layout and figures, not compared; the genes "
     "called, each cell's cluster and cell type identical; passed in headless Chromium locally and in Studio's CI (studio #91, 2026-10-19)",
     "PyDESeq2, Scanpy, scVI, 10x HDF5 and .h5ad stay on the runner"),
    ("Single-cell as a governed skill", "skill.scrna-cell-atlas", "6", "100 MB–GBs; hours", "no",
     "runner (a skill.run job); on count tables job.pipeline.scrna runs the same pipeline in the browser", "—"),
    ("RNA-seq from FASTQ (quantification), docking, ADMET, structure prediction",
     "job.pipeline.rnaseq (FASTQ)/dock/admet/fold", "7", "GBs; native binaries; GPU", "no", "runner (CPU/GPU)",
     "remote HPC worker for heavy cases"),
    ("Jobs, MCP providers, environment checks", "job.*, system.provider_call, system.doctor", "8", "—", "no",
     "runner", "—"),
]


def summary(table: list[dict]) -> str:
    counts = Counter(r["class"] for r in table)
    kinds = Counter((r["class"], r["kind"]) for r in table)
    sources, operations = connector_counts()
    checked = Counter("UNVERIFIED" if r["validation"].startswith("UNVERIFIED") else "a committed record"
                      for r in table)
    lines = ["# Browser compatibility matrix", "",
             "Generated by `studio/scripts/compat_matrix.py` from the tool catalog; one row per entry in "
             "[`matrix.csv`](matrix.csv). `--check` keeps it current (studio/runner/tests).", "",
             f"Entries: {len(table)}. Running in the browser today (classes 1, 1g, 1j, 2, 3, 5): "
             f"{sum(counts[c] for c in ('1', '1g', '1j', '2', '3', '5'))}; as browser jobs (1j): {counts['1j']}; "
             f"with a local kernel (2, 3): "
             f"{counts['2'] + counts['3']}; on the local runner only (6, 7, 8): {sum(counts[c] for c in ('6', '7', '8'))}.", "",
             f"Validation (the `validation` column) is each entry's committed verification record "
             f"(`entry.verification` in the catalog): {checked['a committed record']} entries have one, "
             f"{checked['UNVERIFIED']} are UNVERIFIED (no record, which is not a failure).", "",
             "| Class | Meaning | Entries | By kind |", "|---|---|---:|---|"]
    for cls, label in CLASS_LABELS.items():
        if not counts[cls]:
            continue
        by = ", ".join(f"{k} {n}" for (c, k), n in sorted(kinds.items()) if c == cls)
        lines.append(f"| {cls} | {label} | {counts[cls]} | {by} |")
    lines += ["", "Class 4 (WebNN) has no entries: no tool runs a neural model in the browser yet. " + WEBNN_NOTE + ".", "",
              "## By family", "",
              "| Family | Entries | Class | Memory / compute | Phones | Status | Next |", "|---|---|---|---|---|---|---|"]
    for fam in FAMILIES:
        lines.append("| " + " | ".join(f.format(sources=sources, operations=operations) for f in fam) + " |")
    lines += ["", "Rows of `matrix.csv`: " + ", ".join(f"`{f}`" for f in FIELDS) + ".", ""]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--check", action="store_true", help="fail when the committed files differ")
    args = ap.parse_args()
    table = rows()
    files = {OUT / "matrix.csv": to_csv(table), OUT / "summary.md": summary(table)}
    if args.check:
        stale = [p for p, text in files.items() if not p.is_file() or p.read_text(encoding="utf-8") != text]
        for p in stale:
            print(f"out of date: {p.relative_to(REPO)} (run python3 studio/scripts/compat_matrix.py)")
        return 1 if stale else 0
    OUT.mkdir(parents=True, exist_ok=True)
    for p, text in files.items():
        p.write_text(text, encoding="utf-8")
        print(f"wrote {p.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
