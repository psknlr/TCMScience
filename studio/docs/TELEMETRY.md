# 访问统计、对话记录与管理后台

science.impf.ai 的 Worker 收三类数据，各存一处、互不相通：**使用统计**（计数与耗时，不含任何文字）、**对话记录**（只有访客
在记录说明里同意后才有）、**安全日志**（被拒绝或异常的请求，以及管理员自己的操作）。站点所有者在 `/admin/` 的管理后台查看。
设计参照 TaoChronos（chat.impf.ai）的同意与记录方式：网页先问、同意才记、记录有版本、只存必要字段；与它不同的是，
这个仓库是公开的，**任何记录与导出都不经工作流取回、不提交进仓库**（见「导出与私有保存」）。

```
浏览器（science.impf.ai 的网页）
  ├─同源─▶ POST /api/telemetry/events                 ─▶ UsageLog         每日计数；每轮、每次工具一行数字（无文字）
  ├─同源─▶ POST /api/telemetry/conversations          ─▶ ConversationLog  已同意访客的问答（已隐去证件号、密钥等）
  └─同源─▶ POST /api/telemetry/conversations/delete   ─▶ ConversationLog  访客用自己的删除密钥删除
Worker 自己：Tao-S1 中继与数据库网关的次数和结果 ─▶ UsageLog；被拒绝/异常的请求、管理操作 ─▶ SecurityLog
管理员 ─登录─▶ /admin/（仪表板）与 /api/admin/*（汇总、导出、查看与删除记录）
```

三个都是 SQLite Durable Object（`studio/edge/src/index.js`），与模型限额的 `Limiter` 也分开；任何一个出错，模型中继、
数据库网关、对话本身都不受影响。代码：网页侧 `web/js/core/{consent,telemetry,turnrecord,sensitive,telemetry-contract}.js`、
`web/js/ui/privacy.js`；Worker 侧 `edge/src/telemetry/*.js`、`edge/src/admin.js`；后台页面 `web/admin/`。
`telemetry-contract.js` 与 `sensitive.js` 由网页和 Worker **共用同一个文件**，两边按同一套规则取舍。

| 数据 | 内容 | 有访客写的文字吗 | 默认保留 | 谁能看 |
|---|---|---|---|---|
| 使用统计 `UsageLog` | 每日计数；每轮、每次工具执行的数字 | 没有 | 明细 90 天，每日计数 400 天 | 管理员；`EXPORT_TOKEN` 可导出 |
| 对话记录 `ConversationLog` | 已同意访客每轮的问答与统计 | 有（已隐去证件号、电话、邮箱、密钥等） | 90 天，最多 50 000 条 | 只有管理员（`ADMIN_TOKEN`） |
| 安全日志 `SecurityLog` | 被拒绝或异常的请求、管理员的操作 | 没有（路径不含查询串） | 30 天 | 只有管理员 |

## 同意（记录说明 v1）

- **默认不记录。** 访客第一次提问时（网站开放记录、且访客还没对当前版本的说明做过选择），网页先弹出「对话记录说明」：
  记录什么、从不记录什么、用途与保存期限、怎样撤回。默认焦点在「不记录」；关掉对话框等于不同意。**不同意不影响任何功能**，
  问题照常发送。
- 选择只存在本浏览器（`localStorage` 的 `tcmstudio.consent`：版本、是否同意、时间、使用统计开关、删除密钥）。
  可随时在「设置 → 隐私」改变：关闭记录后不再发送任何对话内容，尚未发出的也丢弃。
- **删除密钥**：同意时浏览器生成 32 字节随机数，只留在本浏览器；每条记录只带它的 sha256（`owner`），由此无法反推访客。
  「设置 → 隐私 → 删除已记录的对话」或「撤回同意并删除」把密钥发给 Worker，删除这个浏览器的全部记录。页面同时显示
  「记录编号」（`owner` 的前 12 位），访客换了浏览器或清除了数据时，可以凭它请管理员删除（见「删除请求」）。
