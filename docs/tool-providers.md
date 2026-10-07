# Tool providers: ToolUniverse and BioMCP as reviewed components, Open Targets datatypes, and source identity

Two projects already do tool registration at scale. ToolUniverse ships 2,826 tool
configurations; BioMCP serves 36 MCP tools in front of PubMed, ClinicalTrials.gov and
MyVariant.info. TCMScience does not compete with either. It admits a small reviewed subset
of each as governed components, with fixed ids, schemas, hosts and licences, and refuses the
rest. Two pieces of evidence plumbing come with this: Open Targets associations keep their
evidence datatype from the API to the analysis, and records that name the same paper, trial
or variant are counted once, whichever provider returned them.

| part | where | state |
|---|---|---|
| ToolUniverse provider | `bioagent.providers.tooluniverse`, `registry/tooluniverse_allowlist.yaml` | 8 tools, each run live through the runtime on 2026-10-07 |
| BioMCP server config and result adapter | `bioagent.providers.biomcp`, `registry/biomcp_server.yaml` | draft config, 5 tools; no transport (built separately) |
| Open Targets datatypes | `providers.public_apis`, `analysis.network_pharmacology` | the live connector and the analysis carry them |
| Source identity | `bioagent.sources.identity` | used by the P0 retrieve skill, `tcmdb.consensus`, network pharmacology, the BioMCP adapter |

## ToolUniverse: a reviewed handful, not a registry

`ToolUniverseProvider` reads the installed package's JSON tool configuration **without
importing the package** (the import alone takes about 4 s and registers every tool class),
and yields one `python` component per tool in `registry/tooluniverse_allowlist.yaml`. No
other ToolUniverse tool exists for the runtime: discovery yields only allowlisted names, and
the module attribute an entrypoint resolves to (`run_<name>`) exists only for them.

| component id | upstream tool | hosts | why TCMScience admits it |
|---|---|---|---|
| `tooluniverse.UniProt_get_entry_by_accession` | UniProtRESTTool | rest.uniprot.org | the protein a target accession names |
| `tooluniverse.PubChem_get_CID_by_compound_name` | PubChemRESTTool | pubchem.ncbi.nlm.nih.gov | constituent name to candidate CIDs |
| `tooluniverse.PubChem_get_compound_properties_by_CID` | PubChemRESTTool | pubchem.ncbi.nlm.nih.gov | the structure that confirms the identity |
| `tooluniverse.ChEMBL_search_activities` | ChEMBLRESTTool | www.ebi.ac.uk | measured activities behind targets |
| `tooluniverse.Reactome_map_uniprot_to_pathways` | ReactomeRESTTool | reactome.org | the annotation enrichment tests against |
| `tooluniverse.EuropePMC_search_articles` | EuropePMCTool | www.ebi.ac.uk, and three NCBI hosts when asked for full text | literature with PMIDs, PMCIDs and DOIs |
| `tooluniverse.ClinicalTrials_search_studies` | ClinicalTrialsTool | clinicaltrials.gov | trials by condition and intervention |
| `tooluniverse.STRING_get_interaction_partners` | BaseRESTTool | string-db.org | partners for the topology step |

**How a tool maps.** The configuration's `parameter` schema becomes the component's
`inputs`, with its `test_examples` as JSON Schema `examples`, one of which the review names
as the smoke test (`validation.smoke_test`, `smoke_arguments()`). `return_schema` becomes
`outputs`; the reviewed hosts become `permissions.network`, which the policy kernel gates.
The code licence is Apache-2.0 with `integration_mode: federated`: the package runs in
place and is never copied. The **data** licence is what the configuration states, and no
reviewed configuration states one, so `license.data` is `unknown`. A pointer to the source's
own terms is kept in `license.note`, as a pointer, not a licence. `provider.commit` is the
SHA-256 of the reviewed configuration entry and `version` the package release, so the event
log names exactly what ran.

**What quarantines a tool.** The allowlist pins the release it was reviewed against
(1.5.6), each tool's configuration entry (SHA-256 of canonical JSON), its implementation
class and every host the class can contact. Discovery yields a tool in `QUARANTINED`, with
the reason, when the installed release differs, the entry's digest differs, the type names
another class, the configuration points at a host the review did not declare, or the tool
now needs an API key. A quarantined component resolves, and is invoked, as UNAVAILABLE; it
never runs. Hosts that live only in the implementation (ChEMBL's base URL, Europe PMC's
full-text fallbacks) were read from the class source at review. Hosts are gated as
declared: the kernel does not intercept sockets (`PolicyKernel.enforcement_report`).

