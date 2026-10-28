# science.impf.ai 的 Cloudflare Worker（edge）

TCMScience Studio 在 <https://science.impf.ai> 上只有这一个 Worker（`tcmscience-studio`）。它做五件事：

1. **提供网页本身**：`studio/_site`（由 `python3 studio/scripts/build_web.py --out studio/_site` 生成）作为 Workers 静态资源，
   由 Cloudflare 直接发送，不经过 Worker 代码、不计入 Worker 请求数。响应头来自本目录的 [`_headers`](_headers)。
2. **Tao-S1 中继**：`/v1/*` 把网页的模型调用加上密钥转发给上游模型服务。密钥只是这个 Worker 的加密机密（secret），
   不进网页、不进仓库、不进日志；浏览器发来的请求不带任何密钥。
3. **数据库访问网关**：`/api/sources/*` 转发注册数据库的只读查询，解决公共 API 的浏览器 CORS 限制。
   请求模板由与网页相同的连接器注册表生成；数据库解析、缓存和分析继续在浏览器中执行。
4. **使用统计与对话记录**：`/api/telemetry/*` 收匿名的使用统计（计数与耗时，不含文字），以及**访客同意后**的对话记录；
   与安全日志分别存在三个 SQLite Durable Object 里。详见 **[docs/TELEMETRY.md](../docs/TELEMETRY.md)**。
5. **管理后台**：`/admin/` 与 `/api/admin/*`，只对持有 `ADMIN_TOKEN` 的站点所有者开放：仪表板、导出、查看与删除记录。

对话、文件、工具运行在访客自己的浏览器或本机 Runner 里；Worker 提供文件、模型中继、数据库查询传输，并只在访客同意后保存对话记录。

```
浏览器（science.impf.ai 的网页）──同源，无密钥──▶ /v1/chat/completions（本 Worker：来源检查、改名、限额、加密钥）──▶ 上游模型服务
                                └────────────▶ 其余路径：静态文件（Cloudflare 直接发送，带 _headers）
                                └────────────▶ /api/sources/request（模板检查、限流）──▶ 注册的公共数据库 API
                                └────────────▶ /api/telemetry/*（同源、限额）──▶ UsageLog · ConversationLog（SecurityLog 记异常请求）
站点所有者 ──ADMIN_TOKEN 登录──▶ /admin/ · /api/admin/*（汇总、导出、记录；每次操作写入审计）
```

## 接口（CONTRACTS.md §1）

| 方法 | 路径 | 作用 |
|---|---|---|
| GET | `/v1/health` | `{ok, service:"tcmscience-studio", version, model:"Tao-S1", models:["Tao-S1"], max_output_tokens, limits:{per_minute, per_day, tokens_per_day}}`。没有密钥或 `RELAY = "off"` 时 `ok:false`，网页据此改用访客自己的模型 |
| GET | `/v1/models` | `{object:"list", data:[{id:"Tao-S1", object:"model", owned_by:"impf"}]}` |
| POST | `/v1/chat/completions` | OpenAI 格式（流式或非流式）。`model` 只能是 `Tao-S1` 或不写；网页把 `model` 写成 JSON 的**第一个键**，中继就只改这一段文本、不重新序列化整段对话 |
| GET | `/api/sources/health` | 网关状态、已注册数据库及操作数量、数据大小与调用频率上限；不要求模型密钥 |
| POST | `/api/sources/request` | `{url, method, headers, body_base64, allowed_hosts, timeout_s?}` → `{status, headers, body_base64, url, transport}`；上游 4xx/5xx 作为 `status` 保留，由浏览器后端处理 |
| GET/HEAD | `/api/datasets/files/<group>/<name>` | 浏览器研究任务的数据中转：只转发 `runtime/datasets.json` 登记的确切 URL，透传 `Range`/`If-Range`，流式返回且不保存任何内容，限流与数据库网关相同（见 studio/docs/BROWSER_JOBS.md） |
| GET | `/api/telemetry/health` | `{ok:true, service:"tcmscience-telemetry", version:1, analytics, conversations, consent_version, geo, retention_days}`（`/v1/health` 的 `telemetry` 字段相同） |
| POST | `/api/telemetry/events` | 一批使用事件 → 204 |
| POST | `/api/telemetry/conversations` | 一条已同意的对话记录 → 201 / 200 `{stored:false}` / 409 `consent_outdated` |
| POST | `/api/telemetry/conversations/delete` | `{key}`：删除这个删除密钥的全部记录 → `{deleted}` |
| POST/GET | `/api/admin/*`、`/admin/` | 管理后台与其接口（登录、汇总、导出、记录、删除、清理），见 TELEMETRY.md「管理后台」 |