- **说明有版本**（`telemetry-contract.js` 的 `CONSENT_VERSION`）。改了说明的内容就加一：旧版本下的同意不再有效，访客会被再问一次；
  Worker 拒收版本不对的记录（409 `consent_outdated`）。同意与拒绝的次数计入使用统计（不含是谁）。
- 「设置 → 通用 → 清除全部数据」会连删除密钥一起删掉；界面先说明这一点，建议先在「隐私」里删除已记录的对话。

## 对话记录：每轮记什么

一行一轮（访客的一个问题及其回答）。网页在一轮结束时生成（`turnrecord.js buildRecord`），Worker 收到后按同一份契约
（`normalizeRecord`）只留下面这些字段，超长的截断：

| 字段 | 含义 |
|---|---|
| `v`、`consent` | 记录格式版本（1）、访客同意的说明版本 |
| `id`、`conversation`、`turn` | 这轮回答的随机编号；对话的随机编号（只在那个浏览器里有意义）与第几问 |
| `owner` | 删除密钥的 sha256；用于访客自己删除，不能用来识别访客 |
| `started`、`finished`、`ms`、`first_ms` | 起止时间（UTC）、整轮耗时、到第一个字或第一段思考出现的耗时（毫秒） |
| `status` | `completed`（答完）/ `cancelled`（访客停止）/ `failed`（出错）/ `interrupted`（页面在回答中途被刷新或关闭，重新打开后补报） |
| `question`、`answer` | 问题（≤4000 字）与回答（Markdown，≤12000 字）；已隐去的内容换成 `[redacted:类别]`，`redacted` 是隐去的处数 |
| `provider`、`model` | 模型服务与模型名（如 `tao` / `Tao-S1`） |
| `steps`、`tool_rounds`、`calls`、`limit_reached` | 智能体步数、调用工具的轮数、模型请求次数（含重试）、是否达到步数上限 |
| `tools[]` | 每次工具执行：`name`、`kind`、`where`（browser / runner / gateway / page）、`status`、`ms`、`error_type`（不含工具参数：既无参数值，也无参数名）；一轮最多 40 个 |
| `citations` | 引文：工具给出的 `total`、原文已核对的 `verified`、回答中引用的 `used`、引用了却查不到的 `unresolved` |
| `claims`、`evidence` | 内核的主张检查（`allowed` / `refused` / `unchecked`）；证据条目、预测、结果的发布与扣留数 |
| `usage` | token：`input` / `output` |
| `error` | 出错类别（model / network / runtime）、类型、HTTP 状态；不含错误信息原文 |
| `app`、`runtime` | Studio 各部分版本（tcmstudio / bioagent / psh）；计算位置（auto / browser / runner）、浏览器运行时状态、Runner 版本、布局、界面语言 |

Worker 另加 `seq`（递增编号）、`received`（收到时间）、`day`（报告日，默认北京时间）。

**从不记录**：API 密钥与设置；上传的文件及其内容；项目文件；工具参数的值；访客的网络地址与浏览器标识。

**整段不记录**（访客同意了也不记；一旦出现，同一对话之后的轮次都不再记录；`turnrecord.js exclusionOf`）：

| 原因 | 条件 |
|---|---|
| `uploaded_file` | 对话里有上传的文件 |
| `project_files` | 项目里有文件（8 KB 以下的文本文件会随每条消息发给模型） |
| `clinical_tool` | 用到临床工具（`clinic.*`、处方草拟 Skill） |
| `user_data_tool` | 工具拿到访客自己的数据：TCMDB 导入、对本地文件的流水线任务，或参数里有文件、路径、上传编号 |
| `sensitive_text` | 问题里有可识别个人的信息（证件号、病历号/住院号、姓名或出生日期字段），读起来像病历（两段以上病历标题），像具体病例（「患者，女，32 岁」），或临床内容与联系方式同时出现（`sensitive.js`） |
| `discarded` | 回答期间访客删除了这个对话 |
| `no_consent`、`unavailable` | 访客没同意；网站没有开放记录 |

这些轮次仍然计入使用统计（只计数，并记下不记录的原因），内容一个字也不发送。

