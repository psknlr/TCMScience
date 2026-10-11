# Release qualification, October 2026

This assessment covers branch `claude/keen-einstein-2l36a3` against the 15 acceptance
criteria of the Comprehensive Agent Validation, Database Reliability and Sandbox Research
Execution plan (§11). Every number here comes from a named run and its reports. Nothing is
counted as working because it is registered, imports, or passes a unit test.
[validation.md](validation.md) defines the statuses, tiers and reports.

**Verdict: not qualified as a whole.** 12 of the 15 criteria are met, 2 are partly met and
1 is not:

- **Core agent tests (partly met):** all twelve research scenarios ran in one live run
  under the agent's own scheduling (live-data #10). Every call went to tcmstudio's runner
  over HTTP, and every job was followed to its end. Eleven pass. Mechanism hypothesis (4)
  ran the research loop as two runner jobs and passed every check but one: three of its
  input files have no reviewed checksum, so it is UNVERIFIED.
- **Governed skills (not met):** they still read the seed corpus. A re-pin is proposed and
  waits on a maintainer, and so does pinning those three files.
- **Scientific provenance (partly met):** the conversational agent's prose cites its tool
  results, but is not checked sentence by sentence.

The real-data path is qualified:
- NPASS, CMAUP and LOTUS are downloaded from their own servers, verified, built and
  queried.
- The governed network pharmacology runs on them to a reproducible, released research
  artifact, now also as the runner's own jobs.
- Docking, ADMET and the omics pipelines run as approved runner jobs and meet references
  outside the code.
- Beyond the research files, 73 of the 74 hub datasets a program can fetch were fetched,
  verified, built and queried live, and 440 of the 444 connector operations answered.

Sources behind logins, data-use agreements or paywalls are not entered on anyone's behalf.
They are BLOCKED, with what a person has to do.

## Runs this assessment rests on