错误一律是 `{"error":{"message","type"}}`，`message` 是告诉访客怎么办的中文，`type` 是：
`forbidden_origin`、`not_configured`、`bad_request`、`too_large`、`model_not_allowed`、`rate_limited`、`daily_limit`、
`total_limit`、`unavailable`、`upstream_auth`、`upstream_rate`、`upstream_quota`、`upstream_error`、`upstream_rejected`、
`upstream_unreachable`、`relay_error`，以及 `https_required`、`not_found`、`method_not_allowed`。每个 429 都带 `Retry-After`。
统计与后台另有 `too_large`、`consent_outdated`、`analytics_off`、`records_off`、`bad_key`、`admin_off`、`unauthorized`、`forbidden`、
`admin_locked`、`bad_token`、`cross_site`、`bad_dataset`。

## 中继保证什么

- **只接受网页的来源**：`Origin` 必须完全等于 `ALLOWED_ORIGINS` 之一（science.impf.ai，以及本机 Runner 提供的
  `http://127.0.0.1:8765`、`http://localhost:8765`）；没有 `Origin` 的请求（脚本、`Referrer-Policy: no-referrer` 的页面）被拒绝。
  别的 Cloudflare 区域的 Worker 转发来的模型调用（带 `CF-Worker` 头，或来自 Cloudflare 为跨区域子请求填写的地址
  `2a06:98c0:3600::103`）不论 `Origin` 写什么都按 `forbidden_origin` 拒绝，安全日志的说明里记下那个区域：Worker 可以随意填写
  `Origin`，网页的请求从不经过 Worker（边缘的 WAF 规则也拦它们：TaoChronos `security/rules.yaml` 第 3 条）。
  这只防浏览器里的盗用与转发；真正限定费用的是下面的限额。
- **访客看不到上游**：只有 `Tao-S1` 这个名字。请求里 `Tao-S1` 换成 `MODELS` 的第一个模型，加上 `UPSTREAM_FIELDS`
  （`reasoning_split`），网页送回的思考格式 `Tao-…` 还原成上游的 `MiniMax-…`；回复（含流）里的模型名改回 `Tao-S1`、思考格式改成
  `Tao-…`，上游独有的字段（`base_resp`、`*_sensitive`、`usage.total_characters`）去掉。上游的任何报错（HTTP 错误、200 回复里的
  `base_resp`、流中的错误事件）都换成中继自己的一句话，上游原文只以状态码记进 Worker 日志。请求里写上游自己的模型名也会被拒绝，
  免得有人借此确认上游是谁。
- **不思考**：请求带 `thinking: {type: "disabled"}`（网页问身份时这样做）时，回复里的思考字段和 `<think>…</think>` 一律去掉，
  无论流怎样切分。最后一条用户消息在问助手的身份或模型时，中继自己也会加上它（与网页同一个判断，`src/identity.js`），
  不依赖客户端。
- **上限**：输出 token 不超过 `MAX_OUTPUT_TOKENS`（`max_tokens` / `max_completion_tokens` 被截到上限，没写就补上；同一个键写了两次时，
  无论键名是否用 `\u` 转义书写，送出的只有中继读到并检查过的那一个）；去掉 `n`；
  请求体不超过 `MAX_BODY_BYTES` 字节（按字节算，汉字 3 字节；超过时边读边停，不先读完）。
- **限额**（`LIMITER` Durable Object，全站一个，SQLite）：每位访客每分钟 `PER_MINUTE`、每天 `PER_DAY` 次模型调用，全体每天
  `TOTAL_PER_DAY` 次；每位访客每天 `TOKENS_PER_DAY`、全体每天 `TOTAL_TOKENS_PER_DAY` 个 token（输入 + 输出）。调用发出时先按请求
  大小预扣（约 3 字节一个 token），回复末尾报告用量后补差；没得到回答的调用（上游报错、连不上）退回预扣，但仍算一次调用。
  「每天」按 UTC 计，北京时间 8:00 重置。另有 `BURST`（Cloudflare 速率限制绑定）：每位访客 10 秒 40 次，在读请求体之前就检查。
  智能体每轮工具调用都要调用一次模型（一个问题最多 16 次），所以这些数比聊天页面需要的高。
