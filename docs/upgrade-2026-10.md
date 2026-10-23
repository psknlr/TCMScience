# Capability upgrade, October 2026: what each tool, dataset and device can be shown to do

This report covers branch `claude/epic-dirac-c1uqll` against the engineering requirements for a
verifiable, browser-first research platform (capability registry, routing, dataset acquisition,
network pharmacology, analysis pipelines, CPU/GPU/NPU evidence, remote compute, receipts, testing,
interface and documentation). It lists what changed, what was tested and where, what is verified
on real data or real hardware, and what is not, with the reason. A claim here rests on a named run
or a committed record; nothing counts as working because it is registered, imports, or passes a
unit test that never reached the thing it describes.

**In one line:** every catalog entry now says what implements it, what evidence it has and whether
it can run here; every call's receipt says what ran, where, on which data and how far it was
checked; large datasets are acquired by a service that resumes only onto the same remote file; the
network-pharmacology research job records sixteen stages and writes nine reproducible exports; the
five analysis pipelines call a run done only when it is complete; and no GPU, NPU or remote-compute
claim is made without evidence for it.

**Not verified in this branch:** none of its new code has run on a physical GPU or an NPU, and none
of it has been verified on the deployed site beyond the post-deploy check (below). From
2026-10-11 to 2026-10-19 GitHub Actions started no job on this repository (every run ended within
seconds with no runner assigned, `main` included), and the development container's network policy
refuses the scientific data hosts, so the suites under "Tests run for this report" ran locally. Jobs
run again since 2026-10-19: on the merged code (e73a1c2), `ci` #317 passed every job a push runs, on
CPU runners; `live-data` #16 and #17 downloaded the real files through the new transfer and ran the
葛根芩连汤 research job, both successfully; and `studio` #86 tested, built and deployed the site. A CI
pass is a test result on GitHub's runners, not a deployment verification.

## Commits

| Commit | What it does |
|---|---|
| `fb16e7b` | a Settings → Privacy test that failed on an unchanged tree now waits for its asynchronous digest |
| `875de94` | router: structured requirements for every unavailable call, `unknown_compute` and `remote_not_configured` with no fallback, host-scoped network approvals, placement and approvals in the receipt, a router that never throws |
| `2f300ec` | one verified capability registry (implementation, verification from committed records, per-runtime readiness, engines, hosts, data), `capability.readiness()` as the single implementation, the capability audit, receipt `tcmstudio.receipt/1`, and the count checker `doc_facts.py` |
| `c602823` | the GPU/NPU evidence ladder: one adapter classifier, a known-answer test per device, "accelerated" only from a like-for-like measurement on a physical adapter, WebNN reported as detection only |
| `031b70e` | per-stage run records for RNA-seq, single-cell, docking, structure prediction and ADMET; completeness checks before a job can succeed; `job_receipt.json`; the pipeline reference manifest |
| `d8bb0c3` | verified resumable acquisition (`acquisition/transfer.py`, `service.py`) and the runner's `/api/datasets` routes; the network-pharmacology research workflow (sixteen stages, nine exports, `verify`) |
| `a22b702` | documentation claims in English and Chinese, `REMOTE_COMPUTE.md`, the connector coverage report by verification level, the monthly live-connector workflow, CI wiring |
| `b1c9409` | the interface: catalog state fields and filters, Settings → Data, job cards with stages, result kinds and provenance, hardware evidence |
| `be06bdc` | the fixes from a whole-branch review: 27 confirmed findings across correctness, honesty, security, contracts and tests |

168 files changed. No file in the content-hash closure of the four pinned governed skills was
edited, and `scripts/check_lockfile.py` reports the four pins matched after every commit.

## Requirements and their status