| Run | Where | What it ran | Result |
|---|---|---|---|
| live-data #1 ([38125314518](https://github.com/psknlr/TCMScience/actions/runs/38125314518)) | GitHub-hosted runner, network on | tier 3 (real downloads, hub, snapshots), tier 4 (benchmark) | tier 3 passed. The benchmark's repeat found a defect: the second run did not replay the literature, so the evidence artifacts differed. Fixed in 03ada6c, with a test that fails without the fix. |
| live-data #3 ([38127338070](https://github.com/psknlr/TCMScience/actions/runs/38127338070)) | GitHub-hosted runner, network on | tiers 3 and 4, the twelve scenarios live on the hub those files built, the download record | success: every step |
| live-data #4 ([38130086806](https://github.com/psknlr/TCMScience/actions/runs/38130086806)) | GitHub-hosted runner, network on | the same, after the benchmark's wording and the scenarios were corrected (39ac75d) | success: every step, with run #3's results reproduced |
| live-data #5 ([38131465636](https://github.com/psknlr/TCMScience/actions/runs/38131465636)) | GitHub-hosted runner, network on | the same, on 07f3a85 | success: every step, with run #4's results reproduced |
| live-data #6, #7, #9 ([38145218261](https://github.com/psknlr/TCMScience/actions/runs/38145218261), [38157405936](https://github.com/psknlr/TCMScience/actions/runs/38157405936), [38164474807](https://github.com/psknlr/TCMScience/actions/runs/38164474807)) | GitHub-hosted runner, network on | the same (#6 on `main`, after PR #53's merge) | success: every step |
| live-data #10 ([38166939895](https://github.com/psknlr/TCMScience/actions/runs/38166939895)) | GitHub-hosted runner, network on, engines installed | tiers 3 and 4, then all twelve scenarios through tcmstudio's runner, every call over HTTP, each job followed to its end | success. The benchmark: 7 stages PASS and 5 UNVERIFIED, as in runs #3 to #5. The scenarios: 11 PASS, 1 UNVERIFIED, 0 FAIL, 0 BLOCKED |
| agent-engines #1 ([38163083038](https://github.com/psknlr/TCMScience/actions/runs/38163083038)) | GitHub-hosted runner, engines installed | scenarios 7–9 as approved jobs through tcmstudio's runner, live | docking and transcriptomics passed. ADMET built both models and failed only its split check, which compared TDC's lower-case metric names (fixed in 68baa96) |
| agent-engines #2, #3 ([38164474760](https://github.com/psknlr/TCMScience/actions/runs/38164474760), [38166939882](https://github.com/psknlr/TCMScience/actions/runs/38166939882)) | the same | the same | every check of all three scenarios passed (`--require-pass`) |
| sources-live #1 ([38157405923](https://github.com/psknlr/TCMScience/actions/runs/38157405923)) | 74 GitHub-hosted runners, one per hub dataset | every downloadable and per-entity hub dataset fetched, verified, built, rebuilt and queried | 3 PASS, 52 UNVERIFIED, 12 FAIL, 5 SKIPPED, 2 BLOCKED. Every failure was ours and was fixed at the root (7741167, 137d7bb) |
| sources-live #2 ([38163083017](https://github.com/psknlr/TCMScience/actions/runs/38163083017)) | the same | the same, after those fixes | 5 PASS, 68 UNVERIFIED (each only for a missing reviewed checksum), 1 BLOCKED (miRTarBase's TLS handshake), 0 FAIL |
| sources-live #3 ([38164474741](https://github.com/psknlr/TCMScience/actions/runs/38164474741)) | the same, and two more runners | the datasets; every connector operation; the sources catalogued unreachable, asked again | datasets as in #2. 438 of 444 connector operations answered and 6 timed out (BLOCKED). 6 of 17 unreachable sources' front ends answered |
| sources-live #4 ([38166939954](https://github.com/psknlr/TCMScience/actions/runs/38166939954)) | the same | the same, with 88 reviewed checksums pinned (c7bcfa1) | 39 dataset jobs ran: 18 PASS, 21 UNVERIFIED, 0 FAIL. All 13 pinned datasets among them pass. GitHub gave the other 35 dataset jobs and the fold job no runner, so they ran nothing and count as not run. 440 of 444 connector operations answered. 7 of 17 unreachable sources' front ends answered |
| ci #273, #274; studio #48, #49 | GitHub-hosted runners | unit tiers, the specialised engine jobs, the offline validation tier; runner, web and edge suites, site build, browser end-to-end tests | ci #273 failed in one job: the analysis job's new readiness step judged kallisto broken, fixed in 07f3a85. ci #274: every job passed. studio #48 and #49: success. |
| ci #278–#281; studio #52–#55 | GitHub-hosted runners | the same, on this round's code | ci #278–#280 and studio #52–#55: every job passed. ci #281 failed in the two BioScience unit tiers on one test, and 2,702 other tests passed. Pinning WFO's files had made the Zenodo pacing test's stand-in bodies fail their checksum. Fixed in 3c81807, which no CI run had reached when this was written (see below). |
| local | development container (its egress proxy refuses the data hosts) | `tcmstudio validate` with the provision jobs from a fresh environment, `bioagent validate benchmark`, and the full suites | on the final code: BioScience unit tier 2,677 passed, 131 skipped, 0 failed (network tests deselected); Studio runner 429 passed, 0 failed |