- **访客不留地址**：计数用 `sha256(VISITOR_SALT | IP（IPv6 取 /64）| UTC 日期)` 的前 24 位，IP 本身不存；Worker 的调用日志
  （invocation logs）关闭，只保留它自己的一行错误（状态码或错误名）。同一出口 IP 后的访客（例如一个实验室）共用限额。
  使用统计的每日访客数另用每天更换、次日删除的随机盐，安全日志只存月度哈希与网段（TELEMETRY.md「IP 地址」）。
- **失败时关闭**：Durable Object 不可达时拒绝调用（503 `unavailable`），绝不放出不计数的调用；`BURST` 绑定本身出错时放行到
  Durable Object（它仍然计数）。
- **取消**：访客停止回答或关掉页面时，上游调用随之中止（`enable_request_signal`），不再继续计费。
- **只走 https**：http 请求 403（本机 `localhost` / `127.0.0.1` 除外）；生产环境里 impf.ai 区域的 Always Use HTTPS 会先把 http 301 到 https。

## 数据库网关

构建生成 `runtime/source-gateway.json`，覆盖连接器注册表中的全部 117 个数据库、444 个操作。
Worker 从 `ASSETS` 读取并验证该文件，部署实例内缓存；清单缺失或格式不正确时返回 503，避免仅部署旧网页后误用网关。
新增连接器后重新构建、部署即可更新，无需维护第二份手写 URL 名单。

请求必须来自 `ALLOWED_ORIGINS`，并完整匹配注册操作的地址、GET/POST 方法、固定参数及正文结构。
GraphQL 只接受注册的完整 query 文本和变量结构，表单和 JSON 正文保留模板中的固定字段。
调用方的 `allowed_hosts` 只能缩小本次请求的主机范围，不能添加数据库；任意 URL、内网、localhost、元数据地址、其他端口、路径跳转和写入方法均拒绝。
每次重定向重新检查注册模板和本次允许的主机，最多跟随三次，HTTPS 不能降级到 HTTP；跨源重定向去掉授权头。

六个已注册的旧数据库仍仅提供 HTTP（包含 HERB 的固定公共 IP）。它们通过浏览器到网关的 HTTPS 连接访问，
网关到这些数据库仍是 HTTP；回复与目录保留 `transport: "http"`，网关不向 HTTP 地址转发 Authorization 或 API-key 请求头。
来源服务的登录、许可、维护或网络拒绝仍由浏览器后端报告；网关不会代替访客取得数据库权限。

请求包默认上限 384 KiB，原始查询正文最多 256 KiB，响应默认最多 8 MiB；读取响应流时持续计数，超限立即取消。
整个查询和响应读取默认最多 30 秒；超限、超时和取消都有明确错误。响应不设置 cookie，结果和查询不写入服务器缓存或日志。
每位访客每分钟 120 次、每天 3000 次，全站每天 50000 次，与模型额度分开；全站 Durable Object 按数据库公开速率安排请求，
排队超过 10 秒时返回 429 和 `Retry-After`。源站 429 的 `Retry-After` 原样交给浏览器后端。

## 网页文件与响应头

- `run_worker_first = ["/v1/*", "/api/sources/*", "/api/datasets/*", "/api/telemetry/*", "/api/admin/*", "/admin", "/admin/*"]`：模型、数据库网关、数据中转、
  统计与管理后台运行 Worker；`/admin/*` 的文件也由 Worker 把关（仪表板的代码只给已登录的管理员，页面强制 CSP）。
  其余文件由 Cloudflare 直接发送（免费，不计数）。
- `not_found_handling = "none"`：找不到文件的路径交给 Worker——浏览器**导航**到它（应用自己的路由，如 `/p/<项目>/c/<会话>`）
  时返回 `index.html`，其他请求（脚本、网页要的 JSON）返回真正的 404，而不是把网页 HTML 当成那个文件。若用 History API 路由，
  网页必须用绝对路径引用自己的文件（`/js/…`）。
