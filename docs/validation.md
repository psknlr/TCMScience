# Validation: what is checked, where, and what each status means

A registry entry says a capability exists; a passing unit test says a function behaves on
the inputs its author chose. Neither says that the capability ran *here*, on real input,
and gave the right answer. The validation commands record that instead of assuming it:
every component gets a record of checks, each with one of six statuses, and every run is
written as machine-readable reports.

## Statuses (`bioagent.validation`)

| Status | Meaning |
|---|---|
| `PASS` | the check ran, and its outcome is what an independent expectation says |
| `FAIL` | the check ran, and its outcome contradicts the expectation: a defect |
| `BLOCKED` | something outside the code stopped it: a network policy, an unreachable host, a missing engine or dataset |
| `SKIPPED` | deliberately not run in this tier (a live download in the offline tier) |
| `UNVERIFIED` | it could run, but nothing here establishes its outcome (no reference, a file without a reviewed checksum, snapshots built elsewhere) |
| `NOT_APPLICABLE` | the check does not apply to this component |

A record is `PASS` only when every check that applies to it passed. Anything else ranks
above `PASS` (`FAIL`, then `BLOCKED`, `UNVERIFIED`, `SKIPPED`), so a skipped or blocked
live test can never be read as a successful one, and a capability nobody could check is
never counted as working. `--strict` makes a command exit non-zero on `BLOCKED` as well as
on `FAIL`; the live-data workflow uses it.

## Tiers

| Tier | What runs | Where | Command |
|---|---|---|---|
| 1 Unit | functions and their contracts | `ci.yml`: `psh`, `bioscience` (`-m unit`), `studio.yml` | `pytest` |
| 2 Integration | every registered capability through the Studio dispatcher with its reference output; the twelve research scenarios; the benchmark as far as it goes offline | `ci.yml`: `validation`, every push | `tcmstudio validate --out DIR`; `bioagent validate benchmark --out DIR` |
| 3 Real data | the reviewed research files from their servers (NPASS, CMAUP, LOTUS, STRING, Reactome, UniProt), verified, built into the data hub and the research snapshots, queried, rebuilt | `live-data.yml`: by hand, weekly, and on a push that changes acquisition, sources, research, analysis or validation | `bioagent validate data --out DIR --work W --live --strict` |
| 4 End-to-end research | the Guizhi Decoction and palpitations benchmark on those files, its literature queried live, run twice and compared | `live-data.yml` | `bioagent validate benchmark --out DIR --raw W/raw --live --repeat --strict` |
| 5 Performance and reliability | each record's runtime; resumed downloads (a byte-range request on a partial file); rebuild and repeat digests; the agent's failure budget | inside tiers 2–4, and the agent tests | — |
| 3 Every hub dataset | each downloadable and per-entity hub dataset (79 defined) fetched from its own server the way Studio's `tcmdb.fetch` job fetches it, built, checked and queried, one runner each; the manual ones listed with what a person has to do | `sources-live.yml`: by hand, weekly, and on a push that changes the hub | `bioagent validate datasets --out DIR --live --confirm-large` |
| 2–4 The engines as jobs | docking, ADMET and the RNA-seq and single-cell pipelines submitted as runner jobs through tcmstudio's own runner over HTTP, approved by the operator, followed with `job_status`, checked against independent references | `agent-engines.yml`: by hand, weekly, and on a push that changes the engines or the job path | `tcmstudio validate --out DIR --tier live --approve-jobs --scenarios 7 8 9 --require-pass` |

The specialised CI jobs stay as they were and remain part of the picture: the analysis
pipelines with the standard tools, docking and ADMET with their engines, the structure
engines, scVI, the end-to-end cases and the manual live-connector verification. The
molecular-modelling and analysis jobs also write the dependency readiness report: the
groups they install are imported, checked against their locked versions and given a
calculation with a known answer there, not only reported `BLOCKED` where they are absent.

Every command can be summarised as Markdown, which both workflows append to the run's
summary page:

```bash
bioagent validate summary DIR [DIR …] [--rows 80]
```