**Worker 再查一遍**：只接受本站页面（`Origin` 与 `Sec-Fetch-Site`）、正文 ≤64 KiB、每位访客每分钟 20 条、每天 500 条；
对问题和回答再做一次敏感内容判断（判为敏感则不存，只计 `excluded:原因`），再隐去一次；同一 `id` 只存一次。
隐去的类别：私钥、JWT、各家 API 密钥、Bearer 令牌、网址中的口令与签名参数、`password=` 一类的键值、邮箱、身份证号、
病历号等编号、姓名与出生日期字段、手机与电话号码、IP 地址。这些规则偏向少记：只提病名、药名、舌脉的科学问题照常记录。

## 使用统计：计什么

网页把事件攒成批（有新事件后约 4 秒发一批；页面隐藏或离开时用 `sendBeacon`），每个标签页一个待发队列（存在 `localStorage`，刷新后
接着发；关掉的标签页留下的队列 10 分钟后由其他标签页代发）；批有随机编号，Worker 对重发的批只计一次。
**在 `/v1/health` 说明本站收统计之前不发送任何东西**，所以本机 Runner 提供的网页、其他托管处都不会发。
发送失败只会稍后重试，从不影响对话。

| 事件 | 字段 | 计入 |
|---|---|---|
| `app_init` | 布局、语言、浏览器家族（chromium / firefox / safari / other）、是否手机 | 应用启动；浏览器与设备 |
| `visit` | 路由名 | 一次访问开始：标签页的第一个页面，或闲置 30 分钟后的第一个页面 |
| `pageview` | 路由名（home / project / conversation / settings / about / catalog / notfound / admin），设置页的分页 | 页面浏览（**只有名字**，从不含项目或对话的编号） |
| `turn_start` | 服务、模型、是否对话的第一问 | 开始的轮次、新对话、当天提问的访客（去重） |
| `turn_end` | 轮次编号、状态、步数、工具轮数、模型调用、工具数与失败数、耗时、首字耗时、token、引文与主张统计、是否记录（不记录的原因）、出错类别 | 每轮一行：成功率、耗时分位数、模型与 token |
| `tool_end` | 调用编号、工具名、类别、运行位置、结果、耗时、错误类型 | 每次工具执行一行：执行次数、失败率、耗时 |
| `workflow` | 类别（skill / study / job / tcmdb）、名称、阶段（started / submitted / completed / failed / refused / cancelled） | 研究流程的开始、提交、完成、失败（同一流程同一阶段只计一次） |
| `error` | 类别、错误名、发生在应用自己代码的哪一行 | 应用错误（**从不含错误信息**，信息里可能有访客写的字；每个页面最多 10 条） |
| `availability` | 服务（relay / runner / browser_runtime / storage）、是否可用 | 服务可用性 |
| `consent` | 同意 / 拒绝 / 撤回 / 删除，说明版本 | 同意情况 |

Worker 自己另计：Tao-S1 中继的每次请求及结果（`relay_call`）和 token（`relay_tokens`）；数据库网关按**数据库主机**计请求数、
结果类别（2xx/4xx/5xx、超时、拒绝、取消）和耗时（`connector`），从不记查询本身。

**这些数怎样区分**（仪表板也这样写）：页面浏览是每进入一个页面一次；访问是一段连续使用；访客按天去重；轮次是访客的一个问题；
一轮可以有多次模型调用（每轮工具调用一次，加上重试）和多次工具执行。

**访客怎样按天去重**：Worker 算出 `sha256(IP（IPv6 取 /64）| User-Agent)`，不保存；`UsageLog` 再用**当天的随机盐**把它
哈希成 32 位，放进当天的「见过」集合。第二天第一次用到新盐时，之前所有日子的盐和集合一起删除。所以谁（包括站点所有者）
都无法把不同日子的访客对上，也无法从中反推地址。同一出口地址后的访客（例如一个实验室）会被算成一个，访客数因此偏少。

**不用的东西**：Cookie（管理后台的登录除外）、任何发给服务器的长期标识、浏览器指纹、第三方脚本或统计服务、跨站追踪。

