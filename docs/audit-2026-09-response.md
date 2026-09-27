# Response to the September 2026 external audit

The audit found ten issues (F01–F10) and supplied an offline reproduction script
with eleven probes. The first change (#22) fixed the deterministic defects (F01–F09)
and put one trust boundary in front of release. The second change carries a question
end to end on real, non-seed data (F10). Neither change rewrites either package. Every probe that
used to be accepted is now refused, and each one is pinned by a regression test.
Before the change the script reported every probe as allowed. After it, the script
reports the following:

| Probe | Before | After |
| --- | --- | --- |
| `scope_expansion` (evidence in "adults with hypertension" → claim about "adults") | allowed | refused, `CLM009` |
| `text_kind_mismatch` (efficacy wording under `claim_kind=attribution`) | publishable | refused, `CLM011` |
| `self_attested_quote` (`quote_verified=True`, empty `content_hash`) | publishable | refused, `CLM010` (no receipt) |
| `unlinked_cited_evidence` (`source_card_id=""`) | publishable (warning) | refused, `ART111` |
| `missing_output_and_attestation` | publishable | refused, `ART113`; also not attested |
| `transitive_hash` (edit an imported helper) | hash unchanged | hash changes |
| `cli_missing_skill_directory` | ran, exit 0 | refused, exit 2 |
| `cli_single_name` (`names=黄芪`) | two queries, 黄 and 芪 | one query, 黄芪 |
| `zero_scientific_dimensions` | aggregate 1.0, trusted | aggregate `null`, not trusted (`NOT_EVALUATED`) |
| `empty_run` | trusted | not trusted (`NO_CASES`) |
| `path_semantics` (activates / inhibits / reversed) | 3 released | 0 released |

## By finding

**F01, population coverage direction.** `_covers` accepted a covered text that
*contained* the asserted one, so evidence about a narrower population licensed a
claim about a broader one. Coverage is now decided only in two cases: exact
agreement, or membership in an explicit enumeration (`adults; children`). No
qualified phrase is decided from its wording in either direction, so every other
case becomes an undeclared extrapolation (`CLM009`). The existing test encoded the
wrong direction and has been replaced. Still open: a declared extrapolation is not
yet distinguished from a *validated* one, and the population is still free text
rather than a structured PICO fact fixed by an evidence reviewer.

**F02, self-declared verification.**
- *Quotes.* A quote now counts as verified only if it has a receipt: the SHA-256
  of the content it was found in plus its character offset. The only way to get a
  receipt is `EvidenceItem.located_in(content)`, which does the search itself. The
  located content goes into a content-addressed `ContentStore`, so
  `validate_artifact(..., content_store=)` can check the bytes at that offset
  again. If they differ, the artifact is refused with `ART115`. The four P0 skills
  now issue receipts instead of setting the flag.
- *Wording versus kind.* The new module `contracts/claim_language.py` flags
  efficacy, normative, certainty and universal-population wording that the
  declared `claim_kind` does not license. It works in English and Chinese, and it
  skips a phrase that is negated nearby. A flagged claim is refused with `CLM011`.
  The same check runs on release statements.
- *Limits.* The check is lexical, so a paraphrase can defeat it. A receipt proves
  that the quote was located in content registered in the store. It does not prove
  that this content came from the pinned snapshot. A verifier that reads the
  snapshot itself is still needed.

**F03, `publishable` is not verified.** Cited evidence with no source card is now
an error (`ART111`), no longer a warning. An output is refused if it is missing
(`ART113`) or if its bytes do not match its hash (`ART114`). An absolute path is
always checked. A relative path is checked under `output_root`, or against the
content store. The verdict now reports four separate states:
`schema_valid` (the same as `publishable`), `evidence_verified`,
`outputs_verified` and `execution_attested`. `release_authorized` is true only
when all four are true. `json_file` used to hash a compact rendering and report the
size of an indented one, so no file on disk could ever match its own hash. It now
hashes the exact bytes it returns, and `write_outputs` writes only content whose
hash matches.

**F04, graph connectivity versus meaning.** A release path now has to follow the
direction in which each edge was recorded. A statement that names a direction of
effect (activates, inhibits, 抑制 …) needs an evidential edge that records that
direction. A path through a `tested_against` edge (measured and found inactive)
supports no effect at all. Overreaching wording is refused here as well. Still
open: typed proof rules for each relation, and the species, concentration and
tissue conditions of each edge.

**F05, aggregation.** A dimension that was not measured is now `None`, where it
used to default to `0.0`. Because latency and cost are inverted before scoring, a
default of `0.0` counted an unmeasured latency or cost as perfect. The harmonic
mean now includes zeros, so any zero makes the aggregate zero. If any weighted
dimension was not measured, the aggregate is `null` and the run gets the
`NOT_EVALUATED` gate. A run with no cases, an errored case, or an unscored case is
not trusted. Proportions above 1.0 and NaN are rejected. A monotonicity test
checks every dimension. Still open: scores should be recomputed from raw traces
and gold labels rather than accepted from the caller.

**F06, a single governed entry.** The new module `bioagent/governed.py`
(`run_governed`) is now the only path the CLI uses. It does the following, and
each step can refuse the run:
1. Loads the manifest from `--dir`, and refuses a missing directory or a skill
   that is absent from it.
2. Checks that the manifest entrypoint is the callable that will actually run.
3. Checks the skill's content hash against `registry/skills.lock.yaml`.
4. Runs the skill under a PSH `TrustedKernel` and appends a start event and a
   validation event to its hash-chained log.
5. Checks the chain, then stamps the artifact with the policy id and the chain
   head.
6. Validates the artifact against the written outputs and the content store.

The CLI exits 0 only when `release_authorized` is true. In `analysis/skill_runner.py`,
a failed PSH import used to be ignored silently. It is now a refusal, unless the
caller passes `require_psh=False` (`--allow-ungoverned`); provenance then records
`governed: false`. Still open: the network-pharmacology analysis still runs beside
its compiled PSH program rather than *as* that program.

**F07, argument typing.** `coerce_arguments` reads each skill's signature. A
sequence parameter always receives a list, split on `,` `，` `、` `;` `；`. Integers,
floats and booleans are converted. An unknown name or a bad value is refused.

**F08, the skill hash.** `skill_content_hash` now covers the entry module and its
*local import closure* (`skill_dependency_closure`), including the `__init__`
files of enclosing packages. Files are named relative to the package root, so the
pin does not depend on how the caller spelled the path. The consequence is that a
change to the shared contract layer moves every P0 skill's pin. That is correct,
because every skill's behaviour changed, and the lockfile has been regenerated.
Still open: a separate environment or dependency digest for third-party packages.

**F09, bridge defaults.** `BioScienceBridge` now isolates by default whenever it
has somewhere to write the child's manifest. In-process execution requires an
explicit `isolate=False`. A failed audit write now refuses the admission
(`strict_audit=True`); earlier versions swallowed the error. Still open: named
deployment profiles (`trusted_local` / `restricted_research` / `sensitive_data`).

**F10, a closed loop on real data.** A new package, `bioagent.research`, carries a
question end to end on snapshot data, outside the seed corpus. The CLI entry point is
`python -m bioagent.cli research "<question>" …`. The run has five stages.

1. **Protocol.** The question is parsed into a formula and, optionally, a disease id.
   A question that names no known formula is refused. The protocol is then frozen
   and its digest is written to the PSH audit chain before any data is read. It
   records the sources, the analysis parameters, the rebuttal tests and the allowed
   fallbacks.
2. **Retrieve.** Each source is loaded through the snapshot ledger.
3. **Analyse.** The network-pharmacology analysis runs with the protocol's parameters.
4. **Rebut.** Every released pathway must still be released under three
   pre-registered tests:
   - the assayed-protein background;
   - leaving out one activity source at a time;
   - two other permutation seeds.

   A rebuttal that cannot run, such as leave-one-out with a single activity source,
   is recorded as a limitation. It is never counted as passed.
5. **Release.** Each cited activity edge becomes an evidence item. Its quote is the
   edge row as the snapshot stores it. Receipts are re-checked against a content
   store rebuilt from the verified snapshots, which also closes F02's open item of
   binding receipts to a snapshot. The artifact is then attested and validated
   together with its written outputs.

Repair is bounded:
- A missing optional source is dropped only when the protocol allows that fallback,
  and the drop is recorded as a deviation.
- A missing required source refuses the run.
- A snapshot that fails its hash check is never worked around.

Every stage is checkpointed, and a re-run with the same state directory resumes from
the last completed stage. Retrieved snapshot ids are re-checked on every resume, and a
damaged checkpoint is recomputed rather than trusted. `tests/test_research_loop.py`
covers each of these paths.

*Real data* (NPASS 2.0 and CMAUP 2.0, restricted to the formula's source species,
plus STRING 12.0 and Reactome, all rebuilt from the public downloads on 2026-09-26;
gold markers reproduced). The question was 葛根芩连汤的实测靶点是否集中在某条
Reactome 通路？, with two activity sources.
- **Background = whole Reactome annotation.** The analysis releases 60 pathway
  hypotheses. Rebuttal refutes all 60: none is significant once the background is
  restricted to proteins that were actually assayed, and 33 also do not survive
  leaving out NPASS. The released artifact contains no hypothesis. The 60
  refutations and their reasons are in `limitations.md` and `rebuttal.json`.
- **Background = assayed proteins (the default).** No pathway is significant, so
  nothing reaches rebuttal. The released artifact is again a negative result.
- **Resume.** Re-running the first case with its state directory reused all four
  completed stages. The run took about 4 s instead of 30 s, and the result was
  unchanged.

This agrees with the earlier finding in
`BioScience-Harness/docs/THIRD_PARTY_DB_CONNECTOR_SPEC.md`: for these public data,
pathway "enrichment" is almost entirely explained by which proteins were tested. The
difference is that the loop now reaches that conclusion by itself, from one question,
under audit, and releases it as the result.

Still open:
- A question is parsed literally: a registered formula name and an ontology id.
  There is no language-model planner.
- Only one formula (葛根芩连汤) is registered with its source species.
- The analysis runs beside its compiled PSH program rather than *as* that program.
- LOTUS and PubChem screening were not rebuilt for this run, although the loop
  accepts them as sources.

## Reproduce

```bash
pip install -e "PSH-Harness[test]" -e "BioScience-Harness[dev]"
cd BioScience-Harness && python -m pytest -q tests/test_audit_regressions.py
python -m bioagent.cli skill normalize-tcm-entities --arg names=黄芪   # one query, all five states ✓
```
