#!/usr/bin/env python
"""Cross-check ``bioagent.spatial`` against Scanpy/Squidpy on the same data.

Run with an interpreter that has scanpy and squidpy (they are not dependencies of the
harness), after ``python -m bioagent.spatial`` has produced an output directory:

    /path/to/venv/bin/python scripts/crosscheck_spatial_squidpy.py \
        --visium /data/V1_Human_Lymph_Node --out-dir results/ --report crosscheck.json

It compares, on the same spots and the same log-normalised matrix:

1. reading: barcodes, tissue positions and total counts against ``scanpy.read_visium``;
2. the output object: ``processed.h5ad`` must open with ``anndata``;
3. the neighbour graph: identical edges to ``squidpy.gr.spatial_neighbors`` (Visium grid);
4. Moran's I: identical values to ``squidpy.gr.spatial_autocorr`` (row-standardised);
5. neighbourhood enrichment: z-scores against ``squidpy.gr.nhood_enrichment`` on the
   same cluster labels (both are permutation estimates, so agreement is approximate).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--visium", type=Path, required=True)
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--report", type=Path, required=True)
    ap.add_argument("--perms", type=int, default=1000)
    a = ap.parse_args()

    import anndata
    import pandas as pd
    import squidpy as sq

    from importlib.metadata import version
    rep: dict = {"versions": {p: version(p) for p in ("anndata", "scanpy", "squidpy")}}
    ours = anndata.read_h5ad(a.out_dir / "processed.h5ad")
    rep["h5ad_opens_with_anndata"] = True
    rep["shape"] = list(ours.shape)

    # 1. reading
    from squidpy.read import visium
    h5 = sorted(a.visium.glob("*filtered_feature_bc_matrix.h5"))[0]
    ref = visium(a.visium, counts_file=h5.name)
    ref.var_names_make_unique()
    bcs = [s.split(":", 1)[1] for s in ours.obs_names]
    ref = ref[bcs].copy()
    rep["reading"] = {
        "spots_compared": len(bcs),
        "max_abs_coordinate_difference_px": float(np.abs(
            np.asarray(ref.obsm["spatial"], float) - np.asarray(ours.obsm["spatial"])).max()),
        "max_abs_total_counts_difference": float(np.abs(
            np.asarray(ref.X.sum(1)).ravel() - ours.obs["total_counts"].to_numpy()).max()),
    }

    # 3. graph
    sq.gr.spatial_neighbors(ours, coord_type="grid", n_neighs=6, n_rings=1,
                            key_added="sq")
    a_sq = ours.obsp["sq_connectivities"].tocoo()
    a_ours = ours.obsp["spatial_connectivities"].tocoo()
    e_sq = set(zip(a_sq.row.tolist(), a_sq.col.tolist()))
    e_ours = set(zip(a_ours.row.tolist(), a_ours.col.tolist()))
    rep["graph"] = {"edges_ours": len(e_ours), "edges_squidpy": len(e_sq),
                    "only_ours": len(e_ours - e_sq), "only_squidpy": len(e_sq - e_ours)}

    # 4. Moran's I on the same matrix, same genes, same graph
    stats = pd.read_csv(a.out_dir / "spatial_statistics.tsv", sep="\t")
    genes = ours.var_names[ours.var["highly_variable"].to_numpy()]
    sq.gr.spatial_autocorr(ours, connectivity_key="spatial_connectivities", genes=list(genes),
                           mode="moran", transformation=True, n_perms=None)
    m = ours.uns["moranI"]
    joined = stats.set_index("gene_id").join(m[["I"]].rename(columns={"I": "I_sq"}))
    d = (joined["I"] - joined["I_sq"]).abs()
    top_ours = set(stats.head(100)["gene_id"])
    top_sq = set(m.sort_values("I", ascending=False).head(100).index)
    rep["morans_i"] = {"genes": int(joined["I_sq"].notna().sum()),
                       "max_abs_difference": float(d.max()),
                       "pearson_r": float(np.corrcoef(joined["I"], joined["I_sq"])[0, 1]),
                       "top100_overlap": len(top_ours & top_sq)}
    if "pval_norm" in m:
        # Same variance; Squidpy reports the tail in the direction of z, the harness the
        # upper tail (positive autocorrelation). They must agree wherever z > 0.
        joined2 = stats.set_index("gene_id").join(m[["pval_norm"]])
        pos = joined2["z"] > 0
        rep["morans_i"]["genes_with_positive_z"] = int(pos.sum())
        rep["morans_i"]["max_abs_p_difference_positive_z"] = float(
            (joined2.loc[pos, "p_norm"] - joined2.loc[pos, "pval_norm"]).abs().max())

    # 5. neighbourhood enrichment on our clusters
    ours.obs["cluster"] = ours.obs["cluster"].astype("category")
    sq.gr.nhood_enrichment(ours, cluster_key="cluster", connectivity_key="spatial",
                           n_perms=a.perms, seed=0, show_progress_bar=False)
    z_sq = ours.uns["cluster_nhood_enrichment"]["zscore"]
    cats = list(ours.obs["cluster"].cat.categories)
    nh = pd.read_csv(a.out_dir / "neighborhood_results.tsv", sep="\t", dtype={"cluster_a": str,
                                                                              "cluster_b": str})
    z_ours = np.array([[nh[(nh.cluster_a == x) & (nh.cluster_b == y)]["z"].iloc[0]
                        for y in cats] for x in cats])
    # pairs whose permutation SD is 0 (no edge possible in any permutation) have no z
    ok = np.isfinite(z_ours) & np.isfinite(z_sq)
    rep["nhood_enrichment"] = {"clusters": len(cats), "pairs_compared": int(ok.sum()),
                               "pairs_without_z": int((~ok).sum()),
                               "pearson_r_z": float(np.corrcoef(z_ours[ok], z_sq[ok])[0, 1]),
                               "max_abs_z_difference": float(np.abs(z_ours - z_sq)[ok].max()),
                               "sign_agreement": float(np.mean(np.sign(z_ours[ok]) ==
                                                               np.sign(z_sq[ok])))}
    a.report.write_text(json.dumps(rep, indent=2))
    print(json.dumps(rep, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
