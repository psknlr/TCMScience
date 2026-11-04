# Release qualification, October 2026

This assessment covers branch `claude/keen-einstein-2l36a3` against the 15 acceptance
criteria of the Comprehensive Agent Validation, Database Reliability and Sandbox Research
Execution plan (§11). Every number here comes from a named run and its reports. Nothing is
counted as working because it is registered, imports, or passes a unit test.
[validation.md](validation.md) defines the statuses, tiers and reports.

**Verdict: not qualified as a whole.** 12 of the 15 criteria are met, 1 is partly met and
2 are not:

- **Core agent tests:** 8 of the 12 research scenarios pass on real data. Docking, ADMET
  and transcriptomics are verified only in their own CI jobs, not through the agent.
- **Governed skills:** they still read the seed corpus. A re-pin is proposed and waits on a
  maintainer.
- **Scientific provenance:** the conversational agent's prose cites its tool results, but
  is not checked sentence by sentence (partly met).

The real-data path itself is qualified. It downloads NPASS, CMAUP and LOTUS from their own
servers, verifies them, builds and queries them, and runs the governed network
pharmacology on them, ending in a reproducible, released research artifact.

## Runs this assessment rests on

| Run | Where | What it ran | Result |
|---|---|---|---|
| live-data #1 ([38125314518](https://github.com/psknlr/TCMScience/actions/runs/38125314518)) | GitHub-hosted runner, network on | tier 3 (real downloads, hub, snapshots), tier 4 (benchmark) | tier 3 passed. The benchmark's repeat found a defect: the second run did not replay the literature, so the evidence artifacts differed. Fixed in 03ada6c, with a test that fails without the fix. |
| live-data #3 ([38127338070](https://github.com/psknlr/TCMScience/actions/runs/38127338070)) | GitHub-hosted runner, network on | tiers 3 and 4, the twelve scenarios live on the hub those files built, the download record | success: every step |
| live-data #4 ([38130086806](https://github.com/psknlr/TCMScience/actions/runs/38130086806)) | GitHub-hosted runner, network on | the same, after the benchmark's wording and the scenarios were corrected (39ac75d) | success: every step, with run #3's results reproduced |
| live-data #5 ([38131465636](https://github.com/psknlr/TCMScience/actions/runs/38131465636)) | GitHub-hosted runner, network on | the same, on the final code (07f3a85) | success: every step, with run #4's results reproduced |
| ci #273, #274; studio #48, #49 | GitHub-hosted runners | unit tiers, the specialised engine jobs, the offline validation tier; runner, web and edge suites, site build, browser end-to-end tests | ci #273 failed in one job: the analysis job's new readiness step judged kallisto broken (below), fixed in 07f3a85. ci #274 (07f3a85): every job passed. studio #48 and #49: success. |
| local | development container (its egress proxy refuses the data hosts) | `tcmstudio validate`, `bioagent validate benchmark`, and the full suites while the changes were made | BioScience 2,585 passed (network tests deselected), Studio runner 399 passed, 0 failed; each later change's own tests passed before it was pushed |

