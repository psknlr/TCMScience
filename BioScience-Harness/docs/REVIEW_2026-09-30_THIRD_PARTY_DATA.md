# 第三方数据接入复核（2026-09-30）

> 复核对象：`docs/THIRD_PARTY_DB_CONNECTOR_SPEC.md`（v2）及其实现——数据源卡片、传输层、
> 58 个在线接口、8 个解析器、2 个抓取器、快照 / 账本 / 发布检查、Skill 数据授权。
> 方法：逐条对照规范前部（§0、§1、§1.1、§2.2）提出的问题读代码；下载 NPASS 2.0、CMAUP 2.0、
> STRING v12 的真实文件核对数据层面的判断；对 58 个源的 153 个操作做了一次实时调用。
> 文中数字均可用文末"复现"一节的命令重算。

---

## 0. 结论

规范前部提出的问题，**大部分已经在代码里解决，而且解决得是对的**：计算预测不再冒充
`PRECLINICAL`，主张许可改成了按集合判断，`web` 方式默认关闭且需要人工审批，数据源卡片在
演化边界之内，快照带内容哈希并在加载时重新校验。

但复核发现了 **四个高严重度问题**，都是"报告成功、数据却不对"这一类，而不是崩溃：

1. **路径参数没有编码**：中文名（如 `黄芩苷`）根本发不出去，带空格的名字被当作非法 URL，
   `a#b` 被当作 `a` 查询**并报告成功**，`?` 能注入查询串。
2. **重定向绕过主机白名单**：`urlopen` 静默跟随 30x，"未声明主机一律 DENIED"只管第一跳。
3. **HTML 页面冒充数据**：登录页、验证码、WAF 挑战页以 200 返回时，被当作成功结果——
   这恰恰是"逆向封装"类数据源最常见的失效方式（§1 记录的"HERB 的文件链接返回 HTML"就是它）。
4. **以行号作记录 ID，且源文件有大量完全重复行**：NPASS 2.0 活性表 958,866 行里有 87,882 行
   完全重复，CMAUP 2.0 有 1,230 行；它们变成了多条"不同"的边，而行号 ID 也让跨版本比较失去意义。

以上四项和另外十四项已经修复；改变代码行为的每一项都有回归测试（L4 只改正文档）。另有两项怀疑经核实**不是**缺陷，撤回（§4）。
还有十一项需要决定或需要真实文件才能做，列在 §5，没有假装做完。

**第二轮（同日）**：§5 的十一项中关闭了七项（O1、O2、O3、O5、O6、O8、O9），O4 维持现状（有意为之），
O7、O10、O11 仍待决。用真实数据重跑时又发现四个问题，均已修复（§7）：

5. **PubChem 基因号 → UniProt 映射多数不是 Swiss-Prot 主号**（O9 核实为真）：旧映射只有 23.7% 落在
   主号上，与 Reactome 的交集从应有的 11,201 个蛋白缩到 2,422 个。规范和 README 中筛选模式的数字
   是在这个映射上算出的。
6. **Open Targets 分页在同分处漏靶点**：接口对同分靶点每次请求排序不同，分页会重复一些、漏掉同样多，
   总数照样对得上。第一轮加的"每个靶点恰好出现一次"检查在实时接口上当场触发。
7. **Skill 声明的数据版本没有被执行**：运行器取每个源最后记录的快照，`npass@2.0` 这类版本声明被忽略。
8. **发布检查把实体名称当成了陈述的措辞**：通路名 "PPARA activates gene expression" 被读成"声称激活"，
   6 条真实的通路假说被误拒。这一条方向相反——是拒绝了不该拒绝的，而不是放过了不该放过的。

受影响的真实数据结果已重算，见 §7.6。

---

## 1. 规范前部提出的问题，逐条核对

"2026-09-23"一列是规范写的状态，"复核"一列是本次对照代码的结果。

### 1.1 §0：对外部方案的四点判断

| # | 规范提出的问题 | 复核 |
| --- | --- | --- |
| 1 | 外部方案范围过大（科研 Skill 平台总路线） | 设计判断，不涉及代码。维持。 |
| 2 | 8 类数据对象大多已有等价实现，只需新增三样 | 已落实：`sources/cards.py`、快照 manifest、节点/边表，没有为每个库建实体类。✓ |
| 3 | 白名单写在 agent 可写的 Skill 目录里，可自我授权 | **部分落实**。卡片层（启用开关、`web_approval`）在 `evolution/boundary.py` 保护之内 ✓；但"∩ 当前权限配置"这第三项**没有任何调用方提供**——`run_skill(allowed=None)` 是唯一路径，等于"Skill 声明 ∩ 已启用卡片"。8 张卡片全部启用，所以实际上 Skill 文件本身就是白名单。**本次**：`run_network_pharmacology.py --allow-source KEY`（可重复）把第三项交给运行方；`provenance.json` 新增 `source_allowance`，未提供时写明 `"unrestricted"`，不再让两项交集冒充三项交集。与许可证联动见 §5-O1。 |
| 4 | 首批数据源遗漏 LOTUS / NPASS / CMAUP | 已解析，并用真实文件跑通。✓ |

### 1.2 §0：对 v1 规范的两处修正

