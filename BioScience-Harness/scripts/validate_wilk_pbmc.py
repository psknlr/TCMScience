"""Exercise the built-in single-cell workflow on healthy Wilk et al. PBMC libraries.

Install BioScience-Harness and rdata==1.0.0. Run this script with the directory
containing the GEO RDS downloads and a new output directory. The default run
uses H1 and H2. --holdout runs only H3. Each donor provided one library.
For the optional comparison install h5py and pass --author-h5ad the versioned
author-annotated asset at
https://datasets.cellxgene.cziscience.com/8d7fad46-2f37-4aa0-ac9d-babbc36efb6f.h5ad
Only five metadata columns are read. Scores use the original exonic GEO counts.
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
from importlib.metadata import version
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
HOLDOUT = (
    "GSM4557336", "HIP023", "cba7b8602cd7db3887d686be905800a8b50ad9639b119168a597444fdf9c26ed",
)
REFERENCE_VERSION = "8d7fad46-2f37-4aa0-ac9d-babbc36efb6f"
# Hash the five extracted columns in deposited row order per donor.
AUTHOR_METADATA = {
    "HIP002": ("H1", "3c38377713efa2e1e997b864a341a332aef9f2b3edf990033107130c36db96d6"),
    "HIP015": ("H2", "4564cdb36d1e3352bc76391fe62f1bed9c1224ad72f184b84aee1a824a963f1e"),
    "HIP023": ("H3", "06d416f2b822399ac9c5786eaea3836809970352fd46b9af021617e4efd05c1d"),
}
AUTHOR_LINEAGE = {
    "B": "B", "CD14 Monocyte": "monocyte", "CD16 Monocyte": "monocyte",
    "CD4 T": "T", "CD8 T": "T", "gd T": "T", "DC": "dendritic", "pDC": "dendritic",
    "Granulocyte": "granulocyte", "RBC": "erythroid", "Platelet": "platelet",
    "NK": "NK", "PB": "plasma",
}
PANEL_LINEAGE = {
    "B cell": "B", "Plasma cell": "plasma", "CD14+ monocyte": "monocyte",
    "FCGR3A+ monocyte": "monocyte", "Naive CD4+ T cell": "T", "Memory CD4+ T cell": "T",
    "CD8+ T cell": "T", "Regulatory T cell": "T", "NK cell": "NK",
    "Conventional dendritic cell": "dendritic", "Plasmacytoid dendritic cell": "dendritic",
    "Neutrophil": "granulocyte", "Platelet": "platelet", "Erythrocyte": "erythroid",
}
CELLS_PER_LIBRARY = 256
MIN_GENES = 200


def read_author_obs(path: Path, libraries: tuple) -> dict:
    import h5py

    with h5py.File(path, "r") as handle:
        obs = handle["obs"]
        ids = obs["_index"].asstr()[:]
        donors = obs["donor_id/categories"].asstr()[:][obs["donor_id/codes"][:]]
        labels = obs["cell.type.coarse/categories"].asstr()[:][obs["cell.type.coarse/codes"][:]]
        counts, features = obs["nCount_RNA"][:], obs["nFeature_RNA"][:]
    result = {}
    for _, donor, _ in libraries:
        author_id, expected = AUTHOR_METADATA[donor]
        donor_rows = np.flatnonzero(donors == author_id)
        if any(np.any(v[donor_rows] != np.floor(v[donor_rows])) for v in (counts, features)):
            raise ValueError(f"{path}: fractional or missing deposited QC counts for {author_id}")
        rows = [(ids[i], donors[i], labels[i], int(counts[i]), int(features[i])) for i in donor_rows]
        content = json.dumps(rows, separators=(",", ":"), ensure_ascii=True).encode()
        if hashlib.sha256(content).hexdigest() != expected:
            raise ValueError(f"{path}: author metadata for {author_id} differs from {REFERENCE_VERSION}")
        result[donor] = rows
    return result


def author_barcodes(matrix, features, barcodes, accession, donor, rows):
    # EpicTools EpicPreHS assigns cell ordinals after these filters, in file order.
    # https://github.com/ajwilk/EpicTools/blob/4cc68f2c798d0f1d7ba52a4325812005b4591993/R/EpicMerge.R#L190-L222
    counts = np.asarray(matrix.sum(axis=0)).ravel()
    detected = np.diff(matrix.indptr)
    keep = (counts >= 1000) & (counts <= 15000) & (detected < 0.75 * counts)
    for prefixes in (("MT-", "MTR"), ("RPL", "RPS")):
        index = [i for i, name in enumerate(features) if name.startswith(prefixes)]
        subtotal = np.asarray(matrix[index].sum(axis=0)).ravel()
        keep &= subtotal < 0.2 * counts
    original = np.flatnonzero(keep)
    if any(not row[0].startswith(donor + ".") for row in rows):
        raise ValueError(f"{donor}: unexpected deposited cell identifier")
    ordinals = np.array([int(row[0].split(".")[1]) - 1 for row in rows])
    if min(ordinals) < 0 or max(ordinals) >= len(original) or len(set(ordinals)) != len(rows):
        raise ValueError(f"{donor}: invalid author ordinal")
    cells = original[ordinals]
    count_r = float(np.corrcoef(counts[cells], [row[3] for row in rows])[0, 1])
    feature_r = float(np.corrcoef(detected[cells], [row[4] for row in rows])[0, 1])
    if not (count_r >= 0.999 and feature_r >= 0.999):
        raise ValueError(f"{donor}: reconstructed cell identity disagrees with deposited QC")
    labels = {f"{accession}:{barcodes[i]}": (row[0], row[2]) for i, row in zip(cells, rows)}
    diagnostics = {"author_metadata_sha256": AUTHOR_METADATA[donor][1],
                   "author_cells": len(rows), "epicprehs_barcodes": len(original),
                   "nCount_RNA_pearson_r": count_r, "nFeature_RNA_pearson_r": feature_r}
    return labels, diagnostics


def prepare(cache: Path, out: Path, accession: str, donor: str, digest: str,
            author_rows: list | None) -> tuple[dict, dict]:
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
    source = {"accession": accession, "donor": donor, "source_url": url, "sha256": digest,
              "source_barcodes": len(barcodes), "source_features": len(features),
              "at_least_200_genes": len(eligible), "sampled": len(chosen)}
    reference = {}
    if author_rows is not None:
        reference, source["barcode_validation"] = author_barcodes(
            matrix, features, barcodes, accession, donor, author_rows)
    return source, reference


def comparison(rows, reference, decisions):
    matched = [row for row in rows if row["cell"] in reference]
    if not matched:
        raise ValueError("No retained cells match the deposited author annotations")
    result = {"retained": len(rows), "matched_author_labels": len(matched),
              "retained_without_author_label": len(rows) - len(matched)}
    for rule in ("current", "median_only"):
        confusion = {}
        assigned_retained = assigned_matched = concordant = 0
        for row in rows:
            label = row["cell_type"]
            decision = decisions[row["cluster"]]
            if rule == "median_only" and decision["reason"] == "minority_support":
                label = decision["candidate"]
            assigned_retained += label != "unassigned"
            if row["cell"] not in reference:
                continue
            _, author = reference[row["cell"]]
            confusion.setdefault(label, Counter())[author] += 1
            assigned_matched += label != "unassigned"
            concordant += PANEL_LINEAGE.get(label) == AUTHOR_LINEAGE[author]
        result[rule] = {"assigned_retained": assigned_retained, "assigned_matched": assigned_matched,
                        "unassigned_matched": len(matched) - assigned_matched,
                        "broad_lineage_concordant": concordant,
                        "broad_concordance_among_matched_percent": 100 * concordant / len(matched),
                        "broad_concordance_among_assigned_matched_percent":
                            100 * concordant / assigned_matched if assigned_matched else None,
                        "panel_call_by_author_coarse_label": confusion}
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cache", type=Path)
    parser.add_argument("out", type=Path)
    parser.add_argument("--holdout", action="store_true", help="Use only held-out healthy donor H3")
    parser.add_argument("--author-h5ad", type=Path, help="Pinned CELLxGENE H5AD with author labels")
    args = parser.parse_args()
    libraries = (HOLDOUT,) if args.holdout else LIBRARIES
    annotations = read_author_obs(args.author_h5ad, libraries) if args.author_h5ad else {}
    args.out.mkdir(parents=True, exist_ok=False)
    sources, reference = [], {}
    for lib in libraries:
        source, labels = prepare(args.cache, args.out, *lib, annotations.get(lib[1]))
        sources.append(source)
        reference.update(labels)
    sheet = args.out / "samples.tsv"
    with sheet.open("w", newline="") as fh:
        writer = csv.writer(fh, delimiter="\t")
        writer.writerow(("sample", "path", "donor", "library", "condition"))
        for accession, donor, _ in libraries:
            writer.writerow((accession, accession, donor, accession, "healthy"))
    config = ScConfig(min_genes=MIN_GENES, n_top_genes=1000, n_pcs=20, doublets="flag",
                      batch_key="library", integration_method="none", seed=2026)
    run = run_scrna(sheet, config, args.out / "analysis")
    ok, problems = verify_run(run.out_dir)
    with (run.out_dir / "cells.tsv").open() as fh:
        rows = list(csv.DictReader(fh, delimiter="\t"))
    retained = Counter(row["sample"] for row in rows)
    if not run.complete or not ok or set(retained) != {lib[0] for lib in libraries}:
        raise RuntimeError(f"Incomplete run or missing library: {problems}; retained = {retained}")
    packages = ["numpy", "scipy", "rdata"]
    if args.author_h5ad:
        packages.append("h5py")
    report = {"dataset": "GSE150728", "sources": sources, "config": config.as_dict(),
              "versions": {pkg: version(pkg) for pkg in packages},
              "summary": {**run.summary(), "out_dir": run.out_dir.relative_to(args.out).as_posix()},
              "cells_per_library_after_qc": retained,
              "verification": {"ok": ok, "problems": problems}, "warnings": run.warnings,
              "cohort": "held-out healthy donor H3" if args.holdout else "exploratory healthy donors H1/H2",
              "scope": "One library per donor. No condition contrast."}
    if args.author_h5ad:
        decisions = run.annotation.decisions
        report["author_comparison"] = {
            "cellxgene_version": REFERENCE_VERSION, "reference_column": "cell.type.coarse",
            "author_lineage_mapping": AUTHOR_LINEAGE, "panel_lineage_mapping": PANEL_LINEAGE,
            "all": comparison(rows, reference, decisions),
            "by_library": {s: comparison([r for r in rows if r["sample"] == s], reference, decisions)
                           for s in retained},
            "interpretation": "Author annotations from the same experiment are a comparative reference. "
                              "The median-only comparison retains the same inputs and clusters."}
    (args.out / "validation.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"retained": retained, "cells": run.n_cells, "genes": run.n_genes,
                      "clusters": len(run.cell_types), "verified": ok}))


if __name__ == "__main__":
    main()