- [`_headers`](_headers)（部署时复制进 `studio/_site/`；Worker 自己的回答由 `src/site.js` 设置同样的头，测试检查两者一致）：
  - `Cross-Origin-Opener-Policy: same-origin` + `Cross-Origin-Embedder-Policy: require-corp`：页面跨源隔离，Pyodide 才能用
    SharedArrayBuffer 中断正在运行的 Python。jsDelivr 带 CORS，Pyodide 照常加载。
  - `Referrer-Policy: strict-origin-when-cross-origin`（**不能**是 `no-referrer`：那样浏览器发 `Origin: null`，中继会拒绝网页自己的调用）。
  - `Content-Security-Policy-Report-Only`：先只报告不拦截（违规显示在浏览器控制台），确认无误后再改成强制的 `Content-Security-Policy`。
  - HSTS、`nosniff`、`X-Frame-Options: SAMEORIGIN`、`Permissions-Policy`（不列 `local-network-access`：网页要能连本机 Runner）。
  - 缓存：内容哈希命名的 `runtime/*.tar.gz` 永久缓存（`immutable`），其余（HTML、JS、CSS、catalog、boot.json）`no-cache`，每次向服务器确认。

## 配置（`wrangler.toml` 的 `[vars]`）

| 变量 | 默认 | 含义 |
|---|---|---|
| `RELAY` | `on` | `off`：停用 Tao-S1（health `ok:false`，调用 503 `not_configured`），不动密钥 |
| `UPSTREAM_BASE` | `https://api.minimax.cn/v1` | 国内站密钥用它；国际站（platform.minimax.io）密钥改为 `https://api.minimax.io/v1` |
| `MODELS` | `MiniMax-M3` | 上游模型（用第一个），访客看不到 |
| `PUBLIC_MODEL` | `Tao-S1` | 访客看到的唯一模型名 |
| `UPSTREAM_FIELDS` | `{"reasoning_split":true}` | 网页没写的这些键加进上游请求 |
| `FORMAT_PREFIX` | `MiniMax-` | 上游思考格式的前缀，对访客显示为 `Tao-` |
| `MAX_OUTPUT_TOKENS` | `8192` | 输出 token 上限 |
| `MAX_BODY_BYTES` | `2000000` | 请求体上限（字节） |
| `PER_MINUTE` / `PER_DAY` / `TOTAL_PER_DAY` | `40` / `1200` / `20000` | 模型调用次数：每人每分钟、每人每天、全体每天（`0` 不限） |
| `TOKENS_PER_DAY` / `TOTAL_TOKENS_PER_DAY` | `10000000` / `200000000` | token：每人每天、全体每天（`0` 不限） |
| `ALLOWED_ORIGINS` | `https://science.impf.ai,http://127.0.0.1:8765,http://localhost:8765` | 可以调用中继的网页来源（完全匹配） |
| `HSTS_MAX_AGE` | `15552000` | Worker 自己回答的 HSTS（区域的 HSTS 设置会覆盖它） |
| `SOURCES` | `on` | `off`：暂停数据库网关，不影响网页和模型中继 |
| `SOURCE_MAX_BODY_BYTES` / `SOURCE_MAX_RESPONSE_BYTES` | `393216` / `8388608` | 数据库请求包及响应的字节上限；响应最大可配置为 16 MiB |
| `SOURCE_TIMEOUT_MS` | `30000` | 数据库查询和响应读取总时限；最大 30 秒 |
| `SOURCE_PER_MINUTE` / `SOURCE_PER_DAY` / `SOURCE_TOTAL_PER_DAY` | `120` / `3000` / `50000` | 数据库查询次数：每人每分钟、每人每天、全站每天；必须大于零 |

数字写错（例如 `"lots"`）时回到默认值，而不是变成不限。机密：`MINIMAX_API_KEY`（必需），`VISITOR_SALT`（可选，16 位以上随机串），
`ADMIN_TOKEN`（管理后台；至少 32 个字符，不设则后台关闭），`EXPORT_TOKEN`（可选，只读的汇总导出；至少 32 个字符）。
在 Cloudflare 控制台里改的变量会在下次部署时被 `wrangler.toml` 覆盖：长期的改动请改文件。

