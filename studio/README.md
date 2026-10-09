# TCMScience Studio

TCMScience 的研究工作台，网址 <https://science.impf.ai>。它是一个受治理的科学智能体：你提问，模型调用 TCMScience 的工具和
受治理的 Skill，每次工具运行都把**在哪里运行、用了什么证据、证据能支撑哪类主张、内核是否准予发布**写在结果旁边。
被拒绝的主张和调用会显示出来：拒绝是一种结果，不是故障。

服务器提供网页、默认模型 Tao-S1 中继和受限的数据库网关。**计算在你自己的设备上进行**：Python 工具在浏览器里
（Pyodide，WebAssembly）运行，或在你电脑上的本机 Runner 里运行（CPU 或 GPU，由你选择）。对话、项目、文件和密钥都保存在
你的浏览器里。

```
浏览器 ──同源──▶ science.impf.ai：网页 + /v1 Tao-S1 中继 + /api/sources 数据库网关
   │
   ├── 模型：Tao-S1（经中继）· 你自己的模型 API · 本地模型（Ollama / LM Studio / vLLM / llama.cpp）
   └── 工具：浏览器（Pyodide CPU / 兼容工具 WebGPU）· 本机 Runner（CPU / GPU）
```

架构、接口与设计规范：[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) · [docs/CONTRACTS.md](docs/CONTRACTS.md) ·
[docs/DESIGN.md](docs/DESIGN.md)。

## 三种用法

| | 怎么开始 | 模型 | 工具在哪里运行 | 需要安装 |
|---|---|---|---|---|
| **1. 直接打开** | 打开 <https://science.impf.ai> | Tao-S1（默认，无需密钥） | 浏览器（Pyodide CPU，兼容序列统计使用 WebGPU） | 无 |
| **2. 加上本机 Runner** | `tcmstudio serve`，打开它打印的配对链接 | 同上，或你自己的 API | 本机 Runner：联网数据源、中医药数据枢纽、长任务、GPU | Python ≥ 3.11 |
| **3. 完全本地** | Runner 提供网页 `http://127.0.0.1:8765/`，模型选本地模型 | 本机的 Ollama / LM Studio / vLLM / llama.cpp | 本机 Runner | Python + 本地模型服务 |

**1. 直接打开。** 第一次运行工具时，浏览器下载 Python 运行环境（Pyodide 314.0.7，来自 jsDelivr，约 9 MB，之后从缓存读取）
和 TCMScience 的代码包（约 1.9 MB，按 SHA-256 核对）。150 个原生工具、4 个固定版本的受治理 Skill、临床辨证都能在浏览器里
运行，结果与本机 Python 运行逐字节相同（同样的 Skill 内容哈希、同样的输出文件哈希）。注册的在线数据库可在批准联网后经
同源网关调用；兼容的序列统计支持 WebGPU。大型流程和原生引擎仍显示各自的依赖。

**2. 加上本机 Runner。** Runner 连接后，工具都在它上面运行（`计算 → 自动`）：本机 CPython，项目各自的持久审计链，你的设备。
它还能运行需本地 Python/MCP 进程的第三方封装、RNA-seq / 单细胞 / 结构预测等后台任务和原生 GPU 引擎。
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
| `--network` | 打开 Runner 自己的联网开关（会保存）；每个项目还要单独打开「联网」 |
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
  TCMScience Python 代码。150 个原生工具、4 个固定版本的受治理 Skill 和临床辨证都可以运行，结果哈希与电脑上相同。
  第一次运行要下载约 11 MB（之后从缓存读取）。在模拟中端手机（CPU 降速 4 倍）的测试里，冷启动后第一次受治理调用约 10 秒完成
  （`e2e/mobile.spec.mjs`）。
- **GPU 自动加速。** GC 含量、汉明距离和序列距离矩阵可通过 WebGPU 计算整数计数，原有 Python 继续处理公式和输出；
  不支持、设备丢失或规模超限时回退 WASM CPU。「设置 → 计算」可选择仅 CPU。普通访问不会启动计算。
- **手机免安装数据库访问。** 注册的数据库查询经同源网关转发，仍需项目联网开关和批准。完整方剂表按需下载并校验，保留全部
  84,294 条来源记录。大文件、后台任务和依赖本地进程的第三方工具仍需 Runner。
- **测试范围。** 自动测试用的是 Chromium 的手机模拟（Android 视口、触控、降速 CPU），还没有在真机上测过 iOS Safari；
  iPhone 上的 Safari 也支持 WebAssembly，内存较小的机型首次加载可能较慢。

