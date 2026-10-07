# Evidence typing on real literature: how often the rules read the design PubMed gives

The literature path types each passage by fixed rules
(`bioagent.literature.evidence.read_fields`, [literature-evidence.md](literature-evidence.md)):
its study design, and the population, comparator and outcome it covers. Everything
downstream trusts that label. `check_claim` and PSH's licensing check a claim against the
design: a correct type check on a wrongly labelled study still reaches the wrong conclusion,
and a cohort read as a trial licenses efficacy. So the labels are measured here on their
own, apart from the type system, on 478 real abstracts.

## In brief

On the test split (302 abstracts, never looked at while the rules were changed):

- **Of every design the rules read, 191 of 196 (97%; 95% CI 94–99%) is the one PubMed
  gives.** Before this change it was 155 of 175 (89%; 83–92%).
- **They read a design for 192 of 245 studies (78%; 73–83%)**, up from 156 (64%; 57–69%).
  The rest stay unassessed, by design.
- **Trial protocols, editorials and narrative reviews:** the rules typed 4 of 57 (7%; 3–17%),
  down from 19 (33%; 22–46%). 1 of the 57 was read as a trial, against 16 before.
- **Wrong readings: 5, down from 20.** 4 of them would license a claim kind PubMed's type
  does not (3 of them efficacy), against 19 (17) before. Read one by one, 2 of the 5 are rule
  errors. 2 are papers that call themselves a systematic review or network meta-analysis
  but carry only PubMed's "Review" type, and 1 calls itself a case-control study although
  its groups were randomly assigned.
- **Population, comparator and outcome are rarely filled:** 9%, 14% and 7% of abstracts. Of
  30 filled values drawn at random and read in their abstracts, 23 are correct (77%;
  59–88%), 5 partly correct and 2 wrong.

| Design | Studies | Precision | Recall | Abstained |
| --- | ---: | --- | --- | --- |
| systematic review | 40 | 36/38 (95%; 83–99%) | 36/40 (90%; 77–96%) | 4 (10%; 4–23%) |
| randomized trial | 48 | 35/36 (97%; 86–100%) | 35/48 (73%; 59–83%) | 12 (25%; 15–39%) |
| observational | 42 | 40/41 (98%; 87–100%) | 40/42 (95%; 84–99%) | 2 (5%; 1–16%) |
| case report | 42 | 35/35 (100%; 90–100%) | 35/42 (83%; 69–92%) | 7 (17%; 8–31%) |
| animal | 31 | 12/13 (92%; 67–99%) | 12/31 (39%; 24–56%) | 19 (61%; 44–76%) |
| in vitro | 42 | 33/33 (100%; 90–100%) | 33/42 (79%; 64–88%) | 9 (21%; 12–36%) |

Precision counts every reading of that design, negatives included. All figures, before and
after, the confusion matrix and each stratum are in
`BioScience-Harness/benchmarks/evidence_typing/results.md`.

**Read the precision for this mix, not for literature at large.** The sample is stratified:
each design and each kind of negative has a few dozen records, whatever its share of the
literature. The 2022 frames below hold five times as many narrative reviews as systematic
reviews (64,716 against 12,423), so the same rate of misreading a narrative review costs more
precision there than here.

## The sample

`scripts/freeze_evidence_typing_sample.py` drew it from Europe PMC's REST API on 2026-10-07
and froze it in `BioScience-Harness/benchmarks/evidence_typing/`. For each stratum:

