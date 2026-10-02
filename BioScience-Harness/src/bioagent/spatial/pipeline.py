"""The processed-data spatial transcriptomics pipeline, and its output contract.

    input check → read (positions, scale factors, image bounds) → QC → normalise,
    variable genes, PCA, expression clusters, markers → neighbour graph per section →
    Moran's I, neighbourhood enrichment → tables, figures, provenance, limitations

Output directory::

    processed.h5ad              AnnData-compatible (X log-normalised, layers['counts'],
                                obsm['spatial'], obsm['X_pca'], obsp['spatial_connectivities'])
                                when h5py is present; otherwise processed.npz
    qc.json                     per-section QC, thresholds, spots and genes kept
    clusters.tsv                spot → section, coordinates, expression cluster
    markers.tsv                 top genes per expression cluster (effect sizes, no p values)
    spatial_statistics.tsv      Moran's I per gene (I, z, p, BH q)
    neighborhood_results.tsv    cluster × cluster neighbourhood enrichment (z, p)
    figures/*.svg               clusters and top spatially variable genes on the tissue
    provenance.json             input and output hashes, parameters, seed, versions
    limitations.md              what this run can and cannot support
    summary.json                small summary for the harness (never the matrix)

The run is deterministic for a given input and configuration: the seed fixes k-means
starts and permutations, and outputs carry content hashes so a rerun can be compared.
"""

from __future__ import annotations

import json
import platform
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import numpy as np

from .expression import ExpressionParams, choose_k, highly_variable, kmeans, markers, \
    normalise, pca_csr
from .graph import build_graph
from .io import SpatialSection, read_h5ad_spatial, read_visium, sha256_file
from .matrix import CSR
from .outputs import svg_spots, write_h5ad
from .qc import QCParams, apply_qc
from .statistics import morans_i, neighbourhood_enrichment

__all__ = ["SectionInput", "SpatialConfig", "load_config", "run_spatial", "PIPELINE_VERSION"]

PIPELINE_VERSION = "0.1.0"


@dataclass(frozen=True)
class SectionInput:
    path: str
    section_id: str
    format: str = "visium"            # visium, h5ad
    sample_id: str = ""
    subject_id: str = ""
    condition: str = ""
    section_key: str = ""             # h5ad: obs column splitting sections
    prefer: str = "h5"                # visium: "h5" or "mex" when both are present


@dataclass(frozen=True)
class SpatialConfig:
    sections: tuple[SectionInput, ...]
    qc: QCParams = QCParams()
    expression: ExpressionParams = ExpressionParams()
    graph_mode: str = "auto"          # grid where the platform has one, else knn
    graph_rings: int = 1
    graph_k: int = 6
    graph_radius: float | None = None
    moran_transformation: str = "r"
    moran_perms: int = 0
    nhood_perms: int = 1000
    n_svg_figures: int = 4
    registration: str | None = None   # None: sections are never joined
    seed: int = 0
    extra: Mapping[str, Any] = field(default_factory=dict)


def load_config(obj: Mapping[str, Any] | str | Path) -> SpatialConfig:
    if not isinstance(obj, Mapping):
        p = Path(obj)
        text = p.read_text(encoding="utf-8")
        if p.suffix in (".yaml", ".yml"):
            import yaml
            obj = yaml.safe_load(text)
        else:
            obj = json.loads(text)
    d = dict(obj)
    secs = tuple(SectionInput(**s) for s in d.pop("sections"))
    if not secs:
        raise ValueError("a run needs at least one section")
    ids = [s.section_id for s in secs]
    if len(set(ids)) != len(ids):
        raise ValueError(f"section ids repeat: {ids}")
    qc = QCParams(**{k: (tuple(v) if k == "mt_prefixes" else v)
                     for k, v in d.pop("qc", {}).items()})
    ex = d.pop("expression", {})
    if "k_range" in ex:
        ex["k_range"] = tuple(ex["k_range"])
    expr = ExpressionParams(**ex)
    known = {f for f in SpatialConfig.__dataclass_fields__}
    unknown = set(d) - known
    if unknown:
        raise ValueError(f"unknown configuration keys {sorted(unknown)}")
    return SpatialConfig(sections=secs, qc=qc, expression=expr, **d)


