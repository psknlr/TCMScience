# TCM data sources: what each database offers and how the harness reaches it

The architecture document `tcm_agent_architecture.docx` lists 66 databases in 14 modules.
A review of 2026-09-30 proposed 65 more sources (entries 67–133 in the catalogue): sample
chemistry, directed mechanisms, perturbation, genetics, safety, immunology and imaging.
On 2026-10-01 every one of them was checked from the harness. The check covered:
- the home page and whether it is reachable;
- any API, tested with one documented request;
- download links, tested with a HEAD or ranged request to confirm they return files;
- logins, CAPTCHAs, fees and email-only job forms;
- the licence or terms of use;
- `robots.txt`.

The integration guide ([`tcm-data-integration-guide.md`](tcm-data-integration-guide.md)) added three more, entries
134–136: the HKBU formula database, the Hong Kong Chinese Materia Medica Standards and the
Hong Kong reference DNA sequences. All three are **manual imports** — a person obtains the
files under whatever terms apply and reviews them before import; nothing is downloaded or
crawled, because none has a confirmed open bulk API.

Each database was then wrapped in the way its site allows, and only in that way. All of
it is reachable through one interface, `bioagent.tcmdb.TCMDataHub`, and its command line
`bioagent.cli tcmdb`.

## The result in numbers

| Access | Architecture (1–66) | Added (67–133) | What the hub does |
| --- | ---: | ---: | --- |
| live API | 3 | 16 | Calls a connector through the governed runtime |
| live API + snapshot | 10 | 27 | Both live calls and a local copy |
| snapshot | 12 | 19 | Downloads the files once, loads them into SQLite and extracts relations |
| manual import | 7 | 0 | A person exports the files; the same loader reads them |
| restricted | 16 | 5 | Nothing to call. The card says what a person would need (an account, a fee, a request to the authors) |
| unreachable | 18 | 0 | Nothing to call. The card records what was observed |

Of the 66 architecture sources, live connectors and downloads reach 25; of the 67 sources added from the review of 2026-09-30 (below), 62. 7 more are reachable
through manual import. NPASS and CMAUP are counted under snapshot; they were already
handled by the research snapshot pipeline (`bioagent.sources`).

On the files downloaded on 2026-10-01, the 15 local stores hold **8.37 million
relations**:

| Relation | Rows | From |
| --- | ---: | --- |
| ingredient → target | 3,164,895 | BATMAN-TCM 2.0 (17,069 known and 2,319,272 predicted with the model score), TM-MC 2.0 (via STITCH/PubChem), ITCM, dbPTH (human, reported), TCMIO (predicted), BATMAN-TCM 1.0 (known) |
| drug → adverse event | 2,977,338 | OFFSIDES (FAERS disproportionality signals with PRR) |
| target → disease | 1,126,763 | TM-MC 2.0 (via DisGeNET), ITCM |
| formula → herb | 593,140 | BATMAN-TCM 2.0, ITCM, HERB 2.0, TM-MC 2.0, TCMIO |
| herb → ingredient | 413,726 | BATMAN-TCM 2.0, TM-MC 2.0 (curated, with the PubMed id), ITCM, TCMIO |
| drug → target | 45,392 | TTD, with clinical status and mechanism |
| herb ↔ drug interaction | 16,168 | DDID, with the outcome and the paper |
| gene-set membership | 8,640 | ImmPort immune gene lists |
| herb/formula → trial, review, paper | 23,295 | HERB 2.0: 8,558 trials (NCT ids), 8,032 meta-analyses, 6,705 papers |

Live connectors add, per query:
- **DCABM-TCM**: constituents detected in blood for prescriptions, herbs and ingredients.
- **TCMBank and ITCM**: herb, ingredient, target and disease look-ups.
- **TTD**: target details, drugs and pathways.
- **SymMap and HERB 2.0** (`tcmdb enrich <herb>`): a herb's ingredients, targets,
  symptoms, diseases and syndromes, an ingredient's targets, and HERB's paper-graded
  targets, cached and built into the stores `symmap_api` and `herb_api`.
- **IEDB**: epitopes, antigens and T-cell assays.
- **openFDA**: reactions reported with a herbal product, and the drugs co-reported with it.
- **Hugging Face and figshare**: the metadata of the training sets.

## What the review of 2026-09-30 asked for, and what was found

The review argued that more herb→ingredient→target databases add little. What is missing
is evidence of other kinds: what a sample actually contains, the direction of an effect,
what a perturbation does, tissue-specific genetics, and safety screens with their negative
results. On the files downloaded on 2026-10-01, the 67 local stores hold **18.4 million
relation rows**. 3.5 million of them are tested negatives, about 12,000 are flagged
inconclusive, and 24,091 unidentifiable rows wait in the unresolved queue.

| Capability | Sources wrapped | Rows (local) |
| --- | --- | ---: |
| Measured chemistry of samples | MassBank (per-record licence), GNPS (12 natural-product libraries), NP-MRD (experimental NMR only), Metabolomics Workbench (live) | 154,341 compound→spectrum |
| Natural-product occurrence and names | COCONUT (CC0), IMPPAT 3.0 (CC BY-NC-ND), ChEBI, World Flora Online (names only: a species is not a materia medica), KNApSAcK (live, per record) | 1.55 M organism→compound |
| Direction of effect | SIGNOR (activation/inhibition/increase/decrease, residue, cell, tissue), DrugCentral (action type; 19,929 of 25,933 rows state none, and none is guessed), GPCRdb, KLIFS | 42,443 regulation; 113,101 drug→target |
| Interactions and screens | BioGRID (MIT; multi-validated physical set), BioGRID ORCS (hits and non-hits), IntAct/Complex Portal (incl. negative interactions), DisProt | 563,039 interactions; 42,016 screen rows |
| Perturbation and cell context | Cellosaurus, LINCS L1000 (GEO metadata only), DepMap (CC BY releases up to 24Q4), scPerturb, ARCHS4 (pointers) | matrices stay files |
| Genetics | eQTL Catalogue (files; REST API retired), IMPC (mouse knockouts), MaveDB (live) | 64,754 variant→gene (associated); 73,500 gene→phenotype |
| Safety | ToxCast invitrodb (CC0; inactive calls kept as negatives), AOP-Wiki (licence per AOP), Orphadata, TCMToxDB, EMA herbal monograph index | 3.34 M compound→assay |
| Drugs and pharmacogenomics | DrugCentral, DrugComb (synergy matrix kept as a file), GDSC, Pharos, PharmacoDB (live), CPIC, ClinPGx, PharmVar | 20,536 drug→pharmacogene |
| Regulation and RNA | ChIP-Atlas (live, 30 s crawl delay), ReMap (non-commercial), miRTarBase, RNAcentral, iPTMnet (non-commercial), JASPAR | 44,195 TF→gene; 19,026 miRNA→gene |
| Immunology, microbes, atlases, imaging | IEDB (with its 63% negative T-cell assays), iReceptor/VDJServer, BV-BRC, MGnify, HCA, HuBMAP, 4DN, BioSamples, IDC, TCIA, IDR, OpenNeuro, BioImage Archive | metadata live; 1.0 M epitope rows |

Several claims in the review did not survive the live check, and the catalogue follows
what the sites say, not the review:

- **GtoPdb**: since September 2026 its policy requires registration and an API key, with
  fees for commercial organisations. Its CSV files still answer anonymously, but they are
  not fetched: a file that answers is not permission. It is catalogued as restricted.
- **DepMap**: only the CC BY figshare releases up to 24Q4 are fetched. The current releases
  sit behind a Cloudflare challenge and click-through non-commercial terms.
- **eQTL Catalogue**: the REST API answers HTTP 410, so the data come from the documented
  files. Variants are keyed by build, chromosome, position and alleles, never by rsID alone.
- **TCIA**: its own API guide calls the NBIA API legacy, so the Imaging Data Commons is the
  primary connector, with the overlap recorded as lineage.
- **IntAct**: the file named `species/Human.zip` holds human–virus interactions, not human
  ones, so it is not used.
- **BioGRID**: the REST API needs a registered key, so the MIT bulk files are used.
- **iPTMnet**: the API answered 503 throughout, so it is pending. Its licence pages
  contradict each other (CC BY-NC-SA, CC BY, CC BY-NC-ND), and the most restrictive is
  applied.
- **AOP-Wiki, MassBank, MaveDB, IDC, OpenNeuro, BioImage Archive**: the licence is set per
  record or per collection, so it is kept per row, not once per source.
- **OpenGWAS, FinnGen, HTAN, MIMIC-IV**: these need a token, a form, dbGaP or credentials,
  and are catalogued as restricted. MIMIC's licence also forbids passing its data to
  online model services.
- **World Flora Online's** matching API serves an incomplete TLS chain. It is pending,
  since TLS verification is never disabled; the CC0 Plant List files are used.

Where a robots.txt disallows a path that the provider's own documentation offers to
programs (Metabolomics Workbench, Rhea, RNAcentral, ChIP-Atlas, the HCA service, EBI's
file server), only documented endpoints and files are used. They are fetched per entity,
never enumerated, at the crawl delay the file sets (`backends.http.DEFAULT_RATES`,
`DatasetSpec.min_interval_s`), and each catalogue entry says so.

One exception is deliberate. www.ebi.ac.uk sets a crawl delay of 10 s but does not disallow
the API paths of its documented services (ChEMBL, Europe PMC, OLS, InterPro, MGnify,
IMPC, BioSamples, ...), and each service publishes its own rate guidance. Throttling the
shared host to one request per 10 s would stall about fifteen connectors that were
verified at the host's existing rate, so that rate is kept for API calls. Bulk files from
EBI hosts are paced by the downloader.

