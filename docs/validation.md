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
| 4 End-to-end research | the Guizhi Decoction and palpitations benchmark on those files, its literature queried live, run twice and compared; 葛根芩连汤 through the research job (sixteen stages, nine exports, then `np_exports verify`): defined, not yet executed | `live-data.yml` | `bioagent validate benchmark --out DIR --raw W/raw --live --repeat --strict` |
| 5 Performance and reliability | each record's runtime; resumed downloads (a byte-range request on a partial file); rebuild and repeat digests; the agent's failure budget | inside tiers 2–4, and the agent tests | — |
| Browser (Pyodide) | the real Pyodide runtime in Chromium: one governed skill's content and output hashes against native CPython (`browser.spec.mjs`), the source gateway's code with a mocked upstream, and the distance matrix with acceleration `auto` (WebGPU or WebAssembly SIMD, whichever the runtime picks; CI's headless Chromium has at most a software adapter, which the runtime refuses, so there it is the CPU), the CPU kernel and the reference Python giving the same result (`browser-access.spec.mjs`), a phone emulation (`mobile.spec.mjs`); and the runtime check, which compares about twenty envelopes with native CPython field by field | the specs: `studio.yml`'s build job, where an unreachable Pyodide CDN fails them (outside CI they skip, with the reason); the runtime check: by hand only, not in CI | `npm run test:e2e`; `npm run test:runtime` |
| Connectors | every operation by level ([below](#connector-verification-levels)) | `ci.yml` `validation` (every push) for the offline levels; `connectors-live.yml` monthly and on demand for the live one, as a report | `python3 studio/scripts/connector_coverage.py --check` |

The specialised CI jobs stay as they were and remain part of the picture: the analysis
pipelines with the standard tools, docking and ADMET with their engines, the structure
engines, scVI, the end-to-end cases and the manual live-connector verification. The
engine paths themselves are exercised by those jobs' own tests (`test_omics_de_backends`,
`test_omics_sc_backends`, `test_omics_deseq2_r`, `test_docking`, `test_admet`). The
analysis and molecular-modelling jobs also re-run the run-record tests and the built-in
reference datasets (`tests/test_runrecord.py`, `tests/test_pipeline_references.py`) in an
install that has the engines, but those tests need none: the RNA-seq and single-cell
references pin the built-in backends, the docking reference compares only its fixture's
digest, and the ADMET reference runs only where `BIOAGENT_TDC_ARCHIVE` names a copy of the
archive, which neither job sets. They check the recorded stages and the reference results,
not the engines. The molecular-modelling and analysis jobs also write the dependency
readiness report: the groups they install are imported, checked against their locked
versions and given a calculation with a known answer there, not only reported `BLOCKED`
where they are absent.

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

## Capability verification levels (the catalog)

Every catalog entry carries `verification = {status, levels}` (`tcmstudio.catalog`), read
from committed records only: a level is never dated by the clock, never taken from a
fixture as if it were live, and an entry with no record is `UNVERIFIED`, which is not a
failure and not a pass. The compatibility matrix's `validation` column
([`studio/docs/compat/matrix.csv`](../studio/docs/compat/matrix.csv)) prints these levels.

| Level | What it shows | What produces it | Entries with it |
|---|---|---|---:|
| `offline_invocation` | the entry's own example ran through the dispatcher, offline | `tcmstudio validate` records it per run (tier 2); no committed record feeds the catalog yet | 0 |
| `reference_output` | the tool's output equals an expected value established independently of its code | `bioagent/validation/references.py`, held by `tcmstudio validate` | 74 |
| `kernel_parity` | a browser kernel returns exactly what the Python implementation returns | `studio/web/test/fixtures/kernel_cases.json`, run by the web and runner tests | 7 |
| `pyodide_parity` | the browser runtime's envelope equals native CPython's | the runtime check (`npm run test:runtime`); it is run by hand and commits no record, so the catalog has none | 0 |
| `lock_pin` | a pinned governed skill's code matches its reviewed content hash | `BioScience-Harness/registry/skills.lock.yaml`, checked on every governed run | 4 |
| `gateway_mock` | the edge gateway (`studio/edge/src/sources.js` `handleSources`) accepted the operation's request and forwarded it to a mocked upstream | reserved for a per-operation run of the gateway's own code; no committed record exists. The connector coverage report's `mock_transport` level (below) does not exercise the gateway, is a separate, lower level, and is reported only in `connectors.md`, never here | 0 |
| `live_upstream` | the real upstream answered the operation's example | `BioScience-Harness/data/connector_live_verification.csv` (`scripts/verify_connectors.py`), with its date | 444 |
| `live_gateway` | a browser call through the deployed gateway reached the real upstream | nothing yet: no committed record exists | 0 |

`state.implementation` is `tested` exactly when some level is `PASS`; the runner adds
`readiness` per runtime (CONTRACTS §2). Nothing here says a capability is ready on a given
machine: readiness is computed where the call would run.

## Connector verification levels

[`studio/docs/compat/connectors.md`](../studio/docs/compat/connectors.md) (one row per
operation in `connectors.csv`, generated by `studio/scripts/connector_coverage.py`, whose
`--check` runs in CI) reports each level apart, and a lower level is never counted as a
higher one:

| Level | What it shows | Where it comes from |
|---|---|---|
| mock transport | the operation, called through Studio's dispatcher as the browser calls it, sends one request to its declared host, the same method, URL and body as the live verification, and a stand-in's HTTP 200 comes back succeeded | the generator, offline, on every `--check` |
| offline refusal | with web access off, the call is refused (`network_off`) before any request | the generator, offline |
| live upstream | the real upstream answered, through the runner's `HTTPBackend`, on the date recorded | `data/connector_live_verification.csv`, changed only by a reviewed commit of a real run |
| live through the deployed gateway | a browser call through science.impf.ai's gateway to the real upstream | none: every operation is "not verified" |
| scheduled full run | every operation re-run against its upstream on a schedule | `.github/workflows/connectors-live.yml`, monthly and on demand; it uploads its CSV and a coverage report and commits nothing, so no scheduled run is recorded in the repository |

The edge gateway's own policy (`studio/edge/src/sources.js`) is unit-tested against a small
synthetic registry and is exercised for one operation, with a mocked upstream, by
`browser-access.spec.mjs`; it is not part of the mock-transport level.

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
| 7 | molecular docking | BLOCKED | Vina, meeko, RDKit, gemmi (`env.provision` group docking); where they are installed, UNVERIFIED |
| 8 | drug-likeness and ADMET | BLOCKED | RDKit, scikit-learn; where they are installed, UNVERIFIED |
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

- **CI runs since 2026-10-11**: every `ci` run from #282 and every `live-data` run from #11
  (2026-10-11, 20:06 UTC) to 2026-10-19 ended within about a minute, almost all within
  five seconds, without running its jobs: a condition of the repository's Actions account,
  not a test result. The last runs that ran their jobs are `ci` #281 and `live-data` #10
  (2026-10-11). What was added since has therefore not run on GitHub: the pipeline stage
  and reference tests in the engine jobs, the 葛根芩连汤 real-data step of `live-data.yml`,
  the connector coverage check and the monthly live connector run. They are defined, not
  executed.
- **The 葛根芩连汤 real-data case**: the research job on the tier-3 files, its nine exports
  uploaded and re-checked with `np_exports verify`, is defined in `live-data.yml` and has
  never run. The README's headline figures for this formula come from runs outside CI and
  are not regression-checked.
- **The verified transfer, live**: `acquisition.transfer.VerifiedTransfer` (resume only onto
  the same remote file, archives read through before a file is kept) is tested against
  loopback servers only; its first contact with the real servers will be the live tier.
- **Browser runtime parity beyond a subset**: the runtime check compares about twenty
  envelopes with native CPython, by hand; no test compares every browser entry, and the
  network-pharmacology seed skill has never been run in Pyodide by a test.
- **Hardware**: no physical GPU and no NPU. WebGPU has run on a software adapter only, a
  runner GPU is reported by its driver and never verified by a computation, and no
  workload has a verified NPU execution provider. Firefox, Safari, iOS and Android devices
  have not been tested.
- **The deployed gateway against real upstreams**: no connector has been verified from a
  browser through science.impf.ai to its source; the post-deploy check reads the gateway's
  health counts only.
- **Remote compute**: none is deployed; `studio/docs/REMOTE_COMPUTE.md` is a design.
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
- **Engines** (docking, ADMET, single-cell): verified in their own CI jobs, not on a
  default runner; `env.provision` installs a group at the CI-tested pins after approval.
  Through the agent they run as jobs a person approves, and the validator approves
  nothing on anyone's behalf: where the engines are installed, the docking and ADMET
  scenarios ask `capabilities_status`, submit nothing, and stay `UNVERIFIED`.