| # | 问题 | 复核 |
| --- | --- | --- |
| 5 | v1 把算法预测映射到 `PRECLINICAL` | 已修正：`EvidenceTier.COMPUTATIONAL_PREDICTION = 0`；`CLAIM_SUPPORT` 按集合判断；`applicability` 用的是 `relation.tier not in admitted`，不再是大小比较。✓ |
| 6 | v1 把网页抓取当作常规通道 | 已收紧：`web` 默认关闭、需审批、卡片校验 ≤ 1 次/秒 ✓。但**卡片上的 `rps` 从未被传输层读取**（见 §2-M6），而且至今**没有任何 web 客户端**，这些规则只能在卡片上校验，无处执行。本次让卡片速率成为实际速率；web 客户端写出之前必须满足的规则见 §3。 |

### 1.3 §1：现状核查表中的"缺口"

| 行 | 规范列出的缺口 | 复核 |
| --- | --- | --- |
| 在线接口 | 中医药库一个都没有；BindingDB、ICD-11、ChiCTR 没有 | BindingDB 已有卡片（`api` + `manual`），解析器**仍未用真实文件验证**。中医药库、ICD-11、ChiCTR 未变——已在规范"仍未完成"中。 |
| 批量下载 | 下载之后没有代码解析 | 已解决。✓ |
| 批量下载 | `default_runtime` 不加载 `BulkDatasetProvider` | 仍如此（只有 `cli datasets` 和 `doctor` 用它），但已被快照流水线取代，不再是缺口。规范应写明。 |
| 批量下载 | 24 个文件只有 5 个锁定校验值 | **本次锁定 12 个**：NPASS 2.0 ×6、CMAUP 2.0 ×5、STRING v12 links，均先核对与声明大小逐字节一致再写入 sha256。现为 **17 / 24**。剩下 7 个是 `current` / `latest` 地址（Reactome、HGNC、KEGG、NCBI taxdmp、ChEMBL 映射、NP Atlas），每次上游发布都会变，锁定只会让每次发布都失败；它们的确切字节仍由每个快照 manifest 的 `raw_files` 固定。 |
| 传输层 | 不读 `Retry-After` | 已解决（秒数与 HTTP 日期两种格式）。✓ |
| 传输层 | 缓存键不含数据版本 | 机制已有（`HTTPRequest.version`），但两个抓取器都 `use_cache=False`，没有调用方设置它。影响有限。 |
| 传输层 | 截断时仍报 `SUCCEEDED` | 已改为 `DEGRADED`。✓ |
| 传输层 | 缓存默认关闭、无 TTL | 未变。抓取器不走缓存，影响有限（§5-O5）。 |
| 传输层 | 重试耗尽后报 `FAILED` | 未变。与状态词表一致（"执行了且上游出错"），建议保留并在规范里写明是有意的（§5-O4）。 |
| 传输层（"已有"列） | "未声明主机一律 `DENIED`" | **对重定向不成立**，见 §2-H2，已修复。 |
| 溯源 | 无记录级许可证、版本、抓取时间 | 已解决：每条边有 `license`、`source_record_id`、`snapshot_id`；manifest 记录原始文件哈希。✓ |
| 溯源 | `ProvenanceCapsule` 字段从未填写 | 这个类**仍然从未被实例化**；同样的字段写进了 `provenance.json`。规范 M3 写"填进 `ProvenanceCapsule`"不准确，应改为"写入 `provenance.json`"。 |
| 编译与发布 | `DESIGNS` 没有 `in_silico` | 已解决（`PREDICTIVE_DESIGNS`）。✓ |
| TCM 证据分级 | `PRECLINICAL` 混杂；按大小比较 | 两处都已修正。✓ |
| 分析工具 | 没有网络拓扑类工具 | 已有（`analysis/network_pharmacology.py`，只用标准库）。✓ |

### 1.4 §1.1 与 §2.2

- `SkillDirectoryProvider` 已被实例化（`psh/assembly.py`），仓库 `skills/` 会被发现。✓
- v1 自我修正五条（快照优先、证据两轴、web 默认关闭、沿用现有后端状态、只规定必须统一的部分）均已落实。✓

---

## 2. 本次发现的问题与处理

严重度：**高** = 报告成功但数据错误，或安全边界失效；**中** = 结论或完整性受影响；**低** = 准确性与可维护性。
"真实数据"一列是在 2026-09-30 下载的官方文件上测得的。