<!-- zh -->
**补充数据源（第 67–133 条）**：按审阅意见新增 65 个来源，逐一实测（API、下载、许可、robots、登录/密钥），并经独立复核后才接入。
- 本地 67 个库共 1840 万条关系，其中 350 万条为“测试阴性”，另有约 1.2 万条不确定结果、2.4 万条无法识别的记录进入待消歧队列。
- 新增能力：样品实测化学（MassBank、GNPS、NP-MRD）、作用方向（SIGNOR、DrugCentral）、互作与筛选（BioGRID/ORCS、IntAct）、遗传背景（eQTL Catalogue、IMPC）、安全性筛选（ToxCast，保留阴性结果；AOP-Wiki）、免疫与影像元数据等。
- 与审阅意见不符之处以网站实际情况为准：GtoPdb 现需注册与密钥，即便 CSV 仍可匿名下载也不抓取；DepMap 新版本有人机验证，只取 24Q4 及以前的 CC BY 版本；eQTL Catalogue 的 REST API 已停用（410）；IntAct 的 `Human.zip` 实为人–病毒互作；iPTMnet 许可前后矛盾，按最严格的非商用处理。
- 许可逐文件、逐记录记录；`relations(commercial=True)` 只返回允许商用的行。

**接入指南新增（第 134–136 条）**：按 [`tcm-data-integration-guide.md`](tcm-data-integration-guide.md) 第一期接入香港浸会大学方剂库、香港中药材标准（HKCMMS）与香港参考 DNA 序列库，三者均为**人工导入**（`access=manual_import`），仅以合成数据验证，未下载任何真实数据，也未假定存在开放批量 API。
- 134 HKBU 方剂库：一行表示“某版本方剂中的一味药”，产出 `formula_herb` 关系，证据为 `listed`（记录该方含此药，不等于疗效）；缺稳定 ID、缺出处或未人工审核的行进入待消歧队列；同名方异版本、生品与炮制品分别用不同 ID。
- 135 港标 HKCMMS：一份专论一项检测一行（鉴别、检查、含量限度、化学指标），保留版次、方法、数值、单位与页码；仅可查询——限度或鉴别方法不是药材—成分关系，不强行转换。
- 136 参考 DNA 序列库：序列以原始 FASTA 保留，另表存标本元数据（基原物种、标记、登录号、凭证标本）；仅可查询——条形码标识基原物种，不是药物成分，单独也不构成物种鉴定。
- 三者许可均为 `unknown`：适配器能跑不等于获得数据许可，`tcmdb check` 把 `unknown` 类作为警告而非许可（只供查询的港标、DNA 也会提示）。
- 新增只读工具 `hkbu_formula_lookup`、`hkcmms_standard_lookup`、`hk_cmm_dna_lookup`，返回 `not_loaded`（未载入）、`incomplete_dataset`（缺表、缺列或缺文件）、`no_matching_record`（无匹配）、`withheld`（有匹配但未经人工核对，或许可不允许本次用途）或 `ok`，不表述为“无效”。每条记录带数据集、文件许可、许可类别和商业使用说明；PSH 中工具的 `data_license` 是数据许可，MIT 只作为 `code_license`。
- 2026-10-05 修订（独立复核后）：三个模板文件严格读取（列名、字段数、引号、UTF-8 不符即拒绝并指出行号）；构建在临时库完成后才替换正式库，失败不影响原库；炮制信息并入药材名（黄芪 + 炙 = 炙黄芪），经 `consensus` 后生品与炮制品不再合并，并补上「炮」「煨」两个炮制前缀；`check` 核对 FASTA 与标本元数据是否一致；隔离（PSH）调用与直接调用读取同一个库，库在许可的根目录之外时明确拒绝；新增 `tcmdb template`（只含表头的模板，`--demo` 生成全部为 pending 的虚构示例）与 `tcmdb verify`（无可检查数据集时以退出码 3 报告 `NO DATASETS CHECKED`）。
- 2026-10-06 新增 134 的科研层通道：`sources.hkbu` 用同一个严格读取器读审核过的 `formula_herb.tsv`，把能完整研究的方剂转成药材层方剂版本。用法：`build_source_snapshots.py gold --hkbu-formulas …`，然后 `run_network_pharmacology.py --formula hkbu:formula.<id>`。
  - 整首方剂的每一行都已核对、字段齐全且一致，每味药都能解析为药材表中的一味药，才导出；其余列出原因。
  - 繁体药名在原文解析不出时，逐字转简体再解析，字表取自 OpenCC。
  - 组成边以 HKBU 导出为主要知识来源，以 `source_row_id` 为记录号，放行的假说可以追溯到原行。
  - 商用运行现在要求被研究方剂的组成记录本身允许商用：HKBU 和方剂表都未声明许可，都会被拒绝。
  - 方剂在快照构建后被改动时，运行被拒绝。
  - 目前只用合成数据验证；研究闭环尚不识别 HKBU 方剂 ID。
- 后续阶段的 TCMSSD、古籍、WHO ICTRP/ChiCTR、2025 版《中国药典》、HerbComb、GNDC 尚未登记，需先取得获准导出与字段、许可核验后才接入。

## Using it

```python
from bioagent.tcmdb import TCMDataHub

hub = TCMDataHub()                       # $BIOAGENT_TCMDB, or <data lake>/tcmdb
hub.catalog(module="M7")                 # the safety-layer sources and how each is reached
hub.fetch("itcm"); hub.build("itcm")     # download, then load and extract relations
hub.herb_ingredients("黄芪")             # every built source; exact name match
hub.herb_ingredients("黄芪", contains=True)   # also 炙黄芪, 黄芪鳖甲散 ...
hub.ingredient_targets("pubchem:5280343", evidence=["known"])
hub.evidence_for("黄芪")                 # trials, meta-analyses and papers recorded in HERB
hub.query("herb2", "clinical_trial", where={"NCT_id": "NCT01699074"})
hub.live("dcabm_tcm", "herb_blood", names=["HUANG QI"])
```

```bash
python -m bioagent.cli tcmdb sources --access live_api
python -m bioagent.cli tcmdb fetch batman2 && python -m bioagent.cli tcmdb build batman2
python -m bioagent.cli tcmdb relations ingredient_target --subject pubchem:5280343 --evidence known
python -m bioagent.cli tcmdb live openfda herbal_event_reactions product=GINKGO
```

### The relation shape

Every source is reduced to one row shape:

```
kind, source, subject_type, subject_id, subject_name,
object_type, object_id, object_name, evidence, score, reference, note,
effect, outcome, context, license
```

The source tables stay in the same SQLite file next to the relations, so a relation can
always be compared with the row it came from.

- **ids**: a global identifier where the source gives one (`pubchem:`, `inchikey:`,
  `symbol:`, `ncbigene:`, `uniprot:`, `ensembl:`, `umls:`, `nct:`, `pmid:`, `rxnorm:`,
  `meddra:`, and `ncbiprotein:` for an NCBI Protein accession, `obi:` for an OBI
  assay-type term); otherwise `<source>:<local id>`.
- **names**: every name the source gives, joined by ` | `. A query matches any one of
  them exactly, ignoring case. `contains=True` switches to substring matching.
- **evidence**: how the source knows the relation, never upgraded:
  - `known`: measured or curated;
  - `predicted`: a model's output, with its score;
  - `aggregated`: integrated from other databases without per-row provenance;
  - `listed`: a composition list;
  - `reported`: a document about the subject;
  - `mentioned`: a text-mined co-mention;
  - `associated`: a statistical association measured in a population or a screen
    (eQTL, GWAS);
  - `signal`: a disproportionality signal from spontaneous reports.

  Only `known` and `reported` rows point at observations; the others support a
  hypothesis at most. The hub only retrieves and labels. It makes no claims, and its
  rows are not yet wired into the research loop. A claim built on them must go through
  the snapshot pipeline (`bioagent.sources`), where `tcm.EvidenceTier` and the artifact
  validator apply.
- **score / reference / note**:
  - `score` is the source's own number (prediction score, STITCH or DisGeNET score, PRR);
  - `reference` is a citation;
  - `note` holds the qualifiers (dose, clinical status, mechanism), and `via X` when the
    row names the upstream database it came from.
- **effect**: what the subject does to the object, only when the source says so:
  `activation` / `inhibition` (on activity: agonist, inhibitor, blocker), `increase` /
  `decrease` (on amount or expression), `binding`, `degradation`, `modulation`, `other`.
  The source's own word stays in the context (`action`). An effect is never inferred.
- **outcome**: `positive` (the relation was found), `negative` (tested and not found: an
  inactive assay, a screen without a hit) or `inconclusive` (the source flags the
  result). A pair that was never tested has no row, so "not tested" and "tested
  negative" stay apart.
- **context**: the conditions as JSON: species, cell line, tissue, plant part, dose and
  unit, time, method or assay, whether the interaction is direct, the residue, P value,
  effect size, sample size, quality flags (`tcmdb.rowkit.CONTEXT_KEYS`).
- **license**: the licence of the file the row came from. TM-MC, for one, licenses its
  files differently. `relations(commercial=True)` keeps only rows whose licence allows
  commercial reuse of derived data: open or share-alike. Non-commercial, no-derivatives
  and unstated licences are left out, because "not stated" grants nothing.