It prints one line of counts per report, the benchmark's acceptance, and every record
that did not pass, with its reason.

## Reports

| Report | Written by | Holds |
|---|---|---|
| `agent_inventory.json` | `tcmstudio validate` | every core tool, catalog entry, job kind, governed skill, hub dataset, source card and execution backend |
| `tool_validation_report.json` | `tcmstudio validate` | every capability against twelve checks: discovery, schema, invocation, functional correctness, error handling, dependency readiness, data integration, execution isolation, recovery, reproducibility, integration, performance |
| `agent_regression_report.json` | `tcmstudio validate` | the twelve research scenarios below, step by step |
| `sandbox_execution_report.json` | `tcmstudio validate [--probe-network]` | the network as real requests found it (never "online" because a switch is on), persistent storage, devices |
| `dependency_readiness_report.json` | `tcmstudio validate`, `bioagent validate deps` | each optional scientific package imported, versioned against the CI-tested pin, and used for a calculation with a known answer |
| `database_readiness_report.json` | `tcmstudio validate`, `bioagent validate databases`, `bioagent validate data` | every catalogued source: licence class, acquisition route, integrity method, parser, real-download record, live-connector record, local build and query |
| `live_download_validation.json` | `bioagent validate data --live` | per file: connectivity, size, resumed download, checksum, decompression, format, release |
| `research_pipeline_report.json` | `bioagent validate benchmark`, `bioagent validate data` | the benchmark's twelve stages; the research snapshots and the loop run on them |
| `scientific_reproducibility_report.json` | `bioagent validate data`, `bioagent validate benchmark --repeat` | rebuilds and repeats that must give the same digests |

Each record carries the component and its version, the input, the status and the first
reason it is not `PASS`, every check with the evidence it rests on, provenance, runtime,
and the command that reproduces it. Each report carries the environment it ran in
(packages, hardware, disk, whether traffic went through a proxy, the CI run), never a
proxy's address or a credential.

Live download results are folded into the committed record
`src/bioagent/data/live_download_verification.json` by
`bioagent validate record --report DIR/live_download_validation.json`, which refuses an
offline report: an offline run verifies nothing about a server.

## Every hub dataset, live (`bioagent validate datasets`)

`sources-live.yml` runs each downloadable and per-entity dataset of the data hub on a
runner of its own, the way Studio's `tcmdb.fetch` and `tcmdb.build` jobs run it, one
record each (`bioagent.validation.hub_datasets`):

