"""Command-line entry points for the omics pipelines (``bioagent rnaseq ...``).

Registered on the main parser by :func:`register`; :func:`dispatch` runs the command
when it is one of these.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

__all__ = ["register", "dispatch", "COMMANDS"]

COMMANDS = ("rnaseq",)


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


def dispatch(a: argparse.Namespace) -> int | None:
    if a.cmd == "rnaseq":
        return _rnaseq(a)
    return None


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
