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
- Snapshots of the releases `skill.yaml` pins: `npass@2.0` is release 2.0 (any scoped
  build of it, such as `2.0+subset-…`), `opentargets@26.09+MONDO_0005148` is that release
  and disease only, and a bare key is any release. A run whose pin no recorded snapshot
  fits is refused and told which versions there are. Open Targets' API serves only its
  current release, so moving to a new one is a deliberate edit of the pin.
- A formula bound to one recorded composition (`sources.herbs.FormulaVersion`); the
  shipped one is 葛根芩连汤 as the 伤寒论 records it.

## How to run

    python scripts/run_network_pharmacology.py --snapshots DIR --ledger DIR/audit/snapshots.jsonl --out RUN_DIR

The research job runs the same analysis with pre-registered rebuttals and an attested
release:

    bioagent research "<question>" --formula 葛根芩连汤 --snapshots DIR --ledger FILE --state-dir STATE --out RUN_DIR

This is the data-driven workflow. The pinned skill `analyze-tcm-network-pharmacology` is a
limited seed-corpus network hypothesis analysis, with no database coverage and no measured
activity.

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

The nine exports of `bioagent.analysis.np_exports`:

- `compounds.tsv`: InChIKey, connectivity block and `id_kind` (inchikey or source_local);
- `targets.tsv`: `evidence_class` measured, distinct publications, herbs, disease score,
  degree and betweenness;
- `disease_targets.tsv`: the whole disease gene set;
- `enrichment.tsv`, with its library;
- `network.tsv`: the STRING edge list at the cut-off, each edge with its record;
- `provenance.json`: snapshot ids, parameters, seed, code digest, PSH program fingerprint,
  result digest, the run's source allowance and purpose, the ledger head it read, coverage,
  and every edge the tables rest on as (snapshot id, record id). Pass this file to a later
  run as `--ledger-head-from` and that run refuses a ledger cut short since;
- `limitations.md`, one limitation per line;
- `report.html`;
- `run_manifest.json` (`tcmscience.run-manifest/1`): the sixteen workflow stages, each
  PASS, FAIL, BLOCKED, UNVERIFIED, SKIPPED or NOT_APPLICABLE with its reason, every
  output's SHA-256 and size, and the command that reproduces the run.

The run also writes `claims.json`, `release.json`, `disease.json` (with a disease) and
`snapshot_lock.json`. Each claim in `claims.json` cites the snapshot edges of one
supporting path and records the strongest kind that path could license.

No file carries a timestamp: two runs on the same snapshots and ledger write the same
bytes. `python -m bioagent.analysis.np_exports verify RUN_DIR --snapshots DIR --ledger
FILE` re-checks every digest and recomputes the tables from the snapshots (exit 0: every
check passed; 1: one contradicts the run; 3: one could not be made). A run whose own stage
check FAILed exits 3, and one in which nothing could be tested is recorded as not complete.

## How to read the result

Every claim is a hypothesis. On 葛根芩连汤 the default run releases **none**: the 66 pathways
enriched against the whole annotation are all explained by which proteins were assayed.
The assayed background has little power in turn (61% of assayed proteins are recorded as
potent hits, because databases rarely record inactive results), so "not enriched" is not
"irrelevant". Read `limitations.md` first: composition is species-level,
targets are what happened to be measured, enrichment reflects screening panels as well as
biology, and the disease overlap describes the target set — the claims are about
pathways, not about treating the indication.

## 中文说明

本技能在经台账核验的数据快照上对一个方剂做网络药理学分析：组成（方剂 → 药材 → 基原物种 → 化合物）、实测靶点（IC50/Ki/Kd/EC50 不超过 10 µM）、在显式背景下的 Reactome 富集（BH 覆盖全部检验通路，再做度匹配置换检验）、仅作描述的 STRING 拓扑，以及与适应证遗传关联基因的重叠（统计量，不是主张）。结论上限为 `mechanism_hypothesis`（机制假说）。

研究任务（`bioagent research … --formula 葛根芩连汤`，Studio 任务 `research.run`）在同一分析之上增加预注册反驳与审计链证明的发布。这是数据驱动的工作流；锁定的 `analyze-tcm-network-pharmacology` 是有限种子语料网络假说分析，不覆盖任何数据库，没有实测活性。

产出九个导出文件：`compounds.tsv`、`targets.tsv`、`disease_targets.tsv`、`enrichment.tsv`、`network.tsv`（STRING 阈值以上的边列表）、`provenance.json`（每条边追溯到快照记录）、`limitations.md`、`report.html` 与 `run_manifest.json`（十六个工作流阶段，各带状态与原因；每个输出的 SHA-256 与大小；复现命令）。文件不含时间戳，同一快照上两次运行字节相同。GO 未检验（未登记 GO 快照），KEGG 未检验（需要许可）；去重只按来源提供的标准 InChIKey，未做结构重新标准化。任一阶段自身检查为 FAIL 时运行以退出码 3 结束；什么都无法检验的运行记为不完整。
