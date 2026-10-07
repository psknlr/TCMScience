"""Count matrices for single-cell analysis.

Reads the formats single-cell data arrive in, into one :class:`CellMatrix` (cells x
genes, sparse integers):

* a Cell Ranger directory (``matrix.mtx[.gz]``, ``barcodes.tsv[.gz]``,
  ``features.tsv[.gz]`` or the older ``genes.tsv``), keeping only "Gene Expression"
  features when the file lists other kinds;
* a Cell Ranger HDF5 file (``filtered_feature_bc_matrix.h5``), with ``h5py``;
* an AnnData file (``.h5ad``), with ``anndata``; its ``X`` (or ``layers['counts']``
  when present) must hold raw counts;
* a text table (CSV or TSV) of genes in rows and cells in columns.

Several samples are combined with :func:`concatenate`, cell barcodes prefixed by the
sample name so they stay unique.

:func:`to_anndata` and :func:`from_anndata` convert to and from Scanpy's container. The
raw counts travel in ``layers['counts']`` as well as ``X``, because Scanpy normalises
``X`` in place: whoever reads the object back after an analysis still finds the counts
the pipeline started from. Cell barcodes, gene IDs, gene names and every per-cell
annotation (sample, donor, batch, condition) are carried by name.
"""

from __future__ import annotations

import csv
import gzip
import io
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
from scipy import io as spio
from scipy import sparse

__all__ = ["CellMatrix", "ScIOError", "read_counts", "read_10x_mtx", "read_10x_h5",
           "read_h5ad", "read_table", "concatenate", "to_anndata", "from_anndata"]


class ScIOError(ValueError):
    """A file that cannot be read as a count matrix."""


@dataclass
class CellMatrix:
    """Raw counts: cells in rows, genes in columns."""

    counts: sparse.csr_matrix
    cells: np.ndarray                      # barcodes
    gene_ids: np.ndarray
    gene_names: np.ndarray
    obs: dict[str, np.ndarray] = field(default_factory=dict)   # per-cell annotations
    source: str = ""

    def __post_init__(self) -> None:
        n, g = self.counts.shape
        if len(self.cells) != n or len(self.gene_ids) != g or len(self.gene_names) != g:
            raise ScIOError(f"a {n} x {g} matrix with {len(self.cells)} cell and "
                            f"{len(self.gene_ids)} gene labels")
        for key, values in self.obs.items():
            if len(values) != n:
                raise ScIOError(f"annotation {key!r} has {len(values)} values for {n} cells")

    @property
    def shape(self) -> tuple[int, int]:
        return self.counts.shape

    def subset(self, cells: np.ndarray | None = None, genes: np.ndarray | None = None
               ) -> "CellMatrix":
        c = slice(None) if cells is None else cells
        g = slice(None) if genes is None else genes
        return CellMatrix(counts=self.counts[c][:, g].tocsr(), cells=self.cells[c],
                          gene_ids=self.gene_ids[g], gene_names=self.gene_names[g],
                          obs={k: v[c] for k, v in self.obs.items()}, source=self.source)


def _open(path: Path):
    with path.open("rb") as fh:
        magic = fh.read(2)
    if magic == b"\x1f\x8b":
        return io.TextIOWrapper(gzip.open(path, "rb"), encoding="utf-8")
    return path.open("r", encoding="utf-8")


def _first(directory: Path, *names: str) -> Path:
    for name in names:
        for candidate in (directory / name, directory / f"{name}.gz"):
            if candidate.is_file():
                return candidate
    raise ScIOError(f"{directory} has none of {', '.join(names)} (or .gz)")


def _check_integer(matrix: sparse.spmatrix, where: str) -> sparse.csr_matrix:
    m = sparse.csr_matrix(matrix)
    if m.nnz and (m.data.min() < 0 or np.any(np.abs(m.data - np.round(m.data)) > 1e-6)):
        raise ScIOError(f"{where} does not hold raw counts (negative or non-integer "
                        "values); the pipeline needs counts, not normalised values")
    m.data = np.round(m.data).astype(np.float32)
    m.eliminate_zeros()
    return m


def _dedupe(names: np.ndarray) -> np.ndarray:
    """Make gene names unique the way Seurat and Scanpy do: NAME, NAME-1, NAME-2."""
    seen: dict[str, int] = {}
    out = []
    for n in names:
        if n in seen:
            seen[n] += 1
            out.append(f"{n}-{seen[n]}")
        else:
            seen[n] = 0
            out.append(n)
    return np.array(out, dtype=object)


