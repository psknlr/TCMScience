# Spatial transcriptomics on processed data

> **概要（中文）**
>
> **新增内容：** `bioagent.spatial`，一个可直接运行的空间转录组分析模块，只处理上游已完成处理的数据。
>
> - **读取输入：** 读取 Visium 常规输出、Visium HD 单一分箱和带组织坐标的 `.h5ad`。
> - **读取时核对：**
>   - 条码与坐标对应；
>   - 比例因子与物理点间距一致；
>   - 坐标落在组织图像内；
>   - Visium 阵列奇偶性。
>   缺少坐标、比例异常或只有 UMAP 时直接报错。
> - **分析流程（固定可复现）：** 质控 → 标准化、高变基因、PCA → 表达聚类（明确标注"不是空间区域"） → 按切片构建空间邻接图（切片之间从不连边） → Moran's I 空间变异基因和邻域富集 → 结果表、SVG 组织图、溯源和局限说明。
> - **执行方式：** 分析在独立解释器中运行，带超时和内存限制；TCMScience 只接收结果路径、统计摘要和溯源信息，不接收表达矩阵。
>
> **公开数据验收：** 10x 人淋巴结 Visium，即 Cell2location 教程所用数据，各项检查都通过。
>
> - **与 Squidpy 1.8.2 逐项对照：**
>   - 读取：坐标和计数完全一致；
>   - 邻接图：23,466 条边完全相同；
>   - Moran's I：最大差异 5×10⁻⁷；
>   - 邻域富集 z 值：相关系数 0.9997，方向全部一致。
> - **阴性对照：** 打乱点位后，空间变异基因从 908 个降到 1 个。
> - **切片隔离：** 同一切片以两个编号载入时，两份之间没有任何边，边数恰好加倍。
> - **可复现性：** 两次运行的 11 个输出文件逐字节相同。
>
> **仍未实现，保持"不可用"：** 细胞类型反卷积（Cell2location/Tangram）、单细胞映射、细胞通信推断、多切片配准与三维重建、空间区域识别、原始数据处理。

The repository audited at `dd16265` had spatial entries only as catalogue metadata. Each
entry carried `backend = none`, `lifecycle_state = UNAVAILABLE` and
`executable_now = False`, and there was no spatial dependency or data reader. This change
adds a minimal, validated workflow for **processed** data. The upstream catalogue entries
it does not implement stay unavailable; see
[`data/spatial_capability_status.csv`](../BioScience-Harness/data/spatial_capability_status.csv).

## What runs

```
input check → read (positions, scale factors, image bounds) → spot QC → normalise,
variable genes, PCA, expression clusters, markers → neighbour graph per section →
Moran's I, neighbourhood enrichment → tables, SVG figures, provenance, limitations
```

```bash
pip install -e "BioScience-Harness[spatial]"     # h5py: 10x HDF5, .h5ad in and out (optional)
python -m bioagent.spatial --config run.yaml --out results/
```

```yaml
# run.yaml
sections:
  - {path: /data/LN1/outs, section_id: LN1, sample_id: S1, subject_id: P1, condition: UC}
qc: {min_counts: 200, min_genes: 100, max_pct_mt: 30}
expression: {n_hvg: 2000, n_pcs: 30}      # k chosen by silhouette in 4–12 unless given
graph_mode: grid                          # Visium hex grid; knn or radius for .h5ad
seed: 0
```

From the harness, the analysis runs in a separate interpreter (it may be a different
virtual environment) with limits, and returns a summary and paths, never the matrix:

```python
from bioagent.spatial.runner import AnalysisEnvironment, run_isolated
r = run_isolated(config, "results/", AnalysisEnvironment(python="/envs/spatial/bin/python",
                                                         timeout_s=3600, memory_mb=16000))
r["status"]   # succeeded | input_error | timeout | resource_limit | unavailable | failed
```

| Module | Does | Refuses |
| --- | --- | --- |
| `spatial.io` | Reads Visium (Space Ranger 1.x `tissue_positions_list.csv`, 2.x `tissue_positions.csv`, 3.x `.parquet`), the HDF5 matrix (`h5py`) or the Matrix Market folder (numpy only), and `scalefactors_json.json`. Reads one Visium HD bin directory as a square grid. Reads `.h5ad` with `obsm['spatial']` and raw counts | Expression barcodes without a position. Spot spacing that disagrees with the scale factor (spots sit 100 µm apart and `spot_diameter_fullres` spans a 65 µm reference spot, a ratio of 1.54 ± 15%; the 10x section measures 1.531). Coordinates outside the image under the recorded `tissue_hires_scalef`. Broken hexagonal parity. A Visium HD `outs` folder without a chosen bin. A folder with no `spatial/`. An `.h5ad` without tissue coordinates ("UMAP is not a tissue position"). Normalised `X` with no raw counts |
| `spatial.qc` | Per-spot counts, genes, mitochondrial %, recorded thresholds, gene filter | A section with fewer than 10 spots after QC; sections whose gene lists differ |
| `spatial.expression` | Library-size normalisation and log1p, Seurat-flavour variable genes, PCA, k-means (k-means++ starts, size-ordered labels), markers with effect sizes | p values for markers (clusters are chosen from the same spots, and spots are not independent) |
| `spatial.graph` | Visium hexagonal grid (6 neighbours), Visium HD square grid (4 or 8), kNN or radius in pixels | Any edge between sections. Unregistered sections are separate tissue; `assert_section_isolated` checks this on every graph |
| `spatial.statistics` | Moran's I: section-centred, row-standardised weights, normality-assumption variance, upper-tail p and BH q, optional within-section permutations. Neighbourhood enrichment: labels permuted within each section | |
| `spatial.pipeline` | The output contract below, deterministic for a given input and seed | Unknown configuration keys; repeated section ids |
| `spatial.runner` | Separate interpreter, environment check, wall-clock timeout, address-space and CPU limits | |