| ID | 严重度 | 位置 | 问题 | 真实数据 / 证据 | 处理 |
| --- | --- | --- | --- | --- | --- |
| H1 | 高 | `providers/public_apis.py` `Operation.render` | 路径占位符原样拼接：`黄芩苷` 触发 `UnicodeEncodeError`，请求根本没发出；`berberine chloride` 被拒为非法 URL；`a#b` 被服务器当作 `a` 收到，结果**以成功返回**；`x?y=1` 注入查询串 | 本地服务器复现四种情况；修复后 `berberine chloride` 在 PubChem 实时解析为 CID 12456，`黄芩苷` 得到如实的 `PUGREST.NotFound` | 只编码会改变请求的字符（非 ASCII、空白/控制符、`#`、`?`、`%`）。`/` 和 `>` 保留：实测 bioRxiv 对 `%2F`、MyVariant 对 `%3E` 都返回 404。**153 个操作的示例请求一个字节都没变**（有测试） |
| H2 | 高 | `backends/http.py` | `urlopen` 静默跟随重定向，主机白名单（组件的 `permissions.network`）只约束第一跳；https 可被降级到 http | 本地服务器：`127.0.0.1` → `localhost` 的 302 被跟随 | 专用重定向处理器：只允许到声明过的主机，禁止 https→http；违反即 `DENIED` 并说明原因；`meta.redirected_to` 记录落点 |
| H3 | 高 | `backends/http.py` | 以 200 返回的 HTML 页面（登录、验证码、WAF 挑战、错误页）在请求 JSON / 文本 / XML 时被当作成功数据（`{"format": "xml", "text": "<html>…"}`）；缓存命中同样 | §1 已记录 HERB 下载链接返回 HTML | 调用方没有明确要 HTML 时，HTML 文档报 `UNAVAILABLE` 并说明"登录、验证码、挑战或错误页不是数据"；能解析为 JSON 的正文不受影响（有的服务器给 JSON 标 `text/html`）；旧缓存中的 HTML 条目不再返回 |
| H4 | 高 | `parsers/npass.py`、`parsers/cmaup.py` | 活性表没有记录 ID，解析器用**行号**：插入一行就让后面所有记录"变了"；完全重复的行变成多条边 | NPASS 2.0：958,866 行中 87,882 行完全重复；全量解析后被引用的蛋白靶点边 157,579 → **153,920**（去重 3,659）。CMAUP 2.0：28,871 行中 1,230 行重复；26,934 → **25,743**（去重 1,191） | 记录 ID 改为行内容摘要（`common.row_digest`）；重复行只保留一次并计数（"exact duplicate of an activity row already read"）；已有的丢弃计数不变 |
| M1 | 中 | `analysis/network_pharmacology.py` | 下界判断只认 `>` 和 `>=`，NPASS 还用 `>>`："IC50 >> 1000 nM"（比 1 µM 弱得多）会被当作 ≤ 10 µM 的命中 | NPASS 全量有 8 行 `>>`，其中 5 行会被误判为命中 | 任何以 `>` 或 `≥` 开头的关系都是下界，永不算命中；定义只有一处（`sources.schema.is_lower_bound`） |
| M2 | 中 | `sources/release.py` | 发布检查（所有 Skill 都要过的门）不看测量值：一条只有下界的 `targets` 边（"IC50 > 100 µM"，化合物没达到这个数）可以支持"作用于该靶点"的主张 | NPASS 潜力值中 52,917 / 243,314（22%）是 `>`，与活性结果同用 `targets` 谓词 | 引用的 `targets` 边若只有下界，拒绝发布并说明原因；与 `tested_against` 同一处理 |
| M3 | 中 | `parsers/bindingdb.py` | 多链复合物的亲和力被记到"第一条有 UniProt 号的链"名下——对 GABA-A α1β2γ2 这类异源复合物是任意的 | BindingDB 表头自己写着"> 1 implies a multichain complex" | 只有一条链有蛋白号时仍归到它（原测试的情形，保留）；多条链是**不同**蛋白时计数并丢弃，与 PubChem 解析器"一个基因对应两个蛋白视为歧义"的处理一致 |
| M4 | 中 | `sources/fetch_opentargets.py` | 只检查总条数：分页边界若在两次请求之间移动（新版本上线、同分排序变化），会出现"重复若干、漏掉同样多"而总数仍相符；数据版本中途变化也没检测 | — | 要求每个靶点恰好出现一次；分页结束后重新读数据版本，变化即报错，不生成快照 |
| M5 | 中 | `sources/cards.py`、`sources/snapshot.py` | 版本号正则 `[^\s@]+` 接受 `npass@../../audit`；key/version 直接拼成快照目录，`build_snapshot` 可写到快照根目录之外（`skill.yaml` 是 agent 可写文件） | — | 版本号限定为 `^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$`（现有全部版本格式都符合）；构建和加载前都校验 |
| M6 | 中 | `sources/cards.py`、`backends/http.py` | 卡片上的 `rps` 没有任何代码读取，传输层按自己的表限速 | Open Targets 卡片写 1 次/秒，实际 5 次/秒；BindingDB 卡片 1 次/秒，实际默认 2 次/秒；"web ≤ 1 次/秒"无法在线上执行 | `HTTPBackend` 默认速率表 = 自带表与所有卡片取更严者（`cards.request_rates`） |
| M7 | 中 | `sources/snapshot.py`、`sources/build.py` | 规范 §3.7 要求漂移检查比较"行数和 ID 的增删"，代码只比行数；而且**所有构建入口都不传 `previous`，漂移检查从未运行过** | — | 增加记录 ID 增删比较（在 H4 之后才有意义）；有账本时自动取同一来源、同一范围的上一个快照作基线。**同一版本、同一批原始文件的重建不做比较**，否则上一快照的 ID 会进入内容哈希，破坏"同样输入、同样 ID"（有测试） |
| M8 | 中 | `analysis/skill_runner.py` | 见 §1.1 第 3 项 | — | 见上 |
| M9 | 中 | `backends/http.py` | 所有响应一律按 UTF-8 解码，忽略 `charset=gbk` / `gb18030` | 中文中医药网站和导出文件常用 GBK；GBK 页面按 UTF-8 解码后中文名全变成替换字符，状态仍是成功 | 按声明的字符集解码，未声明时用 UTF-8 |
| L1 | 低 | `backends/http.py`、`acquisition/downloader.py` | User-Agent 的联系地址是 `https://localhost`，且两处各有一份 | Wikidata 的 UA 政策要求可联系的地址；NCBI 要求提供联系人 | 统一为项目地址加 `BIOAGENT_CONTACT` 环境变量 |
| L2 | 低 | `parsers/bindingdb.py`、`sources/cards.py` | BindingDB 记录级许可证规则在解析器和卡片各写一份；卡片版区分大小写且无人调用 | — | 解析器改用卡片的 `record_license`（不区分大小写）；卡片因此计入 BindingDB 快照的代码哈希 |
| L3 | 低 | `sources/cards.py` | NPASS、CMAUP 许可为"仅限学术免费使用"，`commercial_use` 却标 `unknown`；而且这个字段无人读取 | — | 改为 `forbidden`（条款未授予商业使用）；字段仍无人读取，见 §5-O1 |
| L4 | 低 | `sources/schema.py` | `check_claim` / `licensed_claims` 的文档称其为"发布门规则"，实际是并集语义；发布门用的是"最弱一环"（交集） | — | 文档改正：并集用于"同一步骤的多个替代来源"，路径请用 `release.check_release` |
| L5 | 低 | `analysis/network_pharmacology.py` | 所有未通过的潜力值都记为"高于阈值或不是潜力指标"，NPASS 的 µg/mL 值也被这样描述 | NPASS 潜力值中 22,882 条（9.4%）是 `ug.mL-1`——是潜力指标，可能低于 10 µM，只是没有分子量无法与摩尔阈值比较 | 分成四条原因：不是潜力指标 / 非摩尔单位 / 只有下界 / 高于阈值 |

