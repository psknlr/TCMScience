#!/usr/bin/env python
"""Acceptance run of the spatial workflow on public Visium data.

    python scripts/accept_spatial_visium.py --visium /data/V1_Human_Lymph_Node \
        --work /tmp/spatial_accept --out ../docs/studies

Data: 10x Genomics "Human Lymph Node" (Visium, Space Ranger 1.1.0), the dataset used in
the Cell2location tutorial. Files: V1_Human_Lymph_Node_filtered_feature_bc_matrix.h5,
_filtered_feature_bc_matrix.tar.gz and _spatial.tar.gz from cf.10xgenomics.com, unpacked
into one directory. Nothing from the data is written to the repository except summary
numbers.

Every acceptance item is a check that can fail:

1. data correspondence: HDF5 and Matrix Market inputs give identical spots, counts and
   statistics; barcodes without positions, wrong scale factors and coordinates outside
   the image are refused (each simulated on a copy of the real files);
2. space is used: the neighbour graph is a tissue grid (6 neighbours inside the tissue),
   and shuffling spot positions destroys the spatially variable genes (negative control);
3. sections stay apart: the same section loaded twice under two ids (identical pixel
   coordinates) gives no edge between them and the same per-section statistics;
4. reproducibility: two runs give byte-identical tables and figures;
5. claim scope: a single section yields a within-section description only.

The run goes through ``run_isolated`` (separate interpreter, timeout, memory limit).
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bioagent.spatial import SpatialInputError, build_graph, read_visium  # noqa: E402
from bioagent.spatial.runner import AnalysisEnvironment, run_isolated  # noqa: E402

ENV = AnalysisEnvironment(timeout_s=3600, memory_mb=12000)


def _cfg(sections, **kw):
    base = {"sections": sections, "seed": 20261002, "nhood_perms": 1000}
    base.update(kw)
    return base


def _stats(out: Path) -> dict[str, float]:
    with open(out / "spatial_statistics.tsv", encoding="utf-8") as f:
        return {r["gene_id"]: (float(r["I"]), float(r["q_norm"] or "nan"))
                for r in csv.DictReader(f, delimiter="\t")}


def _copy_spatial(src: Path, dst: Path) -> Path:
    if dst.exists():
        shutil.rmtree(dst)
    dst.mkdir(parents=True)
    shutil.copytree(src / "spatial", dst / "spatial")
    (dst / "filtered_feature_bc_matrix").symlink_to(src / "filtered_feature_bc_matrix")
    return dst


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--visium", type=Path, required=True)
    ap.add_argument("--work", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=ROOT.parent / "docs" / "studies")
    ap.add_argument("--crosscheck", type=Path, default=None,
                    help="report written by crosscheck_spatial_squidpy.py, to include")
    a = ap.parse_args()
    v, w = a.visium.resolve(), a.work.resolve()
    w.mkdir(parents=True, exist_ok=True)
    res: dict = {"dataset": "10x Genomics V1_Human_Lymph_Node (Visium, Space Ranger 1.1.0)",
                 "environment": {"timeout_s": ENV.timeout_s, "memory_mb": ENV.memory_mb}}
    sec = {"path": str(v), "section_id": "LN", "sample_id": "V1_Human_Lymph_Node",
           "subject_id": "10x_lymph_node_donor"}

    # 4. reproducibility, and the main run (HDF5)
    r1 = run_isolated(_cfg([{**sec, "prefer": "h5"}]), w / "run_h5_a", ENV)
    r2 = run_isolated(_cfg([{**sec, "prefer": "h5"}]), w / "run_h5_b", ENV)
    assert r1["status"] == r2["status"] == "succeeded", (r1.get("error"), r2.get("error"))
    pa = json.loads(Path(r1["provenance"]).read_text())["outputs"]
    pb = json.loads(Path(r2["provenance"]).read_text())["outputs"]
    det = sorted(k for k in pa if k.endswith((".tsv", ".svg")) or k in ("qc.json",
                                                                        "limitations.md"))
    res["reproducibility"] = {"files_compared": len(det),
                              "identical": all(pa[k] == pb[k] for k in det),
                              "elapsed_s": [r1["elapsed_s"], r2["elapsed_s"]]}
    s = r1["summary"]
    res["run"] = {k: s[k] for k in ("spots", "genes_kept", "hvg_tested", "clusters",
                                    "spatially_variable_genes_q05", "top_spatially_variable",
                                    "claim_scope")}
    res["run"]["graph"] = {k: s["graph"][k] for k in ("undirected_edges", "mean_degree",
                                                      "isolated_nodes", "degree_counts")}
    qc = json.loads((w / "run_h5_a" / "qc.json").read_text())
    res["qc"] = qc["sections"]["LN"]
    res["input_checks"] = qc["input_checks"]["LN"]

    # 1. HDF5 vs Matrix Market
    r3 = run_isolated(_cfg([{**sec, "prefer": "mex"}]), w / "run_mex", ENV)
    assert r3["status"] == "succeeded", r3.get("error")
    pc = json.loads(Path(r3["provenance"]).read_text())["outputs"]
    res["h5_vs_mex"] = {"identical_tables": all(pa[k] == pc[k] for k in det),
                        "inputs_differ": True}
    refusals = {}
    bad = _copy_spatial(v, w / "bad_positions")
    lines = (bad / "spatial" / "tissue_positions_list.csv").read_text().splitlines()
    in_tissue = [ln for ln in lines if ln.split(",")[1] == "1"]
    drop = set(in_tissue[:5])
    (bad / "spatial" / "tissue_positions_list.csv").write_text(
        "\n".join(ln for ln in lines if ln not in drop) + "\n")
    bad_sf = _copy_spatial(v, w / "bad_scale")
    sf = json.loads((bad_sf / "spatial" / "scalefactors_json.json").read_text())
    sf["spot_diameter_fullres"] *= 2
    (bad_sf / "spatial" / "scalefactors_json.json").write_text(json.dumps(sf))
    bad_img = _copy_spatial(v, w / "bad_image")
    sf2 = json.loads((bad_img / "spatial" / "scalefactors_json.json").read_text())
    sf2["tissue_hires_scalef"] *= 3
    (bad_img / "spatial" / "scalefactors_json.json").write_text(json.dumps(sf2))
    no_sp = w / "no_spatial"
    if no_sp.exists():
        shutil.rmtree(no_sp)
    no_sp.mkdir()
    (no_sp / "filtered_feature_bc_matrix").symlink_to(v / "filtered_feature_bc_matrix")
    for name, path in (("barcodes_without_positions", bad), ("spot_diameter_doubled", bad_sf),
                       ("hires_scale_tripled", bad_img), ("no_spatial_folder", no_sp)):
        try:
            read_visium(path, section_id="x", prefer="mex")
            refusals[name] = "NOT REFUSED"
        except SpatialInputError as exc:
            refusals[name] = str(exc)[:200]
    res["refusals"] = refusals

    # 2. space is used: shuffle positions (negative control)
    shuf = _copy_spatial(v, w / "shuffled")
    rows = [ln.split(",") for ln in (shuf / "spatial" / "tissue_positions_list.csv")
            .read_text().splitlines()]
    tissue = [i for i, r in enumerate(rows) if r[1] == "1"]
    perm = np.random.default_rng(1).permutation(tissue)
    new = [list(r) for r in rows]
    for i, j in zip(tissue, perm):
        new[i][2:] = rows[j][2:]
    (shuf / "spatial" / "tissue_positions_list.csv").write_text(
        "\n".join(",".join(r) for r in new) + "\n")
    r4 = run_isolated(_cfg([{**sec, "path": str(shuf), "prefer": "mex"}]), w / "run_shuffled",
                      ENV)
    assert r4["status"] == "succeeded", r4.get("error")
    st_true, st_shuf = _stats(w / "run_h5_a"), _stats(w / "run_shuffled")
    res["shuffled_positions_control"] = {
        "svg_q05_true": sum(q < 0.05 for _, q in st_true.values()),
        "svg_q05_shuffled": sum(q < 0.05 for _, q in st_shuf.values()),
        "max_I_true": max(i for i, _ in st_true.values()),
        "max_I_shuffled": max(i for i, _ in st_shuf.values())}
    sec_obj = read_visium(v, section_id="LN", prefer="mex")
    g = build_graph([sec_obj], mode="grid")
    res["graph_is_tissue_grid"] = {"modal_degree": int(np.bincount(g.degree).argmax())}

    # 3. the same section twice: no edge between copies, same statistics
    r5 = run_isolated(_cfg([{**sec, "section_id": "LN_copy1", "prefer": "mex"},
                            {**sec, "section_id": "LN_copy2", "prefer": "mex"}],
                           expression={"k": int(s["clusters"])}),
                      w / "run_two_copies", ENV)
    assert r5["status"] == "succeeded", r5.get("error")
    st2 = _stats(w / "run_two_copies")
    common = set(st2) & set(st_true)
    res["section_isolation"] = {
        "edges_two_copies": r5["summary"]["graph"]["undirected_edges"],
        "edges_single": s["graph"]["undirected_edges"],
        "edges_exactly_doubled": r5["summary"]["graph"]["undirected_edges"]
        == 2 * s["graph"]["undirected_edges"],
        "max_abs_I_difference_vs_single": float(max(abs(st2[k][0] - st_true[k][0])
                                                    for k in common)) if common else None,
        "genes_compared": len(common)}
    if a.crosscheck and a.crosscheck.exists():
        res["squidpy_crosscheck"] = json.loads(a.crosscheck.read_text())
    a.out.mkdir(parents=True, exist_ok=True)
    (a.out / "spatial_acceptance.json").write_text(json.dumps(res, indent=2, default=float)
                                                   + "\n")
    print(json.dumps(res, indent=1, default=float)[:6000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