**Execution.** Each tool's entrypoint builds, on first call, an engine holding that tool
alone: `ToolUniverse(load_workspace=False)` and `load_tools(include_tools=[name])`. Before it
runs anything the loaded configuration must have the reviewed digest, come from the
package's own file, and resolve to the reviewed class; otherwise the call is DENIED. Four
defaults of 1.5.6 would make the record wrong, and each was measured before it was handled:

| default | what it would do | handled by |
|---|---|---|
| result cache | constructing an engine creates `~/.tooluniverse/cache.sqlite` and can answer from it | `TOOLUNIVERSE_CACHE_ENABLED/PERSIST=false` while the engine is built |
| logging to stdout | INFO lines on standard output, the PSH isolated executor's result channel | WARNING level, handlers moved to stderr |
| workspace scan | with `load_workspace=False`, `load_tools` still imports `./.tooluniverse/*.py` and lets its JSON replace a built-in tool (a test plants both and watches it happen) | the engine's workspace scan returns nothing |
| failures as values | `run_one_function` returns `{"status": "error", ...}` rather than raising | `classify()`; a failed call raises `ToolUniverseCallError` with its status |

`classify()` keeps ToolUniverse's own rule for what is an error and maps it the way
`HTTPBackend` maps a failed request:

| ToolUniverse result | status |
|---|---|
| typed `ToolUnavailableError`, `ToolAuthError`, `ToolDependencyError`, `ToolConfigError` | UNAVAILABLE |
| any other typed error (`ToolValidationError`, `ToolServerError`, `ToolRateLimitError`) | FAILED |
| untyped, text says it timed out | TIMEOUT |
| untyped, the host could not be reached (proxy, refused, name resolution) | UNAVAILABLE |
| anything else that reports an error, or `None` | FAILED |

`PythonBackend` used to record every exception as FAILED, which `ExecutionStatus.executed`
counts as work done. It now records the status an exception declares as
`execution_status`, when that is FAILED, UNAVAILABLE, TIMEOUT or DENIED; success cannot be
declared by raising. The PSH bridge already turns TIMEOUT into `ToolTimeout` (in process,
`BridgedComponent.value_of`; isolated, exit code 124), so a ToolUniverse timeout reaches the
loop as a call that may have done its work, not as a failure.

**Re-reviewing.** `python -m bioagent.providers.tooluniverse` prints what the installed
package holds for each allowlisted tool (file and entry digests, type, configured hosts, API
keys) and why it would be quarantined. Read the changed entries, then update the
allowlist; nothing updates it automatically.

**Not wired by default.** `psh.assembly.default_runtime` does not include these components;
pass `extra_manifests=ToolUniverseProvider().discover()`. Adding them to every default
registry changes what its term search ranks first for "UniProt" or "ChEMBL", which planners
and tests rely on; that is the integrator's decision.

## BioMCP: a draft server configuration and a result adapter

biomcp-python 0.7.3 (MIT) was started over stdio with the MCP SDK 1.30.0
(`stdio_client`, `ClientSession`) on 2026-10-07. It listed 36 tools. Five are admitted:
`article_searcher`, `article_getter`, `trial_searcher`, `trial_protocol_getter` and
`variant_searcher`. Each was called once, and the trimmed replies are the test fixtures
(`tests/fixtures/biomcp/README.md` gives the query, time and cuts).

`registry/biomcp_server.yaml` is the reviewed configuration, as data: the command
(`{python} -I -m biomcp run`), the working directory, the environment to pass and to set,
the destination (`PUBLIC_REMOTE`), and per tool the SHA-256 of its listed input schema, the
hosts it contacts, the arguments fixed for it and the arguments it ignores. It also records
what was not admitted and why: `trial_getter` and `trial_locations_getter` return site
staff's names, telephone numbers and e-mail addresses; `search` and `fetch` reach every
domain behind one schema. `bioagent.providers.biomcp` reads it and does not start a server:

* `check_listing(tools)` holds a `tools/list` answer against it. A tool it does not name is
  not admitted; an admitted tool whose schema digest changed, or that is listed twice, is
  refused; a configured tool that is absent is reported missing.
* `arguments_for(tool, arguments)` returns the arguments to send: the fixed ones applied
  (`include_cbioportal: false` and `include_oncokb: false`, because they splice summaries
  from other hosts into the same text), and a `ValueError` for an argument the tool ignores.