**关闭**：「设置 → 隐私 → 使用统计」；浏览器开启「请勿追踪」（DNT）或「全球隐私控制」（GPC）时自动关闭且不能打开。关闭时
队列中尚未发出的事件一并丢弃。

**地理位置**：只记 Cloudflare 判定的国家或地区代码（`TELEMETRY_GEO = "country"`；不明或 Tor 记 `XX`），不记城市或省份；
`"off"` 则一律 `XX`。配置**不能**比说明更细：其他值按 `country` 处理，配置测试会报错。

**限额**：一批 ≤50 个事件、正文 ≤32 KiB；每位访客每分钟 30 批、每天 1500 批；另与中继共用 `BURST`（每 10 秒 40 次）。

## 安全日志

| 事件 | 何时 |
|---|---|
| `scanner` | 有人请求本站根本没有的路径（`/wp-login.php`、`/.env`、`*.php` 等扫描器常试的地址） |
| `forbidden_origin` | 别的网站的页面调用中继、网关或统计接口 |
| `invalid_payload`、`oversized` | 统计或记录的正文不合格式、过大 |
| `rate_limited` | 触发限额 |
| `source_refused` | 数据库网关拒绝了不在注册表里的请求 |
| `admin_auth_failed`、`admin_locked` | 管理员登录失败；同一来源 15 分钟内失败 8 次后锁定 15 分钟 |
| `admin_login`、`admin_view`、`admin_export`、`admin_delete`、`admin_prune` | 管理员登录、查看对话内容、导出、删除、手动清理（审计记录） |

每个事件都计数；同一来源同一类事件每 10 秒最多存一条（扫描器一次会敲几千下），管理操作每次都存。Worker 自己看到的事件先在
内存里汇总，最多每 20 秒一次送进安全日志（每次带前 20 条完整事件，其余只计数；存下的时间是事件发生的时间），见下文「Durable
Object 的额度」。管理操作的审计记录不经过汇总，当场写入。

### IP 地址

- `who`：地址（IPv6 取 /64）在**当月随机密钥**下的 HMAC，16 位。同一来源在一个月内可以关联，跨月不能；月密钥在那个月的事件过了
  保留期后删除，此后谁也无法再关联。
- `net`：网段（IPv4 /24、IPv6 /48），足以在 Cloudflare 封禁，不足以指向个人。
- `ip`：完整地址，**只有** `SECURITY_RAW_IP = "flagged"` 时、且只对高危事件（`scanner`、`admin_auth_failed`、`admin_locked`）保存，
  随事件在保留期满时删除。默认 `"off"`：从不保存。
- 路径只存路径，不存查询串（查询串里可能有访客写的字）；`detail` 是程序自己的短标签，从不是请求正文。

## 接口

| 方法 | 路径 | 作用 |
|---|---|---|
| GET | `/api/telemetry/health` | `{ok:true, service:"tcmscience-telemetry", version:1, analytics, conversations, consent_version, geo, retention_days}`；`/v1/health` 的 `telemetry` 字段也带这些（网页据此决定是否询问、是否发送） |
| POST | `/api/telemetry/events` | `{v:1, batch, events:[…]}` → 204；统计关闭时 503 `analytics_off` |
| POST | `/api/telemetry/conversations` | 一条记录 → 201 已存 / 200 `{stored:false, reason:"duplicate"\|"excluded"}`；409 `consent_outdated`；记录关闭时 503 `records_off` |
| POST | `/api/telemetry/conversations/delete` | `{key}` → `{ok, deleted}`；暂停记录时也能删除；每个地址每分钟 10 次 |
| POST | `/api/admin/session` | 登录：表单字段或 JSON 的 `token` → 会话 Cookie；失败 401 `bad_token`，锁定 429 `admin_locked` |
| POST | `/api/admin/session/logout` | 退出 |
| GET | `/api/admin/session` | 会话是否有效、到何时 |
| GET | `/api/admin/telemetry/summary?days=7\|30\|90&provider=&model=&country=` | 仪表板的全部数字 |
| GET | `/api/admin/telemetry/export?dataset=…&format=csv\|json&days=…` | 下载（见下表） |
| GET | `/api/admin/telemetry/conversations?days=&status=&provider=&owner=&before=&limit=` | 对话记录（只有管理员；每次查看写入审计）；`owner` 可以是记录编号（12 位以上） |
| POST | `/api/admin/telemetry/conversations/delete` | `{ids:[…]}` 或 `{owner}` → `{deleted}` |
| POST | `/api/admin/telemetry/prune` | 立即按保留期限清理 |
| GET | `/admin/` | 已登录时是仪表板，否则是登录页；`/admin/dashboard.js` 等只给已登录的管理员 |

