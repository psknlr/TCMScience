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
  2  converted: a JavaScript CPU kernel computes the hot loop, byte-identical to Python (checked)
  3  WebGPU: integer reductions on the GPU, exact, with CPU recounts (and 2 as its fallback)
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
    "2": "browser: JavaScript CPU kernel",
    "3": "browser: WebGPU (exact) + CPU kernel",
    "4": "WebNN candidate",
    "5": "WASM SIMD/threads candidate",
    "6": "substantial work for the browser",
    "7": "native engine or GPU framework (runner)",
    "8": "local runner only",
}

# the tools whose hot loop has a JavaScript kernel (studio/web/js/compute/kernels.js)
KERNEL = {
    "native.global_alignment": "Needleman–Wunsch",
    "native.local_alignment": "Smith–Waterman",
    "native.protein_alignment": "Needleman–Wunsch / Smith–Waterman (BLOSUM62)",
    "native.edit_distance": "Levenshtein",
}
# … and those that also have the WebGPU count kernel (studio/web/js/runtime/webgpu.js)
GPU = {
    "native.distance_matrix": "pairwise site/difference/transition counts",
    "native.hamming_distance": "mismatch count",
    "native.gc_content": "GC counts (windows: CPU prefix sums)",
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
    "skill.analyze-tcm-network-pharmacology": "seeded degree-matched permutation null (1000 per pathway): "
                                              "the most data-parallel CPU work in the catalog; a port must "
                                              "reproduce Python's Mersenne Twister draws exactly",
}
# job and skill details: what blocks the browser, with the Pyodide 314 lockfile in mind
RUNNER_REASONS = {
    "job.pipeline.rnaseq": ("7", "salmon / kallisto / hisat2 / fastp are native binaries; the builtin engine is "
                                 "numpy/scipy (in Pyodide) but a transcriptome index needs GBs of memory"),
    "job.pipeline.scrna": ("6", "the builtin backend is numpy/scipy (in Pyodide) and could run small datasets; "
                                "scanpy, harmony and scvi (torch) are not in Pyodide; files and hours-long runs need a job model"),
    "job.pipeline.fold": ("7", "ESMFold needs torch + transformers (not in Pyodide) and a GPU; ESM Atlas / ColabFold are "
                               "remote services (allow_remote)"),
    "job.pipeline.dock": ("7", "AutoDock Vina, meeko, gemmi and rdkit are native and not in Pyodide"),
    "job.pipeline.admet": ("7", "rdkit is not in Pyodide (scikit-learn is)"),
    "job.research.run": ("6", "pyarrow is in Pyodide 314; the blocker is the data lake snapshot ledger and a long job"),
    "job.skill.run": ("8", "runs a governed skill as a subprocess job"),
    "job.tcmdb.fetch": ("8", "downloads full third-party databases to disk"),
    "job.tcmdb.build": ("8", "builds SQLite stores from those downloads"),
    "skill.retrieve-literature-evidence": ("7", "PaperQA2 and its model calls are not a browser package"),
    "skill.dock-ligands": ("7", "the docking engines above"),
    "skill.predict-admet": ("7", "rdkit"),
    "skill.predict-protein-structure": ("7", "torch / transformers and a GPU, or a remote structure service"),
    "skill.rnaseq-differential-expression": ("7", "the RNA-seq pipeline above"),
    "skill.scrna-cell-atlas": ("6", "the single-cell pipeline above"),
    "system.provider_call": ("8", "ToolUniverse / BioMCP run as local MCP processes"),
    "system.job_status": ("8", "follows runner jobs"),
    "system.doctor": ("8", "inspects the runner's Python environment"),
}
WEBNN_NOTE = ("No model ships with the site; a packaged ONNX model (embeddings for TCM terms, a text classifier) "
              "could run here through ONNX Runtime Web's WebNN, WebGPU or WASM providers")


def classify(e: dict) -> tuple[str, str, str, str, str]:
    """→ (class, local backends in order, fallback, validation, notes)."""
    eid, exec_ = e["id"], e.get("exec") or []
    if eid in GPU:
        return ("3", "webgpu → js kernel → pyodide", "the CPU kernel, then the original Python",
                "exact integer counts; packet bound to the input by SHA-256; Python recounts sampled records; "
                "fixtures + randomized end-to-end parity", GPU[eid])
    if eid in KERNEL:
        return ("2", "js kernel → pyodide", "the original Python",
                "byte-identical output (score bits and int/float type); Python re-checks every alignment; "
                "fixtures + randomized end-to-end parity", KERNEL[eid])
    if eid in RUNNER_REASONS:
        cls, why = RUNNER_REASONS[eid]
        return (cls, "runner (native CPython" + (", GPU" if e.get("gpu") else "") + ")",
                "none in the browser: the call says what it needs", "runner tests", why)
    if "browser" not in exec_:
        return ("8", "runner (native CPython)", "none in the browser", "runner tests", "runner-only entry")
    if e.get("network"):
        hosts = ", ".join(e.get("hosts") or [])
        return ("1g", "pyodide + same-origin gateway", "unavailable offline or when the source is down; the runner",
                "request templates reviewed; edge validates hosts, methods, sizes", hosts)
    if eid in WASM_CANDIDATES:
        return ("5", "pyodide", "—", "byte-identical to native CPython (runtime check)", WASM_CANDIDATES[eid])
    pk = e.get("pyodide_packages") or []
    note = f"loads {', '.join(pk)} on demand" if pk else ""
    return ("1", "pyodide", "—", "byte-identical to native CPython (runtime check)", note)