### Output contract

```
processed.h5ad            AnnData layout written with h5py:
                          X (log-normalised), layers['counts'], obsm['spatial'],
                          obsm['X_pca'], obsp['spatial_connectivities']
                          (processed.npz when h5py is absent)
qc.json                   per-section QC, input checks, graph and clustering summaries
clusters.tsv              spot → section, coordinates, expression cluster
markers.tsv               top genes per cluster (t, approximate log2FC, detection rates)
spatial_statistics.tsv    Moran's I per variable gene: I, z, p, BH q
neighborhood_results.tsv  cluster × cluster: observed and expected edges, z, p
figures/*.svg             clusters and top spatially variable genes on the tissue
provenance.json           input and output sha256, configuration, seed, versions
limitations.md            what this run supports and what it does not
summary.json              small summary for the harness
```

### Execution layer

- **`SubprocessBackend`** now takes:
  - `python=`: the interpreter of the analysis environment;
  - `memory_mb=`: an address-space limit;
  - `env=`: the child's environment.

  The 120 s default timeout is unchanged for quick calls; analysis components pass a
  longer `timeout_s`.
- **`ContainerBackend`** now takes:
  - `allowed_mount_roots=` at construction; with none configured, no mount is allowed;
  - `mounts=[{host, container, mode}]` per call: read-only unless `rw` (the output
    directory), symlinks resolved, sources strictly inside an allowed root, runtime
    sockets and `:`/`,` in paths refused;
  - `memory=` and `cpus=`.

  There is no container runtime on this machine, so the container path is unit-tested
  but has not run live.
- **Native tools** `spatial_neighbors`, `spatial_morans_i` and
  `spatial_neighborhood_enrichment` make the statistics callable as offline components.
  There are now 150 native tools in 13 domains.
- **Study skill:** `bio.spatial-transcriptomics` (`studies.skills`) is the study-skill
  contract. Its claim ceiling is exploratory, and it lists what it refuses. Between-patient
  comparisons go through `studies.spatial.compare_neighbourhoods`, with one value per
  patient.

## Acceptance on public data

The dataset is 10x Genomics *Human Lymph Node* (Visium, Space Ranger 1.1.0), the one used
in the Cell2location tutorial. The files are taken from `cf.10xgenomics.com` and kept
outside the repository. The 10x dataset page returned HTTP 429 when I tried to re-read its
licence, so the CC BY licence 10x states for its public datasets is assumed, not
re-verified today.

- **Script:** `scripts/accept_spatial_visium.py`.
- **Report:** [studies/spatial_acceptance.json](studies/spatial_acceptance.json).
- **Run setup:** every run went through `run_isolated` with a 12 GB memory limit.

| Acceptance item | Check | Result |
| --- | --- | --- |
| Data correspondence | HDF5 and Matrix Market inputs give identical tables | identical |
| | 5 barcodes removed from positions | refused: "5 of 4035 expression barcodes have no tissue position" |
| | Spot diameter doubled | refused: spacing is 0.77 diameters, expected 1.54 |
| | `tissue_hires_scalef` tripled | refused: 3,781 spots fall outside the image |
| | No `spatial/` folder | refused |
| Space is really used | Graph mode | hexagonal tissue grid; modal degree 6; 11,733 edges, 3 isolated spots |
| | Positions shuffled (negative control) | spatially variable genes at q < 0.05: 908 before, 1 after; max Moran's I: 0.72 before, 0.044 after |
| Sections stay apart | Same section loaded twice under two ids (identical pixel coordinates) | edges exactly double (23,466 vs 11,733); no edge between the copies; per-gene I differs by at most 1.5×10⁻⁵ from the single run |
| Reproducible | Two runs | 11 tables and figures byte-identical; about 40 s per run |
| Claim scope | Single section | "within-section description; no between-subject or treatment claim"; `limitations.md` says the run supports no patient, condition or treatment difference |

**Result on the tissue.**
- 4,032 of 4,035 spots and 19,813 genes pass QC.
- There are 8 expression clusters (the smallest has 57 spots).
- 908 of 2,000 variable genes are spatially autocorrelated at q < 0.05.
- The top genes follow lymph-node architecture:
  - IGHG1, IGHG2 and IGLC1: plasma cells;
  - CCL21: T-cell zone;
  - FDCSP, CR2 and CXCL13: follicular dendritic cells in B-cell follicles.
