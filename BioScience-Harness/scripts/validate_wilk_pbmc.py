"""Exercise the built-in single-cell workflow on two published healthy PBMC libraries.

Install BioScience-Harness and rdata==1.0.0. Run this script with the directory
containing the two GEO RDS downloads and a new output directory. Only exonic
counts are used. H1 and H2 are separate donors, each observed in one library.
The author-provided donor identities can be inspected at
https://github.com/ajwilk/2020_Wilk_COVID/blob/a64b19b27664633d5a35d529044ea95fb4bad79d/code/COVID-19_metadata_repo_GEOidents.csv
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import warnings
from collections import Counter
from pathlib import Path

import numpy as np
import rdata
from scipy.io import mmwrite
from scipy.sparse import csc_matrix

from bioagent.omics.scrna import ScConfig, run_scrna, verify_run

LIBRARIES = (
    ("GSM4557334", "HIP002", "aae9cc5d2b0718bd8d654d0050bf46fa174211c3392f4a0e40c1768948a4e178"),
    ("GSM4557335", "HIP015", "02cc556361d706a4faec7bb4ac381fd612a0071429b5914d2d5a0507e15e0bc8"),
)
CELLS_PER_LIBRARY = 256
MIN_GENES = 200


def prepare(cache: Path, out: Path, accession: str, donor: str, digest: str) -> dict:
    filename = f"{accession}_{donor}_cell.counts.matrices.rds.gz"
    raw = cache / filename
    url = f"https://ftp.ncbi.nlm.nih.gov/geo/samples/GSM4557nnn/{accession}/suppl/{filename}"
    if not raw.is_file():
        raise FileNotFoundError(f"Download {url} to {raw}")
    if hashlib.sha256(raw.read_bytes()).hexdigest() != digest:
        raise ValueError(f"GEO file failed SHA-256 verification: {raw}")
    # rdata preserves dgCMatrix slots in a namespace when a class constructor is absent.
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message="Missing constructor for R class.*", category=UserWarning)
        slots = rdata.read_rds(raw)["exon"]
    matrix = csc_matrix((slots.x, slots.i, slots.p), shape=tuple(slots.Dim))
    features, barcodes = slots.Dimnames
    if np.any(matrix.data <= 0) or np.any(matrix.data != matrix.data.astype(np.int64)):
        raise ValueError(f"{raw}: expected positive integer entries in the exonic count matrix")
    if len(set(features)) != matrix.shape[0] or len(set(barcodes)) != matrix.shape[1]:
        raise ValueError(f"{raw}: duplicated or missing feature/barcode identifiers")
    # Rank genuine barcodes independently of expression and cell type after the detection floor.
    eligible = np.flatnonzero(np.diff(matrix.indptr) >= MIN_GENES)
    if len(eligible) < CELLS_PER_LIBRARY:
        raise ValueError(f"{raw}: too few eligible barcodes")
    chosen = sorted(eligible, key=lambda j: hashlib.sha256(barcodes[j].encode()).digest())[:CELLS_PER_LIBRARY]
    dest = out / accession
    dest.mkdir()
    mmwrite(dest / "matrix.mtx", matrix[:, chosen].astype(np.int32), symmetry="general")
    (dest / "features.tsv").write_text("".join(f"{g}\t{g}\tGene Expression\n" for g in features))
    (dest / "barcodes.tsv").write_text("".join(f"{barcodes[j]}\n" for j in chosen))
    return {"accession": accession, "donor": donor, "source_url": url, "sha256": digest,
            "source_barcodes": len(barcodes), "source_features": len(features),
            "at_least_200_genes": len(eligible), "sampled": len(chosen)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cache", type=Path)
    parser.add_argument("out", type=Path)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    sources = [prepare(args.cache, args.out, *lib) for lib in LIBRARIES]
    sheet = args.out / "samples.tsv"
    with sheet.open("w", newline="") as fh:
        writer = csv.writer(fh, delimiter="\t")
        writer.writerow(("sample", "path", "donor", "library", "condition"))
        for accession, donor, _ in LIBRARIES:
            writer.writerow((accession, accession, donor, accession, "healthy"))
    config = ScConfig(min_genes=MIN_GENES, n_top_genes=1000, n_pcs=20, doublets="flag",
                      batch_key="library", integration_method="none", seed=2026)
    run = run_scrna(sheet, config, args.out / "analysis")
    ok, problems = verify_run(run.out_dir)
    with (run.out_dir / "cells.tsv").open() as fh:
        rows = list(csv.DictReader(fh, delimiter="\t"))
    retained = Counter(row["sample"] for row in rows)
    if not run.complete or not ok or set(retained) != {lib[0] for lib in LIBRARIES}:
        raise RuntimeError(f"Incomplete run or missing library: {problems}; retained = {retained}")
    report = {"dataset": "GSE150728", "sources": sources, "config": config.as_dict(),
              "summary": run.summary(), "cells_per_library_after_qc": retained,
              "verification": {"ok": ok, "problems": problems}, "warnings": run.warnings,
              "scope": "Two healthy donors, each observed in one library. No condition contrast."}
    (args.out / "validation.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"retained": retained, "cells": run.n_cells, "genes": run.n_genes,
                      "clusters": len(run.cell_types), "verified": ok}))


if __name__ == "__main__":
    main()
