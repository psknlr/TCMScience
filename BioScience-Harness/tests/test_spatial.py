"""Spatial transcriptomics on processed data: readers, checks, graph, statistics, pipeline."""

from __future__ import annotations

import gzip
import json
import math
import struct
import sys
import zlib
from pathlib import Path

import numpy as np
import pytest

from bioagent.backends.concrete import ContainerBackend, SubprocessBackend
from bioagent.spatial import (CSR, SpatialInputError, assert_section_isolated, build_graph,
                              morans_i, neighbourhood_enrichment, read_visium, run_spatial)
from bioagent.spatial.expression import highly_variable, kmeans, normalise, pca
from bioagent.spatial.graph import SpatialGraph
from bioagent.spatial.runner import AnalysisEnvironment, run_isolated

DIAM = 50.0
PITCH = 100 / 55 * DIAM


def _png(path: Path, w: int, h: int) -> None:
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))
    raw = b"".join(b"\x00" + b"\xff" * w for _ in range(h))
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 0,
                                                                        0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def make_visium(d: Path, *, rows: int = 18, cols: int = 18, seed: int = 0,
                positions: str = "list", hires_scale: float = 0.2, image: bool = True,
                drop_position: bool = False, bad_scale: bool = False,
                hd: bool = False, n_genes: int = 60) -> dict:
    """A small Space Ranger ``outs`` directory with planted spatial genes."""
    rng = np.random.default_rng(seed)
    (d / "spatial").mkdir(parents=True, exist_ok=True)
    spots = []
    for r in range(rows):
        for c in range(cols * 2):
            if hd or (r + c) % 2 == 0:
                spots.append((r, c))
    rc = np.array(spots)
    if hd:
        pitch = 8.0 / 0.25                   # 8 µm bins at 0.25 µm per pixel
        x, y = rc[:, 1] * pitch + 100, rc[:, 0] * pitch + 100
    else:
        x = rc[:, 1] * PITCH / 2 + 200
        y = rc[:, 0] * PITCH * math.sqrt(3) / 2 + 200
    n = len(rc)
    genes = [f"G{i}" for i in range(n_genes - 4)] + ["SPAT_LEFT", "SPAT_BLOB", "MT-CO1",
                                                       "MT-ND1"]
    left = x < np.median(x)
    blob = (x - x.mean()) ** 2 + (y - y.mean()) ** 2 < (0.25 * (x.max() - x.min())) ** 2
    lam = rng.gamma(2.0, 1.5, size=(n, n_genes))
    lam[:, -4] = np.where(left, 25.0, 1.0)
    lam[:, -3] = np.where(blob, 30.0, 1.0)
    lam[:, :10] *= np.where(left, 3.0, 1.0)[:, None]          # a "left" programme
    lam[:, -2:] = 3.0
    counts = rng.poisson(lam)
    barcodes = [f"AAAC{i:06d}-1" for i in range(n)]
    mex = d / "filtered_feature_bc_matrix"
    mex.mkdir(exist_ok=True)
    r_i, c_i = np.nonzero(counts)
    with gzip.open(mex / "matrix.mtx.gz", "wt") as f:
        f.write("%%MatrixMarket matrix coordinate integer general\n%\n")
        f.write(f"{n_genes} {n} {len(r_i)}\n")
        for b, g in zip(r_i, c_i):
            f.write(f"{g + 1} {b + 1} {counts[b, g]}\n")
    with gzip.open(mex / "barcodes.tsv.gz", "wt") as f:
        f.write("\n".join(barcodes) + "\n")
    with gzip.open(mex / "features.tsv.gz", "wt") as f:
        for g in genes:
            f.write(f"ENSG_{g}\t{g}\tGene Expression\n")
    pos_rows = [(b, 1, int(r), int(c), float(yy), float(xx))
                for b, (r, c), xx, yy in zip(barcodes, rc, x, y)]
    if drop_position:
        pos_rows = pos_rows[1:]
    head = ["barcode", "in_tissue", "array_row", "array_col", "pxl_row_in_fullres",
            "pxl_col_in_fullres"]
    if positions == "list":
        (d / "spatial" / "tissue_positions_list.csv").write_text(
            "\n".join(",".join(map(str, r)) for r in pos_rows) + "\n")
    elif positions == "csv":
        (d / "spatial" / "tissue_positions.csv").write_text(
            ",".join(head) + "\n" + "\n".join(",".join(map(str, r)) for r in pos_rows) + "\n")
    else:
        import pyarrow as pa
        import pyarrow.parquet as pq
        pq.write_table(pa.table({h: [r[i] for r in pos_rows] for i, h in enumerate(head)}),
                       d / "spatial" / "tissue_positions.parquet")
    sf = {"spot_diameter_fullres": DIAM * (3 if bad_scale else 1),
          "tissue_hires_scalef": hires_scale, "tissue_lowres_scalef": hires_scale / 3,
          "fiducial_diameter_fullres": DIAM * 1.6}
    if hd:
        sf.update({"microns_per_pixel": 0.25, "bin_size_um": 8.0})
    (d / "spatial" / "scalefactors_json.json").write_text(json.dumps(sf))
    if image:
        _png(d / "spatial" / "tissue_hires_image.png", int((x.max() + 300) * hires_scale),
             int((y.max() + 300) * hires_scale))
    return {"counts": counts, "barcodes": barcodes, "genes": genes, "xy": np.c_[x, y],
            "rc": rc, "left": left, "blob": blob}


