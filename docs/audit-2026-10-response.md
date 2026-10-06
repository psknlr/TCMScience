# Response to the October 2026 audit

The audit found 26 issues (AUD-01 to AUD-26) and proposed fixing them in five batches,
identity first. Each issue is reproduced on `main` before it is fixed, the fix is
pinned by a regression test that starts from the audit's own example, and each batch
lands as its own pull request. This page is updated as each batch lands.

| Batch | Issues | Status |
| --- | --- | --- |
| 1. Herb names, doses, entity types, formula versions | AUD-01 to AUD-05 | fixed (this change) |
| 2. Evidence scope, direction, signatures, citations, final text | AUD-07 to AUD-12 | in progress |
| 3. Statistics and execution provenance | AUD-06, AUD-13, AUD-14 | in progress |
| 4. Chinese retrieval and the model-to-tool link | AUD-15 to AUD-17 | in progress |
| 5. Evaluation, promotion, permissions, packaging | AUD-18 to AUD-26 | in progress |

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

## 中文摘要

第一批（药材身份）已修复：
- **AUD-01 剂量**：先拆剂量、后归一化药名，小数剂量不再丢失小数点；剂量保留原文，同时给出结构化的数量和单位，只有写回后与原文完全一致才保留数值。
- **AUD-02 近名药**：知识库的子串匹配只给候选，不再确认实体；方剂解析也不再剥离“川、大、小、白”。川牛膝、川木通、川木香、白丁香各有自己的条目，确实无误的写法逐条审核为别名。
- **AUD-03 化橘红**：化橘红、橘红、竹叶各自成为独立药材，新物种经 NCBI 核验。
- **AUD-04 成分与药材**：成分、部位、制成品不再被当成“药材＋炮制”，未解析成分标出类型。
- **AUD-05 方剂版本绑定**：组成指纹与快照不一致时，所有分析入口都会拒绝运行。

随包方剂表能完整解析的方剂由 53,206 首变为 50,291 首；减少的部分原本依赖错误的解析。第二至第五批正在进行。
