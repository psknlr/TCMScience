---
name: network-pharmacology
description: Network pharmacology of a TCM formula on verified source snapshots — composition, measured targets, Reactome enrichment with a degree-matched null, descriptive STRING topology — ending in mechanism hypotheses that pass the release check.
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
  recorded in the ledger. The run loads every snapshot through the ledger and refuses one
  that no longer matches it.
- A formula bound to one recorded composition (`sources.herbs.FormulaVersion`); the
  shipped one is 葛根芩连汤 as the 伤寒论 records it.

## How to run

    python scripts/run_network_pharmacology.py --snapshots DIR --ledger DIR/audit/snapshots.jsonl --out RUN_DIR

## Steps (`skill.yaml`)

1. `composition` — formula -> herbs -> source species -> compounds, with composition
   levels (C1 in species, C2 in the medicinal part; nothing reaches C3/C4 from bulk data).
2. `targets` (in vitro) — compound -> protein with IC50/Ki/Kd/EC50 at or below 10 µM.
3. `enrichment` (in silico) — Reactome over-representation against the full human
   background, BH over every tested pathway, then a degree-matched permutation null.
4. `network` (in silico) — STRING subnetwork at confidence >= 0.7, descriptive only.

The PSH compiler checks these designs against the claim: the program compiles for
`mechanism_hypothesis` and is refused for `mechanism` (EVIDENCE103).

## What it produces

`compounds.tsv`, `targets.tsv`, `enrichment.tsv`, `network.tsv`, `claims.json` (each claim
cites the snapshot edges of one supporting path and records the strongest kind that path
could license), `release.json`, `provenance.json` (snapshot ids, parameters, seed, code
digest, PSH program fingerprint, result digest) and `limitations.md`.

## How to read the result

Every claim is a hypothesis. Read `limitations.md` first: composition is species-level,
targets are what happened to be measured, enrichment reflects screening panels as well as
biology, and no disease gene set has been joined yet — the claims are about pathways, not
about the indication.