# CSR --------------------------------------------------------------------------------------

def test_csr_matches_dense():
    rng = np.random.default_rng(0)
    x = rng.poisson(0.4, (40, 25)).astype(np.float32)
    m = CSR.from_dense(x)
    assert np.allclose(m.row_sums(), x.sum(1)) and np.allclose(m.col_sums(), x.sum(0))
    assert np.array_equal(m.col_nnz(), (x > 0).sum(0))
    rows, cols = np.array([3, 1, 30]), np.array([0, 7, 24, 5])
    assert np.allclose(m.take_rows(rows).to_dense(), x[rows])
    assert np.allclose(m.take_cols(cols).to_dense(), x[:, cols])
    f = rng.uniform(0.5, 2, 40)
    mean, var = m.col_mean_var(row_scale=f, transform="log1p")
    ref = np.log1p(x * f[:, None])
    assert np.allclose(mean, ref.mean(0)) and np.allclose(var, ref.var(0, ddof=1))
    dup = CSR.from_coo(np.array([0, 0]), np.array([1, 1]), np.array([2.0, 3.0]), (1, 2))
    assert dup.to_dense().tolist() == [[0.0, 5.0]]


# Readers and their refusals -----------------------------------------------------------------

@pytest.mark.parametrize("positions", ["list", "csv", "parquet"])
def test_read_visium_matrix_market_all_position_formats(tmp_path, positions):
    truth = make_visium(tmp_path, positions=positions)
    sec = read_visium(tmp_path, section_id="s1", subject_id="p1", prefer="mex")
    assert list(sec.barcodes) == truth["barcodes"]
    assert np.allclose(sec.xy, truth["xy"])
    assert np.allclose(sec.counts.to_dense(), truth["counts"])
    assert sec.grid == "visium_hex" and sec.platform == "visium"
    checks = {c["check"]: c for c in sec.checks}
    assert abs(checks["spot_spacing"]["ratio"] - 100 / 55) < 0.01
    assert checks["image_bounds"]["outside"] == 0
    assert all(v.startswith("sha256:") for v in sec.source_files.values())


def test_hdf5_and_matrix_market_agree(tmp_path):
    h5py = pytest.importorskip("h5py")
    truth = make_visium(tmp_path)
    counts = truth["counts"]
    csc_t = CSR.from_dense(counts)        # barcodes × genes CSR == genes × barcodes CSC
    with h5py.File(tmp_path / "filtered_feature_bc_matrix.h5", "w") as f:
        m = f.create_group("matrix")
        m["data"], m["indices"], m["indptr"] = csc_t.data, csc_t.indices, csc_t.indptr
        m["shape"] = np.array([counts.shape[1], counts.shape[0]])
        m["barcodes"] = np.array(truth["barcodes"], dtype="S")
        ft = m.create_group("features")
        ft["id"] = np.array([f"ENSG_{g}" for g in truth["genes"]], dtype="S")
        ft["name"] = np.array(truth["genes"], dtype="S")
        ft["feature_type"] = np.array(["Gene Expression"] * len(truth["genes"]), dtype="S")
    a = read_visium(tmp_path, section_id="s", prefer="h5")
    b = read_visium(tmp_path, section_id="s", prefer="mex")
    assert np.array_equal(a.counts.to_dense(), b.counts.to_dense())
    assert list(a.barcodes) == list(b.barcodes) and list(a.gene_names) == list(b.gene_names)