| Requirement | Status | Where | What backs it |
|---|---|---|---|
| Capability state in three dimensions (implementation, readiness, outcome) | implemented, tested | `tcmstudio/capability.py`, `catalog.py`, `core/catalog.js` `entryState` | `test_capability_state.py` (46 tests); readiness per runtime, so the runner's never replaces the browser's |
| Each entry: implementation, environments, dependencies, datasets, hosts, backends, limits, verification | implemented, tested | catalog `implementation`, `execution.{browser_compute,engines}`, `data`/`data_from`, `hosts`/`hosts_by`, `limits`, `verification` | references resolve (incl. each hand-written op); verification only from committed records; catalog under a 1,000,000-byte budget |
| Counts agree across manifest, matrix, API, UI and docs | implemented, tested | `studio/scripts/doc_facts.py --check` (35 docs), `compat_matrix.py --check` | 652 entries: 150 native, 11 skill, 444 connector, 5 clinic, 15 tcmdb, 5 study, 12 job, 10 system |
| Capability audit service | implemented, tested | `system.capability_audit`, `GET /api/capabilities/audit` | a runner in the development container: 634 ready, 5 missing a dependency (docking, ADMET, PaperQA engines), 11 needing a dataset (none built); a committed PASS record for 526 of them, none for 124 |
| Fixture vs live evidence kept apart | implemented, tested | `studio/docs/compat/connectors.md` | per operation: mock transport PASS 444 (offline), live upstream PASS 444 from the committed record dated 2026-09-17 to 2026-10-07, live through the deployed gateway not verified |
| Task-aware routing with strict preferences | implemented, tested | `core/router.js` | `auto` stays runner-first (one durable audit chain per project); `browser`, `runner` strict; `remote` always unavailable with need `remote_backend`; an unknown value is reported, never run as `auto`; a missing engine is refused before approval |
| Structured unavailable results | implemented, tested | `error.requires` (Requirement: need, detail, remedy, names), `error.reason` | in Python and in the page; `catalog_search` needs strings unchanged |
| Receipts with provenance and verification | implemented, tested | receipt `tcmstudio.receipt/1`: `execution`, `provenance`, `result {kind, outcome, assurance, checks, artifacts, fallbacks}` | additive; results and output hashes unchanged (checked across 166 calls in both runtimes); patient arguments only as hashes |
| Dataset acquisition outside the bounded gateway | implemented, tested on loopback | `acquisition/transfer.py`, `service.py`, `tcmstudio/datasets.py` | resume only onto the same file (If-Range, exact Content-Range) or under a reviewed checksum; archives checked before a file is kept; every redirect hop checked; 23 transfer tests on a local Range server, which are mechanics tests, not live downloads |
| Large real downloads | verified earlier; the new transfer run live on 2026-10-19 | `bioagent/data/live_download_verification.json` | live-data run [38127338070](https://github.com/psknlr/TCMScience/actions/runs/38127338070), 2026-10-11, through the earlier downloader: NPASS activities 90.4 MB, STRING links 83.2 MB and NPASS species pairs 80.3 MB PASS; all 6 NPASS and 5 CMAUP files PASS. The new VerifiedTransfer in live-data #17 ([39236366011](https://github.com/psknlr/TCMScience/actions/runs/39236366011), 2026-10-19): 17 files from their servers, 14 PASS against a reviewed checksum or size, 3 UNVERIFIED (no reviewed checksum or size), a resume onto a partial file PASS on Zenodo, STRING and bidd.group; the committed record still holds the 2026-10-11 run |
| Network pharmacology: seed skill vs research workflow | implemented, tested on fixtures | `analysis/np_exports.py`, `research/workflow.py`, `kinds.py` `research.run` | the pinned seed skill is untouched and titled "Limited seed-corpus network hypothesis analysis"; the research job records 16 stages and writes the 9 exports; GO SKIPPED (no source), KEGG BLOCKED (licence); nothing to test is UNVERIFIED, never PASS |
| A real-data network pharmacology case | passed in live-data #16 and #17 (2026-10-19) | `live-data.yml` step "The research job on real data: 葛根芩连汤" | the step checks exit codes, that the nine exports exist and are not empty, and that `np_exports verify` exits 0; it asserts no figure. Earlier real-data evidence: the Guizhi Decoction benchmark on the real NPASS, CMAUP and LOTUS files, repeated and reproduced in live-data #3–#5 ([release-qualification-2026-10.md](release-qualification-2026-10.md)) |
| Complete pipelines with stages, checks, manifests, limitations | implemented, tested | `bioagent/runrecord.py`, the five pipelines, `jobs.py` | built-in RNA-seq and single-cell references run here; Scanpy, harmonypy, PyDESeq2, RDKit, meeko and scikit-learn paths run in a second local environment; real AutoDock Vina, scVI and the external RNA-seq binaries were not run here, and ran in CI's engine jobs in ci #317 (2026-10-19, CPU runners); local ESMFold weights and ColabFold were not run |
| GPU acceleration only with evidence | implemented, tested | `runtime/webgpu.js` `classifyAdapter`, `compute/accelerator.js`, `receipt.compute.gpu` | the only adapter available here was SwiftShader, classified software: nothing reached "accelerated" |
| NPU | detection only | `compute/capabilities.js`, `devices.py` accelerator-runtime report | "WebNN detected; NPU execution unverified"; no model, no ONNX Runtime, no NPU job device |
| Remote compute backend | designed, not deployed | `studio/docs/REMOTE_COMPUTE.md` | the contract and security controls; nothing in the repository computes remotely |
| Interface | implemented, tested | `ui/pages/catalog.js`, `ui/pages/data.js`, `ui/toolcards.js`, `ui/inspector.js`, `ui/panels.js` | 1,357 strings in each language; axe sweeps include the new views |
| Documentation corrections in English and Chinese | done | READMEs, `studio/README.md`, `studio/docs/*`, `docs/validation.md`, `docs/analysis-pipelines.md`, `docs/omics-backends.md` | `doc_facts.py --check` keeps the restated numbers tied to the catalog |

## New and changed interfaces

- **Catalog document** (`tcmstudio.catalog/1`, additive): per entry `implementation`, `state`,
  `verification`, `approvals`, `execution.browser_compute`, `execution.engines`, `hosts_by`,
  `data_from`, `limits`; per document `limits`, `verification_records`; the new entry
  `system.capability_audit`.
- **Envelope**: `error.requires`, `error.reason`; receipt `tcmstudio.receipt/1` (`schema`,
  `finished_at`, `execution`, `provenance`, `result`); page-made envelopes use `where: "page"` and
  `decided_by`. `envelope.status`, `error.type` and job states are unchanged.
- **Runner HTTP API**: `GET /api/capabilities/audit`; `GET /api/datasets`,
  `GET /api/datasets/{key}/manifest`, `GET /api/datasets/{key}/readiness`,
  `POST /api/datasets/acquisitions`, `GET /api/datasets/acquisitions[/{id}]`,
  `DELETE` or `POST …/cancel`; the job event stream gains `stages`.
- **Files**: `stages.json` (`bioagent.stages/1`), `job_receipt.json` (`tcmstudio.job-receipt/1`),
  the research run's `run_manifest.json` (`tcmscience.run-manifest/1`), `limitations.md`,
  `bioagent/data/pipeline_references.json`.
- **Job**: `progress.{stage, bytes, files, verified}`, `stages`, `limitations`, `receipt`.
- **Approvals**: network grants keyed by their hosts (`<entry>:network@<sha12>`); `remote_runner`
  (`runner@<host>`) for a runner that is not this computer; per-call consent for patient data on
  such a runner.

[studio/docs/CONTRACTS.md](../studio/docs/CONTRACTS.md) and
[CONTRACT_NOTES.md](../studio/docs/CONTRACT_NOTES.md) document each of them.

## Tests run for this report

All in the development container (4 CPUs, Linux, Python 3.13, Node 22), on the final commit:

| Suite | Result |
|---|---|
| Studio runner (`python -m pytest studio/runner/tests`) | 542 passed |
| Studio web (`npm test`) | 456 passed, 1 skipped (the Python glossary comparison, which needs the system `python3` to import the packages) |
| Edge Worker (`node --test studio/edge/test`) | 92 passed |
| BioScience-Harness unit tier (`pytest -m unit`) | 2,698 passed, 139 skipped, 0 failed (the baseline before this branch: 2,586 passed) |
| Pipeline, acquisition and research tests (BioScience-Harness, targeted) | 281 passed, 1 skipped (the TDC ADMET archive is not available) |
| Checks | `compat_matrix --check`, `doc_facts --check`, `connector_coverage --check`, `make_figures --check`, `check_lockfile.py`: all pass |
| Browser end-to-end | `pipeline.spec.mjs` 6 passed; the Pyodide specs cannot run here (the CDN is refused) and in CI they now fail rather than skip |

## Hardware and backends

| Backend | Status | Evidence |
|---|---|---|
| Browser CPU (Pyodide) | verified for what was compared | the runtime check compares the seven kernel tools, the TCM lookups, ranked formula search, two statistics tools and three of the four pinned skills with native Python; it is a manual check, not run in CI |
| JavaScript and WebAssembly SIMD kernels | measured | `studio/docs/benchmarks/2026-10-18-chromium-141-linux-webgpu-software.md`: exact results, CPU timings in Chromium 141 |
| WebGPU, software adapter | tested | SwiftShader is classified software and is never counted as acceleration |
| WebGPU, physical adapter | unverified | no physical GPU was available; "accelerated" is reachable only from a measurement on one |
| Runner GPU (CUDA, ROCm, MPS) | reported only | device lists come from drivers and frameworks; jobs record the assigned device as "selected" |
| NPU (WebNN, OpenVINO, ONNX Runtime providers, Core ML, DirectML) | detection only | no tool runs a neural model; no NPU device is offered to jobs |
| Remote compute | not configured | `remote_compute.configured` is false in both runtimes |

## What remains, and why

- **Live runs of the new code.** The new transfer and the research job's nine exports on real data
  ran in live-data #16 and #17 (2026-10-19), and every CI step a push runs passed in ci #317; the
  葛根芩连汤 step took under two minutes in #17 (its timeout is 70 minutes). Still to come: a connector
  call through the deployed gateway, and the first run of `connectors-live.yml`, which has not run
  yet.
- **Physical GPU and NPU measurements.** They need the hardware; the ladder and the receipt fields are
  ready for them.
- **Object storage (R2) for shared datasets.** Not implemented: it could not be verified here, and
  copies of datasets without a licence record would be redistribution.
- **GO enrichment and KEGG.** No GO annotation source is registered (adding one is a reviewed
  decision about trusted sources); KEGG needs a licence.
- **Structure identity in network pharmacology.** Compounds are merged by exact InChIKey only; no
  re-standardisation is claimed.
- **ONNX Runtime Web and an embedding model.** Not added: no tool uses a model, nothing could be
  verified here, and the runtime would add many megabytes to the offline shell.
- **Remote compute.** Designed (`REMOTE_COMPUTE.md`), not built: the public site never computes.
- **`auto` placement.** Kept runner-first rather than browser-first for light tools: a project keeps
  one durable audit chain, and native Python on the user's own machine is faster than Pyodide.
- **Deployment.** `studio` #86 (2026-10-19, the push to `main` at e73a1c2) tested, built and
  deployed the merged code, and its post-deploy check (`studio/edge/scripts/check.mjs`: the site,
  the source gateway, telemetry and the admin gate, and one tiny Tao-S1 call) passed. That check
  verifies no connector through the gateway.

## 中文摘要

本报告对应分支 `claude/epic-dirac-c1uqll`。每个目录条目现在写明由什么实现、有哪些已提交的核验记录、在当前运行环境能否运行及缺什么；
每次调用的回执写明在哪里运行、用了哪些数据、核验到什么程度（`tcmstudio.receipt/1`）。大型数据集由 Runner 上的获取服务下载，只在能确认是同一远端
文件时续传，文件经完整性检查后才保留。网络药理研究任务记录 16 个阶段并写出 9 个可复现的导出文件；GO 未注册数据源（SKIPPED），KEGG 需要许可
（BLOCKED），没有可检验的内容时记为 UNVERIFIED，绝不记为 PASS。五条分析流程只有在全部必需阶段成功时才算完成。GPU 只有在实体适配器上实测快于
CPU 时才称“已加速”，本环境只有软件渲染器（SwiftShader）；NPU 只做检测（“检测到 WebNN；NPU 执行未经验证”）；远程计算只有设计文档，未部署。

本分支的新代码尚未在实体 GPU 或 NPU 上运行；除部署后的检查外，没有任何新代码在已部署站点上经过验证。2026-10-11 至 2026-10-19 期间，本仓库的 GitHub Actions 没有为任何任务分配运行器，开发容器的网络策略
也拒绝科学数据主机，因此「Tests run for this report」中的测试均在本地运行。自 2026-10-19 起任务恢复运行：在合并后的代码（e73a1c2）上，`ci` #317 中推送时运行的任务全部通过
（CPU 运行器）；`live-data` #16 与 #17 经新的传输下载真实文件并运行葛根芩连汤研究任务，均成功；`studio` #86 完成测试、构建并部署站点。CI 通过是 GitHub
运行器上的测试结果，不是部署验证。此前的真实数据证据仍然有效：2026-10-11 的 live-data 运行下载并核验了 NPASS（活性文件 90.4 MB）、
CMAUP 和 STRING（互作文件 83.2 MB），桂枝汤基准在真实文件上重复运行并复现。