* `adapt(tool, reply, arguments=..., retrieved_at=...)` turns a reply into
  `SourceRecord`s: canonical identifiers, title, the record's text exactly as returned, the
  query, the retrieval time and `biomcp-python 0.7.3`. `SourceRecord.evidence(design=...)`
  makes a candidate `EvidenceItem` with a quote receipt, once the caller states a design; a
  search hit does not say whether it is a trial or a review. `AdaptedReply.source_card()`
  is a `SourceCard` pinned by the reply's SHA-256 at the retrieval time.

What 0.7.3 does that the configuration and adapter account for, each seen in a real reply
or read in its source:

* **Arguments accepted and dropped.** `article_searcher` ignores `page` and `page_size`
  (asked for 3, it returned 10); `trial_searcher` ignores `page`, `location`, `sex`,
  `healthy_volunteers` and `funder_type`; `variant_searcher` ignores `hgvs` and
  `consequence`. A query recorded with them would claim filters that were never applied, so
  they are refused.
* **Errors as successes.** `article_getter("PMC8696197")` answered
  `[{"error": "Invalid identifier format ..."}]` with `isError` false, although its
  description says PMC ids are accepted; trial and variant errors are rendered as `Error:`
  lines. The adapter returns these as FAILED (TIMEOUT or UNAVAILABLE by their text), with no
  records. An empty search is SUCCEEDED with no records; a getter that names no record is
  FAILED.
* **The server's version is not the server's.** `serverInfo.version` in the initialize
  answer is 1.30.0, the MCP SDK's. The configured package version is what is recorded.
* **An assembly the record does not state.** MyVariant.info answers in hg19 unless asked,
  and 0.7.3 does not ask; variant ids such as `chr10:g.114758349C>T` are recorded as
  `hgvs:GRCh37:...` (from the record's UCSC link, else the configured assembly).
* **A disk cache shared between runs.** BioMCP keeps HTTP answers under
  `$XDG_CACHE_HOME/biomcp/http_cache`; the configuration gives each run its own.
* **An environment the SDK empties.** A stdio server is started with HOME, LOGNAME, PATH,
  SHELL, TERM and USER only; behind an egress proxy nothing is reachable unless the proxy
  and CA variables are passed (`env.pass`).

**What the integrator must wire** (the transport and dispatcher are another workstream):

1. Start the server from `server.command`, with `{python}` the interpreter of the
   environment that has `biomcp-python==0.7.3` (the `biomcp` extra), `cwd` an empty
   run-owned directory, the variables in `env.pass` copied from the caller and those in
   `env.set` with `{run_dir}` filled in.
2. After `initialize`, call `tools/list` and `check_listing`; refuse to serve the server when
   `ok` is false, and admit only `admitted`, e.g. through `psh.protocols.mcp.MCPToolAdapter`
   with `destination=PUBLIC_REMOTE`. That adapter takes one host list per server: give it
   the union of the admitted tools' `hosts`, or one adapter per tool to keep them apart.
3. For every call, send `arguments_for(tool, arguments)` and record the UTC time.
4. Pass the reply, the arguments sent and the time to `adapt`; use its `status` as the
   call's `ExecutionStatus` (the MCP `isError` flag alone misses the errors above) and its
   records, not the raw text, as candidate evidence.
5. Run `tests/test_biomcp_adapter.py -m integration` once the transport exists; it checks the
   listed schemas against the configuration and adapts a live reply.

## Open Targets: the datatype travels with the association

An Open Targets association has an overall score and one score per evidence datatype. A
literature co-mention (Europe PMC text mining) and a genetic association have the same
predicate and the same 0-1 scale; only the datatype tells them apart.

**What already held.** Since the snapshot path was added (commit 83e1303),
`fetch_disease_associations` saves `datatypeScores` for every row,
`sources/parsers/opentargets.py` writes one `associated_with` edge per datatype with its
`score_name` and a Biolink knowledge level (`literature` is `text_co_occurrence`), and the
snapshot keeps those edges. `tests/test_opentargets_datatypes.py` now checks the whole chain,
fetch to snapshot, against the fake API of `test_opentargets.py`. The parser is unchanged,
so every existing snapshot and its id stay valid, as do the release, version and duplicate
checks in `fetch_disease_associations`.

**The current schema.** On 2026-10-07 the Platform reported API 26.9.0 and data 26.09.
Over 3,200 association rows (the first 3,000 for type 2 diabetes, the first 200 diseases for
EGFR) the datatypes were `genetic_association`, `literature`, `clinical`,
`somatic_mutation`, `animal_model`, `affected_pathway`, `rna_expression` and
`genetic_literature`. There was no `known_drug`: drug evidence is `clinical` (datasource
`clinical_precedence`). The parser's vocabulary has both names, so older snapshots parse.

**What changed.**

* The live connector operation `opentargets.associated_diseases` returned only the overall
  score. It now returns `datatypeScores{id score}` for each row; it was re-verified live on
  2026-10-07 and its row in `data/connector_live_verification.csv` updated.
* A protocol declares the datatypes that define its disease gene set.
  `Parameters.disease_evidence` takes one datatype, as before, or a tuple whose union
  defines the set (`Parameters.evidence_datatypes` is always the tuple):
  `Parameters(disease_evidence=("genetic_association", "somatic_mutation", "clinical"))`.
  One name stays a plain string, so the digest of every existing protocol is unchanged.
  `overall` cannot be combined with others, since it already aggregates them.
  `scripts/run_network_pharmacology.py --disease-evidence a,b` takes a comma-separated list.
* The result's `disease` section reports what the choice left out: `datatypes` (for each
  datatype, the targets at the cut-off, their overlap with the measured targets, and
  whether the protocol used it), `literature_only` (targets that reach the cut-off through
  text mining and no other datatype, and how many of them are in the gene set) and
  `declared_absent` (declared datatypes the snapshot does not have). The limitations say
  how many literature-only targets were excluded or included, and name a declared datatype
  that contributed nothing, so a protocol written for `known_drug` does not silently get an
  empty gene set from a 26.x snapshot.