def read_10x_mtx(directory: str | Path) -> CellMatrix:
    d = Path(directory)
    mtx = _first(d, "matrix.mtx")
    with (gzip.open(mtx, "rb") if mtx.suffix == ".gz" else mtx.open("rb")) as fh:
        m = spio.mmread(fh)
    with _open(_first(d, "barcodes.tsv")) as fh:
        barcodes = np.array([line.rstrip("\n").split("\t")[0] for line in fh if line.strip()],
                            dtype=object)
    with _open(_first(d, "features.tsv", "genes.tsv")) as fh:
        rows = [line.rstrip("\n").split("\t") for line in fh if line.strip()]
    ids = np.array([r[0] for r in rows], dtype=object)
    names = np.array([r[1] if len(r) > 1 else r[0] for r in rows], dtype=object)
    kinds = np.array([r[2] if len(r) > 2 else "Gene Expression" for r in rows], dtype=object)
    m = sparse.csr_matrix(m).T.tocsr()                    # 10x stores genes x cells
    if m.shape != (len(barcodes), len(ids)):
        raise ScIOError(f"{mtx} is {m.shape[1]} x {m.shape[0]}, but there are "
                        f"{len(ids)} features and {len(barcodes)} barcodes")
    keep = kinds == "Gene Expression"
    m = _check_integer(m[:, keep], str(mtx))
    return CellMatrix(counts=m, cells=barcodes, gene_ids=ids[keep],
                      gene_names=_dedupe(names[keep]), source=str(d))


def read_10x_h5(path: str | Path) -> CellMatrix:
    try:
        import h5py
    except ImportError as exc:                              # pragma: no cover
        raise ScIOError("reading 10x HDF5 needs h5py (pip install h5py)") from exc
    with h5py.File(path, "r") as f:
        if "matrix" not in f:
            raise ScIOError(f"{path} is not a Cell Ranger v3+ HDF5 file (no /matrix)")
        g = f["matrix"]
        shape = tuple(int(x) for x in g["shape"][()])
        m = sparse.csc_matrix((g["data"][()], g["indices"][()], g["indptr"][()]),
                              shape=shape)
        barcodes = np.array([b.decode() for b in g["barcodes"][()]], dtype=object)
        feat = g["features"]
        ids = np.array([b.decode() for b in feat["id"][()]], dtype=object)
        names = np.array([b.decode() for b in feat["name"][()]], dtype=object)
        kinds = np.array([b.decode() for b in feat["feature_type"][()]], dtype=object)
    keep = kinds == "Gene Expression"
    m = _check_integer(m.T.tocsr()[:, keep], str(path))
    return CellMatrix(counts=m, cells=barcodes, gene_ids=ids[keep],
                      gene_names=_dedupe(names[keep]), source=str(path))


def _anndata(purpose: str) -> Any:
    try:
        import anndata
    except ImportError as exc:                              # pragma: no cover
        raise ScIOError(f"{purpose} needs anndata (pip install anndata)") from exc
    return anndata


def read_h5ad(path: str | Path) -> CellMatrix:
    ad = _anndata("reading .h5ad").read_h5ad(path)
    return from_anndata(ad, source=str(path))


def from_anndata(ad: Any, *, source: str = "") -> CellMatrix:
    """The raw counts of an AnnData: ``layers['counts']`` when present, else ``X``.

    Either must hold raw counts; normalised values are refused rather than rounded.
    """
    x = ad.layers["counts"] if "counts" in ad.layers else ad.X
    where = source or "the AnnData"
    m = _check_integer(sparse.csr_matrix(x), f"{where} (X or layers['counts'])")
    names = np.array(ad.var_names, dtype=object)
    ids = (np.array(ad.var["gene_ids"], dtype=object) if "gene_ids" in ad.var
           else names.copy())
    obs = {str(c): np.array(ad.obs[c].astype(str), dtype=object) for c in ad.obs.columns}
    return CellMatrix(counts=m, cells=np.array(ad.obs_names, dtype=object), gene_ids=ids,
                      gene_names=_dedupe(names), obs=obs,
                      source=source or str(ad.uns.get("bioagent_source", "")))


