# Browser expansion plan

The refined plan for moving more of TCMScience Studio's capability plane into a visitor's browser: the principles it
holds to, how it keeps the words for "it works in the browser" honest, where docking stands (Milestone A done,
Milestone B next), the parity tiers it accepts and why docking's search cannot be bit-reproducible, how the engine is
built reproducibly, and the planned route for each entry that still runs only on the runner. Written on 2026-10-20,
alongside the prepared-input docking cycle ([BROWSER_JOBS.md §3.3](BROWSER_JOBS.md#33-docking-from-prepared-structures)).

**A plan, not a record.** What is already done links to its evidence ([BROWSER_JOBS.md](BROWSER_JOBS.md),
[BROWSER_COMPUTE.md](BROWSER_COMPUTE.md)); everything else here is a plan, marked as one, and claims nothing it has not
measured. Of the catalog's 653 entries, 640 run in the browser today and thirteen only on the runner (classes 6–8 of
the [compatibility matrix](compat/summary.md)); this plan is about those thirteen, and about finishing docking. Status
words are those of [BROWSER_COMPUTE.md](BROWSER_COMPUTE.md).

## 1. Principles

**The reference is always the runner's Python.** A browser capability is the same `bioagent`/`psh` code the runner
imports, or an engine held to the native one; a browser result is only ever as good as its comparison with the native
command. A compatibility class says *where* an entry runs, never that its output was compared with native CPython.

**Five metrics, never collapsed into one.** "It works in the browser" hides at least five separate facts, and this
plan keeps them apart:

- **implementation** — the code exists, loads and is reachable from the router;
- **execution** — a real browser ran it (headless Chromium here or in CI; a tab on a person's machine is a stronger,
  separate claim, and almost nothing has it yet);
- **native-comparison** — its output was compared with the native command, *and how closely* (see the evidence levels
  below): a capability can be implemented and executed yet never compared;
- **deployed** — a record from the live site at <https://science.impf.ai>; nothing in this plan has one;
- **device evidence** — what a receipt can honestly assert about the run: `assurance: outputs_verified` only when two
  independent digests of every output agree, device evidence `verified` only when a verify stage ran and passed, and a
  GPU counted as `accelerated` only from a measurement on a *physical* adapter (a software renderer is never evidence of
  a GPU, so P3's physical-GPU acceptance stays open).

A capability can be implemented and executed but uncompared, or compared locally but never deployed, or verified on the
CPU but with no GPU evidence. Each is stated on its own; none is ever rounded up to the next.

**The evidence levels, named per output.** When a browser output is compared with the native one, the comparison is
reported at the strongest level it actually reached, and no stronger word is used for a weaker fact:

- **exact** — byte-identical text, or bit-identical float64 (0 units in the last place): e.g. the omics tables that
  match, the ADMET domain files, Vina's `score()`/`optimize()`;
- **bounded** — within a stated tolerance or a measured band, never called identical: e.g. the omics DE tables, the
  ADMET predictions through `expit`/`10 **`, docking's seeded search over seeds 1–8;
- **structural** — only the structure checked, not the numbers: e.g. the UMAP layout's neighbour-sharing;
- **not compared** — stated as such, with the reason: e.g. the UMAP figures.

**Refuse, never approximate.** An input a browser engine cannot reproduce exactly is refused, named, and left to the
runner — never silently approximated. The ADMET chemistry refuses a molecule it cannot standardise exactly
([§3.2](BROWSER_JOBS.md#32-admet)); docking refuses a receptor or ligand that is not already prepared, and an input
beyond its caps ([§3.3](BROWSER_JOBS.md#33-docking-from-prepared-structures)).

## 2. Docking: two milestones

Docking is split into two milestones so that the browser gains a real, verified capability before the harder,
preparation-heavy work is attempted.

**Milestone A — prepared-input docking (done).** `pipeline.dock` takes structures already prepared for AutoDock Vina
(a rigid-receptor PDBQT, a ligand PDBQT, and a site: `center`+`size` or a box around a `reference_ligand`) and runs them
as a browser job: the search in AutoDock Vina 1.2.7 compiled to WebAssembly, one Vina run per ligand in dedicated
workers on the CPU; the plan, analysis and checks `bioagent.docking.prepared` in a Pyodide worker. Its acceptance was
the exact tier plus the search band of §3; it was compared with native Vina 1.2.7 end to end
([BROWSER_JOBS.md §3.3](BROWSER_JOBS.md#33-docking-from-prepared-structures)) and passed locally and in `studio` CI.
Implemented and tested; not deployed-site verified, and no physical GPU is involved (docking uses no GPU).

**Milestone B — preparation from raw structures (next, not done).** The remaining, larger half: preparing a receptor
and ligands from raw inputs (a PDB/`PDB:`/`UniProt:` receptor, SMILES/CSV/SDF ligands) with Meeko and RDKit in the
browser, so a raw-structure run completes end to end without a runner; interaction analysis and SDF poses (they need
RDKit); and the relay download of a large structure (CI gives the inputs as project files today). Its acceptance will
mirror A's — the prepared PDBQT the browser writes compared byte for byte with the native preparation, then the same
docking band — but it is **not implemented**: those arguments are refused in the browser and left to the runner, and
the governed `dock-ligands` skill run stays on the runner.

## 3. Parity tiers, and why docking's search is not bit-reproducible

Docking is accepted at two tiers, and the separation is the point:

**Exact tier (score and optimize).** Vina's two deterministic operations are bit-identical to the native `vina` 1.2.7
package: `score()` and `optimize()` match its C++ values to 0 units in the last place of float64, and `optimize()`'s
pose text is byte-identical, on all 6 fixture ligands. The browser re-scores every top pose and this comparison is held
to exactness.

**Search tier (the Monte Carlo dock, a measured band).** A seeded `dock` is **not** bit-reproducible — and the reason
is not the WebAssembly build but the algorithm. Native Vina itself gives other poses for the same seed when only
glibc's FMA maths paths are turned off or a different maths library is linked, because the Monte Carlo search amplifies
a last-bit difference (Vina calls only `acos`, `exp`, `pow`, `sin`, `sincos` and `sqrt`). So docking is held to a
measured band, not to byte-for-byte equality: over seeds 1–8 at exhaustiveness 8 the browser's median per-ligand best
score sits inside native's own spread (median |Δbest| 0.002–0.077 kcal/mol for the WebAssembly build, against
0.002–0.086 for native without FMA and 0.002–0.046 for native linked against musl's maths; benzamidine redocks within
2 Å of the crystal pose on 8 of 8 seeds on every engine). The acceptance rule is on the median, never a single seed:
per ligand |median(best browser) − median(best native)| ≤ 0.10 kcal/mol over seeds 1–8, and the crystal redocking
succeeds (top pose ≤ 2 Å) on at least as many seeds in the browser as natively. A single seed's gap can exceed the band
(ibuprofen 0.151 kcal/mol at seed 42); the rule is on the median. The band was measured on the 3PTB fixture's 6
ligands and is not established for other receptors. This is why docking is never described as "byte-identical to the
runner": it is bit-identical where the maths is deterministic, and inside a measured band where it is not.

## 4. The reproducible engine build

An engine the browser runs must be reproducible from pinned inputs, or its output means nothing. AutoDock Vina 1.2.7 is
compiled to WebAssembly with Emscripten 4.0.23 and vendored at `studio/web/js/scientific/dock/vendor/vina-1.2.7.wasm`
(492,557 bytes, SHA-256 `94807d4aeb35…`), pinned in `boot.json`, its SHA-256 checked before anything instantiates it,
and cached by the service worker at that digest. `studio/scripts/build_vina_wasm.py` builds it from pinned,
SHA-256-checked inputs (the Emscripten release binaries, Vina's PyPI source distribution, Ubuntu 24.04's Boost 1.83
headers); `--check` rebuilds it and two independent builds are byte-identical. The port layer `studio/vina-wasm/`
replaces only system facilities, never Vina's own code: a serial `parallel.h` (Vina seeds its Monte Carlo tasks in
order and merges their outputs in task order, so the result does not depend on threads — native Vina gives identical
poses at `cpu=1` and at every core count), Boost thread/filesystem/log shims, and the embind binding `vina_wasm.cpp`
over Vina's own class API. The same discipline — pinned inputs, a byte-identical rebuild, a port that replaces only
system facilities — is the template for any future browser engine (a future Meeko/RDKit preparation path for
Milestone B included).

## 5. The entries that still run only on the runner

Thirteen entries run only on the runner today (classes 6–8). Each row below is a **plan** for where it could run in the
browser, not a claim that it does; several depend on work not started.

| Entry | Kind · class | Why it is on the runner now | Planned route (a plan, not done) |
|---|---|---|---|
| `skill.predict-admet` | skill · 7 | a governed skill runs in PSH's isolated child *process*, which a Web Worker cannot start | run the governed skill in-process through the same PSH broker and audit chain the research loop already uses in the browser (`isolate=False`, [§1.3](BROWSER_JOBS.md#13-governance-in-a-worker)), over the browser ADMET pipeline ([§3.2](BROWSER_JOBS.md#32-admet)); an SDF/`.mol` input stays on the runner |
| `skill.rnaseq-differential-expression` | skill · 7 | same in-process-governance gap; also wraps FASTQ paths | in-process governance over the browser count-matrix pipeline ([§3.1](BROWSER_JOBS.md#31-rna-seq-and-single-cell-from-count-tables)); FASTQ, PyDESeq2 stay on the runner |
| `skill.scrna-cell-atlas` | skill · 6 | governed skill, and large data | in-process governance over the browser count-table pipeline ([§3.1](BROWSER_JOBS.md#31-rna-seq-and-single-cell-from-count-tables)); Scanpy, scVI and inputs above the browser's limits stay on the runner |
| `skill.dock-ligands` | skill · 7 | governed skill wrapping docking | in-process governance over the browser prepared-docking pipeline ([§3.3](BROWSER_JOBS.md#33-docking-from-prepared-structures)); raw-structure preparation is Milestone B |
| `skill.predict-protein-structure` | skill · 7 | ESMFold/ColabFold are native or GPU engines | no browser engine planned; at most a stored-prediction lookup could run in-process, a prediction stays on the runner or a remote worker |
| `skill.retrieve-literature-evidence` | skill · 7 | queries external literature services | the public-database connectors already reach their APIs through the bounded gateway (class 1g); the governed skill could run in-process over those connectors; not built |
| `job.pipeline.fold` | job · 7 | a native structure-prediction engine (and a GPU for the larger models) | a remote HPC worker ([REMOTE_COMPUTE.md](REMOTE_COMPUTE.md), designed, not deployed); no browser engine planned |
| `job.tcmdb.build` | job · 8 | builds the TCM data-hub stores from raw sources onto the runner's filesystem | hub *queries* already run in the browser over packaged or imported stores (class 1); building a store from raw sources in OPFS is possible in principle (as the research snapshot build is) but not planned yet |
| `job.tcmdb.fetch` | job · 8 | fetches and updates hub data | the dataset relay could feed it as it feeds the research files; not built |
| `system.provider_call` | system · 8 | calls external MCP providers, which need a local process | stays on the runner (a browser cannot host an MCP stdio server) |
| `system.doctor` | system · 8 | inspects the local runner's processes, filesystem and installed engines | stays on the runner; the browser has its own capability probe (`system.capabilities`) for the browser side |
| `job.env.provision` | job · 8 | creates environments and installs native engines on the runner | stays on the runner; no browser analogue |
| `job.skill.run` | job · 8 | the generic process-isolated governed skill-run job | the in-process governance above could carry the specific browser-capable skills; the general process-isolated run stays on the runner |

## 6. What this plan does not claim

- **No deployed-site record.** Every browser run cited is headless Chromium, here or on CI's virtual machine; none is
  from the live site, and none is a tab on a person's machine.
- **No physical GPU.** Docking uses no GPU at all; the only GPU path (the ADMET domain search) has run only on a
  software adapter, so P3's physical-GPU acceptance stays open ([BROWSER_COMPUTE.md](BROWSER_COMPUTE.md)).
- **Docking Milestone B is not done.** Only prepared PDBQT structures dock in the browser; preparation from raw
  structures, interaction analysis, SDF poses and the relay download of a large structure are planned, not built.
- **The skill adapters are not built.** The in-process route for `skill.predict-admet`, `rnaseq-differential-expression`,
  `scrna-cell-atlas` and `dock-ligands` is a plan; today each governed skill runs on the runner.
- **Docking is not byte-identical to the runner.** Its `score`/`optimize` are bit-identical and its search is inside a
  measured band — never called identical ([§3](#3-parity-tiers-and-why-dockings-search-is-not-bit-reproducible)).

## 中文

将 TCMScience Studio 能力平面更多地搬入访客浏览器的细化计划：它所遵循的原则、如何把「在浏览器中可用」的措辞保持诚实、对接
的进展（里程碑 A 已完成，里程碑 B 为下一步）、所接受的一致性层级以及为何对接的搜索不可能逐位复现、引擎如何可复现地构建，以及
仍只在 Runner 上运行的每个条目可能的浏览器化路线。写于 2026-10-20，与「已准备输入」对接一轮同期
（[BROWSER_JOBS.md §3.3](BROWSER_JOBS.md#33-docking-from-prepared-structures)）。

**这是计划，不是记录。** 已完成的部分链接到其证据（[BROWSER_JOBS.md](BROWSER_JOBS.md)、[BROWSER_COMPUTE.md](BROWSER_COMPUTE.md)）；
其余都是计划，并标明为计划，绝不声称未经测量之事。目录的 653 个条目中，640 个今天在浏览器中运行，十三个仅在 Runner 上运行
（[兼容矩阵](compat/summary.md)的 6–8 类）；本计划针对这十三个，以及把对接做完。状态用词与
[BROWSER_COMPUTE.md](BROWSER_COMPUTE.md) 一致。

### 1. 原则

**参照物始终是 Runner 的 Python。** 浏览器能力就是 Runner 导入的同一套 `bioagent`/`psh` 代码，或一个与原生对齐的引擎；浏览器
结果的好坏，永远只取决于它与原生命令的比较。兼容类别说明条目在*哪里*运行，而不说明其输出已与原生 CPython 比较过。

**五个指标，绝不并为一个。**「在浏览器中可用」至少掩盖了五个各自独立的事实，本计划把它们分开：

- **实现**——代码存在、能加载、可被路由器触及；
- **执行**——真实浏览器运行过它（这里或 CI 中的无头 Chromium；用户自己机器上的标签页是更强、独立的主张，目前几乎没有能力具备）；
- **原生比较**——其输出已与原生命令比较，*且比到何种程度*（见下文证据层级）：一项能力可以既实现又执行却从未比较；
- **已部署**——来自 <https://science.impf.ai> 线上站点的记录；本计划中无一具备；
- **设备证据**——回执能诚实断言的内容：仅当每个输出的两次独立哈希一致时才 `outputs_verified`，仅当验证阶段运行且通过时设备
  证据才 `verified`，仅当在*实体*适配器上实测时 GPU 才记为 `accelerated`（软件渲染器绝不是 GPU 的证据，故 P3 的实体 GPU 验收
  仍未满足）。

一项能力可以已实现、已执行但未比较，或仅本地比较而从未部署，或在 CPU 上已验证而无 GPU 证据。每一点各自陈述，绝不向上取整。

**逐个输出点名的证据层级。** 当浏览器输出与原生比较时，按它实际达到的最强层级报告，绝不用更强的词形容更弱的事实：

- **精确**——逐字节一致，或 float64 逐位一致（末位 0 个单位）：如匹配的组学表、ADMET 适用域文件、Vina 的 `score()`/`optimize()`；
- **有界**——在给定容差或经测量带内，绝不称作一致：如组学 DE 表、经 `expit`/`10 **` 的 ADMET 预测、对接在种子 1–8 上的搜索；
- **结构**——只核对结构而非数值：如 UMAP 布局的邻居同簇比例；
- **未比较**——如实说明并给出原因：如 UMAP 图。

**拒绝，绝不近似。** 浏览器引擎不能精确复现的输入会被拒绝、点名并交给 Runner——绝不悄悄近似。ADMET 化学拒绝无法精确标准化的
分子（[§3.2](BROWSER_JOBS.md#32-admet)）；对接拒绝尚未准备好的受体或配体，以及超出上限的输入
（[§3.3](BROWSER_JOBS.md#33-docking-from-prepared-structures)）。

### 2. 对接：两个里程碑

对接分为两个里程碑，使浏览器在尝试更难、以准备为主的工作之前，先获得一项真实、已验证的能力。

**里程碑 A——已准备输入的对接（已完成）。** `pipeline.dock` 接受已经为 AutoDock Vina 准备好的结构（刚性受体 PDBQT、配体
PDBQT，以及位点：`center`+`size` 或围绕 `reference_ligand` 的盒子），作为浏览器任务运行：搜索由编译为 WebAssembly 的 AutoDock
Vina 1.2.7 承担，每个配体一次 Vina 运行，在 CPU 上的专用工作线程中进行；计划、分析与检查是 Pyodide 工作线程中的
`bioagent.docking.prepared`。其验收是 §3 的精确层加搜索带；已与原生 Vina 1.2.7 端到端比对
（[BROWSER_JOBS.md §3.3](BROWSER_JOBS.md#33-docking-from-prepared-structures)），本地与 `studio` CI 均通过。已实现、已测试；
未经线上站点验证，也不涉及实体 GPU（对接不使用 GPU）。

**里程碑 B——从原始结构出发的准备（下一步，尚未完成）。** 余下更大的一半：在浏览器中用 Meeko 与 RDKit 从原始输入（PDB/`PDB:`/
`UniProt:` 受体、SMILES/CSV/SDF 配体）准备受体与配体，使从原始结构出发的运行无需 Runner 即可端到端完成；相互作用分析与 SDF
位姿（需要 RDKit）；以及大型结构经中转的下载（如今 CI 以项目文件给出输入）。其验收将与 A 对称——浏览器写出的已准备 PDBQT 与
原生准备逐字节比对，再按同样的对接带——但**尚未实现**：这些参数在浏览器中被拒绝并交给 Runner，受治理的 `dock-ligands` Skill
运行也留在 Runner 上。

### 3. 一致性层级，以及对接搜索为何不可逐位复现

对接按两个层级验收，这一分离正是要点：

**精确层（score 与 optimize）。** Vina 的两个确定性运算与原生 `vina` 1.2.7 包逐位一致：`score()` 与 `optimize()` 与其 C++ 值在
float64 末位相差 0 个单位，`optimize()` 的位姿文本逐字节一致，在全部 6 个夹具配体上如此。浏览器对每个顶位姿重新打分，这一比较
按精确要求。

**搜索层（蒙特卡洛对接，经测量带）。** 带种子的 `dock`**不是**逐位可复现的——原因不在 WebAssembly 构建，而在算法本身。原生
Vina 本身在仅关闭 glibc 的 FMA 数学路径或链接另一数学库时，就会对相同种子给出不同位姿，因为蒙特卡洛搜索放大了末位差异（Vina
只调用 `acos`、`exp`、`pow`、`sin`、`sincos` 与 `sqrt`）。因此对接被约束在经测量带内，而非逐字节一致：在种子 1–8、穷举度 8 下，
浏览器每个配体最佳分数的中位数落在原生自身的散布之内（WebAssembly 构建的中位 |Δbest| 为 0.002–0.077 kcal/mol，对照原生去 FMA
的 0.002–0.086 与原生链接 musl 数学库的 0.002–0.046；苯甲脒在每个引擎上都在 8 个种子中的 8 个上再对接到晶体位姿的 2 Å 以内）。
验收规则针对中位数，而非单个种子：每个配体 |中位(浏览器 best) − 中位(原生 best)| ≤ 0.10 kcal/mol（种子 1–8），且晶体再对接在
浏览器中成功（顶位姿 ≤ 2 Å）的种子数不少于原生。单个种子的差距可超出该带（布洛芬在种子 42 为 0.151 kcal/mol）；规则针对中位数。
该带在 3PTB 夹具的 6 个配体上测得，对其他受体未经确立。这就是为什么对接从不被说成「与 Runner 逐字节一致」：在数学确定之处逐位
一致，在不确定之处落在经测量带内。

### 4. 可复现的引擎构建

浏览器运行的引擎必须可由锁定输入复现，否则其输出毫无意义。AutoDock Vina 1.2.7 用 Emscripten 4.0.23 编译为 WebAssembly，随站点
提供于 `studio/web/js/scientific/dock/vendor/vina-1.2.7.wasm`（492,557 字节，SHA-256 `94807d4aeb35…`），在 `boot.json` 中锁定，
实例化之前核对 SHA-256，并由 Service Worker 按该摘要缓存。`studio/scripts/build_vina_wasm.py` 由锁定且经 SHA-256 核对的输入
（Emscripten 发布的二进制、Vina 的 PyPI 源分发、Ubuntu 24.04 的 Boost 1.83 头文件）构建它；`--check` 重新构建，两次独立构建逐
字节一致。移植层 `studio/vina-wasm/` 只替换系统设施，绝不改 Vina 自己的代码：一个串行的 `parallel.h`（Vina 按顺序为其蒙特卡洛
任务播种并按任务顺序合并输出，故结果不依赖线程——原生 Vina 在 `cpu=1` 与任意核数下给出相同位姿）、Boost 的 thread/filesystem/log
垫片，以及在 Vina 自己的类 API 之上的 embind 绑定 `vina_wasm.cpp`。同样的做法——锁定输入、逐字节可复现的重建、只替换系统设施
的移植——是未来任何浏览器引擎（包括里程碑 B 的 Meeko/RDKit 准备路径）的模板。

### 5. 仍只在 Runner 上运行的条目

今天有十三个条目仅在 Runner 上运行（6–8 类）。下表每一行都是它可能在浏览器中运行的**计划**，不是它已经如此的声明；其中若干依赖
尚未开始的工作。

| 条目 | 种类 · 类别 | 目前为何在 Runner 上 | 计划路线（计划，非已完成） |
|---|---|---|---|
| `skill.predict-admet` | skill · 7 | 受治理的 Skill 在 PSH 的隔离子*进程*中运行，而 Web Worker 不能启动进程 | 用研究任务已在浏览器中采用的同一个 PSH 代理与审计链在同进程内运行（`isolate=False`，[§1.3](BROWSER_JOBS.md#13-governance-in-a-worker)），复用浏览器 ADMET 流程（[§3.2](BROWSER_JOBS.md#32-admet)）；SDF/`.mol` 输入仍在 Runner 上 |
| `skill.rnaseq-differential-expression` | skill · 7 | 同样的同进程治理缺口；也包装 FASTQ 路径 | 在同进程治理下复用浏览器计数矩阵流程（[§3.1](BROWSER_JOBS.md#31-rna-seq-and-single-cell-from-count-tables)）；FASTQ、PyDESeq2 仍在 Runner 上 |
| `skill.scrna-cell-atlas` | skill · 6 | 受治理的 Skill，且数据量大 | 在同进程治理下复用浏览器计数表流程（[§3.1](BROWSER_JOBS.md#31-rna-seq-and-single-cell-from-count-tables)）；Scanpy、scVI 与超出浏览器限值的输入仍在 Runner 上 |
| `skill.dock-ligands` | skill · 7 | 包装对接的受治理 Skill | 在同进程治理下复用浏览器已准备对接流程（[§3.3](BROWSER_JOBS.md#33-docking-from-prepared-structures)）；从原始结构出发的准备是里程碑 B |
| `skill.predict-protein-structure` | skill · 7 | ESMFold/ColabFold 是原生或 GPU 引擎 | 不计划浏览器引擎；至多同进程内做一次已存预测的查找，预测本身留在 Runner 或远程 worker |
| `skill.retrieve-literature-evidence` | skill · 7 | 查询外部文献服务 | 公共数据库连接器已经经受限网关触及其 API（1g 类）；受治理的 Skill 可在同进程内基于这些连接器运行；尚未实现 |
| `job.pipeline.fold` | job · 7 | 原生结构预测引擎（较大模型还需 GPU） | 远程 HPC worker（[REMOTE_COMPUTE.md](REMOTE_COMPUTE.md)，已设计，未部署）；不计划浏览器引擎 |
| `job.tcmdb.build` | job · 8 | 从原始源在 Runner 文件系统上构建 TCM 数据枢纽库 | 枢纽*查询*已在浏览器中基于打包或导入的库运行（1 类）；在 OPFS 中从原始源构建库原理上可行（如研究快照构建），但尚未计划 |
| `job.tcmdb.fetch` | job · 8 | 获取并更新枢纽数据 | 数据集中转可像喂研究文件一样喂它；尚未实现 |
| `system.provider_call` | system · 8 | 调用需要本地进程的外部 MCP 提供程序 | 留在 Runner 上（浏览器不能托管 MCP stdio 服务器） |
| `system.doctor` | system · 8 | 检查本机 Runner 的进程、文件系统与已安装引擎 | 留在 Runner 上；浏览器侧有自己的能力探针（`system.capabilities`） |
| `job.env.provision` | job · 8 | 在 Runner 上创建环境并安装原生引擎 | 留在 Runner 上；没有浏览器对应物 |
| `job.skill.run` | job · 8 | 通用的进程隔离受治理 Skill 运行任务 | 上述同进程治理可承载特定的、浏览器可运行的 Skill；通用的进程隔离运行留在 Runner 上 |

### 6. 本计划不声称之事

- **没有线上站点记录。** 所引用的每次浏览器运行都是无头 Chromium，在本机或 CI 虚拟机上；无一来自线上站点，也无一是用户机器上的标签页。
- **没有实体 GPU。** 对接完全不使用 GPU；唯一的 GPU 路径（ADMET 适用域检索）只在软件适配器上运行过，故 P3 的实体 GPU 验收仍未满足（[BROWSER_COMPUTE.md](BROWSER_COMPUTE.md)）。
- **对接里程碑 B 尚未完成。** 只有已准备好的 PDBQT 结构在浏览器中对接；从原始结构出发的准备、相互作用分析、SDF 位姿与大型结构经中转的下载都是计划，尚未实现。
- **Skill 适配器尚未构建。** `skill.predict-admet`、`rnaseq-differential-expression`、`scrna-cell-atlas` 与 `dock-ligands` 的同进程路线是计划；如今每个受治理的 Skill 都在 Runner 上运行。
- **对接与 Runner 不逐字节一致。** 其 `score`/`optimize` 逐位一致，其搜索落在经测量带内——绝不称作一致（[§3](#3-一致性层级以及对接搜索为何不可逐位复现)）。
