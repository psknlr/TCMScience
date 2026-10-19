# Browser science jobs

How TCMScience Studio (<https://science.impf.ai>) runs research jobs inside a visitor's browser, with no local runner
and no compute server: the network-pharmacology research loop, and (the guidebook's P2) RNA-seq differential
expression from a count matrix and single-cell analysis from count tables. What runs where, how the data gets there,
how a result is shown to be the same result the runner gives, and what is not done. Written on 2026-10-19 on branch
`claude/epic-dirac-c1uqll`. Status words are those of [BROWSER_COMPUTE.md](BROWSER_COMPUTE.md) (implemented and
tested, saying where; implemented, partly verified; proposed; verified in deployment, which nothing here is), and
"not run" for what no run has exercised.

**In one paragraph.** The research loop's Python — `bioagent` and `psh`, the same files the runner imports — runs in
*job workers*: Web Workers holding Pyodide 314.0.7 (CPython 3.14 compiled to WebAssembly) and the SHA-256-verified app
bundle, in a pool sized from the device (one per CPU core the page can spare, at most one per 1.5 GB of device memory,
at most four). The job service runs one job at a time in a tab, in one worker, so today one worker computes at a time. The 17 raw files (418 MB: NPASS, CMAUP, LOTUS, STRING, Reactome, UniProt)
are kept in the site's Origin Private File System (OPFS) and read in place by Python through synchronous access
handles; the snapshots, the ledger, the PSH audit chain and the nine exports are written there too. Jobs are records in
IndexedDB, followed, cancelled and resumed like runner jobs, and each writes a receipt that says where it ran and what
was checked. On the planted world (the research jobs' known answer) a job worker in Chromium writes the five tables
**byte for byte** as native CPython does (`studio/e2e/science.spec.mjs`, passed in Studio's CI with Pyodide's own
pyarrow). The real 葛根芩连汤 case, run the same way in the live-data workflow and compared export by export with the
native run on the same files, passed in `live-data` #18 and #19 (runs 39251758718 and 39258060948, commits 43918ac and 53bdbb0, 2026-10-19, headless Chromium on GitHub's runner, Pyodide 314.0.7 with its pyarrow 22.0.0): the 17 raw files imported into the site's private storage, `research.snapshot` and `research.run` succeeded as browser jobs, and every export was the native run's bytes on the same files (the five tables, `provenance.json`, `limitations.md`, `report.html`; `run_manifest.json` apart from its execution block). That run: 4 herbs, 1,792 constituents, 370 measured human targets, a 237-node, 624-edge STRING network, 959 Reactome pathways tested, none significant after BH (so no permutation null was drawn there; the planted world exercises it). The RNA-seq pipeline in its count-matrix mode (`pipeline.rnaseq` with `counts`) and the single-cell pipeline on count tables (`pipeline.scrna`) run the same way, with Pyodide's numpy and scipy, on files from the person's project ([§3.1](#31-rna-seq-and-single-cell-from-count-tables)); a FASTQ run, PyDESeq2, Scanpy, scVI, 10x HDF5 and `.h5ad` inputs, and inputs above 6,000 cells or 100,000 genes × 200 samples are refused with the reason and left to the runner. On their known-answer references (`omics_counts_world` v1 and `sc_world` v1) every output was compared with the native command's on the same files (`science.mjs --mode omics`, headless Chromium 141, locally): identical bytes, or numbers within a stated tolerance (the largest difference 0.133 of it), or, for the UMAP layout and its figures, not compared and said so; the genes called and each cell's cluster and cell type are identical, and the known answers hold on the browser's files. The same check passed in Studio's CI, with Pyodide's numpy and scipy from jsDelivr and the native side on CI's own Python 3.12.15 (`studio` #91 (run 39286931542, commit 65fa6aa, 2026-10-19)).

## 1. What runs where

| Part | Where | Code |
|---|---|---|
| Planning a call, approvals | the page | `js/core/router.js`: a job placed in the browser goes to the job service, never to the tool worker |
| Job records, queue, cancel, resume, receipts | the page | `js/scientific/runtime/job-manager.js` (IndexedDB `tcmstudio-jobs`) |
| The computation (the research loop; RNA-seq and single-cell from count tables) | job workers (Pyodide, CPU, WebAssembly) | `js/scientific/engines/science.worker.js` → `tcmstudio.browser_jobs` → `bioagent` |
| The omics jobs' inputs: the project's files, copied into the job's directory and checked against their recorded SHA-256 | the page | `js/scientific/pipelines/omics.js`, through `js/scientific/runtime/jobs-runtime.js` (`projectFilesResolver` over the project file store, `js/core/store.js`) |
| Downloads, imports, hashing | a data worker (no Pyodide) | `js/scientific/data/data.worker.js` |
| Files | OPFS, under `tcmstudio/` | `js/scientific/data/opfs-store.js` |
| Relay for hosts that refuse a cross-origin download | the edge Worker, streaming, keeping nothing | `studio/edge/src/datasets.js` (`/api/datasets/files/<group>/<name>`) |
| Language model | Tao-S1 through `/v1` (unchanged) | not a computation of the job |

Job kinds the browser runs: `research.snapshot`, `research.run`, `pipeline.rnaseq` (its count-matrix mode only) and
`pipeline.scrna` (count tables, built-in engines only) (catalog `BROWSER_JOB_KINDS`; each carries its own
`execution.browser_job`: the Pyodide packages it loads, and for the two pipelines what they read, their engines and
`runner_only`, the arguments that take a call to the runner, each with its reason). A connected runner still takes
them under compute **Auto** (one place per project); under **Browser** they run here. A pipeline call
(`run_pipeline {pipeline, arguments}` or `call_tool job.pipeline.rnaseq|scrna`) whose arguments only the runner runs
goes to a connected runner, and without one is refused `needs_runner` with the reason, before anything is queued.
Every other job kind (`pipeline.fold`, `pipeline.dock`, `pipeline.admet`, `skill.run`, `tcmdb.*`, `env.provision`)
stays on the runner, and a call for one in the browser says so.

### 1.1 Job workers

`ScienceWorkerPool` sizes itself from the device: `min(cores − 1, ⌊memory GB / 1.5⌋)`, between 1 and 4 (memory unknown
is taken as 2 GB, and the reason is recorded). A worker boots lazily the first time a job leases it (Pyodide from the
pinned index, the bundle from Cache Storage when its hash matches, the compiled modules put back), loads the Pyodide
packages a stage needs (`pyarrow` for the snapshot store; `numpy` and `scipy` for the omics jobs, 2,960,568 + 14,029,750
bytes, about 16.2 MiB, fetched the first time a worker loads them; all from the same Pyodide distribution), and runs
one operation at a time. Each package asked for is checked in `pyodide.loadedPackages` after loading: Pyodide 314
reports a wheel it could not fetch without throwing, and such a package is refused (`unavailable`), never recorded as
loaded. scipy's Matrix Market reader is held to one thread however scipy was loaded (WebAssembly here has no threads).
The tool runtime's worker is a separate worker: a long job never blocks a tool call. A wasm32 memory only grows, so a
worker whose job was refused for memory is terminated rather than handed to the next job.

Cancelling sets the worker's interrupt buffer (a `SharedArrayBuffer`; the site is cross-origin isolated), which raises
`KeyboardInterrupt` in Python at the next bytecode boundary; a worker that does not stop within the grace period is
terminated and replaced.

### 1.2 Files in place

`mount` creates read-only Emscripten file nodes whose reads go to `FileSystemSyncAccessHandle.read({at})`: a 90 MB
NPASS file costs no memory until Python reads a block of it, and `gzip`, `csv` and `hashlib` read it unchanged. (A
browser without synchronous access handles gets the files copied into memory, up to 768 MB in all, or a refusal that
says why.) What a stage writes stays in the worker's memory file system until `persist` copies it to OPFS, returning
each file's SHA-256 as the worker read it.

### 1.3 Governance in a worker

A Web Worker cannot start a process, so the governed analysis cannot run in PSH's isolated child there. It runs in the
worker's own process through the **same PSH broker and audit chain** (`run_research(isolate=False)`): the program is
compiled under the kernel's policy, the tool call is brokered and audited, and reads are limited to the snapshot root
and the ledger. The artifact records this: `provenance.execution.<stage>.isolation` is `"in_process"` (on the runner,
`"process"`), and the run manifest's `execution.runtime` records `emscripten`/`wasm32`. Nothing claims an isolation
that did not happen. The analysis itself is the same code either way, which is why the tables are the same bytes.

## 2. The data

`runtime/datasets.json` (`tcmstudio.datasets/1`, built from `bioagent.sources.pipeline.required_files` and the licence
records) lists the 17 files: URL, host, reviewed size and checksum where one exists (LOTUS is checked by md5; three
files have neither a reviewed checksum nor a reviewed size and are kept as UNVERIFIED, as on the runner), licence, what
is known about redistribution — from a reviewed licence record, or "unknown — downloading is not permission to
redistribute" — and the pace bioagent keeps with its host (`rate_rps`: zenodo.org 0.1 a second, its robots.txt
Crawl-delay; reactome.org 5; rest.uniprot.org 10; otherwise 1), which the relay keeps too.

Before a snapshot build starts, the browser's free storage is checked against the reviewed sizes of the files still
missing (×1.1); the three files without a reviewed size are not guessed at, and the job's log names them.

A file reaches OPFS by one of three routes, recorded per file in `.meta/<file>.json`:

- **download** from its host, when the host lets a web page download from it (CORS);
- **relay** through the site's edge Worker, for a host that does not: a GET or HEAD for exactly the registry's URL,
  `Range` and `If-Range` passed through, the body streamed back without buffering, nothing stored, rate-limited like the
  source gateway, redirects only to the registry's reviewed hosts;
- **import** of a file the person obtained elsewhere (Settings → Data → *Import files…*).

`auto` tries the host, then the relay, and records both attempts. A download resumes onto its partial file when the
server proves it is the same file (`If-Range` with a strong ETag or Last-Modified, and a `Content-Range` that starts
where the part ends with the same total). Without a readable validator, a file with a reviewed checksum is resumed too
and the checksum checks the joined file: on a mismatch nothing is kept and the download starts again from nothing. Any
other answer restarts from byte 0 and says so, and so does a range the relay refuses (`bad_range`, `size_mismatch`).
Every file is checked again by the snapshot build's own integrity stage — the runner's code — against its reviewed
checksum or size.

## 3. The jobs

**`research.snapshot`**: plan → acquire → build → persist. `build` runs `bioagent.sources.pipeline.
build_research_snapshots(offline=True)` on the mounted files: integrity, parse and gate each source, the gold standard
(the marker compounds of 葛根芩连汤 found in their source species), the ledger. `persist` replaces the browser's snapshot
store and writes `datasets/research/snapshots.json` last; `research.run` refuses snapshots without it. The snapshot
files and the ledger are the job's outputs: the page reads each back from OPFS when the job ends, as it does the report.

**`research.run`**: prepare → research → verify. Its parameters are held to the runner's bounds (100 to 100 000
permutations, source keys, an ontology id for the disease, one formula). The sixteen-stage workflow (`bioagent research`) on the snapshots
restored into the worker, its nine exports and artifact; then `np_exports verify` recomputes every digest, each p and q
from the tables, and re-runs the analysis on the snapshots to the same tables, and the outputs are held to what the
runner's `research.run` job requires of them (`tcmstudio.kinds`).

**Checkpoints and recovery.** A job runs only while a page of the site is open: closing it stops the work (nothing
continues in the background), and the next visit shows the job as **interrupted**, resumable. A resumed job reuses a
stage whose recorded outputs still re-hash to their recorded digests in OPFS, and runs the rest again.

**Receipt.** `out/job_receipt.json` (`tcmstudio.job-receipt/1`): the runner receipt's fields where they mean the same
thing, plus `placement: "browser"`, the runtime and platform, the workers each stage ran in with their busy time, the
WebAssembly memory of the job's workers (`resources.memory.wasm_peak_bytes`: what each worker's memory had grown to when
the job gave it back — a wasm32 memory only grows, so this is the peak since that worker started and can include
earlier jobs on it; null when no worker reported one), and two independent readings of every output's digest: the job
worker's as it wrote the file, and the page's as it read it back from OPFS. `assurance: "outputs_verified"` only when
the two agree and nothing required is missing; the device is `cpu` (WebAssembly), at evidence `verified` only when a
verify stage ran in this attempt and said it passed (a snapshot build has none: `validated`). An omics job's receipt
also lists its `inputs`: each file's SHA-256 as the job read it, its size and the project file it came from (a sample
sheet's tables under `source.files`, as the runner's receipt keeps them).