A row whose object the source could not identify (TM-MC's compound `ID 0`) is not a
relation, and it is not dropped either: it goes to the store's `unresolved` table
(`hub.unresolved(key)`). Merging all such rows under one placeholder id would make one
compound out of every compound the source could not identify.

### Three layers, not one graph

| Layer | Holds | Where |
| --- | --- | --- |
| Raw files | each download as served, with its URL, size and SHA-256 | `raw/<key>/`, `.downloads.json` |
| Tables | each file loaded as written (values kept as text) | `db/<key>.sqlite`, one table per file |
| Relations | the uniform rows above, pointing back to their source rows | the `relations` table of the same store |

Matrices (expression profiles, dose-response curves, screens, spectra archives, images)
stay files or tables. They are not exploded into millions of low-information relation
rows. A relation points at the record it summarises, and statistics run on the matrix.

### Before a dataset is used: `tcmdb check`

`python -m bioagent.cli tcmdb check <key>` (or `all`) is the minimum acceptance test:
- every default file is present, and none is an HTML page (a login, challenge or error
  page) saved in its place; `fetch` refuses such a page as well;
- the store is built, and two rebuilds from the same files give the same relations.
  Each build records an order-independent digest of its relations;
- every row uses a declared relation kind and a known evidence, effect and outcome, and
  its context is JSON with known keys;
- each relation kind's licence and reuse class is reported; an unstated licence is a
  warning;
- the unresolved rows are counted.

## Decisions that are not obvious

- **Undocumented endpoints.** TCMBank, ITCM and TTD answer unauthenticated GET requests
  that their own pages make. These are wrapped, marked *undocumented* on every
  operation, rate-limited to one request per second, and backed by the downloaded files,
  which stay the reference copy.

  The undocumented **POST** APIs of HERB (`/chedi/api/`, a JSON body) and SymMap
  (`/related_components/`, a plain form of `rrid`, `table_name`, `filter`) are wrapped,
  on the repository owner's decision of 2026-10-02, as the connectors `herb_api` and
  `symmap`. Neither needs a token or login, neither site states terms against it, and
  neither has a robots.txt. The wrapper only asks about the entities a person names:
  `tcmdb enrich 黄芪` resolves the herb in the downloaded entity tables, sends one request
  per relation at most once a second, and caches each answer under
  `raw/<symmap_api|herb_api>/`; it never walks the id space. The cached answers are built
  into the stores `symmap_api` and `herb_api` like any other dataset.

  What the answers are is kept on each row:
  - SymMap's herb→target, herb→disease and modern-medicine symptoms are inferred through
    ingredients, so they are `predicted` (with the IES score, P and FDR);
  - SymMap's ingredient lists with PubMed ids are **text co-mentions**, not experiments
    (黄芪 is "linked" to arsenic by 104 such papers), so they are `mentioned`;
  - HERB's literature-graded targets and diseases name the paper, so they are `reported`;
  - HERB's ingredient→target rows name their upstream databases (`via TTD; STITCH`).

- **TCMSP and CancerHSP are not scraped.** The old site embeds its data in HTML pages
  and requires a per-page token copied from the home page; reproducing that token is
  working around the site's guard. The new site's API needs an account (premium
  membership is paid).

  TCMSP's ODbL/DbCL licence does permit reusing data a person exports. The
  `tcmsp_export` dataset reads such exports: the Ingredients and Related Targets tabs of
  each herb, saved as CSV with a `herb` column.

- **BATMAN-TCM 2.0's API is defined but not registered.** It answered the survey's
  `queryTarget` request. During verification the same day it returned 502 and then
  timed out on every retry. The repository ships a connector only when its live
  verification succeeded, so the definition waits in `PENDING_TCM_SOURCES`; register it
  after `python scripts/verify_connectors.py --only batman_tcm2` succeeds. Its six
  complete download files are the `batman2` dataset, so its data is available now.

- **TCMIO target ids are the target table's row numbers, and that was checked.**
  The ingredient–target file refers to targets by a number, and `target.xlsx` has no id
  column. The mapping was tested against TCMIO's own site, through the GET JSON
  endpoints its pages call (`scripts/verify_tcmio_targets.py`):
  - on 2026-10-02, 22 of 22 sampled `/targets/<id>/json` records matched the gene and
    UniProt accession of row `<id>`;
  - for 16 of 16 sampled ingredients, `/ingredients/<id>/targets` returned exactly the
    targets the relation file lists.

  All 41,527 TCMIO ingredient→target pairs therefore carry `uniprot:` ids, with the
  TCMIO id kept in `note`. If a future release names an id beyond the target table,
  nothing is mapped and the TCMIO ids are kept.

- **Dead or compromised hosts are recorded, not used.**
  - HIT 1.0's page is defaced ("Hacked By …"), so the host is treated as compromised.
  - TCMSID's domain now serves an unrelated site.
  - ccTCM, HIT 2.0, ETCM 2.0 and IGTCM serve their data from non-standard ports
    (3456, 2345, 18124, 96), which this environment cannot reach. They may well work
    from another network.

- **Plain http.** HERB, SymMap, TCMBank (whose certificate expired on 2026-06-02) and
  TCMIO work only over plain http. No certificate check was ever disabled; those
  sources are simply addressed by `http://`.

- **"Safety" databases that are something else.** Three entries in the document's
  safety layer were checked:
  - MRTCM is a dataset of metal and metalloid risk in TCM, with no public site;
  - HIM is a 2013 database of in-vivo metabolism, and its domain is gone;
  - TCMSTD's front end loads, but its data API returned 503.

  The hub's safety evidence therefore comes from openFDA/FAERS, OFFSIDES and DDID.

## When several databases give the same relation

Herb→ingredient comes from six sources and ingredient→target from eight. "In how many
databases is it" looks like a vote but is not one, for six reasons:

1. **The same thing has different ids.** Quercetin is `pubchem:5280343` in ITCM and
   BATMAN, an InChIKey in TM-MC, `HBIN041495` in HERB and `SMIT…` in SymMap. TNF is a
   symbol, an Entrez id, a UniProt accession or an Ensembl protein.
2. **Databases copy each other.** ITCM integrates TCMSP, SymMap, TCMID and ETCM; HERB
   lists the upstream of each target (`via TTD; STITCH`); BATMAN's known targets come from
   HIT, DrugBank, KEGG or TTD. Five databases repeating one TCMSP row are one source.
3. **The rows are different kinds of claim.** An experiment in a paper, a curated
   listing, an integration of other databases, a text co-mention, a model prediction and
   a pharmacovigilance signal are not interchangeable, and many of one kind do not make
   one of another.
4. **Absence is not denial.** A database that never covers 黄芪 says nothing about it; a
   database that covers 黄芪 and lists 80 other ingredients but not this one is *silent*.
5. **Names collide.** One name can resolve to two compounds; 黄芪 and 炙黄芪 are
   different materia medica.
6. **Formulas have versions.** BATMAN lists 13 records named 补中益气汤 with different
   herbs; pooling them gave a "formula" of 58 herbs.

`tcmdb.consensus` handles them in this order:

| Step | What it does |
| --- | --- |
| Unify ids | Compounds to an InChIKey (via HERB's and TM-MC's CID↔InChIKey tables), genes to a symbol, herbs to a materia medica entry (Chinese, Latin or pinyin); a processed form stays separate unless `--merge-processed`. A name query is also run under every id it resolves to, so sources that store ids only are reached. |
| Keep evidence kinds apart | Each assertion keeps its rows by kind: known, reported, listed, aggregated, mentioned, predicted, signal. They are ranked, never summed or upgraded. |
| Trace lineage | An observed row's lineage is its paper (PMID/DOI/NCT); an integrated row's is the upstreams its note names; a prediction's is its model; a declared "one of A, B, C" is one unit, not three; anything else is the database itself. |
| Count independent lineages | Units that may share an upstream are joined, and connected components are counted. When in doubt it counts fewer. |
| Detect copying | A **survey** (`tcmdb survey <kind>`) samples subjects that ≥3 sources cover and measures pairwise Jaccard of their object sets. Pairs at ≥0.5 (with ≥20 objects) form a copy cluster that counts once. The clusters come from the saved survey, not from one query: on a well-studied subject independent sources converge on the truth, and a per-query overlap would mistake that for copying. |
| Keep results and directions apart | A negative result (an inactive assay, a non-hit) never counts as support, and support never cancels it: both sides are listed (`outcomes`, `contradicted_by`). Opposite effects (activation and inhibition) are flagged as `effect_conflict`, not resolved. A screen that has no row for a pair did not test it (`not_tested_in`); that is not silence. |
| Report silence and ambiguity | Each assertion names the sources that cover the subject but lack the object. Names that resolve to several entities are listed, not merged. |
| Compare versions | Formulas are compared version by version (`tcmdb compare formula_herb 补中益气汤`), never pooled. |

Each assertion ends in one **support class**, from the best evidence it has:

| Class | Meaning |
| --- | --- |
| independently_replicated | observed (known/reported) in ≥2 independent lineages |
| documented | observed in one lineage |
| associated | a statistical association (eQTL, GWAS), no direct observation |
| integrated | only listed or aggregated rows, however many databases |
| mentioned | only text co-mention |
| predicted | only model predictions |
| signal | only pharmacovigilance disproportionality |
| tested_negative | every row on the pair says it was tested and not found |
| inconclusive | only rows the sources flag as unreliable |

Scores (BATMAN's model score, SymMap's IES, a PRR) are kept per source and never
combined: they measure different things on different scales.

**On the local data (2026-10-01/02):**

| Query | Assertions | In ≥2 databases | independently_replicated | documented | integrated | mentioned | predicted |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| quercetin (`pubchem:5280343`) → targets, 8 sources | 4,451 | 3,556 | 215 | 378 | 3,749 | 33 | 76 |
| 黄芪 → ingredients (6 sources) | 862 | 196 | 80 | 114 | 492 | 176 | – |

Counting databases would call 3,556 quercetin targets "confirmed by several databases";
215 have independent observations behind them. On one query, ITCM and TM-MC agree at
Jaccard 0.82 for quercetin. Across 300 sampled compounds the survey puts them at 0.31,
so most of that agreement is convergence, not copying. For herb→ingredient, BATMAN 2.0
and ITCM overlap at 0.52 across 300 herbs and are counted as one cluster. 补中益气汤 has
15 versions (13 BATMAN, HERB, TM-MC), and similar versions agree at Jaccard 0.5–0.8.

**What remains judgment.** The Jaccard threshold and the sampling frame are choices. With
`--min-sources 2` the measured overlaps drop, so copy detection is a diagnostic to read,
not a proof; the threshold and the survey date are reported with every result.
Declared lineages (BATMAN, ITCM) come from the databases' own papers. When a lineage is
unknown, the row counts as its own database, which can overcount; the copy survey is the
check on that.

```bash
python -m bioagent.cli tcmdb enrich 黄芪                     # SymMap + HERB per-entity queries, cached
python -m bioagent.cli tcmdb survey herb_ingredient          # copy clusters, saved
python -m bioagent.cli tcmdb consensus herb_ingredient --subject 黄芪 --min-support documented
python -m bioagent.cli tcmdb compare formula_herb 补中益气汤
```

<!-- zh -->
**同一类关系，多个数据库都有时怎么办（中文摘要）**：数据库条数不是票数。

- **先统一 ID**：化合物统一到 InChIKey，基因统一到符号，药材统一到本草条目；炮制品默认单列。
- **证据类型分开，不相加、不升级**：实验/文献、收录、整合、共现、预测、信号，各算各的。
- **按独立来源计数，不按数据库计数**：沿着每行的来源（论文、上游库、模型）追溯谱系；“A、B、C 之一”只算一个；可能同源的就合并。
- **抄录检测**：用全局抽样调查判断哪些库互相复制（Jaccard ≥ 0.5），而不是用单次查询的重叠。单次查询里，对研究充分的对象，独立来源本来就会趋同。
- **区分“没收录”和“收录了却没有”**；重名不合并；方剂按版本比较，不合并。
- **分数保留在各自来源上，不合成**。

最终每条关系落到一个支持等级。以槲皮素为例：3,556 个靶点“出现在 ≥2 个库”，但有独立观察支持的只有 215 个。

## Licences

Most of these databases state **no data licence**: HERB, SymMap, ITCM, TM-MC, TCMIO,
DDID, TTD, nSIDES and TCMBank, and NPASS and CMAUP (whose sites were re-read on
2026-10-07). Some state "free for academic use" (BATMAN-TCM).
Clear licences:

| Licence | Sources |
| --- | --- |
| CC0 | openFDA, DrugBank's open vocabulary |
| CC BY 4.0 | IEDB, TCMP-300, TCM-Ladder |
| Apache-2.0 | ShenNong |
| ODbL 1.0 / DbCL 1.0 | TCMSP |
| Use and redistribution with attribution | ImmPort gene lists |
| NLM terms | MeSH |

The hub downloads files into the user's own data directory and redistributes nothing.
Each built store records the licence of every file it loaded (`_tcmdb_files`). Before
anyone publishes data derived from a source without a stated licence, the source's
authors should be asked.

## Every source

The table is generated from `bioagent/data/tcm_source_catalog.json`, which is also what
`hub.catalog()` returns.

| # | Database | Modules | Access | Connector / dataset | Why |
| ---: | --- | --- | --- | --- | --- |
| 1 | TCMSP (v2.3) | M1, M2 | manual import | `tcmsp_export` | Old site serves the data only inside HTML pages behind a per-page token; the new next.tcmsp-e.com API requires an account. Not scraped. Licence ODbL 1.0 / DbCL 1.0 permits reuse of data a person exports. |
| 2 | TCM Database@Taiwan | M1 | unreachable | – | Server times out (HTTP 503 from proxy); appears defunct; no mirrors found. |
| 3 | TCMBank | M1 | live API + snapshot | `tcmbank` / `tcmbank` | Entity xlsx bulk files + undocumented but simple GET JSON API for relations; no auth. |
| 4 | TCMSID | M1 | unreachable | – | Domain serves an unrelated third-party placeholder; dataset not obtainable online. |
| 5 | NPASS 2.0 | M1 | snapshot | `sources:npass` | Bulk files already acquired and parsed (sources.parsers.npass); used by the research loop. |
| 6 | CMAUP 2.0 | M1 | snapshot | `sources:cmaup` | Bulk files already acquired and parsed (sources.parsers.cmaup); used by the research loop. |
| 7 | ccTCM | M1 | unreachable | – | Front-end loads but all data come from a raw-IP:3456 backend that times out from this environment. |
| 8 | BATMAN-TCM 2.0 | M2 | snapshot | `batman2` | Six complete TSV dumps (known and predicted ingredient-target pairs, herbs, formulas) are the dataset 'batman2'. The documented queryTarget/queryTcm API answered the survey, then returned 502 and timed out throughout verification; the connector is defined as pending (providers.public_apis_tcm.PENDING_TCM_SOURCES) and is registered once a verification run succeeds. |
| 9 | HIT 2.0 | M2 | unreachable | – | Real server on port 2345 times out; use HIT-derived data via BATMAN-TCM 2.0 / dbPTH. |
| 10 | HIT 1.0 | M2 | unreachable | – | Host defaced ('Hacked By ...'); treat as compromised and do not fetch from it. |
| 11 | dbPTH 1.0 | M2 | snapshot | `dbpth` | Large, free bulk text/SQL files; no API. |
| 12 | PharmMapper | M2 | restricted | – | Only an asynchronous email job service; no API or data dump - must be run manually per compound. |
| 13 | HERB 2.0 | M3, M10 | live API + snapshot | `herb_api` / `herb2` | Static TSV entity files; relations per entity from the JSON post HERB's own pages send (wrapped on the owner's decision, 1 req/s, cached). |
| 14 | HERB 1.0 | M3 | snapshot | `herb1` | Use V1 TSVs from HERB 2.0 host; herb.ac.cn's own download endpoint is disabled. |
| 15 | ETCM v2.0 | M3, M4 | unreachable | – | Only static SPA shell reachable; all data come from :18124 which times out from this egress (would be web_export/undocumented API elsewhere). |
| 16 | TCMID 2.0 | M3 | unreachable | – | Origin times out; only a tiny unofficial herb table on Zenodo; TCMID ids survive as cross-refs in HERB 2.0/ITCM. |
| 17 | TCMM | M3 | unreachable | – | Expired certificate blocks access; complete data only by contacting authors (restricted). |
| 18 | TCM-Mesh | M3 | unreachable | – | Hosting returns Cloudways domain-mapping error page; database offline. |
| 19 | LTM-TCM | M3, M5 | unreachable | – | Host NXDOMAIN. |
| 20 | ITCM | M3 | live API + snapshot | `itcm` / `itcm` | Public TSV downloads incl. relation tables, plus GET JSON endpoints for live lookups (undocumented). |
| 21 | TCM-ID | M3 | manual import | – | No bulk files or API; HTML detail pages only (per-record POST export). |
| 22 | YaTCM | M3 | unreachable | – | Origin times out from this egress. |
| 23 | CPMCP | M4, M10 | unreachable | – | Domain NXDOMAIN; partial coverage via HERB 2.0 formula table cross-refs. |
| 24 | TCMKD | M4, M5 | restricted | – | Login/registration required; origin blocks this egress. |
| 25 | 古今医案云平台/方剂数据库 | M4 | restricted | – | Only aggregate stats public; case content requires login + CAPTCHA. |
| 26 | 中国中医药数据库(CINTCM/CINTMED) | M4 | restricted | – | Login + fee schedule; no export/API. |
| 27 | SymMap v2 | M5 | live API + snapshot | `symmap` / `symmap2` | Entity xlsx files; associations per entity from the form post SymMap's own detail pages send (wrapped on the owner's decision, 1 req/s, cached). |
| 28 | DCABM-TCM | M6 | live API + snapshot | `dcabm_tcm` / `dcabm` | Documented JSON query API for prescription/herb/ingredient blood exposure plus a structure file dump. |
| 29 | MRTCM | M7 | unreachable | – | No public site found; only the paper (and possibly its supplementary tables) is usable. |
| 30 | HIM数据库 | M7 | unreachable | – | Domain no longer resolves; only paper supplementary data could be used. |
| 31 | TCMSTD（系统毒理学数据库） | M7 | unreachable | – | Front end loads but its data API returned 503; recheck later; if it returns, it would be web_export via undocumented POST JSON. |
| 32 | openFDA / FAERS | M7 | live API | `openfda` | Documented, keyless JSON API with count aggregation, plus JSON bulk partitions. |
| 33 | FDA FAERS季度原始文件 | M7 | snapshot | `faers` | Stable direct zip URLs per quarter; good for local snapshot and herbal-name mapping. |
| 34 | TADRRS-HM（台湾） | M7 | restricted | – | Case data only via TFDA/NRICM; use paper supplementary aggregates. |
| 35 | KAERS（韩国） | M7 | restricted | – | Application-only raw data; Korean hosts unreachable; figshare holds only summary tables. |
| 36 | CADRMS/国家药品不良反应监测 | M7 | manual import | – | Only narrative/aggregate HTML annual reports (2009-2025); manual extraction of TCM figures. |
| 37 | NCCIH HerbList | M7 | manual import | – | Public-domain HTML pages, no data API; a person may save the pages they need. |
| 38 | CancerHSP | M7, M9 | manual import | – | Same page-token pattern as TCMSP; not scraped. A person may export tables and import them. |
| 39 | DDID | M8 | snapshot | `ddid` | Direct CSV downloads cover all tables; host is flaky so snapshot once. |
| 40 | PHYDGI | M8 | restricted | – | Commercial, embedded in Synapse product; only the paper is public. |
| 41 | TwoSIDES | M8 | snapshot | `nsides` | Static files on public S3 with bucket listing; ideal for snapshot. |
| 42 | openFDA/FAERS（联用信号挖掘模式） | M8 | live API | `openfda` | Documented, keyless JSON API with count aggregation, plus JSON bulk partitions. |
| 43 | Natural Medicines（商业） | M8 | restricted | – | Paywalled commercial reference; characterise only. |
| 44 | Medscape Interaction Checker | M8 | restricted | – | Undocumented internal API under restrictive commercial terms; characterise only. |
| 45 | Stockley's Herbal Medicines Interactions | M8 | restricted | – | Subscription-only book/database; characterise only. |
| 46 | DrugBank | M8, M14 | restricted | `drugbank_vocabulary` | API and full database need an account or a paid key; the CC0 open vocabulary is behind a bot challenge here, so a person downloads it and it is imported as a local dataset. |
| 47 | TCMIO | M9 | snapshot | `tcmio` | 9 direct XLSX/SDF downloads, frozen 2019 release. |
| 48 | ImmPort | M9 | snapshot | `immport` | Open gene-list files and anonymous study search; study-level data needs a registered token. |
| 49 | IEDB | M9 | live API | `iedb` | Documented keyless PostgREST API plus full zip exports. |
| 50 | SuperTCM | M9 | unreachable | – | DNS failure and proxy 502; cannot verify. Recheck from a non-proxied network. |
| 51 | CINTCM | M10 | restricted | – | Login + fee schedule; no export/API. |
| 52 | INPUT（网络药理平台） | M10 | manual import | – | Analysis platform with POST-only queries and no downloads; manual export only. |
| 53 | TM-MC 2.0 | M11 | snapshot | `tmmc2` | Complete, documented xlsx dumps of all tables; no API. |
| 54 | BATMAN-TCM 1.0 | M11 | snapshot | `batman1` | Only the known-DTI flat file is retrievable programmatically; predictions require job submission - use 2.0 dumps instead. |
| 55 | ShenNong-TCM-Dataset | M12 | live API + snapshot | `huggingface` / `shennong` | Single 110 MB JSON on HF, ungated Apache-2.0; snapshot it (datasets-server API usable for previews only). |
| 56 | TCM-NER | M12 | restricted | – | Only obtainable after Tianchi login; a person must download once, then it can be stored as a local snapshot (CC BY-SA permits). |
| 57 | TCM-Ladder | M12 | live API + snapshot | `huggingface` / `tcm_ladder` | Ungated CC-BY parquet files on HF; snapshot text parquets (~1.3 MB), visual/video optional. |
| 58 | CMLM-ZhongJing数据 | M12 | restricted | – | Dataset is described but not published; only model weights are public. |
| 59 | herbnet（知识图谱） | M12 | restricted | – | Name is ambiguous and none of the candidate repos publishes the KG itself; confirm the intended source with the requester. |
| 60 | 中医医案知识图谱 | M12 | restricted | – | Repo publishes code + a 10-line sample; the full KG would need author contact (search found 26 'TCM-KG' repos, 114-star ywjawmw/TCM_KG is the main one). |
| 61 | TCMP-300 | M13 | live API + snapshot | `figshare` / `tcmp300` | Figshare API gives stable metadata/file listing; species CSV (20 KB) is the useful table, image tarball only if vision work is needed. |
| 62 | IGTCM | M13 | unreachable | – | Only served on a non-standard port that this egress cannot reach; re-test from an unrestricted network. |
| 63 | TTD（Therapeutic Target Database） | M14 | live API + snapshot | `ttd` / `ttd` | Plain static TSV/SDF files for a local snapshot; undocumented JSON API usable for per-ID lookups. |
| 64 | MeSH | M14 | live API | `mesh` | Documented REST lookup + SPARQL already wrapped (id.nlm.nih.gov); annual XML for local snapshot. |
| 65 | PreDC | M14 | unreachable | – | Legacy site in 'maintenance' since migration of TCMSP; no download; only reachable via a redirect-leaked page body, which is not a sound access path. |
| 66 | NPACT | M14 | manual import | – | Only HTML pages, no download/API, all rights reserved and 20 s crawl delay; a person should export/request data, or rely on PubChem CIDs. |