(live-data #2 was cancelled by a later push.)

## Acceptance criteria (§11)

| Requirement | Acceptance condition | Status | Evidence |
|---|---|---|---|
| Agent inventory | 100% of registered agents and tools enumerated | **Met** | `agent_inventory.json`: 25 core tools, 649 catalog entries (150 native, 11 skill, 444 connector, 5 clinic, 15 tcmdb, 4 study, 11 job, 9 system), 11 job kinds, 11 governed skills, 79 hub datasets, 137 source cards. `test_validate.py` asserts the counts equal the dispatcher's own catalog. |
| Functional testing | Every capability has an executable test or an explicit unsupported/unverified classification | **Met** | All 677 records in `tool_validation_report.json` are classified, with 0 FAIL. 80 pass against references outside the code (74 native tools, 3 core tools, 3 tool chains). 9 are BLOCKED: 5 by a missing engine, 3 by a hub store not built offline, and 1 by the job service the validator does not run. The rest are UNVERIFIED with a reason: mostly no reference answer offline, or a connector not called offline (444). |
| Core agent tests | 100% of mandatory core workflows pass | **Not met** | The twelve scenarios, live on the real hub (runs #3 to #5): 8 PASS, 3 BLOCKED, 1 UNVERIFIED. Docking (7) and ADMET (8) are BLOCKED because the live runner does not install their engines. Transcriptomics (9) is BLOCKED because its packages are not installed there. Their reference runs pass in CI's molecular-modelling and analysis jobs, but not through the agent. Mechanism hypothesis (4) is UNVERIFIED: the seed corpus records no targets, and hypotheses on measured targets come from the benchmark. |
| NPASS acquisition | Real upstream download and integrity verification pass | **Met** | 6 of 6 files from bidd.group, each matching its reviewed SHA-256, in every live run (#1, #3, #4, #5). Resume with a byte-range request shown on bidd.group. |
| CMAUP acquisition | Real upstream download and integrity verification pass | **Met** | 5 of 5 files from bidd.group, each matching its reviewed SHA-256. |
| LOTUS acquisition | Real upstream download and integrity verification pass | **Met** | `260413_frozen.csv.gz` from Zenodo, matching its reviewed SHA-256, decompressed and parsed. Resume shown on Zenodo. |
| Database construction | Authentic datasets successfully parsed, indexed, and queried | **Met** | Tier 3: the NPASS, CMAUP and LOTUS hub datasets built from those files, queried (berberine in *Coptis chinensis* with its measured targets) and rebuilt with identical digests. The three sources agree on berberine's InChIKey (scenario 12). 418 MB from 5 hosts. |
| Sandbox networking | Authorized real-source downloads demonstrated | **Met** on a GitHub-hosted runner | The files came from their own servers through the snapshot pipeline's downloader, the code Studio's `research.snapshot` job runs (`bioagent snapshots build`). The hub datasets were built through the import and build that the `tcmdb.build` job runs. The `tcmdb.fetch` job has its own download path: its checksums and resume are tested offline, but it was not run live. In this development container the proxy refuses those hosts; the validators report BLOCKED, never online. |
| Sandbox persistence | Downloaded data survive supported workflow restarts | **Met** | The benchmark, a separate process, reused the files the data step had downloaded, re-verifying each checksum first ("reused, verified by checksum"). Snapshots are content-addressed and checked against their ledger on load. The runner's job service reattaches running jobs and requeues queued ones after a restart (`test_jobs.py`). |
| Downstream analysis | Real downloaded data successfully drive scientific computation | **Met** | Six research snapshots built from the real files in 117 s, with the gold standard of marker compounds reproduced. The governed analysis then ran on them: 3,230 constituents, 589 human proteins measured at or below 10 µM, 1,123 pathways tested with BH and a 1,000-draw degree-matched null. Three of the files lack a reviewed checksum, so the stages that read them stay UNVERIFIED (see the limitations below). |
| Governed skills | Production-data integration validated and appropriately repinned | **Not met** | The four pinned TCM skills still read the labelled seed corpus. The re-pin is a [proposal](proposals/repin-tcm-skills.md) awaiting a maintainer's `PromotionDecision`; no pin was changed (`check_lockfile.py`: 4 pinned and matched). |
| Scientific provenance | All generated claims traceable to source data or explicitly identified inference | **Partly met** | Every benchmark target rests on a measured activity edge in a verified snapshot; a target without one fails the stage (tested). A hypothesis cites its edges and is capped at `mechanism_hypothesis`. Seed-corpus answers say so. The conversational agent's prose cites tool results (`[E#]`), but nothing checks each sentence against what it cites. |
| Regression protection | Previously resolved PR #52 issues remain fixed | **Met** | The suites holding PR #52's tests pass on the final code: ci #274 and studio #49 on 07f3a85, and the local suites. |
| Reliability | Interrupted workflows recover correctly where checkpointing is supported | **Met** | Live: an interrupted download resumed by byte range on 3 hosts. Tested: the research loop resumes after a failed rebuttal without re-analysing and recomputes a damaged checkpoint; PSH jobs resume collection after a restart; Studio jobs reattach; ADMET model builds resume. A download stays a `.part` file until it is verified, and only then gets its final name. |
| CI transparency | Blocked, skipped, or unverified tests are never reported as successful executions | **Met** | A record passes only when every applicable check passed. `--strict` fails the live job on BLOCKED, and its verdict needs all three live steps to succeed. Each run's summary lists the records that did not pass with their reasons (the first 80 on the page, all of them in the uploaded reports). A green live job therefore means nothing failed or was blocked, not that everything verified: runs #3 to #5 were green with 5 benchmark stages UNVERIFIED, and their summaries say so. |

## The benchmark: Guizhi Decoction and palpitations

> Investigate the potential biological mechanisms through which Guizhi Decoction may
> influence palpitations, using traditional medical literature, pharmacological
> databases, experimental evidence, and computational analysis.

Runs #3 to #5, on real downloads, with the literature queried live and the deterministic
stages run twice in each. The three runs agree; the wording below is from runs #4 and #5,
after the corrections. Acceptance: **explained**. Seven stages pass; five end UNVERIFIED
for one stated reason: they read a file without a reviewed checksum. No stage failed.

| # | Stage | Status | What it found |
|---|---|---|---|
| 1 | formula_version | PASS | 桂枝汤《伤寒论》 is workbook row 23205 (`tcm:formula.fx559d1e07f05c`). The name alone matches 30 versions with 30 different compositions, so the book is what selects one. It matches the curated 伤寒论 formula drug for drug and dose for dose. |
| 2 | herbs | PASS | 桂枝、白芍、甘草、生姜、大枣, each resolved to a crude drug with an NCBI-verified source species |
| 3 | compounds | PASS | 3,230 constituents (桂枝 434, 白芍 425, 甘草 1,166, 生姜 1,228, 大枣 431); the 12 NPASS, CMAUP and LOTUS files match their reviewed checksums |
| 4 | targets | PASS | 589 human proteins measured at or below 10 µM, each resting on a measured activity edge in a verified snapshot |
| 5 | measured_vs_predicted | UNVERIFIED | every edge class counted as measured or predicted; every target has a measured edge; STRING's edges are predictions and feed only the descriptive network topology |
| 6 | pathways | UNVERIFIED | 15 of the 18 pre-registered pathways have proteins annotated to them. Cardiac conduction, Potassium Channels and Muscle contraction have no rows of their own: the file lists lowest-level pathways. 6 pre-registered pathways contain measured targets, among them Phase 0 - rapid depolarisation (CaM kinase II beta, a voltage-gated L-type calcium channel, …) |
| 7 | enrichment | UNVERIFIED | 1,123 pathways tested against the assayed background (558 proteins). None passes both BH and the permutation null. Rebuttals: none survived, none refuted. 366 of the 558 assayed proteins are potent hits, so no pathway can exceed a 1.52-fold enrichment. |
| 8 | literature | PASS | Europe PMC: 274 hits for Guizhi Decoction and palpitations, 31 for Guizhi Gancao Decoction, 1,037 for Guizhi and cardiac terms (25 records kept from each). ClinicalTrials.gov: 25 records, and more may exist. A hit is not evidence. |
| 9 | evidence | UNVERIFIED | the classical record: 桂枝汤《伤寒论》 records no palpitation (悸); 桂枝甘草汤《伤寒论》 records 心下悸. Each evidence type is graded with the claims it can license, and contradictions are listed. |
| 10 | hypotheses | UNVERIFIED | no pathway survived analysis and rebuttal, so no mechanism hypothesis is licensed. This is an absence of evidence, not evidence that any pathway is uninvolved. The artifact passed its release validation. |
| 11 | formula_distinction | PASS | 心下悸 is recorded for 桂枝甘草汤, not 桂枝汤. 桂枝汤 contains both its herbs plus 白芍、生姜、大枣, and gives 桂枝 3两 against 4两. Carrying the record across is marked as reasoning across formulas. |
| 12 | artifacts | PASS | 11 stage artifacts written and hashed; a second run in a fresh state directory reproduced the 10 deterministic ones byte for byte |

What the benchmark establishes:

- On these databases, Guizhi Decoction's constituents have potent measured activity on 589
  human proteins.
- None of the 1,123 Reactome pathways tested is over-represented among them, at the
  stated thresholds, relative to what was assayed.
- The classical palpitation record (心下悸) belongs to a related, smaller formula.

It does not show that the formula acts, or fails to act, on palpitations. The agent
reached that end without inventing a target relationship or calling a speculative pathway
a mechanism, which is the plan's acceptance condition.

## What is verified

- **Real data, end to end.** 17 research files (418 MB) were downloaded from bidd.group,
  Zenodo, STRING, Reactome and UniProt. 14 match reviewed checksums. They were built into
  hub datasets and research snapshots, queried, rebuilt and analysed under governance. The
  committed record, `src/bioagent/data/live_download_verification.json`, holds each file's
  result, date, run and SHA-256, and the database readiness report reads it.
- **The research scenarios, live (runs #3 to #5).**
  - Formula provenance: 桂枝汤 against 《伤寒论》第12条.
  - Natural-product identification: berberine in *Coptis chinensis*, checked against
    PubChem CID 2353.
  - Compound–target retrieval: 88 records, every one a measured activity citing its
    publication.
  - Enrichment and the hypergeometric test, against SciPy.
  - Protein sequence: angiotensin II, against the standard residue table.
  - Clinical literature: Europe PMC and ClinicalTrials.gov records with their identifiers.
  - Evidence synthesis: attribution licensed, efficacy not, and the four 十八反 partners of
    甘草 named.
  - Cross-database integration: NPASS, CMAUP and LOTUS meet on one InChIKey.
- **Scientific packages where they are installed.** In CI's molecular-modelling job (ci #273)
  the cheminformatics, ADMET and data-file groups import at their locked versions and
  compute known answers. Docking's calculation is that job's redocking test (39 tests
  passed). In the analysis job the same step found a defect in the check itself: it asked
  kallisto for `--version`, which kallisto does not support (fixed in 07f3a85). With the fix
  (ci #274), every package in the transcriptomics group imports and every binary answers.
  The group stays UNVERIFIED because pydeseq2's fit is left to that job's pipeline tests
  (145 passed); the statistics and data-file groups pass there.
- **Every capability classified.** 677 records with 0 FAIL. Each report names its
  environment and the command that reproduces it.
- **The agent under failure.** Outcome classes distinguish an unavailable tool or dataset,
  no record, a negative finding and inconclusive evidence. After three failures, or one a
  retry cannot change, the tool is not called again in that turn.
- **Network status from real requests.** The sandbox report probes hosts with real
  requests, and a switch alone never makes a sandbox "online". Sources behind logins,
  data-use agreements, CAPTCHAs or dead hosts are BLOCKED with their reason, and none of
  those controls is bypassed (39 sources).

## What is not, and why

- **Three files have no reviewed checksum**, so every stage that reads them is UNVERIFIED.
  Their observed values are recorded for a maintainer to review and pin; nothing here
  pinned them.

  | File | Bytes | Observed SHA-256 (runs #3 to #5) | Note |
  |---|---|---|---|
  | `9606.protein.aliases.v12.0.txt.gz` (STRING) | 19,777,800 | `b65f730b993ed0c1bd72edf4565d3d425db42861101b29699704810e8f125680` | frozen release v12.0: a checksum will hold (added on 2026-10-28: since pinned to this size and SHA-256 in `bioagent/sources/pipeline.py`, after `live-data` #31, run 40307982154 on 2026-10-26, read the same) |
  | `UniProt2Reactome.txt` (Reactome) | 43,041,375 | `e34fe56a5129d88d8cd67300c279d7e81fa1d3bf86fa0577cf91032c326eaecc` | from `download/current/`, which changes with each release: pin a versioned copy, not this URL |
  | `uniprot_human_reviewed_accessions.txt` (UniProt) | 144,669 | `7a153cbbf75f350b4bdc711d68bd3adbc0f4c5e9244aefb4a2fb8f8a7f24d5b7` | a live query that changes with each UniProt release: pin a release |

- **Governed skills on production data:** a [proposal](proposals/repin-tcm-skills.md) that
  needs a maintainer's review and `PromotionDecision`. Until then the skills answer from
  the labelled seed corpus. Studio routes around its limits without changing them (the
  complete workbook, the hub, the 十八反/十九畏 rule table, the research loop).
- **Docking, ADMET and transcriptomics through the agent.** Their engines run for real in
  CI's molecular-modelling, analysis and end-to-end jobs, which now also write the
  dependency readiness report where the packages are installed. The scenario suite does
  not run them. They run as jobs a person approves, and the validator approves nothing on
  a person's behalf. The next step is an explicit, recorded operator approval for
  validation jobs, plus the engines on the live runner. Until then these scenarios are
  BLOCKED where the engine is missing and UNVERIFIED where it is installed.
- **erratum.0001**: 桂枝甘草汤《伤寒论》 row 23098 reads 「草2两」 for 甘草. The correction is
  proposed, applied and labelled in every answer that uses it, and awaits review.
- **The enrichment test has little power on this background.** Databases record active
  results far more often than inactive ones, so most assayed proteins are potent hits,
  which caps any pathway's fold enrichment. Here 366 of the 558 assayed proteins in Reactome's
  lowest-level mapping are potent hits, so no pathway can exceed 1.52-fold. The other 223 of
  the 589 potent targets have no lowest-level annotation and do not enter the test.
- **Three pre-registered pathways** are parents (Cardiac conduction, Potassium Channels,
  Muscle contraction), so the lowest-level mapping cannot contain them. The definition was
  left unchanged after seeing the data. A later version could pre-register the
  hierarchy's lowest levels, or use Reactome's all-levels mapping with a correction for
  nested tests.
- **Database coverage (runs #4 and #5):** of 137 catalogued sources, 14 pass every check that
  applies, 39 are BLOCKED by access rules or dead hosts, 1 is SKIPPED, and 83 are
  UNVERIFIED (catalogued but not integrated, or never downloaded by a recorded run).
- **The conversational agent's prose** is held to its tool results by rules and citations,
  not by a sentence-level check.
- **This development container** cannot reach the data hosts. Live validation runs only on
  GitHub-hosted runners, through `live-data.yml`: weekly, on demand, and on pushes that
  change acquisition, analysis or validation.

## Deployment readiness

- **Ready**
  - The Studio runner and web app, with CI green (studio #49): runner, web and edge
    suites, the site build, and browser end-to-end tests.
  - The data hub's verified acquisition of NPASS, CMAUP and LOTUS.
  - The snapshot pipeline and the governed research loop on those snapshots.
  - The validation commands and both CI tiers.
- **Ready, with stated limits**
  - Research answers about formulas and natural products. The three unpinned files keep
    the pathway and enrichment stages UNVERIFIED.
  - Seed-corpus skills, labelled as such.
  - Literature retrieval, which returns hits, not appraised evidence.
- **Not ready**
  - Governed TCM skills on production data, until the re-pin is reviewed.
  - Docking, ADMET and transcriptomics as agent workflows: the engines are verified, the
    agent path is not.
- **For the Definition of Done:** a new user can do the following without repairing
  anything.
  1. Ask about a formula.
  2. Approve the snapshot job, which downloads and verifies NPASS, CMAUP, LOTUS, STRING,
     Reactome and UniProt (418 MB) on a runner with web access.
  3. Approve the research job.
  4. Receive a released, reproducible, evidence-traced artifact that says what the data
     license and what they do not.

  For docking, ADMET and omics, the user must first approve `env.provision` for the
  engine group, and that path has not been validated end to end.
