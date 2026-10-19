# TCMScience Studio

TCMScience 的研究工作台，网址 <https://science.impf.ai>。它是一个受治理的科学智能体：你提问，模型调用 TCMScience 的工具和
受治理的 Skill，每次工具运行都把**在哪里运行、用了什么证据、证据能支撑哪类主张、内核是否准予发布**写在结果旁边。
被拒绝的主张和调用会显示出来：拒绝是一种结果，不是故障。

服务器提供网页、默认模型 Tao-S1 中继和受限的数据库网关。**计算在你自己的设备上进行**：Python 工具在浏览器里
（Pyodide，WebAssembly）运行，或在你电脑上的本机 Runner 里运行（CPU 或 GPU，由你选择）。唯一的例外是你在 Runner 上打开
`allow_remote` 并逐次批准的任务（如把序列发往 ESM Atlas 做结构预测）。没有部署任何远程计算环境：计算目标设为「远程」时，
调用会失败（`unavailable`，原因 `remote_not_configured`；`catalog_search` 与 `capabilities_status` 仍由页面回答），
不会改到别处运行（[docs/REMOTE_COMPUTE.md](docs/REMOTE_COMPUTE.md)：已设计，未部署）。对话、项目、文件和密钥都保存在
你的浏览器里。

TCMScience 通过浏览器提供对已登记科学能力的访问。轻量工具经 Pyodide、WebAssembly 和部分 WebGPU 内核在本地运行；资源密集的
流程需要配置好的本机 Runner，或经授权的远程计算环境，而后者目前没有部署。

```
浏览器 ──同源──▶ science.impf.ai：网页 + /v1 Tao-S1 中继 + /api/sources 数据库网关
   │
   ├── 模型：Tao-S1（经中继）· 你自己的模型 API · 本地模型（Ollama / LM Studio / vLLM / llama.cpp）
   └── 工具：浏览器（Pyodide CPU；7 个序列工具有精确的 WASM SIMD / JS 内核）· 本机 Runner（CPU / GPU）
```

架构、接口与设计规范：[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) · [docs/CONTRACTS.md](docs/CONTRACTS.md) ·
[docs/DESIGN.md](docs/DESIGN.md)。

## 三种用法

| | 怎么开始 | 模型 | 工具在哪里运行 | 需要安装 |
|---|---|---|---|---|
| **1. 直接打开** | 打开 <https://science.impf.ai> | Tao-S1（默认，无需密钥） | 浏览器（Pyodide CPU；序列统计有精确的 WebAssembly SIMD 内核，适配器未被识别为软件渲染器时可用 WebGPU，从未在硬件 GPU 上运行） | 无 |
| **2. 加上本机 Runner** | `tcmstudio serve`，打开它打印的配对链接 | 同上，或你自己的 API | 本机 Runner：大文件与数据集构建、长任务、原生引擎、GPU；连接期间（计算目标为「自动」）工具与联网数据源都先由 Runner 运行，计算目标设为「浏览器」时联网数据源经网关调用 | Python ≥ 3.11 |
| **3. 完全本地** | Runner 提供网页 `http://127.0.0.1:8765/`，模型选本地模型 | 本机的 Ollama / LM Studio / vLLM / llama.cpp | 本机 Runner | Python + 本地模型服务 |

**1. 直接打开。** 第一次运行工具时，浏览器下载 Python 运行环境（Pyodide 314.0.7，来自 jsDelivr，约 9 MB，之后从缓存读取）
和 TCMScience 的代码包（约 1.9 MB，按 SHA-256 核对）。150 个原生工具、4 个固定版本的受治理 Skill、临床辨证都能在浏览器里
运行，运行的是与本机相同的 Python 代码（3 个香港数据查询工具需先导入其本地数据，临床辨证按需加载 numpy/scipy）。与本机
Python 逐字节对比过的只是其中一部分：7 个内核工具、`benjamini_hochberg`、`fisher_exact`、中医药查询工具、
排序的方剂检索，以及 4 个固定 Skill 中的 3 个（安全、名称规范化、证据检索）。这些对比由手动运行的运行时核对完成；CI 的端到端
测试另外核对一个受治理 Skill 的内容哈希与输出文件哈希。网络药理学种子 Skill 还没有被任何测试在 Pyodide 中运行过（逐条目的
情况见 [docs/compat/summary.md](docs/compat/summary.md)，其 validation 列来自目录中每个条目已提交的验证记录）。注册的在线数据库
可在批准联网后经同源网关调用。大型流程和原生引擎仍显示各自的依赖。