改 `[vars]` 只需改这个文件：`src/relay.js` 的 `DEFAULTS` 是变量缺失或写错时的后备，不必跟着改。`test/config.test.js`
检查每个变量都在、写法正确（`RELAY` 是 `on`/`off`，`UPSTREAM_BASE` 是 https 地址，数字是整数，`ALLOWED_ORIGINS`
含 `https://science.impf.ai` 且每项都是完整的来源），并且调用次数限额要么是 `0`（不限）、要么至少 16（一个问题最多调用模型
16 次）；只固定 `PUBLIC_MODEL = "Tao-S1"` 和 `UPSTREAM_FIELDS` 里的 `"reasoning_split":true`。写错时 test 作业失败，日志列出
是哪个变量、错在哪里。

「访客看不到上游」指网页与中继的回答：模型名、思考格式和错误都换成本服务自己的说法。这个仓库是公开的，它本身写明了上游服务：
`wrangler.toml`、`src/relay.js` 的 `DEFAULTS`、机密名 `MINIMAX_API_KEY`、部署检查与工作流、文档；公开的 Actions 日志里，
`wrangler deploy` 也会列出 `[vars]` 的值。这是有意的取舍，不必为此改动。

## 遥测配置

统计、记录与安全日志的变量也在 `[vars]` 里（含义与理由见 [TELEMETRY.md](../docs/TELEMETRY.md)「配置」）。写错或越界时回到默认值：

| 变量 | 默认 | 含义 |
|---|---|---|
| `TELEMETRY` | `on` | `off`：不收使用统计，网页不再发送 |
| `TELEMETRY_CONVERSATIONS` | `on` | `off`：不收对话记录，网页不再询问；已存的照常保留、可删除 |
| `TELEMETRY_GEO` | `country` | `off`：不记国家或地区（不提供更细的位置：说明里只写了「国家或地区」） |
| `TELEMETRY_UTC_OFFSET` | `8` | 报告日的时区 |
| `TELEMETRY_MAX_BODY_BYTES` / `CONVERSATION_MAX_BODY_BYTES` | `32768` / `65536` | 一批事件、一条记录的字节上限 |
| `TELEMETRY_PER_MINUTE` / `TELEMETRY_PER_DAY` | `30` / `1500` | 每位访客的事件批数 |
| `CONVERSATIONS_PER_MINUTE` / `CONVERSATIONS_PER_DAY` | `20` / `500` | 每位访客的记录条数 |
| `RETENTION_EVENTS_DAYS` / `RETENTION_AGGREGATES_DAYS` | `90` / `400` | 每轮、每次工具的明细 / 每日计数的保留天数 |
| `RETENTION_CONVERSATIONS_DAYS` / `RETENTION_SECURITY_DAYS` | `90` / `30` | 对话记录 / 安全事件的保留天数 |
| `CONVERSATION_MAX_RECORDS` | `50000` | 对话记录的条数上限（超过时最旧的先删） |
| `SECURITY_RAW_IP` | `off` | `flagged`：高危安全事件保存完整地址（默认只存网段） |
| `ADMIN_SESSION_SECONDS` | `3600` | 管理后台登录的有效期 |
| `TELEMETRY_DAILY_ROWS` | `40000` | 统计每天合计可写的 Durable Object 行数（`0`：不设上限）；免费版全账号每天 10 万行，限额对象也在其中 |

三个 Durable Object（`USAGE` → `UsageLog`、`CONVERSATIONS` → `ConversationLog`、`SECURITY` → `SecurityLog`）由 `[[migrations]]`
的 `v2` 建立；每个每天 03:17（UTC）按保留期限自行清理。

## 停用开关

- **立即**：Cloudflare → Workers & Pages → `tcmscience-studio` → Settings → Variables and Secrets，删除 `MINIMAX_API_KEY`。
  几秒内 health 变成 `ok:false`，网页改请访客用自己的模型；网站本身照常。同时删除仓库 Secret `MINIMAX_API_KEY`，否则下次部署会把它写回去。
- **持久**：把 `wrangler.toml` 的 `RELAY` 改为 `"off"` 并合入 main。部署后的检查读到这个设置，把 health 的 `ok:false`
  当作暂停而不是失败。恢复时改回 `"on"`（或重新放回密钥后重跑工作流）。
