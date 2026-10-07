"""The single-cell RNA-seq pipeline: count matrices to annotated clusters and a report.

    from bioagent.omics.scrna import ScConfig, run_scrna
    run = run_scrna("samples.csv", ScConfig(root="NK cell"), "results/")

The sample sheet lists ``sample,path`` and any sample variables (``condition``,
``batch``, ...); ``path`` is a Cell Ranger directory or HDF5 file, an ``.h5ad`` or a
CSV/TSV table, relative to the sheet. A single count matrix may be given instead of a
sheet.

Steps (Scanpy's and the single-cell best-practice defaults), each recorded:

1. **QC**: genes, counts, mitochondrial and ribosomal shares per cell; outliers by
   5 MADs per sample (3 for mitochondria), at least 200 genes per cell and 3 cells per
   gene (``sc.qc``).
2. **Doublets**: Scrublet's simulated-doublet score per sample; called doublets are
   removed, or kept and flagged (``doublets="flag"``) (``sc.doublets``).
3. **Normalisation**: 10,000 counts per cell and log1p; 2,000 highly variable genes by
   the Seurat method, batch-aware; scaling; PCA (``sc.preprocess``).
4. **Integration** when there is more than one batch (the ``batch`` column, else the
   samples): Harmony on the PCA embedding (``sc.harmony``), scVI, or none
   (``integration_method``).
5. **Graph and clusters**: 15 nearest neighbours, UMAP connectivities, Leiden at
   resolution 1 (``sc.graph``, ``sc.leiden``), and a UMAP layout (``sc.umap``).
6. **Markers**: Wilcoxon rank-sum per cluster (``sc.markers``).
7. **Annotation**: marker scores against a panel, unassigned when no type leads
   (``sc.annotate``).
8. **Trajectory**: PAGA between clusters always; diffusion pseudotime when a root
   cluster or cell type is named (``sc.trajectory``).
9. **Conditions**: pseudobulk DESeq2 per cell type when samples carry two conditions
   (``sc.pseudobulk``).
10. **Report**: tables, SVG figures, ``report.md``/``.html`` and ``run.json`` with every
    parameter and digest; ``verify_run`` re-checks them.

Steps 3 to 6 run on the built-in parts named above or on Scanpy (``analysis_backend``;
``sc.analysis``), and the pseudobulk test of step 9 on the built-in DESeq2
implementation or on PyDESeq2 (``de_backend``). These choices and
``integration_method`` are independent. Each is checked before any count is read; an
implementation that is not installed is refused, never replaced. ``run.json`` records
which ran, with its version, parameters and seeds.

What a result is: clusters are groups of transcriptionally similar cells in this data
set; a cell-type label is an inference from marker expression; pseudotime is an
ordering of cells by similarity from a chosen root, not elapsed time; a pseudobulk
difference is an association between conditions in these samples.
"""

from __future__ import annotations

import csv
import hashlib
import json
import platform
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from . import de_backends, svgplot
from .rnaseq import _html, _sha256
from .sc import analysis as sc_analysis
from .sc import annotate as sc_annotate
from .sc import doublets as sc_doublets
from .sc import graph as sc_graph
from .sc import io as sc_io
from .sc import markers as sc_markers
from .sc import pseudobulk as sc_pb
from .sc import qc as sc_qc
from .sc import trajectory as sc_traj

__all__ = ["ScConfig", "ScRun", "ScError", "read_sheet", "run_scrna", "verify_run"]


class ScError(ValueError):
    """A sample sheet or setting the single-cell pipeline cannot run with."""


