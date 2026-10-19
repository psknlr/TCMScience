# Remote compute: the contract (designed, not deployed)

> **Status: designed, not deployed.** Nothing described in sections 2 to 6 exists. No remote compute
> environment is deployed for TCMScience Studio, none is registered in the code, and none has been tested. This
> document is the contract a remote backend would have to meet before Studio could route a single call to it. What
> exists today is in [section 1](#1-what-exists-today), and only that may be described as working.

Related: [ARCHITECTURE.md](ARCHITECTURE.md) (placement rules), [CONTRACTS.md](CONTRACTS.md) §3 (envelope and
`tcmstudio.receipt/1`), §4 (runner jobs, `job_receipt.json`), §6 (router), [BROWSER_COMPUTE.md](BROWSER_COMPUTE.md)
(what runs in the page).

Studio runs a call in one of two places today: the visitor's browser (Pyodide, with exact JavaScript and WebAssembly
kernels for seven sequence tools) or the person's own runner (`tcmstudio serve`). A pipeline that needs gigabytes of
memory, a native engine or a GPU, and a person who has no such computer, have nowhere to go. A remote compute
environment would be a third place, authorised by the person and run by someone accountable for it. It is not a way
for science.impf.ai to compute: **the public site's server never computes** (ARCHITECTURE.md), and this design keeps
that rule.

---

## 1. What exists today

| Piece | Behaviour now | Where |
|---|---|---|
| The compute preference `remote` | accepted and saved; every call fails (status `failed`, `error.type` `unavailable`, `error.reason` `remote_not_configured`, need `remote_backend`) and is never moved to the browser or the runner instead. The exceptions are `catalog_search` and `capabilities_status`, which the page answers itself (nothing leaves it) | `core/settings.js` (`COMPUTE_TARGETS`), `core/router.js`, CONTRACTS §6 |
| `capabilities_status` / `system.capabilities` | `remote_compute: {configured: false, reason}`, in the page's answer and in the runner's | `core/catalog.js` `REMOTE_COMPUTE`, `dispatch._REMOTE_COMPUTE` |
| The scheduler's `remote` kind | named, no backend registered; a task marked sensitive never goes to a non-local backend without `consent.remote === true` | `compute/scheduler.js`, BROWSER_COMPUTE §3 |
| `allow_remote` (runner setting) | the only path by which data leave the person's machines for computation: the runner's structure-prediction job may send a sequence to ESM Atlas or ColabFold's servers, and docking may fetch a remote receptor, only when it is on and the call's `allow_remote` approval (`<entry>:remote_upload`) was given | CONTRACT_NOTES (`allow_remote`), CONTRACTS §6 |
| A runner on another machine | pairs by address and token; its first call asks `remote_runner` (key `runner@<host>`), never covered by the local `runner` approval, and a clinic call to it is asked every time | `core/router.js`, CONTRACT_NOTES |

A runner on another machine is the person's own computer, reached over the network. It is not the remote compute
environment of this document: it has no queue shared between people, no quotas and no operator.

---

## 2. Roles

```
 browser (page) ──HTTPS──▶ control layer (authenticated; no computation) ──▶ queue ──▶ worker pool (CPU / GPU)
        ▲                       │  identity, project isolation, approvals,          │  the same Python packages
        │                       │  allowlists, quotas, audit                        │  (bioagent, psh, tcmstudio)
        └── signed, short-lived URLs ◀── artefact storage ◀────────────────────────┘  at reviewed versions
```

- **Control layer.** An authenticated service (it may be a Cloudflare Worker on its own route, or any HTTP service)
  that accepts a job, checks it and enqueues it. It runs no scientific code and never reads datasets or results: it
  forwards a job description and returns status and signed URLs. It is separate from science.impf.ai's public relay
  and source gateway, whose behaviour is unchanged.