- FDCSP and CCL21 are anti-correlated across spots (r = −0.60).

Figures are in [studies/spatial_figures/](studies/spatial_figures/).

### Cross-check against Scanpy/Squidpy

`scripts/crosscheck_spatial_squidpy.py` reruns each step with the reference
implementations, on the same spots and the same matrix. It used anndata 0.12.19, scanpy
1.11.5 and squidpy 1.8.2, installed in a separate environment, not as dependencies.

| Step | Agreement |
| --- | --- |
| `processed.h5ad` | opens with `anndata.read_h5ad` |
| Reading vs `squidpy.read.visium` | coordinates and per-spot total counts identical (max difference 0) |
| Neighbour graph vs `sq.gr.spatial_neighbors(coord_type="grid", n_neighs=6)` | 23,466 directed edges, identical sets |
| Moran's I vs `sq.gr.spatial_autocorr` (row-standardised) | max difference 5.0×10⁻⁷ over 2,000 genes; top-100 overlap 100/100; p identical (5×10⁻⁷) where z > 0 |
| Neighbourhood enrichment z vs `sq.gr.nhood_enrichment` | r = 0.9997, sign agreement 100% over all 64 cluster pairs (both are permutation estimates) |

The two tools differ only in the p-value convention. Squidpy reports the tail in the
direction of z; this pipeline reports the upper tail, which tests positive spatial
autocorrelation, the question asked of spatially variable genes. The variance formula is
the same.

## Independent review

A second review of the code (reproductions on the real data and against Scanpy / Squidpy
/ scikit-learn) found no error in the reading, the sparse operations, the variable genes,
PCA (variance ratios equal to Scanpy's to 1e-6), the graphs or Moran's I (equal to a dense
recomputation to 1e-17, including isolated nodes), and nine smaller defects, all fixed with
regression tests:

1. The spacing check assumed a 55 µm spot; `spot_diameter_fullres` is defined on a 65 µm
   spot, so the accepted band was lopsided (the real section sat 4% from the refusal edge).
   It is now centred on 100/65 with ±15%.
2. kNN graphs linked a spot to itself when `k` ≥ the spots in a section.
3. The silhouette gave a one-spot cluster a score of 1 (scikit-learn gives 0), rewarding
   outlier clusters; the earlier lymph-node run had a 1-spot cluster. Singletons now score
   0 and `k` is chosen among clusterings whose smallest cluster has ≥ 10 spots.
4. Memory grew with spots × genes (a dense copy of the variable genes, PCA copies, kNN
   distance blocks): PCA is now streamed from the sparse matrix, Moran's I runs in gene
   blocks and edge chunks, and kNN blocks are bounded (~160 MB). Visium HD at 8 or 2 µm
   has still not been run on real data.
5. Container mounts were unrestricted: any host path, read-write, including `/` or the
   Docker socket, and `:`/`,` injected volume options. Mounts now need configured allowed
   roots, resolve symlinks, refuse sockets and option characters, and default to read-only.
6. `SubprocessBackend` kept `python -I`, which ignores the `PYTHONPATH` of a supplied
   `env=`; a memory-limit failure was indistinguishable from a crash.
7. Configuration errors and "no spot passes QC" came back as `failed` with a traceback;
   they are now `input_error` (missing optional readers: `unavailable`). The default
   graph mode is `auto`, so an `.h5ad` without an array grid uses kNN instead of failing.
8. BLAS thread counts change PCA scores in the last bits; the runner pins one thread and
   provenance records the thread settings (tables were already identical).
9. Duplicate gene symbols (10 in this dataset): markers now carry `gene_id` and figures
   are looked up by id; a constant feature's permutation p is NaN, not 0.01; k-means
   labels always belong to the final centroids.

## What it does not do

| Capability | Status | Why |
| --- | --- | --- |
| Cell-type deconvolution (Cell2location, Tangram), single-cell → spatial mapping | not implemented | Needs a model environment (GPU recommended) and a matched reference. Its output is a model estimate of abundance, recorded as `predicted`, never as an observed identity |
| Cell–cell communication (Spateo LR, …) | not implemented | Results would be labelled inferred; a ligand–receptor plot does not raise a claim to mechanism |
| Section registration, 3D reconstruction | not implemented | Until then, sections are never joined |
| Spatial domain detection, boundaries | not implemented | Expression clusters drawn on the tissue are not domains |
| Xenium, CosMx, MERFISH, Stereo-seq readers | not implemented | For targeted panels, `OmicsArtifact(feature_space="targeted_panel")` and `spatial.detectable_genes` keep the measured panel as the background |
| Raw sequencing or image processing (Space Ranger, segmentation) | out of scope | |
| Between-patient or treatment comparisons from one section | refused by design | Need one value per patient; `studies.spatial.compare_neighbourhoods`, paired or unpaired |
