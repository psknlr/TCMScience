# Figure legends

Legends for Figs. 1–7, in the order and form the figures take in the manuscript. Every number
is the one drawn; each is traced to its source in `source_data/` and `data/`.

---

**Fig. 1 | TCMScience separates what a language model may propose from what the system may
release.** **a**, Architecture, each component named as in the code. The language model
proposes plans, explanations, predictions and claims, and holds no authority. The trusted
kernel (PSH) labels every value at ingress; compiles a program in eight passes (71 diagnostic
codes) into a typed plan; checks the whole plan against an authority that can only narrow; runs
it in a bounded loop whose only actions are model, tool and delegation calls through one
ExecutionBroker; and releases output along one path (quarantine, output gate, release gate),
recording every gate's decision on a hash-chained audit. A scientist plane keeps hypotheses,
preregistered protocols and observations as content-hashed records and runs the inquiry engine
(Fig. 7). The governance layer constrains the kernel and never executes. The capability plane
is admitted through one bridge. Solid arrows, execution; dashed arrows, constraint. **b**, The
117 public sources the connectors reach, in 29 domains, with the sources and typed operations
in each (TCM highlighted). All 444 operations were verified against the live services between
17 September and 7 October 2026. **c**, Data enter as audited snapshots. Each provider release,
under a source card stating its licence and access paths, is parsed, normalized to shared
identifiers, passed through a quality gate that fails closed, content-hashed and recorded on a
ledger verified on load. Below, the eight source cards and the role each played in the 葛根芩连汤
study (Fig. 5); vermillion, no licence stated on the card. A run may use only the sources its
skill declares, the registry admits and its policy allows; its lock names every snapshot.
**d**, Left, the catalogue of 136 TCM databases by how each can be reached (dark, live; light,
as files; grey, not at all), and the federated catalogue of 2,567 capabilities from 16 projects
by kind (filled, the licence permits vendoring; open, invoked in place only). Right, the 150
native tools by domain (TCM knowledge highlighted). All names and counts are read from the code
at the commit the figures were built from.

---