### 2.1 实时核对：修复没有破坏任何在线接口

修复 H1–H3 之后，对 58 个源的全部 153 个操作做了一次实时调用：**150 个成功**；3 个失败都是
Wikidata 服务端对共享云地址的限流（HTTP 429，"Aggressively rate-limiting to 1 req / min"），
与本次改动无关——传输层的速率表注释早已预见这一点。**没有一个操作因为新的重定向检查或 HTML
检查而失败**。

---

## 3. "逆向封装"（`web` 方式）专项

### 3.1 现状

- 仓库中**没有任何 web 客户端**，也没有启用 `web` 方式的卡片。
- 58 个在线源、153 个操作全部是有文档的公开接口（REST / GraphQL / SPARQL），没有 HTML 抓取，
  也没有调用网站前端自用的未公开接口。
- 因此 v1 所说的"通过分析网页前端请求来封装"目前没有实现——这与 v2 "默认关闭、逐源审批"
  的立场一致。

### 3.2 写 web 客户端之前必须满足的规则

本次已经在传输层落实了其中四条（标 ✓），其余是写客户端时的前置条件。建议并入规范 §3.3：

1. **`api` 只指有文档、向第三方提供的接口。** 网站前端自用的未公开 JSON / XHR 接口按 `web`
   处理，同样需要审批——它随时可能改版，服务条款通常也覆盖"自动访问"。
2. **不绕过任何技术措施**：登录、验证码、滑块、请求签名或反爬 token、频率限制、IP 封禁。
   遇到即停止并报告，不重试、不换出口。✓ 这类页面现在会被报为 `UNAVAILABLE`（H3）。
3. **审批留档**：`web_approval` 记录谁、何时，以及依据的服务条款与 robots.txt 的地址和当时
   内容的哈希——条款会改，审批要能说明当时看到的是什么。
4. **单条、按需**：每次调用一条记录；不翻页遍历；每次运行设请求预算。
5. **一律经过 `HTTPBackend`**，从而继承：主机白名单（含重定向，✓ H2）、HTML 拒绝（✓ H3）、
   卡片速率（✓ M6）、可联系的 User-Agent（✓ L1）、64 MiB 上限。
6. **按声明的字符集解码**（✓ M9），并统计替换字符：GBK / GB18030 在中文站点和导出文件中很常见。
7. **抓取到的文本只作为数据进入模型上下文**（PSH 的标签与隔离），不能作为指令——网页是
   提示注入的天然载体。
8. **记录级许可证默认"未授权再分发"**：web 来源的原文不进入发布物，只进入快照与引用。
9. **健康检查**：改版即失效必须能被发现（规范已列为后续项目）。

### 3.3 合规要点（供参考，不是法律意见）

- **数据库的著作权**：《著作权法》（2020 年修正）第十五条规定，对"不构成作品的数据或者其他
  材料"的选择或编排体现独创性的，构成汇编作品。整库复制风险最大——对应规则 4。
- **技术措施**：《刑法》第二百八十五条规定了采用技术手段获取计算机信息系统中数据的刑事责任。
  "不绕过登录、验证码或限流"不只是礼貌问题——对应规则 2。
