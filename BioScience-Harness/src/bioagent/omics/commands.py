"""Command-line entry points for the omics pipelines (``bioagent rnaseq``, ``scrna``).

Registered on the main parser by :func:`register`; :func:`dispatch` runs the command
when it is one of these.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

__all__ = ["register", "dispatch", "COMMANDS"]

COMMANDS = ("rnaseq", "scrna")


def register(sub: argparse._SubParsersAction) -> None:
    r = sub.add_parser("rnaseq", help="RNA-seq from FASTQ to a differential-expression "
                                      "report (QC, trimming, quantification, DESeq2)")
    r.add_argument("--samples", default="",
                   help="sample sheet: sample,fastq_1,fastq_2 and the design columns")
    r.add_argument("--transcripts", default="", help="transcriptome FASTA")
    r.add_argument("--annotation", default="",
                   help="GTF, or a transcript,gene[,name] table (gene-level results)")
    r.add_argument("--genome", default="", help="genome FASTA, for --engine hisat2")
    r.add_argument("--design", default="~ condition", help="e.g. '~ batch + condition'")
    r.add_argument("--contrast", default="",
                   help="factor,numerator,denominator, e.g. condition,treated,control "
                        "(default: the last design factor's two levels)")
    r.add_argument("--covariate", action="append", default=[],
                   help="a numeric design variable; every other one is a factor")
    r.add_argument("--engine", default="auto",
                   choices=("auto", "builtin", "salmon", "kallisto", "hisat2"))
    r.add_argument("--trimmer", default="auto", choices=("auto", "builtin", "fastp", "none"))
    r.add_argument("--alpha", type=float, default=0.05, help="FDR level")
    r.add_argument("--fragment-mean", type=float, default=200.0,
                   help="single-end reads: assumed fragment length")
    r.add_argument("--fragment-sd", type=float, default=30.0)
    r.add_argument("--strand", type=int, default=0, choices=(0, 1, 2),
                   help="featureCounts strandedness (hisat2 engine)")
    r.add_argument("--threads", type=int, default=2)
    r.add_argument("--out", default="", help="output directory")
    r.add_argument("--verify", default="",
                   help="re-check the digests of a finished run directory and exit")
    r.add_argument("--json", action="store_true", help="print the summary as JSON")


    c = sub.add_parser("scrna", help="single-cell RNA-seq from count matrices to annotated "
                                     "clusters (QC, doublets, Harmony, Leiden, markers, "
                                     "trajectories, pseudobulk)")
    c.add_argument("source", nargs="?", default="",
                   help="a sample sheet (sample,path[,condition,batch]) or one count matrix "
                        "(10x directory, .h5, .h5ad, CSV/TSV)")
    c.add_argument("--out", default="", help="output directory")
    c.add_argument("--doublets", default="remove", choices=("remove", "flag", "off"))
    c.add_argument("--expected-doublet-rate", type=float, default=0.06)
    c.add_argument("--min-genes", type=int, default=200)
    c.add_argument("--n-top-genes", type=int, default=2000)
    c.add_argument("--n-pcs", type=int, default=30)
    c.add_argument("--batch-key", default="auto",
                   help="sample-sheet column to integrate over (auto: batch, else sample)")
    c.add_argument("--no-integration", action="store_true", help="skip Harmony")
    c.add_argument("--resolution", type=float, default=1.0, help="Leiden resolution")
    c.add_argument("--markers", default="", help="a marker panel (JSON or cell_type,gene CSV)")
    c.add_argument("--root", default="",
                   help="root cluster or cell type for diffusion pseudotime")
    c.add_argument("--contrast", default="",
                   help="factor,numerator,denominator for pseudobulk (default: the "
                        "condition column's two levels)")
    c.add_argument("--seed", type=int, default=0)
    c.add_argument("--verify", default="", help="re-check a finished run directory and exit")
    c.add_argument("--json", action="store_true")


def dispatch(a: argparse.Namespace) -> int | None:
    if a.cmd == "rnaseq":
        return _rnaseq(a)
    if a.cmd == "scrna":
        return _scrna(a)
    return None


def _scrna(a: argparse.Namespace) -> int:
    from .scrna import ScConfig, ScError, run_scrna, verify_run

    if a.verify:
        ok, problems = verify_run(a.verify)
        print("verified: every input and output matches run.json" if ok
              else "NOT VERIFIED:\n" + "\n".join(f"  - {p}" for p in problems))
        return 0 if ok else 1
    if not (a.source and a.out):
        print("a source and --out are required (or --verify DIR)", file=sys.stderr)
        return 2
    contrast = None
    if a.contrast:
        parts = [p.strip() for p in a.contrast.split(",")]
        if len(parts) != 3:
            print("--contrast is factor,numerator,denominator", file=sys.stderr)
            return 2
        contrast = (parts[0], parts[1], parts[2])
    config = ScConfig(min_genes=a.min_genes, doublets=a.doublets,
                      expected_doublet_rate=a.expected_doublet_rate,
                      n_top_genes=a.n_top_genes, n_pcs=a.n_pcs, batch_key=a.batch_key,
                      integrate="none" if a.no_integration else "harmony",
                      resolution=a.resolution, markers=a.markers or None,
                      root=a.root or None, contrast=contrast, seed=a.seed)
    try:
        run = run_scrna(a.source, config, a.out)
    except (ScError, ValueError) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 1
    summary = run.summary()
    if a.json:
        print(json.dumps(summary, indent=1, ensure_ascii=False, default=str))
        return 0
    print(f"{summary['cells']:,} cells, {summary['genes']:,} genes, "
          f"{len(summary['clusters'])} clusters")
    for c, t in summary["cell_types"].items():
        print(f"  cluster {c}: {t} ({summary['clusters'][c]} cells)")
    for w in run.warnings:
        print(f"warning: {w}")
    print(f"report: {Path(a.out) / 'report.html'}")
    return 0


def _rnaseq(a: argparse.Namespace) -> int:
    from .rnaseq import RNASeqConfig, RNASeqError, run_rnaseq, verify_run

    if a.verify:
        ok, problems = verify_run(a.verify)
        print("verified: every input and output matches run.json" if ok
              else "NOT VERIFIED:\n" + "\n".join(f"  - {p}" for p in problems))
        return 0 if ok else 1
    if not (a.samples and a.out):
        print("--samples and --out are required (or --verify DIR)", file=sys.stderr)
        return 2
    contrast = None
    if a.contrast:
        parts = [p.strip() for p in a.contrast.split(",")]
        if len(parts) != 3:
            print("--contrast is factor,numerator,denominator", file=sys.stderr)
            return 2
        contrast = (parts[0], parts[1], parts[2])
    config = RNASeqConfig(
        transcripts=a.transcripts or None, annotation=a.annotation or None,
        genome=a.genome or None, design=a.design, contrast=contrast, engine=a.engine,
        trimmer=a.trimmer, alpha=a.alpha, fragment_mean=a.fragment_mean,
        fragment_sd=a.fragment_sd, covariates=tuple(a.covariate), strand=a.strand,
        threads=a.threads)
    try:
        run = run_rnaseq(a.samples, config, a.out)
    except (RNASeqError, ValueError, RuntimeError) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 1
    summary = run.summary()
    if a.json:
        print(json.dumps(summary, indent=1, ensure_ascii=False, default=str))
        return 0
    de = summary["differential_expression"]
    print(f"{len(run.samples)} samples, engine {run.engine}, trimming {run.trimmer}")
    print(f"{de['tested']} genes tested: {de['significant']} differ at FDR < {de['alpha']:g} "
          f"({de['up']} up, {de['down']} down in {run.contrast[1]} vs {run.contrast[2]})")
    for w in run.warnings:
        print(f"warning: {w}")
    print(f"report: {Path(a.out) / 'report.html'}")
    return 0
