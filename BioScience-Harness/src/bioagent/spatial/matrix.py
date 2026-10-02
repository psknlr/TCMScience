"""A minimal compressed-sparse-row count matrix (observations × features), numpy only.

The spatial pipeline must run where the harness runs, and the harness depends on numpy
(through pandas) but not on scipy. A Visium section is ~5,000 spots × ~36,000 genes with
a few percent non-zero, so a dense copy is avoided; only the few operations the pipeline
needs are implemented, and each is checked against a dense computation in the tests.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np

__all__ = ["CSR"]


@dataclass(frozen=True)
class CSR:
    data: np.ndarray        # float32 values
    indices: np.ndarray     # int32/int64 column index per value
    indptr: np.ndarray      # int64, len n_rows + 1
    shape: tuple[int, int]

    def __post_init__(self) -> None:
        if len(self.indptr) != self.shape[0] + 1:
            raise ValueError("indptr must have one entry per row plus one")
        if self.indptr[-1] != len(self.data) or len(self.data) != len(self.indices):
            raise ValueError("indptr, indices and data disagree in length")
        if len(self.indices) and (self.indices.min() < 0 or self.indices.max() >= self.shape[1]):
            raise ValueError("a column index lies outside the matrix")

    # construction -------------------------------------------------------------------

    @classmethod
    def from_coo(cls, rows: np.ndarray, cols: np.ndarray, vals: np.ndarray,
                 shape: tuple[int, int]) -> "CSR":
        rows = np.asarray(rows, np.int64)
        cols = np.asarray(cols, np.int64)
        vals = np.asarray(vals, np.float32)
        order = np.lexsort((cols, rows))
        rows, cols, vals = rows[order], cols[order], vals[order]
        # sum duplicates (a valid Matrix Market file has none, but be exact if it does)
        if len(rows) > 1:
            key_change = np.r_[True, (np.diff(rows) != 0) | (np.diff(cols) != 0)]
            if not key_change.all():
                starts = np.flatnonzero(key_change)
                vals = np.add.reduceat(vals, starts)
                rows, cols = rows[starts], cols[starts]
        indptr = np.zeros(shape[0] + 1, np.int64)
        np.add.at(indptr, rows + 1, 1)
        return cls(vals, cols, np.cumsum(indptr), shape)

    @classmethod
    def from_dense(cls, x: np.ndarray) -> "CSR":
        x = np.asarray(x)
        r, c = np.nonzero(x)
        return cls.from_coo(r, c, x[r, c], x.shape)

    @classmethod
    def from_csc_transposed(cls, data, indices, indptr, n_rows: int, n_cols: int) -> "CSR":
        """A CSC matrix of features × observations (the 10x HDF5 layout) is the CSR matrix
        of observations × features with the same three arrays."""
        return cls(np.asarray(data, np.float32), np.asarray(indices, np.int64),
                   np.asarray(indptr, np.int64), (n_rows, n_cols))

    # queries ----------------------------------------------------------------------------

    @property
    def nnz(self) -> int:
        return int(len(self.data))

    def row_ids(self) -> np.ndarray:
        return np.repeat(np.arange(self.shape[0]), np.diff(self.indptr))

    def row_sums(self) -> np.ndarray:
        out = np.zeros(self.shape[0], np.float64)
        np.add.at(out, self.row_ids(), self.data)
        return out

    def row_nnz(self) -> np.ndarray:
        return np.diff(self.indptr).astype(np.int64)

    def col_sums(self) -> np.ndarray:
        return np.bincount(self.indices, weights=self.data, minlength=self.shape[1])

    def col_nnz(self) -> np.ndarray:
        return np.bincount(self.indices, minlength=self.shape[1])

    def col_mean_var(self, row_scale: np.ndarray | None = None,
                     transform: str | None = None) -> tuple[np.ndarray, np.ndarray]:
        """Per-column mean and (sample) variance of ``f(scale_row · x)``, zeros included,
        with ``f`` the identity or ``log1p``."""
        v = self.data.astype(np.float64)
        if row_scale is not None:
            v = v * np.asarray(row_scale, np.float64)[self.row_ids()]
        if transform == "log1p":
            v = np.log1p(v)
        n = self.shape[0]
        s1 = np.bincount(self.indices, weights=v, minlength=self.shape[1])
        s2 = np.bincount(self.indices, weights=v * v, minlength=self.shape[1])
        mean = s1 / n
        var = (s2 - n * mean ** 2) / max(n - 1, 1)
        return mean, np.maximum(var, 0.0)

    # slicing -------------------------------------------------------------------------------

    def take_rows(self, rows: Sequence[int] | np.ndarray) -> "CSR":
        rows = np.asarray(rows)
        rows = np.flatnonzero(rows) if rows.dtype == bool else rows.astype(np.int64)
        lens = np.diff(self.indptr)[rows]
        indptr = np.r_[0, np.cumsum(lens)].astype(np.int64)
        if len(rows) == 0:
            return CSR(self.data[:0], self.indices[:0], indptr, (0, self.shape[1]))
        idx = _gather(self.indptr, rows, lens)
        return CSR(self.data[idx], self.indices[idx], indptr, (len(rows), self.shape[1]))

    def take_cols(self, cols: Sequence[int] | np.ndarray) -> "CSR":
        cols = np.asarray(cols)
        if cols.dtype == bool:
            cols = np.flatnonzero(cols)
        remap = np.full(self.shape[1], -1, np.int64)
        remap[cols] = np.arange(len(cols))
        new = remap[self.indices]
        keep = new >= 0
        rows = self.row_ids()[keep]
        return CSR.from_coo(rows, new[keep], self.data[keep], (self.shape[0], len(cols)))

    def to_dense(self, dtype=np.float32) -> np.ndarray:
        out = np.zeros(self.shape, dtype)
        out[self.row_ids(), self.indices] = self.data
        return out

    def scale_rows(self, factors: np.ndarray) -> "CSR":
        f = np.asarray(factors, np.float64)[self.row_ids()]
        return CSR((self.data * f).astype(np.float32), self.indices, self.indptr, self.shape)

    def log1p(self) -> "CSR":
        return CSR(np.log1p(self.data).astype(np.float32), self.indices, self.indptr,
                   self.shape)


def _gather(indptr: np.ndarray, rows: np.ndarray, lens: np.ndarray) -> np.ndarray:
    """Concatenate ranges [indptr[r], indptr[r+1]) for many rows without a python loop."""
    starts = indptr[rows]
    total = int(lens.sum())
    offsets = np.repeat(starts - np.r_[0, np.cumsum(lens)[:-1]], lens)
    return np.arange(total, dtype=np.int64) + offsets