**Fig. 2 | Study designs are types: what each kind of evidence licenses, and where it is
enforced.** **a**, The strongest grade each study design can reach for each claim kind (the
kernel's matrix, `psh.sir.values.LICENSING`): licensed directly (filled), only by an
extrapolation the claim must declare (open), or not at all (dot). Designs are deliberately
unordered: a classical text is the only design that licenses an attribution directly, and it
cannot license efficacy. An in-silico design (docking, network pharmacology, simulation)
licenses a mechanism hypothesis and nothing stronger (shaded row). Bars, the strongest wording a
single study of that design licenses; no design licenses definitive wording. Building the figure
checks that every pair the compile-time table admits (`psh.workflow.compiler._SUPPORTS`,
EVIDENCE103) is a direct cell of this matrix. **b**, One rule, enforced at four points: when a
plan is compiled (EVIDENCE103 and the type rules, TYP), when the inquiry engine updates belief
(INQ113), when a claim cites its evidence (CLM005), and at release, where every edge on the
claim's path must exist in a verified snapshot. **c**, Fifteen claims drafted in three end-to-end
chains and the claim contract's verdict on each: filled, allowed; open with a cross, refused, with
its codes. Cases 1 and 2 were re-run for this figure (built-in DESeq2 and GSEApy 1.3.1;
PaperQA2 2026.8.12) and returned the recorded verdicts; case 3 needs AutoDock Vina, which was not
installed, and is shown as recorded on 7 October 2026. The RNA-seq experiment is simulated, two
of the three literature abstracts are fixtures and the docking target is a bovine model: the
cases test the chain, not a biological claim. Case 2's first four claims were drafted in Chinese
and are glossed.

---

**Fig. 3 | Each layer of the governance stops different scientific errors.** **a**, Mutation
testing. Ten outputs a reviewer would release (four citing randomized trials, two cohort
studies, a case report, a bench assay, a docking run and a passage of the 伤寒论 *Shanghan
Lun*; six in Chinese, four in English) were each altered by one known scientific error at a
time, giving 121 mutants in 26 error classes. Five gates run as the runtime calls them: three in
the kernel (ingest, which binds identifiers and re-checks signatures; the output gate, which
checks each sentence's support, scope, citations and quotations; and licensing) and two in the
domain layer (the claim contract, CLM001–CLM019, and the release path of snapshot edges). An
output is released when no enabled gate refuses it. **b**, Erroneous outputs released by each of
the 14 configurations: points, proportion; bars, 95% Wilson score intervals; n = 121 mutants each.
No configuration refused any of the ten correct outputs. **c**, Share of each error class's
mutants refused by each gate alone, each layer and the full stack; n per class in parentheses;
intermediate shares printed. **d**, Errors that only one gate stops: mutants the full stack
refuses and releases once that gate alone is switched off. **e**, Errors the full stack released
as the benchmark exposed gaps, each closed by a change to a runtime check rather than to the
benchmark: 19 of 111 mutants at round 1, 10 of 111 after it, and 0 of 121 after round 2 and the
TCM-specific checks CLM015–CLM019, whose operators added 10 mutants (bars, 95% Wilson
intervals). The cases and operators were written by the authors of the gates, so these are
regression measurements of a stated construction, not field error rates.

---

**Fig. 4 | The study-design labels the types rest on agree with PubMed for 97% of the designs
read.** **a**, Protocol. A stratified sample of 478 open-access abstracts (CC BY or CC0; 430
English, from 2022, and 48 Chinese) was frozen before any rule read it and split by a seeded hash
of each PMID; the rules were revised on the dev split only, and the test split was read once by
both versions. **b**, Test split (302 abstracts): the reference (PubMed publication type; MeSH
headings for animal and in-vitro studies) against the design the rules read; outlined cells are
correct readings, which for the 57 negatives (narrative reviews, editorials, trial protocols)
means abstaining. **c**, Precision and recall for each design on the test split, before (open)
and after (filled) the revision; bars, 95% Wilson intervals; numbers, counts after the revision.
**d**, Share of readings correct, studies typed and negatives typed, before and after the
revision, on test (n = 302; bars, 95% Wilson intervals) and dev (n = 176). Dev improved more than
test, as tuning on dev predicts. **e**, Population, comparator and outcome: the share of test
abstracts in which a rule fills each, and 30 filled values drawn by a seeded hash and read in their
abstracts by one reader, the author of the rules, neither blinded nor a domain expert: 23 correct
(77%; 95% CI 59–88%), 5 partly correct, 2 wrong. The reference is an indexer's label rather than
the truth, and the sample is stratified, so the precision holds for this mix and not for the
literature at large.

---

**Fig. 5 | On public data, the network-pharmacology signal of 葛根芩连汤 reflects which proteins
were assayed, and what survives is a herb–drug-interaction signal.** **a**, Data. The four herbs
of the formula as recorded in the *Shanghan Lun*, with the marker compound recovered for each in
all three composition sources; curated potencies (IC50, Ki, Kd or EC50, any value); and PubChem
BioAssay results with inactive results kept, restricted to definitive results on human Swiss-Prot
proteins (23,230 results whose gene identifier maps to no unique primary accession were discarded
and counted). **b**, Left, pathways significant after Benjamini–Hochberg correction and a
degree-matched permutation null, against the whole human Reactome annotation and against the
proteins actually assayed. Right, the carbonic-anhydrase (CO2 hydration) pathway, all 12 of whose
members were assayed and 11 hit: fold enrichment under each background, computed from the counts;
dashed line, the largest fold any pathway can reach on the assayed background (391/237). **c**,
Overlap of the 237 measured targets with type 2 diabetes genes (Open Targets 26.09) under five
definitions of the disease gene set, and on the assayed background (square). P, one-sided
hypergeometric, recomputed from the counts and equal to the reported values. Literature
co-mention is circular: compound–target data and text mining study the same well-studied
proteins. **d**, Share of tested constituents active against each protein in the screening data
(points; bars, 95% Wilson intervals; counts at right); dashed line, all 472 proteins tested
against at least 20 constituents (3,357 of 51,304 tests active). **e**, Pathways passing
Benjamini–Hochberg (q ≤ 0.05) and 10,000 permutations at three minimum numbers of constituents
tested per protein; q where the source reports it; colour, the protein family that carries the
pathway. The values are those the repository documents (cited line by line in Source Data); the
snapshots themselves are not redistributed.

---

**Fig. 6 | From network pharmacology to questions the data can answer no.** **a**, The three
mechanism hypotheses released by the baseline screening analysis (assayed background; 10,000
permutations; seed 20261001; 58 pathways tested in that run), re-run with each of the
12 largest assay campaigns held out and with all cytochrome P450 panel screens held out together
(4,161 edges). Without those panels two pathways cannot be tested at all, because their proteins
enter the background only through them. **b**, Berberine exposure in rats given the whole formula
(18.9 g/kg), the formula without 甘草, or 黄连 alone at the dose the formula contains (n = 10 per
group): ratios of published group means with 90% confidence intervals by the delta method on the
log scale (Welch–Satterthwaite degrees of freedom). Verdicts follow rules fixed in a hash-locked
protocol: increased if the interval lies above 1.25, decreased if below 0.80, otherwise
inconclusive (shaded, 0.80–1.25). The protocol records itself as exploratory, because the primary
contrast was computed before it was locked. **c**, Whether berberine's ToxCast activities are
reachable at the plasma concentrations measured. The ratio R = C × f_u / AC50 is computed with the
unbound fraction f_u unknown, from 0.01 to 1: an activity is plausible if R ≥ 1 across that whole
range and implausible if R < 0.1 even at f_u = 1. Shaded bands, for each group, the AC50 values at
which the verdict depends on f_u; points (jittered vertically), the 28 active AC50s not ruled out at
the highest exposure; counts at right cover all 128 actives. No activity is plausible in any group.
No gut-lumen concentration was measured, so a mechanism in the gut is not addressed. **d**, The
eight routes from network pharmacology towards falsifiable studies, and the status of each;
filled, run on real data in this figure.

---

**Fig. 7 | Deciding what to find out next: the inquiry engine on three planted worlds.**
**a**, Division of labour. The model proposes rival explanations, with a catch-all whose prior
cannot fall below 0.05; the analyses and their cost; and what each explanation predicts each
analysis will show, sealed before the analysis runs. The kernel chooses the next
analysis by expected information per network-pharmacology run, updates belief from the sealed
predictions alone, holds fixed any explanation whose claim kind the analysis's design cannot
license (INQ113), accepts a leader only at a posterior of at least 0.95 after a replicated severe
test against every live rival, and caps the conclusion by the licensing table rather than by the
posterior. **b**, The sealed predictions for the question of why the formula's measured targets
concentrate in a pathway: selective action (a mechanism hypothesis), assay coverage or promiscuous
chemistry (both artefacts). Right, the information each analysis is expected to yield before any
run, in total and per run. The usual analysis, enrichment against the whole annotation, is worth
0.06 bits, because every explanation predicts that it comes out enriched; the assayed background
is worth 0.43. **c**, Belief after each step in three synthetic worlds built on the real
葛根芩连汤 herb layer, each making one explanation true; under the axis, the outcome observed;
dashed line, the acceptance threshold (0.95).
Where two posteriors coincide exactly, the one beneath is drawn as a casing so both stay visible.
**d**, The engine's options at each step: expected bits per run of every analysis still runnable
(shade) and the one it ran (outlined); dots, analyses already run. Each world's true explanation
was accepted (posterior 0.974–0.976). A mechanism hypothesis, stated tentatively, was released
only in the selective world, and the coverage world stopped after 14 of 67 network-pharmacology
runs. The worlds are fixtures with a known answer: they show the mechanics,
not that the engine decides real questions well; every posterior is conditional on the sealed
likelihoods.
