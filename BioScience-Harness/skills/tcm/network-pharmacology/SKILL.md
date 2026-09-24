---
name: network-pharmacology
description: Network pharmacology of a TCM formula on verified source snapshots — composition, measured targets, Reactome enrichment with a degree-matched null, descriptive STRING topology, overlap with the indication's genetic-association genes — ending in mechanism hypotheses that pass the release check.
license: MIT
metadata:
  version: 0.1.0
  tags:
    - tcm
    - network-pharmacology
---

# Network pharmacology (TCM formula)

## When to use

To ask which biological pathways the constituents of a formula *might* act through, as a
hypothesis to test. Not for efficacy, safety or dosing questions: this skill cannot make
those claims, and its contract (`skill.yaml`, `max_claim_kind: mechanism_hypothesis`)
says so to the runtime.

## What it needs

- Source snapshots built with `scripts/build_source_snapshots.py gold --network --ledger ...`
  (herb layer, NPASS, CMAUP, LOTUS, STRING, the full human Reactome annotation), each
  recorded in the ledger, plus the indication's Open Targets associations
  (`scripts/fetch_opentargets.py MONDO_0005148 --raw RAW`, then
  `scripts/build_source_snapshots.py opentargets --file ... --ledger ...`). The run loads every snapshot through the ledger and refuses one
  that no longer matches it.
- A formula bound to one recorded composition (`sources.herbs.FormulaVersion`); the
  shipped one is 葛根芩连汤 as the 伤寒论 records it.

## How to run

    python scripts/run_network_pharmacology.py --snapshots DIR --ledger DIR/audit/snapshots.jsonl --out RUN_DIR

## Steps (`skill.yaml`)

1. `composition` — formula -> herbs -> source species -> compounds, with composition
   levels (C1 in species, C2 in the medicinal part; nothing reaches C3/C4 from bulk data).
2. `targets` (in vitro) — compound -> protein with IC50/Ki/Kd/EC50 at or below 10 µM.
3. `enrichment` (in silico) — Reactome over-representation, BH over every tested pathway,
   then a degree-matched permutation null. The default background is the *assayed*
   proteins (every human protein the compounds were measured against, potent or not), so
   a pathway cannot stand out merely because its proteins sit on a screening panel.
   `--background reactome` tests against the whole human annotation instead; read that as
   where measured activity lands, confounded with what was tested.

   `--hits screening` uses PubChem BioAssay instead of curated measurements: every
   depositor's active *and inactive* call for the formula's compounds
   (`scripts/fetch_pubchem.py`, then `build_source_snapshots.py pubchem`). Counted per
   protein, screening hits saturate, so the test is on the rate: of the compound-protein
   tests on a pathway's proteins, what fraction were active, against random protein sets
   of the same size. Only proteins tested against at least 20 of the compounds take part.
4. `network` (in silico) — STRING subnetwork at confidence >= 0.7, descriptive only.
5. `disease` (in silico) — the measured targets against the indication's gene set, from
   Open Targets *genetic association* scores >= 0.5 (not literature co-mention, which would
   be circular). Hypergeometric plus the degree-matched null; a statistic, never a claim.

The PSH compiler checks these designs against the claim: the program compiles for
`mechanism_hypothesis` and is refused for `mechanism` (EVIDENCE103).

## What it produces

`compounds.tsv`, `targets.tsv` (with each target's disease score), `disease.json`, `enrichment.tsv`, `network.tsv`, `claims.json` (each claim
cites the snapshot edges of one supporting path and records the strongest kind that path
could license), `release.json`, `provenance.json` (snapshot ids, parameters, seed, code
digest, PSH program fingerprint, result digest) and `limitations.md`.

## How to read the result

Every claim is a hypothesis. On 葛根芩连汤 the default run releases **none**: the 66 pathways
enriched against the whole annotation are all explained by which proteins were assayed.
The assayed background has little power in turn (61% of assayed proteins are recorded as
potent hits, because databases rarely record inactive results), so "not enriched" is not
"irrelevant". Read `limitations.md` first: composition is species-level,
targets are what happened to be measured, enrichment reflects screening panels as well as
biology, and the disease overlap describes the target set — the claims are about
pathways, not about treating the indication.