1. **The frame** is every PubMed record (Europe PMC's `MED` source) that matches the
   stratum's query, is open access under a licence Europe PMC reports as CC BY or CC0, and
   has an abstract. The English
   strata take records first published in 2022, which keeps every frame small enough to
   list in full. The Chinese strata take every year, because there are few.
2. **The order** is a seeded hash of each PMID, not the search's ranking.
3. **Records are taken in that order** until the quota is met. A record is kept only when
   - its PubMed metadata gives the stratum's reference (below);
   - its article's own licence statement is CC BY or CC0;
   - its abstract has at least 400 characters (150 in Chinese) and is not a teaser cut off
     with "[...]";
   - no earlier stratum drew it.

No rule ran while the sample was drawn. Of 644 records examined, 166 were excluded, each
counted by reason in `sampling.json`:

- 86 abstracts were under 400 characters (84 of them editorials);
- 49 had another reference than the stratum's;
- 22 had no reference: no design type, two design types, or a scoping review;
- 8 were retracted or under an expression of concern;
- 1 had no CC BY or CC0 licence statement. It was an Elsevier COVID-19 free-access article
  that Europe PMC lists as CC BY.

| Stratum | Reference | Drawn | Frame |
| --- | --- | ---: | ---: |
| randomized trials | randomized trial | 60 | 6,945 |
| systematic reviews and meta-analyses | systematic review | 60 | 12,423 |
| cohort, case-control, cross-sectional (MeSH), any observational | observational | 20 + 20 + 20 + 15 | 340, 76, 568, 4,188 |
| case reports | case report | 50 | 20,764 |
| animal studies | animal | 50 | 35,698 |
| in-vitro studies | in vitro | 50 | 6,124 |
| narrative reviews | negative | 30 | 64,716 |
| editorials | negative | 25 | 3,072 |
| trial protocols | negative | 30 | 1,622 |
| Chinese: trials, reviews, case reports, animal, in vitro, narrative reviews | as in English | 12 + 8 + 8 + 6 + 6 + 8 | 36 to 568 |

**478 records:**

- 430 English, first published in 2022.
- 48 Chinese, 2010–2026, from three journals: 中国肺癌杂志, 色谱 and 中华血液学杂志.
- 302 are in the test split and 176 in dev.

**Licences.** The licence is checked twice: Europe PMC's `license` field first, then the
`<license>` element of the article's own XML. All the licences the XML names must agree, and
a statement with non-commercial, no-derivatives or share-alike terms is refused whatever it
links. Only the statement's first link counts: a BMC article goes on to waive copyright in
its *data* under CC0, which is not the article's licence. The 478 records are:

- CC BY 4.0: 386;
- CC BY 3.0: 45;
- CC0 1.0: 1;
- `CC-BY` with no version: 46, mostly Frontiers (20) and Cureus (11). The article names "the
  Creative Commons Attribution License" without a version. Its statement is kept, and no
  version is made up for it.

Each record carries its attribution:

- authors, journal, year, DOI, PMID and PMCID;
- its Europe PMC URL;
- its licence, with the licence URL or statement;
- the copyright line.

`load_sample` refuses a record under any other licence or without its attribution.
`BioScience-Harness/NOTICE` lists the abstracts as third-party text.

**Text.** The rules read the title and the abstract, as a document would hold them.

- **English:** Europe PMC's `abstractText`, with its markup removed. Section headings are kept
  and followed by a colon; nothing else changes.
- **Chinese:** the Chinese title and `<abstract>` from the article's XML, with the keyword list
  dropped. Europe PMC's abstract for a Chinese article is its English translation, so it
  cannot be used.

## The reference, and how far to trust it

The reference is PubMed's, fetched from E-utilities for every record and stored with it.
`reference_for` derives the label from that metadata, and `load_sample` derives it again, so
a label cannot be edited by hand:

| Reference | PubMed metadata |
| --- | --- |
| systematic review | type Systematic Review, Meta-Analysis or Network Meta-Analysis |
| randomized trial | type Randomized Controlled Trial |
| observational | type Observational Study |
| case report | type Case Reports |
| animal | no design type; a research article only; MeSH Animals, and neither Humans nor a cell-culture heading |
| in vitro | no design type; a research article only; a cell-culture MeSH heading, and neither Animals nor an age group |
| trial protocol (negative) | type Clinical Trial Protocol, even when also typed as the trial |
| editorial (negative) | type Editorial and no design type |
| narrative review (negative) | type Review and no design type |

No reference, and so not drawn:

- types naming two designs;
- a design outside the six (a veterinary trial, a scoping review);
- a retraction or an erratum.

**PubMed's types are an imperfect reference.** These numbers measure agreement with them,
not with the truth.

- **Indexers assign them, and for most of these records a machine did.** PubMed marks how
  each record was indexed:
  - 256 records: "Automated";
  - 83: "Curated";
  - 53: "Manual";
  - 86: PubMed-not-MEDLINE, with no MeSH at all.
- **Some are missing or wrong.** Examples, all read in the abstract:
  - In test, a paper titled "…: A Systematic Review and Pooled Analysis" and a
    self-described network meta-analysis carry only "Review".
  - In test, a ferroptosis lncRNA signature built from TCGA data, a diagnostic-accuracy study
    and a protocol whose participants "will be randomly divided" carry "Randomized
    Controlled Trial".
  - In dev, a protocol ("this cross-sectional study will be carried out") carries
    "Observational Study".
  - In dev, a Chinese retrospective prognostic study carries "Randomized Controlled Trial"
    because it split its patients 随机分为训练集和验证集 (randomly into training and
    validation sets).
- **A report's type is not always its design.** A secondary or pooled analysis of trials is
  often typed as the trial. One test record, "an analysis of three phase III, randomized
  clinical trials", is typed "Randomized Controlled Trial". The rules now set the plural
  aside and leave it unassessed, which counts against them here.
- **No type covers animal or bench work.** Those references rest on MeSH, which is coarser.
  "Animals" without "Humans" marks ants, moths, fish parasites, a pig-tracking camera
  pipeline and a method to measure drug residues in milk as readily as a mouse experiment.
  Of the 19 animal abstentions on test:
  - 11 are about insects, fish, a crop pest, food residues, a camera pipeline, a microscopy
    tool or canine blood units. The reference calls them animal studies; the rules know no
    word for them;
  - 5 are mouse-and-cell studies that name both designs;
  - 3 are experiments the rules miss: a rodent-malaria study and a subarachnoid-haemorrhage
    model that name no species, and a bench study of bull sperm.

## The split, and the order things were done

The split is a seeded hash of the PMID (`split_of`): a third is dev, two thirds test. It is
derived again on load, so a record cannot move after a result is seen. The history shows the
order:

1. `26fd2b5` committed the sample, its labels and its split. No rule had read any record.
2. The rules at `1e14a2a` were run on dev only, and changed looking at dev only (`77af335`).
   The scripts used to look filtered test records out before any rule read them.
3. With the rules fixed, the test split was run for the first time, with both versions of
   the rules (`baseline.json` holds the earlier one).
4. Test errors were then read, to describe them below. The rules were not changed after
   that.

**The test split is now public.** These numbers measure the rules as they were before anyone
saw test. A later change made with these results in view will score better on test than it
deserves. `--check` still guards against regressions, but a fresh estimate needs a fresh
sample: the freeze script with another `SAMPLE_SEED`, or another year's frames.

## What the rules get wrong on test

**The five wrong readings:**

| PMID | Reference | Read | What the text says |
| --- | --- | --- | --- |
| 35057843 | trial protocol | randomized trial | a "protocol update"; "will be equally and randomly divided". Neither is a protocol phrase the rules know. **Rule error** |
| 36194175 | editorial | animal | an editorial about a mouse genetics network, which names mice. **Rule error** |
| 36035839 | randomized trial | observational | "A case-control study explored…", then "randomly divided into the research group and the control group". The text misnames its own design |
| 35805058 | narrative review | systematic review | "A Systematic Review and Pooled Analysis"; "we performed a systematic review". The reference is missing a type |
| 36051702 | narrative review | systematic review | "A network meta-analysis" that searched four databases. The reference is missing a type |

Only the disagreements were re-read. The agreements were not, so no corrected precision is
claimed.

**Where the rules abstain:**

- **Randomized trials, 12 of 48.**
  - Four state a randomization the rules do not know: 随机分成 (randomly divided into)
    instead of 随机分为, allocation by a random-number table (随机数字表法, 随机数字法)
    twice, and a "Cross-Over Controlled Trial" that never says randomized.
  - Five are not reports of a trial's main result: a protocol, a pooled analysis of three
    trials, a prespecified quality-of-life analysis of a phase 3 trial, a
    diagnostic-accuracy study and a TCGA signature.
  - Two name a trial and also an animal or bench experiment.
  - One is a partner-level analysis that names no design.
- **Case reports, 7 of 42.** Three English reports never say "case report" or give the
  patient's age in the form the rule knows. Three Chinese ones report 2 or 3 cases
  (2例并文献复习, 3例), or one case without 并文献复习; the Chinese rule knows only
  1例…并文献复习. One also names an in-vitro experiment.
- **In vitro, 9 of 42.** Cells named by type rather than by a listed line ("breast cancer
  cells", "scleral fibroblasts", "adipose derived stem cells"), and "A549、H460细胞": the
  pattern wants 细胞 right after the name. Two are hardly bench studies: a single-cell atlas
  of keloid tissue, and T-cell repertoires after transplantation.
- **Systematic reviews, 4 of 40.** One says "Study protocol was prospectively registered at
  PROSPERO", which trips the protocol rule. Three are a bibliometric analysis, a narrative
  review of oxidative stress and a business-analytics paper, all typed Systematic Review.

Every gap above was seen on test and is left as it is. Fixing it now would tune on test.

**Chinese abstracts (35 on test):**

- the rules read a design for 18 of 28 studies, all 18 correct (12 of 28 before);
- they typed none of 7 narrative reviews;
- the strata are small and come from three journals, so these are illustrations, not
  estimates.

## Population, comparator, outcome

There is no reference for these fields, so two things are measured:

- **how often a rule fills each field** (test, before → after):
  - population: 28/302 → 28/302 (9%);
  - comparator: 42/302 → 41/302 (14%);
  - outcome: 28/302 → 20/302 (7%);
- **how many filled values are right**, by a manual check.

**The manual check.**

- **Draw.** `run_evidence_typing_benchmark.py --draw-manual-check 30` draws 30 filled values
  from the test split, with the final rules. The draw is a seeded hash of PMID and field, so
  which values are checked was fixed before any was read. It gave 13 populations, 14
  comparators and 3 outcomes.
- **Who read them.** The agent that wrote the rules read each value in its abstract, with
  the span marked: one reader, not blind to the rules, not a domain expert.
- **Criteria**, written before reading:
  - **correct:** the value names this study's population (the people it enrolled or
    reviews), comparator (the group, treatment or condition it compares against) or
    outcome (something it measured);
  - **partly:** it names that, but cut short or padded with other words;
  - **wrong:** it names something else.

**Result: 23 correct (77%; 59–88%), 5 partly, 2 wrong.**

- **Populations:** 9 correct, 2 partly, 2 wrong. Both wrong values come from sentences that
  are not about the study. One is a background prevalence figure in a review. The other is
  "women with … TNBC at high risk", the population of trials the authors of a xenograft
  study propose.
- **Comparators:** 12 correct and 2 partly ("fixed ones", and "subjects" cut from "subjects
  in control group").
- **Outcomes:** 2 correct, and one partly: "overall survival" cut before "and
  progression-free survival".

Each verdict and its reason is in `benchmarks/evidence_typing/manual_check.json`.
`--check` treats a correct value that changes as a regression, and `--out` refuses to
regenerate the results until values that changed are checked again.

## The rule changes, and what they did

All were made looking at dev, before test was run. The principle stayed: an ambiguous text
stays unassessed. Each change either sets aside a mention that is not the study's own
design, or adds a statement of a design.

| Change | Why (dev) |
| --- | --- |
| A protocol has no design read: "study/trial protocol", "protocol for a … trial", "rationale and design", "will be randomized/allocated/recruited", "we will recruit" | 10 of 13 protocols were read as trials, which licenses efficacy from a study with no results |
| A plural design mention is set aside, like a negated one: "randomized controlled trials", "cohort studies", "meta-analyses" | 15 of 28 systematic reviews were ambiguous, most because they name the trials or cohorts they pool; an editorial's "randomized trials are not always feasible" read as a trial |
| A random split of data is set aside: "randomly assigned to training and validation sets", 随机分为训练集 | a prognostic study's data split read as a trial |
| A review that names itself one ("this meta-analysis", "a systematic review was conducted", "本meta分析", a title ending "…的系统评价") reads as a review whatever else it names, unless it names another design as its own too ("the present cohort study") | the designs a review names besides its own are the ones it pools |
| New design statements: "observational study" (and "observational, longitudinal study"), "a 45-year-old man", "1例…并文献复习", a counted species ("121 dogs"), cell assays and widely used cell lines, "随机、双盲", 观察性研究, "systematic literature review" | observational studies that call themselves one, case reports that open with the patient |
| "in vitro fertilization" is not in vitro | an IVF cohort read as ambiguous |
| A population stops before a modal verb or "by". "Compared with baseline" is no comparator. "Associated with" names an outcome only with a direction ("worse FEV1") or a ratio estimate after it | values like "patients with … can be adopted", "several risk factors", "nerve damage" |

**Effect, before → after:**

| Split | Studies typed | Readings right | Wrong readings | Negatives typed |
| --- | --- | --- | ---: | --- |
| dev | 87/140 (62%) → 121/140 (86%) | 86/98 (88%) → 119/121 (98%) | 12 → 2 | 11/36 → 0/36 |
| test | 156/245 (64%) → 192/245 (78%) | 155/175 (89%) → 191/196 (97%) | 20 → 5 | 19/57 → 4/57 |

On test, 38 records went from unassessed to correct and 16 from wrong to unassessed. Three
went the other way:

- the protocol typed as a trial and the pooled analysis, described above, went from correct
  to unassessed;
- the self-described systematic review typed "Review" went from unassessed to wrong.

Dev improved more than test (98% against 97% right, 86% against 78% typed), as tuning on dev
predicts. Most of the gain held.

One expectation in `tests/test_literature_evidence.py` changed. "A systematic review and
meta-analysis of randomized trials in adults" now reads as a systematic review: the trials
are what it pools. A text with two designs of its own ("we report a randomized trial and a
meta-analysis of earlier trials") stays unassessed.

## What this is not

- **Not truth.** The reference is PubMed's, with the faults above.
- **Abstracts, not passages.** The rules read a whole document's text. Here that is a title
  and an abstract; a full text names more designs, and so is ambiguous more often.
- **Not all literature.** The sample is:
  - open access under CC BY or CC0;
  - mostly from 2022;
  - in Chinese, from three journals.

  Other licences mean other publishers and other house styles.
- **Small strata.** Read the intervals. The Chinese strata and the population, comparator
  and outcome check are too small for more than an illustration.
- **One reader.** The manual check is one person's reading, not an annotation with
  agreement measured.

## Run it

```bash
cd BioScience-Harness
# offline, from the committed sample (a few seconds)
python scripts/run_evidence_typing_benchmark.py --out benchmarks/evidence_typing
python scripts/run_evidence_typing_benchmark.py --check benchmarks/evidence_typing/results.json
# the same sample, read by another version of the rules
git show 1e14a2a:BioScience-Harness/src/bioagent/literature/evidence.py > before.py
python scripts/run_evidence_typing_benchmark.py --rules before.py --out RUN/before
# the fields to check by hand
python scripts/run_evidence_typing_benchmark.py --draw-manual-check 30
# a new sample (network: Europe PMC and NCBI E-utilities; about half an hour)
python scripts/freeze_evidence_typing_sample.py --cache CACHE --out RUN/sample
```

`--check` exits 1 when:

- a record reads worse than the committed results show (correct to unassessed or wrong,
  unassessed to wrong; on a negative, any design is wrong);
- a value the manual check found correct now reads otherwise;
- the sample changed.

Improvements pass, with a note asking for the results to be regenerated.

Tests: `BioScience-Harness/tests/test_evidence_typing_benchmark.py` (offline). It covers:

- the reference and the split;
- the committed sample's licences, attribution, labels and split;
- the script on a committed subset of 11 dev records (`tests/fixtures/evidence_typing`);
- `--check` refusing a weakened rule and another sample;
- `--rules` loading another `evidence.py`, and `--out` refusing a manual check of values
  the rules no longer read;
- the committed results and manual check matching what the code reads.

## 中文摘要

**要回答的问题：** 文献证据链用固定规则给段落标注研究设计（以及人群、对照、结局），下游的主张检查和类型检查都以这个标签为准。标签错了，类型检查再正确也会得出错误结论。因此本基准脱离类型系统，单独在真实文献上测量这些规则读得准不准。

**样本：**
- 从 Europe PMC 抽取 478 篇开放获取、CC BY 或 CC0 许可的摘要：430 篇英文（2022 年首次发表），48 篇中文（中国肺癌杂志、色谱、中华血液学杂志）。
- 按设计分层：随机对照试验、系统评价/荟萃分析、队列/病例对照/横断面研究、病例报告、动物实验、体外实验，以及不应被标为试验或综述的负例（叙述性综述、社论、试验方案）。
- 每层先完整列出抽样框，再按 PMID 的带种子哈希排序依次抽取。抽样时没有运行任何规则。
- 许可证核对两次：先看 Europe PMC 的字段，再看文章 XML 自身的许可声明。每条记录都附有作者、期刊、年份、DOI/PMID/PMCID、许可证及其链接或声明。

**参照标准：**
- 以 PubMed 出版类型为准，动物和体外实验另用 MeSH（Animals/Humans、细胞培养主题词）。
- 标签由随样本保存的 PubMed 元数据推导，加载时重新推导，无法手工改动。
- 这个参照并不完美：类型由标引人员给出（本样本多数记录由机器标引），有的缺失、有的错误。例如自称系统评价的论文只标了 Review，试验的二次分析或汇总分析常被标为随机对照试验，MeSH 的 Animals 也涵盖生态学和兽医学论文。因此这里测的是与 PubMed 的一致程度，不是真实准确率。

**划分：** 按 PMID 的带种子哈希固定划分，开发集占三分之一，测试集占三分之二，加载时会重新校验。
- 样本和划分先单独提交（26fd2b5），提交时规则尚未读过任何记录。
- 规则只依据开发集修改（77af335）。
- 规则冻结后，才首次在测试集上运行新旧两版规则。

**测试集结果（302 篇）：**
- 规则读出的设计中，191/196（97%，95% 置信区间 94–99%）与 PubMed 一致；修改前为 155/175（89%）。
- 研究的标注覆盖率从 64% 升至 78%，其余保持"未评估"。
- 负例被误标的比例从 19/57 降至 4/57；被标为试验的从 16 例降至 1 例。
- 错误读取从 20 例降至 5 例，其中会越级授权的从 19 例降至 4 例。逐篇核对这 5 例：2 例是规则错误，2 例是参照漏标（论文自称系统评价），1 例是原文自述设计有误。
- 各设计的精确率：系统评价 95%、随机对照试验 97%、观察性研究 98%、病例报告 100%、动物实验 92%、体外实验 100%。召回率分别为 90%、73%、95%、83%、39%、79%，均附 Wilson 区间。
- 动物实验召回率低，主要因为参照把生态学、兽医学、方法学论文也算作动物实验。
- 人群、对照、结局的填充率分别只有 9%、14%、7%。随机抽取 30 个已填字段逐一人工核对（由编写规则的同一代理执行，单人、非盲、非领域专家）：23 个正确（77%，59–88%），5 个部分正确，2 个错误。

**规则修改（仅依据开发集）：**
- 试验方案（"study protocol""will be randomized"）一律不读设计，因为它没有结果，不能授权任何主张。
- 复数的设计提及（"randomized controlled trials""cohort studies"）视为指代其他研究，与否定提及一样跳过；数据的随机划分（"随机分为训练集"）也一样跳过。
- 自称系统评价/荟萃分析的文本读作系统评价，它提到的其他设计是被汇总的研究。
- 新增"observational study"、病例开头的"a 45-year-old man"、"1例…并文献复习"、计数的物种、常用细胞系等设计表述。
- "in vitro fertilization"不再算体外实验。
- 有歧义的文本仍保持未评估。
- 测试集上发现的缺口（如"随机分成"、"2例并文献复习"、方案"更新"）只记录，不修改，以免在测试集上调参。

**CI：** `scripts/run_evidence_typing_benchmark.py --check` 离线运行。出现以下情况即判为回退：某条记录读得比已提交结果差，人工核对为正确的字段读法改变，或样本被改动。
