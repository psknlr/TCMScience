"""A simulated single-cell experiment with a known answer, for the single-cell tests.

Four samples (two control, two treated) in two batches, as Cell Ranger directories.
Five populations: four cell types marked by canonical genes of real types (B cells,
CD14+ monocytes, NK cells, platelets) and a path of cells running from the NK-like to
the platelet-like state with a known position t. Counts are negative binomial
(dispersion 0.2) around per-gene means scaled by each cell's library size. On top:

* a batch effect: in each batch, 15% of genes shifted by a log-normal factor;
* a condition effect: in treated samples, 20 genes three-fold up in B cells only;
* 5% low-quality cells (a sixth of the library, 40% mitochondrial reads);
* 6% doublets, each the sum of two cells of different types.
"""

from __future__ import annotations

import gzip
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from scipy import io as spio
from scipy import sparse

TYPES = {
    "B": ["MS4A1", "CD79A", "CD79B", "CD19", "BANK1"],
    "Mono": ["CD14", "LYZ", "S100A8", "S100A9", "VCAN"],
    "NK": ["GNLY", "NKG7", "KLRD1", "KLRF1", "PRF1"],
    "Plt": ["PPBP", "PF4", "GP9", "ITGA2B"],
}
EXPECTED = {"B": "B cell", "Mono": "CD14+ monocyte", "NK": "NK cell", "Plt": "Platelet"}
MITO = [f"MT-{g}" for g in ("ND1", "ND2", "CO1", "CO2", "ATP8", "ATP6", "CO3", "ND3",
                             "ND4L", "ND4", "ND5", "ND6", "CYB")]


@dataclass
class ScWorld:
    root: Path
    sheet: Path
    genes: list[str]
    truth: dict[str, dict[str, object]]          # barcode "sample:cell" -> truth
    condition_genes: list[str]


def make_sc_world(root: Path, *, seed: int = 0, n_genes: int = 1200, per_type: int = 120,
                  path_cells: int = 160) -> ScWorld:
    rng = np.random.default_rng(seed)
    root.mkdir(parents=True, exist_ok=True)
    named = [g for v in TYPES.values() for g in v]
    filler = [f"GENE{i:04d}" for i in range(n_genes - len(named) - len(MITO))]
    genes = named + MITO + filler
    g = len(genes)
    base = rng.lognormal(-1.0, 1.2, g)
    base[[genes.index(m) for m in MITO]] = rng.lognormal(0.5, 0.3, len(MITO))
    base /= base.sum()
    profiles = {}
    filler_idx = np.arange(len(named) + len(MITO), g)
    taken = set()
    for k, (t, markers) in enumerate(TYPES.items()):
        p = base.copy()
        extra = [j for j in rng.choice(filler_idx, 40, replace=False) if j not in taken][:20]
        taken.update(extra)
        for j in [genes.index(m) for m in markers] + extra:
            p[j] = max(p[j], np.median(base)) * 8
        profiles[t] = p / p.sum()
    cond_genes = [genes[j] for j in rng.choice([j for j in filler_idx if j not in taken], 20,
                                               replace=False)]
    batch_shift = {b: np.where(rng.random(g) < 0.15, rng.lognormal(0, 0.6, g), 1.0)
                   for b in ("b1", "b2")}
    samples = [("ctrl1", "control", "b1"), ("ctrl2", "control", "b2"),
               ("trt1", "treated", "b1"), ("trt2", "treated", "b2")]
    truth: dict[str, dict[str, object]] = {}
    rows = ["sample,path,condition,batch"]
    mito_idx = np.array([genes.index(m) for m in MITO])
    for s, condition, batch in samples:
        cells, labels, ts = [], [], []
        for t in TYPES:
            for _ in range(per_type // 2):
                cells.append(profiles[t])
                labels.append(t)
                ts.append(np.nan)
        for _ in range(path_cells // 2):
            pos = rng.random()
            cells.append((1 - pos) * profiles["NK"] + pos * profiles["Plt"])
            labels.append("path")
            ts.append(pos)
        mat = []
        flags = []
        for p, lab in zip(cells, labels):
            q = p * batch_shift[batch]
            if condition == "treated" and lab == "B":
                q = q.copy()
                q[[genes.index(c) for c in cond_genes]] *= 3
            q = q / q.sum()
            lib = rng.lognormal(np.log(2500), 0.3)
            low = rng.random() < 0.05
            if low:
                lib /= 6
                q = q.copy()
                q[mito_idx] *= (0.4 / q[mito_idx].sum())
                rest = np.setdiff1d(np.arange(g), mito_idx)
                q[rest] *= 0.6 / q[rest].sum()
            mu = lib * q
            size = 1 / 0.2
            mat.append(rng.negative_binomial(size, size / (size + mu)))
            flags.append(("low_quality" if low else ""))
        mat = np.array(mat)
        n_cells = len(mat)
        n_dbl = int(0.06 * n_cells)
        dbl_rows = []
        for _ in range(n_dbl):
            while True:
                i, j = rng.integers(0, n_cells, 2)
                if labels[i] != labels[j] and "path" not in (labels[i], labels[j]):
                    break
            dbl_rows.append(mat[i] + mat[j])
        all_rows = np.vstack([mat] + ([np.array(dbl_rows)] if dbl_rows else []))
        all_labels = labels + ["doublet"] * n_dbl
        all_t = ts + [np.nan] * n_dbl
        all_flags = flags + ["doublet"] * n_dbl
        order = rng.permutation(len(all_rows))
        all_rows = all_rows[order]
        d = root / s
        d.mkdir(exist_ok=True)
        barcodes = [f"CELL{k:05d}-1" for k in range(len(all_rows))]
        with gzip.open(d / "matrix.mtx.gz", "wb") as fh:
            spio.mmwrite(fh, sparse.csr_matrix(all_rows.T))
        with gzip.open(d / "barcodes.tsv.gz", "wt") as fh:
            fh.write("\n".join(barcodes) + "\n")
        with gzip.open(d / "features.tsv.gz", "wt") as fh:
            fh.write("\n".join(f"ENSG{k:06d}\t{name}\tGene Expression"
                               for k, name in enumerate(genes)) + "\n")
        for k, o in enumerate(order):
            truth[f"{s}:{barcodes[k]}"] = {"type": all_labels[o], "t": all_t[o],
                                           "flag": all_flags[o], "sample": s,
                                           "batch": batch, "condition": condition}
        rows.append(f"{s},{s},{condition},{batch}")
    sheet = root / "samples.csv"
    sheet.write_text("\n".join(rows) + "\n")
    return ScWorld(root=root, sheet=sheet, genes=genes, truth=truth,
                   condition_genes=cond_genes)