def test_refuses_barcodes_without_positions(tmp_path):
    make_visium(tmp_path, drop_position=True)
    with pytest.raises(SpatialInputError, match="no tissue position"):
        read_visium(tmp_path, section_id="s")


def test_refuses_scale_factors_that_do_not_fit_the_spacing(tmp_path):
    make_visium(tmp_path, bad_scale=True)
    with pytest.raises(SpatialInputError, match="spot spacing"):
        read_visium(tmp_path, section_id="s")


def test_refuses_coordinates_outside_the_image(tmp_path):
    make_visium(tmp_path, hires_scale=0.2)
    # the image says the tissue is 4x smaller than the coordinates imply
    sf = json.loads((tmp_path / "spatial" / "scalefactors_json.json").read_text())
    sf["tissue_hires_scalef"] = 0.8
    (tmp_path / "spatial" / "scalefactors_json.json").write_text(json.dumps(sf))
    with pytest.raises(SpatialInputError, match="outside the tissue image"):
        read_visium(tmp_path, section_id="s")


def test_refuses_missing_spatial_folder_and_visium_hd_parent(tmp_path):
    (tmp_path / "a" / "filtered_feature_bc_matrix").mkdir(parents=True)
    with pytest.raises(SpatialInputError, match="no spatial/"):
        read_visium(tmp_path / "a", section_id="s")
    (tmp_path / "hd" / "binned_outputs" / "square_008um").mkdir(parents=True)
    (tmp_path / "hd" / "binned_outputs" / "square_016um").mkdir(parents=True)
    with pytest.raises(SpatialInputError, match="square_008um"):
        read_visium(tmp_path / "hd", section_id="s")


def test_visium_hd_bin_reads_as_a_square_grid(tmp_path):
    d = tmp_path / "binned_outputs" / "square_008um"
    make_visium(d, hd=True, positions="parquet", image=False, rows=10, cols=6)
    sec = read_visium(d, section_id="hd")
    assert sec.platform == "visium_hd" and sec.grid == "square"
    g = build_graph([sec], mode="grid")
    assert g.summary()["degree_counts"][4] > 0 and max(g.degree) == 4


def test_h5ad_without_tissue_coordinates_is_refused(tmp_path):
    h5py = pytest.importorskip("h5py")
    from bioagent.spatial import read_h5ad_spatial
    p = tmp_path / "x.h5ad"
    with h5py.File(p, "w") as f:
        f.create_dataset("X", data=np.ones((3, 2)))
        f.create_group("obsm").create_dataset("X_umap", data=np.zeros((3, 2)))
    with pytest.raises(SpatialInputError, match="UMAP is not a tissue position"):
        read_h5ad_spatial(p)


# Graph ------------------------------------------------------------------------------------

def test_hex_grid_neighbours_and_section_isolation(tmp_path):
    make_visium(tmp_path / "a", seed=1)
    make_visium(tmp_path / "b", seed=2)     # identical coordinates, a different tissue
    a = read_visium(tmp_path / "a", section_id="a")
    b = read_visium(tmp_path / "b", section_id="b")
    g = build_graph([a, b], mode="grid")
    assert int(np.bincount(g.degree).argmax()) == 6
    assert (g.section[g.src] == g.section[g.dst]).all()
    for mode, kw in (("knn", {"k": 6}), ("radius", {"radius": PITCH * 1.05})):
        gg = build_graph([a, b], mode=mode, **kw)
        assert (gg.section[gg.src] == gg.section[gg.dst]).all()
    bad = SpatialGraph(np.array([0]), np.array([a.n_spots]), g.n, g.section, "x")
    with pytest.raises(AssertionError, match="join different sections"):
        assert_section_isolated(bad)


# Statistics -------------------------------------------------------------------------------

def _grid_graph(n: int = 12):
    xs, ys = np.meshgrid(np.arange(n), np.arange(n))
    rc = np.c_[ys.ravel(), xs.ravel()]
    from bioagent.spatial.graph import _SQ4, _grid_edges
    s, t = _grid_edges(rc, _SQ4, 1)
    return SpatialGraph(s, t, len(rc), np.zeros(len(rc), int), "grid"), rc