- **不正当竞争**：司法实践中已有将大规模抓取并实质性替代使用他人数据认定为不正当竞争的案例。
  单条、按需、带引用的使用与此有本质区别——对应规则 4 和卡片的 `citation`。
- **引用义务**：《科学数据管理办法》要求使用科学数据时注明来源。`SourceCard.citation` 已是必填。✓

---

## 4. 核实后撤回的两项怀疑

如实记录，因为它们本身也是结论：

- **"NPASS 的 µM 值被潜力过滤漏掉了"**：过滤只接受单位为 nM 的值，而测试夹具里用的是 `uM`。
  下载真实文件核对：潜力值（IC50/Ki/Kd/EC50）共 243,314 条，**90.0% 为 nM，其余是质量浓度，
  没有一条是 µM**；CMAUP 的潜力值 100% 为 nM。所以只接受 nM 是对的。真正的问题是那 9.4%
  的质量浓度被错误描述（L5）。
- **"解析器代码哈希只覆盖函数本身"**：`snapshot.code_hash(函数)` 确实如此，但实际构建路径
  `build._code` 传入的是解析器模块加上共享的 `common` 与 `schema` 的源码，已经覆盖。唯一需要
  补的是本次修复让 BindingDB 解析器依赖了 `cards.py`，已把它计入 BindingDB 的代码哈希。

---

## 5. 未修复、需要决定或需要真实文件的事项

第一轮列出十一项；"第二轮"一列是同日后续处理的结果。

| ID | 事项 | 第二轮 |
| --- | --- | --- |
| O1 | 与许可证联动的运行授权（例如商业用途的运行拒绝 NPASS、CMAUP） | **已修复**。运行声明用途（`--purpose academic\|commercial`，默认 `academic`，写入 `provenance.json`）。商业用途只授予卡片写明 `commercial_use: allowed` 的源，`unknown` 同样拒绝——"没查过"不是许可。现有卡片：LOTUS、STRING、Reactome、Open Targets、BindingDB 为 `allowed`，NPASS、CMAUP 为 `forbidden`，PubChem BioAssay 为 `unknown`；商业用途运行本 Skill 时后三者被拒绝，原因写进 `sources_refused`。 |
| O2 | 快照账本：截尾或回滚不可检测；只有进程内锁 | **已修复**。追加时持有跨进程文件锁（`<账本>.lock` 上的 `flock`）：实测两个进程并发追加，无锁 3 次中 3 次断链，有锁 100 次中 0 次。每次运行把读到的链头（序号 + 哈希）写进 `provenance.json` 的 `ledger_head`；`verify(expected_head=…)` 与 `run_network_pharmacology.py --ledger-head-from 上次的provenance.json` 拒绝已不含该条目的账本（截短、回滚或改写）。作为锚点的那份 provenance 需保存在 agent 不可写处。 |
| O3 | `open_text` 以 `errors="replace"` 读文件 | **已修复**。`open_text` / `read_rows` 接受 `encoding`（ETCM / HERB 导入时传 `gb18030`）；不可解码的字节仍以替换字符读入（一个坏字节不至于丢掉整个文件），但传入 `report` 时逐行计为警告，写进快照 manifest；没有警告时 manifest 不变，已有快照 ID 不受影响。NPASS、CMAUP、LOTUS、Reactome 解析器已传入 `report`；本次下载的真实文件全部是有效 UTF-8。 |
| O4 | 重试耗尽、`Retry-After` 超过上限时报 `FAILED` | **维持**。与状态词表一致（"执行了且上游出错"），是有意的。 |
| O5 | 响应缓存无 TTL | **已修复**。`HTTPBackend(max_age_s=…)`：超过时限的缓存条目视为未命中并重新请求；默认 `None`（永久）只适合带数据 `version` 的请求。 |
| O6 | 批量下载器不检查重定向落点 | **已修复**。下载器改用传输层同一个重定向处理器：只允许同一主机，禁止 https→http；被拒绝的重定向不重试（重试也会被同样重定向），报 `DownloadError`。25 个下载地址实测无一重定向，现有下载不受影响。 |
| O7 | web 客户端 | 未变：尚不存在；写之前满足 §3.2。 |
| O8 | "BATMAN-TCM 结果一律标为预测"等规则没有落地 | **已修复**（落在卡片校验里）。`PREDICTION_ONLY`：BATMAN-TCM 的所有默认边必须是 `prediction`；`PREDICTED_TARGETS`：ETCM、TCMSP、TCMToxDB 的 `targets` 默认必须是 `prediction`。为这些源写的卡片若声明更高的知识等级，加载卡片时即报错；解析器只能在记录本身写明"经验证"时逐条上调。 |
| O9 | PubChem 基因号 → UniProt 取别名文件中第一个 `UniProt_AC` | **确认是缺陷，已修复**，影响重大，见 §7.1。 |
| O10 | BindingDB 解析器未经真实文件验证 | 未变：下载需人工操作，本环境无法代为完成。 |
| O11 | Wikidata 三个操作在云地址上被服务端限流 | 未变：不是代码问题；在 Wikidata 待遇更好的网络上可用 `rates=` 放宽。 |

