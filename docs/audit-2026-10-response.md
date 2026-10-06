# Response to the October 2026 audit

The audit found 26 issues (AUD-01 to AUD-26) and proposed fixing them in five batches,
identity first. Each issue is reproduced on `main` before it is fixed, the fix is
pinned by a regression test that starts from the audit's own example, and each batch
lands as its own pull request. All five batches have landed.

| Batch | Issues | Status |
| --- | --- | --- |
| 1. Herb names, doses, entity types, formula versions | AUD-01 to AUD-05 | fixed ([#32](https://github.com/psknlr/TCMScience/pull/32)) |
| 2. Evidence scope, direction, signatures, citations, final text | AUD-07 to AUD-12 | fixed ([#33](https://github.com/psknlr/TCMScience/pull/33)) |
| 3. Statistics and execution provenance | AUD-06, AUD-13, AUD-14 | fixed ([#34](https://github.com/psknlr/TCMScience/pull/34)) |
| 4. Chinese retrieval and the model-to-tool link | AUD-15 to AUD-17 | fixed ([#35](https://github.com/psknlr/TCMScience/pull/35)) |
| 5. Evaluation, promotion, permissions, packaging | AUD-18 to AUD-26 | fixed ([#36](https://github.com/psknlr/TCMScience/pull/36)); for AUD-26 the workflow now states its scope, and the benchmark runner is still to be built |

## Batch 1: identity

| Probe | Before | After |
| --- | --- | --- |
| `附子1.5g`, `甘草0.5g`, row 27855 `硫酸黄连素0.5g` | dose 15g, 05g, 05g | 1.5g, 0.5g, 0.5g; amount 1.5 / 0.5 g |
| `tcm_lookup` 白附子, 土茯苓, 水半夏, 黄芪甲苷, 甘草酸 | found: 附子, 茯苓, 半夏, 黄芪, 甘草 | unresolved; the near name is a candidate |
| `tcm_compatibility(白附子, 半夏)` | 十八反 of 附子 with 半夏 | no conflict; 白附子 unresolved, so not "compatible" |
| `化橘红` (20 formulas, e.g. row 15950 定喘汤1号) | 陈皮, *Citrus reticulata* | 化橘红, *Citrus maxima* |
| `黄连素3g`, `人参皂苷10mg` | 黄连 + "素", 人参 + "皂苷" | unresolved, kind `compound` / `constituent_group` |
| same formula id, composition cut to 葛根 | ran, governed, berberine in the output, provenance names the new fingerprint | refused on every path |

**AUD-01, decimal doses.** `parse_composition` normalised the whole item before reading
the dose, and normalising removes punctuation, so `1.5g` became `15g`. In the bundled
table 396 mentions in 195 formulas had a decimal dose. The dose is now split off first,
with decimal points kept; only the name is normalised. `parse_dose` reads the amount and
unit (1.5 g, 三两, 两钱, 钱半 = 1.5 钱, 1分半, 5厘) and keeps a number only when writing it
back gives the dose exactly as written, so `05g` never reads as 5 g. Across the table,
no dose now differs from its written digits, and 520,799 of 545,698 doses read as one
number. Doses that were cut up and stored as processing (`胆星钱半` → dose 半, processing
钱; `麝香5厘` → no dose) and fragments with a dose of their own that were swallowed as a
note (`蒸馏水100ml`) are parsed as written too. The network-pharmacology analysis does not
weight by dose, so no released result depended on the corruption, but 2,552 formulas
changed fingerprint: herb-layer snapshots built with `--formula-table` before this change
should be rebuilt.

**AUD-02, near names.** `TCMKnowledgeBase.resolve` returned a single substring candidate
as the entity found. Only an id, a recorded name or a recorded alias now identifies an
entity; names that contain the query or that it contains are candidates, and the
resolution says so (`status`, `match`). `tcm_herb("白附子")` refuses and lists 附子 as a
candidate; `tcm_compatibility` reports 白附子 as unresolved and applies no 十八反 rule to
it. The same fault was in the formula table's materia resolver: stripping words of origin,
size or colour (川, 大, 小, 白) folded 川牛膝 into 牛膝 (*Achyranthes bidentata*, while
川牛膝 is *Cyathula officinalis*), 白丁香 (sparrow droppings) into 丁香, 大麦 into 小麦 and
川木通 into 木通. Those words are no longer stripped. 川牛膝, 川木通, 川木香 and 白丁香 have
entries of their own; the names of that form that do denote the drug they contain
(川当归, 大半夏, 白云苓, …) are reviewed aliases, each checked against what it denotes, and
ambiguous ones (大麻子, 胡麻子, 大椒) stay unresolved.

**AUD-03, 化橘红.** 化橘红 is the outer pericarp of 化州柚 or 柚 (*Citrus maxima*, syn.
*C. grandis*), and 橘红 a monograph of its own; both were aliases of 陈皮. Each is now its
own drug; the new species were checked against NCBI Taxonomy (化橘红: taxid 37334). A scan
of the aliases for the same kind of fault found one more: 竹叶 and 苦竹叶 (bamboo leaves)
were aliases of the grass 淡竹叶 (*Lophatherum gracile*). 竹叶 is now its own drug
(*Phyllostachys nigra* var. *henonis*); 苦竹叶 stays unresolved.

**AUD-04, constituents read as herbs.** When a name did not resolve, the parser took its
longest known leading name and stored the rest as processing: 黄连素 was 黄连 processed
"素", 人参皂苷 人参 processed "皂苷", and parts and products went the same way (车前子根 →
车前子, 桃花石 → 桃枝, 珍珠母 → 珍珠). The fallback now applies only when the rest is a
processing instruction (去皮, 酒浸, 汤洗七次) or a size. Anything else stays unresolved, and
`Component.kind` says what the name reads as: `compound`, `constituent_group`, `extract`,
or `unresolved`. Checking the names that lost their resolution found three that the old
rules had sent to another species: 莲子草 (it is 墨旱莲, not 莲子), 金头蜈蚣 (a centipede,
not 金箔) and 石南藤 (not 石楠叶).

**AUD-05, formula version and analysed composition.** The runner checked only that the
formula id existed in the herb layer; the analysis read the snapshot's composition while
the provenance recorded the caller's fingerprint. The check now lives in the analysis
itself (`recorded_composition`): a formula whose fingerprint differs from the one the
snapshot records is refused there, so `run_skill`, `run_network_pharmacology` and the
research loop's governed tool all refuse it. A reduced formula (拆方) is studied as its
own version, built into the herb layer; the test removes 黄连 and checks that no compound
of its species reaches the analysis.

Effect on the bundled formula table: 50,291 formulas resolve completely (59.7%, was
53,206). 2,931 formulas lost a resolution that rested on a near name, a part, a product
or a constituent; 16 gained one. `docs/formula-table.md` has the details.

Tests: `tests/test_audit_2026_10_identity.py` (61, on the audit's examples).

## Batch 2: what a claim rests on

| Probe | Before | After |
| --- | --- | --- |
| Evidence in adults with heart failure measuring NT-proBNP; an efficacy claim about children and mortality that fills its own `supported_*` with children, mortality | allowed, no codes | refused: `CLM013` for population and outcome, and both are undeclared extrapolations |
| "A inhibits T1" citing A's binding constant for T1 and an antagonist record for B and T2 | released | refused: no path from A to T1 records a direction |
| A knowledge-base relation recorded as `RANDOMIZED_TRIAL` that cites a 伤寒论 passage; efficacy claim in 儿童 | `licensed=True`, `within_scope` | `extend` refuses the relation; placed in the base directly, it rests on `CLASSICAL_TEXT` and licenses no efficacy claim |
| "黄芪能治愈肺癌，未来研究将优化剂量。" from a Runner whose policy requires claim support | released | refused, like "黄芪能治愈肺癌。" |
| A signed record marked retracted, its retraction edited to `not_retracted` | signature verifies, source accepted | signature fails; the record is marked tampered and supports nothing |
| A record of PMID 34449189 supplied under the key 99999999 | verified as 34449189, displayed as 99999999, success | refused at `ingest_evidence`; the output gate checks the record behind every citation |

**AUD-07, scope stated by the claim.** `check_claim` compared the claim's asserted scope
with the scope the claim itself said its evidence covered. For a claim about people
(`efficacy`, `association`, `safety_signal`, `recommendation`) the covered population and
outcome are now read from the cited evidence items. A `supported_*` value the evidence
contradicts is refused with `CLM013` (`ART105` in an artifact), and declaring the gap does
not excuse it. A scope the evidence does not state is an extrapolation the claim must
declare (`CLM009`): the claim's own word no longer establishes it. Mechanism hypotheses,
traditional use and attribution keep comparing their own fields, because their scope
labels ("in silico", "seed corpus") describe the claim and their evidence records
relations, not study populations. Of the shipped skills only `assess-tcm-safety` can make
a claim about people (a `safety_signal`, once the corpus holds a case report); it now takes
that claim's population and outcome from the record it cites, and a test runs that case.

**AUD-08, direction borrowed from another edge.** Once a path existed, the release check
collected directions from every cited edge. The direction is now composed along each
directed path from the claim's subject to its object: composition and identity edges pass
it through, an inhibitor of an inhibitor increases, and an effect edge that records no
direction (a binding constant, an interaction) leaves its path without one. The stated
direction must be one the paths record, and paths that record both directions support
neither. The default network-pharmacology hypotheses state no direction and are not
affected.

**AUD-09, a relation's tier was its own word.** `TCMKnowledgeBase.extend`, which also
builds the base, refuses a relation recorded above the strongest evidence it cites.
`applicability` reads the tier off the usable evidence, so a relation placed in the base
by other means is bounded too. For a claim about people, the population and condition are
the ones its studies state. The relation's own fields can narrow that scope and no longer
establish it alone, and a clinical claim about a population the evidence does not state
is `extrapolated`, not licensed (before, the reason was recorded and the claim licensed).
Every seed relation sits at or below its evidence.

**AUD-10, a plan exempted the sentence.** The exemption for plans, methods and calls for
further work applied to the whole sentence. It now applies to the clause
(`psh/evidence/clauses.py`). A sentence is cut at clause punctuation, at conjunctions and
just before a construction that opens a plan, and only the clauses that are plans or open
questions are set aside. A sentence without such a construction is read exactly as
before. "黄芪能降低死亡率，但机制尚未明确" is now checked as the claim it makes; the open
question is the mechanism. Checking for the same fault found a second copy in the claim
parser, which also exempted reporting constructions ("This study reports that…", "The
results were…", "Table 2 shows…"). Those sentences skipped the scope check, so a trial
in adults supported "This study reports that empagliflozin reduced … in children" at
0.95. The constructions are now defined once, for the gate and the parser, and reporting
constructions are not among them; a plan or a source's open question contributes no
subject, population or direction to a parsed claim. Not covered: a plan that presupposes
its result ("future studies will confirm that X …") is still read as a plan.

**AUD-11, unsigned decision fields.** Every field of an evidence record is now signed
except the signature, the `trusted` and `tampered` flags derived from it, and the content,
which the signed content hash binds; a field added later is signed without being listed.
A record whose signature does not verify is marked tampered and supports nothing. An
unsigned record is still untrusted text, usable with a provenance caveat. The message
carries a version, so a record signed under the old five-field scheme no longer verifies;
records are signed at retrieval within a run and none is stored with its signature.

**AUD-12, the displayed citation and the verified record.** The mapping key, the record's
identifier and the citation in the text are compared as identifiers
(`canonical_identifier`: `PMID: 34449189`, `pmid:34449189` and `34449189` are one id).
`ingest_evidence` refuses a record supplied under an identifier that is not its own, and
a bibliographic record whose type differs from its identifier's. The Runner and the
Finalizer report either as a refusal at `ingest_evidence`. The output gate and the claim
commit look sources up the same way and treat a citation whose record is another's as
unsupported, for callers that hand sources to the gate directly. Bare text is typed by the
identifier it is supplied under, rather than as a PMID.

Tests: `BioScience-Harness/tests/test_audit_2026_10_evidence.py` (20) and
`PSH-Harness/tests/test_audit_2026_10_gates.py` (41). Run against `main`, 14 of the first
fail, and 24 of the 37 in the second that do not need the new functions; the rest are
cases that pass both before and after. The contract tests' default evidence item now
states the population and outcome its default claim relies on.

## Batch 3: statistics and execution provenance

| Probe | Before | After |
| --- | --- | --- |
| `enrichment_analysis`: background 1,000, 5 query genes, 100 sets of 50, one sharing 2 genes | p = q = 0.0222, significant | p = 0.0222, q = 1.0 over the 100 sets tested; `tests: 100`, `shown: 1` |
| An otherwise valid artifact with `policy_id="policy-never-created"`, `audit_head="not-a-real-hash"` | `execution_attested`, `release_authorized` | `execution_declared` only; checked against an audit chain, refused with `ART117` |
| A research run in-process, then resumed under the default governed configuration | stages reused, 0 tool calls, `governed_execution: true`, released | the in-process stages are not reused; analysis and rebuttal run governed, with their tool calls |

**AUD-06, the multiple-testing family.** The generic `enrichment_analysis` tool kept only
the sets that shared at least `min_overlap` genes with the query and then adjusted their
p-values, so the family of tests was chosen by the data it was testing. Every set within
the set-size bounds (`min_set_size`, `max_set_size`, which do not depend on the query) is
now tested, and the Benjamini–Hochberg adjustment runs over all of them; `min_overlap` only
chooses which rows are shown. The result reports the family size (`tests`), the rows shown,
and the significant sets over the whole family (`significant_not_shown` counts any that the
display filter hides). The network-pharmacology pipeline already adjusted over every
pathway in its pre-set size range, as the audit found, and is unchanged.

**AUD-13, declared versus attested.** `validate_artifact` took `policy_id` and
`audit_head` as an attestation whenever both were non-empty. The two fields are now
reported as `execution_declared`. `execution_attested`, which `release_authorized`
requires, is decided only against an audit chain, through `attestor=`
(`contracts/attestation.py`, `AuditChainAttestor.open(state_dir)`). The chain must verify,
the named head must be an event in it, and after that head the run must have recorded an
`…_artifact_attested` event with this artifact's digest, head and policy. Governed runs
(`run_governed` and the research loop) now record that event at release and validate
against their own chain. Because the digest is recomputed from the artifact in hand, an
imported copy of a released artifact still attests, while one edited after release, or
naming another policy or a made-up head, is refused with `ART117`. With the artifact file
alone, a reviewer can see what it declares, not that it is attested.

**AUD-14, a cache rewriting history.** The research loop keyed its analysis and rebuttal
checkpoints on the protocol and the snapshots, and the artifact described the current
configuration: a result computed in-process was reused by a governed resume, which then
reported `governed_execution: true` with no tool call. The two stages are now keyed on how
they execute as well: mode, deployment profile, compiled program, the analysis code (the
skill's content hash over the tool's import closure) and the environment digest. Each
stage's execution record is saved with it and in the audit chain, and a checkpoint is
reused only when the chain holds the matching completion event, so a checkpoint edited to
claim governed execution is recomputed. The artifact reports each stage as it ran
(`provenance.execution`), and `tool_calls` is the sum recorded when the stages ran, not a
recount. A governed resume of a governed run still reuses its stages.

Tests: `BioScience-Harness/tests/test_audit_2026_10_provenance.py` (12); all 12 fail on
`main`. The research-loop tests now validate a resumed artifact against its audit chain
and expect the attestation event.

## Batch 4: Chinese, and the model-to-tool link

| Probe | Before | After |
| --- | --- | --- |
| Component search for 附子安全, 中文无关词, 量子力学 | the same three DNA tools for each | 附子安全: `tcm_compatibility`, `tcm_herb`; the other two: nothing |
| A Chinese claim checked against the identical Chinese sentence | `unknown`, confidence 0 (English: `support`, 0.95) | `support`, 0.95 |
| The same claim about 儿童 against a trial in 成人 | (unreachable: no Chinese claim was ever supported) | refused: population extrapolation |
| The model plans `{"id": "native.tool.translate", "arguments": {"sequence": "ATGC"}}` | the step has no arguments; the tool fails for want of `sequence` | the step carries `{"sequence": "ATGC"}` and the tool runs |

**AUD-15, Chinese queries.** The component registry indexed and split queries on
`[^a-z0-9]+`, so a Chinese query had no terms and the search returned the first components
of the pool. The registry now uses PSH's bilingual terms (`psh.context.terms`). Chinese
runs contribute their lexicon words, the English of those words, and bigrams, so Chinese
meets Chinese and 附子 also meets *aconite*. A query with no term the catalogue knows now
matches nothing; before, it returned the pool. Manifests now carry `keywords`, and native
tools register their tags there. The tags were never indexed before, so the TCM tools now
answer to Chinese (配伍禁忌, 十八反, 性味归经, 证候 …). The capability catalogue
(`bioagent.registry`) ranks Chinese the same way, and a query nothing matches no longer
returns the whole catalogue unranked. Cross-language matching is only as wide as the
lexicon: a Chinese word it lacks finds Chinese text only.

**AUD-16, Chinese claim support.** The default support verifier read English words only,
split sentences only after `.` and `;`, missed every number written next to a Chinese
character (`\w` includes Chinese, so 降低了30% had no number), and had English cues for
negation and certainty. All of these now read Chinese:
- tokens: words and bigrams of Chinese runs, with a Chinese list of generic study
  vocabulary;
- sentence ends: 。！？；;
- numbers and units: %, 倍, 毫克, 天, 个月, 年;
- negation tied to a finding: 未能降低, 无显著影响, 差异无统计学意义. A bare 未见 is not a
  cue, because 未见明显不良反应 reports safety, not a failed effect;
- concessive clauses: 无论…;
- certainty: 治愈 and 证实 are definitive, but 尚未证实 is not.

Lexical support alone would have opened a hole: the structured scope check also read
English only, so a Chinese claim had no subject and was never checked for population.
The claim parser and the source scope now extract Chinese subjects, directions,
outcomes, populations (儿童, 孕妇, 老年, 透析 …), conditions, designs and sample sizes.
A trial in 成人 therefore does not support the same claim about 儿童 or 孕妇, a 黄芪
trial does not support a 丹参 claim, and a 再住院率 trial does not support a 死亡率 claim.
The extraction is pattern-based. A subject it cannot find leaves the claim unchecked for
subject, as in English, and is never read as a match.

**AUD-17, planner arguments.** `LLMPlanner` built each step from the component id alone.
It now shows the model each candidate's parameters, asks for arguments, keeps them, and
checks them against the parameters the component declares. An unknown name, a missing
required parameter, or a value of the wrong type (judged by the declared type, or by the
type of the declared default) refuses that step, and the note says why. When no step
survives, the planner falls back to heuristic ordering, as it does when no model is bound.
An end-to-end test runs a planned `translate` call and checks that the tool received its
`sequence`.

One Chinese research task runs end to end, as the batch's acceptance standard asks. For
附子与半夏合用是否存在十八反配伍禁忌？ the registry offers the compatibility tool and nothing
else. The planner keeps the herbs the model chose, and the tool returns the 十八反 record
(半夏反乌头). On the PSH side, a Runner whose policy requires claim support releases a
Chinese conclusion its cited Chinese trial supports. It refuses the same conclusion about
children, a claim of cure, and the conclusion without a citation.

Tests: `BioScience-Harness/tests/test_audit_2026_10_language.py` (23) and
`PSH-Harness/tests/test_audit_2026_10_chinese.py` (19). Against `main`, 22 and 15 of them
fail. The tests that pass on `main` cover behaviour that should not change: English
queries, an unrelated source supporting nothing, and refusals that `main` also made,
though there only because it could read no Chinese at all.

## Batch 5: evaluation, promotion, permissions, packaging

| Probe | Before | After |
| --- | --- | --- |
| AUD-18: a missed critical case, run through `score_run` | `gates_failed=[]`, trusted, aggregate 1.0; versions and digests may be empty | `GATE001`, experimental board |
| AUD-19: the easy case scored 99 times, the hard one once | task_success 0.99 (once each: 0.50), trusted | refused: `easy` scored more than once |
| AUD-21: incumbent 0.8, candidate NaN, required gain 0.1 | `PROMOTED`; the candidate is active | `QUARANTINED`: nan is not finite |
| AUD-20: approve 1.0.0 (MIT, hash A), promote 999.0.0 (no licence, hash B) | a stable 999.0.0 carrying the reviewer's name | refused: differs in version, content_hash, license_spdx |
| AUD-23: `git push --force origin main` at the tool gate | allowed, subject to approval | denied by the denylist |
| AUD-22: a hook rewrites `git status` to `git push origin main` | the push reaches the tool; 0 approval requests | approval is asked for `git push origin main`; with no approver the tool is not called |
| AUD-24: a record issued for one note, on another note, target edited to PUBLIC | PUBLIC (as issued: RESEARCH_DEIDENTIFIED) | PHI either way; the record is counted as forged |
| AUD-25: the wheel, run from an empty directory: `bioagent.cli skill normalize-tcm-entities --arg names=黄芪` | no skill manifest or lockfile in the wheel; refused, no skill directory | runs, pinned by the lockfile inside the package, attested, released |

**AUD-18, gates.** `score_run` aggregated the gates the case scores reported about
themselves and never ran the run-level gates, so a missed critical case failed GATE001
when the gate was computed and was trusted when the run was scored. It now evaluates every
gate itself (`gate_failures`), from the scores and from the run's `claims` and
`artifact_reruns`. A gate whose evidence is not supplied is not passed: the row reports it
under `gates_not_run`, and the run stays off the trusted board (`GATES_NOT_RUN`). A run that
does not name all four version axes, its trace digest and its artifact digest is
`UNTRACEABLE`, and is kept off the trusted board too.

**AUD-19, the case set.** A submission is scored only against its Season's cases, each
once. A case scored twice, a case the Season does not have, a case scored on another
track, or a Season that lists a case twice is refused with `ScoringRefused`. A missing
case keeps the run off the trusted board, as before.

**AUD-21, invalid numbers.** NaN compares False with every threshold, so the regression
check and the improvement check both passed. The evolution pipeline now checks every
benchmark result before comparing anything. The score must be a finite number in [0, 1],
and it must rest on at least one case, for the candidate and for the incumbent. A result
that fails quarantines the candidate, and the incumbent stays active. A case score
(`ScoreComponents`) is refused when it is infinite or not a number. NaN was already refused
there.

**AUD-20, what an approval approves.** A `PromotionDecision` named a skill and a version
string, and `promote` installed whatever `decided_version` it was given. An approval now
carries `candidate_digest`, the digest of the reviewed fields: id, version, source and
commit, content hash, licence, integration mode, hosts, filesystem and subprocess
permissions, dependencies, SBOM and test digests. An approval without it is refused when it
is made. `promote` refuses a decision about another skill or version, a digest that is not
the candidate's, and a version that differs from the candidate in any reviewed field. The
refusal names the fields. Rollback had the same fault: it renamed the current entry, so
1.1.0's code and licence came back under 1.0.0's version. It now reinstates the version as
it was promoted, and only on a decision naming that version's digest.

**AUD-23 and AUD-22, the final payload.** Two faults in the tool path compounded each
other. In `ToolGateway.check`, a command the execution policy asks approval for returned
at once, before the denylist and the path checks, so `git push --force` (refused by the
denylist) became an approvable `git push`. The prompt is now recorded and the remaining
checks still run; approval is required only when all of them pass. In
`ExecutionBroker.call_tool`, approval was decided before the PreToolUse hooks ran. A hook
that rewrote `git status` into `git push origin main` therefore had the push reach the tool
with no approval asked; in the audit's test the tool was a stand-in, and nothing was
pushed. The order is now: the gate rules on the payload that was asked; the hooks run; a
rewritten payload goes back through ingress and every gate check; and only then is approval
decided, about the payload that will run. The approver sees that payload's argv.

**AUD-24, declassification records.** Ingress honoured a lowered label when the
declassification record's id was one the kernel had issued, so an issued record could be
moved onto other content and have its target edited, to PUBLIC for example. The kernel now
registers each record with the content it was issued for. Ingress honours a record only
when it is the registered one, field for field, and the value's content is that content.
Any other record is stripped and counted as forged, and the value is classified from its
content. As the audit notes, this needed a record already issued by the same kernel and
access to the Python label API; it was not shown to be reachable from the model's JSON
interface.

**AUD-25, the installed package.** The wheel was built from `src/` alone, and the
resources a governed run needs live beside it: the skill manifests (`skills/`) and the
lockfile and reviewed registry state (`registry/`). `setup.py` now copies both into the
built package as `bioagent/_bundled/`, keeping their layout, and the sdist carries them.
`bioagent.config` finds them in the checkout when running from one, and in the package
otherwise. That resolution is used by the CLI defaults, the research loop's run contract
and the materia taxa (`skills_dir`, `registry_dir`). The CLI's `--dir` had also defaulted
to the relative path `skills/tcm`, which resolved only from the package's own directory;
it now defaults to the shipped skills. Two more faults were silent:
- An installed package found no `materia_taxa.json` and resolved 4 crude drugs instead
  of 344. The research loop also ran without its run contract.
- A skill's content hash depended on where its directory sat. Files were hashed in
  absolute-path order, so a copy of a skill tree hashed differently from the tree, and an
  installed skill would not have matched its own lockfile. They are now hashed in the
  order of the names that enter the digest: the skill's own files, then its code. The
  hashes of the tree are unchanged, so no pin moved.

A governed run reports the lockfile it was checked against (`governed.lockfile`, and
`pinned by` in the CLI). The release is now accepted only once a governed skill has run
from the wheel alone. `scripts/make_release.py` builds the sdist and, from it, the wheel.
It unpacks the wheel and runs `normalize-tcm-entities` in a fresh interpreter (`-S -P`).
That interpreter sees the wheel, PSH and the third-party packages, and nothing of the
checkout; its working directory is empty. The run must import bioagent from the wheel,
load the packaged lockfile, and be attested and released. CI runs this on every push. A
clean virtual environment with `pip install PSH-Harness/ BioScience-Harness/` runs the
skill from an empty directory the same way.

**AUD-26, the candidate benchmark.** The audit is right: `candidate-benchmark.yml` declared
a candidate input and never used it, and it runs no case of a frozen Season. Its checks
are the lockfile, the skill compiler and the unit tier. This batch does not build the
benchmark run. It makes the workflow say what it does. The header and the job name now
state that no benchmark case is executed and that a pass is not a score. The input is
checked for the `<skill-id>@<version>` form and reported as not benchmarked. The six
tracks of twenty cases remain an evaluation design with demonstration runs, as the site
and the case directory already state, and not a completed validation of scientific
capability. A real candidate benchmark needs these parts, none of which exists yet:
- a case runner that executes the frozen Season against the candidate;
- frozen data and scoring rules;
- the run bound to the candidate's pinned version;
- independent scoring, with results others can check.

Tests: `BioScience-Harness/tests/test_audit_2026_10_release.py` (36) and
`PSH-Harness/tests/test_audit_2026_10_permissions.py` (11). Against `main`, 34 and 9 of
them fail. The tests that pass on `main` cover behaviour that should not change: a real
improvement is still promoted, NaN case scores were already refused, an issued
declassification still lowers its own content, and a call no hook touches still needs no
approval. `tests/test_packaging.py` adds an integration test that builds the sdist and the
wheel and runs the same acceptance. The `score_run` and promotion tests in
`test_benchmarks.py` and `test_updates.py` now pass the run-level evidence and the
candidate digest explicitly.

## 中文摘要

第一批（药材身份）已修复：
- **AUD-01 剂量**：先拆剂量、后归一化药名，小数剂量不再丢失小数点；剂量保留原文，同时给出结构化的数量和单位，只有写回后与原文完全一致才保留数值。
- **AUD-02 近名药**：知识库的子串匹配只给候选，不再确认实体；方剂解析也不再剥离“川、大、小、白”。川牛膝、川木通、川木香、白丁香各有自己的条目，确实无误的写法逐条审核为别名。
- **AUD-03 化橘红**：化橘红、橘红、竹叶各自成为独立药材，新物种经 NCBI 核验。
- **AUD-04 成分与药材**：成分、部位、制成品不再被当成“药材＋炮制”，未解析成分标出类型。
- **AUD-05 方剂版本绑定**：组成指纹与快照不一致时，所有分析入口都会拒绝运行。

随包方剂表能完整解析的方剂由 53,206 首变为 50,291 首；减少的部分原本依赖错误的解析。

第二批（主张所依据的证据）已修复：
- **AUD-07 证据范围**：关于人的主张（疗效、相关性、安全性信号、推荐），其人群和终点从被引证据读取。与证据矛盾的自填范围以 `CLM013` 拒绝，且不能靠声明外推豁免；证据没有写明的范围算作外推，必须声明。
- **AUD-08 作用方向**：方向沿主体到客体的每条路径合成，组成边原样传递，“抑制剂的抑制剂”为增强，没有方向的效应边（结合常数、相互作用）使该路径没有方向；无关边不能再借出方向，两个方向都有记录时两者都不支持。
- **AUD-09 证据等级**：知识库关系的等级不能高于其引用证据，`extend` 直接拒绝；适用性判断按实际可用证据计算等级。临床主张的人群和病症以研究写明的为准，关系自带的字段只能收窄，不能单独授予。
- **AUD-10 最终文本**：“未来研究”等计划和开放问题只豁免它所在的子句，同句的疗效断言照常检查。同时发现并修复主张解析器中“This study reports that…”等报告式写法跳过范围检查的问题。
- **AUD-11 签名**：证据记录除签名本身、由签名派生的标志和内容（由已签名的内容哈希绑定）外，所有字段都纳入签名；签名不符的记录标记为被篡改，不能支持任何主张。
- **AUD-12 引用一致**：映射键、记录内部 ID 与正文引用按规范化标识比较；键与记录不一致、文献类型与标识不符时拒绝，输出闸门也逐条核对引用背后的记录。

第三批（统计与执行溯源）已修复：
- **AUD-06 多重检验族**：通用富集工具对所有符合预设集合大小的通路检验并做 BH 校正，最小重叠数只决定展示哪些结果；同时返回检验数量和展示数量。审计的例子校正后 q = 1.0，不再显著。
- **AUD-13 已声明与已认证**：仅填写 `policy_id`、`audit_head` 只算“已声明”；只有对照审计链核实（链完整、所指头部存在、其后记录了本产物摘要与策略）才算“已认证”。伪造的策略或头部、发布后被修改的产物都以 `ART117` 拒绝。
- **AUD-14 缓存与治理状态**：分析与反驳阶段的缓存键包含执行模式、配置、编译程序、分析代码和环境身份，并需审计链中有对应的完成记录才能复用；产物按各阶段实际执行情况记录，进程内结果不会被“升级”为受治理执行。

第四批（中文与模型到工具的连接）已修复：
- **AUD-15 中文检索**：组件注册表使用 PSH 的双语分词，中文词、词典对应的英文和二元组都参与检索；与目录无关的中文查询返回空结果，不再返回任意的 DNA 工具。中医药工具登记了中文关键词，“附子安全”会找到配伍禁忌和药材工具。
- **AUD-16 中文证据核验**：支持核验器读取中文词元、句末标点、数字和单位、与结论相连的否定（“未能降低”“无显著影响”“差异无统计学意义”）、让步从句和证据强度。同时补上结构化范围检查的中文抽取（主体、方向、终点、人群、病症、设计、样本量），成人试验不能支持儿童或孕妇的同一主张。
- **AUD-17 规划器参数**：LLM 规划器保留模型给出的工具参数，并按组件声明校验参数名、必填项和类型；不合格的步骤被拒绝并说明原因。

第五批（评测、晋升、权限与分发）已修复：
- **AUD-18 评测闸门**：`score_run` 自行计算全部运行级闸门，不再采信评分自带的结果；缺少证据而无法检查的闸门记为“未运行”，不计为通过；没有写明四个版本轴、轨迹摘要和产物摘要的运行标为不可追溯。两者都不能进入可信榜。
- **AUD-19 案例集合**：每个案例只能评分一次，重复、赛季外和赛道不符的案例直接拒绝。
- **AUD-21 无效指标**：比较之前先检查每个基准结果：必须是 [0, 1] 内的有限数，且至少有一个案例。NaN、无穷大或越界的结果使候选被隔离，现有版本保持不变。
- **AUD-20 审批对象**：审批决定绑定候选摘要，覆盖版本、来源与提交、代码哈希、许可、权限、依赖等审核字段；晋升对象与审核候选有任何字段不同即拒绝，并指出哪些字段不同。回滚恢复当初晋升的那个版本本身，而不是给当前版本改名。
- **AUD-23 PROMPT 提前返回**：需要审批的命令仍要经过拒绝清单和路径检查，`git push --force` 被直接拒绝。
- **AUD-22 Hook 改写**：Hook 改写后的参数重新经过入口检查和全部闸门检查，审批在最后、针对实际执行的参数进行，审批人看到的是实际要执行的命令。
- **AUD-24 降密记录**：内核登记每条降密记录及其对应内容；只有记录未被修改、且用于原内容时才生效，挪用或篡改的记录按伪造处理，数据按内容重新定级。
- **AUD-25 独立安装包**：wheel 和 sdist 现在携带技能清单、锁文件和注册表，配置在源码树和安装包中都能找到它们，CLI 默认目录不再依赖当前工作目录。同时修复两处静默差异：安装包原先只识别 4 味药材（源码中为 344 味），技能哈希曾随目录位置变化。发布验收会从 sdist 构建 wheel，在看不到源码树的全新解释器中运行受治理技能，CI 每次推送都执行。
- **AUD-26 候选基准**：如实说明候选基准工作流目前只做锁文件、编译和单元测试检查，不运行冻结赛季的科学案例，通过不代表得分。完整案例执行器、冻结数据与评分规则、候选版本绑定和独立评分尚待建设。
