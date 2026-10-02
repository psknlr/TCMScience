"""Writers for the output contract: an AnnData-compatible ``.h5ad`` and SVG tissue plots.

The ``.h5ad`` follows the AnnData on-disk format (``encoding-type`` attributes), written
with ``h5py`` so that neither ``anndata`` nor ``scanpy`` is needed to produce it; any of
them can read it. Plots are plain SVG: no plotting library, identical bytes for identical
input.
"""

from __future__ import annotations

import html
from pathlib import Path
from typing import Mapping, Sequence

import numpy as np

from .matrix import CSR

__all__ = ["write_h5ad", "svg_spots"]


def _str_ds(group, name: str, values: Sequence[str]) -> None:
    import h5py
    ds = group.create_dataset(name, data=np.array([str(v) for v in values], dtype=object),
                              dtype=h5py.string_dtype("utf-8"))
    ds.attrs["encoding-type"] = "string-array"
    ds.attrs["encoding-version"] = "0.2.0"


def _array(group, name: str, values: np.ndarray) -> None:
    ds = group.create_dataset(name, data=np.asarray(values))
    ds.attrs["encoding-type"] = "array"
    ds.attrs["encoding-version"] = "0.2.0"


def _dict(group, name: str):
    g = group.create_group(name)
    g.attrs["encoding-type"] = "dict"
    g.attrs["encoding-version"] = "0.1.0"
    return g


def _csr(group, name: str, m: CSR) -> None:
    g = group.create_group(name)
    g.attrs["encoding-type"] = "csr_matrix"
    g.attrs["encoding-version"] = "0.1.0"
    g.attrs["shape"] = np.array(m.shape, dtype=np.int64)
    g.create_dataset("data", data=m.data.astype(np.float32))
    g.create_dataset("indices", data=m.indices.astype(np.int64))
    g.create_dataset("indptr", data=m.indptr.astype(np.int64))


def _frame(group, name: str, index: Sequence[str], columns: Mapping[str, np.ndarray]) -> None:
    import h5py
    g = group.create_group(name)
    g.attrs["encoding-type"] = "dataframe"
    g.attrs["encoding-version"] = "0.2.0"
    g.attrs["_index"] = "_index"
    g.attrs["column-order"] = np.array(list(columns), dtype=object) if columns else \
        np.array([], dtype=h5py.string_dtype("utf-8"))
    _str_ds(g, "_index", index)
    for col, vals in columns.items():
        vals = np.asarray(vals)
        if vals.dtype.kind in "OUS":
            cats, codes = np.unique(vals.astype(str), return_inverse=True)
            c = g.create_group(col)
            c.attrs["encoding-type"] = "categorical"
            c.attrs["encoding-version"] = "0.2.0"
            c.attrs["ordered"] = False
            _str_ds(c, "categories", cats)
            _array(c, "codes", codes.astype(np.int16 if len(cats) < 30000 else np.int32))
        else:
            _array(g, col, vals)


def _scalar(group, name: str, value) -> None:
    import h5py
    if isinstance(value, str):
        ds = group.create_dataset(name, data=value, dtype=h5py.string_dtype("utf-8"))
        ds.attrs["encoding-type"] = "string"
    else:
        ds = group.create_dataset(name, data=value)
        ds.attrs["encoding-type"] = "numeric-scalar"
    ds.attrs["encoding-version"] = "0.2.0"


def write_h5ad(path: str | Path, *, X: CSR, counts: CSR, obs_names: Sequence[str],
               var_names: Sequence[str], obs: Mapping[str, np.ndarray],
               var: Mapping[str, np.ndarray], obsm: Mapping[str, np.ndarray],
               connectivities: CSR | None, scalefactors: Mapping[str, Mapping[str, float]],
               uns_strings: Mapping[str, str]) -> None:
    import h5py
    with h5py.File(path, "w") as f:
        f.attrs["encoding-type"] = "anndata"
        f.attrs["encoding-version"] = "0.1.0"
        _csr(f, "X", X)
        _frame(f, "obs", obs_names, obs)
        _frame(f, "var", var_names, var)
        om = _dict(f, "obsm")
        for k, v in obsm.items():
            _array(om, k, v)
        lay = _dict(f, "layers")
        _csr(lay, "counts", counts)
        op = _dict(f, "obsp")
        if connectivities is not None:
            _csr(op, "spatial_connectivities", connectivities)
        _dict(f, "varm")
        _dict(f, "varp")
        uns = _dict(f, "uns")
        sp = _dict(uns, "spatial")
        for lib, sf in scalefactors.items():
            lg = _dict(sp, lib)
            sg = _dict(lg, "scalefactors")
            for k, v in sf.items():
                if isinstance(v, (int, float)):
                    _scalar(sg, k, float(v))
        for k, v in uns_strings.items():
            _scalar(uns, k, v)