**对已发表的真实数据结论的影响**（第一轮）：规范中葛根芩连汤的结论（1,792 个成分、370 个实测靶点、
66 条通路，以及改用"实测过的蛋白"背景后无显著通路）**不受第一轮修复影响**，已核实（第二轮发现的
问题影响 PubChem 筛选模式和 Open Targets 的数字，见 §7.6）：

- 在四味药的 NPASS 子集上重跑解析，丢弃计数与规范 M2 一节**完全一致**（细胞系 / 整体生物
  43,297 条，无文献 202 条）——说明本次下载的正是规范所用的文件，去重也没有改动原有计数；
  子集内另去掉 283 条完全重复的边。命中靶点是集合，重复边不改变它。
- M1 涉及的 5 行 `>>` **不在**这四味药的成分里，所以原结果中没有被它误判的命中。

**一次性影响**：H4 改变了 NPASS、CMAUP 边的记录 ID。已有快照不受影响（各自带哈希、照常加载）；
下一次**新版本**构建与旧快照比较时，边 ID 会显示为全部增删并进入人工复核——这是 ID 方案改变
造成的，确认一次即可。

---

## 6. 验证

```bash
cd BioScience-Harness
PYTHONPATH=src:../PSH-Harness/src python -m pytest tests -q     # 1094 passed, 7 skipped
PYTHONPATH=src:../PSH-Harness/src python scripts/check_lockfile.py
PYTHONPATH=src python scripts/verify_connectors.py --no-write   # 实时：150/153，3 个为 Wikidata 429
cd ../PSH-Harness && python -m pytest tests -q                  # 1084 passed
```

### 复现真实数据数字

```bash
curl -O https://bidd.group/NPASS/downloadFiles/NPASSv2.0_download_naturalProducts_activities.txt
# 其余 NPASS、CMAUP 文件同目录；地址见 src/bioagent/acquisition/sources.py
PYTHONPATH=src python -c "
from bioagent.sources.parsers import parse_npass, parse_cmaup
for f in (parse_npass, parse_cmaup):
    r = f('.'); print(f.__name__, dict(r.report.dropped))"
```

---

## 7. 第二轮：真实数据重跑中发现的问题

第一轮之后，用修复后的代码在真实文件上从头重建快照、重新抓取 Open Targets 与 PubChem、
重跑分析，以更新规范中的数字。过程中发现下面几个问题，均已修复并有回归测试。

### 7.1 N1（即 O9）PubChem 基因号 → UniProt：多数不是 Swiss-Prot 主号（高）

- **位置**：`parsers/pubchem_bioassay.py` `gene_to_uniprot`。
- **问题**：对每个 STRING 蛋白取别名文件中列出的第一个 `UniProt_AC`。这一列混有次要号和
  TrEMBL 号，顺序没有约定。Reactome 和其他快照只用主号，所以映射到非主号的蛋白与通路注释对不上。
  这些检测照常进入快照（丢弃计数不受影响），却在通路检验中不属于任何通路——没有报错，也没有计数。
- **真实数据**（STRING v12 别名文件 × UniProt 人类 Swiss-Prot 主号列表）：旧映射覆盖 19,197 个
  基因，其中**只有 4,547 个（23.7%）是主号**；14,354 个（74.8%）是 6 位但不是主号（次要号或
  TrEMBL），296 个（1.5%）是 10 位 TrEMBL 号。能与 Reactome 人类注释对上的蛋白只有 2,422 个；
  修复后映射 18,868 个基因，全部是主号，对上 11,201 个。
- **例子**：旧结果中起作用的 CYP1A2、CYP2C9、CYP2D6、CYP2E1 碰巧映射到了主号；但 **CYP3A4**
  被映射到次要号 P05184（主号 P08684），CA2、PTGS2、EGFR、PPARG、AKT1、TNF 被映射到 TrEMBL
  或次要号（如 PTGS2 → A8K802，EGFR → O00688），全部从通路检验中消失。对于"中药—药物相互作用"
  这个结论，漏掉最重要的药物代谢酶 CYP3A4 是实质性的缺失。
- **修复**：只接受 UniProt 人类 Swiss-Prot 主号列表中的号。列表作为新的下载项
  （`uniprot_human_reviewed_accessions.txt`，`bioagent datasets fetch` 可取）并作为原始文件计入
  快照哈希；缺少时拒绝构建，并提示下载命令。一个基因对应多个主号时仍视为歧义、不映射。
  STRING 解析器在有该文件时也优先选主号。

### 7.2 N2 Open Targets 分页在同分处漏靶点（高）

- **位置**：`sources/fetch_opentargets.py`。
- **问题**：接口每页最多 3,000 条（3,001 即报错）。同分的靶点，接口每次请求的排列顺序都不同，
  所以同一列表的相邻两页会在同分块上重复一些靶点、漏掉同样多，而总数照样对得上。第一轮 M4 加的
  "每个靶点恰好出现一次"检查在实时接口上当场触发，抓取失败——这是正确的失败：只比总数的旧代码会把
  这样的结果当作完整的保存下来。
