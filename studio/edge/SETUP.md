# 教程：上线 science.impf.ai（网页 + Tao-S1 中继）

TCMScience Studio 的一次性配置，约 20 分钟。完成后，访客打开 <https://science.impf.ai> 就能使用 Studio，默认模型 Tao-S1
不用填 API Key。Worker 本身的说明见 [README.md](README.md)。

**密钥放在哪里**：MiniMax 密钥只存在两处加密的地方——GitHub 仓库的 Actions Secrets，和 Cloudflare Worker 的机密（secret）。
部署时 GitHub Actions 把它从前者写入后者。它不进任何文件、网页或日志，保存后两边都无法再查看。

```
MiniMax 控制台 → 复制密钥 → GitHub 仓库 Secrets → studio 工作流部署时写入 → Cloudflare Worker 机密
访客浏览器 → science.impf.ai（同一个 Worker：网页文件 + /v1 中继，加上密钥、限流）→ MiniMax
```

## 开始前

| 需要 | 说明 |
|---|---|
| MiniMax 开放平台账号 | 国内站 [platform.minimaxi.com](https://platform.minimaxi.com)（会跳转到 platform.minimax.cn），或国际站 [platform.minimax.io](https://platform.minimax.io) |
| Cloudflare 账号 | `impf.ai` 托管在这个账号里（名称服务器是 `*.ns.cloudflare.com`）。免费计划即可：静态资源、Durable Objects（SQLite）、速率限制绑定都能用 |
| 仓库管理员权限 | 能打开 psknlr/TCMScience 的 **Settings** |

最后要存进仓库的 Secrets：

| 名称 | 是什么 | 在哪一步取得 |
|---|---|---|
| `CLOUDFLARE_API_TOKEN` | 让 GitHub Actions 能部署 Worker 的令牌 | 第 2 步 |
| `CLOUDFLARE_ACCOUNT_ID` | Cloudflare 账户编号 | 第 3 步 |
| `MINIMAX_API_KEY` | MiniMax 密钥（没有它网站照常上线，只是 Tao-S1 不开放） | 第 1 步 |
| `VISITOR_SALT`（可选） | 访客计数用的盐，16 位以上随机串 | 第 4 步 |

取得的值先暂存在密码管理器或本机的临时记事里，存进 GitHub 后就删掉。**不要**把它们发到聊天、Issue、PR 里，也不要提交进仓库。

## 第 1 步　准备 MiniMax 密钥

1. 登录 MiniMax 开放平台，创建密钥：
   - 国内站：左侧 **接口密钥** → **创建新的 API Key**；
   - 国际站：**API Keys** → **Create new secret key**。
2. 名称填 `tcmscience-studio`，方便以后辨认、单独停用。创建后立即复制。
3. 记下它来自**国内站还是国际站**，第 6 步要用。

选哪种密钥：

- **推荐为这个网站单独建一把按量付费的 API Key**（上面创建的就是）。它按实际用量从账户余额扣费，余额就是费用上限：测试期间只充值你愿意花的金额。
  不要与 TaoChronos（chat.impf.ai）共用同一把：分开后可以单独更换、单独停用，用量也分得清。
- Token Plan 订阅密钥（以 `sk-cp-` 开头）也能用，但它的额度按 5 小时和每周的窗口计算。Studio 的智能体每个问题要调用模型好几次
  （每轮工具调用一次，最多 16 次），公开网站可能很快用完；用完后网页会提示“Tao-S1 的模型额度暂时用完了”，直到下一个窗口。

## 第 2 步　创建 Cloudflare API Token

1. 登录 [dash.cloudflare.com](https://dash.cloudflare.com)，右上角头像 → **My Profile** → 左侧 **API Tokens** → **Create Token**。
   也可以建账户级令牌：左侧 **Manage Account → API Tokens**，其余步骤相同。
2. 在模板列表里找到 **Edit Cloudflare Workers**，点 **Use template**。
3. **Permissions** 保持模板原样。部署用到其中三项：Account 的 *Workers Scripts: Edit* 与 *Account Settings: Read*，
   Zone 的 *Workers Routes: Edit*（静态资源、Durable Objects、速率限制绑定不需要额外权限）。
4. 设置资源范围。这一步最容易漏：
   - **Account Resources**：`Include` → 选 impf.ai 所在的账户。
   - **Zone Resources**：`Include` → `Specific zone` → `impf.ai`。**必须选**，因为自定义域名 `science.impf.ai` 在这个域名下。
     漏选时，第一次部署可能成功，之后再部署会报 `No access to the specified resource`。
   - **Client IP Address Filtering**：留空，因为 GitHub Actions 的出口地址不固定。
   - **TTL**：可以留空（长期有效）。若设了到期日，到期前要按“日常维护”更换令牌。
5. 可以点页面顶部令牌名旁的铅笔图标，改名为 `tcmscience-studio (GitHub Actions)`。
6. 点 **Continue to summary**，核对账户和 `impf.ai` 都在列表里，再点 **Create Token**。
7. 复制显示的令牌。**它只显示这一次**，丢了就删掉重建。

可以用 TaoChronos 已有的那把令牌吗？可以：同一个 Cloudflare 账户、同一个 `impf.ai`，权限也相同（TaoChronos 的令牌若另加了
Containers 权限，只是多余，不碍事）。但 GitHub 的 Secret 存进去后谁也读不出来，所以只有你手里还留着那串令牌原值时才能复用；
找不到原值就按上面新建一把，**不要**为此对 TaoChronos 的令牌点 Roll（会让 TaoChronos 的部署立刻失效）。分开两把也更好：哪一边泄露都只需换那一把。

## 第 3 步　复制 Cloudflare Account ID

下面三处任选一处：

- 左侧 **Workers & Pages** → 右侧 **Account Details** → 点 **Account ID** 旁的复制按钮；
- 按 `Ctrl + K`（Mac 上是 `⌘ + K`），搜索 `Copy account ID`；
- **Domains → impf.ai → Overview**，页面靠下的 **API** 区。

Account ID 是 32 位十六进制字符串。它不算机密，但也不必公开。

## 第 4 步（可选）　生成 VISITOR_SALT

中继按“IP + 日期”的哈希给访客计数，IP 本身不保存。加一个只有 Worker 知道的盐，别人就无法由哈希反推 IPv4 地址。任选一种方法生成：

```bash
openssl rand -hex 32                                        # macOS / Linux
python3 -c "import secrets; print(secrets.token_hex(32))"   # 装有 Python 的任何系统
```

```powershell
# Windows PowerShell
$b = New-Object byte[] 32; [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b); -join ($b | % { $_.ToString('x2') })
```

不设也能运行。以后更换它，当天的访客计数会从零开始（对访客只有好处）。

## 第 5 步　把这些值存为 GitHub 仓库 Secrets

1. 打开 <https://github.com/psknlr/TCMScience/settings/secrets/actions>，
   也就是仓库 → **Settings** → 左侧 *Security* 下的 **Secrets and variables** → **Actions**。
2. 在 **Secrets** 标签下点 **New repository secret**。
3. **Name** 填名称（全大写，一字不差），**Secret** 粘贴值，再点 **Add secret**。依次添加：

   | Name | Secret |
   |---|---|
   | `CLOUDFLARE_API_TOKEN` | 第 2 步的令牌 |
   | `CLOUDFLARE_ACCOUNT_ID` | 第 3 步的 Account ID |
   | `MINIMAX_API_KEY` | 第 1 步的 MiniMax 密钥 |
   | `VISITOR_SALT`（可选） | 第 4 步的随机串 |

4. 完成后，**Repository secrets** 列表里应有这几项，只显示名称和更新时间。

注意：

- 要放在 **Repository secrets**。不要放进 *Environment secrets*（工作流没有指定环境），也不要放进 **Variables** 标签（那里是明文）。
- 保存后值无法再查看，只能点 **Update** 覆盖。它在 Actions 日志里会自动显示为 `***`。
- 粘贴时带进来的空格和换行，工作流会自动去掉。
- 这个仓库是公开的。Secrets 不会因此公开：公开仓库的 Secrets 同样加密，来自他人 fork 的 PR 也读不到；部署只在 main 上运行。
- **Variables** 标签里的 `STUDIO_URL` 只有在网站**不用** `science.impf.ai` 时才需要设置，平时不用管。

## 第 6 步　改接口地址（只有国际站密钥需要）

- **国内站**（platform.minimaxi.com / platform.minimax.cn）的密钥：什么都不用改，默认地址就是 `https://api.minimax.cn/v1`。
- **国际站**（platform.minimax.io）的密钥：把 `studio/edge/wrangler.toml` 里的这一行改为

  ```toml
  UPSTREAM_BASE = "https://api.minimax.io/v1"
  ```

  然后提交到 main。可以直接在 GitHub 网页上编辑这个文件。
- 拿不准时先照常部署。第 8 步的检查若报 `the model service refused the key`，多半就是站点不对，也可能是密钥有误。

## 第 7 步　确认 science.impf.ai 没有别的用途

第一次部署时，Cloudflare 会自动为 `science.impf.ai` 创建 DNS 记录并签发证书。GitHub 上的部署**不会询问也不会失败**：如果这个
名字已有 DNS 记录，或已是另一个 Worker 的自定义域名，部署会直接替换它，原来的用途随即失效。所以第一次部署前请自己检查一次——
这是唯一的保护。

1. Cloudflare → **Domains → impf.ai → DNS → Records**，在搜索框输入 `science`。
2. 应该没有任何结果。有的话，先确认它没有别的用途：不再需要就删除（**Edit → Delete**）；仍在使用就不要部署，改用别的子域名
   （`studio/edge/wrangler.toml` 里 `[[routes]]` 的 `pattern`）。
3. 再看 **Workers & Pages**：其他 Worker 的 **Settings → Domains & Routes** 里不应有 `science.impf.ai`。
4. **不要**手动添加这条记录。

## 第 8 步　部署

- 把含 `studio/` 的改动合入 main，**studio** 工作流会自动运行；
- 或者：仓库 → **Actions** → 左侧 **studio** → 右侧 **Run workflow** → Branch 选 `main` → **Run workflow**。
  这个按钮要等 `.github/workflows/studio.yml` 进入 main 之后才会出现。

打开这次运行，它有三个作业：

| 作业 | 正常时 |
|---|---|
| **test** | Worker、Runner、网页三组测试全部通过（某一部分还不在仓库里时只显示一条提示，不算失败） |
| **build** | 生成 `studio/_site`，复制 `_headers`，Worker 试打包（dry run）通过，上传构建产物 `studio-site` |
| **deploy** | 只在 main 上运行，依次是下面四步 |

**deploy** 的步骤：

| 步骤 | 正常时 |
|---|---|
| Are the secrets set? | 没有输出。缺了 Cloudflare 的 Secret，这里出现黄色警告并列出名称，后面的步骤被跳过：运行仍显示成功，但**什么也没部署**。只缺 `MINIMAX_API_KEY` 时，网站照常部署，Tao-S1 保持关闭 |
| Deploy the Worker with the app, then give it its secrets | 出现 `Uploaded tcmscience-studio`、`Deployed tcmscience-studio triggers`、`science.impf.ai (custom domain)`，以及 `Success! Uploaded secret MINIMAX_API_KEY`（设了盐还有 `VISITOR_SALT`） |
| Check the site, its headers, and one tiny Tao-S1 call | 最多等 10 分钟让新域名和证书生效（期间每 10 秒一行 `waiting for …`），再检查首页的响应头，并用 Tao-S1 做一次极小的调用（最多 8 个 token）。成功时最后一行类似 `{"url":"https://science.impf.ai","model":"Tao-S1",…,"headers":"ok","call":"ok",…}` |

## 第 9 步　验证

1. 浏览器打开 <https://science.impf.ai/v1/health>，应看到：

   ```json
   {"ok":true,"service":"tcmscience-studio","version":"1","model":"Tao-S1","models":["Tao-S1"],"max_output_tokens":8192,"limits":{"per_minute":40,"per_day":1200,"tokens_per_day":10000000}}
   ```

   如果是 `"ok":false`，说明 Worker 已上线但没有密钥（或 `RELAY = "off"`）：补上 `MINIMAX_API_KEY` 后重跑 studio 工作流。
2. 打开 <https://science.impf.ai/>，按 `Ctrl + F5`（Mac 上是 `⌘ + Shift + R`）强制刷新：
   - 模型选择里有 **Tao-S1**，说明“无需密钥；请求经 science.impf.ai 中继转发至模型服务，有调用频率限制”；
   - 问一个问题，回答正常出现；
   - 开发者工具（F12）→ Console 里输入 `crossOriginIsolated`，应为 `true`（Python 工具可以被中断）。
3. Cloudflare 控制台 → **Workers & Pages** → **tcmscience-studio**：
   - **Settings → Domains & Routes** 里有 `science.impf.ai`（Custom domain）；
   - **Settings → Variables and Secrets** 里，`MINIMAX_API_KEY`（和 `VISITOR_SALT`）是 Secret（值不显示），`UPSTREAM_BASE`、`PER_DAY` 等是明文变量；
   - **Metrics** 里能看到 `/v1` 的请求数和错误。网页文件由 Cloudflare 直接发送，不计入这里。中继不记录访客写的内容。

## 日常维护

| 要做的事 | 怎么做 |
|---|---|
| 更换 MiniMax 密钥 | 在 MiniMax 控制台新建密钥 → 仓库 Secrets 里点 `MINIMAX_API_KEY` 的 **Update**，粘贴新值 → **Actions → studio → Run workflow**（main）→ 检查通过后，在 MiniMax 控制台删除旧密钥 |
| 更换 Cloudflare 令牌 | 按第 2 步新建令牌 → 更新 `CLOUDFLARE_API_TOKEN` → 重跑 studio → 在 Cloudflare 删除旧令牌 |
| 调整限额、模型、允许的网页来源 | 改 `studio/edge/wrangler.toml` 的 `[vars]`，合入 main 后自动重新部署。`PER_MINUTE`、`PER_DAY`、`TOTAL_PER_DAY` 是每人每分钟、每人每天、全体每天的模型调用次数；`TOKENS_PER_DAY`、`TOTAL_TOKENS_PER_DAY` 是每人每天、全体每天的 token。一个问题通常调用模型好几次。在控制台里直接改的变量会被下次部署覆盖 |
| **暂停 Tao-S1（立即）** | Cloudflare → tcmscience-studio → **Settings → Variables and Secrets**，删除 `MINIMAX_API_KEY`。网页检测到中继没有密钥后，改请访客用自己的模型；网站照常。**同时**删除仓库 Secret `MINIMAX_API_KEY`，否则下次部署会把它写回去 |
| 暂停 Tao-S1（持久） | 把 `wrangler.toml` 的 `RELAY` 改为 `"off"`，合入 main。部署后的检查会显示 `skipped (RELAY off)`，照常通过。恢复时改回 `"on"` |
| 看用量 | MiniMax 控制台的用量或账单页；Cloudflare 里 Worker 的 **Metrics** |
| 网页的内容安全策略 | 目前是“只报告”（`Content-Security-Policy-Report-Only`，见 `_headers`）。在浏览器 Console 里确认长期没有违规报告后，可以把这一行的名字改为 `Content-Security-Policy` 使其生效，同时改 `src/site.js` |

## 常见问题

| 现象 | 原因 | 处理 |
|---|---|---|
| studio 运行成功，但有黄色警告 `science.impf.ai is not deployed until these repository secrets are set: …` | 列出的 Secret 没设，或名称拼错 | 按第 5 步补上（名称全大写、一字不差），重跑 studio |
| 黄色警告 `no MINIMAX_API_KEY secret` | 没设 MiniMax 密钥 | 网站已上线，Tao-S1 关闭。要开放 Tao-S1 就按第 1、5 步补上后重跑 |
| test 或 build 作业失败 | 代码的测试没过，或构建出错 | 打开失败的步骤看日志；部署不会在测试失败时进行，线上仍是上一个版本。改了 `wrangler.toml` 的 `[vars]` 之后 Edge 步骤失败：日志里 `test/config.test.js` 列出了写错的变量（如 `RELAY` 只能是 `"on"`/`"off"`，数字要写整数，调用次数限额是 `0` 或至少 16），改正后再提交 |
| 部署步骤报 `Authentication error [code: 10000]` | 令牌的权限或账户不对，或 `CLOUDFLARE_ACCOUNT_ID` 填错 | 核对 Account ID；按第 2 步重建令牌（模板 Edit Cloudflare Workers，账户选对） |
| 部署步骤报 `No access to the specified resource`（地址里有 `workers/routes` 或 `domains`） | 令牌的 Zone Resources 里没有 impf.ai | Cloudflare → API Tokens → 该令牌的 **Edit** → Zone Resources 加上 impf.ai，然后重跑。令牌的值不变，GitHub 里不用改 |
| `science.impf.ai` 原来指向别处，部署后变成了这个网站 | GitHub 上的部署不询问就替换已有的 DNS 记录或另一个 Worker 的自定义域名（第 7 步） | 原来的用途需要恢复时，在 Cloudflare 里给它换一个子域名重新设置；或把本站改到别的子域名（`wrangler.toml` 的 `pattern`）后重跑 studio |
| 部署步骤报 `files over 25 MiB` 或文件数超过 20 000 | 构建产物超出 Workers 静态资源的限制 | 大文件改从 CDN 加载，或拆分 |
| 检查步骤报 `… does not answer … after 600 s` | 新域名或证书还没生效 | 等几分钟后重跑 studio；也可以在 Cloudflare 确认 Worker 的 **Domains & Routes** 里有这个域名 |
| 检查步骤报 `lacks Cross-Origin-Opener-Policy … _headers` | 部署的网页里没有 `_headers` | 确认 build 作业的 “Build the app” 步骤复制了 `studio/edge/_headers`，重跑 |
| 检查步骤报 `Tao-S1 is off` | 写入机密那一步没有成功，或 Worker 上没有 `MINIMAX_API_KEY`（`RELAY = "off"` 不会报这个：那是暂停，检查照常通过） | 确认仓库 Secret `MINIMAX_API_KEY` 已设，重跑 studio |
| 检查步骤报 `400 upstream_rejected`，后面跟着 `calling https://api.minimax… directly: …` | 模型服务拒绝了这次调用。后半句是检查步骤用仓库里的密钥**直接**问模型服务得到的原话（密钥已遮掉），按它判断：`base_resp 2013`（参数无效）多半是这把密钥用不了 `MODELS` 里的模型（例如 Token Plan 订阅密钥、另一个账户或站点的密钥）；`directly worked` 说明密钥和模型都没问题 | 换一把按量付费的 API Key（第 1 步），或把 `MODELS` 改成这把密钥可用的模型名；与 TaoChronos 共用同一把已验证可用的密钥也可以。改后重跑 studio |
| 检查步骤刚部署完就报 `Tao-S1 is off`，但 `/v1/health` 随后显示 `"ok":true` | 刚写入的机密要几秒到一两分钟才到达各地的边缘节点 | 检查步骤现在最多等 2 分钟（`--settle`）。仍报这个错时，确认仓库 Secret `MINIMAX_API_KEY` 已设，重跑 studio |
| 检查步骤报 `the model service refused the key` | 国际站密钥配了国内地址（或反之），或者密钥已删除、填错 | 按第 6 步改 `UPSTREAM_BASE`；或更新 `MINIMAX_API_KEY` 后重跑 |
| 检查步骤或网页提示“额度暂时用完了”“暂时繁忙” | MiniMax 余额不足，或 Token Plan 当前窗口的额度用完、调用过于频繁 | 充值，或等下一个窗口。公开网站建议用按量付费的密钥 |
| 网页提示“Tao-S1 只供 TCMScience Studio 网页使用” | 网页的地址不在 `ALLOWED_ORIGINS` 里，或浏览器扩展去掉了 `Origin` | 换了网址时，把新地址加进 `ALLOWED_ORIGINS` 并重新部署 |
| 访客看到“请求太频繁”或“今天的 Tao-S1 免费额度已用完” | 触发了中继的限额（同一出口 IP 的访客共用） | 这是正常的保护，北京时间 8:00 重置。需要时调高 `PER_MINUTE`、`PER_DAY`、`TOTAL_PER_DAY`；“（按用量计）”的是 token 额度 `TOKENS_PER_DAY`、`TOTAL_TOKENS_PER_DAY` |
| 访客看到“Tao-S1 暂时不可用” | 限额计数（Durable Object）暂时连不上，中继宁可拒绝也不放出不计数的调用 | 通常几秒内恢复；持续出现时看 Worker 的 **Logs** |
| 访客看到 Cloudflare Error 1102（CPU 超时） | 很长的对话超出免费计划每次请求 10 ms 的 CPU 时间 | 升级 Workers 付费计划（每月 5 美元）；或调低 `MAX_BODY_BYTES` |
| 访问网站得到 Cloudflare Error 1027 | Workers 免费计划每天 10 万次请求用完了（UTC 零点重置）。只有 `/v1` 和找不到文件的请求计数，网页文件不计 | 全体日限额（默认 20 000 次模型调用）远低于此，一般不会发生。发生时可调低限额，或升级付费计划 |

## 安全须知

- 密钥只在 GitHub Secrets 和 Worker 机密里，网页、仓库、日志里都没有。浏览器发给中继的请求不带任何密钥。
- 能改仓库 Secrets 或 Cloudflare Workers 的人就能换掉密钥。只把仓库管理员和 Cloudflare 账户权限给可信的人。
- 令牌泄露时，在 Cloudflare 的 **API Tokens** 里对它 **Roll**（重新生成）或删除，再更新 GitHub Secret。MiniMax 密钥泄露时，
  在 MiniMax 控制台删除它，再按“更换 MiniMax 密钥”换新。
- 中继限制了来源、模型、输出长度和每人、全体的调用次数与 token 用量，这些限定的是被滥用时最多能花多少；`Origin` 检查只防浏览器里的盗用，
  脚本可以伪造它。真正的费用上限是 MiniMax 账户余额。
- 中继不保存访客的 IP 和对话内容：计数只用哈希，Worker 的调用日志已关闭，错误日志只记状态码。