## Source identity: one paper counts once

`bioagent.sources.identity` does two things, both pure (no network):

`canonical(identifier, scheme=None, *, assembly=None)` gives an identifier one spelling,
or `None`:

| scheme | rule | example |
|---|---|---|
| pmid | digits, leading zeros dropped; `PMID:`, `MED:` and PubMed URLs read | `PMID: 012345` → `pmid:12345` |
| pmcid | `PMC` and digits, version suffix dropped | `pmc8696197.2` → `pmcid:PMC8696197` |
| doi | resolver prefixes removed, percent-decoded, lower case, trailing `.,;` removed | `https://doi.org/10.1016/J.JEP…` → `doi:10.1016/j.jep…` |
| nct | `NCT` and eight digits | `nct01234567` → `nct:NCT01234567` |
| rsid | `rs` and digits | `RS7903146` → `rsid:rs7903146` |
| hgvs | accession kept with its version, gene annotation dropped, one-letter protein substitutions made three-letter; a position by chromosome carries its assembly | `NP_004324.2:p.V600E` → `hgvs:NP_004324.2:p.Val600Glu`; `chr10:g.114758349C>T` on hg19 → `hgvs:GRCh37:chr10:g.114758349C>T` |

A bare number is not read as a PMID unless the caller says so (`2244` is as likely a
PubChem CID). `group_sources(records)` treats the identifiers a record carries as names of
one source and merges records that share a name (union-find), so a BioMCP record naming a
PMID, a PMCID and a DOI joins a PubMed row that has only the PMID and an Open Targets row
that has only the DOI. Merging follows names transitively, erring towards fewer sources, as
`tcmdb.consensus.independent_count` does; an rsID names a dbSNP locus record, so two alleles
under one rsID are one source unless `link_on` leaves `rsid` out. A record with no
recognisable identifier is counted on its own and listed as unidentified. It does not map
a PMID to a DOI that no record states: that needs a lookup service, and two records that
name one paper only by different schemes stay two sources.

**Where counts use it.**

* The P0 retrieve skill (`skills/p0/retrieve.py`) already counted a study cited by two
  relations once; a study recorded twice under two ids with one PMID, DOI or registration is
  now one study too, and the limitation says which records were merged. The seed corpus has
  no such pair, so its output does not change.
* `tcmdb.consensus.lineage_of` canonicalises a row's reference before using it as a lineage.
  DDID and dbPTH store DOIs without the `doi:` prefix; those rows counted as the database
  rather than the paper, and one paper cited by two databases in two spellings was two
  independent lineages.
* Network pharmacology's target rows gain `sources` beside `measurements`: NPASS, CMAUP,
  BindingDB and ChEMBL curate the same papers, so one IC50 can arrive as four edges.
