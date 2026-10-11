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

The specialised CI jobs stay as they were and remain part of the picture: the analysis
pipelines with the standard tools, docking and ADMET with their engines, the structure
engines, scVI, the end-to-end cases and the manual live-connector verification.

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
| 6 | pathways | the pre-registered pathways that contain measured targets: a membership, not a mechanism |
| 7 | enrichment | the controlled over-representation analysis and its rebuttals, governed under PSH |
| 8 | literature | pre-registered Europe PMC and ClinicalTrials.gov queries, records typed by metadata, not appraised |
| 9 | evidence | each evidence type graded by tier with the claims it can license; contradictions computed from the records |
| 10 | hypotheses | the pathways that survived, each a labelled mechanism hypothesis citing measured edges, with what would test it |
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
| 7 | molecular docking | BLOCKED | Vina, meeko, RDKit, gemmi (`env.provision` group docking) |
| 8 | drug-likeness and ADMET | BLOCKED | RDKit, scikit-learn |
| 9 | transcriptomic and single-cell analysis | BLOCKED | the transcriptomics packages |
| 10 | clinical literature and trials | SKIPPED | the network (live tier) |
| 11 | evidence synthesis | runs | — |
| 12 | cross-database integration | BLOCKED | two of NPASS, CMAUP, LOTUS built |

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
  zenodo.org and stringdb-downloads.org. The live tier runs on GitHub's runners instead.
- **STRING aliases and Reactome's UniProt mapping**: no reviewed checksum (Reactome's
  `current` file changes with every release), so the stages that read them stay
  `UNVERIFIED`, with the SHA-256 of what was read.
- **The four pinned governed skills** still read the illustrative seed corpus; changing
  them changes their content hashes, which only a maintainer's review may re-pin
  (`docs/proposals/repin-tcm-skills.md`). Studio routes around the corpus's limits
  without touching them: the complete workbook, the hub, the research loop, the rule table.
- **erratum.0001** (桂枝甘草汤《伤寒论》) is proposed, applied and labelled, awaiting review.
- **Engines** (docking, ADMET, single-cell): verified in their own CI jobs, not on a
  default runner; `env.provision` installs a group at the CI-tested pins after approval.
