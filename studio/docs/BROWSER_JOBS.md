# Browser science jobs

How TCMScience Studio (<https://science.impf.ai>) runs research jobs inside a visitor's browser, with no local runner
and no compute server: the network-pharmacology research loop, (the guidebook's P2) RNA-seq differential
expression from a count matrix and single-cell analysis from count tables, and (P3) ADMET: the model build and the
predictions. What runs where, how the data gets there, how a result is shown to be the same result the runner gives,
and what is not done. Written on 2026-10-19 on branch `claude/epic-dirac-c1uqll`; ADMET ([§3.2](#32-admet)) and docking from prepared structures
([§3.3](#33-docking-from-prepared-structures)) added on
2026-10-20. Status words are those of [BROWSER_COMPUTE.md](BROWSER_COMPUTE.md) (implemented and
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
native run on the same files, passed in `live-data` #18, #19 and #21 (runs 39251758718, 39258060948 and 39286930354, commits 43918ac, 53bdbb0 and 65fa6aa, 2026-10-19, headless Chromium on GitHub's runner, Pyodide 314.0.7 with its pyarrow 22.0.0): the 17 raw files imported into the site's private storage, `research.snapshot` and `research.run` succeeded as browser jobs, and every export was the native run's bytes on the same files (the five tables, `provenance.json`, `limitations.md`, `report.html`; `run_manifest.json` apart from its execution block). That run: 4 herbs, 1,792 constituents, 370 measured human targets, a 237-node, 624-edge STRING network, 959 Reactome pathways tested, none significant after BH (so no permutation null was drawn there; the planted world exercises it). The RNA-seq pipeline in its count-matrix mode (`pipeline.rnaseq` with `counts`) and the single-cell pipeline on count tables (`pipeline.scrna`) run the same way, with Pyodide's numpy and scipy, on files from the person's project ([§3.1](#31-rna-seq-and-single-cell-from-count-tables)); a FASTQ run, PyDESeq2, Scanpy, scVI, 10x HDF5 and `.h5ad` inputs, and inputs larger than a browser worker holds (more than 6,000 cells, a table of more than 66,000,000 cells × genes, a count matrix of more than 10,000,000 genes × samples) are refused with the reason and left to the runner. On their known-answer references (`omics_counts_world` v1 and `sc_world` v1) every output was compared with the native command's on the same files (`science.mjs --mode omics`, headless Chromium 141, locally): identical bytes, or numbers within a stated tolerance (the largest difference 0.133 of it), or, for the UMAP layout and its figures, not compared and said so; the genes called and each cell's cluster and cell type are identical, and the known answers hold on the browser's files. The same check passed in Studio's CI, with Pyodide's numpy and scipy from jsDelivr and the native side on CI's own Python 3.12.15 (`studio` #91 (run 39286931542, commit 65fa6aa, 2026-10-19)). ADMET runs the same way, with two parts the page computes itself ([§3.2](#32-admet)): `admet.models` builds the 22 TDC endpoint models in this browser from the TDC ADMET group archive (nothing trained is shipped) and keeps them in the site's private storage, and `pipeline.admet` predicts with them; the chemistry is RDKit.js 2026.9.1 with exact ports of what it lacks (a molecule they cannot standardise exactly is refused, named, and left to the runner, never approximated), and the applicability-domain search runs on this device's WebAssembly SIMD kernel, or on its GPU only where this device's own check measured the GPU exact and faster, Python checking the answers before it uses them. Compared with native RDKit 2026.9.1 and scikit-learn 1.8.0 at one thread (`science.mjs --mode admet`, headless Chromium 141 with Pyodide 314.0.7, locally): on a small known-answer archive the cards (apart from the files' digests, the seconds and the chemistry's name), the domain files, every prediction at full precision and `admet.tsv` were identical; on a synthetic archive at the TDC group's sizes the raw model outputs and `admet.tsv` were identical and 52 of 16,120 test-set predictions differed by 1 or 2 units in the last place of float64 (the C library's `exp`, or numpy's power, after the trees). On the real TDC archive (1,472,501 bytes at the pinned SHA-256, downloaded natively in CI and given to the page as a project file) the same comparison passed in `live-data` #24 (run 39386727431, commit 0882e95, 2026-10-20; headless Chromium 141 on GitHub's runner, Pyodide 314.0.7 and RDKit.js 2026.9.1 from jsDelivr): the browser chemistry refused 202 of the 81,809 molecules (0.25%), each at a named step, and the native models were trained without them as the browser's were; the 22 cards (apart from the digests, the seconds and the chemistry) and the domain files were identical; on the 16,353 kept test molecules every raw model output and domain search was identical and 36 final predictions differed by 1 unit in the last place of float64 (through `10 **` or `expit`); `pipeline.admet` with these models wrote the native run's `admet.tsv` byte for byte. Studio's CI runs the comparison on the small archive (`studio` #95 (run 39386727362, commit 0882e95, 2026-10-20)). This is implemented and tested locally and in CI; it has not run on a physical GPU (P3's acceptance criterion is not met), in a tab on a person's machine, or with the archive downloaded through the relay. Docking runs the same way in a new **prepared-input mode** ([§3.3](#33-docking-from-prepared-structures)): `pipeline.dock` takes a rigid-receptor PDBQT, a ligand PDBQT and a site, and runs **AutoDock Vina 1.2.7 compiled to WebAssembly** (not a Pyodide package) — built from pinned, SHA-256-checked inputs, two independent builds byte-identical — one Vina run per ligand in dedicated workers on this device's CPU, with the plan, analysis and checks in a Pyodide worker as `bioagent.docking.prepared` runs natively. Vina's `score()` and `optimize()` are bit-identical to the native 1.2.7 package on all 6 fixture ligands, but a seeded search is **not** bit-reproducible (native Vina itself moves when its maths library changes), so docking is held to a measured band over seeds 1–8, not to exactness; the end-to-end comparison (`science.mjs --mode dock`) found the plan bytes and the re-scored pose energies identical and the search inside that band (benzamidine redocks 1.14 Å in the browser, 1.92 Å natively). Receptor and ligand preparation from raw PDB/SMILES (Meeko, RDKit) stays on the runner as the next cycle (Milestone B, not done), and nothing in docking uses the GPU. It is implemented and tested locally and in CI, not on a deployed site.

## 1. What runs where

| Part | Where | Code |
|---|---|---|
| Planning a call, approvals | the page | `js/core/router.js`: a job placed in the browser goes to the job service, never to the tool worker |
| Job records, queue, cancel, resume, receipts | the page | `js/scientific/runtime/job-manager.js` (IndexedDB `tcmstudio-jobs`) |
| The computation (the research loop; RNA-seq and single-cell from count tables; ADMET's model build and pipeline; the docking job's plan, analysis and checks) | job workers (Pyodide, CPU, WebAssembly) | `js/scientific/engines/science.worker.js` → `tcmstudio.browser_jobs` → `bioagent` |
| The docking job's search: one Vina run per ligand | Vina workers (AutoDock Vina 1.2.7, WebAssembly, CPU; no Pyodide) | `js/scientific/dock/dock.worker.js` (one module per worker from `dock/vina.js`), results read by Python (`bioagent.docking.prepared.PrecomputedVina`) |
| The ADMET jobs' chemistry: standardisation, fingerprints, descriptors, alerts | a chemistry worker (RDKit.js 2026.9.1, WebAssembly, CPU; no Pyodide) | `js/scientific/chem/chem.worker.js`, handed to Python as prepared sets (`chem/prepared-set.js` → `bioagent.admet.engine.PrecomputedEngine`) |
| The ADMET applicability-domain search (Tanimoto) | the page: WebAssembly SIMD (JavaScript without SIMD), or WebGPU where this device's check measured it exact and faster; checked again in Python | `js/compute/science-scheduler.js` → `bioagent.admet.engine.PrecomputedSimilarity` |
| The pipeline jobs' inputs: the project's files, copied into the job's directory and checked against their recorded SHA-256 | the page | `js/scientific/pipelines/omics.js` and `admet.js`, through `js/scientific/runtime/jobs-runtime.js` (`projectFilesResolver` over the project file store, `js/core/store.js`) |
| Downloads, imports, hashing | a data worker (no Pyodide) | `js/scientific/data/data.worker.js` |
| Files | OPFS, under `tcmstudio/` | `js/scientific/data/opfs-store.js` |
| Relay for hosts that refuse a cross-origin download | the edge Worker, streaming, keeping nothing | `studio/edge/src/datasets.js` (`/api/datasets/files/<group>/<name>`) |
| Language model | Tao-S1 through `/v1` (unchanged) | not a computation of the job |

Job kinds the browser runs: `research.snapshot`, `research.run`, `pipeline.rnaseq` (its count-matrix mode only),
`pipeline.scrna` (count tables, built-in engines only), `admet.models` and `pipeline.admet` (a CSV, TSV or `.smi`
file, or one SMILES), and `pipeline.dock` (its prepared-input mode only: PDBQT structures) (catalog
`BROWSER_JOB_KINDS`; each carries its own `execution.browser_job`: the Pyodide packages
it loads, and for the pipelines what they read, their engines, for ADMET the chemistry (`rdkit.js` 2026.9.1) and the
molecule limit, for docking the Vina engine (`vina` 1.2.7, the WebAssembly build) and the ligand, receptor and box
caps, and `runner_only`, the arguments that take a call to the runner, each with its
reason). A connected runner still takes them under compute **Auto** (one place per project); under **Browser** they
run here. A pipeline call (`run_pipeline {pipeline, arguments}` or `call_tool job.pipeline.rnaseq|scrna|admet|dock`) whose
arguments only the runner runs
goes to a connected runner, and without one is refused `needs_runner` with the reason, before anything is queued;
with compute set to **Browser** and the runner connected it is refused `compute_browser`, with that reason and the
setting. Arguments the runner's schema refuses are `bad_arguments` before the call is placed or anyone is asked to
approve it: `run_pipeline` without a `pipeline` or naming none of the catalog's (the refusal lists them), `arguments`
that are not an object (JSON text of one is read as that object, as the dispatcher reads it), the kind's parameters
given beside `arguments`, and `call_tool` arguments that are not an object. A kind looked at without a call (the
catalog page, `catalog_search`) is ready where its job service runs it, and the catalog page names a stopped job
service as this browser's, not the runner's. Should the job service's module not load, the router reads what needs
the runner from the catalog's `execution.browser_job.runner_only` rules.
Every other job kind (`pipeline.fold`, `skill.run`, `tcmdb.*`, `env.provision`) stays on the runner,
and a call for one in the browser says so; so does the governed skill `predict-admet`, which wraps the same ADMET
pipeline as a skill run, and the governed skill `dock-ligands`, which wraps docking. `pipeline.dock` is no longer wholly
runner-only: its prepared-input arguments run here ([§3.3](#33-docking-from-prepared-structures)), and only the
raw-structure arguments (a PDB/`PDB:`/`UniProt:` receptor, SMILES/CSV/SDF ligands, `site_ligand`, `allow_remote`) take a
call to the runner.

### 1.1 Job workers

`ScienceWorkerPool` sizes itself from the device: `min(cores − 1, ⌊memory GB / 1.5⌋)`, between 1 and 4 (memory unknown
is taken as 2 GB, and the reason is recorded). A worker boots lazily the first time a job leases it (Pyodide from the
pinned index, the bundle from Cache Storage when its hash matches, the compiled modules put back), loads the Pyodide
packages a stage needs (`pyarrow` for the snapshot store; `numpy` and `scipy` for the omics jobs, 2,960,568 + 14,029,750
bytes, about 16.2 MiB, fetched the first time a worker loads them; for the ADMET jobs `numpy`, `scipy` and
`scikit-learn` 1.8.0, which brings `joblib` and `threadpoolctl`; all from the same Pyodide distribution), and runs
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
records) lists the 17 files: URL, host, reviewed size and checksum where one exists (LOTUS is checked by md5; STRING's
aliases file, a frozen release, by the size and SHA-256 two live downloads read; two files, Reactome's UniProt mapping and
the UniProt accession list, have neither a reviewed checksum nor a reviewed size and are kept as UNVERIFIED, as on the
runner), licence, what is known about redistribution — from a reviewed licence record, or "unknown — downloading is not
permission to redistribute" — and the pace bioagent keeps with its host (`rate_rps`: zenodo.org 0.1 a second, its robots.txt
Crawl-delay; reactome.org 5; rest.uniprot.org 10; otherwise 1), which the relay keeps too. A second group, `admet`,
lists one file, the TDC ADMET group archive the ADMET model build reads ([§3.2](#32-admet)): its URL and host
(dataverse.harvard.edu), the SHA-256 bioagent pins (`bd0005246cb6…`) and the pinned archive's size (1,472,501 bytes),
licence `LicenseRef-TDC-per-dataset` with redistribution unknown (no licence record here has read TDC's terms), relay
allowed, and one redirect host, `dvn-cloud-iqss.s3.amazonaws.com`: Dataverse answers with a 303 to a signed URL on
its S3 storage (seen in the live-data workflow, 2026-10-20, where the archive came from there at the pinned SHA-256).
No relayed download of it has been run in a browser: in CI (`live-data` #24) the archive is downloaded natively and
given to the page as a project file ([§3.2](#32-admet)).

Before a snapshot build starts, the browser's free storage is checked against the reviewed sizes of the files still
missing (×1.1); the two files without a reviewed size are not guessed at, and the job's log names them.

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
verify stage ran in this attempt and said it passed (a snapshot build has none: `validated`). A stage that ran kernel
calls (the ADMET domain search) carries their compute facts, and the receipt gains `compute` (per stage: the backend
of each call, the GPU's adapter and rung, the calibration it cites, `accelerated` with its basis); when a stage used
a GPU result the device is `cpu+webgpu`, and the evidence is `accelerated` only when every GPU call of it reached that
rung ([§3.2](#32-admet)). A GPU result Python then rejected is marked discarded and counts for nothing. An omics job's receipt
also lists its `inputs`: each file's SHA-256 as the job read it, its size and the project file it came from (a sample
sheet's tables under `source.files`, as the runner's receipt keeps them).

### 3.1 RNA-seq and single-cell from count tables

Two of the runner's pipeline kinds run in part as browser jobs. The Python is bioagent's own (`bioagent rnaseq
--counts`, `bioagent scrna`), called through `tcmstudio.browser_jobs.run_rnaseq_counts` and `run_scrna`; the built-in
paths import nothing but numpy, scipy (Pyodide's) and the standard library.

| Kind | What runs in the browser | Refused here, with the reason (the runner runs it) |
|---|---|---|
| `pipeline.rnaseq` | the count-matrix mode: `counts`, a gene × sample table of integers (TSV or CSV, gzipped or not: `gene_id`, an optional `gene_name`, one column per sample), and `samples`, a sample table (a `sample` column and the design variables); the built-in DESeq2 implementation, VST, PCA, sample distances, the figures, `report.md`/`report.html`, `limitations.md`, `run.json` | a run from FASTQ (no `counts`: QC, trimming, quantification), `de_backend: pydeseq2` (PyDESeq2 is not in Pyodide) |
| `pipeline.scrna` | `source`, a count table (CSV, TSV or TXT, gzipped or not; genes in rows, cells in columns) or a sample sheet (`.csv`/`.tsv` with `sample`, `path` and the sample variables) of such tables, and an optional marker table; the built-in analysis (QC, doublets, highly variable genes up to `n_top_genes`, PCA, built-in Harmony or none, neighbours, Leiden, UMAP, markers, annotation, PAGA and pseudotime, pseudobulk with the built-in DESeq2 implementation) | `analysis_backend: scanpy` and `integration: scvi` (not in Pyodide), an `.h5ad` (anndata is not in Pyodide), a 10x HDF5 `.h5` (not yet supported in the browser: Pyodide has h5py, but reading through the job's file mount is untested), a Cell Ranger directory, which the runner reads where it is (`bioagent scrna <dir>`): a directory cannot be a project file, so a name that is no file of the project and no table is refused as `bad_arguments`, the refusal saying that the runner reads one |

Both are also refused, before the analysis reads anything in full, when the input is larger than a job worker can
hold. Single cell: more than **6,000 cells** in all, or one table of more than **66,000,000 cells × genes** (2,000
cells of 33,000 genes; a table is one sample), each table counted a line at a time (its header gives the cells, its
other non-blank lines the genes). RNA-seq: a count matrix of more than **10,000,000 counts** (genes × samples), with
at most **100,000 genes** and **200 samples**, counted from the count table's lines. The refusal is typed `refused`
with need `memory` and gives the count, for example "t.tsv.gz: 2,712 cells x 33,000 genes (89,496,000 counts) exceed
the 66,000,000 counts of one table a browser worker can hold (wasm32, 4 GB): run it on the runner" or "100,000 genes
x 101 samples (10,100,000 counts) exceed the 10,000,000 counts (genes x samples; at most 100,000 genes and 200
samples) a browser worker can hold (wasm32, 4 GB): run it on the runner" (**Where the limits come from**, below). A
`MemoryError` anywhere in a run, and a worker's own allocation failure, is the same refusal, never a runtime error and
never a silently changed method; the worker is then retired (its memory never shrinks). Arguments the runner would refuse are refused here as `bad_arguments`
before anything is queued: the runner's bounds (alpha 0.0001–0.5, resolution 0.05–5, seed 0 to 2³¹ − 1,
`n_top_genes` 50–10,000), unknown parameters, `counts` together with a FASTQ option (`transcripts`, `genome`,
`annotation`, `engine`, `trimmer`) or with a FASTQ sample sheet as `samples` (in the runner's words: "samples: with
counts this is the sample table (sample and the design columns); a FASTQ sample sheet runs without counts"), a
`.txt` sample sheet, a file the project does not hold. An input no engine could read is refused with the reason, as
the native command refuses it (need null), never reported as a crash: a gzipped table whose stream is corrupt or cut
short ("… cannot be read to its end (…)"), a single-cell table that is not UTF-8. Free storage is checked for three
times the inputs' size.

**Reading the tables.** bioagent's single-cell table reader (`bioagent.omics.sc.io.read_table`, natively too) reads a
count table a row at a time and keeps each row's non-zero counts, so a table needs memory for its counts rather than
for its text. It gives the matrix the whole-text parse gave: checked equal in structure, values, types, cells, gene
ids and names on `sc_world`'s tables, a 33,000-gene table and a 2,000 × 33,000 one, CSV and TSV, gzipped, CRLF, quoted
cells, a byte-order mark, NaN, and in `BioScience-Harness/tests/test_omics_scrna.py`. The gain is modest (2.0 → 1.55
GB for one table of 1,356 × 33,000): after reading, the doublet scores' dense matrix dominates. A blank line is now
skipped (it raised `IndexError`), and a row of the wrong length is refused as "rows have N values for M cells"
(before, numpy's message under "a value is not a number"). A Cell Ranger directory whose gzip is corrupt or cut short
is refused the same way on the runner.

**Stages.** *inputs*: each file the parameters name is a file of the person's project (in the page's IndexedDB), named
by its id or its name (the newest file of that name, as the runner takes the newest upload of a bare name). It is
copied into `tcmstudio/jobs/<id>/inputs/` in OPFS, hashed as it is written, and kept only when its SHA-256 is the one
the project recorded (otherwise the job fails as `integrity` and the copy is removed); a sample sheet's `path` cells
are resolved the same way and the sheet is rewritten to name the copies, as the runner's `_stage_sheet` does. The
files are sent nowhere (the model reads a job's status and summary, like any tool result). *analysis*: in one job worker holding numpy and scipy, the inputs mounted read-only, the pipeline
writes its outputs (stage progress comes from its own `stages.json`, through `runrecord.watching`); every file is
persisted to the job's `out/` and registered, the runner's required artefacts under the runner's names (RNA-seq:
`run`, `report`, `results`, `limitations`; single-cell: `run`, `report`, `clusters`, `cells`, `limitations`) and the
rest by path. The stage is checkpointed on its parameters, its inputs' digests and what computed it (the bundle's
SHA-256, Pyodide's version, the numpy and scipy the worker reports), so a job resumed on a changed runtime runs the
analysis again and its limitations name what computed its outputs. *verify*: in the same worker, with
the inputs still mounted where the run record says they were read, `verify_pipeline` runs the pipeline's own
`verify_run` (every input and output re-hashed, the stages complete, each table's columns and rows) and the kind's
`required_artefacts` and `validator:run` checks as the runner's job service makes them, under the runner's names; only
a pass gives device evidence `verified`.

**Limitations.** The job carries the run's own `limitations.md` lines, then "Computed in this browser with Pyodide's
numpy 2.4.6 and scipy 1.18.0; the native run is the reference. Not compared with it here: on two simulated references
the numbers matched within a stated tolerance (BROWSER_JOBS.md §3.1), so a value near a threshold (an adjusted p-value
near alpha) may differ.", the versions being those the worker reports ("version not reported" for one it does not
report), the line kept under the 320 characters of a limitation the model reads whole; and for single-cell "The UMAP
layout is a picture of the graph: it amplifies last-digit differences and is not compared with the native run's". No
tolerance is claimed for a person's own run: only the two references below were compared.

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
| identical apart from what is not compared | `report.html` with each inline figure set aside (no tolerance on its text) | `report.html` likewise; `cells.tsv`, every cell but `umap_1`, `umap_2` |
| within the tolerance below, not identical bytes | `deseq2_results.tsv`: 91 numeric cells differ, the largest using 0.133 of the bound (a `p_adjusted` of 0.205714 against 0.205717), the largest relative difference 1.46e-5; `de_diagnostics.tsv`: 61 cells, 0.083 of the bound (a gene-wise dispersion), relative 2.29e-5 | pseudobulk tables: B cell 20 cells, 0.034 of the bound (relative 8.74e-6); CD14+ monocyte 89, 0.074 (8.66e-6); NK cell 206, 0.082 (1.81e-5); Platelet 26, 0.047 (5.23e-6) |
| not compared, and said so | `plots/dispersion.svg`, coordinates printed at two or three digits: what it draws is compared in `de_diagnostics.tsv` and `deseq2_results.tsv`; `plots/sample_distances.svg`: the sample-to-sample distances it draws are in no table and are not compared (only its input, `vst.tsv`, is) | `cells.tsv` `umap_1`, `umap_2`, `plots/umap_clusters.svg`, `umap_samples.svg`, `paga.svg`: the UMAP layout, which amplifies last-bit differences (`paga.svg`'s edges are the connectivities compared in `paga.tsv`; its nodes sit at the clusters' UMAP medians) |
| compared exactly | the genes called (`p_adjusted` < 0.05): the same 90; `run.json`: the same 5 stages ending the same way, complete, the same 13 outputs | the genes called in each pseudobulk test: the same 16, 0, 0, 0; each cell's cluster and cell type, for all 1,077 cells in order; the clusters (NK cell 341, Platelet 294, B cell 230, CD14+ monocyte 212); `run.json`: the same 15 stages, complete, the same 18 outputs |
| the UMAP's structure instead of its numbers | — | the same cells in the same order, all coordinates finite, and the share of each cell's 15 nearest UMAP neighbours in its own cluster: browser 0.9985, native 0.9983 (required: at least 0.95 and within 0.01 of the native) |

The tolerance, for each numeric cell: |a − b| ≤ 1e-6 + 1e-4·max(|a|,|b|) + one unit in the sixth significant digit of
max(|a|,|b|) (the tables print `%.6g`). Every table that differed is a DESeq2 fit's (`deseq2_results.tsv`,
`de_diagnostics.tsv`, the four pseudobulk tables): the dispersion search, a grid then golden sections on a flat
likelihood, turns a last-bit difference between the native build of numpy, scipy and BLAS and Pyodide's wasm32 build
into a difference the six printed digits can show. Harmony's embedding is printed in no table, and what follows from
it (`clusters.tsv`, `paga.tsv`, `markers.tsv`, `cells.tsv` but the UMAP) was identical. Headers, gene and cell
order, NA positions, True/False flags and every text cell must be equal. A gene whose `p_adjusted` crosses alpha fails
the comparison, unless both values are within the tolerance of alpha, when it is named rather than hidden (none did).
Before the harness existed, the same comparison in Pyodide 314.0.7 under Node over three configurations of the two
references used at most 0.157 of this bound (a Platelet pseudobulk p-value), and the tighter 1e-7 + 1e-5·max was
exceeded 1.55-fold. The rule was fitted on these two simulated references; it is not established for other data.
Since the runs above, the harness gives each figure that is not the same bytes its own reason, naming the table that
holds what it draws only where one does (`plots/pca.svg`, `pca_variance.svg` and `sample_distances.svg` draw what no
table holds), and holds `report.html` to identical text with its figures set aside, with no tolerance on its text
(in both runs above it was identical so). One local run with these rules, from the bundle the limits below were
checked with, gave the same rows and known answers.

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
224 MiB. The commit's other workflows passed too: `ci` #322 (run 39286930329), every job, and `live-data` #21 (run
39286930354), whose browser 葛根芩连汤 comparison found every export identical (`run_manifest.json` apart from its
execution block).

**Where the limits come from.** Whole runs of the browser's entry (`tcmstudio.browser_jobs`: reading, analysis,
report) in Pyodide 314.0.7 under Node 22, not in a browser tab: the WebAssembly memory at its peak (MB of 10⁶ bytes),
one run each. Single cell, the built-in analysis with Harmony, on tables from `sc_world`'s generator widened to 33,000
genes (about 2,200 genes detected per cell, 6.7% of the entries non-zero; gzipped TSV, genes in rows). The first
column is the reader that parsed a table's whole text, the second the streaming one (**Reading the tables**, above),
from a bundle built from the working tree (rows marked * were run with its code patched into an earlier bundle, which
had no per-table limit):

| Input | Whole-text reader | Streaming reader |
|---|---|---|
| four tables of 1,356 cells × 33,000 genes (5,424 cells) | 2,125 MB, 322 s | 1,926 MB, 268 s |
| one table of 1,356 × 33,000 | 2,001 MB, 66 s | 1,550 MB, 51 s * |
| one table of 2,000 × 33,000 (66,000,000 counts) | 2,886 MB to the end of reading (the generated table repeated a barcode, so the run stopped there) | 2,165 MB, 99.5 s |
| one table of 2,712 × 33,000 (89,496,000 counts) | 3,914 MB, 178 s: it ran, at 91% of the ceiling (4 GiB, 4,295 MB) | 3,100 MB, 170 s *; now refused for memory in 2.5 s, before the analysis (113 MB) |
| four tables of 678 × 33,000 (2,712 cells) | 1,171 MB, 148 s | — |
| four tables of 1,356 cells × 2,000 genes, and × 5,000 (5,424 cells) | 1,055 MB, 92 s; 1,207 MB, 122 s | — |
| `sc_world` v1: four tables of 339 cells × 1,200 genes (1,356 cells) | 282 MB, 14.5 s | — |

Earlier, for the P2 commit: `sc_world` four times over (5,424 cells × 1,200 genes) took 37.4 s and 1.02 GB, with the
values of the native run; ten times over (13,568 cells) reached 4.24 GB, the wasm32 ceiling, and a probe allocating
numpy arrays met `MemoryError` at 4.23 GB. RNA-seq, `run_rnaseq_counts` with the built-in DESeq2 implementation on
negative-binomial counts:

| Genes × samples | Counts | Peak | Time |
|---|---|---|---|
| 60,000 × 50 | 3.0 M | 716 MB | 139 s |
| 60,000 × 100 | 6.0 M | 1,178 MB | 277 s |
| 30,000 × 200 | 6.0 M | 1,224 MB | 298 s |
| 100,000 × 100 | 10 M | 1,991 MB | 473 s |
| 50,000 × 200 | 10 M | 1,826 MB | 491 s |
| 100,000 × 200 (a reviewer's run) | 20 M | 3,612 MB, 84% of the ceiling | 973 s |

Earlier, for the P2 commit: 20,000 × 8, 163 MB and 7.4 s; 60,000 × 8, 282 MB and 21.8 s; 20,000 × 48, 282 MB and 38 s.

What bounds them. Single cell: the cells in all (13,568 cells of 1,200 genes reached the ceiling), and each table's
cells × genes, because the doublet scores (`bioagent.omics.sc.doublets.scrublet`) hold one sample's cells × genes
densely, and a table is one sample (one sheet row). Read a row at a time, one table of 2,712 × 33,000 still took 3.1
GB, too near the ceiling; 2,000 × 33,000 took 2.2 GB. Hence 6,000 cells, and 66,000,000 cells × genes in a table.
RNA-seq: memory and time grow with genes × samples; 10 M counts took 1.8–2.0 GB and about eight minutes, 20 M took 3.6
GB and 16 minutes. Hence 10,000,000 counts, with at most 100,000 genes and 200 samples. The limits before these (6,000
cells, counted alone; 100,000 genes × 200 samples, not run at that size when it was set) left too little room: with
the whole-text reader one table of 2,712 × 33,000, within 6,000 cells, took 3.9 GB, and the RNA-seq bound itself 3.6
GB and 973 s.

Checked with a bundle built from the working tree (one run each): the 2,712 × 33,000 table was refused for memory in
2.5 s, before the analysis, with the message quoted above; the 2,000 × 33,000 table and the four tables of 1,356 ×
33,000 ran (the rows above); 100,000 genes × 101 samples (10,100,000 counts) and 100,001 × 100 (10,000,100) were
refused for memory in 2.2 s each. (For the P2 commit, a 6,780-cell sheet was refused for memory in about 10 ms there.)

Not run: the largest single-cell input the limits allow at 33,000 genes (6,000 cells, three tables of 2,000). Not
measured: a browser tab's own memory limit, a low-memory device, a phone, Firefox and Safari. The memory refusals
of the page (a `MemoryError`, a worker's failed allocation, the worker retired) are tested with a scripted worker only:
no browser worker has run out of memory in a test.

### 3.2 ADMET

Two more of the runner's kinds run as browser jobs: `admet.models` (`bioagent admet --build-models`) and
`pipeline.admet` (`bioagent admet`). They are `bioagent.admet` itself, not another model: the same features (a
2048-bit Morgan count fingerprint, the 24 descriptors and the formal charge), the same scikit-learn
HistGradientBoosting models with the same settings, the same applicability domain, rules, alerts and outputs. Pyodide
314.0.7 has scikit-learn 1.8.0, whose training and predictions were bit-identical to native 1.8.0 on the same features
(measured in Pyodide under Node), but no RDKit (PyPI has no wasm32 wheel of it). So the page computes two parts itself and
hands them to Python: the **chemistry**, in a chemistry worker, with RDKit.js 2026.9.1 (npm `@rdkit/rdkit`'s
MinimalLib, the RDKit version `registry/scientific_environments.json` pins) and bioagent's ports of what RDKit.js
lacks, written as prepared sets (`prepared.json`, `features.f32`, `bits.u8`) that
`bioagent.admet.engine.PrecomputedEngine` reads back, held to their molecules by the SHA-256 of their SMILES; and the
**applicability-domain search**, on this device's kernels, written as similarities that
`bioagent.admet.engine.PrecomputedSimilarity` checks before it uses them. Everything else (splitting the archive,
training, predicting, the rules, the report and the checks) is bioagent's Python in a job worker, through
`tcmstudio.browser_jobs` `admet_split`, `admet_train`, `admet_read`, `run_admet` and `verify_pipeline`. Natively,
`bioagent.admet` takes the same seam (`RDKitEngine` by default): its outputs were unchanged byte for byte by it, on a
before/after run of the small archive (`run.json` and the cards gain `chemistry`, `refused` and
`dropped_unstandardisable`).

| Kind | What runs in the browser | Refused here, with the reason (the runner runs it) |
|---|---|---|
| `admet.models` | the TDC ADMET group archive (Harvard Dataverse), fetched by this browser through the site's dataset relay (registry group `admet`, the download held to bioagent's pinned SHA-256 `bd0005246cb6…`) or given as `archive`, a project file (its SHA-256 compared with the pin: another archive is still built, and the job and the cards say it is not the pinned one, so its held-out scores are not TDC's leaderboard's); each endpoint's `train_val` and `test` splits prepared by the chemistry worker; one model, domain and card per endpoint, trained by `bioagent.admet.models.train_endpoint` (the code and settings `build_models` uses); `endpoints` (default all 22), `seed`, `max_iter` (10–5000, default 600), `rebuild` | nothing as a whole; a training molecule the chemistry refuses is left out and counted (below). Offline, a build needs `archive` (the entry's `network_unless`) |
| `pipeline.admet` | `molecules`: a CSV/TSV (`name,smiles`) or `.smi` file of the project, or one SMILES; `endpoints` (default: every model built here); standardisation, the 24 descriptors, Lipinski, Veber, Egan and Ghose, the PAINS A/B/C, Brenk and NIH alerts, each endpoint's prediction with its domain flag, `admet.tsv`, `report.md`/`report.html`, the plot, `limitations.md`, `run.json` | an SDF, `.sdf.gz` or `.mol` file (RDKit's `SDMolSupplier` is not ported: need `runner`); more than 20,000 molecules (need `memory`) |

**Refusals.** Before anything is queued, `pipeline.admet` checks the molecules first (an SDF or `.mol` file: need
`runner`; a file the project lacks, an unknown parameter or a value outside the runner's bounds: `bad_arguments`; more
than **20,000 molecules**, counted a line at a time as Python counts them, before the file is read in full: need
`memory`, and Python refuses the same again in the worker), then the models (endpoints asked for whose models are not
built in this browser: need `dataset`, "Build them first in this browser with job.admet.models (…), or connect the
local runner"); an endpoint name TDC's group does not have is `bad_arguments`, naming the 22 it has (in both kinds). `admet.models` refuses an archive
that is not a zip, or that lacks an endpoint's member, naming the person's file rather than the browser's internal
copy. A model-build parameter outside its bounds, and an unknown one, are `bad_arguments`.

**Stages.** `admet.models`: *archive* (the dataset workspace's `admet` group through the relay, or `archive`, a
project file; its SHA-256 taken as it is copied and compared with the pin, the archive kept at
`tcmstudio/admet/archive/<sha12>.zip`) → *split* (Python
`admet_split`) → for each endpoint not already built with a matching card: *chemistry.<ep>* (the chemistry worker on
the `train_val` and `test` SMILES → prepared dirs) → *train.<ep>* (Python `admet_train`) → *verify*. The models live in
OPFS `tcmstudio/admet/models/` (`model_cards.json`, `<ep>.pkl`, `<ep>_domain.npy`, exactly what `build_models` writes),
shared by every ADMET job here. An endpoint whose model there was built from the same archive, settings, scikit-learn
and chemistry and still re-hashes to its card's digests is kept (both its stages reused); a resumed job also reuses an
endpoint's prepared sets while they match their checkpoint, so a build that stopped part way is picked up where it
left off. `pipeline.admet`: *inputs* (the project file → OPFS, digest) → *read* (Python `admet_read`) → *chemistry*
(the chemistry worker) → *domain* (the scheduler's Tanimoto per endpoint, query bits against the model's domain bits)
→ *analysis* (Python `run_admet` with `PrecomputedEngine` and `PrecomputedSimilarity`) → *verify* (Python
`verify_pipeline`).

**The chemistry engine, and its parity.** The chemistry worker loads RDKit.js from its exact npm version on jsDelivr
(or a local mirror): the loader is vendored (npm's `dist/RDKit_minimal.js`, 106,667 bytes, with one line appended so a
module worker can import it; a test holds its prefix to the npm file's SHA-256), and the wasm (7,379,879 B, SHA-256
`594ceaed…`) is fetched, its SHA-256 and size checked against the pin in `boot.json` before anything compiles it, and
cached by the service worker under its exact URL. RDKit.js's MinimalLib has no `MolStandardize`, no count fingerprint
and no `FilterCatalog`, so bioagent's ports stand in: standardisation (`LargestFragmentChooser`, the `Uncharger`, the
22 default `Normalizer` SMIRKS), the Morgan count fingerprint, `BalabanJ`, `BertzCT`, `qed`, the Gasteiger charges,
`NumValenceElectrons`, `FormalCharge`, and the alerts (PAINS A/B/C, Brenk, NIH: 765 `FilterCatalog` entries,
`filter_catalogs.json`, 0 unported). The parity fixture `studio/web/test/fixtures/chem_parity.json` (484 molecules:
drugs, natural products, salts, zwitterions, metals, the Normalizer stress set, sydnones) holds the engine to native RDKit
2026.9.1: on every molecule it does not refuse, the float32 feature row (the row the models read), the packed bits and
the alerts are identical, and every refusal is one the fixture expects (26 refused, 3 invalid SMILES with native's
text). On RDKit's NCI `first_5K` set (4,999 molecules, never tuned on), 4,851 were identical, 140 refused as predicted,
8 errored as native did, 0 silent mismatches. `qed` and `BertzCT` can differ by 1–2 units in the last place of float64
(V8's `Math.exp`/`Math.log` are not glibc's), and are equal as float32; every other descriptor is exact. The engine
**refuses** (never approximates) a molecule that would need `MetalDisconnector`, `Reionizer` or the `Uncharger`'s
canonical atom ranking, a `Normalizer` product RDKit cannot sanitise (native RDKit goes on with the unsanitised
molecule; molsidomine, a TDC training molecule, and other sydnones are standardised natively to a saturated ring with two
more hydrogens), a dative or zero bond, or more than 400 atoms (RDKit.js traps above about 550): the refusal
names the step, the prepared row carries `need: "runner"`, and the runner's own RDKit reads it. A C++ exception that
gets out of RDKit.js on any other molecule refuses that molecule too (`RDKit.js exception`), and the chemistry worker
starts a fresh module from the same checked bytes for the molecules after it (at most 20 in a batch); a trap stops the
job, since the module's memory is then not trusted. In a build, a refused
training molecule is left out and counted on the card (`dropped_unstandardisable`), so a model built in a browser can
differ from a runner's; the card names the chemistry it was built with, and `run_admet` reports a refused query molecule
as refused and why, not as "could not be read".

**The comparison, and its rules.** `science.mjs --mode admet` runs `admet.models` (the archive as a project file) then
`pipeline.admet` as jobs of the page's job service in headless Chromium 141 (Pyodide 314.0.7, RDKit.js 2026.9.1), and
compares every output with a native run (RDKit 2026.9.1, scikit-learn 1.8.0, one thread). It holds the browser to
exactness, not to a tolerance, with two named exceptions:

- A **model card** is compared field by field apart from what names the process that wrote the model, not the model:
  the model file's and the domain file's SHA-256 (a pickle's bytes depend on the process — wasm32 here, 64-bit
  natively — so the model file itself is not compared; the domain `.npy` bytes are compared as a row of their own), the
  training `seconds` and the chemistry engine's name. The native models are trained on the molecules the browser
  chemistry kept.
- A **prediction** is the model's raw output (the boosted trees' sum), compared exactly, passed through one elementary
  function: scipy's `expit` (whose `exp` is the C library's) for a classification, or numpy's `10 **` for a log10
  endpoint. Those last bits differ between libraries, so a final value may differ by **at most 4 units in the last
  place of float64**, each one named and never called identical; the raw output and the domain search are always
  identical. The **last-bit rule's cause, corrected:** the earlier note blamed glibc for the log10 endpoints, but on an
  AVX-512 machine (this one) numpy's `10 **` uses its own SIMD power, not glibc's `pow`; natively a log10 endpoint's
  last bit can therefore differ between an AVX-512 CPU and others (the vdss_lombardo value checked was glibc's in the
  browser, and native numpy's was the odd one out). Measured on the synthetic archive: at most 2 units, on 52 of 16,120
  test-set predictions, every raw output identical. On 100,000 values in [−4, 4] (Pyodide 314.0.7 under Node against
  native glibc and numpy 2.4.6 on this AVX-512 machine): Pyodide's power differed from glibc's `pow` on 65 (0.07%) and
  from numpy's AVX-512 power on 5,317 (5.3%), by 1 unit; its `exp` from glibc's on 72; `expit` on 34 (0.03%), by at
  most 2 units.

**The GPU domain search, and its evidence.** The domain search (batch Tanimoto max over packed 2048-bit Morgan bits:
per query, the nearest training molecule — smallest index on ties — and the integer intersection and union, Python
dividing in float64) runs on this device's kernels through `science-scheduler.js`. Under the person's acceleration
setting, it uses the GPU (WebGPU, WGSL) only where this device's own check (`device-check.js`) measured a physical
adapter exact and faster than the WebAssembly SIMD kernel at that size; otherwise WebAssembly SIMD (JavaScript without
SIMD). Every backend is held to the others and to Python's `np.bitwise_count`, a GPU result passes a CPU check
(`validateTanimoto`) before it is used, and Python checks it again (`PrecomputedSimilarity`: shapes, every
intersection ≤ min popcount, the union consistent, and the full exact search of a sample of queries); a result that
fails is not used, the CPU recomputes, the run records `similarity.rejected`, and the stage's GPU work is discarded so
it earns no evidence. **There is no physical GPU here or in CI.** With the harness's software-GPU switch forced on,
WebGPU gave results identical to WebAssembly SIMD on the SwiftShader software adapter, and the facts read "webgpu,
software adapter, validated, not accelerated": a software renderer is **never evidence of a GPU**, and `accelerated` is
never claimed from it. The acceptance criterion — the device check faster on a physical GPU — stays open ([§5](#5-what-this-does-not-do-yet),
[BROWSER_COMPUTE.md](BROWSER_COMPUTE.md)).

**Times and memory** (`science.mjs --mode admet`, headless Chromium 141, Pyodide 314.0.7, this machine; SwiftShader
timings are not evidence of a GPU). On the small known-answer archive `admet_world` (caco2_wang, hia_hou): every card
(but for the digests, seconds and chemistry), domain file, raw and final prediction, the alerts, `admet.tsv` and the
plot identical; the reports differ only by the one line naming the molecules the browser refused; caco2_wang MAE
0.1328 at 194 iterations, hia_hou AUROC 1.0 at 600; the expected refusals all refused, and the runner's own run read
them all (6 in the 36-molecule set of the first runs: MetalDisconnector, Reionizer, a 406-atom PEG; 8 in the
38-molecule set, which adds an Uncharger-ranking zwitterion and a dative bond; 9 in the final 39-molecule set, which
adds molsidomine, a `Normalizer` product RDKit cannot sanitise). `admet.models`
took 12–14 s in the browser against 5.0–5.8 s native, and `pipeline.admet` 1.0–2.0 s against 1.3–1.5 s (eight runs);
the worker held 155 MiB (a worker reused after the build reports the build's peak). The forced-software-GPU rerun gave identical outputs, its receipt `cpu+webgpu`. On a synthetic archive at the
TDC group's sizes (22 endpoints, 80,610 training molecules, none refused since synthetic): 118 comparison rows the
same, 11 within the last-bit rule (largest vdss_lombardo, 17 of 226 at 1 unit), 0 failing; `admet.models` 418–437 s in
the browser (chemistry about 210 s, training about 210 s, worker 961–967 MiB) against 443–447 s native at one thread.
Measured in Pyodide 314.0.7 under Node 22 instead (the Python side alone, with native RDKit's chemistry standing in for
RDKit.js; not in a browser tab): a model build at the largest endpoint's size (cyp2d6_veith, 10,504 training and 2,626
test molecules) peaked at 877 MB and took 28 s; a pipeline run against the 22 models reached 224 MB at 1,000 molecules,
625 MB at 10,000 and 1.03 GB at 20,000 (the WebAssembly memory's peak), 23 s with the page's similarities and 113 s when Python recomputes the domain,
`admet.tsv` the native run's bytes both ways — hence the 20,000-molecule limit.

**On the real TDC archive, in CI** (`live-data` #24 (run 39386727431, commit 0882e95, 2026-10-20), the live-data
workflow's `admet` job: headless Chromium 141 on GitHub's hosted ubuntu-latest runner, Pyodide 314.0.7 and RDKit.js
2026.9.1 from jsDelivr as on the site; natively Python 3.12.15, numpy 2.5.4, scipy 1.18.1, scikit-learn 1.8.0 and RDKit
2026.09.1, x86_64, one BLAS thread). The archive, `tdc_admet_group.zip` (1,472,501 bytes, the pinned SHA-256
`bd0005246cb6…`), was downloaded natively from Harvard Dataverse and given to the page as a project file: 22 endpoints,
81,809 molecules over their splits (`train_val` 65,430, `test` 16,379). `admet.models` built all 22 models as a browser
job, its receipt at its recorded digest (`outputs_verified`, device evidence `verified`) and its 45 output files read
back at their recorded digests; the browser chemistry, run again on every split, gave the job's own counts. It refused
202 of the 81,809 molecules (0.25%; 150 distinct SMILES), by step: `Reionizer` 96, `MetalDisconnector` 83, the
`Uncharger`'s canonical atom ranking 18, a `Normalizer` product RDKit cannot sanitise 5 (one each in bioavailability_ma,
bbb_martins, cyp2d6_veith, cyp3a4_veith and ld50_zhu); none was an `RDKit.js exception` (no module restart) and none was
refused for its size or a bond type. Per endpoint, refused of `train_val` + `test`: bioavailability_ma 2 of 640,
solubility_aqsoldb 99 of 9,982 (and 2 invalid SMILES not read, natively too), bbb_martins 8 of 2,030, vdss_lombardo 5 of
1,130, cyp2c9_veith 29 of 12,092, cyp2d6_veith 27 of 13,130, cyp3a4_veith 27 of 12,328, half_life_obach 1 of 667,
ld50_zhu 1 of 7,385, ames 3 of 7,278; the other 12 endpoints none. The native models were trained without the 150
refused SMILES, as the browser's were. The 22 cards were identical field by field apart from the model's and the domain
file's digests, the seconds and the chemistry (among them caco2_wang MAE 0.2882 on 182 test molecules, 728 trained, 477
iterations; hia_hou AUROC 0.9313; lipophilicity_astrazeneca MAE 0.541; solubility_aqsoldb MAE 0.7876 on 1,986, 7,895
trained, 2 unreadable and 99 refused dropped; cyp2d6_veith AUPRC 0.6803 on 2,619, 10,484 trained, 27 refused; ld50_zhu
MAE 0.6291; ames AUROC 0.8551; dili AUROC 0.8883), and the 22 domain files identical bytes; the model pickles were not
compared (their bytes depend on the process), what they predict was. On the 16,353 kept test molecules every raw model
output and every domain search was identical, and 36 final predictions differed by 1 unit in the last place of float64
(allowed: 4): 32 through `10 **` on the log10 endpoints (vdss_lombardo 9, half_life_obach 4, clearance_hepatocyte_az 5,
clearance_microsome_az 14) and 4 through `expit`'s `exp` (cyp2c9_veith 1, cyp2d6_veith 1, cyp3a4_veith 2); every other
prediction was identical, 15 endpoints' test-set predictions entirely so. `pipeline.admet` with these 22 models on the
39-molecule set predicted 29 molecules, refused the 9 expected, each at its step (molsidomine for its `Normalizer`
product), all 9 read by the runner's own run, and did not read 1 (`unclosed_ring`, as natively): `admet.tsv` and
`plots/classification.svg` were identical bytes; `limitations.md`, `report.md` and `report.html` identical but for the
line naming the refused molecules; `run.json`'s stages and models the same; the 35 alerts identical; every model's
predictions identical at full precision except half_life_obach's, clearance_hepatocyte_az's and clearance_microsome_az's
(3 molecules each, 1 unit in the last place, through `10 **`); `qed`'s float64 differed on 10 molecules by at most 2
units (equal as float32); and the domain search on WebAssembly SIMD was native numpy's for all 22 endpoints, re-checked
by Python. Rerun with WebGPU forced on by the harness's switch (SwiftShader, a software adapter), the outputs were
identical bytes and the 22 domain calls ran on WebGPU, each validated on the CPU and never accelerated, the receipt
`cpu+webgpu`: not evidence of a GPU. These full-precision predictions come from a probe that ran the browser's models on
the 23 prepared sets in a job worker (`admet_native.py`). In all, 118 comparison rows the same, 11 within the named
last-bit rules, 22 skipped (the model pickles), 0 failing; 49 page checks passed. Times and memory, one run on GitHub's
hosted runner (its core count not recorded; not a benchmark): `admet.models` took 1,000 s in the browser (chemistry
379 s, training 614 s over the 22 endpoints; the worker's WebAssembly memory peaked at 964 MiB) against 1,122 s for the
native `build_models` at one thread; `pipeline.admet` 2.3 s (chemistry 0.75 s, domain 0.32 s, analysis 0.88 s) against
3.1 s for the native command, and 3.0 s with the GPU forced (domain 1.35 s on SwiftShader); the step, download excluded,
about 44 minutes (07:29–08:13 UTC).

**What has run in CI, and what was not run.** Studio's CI build job runs `chem.browser.mjs`,
`gpu-science.mjs --software` and `science.mjs --mode admet` (on `admet_world` and the 39-molecule set) in one step, and
its test job the web and runner tests: they passed in `studio` #95 (run 39386727362, commit 0882e95, 2026-10-20); the
ADMET comparison first passed in CI in `studio` #93, on an earlier commit, and #95 ran it with the 39-molecule set. The
live-data workflow's `admet` job prints the archive download's redirect chain, then runs the same comparison on the real
TDC archive: it passed in `live-data` #24 (run 39386727431, commit 0882e95, 2026-10-20), above. The run before it,
`live-data` #23 (run 39379769004, commit 3fb65d7), failed: RDKit.js threw a C++ exception on training molecule 447,
molsidomine; commit 0882e95 refuses that molecule ("Normalizer unsanitisable product") and, on a C++ exception from any
other molecule, refuses just that molecule and restarts the module. That run's redirect chain showed Dataverse answering
with a 303 to a signed URL on `dvn-cloud-iqss.s3.amazonaws.com`, now the group's `redirect_hosts`. Not run: a physical
GPU (none here or in CI), so P3's acceptance criterion — verified execution on a physical GPU with measured
performance — is not met until the device check runs on real hardware (Settings → Compute → *Check this device*, or
`node studio/web/test/runtime/gpu-science.mjs --channel chrome --headed`); a browser tab on a person's machine (every
browser run above is headless Chromium, here or on CI's virtual machine); and the archive's download through the relay
in a browser (CI downloads it natively and gives it to the page as a project file; the relay's redirect host is listed, but
no relayed download has been run). ONNX Runtime Web is not used for these tree models and no WebGPU matmul/PCA kernel
was written, for the reasons in [BROWSER_COMPUTE.md](BROWSER_COMPUTE.md).

**Limitations.** A browser ADMET run carries bioagent's own `limitations.md` lines, the line naming any refused
molecules, one "Computed in this browser: chemistry RDKit.js 2026.09.1 + ports, models scikit-learn 1.8.0 built in
this browser from the TDC archive (`<sha12>`); similarity `<backend>`." (≤ 320 characters, the length the model reads
whole), and the predicted-not-measured line the runner adds; `provenance.models` carries the cards' digests.

### 3.3 Docking from prepared structures

Another of the runner's kinds runs as a browser job: `pipeline.dock` (`bioagent dock`), in a new **prepared-input
mode**. It takes structures already prepared for AutoDock Vina — a rigid-receptor PDBQT (`receptor`), a ligand PDBQT
(`ligands`: one ligand, or several as MODEL/ENDMDL blocks), and a site, either a `center` and a `size` or a box built
around a `reference_ligand` (the co-crystal ligand as a PDBQT in its crystal pose, which also turns on redocking
validation) — and runs them exactly as `bioagent dock RECEPTOR.pdbqt LIGANDS.pdbqt` runs natively: it is
`bioagent.docking.prepared` itself, the same plan, box, redocking check, analysis and outputs. The search is **AutoDock
Vina 1.2.7 compiled to WebAssembly** (not a Pyodide package: PyPI has no wasm32 wheel and the search is C++, not
Python), one Vina run per ligand in a dedicated Vina worker on this device's CPU, one thread a worker; the Python
stages run in a Pyodide job worker, as the other pipelines do. Raw structures still go to the runner: preparing a
receptor and ligands (Meeko, RDKit) is the next cycle (Milestone B, not done — [BROWSER_EXPANSION.md](BROWSER_EXPANSION.md)).

| Kind | What runs in the browser | Refused here, with the reason (the runner runs it) |
|---|---|---|
| `pipeline.dock` | a rigid-receptor PDBQT `receptor`, a ligand PDBQT `ligands` (one ligand or several MODEL blocks), and a site (`center` + `size`, or a box around `reference_ligand`, which is redocked to validate the setup); one Vina run per ligand in the WebAssembly build; the plan, the box, the redocking check, scoring with ligand efficiency, the contacts, `scores.tsv`, `report.md`/`report.html`, the plot, `limitations.md`, `run.json` | a receptor that is not a `.pdbqt` (a PDB file, `PDB:<id>[:chains]` or `UniProt:<accession>`) or ligands that are not `.pdbqt` (SMILES, CSV or SDF): preparing them needs Meeko and RDKit (need `runner`); `site_ligand` (a residue/ligand to find in the raw structure) or `allow_remote` (a fetch) present (need `runner`); more than 100 ligands, a receptor of more than 100,000 atoms, a ligand of more than 80 atoms, or a box of more than 216,000 Å³ (need `memory`) |

**Routing and refusals.** The router reads `pipeline.dock`'s arguments against the kind's
`execution.browser_job.runner_only` rules and sends the whole call to the runner when any apply, naming the reason,
before anything is queued: a receptor whose name is not a `.pdbqt` (`not_suffix`), ligands that are not `.pdbqt`
(`not_suffix`), `site_ligand` or `allow_remote` present (`present`). Browser mode refuses rather than falling back:
under compute **Browser** with the runner connected such a call is refused `compute_browser` with that reason and the
setting, and without a runner `needs_runner`. Arguments that mix the two modes (a `.pdbqt` receptor with SMILES, CSV or
SDF ligands, or a PDB receptor with `.pdbqt` ligands or a `reference_ligand`) are `bad_arguments`, as the runner's own
schema refuses them. The caps are measured and enforced on both sides — `DOCK_LIGANDS_MAX` = 100 ligands a run,
`DOCK_RECEPTOR_ATOMS_MAX` = 100,000 receptor atoms, `DOCK_LIGAND_ATOMS_MAX` = 80 ligand atoms, `DOCK_BOX_VOLUME_MAX` =
216,000 Å³ (a 60 Å cube) — and beyond them the job is refused with the reason and left to the runner, Python refusing
the same again in the worker.

**Stages.** *inputs* (each PDBQT a file of the project → OPFS, hashed as written, kept only at its recorded SHA-256) →
*read* (Python `dock_read`: the prepared inputs read and checked as `bioagent dock` reads them, the box computed, and
the per-ligand Vina plan written, `dock_plan.json`) → *dock* (one Vina run per ligand — and the reference ligand, when
redocking — each in a Vina worker; the runs made in the plan's order on several workers side by side) → *analysis*
(Python `run_dock`, `bioagent.docking.prepared` over the Vina results read back by `PrecomputedVina`, which holds each
to the receptor, ligand, parameters, box and wasm it asked for) → *verify* (Python `verify_pipeline`, docking's own
`verify_run` and the kind's `required_artefacts` under the runner's names). A Vina run cannot be interrupted mid-run,
so a cancel terminates every Vina worker at once and the runs already finished keep their checkpoints; a resumed job
reuses a run whose result still re-hashes to its checkpoint. Each Vina instance can reach about 2 GB, so the page runs
at most min(cores − 1, ⌊(deviceMemory − 1.5 GB) / 2.25 GB⌋, 4) Vina workers (2 on a 4-core, 8 GB machine; the 1.5 GB is
the job's own Pyodide worker, held throughout). Time grows with a ligand's atoms and the exhaustiveness, not the ligand
count alone (a fixture-sized ligand about 4–15 s single-threaded; 100 large ligands can take hours — a job resumes per
ligand).

**The Vina engine, and its reproducible build.** The engine is AutoDock Vina 1.2.7 compiled to WebAssembly with
Emscripten 4.0.23, vendored at `studio/web/js/scientific/dock/vendor/vina-1.2.7.wasm` (492,557 bytes, SHA-256
`94807d4aeb35…`, full `94807d4aeb358234fc4506ddd4c61b050d6b056fac8da412798eaf615ef09232`), pinned in `boot.json`
(`vina`), its SHA-256 checked before anything instantiates it, and cached by the service worker at that digest.
`studio/scripts/build_vina_wasm.py` builds it reproducibly from pinned, SHA-256-checked inputs (the Emscripten release
binaries, Vina's PyPI source distribution, Ubuntu 24.04's Boost 1.83 headers); `--check` rebuilds it and two
independent builds are byte-identical. The port layer `studio/vina-wasm/` replaces only system facilities, never Vina's
own code: a serial `parallel.h` (Vina seeds its Monte Carlo tasks in order before the run and merges their outputs in
task order, so the result does not depend on threads — native Vina gives identical poses at `cpu=1` and at every core
count), Boost thread/filesystem/log shims, and the embind binding `vina_wasm.cpp` over Vina's own class API.

**Parity: an exact tier and a search tier.** Vina's two deterministic operations are bit-identical to the native
`vina` 1.2.7 package: `score()` and `optimize()` match its C++ values to 0 units in the last place of float64, and
`optimize()`'s pose text is byte-identical, on all 6 fixture ligands. The search is not. **A seeded `dock` is not
bit-reproducible** — native Vina itself gives other poses for the same seed when only glibc's FMA maths paths are
turned off or a different maths library is linked, because the Monte Carlo search amplifies a last-bit difference (Vina
calls only `acos`, `exp`, `pow`, `sin`, `sincos` and `sqrt`). So docking is held to a measured band, not to exactness.
Over seeds 1–8 at exhaustiveness 8 the browser's median per-ligand best score sits inside native's own spread: median
|Δbest| 0.002–0.077 kcal/mol for the WebAssembly build, against 0.002–0.086 for native without FMA and 0.002–0.046 for
native linked against musl's maths; benzamidine redocks within 2 Å of the crystal pose on 8 of 8 seeds on every engine.
The acceptance rule is on the median, never a single seed: per ligand |median(best browser) − median(best native)| ≤
0.10 kcal/mol over seeds 1–8, and the crystal redocking succeeds (top pose ≤ 2 Å) on at least as many seeds in the
browser as natively. A single seed's gap can exceed that band (ibuprofen 0.151 kcal/mol at seed 42); the rule is on the
median over seeds, enforced in `studio/web/test/scientific/vina.test.mjs`'s fixture path and in `studio` CI
(`VINA_TIER_S=full`, 6 ligands × seeds 1–8).

**End to end** (`science.mjs --mode dock`, headless Chromium 141 with the pinned wasm and Pyodide against native Vina
1.2.7; one local run, native Python 3.12.3, numpy 2.5.4, vina 1.2.7, x86_64, glibc 2.39). The job ran the 6 fixture
ligands with benzamidine (BEN) as the reference ligand, redocked to validate the site:

- the plan: the box (centre −1.8555, 14.366, 16.748; size 18 × 18 × 18 Å, from the reference ligand) and the 8 PDBQT
  texts Vina is given are identical bytes to the native `dock_read`;
- the analysis stage: `run_dock` run natively (CPython) on the browser's own 7 Vina results, read back by
  `PrecomputedVina`, gives byte-identical `scores.tsv`, `report.md`, `report.html`, `limitations.md`, `plots/scores.svg`
  and every `poses/<ligand>.pdbqt` (the Python stages in Pyodide equal CPython);
- the energy model: each browser top pose re-scored by native Vina and by the WebAssembly engine is bit-identical;
- the search: both runs complete, all 6 ligands dock, none refused; BEN redocks 1.14 Å in the browser (score −6.193)
  and 1.92 Å natively (score −6.181), an atom-order RMSD (an upper bound of the symmetry-aware RMSD); per-ligand
  |Δbest| at seed 42 had a median of 0.019 and a largest of 0.151 kcal/mol, inside the band above.

**Receipt, times and memory.** The receipt is `outputs_verified`, device evidence `verified`, with the engine recorded
for the dock stage (AutoDock Vina 1.2.7, the wasm build, SHA-256 `94807d4aeb35…`) and compute facts `vina.dock` on
`vina-wasm` in 2 dedicated workers, no GPU, never accelerated. The dedicated Vina workers' WebAssembly memory is
recorded honestly as "not measured" (the job service does not read it). The browser job took 54.9 s (the dock stage
50.2 s, 7 Vina runs, 96 s of Vina in all across 2 workers).

**What has run, and what was not run.** `science.mjs --mode dock` end to end and `vina.test.mjs` (the exact and search
tiers) pass locally and in `studio` CI. Not run: a physical GPU is unchanged — nothing in docking uses the GPU (only
the ADMET applicability-domain search can, and P3's physical-GPU acceptance stays open, [§3.2](#32-admet)); a browser
tab on a person's machine (the runs above are headless Chromium); and the relay download of a large structure in a
browser (CI gives the inputs as project files). Receptor and ligand preparation in the browser (Meeko, RDKit),
interaction analysis and SDF poses (they need RDKit), and raw PDB/SMILES inputs are Milestone B, the next cycle, and
are not done ([BROWSER_EXPANSION.md](BROWSER_EXPANSION.md)).

**Limitations.** A browser docking run carries bioagent's own `limitations.md` lines — a docking score is the scoring
function's estimate, not a measured affinity; a pose is a hypothesis about how a molecule could sit in the site; a
setup whose redocking check fails makes no claim — and a line naming the engine and that the search is not
bit-reproducible: that it was docked in this browser with AutoDock Vina 1.2.7 compiled to WebAssembly (the pinned
SHA-256), whose `score`/`optimize` are bit-identical to native Vina while the Monte Carlo search is not reproducible
bit for bit across engines or CPUs and is held to the measured band of §3.3.

## 4. How it is checked

| Check | What it shows | Where | Status |
|---|---|---|---|
| Known answer (planted world, 200 permutations) | a job worker's five tables equal native CPython's committed bytes; the exports recompute. It covers the herb layer, NPASS activities, Reactome enrichment with its permutation null, the rebuttals and the exports; the planted world has no STRING, disease, CMAUP or LOTUS layer, so `network.tsv` and `disease_targets.tsv` are header only and the network topology is not exercised | `tcmstudio.browser_selftest`; `studio/e2e/science.spec.mjs`; Settings → Data → *Check this browser* | implemented and tested: identical bytes in headless Chromium 141 (Pyodide 314.0.7, wasm32), locally and in Studio's CI with Pyodide's pyarrow 22.0.0 from jsDelivr |
| The job service end to end | `research.run` and the self-test as jobs: stages, the research stage's checkpoint, a receipt at its recorded digest with `outputs_verified` and device evidence `verified`, the five tables the committed answer; a second run whose workers are stopped as its research stage ends fails, and resumed it reuses that stage from its checkpoint and gives the same tables | `studio/web/test/runtime/science.mjs --mode service` (Studio CI, build job) | implemented and tested in headless Chromium, locally and in Studio's CI with Pyodide's pyarrow 22.0.0 from jsDelivr (`studio` #88, 2026-10-19) |
| Snapshot build from files in OPFS | the parser tests' fixture files mounted read-only from OPFS (a write to one is refused, their bytes in OPFS unchanged) and built in a job worker; then imported through the dataset workspace and built by `research.snapshot` as a job, down to its snapshots, its marker and its receipt (`outputs_verified`). Both give the native build's six snapshot ids | `science.mjs --mode fixture` (Studio CI, build job) | implemented and tested in headless Chromium, locally and in Studio's CI (as above) |
| 葛根芩连汤 on the real files | `research.snapshot` + `research.run` as browser jobs, every export compared with the native run | live-data workflow, step "The same research in a browser" (`studio/web/test/runtime/science.mjs --mode gqd`) | implemented and tested: every export the native run's bytes in `live-data` #18, #19 and #21 (2026-10-19); repeated on every live-data run. Should a number ever differ in its last bit (glibc's and wasm's `math.exp` are not correctly rounded), the comparison reports that table, and the digests restating it, as within a relative 1e-12 — never as identical |
| RNA-seq and single-cell from count tables, against the native run | `pipeline.rnaseq` (count matrix) and `pipeline.scrna` (count tables) as jobs of the page's job service on `omics_counts_world` v1 and `sc_world` v1, read from a project's files: every output compared with the native command's on the same files, as identical bytes, within the stated tolerance (the largest difference 0.133 of it) or not compared, with the reason (the UMAP layout and figures; the layout's structure is checked instead); the genes called, each cell's cluster and cell type and the clusters exact; the receipts, with the inputs at the files' digests; the known answers on the browser's files ([§3.1](#31-rna-seq-and-single-cell-from-count-tables)) | `science.mjs --mode omics` (Studio CI, build job, from commit 65fa6aa) | implemented and tested in headless Chromium, locally (Chromium 141, Pyodide 314.0.7 from a local mirror) and in Studio's CI with Pyodide's numpy and scipy from jsDelivr (`studio` #91, 2026-10-19): every check passed |
| The omics jobs' refusals, inputs and memory | refused before anything is queued, with the reason: FASTQ, PyDESeq2, Scanpy, scVI, `.h5`, `.h5ad`, out-of-bounds or unknown parameters, `counts` with a FASTQ sample sheet (`bad_arguments`, in the runner's words), a file the project lacks (for a name that may be a Cell Ranger directory, saying the runner reads one); `run_pipeline` and `call_tool` arguments the runner's schema refuses, before any approval; under compute Browser with the runner connected, `compute_browser` with the arguments' reason; without the job service's module, the catalog's `runner_only` rules; a resumed analysis run again when what computed it changed; inputs copied from the project's files, a digest mismatch refused; a run with a scripted worker (artefact names, receipt inputs, the verify outcome); running out of memory a refusal that retires the worker. In Python: each function, run natively, writes the command's bytes (all but `run.json` and `stages.json`), the size limits (cells, a table's cells × genes, genes × samples) and a `MemoryError` are typed refusals before or instead of a result, a corrupt or cut-short gzip and a table that is not UTF-8 are refusals with the reason, `verify_pipeline` names the runner's checks; the streaming table reader gives the whole-text parse's matrix; the count-matrix mode's refusals and its reference; a count-matrix call naming a FASTQ engine is `bad_arguments` on the runner | `web/test/scientific/omics.test.mjs`, `web/test/router.test.mjs`, `web/test/catalog-page.test.mjs`, `web/test/runtime/pyboot.test.mjs`; `studio/runner/tests/test_browser_jobs.py`, `test_capability_state.py`; `BioScience-Harness/tests/test_omics_rnaseq_counts.py`, `test_omics_scrna.py`, `test_pipeline_references.py` | implemented and tested locally; the memory paths with a scripted worker only |
| ADMET (`admet.models`, `pipeline.admet`), against the native run | both as jobs of the page's job service, on the small known-answer archive `admet_world` (as a project file) and a 39-molecule set, and on the real TDC archive (all 22 endpoints, the archive a project file, then the 39-molecule set with its models), compared with the native command on the same molecules (RDKit 2026.9.1, scikit-learn 1.8.0, one thread): model cards (apart from the files' digests, seconds and chemistry), domain files and `admet.tsv` identical; the predictions at full precision (raw output and domain search exact, a final value within the named last-bit rule), the alerts, the stages; the reports differ only by the refused-molecules line; the expected refusals refused and read by the runner; then once more with WebGPU forced on the software adapter, the outputs identical ([§3.2](#32-admet)) | `science.mjs --mode admet` (Studio CI, build job; on the real archive, the live-data workflow's `admet` job) | implemented and tested in headless Chromium, locally (RDKit.js and Pyodide from local mirrors), in Studio's CI (`studio` #95 (run 39386727362, commit 0882e95, 2026-10-20)) and on the real TDC archive with RDKit.js and Pyodide from jsDelivr (`live-data` #24 (run 39386727431, commit 0882e95, 2026-10-20): 202 of 81,809 molecules refused at named steps; every card (as above), domain file, raw output and domain search identical; 36 of 16,353 test-set predictions 1 unit apart in the last place); never on a physical GPU |
| ADMET chemistry parity, the kernels and the device check | RDKit.js 2026.9.1 + ports against native RDKit on the parity fixture (484 molecules) and NCI first_5K (4,999): the float32 rows, bits and alerts identical, refusals as predicted; the Tanimoto kernels (JS, WASM SIMD, WebGPU) against each other and `np.bitwise_count`, exact; the device check's known-answer test, parity and verdicts, on SwiftShader (exact, "software, not evidence") | `web/test/scientific/chem.test.mjs`, `chem-worker.test.mjs`, `web/test/science-cpu.test.mjs`, `science-gpu.test.mjs`, `science-scheduler.test.mjs`, `science-calibration.test.mjs`, `scientific/evidence.test.mjs`; `chem.browser.mjs` and `gpu-science.mjs --software` in Chromium | implemented and tested locally and (`chem.browser.mjs`, `gpu-science.mjs --software`) in Studio's CI build job, passed in `studio` #95 (run 39386727362, commit 0882e95, 2026-10-20); never run on a hardware GPU |
| ADMET refusals, routing, receipts and the Python entry | `pipeline.admet` refused before queueing with the reason (SDF/`.mol`: runner; models not built here: dataset, hinting job.admet.models; more than 20,000 molecules: memory), the molecules checked before the models; the scheduler's per-call acceleration rule, serialised GPU calls, a discarded GPU result earning no evidence; the receipt's `device.used` `cpu+webgpu` and `compute` facts; in Python, each `browser_jobs` function run natively writing the command's bytes, the limit a typed refusal, a refused molecule said to be refused and why | `web/test/scientific/admet.test.mjs`, `job-manager.test.mjs`, `jobs-runtime.test.mjs`, `router.test.mjs`, `catalog-page.test.mjs`, `sw.test.mjs`; `studio/runner/tests/test_browser_jobs.py`, `test_catalog.py`, `test_capability_state.py`, `test_webbuild.py`, `test_compat_matrix.py`; `BioScience-Harness/tests/test_admet_engine.py`, `test_admet_build.py` | implemented and tested locally; the web and runner tests also in Studio's CI test job (passed in `studio` #95) |
| Docking (`pipeline.dock`, prepared mode), against the native run | as a job of the page's job service on the 3PTB fixture (6 ligands, benzamidine the reference ligand, seed 42), compared with native Vina 1.2.7 (`science.mjs --mode dock`): the plan (the box and the 8 PDBQT texts) identical bytes to native `dock_read`; `run_dock` run natively on the browser's own Vina results byte-identical (`scores.tsv`, the reports, `limitations.md`, `plots/scores.svg`, every `poses/<ligand>.pdbqt`); each top pose re-scored by native Vina and the WebAssembly engine bit-identical; all 6 ligands dock in both, BEN redocks 1.14 Å browser / 1.92 Å native, per-ligand \|Δbest\| at seed 42 median 0.019, largest 0.151 kcal/mol, inside the band; the receipt `outputs_verified`, `verified`, the engine and `vina.dock` compute facts, no GPU ([§3.3](#33-docking-from-prepared-structures)) | `science.mjs --mode dock` (`dock-plan.mjs`, native side `dock_native.py`; Studio CI, build job) | implemented and tested in headless Chromium 141, locally (one run) and in `studio` CI; never on a physical GPU |
| Vina's exact and search tiers | `score()` and `optimize()` bit-identical to the native `vina` 1.2.7 package (0 ulp float64, pose text byte-identical) on all 6 fixture ligands; a seeded `dock` held to the median band over seeds 1–8 (per-ligand \|median Δbest\| ≤ 0.10 kcal/mol, the redocking succeeding on at least as many seeds as natively), not to bit-reproducibility | `web/test/scientific/vina.test.mjs` (the exact tier always; the search tier `VINA_TIER_S=full`, 6 ligands × seeds 1–8, in `studio` CI) | implemented and tested in Node locally and in Studio's CI build job |
| Docking refusals, routing, receipts and the Vina build | `pipeline.dock` refused or routed before queueing with the reason (a non-`.pdbqt` receptor or ligands, `site_ligand`, `allow_remote`: runner; more than 100 ligands, 100,000 receptor atoms, 80 ligand atoms or a 216,000 Å³ box: memory; mixed-mode arguments: `bad_arguments`), the caps enforced in Python too; the dedicated-worker count and cancel/resume; the receipt's engine, `vina.dock` facts and "not measured" worker memory; the WebAssembly build reproducible from pinned inputs (`build_vina_wasm.py --check`: two builds byte-identical, the pin matched) | `web/test/scientific/dock.test.mjs`, `router.test.mjs`, `catalog-page.test.mjs`, `job-manager.test.mjs`, `jobs-runtime.test.mjs`; `studio/runner/tests/test_browser_dock.py`, `test_browser_jobs.py`, `test_catalog.py`, `test_compat_matrix.py`; `BioScience-Harness/tests/test_docking_prepared.py` | implemented and tested locally; the web and runner tests also in Studio's CI test job |
| In-process governed analysis | the same bytes and result digest as the isolated child natively | `BioScience-Harness/tests/test_research_inprocess.py` | implemented and tested |
| Data layer | downloads, resume, relay fallback, checksum refusal, import, cancel | `web/test/scientific/*.test.mjs` (an in-memory OPFS); the import runs in Chromium's OPFS in `--mode fixture` | implemented and tested |
| Job service and pool | queue, cancel, interruption and resume, receipts, worker crashes | `web/test/scientific/job-manager.test.mjs`, `worker-pool.test.mjs` | implemented and tested |
| Relay | registry-bound, ranges, caps, streaming, each host's pace | `studio/edge/test/datasets.test.js` (no real host, not the Workers runtime) | implemented and tested; not deployed; after a deployment a HEAD probe checks it, as a warning only |

## 5. What this does not do (yet)

- **Threads inside a computation, or jobs side by side.** Each job worker runs single-threaded WebAssembly, and the job
  service runs one job at a time in a tab (a snapshot build and a research run share the snapshot store), so the pool's
  further workers stay unused today. A per-source parallel snapshot build across workers is designed (NPASS, CMAUP, LOTUS and
  Reactome are independent) but not built; multithreaded WASM kernels (pthreads) are not written.
- **GPU or NPU for these jobs.** The research loop and the omics jobs are integer and float Python with exact outputs;
  no WebGPU kernel is used for them, and no WebNN model exists (BROWSER_COMPUTE.md §5). The ADMET jobs' domain search
  can use WebGPU, but only where this device's own check measured a physical adapter exact and faster; there is no
  physical GPU here or in CI, so it has only ever run on a software adapter (exact, never counted as acceleration), and
  P3's acceptance criterion — a physical GPU, measured — stays open ([§3.2](#32-admet), BROWSER_COMPUTE.md §6.1).
- **DuckDB-Wasm / Arrow.** Not used: the reference implementation is the runner's Python, and a second implementation
  of the joins would have to be shown equal to it. Pyodide's own `pyarrow` writes the snapshot store.
- **The rest of the pipelines.** RNA-seq from FASTQ (QC, trimming, quantification), PyDESeq2, Scanpy, scVI, 10x HDF5
  and `.h5ad` inputs stay on the runner; docking from raw structures (receptor and ligand preparation: Meeko, RDKit) and
  structure prediction have no browser engine yet, and so do an ADMET SDF or `.mol` file (RDKit's `SDMolSupplier` is not
  ported), the governed `predict-admet` skill run and the governed `dock-ligands` skill run. A call for them is refused
  in the browser with the reason, never run another way.
- **Bit-reproducible docking.** Vina's `score()` and `optimize()` are bit-identical to the native `vina` 1.2.7 package
  on the fixture ligands, but a seeded `dock` is **not** bit-reproducible: native Vina itself gives other poses for the
  same seed when its maths library changes, because the Monte Carlo search amplifies a last-bit difference. So docking
  is held to a measured band over seeds 1–8 (per ligand \|median(best browser) − median(best native)\| ≤ 0.10 kcal/mol,
  the crystal redocking succeeding on at least as many seeds as natively), not to byte-for-byte equality, and a single
  seed's gap can exceed that band (ibuprofen 0.151 kcal/mol at seed 42). The band was measured on the 3PTB fixture's
  6 ligands; it is not established for other receptors ([§3.3](#33-docking-from-prepared-structures)).
- **Docking preparation in the browser (Milestone B, not done).** Only prepared PDBQT structures dock in the browser.
  Preparing a receptor and ligands from raw inputs (a PDB/`PDB:`/`UniProt:` receptor, SMILES/CSV/SDF ligands, Meeko and
  RDKit), interaction analysis and SDF poses (they need RDKit), and the relay download of a large structure are the next
  cycle and are not implemented; those arguments are refused in the browser and left to the runner. Every docking run
  above is headless Chromium, here or on CI's virtual machine — not a tab on a person's machine, not a deployed site.
- **Byte-identical ADMET outputs.** The domain files and `admet.tsv` are the native run's bytes and the model cards
  the native ones field by field (not their model file's digest, seconds or chemistry name: a pickle's bytes depend on
  the process), and the raw model outputs and the domain search are exact, but a final prediction that passes through `expit` or `10 **` may
  differ from the native run by up to a few units in the last place of float64 (the C library's `exp`, or numpy's own
  AVX-512 power); each such value is named, never called identical ([§3.2](#32-admet)). `qed` and `BertzCT` can differ in
  the last bit of float64 (equal as float32, the precision the models read). A model built here can differ from a
  runner's when the browser chemistry refused training molecules the runner's RDKit reads (on the real TDC archive it
  refused 202 of the 81,809 molecules, 0.25%): the card counts them (`dropped_unstandardisable`), and the comparison
  trains the native reference on the same kept molecules. The comparison in a browser was run on a small known-answer
  archive, a synthetic one at the TDC sizes and, in CI, the real TDC archive (36 of 16,353 test-set predictions 1 unit
  apart in the last place; [§3.2](#32-admet)).
- **Byte-identical omics outputs.** The omics jobs are not the native bytes throughout: on the two references the DE
  and pseudobulk tables differ from the native run in their last printed digits (within the stated tolerance; a
  person's own run is compared with nothing), and the UMAP layout and five figures are not compared at all
  ([§3.1](#31-rna-seq-and-single-cell-from-count-tables)). The tolerance was fitted on two simulated references and is
  not established for other data; only the parameters of those references were compared in a browser (`n_top_genes`
  600, Harmony, seed 0; a pseudotime root, `integration: none`, `doublets: off` or a marker table of one's own were
  not).
- **Larger omics inputs.** More than 6,000 cells, a table of more than 66,000,000 cells × genes, or a count matrix of
  more than 10,000,000 genes × samples (100,000 genes and 200 samples at most) is refused for the runner. The limits
  come from runs in Pyodide 314.0.7 under Node, one run each, not in a browser tab; the largest single-cell input
  they allow at 33,000 genes (6,000 cells in three tables of 2,000) was not run; a tab's own memory limit, a
  low-memory device, a phone, Firefox and Safari are not measured, and no worker has run out of memory in a browser
  test.
- **10x HDF5.** Pyodide has h5py, and an `.h5` written and read back in Pyodide under Node gave the matrix digest
  `sc_world` records for that sample, but reading one through the job's file mount is untested, so `.h5` is refused in
  the browser for now.
- **Background work.** A browser job stops when its page closes; it is never presented as continuing.
- **Phones.** Storage quota and memory for 418 MB of files, and the omics jobs' memory, have not been measured on a phone.
- **Browsers.** Run in Chromium only. The page writes job records' files (receipts, checkpoints, the snapshot marker) with
  `createWritable`; a browser whose main thread lacks it (older Safari) refuses those writes, and the job fails rather
  than pretending to have kept them. Firefox and Safari have not been run.
- **Archives without a checksum.** The runner reads a `.gz` through its CRC before keeping it; the browser does not. Every
  `.gz` the registry lists has a reviewed checksum (STRING's aliases file too, pinned from two live downloads), so a
  truncated copy fails its checksum and nothing is kept; a `.gz` added without one would be kept as UNVERIFIED and fail
  later, when Python parses it.
- **Two tabs downloading the same file** do not corrupt it, but one finished download can be discarded and fetched again.

## 中文摘要

**浏览器科研任务**：网络药理学研究任务（`research.snapshot` 构建快照、`research.run` 十六阶段研究），（指南的 P2）
从计数矩阵出发的 RNA-seq 差异表达（`pipeline.rnaseq` 的计数矩阵模式）和从计数表出发的单细胞分析（`pipeline.scrna`），
（P3）ADMET 的模型构建（`admet.models`）与预测（`pipeline.admet`；见下文「ADMET（§3.2）」，2026-10-20 补入），
以及（P4）从已准备好的结构出发的对接（`pipeline.dock` 的「已准备输入」模式；见下文「对接（§3.3）」，2026-10-20 补入），
都可以完全在访客浏览器中运行，无需本机 Runner 或计算服务器。计算在"任务工作线程"中进行：每个线程是一个装有 Pyodide 314.0.7（编译为
WebAssembly 的 CPython 3.14）和经 SHA-256 校验的应用包的 Web Worker，运行与 Runner 完全相同的 `bioagent`/`psh` 代码；
线程池按设备大小设定（页面可让出的 CPU 核数，且每 1.5 GB 设备内存至多一个、总数至多四个），但任务服务在一个标签页中一次只运行一个任务、
用一个线程，因此目前同一时间只有一个线程在计算。17 个原始文件（418 MB）
保存在本站的私有文件系统（OPFS）中，Python 通过同步访问句柄就地只读读取；下载可直连上游、经本站边缘中转（流式、
不存储、按 bioagent 对各主机的节奏限速）或由用户导入，并在构建快照时按审核过的校验和再次核验。构建前按已审核的文件
大小检查剩余存储空间；两个没有审核大小的文件不做估计，任务日志会列出它们。浏览器 Worker 不能启动子进程，因此受治理的
分析在同一进程内经同一个 PSH 代理与审计链运行，产物如实记录 `isolation: "in_process"`。任务记录存于 IndexedDB，可取消；
页面关闭时任务中断，下次可从已核验的阶段继续。每个任务写出回执：运行位置、运行时、各线程的 WebAssembly 内存峰值、
每个输出（包括快照与账本）的两次独立哈希读取；运行了内核调用的阶段（ADMET 的适用域检索）另记其计算事实（`compute`，见下文 ADMET 段）。

**验证情况**：模拟世界（已知答案）在 Chromium 的任务工作线程中写出的五张表与原生 CPython 逐字节一致——本地和 Studio CI
（使用 jsDelivr 上 Pyodide 自带的 pyarrow 22.0.0）均已通过；它覆盖草药层、NPASS 活性、带置换零分布的 Reactome 富集、
反驳与导出，但没有 STRING、疾病、CMAUP、LOTUS 层，网络拓扑未被检验。任务服务端到端检查（检查点、回执、工作线程丢失后
从检查点续跑）与 OPFS 中夹具文件的快照构建（`research.snapshot` 作为任务运行，快照编号与原生一致）在无头 Chromium
中通过：本地及 Studio 的 CI（`studio` #88，2026-10-19，使用 jsDelivr 上 Pyodide 自带的 pyarrow 22.0.0）均已通过。葛根芩连汤真实数据在 live-data 流程中以浏览器任务运行并与原生运行逐个
导出文件比对：已在 `live-data` #18、#19 与 #21 中通过（运行 39251758718、39258060948 与 39286930354，提交 43918ac、53bdbb0 与 65fa6aa，2026-10-19，GitHub 运行器上的无头 Chromium，Pyodide 314.0.7 及其 pyarrow 22.0.0）：17 个原始文件导入本站私有存储，`research.snapshot` 与 `research.run` 作为浏览器任务成功运行，每个导出文件与同一批文件上的原生运行逐字节一致（五张表、`provenance.json`、`limitations.md`、`report.html`；`run_manifest.json` 除执行块外一致）。该次运行：4 味药材、1,792 个成分、370 个实测人类靶点、237 个节点 624 条边的 STRING 网络、检验 959 条 Reactome 通路，BH 校正后无一显著（因此真实数据上未抽取置换零分布；模拟世界检验了它）。中转已实现并有单元测试，尚未部署。

**RNA-seq 与单细胞（计数表，§3.1）**：在浏览器中运行的是 bioagent 的同一套代码（`bioagent rnaseq --counts`、
`bioagent scrna`），只用 Pyodide 的 numpy 与 scipy（首次加载约 16.2 MiB；Pyodide 未能取得的包会以「不可用」拒绝，不会被记为已加载）。RNA-seq：计数表（基因 × 样本的整数表，
TSV 或 CSV，可 gzip；`gene_id`、可选 `gene_name`、每个样本一列）加样本表（`sample` 列与设计变量），内置 DESeq2 实现、
VST、PCA、样本距离、图与报告。单细胞：计数表（CSV、TSV 或 TXT，可 gzip；基因为行）或由它们组成的样本表（`.csv`/`.tsv`，
含 `sample`、`path`），可选标记基因表；内置分析、内置 Harmony 或不整合、内置伪批量检验，`n_top_genes` 可设。以下情况在
排队前即被拒绝并说明原因，由 Runner 运行：从 FASTQ 出发的运行（没有 `counts`）、
PyDESeq2、Scanpy、scVI、`.h5ad`、10x HDF5（`.h5`：Pyodide 有 h5py，但经任务文件挂载读取尚未测试，暂不支持）、
Cell Ranger 目录（Runner 在其所在的机器上读取，`bioagent scrna <目录>`；目录不可能是项目文件，因此一个既不是项目文件
也不是计数表的名称作为参数错误拒绝，拒绝说明会指出 Runner 读取 Cell Ranger 目录）。超出浏览器工作线程容量的输入
（wasm32 每个工作线程约 4 GB 上限）在分析读入全部数据之前即以「需要内存」拒绝：单细胞总计超过 6,000 个细胞，或单个表
的细胞数 × 基因数超过 66,000,000（如 2,000 个细胞 × 33,000 个基因；一个表即一个样本），每个表逐行计数（表头给出细胞，
其余非空行给出基因）；RNA-seq 计数矩阵超过 10,000,000 个计数（基因 × 样本；且基因至多 100,000 个、样本至多 200 个），
由计数表的行计得。拒绝说明给出所计的数，例如「t.tsv.gz: 2,712 cells x 33,000 genes (89,496,000 counts) exceed the
66,000,000 counts of one table a browser worker can hold (wasm32, 4 GB): run it on the runner」或「100,000 genes x 101
samples (10,100,000 counts) exceed the 10,000,000 counts (genes x samples; at most 100,000 genes and 200 samples) a
browser worker can hold (wasm32, 4 GB): run it on the runner」（见下文「限值的来源」）。运行中任何地方出现
`MemoryError`，以及工作线程自身的内存分配失败，也是同样的拒绝，既不是运行时错误，也不会悄悄改用别的方法；该工作线程
随即退役（其内存从不缩小）。超出 Runner 界限或未知的参数、与 `counts` 同时给出的 FASTQ 选项或作为 `samples`
的 FASTQ 样本表（按 Runner 的原话拒绝：「samples: with counts this is the sample table (sample and the design columns);
a FASTQ sample sheet runs without counts」）、`.txt` 样本表、项目中没有的文件都作为参数错误拒绝；任何引擎都读不了的
输入按原生命令的方式拒绝并说明原因（need 为空），不会报告为崩溃：gzip 数据流损坏或被截断的表（「… cannot be read to
its end (…)」）、不是 UTF-8 的单细胞表。可用存储空间按输入大小的三倍检查。单细胞计数表的读取器
（`bioagent.omics.sc.io.read_table`，原生运行亦然）逐行读取，只保留每行的非零计数，因此一个表所需的内存取决于其计数而非
文本；得到的矩阵与整体解析相同（在 `sc_world` 的表、一个 33,000 基因的表和一个 2,000 × 33,000 的表上，CSV 与 TSV、gzip、
CRLF、带引号的单元格、BOM、NaN，逐项核对结构、数值、类型、细胞、基因 ID 与名称；另有
`BioScience-Harness/tests/test_omics_scrna.py`）。节省有限（单个 1,356 × 33,000 的表从 2.0 GB 降到 1.55 GB）：读入之后
占主导的是双细胞评分的稠密矩阵。空行现在被跳过（此前引发 `IndexError`），长度不对的行以「rows have N values for M
cells」拒绝（此前是归在「a value is not a number」之下的 numpy 报错）；gzip 损坏或被截断的 Cell Ranger 目录在 Runner
上同样拒绝并说明原因。流程调用（`run_pipeline {pipeline, arguments}` 或 `call_tool job.pipeline.rnaseq|scrna`）若参数
只有 Runner 能运行，会交给已连接的 Runner，否则在排队之前即以 `needs_runner` 拒绝并说明原因；
计算目标设为「浏览器」而 Runner 已连接时，以 `compute_browser` 拒绝，并给出该原因与这一设置。Runner 的参数模式会拒绝的
调用（`run_pipeline` 缺少 `pipeline` 或所写的不是目录中的流程（拒绝说明列出可用流程）、`arguments` 不是对象（对象的 JSON
文本按该对象读取，与调度器一致）、把任务参数写在 `arguments` 之外、`call_tool` 的参数不是对象）在放置和请求批准之前
即作为参数错误拒绝。不带调用参数查看某个任务类型时（目录页、`catalog_search`），它在其任务服务运行的地方即为就绪；
目录页把停止的任务服务标为本浏览器的，而不是 Runner 的。若任务服务的模块未能加载，路由器按目录中的
`execution.browser_job.runner_only` 规则判断哪些参数需要 Runner。输入是用户项目中的文件：按 ID 或文件名（同名取最新）找到，复制到该任务的 OPFS 目录，写入时计算
哈希，与项目记录的 SHA-256 不一致即拒绝；样本表中的路径同样解析并改写为副本；每个输入记入任务及其回执（`inputs`）。
这些文件不会被发送到任何地方（模型读到的是任务的状态与摘要，与其他工具结果一样）。分析在一个任务工作线程中运行，输出全部持久化，并在同一线程中由 `verify_pipeline` 按 Runner 的检查
（流程自身的 `verify_run`、`required_artefacts`、`validator:run`）复核。分析阶段的检查点涵盖其参数、输入摘要以及计算它的
运行时（应用包的 SHA-256、Pyodide 版本、工作线程报告的 numpy 与 scipy），运行时变了的任务续跑时会重新分析，其局限说明
写明输出由什么计算。局限说明附上一行（英文原文）：「Computed in this browser with Pyodide's numpy 2.4.6 and scipy
1.18.0; the native run is the reference. Not compared with it here: on two simulated references the numbers matched
within a stated tolerance (BROWSER_JOBS.md §3.1), so a value near a threshold (an adjusted p-value near alpha) may
differ.」即：数值由本浏览器中 Pyodide 的 numpy 与 scipy 计算（版本取自工作线程的报告，未报告时写明），原生运行是参考；
本次运行没有与之比较，只有两个模拟参考上的数值在给定容差内一致，因此接近阈值的值（接近 alpha 的校正 p 值）可能不同。
这一行不超过模型完整读取的 320 个字符。对用户自己的运行不声称任何容差：只比对过下文的两个参考。单细胞另注明 UMAP
布局不与原生比较。

**它们如何与原生运行比对**（`science.mjs --mode omics`）：在两个已知答案参考上（`omics_counts_world` v1：2,000 个基因
× 6 个样本，上调、下调各预设 40 个四倍差异基因；`sc_world` v1 写成四个 gzip 的单样本 TSV 加样本表），原生命令以参考清单的
参数、单个 BLAS 线程运行；在无头 Chromium 141 中，同一批文件作为项目文件，以浏览器任务提交，逐个读回输出并比对。
RNA-seq：`counts.tsv`、`vst.tsv`、`report.md`、`limitations.md` 与四张图逐字节一致；`report.html` 除内嵌图外一致（其文字不设容差）；
`deseq2_results.tsv`（91 个数值格不同，最大用到容差界的 0.133，最大相对差 1.46e-5）与 `de_diagnostics.tsv`（61 格，
0.083，2.29e-5）在容差内；离散度图与样本距离图不比较（图中坐标只印两三位）：离散度图所画数值在 `de_diagnostics.tsv` 与
`deseq2_results.tsv` 中已比较，样本距离图所画的样本间距离不在任何表中，未比较（只比较了其输入 `vst.tsv`）；显著基因
（`p_adjusted` < 0.05）同为 90 个。单细胞（1,077 个细胞）：`clusters.tsv`、`markers.tsv`、`paga.tsv`、`report.md`、
`limitations.md` 与四张图逐字节一致；`cells.tsv` 除 `umap_1`、`umap_2` 外每格一致；四个伪批量表在容差内（最大 0.082，
最大相对差 1.81e-5）；各伪批量检验的显著基因同为 16、0、0、0 个；全部 1,077 个细胞的聚类与细胞类型、聚类表完全相同；
UMAP 坐标与三张 UMAP 图不比较（`paga.svg` 的边是 `paga.tsv` 中已比较的连接度，其节点位于各簇 UMAP 坐标的中位数），改查结构：每个细胞 15 个最近 UMAP 邻居中同簇的比例，浏览器 0.9985、原生 0.9983（要求
至少 0.95，且与原生相差不超过 0.01）。容差为：|a − b| ≤ 1e-6 + 1e-4·max(|a|,|b|) + max(|a|,|b|) 第六位有效数字的一个
单位（表格按 `%.6g` 输出）。不同的表都是 DESeq2 拟合的输出（`deseq2_results.tsv`、`de_diagnostics.tsv` 与四个伪批量表）：
离散度搜索把原生构建与 Pyodide 的 wasm32 构建之间末位的差异放大到打印出的六位数字；Harmony 的嵌入不出现在任何表中，由它
得出的 `clusters.tsv`、`paga.tsv`、`markers.tsv` 与 `cells.tsv`（UMAP 除外）完全相同。表头、基因与细胞顺序、NA 位置、
True/False 标记和文字必须完全相同；跨越 alpha 的基因判为失败，
除非两个值都在 alpha 的容差内，此时点名报告（本次没有）。此前在 Node 下的 Pyodide 中对三种配置做的同样比较，最大用到
界的 0.157（一个血小板伪批量 p 值），而更严的 1e-7 + 1e-5·max 被超出 1.55 倍。这一容差是在两个模拟参考上拟合的，对其他数据未经确立。
此后比对工具对每张不逐字节一致的图给出各自的原因（只在确有表格包含其所画数值时指明该表；`plots/pca.svg`、`pca_variance.svg`
与 `sample_distances.svg` 所画的数值不在任何表中），并要求 `report.html` 去掉内嵌图后文字完全相同，不设容差（上述两次运行
中均如此）。用这些规则在本地运行过一次（所用应用包与下文核对限值的相同），比对结果与已知答案与上面相同。已知答案在浏览器写出的
文件上成立（原生文件亦然）：RNA-seq 上调 40/40、下调 40/40（各至少 38），方向错误 0，90 个显著中假发现 10 个（至多 15），
检验 1,999 个基因；单细胞聚类 ARI 1.0000（887 个细胞），四种细胞类型，B 细胞伪批量中 20 个预设基因找到 16 个（至少 12），
其他类型各 0 个（至多 1），批次混合度 1.0253（大于 0.8）。两个回执都在其记录的摘要处，`outputs_verified`，设备证据
`verified`。在提交 65fa6aa 上的一次本地运行（本机，无头 Chromium 141，Pyodide 来自经摘要核对的本地镜像）：RNA-seq
任务 8.1 秒（分析阶段 3.2 秒，此前工作线程启动 2.0 秒），工作线程 WebAssembly 内存 130 MiB，原生命令单线程 1.09 秒；
单细胞任务 9.5 秒（分析阶段 9.4 秒），同一工作线程内存 224 MiB（自启动以来的峰值，含之前的 RNA-seq 任务），原生 5.33 秒。
本地五次运行全部通过（提交前四次、提交时一次）。Studio 的 CI 构建作业自这一提交起运行同一检查（使用 jsDelivr 上 Pyodide 的 numpy 与 scipy）：已在 `studio` #91（运行 39286931542，提交 65fa6aa，2026-10-19）中通过，在 GitHub 运行器上的无头 Chromium 中，对照 CI 上 Python 3.12.15、numpy 2.5.4、scipy 1.18.1 的单线程原生运行，比对结果与上面相同（原生 UMAP 同簇比例 0.9981），两个任务分别用时 8.2 秒与 10.5 秒（原生 1.26 秒与 5.09 秒），内存 130 与 224 MiB。同一提交的其他工作流也已通过：`ci` #322（运行 39286930329，全部作业）与 `live-data` #21（运行
39286930354，其浏览器葛根芩连汤比对中每个导出文件逐字节一致，`run_manifest.json` 除执行块外一致）。

**限值的来源**：在 Node 22 下的 Pyodide 314.0.7 中完整运行浏览器入口（`tcmstudio.browser_jobs`：读取、分析、报告），不是在
浏览器标签页中，记录 WebAssembly 内存的峰值（MB 按 10⁶ 字节计），每项一次。单细胞（内置分析与 Harmony；`sc_world` 的生成器
扩展到 33,000 个基因，每个细胞约检出 2,200 个基因，6.7% 的条目非零，gzip 的 TSV，基因为行），整体解析文本的读取器对逐行
读取器（逐行读取器的数字来自用工作区代码构建的应用包，标 * 的来自打入旧应用包的同一代码，当时尚无单表限值）：四个
1,356 × 33,000 的表（5,424 个细胞）2,125 MB、322 秒对 1,926 MB、268 秒；单个 1,356 × 33,000 的表 2,001 MB、66 秒对
1,550 MB、51 秒 *；单个 2,000 × 33,000 的表（66,000,000 个计数）2,886 MB（读完为止：生成的表有重复条码，运行在此停止）
对 2,165 MB、99.5 秒；单个 2,712 × 33,000 的表（89,496,000 个计数）3,914 MB、178 秒（运行成功，达到上限 4 GiB 即 4,295 MB 的 91%）对 3,100 MB、170 秒 *，现在会在分析之前 2.5 秒内以
「需要内存」拒绝（113 MB）；四个 678 × 33,000 的表（2,712 个细胞）1,171 MB、148 秒；四个 1,356 个细胞 × 2,000 与 × 5,000
个基因的表 1,055 MB、92 秒与 1,207 MB、122 秒；`sc_world` v1（四个 339 × 1,200 的表，1,356 个细胞）282 MB、14.5 秒。P2 提交时
测得：`sc_world` 放大四倍（5,424 个细胞 × 1,200 个基因）用 37.4 秒、1.02 GB，数值与原生运行相同；放大十倍（13,568 个
细胞）达到 4.24 GB 的 wasm32 上限，一个分配 numpy 数组的探针在 4.23 GB 处遇到 `MemoryError`。RNA-seq（`run_rnaseq_counts`，内置 DESeq2 实现，负二项计数）：60,000 × 50（3.0 M 个计数）716 MB、139 秒；
60,000 × 100（6.0 M）1,178 MB、277 秒；30,000 × 200（6.0 M）1,224 MB、298 秒；100,000 × 100（10 M）1,991 MB、473 秒；
50,000 × 200（10 M）1,826 MB、491 秒；100,000 × 200（20 M，评审者的运行）3,612 MB（上限的 84%）、973 秒；P2 提交时测得
20,000 × 8 用 163 MB、7.4 秒，60,000 × 8 用 282 MB、21.8 秒，20,000 × 48 用 282 MB、38 秒。限值由此而来：单细胞受细胞总数
（1,200 个基因的 13,568 个细胞已达上限）以及单个表的细胞 × 基因约束，因为双细胞评分（`bioagent.omics.sc.doublets.scrublet`）以稠密方式
保存一个样本的细胞 × 基因，而一个表即一个样本（样本表的一行）；即使逐行读取，2,712 × 33,000 的单个表仍用到 3.1 GB，离上限
太近，2,000 × 33,000 用 2.2 GB，故为 6,000 个细胞、单表 66,000,000 个细胞 × 基因。RNA-seq 的内存与时间随基因 × 样本增长：
10 M 个计数用 1.8–2.0 GB、约八分钟，20 M 用 3.6 GB、16 分钟，故为 10,000,000 个计数，且至多 100,000 个基因、200 个样本。
此前的限值（只计细胞的 6,000 个细胞；设定时未在该规模运行过的 100,000 个基因 × 200 个样本）余量太小：用整体解析的读取器，
6,000 个细胞以内的单个 2,712 × 33,000 的表用到 3.9 GB，RNA-seq 的那一界限本身用到 3.6 GB、973 秒。用工作区代码构建的
应用包核对（每项一次）：2,712 × 33,000 的表在分析之前 2.5 秒内以「需要内存」拒绝；2,000 × 33,000 的表与四个
1,356 × 33,000 的表运行成功（即上面的数字）；100,000 个基因 × 101 个样本（10,100,000 个计数）与 100,001 × 100
（10,000,100）各在 2.2 秒内以「需要内存」拒绝。（P2 提交时，一个 6,780 个细胞的样本表同样在 Node 下的 Pyodide 中约 10 毫秒即以
「需要内存」拒绝。）未运行：限值在 33,000 个基因时允许的最大单细胞输入（6,000 个细胞，三个 2,000 个细胞的表）。
未测量：浏览器标签页自身的内存上限、低内存设备、手机、Firefox 与
Safari；页面的内存拒绝路径只用脚本化的工作线程测试过，没有任何浏览器工作线程在测试中真正耗尽内存。

**ADMET（§3.2）**：已实现，已在本地与 CI 中测试：Studio 的 CI 构建作业在小的已知答案归档与 39 个分子的集合上运行比对（`studio` #95（运行
39386727362，提交 0882e95，2026-10-20）），live-data 的 `admet` 作业在真实 TDC 归档上运行（`live-data` #24（运行 39386727431，提交 0882e95，
2026-10-20），GitHub 运行器上的无头 Chromium 141，Pyodide 314.0.7 与 RDKit.js 2026.9.1 来自 jsDelivr；归档 1,472,501 字节、SHA-256
为锁定值，在 CI 中以原生方式下载，作为项目文件交给页面）：浏览器化学拒绝了 81,809 个分子中的 202 个（0.25%），每个都注明被拒的步骤，
原生模型与浏览器的模型一样，训练时不含这些分子；22 张模型卡（摘要、秒数与化学除外）与适用域文件完全相同；16,353 个保留的测试分子上，
每个模型原始输出与适用域检索都相同，36 个最终预测在 float64 末位相差 1 个单位（经 `10 **` 或 `expit`）；`pipeline.admet` 用这些模型写出的
`admet.tsv` 与原生运行逐字节一致（见下文「真实 TDC 归档上的比对」）。尚未在实体 GPU 上运行（P3 的验收标准未满足），
也未在用户自己机器上的标签页中运行，归档也尚未经中转下载。Runner 的另两个任务类型也作为浏览器任务运行——
`admet.models`（`bioagent admet --build-models`）与 `pipeline.admet`（`bioagent admet`）。它们就是 `bioagent.admet` 本身，而非另一个模型：
相同的特征（2048 位 Morgan 计数指纹、24 个描述符与形式电荷）、相同设置的 scikit-learn HistGradientBoosting 模型、相同的适用域、规则、
结构警示与输出。Pyodide 314.0.7 有 scikit-learn 1.8.0（在相同特征上，其训练与预测与原生 1.8.0 逐位一致，在 Node 下的 Pyodide 中测得；
ADMET 任务加载 numpy、scipy 与 scikit-learn 1.8.0，后者带入 joblib 与 threadpoolctl，均来自同一 Pyodide 发行版），但没有 RDKit（PyPI 上没有它的
wasm32 wheel），因此页面自己算两部分并交给 Python：**化学**在化学工作线程（`js/scientific/chem/chem.worker.js`：RDKit.js，WebAssembly，CPU，
不用 Pyodide）中用 RDKit.js 2026.9.1（npm `@rdkit/rdkit` 的 MinimalLib，`registry/scientific_environments.json` 为 rdkit 锁定的版本）加
bioagent 对其所缺之处的移植，写成预备集（`prepared.json`、`features.f32`、`bits.u8`）交 `bioagent.admet.engine.PrecomputedEngine` 读回，并按其
SMILES 的 SHA-256 与分子对应；**适用域检索**在本设备的内核上进行（`js/compute/science-scheduler.js`），写成相似度交
`bioagent.admet.engine.PrecomputedSimilarity` 在使用前核验。其余（拆分归档、训练、预测、规则、报告与各项检查）都是 bioagent 的 Python 在任务
工作线程里运行（`tcmstudio.browser_jobs` 的 `admet_split`、`admet_train`、`admet_read`、`run_admet`、`verify_pipeline`）。原生运行走同一接缝
（默认 `RDKitEngine`），其输出未因此改变：小归档的前后对比逐字节一致（`run.json` 与模型卡新增 `chemistry`、`refused`、
`dropped_unstandardisable`）。两个任务的 `execution.browser_job` 另写明化学（`rdkit.js` 2026.9.1）与分子上限（`limits.molecules`）；
`run_pipeline` 与 `call_tool job.pipeline.admet` 的路由与上文组学流程相同（只有 Runner 能运行的参数交给已连接的 Runner，否则在排队前以
`needs_runner` 拒绝；计算目标为「浏览器」而 Runner 已连接时以 `compute_browser` 拒绝）。其余任务类型（`pipeline.fold`、
`skill.run`、`tcmdb.*`、`env.provision`）以及包装同一 ADMET 流程的受治理 Skill `predict-admet`、包装对接的受治理 Skill
`dock-ligands` 留在 Runner 上。`pipeline.dock` 不再完全只在 Runner 上：其「已准备输入」参数在浏览器中运行（见下文「对接（§3.3）」），
只有从原始结构出发的参数（PDB/`PDB:`/`UniProt:` 受体、SMILES/CSV/SDF 配体、`site_ligand`、`allow_remote`）才交给 Runner。

**两个任务的参数与拒绝**：`admet.models` 读取 TDC ADMET 基准组归档（Harvard Dataverse），由本
浏览器经本站数据集中转（登记组 `admet`，下载须与 bioagent 锁定的 SHA-256 `bd0005246cb6…` 一致）下载，或作为项目文件 `archive` 给出（其 SHA-256 与锁定值比较：别的归档仍会构建，任务与模型卡注明它不是锁定的归档，其留出集成绩因而不能与 TDC 排行榜比较）；化学工作线程准备每个终点的 `train_val` 与 `test` 拆分；每个终点由
`bioagent.admet.models.train_endpoint`（即 `build_models` 所用的代码与设置）训练一个模型、适用域与模型卡；参数为 `endpoints`（默认全部 22 个）、
`seed`、`max_iter`（10–5000，默认 600）、`rebuild`。它不会整体被拒：化学拒绝的训练分子被剔除并计数（见下）；离线时构建需要 `archive`（条目的
`network_unless`）。不是 zip 的归档、缺少某终点成员的归档被拒绝，拒绝说明点名用户的文件，而不是浏览器内部的副本；超出界限或未知的构建参数作为参数
错误（`bad_arguments`）拒绝。`pipeline.admet` 的 `molecules` 是项目中的 CSV/TSV（`name,smiles`）或 `.smi` 文件，或单个 SMILES；`endpoints`
默认为本浏览器已构建的全部模型；输出标准化结果、24 个描述符、Lipinski/Veber/Egan/Ghose 规则、PAINS A/B/C、Brenk 与 NIH 结构警示、各终点的预测
及其适用域标记、`admet.tsv`、`report.md`/`report.html`、图、`limitations.md` 与 `run.json`。排队之前先查分子：SDF、`.sdf.gz` 或 `.mol` 文件
（RDKit 的 `SDMolSupplier` 未移植，need `runner`）；项目中没有的文件、未知参数或超出 Runner 界限的值（`bad_arguments`）；超过 **20,000 个分子**
（need `memory`；在完整读入文件之前按 Python 的计法逐行计数，Python 在工作线程中也会同样拒绝）；再查模型：所请求终点的模型未在本浏览器构建（need
`dataset`：「Build them first in this browser with job.admet.models (…), or connect the local runner」），TDC 基准组没有的终点名作为参数错误（`bad_arguments`）拒绝，并列出它有的 22 个（两个任务都如此）。

**阶段**：`admet.models`：*archive*（经中转的数据集工作区 `admet` 组，或项目文件 `archive`；复制时计算 SHA-256 并与锁定值比较，归档保存在
`tcmstudio/admet/archive/<sha12>.zip`）→ *split*（Python `admet_split`）→ 对每个尚未以匹配模型卡构建的终点：*chemistry.<ep>*（化学工作线程
处理 `train_val` 与 `test` 的 SMILES → 预备目录）→ *train.<ep>*（Python `admet_train`）→ *verify*。模型保存在 OPFS `tcmstudio/admet/models/`
（`model_cards.json`、`<ep>.pkl`、`<ep>_domain.npy`，与 `build_models` 写出的完全相同），供本浏览器的每个 ADMET 任务共用，不分发任何训练好的模型。
某终点的模型若由相同的归档、设置、scikit-learn 与化学构建，且仍能按其模型卡的摘要重新哈希一致，则保留（它的两个阶段都复用）；续跑的任务在某终点的
预备集与检查点一致时也复用它们，因此中途停止的构建从停下处继续。`pipeline.admet`：*inputs*（项目文件 → OPFS，摘要）→ *read*（Python
`admet_read`）→ *chemistry*（化学工作线程）→ *domain*（调度器按终点计算 Tanimoto，查询位对模型的适用域位）→ *analysis*（Python `run_admet`，
用 `PrecomputedEngine` 与 `PrecomputedSimilarity`）→ *verify*（Python `verify_pipeline`）。

**数据**：数据集登记的第二组 `admet` 只列一个文件，即 ADMET 模型构建读取的 TDC ADMET 基准组归档：URL 与主机（dataverse.harvard.edu）、bioagent
锁定的 SHA-256（`bd0005246cb6…`）与锁定归档的大小（1,472,501 字节）、许可 `LicenseRef-TDC-per-dataset` 且再分发情况未知（此处没有任何许可
记录读过 TDC 的条款）、允许中转，以及一个重定向主机 `dvn-cloud-iqss.s3.amazonaws.com`：Dataverse 以 303 指向其 S3 存储上的签名 URL（在 live-data
工作流中见到，2026-10-20，归档从那里取得，SHA-256 与锁定值相同）。尚未在浏览器中运行过经中转的下载：
CI 中（`live-data` #24）归档以原生方式下载，作为项目文件交给页面。

**化学引擎及其一致性**：化学工作线程从 jsDelivr 按精确的 npm 版本（或本地镜像）加载 RDKit.js：加载器随站点提供（npm 的
`dist/RDKit_minimal.js`，106,667 字节，末尾加一行以便模块工作线程导入；有测试核对其前缀与 npm 文件的 SHA-256），wasm（7,379,879 字节，SHA-256
`594ceaed…`）下载后先按 `boot.json` 中的锁定值核对 SHA-256 与大小再编译，并由 Service Worker 按其精确 URL 缓存。RDKit.js 的 MinimalLib 没有
`MolStandardize`、计数指纹和 `FilterCatalog`，由 bioagent 的移植代替：标准化（`LargestFragmentChooser`、`Uncharger`、22 条默认 `Normalizer`
SMIRKS）、Morgan 计数指纹、`BalabanJ`、`BertzCT`、`qed`、Gasteiger 电荷、`NumValenceElectrons`、`FormalCharge`，以及结构警示（PAINS A/B/C、
Brenk、NIH：765 条 `FilterCatalog` 条目，`filter_catalogs.json`，0 条未移植）。一致性夹具 `studio/web/test/fixtures/chem_parity.json`（484 个
分子：药物、天然产物、盐、两性离子、金属、Normalizer 压力集、悉尼酮类）把引擎与原生 RDKit 2026.9.1 对齐：在每个未被拒绝的分子上，float32 特征行（模型读取
的行）、打包的位与结构警示完全相同，每次拒绝都在夹具预期之中（26 个被拒绝，3 个无效 SMILES，报错文字与原生相同）。在 RDKit 的 NCI `first_5K`
集（4,999 个分子，从未用于调校）上：4,851 个完全相同，140 个如预期被拒绝，8 个与原生一样报错，0 个静默不一致。`qed` 与 `BertzCT` 的 float64 值
可能在末位相差 1–2 个单位（V8 的 `Math.exp`/`Math.log` 不是 glibc 的），作为 float32 相等；其他描述符都完全一致。引擎**拒绝**（绝不近似）需要
`MetalDisconnector`、`Reionizer` 或 `Uncharger` 规范原子排序、某条 `Normalizer` 变换的产物无法被 RDKit 消毒（原生 RDKit 带着未消毒的分子继续；
TDC 训练分子吗多明及其他悉尼酮类在原生中被标准化为多两个氢的饱和环）、含配位键或零键、或原子数超过 400（RDKit.js 在约 550 个原子以上会触发
WebAssembly 陷阱）的分子：拒绝说明指出所在步骤，预备行带 `need: "runner"`，由 Runner 自己的 RDKit 读取。RDKit.js 在其他任何分子上抛出未捕获的
C++ 异常时，该分子同样被拒绝（`RDKit.js exception`），化学工作线程用同一份已校验的字节启动新模块处理其后的分子（每批至多 20 次）；陷阱则终止任务，
因为此时模块的内存已不可信。构建时被拒绝的训练分子被剔除并计入模型卡
（`dropped_unstandardisable`），故浏览器构建的模型可能与 Runner 的不同；模型卡注明构建所用的化学，`run_admet` 把被拒的查询分子如实报告为「被拒绝」
及原因，而非「无法读取」。

**比对及其规则**：`science.mjs --mode admet` 在无头 Chromium 141（Pyodide 314.0.7、RDKit.js 2026.9.1）中，以页面任务服务的任务先运行
`admet.models`（归档作为项目文件）再运行 `pipeline.admet`，并把每个输出与原生运行（RDKit 2026.9.1、scikit-learn 1.8.0、单线程）比对。它要求
完全一致，不设容差，只有两个点名的例外：**模型卡**逐字段比对，只略去标识写出模型的进程而非模型本身的字段——模型文件与适用域文件的 SHA-256（pickle
的字节取决于进程：此处为 wasm32，原生为 64 位，因此模型文件本身不比对；适用域 `.npy` 的字节单列一行比对）、训练 `seconds` 与化学引擎名称；原生
模型用浏览器化学保留的分子训练。**预测**是模型的原始输出（提升树之和，完全一致地比对）经过一个初等函数：分类终点为 scipy 的 `expit`（其 `exp`
来自 C 库），log10 终点为 numpy 的 `10 **`。这些末位在不同库之间不同，因此最终值可能在 float64 末位相差**至多 4 个单位**，逐个点名，绝不称作
一致；原始输出与适用域检索始终完全一致。**末位规则的成因（已更正）**：此前把 log10 终点的末位差异归咎于 glibc，但在 AVX-512 机器（本机）上
numpy 的 `10 **` 用其自带的 SIMD 幂运算而非 glibc 的 `pow`，因此原生 log10 终点的末位本身就会因 CPU 是否有 AVX-512 而不同（核查的 vdss_lombardo 值在浏览器中与
glibc 的一致，与众不同的是原生 numpy 的值）。在合成归档上实测：至多 2 个单位，16,120 个测试集预测中 52 个，所有原始输出一致。在 [−4, 4] 内的 100,000 个值上（Node 下的 Pyodide 314.0.7 对照
本机原生的 glibc 与 AVX-512 上的 numpy 2.4.6）：Pyodide 的幂运算与 glibc 的 `pow` 不同者 65 个（0.07%），与 numpy 的 AVX-512 幂运算不同者 5,317 个（5.3%），都差 1 个单位；其 `exp` 与 glibc 的不同者 72 个；`expit` 34 个（0.03%），
至多 2 个单位。

**GPU 适用域检索及其证据**：适用域检索（打包 2048 位 Morgan 位上的批量 Tanimoto 最大值：每个查询的最近训练分子——平局取最小下标——及整数交集与
并集，由 Python 以 float64 相除）经 `science-scheduler.js` 在本设备的内核上运行。在用户的加速设置下，只有本设备自身的检查（`device-check.js`）测得
某实体适配器在该规模既精确又比 WebAssembly SIMD 内核更快时才用 GPU（WebGPU，WGSL），否则用 WebAssembly SIMD（不支持 SIMD 时用 JavaScript）。每个
后端都与其他后端及 Python 的 `np.bitwise_count` 对齐；GPU 结果先通过 CPU 核验（`validateTanimoto`）才被使用，Python 再核验一次
（`PrecomputedSimilarity`：形状、每个交集不超过两者中较小的位数、并集自洽，以及抽样查询的完整精确检索）；不合格的结果不用，由 CPU 重算，运行记录
`similarity.rejected`，该阶段的 GPU 工作作废，不计任何证据。**本机与 CI 都没有实体 GPU**：用测试工具的软件 GPU 开关强制运行时，WebGPU 在
SwiftShader 软件适配器上给出与 WebAssembly SIMD 相同的结果，事实记为「webgpu、软件适配器、已核验、未加速」——软件渲染器**绝不是 GPU 的证据**，
绝不据此声称 `accelerated`。P3 的验收标准（设备检查在实体 GPU 上测得更快）仍未满足（BROWSER_COMPUTE.md）。

**回执**：运行了内核调用的阶段（ADMET 适用域检索）带上这些调用的计算事实，回执增加 `compute`（按阶段：每次调用的后端、GPU 的适配器与证据等级、
所引用的校准、`accelerated` 及其依据）；某阶段用了 GPU 结果时设备为 `cpu+webgpu`，且只有该阶段的每次 GPU 调用都达到该等级时，证据才为
`accelerated`。Python 随后拒绝的 GPU 结果标为作废，不计任何东西。

**时间与内存**（`science.mjs --mode admet`，本机的无头 Chromium 141，Pyodide 314.0.7 与 RDKit.js 来自本地镜像；SwiftShader 的计时不是 GPU 的
证据）。小的已知答案归档 `admet_world`（caco2_wang、hia_hou）上：每张模型卡（除摘要、秒数与化学外）、适用域文件、原始与最终预测、结构警示、
`admet.tsv` 与图完全相同；报告只差点名浏览器所拒分子的那一行；caco2_wang MAE 0.1328（194 次迭代），hia_hou AUROC 1.0（600 次）；预期的拒绝全部
被拒绝，Runner 自己的运行全部读取了它们（最初几次运行用 36 个分子的集合，拒绝 6 个：MetalDisconnector、Reionizer、一个 406 个原子的 PEG；随后
38 个分子的集合拒绝 8 个，另加一个 Uncharger 排序的两性离子与一个配位键分子；最终 39 个分子的集合拒绝 9 个，另加吗多明，其 `Normalizer` 产物
无法被 RDKit 消毒）。`admet.models` 浏览器 12–14 秒、原生 5.0–5.8 秒；`pipeline.admet`
1.0–2.0 秒对 1.3–1.5 秒（八次运行）；工作线程 155 MiB（构建之后复用的工作线程报告的是构建时的峰值）。强制软件 GPU 的重跑输出相同，回执为 `cpu+webgpu`。TDC 规模的合成归档（22 个终点、80,610 个训练分子，
因是合成数据而无一被拒）上：118 个比对行相同，11 个在末位规则内（最大者 vdss_lombardo，226 个中 17 个差 1 个单位），0 个失败；`admet.models`
浏览器 418–437 秒（化学约 210 秒、训练约 210 秒，工作线程 961–967 MiB）对原生单线程 443–447 秒。以下改在 Node 22 下的 Pyodide 314.0.7 中测得
（只是 Python 一侧，以原生 RDKit 的化学代替 RDKit.js，不在浏览器标签页中）：最大终点规模的模型构建（cyp2d6_veith，10,504 个训练与 2,626 个测试
分子）峰值 877 MB、28 秒；对 22 个模型的流程运行在 1,000 个分子时达 224 MB、10,000 个 625 MB、20,000 个 1.03 GB（WebAssembly 内存的峰值），20,000 个分子用页面相似度 23 秒，Python 重算
适用域 113 秒，两种方式的 `admet.tsv` 都是原生运行的字节——故设 20,000 个分子的上限。

**真实 TDC 归档上的比对（CI）**（`live-data` #24（运行 39386727431，提交 0882e95，2026-10-20），live-data 工作流的 `admet` 作业：GitHub
托管的 ubuntu-latest 运行器上的无头 Chromium 141，Pyodide 314.0.7 与 RDKit.js 2026.9.1 与站点一样来自 jsDelivr；原生一侧为 Python 3.12.15、
numpy 2.5.4、scipy 1.18.1、scikit-learn 1.8.0 与 RDKit 2026.09.1，x86_64，单个 BLAS 线程）。归档 `tdc_admet_group.zip`（1,472,501 字节，
SHA-256 为锁定值 `bd0005246cb6…`）以原生方式从 Harvard Dataverse 下载，作为项目文件交给页面：22 个终点，各终点拆分共 81,809 个分子
（`train_val` 65,430 个，`test` 16,379 个）。`admet.models` 作为浏览器任务构建了全部 22 个模型，回执在其记录的摘要处（`outputs_verified`，
设备证据 `verified`），45 个输出文件都在记录的摘要处读回；浏览器化学在每个拆分上重跑，得到与任务自身相同的计数。它拒绝了 81,809 个分子中的
202 个（0.25%，150 个不同的 SMILES），按步骤：`Reionizer` 96 个、`MetalDisconnector` 83 个、`Uncharger` 规范原子排序 18 个、RDKit 无法消毒的
`Normalizer` 产物 5 个（bioavailability_ma、bbb_martins、cyp2d6_veith、cyp3a4_veith 与 ld50_zhu 各 1 个）；没有一个是 `RDKit.js exception`
（无需重启模块），也没有因大小或键型被拒的。按终点（被拒数 / `train_val` + `test` 的分子数）：bioavailability_ma 2/640，solubility_aqsoldb
99/9,982（另有 2 个无效 SMILES 未读入，原生亦然），bbb_martins 8/2,030，vdss_lombardo 5/1,130，cyp2c9_veith 29/12,092，cyp2d6_veith
27/13,130，cyp3a4_veith 27/12,328，half_life_obach 1/667，ld50_zhu 1/7,385，ames 3/7,278，其余 12 个终点为 0。原生模型与浏览器的一样，
训练时不含这 150 个被拒的 SMILES。22 张模型卡除模型与适用域文件的摘要、秒数与化学外逐字段相同（其中 caco2_wang MAE 0.2882，182 个测试分子、
728 个训练分子、477 次迭代；hia_hou AUROC 0.9313；lipophilicity_astrazeneca MAE 0.541；solubility_aqsoldb MAE 0.7876，1,986 个测试分子、
7,895 个训练分子，剔除 2 个无法读取与 99 个被拒的分子；cyp2d6_veith AUPRC 0.6803，2,619 个测试分子、10,484 个训练分子、27 个被拒；ld50_zhu
MAE 0.6291；ames AUROC 0.8551；dili AUROC 0.8883），22 个适用域文件逐字节相同；模型 pickle 不比对（其字节取决于进程），比对的是它们的预测。
在 16,353 个保留的测试分子上，每个模型原始输出与每次适用域检索都相同，36 个最终预测在 float64 末位相差 1 个单位（允许至多 4 个单位）：32 个
经 log10 终点的 `10 **`（vdss_lombardo 9 个、half_life_obach 4 个、clearance_hepatocyte_az 5 个、clearance_microsome_az 14 个），4 个经 `expit` 的
`exp`（cyp2c9_veith 1 个、cyp2d6_veith 1 个、cyp3a4_veith 2 个）；其余预测全部相同，其中 15 个终点的测试集预测完全相同。`pipeline.admet`
用这 22 个模型处理 39 个分子的集合：预测 29 个，如预期拒绝 9 个，各在其步骤（吗多明因其 `Normalizer` 产物），Runner 自己的运行全部读取了这 9
个，1 个未读入（`unclosed_ring`，原生亦然）；`admet.tsv` 与 `plots/classification.svg` 逐字节相同，`limitations.md`、`report.md` 与
`report.html` 只差点名被拒分子的那一行，`run.json` 的阶段与模型相同，35 条结构警示相同；除 half_life_obach、clearance_hepatocyte_az 与
clearance_microsome_az（各 3 个分子，经 `10 **` 末位差 1 个单位）外，每个模型的预测在全精度下都相同；`qed` 的 float64 值在 10 个分子上至多差
2 个单位（作为 float32 相等）；WebAssembly SIMD 上的适用域检索在全部 22 个终点上与原生 numpy 相同，并由 Python 复核。用测试工具的开关强制
WebGPU（SwiftShader，软件适配器）重跑：输出逐字节相同，22 次适用域调用在 WebGPU 上运行，每次都经 CPU 核验，从未计为加速，回执为
`cpu+webgpu`——这不是 GPU 的证据。这些全精度预测由一个探针在任务工作线程中用浏览器的模型对 23 个预备集算出（`admet_native.py`）。合计：118
个比对行相同，11 个在点名的末位规则内，22 个跳过（模型 pickle），0 个失败；49 项页面检查通过。时间与内存（GitHub 托管运行器上的一次运行，
未记录其核数，不是基准测试）：`admet.models` 浏览器 1,000 秒（化学 379 秒，22 个终点的训练 614 秒；工作线程 WebAssembly 内存峰值 964 MiB），
原生单线程 `build_models` 1,122 秒；`pipeline.admet` 浏览器 2.3 秒（化学 0.75 秒、适用域 0.32 秒、分析 0.88 秒），原生命令 3.1 秒，强制 GPU
时 3.0 秒（SwiftShader 上适用域 1.35 秒）；整个步骤（不含下载）约 44 分钟（UTC 07:29–08:13）。

**如何检查与尚未运行**：化学一致性（一致性夹具与 NCI first_5K）、各 Tanimoto 内核（JS、WASM SIMD、WebGPU，彼此及与 `np.bitwise_count` 对照，
完全一致）与设备检查（已知答案测试、一致性与判定，在 SwiftShader 上：精确，「软件，不是证据」）、拒绝、路由、
调度器的逐次调用加速规则与串行的 GPU 调用、作废的 GPU 结果不计证据、回执与 Python 入口的测试都已在本地通过。Studio CI
的构建作业在同一步骤中运行 `chem.browser.mjs`、`gpu-science.mjs --software` 与 `science.mjs --mode admet`（`admet_world` 与 39
个分子的集合），其测试作业运行网页与 Runner 的测试，都已在 `studio` #95（运行 39386727362，提交 0882e95，2026-10-20）中通过（ADMET 比对在 CI
中首次通过是更早提交上的 `studio` #93；#95 用 39 个分子的集合运行）。live-data 的 `admet` 作业先打印归档下载的重定向链，再在真实 TDC
归档上运行同一比对：已在 `live-data` #24（运行 39386727431，提交 0882e95，2026-10-20）中通过（见上文）。此前的 `live-data` #23（运行
39379769004，提交 3fb65d7）失败：RDKit.js 在第 447 个训练分子吗多明上抛出 C++ 异常；提交 0882e95 以「Normalizer unsanitisable product」
拒绝该分子，其他任何分子上的 C++ 异常也只拒绝该分子并重启模块。该次运行的重定向链显示 Dataverse 以 303 指向
`dvn-cloud-iqss.s3.amazonaws.com` 上的签名 URL，该主机现已列入该组的 `redirect_hosts`。尚未运行：实体 GPU（本机与 CI 都没有）——P3 的验收标准
（在实体 GPU 上经验证地执行并测得性能）在设备检查于真实硬件上运行之前（设置 → 计算 → 「检查本设备」，或
`node studio/web/test/runtime/gpu-science.mjs --channel chrome --headed`）不算满足；用户自己机器上的浏览器标签页（以上的浏览器运行
都是无头 Chromium，在本机或 CI 的虚拟机上）；以及在浏览器中经本站中转下载归档（CI 以原生方式下载归档并作为项目文件交给页面；中转的重定向主机已列入，
但尚未运行过经中转的下载）。这些树模型不使用 ONNX Runtime Web，也没有写 WebGPU 的矩阵乘法/PCA 内核，原因见 BROWSER_COMPUTE.md。

**局限说明**：浏览器中的 ADMET 运行带有 bioagent 自己的 `limitations.md` 各行、点名被拒分子的一行、一行「Computed in this browser: chemistry
RDKit.js 2026.09.1 + ports, models scikit-learn 1.8.0 built in this browser from the TDC archive (`<sha12>`); similarity `<backend>`.」（不超过模型
完整读取的 320 个字符），以及 Runner 加上的「预测不等于实测」一行；`provenance.models` 记录各模型卡的摘要。

**对接（§3.3）**：Runner 的又一个任务类型作为浏览器任务运行——`pipeline.dock`（`bioagent dock`）的新**「已准备输入」模式**。它接受已经为
AutoDock Vina 准备好的结构：一个刚性受体 PDBQT（`receptor`）、一个配体 PDBQT（`ligands`：单个配体，或多个 MODEL/ENDMDL 块），以及一个
位点——`center` 加 `size`，或围绕 `reference_ligand`（晶体位姿下作为 PDBQT 的共晶配体，同时开启再对接验证）构建的盒子——并与原生
`bioagent dock RECEPTOR.pdbqt LIGANDS.pdbqt` 完全一样地运行：它就是 `bioagent.docking.prepared` 本身，相同的计划、盒子、再对接检验、
分析与输出。搜索由**编译为 WebAssembly 的 AutoDock Vina 1.2.7**（不是 Pyodide 包：PyPI 没有它的 wasm32 wheel，搜索是 C++ 而非 Python）
承担，每个配体一次 Vina 运行，在本设备 CPU 上专用的 Vina 工作线程中进行，每线程一个线程；Python 阶段与其他流程一样在 Pyodide 任务工作线程
中运行。从原始结构出发仍交给 Runner：准备受体与配体（Meeko、RDKit）是下一轮（里程碑 B，尚未完成——BROWSER_EXPANSION.md）。

**参数与路由/拒绝**：排队之前，路由器按任务类型的 `execution.browser_job.runner_only` 规则读 `pipeline.dock` 的参数，命中任一条就把整个调用
交给 Runner 并说明原因：受体名不是 `.pdbqt`（PDB 文件、`PDB:<id>[:chains]` 或 `UniProt:<accession>`，`not_suffix`）、配体不是 `.pdbqt`
（SMILES、CSV 或 SDF，`not_suffix`）、出现 `site_ligand` 或 `allow_remote`（`present`）——这些都需要 Meeko 与 RDKit 的准备（need
`runner`）。浏览器模式拒绝而非回退：计算目标为「浏览器」且 Runner 已连接时以 `compute_browser` 拒绝并给出原因与设置，没有 Runner 时以
`needs_runner` 拒绝。两种模式混用的参数（`.pdbqt` 受体配 SMILES/CSV/SDF 配体，或 PDB 受体配 `.pdbqt` 配体或 `reference_ligand`）作为
`bad_arguments` 拒绝，Runner 自己的模式同样拒绝。上限两侧都经测量并强制执行——`DOCK_LIGANDS_MAX` = 100 个配体一次运行、
`DOCK_RECEPTOR_ATOMS_MAX` = 100,000 个受体原子、`DOCK_LIGAND_ATOMS_MAX` = 80 个配体原子、`DOCK_BOX_VOLUME_MAX` = 216,000 Å³
（60 Å 立方体）——超出即以相应原因拒绝并交给 Runner，Python 在工作线程中也会再次拒绝。

**阶段**：*inputs*（每个 PDBQT 是项目文件 → OPFS，写入时计算哈希，仅在与记录的 SHA-256 一致时保留）→ *read*（Python `dock_read`：按
`bioagent dock` 的方式读取并检查已准备输入，计算盒子，写出每个配体的 Vina 计划 `dock_plan.json`）→ *dock*（每个配体——以及再对接时的参考
配体——一次 Vina 运行，各在一个 Vina 工作线程中，按计划顺序在多个线程上并行）→ *analysis*（Python `run_dock`，`bioagent.docking.prepared`
处理由 `PrecomputedVina` 读回的 Vina 结果，`PrecomputedVina` 把每个结果与它要求的受体、配体、参数、盒子与 wasm 对应）→ *verify*（Python
`verify_pipeline`，对接自己的 `verify_run` 与任务类型按 Runner 名称的 `required_artefacts`）。Vina 运行中途不可中断，因此取消会同时终止每个
Vina 工作线程，已完成的运行保留其检查点；续跑的任务复用仍能按检查点重新哈希一致的运行。每个 Vina 实例可达约 2 GB，故页面至多运行
min(核数 − 1, ⌊(设备内存 − 1.5 GB) / 2.25 GB⌋, 4) 个 Vina 工作线程（4 核、8 GB 的机器上为 2 个；1.5 GB 是任务自己的 Pyodide 工作线程，
始终占用）。时间随配体原子数与穷举度增长，不只随配体数量（夹具大小的配体单线程约 4–15 秒；100 个大配体可能要数小时——任务按配体续跑）。

**Vina 引擎及其可复现构建**：引擎是用 Emscripten 4.0.23 编译为 WebAssembly 的 AutoDock Vina 1.2.7，随站点提供于
`studio/web/js/scientific/dock/vendor/vina-1.2.7.wasm`（492,557 字节，SHA-256 `94807d4aeb35…`，完整
`94807d4aeb358234fc4506ddd4c61b050d6b056fac8da412798eaf615ef09232`），在 `boot.json` 中锁定（`vina`），在实例化之前核对 SHA-256，并由
Service Worker 按该摘要缓存。`studio/scripts/build_vina_wasm.py` 由锁定且经 SHA-256 核对的输入（Emscripten 发布的二进制、Vina 的 PyPI 源
分发、Ubuntu 24.04 的 Boost 1.83 头文件）可复现地构建它；`--check` 重新构建，两次独立构建逐字节一致。移植层 `studio/vina-wasm/` 只替换系统
设施，绝不改 Vina 自己的代码：一个串行的 `parallel.h`（Vina 在运行前按顺序为其蒙特卡洛任务播种，并按任务顺序合并输出，故结果不依赖线程——
原生 Vina 在 `cpu=1` 与任意核数下给出相同位姿）、Boost 的 thread/filesystem/log 垫片，以及在 Vina 自己的类 API 之上的 embind 绑定
`vina_wasm.cpp`。

**一致性：精确层与搜索层**：Vina 的两个确定性运算与原生 `vina` 1.2.7 包逐位一致：`score()` 与 `optimize()` 与其 C++ 值在 float64 末位
相差 0 个单位，`optimize()` 的位姿文本逐字节一致，在全部 6 个夹具配体上如此。搜索不然。**带种子的 `dock` 不是逐位可复现的**——原生 Vina 本身
在仅关闭 glibc 的 FMA 数学路径或链接另一数学库时，就会对相同种子给出不同位姿，因为蒙特卡洛搜索放大了末位差异（Vina 只调用 `acos`、`exp`、
`pow`、`sin`、`sincos` 与 `sqrt`）。因此对接被约束在一个经测量的带内，而非精确一致。在种子 1–8、穷举度 8 下，浏览器每个配体最佳分数的中位数
落在原生自身的散布之内：WebAssembly 构建的中位 |Δbest| 为 0.002–0.077 kcal/mol，对照原生去 FMA 的 0.002–0.086 与原生链接 musl 数学库的
0.002–0.046；苯甲脒在每个引擎上都在 8 个种子中的 8 个上再对接到晶体位姿的 2 Å 以内。验收规则针对中位数，而非单个种子：每个配体
|中位(浏览器 best) − 中位(原生 best)| ≤ 0.10 kcal/mol（种子 1–8），且晶体再对接在浏览器中成功（顶位姿 ≤ 2 Å）的种子数不少于原生。单个
种子的差距可超出该带（布洛芬在种子 42 为 0.151 kcal/mol）；规则针对种子上的中位数，在 `studio/web/test/scientific/vina.test.mjs` 的夹具
路径与 `studio` CI（`VINA_TIER_S=full`，6 个配体 × 种子 1–8）中强制执行。

**端到端**（`science.mjs --mode dock`，无头 Chromium 141 配锁定的 wasm 与 Pyodide，对照原生 Vina 1.2.7；一次本地运行，原生 Python
3.12.3、numpy 2.5.4、vina 1.2.7、x86_64、glibc 2.39）。任务以 6 个夹具配体运行，苯甲脒（BEN）为参考配体并被再对接以验证位点：计划——盒子
（中心 −1.8555, 14.366, 16.748；大小 18 × 18 × 18 Å，取自参考配体）与交给 Vina 的 8 个 PDBQT 文本与原生 `dock_read` 逐字节一致；分析阶段——
`run_dock` 在原生（CPython）上对浏览器自己由 `PrecomputedVina` 读回的 7 个 Vina 结果运行，`scores.tsv`、`report.md`、`report.html`、
`limitations.md`、`plots/scores.svg` 与每个 `poses/<ligand>.pdbqt` 逐字节一致（Pyodide 中的 Python 阶段等同 CPython）；能量模型——每个浏览器
顶位姿分别由原生 Vina 与 WebAssembly 引擎重新打分，逐位一致；搜索——两次运行都完成，6 个配体都对接，无一被拒，BEN 在浏览器中再对接 1.14 Å
（分数 −6.193）、原生 1.92 Å（分数 −6.181），为原子顺序 RMSD（对称感知 RMSD 的上界），种子 42 的每配体 |Δbest| 中位 0.019、最大 0.151
kcal/mol，在上述带内。

**回执、时间与内存**：回执为 `outputs_verified`、设备证据 `verified`，dock 阶段记录了引擎（AutoDock Vina 1.2.7，wasm 构建，SHA-256
`94807d4aeb35…`）与计算事实 `vina.dock`，在 2 个专用工作线程上运行，无 GPU，从未加速。专用 Vina 工作线程的 WebAssembly 内存如实记为
「未测量」（任务服务不读取它）。浏览器任务用时 54.9 秒（dock 阶段 50.2 秒，7 次 Vina 运行，2 个工作线程上共 96 秒的 Vina）。

**已运行与尚未运行**：`science.mjs --mode dock` 端到端与 `vina.test.mjs`（精确层与搜索层）在本地与 `studio` CI 中通过。尚未运行：实体 GPU
不变——对接不使用 GPU（只有 ADMET 适用域检索会用，P3 的实体 GPU 验收仍未满足，§3.2）；用户自己机器上的浏览器标签页（以上运行都是无头
Chromium）；以及在浏览器中经中转下载大型结构（CI 以项目文件给出输入）。浏览器中准备受体与配体（Meeko、RDKit）、相互作用分析与 SDF 位姿
（需要 RDKit）、从原始 PDB/SMILES 出发的输入都是里程碑 B、下一轮的工作，尚未完成（BROWSER_EXPANSION.md）。

**局限说明**：浏览器中的对接运行带有 bioagent 自己的 `limitations.md` 各行——对接分数是打分函数的估计，不是实测亲和力；位姿是关于分子如何
坐入位点的假说；再对接检验未通过的设置不下任何结论——以及一行点名引擎、并说明搜索不是逐位可复现的（在本浏览器中用编译为 WebAssembly 的
AutoDock Vina 1.2.7（锁定的 SHA-256）对接，其 `score`/`optimize` 与原生 Vina 逐位一致，而蒙特卡洛搜索跨引擎或 CPU 不逐位可复现，被约束在
§3.3 的经测量带内）。

**尚未实现**：计算内的多线程 WASM、研究任务与组学任务的 GPU/NPU（它们不用 WebGPU 内核，也没有 WebNN 模型）、其余流程（从 FASTQ 出发的 RNA-seq、PyDESeq2、Scanpy、scVI、10x HDF5 与
`.h5ad` 需要 Runner；从原始结构出发的对接（受体与配体准备：Meeko、RDKit，里程碑 B，尚未完成）与结构预测在浏览器中没有引擎，ADMET 的 SDF 或 `.mol` 文件、受治理的 `predict-admet` 与 `dock-ligands` Skill 运行也需要 Runner）、逐位可复现的对接（`score`/`optimize` 与原生 Vina 逐位一致，但带种子的蒙特卡洛搜索不是逐位可复现的，只被约束在种子 1–8 上的经测量带内，§3.3）、组学与 ADMET 输出与原生的逐字节一致（组学在两个参考上表格末位数字在
容差内不同，UMAP 不比较，只比对过两个参考的参数，用户自己的运行不做比较；ADMET 的适用域文件与 `admet.tsv` 是原生运行的字节，模型卡逐字段与原生一致（模型文件摘要、秒数与化学名称除外：pickle 的字节取决于进程），模型原始输出与适用域检索完全一致，但经 `expit`/`10 **` 的最终预测值可能在 float64 末位相差几个单位（C 库的 `exp`，或 numpy 自带的 AVX-512 幂运算），逐个点名、绝不称作一致，qed 与 BertzCT 的 float64 末位可能相差而 float32 相等；浏览器化学拒绝了 Runner 的 RDKit 能读的训练分子时（真实 TDC 归档上拒绝了 81,809 个分子中的 202 个，0.25%），浏览器构建的模型可能与 Runner 的不同（模型卡计数，比对用同样保留的分子训练原生参考）；浏览器中的比对在小的已知答案归档、TDC 规模的合成归档以及（CI 中）真实 TDC 归档上运行，真实归档上 16,353 个测试集预测中 36 个在末位差 1 个单位）、组学任务超出限值的输入（超过 6,000 个细胞、单个表
超过 66,000,000 个细胞 × 基因、计数矩阵超过 10,000,000 个基因 × 样本，交给 Runner）、在实体 GPU 上验证 ADMET 适用域检索（本机与 CI 无实体 GPU，P3 验收仍未满足）、手机端与浏览器标签页内存的测量。目前只在 Chromium 中运行过（Firefox、Safari 未运行；主线程缺少 `createWritable` 的浏览器写不了任务记录，
任务会失败而不是假装已保存）；浏览器不像 Runner 那样在保存前校验 `.gz` 的 CRC，但清单中的每个 `.gz` 都有审核过的校验和
（STRING 别名文件这一冻结版本也已按两次独立实际下载读到的大小与 SHA-256 固定），截断的副本会因校验和不符而
不被保留；今后若加入没有校验和的 `.gz`，截断时会标为 UNVERIFIED，并在 Python 解析时失败。