@dataclass(frozen=True)
class ScConfig:
    min_genes: int = 200
    min_cells: int = 3
    nmads: float = 5.0
    doublets: str = "remove"                 # remove | flag | off
    expected_doublet_rate: float = 0.06
    n_top_genes: int = 2000
    n_pcs: int = 30
    batch_key: str = "auto"                  # auto | none | a sample-sheet column
    analysis_backend: str = "builtin"        # builtin | scanpy (steps 3 to 6)
    integration_method: str = "harmony"      # none | harmony | scvi
    scvi_epochs: int = 400                   # scVI's training epochs
    scvi_threads: int = 1                    # torch threads while scVI trains
    de_backend: str = "builtin"              # builtin | pydeseq2 (pseudobulk)
    n_neighbors: int = 15
    resolution: float = 1.0
    markers: str | None = None               # a marker panel file; None: the default
    annotate: bool = True
    root: str | None = None                  # root cluster id or cell type
    paga_threshold: float = 0.05             # PAGA edges that join a trajectory
    contrast: tuple[str, str, str] | None = None
    seed: int = 0

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def read_sheet(path: str | Path) -> list[dict[str, str]]:
    p = Path(path)
    text = p.read_text(encoding="utf-8-sig")
    first = text.splitlines()[0] if text.strip() else ""
    dialect = "excel-tab" if first.count("\t") > first.count(",") else "excel"
    rows = [{k.strip(): (v or "").strip() for k, v in r.items() if k}
            for r in csv.DictReader(text.splitlines(), dialect=dialect)]
    if not rows or "sample" not in rows[0] or "path" not in rows[0]:
        raise ScError(f"{p} needs 'sample' and 'path' columns")
    names = [r["sample"] for r in rows]
    if len(set(names)) != len(names):
        raise ScError(f"{p} names a sample twice")
    for r in rows:
        q = Path(r["path"])
        q = q if q.is_absolute() else p.parent / q
        if not q.exists():
            raise ScError(f"sample {r['sample']}: {q} does not exist")
        r["path"] = str(q)
    return rows


@dataclass
class ScRun:
    out_dir: Path
    n_cells: int
    n_genes: int
    clusters: np.ndarray
    cell_types: dict[str, str]
    annotation: sc_annotate.Annotation | None
    markers: sc_markers.MarkerResult
    umap: np.ndarray
    pseudotime: np.ndarray | None
    paga: sc_traj.Paga
    pseudobulk: sc_pb.PseudobulkResult | None
    qc: dict[str, Any]
    doublets: dict[str, Any]
    integration: dict[str, Any]
    warnings: list[str]
    manifest: dict[str, Any] = field(default_factory=dict)
    cells: np.ndarray = field(default_factory=lambda: np.zeros(0))
    analysis: dict[str, Any] = field(default_factory=dict)   # which backend ran, how

    def summary(self) -> dict[str, Any]:
        sizes = {str(c): int((self.clusters == c).sum()) for c in np.unique(self.clusters)}
        return {"cells": self.n_cells, "genes": self.n_genes, "clusters": sizes,
                "cell_types": self.cell_types, "doublets": self.doublets.get("called", 0),
                "analysis_backend": self.analysis.get("backend"),
                "integration": self.integration.get("method"),
                "integration_implementation": self.integration.get("implementation"),
                "pseudotime": self.pseudotime is not None,
                "pseudobulk": sorted(self.pseudobulk.results) if self.pseudobulk else [],
                "de_backend": ({"backend": self.pseudobulk.backend,
                                "version": self.pseudobulk.version}
                               if self.pseudobulk else None),
                "warnings": list(self.warnings), "out_dir": str(self.out_dir)}


def _code_digest() -> str:
    h = hashlib.sha256()
    base = Path(__file__).parent
    for p in sorted(list(base.glob("*.py")) + list((base / "sc").glob("*.py"))):
        h.update(p.name.encode() + b"\0" + p.read_bytes())
    return h.hexdigest()


def _check_choices(config: ScConfig) -> None:
    """Every implementation the run was asked for, checked before a count is read: one
    that is not installed stops the run here with the reason
    (``optional.BackendUnavailable``) and is never swapped for another."""
    for name, value, allowed in (
            ("analysis_backend", config.analysis_backend, sc_analysis.ANALYSIS_BACKENDS),
            ("integration_method", config.integration_method,
             sc_analysis.INTEGRATION_METHODS),
            ("de_backend", config.de_backend, de_backends.BACKENDS)):
        if value not in allowed:
            raise ScError(f"{name} is one of {', '.join(allowed)}, not {value!r}")
    if config.scvi_epochs < 1 or config.scvi_threads < 1:
        raise ScError("scvi_epochs and scvi_threads are at least 1")
    sc_analysis.check(config.analysis_backend, config.integration_method)
    de_backends.check_backend(config.de_backend)