错误一律是 `{"error":{"message","type"}}`，`message` 是中文。所有统计接口只接受本站页面（`Origin` 必须是本站，
`Sec-Fetch-Site` 为 `same-origin`），只走 https。Worker 的控制台日志只记错误的名字（例如 `telemetry TypeError`），
从不记请求正文、请求头、令牌或地址；Worker 的逐请求日志（invocation logs）是关闭的。

## 保存与保留

| 变量 | 默认 | 删除什么 |
|---|---|---|
| `RETENTION_EVENTS_DAYS` | 90 | 每轮、每次工具的明细行，及其去重编号 |
| `RETENTION_AGGREGATES_DAYS` | 400 | 每日计数 |
| `RETENTION_CONVERSATIONS_DAYS` | 90 | 对话记录（删除请求的墓碑与计数保留一年，不含内容） |
| `RETENTION_SECURITY_DAYS` | 30 | 安全事件、登录失败状态、过期的月密钥 |
| `CONVERSATION_MAX_RECORDS` | 50000 | 超过时最旧的记录先删 |

每个 Durable Object 每天 03:17（UTC）由自己的定时器清理一次；仪表板的「立即清理过期数据」也可以手动执行。每日盐与「见过」集合
每天删除，批次去重编号两天后删除。保留期改短后，下一次清理即生效。

### Durable Object 的额度

统计、记录、安全日志与中继的限额（`LIMITER`）用的是同一个 Cloudflare 账号的 Durable Object 额度。Workers 免费版每天约
10 万次 Durable Object 请求、10 万行写入，用完后到 00:00（UTC）之前所有 Durable Object 调用都会失败——限额对象此时按设计拒绝
模型与数据库网关的调用（失败时关闭）。所以统计绝不能按请求花这份额度：

- **网页的事件**：攒 15 秒或到页面隐藏、关闭时成批发送，一批是一次请求；每批的计数先合并再写（每种计数一行）。
- **Worker 自己的计数**（中继调用与 token、数据库网关请求）与**安全事件**：在 Worker 实例的内存里按天与类别汇总，最多每
  20 秒各送一次（安静一段时间后的第一个当场送出，其余在这 20 秒结束时送出，即使之后没有请求）；扫描器敲几千下、别的网站刷
  几千次，也只是每 20 秒一次请求。代价：实例在送出之前被回收时，还没送出的计数会丢失，这几项数字因此是近似的。
- **对话记录**：一轮一条，只在访客同意时发送，有每分钟、每天的上限。
- **每天的写入上限**（`TELEMETRY_DAILY_ROWS`，默认 40000 行，按 UTC 日计，与 Cloudflare 的额度同一天切换）：使用统计 60 %、
  对话记录 25 %、安全事件 15 %；一位访客（按地址）最多用其中的十分之一。到了上限，统计与记录接口回 503 `budget_spent`
  （`Retry-After` 到 00:00 UTC），Worker 在那之前不再调用这个对象；网页把没发出的留着、按 `Retry-After` 等待；Worker 自己的
  计数在那之前丢弃。安全事件照常计数，只是不再逐条保存（管理操作的审计记录除外）。**从不因额度拒绝**：访客的删除请求、
  按保留期限的清理、管理员登录状态与审计记录。仪表板「设置」里显示三个对象今天已写入的行数与上限。付费版可设为 `0`（不设上限）。
