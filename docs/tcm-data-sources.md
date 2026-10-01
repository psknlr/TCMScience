# TCM data sources: what each database offers and how the harness reaches it

The architecture document `tcm_agent_architecture.docx` lists 66 databases in 14 modules.
On 2026-10-01 every one of them was checked from the harness. The check covered:
- the home page and whether it is reachable;
- any API, tested with one documented request;
- download links, tested with a HEAD or ranged request to confirm they return files;
- logins, CAPTCHAs, fees and email-only job forms;
- the licence or terms of use;
- `robots.txt`.

Each database was then wrapped in the way its site allows, and only in that way. All of
it is reachable through one interface, `bioagent.tcmdb.TCMDataHub`, and its command line
`bioagent.cli tcmdb`.

## The result in numbers

| Access | Sources | What the hub does |
| --- | ---: | --- |
| live API | 4 | Calls a connector through the governed runtime |
| live API + snapshot | 9 | Both live calls and a local copy |
| snapshot | 12 | Downloads the files once, loads them into SQLite and extracts relations |
| manual import | 7 | A person exports the files; the same loader reads them |
| restricted | 16 | Nothing to call. The card says what a person would need (an account, a fee, a request to the authors) |
| unreachable | 18 | Nothing to call. The card records what was observed |

Live connectors and downloads together reach 25 of the 66 sources. 7 more are reachable
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
DDID, TTD, nSIDES and TCMBank. Some state "free for academic use" (BATMAN-TCM,
NPASS/CMAUP). Clear licences:

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


## Re-checking

Each of these commands records what it found:

- `python scripts/verify_connectors.py --only <connector keys>` calls every operation of
  the named connectors and updates `data/connector_live_verification.csv`.
- `python -m bioagent.cli tcmdb fetch <key>` re-downloads a dataset's files and records
  their sizes and checksums in `raw/<key>/.downloads.json`.
- `python -m bioagent.cli tcmdb build <key>` rebuilds a store and records the SHA-256 of
  every file it loaded.

Sites change. The catalogue's `checked` date says when each source was last looked at.