- **Queue.** Durable: an accepted job survives a restart of the control layer and of the workers.
- **Workers.** CPU and GPU machines that run the **same Python implementations** the runner runs (`bioagent`, `psh`,
  `tcmstudio` at the versions in the job's catalog entry, and the engines named in its `execution.engines`), installed
  from reviewed images. A worker never runs a reimplementation, an approximation or a substitute engine: an engine it
  lacks makes the job fail with `missing_engine`, as on the runner.
- **Artefact storage.** Outputs, logs and receipts, per project, readable only through short-lived signed URLs.

---

## 3. The job contract

A remote job is a runner job (CONTRACTS §4) submitted to another place. The same kinds, parameters, states and
records apply, so the page, the receipts and the validators need no second vocabulary.

| Concern | Contract |
|---|---|
| Submission | `{kind, params, project_id, submission_id, dataset_refs, approvals}`. `kind` is a job kind of the catalog; `params` are checked against the entry's schema by the control layer before enqueueing; `submission_id` makes a retried submission idempotent. A free-form command, script or container image is refused. |
| Status | `queued \| running \| succeeded \| failed \| cancelled`, the runner's five states and no others, durable in the queue's store. `progress` has the runner's shape (`{fraction, stage, bytes, files, verified, message}`), read from the job's `stages.json`. `succeeded` only after the outputs are collected and verified (digests and the pipeline's `verify_run`), as on the runner. |
| Dataset locality | A job names datasets by registered key and release (the dataset registry, `GET /api/datasets` on the runner). The environment either holds a verified copy (its reviewed checksum matches) or acquires it through the same verified transfer as the runner (`bioagent.acquisition.transfer`). It never accepts an arbitrary URL. Data the person uploads go to that project's storage only, and only after the approval below. |
| Artefacts | Stored per project with their SHA-256 and size; the job's `job_receipt.json` lists them. Downloads use signed URLs that expire within minutes and name one object. |
| Cancellation | `DELETE` on the job: a queued job never starts; a running one is signalled and stopped between stages; its state becomes `cancelled` and its partial outputs are kept for inspection, never shown as a result. A cancelled job is not retried elsewhere (the scheduler's rule). |
| Retries | Only for failures of the infrastructure (a lost worker, a preempted machine), at most a stated number of times, from the start or from a stage whose outputs verified, and every attempt recorded. A job whose own check failed is not retried: a FAIL is a result. |
| Quotas | Per person and per project: concurrent jobs, CPU-hours, GPU-hours, storage and egress, checked before a job is accepted; an exhausted quota refuses the submission with the quota named. |
| Cost accounting | Each job records its measured CPU and GPU time, peak memory, storage and egress (the `resources` block of `job_receipt.json`, extended with the worker's device), and the account it was charged to. Estimates are shown as estimates. |
| Receipts | The envelope's receipt is `tcmstudio.receipt/1`, with `where` naming the remote environment (never `runner`, never 本机), `execution.placement.chosen: "remote"`, the worker's device with its rung on the evidence ladder (`selected` for an assigned GPU, higher only from the worker's own evidence), and `provenance.datasets` with the releases and checksums used. `job_receipt.json` is written by the worker and digested by the control layer. |

---

## 4. Security controls

- **Identity.** Every request is authenticated as a person (a session or token the control layer verifies); there
  is no anonymous submission. The pairing token of a local runner is not an identity here.
- **Per-project isolation.** A job reads and writes only its project's storage; workers run each job in its own
  sandbox with no access to another project's files, credentials or network allowance.
- **Explicit remote-data approval.** Sending anything to the remote environment (parameters, uploaded files, a
  project's data) needs the person's approval, separate from every other approval: a new reason `remote_compute` with
  the key `remote@<host>`, never covered by `runner`, `runner@<host>` or `<entry>:remote_upload`, and asked again for
  every call that carries clinical (`phi`) data. **The router does not implement this reason today**; every `remote`
  call fails with `remote_not_configured` before an approval would be asked. Adding a backend must add the reason, its
  zh and en wording, and its tests in the same change.
- **Restricted datasets.** A dataset whose licence record forbids redistribution, or that has no licence record, is
  never copied into shared storage on the person's behalf: the job either uses a copy the person is entitled to in
  their own project or does not run. Downloading is not permission to redistribute.
- **Short-lived signed URLs** for every artefact download; no public bucket, no listing.
- **Secrets on the server side only.** Storage and queue credentials, model keys and dataset credentials live in the
  control layer and workers; none is sent to the page, written into an artefact or a receipt, or logged.
- **No arbitrary shell.** Workers run allowlisted job kinds through their reviewed entrypoints, in allowlisted
  container images at pinned digests, with CPU, memory, GPU, time and disk limits per kind. A job cannot name a
  command, an image or a package to install.
- **Network from workers** is limited to the hosts the job's entry declares (its `hosts`, and the dataset registry's
  for acquisitions), as the runner's policy kernel limits them.
- **Audit records.** Every submission, approval, start, retry, cancellation, artefact access and failure is recorded
  with who, what, when and the digests involved, append-only, and readable by the person for their projects.
- **No patient data without authorization.** Clinical inputs (`phi` entries, the clinic) are refused by the remote
  environment unless a recorded authorization for that environment and that project exists (an agreement covering
  the data and the operator), in addition to the per-call approval. Without it the clinic stays in the browser or on
  the person's own runner.

---

## 5. What does not change

- `auto` stays runner-first, then the browser. A remote environment is chosen only by setting compute to `remote`
  (or, later, per job by an explicit choice), never as a silent fallback: a project's skill and clinic runs write one
  durable audit chain, and spreading one project across places without asking would fork it.
- Nothing is ever computed on science.impf.ai's public site, and its relay and source gateway keep their limits.
- A remote result carries the same governance as a local one: the same verdicts, release states, hashes and
  limitations. Running elsewhere licenses no stronger claim.

---

## 6. Before anything here may be called deployed

Each of these must exist and be recorded first: a registered backend that answers `remote_compute.configured: true`
with its host; the `remote_compute` approval in the router with its tests; a test suite against a real deployment
(submit, status, cancel, retry after a lost worker, quota refusal, signed URL expiry, a refused arbitrary command, a
refused `phi` job without authorization); and, for GPU workers, a known-answer test on the device before its results
are labelled as computed there. Until then the only correct description is the one at the top of this page: designed,
not deployed.

---

## 中文摘要

**状态：已设计，未部署。** 第 2 至 6 节描述的内容都不存在：TCMScience Studio 没有部署任何远程计算环境，代码中没有注册任何远程
后端，也没有做过任何测试。

**现在的实际情况：** 计算目标可以设为「远程」并会被保存，但每次调用都失败（状态 `failed`，`error.type` 为 `unavailable`，
`error.reason` 为 `remote_not_configured`，需要 `remote_backend`），不会改到浏览器或本机 Runner 上运行；只有 `catalog_search`
与 `capabilities_status` 由页面自己回答（不离开页面）。`capabilities_status` 与 `system.capabilities` 都报告
`remote_compute: {configured: false, reason}`。数据离开用户自己的机器去做计算的唯一途径，是 Runner 的 `allow_remote` 设置
（结构预测可把序列发往 ESM Atlas 或 ColabFold 服务器），且须经该调用的批准。另一台机器上的 Runner 仍是用户自己的电脑，首次
调用单独批准（`runner@<主机>`），不属于本文所说的远程计算环境。

**设计要点：**
- 控制层只做认证与调度，不运行任何科学代码，不读取数据和结果；science.impf.ai 公共站点的服务器永远不计算。
- 持久队列；CPU 与 GPU 工作节点运行与 Runner 相同的 Python 实现（固定的已审版本），缺少引擎即失败，绝不替换。
- 任务沿用 Runner 的任务类型、参数、五种状态、进度、`stages.json`、`job_receipt.json` 与 `tcmstudio.receipt/1` 收据；收据的
  `where` 写明远程环境，绝不写成本机。
- 数据集按登记的键与版本引用，只接受校验和一致的副本或经同一套校验传输获取，不接受任意 URL；产物按项目存储，只能通过短时
  有效的签名链接下载；可取消、只对基础设施故障重试（检查失败本身是结果，不重试）、按人和项目设配额、记录实际资源消耗。
- 安全：身份认证；按项目隔离；向远程环境发送任何数据都需单独批准（设计中的新原因 `remote_compute`，键为 `remote@<主机>`；
  **路由器目前尚未实现它**，因为所有远程调用都在询问之前就以 `remote_not_configured` 失败）；受限数据集不代为复制；密钥只在服务器端；不允许任意
  shell，只运行白名单任务、固定摘要的镜像并设资源上限；全程审计；没有针对该环境与该项目的书面授权，绝不处理患者数据。
- `auto` 仍是先 Runner 后浏览器，远程环境只能由用户明确选择，不作为静默回退。

在注册了真实后端、实现并测试了 `remote_compute` 批准、对真实部署完成测试（提交、状态、取消、丢失节点后重试、配额拒绝、签名
链接过期、拒绝任意命令、拒绝未授权的患者数据），并对 GPU 节点做过已知答案测试之前，唯一正确的说法就是：已设计，未部署。