- 表结构尽量省写入：以文本为键的表用 `WITHOUT ROWID`（普通表还要多写一份键的索引）。

用量大时请改用付费版（额度按用量计费，不会到点停用），或在 Cloudflare 的用量页面留意 Durable Object 的请求与写入行数。
大量恶意请求本身（不论打到中继还是统计接口）都会消耗免费版每天 10 万次的 Durable Object 请求额度；正式运营建议在
Cloudflare 上为 `/v1/*`、`/api/*` 加 WAF 速率限制规则，或使用付费版。

## 管理后台

**登录**：`ADMIN_TOKEN`（Worker 机密，至少 32 个字符；没设置时 `/admin` 与 `/api/admin/*` 一律 503，后台关闭）。登录页是普通表单，
令牌只在服务器端比较（先各自哈希再按固定时间比较）；成功后浏览器得到会话 Cookie `__Host-tcms_admin`（`HttpOnly; Secure;
SameSite=Strict; Path=/`），内容是到期时间与随机数，用由 `ADMIN_TOKEN` 派生的密钥签名：**更换 `ADMIN_TOKEN` 即让所有会话失效**。
有效期 `ADMIN_SESSION_SECONDS`（默认 1 小时）。同一来源 15 分钟内失败 8 次后锁定 15 分钟。

**防护**：用 Cookie 发出的修改请求必须来自本站页面（`Origin` 检查），别的网站无法借管理员的浏览器删除记录；后台页面强制
`Content-Security-Policy`（只允许本站自己的脚本和样式，不允许内联脚本或样式，不允许被嵌入），`no-store`、`noindex`；
Service Worker 从不缓存 `/admin/`。仪表板的代码只发给已登录的管理员；页面里没有任何令牌，访客写的文字一律作为文本显示，从不当作 HTML。
公开的网页代码里也没有任何管理接口（测试检查）。

**看什么**（7 / 30 / 90 天；按模型服务、模型、国家或地区筛选；主要数字与前一时段在同样筛选下比较）：

- 页面浏览、访问、访客·日（每日去重访客之和）；对话轮次与新对话；成功率；模型调用；token；响应耗时中位数及 p90、p95；工具执行与失败；应用错误；安全事件；
- 访问量折线（页面浏览、访问、访客）、页面与浏览器分布；每日结束的轮次及其结束方式；记录与不记录的原因；失败原因；
- 各模型的轮次、成功率、调用、token、平均耗时；耗时分位数（整轮与首个输出）；工具表（可筛选）及按运行位置；
- 数据库网关按主机的可用率；Tao-S1 中继的结果；研究流程；证据与引文；地理分布；页面报告的错误与服务可用性；
- 同意情况与记录库的存取；安全事件、来源（月度哈希与网段）与管理操作记录；
- 对话记录：**只在点「显示对话记录」后才读取**，每次读取写入审计；可按结束方式筛选、按记录编号查找、逐条或按访客删除。

图表有提示框（指针或方向键）、图例与直接标注，并都能切换成表格；状态用图标加文字表示，不只靠颜色。

**导出**（CSV 带 UTF-8 BOM，以 `= + - @` 开头的单元格前加 `'` 以免被表格软件当作公式；或 JSON；范围是所选的天数，不受筛选影响；
每次导出写入审计）：

| 数据集 | 内容 | `EXPORT_TOKEN` 可取 |
|---|---|---|
| `daily` | 每日计数（`day, metric, a, b, n`） | 是 |
| `turns` | 每轮一行的数字（无文字） | 是 |
| `tools` | 每次工具执行一行 | 是 |
| `connectors` | 数据库网关按主机、结果的每日计数 | 是 |
| `geo` | 按国家或地区的每日浏览、访问、访客 | 是 |
| `security` | 安全事件（含网段） | 否，只有 `ADMIN_TOKEN` |
| `conversations` | 对话记录（含问答） | 否，只有 `ADMIN_TOKEN` |

脚本用法（令牌放在环境变量里，不要写进命令行历史或文件）：

