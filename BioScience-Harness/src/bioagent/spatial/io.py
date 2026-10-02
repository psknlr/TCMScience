"""Reading processed spatial transcriptomics data, and refusing data that cannot be spatial.

Supported inputs (processed outputs only; raw sequencing is out of scope):

* **Visium (Space Ranger 1.x–3.x)**: an ``outs`` directory with
  ``filtered_feature_bc_matrix.h5`` (needs ``h5py``) or the
  ``filtered_feature_bc_matrix/`` Matrix Market directory (numpy only), plus
  ``spatial/`` holding ``tissue_positions_list.csv`` (1.x, no header),
  ``tissue_positions.csv`` (2.x, header) or ``tissue_positions.parquet`` (3.x), and
  ``scalefactors_json.json``.
* **Visium HD**: one bin directory, e.g. ``binned_outputs/square_008um``. Bins lie on a
  square grid, not the hexagonal Visium grid, and each bin size is a different dataset;
  passing the parent ``outs`` directory is refused with the list of bins to choose from.
* **AnnData ``.h5ad``** (needs ``h5py``): accepted only with tissue coordinates in
  ``obsm['spatial']`` and raw counts (``X`` or ``layers['counts']``). An ``.h5ad`` is not
  spatial because of its extension, and a UMAP embedding is not a tissue position.

Every reader checks that expression and positions describe the same spots and that the
scale factors agree with the physical spot spacing, and raises ``SpatialInputError``
with the reason when they do not.
"""

from __future__ import annotations

import csv
import gzip
import hashlib
import io
import json
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

import numpy as np

from .matrix import CSR

__all__ = ["SpatialInputError", "SpatialSection", "read_visium", "read_h5ad_spatial",
           "sha256_file", "VISIUM_SPOT_PITCH_UM", "VISIUM_SPOT_DIAMETER_UM"]

VISIUM_SPOT_PITCH_UM = 100.0       # centre-to-centre distance of neighbouring spots
VISIUM_SPOT_DIAMETER_UM = 55.0


class SpatialInputError(ValueError):
    """The input cannot support a spatial analysis as given."""


@dataclass
class SpatialSection:
    section_id: str
    sample_id: str
    subject_id: str
    barcodes: np.ndarray
    gene_ids: np.ndarray
    gene_names: np.ndarray
    counts: CSR                        # spots × genes, raw counts
    xy: np.ndarray                     # full-resolution pixel coordinates (x = column, y = row)
    grid: str                          # visium_hex, square, none
    array_rc: np.ndarray | None = None  # array row/col, when the platform has a grid
    scalefactors: Mapping[str, float] = field(default_factory=dict)
    images: Mapping[str, str] = field(default_factory=dict)
    platform: str = ""
    source_files: Mapping[str, str] = field(default_factory=dict)   # path -> sha256
    checks: list[dict] = field(default_factory=list)
    condition: str = ""

    @property
    def n_spots(self) -> int:
        return self.counts.shape[0]

    def subset(self, spots: np.ndarray | None = None, genes: np.ndarray | None = None
               ) -> "SpatialSection":
        c = self.counts
        bc, xy, rc = self.barcodes, self.xy, self.array_rc
        if spots is not None:
            c = c.take_rows(spots)
            bc, xy = bc[spots], xy[spots]
            rc = rc[spots] if rc is not None else None
        gi, gn = self.gene_ids, self.gene_names
        if genes is not None:
            c = c.take_cols(genes)
            gi, gn = gi[genes], gn[genes]
        return SpatialSection(self.section_id, self.sample_id, self.subject_id, bc, gi, gn,
                              c, xy, self.grid, rc, self.scalefactors, self.images,
                              self.platform, self.source_files, self.checks, self.condition)


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def _open_text(path: Path):
    return io.TextIOWrapper(gzip.open(path, "rb"), encoding="utf-8") if path.suffix == ".gz" \
        else open(path, encoding="utf-8")


def _first(d: Path, *names: str) -> Path | None:
    for n in names:
        if (d / n).exists():
            return d / n
    return None


# Matrices -----------------------------------------------------------------------------------