def _read(si: SectionInput) -> list[SpatialSection]:
    if si.format == "visium":
        return [read_visium(si.path, section_id=si.section_id, sample_id=si.sample_id,
                            subject_id=si.subject_id, condition=si.condition,
                            prefer=si.prefer)]
    if si.format == "h5ad":
        return read_h5ad_spatial(si.path, section_id=si.section_id, section_key=si.section_key,
                                 sample_id=si.sample_id, subject_id=si.subject_id,
                                 condition=si.condition)
    raise ValueError(f"unknown input format {si.format!r}")


def _vstack(mats: list[CSR]) -> CSR:
    data = np.concatenate([m.data for m in mats])
    indices = np.concatenate([m.indices for m in mats])
    ptrs, off = [np.zeros(1, np.int64)], 0
    for m in mats:
        ptrs.append(m.indptr[1:] + off)
        off += m.nnz
    return CSR(data, indices, np.concatenate(ptrs),
               (sum(m.shape[0] for m in mats), mats[0].shape[1]))


def _write_tsv(path: Path, rows: list[Mapping[str, Any]], cols: list[str]) -> None:
    def fmt(v):
        if isinstance(v, float):
            return "" if not np.isfinite(v) else f"{v:.6g}"
        return str(v)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\t".join(cols) + "\n")
        for r in rows:
            f.write("\t".join(fmt(r.get(c, "")) for c in cols) + "\n")


def _limitations(cfg: SpatialConfig, sections: list[SpatialSection], k: int) -> str:
    subjects = {s.subject_id for s in sections}
    conds = {s.condition for s in sections if s.condition}
    lines = ["# Limitations of this run", ""]
    if len(subjects) < 2:
        lines.append("- One subject: the run shows that the pipeline works on this tissue and "
                     "describes it. It supports no difference between patients, conditions or "
                     "treatments.")
    elif conds:
        lines.append(f"- {len(subjects)} subjects across conditions {sorted(conds)}: any "
                     "comparison must use one value per subject (e.g. "
                     "bioagent.studies.spatial.compare_neighbourhoods), never spots.")
    lines += [
        f"- Expression clusters (k = {k}) are computed without spatial information and drawn "
        "on the tissue afterwards; they are not spatial domains.",
        "- A Visium spot (55 µm) averages several cells; clusters and statistics describe "
        "spots, not cells. No deconvolution was run; any cell-type abundance would be a "
        "model estimate, not an observed identity.",
        "- Moran's I p values assume normality and treat the section as one realisation; "
        "they rank genes by spatial structure within this tissue and do not generalise to "
        "other samples.",
        "- Marker genes are contrasts between clusters defined from the same data, so they "
        "carry effect sizes only.",
    ]
    if cfg.registration is None and len(sections) > 1:
        lines.append("- Sections were not registered: no spatial edge joins two sections, and "
                     "statistics are pooled only after within-section centring/permutation.")
    for s in sections:
        if s.platform == "h5ad" and not s.scalefactors:
            lines.append(f"- {s.section_id}: no scale factors in the .h5ad, so distances are "
                         "in the file's coordinate units and no image overlay is checked.")
    return "\n".join(lines) + "\n"


