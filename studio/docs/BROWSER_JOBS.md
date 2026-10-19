# Browser science jobs

How TCMScience Studio (<https://science.impf.ai>) runs the network-pharmacology research jobs inside a visitor's
browser, with no local runner and no compute server: what runs where, how the data gets there, how a result is shown
to be the same result the runner gives, and what is not done. Written on 2026-10-19 on branch
`claude/epic-dirac-c1uqll`. Status words are those of [BROWSER_COMPUTE.md](BROWSER_COMPUTE.md) (implemented and
tested; implemented, partly verified; proposed; verified in deployment).

**In one paragraph.** The research loop's Python — `bioagent` and `psh`, the same files the runner imports — runs in
*job workers*: Web Workers holding Pyodide 314.0.7 (CPython 3.14 compiled to WebAssembly) and the SHA-256-verified app
bundle, in a pool sized from the device (one per CPU core the page can spare, at most one per 1.5 GB of device memory,
at most four). The job service runs one job at a time in a tab, in one worker, so today one worker computes at a time. The 17 raw files (418 MB: NPASS, CMAUP, LOTUS, STRING, Reactome, UniProt)
are kept in the site's Origin Private File System (OPFS) and read in place by Python through synchronous access
handles; the snapshots, the ledger, the PSH audit chain and the nine exports are written there too. Jobs are records in
IndexedDB, followed, cancelled and resumed like runner jobs, and each writes a receipt that says where it ran and what
was checked. On the planted world (the research jobs' known answer) a job worker in Chromium writes the five tables
**byte for byte** as native CPython does (`studio/e2e/science.spec.mjs`, passed in Studio's CI with Pyodide's own
pyarrow); the real 葛根芩连汤 case is defined in the live-data workflow, run the same way and compared export by export
with the native run, and has not completed a run yet.

## 1. What runs where

| Part | Where | Code |
|---|---|---|
| Planning a call, approvals | the page | `js/core/router.js`: a job placed in the browser goes to the job service, never to the tool worker |
| Job records, queue, cancel, resume, receipts | the page | `js/scientific/runtime/job-manager.js` (IndexedDB `tcmstudio-jobs`) |
| The research computation | job workers (Pyodide, CPU, WebAssembly) | `js/scientific/engines/science.worker.js` → `tcmstudio.browser_jobs` → `bioagent` |
| Downloads, imports, hashing | a data worker (no Pyodide) | `js/scientific/data/data.worker.js` |
| Files | OPFS, under `tcmstudio/` | `js/scientific/data/opfs-store.js` |
| Relay for hosts that refuse a cross-origin download | the edge Worker, streaming, keeping nothing | `studio/edge/src/datasets.js` (`/api/datasets/files/<group>/<name>`) |
| Language model | Tao-S1 through `/v1` (unchanged) | not a computation of the job |

Job kinds the browser runs: `research.snapshot` and `research.run` (catalog `BROWSER_JOB_KINDS`; each carries
`execution.browser_job`). A connected runner still takes them under compute **Auto** (one place per project); under
**Browser** they run here. Every other job kind stays on the runner, and a call for one in the browser says so.

### 1.1 Job workers

`ScienceWorkerPool` sizes itself from the device: `min(cores − 1, ⌊memory GB / 1.5⌋)`, between 1 and 4 (memory unknown
is taken as 2 GB, and the reason is recorded). A worker boots lazily the first time a job leases it (Pyodide from the
pinned index, the bundle from Cache Storage when its hash matches, the compiled modules put back), loads the Pyodide
packages a stage needs (`pyarrow` for the snapshot store, from the same Pyodide distribution), and runs one operation at
a time. The tool runtime's worker is a separate worker: a long job never blocks a tool call.

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

## 3. The two jobs

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
verify stage ran in this attempt and said it passed (a snapshot build has none: `validated`).

## 4. How it is checked

| Check | What it shows | Where | Status |
|---|---|---|---|
| Known answer (planted world, 200 permutations) | a job worker's five tables equal native CPython's committed bytes; the exports recompute. It covers the herb layer, NPASS activities, Reactome enrichment with its permutation null, the rebuttals and the exports; the planted world has no STRING, disease, CMAUP or LOTUS layer, so `network.tsv` and `disease_targets.tsv` are header only and the network topology is not exercised | `tcmstudio.browser_selftest`; `studio/e2e/science.spec.mjs`; Settings → Data → *Check this browser* | implemented and tested: identical bytes in headless Chromium 141 (Pyodide 314.0.7, wasm32), locally and in Studio's CI with Pyodide's pyarrow 22.0.0 from jsDelivr |
| The job service end to end | `research.run` and the self-test as jobs: stages, the research stage's checkpoint, a receipt at its recorded digest with `outputs_verified` and device evidence `verified`, the five tables the committed answer; a second run whose workers are stopped as its research stage ends fails, and resumed it reuses that stage from its checkpoint and gives the same tables | `studio/web/test/runtime/science.mjs --mode service` (Studio CI, build job) | implemented and tested in headless Chromium locally (a local stand-in for Pyodide's pyarrow); its CI step has not run yet |
| Snapshot build from files in OPFS | the parser tests' fixture files mounted read-only from OPFS (a write to one is refused, their bytes in OPFS unchanged) and built in a job worker; then imported through the dataset workspace and built by `research.snapshot` as a job, down to its snapshots, its marker and its receipt (`outputs_verified`). Both give the native build's six snapshot ids | `science.mjs --mode fixture` (Studio CI, build job) | implemented and tested in headless Chromium locally (as above); its CI step has not run yet |
| 葛根芩连汤 on the real files | `research.snapshot` + `research.run` as browser jobs, every export compared with the native run | live-data workflow, step "The same research in a browser" (`studio/web/test/runtime/science.mjs --mode gqd`) | implemented; has not completed a run yet |
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
- **Other pipelines.** RNA-seq and single-cell count-matrix analyses need only numpy and scipy (both in Pyodide) and
  are the next candidates, held to tolerance (their committed digests depend on the numpy build); docking (Vina, RDKit)
  and ADMET (RDKit) have no browser engine.
- **Background work.** A browser job stops when its page closes; it is never presented as continuing.
- **Phones.** Storage quota and memory for 418 MB of files have not been measured on a phone.
- **Browsers.** Run in Chromium only. The page writes job records' files (receipts, checkpoints, the snapshot marker) with
  `createWritable`; a browser whose main thread lacks it (older Safari) refuses those writes, and the job fails rather
  than pretending to have kept them. Firefox and Safari have not been run.
- **Archives without a checksum.** The runner reads a `.gz` through its CRC before keeping it; the browser does not, so a
  truncated STRING aliases file (no reviewed checksum) is kept as UNVERIFIED and fails later, when Python parses it.
- **Two tabs downloading the same file** do not corrupt it, but one finished download can be discarded and fetched again.

## 中文摘要

**浏览器科研任务**：网络药理学研究任务（`research.snapshot` 构建快照、`research.run` 十六阶段研究）可以完全在访客浏览器
中运行，无需本机 Runner 或计算服务器。计算在"任务工作线程"中进行：每个线程是一个装有 Pyodide 314.0.7（编译为
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
从检查点续跑）与 OPFS 中夹具文件的快照构建（`research.snapshot` 作为任务运行，快照编号与原生一致）已在本地无头 Chromium
中通过（pyarrow 为本地替身），其 CI 步骤尚未运行。葛根芩连汤真实数据在 live-data 流程中以浏览器任务运行并与原生运行逐个
导出文件比对：已实现，尚未完成一次运行。中转已实现并有单元测试，尚未部署。

**尚未实现**：计算内的多线程 WASM、GPU/NPU、其他流程（RNA-seq、单细胞为下一步；对接、ADMET 在浏览器中没有引擎）、
手机端测量。目前只在 Chromium 中运行过（Firefox、Safari 未运行；主线程缺少 `createWritable` 的浏览器写不了任务记录，
任务会失败而不是假装已保存）；浏览器不像 Runner 那样在保存前校验 `.gz` 的 CRC，没有审核校验和的 STRING 别名文件若被
截断，会标为 UNVERIFIED，并在 Python 解析时失败。