## Sources added from the review of 2026-09-30 (67–133)

Each was checked the same way, then re-checked by an independent pass before anything was
wrapped. `commercial` is what the source's terms say about commercial reuse (`unknown`
when they say nothing, which grants nothing).

| # | Database | Modules | Access | Connector / dataset | Commercial | What was found |
| ---: | --- | --- | --- | --- | --- | --- |
| 67 | COCONUT 2.0 (COlleCtion of Open Natural prodUcTs) | M1 | live API + snapshot | `coconut` / `coconut` | allowed | COCONUT publishes a monthly CC0 release on S3. Its CSV is the only file listing each compound's organisms, collections and DOIs, and the only unauthenticated API resource is POST /api/search. Because COCONUT collects 71 other natural-product databases without saying which collection reported which organism, organism_compound rows are labelled aggregated. Each row names its collections in a 'via .. |
| 68 | World Flora Online Plant List (June 2026) | M1, M14 | snapshot | – / `wfo` | allowed | WFO's twice-yearly CC0 Plant List is on Zenodo. Its Darwin Core backbone is loaded as name tables (accepted names, synonyms, WFO ids, IPNI LSIDs) for resolving plant names from other sources. It is a taxonomy backbone of species, not a materia medica, so it yields no relations. The documented matching API was defined but not shipped, because its host omits an intermediate certificate and TLS verif |
| 69 | IMPPAT 3.0 (Indian Medicinal Plants, Phytochemistry And Therapeutics) | M11, M1 | snapshot | – / `imppat3` | forbidden | IMPPAT 3.0 (released 2026-09-30) offers eight TSVs and two SDFs as plain downloads, with no API. The TSVs are loaded and the SDFs kept optional. Plant-phytochemical, plant-use and formulation-plant links are digitised listings, so their evidence is listed. Target rows from ChEMBL, NPASS and BindingDB are aggregated. Bioactivity rows with a measured EC50/IC50/Kd/Ki are known, and those without a Un |
| 70 | KNApSAcK Core | M1 | live API | `knapsack` / – | forbidden | KNApSAcK has no bulk data: its download is a 2008 Java client with demo spectra. Its top page documents a URL scheme 'for incorporation to program', and the wrapper calls the pages that scheme leads to (information.php, result.php) for one metabolite, an organism's metabolites, a name search, or one pair's reference, returning the HTML whole. The terms forbid redistribution, so nothing is stored a |
| 71 | TCMToxDB | M7 | snapshot | – / `tcmtoxdb` | unknown | The Download page serves four public static entity CSVs (about 10 MB). The herb-ingredient, herb-target and ingredient-target edges and the literature toxicity records are served only by an undocumented backend that answered 502 on every try. The snapshot therefore yields only what the CSV columns state: formula->herb with doses parsed from the Prescription text, herb toxicity grades, and gene org |
| 72 | PhytoHub | M6 | live API | `phytohub` / – | unknown | PhytoHub offers no download or documented API. Its Rails pages answer .json and .sdf formats for single compounds, name searches and food names without any login. Those are wrapped as per-entity look-ups. The food-compound and precursor-metabolite relations exist only as HTML, so no snapshot dataset is built and they remain manual. |
| 73 | MIBiG 4.0 | M1 | live API + snapshot | `mibig` / `mibig` | allowed | MIBiG publishes its 4.0 release as a 0.95 MB CC BY 4.0 JSON tarball, which is the default snapshot. GBK and FASTA files are optional raw files, and the 5.0rc1 release candidate is not used. The site's per-entry annotations.json files are identical to the release records. Those files and the stats endpoint are wrapped for live look-ups. The full repository listing (a bulk dump) and the broken searc |
| 74 | gutMGene v2.0 | M6 | live API + snapshot | `gutmgene` / `gutmgene` | unknown | The three literature-curated association CSVs are public direct downloads of about 3 MB. They are the default snapshot, and the large metabolic-reconstruction tarball (prediction output) is optional. The browse page's POST endpoint gives the evidence rows behind one association and is wrapped as a per-entity look-up. Causal rows are labelled known and correlational rows associated. |
| 75 | EMA herbal medicines (HMPC) | M10, M7, M11 | snapshot | – / `ema_herbal` | allowed | EMA documents its website data JSON exports, which are regenerated twice a day. The herbal medicines index (0.2 MB) lists the substances, and the whole-site documents index (37 MB) carries the document URLs. Herbal monographs, assessment reports, opinions, list entries, public statements and summaries become subject_monograph rows (reported) pointing at each document, linked to the substance its t |
| 76 | MassBank | M1, M6 | live API + snapshot | `massbank` / `massbank` | unknown | MassBank publishes a documented MassBank3 REST API and export service, and versioned data releases on Zenodo. The live database lags the release: it served data version 2025.10 while release 2026.03 is out. The record-text zip is the only bulk form that keeps each record's LICENSE line, so the snapshot parses it into one row per record and leaves the peaks in the archive. The NIST MSP drops the pe |
| 77 | GNPS spectral libraries | M1, M6 | live API + snapshot | `gnps` / `gnps` | allowed | GNPS2 serves documented JSON endpoints and daily per-library JSON exports. The ALL_GNPS aggregates are 2-10 GB and gnpslibraryjson has no peaks and is stale. The snapshot therefore loads twelve small CC0 natural-product libraries, about 175 MB in total. The library quality class maps to evidence: gold is known, silver reported, bronze reported/inconclusive. Propagated libraries are aggregated, and |
| 78 | NP-MRD | M1 | snapshot | – / `np_mrd` | forbidden | NP-MRD offers download archives (released 2025-08-01) for experimental peak lists, experimental shift-assignment tables, SMILES chunks and seven NP-Card JSON chunks. Predicted nmrML (2.9 GB) and the FIDs (13.7 GB) are separate archives. The snapshot loads only the experimental tables as known compound_spectrum rows. It uses two default card chunks for InChIKeys, which cover about 95% of the tables |
| 79 | Metabolomics Workbench / RefMet | M6 | live API | `metabolomics_workbench` / – | unknown | The site's robots.txt disallows everything, but the REST API v1.2 documentation explicitly offers the API to third-party scripts. The connector therefore wraps only per-entity look-ups: RefMet match/name/formula, compound by InChIKey or CID, one study's summary, analyses or metabolites, studies by RefMet name, and MetStat. It never wraps /refmet/all or the whole-study dumps. The m/z search redirec |
| 80 | ChEBI | M14, M1 | live API + snapshot | `chebi` / `chebi` | allowed | ChEBI 2.0 publishes PostgreSQL-style quoted TSV flat files under CC BY 4.0, and a documented OpenAPI REST API that answered without a key. The flat files are wrapped as a snapshot that gives natural-product origins and the is_a/has_role ontology assertions for chemical entities. The snapshot also maps ChEBI ids, primary and secondary, to InChIKeys for the crosswalk. The REST API is wrapped for per |
| 81 | Rhea | M14, M6 | live API + snapshot | `rhea` / `rhea` | allowed | Rhea serves documented bulk TSV and RDF files on ftp.expasy.org under CC BY 4.0, plus a REST search that its help page offers to programs although robots.txt disallows query URLs. The participants and the rhea2uniprot_sprot file are snapshotted, keeping each participant's side or role and each enzyme's direction. The REST search is wrapped only for targeted, small-limit lookups. |
| 82 | MetaNetX/MNXref | M14 | live API + snapshot | `metanetx` / `metanetx` | forbidden | MetaNetX ships MNXref as one TSV bundle whose '#' headers state each upstream resource's licence, several of them non-commercial, so the dataset is marked commercial_use forbidden. It is loaded as an identifier-reconciliation layer that feeds the compound crosswalk and writes no evidence rows. The documented SPARQL endpoint is wrapped for single-id lookups at the 10 s crawl delay. |
| 83 | SABIO-RK | M14 | live API | `sabio_rk` / – | forbidden | SABIO-RK's documented Export API (OpenAPI 3.1.1) answered all five operations without a key. The site's robots.txt bars crawlers and the data are under a non-commercial licence. It is therefore wrapped only as a live connector for per-entity kinetic-law, compound, enzyme and protein lookups, with no bulk snapshot. |
| 84 | USDA FoodData Central | M14 | snapshot | – / `fooddata_central` | allowed | FoodData Central publishes CC0 bulk CSV zips. The Foundation Foods (April 2026) and SR Legacy (2018) files are small and are snapshotted as per-100 g food_nutrient rows with amount and unit, using evidence derived from each row's derivation code. The API requires an api.data.gov key, so no live connector is built. |
| 85 | BioGRID (interactions, chemical associations) + BioGRID ORCS (CRISPR screens) | M2, M14 | snapshot | – / `biogrid` | allowed | BioGRID publishes all its curated interaction and chemical files under MIT at stable release URLs, but its REST APIs return 401 without a registered key, so it is a snapshot. Unpacked, the full Tab 3.0 file is 1.56 GB. The default store therefore holds the multi-validated physical set (35 MB) plus the chemtab (1.4 MB), and the ALL file is optional. ORCS screens are split per organism: the fly arch |
| 86 | IntAct and Complex Portal (EMBL-EBI) | M2, M14 | live API + snapshot | `intact` / `intact` | allowed | IntAct offers documented PSICQUIC and portal REST services, and Complex Portal has a web service, so positive interaction evidence is queried live per gene. The full IntAct MITAB is 11 GB. psimitab species/Human.zip holds human-virus interactions, not human ones, so it was not used. The snapshot holds Complex Portal's human and mouse complextab (CC0) as complex_member rows, plus IntAct's negative- |
| 87 | SIGNOR | M2 | live API + snapshot | `signor` / `signor` | allowed | SIGNOR documents a small per-entity and per-pathway API and publishes quarterly release files under CC BY 4.0, so it has both a live connector and a snapshot. The effect follows SIGNOR's curation manual: up/down-regulates activity -> activation/inhibition, up/down-regulates quantity (including by expression/repression or by stabilization/destabilization) -> increase/decrease, form complex -> bindi |
| 88 | IUPHAR/BPS Guide to PHARMACOLOGY (GtoPdb) | M2, M14 | restricted | – / – | forbidden | GtoPdb's stated policy since September 2026 requires registered users and an API key, with fees for commercial organisations. Its CSV downloads still answer anonymously, but that does not override the policy. Nothing was downloaded or wrapped: a registered user could add it by manual import. |
| 89 | JASPAR 2026 | M14 | live API + snapshot | `jaspar` / `jaspar` | allowed | JASPAR has a documented REST API, so matrix lookup, factor matrices, search, versions and releases are a live connector. The snapshot loads the matrix, annotation, protein and species tables from the SQLite dump as tf_motif rows (factor UniProt -> CORE matrix). The CORE non-redundant matrices stay as raw MEME/JASPAR files for motif scanning. The sites and BED archives (about 300 MB each) are optio |
| 90 | Cellosaurus | M14, M13 | live API + snapshot | `cellosaurus` / `cellosaurus` | allowed | Cellosaurus offers a documented OpenAPI REST service with no key. It also publishes a documented quarterly flat file under CC BY 4.0. The API's Solr name search is ranked rather than exact (id:HepG2 does not return CVCL_0027), so the connector looks lines up by accession, by any primary or secondary accession, and by cross-reference. Exact name resolution is done offline against the snapshot's cel |
| 91 | LINCS L1000 (GEO GSE92742 / GSE70138 metadata) | M13, M2 | snapshot | – / `lincs_l1000` | allowed | The L1000 signatures are deposited in GEO as frozen 2017 series. Their small metadata files (signatures, perturbagens, cell lines, genes) are loaded as tables. The Level 5 GCTX matrices (5-21 GB) are kept as optional raw analysis-layer files and are never turned into relation rows. GSE106127 is recorded as a re-processed subset of these two series. CLUE's own build sits behind registered-user acad |
| 92 | DepMap Public 24Q4 + PRISM Repurposing 24Q2 (figshare) | M9, M13 | snapshot | – / `depmap` | allowed | The last DepMap releases deposited on figshare under CC BY 4.0 are 24Q4 and Repurposing 24Q2. From these, Model.csv, the common-essential lists and the portal compound table are loaded. The extractor yields OncoTree diseases per model (Cellosaurus RRID when present), CRISPR-inferred common essentials, and aggregated compound targets. The gene-effect and viability matrices stay optional raw files a |
| 93 | scPerturb | M13 | snapshot | – / `scperturb` | allowed | scPerturb is two Zenodo deposits of harmonised single-cell perturbation datasets with no query API. The record exports are loaded as a file catalogue (54 RNA/protein h5ad files and 6 ATAC zips). The eight drug-perturbation h5ad files are listed as pinned optional raw files. Relations would need per-perturbation differential expression, so none are extracted. |
| 94 | ARCHS4 | M13 | snapshot | – / `archs4` | unknown | ARCHS4 publishes uniformly re-aligned RNA-seq compendia only as very large HDF5 files on S3, with no data licence stated. Only the version list (66 files with species, level, sample count, size and sha1) is loaded. The human and mouse v2.5 gene matrices are pinned as optional raw pointers with their sha1. Nothing is turned into rows. |
| 95 | eQTL Catalogue | M13, M5 | snapshot | – / `eqtl_catalogue` | allowed | The old REST API (/eqtl/api/) now answers HTTP 410 and points to file-based data access, so it is not wrapped. The dataset metadata tables (release 7, release 8 beta and tabix FTP paths) are a snapshot from the project's GitHub resources. One small GTEx v8 liver SuSiE credible-set file (1.3 MB, optional) is turned into variant_gene rows. The 779 MB merged r8-beta credible-set parquet is listed onl |
| 96 | MaveDB | M13 | live API | `mavedb` / – | unknown | MaveDB's documented REST API answers public reads without a key. It is wrapped for score-set lookup, search, scores-as-CSV, gene and target queries, and the licence list. The licence is chosen per score set, so callers must read license.shortName before reusing scores. The 1.9 GB CC0 Zenodo bulk archive is not fetched because it is over the size limit and the API serves the same records. |
| 97 | International Mouse Phenotyping Consortium (IMPC) | M5, M13 | live API + snapshot | `impc` / `impc` | allowed | IMPC documents its Solr cores for programmatic access and they answer without authentication, so a live connector wraps per-gene and per-MP-term look-ups, statistical results and disease models. Data release 24.0 is pinned as a snapshot: the 5 MB genotype-phenotype assertions file becomes gene_phenotype rows with evidence 'known', species mouse, and zygosity/sex/procedure in context. Viability is  |
| 98 | FinnGen | M5, M13 | restricted | – / – | unknown | Getting FinnGen summary statistics requires filling in a registration form, and the PheWeb browser's robots.txt disallows all crawling. It is catalogued only, with no connector or snapshot. |
| 99 | IEU OpenGWAS | M5, M13 | restricted | – / – | forbidden | Every OpenGWAS API data endpoint requires a personal JWT token, and its EULA restricts commercial use and bulk download. It is catalogued only; a connector would need a token supplied by the user and has not been built. |
| 100 | EPA ToxCast invitrodb v4.3 (CompTox) | M7 | snapshot | – / `comptox` | allowed | EPA's CTX APIs need a personal key, so ToxCast is wrapped only as a snapshot. EPA's PubChem-deposition archive (193 MB, one workbook per endpoint) gives per-sample hit calls for 1,536 endpoints. The release workbooks declare wrong sheet sizes and one has a missing drawing, so a dedicated sheet-XML reader is used. The deposition's outcome code maps directly to positive/negative/inconclusive. AC50s, |
| 101 | AOP-Wiki (OECD AOP Knowledge Base) | M7 | live API + snapshot | `aopwiki` / `aopwiki` | allowed | AOP-Wiki publishes a dated quarterly AOP-XML (10.5 MB). A streaming reader turns it into tables of AOPs, events, KERs, stressors and chemicals. Live per-AOP XML and JSON views answer anonymously and are wrapped as look-ups. KERs carry each AOP's weight-of-evidence call, so evidence is 'known' where it was assessed and 'reported' where it was not. 30 of the 599 AOPs are 'All rights reserved' and ke |
| 102 | Orphadata Science (Orphanet) | M5 | live API + snapshot | `orphadata` / `orphadata` | allowed | Orphanet publishes its curated knowledge as CC BY 4.0 product XML files and as an OpenAPI service. Both answer anonymously, so the files are a snapshot and the API is wrapped for look-ups. Gene-disease associations become target_disease, with the association type as mechanism and Assessed or Not-yet-assessed setting the evidence. HPO annotations become disease_phenotype with the frequency class, a |
| 103 | NHANES 2017-2018 public-use files (CDC/NCHS) | M10 | snapshot | – / `nhanes` | allowed | NHANES publishes participant-level survey files as SAS transport, not relations. Three small public-use files (demographics with design variables, serum PFAS, supplement ingredients) are loaded as tables through a pandas XPT reader that decodes transport zeros. Associations must be estimated with survey weights, strata and PSUs, so the dataset yields no relation rows. |
| 104 | MIMIC-IV (PhysioNet) | M10 | restricted | – / – | forbidden | MIMIC-IV files are released only to credentialed PhysioNet users under a data use agreement. PhysioNet's policy forbids sending the data to online LLM services, so it is catalogue-only: no connector, no dataset, nothing downloaded. This is recorded in the safety module docstring. |
| 105 | DrugCentral | M8, M14 | live API + snapshot | `drugcentral` / `drugcentral` | allowed | DrugCentral publishes small, current bulk files on an open download server (the drug-target interaction TSV is under 1 MB, plus structures and FDA/EMA/PMDA approval lists) under CC BY-SA 4.0. It also runs an unauthenticated, OpenAPI-documented DRS REST service. The TSV becomes drug_target rows: evidence known, effect from ACTION_TYPE with the original word in context.action, act_value/type/source  |
| 106 | DrugComb | M8, M9 | snapshot | – / `drugcomb` | unknown | The DrugComb portal and its API were unreachable. The authors' Zenodo deposits (CC BY 4.0) are the reference copy. The v1.4 synergy summary (one block per drug pair and cell line) is a screen result, so it is kept as a raw analysis-layer file and not exploded into relations. The drug and cell-line identifier tables are loaded to map DrugComb names to ChEMBL, InChIKey, PubChem, Cellosaurus and DepM |
| 107 | PharmacoDB | M9 | live API | `pharmacodb` / – | unknown | PharmacoDB serves a public GraphQL endpoint (used by its own site). It answered compound, cell-line, experiment, dataset-statistics, search and biomarker queries without a key. It re-processes GDSC, CCLE, CTRP and other screens, so it is wrapped as a live per-entity connector only, and its sensitivity data is not snapshotted (that would duplicate GDSC and be a bulk dump). No licence is stated. |
| 108 | GDSC (Genomics of Drug Sensitivity in Cancer) | M9 | snapshot | – / `gdsc` | forbidden | The GDSC release 8.5 files are published anonymously by Sanger under a non-commercial data-usage policy. The GDSC1 and GDSC2 fitted dose-response workbooks are kept as raw analysis-layer files, not exploded. The screened-compound annotation becomes compound_target rows (evidence listed) under GDSC's own putative target names. The Cell Model Passports model list is loaded. No documented live API fo |
| 109 | Pharos / TCRD (IDG) | M14 | live API + snapshot | `pharos` / `pharos` | unknown | Pharos serves a documented, keyless GraphQL API for target lookups by symbol: TDL, family, ligands, diseases, interactions, plus ligand and version queries. It is wrapped as a live connector. The Pharos400 canonical TDL file on the TCRD download server becomes gene_set_member rows (development level and IDG family per protein) with a UniProt -> symbol crosswalk. The 2.26 GB SQL dump is optional. P |
| 110 | PharmVar (Pharmacogene Variation Consortium) | M8 | snapshot | – / `pharmvar` | forbidden | PharmVar's REST API now needs an account key, but its documented all-genes zip download answers anonymously. The zip's haplotype tables are wrapped as star-allele definitions (GRCh38) with evidence 'listed'. Function assignments come only from the keyed API, so they are not included. |
| 111 | CPIC (Clinical Pharmacogenetics Implementation Consortium) / ClinPGx (formerly PharmGKB) annotations | M8 | live API + snapshot | `cpic` / `cpic` | forbidden | CPIC's documented PostgREST API at api.cpicpgx.org answers without authentication. It is wrapped for small filtered lookups: pairs, drugs, genes, alleles, recommendations and report files. The current gene-drug pairs report (xlsx) is the snapshot: levels A-B are 'known', B/C-D are 'listed' and retired pairs are dropped. ClinPGx's bulk files (relationships, summary annotations, chemicals, drug labe |
| 112 | GPCRdb | M2, M14 | live API + snapshot | `gpcrdb` / `gpcrdb` | allowed | GPCRdb's documented web services answer unauthenticated. They are wrapped for per-receptor lookups: protein, drugs, ligand bioactivities, mutants and structures. The documented drugs full-table download (596 KB) is the snapshot, giving drug_target rows with the stated mechanism as the effect. |
| 113 | KLIFS (Kinase-Ligand Interaction Fingerprints and Structures) | M2, M14 | live API | `klifs` / – | unknown | KLIFS's documented v1 API (/api) and beta v2 answer unauthenticated. They are wrapped for small lookups: kinases, ligands, structures, ChEMBL bioactivities per ligand, interaction fingerprints and the drug list. No bulk snapshot was taken. |
| 114 | ProteomicsDB | M13, M14 | live API | `proteomicsdb` / – | forbidden | ProteomicsDB's API answers unauthenticated for tissue expression per protein, plus the API v2 beta for proteins, Kinobeads dose-response curves and the drug-sensitivity dataset list. These are wrapped as per-protein lookups, with no bulk snapshot. |
| 115 | iPTMnet | M14 | snapshot | – / `iptmnet` | forbidden | iPTMnet publishes three headerless tab-separated bulk files (release 6.2) that download without any login. Its documented Swagger API was down (503) throughout, so the connector is defined but held in PENDING. Each ptm.txt row names its source. Curated upstream rows become 'aggregated' with 'via <db>'; RLIMS-P/eFIP rows are text mining and become 'mentioned'. The licence pages contradict each othe |
| 116 | DisProt | M14 | live API + snapshot | `disprot` / `disprot` | allowed | DisProt serves an unauthenticated JSON API for per-entry and per-accession lookups, and the same /api/search endpoint exports the whole pinned release 2026_06 as TSV and JSON. Region annotations (IDPO/GO term, ECO code, PMID) are manual curation, labelled 'known'. Regions flagged as ambiguous evidence are 'inconclusive'; obsolete regions are dropped. Binding partners, which appear only in the JSON |
| 117 | RNAcentral | M14 | live API + snapshot | `rnacentral` / `rnacentral` | allowed | RNAcentral has a documented REST API (OpenAPI) and release-pinned FTP id-mapping files (release 27). The snapshot loads only the small URS mapping tables (miRBase, HGNC, TarBase, LncBase, IntAct); the 2.66 GB full mapping is optional. Target and interaction pairs exist only per URS through the API, so they are served live by the connector, which keeps to the 5 s crawl delay. |
| 118 | miRTarBase | M2, M14 | snapshot | – / `mirtarbase` | unknown | The MTI CSVs are public downloads with no API. The default file is the 3.3 MB strong-evidence set (reporter assay / western blot); the full 393 MB and human-only 337 MB MTI files are optional. Rows are 'known', with experiments in context.method. The support class (Functional MTI vs Functional MTI (Weak)) is kept in flags and never upgraded. 'Non-Functional MTI' records (an experiment found no reg |
| 119 | ChIP-Atlas | M13 | live API + snapshot | `chip_atlas` / `chip_atlas` | allowed | ChIP-Atlas documents an HTTP API (/openapi.yaml, /agents), which is wrapped at the 30 s crawl delay. The analysis, antigen and cell-type lists are small and are loaded as tables; the 359 MB experiment list is optional. tf_target rows come only from per-TF Target Genes tables, fetched on demand by fetch_target_genes, and only for TFs the analysis list marks '+'; nothing is bulk-downloaded. Rows are |
| 120 | ReMap 2022 | M13 | live API + snapshot | `remap` / `remap` | forbidden | ReMap's REST dataset endpoints answer on port 443 and are wrapped as a live connector. The snapshot keeps only small metadata: the per-target download index (regulator, GEO/ENCODE series, biotype, BED URLs) and the human biotype sheet. The BED catalogues (200 MB CRM, 1.46 GB non-redundant peaks) are optional raw analysis files. The non-commercial CC BY-NC licence is recorded. |
| 121 | Human Cell Atlas Data Portal (Azul) | M13 | live API | `hca_azul` / – | allowed | Azul is a documented, versioned OpenAPI service that answers anonymously for open projects. Its robots.txt disallows the API paths for crawlers, so the connector offers only one project or one file by UUID and never pages the index or generates manifests. There are no small bulk metadata files: data files run to many GB and managed-access data needs OAuth. So it is live-only. |
| 122 | HuBMAP (search, entity and portal APIs; bulk metadata exports) | M13 | live API + snapshot | `hubmap_search` / `hubmap` | allowed | HuBMAP documents its search API, entity API and the portal's .json and bulk TSV exports (llms.txt), and all of them answer anonymously. The connectors request named _source fields only, so no file lists or contact details come back. The small bulk exports plus the UBKG organ list are kept as a snapshot. The reader drops submitter e-mail and name columns and the field-description line. The extracto |
| 123 | Human Tumor Atlas Network | M9, M13 | restricted | – / – | unknown | HTAN publishes open and controlled tiers. Every programmatic route to open-tier files still needs a Synapse or cloud account, and the controlled tier needs dbGaP. No documented anonymous metadata endpoint answered, so HTAN is catalogued only and not wrapped. |
| 124 | 4D Nucleome Data Portal | M13 | live API + snapshot | `fourdn` / `fourdn` | allowed | The 4DN portal documents programmatic metadata access (search and per-item JSON), and the connector uses it with field lists that leave out lab and submitter contacts. Portal downloads need an account, but the same processed files are on the public AWS Open Data bucket. The two small union loop sets of the joint analysis (HFFc6, H1-hESC) are snapshotted as chromatin_loop rows. Multi-GB contact mat |
| 125 | EMBL-EBI BioSamples | M14 | live API | `biosamples` / – | unknown | BioSamples has a documented REST/HAL API that answers anonymously for sample look-ups, free-text search and exact attribute filters, for example a medicinal species as organism. It is wrapped live for look-ups only. A bulk snapshot would mean sweeping millions of records under a 10 s crawl delay. |
| 126 | iReceptor AIRR COVID-19 repository (AIRR Data Commons API) / VDJServer Community Data Portal (AIRR Data Commons API) | M9 | live API | `ireceptor_adc` / – | unknown | The AIRR Data Commons API is a documented community standard with no authentication, and the iReceptor COVID-19 repository answered unauthenticated JSON POST queries for repertoires, facets and rearrangements. Requests are restricted to selected fields and small pages. The login-gated Gateway that federates repositories is not used. VDJServer is a second AIRR Data Commons repository with the same  |
| 127 | BV-BRC Data API | M14 | live API | `bv_brc` / – | unknown | BV-BRC documents an RQL Data API that serves public bacterial and viral genome data without a token. It answered JSON for genomes, taxonomy, AMR phenotypes, specialty genes, PPI, epitopes and antibiotics. The RQL expression is written into the operation path, and requests stay small and per-entity. |
| 128 | MGnify API v2 | M6 | live API | `mgnify` / – | allowed | MGnify's documented v2 API answered studies, samples, analyses with taxonomic annotations, biomes and the MAG/isolate genome catalogues without a key. Only v2 is wrapped, because v1 is deprecated. The result files sit on ftp.ebi.ac.uk, whose robots.txt disallows agents, so they are not fetched. |
| 129 | NCI Imaging Data Commons (IDC) | M13, M9 | live API + snapshot | `idc` / `idc` | allowed | IDC has a documented public v3 REST API (collections, licence breakdowns, cohort counts, read-only SQL over its index) and publishes its full collection index as the MIT-licensed idc-index-data wheel on PyPI. The connector covers metadata queries. The snapshot reads the wheel's parquet members and lists each collection's cancer types. The per-series licences are recorded in each row's note, a flag |
| 130 | The Cancer Imaging Archive (TCIA) | M13 | live API | `tcia` / – | unknown | TCIA offers the documented NBIA v4 REST API (collections, patients, studies, series, modalities, body parts, series sizes) and a Collection Manager API that lists collections and download items, each with its data_license and access level. Both are wrapped as two metadata connectors (two hosts). Licences vary per collection, and some are non-commercial or controlled-access, so there is no snapshot |
| 131 | EMBL-EBI BioImage Archive | M13 | live API | `biostudies` / – | unknown | The BioImage Archive runs on the BioStudies backend on the same host (www.ebi.ac.uk), so it was added as operations on the existing 'biostudies' connector instead of a new source. bioimages_search is the undocumented search endpoint the archive's own pages use. study_info is the documented info endpoint and returns file counts and FTP/HTTP/Globus locations. Licences are per study and include some  |
| 132 | Image Data Resource (IDR) | M13 | live API + snapshot | `idr` / `idr` | allowed | IDR exposes the documented OMERO JSON API (screens, projects, study annotations) and a searcher API that looks up studies by gene or compound and exports a study's curated key-values as parquet or CSV. Both are wrapped as a metadata connector. The snapshot loads two openly licensed exports: MitoCheck (CC0), giving gene -> CMPO phenotype per siRNA and gene-level hits and tested non-hits, and idr002 |
| 133 | OpenNeuro | M13 | live API | `openneuro` / – | unknown | OpenNeuro has a public GraphQL API that returns dataset metadata, snapshots and file listings without a login, so it is wrapped as a metadata connector (dataset, datasets, snapshot_files). Licences are per dataset, mostly CC0 but some non-commercial or unstated, and the content is raw neuroimaging, so there is no snapshot. No image is downloaded. |

## Sources added from the integration guide (134–136)

First phase of [`tcm-data-integration-guide.md`](tcm-data-integration-guide.md): traditional knowledge (formula
composition), quality standards and sample identity. All three are manual imports
(`access=manual_import`), verified with synthetic records only — no real HKBU, HKCMMS or
reference-sequence data was downloaded, and none of the three sites was confirmed to offer
an open bulk API. Their licences are **unknown**: a manual dataset is not licensed for
reuse because its adapter runs, and `tcmdb check` reports the unknown class as a warning,
not permission. A person obtains the data under its terms and reviews it before import.

| # | Database | Modules | Access | Connector / dataset | Commercial | What was found |
| ---: | --- | --- | --- | --- | --- | --- |
| 134 | HKBU Chinese medicine formula database | – | manual import | – / `hkbu_formulas_manual` | unknown | Formula composition, doses, herb base species and citation, reviewed into one row per herb per formula version. Yields `formula_herb` rows with evidence `listed` (composition, not efficacy); rows without a stable id, a reference or a `verified` mark go to the unresolved queue. A formula with several sources or versions keeps several ids, and a processed herb (炮制品) is a separate id from the crude drug. Module assignment is left empty until the architecture document's M1–M14 legend is applied. |
| 135 | Hong Kong Chinese Materia Medica Standards (HKCMMS) | – | manual import | – / `hkcmms_manual` | unknown | Identity, safety and quality monographs: one row per monograph test item (identity, checks, assay limits, chemical marker), with the edition, method, value, unit and page. Query-only — a limit or an identification method is not a herb-ingredient relation and is not forced into one; different editions coexist. |
| 136 | Hong Kong Chinese Materia Medica reference DNA sequences | – | manual import | – / `hk_cmm_dna_manual` | unknown | Published reference sequences (kept as a raw FASTA file) and one row per sequence's specimen metadata (base species, marker, accession, voucher). Query-only: a barcode identifies a base species, it is not a drug ingredient, and authentication also needs a sequence quality check, a reference set and a discrimination method, which this dataset does not supply. This review did not reach the entry page (it timed out), so the exact bundle and its terms must be confirmed with the publisher. |

`bioagent.tools.tcmdb` adds three read-only lookups over these stores
(`hkbu_formula_lookup`, `hkcmms_standard_lookup`, `hk_cmm_dna_lookup`). Each answers with a
status rather than raising: `not_loaded` (no store where the tool looks; `store` names the
path), `incomplete_dataset` (a table, column or file the dataset needs is missing, such as
the reference FASTA without its specimen table), `no_matching_record`, `withheld` (records
matched, but none a person has verified, or none whose licence allows the call's data
use) and `ok`. None of them licenses a statement that a herb, formula or sample is
ineffective. Every record carries its dataset, the licence of the file it came from, that
licence's reuse class and the dataset's stated commercial use; that is not the licence of
the wrapper code, and the PSH bridge reports it as the tool's `data_license` (with the
wrapper's MIT as `code_license`). Table rows are returned only once verified
(`include_pending=True` shows the rest, marked). `commercial=True`, or the operator's
`BIOAGENT_DATA_USE=commercial`, withholds records whose licence does not allow commercial
use, which today is all of them.

Revised 2026-10-05 after an independent review of the first phase:

* the three files are read strictly, as the templates they are: exactly the template's
  columns, the header's number of fields on every line, a tab or line break inside a value
  only when quoted, valid UTF-8 (the lenient shared `tsv` reader had shifted a 15-value row
  under a 14-column header one column right and dropped its last value);
* a build runs in a staging file and replaces the store only when the relations are
  extracted, so a failed import leaves the previous store as it was (this holds for every
  dataset in the hub);
* the processing column is part of the listed material's name (黄芪 with 炙 is 炙黄芪),
  so a crude drug and its processed form stay two herbs through `consensus`, not only two
  ids in the store; 炮 and 煨 now count as processing prefixes (炮附子 is not 附子);
* `tcmdb check` warns on a query-only dataset's unknown licence, counts rows not yet
  verified, and requires the specimen metadata and the reference FASTA to describe the same
  sequences;
* the tools declare that they read `${tcmdb}`, and an isolated (PSH) call reads the same
  hub as a direct one; a hub outside the profile's roots (the data lake, the workspace) is
  refused with the path named, not reported as not loaded;
* `tcmdb template <key>` writes the header-only template (`--demo`: invented rows, all
  pending) and `tcmdb verify` builds, checks and looks up every manual dataset present
  (exit 0 all passed, 1 a failure, 3 nothing to check).

Added 2026-10-06, the research path for 134. `bioagent.sources.hkbu` reads the same
reviewed `formula_herb.tsv`, with the same strict reader, and turns each formula that can
be studied whole into a formula version of the herb layer:
`build_source_snapshots.py gold --hkbu-formulas …`, then
`run_network_pharmacology.py --formula hkbu:formula.<formula_id>`. The rules:

* a formula is exported only when every row is verified, complete (herb id, source row
  id, reference) and consistent, and every herb resolves once to a crude drug of the
  materia table; the others are listed with their reasons;
* a name in traditional characters (黃芩) is simplified character by character and
  resolved again, but only when the name as written does not resolve; the table is
  OpenCC's, cut to the characters the materia names use;
* each composition edge names the HKBU export as its primary knowledge source and the
  row's `source_row_id` as its record id. A hypothesis released on such a formula cites
  that row, and so does the path behind it;
* the licence is `LicenseRef-hkbu-formulas-unstated`. A commercial run now refuses a
  formula whose composition record does not allow commercial use, which covers HKBU and
  the formula table alike; before, the purpose check looked only at the skill's sources;
* a run on a formula edited after the snapshot was built is refused.

It has run on synthetic records only, and the research loop's question parser does not
read HKBU ids yet.

For later phases the guide lists TCMSSD, the classical-text library, WHO ICTRP/ChiCTR,
the 2025 Chinese Pharmacopoeia, HerbComb and GNDC. None is catalogued or wrapped yet:
each needs a permitted export (or a verified API) and the field and licence review the
guide describes before it can be added, so the catalogue is not to be padded with
unverified or unreachable entries.

## Re-checking

Each of these commands records what it found:

- `python scripts/verify_connectors.py --only <connector keys>` calls every operation of
  the named connectors and updates `data/connector_live_verification.csv`.
- `python -m bioagent.cli tcmdb fetch <key>` re-downloads a dataset's files and records
  their sizes and checksums in `raw/<key>/.downloads.json`.
- `python -m bioagent.cli tcmdb build <key>` rebuilds a store and records the SHA-256 of
  every file it loaded.

Sites change. The catalogue's `checked` date says when each source was last looked at.