### 3.1 RNA-seq and single-cell from count tables

Two of the runner's pipeline kinds run in part as browser jobs. The Python is bioagent's own (`bioagent rnaseq
--counts`, `bioagent scrna`), called through `tcmstudio.browser_jobs.run_rnaseq_counts` and `run_scrna`; the built-in
paths import nothing but numpy, scipy (Pyodide's) and the standard library.

| Kind | What runs in the browser | Refused here, with the reason (the runner runs it) |
|---|---|---|
| `pipeline.rnaseq` | the count-matrix mode: `counts`, a gene × sample table of integers (TSV or CSV, gzipped or not: `gene_id`, an optional `gene_name`, one column per sample), and `samples`, a sample table (a `sample` column and the design variables); the built-in DESeq2 implementation, VST, PCA, sample distances, the figures, `report.md`/`report.html`, `limitations.md`, `run.json` | a run from FASTQ (no `counts`: QC, trimming, quantification), a FASTQ sample sheet given as `samples`, `de_backend: pydeseq2` (PyDESeq2 is not in Pyodide) |
| `pipeline.scrna` | `source`, a count table (CSV, TSV or TXT, gzipped or not; genes in rows, cells in columns) or a sample sheet (`.csv`/`.tsv` with `sample`, `path` and the sample variables) of such tables, and an optional marker table; the built-in analysis (QC, doublets, highly variable genes up to `n_top_genes`, PCA, built-in Harmony or none, neighbours, Leiden, UMAP, markers, annotation, PAGA and pseudotime, pseudobulk with the built-in DESeq2 implementation) | `analysis_backend: scanpy` and `integration: scvi` (not in Pyodide), an `.h5ad` (anndata is not in Pyodide), a 10x HDF5 `.h5` (not yet supported in the browser: Pyodide has h5py, but reading through the job's file mount is untested), a Cell Ranger directory |

Both are also refused, before the analysis reads anything in full, when the input is larger than a job worker can
hold: more than **6,000 cells** (counted from the tables' headers), or more than **100,000 genes or 200 samples**
(counted from the count table's lines). The refusal is typed `refused` with need `memory` and says "… exceed the …
a browser worker can hold (wasm32, 4 GB): run it on the runner". A `MemoryError` anywhere in a run, and a worker's
own allocation failure, is the same refusal, never a runtime error and never a silently changed method; the worker is
then retired (its memory never shrinks). Arguments the runner would refuse are refused here as `bad_arguments`
before anything is queued: the runner's bounds (alpha 0.0001–0.5, resolution 0.05–5, seed 0 to 2³¹ − 1,
`n_top_genes` 50–10,000), unknown parameters, `counts` together with a FASTQ option (`transcripts`, `genome`,
`annotation`, `engine`, `trimmer`), a `.txt` sample sheet, a file the project does not hold. Free storage is checked
for three times the inputs' size.

**Stages.** *inputs*: each file the parameters name is a file of the person's project (in the page's IndexedDB), named
by its id or its name (the newest file of that name, as the runner takes the newest upload of a bare name). It is
copied into `tcmstudio/jobs/<id>/inputs/` in OPFS, hashed as it is written, and kept only when its SHA-256 is the one
the project recorded (otherwise the job fails as `integrity` and the copy is removed); a sample sheet's `path` cells
are resolved the same way and the sheet is rewritten to name the copies, as the runner's `_stage_sheet` does. The
files are sent nowhere (the model reads a job's status and summary, like any tool result). *analysis*: in one job worker holding numpy and scipy, the inputs mounted read-only, the pipeline
writes its outputs (stage progress comes from its own `stages.json`, through `runrecord.watching`); every file is
persisted to the job's `out/` and registered, the runner's required artefacts under the runner's names (RNA-seq:
`run`, `report`, `results`, `limitations`; single-cell: `run`, `report`, `clusters`, `cells`, `limitations`) and the
rest by path. The stage is checkpointed on its parameters and its inputs' digests. *verify*: in the same worker, with
the inputs still mounted where the run record says they were read, `verify_pipeline` runs the pipeline's own
`verify_run` (every input and output re-hashed, the stages complete, each table's columns and rows) and the kind's
`required_artefacts` and `validator:run` checks as the runner's job service makes them, under the runner's names; only
a pass gives device evidence `verified`.

**Limitations.** The job carries the run's own `limitations.md` lines, then "Computed in this browser with Pyodide's
numpy 2.4.6 and scipy 1.18.0; the native run is the reference, and its numbers can differ in the last digits (held to
a stated tolerance)", the versions being those the worker reports, and for single-cell "The UMAP layout is a picture
of the graph: it amplifies last-digit differences and is not compared with the native run's".

**How it was compared with the native run** (`studio/web/test/runtime/science.mjs --mode omics`). Natively, the
harness writes the two known-answer references as count tables: `omics_counts_world` v1 (`make_counts_world(seed=1)`:
2,000 genes × 6 samples, 40 genes planted four-fold up and 40 four-fold down) and `sc_world` v1 (`make_sc_world(seed=0)`,
four samples in two batches) as four gzipped per-sample TSVs and a sample sheet, checked cell for cell against
`sc_world`'s matrices as they are written. It runs `bioagent rnaseq --counts … --samples …` and `bioagent scrna` on them
with the reference manifest's parameters (RNA-seq: `~ condition`, contrast `condition,treated,control`, built-in test,
alpha 0.05; single-cell: built-in analysis, Harmony, `n_top_genes` 600, seed 0) at one BLAS thread
(`OPENBLAS_NUM_THREADS=OMP_NUM_THREADS=MKL_NUM_THREADS=1`: natively the UMAP depends on the thread count), and records
Python, numpy, scipy, the machine and the threads. In headless Chromium the same files become a project's files, the
two jobs are submitted to the page's job service with the same parameters, and every output is read back at its
recorded digest and compared file by file:

| | RNA-seq (`pipeline.rnaseq`, 15 files) | Single-cell (`pipeline.scrna`, 20 files; 1,077 cells analysed) |
|---|---|---|
| identical bytes | `counts.tsv`, `vst.tsv`, `report.md`, `limitations.md`; `plots/ma.svg`, `p_values.svg`, `pca.svg`, `volcano.svg` | `clusters.tsv`, `markers.tsv`, `paga.tsv`, `report.md`, `limitations.md`; `plots/markers.svg`, `pca_variance.svg`, `qc_genes.svg`, `qc_mito.svg` |
| identical apart from what is not compared | `report.html` with each inline figure set aside | `report.html` likewise; `cells.tsv`, every cell but `umap_1`, `umap_2` |
| within the tolerance below, not identical bytes | `deseq2_results.tsv`: 91 numeric cells differ, the largest using 0.133 of the bound (a `p_adjusted` of 0.205714 against 0.205717), the largest relative difference 1.46e-5; `de_diagnostics.tsv`: 61 cells, 0.083 of the bound (a gene-wise dispersion), relative 2.29e-5 | pseudobulk tables: B cell 20 cells, 0.034 of the bound (relative 8.74e-6); CD14+ monocyte 89, 0.074 (8.66e-6); NK cell 206, 0.082 (1.81e-5); Platelet 26, 0.047 (5.23e-6) |
| not compared, and said so | `plots/dispersion.svg`, `plots/sample_distances.svg`: figures with coordinates printed at two or three digits, drawn from tables that are compared | `cells.tsv` `umap_1`, `umap_2`, `plots/umap_clusters.svg`, `umap_samples.svg`, `paga.svg`: the UMAP layout, which amplifies last-bit differences |
| compared exactly | the genes called (`p_adjusted` < 0.05): the same 90; `run.json`: the same 5 stages ending the same way, complete, the same 13 outputs | the genes called in each pseudobulk test: the same 16, 0, 0, 0; each cell's cluster and cell type, for all 1,077 cells in order; the clusters (NK cell 341, Platelet 294, B cell 230, CD14+ monocyte 212); `run.json`: the same 15 stages, complete, the same 18 outputs |
| the UMAP's structure instead of its numbers | — | the same cells in the same order, all coordinates finite, and the share of each cell's 15 nearest UMAP neighbours in its own cluster: browser 0.9985, native 0.9983 (required: at least 0.95 and within 0.01 of the native) |

The tolerance, for each numeric cell: |a − b| ≤ 1e-6 + 1e-4·max(|a|,|b|) + one unit in the sixth significant digit of
max(|a|,|b|) (the tables print `%.6g`). The numbers come from iterative fits (the DESeq2 dispersion search, a grid then
golden sections on a flat likelihood; Harmony) that turn a last-bit difference between the native build of numpy,
scipy and BLAS and Pyodide's wasm32 build into a difference the six printed digits can show. Headers, gene and cell
order, NA positions, True/False flags and every text cell must be equal. A gene whose `p_adjusted` crosses alpha fails
the comparison, unless both values are within the tolerance of alpha, when it is named rather than hidden (none did).
Before the harness existed, the same comparison in Pyodide 314.0.7 under Node over three configurations of the two
references used at most 0.157 of this bound (a Platelet pseudobulk p-value), and the tighter 1e-7 + 1e-5·max was
exceeded 1.55-fold. The rule was fitted on these two simulated references; it is not established for other data.

**Known answers on the browser's files** (a Python checker natively reading them, its bounds from
`bioagent/data/pipeline_references.json`; the native files give the same). RNA-seq: the inputs are `omics_counts_world`
v1 by their content digests and the parameters are the manifest's, so the reference label is trusted; 40 of 40 planted
genes found up and 40 of 40 down (at least 38 each required), none in the wrong direction, 10 false discoveries among
90 calls (at most 15), 1,999 genes tested, complete. Single-cell: the parameters are `sc_world`'s; the run carries no
reference label (the count tables name genes by symbol, so their matrix digests are not `sc_world`'s); cluster ARI
against the true cell types 1.0000 over 887 cells, the four cell types named, 16 of 20 planted genes significant in
the B-cell pseudobulk (at least 12), 0, 0 and 0 in the other types (at most 1 each), batch mixing 1.0253 (above 0.8),
complete.

**Receipts, times and memory.** Both jobs succeeded with a receipt at its recorded digest, `outputs_verified`, device
evidence `verified` (`cpu`), checks `verify_run`, `required_artefacts`, `validator:run`, `worker_digests`,
`page_digests`, nothing missing, the inputs recorded at the served files' SHA-256, runtime Pyodide 314.0.7 (CPython
3.14.2) with numpy 2.4.6 and scipy 1.18.0. One run at commit 65fa6aa, on the x86_64 Linux machine where the
references' output digests were recorded (headless Chromium 141, Pyodide from a digest-checked local mirror):

| | RNA-seq | Single-cell |
|---|---|---|
| browser job, start to end | 8.1 s | 9.5 s |
| analysis stage | 3.2 s (after its worker booted in 2.0 s) | 9.4 s (the same worker) |
| inputs and verify stages | 0.02 s and 0.03 s | 0.04 s and 0.03 s |
| WebAssembly memory of the worker | 130 MiB | 224 MiB (the peak since the worker started, the RNA-seq job included) |
| native command, one thread (Python 3.13.16, numpy 2.5.4, scipy 1.18.1) | 1.09 s | 5.33 s |

The last run before the commit gave the same comparison and known answers, with times within 0.2 s; every check
passed in five local runs (four before the commit, one at it). Studio's CI build job runs the same check from this commit on, with Pyodide's numpy and scipy from
jsDelivr: it passed in `studio` #91 (run 39286931542, commit 65fa6aa, 2026-10-19), in headless Chromium on GitHub's runner against a native run on
CI's Python 3.12.15 with numpy 2.5.4 and scipy 1.18.1 at one thread, with the same rows as above (the same files
identical, the same cells within tolerance at the same fractions, the same genes, cells and clusters, the same known
answers; the native UMAP agreement 0.9981); its jobs took 8.2 s and 10.5 s (native 1.26 s and 5.09 s), with 130 and
224 MiB.