- 真正的费用上限是上游账户的余额：建议为这个网站单独建一把按量付费的密钥。
- **统计与记录**：`TELEMETRY = "off"`、`TELEMETRY_CONVERSATIONS = "off"`（合入 main）；**管理后台**：在 Cloudflare 删除 Worker 机密
  `ADMIN_TOKEN`，并删除同名仓库 Secret，否则下次部署会写回。

## 部署

`.github/workflows/studio.yml`：每次推送和 PR，只要改动了 `studio/**`、这个工作流本身，或网站打包、tcmstudio 导入的那部分
（`PSH-Harness/src/**`、`PSH-Harness/pyproject.toml`、`BioScience-Harness/{src,skills,registry}/**`、
`BioScience-Harness/pyproject.toml`、`BioScience-Harness/setup.py`），都跑全部测试并构建网页；只在 main 上（推送或手动运行）部署：
`wrangler deploy` → 写入机密 → `scripts/check.mjs` 检查 health、首页的响应头、数据库网关、统计接口，确认管理后台对未登录者
返回 401（未设 `ADMIN_TOKEN` 时 503）而不是 200，并用 Tao-S1 做一次极小的调用。工作流不读取、不保存任何统计或记录；
需要留存时用 `scripts/export-telemetry.mjs` 导出到仓库之外的私有位置（TELEMETRY.md「导出与私有保存」）。
一次性的配置步骤见 **[SETUP.md](SETUP.md)**。

## 本地

```bash
cd studio/edge
npm test                                   # node --test（Node 22），不需要安装任何依赖
npm run mock                               # 另开终端：模拟上游 http://127.0.0.1:8790/v1
cp .dev.vars.example .dev.vars             # 本地密钥与上游地址（已被 .gitignore）
python3 ../scripts/build_web.py --out ../_site && cp _headers ../_site/_headers
npm run dev                                # https://localhost:8787（自签名证书）：网页 + 中继 + 统计；.dev.vars 里加
                                           # ADMIN_TOKEN=（32 个以上字符）后 https://localhost:8787/admin/ 是管理后台
npm run dry-run                            # 检查配置与打包，不需要 Cloudflare 账户
node scripts/check.mjs --url https://science.impf.ai   # 部署后的检查（工作流也用它）
```

`wrangler dev` 会把网页自己的来源当作 `https://science.impf.ai` 交给 Worker，所以本地不用改 `ALLOWED_ORIGINS`。
Durable Object 的本地数据在 `.wrangler/state`；改了表结构后删掉这个目录。

## 文件

| 文件 | 内容 |
|---|---|
| `src/index.js` | Worker 入口：模型、数据库网关、统计与后台的路由，资源缺失时的导航回退；`Limiter`、`UsageLog`、`ConversationLog`、`SecurityLog` Durable Object |
| `src/telemetry/routes.js` | `/api/telemetry/*`：来源、大小、限额检查，记录在 Worker 端的敏感内容复查 |
| `src/telemetry/usage.js`、`conversations.js`、`security.js` | 三个存储：计数与明细、对话记录与删除墓碑、安全事件与登录锁定 |
| `src/telemetry/config.js` | 统计、记录与后台的变量和机密的读取 |
| `src/admin.js` | 管理后台：登录与会话、汇总、导出（CSV 防公式）、记录的查看与删除、强制 CSP |
| `src/http.js` | 统计与后台共用的小工具（JSON 回答、常数时间比较、来源检查） |
| `src/sources.js` | 注册数据库模板验证、受限转发、重定向与响应大小检查 |
| `src/relay.js` | 中继：来源、改名、过滤、上限、限额、错误改写（移植自 TaoChronos `relay/src/relay.js`） |
| `src/limiter.js` | 限额计数（Durable Object 的 SQLite） |
| `src/site.js` | Worker 自己回答的安全响应头，与 `_headers` 一致 |
| `_headers` | 静态文件的响应头 |
| `scripts/check.mjs` | 部署后的检查 |
| `scripts/export-telemetry.mjs` | 把统计（或在管理员令牌下的记录）导出到仓库之外的私有位置，带保留期限 |
| `scripts/mock-upstream.mjs` | 本地用的模拟上游 |
| `test/*.test.js` | `node --test`：中继、限额、响应头、Worker 入口、`wrangler.toml` 的配置齐全且写法正确、部署检查、统计与记录的存储和接口、管理后台、导出脚本 |
