# 葛根芩连汤 and berberine: what the published data allow

`scripts/study_gqd_berberine.py` re-analyses published group summaries and the locally
built ToxCast store under a protocol locked in the script. **Status: exploratory.**

The primary contrast was computed while the summary-statistics code was being tested,
before the protocol was locked. The protocol records that as an amendment, which makes
the study exploratory by its own rules. The exposure table below summarizes the archived JSON. New runs also check assay
species and quality flags, and report how many readouts are undeterminable.

## Data

| Study | What is public | What is not |
| --- | --- | --- |
| 42-marker pharmacokinetics, Front. Pharmacol. 2018;9:622 ([PMC6018403](https://pmc.ncbi.nlm.nih.gov/articles/PMC6018403/)) | Table 1: mean ± SD per group (rats, n = 10). The formula (18.9 g/kg), the formula without 甘草, and 黄连 alone at the dose the formula contains | Per-animal concentration–time data; plasma protein binding; gut concentrations |
| 葛根芩连汤 vs berberine, Genomics Proteomics Bioinformatics 2020 ([PMC8377040](https://pmc.ncbi.nlm.nih.gov/articles/PMC8377040/)) | 16S (GSA CRA001199) and ileum RNA-seq (GSA CRA001200) raw reads; DEG tables | Glucose, OGTT, insulin and HOMA-IR values, which appear only as figures; per-animal data |
| ToxCast invitrodb (local `comptox` store, CC0) | Berberine chloride: 128 active AC50s and 379 inactive results | — |

## Results

Protocol sha256:94f9536e706f…, status **exploratory**.

| Contrast | Metric | Means | Ratio (90% CI) | Verdict |
| --- | --- | --- | --- | --- |
| GQD vs HL | AUC_last (h·ng/mL) | 389.12 vs 197.98 | 1.97 (1.40–2.76) | increased |
| GQD vs HL | Cmax (ng/mL) | 28.2 vs 76.65 | 0.37 (0.25–0.55) | decreased |
| GQD vs GQD-GC | AUC_last (h·ng/mL) | 389.12 vs 148.97 | 2.61 (1.80–3.79) | increased |
| GQD vs GQD-GC | Cmax (ng/mL) | 28.2 vs 32.28 | 0.87 (0.58–1.32) | inconclusive |
| GQD-GC vs HL | AUC_last (h·ng/mL) | 148.97 vs 197.98 | 0.75 (0.54–1.05) | inconclusive |
| GQD-GC vs HL | Cmax (ng/mL) | 32.28 vs 76.65 | 0.42 (0.31–0.57) | decreased |

ToxCast: 128 active AC50s and 379 inactive results for berberine chloride.

| Group | Plasma Cmax (total) | Implausible | Depends on unknowns | Plausible |
| --- | --- | ---: | ---: | ---: |
| GQD | 28.2 ng/mL (83.8 nM) | 106 | 22 | 0 |
| GQD-GC | 32.28 ng/mL (96.0 nM) | 106 | 22 | 0 |
| HL | 76.65 ng/mL (227.9 nM) | 100 | 28 | 0 |

### 1. The formula changes berberine exposure, but "more exposure" depends on the metric

With 黄连 given at the same dose as in the formula:
- the whole formula roughly **doubles berberine AUC_last** (ratio 1.97, 90% CI 1.40–2.76);
- it **lowers Cmax to about a third** (0.37, 0.25–0.55).

Removing 甘草 from the tested preparation lowered AUC. The full-formula AUC was
2.6 times the AUC without 甘草. The interval comparing the preparation without 甘草
to 黄连 alone included 1 (0.75, 0.54–1.05). The Cmax reduction persisted without
甘草 (0.42, 0.31–0.57). These groups do not resolve contributions of individual constituents.

Together this points to slower, longer absorption or elimination rather than simply more
absorption. The half-life is 11.4 h with the formula against 9.3 h with 黄连 alone. AUC_last
also depends on the sampling window. Two of the protocol's competing explanations
("Cmax and AUC move in opposite directions"; "AUC_last reflects sampling and half-life")
are therefore live. The finding is consistent with the authors' report that glycyrrhizic
acid raises berberine permeability by inhibiting P-gp. It does not, on these summaries,
support "the formula raises berberine exposure" without naming the metric.

### 2. The plasma exposure screen depends on the assumed free fraction

The highest plasma Cmax is 228 nM total, with 黄连 alone; with the formula it is 84 nM.
For each of the 128 active AC50 readouts, the screen divides free rat plasma Cmax
by the in vitro AC50 over an assumed free fraction of 1–100%. The archived intervals
give the following counts.

| Group | Lower bound ≥ 1 | Upper bound ≥ 1 | Highest upper bound |
| --- | ---: | ---: | ---: |
| GQD | 0 | 1 | 1.051 |
| GQD-GC | 0 | 4 | 1.203 |
| HL | 0 | 15 | 2.856 |

No readout reaches its AC50 throughout the assumed range. At the hypothetical
100% free upper limit, one full-formula readout reaches its AC50, a cortical-network
spike measure. The upper bound for the full-formula CYP2D6 reporter is 0.119.
These results leave assay interference, free fraction and exposure at the relevant
site unresolved. Human drug interactions and gut-lumen effects require separate
evidence. The underlying values are in [the archived JSON](gqd_berberine.json).

### 3. The efficacy contrast has no prespecified equivalence result

The 2020 study's conclusion rests on non-significant differences between the formula and
berberine. For example, the reported comparison of microbiota structure gave adjusted P = 0.269, with
n = 6 rats per group. Under the contrast rules (`studies.contrast`), that is
*inconclusive*, not equivalent: no equivalence margin was set, and with six animals only a
large difference could have been detected. Testing it needs the per-animal efficacy
values (a request to the authors). The sequencing can be re-processed from GSA, but that
needs a 16S and RNA-seq pipeline and tens of GB of disk.

## What this changes

- The whole formula's distinct contribution, as these data show it, is
  **pharmacokinetic**: a 甘草-dependent increase in berberine AUC with a lower peak.
  Whether that changes efficacy is not tested by any data here.
- Mechanism claims for berberine that rest on plasma concentrations near or above
  1 µM are not supported at the exposures measured in rats at 1.5 times the clinical dose.
- Next: request the per-animal data of both studies. Measure or find gut-lumen and portal
  concentrations. Run the formula-vs-berberine efficacy contrast with an equivalence
  margin fixed in advance.