**Where the limits come from.** Measured in Pyodide 314.0.7 under Node 22 (not in Chromium), one run each: `sc_world`
four times over (5,424 cells) took 37.4 s and 1.02 GB of WebAssembly memory, with the values of the native run; ten times over
(13,568 cells) reached 4.24 GB, the wasm32 ceiling, and a probe allocating numpy arrays met `MemoryError` at 4.23 GB.
RNA-seq on 20,000 genes × 8 samples took 7.4 s and 163 MB; 60,000 × 8, 21.8 s and 282 MB; 20,000 × 48, 38 s and 282 MB.
Hence 6,000 cells; the 100,000 genes × 200 samples bound lies above anything measured and was not itself run. A
6,780-cell sheet was refused for memory in about 10 ms in that environment; the memory refusals of the page (a
`MemoryError`, a worker's failed allocation, the worker retired) are tested with a scripted worker only: no browser
worker has run out of memory in a test.

## 4. How it is checked

| Check | What it shows | Where | Status |
|---|---|---|---|
| Known answer (planted world, 200 permutations) | a job worker's five tables equal native CPython's committed bytes; the exports recompute. It covers the herb layer, NPASS activities, Reactome enrichment with its permutation null, the rebuttals and the exports; the planted world has no STRING, disease, CMAUP or LOTUS layer, so `network.tsv` and `disease_targets.tsv` are header only and the network topology is not exercised | `tcmstudio.browser_selftest`; `studio/e2e/science.spec.mjs`; Settings → Data → *Check this browser* | implemented and tested: identical bytes in headless Chromium 141 (Pyodide 314.0.7, wasm32), locally and in Studio's CI with Pyodide's pyarrow 22.0.0 from jsDelivr |
| The job service end to end | `research.run` and the self-test as jobs: stages, the research stage's checkpoint, a receipt at its recorded digest with `outputs_verified` and device evidence `verified`, the five tables the committed answer; a second run whose workers are stopped as its research stage ends fails, and resumed it reuses that stage from its checkpoint and gives the same tables | `studio/web/test/runtime/science.mjs --mode service` (Studio CI, build job) | implemented and tested in headless Chromium, locally and in Studio's CI with Pyodide's pyarrow 22.0.0 from jsDelivr (`studio` #88, 2026-10-19) |
| Snapshot build from files in OPFS | the parser tests' fixture files mounted read-only from OPFS (a write to one is refused, their bytes in OPFS unchanged) and built in a job worker; then imported through the dataset workspace and built by `research.snapshot` as a job, down to its snapshots, its marker and its receipt (`outputs_verified`). Both give the native build's six snapshot ids | `science.mjs --mode fixture` (Studio CI, build job) | implemented and tested in headless Chromium, locally and in Studio's CI (as above) |
| 葛根芩连汤 on the real files | `research.snapshot` + `research.run` as browser jobs, every export compared with the native run | live-data workflow, step "The same research in a browser" (`studio/web/test/runtime/science.mjs --mode gqd`) | implemented and tested: every export the native run's bytes in `live-data` #18 and #19 (2026-10-19); repeated on every live-data run. Should a number ever differ in its last bit (glibc's and wasm's `math.exp` are not correctly rounded), the comparison reports that table, and the digests restating it, as within a relative 1e-12 — never as identical |
| RNA-seq and single-cell from count tables, against the native run | `pipeline.rnaseq` (count matrix) and `pipeline.scrna` (count tables) as jobs of the page's job service on `omics_counts_world` v1 and `sc_world` v1, read from a project's files: every output compared with the native command's on the same files, as identical bytes, within the stated tolerance (the largest difference 0.133 of it) or not compared, with the reason (the UMAP layout and figures; the layout's structure is checked instead); the genes called, each cell's cluster and cell type and the clusters exact; the receipts, with the inputs at the files' digests; the known answers on the browser's files ([§3.1](#31-rna-seq-and-single-cell-from-count-tables)) | `science.mjs --mode omics` (Studio CI, build job, from commit 65fa6aa) | implemented and tested in headless Chromium, locally (Chromium 141, Pyodide 314.0.7 from a local mirror) and in Studio's CI with Pyodide's numpy and scipy from jsDelivr (`studio` #91, 2026-10-19): every check passed |
| The omics jobs' refusals, inputs and memory | refused before anything is queued, with the reason: FASTQ, PyDESeq2, Scanpy, scVI, `.h5`, `.h5ad`, out-of-bounds or unknown parameters, a file the project lacks; inputs copied from the project's files, a digest mismatch refused; a run with a scripted worker (artefact names, receipt inputs, the verify outcome); running out of memory a refusal that retires the worker. In Python: each function, run natively, writes the command's bytes (all but `run.json` and `stages.json`), the size limits and a `MemoryError` are typed refusals before or instead of a result, `verify_pipeline` names the runner's checks; the count-matrix mode's refusals and its reference | `web/test/scientific/omics.test.mjs`, `web/test/router.test.mjs`, `web/test/runtime/pyboot.test.mjs`; `studio/runner/tests/test_browser_jobs.py`; `BioScience-Harness/tests/test_omics_rnaseq_counts.py`, `test_pipeline_references.py` | implemented and tested locally; the memory paths with a scripted worker only |
| In-process governed analysis | the same bytes and result digest as the isolated child natively | `BioScience-Harness/tests/test_research_inprocess.py` | implemented and tested |
| Data layer | downloads, resume, relay fallback, checksum refusal, import, cancel | `web/test/scientific/*.test.mjs` (an in-memory OPFS); the import runs in Chromium's OPFS in `--mode fixture` | implemented and tested |
| Job service and pool | queue, cancel, interruption and resume, receipts, worker crashes | `web/test/scientific/job-manager.test.mjs`, `worker-pool.test.mjs` | implemented and tested |
| Relay | registry-bound, ranges, caps, streaming, each host's pace | `studio/edge/test/datasets.test.js` (no real host, not the Workers runtime) | implemented and tested; not deployed; after a deployment a HEAD probe checks it, as a warning only |