```bash
curl -fsS -H "Authorization: Bearer $EXPORT_TOKEN" \
  "https://science.impf.ai/api/admin/telemetry/export?dataset=daily&format=csv&days=30" -o daily.csv
```

`EXPORT_TOKEN` 只能读汇总和不含文字的数据集，不能看对话、不能删除，适合交给只做报表的人或程序；泄露时只需换掉它。

## 导出与私有保存

TaoChronos 用每小时运行的工作流把对话记录取回并提交进它的（私有）仓库。**这里不这样做**：本仓库是公开的，工作流的日志与
构建产物也公开；`studio` 工作流不读取、不保存任何统计、记录或导出，它上传的构建产物只有网页本身。

需要留存时，在自己的电脑上（或在一个**私有**仓库的定时任务里）运行：

```bash
export TCMS_EXPORT_TOKEN=…            # 汇总；要导出对话或安全事件则用 TCMS_ADMIN_TOKEN
node studio/edge/scripts/export-telemetry.mjs --out ~/tcmscience-exports --days 30
```

- 默认只取汇总数据集（`daily, connectors, geo`）；`--datasets` 可加 `turns,tools`（无文字），`security,conversations` 需要管理员令牌。
- **只写到仓库之外**：目标在本仓库内时直接拒绝；在其他 git 仓库里时，只有加 `--private-repo`（确认该仓库私有、访问受限）才写。
- 文件只有所有者可读（0600，目录 0700），按日期分目录；`--keep-days`（默认 180）删除更早的、由这个脚本建立的日期目录，其他文件不动。
- 令牌只从环境变量读取，不接受命令行参数，从不打印；只发往 `--url`（必须是 https）。

导出的对话仍受访客的删除请求约束：访客删除后，已导出的副本请一并删除（墓碑表 `conv_deletions` 记录何时、删了谁的几条，可据此核对）。

## 删除请求

1. 访客自己：「设置 → 隐私 → 删除已记录的对话」（或「撤回同意并删除」），立即删除，无需管理员。
2. 访客已没有那个浏览器的数据：请他提供「记录编号」，在仪表板的「已同意记录的对话」里「按记录编号查找」，
   确认后「删除此访客的全部记录」（或 `POST /api/admin/telemetry/conversations/delete {owner}`）。
3. 自动：保留期满的记录每天删除。

删除只留一条墓碑（何时、谁的、几条，没有内容），用于核对已导出的副本。使用统计本来不含可删除的个人内容。

## 配置

`studio/edge/wrangler.toml` 的 `[vars]`（写错或越界时回到默认值，配置测试会指出）：

| 变量 | 默认 | 含义 |
|---|---|---|
| `TELEMETRY` | `on` | `off`：不收使用统计（网页不再发送） |
| `TELEMETRY_CONVERSATIONS` | `on` | `off`：不收对话记录（网页不再询问；已存的照常保留、可删除） |
| `TELEMETRY_GEO` | `country` | `off`：不记国家或地区 |
| `TELEMETRY_UTC_OFFSET` | `8` | 报告日的时区（小时，-14…14） |
| `TELEMETRY_MAX_BODY_BYTES` / `CONVERSATION_MAX_BODY_BYTES` | `32768` / `65536` | 一批事件 / 一条记录的字节上限 |
| `TELEMETRY_PER_MINUTE` / `TELEMETRY_PER_DAY` | `30` / `1500` | 每位访客每分钟、每天的事件批数 |
| `CONVERSATIONS_PER_MINUTE` / `CONVERSATIONS_PER_DAY` | `20` / `500` | 每位访客每分钟、每天的记录条数 |
| `RETENTION_*_DAYS`、`CONVERSATION_MAX_RECORDS` | 见上 | 保留期限（1…3650 天）与记录条数上限 |
| `SECURITY_RAW_IP` | `off` | `flagged`：高危安全事件保存完整地址 |
| `ADMIN_SESSION_SECONDS` | `3600` | 后台会话时长（300…86400 秒） |
| `TELEMETRY_DAILY_ROWS` | `40000` | 三个统计 Durable Object 每天合计可写的行数（使用统计 60 %、对话记录 25 %、安全事件 15 %）；`0` 不设上限（付费版） |

