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
| live API + snapshot | 7 | Both live calls and a local copy |
| snapshot | 14 | Downloads the files once, loads them into SQLite and extracts relations |
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
object_type, object_id, object_name, evidence, score, reference, note
```

The source tables stay in the same SQLite file next to the relations, so a relation can
always be compared with the row it came from.

- **ids**: a global identifier where the source gives one (`pubchem:`, `inchikey:`,
  `symbol:`, `ncbigene:`, `uniprot:`, `ensembl:`, `umls:`, `nct:`, `pmid:`, `rxnorm:`,
  `meddra:`); otherwise `<source>:<local id>`.
- **names**: every name the source gives, joined by ` | `. A query matches any one of
  them exactly, ignoring case. `contains=True` switches to substring matching.
- **evidence**: how the source knows the relation, never upgraded:
  - `known`: measured or curated;
  - `predicted`: a model's output, with its score;
  - `aggregated`: integrated from other databases without per-row provenance;
  - `listed`: a composition list;
  - `reported`: a document about the subject;
  - `signal`: a disproportionality signal from spontaneous reports.

  Only `known` and `reported` rows point at observations; the others support a
  hypothesis at most. The hub only retrieves and labels. It makes no claims, and its
  rows are not yet wired into the research loop. A claim built on them must go through
  the snapshot pipeline (`bioagent.sources`), where `tcm.EvidenceTier` and the artifact
  validator apply.
- **score / reference / note**:
  - `score` is the source's own number (prediction score, STITCH or DisGeNET score, PRR);
  - `reference` is a citation;
  - `note` holds the qualifiers (dose, clinical status, mechanism, effect direction).

## Decisions that are not obvious

- **Undocumented endpoints.** TCMBank, ITCM and TTD answer unauthenticated GET requests
  that their own pages make. These are wrapped, marked *undocumented* on every
  operation, rate-limited to one request per second, and backed by the downloaded files,
  which stay the reference copy.

  The undocumented **POST** APIs of HERB (`/chedi/api/`) and SymMap
  (`/related_components/`) are not used. A read-only GET that a page makes is one thing;
  driving a site's private write-shaped interface is another.

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

- **TCMIO targets are left as TCMIO ids.** The ingredient–target file refers to targets
  by a number, and the target table has no id column. Mapping the numbers by row order
  might be right, but nothing in the download confirms it, so the relation keeps
  `tcmio:target.<n>` and no gene name.

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
| 13 | HERB 2.0 | M3, M10 | snapshot | `herb2` | Static TSV files downloadable without login; relation pairs need undocumented POST API. |
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
| 27 | SymMap v2 | M5 | snapshot | `symmap2` | Entity xlsx files are freely downloadable; associations need the undocumented POST endpoint or HTML. |
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