def mobile(cls: str, e: dict) -> str:
    if cls in ("1", "1g", "2", "3", "5"):
        extra = " + numpy/scipy on demand" if e.get("pyodide_packages") else ""
        return f"yes: phone CPU (Pyodide ~11 MB once{extra})"
    if cls == "4":
        return "where the model fits"
    return "no: needs the local runner on a computer"


def rows() -> list[dict]:
    from tcmstudio.catalog import build_catalog
    doc = build_catalog("browser", probe=False)
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
     "done (runs in Pyodide, outputs identical to native CPython)", "—"),
    ("Sequence alignment and edit distance", "native.global/local/protein_alignment, native.edit_distance", "2",
     "O(n·m) cells, ≤ 4 M (alignment); 1 byte of traceback per cell", "yes",
     "done, exact; speed-ups measured in docs/benchmarks", "WASM SIMD (anti-diagonal) for a further gain"),
    ("Sequence counts: distance matrices, Hamming, GC windows", "native.distance_matrix, hamming_distance, gc_content",
     "3", "O(n²·w) comparisons; counts handed back are O(n²)", "yes (GPU where present)",
     "done: WebGPU exact + CPU kernel; result handling now dominates large matrices", "WASM SIMD byte compares"),
    ("Phylogeny, k-mers, ORFs, rank statistics", "neighbor_joining, upgma, kmer_counts, …", "5",
     "O(n³) (trees), linear (scans)", "yes", "runs in Pyodide; candidates for kernels", "P2"),
    ("Network pharmacology (permutation null)", "skill.analyze-tcm-network-pharmacology", "5",
     "1000 permutations × pathways; Brandes betweenness", "yes", "runs in Pyodide", "P1: exact MT19937 port"),
    ("Governed TCM skills, clinic, TCM data hub queries, studies", "skill.*(tcm), clinic.*, tcmdb.*, study.*", "1",
     "MB (formula SQLite 12 MB, loaded on demand)", "yes", "done", "ranked multi-term formula retrieval (P1)"),
    ("Public database connectors (119 sources, 444 operations)", "connector.*", "1g",
     "bounded responses", "yes (online)", "done: local parsing and policy, data via the gateway", "—"),
    ("Text embeddings, classification, reranking (none exist yet)", "—", "4", "20–120 MB models",
     "small models", "not started: no model is packaged", "P2: one ONNX embedding model, WebNN → WebGPU → WASM"),
    ("Single-cell and research loop", "job.pipeline.scrna, job.research.run", "6",
     "100 MB–GBs; hours", "no", "runner", "small-dataset browser mode (P3)"),
    ("RNA-seq quantification, docking, ADMET, structure prediction", "job.pipeline.rnaseq/dock/admet/fold", "7",
     "GBs; native binaries; GPU", "no", "runner (CPU/GPU)", "remote HPC worker for heavy cases"),
    ("Jobs, MCP providers, environment checks", "job.*, system.provider_call, system.doctor", "8", "—", "no",
     "runner", "—"),
]


def summary(table: list[dict]) -> str:
    counts = Counter(r["class"] for r in table)
    kinds = Counter((r["class"], r["kind"]) for r in table)
    lines = ["# Browser compatibility matrix", "",
             "Generated by `studio/scripts/compat_matrix.py` from the tool catalog; one row per entry in "
             "[`matrix.csv`](matrix.csv). `--check` keeps it current (studio/runner/tests).", "",
             f"Entries: {len(table)}. Running in the browser today (classes 1, 1g, 2, 3, 5): "
             f"{sum(counts[c] for c in ('1', '1g', '2', '3', '5'))}; with a local kernel (2, 3): "
             f"{counts['2'] + counts['3']}; on the local runner only (6, 7, 8): {sum(counts[c] for c in ('6', '7', '8'))}.", "",
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
        lines.append("| " + " | ".join(fam) + " |")
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
