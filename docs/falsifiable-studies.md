# From network pharmacology to falsifiable studies

The network-pharmacology skill answers "which targets and pathways may a formula touch?".
That question can almost always be answered yes. The work here moves the harness towards
questions data can answer *no*:
- which effects can happen at the exposure actually reached;
- what the whole formula adds over its main constituent;
- whether "synergy" is pharmacodynamics, pharmacokinetics or preparation;
- which conclusions survive a change of data, assay or cell context.

Computation here includes re-analysis of real experimental data. Observations,
statistical estimates and model predictions are recorded separately, and a computed
result is never counted as an experimental replicate.

## What changed in the runner

- **The intervention is a parameter.** `run_skill(formula=…)` and
  `scripts/run_network_pharmacology.py --formula ID` analyse any formula version that
  the herb-layer snapshot records, not only 葛根芩连汤 of 伤寒论. A formula the snapshot
  does not record is refused instead of being analysed as an empty composition. The PSH
  claim scope and the provenance record name the formula that was actually run.
- **The inputs are locked.** Every run writes `snapshot_lock.json`, naming each source's
  exact snapshot id. `--lock` reruns on exactly those snapshots. Without a lock, a source
  that has several recorded snapshots is refused unless `--latest` is passed.

The lock found two real problems on the first try, on the snapshot set this branch was
developed with:

1. **Two different snapshots under one label.** The ledger records two `pubchem_bioassay`
   snapshots with the same version, `2026-09-24+subset-f824da6a`, and different content
   (`#5f9b172a1051` and `#d1473fac6bee`). The old runner silently used the last one.