def run_scrna(source: str | Path, config: ScConfig, out_dir: str | Path) -> ScRun:
    _check_choices(config)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    warnings: list[str] = []
    src = Path(source)
    if src.is_file() and src.suffix.lower() in (".csv", ".tsv") and _is_sheet(src):
        rows = read_sheet(src)
    else:
        rows = [{"sample": "sample1", "path": str(src)}]
    info = {r["sample"]: {k: v for k, v in r.items() if k not in ("sample", "path")}
            for r in rows}
    parts = [(r["sample"], sc_io.read_counts(r["path"])) for r in rows]
    matrix = sc_io.concatenate(parts, info)
    inputs = {}
    for r in rows:
        p = Path(r["path"])
        files = [p] if p.is_file() else sorted(q for q in p.rglob("*") if q.is_file())
        for f in files:
            inputs[str(f)] = _sha256(f)
    if config.markers:
        inputs[str(config.markers)] = _sha256(Path(config.markers))

    # 1. QC
    qres = sc_qc.filter_cells(matrix, sc_qc.QCSettings(min_genes=config.min_genes,
                                                       min_cells=config.min_cells,
                                                       nmads=config.nmads))
    m = qres.matrix
    if m.shape[0] < 50:
        raise ScError(f"only {m.shape[0]} cells pass QC; too few to analyse")
    qc = {"cells_in": int(matrix.shape[0]), "cells_out": int(m.shape[0]),
          "removed": qres.removed, "genes_removed": qres.genes_removed,
          "per_sample": qres.per_sample}

    # 2. doublets
    scores = np.zeros(m.shape[0])
    called = np.zeros(m.shape[0], dtype=bool)
    dbl_detail: dict[str, Any] = {"mode": config.doublets, "per_sample": {}}
    if config.doublets != "off":
        for k, s in enumerate(dict.fromkeys(m.obs["sample"])):
            idx = np.flatnonzero(m.obs["sample"] == s)
            d = sc_doublets.scrublet(m.counts[idx], expected_rate=config.expected_doublet_rate,
                                     seed=config.seed + k)
            scores[idx] = d.scores
            called[idx] = d.predicted
            dbl_detail["per_sample"][str(s)] = {"called": int(d.predicted.sum()),
                                                "cells": len(idx), **d.detail}
            if str(d.detail.get("threshold_method", "")).startswith("none"):
                warnings.append(f"{s}: no doublet threshold found; no doublet called")
        dbl_detail["called"] = int(called.sum())
        share = called.mean() if len(called) else 0.0
        if share > 2 * config.expected_doublet_rate:
            note = ("if the data hold such a continuum, rerun with doublets='flag'"
                    if config.doublets == "remove"
                    else "they were kept and flagged (doublets='flag')")
            warnings.append(f"{share:.0%} of cells were called doublets, over twice the "
                            f"expected {config.expected_doublet_rate:.0%}. Scrublet also scores "
                            "cells in a continuous transition between two types as doublets; "
                            + note)
        if config.doublets == "remove" and called.any():
            keep = ~called
            m = m.subset(cells=keep)
            scores, qmetrics = scores[keep], {k: v[keep] for k, v in qres.metrics.items()}
            called = called[keep]
        else:
            qmetrics = qres.metrics
    else:
        qmetrics = qres.metrics
        dbl_detail["called"] = 0

    # 3-6. normalisation, HVG, PCA, integration, graph, clusters, layout, markers
    batch_values, batch_name = _batches(m, config)
    an = sc_analysis.run(m, batch_values, batch_name, backend=config.analysis_backend,
                         integration=config.integration_method,
                         settings=sc_analysis.StageSettings(
                             n_top_genes=config.n_top_genes, n_pcs=config.n_pcs,
                             n_neighbors=config.n_neighbors, resolution=config.resolution,
                             seed=config.seed, scvi_epochs=config.scvi_epochs,
                             scvi_threads=config.scvi_threads), out_dir=out)
    integration = dict(an.integration)
    if integration.get("converged") is False:
        warnings.append(f"Harmony did not converge in {integration['rounds']} rounds")
    if integration.get("reached_iteration_limit"):
        warnings.append(f"harmonypy stopped at its limit of {integration['iterations']} "
                        "rounds; it may not have converged")
    if batch_values is not None:
        integration["mixing_before"] = _mixing(an.pcs, batch_values)
        integration["mixing_after"] = _mixing(an.embedding, batch_values)
    clusters = an.clusters
    conn = an.connectivities
    mk = an.markers

    # 7. annotation
    annotation = None
    cell_types = {str(c): f"cluster {c}" for c in np.unique(clusters)}
    if config.annotate:
        panel = (sc_annotate.load_panel(config.markers) if config.markers
                 else sc_annotate.default_panel())
        annotation = sc_annotate.annotate_clusters(an.logx, m.gene_names, clusters, panel,
                                                   seed=config.seed)
        cell_types = dict(annotation.labels)
        unassigned = [c for c, v in cell_types.items() if v == sc_annotate.UNASSIGNED]
        if unassigned:
            warnings.append(f"clusters {', '.join(unassigned)}: no cell type of the panel "
                            "leads; left unassigned")

    # 8. trajectory, on the graph the clusters were found on
    pg = sc_traj.paga(an.adjacency, clusters)
    pseudotime = None
    root_note = ""
    if config.root is not None:
        root_cluster = _root_cluster(config.root, cell_types, clusters)
        joined = sc_traj.connected_clusters(pg, root_cluster, config.paga_threshold)
        pseudotime, root, ordered = sc_traj.pseudotime_from(conn, clusters, root_cluster,
                                                            include=joined)
        root_note = (f"root cell {m.cells[root]} in cluster {root_cluster}, over clusters "
                     f"{', '.join(joined)} (joined by PAGA connectivity >= "
                     f"{config.paga_threshold:g}); {ordered:,} cells ordered")
        if ordered < len(clusters):
            warnings.append(f"{len(clusters) - ordered:,} cells lie outside the trajectory "
                            "from the root and have no pseudotime")

    # 9. conditions
    pb = None
    contrast = config.contrast or _infer_contrast(info)
    if contrast is not None:
        labels = np.array([cell_types[str(c)] for c in clusters], dtype=object)
        pb = sc_pb.pseudobulk_de(m.counts, list(m.gene_names), labels, m.obs["sample"], info,
                                 design=f"~ {contrast[0]}", contrast=contrast,
                                 de_backend=config.de_backend)
        if not pb.results:
            warnings.append("no cell type has enough samples per condition for pseudobulk "
                            "testing")

    run = ScRun(out_dir=out, n_cells=int(m.shape[0]), n_genes=int(m.shape[1]),
                clusters=clusters, cell_types=cell_types, annotation=annotation, markers=mk,
                umap=an.umap, pseudotime=pseudotime, paga=pg, pseudobulk=pb, qc=qc,
                doublets=dbl_detail, integration=integration, warnings=warnings,
                cells=m.cells, analysis=an.record)
    _write(run, m, config, qmetrics, scores, called, an, contrast, info, inputs, started,
           root_note)
    return run