## 隐私：什么不会到达服务器

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

## Studio 不声称什么

- 它不提供临床建议。临床辨证的输出是给持证医师的草稿，签署是医师本人的行为，永远不是工具。
- 种子语料是示范性的：没有记载不等于安全，未显著不等于无关；古籍记载是出处，不是临床证据；预测不等于实测，网络药理最多支持
  机制**假说**。
- 「已准予发布」只表示内核的六项发布检查通过，不表示结论正确；证据种类是类型，不是等级。
- 浏览器里的 Python 在 CPU 上单线程运行；WebGPU 只加速已适配的整数统计内核，不代表所有 Python 引擎都能使用 GPU。
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
| `browser.spec.mjs` | 无 Runner：Pyodide 从 jsDelivr 启动，同一问题在浏览器中运行，Skill 内容哈希与输出文件哈希与本机 Python 相同（CDN 不可达时跳过并说明） |
| `approvals.spec.mjs` | `run_pipeline` 的批准卡片：拒绝 → 模型读到 refused；「本项目允许」写入项目并在刷新后仍然有效；后台任务的卡片跟随 Runner 事件直到收集完成，任务文件从 Runner 取来预览，模型用 `job_status` 跟进；联网：项目未开启时不调用也不询问，开启后批准卡片列出主机，再由 Runner 自己的联网开关决定 |
| `relay.spec.mjs` | `wrangler dev` 运行 `studio/edge`，上游指向模拟服务：默认模型 Tao-S1 流式回答，思考格式往返改名，身份问题关闭思考，页面、存储和中继回复都不出现上游名称；Tao-S1 调用工具并在浏览器中运行。Worker 启动失败就是失败；只有在 CI 之外、npm 源不可达（取不到 wrangler）时才跳过 |
| `mobile.spec.mjs` | 手机（390×844、触控、CPU 降速 4 倍）、无 Runner：受治理 Skill 在手机浏览器的 Pyodide 中运行，内容哈希与本机 Python 相同，页面无横向滚动 |
| `pages.spec.mjs` | 首次引导、设置（经表单添加并测试自定义模型）、工具目录、关于；手机视口 390×844；深色主题；无障碍基本检查（可访问名称、地标、焦点、未翻译的键） |
| `a11y.spec.mjs` | 键盘与读屏：跳转链接按下后焦点到目标、路由不变；输入法组字时的 Enter 不发送、Esc 不停止回答；首次发送、停止、批准之后以及菜单、抽屉按 Esc 之后焦点的去向；实时区域的播报；axe-core 检查主要页面（浅色/深色、桌面/手机）与受治理的回答，严重（serious/critical）问题即失败 |
| `pipeline.spec.mjs` | 不开浏览器，检查 `.github/workflows/studio.yml`：网页打包的 Harness 源码改动也会触发；`RELAY = "off"`（持久暂停）时部署后的检查不要求 Tao-S1 回答；CI 中 Worker 启动失败不会被当作跳过 |

Chromium 依次取 `$CHROMIUM_PATH`、`$PLAYWRIGHT_BROWSERS_PATH`（或 `/opt/pw-browsers`）中的 `chromium-1194`、Playwright 自带的
（`npx playwright install chromium`）。`$STUDIO_SITE` 指向已构建的网页时直接使用，否则先构建一次。截图保存在
`$STUDIO_E2E_TMP/screens`（默认 `studio/e2e/test-results/screens`）。另有浏览器运行时的逐项核对：
`npm run test:runtime`（与本机 Python 的结果逐个比较）。

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
browser (Pyodide) or on the local runner (`tcmstudio serve`, CPU or GPU). Conversations, projects, files and keys stay in
your browser.

- **Just open** science.impf.ai: Tao-S1, Python tools in the browser, nothing to install.
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
With Tao-S1, what the model reads is relayed to the upstream model service and not stored; with your own API it goes to
that provider; with a local model it stays on your computer. Studio gives no clinical advice: clinic outputs are drafts for a licensed practitioner, no record
is not evidence of safety, and predicted is not measured.

Tests: `node --test studio/edge/test/*.test.js`, `python3 -m pytest -q studio/runner/tests`, `cd studio && npm test`,
`cd studio && npm run test:e2e` (Playwright: the real runner, Pyodide from jsDelivr, `wrangler dev` of the Worker).
Deployment: [edge/SETUP.md](edge/SETUP.md).

Browser database access and acceleration: [docs/BROWSER_ACCESS.md](docs/BROWSER_ACCESS.md).