- **真实数据**（2026-09-30，2 型糖尿病，平台 26.09，10,206 个靶点，4 页）：几次诊断性分页每次
  重复 1–11 个靶点，都在同分块跨页处（第 3 页末 10 个与第 4 页开头 103 个同分 0.00296 的靶点）；
  有一次两遍分页合起来仍漏 10 个，所以"多抓几遍取并集"也不可靠。同一靶点在两次请求中返回的
  `datatypeScores` 顺序也不同（10 个）。规范中 26.06 的结果是 2026-09-23 用只比总数的代码抓的，
  原始文件未保留，无法事后核对；同分块位于低分尾部，默认疾病基因集（遗传关联 ≥ 0.5）不太可能受
  影响，但"不设阈值"等敏感性分析可能受影响。
- **修复**：不再对一个列表分页。接口的 `BFilter` 按靶点 ID 前缀过滤（实测：对 ID 前缀有效、不区分
  大小写，也匹配基因符号前缀）。按 Ensembl ID 逐位分组，直到每组不超过 3,000 个，每组用一次请求
  整组取回；分组前先用 `size: 0` 的请求只取计数。每一层各组计数之和必须等于上一层，每个靶点必须
  恰好出现一次（符号前缀也会匹配，重叠是可能的，检查会发现），抓取前后的数据版本必须相同。2 型
  糖尿病实测：`ENSG000000` 1,306 个、`ENSG000001` 7,874 个（再分 10 组）、`ENSG000002` 1,023 个、
  `ENSG000003` 3 个，共 10,206 个，逐一出现一次。保存时把 `datatypeScores`、`proteinIds` 排成
  固定顺序。

### 7.3 N3 Skill 的数据版本声明没有被执行（中）

- **位置**：`analysis/skill_runner.py` `_latest`。
- **问题**：运行器取账本中每个源**最后记录**的快照，`skill.yaml` 的版本号被忽略：声明
  `npass@2.0` 的 Skill 会在最后构建的任何 NPASS 版本上运行，声明 `opentargets@26.06+…` 的会在
  26.09 上运行。`provenance.json` 记录了实际的快照 ID，所以事后可查，但契约没有被执行——规范 M3
  的验收标准就是"在指定的快照版本上运行"。
- **修复**：`npass@2.0` 只接受 2.0 版及其范围子集（`2.0+subset-…`），带范围的声明
  （`26.09+MONDO_0005148`）只接受该范围，不写版本才表示"最新"；没有相符的快照时拒绝运行，并列出
  账本中已有的版本。随包 Skill 的 Open Targets 声明改为 `26.09+MONDO_0005148`：接口只提供当前
  版本，26.06 已无法重新抓取。研究循环（`bioagent.research`）按源名而不是版本选快照、并把快照 ID
  预先登记，是另一种契约，未改动。

### 7.4 N4 发布检查把实体名称当成了陈述的措辞（中）

- **位置**：`sources/release.py` `_statement_problem`。
- **问题**：检查"陈述是否声称了作用方向"时扫描整句，包括句中引用的实体名称。分析生成的陈述写的是
  "……，<通路名> may be involved in its action"，而 Reactome 有大量带动词的通路名。这条检查是在
  规范 09-23 那次运行之后加入的（5811cad），测试夹具里的通路名没有动词，所以没有测出来。
- **真实数据**：以完整 Reactome 为背景时，66 条显著通路中 6 条被拒（如 "PPARA activates gene
  expression"），运行以非零状态退出；筛选模式阈值 10 与 50 下各有 1 条被拒（"WNT mediated
  activation of DVL"、"Aflatoxin activation and detoxification"）。同理，Reactome 中大量
  "Defective … causes …" 的疾病通路名、UniProt 名称 "Inhibitor of nuclear factor kappa-B kinase
  subunit beta" 也会被误读。
- **修复**：检查措辞前，把路径上实体（主语、宾语和各条支持边的两端）在已校验快照中记录的名称
  （`name` 与 `names`）从陈述中去掉，而且名称必须独立出现（不在更长的词里）。只去掉路径上实体的
  名称，Skill 不能靠自选的措辞为自己开脱。重跑后 66 / 66、8 / 8、8 / 8 全部通过。

### 7.5 其他（低）

- 两个抓取器把原始文件直接写到最终文件名，中途被中止会留下截断的文件（构建时会报 gzip / JSON
  错误，不会静默）。现在先写 `.part` 再改名，与批量下载器一致。

### 7.6 重算结果（2026-09-30，同一批原始文件，10,000 次置换用于筛选模式）

**不受影响的部分**（与规范一致，逐项核对）：整理型数据的默认运行——1,792 个成分，391 个被测
人类蛋白中 237 个 ≤ 10 µM，检验 959 条通路，0 条显著，命中率 61%，0 条主张；以完整 Reactome 为
背景时 12,155 个蛋白、1,684 条通路、66 条显著；STRING 子网络 237 个节点、624 条边、最大连通分量
195。两次独立运行的结果摘要一致（默认运行、筛选模式各重复一次）。

**Open Targets（26.06 → 26.09，改用分组抓取）**：