(live-data #2 and #8 were cancelled by later pushes.)

## Acceptance criteria (§11)

| Requirement | Acceptance condition | Status | Evidence |
|---|---|---|---|
| Agent inventory | 100% of registered agents and tools enumerated | **Met** | `agent_inventory.json`: 25 core tools, 649 catalog entries (150 native, 11 skill, 444 connector, 5 clinic, 15 tcmdb, 4 study, 11 job, 9 system), 11 job kinds, 11 governed skills, 79 hub datasets, 137 source cards. `test_validate.py` asserts the counts equal the dispatcher's own catalog. |
| Functional testing | Every capability has an executable test or an explicit unsupported/unverified classification | **Met** | All 677 records in `tool_validation_report.json` are classified, with 0 FAIL. 80 pass against references outside the code (74 native tools, 3 core tools, 3 tool chains). 9 are BLOCKED: 5 by a missing engine, 3 by a hub store not built offline, and 1 by the job service the offline validator does not run. The rest are UNVERIFIED with a reason: mostly no reference answer offline, or a connector not called offline (444). The live tiers then run what the offline tier cannot: the jobs, the connectors and the downloads. |
| Core agent tests | 100% of mandatory core workflows pass | **Partly met** | All twelve scenarios ran in one live run through tcmstudio's runner (live-data #10): 11 PASS, 1 UNVERIFIED, 0 FAIL, 0 BLOCKED. Docking (7), ADMET (8) and transcriptomics (9) ran as approved runner jobs and met every reference, as in agent-engines #2 and #3. Mechanism hypothesis (4) ran the research loop as two runner jobs. It passed every check except `inputs_verified`: three of its input files have no reviewed checksum, and pinning them waits on the maintainer re-pin. The condition is 100 %, so it is not met in full. |
| NPASS acquisition | Real upstream download and integrity verification pass | **Met** | 6 of 6 files from bidd.group, each matching its reviewed SHA-256, in every live run (live-data #1, #3–#7, #9, #10; sources-live #1–#4). In live-data #10 the runner's own `research.snapshot` job downloaded and verified them too. Resume with a byte-range request shown on bidd.group. |
| CMAUP acquisition | Real upstream download and integrity verification pass | **Met** | 5 of 5 files from bidd.group, each matching its reviewed SHA-256, in the same runs. |
| LOTUS acquisition | Real upstream download and integrity verification pass | **Met** | `260413_frozen.csv.gz` from Zenodo, matching its reviewed SHA-256, decompressed and parsed, in the same runs. Resume shown on Zenodo. |
| Database construction | Authentic datasets successfully parsed, indexed, and queried | **Met** | Tier 3: the NPASS, CMAUP and LOTUS hub datasets built from those files, queried (berberine in *Coptis chinensis* with its measured targets) and rebuilt with identical digests. The three sources agree on berberine's InChIKey (scenario 12). Beyond them, every downloadable hub dataset except miRTarBase was built from its real files, rebuilt to the same relations and queried for every relation kind it declares (sources-live #2 and #3: 73 datasets, 4.21 GB). |
| Sandbox networking | Authorized real-source downloads demonstrated | **Met** on GitHub-hosted runners | Real-source downloads ran through four paths: the snapshot pipeline's downloader (tier 3); Studio's own `research.snapshot` job, run by tcmstudio's runner on the operator's approval (live-data #10); the hub's fetch, the code Studio's `tcmdb.fetch` job runs (sources-live, every dataset); and the real HTTP backend (every connector operation). In this development container the proxy refuses those hosts, and the validators report BLOCKED, never online. |
| Sandbox persistence | Downloaded data survive supported workflow restarts | **Met** | The benchmark, a separate process, reused the files the data step had downloaded, re-verifying each checksum first ("reused, verified by checksum"). Snapshots are content-addressed and checked against their ledger on load. The runner's job service reattaches running jobs and requeues queued ones after a restart (`test_jobs.py`). |
| Downstream analysis | Real downloaded data successfully drive scientific computation | **Met** | Six research snapshots were built from the real files in 108 s (live-data #10), reproducing the gold standard of marker compounds. The governed analysis then ran on them: 3,230 constituents; 589 human proteins measured at or below 10 µM; 1,123 pathways tested with BH and a 1,000-draw degree-matched null. In live-data #10 the runner's own `research.snapshot` and `research.run` jobs did the same. Docking, ADMET and the omics pipelines computed on real inputs: PDB 3PTB, TDC's archive and 10x Genomics' PBMC 1k. Three of the research files lack a reviewed checksum, so the stages that read them stay UNVERIFIED (see the limitations below). |
| Governed skills | Production-data integration validated and appropriately repinned | **Not met** | The four pinned TCM skills still read the labelled seed corpus. The re-pin is a [proposal](proposals/repin-tcm-skills.md) awaiting a maintainer's `PromotionDecision`; it now also carries the research files' pins. No pin was changed (`check_lockfile.py`: 4 pinned and matched). |
| Scientific provenance | All generated claims traceable to source data or explicitly identified inference | **Partly met** | Every benchmark target rests on a measured activity edge in a verified snapshot; a target without one fails the stage (tested). A hypothesis cites its edges and is capped at `mechanism_hypothesis`. In live-data #10 the runner's research job claimed no pathway because none survived, and invented none. Seed-corpus answers say so. The conversational agent's prose cites tool results (`[E#]`), but nothing checks each sentence against what it cites. |
| Regression protection | Previously resolved PR #52 issues remain fixed | **Met** | The suites holding PR #52's tests passed in ci #278–#280 and studio #52–#55. ci #281's one failure was a test this round's pins broke, not a PR #52 issue. Its fix (3c81807) passes the full local unit tier. |
| Reliability | Interrupted workflows recover correctly where checkpointing is supported | **Met** | Live: an interrupted download resumed by byte range on 3 hosts, and a body EMA cut off mid-transfer resumed (sources-live). Tested: the research loop resumes after a failed rebuttal without re-analysing, and recomputes a damaged checkpoint; PSH jobs resume collection after a restart; Studio jobs reattach; ADMET model builds resume. A download stays a `.part` file until it is verified. A race between the runner's threads that could hide every governed skill is fixed, with a fresh-interpreter test. |
| CI transparency | Blocked, skipped, or unverified tests are never reported as successful executions | **Met** | A record passes only when every applicable check passed. A dataset, connector or job whose host timed out or refused is BLOCKED and listed with its reason, never a pass; only a wrong answer fails those jobs. `--strict` fails the live-data job on BLOCKED. `--require-pass` fails agent-engines on anything short of PASS, with one named exception: a group whose calculation is deferred by design to the engine's own job. Each run's summary lists every record that did not pass. Dataset jobs GitHub never started (sources-live #4) are counted as not run, never as passed. |

## The agent's own scheduling, end to end

With the operator's approval (`--approve-jobs`), `tcmstudio validate` starts tcmstudio's
runner in a temporary home and sends every scenario call to it over HTTP, as the Studio
page sends it:
- `POST /api/call` with the pairing token and the approvals the page's router would ask
  for;
- files through `POST /api/uploads`;
- a job submitted (`run_pipeline`, `network_pharmacology_run`, `call_tool`) and followed
  with `job_status` to its end.

The runner's job service does for these jobs what it does for a user's: the gate, the
fixed command line, the artefact checks and the run's own verification. No job may send
data to a third party.

**All twelve scenarios in one live run** (live-data #10). The data step downloaded and
verified the research files and built the hub, and the benchmark ran. Every scenario then
went through the runner:

| # | Scenario | Result |
|---|---|---|
| 1 | Formula provenance | PASS: 桂枝汤《伤寒论》 is one workbook version; its five crude drugs match 《伤寒论》第12条; no 十八反 or 十九畏 pair among them, which is the absence of a record, not safety |
| 2 | Natural-product identification | PASS: berberine is among the 243 constituents recorded for *Coptis chinensis*, and PubChem resolves it to CID 2353 |
| 3 | Compound–target retrieval | PASS: 88 records, all measured activities, each with its measurement and publication |
| 4 | Mechanism hypothesis | UNVERIFIED. The `research.snapshot` job built six snapshots from the real files and reproduced the gold standard. The `research.run` job, through `network_pharmacology_run`, found 589 measured targets and used no predicted edge. It read exactly the snapshots the build job recorded, claimed no pathway because none survived analysis and rebuttal, and released an artifact that passed its release validation. One check is UNVERIFIED: three input files have no reviewed checksum. |
| 5 | Pathway enrichment | PASS: every p-value equals SciPy's hypergeometric survival function (worst relative error 8.4e-12) |
| 6 | Protein sequence | PASS: angiotensin II translated, 1,045.5345 Da against the standard residue table |
| 7–9 | Docking, ADMET, transcriptomics | PASS, with the numbers of agent-engines #2 and #3 (below) |
| 10 | Clinical literature | PASS: Europe PMC, 10 of 13 hits returned, each with a PMID, PMCID or DOI; ClinicalTrials.gov, 10 studies with their NCT ids |
| 11 | Evidence synthesis | PASS: 《伤寒论》第12条 licenses an attribution, not an efficacy claim; the four 十八反 partners of 甘草 are named |
| 12 | Cross-database integration | PASS: NPASS, CMAUP and LOTUS record berberine in its source species under one InChIKey |

**Docking, ADMET and transcriptomics as runner jobs** (`agent-engines`, [run
38164474760](https://github.com/psknlr/TCMScience/actions/runs/38164474760), repeated
by [38166939882](https://github.com/psknlr/TCMScience/actions/runs/38166939882) and in
live-data #10): every check passed against references outside the code.

| # | Job | Result |
|---|---|---|
| 7 | `pipeline.dock`: trypsin's pocket (PDB 3PTB) with benzamidine, 4-methylbenzamidine and octane | benzamidine redocked 0.42 Å from its crystal pose (threshold 2.0 Å); the box 0.22 Å from the crystal ligand's centroid; benzamidine −6.08 and 4-methylbenzamidine −6.17 kcal/mol, octane −3.48; Vina 1.2.7, meeko 0.8.0, RDKit 2026.09.1 recorded |
| 8 | `pipeline.admet`: aspirin, caffeine, n-tetracontane; two models built from TDC's archive | every weight against IUPAC atomic weights; every polar surface area against Ertl's fragment sums (63.60, 61.82, 0 Å²); Lipinski 0, 0, 2; TDC's scaffold splits 728/182 and 461/117; held-out Caco-2 MAE 0.2821 (≤ 0.40), HIA AUROC 0.9239 (≥ 0.85) |
| 9 | `pipeline.rnaseq`: a simulated experiment as FASTQ, quantified by salmon | all 7 planted genes called in their direction, each fold change within 1 of the planted log2 2; 2 unplanted genes called (of 9 called among 30) |
| 9 | `pipeline.scrna`: 10x Genomics' PBMC 1k from Zenodo, its MD5 checked against the record | 1,044 cells; T 45 %, NK 5 %, B 16 %, monocytes 31 %, each within healthy adult ranges; no tissue cell types |

The first run ([38163083038](https://github.com/psknlr/TCMScience/actions/runs/38163083038))
built the same models and passed every other check. It failed one: the ADMET split check
read TDC's metric names, which the cards write in lower case. That was fixed in 68baa96.

**The engines installed the way a user installs them.** `--provision GROUP` first installs
each engine group through the runner's own `env.provision` job, at the CI-tested pins
and from wheels. The runner verifies each package's import and version before the job
succeeds, and a known-answer calculation where one exists.

Locally, in a fresh Python 3.12 environment with only the base packages, the provision
jobs installed docking, ADMET and transcriptomics:
- **ADMET:** PASS.
- **Docking:** UNVERIFIED by design. Vina's calculation needs a prepared receptor, so it is
  left to the docking job, and the reason is named.
- **Transcriptomics:** UNVERIFIED. Its packages installed and imported. The command-line
  tools no pip job installs (fastp, salmon, kallisto, hisat2) were not found, and are named.

The docking scenario then passed on what the jobs had installed, and so did the RNA-seq
job's planted-gene checks. The ADMET models and the PBMC sample need hosts this container's
proxy refuses, so those steps are BLOCKED here.

That run found two defects a user would have hit:
- The transcriptomics provision job failed although every package had installed: its
  verification also required the four command-line tools. It is now judged on what it
  installs, and reports the tools it did not find.
- The ADMET job, refused its download by the proxy, was reported FAIL. A job that cannot
  reach a host it needs is now BLOCKED with the host and the reason, and only a job that
  fails on its own is FAIL.

`agent-engines` now installs nothing but the base packages and provisions all three groups
through the runner. `--require-pass` accepts a provision verdict short of PASS only when
the calculations it lacks are deferred by design.

**The runner's own reliability.** Running the research job through the runner exposed a
race. The runner's warm-up thread and a request thread could both import PSH for the
first time. In 2 of 5 reproductions Python's import system raised `_DeadlockError`, and
the catalog then hid all 11 governed skills until the runner restarted. The runner now
imports PSH before its threads start, and a failed catalog section is rebuilt on the
next lookup instead of being kept. A fresh-interpreter test fails without the fix.

## Every external source, live

`sources-live` downloads every hub dataset a program can fetch, each on a GitHub-hosted
runner of its own, the way Studio's `tcmdb.fetch` and `tcmdb.build` jobs do. It verifies,
builds, rebuilds and queries each one, sends every operation of every shipped connector
once, and asks the sources catalogued unreachable once more.

| Run | Datasets PASS | UNVERIFIED | FAIL | SKIPPED | BLOCKED | Not run |
|---|---|---|---|---|---|---|
| #1 ([38157405923](https://github.com/psknlr/TCMScience/actions/runs/38157405923)) | 3 | 52 | 12 | 5 | 2 | 0 |
| #2 ([38163083017](https://github.com/psknlr/TCMScience/actions/runs/38163083017)) | 5 | 68 | 0 | 0 | 1 | 0 |
| #3 ([38164474741](https://github.com/psknlr/TCMScience/actions/runs/38164474741)) | 5 | 68 | 0 | 0 | 1 | 0 |
| #4 ([38166939954](https://github.com/psknlr/TCMScience/actions/runs/38166939954)) | 18 | 21 | 0 | 0 | 0 | 35 |

- **Run #1's twelve failures were ours, and were fixed at the root** (7741167, 137d7bb):
  - five downloads refused because figshare, Hugging Face and ClinPGx hand their files
    to another host: the hand-offs are now reviewed and named;
  - a body EMA cut off mid-transfer, now resumed;
  - relation kinds that come from optional files, now fetched when declared (BioGRID,
    nSIDES, the eQTL Catalogue);
  - tables fetched per entity (ChIP-Atlas);
  - per-entity services asked for too few entities (SymMap, HERB);
  - relations AOP-Wiki's release leaves empty, now declared with how that is known.

  The five SKIPPED had only optional files and are now validated on them.
- **Runs #2 and #3: every dataset but one was fetched, verified, built, rebuilt to the same
  relations and queried for every relation kind it declares.** That is 272 files, 4.21 GB
  per run:
  - 256 verified: 231 by size, 13 by their format's own completeness checks, 12 by
    checksum;
  - 14 kept with nothing to check them against.

  The one exception is miRTarBase, BLOCKED: its server ends the TLS handshake, and TLS is
  never weakened. A person who can reach it adds its files with `bioagent tcmdb
  import-raw`, which verifies them as a download would.
- **Why 68 were UNVERIFIED:** each lacked only a reviewed checksum. Runs #2 and #3 read
  257 files identically (228 in all three runs), and none changed. 88 of them are now
  pinned in `bioagent/data/hub_reviewed_checksums.json`, each with the rule that admitted
  it and the runs that read it. Their addresses name a fixed release, or they belong to an
  archive whose files do not change. The 157 at rolling addresses stay unpinned and
  UNVERIFIED: `current` directories, API exports, Hugging Face `main`, unversioned files.
- **Run #4, with those pins:** 39 dataset jobs ran. Every pinned dataset among them passes:
  `coconut`, `comptox`, `dcabm`, `faers`, `herb1`, `herb2`, `impc`, `massbank`, `mesh`,
  `nhanes`, `symmap2`, `tcmp300` and `wfo`. With NPASS, CMAUP, LOTUS and the two
  per-entity services, that is 18 PASS.
  - The 21 that stay UNVERIFIED each still hold a file without a reviewed checksum. Most
    sit at rolling addresses. Two are fixed releases left for the next review: MIBiG 4.0's
    JSON archive and AOP-Wiki's dated export.
  - GitHub gave the other 35 dataset jobs and the job that folds the results no runner.
    They ended at 19:15 UTC without running a step, and re-running them was refused to
    this integration (HTTP 403). They count as not run. 12 of them carry pins, so whether
    those datasets now pass is not yet shown.
- **Connectors (runs #3 and #4):** 438, then 440, of 444 operations across 117 services
  answered.
  - KNApSAcK's four timed out in both runs: its host did not complete the TLS handshake.
  - ReMap's two timed out in #3 and answered in #4.
  - The committed record carries the 440 answers of 2026-10-11, and KNApSAcK's last
    (2026-10-01).

  A timeout fails nothing; a wrong answer fails the job.
- **Sources catalogued unreachable:** 17 were asked again in runs #3 and #4.
  - **Answered (seven, by run #4):** ccTCM, HIT 2.0, ETCM v2.0, TCMSTD, IGTCM and PreDC
    answered at their front end, and so did TCM-Mesh, which answered HTTP 403 in run #3.
    Their cards say so, and the sources stay unreachable until a person has looked at
    whether their data can be obtained.
  - **No answer (nine):** two timeouts, a certificate for another name, an expired
    certificate, YaTCM's HTTP 403, and four names that no longer resolve.
  - **HIT 1.0:** it answered in run #3, but its host was defaced. It received that one
    request before the flag existed. It is now never contacted (`do_not_contact`), and run
    #4 skipped it.
- **Sources that need a person's account:** nothing logs in or accepts terms on anyone's
  behalf.
  - **IEU OpenGWAS:** its connector sends a person's token as a header, only over https to
    its own host, and never stores it. It stays pending until a run with the token
    (repository secret `OPENGWAS_JWT`) has verified it.
  - **DrugBank, TCMSP and TCM-NER:** these come in by a person's manual import.

  [tcm-data-sources.md](tcm-data-sources.md) lists what each restricted source needs.

## The benchmark: Guizhi Decoction and palpitations

> Investigate the potential biological mechanisms through which Guizhi Decoction may
> influence palpitations, using traditional medical literature, pharmacological
> databases, experimental evidence, and computational analysis.

Runs #3 to #5 and #10 ran it on real downloads, with the literature queried live and the
deterministic stages run twice in each. The four runs agree on every computed result. Only
Europe PMC's hit counts move with its index: in #10 they were 268, 30 and 1,024. The
wording below is from runs #4 and #5, after the corrections. Acceptance: **explained**.
Seven stages pass; five end UNVERIFIED for one stated reason: they read a file without a
reviewed checksum. No stage failed.

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

- **Real data, end to end.** In every live-data run, the research files are downloaded,
  verified, built into hub datasets and research snapshots, queried, rebuilt and analysed
  under governance. They total 418 MB from bidd.group, Zenodo, STRING, Reactome and
  UniProt.
- **Every fetchable hub dataset.** In sources-live #2 and #3, 73 of 74 were fetched from
  their own servers, verified, built, rebuilt to the same relations and queried: 4.21 GB in
  272 files per run. The committed record
  (`src/bioagent/data/live_download_verification.json`) holds each file's and each
  dataset's result, date, run and SHA-256, and the database readiness report reads it.
- **Every research scenario through the agent's own runner (live-data #10).** Each of the
  twelve was sent to tcmstudio's runner over HTTP, as the Studio page sends it, and every
  job was followed to its end. That covers:
  - formula provenance against 《伤寒论》第12条;
  - berberine in *Coptis chinensis* against PubChem CID 2353;
  - 88 measured compound–target records, each citing its publication;
  - the research loop as the runner's `research.snapshot` and `research.run` jobs;
  - enrichment against SciPy;
  - angiotensin II against the residue table;
  - docking, ADMET and transcriptomics as approved jobs;
  - Europe PMC and ClinicalTrials.gov records with their identifiers;
  - an attribution licensed and an efficacy claim not;
  - NPASS, CMAUP and LOTUS meeting on one InChIKey.

  Eleven pass. The research loop is UNVERIFIED only for its three unpinned input files.
- **The engines under the agent's scheduling.** Docking, ADMET and the RNA-seq and
  single-cell pipelines ran as approved runner jobs and met every reference (agent-engines
  #2 and #3, and live-data #10).
- **Connectors.** 440 of the 444 shipped connector operations answered live on 2026-10-11,
  and the record a connector ships on carries those answers.
- **Every capability classified.** 677 records with 0 FAIL in the offline tier. Each
  report names its environment and the command that reproduces it.
- **The agent under failure.** Outcome classes distinguish an unavailable tool or dataset,
  no record, a negative finding and inconclusive evidence. After three failures, or one a
  retry cannot change, the tool is not called again in that turn.
- **Network status from real requests.** Hosts are probed with real requests, and a switch
  alone never makes a sandbox "online". Sources behind logins, data-use agreements,
  CAPTCHAs or dead hosts are BLOCKED with their reason. None of those controls is
  bypassed, TLS verification is never turned off, and a host recorded as compromised is
  not contacted.

## What is not, and why

- **Three research files have no reviewed checksum**, so every stage that reads them is
  UNVERIFIED: the benchmark's pathway, enrichment, evidence and hypothesis stages, and
  scenario 4's research run. Their spec is in `acquisition/sources.py`, inside the pinned
  governed skills' import closure, so pinning them waits on the maintainer re-pin
  ([proposal](proposals/repin-tcm-skills.md)). STRING's file is a fixed release that can
  be pinned as it is. The other two need versioned copies.

  | File | Bytes | Observed SHA-256 (live-data runs #3 to #5) | Note |
  |---|---|---|---|
  | `9606.protein.aliases.v12.0.txt.gz` (STRING) | 19,777,800 | `b65f730b993ed0c1bd72edf4565d3d425db42861101b29699704810e8f125680` | frozen release v12.0: a checksum will hold |
  | `UniProt2Reactome.txt` (Reactome) | 43,041,375 | `e34fe56a5129d88d8cd67300c279d7e81fa1d3bf86fa0577cf91032c326eaecc` | from `download/current/`, which changes with each release: pin a versioned copy, not this URL |
  | `uniprot_human_reviewed_accessions.txt` (UniProt) | 144,669 | `7a153cbbf75f350b4bdc711d68bd3adbc0f4c5e9244aefb4a2fb8f8a7f24d5b7` | a live query that changes with each UniProt release: pin a release |

- **Hub files at rolling addresses** (157 files) are verified by their size or their
  format's own completeness checks, never by a pinned checksum, and their datasets stay
  UNVERIFIED. Among them are ChEBI's `flat_files`, IntAct's and np-mrd's `current`
  directories, Hugging Face `main` and GNPS's daily exports. In sources-live #4 that left
  21 datasets UNVERIFIED.
- **35 dataset jobs of sources-live #4 never ran.** From 19:15 UTC on 2026-10-11 GitHub
  gave them no runner. The commits pushed with this report have not been through CI
  either: 3c81807, which fixes ci #281, and `agent-engines`' switch to `--provision`. The
  local suites pass them, and their verdict is the next run's.
- **Governed skills on production data:** a [proposal](proposals/repin-tcm-skills.md)
  that needs a maintainer's review and `PromotionDecision`. Until then the skills answer
  from the labelled seed corpus. Studio routes around their limits without changing them:
  the complete workbook, the hub, the 十八反/十九畏 rule table, the research loop.
- **Unreachable sources:**
  - miRTarBase's server ends the TLS handshake. It stays BLOCKED, and a person who can
    reach it adds its files with `import-raw`.
  - KNApSAcK's API timed out in runs #3 and #4, so its four operations are BLOCKED there.
    They answered on 2026-10-01.
  - Of the 17 sources catalogued unreachable, seven front ends answer now, and a person
    has to re-assess what data they offer.
- **Sources that need a person's account:**
  - The OpenGWAS connector stays pending until a token is among the repository's secrets
    (`OPENGWAS_JWT`) and a run verifies it.
  - DrugBank, TCMSP and TCM-NER come in only by a person's manual import.
  - The others ([tcm-data-sources.md](tcm-data-sources.md)) are not wrapped.
- **Database coverage (live-data #10):** the readiness report holds 146 records: 18 PASS,
  40 BLOCKED by access rules or dead hosts, 9 SKIPPED, 79 UNVERIFIED and 0 FAIL. The
  UNVERIFIED are mostly catalogued sources that are not integrated, or datasets without a
  reviewed checksum.
- **erratum.0001**: 桂枝甘草汤《伤寒论》 row 23098 reads 「草2两」 for 甘草. The correction is
  proposed, applied and labelled in every answer that uses it, and awaits review.
- **The enrichment test has little power on this background.** Databases record active
  results far more often than inactive ones, so most assayed proteins are potent hits,
  which caps any pathway's fold enrichment. Here 366 of the 558 assayed proteins in
  Reactome's lowest-level mapping are potent hits, so no pathway can exceed 1.52-fold. The
  other 223 of the 589 potent targets have no lowest-level annotation and do not enter the
  test.
- **Three pre-registered pathways** are parents (Cardiac conduction, Potassium Channels,
  Muscle contraction), so the lowest-level mapping cannot contain them. The definition was
  left unchanged after seeing the data. A later version could pre-register the hierarchy's
  lowest levels, or use Reactome's all-levels mapping with a correction for nested tests.
- **The conversational agent's prose** is held to its tool results by rules and citations,
  not by a sentence-level check.
- **This development container** cannot reach the data hosts. Live validation runs only on
  GitHub-hosted runners: `live-data.yml`, `sources-live.yml` and `agent-engines.yml`,
  weekly, on demand, and on the pushes that change what they test.

## Deployment readiness

- **Ready**
  - The Studio runner and web app, with CI green (studio #55): runner, web and edge
    suites, the site build, and browser end-to-end tests.
  - The data hub's verified acquisition of NPASS, CMAUP and LOTUS, and of every other
    downloadable dataset except miRTarBase: each was fetched, verified, built and queried
    live. Every pinned dataset that sources-live #4 ran passed.
  - Docking, ADMET and the omics pipelines as agent workflows:
    1. A user approves `env.provision` for the engine group.
    2. The runner installs and verifies the group.
    3. The user approves the job.
    4. The runner runs it and checks its outputs.

    agent-engines #2 and #3 and live-data #10 ran the jobs. The provision step ran locally
    (above). In CI, it is the next run's.
  - The research loop as runner jobs (`research.snapshot`, then
    `network_pharmacology_run`), on files the job downloads and verifies itself. In
    live-data #10 both jobs ran on the operator's approval. They passed every check except
    the one on the three unpinned inputs.
  - The validation commands and every CI tier.
- **Ready, with stated limits**
  - Research answers about formulas and natural products. The three research files
    without a reviewed checksum keep the pathway and enrichment stages UNVERIFIED.
  - Hub datasets whose files sit at rolling addresses: queried from verified files, but
    held to their size or structure, not to a reviewed checksum.
  - Seed-corpus skills, labelled as such.
  - Literature retrieval, which returns hits, not appraised evidence.
- **Not ready**
  - Governed TCM skills on production data, until the re-pin is reviewed.
- **For the Definition of Done:** a new user can do the following without repairing
  anything.
  1. Ask about a formula.
  2. Approve the snapshot job. It downloads and verifies NPASS, CMAUP, LOTUS, STRING,
     Reactome and UniProt (418 MB) on a runner with web access.
  3. Approve the research job.
  4. Receive a released, reproducible, evidence-traced artifact that says what the data
     license and what they do not.

  For docking, ADMET and omics, the user approves `env.provision` for the engine group,
  then the job. The RNA-seq pipeline uses system command-line tools when they are present
  (salmon, fastp, …). They come from the package manager, and the provision job names the
  ones it did not find.
