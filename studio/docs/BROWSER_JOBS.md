# Browser science jobs

How TCMScience Studio (<https://science.impf.ai>) runs the network-pharmacology research jobs inside a visitor's
browser, with no local runner and no compute server: what runs where, how the data gets there, how a result is shown
to be the same result the runner gives, and what is not done. Written on 2026-10-19 on branch
`claude/epic-dirac-c1uqll`. Status words are those of [BROWSER_COMPUTE.md](BROWSER_COMPUTE.md) (implemented and
tested; implemented, partly verified; proposed; verified in deployment).

**In one paragraph.** The research loop's Python — `bioagent` and `psh`, the same files the runner imports — runs in
*job workers*: Web Workers holding Pyodide 314.0.7 (CPython 3.14 compiled to WebAssembly) and the SHA-256-verified app
bundle, one per CPU core the page can spare. The 17 raw files (418 MB: NPASS, CMAUP, LOTUS, STRING, Reactome, UniProt)
are kept in the site's Origin Private File System (OPFS) and read in place by Python through synchronous access
handles; the snapshots, the ledger, the PSH audit chain and the nine exports are written there too. Jobs are records in
IndexedDB, followed, cancelled and resumed like runner jobs, and each writes a receipt that says where it ran and what
was checked. On the planted world (the research jobs' known answer) a job worker in Chromium writes the five tables
**byte for byte** as native CPython does (CI: `studio/e2e/science.spec.mjs`); the real 葛根芩连汤 case is run the same
way in the live-data workflow and compared export by export with the native run.

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
records) lists the 17 files: URL, host, reviewed size and checksum where one exists (LOTUS is checked by md5, three
files have none and are verified by size or left UNVERIFIED, as on the runner), licence and what is known about
redistribution — from a reviewed licence record, or "unknown — downloading is not permission to redistribute".

A file reaches OPFS by one of three routes, recorded per file in `.meta/<file>.json`:

- **download** from its host, when the host lets a web page download from it (CORS);
- **relay** through the site's edge Worker, for a host that does not: a GET or HEAD for exactly the registry's URL,
  `Range` and `If-Range` passed through, the body streamed back without buffering, nothing stored, rate-limited like the
  source gateway, redirects only to the registry's reviewed hosts;
- **import** of a file the person obtained elsewhere (Settings → Data → *Import files…*).

`auto` tries the host, then the relay, and records both attempts. A download resumes onto its partial file only when
the server proves it is the same file (`If-Range` with a strong ETag or Last-Modified, and a `Content-Range` that starts
where the part ends with the same total); anything else restarts from byte 0 and says so. Every file is checked again by
the snapshot build's own integrity stage — the runner's code — against its reviewed checksum or size.

## 3. The two jobs

**`research.snapshot`**: plan → acquire → build → persist. `build` runs `bioagent.sources.pipeline.
build_research_snapshots(offline=True)` on the mounted files: integrity, parse and gate each source, the gold standard
(the marker compounds of 葛根芩连汤 found in their source species), the ledger. `persist` replaces the browser's snapshot
store and writes `datasets/research/snapshots.json` last; `research.run` refuses snapshots without it.

**`research.run`**: prepare → research → verify. The sixteen-stage workflow (`bioagent research`) on the snapshots
restored into the worker, its nine exports and artifact; then `np_exports verify` recomputes every digest, each p and q
from the tables, and re-runs the analysis on the snapshots to the same tables, and the outputs are held to what the
runner's `research.run` job requires of them (`tcmstudio.kinds`).

**Checkpoints and recovery.** A job runs only while a page of the site is open: closing it stops the work (nothing
continues in the background), and the next visit shows the job as **interrupted**, resumable. A resumed job reuses a
stage whose recorded outputs still re-hash to their recorded digests in OPFS, and runs the rest again.

**Receipt.** `out/job_receipt.json` (`tcmstudio.job-receipt/1`): the runner receipt's fields where they mean the same
thing, plus `placement: "browser"`, the runtime and platform, the workers each stage ran in with their busy time, memory
as the browser measured it (`performance.measureUserAgentSpecificMemory`, cross-origin isolated pages only; otherwise
null with the reason), and two independent readings of every output's digest: the job worker's as it wrote the file,
and the page's as it read it back from OPFS. `assurance: "outputs_verified"` only when the two agree and nothing
required is missing; the device is `cpu` (WebAssembly), at evidence `verified` only when the verify stage passed.

## 4. How it is checked

| Check | What it shows | Where | Status |
|---|---|---|---|
| Known answer (planted world, 200 permutations) | a job worker's five tables equal native CPython's committed bytes; the exports recompute | `tcmstudio.browser_selftest`; `studio/e2e/science.spec.mjs` (CI, real Pyodide and pyarrow from jsDelivr); Settings → Data → *Check this browser* | implemented and tested: identical bytes in headless Chromium 141 (Pyodide 314.0.7, wasm32) |
| 葛根芩连汤 on the real files | `research.snapshot` + `research.run` as browser jobs, every export compared with the native run | live-data workflow, step "The same research in a browser" (`studio/web/test/runtime/science.mjs --mode gqd`) | implemented; runs on the next live-data run |
| In-process governed analysis | the same bytes and result digest as the isolated child natively | `BioScience-Harness/tests/test_research_inprocess.py` | implemented and tested |
| Data layer | downloads, resume, relay fallback, checksum refusal, import, cancel | `web/test/scientific/*.test.mjs` (in-memory OPFS) and a Chromium OPFS smoke run | implemented and tested |
| Job service and pool | queue, cancel, interruption and resume, receipts, worker crashes | `web/test/scientific/job-manager.test.mjs`, `worker-pool.test.mjs` | implemented and tested |
| Relay | registry-bound, ranges, caps, streaming | `studio/edge/test/datasets.test.js` | implemented and tested; verified in deployment only by the post-deploy probe |

## 5. What this does not do (yet)

- **Threads inside a computation.** Each job worker runs single-threaded WebAssembly; the pool runs separate jobs and
  stages side by side. A per-source parallel snapshot build across workers is designed (NPASS, CMAUP, LOTUS and
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

## 中文摘要

**浏览器科研任务**：网络药理学研究任务（`research.snapshot` 构建快照、`research.run` 十六阶段研究）现在可以完全在
访客浏览器中运行，无需本机 Runner 或计算服务器。计算在"任务工作线程"中进行：每个线程是一个装有 Pyodide 314.0.7
（编译为 WebAssembly 的 CPython 3.14）和经 SHA-256 校验的应用包的 Web Worker，运行与 Runner 完全相同的 `bioagent`/`psh`
代码。17 个原始文件（418 MB）保存在本站的私有文件系统（OPFS）中，Python 通过同步访问句柄就地读取；下载可直连上游、
经本站边缘中转（流式、不存储）或由用户导入，并在构建快照时按审核过的校验和再次核验。浏览器 Worker 不能启动子进程，
因此受治理的分析在同一进程内经同一个 PSH 代理与审计链运行，产物如实记录 `isolation: "in_process"`。任务记录存于
IndexedDB，可取消；页面关闭时任务中断，下次可从已核验的阶段继续。每个任务写出回执：运行位置、运行时、每个输出的两次
独立哈希读取。验证：模拟世界（已知答案）在 Chromium 的任务工作线程中写出的五张表与原生 CPython 逐字节一致（CI）；
葛根芩连汤真实数据在 live-data 流程中以浏览器任务运行，并与原生运行逐个导出文件比对。尚未实现：计算内的多线程
WASM、GPU/NPU、其他流程（RNA-seq、单细胞为下一步；对接、ADMET 在浏览器中没有引擎）、手机端测量。