| 疾病基因集（Reactome 背景） | 26.06（旧抓取器） | 26.09（分组抓取） |
| --- | --- | --- |
| 关联靶点 | 9,907 | 10,206（每个恰好一次） |
| 遗传关联 ≥ 0.5：背景内 / 重叠 / 倍数 / p | 486 / 9 / 0.95 / 0.61 | 478 / 9 / 0.97 / 0.59 |
| 遗传关联不设阈值 | 2,001 / 45 / 1.15 / 0.17 | 2,215 / 41 / 0.95 / 0.67 |
| 遗传关联 ≥ 0.8 | 49 / 2 / 2.09 / 0.25 | 82 / 2 / 1.25 / 0.48 |
| 总分 ≥ 0.5 | 126 / 8 / 3.26 / 0.003 | 156 / 8 / 2.63 / 0.011 |
| 文献共现 ≥ 0.5 | 557 / 58 / 5.34 / 7.7e-27 | 556 / 57 / 5.26 / 5.2e-26 |
| 实测背景：疾病基因 / 命中 / 倍数 | 16 / 9 / 0.93 | 14 / 9 / 1.06（p = 0.50） |

结论不变：用遗传证据定义疾病基因时没有富集，用文献定义时有极强的"富集"（循环论证）。

**PubChem 筛选模式（修正映射）**：

| | 旧映射 | 修正后 |
| --- | --- | --- |
| 保留的明确判定（有活性 / 无活性） | 143,702（10,710 / 132,992） | 143,679（10,696 / 132,983），1,029 个蛋白 |
| 参与检验的蛋白（阈值 10 / 20 / 50） | 130 / 116 / 98 | 524 / 472 / 417 |
| 检验的通路（同上） | 68 / 58 / 49 | 516 / 468 / 406 |
| 阈值 20 的检测数、活性比例 | 12,579，8.6% | 51,304，6.5% |
| 通过的通路（同上） | 1 / 2 / 2 | 8 / 9 / 8 |
| 被充分测过的 2 型糖尿病基因 | 6–8，倍数 1.2–1.4 | 31–39，倍数 0.85–0.97（p = 0.50–0.69） |

- **CYP 信号保留且更完整**：CYP1A2 136/205、CYP2C9 78/206 与原来相同；新增进入检验的 CYP2C19
  91/204、CYP2D6 75/204、**CYP3A4 70/224**（旧映射下被映到次要号）。外源物代谢、EET/DHET、
  16-20-HETE、类 maresin SPM、阿司匹林 ADME 在三个阈值下都通过，CYP2E1 反应在阈值 20 与 50 下通过。"中药—药物相互作用
  信号，不是作用机制证据"的解读不变，而且现在覆盖了最主要的药物代谢酶 CYP3A4。
- **新信号：核受体转录通路**（阈值 20、50）：22 个核受体、3,355 次检测中 568 次有活性（16.9%，
  2.6 倍），来自 Tox21 / Attagene / Odyssey Thera 等统一面板。ESR1 以激动模式为主（黄酮与异黄酮，
  与植物雌激素一致）；AR、PPARG、THRB 等以拮抗模式报告基因检测为主，易受细胞毒性和荧光素酶抑制
  干扰。规范按"值得核对的线索，不是机制"记录。
- **碳酸酐酶**现在在阈值 10 与 20 下通过（旧映射下 CA2 被映到 TrEMBL 号，只在阈值 10 出现），
  但 121 次检测中 116 次有活性、全部来自按阳性挑选的文献检测，阈值 50 下消失，解读不变。

### 7.7 验证

```bash
cd BioScience-Harness
PYTHONPATH=src:../PSH-Harness/src python -m pytest tests -q        # 1129 passed, 6 skipped（单元层 1127）
cd ../PSH-Harness && PYTHONPATH=src python -m pytest -q && cd -   # 1086 passed
PYTHONPATH=src:../PSH-Harness/src python scripts/check_lockfile.py
# 真实数据（原始文件放在 RAW；Open Targets 与 PubChem 为唯一联网的两步）
python scripts/build_source_snapshots.py gold --network --raw RAW --out SNAP --ledger SNAP/audit/snapshots.jsonl
python scripts/fetch_opentargets.py MONDO_0005148 --raw RAW
python scripts/build_source_snapshots.py opentargets --file RAW/opentargets_MONDO_0005148.json --out SNAP --ledger SNAP/audit/snapshots.jsonl
python scripts/fetch_pubchem.py --composition SNAP/composition.json --raw RAW
python scripts/build_source_snapshots.py pubchem --file RAW/pubchem_bioassay.json.gz --raw RAW --out SNAP --ledger SNAP/audit/snapshots.jsonl
python scripts/run_network_pharmacology.py --snapshots SNAP --ledger SNAP/audit/snapshots.jsonl --out RUN
python scripts/run_network_pharmacology.py --snapshots SNAP --ledger SNAP/audit/snapshots.jsonl --out RUN_S \
    --hits screening --permutations 10000                     # --screening-min-compounds 10 / 50
python scripts/run_network_pharmacology.py ... --background reactome --disease-evidence literature   # 敏感性分析
```

RAW 需要：NPASS 2.0 ×6、CMAUP 2.0 ×5、LOTUS 冻结版、STRING v12 的 links / aliases / info、
`UniProt2Reactome.txt`，以及新增的 `uniprot_human_reviewed_accessions.txt`（下载地址均在
`acquisition/sources.py`）。