def _is_sheet(path: Path) -> bool:
    first = path.read_text(encoding="utf-8-sig").split("\n", 1)[0].lower()
    cells = [c.strip() for c in first.replace("\t", ",").split(",")]
    return "sample" in cells and "path" in cells


def _batches(m: sc_io.CellMatrix, config: ScConfig) -> tuple[np.ndarray | None, str]:
    if config.batch_key == "none":
        return None, "none"
    key = config.batch_key
    if key == "auto":
        key = "batch" if "batch" in m.obs and len(set(m.obs["batch"]) - {""}) > 1 else "sample"
    if key not in m.obs:
        raise ScError(f"no sample column {key!r} to integrate over")
    values = m.obs[key]
    return (values if len(set(values)) > 1 else None), key


def _mixing(emb: np.ndarray, batches: np.ndarray, k: int = 15) -> float:
    """Share of each cell's neighbours from another batch, over the share expected if
    batches mixed perfectly (1 = perfect mixing, 0 = none)."""
    idx, _ = sc_graph.knn(emb, k + 1)
    b = np.asarray(batches)
    observed = float(np.mean(b[idx[:, 1:]] != b[:, None]))
    _, counts = np.unique(b, return_counts=True)
    p = counts / counts.sum()
    expected = float(1 - (p ** 2).sum())
    return round(observed / expected, 4) if expected else 0.0