def to_anndata(m: CellMatrix) -> Any:
    """An AnnData of ``m``: cells x genes, the raw counts in ``X`` and ``layers['counts']``.

    ``obs_names`` are the barcodes and ``var_names`` the gene names, with the IDs in
    ``var['gene_ids']``; each annotation becomes a categorical ``obs`` column. Repeated
    barcodes or gene names are refused: AnnData would accept them, and a later lookup by
    name would then pick one of the duplicates without saying so.
    """
    import pandas as pd
    anndata = _anndata("converting to AnnData")
    for what, labels in (("cell barcode", m.cells), ("gene name", m.gene_names)):
        repeated = sorted(k for k, n in Counter(map(str, labels)).items() if n > 1)
        if repeated:
            raise ScIOError(f"{len(repeated)} {what}s repeat (e.g. {repeated[0]!r}); "
                            "AnnData needs unique names")
    obs = pd.DataFrame({k: pd.Categorical([str(x) for x in v]) for k, v in m.obs.items()},
                       index=pd.Index([str(c) for c in m.cells]))
    var = pd.DataFrame({"gene_ids": [str(g) for g in m.gene_ids]},
                       index=pd.Index([str(g) for g in m.gene_names]))
    ad = anndata.AnnData(X=m.counts.copy(), obs=obs, var=var)
    ad.layers["counts"] = m.counts.copy()
    ad.uns["bioagent_source"] = m.source
    return ad


def read_table(path: str | Path) -> CellMatrix:
    """Genes in rows, cells in columns; the first column holds gene names."""
    path = Path(path)
    with _open(path) as fh:
        text = fh.read()
    first = text.split("\n", 1)[0]
    delimiter = "\t" if first.count("\t") >= first.count(",") else ","
    rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))
    if len(rows) < 2:
        raise ScIOError(f"{path} has no data rows")
    cells = np.array(rows[0][1:], dtype=object)
    genes = np.array([r[0] for r in rows[1:]], dtype=object)
    try:
        values = np.array([[float(v) for v in r[1:]] for r in rows[1:]], dtype=np.float64)
    except ValueError as exc:
        raise ScIOError(f"{path}: a value is not a number ({exc})") from exc
    if values.shape[1] != len(cells):
        raise ScIOError(f"{path}: rows have {values.shape[1]} values for {len(cells)} cells")
    m = _check_integer(sparse.csr_matrix(values.T), str(path))
    return CellMatrix(counts=m, cells=cells, gene_ids=genes.copy(), gene_names=_dedupe(genes),
                      source=str(path))


def read_counts(path: str | Path) -> CellMatrix:
    """Whichever of the supported formats ``path`` is."""
    p = Path(path)
    if p.is_dir():
        return read_10x_mtx(p)
    name = p.name.lower()
    if name.endswith(".h5ad"):
        return read_h5ad(p)
    if name.endswith(".h5"):
        return read_10x_h5(p)
    if name.endswith((".csv", ".tsv", ".txt", ".csv.gz", ".tsv.gz", ".txt.gz")):
        return read_table(p)
    raise ScIOError(f"{p}: not a 10x directory, .h5, .h5ad or a CSV/TSV table")


def concatenate(parts: Sequence[tuple[str, CellMatrix]],
                attributes: Mapping[str, Mapping[str, Any]] | None = None) -> CellMatrix:
    """Samples into one matrix over the union of genes (by gene id).

    Cell barcodes become ``sample:barcode``; ``obs['sample']`` and each sample's
    attributes (condition, batch, ...) are recorded per cell.
    """
    if not parts:
        raise ScIOError("no samples to combine")
    order: dict[str, int] = {}
    names: dict[str, str] = {}
    for _, m in parts:
        for gid, gname in zip(m.gene_ids, m.gene_names):
            if gid not in order:
                order[gid] = len(order)
                names[gid] = gname
    blocks, cells, obs = [], [], {"sample": []}
    attr_keys = sorted({k for a in (attributes or {}).values() for k in a})
    for k in attr_keys:
        obs[k] = []
    for sample, m in parts:
        cols = np.array([order[g] for g in m.gene_ids])
        coo = m.counts.tocoo()
        blocks.append(sparse.csr_matrix((coo.data, (coo.row, cols[coo.col])),
                                        shape=(m.shape[0], len(order))))
        cells += [f"{sample}:{c}" for c in m.cells]
        obs["sample"] += [sample] * m.shape[0]
        for k in attr_keys:
            obs[k] += [str((attributes or {}).get(sample, {}).get(k, ""))] * m.shape[0]
    gene_ids = np.array(list(order), dtype=object)
    return CellMatrix(counts=sparse.vstack(blocks).tocsr(), cells=np.array(cells, dtype=object),
                      gene_ids=gene_ids,
                      gene_names=_dedupe(np.array([names[g] for g in gene_ids], dtype=object)),
                      obs={k: np.array(v, dtype=object) for k, v in obs.items()},
                      source=";".join(m.source for _, m in parts))