* The BioMCP adapter's records carry canonical identifiers; `group_records` groups them.

**Documented, not changed.** The research loop builds one evidence item per cited snapshot
edge, each with its own receipt, and counts nothing; its result already carries the
network-pharmacology `sources` per target. Where a future step counts the support of a
claim, it should count `group_sources` over the cited edges' `publications`, not edges.

## Verification

Run from `BioScience-Harness` with `PYTHONPATH=src:../PSH-Harness/src`:

```
python -m pytest -q tests/test_source_identity.py tests/test_opentargets_datatypes.py \
    tests/test_tooluniverse_provider.py tests/test_biomcp_adapter.py      # unit
python -m pytest -q -m integration tests/test_tooluniverse_provider.py \
    tests/test_biomcp_adapter.py                                          # network
python scripts/verify_connectors.py --only opentargets --no-write         # live connector
```

The unit tier runs the first command's tests that need neither package; the four
ToolUniverse tests that build an engine skip without `tooluniverse` (and fail under
`BIOAGENT_REQUIRE_TOOLS=1`). A job that installs `BioScience-Harness[dev,tooluniverse,biomcp]`
runs them all. On 2026-10-07 the live UniProt P04637 call and the BioMCP listing check both
passed, and all eight ToolUniverse smoke examples succeeded through the runtime.

## 中文摘要

**目的：** ToolUniverse 与 BioMCP 已经提供大规模的工具注册，TCMScience 不与之竞争，而是把其中经过审查的少数工具纳入治理：固定 id、固定 schema、声明主机与许可，其余一律不暴露。同时补上两条证据管线：Open Targets 关联在从 API 到分析的全过程中保留证据类型；同一篇论文、同一项试验、同一个变异无论经由哪个来源到达，只计一次。

**ToolUniverse：**
- **审查清单：** `registry/tooluniverse_allowlist.yaml` 列出 8 个工具（UniProt、PubChem、ChEMBL、Reactome、Europe PMC、ClinicalTrials.gov、STRING），钉住 1.5.6 版本、每个工具配置条目的 SHA-256、实现类与可访问主机；2026-10-07 全部经运行时实测成功。
- **映射规则：** `parameter` 成为输入，`test_examples` 成为冒烟测试，`return_schema` 成为输出，主机成为网络权限；数据许可以配置声明为准，均未声明，故记为 `unknown`。
- **隔离：** 版本、摘要、实现类、未声明主机或新增 API 密钥任何一项不符，工具即被隔离（QUARANTINED），不会运行。
- **执行：** 每个工具独立引擎；关闭结果缓存、日志改到标准错误、禁止扫描工作目录（实测 1.5.6 在 `load_workspace=False` 时仍会导入 `./.tooluniverse` 下的代码）。以值返回的错误映射为 TIMEOUT、UNAVAILABLE 或 FAILED；`PythonBackend` 现在记录入口点声明的失败状态。

**BioMCP：**
- **配置草案：** `registry/biomcp_server.yaml` 记录启动命令、环境变量、5 个准入工具及其输入 schema 摘要、固定参数与被忽略参数，以及不准入的原因（如 `trial_getter` 含联系人电话和邮箱）。
- **结果适配：** `adapt()` 把回复转为带规范标识、查询、检索时间、服务器版本与原文的来源记录。
- **0.7.3 的实测问题：** 部分参数被接受却被忽略；错误作为成功返回；`serverInfo.version` 实为 MCP SDK 版本；变异坐标为 GRCh37 却未声明。
- **集成：** 传输层由另一组实现，本文列出其需接入的五个步骤。

**Open Targets：**
- **已有能力：** 快照路径早已逐类型保存分数。
- **现行 API：** 2026-10-07 实测为 26.9，药物证据称 `clinical`，已无 `known_drug`。
- **本次改动：** 在线连接器返回各类型分数；协议可声明多个证据类型；结果单独报告"仅文献共现"的靶点，以及声明了却不存在的类型。

**来源同一性：**
- **规范化：** `sources.identity` 统一 PMID、PMCID、DOI、NCT、rsID 与 HGVS 的写法（基因组坐标带参考基因组版本），并按共有标识合并记录。
- **接入点：** 已接入 P0 检索技能、`tcmdb.consensus` 的谱系计数、网络药理靶点的 `sources` 计数，以及 BioMCP 适配器。
