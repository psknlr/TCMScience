# Proposal: the four pinned TCM skills on production data, through review and re-pinning

Status: **proposed, awaiting a maintainer's decision.** Nothing here has changed a pin:
`registry/skills.lock.yaml` and the four skills' code are as reviewed, and
`scripts/check_lockfile.py` passes ("4 skill(s) pinned and matched").

## Why

The four governed skills (`normalize-tcm-entities` 1.0.0, `retrieve-tcm-evidence` 1.1.0,
`analyze-tcm-network-pharmacology` 1.1.0, `assess-tcm-safety` 1.0.0) read only the
compiled seed corpus (`bioagent.tcm.knowledge`: 23 herbs, 6 formulas, 8 syndromes, 9
passages). The corpus is declared in every artifact and labelled as illustrative, which is
honest, but it caps what a governed run can do:

- `analyze-tcm-network-pharmacology` finds no molecular target for any of the six formulas
  (the corpus records none), so it correctly claims nothing, every time.
- `assess-tcm-safety` on 甘草 names 甘遂 and not 大戟, 海藻 or 芫花: they are not among the 23
  herbs (found by regression scenario 11, `docs/validation.md`).
- `normalize-tcm-entities` cannot resolve a name outside the corpus, though the materia
  table and the 84,294-row formula workbook are in the repository.

Studio works around these without touching the pinned code: the complete workbook and
materia table behind `tcm_formula`/`tcm_lookup`, the 十八反/十九畏 rule table beside
`tcm_safety_report`, the data hub's measured relations, and the research loop on
snapshots of the real files (`job.research.run`, the benchmark). That keeps governance
intact; it does not make the governed skills themselves production-ready.

Changing a skill's code changes its content hash (manifest plus import closure), and a
governed run refuses a skill whose hash is not the pinned one. The change has to go
through the candidate path and a person's `PromotionDecision`; it is not something this
change set may do on its own.

## What each skill would gain

Every skill keeps the seed corpus as a selectable, labelled demonstration source and adds
an explicit `data_source` argument. No source is reached over the network; everything is
read from local, verified files under paths the manifest declares read-only.

| Skill | `data_source` | Reads | What changes in the output |
|---|---|---|---|
| normalize-tcm-entities 2.0.0 | `seed` (default), `materia`, `workbook` | `sources.materia` (already in the closure), the formula workbook through `sources.formulas` (with its errata) | a resolution names the corpus that produced it; a workbook name with several versions is ambiguous, with its books |
| retrieve-tcm-evidence 2.0.0 | `seed`, `hub` | built hub datasets (`tcmdb`), read-only | evidence items from hub relations keep their source, record id, licence and evidence class (measured, predicted, listed) |
| analyze-tcm-network-pharmacology 2.0.0 | `seed`, `snapshots` | research snapshots verified through the ledger (`sources.snapshot.load_snapshot`) | measured targets from `analysis.network_pharmacology`; claims stay `mechanism_hypothesis`, each citing measured edges; `predicted_targets` stays a separate key |
| assess-tcm-safety 1.1.0 | `seed`, `rule_table` | the clinic pack's 十八反/十九畏 table | the table's pairs as records of tier `expert_experience`, labelled with the pack's review status |

Manifest changes: `permissions.filesystem_read` gains the declared data roots
(`${snapshot_root}`, `${ledger}`, `${tcmdb_root}`, `${formula_table}`), nothing writable,
no network, no subprocess. `evidence.claim_kinds` and `forbidden_claims` are unchanged.

## Before activation

A candidate is refused by a governed run until promoted, and runnable as a development
run. Each revised skill would:

1. live under `skills/candidates/tcm/` with a **new id** while it is a candidate (the
   lockfile check refuses a candidate that shares an id with a stable skill), e.g.
   `analyze-tcm-network-pharmacology-data`;
2. pass its unit tests and the existing P0 tests with `data_source: seed` byte for byte
   (the seed behaviour is the regression baseline);
3. pass, with its data source, the benchmark stages it covers
   (`bioagent validate benchmark --live`: stages 3–7 and 10 for network pharmacology) and
   regression scenarios 1, 4 and 11 (`tcmstudio validate`);
4. be benchmarked against the frozen Season (`scripts/benchmark_candidate.py run/score/verify`);
5. be promoted only by a recorded `PromotionDecision` (`bioagent.updates.registry`), whose
   `candidate_digest` is the reviewed candidate's digest, after which the lockfile is
   regenerated from the registry and `approved_by` names the person; the previous version
   stays as `rollback_version`.

## What is not proposed

- No change to the claim-licensing rules, the kernel's policy, or the evidence tiers.
- No automatic promotion: `scripts/check_no_autopromotion.py` keeps refusing any run that
  writes the lockfile.
- No remote data source inside a governed skill: acquisition stays in the data hub and the
  snapshot pipeline, where it is verified and recorded (`docs/validation.md`).