## 5. What this does not do (yet)

- **Threads inside a computation, or jobs side by side.** Each job worker runs single-threaded WebAssembly, and the job
  service runs one job at a time in a tab (a snapshot build and a research run share the snapshot store), so the pool's
  further workers stay unused today. A per-source parallel snapshot build across workers is designed (NPASS, CMAUP, LOTUS and
  Reactome are independent) but not built; multithreaded WASM kernels (pthreads) are not written.
- **GPU or NPU for these jobs.** The research loop is integer and float Python with exact outputs; no WebGPU kernel is
  used, and no WebNN model exists (BROWSER_COMPUTE.md §5).
- **DuckDB-Wasm / Arrow.** Not used: the reference implementation is the runner's Python, and a second implementation
  of the joins would have to be shown equal to it. Pyodide's own `pyarrow` writes the snapshot store.
- **The rest of the pipelines.** RNA-seq from FASTQ (QC, trimming, quantification), PyDESeq2, Scanpy, scVI, 10x HDF5
  and `.h5ad` inputs stay on the runner; docking (Vina, RDKit), ADMET (RDKit) and structure prediction have no browser
  engine. A call for them is refused in the browser with the reason, never run another way.
- **Byte-identical omics outputs.** The omics jobs are not the native bytes throughout: the DE and pseudobulk tables
  differ from the native run in their last printed digits (within the stated tolerance), and the UMAP layout and five
  figures are not compared at all ([§3.1](#31-rna-seq-and-single-cell-from-count-tables)). The tolerance was fitted on
  two simulated references and is not established for other data; only the parameters of those references were
  compared in a browser (`n_top_genes` 600, Harmony, seed 0; a pseudotime root, `integration: none`, `doublets: off`
  or a marker table of one's own were not).
- **Larger omics inputs.** More than 6,000 cells, or more than 100,000 genes or 200 samples, is refused for the runner.
  The limits come from Pyodide under Node, not from a browser tab; a tab's own memory limit, a low-memory device and a
  phone are not measured, and no worker has run out of memory in a browser test.
- **10x HDF5.** Pyodide has h5py, and an `.h5` written and read back in Pyodide under Node gave the matrix digest
  `sc_world` records for that sample, but reading one through the job's file mount is untested, so `.h5` is refused in
  the browser for now.
- **Background work.** A browser job stops when its page closes; it is never presented as continuing.
- **Phones.** Storage quota and memory for 418 MB of files, and the omics jobs' memory, have not been measured on a phone.
- **Browsers.** Run in Chromium only. The page writes job records' files (receipts, checkpoints, the snapshot marker) with
  `createWritable`; a browser whose main thread lacks it (older Safari) refuses those writes, and the job fails rather
  than pretending to have kept them. Firefox and Safari have not been run.
- **Archives without a checksum.** The runner reads a `.gz` through its CRC before keeping it; the browser does not, so a
  truncated STRING aliases file (no reviewed checksum) is kept as UNVERIFIED and fails later, when Python parses it.
- **Two tabs downloading the same file** do not corrupt it, but one finished download can be discarded and fetched again.

## 中文摘要

**浏览器科研任务**：网络药理学研究任务（`research.snapshot` 构建快照、`research.run` 十六阶段研究），以及（指南的 P2）
从计数矩阵出发的 RNA-seq 差异表达（`pipeline.rnaseq` 的计数矩阵模式）和从计数表出发的单细胞分析（`pipeline.scrna`），
都可以完全在访客浏览器中运行，无需本机 Runner 或计算服务器。计算在"任务工作线程"中进行：每个线程是一个装有 Pyodide 314.0.7（编译为
WebAssembly 的 CPython 3.14）和经 SHA-256 校验的应用包的 Web Worker，运行与 Runner 完全相同的 `bioagent`/`psh` 代码；
线程池按设备大小设定（页面可让出的 CPU 核数，且每 1.5 GB 设备内存至多一个、总数至多四个），但任务服务在一个标签页中一次只运行一个任务、
用一个线程，因此目前同一时间只有一个线程在计算。17 个原始文件（418 MB）
保存在本站的私有文件系统（OPFS）中，Python 通过同步访问句柄就地只读读取；下载可直连上游、经本站边缘中转（流式、
不存储、按 bioagent 对各主机的节奏限速）或由用户导入，并在构建快照时按审核过的校验和再次核验。构建前按已审核的文件
大小检查剩余存储空间；三个没有审核大小的文件不做估计，任务日志会列出它们。浏览器 Worker 不能启动子进程，因此受治理的
分析在同一进程内经同一个 PSH 代理与审计链运行，产物如实记录 `isolation: "in_process"`。任务记录存于 IndexedDB，可取消；
页面关闭时任务中断，下次可从已核验的阶段继续。每个任务写出回执：运行位置、运行时、各线程的 WebAssembly 内存峰值、
每个输出（包括快照与账本）的两次独立哈希读取。

**验证情况**：模拟世界（已知答案）在 Chromium 的任务工作线程中写出的五张表与原生 CPython 逐字节一致——本地和 Studio CI
（使用 jsDelivr 上 Pyodide 自带的 pyarrow 22.0.0）均已通过；它覆盖草药层、NPASS 活性、带置换零分布的 Reactome 富集、
反驳与导出，但没有 STRING、疾病、CMAUP、LOTUS 层，网络拓扑未被检验。任务服务端到端检查（检查点、回执、工作线程丢失后
从检查点续跑）与 OPFS 中夹具文件的快照构建（`research.snapshot` 作为任务运行，快照编号与原生一致）在无头 Chromium
中通过：本地及 Studio 的 CI（`studio` #88，2026-10-19，使用 jsDelivr 上 Pyodide 自带的 pyarrow 22.0.0）均已通过。葛根芩连汤真实数据在 live-data 流程中以浏览器任务运行并与原生运行逐个
导出文件比对：已在 `live-data` #18 与 #19 中通过（运行 39251758718 与 39258060948，提交 43918ac 与 53bdbb0，2026-10-19，GitHub 运行器上的无头 Chromium，Pyodide 314.0.7 及其 pyarrow 22.0.0）：17 个原始文件导入本站私有存储，`research.snapshot` 与 `research.run` 作为浏览器任务成功运行，每个导出文件与同一批文件上的原生运行逐字节一致（五张表、`provenance.json`、`limitations.md`、`report.html`；`run_manifest.json` 除执行块外一致）。该次运行：4 味药材、1,792 个成分、370 个实测人类靶点、237 个节点 624 条边的 STRING 网络、检验 959 条 Reactome 通路，BH 校正后无一显著（因此真实数据上未抽取置换零分布；模拟世界检验了它）。中转已实现并有单元测试，尚未部署。

**RNA-seq 与单细胞（计数表，§3.1）**：在浏览器中运行的是 bioagent 的同一套代码（`bioagent rnaseq --counts`、
`bioagent scrna`），只用 Pyodide 的 numpy 与 scipy（首次加载约 16.2 MiB；Pyodide 未能取得的包会以「不可用」拒绝，不会被记为已加载）。RNA-seq：计数表（基因 × 样本的整数表，
TSV 或 CSV，可 gzip；`gene_id`、可选 `gene_name`、每个样本一列）加样本表（`sample` 列与设计变量），内置 DESeq2 实现、
VST、PCA、样本距离、图与报告。单细胞：计数表（CSV、TSV 或 TXT，可 gzip；基因为行）或由它们组成的样本表（`.csv`/`.tsv`，
含 `sample`、`path`），可选标记基因表；内置分析、内置 Harmony 或不整合、内置伪批量检验，`n_top_genes` 可设。以下情况在
排队前即被拒绝并说明原因，由 Runner 运行：从 FASTQ 出发的运行（没有 `counts`）、把 FASTQ 样本表当作 `samples`、
PyDESeq2、Scanpy、scVI、`.h5ad`、10x HDF5（`.h5`：Pyodide 有 h5py，但经任务文件挂载读取尚未测试，暂不支持）、
Cell Ranger 目录；超过 6,000 个细胞、或超过 100,000 个基因或 200 个样本的输入，在分析读入全部数据之前即以
「需要内存」拒绝（wasm32 每个工作线程约 4 GB 上限），运行中出现 `MemoryError` 也是同样的拒绝，而不是运行时错误，
该工作线程随即退役。超出 Runner 界限或未知的参数、与 `counts` 同时给出的 FASTQ 选项、`.txt` 样本表、项目中没有的文件
都作为参数错误拒绝；可用存储空间按输入大小的三倍检查。输入是用户项目中的文件：按 ID 或文件名（同名取最新）找到，复制到该任务的 OPFS 目录，写入时计算
哈希，与项目记录的 SHA-256 不一致即拒绝；样本表中的路径同样解析并改写为副本；每个输入记入任务及其回执（`inputs`）。
这些文件不会被发送到任何地方（模型读到的是任务的状态与摘要，与其他工具结果一样）。分析在一个任务工作线程中运行，输出全部持久化，并在同一线程中由 `verify_pipeline` 按 Runner 的检查
（流程自身的 `verify_run`、`required_artefacts`、`validator:run`）复核。局限说明附上一行：数值由本浏览器中 Pyodide 的
numpy 2.4.6 与 scipy 1.18.0 计算（版本取自工作线程的报告），原生运行是参考，末位数字可能不同（按给定容差比对）；单细胞
另注明 UMAP 布局不与原生比较。

**它们如何与原生运行比对**（`science.mjs --mode omics`）：在两个已知答案参考上（`omics_counts_world` v1：2,000 个基因
× 6 个样本，上调、下调各预设 40 个四倍差异基因；`sc_world` v1 写成四个 gzip 的单样本 TSV 加样本表），原生命令以参考清单的
参数、单个 BLAS 线程运行；在无头 Chromium 141 中，同一批文件作为项目文件，以浏览器任务提交，逐个读回输出并比对。
RNA-seq：`counts.tsv`、`vst.tsv`、`report.md`、`limitations.md` 与四张图逐字节一致；`report.html` 除内嵌图外一致；
`deseq2_results.tsv`（91 个数值格不同，最大用到容差界的 0.133，最大相对差 1.46e-5）与 `de_diagnostics.tsv`（61 格，
0.083，2.29e-5）在容差内；离散度图与样本距离图不比较（图中坐标只印两三位，所画数值在表中已比较）；显著基因
（`p_adjusted` < 0.05）同为 90 个。单细胞（1,077 个细胞）：`clusters.tsv`、`markers.tsv`、`paga.tsv`、`report.md`、
`limitations.md` 与四张图逐字节一致；`cells.tsv` 除 `umap_1`、`umap_2` 外每格一致；四个伪批量表在容差内（最大 0.082，
最大相对差 1.81e-5）；各伪批量检验的显著基因同为 16、0、0、0 个；全部 1,077 个细胞的聚类与细胞类型、聚类表完全相同；
UMAP 坐标与三张 UMAP 图不比较，改查结构：每个细胞 15 个最近 UMAP 邻居中同簇的比例，浏览器 0.9985、原生 0.9983（要求
至少 0.95，且与原生相差不超过 0.01）。容差为：|a − b| ≤ 1e-6 + 1e-4·max(|a|,|b|) + max(|a|,|b|) 第六位有效数字的一个
单位（表格按 `%.6g` 输出）；表头、基因与细胞顺序、NA 位置、True/False 标记和文字必须完全相同；跨越 alpha 的基因判为失败，
除非两个值都在 alpha 的容差内，此时点名报告（本次没有）。此前在 Node 下的 Pyodide 中对三种配置做的同样比较，最大用到
界的 0.157（一个血小板伪批量 p 值），而更严的 1e-7 + 1e-5·max 被超出 1.55 倍。这一容差是在两个模拟参考上拟合的，对其他数据未经确立。已知答案在浏览器写出的
文件上成立（原生文件亦然）：RNA-seq 上调 40/40、下调 40/40（各至少 38），方向错误 0，90 个显著中假发现 10 个（至多 15），
检验 1,999 个基因；单细胞聚类 ARI 1.0000（887 个细胞），四种细胞类型，B 细胞伪批量中 20 个预设基因找到 16 个（至少 12），
其他类型各 0 个（至多 1），批次混合度 1.0253（大于 0.8）。两个回执都在其记录的摘要处，`outputs_verified`，设备证据
`verified`。在提交 65fa6aa 上的一次本地运行（本机，无头 Chromium 141，Pyodide 来自经摘要核对的本地镜像）：RNA-seq
任务 8.1 秒（分析阶段 3.2 秒，此前工作线程启动 2.0 秒），工作线程 WebAssembly 内存 130 MiB，原生命令单线程 1.09 秒；
单细胞任务 9.5 秒（分析阶段 9.4 秒），同一工作线程内存 224 MiB（自启动以来的峰值，含之前的 RNA-seq 任务），原生 5.33 秒。
本地五次运行全部通过（提交前四次、提交时一次）。Studio 的 CI 构建作业自这一提交起运行同一检查（使用 jsDelivr 上 Pyodide 的 numpy 与 scipy）：已在 `studio` #91（运行 39286931542，提交 65fa6aa，2026-10-19）中通过，在 GitHub 运行器上的无头 Chromium 中，对照 CI 上 Python 3.12.15、numpy 2.5.4、scipy 1.18.1 的单线程原生运行，比对结果与上面相同（原生 UMAP 同簇比例 0.9981），两个任务分别用时 8.2 秒与 10.5 秒（原生 1.26 秒与 5.09 秒），内存 130 与 224 MiB。限值来自 Node 下 Pyodide 314.0.7 的测量（不是
Chromium）：`sc_world` 放大四倍（5,424 个细胞）用 37.4 秒、1.02 GB，放大十倍（13,568 个细胞）达到 4.24 GB 的 wasm32
上限；RNA-seq 60,000 × 8 用 21.8 秒、282 MB，20,000 × 48 用 38 秒、282 MB。100,000 × 200 这一界限高于所测，本身未运行过；
页面的内存拒绝路径只用脚本化的工作线程测试过，没有任何浏览器工作线程在测试中真正耗尽内存。

**尚未实现**：计算内的多线程 WASM、GPU/NPU、其余流程（从 FASTQ 出发的 RNA-seq、PyDESeq2、Scanpy、scVI、10x HDF5 与
`.h5ad` 需要 Runner；对接、ADMET、结构预测在浏览器中没有引擎）、组学输出与原生的逐字节一致（表格末位数字在容差内不同，
UMAP 不比较；只比对过两个参考的参数）、手机端与浏览器标签页内存的测量。目前只在 Chromium 中运行过（Firefox、Safari 未运行；主线程缺少 `createWritable` 的浏览器写不了任务记录，
任务会失败而不是假装已保存）；浏览器不像 Runner 那样在保存前校验 `.gz` 的 CRC，没有审核校验和的 STRING 别名文件若被
截断，会标为 UNVERIFIED，并在 Python 解析时失败。
