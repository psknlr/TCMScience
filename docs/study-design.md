# Study design before tools: a bioinformatics research layer

> **概要（中文）** 这一层把"先选工具再找结论"改成"先定问题、单位、对照和可证伪条件，再判断数据能否回答，最后才运行分析"。
> - 疾病链与中药链分开记录。"疾病中改变的靶点"本身不能证明"某药通过它起作用"，桥接结论必须两条链都有证据。
> - 每个研究先过设计闸门（数据能否回答该问题），再过十项严谨性审计（独立单位、批次混杂、泄漏、重复研究、测量覆盖、真实结局、检验族、样本量、配对、同名不同方的干预）。
> - 所有统计都以患者为单位。
> - 在三个真实公开数据集上运行：
>   - GSE73661：发现元数据身份冲突；基线转录组预测内镜愈合的增益未达预设标准；愈合后黏膜的"残余差异"主要是未完全消退的炎症，而不是修复信号。
>   - GSE250498：把细胞当重复时 14 个细胞类型中有 13 个"显著"，按患者检验后无一通过 FDR；LCN2、DUOX2、S100A8 的差异点估计几乎全部落在"细胞状态"项，但每组只有 4 名对照，校准后的区间无法区分状态与组成，结论记为不支持（需更多患者）。
>   - HMP2：中心与诊断中度混杂；HBI 与 SCCAI 不能合并为一个结局。
> - 缺陷注入基准：门控流程检出 99% 的设计缺陷，在缺陷数据上的错误结论为 0；朴素流程为 66%。

The network-pharmacology and falsifiable-study layers ([falsifiable-studies.md](falsifiable-studies.md))
ask what an intervention may do. This layer asks the questions in order:
1. What question is being asked, about which independent units?
2. Against which contrasts, and what result would refute the hypothesis?
3. Can these data answer it at all?
4. Only then: which tool runs.

Everything is in `bioagent.studies` and runs on numpy alone. Reading `.h5ad` files needs
`h5py`, which only the GSE250498 script uses.

## Two chains, kept apart

| Chain | Example claim | What it needs |
| --- | --- | --- |
| disease | "LCN2 rises in UC epithelium through a change of cell state" | Patient-level contrasts in disease tissue |
| intervention | "a constituent lowers LCN2 in colonic epithelium at the exposure reached" | Perturbation at a measured or plausible exposure, in a relevant cell context |
| bridge | "the formula acts through LCN2" | Both of the above; no stronger than the weaker one |

`contract.assess_bridge` enforces the bridge rule:
- With no intervention-chain record, a bridge claim is `not_supported`. A disease target
  alone does not show that a herb acts on it.
- With docking or association-level intervention evidence, the bridge stays at `association`.

## The contract (`studies.contract`)

| Object | Holds | Why it is separate |
| --- | --- | --- |
| `OriginalDesign` | How the source data were made: randomised or not, what one sample is, pooled libraries, outcome definition | A fact about the source. It caps every claim (non-randomised → no causal claim) and cannot be changed by re-analysis |
| `StudySpec` | Question type (10 routes), chain, hypothesis, **falsifier**, competing explanations, primary contrast, unit of inference | The design of this analysis; `protocol()` gives a hash-lockable `StudyProtocol` |
| `CohortManifest` / `Sample` | One row per measured sample: subject, dataset, condition, time, batch, role, original study, attributes | Independence, pairing, leakage and duplication are checked from here |
| `ContrastSpec` | Variable, levels, unit (subject, never cell), pairing, test family, restriction | Contrasts are written before results exist |
| `OmicsArtifact` | Modality, level (cell/spot/sample), provenance (measured/imputed/predicted), feature space (a targeted panel must list its features) | A 1,000-gene panel is not a transcriptome, and a docking score is not a measurement |
| `ValidationPlan` | Discovery and validation sets, split unit, data used for selection, frozen selection fingerprint, test families, minimum units, time windows | Validation that touched selection is not validation |
| `ClaimRecord` | Statement, chain, level on a fixed ladder, evidence, and every downgrade with its reason | Claims only go down: `not_supported < exploratory < association < validated_association < experimentally_supported < causal` |

## The design gate (`studies.gate`)

`design_gate(spec, manifest, contrasts, artifacts)` returns `answerable`, `downgraded` or
`not_answerable`, with what is missing. Rules by question type:

| Question type | Not answerable without |
| --- | --- |
| composition_vs_state | Cell-level data with cell-type labels |
| spatial_neighbourhood | Coordinates and labels |
| inflammation_vs_repair | A measured severity (endoscopic or histological) |
| genetic_target | Tissue or cell-type QTL plus GWAS |
| isoform | Junction- or isoform-resolved RNA (3'-tag scRNA is flagged) |
| microbial_function | Metagenomes. Metatranscriptomes must come from the same collection; without them the question is downgraded to "potential only" |
| multiomics_discordance | Two measured layers on the same subjects |
| perturbation_transfer | Perturbation signatures, with baselines required |
| disease_specificity | Comparison diseases |
| response_prediction | A real outcome with two observed values, measured after the predictors |

For every question type the gate also counts independent units per arm:
- fewer than 2 per arm: not answerable;
- fewer than 5 per arm: downgraded to estimates only.

## The rigor audit (`studies.audit`)

| Check | Stops when | Downgrades when |
| --- | --- | --- |
| `independent_units` | A sample has no subject. Cells, spots or barcodes are the unit of a contrast. A pooled library is treated as a person | Samples are the unit and subjects contribute several |
| `identity_consistency` | Fields that name the person disagree | |
| `batch_confounding` | No batch contains both levels. Counted in people, not samples | Cramér's V at or above the threshold; a warning when every batch holds a single subject |
| `leakage` | A subject is in both discovery and validation. Validation data were used for selection | No frozen record of the selection |
| `duplicate_studies` | One original study, or one source sample, supplies both discovery and validation | |
| `coverage` | A panel is analysed against a genome background or used for a transcriptome-wide claim. Predicted data stand in for a measurement | |
| `outcome` | No outcome, or only one value | The outcome is derived from the predictors |
| `test_family` | | A tested contrast belongs to no prespecified family |
| `sufficiency` | Fewer than 3 units in an arm | Fewer than the plan's minimum (default 5) |
| `pairing` | Fewer than 3 complete pairs. `pairing_consistency`: declared pairs are no more alike on identity features (sex genes, genotype) than random pairs; exact enumeration when pairs are few, and a warning instead of a stop when too few to verify | |
| `intervention_identity` | One name, several compositions (four-herb 葛根芩连汤 vs the seven-herb trial formula) | |

`apply_audit` caps a claim:
- a stop makes it `not_supported`;
- a downgrade caps it at `exploratory`;
- a non-randomised source caps a causal claim at `validated_association`.

## Subject-level analyses

| Module | Function | Unit and safeguard |
| --- | --- | --- |
| `singlecell` | `pseudobulk` | Sums per sample × cell type, with a minimum number of cells |
| | `composition_test` | Per-subject counts. CLR difference with a Welch interval and a permutation p over subjects (sign-flips when paired; enumerated exactly when small, with the smallest attainable p reported and a warning when it exceeds 0.05), BH across cell types |
| | `state_test` | Per-subject log-CPM within one cell type |
| | `decompose_bulk` | Splits a tissue-level difference into composition (Σ Δp·ē) and state (Σ p̄·Δe), with subject-bootstrap intervals and a verdict (composition / state / both / unresolved). The within-arm covariance term is reported separately |
| `spatial` | `neighbourhood_enrichment` | Labels permuted within each section |
| | `compare_neighbourhoods` | Compares per-subject log ratios between arms, or within each subject when the contrast is paired |
| | `detectable_genes` | Gives the panel background |
| `validation` | `grouped_cv` | Leave-subjects-out, with selection inside folds and a subject-level permutation null. `sample_split_cv` exists only to show the leak |
| `longitudinal` | `match_layers` | Same collection, or within a tolerance in days |
| | `visit_pairs` | Uses windows fixed in the plan |
| | `next_visit_prediction` | Split by subject; must beat both *persistence* (the next visit equals this one) and the training mean, since persistence is weak when autocorrelation is low |
| `power` | `simulate_power` | Cells nested in people. Shows that more cells do not replace more people, and how far a cell-level test overstates |
| `workspace` | `create_workspace` / `verify_workspace` | The study directory below. A changed input makes the study exploratory |

### The study directory

```
study/
  protocol.yaml        question, hypothesis, falsifier, original designs, plan, artifacts, protocol hash
  cohort_manifest.tsv  contrasts.tsv  snapshot_lock.json  environment.lock (code commit, +dirty)
  qc/  discovery/  validation/  sensitivity/
  claims.json  provenance.json  limitations.md
```

Two real examples are in [studies/workspaces/](studies/workspaces/).

### `bio.*` study skills (`studies.skills`)

The eight proposed skills are written as contracts:
- `bio.cohort-audit`
- `bio.cell-composition-state`
- `bio.spatial-context`
- `bio.longitudinal-response`
- `bio.genetic-triangulation`
- `bio.isoform-context`
- `bio.microbial-function`
- `bio.perturbation-validation`

Each contract states its question type, what it needs, which analyses it calls, its claim
ceiling and what it refuses. Three of them (genetic triangulation, isoform, perturbation)
have no analysis yet. They run the gate and the audit and then stop.

The contracts are not pinned in `registry/skills.lock.yaml`. A pin requires a named human
approver, and none has signed them.

## Fault-injection benchmark (`studies.faults`, `scripts/eval_design_faults.py`)

Each task is a simulated single-cell study with known truth: patients nested in arms,
cells nested in patients, and a between-patient effect on every gene. Seven defects are
injected into null studies. Results are from 50 replicates per cell, seed 20261002
([studies/design_faults.md](studies/design_faults.md)).

| | naive pipeline | contract-gated pipeline |
| --- | ---: | ---: |
| false positives, clean null | 0.34 | 0.04 |
| power, clean positive | 1.00 | 0.92 |
| false alarms, clean studies (incl. paired) | 0.00 | 0.00 |
| defects detected | 0.00 | 0.99 |
| claims made on faulted null data | 0.66 | 0.00 |

| Injected defect | Caught by | Naive: claimed | Gated: detected |
| --- | --- | ---: | ---: |
| the same patient's cells in training and test | independent_units | 50/50 | 50/50 |
| shuffled pre/post sample sheet | pairing (identity features) | 1/50 | 47/50 |
| batch identical to condition | batch_confounding | 50/50 | 50/50 |
| targeted panel scored against a genome background | coverage | 50/50 | 50/50 |
| predicted interaction used as measured | coverage | 50/50 | 50/50 |
| two compositions under one formula name | intervention_identity | 15/50 | 50/50 |
| validation data used for selection | leakage | 15/50 | 50/50 |

The naive pipeline treats cells as replicates. That gives it 100% power and 34% false
positives. The gated pipeline tests patients: it gives up some power (0.92) and holds
false positives near the nominal 5%.

Three shuffled pairings out of 50 went undetected. In those, the random reassignment left
most pairs in place.

A generic LLM agent can be compared the same way, as a callable `study → {claim, flags}`.
None is included, because its result would depend on a model run the script cannot
reproduce.

## On real public data

### GSE73661: ulcerative colitis under vedolizumab and infliximab

Script: `scripts/study_uc_gse73661.py`. The data are 178 colonic biopsies on Affymetrix
HuGene 1.0 ST (RMA), from 78 people; GEO reports 79. The output is in
[studies/uc_gse73661.json](studies/uc_gse73661.json), and the two study directories are in
[studies/workspaces/](studies/workspaces/).

**Cohort audit:**

- **Who is behind each biopsy.** For 8 samples, the person named in the source text
  disagrees with the title and the characteristics field. Two controls are called "control
  individual 2" in the source text but 4 and 7 elsewhere. Six week-12 vedolizumab biopsies
  carry the patient number of a neighbouring row (e.g. `Bp1024`: title and
  characteristics say 37, source text says 75). Title and characteristics agree, so they
  are used, and the conflicts are recorded. The responder labels themselves agree with the
  Mayo subscores on every post-treatment biopsy.
- **Repeated biopsies.** 68 of 78 people contribute 2–3 biopsies, so samples are not
  independent units.
- **Collection period.** GEO records no hybridisation batch. The only proxy is the
  biobank-number range:
  - infliximab and 6 controls: 69–207;
  - 6 controls: 607–616;
  - vedolizumab and placebo: 771–1473.

  Pooling vedolizumab-healed biopsies against controls is therefore **stopped**: no period
  contains both.

**Study A, `response_prediction`.** Does the W0 transcriptome predict endoscopic healing
beyond W0 severity and therapy?
- **Design.**
  - 64 patients, 17 responders.
  - Response is taken at the first post-treatment R/NR biopsy.
  - Five-fold cross-validation, split by subject.
  - Top 50 genes selected inside each fold.
  - 200 subject-level permutations.
- **Results** (cross-validated AUC):

| Model | AUC | permutation p |
| --- | ---: | ---: |
| severity + therapy | 0.37 | 0.84 |
| expression (top 50) | 0.66 | 0.045 |
| expression + severity + therapy (primary) | 0.65 | 0.060 |
| *leaky:* all timepoints, sample split | 0.72 | — |
| *leaky:* all timepoints, subject split | 0.72 | — |

- **Verdict: `not_supported`.** The prespecified rule required the primary model to reach
  p < 0.05; it reached p = 0.06.
- **Severity carries no information here.** W0 Mayo subscores are almost all 2–3.
- **Leakage.** Using post-treatment biopsies inflates the AUC: a healed biopsy predicts
  "healed" because it *is* the outcome.

**Study B, `inflammation_vs_repair`.** Does mucosa healed after infliximab still differ
from controls, and is that repair or residual inflammation?
- **Groups.**
  - 8 healed patients: R and Mayo 0–1 at W4–6.
  - 11 controls.
  - 23 active W0 infliximab patients, used as the inflammation axis.
- **Residual genes.** 22,195 genes were tested; 229 differ at q < 0.05 with |log2 FC| ≥ 1.
- **Classification against the inflammation axis.**
  - **134 partly resolved inflammation.** Same direction as active disease, smaller.
    Examples: SLC6A14 at +6.4 in active disease and +3.9 healed; APOBEC3B; SLC22A5.
  - **91 exceed active disease.** 41 of these are snoRNA, histone or other non-coding
    transcripts. They are higher in both healed and active mucosa than in controls, which
    points at the controls rather than at biology.
  - **4 opposite to inflammation.** JCHAIN, APOBEC3A, MEF2C and PLA2G7, all lower in
    healed mucosa: the candidate repair signal.
- **Same-period sensitivity.** Using only the 5 controls from the infliximab collection
  period, 80 of the 229 genes survive: 49 partly resolved inflammation, 31 exceed active
  disease, and **0** of the 4 opposite genes.
- **Verdict: `not_supported` for a repair signal.**
  - What healed mucosa retains here is mostly unresolved inflammation.
  - The candidate repair genes do not survive controls from the same period.
- **Amendment.** The three-way classification and the same-period sensitivity were added
  after the first run. That first run had counted the snoRNA genes as "not explained by
  inflammation". The amendment is recorded in the output, and the claim is capped at
  exploratory.

### GSE250498: composition against state in UC mucosa, per patient

Script: `scripts/study_uc_gse250498.py`; output in [studies/uc_gse250498.json](studies/uc_gse250498.json).
It reads the processed GSE250487 biopsy object (`layers/counts`, 93,900 cells).

**Audit of the GEO sample rows.**
- The 10 GEO "samples" are lanes of one pooled 10x library.
- Every lane holds 10–11 of the same people, assigned by genotype (demuxlet).
- An analysis by GEO sample is **stopped**: a lane is not a person.
- The real units are 21 biopsies from 12 people: 4 controls (HC), 4 UC on vedolizumab
  (UCV) and 4 UC without biologics (UCNB).
- The gate **downgrades** the study to estimates only, with fewer than 5 people per arm.

**Composition** (UC vs HC, CLR per person, all 495 relabellings enumerated, 14 coarse cell types):
- Per person, no cell type passes FDR. The smallest q is 0.58.
- Treating the 93,900 cells as replicates makes **13 of the 14 shifts "significant"**.
- Vedolizumab vs UCNB (all 70 relabellings): the largest shift is fewer MNPs (CLR −0.92, p = 0.029, q = 0.20).

**Composition against state** for genes named before the counts were read:

| Gene | Composition term | State term | Verdict | Within-type state, per person |
| --- | --- | --- | --- | --- |
| LCN2 (UC vs HC) | 0.007 [−0.022, 0.037] | 0.040 [−0.021, 0.101] | unresolved | epithelium log2 +2.45, p = 0.016 |
| DUOX2 (UC vs HC) | 0.002 [−0.006, 0.009] | 0.009 [−0.018, 0.036] | unresolved | epithelium log2 +2.39, p = 0.057 |
| S100A8 (UC vs HC) | 0.000 [−0.007, 0.007] | 0.007 [−0.009, 0.024] | unresolved | MNP log2 +3.63, p = 0.007 |
| ITGA4 (UCV vs UCNB) | 0.018 [−0.040, 0.076] | 0.001 [−0.087, 0.089] | unresolved | CD4 T log2 −0.16, p = 0.44 |
| ITGB7 (UCV vs UCNB) | 0.014 [−0.033, 0.062] | −0.025 [−0.114, 0.065] | unresolved | CD4 T log2 −0.61, p = 0.14 |

- Intervals are the bootstrap SE over people × a t quantile on the smaller arm's degrees
  of freedom (3 here), each term at 97.5% so the verdict, which reads both terms, keeps
  5% overall.
- An earlier percentile-bootstrap version labelled the first three "state". An independent
  review showed it returned a verdict other than "unresolved" in about 22% of null data;
  it was replaced, and the null rate is now at most 5% (tested).
- For the three inflammation genes the point estimates sit almost entirely in the state
  term (LCN2: 0.040 vs 0.007), and the per-person within-type tests point the same way.
  With 4 controls the calibrated decomposition still cannot separate state from
  composition, so the claim is recorded as `not_supported`, pending more people.
- The vedolizumab target chains show no resolvable change.

**Power.** The observed per-person spread gives the following power for a plasma-cell
shift of the observed size:

| People per arm | Power |
| --- | --- |
| 4 | 0.19 |
| 8 | 0.48 |
| 16 | 0.81 |

The power is the same at 500 and at 5,000 cells per person.

### HMP2 / IBDMDB: what plan C can and cannot answer

Script: `scripts/audit_hmp2.py`; output in [studies/hmp2_audit.json](studies/hmp2_audit.json).
It reads only the public sample metadata.

- **People.** 130 people have metagenomes: 65 CD, 38 UC and 27 nonIBD.
- **Repeated samples.** 1,638 metagenomes, a median of 12 per person. A sample-level test
  would count each person about 12 times.
- **Site is confounded with diagnosis.** Counted in people, Cramér's V is 0.37 for CD vs
  nonIBD and 0.39 for UC vs nonIBD. Cedars-Sinai contributed 20 CD and 12 UC patients but
  1 nonIBD participant, so site must be adjusted for or stratified.
- **Same-stool layers.**
  - 815 collections have DNA and RNA from the same stool, covering 109 people.
  - 332 collections have DNA, RNA and metabolome, covering 102 people.
- **Disease activity is not one outcome.**
  - HBI is recorded for CD and SCCAI for UC; nonIBD participants have neither.
  - Fecal calprotectin covers 456 of 1,638 samples.
  - A pooled "active vs inactive" outcome is **stopped**.
- **Next-visit pairs.** With a 7–35-day window fixed in advance:
  - 1,268 metagenome pairs from 111 people;
  - 422 metatranscriptome pairs from 96 people.
  Persistence and the training mean are the baselines to beat.

### Not fetched, and why

- **SCP259** (Smillie et al., UC atlas, Broad Single Cell Portal): downloads need a sign-in.
  The harness does not log in on the user's behalf.
- **Raw sequencing reprocessing:** out of scope. There is no aligner or R in this
  environment and about 7 GB of disk. Only processed, deposited matrices are read.

## Where the nine routes stand

| Route | Status |
| --- | --- |
| Cell composition vs state | **Run** on GSE250498: no composition shift passes FDR per patient; state vs composition for inflammation genes not resolvable with 4 controls |
| Spatial neighbourhood | Per-patient comparison implemented; a processed-data spatial workflow (`bioagent.spatial`) is validated on public Visium data against Squidpy ([spatial-transcriptomics.md](spatial-transcriptomics.md)). The GSE250498 spatial subseries is not yet read |
| Inflammation vs repair | **Run** on GSE73661: residual change is mostly unresolved inflammation |
| Tissue-specific genetic targets | Contract and gate only; needs eQTL and colocalisation inputs |
| Splicing / isoforms | Contract and gate only (3'-tag data flagged) |
| Microbial function layers | HMP2 audit done; feature tables not yet read |
| Multi-omics inconsistency | Layer matching done on HMP2 |
| Perturbation extrapolation | Contract and gate only |
| Cross-disease specificity | Gate only |

The next steps follow the proposed order:
1. Read the HMP2 functional profiles (plan C).
2. Add eQTL Catalogue colocalisation (plan B), mapped to the TCM intervention chain
   through `assess_bridge`.
3. Run a blind agent evaluation on the fault benchmark.