| Check | What it takes to pass |
|---|---|
| acquisition | every file kept only once verified: its reviewed checksum, else the declared or the server's size, else, for a file a server streams without a size, its format's own completeness checks (a zip's per-member CRC-32s, a gzip, bzip2 or xz stream's check, a JSON or XML document that parses to its end). An HTML page in place of a file, a redirect to an undeclared host and a truncated transfer are refused |
| integrity | every file held to a reviewed checksum (PASS); a file kept on its size or its structure alone is UNVERIFIED with the SHA-256 that was read, for a maintainer to review and pin |
| entity_tables | for a dataset whose relations come from tables fetched per entity on request (ChIP-Atlas's Target Genes, per transcription factor), one fetched (STAT3) |
| build | the SQLite store, and rows of the declared relation kinds |
| check | the hub's acceptance checks; a rebuild gives the same relations |
| queries | every declared relation kind returns rows from this source and every loaded table answers; a kind or table the spec says the pinned release leaves empty (`DatasetSpec.known_empty`, with how that is known: AOP-Wiki's 2026-10-01 export links no stressor to a key event) is reported as such |

Which files: a dataset's default files, plus the optional files a declared relation kind
comes from (`DatasetSpec.optional_relations`: BioGRID's complete file for genetic
interactions, nSIDES's OFFSIDES, the eQTL Catalogue's credible sets); a dataset whose
default files hold no table is validated on all its files; one whose files are all kept
as files (FAERS's quarterly zip, MeSH's XML) is fetched and verified, and its build is
NOT_APPLICABLE. The per-entity services are asked for one herb and one ingredient
(SymMap SMHB00001 and quercetin SMIT00013; HERB's Ai Ye HERB000066, HERB002560 and
quercetin HBIN041495), so every relation kind they declare can come back; when one does
not, the record lists what the answers held.

Some hosts hand their files to another host: figshare to its S3 bucket, Hugging Face to
its CDN under hf.co, ClinPGx to s3.pgkb.org, ARCHS4 to the lab's other S3 host. The shared
downloader refuses any answer from another host than the URL's; a file's reviewed spec now
names its hand-off (`FileSpec.redirects`), and the hub's downloader (`tcmdb/download.py`)
reads the redirect without following it, checks the target is https on a named host, and
requests it there, verified like any other file. A body cut off mid-transfer is resumed.
(Both belong in the shared downloader, which is in the pinned governed skills' import
closure; moving them there, and adding the hand-off hosts to the research profile's
allowlist, waits on the maintainer re-pin in `docs/proposals/repin-tcm-skills.md`.)

A source that refuses (an unreachable host, a TLS handshake the server ends, a login) is
BLOCKED with its reason and the import command a person who can reach it would use
(`bioagent tcmdb import-raw KEY FILE...`, which verifies the file as a download would be);
nothing is tried around it. Manual datasets are listed the same way. Each live result is
folded into the committed record (`bioagent validate record --report ...`), which the
database readiness report reads as `real_download` and `recorded_build`.

## The Guizhi Decoction and palpitations benchmark

> Investigate the potential biological mechanisms through which Guizhi Decoction may
> influence palpitations, using traditional medical literature, pharmacological
> databases, experimental evidence, and computational analysis.

The definition, `src/bioagent/data/benchmark_guizhi_palpitations.json`, fixes before any
data are read what each stage must produce, what it needs, how it is validated and the
limitations it may end with, together with the cardiac and electrophysiological Reactome
pathways (matched by stable id or exact name) and the literature queries. The protocol is
the research loop's, with NPASS and CMAUP as activity sources and LOTUS and STRING
optional: potency at or below 10 µM, the assayed-protein background, Benjamini–Hochberg,
a degree-matched permutation null, and three rebuttals.

| # | Stage | What runs |
|---|---|---|
| 1 | formula_version | 桂枝汤《伤寒论》 resolved in the workbook (30 versions of the name), checked against the curated 伤寒论 formula and §12 |
| 2 | herbs | each crude drug with its dose, processing, written name and NCBI-verified species |
| 3 | compounds | the herbs' constituents in NPASS, CMAUP and LOTUS, by composition level and source |
| 4 | targets | proteins measured at or below 10 µM, each resting on a measured edge (a target without one fails the stage) |
| 5 | measured_vs_predicted | every edge class counted as measured or predicted; no target or hypothesis rests on a prediction |
| 6 | pathways | the pre-registered pathways that contain measured targets: a membership, not a mechanism. `UniProt2Reactome.txt` lists lowest-level pathways, so a parent such as Cardiac conduction has no rows of its own; the stage names it as not annotated |
| 7 | enrichment | the controlled over-representation analysis and its rebuttals, governed under PSH, with the test's power: how many assayed proteins are potent hits, and so the largest fold enrichment possible |
| 8 | literature | pre-registered Europe PMC and ClinicalTrials.gov queries, records typed by metadata, not appraised |
| 9 | evidence | each evidence type graded by tier with the claims it can license; contradictions computed from the records |
| 10 | hypotheses | the pathways that survived, each a labelled mechanism hypothesis citing measured edges, with what would test it; none surviving is an absence of evidence, not evidence of no involvement |
| 11 | formula_distinction | 桂枝汤 against 桂枝甘草汤: compositions, doses, indications; 心下悸 is 桂枝甘草汤's (§64) |
| 12 | artifacts | one canonical JSON artifact per stage with its SHA-256; `--repeat` compares a second run byte for byte |

Acceptance: every stage either completes on verified data or ends with a precise,
reproducible explanation: its status, its reason and the command that reproduces it. The
report's `acceptance` says which (`completed`, `explained`, or `not met` when any stage
fails). Snapshots the benchmark did not build from verified files keep its data stages
`UNVERIFIED`; and a stage whose files include one without a reviewed checksum (STRING's
alias file, Reactome's UniProt mapping, which changes with every release) is `UNVERIFIED`
with that file's SHA-256 recorded for review.

```bash
bioagent validate benchmark --out OUT                         # offline: stages 1, 2 and 11
bioagent validate benchmark --out OUT --raw RAW --live --repeat   # everything
bioagent validate benchmark --out OUT --snapshots SNAP            # snapshots built elsewhere
```

## The regression scenarios (`tcmstudio/scenarios.py`)

Each scenario is a task an agent is given, written as the tool calls a correct answer
needs, run in order through the dispatcher, with expected outputs, required data,
validation criteria checked against references outside the code, and acceptable
limitations. A step that cannot run ends the scenario with the class the agent must give
it (`tool_unavailable`, `data_unavailable`, …).

| # | Scenario | Offline here | Needs |
|---|---|---|---|
| 1 | formula composition and provenance | runs | the workbook |
| 2 | natural-product compound identification | BLOCKED | NPASS, CMAUP or LOTUS built (`--tcmdb-root`); PubChem live |
| 3 | compound–target retrieval | BLOCKED | NPASS or CMAUP built |
| 4 | mechanism hypothesis generation | UNVERIFIED | the seed skill correctly claims nothing; measured hypotheses are the benchmark's |
| 5 | pathway enrichment | runs (against SciPy) | — |
| 6 | protein sequence analysis | runs (against the standard residue table) | — |
| 7 | molecular docking | BLOCKED; UNVERIFIED where the engines are installed; with `--approve-jobs`, a job that must pass | Vina, meeko, RDKit, gemmi (`env.provision` group docking) |
| 8 | drug-likeness and ADMET | BLOCKED; UNVERIFIED where installed; with `--approve-jobs`, a job (its models in the live tier) | RDKit, scikit-learn; TDC's archive (live) |
| 9 | transcriptomic and single-cell analysis | BLOCKED; with `--approve-jobs`, the RNA-seq job (and in the live tier the PBMC job) | the transcriptomics packages; Zenodo (live) |
| 10 | clinical literature and trials | SKIPPED | the network (live tier) |
| 11 | evidence synthesis | runs | — |
| 12 | cross-database integration | BLOCKED | two of NPASS, CMAUP, LOTUS built |

### Scenarios 7–9 as approved jobs (`--approve-jobs`)

With the operator's approval the validator starts tcmstudio's own runner in a temporary
home (`tcmstudio/validate_jobs.py`) and calls it over HTTP the way the Studio page does:
`POST /api/call` with the pairing token and the approvals the page's router would ask for
(`job`, `network`, `remote_upload`, `first_runner_call`), files through `POST
/api/uploads`, the pipeline submitted with `run_pipeline` and followed with `job_status`
until it has finished. The runner's job service does what it does for a user: the gate,
the fixed command line, the artefact checks and the run's own verification. Each job's
step records its id, kind, final state, approvals and artefacts; a job that failed, was
refused (an engine not installed: `tool_unavailable` with the remedy) or did not finish
within `--jobs-timeout` is not passed. The finished results are judged against references
outside the code:

| # | The job | Checked against |
|---|---|---|
| 7 | `pipeline.dock`: trypsin's pocket (PDB 3PTB, shipped with bioagent, CC0) uploaded with benzamidine, 4-methylbenzamidine and octane | benzamidine redocked within 2.0 Å of its crystal pose; the box within 1.5 Å of the crystal ligand's centroid, computed from the PDB coordinates; both amidines (anchored at Asp189) scoring better than octane; Vina, meeko and RDKit versions recorded |
| 8 | `pipeline.admet`: aspirin, caffeine, n-tetracontane; live, two models built from TDC's archive | weights from IUPAC atomic weights (±0.06); polar surface areas summed by hand from Ertl's fragment contributions (63.60, 61.82, 0 Å²); Lipinski violations 0, 0, 2; live: TDC's scaffold split sizes (Caco-2 728/182, HIA 461/117) and metrics, held-out Caco-2 MAE ≤ 0.40 and HIA AUROC ≥ 0.85 |
| 9 | `pipeline.rnaseq`: a simulated experiment (`bioagent.omics.reference_world`, 3 + 3 paired libraries) uploaded as FASTQ files, a sheet, a transcriptome and a GTF | the 7 planted genes called (padj < 0.05) in their direction, each log2 fold change within 1 of the planted 2, at most 2 unplanted genes called (Benjamini-Hochberg bounds the expected share of false calls, not one run's); Welch's t against SciPy |
| 9 | live: `pipeline.scrna` on 10x Genomics' PBMC 1k (Zenodo record 14868136, its MD5 read from the record) | 700–1,600 cells kept; T, NK, B and monocyte clusters all found; at most 5 % of cells called a type that does not circulate; each lineage's share within a widened healthy-adult range (T 25–85 %, NK 2–35 %, B 2–30 %, monocytes 4–45 %) |

Run here (Python 3.12 with the engines installed, offline): the docking job redocked
benzamidine 0.42 Å from its crystal pose with the box 0.22 Å from the crystal centroid
(benzamidine −6.08, 4-methylbenzamidine −6.17, octane −3.48 kcal/mol; Vina 1.2.7); the
ADMET job matched every weight, surface area and rule count; the RNA-seq job called all 7
planted genes in their direction, and 2 unplanted ones. The live tier, with salmon, the
TDC models and the PBMC sample, is CI's `agent-engines` workflow.

Scenario 11 found that a safety report on 甘草 named only 甘遂: the seed corpus behind the
pinned skill holds 23 herbs. Studio now adds the clinic pack's 十八反/十九畏 table beside the
skill's answer (甘遂、京大戟、红大戟、海藻、芫花), labelled as a draft not reviewed by a
licensed practitioner, and `tcm_compatibility` reports the table's pairs too.

## The agent under failure

Every failed or empty tool result ends with an outcome line the model reads
(`studio/web/js/core/agent.js`): `tool_unavailable`, `data_unavailable`, `no_record`
("not evidence that none exists"), `input_error`, `not_permitted`, `tool_failed`; and for
results whose rows carry outcomes, `negative_finding`, `mixed_findings` or
`inconclusive`. A tool that fails three times in a turn, or once in a way a retry cannot
change, is not called again in that turn: the next call gets a refusal listing the
failures and the ways on.

## Not verified here, and why

- **Live downloads from this development sandbox**: its egress proxy refuses bidd.group,
  zenodo.org and stringdb-downloads.org. The live tier runs on GitHub's runners instead;
  what it found is in [the release qualification report](release-qualification-2026-10.md).
- **STRING aliases, Reactome's UniProt mapping and the UniProt accession list**: no
  reviewed checksum (Reactome's `current` file and the UniProt query change with every
  release), so the stages that read them stay `UNVERIFIED`, with the SHA-256 of what was
  read, for a maintainer to review and pin.
- **The four pinned governed skills** still read the illustrative seed corpus; changing
  them changes their content hashes, which only a maintainer's review may re-pin
  (`docs/proposals/repin-tcm-skills.md`). Studio routes around the corpus's limits
  without touching them: the complete workbook, the hub, the research loop, the rule table.
- **erratum.0001** (桂枝甘草汤《伤寒论》) is proposed, applied and labelled, awaiting review.
- **Engines** (docking, ADMET, the RNA-seq and single-cell pipelines): on a default runner
  they are not installed; `env.provision` installs a group at the CI-tested pins after
  approval. Through the agent they run as jobs a person approves. Without
  `--approve-jobs` the validator approves nothing: where the engines are installed, the
  docking and ADMET scenarios ask `capabilities_status`, submit nothing, and stay
  `UNVERIFIED`. With it, the operator's approval is recorded in every report and the jobs
  run and are judged (above); `agent-engines.yml` requires them to pass.