def test_morans_i_matches_the_dense_formula():
    g, rc = _grid_graph()
    rng = np.random.default_rng(0)
    v = np.c_[rc[:, 1].astype(float), rng.normal(size=len(rc))]
    W = np.zeros((g.n, g.n))
    W[g.src, g.dst] = 1.0
    W = W / W.sum(1, keepdims=True)
    z = v - v.mean(0)
    ref = g.n / W.sum() * np.einsum("ij,ik,jk->k", W, z, z) / (z * z).sum(0)
    r = morans_i(v, g)
    assert np.allclose(r["I"], ref)
    assert r["I"][0] > 0.9 and r["p_norm"][0] < 1e-10 and r["p_norm"][1] > 0.01


def test_morans_i_null_moments_by_permutation():
    g, _ = _grid_graph(10)
    rng = np.random.default_rng(1)
    vals = rng.normal(size=(g.n, 400))
    r = morans_i(vals, g)
    assert abs(r["I"].mean() - r["expected"][0]) < 0.01
    assert 0.8 < r["I"].var() / r["var_norm"][0] < 1.25
    assert 0.02 < (r["p_norm"] < 0.05).mean() < 0.09


def test_neighbourhood_enrichment_detects_segregation():
    g, rc = _grid_graph()
    lab = np.where(rc[:, 1] < 6, "a", "b")
    r = neighbourhood_enrichment(lab, g, n_perms=200)
    assert r["z"][0, 0] > 5 and r["z"][0, 1] < -5
    mixed = np.random.default_rng(0).choice(["a", "b"], g.n)
    r0 = neighbourhood_enrichment(mixed, g, n_perms=200)
    assert np.abs(r0["z"]).max() < 3.5


def test_expression_steps_are_deterministic():
    rng = np.random.default_rng(0)
    m = CSR.from_dense(rng.poisson(2, (80, 40)).astype(np.float32))
    ln, f = normalise(m, 1e4)
    assert np.allclose(np.expm1(ln.to_dense()).sum(1), 1e4, rtol=1e-4)
    hv = highly_variable(m, f, 10)
    assert len(hv) == 10
    pcs1, _ = pca(ln.to_dense(np.float64), 5)
    pcs2, _ = pca(ln.to_dense(np.float64), 5)
    assert np.array_equal(pcs1, pcs2)
    l1, _ = kmeans(pcs1, 3, seed=2)
    l2, _ = kmeans(pcs1, 3, seed=2)
    assert np.array_equal(l1, l2)


# Pipeline and output contract ---------------------------------------------------------------

def _config(paths: dict[str, Path], **extra) -> dict:
    return {"sections": [{"path": str(p), "section_id": sid, "subject_id": f"donor_{sid}",
                          "prefer": "mex"} for sid, p in paths.items()],
            "qc": {"min_counts": 10, "min_genes": 5, "min_spots_per_gene": 3},
            "expression": {"n_hvg": 40, "n_pcs": 10, "k": 3}, "nhood_perms": 100,
            "seed": 3, **extra}


CONTRACT = ("qc.json", "clusters.tsv", "markers.tsv", "spatial_statistics.tsv",
            "neighborhood_results.tsv", "provenance.json", "limitations.md", "summary.json")


def test_pipeline_writes_the_contract_and_finds_planted_genes(tmp_path):
    make_visium(tmp_path / "in", seed=4)
    out = tmp_path / "out"
    summary = run_spatial(_config({"s1": tmp_path / "in"}), out)
    for name in CONTRACT:
        assert (out / name).exists(), name
    assert any((out / "figures").glob("s1_clusters.svg"))
    top = [r["gene"] for r in summary["top_spatially_variable"][:3]]
    assert "SPAT_LEFT" in top and "SPAT_BLOB" in top
    prov = json.loads((out / "provenance.json").read_text())
    assert prov["seed"] == 3 and all(v.startswith("sha256:") for v in prov["inputs"].values())
    assert "One subject" in (out / "limitations.md").read_text()
    assert summary["claim_scope"].startswith("within-section")


def test_pipeline_is_reproducible(tmp_path):
    make_visium(tmp_path / "in", seed=5)
    a = run_spatial(_config({"s": tmp_path / "in"}), tmp_path / "a")
    b = run_spatial(_config({"s": tmp_path / "in"}), tmp_path / "b")
    pa = json.loads((tmp_path / "a" / "provenance.json").read_text())["outputs"]
    pb = json.loads((tmp_path / "b" / "provenance.json").read_text())["outputs"]
    det = [k for k in pa if k.endswith((".tsv", ".svg", ".json"))]
    assert det and all(pa[k] == pb[k] for k in det)
    assert a["top_spatially_variable"] == b["top_spatially_variable"]