完整的数据驱动网络药理学研究已实现为浏览器任务：`research.snapshot` 与 `research.run`，Runner 的同一套 Python
在 Pyodide 任务工作线程中执行，原始文件保存在本站私有存储（OPFS）中（[docs/BROWSER_JOBS.md](docs/BROWSER_JOBS.md)）。它已在
已知答案（模拟世界）和解析器测试的夹具文件上检验；在浏览器中用真实的 17 个文件构建并研究、与原生运行逐个导出文件比对，
已在 `live-data` #18、#19 与 #21 中通过（运行 39251758718、39258060948 与 39286930354，提交 43918ac、53bdbb0 与 65fa6aa，2026-10-19，GitHub 运行器上的无头 Chromium，Pyodide 314.0.7 及其 pyarrow 22.0.0）：17 个原始文件导入本站私有存储，`research.snapshot` 与 `research.run` 作为浏览器任务成功运行，每个导出文件与同一批文件上的原生运行逐字节一致（五张表、`provenance.json`、`limitations.md`、`report.html`；`run_manifest.json` 除执行块外一致）。该次运行：4 味药材、1,792 个成分、370 个实测人类靶点、237 个节点 624 条边的 STRING 网络、检验 959 条 Reactome 通路，BH 校正后无一显著（因此真实数据上未抽取置换零分布；模拟世界检验了它）。种子 Skill
仍是有限的种子语料网络假说分析。