def _root_cluster(root: str, cell_types: Mapping[str, str], clusters: np.ndarray) -> str:
    if str(root) in cell_types:
        return str(root)
    named = [c for c, t in cell_types.items() if t == root]
    if not named:
        raise ScError(f"root {root!r} is neither a cluster nor an assigned cell type "
                      f"({', '.join(sorted(set(cell_types.values())))})")
    return max(named, key=lambda c: int((clusters.astype(str) == c).sum()))


def _infer_contrast(info: Mapping[str, Mapping[str, str]]) -> tuple[str, str, str] | None:
    levels = sorted({v.get("condition", "") for v in info.values()} - {""})
    if len(levels) != 2:
        return None
    ref = next((lv for lv in levels if lv.lower() in ("control", "ctrl", "untreated",
                                                      "vehicle", "normal", "healthy")),
               levels[0])
    other = next(lv for lv in levels if lv != ref)
    return ("condition", other, ref)


# ============================================================== outputs

def _tsv(path: Path, header: Sequence[str], rows: Sequence[Sequence[Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        fh.write("\t".join(header) + "\n")
        for r in rows:
            fh.write("\t".join("NA" if v is None else (f"{v:.6g}" if isinstance(v, float)
                                                       else str(v)) for v in r) + "\n")


def _write(run: ScRun, m: sc_io.CellMatrix, config: ScConfig, qmetrics: dict[str, np.ndarray],
           scores: np.ndarray, called: np.ndarray, an: sc_analysis.Analysis,
           contrast: tuple[str, str, str] | None, info: Mapping[str, Any],
           inputs: dict[str, str], started: str, root_note: str) -> None:
    out = run.out_dir
    cl = run.clusters
    pt = run.pseudotime
    _tsv(out / "cells.tsv",
         ["cell", "sample", "cluster", "cell_type", "n_genes", "total_counts", "pct_mito",
          "doublet_score", "doublet_called", "umap_1", "umap_2", "pseudotime"],
         [[m.cells[i], m.obs["sample"][i], int(cl[i]), run.cell_types[str(cl[i])],
           float(qmetrics["n_genes"][i]), float(qmetrics["total_counts"][i]),
           float(qmetrics["pct_mito"][i]), float(scores[i]), bool(called[i]),
           float(run.umap[i, 0]), float(run.umap[i, 1]),
           (float(pt[i]) if pt is not None else None)] for i in range(len(cl))])
    rows = []
    for g in run.markers.groups:
        for r in run.markers.top(g, 50):
            rows.append([g, run.cell_types.get(g, ""), r["gene"], r["score"],
                         r["log2_fold_change"], r["p_adjusted"], r["pct_in"], r["pct_out"]])
    _tsv(out / "markers.tsv", ["cluster", "cell_type", "gene", "score", "log2_fold_change",
                               "p_adjusted", "pct_in", "pct_out"], rows)
    crow = []
    for g in run.markers.groups:
        conf = run.annotation.confidence.get(g, 0.0) if run.annotation else None
        top = ", ".join(r["gene"] for r in run.markers.top(g, 5))
        crow.append([g, int((cl.astype(str) == g).sum()), run.cell_types.get(g, ""),
                     conf, top])
    _tsv(out / "clusters.tsv", ["cluster", "cells", "cell_type", "confidence", "top_markers"],
         crow)
    _tsv(out / "paga.tsv", ["cluster_a", "cluster_b", "connectivity"],
         [[a, b, float(run.paga.connectivities[run.paga.groups.index(a),
                                                run.paga.groups.index(b)])]
          for a in run.paga.groups for b in run.paga.groups if a < b
          and run.paga.connectivities[run.paga.groups.index(a), run.paga.groups.index(b)] > 0])
    if run.pseudobulk:
        pdir = out / "pseudobulk"
        pdir.mkdir(exist_ok=True)
        contract = [attr for _, attr, _ in de_backends.CONTRACT]
        for ct, res in run.pseudobulk.results.items():
            safe = "".join(ch if ch.isalnum() else "_" for ch in ct)
            _tsv(pdir / f"{safe}.tsv", ["gene", *contract],
                 [[r["gene"], *(r[c] for c in contract)] for r in res.table()])
    plots = _plots(run, m, qmetrics, an.variance_ratio)
    pdir = out / "plots"
    pdir.mkdir(exist_ok=True)
    for name, svg in plots.items():
        (pdir / f"{name}.svg").write_text(svg, encoding="utf-8")
    md = _markdown(run, config, contrast, an, root_note)
    (out / "report.md").write_text(md, encoding="utf-8")
    (out / "report.html").write_text(_html(md, plots), encoding="utf-8")
    outputs = {str(p.relative_to(out)): _sha256(p) for p in sorted(out.rglob("*"))
               if p.is_file() and p.name != "run.json"}
    pseudobulk = None
    if run.pseudobulk is not None:
        pb = run.pseudobulk
        pseudobulk = {"backend": pb.backend, "version": pb.version,
                      "tests": {ct: res.record() for ct, res in pb.results.items()},
                      "skipped": pb.skipped}
    manifest = {"pipeline": "bioagent.omics.scrna", "code_digest": _code_digest(),
                "started": started,
                "finished": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "python": platform.python_version(), "numpy": np.__version__,
                "config": config.as_dict(), "samples": info, "inputs": inputs,
                "outputs": outputs, "summary": run.summary(), "qc": run.qc,
                "doublets": run.doublets,
                "analysis": {**run.analysis, "modularity": an.modularity},
                "integration": run.integration, "pseudobulk": pseudobulk,
                "warnings": run.warnings}
    run.manifest = manifest
    (out / "run.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2,
                                             default=str), encoding="utf-8")


def verify_run(out_dir: str | Path) -> tuple[bool, list[str]]:
    out = Path(out_dir)
    manifest = json.loads((out / "run.json").read_text(encoding="utf-8"))
    problems = []
    for path, digest in manifest["inputs"].items():
        p = Path(path)
        if not p.is_file():
            problems.append(f"input {path} is missing")
        elif _sha256(p) != digest:
            problems.append(f"input {path} has changed")
    for rel, digest in manifest["outputs"].items():
        p = out / rel
        if not p.is_file():
            problems.append(f"output {rel} is missing")
        elif _sha256(p) != digest:
            problems.append(f"output {rel} has changed")
    return not problems, problems


def _plots(run: ScRun, m: sc_io.CellMatrix, qmetrics: dict[str, np.ndarray],
           ratio: np.ndarray) -> dict[str, str]:
    plots: dict[str, str] = {}
    x, y = run.umap[:, 0], run.umap[:, 1]
    labels = [f"{c}: {run.cell_types[str(c)]}" for c in run.clusters]
    order = sorted(set(labels), key=lambda v: int(v.split(":")[0]))
    radius = 2.4 if len(x) < 3000 else 1.4
    plots["umap_clusters"] = svgplot.scatter(
        x, y, groups=labels, colours=dict(zip(order, svgplot.palette(len(order)))),
        legend_order=order, radius=radius, title="UMAP, coloured by cluster and cell type",
        xlabel="UMAP 1", ylabel="UMAP 2")
    plots["umap_samples"] = svgplot.scatter(x, y, groups=list(m.obs["sample"]), radius=radius,
                                            legend_order=sorted(set(m.obs["sample"])),
                                            title="UMAP, coloured by sample",
                                            xlabel="UMAP 1", ylabel="UMAP 2")
    if run.pseudotime is not None:
        plots["umap_pseudotime"] = svgplot.scatter(
            x, y, values=run.pseudotime, value_label="pseudotime", radius=radius,
            title="Diffusion pseudotime from the root", xlabel="UMAP 1", ylabel="UMAP 2")
    centres = {g: (float(np.median(x[run.clusters.astype(str) == g])),
                   float(np.median(y[run.clusters.astype(str) == g]))) for g in run.paga.groups}
    node_colours = dict(zip(order, svgplot.palette(len(order))))
    node_colours = {g: node_colours[next(o for o in order if o.split(":")[0] == g)]
                    for g in run.paga.groups}
    sizes = {g: float((run.clusters.astype(str) == g).sum()) for g in run.paga.groups}
    edges = [(a, b, float(run.paga.connectivities[run.paga.groups.index(a),
                                                   run.paga.groups.index(b)]))
             for a in run.paga.groups for b in run.paga.groups
             if a < b and run.paga.connectivities[run.paga.groups.index(a),
                                                   run.paga.groups.index(b)] > 0.01]
    plots["paga"] = svgplot.network(centres, edges, sizes=sizes, colours=node_colours,
                                    title="PAGA: connectivity between clusters (on UMAP)")
    plots["qc_genes"] = svgplot.histogram(qmetrics["n_genes"], bins=40,
                                          title="Genes detected per cell (after QC)",
                                          xlabel="genes")
    plots["qc_mito"] = svgplot.histogram(qmetrics["pct_mito"], bins=40,
                                         title="Mitochondrial share per cell (after QC)",
                                         xlabel="% of counts")
    plots["pca_variance"] = svgplot.bars([str(i + 1) for i in range(len(ratio))],
                                         list(ratio), title="Variance explained by each PC",
                                         ylabel="fraction")
    groups = run.markers.groups
    genes, seen = [], set()
    for g in groups:
        for r in run.markers.top(g, 3):
            if r["gene"] not in seen:
                seen.add(r["gene"])
                genes.append(r["gene"])
    if genes:
        col = [list(run.markers.genes).index(gname) for gname in genes]
        share = run.markers.pct_in[:, col]
        plots["markers"] = svgplot.heatmap(
            share, [f"{g}: {run.cell_types.get(g, '')}" for g in groups], genes,
            title="Share of cells expressing each cluster's top markers",
            scale_label="fraction")
    return plots


def _implementation(integration: Mapping[str, Any]) -> str:
    """The integration as run: method, and the implementation and version when named."""
    name = integration.get("implementation")
    if not name:
        return str(integration.get("method"))
    found = integration.get("version")
    return f"{integration.get('method')} ({name}{f' {found}' if found else ''})"


def _markdown(run: ScRun, config: ScConfig, contrast: tuple[str, str, str] | None,
              an: sc_analysis.Analysis, root_note: str) -> str:
    s = run.summary()
    scanpy = an.record["backend"] == "scanpy"
    stages = (f"Scanpy {an.record['versions'].get('scanpy', '')}" if scanpy
              else "the built-in parts")
    pb = run.pseudobulk
    tester = ("" if pb is None else "the built-in DESeq2 implementation"
              if pb.backend == "builtin" else f"PyDESeq2 {pb.version}")
    integ = run.integration
    lines = ["# Single-cell RNA-seq: clusters, cell types and trajectories", ""]
    lines += ["## Result", "",
              f"- {run.qc['cells_in']:,} cells read; {run.qc['cells_out']:,} pass QC; "
              f"{run.doublets.get('called', 0)} called doublets "
              f"({'removed' if config.doublets == 'remove' else config.doublets}); "
              f"{s['cells']:,} cells and {s['genes']:,} genes analysed.",
              f"- {len(s['clusters'])} Leiden clusters at resolution {config.resolution:g} "
              f"(modularity {an.modularity:.3f}).",
              f"- Integration: {_implementation(integ)} over `{integ.get('batch_key')}`"
              + (f" ({integ['requested']} requested; {integ['reason']})"
                 if "requested" in integ else "")
              + (f"; batch mixing {integ['mixing_before']:.2f} → "
                 f"{integ['mixing_after']:.2f} (1 = fully mixed)"
                 if "mixing_after" in integ else "") + ".",
              f"- Normalisation to markers: **{stages}**"
              + (f"; pseudobulk test: **{tester}**." if tester else "."), ""]
    if run.warnings:
        lines += ["## Warnings", ""] + [f"- {w}" for w in run.warnings] + [""]
    lines += ["## Clusters", "", "| cluster | cells | cell type | confidence | top markers |",
              "|---|---|---|---|---|"]
    for g in run.markers.groups:
        conf = run.annotation.confidence.get(g) if run.annotation else None
        top = ", ".join(r["gene"] for r in run.markers.top(g, 5))
        lines.append(f"| {g} | {s['clusters'].get(g, 0)} | {run.cell_types.get(g, '')} | "
                     f"{'NA' if conf is None else f'{conf:.0%}'} | {top} |")
    lines += ["", "Confidence is the share of the cluster's cells whose own best-scoring type "
              "is the cluster's. `markers.tsv` holds 50 markers per cluster, `cells.tsv` every "
              "cell, `paga.tsv` the cluster connectivities.", ""]
    lines += ["## Trajectory", ""]
    lines.append("PAGA tree (minimum spanning tree of the inverse connectivities): "
                 + "; ".join(f"{a}–{b} ({w:.2f})" for a, b, w in run.paga.tree) + ".")
    lines.append("")
    if run.pseudotime is not None:
        lines.append(f"Diffusion pseudotime from {root_note}: an ordering by similarity from "
                     "the root, not elapsed time.")
    else:
        lines.append("No pseudotime: name a root cluster or cell type (`--root`) to order "
                     "cells; the data cannot say which cells come first.")
    lines.append("")
    if run.pseudobulk is not None and contrast is not None:
        lines += [f"## {contrast[1]} vs {contrast[2]}, per cell type (pseudobulk)", "",
                  "| cell type | samples | genes at FDR < 0.05 | up | down |",
                  "|---|---|---|---|---|"]
        for ct, res in sorted(run.pseudobulk.results.items()):
            sm = res.summary()
            lines.append(f"| {ct} | {len(res.samples)} | {sm['significant']} | {sm['up']} | "
                         f"{sm['down']} |")
        for ct, why in sorted(run.pseudobulk.skipped.items()):
            lines.append(f"| {ct} | not tested: {why} | | | |")
        lines.append("")
    lines += ["## Figures", ""]
    for name in ("umap_clusters", "umap_samples", "umap_pseudotime", "paga", "markers",
                 "qc_genes", "qc_mito", "pca_variance"):
        lines.append(f"![{name}](plots/{name}.svg)")
    lines += ["", "## Methods", "",
              f"- QC: ≥ {config.min_genes} genes per cell, ≥ {config.min_cells} cells per gene, "
              f"outliers beyond {config.nmads:g} MADs per sample (3 for mitochondria).",
              f"- Doublets: Scrublet's method, expected rate {config.expected_doublet_rate:g}; "
              "transitional cells can resemble doublets.",
              f"- Steps by {stages}: normalisation to 10,000 counts and log1p; "
              f"{int(an.highly_variable.sum())} highly variable genes (Seurat method); "
              f"{an.pcs.shape[1]} PCs; integration {_implementation(integ)}; kNN k = "
              f"{config.n_neighbors}; Leiden (resolution {config.resolution:g}); UMAP "
              + ("layout (umap-learn); " if scanpy else "layout (batched SGD); ")
              + "Wilcoxon rank-sum markers with tie correction, BH per cluster. Every "
              "stage's parameters and seed are in `run.json`.",
              "- Annotation: score_genes against the marker panel; unassigned without a "
              "clear lead.", "- Trajectory: PAGA; diffusion pseudotime from a named root.",
              "- Conditions: pseudobulk sums per sample and cell type, DESeq2 method"
              + (f" by {tester}." if tester else "."), "",
              "## What these results are", "",
              "Clusters are groups of transcriptionally similar cells in this data set. A "
              "cell-type label is an inference from marker expression against a panel, not a "
              "measurement. Pseudotime orders cells by similarity from a chosen root and is "
              "not time. A pseudobulk difference is an association between conditions in "
              "these samples. None of it establishes a mechanism or a clinical effect.", "",
              "## Reproducing it", "",
              "`run.json` records every input and output digest and the configuration; "
              "`bioagent scrna --verify <out>` re-checks the digests.", ""]
    return "\n".join(lines)