# A categorical palette (Okabe–Ito, then extended) and a sequential ramp.
_CAT = ("#0072B2", "#E69F00", "#009E73", "#CC79A7", "#56B4E9", "#D55E00", "#F0E442",
        "#000000", "#7F7F7F", "#882255", "#44AA99", "#117733", "#332288", "#AA4499",
        "#DDCC77", "#88CCEE")
_SEQ = ("#f7fbff", "#deebf7", "#c6dbef", "#9ecae1", "#6baed6", "#4292c6", "#2171b5",
        "#08519c", "#08306b")


def svg_spots(xy: np.ndarray, *, title: str, labels: np.ndarray | None = None,
              values: np.ndarray | None = None, spot_diameter_px: float | None = None,
              width: int = 640) -> str:
    """Spots on tissue coordinates (y down, as in the image), coloured by a label or a
    value. Returns SVG text."""
    x, y = xy[:, 0], xy[:, 1]
    x0, x1, y0, y1 = x.min(), x.max(), y.min(), y.max()
    span = max(x1 - x0, y1 - y0) or 1.0
    pad = 20
    plot = width - 2 * pad - (150 if labels is not None else 60)
    s = plot / span
    h = int((y1 - y0) * s + 2 * pad + 30)
    r = max(1.0, (spot_diameter_px or span / 80) * s / 2)
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{h}" '
           f'viewBox="0 0 {width} {h}" font-family="Helvetica, Arial, sans-serif">',
           f'<rect width="{width}" height="{h}" fill="#ffffff"/>',
           f'<text x="{pad}" y="18" font-size="13" fill="#222">{html.escape(title)}</text>']
    if labels is not None:
        cats = sorted(set(map(str, labels)), key=lambda v: (len(v), v))
        col = {c: _CAT[i % len(_CAT)] for i, c in enumerate(cats)}
        fills = [col[str(v)] for v in labels]
    else:
        v = np.asarray(values, float)
        lo, hi = np.nanpercentile(v, 1), np.nanpercentile(v, 99)
        t = np.clip((v - lo) / (hi - lo if hi > lo else 1), 0, 1)
        fills = [_SEQ[int(round(tt * (len(_SEQ) - 1)))] for tt in t]
    for (xx, yy), fc in zip(xy, fills):
        out.append(f'<circle cx="{pad + (xx - x0) * s:.1f}" cy="{30 + (yy - y0) * s:.1f}" '
                   f'r="{r:.2f}" fill="{fc}"/>')
    lx = width - (140 if labels is not None else 50)
    if labels is not None:
        for i, c in enumerate(cats[:30]):
            yy = 40 + 16 * i
            out.append(f'<rect x="{lx}" y="{yy - 9}" width="10" height="10" fill="{col[c]}"/>')
            out.append(f'<text x="{lx + 14}" y="{yy}" font-size="11" fill="#222">'
                       f'{html.escape(c)}</text>')
    else:
        for i, cc in enumerate(_SEQ[::-1]):
            out.append(f'<rect x="{lx}" y="{40 + 12 * i}" width="12" height="12" fill="{cc}"/>')
        out.append(f'<text x="{lx}" y="{36}" font-size="10" fill="#222">{hi:.2f}</text>')
        out.append(f'<text x="{lx}" y="{40 + 12 * len(_SEQ) + 12}" font-size="10" '
                   f'fill="#222">{lo:.2f}</text>')
    out.append("</svg>")
    return "\n".join(out) + "\n"