机密（GitHub 仓库 Secrets，部署时写入 Worker；不进任何文件）：

| 名称 | 作用 |
|---|---|
| `ADMIN_TOKEN` | 管理后台与全部管理接口；至少 32 个字符；不设则后台关闭 |
| `EXPORT_TOKEN`（可选） | 只读：汇总与不含文字的数据集；至少 32 个字符 |
| `VISITOR_SALT`（可选） | 限额计数用的盐（与中继共用） |

改了说明的内容（记录什么、保存多久、谁能看）时：同时改 `strings.js` 的 `ui.consent.*` 与 `ui.privacy.*`（中英文），
把 `CONSENT_VERSION` 加一，并更新本文。保留期改动会自动出现在说明里（说明读 `/v1/health` 的 `retention_days`）。

## 部署

1. 生成 `ADMIN_TOKEN`（以及可选的 `EXPORT_TOKEN`），存为仓库 Secrets（`studio/edge/SETUP.md` 第 4、5 步）。
2. 合入 main：工作流部署 Worker（`[[migrations]]` 的 `v2` 自动建立三个 Durable Object），写入机密，然后 `scripts/check.mjs
   --require-telemetry` 检查 `/api/telemetry/health`，并确认 `/admin/dashboard.js` 与 `/api/admin/telemetry/summary` 对未登录者
   返回 401（未设 `ADMIN_TOKEN` 时 503），**绝不返回 200**。
3. 打开 `https://science.impf.ai/admin/` 登录，确认数字出现。

暂停：`TELEMETRY = "off"` / `TELEMETRY_CONVERSATIONS = "off"` 合入 main；关闭后台：在 Cloudflare 删除 Worker 机密 `ADMIN_TOKEN`
（并删除仓库 Secret，否则下次部署会写回）。

## 测试

| 测试 | 覆盖 |
|---|---|
| `web/test/publiclinks.test.mjs` | 网页不出现项目主页、Arena 与仓库链接（含运行时文本） |
| `web/test/telemetry.test.mjs` | 同意的版本与撤回；敏感内容与隐去；契约的字段与长度；不记录的原因；记录与事件的构建；只在本站收统计时发送；DNT/GPC；失败重试与刷新后续发；中断的轮次 |
| `web/dev/test/telemetry-ui.test.mjs` | 真实应用里的询问对话框、同意 / 拒绝 / 撤回、上传文件与敏感问题不记录、隐私设置页、发送失败不影响对话 |
| `web/test/admin.test.mjs` | 后台文字中英文对齐；图表刻度；仪表板对真实接口的显示；访客文字只作文本；按需读取与审计；删除；会话过期；公开代码不含管理接口与令牌 |
| `edge/test/telemetry-store.test.js` | 三个存储：计数互不混淆、去重、每日盐、筛选与对比、保留期、删除与墓碑、安全事件的地址处理 |
| `edge/test/telemetry.test.js` | 接口：来源检查、正文上限、限额、敏感内容在 Worker 端被拒、登录与锁定、会话、CSRF、导出权限、CSV 防公式、定时清理 |
| `edge/test/export.test.js` | 导出脚本：只写到仓库之外、令牌只从环境读、权限、保留 |
| `edge/test/check.test.js`、`e2e/pipeline.spec.mjs` | 部署后的检查（后台不对未登录者开放）；工作流只在令牌够长时写入机密且从不打印 |
| `e2e/telemetry.spec.mjs` | 真实 Worker（`wrangler dev`）上的完整流程：中英文、桌面与手机、同意与拒绝、记录可在后台看到、删除、统计中断时对话照常、刷新中断、两位访客同时使用、管理员登录与导出、登录页与仪表板的 axe 无障碍检查（浅色与深色） |
| `e2e/a11y.spec.mjs` | 主界面的 axe 检查，含「设置 → 隐私」 |