从计数矩阵出发的 RNA-seq 差异表达（`pipeline.rnaseq` 的计数矩阵模式）和从计数表出发的单细胞分析（`pipeline.scrna`，
内置分析与 Harmony）也已实现为浏览器任务：Runner 的同一套 Python 配 Pyodide 的 numpy 与 scipy，输入是项目里的文件，复制到
任务的私有存储并按记录的 SHA-256 核对（[docs/BROWSER_JOBS.md §3.1](docs/BROWSER_JOBS.md#31-rna-seq-and-single-cell-from-count-tables)）。
在两个已知答案参考上与原生命令逐个输出比对：逐字节一致、数值在给定容差内（最大用到容差的 0.133），或不比较并说明原因
（UMAP 布局与图；用户自己的运行不做比较）；显著基因、每个细胞的聚类与细胞类型完全相同，预设答案在浏览器写出的文件上成立。已在本地无头 Chromium
（Pyodide 314.0.7）中测试，并在 Studio 的 CI（`studio` #91，2026-10-19，使用 jsDelivr 上 Pyodide 的 numpy 与 scipy）中通过。从 FASTQ 出发的运行、PyDESeq2、Scanpy、scVI、10x HDF5 与 `.h5ad`，以及
超过 6,000 个细胞、单个表超过 66,000,000 个细胞 × 基因或计数矩阵超过 10,000,000 个基因 × 样本的输入（限值依据 Node 下
Pyodide 314.0.7 中的运行设定，不是在浏览器标签页中测得），会被拒绝并说明原因，需要本机 Runner。

提供 WebNN 能力检测；只有具备经验证的执行提供程序和硬件执行证据的工作负载，才支持
真正的 NPU 加速，目前没有任何工作负载具备这两者。

**2. 加上本机 Runner。** Runner 连接后，工具都在它上面运行（`计算 → 自动`）：本机 CPython，项目各自的持久审计链，你的设备。
它还能运行需本地 Python/MCP 进程的第三方封装、从 FASTQ 出发的 RNA-seq、Scanpy / scVI 单细胞、结构预测等后台任务和原生 GPU 引擎。
每个项目第一次在 Runner 上运行工具、每个任务、每次联网，都先请你批准（允许一次 / 本项目允许 / 拒绝）。

**3. 完全本地。** Runner 自带网页：打开 `http://127.0.0.1:8765/`，在「设置 → 模型」里选本地模型，模型调用经 Runner 转发到
本机的模型服务，工具在 Runner 上运行，什么都不离开你的电脑。（Tao-S1 需要联网；离线时用本地模型。）

## 安装和运行本机 Runner

需要 Python 3.11 或更新版本。先建一个虚拟环境，再在 TCMScience 仓库的根目录安装（Ubuntu 23.04+、Debian 12 和 Homebrew
的 Python 不允许直接 `pip install` 到系统里，会报 `externally-managed-environment`）：

```bash
python3 -m venv ~/.tcmscience/venv        # Debian/Ubuntu 报 ensurepip 不可用时，先 sudo apt install python3-venv
. ~/.tcmscience/venv/bin/activate         # Windows（PowerShell）：~\.tcmscience\venv\Scripts\Activate.ps1
python -m pip install -e PSH-Harness -e BioScience-Harness -e studio/runner
tcmstudio serve
```

`tcmstudio` 命令装在这个虚拟环境里：在新的终端里先再执行一次上面的 `activate` 那一行，再运行 `tcmstudio serve`。

它打印本机地址、配对链接和配对令牌，并在浏览器中打开配对链接 `https://science.impf.ai/#pair=…`；点「连接」即可。网页记住
地址和令牌，并立即把令牌从地址栏删掉。Runner 提供的网页另有一条本机链接 `http://127.0.0.1:<端口>/#pair=…`。

| 选项 | 作用 |
|---|---|
| `--port 8765` | 端口（默认 8765；`0` 由系统选择）。Tao-S1 只接受 `https://science.impf.ai` 与 `http://127.0.0.1:8765` / `http://localhost:8765` 上的页面；其他端口上的本机页面请用自己的模型 |
| `--home DIR` | 主目录（默认 `~/.tcmscience/studio`）：设置与令牌 `runner.json`、任务、上传、各项目的审计链、数据 |
| `--device auto\|cpu\|cuda:N\|rocm:N\|mps` | 任务用的设备（会保存）。`auto` 选可用的 GPU，没有就用 CPU |
| `--threads N` · `--max-jobs N` | 每个任务的 CPU 线程数、同时运行的任务数（会保存） |
| `--no-network` | 关闭 Runner 自己的联网开关（默认开启；会保存，`--network` 重新打开）；每个项目的「联网」开关和每个新主机的批准仍然有效 |
| `--allow-origin URL` · `--allow-host HOST` | 允许另一个页面来源；允许模型代理转发到另一个模型服务 |
| `--no-token` | 不要求配对令牌（只在绑定本机地址时可用） |
| `--web DIR` · `--no-browser` · `--quiet` | 提供另一份网页（如 `studio/_site`）；不打开浏览器；不逐条记录请求 |

**GPU。** 设备可以在命令行选，也可以在网页的「设置 → 计算」里选；那里列出 Runner 检测到的 CPU、CUDA / ROCm GPU（名称、
显存）、Apple MPS，以及已安装的计算引擎和安装命令。能用 GPU 的是结构预测（ESMFold）和 `predict-protein-structure`；同一时间
最多一个任务用 GPU，其余任务以 `CUDA_VISIBLE_DEVICES=""` 在 CPU 上运行。单细胞流程的 scVI 按设计在 CPU 上运行。可选引擎
（pydeseq2、salmon、scanpy、vina、ESMFold 等）没有安装时，相关条目显示「缺少依赖」，不会被替换。

**配对与安全。** Runner 只监听 `127.0.0.1`，检查 `Host`（防 DNS 重绑定），只接受 science.impf.ai、本机页面和
`--allow-origin` 列出的来源，`/api/*` 需要配对令牌。Chrome 第一次连接本机地址时会询问「本地网络访问」，请允许。
同一个 `--home` 只能运行一个 Runner；任务在 Runner 重启后继续，由新进程重新接管。

## 使用本地模型

在「设置 → 模型」中填写地址和模型名，按「测试连接」检查工具调用是否可用。浏览器直接调用本地服务需要它允许
`https://science.impf.ai` 跨域；连接了本机 Runner 时，本地模型的调用自动经 Runner 转发，不需要跨域设置。

| 服务 | 地址 | 直接调用需要 | 工具调用 |
|---|---|---|---|
| Ollama | `http://127.0.0.1:11434/v1` | `OLLAMA_ORIGINS=https://science.impf.ai ollama serve`（macOS 应用：`launchctl setenv OLLAMA_ORIGINS https://science.impf.ai` 后重启） | 选支持工具的模型，如 qwen3 |
| LM Studio | `http://127.0.0.1:1234/v1` | Developer → Server Settings → Enable CORS | 选支持工具的模型 |
| vLLM | `http://127.0.0.1:8000/v1` | `vllm serve … --allowed-origins '["https://science.impf.ai"]'` | `--enable-auto-tool-choice --tool-call-parser <解析器>` |
| llama.cpp | `http://127.0.0.1:8080/v1` | `llama-server` 默认允许跨域 | 以 `--jinja` 启动 |
| 其他 OpenAI / Anthropic 兼容服务 | 「添加 OpenAI 兼容接口」或「添加 Anthropic 兼容接口」 | 该服务允许跨域，或经 Runner 转发 | — |

你自己的云端 API（OpenAI、Anthropic、DeepSeek、通义千问、Kimi、智谱、SiliconFlow、OpenRouter）同样在这里填写密钥；密钥只保存
在这个浏览器中，调用直接发往该服务，不经过 science.impf.ai。

## 在手机上使用

手机浏览器打开 <https://science.impf.ai> 即可，界面按手机布局显示（侧栏、检查器都改为从边缘滑出的面板）。

- **用手机自己的算力。** 没有本机 Runner 时，工具在手机浏览器里运行：Pyodide（WebAssembly）用手机的 CPU 执行真实的
  TCMScience Python 代码。150 个原生工具、4 个固定版本的受治理 Skill 和临床辨证都可以运行，代码与电脑上相同。自动测试
  （`e2e/mobile.spec.mjs`，Chromium 的手机模拟，CPU 降速 4 倍）核对的是一个受治理 Skill 的内容哈希与本机 Python 相同；
  它记录冷启动后第一次调用的耗时，但只作注释，不作断言，也没有提交任何测量记录。第一次运行要下载约 11 MB（之后从缓存读取）。
- **本机加速，结果不变。** 序列比对（全局、局部、蛋白）和编辑距离由 JavaScript 内核计算；序列距离矩阵、汉明距离和
  GC 含量的整数计数由 WebAssembly SIMD 内核计算；只有适配器未被识别为软件渲染器（实体适配器，或无法识别的适配器，后者从不算作 GPU）、且调度器预计
  GPU 更快时才改用 WebGPU（WebGPU 只在软件适配器上测试过，从未在硬件 GPU 上运行，速度未经测量）。原有 Python 继续处理公式、取整和输出，并在使用前核对每个内核
  结果，输出与原始 Python 逐字节相同（374 个一致性用例，在 Studio 的 CI 中运行）。2026-10-10 的测量中，序列比对和编辑距离比浏览器里
  的原始 Python 快 7–43 倍，也快于本机 CPython（[测量](docs/BROWSER_COMPUTE.md#9-performance)）。不支持、设备丢失或规模超限时回退到
  CPU 内核，再回退到 Python。「设置 → 计算」可选自动、仅 CPU 或仅原始 Python。普通访问不会启动计算。
- **离线可用。** 第一次访问后，网页、Python 运行环境和字体由 Service Worker 按带 SHA-256 的清单缓存，断网时仍能打开页面、
  运行浏览器工具；用过的数据文件（如方剂表）也保存在本地并经校验。新版本下载好后提示重新载入，不会强制切换；网址加
  `?nosw` 可移除 Service Worker 及其缓存。模型调用和数据库查询仍需联网。
- **手机免安装数据库访问。** 注册的数据库查询经同源网关转发，仍需项目联网开关和批准。完整方剂表按需下载并校验，保留全部
  84,294 条来源记录；`tcmdb.formulas` 加 `rank: true` 可多词检索并按相关度排序，药名同时按药材表中的其他写法查找（如
  黄芪 / 黄耆），每条记录注明如何匹配。查药材（`tcm_herb`）会列出完整方剂库中含此药的方剂（`tcmdb.formulas` 加 `herb`
  可翻页）；查证候（`tcm_syndrome`）会列出功效中提及该证的方剂（只查功效，「注意」中的禁忌不算主治；`tcmdb.formulas` 加
  `indication` 可翻页）；配伍核查遇到种子语料以外的方剂，若完整方剂库只有一个版本，就按其组成逐味核查。方剂库无法加载时，
  结果会注明「仅检索种子语料」。数据源目录逐条标明在本机的就绪状态（仅收录、在线可查、可下载、部分下载、已下载、
  可查询）和下一步；NPASS、CMAUP、LOTUS 可在 Runner 上下载并构建，得到成分—靶点实测活性和植物—成分记录。`literature_search`
  也可检索 ClinicalTrials.gov 的注册试验。受治理的 Skill 代码经过审核锁定，仍基于种子语料运行。大文件、后台任务和依赖本地进程的第三方工具仍需
  Runner。
- **测试范围。** 自动测试用的是 Chromium 的手机模拟（Android 视口、触控、降速 CPU），还没有在真机上测过 iOS Safari；
  iPhone 上的 Safari 也支持 WebAssembly，内存较小的机型首次加载可能较慢。

## 隐私：什么会、什么不会到达服务器

- **保存在本地的**：项目、会话、上传的文件（下一条所说的小文本文件除外）、本地工具结果、审计链、你的模型
  API 密钥。它们保存在浏览器的 IndexedDB / localStorage 里，或在你电脑上的 Runner 主目录里。清除网站数据会删除浏览器里的
  部分；需要保留时请在「设置 → 通用」中导出项目。
- **项目里的小文本文件会发给模型**：项目中 8 KB 以下的文本文件（合计最多 64 KB）附在系统提示里，随本项目的每条消息发给
  所选的模型；其余文件只把名称、大小和哈希告诉模型。使用 Tao-S1 时，这些文本和消息一样经 science.impf.ai 的中继转给模型
  服务（中继不保存）。文件列表里标着「发送给模型」的就是这些文件；不想发送的，请不要放进项目。
- **使用 Tao-S1 时**：发给模型的内容（你的消息、系统提示、模型读到的工具结果文本）经中继转发给上游模型服务。中继不保存这些
  内容，Worker 的调用日志关闭；限额按访客 IP 的加盐哈希计数，IP 本身不存。
- **使用你自己的 API 时**：模型调用直接从浏览器（或经本机 Runner）发往该服务，受该服务的条款约束。
- **使用本地模型时**：模型调用不离开你的电脑。
- 联网数据源只在你为项目打开「联网」并批准后才访问，访问的主机会在批准卡片上列出。
- 浏览器数据库查询的 URL、查询正文和必要的数据库请求头会经 science.impf.ai 网关转发到对应数据源，返回数据也经过网关。
  网关不保存查询或响应，不接收环境 Cookie，不将数据库请求转发给模型；模型读取的工具结果仍按所选模型路径发送。
- **对话记录（需要你同意）**：第一次提问时，网页询问能否记录对话以测试和改进 Studio；默认不记录，不同意也照常使用。同意后，
  每轮的问答（证件号、电话、邮箱、密钥等已隐去）与工具名、耗时、token 等统计保存在 science.impf.ai，只有站点管理员能看，
  90 天后自动删除。含上传文件或项目文件、用到临床工具、或含可识别个人信息的对话从不记录。可随时在「设置 → 隐私」撤回并删除。
- **匿名使用统计**：访问量、所用功能、工具与模型的调用次数、耗时与出错情况、所在国家或地区；不含对话内容，访客按天以每天更换的
  随机盐去重，无法跨日认出你。可在「设置 → 隐私」关闭；浏览器开启「请勿追踪」或 GPC 时自动关闭。
- 详细字段、保存期限与管理方式见 [docs/TELEMETRY.md](docs/TELEMETRY.md)。

## Studio 不声称什么

- 它不提供临床建议。临床辨证的输出是给持证医师的草稿，签署是医师本人的行为，永远不是工具。
- 种子语料是示范性的：没有记载不等于安全，未显著不等于无关；古籍记载是出处，不是临床证据；预测不等于实测，网络药理最多支持
  机制**假说**。
- 「已准予发布」只表示内核的六项发布检查通过，不表示结论正确；证据种类是类型，不是等级。
- 浏览器里的 Python 在 CPU 上单线程运行；本机内核只覆盖 7 个序列工具（比对、编辑距离和三项整数计数），WebGPU 只用于
  其中的整数计数，不代表所有 Python 引擎都能使用 GPU。提供 WebNN 能力检测，但浏览器能创建 WebNN 的「npu」上下文不等于有
  NPU：只有具备经验证的执行提供程序和硬件执行证据的工作负载才支持真正的 NPU 加速，目前没有这样的工作负载，Studio 不使用也
  不声称 NPU 加速。
- 浏览器模式的网络药理学只是有限的种子语料网络假说流程，不是完整的数据驱动分析；没有部署任何远程计算环境。
- 完整方剂表是原始来源记录，授权状态未声明；不把这些记录自动升级为临床知识、安全判断或完整靶点网络。
- 可复现的是输入哈希、Skill 内容哈希和每个输出文件的 SHA-256；整份结果的哈希含运行编号、时间和审计链位置，每次都不同。
- 中继的来源检查只防止网页被别处盗用，不是身份认证；限额按 IP 计，同一出口的访客（如一个实验室）共用。

## 开发

```bash
# 单元测试（零运行时依赖；开发依赖仅用于测试）
node --test studio/edge/test/*.test.js                       # Worker：中继、限额、静态文件头
python3 -m pytest -q studio/runner/tests                     # tcmstudio：目录、分发、Runner HTTP API、任务、网页构建
cd studio && npm install && npm test                         # 网页核心、界面、浏览器运行时（脚本化的 Worker）

# 构建网页（studio/_site，已被 git 忽略）
python3 studio/scripts/build_web.py --out studio/_site

# 本地运行
tcmstudio serve --web studio/_site                           # Runner 提供构建好的网页
cd studio/edge && npm run mock                               # 上游模型的模拟服务（另开一个终端）
cp studio/edge/.dev.vars.example studio/edge/.dev.vars && (cd studio/edge && npm run dev)   # Worker：https://localhost:8787
```

**端到端测试**（`studio/e2e`，Playwright 1.56 + Chromium）：

```bash
cd studio && npm install && npm run test:e2e
```

| 用例 | 内容 |
|---|---|
| `runner.spec.mjs` | 真实 Runner 提供网页并配对；自定义模型（脚本化的模拟模型 `e2e/mock-llm.mjs`）调用 `tcm_safety_report`，Runner 执行受治理 Skill；工具卡片（本机 Runner）、主张与产物卡片、发布状态、引用、检查器五页、英文界面；刷新后对话仍在；编辑问题产生分支；中途停止；中文文件名的文件发送到 Runner，名称与哈希不变 |
| `browser.spec.mjs` | 无 Runner：Pyodide 从 jsDelivr 启动，同一问题在浏览器中运行，Skill 内容哈希与输出文件哈希与本机 Python 相同（CDN 不可达时：在本地跳过并说明原因；在 CI 中失败） |
| `browser-access.spec.mjs` | 浏览器中的 Python 读取完整方剂表；联网关闭时数据库调用被拒绝且不发出请求，开启后经同源网关（真实的网关代码，上游为模拟）调用 `connector.mygene.gene`；距离矩阵在 `auto` 加速（WebGPU 或 WebAssembly SIMD，由运行时选择；CI 的无头 Chromium 至多有软件适配器，运行时会拒绝它，因此在 CPU 上算）、CPU 内核与参考 Python 三者结果一致（CDN 不可达时同上） |
| `approvals.spec.mjs` | `run_pipeline` 的批准卡片：拒绝 → 模型读到 refused；「本项目允许」写入项目并在刷新后仍然有效；后台任务的卡片跟随 Runner 事件直到收集完成，任务文件从 Runner 取来预览，模型用 `job_status` 跟进；联网：项目未开启时不调用也不询问，开启后批准卡片列出主机，再由 Runner 自己的联网开关决定 |
| `relay.spec.mjs` | `wrangler dev` 运行 `studio/edge`，上游指向模拟服务：默认模型 Tao-S1 流式回答，思考格式往返改名，身份问题关闭思考，页面、存储和中继回复都不出现上游名称；Tao-S1 调用工具并在浏览器中运行。Worker 启动失败就是失败；只有在 CI 之外、npm 源不可达（取不到 wrangler）时才跳过 |
| `mobile.spec.mjs` | 手机（390×844、触控、CPU 降速 4 倍）、无 Runner：受治理 Skill 在手机浏览器的 Pyodide 中运行，内容哈希与本机 Python 相同，页面无横向滚动（CDN 不可达时同上） |
| `pages.spec.mjs` | 首次引导、设置（经表单添加并测试自定义模型）、工具目录、关于；手机视口 390×844；深色主题；无障碍基本检查（可访问名称、地标、焦点、未翻译的键） |
| `a11y.spec.mjs` | 键盘与读屏：跳转链接按下后焦点到目标、路由不变；输入法组字时的 Enter 不发送、Esc 不停止回答；首次发送、停止、批准之后以及菜单、抽屉按 Esc 之后焦点的去向；实时区域的播报；axe-core 检查主要页面（浅色/深色、桌面/手机）与受治理的回答，严重（serious/critical）问题即失败 |
| `pipeline.spec.mjs` | 不开浏览器，检查 `.github/workflows/studio.yml`：网页打包的 Harness 源码改动也会触发；`RELAY = "off"`（持久暂停）时部署后的检查不要求 Tao-S1 回答；CI 中 Worker 启动失败、Pyodide 的 CDN 不可达都不会被当作跳过 |
| `telemetry.spec.mjs` | 真实 Worker（`wrangler dev`）的遥测：征询同意的流程、无论如何都不记录的内容、遥测故障不影响对话、管理员仪表板（登录、双语、手机、删除、导出、退出） |

Chromium 依次取 `$CHROMIUM_PATH`、`$PLAYWRIGHT_BROWSERS_PATH`（或 `/opt/pw-browsers`）中的 `chromium-1194`、Playwright 自带的
（`npx playwright install chromium`）。`$STUDIO_SITE` 指向已构建的网页时直接使用，否则先构建一次。截图保存在
`$STUDIO_E2E_TMP/screens`（默认 `studio/e2e/test-results/screens`）。另有浏览器运行时的逐项核对：
`npm run test:runtime`（真实 Pyodide 中约 20 个调用的信封与本机 Python 逐个比较，名单见
[docs/BROWSER_COMPUTE.md §10](docs/BROWSER_COMPUTE.md#10-validation)）。它需要能访问 Pyodide 的 CDN（或用 `--pyodide-dir`
提供本地副本），**只能手动运行，不在 CI 中**。

## 部署

science.impf.ai 是一个 Cloudflare Worker（`studio/edge`）：网页作为静态资源，`/v1/*` 是 Tao-S1 中继。部署由
`.github/workflows/studio.yml` 在 `main` 分支上完成（测试 → 构建 → 部署，仓库有 Cloudflare 机密时才部署）。`studio/`、
工作流本身，或网页打包进去的 Harness 代码（`PSH-Harness/src`、`BioScience-Harness` 的 `src`/`skills`/`registry` 及两者的安装
文件）有改动时，它都会运行。一次性的配置步骤见
[edge/SETUP.md](edge/SETUP.md)，Worker 的行为见 [edge/README.md](edge/README.md)。

---

## English

**TCMScience Studio** is the web workbench for TCMScience at <https://science.impf.ai>: a governed scientific agent whose
every tool run shows where it ran, what evidence it used, which kinds of claim that evidence licenses, and whether the
kernel authorized the release. Refusals are shown as results.

The server serves the page, relays Tao-S1 and forwards approved database queries. **Computation runs on your machine**: in the
browser (Pyodide) or on the local runner (`tcmstudio serve`, CPU or GPU). The one exception is a runner job you allow to call
a third-party service (`allow_remote`, approved per call, for example structure prediction on ESM Atlas). No remote compute
environment is deployed: under compute set to Remote every call fails (`unavailable`, reason `remote_not_configured`; `catalog_search` and
`capabilities_status` are still answered by the page) and is never run elsewhere
([docs/REMOTE_COMPUTE.md](docs/REMOTE_COMPUTE.md): designed, not deployed). Conversations, projects, files and keys stay in
your browser; the server keeps a conversation only if you agree to the recording notice (and never one with files, a
clinical tool or identifying details), and counts anonymous usage you can switch off ([docs/TELEMETRY.md](docs/TELEMETRY.md)).

- **Just open** science.impf.ai: Tao-S1, Python tools in the browser, nothing to install. TCMScience provides browser-based
  access to its registered scientific capabilities. Lightweight tools execute locally through Pyodide, WebAssembly, and
  selected WebGPU kernels. Resource-intensive pipelines require a configured local Runner or an authorized remote compute
  environment, and none of the latter is deployed. The browser runs the same Python code as the runner; its outputs were
  compared with native CPython for a named subset only (the seven kernel tools, `benjamini_hochberg`, `fisher_exact`, the
  TCM lookups, ranked formula search and three of the four pinned skills, by the manual runtime check; CI's end-to-end specs
  compare one governed skill's content and output hashes). Per entry: [docs/compat/summary.md](docs/compat/summary.md), whose
  validation column is each entry's committed verification record.
- **Network pharmacology in the browser.** The full data-driven research loop runs as browser jobs: `research.snapshot`
  (the 17 raw files downloaded, relayed or imported into the site's private storage, verified and built into snapshots)
  and `research.run` (the sixteen stages and nine exports), the runner's own Python in Pyodide job workers. On the planted
  world a job worker writes the native bytes; the real 葛根芩连汤 comparison in the live-data workflow passed in `live-data` #18, #19 and #21 (runs 39251758718, 39258060948 and 39286930354, commits 43918ac, 53bdbb0 and 65fa6aa, 2026-10-19, headless Chromium on GitHub's runner, Pyodide 314.0.7 with its pyarrow 22.0.0): the 17 raw files imported into the site's private storage, `research.snapshot` and `research.run` succeeded as browser jobs, and every export was the native run's bytes on the same files (the five tables, `provenance.json`, `limitations.md`, `report.html`; `run_manifest.json` apart from its execution block). That run: 4 herbs, 1,792 constituents, 370 measured human targets, a 237-node, 624-edge STRING network, 959 Reactome pathways tested, none significant after BH (so no permutation null was drawn there; the planted world exercises it)
  ([docs/BROWSER_JOBS.md](docs/BROWSER_JOBS.md)). The seed skill remains a limited seed-corpus network hypothesis
  workflow.
- **RNA-seq and single-cell in the browser.** RNA-seq differential expression from a count matrix (`pipeline.rnaseq` in
  its count-matrix mode) and single-cell analysis of count tables (`pipeline.scrna`, the built-in analysis and Harmony)
  also run as browser jobs: the runner's Python with Pyodide's numpy and scipy, the inputs files of the project, copied
  into the job's private storage and checked against their recorded SHA-256
  ([docs/BROWSER_JOBS.md §3.1](docs/BROWSER_JOBS.md#31-rna-seq-and-single-cell-from-count-tables)). On two known-answer
  references, compared output by output with the native command: identical bytes, numbers within a stated tolerance
  (at most 0.133 of it), or not compared, with the reason (the UMAP layout and figures; a person's own run is not
  compared); the genes called and each
  cell's cluster and cell type identical, the planted answers found on the browser's files. Tested locally in headless
  Chromium (Pyodide 314.0.7), and passed in Studio's CI (`studio` #91, 2026-10-19, Pyodide's numpy and scipy from
  jsDelivr). A FASTQ run, PyDESeq2, Scanpy, scVI, 10x HDF5 and `.h5ad`, and
  inputs above 6,000 cells, a table above 66,000,000 cells × genes or a count matrix above 10,000,000 genes × samples
  (limits set from runs in Pyodide 314.0.7 under Node, not in a browser tab) are refused with the reason and need the local
  runner.
- **NPU.** WebNN capability detection is available. Actual NPU acceleration is supported only for workloads with a verified
  execution provider and hardware-backed execution evidence; no workload has either today.
- **Add the local runner**: in a virtual environment (Ubuntu 23.04+, Debian 12 and Homebrew Python refuse a
  system-wide `pip install` with `externally-managed-environment`; on Debian/Ubuntu `python3 -m venv` first needs
  `sudo apt install python3-venv`): `python3 -m venv ~/.tcmscience/venv && . ~/.tcmscience/venv/bin/activate`
  (Windows PowerShell: `~\.tcmscience\venv\Scripts\Activate.ps1`), then, from the repository root,
  `python -m pip install -e PSH-Harness -e BioScience-Harness -e studio/runner && tcmstudio serve`, and open the pairing
  link it prints. `tcmstudio` lives in that environment: activate it again in a new terminal. While connected, tools
  run on the runner (native Python, durable per-project audit chains, native third-party processes, jobs, GPU with
  `--device cuda:0` etc.).
- **Fully local**: open the runner's own page `http://127.0.0.1:8765/` and pick a local model (Ollama, LM Studio, vLLM,
  llama.cpp); model calls go through the runner, so no CORS setup is needed.

Privacy: project state and files remain local. Approved browser database queries, request bodies, supported database
headers and responses pass through science.impf.ai to the registered source; the gateway does not retain these payloads.
With Tao-S1, what the model reads is relayed to the upstream model service and not stored by the relay; with your own API it
goes to that provider; with a local model it stays on your computer. Conversation records are opt-in (off until you agree,
withdrawable with deletion under Settings → Privacy, kept 90 days, readable only by the site's administrators); anonymous
usage statistics hold no conversation content and cannot follow a visitor from one day to the next. Studio gives no clinical advice: clinic outputs are drafts for a licensed practitioner, no record
is not evidence of safety, and predicted is not measured.

Tests: `node --test studio/edge/test/*.test.js`, `python3 -m pytest -q studio/runner/tests`, `cd studio && npm test`,
`cd studio && npm run test:e2e` (Playwright: the real runner, Pyodide from jsDelivr, `wrangler dev` of the Worker; the specs
that need Pyodide skip with the reason when its CDN is unreachable, and fail in CI). `npm run test:runtime` compares about
twenty envelopes from real Pyodide with native CPython; it is run by hand, not in CI.
Deployment: [edge/SETUP.md](edge/SETUP.md).

Browser database access: [docs/BROWSER_ACCESS.md](docs/BROWSER_ACCESS.md), and each connector operation by verification level:
[docs/compat/connectors.md](docs/compat/connectors.md). Local computation in the browser (exact JavaScript and WebAssembly SIMD
kernels for seven sequence tools; WebGPU counts where the scheduler expects them to be faster, on any adapter not identified
as a software renderer (a physical one, or an unidentified one, which is never counted as a GPU), never yet run on a
hardware GPU; offline use; what was measured and what is only planned): [docs/BROWSER_COMPUTE.md](docs/BROWSER_COMPUTE.md).
Remote compute: [docs/REMOTE_COMPUTE.md](docs/REMOTE_COMPUTE.md) (designed, not deployed).
