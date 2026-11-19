"""Compare the two bulk DE backends on Bioconductor's public pasilla count matrix.

Pass pasilla_gene_counts.tsv, pasilla_sample_annotation.csv, and a new output directory.
The source commit, data digests and interpretation are in docs/omics-backends.md.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path

import numpy as np
from scipy.stats import spearmanr

from bioagent.omics.rnaseq_counts import RNASeqCountsConfig, run_rnaseq_counts

COUNT_SHA = "ea0dafbfcc600559644cfe7dd5cc8de809d631eb64ba3089aaa25c2fa0954dad"
SAMPLE_SHA = "1844f871ca5d750f41685c73b87bd7dda291ec0d52304c1afb46a17ae88db745"


def compare(counts: Path, annotation: Path, out: Path) -> dict:
    for path, digest in ((counts, COUNT_SHA), (annotation, SAMPLE_SHA)):
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError(f"{path} does not match the published validation input")
    out.mkdir(parents=True, exist_ok=False)
    samples = out / "samples.tsv"
    with annotation.open() as src, samples.open("w", newline="") as dest:
        writer = csv.writer(dest, delimiter="\t")
        writer.writerow(("sample", "condition", "library_type"))
        for row in csv.DictReader(src):
            writer.writerow((row["file"].removesuffix("fb"), row["condition"], row["type"]))
    results = []
    for backend in ("builtin", "pydeseq2"):
        config = RNASeqCountsConfig(de_backend=backend, design="~ library_type + condition",
                                    contrast=("condition", "treated", "untreated"))
        run = run_rnaseq_counts(counts, samples, config, out / backend)
        if not run.complete:
            raise RuntimeError(f"{backend} did not complete; see {out / backend}")
        results.append(run.result)
    native, independent = results
    if native.genes != independent.genes or native.samples != independent.samples:
        raise ValueError("backends returned different gene or sample order")
    paired = np.isfinite(native.p_adjusted) & np.isfinite(independent.p_adjusted)
    native_hits = native.p_adjusted < 0.05
    other_hits = independent.p_adjusted < 0.05
    shared = native_hits & other_hits
    return {"design": native.design, "contrast": native.contrast, "samples": len(native.samples),
            "genes": len(native.genes), "both_adjusted_p_finite": int(paired.sum()),
            "lfc_spearman_on_those_genes": float(spearmanr(native.log2_fold_change[paired],
                                                          independent.log2_fold_change[paired]).statistic),
            "versions": {r.backend: r.version for r in results},
            "q_below_0.05": {"builtin": int(native_hits.sum()), "pydeseq2": int(other_hits.sum()),
                             "both": int(shared.sum()),
                             "both_with_same_direction": int(np.count_nonzero(
                                 shared & (np.sign(native.log2_fold_change)
                                           == np.sign(independent.log2_fold_change))))}}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("counts", "annotation", "out"):
        parser.add_argument(name, type=Path)
    args = parser.parse_args()
    report = compare(args.counts, args.annotation, args.out)
    (args.out / "comparison.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(json.dumps(report, indent=2))