def _read_mex(d: Path) -> tuple[CSR, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    mtx = _first(d, "matrix.mtx.gz", "matrix.mtx")
    bcs = _first(d, "barcodes.tsv.gz", "barcodes.tsv")
    fts = _first(d, "features.tsv.gz", "features.tsv", "genes.tsv")
    if not (mtx and bcs and fts):
        raise SpatialInputError(f"{d} is not a Matrix Market count directory (needs matrix.mtx, "
                                "barcodes.tsv and features.tsv)")
    with _open_text(mtx) as f:
        header = f.readline()
        if "coordinate" not in header:
            raise SpatialInputError(f"{mtx.name}: only coordinate Matrix Market files are read")
        line = f.readline()
        while line.startswith("%"):
            line = f.readline()
        n_feat, n_bc, nnz = map(int, line.split())
        arr = np.loadtxt(f, dtype=np.float64, ndmin=2) if nnz else np.zeros((0, 3))
    if len(arr) != nnz:
        raise SpatialInputError(f"{mtx.name} declares {nnz} entries but holds {len(arr)}")
    with _open_text(bcs) as f:
        barcodes = np.array([ln.strip().split("\t")[0] for ln in f if ln.strip()])
    with _open_text(fts) as f:
        rows = [ln.rstrip("\n").split("\t") for ln in f if ln.strip()]
    gene_ids = np.array([r[0] for r in rows])
    gene_names = np.array([r[1] if len(r) > 1 else r[0] for r in rows])
    ftype = np.array([r[2] if len(r) > 2 else "Gene Expression" for r in rows])
    if len(barcodes) != n_bc or len(gene_ids) != n_feat:
        raise SpatialInputError("matrix dimensions do not match the barcode and feature lists")
    # Matrix Market is 1-based, features × barcodes
    counts = CSR.from_coo(arr[:, 1].astype(np.int64) - 1, arr[:, 0].astype(np.int64) - 1,
                          arr[:, 2], (n_bc, n_feat))
    return counts, barcodes, gene_ids, gene_names, ftype


def _read_10x_h5(path: Path) -> tuple[CSR, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    try:
        import h5py
    except ImportError as exc:
        raise SpatialInputError(f"{path.name} is HDF5 and h5py is not installed; install the "
                                "'spatial' extra or pass the filtered_feature_bc_matrix/ "
                                "directory instead") from exc
    with h5py.File(path, "r") as f:
        if "matrix" not in f:
            raise SpatialInputError(f"{path.name} has no 'matrix' group: not a Cell Ranger / "
                                    "Space Ranger v3+ HDF5 file")
        m = f["matrix"]
        n_feat, n_bc = (int(x) for x in m["shape"][:])
        counts = CSR.from_csc_transposed(m["data"][:], m["indices"][:], m["indptr"][:],
                                         n_bc, n_feat)
        dec = np.vectorize(lambda b: b.decode() if isinstance(b, bytes) else b)
        barcodes = dec(m["barcodes"][:])
        ft = m["features"]
        gene_ids, gene_names = dec(ft["id"][:]), dec(ft["name"][:])
        ftype = dec(ft["feature_type"][:]) if "feature_type" in ft else \
            np.full(n_feat, "Gene Expression")
    return counts, barcodes, gene_ids, gene_names, ftype


# Positions ------------------------------------------------------------------------------------

_POS_COLS = ("barcode", "in_tissue", "array_row", "array_col", "pxl_row_in_fullres",
             "pxl_col_in_fullres")


def _read_positions(spatial: Path) -> tuple[dict[str, tuple], str]:
    p = _first(spatial, "tissue_positions.parquet", "tissue_positions.csv",
               "tissue_positions_list.csv")
    if p is None:
        raise SpatialInputError(f"{spatial} holds no tissue_positions file: without tissue "
                                "coordinates there is no spatial analysis")
    if p.suffix == ".parquet":
        import pyarrow.parquet as pq
        t = pq.read_table(p).to_pydict()
        missing = [c for c in _POS_COLS if c not in t]
        if missing:
            raise SpatialInputError(f"{p.name} lacks columns {missing}")
        rows = zip(*(t[c] for c in _POS_COLS))
    else:
        with open(p, encoding="utf-8") as f:
            reader = list(csv.reader(f))
        if reader and reader[0] and reader[0][0] == "barcode":
            head = reader[0]
            missing = [c for c in _POS_COLS if c not in head]
            if missing:
                raise SpatialInputError(f"{p.name} lacks columns {missing}")
            ix = [head.index(c) for c in _POS_COLS]
            rows = ([r[i] for i in ix] for r in reader[1:] if r)
        else:
            rows = (r[:6] for r in reader if r)
    out = {}
    for bc, it, ar, ac, pr, pc in rows:
        if bc in out:
            raise SpatialInputError(f"barcode {bc} appears twice in {p.name}")
        out[str(bc)] = (int(it), int(ar), int(ac), float(pr), float(pc))
    return out, p.name


def _png_size(path: Path) -> tuple[int, int] | None:
    """(width, height) from a PNG header, without an imaging library."""
    try:
        with open(path, "rb") as f:
            head = f.read(24)
        if head[:8] != b"\x89PNG\r\n\x1a\n":
            return None
        w, h = struct.unpack(">II", head[16:24])
        return int(w), int(h)
    except OSError:
        return None


def _nn_distance(xy: np.ndarray, sample: int = 400, seed: int = 0) -> float:
    """Median nearest-neighbour distance (on a subsample of query points)."""
    rng = np.random.default_rng(seed)
    q = xy[rng.choice(len(xy), min(sample, len(xy)), replace=False)]
    d = np.sqrt(((q[:, None, :] - xy[None, :, :]) ** 2).sum(-1))
    d[d == 0] = np.inf
    return float(np.median(d.min(1)))


def _check_geometry(sec: SpatialSection, spatial: Path | None) -> None:
    sf = sec.scalefactors
    checks = sec.checks
    if not np.isfinite(sec.xy).all():
        raise SpatialInputError("some tissue coordinates are missing or not finite")
    diam = float(sf.get("spot_diameter_fullres", 0) or 0)
    hires = float(sf.get("tissue_hires_scalef", 0) or 0)
    if sec.platform == "visium":
        if diam <= 0:
            raise SpatialInputError("scalefactors lack a positive spot_diameter_fullres")
        if not 0 < hires <= 1:
            raise SpatialInputError(f"tissue_hires_scalef {hires} is not in (0, 1]")
        nn = _nn_distance(sec.xy)
        ratio = nn / diam
        expected = VISIUM_SPOT_PITCH_UM / VISIUM_SPOT_DIAMETER_UM
        checks.append({"check": "spot_spacing", "nn_distance_px": nn,
                       "spot_diameter_px": diam, "ratio": ratio, "expected": expected})
        if not 0.8 * expected <= ratio <= 1.2 * expected:
            raise SpatialInputError(
                f"spot spacing ({nn:.1f} px) is {ratio:.2f} spot diameters; Visium spots are "
                f"{expected:.2f} diameters apart, so the scale factors or the coordinates do "
                "not belong together")
        if sec.array_rc is not None:
            odd = int(((sec.array_rc[:, 0] + sec.array_rc[:, 1]) % 2).sum())
            checks.append({"check": "hex_parity", "violations": odd})
            if odd:
                raise SpatialInputError(f"{odd} spots break the Visium array parity "
                                        "(row + col even): not a standard Visium layout")
    elif sec.platform == "visium_hd":
        mpp = float(sf.get("microns_per_pixel", 0) or 0)
        bin_um = float(sf.get("bin_size_um", 0) or 0)
        if mpp <= 0 or bin_um <= 0:
            raise SpatialInputError("Visium HD scalefactors need microns_per_pixel and "
                                    "bin_size_um")
        nn = _nn_distance(sec.xy)
        expected = bin_um / mpp
        checks.append({"check": "bin_spacing", "nn_distance_px": nn, "expected_px": expected})
        if not 0.75 * expected <= nn <= 1.25 * expected:
            raise SpatialInputError(f"bin spacing {nn:.1f} px disagrees with bin_size_um / "
                                    f"microns_per_pixel = {expected:.1f} px")
    # image bounds under the recorded scale factor
    img = sec.images.get("hires")
    if img and hires > 0:
        size = _png_size(Path(img))
        if size:
            w_full, h_full = size[0] / hires, size[1] / hires
            out = int(((sec.xy[:, 0] < 0) | (sec.xy[:, 0] > w_full * 1.02) |
                       (sec.xy[:, 1] < 0) | (sec.xy[:, 1] > h_full * 1.02)).sum())
            checks.append({"check": "image_bounds", "image_px": list(size),
                           "fullres_extent": [w_full, h_full], "outside": out})
            if out:
                raise SpatialInputError(f"{out} spots fall outside the tissue image under the "
                                        f"recorded tissue_hires_scalef {hires}")


def read_visium(path: str | Path, *, section_id: str, sample_id: str = "",
                subject_id: str = "", condition: str = "", prefer: str = "h5") -> SpatialSection:
    d = Path(path)
    if not d.is_dir():
        raise SpatialInputError(f"{d} is not a directory")
    if (d / "binned_outputs").is_dir() and not (_first(d, "filtered_feature_bc_matrix.h5",
                                                       "filtered_feature_bc_matrix")
                                                or list(d.glob("*_filtered_feature_bc_matrix.h5"))):
        bins = sorted(p.name for p in (d / "binned_outputs").iterdir() if p.is_dir())
        raise SpatialInputError(f"{d} is a Visium HD output; choose one bin size and pass its "
                                f"directory: {bins}")
    spatial = d / "spatial"
    if not spatial.is_dir():
        raise SpatialInputError(f"{d} has no spatial/ directory: expression without tissue "
                                "positions cannot be analysed spatially")
    h5 = d / "filtered_feature_bc_matrix.h5"
    if not h5.exists():
        # 10x public downloads prefix the file with the sample name
        named = sorted(d.glob("*_filtered_feature_bc_matrix.h5"))
        if len(named) > 1:
            raise SpatialInputError(f"{d} holds several filtered matrices: {named}")
        h5 = named[0] if named else h5
    mex = d / "filtered_feature_bc_matrix"
    files: dict[str, str] = {}
    if h5.exists() and (prefer == "h5" or not mex.is_dir()):
        counts, bcs, gid, gname, ftype = _read_10x_h5(h5)
        files[str(h5)] = sha256_file(h5)
    elif mex.is_dir():
        counts, bcs, gid, gname, ftype = _read_mex(mex)
        for p in sorted(mex.iterdir()):
            files[str(p)] = sha256_file(p)
    else:
        raise SpatialInputError(f"{d} holds neither filtered_feature_bc_matrix.h5 nor "
                                "filtered_feature_bc_matrix/")
    gex = ftype == "Gene Expression"
    if not gex.all():
        counts = counts.take_cols(np.flatnonzero(gex))
        gid, gname = gid[gex], gname[gex]
    positions, pos_name = _read_positions(spatial)
    files[str(spatial / pos_name)] = sha256_file(spatial / pos_name)
    sfp = spatial / "scalefactors_json.json"
    if not sfp.exists():
        raise SpatialInputError("spatial/scalefactors_json.json is missing: pixel coordinates "
                                "cannot be related to the image or to physical distance")
    scalefactors = json.loads(sfp.read_text())
    files[str(sfp)] = sha256_file(sfp)
    missing = [b for b in bcs if b not in positions]
    if missing:
        raise SpatialInputError(f"{len(missing)} of {len(bcs)} expression barcodes have no "
                                f"tissue position (e.g. {missing[:3]}): expression and "
                                "positions do not describe the same spots")
    if len(set(bcs)) != len(bcs):
        raise SpatialInputError("expression barcodes repeat")
    pos = np.array([positions[b] for b in bcs], dtype=float)
    checks = []
    off = int((pos[:, 0] != 1).sum())
    checks.append({"check": "in_tissue", "spots": len(bcs), "not_in_tissue": off})
    hd = "bin_size_um" in scalefactors or "square_" in d.name
    images = {k: str(spatial / f) for k, f in (("hires", "tissue_hires_image.png"),
                                                ("lowres", "tissue_lowres_image.png"))
              if (spatial / f).exists()}
    sec = SpatialSection(
        section_id=section_id, sample_id=sample_id or section_id,
        subject_id=subject_id or sample_id or section_id, barcodes=np.asarray(bcs),
        gene_ids=np.asarray(gid), gene_names=np.asarray(gname), counts=counts,
        xy=np.column_stack([pos[:, 4], pos[:, 3]]),
        grid="square" if hd else "visium_hex",
        array_rc=pos[:, 1:3].astype(np.int64), scalefactors=scalefactors, images=images,
        platform="visium_hd" if hd else "visium", source_files=files, checks=checks,
        condition=condition)
    if off:
        checks.append({"check": "in_tissue_warning",
                       "message": f"{off} matrix barcodes are marked outside the tissue"})
    _check_geometry(sec, spatial)
    return sec


def read_h5ad_spatial(path: str | Path, *, section_id: str = "", section_key: str = "",
                      sample_id: str = "", subject_id: str = "", condition: str = ""
                      ) -> list[SpatialSection]:
    """Read an AnnData file that carries tissue coordinates; one section per value of
    ``obs[section_key]`` (or one section)."""
    try:
        import h5py
    except ImportError as exc:
        raise SpatialInputError("reading .h5ad needs h5py (the 'spatial' extra)") from exc
    p = Path(path)
    with h5py.File(p, "r") as f:
        if "obsm" not in f or "spatial" not in f["obsm"]:
            raise SpatialInputError(f"{p.name} has no obsm['spatial']: an .h5ad without tissue "
                                    "coordinates cannot support a spatial analysis (an "
                                    "embedding such as UMAP is not a tissue position)")
        xy = np.asarray(f["obsm"]["spatial"][:], float)
        if xy.ndim != 2 or xy.shape[1] < 2:
            raise SpatialInputError("obsm['spatial'] must be an n × 2 coordinate array")
        xy = xy[:, :2]

        def frame_index(g) -> np.ndarray:
            key = g.attrs.get("_index", "_index")
            v = g[key][:]
            return np.array([x.decode() if isinstance(x, bytes) else str(x) for x in v])

        def column(g, name) -> np.ndarray:
            o = g[name]
            if isinstance(o, h5py.Group):           # categorical
                cats = [c.decode() if isinstance(c, bytes) else str(c)
                        for c in o["categories"][:]]
                return np.array([cats[c] if c >= 0 else "" for c in o["codes"][:]])
            return np.array([x.decode() if isinstance(x, bytes) else str(x) for x in o[:]])

        obs_names = frame_index(f["obs"])
        var_names = frame_index(f["var"])
        src = f["layers"]["counts"] if "layers" in f and "counts" in f["layers"] else f["X"]
        if isinstance(src, h5py.Group):
            enc = src.attrs.get("encoding-type", "")
            shape = tuple(int(x) for x in src.attrs["shape"])
            if enc == "csr_matrix":
                counts = CSR(np.asarray(src["data"][:], np.float32),
                             np.asarray(src["indices"][:], np.int64),
                             np.asarray(src["indptr"][:], np.int64), shape)
            elif enc == "csc_matrix":
                cols = np.repeat(np.arange(shape[1]), np.diff(src["indptr"][:]))
                counts = CSR.from_coo(src["indices"][:], cols, src["data"][:], shape)
            else:
                raise SpatialInputError(f"unsupported matrix encoding {enc!r}")
        else:
            counts = CSR.from_dense(np.asarray(src[:]))
        if counts.nnz and not np.allclose(counts.data[:10000], np.round(counts.data[:10000])):
            raise SpatialInputError("the count matrix is not integer: it looks normalised; "
                                    "provide raw counts in X or layers['counts']")
        sections_col = column(f["obs"], section_key) if section_key else None
        uns_sf = {}
        if "uns" in f and "spatial" in f["uns"]:
            for lib in f["uns"]["spatial"]:
                g = f["uns"]["spatial"][lib]
                if "scalefactors" in g:
                    uns_sf[lib] = {k: float(g["scalefactors"][k][()])
                                   for k in g["scalefactors"]}
    if len(obs_names) != counts.shape[0] or len(xy) != counts.shape[0]:
        raise SpatialInputError("obs, X and obsm['spatial'] disagree in length")
    files = {str(p): sha256_file(p)}
    groups = {section_id or p.stem: np.arange(len(obs_names))} if sections_col is None else \
        {str(v): np.flatnonzero(sections_col == v) for v in np.unique(sections_col)}
    out = []
    for sid, idx in groups.items():
        sf = uns_sf.get(sid) or (next(iter(uns_sf.values())) if len(uns_sf) == 1 else {})
        sec = SpatialSection(
            section_id=sid, sample_id=sample_id or sid, subject_id=subject_id or sample_id or sid,
            barcodes=obs_names[idx], gene_ids=var_names, gene_names=var_names,
            counts=counts.take_rows(idx), xy=xy[idx], grid="none", scalefactors=sf,
            platform="h5ad", source_files=files,
            checks=[{"check": "scalefactors", "present": bool(sf)}], condition=condition)
        if not np.isfinite(sec.xy).all():
            raise SpatialInputError(f"section {sid}: some coordinates are missing")
        out.append(sec)
    return out