def run_spatial(config: SpatialConfig | Mapping[str, Any] | str | Path,
                out_dir: str | Path) -> dict:
    t0 = time.perf_counter()
    cfg = config if isinstance(config, SpatialConfig) else load_config(config)
    out = Path(out_dir)
    (out / "figures").mkdir(parents=True, exist_ok=True)
    rng_seed = cfg.seed

    sections: list[SpatialSection] = []
    for si in cfg.sections:
        sections.extend(_read(si))
    inputs = {}
    for s in sections:
        inputs.update(s.source_files)
    input_checks = {s.section_id: s.checks for s in sections}

    sections, qc_report, qc_metrics = apply_qc(sections, cfg.qc)
    counts = _vstack([s.counts for s in sections])
    genes = sections[0].gene_names
    gene_ids = sections[0].gene_ids
    lognorm, factors = normalise(counts, cfg.expression.target_sum)
    hvg = highly_variable(counts, factors, cfg.expression.n_hvg)
    hv_mat = lognorm.take_cols(hvg)          # sparse; densified in blocks only
    pcs, var_ratio = pca_csr(hv_mat, cfg.expression.n_pcs)
    if cfg.expression.k:
        k, sil = cfg.expression.k, {}
    else:
        k, sil = choose_k(pcs[:, :15], cfg.expression.k_range, seed=rng_seed)
    labels, _ = kmeans(pcs[:, :15], k, seed=rng_seed)
    small = [int(c) for c, n_ in zip(*np.unique(labels, return_counts=True)) if n_ < 10]
    mk = markers(lognorm, labels, genes, cfg.expression.n_markers, gene_ids)

    graph = build_graph(sections, mode=cfg.graph_mode, rings=cfg.graph_rings, k=cfg.graph_k,
                        radius=cfg.graph_radius)
    mi = morans_i(hv_mat, graph, transformation=cfg.moran_transformation,
                  n_perms=cfg.moran_perms, seed=rng_seed)
    nh = neighbourhood_enrichment(labels, graph, n_perms=cfg.nhood_perms, seed=rng_seed)

    # tables ---------------------------------------------------------------------------
    sec_of = np.concatenate([[s.section_id] * s.n_spots for s in sections])
    bcs = np.concatenate([s.barcodes for s in sections])
    xy = np.concatenate([s.xy for s in sections])
    clusters = [{"spot": f"{sid}:{b}", "section": sid, "barcode": b, "x_px": float(p[0]),
                 "y_px": float(p[1]), "cluster": int(c)}
                for sid, b, p, c in zip(sec_of, bcs, xy, labels)]
    _write_tsv(out / "clusters.tsv", clusters,
               ["spot", "section", "barcode", "x_px", "y_px", "cluster"])
    _write_tsv(out / "markers.tsv", mk, ["cluster", "gene", "gene_id", "t", "log2fc_approx",
                                         "pct_in",
                                         "pct_out", "mean_log_in", "mean_log_out"])
    stat_rows = [{"gene": str(genes[g]), "gene_id": str(gene_ids[g]), "I": float(mi["I"][j]),
                  "expected": float(mi["expected"][j]), "z": float(mi["z"][j]),
                  "p_norm": float(mi["p_norm"][j]), "q_norm": float(mi["q_norm"][j]),
                  **({"p_perm": float(mi["p_perm"][j])} if "p_perm" in mi else {})}
                 for j, g in enumerate(hvg)]
    stat_rows.sort(key=lambda r: -r["I"] if np.isfinite(r["I"]) else np.inf)
    _write_tsv(out / "spatial_statistics.tsv", stat_rows,
               ["gene", "gene_id", "I", "expected", "z", "p_norm", "q_norm"]
               + (["p_perm"] if "p_perm" in mi else []))
    nh_rows = []
    for a, la in enumerate(nh["labels"]):
        for b, lb in enumerate(nh["labels"]):
            nh_rows.append({"cluster_a": la, "cluster_b": lb,
                            "observed_edges": int(nh["observed"][a, b]),
                            "expected_edges": float(nh["expected"][a, b]),
                            "z": float(nh["z"][a, b]), "p_enriched": float(nh["p_enriched"][a, b]),
                            "p_depleted": float(nh["p_depleted"][a, b])})
    _write_tsv(out / "neighborhood_results.tsv", nh_rows,
               ["cluster_a", "cluster_b", "observed_edges", "expected_edges", "z",
                "p_enriched", "p_depleted"])

    # figures (per section) ---------------------------------------------------------------------
    top = [(r["gene"], r["gene_id"]) for r in stat_rows[:cfg.n_svg_figures]]
    gene_col = {str(gene_ids[g]): j for j, g in enumerate(hvg)}     # ids: symbols repeat
    dup = {n_ for n_, c in zip(*np.unique(genes.astype(str), return_counts=True)) if c > 1}
    start = 0
    for s in sections:
        sl = slice(start, start + s.n_spots)
        diam = s.scalefactors.get("spot_diameter_fullres")
        (out / "figures" / f"{s.section_id}_clusters.svg").write_text(
            svg_spots(s.xy, title=f"{s.section_id}: expression clusters (not spatial domains)",
                      labels=labels[sl], spot_diameter_px=diam), encoding="utf-8")
        for gname, gid in top:
            label = f"{gname}_{gid}" if gname in dup else gname
            safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in label)
            col = hv_mat.take_cols([gene_col[gid]]).take_rows(np.arange(sl.start, sl.stop))
            (out / "figures" / f"{s.section_id}_{safe}.svg").write_text(
                svg_spots(s.xy, title=f"{s.section_id}: {label} (log-normalised)",
                          values=col.to_dense(np.float64)[:, 0], spot_diameter_px=diam),
                encoding="utf-8")
        start += s.n_spots

    # processed object -------------------------------------------------------------------------
    conn = CSR.from_coo(graph.src, graph.dst, np.ones(len(graph.src)), (graph.n, graph.n))
    obs = {"section": sec_of, "sample_id": np.concatenate([[s.sample_id] * s.n_spots
                                                           for s in sections]),
           "subject_id": np.concatenate([[s.subject_id] * s.n_spots for s in sections]),
           "condition": np.concatenate([[s.condition or "NA"] * s.n_spots for s in sections]),
           "cluster": labels.astype(str),
           "total_counts": np.concatenate([qc_metrics[s.section_id]["total_counts"]
                                           for s in sections]),
           "n_genes": np.concatenate([qc_metrics[s.section_id]["n_genes"] for s in sections]),
           "pct_mt": np.concatenate([qc_metrics[s.section_id]["pct_mt"] for s in sections])}
    hv = np.zeros(len(genes), bool)
    hv[hvg] = True
    try:
        import h5py  # noqa: F401
        processed = out / "processed.h5ad"
        write_h5ad(processed, X=lognorm, counts=counts,
                   obs_names=[f"{a}:{b}" for a, b in zip(sec_of, bcs)],
                   var_names=list(gene_ids), obs=obs,
                   var={"gene_name": np.asarray(genes, dtype=object), "highly_variable": hv},
                   obsm={"spatial": xy, "X_pca": pcs}, connectivities=conn,
                   scalefactors={s.section_id: s.scalefactors for s in sections},
                   uns_strings={"bioagent_pipeline": f"bioagent.spatial {PIPELINE_VERSION}"})
    except ImportError:
        processed = out / "processed.npz"
        np.savez_compressed(processed, X_data=lognorm.data, X_indices=lognorm.indices,
                            X_indptr=lognorm.indptr, shape=np.array(lognorm.shape), xy=xy,
                            pcs=pcs, labels=labels, genes=gene_ids, spots=bcs)

    qc_report["input_checks"] = input_checks
    qc_report["graph"] = graph.summary()
    qc_report["expression"] = {"hvg": int(len(hvg)), "pcs": int(pcs.shape[1]),
                               "pc_variance_ratio": [float(v) for v in var_ratio[:10]],
                               "k": int(k), "clusters_under_10_spots": small,
                               "silhouette_by_k": {int(a): float(b)
                                                                for a, b in sil.items()},
                               "cluster_sizes": {int(c): int(n) for c, n in
                                                 zip(*np.unique(labels, return_counts=True))}}
    (out / "qc.json").write_text(json.dumps(qc_report, indent=2, default=float),
                                 encoding="utf-8")
    (out / "limitations.md").write_text(_limitations(cfg, sections, k), encoding="utf-8")

    outputs = {p.relative_to(out).as_posix(): sha256_file(p) for p in sorted(out.rglob("*"))
               if p.is_file() and p.name not in ("provenance.json", "summary.json")}
    import os
    versions = {"python": sys.version.split()[0], "numpy": np.__version__,
                "platform": platform.platform(), "pipeline": PIPELINE_VERSION,
                # BLAS threading changes float results in the last bits (PCA scores in
                # processed.h5ad); tables are rounded at write time and do not change
                "threads": {v: os.environ.get(v, "") for v in
                            ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS")},
                "cpu_count": os.cpu_count()}
    try:
        import h5py
        versions["h5py"] = h5py.__version__
    except ImportError:
        pass
    cfg_dict = asdict(cfg)
    prov = {"created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "config": cfg_dict, "inputs": inputs, "outputs": outputs, "versions": versions,
            "seed": rng_seed, "elapsed_s": round(time.perf_counter() - t0, 2)}
    (out / "provenance.json").write_text(json.dumps(prov, indent=2, default=str),
                                         encoding="utf-8")
    sig = [r for r in stat_rows if np.isfinite(r["q_norm"]) and r["q_norm"] < 0.05]
    summary = {
        "status": "succeeded", "out_dir": str(out), "pipeline": PIPELINE_VERSION,
        "sections": [{"section_id": s.section_id, "subject_id": s.subject_id,
                      "platform": s.platform, "spots": s.n_spots} for s in sections],
        "spots": int(counts.shape[0]), "genes_kept": int(counts.shape[1]),
        "hvg_tested": int(len(hvg)), "clusters": int(k),
        "graph": graph.summary(),
        "spatially_variable_genes_q05": len(sig),
        "top_spatially_variable": [{"gene": r["gene"], "I": r["I"]} for r in stat_rows[:10]],
        "outputs": sorted(outputs),
        "claim_scope": "within-section description; no between-subject or treatment claim",
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2, default=float),
                                      encoding="utf-8")
    return summary