def test_two_sections_stay_apart(tmp_path):
    make_visium(tmp_path / "a", seed=6)
    make_visium(tmp_path / "b", seed=7)
    s = run_spatial(_config({"a": tmp_path / "a", "b": tmp_path / "b"}), tmp_path / "out")
    assert [x["section_id"] for x in s["sections"]] == ["a", "b"]
    lim = (tmp_path / "out" / "limitations.md").read_text()
    assert "not registered" in lim


def test_processed_h5ad_has_anndata_layout(tmp_path):
    h5py = pytest.importorskip("h5py")
    make_visium(tmp_path / "in", seed=8)
    run_spatial(_config({"s": tmp_path / "in"}), tmp_path / "out")
    with h5py.File(tmp_path / "out" / "processed.h5ad", "r") as f:
        assert f.attrs["encoding-type"] == "anndata"
        assert f["X"].attrs["encoding-type"] == "csr_matrix"
        assert f["obsm"]["spatial"].shape[1] == 2
        assert "counts" in f["layers"] and "spatial_connectivities" in f["obsp"]
        assert f["obs"]["cluster"].attrs["encoding-type"] == "categorical"


def test_config_rejects_unknown_keys_and_repeated_sections(tmp_path):
    from bioagent.spatial import load_config
    with pytest.raises(ValueError, match="unknown configuration keys"):
        load_config({"sections": [{"path": "x", "section_id": "a"}], "foo": 1})
    with pytest.raises(ValueError, match="repeat"):
        load_config({"sections": [{"path": "x", "section_id": "a"},
                                  {"path": "y", "section_id": "a"}]})


# Isolated execution -------------------------------------------------------------------------

def test_run_isolated_returns_paths_and_summary_not_data(tmp_path):
    make_visium(tmp_path / "in", seed=9)
    r = run_isolated(_config({"s": tmp_path / "in"}), tmp_path / "out",
                     AnalysisEnvironment(timeout_s=600, memory_mb=4096))
    assert r["status"] == "succeeded", r.get("error")
    assert r["summary"]["spots"] > 0 and Path(r["provenance"]).exists()
    assert r["environment"]["usable"] and r["environment"]["memory_mb"] == 4096
    assert len(json.dumps(r)) < 20000


def test_run_isolated_statuses(tmp_path):
    r = run_isolated(_config({"s": tmp_path / "missing"}), tmp_path / "o1")
    assert r["status"] == "input_error"
    make_visium(tmp_path / "bad", bad_scale=True)
    r = run_isolated(_config({"s": tmp_path / "bad"}), tmp_path / "o2")
    assert r["status"] == "input_error" and "spot spacing" in r["error"]
    make_visium(tmp_path / "ok", seed=1)
    r = run_isolated(_config({"s": tmp_path / "ok"}), tmp_path / "o3",
                     AnalysisEnvironment(timeout_s=0.01))
    assert r["status"] == "timeout"
    r = run_isolated(_config({"s": tmp_path / "ok"}), tmp_path / "o4",
                     AnalysisEnvironment(python="/nonexistent/python"))
    assert r["status"] == "unavailable"


# Execution backends -------------------------------------------------------------------------

def test_subprocess_backend_takes_an_interpreter_and_limits():
    b = SubprocessBackend(python=sys.executable, timeout_s=900, memory_mb=2048,
                          env={"PATH": "/usr/bin"})
    assert b.python == sys.executable and b.timeout_s == 900 and b.memory_mb == 2048
    assert b._preexec() is not None
    assert SubprocessBackend()._preexec() is None and SubprocessBackend().timeout_s == 120.0


def test_container_mounts_are_checked(tmp_path):
    args = ContainerBackend._mount_args([{"host": str(tmp_path), "container": "/data"},
                                         {"host": str(tmp_path), "container": "/out",
                                          "mode": "rw"}])
    assert args == ["-v", f"{tmp_path}:/data:ro", "-v", f"{tmp_path}:/out:rw"]
    for bad in ({"host": "relative", "container": "/d"},
                {"host": str(tmp_path / "nope"), "container": "/d"},
                {"host": str(tmp_path), "container": "d"},
                {"host": str(tmp_path), "container": "/d", "mode": "x"}):
        with pytest.raises(ValueError):
            ContainerBackend._mount_args([bad])
    assert ContainerBackend._resource_args("8g", 4) == ["--memory", "8g", "--cpus", "4"]