2. **The earlier one no longer exists.** Snapshots are stored by version, so the second
   build overwrote the first on disk. A lock naming `#5f9b…` is now refused ("loaded
   #d147…, but the run pins #5f9b…"), where the old runner would have used different data
   under the same name. The snapshot builder should refuse to reuse a version label for
   different content; that is the next fix.

The lock reproduces the run exactly: the baseline below gives the same result digest
(`sha256:6568c7be…`) unlocked with `--latest` and again from its lock.

## Route A on real data: what the 葛根芩连汤 mechanism signals rest on

The baseline is screening hits (PubChem actives weighed against inactives) against the
background of proteins the compounds were actually tested on: 10,000 permutations, seed
20261001. It tests 58 pathways and releases 3 mechanism hypotheses, all at q = 0.033:
- synthesis of epoxy (EET) and dihydroxyeicosatrienoic acids (DHET);
- synthesis of (16-20)-hydroxyeicosatetraenoic acids (HETE);
- biosynthesis of maresin-like SPMs.

All three are carried by cytochrome P450 enzymes (CYP1A2, CYP2C9, CYP2C19, …). The
screening edges against those enzymes come mostly from the Tox21 P450-Glo and qHTS
CYP-antagonist panels. These are luminescence or luciferase reporter screens that tested
thousands of compounds. `scripts/run_robustness.py` re-runs the analysis with each group
of evidence held out:

| Held out | EET/DHET | HETE | Maresin-like SPMs |
| --- | ---: | ---: | ---: |
| nothing (baseline) | 0.033 | 0.033 | 0.033 |
| one assay campaign at a time (12 largest AIDs) | survives all | survives all | lost without AID2284394 (q 0.093) or AID2283485 (q 0.086) |
| the whole CYP-panel family (4,161 edges) | not testable | not testable | 0.299 |

Every released hypothesis is carried by the CYP screening panels. Without them, two of
the pathways are not even testable, because their proteins enter the background only
through those panels. The signal shows that the formula's constituents were screened
against drug-metabolising enzymes and some inhibited them. That is a pharmacokinetic and
herb–drug-interaction signal (routes B and H), not evidence of an eicosanoid or
lipid-mediator mechanism of action.

Within the evidence available, the conclusion the data allow is: *"no pathway signal of
葛根芩连汤 is supported independently of CYP-inhibition screening panels."* The claims
stay hypotheses; this says what they rest on.

## The analyses (`bioagent.studies`)

| Module | Route | What it decides | What it refuses |
| --- | --- | --- | --- |
| `robustness` | A | Whether a pathway survives holding out each assay campaign, assay family or original study (all rows of one origin removed together) | Splitting one experiment between analysis and check |
| `exposure` | B | Free-concentration-to-potency ratio as a range: plausible, implausible, depends on unknowns, or undeterminable | AUC divided by a potency; mass units without a molecular weight; plasma as tissue; parent as metabolite; an assumed free fraction of 1 |
| `contrast` | D | Formula minus monomer in the same study, with a Welch interval and a two-one-sided equivalence test against a margin set in advance | Groups from different studies; "formula significant, monomer not" as a difference; non-significance as equivalence; residual as synergy |
| `combination` | E | Effectiveness, excess over the prespecified reference (Bliss, HSA or Loewe from Hill fits), and selectivity against cytotoxicity, as three answers | Synergy from single agents alone; choosing the reference model afterwards |
| `heterogeneity` | G | The treatment × baseline interaction across both arms, BH-adjusted across prespecified modifiers | One arm; post-treatment features as modifiers; more modifiers than prespecified |
| `design` | all | Exact interventions (four-herb 葛根芩连汤 is not the seven-herb trial formula of the same name), preparation batches, censored measurements, exposures, assay results, perturbation contrasts, and a hash-locked protocol | "Not tested", "below LOD", "above highest tested" and "inactive" becoming 0; changing the endpoint or thresholds of a locked protocol without the analysis turning exploratory |

The statistics are implemented on numpy, with no scipy; the t distribution is checked
against tables in `tests/test_studies.py`.

An independent review checked the statistics numerically, against exact formulas, numerical
integration and simulation:
- the t distribution, Welch intervals, TOST, BH and OLS were correct;
- five defects were found and fixed, each now with a regression test.

The worst was in the combination analysis. A percentile bootstrap over three replicates,
with the Loewe reference held fixed, flagged "exceeds the reference" in 9–20% of cells of
data that had no excess, against a nominal 2.5%. The interval is now a t interval on
excess = observed − reference. Its variance adds the propagated variance of the reference:
a delta method for Bliss and HSA, and refitting both Hill curves to replicate-resampled
data for Loewe. Degrees of freedom are conservative.

| Data with no excess | Before (exceeds / below) | After (exceeds / below) | Nominal |
| --- | --- | --- | --- |
| Bliss, 3 replicates | 9.4% / 11.1% | 0.3% / 0.3% | 2.5% each |
| Bliss, 8 replicates | 5.9% / 4.2% | 2.0% / 1.6% | 2.5% each |
| Loewe, sham combination | 17–18% / 19–20% | 0.3–0.6% / 0.8–1.1% | 2.5% each |

The test is now conservative at very few replicates, which is the safer direction for a
synergy claim. The other four defects:
- the exposure screen compared records in different units before converting them;
- it preferred a below-LOD bound over a measured value;
- `umol/L`-style units were refused;
- tiny p values cancelled to 0, and the t quantile was clamped at ±1000.

## The eight routes: status and what each still needs

| Route | Status in this branch | What it needs next |
| --- | --- | --- |
| A. Mechanism robustness | **Run** on 葛根芩连汤 (above) | Matching by assay type and compound properties; leave-one-study-out through the tcmdb lineage (`consensus`) for curated, non-screening evidence |
| B. Exposure-constrained mechanism | Analysis implemented and tested | Measured concentration–time and free-fraction data for the marker compounds, mapped to the formula and batch (e.g. the 2018 whole-formula / no-licorice / single-herb pharmacokinetic study); availability not yet checked |
| C. Cell context and direction | Not started | Disease pseudobulk per individual × cell type; real perturbation signatures (LINCS metadata is in `tcmdb`); baselines (no change, mean, linear) before any model |
| D. Formula vs monomer | Analysis implemented and tested | The individual-level data of the 葛根芩连汤 vs berberine study; availability to be checked before any analysis |
| E. Combination | Analysis implemented and tested | Same-experiment single-agent and combination dose matrices (DrugComb's summary is kept in `tcmdb`; berberine–glycyrrhizin needs pharmacodynamic data) |
| F. Preparation and batch | Records in place (`PreparationBatch`) | Paired batch × chemistry × function data, with leave-batch-out validation |
| G. Response heterogeneity | Analysis implemented and tested | Patient-level baseline, arm and outcome tables; the 2024 seven-herb trial's sequencing (PRJCA007557) is public, its linked clinical data are on request |
| H. Benefit vs toxicity | Partly (ToxCast with negatives and cytotoxicity in `tcmdb`) | Efficacy and toxicity curves under comparable exposure; compare at equal benefit, not equal nominal dose |

None of the analyses runs on data it does not have. Where data are missing, the route
stops and says what is missing.
